"""deck_evaluator.py — classify a decklist by HOW IT WINS, derived from the engine's KNOWN rules.

Two questions, both answered from mechanics witchcraft already interprets (NOT oracle-text guessing):

  1. Which cards WIN vs which cards HELP WIN?
       - a card WINS if its interpreted effect can DIRECTLY drive a §104 loss/win condition to its
         threshold (deal a player to 0 life, 10 poison, deck them out, or an explicit "you win").
       - a card HELPS if it feeds a win axis sub-lethally, or is an engine piece (ramp / draw / selection /
         tutor / interaction / tokens / anthem / a copy-or-extra-turn multiplier).
  2. What is the deck's WIN MECHANIC — the §104 axis its cards most advance? For a fuzzy deck this stays
     true at the abstract level ("deal damage"), split into CREATURE-COMBAT vs NON-CREATURE damage.

WHY DERIVED, NOT HARD-CODED: the win axes ARE the engine's §104 rules —
    loss_threshold("life_zero", 0) / ("poison_ten", 10) / ("commander_damage", 21),
    loses_game :- would_draw_from_empty (deck-out), wins_game/loses_game :- eff_win/lose_game.
Each card's contribution is read from the SAME parsed card_effect clauses the engine translates, mapped
to an axis by the engine's known verb/target vocabulary (life loss -> life_zero, poison counters ->
poison_ten, mill -> deck-out, win_game -> alt_win, infect/combat -> the relevant axis). So as the pipeline
interprets MORE effects, more contributions light up automatically — and clauses the engine can't yet read
(a card's dropped clauses) are reported as the DISCOVERABLE FRONTIER: where interpreting more would reveal
more of the deck's plan.

    python3 deck_evaluator.py "Izzet Prowess (STD)"     # a named meta deck
    python3 deck_evaluator.py --cedh "Ral Turbo Storm"  # a named cEDH deck
    import deck_evaluator as D; D.evaluate(["Opt","Lightning Bolt",...])   # any card list
"""

from __future__ import annotations

import sys

import sim
import card_corpus
import ground

# ── the §104 win axes (each IS an engine loss/win rule — see module docstring) ───────────────────────
AXES = {
    "life_zero":        "reduce a player to 0 life (§104.2a)",
    "poison_ten":       "give a player 10 poison counters (§104.2c)",
    "commander_damage": "21 combat damage from one commander (§903.10a) — Commander games only; the engine "
                        "now accumulates per-commander combat damage and adjudicates the loss",
    "mill_out":         "make a player draw from an empty library (§104.3a / §104.2c deck-out)",
    "alt_win":          "an effect that says you win / a player loses (§104.2)",
}

# player-facing target slugs (an effect that HARMS an opponent advances a win axis). 'you'/'controller'/
# 'yourself' are self-facing and never count toward winning. 'any_target'/'target_player' can be aimed at an
# opponent, so they count (the player chooses the opponent).
_OPP_TGT = {
    "target_player", "target_opponent", "each_opponent", "that_player", "that_opponent",
    "defending_player", "each_player", "any_target", "target_player_or_planeswalker",
    "target_opponent_or_planeswalker", "each_of_your_opponents",
}
_SELF_TGT = {"you", "yourself", "controller", "its_controller", "-", "none"}
# words that mark a target as a PERMANENT (not a player) — damage/effects aimed here are removal, not a life
# hit, so they never advance the life_zero axis.
_PERM_WORDS = {"creature", "permanent", "artifact", "enchantment", "planeswalker", "battle", "land"}

# verbs that, aimed at an opponent, push a §104 axis — with the axis they push (the engine's known vocab).
_AXIS_VERB = {
    "deal_damage": "life_zero", "lose_life": "life_zero", "drain": "life_zero",
    "mill": "mill_out", "win_game": "alt_win",
}
# combat-relevant evasion (a creature that connects) and damage multipliers — keyword vocabulary the engine
# models (printed_keyword / has_keyword).
_EVASION = {"flying", "menace", "trample", "fear", "shadow", "horsemanship", "intimidate", "skulk", "daunt"}
_MULT_KW = {"storm", "cascade", "double_strike"}
# verbs that MULTIPLY/extend a turn's output (help any axis go lethal): copy a spell, extra turn/combat, doublers.
_MULT_VERB = {"copy", "extra_turn", "extra_combat", "double"}

_SCALABLE = ("x", "for_each", "equal_to", "number_of", "that_", "the_amount", "twice")  # variable / scaling amount


def _hits_opp(tgt: str) -> bool:
    """True iff the target is a PLAYER an opponent controls — i.e. an effect here reduces a life total /
    mills / poisons a player (advancing a §104 axis). A permanent target (…_creature_…, …_artifact_…) is
    removal, never a life hit."""
    t = str(tgt)
    if any(w in t for w in _PERM_WORDS):
        return False
    return (t in _OPP_TGT) or ("opponent" in t) or ("player" in t)


def _amt(a) -> int | None:
    s = str(a)
    return int(s) if s.lstrip("-").isdigit() else None


def _scalable(a, extra="") -> bool:
    s = (str(a) + " " + str(extra)).lower()
    return any(k in s for k in _SCALABLE)


_DB = None
_CORPUS = None


def _load():
    global _DB, _CORPUS
    if _DB is None:
        _DB = sim.load_db()
        _CORPUS = {c["name"]: c for c in card_corpus.load_cards()}
    return _DB, _CORPUS


def _clauses(slug: str):
    """Every parsed (kind, verb, amt, tgt, extra, cond) clause across a card's abilities + which it has."""
    db, _ = _load()
    f = db.get(slug, {})
    out = []
    for aid, ab in f.get("abilities", {}).items():
        for (_seq, verb, amt, tgt, extra, cond) in ab.get("effects", []):
            out.append((ab.get("kind"), verb, amt, tgt, extra, cond))
    return out, f


def card_profile(name: str, commander: bool = False) -> dict:
    """Classify ONE card from its interpreted mechanics: role (wins/helps/manabase/filler), the §104 axes it
    advances (with a rough magnitude), helper tags, whether it's a finisher, and the clauses the engine can't
    yet read (the discoverable frontier). `commander` gates the §903.10a commander-damage axis — outside a
    Commander game a legendary creature is NOT a commander and deals no commander damage."""
    db, corpus = _load()
    c = corpus.get(name, {})
    slug = ground.slug(name)
    clauses, f = _clauses(slug)
    types = {t.lower() for t in c.get("types") or []}
    kws = {str(k).lower().replace(" ", "_") for k in c.get("keywords") or []}
    kws |= {str(k).lower().replace(" ", "_") for k in f.get("keywords") or set()}   # db printed_keyword set
    power = _amt(c.get("power"))
    legendary = "legendary" in {s.lower() for s in c.get("supertypes") or []}

    axes: dict[str, float] = {}
    helps: dict[str, str] = {}
    reasons: list[str] = []
    finisher = False
    dmg = {"combat": 0.0, "spell": 0.0}                         # the life_zero split, tracked at the source

    def add_axis(ax, mag, why, src=None):
        axes[ax] = axes.get(ax, 0) + mag
        reasons.append(why)
        if ax == "life_zero" and src:
            dmg[src] += mag

    # ── §104 axis contributions from the parsed effect clauses (engine's known verb→axis vocab) ──
    for (kind, verb, amt, tgt, extra, cond) in clauses:
        # explicit "you win / a player loses" — the strongest signal (§104.2).
        if verb == "win_game" or (verb in ("trigger_effect",) and "win" in str(amt)):
            add_axis("alt_win", 100, "explicit win-the-game effect")
            finisher = True
            continue
        # poison counters on a player (§104.2c).
        if verb == "put_counter" and str(extra) == "poison" and (_hits_opp(tgt) or "player" in str(tgt)):
            n = _amt(amt) or 1
            add_axis("poison_ten", n * 2 + (8 if _scalable(amt, extra) else 0), f"gives {amt} poison to {tgt}")
            continue
        # life loss / damage / mill aimed at an opponent.
        ax = _AXIS_VERB.get(verb)
        if ax and _hits_opp(tgt):
            n = _amt(amt)
            scal = _scalable(amt, extra) or any(k in kws for k in _MULT_KW)
            mag = (12 if scal else (n if n is not None else 3))
            add_axis(ax, mag, f"{verb} {amt} to {tgt}" + (" (scalable)" if scal else ""), src="spell")
            if scal or (n is not None and n >= 5) or ax == "alt_win":
                finisher = True
            continue

    # ── combat as a win axis: a creature that can connect feeds life_zero (or poison via infect) ──
    if "creature" in types and power is not None and "defender" not in kws:
        infectish = bool(kws & {"infect", "toxic", "poisonous"})
        evasive = bool(kws & _EVASION)
        if infectish:
            add_axis("poison_ten", power * 2 + (6 if evasive else 0), f"infect/toxic {power}-power creature")
            if power >= 3 or evasive:
                finisher = True
        else:
            mag = power + (3 if evasive else 0) + (3 if kws & {"double_strike"} else 0)
            add_axis("life_zero", mag, f"{power}-power creature" + (" (evasive)" if evasive else ""), src="combat")
            if power >= 5 and evasive:
                finisher = True
            # §903.10a commander damage applies ONLY in a Commander game (and only to a card that is the
            # commander). Outside that format it's a no-op — a legendary creature is just a creature.
            if commander and legendary and power >= 4:
                add_axis("commander_damage", power + (4 if evasive else 0), "big legendary (commander-damage)")

    # grants infect/toxic to YOUR team (converts combat to the poison axis: Triumph of the Hordes).
    for (kind, verb, amt, tgt, extra, cond) in clauses:
        if verb == "grant_keyword" and str(extra) in ("infect", "toxic") and "you_control" in str(tgt):
            add_axis("poison_ten", 14, "grants infect/toxic to your creatures")
            finisher = True

    # ── helper tags (engine-known enabler vocabulary) ──
    verbs = {v for (_k, v, *_r) in clauses}
    if f.get("mana") or "add_mana" in verbs:
        helps["ramp"] = "produces mana"
    if "draw" in verbs:
        helps["draw"] = "draws cards"
    if verbs & {"scry", "surveil", "look"}:
        helps["selection"] = "filters draws"
    if "search" in verbs:
        helps["tutor"] = "searches library"
    if "counter" in verbs:
        helps["interaction"] = "counters spells"
    elif verbs & {"destroy", "exile", "return_to_hand", "tap"} and any(
            _hits_opp(t) for (_k, v, _a, t, _e, _c) in clauses if v in {"destroy", "exile", "return_to_hand", "tap"}):
        helps["interaction"] = "removal"
    if "create" in verbs:
        helps["tokens"] = "makes tokens (board for combat)"
    if any(k == "static" and v in ("modify_pt", "grant_keyword") and "you_control" in str(t)
           for (k, v, _a, t, _e, _c) in clauses):
        helps["anthem"] = "anthem/lord (pumps the team)"
    if verbs & _MULT_VERB or kws & _MULT_KW:
        helps["multiplier"] = "copies/extends (storm, copy, extra turn/combat)"

    # ── role ──
    is_land = "land" in types
    win_axis_score = sum(axes.get(a, 0) for a in AXES)
    if finisher:
        role = "WINS"
    elif win_axis_score > 0 and not helps:
        role = "WINS" if win_axis_score >= 10 else "HELPS"
    elif helps or win_axis_score > 0:
        role = "HELPS"
    elif is_land:
        role = "MANABASE"
    else:
        role = "FILLER"

    # discoverable frontier: win-relevant clauses the engine couldn't read (would change this card's role).
    import bridge_to_engine
    try:
        _, dropped = bridge_to_engine.card_facts(name, "p", "t", db, corpus)
    except Exception:
        dropped = []
    drop_verbs = sorted({d for _, d in dropped})

    # a genuine parse gap: nothing the engine reads — no clauses, no mana ability, and nothing the keyword/
    # combat path picked up either (so NOT Glistener Elf, whose infect IS interpreted via its keyword).
    no_effect = not clauses and not is_land and not f.get("mana") and not axes and not helps
    return {"name": name, "role": role, "axes": axes, "helps": helps, "finisher": finisher,
            "reasons": reasons, "dropped": drop_verbs, "is_land": is_land, "legendary": legendary,
            "in_corpus": name in corpus, "dmg": dmg, "no_effect": no_effect}


def evaluate(names, label: str = "deck", quiet: bool = False, commander: bool = False) -> dict:
    """Classify a whole decklist (count-agnostic) and report its win topology + mechanic. `commander` enables
    the §903.10a commander-damage axis (only meaningful in a Commander game)."""
    import builtins
    _print = (lambda *a, **k: None) if quiet else builtins.print
    globals()["print"] = _print
    try:
        return _evaluate(names, label, commander)
    finally:
        globals().pop("print", None)


def _evaluate(names, label: str, commander: bool = False) -> dict:
    profs = [card_profile(n, commander) for n in dict.fromkeys(names)]   # distinct, order-preserving
    deck_axis: dict[str, float] = {}
    combat = spell = 0.0
    for p in profs:
        for a, m in p["axes"].items():
            deck_axis[a] = deck_axis.get(a, 0) + m
        combat += p["dmg"]["combat"]                            # life_zero split, tracked per source clause
        spell += p["dmg"]["spell"]
    mechanic = max(deck_axis, key=deck_axis.get) if deck_axis else "none-detected"

    winners = sorted([p for p in profs if p["role"] == "WINS"],
                     key=lambda p: -sum(p["axes"].values()))
    helpers = sorted([p for p in profs if p["role"] == "HELPS"],
                     key=lambda p: -sum(p["axes"].values()))
    frontier = [p for p in profs if p["role"] in ("FILLER", "MANABASE", "HELPS") and p["dropped"]
                and not p["in_corpus"] is False]

    print(f"\n{'=' * 70}\nWIN-TOPOLOGY: {label}\n{'=' * 70}")
    print(f"distinct cards: {len(profs)}")
    print(f"\nPRIMARY WIN MECHANIC: {mechanic}  — {AXES.get(mechanic, '?')}")
    rank = sorted(deck_axis.items(), key=lambda kv: -kv[1])
    print("axis pressure (summed contribution): " + ", ".join(f"{a}={m:.0f}" for a, m in rank if m))
    if deck_axis.get("life_zero"):
        tot = combat + spell or 1
        print(f"  life_zero split -> creature-combat {100 * combat / tot:.0f}% / non-creature damage {100 * spell / tot:.0f}%")

    print(f"\nCARDS THAT WIN ({len(winners)}):")
    for p in winners:
        ax = ",".join(f"{a}:{m:.0f}" for a, m in sorted(p["axes"].items(), key=lambda kv: -kv[1]))
        print(f"  {p['name']:32} [{ax}]  {p['reasons'][0] if p['reasons'] else ''}")

    print(f"\nCARDS THAT HELP WIN ({len(helpers)}):")
    for p in helpers:
        tags = ",".join(p["helps"]) or ",".join(f"{a}" for a in p["axes"])
        print(f"  {p['name']:32} {tags}")

    other = [p for p in profs if p["role"] in ("MANABASE", "FILLER")]
    print(f"\nMANABASE/OTHER ({len(other)}): " + ", ".join(p["name"] for p in other[:18])
          + (" …" if len(other) > 18 else ""))

    disc = [p for p in profs if (p["dropped"] or p["no_effect"]) and p["role"] in ("FILLER", "HELPS")]
    if disc:
        print(f"\nDISCOVERABLE FRONTIER ({len(disc)} cards have effects the engine can't yet read — "
              "interpreting these may reveal more of the plan):")
        for p in disc[:12]:
            why = ("no interpreted effect (parse gap)" if p["no_effect"]
                   else "dropped: " + ",".join(p["dropped"]))
            print(f"  {p['name']:32} {why}")

    return {"mechanic": mechanic, "axes": deck_axis, "winners": winners, "helpers": helpers}


def _named_deck(name: str, cedh: bool):
    """Return (card names, is_commander). cEDH decks ARE Commander (§903); a constructed deck carries its
    format, so commander damage only applies when that format is Commander."""
    if cedh:
        import cedh_decklists as M
        decks = getattr(M, "DECKS", None) or {}
    else:
        import meta_decklists_constructed as M
        decks = M.DECKS
    d = decks.get(name)
    if d is None:
        raise SystemExit(f"deck not found: {name}\navailable: {', '.join(list(decks)[:12])} …")
    cards = d["cards"]
    names = list(cards.keys()) if isinstance(cards, dict) else list(cards)
    commander = cedh or str(d.get("format", "")).lower() == "commander"
    return names, commander


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--cedh"]
    cedh = "--cedh" in sys.argv
    deck = args[0] if args else "Izzet Prowess (STD)"
    names, commander = _named_deck(deck, cedh)
    evaluate(names, label=deck, commander=commander)
