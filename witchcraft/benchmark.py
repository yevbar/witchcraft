"""witchcraft.benchmark — measure a heuristic's strength, conveniently.

Two harnesses:

  benchmark(player, opponent)        — fast SELF-PLAY in the witchcraft engine. Drop in any Player, get a
                                       win-rate vs a baseline (RandomPlayer by default). No Forge, no JVM.
  benchmark_vs_forge(bot)            — play the witchcraft bot against FORGE's AI with Forge refereeing
                                       (the source of truth), aggregating win-rate over games. Also runs the
                                       MIRROR: a parallel witchcraft state reconstructed from Forge's stream,
                                       so you get witchcraft's coverage of the real game alongside the result.

The point: developing a custom heuristic is `class MyBot(Player): def choose_move(self, game): ...`, then
`benchmark(MyBot())`. Validating against Forge is `benchmark_vs_forge(...)`.
"""

from __future__ import annotations

import contextlib
import io
import time


def benchmark(player, opponent=None, *, games: int = 20, variant: str = "two-player", seed: int = 0,
              decks: dict | None = None, commanders: dict | None = None, incremental: bool = False,
              max_moves: int = 4000, swap_seats: bool = True) -> dict:
    """Play `player` vs `opponent` (default RandomPlayer) over `games` witchcraft self-play games and report
    `player`'s record. Seats are swapped every other game (so a first-player edge doesn't bias the result),
    and each game uses a distinct seed (`seed + i`). Returns
    {games, wins, losses, draws, win_rate, avg_turns, wall_s, games_per_s}.

        from witchcraft import benchmark, RandomPlayer, Player
        class MyBot(Player):
            def choose_move(self, game): ...
        benchmark(MyBot(), games=50)                 # -> {'win_rate': 0.62, 'avg_turns': 14.1, ...}
        benchmark(MyBot(), RandomPlayer(seed=1))     # vs a fixed-seed baseline
    """
    from .players import RandomPlayer, play
    if opponent is None:
        opponent = RandomPlayer()
    wins = losses = draws = 0
    total_turns = 0
    t0 = time.perf_counter()
    for i in range(games):
        flip = swap_seats and (i % 2 == 1)
        players = {"alice": opponent, "bob": player} if flip else {"alice": player, "bob": opponent}
        mine = "bob" if flip else "alice"
        with contextlib.redirect_stdout(io.StringIO()):                # the engine narrates each step — mute it
            g = play(players, decks, variant=variant, seed=seed + i, commanders=commanders,
                     incremental=incremental, max_moves=max_moves)
        w = g.winner()
        total_turns += g.turn_number
        if w == mine:
            wins += 1
        elif w is None:
            draws += 1
        else:
            losses += 1
    wall = time.perf_counter() - t0
    return {
        "games": games, "wins": wins, "losses": losses, "draws": draws,
        "win_rate": round(wins / games, 3) if games else 0.0,
        "avg_turns": round(total_turns / games, 1) if games else 0.0,
        "wall_s": round(wall, 2), "games_per_s": round(games / wall, 2) if wall else 0.0,
    }


def benchmark_vs_forge(bot="engine", *, games: int = 5, witch_deck: str = "vanilla",
                       opp_deck: str = "vanilla", timeout: int = 120) -> dict:
    """Benchmark the witchcraft bot against FORGE's AI with Forge refereeing (the source of truth), over
    `games`. Returns the bot's record vs Forge PLUS the MIRROR signal: as the engine bot plays, it
    reconstructs the board from Forge's observation stream every decision and runs witchcraft in parallel,
    so `mirror_modeled_frac`/`mirror_endorsed_frac` report how much of the authoritative Forge game
    witchcraft could model/endorse — a direct, game-by-game fidelity benchmark against Forge.

        from witchcraft.benchmark import benchmark_vs_forge
        benchmark_vs_forge("engine", games=10)   # win-rate vs Forge AI + witchcraft's coverage of the game

    Requires Forge installed (see witchcraft.forge.forge_available()). Heavy: one JVM per game. The mirror
    fractions are populated by the 'engine' bot (it reconstructs); 'random' leaves them None."""
    from . import forge as wf
    if not wf.forge_available():
        raise RuntimeError("Forge not available — need a built fatjar + JDK 17; see "
                           "witchcraft.forge.forge_available() / play_forge().")
    bot_wins = forge_wins = inconclusive = 0
    turns, modeled, endorsed = [], [], []
    t0 = time.perf_counter()
    for i in range(games):
        r = wf.play_forge(bot=bot, witch_deck=witch_deck, opp_deck=opp_deck,
                          timeout=timeout, recompile=(i == 0))     # compile the harness once
        w = r["winner"]
        if w == "Witchcraft-Engine":
            bot_wins += 1
        elif w == "Forge-AI":
            forge_wins += 1
        else:
            inconclusive += 1                                       # TIMEOUT/ERR — game didn't resolve
        if str(r["turns"]).isdigit():
            turns.append(int(r["turns"]))
        if r.get("modeled_frac") is not None:
            modeled.append(r["modeled_frac"])
        if r.get("endorsed_frac") is not None:
            endorsed.append(r["endorsed_frac"])
    wall = time.perf_counter() - t0
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    return {
        "games": games, "bot": bot if isinstance(bot, str) else ("net" if hasattr(bot, "net") else getattr(bot, "name", "player")),
        "source_of_truth": "forge",
        "bot_wins": bot_wins, "forge_wins": forge_wins, "inconclusive": inconclusive,
        "bot_win_rate": round(bot_wins / games, 3) if games else 0.0,
        "avg_turns": avg(turns),
        "mirror_modeled_frac": avg(modeled), "mirror_endorsed_frac": avg(endorsed),
        "wall_s": round(wall, 1),
    }
