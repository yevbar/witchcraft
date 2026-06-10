"""game.py — game SETUP + a self-play harness over the rules engine.

This is the top of the stack the project has been building toward:

    engine_rules.dl (datalog)   derives the consequences of a state      — the rules
    driver.py                   applies them, loops, owns chance/choice   — the shim
    env.py                      enumerates legal actions, steps purely    — the referee
    game.py  (this)             builds a real game, runs a policy to win  — the agent harness

`new_game` assembles real decks (via the bridge's card-fact feed), installs a SEEDED, clone-safe RNG
and per-seat policies, reads the per-variant starting life / hand size from the interpreted rules
(§103.4, starting.dl), and runs the London mulligan (§103.4) — every random event through driver._random,
every decision through driver._choose, so a search/policy can drive or observe all of them.

`self_play` is the "frankenstein MTG Stockfish": a complete (if very weak) agent — a uniform-random or
greedy policy over EXACTLY the action surface an AlphaZero net would drive (env.legal_actions/step). The
policy is the only thing a stronger agent would replace; the architecture around it is already here.

    import game
    game.self_play(game.DECKS, variant="two-player", seed=7)        # -> winner ('alice' | 'bob' | None)
    game.demo()                                                     # narrated two-random-agents game
"""

from __future__ import annotations

import contextlib
import io

import driver
import env
import bridge_to_engine as bridge

DECKS = bridge._DEMO_DECKS              # the on-color Gruul vs Dimir demo decks (real cards)


# ---- policies (the agent) -----------------------------------------------------------------------------
# A policy has the SAME signature as driver._choose's seam: (state, key, options, default) -> choice.
# It serves BOTH roles — picking among env.legal_actions (top-level) and resolving the sub-choices that
# arise inside the driver's resolution (target/mode/block/mulligan). One function, every decision.

def random_policy(state: dict, key: str, options, default):
    """Uniform-random over the legal options — the simplest complete policy (a very poor 'Stockfish').
    Draws from the state's seeded RNG so a game is reproducible given its seed."""
    if options is None:
        return default
    opts = list(options)
    return driver._rng(state).choice(opts) if opts else default


def greedy_policy(state: dict, key: str, options, default):
    """The codebase's historical greedy pick (attack with all, target the strongest, cast the first
    castable) — i.e. just take the driver's hand-tuned default at every seam."""
    return default


def first_action_policy(state: dict, key: str, options, default):
    """Deterministic: always the first legal option (a fixed, reproducible baseline for tests)."""
    if options is None:
        return default
    opts = list(options)
    return opts[0] if opts else default


# ---- mulligan (§103.4, London) ------------------------------------------------------------------------

def _return_hand(state: dict, p: str) -> None:
    """Put p's whole hand back into their library (membership only; order is set by the reshuffle)."""
    hand = [c for (pp, c) in state.get("in_hand", set()) if pp == p]
    for c in hand:
        state["in_hand"].discard((p, c))
        state.setdefault("in_library", set()).add((p, c))


def mulligan(state: dict, players: list[str], variant: str = "default") -> None:
    """§103.4 London mulligan, every step routed through a seam so a policy drives it (the naive default
    KEEPS the opening hand — k=0 — so a basic game just works): each player may mulligan; on a mulligan
    the hand returns to the library, it's reshuffled (seeded RNG), and a fresh hand is drawn; when the
    player finally keeps after k mulligans, they bottom k cards. keep/mull and the bottoming both go
    through driver._choose (default keep / bottom-arbitrary)."""
    hsize = driver._variant_hand_size(variant)
    for p in players:
        mulls = 0
        while mulls < hsize:
            if driver._choose(state, "mulligan", (True, False), True):    # True = keep (default)
                break
            _return_hand(state, p)
            driver._shuffle_library(state, p)
            for _ in range(hsize):
                driver._draw(state, p)
            mulls += 1
        for _ in range(mulls):                              # London: bottom one card per mulligan taken
            hand = sorted(c for (pp, c) in state.get("in_hand", set()) if pp == p)
            if not hand:
                break
            card = driver._choose(state, "bottom", hand, hand[0])
            state["in_hand"].discard((p, card))
            state.setdefault("in_library", set()).add((p, card))
            state.setdefault("_lib_order", {}).setdefault(p, []).append(card)   # to the true bottom


# ---- game construction --------------------------------------------------------------------------------

def _policy_dispatch(policies: dict):
    """Route every seam to the policy of the player whose decision it is (the active player, except a
    blocks decision which belongs to the defender). Falls back to the default when a seat has no policy."""
    def choose(state, key, options, default):
        ap = next(iter(state["active_player"]))[0]
        seat = driver._others(state, ap)[0] if key == "blocks" and driver._others(state, ap) else ap
        pol = policies.get(seat)
        return pol(state, key, options, default) if pol else default
    return choose


def new_game(decks: dict, variant: str = "default", seed: int = 0, policies: dict | None = None) -> dict:
    """Build a ready-to-play game: real decks bridged + shuffled (seeded), per-variant life/hand from the
    rules, opening hands drawn, London mulligan run. If `policies` ({player: policy}) is given, install a
    dispatcher on the _choose seam so the driver's internal sub-choices follow each seat's policy."""
    state = bridge.make_deck_state(decks, seed=seed, variant=variant)
    mulligan(state, list(decks), variant=variant)
    if policies:
        state["_policy"] = _policy_dispatch(policies)
    return state


# ---- self-play (the agent loop) -----------------------------------------------------------------------

def self_play(decks: dict, variant: str = "two-player", seed: int = 0, policy=random_policy,
              max_decisions: int = 4000, verbose: bool = False) -> str | None:
    """Play a FULL game through the referee with `policy` choosing among env.legal_actions, to a terminal
    state. Returns the winner ('alice'|'bob'|None on a draw / decision cap). The complete agent loop —
    observe the legal actions, pick one, step — over the same surface an AlphaZero policy would drive.
    `policy` also resolves the driver's internal sub-choices (same signature)."""
    state = new_game(decks, variant=variant, seed=seed)
    state["_policy"] = policy                               # internal sub-choices use the same policy
    sink = (lambda: contextlib.nullcontext()) if verbose else (lambda: contextlib.redirect_stdout(io.StringIO()))
    with sink():
        s = env.start(state)
        for _ in range(max_decisions):
            if env.is_terminal(s):
                break
            acts = env.legal_actions(s)
            if not acts:
                break
            a = policy(s, "action", acts, acts[0])
            s = env.step(s, a)
    return env.winner(s)


def demo(seed: int = 7) -> None:
    """A narrated game between two uniform-random agents — the architecture end-to-end: rules in datalog,
    chance + choices through the shim's seams, the referee enumerating actions, a trivial policy picking."""
    print(f"frankenstein MTG self-play (two random agents, seed={seed}, Gruul vs Dimir real cards):\n")
    w = self_play(DECKS, variant="two-player", seed=seed, policy=random_policy, verbose=False)
    print(f"  winner: {w}" if w else "  no decisive result within the decision cap")


if __name__ == "__main__":
    demo()
