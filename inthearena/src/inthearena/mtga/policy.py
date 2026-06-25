"""inthearena.mtga.policy — choose an MTG Arena decision from the menu the GRE hands the local player.

A `Policy` maps a `gre.Decision` (the legal options MTGA offered) to a choice. The point of the arena bridge
is to drop OUR bots in here. `AggroPolicy` is the first wiring: it is the `mtg.aggro.AggroPlayer` philosophy
expressed directly over MTGA's decision kinds — play a land, else cast a spell, else activate, else pass;
attack with everything; barely block — with NO rules engine, because the GRE already supplies the legal set.

A choice is the selected raw GRE option(s) (or a sentinel), so a downstream executor / shadow logger can act
on or display it. `AggroPolicy` is intentionally tiny and readable — the strategy is one ranking, like
`mtg.aggro`.
"""

from __future__ import annotations

from typing import Optional, Protocol

from .gre import Decision

# Action ranking for a priority decision (ActionsAvailableReq). Mana abilities are auto-resolved by MTGA's
# tap-for-cost, so a proactive policy ignores them and never just floats mana.
_PLAY = "ActionType_Play"            # a land drop
_CAST = "ActionType_Cast"            # a spell
_ACTIVATE = "ActionType_Activate"    # a non-mana activated ability
_PASS = "ActionType_Pass"


class Policy(Protocol):
    """Decide an MTGA decision. Return the chosen GRE option(s), or a sentinel ('keep', [], None) per kind."""

    def decide(self, d: Decision):  # -> chosen option(s) | sentinel
        ...


class AggroPolicy:
    """Develop and attack, relentlessly — the `mtg.aggro.AggroPlayer` policy over the live MTGA menu."""

    name = "aggro"

    def decide(self, d: Decision):
        return getattr(self, f"_on_{d.kind}", self._on_default)(d)

    # priority: land > spell > activate > pass (the whole aggro strategy, one ranking)
    def _on_actions(self, d: Decision):
        for kind in (_PLAY, _CAST, _ACTIVATE):
            move = next((a for a in d.options if a.get("actionType") == kind), None)
            if move is not None:
                return move
        return next((a for a in d.options if a.get("actionType") == _PASS), None)

    # attack with EVERY qualified attacker, each at the opponent (first legal player recipient)
    def _on_attackers(self, d: Decision):
        chosen = []
        for atk in d.options:
            recips = atk.get("legalDamageRecipients", [])
            target = next((r for r in recips if r.get("type") == "DamageRecType_Player"), None) or \
                     (recips[0] if recips else None)
            chosen.append({"attackerInstanceId": atk.get("attackerInstanceId"), "target": target})
        return chosen

    def _on_blockers(self, d: Decision):
        return []                                          # pure aggro: never block

    def _on_mulligan(self, d: Decision):
        return "keep"                                      # keep seven (a land-count heuristic is a TODO)

    def _on_targets(self, d: Decision):
        return d.options[0] if d.options else None         # best-effort: first legal target

    def _on_optional(self, d: Decision):
        return None                                        # decline optional actions by default

    def _on_default(self, d: Decision):
        return d.options[0] if d.options else None


def describe(d: Decision, choice) -> str:
    """A short human-readable line for a (decision, choice) — used by the shadow logger. Resolves card hints
    from the GameView objects where possible (grpId + types/PT; the grpId→name DB is a later add)."""
    def card(inst):
        o = d.view.objects.get(inst, {})
        types = "/".join(t.replace("CardType_", "") for t in o.get("cardTypes", [])) or "?"
        p, t = o.get("power", {}).get("value"), o.get("toughness", {}).get("value")
        pt = f" {p}/{t}" if p is not None else ""
        return f"grp{o.get('grpId', '?')}({types}{pt})"

    if d.kind == "actions":
        if not choice or choice.get("actionType") == _PASS:
            return "pass"
        at = choice.get("actionType", "?").replace("ActionType_", "")
        return f"{at} {card(choice.get('instanceId'))}"
    if d.kind == "attackers":
        if not choice:
            return "no attack"
        return "attack: " + ", ".join(card(a["attackerInstanceId"]) for a in choice)
    if d.kind == "blockers":
        return "no blocks"
    if d.kind == "mulligan":
        return f"mulligan -> {choice}"
    if d.kind == "targets":
        return f"target {choice}" if choice else "no target"
    return str(choice)
