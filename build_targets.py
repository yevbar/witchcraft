"""Build datalog/targets.dl — §115 targeting families, interpreted from rules.txt.

Three regular families:

  §115.1a-d  "An [instant or sorcery spell / Aura spell / activated ability / triggered
              ability] is targeted if it ... 'target'."   -> targeted_kind(kind)
  §115.7a-d  "change the target(s)" / "change a target" / "change any targets" /
              "choose new targets"                          -> retarget_phrase(phrase)
  §115.9a-c  "[spell or ability] with [N] targets" / "that targets [x]" / "that targets
              only [x]"                                     -> target_check_phrase(phrase)

The ability kind is classified by keyword; the §115.7/§115.9 instruction wordings are captured
verbatim from their quotes (these are the canonical phrasings effects use, so faithful capture
is the accurate move). Hybrid: regex isolates the quoted phrase, a lexicon names the kind.
"""

from __future__ import annotations

import re
from pathlib import Path

from dlgen import Program
from rules_parser import split

_QUOTE = re.compile(r"“([^”]+)”")


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def _kind(low: str) -> str | None:
    if "instant or sorcery" in low:
        return "instant_or_sorcery"
    if "aura" in low:
        return "aura"
    if "activated ability" in low:
        return "activated"
    if "triggered ability" in low:
        return "triggered"
    return None


def targeted_kinds() -> list[tuple[str, str]]:
    """(rule, kind) — §115.1a-d the ability kinds that are targeted when they say 'target'."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "115":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith("115.1") or sr.number == "115.1":
                        continue
                    low = sr.text.lower()
                    if "targeted" not in low:
                        continue
                    k = _kind(low)
                    if k:
                        rows.append((sr.number, k))
    return rows


def _phrases(prefix: str) -> list[tuple[str, str]]:
    """(rule, quoted phrase) for §115.x subrules under prefix — the quoted instruction wordings
    that concern targets (excludes the §115.7f 'divide'/'distribute' quotes)."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "115":
                continue
            for r in g.rules:
                for sr in r.subrules:
                    if not sr.number.startswith(prefix) or sr.number == prefix:
                        continue
                    for q in _QUOTE.findall(sr.text):
                        if "target" in q.lower():
                            rows.append((sr.number, q.replace('"', "'").strip()))
                            break
    return rows


def retarget_phrases() -> list[tuple[str, str]]:
    return _phrases("115.7")


def target_check_phrases() -> list[tuple[str, str]]:
    return _phrases("115.9")


def build() -> tuple[str, dict]:
    kinds, retarget, checks = targeted_kinds(), retarget_phrases(), target_check_phrases()
    p = Program()
    p.comment("targets.dl — §115 targeting families, interpreted from rules.txt.")
    p.comment("targeted_kind(kind); retarget_phrase(phrase); target_check_phrase(phrase). GENERATED.")
    p.blank()
    p.decl("targeted_kind", [("kind", "symbol")])
    p.decl("retarget_phrase", [("phrase", "symbol")])
    p.decl("target_check_phrase", [("phrase", "symbol")])
    p.blank()
    for _n, k in kinds:
        p.fact(f'targeted_kind("{k}")')
    p.blank()
    for _n, ph in retarget:
        p.fact(f'retarget_phrase("{ph}")')
    p.blank()
    for _n, ph in checks:
        p.fact(f'target_check_phrase("{ph}")')
    p.blank()
    p.output("targeted_kind")
    p.output("retarget_phrase")
    p.output("target_check_phrase")
    p.blank()
    p.comment("conformance — spot-check the targeting families §115 states plainly")
    p.conformance(
        [("expect_kind", [("kind", "symbol")]), ("expect_retarget", [("phrase", "symbol")])],
        [("kind", "expect_kind(K)", "miss", "targeted_kind(K)"),
         ("retarget", "expect_retarget(P)", "miss", "retarget_phrase(P)")],
    )
    for atom in ['expect_kind("activated")', 'expect_kind("triggered")', 'expect_kind("aura")']:
        p.fact(atom)
    for atom in ['expect_retarget("change a target")', 'expect_retarget("choose new targets")']:
        p.fact(atom)
    return p.text(), {"kinds": len(kinds), "retarget": len(retarget), "checks": len(checks)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/targets.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/targets.dl ({report['kinds']} targeted_kind, "
          f"{report['retarget']} retarget_phrase, {report['checks']} target_check_phrase)")


if __name__ == "__main__":
    main()
