"""witchcraft — a python-chess-style API for an MTG rules engine.

    import witchcraft
    g = witchcraft.Game()                       # a ready-to-play 1v1 game (Gruul vs Dimir demo decks)
    while not g.is_game_over():
        g.push(g.legal_moves[0])
    print(g.outcome())                          # ('alice', 'bob lost')

The engine itself (datalog rules + the souffle backends) lives at the repository root as top-level modules
(`driver`, `env`, `game`, `bridge_to_engine`, the `engine_*` backends). This package is a thin, stateful
object layer over them — the part a user actually imports, shaped like python-chess.

PACKAGING NOTE (deferred): the engine still resolves its rules file (`datalog/engine_rules.dl`) and the
souffle fork RELATIVE to the current working directory, so for now run from the repository root. Making the
engine path-independent (importlib.resources) and shipping the compiled `.so`/wheel is tracked separately —
see PACKAGING.md. Importing this package adds the repo root to sys.path so `import driver` works from any
cwd; only the data-file lookups remain cwd-bound.
"""

from __future__ import annotations

import os
import sys

# The repo root (this package's parent) holds the engine modules. Put it on the path so `import driver`
# resolves no matter where Python was started — the one cwd-independence we can buy without restructuring.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from .game import Game, DEMO_DECKS                                      # noqa: E402
from .players import Player, RandomPlayer, GreedyPlayer, play           # noqa: E402
from .forge import play_forge, forge_available                         # noqa: E402  (lazy JVM tooling inside)

__all__ = ["Game", "DEMO_DECKS", "Player", "RandomPlayer", "GreedyPlayer", "play",
           "play_forge", "forge_available",
           "new_game", "self_play", "demo", "engine_available", "__version__"]
__version__ = "0.1.0"


def new_game(decks: dict | None = None, **kw) -> Game:
    """Construct a Game (alias for `Game(...)`, for users who prefer a factory function)."""
    return Game(decks, **kw)


def self_play(decks: dict | None = None, *, variant: str = "two-player", seed: int = 0,
              policy=None, max_moves: int = 4000) -> str | None:
    """Play a full game to a terminal state with `policy` choosing each move, returning the winner.
    `policy(game) -> move` picks from `game.legal_moves`; defaults to uniform-random over the legal moves."""
    import game as _setup
    if policy is None:
        def policy(g: Game):
            return _setup.random_policy(g.state, "action", g.legal_moves, g.legal_moves[0])
    g = Game(decks, variant=variant, seed=seed)
    for _ in range(max_moves):
        if g.is_game_over() or not g.legal_moves:
            break
        g.push(policy(g))
    return g.winner()


def demo(seed: int = 7) -> Game:
    """Play a quick random self-play game and return the finished Game (inspect `.outcome()`, `.life()`)."""
    import game as _setup
    g = Game(seed=seed)
    while not g.is_game_over() and g.legal_moves:
        g.push(_setup.random_policy(g.state, "action", g.legal_moves, g.legal_moves[0]))
    return g


def engine_available() -> str:
    """Which engine backend is live: 'incremental', 'native' (compiled), 'inproc' (.so), or 'interpreter'
    (souffle interpreter fallback). A quick way to confirm the C++ half built on this machine."""
    import engine_native
    import engine_inproc
    if os.environ.get("MTG_INCREMENTAL"):
        import engine_incremental
        if engine_incremental.available():
            return "incremental"
    if not os.environ.get("MTG_NO_INPROC") and engine_inproc.available():
        return "inproc"
    if not os.environ.get("MTG_NO_NATIVE") and engine_native.available():
        return "native"
    return "interpreter"
