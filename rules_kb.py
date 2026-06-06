"""rules_kb.py — a small, reviewable query layer over the TRANSPILED rules knowledge base.

The transpile.py / build_* pipeline emits descriptive facts ABOUT the rules — what each subject
may do, can't do, must do; what triggers what; what defines, relates, or composes what — into
datalog/*.dl. Those facts are the machine-readable distillation of the English rulebook, but until
now nothing READ them back. This loads them into Python as a flat index (relation -> list of
argument-dicts) with a couple of typed accessors, so the knowledge base is queryable without souffle.

Pure reader: it parses the GENERATED .dl, so it always reflects the last `python3 build.py`. No facts
are authored here — every row traces to a rule number in the .dl comments.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

# The descriptive (rules-ABOUT) relations and their argument names. Engine-execution relations
# (on_battlefield, controls, …) and scaffolding (decls, conformance) are intentionally excluded —
# this KB is the interpreted SEMANTICS of the prose, not the game-state simulator.
SCHEMA: dict[str, tuple] = {
    "action":            ("subject", "verb", "object"),
    "ability":           ("subject", "action", "qualifier_kind", "qualifier"),
    "permission":        ("subject", "action", "qualifier_kind", "qualifier"),
    "restriction":       ("subject", "action", "qualifier_kind", "qualifier"),
    "requirement":       ("subject", "action", "qualifier_kind", "qualifier"),
    "conditional":       ("trigger_subject", "trigger_verb", "outcome_subject", "outcome_verb", "kind"),
    "relation":          ("subject", "verb", "object"),
    "has_property":      ("subject", "property", "present"),
    "comparison":        ("subject", "relation", "object"),
    "is_property":       ("subject", "adjective"),
    "negation":          ("subject", "verb", "object"),
    "symbol_means":      ("glyph", "meaning"),
    "isa":               ("term", "category"),
    "derived":           ("subject", "action", "kind", "complement"),
    "effect":            ("subject", "verb", "object", "polarity"),
    "grants":            ("subject", "modal", "granted_action"),
    "count_of":          ("name", "n"),
    "action_definition": ("action",),
    "gerund_action":     ("action", "verb", "object", "polarity"),
    "some_are":          ("subject", "category", "polarity"),
    "keyword_action_trigger_timing": ("action", "timing"),
    "not_isa":           ("term", "category"),
    "attribute_of":      ("owner", "attribute", "value"),
    "trigger_condition": ("ability_class", "event_subject", "event_verb"),
}

_FACT = re.compile(r'^([a-z_]\w*)\((.*)\)\.\s*(?://.*)?$')


def _split_args(s: str) -> list:
    """Split a souffle argument list on top-level commas; unquote strings, int-ify numbers."""
    out, buf, inq = [], "", False
    for ch in s:
        if ch == '"':
            inq = not inq
        elif ch == "," and not inq:
            out.append(buf.strip())
            buf = ""
            continue
        buf += ch
    out.append(buf.strip())
    vals = []
    for a in out:
        if a.startswith('"') and a.endswith('"'):
            vals.append(a[1:-1])
        elif re.fullmatch(r"-?\d+", a):
            vals.append(int(a))
        else:
            vals.append(a)
    return vals


def load(datadir: str = "datalog") -> dict:
    """Parse every datalog/*.dl and return {relation: [argument-tuple]} for the SCHEMA relations."""
    kb: dict = defaultdict(list)
    for f in sorted(Path(datadir).glob("*.dl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or ":-" in line or line[0] in ".#/":      # skip decls/rules/outputs/comments
                continue
            m = _FACT.match(line)
            if not m or m.group(1) not in SCHEMA:
                continue
            vals = _split_args(m.group(2))
            if len(vals) == len(SCHEMA[m.group(1)]):
                kb[m.group(1)].append(tuple(vals))
    return dict(kb)


def query(kb: dict, rel: str, **where) -> list:
    """Rows of `rel` (as {arg: value} dicts) matching the given column==value constraints."""
    cols = SCHEMA[rel]
    idx = {c: i for i, c in enumerate(cols)}
    return [dict(zip(cols, row)) for row in kb.get(rel, [])
            if all(row[idx[k]] == v for k, v in where.items())]


# Relations whose FIRST argument names the entity the fact is about (so `about` can gather them).
SUBJECT_KEYED = ("isa", "not_isa", "some_are", "attribute_of", "permission", "restriction",
                 "requirement", "ability", "has_property", "action", "negation", "relation",
                 "comparison", "is_property", "effect", "grants")


def about(kb: dict, subject: str) -> dict:
    """Everything the KB asserts about a SUBJECT — what it is / isn't, its attributes, what it may,
    can't, and must do, its actions and relations — gathered across the subject-keyed relations."""
    out: dict = {}
    for rel in SUBJECT_KEYED:
        hits = [r for r in kb.get(rel, []) if r[0] == subject]
        if hits:
            out[rel] = [dict(zip(SCHEMA[rel], r)) for r in hits]
    return out


def taxonomy(kb: dict) -> dict:
    """The genus graph: category -> {terms asserted to be (a kind of) it}, from isa() plus the
    existential some_are(); a term explicitly denied (not_isa) is shown prefixed '!'. A small
    reviewable view of the interpreted type hierarchy."""
    out: dict = {}
    for term, cat in kb.get("isa", []):
        out.setdefault(cat, set()).add(term)
    for subj, cat, present in kb.get("some_are", []):
        if present == "yes":
            out.setdefault(cat, set()).add(subj + " (some)")
    for term, cat in kb.get("not_isa", []):
        out.setdefault(cat, set()).add("!" + term)
    return {cat: sorted(terms) for cat, terms in sorted(out.items())}


def contradictions(kb: dict) -> list:
    """Self-consistency check over the interpreted KB. Two severities:

      HARD  isa(X, Y) AND not_isa(X, Y) — X both is and isn't a Y. These are unconditional about the
            TERM's identity, so a hit is a genuine mis-parse to chase.
      SOFT  effect/gerund (S, V, O) asserted yes AND no. Often benign: the patterns drop a subject
            RESTRICTION, so "some effects change text" (yes) and "effects that add abilities don't
            change text" (no) collapse to the same triple though both are true. Worth a human glance,
            not necessarily a bug.

    Returns [(kind, key, note)] with kind 'isa/not_isa' (hard) or '<rel>_polarity' (soft)."""
    out = []
    isa, nis = set(kb.get("isa", [])), set(kb.get("not_isa", []))
    for term, cat in sorted(isa & nis):
        out.append(("isa/not_isa", (term, cat), "hard"))
    pol: dict = {}                                       # (rel, subject, verb, object) -> set of polarities
    for rel in ("effect", "gerund_action"):
        for row in kb.get(rel, []):
            pol.setdefault((rel, *row[:-1]), set()).add(row[-1])
    for key, ps in sorted(pol.items()):
        if {"yes", "no"} <= ps:
            out.append((key[0] + "_polarity", key[1:], "soft (subject restriction flattened)"))
    return out


def _demo() -> None:
    kb = load()
    print("rules knowledge base — interpreted from rules.txt, read back from datalog/*.dl")
    print(f"  {sum(len(v) for v in kb.values())} facts across {len(kb)} relations:")
    for rel in sorted(kb, key=lambda r: -len(kb[r])):
        print(f"    {len(kb[rel]):4}  {rel}")

    print("\nexample queries:")
    repl = query(kb, "conditional", kind="replacement")
    print(f"  replacement-effect conditionals: {len(repl)}  e.g. {repl[0] if repl else '-'}")
    evasion = query(kb, "restriction", action="be_block")
    print(f"  'can't be blocked' restrictions: {len(evasion)}  qualifiers: "
          f"{sorted({r['qualifier_kind'] for r in evasion})}")
    print(f"  symbol meanings: {[(r['glyph'], r['meaning']) for r in query(kb, 'symbol_means')][:3]}")
    neg_gerund = query(kb, "gerund_action", polarity="no")
    print(f"  gerund actions that DON'T cause something: {len(neg_gerund)}  "
          f"e.g. {[(r['action'], r['verb'], r['object']) for r in neg_gerund][:3]}")
    print(f"  keyword-action trigger timings: "
          f"{[(r['action'], r['timing']) for r in query(kb, 'keyword_action_trigger_timing')][:4]}")

    print("\ntaxonomy() — interpreted genus graph (category -> terms), abilities branch:")
    tax = taxonomy(kb)
    for cat in ("activated_ability", "keyword_ability", "static_ability"):
        if cat in tax:
            print(f"  {cat}: {tax[cat]}")

    print("\nabout('activated_ability') — every interpreted assertion about it:")
    for rel, rows in about(kb, "activated_ability").items():
        print(f"  {rel}: {len(rows)}  e.g. {rows[0]}")

    print("\nnegative taxonomy (not_isa) + attribute definitions:")
    print(f"  not_isa: {[(r['term'], r['category']) for r in query(kb, 'not_isa')]}")
    print(f"  attribute_of: {[(r['owner'], r['attribute'], r['value']) for r in query(kb, 'attribute_of')][:4]}")

    bad = contradictions(kb)
    print(f"\nconsistency check — KB self-contradictions: {len(bad)}"
          + ("  (none — the interpreted facts are internally consistent)" if not bad else ""))
    for c in bad:
        print(f"  CONFLICT {c}")


if __name__ == "__main__":
    _demo()
