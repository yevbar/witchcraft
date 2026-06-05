"""A real spaCy + lark transpiler: English rule sentence -> Datalog.

Pipeline: preprocess() masks formal fragments ({W/U}, URLs, §refs) into single
alphabetic tokens (so spaCy tokenizes/parses deterministically and the fragments
route to their lark grammars via the legend) -> spaCy dependency parse gives the
structure -> small lexicons map English terms to Datalog predicates -> a Datalog
rule. It transpiles the FORMULAIC families and returns None on non-formulaic prose.

Patterns covered:
  - SBA value condition (§704.5a/c/f/i): "If a [subj] has [attr] [value], [outcome]."
  - SBA ceases (§704.5d):               "If a [subj] is in a zone other than the [z], it ceases to exist."
  - keyword action (§701.8a/13a):       "To [verb] ..., move it ... to [dest]."
  - keyword prohibition (§702.3b):      "A creature with [keyword] can't [verb]."
"""

from __future__ import annotations

from dataclasses import dataclass

import spacy
from lark import Lark, Transformer

from preprocess import preprocess

_NLP = spacy.load("en_core_web_sm")

# --- lark grammar for the value-condition sublanguage ------------------------
# The dependency patterns hand lark a comparison phrase ("0 or less", "greater than
# five", "at least three") and lark evaluates it into a (operator, value) term.
_VALUE_GRAMMAR = r"""
    ?start: cond
    cond: "greater" "than" "or" "equal" "to" NUM -> ge
        | "less" "than" "or" "equal" "to" NUM    -> le
        | "greater" "than" NUM   -> gt
        | "more" "than" NUM      -> gt
        | "less" "than" NUM      -> lt
        | "fewer" "than" NUM     -> lt
        | "at" "least" NUM       -> ge
        | "at" "most" NUM        -> le
        | "exactly" NUM          -> eq
        | NUM "or" "less"        -> le
        | NUM "or" "fewer"       -> le
        | NUM "or" "more"        -> ge
        | NUM "or" "greater"     -> ge
        | NUM                    -> eq
    NUM: /[0-9]+/ | "zero" | "one" | "two" | "three" | "four" | "five" | "six"
       | "seven" | "eight" | "nine" | "ten" | "eleven" | "twelve" | "thirteen"
       | "fourteen" | "fifteen" | "sixteen" | "seventeen" | "eighteen"
       | "nineteen" | "twenty"
    %ignore " "
"""
_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
# comparison words the dependency patterns gather (with the number) into a lark phrase.
_CMP_WORDS = {"greater", "less", "fewer", "more", "than", "at", "least", "most", "or", "equal", "to", "exactly"}


def _num(tok) -> int:
    s = str(tok)
    return int(s) if s.isdigit() else _WORDS[s]


class _ToCond(Transformer):
    def le(self, x): return ("<=", _num(x[0]))
    def ge(self, x): return (">=", _num(x[0]))
    def lt(self, x): return ("<", _num(x[0]))
    def gt(self, x): return (">", _num(x[0]))
    def eq(self, x): return ("=", _num(x[0]))


_VALUE = Lark(_VALUE_GRAMMAR, parser="lalr", transformer=_ToCond())

# --- lexicons: English term -> Datalog (the reviewable ontology mapping) ------
SUBJECT_PRED = {"creature": "creature_perm", "planeswalker": "planeswalker_perm", "player": None, "team": None}
ATTR_REL = {"toughness": "toughness", "life": "life", "loyalty": "loyalty", "poison": "poison"}
CEASE_SUBJECT = {"token": "is_token", "copy": "is_copy"}      # §704.5d token / §704.5e copy of a spell
ZONE_NOUNS = {"battlefield", "graveyard", "hand", "library", "exile", "stack", "command"}
STATUS_VERBS = {"tap": "tapped", "untap": "untapped", "transform": "transformed",
                "convert": "transformed", "manifest": "face_down", "cloak": "face_down",
                "exert": "exerted"}                            # keyword action -> resulting status
PROHIBIT_VERBS = {"attack": "cant_attack", "block": "cant_block"}      # active "can't [verb]"
# passive "A [permanent] with [keyword] can't be [verb]" — only UNQUALIFIED verbs (block/enchant/
# equip drop an "except by"/"of the stated quality" qualifier, so they'd be inaccurate as flat facts).
PASSIVE_PROHIBIT = {"destroy": "cant_be_destroyed", "counter": "cant_be_countered",
                    "regenerate": "cant_be_regenerated", "sacrifice": "cant_be_sacrificed"}


@dataclass
class Out:
    rule: str
    datalog: str
    pattern: str


def _root(doc):
    return next((t for t in doc if t.dep_ == "ROOT"), None)


def _child(tok, dep=None, lemma=None):
    for c in tok.children:
        if (dep is None or c.dep_ == dep) and (lemma is None or c.lemma_ == lemma):
            return c
    return None


def _sba_value(rule, doc):
    """§704.5a/c/f/i — "If a [subj] has [attr] [value], [outcome]"."""
    have = next((t for t in doc if t.lemma_ == "have"), None)
    if have is None:
        return None
    subj, dobj = _child(have, "nsubj"), _child(have, "dobj") or _child(have, "obj")
    if subj is None or dobj is None or subj.lemma_.lower() not in SUBJECT_PRED:
        return None
    sub = list(dobj.subtree)
    compound = _child(dobj, "compound")
    attr = compound.lemma_.lower() if dobj.lemma_ in ("counter", "counters") and compound else dobj.lemma_.lower()
    if attr not in ATTR_REL:
        return None
    numtok = next((t for t in sub if t.like_num or t.lemma_.lower() in _WORDS), None)
    if numtok is None:
        return None
    # build the comparison phrase from the subtree's comparison words + the number,
    # in document order, then let lark evaluate it into a (operator, value) term.
    phrase = " ".join(t.text.lower() for t in sub
                      if t.lemma_.lower() in _CMP_WORDS or t is numtok)
    try:
        cmp, value = _VALUE.parse(phrase)
    except Exception:
        return None
    lemmas = {t.lemma_ for t in doc}
    head = "sba_loses" if "lose" in lemmas else "sba_graveyard" if "graveyard" in lemmas else None
    if head is None:
        return None
    subject = subj.lemma_.lower()
    var = "T" if subject == "team" else "P" if subject == "player" else "O"
    body = ([f"{SUBJECT_PRED[subject]}({var})"] if SUBJECT_PRED[subject] else [])
    rel = ATTR_REL[attr]
    body += [f"{rel}({var}, {value})"] if cmp == "=" else [f"{rel}({var}, V)", f"V {cmp} {value}"]
    return Out(rule, f'{head}({var}, "{rule}") :- ' + ", ".join(body) + f".   // {rule}", "sba_value")


# "the same [X]" grouping key -> (relation, mode). mode "controller" means the key is the
# controlling player (controls(Key, P)); "forward" means an attribute (rel(P, Key)).
SAME_KEY_REL = {"name": ("name", "forward"), "player": ("controls", "controller"),
                "controller": ("controls", "controller"), "color": ("color", "forward"),
                "type": ("has_type", "forward")}


def _sba_grouped(rule, doc):
    """§704.5j legend rule (and kin) — "If two or more [type] with the same [X] (and the same [Y])
    ..., the rest are put into graveyards." The dependency parse reads the noun phrase, its
    quantifier ("two or more" -> count >= 2) and each "the same [X]" modifier as a Datalog
    group-by key -> a count-aggregate rule. (The user's noun-phrase-as-condition idea.)"""
    if doc[0].lemma_.lower() != "if" or "graveyard" not in {t.lemma_ for t in doc}:
        return None
    subj = next((t for t in doc if t.dep_ in ("nsubj", "nsubjpass")
                 and any(c.dep_ == "nummod" for c in t.children)), None)
    if subj is None:
        return None
    nm = next(c for c in subj.children if c.dep_ == "nummod")
    if not any(c.lemma_ == "more" for c in nm.children):        # require "[N] or more"
        return None
    thresh = _num(nm) if (str(nm).isdigit() or str(nm).lower() in _WORDS) else 2
    filt = next((c.lemma_.lower() for c in subj.children if c.dep_ == "amod"), None)
    if filt is None:
        return None
    keys = [SAME_KEY_REL[s.head.lemma_.lower()] for s in doc
            if s.lemma_ == "same" and s.head.lemma_.lower() in SAME_KEY_REL]
    if not keys:
        return None
    body, inner = [f"{filt}(P)"], [f"{filt}(P2)"]
    for i, (rel, mode) in enumerate(keys, 1):
        v = f"K{i}"
        body.append(f"{rel}({v}, P)" if mode == "controller" else f"{rel}(P, {v})")
        inner.append(f"{rel}({v}, P2)" if mode == "controller" else f"{rel}(P2, {v})")
    body.append(f"N = count : {{ {', '.join(inner)} }}")
    body.append(f"N >= {thresh}")
    return Out(rule, f'sba_legend_conflict(P) :- ' + ", ".join(body) + f".   // {rule}", "sba_grouped")


def _sba_world(rule, doc):
    """§704.5k world rule — "If two or more permanents have the supertype world, all except the
    one that has had the world supertype for the shortest amount of time are put into graveyards"
    (and, on a tie for shortest, all of them). A supertype FILTER plus a timestamp-extremum
    survivor: shortest-time-held == newest == max timestamp. So a permanent of that supertype is
    put away if there are >=2 and either it isn't the newest, or the newest is tied (so none is
    uniquely newest). Reads the filtering supertype from the dobj of "have"; keys the SBA on it."""
    lemmas = {t.lemma_.lower() for t in doc}
    surface = {t.text.lower() for t in doc}
    if doc[0].lemma_.lower() != "if" or not {"graveyard", "supertype"} <= lemmas or "shortest" not in surface:
        return None
    # the filtering supertype is the dobj of "have" that names the supertype value (not the
    # literal word "supertype"): "...have the supertype world" -> world.
    sup = next((c for t in doc if t.lemma_ == "have"
                for c in t.children if c.dep_ == "dobj" and c.lemma_ != "supertype"), None)
    if sup is None:
        return None
    name = sup.lemma_.lower()                                    # "world"
    rel, head, since = f"{name}_perm", f"sba_{name}_conflict", f"{name}_since"
    common = f"{rel}(P), C = count : {{ {rel}(_) }}, C >= 2, Mx = max T : {{ {rel}(Q), {since}(Q, T) }}"
    older = f"{head}(P) :- {common}, {since}(P, Ts), Ts < Mx.   // {rule} (not the newest)"
    tie = f"{head}(P) :- {common}, NC = count : {{ {rel}(R), {since}(R, Mx) }}, NC >= 2.   // {rule} (tie for newest)"
    return Out(rule, older + "\n" + tie, "sba_world")


def _sba_scheme(rule, doc):
    """§704.6e schemes SBA — "if a non-ongoing scheme card is face up in the command zone, and no
    triggered abilities of any scheme are on the stack or waiting..., that scheme card is turned
    face down and put on the bottom of its owner's scheme deck." So a face-up scheme in the command
    zone is retired UNLESS it's ongoing (the ongoing supertype's interpreted exemption, scheme_exempt)
    or a scheme's triggered ability is still pending. The 'non-ongoing' modifier is the exemption hook."""
    lemmas = {t.lemma_.lower() for t in doc}
    root = next((t for t in doc if t.dep_ == "ROOT"), None)
    if not ({"scheme", "command", "face"} <= lemmas) or root is None or root.lemma_ != "turn":
        return None
    body = ["is_scheme(O)", "face_up(O)", 'in_zone(O, "command")', "!scheme_exempt(O)", '!scheme_trigger_pending("yes")']
    return Out(rule, "sba_scheme_retire(O) :- " + ", ".join(body) + f".   // {rule}", "sba_scheme")


def _sba_aggregate_loss(rule, doc):
    """§704.6c commander damage — "a player who's been dealt N or more [quantity] by the same
    [source] loses": a SUM aggregate grouped by the 'same [source]'. Sibling of the legend
    rule's count aggregate (the user's noun-phrase-as-condition idea, with sum)."""
    root = _root(doc)
    if root is None or root.lemma_ != "lose":
        return None
    dealt = next((t for t in doc if t.lemma_ == "deal" and t.dep_ == "relcl"), None)
    dobj = _child(dealt, "dobj") if dealt else None
    nm = _child(dobj, "nummod") if dobj else None
    if nm is None or not any(c.lemma_ == "more" for c in nm.children):
        return None
    by = _child(dealt, "prep", "by")
    src = _child(by, "pobj") if by else None
    if src is None or not any(c.lemma_ == "same" for c in src.children):
        return None
    comp = _child(dobj, "compound")
    quantity = f"{comp.lemma_.lower()}_{dobj.lemma_.lower()}" if comp else dobj.lemma_.lower()
    grouper, thresh = src.lemma_.lower(), _num(nm)
    q = f"dealt_{quantity}"
    return Out(rule, f'sba_loses(P, "{rule}") :- {grouper}(Src), {q}(Src, P, _), '   # ground P in the outer scope
               f'D = sum X : {{ {q}(Src, P, X) }}, D >= {thresh}.   // {rule}', "sba_aggregate")


def _sba_lethal(rule, doc):
    """§704.5g/h — lethal damage: "If a creature has toughness > 0 [and marked >= toughness /
    dealt damage by deathtouch], it is destroyed." Damage tracking left as engine predicates."""
    if doc[0].lemma_.lower() != "if":                          # the SBA conditional form ("If a creature ...")
        return None
    lem = " ".join(t.lemma_ for t in doc).lower()
    if "creature" not in lem or "toughness" not in lem or "destroy" not in lem:
        return None
    if "deathtouch" in lem:                                    # §704.5h
        body = "creature_perm(O), toughness(O, T), T > 0, dealt_deathtouch_damage(O)"
    elif "lethal" in lem or "mark" in lem:                     # §704.5g
        body = "creature_perm(O), toughness(O, T), T > 0, marked(O, D), D >= T"
    else:
        return None
    return Out(rule, f'sba_graveyard(O, "{rule}") :- {body}.   // {rule}', "sba_lethal")


def _damage_result(rule, doc):
    """§120.3 — "Damage dealt to a [recipient] [by a source with/without X] causes [result]".
    Captures the damage system as damage_result(recipient, source_quality, result)."""
    root = _root(doc)
    if root is None or root.lemma_ != "deal" or not any(c.dep_ == "nsubj" and c.lemma_ == "damage" for c in root.children):
        return None
    lem = " ".join(t.lemma_ for t in doc).lower()
    cause = next((t for t in doc if t.lemma_ == "cause"), None)
    if cause is None or any(c.dep_ == "neg" for c in cause.children):   # canonical "...causes..." (not "doesn't cause")
        return None
    to = _child(root, "prep", "to")                            # recipient from the dependency parse
    recipient = _child(to, "pobj").lemma_.lower() if (to and _child(to, "pobj")) else "any"
    if recipient not in ("any", "player", "creature", "planeswalker", "battle", "permanent", "object"):
        return None
    txt = doc.text
    quals = [q for q in ("infect", "wither", "lifelink", "toxic", "deathtouch") if q in lem]
    quality = (("no_" if ("without" in lem or "neither" in lem) else "") + "_or_".join(quals)) if quals else "any"
    result = ("poison" if "poison" in lem else "gain_life" if "gain" in lem else
              "lose_life" if "lose" in lem and "life" in lem else "remove_loyalty" if "loyalty" in lem else
              "remove_defense" if "defense" in lem else "m1m1" if "-1/-1" in txt else
              "marked" if "mark" in lem else None)
    if result is None:
        return None
    return Out(rule, f'damage_result("{recipient}", "{quality}", "{result}").   // {rule}', "damage_result")


def _sba_attachment(rule, doc):
    """§704.5m/n/p — "If a[n] [type] is [illegally] attached ..., it is put into its owner's
    graveyard / becomes unattached." The attachment condition is left as a predicate the
    engine defines (illegally_attached / attached)."""
    if not any(t.lemma_ == "attach" for t in doc):
        return None
    subj = next((t for t in doc if t.dep_ == "nsubjpass"), None)
    if subj is None or subj.lemma_.lower() not in ("aura", "equipment", "fortification", "battle", "creature"):
        return None
    lem = " ".join(t.lemma_ for t in doc).lower()
    cond = "illegally_attached" if "illegal" in lem else "attached"
    head = "sba_graveyard" if "graveyard" in lem else "sba_unattach" if "unattach" in lem else None
    if head is None:
        return None
    typ = subj.lemma_.lower()
    return Out(rule, f'{head}(O, "{rule}") :- is_{typ}(O), {cond}(O).   // {rule}', "sba_attachment")


def _sba_ceases(rule, doc):
    """§704.5d — "If a [subj] is in a zone other than the [zone], it ceases to exist"."""
    root = _root(doc)
    if root is None or root.lemma_ != "cease":
        return None
    isv = _child(root, "advcl", "be")
    subj = _child(isv, "nsubj") if isv else None
    than = next((t for t in doc if t.lemma_ == "than"), None)
    excl = _child(than, "pobj") if than else None
    if subj is None or excl is None or subj.lemma_.lower() not in CEASE_SUBJECT:
        return None
    pred, zone = CEASE_SUBJECT[subj.lemma_.lower()], excl.lemma_.lower()
    return Out(rule, f'sba_ceases(O) :- {pred}(O), in_zone(O, Z), Z != "{zone}".   // {rule}', "sba_ceases")


def _zone(pobj):
    """Resolve a prepositional object to a canonical zone name ("its owner's
    graveyard"->graveyard, "the exile zone"->exile), or None if it isn't a zone."""
    if pobj is None:
        return None
    if pobj.lemma_ in ZONE_NOUNS:
        return pobj.lemma_
    if pobj.lemma_ == "zone":                                   # "the exile zone"
        return next((c.lemma_ for c in pobj.children if c.dep_ in ("compound", "amod") and c.lemma_ in ZONE_NOUNS), None)
    return None


def _keyword_action(rule, doc):
    """§701.8a/9a/13a/21a — "To [verb] a [obj], move it from [zone] to [zone]"."""
    root = _root(doc)
    if root is None or root.lemma_ != "move":
        return None
    verb = _child(root, "advcl")
    if verb is None or not verb.lemma_.isalpha():
        return None
    if not any(c.dep_ == "aux" and c.lemma_ == "to" for c in verb.children):
        return None                                            # require the "To [verb]" infinitive (not "As X begins")
    to_p = _child(root, "prep", "to")
    to_z = _zone(_child(to_p, "pobj")) if to_p else None
    if to_z is None:
        return None
    from_p = _child(root, "prep", "from")
    from_z = (_zone(_child(from_p, "pobj")) if from_p else None) or "any"
    return Out(rule, f'zone_change(O, "{from_z}", "{to_z}") :- do_{verb.lemma_}(O).   // {rule}', "keyword_action")


def _status_action(rule, doc):
    """§701.26a/26b/27a/40a — "To [verb] a [obj], turn/rotate it ..." -> status(O, ...)."""
    root = _root(doc)
    if root is None or root.lemma_ not in ("turn", "rotate"):
        return None
    verb = _child(root, "advcl")
    if verb is None or not any(c.dep_ == "aux" and c.lemma_ == "to" for c in verb.children):
        return None
    if verb.lemma_ not in STATUS_VERBS:
        return None
    return Out(rule, f'status(O, "{STATUS_VERBS[verb.lemma_]}") :- do_{verb.lemma_}(O).   // {rule}', "status_action")


def _keyword_class(rule, doc):
    """§702.Na — "[Keyword] is a [type] ability." -> keyword_class(name, type) fact.
    Generates the keyword taxonomy (static/triggered/activated/spell/...) from the text."""
    root = _root(doc)
    if root is None or root.lemma_ != "be":
        return None
    attr, subj = _child(root, "attr"), _child(root, "nsubj")
    if attr is None or subj is None or attr.lemma_ != "ability" or subj.pos_ == "PRON":
        return None                                            # reject "This/It is a ... ability" (gerund keywords like "Flying" are VERB, keep them)
    if subj.lemma_ == "ability":                               # "An activated ability is..." defines ability, not a keyword
        return None
    typ = next((c.text.lower() for c in attr.children if c.dep_ in ("amod", "compound")), None)
    if typ is None:                                            # type is amod ("static") or compound ("evasion ability")
        return None
    pre = [c.text.lower() for c in subj.children if c.dep_ in ("amod", "compound")]
    name = "_".join(pre + [subj.text.lower()])
    return Out(rule, f'keyword_class("{name}", "{typ}").   // {rule}', "keyword_class")


def _prohibition(rule, doc):
    """§702.3b — "A creature with [keyword] can't [verb]"."""
    root = _root(doc)
    if root is None or root.lemma_ not in PROHIBIT_VERBS:
        return None
    if not any(c.dep_ == "neg" for c in root.children):
        return None
    subj = _child(root, "nsubj")
    with_ = _child(subj, "prep", "with") if subj else None
    kw = _child(with_, "pobj") if with_ else None
    if kw is None:
        return None
    return Out(rule, f'{PROHIBIT_VERBS[root.lemma_]}(C) :- has_keyword(C, "{kw.lemma_}").   // {rule}', "prohibition")


def _passive_prohibition(rule, doc):
    """§702.12b — "A permanent with [keyword] can't be [verb]" (passive)."""
    root = _root(doc)
    if root is None or root.lemma_ not in PASSIVE_PROHIBIT:
        return None
    if not any(c.dep_ == "neg" for c in root.children) or not any(c.dep_ == "auxpass" for c in root.children):
        return None
    subj = _child(root, "nsubjpass")
    with_ = _child(subj, "prep", "with") if subj else None
    kw = _child(with_, "pobj") if with_ else None
    if kw is None:
        return None
    return Out(rule, f'{PASSIVE_PROHIBIT[root.lemma_]}(C) :- has_keyword(C, "{kw.lemma_}").   // {rule}', "passive_prohibition")


_QUAL_CONDITION = (" unless ", " if ", " while ", " until ", " once ", " as long as ")


def _pobj_head(prep):
    """The head noun of a prep/agent phrase ('by creatures …' -> 'creature'), or '-'."""
    pobj = _child(prep, "pobj") or _child(prep, "obj")
    return pobj.lemma_.lower() if pobj is not None else "-"


def _masked(tok):
    """True if `tok` is a preprocess placeholder for a masked formal fragment (a mana symbol, etc.),
    so it must not be emitted as a subject/object — its text is a random legend key, not a word."""
    return tok.text in _LEGEND


def _passive(rule, doc):
    """Bare passive "[subject] is/are [verb]ed [by/as] …" -> derived(subject, action, complement_kind,
    complement). The derivation family: how a value/object is determined, treated, produced, chosen.
    The complement is classified like a modal qualifier, plus 'as' (treated AS X). Modal passives
    ('can't/may be …') belong to restriction/permission and are excluded; SBA/damage passives are
    caught earlier. Subjects/complements that are masked placeholders are dropped."""
    root = _root(doc)
    if root is None or root.pos_ != "VERB":
        return None
    kids = list(root.children)
    if not any(c.dep_ == "auxpass" and c.lemma_ == "be" for c in kids):
        return None
    if any(c.lemma_ in _MODALS and c.dep_ in ("aux", "auxpass") for c in kids):
        return None
    subj = next((c for c in kids if c.dep_ == "nsubjpass"), None)
    if subj is None or subj.pos_ not in ("NOUN", "PROPN") or _masked(subj):
        return None
    agent = next((c for c in kids if c.dep_ == "agent"), None)
    asp = next((c for c in kids if c.dep_ == "prep" and c.lemma_ == "as"), None)
    prep = next((c for c in kids if c.dep_ == "prep"), None)
    if agent is not None:
        kind, comp = "by", _pobj_head(agent)
    elif asp is not None:
        kind, comp = "as", _pobj_head(asp)
    elif any(c.dep_ == "advcl" for c in kids):
        kind, comp = "condition", "-"
    elif prep is not None:
        kind, comp = "scope", _pobj_head(prep)
    else:
        kind, comp = "absolute", "-"
    if comp in _LEGEND:                                   # complement is a masked placeholder -> drop the value
        comp = "-"
    return Out(rule, f'derived("{subj.lemma_.lower()}", "{root.lemma_.lower()}", "{kind}", "{comp}").   // {rule}',
               "passive")


def _existential(rule, doc):
    """"There is/are [N] [noun]" -> count_of(noun, n). The count subset of existentials (the rest —
    'several ways', 'no restrictions', 'different kinds' — have no number and are abstained)."""
    if doc[0].lemma_.lower() != "there" or doc[0].dep_ != "expl":
        return None
    root = _root(doc)
    if root is None or root.lemma_ != "be":
        return None
    head = next((c for c in root.children if c.dep_ in ("attr", "nsubj") and c.pos_ in ("NOUN", "PROPN")), None)
    if head is None or _masked(head):
        return None
    num = next((c for c in head.children if c.dep_ == "nummod"), None)
    if num is None:
        return None
    n = int(num.text) if num.text.isdigit() else _WORDS.get(num.lemma_.lower())
    if n is None:
        return None
    pre = [c.text.lower() for c in head.children if c.dep_ in ("amod", "compound")]
    name = "_".join(pre + [head.lemma_.lower()])
    return Out(rule, f'count_of("{name}", {n}).   // {rule}', "existential")


_CAP_VERBS = {"instruct", "allow", "require", "permit", "force", "enable", "cause"}


def _capability(rule, doc):
    """"[subject] instructs/allows/requires a player TO [verb]" -> grants(subject, modal_verb,
    granted_action). The effect-capability family: the meaningful object is the INFINITIVE the
    player is told/allowed to do (instruct->create, allow->take), captured from the to-complement."""
    root = _root(doc)
    if root is None or root.lemma_ not in _CAP_VERBS or root.pos_ != "VERB":
        return None
    kids = list(root.children)
    if any(c.dep_ in ("aux", "auxpass", "neg") for c in kids):
        return None
    subj = next((c for c in kids if c.dep_ == "nsubj"), None)
    if subj is None or subj.pos_ not in ("NOUN", "PROPN") or _masked(subj):
        return None
    act = next((c for c in kids if c.dep_ in ("xcomp", "advcl", "ccomp", "acl") and c.pos_ == "VERB"
                and any(g.lemma_ == "to" and g.dep_ == "aux" for g in c.children)), None)
    if act is None:
        return None
    return Out(rule, f'grants("{subj.lemma_.lower()}", "{root.lemma_.lower()}", "{act.lemma_.lower()}").   // {rule}',
               "capability")


def _obligation(rule, doc):
    """"[subject] must [verb]" requirement, classified by qualifier — the obligation mirror of
    _permission/_restriction -> requirement(subject, action, qualifier_kind, qualifier). 'must not'
    is negated (a prohibition) and excluded."""
    root = _root(doc)
    if root is None or root.pos_ != "VERB":
        return None
    kids = list(root.children)
    if not any(c.lemma_ == "must" and c.dep_ in ("aux", "auxpass") for c in kids):
        return None
    if any(c.dep_ == "neg" for c in kids):
        return None
    subj = next((c for c in kids if c.dep_ in ("nsubj", "nsubjpass")), None)
    if subj is None or subj.pos_ not in ("NOUN", "PROPN"):
        return None
    action = ("be_" if any(c.dep_ == "auxpass" for c in kids) else "") + root.lemma_.lower()
    kind, qual = _classify_qualifier(root, doc)
    return Out(rule, f'requirement("{subj.lemma_.lower()}", "{action}", "{kind}", "{qual}").   // {rule}',
               "obligation")


_REL_VERBS = {"refer", "mean", "represent", "include", "contain", "consist", "comprise", "cause", "affect"}


def _relation(rule, doc):
    """Declarative relational SVO "[subject] refers to / means / represents / contains [object]" ->
    relation(subject, verb, object). The reference/composition family — semantic links the sweep
    missed. Handles 'refers TO' / 'consists OF' (prep object). Requires a clean noun subject AND
    object (the relation needs both ends); passive/modal/negated forms are excluded as lossy."""
    root = _root(doc)
    if root is None or root.lemma_ not in _REL_VERBS or root.pos_ != "VERB":
        return None
    kids = list(root.children)
    if any(c.dep_ in ("aux", "auxpass") for c in kids) or any(c.dep_ == "neg" for c in kids):
        return None
    subj = next((c for c in kids if c.dep_ == "nsubj"), None)
    if subj is None or subj.pos_ not in ("NOUN", "PROPN") or _masked(subj):
        return None
    obj = next((c for c in kids if c.dep_ in ("dobj", "obj")), None)
    if obj is None:                                        # 'refers TO x', 'consists OF x'
        prep = next((c for c in kids if c.dep_ == "prep"), None)
        obj = next((c for c in prep.children if c.dep_ == "pobj"), None) if prep is not None else None
    if obj is None or obj.pos_ not in ("NOUN", "PROPN") or _masked(obj):
        return None
    return Out(rule, f'relation("{subj.lemma_.lower()}", "{root.lemma_.lower()}", "{obj.lemma_.lower()}").   // {rule}',
               "relation")


_EFFECT_VERBS = {"become", "get", "gain", "lose", "set", "change", "switch", "remove", "add"}


def _effect(rule, doc):
    """Active effect verb "[subject] becomes/gets/loses/changes [object]" -> effect(subject, verb,
    object, polarity). The game-state-change family; the verb carries the direction (gain vs lose),
    and polarity = yes | no captures negation ('costs don't change the mana cost'). Passive ('is
    changed') goes to _passive; modal ('may become') to permission. Masked objects/subjects dropped."""
    root = _root(doc)
    if root is None or root.lemma_ not in _EFFECT_VERBS or root.pos_ != "VERB":
        return None
    kids = list(root.children)
    if any(c.dep_ == "auxpass" for c in kids):             # passive -> _passive
        return None
    if any(c.lemma_ in _MODALS and c.dep_ in ("aux", "auxpass") for c in kids):
        return None
    subj = next((c for c in kids if c.dep_ == "nsubj"), None)
    if subj is None or subj.pos_ not in ("NOUN", "PROPN") or _masked(subj):
        return None
    obj = next((c for c in kids if c.dep_ in ("dobj", "obj", "attr", "acomp")), None)
    o = (obj.lemma_.lower() if obj is not None and obj.pos_ in ("NOUN", "PROPN", "ADJ")
         and not _masked(obj) else "-")
    polarity = "no" if any(c.dep_ == "neg" for c in kids) else "yes"
    return Out(rule, f'effect("{subj.lemma_.lower()}", "{root.lemma_.lower()}", "{o}", "{polarity}").   // {rule}',
               "effect")


def _classify_qualifier(root, doc):
    """The qualifier KIND + head of a modal clause, shared by _restriction and _permission.
    kind = except | by | condition | qualified | scope | frequency | absolute; the head is the
    content noun of the qualifying phrase, else '-'. Comparatives/quantifiers demote 'absolute'."""
    kids = list(root.children)
    low = doc.text.lower()
    agent = next((c for c in kids if c.dep_ == "agent"), None) or \
        next((c for c in kids if c.dep_ == "prep" and c.lemma_ == "by"), None)
    kind, qual = "absolute", "-"
    if "except" in low:
        kind, qual = "except", (_pobj_head(agent) if agent is not None else "-")
    elif agent is not None:
        kind, qual = "by", _pobj_head(agent)
    elif any(c.dep_ == "advcl" for c in kids) or any(w in low for w in _QUAL_CONDITION):
        kind = "condition"
    elif any(t.dep_ in ("relcl", "acl") for t in doc):
        kind = "qualified"
    elif (prep := next((c for c in kids if c.dep_ == "prep"), None)) is not None:
        kind, qual = "scope", _pobj_head(prep)
    elif any(c.dep_ in ("advmod", "npadvmod") for c in kids):
        kind = "frequency"
    if kind == "absolute" and any(w in low for w in
                                  (" greater", " more ", " fewer", " less ", " than ", "certain", "normally", "specific")):
        kind = "qualified"
    return kind, qual


def _restriction(rule, doc):
    """Any "[subject] can't [verb]" prohibition, CLASSIFIED by its qualifier rather than abstained.

    The qualified prohibitions cluster by part of speech, so we record the qualifier instead of
    flattening (which would be lossy) -> restriction(subject, action, qualifier_kind, qualifier):
      except clause  -> kind=except     (evasion: "can't be blocked except by creatures with …")
      agent (by X)   -> kind=by         ("can't be targeted by spells with …")
      if/unless/while-> kind=condition
      relative clause-> kind=qualified  ("can't … an object THAT'S not a creature")
      prep phrase    -> kind=scope       ("can't enchant an object OUTSIDE its range")
      adverb         -> kind=frequency   ("can't be chosen MULTIPLE times")
      none           -> kind=absolute
    The 4th field is the head of the qualifying phrase when it's a content noun, else '-'."""
    root = _root(doc)
    if root is None or root.pos_ != "VERB":
        return None
    kids = list(root.children)
    if not any(c.lemma_ in ("can", "could") and c.dep_ in ("aux", "auxpass") for c in kids):
        return None
    if not any(c.dep_ == "neg" for c in kids):
        return None
    subj = next((c for c in kids if c.dep_ in ("nsubj", "nsubjpass")), None)
    if subj is None or subj.pos_ not in ("NOUN", "PROPN"):    # need a concrete noun subject
        return None
    action = ("be_" if any(c.dep_ == "auxpass" for c in kids) else "") + root.lemma_.lower()
    kind, qual = _classify_qualifier(root, doc)
    return Out(rule, f'restriction("{subj.lemma_.lower()}", "{action}", "{kind}", "{qual}").   // {rule}',
               "restriction")


def _permission(rule, doc):
    """"[subject] may [verb]" permission, CLASSIFIED by its qualifier — the positive mirror of
    _restriction (same parse and qualifier kinds) -> permission(subject, action, qualifier_kind,
    qualifier). 'may not …' is negated (a prohibition), so it's left out."""
    root = _root(doc)
    if root is None or root.pos_ != "VERB":
        return None
    kids = list(root.children)
    if not any(c.lemma_ == "may" and c.dep_ in ("aux", "auxpass") for c in kids):
        return None
    if any(c.dep_ == "neg" for c in kids):                 # "may not" is a prohibition, not a permission
        return None
    subj = next((c for c in kids if c.dep_ in ("nsubj", "nsubjpass")), None)
    if subj is None or subj.pos_ not in ("NOUN", "PROPN"):
        return None
    action = ("be_" if any(c.dep_ == "auxpass" for c in kids) else "") + root.lemma_.lower()
    kind, qual = _classify_qualifier(root, doc)
    return Out(rule, f'permission("{subj.lemma_.lower()}", "{action}", "{kind}", "{qual}").   // {rule}',
               "permission")


_MODALS = {"may", "must", "can", "could", "will", "shall", "would", "should"}


def _possession(rule, doc):
    """Bare declarative "[subject] has/have [no] [characteristic]" -> has_property(subject, property, present).

    The possessive family: an entity's characteristics. Faithful by capturing POLARITY (present =
    yes | no), so "a colorless object has no color" never becomes a false has(object, color). Only a
    main-verb 'have' counts — perfect-tense ('has been …'), 'has to', modal possession ('may/must
    have …', left to the permission/obligation patterns), and clausal 'have X choose' are excluded."""
    root = _root(doc)
    if root is None or root.lemma_ != "have" or root.pos_ not in ("VERB", "AUX"):
        return None
    kids = list(root.children)
    if any(c.dep_ == "aux" and (c.lemma_ == "to" or c.lemma_ in _MODALS) for c in kids):
        return None                                        # 'has to', 'may/must have'
    if any(c.dep_ in ("xcomp", "ccomp") for c in kids):    # 'have each player choose …'
        return None
    subj = next((c for c in kids if c.dep_ == "nsubj"), None)
    if subj is None or subj.pos_ not in ("NOUN", "PROPN"):
        return None
    dobj = next((c for c in kids if c.dep_ in ("dobj", "obj", "attr")), None)
    if dobj is None or dobj.pos_ not in ("NOUN", "PROPN"):
        return None
    neg = any(c.dep_ == "neg" for c in kids) or any(c.lemma_ == "no" for c in dobj.children if c.dep_ == "det")
    present = "no" if neg else "yes"
    return Out(rule, f'has_property("{subj.lemma_.lower()}", "{dobj.lemma_.lower()}", "{present}").   // {rule}',
               "possession")


# Subordinate-clause marks that introduce a trigger/condition clause — the cross-cutting two-clause
# family. Beyond if/when (condition/event), the temporal (as/before/after/during/while/once/until)
# and causal (because/since) marks give the same trigger->outcome structure across the rulebook.
_TRIGGER_MARKS = ("if", "when", "whenever", "as", "before", "after", "during",
                  "while", "once", "until", "unless", "because", "since")
_MARK_KIND = {"when": "trigger", "whenever": "trigger", "as": "temporal", "before": "temporal",
              "after": "temporal", "during": "temporal", "while": "temporal", "once": "temporal",
              "until": "temporal", "because": "causal", "since": "causal"}


def _clause_subject(verb):
    """The noun subject lemma of a clause verb, or '-' (pronoun/none)."""
    s = next((c for c in verb.children if c.dep_ in ("nsubj", "nsubjpass")), None)
    return s.lemma_.lower() if s is not None and s.pos_ in ("NOUN", "PROPN") else "-"


def _conditional(rule, doc):
    """"If/When [trigger clause], [outcome clause]" -> conditional(trigger_subj, trigger_verb,
    outcome_subj, outcome_verb, kind). The big two-clause family: rather than interpret the full
    semantics, capture the STRUCTURE — which clause subject/verb triggers which outcome — exactly as
    the restriction pattern captures a prohibition's qualifier. kind classifies the trigger:
    replacement ("would … instead"), trigger ("When/Whenever …"), or condition ("If …")."""
    root = _root(doc)
    if root is None or root.pos_ != "VERB":
        return None
    trig = None                                            # the if/when adverbial clause
    for c in root.children:
        if c.dep_ in ("advcl", "ccomp"):
            if any(t.dep_ == "mark" and t.lemma_.lower() in _TRIGGER_MARKS for t in c.subtree):
                trig = c
                break
    if trig is None:
        return None
    ts, os_ = _clause_subject(trig), _clause_subject(root)
    if ts == "-" and os_ == "-":                           # need at least one concrete clause subject
        return None
    sub = " ".join(t.text.lower() for t in trig.subtree)
    mark = next((t.lemma_.lower() for t in trig.subtree if t.dep_ == "mark" and t.lemma_.lower() in _TRIGGER_MARKS), "if")
    kind = ("replacement" if "would" in sub and "instead" in doc.text.lower()
            else _MARK_KIND.get(mark, "condition"))
    return Out(rule, f'conditional("{ts}", "{trig.lemma_.lower()}", "{os_}", '
                     f'"{root.lemma_.lower()}", "{kind}").   // {rule}', "conditional")


def _qual_list(q, doc, root):
    """A quality head + its conjuncts ("flying and/or reach"), but only if every
    one is a clean single keyword. Rejects comparatives like "greater power"
    (an amod child) so power/toughness restrictions fall to the long tail."""
    quals = [q] + [t for t in doc if t.dep_ == "conj" and t.i > q.i and t.head in (q, root)]
    if any(not t.is_alpha or any(c.dep_ == "amod" for c in t.children) for t in quals):
        return None
    return quals


def _evasion(rule, doc):
    """§702.9b/17b/31b/111b — "A creature with [kw] can't be blocked
    except by creatures with [quality(s)]"  (illegal if blocker has none),
    "...by creatures without [quality]"      (illegal if blocker lacks it),
    "...except by two or more creatures"     (illegal if fewer than two)."""
    root = _root(doc)
    if root is None or root.lemma_ != "block":
        return None
    if not any(c.dep_ == "neg" for c in root.children) or not any(c.dep_ == "auxpass" for c in root.children):
        return None
    subj = _child(root, "nsubjpass")
    with_ = _child(subj, "prep", "with") if subj else None
    akw = _child(with_, "pobj") if with_ else None              # the attacker's evasion keyword
    if akw is None:
        return None
    head = f'illegal_block(B, A) :- blocks(B, A), has_keyword(A, "{akw.lemma_}")'

    without = _child(root, "prep", "without")                   # "by creatures without horsemanship"
    if without:
        quals = _qual_list(_child(without, "pobj"), doc, root) if _child(without, "pobj") else None
        if not quals:
            return None
        negs = ", ".join(f'!has_keyword(B, "{t.lemma_}")' for t in quals)
        return Out(rule, f'{head}, {negs}.   // {rule}', "evasion")

    exc = _child(root, "prep", "except")
    by = _child(exc, "prep", "by") if exc else None
    pobj = _child(by, "pobj") if by else None
    if pobj is None:
        return None
    qual = _child(pobj, "prep", "with")                         # "creatures with flying and/or reach"
    if qual:
        quals = _qual_list(_child(qual, "pobj"), doc, root) if _child(qual, "pobj") else None
        if not quals:
            return None
        negs = ", ".join(f'!has_keyword(B, "{t.lemma_}")' for t in quals)
        return Out(rule, f'{head}, {negs}.   // {rule}', "evasion")
    num = _child(pobj, "nummod")                                # "two or more creatures"
    if num and num.lemma_ in _WORDS:
        return Out(rule, f'{head}, n_blockers(A, N), N < {_WORDS[num.lemma_]}.   // {rule}', "evasion")
    # "except by artifact creatures and/or black creatures" — type/color qualifiers
    # carried as compound/amod on the noun(s); illegal if the blocker is none of them.
    nouns = [pobj] + [t for t in doc if t.dep_ == "conj" and t.head == pobj]
    if any(any(c.dep_ == "relcl" for c in n.children) for n in nouns):
        return None                                             # relative clause (intimidate "share a color") -> long tail
    quals = [c.lemma_ for n in nouns for c in n.children if c.dep_ in ("compound", "amod") and c.is_alpha]
    if quals:
        negs = ", ".join(f"!{q}(B)" for q in quals)
        return Out(rule, f'{head}, {negs}.   // {rule}', "evasion")
    return None


ISA_BAD_PRED = {"kind", "number", "part", "set", "type", "group", "amount", "member", "one",
                "thing", "way", "result", "example", "exception", "object", "ability", "symbol"}
ISA_SKIP_START = {"if", "in", "when", "whenever", "while", "as", "because", "unless", "instead",
                  "any", "each", "some", "all"}


def _isa(rule, doc):
    """Genus-differentia definition "A [term] is a [category] ..." -> isa(term, category).
    Strict guards keep it ACCURATE: indefinite subject, nominal predicate, not negated,
    no disjunction, no conjunction in subject/predicate, not a conditional sentence."""
    root = _root(doc)
    if root is None or root.lemma_ != "be" or doc[0].lemma_.lower() in ISA_SKIP_START:
        return None
    if any(c.dep_ == "neg" for c in root.children) or any(t.lemma_ in ("not", "or") for t in doc):
        return None
    subj, attr = _child(root, "nsubj"), _child(root, "attr")
    if subj is None or attr is None or attr.pos_ not in ("NOUN", "PROPN") or attr.lemma_ in ISA_BAD_PRED:
        return None
    if any(c.dep_ in ("conj", "cc") for c in attr.children) or any(c.dep_ in ("conj", "cc") for c in subj.children):
        return None
    if subj.pos_ not in ("NOUN", "PROPN") or _masked(subj) or _masked(attr):    # any noun subject (definite/bare/indefinite)
        return None
    pre = [c.text.lower() for c in subj.children if c.dep_ in ("amod", "compound")]
    name = "_".join(pre + [subj.lemma_.lower()])
    if name == attr.lemma_.lower():
        return None
    return Out(rule, f'isa("{name}", "{attr.lemma_.lower()}").   // {rule}', "isa")


def _symbol_def(rule, doc):
    """§107.5+ — "The [name] symbol is {glyph}." -> symbol(name, glyph). The glyph
    was masked by preprocess; recover it from the legend that transpile_rule set."""
    root = _root(doc)
    if root is None or root.lemma_ != "be":
        return None
    subj, attr = _child(root, "nsubj"), _child(root, "attr")
    if subj is None or attr is None or subj.lemma_ != "symbol":
        return None
    glyph = _LEGEND.get(attr.text)                             # masked token -> original "{T}"
    name = "_".join(c.text.lower() for c in subj.children if c.dep_ in ("amod", "compound"))
    if not glyph or not name:
        return None
    return Out(rule, f'symbol("{name}", "{glyph[0]}").   // {rule}', "symbol_def")


_PATTERNS = [_sba_grouped, _sba_world, _sba_scheme, _sba_aggregate_loss, _sba_lethal, _sba_value, _damage_result, _sba_attachment, _sba_ceases, _keyword_action, _status_action, _evasion, _passive_prohibition, _prohibition, _symbol_def, _keyword_class, _isa, _restriction, _conditional, _possession, _permission, _passive, _effect, _capability, _relation, _obligation, _existential]

_LEGEND: dict = {}                                            # preprocess legend for the sentence under transpilation


def _normalize(s: str) -> str:
    """Normalize curly quotes/apostrophes to ASCII so spaCy tokenizes/parses
    deterministically (e.g. curly "can’t" otherwise breaks the dependency parse)."""
    return s.replace("’", "'").replace("“", '"').replace("”", '"')


def transpile_rule(rule: str, text: str) -> Out | None:
    """Try each pattern on the first sentence; return the Datalog or None."""
    global _LEGEND
    sent = _normalize(text.split(". ")[0])
    masked = preprocess(sent)                    # mask formal fragments for a clean parse
    _LEGEND = masked.legend                      # patterns (e.g. _symbol_def) may resolve masked tokens
    doc = _NLP(masked.text)
    for fn in _PATTERNS:
        out = fn(rule, doc)
        if out is not None:
            return out
    return None


if __name__ == "__main__":
    from rules_parser import split
    doc = split(open("rules.txt", encoding="utf-8").read())
    for s in doc.sections:
        for g in s.groups:
            if g.number in ("701", "702", "704"):
                for r in g.rules:
                    for sr in [r] + r.subrules:
                        out = transpile_rule(sr.number, sr.text)
                        if out:
                            print(f"[{out.pattern:14}] {out.datalog}")
