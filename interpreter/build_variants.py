"""Build datalog/variants.dl — §8 Multiplayer + §9 Casual Variant facts, from rules.txt.

The §8/§9 rules are mostly procedural prose, but four regular shapes are read out by fixed
anchors / the catalogue structure (abstaining when the anchor is absent):

  §80x.1/§90x.1  Each group introduces a multiplayer OPTION or a VARIANT, named by its title
                 and confirmed by the defining rule -> multiplayer_construct(name, kind).
  "The <variant> variant uses the <option> option"  -> variant_uses(variant, option)
                 (the option is validated against the interpreted option roster; messy
                 "uses the following…/combat rules for…" phrasings are abstained).
  "<variant> … teams of <N> players each"           -> variant_teams(variant, n)
  "options used are determined/decided before play begins" -> variant_property(variant, options_set_before_play)
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

import rulescan
from interpreter.dlgen import Program
from interpreter.rules_parser import split

_WORD = {"two": 2, "three": 3, "four": 4, "five": 5}
_USES = re.compile(r"The (.+?) variant (?:always )?uses the ([\w\- ]+?) option", re.I)
_TEAMS = re.compile(r"teams of (\w+) players each", re.I)
_ROI = re.compile(r"range of influence[^.\d]{0,25}(\d+)", re.I)
_OPTION_USED = re.compile(r"[Tt]he ([a-z][\w ]+?) option (is|isn['’]?t)(?: normally| usually)? used")

# (anchor phrase, property) — recurring clean clauses across §8/§9 -> variant_property(variant, property).
_PROPS = [
    ("options used are determined before play begins", "options_set_before_play"),
    ("options used are decided before play begins", "options_set_before_play"),
    ("resources (cards in hand, mana, and so on) are not shared", "resources_not_shared"),
    ("Each team sits together on one side of the table", "team_sits_together"),
    ("randomly seated around the table", "random_seating"),
    ("players are seated at random", "random_seating"),
    ("shared life total", "shared_life_total"),
    ("skips the draw step of its first turn", "first_turn_skips_draw"),
    ("uses color identity to determine", "uses_color_identity"),
    ("do not use sideboards", "no_sideboard"),
]

_DECK = re.compile(r"exactly (\d+) cards, including its commander")

# (anchor, subject, action) — what can't reach outside a range of influence (Limited Range group).
_ROI_RESTRICT = [
    ("Creatures can attack only", "creature", "attack"),
    ("be the targets of spells or abilities", "spell_or_ability", "target"),
    ("activate the activated abilities", "player", "activate_ability"),
    ("enchant an object or player outside", "aura", "enchant"),
    ("equip an object outside", "equipment", "equip"),
    ("affect objects or players outside", "spell_or_ability", "affect"),
]

# (anchor, face, effect) — planar-die roll outcomes (Planechase group); the "If the die roll is …"
# lead is distinctive so "planeswalking ability" (which recurs throughout §901) isn't over-matched.
_DIE_OUTCOME = [
    ("If the die roll is a blank face", "blank", "nothing_happens"),
    ("If the die roll is the chaos symbol", "chaos", "chaos_ensues"),
    ("If the die roll is the Planeswalker symbol", "planeswalker", "planeswalking_ability"),
]

# canonical deck size -> which Commander-family variant requires it (value is content, not a number).
_DECK_VARIANT = {100: "commander", 60: "brawl"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _variant_groups():
    """Yield (group, name_slug, kind) for each §8/§9 option/variant group (skipping General)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    for s in doc.sections:
        if s.number not in ("8", "9"):
            continue
        for g in s.groups:
            if not g.rules or "General" in g.title:
                continue
            kind = "option" if g.title.rstrip("s").endswith("Option") else "variant"
            bare = g.title.replace(" Options", "").replace(" Option", "").replace(" Variant", "")
            yield g, _slug(bare), kind


def constructs() -> list[tuple[str, str, str]]:
    """(rule, name, kind) — the option/variant roster; the group's first rule must name it."""
    rows = []
    for g, name, kind in _variant_groups():
        lead = name.split("_")[0]
        if lead in g.rules[0].text.lower():
            rows.append((g.rules[0].number, name, kind))
    return rows


def variant_uses() -> list[tuple[str, str, str]]:
    """(rule, variant, option) — '<variant> variant uses the <option> option', option validated."""
    options = {name for _n, name, kind in constructs() if kind == "option"}
    rows = []
    for g, _name, _kind in _variant_groups():
        for r in g.rules:
            for sr in [r] + r.subrules:
                m = _USES.search(sr.text)
                if m and _slug(m.group(2)) in options:
                    rows.append((sr.number, _slug(m.group(1)), _slug(m.group(2))))
    return rows


def variant_teams() -> list[tuple[str, str, int]]:
    """(rule, variant, n) — '<variant> … teams of <N> players each'."""
    rows = []
    for g, name, _kind in _variant_groups():
        for r in g.rules:
            for sr in [r] + r.subrules:
                m = _TEAMS.search(sr.text)
                if m and m.group(1).lower() in _WORD:
                    rows.append((sr.number, name, _WORD[m.group(1).lower()]))
    return rows


def variant_properties() -> list[tuple[str, str, str]]:
    """(rule, variant, property) — recurring clean clauses (resources not shared, team sits
    together, random seating, shared life, first-turn draw skip, options set before play)."""
    rows, seen = [], set()
    for g, name, _kind in _variant_groups():
        for r in g.rules:
            for sr in [r] + r.subrules:
                for phrase, prop in _PROPS:
                    if phrase in sr.text and (sr.number, prop) not in seen:
                        seen.add((sr.number, prop))
                        rows.append((sr.number, name, prop))
    return rows


def variant_range_of_influence() -> list[tuple[str, str, int]]:
    """(rule, variant, n) — a variant's range of influence ('range of influence of/is N')."""
    rows = []
    for g, name, _kind in _variant_groups():
        for r in g.rules:
            for sr in [r] + r.subrules:
                m = _ROI.search(sr.text)
                if m:
                    rows.append((sr.number, name, int(m.group(1))))
    return rows


def attack_direction() -> list[tuple[str, str, str]]:
    """(rule, option, direction) — the attack-left / attack-right options (Attack Left/Right group)."""
    return rulescan.find(
        [("attack left option is used", "attack_left", "left"),
         ("attack right option is used", "attack_right", "right")],
        group_title="Attack Left and Attack Right")


def option_used() -> list[tuple[str, str, str, str]]:
    """(rule, variant, option, used) — 'The <option> option is/isn't used' in a variant group,
    option validated against the interpreted option roster (multi-option list clauses abstained)."""
    options = {name for _n, name, kind in constructs() if kind == "option"}
    rows = []
    for g, name, kind in _variant_groups():
        if kind != "variant":
            continue
        for r in g.rules:
            for sr in [r] + r.subrules:
                m = _OPTION_USED.search(sr.text)
                if m and _slug(m.group(1)) in options:
                    used = "yes" if m.group(2).lower() == "is" else "no"
                    rows.append((sr.number, name, _slug(m.group(1)), used))
    return rows


def roi_restriction() -> list[tuple[str, str, str]]:
    """(rule, subject, action) — what can't reach outside a range of influence (Limited Range group)."""
    return rulescan.find(_ROI_RESTRICT, group_title="Limited Range of Influence")


def variant_deck_size() -> list[tuple[str, str, int]]:
    """(rule, variant, n) — 'exactly N cards, including its commander' (Commander group); the variant
    is keyed off the canonical size (100 Commander, 60 Brawl), not the rule number."""
    rows = []
    for g, _name, _kind in _variant_groups():
        if "Commander" not in g.title:
            continue
        for r in g.rules:
            for sr in [r] + r.subrules:
                m = _DECK.search(sr.text)
                if m:
                    n = int(m.group(1))
                    rows.append((sr.number, _DECK_VARIANT.get(n, "commander"), n))
    return rows


def planar_die_faces() -> list[tuple[str, str, int]]:
    """(rule, face, n) — the planar die's faces (six-sided: 1 Planeswalker, 1 chaos, 4 blank),
    attached to whatever rule introduces the die."""
    hits = rulescan.find([("six-sided die",)], group_title="Planechase")
    if not hits:
        return []
    num = hits[0][0]
    return [(num, "planeswalker", 1), (num, "chaos", 1), (num, "blank", 4)]


def planar_die_outcomes() -> list[tuple[str, str, str]]:
    """(rule, face, effect) — what each planar-die roll does (Planechase group)."""
    return rulescan.find(_DIE_OUTCOME, group_title="Planechase")


_PLAYER_COUNT = re.compile(r"may be a (two-player) game or a (multiplayer) game", re.I)
_CZ_FUNCTION = re.compile(r"abilities of a face-up (.+?) in the command zone function from that zone", re.I)
_ROI_EXEMPT = re.compile(r"([\w ]+?) are exempt from the limited range of influence", re.I)


def _object_slug(phrase: str) -> str:
    """Slug a card-object phrase to a singular form: 'plane cards' -> 'plane_card'."""
    return _slug(re.sub(r"cards\b", "card", phrase.strip(), flags=re.I))


def variant_player_count() -> list[tuple[str, str, str]]:
    """(rule, variant, count) — 'A <variant> game may be a two-player game or a multiplayer game',
    one fact per allowed count; the variant comes from the group title."""
    rows = []
    for g, name, _kind in _variant_groups():
        for r in g.rules:
            for sr in [r] + r.subrules:
                m = _PLAYER_COUNT.search(sr.text)
                if m:
                    for grp in (m.group(1), m.group(2)):
                        rows.append((sr.number, name, _slug(grp)))
    return rows


def command_zone_function() -> list[tuple[str, str]]:
    """(rule, object) — 'Any abilities of a face-up <object> in the command zone function from that
    zone', one fact per object (a list like 'plane card or phenomenon card' splits)."""
    rows = []
    for g, _name, _kind in _variant_groups():
        for r in g.rules:
            for sr in [r] + r.subrules:
                m = _CZ_FUNCTION.search(sr.text)
                if m:
                    for obj in re.split(r"\s+or\s+", m.group(1)):
                        rows.append((sr.number, _object_slug(obj)))
    return rows


def roi_exempt() -> list[tuple[str, str]]:
    """(rule, object) — '<objects> are exempt from the limited range of influence', one per object."""
    rows, seen = [], set()
    for g, _name, _kind in _variant_groups():
        for r in g.rules:
            for sr in [r] + r.subrules:
                m = _ROI_EXEMPT.search(sr.text)
                if m:
                    for obj in re.split(r"\s+and\s+", m.group(1)):
                        key = (sr.number, _object_slug(obj))
                        if key not in seen and obj.strip():
                            seen.add(key)
                            rows.append(key)
    return rows


def build() -> tuple[str, dict]:
    cons, uses, teams = constructs(), variant_uses(), variant_teams()
    props, roi, adir, opt = variant_properties(), variant_range_of_influence(), attack_direction(), option_used()
    deck, faces, outcomes = variant_deck_size(), planar_die_faces(), planar_die_outcomes()
    roi_r = roi_restriction()
    pcount, czf, rexempt = variant_player_count(), command_zone_function(), roi_exempt()
    p = Program()
    p.comment("variants.dl — §8 multiplayer + §9 casual variant facts, interpreted from rules.txt.")
    p.comment("multiplayer_construct(name, kind); variant_uses(variant, option); variant_teams(variant, n); "
              "variant_property(variant, property); variant_range_of_influence(variant, n); "
              "attack_direction(option, direction); option_used(variant, option, used). GENERATED.")
    p.blank()
    p.decl("multiplayer_construct", [("name", "symbol"), ("kind", "symbol")])
    p.decl("variant_uses", [("variant", "symbol"), ("option", "symbol")])
    p.decl("variant_teams", [("variant", "symbol"), ("n", "number")])
    p.decl("variant_property", [("variant", "symbol"), ("property", "symbol")])
    p.decl("variant_range_of_influence", [("variant", "symbol"), ("n", "number")])
    p.decl("attack_direction", [("option", "symbol"), ("direction", "symbol")])
    p.decl("option_used", [("variant", "symbol"), ("option", "symbol"), ("used", "symbol")])
    p.decl("variant_deck_size", [("variant", "symbol"), ("n", "number")])
    p.decl("planar_die_face", [("face", "symbol"), ("n", "number")])
    p.decl("planar_die_outcome", [("face", "symbol"), ("effect", "symbol")])
    p.decl("roi_restriction", [("subject", "symbol"), ("action", "symbol")])
    p.decl("variant_player_count", [("variant", "symbol"), ("players", "symbol")])
    p.decl("command_zone_function", [("object", "symbol")])
    p.decl("roi_exempt", [("object", "symbol")])
    p.blank()
    for _n, name, kind in cons:
        p.fact(f'multiplayer_construct("{name}", "{kind}")')
    p.blank()
    for _n, var, o in uses:
        p.fact(f'variant_uses("{var}", "{o}")')
    for _n, var, n in teams:
        p.fact(f'variant_teams("{var}", {n})')
    seen_prop = set()
    for _n, var, prop in props:                          # several rules can state the same property
        if (var, prop) in seen_prop:
            continue
        seen_prop.add((var, prop))
        p.fact(f'variant_property("{var}", "{prop}")')
    for _n, var, n in roi:
        p.fact(f'variant_range_of_influence("{var}", {n})')
    for _n, o, d in adir:
        p.fact(f'attack_direction("{o}", "{d}")')
    for _n, var, o, used in opt:
        p.fact(f'option_used("{var}", "{o}", "{used}")')
    for _n, var, n in deck:
        p.fact(f'variant_deck_size("{var}", {n})')
    for _n, face, n in faces:
        p.fact(f'planar_die_face("{face}", {n})')
    for _n, face, eff in outcomes:
        p.fact(f'planar_die_outcome("{face}", "{eff}")')
    for _n, subj, act in roi_r:
        p.fact(f'roi_restriction("{subj}", "{act}")')
    for _n, var, cnt in pcount:
        p.fact(f'variant_player_count("{var}", "{cnt}")')
    seen_czf = set()
    for _n, obj in czf:                                  # several variant groups state the same frame
        if obj not in seen_czf:
            seen_czf.add(obj)
            p.fact(f'command_zone_function("{obj}")')
    seen_rex = set()
    for _n, obj in rexempt:                              # §801.18 and §901.13 state the same exemption
        if obj not in seen_rex:
            seen_rex.add(obj)
            p.fact(f'roi_exempt("{obj}")')
    p.blank()
    p.output("multiplayer_construct", "variant_uses", "variant_teams", "variant_property")
    p.output("variant_range_of_influence", "attack_direction", "option_used")
    p.output("variant_deck_size", "planar_die_face", "planar_die_outcome", "roi_restriction")
    p.output("variant_player_count", "command_zone_function", "roi_exempt")
    p.blank()
    p.comment("conformance — spot-check the §8/§9 facts the rules state plainly")
    p.conformance(
        [("expect_construct", [("name", "symbol"), ("kind", "symbol")]),
         ("expect_uses", [("variant", "symbol"), ("option", "symbol")]),
         ("expect_teams", [("variant", "symbol"), ("n", "number")]),
         ("expect_property", [("variant", "symbol"), ("property", "symbol")]),
         ("expect_dir", [("option", "symbol"), ("direction", "symbol")])],
        [("construct", "expect_construct(N, K)", "miss", "multiplayer_construct(N, K)"),
         ("uses", "expect_uses(V, O)", "miss", "variant_uses(V, O)"),
         ("teams", "expect_teams(V, N)", "miss", "variant_teams(V, N)", "V", '"-"'),
         ("property", "expect_property(V, P)", "miss", "variant_property(V, P)"),
         ("dir", "expect_dir(O, D)", "miss", "attack_direction(O, D)")],
    )
    for atom in ['expect_construct("commander", "variant")',
                 'expect_construct("shared_team_turns", "option")']:
        p.fact(atom)
    p.fact('expect_uses("two_headed_giant", "shared_team_turns")')
    p.fact('expect_teams("two_headed_giant", 2)')
    p.fact('expect_property("two_headed_giant", "team_sits_together")')
    p.fact('expect_dir("attack_left", "left")')
    return p.text(), {"constructs": len(cons), "uses": len(uses), "teams": len(teams),
                      "props": len(props), "roi": len(roi), "adir": len(adir), "opt": len(opt),
                      "deck": len(deck), "faces": len(faces), "outcomes": len(outcomes),
                      "roi_r": len(roi_r), "pcount": len(pcount), "czf": len(czf), "rexempt": len(rexempt)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/variants.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/variants.dl ({report['constructs']} construct, {report['uses']} uses, "
          f"{report['teams']} teams, {report['props']} property, {report['roi']} roi, "
          f"{report['adir']} attack_direction, {report['opt']} option_used, {report['deck']} deck_size, "
          f"{report['faces']} planar_die_face, {report['outcomes']} planar_die_outcome, "
          f"{report['roi_r']} roi_restriction, {report['pcount']} player_count, "
          f"{report['czf']} command_zone_function, {report['rexempt']} roi_exempt)")


if __name__ == "__main__":
    main()
