"""test_transform_self.py — §712 TRANSFORM as a directly-encoded self effect verb (effect_handlers/transform.py).

The driver already owns the flip (driver._transform), but the raw `transform` verb had no bridge ENCODE entry,
so 'transform this'/'transform it' clauses dropped at encoding (169 self-scope cards). This wires the encoder:
  1. ENCODE routes self/'it'/bare-name to the source; target/other-permanent/token scopes ABSTAIN;
  2. APPLY flips the source via driver._transform (instance_of -> back slug), no-op without a transform_target;
  3. end-to-end: a transforming DFC's self clause no longer drops through the bridge;
  4. both info modes: the flipped identity is public board state and reads identically on observe().
Run: python3 test_transform_self.py
"""
from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import io, contextlib
from interpreter import card_corpus
import effect_handlers, driver, sim, observe
import bridge_to_engine as bridge

effect_handlers.load()
_P = [0, 0]
def check(name, cond):
    _P[0] += 1; _P[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

def _quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)

# (1) the bridge ENCODE entry --------------------------------------------------------------------------
enc = effect_handlers.ENCODE.get("transform")
check("transform has a bridge ENCODE entry", enc is not None)
check("encoder routes 'self' to the source", enc("transform", 0, "self", "-") == ("transform", 0, "self"))
check("encoder routes 'it' to the source", enc("transform", 0, "it", "-") == ("transform", 0, "self"))
check("encoder routes a bare self-name token to the source", enc("transform", 0, "ashling", "-") == ("transform", 0, "self"))
check("encoder ABSTAINS on target_creature", enc("transform", 0, "target_creature", "-") is None)
check("encoder ABSTAINS on each_creature", enc("transform", 0, "each_creature", "-") is None)
check("encoder ABSTAINS on an incubator token scope", enc("transform", 0, "target_incubator_token_you_control", "-") is None)

# (2)+(3) end-to-end through the bridge: a transforming DFC's self clause no longer drops -----------------
db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
dfc = next((n for n in corpus if "Delver of Secrets" in n), None) or "Aberrant Researcher // Perfected Form"
f, dropped = bridge.card_facts(dfc, "alice", "d1", db, corpus)
check(f"{dfc}: the self transform clause is no longer dropped", all(d != "transform" for _k, d in dropped))
check("the card carries a transform_target the flip uses", bool(f.get("transform_target")))

# (2) the applier flips the source via driver._transform ----------------------------------------------
front = sorted(s for (_o, s) in f.get("instance_of", set()))
tt = set(f.get("transform_target", set()))
st = {"instance_of": set(f.get("instance_of", set())), "on_battlefield": {("d1",)},
      "printed_control": {("alice", "d1")}, "transform_target": set(tt)}
_quiet(effect_handlers.APPLY["transform"], driver, st, "trig", 0, "self", "d1", "alice")
back = next(b for (_o, b) in tt)
check("applier flips instance_of(d1) to the back slug", ("d1", back) in st["instance_of"])
check("the front slug is gone after the flip", all(s != front[0] for (_o, s) in st["instance_of"]))

# applier is a faithful no-op on a permanent with no other face
st2 = {"instance_of": {("x", "grizzly_bears")}, "on_battlefield": {("x",)},
       "printed_control": {("alice", "x")}, "transform_target": set()}
_quiet(effect_handlers.APPLY["transform"], driver, st2, "trig", 0, "self", "x", "alice")
check("no-op without a transform_target (a non-DFC stays put)", ("x", "grizzly_bears") in st2["instance_of"])

# (4) both info modes: the flipped identity is public ---------------------------------------------------
st3 = {"is_player": {("alice",), ("bob",)}, "instance_of": {("d1", back)}, "on_battlefield": {("d1",)},
       "printed_control": {("alice", "d1")}, "in_hand": {("alice", "secret")}}
obs = observe.observe(st3, "bob")
check("imperfect info: observe(bob) hides alice's hand", not any(p == "alice" for (p, _c) in obs.get("in_hand", set())))
check("imperfect info: bob sees the transformed (back) identity on the battlefield", ("d1", back) in obs.get("instance_of", set()))

print(f"\n{_P[1]}/{_P[0]} checks passed")
if _P[1] != _P[0]:
    raise SystemExit(1)
