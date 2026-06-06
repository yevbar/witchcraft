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
    sentence: int = 0          # which sentence of the rule the fact came from (0 = the opening statement)


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


def _clean(tok):
    """A usable noun argument: NOUN/PROPN, not masked, no mask-residue symbols."""
    return (tok is not None and tok.pos_ in ("NOUN", "PROPN") and not _masked(tok)
            and not any(ch in tok.text for ch in "—[](){}"))


def _imperative(rule, doc):
    """Instruction in the imperative mood "[To …,] [verb] [object]" -> action(player, verb, object).
    A base-form (VB) root with NO subject and no auxiliary is an imperative — the rulebook instructs
    the player to do something ("To cast a card …, turn it face down", "Choose targets", "Check
    state-based actions"). The implied subject is the player. Content-agnostic (imperative mood, no
    word list); cross-reference imperatives ('see rule …') are excluded."""
    root = _root(doc)
    if root is None or root.pos_ != "VERB" or root.tag_ != "VB" or root.lemma_ in ("see", "be", "have"):
        return None
    kids = list(root.children)
    if any(c.dep_ in ("nsubj", "nsubjpass", "aux", "auxpass") for c in kids):
        return None
    if not any(c.dep_ in ("dobj", "obj", "dative", "oprd", "prep", "advcl", "advmod", "ccomp", "npadvmod")
               for c in kids):                              # a real instruction has an object/modifier,
        return None                                         # not a bare keyword-name header ("Investigate")
    obj = next((c for c in kids if c.dep_ in ("dobj", "obj")), None)
    o = obj.lemma_.lower() if _clean(obj) else "-"
    return Out(rule, f'action("player", "{root.lemma_.lower()}", "{o}").   // {rule}', "action")


_PARTITIVE_SUBJ = {"most", "some", "part", "rest", "all", "half", "none", "much", "many"}


def _action(rule, doc):
    """Generic active-declarative SVO "[subject] [verb] [object]" -> action(subject, verb, object).
    The coarse catch-all (runs LAST) for declaratives whose verb no specific pattern claims; object
    is '-' when there's no clean noun object. Copula/possession (be/have), passive, modal and
    negated forms are excluded — those belong to the structured patterns ahead of this one."""
    root = _root(doc)
    if root is None or root.pos_ != "VERB" or root.lemma_ in ("be", "have"):
        return None
    kids = list(root.children)
    if any(c.dep_ == "neg" for c in kids) or any(c.dep_ == "auxpass" for c in kids):
        return None
    if any(c.dep_ == "aux" and c.lemma_ not in ("will", "shall") for c in kids):
        return None                                        # only a future-tense aux ('X will cause Y' = 'X causes Y')
    subj = next((c for c in kids if c.dep_ == "nsubj"), None)
    if subj is None:
        return None
    # a partitive-quantifier subject ("Most of the area … represents …") takes its real head from the
    # 'of' object — "most of the area" is about the area. Use the pobj as the subject noun.
    if subj.pos_ in ("ADJ", "DET", "PRON", "NOUN") and subj.lemma_.lower() in _PARTITIVE_SUBJ:
        of = next((c for c in subj.children if c.dep_ == "prep" and c.lemma_ == "of"), None)
        pobj = _child(of, "pobj") if of else None
        subj = pobj if _clean(pobj) else subj
    if not _clean(subj):
        return None
    obj = next((c for c in kids if c.dep_ in ("dobj", "obj")), None)
    o = obj.lemma_.lower() if _clean(obj) else "-"
    return Out(rule, f'action("{subj.lemma_.lower()}", "{root.lemma_.lower()}", "{o}").   // {rule}', "action")


_COMPARE_ADJ = {"same", "different", "greater", "less", "fewer", "equal", "identical", "similar"}
# predicate adjectives that are really the head of an idiom needing a complement ("subject TO the rules",
# "due TO …") — the bare adjective carries no standalone property, so _is_property abstains.
_IDIOM_ADJ = {"subject", "due"}


def _comparison(rule, doc):
    """Copula comparison "[subject] is the same as / different from / greater than [object]" ->
    comparison(subject, relation, object). The equivalence/ordering family; relation is the
    comparative adjective, object the head of the as/from/than/to complement (or '-')."""
    root = _root(doc)
    if root is None or root.lemma_ != "be" or any(c.dep_ == "neg" for c in root.children):
        return None
    subj = next((c for c in root.children if c.dep_ == "nsubj"), None)
    if not _clean(subj):
        return None
    adj = None                                            # the comparative adjective (acomp/attr, or amod of attr)
    for c in root.children:
        if c.dep_ in ("acomp", "attr"):
            if c.lemma_.lower() in _COMPARE_ADJ:
                adj = c
                break
            am = next((g for g in c.children if g.dep_ == "amod" and g.lemma_.lower() in _COMPARE_ADJ), None)
            if am:
                adj = am
                break
    if adj is None:
        return None
    prep = None
    for src in (adj, adj.head, root):
        prep = next((c for c in src.children if c.dep_ == "prep" and c.lemma_ in ("as", "from", "than", "to")), None)
        if prep:
            break
    obj = next((c for c in prep.children if c.dep_ == "pobj"), None) if prep else None
    o = obj.lemma_.lower() if _clean(obj) else "-"
    return Out(rule, f'comparison("{subj.lemma_.lower()}", "{adj.lemma_.lower()}", "{o}").   // {rule}',
               "comparison")


def _is_property(rule, doc):
    """Copula predicate-adjective "[subject] is exempt / optional / independent" -> is_property(subject,
    adjective). The adjectival twin of _isa (which takes a noun predicate); comparative adjectives go
    to _comparison."""
    root = _root(doc)
    # a UNIVERSAL quantifier start ("Each/Every/All X is [adj]") is faithful — the property holds of
    # every instance — so it's allowed here even though _isa abstains on quantified subjects.
    start = doc[0].lemma_.lower()
    if root is None or root.lemma_ != "be" or (start in ISA_SKIP_START and start not in ("each", "every", "all")):
        return None
    if any(c.dep_ == "neg" for c in root.children):
        return None
    subj = next((c for c in root.children if c.dep_ == "nsubj"), None)
    if not _clean(subj):
        return None
    adj = next((c for c in root.children if c.dep_ == "acomp" and c.pos_ == "ADJ"
                and c.lemma_.lower() not in _COMPARE_ADJ and c.lemma_.lower() not in _IDIOM_ADJ
                and not _masked(c)), None)
    if adj is None:
        return None
    conjs = [c for c in adj.children if c.dep_ == "conj"]
    if conjs:
        # a coordinated predicate adjective distributes only under a POSSIBILITY modal ("can/may be
        # beneficial or detrimental" = alternative properties, each holding). A plain copula list ("the
        # colors ARE white, blue, …") is an enumeration of MEMBERS, not properties, so it stays abstained.
        if not any(c.dep_ == "aux" and c.lemma_ in ("can", "may", "could", "might") for c in root.children):
            return None
        adjs = [adj] + conjs
        if any(a.pos_ != "ADJ" or a.lemma_.lower() in _COMPARE_ADJ or _masked(a) for a in adjs):
            return None
        lines = [f'is_property("{subj.lemma_.lower()}", "{a.lemma_.lower()}").' for a in adjs]
        lines[0] += f"   // {rule}"
        return Out(rule, "\n".join(lines), "property")
    return Out(rule, f'is_property("{subj.lemma_.lower()}", "{adj.lemma_.lower()}").   // {rule}', "property")


def _negation(rule, doc):
    """Negated active declarative "[subject] doesn't [verb] [object]" -> negation(subject, verb,
    object). The negative-polarity declaratives not already captured as a polarity field by
    _effect/_possession; modal negation ('can't/may not') and copula 'is not' are excluded."""
    root = _root(doc)
    if root is None or root.pos_ != "VERB" or root.lemma_ == "be":
        return None
    kids = list(root.children)
    if not any(c.dep_ == "neg" for c in kids):
        return None
    if any(c.lemma_ in _MODALS and c.dep_ in ("aux", "auxpass") for c in kids):
        return None
    subj = next((c for c in kids if c.dep_ in ("nsubj", "nsubjpass")), None)
    if not _clean(subj):
        return None
    obj = next((c for c in kids if c.dep_ in ("dobj", "obj")), None)
    o = obj.lemma_.lower() if _clean(obj) else "-"
    return Out(rule, f'negation("{subj.lemma_.lower()}", "{root.lemma_.lower()}", "{o}").   // {rule}', "negation")


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
    # 'is/are V-ed', or a copular linking verb of state ('remains revealed', 'becomes blocked') — the
    # latter is the same passive-state structure, just a different copula (some tagged a noun, so the
    # surface forms are listed).
    aux = next((c for c in kids if c.dep_ == "auxpass" and c.lemma_.lower() in _LINK_PASS), None)
    if aux is None:
        return None
    if any(c.lemma_ in _MODALS and c.dep_ in ("aux", "auxpass") for c in kids):
        return None
    if any(c.dep_ == "neg" for c in kids):               # "isn't determined / aren't shared" — don't assert it
        return None
    subj = (next((c for c in kids if c.dep_ in ("nsubjpass", "nsubj")), None)
            or next((c for c in aux.children if c.dep_ in ("nsubj", "nsubjpass")), None))   # subj under the copula
    if subj is None or subj.pos_ not in ("NOUN", "PROPN") or _masked(subj):
        return None
    if any(c.lemma_.lower() in _NEG_DET for c in subj.children if c.dep_ in ("det", "predet", "amod")):
        return None                                      # "NEITHER object becomes paired" — don't assert it positively
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
    if num is not None:
        n = int(num.text) if num.text.isdigit() else _WORDS.get(num.lemma_.lower())
    elif any(c.dep_ == "det" and c.lemma_.lower() in ("a", "an") for c in head.children):
        n = 1                                              # "There is AN inherent triggered ability" -> exactly one
    else:
        n = None
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


_REL_VERBS = {"refer", "mean", "represent", "include", "contain", "consist", "comprise", "cause",
              "affect", "describe", "denote", "indicate", "involve"}


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


def _ability(rule, doc):
    """"[subject] can [verb]" capability, CLASSIFIED by its qualifier — the positive "can/could"
    mirror of _permission ("may") and _restriction ("can't") -> ability(subject, action,
    qualifier_kind, qualifier). Negated "can't" is a restriction and excluded; "may" stays with
    _permission. Same parse and qualifier kinds as its siblings."""
    root = _root(doc)
    if root is None or root.pos_ != "VERB":
        return None
    kids = list(root.children)
    if not any(c.lemma_ in ("can", "could") and c.dep_ in ("aux", "auxpass") for c in kids):
        return None
    if any(c.dep_ == "neg" for c in kids):                 # "can't" is a restriction, not an ability
        return None
    subj = next((c for c in kids if c.dep_ in ("nsubj", "nsubjpass")), None)
    if subj is None or subj.pos_ not in ("NOUN", "PROPN"):
        return None
    action = ("be_" if any(c.dep_ == "auxpass" for c in kids) else "") + root.lemma_.lower()
    kind, qual = _classify_qualifier(root, doc)
    return Out(rule, f'ability("{subj.lemma_.lower()}", "{action}", "{kind}", "{qual}").   // {rule}',
               "ability")


_MODALS = {"may", "must", "can", "could", "will", "shall", "would", "should"}
# copular linking verbs of state (some spaCy-tagged as nouns, hence the surface forms) — "X remains
# revealed" / "X becomes blocked" is the same passive-state structure as "X is revealed".
_LINK_PASS = {"be", "remain", "remains", "stay", "stays", "become", "becomes", "get", "gets", "keep", "keeps"}


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
    if any(c.dep_ == "xcomp" for c in kids):               # raising / 'have to be …'
        return None
    for c in kids:                                         # causative 'have each player choose …' — the ccomp's
        if c.dep_ == "ccomp":                              # subject is a concrete CAUSEE. A relative clause
            csub = next((g for g in c.children if g.dep_ in ("nsubj", "nsubjpass")), None)   # ('abilities THAT
            if csub is None or csub.lemma_.lower() not in ("that", "which", "who"):          # represent it') has a
                return None                                # relativizer subject — that's a modifier, keep it.
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
    if root is None or (root.pos_ != "VERB" and root.lemma_ != "be"):
        return None                                        # allow a copula root ("…, the game IS a draw")
    kind_override = None
    mark_trig = next((c for c in root.children if c.dep_ in ("advcl", "ccomp")           # if/when adverbial clause
                      and any(t.dep_ == "mark" and t.lemma_.lower() in _TRIGGER_MARKS for t in c.subtree)), None)
    temp_trig = None                                        # SENTENCE-INITIAL "Any/Each time [clause], …" trigger
    tnp = next((c for c in root.children if c.lemma_ == "time" and c.left_edge.i == 0
                and any(g.lemma_.lower() in ("any", "each", "every", "the") for g in c.children if g.dep_ == "det")), None)
    if tnp is not None:
        rel = next((c for c in tnp.children if c.dep_ in ("relcl", "acl")), None)
        if rel is not None and _clause_subject(rel) != "-":
            temp_trig = rel
    # prefer a concrete-subject trigger: the if/when clause if it has one, else the temporal NP
    if mark_trig is not None and _clause_subject(mark_trig) != "-":
        trig = mark_trig
    elif temp_trig is not None:
        if any(c.lemma_ in ("may", "can") and c.dep_ in ("aux", "auxpass") for c in root.children):
            return None                                    # "Any time …, you MAY …" is a permission, not a trigger
        trig, kind_override = temp_trig, "temporal"
    else:
        trig = mark_trig
    if trig is None:
        return None
    ts, os_ = _clause_subject(trig), _clause_subject(root)
    if ts == "-" and os_ == "-":                           # need at least one concrete clause subject
        return None
    sub = " ".join(t.text.lower() for t in trig.subtree)
    mark = next((t.lemma_.lower() for t in trig.subtree if t.dep_ == "mark" and t.lemma_.lower() in _TRIGGER_MARKS), "if")
    kind = (kind_override or ("replacement" if "would" in sub and "instead" in doc.text.lower()
            else _MARK_KIND.get(mark, "condition")))
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
                  "any", "each", "some", "all", "only"}            # "Only X are Y" is restrictive, not a definition
ISA_PARTITIVE = {"kind", "type", "sort", "form"}                # "a kind of X" -> the genus is X
ISA_FUNC_MOD = {"only", "other", "same", "such", "certain", "single", "given", "specific", "first",
                "last", "new", "original", "following", "particular", "equal"}   # not type-specifying modifiers


def _np_name(tok) -> str:
    """A noun phrase's name: its content modifiers (amod/compound) + head lemma, joined with '_'."""
    return "_".join([c.text.lower() for c in tok.children if c.dep_ in ("amod", "compound")] + [tok.lemma_.lower()])


def _isa_genus(attr):
    """The category (genus) named by a copula predicate, or None to abstain. A specific predicate is
    its own NP name; a 'kind/type of Y' partitive resolves to Y; a BARE generic head (ability, object,
    type, …) with no type-specifying modifier is too weak to assert -> None. This recovers faithful
    definitions the flat blocklist used to drop ('… is a loyalty ability', '… is a kind of ability')."""
    if attr.lemma_ in ISA_PARTITIVE:                            # "a (special) kind of activated ability" -> the genus is Y
        of = next((c for c in attr.children if c.dep_ == "prep" and c.lemma_ == "of"), None)
        pobj = _child(of, "pobj") if of else None
        if pobj is None or pobj.pos_ not in ("NOUN", "PROPN") or _masked(pobj):
            return None
        return _isa_genus(pobj)                                 # recurse: "kind of ability" (bare) still abstains
    if attr.lemma_ in ISA_BAD_PRED:                             # generic head: keep only if a type modifier makes it specific
        mods = [c.text.lower() for c in attr.children
                if c.dep_ in ("amod", "compound") and c.is_alpha and c.text.lower() not in ISA_FUNC_MOD]
        return "_".join(mods + [attr.lemma_.lower()]) if mods else None
    return attr.lemma_.lower()                                  # specific head: unchanged (head-only, additive)


def _subclass(rule, doc):
    """Restricted-subject definition "Xs that [criterion] are [Modified-X]s" -> isa(modified_x, x).
    The PREDICATE names a subclass of the subject's kind ("Effects that use the word 'instead' are
    replacement effects" -> isa(replacement_effect, effect); "The team whose turn it is is the active
    team" -> isa(active_team, team)). This is the FAITHFUL direction — the named subclass genuinely is
    a kind of the parent — and it sidesteps the over-claim of a forward isa(effect, replacement_effect),
    which is why a restricted subject is otherwise abstained. Fires only when the subject carries a
    relative clause AND the predicate shares its head noun (so it's truly a subclass, not a definition)."""
    root = _root(doc)
    if root is None or root.lemma_ != "be" or any(c.dep_ == "neg" for c in root.children):
        return None
    if any(t.lemma_.lower() in ("not", "neither", "nor") for t in doc):
        return None
    subj, attr = _child(root, "nsubj"), _child(root, "attr")
    if subj is None or attr is None or subj.pos_ not in ("NOUN", "PROPN") or attr.pos_ not in ("NOUN", "PROPN"):
        return None
    if _masked(subj) or _masked(attr) or any(c.dep_ in ("conj", "cc") for c in attr.children):
        return None
    if not any(c.dep_ in ("relcl", "acl") for c in subj.children):     # subject must be restricted
        return None
    if subj.lemma_.lower() != attr.lemma_.lower():                      # predicate must share the head -> a subclass
        return None
    parent, sub = subj.lemma_.lower(), _np_name(attr)
    if sub == parent:
        return None
    return Out(rule, f'isa("{sub}", "{parent}").   // {rule}', "isa")


def _isa(rule, doc):
    """Genus-differentia definition "A [term] is a [category]" -> isa(term, category). A COORDINATED
    subject distributes — "Power and toughness are characteristics" -> isa(power, ...), isa(toughness,
    ...), each conjunct independently of that category (faithful, the same per-conjunct expansion the
    rules intend). A coordinated or negated PREDICATE ("is A or B", "is neither A nor B") is an
    enumeration / negation, not a clean genus, so it's abstained. Strict otherwise: nominal predicate
    not in the generic blocklist, not negated, not a leading conditional/quantified clause."""
    root = _root(doc)
    if root is None or root.lemma_ != "be" or doc[0].lemma_.lower() in ISA_SKIP_START:
        return None
    subj, attr = _child(root, "nsubj"), _child(root, "attr")
    if subj is None or attr is None:
        return None
    # partitive predicate "X is one of the Y" -> the genus is Y ("a player is one of the people" ->
    # isa(player, person)). Excludes a COUNTING context ("exactly one of the FIVE colors" — a count of
    # an attribute set, not a type the subject instantiates), which carries 'exactly'/a numbered set.
    if attr.pos_ == "NUM" and attr.lemma_ == "one" and _clean(subj):
        if any(t.lemma_ in ("not", "neither", "nor") and t.head in (root, attr) for t in doc):
            return None
        of = next((c for c in attr.children if c.dep_ == "prep" and c.lemma_ == "of"), None)
        pobj = _child(of, "pobj") if of else None
        if (pobj is not None and pobj.pos_ in ("NOUN", "PROPN") and not _masked(pobj)
                and not any(c.dep_ == "nummod" for c in pobj.children)
                and not any(c.lemma_.lower() == "exactly" for c in attr.children)):
            return Out(rule, f'isa("{_np_name(subj)}", "{pobj.lemma_.lower()}").   // {rule}', "isa")
        return None
    if attr.pos_ not in ("NOUN", "PROPN"):
        return None
    # negation must attach to the COPULA or its predicate ("X is not a Y" / "neither A nor B"), not to a
    # relative clause describing the subject/predicate ("actions that DON'T use the stack" — 116.1).
    if any(c.dep_ == "neg" for c in root.children) or any(
            t.lemma_ in ("not", "neither", "nor") and t.head in (root, attr) for t in doc):
        return None
    if any(c.dep_ in ("conj", "cc") for c in attr.children) or _masked(attr):   # disjunctive predicate -> enumeration
        return None
    cat = _isa_genus(attr)                                                       # the real category (genus)
    if cat is None:                                                             # bare generic head -> abstain
        return None
    subjects = [subj] + [c for c in subj.children if c.dep_ == "conj"]          # head + coordinated conjuncts
    if len(subjects) > 1:
        # "and" + a SINGULAR copula is noun-phrase-internal coordination ("a power and toughness
        # sticker IS …" = one noun), not two subjects; only plural agreement ("Power and toughness
        # ARE …") or "or" (alternatives) is genuine subject coordination. Else a false isa("power", …).
        cc = next((c.lemma_.lower() for s in subjects for c in s.children if c.dep_ == "cc"), "and")
        if cc == "and" and root.tag_ == "VBZ":
            return None
    lines = []
    for s in subjects:
        if s.pos_ not in ("NOUN", "PROPN") or _masked(s):
            return None
        pre = [c.text.lower() for c in s.children if c.dep_ in ("amod", "compound")]
        name = "_".join(pre + [s.lemma_.lower()])
        if name != cat:
            lines.append(f'isa("{name}", "{cat}").')
    if not lines:
        return None
    lines[0] += f"   // {rule}"
    return Out(rule, "\n".join(lines), "isa")


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


_MEANING_VERBS = {"represent", "mean"}


def _symbol_means(rule, doc):
    """§107.4c/d/g/h — "[The …] symbol {glyph} represents / means [meaning]" (also passive
    "is used to represent [meaning]") -> symbol_means(glyph, meaning). The masked glyph is the
    nsubj, recovered from the legend; the meaning is the WHOLE object noun phrase (kept intact so
    the fact isn't lossy — {0} -> "zero mana", not just "mana"). The copula form "X symbol is
    {glyph}" stays with _symbol_def; relational SVO with a non-masked subject stays with _relation."""
    root = _root(doc)
    if root is None:
        return None
    subj = next((c for c in root.children if c.dep_ in ("nsubj", "nsubjpass")), None)
    if subj is None or subj.text not in _LEGEND:
        return None
    glyph = _LEGEND[subj.text]
    g = glyph[0] if isinstance(glyph, (list, tuple)) else glyph
    if not (isinstance(g, str) and g.startswith("{")):        # subject must be a masked symbol glyph
        return None
    if root.lemma_ in _MEANING_VERBS:                         # active "{glyph} represents/means X"
        mverb = root
    elif root.lemma_ == "use":                                # passive "is used to represent X"
        mverb = next((c for c in root.children if c.dep_ == "xcomp" and c.lemma_ in _MEANING_VERBS), None)
    else:
        return None
    if mverb is None:
        return None
    obj = next((c for c in mverb.children if c.dep_ in ("dobj", "obj")), None)
    if obj is None:
        return None
    meaning = " ".join(t.text.lower() for t in obj.subtree
                       if t.is_alpha and t.pos_ != "DET" and not _masked(t))
    if not meaning:
        return None
    return Out(rule, f'symbol_means("{g}", "{meaning}").   // {rule}', "symbol_means")


def _some_are(rule, doc):
    """Partial-membership "Some [X] are [Y]" -> some_are(subject, category, polarity). The HONEST
    counterpart to _isa for "some" statements: "Some activated abilities are loyalty abilities" must
    not become a universal isa (only SOME are), so it gets its own existential relation. Negation
    ("Some replacement effects are not continuous effects") flips polarity to no. A coordinated
    predicate ("are replacement effects or prevention effects") is abstained (kept singular)."""
    if doc[0].lemma_.lower() != "some":
        return None
    root = _root(doc)
    if root is None or root.lemma_ != "be":
        return None
    subj, attr = _child(root, "nsubj"), _child(root, "attr") or _child(root, "acomp")
    if subj is None or attr is None or subj.pos_ not in ("NOUN", "PROPN") or attr.pos_ not in ("NOUN", "PROPN"):
        return None
    if _masked(subj) or _masked(attr) or any(c.dep_ in ("conj", "cc") for c in attr.children):
        return None
    sname = "_".join([c.text.lower() for c in subj.children if c.dep_ in ("amod", "compound")] + [subj.lemma_.lower()])
    aname = "_".join([c.text.lower() for c in attr.children if c.dep_ in ("amod", "compound")] + [attr.lemma_.lower()])
    if sname == aname:
        return None
    polarity = "no" if any(c.dep_ == "neg" for c in root.children) else "yes"
    return Out(rule, f'some_are("{sname}", "{aname}", "{polarity}").   // {rule}', "some_are")


def _not_isa(rule, doc):
    """Negated genus definition "X is not a Y" / "X is neither A nor B" -> not_isa(term, category),
    one per coordinated category. The negative twin of _isa — what something ISN'T. The negation
    must attach to the COPULA itself (not a relative clause: "actions … that don't use the stack"
    must NOT become not_isa(action, action)), and the subject must be UNRESTRICTED (no relative
    clause), so the denial is never over-claimed."""
    root = _root(doc)
    if root is None or root.lemma_ != "be" or doc[0].lemma_.lower() in ISA_SKIP_START:
        return None
    subj, attr = _child(root, "nsubj"), _child(root, "attr")
    if subj is None or attr is None or subj.pos_ not in ("NOUN", "PROPN") or attr.pos_ not in ("NOUN", "PROPN"):
        return None
    if _masked(subj) or _masked(attr) or any(c.dep_ in ("relcl", "acl", "prep") for c in subj.children):
        return None                                                    # restricted subject ("ability WITH a target") -> over-claim
    neg = (any(c.dep_ == "neg" for c in root.children)                  # "is not …" on the copula
           or any(t.lemma_.lower() in ("not", "neither") and t.head in (root, attr) for t in doc))   # "neither … nor"
    if not neg:
        return None
    sname = _np_name(subj)
    lines = []
    for c in [attr] + [k for k in attr.children if k.dep_ == "conj"]:   # "neither A nor B" -> A and B
        if c.pos_ not in ("NOUN", "PROPN") or _masked(c):
            continue
        g = _isa_genus(c)
        if g and g != sname:
            lines.append(f'not_isa("{sname}", "{g}").')
    if not lines:
        return None
    lines[0] += f"   // {rule}"
    return Out(rule, "\n".join(lines), "not_isa")


def _attribute_of(rule, doc):
    """Possessive-attribute definition "X's Y is Z" / "the Y of X is Z" -> attribute_of(owner,
    attribute, value): a creature's power is an amount; an ability's source is an object; a player's
    opponent is a player. The owner + attribute are crisp; value is the predicate noun's genus. Runs
    AFTER _isa, so it only claims the possessive copulas _isa abstains on (additive). Excludes
    partial ('Some …'), modal, coordinated-subject, adjectival/pronoun-valued and anaphoric forms —
    those aren't clean attribute definitions."""
    root = _root(doc)
    if root is None or root.lemma_ != "be" or any(c.dep_ == "neg" for c in root.children):
        return None
    if doc[0].lemma_.lower() == "some" or any(c.lemma_ in _MODALS and c.dep_ in ("aux", "auxpass") for c in root.children):
        return None
    subj = _child(root, "nsubj")
    attr = _child(root, "attr")
    if attr is None:                                                   # "is the player designated as X" — spaCy reads the
        ccomp = next((c for c in root.children if c.dep_ in ("ccomp", "acomp") and c.pos_ == "VERB"), None)
        attr = _child(ccomp, "nsubj") if ccomp is not None else None   # predicate as a clause; value = the participle's subject
    if subj is None or attr is None or subj.pos_ not in ("NOUN", "PROPN") or attr.pos_ not in ("NOUN", "PROPN"):
        return None
    if _masked(subj) or _masked(attr) or any(c.dep_ in ("conj", "cc") for c in subj.children):
        return None
    if any(c.dep_ in ("nummod", "conj", "cc") for c in attr.children):  # "is seven cards" / "is name, mana cost, …"
        return None                                                    # a quantity / enumeration, not a clean genus value
    poss = _child(subj, "poss")
    of = next((c for c in subj.children if c.dep_ == "prep" and c.lemma_ == "of"), None)
    owner = poss if (poss is not None and poss.pos_ in ("NOUN", "PROPN")) else \
        (_child(of, "pobj") if of is not None else None)
    if owner is None or owner.pos_ not in ("NOUN", "PROPN") or _masked(owner):
        return None
    if any(c.lemma_.lower() in _ANAPHOR_MOD for c in owner.children if c.dep_ in ("det", "amod", "poss")):
        return None                                                    # "these alternative characteristics"
    return Out(rule, f'attribute_of("{owner.lemma_.lower()}", "{_np_name(subj)}", "{attr.lemma_.lower()}").   // {rule}',
               "attribute_of")


def _gerund_action(rule, doc):
    """Nominalized-action subject "[Gerund] … [verb]s [object]" -> gerund_action(action, verb,
    object, polarity). Many consequences are stated with a gerund-phrase subject ("Doubling a
    creature's power creates a continuous effect", "Revealing a card doesn't cause …"); spaCy roots
    these on the main verb with the gerund as a clausal subject (csubj) and no noun nsubj, so the SVO
    patterns miss them. action = gerund lemma; polarity = yes|no (captures doesn't / won't / not)."""
    root = _root(doc)
    if root is None or (root.pos_ != "VERB" and root.lemma_ != "be"):
        return None
    g = doc[0]
    if g.tag_ != "VBG" or g.dep_ != "csubj" or g.head != root:
        return None
    obj = next((c for c in root.children if c.dep_ in ("dobj", "obj", "attr", "acomp")), None)
    o = (obj.lemma_.lower() if obj is not None and obj.pos_ in ("NOUN", "PROPN", "ADJ")
         and not _masked(obj) else "-")
    polarity = "no" if any(c.dep_ == "neg" for c in root.children) else "yes"
    return Out(rule, f'gerund_action("{g.lemma_.lower()}", "{root.lemma_.lower()}", "{o}", "{polarity}").   // {rule}',
               "gerund_action")


_PATTERNS = [_sba_grouped, _sba_world, _sba_scheme, _sba_aggregate_loss, _sba_lethal, _sba_value, _damage_result, _sba_attachment, _sba_ceases, _keyword_action, _status_action, _evasion, _passive_prohibition, _prohibition, _symbol_def, _symbol_means, _keyword_class, _subclass, _isa, _some_are, _not_isa, _attribute_of, _restriction, _conditional, _possession, _permission, _passive, _effect, _capability, _relation, _obligation, _existential, _comparison, _is_property, _negation, _ability, _gerund_action, _imperative, _action]

_LEGEND: dict = {}                                            # preprocess legend for the sentence under transpilation


import re as _re

# Aside parentheticals — cross-references and illustrative lists that spaCy otherwise mis-attaches
# as the sentence ROOT ("see rule N" -> root "see"), stranding the real clause's subject. Stripping
# them before the parse is the leading-scope-recovery fix: it rescues the main clause for every
# pattern, not just one. Only asides that START with one of these markers are removed (a "(see …)"
# is always a reference; a bare "(…)" might carry content, so it's left in place).
_ASIDE = _re.compile(r"\s*\((?:(?:see |such as |for example|e\.g\.|i\.e\.|including )[^()]*|[^()]*\bsee rules?\b[^()]*)\)", _re.I)


def _normalize(s: str) -> str:
    """Normalize curly quotes/apostrophes to ASCII so spaCy tokenizes/parses deterministically
    (e.g. curly "can’t" otherwise breaks the dependency parse), and drop "(see rule N)"-style aside
    parentheticals that would otherwise hijack the dependency ROOT."""
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    return _ASIDE.sub("", s).strip()


# Core game-object nouns spaCy systematically MIS-TAGS — they're lexically nouns AND adjectives/verbs
# ("a permanent" / "permanent object"; "a token" / "token creature"; "a trigger" / "it triggers"), so
# in the rules' terse style the tagger often calls the noun an ADJ/VERB. That makes every noun-subject
# guard (_clause_subject, _isa, _action, …) reject the rule. Retagging these lemmas to NOUN — ONLY when
# they sit in a nominal-head dependency role (subject/object/predicate), never as a modifier — fixes the
# subject for ALL patterns at once. "permanent" alone accounts for ~30 stranded rules.
_GAME_NOUNS = {"permanent", "spell", "token", "copy", "trigger", "counter", "emblem"}
_NOMINAL_DEPS = {"nsubj", "nsubjpass", "dobj", "obj", "pobj", "attr", "appos", "conj"}


def _retag_game_nouns(doc) -> None:
    """In-place: promote a mis-tagged game-object lemma to NOUN when it heads a nominal phrase; and an
    '-ing' word in SUBJECT position is a gerund (a noun: "Banding doesn't cause …"), so promote it too."""
    for tok in doc:
        if (tok.pos_ in ("ADJ", "VERB") and tok.dep_ in _NOMINAL_DEPS
                and tok.lemma_.lower() in _GAME_NOUNS and tok.text not in _LEGEND):
            tok.pos_ = "NOUN"
        elif (tok.pos_ in ("ADJ", "VERB") and tok.dep_ in ("nsubj", "nsubjpass")
              and tok.text.lower().endswith("ing") and tok.text not in _LEGEND):
            tok.pos_ = "NOUN"


def _retag_you(doc) -> None:
    """In-place: "you" is the rulebook's second-person referent for the player acting, everywhere — so
    a "you" in a subject role is resolved to the noun 'player', letting the noun-subject patterns apply
    ("You can't cast a card face down" -> restriction(player, cast, …)). A universal pronoun
    normalization, not keyword-specific; any "you" rule benefits and a rewording still resolves."""
    for tok in doc:
        if tok.lemma_.lower() == "you" and tok.dep_ in ("nsubj", "nsubjpass"):
            tok.pos_, tok.lemma_ = "NOUN", "player"


def _retag_root_verb(doc) -> None:
    """In-place: a root-position NOUN that has BOTH a subject and a verbal complement (a direct/dative
    object or a clausal complement) is almost certainly a mis-tagged VERB — nouns don't take subjects
    AND objects ("a spell … enters the battlefield", where 'enters' is read as a noun). Promote it so
    the SVO patterns apply. Content-agnostic (no word list); a pure copula predicate ('a spell is a
    card' -> root 'card') has a subject but NO object, so it is left alone."""
    root = next((t for t in doc if t.dep_ == "ROOT"), None)
    if root is None or root.pos_ != "NOUN" or root.text in _LEGEND:
        return
    if (any(c.dep_ in ("nsubj", "nsubjpass") for c in root.children)
            and any(c.dep_ in ("dobj", "obj", "dative", "oprd", "ccomp", "xcomp") for c in root.children)):
        root.pos_ = "VERB"


# --- truthiness guards for interpreting a rule beyond its opening sentence --------------------
#
# A rule's later sentences are only safe to interpret when each stands on its own. We accept a
# later sentence ONLY if it is self-contained — a conditional (its condition is stated in-sentence)
# or a standalone generalization (an indefinite / quantified / bare-generic subject) — and skip any
# EXTENSION whose truth leans on the prior sentence: a pronoun / demonstrative / possessive subject
# ("it", "such an ability", "its front face"), a definite back-reference ("the token …"), a discourse
# continuation ("then …", "the same is true …"), or a cross-reference aside ("See rule …"). This is
# deliberately conservative: a sentence that needs context is dropped, never flattened into a
# context-free fact.
_FOLLOWUP_NOISE = _re.compile(
    r'^(see rule|see section|for more information|for rules|for details|for a list|'
    r'example|note that|this is an exception)\b', _re.I)
_DISCOURSE_LEAD = {"then", "otherwise", "instead", "however", "similarly", "likewise", "additionally",
                   "conversely", "nonetheless", "regardless", "meanwhile", "alternatively", "also",
                   "thus", "therefore"}
_DEFINITE_DET = {"the", "this", "that", "these", "those", "such"}      # a definite subject may be a back-reference
_ANAPHOR_MOD = {"such", "other", "this", "that", "these", "those", "same", "its", "their", "his", "her"}
_NEG_DET = {"no", "neither", "none"}                                   # negative-quantified subject

# Patterns that ASSERT their subject positively. A negative-quantified subject ("No player gets
# priority") would invert their truth, so we suppress them in that case — a negated generalization
# can't be stated faithfully as a positive fact. Structural skeletons (conditional, sba_*, …) and
# the already-negative _negation are exempt.
_POS_ASSERT = {"action", "effect", "possession", "ability", "permission", "restriction", "obligation",
               "relation", "isa", "property", "comparison", "capability", "some_are", "gerund_action",
               "passive"}


def _balanced(s: str) -> bool:
    """True if s has no open quotation or bracket — a complete span, not cut mid-quote/mid-bracket."""
    return (s.count("“") == s.count("”") and s.count('"') % 2 == 0       # " "
            and s.count("[") == s.count("]") and s.count("{") == s.count("}")
            and s.count("(") == s.count(")"))


def _split_sentences(text: str) -> list:
    """Sentence-split on '. ', re-joining any chunk that ends mid-quote or mid-bracket — the rules
    quote ability text that itself contains sentence breaks ("…enters with N counters. …"), and a
    naive split would shear those into meaningless fragments. For text with no such spanning quote
    (the vast majority of rules) this is EXACTLY text.split(". "), so sentence 0 is byte-identical to
    the prior single-sentence behavior and nothing already-covered changes."""
    out, buf = [], ""
    for part in text.split(". "):
        buf = part if not buf else buf + ". " + part
        if _balanced(buf):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def _neg_subject(doc) -> bool:
    """True if the main clause subject is negatively quantified ('No player …', 'Neither team …')."""
    root = _root(doc)
    subj = next((c for c in root.children if c.dep_ in ("nsubj", "nsubjpass")), None) if root else None
    if subj is None:
        return False
    return (subj.lemma_.lower() in _NEG_DET
            or any(c.lemma_.lower() in _NEG_DET for c in subj.children if c.dep_ in ("det", "predet", "amod")))


def _self_contained(sentence: str, doc) -> bool:
    """True if a fact drawn from this (non-opening) sentence is true on its own — see the note above."""
    s = sentence.strip().lstrip('" ').lower()
    if _FOLLOWUP_NOISE.match(s) or s.startswith(("the same", "doing so", "such ", "other ", "these ", "those ")):
        return False
    if doc[0].lemma_.lower() in _DISCOURSE_LEAD:
        return False
    root = _root(doc)
    if root is None:
        return False
    if any(c.dep_ in ("advcl", "ccomp")                              # a conditional/temporal: condition is in-sentence
           and any(t.dep_ == "mark" and t.lemma_.lower() in _TRIGGER_MARKS for t in c.subtree)
           for c in root.children):
        return True
    subj = next((c for c in root.children if c.dep_ in ("nsubj", "nsubjpass")), None)
    if subj is None:                                                 # subjectless generalization (gerund / existential)
        return True
    if subj.pos_ == "PRON":                                          # "it / they / this …"
        return False
    if any(c.dep_ == "poss" for c in subj.children):                # "its / a player's … X" — specific/relational
        return False
    if any(c.lemma_.lower() in _ANAPHOR_MOD                          # "such / other / that … X" in any modifier slot
           for c in subj.children if c.dep_ in ("det", "predet", "amod", "nmod", "nummod")):
        return False
    det = next((c for c in subj.children if c.dep_ == "det"), None)
    if det is not None and det.lemma_.lower() in _DEFINITE_DET:      # definite "the X" -> possible back-reference
        return False
    return True                                                     # indefinite / quantified / bare generic -> standalone


def transpile_rule(rule: str, text: str) -> Out | None:
    """Interpret a rule into one faithful Datalog fact, reading its sentences in order.

    Sentence 0 is the rule's opening statement (its primary meaning). Later sentences are read too,
    but ONLY when self-contained (_self_contained), so a fact never depends on earlier text. Across
    all sentences, a negative-quantified subject is never asserted positively (_neg_subject), so a
    negated generalization yields no false positive fact. Returns the first faithful fact, tagged
    with the sentence it came from, or None."""
    global _LEGEND
    for i, chunk in enumerate(_split_sentences(text)):
        sent = _normalize(chunk)
        if not sent:
            continue
        masked = preprocess(sent)                # mask formal fragments for a clean parse
        _LEGEND = masked.legend                  # patterns (e.g. _symbol_def) may resolve masked tokens
        if not masked.text.strip():
            continue
        doc = _NLP(masked.text)
        if len(doc) == 0:
            continue
        _retag_game_nouns(doc)                    # fix mis-tagged game-object subjects before matching
        _retag_root_verb(doc)                     # fix a root verb mis-read as a noun
        _retag_you(doc)                           # "you" -> the player acting
        if i > 0 and not _self_contained(chunk, doc):
            continue
        for fn in _PATTERNS:
            out = fn(rule, doc)
            if out is None:
                continue
            if out.pattern in _POS_ASSERT and _neg_subject(doc):   # don't assert a negated subject positively
                break                                              # (try the next sentence instead)
            out.sentence = i
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
