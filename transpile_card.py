"""transpile_card.py — interpret a card-oracle ability UNIT into grounded Datalog facts (or abstain).

Card-side analogue of transpile.transpile_rule. Same contract and prime directive: return the first
FAITHFUL fact, or None — never a lossy/over-claimed fact. Every fact must GROUND in a rules-defined
term (see ground.py): a keyword we can't find in the §702 roster is abstained, not invented.

A pattern fn takes (unit, ctx) and returns a CardOut | None. ctx carries the card's typeline/colors
etc. for patterns that need it. Patterns are tried in order; first hit wins.

Slice 1 (this file): keyword abilities — the productive head of the distribution.
  _kw_line   "Flying" / "Flying, vigilance" / "First strike"   -> card_keyword(card, kw)
  _kw_param  "Enchant creature" / "Equip {S}" / "Ward {S}"     -> card_keyword(card, kw)
                                                                + card_keyword_param(card, kw, arg)
Later slices (registered here as they land): mana abilities, activated "cost: effect", triggered
"when/whenever/at …", spell effects (reusing transpile.py's grammar patterns).
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field

import ground
from card_effects import parse_effect, parse_clause, parse_clauses, _TGT, _mana_production

_KW = ground.keyword_abilities()
# longest keyword first, so "cumulative_upkeep" wins over a hypothetical "cumulative" prefix.
_KW_BY_LEN = sorted(_KW, key=lambda k: -k.count("_"))


@dataclass
class CardOut:
    card: str                       # slug of the card name
    facts: list[str]                # ground datalog atoms (without trailing '.')
    pattern: str
    template: str = ""
    meta: dict = field(default_factory=dict)


def _target_slug(s: str) -> str:
    s = s.strip().rstrip(".")
    if s == "~":
        return "self"
    if s == "it":
        return "it"
    return ground.slug(s) or "self"


def _ground_kw(token: str):
    """Map a keyword token to (grounded_keyword, param|None), or None. Handles the families the rules
    abstract: a §702.14 landwalk variant ('Swampwalk' -> landwalk/swamp) and §702's combined
    daybound_and_nightbound (cards print 'Daybound'/'Nightbound' separately)."""
    s = ground.slug(token)
    if s in _KW:
        return (s, None)
    if s.endswith("walk") and "landwalk" in _KW:
        return ("landwalk", s[:-4])
    if s in ("daybound", "nightbound") and "daybound_and_nightbound" in _KW:
        return ("daybound_and_nightbound", s)
    return None


def _kw_line(unit, ctx):
    """The whole unit is one keyword, or a comma-list of keywords, ALL grounded in §702.
    'Flying' / 'Flying, vigilance' / 'First strike' / 'Swampwalk'. If any comma-part isn't a grounded
    keyword (e.g. 'Protection from red, white, and blue'), abstain so _kw_param can handle it."""
    parts = [p.strip() for p in unit.raw.rstrip(".").split(",") if p.strip()]
    if not parts:
        return None
    grounded = [_ground_kw(p) for p in parts]
    if not all(grounded):
        return None
    facts = []
    for kw, param in grounded:
        facts.append(f'card_keyword("{ctx["id"]}", "{kw}")')
        if param:
            facts.append(f'card_keyword_param("{ctx["id"]}", "{kw}", "{param}")')
    return CardOut(ctx["id"], facts, "kw_line")


def _kw_param(unit, ctx):
    """A parametrized keyword ability: '<Keyword> <arg>' where the keyword is grounded in §702.
    'Enchant creature' -> kw=enchant arg=creature; 'Equip {S}' -> kw=equip arg={S};
    'Protection from red' -> kw=protection arg=from_red. Longest grounded keyword prefix wins."""
    body = unit.raw.rstrip(".").strip()
    s = ground.slug(body)
    for kw in _KW_BY_LEN:
        if s == kw:
            return None                                   # bare keyword — _kw_line's job
        if s.startswith(kw + "_"):
            arg = body[len(kw.replace("_", " ")):].strip()
            facts = [f'card_keyword("{ctx["id"]}", "{kw}")']
            if arg:
                facts.append(f'card_keyword_param("{ctx["id"]}", "{kw}", "{ground.slug(arg) or arg}")')
            return CardOut(ctx["id"], facts, "kw_param", meta={"arg": arg})
    return None


def _mana_ability(unit, ctx):
    """An activated mana ability '<cost>: Add <mana>.' (§605.1a — activated, no target, adds mana).
    Cost and produced mana are grounded (symbols via §107.4, colors via §105). Abstains on any
    non-clean production (conditional/variable amounts) rather than guess."""
    if '"' in unit.raw:
        return None                               # a granted/quoted ability ('… have "{T}: Add …"'), not its own
    m = re.match(r"^(?P<cost>[^:]+):\s*Add (?P<what>.+?)\.?\s*$", unit.raw)
    if not m:
        return None
    cost = m.group("cost").strip()
    if "{" not in cost:                           # require a symbol-led cost ({T}, {1}{T}, …) — abstain on prose costs
        return None
    prod = _mana_production(m.group("what"))
    if not prod:
        return None
    cid = ctx["id"]
    facts = [f'card_mana_ability("{cid}", "{cost}")']
    for p in prod:
        facts.append(f'card_adds_mana("{cid}", "{cost}", "{p}")')
    return CardOut(cid, facts, "mana_ability", meta={"cost": cost, "produces": prod})


# ---- effect bodies (shared by spell / activated / triggered) --------------------------------------
_SPLIT_AND = re.compile(r"\s+and\s+|,\s+then\s+|,\s+and\s+", re.I)
_COST_VERB = re.compile(r"^(sacrifice|discard|pay|exile|tap|untap|remove|return|reveal|mill|put)\b", re.I)


# ability-modifier clauses — timing/frequency restrictions (§602.5/§603), not effects. Recognized and
# recorded as ability_modifier facts so they don't block the effect body from parsing.
_MODIFIERS = [
    (re.compile(r"^activate (?:this ability )?only as a sorcery$", re.I), "activate_sorcery_speed"),
    (re.compile(r"^activate (?:this ability )?only once each turn$", re.I), "activate_once_per_turn"),
    (re.compile(r"^activate (?:this ability )?only during your turn$", re.I), "activate_your_turn_only"),
    (re.compile(r"^activate (?:this ability )?only any time you could cast a sorcery$", re.I), "activate_sorcery_speed"),
    (re.compile(r"^this ability triggers only once each turn$", re.I), "triggers_once_per_turn"),
    (re.compile(r"^activate (?:this ability )?only during your upkeep$", re.I), "activate_your_upkeep_only"),
    (re.compile(r"^do this only once each turn$", re.I), "once_per_turn"),
    (re.compile(r"^any player may activate this ability$", re.I), "any_player_may_activate"),
]


def _split_modifiers(text: str):
    """Partition a body's sentences into (effect_text, [modifier_tags]) — pulling out timing/frequency
    restriction clauses so the remaining effect text can parse on its own."""
    keep, tags = [], []
    for sent in re.split(r"(?<=[.])\s+", text.strip()):
        s = sent.strip().rstrip(".")
        if not s:
            continue
        tag = next((t for pat, t in _MODIFIERS if pat.match(s)), None)
        (tags.append(tag) if tag else keep.append(sent))
    return " ".join(keep).strip(), tags


def _parse_body(text: str):
    """A clause body -> list[Effect], requiring EVERY sub-effect to parse (else None — no half facts).
    Splits on sentence boundaries and simple 'and'/'then' conjunctions; abstains on anything else."""
    out = []
    for sentence in re.split(r"(?<=[.])\s+", text.strip()):
        sentence = sentence.strip().rstrip(".")
        if not sentence:
            continue
        multi = parse_clauses(sentence)
        if multi:
            out.extend(multi)
            continue
        parts = _SPLIT_AND.split(sentence)
        if len(parts) < 2:
            return None
        for p in parts:
            e = parse_clause(p)
            if not e:
                return None
            out.append(e)
    return out or None


def _effect_facts(cid, aid, effects):
    return [f'effect("{cid}", "{aid}", {i}, "{e.verb}", "{e.amount}", "{e.target}", "{e.extra}", "{e.cond}")'
            for i, e in enumerate(effects)]


def _cost_ok(cost: str) -> bool:
    if '"' in cost or len(cost) > 60 or ":" in cost:
        return False
    for part in cost.split(","):
        part = part.strip()
        if not part:
            continue
        if re.fullmatch(r"(\{[^}]+\}|[+\-]?\d+|[TQ]|\s)+", part) or _COST_VERB.match(part):
            continue
        return False
    return True


def _types(ctx) -> set:
    return set((ctx.get("card") or {}).get("types") or [])


def _spell(unit, ctx):
    """An instant/sorcery one-shot: the whole line is effect(s) with no cost/trigger prefix."""
    if not ({"Instant", "Sorcery"} & _types(ctx)):
        return None
    effects = _parse_body(unit.raw)
    if not effects:
        return None
    aid = f"a{ctx.get('seq', 0)}"
    return CardOut(ctx["id"], [f'ability("{ctx["id"]}", "{aid}", "spell")']
                   + _effect_facts(ctx["id"], aid, effects), "spell")


def _static_control(unit, ctx):
    """'You control enchanted <perm>.' — an Aura's static control-change (§613 layer 2 / §720)."""
    m = re.match(r"^You (?:gain )?control (enchanted \w+)\.?$", unit.raw, re.I)
    if not m:
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    return CardOut(cid, [f'ability("{cid}", "{aid}", "static")',
                         f'effect("{cid}", "{aid}", 0, "gain_control", "-", "{ground.slug(m.group(1))}", "-", "-")'],
                   "static_control")


def _activated(unit, ctx):
    """'<cost>: <effect(s)>' — an activated ability (§602). Cost must look like a cost; effects parse."""
    m = re.match(r"^(?P<cost>[^:]{1,60}):\s*(?P<body>.+)$", unit.raw)
    if not m or not _cost_ok(m.group("cost")):
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    cost_facts = [f'ability("{cid}", "{aid}", "activated")',
                  f'ability_cost("{cid}", "{aid}", "{m.group("cost").strip()}")']
    mh = _MODAL_HEAD.match(m.group("body"))
    if mh:
        return CardOut(cid, cost_facts + [f'card_modal("{cid}", "{ground.slug(mh.group(1))}")'], "activated")
    body, mods = _split_modifiers(m.group("body"))
    effects = _parse_body(body) if body else None
    if not effects:
        return None
    cost_facts += [f'ability_modifier("{cid}", "{aid}", "{t}")' for t in mods]
    return CardOut(cid, cost_facts + _effect_facts(cid, aid, effects), "activated")


_TRIG = re.compile(r"^(?:When|Whenever|At) (?P<trig>.+?), (?P<body>.+)$", re.I)


_MODAL_HEAD = re.compile(r"^choose (one or both|one or more|up to one|up to two|up to three|one|two|three)\b", re.I)


def _triggered(unit, ctx):
    """'When/Whenever/At <event>, <effect(s)>' — a triggered ability (§603). A modal body
    ('…, choose one —') records the trigger + modal marker; the modes follow as • options."""
    m = _TRIG.match(unit.raw)
    if not m:
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    trig = ground.slug(m.group("trig"))
    head = [f'ability("{cid}", "{aid}", "triggered")', f'ability_trigger("{cid}", "{aid}", "{trig}")']
    mh = _MODAL_HEAD.match(m.group("body"))
    if mh:
        return CardOut(cid, head + [f'card_modal("{cid}", "{ground.slug(mh.group(1))}")'], "triggered")
    body, mods = _split_modifiers(m.group("body"))
    effects = _parse_body(body) if body else None
    if not effects:
        return None
    head += [f'ability_modifier("{cid}", "{aid}", "{t}")' for t in mods]
    return CardOut(cid, head + _effect_facts(cid, aid, effects), "triggered")


_LOYALTY = re.compile(r"^\[([+\-−]?\d+)\]:\s*(?P<body>.+)$")


def _loyalty(unit, ctx):
    """A planeswalker loyalty ability '[+N]: <effect>' / '[−N]: <effect>' (§606) — an activated ability
    whose cost is a loyalty change."""
    m = _LOYALTY.match(unit.raw)
    if not m:
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    cost = m.group(1).replace("−", "-")
    body, mods = _split_modifiers(m.group("body"))
    effects = _parse_body(body) if body else None
    if not effects:
        return None
    facts = [f'ability("{cid}", "{aid}", "loyalty")', f'ability_cost("{cid}", "{aid}", "{cost}")']
    facts += [f'ability_modifier("{cid}", "{aid}", "{t}")' for t in mods]
    return CardOut(cid, facts + _effect_facts(cid, aid, effects), "loyalty")


_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7}
_SAGA = re.compile(r"^(?P<ch>[IVX]+(?:, [IVX]+)*) — (?P<body>.+)$")


def _saga_chapter(unit, ctx):
    """A Saga chapter ability 'I — <effect>' / 'I, II — <effect>' (§714) — a triggered ability that
    fires when the chapter's lore counter is reached. Gated on the Saga subtype."""
    if "Saga" not in ((ctx.get("card") or {}).get("subtypes") or []):
        return None
    m = _SAGA.match(unit.raw)
    if not m:
        return None
    effects = _parse_body(m.group("body"))
    if not effects:
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    facts = [f'ability("{cid}", "{aid}", "saga_chapter")']
    for ch in m.group("ch").split(", "):
        if ch in _ROMAN:
            facts.append(f'ability_trigger("{cid}", "{aid}", "chapter_{_ROMAN[ch]}")')
    return CardOut(cid, facts + _effect_facts(cid, aid, effects), "saga_chapter")


def _etb_choose(unit, ctx):
    """'As ~ enters, choose a <X>.' — an as-enters choice replacement (§614.12/§603.6e)."""
    m = re.match(r"^As ~ enters, choose (?:a|an) (.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_etb_choose("{cid}", "{ground.slug(m.group(1))}")'], "etb_choose")


# static player-rule modifications, each grounded: hand size §402.2, extra land §505.5b/§116.2a,
# top-card play §601/§715, no max hand size §402.2.
_STATIC_PLAYER = [
    (r"^You have no maximum hand size\.?$", "no_maximum_hand_size"),
    (r"^You may play an additional land on each of your turns\.?$", "extra_land_per_turn"),
    (r"^You may play (?:an? )?additional lands? on each of your turns\.?$", "extra_land_per_turn"),
    (r"^You may look at the top card of your library any time\.?$", "look_at_top_card"),
]


def _static_player(unit, ctx):
    for pat, tag in _STATIC_PLAYER:
        if re.match(pat, unit.raw, re.I):
            return CardOut(ctx["id"], [f'card_static_player("{ctx["id"]}", "{tag}")'], "static_player")
    return None


_STATIC_PT = re.compile(rf"^(?P<who>{_TGT}) gets? (?P<pt>[+-]\d+/[+-]\d+)"
                        rf"(?: and (?:has|gains?) (?P<kw>[\w, ]+?))?\.?$", re.I)


def _static_pt(unit, ctx):
    """A static P/T grant with no duration — '<subject> get(s) +N/+N[ and has <keywords>].' (§613:
    layer 7c P/T, layer 6 ability-adding). The absent 'until end of turn' is what makes it static;
    one-shot 'until end of turn' pumps go to _spell/_activated via the effect engine instead."""
    m = _STATIC_PT.match(unit.raw)
    if not m:
        return None
    who = _target_slug(m.group("who"))
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    facts = [f'ability("{cid}", "{aid}", "static")',
             f'effect("{cid}", "{aid}", 0, "modify_pt", "{m.group("pt")}", "{who}", "-", "-")']
    if m.group("kw"):
        grounded = [_ground_kw(k.strip()) for k in re.split(r",| and ", m.group("kw")) if k.strip()]
        if not all(grounded):
            return None                       # abstain rather than emit a partial grant
        for i, (kw, _param) in enumerate(grounded, 1):
            facts.append(f'effect("{cid}", "{aid}", {i}, "grant_keyword", "{kw}", "{who}", "-", "-")')
    return CardOut(cid, facts, "static_pt")


def _etb_tapped(unit, ctx):
    """'~ enters tapped.' — an ETB replacement (§614) that the permanent enters tapped."""
    if not re.match(r"^~ enters tapped\.?$", unit.raw):
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_enters_tapped("{cid}")'], "etb_tapped")


def _modal(unit, ctx):
    """'Choose one —' / 'Choose one or both —' — a modal spell/ability header (§700.2)."""
    m = re.match(r"^Choose (one or both|up to one|up to two|up to three|one|two|three)\s*[—–-]?\s*$",
                 unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_modal("{cid}", "{ground.slug(m.group(1))}")'], "modal")


def _mode_option(unit, ctx):
    """'• <effect>' — one mode of a modal spell/ability; its body is a normal effect clause."""
    m = re.match(r"^[•·∙]\s*(?P<body>.+)$", unit.raw)
    if not m:
        return None
    effects = _parse_body(m.group("body"))
    if not effects:
        return None
    cid, aid = ctx["id"], f"mode{ctx.get('seq', 0)}"
    return CardOut(cid, [f'mode_option("{cid}", "{aid}")'] + _effect_facts(cid, aid, effects), "mode_option")


# grounded static restrictions: block/attack §508–509, be blocked §509, be countered §701/§601.
_CANT = {"block": "block", "be blocked": "be_blocked", "attack": "attack",
         "attack or block": "attack_or_block", "be countered": "be_countered",
         "be regenerated": "be_regenerated", "be sacrificed": "be_sacrificed",
         "attack you": "attack_you", "attack you or planeswalkers you control": "attack_you_or_pws"}
_CANT_SUBJ = re.compile(r"^(~|enchanted creature|equipped creature) can't (.+?)\.?$", re.I)


def _cant(unit, ctx):
    """'<subject> can't <X>.' — a static restriction grounded in combat/§701 rules (fixed action set).
    Subject is the card itself (~) or the attached creature (enchanted/equipped)."""
    m = _CANT_SUBJ.match(unit.raw)
    if not m or m.group(2).lower() not in _CANT:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_cant("{cid}", "{_target_slug(m.group(1))}", "{_CANT[m.group(2).lower()]}")'],
                   "cant")


def _static_grant(unit, ctx):
    """A static keyword grant with no P/T — '<subject> has/have <keywords>.' (§613 layer 6): an Aura's
    'Enchanted creature has flying.', an anthem's 'Other creatures you control have trample.'"""
    m = re.match(rf"^(?P<who>{_TGT}) (?:has|have) (?P<kw>[\w, ]+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    grounded = [_ground_kw(k.strip()) for k in re.split(r",| and ", m.group("kw")) if k.strip()]
    if not grounded or not all(grounded):
        return None
    who = _target_slug(m.group("who"))
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    facts = [f'ability("{cid}", "{aid}", "static")']
    facts += [f'effect("{cid}", "{aid}", {i}, "grant_keyword", "{kw}", "{who}", "-", "-")'
              for i, (kw, _p) in enumerate(grounded)]
    return CardOut(cid, facts, "static_grant")


def _additional_cost(unit, ctx):
    """'As an additional cost to cast ~, <cost>.' (§601.2b/§118) — record the extra casting cost."""
    m = re.match(r"^As an additional cost to cast ~, (.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_additional_cost("{cid}", "{ground.slug(m.group(1))}")'], "additional_cost")


def _enters_with_counters(unit, ctx):
    """'~ enters with N +N/+N counters on it.' — an ETB counter replacement (§122/§614)."""
    m = re.match(r"^~ enters with (\w+) ([+\-]\d+/[+\-]\d+|\w[\w ]*?) counters? on it\.?$", unit.raw)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_enters_with_counters("{cid}", "{ground.slug(m.group(2))}", "{ground.slug(m.group(1))}")'],
                   "enters_with_counters")


def _doesnt_untap(unit, ctx):
    """'~ / Enchanted creature doesn't untap during …untap step.' — an untap restriction (§502)."""
    m = re.match(r"^(~|Enchanted creature|Equipped creature) doesn't untap during "
                 r"(?:its controller's|your|their) untap step\.?$", unit.raw)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_doesnt_untap("{cid}", "{ground.slug(m.group(1)) or "self"}")'], "doesnt_untap")


def _attacks_each_combat(unit, ctx):
    """'~ attacks each combat if able.' — a combat requirement (§508)."""
    if not re.match(r"^~ attacks each combat if able\.?$", unit.raw):
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_attacks_each_combat("{cid}")'], "attacks_each_combat")


_PATTERNS = [_kw_line, _kw_param, _etb_tapped, _enters_with_counters, _doesnt_untap,
             _attacks_each_combat, _etb_choose, _static_player, _additional_cost, _static_pt,
             _static_grant, _modal, _mode_option, _cant, _loyalty, _saga_chapter, _mana_ability,
             _triggered, _activated, _spell, _static_control]

# an ability-word prefix is flavor (§207.2c, no rules meaning) — strip 'Heroic —', 'Landfall —',
# 'Bio-plasmic Barrage —' so the triggered ability that follows reaches its pattern. Restricted to a
# prefix FOLLOWED BY a trigger word (When/Whenever/At): that's the safe signal for a real ability word
# and avoids the keyword-cost em-dash syntax ('Cumulative upkeep — Pay {1}', 'Buyback — {cost}'), Saga
# chapters, die tables, loyalty, and the modal header. A grounded keyword prefix is never stripped.
_ABILITY_WORD = re.compile(r"^(?P<word>[A-Z][a-z][\w'’-]*(?: [A-Z]?[\w'’-]+){0,3})\s+—\s+"
                           r"(?P<rest>(?:When|Whenever|At) .+)$")


def _strip_ability_word(raw: str) -> str:
    m = _ABILITY_WORD.match(raw)
    if m and _ground_kw(m.group("word")) is None:
        return m.group("rest")
    return raw


def transpile_unit(unit, ctx) -> "CardOut | None":
    """Interpret one ability unit; first faithful pattern wins, else None (abstain)."""
    stripped = _strip_ability_word(unit.raw)
    u = unit if stripped == unit.raw else dataclasses.replace(unit, raw=stripped)
    for fn in _PATTERNS:
        out = fn(u, ctx)
        if out:
            out.template = unit.template
            return out
    return None


if __name__ == "__main__":
    import card_corpus
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for name in ("Serra Angel", "Birds of Paradise", "Snapcaster Mage", "Sram's Expertise"):
        c = cards.get(name)
        if not c:
            continue
        ctx = {"id": ground.slug(name)}
        print(f"== {name}")
        for u in card_corpus.units_of(c):
            o = transpile_unit(u, ctx)
            print(f"  {u.raw[:46]:46} -> {o.facts if o else None}")
