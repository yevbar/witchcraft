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
from card_effects import parse_effect, parse_clause, parse_clauses, _TGT, _mana_production, _is_compound_object

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
    if s.endswith("cycling") and s != "cycling" and "cycling" in _KW:   # §702.29 typecycling variants
        return ("cycling", s[:-len("cycling")].rstrip("_") or "land")
    if s == "megamorph" and "morph" in _KW:                            # §702.37b — a variant of morph
        return ("morph", "mega")
    if s == "multikicker" and "kicker" in _KW:                         # §702.33 — a variant of kicker
        return ("kicker", "multi")
    if s in ("daybound", "nightbound") and "daybound_and_nightbound" in _KW:
        return ("daybound_and_nightbound", s)
    # a §702 keyword that carries a symbol/cost parameter inline: 'ward {2}', 'ward {1}{W}' ->
    # (ward, '2' / '1_w'). The keyword stem must itself be grounded.
    mm = re.match(r"^([a-z][a-z' -]*?)\s*((?:\{[^}]+\})+)$", token.strip(), re.I)
    if mm and ground.slug(mm.group(1)) in _KW:
        return (ground.slug(mm.group(1)), ground.slug(mm.group(2)))
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


def _typecycling(unit, ctx):
    """'<Type>cycling <cost>' (§702.29) — a typecycling variant of cycling (§702.28). 'Plainscycling {2}',
    'Basic landcycling {1}{G}', 'Landcycling {2}'. Grounds in cycling with the type as a parameter."""
    m = re.match(r"^([\w ]+?cycling)(?: (\{[^}]+\}|.+?))?\.?$", unit.raw, re.I)
    if not m:
        return None
    g = _ground_kw(m.group(1))
    if g is None or g[0] != "cycling" or g[1] is None:
        return None
    cid = ctx["id"]
    facts = [f'card_keyword("{cid}", "cycling")', f'card_keyword_param("{cid}", "cycling", "{g[1]}")']
    if m.group(2):
        facts.append(f'card_keyword_param("{cid}", "cycling", "cost_{ground.slug(m.group(2))}")')
    return CardOut(cid, facts, "typecycling")


def _kw_param(unit, ctx):
    """A parametrized keyword ability: '<Keyword> <arg>' where the keyword is grounded in §702.
    'Enchant creature' -> kw=enchant arg=creature; 'Equip {S}' -> kw=equip arg={S};
    'Protection from red' -> kw=protection arg=from_red. Longest grounded keyword prefix wins."""
    body = unit.raw.rstrip(".").strip()
    # a first-word VARIANT keyword (megamorph -> morph/mega, Plainscycling handled by _typecycling):
    first = body.split()[0] if body.split() else ""
    gv = _ground_kw(first)
    if gv and gv[1] and " " in body:
        kw, param = gv
        arg = body[len(first):].strip()
        facts = [f'card_keyword("{ctx["id"]}", "{kw}")', f'card_keyword_param("{ctx["id"]}", "{kw}", "{param}")']
        if arg:
            facts.append(f'card_keyword_param("{ctx["id"]}", "{kw}", "{ground.slug(arg)}")')
        return CardOut(ctx["id"], facts, "kw_param")
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


_PROTO = re.compile(r"^Prototype ((?:\{[^}]+\})+) — (\d+/\d+)$", re.I)


def _prototype(unit, ctx):
    """'Prototype <cost> — P/T' (§702.160) — a grounded keyword giving an alternative cost + size.
    Recorded as the keyword plus its cost and printed P/T parameters."""
    m = _PROTO.match(unit.raw)
    if not m or "prototype" not in ground.keyword_abilities():
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_keyword("{cid}", "prototype")',
                         f'card_keyword_param("{cid}", "prototype", "cost_{ground.slug(m.group(1))}")',
                         f'card_keyword_param("{cid}", "prototype", "pt_{m.group(2)}")'], "prototype")


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
_SPLIT_AND = re.compile(r"\s+and\s+|,\s+then\s+|,\s+and\s+|,\s+(?=put\s)|,\s+(?=reveal\s)|\.\s+(?=then\s)", re.I)

from card_effects import _PREDICATE_LEADS

_STRONG_SPLIT = re.compile(r",?\s+then\s+|\.\s+", re.I)        # sequence/sentence boundaries: always split
_AND_SPLIT = re.compile(r"\s*,\s+and\s+|\s+and\s+|,\s+(?=put |reveal |draw )", re.I)


def _is_predicate(part: str) -> bool:
    """True if a fragment opens a NEW effect (a player/pronoun subject or a grounded verb) rather than
    continuing a noun phrase ('hand and graveyard', 'flying and trample' do NOT)."""
    w = (part.split() or [""])[0].lower().rstrip("s")
    return w in _PREDICATE_LEADS or w in ground.effect_verbs() or (w + "s") in ground.effect_verbs()


def _smart_split(s: str):
    """Split a clause into effect-parts, breaking on ', then'/'. ' always but on ' and '/' , and ' ONLY
    when the right side is itself an effect — so noun conjunctions ('hand and graveyard', 'hexproof and
    trample', 'artifacts and enchantments') stay intact while effect conjunctions ('draw a card and you
    gain 2 life', 'target creature gets +1/+1 and target creature gets -1/-1') split. The right side
    counts as an effect if it's a bare predicate continuation OR it parses on its own."""
    parts = []
    for chunk in _STRONG_SPLIT.split(s):
        if not chunk or not chunk.strip():
            continue
        pieces = _AND_SPLIT.split(chunk)
        merged = [pieces[0]]
        for nxt in pieces[1:]:
            if nxt and (_is_predicate(nxt) or parse_clause(nxt) is not None):
                merged.append(nxt)
            else:                                      # noun conjunction — rejoin with ' and '
                merged[-1] = merged[-1] + " and " + nxt
        parts += merged
    return [p for p in parts if p and p.strip()]
_COST_VERB = re.compile(r"^(sacrifice|discard|pay|exile|tap|untap|remove|return|reveal|mill|put|exert|"
                        r"waterbend|earthbend|airbend|collect)\b", re.I)


# ability-modifier clauses — timing/frequency restrictions (§602.5/§603), not effects. Recognized and
# recorded as ability_modifier facts so they don't block the effect body from parsing.
_MODIFIERS = [
    (re.compile(r"^activate (?:this ability )?only as a sorcery$", re.I), "activate_sorcery_speed"),
    (re.compile(r"^activate (?:this ability )?only once each turn$", re.I), "activate_once_per_turn"),
    (re.compile(r"^activate (?:this ability )?only once$", re.I), "activate_only_once"),
    (re.compile(r"^activate (?:this ability )?only during your turn$", re.I), "activate_your_turn_only"),
    (re.compile(r"^activate (?:this ability )?only any time you could cast a sorcery$", re.I), "activate_sorcery_speed"),
    (re.compile(r"^this ability triggers only once each turn$", re.I), "triggers_once_per_turn"),
    (re.compile(r"^activate (?:this ability )?only during your upkeep$", re.I), "activate_your_upkeep_only"),
    (re.compile(r"^do this only once each turn$", re.I), "once_per_turn"),
    (re.compile(r"^any player may activate this ability$", re.I), "any_player_may_activate"),
    (re.compile(r"^activate (?:this ability )?only (?:during your turn, )?before attackers are declared$", re.I),
     "activate_before_attackers"),
]


# quote-safe splitting — a quoted granted ability ("When …, draw a card.") contains sentence/clause
# punctuation that must NOT trigger the splitters; mask "…" to a sentinel, split, then restore.
_QUOTE = re.compile(r'"[^"]*"')


def _mask_q(text: str):
    q = []

    def r(m):
        q.append(m.group(0))
        return f" \x01{len(q) - 1}\x01 "
    return _QUOTE.sub(r, text), q


def _unmask(s: str, q):
    s = re.sub(r"\x01(\d+)\x01", lambda m: q[int(m.group(1))], s)
    return re.sub(r"\s{2,}", " ", s).strip()         # collapse the spaces the sentinel padding left


def _sentences(text: str):
    """Sentence-split that never cuts inside a quoted ability."""
    masked, q = _mask_q(text)
    return [_unmask(s, q) for s in re.split(r"(?<=[.])\s+", masked.strip()) if s.strip()]


_ACTIVATE_RESTR = re.compile(r"^activate (?:this ability )?(only .+|no more than .+)$", re.I)
_SPEND_RESTR = re.compile(r"^spend this mana only (.+)$", re.I)
_TRIG_ONCE = re.compile(r"^this ability triggers only once$", re.I)


def _split_modifiers(text: str):
    """Partition a body's sentences into (effect_text, [modifier_tags]) — pulling out timing/frequency
    restriction clauses so the remaining effect text can parse on its own. A general 'Activate … only …'
    / 'Activate … no more than …' is captured with its condition as a slug (§602.5)."""
    keep, tags = [], []
    for s in _sentences(text):
        s = s.rstrip(".")
        if not s:
            continue
        tag = next((t for pat, t in _MODIFIERS if pat.match(s)), None)
        if tag is None and (m := _ACTIVATE_RESTR.match(s)):
            tag = "activate_" + ground.slug(m.group(1))
        if tag is None and (m := _SPEND_RESTR.match(s)):
            tag = "spend_only_" + ground.slug(m.group(1))
        if tag is None and _TRIG_ONCE.match(s):
            tag = "triggers_once"
        (tags.append(tag) if tag else keep.append(s))
    return ". ".join(keep).strip(), tags        # rejoin with periods so _parse_body can re-split


_SUBJ_RE = re.compile(rf"^({_TGT}) ", re.I)
# 3rd-person predicate verbs that, with no subject, indicate a shared-subject continuation ('… , then
# draws a card', '…, then exiles the rest').
_BARE_PRED = re.compile(r"^(?:draws?|discards?|gains?|loses?|mills?|exiles?|shuffles?|sacrifices?|"
                        r"creates?|puts?|returns?|reveals?|searches?|taps?|untaps?|adds?)\b", re.I)


def _leading_subject(part: str):
    """The player/permanent subject NP a clause opens with ('Each player', 'Target opponent'), or None."""
    m = _SUBJ_RE.match(part)
    return m.group(1) if m else None


def _has_leading_subject(part: str) -> bool:
    """True if the part already starts with its own subject (so it doesn't need one reattached) — i.e.
    it does NOT start with a bare 3rd-person predicate verb."""
    return not _BARE_PRED.match(part.strip())


_WRAPPER = re.compile(r"^(?P<w>if you do|you may|if (?!you do\b)[^,]+?),?\s+(?P<rest>.+)$", re.I)


def _peel_wrapper(sentence):
    """A leading clause-wrapper over a COMPOUND consequent ('If you do, draw a card and you gain 2
    life'; 'You may exile X and draw Y') -> (cond, rest). Single-consequent wrappers are handled in
    parse_clause; this catches the compound case the body splitter must expand. Returns None if no
    wrapper or the consequent isn't actually compound."""
    m = _WRAPPER.match(sentence)
    if not m or len(_smart_split(m.group("rest"))) < 2:
        return None
    w = m.group("w").lower()
    cond = "if_you_did" if w == "if you do" else ("may" if w == "you may" else ground.slug(w[3:]))
    return cond, m.group("rest")


def _parse_body(text: str):
    """A clause body -> list[Effect], requiring EVERY sub-effect to parse (else None — no half facts).
    Splits on sentence boundaries and simple 'and'/'then' conjunctions (quote-safe); else abstains."""
    out = []
    for sentence in _sentences(text):
        sentence = sentence.rstrip(".")
        if not sentence:
            continue
        peeled = _peel_wrapper(sentence)             # 'If you do, <compound>' / 'You may <compound>'
        if peeled:
            cond, rest = peeled
            sub = _parse_body(rest)
            if sub:
                out.extend(dataclasses.replace(e, cond=(cond if e.cond == "-" else f"{cond}__{e.cond}"))
                           for e in sub)
                continue
        multi = parse_clauses(sentence)
        # Prefer a whole-clause parse UNLESS the sentence runs on into a second effect ('… and gain
        # control of it', '… then exile it'): a single-effect whole-parse there has swallowed the
        # continuation into its target, so try the split first and only fall back if the split fails.
        if multi and (len(multi) > 1 or not _is_compound_object(sentence)):
            out.extend(multi)
            continue
        masked, q = _mask_q(sentence)
        # protect intra-phrase ' and ' that is NOT a conjunction of effects ('base power and toughness',
        # 'power and toughness') so the splitter doesn't tear the phrase apart.
        masked = re.sub(r"power and toughness", "power\x00and\x00toughness", masked, flags=re.I)
        parts = [_unmask(p.replace("\x00", " "), q) for p in _smart_split(masked)]
        if len(parts) >= 2:
            subj = _leading_subject(parts[0])          # for 'X A, then B' the later predicates share X
            sub, ok = [], True
            for j, p in enumerate(parts):
                e = None
                if j > 0 and subj and not _has_leading_subject(p):
                    e = parse_clause(f"{subj} {p}")    # reattach the shared subject ('then draws …')
                if not e:
                    e = parse_clause(p)
                if not e:
                    ok = False
                    break
                sub.append(e)
            if ok:
                out.extend(sub)
                continue
        if multi:                       # split didn't fully parse — fall back to the whole-clause parse
            out.extend(multi)
            continue
        return None
    return out or None


def _effect_facts(cid, aid, effects):
    return [f'card_effect("{cid}", "{aid}", {i}, "{e.verb}", "{e.amount}", "{e.target}", "{e.extra}", "{e.cond}")'
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
    return CardOut(ctx["id"], [f'card_ability("{ctx["id"]}", "{aid}", "spell")']
                   + _effect_facts(ctx["id"], aid, effects), "spell")


def _static_effect(unit, ctx):
    """LAST-RESORT: a bare effect line on a permanent (no cost/trigger/keyword) that nonetheless parses
    fully into grounded effects — e.g. 'Skip your draw step.' This is the static analogue of _spell;
    it runs only after every specific pattern has declined, and _parse_body's all-or-nothing grounding
    keeps it faithful (a single ungrounded clause => abstain)."""
    if {"Instant", "Sorcery"} & _types(ctx):
        return None                               # one-shots are _spell's job
    if re.match(r"^(?:When|Whenever|At|If)\b", unit.raw, re.I) or ":" in unit.raw or '"' in unit.raw:
        return None                               # triggered/activated/quoted/conditional — not a bare static
    # replacement effects ('… would …, … instead') and die/level table rows ('1—9 | …') parse only
    # lossily through the one-shot engine — abstain rather than emit a mangled slug (prime directive).
    if re.search(r"\bwould\b|\binstead\b|—|\|", unit.raw):
        return None
    effects = _parse_body(unit.raw)
    if not effects:
        return None
    aid = f"a{ctx.get('seq', 0)}"
    return CardOut(ctx["id"], [f'card_ability("{ctx["id"]}", "{aid}", "static")']
                   + _effect_facts(ctx["id"], aid, effects), "static_effect")


def _static_control(unit, ctx):
    """'You control enchanted <perm>.' — an Aura's static control-change (§613 layer 2 / §720)."""
    m = re.match(r"^You (?:gain )?control (enchanted \w+)\.?$", unit.raw, re.I)
    if not m:
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    return CardOut(cid, [f'card_ability("{cid}", "{aid}", "static")',
                         f'card_effect("{cid}", "{aid}", 0, "gain_control", "-", "{ground.slug(m.group(1))}", "-", "-")'],
                   "static_control")


def _activated(unit, ctx):
    """'<cost>: <effect(s)>' — an activated ability (§602). Cost must look like a cost; effects parse."""
    m = re.match(r"^(?P<cost>[^:]{1,60}):\s*(?P<body>.+)$", unit.raw)
    if not m or not _cost_ok(m.group("cost")):
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    cost_facts = [f'card_ability("{cid}", "{aid}", "activated")',
                  f'card_ability_cost("{cid}", "{aid}", "{m.group("cost").strip()}")']
    mh = _MODAL_HEAD.match(m.group("body"))
    if mh:
        return CardOut(cid, cost_facts + [f'card_modal("{cid}", "{ground.slug(mh.group(1))}")'], "activated")
    body, mods = _split_modifiers(m.group("body"))
    effects = _parse_body(body) if body else None
    if not effects:
        return None
    cost_facts += [f'card_ability_modifier("{cid}", "{aid}", "{t}")' for t in mods]
    return CardOut(cid, cost_facts + _effect_facts(cid, aid, effects), "activated")


_REPL = re.compile(r"^If (?P<cond>.+? would .+?), (?P<repl>.+?) instead\.?$", re.I | re.S)


def _replacement(unit, ctx):
    """'If <X> would <event>, <replacement> instead.' — a §614 replacement effect. The replaced event
    is recorded as a descriptive slug (like a trigger condition) on a 'replacement'-kind ability, and
    the replacement body must parse into grounded effects (else abstain). Quantitative replacements
    ('… twice that many …', '… plus N …') don't ground and so faithfully fall through to abstention."""
    m = _REPL.match(unit.raw)
    if not m or '"' in unit.raw:
        return None
    effects = _parse_body(m.group("repl"))
    if not effects:
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    ev = ground.slug(m.group("cond"))
    head = [f'card_ability("{cid}", "{aid}", "replacement")',
            f'card_ability_trigger("{cid}", "{aid}", "{ev}")']
    return CardOut(cid, head + _effect_facts(cid, aid, effects), "replacement")


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
    head = [f'card_ability("{cid}", "{aid}", "triggered")', f'card_ability_trigger("{cid}", "{aid}", "{trig}")']
    mh = _MODAL_HEAD.match(m.group("body"))
    if mh:
        return CardOut(cid, head + [f'card_modal("{cid}", "{ground.slug(mh.group(1))}")'], "triggered")
    body, mods = _split_modifiers(m.group("body"))
    effects = _parse_body(body) if body else None
    if not effects:
        return None
    head += [f'card_ability_modifier("{cid}", "{aid}", "{t}")' for t in mods]
    return CardOut(cid, head + _effect_facts(cid, aid, effects), "triggered")


_LOYALTY = re.compile(r"^\[([+\-−]?(?:\d+|X))\]:\s*(?P<body>.+)$")


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
    facts = [f'card_ability("{cid}", "{aid}", "loyalty")', f'card_ability_cost("{cid}", "{aid}", "{cost}")']
    facts += [f'card_ability_modifier("{cid}", "{aid}", "{t}")' for t in mods]
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
    facts = [f'card_ability("{cid}", "{aid}", "saga_chapter")']
    for ch in m.group("ch").split(", "):
        if ch in _ROMAN:
            facts.append(f'card_ability_trigger("{cid}", "{aid}", "chapter_{_ROMAN[ch]}")')
    return CardOut(cid, facts + _effect_facts(cid, aid, effects), "saga_chapter")


def _etb_choose(unit, ctx):
    """'As ~ enters, choose a <X>.' — an as-enters choice replacement (§614.12/§603.6e)."""
    m = re.match(r"^As (?:~|it) enters, choose (?:a|an) (.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_etb_choose("{cid}", "{ground.slug(m.group(1))}")'], "etb_choose")


def _as_enters(unit, ctx):
    """'As ~ enters, <effect>.' — a §614.12/§603.6e as-enters ability whose body must ground (else
    abstain). Modeled as a triggered ability on the 'enters' event, reusing the effect engine."""
    m = re.match(r"^As (?:~|it) enters, (?P<body>.+)$", unit.raw, re.I)
    if not m:
        # trailing form: '<body> as ~ enters.' (e.g. 'If it's neither day nor night, it becomes day as
        # ~ enters.') — same §614.12 as-enters ability with the body before the clause.
        m = re.match(r"^(?P<body>.+?) as (?:~|it) enters\.?$", unit.raw, re.I)
    if not m:
        return None
    effects = _parse_body(m.group("body"))
    if not effects:
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    return CardOut(cid, [f'card_ability("{cid}", "{aid}", "triggered")',
                         f'card_ability_trigger("{cid}", "{aid}", "enters")']
                   + _effect_facts(cid, aid, effects), "triggered")


# static player-rule modifications, each grounded: hand size §402.2, extra land §505.5b/§116.2a,
# top-card play §601/§715, no max hand size §402.2.
_STATIC_PLAYER = [
    (r"^You have no maximum hand size(?: for the rest of the game)?\.?$", "no_maximum_hand_size"),
    (r"^Players have no maximum hand size(?: for the rest of the game)?\.?$", "players_no_maximum_hand_size"),
    (r"^Your opponents have no maximum hand size(?: for the rest of the game)?\.?$", "opponents_no_maximum_hand_size"),
    (r"^Your opponents can't cast spells during your turn\.?$", "opponents_cant_cast_during_your_turn"),
    (r"^Players can't gain life\.?$", "players_cant_gain_life"),
    (r"^Your opponents can't gain life\.?$", "opponents_cant_gain_life"),
    (r"^You may play an additional land on each of your turns\.?$", "extra_land_per_turn"),
    (r"^You may play (?:an? )?additional lands? on each of your turns\.?$", "extra_land_per_turn"),
    (r"^You may look at the top card of your library any time\.?$", "look_at_top_card"),
    (r"^You may choose not to untap ~ during your untap step\.?$", "may_skip_untap"),
    (r"^You play with your hand revealed\.?$", "you_play_hand_revealed"),
    (r"^Your opponents play with their hands revealed\.?$", "opponents_play_hands_revealed"),
    (r"^Players play with their hands revealed\.?$", "players_play_hands_revealed"),
    (r"^Each player can't cast more than one spell each turn\.?$", "each_player_one_spell_per_turn"),
    (r"^You can't lose the game and your opponents can't win the game\.?$", "cant_lose_opponents_cant_win"),
]


def _static_player(unit, ctx):
    cid, r = ctx["id"], unit.raw

    def mk(tag):
        return CardOut(cid, [f'card_static_player("{cid}", "{tag}")'], "static_player")

    for pat, tag in _STATIC_PLAYER:
        if re.match(pat, r, re.I):
            return mk(tag)
    # capturing permission/restriction statics (§116/§118/§601) — the scope is a descriptive slug.
    m = re.match(r"^You can't cast (.+?)\.?$", r, re.I)
    if m:
        return mk("cant_cast_" + ground.slug(m.group(1)))
    m = re.match(r"^You may cast (.+?) from (the top of your library|your graveyard|exile|among them)\b.*?\.?$", r, re.I)
    if m:
        return mk("may_cast_" + ground.slug(m.group(1)) + "_from_" + ground.slug(m.group(2)))
    m = re.match(r"^You may play (.+?) from (the top of your library|your graveyard|exile)\b.*?\.?$", r, re.I)
    if m:
        return mk("may_play_" + ground.slug(m.group(1)) + "_from_" + ground.slug(m.group(2)))
    m = re.match(r"^You may play any number of (?:additional )?lands?\b.*?\.?$", r, re.I)
    if m:
        return mk("unlimited_lands")
    m = re.match(r"^You may spend (.+?) as though it were (.+?)\.?$", r, re.I)
    if m:
        return mk("spend_" + ground.slug(m.group(1)) + "_as_" + ground.slug(m.group(2)))
    m = re.match(r"^You may (play lands(?: and cast (?:noncreature )?spells)?|cast (?:noncreature )?spells) from (.+?)\.?$", r, re.I)
    if m:
        return mk("may_" + ground.slug(m.group(1)) + "_from_" + ground.slug(m.group(2)))
    m = re.match(r"^You may look at (?:and play )?(.+?)(?: for as long as .+?| this turn)?\.?$", r, re.I)
    if m:
        return mk("may_look_at_" + ground.slug(m.group(1)))
    return None


# card-level static declarations (commander/companion variants §903; static combat requirements §508/509)
_CARD_STATIC = [
    # doubling replacement effects (§614 — Doubling Season / Parallel Lives / Hardened Scales family)
    (r"^If one or more tokens would be created under your control, twice that many.*instead\.?$", "doubles_tokens"),
    (r"^If an effect would create one or more tokens under your control, it creates twice that many.*instead\.?$", "doubles_tokens"),
    (r"^If one or more (?:[\w/+ ]*?)counters would be put on .*?, twice that many.*instead\.?$", "doubles_counters"),
    (r"^If you would put one or more (?:[\w/+ ]*?)counters on .*?, put twice that many.*instead\.?$", "doubles_counters"),
    (r"^~ can be your commander\.?$", "can_be_commander"),
    (r"^[A-Z][a-z]+ commander$", "can_be_commander"),       # ability words: 'Spell commander', …
    (r"^Commander [a-z]+$", "can_be_commander"),             # 'Commander ninja', 'Commander enchantment'
    (r"^Friends forever$", "friends_forever"),
    (r"^~ can't be your commander\.?$", "cant_be_commander"),
    (r"^Doctor's companion\.?$", "doctors_companion"),
    (r"^Choose a Background\.?$", "choose_a_background"),
    (r"^Partner\.?$", "partner"),
    (r"^~ must be blocked if able\.?$", "must_be_blocked"),
    (r"^All creatures able to block ~ do so\.?$", "lure"),
    (r"^~ can't attack or block alone\.?$", "cant_attack_or_block_alone"),
    (r"^~ attacks? alone\.?$", "attacks_alone"),
    (r"^~ can attack(?: this turn)? as though it didn't have defender\.?$", "can_attack_despite_defender"),
    (r"^If ~ is in your opening hand, you may begin the game with it on the battlefield\.?$", "opening_hand_to_battlefield"),
    (r"^Play with the top card of your library revealed\.?$", "play_with_top_revealed"),
    (r"^Players play with the top card of their libraries revealed\.?$", "players_play_top_revealed"),
    (r"^You may play lands from your graveyard\.?$", "play_lands_from_graveyard"),
    (r"^A deck can have any number of cards named ~\.?$", "any_number_in_deck"),
    (r"^It's still a land\.?$", "still_a_land"),
    (r"^Creatures with power less than ~'s power can't block it\.?$", "cant_be_blocked_by_lower_power"),
    (r"^Enchanted creature can't attack or block, and its activated abilities can't be activated\.?$",
     "enchanted_cant_attack_block_or_activate"),
    (r"^You may cast ~ as though it had flash\.?$", "cast_as_though_flash"),
    (r"^You may cast creature spells as though they had flash\.?$", "creature_spells_as_though_flash"),
    (r"^You may cast spells as though they had flash\.?$", "cast_spells_as_though_flash"),
    (r"^You may look at the top card of your library any time\.?$", "look_at_top_card"),
    (r"^You may choose the same mode more than once\.?$", "modal_repeat_allowed"),
    (r"^You don't lose the game for having \w+ or less life\.?$", "dont_lose_from_life"),
    (r"^You don't lose the game for (?:having an empty library|drawing from an empty library)\.?$", "dont_lose_from_empty_library"),
    (r"^Cards in graveyards can't be the targets of spells or abilities\.?$", "graveyard_cards_untargetable"),
    (r"^~ is the chosen (?:type|color) in addition to its other (?:types|colors)\.?$", "is_chosen_type_added"),
]


def _card_static(unit, ctx):
    for pat, tag in _CARD_STATIC:
        if re.match(pat, unit.raw, re.I):
            return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "{tag}")'], "card_static")
    return None


# Static-anthem SUBJECT grammar — a SUBSET of permanents an always-on effect applies to (§613 layer
# 6/7). Broader than _TGT (which is for spell targets): it admits multi-word adjective chains and a
# trailing set-qualifier ('… of the chosen type', '… with flying', '… that are enchanted'). The set
# descriptor is recorded as a faithful slug (like _cant/_restriction subjects) — it names WHICH
# permanents, not a new mechanic; the grounded part is the verb (modify_pt / grant_keyword).
_SUBJ = (
    r"(?:~|enchanted \w+|equipped \w+|"
    r"(?:other |another |all |each )?[\w'-]+(?: [\w'-]+){0,4}? "
        r"(?:you control|you own|your opponents control|an opponent controls|they control|your team controls|a player controls|each player controls)"
        r"(?: (?:with|of|that are|that have|named|without|other than) [\w'+/{}., -]+?)?|"
    r"(?:other |all )?[\w'-]+ (?:creatures?|permanents?|tokens?)|"
    r"creatures?|permanents?|you|players|it)"
)


_ASLONGAS = re.compile(r"^As long as (?P<cond>.+?), (?P<eff>.+?)\.?$", re.I)


def _as_long_as(unit, ctx):
    """'As long as <cond>, <effect>.' — a conditional static (§611). Rewrites the leading condition to
    the trailing 'as long as' form the static handlers already understand and re-dispatches, so the
    effect ('it gets +N/+N', 'Goblin creatures get +N/+N', '<subj> has <kw>') carries the condition.
    Abstains if the effect itself doesn't ground."""
    m = _ASLONGAS.match(unit.raw)
    if not m:
        return None
    eff = m.group("eff").strip()
    rebuilt = f"{eff[0].upper()}{eff[1:]} as long as {m.group('cond')}"
    return _try_patterns(dataclasses.replace(unit, raw=rebuilt), ctx)

_STATIC_PT = re.compile(rf"^(?:during your turn, )?(?P<who>{_SUBJ}) gets? (?P<pt>[+-]\d+/[+-]\d+)"
                        rf"(?: and (?:has|gains?) (?P<kw>[\w,{{}} ]+?))?"
                        rf"(?: (?P<conn>as long as|for each) (?P<cond>.+?))?\.?$", re.I)


def _static_pt(unit, ctx):
    """A static P/T grant with no duration — '<subject> get(s) +N/+N[ and has <keywords>].' (§613:
    layer 7c P/T, layer 6 ability-adding). The absent 'until end of turn' is what makes it static;
    one-shot 'until end of turn' pumps go to _spell/_activated via the effect engine instead."""
    m = _STATIC_PT.match(unit.raw)
    if not m:
        return None
    who = _target_slug(m.group("who"))
    cond = ground.slug(m.group("conn")) + "_" + ground.slug(m.group("cond")) if m.group("cond") else \
        ("during_your_turn" if unit.raw.lower().startswith("during your turn,") else "-")
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    facts = [f'card_ability("{cid}", "{aid}", "static")',
             f'card_effect("{cid}", "{aid}", 0, "modify_pt", "{m.group("pt")}", "{who}", "-", "{cond}")']
    if m.group("kw"):
        grounded = [_ground_kw(k.strip()) for k in re.split(r",| and ", m.group("kw")) if k.strip()]
        if not all(grounded):
            return None                       # abstain rather than emit a partial grant
        for i, (kw, _param) in enumerate(grounded, 1):
            facts.append(f'card_effect("{cid}", "{aid}", {i}, "grant_keyword", "{kw}", "{who}", "-", "{cond}")')
    return CardOut(cid, facts, "static_pt")


# a conjunct in 'gets +N/+N, <c1>, <c2>, and <cN>' begins with one of these static predicates; a
# comma/and that is NOT followed by one (e.g. 'has flying, first strike, and trample') stays joined.
_PRED_LEAD = re.compile(r"^(?:has|have|is|are|can't|cant|can|gains?|becomes?|doesn't|don't|loses?|must|"
                        r"attacks?|blocks?)\b", re.I)


def _split_conjuncts(rest):
    """Split 'has intimidate, and is a black Zombie' into ['has intimidate', 'is a black Zombie'] —
    breaking only at a ', '/' and ' whose right side starts a NEW static predicate, so a keyword list
    inside one conjunct ('has flying, first strike, and trample') is left intact."""
    parts = re.split(r"(,\s+and\s+|,\s+|\s+and\s+)", rest)
    out, cur, i = [], parts[0], 1
    while i < len(parts):
        sep, nxt = parts[i], parts[i + 1] if i + 1 < len(parts) else ""
        if _PRED_LEAD.match(nxt.strip()):
            out.append(cur)
            cur = nxt
        else:
            cur = cur + sep + nxt
        i += 2
    out.append(cur)
    return [p.strip() for p in out if p.strip()]


def _anthem_conjunct(unit, ctx):
    """'<subject> gets +N/+N[, <conjunct>]*[, and <conjunct>].' where each conjunct is a SECOND grounded
    static — a keyword grant ('has flying'), restriction ('can't block'), 'doesn't untap …', a type/
    color set ('is a black Zombie'), or a quoted ability. Emits the P/T plus every conjunct interpreted
    by re-dispatching '<subject> <conjunct>'. Runs AFTER _static_pt (which owns the plain 'and has
    <keyword>' form); abstains if ANY conjunct doesn't ground (prime directive — no partial grant)."""
    m = re.match(rf"^(?P<subj>{_SUBJ}) gets? (?P<pt>[+-]\d+/[+-]\d+)(?:,| and) (?P<rest>.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    subj = m.group("subj")
    extra = []
    for j, conj in enumerate(_split_conjuncts(m.group("rest"))):
        # dispatch each conjunct under a DISTINCT seq so ability/effect ids never collide
        bo = _try_patterns(dataclasses.replace(unit, raw=f"{subj[0].upper()}{subj[1:]} {conj}"),
                           {**ctx, "seq": f"{ctx.get('seq', 0)}{chr(98 + j)}"})
        if not bo:
            return None
        extra += bo.facts
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    facts = [f'card_ability("{cid}", "{aid}", "static")',
             f'card_effect("{cid}", "{aid}", 0, "modify_pt", "{m.group("pt")}", "{_target_slug(subj)}", "-", "-")']
    return CardOut(cid, facts + extra, "static_pt")


def _etb_tapped(unit, ctx):
    """'~ enters tapped[ unless <condition>]' / '~ enters tapped with N <kind> counters on it.' — an
    ETB replacement (§614). The 'unless …' condition is a descriptive slug (cross-cutting across the
    tapland variants); the 'with N counters' form also emits the counter-placement fact."""
    mc = re.match(r"^~ enters tapped with (\w+) ([+\-]\d+/[+\-]\d+|\w[\w ]*?) counters? on it\.?$",
                  unit.raw, re.I)
    if mc:
        cid = ctx["id"]
        return CardOut(cid, [f'card_enters_tapped("{cid}", "-")',
                             f'card_enters_with_counters("{cid}", "{ground.slug(mc.group(2))}", "{ground.slug(mc.group(1))}")'],
                       "etb_tapped")
    m = re.match(r"^~ enters tapped(?: unless (.+?))?\.?$", unit.raw)
    if not m:
        return None
    cid = ctx["id"]
    cond = "unless_" + ground.slug(m.group(1)) if m.group(1) else "-"
    return CardOut(cid, [f'card_enters_tapped("{cid}", "{cond}")'], "etb_tapped")


def _enters_tapped_others(unit, ctx):
    """'<types> [your opponents control] enter [the battlefield] tapped.' — a §614 static that taps a
    class of OTHER permanents as they enter (Kismet / Frozen Aether / Imposing Sovereign family). The
    affected class + scope is a faithful descriptive slug; emitted card-level since it's not on ~ itself."""
    m = re.match(r"^((?:[A-Za-z]+, )*(?:[A-Za-z]+,? and )?[A-Za-z]+)"
                 r"( your opponents control| an opponent controls)? enters?(?: the battlefield)? tapped\.?$",
                 unit.raw, re.I)
    if not m:
        return None
    types = ground.slug(m.group(1))
    if types in ("it", "they", "this", "that"):            # ~/it ETB is _etb_tapped's job, not this
        return None
    scope = "opponents_" if m.group(2) else ""
    cid = ctx["id"]
    return CardOut(cid, [f'card_static("{cid}", "{scope}{types}_enter_tapped")'], "card_static")


def _modal(unit, ctx):
    """'Choose one —' / 'Choose one or both —' / 'Choose one at random —' — a modal spell/ability
    header (§700.2). Also the Commander-precon form 'Choose one. If you control a commander as you cast
    ~, you may choose both instead.' (the commander rider recorded as a flag)."""
    m = re.match(r"^Choose (one or both|one or more|up to one|up to two|up to three|one|two|three)"
                 r"(?P<rand> at random)?\s*[—–-]?\s*"
                 r"(?:\.\s*(?P<cmd>If you control a commander[^.]*\.))?"
                 r"(?:\s*(?P<rep>You may choose the same mode more than once\.))?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    mode = ground.slug(m.group(1)) + ("_at_random" if m.group("rand") else "")
    facts = [f'card_modal("{cid}", "{mode}")']
    if m.group("cmd"):
        facts.append(f'card_static("{cid}", "commander_choose_both")')
    if m.group("rep"):
        facts.append(f'card_static("{cid}", "modal_repeat_allowed")')
    return CardOut(cid, facts, "modal")


def _mode_option(unit, ctx):
    """'• <effect>' — one mode of a modal spell/ability; its body is a normal effect clause."""
    m = re.match(r"^[•·∙]\s*(?P<body>.+)$", unit.raw)
    if not m:
        return None
    effects = _parse_body(m.group("body"))
    if not effects:
        return None
    cid, aid = ctx["id"], f"mode{ctx.get('seq', 0)}"
    return CardOut(cid, [f'card_mode_option("{cid}", "{aid}")'] + _effect_facts(cid, aid, effects), "mode_option")


# grounded static restrictions: block/attack §508–509, be blocked §509, be countered §701/§601.
_CANT = {"block": "block", "be blocked": "be_blocked", "attack": "attack",
         "attack or block": "attack_or_block", "be countered": "be_countered",
         "be regenerated": "be_regenerated", "be sacrificed": "be_sacrificed",
         "attack you": "attack_you", "attack you or planeswalkers you control": "attack_you_or_pws",
         "attack alone": "attack_alone", "block alone": "block_alone",
         "attack or block alone": "attack_or_block_alone", "gain life": "gain_life",
         "be targeted by spells or abilities your opponents control": "be_targeted_by_opponents"}
_CANT_SUBJ = re.compile(r"^(~|enchanted creature|equipped creature|enchanted permanent|equipped permanent|"
                        r"players|your opponents|you|creatures|creature spells you control) can't (.+?)\.?$", re.I)


def _cant(unit, ctx):
    """'<subject> can't <X>[ and can't <Y>].' — a static restriction grounded in combat/§701/§119 rules
    (fixed action set). Subject is the card (~), the attached permanent, or a player/spell set. A
    compound 'can't X and can't Y' yields one fact per restriction; every action must be in the set."""
    m = _CANT_SUBJ.match(unit.raw)
    if not m:
        return None
    actions = [a.strip().lower() for a in re.split(r" and can't ", m.group(2))]
    if not all(a in _CANT for a in actions):
        return None
    cid, who = ctx["id"], _target_slug(m.group(1))
    return CardOut(cid, [f'card_cant("{cid}", "{who}", "{_CANT[a]}")' for a in actions], "cant")


# complex static combat restrictions with a qualifier (§508/§509) — captured as a descriptive slug.
_CRESTR = [
    (r"can't be blocked except by (.+)", "cant_be_blocked_except_by_"),
    (r"can't be blocked by more than (.+)", "cant_be_blocked_by_more_than_"),
    (r"can't be blocked by (.+)", "cant_be_blocked_by_"),
    (r"can block only (.+)", "can_block_only_"),
    (r"can attack only (.+)", "can_attack_only_"),
    (r"can't attack unless (.+)", "cant_attack_unless_"),
    (r"can't block unless (.+)", "cant_block_unless_"),
    (r"attacks each combat if able if (.+)", "attacks_each_combat_if_"),
    # generic trailing captures (after the specific shapes above) — a §508/§509 restriction with any
    # qualifier, recorded as a descriptive slug rather than abstaining.
    (r"can't be blocked (.+)", "cant_be_blocked_"),
    (r"can't block (.+)", "cant_block_"),
    (r"can't attack (.+)", "cant_attack_"),
]


def _combat_restriction(unit, ctx):
    """'<subject> can('t) <combat-verb> <qualifier>.' — a static combat restriction with a condition
    (§508/§509). The qualifier is recorded as a descriptive slug (like a trigger/condition slug)."""
    m = re.match(r"^(~|enchanted creature|equipped creature|enchanted permanent|equipped permanent|"
                 r"creatures|all creatures|creature spells) (can.+?)\.?$",
                 unit.raw, re.I)
    if not m:
        return None
    who = _target_slug(m.group(1))
    # a second, non-restriction effect ('… and has shroud', '… and gets +1/+1') must NOT be buried in
    # the restriction slug — abstain rather than emit a conflated fact (prime directive).
    if re.search(r" and (?:has|have|gains?|is|gets?|can't|becomes?) ", m.group(2), re.I):
        return None
    for pat, prefix in _CRESTR:
        mm = re.match("^" + pat + "$", m.group(2), re.I)
        if mm:
            cid = ctx["id"]
            return CardOut(cid, [f'card_restriction("{cid}", "{who}", "{prefix}{ground.slug(mm.group(1))}")'],
                           "combat_restriction")
    return None


_GRANTED = re.compile(
    r'^(?P<who>~|enchanted \w+|equipped \w+|(?:\w+ )?\w+ you control|other [\w ]+?|'
    r'target [\w ]+?|each [\w ]+?|all [\w ]+?|it|they) (?:has|have|gains?) "(?P<ab>.+)"'
    r'(?P<dur> until end of turn)?\.?$', re.I | re.S)


def _granted_ability(unit, ctx):
    """'<subject> has/gains "<ability>" [until end of turn].' — granting a quoted ability (§613.6).
    The granted ability is recorded as a slug of its (already self-normalized) text — coarse but
    faithful; its 'self' resolves to whoever holds it. Only WHOLE-unit grants are handled here, since
    a quoted ability's internal punctuation would corrupt the body splitters."""
    m = _GRANTED.match(unit.raw)
    if not m:
        return None
    ab = ground.slug(m.group("ab"))[:160]
    if not ab:
        return None
    cid = ctx["id"]
    dur = "until_end_of_turn" if m.group("dur") else "-"
    return CardOut(cid, [f'card_grants_ability("{cid}", "{_target_slug(m.group("who"))}", "{ab}", "{dur}")'],
                   "granted_ability")


def _static_grant(unit, ctx):
    """A static keyword grant with no P/T — '[During your turn, ]<subject> has/have <keywords>
    [as long as <cond>].' (§613 layer 6): 'Enchanted creature has flying', 'During your turn, ~ has
    first strike', 'Other creatures you control have trample as long as you control a Forest'."""
    m = re.match(rf"^(?:during your turn, )?(?P<who>{_SUBJ}) (?:has|have|gains?) (?P<kw>[\w,{{}} ]+?)"
                 rf"(?: as long as (?P<cond>.+?))?\.?$", unit.raw, re.I)
    if not m:
        return None
    grounded = [_ground_kw(k.strip()) for k in re.split(r",\s*(?:and )?| and ", m.group("kw")) if k.strip()]
    if not grounded or not all(grounded):
        return None
    who = _target_slug(m.group("who"))
    cond = "as_long_as_" + ground.slug(m.group("cond")) if m.group("cond") else \
        ("during_your_turn" if unit.raw.lower().startswith("during your turn,") else "-")
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    facts = [f'card_ability("{cid}", "{aid}", "static")']
    facts += [f'card_effect("{cid}", "{aid}", {i}, "grant_keyword", "{kw}", "{who}", "-", "{cond}")'
              for i, (kw, _p) in enumerate(grounded)]
    return CardOut(cid, facts, "static_grant")


def _additional_cost(unit, ctx):
    """'As an additional cost to cast ~, <cost>.' (§601.2b/§118) — record the extra casting cost."""
    m = re.match(r"^As an additional cost to cast ~, (.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_additional_cost("{cid}", "{ground.slug(m.group(1))}")'], "additional_cost")


def _exert(unit, ctx):
    """'You may exert ~ as it attacks.[ When you do, <effect>.]' — the §701.40 exert keyword action: a
    static option to exert when attacking, plus the reflexive 'when you do' triggered ability whose
    body must ground (else abstain)."""
    m = re.match(r"^You may exert ~ as it attacks\.(?: When you do, (?P<body>.+?)\.?)?$", unit.raw, re.I)
    if not m or "exert" not in ground.keyword_actions():
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    facts = [f'card_static("{cid}", "may_exert_as_it_attacks")']
    if m.group("body"):
        effects = _parse_body(m.group("body"))
        if not effects:
            return None
        facts += [f'card_ability("{cid}", "{aid}", "triggered")',
                  f'card_ability_trigger("{cid}", "{aid}", "exert_attacks")']
        facts += _effect_facts(cid, aid, effects)
    return CardOut(cid, facts, "exert")


def _enter_as_copy(unit, ctx):
    """'You may have ~ enter as a copy of <X>[, except <mods>].' — a copy/clone ETB (§707/§614). The
    copied object and any exceptions are recorded as a descriptive slug."""
    m = re.match(r"^You may have ~ enter as a copy of (.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "enters_as_copy_of_{ground.slug(m.group(1))}")'],
                   "card_static")


def _escapes_with(unit, ctx):
    """'~ escapes with N <kind> counters on it.' — a rider on the §702.139 Escape keyword describing
    counters the card gains when it escapes."""
    m = re.match(r"^~ escapes with (\w+) ([+\-]\d+/[+\-]\d+|\w[\w ]*?) counters? on it\.?$", unit.raw, re.I)
    if not m or "escape" not in ground.keyword_abilities():
        return None
    return CardOut(ctx["id"],
                   [f'card_static("{ctx["id"]}", "escapes_with_{ground.slug(m.group(1))}_{ground.slug(m.group(2))}_counter")'],
                   "card_static")


def _assign_damage_unblocked(unit, ctx):
    """'You may have ~ assign its combat damage as though it weren't blocked.' — a §509.2 damage-
    assignment option (trample-like)."""
    if not re.match(r"^You may have ~ assign its combat damage as though it weren't blocked\.?$",
                    unit.raw, re.I):
        return None
    return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "may_assign_damage_as_though_unblocked")'],
                   "card_static")


def _cast_as_flash(unit, ctx):
    """'You may cast ~ as though it had flash[ <rider>].' — a flash-granting timing permission (§702.8
    as-though, §601). Any trailing rider ('if you pay {2} more', 'If you cast it any time a sorcery
    couldn't…') is recorded as a descriptive slug suffix — coarse but faithful."""
    m = re.match(r"^You may cast ~ as though it had flash(?:[.,]? (.+?))?\.?$", unit.raw, re.I)
    if m:
        tag = "cast_as_though_flash" + ("_" + ground.slug(m.group(1)) if m.group(1) else "")
        return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "{tag}")'], "card_static")
    # 'You may cast <X> spells as though they had flash' — a flash-grant scoped to a spell class.
    m = re.match(r"^You may cast (.+?) as though they had flash\.?$", unit.raw, re.I)
    if m:
        return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "cast_{ground.slug(m.group(1))}_as_flash")'], "card_static")
    return None


def _alt_cost(unit, ctx):
    """'You may [pay/return/remove …] rather than pay <X>'s/the mana cost [for … spells].' — an
    alternative casting cost (§118.9/§601). The alternative and what it replaces are slugged."""
    m = re.match(r"^You may (.+?) rather than pay (~'s mana cost|its mana cost|the mana cost(?: for [\w' ]+?)?|the equip cost[\w' ]*?)(?:[,.].*)?$", unit.raw, re.I)
    if not m:
        return None
    return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "alt_cost_{ground.slug(m.group(1))}_for_{ground.slug(m.group(2))}")'], "card_static")


def _enters_with_counters(unit, ctx):
    """'~ enters with N +N/+N counters on it.' — an ETB counter replacement (§122/§614)."""
    # dynamic-count form: '… enters with a number of <kind> counters on it equal to <X>' (§122/§614).
    md = re.match(r"^(?:~|it|That \w+) enters with a number of ([+\-]\d+/[+\-]\d+|\w[\w ]*?) counters? on it "
                  r"equal to (.+?)\.?$", unit.raw)
    if md:
        cid = ctx["id"]
        return CardOut(cid, [f'card_enters_with_counters("{cid}", "{ground.slug(md.group(1))}", "equal_to_{ground.slug(md.group(2))}")'],
                       "enters_with_counters")
    m = re.match(r"^(?:If .+?, )?(?:~|it) enters with (\w+) ([+\-]\d+/[+\-]\d+|\w[\w ]*?) counters? on it"
                 r"(?: (?:if|for each) (?P<cond>.+?))?\.?$", unit.raw)
    if not m:
        return None
    cid = ctx["id"]
    cond = m.group("cond") or (unit.raw.lower().startswith("if ") and "conditional") or None
    n = ground.slug(m.group(1)) if not cond else "var"
    return CardOut(cid, [f'card_enters_with_counters("{cid}", "{ground.slug(m.group(2))}", "{n}")'],
                   "enters_with_counters")


def _cast_restriction(unit, ctx):
    """'Cast ~ only <when/if …>.' — a casting timing/condition restriction (§601)."""
    m = re.match(r"^Cast ~ only (.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "cast_only_{ground.slug(m.group(1))}")'], "card_static")


def _doesnt_untap(unit, ctx):
    """'~ / Enchanted creature doesn't untap during …untap step.' — an untap restriction (§502)."""
    m = re.match(r"^(~|Enchanted \w+|Equipped \w+|That creature|That permanent) doesn't untap "
                 r"during (?:its controller's|your|their)(?: next)? untap step\.?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_doesnt_untap("{cid}", "{_target_slug(m.group(1))}")'], "doesnt_untap")


def _cost_modifier(unit, ctx):
    """Cost-reduction / -increase statics (§118.9): '~ costs {S} less to cast [if <cond>]',
    '<X> spells you cast cost {S} less to cast', '~ costs {S} more to cast for each …'."""
    cid = ctx["id"]
    m = re.match(r"^~ costs ((?:\{[^}]+\})+|\d+) (less|more) to cast(?: (.+?))?\.?$", unit.raw, re.I)
    if m:
        sc = ground.slug(m.group(3)) if m.group(3) else "-"
        return CardOut(cid, [f'card_cost_modifier("{cid}", "{m.group(2)}", "{ground.slug(m.group(1))}", "self", "{sc}")'],
                       "cost_modifier")
    m = re.match(r"^((?:the first )?[\w' ]*?spells?(?: you cast)?(?: each turn| this turn| from [\w' ]+?)?) costs? ((?:\{[^}]+\})+|\d+) (less|more) to cast\.?$", unit.raw, re.I)
    if m:
        return CardOut(cid, [f'card_cost_modifier("{cid}", "{m.group(3)}", "{ground.slug(m.group(2))}", '
                            f'"{ground.slug(m.group(1))}", "-")'], "cost_modifier")
    # '~'s/this ability costs {S} less to activate [for each …]' — activated-ability cost reduction
    m = re.match(r"^(?:~'s abilities?|this ability|abilities you activate) costs? ((?:\{[^}]+\})+|\d+) (less|more) to activate(?: (.+?))?\.?$", unit.raw, re.I)
    if m:
        sc = ground.slug(m.group(3)) if m.group(3) else "-"
        return CardOut(cid, [f'card_cost_modifier("{cid}", "{m.group(2)}", "{ground.slug(m.group(1))}", "activated_ability", "{sc}")'],
                       "cost_modifier")
    return None


def _class_level(unit, ctx):
    """A Class card's level-up activated ability '{cost}: Level N' (§716)."""
    m = re.match(r"^((?:\{[^}]+\})+): Level (\d+)$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_class_level("{cid}", "{m.group(1)}", "{m.group(2)}")'], "class_level")


def _cda(unit, ctx):
    """A characteristic-defining ability (§604.3): \"~'s power [and toughness] (is|are) [each] equal to
    <X>\" — the P/T is defined by a game quantity, recorded as a descriptive slug."""
    m = re.match(r"^~'s (power and toughness|power|toughness) (?:is|are) (?:each )?equal to (.+?)\.?$",
                 unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_cda("{cid}", "{ground.slug(m.group(1))}", "{ground.slug(m.group(2))}")'], "cda")


def _painland(unit, ctx):
    """'As ~ enters, you may <action>. If you don't, it enters tapped.' — the conditional-tapland ETB
    (§614): painlands ('pay N life'), fastlands/checklands/etc. ('reveal …', 'pay {1}', a control
    condition). The 'unless' action is recorded as a descriptive slug."""
    m = re.match(r"^As ~ enters, you may (.+?)\. If you don't, (?:it|~) enters tapped\.?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    act = re.sub(r"^pay (\d+) life$", r"pay_\1_life", m.group(1).strip(), flags=re.I)
    return CardOut(cid, [f'card_enters_tapped("{cid}", "unless_{ground.slug(act)}")'], "etb_tapped")


_STATION_BAND = re.compile(r"^(\d+)\+ \| (.+)$", re.S)


def _station_band(unit, ctx):
    """A Station band 'N+ | <ability>' (§702 Station): at N+ charge counters the Spacecraft gains the
    band's ability. Gated on the grounded Station keyword being present on the card; the band body is
    interpreted by the normal ability patterns and tagged with its threshold (abstain if it doesn't
    ground — no half-credit)."""
    card = ctx.get("card") or {}
    if "station" not in ground.keyword_abilities() or "Station" not in (card.get("text") or ""):
        return None
    m = _STATION_BAND.match(unit.raw)
    if not m:
        return None
    bo = _try_patterns(dataclasses.replace(unit, raw=m.group(2).strip()), ctx)
    if not bo:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_level("{cid}", "station", "{m.group(1)}_plus")'] + bo.facts, "station")


def _leveler(unit, ctx):
    """Leveler-card band/P-T lines (§711): 'LEVEL 2-6' / 'LEVEL 7+' -> a level band; a bare 'N/N'
    line -> that band's power/toughness. Gated on the Level Up keyword so a bare P/T can't false-match
    elsewhere."""
    if "Level Up" not in ((ctx.get("card") or {}).get("keywords") or []):
        return None
    cid = ctx["id"]
    m = re.match(r"^LEVEL (\d+)(?:-(\d+)|(\+))$", unit.raw, re.I)
    if m:
        hi = m.group(2) or ("max" if m.group(3) else m.group(1))
        return CardOut(cid, [f'card_level("{cid}", "band", "{m.group(1)}_{hi}")'], "leveler")
    if re.match(r"^[+-]?\d+/[+-]?\d+$", unit.raw):
        return CardOut(cid, [f'card_level("{cid}", "pt", "{unit.raw}")'], "leveler")
    return None


def _enters_prepared(unit, ctx):
    """'~ enters prepared.' — gains the prepared designation as it enters (§722.3)."""
    if not re.match(r"^~ enters prepared\.?$", unit.raw, re.I):
        return None
    return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "enters_prepared")'], "card_static")


def _can_block_additional(unit, ctx):
    """'<subject> can block an additional creature[ each combat]' / 'can block any number of creatures'
    — a static blocking ability (§509). Subject is ~ or a creature subset."""
    sub = r"(?:~|each creature you control|creatures you control)"
    if re.match(rf"^{sub} can block any number of creatures\.?$", unit.raw, re.I):
        return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "can_block_any_number")'], "card_static")
    m = re.match(rf"^{sub} can block an additional (?:creature|\w+ creatures?)(?: each combat)?\.?$", unit.raw, re.I)
    if not m:
        return None
    return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "can_block_additional")'], "card_static")


def _assigns_toughness(unit, ctx):
    """'<subject> assigns combat damage equal to its toughness rather than its power' — the §510 Doran-
    style damage-assignment characteristic (scope coarse; the mechanic is recorded card-level)."""
    if not re.match(r"^(?:~|each creature you control|creatures you control|each creature) assigns? "
                    r"combat damage equal to its toughness rather than its power\.?$", unit.raw, re.I):
        return None
    return CardOut(ctx["id"], [f'card_static("{ctx["id"]}", "assigns_combat_damage_as_toughness")'], "card_static")


def _attacks_each_combat(unit, ctx):
    """'~ attacks each combat if able.' — a combat requirement (§508)."""
    if not re.match(r"^~ attacks each combat if able\.?$", unit.raw):
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'card_attacks_each_combat("{cid}")'], "attacks_each_combat")


def _static_conjuncts(unit, ctx):
    """GENERAL compound static: '<subject> <p1>, <p2>, and <pN>.' where each predicate is itself a
    grounded static (has-keyword / is-a-type / can't-restriction / doesn't-untap). Catches the
    has+type-set, has+restriction, etc. compounds the single-predicate handlers miss. Runs LATE (after
    _static_grant/_static_pt own the simple shapes); abstains unless EVERY conjunct grounds — and only
    fires on 2+ conjuncts, so it never competes with the specific single-predicate handlers."""
    if {"Instant", "Sorcery"} & _types(ctx) or ":" in unit.raw or unit.raw.lstrip().startswith('"'):
        return None
    m = re.match(rf"^(?P<subj>{_SUBJ}) (?P<rest>(?:has|have|is|are|can't|gains?|becomes?|doesn't|don't|"
                 rf"loses?) .+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    conjs = _split_conjuncts(m.group("rest"))
    if len(conjs) < 2:
        return None
    subj = m.group("subj")
    facts = []
    for j, conj in enumerate(conjs):
        bo = _try_patterns(dataclasses.replace(unit, raw=f"{subj[0].upper()}{subj[1:]} {conj}"),
                           {**ctx, "seq": f"{ctx.get('seq', 0)}{chr(98 + j)}"})
        if not bo:
            return None
        facts += bo.facts
    return CardOut(ctx["id"], facts, "static_grant")


_PATTERNS = [_kw_line, _typecycling, _prototype, _kw_param, _leveler, _station_band, _painland, _enters_prepared, _can_block_additional,
             _cost_modifier, _class_level, _cda, _cast_restriction, _etb_tapped, _enters_with_counters,
             _doesnt_untap,
             _attacks_each_combat, _assigns_toughness, _etb_choose, _as_enters, _static_player, _exert, _enter_as_copy,
             _escapes_with, _assign_damage_unblocked, _cast_as_flash, _alt_cost, _card_static,
             _additional_cost, _as_long_as, _static_pt, _anthem_conjunct,
             _granted_ability, _static_grant, _static_conjuncts, _enters_tapped_others, _modal, _mode_option, _cant, _combat_restriction,
             _loyalty, _saga_chapter, _mana_ability, _replacement, _triggered, _activated, _spell,
             _static_control, _static_effect]

# an ability-word prefix is flavor (§207.2c, no rules meaning) — strip 'Heroic —', 'Landfall —',
# 'Bio-plasmic Barrage —' so the triggered ability that follows reaches its pattern. Restricted to a
# prefix FOLLOWED BY a trigger word (When/Whenever/At): that's the safe signal for a real ability word
# and avoids the keyword-cost em-dash syntax ('Cumulative upkeep — Pay {1}', 'Buyback — {cost}'), Saga
# chapters, die tables, loyalty, and the modal header. A grounded keyword prefix is never stripped.
_ABILITY_WORD = re.compile(r"^(?P<word>[A-Z][a-z][\w'’-]*(?: [a-z]?[\w'’-]+){0,2})\s+—\s+(?P<rest>.+)$")


def _strip_ability_word(raw: str) -> str:
    """Strip a flavor ability-word prefix ('Threshold — …', 'Landfall — …', §207.2c) for ANY following
    ability, so it reaches its pattern. Excludes grounded keywords (so keyword-cost em-dash syntax like
    'Cumulative upkeep — Pay {1}' is kept) and the structural 'Choose …'/'Level …' headers; Saga
    chapters (roman, all-caps) and loyalty ('[+1]') don't match the lowercase-tailed word shape."""
    m = _ABILITY_WORD.match(raw)
    if not m:
        return raw
    w = m.group("word")
    if w.startswith(("Choose", "Level")) or _ground_kw(w) is not None:
        return raw
    return m.group("rest")


def _try_patterns(u, ctx):
    for fn in _PATTERNS:
        out = fn(u, ctx)
        if out:
            return out
    return None


def transpile_unit(unit, ctx) -> "CardOut | None":
    """Interpret one ability unit; first faithful pattern wins, else None (abstain).
    Fallback: a multi-sentence line whose EVERY sentence is independently a whole ability (e.g.
    '~ enters tapped. As it enters, choose a color.') — interpret each and merge, no half-credit."""
    stripped = _strip_ability_word(unit.raw)
    u = unit if stripped == unit.raw else dataclasses.replace(unit, raw=stripped)
    out = _try_patterns(u, ctx)
    if out:
        out.template = unit.template
        return out
    sents = _sentences(u.raw)
    if len(sents) >= 2:
        facts, ok = [], True
        for j, sent in enumerate(sents):
            sub = dataclasses.replace(u, raw=sent.rstrip("."))
            so = _try_patterns(sub, {**ctx, "seq": f"{ctx.get('seq', 0)}_{j}"})
            if not so:
                ok = False
                break
            facts += so.facts
        if ok:
            return CardOut(ctx["id"], facts, "multi", template=unit.template)
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
