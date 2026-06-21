"""transpile_card.py — interpret a card-oracle ability UNIT into grounded Datalog facts (or abstain).

Card-side analogue of transpile.transpile_rule. Same contract and prime directive: return the first
FAITHFUL fact, or None — never a lossy/over-claimed fact. Every fact must GROUND in a rules-defined
term (see ground.py): a keyword we can't find in the §702 roster is abstained, not invented.

A pattern fn takes (unit, ctx) and returns a CardOut | None. ctx carries the card's typeline/colors
etc. for patterns that need it. Patterns are tried in order; first hit wins.

Slice 1 (this file): keyword abilities — the productive head of the distribution.
  _kw_line   "Flying" / "Flying, vigilance" / "First strike"   -> printed_keyword(card, kw)
  _kw_param  "Enchant creature" / "Equip {S}" / "Ward {S}"     -> printed_keyword(card, kw)
                                                                + keyword_param(card, kw, arg)
Later slices (registered here as they land): mana abilities, activated "cost: effect", triggered
"when/whenever/at …", spell effects (reusing transpile.py's grammar patterns).
"""

from __future__ import annotations

import dataclasses
import functools
import os
import re
from dataclasses import dataclass, field

import ground
from card_effects import parse_effect, parse_clause, parse_clauses, _TGT, _mana_production, _is_compound_object
from card_effects import Effect


# ---- dependency-graph fallback (spaCy) ---------------------------------------------------------
# When the curated regex templates fail, read the clause's DEPENDENCY GRAPH instead of its surface
# string: find a verb we trust (an UNAMBIGUOUS lemma -> grounded verb map — no 'gain'/'put'/'deal'
# whose sense is positional), then capture its grammatical arguments (direct object, the 'to/into/onto'
# prep that names a zone). This recovers word-order variants the positional regex can't ('Return to the
# battlefield X', 'Exile, then return …') without trusting spaCy to CHOOSE the verb. Lazy-loaded so the
# model only initialises if a clause actually reaches the fallback.
_DEP_VERB = {"return": None, "exile": "exile", "destroy": "destroy", "tap": "tap", "untap": "untap",
             "sacrifice": "sacrifice", "regenerate": "regenerate", "counter": "counter",
             "goad": "goad", "detain": "detain", "scry": "scry", "mill": "mill"}
_DEP_ZONE = {"hand": "return_to_hand", "battlefield": "return_to_battlefield",
             "library": "return_to_hand", "graveyard": "put_in_graveyard"}


def _spacy_effect(clause: str):
    """Dependency-graph extraction for a single grounded verb + object (+ zone). Returns an Effect or
    None — deliberately STRICT so a mis-parse or a non-imperative use never yields a wrong fact:
      • the verb must be the clause ROOT (not buried in a subordinate/relative clause);
      • the clause must be a bare imperative — no subject (no nsubj), no negation, no modal/'unless';
      • no conjunction or second clause hanging off the verb (cc/conj/advcl/ccomp) — those would drop
        content ('destroy that creature AND ~');
      • the verb + its single direct object must SPAN the whole clause (every non-punct token sits in
        the verb's subtree) so nothing is silently dropped;
      • 'return' additionally needs a 'to/into <zone>' prepositional object."""
    nlp = _dep_nlp()
    if nlp is None or re.search(r"\b(can't|cannot|can not|n't|not|unless|would|may|if|whenever|when|"
                               r"additional cost|rather than|as though|for each)\b", clause, re.I):
        return None
    doc = nlp(clause)
    root = next((t for t in doc if t.dep_ == "ROOT"), None)
    if root is None or root.lemma_.lower() not in _DEP_VERB or root.pos_ != "VERB":
        return None
    deps = {ch.dep_ for ch in root.children}
    if deps & {"nsubj", "nsubjpass", "neg", "cc", "conj", "advcl", "ccomp", "csubj", "mark", "aux"}:
        return None
    obj = next((ch for ch in root.children if ch.dep_ in ("dobj", "obj")), None)
    if obj is None or any(ch.dep_ in ("cc", "conj") for ch in obj.children):
        return None
    # the verb must head the entire clause — no token outside its subtree (else content is dropped)
    span = {t.i for t in root.subtree}
    if any(t.i not in span for t in doc if not t.is_punct):
        return None
    obj_slug = ground.slug(" ".join(t.text for t in obj.subtree if t.dep_ != "punct"))
    # a leading preposition/conjunction in the object means the parse mis-attached it — abstain.
    if not obj_slug or len(obj_slug) > 80 or re.match(r"^(to|from|of|with|at|into|onto|and|or)_", obj_slug):
        return None
    lem = root.lemma_.lower()
    if lem == "return":
        zone = None
        for ch in root.children:
            if ch.dep_ in ("prep", "dative") and ch.text.lower() in ("to", "into", "onto"):
                sub = {t.text.lower() for t in ch.subtree}
                zone = next((z for w, z in _DEP_ZONE.items() if w in sub), None)
                if zone:
                    break
        return Effect(zone, "-", obj_slug) if zone else None
    return Effect(_DEP_VERB[lem], "-", obj_slug)


@functools.lru_cache(maxsize=1)
def _dep_nlp():
    # MTG_NO_SPACY disables the spaCy dependency-graph fallback WITHOUT importing it. `import transpile`
    # runs `spacy.load("en_core_web_sm")`, whose pipeline construction eagerly pulls in thinc->torch
    # (~400MiB) + the legacy spacy-transformers entry point->HuggingFace transformers (~140MiB). That
    # ~700MiB floor — not any parse — is what OOMs memory-thin boxes during a full corpus sweep
    # (migrate_check), since the first card that falls through to _spacy_effect triggers the whole load.
    if os.environ.get("MTG_NO_SPACY"):
        return None
    try:
        import transpile
        return transpile._NLP
    except Exception:
        return None

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
        facts.append(f'printed_keyword("{ctx["id"]}", "{kw}")')
        if param:
            facts.append(f'keyword_param("{ctx["id"]}", "{kw}", "{param}")')
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
    facts = [f'printed_keyword("{cid}", "cycling")', f'keyword_param("{cid}", "cycling", "{g[1]}")']
    if m.group(2):
        facts.append(f'keyword_param("{cid}", "cycling", "cost_{ground.slug(m.group(2))}")')
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
        facts = [f'printed_keyword("{ctx["id"]}", "{kw}")', f'keyword_param("{ctx["id"]}", "{kw}", "{param}")']
        if arg:
            facts.append(f'keyword_param("{ctx["id"]}", "{kw}", "{ground.slug(arg)}")')
        return CardOut(ctx["id"], facts, "kw_param")
    s = ground.slug(body)
    for kw in _KW_BY_LEN:
        if s == kw:
            return None                                   # bare keyword — _kw_line's job
        if s.startswith(kw + "_"):
            arg = body[len(kw.replace("_", " ")):].strip()
            facts = [f'printed_keyword("{ctx["id"]}", "{kw}")']
            if arg:
                facts.append(f'keyword_param("{ctx["id"]}", "{kw}", "{ground.slug(arg) or arg}")')
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
    return CardOut(cid, [f'printed_keyword("{cid}", "prototype")',
                         f'keyword_param("{cid}", "prototype", "cost_{ground.slug(m.group(1))}")',
                         f'keyword_param("{cid}", "prototype", "pt_{m.group(2)}")'], "prototype")


_ESCAPE = re.compile(r"^Escape\s*[—-]\s*((?:\{[^}]+\})+),\s*Exile (\w+) other cards? from your graveyard\.", re.I)
_NUM_WORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_ESCAPE_COLOR = {"w": "white", "u": "blue", "b": "black", "r": "red", "g": "green", "c": "colorless"}


def _escape_cost_facts(cost_str, cid):
    """Parse an escape mana cost '{G}{G}{U}{U}' / '{3}{B}{B}' into STRUCTURED card-level cost facts
    (card_escape_generic + card_escape_pip per color, the same shape as a printed mana cost). A symbol the
    cost model can't hold faithfully (X / hybrid / Phyrexian / snow) abstains the whole escape (-> None)."""
    pips: dict[str, int] = {}
    generic = 0
    for sym in re.findall(r"\{([^}]+)\}", cost_str):
        s = sym.lower()
        if s.isdigit():
            generic += int(s)
        elif s in _ESCAPE_COLOR:
            pips[_ESCAPE_COLOR[s]] = pips.get(_ESCAPE_COLOR[s], 0) + 1
        else:
            return None                                    # X / hybrid / Phyrexian / snow — abstain (faithful)
    facts = [f'card_escape_generic("{cid}", {generic})']    # always present (even 0), like a printed cost
    facts += [f'card_escape_pip("{cid}", "{col}", {n})' for col, n in sorted(pips.items())]
    return facts


def _escape(unit, ctx):
    """'Escape—<cost>, Exile N other cards from your graveyard' (§702.166) — the alternative cost to cast this
    card from the GRAVEYARD. Emits the escape keyword + its STRUCTURED mana cost (card_escape_generic /
    card_escape_pip, like a printed cost) + the exile-count (card_escape_exile) — the facts the engine's
    eff_pip cost-switch and the driver's exile-cost payment read to make the card castable from the graveyard."""
    m = _ESCAPE.match(unit.raw.strip())
    if not m or "escape" not in ground.keyword_abilities():
        return None
    w = m.group(2).lower()
    n = _NUM_WORD.get(w, int(w) if w.isdigit() else None)
    cost_facts = _escape_cost_facts(m.group(1), ctx["id"]) if n is not None else None
    if cost_facts is None:                                 # unreadable exile count / cost -> abstain
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'printed_keyword("{cid}", "escape")',
                         f'keyword_param("{cid}", "escape", "cost_{ground.slug(m.group(1))}")',
                         f'card_escape_exile("{cid}", {n})', *cost_facts], "escape")


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
    facts = [f'mana_ability("{cid}", "{cost}")']
    for p in prod:
        facts.append(f'adds_mana("{cid}", "{cost}", "{p}")')
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
_BARE_PRED = re.compile(r"^(?:draws?|discards?|gains?|gets?|has|have|loses?|mills?|exiles?|shuffles?|"
                        r"sacrifices?|creates?|puts?|returns?|reveals?|searches?|taps?|untaps?|adds?|"
                        r"becomes?|attacks?|blocks?)\b", re.I)
# BASE-FORM (imperative) one-shot effect verbs — when a part OPENS with one of these it is a verb
# phrase ('Exile target creature …', 'Tap all lands …', 'Destroy that creature …'), NOT a subject NP,
# so _leading_subject must not capture its verb+object as a 'subject' to reattach to a sibling clause.
_IMPERATIVE_LEAD = re.compile(r"^(?:exile|tap|untap|destroy|return|put|draw|create|counter|search|reveal|"
                              r"sacrifice|mill|scry|gain|lose|discard|shuffle|deal|prevent|regenerate|"
                              r"detain|goad|attach|copy|remove|choose|cast|play)\b", re.I)


def _leading_subject(part: str):
    """The player/permanent subject NP a clause opens with ('Each player', 'Target opponent'), or None.
    Returns None when the part is an IMPERATIVE verb phrase ('Exile target creature …', 'Tap all lands
    …'): there the leading words are verb+object, not a subject, so the _TGT regex would wrongly capture
    'Exile target creature' as a subject and reattach it to a sibling imperative — swallowing it into one
    lossy target. Sibling imperatives ('Exile X, then return it') must each parse standalone instead."""
    if _IMPERATIVE_LEAD.match(part):
        return None
    m = _SUBJ_RE.match(part)
    return m.group(1) if m else None


def _has_leading_subject(part: str) -> bool:
    """True if the part already starts with its own subject (so it doesn't need one reattached) — i.e.
    it does NOT start with a bare 3rd-person predicate verb."""
    return not _BARE_PRED.match(part.strip())


_WRAPPER = re.compile(r"^(?P<w>if you do|you may|if (?!you do\b)[^,]+?|until (?:end of turn|your next turn|end of combat)),?\s+(?P<rest>.+)$", re.I)
# multidamage consequent under an 'if <cond>,' wrapper, where the condition itself contains commas the
# minimal [^,]+? would stop short of ('If there are seven or more cards …, instead ~ deals N … and M
# damage to …'). Greedy-match the condition up to the LAST ', ' that precedes a damage-source subject.
_IF_MULTIDMG = re.compile(r"^if (?!you do\b)(?P<cond>.+), (?P<rest>(?:instead,?\s+)?"
                          r"(?:~|it|he|she|they|that \w+|this \w+|[A-Z][\w']+) deals (?:\d+|X) damage to .+)$", re.I)


def _peel_wrapper(sentence):
    """A leading clause-wrapper over a COMPOUND consequent ('If you do, draw a card and you gain 2
    life'; 'You may exile X and draw Y') -> (cond, rest). Single-consequent wrappers are handled in
    parse_clause; this catches the compound case the body splitter must expand. Returns None if no
    wrapper or the consequent isn't actually compound."""
    # 'if <comma-laden cond>, <multidamage>' — the minimal [^,]+? condition in _WRAPPER stops short of a
    # comma inside the condition, so match the multidamage form greedily first (condition up to the last
    # ', ' before a damage clause). Faithful: the consequent must be a real multidamage to qualify.
    mi = _IF_MULTIDMG.match(sentence)
    if mi and _multi_damage(mi.group("rest")):
        return ground.slug(mi.group("cond")), mi.group("rest")
    m = _WRAPPER.match(sentence)
    # the consequent counts as COMPOUND when the effect-splitter yields >1 part, OR it is a multi-
    # recipient damage clause ('… instead ~ deals N to A and M damage to B') that _smart_split leaves
    # whole (the 'and' is followed by a number) but _multi_damage fans out, OR it is a compound buff
    # ('it gains trample and gets +X/+X …') that only parse_clauses (via _eot_compound) splits — all of
    # which a single inner parse_clause would otherwise swallow into one lossy effect.
    if not m:
        return None
    rest = m.group("rest")
    pc = parse_clauses(rest)
    if len(_smart_split(rest)) < 2 and not _multi_damage(rest) and not (pc and len(pc) > 1):
        return None
    w = m.group("w").lower()
    if w == "if you do":
        cond = "if_you_did"
    elif w == "you may":
        cond = "may"
    elif w.startswith("until "):
        cond = "until_" + ground.slug(w[6:])
    else:
        cond = ground.slug(w[3:])
    return cond, m.group("rest")


# Non-executable §613.1c persistence reminders: they clarify that a continuous effect that grants/sets
# a characteristic does NOT cause its own source (an Aura) to fall off — they carry no executable effect
# of their own, so the body splitter drops them rather than abstaining. Kept TIGHT (this exact reminder
# family only) so real constraint clauses ('this effect reduces only colored mana', 'can't reduce below
# one') still abstain — those carry simulation-relevant information.
_CLARIFICATION = re.compile(r"^This effect doesn't remove (?:~|it|[\w' -]+?)$", re.I)


_COMMA_LIST_SPLIT = re.compile(r",\s+(?=(?:put|reveal|draw|mill|discard|gain|lose|exile|destroy|create|"
                               r"tap|untap|sacrifice|return|scry|shuffle|search|counter|copy|prevent|"
                               r"regenerate|goad|detain|attach|cast|play|surveil|investigate|proliferate|"
                               r"populate|venture|amass|connive|explore|fight|then)s?\s)", re.I)


def _comma_resplit(part, subj):
    """Safely split a comma-list of predicates ('discards X, mills Y') into grounded effects: split on
    ', ' before a known verb, parse each (reattaching `subj` when given). All-or-nothing — returns the
    effect list only if EVERY sub-part grounds, else None, so it can never regress a passing card."""
    bits = _COMMA_LIST_SPLIT.split(part)
    if len(bits) < 2:
        return None
    out = []
    for i, b in enumerate(bits):
        b = b.strip()
        e = parse_clause(f"{subj} {b}") if (subj and i > 0) else parse_clause(b)
        if not e:
            return None
        out.append(e)
    return out


def _parse_body(text: str):
    """A clause body -> list[Effect], requiring EVERY sub-effect to parse (else None — no half facts).
    Splits on sentence boundaries and simple 'and'/'then' conjunctions (quote-safe); else abstains."""
    out = []
    for sentence in _sentences(text):
        sentence = sentence.rstrip(".")
        if not sentence:
            continue
        if _CLARIFICATION.match(sentence):           # non-executable §613 persistence reminder — carries
            continue                                 # no effect, so skip it (drop, never abstain on it)
        # ability-word prefix (§207.2c, no rules meaning) on an effect clause: 'Ferocious — <effect>'.
        # Strip it when the remainder parses AND the whole sentence either doesn't parse OR parses only
        # as a single (possibly swallowed) effect while the stripped body fans out into MORE effects —
        # the latter recovers ability-worded multidamage ('Threshold — … instead ~ deals N to A and M
        # damage to B'), where the un-stripped whole-parse greedily swallows the second recipient.
        aw = re.match(r"^[A-Z][\w'/-]*(?: [A-Z'][\w'/-]*)* [—–] (.+)$", sentence)
        if aw:
            stripped = _parse_body(aw.group(1))
            if stripped and (not parse_clause(sentence) or len(stripped) > 1):
                out.extend(stripped)
                continue
        peeled = _peel_wrapper(sentence)             # 'If you do, <compound>' / 'You may <compound>'
        if peeled:
            cond, rest = peeled
            sub = _parse_body(rest)
            if sub:
                out.extend(dataclasses.replace(e, cond=(cond if e.cond == "-" else f"{cond}__{e.cond}"))
                           for e in sub)
                continue
        # multi-recipient damage ('deals N to A and M damage to B/you/itself') is a swallow the
        # whole-parse would NOT flag as a compound object (the 'and' is followed by a number, not a
        # verb), so split it into per-recipient deal_damage BEFORE accepting the whole-parse below.
        dmg = _multi_damage(sentence)
        if dmg:
            out.extend(dmg)
            continue
        sv = _split_same_verb_objects(sentence)   # 'Destroy that creature and ~' -> two destroy effects
        if sv:
            out.extend(sv)
            continue
        multi = parse_clauses(sentence)
        # A whole-sentence GRANT of a single QUOTED ability ('<who> gain(s) "…"', 'You get an emblem
        # with "…"') is complete as-is: the quoted ability is slugged WHOLE, so the ':'/'and'/'then' the
        # run-on detector sees lives INSIDE the quote — splitting would corrupt it. Trust the whole-parse
        # here (faithful, never a swallow) before the _is_compound_object guard rejects it for that inner
        # punctuation. Gated on a single effect whose verb is the quote-bearing grant/emblem producer.
        if multi and len(multi) == 1 and multi[0].verb in ("grant_ability", "get_emblem") and '"' in sentence:
            out.extend(multi)
            continue
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
                    resplit = _comma_resplit(p, subj if (subj and not _has_leading_subject(p)) else None)
                    if resplit:                          # 'discards X, mills Y' -> two grounded effects
                        sub.extend(resplit)
                        continue
                    ok = False
                    break
                sub.append(e)
            if ok:
                out.extend(sub)
                continue
        # fall back to the whole-clause parse — BUT only when it is NOT a swallowed run-on. If the
        # sentence runs on into a second effect (_is_compound_object) and the split failed to ground
        # every part, the single whole-parse has greedily absorbed the continuation into its target (a
        # lossy swallow). Abstain on that part rather than emit the garbage slug (faithful-or-abstain);
        # the later splitters / a final `return None` then handle it.
        if multi and not _is_compound_object(sentence):
            out.extend(multi)
            continue
        dist = _distribute_subjects(sentence)   # '<A> and <B> [each] <predicate>' -> effect on each
        if dist:
            out.extend(dist)
            continue
        dep = _spacy_effect(sentence)           # LAST RESORT: dependency-graph verb+object extraction
        if dep:
            out.append(dep)
            continue
        return None
    return out or None


# Two+ damage segments each of form 'N damage to <recipient>', joined by ',' and/or ' and '. The
# required second 'N damage to' is what distinguishes this from the LEGITIMATE combined target
# '~ deals N damage to each creature and each player' (one amount, one 'damage to', a single recipient
# set) — there the ' and ' joins recipients inside ONE segment, so this pattern does not match it.
# a damage-segment amount: a bare number/X, the same as an 'additional' instance ('an additional 1', '1
# additional'), or 'that much' (a §120 back-reference). 'additional'/'an additional' is English glue marking
# a SECOND damage instance and is dropped before the segment is parsed — each instance is its own deal_damage.
_DAMT = r"(?:an additional \d+|\d+ additional|\d+|x|that much)"
_MULTI_DMG = re.compile(rf"^(?:instead,?\s+)?(?P<src>~|it|he|she|they|that \w+|this \w+|[A-Z][\w']+(?:,? [A-Z][\w']+)*) deals "
                        rf"(?P<segs>{_DAMT} damage to .+?(?:,|,? and) {_DAMT} damage to .+)$", re.I)


def _multi_damage(sentence):
    """'<source> deals N damage to A, M damage to B[, and K damage to C]' (Arc Lightning / Fiery
    Cannonade family) and the two-recipient differing-amount form '<source> deals N damage to A and M
    damage to B/you/itself' (Orcish Artillery / Psionic Blast / Chandra's Outrage) -> one grounded
    deal_damage per recipient. All-or-nothing: every segment must ground or it abstains. Does NOT touch
    the combined-target '… to each creature and each player' shape (no second 'N damage to')."""
    m = _MULTI_DMG.match(sentence)
    if not m:
        return None
    src, segs = m.group("src"), m.group("segs")
    out = []
    # split before each 'N damage to' on a ',' or ' and ' boundary (keeps recipient-internal ' and '
    # such as 'target player or planeswalker' or 'each creature and each player' inside one segment).
    for seg in re.split(rf",\s+(?:and\s+)?|\s+and\s+(?={_DAMT} damage to )", segs):
        seg = seg.strip()
        if not re.match(rf"^{_DAMT} damage to ", seg, re.I):
            return None
        seg = re.sub(r"^an additional ", "", seg, flags=re.I)          # 'an additional 1 damage to' -> '1 damage to'
        seg = re.sub(r"^(\d+|x) additional ", r"\1 ", seg, flags=re.I)  # '1 additional damage to'   -> '1 damage to'
        e = parse_clause(f"{src} deals {seg}")
        if not e:
            return None
        out.append(e)
    return out


# a single imperative verb over TWO distinct individual objects joined by 'and' — 'Destroy that creature
# and ~', 'Exile it and that artifact'. The second object must be a clearly-individual reference (~/it/
# that-NP), NOT a type plural ('artifacts and enchantments') which is one combined destroy target. Splits
# into one effect per object (all-or-nothing). A trailing timing tail ('at end of combat') is shared.
_SAME_VERB_OBJS = re.compile(
    r"^(?P<verb>destroy|exile|sacrifice|tap|untap|return) (?P<a>.+?) and "
    r"(?P<b>~|it|that [\w' -]+?)(?P<tail> (?:at end of combat|this turn|this combat))?$", re.I)
# 'return <A> and <B> to <destination> [tail]' (Contempt 'return it and ~ to their owners' hands …') —
# the destination phrase sits between objects and would otherwise be swallowed; distribute it to each.
_RETURN_OBJS = re.compile(
    r"^return (?P<a>.+?) and (?P<b>~|it|that [\w' -]+?) (?P<dest>to [\w' -]+? (?:hand|hands|battlefield|"
    r"graveyard|library))(?P<tail> (?:at end of combat|this turn|this combat))?$", re.I)


def _split_same_verb_objects(sentence):
    r = _RETURN_OBJS.match(sentence)
    if r:
        tail = r.group("tail") or ""
        e1 = parse_clause(f"return {r.group('a')} {r.group('dest')}{tail}")
        e2 = parse_clause(f"return {r.group('b')} {r.group('dest')}{tail}")
        return [e1, e2] if (e1 and e2) else None
    a = _SAME_VERB_OBJS.match(sentence)
    if not a:
        return None
    verb, tail = a.group("verb"), a.group("tail") or ""
    e1 = parse_clause(f"{verb} {a.group('a')}{tail}")
    e2 = parse_clause(f"{verb} {a.group('b')}{tail}")
    return [e1, e2] if (e1 and e2) else None


_DIST_SUBJ = re.compile(rf"^({_TGT}) and ((?:up to \w+ other |another |[\w' -]+? )?{_TGT}) (?:each )?"
                        r"(gains?|gets?|haves?|has|deals?|becomes?|are|is|can't|attacks?|blocks?|"
                        r"phases?|fight|fights|don't|doesn't|draws?|mills?|discards?|creates?|"
                        r"sacrifices?|scry|scries|loses?) (.+)$", re.I)


def _distribute_subjects(sentence):
    """'<A> and <B> [each] <predicate>' (two subjects sharing one effect, e.g. 'it and Zombies you
    control gain deathtouch') -> the predicate parsed once per subject. All-or-nothing: both must
    ground or it abstains, so it never regresses a passing card."""
    m = _DIST_SUBJ.match(sentence)
    if not m:
        return None
    verb, rest = m.group(3), m.group(4)
    a = parse_clause(f"{m.group(1)} {verb} {rest}")
    b = parse_clause(f"{m.group(2)} {verb} {rest}")
    return [a, b] if (a and b) else None


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


# §305.6 land-type VOCABULARY — the grounded roster of basic land types (plus the typeless 'Wastes',
# which this KB normalizes alongside them). A §305.7 land-type-changing static (`land_type_set`) is
# justified ONLY when its result set grounds ENTIRELY in this roster: the copula + a closed-roster
# result is the anchor, NOT the English shape. 'All creatures are black' / 'Enchanted land is the
# chosen type' have no roster-grounded result and so abstain.
_LAND_TYPE_NORM = {
    "plains": "plains", "plain": "plains",
    "island": "island", "islands": "island",
    "swamp": "swamp", "swamps": "swamp",
    "mountain": "mountain", "mountains": "mountain",
    "forest": "forest", "forests": "forest",
    "wastes": "wastes", "waste": "wastes",
}
# The §305.7 copula that joins a land scope to its new type(s). Grounding the relation requires BOTH
# this copula AND a fully roster-grounded result — neither alone, and never the surface template.
_LAND_TYPE_COPULA = frozenset({"is", "are", "becomes", "become"})
# Closed scope roster: each recognized land SCOPE NP maps to its grounded scope token. ('all <X>' is
# handled separately because <X> must itself ground as a land type to be an all-of-a-type scope.)
_LAND_SCOPE_NORM = {
    "each land": "each_land",
    "lands you control": "lands_you_control",
    "enchanted land": "enchanted_land",
    "~": "self",
    "lands": "all_lands",
}
_LANDTYPE_SET = re.compile(
    r"^(?P<subj>Nonbasic lands?|All [A-Za-z]+|Each land|Lands you control|Enchanted land|~|Lands)"
    r" (?P<copula>are|is) (?P<types>.+?)"
    r"(?P<add> in addition to (?:its|their) other(?: land)? types)?\.?$", re.I)


def _land_type_set(unit, ctx):
    """'<lands> are/is <basic type(s)> [in addition to their other types].' — a §305.7 land
    type-changing static (Blood Moon, Conversion, Yavimaya, Celestial Dawn, Lush Growth). Emits one
    land_type_set(cid, scope, type, mode) per resulting basic type. Abstains unless the scope is
    a recognized land set AND every result is a basic land type — so 'All creatures are black' or
    'Enchanted land is the chosen type' fall through rather than mint a bogus land-type fact.

    GROUNDED-PREDICATE form: the `land_type_set` relation is justified by the §305.7 anchor — the
    copula (_LAND_TYPE_COPULA) joining a recognized land SCOPE to a result set that grounds ENTIRELY
    in the closed §305.6 land-type roster (_LAND_TYPE_NORM). The regex only EXTRACTS the scope/result
    spans and the `in addition to` mode marker; it no longer selects the predicate by English shape."""
    m = _LANDTYPE_SET.match(unit.raw)
    if not m:
        return None
    # ground the copula against the §305.7 type-changing connective (defensive — regex constrains it).
    if m.group("copula").lower() not in _LAND_TYPE_COPULA:
        return None
    subj = m.group("subj").lower()
    if subj.startswith("nonbasic land"):
        scope = "nonbasic_lands"
    elif subj in _LAND_SCOPE_NORM:
        scope = _LAND_SCOPE_NORM[subj]
    elif subj.startswith("all "):
        rest = subj[4:]
        if rest == "lands":
            scope = "all_lands"
        else:
            t = _LAND_TYPE_NORM.get(rest)              # 'all <land type>' grounds only if <X> is a
            if not t:                                  # closed §305.6 land type; 'All creatures' /
                return None                            # 'All Slivers' have no roster-grounded scope.
            scope = "all_" + t
    else:
        return None
    raw_types = re.sub(r"\b(?:a|an)\s+", "", m.group("types"), flags=re.I)
    raw_types = re.sub(r",?\s+and\s+", ", ", raw_types)        # 'A, B, and C' / 'A and B' -> comma list
    parts = [p.strip().rstrip(".").lower() for p in raw_types.split(",") if p.strip()]
    types = [_LAND_TYPE_NORM.get(t) for t in parts]
    if not types or not all(types):               # any non-basic-type result => not this static
        return None
    mode = "additional" if m.group("add") else "replace"
    cid = ctx["id"]
    facts = [f'land_type_set("{cid}", "{scope}", "{t}", "{mode}")' for t in dict.fromkeys(types)]
    return CardOut(cid, facts, "land_type_set")


# §614 REPLACEMENT-EFFECT VOCABULARY — the grounded anchor word that licenses a §614 "… would …,
# … instead" replacement static. A replacement effect is defined (§614.1) by stating that some event
# happens *otherwise than it normally would*, signalled by the word `instead`. Each §614 family below
# (damage-redirect, damage-multiply, life-floor) is justified by THIS anchor joined to a grounded
# REPLACED-QUANTITY token — not by which English template fired. `_INSTEAD` membership + the family's
# replaced-quantity grounding is what licenses the relation.
_INSTEAD = "instead"
# The §614 quantity a damage-redirect replaces is the damage DESTINATION ('dealt to <A>' → 'dealt to
# <B>'): the grounded copula that re-routes the same damage to a new recipient.
_REDIRECT_COPULA = frozenset({"is dealt to", "are dealt to"})

_DMG_REDIRECT = re.compile(
    r"^All damage that would be dealt to (?P<from>~|[\w' ]+?) "
    r"(?P<copula>is dealt to|are dealt to) (?P<to>~|[\w' ]+?) (?P<instead>instead)\.?$", re.I)


def _damage_redirect(unit, ctx):
    """'All damage that would be dealt to <A> is dealt to <B> instead.' — a §614 damage-redirection
    replacement static (Pariah, Pariah's Shield, Treacherous Link, Empyrial Archangel). The one-shot
    '… this turn …' version is a spell (handled by _spell); this is the permanent/static form.

    GROUNDED-PREDICATE form: the `damage_redirect` relation is justified by the §614 anchor — the
    `instead` replacement word (`_INSTEAD`) joining a damage event to a re-routing copula
    (`_REDIRECT_COPULA`) that names a new DESTINATION. The regex only EXTRACTS the from/to spans and
    the anchor tokens; it abstains unless both the copula and `instead` ground, so the predicate is
    licensed by §614 vocabulary, not by the surface 'All damage …' template."""
    if "this turn" in unit.raw.lower():
        return None
    m = _DMG_REDIRECT.match(unit.raw)
    if not m:
        return None
    # ground the §614 anchor: the redirect copula + the `instead` replacement word license the
    # predicate (defensive — the regex already constrains both; abstain rather than mint un-anchored).
    if m.group("copula").lower() not in _REDIRECT_COPULA or m.group("instead").lower() != _INSTEAD:
        return None
    def ref(s):
        s = s.strip()
        return "self" if s in ("~", "it") else ground.slug(s)
    frm, to = ref(m.group("from")), ref(m.group("to"))
    if not frm or not to:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'damage_redirect("{cid}", "{frm}", "{to}")'], "damage_redirect")


# §616 damage-MULTIPLICATION VOCABULARY — the grounded factor roster that licenses a `damage_multiplier`
# relation. The §614/616 replacement here replaces the AMOUNT of damage: the same source deals a
# multiple of what it would. `_FACTOR` is the closed multiplication vocabulary; the relation is
# justified by a factor word grounding in this roster (joined to the §614 `instead` anchor in the
# replacement form), NOT by the 'If … would …' template. A non-{double,twice,triple} factor abstains.
_FACTOR = {"double": "2", "twice": "2", "triple": "3"}
_DMG_MULT_A = re.compile(
    r"^If (?P<src>.+?) would deal (?:combat |noncombat )?damage(?P<tgt> to [^,]+?)?, "
    r"(?:it|that source|that creature|that spell) deals (?P<factor>double|triple|twice) "
    r"that (?:damage|much damage)(?: to [^,.]+?)? (?P<instead>instead)\.?$", re.I)
_DMG_MULT_B = re.compile(r"^(?P<factor>Double|Triple) all damage (?P<src>.+?) would deal\.?$", re.I)


def _damage_multiplier(unit, ctx):
    """Damage-multiplication replacement statics (§614/616): 'If <source> would deal damage [to <X>],
    it deals double/triple that damage instead' (Furnace of Rath, Gratuitous Violence, Fiery
    Emancipation, Gisela, Obosh) and 'Double/Triple all damage <X> would deal' (Mjölnir, Collective
    Inferno). Emits damage_multiplier(cid, source, factor, target). Anchored at ^If/^Double/^Triple
    so the ability-word/temporary wrappers (Hellbent/Delirium —, 'until your next turn') fall through;
    'this turn'/'until' temporary versions abstain (they're one-shots), as do non-2/3 factors.

    GROUNDED-PREDICATE form: the `damage_multiplier` relation is justified by the §616 multiplication
    FACTOR vocabulary — the captured factor word must ground in `_FACTOR` (and, in the replacement
    form A, be joined to the §614 `instead` anchor). The regexes only EXTRACT the source/target spans
    and the factor token; the predicate is licensed by factor membership, not by the English shape."""
    r = unit.raw
    # abstain on any temporary duration or embedded condition — a 'while/as long as' clause would be
    # swallowed into the target slug (Rollercrusher's Delirium 'while there are four or more card
    # types …'), and 'this turn'/'until' versions are one-shots, not permanent multiplier statics.
    if re.search(r"\bthis turn\b|\buntil\b|\bwhile\b|\bas long as\b", r, re.I):
        return None
    cid = ctx["id"]
    m = _DMG_MULT_A.match(r)
    if m:
        factor = m.group("factor").lower()
        # ground the §616 factor + the §614 `instead` replacement anchor — both license the predicate.
        if factor not in _FACTOR or m.group("instead").lower() != _INSTEAD:
            return None
        src = ground.slug(m.group("src"))
        tgt = ground.slug(m.group("tgt")[4:]) if m.group("tgt") else "-"
        if src and tgt:
            return CardOut(cid, [f'damage_multiplier("{cid}", "{src}", {_FACTOR[factor]}, "{tgt}")'],
                           "damage_multiplier")
        return None
    m = _DMG_MULT_B.match(r)
    if m:
        factor = m.group("factor").lower()
        if factor not in _FACTOR:                  # grounded factor licenses the predicate (B form has
            return None                            # no `instead`: the leading factor IS the §616 anchor)
        src = ground.slug(m.group("src"))
        if src:
            return CardOut(cid, [f'damage_multiplier("{cid}", "{src}", {_FACTOR[factor]}, "-")'],
                           "damage_multiplier")
    return None


# §614 life-total-FLOOR VOCABULARY — the grounded reduction verb that, joined to the `instead`
# replacement word, licenses a `life_floor` relation. The §614 replacement here replaces the AMOUNT of
# a life-total reduction (it would drop below N, but is floored AT N). `_FLOOR_VERB` is the grounded
# §119/§614 'reduce(s)' connective on the life total; the relation is justified by this verb + the
# `instead` anchor, NOT by the surface 'Damage that would …' template.
_FLOOR_VERB = frozenset({"reduces", "reduce"})

_LIFE_FLOOR = re.compile(
    r"^(?:(?P<cond>If .+?|As long as .+?), )?damage that would reduce your life total to less than "
    r"\d+ (?P<verb>reduces|reduce) it to (?P<floor>\d+) (?P<instead>instead)\.?$", re.I)


def _life_floor(unit, ctx):
    """'[<cond>, ]Damage that would reduce your life total to less than N reduces it to N instead.' —
    a §614 life-total floor replacement (Ali from Cairo, Fortune Thief, Sustaining Spirit, and the
    conditional Worship / Elderscale Wurm). The 'until end of turn' / triggered forms start with
    When/Until and don't match this static anchor.

    GROUNDED-PREDICATE form: the `life_floor` relation is justified by the §614 anchor — the grounded
    life-total reduction verb (`_FLOOR_VERB`) joined to the `instead` replacement word (`_INSTEAD`).
    The regex only EXTRACTS the optional condition span and the floor amount; it abstains unless both
    the verb and `instead` ground, so the predicate is licensed by §614 vocabulary, not the template."""
    m = _LIFE_FLOOR.match(unit.raw)
    if not m:
        return None
    # ground the §614 anchor: the reduction verb + the `instead` replacement word license the predicate
    # (defensive — the regex already constrains both; abstain rather than mint an un-anchored floor).
    if m.group("verb").lower() not in _FLOOR_VERB or m.group("instead").lower() != _INSTEAD:
        return None
    cond = ground.slug(m.group("cond")) if m.group("cond") else "-"
    if not cond:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'life_floor("{cid}", {m.group("floor")}, "{cond}")'], "life_floor")


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


_PREVENT_LINE = re.compile(r"^(?:(during your turn|during combat),\s+)?(prevent .+)$", re.I)


def _prevent_static(unit, ctx):
    """A bare single-sentence damage-prevention static ability (§615) — 'Prevent all combat damage that
    would be dealt to ~.', 'Prevent all damage target creature would deal this turn.', 'During your
    turn, prevent all damage that would be dealt to ~.'  These never reach `_static_effect` because its
    'would'/replacement guard (rightly) rejects them, so this routes the prevent clause through
    parse_clause directly (all-or-nothing). A leading 'During your turn,'/'During combat,' temporal
    prefix is recorded as the effect's condition (the established `during_your_turn` convention)."""
    if {"Instant", "Sorcery"} & _types(ctx):
        return None                               # one-shots are _spell's job
    if re.match(r"^(?:When|Whenever|At|If|As)\b", unit.raw, re.I) or ":" in unit.raw or '"' in unit.raw:
        return None                               # triggered/activated/quoted/conditional — not a bare static
    m = _PREVENT_LINE.match(unit.raw.rstrip("."))
    if not m or "." in m.group(2):                # single sentence only (multi-clause -> abstain here)
        return None
    e = parse_clause(m.group(2))
    if e is None or e.verb != "prevent_damage":
        return None
    if m.group(1):
        cond = ground.slug(m.group(1))
        e = dataclasses.replace(e, cond=cond if e.cond == "-" else f"{cond}__{e.cond}")
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    return CardOut(cid, [f'card_ability("{cid}", "{aid}", "static")']
                   + _effect_facts(cid, aid, [e]), "static_effect")


def _activated(unit, ctx):
    """'<cost>: <effect(s)>' — an activated ability (§602). Cost must look like a cost; effects parse."""
    m = re.match(r"^(?P<cost>[^:]{1,60}):\s*(?P<body>.+)$", unit.raw)
    if not m or not _cost_ok(m.group("cost")):
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    cost_facts = [f'card_ability("{cid}", "{aid}", "activated")',
                  f'ability_cost("{cid}", "{aid}", "{m.group("cost").strip()}")']
    mh = _MODAL_HEAD.match(m.group("body"))
    if mh:
        return CardOut(cid, cost_facts + [f'modal("{cid}", "{ground.slug(mh.group(1))}")'], "activated")
    body, mods = _split_modifiers(m.group("body"))
    effects = _parse_body(body) if body else None
    if not effects:
        return None
    cost_facts += [f'ability_modifier("{cid}", "{aid}", "{t}")' for t in mods]
    return CardOut(cid, cost_facts + _effect_facts(cid, aid, effects), "activated")


_REPL = re.compile(r"^If (?P<cond>.+? would .+?), (?P<repl>.+?) instead\.?$", re.I | re.S)


def _replacement(unit, ctx):
    """'If <X> would <event>, <replacement> instead.' — a §614 replacement effect. The replaced event
    is recorded as a descriptive slug (like a trigger condition) on a 'replacement'-kind ability, and
    the replacement body must parse into grounded effects (else abstain). Quantitative replacements
    ('… twice that many …', '… plus N …') don't ground and so faithfully fall through to abstention."""
    m = _REPL.match(unit.raw)
    if not m:
        # §615 prevention/regeneration replacements don't use 'instead' ('If damage would be dealt to ~,
        # prevent that damage[. …].' / 'If ~ would be destroyed, regenerate it.'); the replacement body
        # begins with 'prevent' or 'regenerate'.
        m = re.match(r"^If (?P<cond>.+? would .+?), (?P<repl>(?:prevent|regenerate) .+?)\.?$", unit.raw, re.I | re.S)
    if not m or '"' in unit.raw:
        return None
    effects = _parse_body(m.group("repl"))
    if not effects:
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    ev = ground.slug(m.group("cond"))
    head = [f'card_ability("{cid}", "{aid}", "replacement")',
            f'ability_trigger("{cid}", "{aid}", "{ev}")']
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
    # §603 SELF-REFERENCE — 'When <this card's short name> enters/dies/attacks' refers to the source
    # itself (the oracle uses the legend's short name where rules text would use ~). Normalize the short
    # name to ~ in the TRIGGER subject so it slugs to the self event ('enters'), not a name-keyed phrase
    # ('katara_enters'). Collision-guarded by _short_name (won't fire for a name that is a rules word).
    trig_raw = m.group("trig")
    short = _short_name(ctx.get("card") or {})
    if short and re.search(r"\b" + re.escape(short) + r"\b", trig_raw):
        trig_raw = re.sub(r"\b" + re.escape(short) + r"(?:'s)?\b",
                          lambda mm: "~'s" if mm.group(0).endswith("'s") else "~", trig_raw)
    trig = ground.slug(trig_raw)
    head = [f'card_ability("{cid}", "{aid}", "triggered")', f'ability_trigger("{cid}", "{aid}", "{trig}")']
    mh = _MODAL_HEAD.match(m.group("body"))
    if mh:
        return CardOut(cid, head + [f'modal("{cid}", "{ground.slug(mh.group(1))}")'], "triggered")
    body, mods = _split_modifiers(m.group("body"))
    effects = _parse_body(body) if body else None
    if not effects:
        return None
    head += [f'ability_modifier("{cid}", "{aid}", "{t}")' for t in mods]
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
    facts = [f'card_ability("{cid}", "{aid}", "loyalty")', f'ability_cost("{cid}", "{aid}", "{cost}")']
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
    facts = [f'card_ability("{cid}", "{aid}", "saga_chapter")']
    for ch in m.group("ch").split(", "):
        if ch in _ROMAN:
            facts.append(f'ability_trigger("{cid}", "{aid}", "chapter_{_ROMAN[ch]}")')
    return CardOut(cid, facts + _effect_facts(cid, aid, effects), "saga_chapter")


def _etb_choose(unit, ctx):
    """'As ~ enters, choose a <X>.' — an as-enters choice replacement (§614.12/§603.6e).

    Two shapes:
      - CATEGORY: 'choose a color' / 'choose a basic land type' -> etb_choose(cid, category)
      - EXPLICIT options: 'choose Khans or Dragons' / 'choose odd or even' /
        'choose Elemental, Elf, ..., or Treefolk' -> one etb_choose_option(cid, opt) each.
    The controller makes the choice; only this "(~|it) enters, choose ..." subject is handled (the
    'each player chooses' / 'an opponent chooses' / 'secretly choose' variants have a different
    chooser and are left to abstain rather than mis-attribute who decides)."""
    cid = ctx["id"]
    m = re.match(r"^As (?:~|it) enters, choose (?:a|an) (.+?)\.?$", unit.raw, re.I)
    if m:
        return CardOut(cid, [f'etb_choose("{cid}", "{ground.slug(m.group(1))}")'], "etb_choose")
    m = re.match(r"^As (?:~|it) enters, choose (?P<opts>.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    opts_src = m.group("opts")
    # only the "choose X or Y[, ...]" mode-pick is an explicit option set. "choose two abilities
    # from among A, B, and C" is a DIFFERENT mechanic (choose-N, and-joined) — abstain on it and
    # on any and-joined list rather than mangle the options into wrong slugs.
    if "from among" in opts_src.lower() or re.search(r"\band\b", opts_src, re.I):
        return None
    # normalize "A or B" / "A, B, or C" / "A, B or C" to a comma list, then split.
    opts_raw = re.sub(r",?\s+or\s+", ", ", opts_src)
    opts = [o.strip() for o in opts_raw.split(",") if o.strip()]
    if len(opts) < 2:                       # not an enumerated choice (e.g. 'choose two colors')
        return None
    slugs = [ground.slug(o) for o in opts]
    if not all(slugs):                      # every option must ground to a clean slug, else abstain
        return None
    return CardOut(cid, [f'etb_choose_option("{cid}", "{s}")' for s in slugs], "etb_choose")


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
                         f'ability_trigger("{cid}", "{aid}", "enters")']
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
    (r"^You may play (?:an? )?additional lands? (?:on|during) each of your turns\.?$", "extra_land_per_turn"),
    (r"^Each player may play (?:an? )?additional lands? (?:on|during) each of their turns\.?$", "each_player_extra_land_per_turn"),
    (r"^Players may play (?:an? )?additional lands? (?:on|during) each (?:of their turns|turn)\.?$", "each_player_extra_land_per_turn"),
    (r"^You may look at the top card of your library any time\.?$", "look_at_top_card"),
    (r"^You may choose not to untap ~ during your untap step\.?$", "may_skip_untap"),
    (r"^Players skip their untap steps?\.?$", "players_skip_untap_step"),
    (r"^Players can't untap during their untap steps?\.?$", "players_cant_untap"),
    (r"^You play with your hand revealed\.?$", "you_play_hand_revealed"),
    (r"^Your opponents play with their hands revealed\.?$", "opponents_play_hands_revealed"),
    (r"^Players play with their hands revealed\.?$", "players_play_hands_revealed"),
    (r"^Each player can't cast more than one spell each turn\.?$", "each_player_one_spell_per_turn"),
    (r"^You can't lose the game and your opponents can't win the game\.?$", "cant_lose_opponents_cant_win"),
]


def _static_player(unit, ctx):
    cid, r = ctx["id"], unit.raw

    def mk(tag):
        return CardOut(cid, [f'static_player("{cid}", "{tag}")'], "static_player")

    for pat, tag in _STATIC_PLAYER:
        if re.match(pat, r, re.I):
            return mk(tag)
    # N-additional-lands permission (§505.5b/§116.2a) — Azusa's 'play two additional lands'. Capture the
    # count faithfully (a bare 'extra_land_per_turn' would understate it).
    m = re.match(r"^You may play (\w+) additional lands? (?:on|during) each of your turns\.?$", r, re.I)
    if m and m.group(1).lower() not in ("an", "a"):
        return mk("extra_lands_per_turn_" + ground.slug(m.group(1)))
    # §502.3 untap-step limit: 'Players can't untap more than one <thing> during their untap steps'
    # (Smoke, Damping Field, Winter Moon), optionally gated by 'As long as ~ is untapped,' (Winter Orb).
    m = re.match(r"^(?:As long as ~ is untapped, )?[Pp]layers can't untap more than one (.+?) "
                 r"during their untap steps?\.?$", r, re.I)
    if m:
        return mk("players_cant_untap_more_than_one_" + ground.slug(m.group(1)))
    # capturing permission/restriction statics (§116/§118/§601) — the scope is a descriptive slug.
    m = re.match(r"^You can't cast (.+?)\.?$", r, re.I)
    if m:
        return mk("cant_cast_" + ground.slug(m.group(1)))
    # a leading frequency/timing prefix on a casting-permission static (§116/§601): 'Once during each of
    # your turns, …', 'During each of your turns, …', 'During your turn, …'. Peel it, record it as a
    # suffix on the permission slug, and re-match the 'may cast/play … from <zone>' families below. Only
    # when the remainder is a SINGLE sentence — a trailing second sentence (Kess/Edgar's 'If a spell cast
    # this way …') carries its own effect, so we abstain there rather than silently swallow it.
    freq, body = "", r
    mp = re.match(r"^(Once during each of your turns?|During each of your turns?|During your turn),\s+(.+)$", r, re.I)
    if mp and ". " not in mp.group(2).rstrip("."):
        freq = {"once": "_once_per_turn"}.get(mp.group(1).split()[0].lower(), "_during_your_turn")
        body = mp.group(2)
        # the zone phrase must END the sentence — a trailing 'by paying/sacrificing … in addition to
        # their costs' (Festival/Maestros) or 'and mana of any type can be spent …' (Tinybones) carries a
        # real alternative-cost/extra clause we won't drop, so those abstain rather than ground lossily.
        m = re.match(r"^You may cast (.+?) from (the top of your library|your graveyard|exile|among them)\.?$", body, re.I)
        if m:
            return mk("may_cast_" + ground.slug(m.group(1)) + "_from_" + ground.slug(m.group(2)) + freq)
        m = re.match(r"^You may play (.+?) from (the top of your library|your graveyard|exile)\.?$", body, re.I)
        if m:
            return mk("may_play_" + ground.slug(m.group(1)) + "_from_" + ground.slug(m.group(2)) + freq)
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
    # saddle/crew power bonus (§702.176/§702.122 — power counts as N greater for the keyword cost)
    (r"^~ saddles Mounts and crews Vehicles as though its power were (\d+) greater\.?$",
     "saddles_crews_as_though_power_greater_by_"),
    # §202.3a 'spend only … mana on X' — a colored-mana spend restriction on the X cost
    (r"^Spend only (.+? mana on X)\.?$", "restriction_spend_only_"),
    # §605/§302.6 pseudo-haste for activated abilities (Thousand-Year Elixir, Tyvar)
    (r"^You may activate abilities of creatures you control as though those creatures had haste\.?$",
     "activate_creature_abilities_as_though_haste"),
    (r"^You may activate abilities of other creatures you control as though those creatures had haste\.?$",
     "activate_other_creature_abilities_as_though_haste"),
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
    (r"^X can't be (\d+)\.?$", "x_cant_be_"),                   # §107.3 constraint on the chosen X value
    (r"^(?:Each player|Players) can't draw more than one card each turn\.?$", "max_one_draw_each_turn"),
    (r"^Each player can't cast more than one spell each turn\.?$", "max_one_spell_each_turn"),
    (r"^No more than one creature can attack each combat\.?$", "max_one_attacker_each_combat"),
    (r"^No more than one creature can block each combat\.?$", "max_one_blocker_each_combat"),
    (r"^(?:Each player|Players) can cast spells only any time they could cast a sorcery\.?$", "cast_only_as_sorcery"),
    (r"^Your opponents can cast spells only any time they could cast a sorcery\.?$", "opponents_cast_only_as_sorcery"),
    (r"^Each opponent can cast spells only any time they could cast a sorcery\.?$", "opponents_cast_only_as_sorcery"),
    (r"^Spells with the chosen name can't be cast\.?$", "spells_with_chosen_name_cant_be_cast"),
    (r"^Activated abilities of sources with the chosen name can't be activated unless they're mana abilities\.?$",
     "abilities_of_chosen_name_cant_be_activated"),
    (r"^Creatures entering don't cause abilities to trigger\.?$", "creatures_entering_dont_trigger"),
    (r"^Permanents entering the battlefield don't cause abilities to trigger\.?$", "permanents_entering_dont_trigger"),
]


def _card_static(unit, ctx):
    for pat, tag in _CARD_STATIC:
        m = re.match(pat, unit.raw, re.I)
        if m:
            # a tag ending in '_' with a capturing pattern keeps the captured value (e.g. 'x_cant_be_0')
            full = tag + ground.slug(m.group(1)) if (tag.endswith("_") and m.groups()) else tag
            return CardOut(ctx["id"], [f'static("{ctx["id"]}", "{full}")'], "static")
    return None


# Mana-pool / spend-restriction RIDER sentences — almost always the TAIL sentence of a multi-sentence
# 'Add {…}. <rider>' line whose mana-add grounds but whose rider didn't, so the WHOLE line failed
# (e.g. Geosurge 'Add {R}…. Spend this mana only to cast artifact or creature spells.'; Jegantha
# '{T}: Add {W}{U}{B}{R}{G}. This mana can't be spent to pay generic mana costs.'). Each is a §605/§106
# constraint on mana already in a pool: spend-restriction (§106.1c), the §500.4 mana-emptying exemption
# (Shizuko/Omnath 'you don't lose this mana as steps and phases end'), or a may-spend-any-type relaxation.
# The grounded slug names WHICH constraint + its faithful condition tail (like _SPEND_RESTR already does
# for the same family inside _split_modifiers); a wrong fact is avoided by anchoring the whole sentence.
_MANA_RIDER = [
    # spend-restriction: which spells/abilities this mana may pay for (matches _SPEND_RESTR's slug shape)
    (r"^(?:spend|use) this mana only (?P<c>.+)$", "spend_only_"),
    # this mana CAN'T pay for X / you can't spend this mana to X — the negative form
    (r"^this mana can'?t be spent (?P<c>.+)$", "mana_cant_be_spent_"),
    (r"^you can'?t spend this mana (?P<c>to .+)$", "mana_cant_be_spent_"),
    # §500.4 mana-emptying exemption: the produced mana persists past the step/phase end (Shizuko/Omnath)
    (r"^(?:until (?P<d1>[^,]+), )?(?:you|they) don'?t lose this mana(?P<c1> as steps[\w ]*end)$",
     "dont_lose_this_mana"),
    (r"^(?:until (?P<d2>[^,]+), )?(?:that|this) mana doesn'?t empty(?P<c2> from [\w' ]*mana pool)$",
     "mana_doesnt_empty"),
    # a may-spend-any-type relaxation tied to an exile-then-cast effect (Laughing Jasper Flint family)
    (r"^mana of any type can be spent (?P<c>to .+)$", "mana_any_type_"),
    # note the type of mana spent (a charge/filter setup — Jeweled Amulet)
    (r"^note the type of mana spent to pay this (?:activation )?cost$", "note_mana_type_spent"),
]


# A 'can't be regenerated' rider — the TAIL sentence of a destroy/deal-damage line saying the affected
# permanents can't regenerate (§701.15c). Almost always sentence 2 of '<destroy/damage>. <subj> can't
# be regenerated[ this turn].' (Catastrophe, Incinerate, Mephitic Ooze, Balefire Dragon). The subject is
# whatever the prior sentence destroyed/damaged ('that creature', 'creatures destroyed this way', 'it');
# we record WHICH set + the this-turn scope as a faithful slug, so the whole line grounds.
_CANT_REGEN = re.compile(
    r"^(?P<subj>(?:a |the |that |those )?creatures?(?: dealt damage this way| destroyed this way)?"
    r"|(?:an? |the |those )?(?:artifacts?|permanents?|lands?)(?: destroyed this way)?"
    r"|the creature|it|they) can'?t be regenerated(?P<turn> this turn)?$", re.I)


def _cant_regenerate(unit, ctx):
    """'<subject> can't be regenerated[ this turn].' — a §701.15c no-regeneration rider (the tail of a
    destroy/damage line). Emits a faithful static slug naming the affected set + scope."""
    m = _CANT_REGEN.match(unit.raw.strip().rstrip("."))
    if not m:
        return None
    subj = ground.slug(m.group("subj"))
    scope = "_this_turn" if m.group("turn") else ""
    return CardOut(ctx["id"], [f'static("{ctx["id"]}", "{subj}_cant_be_regenerated{scope}")'],
                   "static")


def _mana_rider(unit, ctx):
    """A mana-pool / spend-restriction rider sentence (§106/§500.4/§605) — see _MANA_RIDER. Emits one
    faithful static slug so the surrounding 'Add {…}. <rider>' line grounds as a whole."""
    s = unit.raw.strip().rstrip(".")
    for pat, tag in _MANA_RIDER:
        m = re.match(pat, s, re.I)
        if not m:
            continue
        gd = m.groupdict()
        if tag.endswith("_") and gd.get("c"):                 # condition-carrying slug
            full = tag + ground.slug(gd["c"])
        else:                                                  # duration-prefixed pool-persistence slug
            dur = next((gd[k] for k in ("d1", "d2") if gd.get(k)), None)
            full = (f"until_{ground.slug(dur)}_" if dur else "") + tag
        return CardOut(ctx["id"], [f'static("{ctx["id"]}", "{full[:120]}")'], "static")
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
    r"each of (?:those|the|them)(?: [\w'-]+)?|"
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

# §613 continuous P/T-modification VOCABULARY — the grounded anchor that justifies a `modify_pt`
# (anthem) relation. The relation is emitted because a line carries BOTH the §613 layer-7c P/T-delta
# (`±N/±N`, captured structurally below) AND the §613 continuous-effect verb `get(s)` — NOT because a
# particular English template matched. `_PT_GET_VERB` is the grounded copula set; a line that names a
# `±N/±N` value with any OTHER verb ('deals ±N/±N', 'has ±N/+N') has no anthem anchor and abstains.
_PT_GET_VERB = frozenset({"get", "gets"})
# The grounded §613 condition connectives that scope a continuous P/T modification (`as long as` →
# §611 conditional duration; `for each` → §613 variable amount). Selected from this closed set, not
# from free prose; the connective is preserved in the emitted condition slug.
_PT_COND_CONN = frozenset({"as long as", "for each"})

_STATIC_PT = re.compile(rf"^(?:during your turn, )?(?P<who>{_SUBJ}) (?P<verb>gets?) (?:an additional )?(?P<pt>[+-]\d+/[+-]\d+)"
                        rf"(?: and (?:has|gains?) (?P<kw>[\w,{{}} ]+?))?"
                        rf"(?: (?P<conn>as long as|for each) (?P<cond>.+?))?\.?$", re.I)


def _static_pt(unit, ctx):
    """A static P/T grant with no duration — '<subject> get(s) +N/+N[ and has <keywords>].' (§613:
    layer 7c P/T, layer 6 ability-adding). The absent 'until end of turn' is what makes it static;
    one-shot 'until end of turn' pumps go to _spell/_activated via the effect engine instead.

    GROUNDED-PREDICATE form: the `modify_pt` relation is justified by the §613 anthem ANCHOR — the
    structural `±N/±N` P/T-delta JOINED to the grounded continuous-effect verb `get(s)` (_PT_GET_VERB).
    The regex only EXTRACTS the spans (subject NP, the P/T delta, the keyword tail, the condition NP +
    its grounded connective); it abstains unless the `get(s)` anchor grounds — so a `±N/±N` value
    reached by any other verb mints no anthem fact. The keyword tail grounds via _ground_kw (§702)."""
    m = _STATIC_PT.match(unit.raw)
    if not m:
        return None
    # ground the §613 anthem verb — abstain rather than emit a `modify_pt` for a non-`get(s)` line
    # (defensive: the regex already constrains `verb` to this set, but the predicate is justified by
    # vocabulary membership, not by the surface template having matched).
    if m.group("verb").lower() not in _PT_GET_VERB:
        return None
    # ground the condition connective against the closed §611/§613 set (defensive; regex-constrained).
    if m.group("conn") and m.group("conn").lower() not in _PT_COND_CONN:
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
    <keyword>' form); abstains if ANY conjunct doesn't ground (prime directive — no partial grant).

    GROUNDED-PREDICATE form: the own `modify_pt` head is justified by the SAME §613 anthem anchor as
    _static_pt — the structural `±N/±N` delta JOINED to the grounded continuous-effect verb `get(s)`
    (_PT_GET_VERB). The regex EXTRACTS the subject NP, the P/T delta, and the conjunct rest; each
    conjunct is grounded by re-dispatch. Abstains unless the `get(s)` anchor grounds."""
    m = re.match(rf"^(?P<subj>{_SUBJ}) (?P<verb>gets?) (?P<pt>[+-]\d+/[+-]\d+)(?:,| and) (?P<rest>.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    if m.group("verb").lower() not in _PT_GET_VERB:        # §613 anthem verb anchor (see _static_pt)
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
                             f'enters_with_counters("{cid}", "{ground.slug(mc.group(2))}", "{ground.slug(mc.group(1))}")'],
                       "etb_tapped")
    m = re.match(r"^~ enters tapped(?: unless (.+?))?(?: if (.+?))?\.?$", unit.raw)
    if m:
        cond = "unless_" + ground.slug(m.group(1)) if m.group(1) else \
            ("if_" + ground.slug(m.group(2)) if m.group(2) else "-")
        return CardOut(ctx["id"], [f'card_enters_tapped("{ctx["id"]}", "{cond}")'], "etb_tapped")
    # '~ enters tapped and doesn't untap during your/its controller's untap step' (Leviathan, Traxos…).
    m = re.match(r"^~ enters tapped and (?:doesn't|does not) untap during "
                 r"(?:its controller's|your|their) (?:next )?untap step\.?$", unit.raw, re.I)
    if m:
        cid = ctx["id"]
        return CardOut(cid, [f'card_enters_tapped("{cid}", "-")', f'doesnt_untap("{cid}", "self")'], "etb_tapped")
    # leading-conditional tapland: 'If <cond>, ~ enters tapped.' (Cave of the Frost Dragon family).
    m = re.match(r"^If (.+?), ~ enters tapped\.?$", unit.raw, re.I)
    if not m:
        return None
    return CardOut(ctx["id"], [f'card_enters_tapped("{ctx["id"]}", "if_{ground.slug(m.group(1))}")'], "etb_tapped")


def _ability_activation_static(unit, ctx):
    """'Activated abilities of <X> can't be activated [unless <cond>].' — a §602.5 activation
    restriction (Cursed Totem, Linvala, Pithing Needle family). Affected class is a descriptive slug."""
    m = re.match(r"^Activated abilities of (.+?) can't be activated(?: unless (.+?))?\.?$", unit.raw, re.I)
    if not m:
        return None
    tag = "activated_abilities_of_" + ground.slug(m.group(1)) + "_cant_be_activated" + \
          ("_unless_" + ground.slug(m.group(2)) if m.group(2) else "")
    return CardOut(ctx["id"], [f'static("{ctx["id"]}", "{tag}")'], "static")


def _enters_tapped_others(unit, ctx):
    """'<types> [your opponents control] enter [the battlefield] tapped.' — a §614 static that taps a
    class of OTHER permanents as they enter (Kismet / Frozen Aether / Imposing Sovereign family). The
    affected class + scope is a faithful descriptive slug; emitted card-level since it's not on ~ itself."""
    m = re.match(r"^((?:[A-Za-z]+, )*(?:[A-Za-z]+,? and )?[A-Za-z]+)"
                 r"( your opponents control| an opponent controls| you control)? "
                 r"enters?(?: the battlefield)? (tapped|untapped)\.?$",
                 unit.raw, re.I)
    if not m:
        return None
    types = ground.slug(m.group(1))
    if types in ("it", "they", "this", "that"):            # ~/it ETB is _etb_tapped's job, not this
        return None
    scope = {" your opponents control": "opponents_", " an opponent controls": "opponents_",
             " you control": "you_", None: ""}[m.group(2)]
    cid = ctx["id"]
    return CardOut(cid, [f'static("{cid}", "{scope}{types}_enter_{m.group(3).lower()}")'], "static")


def _modal(unit, ctx):
    """'Choose one —' / 'Choose one or both —' / 'Choose one at random —' — a modal spell/ability
    header (§700.2). Also the Commander-precon form 'Choose one. If you control a commander as you cast
    ~, you may choose both instead.' (the commander rider recorded as a flag)."""
    m = re.match(r"^Choose (one or both|one or more|up to one|up to two|up to three|one|two|three)"
                 r"(?P<rand> at random)?\s*[—–-]?\s*"
                 r"(?:\.\s*(?P<cmd>If .+?, (?:you may )?choose .+? instead\.))?"
                 r"(?:\s*(?P<rep>You may choose the same mode more than once\.))?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    mode = ground.slug(m.group(1)) + ("_at_random" if m.group("rand") else "")
    facts = [f'modal("{cid}", "{mode}")']
    if m.group("cmd"):
        # conditional 'choose more' rider (commander / kicked / teamwork / max speed / …) — descriptive slug.
        facts.append(f'static("{cid}", "{ground.slug(m.group("cmd"))[:120]}")')
    if m.group("rep"):
        facts.append(f'static("{cid}", "modal_repeat_allowed")')
    return CardOut(cid, facts, "modal")


def _mode_option(unit, ctx):
    """'• <effect>' — one mode of a modal spell/ability; its body is a normal effect clause. A leading
    flavor mode-name ('Cure Wounds — …', 'Dispel Magic — …') is stripped like an ability word (§207.2c,
    no rules meaning) when doing so lets the mode body parse — the name is kept as a descriptive fact."""
    m = re.match(r"^[•·∙]\s*(?P<body>.+)$", unit.raw)
    if not m:
        return None
    body = m.group("body")
    effects = _parse_body(body)
    if not effects:
        fm = re.match(r"^[A-Z][\w' /'-]+? [—–-] (.+)$", body)   # strip a flavor mode-name (no rules meaning)
        if fm:
            effects = _parse_body(fm.group(1))
    if not effects:
        return None
    cid, aid = ctx["id"], f"mode{ctx.get('seq', 0)}"
    return CardOut(cid, [f'mode_option("{cid}", "{aid}")'] + _effect_facts(cid, aid, effects), "mode_option")


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
    return CardOut(cid, [f'cant("{cid}", "{who}", "{_CANT[a]}")' for a in actions], "cant")


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
    # §115.6 targeting restrictions and §303/§301 attach restrictions — descriptive grounded slugs.
    (r"can't be the targets? of (.+)", "cant_be_target_of_"),
    (r"can be attached only to (.+)", "can_be_attached_only_to_"),
    (r"can only attack (.+)", "can_only_attack_"),
    (r"can only block (.+)", "can_only_block_"),
    (r"can't have (.+)", "cant_have_"),
    # generic trailing captures (after the specific shapes above) — a §508/§509 restriction with any
    # qualifier, recorded as a descriptive slug rather than abstaining.
    (r"can't be blocked (.+)", "cant_be_blocked_"),
    (r"can't block (.+)", "cant_block_"),
    (r"can't attack (.+)", "cant_attack_"),
    (r"can't be (.+)", "cant_be_"),                 # can't be copied/equipped/enchanted by X/regenerated …
    (r"can only (.+)", "can_only_"),                # can only attack/block alone, etc.
]


def _combat_restriction(unit, ctx):
    """'<subject> can('t) <combat-verb> <qualifier>.' — a static combat restriction with a condition
    (§508/§509). The qualifier is recorded as a descriptive slug (like a trigger/condition slug)."""
    # leading conditional/temporal wrappers ('As long as …', 'During your turn, …', 'Until …', 'This
    # turn, …', 'Except for …') carry a condition we won't fold into a flat restriction slug — abstain
    # and let the dedicated conditional-static handlers take them.
    if re.match(r"^(?:As long as|During|Until|This turn|Next turn|Except for)\b", unit.raw, re.I):
        return None
    # broadened subject: any §508/§509-restrictable SET (type/color/keyword subsets, 'Creatures you
    # control [with …]', 'Cowards', a legendary's short name via the _try_patterns ~-substitution) —
    # not just the old fixed alternation. The required '(can…)' clause + _CRESTR qualifier keeps it
    # from grabbing non-combat lines.
    # _TGT misses BARE plural subjects ('Creatures', 'creature spells') the old fixed list had, so
    # union them back in alongside the broadened _TGT sets.
    m = re.match(rf"^({_TGT}|creatures|creature spells) (can.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    # On a one-shot spell, a 'target …' subject is the SPELL'S OWN target — 'Target creature can't be
    # blocked this turn' is a one-shot effect that belongs to _spell, not a static card_restriction. So
    # decline only the target-led subjects on instants/sorceries; non-target subjects ('~ can't be
    # copied') keep their prior _combat_restriction coverage.
    if ({"Instant", "Sorcery"} & _types(ctx)
            and re.match(r"^(?:up to \w+ |any number of |another |a (?:second|third|fourth) )?(?:\w+ )?target\b",
                         m.group(1), re.I)):
        return None
    who = _target_slug(m.group(1))
    # a second, non-restriction effect ('… and has shroud', '… and gets +1/+1') must NOT be buried in
    # the restriction slug — abstain rather than emit a conflated fact (prime directive). (Kept as the
    # original guard so the broadened subject is PURELY ADDITIVE — no previously-covered line regresses.)
    if re.search(r" and (?:has|have|gains?|is|gets?|can't|becomes?) ", m.group(2), re.I):
        return None
    # a SEPARATE non-restriction effect folded into the qualifier (a grant of an ability, a redirect,
    # a second sentence) makes the slug a conflation — abstain (guardian_beast 'they have indestructible',
    # dog_umbra 'otherwise has umbra armor', togglodyte 'and prevent all damage'). These markers are
    # chosen to NOT fire on a legit second RESTRICTION clause (Faith's Fetters '…, and its activated
    # abilities can't be activated'), which the line above already permits as established behavior.
    if re.search(r"\b(?:otherwise|prevent all|this effect doesn't)\b", m.group(2), re.I):
        return None
    # ', and <NEW SUBJECT> can…' is a second restriction on a DIFFERENT subject (Autumn's Veil 'Spells
    # you control can't be countered…, and creatures you control can't be the targets…') — a conflation;
    # abstain. The new-subject list excludes 'its' so Faith's Fetters' same-permanent ', and its
    # activated abilities can't be activated' continuation is still permitted.
    if re.search(r", and (?:creatures?|players?|permanents?|lands?|artifacts?|enchantments?|spells?|"
                 r"tokens?|they|you|your opponents?|each) ", m.group(2), re.I):
        return None
    for pat, prefix in _CRESTR:
        mm = re.match("^" + pat + "$", m.group(2), re.I)
        if mm:
            cid = ctx["id"]
            return CardOut(cid, [f'card_restriction("{cid}", "{who}", "{prefix}{ground.slug(mm.group(1))}")'],
                           "combat_restriction")
    return None


# A static GRANT of one-or-more QUOTED abilities to a SET of permanents (§613.6) — the anthem-shaped
# sibling of _granted_ability, for subjects _GRANTED's narrow 'who' alternation misses ('Commander
# creatures you own have …', 'Green creatures have …', soulbond 'each of those creatures has …',
# 'Creatures you control with the chosen name have …'). The line is WHOLE-unit: '<set> has/have
# "<ab1>"[ and "<ab2>"]*[ until end of turn].', optionally wrapped in a leading 'As long as <cond>, '
# (soulbond, 'As long as you control a Demon, ~ has …'). Each quoted ability is recorded faithfully as
# a slug of its self-normalized text; the wrapper condition rides the duration slot ('as_long_as_…').
_GRANT_SET = re.compile(
    rf'^(?:As long as (?P<cond>.+?), )?(?P<who>{_SUBJ}) (?:has|have) '
    r'(?P<abs>"[^"]+"(?: and "[^"]+")*)'
    r'(?P<dur> until end of turn)?\.?$', re.I | re.S)


def _grant_quoted_to_set(unit, ctx):
    """'<set of permanents> has/have "<quoted ability>"[ and "<quoted ability>"]*.' — a static §613.6
    ability grant to a SUBSET of permanents (the anthem analogue of _granted_ability). Also covers the
    soulbond wrapper 'As long as ~ is paired with another creature, each of those creatures has "…"'
    and 'As long as <cond>, <subj> has "…"', recording the pairing/condition in the duration slot.
    Emits one grants_ability per quoted ability; abstains if any quoted body slugs empty."""
    if {"Instant", "Sorcery"} & _types(ctx):
        return None
    m = _GRANT_SET.match(unit.raw)
    if not m:
        return None
    bodies = re.findall(r'"([^"]+)"', m.group("abs"))
    abs_slugs = [ground.slug(b)[:160] for b in bodies]
    if not abs_slugs or not all(abs_slugs):
        return None
    who = _target_slug(m.group("who"))
    if m.group("cond"):
        dur = "as_long_as_" + ground.slug(m.group("cond"))
    elif m.group("dur"):
        dur = "until_end_of_turn"
    else:
        dur = "-"
    cid = ctx["id"]
    facts = [f'grants_ability("{cid}", "{who}", "{ab}", "{dur}")' for ab in abs_slugs]
    return CardOut(cid, facts, "grant_quoted_to_set")


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
    return CardOut(cid, [f'grants_ability("{cid}", "{_target_slug(m.group("who"))}", "{ab}", "{dur}")'],
                   "granted_ability")


_GRANT_KW_AB = re.compile(rf'^(?P<who>{_SUBJ}) (?:has|have|gains?) (?P<kw>[\w,{{}} ]+?) and '
                          r'"(?P<ab>.+)"(?P<dur> until end of turn)?\.?$', re.I | re.S)


def _grant_kw_and_ability(unit, ctx):
    """'<subject> has <keyword(s)> and "<quoted ability>".' — an Aura/Equipment §613.6 grant of BOTH a
    keyword AND a quoted ability (Bequeathal, Underworld Rage-Hound auras …). Emits one grant_keyword
    effect per keyword plus a grants_ability; abstains unless every keyword grounds."""
    m = _GRANT_KW_AB.match(unit.raw)
    if not m:
        return None
    grounded = [_ground_kw(k.strip()) for k in re.split(r",| and ", m.group("kw")) if k.strip()]
    if not grounded or not all(grounded):
        return None
    ab = ground.slug(m.group("ab"))[:160]
    if not ab:
        return None
    who, cid, aid = _target_slug(m.group("who")), ctx["id"], f"a{ctx.get('seq', 0)}"
    facts = [f'card_ability("{cid}", "{aid}", "static")']
    facts += [f'card_effect("{cid}", "{aid}", {i}, "grant_keyword", "{kw}", "{who}", "-", "-")'
              for i, (kw, _p) in enumerate(grounded)]
    dur = "until_end_of_turn" if m.group("dur") else "-"
    facts.append(f'grants_ability("{cid}", "{who}", "{ab}", "{dur}")')
    return CardOut(cid, facts, "granted_ability")


def _static_grant(unit, ctx):
    """A static keyword grant with no P/T — '[During your turn, ]<subject> has/have <keywords>
    [as long as <cond>].' (§613 layer 6): 'Enchanted creature has flying', 'During your turn, ~ has
    first strike', 'Other creatures you control have trample as long as you control a Forest'."""
    m = re.match(rf"^(?:during your turn, )?(?P<who>{_SUBJ}) (?:has|have|gains?|is|are) (?P<kw>[\w,{{}} ]+?)"
                 rf"(?: as long as (?P<cond>.+?))?\.?$", unit.raw, re.I)
    if not m:
        return None
    # the greedy {_SUBJ} can swallow a FIRST effect into 'who' ('… have base power and toughness 4/4 and
    # have flying' -> who='…4/4', kw='flying'): if 'who' itself carries a second predicate, this is a
    # multi-effect line _static_grant must NOT collapse — defer it (return None) so the body splitter
    # (which splits into the component effects) or abstention handles it faithfully.
    if re.search(r"\b(?:has|have|gains?|gets?|loses?|becomes?) \b|\bpower and toughness\b", m.group("who"), re.I):
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
    return CardOut(cid, [f'additional_cost("{cid}", "{ground.slug(m.group(1))}")'], "additional_cost")


def _exert(unit, ctx):
    """'You may exert ~ as it attacks.[ When you do, <effect>.]' — the §701.40 exert keyword action: a
    static option to exert when attacking, plus the reflexive 'when you do' triggered ability whose
    body must ground (else abstain)."""
    m = re.match(r"^You may exert ~ as it attacks\.(?: When you do, (?P<body>.+?)\.?)?$", unit.raw, re.I)
    if not m or "exert" not in ground.keyword_actions():
        return None
    cid, aid = ctx["id"], f"a{ctx.get('seq', 0)}"
    facts = [f'static("{cid}", "may_exert_as_it_attacks")']
    if m.group("body"):
        effects = _parse_body(m.group("body"))
        if not effects:
            return None
        facts += [f'card_ability("{cid}", "{aid}", "triggered")',
                  f'ability_trigger("{cid}", "{aid}", "exert_attacks")']
        facts += _effect_facts(cid, aid, effects)
    return CardOut(cid, facts, "exert")


def _enter_as_copy(unit, ctx):
    """'You may have ~ enter as a copy of <X>[, except <mods>].' — a copy/clone ETB (§707/§614). The
    copied object and any exceptions are recorded as a descriptive slug."""
    m = re.match(r"^You may have ~ enter as a copy of (.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    return CardOut(ctx["id"], [f'static("{ctx["id"]}", "enters_as_copy_of_{ground.slug(m.group(1))}")'],
                   "static")


def _escapes_with(unit, ctx):
    """'~ escapes with N <kind> counters on it.' — a rider on the §702.139 Escape keyword describing
    counters the card gains when it escapes."""
    m = re.match(r"^~ escapes with (\w+) ([+\-]\d+/[+\-]\d+|\w[\w ]*?) counters? on it\.?$", unit.raw, re.I)
    if not m or "escape" not in ground.keyword_abilities():
        return None
    return CardOut(ctx["id"],
                   [f'static("{ctx["id"]}", "escapes_with_{ground.slug(m.group(1))}_{ground.slug(m.group(2))}_counter")'],
                   "static")


def _assign_damage_unblocked(unit, ctx):
    """'You may have ~ assign its combat damage as though it weren't blocked.' — a §509.2 damage-
    assignment option (trample-like)."""
    if not re.match(r"^You may have ~ assign its combat damage as though it weren't blocked\.?$",
                    unit.raw, re.I):
        return None
    return CardOut(ctx["id"], [f'static("{ctx["id"]}", "may_assign_damage_as_though_unblocked")'],
                   "static")


def _cast_as_flash(unit, ctx):
    """'You may cast ~ as though it had flash[ <rider>].' — a flash-granting timing permission (§702.8
    as-though, §601). Any trailing rider ('if you pay {2} more', 'If you cast it any time a sorcery
    couldn't…') is recorded as a descriptive slug suffix — coarse but faithful."""
    m = re.match(r"^You may cast ~ as though it had flash(?:[.,]? (.+?))?\.?$", unit.raw, re.I)
    if m:
        tag = "cast_as_though_flash" + ("_" + ground.slug(m.group(1)) if m.group(1) else "")
        return CardOut(ctx["id"], [f'static("{ctx["id"]}", "{tag}")'], "static")
    # 'You may cast <X> spells as though they had flash' — a flash-grant scoped to a spell class.
    m = re.match(r"^You may cast (.+?) as though they had flash\.?$", unit.raw, re.I)
    if m:
        return CardOut(ctx["id"], [f'static("{ctx["id"]}", "cast_{ground.slug(m.group(1))}_as_flash")'], "static")
    # 'The next <spell class> you cast this turn can be cast as though it had flash.' — one-shot flash grant.
    m = re.match(r"^The next (.+?) you cast this turn can be cast as though it had flash\.?$", unit.raw, re.I)
    if m:
        return CardOut(ctx["id"], [f'static("{ctx["id"]}", "next_{ground.slug(m.group(1))}_as_flash")'], "static")
    # 'The first/next <spell class> you cast each/this turn has <keyword[ N]>.' — grants a §702 keyword
    # to a scoped spell; grounds only if the keyword is in the §702 roster.
    m = re.match(r"^The (first|next) (.+?) you cast (?:each|this) turn has ([\w ]+?)\.?$", unit.raw, re.I)
    if m:
        kw = _ground_kw(m.group(3).strip())
        if kw and kw[0]:
            return CardOut(ctx["id"], [f'static("{ctx["id"]}", "{m.group(1).lower()}_{ground.slug(m.group(2))}_has_{kw[0]}")'],
                           "static")
    return None


def _alt_cost(unit, ctx):
    """'You may [pay/return/remove …] rather than pay <X>'s/the mana cost [for … spells].' — an
    alternative casting cost (§118.9/§601). The alternative and what it replaces are slugged."""
    m = re.match(r"^You may (.+?) rather than pay (~'s mana cost|its mana cost|the mana cost(?: for [\w' ]+?)?|the equip cost[\w' ]*?)(?:[,.].*)?$", unit.raw, re.I)
    if not m:
        return None
    return CardOut(ctx["id"], [f'static("{ctx["id"]}", "alt_cost_{ground.slug(m.group(1))}_for_{ground.slug(m.group(2))}")'], "static")


def _enters_with_counters(unit, ctx):
    """'~ enters with N +N/+N counters on it.' — an ETB counter replacement (§122/§614)."""
    # dynamic-count form: '… enters with a number of <kind> counters on it equal to <X>' (§122/§614).
    md = re.match(r"^(?:~|it|That \w+) enters with a number of ([+\-]\d+/[+\-]\d+|\w[\w ]*?) counters? on it "
                  r"equal to (.+?)\.?$", unit.raw)
    if md:
        cid = ctx["id"]
        return CardOut(cid, [f'enters_with_counters("{cid}", "{ground.slug(md.group(1))}", "equal_to_{ground.slug(md.group(2))}")'],
                       "enters_with_counters")
    m = re.match(r"^(?:If .+?, )?(?:~|it) enters with (\w+) ([+\-]\d+/[+\-]\d+|\w[\w ]*?) counters? on it"
                 r"(?: (?:if|for each) (?P<cond>.+?))?\.?$", unit.raw)
    if not m:
        return None
    cid = ctx["id"]
    cond = m.group("cond") or (unit.raw.lower().startswith("if ") and "conditional") or None
    n = ground.slug(m.group(1)) if not cond else "var"
    return CardOut(cid, [f'enters_with_counters("{cid}", "{ground.slug(m.group(2))}", "{n}")'],
                   "enters_with_counters")


def _cast_restriction(unit, ctx):
    """'Cast ~ only <when/if …>.' — a casting timing/condition restriction (§601)."""
    m = re.match(r"^Cast ~ only (.+?)\.?$", unit.raw, re.I)
    if not m:
        return None
    return CardOut(ctx["id"], [f'static("{ctx["id"]}", "cast_only_{ground.slug(m.group(1))}")'], "static")


def _doesnt_untap(unit, ctx):
    """'~ / Enchanted creature doesn't untap during …untap step.' — an untap restriction (§502).
    Also the SET form 'Creatures with power 3 or greater / Nonbasic lands / Islands don't untap during
    their controllers' untap steps' (Meekstone, Back to Basics, Choke, Intruder Alarm, Hokori): the
    subject set is slugged faithfully into the same doesnt_untap target slot."""
    cid = ctx["id"]
    m = re.match(r"^(~|Enchanted \w+|Equipped \w+|That creature|That permanent) doesn't untap "
                 r"during (?:its controller's|your|their)(?: next)? untap step\.?$", unit.raw, re.I)
    if m:
        return CardOut(cid, [f'doesnt_untap("{cid}", "{_target_slug(m.group(1))}")'], "doesnt_untap")
    m = re.match(r"^(?P<subj>.+?) don't untap during (?:their controllers'|their) untap steps?\.?$",
                 unit.raw, re.I)
    if m:
        subj = ground.slug(m.group("subj"))
        if subj:
            return CardOut(cid, [f'doesnt_untap("{cid}", "{subj}")'], "doesnt_untap")
    return None


# §118.9 cost-modification VOCABULARY — the grounded anchor that justifies a `cost_modifier`
# relation. The relation is emitted because a line carries the §118.9 'costs {N} <dir> to <kind>'
# verb phrase, NOT because a particular English template matched. `dir` is a grounded direction
# token; `kind` is the grounded action whose cost is modified (casting §601 vs activating §602),
# which also fixes the default scope of the self/ability form.
_COST_MOD_DIRECTIONS = frozenset({"less", "more"})
_COST_MOD_KINDS = frozenset({"cast", "activate"})
_KIND_DEFAULT_SCOPE = {"cast": "self", "activate": "activated_ability"}

# Each anchor is purely STRUCTURAL: it locates the §118.9 phrase and the span slots around it.
# `scope_grp`/`cond_grp` name which capture (if any) holds the affected-set NP and the condition NP;
# `lead_cond_grp` is a leading 'If <cond>,' rider. None of these select the predicate — the predicate
# is `cost_modifier`, justified by the matched §118.9 anchor (kind ∈ _COST_MOD_KINDS).
_COST_MOD_ANCHORS = [
    # SELF form: '[If <cond>, ]~ costs {N} <dir> to cast[ <rider/for each …>]' — scope defaults to self.
    (re.compile(r"^(?:If (?P<lead>.+?), )?~ costs (?P<amt>(?:\{[^}]+\})+|\d+) (?P<dir>less|more) "
                r"to (?P<kind>cast)(?:,? (?P<cond>.+?))?\.?$", re.I),
     "self", None, "cond", "lead"),
    # SET form: '<spell-class> spells you cast cost {N} <dir> to cast' — scope is the matched NP.
    (re.compile(r"^(?P<scope>[\w'~ ]*?spells?[\w'~ ]*?) costs? (?P<amt>(?:\{[^}]+\})+|\d+) "
                r"(?P<dir>less|more) to (?P<kind>cast)\.?$", re.I),
     None, "scope", None, None),
    # ABILITY form: "~'s abilities / this ability / abilities you activate cost {N} <dir> to activate
    # [<for each …>]" — scope defaults to activated_ability.
    # the leading NP ('~'s abilities' / 'this ability' / …) is matched only to anchor the phrase; the
    # scope is the §602 default 'activated_ability' (default_scope=None -> _KIND_DEFAULT_SCOPE[kind]).
    (re.compile(r"^(?:~'s abilities?|this ability|abilities you activate) costs? "
                r"(?P<amt>(?:\{[^}]+\})+|\d+) (?P<dir>less|more) to (?P<kind>activate)(?: (?P<cond>.+?))?\.?$", re.I),
     None, None, "cond", None),
]


def _cost_modifier(unit, ctx):
    """Cost-reduction / -increase statics (§118.9): '~ costs {S} less to cast [if <cond>]',
    '<X> spells you cast cost {S} less to cast', '~ costs {S} more to cast for each …'.

    GROUNDED-PREDICATE form (see _COST_MOD_ANCHORS): the `cost_modifier` relation is justified by
    the §118.9 'costs {N} <dir> to <kind>' anchor — the regexes only EXTRACT the four spans
    (direction, amount, affected-set scope, condition); they no longer select the predicate."""
    cid = ctx["id"]
    for pat, default_scope, scope_grp, cond_grp, lead_grp in _COST_MOD_ANCHORS:
        m = pat.match(unit.raw)
        if not m:
            continue
        gd = m.groupdict()
        direction = gd["dir"].lower()
        kind = gd["kind"].lower()
        # the anchor is grounded against §118.9 vocabulary — abstain rather than emit an
        # un-grounded predicate (defensive: the regexes already constrain dir/kind to these).
        if direction not in _COST_MOD_DIRECTIONS or kind not in _COST_MOD_KINDS:
            return None
        scope = ground.slug(gd[scope_grp]) if scope_grp else (default_scope or _KIND_DEFAULT_SCOPE[kind])
        cond_raw = gd.get(cond_grp) if cond_grp else None
        if cond_raw:
            cond = ground.slug(cond_raw)
        elif lead_grp and gd.get(lead_grp):
            cond = "if_" + ground.slug(gd[lead_grp])
        else:
            cond = "-"
        return CardOut(cid, [f'cost_modifier("{cid}", "{direction}", "{ground.slug(gd["amt"])}", '
                            f'"{scope}", "{cond}")'], "cost_modifier")
    return None


def _class_level(unit, ctx):
    """A Class card's level-up activated ability '{cost}: Level N' (§716)."""
    m = re.match(r"^((?:\{[^}]+\})+): Level (\d+)$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'class_level("{cid}", "{m.group(1)}", "{m.group(2)}")'], "class_level")


# §604.3 characteristic-defining VOCABULARY — the grounded anchor that justifies a `cda` relation.
# The relation is emitted because a line states a P/T CHARACTERISTIC (the closed §604.3 / §208 set
# below) joined by a copula to a defining game quantity, NOT because a particular English template
# matched. `_CDA_CHARACTERISTICS` is the closed roster of characteristics a CDA may define here;
# `_CDA_COPULA` is the grounded §604.3 "is defined by" connective (is/are/becomes). Membership in
# both is what licenses `cda`; the regex only EXTRACTS the characteristic token, the definition span,
# and an optional duration rider.
_CDA_CHARACTERISTICS = frozenset({"power", "toughness", "power and toughness"})
_CDA_COPULA = frozenset({"is", "are", "be", "becomes", "become"})

# Purely STRUCTURAL: locate the §604.3 copula and the span slots around it. `char` / `copula` are
# matched permissively and then GROUNDED against the closed sets above (abstain otherwise); `val` is
# the defining-quantity span; `dur` is an optional leading 'during <X>,' rider. No predicate is
# selected here — the predicate is `cda`, justified by the matched §604.3 anchor.
_CDA_ANCHOR = re.compile(
    r"^(?:during (?P<dur>[\w' ]+?), )?~'s (?P<char>power and toughness|power|toughness) "
    r"(?P<copula>is|are|becomes?) (?:each )?(?:equal to )?(?P<val>.+?)\.?$", re.I)


def _cda(unit, ctx):
    """A characteristic-defining ability (§604.3): \"~'s power [and toughness] (is|are) [each] equal to
    <X>\" — the P/T is defined by a game quantity, recorded as a descriptive slug.

    GROUNDED-PREDICATE form (see _CDA_ANCHOR): the `cda` relation is justified by the §604.3 anchor —
    a closed P/T CHARACTERISTIC (_CDA_CHARACTERISTICS) joined by the grounded defining copula
    (_CDA_COPULA) to a quantity. The regex only EXTRACTS the characteristic, definition, and duration
    spans; it no longer selects the predicate. Abstains if either token isn't in its grounded set."""
    m = _CDA_ANCHOR.match(unit.raw)
    if not m:
        return None
    char = m.group("char").lower()
    copula = m.group("copula").lower()
    # the anchor is grounded against the §604.3 / §208 vocabulary — abstain rather than emit an
    # un-grounded predicate (defensive: the regex already constrains both to these sets).
    if char not in _CDA_CHARACTERISTICS or copula not in _CDA_COPULA:
        return None
    cid = ctx["id"]
    val = ground.slug(m.group("val"))
    if m.group("dur"):
        val += "_during_" + ground.slug(m.group("dur"))
    return CardOut(cid, [f'cda("{cid}", "{ground.slug(char)}", "{val}")'], "cda")


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
    return CardOut(cid, [f'level("{cid}", "station", "{m.group(1)}_plus")'] + bo.facts, "station")


def _specialize(unit, ctx):
    """'Specialize {cost}' (Baldur's Gate keyword, NOT in this rules.txt KB). Recorded as a DESCRIPTIVE
    card fact — NOT a grounded §702 keyword — so it captures the card's text without over-claiming a
    rules-defined keyword the engine can't validate."""
    m = re.match(r"^Specialize ((?:\{[^}]+\})+)$", unit.raw)
    if not m:
        return None
    return CardOut(ctx["id"], [f'specialize("{ctx["id"]}", "{m.group(1)}")'], "specialize")


def _ticket_pt(unit, ctx):
    """Unfinity ticket cards (acorn): '{TK}{TK} — N/N' sets the creature's power/toughness when that
    many tickets have been paid — an alternate-P/T threshold table, structurally like a leveler band.
    One fact per row: ticket_pt(card, ticket_count, "P/T")."""
    m = re.match(r"^((?:\{TK\})+)\s*[—-]\s*([+-]?\d+/[+-]?\d+)$", unit.raw)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'ticket_pt("{cid}", {m.group(1).count("{TK}")}, "{m.group(2)}")'],
                   "ticket_pt")


# --- descriptive mechanics ABSENT from this rules.txt KB -------------------------------------------
# These are named keyword/parameter mechanics from supplemental/newer sets that the §702 roster in
# rules.txt does NOT define, so per the prime directive we must NOT emit a grounded printed_keyword for
# them. Each gets a DEDICATED descriptive relation (like specialize / ticket_pt) that records
# the card's text faithfully without over-claiming a rules-defined keyword the engine can't validate.

def _starting_intensity(unit, ctx):
    """'Starting intensity N' (the Intensity mechanic, a custom/Un- set keyword NOT in this rules.txt
    KB) — sets the permanent's initial intensity count. Recorded descriptively as intensity with
    kind 'starting' and the integer value."""
    m = re.match(r"^Starting intensity (\d+)\.?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'intensity("{cid}", "starting", "{m.group(1)}")'], "intensity")


def _intensify_static(unit, ctx):
    """A standalone static intensity-modifier line (the Intensity mechanic, NOT in this rules.txt KB):
    'Cards you own named ~ intensify by N.' / 'All Chorus cards you own intensify by N.' — a continuous
    effect raising the intensity of a set of cards by N. The affected set is recorded as a faithful
    descriptive slug (WHICH cards) and N as the amount; we do NOT claim a grounded keyword. Restricted
    to the bare static shape (no When/Whenever/At trigger, no ':') so it never swallows a triggered
    '… intensifies by N' clause that the effect engine should own."""
    if re.match(r"^(?:When|Whenever|At)\b", unit.raw, re.I) or ":" in unit.raw or '"' in unit.raw:
        return None
    # 'who' is a bare noun phrase — no sentence boundary (a '. ' means a preceding effect clause that
    # the multi-sentence splitter / effect engine should own, not a subject for us to swallow).
    m = re.match(r"^(?:Then )?(?P<who>[^.]+?) intensify by (?P<n>\d+)\.?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'intensify("{cid}", "{ground.slug(m.group("who"))}", "{m.group("n")}")'],
                   "intensify")


def _augment(unit, ctx):
    """'Augment {cost}' (the Unstable Augment keyword — a half-card mechanic NOT defined in this
    rules.txt §702 roster). Recorded descriptively as augment with the augment cost, like
    specialize, so the card's text is captured without over-claiming a grounded keyword."""
    m = re.match(r"^Augment ((?:\{[^}]+\})+)$", unit.raw)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'augment("{cid}", "{m.group(1)}")'], "augment")


def _poison_tolerance(unit, ctx):
    """'Poison Tolerance +N' (a supplemental/silver-border mechanic NOT in this rules.txt KB) — raises
    the poison threshold before the player loses. Recorded descriptively as poison_tolerance with
    the integer bonus; we do NOT claim a grounded keyword."""
    m = re.match(r"^Poison Tolerance \+(\d+)\.?$", unit.raw, re.I)
    if not m:
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'poison_tolerance("{cid}", "{m.group(1)}")'], "poison_tolerance")


# --- SUPPLEMENTAL clusters (Unfinity stickers/tickets/teamwork, Contraptions, Alchemy spellbooks) ---
# None of these are defined in this rules.txt §702 roster, so per the prime directive we do NOT mint a
# grounded printed_keyword for them — each gets a DEDICATED descriptive card_* relation. To stay faithful,
# every handler matches the WHOLE oracle line: a compound body whose non-supplemental half wouldn't
# ground on its own ABSTAINS (returns None) rather than emitting a partial fact.

def _teamwork(unit, ctx):
    """'Teamwork N' — the Unfinity keyword ability with a numeric parameter. NOT in this rules.txt KB,
    so recorded descriptively as teamwork(card, N) rather than a grounded §702 keyword."""
    m = re.match(r"^Teamwork (\d+)\.?$", unit.raw, re.I)
    if not m:
        return None
    return CardOut(ctx["id"], [f'teamwork("{ctx["id"]}", {m.group(1)})'], "teamwork")


# A sticker-placement clause: 'put a[n] [<kind>] sticker[s] on <target>'. <kind> is one of the four
# Unfinity sticker categories (name / art / power and toughness / ability) or absent (a plain sticker).
_STICKER_KIND = {"name": "name", "art": "art", "power and toughness": "power_and_toughness",
                 "ability": "ability"}
_STICKER_PUT = re.compile(
    r"^you may put (?:up to \w+ )?(?:a |an )?(?:(?P<kind>name|art|power and toughness|ability) )?sticker(?:s)? "
    r"on (?P<target>[^.,]+?)\.?$", re.I)


def _sticker_clause(text):
    """Parse a bare 'you may put [a] [<kind>] sticker on <target>' clause -> (kind, target_slug) or
    None. Used by _sticker for both the trigger frames and the modal-bullet/standalone forms."""
    m = _STICKER_PUT.match(text.strip().rstrip("."))
    if not m:
        return None
    kind = _STICKER_KIND.get((m.group("kind") or "").strip().lower(), "plain")
    target = ground.slug(m.group("target"))
    return (kind, target) if target else None


# 'When/Whenever/As ~ <event>, [you get {TK}…, then ]you may put … sticker on …' — the productive
# Unfinity sticker frames. The optional 'you get {TK}{TK}…, then ' ticket-gain prefix is itself a
# supplemental mechanic, captured as a SEPARATE get_tickets fact (both halves grounded faithfully).
# the body is restricted to a SINGLE sentence ([^.]) so a compound line with a follow-on sentence
# ('… name sticker on it. You gain X life …') can't swallow the second effect into the target slug.
_STICKER_FRAME = re.compile(
    r"^(?P<trig>When|Whenever|As) ~ (?P<event>enters|attacks|deals combat damage to a player), "
    r"(?:you get (?P<tk>(?:\{TK\})+), then )?(?P<body>you may put [^.]+?)\.?$", re.I)
_FRAME_EVENT = {"enters": "enters", "attacks": "attacks",
                "deals combat damage to a player": "combat_damage"}


def _sticker(unit, ctx):
    """Unfinity STICKER frames (a digital/supplemental mechanic NOT in this rules.txt KB). Three whole-
    line shapes, all recorded descriptively:
      • 'When/Whenever/As ~ <event>, [you get {TK}…, then ]you may put a[ <kind>] sticker on <target>.'
        -> sticker(card, <event>, <kind>, <target>) (+ get_tickets for any {TK} prefix);
      • '• You may put … sticker on <target>.' (a modal bullet) -> frame 'modal';
      • a bare 'Put an art sticker on <target>.' / standalone 'You may put …' -> frame 'standalone'.
    The body must be EXACTLY a sticker-placement clause (no trailing 'and …'/'. …' second effect), so a
    compound line whose remainder wouldn't ground abstains rather than emit a partial fact."""
    cid = ctx["id"]
    raw = unit.raw.strip()
    # modal bullet: '• You may put … sticker on …'
    if raw.startswith("•"):
        sc = _sticker_clause(raw[1:].strip())
        if sc:
            return CardOut(cid, [f'sticker("{cid}", "modal", "{sc[0]}", "{sc[1]}")'], "sticker")
        return None
    m = _STICKER_FRAME.match(raw)
    if m:
        sc = _sticker_clause(m.group("body"))
        if not sc:
            return None
        facts = []
        if m.group("tk"):
            facts.append(f'get_tickets("{cid}", {m.group("tk").count("{TK}")})')
        frame = _FRAME_EVENT[m.group("event").lower()]
        facts.append(f'sticker("{cid}", "{frame}", "{sc[0]}", "{sc[1]}")')
        return CardOut(cid, facts, "sticker")
    # bare standalone placement ('Put an art sticker on …' imperative, or 'You may put …') — single clause
    if re.match(r"^(?:You may )?put ", raw, re.I) and "." not in raw.rstrip("."):
        body = re.sub(r"^Put ", "you may put ", raw, flags=re.I) if not raw.lower().startswith("you may") else raw
        sc = _sticker_clause(body)
        if sc:
            return CardOut(cid, [f'sticker("{cid}", "standalone", "{sc[0]}", "{sc[1]}")'], "sticker")
    return None


# Contraption assembly (the Unstable 'assemble a Contraption' mechanic — NOT in this rules.txt KB).
_CONTRAPTION_COUNT = {"a": "1", "two": "2", "x": "x"}
_ASSEMBLE = re.compile(r"^assemble (?P<n>a|two|X) Contraptions?\.?$", re.I)
_ETB_ASSEMBLE = re.compile(r"^When ~ enters, (?:it|~) assembles a Contraption\.?$", re.I)


def _assemble_contraption(unit, ctx):
    """Contraption assembly, a supplemental (Unstable) mechanic with no §702 grounding. Two whole-line
    shapes recorded descriptively as assemble_contraption(card, frame, count):
      • 'When ~ enters, it/~ assembles a Contraption.'  -> frame 'enters', count '1';
      • a bare imperative 'Assemble a/two/X Contraption(s).' (spell or modal bullet) -> frame 'spell'.
    Anchored whole-line so compound bodies ('… then assemble a Contraption', '… for each …') abstain."""
    cid = ctx["id"]
    raw = unit.raw.strip()
    if _ETB_ASSEMBLE.match(raw):
        return CardOut(cid, [f'assemble_contraption("{cid}", "enters", "1")'], "assemble_contraption")
    bullet = raw[1:].strip() if raw.startswith("•") else raw
    m = _ASSEMBLE.match(bullet)
    if m:
        cnt = _CONTRAPTION_COUNT[m.group("n").lower()]
        frame = "modal" if raw.startswith("•") else "spell"
        return CardOut(cid, [f'assemble_contraption("{cid}", "{frame}", "{cnt}")'], "assemble_contraption")
    return None


# Spellbook draft/conjure (Alchemy/Conspiracy 'spellbook' zone — NOT in this rules.txt KB). Kept TIGHT:
# only the bare 'draft a card from <X>'s spellbook' clause, optionally under a simple known trigger
# frame. Compound lines ('… and exile it', '… then put those cards onto the battlefield', '… twice')
# carry a SECOND effect that wouldn't ground on its own, so they abstain (faithful-or-abstain).
_SPELLBOOK_FRAME = re.compile(
    r"^(?:(?P<trig>When|Whenever|As) ~ (?P<event>enters|dies|attacks), )?"
    r"draft a card from ~'s spellbook\.?$", re.I)
_SPELLBOOK_EVENT = {"enters": "enters", "dies": "dies", "attacks": "attacks"}


def _spellbook(unit, ctx):
    """'[<trigger>, ]draft a card from ~'s spellbook.' — the cleanest spellbook frame, recorded
    descriptively as spellbook(card, frame, 'draft'). frame is the trigger event ('enters'/'dies'/
    'attacks') or 'standalone' for the bare imperative. Tight on purpose: any compound continuation
    abstains so we never emit a partial fact for the ungrounded second half."""
    m = _SPELLBOOK_FRAME.match(unit.raw.strip())
    if not m:
        return None
    frame = _SPELLBOOK_EVENT.get((m.group("event") or "").lower(), "standalone")
    return CardOut(ctx["id"], [f'spellbook("{ctx["id"]}", "{frame}", "draft")'], "spellbook")


def _ready_to_run(unit, ctx):
    """'Ready to run' (a named keyword on the 'Runner' subgame cards, NOT in this rules.txt KB) — a
    bare designation marker. Recorded descriptively as static('ready_to_run') so the line is
    captured without claiming a grounded §702 keyword."""
    if not re.match(r"^Ready to run\.?$", unit.raw, re.I):
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'static("{cid}", "ready_to_run")'], "ready_to_run")


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
        return CardOut(cid, [f'level("{cid}", "band", "{m.group(1)}_{hi}")'], "leveler")
    if re.match(r"^[+-]?\d+/[+-]?\d+$", unit.raw):
        return CardOut(cid, [f'level("{cid}", "pt", "{unit.raw}")'], "leveler")
    return None


def _enters_prepared(unit, ctx):
    """'~ enters prepared.' — gains the prepared designation as it enters (§722.3)."""
    if not re.match(r"^~ enters prepared\.?$", unit.raw, re.I):
        return None
    return CardOut(ctx["id"], [f'static("{ctx["id"]}", "enters_prepared")'], "static")


def _can_block_additional(unit, ctx):
    """'<subject> can block an additional creature[ each combat]' / 'can block any number of creatures'
    — a static blocking ability (§509). Subject is ~ or a creature subset."""
    sub = r"(?:~|each creature you control|creatures you control)"
    if re.match(rf"^{sub} can block any number of creatures\.?$", unit.raw, re.I):
        return CardOut(ctx["id"], [f'static("{ctx["id"]}", "can_block_any_number")'], "static")
    m = re.match(rf"^{sub} can block an additional (?:creature|\w+ creatures?)(?: each combat)?\.?$", unit.raw, re.I)
    if not m:
        return None
    return CardOut(ctx["id"], [f'static("{ctx["id"]}", "can_block_additional")'], "static")


def _assigns_toughness(unit, ctx):
    """'<subject> assigns combat damage equal to its toughness rather than its power' — the §510 Doran-
    style damage-assignment characteristic (scope coarse; the mechanic is recorded card-level)."""
    if not re.match(r"^(?:~|each creature you control|creatures you control|each creature) assigns? "
                    r"combat damage equal to its toughness rather than its power\.?$", unit.raw, re.I):
        return None
    return CardOut(ctx["id"], [f'static("{ctx["id"]}", "assigns_combat_damage_as_toughness")'], "static")


def _attacks_each_combat(unit, ctx):
    """'~ attacks each combat if able.' — a combat requirement (§508)."""
    if not re.match(r"^~ attacks each combat if able\.?$", unit.raw):
        return None
    cid = ctx["id"]
    return CardOut(cid, [f'attacks_each_combat("{cid}")'], "attacks_each_combat")


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


_PATTERNS = [_kw_line, _typecycling, _prototype, _escape, _kw_param, _specialize, _ticket_pt,
             _teamwork, _sticker, _assemble_contraption, _spellbook,
             _starting_intensity, _intensify_static, _augment, _poison_tolerance, _ready_to_run,
             _leveler, _station_band, _painland, _enters_prepared, _can_block_additional,
             _cost_modifier, _class_level, _cda, _cast_restriction, _etb_tapped, _enters_with_counters,
             _doesnt_untap,
             _attacks_each_combat, _assigns_toughness, _etb_choose, _as_enters, _static_player, _exert, _enter_as_copy,
             _escapes_with, _assign_damage_unblocked, _cast_as_flash, _alt_cost, _card_static, _mana_rider,
             _cant_regenerate,
             _additional_cost, _grant_quoted_to_set, _as_long_as, _static_pt, _anthem_conjunct,
             _granted_ability, _grant_kw_and_ability, _static_grant, _static_conjuncts, _enters_tapped_others,
             _ability_activation_static, _modal, _mode_option, _cant, _combat_restriction,
             _loyalty, _saga_chapter, _mana_ability, _replacement, _triggered, _activated, _spell,
             _static_control, _prevent_static, _land_type_set, _damage_redirect, _damage_multiplier,
             _life_floor, _static_effect]

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


# words a legendary's short name must NOT be before we dare substitute it with ~ (a self-reference):
# §702 keywords, the five colours, card types/supertypes, and a few common nouns that double as names.
@functools.lru_cache(maxsize=1)
def _shortname_blocklist() -> frozenset:
    types = {"creature", "artifact", "enchantment", "land", "planeswalker", "instant", "sorcery",
             "battle", "legendary", "basic", "snow", "token", "permanent", "spell", "player", "card",
             "wall", "fog", "counterspell", "shatter", "ornithopter", "juggernaut", "control"}
    return frozenset(ground.keyword_abilities()) | ground.colors() | types


def _short_name(card) -> str | None:
    """The self-reference short name of a legendary card (the part before the first comma), or None if
    it's missing, too short, or collides with a rules word (so substituting ~ would be unsafe)."""
    if "Legendary" not in (card.get("supertypes") or []):
        return None
    short = re.split(r",", card.get("name", ""), 1)[0].strip()
    if len(short) < 3 or ground.slug(short) in _shortname_blocklist():
        return None
    # also block when ANY word of a multi-word short name is a rules word ('Wall of Omens' -> 'Wall')
    if any(ground.slug(w) in _shortname_blocklist() for w in short.split()):
        return None
    return short


def transpile_unit(unit, ctx) -> "CardOut | None":
    """Interpret one ability unit; first faithful pattern wins, else None (abstain).
    Fallback: a multi-sentence line whose EVERY sentence is independently a whole ability (e.g.
    '~ enters tapped. As it enters, choose a color.') — interpret each and merge, no half-credit."""
    stripped = _strip_ability_word(unit.raw)
    u = unit if stripped == unit.raw else dataclasses.replace(unit, raw=stripped)
    out = _try_patterns(u, ctx)
    if not out:
        out = _multi_sentence(u, ctx)
    # SHORT-NAME FALLBACK (all-or-nothing): a legendary that refers to itself by its short name
    # ('Heliod isn't a creature', 'Ghave enters with five +1/+1 counters') never matched ~-anchored
    # patterns. Only on a FAILED parse, substitute the (collision-guarded) short name with ~ and retry;
    # keep the result only if it now grounds, so a passing card can never regress.
    if not out:
        short = _short_name(ctx.get("card") or {})
        if short and re.search(r"\b" + re.escape(short) + r"\b", u.raw):
            sub_raw = re.sub(r"\b" + re.escape(short) + r"(?:'s)?\b",
                             lambda m: "~'s" if m.group(0).endswith("'s") else "~", u.raw)
            su = dataclasses.replace(u, raw=sub_raw)
            out = _try_patterns(su, ctx) or _multi_sentence(su, ctx)
    if out:
        out.template = unit.template
        return out
    return None


def _multi_sentence(u, ctx) -> "CardOut | None":
    """A multi-sentence line whose EVERY sentence is independently a whole ability — interpret each and
    merge (no half-credit)."""
    sents = _sentences(u.raw)
    if len(sents) < 2:
        return None
    facts, ok = [], True
    for j, sent in enumerate(sents):
        sub = dataclasses.replace(u, raw=sent.rstrip("."))
        so = _try_patterns(sub, {**ctx, "seq": f"{ctx.get('seq', 0)}_{j}"})
        if not so:
            ok = False
            break
        facts += so.facts
    return CardOut(ctx["id"], facts, "multi") if ok else None


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
