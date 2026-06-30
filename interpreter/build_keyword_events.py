"""Build datalog/keyword_events.dl — quoted keyword-event definitions + keyword class-by-context.

Two recurring §702 keyword-ability forms:

  event_definition(event, subject, verb)   A keyword names a quoted game-event and the rule defines
                                            when it happens: "A creature 'evolves' when one or more
                                            +1/+1 counters are put on it" -> event_definition("evolve",
                                            "counter", "put"). The event is the quoted phrase (head
                                            verb lemmatized, keeping any qualifier: "attacks alone" ->
                                            "attack_alone"); subject/verb come from parsing ONLY the
                                            condition clause (where spaCy is reliable).

  keyword_class_ctx(keyword, context, kind) Some keywords are a different KIND of ability depending on
                                            the object: "Ascend on an instant or sorcery spell
                                            represents a spell ability" / "Ascend on a permanent
                                            represents a static ability" -> ("ascend","spell","spell"),
                                            ("ascend","permanent","static").

Both read with anchored regexes (these sentences mis-parse whole — proper-noun keywords, quoted
events), parsing only the reliable sub-clause. Content-driven, faithful, nothing flattened.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re
from pathlib import Path

from interpreter import transpile
from interpreter.dlgen import Program
from preprocess import preprocess
from interpreter.rules_parser import split
from interpreter.transpile import _NLP, _normalize, _retag_game_nouns, _root, _child, _split_sentences

_Q = "[“”\"]"
# [Det subject] "event" [optional object] (if|when|as|after) [condition clause]
_EVENT = re.compile(rf"^(?:An?|The|Each) [\w ]+? {_Q}([^“”\"]+){_Q}\s+(?:[\w ]+? )?(?:if|when|as|after|whenever)\s+(.+)$", re.I)
# "K on [context] represents a [kind] ability/spell"  /  "K is a [kind] ability found on [context]"
_REPR = re.compile(rf"^([A-Z][a-z]+) on (?:an? )?(.+?) represents an? (\w+) (?:ability|spell)\b", re.I)
_FOUND = re.compile(r"^([A-Z][a-z]+) is an? (\w+) ability found on (?:some )?(.+?) (?:cards?|spells?)\b", re.I)
# "You choose which [object] to [action] as you choose to pay a spell's [keyword] cost"
_COST = re.compile(r"^You choose which (\w+) to (\w+) as you .*?pay a spell.s (\w+) cost", re.I)


def _lemma_event(phrase: str) -> str:
    """The event name: head verb lemmatized + content qualifiers, determiners/pronouns dropped
    ('attacks alone' -> attack_alone, 'attacks a player' -> attack_player)."""
    doc = _NLP(phrase)
    if len(doc) == 0:
        return phrase.lower().replace(" ", "_")
    return "_".join([doc[0].lemma_.lower()]
                    + [t.text.lower() for t in doc[1:] if t.is_alpha and t.pos_ not in ("DET", "PRON", "ADP")])


def _condition(clause: str) -> tuple[str, str] | None:
    """(subject, verb) of a condition clause; subject '-' if pronoun/none, 'you' -> player."""
    m = preprocess(_normalize(clause))
    transpile._LEGEND = m.legend
    d = _NLP(m.text)
    if len(d) == 0:
        return None
    _retag_game_nouns(d)
    root = _root(d)
    if root is None or root.pos_ not in ("VERB", "AUX"):
        return None
    s = _child(root, "nsubj") or _child(root, "nsubjpass")
    subj = (s.lemma_.lower() if s is not None and s.pos_ in ("NOUN", "PROPN")
            else "player" if s is not None and s.lemma_.lower() in ("you", "-PRON-") else "-")
    return subj, root.lemma_.lower()


def events() -> list[tuple[str, str, str, str]]:
    """(rule, event, subject, verb) for every quoted-event definition."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _EVENT.match(_split_sentences(sr.text)[0].strip())
                    if not m or any(c in m.group(1) for c in "[]{}/"):   # skip bracketed template quotes
                        continue
                    cond = _condition(m.group(2))
                    if cond is None:
                        continue
                    event = _lemma_event(m.group(1))
                    if (event, *cond) not in seen:
                        seen.add((event, *cond))
                        rows.append((sr.number, event, *cond))
    return rows


def class_contexts() -> list[tuple[str, str, str, str]]:
    """(rule, keyword, context, kind) for 'K on [context] represents a [kind] ability/spell'."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    s0 = _split_sentences(sr.text)[0].strip()
                    m = _REPR.match(s0)
                    if m:
                        kw, ctx, kind = m.group(1).lower(), m.group(2).split()[-1].lower(), m.group(3).lower()
                    else:
                        m = _FOUND.match(s0)                            # "K is a [kind] ability found on [ctx] cards"
                        if not m:
                            continue
                        kw, ctx, kind = m.group(1).lower(), m.group(3).split()[-1].lower(), m.group(2).lower()
                    if (kw, ctx, kind) not in seen:
                        seen.add((kw, ctx, kind))
                        rows.append((sr.number, kw, ctx, kind))
    return rows


def cost_choices() -> list[tuple[str, str, str, str]]:
    """(rule, keyword_cost, object, action) for 'you choose which X to Y as you pay a spell's K cost'."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    rows, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _COST.match(_split_sentences(sr.text)[0].strip())
                    if not m:
                        continue
                    fact = (m.group(3).lower(), m.group(1).lower(), m.group(2).lower())
                    if fact not in seen:
                        seen.add(fact)
                        rows.append((sr.number, *fact))
    return rows


def build() -> tuple[str, dict]:
    ev, cc, ch = events(), class_contexts(), cost_choices()
    p = Program()
    p.comment("keyword_events.dl — quoted keyword-event definitions + keyword class-by-context, from rules.txt.")
    p.comment("event_definition(event, subject, verb); keyword_class_ctx(keyword, context, kind). GENERATED.")
    p.blank()
    p.decl("event_definition", [("event", "symbol"), ("subject", "symbol"), ("verb", "symbol")])
    p.decl("keyword_class_ctx", [("keyword", "symbol"), ("context", "symbol"), ("kind", "symbol")])
    p.decl("keyword_cost_choice", [("keyword_cost", "symbol"), ("object", "symbol"), ("action", "symbol")])
    p.blank()
    p.comment("--- event_definition: when a quoted keyword-event happens ---")
    for _n, e, s, v in ev:
        p.fact(f'event_definition("{e}", "{s}", "{v}")')
    p.blank()
    p.comment("--- keyword_class_ctx: a keyword's ability kind depends on the object ---")
    for _n, kw, ctx, kind in cc:
        p.fact(f'keyword_class_ctx("{kw}", "{ctx}", "{kind}")')
    p.blank()
    p.comment("--- keyword_cost_choice: paying a keyword cost asks you to choose an object to act on ---")
    for _n, kc, obj, act in ch:
        p.fact(f'keyword_cost_choice("{kc}", "{obj}", "{act}")')
    p.blank()
    p.output("event_definition", "keyword_class_ctx", "keyword_cost_choice")
    p.blank()
    p.comment("conformance — spot-check an event the rules define plainly")
    p.conformance(
        [("expect_event", [("event", "symbol"), ("verb", "symbol")])],
        [("event", "expect_event(E, V)", "miss", "event_definition(E, _, V)")])
    p.fact('expect_event("evolve", "put")')
    return p.text(), {"events": len(ev), "contexts": len(cc), "choices": len(ch)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_events.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/keyword_events.dl (event_definition={report['events']}, keyword_cost_choice={report['choices']}, "
          f"keyword_class_ctx={report['contexts']})")


if __name__ == "__main__":
    main()
