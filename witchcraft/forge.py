"""witchcraft.forge — a mode where FORGE is the source of truth and a "forge player" is involved.

The witchcraft `Game` makes *witchcraft* authoritative (our datalog engine referees). This mode is the
inverse: **Forge runs and judges the game**, one seat is Forge's own AI (the "forge player"), and the other
seat is a witchcraft-driven bot that connects over `forge_bridge`'s socket and answers each Forge decision
with a policy. This is the only faithful way to put Forge in the loop — there is no reverse bridge that would
let Forge's AI choose moves inside a witchcraft `Game`, so when you want Forge to be the truth, the game must
actually run in Forge.

    import witchcraft.forge as wf
    if wf.forge_available():
        r = wf.play_forge(bot="engine")          # Forge AI vs the witchcraft 'stockfish' bot; Forge judges
        print(r["winner"], r["turns"])           # winner is Forge's verdict (the source of truth)

Requires Forge installed: a built fatjar + JDK 17 (set $FORGE / $JDK, see forge_integration/RUNNING.md).
`play_forge` raises a clear RuntimeError if not. It reuses the wired forge_integration orchestration
(ForgeVsBot + run_bot), so it inherits its cost: it spins up the JVM and can be slow / memory-hungry.
"""

from __future__ import annotations

import os
import sys


def _orch():
    """Lazily import the forge_integration orchestration (only when this mode is actually used, so the
    package never pulls the JVM tooling on a normal `import witchcraft`)."""
    fi = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "forge_integration")
    if fi not in sys.path:
        sys.path.insert(0, fi)
    import run_tournament as rt
    return rt


def forge_available() -> bool:
    """True iff Forge can run here — the JDK `java` binary and the built fatjar both exist (the same paths
    forge_integration uses; override with $JDK / $FORGE)."""
    try:
        rt = _orch()
    except Exception:
        return False
    return os.path.exists(os.path.join(rt.JDK, "bin", "java")) and os.path.exists(rt.FATJAR)


# The witchcraft bot seat's policy, as run_bot.py understands it (MTG_POLICY): 'engine' = the win_search
# 'stockfish' (reconstructs the board from Forge's observation, runs env lookahead); 'random' = the baseline.
_BOT_POLICIES = ("engine", "random")


def _bot_policy_name(bot) -> str:
    """Resolve the bot-seat policy. Accepts a name ('engine'|'random') or a witchcraft Player — a
    RandomPlayer maps to 'random', any other Player to 'engine' (the strongest wired bot). NOTE: a custom
    Game-based Player can't drive Forge directly — Forge hands the seat an *observation*, not a witchcraft
    state, and offers Forge-ids, not witchcraft moves — so the wired obs-policies are what run a Forge seat.
    Write a `forge_bridge` obs-policy for a custom Forge bot."""
    if isinstance(bot, str):
        if bot not in _BOT_POLICIES:
            raise ValueError(f"bot policy must be one of {_BOT_POLICIES} (or a Player); got {bot!r}")
        return bot
    from .players import Player, RandomPlayer
    if isinstance(bot, RandomPlayer):
        return "random"
    if isinstance(bot, Player):
        return "engine"
    raise TypeError(f"bot must be 'engine'|'random' or a witchcraft Player; got {type(bot).__name__}")


def play_forge(bot="engine", *, witch_deck: str = "vanilla", opp_deck: str = "vanilla",
               timeout: int = 300, recompile: bool = True) -> dict:
    """Run ONE game refereed by Forge (the source of truth): the witchcraft bot seat (driven by `bot`) vs
    Forge's own AI (the "forge player"). Returns Forge's verdict — {winner, turns, wall_ms, bot_policy,
    forge_ai_seat, source_of_truth='forge', modeled_frac, endorsed_frac}. `witch_deck`/`opp_deck` are
    ForgeVsBot archetypes ('vanilla', 'infect', …). Raises RuntimeError if Forge isn't installed.

    Heavy: spins up the JVM (and can stall on long games); set $JVM_HEAP / $GAME_TIMEOUT on small hosts."""
    rt = _orch()
    if not forge_available():
        raise RuntimeError(
            f"Forge not available — need the JDK at {rt.JDK}/bin/java and the fatjar at {rt.FATJAR}. "
            f"Set $JDK / $FORGE, or build the Forge fatjar (see forge_integration/RUNNING.md).")
    which = _bot_policy_name(bot)
    if recompile:
        rt.compile_harnesses()
    port = rt.free_port(8800)
    res = rt.run_game("ForgeVsBot", {"witchDeck": witch_deck, "oppDeck": opp_deck},
                      port, timeout=timeout, bot_env={"MTG_POLICY": which})
    return {
        "winner": res["winner"],                 # Forge's RESULT verdict — the source of truth
        "turns": res["turns"],
        "wall_ms": res["wall"],
        "bot_policy": which,
        "forge_ai_seat": "Forge-AI",
        "source_of_truth": "forge",
        "modeled_frac": res.get("modeled"),       # how much of its decisions the witchcraft bot could model
        "endorsed_frac": res.get("endorsed"),
    }
