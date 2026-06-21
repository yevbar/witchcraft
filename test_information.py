"""test_information.py — the InformationPlayer: a player that ALWAYS PASSES but toggles between perfect and
imperfect information. Proves (a) it never acts, even when real moves are available, (b) imperfect mode hides
the opponent's hidden zones while perfect mode reveals them, and (c) the mode toggles mid-game. Witchcraft
self-play only (no Forge).
"""
from __future__ import annotations

import contextlib
import io

from witchcraft import InformationPlayer, RandomPlayer, play
from witchcraft.game import Game

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _opponent(g: Game, seat: str) -> str:
    return next(p for p in g.players if p != seat)


def _imperfect_hides_opponent() -> None:
    """Default (imperfect) mode: bound to the §103 redacted observation — own hand visible, opponent's hidden."""
    g = Game(seed=3)
    seat = g.turn
    opp = _opponent(g, seat)
    p = InformationPlayer().bind(g, seat)
    check("imperfect: perceive is an observation from the seat", p.perceive(g, seat).observer == seat)
    check("imperfect: the opponent's hand is hidden", len(p.opponent.hand) == 0)
    check("imperfect: my OWN hand is still visible", len(p.me.hand) == len(g.hand(seat)) > 0)


def _perfect_reveals_everything() -> None:
    """Perfect mode: bound to the full game — every hand and library visible."""
    g = Game(seed=3)
    seat = g.turn
    opp = _opponent(g, seat)
    p = InformationPlayer(perfect=True).bind(g, seat)
    check("perfect: perceive is the full game (no observer)", p.perceive(g, seat).observer is None)
    check("perfect: the opponent's hand is fully visible", len(p.opponent.hand) == len(g.hand(opp)) > 0)
    check("perfect: opponent library size is known too", p.opponent.library_size == g.library_size(opp) > 0)


def _toggle_midgame() -> None:
    """The knob flips both ways and is honoured on the next bind — the 'toggling' the harness needs."""
    g = Game(seed=5)
    seat = g.turn
    p = InformationPlayer()                                    # start imperfect
    check("toggle: starts imperfect (opponent hidden)", len(p.bind(g, seat).opponent.hand) == 0)
    p.reveal()
    check("toggle: reveal() -> opponent visible", len(p.bind(g, seat).opponent.hand) > 0)
    p.conceal()
    check("toggle: conceal() -> opponent hidden again", len(p.bind(g, seat).opponent.hand) == 0)
    check("toggle: toggle() flips the flag", p.toggle().perfect is True and p.toggle().perfect is False)


def _always_passes() -> None:
    """Over a full game it never takes a real action — every decision is a pass — even when casts are legal."""
    class Recorder(InformationPlayer):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.kinds: list[str] = []
            self.saw_real_option = False

        def choose_move(self, game):
            if any(m.kind in ("cast", "play", "attack") for m in game.legal_moves):
                self.saw_real_option = True                   # a non-pass move was on the table...
            mv = super().choose_move(game)
            self.kinds.append(mv.kind)                        # ...and we still passed
            return mv

    rec = Recorder(perfect=True)
    with contextlib.redirect_stdout(io.StringIO()):
        g = play({"alice": rec, "bob": RandomPlayer(seed=2)}, seed=7, max_moves=4000)
    check("always-passes: the player made at least a few decisions", len(rec.kinds) >= 3)
    check("always-passes: EVERY decision was a pass", all(k == "pass" for k in rec.kinds))
    check("always-passes: it passed even with a real move available", rec.saw_real_option)
    check("always-passes: the game still reaches a terminal result", g.is_game_over() and g.winner() is not None)


def _two_passers_deckout() -> None:
    """Two passers do nothing but draw, so the game resolves by §104.3c deck-out (it terminates, no hang)."""
    with contextlib.redirect_stdout(io.StringIO()):
        g = play({"alice": InformationPlayer(perfect=True), "bob": InformationPlayer()}, seed=1, max_moves=4000)
    check("two passers: the game terminates with a winner", g.is_game_over() and g.winner() is not None)
    check("two passers: it ends in reasonable time (deck-out, not the move cap)", g.move_count() < 4000)


def run() -> None:
    _imperfect_hides_opponent()
    _perfect_reveals_everything()
    _toggle_midgame()
    _always_passes()
    _two_passers_deckout()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
