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
DECLARED = set(re.findall(r"^\.decl (\w+)", RULES, re.M))

# zone name (from the transpiled §701 keyword-action rules) -> driver state relation.
ZONE = {"battlefield": "on_battlefield", "graveyard": "graveyard",
        "hand": "in_hand", "exile": "exile", "library": "library"}

OUTPUTS = ["to_untap", "to_draw", "zone_change", "loses_game", "advance_to", "player_damage", "pending"]


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
        return {f.stem: {tuple(r) for r in csv.reader(f.open(), delimiter="\t")}
                for f in Path(d).glob("*.csv")}


def run(state: dict, outputs: list[str]) -> dict:
    """Run the engine on `state`; return the requested output relations (memoized, pure)."""
    fkey = _facts_key(state)
    derived = _CACHE.get(fkey)
    if derived is None:
        derived = _CACHE[fkey] = _evaluate(fkey)
    return {rel: derived.get(rel, set()) for rel in outputs}


def _others(state: dict, p: str) -> list[str]:
    return sorted(q for (q,) in state["is_player"] if q != p)


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
            for p in players:
                hand = sorted(c for (pp, c) in state.get("in_hand", set()) if pp == p)
                for card in hand[:n]:
                    state["in_hand"].discard((p, card))
                    state.setdefault("graveyard", set()).add((card,))
                if hand:
                    print(f"    trigger {a}: {p} discards {min(n, len(hand))}")
        elif eff == "add_counter":                           # tgt = counter kind (p1p1/m1m1), on the source
            _bump_counter(state, src, tgt, n)
            print(f"    trigger {a}: {src} gets {n} {tgt} counter(s)")
        elif eff == "create_token":                          # tgt = predefined token name
            _create_token(state, tgt, ctrl, n)
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
    for (a, dp, dt, c, _ctrl) in sorted(out["pending_pt"]):
        eid = f"{a}__pt__{c}"
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
_HARMFUL_TARGET = {"destroy", "exile", "tap", "return_to_hand"}


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


def _pick_target(state: dict, ctrl: str, cls: str, verb: str, payload: str,
                 controls: set, powers: dict, creatures: set) -> str | None:
    """§601.2c choose a legal target for a single-target effect. `cls` constrains the legal set
    (any / you_control / opponent); within it, a harmful verb (removal/tap/bounce, or a P/T shrink)
    picks the strongest enemy creature and a beneficial one the strongest own creature."""
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in controls if p == ctrl}
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
    return max(cands, key=keyf)


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
    attackers = sorted(c for (c,) in run(state, ["may_attack"])["may_attack"]
                       if (c,) not in sick or c in haste)
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
    state["blocks"] = {(b, a) for a, b in blocks.items()}
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
            print(f"  ** {p} draws from an empty library and loses the game (§104.3c) **")
            return p
    for (c, frm, to) in sorted(out["zone_change"]):              # §701.8a zone moves
        state.setdefault(ZONE[frm], set()).discard((c,))
        state.setdefault(ZONE[to], set()).add((c,))
        verb = "dies" if (frm, to) == ("battlefield", "graveyard") else f"moves {frm}"
        print(f"    {c} {verb} -> {to}")
    for (p, n) in sorted(out["player_damage"]):                  # §510.2 persist combat damage
        print(f"    {p} takes {n} -> {_adjust_life(state, p, -int(n))} life")
    _apply_effects(state, out["pending"])                        # §603 -> §608 triggered effects
    dead = sorted(p for (p, v) in state["life"] if v <= LIFE_LOSS_THRESHOLD)
    if out["loses_game"] or dead:                                # §704.5a / triggered-effect death
        loser = sorted(out["loses_game"])[0][0] if out["loses_game"] else dead[0]
        print(f"  ** {loser} loses the game **")
        return loser
    return None


# §106.1a — the five colors plus colorless; a mana-creature (dork) abstains to colorless mana.
_COLORS = ("white", "blue", "black", "red", "green", "colorless")


def _untapped_sources(state: dict, ap: str) -> list[tuple[str, str | None]]:
    """The active player's untapped mana sources as (source_id, color_or_None): each untapped land it
    controls paired with each color it produces (land_produces, from the bridge), then each non-sick
    mana creature as colorless (§605). A land with no produced color still taps as a colorless source."""
    bf, ctrl, tapped = state.get("on_battlefield", set()), state.get("printed_control", set()), state.get("tapped", set())
    produces = state.get("land_produces", set())
    out: list[tuple[str, str | None]] = []
    lands = sorted(c for (c,) in bf if (c, "land") in state.get("printed_type", set())
                   and (ap, c) in ctrl and (c,) not in tapped)
    for c in lands:
        cols = sorted(col for (s, col) in produces if s == c)
        out.append((c, cols[0] if cols else "colorless"))     # one color per land (basics are monocolor)
    dorks = sorted(c for (c,) in bf if (c,) in state.get("mana_source", set()) and (ap, c) in ctrl
                   and (c,) not in tapped and (c,) not in state.get("_sick", set()))
    for c in dorks:
        out.append((c, "colorless"))                          # §605 mana dork -> colorless (kept simple)
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
    # build the colored pool: tally untapped sources by the color each produces.
    sources = _untapped_sources(state, ap)
    if not sources:
        return  # no driver-managed lands/dorks: leave any pre-seeded mana_pool/mana_available as-is (demos)
    by_color: dict[str, int] = {}
    for _src, col in sources:
        by_color[col] = by_color.get(col, 0) + 1
    state["mana_pool"] = {(p, c, n) for (p, c, n) in state.get("mana_pool", set()) if p != ap} \
        | {(ap, col, n) for col, n in by_color.items()}
    total = sum(by_color.values())
    state["mana_available"] = {(p, m) for (p, m) in state.get("mana_available", set()) if p != ap} | {(ap, total)}


def _spend_mana(state: dict, ap: str, spell: str) -> None:
    """Pay a spell's COLORED cost (§601.2g) by TAPPING untapped sources: first one right-color source per
    colored pip (mana_pip), then any remaining untapped source per generic (mana_generic). Tapping (not
    just decrementing) makes mana deplete faithfully — a tapped source can't pay again this turn or
    attack, and untaps next turn. The colored pool / flat count are refreshed from what's left untapped
    so the rest of the cast loop sees the reduced mana. INVARIANT: only call when can_afford held."""
    pips: dict[str, int] = {}
    for (s, col, n) in state.get("mana_pip", set()):
        if s == spell:
            pips[col] = pips.get(col, 0) + int(n)
    generic = sum(int(n) for (s, n) in state.get("mana_generic", set()) if s == spell)
    if not pips and generic == 0 and (spell, generic) not in state.get("mana_generic", set()):
        generic = next((int(c) for (s, c) in state.get("mana_cost", set()) if s == spell), 0)  # legacy fallback

    sources = _untapped_sources(state, ap)                    # (id, color) pairs, lands first then dorks
    if not sources:                                           # pre-seeded flat mana (demos): decrement count only
        cost = generic + sum(pips.values())
        cur = next((m for (p, m) in state.get("mana_available", set()) if p == ap), 0)
        state["mana_available"] = {(p, m) for (p, m) in state.get("mana_available", set()) if p != ap} | {(ap, max(0, cur - cost))}
        return
    used: set[str] = set()
    # 1) pay each colored pip from an untapped source of that exact color.
    for col, need in pips.items():
        paid = 0
        for sid, scol in sources:
            if paid >= need:
                break
            if sid in used or scol != col:
                continue
            used.add(sid); paid += 1
    # 2) pay generic from any remaining untapped source (color-agnostic, §202.1).
    paid = 0
    for sid, _scol in sources:
        if paid >= generic:
            break
        if sid in used:
            continue
        used.add(sid); paid += 1
    for sid in used:
        state.setdefault("tapped", set()).add((sid,))
    # refresh the pool/count from sources still untapped after this payment.
    by_color: dict[str, int] = {}
    for sid, col in sources:
        if sid not in used:
            by_color[col] = by_color.get(col, 0) + 1
    state["mana_pool"] = {(p, c, n) for (p, c, n) in state.get("mana_pool", set()) if p != ap} \
        | {(ap, col, n) for col, n in by_color.items()}
    cur = sum(by_color.values())
    state["mana_available"] = {(p, m) for (p, m) in state.get("mana_available", set()) if p != ap} | {(ap, cur)}


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
    return sorted(r for r in state.get("spell_effect", set()) if r[0] == spell)


def _choose_mode(state: dict, spell: str) -> None:
    """§601.2b — as a modal spell is cast, its controller chooses the mode(s). Greedy/deterministic: pick
    the first offered mode and record chose_mode so the engine derives active_mode(spell, mode); only that
    mode's effects resolve. (The bridge offers a mode only if its effects are resolvable.)"""
    modes = sorted(m for (s, m) in state.get("spell_mode", set()) if s == spell)
    if modes:
        state.setdefault("chose_mode", set()).add((spell, modes[0]))
        print(f"      {spell}: chooses mode {modes[0]}")


def _fire_cast_triggers(state: dict, caster: str, spell: str) -> None:
    """§601.2i — 'whenever you cast a spell' triggers fire as the spell goes on the stack. Open the cast
    window (cast_spell) so the engine fires the matching cast-triggers, then apply only the NEW pending
    the cast produced (diff vs. the pre-cast pending) so unrelated triggers aren't double-applied. The
    cast window stays set across _apply_effects so cast-triggered creature effects fire too."""
    before = run(state, ["pending"])["pending"]
    state["cast_spell"] = {(caster, spell)}
    new = run(state, ["pending"])["pending"] - before
    _apply_effects(state, new)
    state["cast_spell"] = set()


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
    for (_s, mode) in sorted(r for r in state.get("spell_reanimate", set()) if r[0] == spell):
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
    rows = sorted(r for r in state.get("spell_target", set()) if r[0] == spell)
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
    rows = sorted(r for r in state.get("spell_damage", set()) if r[0] == spell)
    for (_s, n, kind) in rows:
        _apply_damage(state, spell, n, kind, ctrl)


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
    rows = sorted(r for r in state.get("spell_scope", set()) if r[0] == spell)
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
        else:
            _apply_effects(state, {(top, eff, amt, tgt, src, actrl)})
        return
    if (top,) in out["fizzles"]:
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
    _fire_cast_triggers(state, p, spell)                     # §601.2i — cast triggers
    print(f"    {p} responds: casts {spell} (onto the stack)")
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
    while True:
        state["has_priority"] = {(ap,)}                      # §601 active player has priority in its main phase
        castable = sorted(s for (p, s) in run(state, ["can_cast"])["can_cast"] if p == ap)
        if not castable:
            break
        spell = castable[0]
        _spend_mana(state, ap, spell)                        # §601.2g — consume the mana so casts are limited
        state["in_hand"].discard((ap, spell))
        _stack_push(state, spell, ap)
        _choose_mode(state, spell)                           # §601.2b — choose mode(s) if it's a modal spell
        _fire_cast_triggers(state, ap, spell)                # §601.2i — 'whenever you cast a spell' triggers
        print(f"    {ap} casts {spell}")
        _resolve_stack(state, ap, players)                   # response window + top-down resolution
    state["has_priority"] = set()
    _activate_phase(state, ap, players)                      # §602 — then use a non-mana activated ability if able


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
        out.append(row)
    return sorted(out)


def _activate_phase(state: dict, ap: str, players: list) -> None:
    """§602 — the active player activates ONE non-mana activated ability it can afford, pushing it onto
    the stack to resolve (with a response window) like a spell. Greedy single activation keeps the loop
    decisive; the ability's effect runs through the shared _apply_effects."""
    usable = _activatable(state, ap)
    if not usable:
        return
    a, src, cost, taps, eff, amt, tgt = usable[0]
    if int(cost):                                            # pay the mana part via the mana model
        _spend_ability_mana(state, ap, int(cost))
    if taps == "T":
        state.setdefault("tapped", set()).add((src,))        # §602.2 pay {T}
    state.setdefault("_ability_effect", {})[a] = (eff, int(amt), tgt, src, ap)
    _stack_push(state, a, ap)
    print(f"    {ap} activates {a} ({src}: {eff} {amt})")
    _resolve_stack(state, ap, players)


def _spend_ability_mana(state: dict, ap: str, cost: int) -> None:
    """Pay an activated ability's mana cost by tapping that many untapped lands/mana-creatures — the same
    payment shape as _spend_mana, reused so abilities deplete mana faithfully (the mana model owns it)."""
    bf, ctrl, tapped = state.get("on_battlefield", set()), state.get("printed_control", set()), state.get("tapped", set())
    lands = sorted(c for (c,) in bf if (c, "land") in state.get("printed_type", set()) and (ap, c) in ctrl and (c,) not in tapped)
    dorks = sorted(c for (c,) in bf if (c,) in state.get("mana_source", set()) and (ap, c) in ctrl
                   and (c,) not in tapped and (c,) not in state.get("_sick", set()))
    for c in (lands + dorks)[:cost]:
        state.setdefault("tapped", set()).add((c,))
    cur = next((m for (q, m) in state.get("mana_available", set()) if q == ap), 0)
    state["mana_available"] = {(q, m) for (q, m) in state.get("mana_available", set()) if q != ap} | {(ap, max(0, cur - cost))}


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
        nxt_p = players[(players.index(ap) + 1) % len(players)]  # pass the turn (§500.6)
        state["active_player"] = {(nxt_p,)}
        state["current_step"] = {("untap",)}
        state["attacks"], state["blocks"] = set(), set()        # combat declarations don't carry over
        state["_land_played"] = set()                           # §305.2 — a fresh land drop next turn
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
