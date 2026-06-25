"""mtg.information — a player that never acts, but toggles between perfect and imperfect information.

The other players sit on a spectrum of *mechanical* awareness: `HeuristicPlayer` reasons about the board,
`LookaheadPlayer` searches blindly. `InformationPlayer` is orthogonal to both — it makes no decisions at all
(it always passes), and its only knob is INFORMATION: how much of the hidden game state it is allowed to see.

  * imperfect (the default, §103) — the redacted `observation`: opponents' hands and libraries are hidden.
  * perfect — the full game: every hand and library is visible.

Because it only ever passes, flipping the knob never changes what HAPPENS in the game — only what the seat
KNOWS. That makes it a clean substrate for information-set experiments (how much does seeing the opponent's
hand matter?) and an exercise of the imperfect-information `observation` machinery. Witchcraft-only — Forge
runs its own AI and owns information there, so this has no role in the Forge path.

    from mtg import InformationPlayer, play
    spy = InformationPlayer(perfect=True)            # an omniscient passer
    play({"alice": spy, "bob": RandomPlayer()})      # spy sees everything but does nothing
    spy.conceal()                                     # ...or toggle it back to the redacted default mid-game
"""
from __future__ import annotations

from .players import Player


class InformationPlayer(Player):
    """A player that ALWAYS PASSES, with a toggleable perfect/imperfect information view.

    It is bound to what it PERCEIVES, so every seat-scoped accessor reflects the current mode transparently:
    in imperfect mode `self.opponent.hand` is hidden (empty), in perfect mode it shows the real cards. Toggle
    mid-game with `reveal()` / `conceal()` / `set_perfect(...)` / `toggle()` — the game handles the switch
    seamlessly because the move is always the same (a pass), only the perception changes.
    """

    name = "information"

    def __init__(self, perfect: bool = False):
        self.perfect = bool(perfect)            # True = perfect (see-all), False = imperfect (redacted default)

    # ---- the information toggle -------------------------------------------------------------------

    def set_perfect(self, perfect: bool = True) -> "InformationPlayer":
        """Set the information mode (True = perfect/see-all, False = imperfect/redacted). Returns self."""
        self.perfect = bool(perfect)
        return self

    def reveal(self) -> "InformationPlayer":
        """Switch to PERFECT information — see every hand and library. Returns self."""
        return self.set_perfect(True)

    def conceal(self) -> "InformationPlayer":
        """Switch to IMPERFECT information — back to the §103 redacted default. Returns self."""
        return self.set_perfect(False)

    def toggle(self) -> "InformationPlayer":
        """Flip between perfect and imperfect information. Returns self."""
        self.perfect = not self.perfect
        return self

    # ---- perception -------------------------------------------------------------------------------

    def perceive(self, game: "Game", seat: str | None = None) -> "Game":
        """The game as this player currently SEES it: the full `game` when perfect; otherwise the §103
        redacted `observation` from its seat (opponents' hands and libraries hidden). A game that is already
        an observation is returned unchanged (you can't reveal what was redacted upstream)."""
        seat = seat if seat is not None else (self._seat or game.turn)
        if self.perfect or game.observer is not None:
            return game
        return game.observation(seat)

    def bind(self, game: "Game", seat: str | None = None) -> "InformationPlayer":
        """Bind to the PERCEIVED view (not the raw game), so the seat-scoped accessors honour the information
        mode — imperfect hides the opponents' hidden zones, perfect reveals them."""
        seat = seat if seat is not None else game.turn
        super().bind(self.perceive(game, seat), seat)
        return self

    # ---- the (non-)decision -----------------------------------------------------------------------

    def choose_move(self, game: "Game"):
        """Never act: take the do-nothing move — a literal pass, or the no-attack / no-block option at a
        combat declaration. Computed on the TRUE `game` (not the perceived view), so the move is always legal
        to push there; perception (which may have just toggled) is refreshed for the seat's own accessors."""
        self.bind(game)
        return game.priority.skip()
