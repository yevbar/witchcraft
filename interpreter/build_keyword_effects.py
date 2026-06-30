"""Build datalog/keyword_effects.dl — structured effects of the formulaic keyword
definitions in §701/§702 (the counter/draw/scry/token keywords). Where
keyword_defs.dl stores the definition VERBATIM, this REDUCES the common effect
verbs to structured facts:

  keyword_effect(keyword, effect, amount, target)
  monstrosity -> (add_counter, N, p1p1) · blight -> (add_counter, N, m1m1)
  scry -> (look_top, N, library) · gift_a_card -> (draw, 1, card)
  gift_a_food -> (create_token, 1, food)

The effect VERB + its object NP + the amount modifier are now found by a spaCy
dependency parse (robust to wording: "look at the top N cards" / "draws a card" /
"creates a Food token"); a small lark grammar evaluates the amount sublanguage
(N / a|an|one / digit). The "+1/+1 counter" fragment that trips spaCy is still
read as a literal scan of the clause (exactly as before — spaCy mangles it to
"plus1/plus1"). Deterministic; only the formulaic effect verbs are reduced.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

import spacy
from lark import Lark, Transformer

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_NLP = spacy.load("en_core_web_sm")
_COUNTER = {"+1/+1": "p1p1", "-1/-1": "m1m1"}

# --- amount sublanguage: a lark grammar evaluates the count modifier of the
# effect's object NP ("N" / "a" / "an" / "one" / a digit) into the slug the old
# _amount() produced. Indefinite article -> "1"; the parameter N stays "N". ----
_AMOUNT_GRAMMAR = r"""
    ?start: amount
    amount: "n"               -> n
          | "a"               -> one
          | "an"              -> one
          | "one"             -> one
          | DIGITS            -> digits
    DIGITS: /[0-9]+/
    %ignore " "
"""


class _ToAmount(Transformer):
    def n(self, _):       return "N"
    def one(self, _):     return "1"
    def digits(self, x):  return str(x[0])


_AMOUNT = Lark(_AMOUNT_GRAMMAR, parser="lalr", transformer=_ToAmount())


def _amount(w: str) -> str:
    """Slug an amount word via the lark grammar; unrecognized words pass through
    verbatim (faithful to the old regex's else-branch)."""
    try:
        return _AMOUNT.parse(w.lower())
    except Exception:
        return w.lower()


# 'put <amount> +1/+1 counters on' — read as a literal off the clause text. spaCy
# tokenizes '+1/+1' to 'plus1/plus1' and corrupts the surrounding parse, so both the
# AMOUNT and the counter KIND are read literally here, exactly as the original regex
# did (`put (\w+) ([+\-]1/[+\-]1) counters? on`). spaCy still locates the clause.
_PUT_COUNTER = re.compile(r"put (\w+) ([+\-]1/[+\-]1) counters? on")


def _counter_clause(verb):
    """(amount, kind) for the 'put N +1/+1 counters on' clause governed by `verb`,
    or None. Literal scan of the verb's clause text."""
    span = verb.doc[verb.left_edge.i: verb.right_edge.i + 1].text
    m = _PUT_COUNTER.search(span)
    if not m:
        return None
    return _amount(m.group(1)), _COUNTER[m.group(2)]


def _amount_mod(noun) -> str:
    """The count modifier of an object NP, as the old regex's first capture group:
    the nummod/compound/det child that names the quantity (skipping 'top', 'the')."""
    for c in noun.children:
        if c.dep_ in ("nummod", "compound", "det", "amod") and c.text.lower() not in ("top", "the"):
            return _amount(c.text)
    return ""


def _obj_noun(verb, *lemmas):
    """An object noun (dobj/pobj reachable from the verb) whose lemma is one of lemmas."""
    for t in verb.subtree:
        if t.lemma_ in lemmas and t.dep_ in ("dobj", "obj", "pobj"):
            vo = _verb_of(t)
            if vo is not None and vo.i == verb.i:
                return t
    return None


def _verb_of(noun):
    """Walk up dobj/pobj/prep to the governing verb."""
    t = noun.head
    while t is not None and t.pos_ not in ("VERB", "AUX") and t.head is not t:
        t = t.head
    return t


def _effects_from_doc(doc):
    """Yield (effect, amount, target) for every formulaic effect clause in the
    parsed definition sentence. Mirrors the five old _EFFECTS regexes, in order."""
    out = []
    # put <amount> +1/+1 counters on  ->  add_counter
    for v in (t for t in doc if t.lemma_ == "put" and t.pos_ == "VERB"):
        cc = _counter_clause(v)
        if cc is not None:
            amt, ctr = cc
            out.append(("add_counter", amt, ctr))
    # look at the top <amount> cards  ->  look_top (library)
    for v in (t for t in doc if t.lemma_ == "look" and t.pos_ == "VERB"):
        noun = _obj_noun(v, "card")
        if noun is not None and any(c.lemma_ == "top" for c in noun.children):
            out.append(("look_top", _amount_mod(noun), "library"))
    # draw <amount> cards  ->  draw (card)
    for v in (t for t in doc if t.lemma_ == "draw" and t.pos_ == "VERB"):
        noun = _obj_noun(v, "card")
        if noun is not None:
            out.append(("draw", _amount_mod(noun), "card"))
    # gain <amount> life  ->  gain_life (life)
    for v in (t for t in doc if t.lemma_ == "gain" and t.pos_ == "VERB"):
        noun = _obj_noun(v, "life")
        if noun is not None:
            out.append(("gain_life", _amount_mod(noun), "life"))
    # create a|an|one|<n> <Name> token  ->  create_token (name)
    for v in (t for t in doc if t.lemma_ == "create" and t.pos_ == "VERB"):
        tok = _obj_noun(v, "token")
        # the token's name: capitalized compound(s) on the 'token' head, or the
        # head NP itself when spaCy attaches 'token' as an amod (e.g. "Incubator token")
        name = _token_name(v, tok)
        if name:
            out.append(("create_token", "1", name))
    return out


def _token_name(verb, token_tok):
    """The created token's name slug: the capitalized compound/proper modifiers of
    the 'token' object (e.g. 'Food token' -> food), faithful to the old regex which
    captured the [A-Z][\\w ]* run before 'token'."""
    head = None
    if token_tok is not None:
        comps = [c for c in token_tok.children if c.dep_ == "compound" and c.text[:1].isupper()]
        if comps:
            head = comps
    if head is None:
        # spaCy sometimes makes 'token' an amod of the name (PROPN dobj of create)
        for t in verb.children:
            if t.dep_ in ("dobj", "obj") and t.pos_ == "PROPN" and any(c.lemma_ == "token" for c in t.children):
                head = [t]
                break
    if not head:
        return None
    raw = " ".join(c.text for c in head)
    return re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")

# keyword name: '"Keyword" [N] means/is' or 'To keyword[, ]'
_KW = re.compile(r'^"?([A-Z][\w ]*?)"? ?N? (?:means|is to|is )|^To "?([a-z][\w ]+?)"?[, ]')
_STOP = {"if", "a", "an", "the", "when", "whenever", "previously", "that", "this", "each", "some", "player"}


def _keyword(first: str) -> str | None:
    m = _KW.match(first)
    if not m:
        return None
    name = re.sub(r"\s+[Nn]$", "", (m.group(1) or m.group(2)).strip())   # drop the trailing "N" parameter
    words = name.lower().split()
    if not words or words[0] in _STOP or any(w in ("player", "permanent") for w in words) or len(words) > 4:
        return None
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def extract() -> list:
    """(rule#, keyword, effect, amount, target)."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            if g.number not in ("701", "702"):
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    first = sr.text.split(". ")[0].replace("’", "'").replace("“", '"').replace("”", '"')
                    kw = _keyword(first)
                    if not kw or kw in seen:
                        continue
                    effs = _effects_from_doc(_NLP(first))
                    if effs:
                        eff, amt, tgt = effs[0]          # first effect, in regex priority order
                        out.append((sr.number, kw, eff, amt, tgt))
                        seen.add(kw)
    return out


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("keyword_effects.dl — structured effects of formulaic §701/§702 keyword definitions.")
    p.comment("keyword_effect(keyword, effect, amount, target). GENERATED, not hand-written.")
    p.blank()
    p.decl("keyword_effect", [("keyword", "symbol"), ("effect", "symbol"), ("amount", "symbol"), ("target", "symbol")])
    p.blank()
    for _num, kw, eff, amt, tgt in rows:
        p.fact(f'keyword_effect("{kw}", "{eff}", "{amt}", "{tgt}")')
    p.blank()
    p.output("keyword_effect")
    p.blank()
    p.comment("conformance — spot-check effects the rules state plainly")
    p.conformance(
        [("expect_effect", [("keyword", "symbol"), ("effect", "symbol"), ("target", "symbol")])],
        [("effect", "expect_effect(K, E, T)", "miss", "keyword_effect(K, E, _, T)")],
    )
    for atom in ['expect_effect("monstrosity", "add_counter", "p1p1")',
                 'expect_effect("blight", "add_counter", "m1m1")',
                 'expect_effect("scry", "look_top", "library")']:
        p.fact(atom)
    return p.text(), {"count": len(rows), "rows": rows}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_effects.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/keyword_effects.dl ({report['count']} keyword effects)")
    for _n, kw, eff, amt, tgt in report["rows"]:
        print(f"    {kw:18} {eff:14} {amt:3} {tgt}")


if __name__ == "__main__":
    main()
