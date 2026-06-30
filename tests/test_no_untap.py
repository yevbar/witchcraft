"""test_no_untap.py — 'doesn't untap during its controller's untap step': a flagged permanent stays tapped
through its controller's untap step while others untap. The CONTINUOUS lock (extra=="-") is faithful on
self/anaphora; the one-shot 'during your NEXT untap step' (extra=="next") ABSTAINS. Untap status is public
board state. Run: python3 test_no_untap.py"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from mtg import driver
import observe, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# --- encode: continuous self/anaphora -> doesnt_untap; one-shot 'next' + targeted -> abstain -------
enc = effect_handlers.ENCODE["doesnt_untap"]
check("encode self (continuous) -> doesnt_untap", enc("doesnt_untap", "-", "self", "-") == ("doesnt_untap", 0, "self"))
check("encode it (continuous) -> doesnt_untap", enc("doesnt_untap", "-", "it", "-") == ("doesnt_untap", 0, "self"))
check("encode self with 'next' timing ABSTAINS", enc("doesnt_untap", "-", "self", "next") is None)
check("encode target_creature ABSTAINS (needs real targeting)", enc("doesnt_untap", "-", "target_creature", "-") is None)
check("encode that_creature with 'next' ABSTAINS", enc("doesnt_untap", "-", "that_creature", "next") is None)
check("encode lands_you_control (board scope) ABSTAINS", enc("doesnt_untap", "-", "lands_you_control", "-") is None)

# --- applier: flags the SOURCE permanent ----------------------------------------------------------
s = {"on_battlefield": {("vault",)}, "tapped": {("vault",)}}
effect_handlers.APPLY["doesnt_untap"](driver, s, "a0", 0, "self", "vault", "alice")
check("applier flags the source in doesnt_untap", ("vault",) in s.get("doesnt_untap", set()))

# --- the untap step skips a flagged permanent while others untap ----------------------------------
# alice's untap step: she controls a flagged 'vault' (tapped, doesn't untap) and a plain 'bear' (tapped).
s = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
     "active_player": {("alice",)}, "current_step": {("untap",)},
     "on_battlefield": {("vault",), ("bear",)},
     "printed_type": {("vault", "artifact"), ("bear", "creature")},
     "printed_control": {("alice", "vault"), ("alice", "bear")},
     "counter": set(), "attacks": set(), "blocks": set(),
     "tapped": {("vault",), ("bear",)},
     "doesnt_untap": {("vault",)}}
out = driver.run(s, driver.OUTPUTS)
check("engine derives both as to_untap candidates", {("vault",), ("bear",)} <= out["to_untap"])
driver._apply_outputs(s, out, "alice")
check("the flagged permanent stays tapped through the untap step", ("vault",) in s["tapped"])
check("a non-flagged permanent untaps normally", ("bear",) not in s["tapped"])

# without the flag, the same permanent DOES untap (control)
s2 = {**s, "tapped": {("vault",), ("bear",)}, "doesnt_untap": set()}
out2 = driver.run(s2, driver.OUTPUTS)
driver._apply_outputs(s2, out2, "alice")
check("control: without the flag the permanent untaps", ("vault",) not in s2["tapped"])

# --- Seedborn-style off-turn untap also respects the flag -----------------------------------------
s3 = {"is_player": {("alice",), ("bob",)},
      "active_player": {("bob",)}, "current_step": {("untap",)},
      "on_battlefield": {("vault",), ("muse",)},
      "printed_control": {("alice", "vault"), ("alice", "muse")},
      "seedborn_untap_source": {("muse",)},
      "tapped": {("vault",)}, "doesnt_untap": {("vault",)}}
driver._seedborn_untap(s3, "bob")
check("Seedborn off-turn untap skips a flagged permanent", ("vault",) in s3["tapped"])

# --- info mode: untap/board state is public; the flag lives on public ids -------------------------
s = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("vault",)},
     "printed_control": {("alice", "vault")}, "tapped": {("vault",)}, "doesnt_untap": {("vault",)}}
va = observe.observe(s, "alice"); vb = observe.observe(s, "bob")
check("imperfect info: alice sees vault is tapped (public)", ("vault",) in va.get("tapped", set()))
check("imperfect info: bob sees vault is tapped too (public)", ("vault",) in vb.get("tapped", set()))

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
