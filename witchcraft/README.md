# witchcraft — a python-chess-style API for an MTG rules engine

One stateful object holds the whole game; you read the legal moves, push one, pop to take it back — the
[python-chess](https://python-chess.readthedocs.io/) shape, but with Magic's vocabulary (players, turns,
steps, the stack, and the zones — battlefield, hand, library, graveyard, command zone).

## Quickstart

Run from the repository root (see the packaging note below).

```python
import witchcraft

g = witchcraft.Game()                    # the demo Gruul-vs-Dimir matchup (real cards), seed 0
g.legal_moves                            # the action tuples you may play now
g.push(g.legal_moves[0])                 # make a move; advances to the next decision point
g.pop()                                  # take it back (no snapshots needed — env.step is pure)

g.turn                                   # whose decision it is
g.life()                                 # {'alice': 20, 'bob': 20}
g.battlefield()                          # {permanent: controller}
g.hand('alice')                          # ['grizzly_bears_6', ...]
g.is_game_over(), g.outcome(), g.result()

while not g.is_game_over():              # a trivial "first legal option" policy
    g.push(g.legal_moves[0])
```

A **move** is an action tuple from the referee (`env.legal_actions`):

| tuple | meaning |
|---|---|
| `("cast", player, spell, {choices})` | cast a spell with its forced sub-choices (mode/target/name) |
| `("cast_commander", player, name)` | cast the commander from the command zone (§903.6) |
| `("activate", player, ability_row, {...})` | activate an ability |
| `("attack", frozenset(attackers))` | declare attackers |
| `("block", frozenset((blocker, attacker)))` | declare blockers |
| `("pass",)` | pass priority / end the window |

`Game.describe_move(move)` renders any of these to a short label.

### Keys for tree search

cast/activate moves carry a `{choices}` dict, so the **raw tuple is not hashable**. For tree modeling:

```python
witchcraft.Game.move_key(move)   # hashable canonical move (key a policy / visited table on this)
g.key()                          # hashable transposition key of the POSITION (driver._facts_key)
g.copy()                         # cheap, independent branch — push/pop without touching the parent
g.push(move, checked=False)      # skip the legality re-check in hot loops

with g.branch(move):             # push on enter, pop on exit — for recursive tree walks
    visit(g.key())               # g is the child here; restored to the parent on exit (even on exception)
```

`g.key()` keys a transposition table / repetition set directly; equal keys denote engine-equivalent states.
`branch()` is pure push/pop ergonomics — it does **not** use the engine's incremental rollback (measured
~1.0× for this sorcery-speed move space; the tree is too narrow to amortize — see `incremental/README.md`).

### Inspecting cards (the `piece_at` analog)

Zone accessors return opaque ids; `card()`/`permanents()` return their derived characteristics:

```python
g.card("grizzly_bears_6")
# {'id':..., 'zone':'battlefield', 'controller':'alice', 'types':['creature'], 'subtypes':['bear'],
#  'colors':['green'], 'is_creature':True, 'power':2, 'toughness':2, 'keywords':[],
#  'tapped':False, 'summoning_sick':False}

g.permanents()                       # views for everything on the battlefield (one engine eval for all)
g.permanents(player="alice")         # filter by controller
g.permanents(type="creature")        # filter by printed type
```

Power/toughness/creature-ness/keywords/control are the engine's **derived** values (effects applied);
types/colors/subtypes are printed. Fields that don't apply (controller off the battlefield, P/T of a land)
come back `None`/`[]`/`False`.

### Serialization (the FEN analog)

Persist a position to JSON and reconstruct an engine-equivalent game — **future play included**, since the
RNG position is preserved:

```python
blob = g.serialize()                 # JSON str (round-trips sets/tuples/dicts/RNG that JSON can't hold)
g2 = witchcraft.Game.deserialize(blob)
assert g2.key() == g.key()           # same position; replaying the same moves yields identical games

g3 = witchcraft.Game.from_state(other_game.state)   # wrap a raw state dict in-memory (no JSON)
```

Captures the **position only**, not the move history (so `pop()` can't cross the boundary, like a chess
FEN) or an installed `policies` seam (re-supply it on the rebuilt game if needed).

### Readable names

Engine ids/slugs are underscored (`grizzly_bears_6`); recover the printed name (with MTG's casing) anywhere:

```python
g.name("grizzly_bears_6")        # 'Grizzly Bears'
g.name(commander_id)             # 'Magda, Brazen Outlaw'  (corpus-correct, not naive title-case)
g.card(id)["name"]               # card()/permanents() views include a 'name' field
g.describe(move)                 # 'alice: cast Grizzly Bears'  (vs Game.describe_move(move), id-form)
```

### Imperfect information (what one seat sees)

```python
obs = g.observation("alice")     # a redacted, READ-ONLY Game from alice's seat (§103)
obs.hand("alice")                # alice's real hand
obs.hand("bob")                  # [] — hidden; but obs.hand_count("bob") gives the true size
obs.library_size("bob")          # true count (rows hidden, count carried)
obs.battlefield(); obs.life()    # public info kept
obs.library_top()                # cards this seat scried/looked at, in order (else [])
obs.push(...)                    # RuntimeError — observations are read-only (legality lives on the true game)
```

This is the view an agent should reason over to "play like a real player." Note terminal/turn bookkeeping
(`_loser`, `_turn`) is redacted, so `is_game_over()`/`turn_number` aren't meaningful on an observation — it's
a snapshot for reasoning about an in-progress position, not for driving.

### Engine backend

```python
g = witchcraft.Game(incremental=True)   # in-process incremental update backend; g.incremental reports if it engaged
witchcraft.engine_available()           # 'incremental' | 'native' | 'inproc' | 'interpreter'
```

`incremental=True` is byte-identical to the default backend (verified across full games), ~2× faster on
large states and neutral on small. It's a **process-global** selection (the driver reads it per eval) and
degrades gracefully — if the souffle fork isn't built it warns and falls back.

## Variants

```python
import game as _setup
g = witchcraft.Game(_setup.COMMANDER_DECKS, variant="commander", seed=1,
                    commanders=_setup.COMMANDERS)   # §903: 40 life, command zone
witchcraft.self_play(seed=7)                          # full random game -> winner
```

## How it sits on the engine

```
datalog/engine_rules.dl   derives the consequences of a state   — the rules
driver.py                 applies them, owns chance/choice       — the shim
env.py                    enumerates legal actions, steps PURELY — the referee
game.py (top level)       builds a real game (decks, mulligan)   — the setup
witchcraft.Game           a stateful object wrapping all of it   — this package
```

## Packaging status

This package is the **pure-Python API layer**. It currently imports the engine modules from the repository
root and the engine resolves its data files (`datalog/engine_rules.dl`, the souffle fork) **relative to the
current working directory** — so for now, **run from the repo root**. Making it `pip install witchcraft`-clean
(path-independent data lookups + a prebuilt `.so`/wheel so no C++ toolchain is needed) is deferred and
tracked in [`PACKAGING.md`](../PACKAGING.md).

Check which engine backend built on your machine:

```python
witchcraft.engine_available()   # 'incremental' | 'native' | 'inproc' | 'interpreter'
```
