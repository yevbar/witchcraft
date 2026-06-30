"""mtg.forge — a mode where FORGE is the source of truth and a "forge player" is involved.

The mtg `Game` makes *mtg* authoritative (our datalog engine referees). This mode is the
inverse: **Forge runs and judges the game**, one seat is Forge's own AI (the "forge player"), and the other
seat is a mtg-driven bot that connects over `forge_bridge`'s socket and answers each Forge decision
with a policy. This is the only faithful way to put Forge in the loop — there is no reverse bridge that would
let Forge's AI choose moves inside a mtg `Game`, so when you want Forge to be the truth, the game must
actually run in Forge.

    import mtg.forge as wf
    if wf.forge_available():
        r = wf.play_forge(bot="engine")          # Forge AI vs the mtg 'stockfish' bot; Forge judges
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
    package never pulls the JVM tooling on a normal `import mtg`)."""
    fi = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "forge_integration")
    if fi not in sys.path:
        sys.path.insert(0, fi)
    import run_tournament as rt
    return rt


def forge_status() -> dict:
    """An agent-debuggable view of Forge readiness: the RESOLVED paths (after forge_integration's
    auto-discovery of a local JDK 17 + installed fatjar) and which existence check passes — so a False
    `available` is actionable instead of silent. Keys: available, jdk, jdk_ok, fatjar, fatjar_ok, assets,
    headless (+ error if the orchestration import failed). Override any path with $JDK / $FATJAR / $FORGE /
    $FORGE_ASSETS; force the display mode with $FORGE_HEADLESS."""
    try:
        rt = _orch()
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
    jdk_ok = os.path.exists(os.path.join(rt.JDK, "bin", "java"))
    fatjar_ok = os.path.exists(rt.FATJAR)
    return {"available": jdk_ok and fatjar_ok, "jdk": rt.JDK, "jdk_ok": jdk_ok,
            "fatjar": rt.FATJAR, "fatjar_ok": fatjar_ok,
            "assets": getattr(rt, "FORGE_ASSETS", None), "headless": getattr(rt, "_HEADLESS", None)}


def forge_available() -> bool:
    """True iff Forge can run here — the JDK `java` binary and the built fatjar both exist. The paths are
    auto-discovered (local JDK 17, an installed Forge fatjar); override with $JDK / $FATJAR / $FORGE. Use
    `forge_status()` to see the resolved paths and WHY this is False."""
    return forge_status().get("available", False)


# The mtg bot seat's policy, as run_bot.py understands it (MTG_POLICY): 'engine' = the win_search
# 'stockfish' (reconstructs the board from Forge's observation, runs env lookahead); 'random' = the baseline.
_BOT_POLICIES = ("engine", "random")


def _bot_policy_name(bot) -> str:
    """Resolve the bot-seat policy. Accepts a name ('engine'|'random') or a mtg Player — a
    RandomPlayer maps to 'random', any other Player to 'engine' (the strongest wired bot). NOTE: a custom
    Game-based Player can't drive Forge directly — Forge hands the seat an *observation*, not a mtg
    state, and offers Forge-ids, not mtg moves — so the wired obs-policies are what run a Forge seat.
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
    raise TypeError(f"bot must be 'engine'|'random' or a mtg Player; got {type(bot).__name__}")


def play_forge(bot="engine", *, witch_deck: str = "vanilla", opp_deck: str = "vanilla",
               timeout: int = 300, recompile: bool = True) -> dict:
    """Run ONE game refereed by Forge (the source of truth): the mtg bot seat (driven by `bot`) vs
    Forge's own AI (the "forge player"). `bot` is 'engine'|'random', a mtg Player, OR a TRAINED VALUE
    NET (a rebel_train.NetValue OR a cardnet.CardNetValue) — the net is saved to disk and the bot process
    drives the seat with it (the 'net' policy: reconstruct + 1-ply net-rank the plays). Returns Forge's
    verdict — {winner, turns, wall_ms,
    bot_policy, forge_ai_seat, source_of_truth='forge', modeled_frac, endorsed_frac}. `witch_deck`/`opp_deck`
    are ForgeVsBot archetypes ('vanilla', 'infect', …). Raises RuntimeError if Forge isn't installed.

    Heavy: spins up the JVM (and can stall on long games); set $JVM_HEAP / $GAME_TIMEOUT on small hosts."""
    rt = _orch()
    if not forge_available():
        raise RuntimeError(
            f"Forge not available — need the JDK at {rt.JDK}/bin/java and the fatjar at {rt.FATJAR}. "
            f"Set $JDK / $FORGE, or build the Forge fatjar (see forge_integration/RUNNING.md).")
    if hasattr(bot, "net"):                                  # a trained value net (NetValue or CardNetValue)
        import tempfile                                      # save it where the bot subprocess can load it
        if hasattr(bot.net, "state_dict"):                  # a torch CardValueNet -> cardnet.save (.pt); the bridge
            from . import cardnet                            # loader detects the format. (rebel_forge.load_value_fn)
            path = os.path.join(tempfile.gettempdir(), "rebel_forge_vnet.pt")
            cardnet.save(bot.net, path)
        else:                                               # the legacy 14-feature TinyValueNet (numpy .npz)
            path = os.path.join(tempfile.gettempdir(), "rebel_forge_vnet")
            bot.net.save(path); path += ".npz"
        which = "net"
        bot_env = {"MTG_POLICY": "net", "MTG_VALUE_NET": path}
    else:
        which = _bot_policy_name(bot)
        bot_env = {"MTG_POLICY": which}
    if recompile:
        rt.compile_harnesses()
    port = rt.free_port(8800)
    res = rt.run_game("ForgeVsBot", {"witchDeck": witch_deck, "oppDeck": opp_deck},
                      port, timeout=timeout, bot_env=bot_env)
    return {
        "winner": res["winner"],                 # Forge's RESULT verdict — the source of truth
        "turns": res["turns"],
        "wall_ms": res["wall"],
        "bot_policy": which,
        "forge_ai_seat": "Forge-AI",
        "source_of_truth": "forge",
        "modeled_frac": res.get("modeled"),       # how much of its decisions the mtg bot could model
        "endorsed_frac": res.get("endorsed"),
    }
