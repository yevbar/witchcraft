"""forge_bridge.py — let our Python agent play AS A PLAYER inside a Forge game (vs the Forge AI).

WHY this shape. Forge (https://github.com/Card-Forge/forge) is a complete, AUTHORITATIVE Java rules
engine. Two rules engines can't co-simulate one game, so we don't try: **Forge runs the match and owns
the state; our Python agent is one seat.** Forge asks the seat to make each decision (mulligan, which
spell to play, targets, blocks, …) and our agent answers. That request/answer shape is exactly the
`_choose` / `legal_actions` seam the shim already exposes — so this module is a thin TRANSLATOR between
Forge's `PlayerController` decision calls and our policy, not a second engine.

  Forge (Java)  --decide-->  forge_bridge.ForgePlayer  --(state,key,options,default)-->  policy
                <--choice--                            <--chosen option--

TRANSPORT is line-delimited JSON, over a TCP socket (`serve`/`connect`) or stdio pipe (`run_stdio`). The
decision LOGIC is `ForgePlayer.handle(msg) -> reply`, a pure function of (message, policy, last observation)
— so it's fully testable in-process against a MOCK Forge (see test_forge_bridge.py / test_forge_engine.py)
with no socket/JVM.

WHERE THE REAL FORGE LIVES. This module is just the adapter. The actual Forge JVM connector — a headless
Forge match with witchcraft driving a seat through the socket above — is in `forge_integration/`
(ForgeVsBot.java, ForgeComboKill.java; run via forge_integration/run.sh, and see
forge_integration/README.md for prerequisites + setup). So: the mock tests check this protocol/policy
fast; forge_integration/ runs it for real against Forge's authoritative rules.

PROTOCOL (Forge connector -> us):
  {"type":"hello","you":<seatId>,"players":[...],"variant":...}      -> we reply {"type":"ready","name":...}
  {"type":"observe","state":{...}}                                    push a state snapshot (for the policy)
  {"type":"decide","id":N,"kind":K,"options":<kind-specific>,"default":<opt?>,"context":{...}}
  {"type":"result","winner":<seatId|null>} / {"type":"bye"}
us -> Forge:
  {"type":"ready",...} / {"type":"choice","id":N,"value":<kind-specific>}

DECISION KINDS (K) mirror Forge's PlayerController methods; options/value encodings in `_decide`:
  mulligan      <- mulliganKeepHand            value: bool (keep)
  action        <- getAbilityToPlay            options:[{id,label,kind}] incl a pass; value: chosen id-dict
  target        <- chooseTargetsFor            options:[{id,label}]      value: chosen id-dict (single)
  mode          <- chooseModeForAbility        options:[{id,label}]      value: chosen id-dict
  number        <- chooseNumber (X)            options:{min,max}         value: int
  confirm       <- confirmAction (a 'may')     options:[true,false]      value: bool
  choose        <- chooseSingleEntityForEffect options:[{id,label}]      value: chosen id-dict
  discard       <- chooseCardsToDiscard        options:{cards:[…],n:k}   value: [chosen ids]
  attackers     <- declareAttackers            options:{attackers:[…],defenders:[…]} value:[[atk,def],…]
  blockers      <- declareBlockers             options:{pairs:[[blk,atk],…]}         value:[[blk,atk],…]

INTEGRATION (the Forge/Java side — small, not in this repo): add a `PlayerController` that forwards each
decision method to this bridge. e.g. a `NetworkBotController extends PlayerControllerAi` whose
`mulliganKeepHand`, `getAbilityToPlay`, `chooseTargetsFor`, `declareAttackers/Blockers`, `chooseNumber`,
`confirmAction`, `chooseSingleEntityForEffect` each serialize a `decide` message, block on the socket for
the `choice`, and return it as the Forge object (look the id up in the live game). Run forge_bridge.serve()
first, point the Java connector at host:port, seat it as the AI's opponent, and the match plays out.
(Falling back to PlayerControllerAi for any decision kind this agent doesn't answer keeps a game legal.)

POLICY: any `(state, key, options, default) -> choice` (the shim's _choose signature) drives the bot —
game.random_policy / greedy_policy work as-is. The `state` passed is the latest Forge observation (a dict),
not our datalog state: Forge is authoritative, so the bot picks among the options FORGE deems legal. A
stronger, engine-backed policy (reconstruct our facts from the observation, run env lookahead) plugs in at
the same seam — `engine_policy` marks that hook.
"""

from __future__ import annotations

import json
import os
import random
import socket

# Forge PlayerController decision method -> our _choose seam key (so a policy keyed on the shim's own
# decision vocabulary drives the Forge bot unchanged).
_KIND_KEY = {
    "mulligan": "mulligan", "action": "action", "target": "target", "mode": "mode",
    "number": "number", "confirm": "confirm", "choose": "choose", "discard": "discard",
    "attackers": "attackers", "blockers": "blocks", "name": "name", "pay": "pay",
}


def greedy_policy(state, key, options, default):
    """Take Forge's suggested default at every decision (defers to Forge's own AI heuristic for that
    seat's pick) — the simplest legal bot."""
    return default


def random_policy(seed: int = 0):
    """A uniform-random policy over the offered options, with its OWN seeded RNG (reproducible games vs
    the Forge AI). Returns a `(state, key, options, default)` callable."""
    rng = random.Random(seed)

    def pol(state, key, options, default):
        opts = list(options) if options is not None else []
        return rng.choice(opts) if opts else default
    return pol


# ---- engine-backed policy: OUR engine provides and plays the move (Forge owns the state) --------------
# Forge guarantees the offered options are legal; the COMPLETENESS signal is whether OUR rules engine can
# independently MODEL each option (recognize the card/ability) and ENDORSE it (derive it as legal/playable
# in our own model). A modeled+endorsed move means our engine understands that game situation; an option we
# can't model or endorse is a coverage GAP we record. The bot always returns a VALID Forge option — when our
# engine endorses one it plays that; otherwise it falls back (and logs the gap). This is the "Stockfish for
# Magic": the shim's own engine choosing the move in a Forge-refereed game.

# Forge zone -> (our relation, is it player-scoped?). battlefield/graveyard/exile are arity-1 (card,);
# hand/library are arity-2 (player, card) — matching the engine's .decl for each.
_OBS_ZONE = {"battlefield": ("on_battlefield", False), "graveyard": ("graveyard", False),
             "exile": ("exile", False), "hand": ("in_hand", True), "library": ("in_library", True)}


def reconstruct(obs: dict, seat: str):
    """Build OUR driver state from a Forge observation snapshot (players/life/step + per-zone cards, each
    {id, name, controller, tapped?, counters?}). Each named card is bridged from cards.dl via its ORACLE
    NAME (bridge.card_facts) under its Forge id, so our engine reasons over the same board Forge shows.
    Returns (state, unmodeled) where `unmodeled` lists (zone, name) cards our interpreter doesn't cover —
    the direct completeness gaps. Cards not in our corpus still get a bare object (so id plumbing works)."""
    import driver
    import bridge_to_engine as bridge
    import sim
    import card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    players = obs.get("players") or [seat]
    life = {p: int(v) for p, v in (obs.get("life") or {}).items()}
    state: dict = {
        "is_player": {(p,) for p in players},
        "active_player": {(obs.get("active", players[0]),)},
        "current_step": {(obs.get("step", "precombat_main"),)},
        "life": {(p, life.get(p, 20)) for p in players},
        "on_battlefield": set(), "in_hand": set(), "graveyard": set(), "exile": set(),
        "in_library": set(), "tapped": set(), "counter": set(), "attacks": set(), "blocks": set(),
        "_land_played": set(),
    }
    unmodeled: list = []
    for zone, cards in (obs.get("zones") or {}).items():
        spec = _OBS_ZONE.get(zone)
        if spec is None:
            continue
        rel, player_scoped = spec
        for card in cards:
            cid, name = str(card["id"]), card["name"]
            ctrl = card.get("controller", seat)
            state.setdefault(rel, set()).add((ctrl, cid) if player_scoped else (cid,))
            state.setdefault("printed_control", set()).add((ctrl, cid))
            # GRAVEYARD CROSS-LAYER: also emit the player-scoped in_graveyard(owner, card) so the engine's
            # graveyard-count conditions (threshold/delirium/type counts) evaluate. The arity-1 `graveyard`
            # zone above carries no owner; in_graveyard is fed ONLY here (Forge games), leaving self-play —
            # which never reconstructs from a Forge obs — inert. Owner is the graveyard's controller field.
            if zone == "graveyard":
                state.setdefault("in_graveyard", set()).add((ctrl, cid))
            if card.get("token"):                            # §111 token -> 'control a token' cond_met (any zone/corpus)
                state.setdefault("is_token", set()).add((cid,))
            if name not in corpus:                           # a card our interpreter doesn't model -> gap
                # §111 TOKENS have no oracle/corpus entry, but Forge sends their derived characteristics
                # (token/creature/land + P/T). Synthesize a minimal object so the seat can actually USE
                # what it made (e.g. attack with Otter tokens from Stormchaser's Talent) — NOT a gap.
                if card.get("token"):
                    slug = f"_tok_{cid}"
                    state.setdefault("instance_of", set()).add((cid, slug))
                    if card.get("creature"):
                        state.setdefault("printed_type", set()).add((cid, "creature"))
                        state.setdefault("card_type", set()).add((slug, "creature"))
                        state.setdefault("spell_type", set()).add((cid, "creature"))
                        state.setdefault("printed_power", set()).add((cid, int(card.get("pow", 0))))
                        state.setdefault("printed_toughness", set()).add((cid, int(card.get("tou", 0))))
                    if card.get("land"):
                        state.setdefault("printed_type", set()).add((cid, "land"))
                        state.setdefault("card_type", set()).add((slug, "land"))
                    if card.get("tapped"):
                        state["tapped"].add((cid,))
                    continue
                unmodeled.append((zone, name))
                continue
            facts, _ = bridge.card_facts(name, ctrl, cid, db, corpus)
            for r, rows in facts.items():
                state.setdefault(r, set()).update(rows)
            c = corpus[name]
            for t in c.get("types") or []:                   # castable/playable from hand
                state.setdefault("spell_type", set()).add((cid, t.lower()))
            state.setdefault("mana_cost", set()).add((cid, bridge._mana_value(c.get("manaCost"))))
            bridge._register_colored(state, cid, c)
            if card.get("tapped"):
                state["tapped"].add((cid,))
            for kind, k in (card.get("counters") or {}).items():
                driver._bump_counter(state, cid, kind, int(k))
    # §103 LIBRARIES — the lookahead simulates from this state, so a faithful library matters for any
    # library-dependent line (Thassa's Oracle wins on an EMPTY library; without the opponent's library the
    # search would fabricate a deck-out win). The obs sends a per-player library COUNT ('libCounts'); we
    # synthesize that many lightweight placeholder cards per player (id + instance_of only — enough for the
    # §701.18 exile + the §104 library-count win condition). Not the real contents (the opponent's are
    # hidden, and the combo names a card NOT in the deck regardless), just a faithful count.
    for p, cnt in (obs.get("libCounts") or {}).items():
        have = sum(1 for (pp, _c) in state["in_library"] if pp == p)
        for i in range(max(0, int(cnt) - have)):
            cid = f"_lib_{p}_{i}"
            state["in_library"].add((p, cid))
            state.setdefault("instance_of", set()).add((cid, "_libcard"))
            state.setdefault("printed_control", set()).add((p, cid))
    bridge._materialize_printed(state)
    # §608 carry Forge's per-turn spell-cast count (game.getStack().getSpellsCastThisTurn().size()) so our
    # model's §702.40 storm count matches Forge mid-turn — without it a re-search would think storm=0 after
    # Forge already cast several spells this turn (the desync the lookahead-driven policy must avoid).
    state["_cast_count"] = int(obs.get("castThisTurn", 0) or 0)
    # §106.4 carry Forge's FLOATING mana (the live mana pool) so the lookahead spends mana already produced
    # (a ritual's output) before tapping sources — combos that hinge on precise floating mana stay in sync.
    state["floating_mana"] = {(p, c, int(n)) for p, pool in (obs.get("floating") or {}).items()
                              for c, n in (pool or {}).items() if int(n) > 0}
    return state, unmodeled


class EnginePolicy:
    """A policy (state, key, options, default)->choice in which OUR datalog engine selects the move. It
    reconstructs the board from the Forge observation, asks the engine what it can model/endorse among the
    offered options, plays an endorsed move when one exists (else falls back), and records coverage stats
    — the running indication of how completely our engine models a real game. `__call__` matches the
    ForgePlayer policy seam, so it drops in wherever random_policy/greedy_policy go."""

    def __init__(self, fallback=greedy_policy, max_turns=None, node_budget=None):
        self.fallback = fallback
        # The lookahead horizon (how many turns ahead the search will look for a win) and node cap. Default
        # to a shallow turn-1 lethal-finder; the tournament overrides via MTG_SEARCH_TURNS / MTG_SEARCH_BUDGET
        # to compare search depths (deeper = endorses multi-turn kills it can't see at depth 1, but costs more).
        self.max_turns = max_turns if max_turns is not None else int(os.environ.get("MTG_SEARCH_TURNS", "1"))
        self.node_budget = node_budget if node_budget is not None else int(os.environ.get("MTG_SEARCH_BUDGET", "20000"))
        # when no forced win is found, DEVELOP toward the deck's win axis (§104) instead of passing — the axis
        # comes from deck_evaluator.deck_axis, handed in via MTG_DECK_AXIS. None -> pure win-or-pass.
        self.axis = os.environ.get("MTG_DECK_AXIS") or None
        self.progress_turns = int(os.environ.get("MTG_PROGRESS_TURNS", "4"))   # deep enough to value the
        self.progress_budget = int(os.environ.get("MTG_PROGRESS_BUDGET", "3000"))  # follow-through (attack), not just setup
        # the deck's primary synergy combo (interaction_evaluator.synergy_cluster) — slugs + size, handed in
        # via MTG_SYNERGY / MTG_SYNERGY_SIZE — so the develop search can value assembling/invoking the combo
        # against direct win progress on a shared %-scale. start_life sets the §104 life ref (20 vs 40).
        _syn = os.environ.get("MTG_SYNERGY")
        self.synergy = ({"slugs": set(_syn.split(",")), "size": int(os.environ.get("MTG_SYNERGY_SIZE", "0"))}
                        if _syn else None)
        self.start_life = int(os.environ.get("MTG_START_LIFE", "20"))
        # PERFECT-INFORMATION ADVERSARIAL MODE (§ minimax): when MTG_MINIMAX=1, the develop search is
        # find_minimax — maximize my win while the opponent (on MTG_OPP_AXIS, its deck's §104 axis) plays its
        # best line to win/deny. Default off -> the opponent-passive find_progress.
        self.minimax = os.environ.get("MTG_MINIMAX") == "1"
        self.opp_axis = os.environ.get("MTG_OPP_AXIS") or "life_zero"
        self.minimax_turns = int(os.environ.get("MTG_MINIMAX_TURNS", "3"))   # horizon ceiling (time-bounded below)
        self.minimax_time = float(os.environ.get("MTG_MINIMAX_TIME", "2.5"))  # wall-clock cap per decision
        self.stats = {"decisions": 0, "engine_decided": 0, "offered": 0, "modeled": 0, "endorsed": 0,
                      "unmodeled_cards": set(), "by_kind": {}, "search_turns": self.max_turns}
        self._pending_name = None      # the card name the lookahead planned for the next 'choose a card name'
        self._slug2name = None         # lazy slug -> oracle-name map (the search names a card by slug)

    def _slug_to_name(self, slug):
        """Map the env's card-name SLUG (what the lookahead picks, e.g. 'standard_procedure') back to the
        oracle name Forge expects ('Standard Procedure'). The sentinel 'name a card not in your deck' rides
        this path like any other named card — nothing combo-specific."""
        if slug is None:
            return None
        if self._slug2name is None:
            import card_corpus
            import ground
            self._slug2name = {ground.slug(c["name"]): c["name"] for c in card_corpus.load_cards()}
        return self._slug2name.get(str(slug))

    def _pick_name(self, driver, state, seat, options, default):
        """Serve the card name the lookahead planned at cast time (stored in _pick_action). The SEARCH chose
        it — here we only relay it to Forge's 'choose a card name' prompt."""
        nm = self._pending_name
        self._pending_name = None
        return (nm, 1, 1, 1, 1) if nm else (None, 0, 0, 1, 0)

    def _pick_pay(self, driver, state, seat, options, default):
        """§106 — OUR mana model decides the EXACT payment: which untapped sources to tap and what color each
        produces, so Forge executes the precise mana (the sources/colors a combo can hinge on — e.g. pay {B}
        from a Mox, not by sacrificing a Black Lotus needed later for {U}{U}). Returns the plan, or None to
        let Forge pay it (a cost our model can't cover from the reconstructed board)."""
        cost = options if isinstance(options, dict) else {}
        pips = {str(k): int(v) for k, v in (cost.get("pips") or {}).items()}
        generic = int(cost.get("generic") or 0)
        plan = driver.mana_plan(state, seat, pips, generic)
        if not plan:
            return None, 0, 0, 1, 0
        return plan, 1, 1, 1, 1                                # ONE payment decision endorsed (not len(plan) -> frac>1)

    def _pick_mulligan(self, driver, state, seat, options, default):
        """§103.4 — keep a hand with a workable land count (2-5 of 7); mulligan a no-lander or a flood. Cap at
        2 mulligans so a low-land deck doesn't mull to death (London: keep the next hand regardless). Without
        this the seat kept EVERY hand — including 0-land and all-land — and stalled before it could develop."""
        self._mulls = getattr(self, "_mulls", 0)
        hand = [c for (p, c) in state.get("in_hand", set()) if p == seat]
        lands = sum(1 for c in hand if (c, "land") in state.get("spell_type", set()))
        keep = (2 <= lands <= 5) or self._mulls >= 2 or len(hand) <= 4
        if not keep:
            self._mulls += 1
        return keep, 1, 1, 1, 1

    def __call__(self, obs, key, options, default):
        seat = obs.get("seat")
        handler = getattr(self, f"_pick_{key}", None)
        self.stats["decisions"] += 1
        if handler is None or seat is None:
            return self.fallback(obs, key, options, default)
        try:
            import driver
            state, unmodeled = reconstruct(obs, seat)
            self.stats["unmodeled_cards"].update(n for _z, n in unmodeled)
            choice, modeled, endorsed, offered, used_engine = handler(driver, state, seat, options, default)
        except Exception:
            return self.fallback(obs, key, options, default)
        bk = self.stats["by_kind"].setdefault(key, {"offered": 0, "modeled": 0, "endorsed": 0, "engine": 0})
        bk["offered"] += offered; bk["modeled"] += modeled; bk["endorsed"] += endorsed; bk["engine"] += used_engine
        self.stats["offered"] += offered; self.stats["modeled"] += modeled; self.stats["endorsed"] += endorsed
        self.stats["engine_decided"] += used_engine
        return choice if choice is not None else self.fallback(obs, key, options, default)

    # each _pick_* returns (choice, modeled, endorsed, offered, used_engine)
    def _cast_from_path(self, path, spells):
        """Map a lookahead line's first move, when it's a CAST, to the matching offered spell option (by host
        card id), recording any 'choose a card name' sub-choice for _pick_name. None if the first move isn't a
        cast or isn't among the offered castable spells (e.g. it needs mana Forge can't pay right now)."""
        if not (path and path[0][0] == "cast"):
            return None
        a0 = path[0]
        choice = next((o for o in spells if str(o["id"]) == str(a0[2])), None)
        if choice is not None:
            sub = a0[3] if len(a0) > 3 and isinstance(a0[3], dict) else {}
            self._pending_name = self._slug_to_name(sub.get("name")) if sub.get("name") else None
        return choice

    def _pick_action(self, driver, state, seat, options, default):
        """Drive the play decision with the engine's OWN lookahead. Each decision, from OUR seat's reconstructed
        main phase: (1) if there's a line that WINS this turn, play its first cast; (2) else DEVELOP — play a
        land if Forge offers one (§305: build the mana base, one per turn, so future casts are affordable);
        (3) else cast the move that makes the most PROGRESS toward the deck's win axis + synergy combo. The
        lookahead reasons from Forge's REAL current mana (we mark this turn's land drop used so it doesn't
        phantom-develop an extra land and pick a cast Forge can't pay for). Falls back to a safe default if it
        can find nothing — NO Forge-AI strategy."""
        import win_search
        spells = [o for o in options if isinstance(o, dict) and o.get("kind") == "spell"]
        lands = [o for o in options if isinstance(o, dict) and o.get("kind") == "land"]
        objs = {o for (o,) in state.get("on_battlefield", set())} | {c for (_p, c) in state.get("in_hand", set())}
        # offered/modeled count ALL play options surfaced here — spells AND lands. Lands must be included:
        # the engine actively plays them (a land is a modeled, endorsed decision), so omitting them from the
        # denominator let a land play add 1 to endorsed against 0 offered -> cumulative endorsed_frac > 1.
        offered = len(spells) + len(lands)
        modeled = sum(1 for o in spells if str(o["id"]) in objs) + len(lands)
        s = dict(state)
        s["active_player"] = {(seat,)}
        s.setdefault("current_step", {("precombat_main",)})
        s["_land_played"] = set(s.get("_land_played", set())) | {(seat,)}   # reason from CURRENT mana (no phantom land)
        # 1) a forced win THIS turn — the combo / lethal line.
        path, _n = win_search.find_win(s, me=seat, max_turns=self.max_turns, node_budget=self.node_budget)
        choice = self._cast_from_path(path, spells)
        kind = "win" if choice is not None else None
        # 2) no kill -> DEVELOP: play a land to grow the mana base (Forge surfaces land plays here; it enforces
        # one per turn). This is the bootstrap the seat needs — without lands it can never cast its spells.
        if choice is None and lands:
            choice, kind = lands[0], "land"
        # 3) else develop toward the win axis + synergy combo with a castable spell. In ADVERSARIAL mode
        # (self.minimax) the develop search is find_minimax — the move that maximizes my winning while the
        # opponent plays its best line to win/deny (perfect information); otherwise the opponent-passive
        # find_progress.
        if choice is None and self.axis:
            if self.minimax:
                path, _ = win_search.find_minimax(s, me=seat, my_axis=self.axis, opp_axis=self.opp_axis,
                                                  max_turns=self.minimax_turns, node_budget=self.progress_budget,
                                                  synergy=self.synergy, start_life=self.start_life,
                                                  time_budget=self.minimax_time)
                kind_tag = "minimax"
            else:
                path, _ = win_search.find_progress(s, me=seat, axis=self.axis,
                                                   max_turns=self.progress_turns, node_budget=self.progress_budget,
                                                   synergy=self.synergy, start_life=self.start_life)
                kind_tag = "progress"
            choice = self._cast_from_path(path, spells)
            kind = kind_tag if choice is not None else kind
        if os.environ.get("MTG_DEBUG"):
            import sys as _sys
            print(f"[dbg] step={sorted(state.get('current_step', set()))} spells={len(spells)} lands={len(lands)} "
                  f"-> {kind}:{choice['id'] if isinstance(choice, dict) else None}", file=_sys.stderr, flush=True)
        endorsed = 1 if choice is not None else 0
        return choice, modeled, endorsed, offered, endorsed

    def _pick_target(self, driver, state, seat, options, default):
        """Target an entity our engine models as a legal creature target."""
        creatures = {c for (c,) in driver.run(state, ["creature"])["creature"]}
        opts = [o for o in options if isinstance(o, dict)]
        modeled = [o for o in opts if str(o["id"]) in creatures]
        choice = modeled[0] if modeled else (opts[0] if opts else default)
        return choice, len(modeled), len(modeled), len(opts), 1

    def _pick_attackers(self, driver, state, seat, options, default):
        """Among Forge's candidate attack declarations, pick the one whose attackers OUR engine endorses
        (may_attack) and that swings widest — our engine choosing the attack."""
        eligible = {c for (c,) in driver.run(state, ["may_attack"])["may_attack"]}
        best, best_n = default, -1
        offered = sum(len(c) for c in options if c)
        endorsed = 0
        for cand in options:
            ok = [pair for pair in cand if str(pair[0]) in eligible]
            if len(ok) > best_n:
                best, best_n = (cand if len(ok) == len(cand) else ok), len(ok)
            endorsed = max(endorsed, len(ok))
        return best, endorsed, endorsed, max(offered, 1), 1

    def _pick_blocks(self, driver, state, seat, options, default):
        """Block to AVOID LETHAL, else take the damage — the seat's defense MIRRORS the search's opponent
        model (a racing/aggro posture: keep creatures attacking, chump only to survive). Among Forge's legal
        candidate assignments (our engine agrees no illegal_block), pick: no-block if the unblocked swing is
        survivable, else the legal block that lets the seat live with the least damage through."""
        offered = max(sum(len(c) for c in options if c), 1)
        pw = {c: int(n) for (c, n) in driver.run(state, ["power"])["power"]}
        attackers = {a for (a, d) in state.get("attacks", set()) if d == seat}
        mylife = next((int(n) for (q, n) in state.get("life", set()) if q == seat), 20)

        def dmg(cand):
            blocked = {a for (_b, a) in cand}
            return sum(pw.get(a, 0) for a in attackers if a not in blocked)

        legal = []
        for cand in options:
            probe = driver.clone_state(state)
            probe["blocks"] = {tuple(p) for p in cand}
            if not driver.run(probe, ["illegal_block"])["illegal_block"]:
                legal.append(cand)
        if not legal:
            return default, 0, 0, offered, 1
        no_block = next((c for c in legal if not c), None)
        if no_block is not None and dmg(no_block) < mylife:       # not lethal -> race, keep blockers attacking
            return no_block, 0, 0, offered, 1
        best = min(legal, key=dmg)                                # lethal -> survive on the least damage through
        return best, len(best), len(best), offered, 1

    def coverage(self) -> dict:
        """A completeness report: of the options Forge offered at engine-handled decisions, the fraction our
        engine MODELED (recognized) and ENDORSED (derived as legal/playable), plus the cards it couldn't model."""
        s = self.stats
        frac = lambda a, b: round(a / b, 3) if b else None
        return {
            "decisions": s["decisions"], "engine_decided": s["engine_decided"],
            "options_offered": s["offered"], "modeled": s["modeled"], "endorsed": s["endorsed"],
            "modeled_frac": frac(s["modeled"], s["offered"]), "endorsed_frac": frac(s["endorsed"], s["offered"]),
            "unmodeled_cards": sorted(s["unmodeled_cards"]), "by_kind": s["by_kind"],
        }


def engine_policy(obs, key, options, default):
    """Module-level convenience: a single shared EnginePolicy instance (so its coverage() accumulates)."""
    return _ENGINE_SINGLETON(obs, key, options, default)


_ENGINE_SINGLETON = EnginePolicy()


class RandomPolicy:
    """A uniform-random BASELINE seat: among the legal options Forge offers at each decision, pick one at
    random (a mana-payment Forge can do itself is deferred). This is the control our win_search 'stockfish'
    must beat — if the search can't out-play random, it isn't earning its cost. Same (obs, key, options,
    default)->choice seam as EnginePolicy, so it drops straight into ForgePlayer."""

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)                       # seeded -> reproducible
        self.history: list = []
        self.stats = {"decisions": 0, "policy": "random"}

    def __call__(self, obs, key, options, default):
        self.stats["decisions"] += 1
        if key == "pay":                                      # let Forge pay mana (random pip choices break casts)
            return default
        if isinstance(options, (list, tuple)) and options:
            return self._rng.choice(list(options))
        return default

    def coverage(self) -> dict:
        return {"policy": "random", "decisions": self.stats["decisions"], "modeled_frac": None, "endorsed_frac": 0.0}


class ForgePlayer:
    """A Forge seat driven by a policy. `handle(msg)` is a pure message->reply step (None = no reply);
    the transports below just pump messages through it."""

    def __init__(self, policy=greedy_policy, name: str = "datalog-agent"):
        self.policy = policy
        self.name = name
        self.seat = None
        self.obs: dict = {}                 # latest Forge state snapshot, handed to the policy as `state`
        self.history: list = []             # (kind, value) of every decision made — for tests / replay

    # -- message handling ------------------------------------------------------------------------------
    def handle(self, msg: dict):
        t = msg.get("type")
        if t == "hello":
            self.seat = msg.get("you")
            self.obs = {"seat": self.seat, "players": msg.get("players", []), "variant": msg.get("variant")}
            return {"type": "ready", "name": self.name, "seat": self.seat}
        if t == "observe":
            self.obs = {**self.obs, **(msg.get("state") or {})}
            return None
        if t == "decide":
            return self._decide(msg)
        if t in ("result", "bye", "ping"):
            return {"type": "pong"} if t == "ping" else None
        return {"type": "error", "reason": f"unknown message type {t!r}"}

    def _choose(self, key, choices, default):
        """Route one decision through the policy (the shim seam), validating the pick is legal."""
        choice = self.policy(self.obs, key, choices, default)
        if choices and choice not in choices:                # a stray policy pick -> fall back to legal default
            choice = default if default in choices else choices[0]
        self.history.append((key, choice))
        return choice

    def _decide(self, msg: dict) -> dict:
        kind = msg.get("kind")
        key = _KIND_KEY.get(kind, kind)
        opts = msg.get("options")
        default = msg.get("default")
        value = self._resolve(kind, key, opts, default)
        return {"type": "choice", "id": msg.get("id"), "value": value}

    def _resolve(self, kind, key, opts, default):
        # single-pick kinds: the option list IS the choice space; the chosen option is the value.
        if kind in ("action", "target", "mode", "choose"):
            choices = list(opts or [])
            if not choices:
                return default
            return self._choose(key, choices, default if default in choices else choices[0])
        if kind == "mulligan":
            return bool(self._choose(key, [True, False], True if default is None else bool(default)))
        if kind == "confirm":
            return bool(self._choose(key, [True, False], False if default is None else bool(default)))
        if kind == "number":
            lo, hi = int(opts.get("min", 0)), int(opts.get("max", 0))
            rng = list(range(lo, hi + 1)) or [lo]
            return int(self._choose(key, rng, lo if default is None else int(default)))
        if kind == "name":                                   # §701.18 'choose a card name' (Demonic Consultation)
            return self._choose(key, [], default if default is not None else "")  # free-form: the policy names it
        if kind == "pay":                                    # §106 mana payment — the policy returns a SOURCE plan
            # the cost rides in `opts` ({pips, generic}); pass it straight to the policy (no option-set
            # validation — the value is a free-form plan, not a pick from a list). [] -> Forge pays itself.
            plan = self.policy(self.obs, key, opts or {}, default if default is not None else [])
            self.history.append((key, plan))
            return plan if plan is not None else []
        if kind == "discard":
            cards = list((opts or {}).get("cards", []))
            n = int((opts or {}).get("n", 0))
            return self._pick_subset(key, cards, exactly=min(n, len(cards)))
        if kind == "attackers":
            return self._declare_attackers(key, opts or {}, default)
        if kind == "blockers":
            return self._declare_blockers(key, opts or {}, default)
        return default                                       # unknown kind -> Forge's default keeps it legal

    # -- combat / multi-pick (the candidate-set style env uses: offer a few full declarations, pick one) -
    def _pick_subset(self, key, items, exactly=None):
        """Choose a subset of `items` by repeatedly picking one (records each via the seam). `exactly` n
        forces a count (discard n); otherwise the policy may stop early via a None sentinel."""
        chosen, pool = [], list(items)
        while pool and (exactly is None or len(chosen) < exactly):
            opt = self._choose(key, pool + ([None] if exactly is None else []), pool[0])
            if opt is None:
                break
            chosen.append(opt)
            pool.remove(opt)
        return chosen

    def _declare_attackers(self, key, opts, default):
        """Offer candidate attack declarations (none / all-at-first-defender / each singleton) and pick
        one — the same finite candidate-set env.legal_actions uses, but over Forge-provided ids."""
        attackers = list(opts.get("attackers", []))
        defenders = list(opts.get("defenders", [])) or [None]
        d0 = defenders[0]
        cands = [[]]                                         # no attack
        if attackers:
            cands.append([[a, d0] for a in attackers])       # alpha strike at the first defender
            cands += [[[a, d0]] for a in attackers]          # each lone attacker
        dflt = default if default in cands else cands[0]
        return self._choose(key, cands, dflt)

    def _declare_blockers(self, key, opts, default):
        """Offer candidate block assignments (none / greedy one-per-attacker / each single legal block)."""
        pairs = [tuple(p) for p in opts.get("pairs", [])]
        cands = [[]]
        greedy, used_b, used_a = [], set(), set()
        for (b, a) in pairs:
            if b not in used_b and a not in used_a:
                greedy.append([b, a]); used_b.add(b); used_a.add(a)
        if greedy:
            cands.append(greedy)
        for (b, a) in pairs:
            if [[b, a]] not in cands:
                cands.append([[b, a]])
        dflt = default if default in cands else cands[0]
        return self._choose(key, cands, dflt)


# ---- transports ---------------------------------------------------------------------------------------

def _pump(player: ForgePlayer, recv_line, send_line) -> str | None:
    """Drive one Forge connection to completion: read JSON lines, hand each to the player, write any
    reply. Returns the game result's winner seat (or None). Transport-agnostic — `recv_line` yields
    decoded str lines (or None at EOF), `send_line` writes one str line."""
    winner = None
    while True:
        line = recv_line()
        if not line:
            break
        msg = json.loads(line)
        if msg.get("type") == "result":
            winner = msg.get("winner")
        reply = player.handle(msg)
        if reply is not None:
            send_line(json.dumps(reply))
        if msg.get("type") in ("result", "bye"):
            break
    return winner


def serve(player: ForgePlayer, host: str = "127.0.0.1", port: int = 0, once: bool = True):
    """Listen for a Forge connector and play seats until it disconnects. Returns (bound_port, winner).
    The Forge-side connector dials host:port and speaks the line-JSON protocol."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    bound = srv.getsockname()[1]
    srv.listen(1)
    winner = None
    try:
        while True:
            conn, _addr = srv.accept()
            with conn, conn.makefile("r") as r, conn.makefile("w") as w:
                def send(s, _w=w):
                    _w.write(s + "\n"); _w.flush()
                winner = _pump(player, lambda _r=r: _r.readline(), send)
            if once:
                break
    finally:
        srv.close()
    return bound, winner


def connect(player: ForgePlayer, host: str, port: int) -> str | None:
    """Dial a Forge connector that is LISTENING (the inverse of serve). Returns the winner seat."""
    with socket.create_connection((host, port)) as conn, conn.makefile("r") as r, conn.makefile("w") as w:
        def send(s):
            w.write(s + "\n"); w.flush()
        return _pump(player, lambda: r.readline(), send)


def run_stdio(player: ForgePlayer, stdin=None, stdout=None) -> str | None:
    """Speak the protocol over stdin/stdout (a pipe transport for a Forge connector that spawns the bot
    as a subprocess). Returns the winner seat."""
    import sys
    ins = stdin or sys.stdin
    outs = stdout or sys.stdout

    def send(s):
        outs.write(s + "\n"); outs.flush()
    return _pump(player, lambda: ins.readline(), send)


if __name__ == "__main__":
    # Stand up a bot server on an ephemeral port for a Forge connector to dial.
    p = ForgePlayer(policy=random_policy(seed=0), name="datalog-random-bot")
    port, win = serve(p, port=0)
    print(f"datalog Forge bot finished on port {port}; winner seat: {win}")
