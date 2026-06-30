"""mtg.benchmark — measure a heuristic's strength, conveniently.

Two harnesses:

  benchmark(player, opponent)        — fast SELF-PLAY in the mtg engine. Drop in any Player, get a
                                       win-rate vs a baseline (RandomPlayer by default). No Forge, no JVM.
  benchmark_vs_forge(bot)            — play the mtg bot against FORGE's AI with Forge refereeing
                                       (the source of truth), aggregating win-rate over games. Also runs the
                                       MIRROR: a parallel mtg state reconstructed from Forge's stream,
                                       so you get mtg's coverage of the real game alongside the result.

The point: developing a custom heuristic is `class MyBot(Player): def choose_move(self, game): ...`, then
`benchmark(MyBot())`. Validating against Forge is `benchmark_vs_forge(...)`.
"""

from __future__ import annotations

import contextlib
import io
import random
import time


def benchmark(player, opponent=None, *, games: int = 20, variant: str = "two-player", seed: int = 0,
              decks: dict | None = None, deck_pool: list | None = None, player_deck: list | None = None,
              commanders: dict | None = None, incremental: bool = False, max_moves: int = 4000,
              swap_seats: bool = True, explicit_lands: bool = False, paired: bool = True) -> dict:
    """Play `player` vs `opponent` (default RandomPlayer) over `games` mtg self-play games and report
    `player`'s record. Seats are swapped every other game (so a first-player edge doesn't bias the result).
    With `paired` (default, requires `swap_seats`) the two seat orientations of each pair reuse the SAME game
    seed — common random numbers, so the deck shuffle is identical and deck-luck cancels in the paired
    difference (~halves games-to-significance). `paired=False` falls back to a distinct seed per game
    (`seed + i`). Returns {games, wins, losses, draws, win_rate, avg_turns, wall_s, games_per_s}.

        from mtg import benchmark, RandomPlayer, Player
        class MyBot(Player):
            def choose_move(self, game): ...
        benchmark(MyBot(), games=50)                 # -> {'win_rate': 0.62, 'avg_turns': 14.1, ...}
        benchmark(MyBot(), RandomPlayer(seed=1))     # vs a fixed-seed baseline
    """
    from .players import RandomPlayer, play
    if opponent is None:
        opponent = RandomPlayer()
    # The effective action space is what play() will actually open: the explicit `explicit_lands` OR either
    # player's `wants_*` capability (players.py). It is constant across this call's games (same two players),
    # but DIFFERS BY OPPONENT across a gauntlet — e.g. OFF vs RandomPlayer, forced ON vs HeuristicPlayer. That
    # silently evaluates one net in different action spaces across rungs. Compute it once and REPORT it (below)
    # so the mismatch is auditable; pin `explicit_lands=True` in a gauntlet to keep rungs comparable.
    _both = (player, opponent)
    eff_explicit = explicit_lands or any(getattr(p, "wants_explicit_lands", False) for p in _both)
    eff_instant = any(getattr(p, "wants_instant_speed", False) for p in _both)
    wins = losses = draws = 0
    total_turns = 0
    outcomes = []
    t0 = time.perf_counter()
    crn = paired and swap_seats                                        # common random numbers across the pair
    # GENERALIZATION: with `deck_pool` (a list of decks), each game draws both seats' decks from the pool, so the
    # measure spans MANY matchups, not one fixed deck pair. The deck RNG is seeded only from `seed`, so the
    # matchup sequence is reproducible AND identical across a gauntlet's rungs (every rung faces the same decks,
    # like pinned explicit_lands). Under CRN the matchup is sampled ONCE PER PAIR and reused across the two seat
    # orientations, so deck-luck still cancels in the pair (both contestants play both decks on the same shuffle).
    deck_rng = random.Random((seed + 1) * 1_000_003) if (deck_pool or player_deck) else None
    cur_decks = decks
    cur_opp = None
    for i in range(games):
        flip = swap_seats and (i % 2 == 1)
        # paired CRN: the two orientations of pair k (games 2k, 2k+1) share game seed `seed + k`, so the deck
        # shuffle is identical and only the seat assignment differs -> deck-luck cancels. Else distinct per game.
        gseed = seed + (i // 2) if crn else seed + i
        fresh = not crn or i % 2 == 0                                  # sample the varying deck once per CRN pair
        if player_deck is not None:
            # DECK-SPECIFIC measure: `player`'s deck is FIXED (it follows `player` across the seat-swap), only
            # the OPPONENT's deck varies (from deck_pool). Halves the deck-luck noise vs sampling BOTH seats --
            # the agent's own deck no longer randomly helps/hurts it, so the score reflects skill on THIS deck.
            if fresh:
                cur_opp = deck_rng.choice(deck_pool) if deck_pool else player_deck
            pseat, oseat = ("bob", "alice") if flip else ("alice", "bob")
            cur_decks = {pseat: player_deck, oseat: cur_opp}
        elif deck_pool and fresh:                                      # mixed matchup: both seats from the pool
            cur_decks = {"alice": deck_rng.choice(deck_pool), "bob": deck_rng.choice(deck_pool)}
        players = {"alice": opponent, "bob": player} if flip else {"alice": player, "bob": opponent}
        mine = "bob" if flip else "alice"
        with contextlib.redirect_stdout(io.StringIO()):                # the engine narrates each step — mute it
            g = play(players, cur_decks, variant=variant, seed=gseed, commanders=commanders,
                     incremental=incremental, max_moves=max_moves, explicit_lands=explicit_lands)
        w = g.winner()
        total_turns += g.turn_number
        outcomes.append(1.0 if w == mine else (0.5 if w is None else 0.0))   # mine's per-game score
        if w == mine:
            wins += 1
        elif w is None:
            draws += 1
        else:
            losses += 1
    wall = time.perf_counter() - t0
    # Under CRN, games (2k, 2k+1) share a seed -> average them into one pair score so the correlated deck-luck
    # cancels; the honest SE is then the sample SE of these pair scores (see score_stats), which tightens as the
    # pairing actually cancels variance. A trailing odd game forms a singleton group. Without CRN, no pairing.
    pair_scores = ([sum(outcomes[k:k + 2]) / len(outcomes[k:k + 2]) for k in range(0, games, 2)]
                   if crn and games else None)
    return {
        "games": games, "wins": wins, "losses": losses, "draws": draws,
        "win_rate": round(wins / games, 3) if games else 0.0,
        "avg_turns": round(total_turns / games, 1) if games else 0.0,
        "wall_s": round(wall, 2), "games_per_s": round(games / wall, 2) if wall else 0.0,
        "pair_scores": pair_scores,                                     # CRN pair scores for paired SE (or None)
        "explicit_lands": eff_explicit, "instant_speed": eff_instant,   # the action space these games ran in
        "deck_pool": len(deck_pool) if deck_pool else 0,                # # decks in the matchup pool (0 = fixed)
    }


def benchmark_vs_forge(bot="engine", *, games: int = 5, witch_deck: str = "vanilla",
                       opp_deck: str = "vanilla", timeout: int = 120) -> dict:
    """Benchmark the mtg bot against FORGE's AI with Forge refereeing (the source of truth), over
    `games`. Returns the bot's record vs Forge PLUS the MIRROR signal: as the engine bot plays, it
    reconstructs the board from Forge's observation stream every decision and runs mtg in parallel,
    so `mirror_modeled_frac`/`mirror_endorsed_frac` report how much of the authoritative Forge game
    mtg could model/endorse — a direct, game-by-game fidelity benchmark against Forge.

        from mtg.benchmark import benchmark_vs_forge
        benchmark_vs_forge("engine", games=10)   # win-rate vs Forge AI + mtg's coverage of the game

    Requires Forge installed (see mtg.forge.forge_available()). Heavy: one JVM per game. The mirror
    fractions are populated by the 'engine' bot (it reconstructs); 'random' leaves them None."""
    from . import forge as wf
    if not wf.forge_available():
        raise RuntimeError("Forge not available — need a built fatjar + JDK 17; see "
                           "mtg.forge.forge_available() / play_forge().")
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
