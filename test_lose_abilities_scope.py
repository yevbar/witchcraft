"""test_lose_abilities_scope.py — §613 layer-6 "loses all abilities", BOARD / FILTERED scopes.

The board-scope companion to test_lose_abilities.py (the self/single-target case). Two engine paths feed
the loses_abilities flag for a board scope (see datalog/engine_rules.dl, the §613 layer-6 BOARD block):

  1. CONTINUOUS STATIC board scope (Humility "all creatures lose all abilities"; "creatures you control
     lose all abilities") — derived ENTIRELY IN THE ENGINE (lose_ab_src -> loses_abilities, like an
     anthem). Self-cleaning, no Python runs.
  2. ONE-SHOT SPELL board scope (Wrath of Oko, You Exist Only to Amuse) — the engine emits a player-scoped
     spell_effect(spell, "lose_abilities_scope", 0, <scope>); the driver routes it to
     effect_handlers/lose_abilities_scope.py which expands the scope and writes the lock.

We read has_keyword (an .output relation) as the OBSERVABLE effect: an affected creature has NO keywords;
an unaffected one keeps them; P/T and creature-ness are UNTOUCHED. Both info modes.
Run: python3 test_lose_abilities_scope.py"""
import driver

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)


def _board():
    # g = alice's 4/4 RED GOBLIN with flying; e = bob's 3/3 with flying + first_strike. Plus a static
    # anthem 'an' ('creatures you control have trample') so g also has a GRANTED keyword (suppressed too).
    return {
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": {("g",), ("e",), ("an",)},
        "printed_type": {("g", "creature"), ("e", "creature"), ("an", "enchantment")},
        "printed_subtype": {("g", "goblin")},
        "printed_color": {("g", "red")},
        "printed_power": {("g", 4), ("e", 3)}, "printed_toughness": {("g", 4), ("e", 3)},
        "printed_keyword": {("g", "flying"), ("e", "flying"), ("e", "first_strike")},
        "printed_control": {("alice", "g"), ("bob", "e"), ("alice", "an")},
        "instance_of": {("an", "ans")},
        "card_ability": {("ans", "a0", "static")},
        "card_effect": {("ans", "a0", 0, "grant_keyword", "trample", "creatures_you_control", "-", "-")},
        "counter": set(), "tapped": set(),
    }


def _kw(s, c):
    o = driver.run(s, ["has_keyword", "power", "eff_toughness", "creature"])
    return ({v for (cc, v) in o["has_keyword"] if cc == c},
            next((int(v) for (cc, v) in o["power"] if cc == c), None),
            next((int(v) for (cc, v) in o["eff_toughness"] if cc == c), None),
            (c,) in o["creature"])


# === CONTROL: no lose-abilities source -> everyone keeps their keywords =================================
s = _board()
kg, pg, tg, cg = _kw(s, "g")
check("control: g has printed flying + GRANTED trample", {"flying", "trample"} <= kg)
ke, _, _, _ = _kw(s, "e")
check("control: e has flying + first_strike", {"flying", "first_strike"} <= ke)


# === STATIC 'creatures you control lose all abilities' (a Humility-style lord alice controls) ===========
# Every creature ALICE controls loses ALL abilities; opponents' creatures are untouched; P/T/type unchanged.
def _static(scope, ctrl="alice"):
    s = _board()
    s["on_battlefield"].add(("lab",))
    s["printed_type"].add(("lab", "enchantment"))
    s["printed_control"].add((ctrl, "lab"))
    s["instance_of"].add(("lab", "labs"))
    s["card_ability"].add(("labs", "a0", "static"))
    s["card_effect"].add(("labs", "a0", 0, "lose_abilities", "-", scope, "-", "-"))
    return s

s = _static("creatures_you_control")
kg, pg, tg, cg = _kw(s, "g")
check("static cyc: g (yours) has ZERO keywords (flying + granted trample gone)", kg == set())
check("static cyc: g P/T UNCHANGED 4/4 (layer 6 strips only abilities)", (pg, tg) == (4, 4))
check("static cyc: g still a creature (type untouched)", cg)
ke, pe, te, ce = _kw(s, "e")
check("static cyc: e (opponent's) KEEPS flying + first_strike", {"flying", "first_strike"} <= ke)
check("static cyc: e P/T unchanged 3/3", (pe, te) == (3, 3))

# STATIC 'all creatures lose all abilities' (Humility) — EVERY creature loses, both sides.
s = _static("all_creatures")
kg, _, _, _ = _kw(s, "g"); ke, _, _, _ = _kw(s, "e")
check("static all_creatures (Humility): g has no keywords", kg == set())
check("static all_creatures (Humility): e has no keywords", ke == set())


# === ONE-SHOT SPELL board scope: the engine emits spell_effect -> the scope applier writes the lock ======
import effect_handlers
effect_handlers.load()
check("APPLY registered for lose_abilities_scope", "lose_abilities_scope" in effect_handlers.APPLY)

# The engine derives spell_effect(spell, 'lose_abilities_scope', 0, scope) for a resolving SPELL.
def _spell(scope):
    s = _board()
    s["instance_of"].add(("w1", "woko"))
    s["card_ability"].add(("woko", "a0", "spell"))
    s["card_effect"].add(("woko", "a0", 0, "lose_abilities", "-", scope, "-", "-"))
    return s

s = _spell("all_creatures")
se = {r for r in driver.run(s, ["spell_effect"])["spell_effect"] if r[1] == "lose_abilities_scope"}
check("spell all_creatures: engine emits spell_effect(w1,lose_abilities_scope,0,all_creatures)",
      ("w1", "lose_abilities_scope", "0", "all_creatures") in se)

# Drive the applier as _run_spell_effects would (a=spell, n=0, tgt=scope, src=spell, ctrl=caster).
s = _board()
effect_handlers.APPLY["lose_abilities_scope"](driver, s, "w1", 0, "all_creatures", "w1", "alice")
kg, pg, tg, cg = _kw(s, "g"); ke, _, _, _ = _kw(s, "e")
check("spell all_creatures APPLY: g loses all keywords", kg == set())
check("spell all_creatures APPLY: e loses all keywords", ke == set())
check("spell all_creatures APPLY: g P/T unchanged 4/4", (pg, tg) == (4, 4) and cg)

# 'creatures your opponents control lose all abilities' cast by alice -> only bob's creatures.
s = _board()
effect_handlers.APPLY["lose_abilities_scope"](driver, s, "y1", 0, "creatures_your_opponents_control", "y1", "alice")
kg, _, _, _ = _kw(s, "g"); ke, _, _, _ = _kw(s, "e")
check("spell opp-control APPLY: g (caster's) KEEPS flying + trample", {"flying", "trample"} <= kg)
check("spell opp-control APPLY: e (opponent's) loses all keywords", ke == set())


# === IMPERFECT-INFORMATION: the suppression holds on the observed (public board) view ==================
import observe
s = _static("creatures_you_control")
s["loses_abilities"] = set()                                 # static derivation is engine-side; nothing to redact
view = observe.observe(s, "bob")                             # bob observes alice's board
kg, pg, tg, cg = _kw(view, "g")
check("imperfect: bob's view also sees alice's g with NO keywords", kg == set())
check("imperfect: bob's view still sees g as a 4/4 creature", (pg, tg) == (4, 4) and cg)
ke, _, _, _ = _kw(view, "e")
check("imperfect: bob's view sees bob's own e KEEP its keywords", {"flying", "first_strike"} <= ke)

# spell one-shot lock also survives observe (public loses_abilities EDB)
s = _board()
effect_handlers.APPLY["lose_abilities_scope"](driver, s, "w1", 0, "all_creatures", "w1", "alice")
view = observe.observe(s, "bob")
check("imperfect: one-shot lock loses_abilities survives observe", ("g",) in view.get("loses_abilities", set()))
kg, _, _, _ = _kw(view, "g")
check("imperfect: g has no keywords on bob's observed view", kg == set())


print(f"\n{_ok[1]}/{_ok[0]} passed")
import sys
sys.exit(0 if _ok[1] == _ok[0] else 1)
