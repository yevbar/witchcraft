"""Split the MTG Comprehensive Rules (rules.txt) into structured sections.

This module is a *splitter*, not a *parser* of rule semantics. It cuts the
document into its top-level sections (frontmatter-style metadata, contents,
rules body, glossary, credits) and assembles the rules body into a nested tree
of Section -> Group -> Rule -> Subrule objects. It deliberately does NOT
interpret the meaning of a rule's text -- the raw text of each rule is preserved
verbatim so a later pass can evaluate it into a richer model.

Document layout (as of the 2026-04-17 rules):

    Magic: The Gathering Comprehensive Rules   <- title
    These rules are effective as of ...         <- effective date
    Introduction                                <- intro prose
    Contents                                    <- table of contents (kept raw)
    1. Game Concepts                            <- section header
    100. General                                <- rule-group header
    100.1. ...                                  <- rule
    100.1a ...                                  <- subrule
        Example: ...                            <- example (attached to its rule)
    Glossary                                    <- term/definition blocks
    Credits                                     <- credits prose
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from models import Document, Group, GlossaryEntry, Metadata, Rule, Section, Subrule


# --- block classification ---------------------------------------------------
#
# Each blank-line-delimited block in the rules body starts with a token that
# tells us what it is. We only look at that leading token; everything after it
# is kept as raw text.

# 100.1a  (subrule: group.rule + one-or-more letters; trailing dot is optional,
# e.g. both "100.1a ..." and "119.1d. ..." occur in the source)
RE_SUBRULE = re.compile(r"^(\d{3}\.\d+[a-z]+)\.?\s+(.*)$", re.DOTALL)
# 100.1.  (rule: group.rule; trailing dot is optional, e.g. "606.5 ..." occurs)
RE_RULE = re.compile(r"^(\d{3}\.\d+)\.?\s+(.*)$", re.DOTALL)
# 100. General  (rule-group header: 3-digit number + title)
RE_GROUP = re.compile(r"^(\d{3})\.\s+(.*)$", re.DOTALL)
# 1. Game Concepts  (section header: 1-2 digit number + title)
RE_SECTION = re.compile(r"^(\d{1,2})\.\s+(.*)$", re.DOTALL)
# Example: ...  (an example, attached to the preceding rule/subrule)
RE_EXAMPLE = re.compile(r"^Example:\s*(.*)$", re.DOTALL)


@dataclass
class _RawBlock:
    """A classified-but-not-yet-nested block from the rules body."""

    kind: str  # section | group | rule | subrule | example
    number: str
    text: str
    examples: list[str] = field(default_factory=list)
    line: int = 0


def _find_standalone(lines: list[str], target: str) -> list[int]:
    """Return 0-based indices of lines whose stripped content equals `target`."""
    return [i for i, ln in enumerate(lines) if ln.strip() == target]


def _iter_blocks(lines: list[str], start: int, end: int):
    """Yield (first_line_index, block_lines) for each blank-delimited block.

    A block is a maximal run of non-blank lines within lines[start:end].
    """
    i = start
    while i < end:
        if not lines[i].strip():
            i += 1
            continue
        block_start = i
        block: list[str] = []
        while i < end and lines[i].strip():
            block.append(lines[i])
            i += 1
        yield block_start, block


def _split_metadata(lines: list[str], contents_idx: int) -> Metadata:
    """Extract title, effective date, and introduction from the head block."""
    head = lines[:contents_idx]
    title = head[0].strip() if head else ""

    effective_date = None
    for ln in head:
        m = re.search(r"effective as of\s+(.+?)\.?\s*$", ln.strip())
        if m:
            effective_date = m.group(1).strip()
            break

    # Introduction: prose following the "Introduction" heading up to Contents.
    intro_lines: list[str] = []
    for idx, ln in enumerate(head):
        if ln.strip() == "Introduction":
            intro_lines = [l.rstrip() for l in head[idx + 1:]]
            break
    introduction = "\n".join(intro_lines).strip()

    return Metadata(title=title, effective_date=effective_date, introduction=introduction)


def _classify_block(block_start: int, block: list[str]) -> _RawBlock:
    """Classify one rules-body block into a _RawBlock.

    An "Example:" block (one whose first line is an example) is returned with
    kind "example"; the assembler attaches it to the preceding rule/subrule.
    """
    first = block[0].strip()

    # A block that starts with "Example:" is an orphaned example -- it belongs
    # to whatever rule/subrule preceded it.
    if RE_EXAMPLE.match(first):
        examples = [
            RE_EXAMPLE.match(ln.strip()).group(1).strip()
            for ln in block
            if RE_EXAMPLE.match(ln.strip())
        ]
        return _RawBlock(kind="example", number="", text="", examples=examples,
                         line=block_start + 1)

    rest = block[1:]
    examples = [
        RE_EXAMPLE.match(ln.strip()).group(1).strip()
        for ln in rest
        if RE_EXAMPLE.match(ln.strip())
    ]
    # Any non-example continuation lines get appended to the text (rare).
    continuation = [ln.strip() for ln in rest if not RE_EXAMPLE.match(ln.strip())]

    def build(kind: str, number: str, text: str) -> _RawBlock:
        if continuation:
            text = (text + "\n" + "\n".join(continuation)).strip()
        return _RawBlock(kind=kind, number=number, text=text.strip(),
                         examples=examples, line=block_start + 1)

    # Order matters: subrule and rule before the broader header patterns.
    if m := RE_SUBRULE.match(first):
        return build("subrule", m.group(1), m.group(2))
    if m := RE_RULE.match(first):
        return build("rule", m.group(1), m.group(2))
    if m := RE_GROUP.match(first):
        return build("group", m.group(1), m.group(2))
    if m := RE_SECTION.match(first):
        return build("section", m.group(1), m.group(2))

    raise ValueError(
        f"Unrecognized block at line {block_start + 1}: {first[:80]!r}"
    )


def _assemble_sections(lines: list[str], start: int, end: int) -> list[Section]:
    """Build the Section -> Group -> Rule -> Subrule tree from the rules body."""
    sections: list[Section] = []
    section: Section | None = None
    group: Group | None = None
    rule: Rule | None = None
    subrule: Subrule | None = None

    for block_start, block in _iter_blocks(lines, start, end):
        b = _classify_block(block_start, block)

        if b.kind == "example":
            # Attach to the most recently opened rule/subrule.
            target = subrule or rule
            if target is None:
                raise ValueError(f"Example before any rule at line {b.line}")
            target.examples.extend(b.examples)
            continue

        if b.kind == "section":
            section = Section(number=b.number, title=b.text, line=b.line)
            sections.append(section)
            group = rule = subrule = None
        elif b.kind == "group":
            if section is None:
                raise ValueError(f"Group before any section at line {b.line}")
            group = Group(number=b.number, title=b.text, line=b.line)
            section.groups.append(group)
            rule = subrule = None
        elif b.kind == "rule":
            if group is None:
                raise ValueError(f"Rule before any group at line {b.line}")
            rule = Rule(number=b.number, text=b.text, examples=b.examples,
                        line=b.line)
            group.rules.append(rule)
            subrule = None
        elif b.kind == "subrule":
            if rule is None:
                raise ValueError(f"Subrule before any rule at line {b.line}")
            subrule = Subrule(number=b.number, text=b.text, examples=b.examples,
                              line=b.line)
            rule.subrules.append(subrule)

    return sections


def _split_glossary(lines: list[str], start: int, end: int) -> list[GlossaryEntry]:
    entries: list[GlossaryEntry] = []
    for block_start, block in _iter_blocks(lines, start, end):
        term = block[0].strip()
        definition = "\n".join(l.rstrip() for l in block[1:]).strip()
        entries.append(GlossaryEntry(term=term, definition=definition, line=block_start + 1))
    return entries


def split(text: str) -> Document:
    """Split the raw rules text into a structured Document."""
    text = text.lstrip("﻿")  # strip a leading BOM if present
    lines = text.splitlines()

    contents_indices = _find_standalone(lines, "Contents")
    glossary_indices = _find_standalone(lines, "Glossary")
    credits_indices = _find_standalone(lines, "Credits")

    if not contents_indices:
        raise ValueError("Could not locate the 'Contents' header.")
    if len(glossary_indices) < 1 or len(credits_indices) < 1:
        raise ValueError("Could not locate Glossary/Credits sections.")

    contents_idx = contents_indices[0]

    # The body starts right after the table of contents. The TOC's final entry
    # is "Credits", so the first standalone "Credits" line marks the TOC end and
    # the body begins at the next non-blank line.
    body_start = credits_indices[0] + 1
    # The real Glossary is the last standalone "Glossary" (the first one is a
    # TOC entry); likewise the real Credits is the last "Credits".
    glossary_idx = glossary_indices[-1]
    credits_idx = credits_indices[-1]

    metadata = _split_metadata(lines, contents_idx)
    contents_raw = "\n".join(
        l.rstrip() for l in lines[contents_idx + 1:body_start - 1]
    ).strip()
    sections = _assemble_sections(lines, body_start, glossary_idx)
    glossary = _split_glossary(lines, glossary_idx + 1, credits_idx)
    credits = "\n".join(l.rstrip() for l in lines[credits_idx + 1:]).strip()

    return Document(
        metadata=metadata,
        contents_raw=contents_raw,
        sections=sections,
        glossary=glossary,
        credits=credits,
    )
