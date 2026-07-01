"""mtg — a python-chess-style API for an MTG rules engine.

    import mtg
    g = mtg.Game()                       # a ready-to-play 1v1 game (Gruul vs Dimir demo decks)
    while not g.is_game_over():
        g.push(g.legal_moves[0])
    print(g.outcome())                          # ('alice', 'bob lost')

Or drive it symbolically (python-chess's `board.push_san("e4")` analog) — build a game from decklists,
force an opening hand, and name your moves by card:

    from mtg import Card, mountain, play
    g = mtg.Game.new([mountain] * 40, starting_hand=lambda: [mountain])   # a chosen opening hand
    g.play(mountain)                            # play a land by Card (or g.push(play("Mountain")) by name)
    g.cast("Grizzly Bears")                     # cast a spell by name; ValueError if it isn't legal now

`Game.new(*decks | seat=deck, ...)` loads decklists (name lists, `{name: count}`, `Card`s, or a file/path);
`starting_hand` is a spec (or `{seat: spec}`) — a list of `Card`/names to guarantee in the opening hand, or
a callable returning `Optional[list]` (None = a normal random hand). Forcing a card the seat doesn't own
raises. `Card`, the five basic lands (`plains`/`island`/`swamp`/`mountain`/`forest`), and the `play`/`cast`
move builders are top-level exports.

The engine now lives INSIDE this package: the execution core (`mtg.driver`, `mtg.sim`, the `mtg.engine_*`
souffle backends, `mtg.bridge_to_engine`) and the agent-facing layer (`mtg.engine.env`, `mtg.engine.observe`,
`mtg.engine.search`/`win_search`, `mtg.engine.game`, `mtg.engine.engine`). It drives the Datalog build the
way python drives a stockfish binary; the English->Datalog transpiler that PRODUCES that build is the separate
`interpreter` package (mtg imports a little of it at build time — being decoupled separately).

This top-level API is shaped like python-chess and is LAZY (PEP 562): importing a low-level module such as
`from mtg import driver` stays light; the names below load their submodule (and pydantic/value-nets/JVM
tooling) only on first access.

PACKAGING NOTE (deferred): the engine resolves its rules file (`datalog/engine_rules.dl`) and the souffle
fork RELATIVE to the repo root, so for now run from there. Shipping the compiled `.so`/wheel (importlib.
resources) is tracked separately — see PACKAGING.md. Importing this package puts the repo root and packages/
on sys.path; only the data-file lookups remain cwd-bound.
"""

from __future__ import annotations

import os
import sys

# This package lives in packages/mtg/. Put both packages/ (so `mtg.*` resolves) and the repo root (which
# holds interpreter/ and the cwd-relative datalog/ build) on the path, no matter where Python was started.
_PKGS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # packages/
_ROOT = os.path.dirname(_PKGS)                                        # repo root
for _p in (_ROOT, _PKGS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# LAZY public API (PEP 562). The engine execution core (driver, sim, the souffle backends,
# bridge_to_engine) and the agent layer (mtg.engine.*) now live IN this package, so importing a low-level
# module (`from mtg import driver`) must NOT eagerly boot the python-chess API (which pulls pydantic, the
# value nets, JVM tooling, ...). Each public name below loads its submodule on first access instead.
import importlib  # noqa: E402

_LAZY = {
    "Game": ".game", "DEMO_DECKS": ".game",
    "Move": ".models", "Pass": ".models", "Permanent": ".models", "CardRef": ".models",
    "Card": ".models", "MoveSpec": ".models", "cast": ".models",
    "plains": ".models", "island": ".models", "swamp": ".models", "mountain": ".models", "forest": ".models",
    "Player": ".players", "RandomPlayer": ".players", "GreedyPlayer": ".players", "play": ".players",
    "InformationPlayer": ".information",
    "play_forge": ".forge", "forge_available": ".forge", "forge_status": ".forge",
    "benchmark": ".benchmark", "benchmark_vs_forge": ".benchmark",
    "load_deck": ".decks", "parse_deck": ".decks", "bundled_decks": ".decks",
    "read_cards": ".decks", "read_arena_cards": ".decks",
    "find_best_deck": ".deckbuilding",
    "ReBeLPlayer": ".rebel", "heuristic_value": ".rebel",
    "cards": ".cards", "CardCorpus": ".cards",
    "anything": ".predicates", "is_creature": ".predicates", "is_artifact": ".predicates",
    "is_enchantment": ".predicates", "is_instant": ".predicates", "is_sorcery": ".predicates",
    "is_land": ".predicates", "is_planeswalker": ".predicates", "is_battle": ".predicates",
    "is_permanent": ".predicates", "is_mana_rock": ".predicates", "is_commander_cast": ".predicates",
    "is_draw_ability": ".predicates", "is_cantrip": ".predicates", "is_creature_damage": ".predicates",
    "creature_damage": ".predicates",
}


def __getattr__(name):                                                  # PEP 562 module-level lazy attrs
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    val = getattr(importlib.import_module(mod, __name__), name)
    globals()[name] = val                                              # cache so __getattr__ fires once
    return val


def __dir__():
    return sorted(list(globals()) + list(_LAZY))


__all__ = ["Game", "DEMO_DECKS", "Move", "Pass", "Permanent", "CardRef",
           "Card", "MoveSpec", "cast", "plains", "island", "swamp", "mountain", "forest",
           "Player", "RandomPlayer", "GreedyPlayer", "InformationPlayer", "play",
           "ReBeLPlayer", "heuristic_value", "cards", "CardCorpus",
           "play_forge", "forge_available", "forge_status", "benchmark", "benchmark_vs_forge",
           "load_deck", "parse_deck", "bundled_decks", "read_cards", "read_arena_cards", "find_best_deck",
           "anything", "is_creature", "is_artifact", "is_enchantment", "is_instant", "is_sorcery", "is_land",
           "is_planeswalker", "is_battle", "is_permanent", "is_mana_rock", "is_commander_cast",
           "is_draw_ability", "is_cantrip", "is_creature_damage", "creature_damage",
           "new_game", "self_play", "demo", "engine_available", "__version__"]
__version__ = "0.1.0"


def new_game(decks: dict | None = None, **kw) -> Game:
    """Construct a Game (alias for `Game(...)`, for users who prefer a factory function)."""
    return Game(decks, **kw)


def self_play(decks: dict | None = None, *, variant: str = "two-player", seed: int = 0,
              policy=None, max_moves: int = 4000) -> str | None:
    """Play a full game to a terminal state with `policy` choosing each move, returning the winner.
    `policy(game) -> move` picks from `game.legal_moves`; defaults to uniform-random over the legal moves."""
    from mtg.engine import game as _setup
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
    from mtg.engine import game as _setup
    g = Game(seed=seed)
    while not g.is_game_over() and g.legal_moves:
        g.push(_setup.random_policy(g.state, "action", g.legal_moves, g.legal_moves[0]))
    return g


def engine_available() -> str:
    """Which engine backend is live: 'incremental', 'native' (compiled), 'inproc' (.so), or 'interpreter'
    (souffle interpreter fallback). A quick way to confirm the C++ half built on this machine."""
    from mtg import engine_native
    from mtg import engine_inproc
    if os.environ.get("MTG_INCREMENTAL"):
        from mtg import engine_incremental
        if engine_incremental.available():
            return "incremental"
    if not os.environ.get("MTG_NO_INPROC") and engine_inproc.available():
        return "inproc"
    if not os.environ.get("MTG_NO_NATIVE") and engine_native.available():
        return "native"
    return "interpreter"
