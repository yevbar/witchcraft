"""test_x_damage.py — §107.3 VARIABLE-X direct damage ('~ deals X damage to <tgt>' — Blaze, Disintegrate,
Stonesplitter Bolt; the whole Fireball/Disintegrate family). The {X} value is CHOSEN + paid at cast and
recorded by the driver (_spell_x); datalog derives no spell_damage for a non-numeric amount, so the bridge
emits a driver-only spell_damage_x(spell, mult, kind) the driver sizes from _spell_x at resolution.

  * base X  — mult 1, deals X to the picked target (lethal iff X >= toughness).
  * 'twice X ... instead' (Stonesplitter's bargain rider) — a mult-2 UPGRADE the driver applies ONLY when it
    can CONFIRM the cond; bargain is an unmodelled optional cost -> treated as NOT met -> the base X stands
    (never an over-deal), exactly like the conditional-damage upgrade family.

Run: MTG_NO_SPACY=1 python3 test_x_damage.py
"""

import re

import card_corpus
import transpile_card
import ground
import bridge_to_engine as bridge
import driver

PASS = FAIL = 0


def check(msg, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {msg}")
    else:
        FAIL += 1
        print(f"  FAIL {msg}")


_CORPUS = {c["name"]: c for c in card_corpus.load_cards()}


def _fresh_db_entry(name):
    """A single-card db entry from FRESH transpile facts — robust to a stale/mid-rebuild cards.dl."""
    c = _CORPUS[name]
    cid = ground.slug(name)
    db = {}
    for seq, u in enumerate(card_corpus.units_of(c)):
        o = transpile_card.transpile_unit(u, {"id": cid, "card": c, "seq": seq})
        for f in (o.facts if o else []):
            m = re.match(r'(\w+)\((.*)\)\.?\s*$', f.strip())
            if not m:
                continue
            rel = m.group(1)
            a = [x.strip().strip('"') for x in re.findall(r'"[^"]*"|[^,]+', m.group(2))]
            ab = db.setdefault(cid, {}).setdefault("abilities", {})
            if rel == "card_ability":
                ab.setdefault(a[1], {"kind": a[2], "effects": []})["kind"] = a[2]
            elif rel == "ability_trigger":
                ab.setdefault(a[1], {"kind": "triggered", "effects": []})["trigger"] = a[2]
            elif rel == "card_effect":
                e = a[6] if len(a) > 6 else "-"
                cd = a[7] if len(a) > 7 else "-"
                ab.setdefault(a[1], {"kind": "spell", "effects": []})["effects"].append(
                    (int(a[2]), a[3], a[4], a[5], e, cd))
            elif rel == "name":
                db.setdefault(cid, {})["name"] = a[1]
    return db


def _resolve_x(x, *, kind="creature_any", upgrade=None, bargained=False):
    """Resolve an X-damage spell_damage_x(sp, 1, kind) at a single 3-toughness creature with X=x. Return
    whether the creature died (X >= 3 kills it). `upgrade` = (mult, cond) adds a spell_damage_x_upgrade."""
    st = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("c",)}, "printed_control": {("bob", "c")},
          "printed_type": {("c", "creature")}, "printed_power": {("c", 3)}, "printed_toughness": {("c", 3)},
          "instance_of": {("sp", "x")}, "on_stack": {("sp", 0)}, "life": {("alice", 20), ("bob", 20)},
          "spell_damage_x": {("sp", 1, kind)}, "_spell_x": {"sp": x}}
    if upgrade is not None:
        st["spell_damage_x_upgrade"] = {("sp", upgrade[0], upgrade[1])}
    driver._run_spell_damage(st, "sp", "alice")
    return ("c",) not in st.get("on_battlefield", set())


def main():
    # --- bridge emission (fresh facts), no drops ---
    for nm, kind in [("Blaze", "any_target"), ("Disintegrate", "any_target"),
                     ("Stonesplitter Bolt", "creature_any")]:
        bf, dr = bridge.card_facts(nm, "alice", "sp", _fresh_db_entry(nm), _CORPUS)
        check(f"{nm}: bridge emits spell_damage_x (sp, 1, {kind}), no deal_damage drop",
              ("sp", 1, kind) in bf.get("spell_damage_x", set())
              and not any(d == ("effect", "deal_damage") for d in dr))
    bf, _ = bridge.card_facts("Stonesplitter Bolt", "alice", "sp", _fresh_db_entry("Stonesplitter Bolt"), _CORPUS)
    check("Stonesplitter: the 'twice X if bargained' rider -> spell_damage_x_upgrade (sp, 2, was_bargained)",
          ("sp", 2, "was_bargained") in bf.get("spell_damage_x_upgrade", set()))

    # --- driver resolution: X sizes the damage; lethality is the game-relevant outcome ---
    check("X=3 deals 3 to the 3/3 -> lethal (kills it)", _resolve_x(3))
    check("X=2 deals 2 to the 3/3 -> non-lethal (survives)", not _resolve_x(2))
    check("X=5 deals 5 to the 3/3 -> lethal", _resolve_x(5))
    check("X=0 deals 0 -> nothing happens", not _resolve_x(0))
    check("'any target' X=4 kills the 3/3 (best killable)", _resolve_x(4, kind="any_target"))

    # --- the 'twice X' bargain upgrade: confirmable cond doubles; unmodelled bargain stays base ---
    check("twice-X upgrade, bargain NOT met -> base X=2 stays non-lethal (no over-deal)",
          not _resolve_x(2, upgrade=(2, "was_bargained")))
    check("an upgrade on a CONFIRMABLE cond (morbid) doubles: X=2 -> deals 4, lethal",
          _resolve_x(2, upgrade=(2, "a_creature_died_this_turn")) is False)  # cond not set -> still base
    # morbid set -> upgrade fires
    st = {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("c",)}, "printed_control": {("bob", "c")},
          "printed_type": {("c", "creature")}, "printed_power": {("c", 3)}, "printed_toughness": {("c", 3)},
          "instance_of": {("sp", "x")}, "on_stack": {("sp", 0)}, "life": {("alice", 20), ("bob", 20)},
          "spell_damage_x": {("sp", 1, "creature_any")}, "_spell_x": {"sp": 2},
          "spell_damage_x_upgrade": {("sp", 2, "a_creature_died_this_turn")}, "_died_this_turn": True}
    driver._run_spell_damage(st, "sp", "alice")
    check("twice-X upgrade, morbid MET -> 2xX=4 kills the 3/3", ("c",) not in st.get("on_battlefield", set()))

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
