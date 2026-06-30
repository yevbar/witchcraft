"""test_convert.py — §701.28 CONVERT reduces to §712 TRANSFORM (effect_handlers/convert.py).

'Convert ~' / 'convert it' flips a transforming double-faced permanent to its other face. We prove the
convert applier flips the source's face through the SAME driver._transform path transform uses, on a real
double-faced fixture (Ral, Monsoon Mage // Ral, Leyline Prodigy from the corpus):
  1. encode: self/'it'/a bare self-name token -> ('convert', 0, 'self'); target/another/other -> abstain;
  2. apply: the source's instance_of (printed identity) flips to the BACK slug; the engine re-derives the
     back-face type — perfect AND imperfect information (a flipped face is public board state);
  3. a non-DFC convert is a faithful no-op (no transform_target).

Run: python3 test_convert.py
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

from interpreter import card_corpus
from mtg import driver
from mtg import bridge_to_engine as B
import effect_handlers
import observe
from mtg import sim

effect_handlers.load()
_P = [0, 0]


def check(desc: str, ok: bool) -> None:
    _P[0] += 1
    _P[1] += 1 if ok else 0
    print(f"  {'ok  ' if ok else 'FAIL'} {desc}")


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _front_state(db, corpus):
    """Ral, Monsoon Mage (front creature) on the battlefield with its back-face facts (a transform_target)."""
    f, _dr = B.card_facts("Ral, Monsoon Mage // Ral, Leyline Prodigy", "p", "ral", db, corpus)
    st = {}
    for k, rows in f.items():
        st.setdefault(k, set()).update(rows)
    st.setdefault("is_player", set()).update({("p",), ("q",)})
    st.setdefault("on_battlefield", set()).add(("ral",))
    st.setdefault("printed_control", set()).add(("p", "ral"))
    st.setdefault("life", set()).update({("p", 40), ("q", 40)})
    for rel in ("printed_type", "printed_power", "printed_toughness", "printed_subtype", "printed_color"):
        st[rel] = driver.run(st, [rel])[rel]                  # materialize the front identity, as real play does
    st["_is_cast_count"] = 0
    return st


def run() -> None:
    # (0) the handler is registered (auto-wired via effect_handlers.load()).
    check("convert encoder registered", "convert" in effect_handlers.ENCODE)
    check("convert applier registered", "convert" in effect_handlers.APPLY)

    enc = effect_handlers.ENCODE["convert"]
    # (1) encode: self / it / a bare self-name token -> ('convert', 0, 'self'); a real target -> abstain.
    check("encode 'it' -> ('convert',0,'self')", enc("convert", "-", "it", "-") == ("convert", 0, "self"))
    check("encode 'self' -> self", enc("convert", "-", "self", "-") == ("convert", 0, "self"))
    check("encode bare self-name 'ratchet' -> self (the §701.28 source flips)",
          enc("convert", "-", "ratchet", "-") == ("convert", 0, "self"))
    check("encode 'starscream' (a Transformers source name) -> self",
          enc("convert", "-", "starscream", "-") == ("convert", 0, "self"))
    check("ABSTAIN on 'target_creature'", enc("convert", "-", "target_creature", "-") is None)
    check("ABSTAIN on 'another_creature'", enc("convert", "-", "another_creature", "-") is None)
    check("ABSTAIN on 'each_creature'", enc("convert", "-", "each_creature", "-") is None)

    # (2) apply on a real DFC: the source flips to its BACK face (same path as transform).
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    st = _front_state(db, corpus)
    check("fixture: starts as a creature (front face)", ("ral", "creature") in st["printed_type"])
    check("fixture: has a transform_target (a back face to convert to)",
          any(o == "ral" for (o, _b) in st.get("transform_target", set())))
    _quiet(effect_handlers.APPLY["convert"], driver, st, "ral_a1", 0, "self", "ral", "p")
    check("converted: instance_of flipped to the BACK slug",
          ("ral", "ral_leyline_prodigy") in st["instance_of"])
    check("converted: front-face identity gone (no longer instance_of the front slug)",
          ("ral", "ral_monsoon_mage") not in st["instance_of"])
    check("converted: ENGINE re-derives the back-face type (planeswalker) from the flipped instance_of",
          ("ral", "planeswalker") in driver.run(st, ["printed_type"])["printed_type"])
    check("converted: it is a NEW object (summoning sick) — same §712 transform semantics",
          ("ral",) in st.get("_sick", set()))

    # (3) the flipped face is PUBLIC board state — observe(opponent) sees the converted identity.
    st["in_hand"] = {("p", "secret")}
    obs = observe.observe(st, "q")                            # the opponent's view
    check("imperfect info: observe(q) hides p's hand", not any(pl == "p" for (pl, _c) in obs.get("in_hand", set())))
    check("imperfect info: observe(q) shows the converted (back-face) identity (public board)",
          ("ral", "ral_leyline_prodigy") in obs.get("instance_of", set()))

    # (4) a non-DFC convert is a faithful no-op (no transform_target).
    nd = {"on_battlefield": {("plain",)}, "printed_control": {("p", "plain")},
          "printed_type": {("plain", "creature")}, "is_player": {("p",), ("q",)}}
    _quiet(effect_handlers.APPLY["convert"], driver, nd, "x", 0, "self", "plain", "p")
    check("non-DFC convert is a no-op (instance_of unchanged / absent)",
          not nd.get("instance_of"))

    print(f"\n{_P[1]}/{_P[0]} checks passed")
    if _P[1] != _P[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
