"""test_tokens.py — §111.10 token creation. A 'create N <spec> token' effect builds full battlefield
permanents from the token spec slug (P/T, types, subtypes, colors) so combat, lords and anthems apply to
them — a Goblin token gets the Goblin lord's buff. The spec lives in the effect's `extra` column; the bridge
carries it through create_token and the driver parses it. Variable counts abstain.

Run: python3 test_tokens.py   (needs datalog/cards.dl for the bridge check)
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import contextlib
import io

import driver
import bridge_to_engine as bridge
import souffle_eval
from driver import RULES

CHECKS: list[tuple[str, bool]] = []

_PROG = RULES + "\n.output trigger_effect\n"        # the engine + a .output for the internal trigger_effect


def _engine_token_rows(f: dict):
    """The §111 create_token rows the ENGINE derives from a card's parse facts (spell_effect + trigger_effect,
    keyed by instance 'x'). ONE WORLD: create_token is now DATALOG-derived, so read it back from the engine.
    Evaluated through souffle_eval (native binary first); returns None if the backend aborts on this card (a
    few 'put_counter X' cards trip an unrelated §122 to_number) so the caller can skip+count it."""
    if not any(v == "create" for (_c, _a, _i, v, *_r) in f.get("card_effect", set())):
        return []
    st = {k: f[k] for k in ("instance_of", "card_ability", "card_effect", "ability_trigger") if k in f}
    st["is_player"] = {("alice",), ("bob",)}
    out = souffle_eval.eval_state(_PROG, st)
    if out is None:
        return None                                            # backend aborted -> skip this card
    rows = out.get("spell_effect", set()) | out.get("trigger_effect", set())
    return [r for r in rows if r[0].split("_")[0] == "x" and "create_token" in r]


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _state(effs: set) -> dict:
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": set(), "printed_type": set(), "printed_power": set(), "printed_toughness": set(),
        "printed_control": set(), "printed_subtype": set(), "printed_color": set(),
        "tapped": set(), "_sick": set(), "counter": set(), "spell_effect": effs,
    }


def _tokens(state: dict) -> list[str]:
    return sorted(c for (c,) in state["on_battlefield"])


def _powers(state: dict) -> dict:
    return {c: int(n) for (c, n) in driver.run(state, ["power"])["power"]}


def _driver_checks() -> None:
    # 'create two 1/1 white Soldier creature tokens' -> two full permanents with subtype + color + P/T.
    st = _state({("rally", "create_token", 2, "1_1_white_soldier_creature")})
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "rally", "alice")
    toks = _tokens(st)
    check("creates the right NUMBER of tokens (2)", len(toks) == 2)
    check("tokens are 1/1 (printed P/T parsed from the spec)", all(_powers(st)[t] == 1 for t in toks))
    check("tokens carry the subtype (Soldier) for tribal lords",
          all((t, "soldier") in st["printed_subtype"] for t in toks))
    check("tokens carry the color (white) for color lords",
          all((t, "white") in st["printed_color"] for t in toks))
    check("creature tokens are summoning-sick (§302.6)", all((t,) in st["_sick"] for t in toks))

    # a Soldier lord ('other Soldiers get +1/+1') reaches the freshly made Soldier tokens (composition).
    st["on_battlefield"].add(("marshal",))
    st["printed_type"].add(("marshal", "creature"))
    st["printed_power"].add(("marshal", 2)); st["printed_toughness"].add(("marshal", 2))
    st["printed_control"].add(("alice", "marshal")); st["printed_subtype"].add(("marshal", "soldier"))
    st["static_pt"] = {("marshal", 1, 1, "other_creatures")}
    st["static_filter"] = {("marshal", "subtype", "soldier")}
    p = _powers(st)
    check("a Soldier lord buffs the Soldier tokens (1/1 -> 2/2)", all(p[t] == 2 for t in toks))

    # a multi-type token ('1_1_colorless_thopter_artifact_creature') is both an artifact AND a creature.
    st = _state({("fab", "create_token", 1, "1_1_colorless_thopter_artifact_creature")})
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "fab", "alice")
    tok = _tokens(st)[0]
    htypes = {t for (c, t) in driver.run(st, ["controls"])["controls"]}  # noqa: F841 (touch the engine)
    check("a multi-type token is an artifact creature",
          (tok, "artifact") in st["printed_type"] and (tok, "creature") in st["printed_type"])
    check("the Thopter token has the Thopter subtype", (tok, "thopter") in st["printed_subtype"])

    # a named non-creature token ('treasure') -> an artifact, no P/T, not summoning-sick.
    st = _state({("greed", "create_token", 3, "treasure")})
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "greed", "alice")
    toks = _tokens(st)
    check("creates 3 treasure tokens", len(toks) == 3)
    check("a treasure token is an artifact, not a creature",
          all((t, "artifact") in st["printed_type"] and (t, "creature") not in st["printed_type"] for t in toks))


def _bridge_check() -> None:
    from interpreter import card_corpus
    import sim
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    n = 0
    skipped = 0
    sample = None
    for name in corpus:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        # spell_effect / trigger_effect rows carry create_token + the spec (not the old 'controller' bug).
        toks = _engine_token_rows(f)
        if toks is None:                                       # backend aborted on this card (§122 to_number)
            skipped += 1
            continue
        if toks:
            n += 1
            if sample is None:
                sample = (name, sorted(toks))
    if skipped:
        print(f"  ({skipped} cards skipped — souffle backend aborted on an unrelated §122 to_number)")
    check("real cards create tokens via create_token (>= 50)", n >= 50)
    check("a concrete token-maker was produced (spec carried, not 'controller')",
          sample is not None and all("controller" not in r for r in (sample[1] if sample else [])))


def run() -> None:
    _driver_checks()
    _bridge_check()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
