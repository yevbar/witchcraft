"""test_regeneration.py — §701.15 REGENERATION (a replacement SHIELD): the next time the permanent would be
destroyed this turn, instead tap it + remove it from combat + it isn't destroyed (the shield is used up).
Also `cant_be_regenerated` ignores the shield. Shields are PUBLIC info (observe shows them to both seats).
Run: python3 test_regeneration.py"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from mtg import driver
from mtg.engine import observe
import effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# --- encode: faithful shapes / abstain on the rest -------------------------------------------------
enc = effect_handlers.ENCODE["regenerate"]
check("encode self -> player-scoped regenerate", enc("regenerate", "-", "self", "-") == ("regenerate", 0, "controller"))
check("encode it -> player-scoped regenerate", enc("regenerate", "-", "it", "-") == ("regenerate", 0, "controller"))
check("encode enchanted_creature -> regenerate_host", enc("regenerate", "-", "enchanted_creature", "-") == ("regenerate_host", 0, "controller"))
check("encode target_creature -> None (engine targeting owns it)", enc("regenerate", "-", "target_creature", "-") is None)
check("encode target_zombie -> None (subtype abstains)", enc("regenerate", "-", "target_zombie", "-") is None)
check("encode each_creature_you_control -> None (board abstains)", enc("regenerate", "-", "each_creature_you_control", "-") is None)


# A 1/0 creature dies as a §704.5g SBA (0 toughness) -> the zone_change destroy chokepoint.
def _dying(shield=False, cant_regen=False, victim_tough=0):
    s = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
         "active_player": {("alice",)}, "current_step": {("main1",)},
         "on_battlefield": {("victim",)},
         "printed_type": {("victim", "creature")},
         "printed_power": {("victim", 1)}, "printed_toughness": {("victim", victim_tough)},
         "printed_control": {("alice", "victim")},
         "counter": set(), "tapped": set(), "attacks": set(), "blocks": set()}
    if shield:
        s["_regen_shield"] = {("victim",)}
    if cant_regen:
        s["cant_be_regenerated"] = {("victim",)}
    out = driver.run(s, driver.OUTPUTS)
    driver._apply_outputs(s, out, "alice")
    return s

# --- no shield: it dies --------------------------------------------------------------------------
s = _dying(shield=False)
check("no shield: a dying creature goes to the graveyard", ("victim",) in s.get("graveyard", set()))
check("no shield: it left the battlefield", ("victim",) not in s["on_battlefield"])

# --- shield: it SURVIVES, tapped, shield consumed -------------------------------------------------
s = _dying(shield=True)
check("shield: the creature SURVIVES (still on battlefield)", ("victim",) in s["on_battlefield"])
check("shield: it is NOT in the graveyard", ("victim",) not in s.get("graveyard", set()))
check("shield: it is now TAPPED (§701.15c)", ("victim",) in s.get("tapped", set()))
check("shield: the shield is CONSUMED (used up)", ("victim",) not in s.get("_regen_shield", set()))

# --- shield + cant_be_regenerated: it DIES anyway -------------------------------------------------
s = _dying(shield=True, cant_regen=True)
check("cant_be_regenerated: the creature DIES despite the shield", ("victim",) in s.get("graveyard", set()))
check("cant_be_regenerated: it left the battlefield", ("victim",) not in s["on_battlefield"])

# --- second destruction THIS turn (shield already used) -> it dies --------------------------------
# Shield it, kill it once (survives, shield gone), then it would be destroyed again -> dies.
s = _dying(shield=True)
check("first destruction with shield: survives", ("victim",) in s["on_battlefield"])
# re-run the destroy: still a 1/0 SBA, no shield left now
out = driver.run(s, driver.OUTPUTS)
driver._apply_outputs(s, out, "alice")
check("second destruction same turn (shield consumed): it dies", ("victim",) in s.get("graveyard", set()))


# --- removed from combat: a shielded blocker is pulled out of combat ------------------------------
# A 2/1 'victim' blocks a 3/1 attacker -> takes lethal combat damage, but a shield saves it: tapped +
# removed from combat (its block row dropped), so next combat-damage step it is no longer dealt damage.
def _combat_block(shield):
    s = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
         "active_player": {("bob",)}, "current_step": {("combat_damage",)},
         "on_battlefield": {("vblk",), ("atk",)},
         "printed_type": {("vblk", "creature"), ("atk", "creature")},
         "printed_power": {("vblk", 2), ("atk", 3)},
         "printed_toughness": {("vblk", 1), ("atk", 4)},
         "printed_control": {("alice", "vblk"), ("bob", "atk")},
         "attacks": {("atk", "alice")}, "blocks": {("vblk", "atk")},
         "counter": set(), "tapped": set()}
    if shield:
        s["_regen_shield"] = {("vblk",)}
    out = driver.run(s, driver.OUTPUTS)
    driver._apply_outputs(s, out, "bob")
    return s

s = _combat_block(shield=False)
check("combat, no shield: the blocker dies", ("vblk",) in s.get("graveyard", set()))
s = _combat_block(shield=True)
check("combat, shield: the blocker SURVIVES", ("vblk",) in s["on_battlefield"])
check("combat, shield: it was REMOVED from combat (block row cleared)", not any("vblk" in r for r in s.get("blocks", set())))
check("combat, shield: it is tapped", ("vblk",) in s.get("tapped", set()))


# --- target_creature spell path: 'Regenerate target creature' shields the picked creature ---------
# _apply_target_verb's regenerate case (the spell/trigger targeting path) sets a shield on the target.
s = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("mine",)},
     "printed_type": {("mine", "creature")}, "printed_power": {("mine", 2)},
     "printed_toughness": {("mine", 2)}, "printed_control": {("alice", "mine")},
     "counter": set(), "tapped": set(), "attacks": set(), "blocks": set()}
driver._apply_target_verb(s, "spell0", "spell", "regenerate", "-", "mine", "alice", set(), {"mine": "alice"})
check("target regenerate: the target gains a shield", ("mine",) in s.get("_regen_shield", set()))


# --- BOTH info modes: a shield is PUBLIC, both seats observe it ------------------------------------
s = _dying(shield=True)   # victim survives with a residual? no — shield consumed. Set a fresh shield to observe.
s2 = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("c",)},
      "printed_control": {("alice", "c")}, "_regen_shield": {("c",)}}
check("imperfect info: alice (owner) sees the shield", ("c",) in observe.observe(s2, "alice").get("regen_shield", set()))
check("imperfect info: bob (opponent) sees the shield too (public)", ("c",) in observe.observe(s2, "bob").get("regen_shield", set()))

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
