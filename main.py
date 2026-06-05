"""CLI entry point for splitting the MTG Comprehensive Rules.

Usage:
    python main.py rules.txt            # human-readable summary
    python main.py rules.txt --json     # full structured dump to stdout
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from models import Document
from rules_parser import split


def summary(doc: Document) -> str:
    groups = [g for s in doc.sections for g in s.groups]
    rules = [r for g in groups for r in g.rules]
    subrules = [sr for r in rules for sr in r.subrules]
    n_examples = sum(len(r.examples) for r in rules) + sum(len(sr.examples) for sr in subrules)
    lines = [
        f"Title:           {doc.metadata.title}",
        f"Effective date:  {doc.metadata.effective_date}",
        f"Intro chars:     {len(doc.metadata.introduction)}",
        f"Contents chars:  {len(doc.contents_raw)}",
        "Rules body:",
        f"    sections {len(doc.sections)}",
        f"    groups   {len(groups)}",
        f"    rules    {len(rules)}",
        f"    subrules {len(subrules)}",
        f"    examples {n_examples}",
        f"Glossary terms:  {len(doc.glossary)}",
        f"Credits chars:   {len(doc.credits)}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="Path to rules.txt")
    parser.add_argument("--json", action="store_true", help="Dump full structure as JSON")
    parser.add_argument("--encoding", default="utf-8")
    args = parser.parse_args(argv)

    text = args.path.read_text(encoding=args.encoding)
    doc = split(text)

    if args.json:
        sys.stdout.write(doc.model_dump_json(indent=2))
        sys.stdout.write("\n")
    else:
        print(summary(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
