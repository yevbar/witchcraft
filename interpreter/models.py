"""Pydantic model for the split MTG Comprehensive Rules document.

The rules body is a strict hierarchy:

    Section  (e.g. "1. Game Concepts")
      Group  (e.g. "100. General")
        Rule       (e.g. "100.1")
          Subrule  (e.g. "100.1a")

Each level is its own object and contains its children, rather than a single
flat block type tagged with a `kind`. Rule/subrule text is still kept raw -- a
later evaluative pass can build richer meaning on top of these.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Subrule(BaseModel):
    """A lettered subrule, e.g. "100.1a"."""

    number: str
    text: str
    examples: list[str] = Field(default_factory=list)
    line: int = 0


class Rule(BaseModel):
    """A numbered rule, e.g. "100.1", with its lettered subrules."""

    number: str
    text: str
    examples: list[str] = Field(default_factory=list)
    subrules: list[Subrule] = Field(default_factory=list)
    line: int = 0


class Group(BaseModel):
    """A rule group, e.g. "100. General", with its rules."""

    number: str
    title: str
    rules: list[Rule] = Field(default_factory=list)
    line: int = 0


class Section(BaseModel):
    """A top-level section, e.g. "1. Game Concepts", with its groups."""

    number: str
    title: str
    groups: list[Group] = Field(default_factory=list)
    line: int = 0


class GlossaryEntry(BaseModel):
    term: str
    definition: str
    line: int = 0


class Metadata(BaseModel):
    """Frontmatter-style metadata pulled from the document head."""

    title: str
    effective_date: str | None = None
    introduction: str


class Document(BaseModel):
    metadata: Metadata
    contents_raw: str          # the table of contents, kept verbatim (unparsed)
    sections: list[Section] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    credits: str
