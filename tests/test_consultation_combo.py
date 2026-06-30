"""test_consultation_combo.py — the "name a card" decision end-to-end: Demonic Consultation + Thassa's
Oracle, discovered by the win-lookahead.

The combo (cEDH's canonical 2-card win): cast Demonic Consultation naming a card NOT in your deck, so the
reveal-until-named exiles your ENTIRE library; then cast Thassa's Oracle, whose ETB wins because X (your
devotion, ≥ 0) ≥ the number of cards in your now-empty library (0). This exercises three pieces wired for
the "name a card" request:
  • effect_handlers/library.py  name_exile_lib — the choose-a-name + reveal-until self-mill, with a
    guaranteed-absent sentinel ("Standard Procedure") as the "a card not in my deck" option.
  • bridge_to_engine._fold_name_exile — folds Consultation's 5-clause sequence into ONE name_exile_lib.
  • env._cast_choices — enumerates the name sub-choices so a search explores naming the absent card.
  • effect_handlers/players.py  win_lib_empty — Thassa's Oracle's win, FAITHFULLY gated on an empty library
    (never the old unconditional win).

FAITHFUL-OR-ABSTAIN is checked too: Thassa's Oracle with a FULL library does NOT win (the search finds no
line), and naming a card that IS in the library does not empty it.

Run: python3 test_consultation_combo.py
"""

from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import driver
import bridge_to_engine as B
import env
import win_search
import effect_handlers

effect_handlers.load()

CHECKS: list[tuple[str, bool]] = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _combo_state(include_consult=True, hand_pool_colors=("blue", "black"), lib_filler=12):
    """alice holds Thassa's Oracle (+ Demonic Consultation if include_consult) with a colored mana pool and
    a `lib_filler`-card library; bob is a passive opponent. Built through make_deck_state so colored costs
    register and the cards resolve through the real interpreter."""
    deck_a = (["Demonic Consultation"] if include_consult else []) + ["Thassa's Oracle"] + ["Lightning Bolt"] * lib_filler
    st = B.make_deck_state({"alice": deck_a, "bob": ["Mountain"] * 20}, seed=1, hand=0, life=40)

    def ids_of(name):
        return [t for (t, n) in st["instance_of"] if n == name and ("alice", t) in st["in_library"]]

    combo = (["Demonic Consultation"] if include_consult else []) + ["Thassa's Oracle"]
    ids = {}
    for nm in combo:
        slug = {"Demonic Consultation": "demonic_consultation", "Thassa's Oracle": "thassa_s_oracle"}[nm]
        tid = ids_of(slug)[0]
        ids[nm] = tid
        st["in_library"].discard(("alice", tid)); st["in_hand"].add(("alice", tid))
        if tid in st["_lib_order"]["alice"]:
            st["_lib_order"]["alice"].remove(tid)
    st["mana_pool"] = {("alice", c, 5) for c in hand_pool_colors}
    st["active_player"] = {("alice",)}; st["current_step"] = {("precombat_main",)}; st["has_priority"] = {("alice",)}
    return st, ids


def _consult_then_oracle():
    st, ids = _combo_state()
    driver.clear_cache()
    path, nodes = win_search.find_win(st, me="alice", max_turns=1, node_budget=20000)
    check("combo: win-lookahead finds a line", path is not None)
    check("combo: the line casts Demonic Consultation first",
          path is not None and path[0][0] == "cast" and path[0][2] == ids["Demonic Consultation"])
    check("combo: it names the absent sentinel (a card not in the deck)",
          path is not None and path[0][3].get("name") == "standard_procedure")
    check("combo: the line then casts Thassa's Oracle",
          path is not None and any(a[0] == "cast" and a[2] == ids["Thassa's Oracle"] for a in path))
    check("combo: discovered cheaply (few nodes)", nodes < 200)


def _naming_present_card_does_not_win():
    # forcing the name to a card that IS in the library (Lightning Bolt) reveals until it — a few cards
    # exiled, library NOT emptied — so the subsequent Oracle does NOT win.
    st, ids = _combo_state()
    players = ["alice", "bob"]
    st["_forced"] = {"name": "lightning_bolt"}
    driver._cast_spell(st, "alice", ids["Demonic Consultation"], players)
    st["_forced"] = {}
    libn = sum(1 for (p, c) in st["in_library"] if p == "alice")
    check("naming a present card does NOT empty the library", libn > 0)
    driver._cast_spell(st, "alice", ids["Thassa's Oracle"], players)
    check("Oracle with a non-empty library does NOT win (faithful)",
          ("alice",) not in driver.run(st, ["wins_game"])["wins_game"])


def _oracle_alone_does_not_win():
    # Thassa's Oracle cast with a FULL library must NOT win — the old unconditional win would have; the
    # faithful win_lib_empty gate abstains. The lookahead finds no win.
    st, ids = _combo_state(include_consult=False)
    driver.clear_cache()
    path, _ = win_search.find_win(st, me="alice", max_turns=1, node_budget=8000)
    check("Oracle alone (full library) yields no win line (no false positive)", path is None)
    # and a direct cast confirms the SBA never fires
    driver._cast_spell(st, "alice", ids["Thassa's Oracle"], ["alice", "bob"])
    check("Oracle alone does not assert a win", ("alice",) not in driver.run(st, ["wins_game"])["wins_game"])


def run():
    _consult_then_oracle()
    _naming_present_card_does_not_win()
    _oracle_alone_does_not_win()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
