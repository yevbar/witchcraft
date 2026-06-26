"""inthearena.mtga.engine_policy — drive MTGA decisions with a python-mtg (witchcraft) Player.

This is the REVERSE of the Arena->engine state sync (`engine.to_game`): for each MTGA `Decision`, build an
`mtg.Game` positioned at the live board, ask a witchcraft `Player` for its move, and translate that move BACK to
the MTGA option it corresponds to — so a bot developed in python-mtg actually ENACTS in Arena.

The translation hinges on one fact: `engine.build_state` names every VISIBLE card `slug_<instanceId>`, where
`instanceId` is the MTGA GRE id. So an engine move's `card.id` carries the MTGA instanceId straight back, and we
just find the `Decision` option with that id. Attackers map by the same id; blocks/targets aren't wired (the
blind player doesn't make them).

Robustness: whenever the engine can't be used here — not importable, its datalog isn't on the repo-relative path
(run take_over from the repo root to use it), or a move can't be mapped — `EnginePolicy` falls back to a plain
`fallback` policy (default `blind_rage`) so the live bot never STALLS while we build the bridge and engine card
coverage. Every step logs what the engine chose vs what was executed, so the bridge can be ironed out iteratively.
"""

from __future__ import annotations

import logging
from typing import Optional

from .policy import BlindRagePolicy

_log = logging.getLogger(__name__)
_FALLBACK = object()   # _translate sentinel: "couldn't map this engine move — defer to the fallback policy"


def mtga_instance_id(engine_id) -> Optional[int]:
    """Recover the MTGA instanceId from an engine instance id. `build_state` names every visible card
    `slug_<instanceId>`, so the trailing integer IS the MTGA instanceId. Determinized hidden cards are
    `slug_x<n>` (no real id) -> None; our own moves never reference those."""
    if not engine_id:
        return None
    tail = str(engine_id).rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _attacker_target(atk):
    """The player damage-recipient for an MTGA qualified attacker (what aggro aims at) — first player, else any."""
    recips = getattr(atk, "legalDamageRecipients", None) or []
    return next((r for r in recips if getattr(r, "type", None) == "DamageRecType_Player"), None) or \
        (recips[0] if recips else None)


class EnginePolicy:
    """Decide MTGA decisions by running a WITCHCRAFT engine Player over the synced board and translating its
    move back to the MTGA option. `player` is any `mtg` Player (default `BlindAggroPlayer`, imported lazily so
    inthearena loads without the engine). Falls back to `fallback` (default `blind_rage`) whenever the engine
    can't be used or a move can't be mapped."""

    name = "witchcraft"

    def __init__(self, player=None, *, fallback=None, opponent_deck=None, seed: int = 0):
        self._player = player
        self._fallback = fallback or BlindRagePolicy()
        self._opponent_deck = opponent_deck
        self._seed = seed

    # ── engine side ───────────────────────────────────────────────────────────────────────────────────────
    def _engine_move(self, d):
        """The witchcraft player's chosen move for `d`, or None if the engine can't be used here."""
        try:
            from .engine import to_game
            if self._player is None:
                from mtg.blind_aggro import BlindAggroPlayer
                self._player = BlindAggroPlayer()
            game = to_game(d.view, d.seat, opponent_deck=self._opponent_deck, seed=self._seed)
            move = self._player.bind(game, "alice").choose_move(game)
            _log.info("  engine: %s chose %s%s", getattr(self._player, "name", "?"),
                      getattr(move, "kind", move),
                      f" {getattr(getattr(move, 'card', None), 'id', '')}".rstrip())
            return move
        except Exception as e:
            _log.info("  engine: unusable here (%s: %s) — using %s", type(e).__name__, e, self._fallback.name)
            return None

    # ── decide ────────────────────────────────────────────────────────────────────────────────────────────
    def decide(self, d):
        if d.kind == "blockers":
            return []                                        # never block
        if d.kind == "targets":
            return None                                      # decline targets (no board targeting)
        if d.kind == "mulligan":
            return self._fallback.decide(d)                  # keep (blind); engine-driven mulligan is TBD
        move = self._engine_move(d)
        if move is None:
            return self._fallback.decide(d)
        choice = self._translate(d, move)
        if choice is _FALLBACK:
            _log.info("  engine: move didn't map to a %s option — using %s", d.kind, self._fallback.name)
            return self._fallback.decide(d)
        return choice

    def _translate(self, d, move):
        """Map a witchcraft engine `move` to the MTGA option for decision `d`. Returns the option (an `Action` /
        attacker list / [] / None), or `_FALLBACK` if it can't be mapped."""
        kind = getattr(move, "kind", None)
        if d.kind == "actions":
            if kind in (None, "pass", "skip"):
                return next((a for a in d.options if a.actionType == "ActionType_Pass"), None)
            if kind in ("play", "cast"):                     # land drop / spell — match by the encoded instanceId
                inst = mtga_instance_id(getattr(getattr(move, "card", None), "id", None))
                want = "ActionType_Play" if kind == "play" else "ActionType_Cast"
                a = next((o for o in d.options if o.instanceId == inst and o.actionType == want), None)
                return a if a is not None else _FALLBACK
            return _FALLBACK                                 # activate / unknown — not bridged yet
        if d.kind == "attackers":
            if kind != "attack" or not getattr(move, "attackers", None):
                return []                                    # engine declines combat -> no attack
            want = {mtga_instance_id(a) for a in move.attackers}
            return [{"attackerInstanceId": atk.attackerInstanceId, "target": _attacker_target(atk)}
                    for atk in (d.options or []) if atk.attackerInstanceId in want]
        return _FALLBACK
