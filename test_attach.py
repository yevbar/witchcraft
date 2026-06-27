"""test_attach.py — §301/§303 Aura attachment. An Aura with a static 'enchanted creature gets +X/+X' /
'has <keyword>' buff is mapped to scope 'attached'; on resolution the driver attaches it to a creature
(beneficial -> own strongest, negative -> enemy strongest) via attached_to, and the engine applies the buff
to that creature through the §613 layers. §704.5n: when the host leaves, the Aura is put into the graveyard.

Run: python3 test_attach.py   (needs datalog/cards.dl for the bridge checks)
"""

from __future__ import annotations

import contextlib
import io

import driver
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _powers(state: dict) -> dict:
    return {c: int(n) for (c, n) in driver.run(state, ["power"])["power"]}


def _board() -> dict:
    """alice controls 'bear' (2/2) and 'wolf' (1/1); bob controls 'ogre' (3/3)."""
    return {
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": {("bear",), ("wolf",), ("ogre",)},
        "printed_type": {("bear", "creature"), ("wolf", "creature"), ("ogre", "creature")},
        "printed_subtype": set(), "printed_power": {("bear", 2), ("wolf", 1), ("ogre", 3)},
        "printed_toughness": {("bear", 2), ("wolf", 1), ("ogre", 3)},
        "printed_control": {("alice", "bear"), ("alice", "wolf"), ("bob", "ogre")},
        "counter": set(), "tapped": set(), "attached_to": set(), "graveyard": set(),
    }


def _driver_checks() -> None:
    # a beneficial Aura (+2/+2) entering for alice attaches to her STRONGEST creature (bear), buffing it.
    st = _board()
    st["printed_subtype"].add(("holystr", "aura"))
    st["static_pt"] = {("holystr", 2, 2, "attached")}
    st["on_battlefield"].add(("holystr",))
    with contextlib.redirect_stdout(io.StringIO()):
        driver._attach_aura(st, "holystr", "alice")
    check("beneficial aura attaches to controller's strongest creature (bear)",
          ("holystr", "bear") in st["attached_to"])
    check("the enchanted creature is buffed (bear 2/2 -> 4/4)", _powers(st).get("bear") == 4)
    check("a different own creature is NOT buffed (wolf stays 1)", _powers(st).get("wolf") == 1)
    check("the opponent's creature is NOT buffed (ogre stays 3)", _powers(st).get("ogre") == 3)

    # a negative Aura (-2/-0) entering for alice attaches to the OPPONENT's strongest creature (ogre).
    st = _board()
    st["printed_subtype"].add(("weakness", "aura"))
    st["static_pt"] = {("weakness", -2, 0, "attached")}
    st["on_battlefield"].add(("weakness",))
    with contextlib.redirect_stdout(io.StringIO()):
        driver._attach_aura(st, "weakness", "alice")
    check("negative aura attaches to the opponent's strongest creature (ogre)",
          ("weakness", "ogre") in st["attached_to"])
    check("the enchanted enemy is weakened (ogre 3 -> 1)", _powers(st).get("ogre") == 1)

    # a keyword Aura (flying) grants the keyword to the attached creature.
    st = _board()
    st["printed_subtype"].add(("wings", "aura"))
    st["static_grant"] = {("wings", "flying", "attached")}
    st["on_battlefield"].add(("wings",))
    with contextlib.redirect_stdout(io.StringIO()):
        driver._attach_aura(st, "wings", "alice")
    host = next((c for (a, c) in st["attached_to"] if a == "wings"), None)
    check("keyword aura attaches to an own creature", host in ("bear", "wolf"))
    check("the attached creature gains the keyword",
          (host, "flying") in driver.run(st, ["has_keyword"])["has_keyword"])

    # §704.5n — when the host leaves, the Aura goes to the graveyard and stops buffing.
    st = _board()
    st["printed_subtype"].add(("holystr", "aura"))
    st["static_pt"] = {("holystr", 2, 2, "attached")}
    st["on_battlefield"].add(("holystr",))
    st["attached_to"].add(("holystr", "bear"))
    st["on_battlefield"].discard(("bear",))                   # the host died
    st["graveyard"].add(("bear",))
    with contextlib.redirect_stdout(io.StringIO()):
        driver._aura_sba(st)
    check("an Aura whose host left is put into the graveyard (§704.5n)",
          ("holystr",) in st["graveyard"] and ("holystr",) not in st["on_battlefield"])
    check("the dangling attachment is cleared", not st["attached_to"])

    # a non-buff Aura (no static 'attached' facts) isn't auto-attached by this path (e.g. Pacifism).
    st = _board()
    st["printed_subtype"].add(("pacifism", "aura"))
    with contextlib.redirect_stdout(io.StringIO()):
        driver._attach_aura(st, "pacifism", "alice")
    check("an aura with no P/T-or-keyword buff isn't attached here", not st["attached_to"])


def _equipment_checks() -> None:
    # §301.5 the driver equips an Equipment to the controller's strongest creature; the buff then applies.
    st = _board()
    st["printed_subtype"].add(("sword", "equipment"))
    st["on_battlefield"].add(("sword",))
    st["static_pt"] = {("sword", 2, 0, "attached")}
    st["attached_to"] = set()
    with contextlib.redirect_stdout(io.StringIO()):
        driver._equip(st, "sword", "alice")
    check("equip attaches to the controller's strongest creature (bear)", ("sword", "bear") in st["attached_to"])
    check("the equipped creature gets the buff (bear 2/0 -> 4)", _powers(st).get("bear") == 4)

    # equip MOVES from a prior host (§701.3) rather than stacking attachments.
    st2 = _board()
    st2["printed_subtype"].add(("sword", "equipment"))
    st2["static_pt"] = {("sword", 2, 0, "attached")}
    st2["attached_to"] = {("sword", "wolf")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._equip(st2, "sword", "alice")
    check("re-equip moves the equipment (only one host)",
          ("sword", "bear") in st2["attached_to"] and ("sword", "wolf") not in st2["attached_to"])

    # §704.5q — when the equipped creature leaves, the Equipment UNATTACHES but stays on the battlefield.
    st3 = _board()
    st3["printed_subtype"].add(("sword", "equipment"))
    st3["on_battlefield"].add(("sword",))
    st3["attached_to"] = {("sword", "bear")}
    st3["on_battlefield"].discard(("bear",))
    with contextlib.redirect_stdout(io.StringIO()):
        driver._aura_sba(st3)
    check("an Equipment whose host left stays on the battlefield (§704.5q)", ("sword",) in st3["on_battlefield"])
    check("the equipment's attachment is cleared", not st3["attached_to"])


def _control_checks() -> None:
    # §613 a control-stealing Aura (Control Magic, Persuasion): attaches to the opponent's strongest creature
    # and feeds eff_gain_control so the engine flips control to the Aura's controller.
    st = _board()
    st["printed_subtype"].add(("magic", "aura"))
    st["on_battlefield"].add(("magic",))
    st["aura_control"] = {("magic",)}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._attach_aura(st, "magic", "alice")
    check("a control Aura attaches to the opponent's strongest creature (ogre)",
          ("magic", "ogre") in st["attached_to"])
    controls = {(p, c) for (p, c) in driver.run(st, ["controls"])["controls"]}
    check("control is flipped to the Aura's controller (alice controls ogre)", ("alice", "ogre") in controls)
    check("the original controller no longer controls it (not bob)", ("bob", "ogre") not in controls)

    # when the stolen creature leaves, the steal ends (eff_gain_control cleared) and the Aura goes to graveyard.
    st["on_battlefield"].discard(("ogre",))
    with contextlib.redirect_stdout(io.StringIO()):
        driver._aura_sba(st)
    check("the control-steal ends when the host leaves (eff_gain_control cleared)",
          not st.get("eff_gain_control"))
    check("the control Aura goes to the graveyard (§704.5n)", ("magic",) in st["graveyard"])


def _bridge_checks() -> None:
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # Holy Strength: 'enchanted creature gets +1/+2' -> static_pt scope attached.
    f, _ = bridge.card_facts("Holy Strength", "alice", "x", db, corpus)
    check("Holy Strength -> static_pt(+1/+2, attached)",
          ("x", 1, 2, "attached") in f.get("static_pt", set()))

    # Bonesplitter (Equipment): 'equipped creature gets +2/+0' -> static_pt scope attached.
    if "Bonesplitter" in corpus:
        f, _ = bridge.card_facts("Bonesplitter", "alice", "x", db, corpus)
        check("Bonesplitter -> static_pt(+2/+0, attached)",
              ("x", 2, 0, "attached") in f.get("static_pt", set()))

    # Control Magic: 'you control enchanted creature' -> aura_control marker.
    if "Control Magic" in corpus:
        f, _ = bridge.card_facts("Control Magic", "alice", "x", db, corpus)
        check("Control Magic -> aura_control", ("x",) in f.get("aura_control", set()))

    # corpus body of attachment buffs + control auras.
    nb = nc = 0
    for name in corpus:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        if any(sc == "attached" for (_s, *_r, sc) in f.get("static_pt", set())) \
           or any(sc == "attached" for (_s, _kw, sc) in f.get("static_grant", set())):
            nb += 1
        if f.get("aura_control"):
            nc += 1
    check("the corpus yields a body of attachment buffs (>= 100)", nb >= 100)
    check("the corpus yields a body of control Auras (>= 20)", nc >= 20)


def _attached_counter_checks() -> None:
    """§301/§303 'put a +1/+1 / -1/-1 counter on enchanted/equipped creature' -> add_counter_attached on the
    HOST (not the Aura/Equip itself, the pre-existing misresolution this fixes). Bridge emit + driver arm +
    the attach-by-sign host pick, plus the non-P/T / variable / non-attachment ABSTAINS."""
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # bridge: a +1/+1 'enchanted creature' upkeep aura -> add_counter_attached + a 'pos' attach marker.
    f, _ = bridge.card_facts("Forced Adaptation", "alice", "fa", db, corpus)
    check("Forced Adaptation -> trigger add_counter_attached(1, p1p1) on the host",
          ("fa_a1", "add_counter_attached", 1, "p1p1") in f.get("trigger_effect", set()))
    check("Forced Adaptation -> attach_counter(pos)", ("fa", "pos") in f.get("attach_counter", set()))

    # bridge: a -1/-1 aura -> a 'neg' marker (so _attach_aura picks an ENEMY host).
    f, _ = bridge.card_facts("Biting Tether", "alice", "bt", db, corpus)
    check("Biting Tether -> trigger add_counter_attached(1, m1m1)",
          ("bt_a2", "add_counter_attached", 1, "m1m1") in f.get("trigger_effect", set()))
    check("Biting Tether -> attach_counter(neg)", ("bt", "neg") in f.get("attach_counter", set()))

    # bridge: an activated 'put a +1/+1 on enchanted creature' (Daily Regimen) -> add_counter_attached.
    f, _ = bridge.card_facts("Daily Regimen", "alice", "dr", db, corpus)
    check("Daily Regimen -> activated add_counter_attached(1, p1p1)",
          any(r[4] == "add_counter_attached" and r[5] == 1 and r[6] == "p1p1"
              for r in f.get("activated_ability", set())))

    # bridge ABSTAINS: a non-symmetric P/T attached counter (+1/+0 — no engine consumer).
    f, _ = bridge.card_facts("Consuming Ferocity", "alice", "cf", db, corpus)
    check("Consuming Ferocity (+1/+0 attached) abstains (no add_counter_attached)",
          not any("add_counter_attached" in str(r) for r in f.get("trigger_effect", set())))

    # driver arm: the counter lands on the HOST, never on the Aura/Equipment instance.
    st = {"attached_to": {("aura1", "bear")}, "counter": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, {("a", "add_counter_attached", 1, "p1p1", "aura1", "alice")})
    check("add_counter_attached lands on the host (bear gets +1/+1)", ("bear", "p1p1", 1) in st["counter"])
    check("add_counter_attached does NOT counter the aura itself",
          not any(o == "aura1" for (o, _k, _c) in st["counter"]))

    # driver arm: no host -> graceful no-op (never fabricates a target).
    st = {"attached_to": set(), "counter": set()}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_effects(st, {("a", "add_counter_attached", 2, "m1m1", "aura2", "alice")})
    check("add_counter_attached with no host is a no-op", st["counter"] == set())

    # _attach_aura: a bare-trigger +1/+1 aura attaches to the controller's strongest creature (bear);
    # a -1/-1 aura attaches to the opponent's strongest (ogre).
    st = _board(); st["printed_subtype"].add(("ca", "aura")); st["on_battlefield"].add(("ca",))
    st["attach_counter"] = {("ca", "pos")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._attach_aura(st, "ca", "alice")
    check("a +1/+1 counter aura attaches to an own creature (bear)", ("ca", "bear") in st["attached_to"])

    st = _board(); st["printed_subtype"].add(("cn", "aura")); st["on_battlefield"].add(("cn",))
    st["attach_counter"] = {("cn", "neg")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._attach_aura(st, "cn", "alice")
    check("a -1/-1 counter aura attaches to the opponent's creature (ogre)", ("cn", "ogre") in st["attached_to"])


def run() -> None:
    _driver_checks()
    _equipment_checks()
    _control_checks()
    _bridge_checks()
    _attached_counter_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
