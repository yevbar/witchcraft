"""search.py — a small, reviewable state-space layer over the Datalog engine.

The driver runs ONE line of play with a fixed policy. This adds the three primitives a
Python model needs to look ahead, branch, and detect loops exactly as the rules define
them (§732.1b/§732.3 "the same game state being reached multiple times"; §732.4 a loop of
mandatory actions is a draw):

    legal_moves(state)   -> [move]      the choices available now (engine-derived)
    apply(state, move)   -> state'      a PURE transition — never mutates `state`
    canonical_key(state) -> hashable    a state's identity up to object renaming (α-equivalence)

and, composed from them:

    find_loop(state, depth)  searches up to `depth` plies for a line that returns to a
    structurally-equivalent board with non-decreasing resources ("enough fuel to repeat") —
    a repeatable/infinite loop, with the cycle of moves and the per-iteration resource gain.

Honesty about scope: the move space is only as rich as the engine. Today that is
sorcery-speed casting + step advancement; the engine does not yet model activated/mana
abilities or the stack interaction most real combos use, so find_loop searches that thinner
space. The three primitives are written to extend unchanged as the engine grows — only
legal_moves/apply gain new move kinds. Combat uses the driver's fixed greedy policy.

The engine transition (driver.run) is a pure function of state, which is what makes apply
sound to branch and makes canonical_key usable as a transposition key.
"""

from __future__ import annotations

import contextlib
import copy
import io

import driver

# Resource "fuel gauges" excluded from the structural board key and tracked separately:
# two states with the same board but different life/mana are the SAME board, more/less fuel.
FUEL = ("life", "mana_available")


def _clone(state: dict) -> dict:
    return copy.deepcopy(state)


def _active(state: dict) -> str:
    return next(iter(state["active_player"]))[0]


def _sole(s: set) -> str:
    return next(iter(s))[0]


# --- canonical_key: α-equivalence over object identities (Weisfeiler-Lehman colour refinement) ---
#
# A renamable OBJECT is an entity that occupies a zone — the rules' own notion of object
# identity (every object is in exactly one zone). Everything else stays a fixed literal:
# players (seats), integers (scalars), and the rules VOCABULARY symbols (step names, card
# types, zones, counter kinds, keywords). So two states that differ only by a consistent
# renaming of objects get the SAME key — a loop that mints a fresh token id each iteration is
# recognised as returning to an equivalent state — while precombat_main and combat stay
# distinct. Sound up to the usual WL limitation (it can fail to separate highly symmetric
# non-isomorphic boards — a rare, documented false match).

# (relation, position) slots that hold an object's identity — the zones an object can occupy.
_ZONE_SLOTS = [("on_battlefield", 0), ("on_stack", 0), ("graveyard", 0),
               ("exile", 0), ("in_hand", 1), ("in_library", 1)]


def _objects(state: dict, exclude: tuple) -> set:
    objs = set()
    for rel, pos in _ZONE_SLOTS:
        if rel in exclude:
            continue
        for row in state.get(rel, ()):
            if isinstance(row, tuple) and len(row) > pos and not isinstance(row[pos], int):
                objs.add(row[pos])
    return objs


def _tok(x, objs, color):
    if isinstance(x, int):
        return ("n", x)
    if x in objs:
        return ("o", color[x])             # renamable object -> its canonical colour
    return ("s", x)                         # fixed literal: player / step / type / zone / kind / keyword


def canonical_key(state: dict, exclude: tuple = ()) -> tuple:
    rels = {k: v for k, v in state.items()
            if isinstance(v, (set, frozenset)) and k not in exclude}
    objs = _objects(state, exclude)
    color = {o: 0 for o in objs}
    for _ in range(len(objs) + 1):                      # refine until stable (<= |objs| rounds)
        sig: dict = {o: [] for o in objs}
        for r, rows in rels.items():
            for row in rows:
                for i, x in enumerate(row):
                    if x in color:                      # x is an object: record its incident edge
                        others = tuple((j, _tok(y, objs, color))
                                       for j, y in enumerate(row) if j != i)
                        sig[x].append((r, i, others))
        buckets: dict = {}
        for o in objs:
            buckets.setdefault(tuple(sorted(repr(s) for s in sig[o])), []).append(o)
        new = {o: idx for idx, kb in enumerate(sorted(buckets)) for o in buckets[kb]}
        if new == color:
            break
        color = new
    facts = [(r, tuple(_tok(x, objs, color) for x in row))
             for r, rows in rels.items() for row in rows]
    return tuple(sorted(repr(f) for f in facts))


def structural_key(state: dict) -> tuple:
    """Canonical key of the BOARD only — the fuel gauges (life, mana) are abstracted out."""
    return canonical_key(state, exclude=FUEL)


# --- resources: the per-player "fuel" a loop must not deplete to be repeatable --------------

def resources(state: dict) -> dict:
    """player -> (life, mana, hand size, library size). The data a 'has enough fuel to repeat'
    check compares between two equivalent boards."""
    out = {}
    for (p,) in state.get("is_player", set()):
        life = next((v for (q, v) in state.get("life", set()) if q == p), 0)
        mana = next((v for (q, v) in state.get("mana_available", set()) if q == p), 0)
        hand = sum(1 for (q, _) in state.get("in_hand", set()) if q == p)
        lib = sum(1 for (q, _) in state.get("in_library", set()) if q == p)
        out[p] = (life, mana, hand, lib)
    return out


def _dominates(a: dict, b: dict) -> bool:
    """True if `a` is componentwise >= `b` for every player — nothing got worse."""
    return all(all(x >= y for x, y in zip(a[p], b[p])) for p in b)


def _delta(a: dict, b: dict) -> dict:
    return {p: tuple(x - y for x, y in zip(a[p], b[p])) for p in b}


# --- legal_moves / apply: the (currently thin) move space, exposed as choices ----------------

def legal_moves(state: dict) -> list:
    """The active player's choices now: cast each castable spell (engine-derived can_cast), or
    pass priority to advance the step. Empty once a player has lost (terminal state)."""
    if state.get("_loser"):
        return []
    ap = _active(state)
    probe = _clone(state)
    probe["has_priority"] = {(ap,)}                      # can_cast requires priority
    castable = sorted(s for (p, s) in driver.run(probe, ["can_cast"])["can_cast"] if p == ap)
    return [("cast", ap, s) for s in castable] + [("pass",)]


def _cast_one(s: dict, ap: str, spell: str) -> None:
    """Cast one spell sorcery-speed and resolve it — the single-spell core of driver._cast_phase."""
    s["has_priority"] = {(ap,)}
    s["in_hand"].discard((ap, spell))
    s["on_stack"], s["all_passed"] = {(spell, 0)}, {("yes",)}
    out = driver.run(s, ["fizzles", "enters_battlefield", "enters_tapped", "enters_with_counter"])
    s["on_stack"], s["all_passed"] = set(), set()
    if (spell,) not in out["fizzles"] and (spell,) in out["enters_battlefield"]:
        s["on_battlefield"].add((spell,))
        s.setdefault("printed_control", set()).add((ap, spell))
        if (spell,) in out["enters_tapped"]:
            s.setdefault("tapped", set()).add((spell,))
        for (c, k, n) in out["enters_with_counter"]:
            if c == spell:
                driver._bump_counter(s, spell, k, int(n))
    s["has_priority"] = set()


def _pass(s: dict) -> None:
    """Pass priority: run the engine, apply what it derives, advance the step (or pass the turn).
    Mirrors one iteration of driver.play_game's inner loop, minus the auto-cast (that's a choice)."""
    ap = _active(s)
    step = _sole(s["current_step"])
    if step == "declare_attackers":
        driver.declare_attackers(s, ap)
    elif step == "declare_blockers":
        driver.declare_blockers(s, ap)
    if step == "cleanup":
        driver._end_of_turn(s)
    out = driver.run(s, driver.OUTPUTS)
    loser = driver._apply_outputs(s, out, ap)
    if loser:
        s["_loser"] = loser
    if out["advance_to"]:
        s["current_step"] = out["advance_to"]
    else:                                               # past cleanup -> pass the turn (§500.6)
        players = sorted(p for (p,) in s["is_player"])
        nxt = players[(players.index(ap) + 1) % len(players)]
        s["active_player"] = {(nxt,)}
        s["current_step"] = {("untap",)}
        s["attacks"], s["blocks"] = set(), set()


def apply(state: dict, move: tuple) -> dict:
    """Pure transition: return the state after `move`, leaving `state` untouched."""
    s = _clone(state)
    with contextlib.redirect_stdout(io.StringIO()):     # the driver helpers narrate; stay quiet here
        if move[0] == "cast":
            _cast_one(s, move[1], move[2])
        else:
            _pass(s)
    return s


# --- find_loop: a repeatable cycle = equivalent board reached with non-decreasing fuel --------

def find_loop(state: dict, depth: int = 5, moves_fn=legal_moves, apply_fn=apply) -> dict | None:
    """Search up to `depth` plies for a line of play that returns to a structurally-equivalent
    board whose resources dominate the earlier visit ('enough fuel to repeat'). Returns the
    cycle's moves and per-iteration resource gain, or None. A zero gain is an exact cycle
    (the §732.4 mandatory-loop / declare-N case); a positive gain is a net-positive engine.

    moves_fn/apply_fn default to the engine's; they're injectable so the cycle logic can be
    exercised on a toy transition system independent of the (currently thin) engine move space."""
    ancestors: dict = {}                                # struct_key -> (resources, path_index)
    path: list = []

    def dfs(s: dict, d: int) -> dict | None:
        sk = structural_key(s)
        res = resources(s)
        if sk in ancestors:
            res0, pi = ancestors[sk]
            if _dominates(res, res0):
                return {"loop_moves": path[pi:], "gain": _delta(res, res0)}
        if d == 0 or s.get("_loser"):
            return None
        ancestors[sk] = (res, len(path))
        for mv in moves_fn(s):
            path.append(mv)
            found = dfs(apply_fn(s, mv), d - 1)
            path.pop()
            if found:
                return found
        del ancestors[sk]
        return None

    return dfs(_clone(state), depth)


def _demo() -> None:
    base = {
        "current_step": {("precombat_main",)},
        "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",)},
        "life": {("alice", 20), ("bob", 20)},
        "in_hand": {("alice", "wolf")},
        "spell_type": {("wolf", "creature")},
        "mana_cost": {("wolf", 2)},
        "mana_available": {("alice", 2)},
        "on_battlefield": set(), "tapped": set(),
        "attacks": set(), "blocks": set(), "counter": set(),
        "in_library": {("alice", f"a{i}") for i in range(3)} | {("bob", f"b{i}") for i in range(3)},
    }

    print("1) canonical_key — α-equivalence over object identities")
    a = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("wolf#1",)},
         "printed_control": {("alice", "wolf#1")}, "printed_type": {("wolf#1", "creature")}}
    b = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("wolf#7",)},
         "printed_control": {("alice", "wolf#7")}, "printed_type": {("wolf#7", "creature")}}
    c = copy.deepcopy(b); c["on_battlefield"].add(("wolf#8",))  # an extra token -> different board
    print(f"   renamed token equal? {canonical_key(a) == canonical_key(b)}   "
          f"different board equal? {canonical_key(a) == canonical_key(c)}")

    print("2) legal_moves + apply (purity)")
    moves = legal_moves(base)
    print(f"   moves: {moves}")
    nxt = apply(base, ("cast", "alice", "wolf"))
    print(f"   after cast: wolf on battlefield? {('wolf',) in nxt['on_battlefield']}   "
          f"original hand intact? {('alice', 'wolf') in base['in_hand']}")

    print("3) repeat certificate — equivalent board + non-decreasing fuel")
    early = {"is_player": {("alice",)}, "on_battlefield": {("rock",)},
             "mana_available": {("alice", 1)}, "life": {("alice", 20)}}
    late = {"is_player": {("alice",)}, "on_battlefield": {("rock",)},
            "mana_available": {("alice", 3)}, "life": {("alice", 20)}}   # +2 mana, same board
    ok = structural_key(early) == structural_key(late) and _dominates(resources(late), resources(early))
    print(f"   same board, +2 mana -> repeatable? {ok}")

    print("4) find_loop on the modeled (sorcery-speed) move space")
    loop = find_loop(base, depth=4)
    print(f"   {loop if loop else 'no repeatable loop (casting consumes hand; drawing consumes library) '
                                 '— expected until activated/mana abilities are modeled'}")

    print("5) find_loop fires on a real cycle (toy 'mana rock': +1 mana, board unchanged)")
    rock = {"is_player": {("alice",)}, "on_battlefield": {("rock",)},
            "mana_available": {("alice", 0)}, "life": {("alice", 20)},
            "in_hand": set(), "in_library": set()}

    def toy_moves(s):
        return [("tap_rock",)]

    def toy_apply(s, _mv):
        s = copy.deepcopy(s)
        mana = next(v for (q, v) in s["mana_available"] if q == "alice")
        s["mana_available"] = {("alice", mana + 1)}
        return s

    print(f"   {find_loop(rock, depth=2, moves_fn=toy_moves, apply_fn=toy_apply)}")


if __name__ == "__main__":
    _demo()
