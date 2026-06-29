"""mtg.society_of_control — a hand-built MTG CONTROL bot, the iteration surface for control heuristics.

`SocietyOfControlPlayer` STARTS as a copy of `DeleuzePlayer` (mtg.deleuze) — which is itself a copy of
`HeuristicPlayer`: develop, attack with intent, block to matter, curve out creatures/permanents, throw a
player-targeting spell at the face. From there it's the canvas to grow CONTROL play onto — trade resources,
answer threats with removal, draw cards, hold up reactive instants, stabilise then win — independently of the
aggro-leaning deleuze. (The name nods to Deleuze's *Postscript on the Societies of Control*, the sequel to the
player it forks.) Everything lives here in one file so the whole strategy is visible and editable: `choose_move`
declares the strategy as one prioritized list of scored preferences, and the `*_choice` methods (over the leaf
eval `_value`) are the metrics. `prioritize` picks the max-scoring move in the first non-empty category, so the
*order of the arguments is the strategy* and the per-move scorers are the *knobs*.

    from mtg import benchmark
    from mtg.society_of_control import SocietyOfControlPlayer
    benchmark(SocietyOfControlPlayer(), games=50)        # -> {'win_rate': ..., ...}
"""
from __future__ import annotations

import random

import env

from .game import Game
from .models import Move, PriorityOption as Do
from .players import Player
from .predicates import (anything, creature_damage, is_cantrip, is_commander_cast, is_creature,
                         is_creature_damage, is_draw_ability, is_mana_rock, is_permanent)


class SocietyOfControlPlayer(Player):
    """A hand-built MTG CONTROL heuristic, started as a copy of `DeleuzePlayer`: develop, attack with intent,
    block to matter, curve out. This is the canvas to evolve TOWARD control — removal, card advantage, reactive
    instants, stabilise-then-win. `choose_move` declares the strategy as one prioritized list of scored
    preferences; the `*_choice` methods are the metrics."""

    name = "society_of_control"

    # choose_move scores land plays (Do.LANDS / land_choice), so it needs the §305 land drop surfaced as a
    # move — the harness reads this and builds the Game with explicit_lands (else lands auto-develop and the
    # land logic never fires).
    wants_explicit_lands = True

    # leaf board-eval weights (used by _value; dial to tune the eval)
    W_LIFE_DIFF = 0.05
    W_AGGRO = 0.30          # push opponent toward 0
    W_BOARD_POWER = 0.30
    W_PRESENCE = 0.10
    W_CARDS = 0.08

    # combat scorer weights (dial to tune aggression / risk tolerance)
    LETHAL = 1000.0         # overwhelming bonus (attack) / penalty (block) for a lethal swing
    W_DAMAGE = 2.0          # value per point of damage an attack lands
    W_CRACKBACK = 1.5       # penalty weight on a lethal-looking crackback
    W_TRADE = 1.5           # value weight on winning / losing a creature in combat

    # curve-out (curve_choice): among the cards a matched line deploys, play the CHEAPER ones first. W_CURVE
    # (>> the develop_choice spread) makes mana value the primary sort and the board metric the tiebreak;
    # _CURVE_BASE keeps every matched card above the floor=0.0 gate so it still gets cast (a real mana value
    # never exceeds _CURVE_BASE).
    W_CURVE = 1.0
    _CURVE_BASE = 20.0

    # burn_choice base: a lethal creature-removal cast scores _BURN_BASE + a target-priority term (see below),
    # always >0 so it clears the floor=0.0 gate (it only fires on an actual kill).
    _BURN_BASE = 1.0

    # burn TARGET priority (which killable creature to point removal at), as one lexicographic float so
    # game.prioritize's max picks the top priority: the opponent's COMMANDER first — but ONLY when this spell can
    # actually kill it (it recurs from the command zone, so kill it on sight when you can) — then highest POWER
    # (the hardest clock), then highest TOUGHNESS (the most resilient body), then an arbitrary-but-reproducible
    # jitter. EXCEPTION to power-first: if the biggest-power killable creature is one we could already answer in
    # combat (a creature of ours blocks and kills it), don't waste removal there — rank by TOUGHNESS instead, to
    # kill the body combat can't. The weights just stack those keys so a higher tier always dominates the ones
    # below it; they assume sane creature P/T (well under ~10k).
    _TGT_COMMANDER = 1e9
    _TGT_POWER = 1e4
    _TGT_TOUGH = 1.0

    # forced-win gate slack: extra headroom on the cheap reach estimate so we never skip the (expensive) win scan
    # when a real kill is on the table — only when nobody is plausibly in range.
    _WIN_SLACK = 2

    def choose_move(self, game) -> Move | None:
        self.bind(game)                          # so self.creatures / self.opponent / self.life are live here

        win = self.force_win(game)               # TAKE OVER: if a win is forceable THIS turn, close the game —
        if win is not None:                      # a lethal line beats any positional heuristic (on our turn OR,
            return win                           # at instant speed, on the opponent's: the commander 'mousetrap')

        return game.prioritize(
            # Game actions
            Do.LANDS.prefer(self.land_choice),                                   # play a land (non-basics first),

            # Playing spells
            Do.SPELLS.matching(is_cantrip).prefer(self.free_cantrip_choice, floor=0.0),   # FREE one-drop cantrips first (commander refunds the cast),
            Do.SPELLS.matching(is_creature_damage).prefer(self.burn_choice, floor=0.0),   # KILL a threat with burn — incl generic any-target burn (only if it kills),
            Do.RESOLVE_TRIGGER.prefer(self.resolve_choice, floor=0.0),           # else aim a player-target spell at their face (kill a creature takes precedence),
            Do.SPELLS.matching(is_mana_rock).prefer(self.curve_choice, floor=0.0),        # RAMP — rocks/dorks first,
            Do.SPELLS.matching(is_commander_cast).prefer(self.curve_choice, floor=0.0),   # then the COMMANDER (Brawl) before any creature,
            Do.SPELLS.matching(is_creature).prefer(self.curve_choice, floor=0.0),         # then other CREATURES (curve out),
            Do.SPELLS.matching(is_permanent).prefer(self.curve_choice, floor=0.0),        # then other PERMANENTS,
            Do.SPELLS.matching(anything).prefer(self.develop_choice, floor=0.0),          # else ANY remaining spell if it beats passing,

            # Activating abilities
            Do.ABILITIES.prefer(self.develop_choice, floor=0.0),  # else the best ability, same gate,
            Do.ABILITIES.matching(is_draw_ability).prefer(self.draw_ability_choice, floor=0.0),  # else cash a body in for a card (engine + a pitch),

            # Combat related
            Do.ATTACKS.prefer(self.attack_choice),              # else the best attack declaration,
            Do.BLOCKS.prefer(self.block_choice),                # else the best block assignment,

            # Pass
            Do.SKIP,                                            # else pass.
        )

    # ---- forced-win take-over (close the game when lethal is available) --------------------------

    def is_win_forceable(self, game) -> bool:
        """True if a WIN is available THIS turn — a move whose resolution drops an opponent to 0 with us still
        alive, on OUR turn or, at instant speed, on the OPPONENT's (the commander 'mousetrap': sink unspent mana
        into its {X}: deal X ability for lethal). Just detects that a lethal move EXISTS now; `force_win` returns
        the move and `choose_move` takes it — move-by-move, re-checked after each step (so we never have to
        predict the opponent's replies, only react to the board in front of us)."""
        return self.force_win(game) is not None

    def force_win(self, game) -> "Move | None":
        """The next Move of a lethal line available this turn, or None. TAKE OVER from the positional heuristic to
        close the game. v1 = the first legal CAST/ACTIVATE/COMBAT move whose 1-ply resolution wins (lethal burn, a
        lethal X 'mousetrap' activation — the engine auto-pays the max affordable X, so a killing activation reads
        as a win here). Lands/pass can't win in one step and are skipped (cheap). A multi-card kill lands move-by-
        move: each decision we re-detect, so the line is taken as it becomes lethal."""
        # cheap gate: only bother simulating when the opponent is actually within reach this turn (avoids a full
        # env.step scan every decision when there's obviously no kill).
        if not self._lethal_plausible(game):
            return None
        for m in game.legal_moves:
            if getattr(m, "kind", None) in (None, "pass", "skip", "play", "land"):
                continue                                       # these never win on their own resolution
            if self._wins_now(game, m):
                return m
        return None

    def _lethal_plausible(self, game) -> bool:
        """Cheap upper-bound gate for the win scan: is an opponent low enough that THIS turn's burst could plausibly
        kill them? Bound = our untapped board power (a swing) + available mana (a proxy for X-burn / the mousetrap)
        + a small slack. Conservative on the high side so we never gate out a real kill, but skips the expensive
        simulation when nobody is in range."""
        st = game.state
        mana = max((m for (p, m) in st.get("mana_available", set()) if p == self.seat), default=0)
        swing = sum(c.power for c in self.creatures if not c.tapped)
        reach = mana + swing + self._WIN_SLACK
        return any(v <= reach for p, v in game.life().items() if p != self.seat)

    def _wins_now(self, game, move) -> bool:
        """Does resolving `move` win immediately — every opponent to 0 (or the game over in our favour) with us
        still alive? Simulated 1-ply via env.step (which auto-advances through resolution); False if the step can't
        resolve (e.g. it needs a follow-up choice the lookahead can't supply)."""
        try:
            child = Game.from_state(env.step(game.state, move.raw))
        except Exception:
            return False
        if child.is_game_over():
            return child.winner() == self.seat
        life = child.life()
        return life.get(self.seat, 1) > 0 and any(v <= 0 for p, v in life.items() if p != self.seat)

    def choose_x(self, game, *, lethal: int | None = None, affordable: int | None = None) -> int:
        """PLACEHOLDER for the 'choose value for X' decision (the commander mousetrap's {X}: deal X). Pick X to be
        exactly lethal when we know it, else the max affordable. NOTE: the engine ALREADY greedily pays the max
        affordable X by default (driver._choose 'x_value'), so a self-play kill works without this — it's the hook
        for once we drive X explicitly. TODO(inthearena): the live 'choose value for X' slider isn't a parsed GRE
        Req yet, so the bridge can't enact it — next step is to STOP the bot on that screen, screenshot it, model
        its Req, and wire an executor (set the slider to `choose_x`'s value, confirm)."""
        if lethal is not None and (affordable is None or lethal <= affordable):
            return lethal
        return affordable or 0

    # ---- choices (score ONE move; game.prioritize picks the max in each category) ----------------

    def land_choice(self, game, move) -> float:
        """Prefer NON-BASIC lands first: basics are the most fungible (any deck can fetch/replay them), so
        spend the scarcer, ability-bearing non-basics first and keep basics in reserve. (Only relevant under
        explicit_lands — in the default mode lands auto-develop and don't surface as moves.)"""
        return 0.0 if move.card.is_basic else 1.0

    # value of a free one-drop cantrip — any positive clears the floor=0.0 gate. It's the FIRST spell line, so a
    # genuinely-free cantrip is cast before anything else: it replaces itself (a card) and triggers the commander
    # (more mana / its on-cast effects) at no net cost, so there's no reason to hold it.
    _FREE_CANTRIP_VALUE = 1.0
    # the §205 card types a commander's on-cast trigger might name (to check the trigger covers THIS spell).
    _SPELL_TYPES = ("instant", "sorcery", "creature", "artifact", "enchantment", "planeswalker", "land", "battle")

    def free_cantrip_choice(self, game, move) -> float:
        """Score a ONE-DROP cantrip that our COMMANDER makes effectively FREE — cast it first. The matcher
        (`is_cantrip`) says the spell draws; this adds the two board conditions that make casting it pure upside:
        it's a ONE-DROP (mana value 1), and a commander we control REFUNDS the cast — a triggered 'whenever you
        cast …, add mana' ability whose trigger covers this spell's type (e.g. Electro, Assaulting Battery: add
        {R} on an instant/sorcery). Then the {1} comes straight back and the spell replaces itself, so there's no
        reason not to fire it before developing. Anything else (mv != 1, or no refunding commander in play) scores
        -inf and falls through to the normal spell lines, so this is inert without such a commander."""
        if self._mana_value(game, move.card.id) != 1:
            return float("-inf")
        if not self._commander_refunds_cast(game, move):
            return float("-inf")
        return self._FREE_CANTRIP_VALUE

    def _my_commander_insts(self, game) -> list:
        """Instance ids of the commanders WE control on the battlefield."""
        cmd = game.state.get("is_commander", set())
        return [p.id for p in self.battlefield if (p.id,) in cmd]

    def _slug_refunds_cast(self, game, slug, move) -> bool:
        """True if commander `slug` has a triggered 'whenever you cast …' ability that ADDS MANA and whose trigger
        covers `move`'s spell type — i.e. casting `move` would be refunded. From `ability_trigger` + `card_effect`."""
        if slug is None:
            return False
        triggers = game.state.get("ability_trigger", set())            # (slug, aid, phrase)
        effects = game.state.get("card_effect", set())
        for (s, aid, phrase) in triggers:
            if s != slug or "cast" not in str(phrase):
                continue                                               # not a 'whenever you cast …' trigger
            adds_mana = any(r[0] == slug and r[1] == aid and len(r) > 3 and r[3] == "add_mana" for r in effects)
            if adds_mana and self._spell_matches_trigger(move, str(phrase)):
                return True
        return False

    def _commander_refunds_cast(self, game, move) -> bool:
        """True if a commander we control ON THE BATTLEFIELD would REFUND casting `move` (so it's free RIGHT NOW).
        False without card rules / without such a commander in play."""
        return any(self._slug_refunds_cast(game, self._slug_of(game, i), move)
                   for i in self._my_commander_insts(game))

    def _my_command_zone(self, game) -> list:
        """The instance ids of OUR command zone (§408) — commanders not currently in play. [] off a commander game."""
        cz = game.command_zone(self.seat) if hasattr(game, "command_zone") else []
        return cz if isinstance(cz, list) else []

    def _refunding_commander_exists(self, game, move) -> bool:
        """True if a commander that would REFUND `move` (covers its type) exists for us anywhere — on the
        battlefield OR still in the command zone (so it WILL refund once cast). Used to decide whether holding a
        one-drop for 'when the commander's out' is even worthwhile; False in a non-commander game, so nothing is
        held there."""
        insts = list(self._my_commander_insts(game)) + self._my_command_zone(game)
        return any(self._slug_refunds_cast(game, self._slug_of(game, i), move) for i in insts)

    def _save_one_drop_burn(self, game, move) -> bool:
        """A one-drop NON-creature damage spell we'd rather HOLD: cast it later for FREE once the commander
        refunds it, developing in the meantime. Only holds when (a) it's a one-drop, (b) a commander that WOULD
        refund it exists (in play or the command zone — so the wait pays off; never in a non-commander game), and
        (c) it isn't already free (commander not yet in play). It can still KILL a creature now — that's the burn
        line, which runs BEFORE the face/develop lines this gate sits on, so a worthwhile kill is taken first."""
        if getattr(move, "kind", None) != "cast":
            return False
        card = move.card
        has_type = getattr(card, "has_type", None)
        if card is None or not callable(has_type) or has_type("creature"):
            return False
        if creature_damage(game, move) is None:                        # not a damage spell
            return False
        if self._mana_value(game, move.card.id) != 1:                  # one-drop only
            return False
        return self._refunding_commander_exists(game, move) and not self._commander_refunds_cast(game, move)

    def _spell_matches_trigger(self, move, phrase: str) -> bool:
        """Does `move`'s spell satisfy a cast-trigger `phrase`? If the phrase names card types (e.g. an
        'instant_or_sorcery' trigger), the spell must have one of them; a phrase that names no type (a generic
        'whenever you cast a spell') covers everything."""
        named = [t for t in self._SPELL_TYPES if t in phrase]
        if not named:
            return True
        card = move.card
        return card is not None and any(card.has_type(t) for t in named)

    def burn_choice(self, game, move) -> float:
        """Score a creature-targeting damage spell by the THREAT IT REMOVES — but only when it WOULD KILL an
        opponent creature (else -inf, so the floor=0.0 gate holds it). The engine enumerates one cast variant per
        legal target, so this scores the SPECIFIC creature this variant hits (`move.choices['target']`); when the
        spell carries no cast-time target it's scored by the best creature on the board (`_burn_target`). Lethality
        is `damage >= toughness` (ignores marked damage / deathtouch / indestructible — a heuristic). The 'is this
        creature-target damage at all' gate is the LINE's `.matching(is_creature_damage)`; this is the board
        judgement, and `_target_priority` orders WHICH creature: commander (when killable) > power > toughness.

        COMMANDER RESERVE: if the spell could kill the opponent's COMMANDER, hold the WHOLE spell unless a SECOND
        commander-killing burn card is in hand — a commander recasts from the command zone, so keep one answer in
        reserve for its next appearance rather than spending the last on it now (overrides the kill-commander-first
        target priority: reserve the card entirely when it's our only answer)."""
        dmg = creature_damage(game, move)
        if dmg is None:                                # (the matcher already gates this; belt-and-suspenders
            return float("-inf")                       #  so burn_choice is also safe to score standalone)
        victim = self._burn_target(game, move, dmg)
        if victim is None:
            return float("-inf")                       # this cast kills no opponent creature -> hold it
        cmd = self._opp_commander(game)
        if cmd is not None and cmd.toughness <= dmg:
            backups = self._commander_answers_in_hand(game, cmd.toughness) - {move.card.id}
            if not backups:
                return float("-inf")                   # our only commander-answer -> reserve the spell entirely
        return self._BURN_BASE + self._target_priority(game, victim, dmg)

    def _burn_target(self, game, move, dmg):
        """The opponent creature THIS burn kills, or None if it kills nothing. With a cast-time target
        (`move.choices['target']`, one variant per legal target) it's that creature — provided it's an opponent's
        and within the damage. With no cast-time target (the choice is deferred to resolution) it's the
        highest-`_target_priority` killable creature on the board, so the cast is still scored by the threat it
        would remove."""
        victims = [c for o in self.opponents for c in o.creatures if c.toughness <= dmg]
        tid = (move.choices or {}).get("target")
        if tid is not None:
            return next((c for c in victims if c.id == tid), None)
        return max(victims, key=lambda c: self._target_priority(game, c, dmg), default=None)

    def _target_priority(self, game, victim, dmg) -> float:
        """How much to prefer pointing removal at `victim` — the lexicographic burn-target order encoded as one
        float (higher = kill first):

          1. the opponent's COMMANDER, but ONLY when this spell can actually kill it (`toughness <= dmg`): it
             recurs from the command zone, so kill it on sight when you can — but a spell that CAN'T kill it
             (e.g. 4 damage at a 6/6 commander) must NOT bend the priority toward it; it just picks the best body.
          2. then highest POWER (the hardest clock) — EXCEPT when the biggest-power killable creature is one we
             could already answer in combat (a creature of ours blocks and kills it): then removal is better
             spent on the body combat can't kill, so rank by TOUGHNESS first instead.
          3. then the other of power/toughness, then an arbitrary jitter (a game-seeded value in [0,1) keyed by
             the creature id, so otherwise-identical creatures get a STABLE pseudo-random order — reproducible,
             and it never touches the game RNG stream).
        """
        if victim.toughness <= dmg and (victim.id,) in game.state.get("is_commander", set()):
            return (self._TGT_COMMANDER + self._TGT_POWER * victim.power
                    + self._TGT_TOUGH * victim.toughness + self._target_jitter(game, victim))
        # power-first, unless the biggest-power killable creature is one combat already answers -> toughness-first
        killable = [c for o in self.opponents for c in o.creatures if c.toughness <= dmg]
        top_power = max(killable, key=lambda c: c.power, default=None)
        toughness_first = top_power is not None and self._answerable_in_combat(top_power)
        primary, secondary = ((victim.toughness, victim.power) if toughness_first
                              else (victim.power, victim.toughness))
        return self._TGT_POWER * primary + self._TGT_TOUGH * secondary + self._target_jitter(game, victim)

    def _answerable_in_combat(self, target) -> bool:
        """True if a creature of ours could BLOCK AND KILL `target` and survive — an untapped creature with power
        >= the target's toughness (kills it) and toughness > the target's power (lives through it). When the
        biggest threat is answerable this way, burn is better spent elsewhere (see `_target_priority`)."""
        return any(not b.tapped and b.power >= target.toughness and b.toughness > target.power
                   for b in self.creatures)

    @staticmethod
    def _target_jitter(game, victim) -> float:
        """A stable per-creature tiebreak in [0,1): a game-seeded Random keyed by the creature id, so it's
        reproducible across runs and never consumes the game's own RNG stream."""
        return random.Random(f"{game.state.get('_seed', 0)}:{victim.id}").random()

    def _opp_commander(self, game):
        """The opponent's COMMANDER as a battlefield creature (is_commander + opponent-controlled), or None. A
        commander stays a commander on the board (the bridge tracks its card id across the cast from the command
        zone), so this catches the case where we could burn it down."""
        cmd = game.state.get("is_commander", set())
        return next((c for o in self.opponents for c in o.creatures if (c.id,) in cmd), None)

    def _commander_answers_in_hand(self, game, toughness) -> set:
        """Instance ids of OUR hand cards that are creature-target burn able to kill a creature of `toughness` —
        the pool of commander answers we could hold in reserve."""
        return {inst for (seat, inst) in game.state.get("in_hand", set())
                if seat == self.seat and (d := creature_damage(game, inst)) is not None and d >= toughness}

    def curve_choice(self, game, move) -> float:
        """CURVE OUT — among the cards a line MATCHES (mana rocks, creatures, other permanents), deploy the
        CHEAPER one first: mana value is the primary sort, the 1-ply board metric (`develop_choice`) the
        equal-cost tiebreak. `_CURVE_BASE` keeps every match above the floor=0.0 gate (a real mana value never
        reaches it), so a matched card is always deployed — the LINE's `.matching(...)` decides WHICH cards this
        applies to, this just orders them. (Shared by the mana-rock / creature / permanent lines.)"""
        mv = self._mana_value(game, move.card.id)
        return (self._CURVE_BASE - self.W_CURVE * mv) + self._develop_tiebreak(game, move)

    def _develop_tiebreak(self, game, move) -> float:
        """`develop_choice` used as the curve TIEBREAK — but a FAILED 1-ply lookahead (env.step raised, e.g. an
        uncovered/complex card resolution on a board with triggers like lifegain or Deafening Silence) returns
        -inf, and -inf would veto a deploy the curve already decided. Clamp it to 0 so the creature/permanent
        still gets cast, just without the board-value ordering refinement (the original heuristic's value-gated
        SPELLS line keeps the -inf there, where 'only act if it beats passing' is the intended behaviour)."""
        dv = self.develop_choice(game, move)
        return dv if dv != float("-inf") else 0.0

    def _mana_value(self, game, card_id) -> int:
        """The mana value (CMC) of `card_id` from the engine state — generic `mana_cost` plus the coloured
        `mana_pip` counts. (In the inthearena bridge `mana_cost` is fed as the total CMC from MTGA and there's no
        `mana_pip`, so this still sums to the right value; 0 when the cost is unknown.)"""
        st = game.state
        return (sum(n for (s, n) in st.get("mana_cost", set()) if s == card_id)
                + sum(n for (s, _c, n) in st.get("mana_pip", set()) if s == card_id))

    def resolve_choice(self, game, move) -> float:
        """Resolve a TARGETED effect at the OPPONENT (Do.RESOLVE_TRIGGER). The engine enumerates one cast/
        activate variant per legal target, so this scores the variant whose target is an opponent PLAYER at
        1.0 and every other variant at 0.0. Under the `floor=0.0` in choose_move that means only a player-
        targeting play (burn / 'target player') fires here — aimed at their face — while creature-targeting
        and untargeted plays score 0 and fall through to normal development. The SAME 'a player target -> the
        opponent' rule drives the inthearena bridge's MTGA SelectTargets pick (see engine_policy)."""
        if self._save_one_drop_burn(game, move):
            return 0.0                                      # HOLD a one-drop burn for when the commander makes it free
        target = (move.choices or {}).get("target")
        return 1.0 if target in {o.seat for o in self.opponents} else 0.0

    def attack_choice(self, game, move) -> float:
        """Value of declaring `move`'s attackers: damage that lands under a worst-case block (they block our
        biggest, the rest connect) plus an outright lethal swing, minus a lethal-looking crackback from the
        untapped creatures we'd leave home. Empty attack scores -1 (passing up an attack is rarely right)."""
        attackers = move.attackers
        if not attackers:
            return -1.0
        opp_blockers = self.opponent.blockers                          # their untapped creatures
        n_blockers = len(opp_blockers)
        opp_swing = sum(c.power for c in opp_blockers)                  # what they could hit back with
        my_life, opp_life = self.life, self.opponent.life
        powers = sorted((c.power for c in self.creatures if c.id in attackers), reverse=True)
        unblocked = sum(powers[n_blockers:]) if n_blockers < len(powers) else 0
        landed = min(unblocked, opp_life)
        staying = [c for c in self.creatures if c.id not in attackers and not c.tapped]
        my_def = my_life + sum(c.toughness for c in staying)
        risk = max(0, opp_swing - my_def) * self.W_CRACKBACK
        return (self.LETHAL if unblocked >= opp_life else 0.0) + self.W_DAMAGE * landed - risk

    def block_choice(self, game, move) -> float:
        """Value of `move`'s block assignment: damage prevented and trading up (their_loss), minus losing our
        own creatures (my_loss), minus a hard penalty for leaving a lethal amount unblocked. The attacking
        creatures come from the engine state (so an unblockable attacker's damage still counts)."""
        my_life = self.life                                            # I'm the defender during declare_blockers
        attackers = {a for (a, _d) in game.state.get("attacks", set())}
        blocks = move.blocks
        blocked = {a for (_b, a) in blocks}
        prevented = their_loss = my_loss = 0.0
        for (b, a) in blocks:
            ac, bc = game.card(a), game.card(b)
            prevented += ac.power
            if bc.power >= ac.toughness:
                their_loss += self._creature_value(ac)
            if ac.power >= bc.toughness:
                my_loss += self._creature_value(bc)
        unblocked = sum(game.card(a).power for a in attackers if a not in blocked)
        lethal_pen = self.LETHAL if unblocked >= my_life else 0.0
        return prevented + self.W_TRADE * their_loss - self.W_TRADE * my_loss - lethal_pen

    def develop_choice(self, game, move) -> float:
        """How much a non-combat play `move` IMPROVES the board: _value(after the play) - _value(now), via a
        1-ply lookahead. A value-negative play scores < 0, so the `floor=0.0` in choose_move skips it (and
        the chain falls through to passing) — the old 'only act if it beats sitting still' gate."""
        if self._save_one_drop_burn(game, move):
            return float("-inf")                           # HOLD a one-drop burn for when the commander makes it free
        try:
            child = Game.from_state(env.step(game.state, move.raw))    # env.step normalises the Move to .raw
        except Exception:
            return float("-inf")
        return self._value(child, self.seat) - self._value(game, self.seat)

    # value of activating a board-costing DRAW ability when it's allowed — any positive clears the floor=0.0 gate
    # (it's the LAST ability option, after develop_choice, so it only fires when nothing better wants the mana).
    _DRAW_ABILITY_VALUE = 1.0

    def draw_ability_choice(self, game, move) -> float:
        """Score an activated DRAW ability that COSTS US THE BOARD (sacrifices its own source). `develop_choice`
        rightly refuses these — trading a creature for a card is a board loss — so this is the deliberate
        exception: cashing the body in is FINE when the body is AFFORDABLE TO LOSE, by either route —
          (a) a separate CONTINUOUS draw engine keeps the hand refilling (a triggered / non-sacrifice repeatable
              draw — not a one-shot spell or another sac-draw); OR
          (b) the BOARD CAN SPARE IT — at least TWO OTHER creatures remain after the sacrifice (a wide board, so
              one body for a card — plus any ramp/token the sac also makes — is fine).
        Affordability of the activation itself is implicit: the ability only surfaces as a legal move when payable.
        Then, if the ability also DISCARDS (a rummage), we still require a 'discardable' card to pitch — an EXCESS
        LAND (a land in hand once we already control five). A draw ability that does NOT cost the board (it keeps
        its source) returns -inf here and is left to `develop_choice`, so only the sacrifice case changes.
        (Slug-level: keyed on the source card's facts, exact for the common one-activated-ability case.)"""
        slug = self._slug_of(game, move.card.id)
        if slug is None or not self._sacrifices_self_to_draw(game, slug):
            return float("-inf")                       # not a board-costing draw -> develop_choice handles it
        if not (self._has_continuous_draw_source(game, exclude=move.card.id)
                or self._board_can_spare_creature(game, move)):
            return float("-inf")                       # no engine AND a thin board -> don't trade the body for a wash
        if self._draws_with_discard(game, slug) and not self._has_discardable(game):
            return float("-inf")                       # a rummage with nothing worth pitching -> hold the body
        return self._DRAW_ABILITY_VALUE                # affordable to lose the body -> cashing it in is fine

    def _board_can_spare_creature(self, game, move) -> bool:
        """True if our board can afford to sacrifice `move`'s source creature — at least TWO OTHER creatures of
        ours remain after it goes. With a wide board, spending one body for a card (and any ramp/token the sac
        also yields) is fine."""
        return sum(1 for c in self.creatures if c.id != move.card.id) >= 2

    def _slug_of(self, game, inst):
        """The card slug for an instance id (from `instance_of`), or None."""
        return next((s for (i, s) in game.state.get("instance_of", set()) if i == inst), None)

    def _draw_aids(self, game, slug) -> set:
        """The ability ids of `slug` whose effect is `draw`."""
        return {r[1] for r in game.state.get("card_effect", set()) if r[0] == slug and len(r) > 3 and r[3] == "draw"}

    def _ability_costs(self, game, slug) -> list:
        """(aid, cost-text) for each of `slug`'s activated-ability costs (`ability_cost`)."""
        return [(aid, cost) for (s, aid, cost) in game.state.get("ability_cost", set()) if s == slug]

    def _sacrifices_self_to_draw(self, game, slug) -> bool:
        """True if a DRAW ability of `slug` sacrifices its OWN source as a cost ('Sacrifice ~') — the board-loss
        case develop_choice declines, and the one this exception is for."""
        draw = self._draw_aids(game, slug)
        return any(aid in draw and "sacrifice ~" in str(cost).lower()
                   for (aid, cost) in self._ability_costs(game, slug))

    def _draws_with_discard(self, game, slug) -> bool:
        """True if a DRAW ability of `slug` also DISCARDS as a cost (a rummage), which needs a card to pitch."""
        draw = self._draw_aids(game, slug)
        return any(aid in draw and "discard" in str(cost).lower()
                   for (aid, cost) in self._ability_costs(game, slug))

    def _has_continuous_draw_source(self, game, *, exclude) -> bool:
        """True if we control a CONTINUOUS draw engine other than `exclude`: a battlefield permanent whose draw
        comes from a TRIGGERED ability (e.g. Byway Barterer) or a NON-sacrifice ACTIVATED ability (e.g. Diary of
        Dreams) — i.e. repeatable, not a one-shot spell or another sac-draw."""
        for perm in self.battlefield:
            if perm.id == exclude:
                continue
            slug = self._slug_of(game, perm.id)
            if slug is None:
                continue
            draw = self._draw_aids(game, slug)
            if not draw:
                continue
            kinds = {aid: kind for (s, aid, kind) in game.state.get("card_ability", set()) if s == slug}
            costs = dict(self._ability_costs(game, slug))
            for aid in draw:
                if kinds.get(aid) == "triggered":
                    return True                        # a triggered draw engine (Byway Barterer)
                if kinds.get(aid) == "activated" and "sacrifice ~" not in str(costs.get(aid, "")).lower():
                    return True                        # a repeatable activated draw (Diary of Dreams)
        return False

    def _has_discardable(self, game) -> bool:
        """True if we hold a card freely worth pitching to a rummage — an EXCESS LAND: a land in hand once we
        already control five lands (the sixth is surplus). Only excess lands count as discardable here."""
        if len(self.me.permanents(type="land")) < 5:
            return False
        ptype = game.state.get("printed_type", set())
        return any((inst, "land") in ptype for inst in self.hand)

    @staticmethod
    def _creature_value(c) -> float:                                   # a rough creature worth for a trade
        return c.power + c.toughness + 1.0

    # ---- leaf board eval -------------------------------------------------------------------------

    def _value(self, game, seat: str) -> float:
        """Aggression-tilted board eval in [-1, 1] from `seat`'s view. Reads the board through the bound seat
        views (`self.creatures` / `self.opponent.creatures` — typed `Permanent`s with `.power`), so the bind
        is pointed at `game` for the read and restored afterwards (`game` may be a hypothetical child)."""
        if game.is_game_over():
            w = game.winner()
            return 1.0 if w == seat else (-1.0 if w is not None else 0.0)
        life = game.life()
        if seat not in life:
            return 0.0
        my_life = life[seat]
        opp_life = min((v for p, v in life.items() if p != seat), default=20)
        with self.bound(game, seat):                           # point self.creatures/.opponent at `game`, restore after
            my_creatures, opp_creatures = self.creatures, self.opponent.creatures
        my_pow = sum(c.power for c in my_creatures)
        opp_pow = sum(c.power for c in opp_creatures)
        hands = game.hand_count()
        my_hand = hands.get(seat, 0)
        opp_hand = max((v for p, v in hands.items() if p != seat), default=0)
        v = (self.W_LIFE_DIFF * (my_life - opp_life) / 20.0
             + self.W_AGGRO * (20 - opp_life) / 20.0
             + self.W_BOARD_POWER * (my_pow - opp_pow) / 10.0
             + self.W_PRESENCE * (len(my_creatures) - len(opp_creatures)) / 6.0
             + self.W_CARDS * (my_hand - opp_hand) / 5.0)
        if my_life <= 5:
            v -= 0.25
        return max(-0.99, min(0.99, v))
