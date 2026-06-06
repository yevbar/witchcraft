"""Build datalog/statements.dl — gerund-action + partial-membership statements, from rules.txt.

Two transpile.py families that the noun-subject SVO patterns structurally miss, emitted in ONE
parse pass:
  _gerund_action -> gerund_action(action, verb, object, polarity)   nominalized-action subject
                    "Doubling a creature's power creates a continuous effect" (csubj gerund, no noun
                    subject); polarity captures doesn't / won't / not.
  _some_are      -> some_are(subject, category, polarity)            partial membership
                    "Some activated abilities are loyalty abilities" — recorded as existential, NOT a
                    universal isa (only SOME are), the honest counterpart to the ontology's isa.

Both via transpile.py's spaCy dependency parse. Only gerund-led ("…ing …") and "Some …" sentences
are parsed, keeping the build fast.
"""

from __future__ import annotations

from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import transpile_rule

_KINDS = ("gerund_action", "some_are")


def extract() -> dict:
    """{pattern: [(rule, fact)]} for gerund_action / some_are, deduped by fact."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = {k: [] for k in _KINDS}
    seen = set()
    for s in doc.sections:
        for g in s.groups:
            for r in g.rules:
                for sr in [r] + r.subrules:
                    first = sr.text.split()[0].lower() if sr.text.split() else ""
                    if not (first.endswith("ing") or first == "some"):
                        continue
                    o = transpile_rule(sr.number, sr.text)
                    if o and o.pattern in _KINDS:
                        fact = o.datalog.split("   //")[0].strip()
                        if fact not in seen:
                            seen.add(fact)
                            out[o.pattern].append((sr.number, fact))
    return out


def build() -> tuple[str, dict]:
    rows = extract()
    p = Program()
    p.comment("statements.dl — gerund-action + partial-membership, TRANSPILED by transpile.py")
    p.comment("(spaCy dependency parse). gerund_action(action, verb, object, polarity); "
              "some_are(subject, category, polarity). GENERATED.")
    p.blank()
    p.decl("gerund_action", [("action", "symbol"), ("verb", "symbol"), ("object", "symbol"), ("polarity", "symbol")])
    p.decl("some_are", [("subject", "symbol"), ("category", "symbol"), ("polarity", "symbol")])
    p.blank()
    p.comment("--- gerund_action: '[Gerund] … [verb]s [object]' (polarity yes|no) ---")
    for _n, fact in rows["gerund_action"]:
        p.raw(fact)
    p.blank()
    p.comment("--- some_are: 'Some [X] are [Y]' partial membership (NOT universal isa) ---")
    for _n, fact in rows["some_are"]:
        p.raw(fact)
    p.blank()
    p.output("gerund_action", "some_are")
    p.blank()
    p.comment("conformance — spot-check a partial-membership the rules state plainly")
    p.conformance(
        [("expect_some_are", [("subject", "symbol"), ("category", "symbol")])],
        [("some_are", "expect_some_are(S, C)", "miss", "some_are(S, C, _)")])
    p.fact('expect_some_are("activated_ability", "loyalty_ability")')
    return p.text(), {k: len(rows[k]) for k in _KINDS}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/statements.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/statements.dl (gerund_action={report['gerund_action']}, some_are={report['some_are']})")


if __name__ == "__main__":
    main()
