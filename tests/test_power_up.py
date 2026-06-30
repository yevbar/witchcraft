"""test_power_up.py — the Marvel Super Heroes "Power-up" keyword ability is engine-clean END-TO-END.

A Power-up ability is printed "Power-up — {cost}: <effect>." (§602 activated ability with a flavor-label
prefix). The STRUCTURAL layer strips the "Power-up —" label (transpile_card._strip_ability_word, the
generic §207.2c ability-word stripper) so the "{cost}: <effect>" parses through the existing _activated
handler, and the effect leaves (put_counter / create_token) route through the card_effects HYBRID — no
new interpretation regex. The bridge then translates the activated ability, and the driver resolves it.

This test runs the WHOLE chain per card (parse -> bridge -> driver activation) WITHOUT needing a cards.dl
regen: it hand-builds the per-card db `f` dict from the transpile facts (the same shape sim.load_db
produces) and feeds it to bridge.card_facts. Run: MTG_NO_SPACY=1 python3 test_power_up.py
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
import os
import re

os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter import card_corpus
from interpreter import transpile_card as tc
from interpreter import ground
from mtg import bridge_to_engine as bridge
from mtg import driver

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


# ---- hand-build the {slug: f} db dict from transpile facts (mirrors sim.load_db's parse) ------------
def _parse_fact(fact: str):
    m = re.match(r"^(\w+)\((.*)\)$", fact)
    rel = m.group(1)
    args = [a.strip().strip('"') for a in re.findall(r'"[^"]*"|[^,]+', m.group(2))]
    return rel, args


def _transpile_card(card: dict):
    """All transpile facts for `card`, plus the per-card f dict (sim.load_db shape)."""
    cid = ground.slug(card.get("name"))
    facts: list[str] = []
    for i, u in enumerate(card_corpus.units_of(card)):
        out = tc.transpile_unit(u, {"id": cid, "card": card, "seq": i})
        if out:
            facts += out.facts
    f: dict = {}
    abilities = f.setdefault("abilities", {})
    for fa in facts:
        rel, a = _parse_fact(fa)
        if rel == "printed_keyword":
            f.setdefault("keywords", set()).add(a[1])
        elif rel == "card_ability":
            abilities[a[1]] = {"kind": a[2], "effects": []}
        elif rel == "ability_cost":
            abilities[a[1]]["cost"] = a[2]
        elif rel == "ability_trigger":
            abilities[a[1]]["trigger"] = a[2]
        elif rel == "card_effect":
            abilities.setdefault(a[1], {"kind": "spell", "effects": []})["effects"].append(
                (int(a[2]), a[3], a[4], a[5], a[6], a[7]))
        elif rel == "card_restriction":
            f.setdefault("cant", set()).add((a[1], a[2]))
    return cid, f, facts


_CORPUS = {c.get("name"): c for c in card_corpus.load_cards()}


def _bridge_rows(name: str):
    """The activated_ability rows + dropped clauses the bridge produces for `name`, instanced as 'x'."""
    card = _CORPUS[name]
    cid, f, facts = _transpile_card(card)
    out, dropped = bridge.card_facts(name, "alice", "x", {cid: f}, _CORPUS)
    return out.get("activated_ability", set()), dropped, facts


# ---- (1) PARSE: every Power-up ability unit strips its label and parses as an activated ability ------
def _parse_checks() -> None:
    hits = [c for c in _CORPUS.values() if "Power-up" in (c.get("text") or "")]
    check("found the Power-up card set (>= 13 cards)", len(hits) >= 13)
    parsed = abstained = 0
    for c in hits:
        cid = ground.slug(c.get("name"))
        for i, u in enumerate(card_corpus.units_of(c)):
            if not u.raw.startswith("Power-up —"):
                continue
            out = tc.transpile_unit(u, {"id": cid, "card": c, "seq": i})
            if out and any('"activated"' in fact for fact in out.facts):
                parsed += 1
            else:
                abstained += 1
    # 36/37 parse (Thanos's "choose odd or even ... destroy each ..." modal rider abstains, faithfully).
    check("Power-up ability lines parse as activated (>= 35)", parsed >= 35)
    check("the label-strip never produces a half/non-activated parse other than the known modal rider",
          abstained <= 1)


# ---- (2) STRUCTURE: the 'Power-up —' label is what's stripped (not a coincidence of the cost text) ---
def _label_checks() -> None:
    raw = "Power-up — {3}{G}: Put two +1/+1 counters on ~."
    check("'_strip_ability_word' removes the 'Power-up —' label structurally",
          tc._strip_ability_word(raw) == "{3}{G}: Put two +1/+1 counters on ~.")


# ---- (3) BRIDGE: the activated counter-placement translates (no drop on the core) -------------------
def _bridge_checks() -> None:
    # Brave Brawler: 'Power-up — {4}{W}: Put two +1/+1 counters on ~.' -> a clean add_counter activated row.
    rows, dropped, facts = _bridge_rows("Brave Brawler")
    bb = next((r for r in rows if r[4] == "add_counter"), None)
    check("Brave Brawler Power-up -> one add_counter activated_ability row",
          bb is not None and bb[5] == 2 and bb[6] == "p1p1")
    check("Brave Brawler Power-up drops nothing", not dropped)
    check("Brave Brawler Power-up cost is {4}{W} = 5 generic-equivalent", bb is not None and bb[2] == 5)

    # Ultron Drone: counter + token in one ability -> two rows, nothing dropped.
    rows, dropped, _ = _bridge_rows("Ultron Drone")
    check("Ultron Drone Power-up -> add_counter row", any(r[4] == "add_counter" for r in rows))
    check("Ultron Drone Power-up -> create_token row", any(r[4] == "create_token" for r in rows))
    check("Ultron Drone Power-up drops nothing", not dropped)

    # Gamma Grotesque: counter LANDS; the dynamic 'draw for each creature with a counter' rider abstains
    # (a dynamic count the bridge doesn't model) -> the core ability is clean, the rider is a faithful drop.
    rows, dropped, _ = _bridge_rows("Gamma Grotesque")
    check("Gamma Grotesque Power-up -> add_counter row (3 +1/+1)",
          any(r[4] == "add_counter" and r[5] == 3 for r in rows))
    check("Gamma Grotesque dynamic draw rider abstains (faithful)",
          any(d[0] == "effect" and d[1] == "draw" for d in dropped))


# ---- (4) END-TO-END: the driver activates a Power-up ability and the counters actually land ----------
def _state_with(activated: set, mana: int = 7) -> dict:
    """alice controls 'hero' (a 2/2, untapped, not sick) + lands; she has `mana` available to activate."""
    return {
        "is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
        "life": {("alice", 20), ("bob", 20)}, "current_step": {("postcombat_main",)},
        "on_battlefield": {("hero",), ("l1",)},
        "printed_type": {("hero", "creature"), ("l1", "land")},
        "printed_power": {("hero", 2)}, "printed_toughness": {("hero", 2)},
        "printed_control": {("alice", "hero"), ("alice", "l1")},
        "mana_available": {("alice", mana), ("bob", 0)},
        "activated_ability": activated,
        "counter": set(), "tapped": set(), "_sick": set(),
        "on_stack": set(), "_stack_info": {}, "in_hand": set(), "graveyard": set(), "exile": set(),
    }


def _powers(state: dict) -> dict:
    return {c: int(n) for (c, n) in driver.run(state, ["power"])["power"]}


def _e2e_checks() -> None:
    # Drive the REAL bridge output: take Serpent Specialist's translated activated row, rename its source
    # to 'hero', and let the driver activate it. The +1/+1 counters must land and power must climb 2 -> 4.
    rows, _, _ = _bridge_rows("Serpent Specialist")      # 'Power-up — {3}{G}: Put two +1/+1 counters on ~.'
    src_row = next(r for r in rows if r[4] == "add_counter")
    a, _src, cost, taps, eff, amt, tgt = src_row
    act = {("hero_pu", "hero", cost, taps, eff, amt, tgt)}
    st = _state_with(act, mana=cost)
    with contextlib.redirect_stdout(io.StringIO()):
        driver._activate_phase(st, "alice", ["alice", "bob"])
    # the ability sits on the stack with its effect slot set; resolve it through the shared effect path
    # (the same _ability_effect -> _apply_effects step the game's stack resolution uses).
    slot = st.get("_ability_effect", {}).get("hero_pu")
    if slot is not None:
        e, n, t, s, ap = slot
        with contextlib.redirect_stdout(io.StringIO()):
            driver._apply_effects(st, {(s + "_pu", e, n, t, s, ap)})
    has_counter = any(o == "hero" and k == "p1p1" and c >= 2 for (o, k, c) in st.get("counter", set()))
    check("driver activates Power-up: two +1/+1 counters land on the source", has_counter)
    check("driver activates Power-up: source power grows 2 -> 4", _powers(st).get("hero") == 4)
    check("driver paid the Power-up mana cost (alice spent it all)",
          all(m == 0 for (q, m) in st["mana_available"] if q == "alice"))


def main() -> None:
    _parse_checks()
    _label_checks()
    _bridge_checks()
    _e2e_checks()
    width = max(len(n) for n, _ in CHECKS)
    ok = 0
    for name, passed in CHECKS:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:<{width}}")
        ok += passed
    print(f"\n{ok}/{len(CHECKS)} checks passed")
    if ok != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
