"""driver.py — the small control loop over the Datalog engine.

The engine (datalog/engine_rules.dl) DERIVES the consequences of a game state;
this applies them and re-runs. It holds NO rules — only the apply-and-loop
mechanics Datalog can't do: retraction across states, unbounded looping, the turn
cycle, casting/priority windows, sacrifices, and the turn-based actions the engine
flags. State is a dict of {relation: set of tuples}; the base inputs are the
printed_* characteristics, and the engine derives controls/has_type/creature/etc.
from them — the driver reads those derived relations back, never raw state.
"""

from __future__ import annotations

import csv
import os
import random
import re
import subprocess
import tempfile
from pathlib import Path

import sys

import engine_native        # compiled-binary backend; falls back to the interpreter if unavailable
import effect_handlers      # pluggable effect verbs (effect_handlers/*.py); _apply_effects dispatches here

_THIS = sys.modules[__name__]   # passed to effect-handler apply fns so they reach driver helpers w/o a cycle
effect_handlers.load()

RULES = Path("datalog/engine_rules.dl").read_text()
# relations the engine knows about; driver-only bookkeeping (in_library, ...) is not passed to souffle.
# Derived from the engine's `.decl` schema via the single introspection layer (engine_schema), not a
# second ad-hoc parse — so the shim's notion of the engine interface can't drift from the engine.
import engine_schema
DECLARED = set(engine_schema.relations())

# zone name (from the transpiled §701 keyword-action rules) -> driver state relation.
ZONE = {"battlefield": "on_battlefield", "graveyard": "graveyard",
        "hand": "in_hand", "exile": "exile", "library": "library"}


def clone_state(state: dict) -> dict:
    """A fast, correct copy of a game state for branching search (~17x faster than copy.deepcopy). The
    state is overwhelmingly `set`s of immutable tuples, so a shallow `set.copy()` is independent (no tuple
    aliasing); the few mutable non-set values (the _lib_order lists, the _stack_info/_ability_effect/_forced
    dicts) are copied one level deep, which is all the driver ever mutates in place."""
    out: dict = {}
    for k, v in state.items():
        if isinstance(v, set):
            out[k] = v.copy()                            # tuples are immutable -> shallow copy is safe
        elif isinstance(v, dict):
            out[k] = {kk: (vv.copy() if isinstance(vv, (list, set, dict)) else vv) for kk, vv in v.items()}
        elif isinstance(v, list):
            out[k] = list(v)
        elif isinstance(v, random.Random):
            r = random.Random(); r.setstate(v.getstate())   # a cloned branch advances its OWN chance stream
            out[k] = r                                       # (independent + reproducible — not aliased)
        else:
            out[k] = v                                   # ints / strings / immutables shared
    return out


def _choose(state: dict, key: str, options, default):
    """The single seam EVERY player decision routes through — so the shim is a referee, not a hardcoded
    player. `options` is the legal set (for enumeration by a search/policy layer); `default` is the greedy
    pick this codebase has always used. Resolution order:
      * state['_policy'](state, key, options, default) -> choice   (an external policy, e.g. a net/MCTS)
      * state['_forced'][key]                                      (a specific choice search.apply injects)
      * default                                                    (the greedy heuristic — unchanged play)
    A forced choice is validated against `options` when options is a concrete collection (else trusted)."""
    pol = state.get("_policy")
    if pol is not None:
        return pol(state, key, options, default)
    forced = state.get("_forced")
    if forced and key in forced:
        choice = forced[key]
        if options is None or choice in options:
            return choice
    return default


# --- randomness: a seeded, clone-safe RNG + the CHANCE seam (the analogue of _choose, for the
# non-deterministic game events the deterministic engine cannot model: library shuffles, coin flips,
# random discard). A game is fully REPRODUCIBLE given its seed; a search/self-play branch carries its
# OWN copy of the stream (clone_state preserves it) so a lookahead never perturbs the live game. This
# is what lets MCTS/AlphaZero treat shuffles as chance nodes rather than hidden nondeterminism. ---
def _rng(state: dict) -> random.Random:
    """The game state's seeded RNG, created lazily from state['_seed'] (default 0) the first time a
    random event needs it, then stored in state['_rng'] and preserved across clone_state."""
    r = state.get("_rng")
    if r is None:
        r = state["_rng"] = random.Random(state.get("_seed", 0))
    return r


def _random(state: dict, key: str, options, weights=None):
    """The single seam EVERY chance event routes through — the analogue of _choose for randomness, so a
    search/policy can OBSERVE or FIX chance outcomes (chance nodes, reproducible rollouts). Resolution:
      * state['_chance'](state, key, options, weights) -> outcome   (an external sampler/fixer)
      * a (weighted) draw from the state's seeded RNG               (default: real seeded randomness)
    `options` is the outcome space (a non-empty sequence); `weights` an optional same-length weighting."""
    pol = state.get("_chance")
    if pol is not None:
        return pol(state, key, options, weights)
    opts = list(options)
    if weights is not None:
        return _rng(state).choices(opts, weights=list(weights), k=1)[0]
    return _rng(state).choice(opts)


def _shuffle_library(state: dict, p: str) -> None:
    """§701.20 — randomize player p's library into a fresh _lib_order using the state's seeded RNG. The
    membership set (in_library) is unchanged; only the draw ORDER is permuted. A real shuffle (not the
    old canonical sort): reproducible given the seed, genuinely random across seeds."""
    lib = [c for (pp, c) in state.get("in_library", set()) if pp == p]
    _rng(state).shuffle(lib)
    state.setdefault("_lib_order", {})[p] = lib


def _flip_coin(state: dict, key: str = "coin") -> str:
    """§705.2 flip a coin -> 'heads' / 'tails' through the chance seam."""
    return _random(state, key, ("heads", "tails"))


# --- §103.4 per-variant game-setup numbers, READ from the interpreted rules (starting.dl), not
# hardcoded here — so adding a variant to the rules interpretation is enough; the shim follows. ---
def _variant_life(variant: str) -> int:
    text = Path("datalog/starting.dl").read_text()
    m = re.search(rf'starting_life\("{re.escape(variant)}", (\d+)\)', text)
    return int(m.group(1)) if m else DEFAULT_LIFE


def _variant_hand_size(variant: str) -> int:
    text = Path("datalog/starting.dl").read_text()
    m = re.search(rf'starting_hand_size\("{re.escape(variant)}", (\d+)\)', text)
    return int(m.group(1)) if m else 7

OUTPUTS = ["to_untap", "to_draw", "zone_change", "loses_game", "wins_game", "advance_to", "player_damage",
           "combat_commander_damage", "combat_poison", "pending"]


def _lit(x: object) -> str:
    return f'"{x}"' if isinstance(x, str) else str(x)


# Memoization of the engine transition. run() is a PURE function of the DECLARED facts in `state`
# (souffle is deterministic; nothing else is read), so identical engine-inputs always derive the
# same outputs. A lookahead search re-reaches the same engine-input on many branches; caching it
# collapses "total tree nodes × one souffle call" into "DISTINCT engine-inputs × one souffle call",
# which (with search.canonical_key dedup on top) is what makes deep multi-state lookahead cheap.
# Keyed by the canonical (order-independent) fact set; cleared with clear_cache() between scenarios.
_CACHE: dict = {}
_EVALS = [0]                                          # count of actual souffle invocations (cache misses)


def _facts_key(state: dict) -> frozenset:
    return frozenset((rel, frozenset(rows)) for rel, rows in state.items()
                     if rel in DECLARED and rows)


def clear_cache() -> None:
    _CACHE.clear()
    _EVALS[0] = 0


def cache_stats() -> dict:
    return {"distinct_states": len(_CACHE), "souffle_evals": _EVALS[0]}


def _evaluate(fkey: frozenset) -> dict:
    """Run the engine once for a fact set and return ALL outputs (cached). The program derives every
    relation regardless of what's read back, so we capture them all and serve any later request.

    Prefers the compiled native binary (engine_native, ~17x faster); falls back to the souffle
    interpreter when no binary can be built or MTG_NO_NATIVE is set — byte-identical either way."""
    _EVALS[0] += 1
    if not os.environ.get("MTG_NO_NATIVE") and engine_native.available():
        return engine_native.evaluate(fkey)
    facts = "\n".join(f"{rel}({', '.join(map(_lit, row))})."
                      for rel, rows in fkey for row in rows)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "e.dl").write_text(RULES + "\n" + facts)
        subprocess.run(["souffle", f"{d}/e.dl", "-D", d], check=True, capture_output=True)
        return {f.stem: {tuple(r) for r in csv.reader(f.open(), delimiter="\t")}    # only non-empty outputs:
                for f in Path(d).glob("*.csv") if f.stat().st_size > 0}              # absent rel -> empty set in run()


def run(state: dict, outputs: list[str]) -> dict:
    """Run the engine on `state`; return the requested output relations (memoized, pure)."""
    fkey = _facts_key(state)
    derived = _CACHE.get(fkey)
    if derived is None:
        derived = _CACHE[fkey] = _evaluate(fkey)
    return {rel: derived.get(rel, set()) for rel in outputs}


def _others(state: dict, p: str) -> list[str]:
    return sorted(q for (q,) in state["is_player"] if q != p)


def _next_active_player(state: dict, ap: str, players: list[str]) -> str:
    """§500.7 — who is the active player for the NEXT turn. Normally the next player in turn order; but if
    the current active player has a pending EXTRA TURN (effect_handlers/players.extra_turn set state
    ['_extra_turns'][ap]), they keep the turn — consume one extra-turn marker and stay active (§500.7 extra
    turns are taken by the same player before the turn passes)."""
    extra = state.setdefault("_extra_turns", {})
    if extra.get(ap, 0) > 0:
        extra[ap] -= 1
        print(f"    {ap} takes an extra turn (§500.7)")
        return ap
    return players[(players.index(ap) + 1) % len(players)]


def _creatures_of(state: dict, p: str) -> list[str]:
    """Creatures p controls — from the engine's DERIVED controls/creature (which fold in
    printed_control/printed_type and the layer system), not raw state, so a permanent that
    entered via casting is included just like one set up directly."""
    out = run(state, ["controls", "creature"])
    creatures = {c for (c,) in out["creature"]}
    return sorted(c for (pp, c) in out["controls"] if pp == p and c in creatures)


def _set_life(state: dict, p: str, n: int) -> None:
    state["life"] = {(q, v) for (q, v) in state["life"] if q != p} | {(p, n)}


def _adjust_life(state: dict, p: str, delta: int) -> int:
    cur = next(v for (q, v) in state["life"] if q == p)
    _set_life(state, p, cur + delta)
    return cur + delta


# predefined token characteristics, parsed once from the transpiled §111.10 slice.
def _load_token_defs() -> dict:
    defs: dict = {}
    for line in Path("datalog/token_defs.dl").read_text().splitlines():
        m = re.match(r'token_(pt|card_type)\("([^"]+)", "?([^",)]+)"?(?:, (\d+))?\)', line)
        if not m:
            continue
        kind, name, a, b = m.groups()
        d = defs.setdefault(name, {"types": []})
        if kind == "pt":
            d["pt"] = (int(a), int(b))
        else:
            d["types"].append(a)
    return defs


TOKEN_DEFS = _load_token_defs()


def _load_grant_priority_steps() -> set:
    """The steps in which the active player receives priority (and so may cast) — read from
    the §5 turn-structure rules interpreted into turn_actions.dl, not hardcoded here."""
    text = Path("datalog/turn_actions.dl").read_text()
    return set(re.findall(r'grants_priority\("([^"]+)"\)', text))


GRANTS_PRIORITY = _load_grant_priority_steps()


def _load_draw_skip_variants() -> set:
    """Game variants whose first player skips the draw step of their first turn (§103.8),
    interpreted into starting.dl — not hardcoded here."""
    text = Path("datalog/starting.dl").read_text()
    return {v for v, s in re.findall(r'first_turn_draw_skip\("([^"]+)", "([^"]+)"\)', text) if s == "yes"}


def _default_starting_life() -> int:
    """The default starting life total (§103.4), interpreted into starting.dl."""
    text = Path("datalog/starting.dl").read_text()
    return int(re.search(r'starting_life\("default", (\d+)\)', text).group(1))


def _life_loss_threshold() -> int:
    """The life total at or below which a player loses (§104.3b / §704.5a), interpreted into
    ending.dl — the same loss_threshold the engine reads, not a hardcoded 0."""
    text = Path("datalog/ending.dl").read_text()
    return int(re.search(r'loss_threshold\("life_zero", (-?\d+)\)', text).group(1))


def _load_keyword_abilities() -> frozenset:
    """The defined §702 keyword abilities (flying, trample, …), interpreted into
    keyword_ability_index.dl — the canonical keyword vocabulary. The engine's build-time
    conformance checks its test scenarios against this; the driver checks runtime states
    fed through engine_rules.dl (which carries no conformance) against the same roster."""
    text = Path("datalog/keyword_ability_index.dl").read_text()
    return frozenset(re.findall(r'keyword_ability_index\("[^"]+", "([^"]+)"\)', text))


KEYWORD_ABILITIES = _load_keyword_abilities()
# keyword-bearing input relations whose LAST column is a keyword name (validated below).
_KEYWORD_INPUTS = ("printed_keyword", "eff_grant_keyword", "eff_remove_keyword")


def assert_known_keywords(state: dict) -> None:
    """Guard a driver game state: every keyword it grants must be a defined §702 ability
    (§702 roster, interpreted). Catches a typo'd keyword before it silently does nothing in
    the engine — the runtime mirror of the engine's unknown_keyword conformance check."""
    unknown = {row[-1] for rel in _KEYWORD_INPUTS for row in state.get(rel, set())
               if row and row[-1] not in KEYWORD_ABILITIES}
    if unknown:
        raise ValueError(f"unknown keyword(s) not in the interpreted §702 roster: {sorted(unknown)}")


DRAW_SKIP_VARIANTS = _load_draw_skip_variants()        # {"two-player", "two-headed_giant"}
DEFAULT_LIFE = _default_starting_life()                # 20
LIFE_LOSS_THRESHOLD = _life_loss_threshold()           # 0 (§104.3b)
# §110.5b — permanents enter untapped/unflipped/face up/phased in; the driver never taps an
# entering permanent unless a §614 replacement (enters_tapped) says so, matching that default.


_TOKEN_TYPE_WORDS = {"creature", "artifact", "enchantment", "land", "planeswalker"}
_TOKEN_COLOR_WORDS = {"white", "blue", "black", "red", "green", "colorless"}


def _parse_token_spec(spec: str) -> dict:
    """§111.10 parse a token spec slug into characteristics. '1_1_white_soldier_creature' -> a 1/1 white
    Soldier creature; '2_2_black_zombie_creature'; '1_1_colorless_thopter_artifact_creature' (multi-type);
    a named token ('treasure'/'food'/'powerstone') -> a colorless artifact. Tokens are full permanents so
    lords/anthems and combat apply to them (a Goblin token gets the Goblin lord's buff)."""
    parts = str(spec).split("_")
    if len(parts) >= 3 and parts[0].lstrip("-").isdigit() and parts[1].lstrip("-").isdigit():
        rest = parts[2:]
        types = [w for w in rest if w in _TOKEN_TYPE_WORDS] or ["creature"]
        colors = [w for w in rest if w in _TOKEN_COLOR_WORDS and w != "colorless"]
        subtypes = [w for w in rest if w not in _TOKEN_TYPE_WORDS and w not in _TOKEN_COLOR_WORDS]
        return {"pt": (int(parts[0]), int(parts[1])), "types": types, "colors": colors, "subtypes": subtypes}
    d = TOKEN_DEFS.get(spec)                                  # a known named token (transpiled §111.10 defs)
    if d:
        return {"pt": d.get("pt"), "types": d.get("types") or ["artifact"], "colors": [], "subtypes": []}
    return {"pt": None, "types": ["artifact"], "colors": [], "subtypes": [spec]}   # food/treasure/clue/…


def _create_token(state: dict, spec: str, controller: str, n: int) -> None:
    d = _parse_token_spec(spec)
    for _ in range(n):
        state["_tok"] = state.get("_tok", 0) + 1
        tid = f"{spec}#{state['_tok']}"
        state.setdefault("on_battlefield", set()).add((tid,))             # printed_* only; the engine
        state.setdefault("printed_control", set()).add((controller, tid)) # derives controls/has_type/creature
        for t in d["types"]:
            state.setdefault("printed_type", set()).add((tid, t))
        for st in d.get("subtypes", []):                                  # §205.3 — so tribal lords reach tokens
            state.setdefault("printed_subtype", set()).add((tid, st))
        for col in d.get("colors", []):                                   # §105 — so color lords reach tokens
            state.setdefault("printed_color", set()).add((tid, col))
        if d.get("pt"):
            state.setdefault("printed_power", set()).add((tid, d["pt"][0]))
            state.setdefault("printed_toughness", set()).add((tid, d["pt"][1]))
        if "creature" in d["types"]:
            state.setdefault("_sick", set()).add((tid,))                  # §302.6 summoning sickness
        print(f"    {controller} creates a {spec} token ({tid})")


def _bump_counter(state: dict, obj: str, kind: str, n: int) -> None:
    cur = next((c for (o, k, c) in state.get("counter", set()) if o == obj and k == kind), 0)
    state.setdefault("counter", set()).discard((obj, kind, cur))
    state["counter"].add((obj, kind, cur + n))


def _apply_effects(state: dict, pending: set) -> None:
    """Apply the effects of triggered abilities the engine fired (§603 -> §608 resolution).
    Player targets: each_opponent -> all other players; controller/self -> the controller."""
    for (a, eff, amt, tgt, src, ctrl) in sorted(pending):
        n = int(amt)
        players = _others(state, ctrl) if tgt == "each_opponent" else [ctrl]
        if eff in ("lose_life", "deal_damage"):
            for p in players:
                print(f"    trigger {a}: {p} {'loses' if eff == 'lose_life' else 'takes'} {n} -> {_adjust_life(state, p, -n)} life")
        elif eff == "gain_life":
            for p in players:
                print(f"    trigger {a}: {p} gains {n} life -> {_adjust_life(state, p, n)}")
        elif eff == "draw":
            for _ in range(n):
                _draw(state, ctrl)
        elif eff == "mill":                                  # §701.13 — top n of library to graveyard
            for p in players:
                order = state.get("_lib_order", {}).get(p)
                for _ in range(n):
                    card = order.pop(0) if order else next(
                        (c for (pp, c) in sorted(state.get("in_library", set())) if pp == p), None)
                    if card is None:
                        break
                    state["in_library"].discard((p, card))
                    state.setdefault("graveyard", set()).add((card,))
                print(f"    trigger {a}: {p} mills {n}")
        elif eff == "discard":                               # §701.8 — discard n from hand
            at_random = "random" in str(tgt) or "random" in str(src)   # 'discard a card at random'
            for p in players:
                hand = sorted(c for (pp, c) in state.get("in_hand", set()) if pp == p)
                k = min(n, len(hand))
                for _ in range(k):
                    # a random discard is a CHANCE event (_random); a normal discard is the player's
                    # CHOICE (_choose). Either way the seam makes it observable to a policy/search.
                    card = (_random(state, "discard", hand) if at_random
                            else _choose(state, "discard", hand, hand[0]))
                    hand.remove(card)
                    state["in_hand"].discard((p, card))
                    state.setdefault("graveyard", set()).add((card,))
                if k:
                    print(f"    trigger {a}: {p} discards {k}{' at random' if at_random else ''}")
        elif eff == "add_counter":                           # tgt = counter kind (p1p1/m1m1), on the source
            _bump_counter(state, src, tgt, n)
            print(f"    trigger {a}: {src} gets {n} {tgt} counter(s)")
        elif eff == "create_token":                          # tgt = predefined token name
            _create_token(state, tgt, ctrl, n)
        elif eff == "fog":                                   # §615 Fog — prevent all combat damage this turn
            state.setdefault("prevent_all_combat", set()).add(("yes",))
            print(f"    {a}: all combat damage is prevented this turn")
        elif eff == "switchpt":                              # §613 layer 7d — switch the source's P/T until EOT
            eid = f"{a}__sw__{src}"
            state.setdefault("eff_switch_pt", set()).add((eid, src))
            state.setdefault("until_eot", set()).add((eid,))
            print(f"    {a}: {src} switches power and toughness until end of turn")
        elif eff == "animate":                               # §613 'becomes a P/T creature' (man-lands) until EOT
            dp, dt = (int(x) for x in tgt.split("/"))         # tgt carries the P/T; feeds the §613 layers
            eid = f"{a}__anim__{src}"
            state.setdefault("eff_set_power", set()).add((eid, src, dp, 1))
            state.setdefault("eff_set_toughness", set()).add((eid, src, dt, 1))
            state.setdefault("eff_add_type", set()).add((eid, src, "creature"))
            state.setdefault("until_eot", set()).add((eid,))  # §611.2 wears off at cleanup (still a land/etc.)
            print(f"    {a}: {src} becomes a {tgt} creature until end of turn")
        else:                                                # pluggable verbs (effect_handlers/*.py)
            h = effect_handlers.APPLY.get(eff)
            if h:
                h(_THIS, state, a, n, tgt, src, ctrl)
    _apply_creature_effects(state)                           # §603 creature-scoped P/T / grant / destroy


def _apply_creature_effects(state: dict) -> None:
    """Apply the engine's creature-SCOPED triggered effects (§603) to the resolved creatures: a P/T pump
    and a keyword grant are materialized as id-carrying, until-end-of-turn continuous effects (eff_mod_*,
    eff_grant_keyword) that re-derive through the §613 layer system and are cleared at cleanup; a destroy
    moves the creature to its owner's graveyard. The id is deterministic per (ability, creature) so re-
    deriving the same fire across steps is idempotent (set semantics — no double-buffing)."""
    out = run(state, ["pending_pt", "pending_grant", "pending_destroy",
                      "pending_exile", "pending_tap", "pending_untap", "pending_return",
                      "pending_target", "controls", "power", "creature", "cant_be_destroyed"])
    indestructible = {c for (c,) in out["cant_be_destroyed"]}   # §702.12b — the engine derives this
    # §603 a pump fired during a CAST window (cast_spell set) is a per-cast trigger (PROWESS, 'whenever you
    # cast …'): salt its effect id with the spell so SEVERAL casts STACK (+1/+1 each) instead of collapsing
    # to one under the deterministic per-(ability,creature) id. Outside a cast window the id stays stable
    # (idempotent re-derivation across steps — set semantics, no double-buffing).
    _cast_salt = "__" + next((s for (_p, s) in state.get("cast_spell", set())), "") if state.get("cast_spell") else ""
    for (a, dp, dt, c, _ctrl) in sorted(out["pending_pt"]):
        eid = f"{a}__pt__{c}{_cast_salt}"
        before = (eid, c, int(dp)) in state.get("eff_mod_power", set())
        state.setdefault("eff_mod_power", set()).add((eid, c, int(dp)))
        state.setdefault("eff_mod_toughness", set()).add((eid, c, int(dt)))
        state.setdefault("until_eot", set()).add((eid,))     # §611.2 wears off at cleanup
        if not before:
            print(f"    trigger {a}: {c} gets {'+' if int(dp) >= 0 else ''}{dp}/{'+' if int(dt) >= 0 else ''}{dt} until end of turn")
    for (a, kw, c, _ctrl) in sorted(out["pending_grant"]):
        eid = f"{a}__kw__{kw}__{c}"
        before = (eid, c, kw) in state.get("eff_grant_keyword", set())
        state.setdefault("eff_grant_keyword", set()).add((eid, c, kw))
        state.setdefault("until_eot", set()).add((eid,))
        if not before:
            print(f"    trigger {a}: {c} gains {kw} until end of turn")
    for (a, c, _ctrl) in sorted(out["pending_destroy"]):
        if (c,) in state.get("on_battlefield", set()):       # §701.7 — move it to the graveyard
            if c in indestructible:                          # §702.12b — indestructible isn't destroyed
                print(f"    trigger {a}: {c} can't be destroyed (indestructible)")
                continue
            state["on_battlefield"].discard((c,))
            state.setdefault("graveyard", set()).add((c,))
            print(f"    trigger {a}: {c} is destroyed -> graveyard")
    # §701 one-shot zone moves on the resolved creatures (no duration to clear at cleanup).
    for (a, c, _ctrl) in sorted(out["pending_exile"]):
        if (c,) in state.get("on_battlefield", set()):       # §701.10 — move it to exile
            state["on_battlefield"].discard((c,))
            state.setdefault("exile", set()).add((c,))
            print(f"    trigger {a}: {c} is exiled -> exile")
    for (a, c, ctrl) in sorted(out["pending_return"]):
        if (c,) in state.get("on_battlefield", set()):       # §701.21 bounce — move it to its controller's hand
            state["on_battlefield"].discard((c,))
            state.setdefault("in_hand", set()).add((ctrl, c))
            print(f"    trigger {a}: {c} is returned to {ctrl}'s hand")
    for (a, c, _ctrl) in sorted(out["pending_tap"]):
        if (c,) in state.get("on_battlefield", set()) and (c,) not in state.get("tapped", set()):
            state.setdefault("tapped", set()).add((c,))       # §701.20 tap
            print(f"    trigger {a}: {c} is tapped")
    for (a, c, _ctrl) in sorted(out["pending_untap"]):
        if (c,) in state.get("on_battlefield", set()) and (c,) in state.get("tapped", set()):
            state["tapped"].discard((c,))                     # §701.20 untap
            print(f"    trigger {a}: {c} is untapped")

    # §115 SINGLE-TARGET effects: the engine surfaces the firing + legal-target class; the driver makes
    # the §601.2c choice. controls(player, creature) and power give the board; the verb's polarity picks
    # whether to hit the strongest legal enemy (removal/tap/bounce/shrink) or buff the strongest own.
    controls = {(p, c) for (p, c) in out["controls"]}
    powers = {c: int(n) for (c, n) in out["power"]}
    creatures = {c for (c,) in out["creature"]}
    owner_of = {c: p for (p, c) in controls}
    for (a, s, verb, payload, cls, ctrl) in sorted(out["pending_target"]):
        tgt = _pick_target(state, ctrl, cls, verb, payload, controls, powers, creatures)
        if tgt is not None:
            _apply_target_verb(state, a, "trigger", verb, payload, tgt, ctrl, indestructible, owner_of)
    # §120 triggered direct damage (Flametongue Kavu): the driver picks the damage target it surfaced.
    for (a, s, n, kind, ctrl) in sorted(run(state, ["pending_damage"])["pending_damage"]):
        _apply_damage(state, a, int(n), kind, ctrl)
    # §701 triggered reanimation (Reya Dawnbringer): the driver moves the best graveyard creature. Guarded
    # against a re-derived trigger reanimating twice in one firing window (the move isn't self-idempotent).
    for (a, s, mode, ctrl) in sorted(run(state, ["pending_reanimate"])["pending_reanimate"]):
        if (a, s) in state.setdefault("_reanimated", set()):
            continue
        state["_reanimated"].add((a, s))
        _reanimate_one(state, a, ctrl, mode)
    _aura_sba(state)                                          # §704.5n an Aura whose host left -> graveyard


def _aura_sba(state: dict) -> None:
    """§704.5 state-based actions on attachments whose host has left the battlefield: an Aura is put into
    its owner's graveyard (§704.5n), an Equipment merely becomes unattached and stays (§704.5q). Either way
    the attachment is cleared so the static buff stops applying."""
    bf = state.get("on_battlefield", set())
    subtype = state.get("printed_subtype", set())
    for (perm, host) in sorted(state.get("attached_to", set())):
        if (host,) not in bf:
            state["attached_to"].discard((perm, host))
            if state.get("eff_gain_control"):                # a control-Aura's steal ends with the attachment
                state["eff_gain_control"] = {r for r in state["eff_gain_control"] if r[0] != f"{perm}__ctrl"}
            if (perm, "aura") in subtype and (perm,) in bf:  # §704.5n an Aura with no legal host dies
                bf.discard((perm,))
                state.setdefault("graveyard", set()).add((perm,))
                print(f"    {perm} falls off (host {host} gone) -> graveyard")
            else:                                            # §704.5q an Equipment just unattaches
                print(f"    {perm} becomes unattached (host {host} gone)")


# Verbs that HURT the targeted creature -> aim at the opponent's board; the rest BENEFIT it -> aim own.
_HARMFUL_TARGET = {"destroy", "exile", "tap", "return_to_hand", "switchpt"}


def _apply_target_verb(state: dict, a: str, kind: str, verb: str, payload: str, tgt: str,
                       ctrl: str, indestructible: set, owner_of: dict) -> None:
    """Apply one resolved single-target creature verb to the already-chosen `tgt`. Shared by §603
    triggered abilities (kind='trigger') and §608 instant/sorcery resolution (kind='spell'). A P/T
    pump or keyword grant is an until-EOT continuous effect; destroy/exile/return/tap/untap are §701
    one-shot zone/state moves. `kind` only flavors the log line."""
    if verb == "modify_pt":
        dp, dt = (int(x) for x in payload.split("/"))
        eid = f"{a}__pt__{tgt}"
        state.setdefault("eff_mod_power", set()).add((eid, tgt, dp))
        state.setdefault("eff_mod_toughness", set()).add((eid, tgt, dt))
        state.setdefault("until_eot", set()).add((eid,))
        print(f"    {kind} {a}: targets {tgt} for {'+' if dp >= 0 else ''}{dp}/{'+' if dt >= 0 else ''}{dt} until end of turn")
    elif verb == "switchpt":                                 # §613 layer 7d switch the target's P/T until EOT
        eid = f"{a}__sw__{tgt}"
        state.setdefault("eff_switch_pt", set()).add((eid, tgt))
        state.setdefault("until_eot", set()).add((eid,))
        print(f"    {kind} {a}: switches {tgt}'s power and toughness until end of turn")
    elif verb == "counter":                                  # §122 put N +1/+1 or -1/-1 counters (PERSISTENT)
        ckind, n = payload.split(":")
        # counters are cumulative, but a triggered pending_target is RE-DERIVED on every _apply_creature_
        # effects pass (unlike a one-shot spell or a diffed pending) — guard so one firing adds them once.
        seen = (a, tgt, ckind, int(n))
        if seen in state.setdefault("_counter_applied", set()):
            return
        state["_counter_applied"].add(seen)
        _bump_counter(state, tgt, ckind, int(n))
        print(f"    {kind} {a}: puts {n} {ckind} counter(s) on {tgt}")
    elif verb == "grant":
        eid = f"{a}__kw__{payload}__{tgt}"
        state.setdefault("eff_grant_keyword", set()).add((eid, tgt, payload))
        state.setdefault("until_eot", set()).add((eid,))
        print(f"    {kind} {a}: targets {tgt}, grants {payload} until end of turn")
    elif verb == "destroy":
        if tgt in indestructible:
            print(f"    {kind} {a}: targets {tgt} but it can't be destroyed (indestructible)")
            return
        state["on_battlefield"].discard((tgt,))
        state.setdefault("graveyard", set()).add((tgt,))
        print(f"    {kind} {a}: destroys target {tgt} -> graveyard")
    elif verb == "exile":
        state["on_battlefield"].discard((tgt,))
        state.setdefault("exile", set()).add((tgt,))
        print(f"    {kind} {a}: exiles target {tgt} -> exile")
    elif verb == "return_to_hand":
        state["on_battlefield"].discard((tgt,))
        state.setdefault("in_hand", set()).add((owner_of.get(tgt, ctrl), tgt))
        print(f"    {kind} {a}: returns target {tgt} to {owner_of.get(tgt, ctrl)}'s hand")
    elif verb == "tap":
        if (tgt,) not in state.get("tapped", set()):
            state.setdefault("tapped", set()).add((tgt,))
            print(f"    {kind} {a}: taps target {tgt}")
    elif verb == "untap":
        if (tgt,) in state.get("tapped", set()):
            state["tapped"].discard((tgt,))
            print(f"    {kind} {a}: untaps target {tgt}")


# perm[_own]_<token> class -> the printed types a candidate permanent must match (ANY of), or a special
# token ('nonland' / 'noncreature' / 'any'). The class string is opaque to the bridge and flows through
# the datalog target_class straight to here. An 'own_' segment restricts the candidates to the controller's
# permanents ('you own/you control' targets, e.g. Get Out's protective self-bounce).
_PERM_FILTER = {
    "artifact": ("artifact",), "enchantment": ("enchantment",),
    "artifact_enchantment": ("artifact", "enchantment"),
    "creature_enchantment": ("creature", "enchantment"),
    "cep": ("creature", "enchantment", "planeswalker"),
    "acep": ("artifact", "creature", "enchantment", "planeswalker"),   # Otawara: artifact/creature/ench/pw
    "noncreature": ("noncreature",), "nonland": ("nonland",), "any": ("any",),
}


def _perm_candidates(state: dict, cls: str, creatures: set, ctrl: str | None = None) -> list[str]:
    """§115 the on-battlefield permanents matching a perm[_own]_<token> target class. Type is read from the
    surfaced printed_type, with the engine's DERIVED `creature` folded in so an animated land / token counts
    as a creature. 'nonland' = any permanent without a printed land type; 'noncreature' = not a creature;
    'any' = every permanent. A leading 'own_' restricts to the controller's permanents (printed_control)."""
    body = cls[len("perm_"):] if cls.startswith("perm_") else cls
    own = body.startswith("own_")
    opp = body.startswith("opp_")                              # §115.4 'you don't control / an opponent controls'
    if own:
        body = body[len("own_"):]
    elif opp:
        body = body[len("opp_"):]
    want = _PERM_FILTER.get(body, ())
    on_bf = sorted(c for (c,) in state.get("on_battlefield", set()))
    ptype = state.get("printed_type", set())
    mine = {c for (p, c) in state.get("printed_control", set()) if p == ctrl}

    def matches(c: str) -> bool:
        if own and c not in mine:
            return False
        if opp and c in mine:                                 # an opponent-controlled restriction excludes mine
            return False
        types = {t for (o, t) in ptype if o == c}
        if c in creatures:
            types.add("creature")
        if want == ("any",):
            return True
        if want == ("nonland",):
            return "land" not in types
        if want == ("noncreature",):
            return "creature" not in types
        return any(t in types for t in want)

    return [c for c in on_bf if matches(c)]


def _pick_target(state: dict, ctrl: str, cls: str, verb: str, payload: str,
                 controls: set, powers: dict, creatures: set) -> str | None:
    """§601.2c choose a legal target for a single-target effect. `cls` constrains the legal set
    (any / you_control / opponent for creatures; perm_<filter> for non-creature permanents); within it,
    a harmful verb (removal/tap/bounce, or a P/T shrink) picks the strongest enemy and a beneficial one
    the strongest own permanent."""
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in controls if p == ctrl}
    if cls.startswith("perm_"):                              # §115 non-creature permanent target (Abrade, bounce)
        cands = _perm_candidates(state, cls, creatures, ctrl)
    else:
        cands = [c for c in creatures if c in on_bf]
        if cls == "you_control":
            cands = [c for c in cands if c in mine]
        elif cls == "opponent":
            cands = [c for c in cands if c not in mine]
    if not cands:
        return None
    harmful = verb in _HARMFUL_TARGET
    if verb == "modify_pt":                                   # a net-negative pump is removal-flavored
        dp, dt = (int(x) for x in payload.split("/"))
        harmful = (dp + dt) < 0
    elif verb == "counter":                                  # a -1/-1 counter is removal; +1/+1 is a buff
        harmful = payload.startswith("m1m1")
    # prefer enemy creatures for harmful effects, own creatures for beneficial ones, then strongest.
    def keyf(c):
        own = c in mine
        prefer = (not own) if harmful else own
        return (prefer, powers.get(c, 0))
    greedy = max(cands, key=keyf)
    return _choose(state, "target", sorted(cands), greedy)    # §601.2c — the target choice (referee seam)


def _sacrifice(state: dict, obj: str) -> None:
    """§701.17 sacrifice — a §603.10a look-back event. Set `sacrificed` so any "when this is
    sacrificed" triggers fire against the still-present permanent (the engine derives them via
    ev_sacrifice -> fires -> pending), apply their effects, then move it to its owner's graveyard.
    phased_out / countered are symmetric — same shape, different event input."""
    print(f"    {obj} is sacrificed")
    state["sacrificed"] = {(obj,)}
    _apply_effects(state, run(state, ["pending"])["pending"])
    state["sacrificed"] = set()
    state["on_battlefield"].discard((obj,))
    state.setdefault("graveyard", set()).add((obj,))


def declare_attackers(state: dict, ap: str) -> None:
    """§508 — the active player's eligible creatures attack an opponent (greedy policy)."""
    opp = _others(state, ap)[0]
    sick = state.get("_sick", set())                             # §302.6 — entered this turn, no haste
    haste = {c for (c, k) in run(state, ["has_keyword"])["has_keyword"] if k == "haste"}  # granted-aware (§613 layer 6)
    eligible = sorted(c for (c,) in run(state, ["may_attack"])["may_attack"]
                      if (c,) not in sick or c in haste)
    # §508 the attacker-SET choice (referee seam): default greedy = attack with everything eligible.
    # options=None (a SET-valued choice, not an atom) — legality is enforced by the `c in eligible` clamp.
    chosen = _choose(state, "attackers", None, frozenset(eligible))
    attackers = sorted(c for c in eligible if c in chosen)
    state["attacks"] = {(c, opp) for c in attackers}
    if attackers:
        print(f"    {ap} attacks {opp} with {', '.join(attackers)}")


def declare_blockers(state: dict, ap: str) -> None:
    """§509 — the defending player blocks attackers one-for-one. Each UNTAPPED blocker is assigned to the
    first attacker it can LEGALLY block, using the engine's illegal_block (§509.1b — so flying is only
    blocked by flying/reach, etc.) rather than a naive pairing that wastes a blocker on an illegal block."""
    opp = _others(state, ap)[0]
    attackers = sorted(a for (a, _) in state.get("attacks", set()))
    blockers = [b for b in _creatures_of(state, opp) if (b,) not in state.get("tapped", set())]
    blocks: dict = {}                                            # attacker -> blocker (one blocker each)
    for b in blockers:
        for a in attackers:
            if a in blocks:
                continue
            probe = dict(state); probe["blocks"] = {(b, a)}     # ask the engine whether this block is legal
            if (b, a) not in run(probe, ["illegal_block"])["illegal_block"]:
                blocks[a] = b
                break
    greedy = frozenset((b, a) for a, b in blocks.items())
    # §509 the block-ASSIGNMENT choice (referee seam): default greedy = one legal blocker per attacker.
    chosen = _choose(state, "blocks", None, greedy)
    state["blocks"] = set(chosen)
    for b, a in sorted(state["blocks"]):
        print(f"    {opp} blocks {a} with {b}")


def _draw(state: dict, p: str) -> bool:
    """Active player draws the top of their library; False if the library is empty
    (§104.3c — that player loses the game). Honors a real library ORDER (`_lib_order`, driver
    bookkeeping a shuffled deck sets) so draws come off the true top; falls back to any card."""
    order = state.get("_lib_order", {}).get(p)
    card = None
    if order:
        card = order.pop(0)
    else:
        lib = sorted(c for (pp, c) in state.get("in_library", set()) if pp == p)
        card = lib[0] if lib else None
    if card is None:
        return False
    state["in_library"].discard((p, card))
    state.setdefault("in_hand", set()).add((p, card))
    print(f"    {p} draws {card}")
    return True


def _apply_outputs(state: dict, out: dict, ap: str) -> str | None:
    """Apply everything the engine derived for this step, in order; return a loser if
    one is decided this step (else None). This is the whole 'driver acts on engine
    output' surface — every consequence the engine flags is handled here."""
    # derived relations are sets; iterate them sorted so behavior is canonical regardless of the
    # backend's row order (the souffle interpreter and the compiled binary emit sets in different orders).
    for (c,) in sorted(out["to_untap"]):                         # §502.3 untap
        state["tapped"].discard((c,)); print(f"    {ap} untaps {c}")
    for (p,) in sorted(out["to_draw"]):                          # §504.1 draw
        if not _draw(state, p):
            # §104.3c — drawing from an empty library is a LOSS, UNLESS a §614 "you win when your library
            # is empty" replacement (Laboratory Maniac / Thassa's Oracle / Jace, Wielder of Mysteries) the
            # player controls turns it into a WIN. The ENGINE derives which: feed the would-draw-from-empty
            # moment and read back wins_game / loses_game over the library_win_repl the driver bookkeeps.
            probe = run({**state, "would_draw_from_empty": state.get("would_draw_from_empty", set()) | {(p,)}},
                        ["wins_game", "loses_game"])
            if (p,) in probe["wins_game"]:
                print(f"  ** {p}'s library is empty — a §614 replacement makes {p} WIN the game **")
                return _end_with_winner(state, p)
            print(f"  ** {p} draws from an empty library and loses the game (§104.3c) **")
            return p
    for (c, frm, to) in sorted(out["zone_change"]):              # §701.8a zone moves
        state.setdefault(ZONE[frm], set()).discard((c,))
        # §903.9 / §704.5 commander replacement: a commander headed to graveyard/exile (or hand/library)
        # MAY instead go to the command zone (a _choose decision); if taken, skip the normal destination.
        if _is_commander(state, c) and to in ("graveyard", "exile", "hand", "library") \
                and _commander_replacement(state, c, to):
            continue
        state.setdefault(ZONE[to], set()).add((c,))
        verb = "dies" if (frm, to) == ("battlefield", "graveyard") else f"moves {frm}"
        print(f"    {c} {verb} -> {to}")
    for (p, n) in sorted(out["player_damage"]):                  # §510.2 persist combat damage
        print(f"    {p} takes {n} -> {_adjust_life(state, p, -int(n))} life")
    for (p, cmd, n) in sorted(out.get("combat_commander_damage", set())):   # §903.10a accrue commander damage
        cd = state.setdefault("commander_damage", set())          # carried per-(player, commander) total
        old = next((b for (pp, cc, b) in cd if pp == p and cc == cmd), 0)
        cd.discard((p, cmd, old)); cd.add((p, cmd, old + int(n)))
        print(f"    {p} has now taken {old + int(n)} combat damage from commander {cmd} (§903.10a)")
    for (p, n) in sorted(out.get("combat_poison", set())):        # §704.5c accrue infect poison across turns
        pz = state.setdefault("poison", set())                    # carried per-player poison total (like life takes damage)
        old = next((b for (pp, b) in pz if pp == p), 0)
        pz.discard((p, old)); pz.add((p, old + int(n)))
        print(f"    {p} now has {old + int(n)} poison counters (§704.5c)")
    _apply_effects(state, out["pending"])                        # §603 -> §608 triggered effects
    # §104.2a — an effect-derived WIN ends the game: the winner wins, every other player loses.
    # The engine derives wins_game from a resolved "you win the game" effect (Thassa's Oracle, Approach
    # of the Second Sun, Felidar Sovereign, Test of Endurance). Read it AFTER applying pending effects,
    # since a resolving trigger may have asserted eff_win_game into the state this step.
    won = run(state, ["wins_game"])["wins_game"]
    if won:
        return _end_with_winner(state, sorted(won)[0][0])
    # §704.5a life threshold / §704.5c poison / §104.3a effect loss — all surfaced as loses_game by the
    # engine. dead[] re-derives the life threshold directly as a backstop (a triggered effect may have
    # dropped a life total below the engine's view of `out` captured before _apply_effects ran).
    # The probe CLEARS attacks/blocks: this combat's damage was already persisted above (player_damage ->
    # life, combat_commander_damage -> carried total), so leaving `attacks` set would let the engine's
    # combat_now()->deals re-derive that same damage and subtract it AGAIN — turning a NON-lethal swing
    # (opp at 5 taking 3) into a phantom loss (5-3=2, then 2-3=-1). Lethal combat losses are already
    # captured in out["loses_game"] (computed from base life, before this step's damage was applied); this
    # re-run exists only to catch NON-combat triggered-effect losses, so suppressing combat here is exact.
    lost = run({**state, "attacks": set(), "blocks": set()}, ["loses_game"])["loses_game"]
    dead = sorted(p for (p, v) in state["life"] if v <= LIFE_LOSS_THRESHOLD)
    if out["loses_game"] or lost or dead:                        # §704.5a / §104.3a triggered-effect death
        loser = (sorted(out["loses_game"])[0][0] if out["loses_game"]
                 else sorted(lost)[0][0] if lost else dead[0])
        print(f"  ** {loser} loses the game **")
        return loser
    return None


def _end_with_winner(state: dict, winner: str) -> str:
    """§104.2a — `winner` wins the game; in this driver's loss-returning contract that means every other
    player loses. Print the win and return a losing opponent (the lone opponent in a two-player game) so
    play_game ends; records the winner in state['_winner'] for callers that want it."""
    state["_winner"] = winner
    others = _others(state, winner)
    for p in others:
        print(f"  ** {winner} WINS the game -> {p} loses (§104.2a) **")
    return others[0] if others else winner


# §106.1a — the five WUBRG colors plus colorless. WILDCARD = a source produces "any color" (Birds,
# Chromatic Lantern); a frozenset = a restricted choice (Noble Hierarch -> {G/W/U}). Colorless mana
# can't pay a colored pip, so a wildcard's spendable colors are the five WUBRG.
_COLORS = ("white", "blue", "black", "red", "green", "colorless")
_WUBRG = ("white", "blue", "black", "red", "green")
ANY = frozenset(_WUBRG)                                        # produced "any color" — spends as any WUBRG pip


def _source_units(state: dict, ap: str):
    """Every untapped mana SOURCE the active player controls, with the REAL mana it taps for (§106/§605).
    Yields (source_id, units, cost_generic, taps_self) where `units` is the list of mana the source
    produces — each a concrete color string ('green'/'colorless') OR a frozenset of allowed WUBRG colors
    (a wildcard: ANY for 'any color', a smaller set for 'X or Y'). `cost_generic` is generic mana that
    must be paid to activate it (Signets: {1}); `taps_self` whether activating taps the source.

    LANDS use land_produces (one color per land, basics monocolor). NON-LAND sources use the precise
    source_produces / source_wildcard rows the bridge lexed from oracle text (Sol Ring -> [colorless,
    colorless]; Dimir Signet -> [blue,black] costing {1}). A source with NO precise row but flagged
    mana_source (parse said 'has a mana ability' but output abstained) falls back to one colorless mana,
    preserving the legacy behavior for un-lexed dorks."""
    bf, ctrl, tapped = state.get("on_battlefield", set()), state.get("printed_control", set()), state.get("tapped", set())
    sick = state.get("_sick", set())
    produces = state.get("land_produces", set())
    s_fixed = state.get("source_produces", set())             # (tid, color, amount)
    s_wild = state.get("source_wildcard", set())              # (tid, kind, amount)
    s_cost = state.get("source_cost", set())                  # (tid, generic, taps_self)
    precise = {t for (t, _c, _a) in s_fixed} | {t for (t, _k, _a) in s_wild}

    lands = sorted(c for (c,) in bf if (c, "land") in state.get("printed_type", set())
                   and (ap, c) in ctrl and (c,) not in tapped)
    for c in lands:                                           # §106 a land taps for one mana of a color it makes
        cols = sorted(col for (s, col) in produces if s == c)
        # a DUAL/any-color land (Underground Sea, City of Brass) is FLEXIBLE: a frozenset wildcard the pool
        # aims at the hand's demand (§106.6 the player picks the color). A basic makes its single color.
        unit = frozenset(cols) if len(cols) > 1 else (cols[0] if cols else "colorless")
        yield (c, [unit], 0, True)

    # non-land sources controlled by ap and untapped. §302.6 summoning sickness only blocks a CREATURE's
    # {T} mana ability (a dork that entered this turn) — a mana ROCK (artifact) taps the turn it enters.
    is_creature = state.get("printed_type", set())
    rest = sorted(c for (c,) in bf if (ap, c) in ctrl and (c,) not in tapped
                  and (c, "land") not in state.get("printed_type", set())
                  and not ((c, "creature") in is_creature and (c,) in sick)
                  and (c in precise or (c,) in state.get("mana_source", set())))
    for c in rest:
        if c in precise:
            units: list = []
            for (t, col, amt) in s_fixed:
                if t == c:
                    units += [col] * int(amt)
            for (t, kind, amt) in s_wild:
                if t == c:
                    if kind in _SAME_COLOR_KINDS and int(amt) > 1:
                        # §106 'add N mana of any ONE color' (Black Lotus): a bundle — all N share one
                        # chosen color, NOT N independent wildcards (which would fabricate impossible
                        # multi-color mana). A ('one', colorset, n) unit the pool resolves to one color.
                        units.append(("one", _wildcard_set(kind), int(amt)))
                    else:
                        units += [_wildcard_set(kind)] * int(amt)   # independent wildcard mana (any color)
            cg = next((int(g) for (t, g, _ts) in s_cost if t == c), 0)
            ts = next((bool(ts) for (t, _g, ts) in s_cost if t == c), True)
            yield (c, units, cg, ts)
        else:                                                # legacy un-lexed dork: one colorless mana (§605)
            yield (c, ["colorless"], 0, True)


# §106 mana descriptors where N mana must all be ONE chosen color (a bundle), not N independent wildcards.
_SAME_COLOR_KINDS = {"any_one_color", "chosen_color"}


def _wildcard_set(kind: str) -> frozenset:
    """The set of WUBRG colors a wildcard mana descriptor can pay (§106). 'any color' & relatives -> all
    five; a 'green_or_white' choice -> just those. A colorless-only descriptor isn't a wildcard."""
    if "_or_" in kind:
        parts = [p for p in kind.split("_or_") if p in _WUBRG]
        return frozenset(parts) if parts else ANY
    return ANY                                                # any_color / any_one_color / chosen / commander identity


def _untapped_sources(state: dict, ap: str) -> list[tuple[str, str | None]]:
    """LEGACY (id, color) view of the active player's untapped sources, kept for callers that only need
    a flat per-source color. A multi-mana source appears once per mana it makes; a wildcard collapses to
    a representative WUBRG color (or 'colorless'). Precise payment goes through _source_units instead."""
    out: list[tuple[str, str | None]] = []
    for sid, units, _cg, _ts in _source_units(state, ap):
        for u in units:
            if isinstance(u, tuple) and u and u[0] == "one":   # an 'N of one color' bundle -> n of a rep color
                out += [(sid, next(iter(sorted(u[1]))))] * u[2]
            else:
                out.append((sid, next(iter(sorted(u))) if isinstance(u, frozenset) else u))
    return out


def _develop_mana(state: dict, ap: str) -> None:
    """Driver-side §305 land mechanics the datalog engine leaves to the apply-and-loop. Play ONE land
    this turn (§305.2) from the active player's hand, then refresh its COLORED mana pool (§106) from the
    untapped lands it controls — each contributes one mana of its produced color (land_produces). The
    engine authors casting legality (can_cast/can_afford over mana_pool); this only stocks the pool. A
    flat mana_available count is kept in sync for the legacy fallback / cache continuity."""
    played = state.setdefault("_land_played", set())          # driver bookkeeping; not a souffle relation
    if (ap,) not in played:
        land = next((s for (p, s) in sorted(state.get("in_hand", set()))
                     if p == ap and (s, "land") in state.get("spell_type", set())), None)
        if land:
            state["in_hand"].discard((ap, land))
            state["on_battlefield"].add((land,))
            state.setdefault("printed_control", set()).add((ap, land))
            played.add((ap,))
            print(f"    {ap} plays land {land}")
            # §603 LANDFALL — a played land enters the battlefield without using the stack, so signal
            # just_entered(land) so the engine fires 'whenever a land you control enters' triggers, apply
            # their effects, then clear the one-step signal (it must not persist past this land drop).
            state.setdefault("just_entered", set()).add((land,))
            _apply_effects(state, run(state, ["pending"])["pending"])
            state["just_entered"].discard((land,))
    _refresh_mana_pool(state, ap)


def _controls_any_source(state: dict, ap: str) -> bool:
    """True if ap controls ANY driver-managed mana source on the battlefield — a land, a flagged
    mana_source, or a precise (lexed) rock/dork — TAPPED OR NOT. Distinguishes a real board (whose pool
    is rebuilt from sources, reading 0 when all are tapped) from a pure pre-seeded demo state (whose
    hand-set mana_pool/mana_available must be left untouched)."""
    bf, ctrl = state.get("on_battlefield", set()), state.get("printed_control", set())
    ptype = state.get("printed_type", set())
    precise = {t for (t, _c, _a) in state.get("source_produces", set())} \
        | {t for (t, _k, _a) in state.get("source_wildcard", set())}
    for (c,) in bf:
        if (ap, c) not in ctrl:
            continue
        if (c, "land") in ptype or (c,) in state.get("mana_source", set()) or c in precise:
            return True
    return False


def _mana_demand(state: dict, ap: str) -> list[str]:
    """The colored pips ap's HAND wants to pay, as a flat color list (a {U}{B} spell -> ['blue','black']),
    used to aim wildcard mana (§106.6 a player chooses the color when a source could make several). A
    color appearing in more castable pips is wanted more, so wildcards fill real demand first."""
    want: dict[str, int] = {}
    hand = {s for (p, s) in state.get("in_hand", set()) if p == ap}
    for (s, col, n) in state.get("mana_pip", set()):
        if s in hand and col in _WUBRG:
            want[col] = want.get(col, 0) + int(n)
    return [c for c in sorted(want, key=lambda c: -want[c]) for _ in range(want[c])]


# --- §106.4 FLOATING MANA — mana actually IN the pool right now (produced and not yet spent: a ritual's
# output, or a source's excess over a cost). It PERSISTS across spells within a step and empties at the
# end of each step/phase (§500.4). Distinct from the tappable POTENTIAL of untapped sources — affordability
# (mana_pool) is floating + potential, but a spell SPENDS floating first, and Forge's live pool syncs into
# it (so the tree search reasons over the real floating mana). Stored as floating_mana(player, color, n). ---
def _floating(state: dict, p: str) -> dict:
    return {c: n for (pp, c, n) in state.get("floating_mana", set()) if pp == p and n > 0}


def _set_floating(state: dict, p: str, by_color: dict) -> None:
    state["floating_mana"] = {(pp, c, n) for (pp, c, n) in state.get("floating_mana", set()) if pp != p} \
        | {(p, c, int(n)) for c, n in by_color.items() if int(n) > 0}


def _add_floating(state: dict, p: str, by_color: dict) -> None:
    fl = _floating(state, p)
    for c, n in by_color.items():
        fl[c] = fl.get(c, 0) + int(n)
    _set_floating(state, p, fl)


def _empty_mana_pool(state: dict) -> None:
    """§500.4 — at the end of each step and phase, every player's mana pool empties. Called on every step
    transition so floating mana never leaks across steps."""
    state["floating_mana"] = set()


def _resolve_pool(state: dict, ap: str):
    """Turn ap's untapped sources into a CONCRETE {color: count} §106 pool the engine can check pips
    against, assigning each wildcard mana to a color the hand demands (then a default spread) so 'any
    color' sources actually pay colored costs. Subtracts each source's activation cost (Signets pay {1})
    from generic mana first — a source that can't net positive isn't counted. §106.4 FLOATING mana already
    in the pool is added on top. Returns (by_color, total) or None when ap controls NO source AND has no
    floating mana (a pre-seeded demo pool is left untouched). A player whose sources are all TAPPED returns
    its floating mana (or ({}, 0)) — a fully-spent board reads as its real remaining mana, not a stale count."""
    floating = _floating(state, ap)
    if not _controls_any_source(state, ap):
        if floating:                                          # no tappable source, but mana is floating
            return dict(floating), sum(floating.values())
        return None                                           # pure demo state: leave a pre-seeded pool alone
    units_rows = list(_source_units(state, ap))               # only the UNTAPPED ones
    if not units_rows:
        return dict(floating), sum(floating.values())         # all sources tapped -> just the floating mana
    fixed: dict[str, int] = {}
    wilds: list[frozenset] = []
    bundles: list[tuple[frozenset, int]] = []                 # ('add N of ONE color' — Black Lotus)
    cost_generic = 0
    for _sid, units, cg, _ts in units_rows:
        cost_generic += cg
        for u in units:
            if isinstance(u, tuple) and u and u[0] == "one":
                bundles.append((u[1], u[2]))
            elif isinstance(u, frozenset):
                wilds.append(u)
            else:
                fixed[u] = fixed.get(u, 0) + 1
    by_color = dict(fixed)
    # assign wildcards to the hand's demanded colors, filling the MOST-demanded color FULLY before the next
    # (a fixed priority by original want, so 2 Lotus Petals make {U}{U} for a {U}{U} spell rather than one
    # blue + one of some incidental other-color demand). CONSUMING remaining need as it's filled spreads
    # leftover wildcards across distinct pips; once all demand is met, a default WUBRG spread keeps the pool
    # colorful. Deterministic: the priority order is by (-want, color), independent of set iteration order.
    want: dict[str, int] = {}
    for col in _mana_demand(state, ap):                       # a flat demand list -> a consumable multiset
        want[col] = want.get(col, 0) + 1
    priority = sorted(want, key=lambda c: (-want[c], c))      # high-demand colors first, fully, then the rest
    remaining = dict(want)
    spread = ["green", "white", "blue", "black", "red"]

    def aim(colset, n):                                       # assign n mana of `colset` to demanded colors
        nonlocal by_color
        for _ in range(n):
            pick = next((c for c in priority if remaining.get(c, 0) > 0 and c in colset), None) \
                or next((c for c in spread if c in colset), None) or next(iter(sorted(colset)))
            if remaining.get(pick, 0) > 0:
                remaining[pick] -= 1
            by_color[pick] = by_color.get(pick, 0) + 1

    for w in wilds:                                           # independent wildcards (any color) — one at a time
        aim(w, 1)
    # §106 same-color bundles: all N mana go to ONE color — the most-demanded color the bundle can make.
    for colset, n in bundles:
        pick = next((c for c in priority if remaining.get(c, 0) > 0 and c in colset), None) \
            or next((c for c in spread if c in colset), None) or next(iter(sorted(colset)))
        for _ in range(n):
            if remaining.get(pick, 0) > 0:
                remaining[pick] -= 1
        by_color[pick] = by_color.get(pick, 0) + n
    # pay each source's activation cost from generic (colorless first, then any color) — net the pool.
    for _ in range(cost_generic):
        donor = "colorless" if by_color.get("colorless", 0) else next((c for c in by_color if by_color[c]), None)
        if donor is None:
            break
        by_color[donor] -= 1
    for c, n in floating.items():                             # §106.4 floating mana sits on top of source potential
        by_color[c] = by_color.get(c, 0) + n
    by_color = {c: n for c, n in by_color.items() if n > 0}
    return by_color, sum(by_color.values())


def _refresh_mana_pool(state: dict, ap: str) -> None:
    """Stock ap's CONCRETE colored mana pool (§106) from every untapped mana SOURCE it controls — real
    colors and amounts (Sol Ring -> 2 colorless, Llanowar Elves -> 1 green, a Signet -> its colors net of
    its {1} cost, a wildcard source aimed at a demanded color) — plus the flat mana_available count for
    the legacy fallback / cache continuity. The land-PLAY half lives in _develop_mana; this is the pool
    refresh alone, so a reconstructed board (e.g. the Forge bridge) can stock mana without a land drop."""
    resolved = _resolve_pool(state, ap)
    if resolved is None:
        return  # no driver-managed sources: leave any pre-seeded mana_pool/mana_available as-is (demos)
    by_color, total = resolved
    state["mana_pool"] = {(p, c, n) for (p, c, n) in state.get("mana_pool", set()) if p != ap} \
        | {(ap, col, n) for col, n in by_color.items()}
    state["mana_available"] = {(p, m) for (p, m) in state.get("mana_available", set()) if p != ap} | {(ap, total)}


def _sacrifice_source(state: dict, sid: str) -> None:
    """§118.3/§605 — a one-shot fast-mana source (Lotus Petal, Black Lotus) pays by being SACRIFICED, not
    tapped: move it off the battlefield to its owner's graveyard. (These cards carry no 'when sacrificed'
    trigger, so we skip the §603.10a look-back firing _sacrifice does — avoiding re-entrancy mid-payment.)"""
    state.get("on_battlefield", set()).discard((sid,))
    state.setdefault("graveyard", set()).add((sid,))
    state.get("tapped", set()).discard((sid,))


def _slots_bundles(units):
    """Split a source's mana `units` into single 1-mana slots (a color string or a wildcard frozenset) and
    same-color bundles ('one', colorset, n) where all n must be ONE color; plus the total mana count."""
    slots, bundles, total = [], [], 0
    for u in units:
        if isinstance(u, tuple) and u and u[0] == "one":
            bundles.append((u[1], u[2])); total += u[2]
        else:
            slots.append(u if isinstance(u, frozenset) else {u}); total += 1
    return slots, bundles, total


def _production(rows, demand_pips: dict) -> dict:
    """The {color: count} a set of (sid, units, cg, taps) source rows produces, aiming wildcards/bundles at
    `demand_pips` first (then a default WUBRG spread). Used to compute the EXCESS that floats after a payment
    (production minus the cost the sources covered) — same aiming as _resolve_pool, for a specific cost."""
    fixed: dict = {}
    wilds: list = []
    bundles: list = []
    cost_generic = 0
    for _sid, units, cg, _ts in rows:
        cost_generic += cg
        for u in units:
            if isinstance(u, tuple) and u and u[0] == "one":
                bundles.append((u[1], u[2]))
            elif isinstance(u, frozenset):
                wilds.append(u)
            else:
                fixed[u] = fixed.get(u, 0) + 1
    by_color = dict(fixed)
    remaining = dict(demand_pips)
    priority = sorted(demand_pips, key=lambda c: (-demand_pips[c], c))
    spread = ["green", "white", "blue", "black", "red"]

    def aim(colset, n):
        for _ in range(n):
            pick = next((c for c in priority if remaining.get(c, 0) > 0 and c in colset), None) \
                or next((c for c in spread if c in colset), None) or next(iter(sorted(colset)))
            if remaining.get(pick, 0) > 0:
                remaining[pick] -= 1
            by_color[pick] = by_color.get(pick, 0) + 1

    for w in wilds:
        aim(w, 1)
    for colset, n in bundles:
        pick = next((c for c in priority if remaining.get(c, 0) > 0 and c in colset), None) \
            or next((c for c in spread if c in colset), None) or next(iter(sorted(colset)))
        for _ in range(n):
            if remaining.get(pick, 0) > 0:
                remaining[pick] -= 1
        by_color[pick] = by_color.get(pick, 0) + n
    for _ in range(cost_generic):
        donor = "colorless" if by_color.get("colorless", 0) else next((c for c in by_color if by_color[c]), None)
        if donor is None:
            break
        by_color[donor] -= 1
    return {c: n for c, n in by_color.items() if n > 0}


def mana_plan(state: dict, ap: str, pips: dict, generic: int):
    """§106 — WHICH untapped sources `ap` should tap (and, for any-color/bundle sources, what COLOR each
    should produce) to pay a cost of `pips` (a {color: count} of colored pips) + `generic`. Mirrors
    _spend_mana's greedy source selection but RETURNS the plan instead of mutating, so an external engine
    (Forge) can execute the EXACT payment witchcraft intends — the precise sources/colors a combo can
    depend on (e.g. pay {B} from Mox Jet, NOT by sacrificing a Black Lotus needed later for {U}{U}).

    Returns a list of {"id": source, "express": color_to_force_or_'', "sacrifice": bool} in tap order, or
    None if the model can't cover the cost from untapped sources. `express` is set only for a flexible
    source (any-color / same-color bundle) — a fixed source produces its own color."""
    # §106.4 floating mana already in the pool pays first (Forge's payManaCostFromPool spends the pool before
    # tapping), so the SOURCE plan only needs to cover the remainder.
    need = dict(pips)
    ng = int(generic)
    floating = _floating(state, ap)
    for col in list(need):
        take = min(need[col], floating.get(col, 0))
        if take:
            need[col] -= take; floating[col] -= take
    for col in ["colorless"] + sorted(c for c in floating if c != "colorless"):
        if ng <= 0:
            break
        take = min(ng, floating.get(col, 0))
        if take:
            ng -= take; floating[col] -= take

    def _done():
        return not any(v > 0 for v in need.values()) and ng <= 0

    if _done():
        return []                                             # floating covers it all — no sources to tap
    rows = list(_source_units(state, ap))
    if not rows:
        return None

    def keyf(row):                                            # concrete sources first; flexible held for pips
        _sid, units, _cg, _ts = row
        flexcount = sum(1 for u in units if isinstance(u, (frozenset, tuple)))
        return (flexcount, sum(u[2] if isinstance(u, tuple) and u and u[0] == "one" else 1 for u in units))

    sacrifices = state.get("source_sacrifice", set())
    plan: list = []
    for sid, units, cg, _ts in sorted(rows, key=keyf):
        if _done():
            break
        slots, bundles, total = _slots_bundles(units)
        avail = total - cg
        if avail <= 0:
            continue
        express, contributed = None, False
        for colset in slots:                                 # single slots: pay a matching pip, else generic
            if avail <= 0:
                break
            hit = next((c for c in need if need[c] > 0 and c in colset), None)
            if hit is not None:
                need[hit] -= 1; avail -= 1; contributed = True
                if len(colset) > 1 and express is None:      # a wildcard slot -> express the chosen color
                    express = hit
            elif ng > 0:
                ng -= 1; avail -= 1; contributed = True
                if len(colset) > 1 and express is None:      # wildcard paying generic -> any allowed color
                    express = next(iter(sorted(colset)))
        for colset, n in bundles:                            # a same-color bundle: all n -> ONE color
            if avail <= 0:
                break
            color = max((c for c in need if need[c] > 0 and c in colset), key=lambda c: need[c], default=None)
            if color is None and ng > 0:
                color = next(iter(sorted(colset)))
            if color is None:
                continue
            give = min(n, avail)
            paid = min(need.get(color, 0), give)
            need[color] = need.get(color, 0) - paid
            ng -= max(0, give - paid)
            avail -= give; contributed = True
            if express is None:
                express = color
        if contributed:
            plan.append({"id": sid, "express": express or "", "sacrifice": (sid,) in sacrifices})
    return plan if _done() else None


def _spend_mana(state: dict, ap: str, spell: str) -> None:
    """Pay a spell's COLORED cost (§601.2g) by TAPPING untapped sources for their REAL mana. Each tapped
    source yields ALL its mana at once (§106.4: Sol Ring -> 2 colorless, a Signet -> its 2 colors after
    its {1}); we tap sources until every colored pip (from the right color, incl. wildcards) and the
    generic are covered. Tapping (not decrementing) deletes mana faithfully — a tapped source can't pay
    again this turn or attack, and untaps next turn. The pool is refreshed from what's left untapped so
    the rest of the cast loop sees the reduced mana. INVARIANT: only call when can_afford held."""
    pips: dict[str, int] = {}
    for (s, col, n) in state.get("mana_pip", set()):
        if s == spell:
            pips[col] = pips.get(col, 0) + int(n)
    generic = sum(int(n) for (s, n) in state.get("mana_generic", set()) if s == spell)
    if not pips and generic == 0 and (spell, generic) not in state.get("mana_generic", set()):
        generic = next((int(c) for (s, c) in state.get("mana_cost", set()) if s == spell), 0)  # legacy fallback

    # §106.4 spend FLOATING mana FIRST (it's already in the pool): colored pips from matching floating, then
    # generic from leftover floating (colorless preferred, to keep colored mana for colored pips). Only the
    # REMAINDER taps sources. This is what lets a ritual's mana carry across spells.
    floating = _floating(state, ap)
    for col in list(pips):
        take = min(pips[col], floating.get(col, 0))
        if take:
            pips[col] -= take; floating[col] -= take
    for col in ["colorless"] + sorted(c for c in floating if c != "colorless"):
        if generic <= 0:
            break
        take = min(generic, floating.get(col, 0))
        if take:
            generic -= take; floating[col] -= take
    _set_floating(state, ap, floating)

    rows = list(_source_units(state, ap))                     # (id, units, cost_generic, taps_self)
    if not rows:                                              # pre-seeded flat mana (demos): decrement count only
        cost = generic + sum(pips.values())
        cur = next((m for (p, m) in state.get("mana_available", set()) if p == ap), 0)
        state["mana_available"] = {(p, m) for (p, m) in state.get("mana_available", set()) if p != ap} | {(ap, max(0, cur - cost))}
        return
    used: set[str] = set()
    used_rows: list = []
    rem_pips, rem_generic = dict(pips), generic           # cost the SOURCES must cover (after floating) — for excess
    need_pips = dict(pips)
    need_generic = generic
    # tap sources to cover the cost. A source contributes ALL its mana when tapped; we apply that mana to
    # an unmet colored pip first (matching the source's color, prefer a concrete-color source over a
    # wildcard for that pip), then to generic. Greedy but faithful: tap only sources that help.
    def apply(units, cg):
        nonlocal need_generic
        slots, bundles, total = _slots_bundles(units)
        avail = total - cg                                    # net mana after the source's activation cost
        for colset in slots:                                  # each single slot pays one matching pip
            if avail <= 0:
                break
            hit = next((c for c in need_pips if need_pips[c] > 0 and c in colset), None)
            if hit:
                need_pips[hit] -= 1; avail -= 1
        for colset, n in bundles:                             # a bundle pays up to n pips of ONE chosen color
            if avail <= 0:
                break
            color = max((c for c in need_pips if need_pips[c] > 0 and c in colset),
                        key=lambda c: need_pips[c], default=None)
            give = min(n, avail)
            if color is not None:
                paid = min(need_pips[color], give)
                need_pips[color] -= paid; avail -= paid       # leftover of the bundle falls through to generic
        # leftover mana pays generic
        take = min(avail, need_generic)
        need_generic -= max(0, take)

    def helps(units, cg):
        slots, bundles, total = _slots_bundles(units)
        avail = total - cg
        if avail <= 0:
            return False
        if need_generic > 0:
            return True
        for colset in slots + [b[0] for b in bundles]:
            if any(need_pips.get(c, 0) > 0 for c in colset):
                return True
        return False

    # order: concrete single-color sources first (preserve flexible wildcards/bundles for pips), then flex.
    def keyf(row):
        _sid, units, _cg, _ts = row
        flexcount = sum(1 for u in units if isinstance(u, (frozenset, tuple)))
        return (flexcount, sum(u[2] if isinstance(u, tuple) and u and u[0] == "one" else 1 for u in units))
    for sid, units, cg, _ts in sorted(rows, key=keyf):
        if not (need_pips and any(v > 0 for v in need_pips.values())) and need_generic <= 0:
            break
        if helps(units, cg):
            apply(units, cg)
            used.add(sid)
            used_rows.append((sid, units, cg, _ts))
    sacrifices = state.get("source_sacrifice", set())
    for sid in used:
        if (sid,) in sacrifices:                              # §605 one-shot fast mana: sacrificed, not tapped
            _sacrifice_source(state, sid)
        else:
            state.setdefault("tapped", set()).add((sid,))
    # §106.4 a tapped source yields ALL its mana at once; mana beyond the cost FLOATS (Sol Ring -> {C}{C} for
    # a {C} cost leaves {C} floating; Black Lotus -> 3 blue for {U}{U} leaves 1 blue). Production minus the
    # cost the sources covered is the excess.
    if used_rows:
        prod = _production(used_rows, rem_pips)
        for col, need in rem_pips.items():
            prod[col] = prod.get(col, 0) - need
        g = rem_generic
        for col in ["colorless"] + sorted(c for c in prod if c != "colorless"):
            if g <= 0:
                break
            take = min(g, max(0, prod.get(col, 0)))
            prod[col] = prod.get(col, 0) - take; g -= take
        _add_floating(state, ap, {c: n for c, n in prod.items() if n > 0})
    _refresh_mana_pool(state, ap)                             # pool/count from sources still untapped + floating


# --- §405 THE STACK: push -> priority window -> resolve top -----------------------------------------
# `on_stack(obj, pos)` is the engine's stack (pos = depth, higher resolves first via stack_top). The
# driver tracks per-object bookkeeping the engine doesn't need — who controls it and how it resolves —
# in `_stack_info[obj] = controller`. Mana is paid by the mana model (_spend_mana); the engine still
# authors can_cast / resolves / fizzles / countered / enters_*.

def _stack_push(state: dict, obj: str, controller: str) -> None:
    """§601.2 / §405.1 — put a just-cast spell (or activated ability) on top of the stack."""
    positions = [p for (_o, p) in state.get("on_stack", set())]
    pos = (max(positions) + 1) if positions else 0
    state.setdefault("on_stack", set()).add((obj, pos))
    state.setdefault("_stack_info", {})[obj] = controller


def _stack_remove(state: dict, obj: str) -> None:
    state["on_stack"] = {(o, p) for (o, p) in state.get("on_stack", set()) if o != obj}
    state.get("_stack_info", {}).pop(obj, None)


def _spell_effects(state: dict, spell: str) -> list:
    # ONE WORLD: spell_effect is now an ENGINE relation — the player-scoped slice is DERIVED IN DATALOG
    # from the card parse facts (translate.dl), the rest is still bridge-fed (.input). souffle unions both,
    # so read it back from the engine rather than raw state (amount comes back as a string; _apply_effects
    # does int(amt) either way).
    return sorted(r for r in run(state, ["spell_effect"])["spell_effect"] if r[0] == spell)


def _choose_mode(state: dict, spell: str) -> None:
    """§601.2b — as a modal spell is cast, its controller chooses the mode(s). Greedy/deterministic: pick
    the first offered mode and record chose_mode so the engine derives active_mode(spell, mode); only that
    mode's effects resolve. (The bridge offers a mode only if its effects are resolvable.)"""
    modes = sorted(m for (s, m) in state.get("spell_mode", set()) if s == spell)
    if modes:
        mode = _choose(state, "mode", modes, modes[0])        # §601.2b — the mode choice (referee seam)
        state.setdefault("chose_mode", set()).add((spell, mode))
        print(f"      {spell}: chooses mode {mode}")


def _fire_cast_triggers(state: dict, caster: str, spell: str) -> None:
    """§601.2i — 'whenever you cast a spell' triggers fire as the spell goes on the stack. Open the cast
    window (cast_spell) so the engine fires the matching cast-triggers, then apply only the NEW pending
    the cast produced (diff vs. the pre-cast pending) so unrelated triggers aren't double-applied. The
    cast window stays set across _apply_effects so cast-triggered creature effects fire too.

    The engine already derives the cast-trigger family from cast_spell (you_cast / you_cast_noncreature /
    you_cast_instant_or_sorcery / opponent_cast / any_cast — see build_engine.py), so a card whose trigger
    is one of those fires here automatically (this is what carries PROWESS-style 'whenever you cast …'
    abilities once a card supplies the trigger)."""
    before = run(state, ["pending"])["pending"]
    state["cast_spell"] = {(caster, spell)}
    new = run(state, ["pending"])["pending"] - before
    _apply_effects(state, new)
    state["cast_spell"] = set()


# --- §608 CAST COUNTER + §707.10 SPELL COPYING ------------------------------------------------------
# Two pieces of infrastructure shared by the spell-count / spell-copy mechanics (storm, gravestorm, the
# 'copy target spell' family, replicate). The cast counter is per-turn shim state the engine doesn't need
# (storm's copy is a shim action, not a datalog derivation); the copier reuses the engine's instance_of
# re-derivation so a copy's effects/targets fall out for free.
def _note_cast(state: dict) -> int:
    """Record that a spell was just cast THIS TURN (§608), returning how many were cast BEFORE it (the
    storm count). Reset to 0 at each turn boundary (play_game / env). Counts spells by ANY player —
    §702.40a 'each spell cast before it this turn' is not controller-restricted."""
    prior = state.get("_cast_count", 0)
    state["_cast_count"] = prior + 1
    return prior


def _spell_keywords(state: dict, spell: str) -> set:
    """The keywords on a spell OBJECT, read from its card identity (card_keyword via instance_of). Used for
    STACK keywords (storm, cascade, replicate) that the engine doesn't surface as printed_keyword — that
    relation is gated to the static creature-keyword roster (engine_keyword), not stack abilities."""
    slug = next((s for (o, s) in state.get("instance_of", set()) if o == spell), None)
    if slug is None:
        return set()
    return {kw for (c, kw) in state.get("card_keyword", set()) if c == slug}


def _copy_spell(state: dict, spell: str, controller: str, n: int = 1) -> list:
    """§707.10 — put `n` copies of `spell` onto the stack under `controller`'s control. A copy has the same
    characteristics, so we give it a fresh id pointing at the SAME card (instance_of) plus the spell's type
    and a controller; the engine then RE-DERIVES the copy's spell_effect / spell_damage / spell_target from
    instance_of (translate.dl) — no per-effect plumbing, and a targeted copy picks NEW targets on
    resolution (the driver chooses per spell-object). The copy is flagged in `_is_copy` so it ceases to
    exist instead of going to a graveyard (§707.10a, §608.2m). Returns the new copy ids."""
    slug = next((s for (o, s) in state.get("instance_of", set()) if o == spell), None)
    types = {t for (s, t) in state.get("spell_type", set()) if s == spell}
    made = []
    for _ in range(n):
        state["_copy_seq"] = state.get("_copy_seq", 0) + 1
        cp = f"{spell}__copy{state['_copy_seq']}"
        if slug is not None:
            state.setdefault("instance_of", set()).add((cp, slug))
        for t in types:
            state.setdefault("spell_type", set()).add((cp, t))
        state.setdefault("printed_control", set()).add((controller, cp))
        state.setdefault("_is_copy", set()).add((cp,))
        _stack_push(state, cp, controller)
        made.append(cp)
    return made


def _discard_copy(state: dict, cp: str) -> None:
    """§707.10a — a resolved (or countered) spell COPY ceases to exist: drop the derivation handles we gave
    it so it leaves no trace in the engine facts (and the stack/graveyard never holds a phantom id)."""
    state["instance_of"] = {(o, s) for (o, s) in state.get("instance_of", set()) if o != cp}
    state["spell_type"] = {(o, t) for (o, t) in state.get("spell_type", set()) if o != cp}
    state["printed_control"] = {(p, o) for (p, o) in state.get("printed_control", set()) if o != cp}
    state.get("_is_copy", set()).discard((cp,))


def _storm(state: dict, spell: str, controller: str, prior: int) -> None:
    """§702.40 STORM — 'When you cast this spell, copy it for each spell cast before it this turn.' `prior`
    is that count (from _note_cast). Faithful to the copy mechanic; the storm trigger itself is modeled as
    resolving immediately (the copies are created as the spell is cast), which the greedy engine never
    races. The copies don't re-trigger storm (they aren't cast) and aren't counted."""
    if prior <= 0 or "storm" not in _spell_keywords(state, spell):
        return
    _copy_spell(state, spell, controller, prior)
    print(f"      storm: {spell} is copied {prior} time(s) (spells cast before it this turn: {prior})")


def _run_spell_effects(state: dict, spell: str, ctrl: str) -> None:
    """§608.2c — a resolving instant/sorcery runs its effects, then goes to the graveyard. `counter`
    removes its target from the stack (the engine's `countered` event then lets any 'when countered'
    trigger fire); the rest are applied via _apply_effects (the shared effect resolver)."""
    if any(s == spell for (s, _m) in state.get("spell_mode", set())):   # §700.2 modal: only the CHOSEN mode resolves
        active = {m for (s, m) in run(state, ["active_mode"])["active_mode"] if s == spell}
        effs = sorted((spell, eff, amt, tgt) for (s, m, eff, amt, tgt) in state.get("spell_effect_mode", set())
                      if s == spell and m in active)
    else:
        effs = _spell_effects(state, spell)
    for (_s, eff, amt, tgt) in effs:
        if eff == "counter":                                 # §701.5 — counter the spell below it on the stack
            victim = _counter_target(state, spell)
            if victim is not None:
                print(f"      {spell} counters {victim}")
                state["countered"] = {(victim,)}             # §603.10e look-back event for 'when countered'
                _apply_effects(state, run(state, ["pending"])["pending"])
                state["countered"] = set()
                _stack_remove(state, victim)
                _to_graveyard(state, victim)
            else:
                print(f"      {spell} has no spell to counter")
        elif eff == "ctarget":                               # §601.2c a CHOSEN mode's single-target zone move
            verb, payload, cls = str(tgt).split("|")          # (Prismari Charm bounce, Get Out self-bounce)
            _resolve_one_target(state, spell, "spell", ctrl, verb, payload, cls)
        elif eff == "cdamage":                               # §120 a chosen mode's direct damage to a target
            _apply_damage(state, spell, int(amt), str(tgt), ctrl)
        else:                                                # shared effect resolver (§603 -> §608 vocabulary)
            _apply_effects(state, {(f"{spell}", eff, amt, tgt, spell, ctrl)})
    _run_spell_targets(state, spell, ctrl)                    # §115 single-target creature effects (Murder, ...)
    _run_spell_scope(state, spell, ctrl)                      # board-scope creature effects (Overrun, Wrath, ...)
    _run_spell_damage(state, spell, ctrl)                     # §120 direct damage (Lightning Bolt, Shock, ...)
    _run_spell_reanimate(state, spell, ctrl)                  # §701 reanimation (Resurrection, Zombify, ...)


def _run_spell_reanimate(state: dict, spell: str, ctrl: str) -> None:
    """§701 reanimation: move the best creature card in a graveyard to the battlefield under the caster's
    control (summoning-sick; tapped if the clause said so). The card isn't on the battlefield, so its type
    is read from printed_type, not the engine's `creature` (which requires a battlefield permanent)."""
    # ONE WORLD: spell_reanimate is now an ENGINE relation — DERIVED IN DATALOG from the card parse facts
    # (translate.dl), the rest still bridge-fed (.input). souffle unions both; read it back from the engine.
    for (_s, mode) in sorted(r for r in run(state, ["spell_reanimate"])["spell_reanimate"] if r[0] == spell):
        _reanimate_one(state, spell, ctrl, mode)


def _reanimate_one(state: dict, label: str, ctrl: str, mode: str) -> None:
    """§701 put the strongest creature card from a zone onto the battlefield under `ctrl` (summoning-sick).
    `mode` encodes the source ZONE and tappedness: 'graveyard'/'hand', optionally '_tapped'. Reanimation
    pulls from the graveyard; a from-hand cheat (Sneak Attack, Elvish Piper) pulls from the caster's hand.
    The card isn't a battlefield permanent yet, so its type/power are read from printed_*."""
    zone = "hand" if str(mode).startswith("hand") else "graveyard"
    tapped = str(mode).endswith("tapped")
    ptype = state.get("printed_type", set())
    ppow = {c: int(n) for (c, n) in state.get("printed_power", set())}
    if zone == "hand":
        cards = [c for (p, c) in state.get("in_hand", set()) if p == ctrl]
    else:
        cards = [c for (c,) in state.get("graveyard", set())]
    targets = sorted((c for c in cards if (c, "creature") in ptype), key=lambda c: ppow.get(c, 0), reverse=True)
    if not targets:
        print(f"      {label} finds no creature card to put onto the battlefield")
        return
    c = targets[0]
    if zone == "hand":
        state["in_hand"].discard((ctrl, c))
    else:
        state["graveyard"].discard((c,))
    state.setdefault("on_battlefield", set()).add((c,))
    state.setdefault("printed_control", set())                # §701 under the caster's control
    state["printed_control"] = {(p, x) for (p, x) in state["printed_control"] if x != c} | {(ctrl, c)}
    state.setdefault("_sick", set()).add((c,))                # §302.6 summoning sickness
    if tapped:
        state.setdefault("tapped", set()).add((c,))
    via = "puts into play from hand" if zone == "hand" else "reanimates"
    print(f"      {label} {via} {c} -> {ctrl}'s battlefield{' (tapped)' if tapped else ''}")


def _run_spell_targets(state: dict, spell: str, ctrl: str) -> None:
    """§608.2c + §601.2c — a resolving instant/sorcery's single 'target creature' effects: the engine
    surfaced the legal-target class (spell_target), the driver picks the target (removal/tap/bounce ->
    strongest enemy, buff/grant -> strongest own) and applies it. Same machinery as triggered targets."""
    # ONE WORLD: spell_target is now an ENGINE relation — DERIVED IN DATALOG from the card parse facts
    # (translate.dl), the rest still bridge-fed (.input). souffle unions both; read it back from the engine.
    rows = sorted(r for r in run(state, ["spell_target"])["spell_target"] if r[0] == spell)
    for (_s, verb, payload, cls) in rows:
        _resolve_one_target(state, spell, "spell", ctrl, verb, payload, cls)


def _resolve_one_target(state: dict, label: str, kind: str, ctrl: str, verb: str, payload: str, cls: str) -> None:
    """§601.2c pick a legal target of `cls` and apply one creature verb — shared by spell resolution and
    activated-ability resolution. Re-reads the board each call so the choice reflects current state."""
    out = run(state, ["controls", "power", "creature", "cant_be_destroyed"])
    indestructible = {c for (c,) in out["cant_be_destroyed"]}
    controls = {(p, c) for (p, c) in out["controls"]}
    powers = {c: int(n) for (c, n) in out["power"]}
    creatures = {c for (c,) in out["creature"]}
    owner_of = {c: p for (p, c) in controls}
    tgt = _pick_target(state, ctrl, cls, verb, payload, controls, powers, creatures)
    if tgt is not None:
        _apply_target_verb(state, label, kind, verb, payload, tgt, ctrl, indestructible, owner_of)


def _run_spell_damage(state: dict, spell: str, ctrl: str) -> None:
    """§120 direct damage from a resolving burn instant/sorcery. The driver picks the target the engine
    can't: a creature target -> mark the damage and apply lethality (n >= final toughness, unless
    indestructible, kills it via the §704 destroy path); a player target -> life loss; 'any target' ->
    kill a creature if the damage is lethal to a real threat, else go face. (Non-lethal marked damage
    isn't persisted outside combat — a known simplification; the game-relevant outcome is lethality.)"""
    # ONE WORLD: spell_damage is now an ENGINE relation — DERIVED IN DATALOG from the card parse facts
    # (translate.dl), the rest still bridge-fed (.input). souffle unions both; read it back from the engine
    # (the amount comes back a string through souffle, so int(n) before the lethality arithmetic).
    rows = sorted(r for r in run(state, ["spell_damage"])["spell_damage"] if r[0] == spell)
    for (_s, n, kind) in rows:
        _apply_damage(state, spell, int(n), kind, ctrl)


def _apply_damage(state: dict, label: str, n: int, kind: str, ctrl: str) -> None:
    """§120 resolve one direct-damage effect whose target the engine can't choose. A creature target ->
    lethality (n >= final toughness, unless indestructible, destroys it); a player -> life loss; 'any
    target' -> kill a finishable threat, else go face. Shared by burn spells (label=spell) and triggered
    damage (label=ability). (Non-lethal marked damage isn't persisted outside combat — a simplification.)"""
    out = run(state, ["controls", "creature", "power", "eff_toughness", "cant_be_destroyed"])
    indestructible = {c for (c,) in out["cant_be_destroyed"]}
    controls = {(p, c) for (p, c) in out["controls"]}
    powers = {c: int(x) for (c, x) in out["power"]}
    tough = {c: int(x) for (c, x) in out["eff_toughness"]}
    creatures = {c for (c,) in out["creature"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in controls if p == ctrl}
    enemy = sorted(c for c in creatures if c in on_bf and c not in mine)
    opp = _others(state, ctrl)[0] if _others(state, ctrl) else None

    def kill(c):                                              # mark lethal damage -> §704.5g destroy
        if c in indestructible:
            print(f"      {label} deals damage to {c} but it can't be destroyed (indestructible)")
            return
        state["on_battlefield"].discard((c,))
        state.setdefault("graveyard", set()).add((c,))
        print(f"      {label} deals lethal damage to {c} -> graveyard")

    def best_killable():                                      # strongest enemy whose toughness n can finish
        killable = [c for c in enemy if c not in indestructible and tough.get(c, 1) <= n]
        return max(killable, key=lambda c: powers.get(c, 0)) if killable else None

    if kind == "self":
        print(f"      {label} deals {n} to {ctrl} -> {_adjust_life(state, ctrl, -n)} life")
    elif kind == "face":
        if opp is not None:
            print(f"      {label} deals {n} to {opp} -> {_adjust_life(state, opp, -n)} life")
    elif kind in ("creature_any", "creature_opponent"):
        tgt = best_killable() or (max(enemy, key=lambda c: powers.get(c, 0)) if enemy else None)
        if tgt is None:
            print(f"      {label} has no creature to damage")
        elif tough.get(tgt, 1) <= n:
            kill(tgt)
        else:
            print(f"      {label} deals {n} to {tgt} (non-lethal)")
    elif kind == "any_target":                                # kill a real threat if we can, else go face
        tgt = best_killable()
        if tgt is not None:
            kill(tgt)
        elif opp is not None:
            print(f"      {label} deals {n} to {opp} -> {_adjust_life(state, opp, -n)} life")
    elif kind.startswith(("all_creatures", "all_ground", "all_flyers")):   # §120 a board sweeper (Pyroclasm,
        flyers = {c for (c, k) in run(state, ["has_keyword"])["has_keyword"] if k == "flying"}  # Earthquake, Hurricane)
        def hit(c):                                            # Earthquake spares flyers; Hurricane hits only them
            if kind.startswith("all_ground"):
                return c not in flyers
            if kind.startswith("all_flyers"):
                return c in flyers
            return True
        for c in sorted(c for c in creatures if c in on_bf and hit(c)):
            if tough.get(c, 1) <= n:
                kill(c)
        if kind.endswith("_and_players"):
            for p in sorted(q for (q,) in state.get("is_player", set())):
                print(f"      {label} deals {n} to {p} -> {_adjust_life(state, p, -n)} life")


def _run_spell_scope(state: dict, spell: str, ctrl: str) -> None:
    """§608 board-scope creature effects on a resolving spell (Overrun: creatures you control get +X/+X;
    Wrath of God: destroy all creatures). The driver expands the scope to concrete creatures — its own
    (creatures_you_control) or every creature (all_creatures) — and applies the verb to each."""
    # ONE WORLD: spell_scope is now an ENGINE relation — DERIVED IN DATALOG from the card parse facts
    # (translate.dl), the rest still bridge-fed (.input). souffle unions both; read it back from the engine.
    rows = sorted(r for r in run(state, ["spell_scope"])["spell_scope"] if r[0] == spell)
    if not rows:
        return
    out = run(state, ["controls", "creature", "cant_be_destroyed"])
    indestructible = {c for (c,) in out["cant_be_destroyed"]}
    controls = {(p, c) for (p, c) in out["controls"]}
    creatures = {c for (c,) in out["creature"]}
    owner_of = {c: p for (p, c) in controls}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in controls if p == ctrl}
    for (_s, verb, payload, scope) in rows:
        targets = sorted(c for c in creatures if c in on_bf
                         and (scope == "all_creatures" or c in mine))
        for tgt in targets:
            _apply_target_verb(state, spell, "spell", verb, payload, tgt, ctrl, indestructible, owner_of)


def _static_attached_pt(state: dict, perm: str) -> int:
    """The net P/T swing a permanent's 'attached creature' static buff carries (sum of dp+dt), or 0 if it
    only grants a keyword. Used to pick a friendly vs. enemy host for an Aura."""
    return sum(int(dp) + int(dt) for (s, dp, dt, sc) in state.get("static_pt", set())
              if s == perm and sc == "attached")


def _has_attached_static(state: dict, perm: str) -> bool:
    return any(s == perm and sc == "attached" for (s, _dp, _dt, sc) in state.get("static_pt", set())) \
        or any(s == perm and sc == "attached" for (s, _kw, sc) in state.get("static_grant", set()))


def _attach_aura(state: dict, aura: str, ctrl: str) -> None:
    """§303.4 an Aura enters the battlefield attached to a creature. We attach Auras that carry a P/T or
    keyword 'enchanted creature' static buff (applied via attached_to) or that STEAL control (Control Magic,
    via eff_gain_control): a beneficial buff goes on the controller's strongest creature; a negative buff or
    a control-steal goes on the opponent's strongest. Auras with no legal host stay unattached (no effect)."""
    is_control = (aura,) in state.get("aura_control", set())
    if (aura, "aura") not in state.get("printed_subtype", set()):
        return
    if not _has_attached_static(state, aura) and not is_control:
        return
    out = run(state, ["controls", "creature", "power"])
    controls = {(p, c) for (p, c) in out["controls"]}
    creatures = {c for (c,) in out["creature"]}
    powers = {c: int(n) for (c, n) in out["power"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in controls if p == ctrl}
    harmful = is_control or _static_attached_pt(state, aura) < 0   # a control-steal targets an enemy
    cands = [c for c in creatures if c in on_bf and c != aura and ((c not in mine) if harmful else (c in mine))]
    if not cands:                                            # no legal host of the wanted side -> any creature
        cands = [c for c in creatures if c in on_bf and c != aura]
    if not cands:
        return
    host = max(cands, key=lambda c: powers.get(c, 0))
    state.setdefault("attached_to", set()).add((aura, host))
    print(f"      {aura} is attached to {host}")
    if is_control:                                           # §613 layer 2 — the Aura's controller takes control
        state.setdefault("eff_gain_control", set()).add((f"{aura}__ctrl", ctrl, host, 1))
        print(f"      {aura}: {ctrl} gains control of {host}")


def _equip(state: dict, equipment: str, ctrl: str) -> None:
    """§301.5 attach (or move) an Equipment to the controller's strongest creature — its 'equipped creature'
    static buff then applies via attached_to. Equip buffs are beneficial, so it always goes on an own
    creature; an existing attachment is moved (§701.3)."""
    out = run(state, ["controls", "creature", "power"])
    powers = {c: int(n) for (c, n) in out["power"]}
    creatures = {c for (c,) in out["creature"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = [c for (p, c) in out["controls"] if p == ctrl and c in creatures and c in on_bf]
    if not mine:
        return
    host = max(mine, key=lambda c: powers.get(c, 0))
    state["attached_to"] = {(a, c) for (a, c) in state.get("attached_to", set()) if a != equipment} | {(equipment, host)}
    print(f"      {equipment} is equipped to {host}")


def _counter_target(state: dict, counterspell: str) -> str | None:
    """The spell a counterspell counters: the topmost OTHER object on the stack (the one it was cast
    in response to). With a one-deep response window that's the spell directly below it."""
    below = sorted(((p, o) for (o, p) in state.get("on_stack", set()) if o != counterspell), reverse=True)
    return below[0][1] if below else None


def _to_graveyard(state: dict, obj: str) -> None:
    """§608.2m / §405.5 — a resolved or countered spell that isn't a permanent goes to the graveyard."""
    state.setdefault("graveyard", set()).add((obj,))


def _resolve_top(state: dict) -> None:
    """§608 — resolve the top object of the stack once all players have passed. A spell that resolves
    enters the battlefield (permanent, applying §614 ETB replacements) or runs its effects then hits the
    graveyard (instant/sorcery); a fizzled/countered spell leaves with no effect. The engine derives
    resolves / fizzles / enters_* — the driver just moves the object and applies what's derived."""
    state["all_passed"] = {("yes",)}
    out = run(state, ["stack_top", "resolves", "fizzles", "enters_battlefield",
                      "enters_tapped", "enters_with_counter"])
    state["all_passed"] = set()
    top = next((o for (o,) in out["stack_top"]), None)
    if top is None:                                          # nothing the engine recognizes on the stack
        state["on_stack"] = set(); state["_stack_info"] = {}  # clear so the priority loop can't hang
        return
    ctrl = state.get("_stack_info", {}).get(top, next(iter(state["active_player"]))[0])
    _stack_remove(state, top)
    if top in state.get("_ability_effect", {}):              # §602 a resolving activated ability (not a spell)
        eff, amt, tgt, src, actrl = state["_ability_effect"].pop(top)
        print(f"    {top} resolves (activated ability)")
        if eff == "ctarget":                                 # §115 single-target creature verb -> driver picks
            verb, payload, cls = tgt.split("|")
            _resolve_one_target(state, top, "ability", actrl, verb, payload, cls)
        elif eff == "cdamage":                               # §120 direct damage -> driver picks the target
            _apply_damage(state, top, amt, tgt, actrl)
        elif eff == "equip":                                 # §301.5 attach the Equipment to a creature
            _equip(state, src, actrl)
        elif eff == "reanimate":                             # §701 activated reanimator / from-hand cheat
            _reanimate_one(state, top, actrl, tgt)
        elif eff == "level_up":                              # §717 raise a Class's level, fire its 'becomes level N'
            lvl = state.setdefault("_class_level", {})
            lvl[src] = int(amt)
            print(f"    {src} becomes level {amt}")
            for (cid, n2, e2, a2, t2) in sorted(state.get("class_level_effect", set())):
                if cid == src and int(n2) == int(amt):       # the level-N ability's effect resolves now
                    _apply_effects(state, {(f"{src}_lvl{amt}", e2, int(a2), t2, src, actrl)})
        else:
            _apply_effects(state, {(top, eff, amt, tgt, src, actrl)})
        return
    if (top,) in out["fizzles"]:
        if (top,) in state.get("_is_copy", set()):           # §707.10a a fizzled COPY just ceases to exist
            print(f"    {top} (copy) fizzles (no legal target) -> ceases to exist")
            _discard_copy(state, top)
            return
        print(f"    {top} fizzles (no legal target) -> graveyard")
        _to_graveyard(state, top)
        return
    if (top,) in out["enters_battlefield"]:                  # a permanent spell becomes a permanent
        print(f"    {top} resolves -> battlefield")
        state["on_battlefield"].add((top,))
        state.setdefault("printed_control", set()).add((ctrl, top))
        state.setdefault("_sick", set()).add((top,))         # §302.6 summoning sickness until controller's next turn
        _attach_aura(state, top, ctrl)                        # §303.4 an Aura enters attached to a creature
        if (top,) in out["enters_tapped"]:
            state.setdefault("tapped", set()).add((top,)); print(f"      {top} enters tapped")
        for (c, k, n) in sorted(out["enters_with_counter"]):
            if c == top:
                _bump_counter(state, top, k, int(n)); print(f"      {top} enters with {n} {k} counter")
        # §603.2a — the permanent's own enters ability triggers AS it enters. Re-assert it as the resolving
        # object (ev_etb only holds while resolving) with the permanent now on the battlefield so
        # 'creatures you control' scopes include it; _apply_effects applies both player- and creature-scoped
        # ETB pendings, then it leaves the stack for good.
        maxd = max([d for (_o, d) in state.get("on_stack", set())], default=-1)
        state.setdefault("on_stack", set()).add((top, maxd + 1))
        state["all_passed"] = {("yes",)}
        _apply_effects(state, run(state, ["pending"])["pending"])
        state["all_passed"] = set()
        _stack_remove(state, top)
        return
    print(f"    {top} resolves")                             # an instant/sorcery: run effects, then graveyard
    _run_spell_effects(state, top, ctrl)
    if (top,) in state.get("_is_copy", set()):               # §707.10a a resolved COPY ceases to exist (no graveyard)
        _discard_copy(state, top)
    else:
        _to_graveyard(state, top)


def _cast_instant_response(state: dict, p: str) -> bool:
    """§405.2 RESPONSE WINDOW — player `p` (with priority) may cast ONE instant from hand onto the stack
    in response to whatever is on top. Greedy: casts the first castable instant (this is how a held
    counterspell answers a spell on the stack). Returns True if it cast something (priority resets)."""
    state["has_priority"] = {(p,)}
    castable = sorted(s for (q, s) in run(state, ["can_cast"])["can_cast"]
                      if q == p and (s, "instant") in state.get("spell_type", set()))
    if not castable:
        return False
    spell = castable[0]
    _spend_mana(state, p, spell)                             # mana model owns payment
    state["in_hand"].discard((p, spell))
    _stack_push(state, spell, p)
    _choose_mode(state, spell)                               # §601.2b — modal instant chooses its mode
    prior = _note_cast(state)                                # §608 a response-cast counts toward storm too
    _fire_cast_triggers(state, p, spell)                     # §601.2i — cast triggers
    print(f"    {p} responds: casts {spell} (onto the stack)")
    _storm(state, spell, p, prior)                           # §702.40 storm on a response-cast instant
    return True


def _resolve_stack(state: dict, ap: str, players: list) -> None:
    """§117.4 / §405.5 — run the priority loop until the stack empties: after each push, every non-active
    player gets a response window; when all pass, the top resolves. Repeat until the stack is empty."""
    while state.get("on_stack"):
        responded = False
        for p in players:                                    # §405.2 non-active players may respond first
            if p != ap and _cast_instant_response(state, p):
                responded = True
                break
        if responded:
            continue                                         # a response was added; re-open priority on the new top
        _resolve_top(state)                                  # all passed -> resolve the top object
    state["has_priority"] = set()


def _cast_phase(state: dict, ap: str) -> None:
    """§601 -> §608 (sorcery-speed): the active player casts each spell it can, ONE at a time, each onto
    the real stack. After every cast a RESPONSE WINDOW opens (non-active players may cast an instant — a
    held counterspell answers here); the stack then resolves top-down. The engine derives
    can_cast / resolves / fizzles / countered / enters_* — the driver pushes, runs priority, and applies
    what's derived. Mana is paid by the mana model (_develop_mana / _spend_mana); effects via _apply_effects."""
    _develop_mana(state, ap)                                 # §305 land drop + refresh mana from lands
    players = sorted(q for (q,) in state["is_player"])
    # §903.6 — greedy: cast the commander from the command zone when affordable (default-on, like spells)
    for cmd in can_cast_commander(state, ap):
        if _choose(state, "cast_commander", (cmd, None), cmd):
            cast_commander(state, ap, cmd, players)
            break
    while True:
        state["has_priority"] = {(ap,)}                      # §601 active player has priority in its main phase
        castable = sorted(s for (p, s) in run(state, ["can_cast"])["can_cast"] if p == ap)
        if not castable:
            break
        _cast_spell(state, ap, castable[0], players)         # greedy: cast the first castable spell
    state["has_priority"] = set()
    _activate_phase(state, ap, players)                      # §602 — then use a non-mana activated ability if able


def _cast_spell(state: dict, ap: str, spell: str, players: list) -> None:
    """§601 -> §608 cast ONE spell sorcery-speed onto the real stack and resolve it (mode + cast triggers +
    response window + top-down resolution). The single-spell core of _cast_phase — reused by the env/search
    so an external policy can cast a CHOSEN spell (with forced mode/target via the _choose seam)."""
    _spend_mana(state, ap, spell)                            # §601.2g — consume the mana so casts are limited
    state["in_hand"].discard((ap, spell))
    _stack_push(state, spell, ap)
    _choose_mode(state, spell)                               # §601.2b — choose mode(s) if it's a modal spell
    prior = _note_cast(state)                                # §608 count this spell; `prior` = storm count
    _fire_cast_triggers(state, ap, spell)                    # §601.2i — 'whenever you cast a spell' triggers
    print(f"    {ap} casts {spell}")
    _storm(state, spell, ap, prior)                          # §702.40 storm — copy it once per earlier spell
    _resolve_stack(state, ap, players)                       # response window + top-down resolution


# --- §903 COMMANDER (the command zone, the recast tax, and the §903.9 / §704.5 replacement) -----------
# The DATALOG ENGINE owns the rules (starting_life("commander", 40) in starting.dl; cast_permission /
# resolves_to over the spell's TYPE; can_afford over the mana model). The SHIM owns the STATE the engine
# doesn't carry across casts: which object is whose commander, the command-zone membership, and the
# per-commander recast count that becomes the §903.8 "{2} for each previous time" tax. The commander is
# made a normal castable spell (spell_type + mana_pip/_generic, like any hand card); only the SOURCE ZONE
# (command_zone, not in_hand) and the tax differ — so this reuses _cast_spell / the resolve path wholesale.

def _commanders_of(state: dict, p: str) -> list[str]:
    """The commanders player p owns that are CURRENTLY in the command zone (castable from there)."""
    return sorted(c for (pp, c) in state.get("command_zone", set()) if pp == p)


def _commander_tax(state: dict, cmd: str) -> int:
    """§903.8 — {2} generic for EACH previous time this commander was cast from the command zone."""
    return 2 * state.get("_cmd_casts", {}).get(cmd, 0)


def _apply_commander_tax(state: dict, cmd: str, announce: bool = False) -> dict:
    """Fold the §903.8 recast tax into the commander's mana cost at the COST LEVEL (so the existing mana
    model pays it untouched): bump mana_generic and mana_cost by {2}×prior-casts. Returns a snapshot of
    the rows changed so _restore_commander_tax can put the printed cost back after the cast. `announce`
    prints the surcharge (only the real cast does — the affordability PROBE stays quiet)."""
    tax = _commander_tax(state, cmd)
    gen_rows = {(s, n) for (s, n) in state.get("mana_generic", set()) if s == cmd}
    cost_rows = {(s, n) for (s, n) in state.get("mana_cost", set()) if s == cmd}
    snap = {"mana_generic": gen_rows, "mana_cost": cost_rows}
    if tax:
        state["mana_generic"] = ({r for r in state.get("mana_generic", set()) if r[0] != cmd}
                                 | ({(cmd, int(n) + tax) for (_s, n) in gen_rows} or {(cmd, tax)}))
        state["mana_cost"] = ({r for r in state.get("mana_cost", set()) if r[0] != cmd}
                              | {(cmd, int(n) + tax) for (_s, n) in cost_rows})
        if announce:
            print(f"    (commander tax: +{{{tax}}} for {state['_cmd_casts'].get(cmd, 0)} prior cast(s))")
    return snap


def _restore_commander_tax(state: dict, cmd: str, snap: dict) -> None:
    """Put the commander's PRINTED cost back (the tax is only a casting surcharge, not a new printed cost)."""
    for rel, rows in snap.items():
        state[rel] = {r for r in state.get(rel, set()) if r[0] != cmd} | rows


def can_cast_commander(state: dict, p: str) -> list[str]:
    """§903.6 — the commanders p may cast from the command zone RIGHT NOW: in the command zone, sorcery
    speed (active player, a main phase, empty stack), and affordable INCLUDING the §903.8 tax. Mana
    legality is the engine's can_afford, checked against the tax-bumped cost via a probe state."""
    ap = next(iter(state["active_player"]))[0]
    if p != ap:
        return []
    step = next(iter(state["current_step"]))[0]
    if step not in ("precombat_main", "postcombat_main") or state.get("on_stack"):
        return []
    out = []
    for cmd in _commanders_of(state, p):
        probe = clone_state(state)
        snap = _apply_commander_tax(probe, cmd)               # tax-bumped cost on the probe
        probe["in_hand"] = set(probe.get("in_hand", set())) | {(p, cmd)}   # can_afford reads in_hand
        probe["has_priority"] = {(p,)}
        affordable = any(q == p and s == cmd for (q, s) in run(probe, ["can_cast"])["can_cast"])
        _restore_commander_tax(probe, cmd, snap)
        if affordable:
            out.append(cmd)
    return out


def cast_commander(state: dict, ap: str, cmd: str, players: list) -> None:
    """§903.6 — cast commander `cmd` from the command zone (sorcery speed) paying its cost + the §903.8
    {2}×prior-casts tax. Reuses _cast_spell wholesale: the only differences are the SOURCE ZONE (remove
    from command_zone, drop it into in_hand for the one cast so _cast_spell's in_hand.discard + the
    engine's can_cast see it) and the tax (folded into the cost, then restored). Records the cast so the
    NEXT cast-from-command-zone costs {2} more."""
    snap = _apply_commander_tax(state, cmd, announce=True)     # §903.8 surcharge at the cost level
    state.setdefault("command_zone", set()).discard((ap, cmd))
    state.setdefault("in_hand", set()).add((ap, cmd))          # the source-zone shim: cast it as if from hand
    print(f"    {ap} casts commander {cmd} from the command zone")
    _cast_spell(state, ap, cmd, players)                      # reuse the whole cast -> resolve -> ETB path
    state.setdefault("_cmd_casts", {})[cmd] = state.get("_cmd_casts", {}).get(cmd, 0) + 1
    _restore_commander_tax(state, cmd, snap)                   # the tax was a one-cast surcharge


def _commander_replacement(state: dict, cmd: str, to_zone: str) -> bool:
    """§903.9 / §704.5 — when a commander WOULD move to a graveyard or exile (or hand/library), its owner
    MAY instead put it into the command zone. A referee decision through the _choose seam (default = use
    the replacement, the strategically standard line). Returns True if it was redirected to the command
    zone. Each return increments the recast tax (§903.8) for the next cast."""
    owner = next((p for (p, c) in state.get("_commander_owner", set()) if c == cmd), None)
    if owner is None:
        return False
    use_cz = _choose(state, "commander_replacement", (True, False), True)
    if not use_cz:
        return False
    state.setdefault("command_zone", set()).add((owner, cmd))
    state.setdefault("_sick", set()).discard((cmd,))
    print(f"    {cmd} would move to {to_zone}; {owner} returns it to the command zone instead (§903.9)")
    return True


def _is_commander(state: dict, obj: str) -> bool:
    return any(c == obj for (_p, c) in state.get("_commander_owner", set()))


def _activatable(state: dict, p: str) -> list:
    """§602.5 — the activated abilities player p can pay for right now: source on the battlefield and
    controlled by p, its {T} part untappable (source untapped & not summoning-sick), enough mana for the
    mana part. Returns (ability_id, source, mana_cost, taps_self, eff, amount, target) rows."""
    bf = state.get("on_battlefield", set())
    ctrl = state.get("printed_control", set())
    tapped = state.get("tapped", set())
    sick = state.get("_sick", set())
    mana = next((m for (q, m) in state.get("mana_available", set()) if q == p), 0)
    out = []
    for row in state.get("activated_ability", set()):
        a, src, cost, taps, eff, amt, tgt = row
        if (src,) not in bf or (p, src) not in ctrl:
            continue
        if int(cost) > mana:
            continue
        if taps == "T" and ((src,) in tapped or (src,) in sick):
            continue                                         # can't pay {T}: already tapped or summoning sick
        if eff == "equip":                                   # §301.5 only worth equipping if currently
            if any(a2 == src for (a2, _c) in state.get("attached_to", set())):
                continue                                     # unattached (no re-equip churn) and ...
            if not any(pp == p and cc in {c for (c,) in run(state, ["creature"])["creature"]}
                       for (pp, cc) in run(state, ["controls"])["controls"]):
                continue                                     # ... the controller has a creature to hold it
        if eff == "reanimate":                               # §701 don't waste mana if the source zone has
            zone = "hand" if str(tgt).startswith("hand") else "graveyard"   # no creature card to put in play
            ptype = state.get("printed_type", set())
            cards = ([c for (pp, c) in state.get("in_hand", set()) if pp == p] if zone == "hand"
                     else [c for (c,) in state.get("graveyard", set())])
            if not any((c, "creature") in ptype for c in cards):
                continue
        if eff == "level_up":                                # §717 a Class advances ONE level at a time (N from N-1)
            if state.get("_class_level", {}).get(src, 1) != int(amt) - 1:
                continue
        out.append(row)
    return sorted(out)


def _activate_phase(state: dict, ap: str, players: list) -> None:
    """§602 — the active player activates ONE non-mana activated ability it can afford, pushing it onto
    the stack to resolve (with a response window) like a spell. Greedy single activation keeps the loop
    decisive; the ability's effect runs through the shared _apply_effects."""
    usable = _activatable(state, ap)
    if not usable:
        return
    # §602 the activation choice (referee seam): default greedy = activate the first affordable ability;
    # the option set includes None (decline) so a policy can choose not to activate.
    chosen = _choose(state, "activate", usable + [None], usable[0])
    if chosen is None:
        return
    a, src, cost, taps, eff, amt, tgt = chosen
    if int(cost):                                            # pay the mana part via the mana model
        _spend_ability_mana(state, ap, int(cost))
    if taps == "T":
        state.setdefault("tapped", set()).add((src,))        # §602.2 pay {T}
    state.setdefault("_ability_effect", {})[a] = (eff, int(amt), tgt, src, ap)
    _stack_push(state, a, ap)
    print(f"    {ap} activates {a} ({src}: {eff} {amt})")
    _resolve_stack(state, ap, players)


def _spend_ability_mana(state: dict, ap: str, cost: int) -> None:
    """Pay an activated ability's GENERIC mana cost by tapping untapped sources for their real mana —
    each source contributes its full net output (§106.4), so a Sol Ring pays a {2} cost with one tap.
    Same payment shape as _spend_mana (the mana model owns it); abilities deplete mana faithfully."""
    if not _controls_any_source(state, ap):                   # pre-seeded flat mana (demo): decrement the count
        cur = next((m for (q, m) in state.get("mana_available", set()) if q == ap), 0)
        state["mana_available"] = {(q, m) for (q, m) in state.get("mana_available", set()) if q != ap} | {(ap, max(0, cur - cost))}
        return
    paid = 0
    for sid, units, cg, _ts in _source_units(state, ap):
        if paid >= cost:
            break
        net = sum(1 for _u in units) - cg
        if net <= 0:
            continue
        state.setdefault("tapped", set()).add((sid,))
        paid += net
    _refresh_mana_pool(state, ap)                             # recompute pool/count from sources still untapped (0 if all tapped)


def _end_of_turn(state: dict) -> None:
    """§514.2 cleanup — until-end-of-turn continuous effects end (the driver removes them)."""
    ending = {e for (e,) in run(state, ["ends_at_cleanup"])["ends_at_cleanup"]}
    for e in ending:
        for rel in [k for k in state if k.startswith("eff_")]:
            state[rel] = {row for row in state[rel] if row and row[0] != e}
    if ending and state.get("until_eot"):                    # drop the consumed markers so they don't accrue
        state["until_eot"] = {row for row in state["until_eot"] if row and row[0] not in ending}
    # clear the once-per-firing guards so a RECURRING trigger (an every-upkeep reanimation/counter) fires
    # again next turn — they only prevent a re-derived trigger doubling within a single firing window.
    state["_reanimated"] = set()
    state["_counter_applied"] = set()
    state["prevent_all_combat"] = set()                      # §615 Fog lasts only 'this turn'


def play_game(state: dict, players: list[str], max_turns: int = 20) -> str | None:
    """The turn loop: cycle the rules-derived steps, run the engine, apply what it
    derives, pass the turn. Returns the loser (or None if the turn limit is hit)."""
    assert_known_keywords(state)                                 # reject keywords outside the interpreted §702 roster
    variant = "two-player" if len(players) == 2 else "default"   # §103.8 first-turn draw skip applies per variant
    for turn in range(max_turns):
        ap = next(iter(state["active_player"]))[0]
        skip_draw = turn == 0 and variant in DRAW_SKIP_VARIANTS   # the starting player skips their first draw
        while True:                                              # one step at a time, this turn
            step = next(iter(state["current_step"]))[0]
            if step == "declare_attackers":          # §508 turn-based action (before priority)
                declare_attackers(state, ap)
            elif step == "declare_blockers":         # §509 turn-based action (before priority)
                declare_blockers(state, ap)
            if step in GRANTS_PRIORITY:              # §5 priority window — the active player may cast
                _cast_phase(state, ap)
            if step == "cleanup":                    # §514.2 cleanup
                _end_of_turn(state)
            out = run(state, OUTPUTS)
            if skip_draw and step == "draw":         # §103.8a — the player who plays first skips it
                out["to_draw"] = set(); print(f"    {ap} skips their first-turn draw (§103.8a)")
            loser = _apply_outputs(state, out, ap)
            if loser:
                return loser
            if not out["advance_to"]:                            # past cleanup -> turn ends
                break
            state["current_step"] = out["advance_to"]            # advance to the engine's next step
            _empty_mana_pool(state)                              # §500.4 mana empties at end of each step/phase
        nxt_p = _next_active_player(state, ap, players)          # pass the turn (§500.6) — or take an extra one
        state["active_player"] = {(nxt_p,)}
        state["current_step"] = {("untap",)}
        _empty_mana_pool(state)                                  # §500.4 — pool empties across the turn boundary too
        state["attacks"], state["blocks"] = set(), set()        # combat declarations don't carry over
        state["_land_played"] = set()                           # §305.2 — a fresh land drop next turn
        state["_cast_count"] = 0                                 # §608/§702.40 storm count is per-turn
        ctrl = {c for (pp, c) in run(state, ["controls"])["controls"] if pp == nxt_p}
        state["_sick"] = {row for row in state.get("_sick", set()) if row[0] not in ctrl}  # §302.6 sickness wears off at turn start
        print(f"  --- {ap}'s turn ends; {nxt_p} becomes the active player ---")
    return None


def demo() -> None:
    # A self-playing game showing the cast -> resolve -> ETB flow AND two triggered abilities:
    #   precombat main: alice casts a "wolf" (2 mana). It resolves, enters the battlefield, and a
    #                   §614 replacement (repl_enters_with_counter) puts it in as a 3/3.
    #   upkeep:  alice's bear has "put a +1/+1 counter on ~"  -> grows 2/2 to 3/3 (add_counter)
    #   dies:    bear has "when ~ dies, each opponent loses 5" -> finishes bob off (lose_life)
    # Combat: bear (3/3) and wolf (3/3) attack; bob blocks bear with its ogre (3/3). Bear/ogre
    # trade, the wolf hits bob for 3, and the bear's death trigger then drops bob below zero.
    # (Summoning sickness isn't modelled in the engine yet, so the wolf can attack the turn it
    # enters; noted as a known simplification.) The driver moves the spell and applies each
    # consequence the engine derives — it authors none of the game logic itself.
    state = {
        "current_step": {("untap",)},
        "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",)},
        "life": {("alice", DEFAULT_LIFE), ("bob", 4)},           # §103.4 default starting life for alice
        "on_battlefield": {("bear",), ("ogre",)},                # base printed_* only — the engine derives
        "printed_type": {("bear", "creature"), ("ogre", "creature"), ("wolf", "creature")},  # has_type/controls/creature
        "printed_power": {("bear", 2), ("ogre", 3), ("wolf", 2)},
        "printed_toughness": {("bear", 2), ("ogre", 3), ("wolf", 2)},
        "printed_control": {("alice", "bear"), ("bob", "ogre")},
        "in_hand": {("alice", "wolf")},                          # a castable creature spell
        "spell_type": {("wolf", "creature")},
        "mana_cost": {("wolf", 2)},
        "mana_available": {("alice", 2)},
        "repl_enters_with_counter": {("welcome", "wolf", "p1p1", 1)},   # §614 — enters as a 3/3
        "has_trigger": {("grow", "bear", "upkeep"), ("rage", "bear", "dies_self")},
        "trigger_effect": {("grow", "add_counter", 1, "p1p1"), ("rage", "lose_life", 5, "each_opponent")},
        "counter": set(),
        "tapped": set(),
        "attacks": set(),
        "blocks": set(),
        "in_library": {("alice", f"a{i}") for i in range(6)} | {("bob", f"b{i}") for i in range(6)},
    }
    print("playing from alice's untap step (alice will cast a wolf; bear has upkeep-grow + death triggers):")
    loser = play_game(state, ["alice", "bob"])
    print(f"\nresult: {loser} lost")
    print("life:      ", sorted(state["life"]))
    print("battlefield:", sorted(c for (c,) in state["on_battlefield"]))
    print("graveyard:  ", sorted(c for (c,) in state.get("graveyard", set())))


def demo_sacrifice() -> None:
    # A focused look at the §603.10a look-back sacrifice event end-to-end through the shim:
    # alice's "altar" has "When ~ is sacrificed, each opponent loses 3." She sacrifices it; the
    # engine fires the trigger against the still-present permanent and the driver applies it.
    state = {
        "is_player": {("alice",), ("bob",)},
        "life": {("alice", 20), ("bob", 9)},
        "on_battlefield": {("altar",)},
        "printed_control": {("alice", "altar")},                 # engine derives controls(alice, altar)
        "has_trigger": {("rite", "altar", "sacrificed_self")},
        "trigger_effect": {("rite", "lose_life", 3, "each_opponent")},
    }
    print("\nsacrifice demo (altar: 'when sacrificed, each opponent loses 3'):")
    _sacrifice(state, "altar")
    print("life:      ", sorted(state["life"]))
    print("graveyard:  ", sorted(c for (c,) in state.get("graveyard", set())))


if __name__ == "__main__":
    demo()
    demo_sacrifice()
