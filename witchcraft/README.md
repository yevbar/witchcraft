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

## Players

A `Player` answers the two kinds of decision the engine raises, both wired through the shim: a **top-level
move** (`choose_move(game)` — pick from `game.legal_moves`) and an internal **sub-choice**
(`decide(view, key, options, default)` — a target/mode/blocks/discard resolved inside `step`). `play()` runs
a full game with each seat driven by its player; a seat's player drives *both* its moves and its sub-choices.

```python
from witchcraft import Player, RandomPlayer, GreedyPlayer, play

result = play({"alice": RandomPlayer(), "bob": GreedyPlayer()}, seed=7)
result.outcome()          # ('alice', 'bob lost')

# a custom heuristic — subclass Player, override choose_move; you get the whole Game
class Aggro(Player):
    def choose_move(self, game):
        atk = [m for m in game.legal_moves if m[0] == "attack"]
        return max(atk, key=lambda m: len(m[1])) if atk else game.legal_moves[0]
    # (optionally also override decide() to steer targets/modes/blocks)

play({"alice": Aggro(), "bob": RandomPlayer(seed=1)}, variant="two-player", seed=3)
```

- **`RandomPlayer(seed=None)`** — uniform-random; `seed=None` draws from the game's own seeded RNG (so the
  game is reproducible from its seed), an explicit `seed` gives the player its own independent RNG.
- **`GreedyPlayer`** (= base `Player`) — takes the engine's hand-tuned default at every decision; a complete,
  legal opponent with zero config.
- **Custom** — subclass `Player`. `choose_move(game)` is where heuristics/search live (use `game.copy()`,
  `game.key()`, `game.push`/`pop`, the zone accessors); override `decide()` only if you want to steer the
  nested sub-choices too.

(London mulligan resolves with the engine default — keep — before play begins.)

## Benchmarking a heuristic

Drop in a `Player` and measure it. Self-play is fast (no Forge); the seats are swapped every other game and
`Random` vs `Random` lands at ~0.50, so the win-rate is honest.

```python
from witchcraft import benchmark, RandomPlayer, Player

class MyBot(Player):
    def choose_move(self, game): ...

benchmark(MyBot(), games=50)                 # -> {'win_rate': 0.62, 'avg_turns': 14.1, 'games_per_s': 1.4, ...}
benchmark(MyBot(), RandomPlayer(seed=1))     # vs a fixed-seed baseline
```

To benchmark **against Forge** (Forge referees — the source of truth — and runs the **mirror**: witchcraft
reconstructs the board from Forge's observation stream every decision and runs in parallel):

```python
from witchcraft.benchmark import benchmark_vs_forge
benchmark_vs_forge("engine", games=10)
# -> {'bot_win_rate': 0.3, 'avg_turns': 23,
#     'mirror_modeled_frac': 0.93,   # how much of Forge's real game witchcraft could MODEL
#     'mirror_endorsed_frac': 0.17,  # how much it could independently ENDORSE as legal (the engine gaps)
#     'source_of_truth': 'forge', ...}
```

`benchmark_vs_forge` needs Forge installed and spins up one JVM per game (heavy). The two mirror fractions
are the differential-fidelity signal — high `modeled` / low `endorsed` means witchcraft *recognizes* the
board but can't yet *derive* the play, which is the precise place to extend the rules engine.

## Variants

```python
import game as _setup
g = witchcraft.Game(_setup.COMMANDER_DECKS, variant="commander", seed=1,
                    commanders=_setup.COMMANDERS)   # §903: 40 life, command zone
witchcraft.self_play(seed=7)                          # full random game -> winner
result = play({"alice": RandomPlayer(), "bob": RandomPlayer()},
              _setup.COMMANDER_DECKS, variant="commander", commanders=_setup.COMMANDERS)  # players, any variant
```

## Decks (human-readable lists)

Deck lists are plain text — `N Card Name` or just `Card Name`, one per line (`#`/`//` comments and blank
lines ignored). The names are the **human-readable oracle names the engine resolves directly** (no slug/encode
step), so you can hand-write a `.txt` and read it line by line.

```python
import witchcraft
witchcraft.bundled_decks()                          # ['izzet_prowess', 'mono_black_zombies',
                                                    #  'mono_green_landfall', 'selesnya_landfall']
deck = witchcraft.load_deck("mono_green_landfall")  # bundled name -> flat list ['Forest', 'Forest', ...]
deck = witchcraft.load_deck("/path/to/my.txt")      # ...or any file path

g = witchcraft.Game({"alice": witchcraft.load_deck("izzet_prowess"),
                     "bob":   witchcraft.load_deck("mono_black_zombies")})
witchcraft.benchmark(MyBot(), decks={"alice": witchcraft.load_deck("selesnya_landfall"),
                                     "bob":   witchcraft.load_deck("selesnya_landfall")})
```

Four 40-card, limited-style pools ship with the package (`witchcraft/decks/*.txt`): **mono_green_landfall**,
**selesnya_landfall** (GW go-wide), **izzet_prowess** (UR spells), **mono_black_zombies**. MTGO/Arena/.dck
exports also parse (the loader skips `[Section]` headers and `Name=`/metadata lines).

## Forge as the source of truth

The default engine is witchcraft (our datalog rules referee). For a mode where **Forge** is authoritative and
one seat is the **"forge player"** (Forge's own AI), use `witchcraft.forge` — the game actually runs in Forge,
and the witchcraft side connects as a bot seat (over `forge_bridge`'s socket) answering each Forge decision:

```python
import witchcraft.forge as wf
if wf.forge_available():                      # needs a built Forge fatjar + JDK 17 ($FORGE / $JDK)
    r = wf.play_forge(bot="engine")           # Forge AI vs the witchcraft 'stockfish' bot; FORGE judges
    print(r["winner"], r["turns"])            # winner is Forge's verdict — the source of truth
    # bot="random" for the baseline seat; witch_deck/opp_deck pick ForgeVsBot archetypes ('vanilla','infect')
```

This is the only faithful way to involve Forge: there is **no reverse bridge** that lets Forge's AI choose
moves inside a witchcraft `Game`, so when Forge is the truth, the game runs in Forge. It reuses the wired
`forge_integration` orchestration (`ForgeVsBot` + `run_bot`), so it spins up the JVM and can be slow /
memory-hungry; set `$JVM_HEAP` / `$GAME_TIMEOUT` on small hosts. The witchcraft bot seat is driven by an
`forge_bridge` obs-policy (`'engine'` = the win-search bot, `'random'` = baseline); a `RandomPlayer` maps to
`'random'`, any other `Player` to `'engine'`. (A custom *Game-based* `Player` can't drive a Forge seat —
Forge hands it an observation + Forge-ids, not a witchcraft state + moves; write a `forge_bridge` policy for a
custom Forge bot.)

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
