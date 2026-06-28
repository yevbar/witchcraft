"""inthearena.mtga.engine_policy — drive MTGA decisions with a python-mtg (witchcraft) Player.

This is the REVERSE of the Arena->engine state sync (`engine.to_game`): for each MTGA `Decision`, build an
`mtg.Game` positioned at the live board, ask a witchcraft `Player` for its move, and translate that move BACK to
the MTGA option it corresponds to — so a bot developed in python-mtg actually ENACTS in Arena.

The translation hinges on one fact: `engine.build_state` names every VISIBLE card `slug_<instanceId>`, where
`instanceId` is the MTGA GRE id. So an engine move's `card.id` carries the MTGA instanceId straight back, and we
just find the `Decision` option with that id. Attackers and BLOCKS map by the same id (a block move's
`(blocker_id, attacker_id)` pairs -> MTGA pairings). PLAYER targets are wired (`_targets_choice`): a spell that
targets a player is aimed at the OPPONENT (the engine bakes its own target into the cast move, so the SelectTargets
decision is resolved here directly, not by translating a move). NB: the engine only PRODUCES a block
move once the bridge feeds it the declared attackers (`build_state` doesn't model 'attacking' yet) and the player
declares blocks (`AggroPlayer` does, `BlindAggroPlayer` doesn't) — until then a blockers decision declines.

NO BLIND FALLBACK: the engine Player drives EVERY decision — there is deliberately no blind_rage safety net, so
what you see IS the heuristic's behaviour, nothing masked. When a decision can't be enacted (the engine isn't
importable / its datalog isn't on the repo-relative path — run take_over from the repo root — or the chosen move
doesn't map to an MTGA option), the bridge takes the safe NO-OP for that decision (pass / no-attack / no-block /
decline), never a blind aggressive play. Every step logs what the engine chose and whether it mapped, so the
heuristic can be iterated on cleanly and accurately.
"""

from __future__ import annotations

import logging
from typing import Optional

_log = logging.getLogger(__name__)
_NOMAP = object()   # _translate sentinel: "couldn't map this engine move — take the safe no-op (pass/decline)"


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
    move back to the MTGA option. `player` is any `mtg` Player (default `AggroPlayer`, imported lazily so
    inthearena loads without the engine — it beats `HeuristicPlayer` ~68-32 head-to-head and declares blocks).
    There is NO blind fallback: when the engine can't be used or a move can't be mapped, the bridge takes the
    safe no-op (pass / no-attack / no-block / decline) so the engine Player's real behaviour is never masked."""

    name = "witchcraft"

    def __init__(self, player=None, *, opponent_deck=None, seed: int = 0):
        self._player = player
        self._opponent_deck = opponent_deck
        self._seed = seed

    # ── engine side ───────────────────────────────────────────────────────────────────────────────────────
    def _engine_move(self, d):
        """The witchcraft player's chosen move for `d`, or None if the engine can't be used here."""
        try:
            from .engine import to_game
            if self._player is None:
                from mtg.aggro import AggroPlayer       # the default engine bot: beats HeuristicPlayer ~68-32
                self._player = AggroPlayer()             # head-to-head (ladder.compare), and it BLOCKS (Do.BLOCKS)
            # AFFORDABILITY ORACLE: MTGA already tells us which casts we can pay for (auto_payable — no cost, or it
            # found a tap plan). Feed those as the engine's castable set so `can_afford` fires and it surfaces the
            # casts (the engine has no mana model for a static snapshot / uncovered cards). The engine still picks
            # WHICH to cast; Arena stays the affordability ground truth.
            castable = {a.instanceId for a in (d.options or [])
                        if getattr(a, "actionType", None) == "ActionType_Cast"
                        and getattr(a, "instanceId", None) is not None and getattr(a, "auto_payable", False)}
            # GATE LAND DROPS to what MTGA offers as Play, on an actions decision only — the live view can lag a
            # beat and still show a just-played land in hand, and Do.LANDS leads, so an ungated engine re-picks the
            # stale land forever and the bot passes the turn instead of casting. (Non-actions decisions don't offer
            # Plays, so `playable=None` there leaves lands ungated — irrelevant, no land decision is being made.)
            playable = ({a.instanceId for a in (d.options or [])
                         if getattr(a, "actionType", None) == "ActionType_Play"
                         and getattr(a, "instanceId", None) is not None}
                        if d.kind == "actions" else None)
            # MANA VALUE (CMC) of each offered cast, so a curve-out player (deleuze) can deploy cheaper first.
            costs = {a.instanceId: a.mana_value for a in (d.options or [])
                     if getattr(a, "actionType", None) == "ActionType_Cast"
                     and getattr(a, "instanceId", None) is not None and a.manaCost}
            game = to_game(d.view, d.seat, opponent_deck=self._opponent_deck, seed=self._seed,
                           castable=castable, playable=playable, costs=costs)
            move = self._player.bind(game, "alice").choose_move(game)
            _log.info("  engine: %s chose %s%s", getattr(self._player, "name", "?"),
                      getattr(move, "kind", move),
                      f" {getattr(getattr(move, 'card', None), 'id', '')}".rstrip())
            return move
        except Exception as e:
            _log.info("  engine: unusable here (%s: %s) — taking the no-op (pass/decline)", type(e).__name__, e)
            return None

    # ── decide ────────────────────────────────────────────────────────────────────────────────────────────
    def decide(self, d):
        if d.kind == "targets":
            return self._targets_choice(d)                   # aim a player target at the opponent
        if d.kind == "mulligan":
            return "keep"                                    # keep the opener; engine-driven mulligan is TBD
        if d.kind == "assign_damage":
            return "done"                                    # accept MTGA's suggested combat-damage order
        move = self._engine_move(d)
        if move is None:
            return self._noop(d)                             # engine unusable -> pass/decline (no blind play)
        choice = self._translate(d, move)
        if choice is _NOMAP:
            _log.info("  engine: move didn't map to a %s option — taking the no-op (pass/decline)", d.kind)
            return self._noop(d)
        if d.kind == "actions" and getattr(choice, "actionType", None) == "ActionType_Pass":
            self._explain_pass(d)                            # surface WHY a castable-looking hand still passed
        return choice

    def _explain_pass(self, d) -> None:
        """When we PASS an actions decision while MTGA listed casts, log WHY none were taken. Two distinct cases,
        and the log MUST separate them — they point at opposite fixes:

          * NOT auto-payable: the bridge can only cast what MTGA will AUTO-TAP (`auto_payable`); a cast needing a
            MANUAL tap (off-colour, or mana from a creature like a mana dork, that MTGA didn't auto-solve) is
            excluded from `castable`, so the engine never sees it and the click-only bridge couldn't pay it anyway.
            This is a bridge LIMITATION, not an engine bug — expected.
          * auto-payable but STILL passed: at least one offered cast WAS auto-payable (so it was fed to the engine
            as `castable`/`free_cast`), yet the engine chose to pass over it. That is a real ENGINE/surfacing bug —
            a payable creature should always beat passing (creature_choice >> floor). Flag it loudly so the next
            occurrence is self-diagnosing instead of a silent skip.

        Makes 'skipped to combat with cards in hand' self-explanatory in the log."""
        casts = [a for a in (d.options or []) if getattr(a, "actionType", None) == "ActionType_Cast"]
        if not casts:
            return
        from . import cards
        payable = [a for a in casts if getattr(a, "auto_payable", False)]
        unpaid = [a for a in casts if not getattr(a, "auto_payable", False)]
        if payable:
            _log.warning("  engine: PASSED with %d auto-payable cast(s) offered — this is an ENGINE bug, a payable "
                         "spell should beat passing (check it surfaced as a cast move + scored above floor): %s",
                         len(payable), [cards.label(a.grpId) for a in payable][:5])
        elif unpaid:
            _log.info("  engine: passed with %d cast(s) offered — %d not auto-payable (need a manual tap MTGA "
                      "didn't auto-solve; the click bridge can't pay those): %s", len(casts), len(unpaid),
                      [cards.label(a.grpId) for a in unpaid][:5])

    @staticmethod
    def _noop(d):
        """The safe do-nothing choice for decision `d` when the engine can't be enacted — PASS an actions
        decision, decline combat (no attack / no block), decline a target. Never a blind aggressive play, so the
        engine Player's real behaviour stays visible."""
        if d.kind == "actions":
            return next((a for a in (d.options or []) if getattr(a, "actionType", None) == "ActionType_Pass"), None)
        if d.kind in ("attackers", "blockers"):
            return []                                        # no attack / no block
        return None                                          # targets / anything else -> decline

    def _targets_choice(self, d):
        """Resolve MTGA's SelectTargets the way HeuristicPlayer.resolve_choice resolves it in the engine: when a
        target slot can hit a PLAYER, aim at the OPPONENT. A player candidate is one that ISN'T a battlefield
        permanent (its targetInstanceId isn't a known game object); the opponent is the player candidate whose
        id isn't our seat. Returns a list of picks (one per required slot) for the executor — a player pick
        carries `player=<seat>`, a permanent pick carries `instanceId`. A slot with no player candidate falls
        back to the first legal candidate so play still progresses (creature-target spells aren't the focus
        here yet). [] / no slots -> None (decline). This mirrors the engine rule but on MTGA's target dicts;
        the engine bakes its own target into the cast move, so there's no engine move to translate here."""
        picks = []
        for slot in (d.options or []):
            cands = (slot.get("targets") or []) if isinstance(slot, dict) else []
            if not cands:
                continue
            ids = [(c, c.get("targetInstanceId")) for c in cands if c.get("targetInstanceId") is not None]
            players = [(c, tid) for (c, tid) in ids if tid not in d.view.objects]   # not a permanent -> a player
            opp = next((tid for (c, tid) in players if tid != d.seat), None)
            if opp is not None:
                picks.append({"player": opp})
            elif ids:
                picks.append({"instanceId": ids[0][1]})                             # creature/permanent target
        return picks or None

    def _translate(self, d, move):
        """Map a witchcraft engine `move` to the MTGA option for decision `d`. Returns the option (an `Action` /
        attacker list / [] / None), or `_NOMAP` if it can't be mapped (decide then takes the safe no-op)."""
        kind = getattr(move, "kind", None)
        if d.kind == "actions":
            if kind in (None, "pass", "skip"):
                return next((a for a in d.options if a.actionType == "ActionType_Pass"), None)
            if kind in ("play", "cast"):                     # land drop / spell — match by the encoded instanceId
                inst = mtga_instance_id(getattr(getattr(move, "card", None), "id", None))
                want = "ActionType_Play" if kind == "play" else "ActionType_Cast"
                a = next((o for o in d.options if o.instanceId == inst and o.actionType == want), None)
                return a if a is not None else _NOMAP
            return _NOMAP                                 # activate / unknown — not bridged yet
        if d.kind == "attackers":
            if kind != "attack" or not getattr(move, "attackers", None):
                return []                                    # engine declines combat -> no attack
            want = {mtga_instance_id(a) for a in move.attackers}
            return [{"attackerInstanceId": atk.attackerInstanceId, "target": _attacker_target(atk)}
                    for atk in (d.options or []) if atk.attackerInstanceId in want]
        if d.kind == "blockers":
            # The engine's block move is kind == "block" with `blocks` = a set of (blocker_id, attacker_id) engine
            # pairs (slug_<instanceId>). Map each back to MTGA ids and keep only LEGAL pairings per the GRE
            # (declareBlockersReq lists, per blocker, the attackers it may block). [] -> decline ('No Blocks').
            if kind != "block" or not getattr(move, "blocks", None):
                return []
            legal = {}                                       # blockerInstanceId -> {attackers it may block}
            for b in (d.options or []):
                bid = b.get("blockerInstanceId") if isinstance(b, dict) else None
                if bid is not None:
                    legal[bid] = set(b.get("attackerInstanceIds") or []) | set(b.get("selectedAttackerInstanceIds") or [])
            pairs = []
            for pair in move.blocks:
                if not isinstance(pair, (tuple, list, frozenset, set)) or len(pair) != 2:
                    continue
                blk, atk = (mtga_instance_id(x) for x in tuple(pair))
                if blk is not None and atk is not None and atk in legal.get(blk, ()):
                    pairs.append({"blockerInstanceId": blk, "attackerInstanceId": atk})
            return pairs
        return _NOMAP
