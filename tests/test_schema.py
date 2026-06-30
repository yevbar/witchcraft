"""test_schema.py — the engine-schema introspection layer (engine_schema.py) and the shim-vs-engine
consistency it enforces. This replaces hand audits with a query: the bridge can only ever target trigger
events the engine actually implements, and the shim's relation set is the engine's `.decl` schema.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)

import engine_schema


PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    PASS += bool(cond)
    FAIL += not cond


def main():
    rels = engine_schema.relations()
    check("relations() parses a non-trivial schema", len(rels) > 100)
    check("every relation has typed columns (symbol/number only)",
          engine_schema.column_types() <= {"symbol", "number"})
    check("EDB and IDB partition the schema", set(engine_schema.edb()) | set(engine_schema.idb()) == set(rels)
          and not (set(engine_schema.edb()) & set(engine_schema.idb())))
    check("outputs are a subset of the relations", engine_schema.outputs() <= set(rels))

    # the shim derives its relation set from the schema (no second ad-hoc parse to drift).
    import driver
    check("driver.DECLARED == engine_schema.relations()", driver.DECLARED == set(rels))

    # supported event vocabulary is recovered from the fires rules.
    events = engine_schema.supported_events()
    check("supported_events() recovers the engine's trigger vocabulary", "etb_self" in events
          and "your_sacrifice" in events and len(events) > 20)

    # THE KEY INVARIANT: the bridge never targets a trigger event the engine doesn't implement.
    v = engine_schema.validate_bridge()
    check("no bridge drift: every _EVENT value is a real engine event", v["unknown"] == [])
    # informational: engine events with a fires rule but no card phrasing mapped (the gap, auto-computed).
    print(f"     (engine events with no bridge mapping, for reference: {v['unmapped']})")
    check("the bridge maps a healthy slice of the engine's events", len(v["bridge_targets"]) >= 20)

    # outputs the driver never reads should be a tiny, known set (vestigial / transitively consumed).
    vo = engine_schema.validate_outputs_consumed()
    check("almost every engine output is referenced by the driver", len(vo["unread"]) <= 4)

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
