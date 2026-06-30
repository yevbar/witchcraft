"""Build datalog/concepts.dl — §1 Game Concepts definitional families, from rules.txt.

Three small, regular families that the rest of the engine references:

  §117.1  "A player may [cast/activate/take] [object] [any time they have priority |
           during their main phase ... priority ... empty]."
        -> player_may(action, object, timing)      timing = priority | sorcery

  §113.3  "[Spell/Activated/Triggered/Static] abilities ..."
        -> ability_category(name)                   the four ability categories

  §122.1  "A [kind] counter ... [creates a replacement/prevention effect | a triggered
           ability | modifies P/T | indicates a characteristic | grants a keyword]."
        -> counter_kind(kind, creates)              what each counter kind does

Hybrid: the §117.1 permission frame is genuine SVO prose ("A player may VERB <object NP>
<timing clause>"), so it's read by a spaCy dependency parse — the modal VERB is the ROOT, the
permitted action; its dobj subtree is the object NP; a "during their main phase" prep child
marks the sorcery-speed timing (a small lark grammar classifies the timing sublanguage into
priority|sorcery). This is robust to the object NP's internal wording where the old single
regex hard-coded `(?:an?|some|other|a) [\\w\\- ]+?`. The §113.3 category frame ("X abilities",
rule-number-gated) and the §122.1 counter-effect lexicon stay regex — those parse structured /
literal text a dependency parse can't improve. A counter whose effect doesn't match the lexicon
(e.g. poison, an SBA reference) is abstained on rather than mislabeled — a wrong fact is worse
than no fact.
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

# --- timing sublanguage: a lark grammar classifies the §117.1 timing clause into the
# speed slug the old regex's three alternatives produced. "during ... main phase ..." ->
# sorcery (the clause mentions the main phase + an empty stack); a bare priority clause ->
# priority. Faithful to: `"sorcery" if "main phase" in timing else "priority"`. ----------
_TIMING_GRAMMAR = r"""
    ?start: timing
    timing: "sorcery"   -> sorcery
          | "priority"  -> priority
"""


class _ToTiming(Transformer):
    def sorcery(self, _):   return "sorcery"
    def priority(self, _):  return "priority"


_TIMING = Lark(_TIMING_GRAMMAR, parser="lalr", transformer=_ToTiming())

# the permitted-action verbs of §117.1 (cast / activate / take / play), exactly the old
# regex alternation. The ROOT verb's lemma must be one of these.
_PM_VERBS = {"cast", "activate", "take", "play"}
# the leading article/quantifier the old _ART stripped from the object NP before slugging.
_ART = re.compile(r"^(?:an?|some|other|a)\s+")
_KIND = re.compile(r"(?:A |An |One or more |The number of )([\w/+\-]+) counters?\b", re.I)


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def _object_np(verb) -> str | None:
    """The slug of the verb's object NP, faithful to the old regex's second capture group
    (`(?:an?|some|other|a) [\\w\\- ]+?`) then `_ART`-stripped + spaces->underscores. The NP is
    the contiguous span from the dobj's leftmost determiner/modifier to the dobj head; the
    timing clause (npadvmod 'time' / prep 'during') sits after the head and is excluded."""
    dobjs = [c for c in verb.children if c.dep_ in ("dobj", "obj")]
    if not dobjs:
        return None
    dobj = dobjs[0]
    mods = [c for c in dobj.lefts if c.dep_ in ("det", "amod", "compound", "nummod", "poss")]
    start = min([m.i for m in mods] + [dobj.i])
    np = dobj.doc[start: dobj.i + 1].text.strip()
    if not _ART.match(np):                       # old regex required a leading article/quantifier
        return None
    return _ART.sub("", np).replace(" ", "_")


def _timing(verb) -> str:
    """Classify the §117.1 timing clause governed by `verb` into priority|sorcery via lark.
    'during their main phase' (a prep child whose object is the main phase) => sorcery."""
    main_phase = any(
        c.dep_ == "prep" and c.text.lower() == "during"
        and any(t.lemma_ == "phase" for t in c.subtree)
        for c in verb.children
    )
    return _TIMING.parse("sorcery" if main_phase else "priority")


def _has_priority_clause(verb) -> bool:
    """The old regex anchored on a 'priority'-bearing timing clause (any time / whenever they
    have priority / during their main phase ... priority ... empty). Require 'priority' to
    appear in the verb's clause so we don't over-match a bare 'A player may take an action'."""
    span = verb.doc[verb.left_edge.i: verb.right_edge.i + 1].text.lower()
    return "priority" in span


def player_may() -> list[tuple[str, str, str, str]]:
    """(rule, action, object, timing) — §117.1 priority/timing permissions, read by a spaCy
    dependency parse of the "A player may VERB <object> <timing>" permission frame."""
    rows, seen = [], set()
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "117":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    for sent in re.split(r"(?<=[.]) ", sr.text):
                        doc = _NLP(sent.strip())
                        for verb in doc:
                            if verb.dep_ != "ROOT" or verb.lemma_ not in _PM_VERBS:
                                continue
                            subj = next((c for c in verb.children
                                         if c.dep_ == "nsubj" and c.lemma_ == "player"), None)
                            modal = any(c.dep_ == "aux" and c.lemma_ == "may" for c in verb.children)
                            if subj is None or not modal or not _has_priority_clause(verb):
                                continue
                            obj = _object_np(verb)
                            if obj is None:
                                continue
                            tclass = _timing(verb)
                            if (verb.lemma_, obj, tclass) not in seen:
                                seen.add((verb.lemma_, obj, tclass))
                                rows.append((sr.number, verb.lemma_, obj, tclass))
    return rows


def ability_category() -> list[tuple[str, str]]:
    """(rule, name) — §113.3a-d the four ability categories."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "113":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("113.3") or sr.number == "113.3":
                        continue
                    m = re.match(r"(\w+) abilities", sr.text.strip())
                    if m:
                        rows.append((sr.number, m.group(1).lower()))
    return rows


def _creates(text: str) -> str | None:
    low = text.lower()
    if "replacement effect" in low and "prevention effect" in low:
        return "replacement_and_prevention"
    if "replacement effect" in low:
        return "replacement"
    if "prevention effect" in low:
        return "prevention"
    if "triggered ability" in low:
        return "triggered_ability"
    if "keyword" in low and "gain that" in low:
        return "keyword_grant"
    if "indicates how much" in low:
        return "characteristic"
    if "power" in low and "toughness" in low:
        return "modifies_pt"
    return None


def counter_kind() -> list[tuple[str, str, str]]:
    """(rule, kind, creates) — §122.1 what each counter kind does (abstains if unclassifiable)."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "122":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("122.1") or sr.number == "122.1":
                        continue
                    m = _KIND.search(sr.text)
                    creates = _creates(sr.text)
                    if not m or creates is None:
                        continue
                    kind = m.group(1).lower().replace("+x/+y", "p_t").replace("/", "_")
                    rows.append((sr.number, kind, creates))
    return rows


def build() -> tuple[str, dict]:
    pm, ac, ck = player_may(), ability_category(), counter_kind()
    p = Program()
    p.comment("concepts.dl — §1 Game Concepts definitional families, interpreted from rules.txt.")
    p.comment("player_may(action, object, timing); ability_category(name); counter_kind(kind, creates). GENERATED.")
    p.blank()
    p.decl("player_may", [("action", "symbol"), ("object", "symbol"), ("timing", "symbol")])
    p.decl("ability_category", [("name", "symbol")])
    p.decl("counter_kind", [("kind", "symbol"), ("creates", "symbol")])
    p.blank()
    for _n, a, o, t in pm:
        p.fact(f'player_may("{a}", "{o}", "{t}")')
    p.blank()
    for _n, name in ac:
        p.fact(f'ability_category("{name}")')
    p.blank()
    for _n, kind, creates in ck:
        p.fact(f'counter_kind("{kind}", "{creates}")')
    p.blank()
    p.output("player_may")
    p.output("ability_category")
    p.output("counter_kind")
    p.blank()
    p.comment("conformance — spot-check the definitions the rules state plainly")
    p.conformance(
        [("expect_may", [("action", "symbol"), ("object", "symbol"), ("timing", "symbol")]),
         ("expect_cat", [("name", "symbol")]),
         ("expect_ck", [("kind", "symbol"), ("creates", "symbol")])],
        [("may", "expect_may(A, O, T)", "miss", "player_may(A, O, T)"),
         ("cat", "expect_cat(N)", "miss", "ability_category(N)"),
         ("ck", "expect_ck(K, C)", "miss", "counter_kind(K, C)")],
    )
    for atom in ['expect_may("cast", "instant_spell", "priority")',
                 'expect_may("activate", "mana_ability", "priority")']:
        p.fact(atom)
    for atom in ['expect_cat("triggered")', 'expect_cat("static")']:
        p.fact(atom)
    for atom in ['expect_ck("shield", "replacement_and_prevention")',
                 'expect_ck("rad", "triggered_ability")',
                 'expect_ck("keyword", "keyword_grant")']:
        p.fact(atom)
    return p.text(), {"player_may": len(pm), "ability_category": len(ac), "counter_kind": len(ck)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/concepts.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/concepts.dl ({report['player_may']} player_may, "
          f"{report['ability_category']} ability_category, {report['counter_kind']} counter_kind)")


if __name__ == "__main__":
    main()
