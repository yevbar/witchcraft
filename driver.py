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
from collections import OrderedDict
from pathlib import Path

import sys

import engine_native        # compiled-binary backend (subprocess); falls back to the interpreter if unavailable
import engine_inproc        # in-process compiled engine (ctypes .so, no fork/files); preferred when buildable
import engine_incremental   # incremental `update` backend (fork --incremental); opt-in via MTG_INCREMENTAL
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
      * state['_forced'][key]                                      (a SPECIFIC per-step choice env.step /
                                                                    search.apply injects — authoritative)
      * state['_policy'](state, key, options, default) -> choice   (the general policy, e.g. a net/MCTS)
      * default                                                    (the greedy heuristic — unchanged play)
    `_forced` is checked FIRST so a choice pinned for THIS step wins over the general `_policy`. It matters
    when both are present: env.step realizes a chosen top-level move (an attack/block set, a cast's
    mode/target) by setting `_forced`, while `play()` installs the seat's policy as `_policy` to resolve the
    OTHER sub-choices. With `_policy` first, its default silently overrode the injected combat declaration —
    the engine attacked with all eligible regardless of what the player chose. A forced choice is validated
    against `options` when options is a concrete collection (else trusted)."""
    forced = state.get("_forced")
    if forced and key in forced:
        choice = forced[key]
        if options is None or choice in options:
            return choice
    pol = state.get("_policy")
    if pol is not None:
        return pol(state, key, options, default)
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
    import observe
    observe.on_shuffle(state, p)                             # §708: forget library ORDER + end face-up reveals (keep identity memory)


def _flip_coin(state: dict, key: str = "coin") -> str:
    """§705.2 flip a coin -> 'heads' / 'tails' through the chance seam."""
    return _random(state, key, ("heads", "tails"))


def _roll_die(state: dict, key: str, sides: int) -> int:
    """§706 roll a single dN -> an integer in 1..sides through the SAME seeded chance seam as the coin flip
    (_random). A search/policy can OBSERVE or FIX the roll via state['_chance']; otherwise it is a uniform
    draw from the state's seeded RNG, so a game is fully reproducible given its seed and a clone carries its
    own copy of the stream. NEVER use python's global random here — that would be a clone-safety/repro bug."""
    sides = max(1, int(sides))
    return int(_random(state, key, tuple(range(1, sides + 1))))


def _tap(state: dict, c: str) -> None:
    """§701.20 tap a permanent AND record it in just_tapped so a 'whenever ~ becomes tapped' trigger (City of
    Brass) can fire at the next _fire_tap_triggers checkpoint. A no-op for the trigger if it was already tapped."""
    tapped = state.setdefault("tapped", set())
    if (c,) not in tapped:
        tapped.add((c,))
        state.setdefault("_just_tapped", set()).add((c,))


def _resolve_delayed_upkeep(state: dict, ap: str) -> str | None:
    """§603.7c resolve any DELAYED upkeep trigger due for `ap` (the Pact cycle: 'at your next upkeep pay
    <cost> or lose'). 'Next upkeep' = an upkeep on a LATER turn than the one it was scheduled. The controller
    pays the generic cost from its mana if able (a greedy player always pays to avoid losing); otherwise it
    loses the game. Returns the loser if a Pact goes unpaid, else None."""
    due = [(c, cost, t) for (c, cost, t) in state.get("_delayed_upkeep", set())
           if c == ap and state.get("_turn", 0) > t]
    for entry in due:
        c, cost, _t = entry
        state["_delayed_upkeep"].discard(entry)
        _refresh_mana_pool(state, c)
        avail = next((m for (q, m) in state.get("mana_available", set()) if q == c), 0)
        if avail >= cost:
            _spend_ability_mana(state, c, cost)
            print(f"    {c} pays {{{cost}}} for the Pact at upkeep")
        else:
            print(f"  ** {c} can't pay the Pact's {{{cost}}} at upkeep and loses the game (§104.3a) **")
            return c
    return None


def _fire_tap_triggers(state: dict) -> None:
    """§603 fire 'whenever ~ becomes tapped' triggers for the permanents tapped since the last checkpoint. Fed
    as just_tapped (mirrors the landfall just_entered pattern); resolve the pending, then clear the window so
    it doesn't re-fire. Called at SAFE points (after mana payment / combat), never mid-tap-loop."""
    just = state.pop("_just_tapped", set())
    if not just:
        return
    state["just_tapped"] = just
    _apply_effects(state, *_pending_both(state))
    state["just_tapped"] = set()


def _fire_lifegain_triggers(state: dict) -> None:
    """§603 fire 'whenever YOU gain life' triggers (Celestial Unicorn, Ajani's Pridemate, Archangel of Thune,
    Cleric Class) for the players whose life INCREASED since the last checkpoint. _adjust_life arms
    _just_gained_life with each gainer (post-replacement, post-'can't gain'); this opens the driver-fed
    just_gained_life window, applies the NEW pending the window produces (diff vs. the standing pending so
    unrelated triggers aren't re-applied), then CLOSES the window before applying — the search-trigger lesson:
    a lifegain trigger that itself gains life (a counter on a lifelinker, a 'gain N life' trigger) re-arms the
    accumulator, which a later DRAIN-LOOP round picks up, rather than re-firing this same pending. The drain
    loop is ROUND-CAPPED so a self-re-arming chain (Archangel pumps a lifelinker that then gains again) can't
    run away. Called at SAFE checkpoints (after a resolving stack object / each step), never mid-_adjust_life.
    CONTROLLER-scoped: the engine fires only watchers whose controller is the gaining player."""
    if not state.get("_just_gained_life"):
        return
    rounds = 0
    while state.get("_just_gained_life") and rounds < 16:     # ROUND CAP — natural lifegain chains are short
        rounds += 1
        gainers = state.pop("_just_gained_life")              # the players who gained this round (each fires once)
        before, before_dyn = _pending_both(state)
        state["just_gained_life"] = gainers
        now, now_dyn = _pending_both(state)
        new, new_dyn = now - before, now_dyn - before_dyn
        state["just_gained_life"] = set()                    # CLOSE the window before applying — a nested gain
        _apply_effects(state, new, new_dyn)                  # re-arms _just_gained_life for the next loop round
    state["just_gained_life"] = set()
    state.pop("_just_gained_life", None)                     # cap hit -> drop any residual so it can't leak forward


def _fire_counter_placed_triggers(state: dict) -> None:
    """§603/§122 fire '+1/+1 COUNTER-PLACEMENT' triggers (Lonis, Sharktocrab, Knighted Myr, Fathom Mage, Shalai
    and Hallar, Simic Ascendancy, …) for the creatures one or more +1/+1 counters were just put on. _bump_counter
    arms _just_p1p1_placed with each such creature (gated to the +1/+1 kind, n > 0, NOT the §614.13 enters-with
    path). This opens the driver-fed just_p1p1_placed window, applies the NEW pending the window produces (DIFF
    vs. the standing pending so unrelated triggers aren't re-applied), then CLOSES the window before applying —
    the same lesson as _fire_lifegain_triggers: a counter-placement trigger that ITSELF places +1/+1 counters
    (Generous Pup, Enduring Scalelord, Botanical Brawler, Hardened Bonds) re-arms the accumulator via
    _bump_counter, which a LATER drain-loop round picks up rather than re-firing this same pending. The loop is
    ROUND-CAPPED so a self-re-arming chain can't run away (the RE-FIRE GUARD). Called at SAFE checkpoints (after
    a resolving stack object / each step), never mid-_bump_counter. The engine scopes each watcher (SELF =
    the creature is the source; YOUR-CREATURE = the source's controller controls it)."""
    if not state.get("_just_p1p1_placed"):
        return
    rounds = 0
    while state.get("_just_p1p1_placed") and rounds < 16:    # ROUND CAP — counter-placement chains are short
        rounds += 1
        placed = state.pop("_just_p1p1_placed")              # the creatures counters landed on this round
        before, before_dyn = _pending_both(state)
        state["just_p1p1_placed"] = placed
        now, now_dyn = _pending_both(state)
        new, new_dyn = now - before, now_dyn - before_dyn
        state["just_p1p1_placed"] = set()                    # CLOSE the window before applying — a nested counter
        _apply_effects(state, new, new_dyn)                  # placement re-arms _just_p1p1_placed for the next round
    state["just_p1p1_placed"] = set()
    state.pop("_just_p1p1_placed", None)                     # cap hit -> drop any residual so it can't leak forward
def _fire_you_do_costs(state: dict) -> None:
    """§603.2c 'If you do' SEQUENCING — for each FIRED antecedent ability that carries an optional cost
    (you_do_cost), OFFER the cost through the _choose seam (key 'you_do_<kind>', DEFAULT = decline — the
    faithful common line, leaving the consequent inert exactly as before this feature). Iff the controller
    elects to pay AND can afford it, PAY the cost, open the did_optional(ante_IA) window so the engine fires
    the paired 'you_did' consequent (the SAME reflexive-window pattern as just_tapped / just_gained_life),
    apply the NEW pending the window produces (the consequent Y), then CLOSE the window. The consequent
    resolves through the shared effect path — its targets/scope come from the engine exactly like any trigger.

    Mirrors _fire_lifegain_triggers: read the ANTECEDENT abilities that fired this checkpoint, diff the pending
    so only the consequent's NEW effects are applied. Costs the driver can model: pay {N} mana / sacrifice a
    creature / exile this / discard a card / pay N life. An unaffordable or declined cost is a faithful no-op."""
    costs = {a: (k, int(n)) for (a, k, n) in state.get("you_do_cost", set())}
    if not costs:
        return
    fired = {a for (a, _s) in run(state, ["fires"])["fires"] if a in costs}   # antecedent IAs that fired now
    for ante in sorted(fired):
        kind, amt = costs[ante]
        # ante IA == f'{src}_{aid}'; recover its source instance (the LONGEST controlled-instance prefix of the
        # IA, since aid may itself contain '_') and that instance's controller.
        src = max((c for (_p, c) in state.get("printed_control", set()) if ante.startswith(f"{c}_")),
                  key=len, default=None)
        ctrl = next((p for (p, c) in state.get("printed_control", set()) if c == src), None) if src else None
        if ctrl is None:
            continue
        if not _pay_optional_cost(state, src, kind, amt, ctrl, dry_run=True):
            continue                                          # can't afford / nothing to pay with -> can't take it
        if not _choose(state, f"you_do_{kind}", (False, True), False):
            continue                                          # DEFAULT: decline (consequent stays inert)
        if not _pay_optional_cost(state, src, kind, amt, ctrl, dry_run=False):
            continue
        before, before_dyn = _pending_both(state)             # open the reflexive window for THIS antecedent only
        state.setdefault("did_optional", set()).add((ante,))
        now, now_dyn = _pending_both(state)
        new, new_dyn = now - before, now_dyn - before_dyn
        state["did_optional"].discard((ante,))                # CLOSE before applying (consequent shouldn't re-fire)
        print(f"    §603.2c {ctrl} takes the optional {kind} cost of {ante} -> its 'if you do' consequent resolves")
        _apply_effects(state, new, new_dyn)


def _pay_optional_cost(state: dict, src: str, kind: str, amt: int, ctrl: str, dry_run: bool) -> bool:
    """Pay (or test affordability of) an 'If you do' antecedent's optional cost. Returns True if the cost is
    payable (and, when not dry_run, was paid). `src` is the antecedent ability's source permanent instance."""
    if kind == "pay":                                         # §118 generic mana
        _refresh_mana_pool(state, ctrl)
        avail = next((m for (q, m) in state.get("mana_available", set()) if q == ctrl), 0)
        if avail < amt:
            return False
        if not dry_run:
            _spend_ability_mana(state, ctrl, amt)
        return True
    if kind == "pay_life":                                    # §119 pay N life (amt 0 = a variable cost — skip)
        if amt <= 0:
            return False
        if next((l for (p, l) in state.get("life", set()) if p == ctrl), 0) <= amt:
            return False                                      # don't pay yourself to 0 or below for an optional perk
        if not dry_run:
            _adjust_life(state, ctrl, -amt)
        return True
    if kind == "exile_self":                                 # 'you may exile this' — the source leaves for exile
        if (src,) not in state.get("on_battlefield", set()) and (src,) not in state.get("graveyard", set()):
            return False
        if not dry_run:
            for z in ("on_battlefield", "graveyard"):
                state.get(z, set()).discard((src,))
            state.setdefault("exile", set()).add((src,))
        return True
    if kind == "sacrifice":                                  # 'you may sacrifice a creature'
        cands = _sac_candidates(state, ctrl, "creature", src)
        if not cands:
            return False
        if not dry_run:
            for _ in range(max(1, amt)):
                cands = _sac_candidates(state, ctrl, "creature", src)
                if not cands:
                    break
                _sacrifice(state, _choose(state, "sacrifice", cands, _sac_default(state, cands, src)))
        return True
    if kind == "discard":                                   # 'you may discard a card'
        hand = sorted(c for (p, c) in state.get("in_hand", set()) if p == ctrl)
        if not hand:
            return False
        if not dry_run:
            for _ in range(max(1, amt)):
                hand = sorted(c for (p, c) in state.get("in_hand", set()) if p == ctrl)
                if not hand:
                    break
                card = _choose(state, "discard", hand, hand[0])
                state["in_hand"].discard((ctrl, card))
                state.setdefault(_discard_zone(state, ctrl), set()).add((card,))
        return True
    return False


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
           "combat_commander_damage", "combat_poison", "pending", "ev_combat_dmg_player"]


def _lit(x: object) -> str:
    return f'"{x}"' if isinstance(x, str) else str(x)


# Memoization of the engine transition. run() is a PURE function of the DECLARED facts in `state`
# (souffle is deterministic; nothing else is read), so identical engine-inputs always derive the
# same outputs. A lookahead search re-reaches the same engine-input on many branches; caching it
# collapses "total tree nodes × one souffle call" into "DISTINCT engine-inputs × one souffle call",
# which (with search.canonical_key dedup on top) is what makes deep multi-state lookahead cheap.
# Keyed by the canonical (order-independent) fact set; cleared with clear_cache() between scenarios.
# BOUNDED LRU (lever #2): the per-state eval cache is what amortizes a search node's expansion — sibling lines
# share the same auto-advance phase crossings (untap/upkeep/draw/combat/…), so a persistent cache turns env.step
# from ~9 fresh evals into a handful of hits (measured ~7.6x warm vs a cold cache). An UNBOUNDED dict would grow
# without limit over a long search and OOM — forcing periodic clear_cache(), which throws the amortization away.
# An OrderedDict keyed by the canonical fact set, evicting least-recently-used past a cap, keeps the amortization
# while bounding memory. MTG_EVAL_CACHE sets the cap (0 = unbounded; default 200k distinct states).
_CACHE: "OrderedDict" = OrderedDict()
_CACHE_MAX = int(os.environ.get("MTG_EVAL_CACHE", "200000"))
_EVALS = [0]                                          # count of actual souffle invocations (cache misses)
_EVICTIONS = [0]


def _facts_key(state: dict) -> frozenset:
    return frozenset((rel, frozenset(rows)) for rel, rows in state.items()
                     if rel in DECLARED and rows)


def clear_cache() -> None:
    _CACHE.clear()
    _EVALS[0] = 0
    _EVICTIONS[0] = 0


def cache_stats() -> dict:
    return {"distinct_states": len(_CACHE), "souffle_evals": _EVALS[0],
            "capacity": _CACHE_MAX, "evictions": _EVICTIONS[0]}


def _evaluate(fkey: frozenset) -> dict:
    """Run the engine once for a fact set and return ALL outputs (cached). The program derives every
    relation regardless of what's read back, so we capture them all and serve any later request.

    Backend preference (all byte-identical): (1) the IN-PROCESS compiled engine (engine_inproc — the souffle
    C++ linked as a .so and called over ctypes, no fork/no files; ~10x the subprocess path, ~0.7 ms/state);
    (2) the compiled binary via subprocess (engine_native); (3) the souffle interpreter. MTG_NO_NATIVE forces
    the interpreter; MTG_NO_INPROC forces the subprocess (skips the in-process .so) for A/B comparison."""
    _EVALS[0] += 1
    if not os.environ.get("MTG_NO_NATIVE"):
        # MTG_INCREMENTAL: the in-process incremental `update` backend (bootstrap once, then stage the input
        # diff and re-evaluate only the affected strata). Byte-identical to a full recompute (verified on a
        # full demo game across 27 engine calls); fastest for the sequential, small-diff turn loop.
        if os.environ.get("MTG_INCREMENTAL") and engine_incremental.available():
            return engine_incremental.evaluate(fkey)
        if not os.environ.get("MTG_NO_INPROC") and engine_inproc.available():
            return engine_inproc.evaluate(fkey)
        if engine_native.available():
            return engine_native.evaluate(fkey)
    facts = "\n".join(f"{rel}({', '.join(map(_lit, row))})."
                      for rel, rows in fkey for row in rows)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "e.dl").write_text(RULES + "\n" + facts)
        subprocess.run([engine_native._souffle_bin(), f"{d}/e.dl", "-D", d], check=True, capture_output=True)
        return {f.stem: {tuple(r) for r in csv.reader(f.open(), delimiter="\t")}    # only non-empty outputs:
                for f in Path(d).glob("*.csv") if f.stat().st_size > 0}              # absent rel -> empty set in run()


def run(state: dict, outputs: list[str]) -> dict:
    """Run the engine on `state`; return the requested output relations (memoized, pure). The cache is a
    bounded LRU: a hit refreshes recency; a miss evaluates, inserts, and evicts the oldest past the cap."""
    fkey = _facts_key(state)
    derived = _CACHE.get(fkey)
    if derived is None:
        derived = _CACHE[fkey] = _evaluate(fkey)
        if _CACHE_MAX and len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)               # evict least-recently-used
            _EVICTIONS[0] += 1
    else:
        _CACHE.move_to_end(fkey)                     # mark most-recently-used
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


# --- TURN-STRUCTURE effects (§505/§506 extra combat, §500.7 skip step) — driver-only counters/flags --------
# Both mirror extra_turn: a resolving effect (effect_handlers/extra_combat.py / skip_step.py) only BUMPS a
# per-turn marker in state; the turn loop below does the structural work. Cleared each turn at turn-pass.
_COMBAT_STEPS = ("beginning_of_combat", "declare_attackers", "declare_blockers",
                 "combat_damage", "end_of_combat")


def _skipping_step(state: dict, ap: str, step: str) -> bool:
    """§500.7 — is the active player's `step` being skipped this turn? Reads the per-turn flags set by the
    `skip` effect (effect_handlers/skip_step.py): state['_skip_step'] = {(player, engine_step)}. A
    'combat_phase' skip flag (engine_step 'combat') suppresses the WHOLE combat (every §506 step)."""
    flags = state.get("_skip_step", set())
    if (ap, step) in flags:
        return True
    return step in _COMBAT_STEPS and (ap, "combat") in flags


def _extra_combat_redirect(state: dict, ap: str, advance_to: set) -> set:
    """§505/§506 — at end_of_combat, if the active player has a pending additional combat phase
    (state['_extra_combats'][ap] > 0, set by effect_handlers/extra_combat.py), loop back into combat
    (beginning_of_combat) instead of advancing to the postcombat main phase; consume one marker. Bounded by
    the counter itself (each redirect decrements it). Returns the (possibly redirected) advance_to set."""
    extra = state.setdefault("_extra_combats", {})
    if extra.get(ap, 0) > 0 and advance_to == {("postcombat_main",)}:
        extra[ap] -= 1
        print(f"    {ap} gets an additional combat phase (§505/§506)")
        return {("beginning_of_combat",)}
    return advance_to


def _creatures_of(state: dict, p: str) -> list[str]:
    """Creatures p controls — from the engine's DERIVED controls/creature (which fold in
    printed_control/printed_type and the layer system), not raw state, so a permanent that
    entered via casting is included just like one set up directly."""
    out = run(state, ["controls", "creature"])
    creatures = {c for (c,) in out["creature"]}
    return sorted(c for (pp, c) in out["controls"] if pp == p and c in creatures)


def _dyn_count(state: dict, tag: str, ctrl: str) -> int:
    """STRUCTURAL #3 — resolve a 'for each' count TAG to a live number from ctrl's perspective, at
    resolution time. Recognized CONTROLLER-scoped dimensions only (faithful-or-abstain — the engine's
    dyn_amount_tag table already restricts to these). Battlefield counts come from the engine's DERIVED
    controls/creature/has_type (so casting-entered permanents and layer effects are included, like
    _creatures_of); hand/graveyard counts come from the zone sets the driver tracks.
      opponents            — the number of opponents you have (the other players)
      creature_yc          — creatures you control
      artifact_yc/land_yc  — artifacts/lands you control (has_type)
      cards_in_hand        — cards in your hand
      creature_cards_in_gy — creature cards in your graveyard (owned by you, via printed_control + card_type)"""
    if tag == "opponents":                                    # §111 'for each opponent you have' (token-makers)
        return len(_others(state, ctrl))
    if tag == "creature_yc":
        return len(_creatures_of(state, ctrl))
    if tag in ("artifact_yc", "land_yc"):
        t = "artifact" if tag == "artifact_yc" else "land"
        # printed_type is what the engine reliably exposes for non-creature permanents (has_type is creature-
        # centric / §613 add-remove only); a permanent on the battlefield under ctrl's control with that type.
        out = run(state, ["controls", "printed_type"])
        typed = {c for (c, ty) in out["printed_type"] if ty == t}
        return sum(1 for (p, c) in out["controls"] if p == ctrl and c in typed)
    if tag == "cards_in_hand":
        return sum(1 for (p, _c) in state.get("in_hand", set()) if p == ctrl)
    if tag == "creature_cards_in_gy":
        owner = {c: p for (p, c) in state.get("printed_control", set())}
        io = {i: s for (i, s) in state.get("instance_of", set())}
        cre = {s for (s, ty) in state.get("card_type", set()) if ty == "creature"}
        return sum(1 for (c,) in state.get("graveyard", set())
                   if owner.get(c) == ctrl and io.get(c) in cre)
    return 0   # unrecognized tag -> abstain (no engine rule should produce one)


def _pending_both(state: dict) -> tuple:
    """The engine's current (pending, pending_dyn) — the fixed and the 'for each' triggered effects. The
    trigger chokepoints fire both together (a §603 trigger may have either kind of amount)."""
    out = run(state, ["pending", "pending_dyn"])
    return out["pending"], out["pending_dyn"]


def _apply_dyn(state: dict, pending_dyn: set) -> None:
    """STRUCTURAL #3 — resolve the DYNAMIC ('for each') player-scoped effects (pending_dyn / spell_dyn_effect
    rows the driver staged as pending_dyn tuples) by evaluating each count TAG to a number and routing the
    resulting fixed amount through the shared _apply_effects path. A zero count -> the effect does nothing
    (e.g. draw 0), which is correct. Rows: (ability, effect, base, tag, target, source, controller)."""
    fixed = set()
    for (a, eff, base, tag, tgt, src, ctrl) in sorted(pending_dyn):
        n = int(base) * _dyn_count(state, tag, ctrl)
        print(f"    dyn {a}: {eff} amount = {base} per {tag} = {n}")
        fixed.add((a, eff, n, tgt, src, ctrl))
    if fixed:
        _apply_effects(state, fixed)


def _set_life(state: dict, p: str, n: int) -> None:
    state["life"] = {(q, v) for (q, v) in state["life"] if q != p} | {(p, n)}


def _life_gain_mods(state: dict, p: str) -> tuple:
    """§614 life-gain replacements p controls: (#doublers, +flat). 'twice that amount' (Alhammarret's
    Archive / Rhox Faithmender) doubles; 'that amount plus 1' adds 1 — applied to ANY life p gains."""
    lr = state.get("life_repl")
    if not lr:
        return (0, 0)
    io = {i: c for (i, c) in state.get("instance_of", set())}
    dbl = plus = 0
    for (pp, c) in state.get("printed_control", set()):
        if pp == p:
            s = io.get(c)
            dbl += (s, "double") in lr
            plus += (s, "plus1") in lr
    return (dbl, plus)


def _cant_gain_life(state: dict, p: str) -> bool:
    """§604/§614 a life-gain PREVENTION affecting p (static_player, when loaded): 'players_cant_gain_life'
    from ANY permanent (Sulfuric Vortex/Witch Hunt) prevents EVERYONE'S gain; 'opponents_cant_gain_life'
    (Tibalt/Erebos) prevents gain for the controller's OPPONENTS. False if static_player isn't loaded."""
    sp = state.get("static_player")
    if not sp:
        return False
    io = {i: c for (i, c) in state.get("instance_of", set())}
    for (owner, c) in state.get("printed_control", set()):
        slug = io.get(c)
        if (slug, "players_cant_gain_life") in sp:
            return True
        if (slug, "opponents_cant_gain_life") in sp and owner != p:   # p is an OPPONENT of the controller
            return True
    return False


def _adjust_life(state: dict, p: str, delta: int) -> int:
    if delta > 0:                                            # §614 a life GAIN — apply p's life-gain replacements
        if _cant_gain_life(state, p):                        # §604 a 'can't gain life' static -> the gain is 0
            delta = 0
        else:
            dbl, plus = _life_gain_mods(state, p)
            delta = delta * (2 ** dbl) + plus
    if delta > 0:                                            # §603 p ACTUALLY gained life (post-replacement, post
        state.setdefault("_just_gained_life", set()).add((p,))  # 'can't gain') -> arm a 'whenever you gain life' window
        # §611.2 also set the TURN-SCOPED flag (the engine input gained_life_this_turn) the SOI 'Infusion'
        # continuous condition reads ('… as long as you gained life this turn'). Unlike the per-resolution
        # just_gained_life window above, this persists for the rest of the turn (cleared at §514.2 cleanup).
        state.setdefault("gained_life_this_turn", set()).add((p,))
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


def _doubler_count(state: dict, ctrl: str, kind: str) -> int:
    """§614 the number of replacement doublers (static 'doubles_<kind>' — Doubling Season/Parallel Lives for
    'tokens', Primal Vigor/Doubling Season for 'counters') that the controller `ctrl` has, so a created/
    placed quantity is multiplied by 2**count (two Doubling Seasons -> x4). Reads the driver-only `doubler`
    facts the bridge emits, gated on what ctrl controls (printed_control). 0 -> no doubling."""
    if not state.get("doubler"):
        return 0
    io = {i: c for (i, c) in state.get("instance_of", set())}
    dset = state["doubler"]
    return sum(1 for (p, c) in state.get("printed_control", set())
               if p == ctrl and (io.get(c), kind) in dset)


def _no_untap_set(state: dict) -> set:
    """§502 the instances that DON'T untap: the verb-set continuous lock (state['doesnt_untap'], from
    effect_handlers/no_untap) UNION the static EDB facts (state['static_no_untap'] = {(slug, who)}, fed by
    card_facts — Mana Vault/Basalt Monolith 'self', and Auras/Equipment whose enchanted/equipped permanent
    is locked). 'self' -> every instance of that slug; enchanted_/equipped_ -> the permanent it's attached to.
    Board-scope whos (e.g. 'red_creatures') abstain (faithful — needs a filtered scope we don't resolve here)."""
    out = set(state.get("doesnt_untap", set()))
    snu = state.get("static_no_untap")
    if not snu:
        return out
    by_slug = {}
    for (slug, who) in snu:
        by_slug.setdefault(slug, set()).add(who)
    inst_of = state.get("instance_of", set())
    attached = state.get("attached_to", set())               # (aura/equip, host)
    for (inst, slug) in inst_of:
        whos = by_slug.get(slug)
        if not whos:
            continue
        if "self" in whos:
            out.add((inst,))
        if whos & {"enchanted_creature", "enchanted_permanent", "equipped_creature"}:
            for (a, host) in attached:                       # this Aura/Equipment instance locks its host
                if a == inst:
                    out.add((host,))
    return out


def _gy_replaced(state: dict, obj: str) -> bool:
    """§614 graveyard-hate replacement: would `obj` be EXILED instead of going to a graveyard? True if a
    player controls a 'a card would be put into a graveyard … exile it instead' permanent whose scope covers
    obj's owner — 'all' graveyards (Rest in Peace) for everyone, or 'opponents' (Leyline of the Void) for a
    card owned by an opponent of that permanent's controller. Owner is proxied by obj's controller."""
    gr = state.get("gy_repl")
    if not gr:
        return False
    io = {i: c for (i, c) in state.get("instance_of", set())}
    pc = state.get("printed_control", set())
    owner = next((p for (p, c) in pc if c == obj), None)
    for (p, c) in pc:
        s = io.get(c)
        if (s, "all") in gr:
            return True
        if (s, "opponents") in gr and owner is not None and owner != p:
            return True
    return False


def _consume_regen_shield(state: dict, obj: str) -> bool:
    """§701.15 REGENERATION replacement. If `obj` has a regeneration shield (state['_regen_shield'], set up
    by effect_handlers/regeneration) and it ISN'T cant_be_regenerated, REPLACE this destruction: consume the
    shield, TAP it, REMOVE it from combat, and it is NOT destroyed — return True so the destroy chokepoint
    leaves it on the battlefield. Otherwise return False (it dies normally). The shield is per-turn (cleared
    at cleanup) and one-shot (consumed here). Mirrors how _gy_replaced / cant_be_destroyed gate the destroy."""
    if (obj,) not in state.get("_regen_shield", set()):
        return False
    if (obj,) in state.get("cant_be_regenerated", set()):     # §701.15g — the shield can't save it
        return False
    state["_regen_shield"].discard((obj,))                    # §701.15c — used up
    _tap(state, obj)                                          # §701.15c (1) tap it
    # §701.15c (2) remove it from combat — drop any attack/block declaration mentioning it.
    state["attacks"] = {row for row in state.get("attacks", set()) if obj not in row}
    state["blocks"] = {row for row in state.get("blocks", set()) if obj not in row}
    print(f"    {obj} would be destroyed — a §701.15 regeneration shield taps it instead (removed from combat)")
    return True


def _redirect_player_damage(state: dict, player: str) -> str | None:
    """§616 DAMAGE REDIRECTION. If `player` is protected by a redirect (state['_damage_redirect'][player],
    set by effect_handlers/redirect_damage from a Pariah/Kjeldoran/en-Kor 'damage to you is dealt to <a
    creature you control> instead'), return the creature the damage is rerouted to (if it's still on the
    battlefield) — the §616 replacement. Otherwise None (the player takes the damage normally). Mirrors how
    _gy_replaced / _consume_regen_shield gate their chokepoints; per-turn, cleared at §514.2 cleanup."""
    creature = state.get("_damage_redirect", {}).get(player)
    if creature is not None and (creature,) in state.get("on_battlefield", set()):
        return creature
    return None


# --- §720 THE MONARCH (a player designation with ongoing draw + steal-on-combat-damage) ---------------
def _set_monarch(state: dict, p: str, reason: str = "") -> None:
    """§720.2 designate `p` the monarch, replacing any prior one (only ONE monarch at a time). Public info."""
    cur = next((q for (q,) in state.get("_monarch", set())), None)
    if cur == p:
        return
    state["_monarch"] = {(p,)}
    tail = f" ({reason})" if reason else ""
    print(f"    {p} becomes the monarch (§720.2){tail}")


def _steal_monarch_on_combat(state: dict, dmg_pairs: set) -> None:
    """§720.5 whenever a creature deals COMBAT DAMAGE to the monarch, that creature's CONTROLLER becomes the
    monarch. dmg_pairs = ev_combat_dmg_player (source, player) from the engine. If a creature the monarch
    doesn't control hit the current monarch this combat, its controller takes the crown. (If several creatures
    hit the monarch, the controller of the first by sorted id takes it — a deterministic referee resolution.)"""
    cur = next((q for (q,) in state.get("_monarch", set())), None)
    if cur is None or not dmg_pairs:
        return
    controls = run(state, ["controls"])["controls"]
    ctrl_of = {c: p for (p, c) in controls}
    for (src, p) in sorted(dmg_pairs):
        if p != cur:                                          # only damage to the monarch matters
            continue
        new = ctrl_of.get(src)
        if new is not None and new != cur:                    # a creature you don't already monarch-own hit you
            _set_monarch(state, new, reason=f"{src} dealt combat damage to the monarch")
            return


# --- §720-ish THE INITIATIVE (a player designation; structurally a sibling of THE MONARCH) ------------
def _set_initiative(state: dict, p: str, reason: str = "") -> None:
    """Designate `p` the initiative-holder, replacing any prior one (only ONE at a time). Public info. The
    upkeep 'venture into Undercity' consequence ABSTAINS (no dungeon model) — we hold only the designation."""
    cur = next((q for (q,) in state.get("_initiative", set())), None)
    if cur == p:
        return
    state["_initiative"] = {(p,)}
    tail = f" ({reason})" if reason else ""
    print(f"    {p} takes the initiative{tail}")


def _steal_initiative_on_combat(state: dict, dmg_pairs: set) -> None:
    """Whenever a creature deals COMBAT DAMAGE to the initiative-holder, that creature's CONTROLLER takes the
    initiative. Mirrors _steal_monarch_on_combat exactly (reads ev_combat_dmg_player + controls)."""
    cur = next((q for (q,) in state.get("_initiative", set())), None)
    if cur is None or not dmg_pairs:
        return
    controls = run(state, ["controls"])["controls"]
    ctrl_of = {c: p for (p, c) in controls}
    for (src, p) in sorted(dmg_pairs):
        if p != cur:                                          # only damage to the initiative-holder matters
            continue
        new = ctrl_of.get(src)
        if new is not None and new != cur:                    # a creature you don't already control hit you
            _set_initiative(state, new, reason=f"{src} dealt combat damage to the initiative-holder")
            return


def _create_token(state: dict, spec: str, controller: str, n: int) -> None:
    n *= 2 ** _doubler_count(state, controller, "tokens")     # §614 Doubling Season / Parallel Lives / ...
    d = _parse_token_spec(spec)
    for _ in range(n):
        state["_tok"] = state.get("_tok", 0) + 1
        tid = f"{spec}#{state['_tok']}"
        state.setdefault("on_battlefield", set()).add((tid,))             # printed_* only; the engine
        state.setdefault("printed_control", set()).add((controller, tid)) # derives controls/has_type/creature
        state.setdefault("is_token", set()).add((tid,))                   # §111 token -> 'control a token' cond_met
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


def _slug_of(state: dict, card: str) -> str | None:
    return next((c for (i, c) in state.get("instance_of", set()) if i == card), None)


def _cost_value(spec: str) -> int:
    """Total mana value of a cost slug like '3_w_w' (generic 3 + 2 pips = 5); 'x' counts 0."""
    total = 0
    for tok in str(spec).split("_"):
        if tok.isdigit():
            total += int(tok)
        elif tok and tok != "x":
            total += 1
    return total


def _has_card_keyword(state: dict, card: str, kw: str) -> bool:
    slug = _slug_of(state, card)
    return slug is not None and (slug, kw) in state.get("card_keyword", set())


def turn_up_cost(state: dict, card: str) -> int:
    """The generic mana the controller pays to turn `card` face up: a morph/disguise card's keyword cost
    (keyword_param), else (a manifested creature) its own mana value; 0 if unknown (treated as free)."""
    slug = _slug_of(state, card)
    for kw in ("morph", "disguise"):
        p = next((pr for (s, k, pr) in state.get("keyword_param", set()) if s == slug and k == kw), None)
        if p is not None:
            return _cost_value(p)
    mv = next((n for (i, n) in state.get("mana_cost", set()) if i == card), None)
    return int(mv) if mv is not None else 0


def turn_face_up(state: dict, card: str) -> bool:
    """§708.5 turn a face-down permanent FACE UP: drop face_down(card) so the engine resolves its REAL
    characteristics again, and make it public (everyone now sees its identity). Returns False if `card`
    isn't face down. The CALLER enforces legality (creature card) and pays turn_up_cost."""
    if (card,) not in state.get("face_down", set()):
        return False
    state["face_down"].discard((card,))
    state.setdefault("revealed", set()).add((card,))         # §708 it is now public to every player
    state.setdefault("_just_turned_face_up", set()).add((card,))  # §603 'is turned face up' window (Boltbender)
    print(f"    {card} is turned face up")
    return True


def _fire_turn_face_up_triggers(state: dict) -> None:
    """§603/§708.5 fire 'when this permanent is turned face up' triggers for the permanents the driver just
    turned face up. Fed as just_turned_face_up (mirrors the just_tapped 'becomes tapped' pattern); resolve the
    pending, then clear the window so it doesn't re-fire. Called right after the turn-face-up action resolves."""
    just = state.pop("_just_turned_face_up", set())
    if not just:
        return
    state["just_turned_face_up"] = just
    _apply_effects(state, *_pending_both(state))
    state["just_turned_face_up"] = set()


def cast_face_down(state: dict, card: str, ctrl: str) -> bool:
    """§702.37 morph / §702.166 disguise — cast a card from hand FACE DOWN as a 2/2 creature for {3}: it
    enters the battlefield face down, the controller KNOWS it, opponents see only the 2/2 body (observe).
    Returns False if `card` isn't in ctrl's hand. (Disguise's ward {2} isn't mechanically modelled.)"""
    if (ctrl, card) not in state.get("in_hand", set()):
        return False
    _spend_ability_mana(state, ctrl, 3)                       # §702.37e the face-down cast costs {3}
    state["in_hand"].discard((ctrl, card))
    state.setdefault("on_battlefield", set()).add((card,))
    state.setdefault("printed_control", set()).add((ctrl, card))
    state.setdefault("face_down", set()).add((card,))         # §708.2 -> engine 2/2 colorless body
    state.setdefault("known", set()).add((ctrl, card))        # the controller knows what it cast
    state.setdefault("_sick", set()).add((card,))
    print(f"    {ctrl} casts {card} face down (2/2)")
    return True


def foretell(state: dict, card: str, ctrl: str) -> bool:
    """§702.143 foretell — exile a card from hand FACE DOWN for {2} (cast later for its foretell cost). Its
    identity is hidden in exile from opponents but KNOWN to its owner (observe). False if not in hand."""
    if (ctrl, card) not in state.get("in_hand", set()):
        return False
    _spend_ability_mana(state, ctrl, 2)                       # §702.143a foretell costs {2}
    state["in_hand"].discard((ctrl, card))
    state.setdefault("exile", set()).add((card,))
    state.setdefault("face_down", set()).add((card,))
    state.setdefault("known", set()).add((ctrl, card))
    state.setdefault("_foretold", set()).add((card,))        # marker: foretold (castable from exile)
    print(f"    {ctrl} foretells {card} (face down in exile)")
    return True


def cycling_cost(state: dict, card: str) -> int | None:
    """§702.29 the plain-mana cost to cycle `card` (the instance's real card carries cycling_card(slug, cost)),
    or None if it isn't a cycling card. Resolved through instance_of like the other card-level reads."""
    inst = {i: c for (i, c) in state.get("instance_of", set())}
    slug = inst.get(card)
    return next((n for (s, n) in state.get("cycling_card", set()) if s == slug), None)


def typecycling_predicate(state: dict, card: str) -> str | None:
    """§702.29 — if `card`'s cycling is a TYPECYCLING variant (Plainscycling/Basic landcycling/Slivercycling),
    the §701.18 search predicate for the card it fetches ('any_land' / 'subtype:plains' / 'csub:sliver'), else
    None (plain cycling — the effect is a DRAW). Carried driver-side as typecycling_card(slug, pred), folded
    from the parse facts by the bridge (read by slug through instance_of, like cycling_card)."""
    inst = {i: c for (i, c) in state.get("instance_of", set())}
    slug = inst.get(card)
    return next((p for (s, p) in state.get("typecycling_card", set()) if s == slug), None)


def cycle(state: dict, card: str, ctrl: str) -> bool:
    """§702.28/29 CYCLING — a from-hand activated ability: '{cost}, Discard this card: Draw a card.' Pay the
    cycling mana, discard the card from hand to the graveyard (§701.8 / _discard_zone), open the §603
    just_cycled window so 'whenever you cycle a card' payoffs fire (Renewed Faith, Decree of Justice), then
    the EFFECT: a plain cycle DRAWS a card; a §702.29 TYPECYCLING (Plains/Basic land/Sliver…cycling) instead
    SEARCHES the library for a matching [type] card, puts it into HAND, and shuffles (reusing the §701.18 tutor
    machinery in effect_handlers/library). Both fire their respective §603 watchers (draw / search). False if
    the card isn't in ctrl's hand or has no plain-mana cycling cost."""
    if (ctrl, card) not in state.get("in_hand", set()):
        return False
    cost = cycling_cost(state, card)
    if cost is None:
        return False
    if cost:
        _spend_ability_mana(state, ctrl, cost)               # §702.29a pay the cycling mana cost
    state["in_hand"].discard((ctrl, card))                   # §118 'Discard this card' is part of the cost
    state.setdefault(_discard_zone(state, ctrl), set()).add((card,))
    print(f"    {ctrl} cycles {card}")
    _fire_cycle_triggers(state, ctrl)                        # §603 'whenever you cycle a card' payoffs
    pred = typecycling_predicate(state, card)
    if pred is not None:
        from effect_handlers import library as _lib          # §702.29 SEARCH for 'a [type] card' -> HAND, then shuffle
        found = _lib._select_card(state, ctrl, pred)         # §701.18 tutor: pull the matching card OUT (fires search triggers)
        if found is not None:
            state.setdefault("in_hand", set()).add((ctrl, found))
            print(f"    {ctrl} searches and puts {found} into hand")
        else:
            print(f"    {ctrl} searches but finds no matching card")   # §701.18c legal fail-to-find
        _shuffle_library(state, ctrl)                        # §701.18 'then shuffle' (the fetched card is already out)
    else:
        _draw(state, ctrl)                                   # §702.29a plain cycling: draw a card (fires draw triggers)
    return True


def _fire_cycle_triggers(state: dict, p: str) -> None:
    """§603 fire 'whenever ~ cycles a card' triggers for p's just-completed cycle (mirrors _fire_draw_triggers):
    open the driver-fed just_cycled window (-> ev_cycle), resolve the new pending, then clear. A re-entrancy
    guard caps the chain so a pathological cycle->cycle loop can't run away."""
    if state.get("_in_cycle_trigger", 0) >= 8:               # depth cap — natural cycle chains are short
        return
    state["_in_cycle_trigger"] = state.get("_in_cycle_trigger", 0) + 1
    try:
        state["just_cycled"] = {(p,)}
        _apply_effects(state, *_pending_both(state))
    finally:
        state["just_cycled"] = set()
        state["_in_cycle_trigger"] -= 1


def _transform(state: dict, obj: str, ctrl: str) -> None:
    """§712 transform `obj` into its back face: flip instance_of(obj) to the back slug the bridge linked via
    transform_target, re-materialize its printed identity from the back's card_* facts (the engine then derives
    the back-face permanent — for Ral, a planeswalker with its loyalty abilities), and enter it as a NEW object
    under `ctrl` — summoning sick, front-face counters cleared, with §306.5b starting loyalty: the back's
    printed loyalty PLUS one per instant/sorcery cast this turn (Ral, Leyline Prodigy's enters-with rider).
    Fires the §603 enters-the-battlefield window. A no-op if obj has no transform target."""
    back = next((bs for (o, bs) in state.get("transform_target", set()) if o == obj), None)
    if back is None:
        return
    state["instance_of"] = {(o, s) for (o, s) in state.get("instance_of", set()) if o != obj} | {(obj, back)}
    bt = {t for (s, t) in state.get("card_type", set()) if s == back}
    bsub = {st for (s, st) in state.get("card_subtype", set()) if s == back}
    bcol = {co for (s, co) in state.get("card_color", set()) if s == back}
    for rel, vals in (("printed_type", bt), ("printed_subtype", bsub), ("printed_color", bcol)):
        state[rel] = {(o, v) for (o, v) in state.get(rel, set()) if o != obj} | {(obj, v) for v in vals}
    for rel in ("printed_power", "printed_toughness"):         # a planeswalker has no P/T
        state[rel] = {(o, v) for (o, v) in state.get(rel, set()) if o != obj}
    state["counter"] = {(o, k, c) for (o, k, c) in state.get("counter", set()) if o != obj}   # new object: fresh
    base = next((n for (s, n) in state.get("card_loyalty", set()) if s == back), 0)
    loy = base + (state.get("_is_cast_count", 0) if "planeswalker" in bt else 0)   # §306.5b ETB loyalty scaling
    if "planeswalker" in bt and loy > 0:
        state.setdefault("counter", set()).add((obj, "loyalty", loy))
    state.setdefault("_sick", set()).add((obj,))               # §302.6 a new object is summoning sick
    state.setdefault("just_entered", set()).add((obj,))        # §603 enters-the-battlefield window
    print(f"    {obj} transforms into {back}"
          + (f" (planeswalker, loyalty {loy})" if "planeswalker" in bt else ""))
    _apply_effects(state, *_pending_both(state))  # §603 fire 'when ~ enters' triggers
    state["just_entered"].discard((obj,))


def _bump_counter(state: dict, obj: str, kind: str, n: int, placed_event: bool = True) -> None:
    if n > 0 and kind == "p1p1":                              # §614 counter doublers (Doubling Season / Primal Vigor)
        owner = next((p for (p, c) in state.get("printed_control", set()) if c == obj), None)
        if owner:
            n *= 2 ** _doubler_count(state, owner, "counters")
    cur = next((c for (o, k, c) in state.get("counter", set()) if o == obj and k == kind), 0)
    state.setdefault("counter", set()).discard((obj, kind, cur))
    state["counter"].add((obj, kind, cur + n))
    # §603/§122 arm the '+1/+1 counter(s) put on ~' window for the counter-placement triggers (Lonis,
    # Sharktocrab, Shalai and Hallar, …). GATED to the +1/+1 kind with n > 0 (caution (b): only +1/+1
    # counters; a removal / -1/-1 / loyalty bump must NOT fire it). KEYED BY THE CREATURE so 'one or more'
    # fires exactly ONCE even if several counters land at once (caution (a): the set dedupes per placement).
    # placed_event=False suppresses the signal for §614.13 'enters the battlefield WITH counters' — those are
    # a REPLACEMENT as the permanent enters, not a 'counter is put on' event, so they don't trigger (caution).
    if placed_event and kind == "p1p1" and n > 0:
        state.setdefault("_just_p1p1_placed", set()).add((obj,))


def _discard_zone(state: dict, p: str) -> str:
    """§701.8 where p's discarded cards go: the graveyard, OR 'exile' if p controls a 'whenever you discard a
    card, exile that card from your graveyard' source (Necropotence) — modeled as a discard-to-exile replacement."""
    mine = {c for (pp, c) in state.get("printed_control", set()) if pp == p}
    bf = {c for (c,) in state.get("on_battlefield", set())}
    return "exile" if any(s in mine and s in bf for (s,) in state.get("discard_exile_source", set())) else "graveyard"


def _apply_effects(state: dict, pending: set, pending_dyn: set | None = None) -> None:
    """Apply the effects of triggered abilities the engine fired (§603 -> §608 resolution).
    Player targets: each_opponent -> all other players; controller/self -> the controller.
    STRUCTURAL #3 — `pending_dyn` (optional): the DYNAMIC ('for each') trigger effects to resolve alongside
    (rows (a,eff,base,tag,tgt,src,ctrl)); each is scaled to a live count and routed back through this path.
    Trigger chokepoints pass the engine's current pending_dyn; diff sites pass the diff; spells pass None."""
    if pending_dyn:
        _apply_dyn(state, pending_dyn)
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
                    state.setdefault(_discard_zone(state, p), set()).add((card,))
                if k:
                    print(f"    trigger {a}: {p} discards {k}{' at random' if at_random else ''}")
        elif eff == "add_counter":                           # tgt = counter kind (p1p1/m1m1), on the source
            _bump_counter(state, src, tgt, n)
            print(f"    trigger {a}: {src} gets {n} {tgt} counter(s)")
        elif eff == "add_counter_attached":                  # §301/§303 tgt = kind; on the host this Aura/Equip
            host = next((h for (au, h) in state.get("attached_to", set()) if au == src), None)
            if host is not None:                             # is attached to (choice-free; no host -> no-op)
                _bump_counter(state, host, tgt, n)
                print(f"    {a}: {host} (attached to {src}) gets {n} {tgt} counter(s)")
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
        elif eff == "modal_trigger":                         # §700.2 a MODAL triggered ability (Hullbreaker Horror)
            _resolve_modal_trigger(state, a, n, tgt, src, ctrl)
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
            if _consume_regen_shield(state, c):              # §701.15 a regen shield replaces the destruction
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
            _tap(state, c)                                    # §701.20 tap (records just_tapped)
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
    _loyalty_sba(state)                                       # §704.5i a planeswalker with 0 loyalty -> graveyard


def _loyalty_sba(state: dict) -> None:
    """§704.5i — a planeswalker with 0 (or less) loyalty is put into its owner's graveyard. Loyalty is a
    `loyalty` counter (set when the planeswalker enters / activates a loyalty ability); a planeswalker with NO
    loyalty counter row is left alone (defensive — it was never loyalty-tracked, so we don't fabricate a death)."""
    bf = state.get("on_battlefield", set())
    ptype = state.get("printed_type", set())
    dead = [o for (o, k, c) in state.get("counter", set())
            if k == "loyalty" and c <= 0 and (o,) in bf and (o, "planeswalker") in ptype]
    for o in sorted(dead):
        bf.discard((o,))
        state.setdefault("graveyard", set()).add((o,))
        state["counter"] = {r for r in state.get("counter", set()) if r[0] != o}
        print(f"    {o} has 0 loyalty -> graveyard (§704.5i)")


# Verbs that HURT the targeted creature -> aim at the opponent's board; the rest BENEFIT it -> aim own.
_HARMFUL_TARGET = {"destroy", "exile", "tap", "return_to_hand", "switchpt"}


def _apply_target_verb(state: dict, a: str, kind: str, verb: str, payload: str, tgt: str,
                       ctrl: str, indestructible: set, owner_of: dict) -> None:
    """Apply one resolved single-target creature verb to the already-chosen `tgt`. Shared by §603
    triggered abilities (kind='trigger') and §608 instant/sorcery resolution (kind='spell'). A P/T
    pump or keyword grant is an until-EOT continuous effect; destroy/exile/return/tap/untap are §701
    one-shot zone/state moves. `kind` only flavors the log line."""
    # §700.x COMMIT A CRIME — this targeted verb is a crime iff the targeted object belongs to an OPPONENT
    # of the source's controller (targeting your own permanent is never a crime). Noted BEFORE the verb so
    # the still-present target's controller is read. _note_crime fires the 'whenever you commit a crime'
    # triggers exactly once per (controller, source). Beneficial verbs (a pump/grant on your own creature)
    # have owner_of[tgt] == ctrl and are correctly skipped; a buff aimed at an opponent's creature IS a crime.
    _note_crime(state, ctrl, owner_of.get(tgt), a)
    state.setdefault("_spell_pick", {})[a] = tgt              # §607.2 remember the chosen target so a 'that
    #                                                          creature' rider (Team Tactics) can reference it.
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
    elif verb in ("setpt", "setpt_eot"):                     # §613 layer 7b 'becomes a P/T creature' — SET base P/T
        # eff_set_power/toughness feed the engine's set_power -> base_power (§613 7b); counters (7c) and pumps
        # still layer on top. 'setpt' is a PERMANENT set (Diminish 1/1, Quandrix Charm 5/5 — no duration);
        # 'setpt_eot' wears off at cleanup (Humble/Ovinize 0/1 'until end of turn'). The engine doesn't model
        # an Aura-style attachment, so this is the one-shot/EOT target case only — the source's own type/color
        # are untouched (a 'becomes a P/T creature' on a creature target sets only the P/T, choice-free).
        dp, dt = (int(x) for x in payload.split("/"))
        eid = f"{a}__setpt__{tgt}"
        state.setdefault("eff_set_power", set()).add((eid, tgt, dp, 1))
        state.setdefault("eff_set_toughness", set()).add((eid, tgt, dt, 1))
        if verb == "setpt_eot":
            state.setdefault("until_eot", set()).add((eid,))
        suffix = " until end of turn" if verb == "setpt_eot" else ""
        print(f"    {kind} {a}: {tgt} becomes a {dp}/{dt} creature{suffix}")
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
        if _consume_regen_shield(state, tgt):                # §701.15 a regen shield replaces the destruction
            return
        state["on_battlefield"].discard((tgt,))
        state.setdefault("graveyard", set()).add((tgt,))
        print(f"    {kind} {a}: destroys target {tgt} -> graveyard")
    elif verb == "regenerate":                               # §701.15 'Regenerate target creature' (instant)
        state.setdefault("_regen_shield", set()).add((tgt,))
        print(f"    {kind} {a}: {tgt} gains a regeneration shield (§701.15)")
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
            _tap(state, tgt)
            print(f"    {kind} {a}: taps target {tgt}")
    elif verb == "untap":
        if (tgt,) in state.get("tapped", set()):
            state["tapped"].discard((tgt,))
            print(f"    {kind} {a}: untaps target {tgt}")
    elif verb == "cant_be_blocked":                          # §509.1b 'target creature can't be blocked' (Aqueous Form)
        # write the engine input cant_be_blocked(tgt) -> illegal_block(B,tgt) forbids any blocker (honored by
        # the engine combat AND env._legal_block_pairs). Turn-scoped: end-of-turn cleanup clears the relation.
        state.setdefault("cant_be_blocked", set()).add((tgt,))
        print(f"    {kind} {a}: {tgt} can't be blocked this turn")
    elif verb == "cant_block":                               # §509.1b 'target creature can't block' (Spider-Man,
        # Web-Spinner) — add the specific creature to _cant_block, which declare_blockers excludes from the
        # legal blockers (it already drops any (b,) in _cant_block). Turn-scoped: cleared at end of turn.
        state.setdefault("_cant_block", set()).add((tgt,))
        print(f"    {kind} {a}: {tgt} can't block this turn")


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
    "acl": ("artifact", "creature", "land"),                           # Twitch: artifact/creature/land tapper
    "land": ("land",),                                                 # Sundering Eruption: destroy target land
    "aenl": ("artifact", "enchantment", "nonbasic_land"),              # Boseiju: artifact/ench/NONBASIC land
    "artifact_creature": ("artifact", "creature"),                     # §115 'target artifact or creature' (Abrade-style)
    "artifact_land": ("artifact", "land"),                             # 'target artifact or land'
    "creature_land": ("creature", "land"),                             # 'target creature or land'
    "artifact_enchantment_land": ("artifact", "enchantment", "land"),  # 'target artifact, enchantment, or land'
    "nonbasic_land": ("nonbasic_land",),                               # §205.4 'target nonbasic land' (Wasteland-style)
    "noncreature": ("noncreature",), "nonland": ("nonland",), "any": ("any",),
}
_PERM_COLORS = {"white", "blue", "black", "red", "green"}    # §105 a COLOR target class (Pyroblast/REB: a blue permanent)


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
    color = body if body in _PERM_COLORS else None            # §105 a color-only filter (Pyroblast: a blue permanent)
    want = _PERM_FILTER.get(body, ())
    on_bf = sorted(c for (c,) in state.get("on_battlefield", set()))
    ptype = state.get("printed_type", set())
    pcolor = state.get("printed_color", set())
    basic = {o for (o, sup) in state.get("has_supertype", set()) if sup == "basic"}   # §205.4 basic-land split
    mine = {c for (p, c) in state.get("printed_control", set()) if p == ctrl}

    def matches(c: str) -> bool:
        if own and c not in mine:
            return False
        if opp and c in mine:                                 # an opponent-controlled restriction excludes mine
            return False
        if color is not None:                                 # any permanent of that color (§105)
            return (c, color) in pcolor
        types = {t for (o, t) in ptype if o == c}
        if c in creatures:
            types.add("creature")
        if want == ("any",):
            return True
        if want == ("nonland",):
            return "land" not in types
        if want == ("noncreature",):
            return "creature" not in types
        # §205.4 a NONBASIC land matches the 'nonbasic_land' token when it's a land without the Basic supertype.
        if "land" in types and "nonbasic_land" in want and c not in basic:
            return True
        return any(t in types for t in want if t != "nonbasic_land")

    return [c for c in on_bf if matches(c)]


# §115 a restricted legal-target class carries FILTER tokens after the base, joined by '#': e.g.
# 'any#attacking', 'any#powge:4', 'opponent#tapped', 'any#notcolor:black#nottype:artifact'. The base
# (any / you_control / opponent / perm_<...>) selects the candidate pool exactly as before; each filter
# then NARROWS it (§115.4 — a candidate the restriction excludes is not a legal target). Narrowing is
# always faithful: we only ever shrink the legal set, never add an illegal target. The bridge encodes
# these in target_class (build_engine copies them into the engine's target_class table); the class is
# opaque to the engine and decoded only here.
def _target_filter_pred(state: dict, filt: str, powers: dict, creatures: set):
    """Return a predicate creature_id -> bool for one filter token, plus a flag for whether it needs the
    candidate to be a creature (combat/P-T/keyword filters do; type/color ones apply to any permanent)."""
    name, _, arg = filt.partition(":")
    if name == "attacking":
        atk = {a for (a, _d) in state.get("attacks", set())}
        return lambda c: c in atk
    if name == "blocking":
        blk = {b for (b, _a) in state.get("blocks", set())}
        return lambda c: c in blk
    if name == "atkorblk":
        atk = {a for (a, _d) in state.get("attacks", set())} | {b for (b, _a) in state.get("blocks", set())}
        return lambda c: c in atk
    if name == "tapped":
        tap = {c for (c,) in state.get("tapped", set())}
        return lambda c: c in tap
    if name == "untapped":
        tap = {c for (c,) in state.get("tapped", set())}
        return lambda c: c not in tap
    if name in ("powge", "powle"):
        n = int(arg)
        return (lambda c: powers.get(c, 0) >= n) if name == "powge" else (lambda c: powers.get(c, 0) <= n)
    if name in ("touge", "toule"):
        n = int(arg)
        tough = {c: int(x) for (c, x) in run(state, ["eff_toughness"])["eff_toughness"]}
        return (lambda c: tough.get(c, 0) >= n) if name == "touge" else (lambda c: tough.get(c, 0) <= n)
    if name in ("mvge", "mvle"):
        n = int(arg)
        mv = {s: int(v) for (s, v) in state.get("mana_cost", set())}
        return (lambda c: mv.get(c, 0) >= n) if name == "mvge" else (lambda c: mv.get(c, 0) <= n)
    if name == "kw":
        have = {c for (c, k) in run(state, ["has_keyword"])["has_keyword"] if k == arg}
        return lambda c: c in have
    if name == "color":
        col = {c for (c, x) in state.get("printed_color", set()) if x == arg}
        return lambda c: c in col
    if name == "notcolor":
        col = {c for (c, x) in state.get("printed_color", set()) if x == arg}
        return lambda c: c not in col
    if name == "nottype":
        typ = {c for (c, t) in state.get("printed_type", set()) if t == arg}
        return lambda c: c not in typ
    if name == "nonlegendary":
        leg = {c for (c, s) in state.get("has_supertype", set()) if s == "legendary"}
        return lambda c: c not in leg
    return lambda c: True                                    # an unrecognized filter is conservatively a no-op pass


def _pick_target(state: dict, ctrl: str, cls: str, verb: str, payload: str,
                 controls: set, powers: dict, creatures: set) -> str | None:
    """§601.2c choose a legal target for a single-target effect. `cls` constrains the legal set
    (any / you_control / opponent for creatures; perm_<filter> for non-creature permanents; plus optional
    '#'-joined restriction filters — see _target_filter_pred); within it, a harmful verb (removal/tap/
    bounce, or a P/T shrink) picks the strongest enemy and a beneficial one the strongest own permanent."""
    base, *filters = cls.split("#")                          # §115 strip any restriction filters off the base class
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in controls if p == ctrl}
    if base.startswith("perm_"):                             # §115 non-creature permanent target (Abrade, bounce)
        cands = _perm_candidates(state, base, creatures, ctrl)
    else:
        cands = [c for c in creatures if c in on_bf]
        if base == "you_control":
            cands = [c for c in cands if c in mine]
        elif base == "opponent":
            cands = [c for c in cands if c not in mine]
    for filt in filters:                                     # §115.4 narrow to candidates meeting each restriction
        pred = _target_filter_pred(state, filt, powers, creatures)
        cands = [c for c in cands if pred(c)]
    if not cands:
        return None
    harmful = verb in _HARMFUL_TARGET
    if verb == "modify_pt":                                   # a net-negative pump is removal-flavored
        dp, dt = (int(x) for x in payload.split("/"))
        harmful = (dp + dt) < 0
    elif verb == "counter":                                  # a -1/-1 counter is removal; +1/+1 is a buff
        harmful = payload.startswith("m1m1")
    elif verb in ("setpt", "setpt_eot"):                     # §613 'becomes a P/T creature' — a SET base P/T.
        # Heuristic, choice-free: a SMALL set P/T (Diminish 1/1, Humble/Ovinize 0/1) is removal-flavored ->
        # aim at the strongest enemy to neuter it; a LARGE set (Quandrix Charm 5/5, Gigantomancer 7/7) is a
        # buff -> aim at the controller's strongest. (you_control-classed clauses already restrict to own.)
        dp, dt = (int(x) for x in payload.split("/"))
        harmful = (dp + dt) <= 3
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
    if (obj,) in state.get("_sacrificing", set()):
        return                                               # §603.10a re-entrancy guard: a 'when sacrificed' trigger
    state.setdefault("_sacrificing", set()).add((obj,))      # re-sacrificing the SAME object would recurse forever
    print(f"    {obj} is sacrificed")
    state["sacrificed"] = {(obj,)}
    _apply_effects(state, *_pending_both(state))
    state["sacrificed"] = set()
    state["on_battlefield"].discard((obj,))
    state.setdefault("graveyard", set()).add((obj,))
    state["_sacrificing"].discard((obj,))


def _sac_candidates(state: dict, p: str, kind: str, source: str | None = None) -> list[str]:
    """§602.5 the permanents player p controls that satisfy a 'Sacrifice a <X>' activation cost `kind`:
      - a type word (creature/artifact/land/enchantment/planeswalker) — controls + that type (creature
        is the engine-DERIVED creature so animated lands/tokens count);
      - 'another_creature'  — a creature p controls OTHER than the source ('Sacrifice another creature');
      - 'subtype:<x>'       — a permanent p controls with that printed subtype (Saproling/Goblin/Food).
      - 'permanent'         — any permanent p controls.
    Returns the sorted candidate ids (empty if none — the ability is then unaffordable)."""
    out = run(state, ["controls", "creature"])
    mine = {c for (pp, c) in out["controls"] if pp == p}
    creatures = {c for (c,) in out["creature"]}
    if kind == "another_creature":
        return sorted(c for c in mine if c in creatures and c != source)
    if kind.startswith("subtype:"):
        sub = kind.split(":", 1)[1]
        psub = state.get("printed_subtype", set())
        return sorted(c for c in mine if (c, sub) in psub)
    if kind == "permanent":
        return sorted(mine)
    if kind == "creature":
        return sorted(c for c in mine if c in creatures)
    ptype = state.get("printed_type", set())                     # a printed type (artifact/land/enchantment/planeswalker)
    return sorted(c for c in mine if (c, kind) in ptype)


def _sac_default(state: dict, cands: list, source: str | None) -> str:
    """The greedy sacrifice victim for a 'Sacrifice a <X>' cost: prefer a permanent OTHER than the ability's
    source (don't blow up the engine of the ability unless it's the only option), then the lowest power
    (sacrifice the least valuable body). A policy/search overrides via the _choose seam."""
    powers = {c: int(n) for (c, n) in run(state, ["power"])["power"]}
    return min(cands, key=lambda c: (c == source, powers.get(c, 0), c))


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
    # §509.1a a creature with a 'can't block' restriction (e.g. a suspected creature) is never a legal blocker.
    cant_block = state.get("_cant_block", set())
    blockers = [b for b in _creatures_of(state, opp)
                if (b,) not in state.get("tapped", set()) and (b,) not in cant_block]
    if ("without_flying",) in cant_block:                       # §509.1b 'creatures without flying can't block'
        flyers = {c for (c, k) in run(state, ["has_keyword"])["has_keyword"] if k == "flying"}
        blockers = [b for b in blockers if b in flyers]
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
    kt = state.get("_known_top", {}).get(p)                  # §708: the drawn top leaves p's known-top window
    if kt and kt[0] == card:
        kt.pop(0)
    elif kt and card in kt:
        kt.remove(card)
    print(f"    {p} draws {card}")
    _fire_draw_triggers(state, p)                            # §603 'whenever a player draws a card' triggers
    return True


def _fire_draw_triggers(state: dict, p: str) -> None:
    """§603 fire 'whenever ~ draws a card' triggers for p's just-completed draw, gated on the per-(player,turn)
    draw ORDINAL (draw_ord — 'their second card each turn', Faerie Mastermind). Fed as just_drew + draw_ord
    (mirrors the cast-ordinal window); resolve the pending, then clear. A re-entrancy guard caps the natural
    chain (a draw trigger that itself draws) so a pathological loop can't run away."""
    by = state.setdefault("_draw_by", {})
    by[p] = n = by.get(p, 0) + 1
    if state.get("_in_draw_trigger", 0) >= 8:                # depth cap — natural draw chains are short
        return
    state["_in_draw_trigger"] = state.get("_in_draw_trigger", 0) + 1
    try:
        state["just_drew"] = {(p,)}
        state["draw_ord"] = {(p, n)}
        _apply_effects(state, *_pending_both(state))
    finally:
        state["just_drew"] = set()
        state["draw_ord"] = set()
        state["_in_draw_trigger"] -= 1


def _fire_search_triggers(state: dict, searcher: str) -> None:
    """§701.18 fire 'whenever an opponent searches their library' triggers (Wan Shi Tong) for a library
    search just performed by `searcher`. Driver-fed ev_search_library opens the window; the engine fires the
    watchers controlled by the searcher's OPPONENTS. Apply only the NEW pending the window produces (diff vs.
    pre-window), so unrelated standing triggers aren't re-applied; a re-entrancy guard caps the chain (a
    search trigger that itself searches). Fired once per `search your library` instruction (a multi-card
    search counts once — see callers)."""
    if state.get("_in_search_trigger", 0) >= 8:
        return
    before, before_dyn = _pending_both(state)
    state["_in_search_trigger"] = state.get("_in_search_trigger", 0) + 1
    try:
        state["ev_search_library"] = {(searcher,)}
        now, now_dyn = _pending_both(state)
        new, new_dyn = now - before, now_dyn - before_dyn
        state["ev_search_library"] = set()                   # CLOSE the window before applying — the trigger's own
        _apply_effects(state, new, new_dyn)                  # draw re-runs pending and would otherwise re-fire it
    finally:
        state["ev_search_library"] = set()
        state["_in_search_trigger"] -= 1


def _note_crime(state: dict, ctrl: str, victim_owner: str | None, source: str) -> None:
    """§700.x — a player COMMITS A CRIME when a spell/ability/action they control TARGETS an opponent, a
    permanent/spell/ability an opponent controls, or a card in an opponent's graveyard. The driver calls this
    from the single-target resolution chokepoints once a chosen target's CONTROLLER/OWNER is known: `ctrl` is
    the player who controls the targeting source, `victim_owner` the opponent who controls/owns the targeted
    object (or the opponent player themselves, for a player-target effect), and `source` the spell/ability id
    doing the targeting. A crime fires ONLY when victim_owner is a DIFFERENT player than ctrl (targeting your
    own object is not a crime). De-duped per (ctrl, source) so a multi-target / multi-effect source that hits
    an opponent more than once fires the crime ONCE, not once per target. The trigger loop runs in
    _fire_crime_triggers.

    FAITHFULNESS / MOMENT: the rules commit the crime when targets are CHOSEN (at announcement, as the
    spell/ability goes on the stack). This engine does not expose chosen targets at cast/activate time —
    targets are picked transiently at RESOLUTION (see _pick_target / _apply_target_verb / _apply_damage). So
    the crime is detected at resolution, the first point the targeted object's controller is known. For the
    'whenever you commit a crime' payoffs this models (counters, draw, drain — none care about the intervening
    announce->resolve window), the observable outcome matches; the only divergence is the exact timing of the
    crime relative to an opponent's response window, which this engine does not simulate at target granularity."""
    if victim_owner is None or victim_owner == ctrl:
        return                                                # targeting your own object / no opponent owner — not a crime
    seen = state.setdefault("_crime_noted", set())
    key = (ctrl, source)
    if key in seen:                                           # once per crime (per source), not once per target
        return
    seen.add(key)
    _fire_crime_triggers(state, ctrl)


def _fire_crime_triggers(state: dict, criminal: str) -> None:
    """§700.x fire 'whenever you commit a crime' triggers (MKM — Deepmuck Desperado, Marauding Sphinx, …) for
    a crime just committed by `criminal` (their spell/ability targeted an opponent's stuff — see _note_crime).
    Driver-fed committed_crime opens the window; the engine fires the watchers CONTROLLED BY the criminal
    (you_commit_a_crime) and that player's opponents (an_opponent_commits_a_crime / a_player_commits_a_crime).
    Apply only the NEW pending the window produces (diff vs. pre-window) so unrelated standing triggers aren't
    re-applied; a re-entrancy guard caps a crime-trigger that itself commits a crime."""
    if state.get("_in_crime_trigger", 0) >= 8:
        return
    before, before_dyn = _pending_both(state)
    state["_in_crime_trigger"] = state.get("_in_crime_trigger", 0) + 1
    try:
        state["committed_crime"] = {(criminal,)}
        now, now_dyn = _pending_both(state)
        new, new_dyn = now - before, now_dyn - before_dyn
        state["committed_crime"] = set()                     # CLOSE the window before applying the new triggers
        _apply_effects(state, new, new_dyn)
    finally:
        state["committed_crime"] = set()
        state["_in_crime_trigger"] -= 1


def _apply_outputs(state: dict, out: dict, ap: str) -> str | None:
    """Apply everything the engine derived for this step, in order; return a loser if
    one is decided this step (else None). This is the whole 'driver acts on engine
    output' surface — every consequence the engine flags is handled here."""
    # derived relations are sets; iterate them sorted so behavior is canonical regardless of the
    # backend's row order (the souffle interpreter and the compiled binary emit sets in different orders).
    no_untap = _no_untap_set(state)                              # permanents that 'don't untap' (verb lock + static EDB)
    for (c,) in sorted(out["to_untap"]):                         # §502.3 untap
        if (c,) in no_untap:                                      # 'doesn't untap during its controller's untap step'
            print(f"    {c} doesn't untap (stays tapped)"); continue
        state["tapped"].discard((c,)); print(f"    {ap} untaps {c}")
    if ("untap",) in state.get("current_step", set()):           # §502 Seedborn Muse untaps off-turn
        _seedborn_untap(state, ap)
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
        # §701.15 REGENERATION — a battlefield->graveyard destruction may be replaced by a regen shield
        # (tap + remove from combat, NOT destroyed). Consult BEFORE leaving the battlefield, like cant_be_
        # destroyed gates `dies` in the engine; if the shield fires, the permanent stays put.
        if (frm, to) == ("battlefield", "graveyard") and _consume_regen_shield(state, c):
            continue
        state.setdefault(ZONE[frm], set()).discard((c,))
        # §903.9 / §704.5 commander replacement: a commander headed to graveyard/exile (or hand/library)
        # MAY instead go to the command zone (a _choose decision); if taken, skip the normal destination.
        if _is_commander(state, c) and to in ("graveyard", "exile", "hand", "library") \
                and _commander_replacement(state, c, to):
            continue
        if to == "graveyard" and _gy_replaced(state, c):     # §614 Rest in Peace / Leyline of the Void: exile instead
            state.setdefault("exile", set()).add((c,))
            print(f"    {c} would be put into a graveyard — a §614 replacement exiles it instead")
            continue
        state.setdefault(ZONE[to], set()).add((c,))
        verb = "dies" if (frm, to) == ("battlefield", "graveyard") else f"moves {frm}"
        print(f"    {c} {verb} -> {to}")
    for (p, n) in sorted(out["player_damage"]):                  # §510.2 persist combat damage
        print(f"    {p} takes {n} -> {_adjust_life(state, p, -int(n))} life")
        state.setdefault("_combat_damaged", set()).add((p,))     # §510 players dealt combat damage THIS TURN (Tymna)
    _steal_monarch_on_combat(state, out.get("ev_combat_dmg_player", set()))   # §720.5 monarch steal
    _steal_initiative_on_combat(state, out.get("ev_combat_dmg_player", set()))  # initiative steal on combat damage
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


def _source_unit_list(c: str, s_fixed: set, s_wild: set) -> list:
    """The mana UNITS a precise (lexed) source `c` taps for — concrete colors and wildcard/bundle descriptors
    from its source_produces / source_wildcard rows (§106). Shared by the normal and alt-cost source loops."""
    units: list = []
    for (t, col, amt) in s_fixed:
        if t == c:
            units += [col] * int(amt)
    for (t, kind, amt) in s_wild:
        if t == c:
            if kind in _SAME_COLOR_KINDS and int(amt) > 1:
                # §106 'add N mana of any ONE color' (Black Lotus / Lion's Eye Diamond): a bundle — all N
                # share one chosen color, NOT N independent wildcards (which would fabricate impossible
                # multi-color mana). A ('one', colorset, n) unit the pool resolves to one color.
                units.append(("one", _wildcard_set(kind), int(amt)))
            else:
                units += [_wildcard_set(kind)] * int(amt)     # independent wildcard mana (any color)
    return units


def _source_cost_generic(c: str, s_cost: set) -> int:
    return next((int(g) for (t, g, _ts) in s_cost if t == c), 0)


def _source_taps(c: str, s_cost: set) -> bool:
    return next((bool(ts) for (t, _g, ts) in s_cost if t == c), True)


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

    # §605 ALT-COST mana sources (Treasonous Ogre 'Pay 3 life', Spirit Guides 'Exile ~ from hand', Lion's Eye
    # Diamond 'Discard hand, Sacrifice') — handled in a dedicated block below (they pay a SPECIAL cost, not a
    # tap), so exclude their tids from the normal battlefield loop here.
    special_cost = state.get("source_special_cost", set())
    special_tids = {t for (t, _k, _a) in special_cost}

    # non-land sources controlled by ap and untapped. §302.6 summoning sickness only blocks a CREATURE's
    # {T} mana ability (a dork that entered this turn) — a mana ROCK (artifact) taps the turn it enters.
    is_creature = state.get("printed_type", set())
    # §106 DYNAMIC-amount sources (Vivi: 'Add X mana … where X is its power'): yield `power` wildcard mana
    # of the source's dynamic colors, read live. Computed once (the power query) only if such a source exists.
    dyn_power = {t for (t,) in state.get("source_dyn_power", set())}
    dyn_colors: dict = {}
    for (t, col) in state.get("source_dyn_color", set()):
        dyn_colors.setdefault(t, set()).add(col)
    live_power = {c: int(n) for (c, n) in run(state, ["power"])["power"]} if dyn_power else {}
    rest = sorted(c for (c,) in bf if (ap, c) in ctrl and (c,) not in tapped and c not in special_tids
                  and (c, "land") not in state.get("printed_type", set())
                  and not ((c, "creature") in is_creature and (c,) in sick)
                  and (c in precise or c in dyn_power or (c,) in state.get("mana_source", set())))
    for c in rest:
        if c in dyn_power:                                   # §106 X = the source's power, in its dynamic colors
            n = live_power.get(c, 0)
            cols = frozenset(dyn_colors.get(c)) if dyn_colors.get(c) else ANY
            yield (c, [cols] * n, _source_cost_generic(c, s_cost), _source_taps(c, s_cost))
        elif c in precise:
            yield (c, _source_unit_list(c, s_fixed, s_wild), _source_cost_generic(c, s_cost), _source_taps(c, s_cost))
        else:                                                # legacy un-lexed dork: one colorless mana (§605)
            yield (c, ["colorless"], 0, True)

    # §605 ALT-COST mana sources: a from-hand source (Spirit Guides — 'exile_hand') lives in the active
    # player's HAND; a battlefield one (Treasonous Ogre 'pay_life', Lion's Eye Diamond 'discard_hand') is an
    # untapped permanent it controls. We yield the mana they produce with cost_generic=0 / taps_self=False —
    # the SPECIAL cost (life / discard / exile / sacrifice) is paid by _spend_mana when the source is used.
    in_hand = state.get("in_hand", set())
    counters = state.get("counter", set())
    for (t, kind, _amt) in sorted(special_cost):
        if t not in precise:
            continue
        if kind == "exile_hand":
            if (ap, t) not in in_hand:
                continue                                     # the card must be in the active player's hand
        elif kind.startswith("tap_perms:"):                  # §605 Cabbage: tap N untapped <subtype> you control
            if (t,) not in bf or (ap, t) not in ctrl:
                continue                                     # the source permanent itself need NOT be untapped
            sub = kind.split(":", 1)[1]
            foods = [c for (c,) in bf if (ap, c) in ctrl
                     and (c, sub) in state.get("printed_subtype", set()) and (c,) not in tapped]
            if len(foods) < int(_amt):
                continue                                     # not enough untapped <subtype> permanents to pay
        else:
            if (t,) not in bf or (ap, t) not in ctrl or (t,) in tapped:
                continue                                     # a battlefield alt-cost source, untapped & controlled
            if kind.startswith("remove_counter:"):           # §605 Steam-Kin: must HAVE the N counters to remove
                ckind = kind.split(":", 1)[1]
                have = next((c for (o, k, c) in counters if o == t and k == ckind), 0)
                if have < int(_amt):
                    continue
        yield (t, _source_unit_list(t, s_fixed, s_wild), 0, False)


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


def _put_land_in_play(state: dict, ap: str, land: str) -> None:
    """Move a SPECIFIC land from ap's hand onto the battlefield and fire its §603 landfall (just_entered) —
    no land-drop allowance bookkeeping and no pool refresh. The shared core of the auto land drop
    (_develop_mana) and the explicit ('play', ap, land) action (_play_land)."""
    state["in_hand"].discard((ap, land))
    state["on_battlefield"].add((land,))
    state.setdefault("printed_control", set()).add((ap, land))
    print(f"    {ap} plays land {land}")
    # §603 LANDFALL — a played land enters without using the stack, so signal just_entered(land) so the
    # engine fires 'whenever a land you control enters' triggers, apply them, then clear the signal.
    state.setdefault("just_entered", set()).add((land,))
    _apply_effects(state, *_pending_both(state))
    state["just_entered"].discard((land,))


def _develop_mana(state: dict, ap: str) -> None:
    """Driver-side §305 land mechanics the datalog engine leaves to the apply-and-loop. Play ONE land
    this turn (§305.2) from the active player's hand, then refresh its COLORED mana pool (§106) from the
    untapped lands it controls — each contributes one mana of its produced color (land_produces). The
    engine authors casting legality (can_cast/can_afford over mana_pool); this only stocks the pool. A
    flat mana_available count is kept in sync for the legacy fallback / cache continuity.

    This is the AUTO land drop (the engine picks the land); for agent-driven land plays the env surfaces an
    explicit ('play', ap, land) action wired to `_play_land` and skips this (see env._develop_if_main)."""
    played = state.setdefault("_land_played", set())          # driver bookkeeping; not a souffle relation

    def _play_one() -> bool:                                   # play one land from hand; False if none left
        land = next((s for (p, s) in sorted(state.get("in_hand", set()))
                     if p == ap and (s, "land") in state.get("spell_type", set())), None)
        if not land:
            return False
        _put_land_in_play(state, ap, land)
        return True

    if (ap,) not in played:                                    # §305.2 the one base land drop
        if _play_one():
            played.add((ap,))
    # §305.2 EXTRA land drops: Exploration/Azusa (static, +1/+2) + one-shot 'play an additional land this
    # turn' grants (effect_handlers/lands.py). Play up to the remaining allowance from what's still in hand.
    allow = _static_extra_lands(state, ap) + state.get("_extra_land_grants", {}).get(ap, 0)
    used = state.setdefault("_extra_lands_used", {})
    while used.get(ap, 0) < allow and _play_one():
        used[ap] = used.get(ap, 0) + 1
    _refresh_mana_pool(state, ap)


def _static_extra_lands(state: dict, ap: str) -> int:
    """§305.2 extra land drops from CONTINUOUS abilities (static_player facts, when present in state):
    'extra_land_per_turn' = +1 each (Exploration), 'extra_lands_per_turn_two' = +2 (Azusa), and an
    'each_player_extra_land_per_turn' from ANY permanent = +1 for everyone. 0 if static_player isn't loaded."""
    sp = state.get("static_player", set())
    if not sp:
        return 0
    io = {i: c for (i, c) in state.get("instance_of", set())}
    ctrl = state.get("printed_control", set())
    extra = 0
    for (c,) in state.get("on_battlefield", set()):
        slug = io.get(c)
        if (ap, c) in ctrl:
            if (slug, "extra_land_per_turn") in sp:
                extra += 1
            elif (slug, "extra_lands_per_turn_two") in sp:
                extra += 2
        if (slug, "each_player_extra_land_per_turn") in sp:
            extra += 1
    return extra


def _grant_extra_land(state: dict, ap: str, n: int = 1) -> None:
    """One-shot 'play an additional land this turn' (§116.2a): bump ap's extra-land grant for this turn."""
    g = state.setdefault("_extra_land_grants", {})
    g[ap] = g.get(ap, 0) + n


def _land_drops_remaining(state: dict, ap: str) -> int:
    """§305.2 how many more lands ap may play this turn: the one base drop (unless already used) plus any
    extra-land allowance (Exploration/Azusa statics + one-shot grants) not yet spent. The budget the
    explicit ('play', ap, land) env action is offered against."""
    base = 0 if (ap,) in state.get("_land_played", set()) else 1
    allow = _static_extra_lands(state, ap) + state.get("_extra_land_grants", {}).get(ap, 0)
    extra = max(0, allow - state.get("_extra_lands_used", {}).get(ap, 0))
    return base + extra


def _play_land(state: dict, ap: str, land: str) -> None:
    """§305 play a CHOSEN land from ap's hand — the explicit, agent-driven counterpart to the auto land drop
    in _develop_mana. Put it in play and fire landfall, consume one land-drop allowance (the base drop
    first, then an extra), and refresh the mana pool so the new land's mana is immediately castable. The
    caller (env) is responsible for only offering this while `_land_drops_remaining` > 0."""
    _put_land_in_play(state, ap, land)
    played = state.setdefault("_land_played", set())
    if (ap,) not in played:
        played.add((ap,))
    else:
        used = state.setdefault("_extra_lands_used", {})
        used[ap] = used.get(ap, 0) + 1
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
    # §605 a from-hand alt-cost mana source (Spirit Guide in hand) is a real source even with no board.
    hand_exile = {t for (t, k, _a) in state.get("source_special_cost", set()) if k == "exile_hand"}
    if any(c in hand_exile for (p, c) in state.get("in_hand", set()) if p == ap):
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


def _spends_any_color(state: dict, p: str) -> bool:
    """§106.6 'you may spend mana as though it were mana of any color' — does p hold the BLANKET permission?
    Set by effect_handlers/spend_mana_as (state['_spend_any_color'] = {(player,)}), cleared at §514.2 cleanup.
    When True, the mana model ignores color-matching: _resolve_pool re-colors p's whole pool toward demand so
    pip_shortfall can't fire, and _spend_mana lets any source pay any pip. Public info (re-exported by observe)."""
    return (p,) in state.get("_spend_any_color", set())


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
    transition so floating mana never leaks across steps. EXCEPTION: mana flagged RETAINED 'until end of turn'
    (Birgi) survives — but only up to what's actually still in the pool, so spent retained mana doesn't return."""
    retained = state.get("_retained_mana", set())
    if not retained:
        state["floating_mana"] = set()
        return
    survivors = set()
    new_retained = set()
    for p in {pp for (pp, _c, _n) in retained}:
        cur = _floating(state, p)
        for (pp, c, k) in retained:
            if pp != p:
                continue
            keep = min(int(k), cur.get(c, 0))                  # retained mana still present survives; spent is gone
            if keep > 0:
                survivors.add((p, c, keep)); new_retained.add((p, c, keep))
    state["floating_mana"] = survivors
    state["_retained_mana"] = new_retained


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
    # §106.6 'spend mana as though it were any color' — the player may pay any pip with any mana, so re-color
    # the WHOLE pool toward the hand's demanded colors (the total is preserved). This lets the engine's
    # per-color pip_shortfall see enough of each demanded color (it would otherwise fail an off-color pip).
    if _spends_any_color(state, ap):
        total = sum(by_color.values())
        demand = _mana_demand(state, ap)                      # the flat list of demanded pip colors
        recol: dict[str, int] = {}
        for col in demand:                                    # one unit toward each demanded pip (in priority order)
            if total <= 0:
                break
            recol[col] = recol.get(col, 0) + 1; total -= 1
        for col in demand[::-1]:                              # then top up demanded colors with the remaining mana
            if total <= 0:
                break
            recol[col] += 1; total -= 1
        if total > 0:                                         # any leftover stays colorless (still pays generic)
            recol["colorless"] = recol.get("colorless", 0) + total
        if recol:                                             # (no demand at all -> leave the real pool unchanged)
            by_color = recol
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


def _pay_special_source_cost(state: dict, ap: str, sid: str, cost: tuple) -> None:
    """§605 pay an ALT-COST mana source's special activation cost when it's used for mana:
      pay_life   — the controller loses N life (Treasonous Ogre);
      exile_hand — the source card is exiled FROM HAND (Simian / Elvish Spirit Guide);
      discard_hand — the controller discards the rest of their hand to the graveyard (Lion's Eye Diamond;
                     its self-sacrifice is handled separately via source_sacrifice).
    The mana itself is produced by the normal source machinery; this only deducts the cost."""
    kind, amount = cost
    if kind.startswith("remove_counter:"):                   # §605 Runaway Steam-Kin: remove N counters for mana
        ckind = kind.split(":", 1)[1]
        _bump_counter(state, sid, ckind, -int(amount))
        print(f"    {ap} removes {amount} {ckind} counter(s) from {sid} for mana")
    elif kind.startswith("tap_perms:"):                      # §605 Cabbage: tap N untapped <subtype> you control
        sub = kind.split(":", 1)[1]
        foods = sorted(c for (c,) in state.get("on_battlefield", set())
                       if (ap, c) in state.get("printed_control", set())
                       and (c, sub) in state.get("printed_subtype", set()) and (c,) not in state.get("tapped", set()))
        for c in foods[:int(amount)]:
            _tap(state, c)
        print(f"    {ap} taps {amount} {sub}(s) to activate {sid} for mana")
    elif kind == "pay_life":
        _adjust_life(state, ap, -int(amount))
        print(f"    {ap} pays {amount} life to activate {sid}")
    elif kind == "exile_hand":
        state.get("in_hand", set()).discard((ap, sid))
        state.setdefault("exile", set()).add((sid,))
        print(f"    {ap} exiles {sid} from hand for mana")
    elif kind == "discard_hand":
        rest = [c for (p, c) in state.get("in_hand", set()) if p == ap and c != sid]
        for c in rest:
            state["in_hand"].discard((ap, c))
            state.setdefault("graveyard", set()).add((c,))
        if rest:
            print(f"    {ap} discards their hand ({len(rest)} card(s)) to activate {sid}")


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
    (Forge) can execute the EXACT payment mtg intends — the precise sources/colors a combo can
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


def _pay_pitch_cost(state: dict, ap: str, spell: str) -> None:
    """§118.9 PITCH alternative cost (the Force cycle): if `spell` has a pitch_cost, the controller exiles a
    card of that color from hand (the pitched fodder) — the cost it paid INSTEAD of mana. A no-op if the spell
    has no pitch cost (a different free cast). The pitched card is the canonical-first matching card, excluding
    the spell itself; color via printed_color (color identity), the same signal the engine's free_cast used."""
    pitch = [(s, col) for (s, col, _g) in state.get("pitch_cost", set()) if s == spell]
    if not pitch:
        return
    color = pitch[0][1]
    pcolor = run(state, ["printed_color"])["printed_color"]
    fodder = sorted(c for (p, c) in state.get("in_hand", set())
                    if p == ap and c != spell and (c, color) in pcolor)
    if fodder:
        state["in_hand"].discard((ap, fodder[0]))
        state.setdefault("exile", set()).add((fodder[0],))
        print(f"    {ap} exiles {fodder[0]} (a {color} card) to pitch-cast {spell}")


def _spend_mana(state: dict, ap: str, spell: str) -> None:
    """Pay a spell's COLORED cost (§601.2g) by TAPPING untapped sources for their REAL mana. Each tapped
    source yields ALL its mana at once (§106.4: Sol Ring -> 2 colorless, a Signet -> its 2 colors after
    its {1}); we tap sources until every colored pip (from the right color, incl. wildcards) and the
    generic are covered. Tapping (not decrementing) deletes mana faithfully — a tapped source can't pay
    again this turn or attack, and untaps next turn. The pool is refreshed from what's left untapped so
    the rest of the cast loop sees the reduced mana. INVARIANT: only call when can_afford held."""
    out = run(state, ["free_cast", "has_escape", "escape_pip", "escape_generic"])
    if (ap, spell) in out["free_cast"]:                         # §118.9 an alternative free cost: pay no mana
        _pay_pitch_cost(state, ap, spell)                       # §118.9 PITCH: exile a matching card if applicable
        print(f"    {ap} casts {spell} without paying its mana cost (§118.9)")
        return
    # §702.166 when cast via ESCAPE (a may_play card with an escape cost), pay the ESCAPE mana cost, not the
    # printed one — exactly the cost the engine's eff_pip used for affordability.
    escaping = (ap, spell) in state.get("may_play", set()) and (spell,) in out["has_escape"]
    pip_rows = out["escape_pip"] if escaping else state.get("mana_pip", set())
    gen_rows = out["escape_generic"] if escaping else state.get("mana_generic", set())
    pips: dict[str, int] = {}
    for (s, col, n) in pip_rows:
        if s == spell:
            pips[col] = pips.get(col, 0) + int(n)
    generic = sum(int(n) for (s, n) in gen_rows if s == spell)
    if not pips and generic == 0 and (spell, generic) not in state.get("mana_generic", set()):
        generic = next((int(c) for (s, c) in state.get("mana_cost", set()) if s == spell), 0)  # legacy fallback

    # §107.3 an X SPELL: the controller chooses X and pays {X} (×k) in addition to the fixed cost. Greedy
    # default — spend ALL remaining mana into X (commit to the X-spell: a big tutor / Walking Ballista); the
    # _choose seam lets a policy pick a smaller X. Record _spell_x so the resolution (a 'mana value X or less'
    # tutor, X damage, etc.) reads the value back.
    xk = next((int(k) for (s, k) in state.get("x_count", set()) if s == spell), 0)
    if xk and not escaping:
        avail = next((m for (q, m) in state.get("mana_available", set()) if q == ap), 0)
        fixed = generic + sum(pips.values())
        x = _choose(state, "x_value", None, max(0, (avail - fixed) // xk))
        generic += xk * int(x)
        state.setdefault("_spell_x", {})[spell] = int(x)
        if x:
            print(f"    {ap} chooses X={x} for {spell} (pays {xk * int(x)} more)")

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
    # §106.6 'spend mana as though it were any color' — when this player holds the blanket permission, any
    # source's mana may pay any pip, so colset-matching is relaxed (an off-color source covers a colored pip).
    anyc = _spends_any_color(state, ap)
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
            hit = next((c for c in need_pips if need_pips[c] > 0 and (anyc or c in colset)), None)
            if hit:
                need_pips[hit] -= 1; avail -= 1
        for colset, n in bundles:                             # a bundle pays up to n pips of ONE chosen color
            if avail <= 0:
                break
            color = max((c for c in need_pips if need_pips[c] > 0 and (anyc or c in colset)),
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
        if anyc and any(v > 0 for v in need_pips.values()):   # §106.6 any source helps any unmet pip
            return True
        for colset in slots + [b[0] for b in bundles]:
            if any(need_pips.get(c, 0) > 0 for c in colset):
                return True
        return False

    # order: concrete single-color sources first (preserve flexible wildcards/bundles for pips), then flex.
    # §605 an ALT-COST source (pay life / discard hand / exile from hand) is sorted LAST — a last resort the
    # driver taps only when normal lands/rocks can't cover the cost (so it won't gratuitously crack Lion's Eye
    # Diamond or pay life when a land would do).
    special_tids = {t for (t, _k, _a) in state.get("source_special_cost", set())}

    def keyf(row):
        _sid, units, _cg, _ts = row
        flexcount = sum(1 for u in units if isinstance(u, (frozenset, tuple)))
        return (1 if _sid in special_tids else 0, flexcount,
                sum(u[2] if isinstance(u, tuple) and u and u[0] == "one" else 1 for u in units))
    for sid, units, cg, _ts in sorted(rows, key=keyf):
        if not (need_pips and any(v > 0 for v in need_pips.values())) and need_generic <= 0:
            break
        if helps(units, cg):
            apply(units, cg)
            used.add(sid)
            used_rows.append((sid, units, cg, _ts))
    sacrifices = state.get("source_sacrifice", set())
    special = {t: (k, int(a)) for (t, k, a) in state.get("source_special_cost", set())}
    for sid in used:
        if sid in special:                                    # §605 alt-cost source: pay the SPECIAL cost, no tap
            _pay_special_source_cost(state, ap, sid, special[sid])
            if (sid,) in sacrifices:                           # Lion's Eye Diamond also sacrifices itself
                _sacrifice_source(state, sid)
        elif (sid,) in sacrifices:                            # §605 one-shot fast mana: sacrificed, not tapped
            _sacrifice_source(state, sid)
        else:
            _tap(state, sid)                                  # §701.20 tap (records just_tapped -> City of Brass)
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
    # §106 'haste mana' rider (Arena of Glory): if a CREATURE spell was paid (partly) with a source whose mana
    # grants haste 'if spent on a creature spell', the creature enters with haste this turn.
    if (spell, "creature") in state.get("spell_type", set()) \
            and used & {t for (t,) in state.get("source_haste_rider", set())}:
        state.setdefault("_enters_with_haste", set()).add((spell,))
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


def _controls_commander(state: dict, p: str) -> bool:
    """§903 — does player p control a commander (a commander permanent on the battlefield they control)?
    Used by the modal 'you may choose both if you control a commander' Commander-precon rider."""
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (pp, c) in state.get("printed_control", set()) if pp == p}
    return any(c in on_bf and c in mine for (_o, c) in state.get("_commander_owner", set()))


def _choose_mode(state: dict, spell: str) -> None:
    """§601.2b — as a modal spell is cast, its controller chooses the mode(s). Records chose_mode so the engine
    derives active_mode(spell, mode); only chosen modes' effects resolve. The bridge offers a mode only if its
    effects are resolvable, and emits HOW MANY to choose (spell_mode_count; bumped by spell_mode_count_commander
    when the controller controls a commander — the 'choose both if commander' rider). Greedy default: the first
    `count` offered modes, exposed as a SET-valued referee choice (_choose) a policy/search can override."""
    modes = sorted(m for (s, m) in state.get("spell_mode", set()) if s == spell)
    if not modes:
        return
    ctrl = state.get("_stack_info", {}).get(spell)
    count = next((int(n) for (s, n) in state.get("spell_mode_count", set()) if s == spell), 1)
    cmore = next((int(n) for (s, n) in state.get("spell_mode_count_commander", set()) if s == spell), None)
    if cmore is not None and ctrl is not None and _controls_commander(state, ctrl):
        count = max(count, cmore)                             # §700.2 'choose both if you control a commander'
    count = min(count, len(modes))
    chosen = _choose(state, "modes", None, frozenset(modes[:count]))   # §601.2b — SET-valued mode choice (referee seam)
    picked = sorted(m for m in (chosen if isinstance(chosen, (set, frozenset)) else {chosen}) if m in modes)
    for m in picked:
        state.setdefault("chose_mode", set()).add((spell, m))
    print(f"      {spell}: chooses mode(s) {', '.join(picked)}")


def _fire_cast_triggers(state: dict, caster: str, spell: str) -> None:
    """§601.2i — 'whenever you cast a spell' triggers fire as the spell goes on the stack. Open the cast
    window (cast_spell) so the engine fires the matching cast-triggers, then apply only the NEW pending
    the cast produced (diff vs. the pre-cast pending) so unrelated triggers aren't double-applied. The
    cast window stays set across _apply_effects so cast-triggered creature effects fire too.

    The engine already derives the cast-trigger family from cast_spell (you_cast / you_cast_noncreature /
    you_cast_instant_or_sorcery / opponent_cast / any_cast — see build_engine.py), so a card whose trigger
    is one of those fires here automatically (this is what carries PROWESS-style 'whenever you cast …'
    abilities once a card supplies the trigger)."""
    before, before_dyn = _pending_both(state)
    # §608 PER-TURN NTH-CAST ordinal: this is the caster's n-th spell this turn (and m-th noncreature). Fed
    # as cast_ord / cast_nc_ord ONLY during the window so 'first/second … spell each turn' triggers fire
    # exactly on the matching cast. The counters are per-(player, turn), reset at the turn boundary.
    by = state.setdefault("_cast_by", {})
    by[caster] = n = by.get(caster, 0) + 1
    state["cast_spell"] = {(caster, spell)}
    state["cast_ord"] = {(caster, n)}
    if (spell, "creature") not in state.get("spell_type", set()):       # noncreature ordinal (Esper Sentinel)
        ncby = state.setdefault("_cast_nc_by", {})
        ncby[caster] = m = ncby.get(caster, 0) + 1
        state["cast_nc_ord"] = {(caster, m)}
    else:
        state["cast_nc_ord"] = set()
    now, now_dyn = _pending_both(state)
    new, new_dyn = now - before, now_dyn - before_dyn
    _apply_effects(state, new, new_dyn)
    state["cast_spell"] = set()
    state["cast_ord"] = set()
    state["cast_nc_ord"] = set()


# --- §608 CAST COUNTER + §707.10 SPELL COPYING ------------------------------------------------------
# Two pieces of infrastructure shared by the spell-count / spell-copy mechanics (storm, gravestorm, the
# 'copy target spell' family, replicate). The cast counter is per-turn shim state the engine doesn't need
# (storm's copy is a shim action, not a datalog derivation); the copier reuses the engine's instance_of
# re-derivation so a copy's effects/targets fall out for free.
def _note_cast(state: dict, spell: str | None = None) -> int:
    """Record that a spell was just cast THIS TURN (§608), returning how many were cast BEFORE it (the
    storm count). Reset to 0 at each turn boundary (play_game / env). Counts spells by ANY player —
    §702.40a 'each spell cast before it this turn' is not controller-restricted. Also tallies INSTANT/SORCERY
    casts this turn (_is_cast_count) for 'for each instant/sorcery you've cast this turn' payoffs (Ral,
    Leyline Prodigy's enters-with-extra-loyalty)."""
    prior = state.get("_cast_count", 0)
    state["_cast_count"] = prior + 1
    if spell is not None and {t for (s, t) in state.get("spell_type", set()) if s == spell} & {"instant", "sorcery"}:
        state["_is_cast_count"] = state.get("_is_cast_count", 0) + 1
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
    if made:
        # §707 MAGECRAFT 'whenever you copy a spell' (Storm-Kiln Artist): fire the copy window for the copier.
        state["copied_spell"] = {(controller,)}
        _apply_effects(state, *_pending_both(state))
        state["copied_spell"] = set()
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


def _run_spell_effects(state: dict, spell: str, ctrl: str, tctrl: str | None = None) -> None:
    """§608.2c — a resolving instant/sorcery runs its effects, then goes to the graveyard. `counter`
    removes its target from the stack (the engine's `countered` event then lets any 'when countered'
    trigger fire); the rest are applied via _apply_effects (the shared effect resolver). `tctrl` is the
    TARGETING perspective — usually the spell's controller, but a player who MISDIRECTED this spell instead
    (its targets are then picked to serve them); the spell's own effects still belong to `ctrl`."""
    tctrl = tctrl or ctrl
    if any(s == spell for (s, _m) in state.get("spell_mode", set())):   # §700.2 modal: only the CHOSEN mode resolves
        active = {m for (s, m) in run(state, ["active_mode"])["active_mode"] if s == spell}
        effs = sorted((spell, eff, amt, tgt) for (s, m, eff, amt, tgt) in state.get("spell_effect_mode", set())
                      if s == spell and m in active)
    else:
        effs = _spell_effects(state, spell)
    exile_countered = any(e == "counter_exile" for (_s, e, _a, _t) in effs)   # §614 Force of Negation rider
    for (_s, eff, amt, tgt) in effs:
        if eff == "counter":                                 # §701.5 — counter the spell below it on the stack
            victim = _counter_target(state, spell)
            if victim is not None and _cant_be_countered(state, victim):
                print(f"      {victim} can't be countered — {spell} fails to counter it")  # §701.5f
                victim = None                                # no legal spell to counter (it stays on the stack)
            if victim is not None:
                print(f"      {spell} counters {victim}")
                state["countered"] = {(victim,)}             # §603.10e look-back event for 'when countered'
                _apply_effects(state, *_pending_both(state))
                state["countered"] = set()
                _stack_remove(state, victim)
                if exile_countered:                          # §614 'exile it instead of … graveyard'
                    state.setdefault("_flashback", set()).add((victim,))   # _to_graveyard exiles a flagged object
                _to_graveyard(state, victim)
            else:
                print(f"      {spell} has no spell to counter")
        elif eff == "counter_exile":                         # §614 the rider itself — handled with the counter above
            continue
        elif eff == "counter_mass":                          # §701.5 exile EVERY other spell on the stack (Mindbreak Trap)
            victims = sorted(o for (o, _p) in state.get("on_stack", set())
                             if o != spell and not _cant_be_countered(state, o))   # §701.5f skip uncounterable
            for v in victims:
                print(f"      {spell} exiles {v} from the stack")
                state["countered"] = {(v,)}
                _apply_effects(state, *_pending_both(state))
                state["countered"] = set()
                _stack_remove(state, v)
                state.setdefault("_flashback", set()).add((v,))   # Mindbreak Trap EXILES them
                _to_graveyard(state, v)
        elif eff == "change_targets":                        # §115 Misdirection — redirect the spell below it
            victim = _counter_target(state, spell)
            if victim is not None:
                state.setdefault("_redirect", {})[victim] = ctrl   # its targets now serve the Misdirector
                print(f"      {spell} changes the target of {victim} (it now serves {ctrl})")
            else:
                print(f"      {spell} has no spell to redirect")
        elif eff == "ctarget":                               # §601.2c a CHOSEN mode's single-target zone move
            verb, payload, cls = str(tgt).split("|")          # (Prismari Charm bounce, Get Out self-bounce)
            _resolve_one_target(state, spell, "spell", tctrl, verb, payload, cls)
        elif eff == "cdamage":                               # §120 a chosen mode's direct damage to a target
            _apply_damage(state, spell, int(amt), str(tgt), tctrl)
        else:                                                # shared effect resolver (§603 -> §608 vocabulary)
            _apply_effects(state, {(f"{spell}", eff, amt, tgt, spell, ctrl)})
    _run_spell_dyn(state, spell, ctrl)                       # STRUCTURAL #3 — 'for each' count-scaled player effects
    _run_spell_targets(state, spell, tctrl)                   # §115 single-target creature effects (Murder, ...)
    _run_spell_scope(state, spell, ctrl)                      # board-scope creature effects (Overrun, Wrath, ...)
    _run_spell_damage(state, spell, tctrl)                    # §120 direct damage (Lightning Bolt, Shock, ...)
    _run_spell_riders(state, spell, ctrl)                     # §607.2 'that creature(' s controller)' riders (teamwork)
    _run_spell_you_do(state, spell, ctrl)                     # §608 'you may <cost>. If you do, draw N' (Vision of Love)
    _run_spell_reanimate(state, spell, ctrl)                  # §701 reanimation (Resurrection, Zombify, ...)


def _run_spell_you_do(state: dict, spell: str, ctrl: str) -> None:
    """§608 a SPELL's intra-ability 'You may <self-cost>. If you do, <consequent>' (Vision of Love: 'You may
    sacrifice an artifact or discard a card. If you do, draw two cards'). The bridge folds the pair into
    spell_you_do_cost(spell, '<kind>:<filter>|…') — the OR-alternatives — and spell_you_do_effect(spell,
    'draw', N). Here we OFFER the optional cost: gather the affordable alternatives, route the pay-or-decline
    through _choose (DEFAULT = pay — unlike the §603.2c triggered you_do, a SPELL was cast specifically for its
    consequent, so the card-positive line is to pay; a policy/search may still decline), pay the first
    affordable alternative, then run the consequent. An unpayable cost is a faithful no-op (no consequent)."""
    specs = [s for (sp, s) in state.get("spell_you_do_cost", set()) if sp == spell]
    effs = [(v, int(n)) for (sp, v, n) in state.get("spell_you_do_effect", set()) if sp == spell]
    if not specs or not effs:
        return
    payable = []
    for opt in specs[0].split("|"):
        kind, filt = opt.split(":")
        if kind == "sacrifice" and _sac_candidates(state, ctrl, filt):
            payable.append(("sacrifice", filt))
        elif kind == "discard" and any(p == ctrl for (p, _c) in state.get("in_hand", set())):
            payable.append(("discard", filt))
    if not payable:
        print(f"      {spell}: optional cost unpayable -> 'if you do' consequent does not happen")
        return
    if not _choose(state, "spell_you_do", (False, True), True):
        print(f"      {spell}: declined the optional cost -> no consequent")
        return
    kind, filt = payable[0]
    if kind == "sacrifice":
        cands = _sac_candidates(state, ctrl, filt)
        _sacrifice(state, _choose(state, "sacrifice", cands, _sac_default(state, cands, None)))
    else:                                                     # discard a card
        hand = sorted(c for (p, c) in state.get("in_hand", set()) if p == ctrl)
        card = _choose(state, "discard", hand, hand[0])
        state["in_hand"].discard((ctrl, card))
        state.setdefault(_discard_zone(state, ctrl), set()).add((card,))
    print(f"      {spell}: paid the optional cost ({kind} a {filt}) -> 'if you do' consequent resolves")
    for (v, n) in effs:
        if v == "draw":
            for _ in range(n):
                _draw(state, ctrl)


def _run_spell_dyn(state: dict, spell: str, ctrl: str) -> None:
    """STRUCTURAL #3 — a resolving spell's player-scoped DYNAMIC effects: draw/gain_life/lose_life/mill whose
    amount is a recognized 'for each' count slug (spell_dyn_effect: (spell, eff, base, tag, scope)). The count
    is evaluated NOW (at resolution) from the controller's board/hand/graveyard, and base*count is routed
    through the shared _apply_effects path (so doublers etc. still apply). Scope rides the target column."""
    rows = sorted(r for r in run(state, ["spell_dyn_effect"])["spell_dyn_effect"] if r[0] == spell)
    _apply_dyn(state, {(s, eff, base, tag, scope, spell, ctrl) for (s, eff, base, tag, scope) in rows})


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


def _bounce_spell_target(state: dict, ctrl: str, scope: str) -> str | None:
    """§701.5 the SPELL a 'return target spell to its owner's hand' mode bounces: the topmost OTHER object on
    the stack (scope='opp' restricts to one the resolving controller doesn't control — Hullbreaker's 'a spell
    you don't control'). Returns None when no legal spell is on the stack (the mode then does nothing)."""
    owner = {o: p for (p, o) in state.get("printed_control", set())}
    below = sorted(((p, o) for (o, p) in state.get("on_stack", set())), reverse=True)
    for (_pos, o) in below:
        if scope == "opp" and owner.get(o) == ctrl:           # §115.4 'you don't control' — skip own spells
            continue
        return o
    return None


def _bounce_spell(state: dict, label: str, ctrl: str, scope: str) -> None:
    """§701.5 return a SPELL on the stack to its owner's hand — a SOFT COUNTER (the spell leaves the stack
    without resolving, §608.2k-style). Hullbreaker Horror's 'return target spell you don't control to its
    owner's hand' mode. A copy ceases to exist instead (§707.10a); a real spell goes to its owner's hand."""
    victim = _bounce_spell_target(state, ctrl, scope)
    if victim is None:
        print(f"    trigger {label}: no spell to return to hand")
        return
    owner = next((p for (p, o) in state.get("printed_control", set()) if o == victim), ctrl)
    _stack_remove(state, victim)
    if (victim,) in state.get("_is_copy", set()):             # §707.10a a copy ceases to exist
        _discard_copy(state, victim)
        print(f"    trigger {label}: returns the copy {victim} to nowhere (it ceases to exist)")
        return
    state.setdefault("in_hand", set()).add((owner, victim))
    print(f"    trigger {label}: returns spell {victim} to {owner}'s hand (soft counter)")


def _mode_applicable(state: dict, ctrl: str, mode: str, rows: list) -> bool:
    """Whether a modal trigger's `mode` currently does something — used for the greedy default so an
    'up to one' modal picks a mode that has a legal target instead of fizzling. A ctarget needs a legal
    target of its class; a bounce_spell needs a spell on the stack; any other effect is assumed applicable."""
    for (_a, _m, eff, _amt, tgt) in rows:
        if _m != mode:
            continue
        if eff == "ctarget":
            verb, payload, cls = str(tgt).split("|")
            out = run(state, ["controls", "power", "creature"])
            controls = {(p, c) for (p, c) in out["controls"]}
            powers = {c: int(n) for (c, n) in out["power"]}
            creatures = {c for (c,) in out["creature"]}
            if _pick_target(state, ctrl, cls, verb, payload, controls, powers, creatures) is None:
                return False
        elif eff == "bounce_spell":
            if _bounce_spell_target(state, ctrl, str(tgt)) is None:
                return False
    return True


def _resolve_modal_trigger(state: dict, a: str, count: int, spec: str, src: str, ctrl: str) -> None:
    """§700.2 + §603 — a MODAL triggered ability resolves: its controller chooses up to `count` of the
    offered modes (optional — 0..count — when the printed line was 'choose up to N'), then each chosen
    mode's effects resolve. The offered modes + their per-mode effects came from the bridge as
    trigger_mode_effect (keyed by this ability id, read directly — pure shim). The mode choice is exposed
    on the _choose seam (SET-valued); the greedy default prefers modes that currently DO something."""
    parts = str(spec).split("|")
    optional = "opt" in parts
    modes = [p for p in parts if p != "opt"]
    if not modes:
        return
    rows = [r for r in state.get("trigger_mode_effect", set()) if r[0] == a]
    applicable = [m for m in sorted(modes) if _mode_applicable(state, ctrl, m, rows)]
    if applicable:
        default = frozenset(applicable[:count])
    else:
        default = frozenset() if optional else frozenset(sorted(modes)[:count])
    chosen = _choose(state, "trigger_modes", None, default)
    picked = sorted(m for m in (chosen if isinstance(chosen, (set, frozenset)) else {chosen}) if m in modes)[:count]
    if not picked:
        print(f"    trigger {a}: chooses no mode")
        return
    print(f"    trigger {a}: chooses mode(s) {', '.join(picked)}")
    for m in picked:
        for (_a, _m, eff, amt, tgt) in sorted(r for r in rows if r[1] == m):
            if eff == "ctarget":                             # §601.2c the mode's single-target zone move
                verb, payload, cls = str(tgt).split("|")
                _resolve_one_target(state, a, "trigger", ctrl, verb, payload, cls)
            elif eff == "cdamage":                           # §120 the mode's direct damage
                _apply_damage(state, a, int(amt), str(tgt), ctrl)
            elif eff == "bounce_spell":                      # §701.5 soft counter (return a spell to hand)
                _bounce_spell(state, a, ctrl, str(tgt))
            else:                                            # shared effect resolver (§603 -> §608 vocabulary)
                _apply_effects(state, {(a, eff, int(amt), str(tgt), src, ctrl)})


def _ramp_basic(state: dict, label: str, p: str) -> None:
    """§701.18 'that player may search their library for a land card with a basic land type, put it onto the
    battlefield, then shuffle' (Boseiju's channel consolation, given to the player whose permanent was
    destroyed). The player MAY decline (the choice is on the seam); greedy default = take the land."""
    basic = {o for (o, sup) in state.get("has_supertype", set()) if sup == "basic"}
    ptype = state.get("printed_type", set())
    lib = [c for (pp, c) in state.get("in_library", set()) if pp == p]
    basics = sorted(c for c in lib if (c, "land") in ptype and c in basic)
    take = _choose(state, "channel_ramp", basics + [None], basics[0] if basics else None)
    if take is None:
        if basics:
            print(f"    channel {label}: {p} declines to search a basic land")
        else:
            print(f"    channel {label}: {p} has no basic land to search")
        _shuffle_library(state, p)                            # §701.18 'then shuffle' happens even on a failed/declined search
        return
    state["in_library"].discard((p, take))
    if p in state.get("_lib_order", {}):
        state["_lib_order"][p][:] = [x for x in state["_lib_order"][p] if x != take]
    state.setdefault("on_battlefield", set()).add((take,))    # untapped (Boseiju doesn't say tapped)
    print(f"    channel {label}: {p} searches up basic land {take} -> battlefield")
    _shuffle_library(state, p)                                # §701.18 'then shuffle'


def _resolve_channel(state: dict, label: str, ctrl: str, payload: str) -> None:
    """§702.x resolve a CHANNEL ability: pick a legal target of the class, apply the verb (destroy / bounce),
    then run the optional consolation (Boseiju: the targeted permanent's controller may ramp a basic land).
    `payload` = 'verb|verb_payload|class|consolation'."""
    verb, vpayload, cls, consol = str(payload).split("|")
    out = run(state, ["controls", "power", "creature", "cant_be_destroyed"])
    indestructible = {c for (c,) in out["cant_be_destroyed"]}
    controls = {(p, c) for (p, c) in out["controls"]}
    powers = {c: int(n) for (c, n) in out["power"]}
    creatures = {c for (c,) in out["creature"]}
    owner_of = {c: p for (p, c) in controls}
    tgt = _pick_target(state, ctrl, cls, verb, vpayload, controls, powers, creatures)
    if tgt is None:
        print(f"    channel {label}: no legal target")
        return
    victim_ctrl = owner_of.get(tgt)
    _apply_target_verb(state, label, "channel", verb, vpayload, tgt, ctrl, indestructible, owner_of)
    if consol == "ramp_basic" and victim_ctrl is not None:    # §701.18 the consolation goes to THAT player
        _ramp_basic(state, label, victim_ctrl)


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


def _run_spell_riders(state: dict, spell: str, ctrl: str) -> None:
    """§607.2 a spell's 'that creature' / 'that creature's controller' RIDER — an effect whose target is the
    creature the spell's MAIN effect already chose (stored in _spell_pick). The bridge emits these as
    spell_rider(spell, verb, payload, target_ref, cond) for the forms whose anaphoric target the fresh-target
    model can't bind; we resolve them against the remembered pick. Currently the TEAMWORK riders — Team
    Tactics ('that creature gains trample') and Repulsor Blast ('deals 2 to that creature's controller') —
    cond 'teamwork' applies ONLY if the spell was cast using teamwork; cond '-' always applies. No remembered
    pick (the main effect found no target / fizzled) -> the rider has nothing to reference and is skipped."""
    riders = [r for r in state.get("spell_rider", set()) if r[0] == spell]
    if not riders:
        return
    pick = state.get("_spell_pick", {}).get(spell)
    if pick is None:                                          # the main effect chose no creature -> rider no-ops
        return
    teamwork = (spell,) in state.get("cast_using_teamwork", set())
    owner_of = {o: p for (p, o) in state.get("printed_control", set())}
    for (_s, verb, payload, ref, cond) in sorted(riders):
        if cond == "teamwork" and not teamwork:               # the rider's 'if cast using teamwork' gate
            continue
        if str(ref) == "that_creature":                       # the rider hits the same creature
            v = "grant" if str(verb) == "grant_keyword" else str(verb)   # _apply_target_verb's keyword arm is 'grant'
            _apply_target_verb(state, spell, "spell", v, str(payload), pick, ctrl, set(), owner_of)
        elif str(ref) == "that_creature_controller":          # …or its controller (a player)
            p = owner_of.get(pick)
            if p is not None and str(verb) == "deal_damage":  # 'deals N to that creature's controller'
                print(f"      {spell}: {p} (that creature's controller) takes {payload} -> "
                      f"{_adjust_life(state, p, -int(payload))} life")


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
        if _consume_regen_shield(state, c):                  # §701.15 a regen shield replaces the destruction
            return
        state["on_battlefield"].discard((c,))
        state.setdefault("graveyard", set()).add((c,))
        print(f"      {label} deals lethal damage to {c} -> graveyard")

    def best_killable():                                      # strongest enemy whose toughness n can finish
        killable = [c for c in enemy if c not in indestructible and tough.get(c, 1) <= n]
        return max(killable, key=lambda c: powers.get(c, 0)) if killable else None

    def hit_player(p):                                        # §616 a player's damage may be REDIRECTED first
        redir = _redirect_player_damage(state, p)            # to a creature they control (Pariah/Kjeldoran/en-Kor)
        if redir is not None:
            print(f"      {label}'s {n} to {p} is redirected to {redir} (§616)")
            if tough.get(redir, 1) <= n:                     # the creature takes the damage instead — lethal if it finishes it
                kill(redir)
            else:
                print(f"      {label} deals {n} to {redir} (non-lethal)")
        else:
            print(f"      {label} deals {n} to {p} -> {_adjust_life(state, p, -n)} life")

    owner_of = {c: p for (p, c) in controls}                  # §700.x for the crime check below — a hit creature's controller
    if kind == "self":
        hit_player(ctrl)                                       # 'damage to you' targets the controller — never a crime
    elif kind == "face":
        if opp is not None:
            _note_crime(state, ctrl, opp, label)              # §700.x burn targeting an opponent player IS a crime
            hit_player(opp)
    elif kind.startswith("creature_fixed:"):                  # §701.12 a FIXED creature (fight): the target is
        tgt = kind.split(":", 1)[1]                            # already chosen — apply n to THAT creature, lethal
        if tgt not in creatures or tgt not in on_bf:           # if n >= its toughness (unless indestructible/regen)
            print(f"      {label} has no creature {tgt} to damage")
        else:
            _note_crime(state, ctrl, owner_of.get(tgt), label)  # §700.x crime iff the fought creature is an opponent's
            if tough.get(tgt, 1) <= n:
                kill(tgt)
            else:
                print(f"      {label} deals {n} to {tgt} (non-lethal)")
    elif kind in ("creature_any", "creature_opponent"):
        tgt = best_killable() or (max(enemy, key=lambda c: powers.get(c, 0)) if enemy else None)
        if tgt is None:
            print(f"      {label} has no creature to damage")
        else:
            state.setdefault("_spell_pick", {})[label] = tgt   # §607.2 remember the damaged creature for a
            #                                                    'that creature('s controller)' rider (Repulsor Blast)
            _note_crime(state, ctrl, owner_of.get(tgt), label)  # §700.x crime iff the damaged creature is an opponent's
            if tough.get(tgt, 1) <= n:
                kill(tgt)
            else:
                print(f"      {label} deals {n} to {tgt} (non-lethal)")
    elif kind == "any_target":                                # kill a real threat if we can, else go face
        tgt = best_killable()
        if tgt is not None:
            _note_crime(state, ctrl, owner_of.get(tgt), label)  # §700.x 'any target' resolving onto an opponent's creature
            kill(tgt)
        elif opp is not None:
            _note_crime(state, ctrl, opp, label)              # §700.x 'any target' going to an opponent's face
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
    out = run(state, ["controls", "creature", "cant_be_destroyed", "power"])
    indestructible = {c for (c,) in out["cant_be_destroyed"]}
    controls = {(p, c) for (p, c) in out["controls"]}
    creatures = {c for (c,) in out["creature"]}
    powers = {c: int(n) for (c, n) in out["power"]}
    owner_of = {c: p for (p, c) in controls}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in controls if p == ctrl}
    ptype = state.get("printed_type", set())
    for (_s, verb, payload, full_scope) in rows:
        # §115 a board scope may carry '#'-joined FILTER tokens (creatures_you_control#attacking, all_creatures#
        # tapped) — the same filter vocabulary as restricted single targets (driver._target_filter_pred). Split
        # the base scope off, expand it, then NARROW by each filter (always faithful — only shrinks the set).
        scope, *scope_filters = full_scope.split("#")
        if scope == "own_nonland_perms":
            # §613 Dramatic Reversal — every NONLAND permanent the controller controls (not just creatures).
            targets = sorted(c for c in mine if c in on_bf and (c, "land") not in ptype)
        elif scope == "creatures_your_opponents_control":
            # §611 every creature an opponent controls (the resolving controller's are excluded).
            targets = sorted(c for c in creatures if c in on_bf and c not in mine)
        elif scope in ("all_artifacts", "all_enchantments", "all_lands", "all_planeswalkers",
                       "all_nonland_permanents", "all_permanents"):
            # §701 board-wide NON-CREATURE mass scopes — enumerate every permanent of the named type.
            types_of = {}
            for (o, t) in ptype:
                types_of.setdefault(o, set()).add(t)
            for c in creatures:                                  # the engine-DERIVED creature type (animated lands/tokens)
                types_of.setdefault(c, set()).add("creature")
            want = {"all_artifacts": "artifact", "all_enchantments": "enchantment",
                    "all_lands": "land", "all_planeswalkers": "planeswalker"}.get(scope)
            def _match(c):
                ts = types_of.get(c, set())
                if scope == "all_permanents":
                    return True
                if scope == "all_nonland_permanents":
                    return "land" not in ts
                return want in ts
            targets = sorted(c for c in on_bf if _match(c))
        else:
            # creatures_you_control / other_creatures_you_control (a spell has no self creature to exclude,
            # so it expands to the controller's creatures) / all_creatures.
            targets = sorted(c for c in creatures if c in on_bf
                             and (scope == "all_creatures" or c in mine))
        for filt in scope_filters:                               # §115 narrow the scope by each restriction filter
            pred = _target_filter_pred(state, filt, powers, creatures)
            targets = [c for c in targets if pred(c)]
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


def _attach_counter_sign(state: dict, perm: str) -> str | None:
    """The sign of a permanent's 'put a counter on enchanted/equipped creature' effect ('pos' for +1/+1,
    'neg' for -1/-1), or None if it carries no such marker. Auras whose ONLY 'attached' effect is this
    counter (Forced Adaptation, Biting Tether) still need to attach so the host can be found at resolution;
    the sign picks a friendly (pos) vs enemy (neg) host, like a static P/T buff's sign does."""
    signs = {sg for (p, sg) in state.get("attach_counter", set()) if p == perm}
    if not signs:
        return None
    return "neg" if "neg" in signs else "pos"


def _attach_aura(state: dict, aura: str, ctrl: str) -> None:
    """§303.4 an Aura enters the battlefield attached to a creature. We attach Auras that carry a P/T or
    keyword 'enchanted creature' static buff (applied via attached_to), a 'counter on enchanted creature'
    effect (attach_counter), or that STEAL control (Control Magic, via eff_gain_control): a beneficial buff
    goes on the controller's strongest creature; a negative buff or a control-steal goes on the opponent's
    strongest. Auras with no legal host stay unattached (no effect)."""
    is_control = (aura,) in state.get("aura_control", set())
    csign = _attach_counter_sign(state, aura)
    if (aura, "aura") not in state.get("printed_subtype", set()):
        return
    if not _has_attached_static(state, aura) and not is_control and csign is None:
        return
    out = run(state, ["controls", "creature", "power"])
    controls = {(p, c) for (p, c) in out["controls"]}
    creatures = {c for (c,) in out["creature"]}
    powers = {c: int(n) for (c, n) in out["power"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in controls if p == ctrl}
    # a control-steal / negative static buff / a -1/-1 'attached' counter targets an enemy; else a friend.
    harmful = is_control or _static_attached_pt(state, aura) < 0 or csign == "neg"
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


def _cant_be_countered(state: dict, spell: str) -> bool:
    """§701.5f — does the spell `spell` (an object on the stack) have a 'can't be countered' static? The
    faithful SELF slice: its card carries `cant(card, "self", "be_countered")` (Emrakul, Supreme Verdict,
    Vexing Shusher, …), surfaced by the bridge as the driver-only `uncounterable` flag on this instance.
    (Controller-scoped 'spells you control can't be countered' anthems are a separate continuous static —
    not modeled here; this owns the self form.)"""
    return (spell,) in state.get("uncounterable", set())


def _to_graveyard(state: dict, obj: str) -> None:
    """§608.2m / §405.5 — a resolved or countered spell that isn't a permanent goes to the graveyard, UNLESS
    it was cast with flashback (§702.34d): such a spell is EXILED instead of going to the graveyard."""
    if (obj,) in state.get("_flashback", set()):
        state.setdefault("exile", set()).add((obj,))
        state["_flashback"].discard((obj,))
        print(f"    {obj} was cast with flashback -> exiled (§702.34d)")
        return
    state.setdefault("graveyard", set()).add((obj,))


def _leave_cast_zone(state: dict, ap: str, spell: str) -> None:
    """§601 remove a just-cast spell from its SOURCE zone. Normally the hand; but a card cast from a non-hand
    source under a `may_play` permission (impulse from exile, flashback from the graveyard) leaves THAT zone
    and loses the one-shot permission. Zone-agnostic so the same cast path serves every source."""
    state["in_hand"].discard((ap, spell))
    if (ap, spell) in state.get("may_play", set()):
        for z in ("exile", "graveyard"):                     # impulse = exile, flashback = graveyard
            state.get(z, set()).discard((spell,))
        state["may_play"].discard((ap, spell))


def _offer_escape(state: dict, ap: str) -> None:
    """§702.166 — make each ESCAPE card in ap's graveyard castable from there (set may_play), when the escape
    additional cost is PAYABLE: ap's graveyard must hold at least N+1 cards (the escaping card + N others to
    exile). Re-evaluated before each cast window (escape is a static permission, not a one-shot)."""
    out = run(state, ["has_escape", "escape_exile"])
    has = {c for (c,) in out["has_escape"]}
    if not has:
        return
    exile_n = {s: int(n) for (s, n) in out["escape_exile"]}
    gy = [c for (c,) in state.get("graveyard", set())]
    ctrl = state.get("printed_control", set())
    for c in gy:
        if c in has and (ap, c) in ctrl and len(gy) >= exile_n.get(c, 0) + 1:
            state.setdefault("may_play", set()).add((ap, c))


def _pay_escape_cost(state: dict, ap: str, spell: str) -> None:
    """§702.166 the escape ADDITIONAL cost — exile N OTHER cards from ap's graveyard. Called right after the
    escaping spell itself has left the graveyard (via _leave_cast_zone), so the 'others' are what remains."""
    n = next((int(x) for (s, x) in run(state, ["escape_exile"])["escape_exile"] if s == spell), 0)
    if n <= 0:
        return
    gy = sorted(c for (c,) in state.get("graveyard", set()) if (ap, c) in state.get("printed_control", set()))
    for c in gy[:n]:
        state["graveyard"].discard((c,))
        state.setdefault("exile", set()).add((c,))
    print(f"    {ap} escapes {spell}: exiles {min(n, len(gy))} other card(s) from the graveyard (§702.166)")


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
        elif eff == "channel":                               # §702.x channel: a single-target verb + an opt. consolation
            _resolve_channel(state, top, actrl, tgt)
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
    if any(top == ia for (ia, _c, _a) in state.get("loy_cast", set())):   # §606 a resolving LOYALTY ability
        print(f"    {top} resolves (loyalty ability)")
        _run_spell_effects(state, top, ctrl)                 # same effect/target/scope/damage path as a spell
        state["loy_cast"] = {r for r in state.get("loy_cast", set()) if r[0] != top}   # it ceases to exist (no zone)
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
        if (top,) in state.get("_enters_with_haste", set()):  # §106 cast with Arena of Glory's 'haste mana'
            eid = f"hastemana__{top}"
            state.setdefault("eff_grant_keyword", set()).add((eid, top, "haste"))
            state.setdefault("until_eot", set()).add((eid,))  # §613 layer 6 — wears off at cleanup
            state["_enters_with_haste"].discard((top,))
            print(f"      {top} enters with haste (cast with Arena of Glory's mana)")
        _attach_aura(state, top, ctrl)                        # §303.4 an Aura enters attached to a creature
        if (top, "planeswalker") in state.get("printed_type", set()):   # §306.5b enters with its starting loyalty
            base = next((n for (s, n) in state.get("card_loyalty", set())
                         if s in {sl for (o, sl) in state.get("instance_of", set()) if o == top}), 0)
            _bump_counter(state, top, "loyalty", base); print(f"      {top} enters with {base} loyalty")
        if (top,) in out["enters_tapped"]:
            state.setdefault("tapped", set()).add((top,)); print(f"      {top} enters tapped")
        for (c, k, n) in sorted(out["enters_with_counter"]):
            if c == top:
                # §614.13 'enters the battlefield WITH counters' is a REPLACEMENT as it enters, NOT a 'counter
                # is put on' event — placed_event=False so it doesn't fire a counter-placement trigger.
                _bump_counter(state, top, k, int(n), placed_event=False); print(f"      {top} enters with {n} {k} counter")
        # §603.2a — the permanent's own enters ability triggers AS it enters. Re-assert it as the resolving
        # object (ev_etb only holds while resolving) with the permanent now on the battlefield so
        # 'creatures you control' scopes include it; _apply_effects applies both player- and creature-scoped
        # ETB pendings, then it leaves the stack for good.
        maxd = max([d for (_o, d) in state.get("on_stack", set())], default=-1)
        state.setdefault("on_stack", set()).add((top, maxd + 1))
        state["all_passed"] = {("yes",)}
        _apply_effects(state, *_pending_both(state))
        state["all_passed"] = set()
        _stack_remove(state, top)
        return
    print(f"    {top} resolves")                             # an instant/sorcery: run effects, then graveyard
    # §115 MISDIRECTION redirect: if this spell's target was changed, pick its target from the perspective of
    # the player who redirected it (a harmful spell now hits THEIR enemy), not the spell's own controller.
    tctrl = state.setdefault("_redirect", {}).pop(top, ctrl)
    _run_spell_effects(state, top, ctrl, tctrl)
    if (top,) in state.get("_is_copy", set()):               # §707.10a a resolved COPY ceases to exist (no graveyard)
        _discard_copy(state, top)
    elif (top,) in state.get("_resolved_to_hand", set()):    # §701 a spell that returned ITSELF to hand (bounce_self
        state["_resolved_to_hand"].discard((top,))           # — Hanabi Blast, 'How to Keep an Izzet Mage Busy'): the
        #                                                      bounce_self applier already put it in hand; skip graveyard.
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
    _leave_cast_zone(state, p, spell)                        # §601 leave the source zone (hand / flashback GY / exile)
    _stack_push(state, spell, p)
    _choose_mode(state, spell)                               # §601.2b — modal instant chooses its mode
    prior = _note_cast(state, spell)                         # §608 a response-cast counts toward storm too
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
        _fire_lifegain_triggers(state)                       # §603 'whenever you gain life' for any gain this resolution
        _fire_counter_placed_triggers(state)                 # §603/§122 '+1/+1 counter(s) put on ~' for any counters this resolution
        _fire_you_do_costs(state)                            # §603.2c 'If you do' — offer any fired antecedent's optional cost
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
        _offer_escape(state, ap)                              # §702.166 make payable escape GY cards castable
        castable = sorted(s for (p, s) in run(state, ["can_cast"])["can_cast"] if p == ap)
        if not castable:
            break
        _cast_spell(state, ap, castable[0], players)         # greedy: cast the first castable spell
    state["has_priority"] = set()
    _activate_phase(state, ap, players)                      # §602 — then use a non-mana activated ability if able
    _activate_loyalty(state, ap, players)                    # §606.3 — a planeswalker loyalty ability (sorcery speed)


def _offer_teamwork(state: dict, ap: str, spell: str) -> None:
    """§702.x TEAMWORK N (Marvel) — an OPTIONAL ADDITIONAL COST paid as the spell is cast: 'you may tap any
    number of creatures you control with total power N or more'. If paid, it sets the cast_using_teamwork(spell)
    window so the engine derives the spell's 'if this spell was cast using teamwork, <bonus>' rider effects
    (the card_effect rows tagged cond 'was_cast_using_teamwork'); declined, only the MAIN effect resolves.

    The SAME shape as _fire_you_do_costs (an optional cost gating a consequent), routed through the _choose
    seam (key 'teamwork', DEFAULT decline) so a search/policy can choose to pay. The cost is payable only if
    the caster controls UNTAPPED creatures whose total power reaches N; if so and the controller elects to pay,
    tap a power-greedy minimal set (biggest first) to reach N and open the window. Untaxed declination (the
    common line) leaves the rider inert exactly as before this feature."""
    n = next((int(amt) for (sp, amt) in state.get("teamwork_cost", set()) if sp == spell), None)
    if n is None:
        return
    powers = {c: int(p) for (c, p) in run(state, ["power"])["power"]}
    tapped = state.get("tapped", set())
    avail = sorted(((powers.get(c, 0), c) for c in _creatures_of(state, ap)
                    if (c,) not in tapped and powers.get(c, 0) > 0), reverse=True)
    if sum(p for p, _c in avail) < n:                        # can't reach the total-power threshold -> can't pay
        return
    if not _choose(state, "teamwork", (False, True), False):  # DEFAULT: decline (only the main effect resolves)
        return
    chosen, total = [], 0
    for p, c in avail:                                       # tap a minimal big-first set reaching total power N
        if total >= n:
            break
        chosen.append(c); total += p
    for c in chosen:
        _tap(state, c)
    _fire_tap_triggers(state)                                # §603 'whenever ~ becomes tapped to pay a teamwork cost'
    state.setdefault("cast_using_teamwork", set()).add((spell,))   # the window the rider's spell_* rules gate on
    print(f"    {ap} pays {spell}'s teamwork {n} cost — taps {', '.join(chosen)} (total power {total}); the rider resolves")


def _cast_spell(state: dict, ap: str, spell: str, players: list) -> None:
    """§601 -> §608 cast ONE spell sorcery-speed onto the real stack and resolve it (mode + cast triggers +
    response window + top-down resolution). The single-spell core of _cast_phase — reused by the env/search
    so an external policy can cast a CHOSEN spell (with forced mode/target via the _choose seam)."""
    escaping = (ap, spell) in state.get("may_play", set()) \
        and (spell,) in run(state, ["has_escape"])["has_escape"]    # §702.166 cast via escape (before may_play clears)
    _spend_mana(state, ap, spell)                            # §601.2g — consume the mana so casts are limited
    _fire_tap_triggers(state)                                # §603 'whenever ~ becomes tapped' (sources tapped to pay)
    _leave_cast_zone(state, ap, spell)                       # §601 leave the source zone (hand / exile / graveyard)
    if escaping:
        _pay_escape_cost(state, ap, spell)                   # §702.166 additional cost: exile N other GY cards
    _offer_teamwork(state, ap, spell)                        # §702.x TEAMWORK — optional additional cost (tap
    #                                                          creatures of total power N); if paid, enables the rider
    _stack_push(state, spell, ap)
    _choose_mode(state, spell)                               # §601.2b — choose mode(s) if it's a modal spell
    prior = _note_cast(state, spell)                         # §608 count this spell; `prior` = storm count
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


def _legendary_count(state: dict, p: str) -> int:
    """§205.4 how many LEGENDARY CREATURES player p controls — for the channel '{1} less per legendary
    creature you control' cost reduction (Boseiju). Reads the engine's legendary classification (derived
    from has_supertype) intersected with p's creatures."""
    out = run(state, ["controls", "creature"])
    legendary = {o for (o, sup) in state.get("has_supertype", set()) if sup == "legendary"}
    creatures = {c for (c,) in out["creature"]}
    return len({c for (pp, c) in out["controls"] if pp == p and c in creatures and c in legendary})


def _ability_eff_cost(state: dict, a: str, cost, p: str) -> int:
    """The effective mana cost of activated ability `a` after §118 cost reductions (the channel
    '{1} less per legendary creature you control' rider). The reduction lowers only the GENERIC mana, so
    the cost floors at the colored-pip portion the bridge recorded (e.g. Boseiju's {1}{G} floors at {G}=1)."""
    n = int(cost)
    red = next((r for r in state.get("ability_cost_reduction", set()) if r[0] == a and r[1] == "legendary_creature"), None)
    if red is not None:
        floor = int(red[2]) if len(red) > 2 else 0
        n = max(floor, n - _legendary_count(state, p))
    return n


def _activatable(state: dict, p: str) -> list:
    """§602.5 — the activated abilities player p can pay for right now: source on the battlefield and
    controlled by p, its {T} part untappable (source untapped & not summoning-sick), enough mana for the
    mana part. A §702.x CHANNEL ability (ability_from_hand) is instead gated on the source being in p's
    HAND. Returns (ability_id, source, mana_cost, taps_self, eff, amount, target) rows."""
    bf = state.get("on_battlefield", set())
    ctrl = state.get("printed_control", set())
    tapped = state.get("tapped", set())
    sick = state.get("_sick", set())
    mana = next((m for (q, m) in state.get("mana_available", set()) if q == p), 0)
    out = []
    for row in state.get("activated_ability", set()):
        a, src, cost, taps, eff, amt, tgt = row
        from_hand = (a,) in state.get("ability_from_hand", set())
        if from_hand:                                        # §702.x channel — activated from HAND, not the battlefield
            if (p, src) not in state.get("in_hand", set()):
                continue
        elif (src,) not in bf or (p, src) not in ctrl:
            continue
        if _ability_eff_cost(state, a, cost, p) > mana:
            continue
        if taps == "T" and ((src,) in tapped or (src,) in sick):
            continue                                         # can't pay {T}: already tapped or summoning sick
        life_cost = next((int(ln) for (aa, ln) in state.get("ability_life_cost", set()) if aa == a), 0)
        if life_cost and next((v for (q, v) in state.get("life", set()) if q == p), 0) <= life_cost:
            continue                                         # §118.4 'Pay N life': can't pay if it wouldn't leave you ≥1
        disc_cost = next((int(dn) for (aa, dn) in state.get("ability_discard_cost", set()) if aa == a), 0)
        if disc_cost and len([c for (pp, c) in state.get("in_hand", set()) if pp == p]) < disc_cost:
            continue                                         # §118 'Discard N cards': need N cards in hand (Nezahal)
        sac_kind = next((k for (aa, k) in state.get("ability_sac_filter", set()) if aa == a), None)
        if sac_kind is not None and not _sac_candidates(state, p, sac_kind, src):
            continue                                         # §602.5 'Sacrifice a <X>': need a permanent to sacrifice
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
        if eff == "channel":                                 # §702.x don't discard the card unless a legal target exists
            verb, vpayload, cls, _consol = str(tgt).split("|")
            o = run(state, ["controls", "power", "creature"])
            controls = {(pp, cc) for (pp, cc) in o["controls"]}
            powers = {cc: int(nn) for (cc, nn) in o["power"]}
            creatures = {cc for (cc,) in o["creature"]}
            if _pick_target(state, p, cls, verb, vpayload, controls, powers, creatures) is None:
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
    eff_cost = _ability_eff_cost(state, a, cost, ap)         # §118 after the channel '{1} less per legendary' reduction
    if eff_cost:                                             # pay the mana part via the mana model
        _spend_ability_mana(state, ap, eff_cost)
    if (a,) in state.get("ability_discard_self", set()):     # §702.x channel — 'Discard this card' is part of the cost
        state["in_hand"].discard((ap, src))
        state.setdefault(_discard_zone(state, ap), set()).add((src,))
        print(f"    {ap} discards {src} (channel cost)")
    life_cost = next((int(ln) for (aa, ln) in state.get("ability_life_cost", set()) if aa == a), 0)
    if life_cost:                                            # §118 'Pay N life' (Necropotence, Griselbrand)
        print(f"    {ap} pays {life_cost} life -> {_adjust_life(state, ap, -life_cost)}")
    disc_cost = next((int(dn) for (aa, dn) in state.get("ability_discard_cost", set()) if aa == a), 0)
    if disc_cost:                                            # §118 'Discard N cards' (Nezahal) — discard the cheapest
        _apply_effects(state, {(a, "discard", disc_cost, "-", src, ap)})
    if taps == "T":
        _tap(state, src)                                     # §602.2 pay {T} (records just_tapped)
    _fire_tap_triggers(state)                                # §603 'becomes tapped' for the {T} cost / mana taps
    if (a,) in state.get("ability_sac_cost", set()):         # §118 a 'Sacrifice this' activation cost (Teardrop Kami)
        _sacrifice(state, src)                               # fires 'when sacrificed', then -> graveyard
    sac_kind = next((k for (aa, k) in state.get("ability_sac_filter", set()) if aa == a), None)
    if sac_kind is not None:                                  # §602.5 'Sacrifice a <creature/artifact/subtype>'
        cands = _sac_candidates(state, ap, sac_kind, src)    # the cost CHOICE (which permanent) — referee seam
        victim = _choose(state, "sacrifice", sorted(cands), _sac_default(state, cands, src)) if cands else None
        if victim is not None:
            print(f"    {ap} sacrifices {victim} ({sac_kind}) to activate {a}")
            _sacrifice(state, victim)
    state.setdefault("_ability_effect", {})[a] = (eff, int(amt), tgt, src, ap)
    _stack_push(state, a, ap)
    print(f"    {ap} activates {a} ({src}: {eff} {amt})")
    _resolve_stack(state, ap, players)


def _loyalty_activatable(state: dict, p: str) -> list:
    """§606 — the loyalty abilities p can activate right now: a planeswalker p controls that hasn't yet
    activated a loyalty ability this turn (§606.3), each ability whose cost is PAYABLE (a [−N] needs ≥N
    loyalty) AND whose effects RESOLVE (probed via the loy_cast spell path, so we never offer a no-op that
    would burn loyalty for nothing). Returns (planeswalker, card_slug, ability_id, delta) rows. Sorcery
    speed / once-per-turn timing is enforced by the caller (only invoked in the main phase, empty stack)."""
    bf = state.get("on_battlefield", set())
    ctrl = state.get("printed_control", set())
    ptype = state.get("printed_type", set())
    used = state.get("_loyalty_used", set())
    loy_ab = state.get("loyalty_ability", set())             # (card_slug, ability_id, delta) — driver-side
    if not loy_ab:
        return []
    inst = {o: s for (o, s) in state.get("instance_of", set())}
    cur_loy = {o: c for (o, k, c) in state.get("counter", set()) if k == "loyalty"}
    cands = []
    for o in sorted(c for (c,) in bf):
        if (p, o) not in ctrl or (o, "planeswalker") not in ptype or (o,) in used or o not in inst:
            continue
        for (cs, aid, delta) in loy_ab:
            if cs == inst[o] and not (int(delta) < 0 and cur_loy.get(o, 0) < -int(delta)):
                cands.append((o, inst[o], aid, int(delta)))
    if not cands:
        return []
    # batch-probe resolvability: one engine run, a distinct loy_cast probe per candidate.
    probe = {**state, "loy_cast": {(f"__loyq_{i}", cs, aid) for i, (_o, cs, aid, _d) in enumerate(cands)}}
    out = run(probe, ["spell_effect", "spell_target", "spell_scope", "spell_damage"])
    resolves = {r[0] for rel in out.values() for r in rel if str(r[0]).startswith("__loyq_")}
    return sorted(c for i, c in enumerate(cands) if f"__loyq_{i}" in resolves)


def _activate_loyalty(state: dict, ap: str, players: list) -> None:
    """§606.3 — the active player may activate ONE loyalty ability of a planeswalker it controls (sorcery
    speed, once per turn per planeswalker). Pays the loyalty cost (adds/removes `delta` loyalty counters),
    marks the planeswalker used this turn, and puts the ability on the stack to resolve through the spell
    path (loy_cast -> spell_effect/target/scope/damage). Default greedy: DECLINE (None) — loyalty is a scarce
    resource, so a policy/search opts in via the _choose seam."""
    usable = _loyalty_activatable(state, ap)
    if not usable:
        return
    chosen = _choose(state, "loyalty", usable + [None], None)   # §606.3 the activation choice (default: decline)
    if chosen is None:
        return
    pw, card, aid, delta = chosen
    _bump_counter(state, pw, "loyalty", delta)               # §606.3 pay: add/remove loyalty counters
    state.setdefault("_loyalty_used", set()).add((pw,))      # §606.3 only once per turn per planeswalker
    ia = f"{pw}__loy__{aid}"
    state.setdefault("loy_cast", set()).add((ia, card, aid))
    _stack_push(state, ia, ap)
    print(f"    {ap} activates {pw}'s loyalty ability {aid} ({'+' if delta >= 0 else ''}{delta} loyalty)")
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
        _tap(state, sid)                                      # §701.20 tap a source for ability mana (just_tapped)
        paid += net
    _refresh_mana_pool(state, ap)                             # recompute pool/count from sources still untapped (0 if all tapped)


def _seedborn_untap(state: dict, ap: str) -> None:
    """§502 Seedborn Muse — each player who controls an 'untap all permanents you control during each OTHER
    player's untap step' source untaps their permanents during ap's untap step (ap != the source's owner; on
    their own untap step they already untapped normally)."""
    bf = {c for (c,) in state.get("on_battlefield", set())}
    ctrl = state.get("printed_control", set())
    owner_of = {c: p for (p, c) in ctrl}
    for (s,) in state.get("seedborn_untap_source", set()):
        owner = owner_of.get(s)
        if owner is None or owner == ap or s not in bf:
            continue
        for c in sorted(c for (p, c) in ctrl if p == owner):
            if (c,) in _no_untap_set(state):                     # 'doesn't untap' overrides Seedborn too
                continue
            if (c,) in state.get("tapped", set()):
                state["tapped"].discard((c,)); print(f"    {owner} untaps {c} (Seedborn Muse)")


def _skips_draw(state: dict, ap: str) -> bool:
    """§504 — does ap skip their draw step? (controls a 'Skip your draw step' source: Necropotence.)"""
    mine = {c for (p, c) in state.get("printed_control", set()) if p == ap}
    bf = {c for (c,) in state.get("on_battlefield", set())}
    return any(s in mine and s in bf for (s,) in state.get("skip_draw_source", set()))


def _deliver_necro(state: dict, ap: str) -> None:
    """§601 deliver the cards Necropotence exiled face down (state['_necro_pending']) into ap's hand at the
    beginning of ap's end step — the §513 delayed draw."""
    pend = sorted(c for (p, c) in state.get("_necro_pending", set()) if p == ap)
    for c in pend:
        state["_necro_pending"].discard((ap, c))
        state.get("exile", set()).discard((c,))
        state.setdefault("in_hand", set()).add((ap, c))
    if pend:
        print(f"    {ap} puts {len(pend)} card(s) exiled by Necropotence into hand (end step)")


def _return_stolen(state: dict, ap: str) -> None:
    """§608 Mnemonic Betrayal — at ap's end step, return the still-exiled stolen cards (state['_stolen_cards'])
    to their owners' graveyards; a card ap actually cast this turn already left exile and isn't returned. The
    cast permission (may_play) is cleared either way."""
    stolen = state.get("_stolen_cards", set())
    returned = 0
    for (c, _o) in sorted(stolen):
        state.get("may_play", set()).discard((ap, c))
        if (c,) in state.get("exile", set()):                # still exiled (wasn't cast) -> back to graveyard
            state["exile"].discard((c,))
            state.setdefault("graveyard", set()).add((c,))
            returned += 1
    if stolen:
        state["_stolen_cards"] = set()
        print(f"    {ap}: {returned} stolen card(s) return to their owners' graveyards (end step)")


def _no_max_hand_size(state: dict, p: str) -> bool:
    """§402.2 / §604 p has NO maximum hand size (static_player, when loaded): 'no_maximum_hand_size' on a
    permanent p controls (Reliquary Tower/Thought Vessel/Venser's Journal), OR 'players_no_maximum_hand_size'
    on ANY permanent (The Lux Foundation Library — every player). False if static_player isn't loaded."""
    sp = state.get("static_player")
    if not sp:
        return False
    io = {i: c for (i, c) in state.get("instance_of", set())}
    ctrl = state.get("printed_control", set())
    for (c,) in state.get("on_battlefield", set()):
        slug = io.get(c)
        if (slug, "players_no_maximum_hand_size") in sp:
            return True
        if (p, c) in ctrl and (slug, "no_maximum_hand_size") in sp:
            return True
    return False


def _cleanup_discard(state: dict, ap: str, max_hand: int = 7) -> None:
    """§514.1 cleanup — the active player discards down to their maximum hand size (normally seven, §402.2).
    SKIPPED entirely if ap has a 'no maximum hand size' static (Reliquary Tower etc.). Each discard routes
    through the _choose seam (the player's choice) so a policy/search sees it; the greedy default keeps the
    lowest-sorted card (stable, deterministic). Discarded cards go to ap's discard zone (graveyard/exile)."""
    if _no_max_hand_size(state, ap):
        return
    hand = sorted(c for (pp, c) in state.get("in_hand", set()) if pp == ap)
    while len(hand) > max_hand:
        card = _choose(state, "cleanup_discard", hand, hand[0])
        hand.remove(card)
        state["in_hand"].discard((ap, card))
        state.setdefault(_discard_zone(state, ap), set()).add((card,))


def _end_of_turn(state: dict) -> None:
    """§514.2 cleanup — until-end-of-turn continuous effects end (the driver removes them)."""
    ap = next(iter(state["active_player"]))[0]               # §514.1 active player discards to max hand size first
    _cleanup_discard(state, ap)
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
    state["_combat_draw_fired"] = set()                      # §510 Tymna's postcombat-main draw fires once/turn
    state["_sac_subtype_fired"] = set()                      # §603 Cabbage's 'sac a Food on combat damage' once/turn
    state["prevent_all_combat"] = set()                      # §615 Fog lasts only 'this turn'
    state["_cant_block"] = set()                             # §509.1b 'can't block this turn' restriction
    state["cant_be_blocked"] = set()                         # §509.1b 'can't be blocked this turn' restriction
    state["_regen_shield"] = set()                           # §701.15 a regeneration shield lasts only 'this turn'
    state["cant_be_regenerated"] = set()                     # §701.15g the 'can't be regenerated this turn' rider
    state["_spend_any_color"] = set()                        # §106.6 'spend mana as though any color' is a per-turn grant
    state["_damage_redirect"] = {}                           # §616 a 'damage to you is dealt to <creature>' redirect this turn
    state["_crime_noted"] = set()                            # §700.x reset the per-(controller, source) crime dedup each turn
    state["gained_life_this_turn"] = set()                   # §611.2 the SOI 'Infusion' 'gained life this turn' flag is turn-scoped


def play_game(state: dict, players: list[str], max_turns: int = 20) -> str | None:
    """The turn loop: cycle the rules-derived steps, run the engine, apply what it
    derives, pass the turn. Returns the loser (or None if the turn limit is hit)."""
    assert_known_keywords(state)                                 # reject keywords outside the interpreted §702 roster
    variant = "two-player" if len(players) == 2 else "default"   # §103.8 first-turn draw skip applies per variant
    for turn in range(max_turns):
        ap = next(iter(state["active_player"]))[0]
        state["_turn"] = turn                                    # §603.7c turn counter for delayed (Pact) triggers
        skip_draw = turn == 0 and variant in DRAW_SKIP_VARIANTS   # the starting player skips their first draw
        while True:                                              # one step at a time, this turn
            step = next(iter(state["current_step"]))[0]
            if step == "upkeep":                     # §603.7c resolve a delayed Pact cost (pay or lose) first
                pact_loser = _resolve_delayed_upkeep(state, ap)
                if pact_loser:
                    return pact_loser
            skip_this = _skipping_step(state, ap, step)   # §500.7 a 'skip your <step>' effect this turn
            if step == "declare_attackers":          # §508 turn-based action (before priority)
                if not skip_this:                    # §506 a skipped combat phase declares no attackers
                    declare_attackers(state, ap)
            elif step == "declare_blockers":         # §509 turn-based action (before priority)
                if not skip_this:
                    declare_blockers(state, ap)
            if step in GRANTS_PRIORITY:              # §5 priority window — the active player may cast
                _cast_phase(state, ap)
            _fire_tap_triggers(state)                # §603 'becomes tapped' for any taps this step (combat, effects)
            _fire_lifegain_triggers(state)           # §603 'whenever you gain life' for any gain this step (combat lifelink, effects)
            _fire_counter_placed_triggers(state)     # §603/§122 '+1/+1 counter(s) put on ~' for any counters this step (effects)
            _fire_you_do_costs(state)                # §603.2c 'If you do' — offer any fired antecedent's optional cost
            if step == "end":                        # §513 'at the beginning of your next end step' deliveries
                _deliver_necro(state, ap)            # §601 Necropotence: exiled cards come to hand at end step
                _return_stolen(state, ap)            # §608 Mnemonic Betrayal: stolen cards return to graveyards
                if (ap,) in state.get("_monarch", set()):   # §720.6 the monarch draws at the beginning of THEIR end step
                    print(f"    {ap} is the monarch and draws a card (§720.6)")
                    _draw(state, ap)
            if step == "cleanup":                    # §514.2 cleanup
                _end_of_turn(state)
            out = run(state, OUTPUTS)
            if step == "untap" and skip_this:        # §500.7 a 'skip your untap step' effect
                out["to_untap"] = set(); print(f"    {ap} skips their untap step")
            if step == "draw" and (skip_draw or _skips_draw(state, ap) or skip_this):   # §103.8a first-turn / §504 Necropotence / §500.7 'skip your draw step'
                out["to_draw"] = set(); print(f"    {ap} skips their draw step")
            loser = _apply_outputs(state, out, ap)
            if loser:
                return loser
            if not out["advance_to"]:                            # past cleanup -> turn ends
                break
            advance_to = _extra_combat_redirect(state, ap, out["advance_to"])  # §505/§506 loop back into combat
            if advance_to != out["advance_to"]:                  # an extra combat means fresh §508 declarations
                state["attacks"], state["blocks"] = set(), set()
            state["current_step"] = advance_to                   # advance to the engine's next step
            _empty_mana_pool(state)                              # §500.4 mana empties at end of each step/phase
        nxt_p = _next_active_player(state, ap, players)          # pass the turn (§500.6) — or take an extra one
        state["active_player"] = {(nxt_p,)}
        state["current_step"] = {("untap",)}
        state["_retained_mana"] = set()                          # §500.4 retained mana lasts only 'until end of turn'
        _empty_mana_pool(state)                                  # §500.4 — pool empties across the turn boundary too
        state["attacks"], state["blocks"] = set(), set()        # combat declarations don't carry over
        state["_land_played"] = set()                           # §305.2 — a fresh land drop next turn
        state["_extra_lands_used"] = {}; state["_extra_land_grants"] = {}   # extra-land allowance resets too
        state["_cast_count"] = 0                                 # §608/§702.40 storm count is per-turn
        state["_is_cast_count"] = 0                              # §712 instant/sorcery-cast tally is per-turn (Ral)
        state["_loyalty_used"] = set()                          # §606.3 loyalty ability is once-per-turn per planeswalker
        state["_combat_damaged"] = set()                        # §510 'dealt combat damage this turn' resets (Tymna)
        state["_cast_by"] = {}; state["_cast_nc_by"] = {}        # §608 per-player nth-cast ordinals reset each turn
        state["_draw_by"] = {}                                   # §603 per-player draw ordinal ('Nth card each turn') resets
        state["may_play"] = set(); state["_flashback"] = set()  # §608/§702.34 impulse + flashback permissions expire EOT
        state["free_grant"] = set()                             # §118.9 the impulse 'play without paying' grant expires with may_play
        state["_extra_combats"] = {}; state["_skip_step"] = set()  # §505/§506 + §500.7 turn-structure flags are per-turn
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
