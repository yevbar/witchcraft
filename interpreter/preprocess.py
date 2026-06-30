"""Preprocess rule text by replacing costs and URLs with opaque, alphabetic tokens.

MTG cost symbols are written inside curly braces -- ``{W}``, ``{2}``, ``{T}``,
``{W/U}``, ``{2/B}`` -- which a whitespace/NLP tokenizer shreds into several
tokens (``{``, ``W``, ``}``). URLs (``WPN.Wizards.com/en/rules-documents``)
fragment too.

We replace each cost expression, URL, and subrule citation with a single, purely
*alphabetic*, deterministic placeholder token, and stash the verbatim original in
a ``legend``:

    {W}{W}{W}{2}                       ->  costXXXXXX
    {W/U}                              ->  costYYYYYY     (distinct from {W}{U}!)
    WPN.Wizards.com/en/rules-documents ->  urlZZZZZZ
    205.3g  /  601.2a–h                ->  subruleWWWWWW  (self-identifying prefix)
    701.33  /  509.2                   ->  ruleVVVVVV     (plain rule reference)

Why opaque + alphabetic (rather than a readable mapping like ``{W/U}`` -> ``WU``):

* A readable mapping is *lossy and ambiguous* -- ``{W/U}`` (pay W OR U) collapses
  to ``WU``, which is indistinguishable from the run ``{W}{U}`` (pay W AND U).
  The hybrid's optionality is destroyed. Keeping the original in the legend
  preserves it exactly.
* Purely alphabetic tokens survive tokenization whole. Mixed digit/letter tokens
  like ``1G`` get re-split by spaCy's number/letter rules; ``costabcdef`` never
  does.

Tokens are content-derived (a base-26 hash of the original), so the same cost or
URL always maps to the same token -- stable across runs and fully referenceable
via ``Replacement.lookup``.
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


import hashlib
import re
from dataclasses import dataclass, field

# Matches one cost expression: a run of one or more *adjacent* cost symbols,
# e.g. "{W}", "{W/U}", "{1}{U}{U}", "{2/B}{G}". Adjacent symbols (no separator)
# form a single expression and become a single token.
COST_RUN_RE = re.compile(r"(?:\{[^{}]+\})+")

# Matches a scheme-less URL: a dotted host ending in a known TLD, plus an
# optional path, e.g. "WPN.Wizards.com/en/rules-documents". Anchoring on a TLD
# avoids matching rule numbers like "100.2a". Trailing sentence punctuation is
# stripped back off in replace_urls().
URL_RE = re.compile(
    r"\b(?:[A-Za-z0-9-]+\.)+(?:com|net|org|sh|io|gov|edu)\b(?:/[^\s,)\"”']*)?",
    re.IGNORECASE,
)
_URL_TRAILING = ".,;:)\"”'"

# Matches a subrule citation -- a three-digit group, a rule number, and at least
# one subrule letter -- with an optional range, e.g. "205.3g", "601.2a–h",
# "608.2c–608.3e". The trailing letter is what distinguishes a subrule reference
# from a plain rule reference like "701.33". spaCy otherwise splits these
# ("205.3g" -> "205.3", "g"); hashing keeps each citation a single, identifiable
# token.
_SUBRULE = r"\d{3}\.\d+[a-z]+"                    # 205.3g
_RANGE_END = r"(?:\d{3}\.\d+[a-z]*|[a-z]+)"       # 608.3e  or just "h"
SUBRULE_REF_RE = re.compile(rf"\b{_SUBRULE}(?:[–-]{_RANGE_END})?")

# Matches a plain rule citation -- group + rule number with NO subrule letter,
# e.g. "701.33", "509.2", with an optional range ("509.2–4", "509.2–510.1").
# The negative lookahead ensures we never grab the rule part of a subrule
# citation; subrule references are replaced first regardless. These usually
# survive spaCy as a single numeric token, so hashing is about *identifiability*
# (tagging the token as a rule reference), not fixing fragmentation.
_RULE = r"\d{3}\.\d+"
_RULE_RANGE_END = r"(?:\d{3}\.\d+|\d+)"           # 510.1  or just "4"
RULE_REF_RE = re.compile(rf"\b{_RULE}(?![a-z])(?:[–-]{_RULE_RANGE_END})?")

# Length of the alphabetic hash. 26**6 ~= 309M keeps collisions negligible for
# the few hundred distinct costs/URLs in the rules.
_HASH_LEN = 6


def _alpha_hash(text: str, length: int = _HASH_LEN) -> str:
    """Deterministic, purely lowercase-alphabetic hash of ``text``."""
    n = int.from_bytes(hashlib.sha1(text.encode("utf-8")).digest(), "big")
    out = []
    for _ in range(length):
        n, rem = divmod(n, 26)
        out.append(chr(ord("a") + rem))
    return "".join(out)


def cost_token(expression: str) -> str:
    """Stable alphabetic token for a cost expression, e.g. ``costabcdef``."""
    return "cost" + _alpha_hash(expression)


def url_token(url: str) -> str:
    """Stable alphabetic token for a URL, e.g. ``urlabcdef``."""
    return "url" + _alpha_hash(url.lower())


def subrule_token(citation: str) -> str:
    """Stable alphabetic token for a subrule citation, e.g. ``subruleabcdef``.

    The ``subrule`` prefix lets a later pass recognize, from the token alone,
    that it refers to a subrule (vs a cost or URL).
    """
    return "subrule" + _alpha_hash(citation)


def rule_token(citation: str) -> str:
    """Stable alphabetic token for a rule citation, e.g. ``ruleabcdef``.

    The ``rule`` prefix is distinct from ``subrule`` (``"subrule".startswith
    ("rule")`` is False), so the two reference kinds stay distinguishable.
    """
    return "rule" + _alpha_hash(citation)


@dataclass
class Replacement:
    """Result of replacing costs/URLs in a piece of text."""

    text: str                                   # text with placeholders
    legend: dict[str, list[str]] = field(default_factory=dict)  # token -> originals

    def lookup(self, token: str) -> list[str]:
        """Return the original string(s) a placeholder token stands for."""
        return self.legend.get(token, [])


def replace_costs(text: str) -> Replacement:
    """Replace every cost expression with an opaque alphabetic token.

    Each maximal run of adjacent ``{..}`` symbols becomes one token; the legend
    maps that token back to the verbatim original (``{W/U}`` stays ``{W/U}``),
    so optionality and pip order are fully preserved.
    """
    legend: dict[str, set[str]] = {}

    def repl(match: re.Match) -> str:
        expression = match.group(0)
        token = cost_token(expression)
        legend.setdefault(token, set()).add(expression)
        return token

    out = COST_RUN_RE.sub(repl, text)
    return Replacement(text=out, legend={k: sorted(v) for k, v in legend.items()})


def replace_urls(text: str) -> Replacement:
    """Replace each URL with an opaque alphabetic ``url<hash>`` token.

    Trailing sentence punctuation is preserved rather than absorbed, and the
    legend recovers the original URL.
    """
    legend: dict[str, set[str]] = {}

    def repl(match: re.Match) -> str:
        url = match.group(0)
        trailing = ""
        while url and url[-1] in _URL_TRAILING:
            trailing = url[-1] + trailing
            url = url[:-1]
        token = url_token(url)
        legend.setdefault(token, set()).add(url)
        return token + trailing

    out = URL_RE.sub(repl, text)
    return Replacement(text=out, legend={k: sorted(v) for k, v in legend.items()})


def replace_subrule_refs(text: str) -> Replacement:
    """Replace each subrule citation with an opaque ``subrule<hash>`` token.

    Ranges (``601.2a–h``) are kept intact as a single citation; the legend maps
    the token back to the verbatim citation so a later pass can resolve or
    expand it.
    """
    legend: dict[str, set[str]] = {}

    def repl(match: re.Match) -> str:
        citation = match.group(0)
        token = subrule_token(citation)
        legend.setdefault(token, set()).add(citation)
        return token

    out = SUBRULE_REF_RE.sub(repl, text)
    return Replacement(text=out, legend={k: sorted(v) for k, v in legend.items()})


def replace_rule_refs(text: str) -> Replacement:
    """Replace each plain rule citation with an opaque ``rule<hash>`` token.

    Must run AFTER replace_subrule_refs so it can't grab the rule portion of a
    subrule citation. Ranges are kept intact; the legend recovers the original.
    """
    legend: dict[str, set[str]] = {}

    def repl(match: re.Match) -> str:
        citation = match.group(0)
        token = rule_token(citation)
        legend.setdefault(token, set()).add(citation)
        return token

    out = RULE_REF_RE.sub(repl, text)
    return Replacement(text=out, legend={k: sorted(v) for k, v in legend.items()})


def preprocess(text: str) -> Replacement:
    """Run the full chain: URLs, subrule citations, rule citations, costs.

    Order matters: URLs first (so their digits/dots can't be mistaken for a
    citation), then subrule citations (with letters), then plain rule citations
    (so subrule refs aren't partially matched), then costs. Returns the rewritten
    text and a combined ``legend`` mapping every produced placeholder token back
    to the verbatim original string(s) it stands for.
    """
    urls = replace_urls(text)
    subrefs = replace_subrule_refs(urls.text)
    rulerefs = replace_rule_refs(subrefs.text)
    costs = replace_costs(rulerefs.text)
    return Replacement(
        text=costs.text,
        legend={**urls.legend, **subrefs.legend, **rulerefs.legend, **costs.legend},
    )


if __name__ == "__main__":
    # Tiny self-demo -- note {W/U} and {W}{U} now get *distinct* tokens.
    samples = [
        "Pay {W}{W}{W}{2} and {T}: draw a card.",
        "Pay {W/U} (hybrid) vs {W}{U} (two pips).",
        "As an additional cost, pay {2/B} or {W/U/P}.",
        "found at WPN.Wizards.com/en/rules-documents.",
        "See rule 205.3g and rules 601.2a–h for details.",
        "See rule 701.33 and rule 509.2, plus 509.2–4.",
    ]
    for s in samples:
        r = preprocess(s)
        print(s)
        print("  ->", r.text)
        print("  legend:", r.legend)
        print()
