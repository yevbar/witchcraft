"""Build datalog/keyword_defs.dl — keyword reminder/definition catalog from the
"[Keyword] means [definition]" sentences in §702 (e.g. hexproof, vanishing, the
gift/partner keywords). keyword_means(keyword, scope, "definition") captures each
keyword's rules-text definition VERBATIM from rules.txt (faithful, not paraphrased).

Complements keyword_taxonomy (the keyword -> ability-class map) with the actual
definition text. Regex extraction; deterministic.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

# "Keyword" [on a/an scope] means "definition..."   (curly quotes normalized first)
_MEANS = re.compile(r'^"?([A-Z][\w ]+?)"? (?:on (?:a|an) (\w+) )?means:?\s*"?(.+)$')


def _slug(kw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", kw.lower()).strip("_")


def _esc(s: str) -> str:
    return s.replace('"', "'").rstrip(" '”").strip()


def extract() -> list[tuple[str, str, str, str]]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out, seen = [], set()
    for s in doc.sections:
        for g in s.groups:
            if g.number != "702":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    first = sr.text.split(". ")[0].replace("’", "'").replace("“", '"').replace("”", '"')
                    if " means" not in first:
                        continue
                    m = _MEANS.match(first)
                    if not m:
                        continue
                    kw = _slug(m.group(1))
                    if not kw or kw in seen:
                        continue
                    seen.add(kw)
                    out.append((sr.number, kw, m.group(2) or "any", _esc(m.group(3))))
    return out


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("keyword_defs.dl — §702 keyword 'means' definitions extracted VERBATIM from rules.txt.")
    p.comment("keyword_means(keyword, scope, definition). GENERATED, not hand-written.")
    p.blank()
    p.decl("keyword_means", [("keyword", "symbol"), ("scope", "symbol"), ("definition", "symbol")])
    p.blank()
    for _num, kw, scope, defn in rows:
        p.fact(f'keyword_means("{kw}", "{scope}", "{defn}")')
    p.blank()
    p.output("keyword_means")
    p.blank()
    p.comment("conformance — every definition is non-empty")
    p.conformance(
        [("expect_defined", [("keyword", "symbol")])],
        [("defined", "expect_defined(K)", "miss", "keyword_means(K, _, _)")],
    )
    for _num, kw, _s, _d in rows[:3]:
        p.fact(f'expect_defined("{kw}")')
    return p.text(), {"count": len(rows), "keywords": [kw for _, kw, _, _ in rows]}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/keyword_defs.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/keyword_defs.dl ({report['count']} keyword definitions)")


if __name__ == "__main__":
    main()
