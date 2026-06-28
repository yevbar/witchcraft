"""inthearena.mtga.engine — position an `mtg.Game` at the current MTGA board by DETERMINIZATION.

The opponent's hand and both libraries are hidden information. The standard move for imperfect-information
games is to determinize: feed the KNOWN info and sample a plausible filling for the unknown. So:

  * KNOWN, fed directly  — our hand, BOTH battlefields, life totals, whose turn/phase it is. Card
    characteristics come from the GRE objects themselves (types / P-T / colors), so this works even for cards
    the engine's corpus doesn't cover yet.
  * HIDDEN, randomly held — the opponent's hand (we know the count, not the cards) and the libraries are filled
    with a seeded random configuration drawn from an imagined pool (`opponent_deck=`, else a generic default).

`to_game(view, me)` returns a real `mtg.Game` (via `Game.from_state`) sitting at that board, which you can
query (life, battlefield, …). This is the one module that bridges into the engine.

SCOPE: reconstructs the BOARD (zones / types / P-T / life / turn). It does NOT load card ABILITY rules — full
rules-awareness depends on the engine's card coverage, tracked separately. Re-call it as the log advances to
re-position the Game (the GameView is updated by the diff reader; this projects a fresh determinized state).
"""

from __future__ import annotations

import functools
import random
from typing import Optional

from . import cards
from .gre import GameObject, GameView

# (phase, step) -> engine current_step; main phases fall back by phase alone.
_STEP = {
    ("Phase_Beginning", "Step_Untap"): "untap",
    ("Phase_Beginning", "Step_Upkeep"): "upkeep",
    ("Phase_Beginning", "Step_Draw"): "draw",
    ("Phase_Main1", "Step_Main"): "precombat_main",
    ("Phase_Combat", "Step_BeginCombat"): "begin_combat",
    ("Phase_Combat", "Step_DeclareAttack"): "declare_attackers",
    ("Phase_Combat", "Step_DeclareAttackers"): "declare_attackers",
    ("Phase_Combat", "Step_DeclareBlock"): "declare_blockers",
    ("Phase_Combat", "Step_DeclareBlockers"): "declare_blockers",
    ("Phase_Combat", "Step_CombatDamage"): "combat_damage",
    ("Phase_Combat", "Step_EndCombat"): "end_combat",
    ("Phase_Main2", "Step_Main"): "postcombat_main",
    ("Phase_Ending", "Step_End"): "end_step",
    ("Phase_Ending", "Step_Cleanup"): "cleanup",
}
_PHASE_FALLBACK = {"Phase_Main1": "precombat_main", "Phase_Main2": "postcombat_main"}

# The engine seat names the bridge assigns: OUR seat (`me`) and the opponent. Defined ONCE here and carried on
# the built state as `_me` (read by engine_policy to bind its Player), so the name isn't a magic string that has
# to be kept in sync across modules. Heads-up only — a third name would be needed to generalise to multiplayer.
ME_SEAT, OPP_SEAT = "alice", "bob"

# A generic pool to fill imagined hidden cards (valid engine identities) when no opponent_deck is given.
_DEFAULT_POOL = ["grizzly_bears", "hill_giant", "plains", "forest", "island", "mountain", "swamp"]

_RELATIONS = ("is_player", "life", "active_player", "current_step", "in_hand", "in_library",
              "printed_control", "on_battlefield", "instance_of", "printed_type", "printed_subtype",
              "has_supertype", "printed_color", "printed_power", "printed_toughness", "tapped",
              "command_zone", "is_commander", "attacks", "spell_type", "free_grant", "mana_cost")


def _eng(token: str) -> str:
    """'CardType_Creature' -> 'creature', 'SubType_Plains' -> 'plains' (MTGA enum -> engine vocabulary)."""
    return token.split("_", 1)[1].lower() if "_" in token else token.lower()


def _engine_step(turn) -> str:
    return _STEP.get((turn.phase, turn.step)) or _PHASE_FALLBACK.get(turn.phase, "precombat_main")


def _slug(name: str) -> str:
    import ground
    return ground.slug(name)


@functools.lru_cache(maxsize=1)
def _card_rules_db():
    """(db, corpus) for loading a card's RULE facts (card_effect / card_ability / triggers / …) by oracle name —
    parsed once and cached. None if the engine's card tooling isn't importable, in which case the synced state
    stays board-only (rule-less) and rules-aware scorers degrade gracefully."""
    try:
        import importlib
        import card_corpus
        sim = importlib.import_module("sim")
        return sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}
    except Exception:
        return None


def _merge_card_rules(s: dict, known: list) -> None:
    """Merge each KNOWN card's RULE facts into the synced state `s`, so the engine reasons about what cards DO
    (burn removal, ETB triggers, combat keywords, …) move-by-move — not just their board stats. `known` is a list
    of (oracle_name, seat, instanceId) for the VISIBLE cards (our hand + both battlefields). Determinized hidden
    cards stay rule-less — we don't know them. We feed card_facts' SLUG-keyed card rules but SKIP printed_control:
    it's instance/zone-sensitive and build_state already owns it (battlefield only), so a hand card mustn't get a
    controller. instance_of matches build_state's id, so it dedupes. Uncovered cards contribute no card_effect, so
    rules-aware scorers stay correctly inert for them."""
    loaded = _card_rules_db()
    if loaded is None:
        return
    db, corpus = loaded
    import bridge_to_engine as bridge
    for name, seat, iid in known:
        try:
            facts, _dropped = bridge.card_facts(name, seat, f"{_slug(name)}_{iid}", db, corpus)
        except Exception:
            continue
        for rel, rows in facts.items():
            if rel == "printed_control":                   # zone-sensitive; build_state owns it (battlefield only)
                continue
            s.setdefault(rel, set()).update(rows)


def build_state(view: GameView, me: int, *, opponent_deck: Optional[list] = None, seed: int = 0,
                castable: Optional[set] = None, playable: Optional[set] = None,
                costs: Optional[dict] = None, load_rules: bool = True) -> dict:
    """Build an mtg engine STATE dict from `view`, as seen by seat `me`: visible objects fed directly, hidden
    zones determinized (seeded). `opponent_deck` is a list of imagined card names/slugs for the fill.

    `castable` is the set of MTGA instanceIds in our hand that MTGA reports we can PAY for right now — they're
    fed as `free_grant` so the engine DERIVES `free_cast` -> `can_afford` and surfaces those casts. (We feed
    `free_grant`, an EDB input, NOT `free_cast` directly: `free_cast` is a derived relation, and the incremental
    souffle driver's cross-call state makes a directly-fed derived value survive unreliably — the 'passed with a
    castable spell in hand' bug.) The engine has no mana model for a determinized snapshot (mana is developed on
    phase ENTRY, which a static state skips) and no cost facts for cards outside its corpus, so AFFORDABILITY is
    delegated to MTGA (the oracle); the engine still decides WHICH affordable spell to cast. Without it the engine
    sees nothing castable and just passes.

    `playable` GATES land drops to MTGA's offered Play actions: a hand land NOT in `playable` is kept in hand but
    not surfaced as a §305 land play. The live GameView can lag a beat (a just-PLAYED land still shows in hand),
    and the engine's Do.LANDS leads, so without this gate it re-picks the stale land every actions decision, the
    move doesn't map, and the bot passes the whole turn instead of casting. `playable=None` = don't gate (offer
    all hand lands — the default for tests / `suggest`); pass MTGA's Play instanceIds to gate to reality."""
    rng = random.Random(seed)
    castable = castable or set()
    s = {k: set() for k in _RELATIONS}
    seats = view.seats() or [me]
    name_of = {sid: (ME_SEAT if sid == me else OPP_SEAT) for sid in seats}
    opp = next((x for x in seats if x != me), None)
    if opp is not None and opp not in name_of:
        name_of[opp] = OPP_SEAT
    # who an attacker controlled by each player is attacking (the OTHER player) — for the `attacks` combat fact
    defender_of = {nm: next((o for o in name_of.values() if o != nm), None) for nm in name_of.values()}

    for sid in seats:
        s["is_player"].add((name_of[sid],))
        if view.life.get(sid) is not None:
            s["life"].add((name_of[sid], view.life[sid]))
    if view.turn.activePlayer is not None and view.turn.activePlayer in name_of:
        s["active_player"].add((name_of[view.turn.activePlayer],))
    s["current_step"].add((_engine_step(view.turn),))

    pool = [_slug(n) for n in (opponent_deck or _DEFAULT_POOL)]
    hidden_n = [0]

    def place_visible(o: GameObject, zone: str, seat_name: str) -> None:
        slug = _slug(cards.card_name(o.grpId))
        inst = f"{slug}_{o.instanceId}"
        s["instance_of"].add((inst, slug))
        for ct in o.cardTypes:
            s["printed_type"].add((inst, _eng(ct)))
            # spell_type is a SHIM INPUT keyed per-instance (engine.dl SHIM_INPUTS), NOT derived from
            # printed_type. _playable_lands gates on spell_type(inst, "land") and can_cast on spell_type(inst,
            # "instant"/"sorcery"/…) — so without feeding it the engine sees NO playable lands and NO castable
            # spells for a determinized card, and a land-first player just passes. Fed from the GRE card types.
            s["spell_type"].add((inst, _eng(ct)))
        for st in o.subtypes:
            s["printed_subtype"].add((inst, _eng(st)))
        for sup in o.superTypes:
            s["has_supertype"].add((inst, _eng(sup)))
        for c in o.color:
            s["printed_color"].add((inst, _eng(c)))
        if o.p is not None:
            s["printed_power"].add((inst, o.p))
        if o.t is not None:
            s["printed_toughness"].add((inst, o.t))
        if zone == "hand":
            s["in_hand"].add((seat_name, inst))
            if o.instanceId in castable:                       # MTGA says we can pay -> let the engine cast it.
                # Feed `free_grant` (a SHIM_INPUT / EDB fact), NOT `free_cast`. `free_cast` is a DERIVED relation
                # (engine_rules.dl: free_cast(P,S) :- free_grant(P,S), playable_source(P,S)); the incremental
                # souffle driver carries state across calls, so a directly-fed derived value survives or gets
                # cleared depending on engine history — nondeterministic, and the cause of 'passed with a castable
                # creature in hand' (the cast vanished on the 2nd+ decision of a turn). free_grant is seeded as a
                # true input every run, so the engine DERIVES free_cast -> can_afford -> can_cast deterministically.
                s["free_grant"].add((seat_name, inst))
            if playable is not None and o.instanceId not in playable:
                s["spell_type"].discard((inst, "land"))        # not an offered land drop (e.g. a stale, already-
                #                                                played land still in the lagging view) -> hide it
            if costs and o.instanceId in costs:                # CMC from MTGA, for curve-out; affordability stays
                s["mana_cost"].add((inst, costs[o.instanceId]))  # free_grant path (affordability already granted)
        elif zone == "library":
            s["in_library"].add((seat_name, inst))
        elif zone == "command":                            # the commander (Brawl/Commander) — public
            s["command_zone"].add((seat_name, inst))
            s["is_commander"].add((inst,))
        else:                                              # battlefield
            s["on_battlefield"].add((inst,))
            s["printed_control"].add((seat_name, inst))
            if o.isTapped:
                s["tapped"].add((inst,))
            if o.is_attacking and defender_of.get(seat_name):
                # COMBAT: an attacking creature attacks the DEFENDING player (the other seat). Feeding this
                # `attacks(attacker, defender)` fact is what lets the engine enumerate real blocks at
                # declare-blockers — without it the engine sees combat with no attackers and offers only 'no
                # blocks'. (Player target only; attacks on planeswalkers aren't modelled here.)
                s["attacks"].add((inst, defender_of[seat_name]))

    def place_hidden(seat_name: str, zone: str) -> None:
        slug = rng.choice(pool)
        hidden_n[0] += 1
        inst = f"{slug}_x{hidden_n[0]}"                    # an imagined card: identity only, no revealed P/T
        s["instance_of"].add((inst, slug))
        (s["in_hand"] if zone == "hand" else s["in_library"]).add((seat_name, inst))

    # 1) place every VISIBLE object by its own zoneId (reliable per-object)
    placed = set()
    known = []                                             # (oracle_name, seat, instanceId) for the rules merge
    _ZONE = {"ZoneType_Battlefield": "battlefield", "ZoneType_Hand": "hand", "ZoneType_Library": "library",
             "ZoneType_Command": "command"}
    for o in view.objects.values():
        z = view.zones.get(o.zoneId)
        seat = view._seat_of(o)
        zone = _ZONE.get(z.type) if z else None
        if zone is None or seat not in name_of or not cards.card_name(o.grpId):
            continue
        place_visible(o, zone, name_of[seat])
        placed.add(o.instanceId)
        known.append((cards.card_name(o.grpId), name_of[seat], o.instanceId))

    # 2) DETERMINIZE the hidden remainder: each hand/library lists its instance ids (incl. face-down ones we
    #    can't see); any id not placed above is an imagined card sampled from the pool — count-accurate.
    for z in view.zones.values():
        zone = _ZONE.get(z.type)
        if zone not in ("hand", "library") or z.ownerSeatId not in name_of:
            continue
        nm = name_of[z.ownerSeatId]
        for iid in z.objectInstanceIds:
            if iid not in placed:
                place_hidden(nm, zone)

    # 3) PLAYABLE-FROM-ANYWHERE: MTGA's Play options are the ground truth of which lands we can play THIS turn —
    #    and they're not always in hand. Recent sets exile a card and let you play it ('impulse draw' / adventure
    #    / plot), so a Plains MTGA offers as a Play can sit in ZoneType_Exile (which the engine doesn't model).
    #    Surface every offered land as a playable hand land so the engine doesn't ignore it. (ActionType_Play is
    #    always a land drop; `playable` is None when ungated.)
    for iid in (playable or ()):
        o = view.objects.get(iid)
        if o is None or not cards.card_name(o.grpId):
            continue
        slug = _slug(cards.card_name(o.grpId))
        inst = f"{slug}_{iid}"
        s["instance_of"].add((inst, slug))
        s["in_hand"].add((name_of.get(me, ME_SEAT), inst))   # treat it as castable-from-hand for the §305 drop
        s["spell_type"].add((inst, "land"))

    # RULES-AWARE SYNC: load each known card's effect/ability/trigger facts so the bot reasons about what cards
    # DO (removal, ETB, keywords) when finding the next move — not just their board stats. Move-by-move: we
    # re-sync every decision, so this never needs to predict the opponent, just understand the current cards.
    if load_rules:
        _merge_card_rules(s, known)

    s["_turn"] = view.turn.turnNumber or 0
    s["_me"] = name_of.get(me, ME_SEAT)                     # OUR engine seat name — engine_policy binds its Player here
    s["_seed"] = seed
    s["_variant"] = view.variant                           # brawl / two-player, from the match's format
    # §305 EXPLICIT land drops: the bridge must surface "play a land" as an engine MOVE so it can ENACT it as an
    # MTGA click — without this the engine auto-develops lands in its own model and never offers the move, so a
    # land-first player (HeuristicPlayer/AggroPlayer all list Do.LANDS) sees no land to play and PASSES, stranding
    # the real land in hand into the end-of-turn discard. `Game.from_state` reads this flag off the state.
    s["_explicit_lands"] = True
    # §117.1a INSTANT-SPEED priority: MTGA is the priority authority — it only issues us an actions decision when
    # we ACTUALLY hold priority (incl. opponent-turn / combat windows). So always open the engine's instant-speed
    # window here; without it `legal_moves` at a non-main step is just [pass] and a payable instant (counterspell,
    # combat trick, removal) never surfaces — the engine declines reactive plays it's fully capable of. Instants
    # are still gated by `can_afford` (we only feed auto-payable casts) and by spell_type's cast_permission, so
    # this can't make a sorcery castable off-turn; it only un-hides the instants MTGA already offered us.
    s["_instant_speed"] = True
    return s


def to_game(view: GameView, me: int, *, opponent_deck: Optional[list] = None, seed: int = 0,
            castable: Optional[set] = None, playable: Optional[set] = None, costs: Optional[dict] = None,
            load_rules: bool = True):
    """An `mtg.Game` positioned at `view`'s board (visible info fed; hidden info determinized). Re-call as the
    log advances to re-derive the Game from the updated view. `castable` = MTGA instanceIds we can pay for now
    (fed as free_grant -> derived free_cast); `playable` = MTGA's offered land-drop instanceIds (gates §305 plays);
    `costs` = {instanceId: mana value} (fed as mana_cost, for curve-out); `load_rules` loads known cards' effect/
    ability facts so the engine reasons about what they DO (default on). See build_state."""
    from mtg.game import Game
    return Game.from_state(build_state(view, me, opponent_deck=opponent_deck, seed=seed,
                                       castable=castable, playable=playable, costs=costs, load_rules=load_rules))


def suggest(view: GameView, me: int, *, player=None, opponent_deck: Optional[list] = None, seed: int = 0):
    """Translate `view` into the `mtg` engine and report what a WITCHCRAFT player would do for the LOCAL player
    (mapped to 'alice'): the engine's suggested move, the legal-move menu, and a small state summary. `player` is
    any `mtg` Player instance (default: `BlindAggroPlayer` — develop, cast, swing, never block); pass another to
    drive with a different bot. Returns a dict, or None if the `mtg` engine isn't importable (inthearena stays
    usable without it — and the engine loads its datalog by a path relative to the repo root, so run from there)
    or the state can't be translated. NOTE: the engine only models a fraction of real cards today, so for an
    unmodeled board the suggestion will often be just 'pass' — this is the seam to build coverage against."""
    try:
        if player is None:
            from mtg.aggro import AggroPlayer            # default engine bot (beats HeuristicPlayer head-to-head)
            player = AggroPlayer()
    except Exception:
        return None
    try:
        game = to_game(view, me, opponent_deck=opponent_deck, seed=seed)
    except Exception:
        return None
    legal = [game.describe_move(m) for m in game.legal_moves]
    suggested = None
    try:
        move = player.bind(game, "alice").choose_move(game)
        suggested = game.describe_move(move)
    except Exception:
        suggested = None
    return {"turn": game.turn_number, "step": game.step, "active": game.active_player,
            "legal": legal, "suggested": suggested}
