"""engine_schema.py — introspect the souffle engine's "schema" the way you'd query a database's
information_schema (or run GraphQL introspection), so the bridge and the python shim DERIVE and VALIDATE
their interface to the engine instead of hand-maintaining it.

Souffle is a Datalog store over a fact database, but this build exposes no SQLite/JSON schema endpoint
(`--show` only dumps the AST / type-analysis / a precedence graph). The authoritative, version-controlled
schema is therefore the program text itself:

  * RELATIONS + COLUMN TYPES   — the `.decl` declarations (like a table schema).
  * OUTPUTS                    — the `.output` directives (the queryable surface the engine emits).
  * EDB (inputs)              — relations declared but never a rule head (the facts the shim must supply).
  * IDB (derived)             — relations that appear as a rule head (computed by the engine).

The VOCABULARY the engine understands — trigger-event names ("etb_self", "your_sacrifice") and the like —
is NOT in the relation schema; those are string CONSTANTS inside rule bodies (more like stored-procedure
source than a column type). We recover them by scanning the rules for the constants in the relevant
position — e.g. every event name the `fires` rules accept. That makes "what events does the engine support?"
a query (`supported_events()`) rather than a manual grep, and lets `validate_bridge()` assert the bridge's
_EVENT table only ever targets real engine events and report which engine events still have no mapping.

driver.py (`DECLARED`) and engine_native.py (`_edb`) already parse `.decl` ad hoc; this module is the single
place that should live, so the shim's relation lists are schema-derived, not hand-copied.

Run: python3 engine_schema.py    # print the schema + the bridge-vs-engine event coverage report
"""

from __future__ import annotations

import re
from pathlib import Path

from mtg import _paths       # resolves datalog/ whether running from the repo or an installed wheel

_RULES_PATH = _paths.datalog("engine_rules.dl")
_RULES = _RULES_PATH.read_text()


def relations() -> dict[str, list[tuple[str, str]]]:
    """{relation: [(column, type), ...]} for every `.decl` — the engine's relation schema."""
    out: dict[str, list[tuple[str, str]]] = {}
    for m in re.finditer(r'^\.decl\s+(\w+)\(([^)]*)\)', _RULES, re.M):
        cols = []
        for part in (p for p in m.group(2).split(",") if p.strip()):
            name, _, typ = part.partition(":")
            cols.append((name.strip(), typ.strip()))
        out[m.group(1)] = cols
    return out


def outputs() -> set[str]:
    """Relations the engine emits via `.output` — the surface the shim can read back (like a view set)."""
    return set(re.findall(r'^\.output\s+(\w+)', _RULES, re.M))


def _rule_heads() -> set[str]:
    heads = set()
    for line in _RULES.splitlines():
        s = line.strip()
        if s.startswith((".decl", ".output", ".input", "//")) or not s:
            continue
        m = re.match(r'(\w+)\(', s)            # a fact or a rule head (atom before ':-')
        if m:
            heads.add(m.group(1))
    return heads


def edb() -> set[str]:
    """Input (EDB) relations — declared but never a rule head: exactly the facts the shim must supply."""
    return set(relations()) - _rule_heads()


def idb() -> set[str]:
    """Derived (IDB) relations — the engine computes these from rules."""
    return set(relations()) & _rule_heads()


def supported_events() -> set[str]:
    """The trigger-event vocabulary the `fires` rules accept (the string constant in has_trigger(A,S,"..."))
    — the engine's event 'enum', recovered from the rule bodies since it isn't a relation/column."""
    return set(re.findall(r'has_trigger\(A, S, "(\w+)"\)', _RULES))


def column_types() -> set[str]:
    """The primitive types used across the schema (souffle has `symbol`/`number`/`unsigned`/`float`)."""
    return {t for cols in relations().values() for _, t in cols}


# ---- using the schema to VALIDATE the shim (turns the manual gap-audit into a query) -------------------

def validate_bridge() -> dict:
    """Cross-check the bridge's hand-written _EVENT table against the engine's actual event vocabulary:
      * `unknown`  — _EVENT values that are NOT real engine events (typos / drift — these would silently
                     never fire). This SHOULD always be empty.
      * `unmapped` — engine events with a `fires` rule but no bridge phrasing routing to them (the
                     'pure-bridge gap' surface, computed not hand-maintained).
    """
    from mtg import bridge_to_engine as bridge
    events = supported_events()
    bridge_targets = set(bridge._EVENT.values())
    return {
        "engine_events": sorted(events),
        "bridge_targets": sorted(bridge_targets),
        "unknown": sorted(bridge_targets - events),     # bridge points at a non-existent engine event
        "unmapped": sorted(events - bridge_targets),     # engine supports it, no card phrasing maps to it
    }


def validate_outputs_consumed() -> dict:
    """Which `.output` relations the driver/referee actually read (directly or transitively), from the
    schema. Both driver.py (the player) AND env.py (the referee built on it, which probes engine outputs to
    enumerate legal combat actions — e.g. illegal_block, may_attack, must_attack) are output consumers."""
    here = Path(__file__).parent                                    # packages/mtg/ (holds driver.py)
    import ast
    files = list(here.glob('*.py')) + list((here / 'engine').glob('*.py'))
    handlers = here.parent.parent / 'effect_handlers'
    if not handlers.exists():
        handlers = here.parent / 'effect_handlers'
    files += list(handlers.glob('*.py'))
    reads = {node.value for path in files if path.name != 'engine_schema.py'
             for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.Constant) and isinstance(node.value, str)}            # any quoted relation the driver/referee mentions
    outs = outputs()
    return {"outputs": sorted(outs), "unread": sorted(outs - reads)}


def main() -> None:
    rels, out, e, i = relations(), outputs(), edb(), idb()
    print(f"engine schema: {len(rels)} relations  |  {len(e)} EDB (inputs)  |  {len(i)} IDB (derived)  |  {len(out)} outputs")
    print(f"column types in use: {sorted(column_types())}")
    v = validate_bridge()
    print(f"\nbridge _EVENT -> engine events: {len(v['bridge_targets'])} targets, "
          f"{len(v['unknown'])} unknown (drift), {len(v['unmapped'])} engine events with no mapping")
    if v["unknown"]:
        print(f"  !! DRIFT — bridge targets not in the engine: {v['unknown']}")
    print(f"  engine events with no bridge phrasing (the gap, auto-computed): {v['unmapped']}")
    vo = validate_outputs_consumed()
    print(f"\noutputs not read by the driver: {vo['unread']}")


if __name__ == "__main__":
    main()
