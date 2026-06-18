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
```

`g.key()` keys a transposition table / repetition set directly; equal keys denote engine-equivalent states.

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
