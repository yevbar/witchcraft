"""test_modal.py — §700.2 modal spells through the datalog engine: the controller chooses a mode as the
spell is cast (chose_mode), the engine derives active_mode, and ONLY the chosen mode's effects resolve."""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import driver

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    PASS += cond
    FAIL += not cond


def _state():
    # a modal spell `chrm` on the stack with two modes: mode1 = controller gains 3 life, mode2 = draw 2.
    return {
        "is_player": {("a",), ("b",)}, "active_player": {("a",)},
        "life": {("a", 20), ("b", 20)},
        "on_stack": {("chrm", 0)}, "all_passed": {("yes",)}, "_stack_info": {"chrm": "a"},
        "spell_type": {("chrm", "instant")}, "in_library": {("a", f"a{i}") for i in range(5)},
        "_lib_order": {"a": [f"a{i}" for i in range(5)]},
        "spell_mode": {("chrm", "mode1"), ("chrm", "mode2")},
        "spell_effect_mode": {("chrm", "mode1", "gain_life", 3, "controller"),
                              ("chrm", "mode2", "draw", 2, "controller")},
        "in_hand": set(), "graveyard": set(), "on_battlefield": set(), "tapped": set(), "counter": set(),
    }


def main():
    # the engine offers both modes, but active_mode holds only for the chosen one
    st = _state()
    driver._choose_mode(st, "chrm")
    chosen = {m for (s, m) in st.get("chose_mode", set()) if s == "chrm"}
    check("a mode is chosen at cast (chose_mode set)", chosen == {"mode1"})
    active = {m for (s, m) in driver.run(st, ["active_mode"])["active_mode"] if s == "chrm"}
    check("engine derives active_mode for ONLY the chosen mode", active == {"mode1"})

    hand_before = len(st["in_hand"])
    driver._run_spell_effects(st, "chrm", "a")
    life_a = next(v for (p, v) in st["life"] if p == "a")
    check("only the chosen mode resolves: controller gained 3 life (mode1)", life_a == 23)
    check("the OTHER mode did NOT resolve: no cards drawn (mode2 skipped)", len(st["in_hand"]) == hand_before)

    # an illegal choice (a mode the spell doesn't offer) yields no active_mode -> nothing resolves
    st2 = _state()
    st2["chose_mode"] = {("chrm", "mode_bogus")}
    active2 = {m for (s, m) in driver.run(st2, ["active_mode"])["active_mode"] if s == "chrm"}
    check("a non-offered mode choice is not active (§700.2 — must be an offered mode)", active2 == set())

    _modal_target_checks()
    _modal_damage_checks()
    _modal_count_and_card_checks()

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


def _modal_target_checks():
    """§601.2c a CHOSEN mode's single-target zone move resolves through the target machinery (Prismari
    Charm's bounce-nonland-permanent; Get Out's OWN-restricted protective bounce)."""
    import contextlib
    import io
    # Prismari Charm mode3: bounce a nonland permanent -> aims at the opponent's, never a land.
    st = {
        "is_player": {("a",), ("b",)}, "on_stack": set(), "_stack_info": {},
        "on_battlefield": {("charm",), ("bmox",), ("bland",)},
        "printed_type": {("bmox", "artifact"), ("bland", "land")},
        "printed_control": {("a", "charm"), ("b", "bmox"), ("b", "bland")},
        "spell_mode": {("charm", "m3")}, "chose_mode": {("charm", "m3")},
        "spell_effect_mode": {("charm", "m3", "ctarget", 0, "return_to_hand|-|perm_nonland")},
        "tapped": set(), "graveyard": set(), "in_hand": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "charm", "a")
    check("modal bounce returns the opponent's nonland permanent", ("b", "bmox") in st["in_hand"])
    check("modal bounce can't hit a land (perm_nonland excludes it)", ("bland",) in st["on_battlefield"])

    # Get Out mode2: bounce ONE creature/enchantment YOU OWN -> the controller's own, not the opponent's.
    st2 = {
        "is_player": {("a",), ("b",)}, "on_stack": set(), "_stack_info": {},
        "on_battlefield": {("go",), ("mine",), ("theirs",)},
        "printed_type": {("mine", "creature"), ("theirs", "creature")},
        "printed_control": {("a", "go"), ("a", "mine"), ("b", "theirs")},
        "spell_mode": {("go", "m2")}, "chose_mode": {("go", "m2")},
        "spell_effect_mode": {("go", "m2", "ctarget", 0, "return_to_hand|-|perm_own_creature_enchantment")},
        "tapped": set(), "graveyard": set(), "in_hand": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st2, "go", "a")
    check("modal 'you own' bounce returns the controller's own permanent", ("a", "mine") in st2["in_hand"])
    check("modal 'you own' bounce never touches the opponent's", ("theirs",) in st2["on_battlefield"])


def _modal_damage_checks():
    """§120 a chosen mode's direct damage resolves through the damage machinery (Prismari Charm's
    'deals 1 to each of one or two targets' -> one chosen target, not the caster)."""
    import contextlib
    import io
    st = {
        "is_player": {("a",), ("b",)}, "life": {("a", 20), ("b", 20)},
        "on_stack": set(), "_stack_info": {}, "on_battlefield": {("charm",)},
        "printed_type": set(), "printed_control": {("a", "charm")},
        "spell_mode": {("charm", "m2")}, "chose_mode": {("charm", "m2")},
        "spell_effect_mode": {("charm", "m2", "cdamage", 1, "any_target")},
        "tapped": set(), "graveyard": set(), "in_hand": set(),
    }
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "charm", "a")
    life_a = next(v for (p, v) in st["life"] if p == "a")
    check("modal damage does NOT hit the caster (no self-damage bug)", life_a == 20)


def _modal_count_and_card_checks():
    """The mode-LEAK fix + the mode-COUNT selection + the three effects modal infrastructure unlocked, all
    over REAL cards (card_facts -> engine/driver). 'Choose two' picks two; 'Choose one; both if you control a
    commander' picks one normally and both with a commander; Will of the Jeskai (both modes) and Quiet
    Speculation resolve end-to-end."""
    import contextlib
    import io
    import copy
    import card_corpus
    import sim
    import bridge_to_engine as B
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def facts(name, tid):
        f, dr = B.card_facts(name, "p", tid, db, corpus)
        st = {}
        for k, rows in f.items():
            st.setdefault(k, set()).update(rows)
        st.setdefault("is_player", set()).update({("p",), ("q",)})
        st.setdefault("_stack_info", {})[tid] = "p"
        return st, f, dr

    def chosen(st, tid, commander=False):
        if commander:
            st.setdefault("on_battlefield", set()).add(("cmdr",))
            st.setdefault("printed_control", set()).add(("p", "cmdr"))
            st.setdefault("_commander_owner", set()).add(("p", "cmdr"))
        with contextlib.redirect_stdout(io.StringIO()):
            driver._choose_mode(st, tid)
        return sorted(m for (s, m) in st.get("chose_mode", set()) if s == tid)

    # (1) the mode-LEAK fix: a modal card derives NO flat spell_* (only the mode-gated spell_effect_mode).
    cst, cf, _ = facts("Cryptic Command", "cc")
    flat = driver.run(cst, ["spell_target", "spell_effect", "spell_scope", "spell_damage"])
    check("modal Cryptic Command leaks NO flat spell_* row",
          sum(len([r for r in flat[rel] if r[0] == "cc"]) for rel in flat) == 0)
    # (2) mode-COUNT: 'Choose two' picks two.
    check("'Choose two' (Cryptic Command) picks two modes", len(chosen(copy.deepcopy(cst), "cc")) == 2)

    wst, wf, wdr = facts("Will of the Jeskai", "w")
    check("Will of the Jeskai is CLEAN (both modes interpreted)", wdr == [])
    check("no commander -> chooses ONE mode", len(chosen(copy.deepcopy(wst), "w")) == 1)
    check("controls a commander -> chooses BOTH modes", len(chosen(copy.deepcopy(wst), "w", commander=True)) == 2)

    # (3a) Will of the Jeskai end-to-end (both modes): discard hand + draw five; a GY Bolt gains flashback.
    est, _, _ = facts("Will of the Jeskai", "w")
    bf, _ = B.card_facts("Lightning Bolt", "p", "bolt", db, corpus)
    for k, rows in bf.items():
        est.setdefault(k, set()).update(rows)
    est.setdefault("on_battlefield", set()).add(("cmdr",))
    est.setdefault("printed_control", set()).update({("p", "cmdr"), ("p", "bolt")})
    est.setdefault("_commander_owner", set()).add(("p", "cmdr"))
    est.setdefault("graveyard", set()).add(("bolt",))
    est.setdefault("spell_type", set()).add(("bolt", "instant"))   # the deck loader sets spell_type per card
    est.setdefault("in_hand", set()).update({("p", "h1"), ("p", "h2")})
    est["_lib_order"] = {"p": [f"lib{i}" for i in range(8)]}
    est.setdefault("in_library", set()).update({("p", f"lib{i}") for i in range(8)})
    est.setdefault("life", set()).update({("p", 40), ("q", 40)})
    est["printed_type"] = driver.run(est, ["printed_type"])["printed_type"]
    with contextlib.redirect_stdout(io.StringIO()):
        driver._choose_mode(est, "w")
        driver._run_spell_effects(est, "w", "p")
    check("mode1 discards hand and draws five",
          sorted(c for (pp, c) in est["in_hand"] if pp == "p") == [f"lib{i}" for i in range(5)])
    check("mode2 makes the GY Bolt flashback-castable (may_play + exile-on-resolve)",
          ("p", "bolt") in est.get("may_play", set()) and ("bolt",) in est.get("_flashback", set()))

    # (3b) Quiet Speculation: only the flashback cards move to the graveyard; non-flashback stay.
    qst, qf, qdr = facts("Quiet Speculation", "qs")
    check("Quiet Speculation is CLEAN", qdr == [])
    check("Quiet Speculation = search_to_graveyard 3 keyword:flashback",
          ("qs", "search_to_graveyard", 3, "keyword:flashback") in qf.get("spell_effect", set()))
    for nm, t in [("Faithless Looting", "fl"), ("Deep Analysis", "da"), ("Lightning Bolt", "lb"), ("Counterspell", "cs")]:
        cf2, _ = B.card_facts(nm, "p", t, db, corpus)
        for k, rows in cf2.items():
            qst.setdefault(k, set()).update(rows)
        qst.setdefault("in_library", set()).add(("p", t))
    qst["_lib_order"] = {"p": ["fl", "da", "lb", "cs"]}
    qst.setdefault("life", set()).update({("p", 40), ("q", 40)})
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(qst, "qs", "p")
    check("only flashback cards (fl, da) move to the graveyard",
          sorted(c for (c,) in qst.get("graveyard", set())) == ["da", "fl"])
    check("non-flashback cards (lb, cs) stay in the library",
          sorted(c for (pp, c) in qst.get("in_library", set()) if pp == "p") == ["cs", "lb"])


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
