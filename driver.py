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

import engine_native        # compiled-binary backend; falls back to the interpreter if unavailable

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


def _create_token(state: dict, name: str, controller: str, n: int) -> None:
    d = TOKEN_DEFS.get(name, {"types": ["creature"]})
    for _ in range(n):
        state["_tok"] = state.get("_tok", 0) + 1
        tid = f"{name}#{state['_tok']}"
        state.setdefault("on_battlefield", set()).add((tid,))             # printed_* only; the engine
        state.setdefault("printed_control", set()).add((controller, tid)) # derives controls/has_type/creature
        for t in d["types"]:
            state.setdefault("printed_type", set()).add((tid, t))
        if "pt" in d:
            state.setdefault("printed_power", set()).add((tid, d["pt"][0]))
            state.setdefault("printed_toughness", set()).add((tid, d["pt"][1]))
        print(f"    {controller} creates a {name} token ({tid})")


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
        elif eff == "add_counter":                           # tgt = counter kind (p1p1/m1m1), on the source
            _bump_counter(state, src, tgt, n)
            print(f"    trigger {a}: {src} gets {n} {tgt} counter(s)")
        elif eff == "create_token":                          # tgt = predefined token name
            _create_token(state, tgt, ctrl, n)


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
    attackers = sorted(c for (c,) in run(state, ["may_attack"])["may_attack"])
    state["attacks"] = {(c, opp) for c in attackers}
    if attackers:
        print(f"    {ap} attacks {opp} with {', '.join(attackers)}")


def declare_blockers(state: dict, ap: str) -> None:
    """§509 — the defending player blocks attackers one-for-one with its creatures."""
    opp = _others(state, ap)[0]
    attackers = sorted(a for (a, _) in state.get("attacks", set()))
    blockers = _creatures_of(state, opp)
    state["blocks"] = {(b, a) for b, a in zip(blockers, attackers)}
    for b, a in sorted(state["blocks"]):
        print(f"    {opp} blocks {a} with {b}")


def _draw(state: dict, p: str) -> bool:
    """Active player draws the top of their library; False if the library is empty
    (§104.3c — that player loses the game)."""
    lib = sorted(c for (pp, c) in state.get("in_library", set()) if pp == p)
    if not lib:
        return False
    state["in_library"].discard((p, lib[0]))
    state.setdefault("in_hand", set()).add((p, lib[0]))
    print(f"    {p} draws {lib[0]}")
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


def _cast_phase(state: dict, ap: str) -> None:
    """§601 -> §608 (sorcery-speed): the active player casts each spell it can. Each goes on the
    stack and resolves; a creature becomes a permanent (applying §614 ETB replacements), and a
    spell whose targets are all illegal fizzles (§608.2b). The engine derives can_cast / resolves
    / fizzles / enters_*; the driver just moves the spell and applies the result."""
    state["has_priority"] = {(ap,)}                          # §601 active player has priority in its main phase
    while True:
        castable = sorted(s for (p, s) in run(state, ["can_cast"])["can_cast"] if p == ap)
        if not castable:
            break
        spell = castable[0]
        state["in_hand"].discard((ap, spell))
        state["on_stack"], state["all_passed"] = {(spell, 0)}, {("yes",)}   # cast; no responses here
        out = run(state, ["fizzles", "enters_battlefield", "enters_tapped", "enters_with_counter"])
        state["on_stack"], state["all_passed"] = set(), set()
        if (spell,) in out["fizzles"]:
            print(f"    {ap} casts {spell} -> fizzles (no legal target)")
            continue
        print(f"    {ap} casts {spell} -> resolves")
        if (spell,) in out["enters_battlefield"]:
            state["on_battlefield"].add((spell,))
            state.setdefault("printed_control", set()).add((ap, spell))
            if (spell,) in out["enters_tapped"]:
                state.setdefault("tapped", set()).add((spell,)); print(f"      {spell} enters tapped")
            for (c, k, n) in sorted(out["enters_with_counter"]):
                if c == spell:
                    _bump_counter(state, spell, k, int(n)); print(f"      {spell} enters with {n} {k} counter")
    state["has_priority"] = set()


def _end_of_turn(state: dict) -> None:
    """§514.2 cleanup — until-end-of-turn continuous effects end (the driver removes them)."""
    for (e,) in run(state, ["ends_at_cleanup"])["ends_at_cleanup"]:
        for rel in [k for k in state if k.startswith("eff_")]:
            state[rel] = {row for row in state[rel] if row and row[0] != e}


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
