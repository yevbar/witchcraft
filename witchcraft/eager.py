"""witchcraft.eager — a player that picks its plan from the deck's WIN CONDITIONS.

Where `HeuristicPlayer` hand-codes one general policy and `AggroPlayer` always races damage, `EagerPlayer` first
asks the engine which win axes its deck can even pursue (`game.win_conditions`, from `witchcraft.wincon`) and
then DISPATCHES move selection to a strategy method per available axis — so a poison deck pursues poison, a mill
deck mills, an aggro deck races life, without one tangled heuristic. The per-axis handlers are no-op placeholders
for now (they return None and the player falls back to a legal move); the value here is the scaffold: the deck's
reachable axes drive a clean switch, and each branch is a small focused strategy to fill in.

    from witchcraft import benchmark
    from witchcraft.eager import EagerPlayer
    benchmark(EagerPlayer(), games=10)
"""

from __future__ import annotations

from .players import Player
from .wincon import WinCon


class EagerPlayer(Player):
    """Choose a move by dispatching to the strategy for one of the deck's reachable win conditions."""

    name = "eager"

    # Try the win conditions in this order; the first whose handler returns a move wins. (A "you win" or
    # commander line ends the game fastest; life is the universal fallback.)
    _PRIORITY = (WinCon.WIN_GAME, WinCon.COMMANDER_DAMAGE, WinCon.POISON_TEN, WinCon.DECKOUT, WinCon.LIFE_ZERO)

    def choose_move(self, game):
        forced = game.only_legal_move                      # a forced step (one option) -> take it, skip the search
        if forced is not None:
            return forced
        if not game.legal_moves:
            return None

        viable_wincons = game.win_conditions(self.me)      # the axes MY deck can pursue
        for approach in self._PRIORITY:
            if approach in viable_wincons:
                if (move := self._DISPATCH[approach](self, game)) is not None:
                    return move

        return game.legal_moves[0]                         # no axis handler produced a move yet -> default legal move

    # ---- per-win-condition strategies (placeholders — each returns None for now) ------------------------------

    def _via_win_game(self, game):
        """Assemble / cast the 'you win the game' effect (Thassa's Oracle, Approach, …)."""
        return None

    def _via_commander_damage(self, game):
        """Connect with the commander for 21 (§903.10a)."""
        return None

    def _via_poison_ten(self, game):
        """Build an infect/toxic clock to 10 poison."""
        return None

    def _via_deckout(self, game):
        """Mill / force-draw the opponent to an empty library."""
        return None

    def _via_life_zero(self, game):
        """Race the opponent's life to 0 (develop creatures + attack, burn, drain)."""
        return None

    _DISPATCH = {
        WinCon.WIN_GAME: _via_win_game,
        WinCon.COMMANDER_DAMAGE: _via_commander_damage,
        WinCon.POISON_TEN: _via_poison_ten,
        WinCon.DECKOUT: _via_deckout,
        WinCon.LIFE_ZERO: _via_life_zero,
    }
