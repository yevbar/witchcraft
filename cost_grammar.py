"""lark grammar for MTG mana-cost notation, with lowering to Dafny `ManaCost`.

This is the formal-sublanguage parser of the pipeline: cost strings like
``{1}{W}{W}``, ``{W/U}``, ``{2/B}``, ``{W/U/P}`` are genuinely a grammar, so lark
parses them exactly -- crucially *preserving optionality* (``{W/U}`` is a choice,
not the run ``{W}{U}``). The parsed structure then lowers to a Dafny `ManaCost`
literal for the generated spec.

    "{1}{W}{W}"  ->  [GenericSym(1), ManaSym(OfColor(W)), ManaSym(OfColor(W))]
    "{W/U}"      ->  [Hybrid([ManaSym(OfColor(W)), ManaSym(OfColor(U))])]
    "{W/P}"      ->  [Hybrid([ManaSym(OfColor(W)), LifeSym(2)])]
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lark import Lark, Transformer

# Standard life paid for one Phyrexian symbol ({W/P} = pay W or 2 life).
PHYREXIAN_LIFE = 2

GRAMMAR = r"""
    ?start: cost
    cost:   symbol+
    symbol: "{" body "}"
    body:   atom ("/" atom)*
    atom:   COLOR -> color
          | INT   -> generic
          | "C"   -> colorless
          | "S"   -> snow
          | "E"   -> energy
          | "P"   -> phyrexian
          | VAR   -> variable
    COLOR: /[WUBRG]/
    VAR:   /[XYZ]/
    %import common.INT
    %ignore " "
"""


# --- structured cost symbols (mirrors the Dafny CostSym datatype) ------------

@dataclass(frozen=True)
class ManaSym:
    pip: str          # a color "W".."G", or "C" for colorless

@dataclass(frozen=True)
class GenericSym:
    n: int

@dataclass(frozen=True)
class VarSym:
    name: str         # "X", "Y", "Z"

@dataclass(frozen=True)
class LifeSym:
    life: int

@dataclass(frozen=True)
class EnergySym:
    amount: int       # {E} -- one energy counter

@dataclass(frozen=True)
class SnowSym:
    pass              # {S} -- parsed but deferred (needs per-mana snow flags)

@dataclass(frozen=True)
class Hybrid:
    opts: tuple       # a choice among CostSym options ({W/U}, {2/B}, {W/P})


class Unsupported(Exception):
    """Raised when a cost can't yet lower to the v1 Dafny model (e.g. {S})."""


class _ToCost(Transformer):
    def color(self, items):     return ManaSym(str(items[0]))
    def generic(self, items):   return GenericSym(int(items[0]))
    def colorless(self, items): return ManaSym("C")
    def snow(self, items):      return SnowSym()
    def energy(self, items):    return EnergySym(1)
    def phyrexian(self, items): return LifeSym(PHYREXIAN_LIFE)
    def variable(self, items):  return VarSym(str(items[0]))
    def body(self, items):
        return items[0] if len(items) == 1 else Hybrid(tuple(items))
    def symbol(self, items):    return items[0]
    def cost(self, items):      return list(items)


_PARSER = Lark(GRAMMAR, parser="lalr", transformer=_ToCost())


def parse_cost(text: str) -> list:
    """Parse a cost string (e.g. ``"{1}{W}{W}"``) into a list of CostSym."""
    return _PARSER.parse(text)


# --- lowering to Dafny -------------------------------------------------------

def to_dafny_sym(sym) -> str:
    """Lower one CostSym to its Dafny constructor expression."""
    if isinstance(sym, ManaSym):
        return "ManaSym(Colorless)" if sym.pip == "C" else f"ManaSym(OfColor({sym.pip}))"
    if isinstance(sym, GenericSym):
        return f"GenericSym({sym.n})"
    if isinstance(sym, VarSym):
        return "VarSym"
    if isinstance(sym, LifeSym):
        return f"LifeSym({sym.life})"
    if isinstance(sym, EnergySym):
        return f"EnergySym({sym.amount})"
    if isinstance(sym, Hybrid):
        return "Hybrid([" + ", ".join(to_dafny_sym(o) for o in sym.opts) + "])"
    if isinstance(sym, SnowSym):
        raise Unsupported("{S} snow cost not modeled yet (needs per-mana snow flags)")
    raise TypeError(f"unknown cost symbol: {sym!r}")


def to_dafny_cost(symbols) -> str:
    """Lower a parsed cost (list of CostSym) to a Dafny `ManaCost` literal."""
    return "[" + ", ".join(to_dafny_sym(s) for s in symbols) + "]"


if __name__ == "__main__":
    for s in ["{1}{W}{W}", "{W/U}", "{2/B}", "{W/U/P}", "{W/P}", "{X}{R}"]:
        print(f"{s:12} -> {to_dafny_cost(parse_cost(s))}")
