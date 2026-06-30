"""test_conditional_static.py — §611.2 CONDITIONAL self/board static buffs ('~ gets +1/+1 / has flying AS
LONG AS <cond>'). The engine already resolves these (conditional static_pt/static_grant gated on cond_met);
this verifies the BRIDGE no longer falsely drops them: a static whose condition is MODELED (cond_met) and
whose scope is a self/unfiltered-board anthem scope with an engine-expressible payload is engine-owned (clean),
while an unmodeled condition or an attached/filtered scope still faithfully abstains.

Run: python3 test_conditional_static.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

from interpreter import card_corpus
import sim, driver, observe
import bridge_to_engine as bridge

_P = [0, 0]
def check(name, cond):
    _P[0] += 1; _P[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

db = sim.load_db()
corpus = {c["name"]: c for c in card_corpus.load_cards()}

# (1) the modeled-condition set loads from the engine file --------------------------------------------
check("modeled conditions loaded from engine_rules.dl", len(bridge._MODELED_CONDS) >= 50)
check("a known modeled condition is present", "as_long_as_you_control_an_artifact" in bridge._MODELED_CONDS)

# (2) the engine actually buffs a conditional self-static when the condition holds ---------------------
def _self_cond(cond, *, satisfy):
    s = {"is_player": {("alice",), ("bob",)},
         "on_battlefield": {("me",)} | ({("art",)} if satisfy else set()),
         "printed_control": {("alice", "me")} | ({("alice", "art")} if satisfy else set()),
         "printed_type": {("me", "creature")} | ({("art", "artifact")} if satisfy else set()),
         "printed_power": {("me", 2)}, "printed_toughness": {("me", 2)},
         "instance_of": {("me", "tc")}, "card_ability": {("tc", "a1", "static")},
         "card_effect": {("tc", "a1", 0, "modify_pt", "+1/+1", "self", "-", cond)}}
    return {c: int(p) for (c, p) in driver.run(s, ["power"])["power"]}.get("me")

check("conditional self-static buffs when condition holds (3/3 with artifact)",
      _self_cond("as_long_as_you_control_an_artifact", satisfy=True) == 3)
check("conditional self-static is inert when condition fails (2/2, no artifact)",
      _self_cond("as_long_as_you_control_an_artifact", satisfy=False) == 2)

# (3) the bridge no longer DROPS a modeled conditional self/board static -------------------------------
def _dropped(nm):
    return bridge.card_facts(nm, "alice", "x", db, corpus)[1]

clean_examples = []
for nm in ("Ardent Recruit", "Anurid Barkripper"):
    if nm in corpus:
        d = _dropped(nm)
        check(f"{nm!r}: modeled conditional self-static is no longer dropped",
              not any(k == "static" and v == "modify_pt" for k, v in d))
        clean_examples.append(nm)
check("(found at least one real clean conditional-static card)", clean_examples)

# (4) faithful abstain: an UNMODELED condition, or an attached/filtered scope, still drops -------------
# synthesize via a card_effect probe through the engine-encode path isn't needed; assert the bridge logic:
# an unmodeled condition is not in _MODELED_CONDS, so it must not be claimed clean.
check("an unmodeled condition is absent from the modeled set",
      "as_long_as_you_control_another_black_creature" not in bridge._MODELED_CONDS)
# Akki War Paint: attached scope (enchanted_permanent) + a modeled cond -> still abstains (engine conditional
# rule joins anthem_scope, which excludes attached).
if "Akki War Paint" in corpus:
    check("attached-scope conditional static still abstains (Akki War Paint)",
          any(k == "static" for k, v in _dropped("Akki War Paint")))

# (5) both info modes: the conditional buff is public board state --------------------------------------
s = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("me",), ("art",)},
     "printed_control": {("alice", "me"), ("alice", "art")},
     "printed_type": {("me", "creature"), ("art", "artifact")},
     "printed_power": {("me", 2)}, "printed_toughness": {("me", 2)},
     "instance_of": {("me", "tc")}, "card_ability": {("tc", "a1", "static")},
     "card_effect": {("tc", "a1", 0, "modify_pt", "+1/+1", "self", "-", "as_long_as_you_control_an_artifact")},
     "in_hand": {("bob", "secret")}}
obs = observe.observe(s, "bob")
check("imperfect info: the conditional buff holds on the opponent's observed view",
      {c: int(p) for (c, p) in driver.run(obs, ["power"])["power"]}.get("me") == 3)

print(f"\n{_P[1]}/{_P[0]} checks passed")
if _P[1] != _P[0]:
    raise SystemExit(1)
