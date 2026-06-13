"""test_transform.py — §712 TRANSFORM: a double-faced card's BACK face now exists in witchcraft (added to
the oracle corpus from MTGJSON's two card objects), is interpreted into cards.dl (its type/loyalty/abilities),
and the front's 'flip a coin; if you win, you may exile ~ and return it transformed' resolves end-to-end:
the object flips to its back-face planeswalker (Ral, Leyline Prodigy) with §306.5b loyalty = base + one per
instant/sorcery cast this turn. Run: python3 test_transform.py
"""
from __future__ import annotations

import contextlib
import io

import card_corpus
import driver
import bridge_to_engine as B
import effect_handlers
import sim

effect_handlers.load()
_P = [0, 0]


def check(desc: str, ok: bool) -> None:
    _P[0] += 1
    _P[1] += 1 if ok else 0
    print(f"  {'ok  ' if ok else 'FAIL'} {desc}")


def _front_state(db, corpus, is_cast_this_turn=0):
    """Ral, Monsoon Mage (front creature) on the battlefield, with its back-face facts emitted by card_facts."""
    f, dr = B.card_facts("Ral, Monsoon Mage // Ral, Leyline Prodigy", "p", "ral", db, corpus)
    st = {}
    for k, rows in f.items():
        st.setdefault(k, set()).update(rows)
    st.setdefault("is_player", set()).update({("p",), ("q",)})
    st.setdefault("on_battlefield", set()).add(("ral",))
    st.setdefault("printed_control", set()).add(("p", "ral"))
    st.setdefault("life", set()).update({("p", 40), ("q", 40)})
    for rel in ("printed_type", "printed_power", "printed_toughness", "printed_subtype", "printed_color"):
        st[rel] = driver.run(st, [rel])[rel]                  # materialize the front identity, as real play does
    st["_is_cast_count"] = is_cast_this_turn
    st["_chance"] = lambda state, key, opts, w=None: "heads"  # force a WON flip
    return st, dr


def run() -> None:
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # (1) the back face EXISTS in the corpus + is interpreted into cards.dl as a planeswalker with loyalty.
    back = corpus.get("Ral, Leyline Prodigy")
    check("back face is in the oracle corpus", bool(back) and back.get("face") == "back")
    check("back face is a planeswalker with starting loyalty 2",
          bool(back) and "Planeswalker" in (back.get("types") or []) and str(back.get("loyalty")) == "2")
    bdb = db.get(B.ground.slug("Ral, Leyline Prodigy"), {})
    check("back face's loyalty abilities are interpreted in cards.dl",
          any(a.get("kind") == "loyalty" for a in (bdb.get("abilities") or {}).values()))

    # (2) the front emits the transform link + the back's identity, and is now CLEAN (no dropped clause).
    f, dr = B.card_facts("Ral, Monsoon Mage // Ral, Leyline Prodigy", "p", "ral", db, corpus)
    check("Ral (front) is CLEAN — the transform clause resolves", dr == [])
    check("front emits transform_target(tid, back_slug)", ("ral", "ral_leyline_prodigy") in f.get("transform_target", set()))
    check("front emits the back's starting loyalty", ("ral_leyline_prodigy", 2) in f.get("card_loyalty", set()))
    check("front emits the back's planeswalker type", ("ral_leyline_prodigy", "planeswalker") in f.get("card_type", set()))
    check("the coin-flip trigger carries the |transform rider",
          ("ral_a1", "coin_flip", 0, "lose:1|win:0|transform") in f.get("trigger_effect", set()))

    # (3) end-to-end on a WON flip: the creature transforms into the back-face planeswalker (engine re-derives
    # its type), with §306.5b loyalty = base 2 + one per instant/sorcery cast this turn (here 3 -> 5).
    st, _ = _front_state(db, corpus, is_cast_this_turn=3)
    check("starts as a creature", ("ral", "creature") in st["printed_type"])
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["coin_flip"](driver, st, "ral_a1", 0, "lose:1|win:0|transform", "ral", "p")
    check("transformed: instance flipped to the back slug", ("ral", "ral_leyline_prodigy") in st["instance_of"])
    check("transformed: now a planeswalker (materialized)", ("ral", "planeswalker") in st["printed_type"]
          and ("ral", "creature") not in st["printed_type"])
    check("transformed: ENGINE re-derives the planeswalker type from instance_of",
          ("ral", "planeswalker") in driver.run(st, ["printed_type"])["printed_type"])
    check("transformed: loyalty = base 2 + 3 instant/sorcery cast this turn = 5",
          ("ral", "loyalty", 5) in st.get("counter", set()))
    check("transformed: it is summoning sick (a new object)", ("ral",) in st.get("_sick", set()))

    # (4) no instant/sorcery cast this turn -> enters with just its base loyalty (2).
    st2, _ = _front_state(db, corpus, is_cast_this_turn=0)
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["coin_flip"](driver, st2, "ral_a1", 0, "lose:1|win:0|transform", "ral", "p")
    check("with 0 spells cast, loyalty = base 2", ("ral", "loyalty", 2) in st2.get("counter", set()))

    # (5) the 'may' is optional: declining (via the _choose seam) leaves the creature un-transformed.
    st3, _ = _front_state(db, corpus, is_cast_this_turn=3)
    st3["_policy"] = lambda state, key, options, default: False if key == "transform" else default  # decline
    with contextlib.redirect_stdout(io.StringIO()):
        effect_handlers.APPLY["coin_flip"](driver, st3, "ral_a1", 0, "lose:1|win:0|transform", "ral", "p")
    check("declining the optional 'may exile' leaves it a creature (no transform)",
          ("ral", "creature") in st3["printed_type"] and ("ral", "ral_leyline_prodigy") not in st3["instance_of"])

    print(f"\n{_P[1]}/{_P[0]} checks passed")
    if _P[1] != _P[0]:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
