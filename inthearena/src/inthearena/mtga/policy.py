"""inthearena.mtga.policy — choose an MTG Arena decision from the menu the GRE hands the local player.

A `Policy` maps a `gre.Decision` (the typed legal options MTGA offered) to a choice. The point of the arena
bridge is to drop OUR bots in here. `AggroPolicy` is the first wiring: `mtg.aggro.AggroPlayer`'s philosophy
expressed directly over MTGA's decision kinds — play a land, else cast, else activate, else pass; attack with
everything; barely block — with NO rules engine, because the GRE already supplies the legal set.
"""

from __future__ import annotations

from typing import Optional, Protocol

from . import cards
from .gre import Action, Attacker, Decision

# Action ranking for a priority decision. Mana abilities are auto-resolved by MTGA's tap-for-cost, so a
# proactive policy ignores them and never just floats mana.
_PLAY = "ActionType_Play"            # a land drop
_CAST = "ActionType_Cast"            # a spell
_ACTIVATE = "ActionType_Activate"    # a non-mana activated ability
_PASS = "ActionType_Pass"


class Policy(Protocol):
    """Decide an MTGA decision. Return the chosen option(s), or a sentinel ('keep', [], None) per kind."""

    def decide(self, d: Decision):
        ...


class AggroPolicy:
    """Develop and attack, relentlessly — the `mtg.aggro.AggroPlayer` policy over the live MTGA menu."""

    name = "aggro"

    def decide(self, d: Decision):
        return getattr(self, f"_on_{d.kind}", self._on_default)(d)

    # priority: land > spell > activate > pass (the whole aggro strategy, one ranking)
    def _on_actions(self, d: Decision) -> Optional[Action]:
        for kind in (_PLAY, _CAST, _ACTIVATE):
            move = next((a for a in d.options if a.actionType == kind), None)
            if move is not None:
                return move
        return next((a for a in d.options if a.actionType == _PASS), None)

    # attack with EVERY qualified attacker, each at the opponent (first legal player recipient)
    def _on_attackers(self, d: Decision) -> list:
        chosen = []
        for atk in d.options:                                # atk: Attacker
            recips = atk.legalDamageRecipients
            target = next((r for r in recips if r.type == "DamageRecType_Player"), None) or \
                     (recips[0] if recips else None)
            chosen.append({"attackerInstanceId": atk.attackerInstanceId, "target": target})
        return chosen

    def _on_blockers(self, d: Decision) -> list:
        return []                                            # pure aggro: never block

    def _on_mulligan(self, d: Decision) -> str:
        return "keep"                                        # always accept the opening hand

    def _on_targets(self, d: Decision):
        return d.options[0] if d.options else None           # best-effort: first legal target

    def _on_default(self, d: Decision):
        return d.options[0] if d.options else None


class ArenaAggroPolicy(AggroPolicy):
    """`aggro_arena` — aggro tuned to beat Arena's built-in practice bot. Same relentless develop-and-attack core
    (the out-of-the-box bot folds to a clean curve), with the one own-goal removed: a basic keepable-hand
    mulligan instead of blind keep. Further tuning levers (as we play games): cast ORDER (curve out / highest-
    impact first, vs the current first-legal pick), and SELECTIVE blocking to not die while racing."""

    name = "aggro_arena"

    def _on_mulligan(self, d: Decision) -> str:
        # Keep a workable opener; only ship the unkeepable extremes (no lands, or flooded). London mulligan
        # always shows 7, so count lands in hand. If the view doesn't have the hand yet, keep (don't churn).
        hand = d.view.hand(d.seat) if d.seat is not None else []
        lands = sum(1 for o in hand if "CardType_Land" in (o.cardTypes or []))
        if not hand:
            return "keep"
        return "keep" if 1 <= lands <= 5 else "mulligan"


class BlindRagePolicy(AggroPolicy):
    """`blind_rage` — pure blind aggro that never engages a decision needing board targeting. Keep, develop,
    swing with EVERYTHING; declare NO blocks (just pass when blocks come around) and decline any target. So it
    never stalls on an unwired interaction — every decision resolves to a hand click, 'All Attack', or the
    advance button. For decks where racing without ever blocking actually works (no targeted spells needed)."""

    name = "blind_rage"

    # _on_mulligan -> 'keep', _on_actions (land>cast>activate>pass), _on_attackers (all), _on_blockers ([])
    # are inherited from AggroPolicy — already exactly blind aggro. Only the targeting paths change:

    def _on_targets(self, d: Decision):
        return None                                          # never target — pass (deck has no targeted spells)

    def _on_default(self, d: Decision):
        return None                                          # anything unmapped: decline/pass, don't risk it


def describe(d: Decision, choice) -> str:
    """A short human-readable line for a (decision, choice), resolving grpIds to card names via `cards`."""
    def by_instance(inst):
        o = d.view.objects.get(inst)
        return cards.label(o.grpId) if o else f"inst{inst}"

    if d.kind == "actions":
        if not choice or choice.actionType == _PASS:
            return "pass"
        verb = (choice.actionType or "?").replace("ActionType_", "")
        return f"{verb} {cards.label(choice.grpId)}"
    if d.kind == "attackers":
        if not choice:
            return "no attack"
        return "attack: " + ", ".join(by_instance(a["attackerInstanceId"]) for a in choice)
    if d.kind == "blockers":
        return "no blocks"
    if d.kind == "mulligan":
        return f"mulligan -> {choice}"
    if d.kind == "targets":
        return "target chosen" if choice else "no target"
    return str(choice)
