"""test_win_conditions.py — §104 / §704.5 WIN & LOSS conditions, derived (engine) + applied (shim).

The engine DERIVES the win/loss state-based condition: wins_game(P) / loses_game(P) over the wired game
state (engine_rules.dl, built by build_engine). The driver APPLIES it: _apply_outputs ends the game with
the right winner/loser, and the win/lose effect verbs (effect_handlers/players.py) ASSERT the result into
the engine inputs eff_win_game / eff_lose_game the engine reads.

Covers the cEDH-relevant endings that previously never resolved to a winner:
  EFFECT WIN   — 'you win the game' (Thassa's Oracle, Approach of the Second Sun, Felidar Sovereign,
                 Test of Endurance): a resolved effect -> eff_win_game -> wins_game -> the controller wins.
  EFFECT LOSS  — 'target player loses' / 'each opponent loses the game': eff_lose_game -> loses_game.
  EMPTY-LIBRARY WIN  — §614 replacement (Laboratory Maniac / Thassa's Oracle / Jace, Wielder of Mysteries):
                 a draw from an empty library becomes a WIN instead of the §104.3c deck-out LOSS.
And the existing endings still hold:
  LIFE <= 0    — §704.5a, derived loses_game from the life threshold (interpreted in ending.dl).
  DECK-OUT     — §104.3c, drawing from an empty library with NO replacement is a loss.

Run: python3 test_win_conditions.py
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import io
from contextlib import redirect_stdout

import driver as D

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _quiet(fn):
    """Run fn with stdout captured; return (result, log_text)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        r = fn()
    return r, buf.getvalue()


def _two_players(**extra):
    state = {
        "is_player": {("alice",), ("bob",)},
        "life": {("alice", 20), ("bob", 20)},
        "tapped": set(),
        "in_library": {("alice", "a0"), ("bob", "b0")},
    }
    state.update(extra)
    return state


def _outputs(**over):
    out = {"to_untap": set(), "to_draw": set(), "zone_change": set(),
           "loses_game": set(), "wins_game": set(), "player_damage": set(), "pending": set()}
    out.update(over)
    return out


# ---- ENGINE DERIVATION (wins_game / loses_game over the wired state) --------------------------------

def test_engine_derives_win_loss() -> None:
    """The engine derives the §104 win/loss SBA from its inputs — no driver logic involved here."""
    D.clear_cache()
    # §104.2a — a resolved 'you win' effect (eff_win_game) makes the player win.
    s = _two_players(eff_win_game={("alice",)})
    out = D.run(s, ["wins_game", "loses_game"])
    check("engine: eff_win_game -> wins_game (§104.2a)",
          out["wins_game"] == {("alice",)} and not out["loses_game"])

    # §104.3a — a resolved 'you/target lose' effect (eff_lose_game) makes the player lose.
    s = _two_players(eff_lose_game={("bob",)})
    out = D.run(s, ["wins_game", "loses_game"])
    check("engine: eff_lose_game -> loses_game (§104.3a)",
          out["loses_game"] == {("bob",)} and not out["wins_game"])

    # §704.5a — life at/below the interpreted life_zero threshold loses.
    s = _two_players(life={("alice", 0), ("bob", 20)})
    out = D.run(s, ["loses_game"])
    check("engine: life <= 0 -> loses_game (§704.5a)", out["loses_game"] == {("alice",)})

    # §704.5c — poison at/above the interpreted poison_ten threshold loses.
    s = _two_players(poison={("alice", 10)})
    out = D.run(s, ["loses_game"])
    check("engine: poison >= 10 -> loses_game (§704.5c)", ("alice",) in out["loses_game"])

    # §104.3c — would draw from an empty library, no replacement -> loss.
    s = _two_players(would_draw_from_empty={("alice",)})
    out = D.run(s, ["wins_game", "loses_game"])
    check("engine: empty-library draw, no replacement -> loses_game (§104.3c)",
          out["loses_game"] == {("alice",)} and not out["wins_game"])

    # §614 — the SAME empty-library draw with a win-replacement -> WIN instead.
    s = _two_players(would_draw_from_empty={("alice",)}, library_win_repl={("alice",)})
    out = D.run(s, ["wins_game", "loses_game"])
    check("engine: empty-library draw + §614 replacement -> wins_game (Lab Maniac)",
          out["wins_game"] == {("alice",)} and not out["loses_game"])


# ---- SHIM APPLY (driver._apply_outputs ends the game on the derivation) ------------------------------

def test_thassas_oracle_effect_win() -> None:
    """Thassa's Oracle: 'you win the game'. A fired trigger carries the win_game effect; the effect handler
    asserts eff_win_game(controller), the engine derives wins_game, and the driver ends the game with the
    controller as the winner (the opponent loses). Note alice is at 1 life — she WINS regardless."""
    D.clear_cache()
    state = _two_players(life={("alice", 1), ("bob", 20)})
    out = _outputs(pending={("thassa", "win_game", 0, "controller", "oracle", "alice")})
    loser, _log = _quiet(lambda: D._apply_outputs(state, out, "alice"))
    check("Thassa's Oracle 'you win': controller alice is recorded as the winner",
          state.get("_winner") == "alice")
    check("Thassa's Oracle 'you win': the opponent bob is the loser returned", loser == "bob")


def test_target_player_loses() -> None:
    """'each opponent loses the game' / 'target player loses': the lose_game effect asserts eff_lose_game on
    each target, the engine derives loses_game, and the driver ends the game with that player as the loser."""
    D.clear_cache()
    state = _two_players()
    out = _outputs(pending={("doom", "lose_game", 0, "each_opponent", "src", "alice")})
    loser, _log = _quiet(lambda: D._apply_outputs(state, out, "alice"))
    check("'each opponent loses the game': bob is the loser", loser == "bob")
    check("'each opponent loses the game': eff_lose_game was asserted",
          ("bob",) in state.get("eff_lose_game", set()))


def test_empty_library_win_replacement() -> None:
    """Laboratory Maniac / Thassa's Oracle / Jace: the player would draw from an empty library, but the
    §614 replacement (library_win_repl) makes them WIN instead of decking out. The driver probes the engine
    at the draw moment and ends the game with that player as the winner."""
    D.clear_cache()
    state = _two_players(in_library={("bob", "b0")}, library_win_repl={("alice",)})  # alice's library is empty
    out = _outputs(to_draw={("alice",)})
    loser, log = _quiet(lambda: D._apply_outputs(state, out, "alice"))
    check("empty-library §614 replacement: alice WINS instead of decking out",
          state.get("_winner") == "alice" and loser == "bob")
    check("empty-library §614 replacement: the win is announced", "WIN" in log)


def test_normal_deckout_still_loses() -> None:
    """Regression: a draw from an empty library WITHOUT a replacement is still the §104.3c deck-out LOSS."""
    D.clear_cache()
    state = _two_players(in_library={("bob", "b0")})                       # alice's library is empty, no repl
    out = _outputs(to_draw={("alice",)})
    loser, log = _quiet(lambda: D._apply_outputs(state, out, "alice"))
    check("normal deck-out: alice loses the game (§104.3c)", loser == "alice")
    check("normal deck-out: NO winner is recorded (it's a loss, not a win)", state.get("_winner") is None)
    check("normal deck-out: empty-library loss is announced", "empty library" in log)


def test_life_threshold_still_loses() -> None:
    """Regression: the existing §704.5a life<=0 path still ends the game via the engine's loses_game."""
    D.clear_cache()
    state = _two_players(life={("alice", 0), ("bob", 20)})
    out = D.run(state, D.OUTPUTS)
    loser, _log = _quiet(lambda: D._apply_outputs(state, out, "bob"))
    check("life <= 0: alice loses the game (§704.5a)", loser == "alice")

    # a positive-life board with no loss/win decides nothing.
    D.clear_cache()
    state = _two_players()
    out = D.run(state, D.OUTPUTS)
    loser, _log = _quiet(lambda: D._apply_outputs(state, out, "alice"))
    check("no threshold crossed: the game continues (no loser)", loser is None)


def test_infect_poison_accumulates() -> None:
    """§704.5c — infect combat damage accrues as poison counters ACROSS turns (the driver folds the engine's
    per-combat combat_poison into the carried `poison` total, the way life takes player_damage). Three 3-power
    infect swings reach 9 (alive), the fourth crosses 10 and ends the game — not a single ≥10 swing."""
    def infect_combat(carried):
        return _two_players(
            active_player={("alice",)}, current_step={("combat_damage",)},
            poison=({("bob", carried)} if carried else set()),
            on_battlefield={("inf",)}, printed_type={("inf", "creature")},
            printed_power={("inf", 3)}, printed_toughness={("inf", 3)},
            printed_control={("alice", "inf")}, printed_keyword={("inf", "infect")},
            attacks={("inf", "bob")}, blocks=set(), counter=set())
    carried, loser = 0, None
    for _turn in range(4):
        D.clear_cache()
        s = infect_combat(carried)
        out = D.run(s, D.OUTPUTS)
        loser, _log = _quiet(lambda: D._apply_outputs(s, out, "alice"))
        carried = next((b for (p, b) in s.get("poison", set()) if p == "bob"), 0)
    check("infect poison accrues across turns to 12 (3+3+3+3)", carried == 12)
    check("the swing crossing 10 poison ends the game (§704.5c)", loser == "bob")

    # a SINGLE non-lethal infect swing must not fabricate a loss (no combat double-count — mirrors §510.2 fix).
    D.clear_cache()
    s = infect_combat(0)
    out = D.run(s, D.OUTPUTS)
    loser, _log = _quiet(lambda: D._apply_outputs(s, out, "alice"))
    check("a single 3-poison swing is non-lethal (3 < 10, no loss fabricated)", loser is None)


def run() -> None:
    test_engine_derives_win_loss()
    test_thassas_oracle_effect_win()
    test_target_player_loses()
    test_empty_library_win_replacement()
    test_normal_deckout_still_loses()
    test_life_threshold_still_loses()
    test_infect_poison_accumulates()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
