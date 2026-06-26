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
