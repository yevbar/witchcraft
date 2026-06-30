"""Build datalog/costs.dl — §118 cost definitions, interpreted from rules.txt.

Two regular families:

  §118.3  "Paying [mana] is done by removing ... from a player's mana pool."
          "Paying [life] is done by subtracting ... from a player's life total."
        -> cost_payment(resource, method)

  §118.8/§118.9  "An [additional/alternative] cost is a cost listed in a spell's text ..."
                 "[Additional/Alternative] costs are ... optional."
        -> cost_type(name)  /  cost_type_optional(name)

Hybrid: regex anchors each fixed frame; the payment method is a canonical slug per resource
(remove_from_mana_pool / subtract_from_life_total). These ground the engine's cost model
(can_afford pays mana from a pool; paying life subtracts from the life total).
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

import re
from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split

_PAY = re.compile(r"Paying (\w+) is done by (\w+)", re.I)
_TYPE = re.compile(r"An (additional|alternative) cost is a cost", re.I)
_OPT = re.compile(r"(additional|alternative) costs (?:are|may be)[\w ]*optional", re.I)
_METHOD = {("mana", "removing"): "remove_from_mana_pool",
           ("life", "subtracting"): "subtract_from_life_total"}


def _doc():
    return split(Path("rules.txt").read_text(encoding="utf-8"))


def cost_payment() -> list[tuple[str, str, str]]:
    """(rule, resource, method) — §118.3 how each resource is paid (abstains if unmapped)."""
    rows = []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "118":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    m = _PAY.search(sr.text)
                    if not m:
                        continue
                    method = _METHOD.get((m.group(1).lower(), m.group(2).lower()))
                    if method:
                        rows.append((sr.number, m.group(1).lower(), method))
    return rows


def cost_types() -> tuple[list, list]:
    """(cost_type rows, optional rows), each (rule, name) — §118.8/§118.9."""
    types, opt = [], []
    for s in _doc().sections:
        for g in s.groups:
            if g.number != "118":
                continue
            for r in g.rules:
                for sr in [r] + r.subrules:
                    mt = _TYPE.search(sr.text)
                    if mt:
                        types.append((sr.number, mt.group(1).lower()))
                    mo = _OPT.search(sr.text)
                    if mo:
                        opt.append((sr.number, mo.group(1).lower()))
    return types, opt


def build() -> tuple[str, dict]:
    pay = cost_payment()
    types, opt = cost_types()
    p = Program()
    p.comment("costs.dl — §118 cost definitions, interpreted from rules.txt.")
    p.comment("cost_payment(resource, method); cost_type(name); cost_type_optional(name). GENERATED.")
    p.blank()
    p.decl("cost_payment", [("resource", "symbol"), ("method", "symbol")])
    p.decl("cost_type", [("name", "symbol")])
    p.decl("cost_type_optional", [("name", "symbol")])
    p.blank()
    for _n, res, method in pay:
        p.fact(f'cost_payment("{res}", "{method}")')
    p.blank()
    for _n, name in types:
        p.fact(f'cost_type("{name}")')
    for _n, name in opt:
        p.fact(f'cost_type_optional("{name}")')
    p.blank()
    p.output("cost_payment")
    p.output("cost_type")
    p.output("cost_type_optional")
    p.blank()
    p.comment("conformance — spot-check the cost rules §118 states plainly")
    p.conformance(
        [("expect_pay", [("resource", "symbol"), ("method", "symbol")]),
         ("expect_type", [("name", "symbol")])],
        [("pay", "expect_pay(R, M)", "miss", "cost_payment(R, M)"),
         ("type", "expect_type(N)", "miss", "cost_type(N)")],
    )
    for atom in ['expect_pay("mana", "remove_from_mana_pool")',
                 'expect_pay("life", "subtract_from_life_total")']:
        p.fact(atom)
    for atom in ['expect_type("additional")', 'expect_type("alternative")']:
        p.fact(atom)
    return p.text(), {"pay": len(pay), "types": len(types), "opt": len(opt)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/costs.dl").write_text(source, encoding="utf-8")
    print(f"wrote datalog/costs.dl ({report['pay']} cost_payment, {report['types']} cost_type, "
          f"{report['opt']} cost_type_optional)")


if __name__ == "__main__":
    main()
