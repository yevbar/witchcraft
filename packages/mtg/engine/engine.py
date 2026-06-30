"""engine.py — a small, reviewable MTG game engine that plays FULLER games from the grounded facts.

Builds on sim.py: instead of a scripted effect demo, this runs an actual turn loop (untap → upkeep →
draw → main → combat → main2 → end), with a mana system, the stack, casting from hand, ETB triggers,
combat, and state-based actions. Two simple-AI decks of REAL cards play to a win.

Nothing about specific cards is hardcoded: a card is its data (mana cost, types, P/T) from the oracle
corpus plus its grounded facts (keywords, abilities, effects) from datalog/cards.dl. The engine only
knows the rules-grounded vocabulary — one handler per verb, basic-land mana per §305.6, the turn
structure per §5. Cards whose text wasn't interpreted just act as their vanilla characteristics.

The engine executes the grounded verbs in HANDLED_VERBS (one branch in _do each); every other grounded
verb still loads as data but resolves to a no-op. `engine.py audit` reports the execution coverage —
the share of all grounded effect instances the engine actually runs — as the honest counterpart to
coverage.py's interpretation %.

Run: python3 build_cards.py && python3 engine.py        # play the demo game
     python3 engine.py audit                            # report execution coverage
     python3 test_engine.py                             # regression-check the verb handlers
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass, field

from mtg import _corpus as card_corpus                  # the oracle-corpus artifact reader (no interpreter import)
from mtg._text import slug                              # name->id contract (was interpreter.slug)
from mtg import sim

# basic-land subtype -> color it taps for (§305.6) — basic lands have no oracle text to interpret.
_BASIC = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}
_COLOR = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G", "colorless": "C"}
_SYM = re.compile(r"\{([^}]+)\}")

# the grounded effect verbs the engine actually EXECUTES (one branch in _do each). Everything the
# interpreter grounds beyond this set still loads as data but resolves as a no-op — audit() reports
# the gap so execution coverage is measured, not assumed.
# the verbs executed by the inline branches in Game._do. ADDITIONAL verbs are contributed by the
# pluggable handlers under engine_handlers/ (auto-discovered at import; one file per verb-group, so
# parallel work never collides). HANDLED_VERBS — the full executed set, used by audit() — is the union,
# computed at the bottom of this module once the registry has loaded.
_INLINE_VERBS = frozenset({
    "add_mana", "deal_damage", "destroy", "draw", "gain_life", "lose_life", "modify_pt", "put_counter",
    "exile", "gain_control", "sacrifice", "tap", "untap", "mill", "discard", "create",
    "grant_keyword", "return_to_hand", "return_to_battlefield", "fight",
})

# the §702 keyword roster — lets grant_keyword pick the keyword out of whichever slot holds it
# (the other slot carries the duration, e.g. "until_end_of_turn").
_KEYWORDS = ground.keyword_abilities()

# permanent-type words that appear in target specs -> the Card.types token they select.
_PERM_TYPES = {"creature": "Creature", "land": "Land", "artifact": "Artifact",
               "enchantment": "Enchantment", "planeswalker": "Planeswalker", "battle": "Battle"}


def parse_cost(mana_cost: str | None) -> Counter:
    """'{3}{W}{W}' -> Counter(generic=3, W=2). Hybrid/Phyrexian simplified to generic (documented)."""
    out = Counter()
    for sym in _SYM.findall(mana_cost or ""):
        if sym.isdigit():
            out["generic"] += int(sym)
        elif sym in ("W", "U", "B", "R", "G", "C"):
            out[sym] += 1
        else:
            out["generic"] += 1          # hybrid {W/U}, phyrexian {W/P}, {X}=0 — simplified
    return out


@dataclass
class Card:
    name: str
    cost: Counter
    types: set
    subtypes: set
    power: int | None
    toughness: int | None
    keywords: set = field(default_factory=set)
    abilities: list = field(default_factory=list)   # dicts: {kind,cost,trigger,effects:[(v,a,t,x,c)]}
    mana: dict = field(default_factory=dict)         # activated mana: cost_str -> [colors]

    @property
    def is_land(self):
        return "Land" in self.types

    @property
    def is_permanent(self):
        return bool({"Creature", "Land", "Artifact", "Enchantment", "Planeswalker", "Battle"} & self.types)

    def taps_for(self) -> list[str]:
        """Colors this card produces when tapped (basic-land intrinsic + interpreted mana abilities)."""
        cols = [_BASIC[s] for s in self.subtypes if s in _BASIC]
        for produced in self.mana.values():
            for p in produced:
                cols.append(_COLOR.get(p, "C") if p in _COLOR else ("ANY" if "any" in p else "C"))
        return cols


def load_deck_cards() -> dict:
    """All cards that appear in the demo decks, as Card objects (corpus data + cards.dl facts)."""
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    db = sim.load_db()
    cards = {}
    for name, c in corpus.items():
        cid = slug(name)
        facts = db.get(cid, {})
        cards[name] = Card(
            name=name, cost=parse_cost(c.get("manaCost")),
            types=set(c.get("types") or []), subtypes=set(c.get("subtypes") or []),
            power=int(c["power"]) if str(c.get("power") or "").lstrip("-").isdigit() else None,
            toughness=int(c["toughness"]) if str(c.get("toughness") or "").lstrip("-").isdigit() else None,
            keywords=set(facts.get("keywords", set())),
            abilities=list(facts.get("abilities", {}).values()),
            mana=dict(facts.get("mana", {})),
        )
    return cards


@dataclass(eq=False)            # identity equality — two identical Islands are distinct objects
class Perm:
    card: Card
    ctrl: int
    tapped: bool = False
    sick: bool = True            # summoning sickness
    boost: tuple = (0, 0)        # until-end-of-turn P/T
    counters: int = 0            # +1/+1 counters
    dmg: int = 0
    granted: set = field(default_factory=set)        # keywords granted for the rest of the game
    granted_eot: set = field(default_factory=set)    # keywords granted until end of turn
    set_pt: tuple | None = None                      # base-P/T override (§613.3 'has base P/T N/N'); None = printed
    flags: set = field(default_factory=set)          # generic boolean states set by handlers and read
    # by the shared loops: 'cant_attack'/'cant_be_blocked'/'cant_block' (combat), 'doesnt_untap' (untap),
    # 'regen_shield' (replaces the next lethal with a tap+heal). Lets new static-modifier handlers live in
    # their own engine_handlers/ file and just SET a flag — the enforcement is pre-wired below.

    @property
    def power(self):
        base = self.set_pt[0] if self.set_pt else (self.card.power or 0)
        return base + self.boost[0] + self.counters

    @property
    def toughness(self):
        base = self.set_pt[1] if self.set_pt else (self.card.toughness or 0)
        return base + self.boost[1] + self.counters

    def has(self, kw):
        return kw in self.card.keywords or kw in self.granted or kw in self.granted_eot


@dataclass
class Player:
    name: str
    life: int = 20
    hand: list = field(default_factory=list)
    library: list = field(default_factory=list)
    bf: list = field(default_factory=list)        # battlefield Perms
    grave: list = field(default_factory=list)
    exile: list = field(default_factory=list)     # exiled cards (§406) — out of the game
    pool: Counter = field(default_factory=Counter)
    lands_played: int = 0
    prevent: int = 0                              # damage-prevention shield (next N damage to this player)
    resources: Counter = field(default_factory=Counter)  # generic resource counters (energy, etc.)

    def creatures(self):
        return [p for p in self.bf if "Creature" in p.card.types]

    def mana_sources(self):
        return [p for p in self.bf if not p.tapped and p.card.taps_for()]


class Game:
    def __init__(self, deckA, deckB, seed=0):
        rng = random.Random(seed)
        self.p = [Player("Alice"), Player("Bob")]
        for pl, deck in zip(self.p, (deckA, deckB)):
            pl.library = list(deck)
            rng.shuffle(pl.library)
            pl.hand = [pl.library.pop() for _ in range(7)]
        self.active = 0
        self.turn = 0
        self.rng = rng
        self.over = False

    # ---- logging -----------------------------------------------------------------------------------
    def log(self, msg, ind=1):
        print("  " * ind + msg)

    # ---- mana --------------------------------------------------------------------------------------
    def _can_pay(self, pl: Player, cost: Counter) -> bool:
        return self._assign(pl, cost) is not None

    def _assign(self, pl: Player, cost: Counter):
        """Greedy: assign untapped mana sources to a cost. Returns the list of sources to tap, or None.
        Colored requirements first (rarest capability), then generic from whatever's left."""
        srcs = [(p, set(p.card.taps_for())) for p in pl.mana_sources()]
        used = []
        for color in ("W", "U", "B", "R", "G", "C"):
            for _ in range(cost.get(color, 0)):
                cand = [s for s in srcs if s not in used and (color in s[1] or "ANY" in s[1])]
                if not cand:
                    return None
                cand.sort(key=lambda s: len(s[1]))
                used.append(cand[0])
        for _ in range(cost.get("generic", 0)):
            cand = [s for s in srcs if s not in used]
            if not cand:
                return None
            cand.sort(key=lambda s: len(s[1]), reverse=True)
            used.append(cand[0])
        return [s[0] for s in used]

    def _pay(self, pl: Player, cost: Counter) -> bool:
        srcs = self._assign(pl, cost)
        if srcs is None:
            return False
        for p in srcs:
            p.tapped = True
        return True

    # ---- casting & the stack -----------------------------------------------------------------------
    def cast(self, pl: Player, card: Card):
        opp = self.p[1 - self.p.index(pl)]
        if not self._pay(pl, card.cost):
            return False
        pl.hand.remove(card)
        self.log(f"{pl.name} casts {card.name}")
        if card.is_permanent:
            perm = Perm(card, self.p.index(pl), sick=True)
            pl.bf.append(perm)
            self._etb(pl, opp, perm)
        else:                                     # instant/sorcery — resolve then graveyard
            self._resolve_spell(pl, opp, card)
            pl.grave.append(card)
        self.sba()
        return True

    def play_land(self, pl: Player, card: Card):
        pl.hand.remove(card)
        pl.bf.append(Perm(card, self.p.index(pl), sick=False))
        pl.lands_played += 1
        self.log(f"{pl.name} plays {card.name}")

    def _etb(self, pl, opp, perm):
        for ab in perm.card.abilities:
            if ab["kind"] == "triggered" and ab.get("trigger", "").startswith("enters"):
                self.log(f"{perm.card.name} ETB triggers", 2)
                self._run_effects(pl, opp, ab["effects"], source=perm)

    def _resolve_spell(self, pl, opp, card):
        for ab in card.abilities:
            if ab["kind"] == "spell":
                self._run_effects(pl, opp, ab["effects"], source=None)

    # ---- effect execution (heuristic targeting) ----------------------------------------------------
    def _run_effects(self, pl, opp, effects, source):
        did = True
        for _seq, verb, amt, tgt, extra, cond in sorted(effects):
            if cond == "if_you_did" and not did:
                continue
            did = True
            self._do(pl, opp, verb, amt, tgt, extra, source)

    def _pick_enemy_creature(self, opp):
        cs = opp.creatures()
        return max(cs, key=lambda p: p.power) if cs else None

    def _players(self, pl, opp, tgt, default):
        """The player(s) a player-scoped target hits. `default` is the verb's natural subject when
        tgt names no player (you draw; a targeted player ≈ the opponent in a 2-player game). Handles
        each_player (both), each_opponent/each_other_player (the opponent), you, and target/that_player."""
        if tgt in ("you", "yourself"):
            return [pl]
        if "each" in tgt or "all_player" in tgt or "both_player" in tgt:
            return [opp] if ("opponent" in tgt or "other" in tgt) else [pl, opp]
        if "opponent" in tgt or tgt.startswith(("target_player", "that_player")):
            return [opp]
        return default

    def _mass(self, tgt):
        """Whether a target is plural (all/each/creatures/them …) rather than a single pick."""
        return tgt.startswith(("all", "each", "creatures", "those", "every")) or \
            "_creatures" in tgt or "any_number" in tgt or tgt in ("them", "they")

    def _targets(self, who, tgt, pred=None):
        """The creatures a creature-scoped target hits: every matching creature of `who` for a mass
        spec, else the single strongest. `pred` filters candidates (e.g. only untapped)."""
        cs = [c for c in who.creatures() if pred is None or pred(c)]
        if not cs:
            return []
        return list(cs) if self._mass(tgt) else [max(cs, key=lambda p: p.power)]

    def _owner_side(self, pl, opp, tgt, default):
        """The side a target spec names by ownership: '…you_control' -> you; 'opponent'/'you_don_t_control'
        -> the opponent; otherwise `default` (side-neutral specs like 'target_creature')."""
        yours = "you_control" in tgt or tgt.endswith("_you_control")
        foes = "opponent" in tgt or "you_don_t_control" in tgt
        if yours and not foes:
            return pl
        if foes and not yours:
            return opp
        return default

    def _perm_targets(self, pl, opp, tgt, pred=None):
        """Permanents hit by a permanent-scoped target spec, faithful to its type, ownership, and
        plurality:
          - type words (creature/land/artifact/…) filter by Card.types; none/'permanent' -> any permanent
          - ownership via _owner_side; a side-neutral mass spec hits both sides, a side-neutral single
            spec hits the opponent's strongest match (the removal heuristic)
          - a mass spec (all/each/…) hits every match; a single spec hits one."""
        types = {_PERM_TYPES[w] for w in tgt.split("_") if w in _PERM_TYPES}

        def ok(p):
            return p.card.is_permanent and (not types or p.card.types & types) and (pred is None or pred(p))

        side = self._owner_side(pl, opp, tgt, None)
        if self._mass(tgt):
            sides = [side] if side else [pl, opp]
            return [p for s in sides for p in s.bf if ok(p)]
        cands = [p for p in (side or opp).bf if ok(p)]
        return [max(cands, key=lambda p: p.power)] if cands else []

    def _do(self, pl, opp, verb, amt, tgt, extra, source):
        n = int(amt) if str(amt).lstrip("-").isdigit() else 0
        if verb == "deal_damage":
            if any(w in tgt for w in ("creature", "permanent", "planeswalker")):
                for p in self._perm_targets(pl, opp, tgt, pred=lambda x: "Creature" in x.card.types):
                    p.dmg += n; self.log(f"{n} damage to {p.card.name}", 2)
            if any(w in tgt for w in ("player", "opponent")) or tgt in ("you", "any_target"):
                for who in self._players(pl, opp, tgt, default=[opp]):   # any_target -> face (opp)
                    dealt = self._damage_player(who, n)
                    self.log(f"{dealt} damage to {who.name} (life {who.life})", 2)
        elif verb == "draw":
            for who in self._players(pl, opp, tgt, default=[pl]):
                self._draw(who, n or 1)
        elif verb == "gain_life":
            pl.life += n; self.log(f"{pl.name} gains {n} (life {pl.life})", 2)
        elif verb == "lose_life":
            opp.life -= n; self.log(f"{opp.name} loses {n} (life {opp.life})", 2)
        elif verb == "modify_pt" and self._parse_boost(amt):
            dp, dt = self._parse_boost(amt)
            if source in pl.bf and tgt in ("self", "it", "enchanted_creature", "equipped_creature"):
                hit = [source]                              # the source / the creature it's attached to
            else:                                           # a shrink (negative) reads as enemy removal; a pump as your own
                hit = self._targets(pl if dp + dt >= 0 else opp, tgt)
            for who in hit:
                who.boost = (who.boost[0] + dp, who.boost[1] + dt)
                self.log(f"{who.card.name} gets {amt} (now {who.power}/{who.toughness})", 2)
        elif verb == "put_counter" and self._parse_boost(extra):
            dp, dt = self._parse_boost(extra)               # +1/+1 -> (1,1); -1/-1 -> (-1,-1)
            per = dp if dp == dt else (1 if dp + dt >= 0 else -1)
            if source in pl.bf and tgt in ("self", "it"):
                hit = [source]
            else:                                           # ownership in the spec wins; sign is the fallback
                hit = self._targets(self._owner_side(pl, opp, tgt, pl if per >= 0 else opp), tgt)
            for who in hit:
                who.counters += (n or 1) * per
                self.log(f"{who.card.name} gets a {extra} counter (now {who.power}/{who.toughness})", 2)
            if per < 0:
                self.sba()                                  # -1/-1 counters can be lethal
        elif verb == "destroy":
            for p in self._perm_targets(pl, opp, tgt):      # all_creatures -> board wipe; target_land -> a land; etc.
                self._destroy(p); self.log(f"destroys {p.card.name}", 2)
        elif verb == "add_mana":
            for c in extra.split("_"):
                pl.pool[_COLOR.get(c, "C")] += 1
        elif verb == "exile":
            if any(z in tgt for z in ("graveyard", "hand", "library", "top_of")):
                return                            # zone-internal manipulation, not a board removal
            for p in self._perm_targets(pl, opp, tgt):
                self.p[p.ctrl].bf.remove(p); self.p[p.ctrl].exile.append(p.card)
                self.log(f"exiles {p.card.name}", 2)
        elif verb == "gain_control":
            if tgt not in ("self", "it", "that_card"):     # self/it name the source or a prior object we can't resolve
                for ec in self._perm_targets(pl, opp, tgt):
                    if ec.ctrl == self.p.index(pl):        # already ours -> nothing to gain
                        continue
                    self.p[ec.ctrl].bf.remove(ec)
                    ec.ctrl = self.p.index(pl); ec.sick = True; ec.tapped = False
                    pl.bf.append(ec)
                    self.log(f"{pl.name} gains control of {ec.card.name}", 2)
        elif verb == "sacrifice":
            if tgt in ("it", "self"):                          # the source sacrifices itself (if still here)
                victims = [(pl, source)] if source in pl.bf else []
            else:
                spec = extra if extra not in ("-", "") else tgt   # object type lives in extra ("a_land") or tgt
                types = {_PERM_TYPES[w] for w in spec.split("_") if w in _PERM_TYPES}

                def loseable(p, src=source):
                    return (p.card.is_permanent and (not types or p.card.types & types)
                            and not ("another" in spec and p is src))

                victims = []
                for who in self._players(pl, opp, tgt, default=[pl]):  # each named player sacrifices their weakest match(es)
                    pool = sorted((p for p in who.bf if loseable(p)), key=lambda p: p.power)
                    victims += [(who, v) for v in pool[:max(n, 1)]]
            for who, v in victims:
                if v:
                    who.bf.remove(v); who.grave.append(v.card)
                    self.log(f"{who.name} sacrifices {v.card.name}", 2)
        elif verb == "tap":
            hit = self._targets(opp, tgt, pred=lambda c: not c.tapped)
            for c in hit:
                c.tapped = True
            if hit:
                self.log(f"taps {hit[0].card.name}" if len(hit) == 1
                         else f"taps {len(hit)} of {opp.name}'s creatures", 2)
        elif verb == "untap":
            if tgt == "self" and source:
                source.tapped = False
            else:
                hit = self._targets(pl, tgt, pred=lambda c: c.tapped)
                for c in hit:
                    c.tapped = False
                if hit:
                    self.log(f"untaps {hit[0].card.name}" if len(hit) == 1
                             else f"untaps {len(hit)} creatures", 2)
        elif verb == "mill":
            for who in self._players(pl, opp, tgt, default=[opp]):
                moved = 0
                while moved < (n or 1) and who.library:
                    who.grave.append(who.library.pop()); moved += 1
                if moved:
                    self.log(f"{who.name} mills {moved}", 2)
        elif verb == "discard":
            empties_hand = amt in ("all", "their_hand", "its_hand", "your_hand")
            for who in self._players(pl, opp, tgt, default=[opp]):
                k = len(who.hand) if empties_hand else (n or 1)
                dropped = 0
                for _ in range(k):
                    if who.hand:
                        who.grave.append(who.hand.pop()); dropped += 1
                if dropped:
                    self.log(f"{who.name} discards {dropped} (hand {len(who.hand)})", 2)
        elif verb == "create":
            tok = self._make_token(extra)
            if tok and n > 0:
                for _ in range(n):
                    pl.bf.append(Perm(tok, self.p.index(pl), sick=True))
                self.log(f"{pl.name} creates {n} {tok.name}", 2)
        elif verb == "grant_keyword":
            kw = extra if extra in _KEYWORDS else (amt if amt in _KEYWORDS else None)
            if kw:
                hit = ([source] if (source in pl.bf and tgt in ("it", "self"))
                       else self._targets(pl, tgt))
                bucket = "granted_eot" if amt == "until_end_of_turn" else "granted"
                for who in hit:
                    getattr(who, bucket).add(kw)
                if hit:
                    self.log(f"{hit[0].card.name} gains {kw}" if len(hit) == 1
                             else f"{len(hit)} creatures gain {kw}", 2)
        elif verb == "return_to_hand":
            # a "card" target (vs a "permanent"/"creature") names a graveyard object, not a battlefield one
            if extra == "from_graveyard" or "graveyard" in tgt or tgt.endswith(("_card", "_cards")):
                types = {_PERM_TYPES[w] for w in tgt.split("_") if w in _PERM_TYPES}
                card = next((c for c in reversed(pl.grave) if not types or (c.types & types)), None)
                if card:                                       # match the named type (creature card …), else any card
                    pl.grave.remove(card); pl.hand.append(card)
                    self.log(f"{pl.name} returns {card.name} to hand", 2)
            elif source in pl.bf + opp.bf and tgt in ("it", "self", "that_card"):
                owner = self.p[source.ctrl]
                owner.bf.remove(source); owner.hand.append(source.card)
                self.log(f"{source.card.name} returns to {owner.name}'s hand", 2)
            else:                                                  # bounce the targeted permanent(s) to owner's hand
                for p in self._perm_targets(pl, opp, tgt):
                    owner = self.p[p.ctrl]
                    owner.bf.remove(p); owner.hand.append(p.card)
                    self.log(f"bounces {p.card.name} to {owner.name}'s hand", 2)
        elif verb == "return_to_battlefield":
            if "graveyard" in tgt or "graveyard" in extra:         # reanimation (flicker needs an exile step we don't model)
                card = next((c for c in reversed(pl.grave) if "Creature" in c.types), None)
                if card:
                    pl.grave.remove(card)
                    pl.bf.append(Perm(card, self.p.index(pl), sick=True, tapped=("tapped" in extra)))
                    self.log(f"{pl.name} reanimates {card.name}", 2)
        elif verb == "fight":
            mine = source if (source in pl.bf) else max(pl.creatures(), key=lambda p: p.power, default=None)
            ec = self._pick_enemy_creature(opp)
            if mine and ec:
                self._fight(mine, ec)
                self.log(f"{mine.card.name} fights {ec.card.name}", 2)
                self.sba()
        else:
            # PLUGGABLE handlers (engine_handlers/*.py). A verb with no inline branch above dispatches
            # here; an unregistered verb stays a faithful no-op (loads as data, doesn't act). sba() runs
            # after so a handler that deals damage / destroys settles deaths and wins like the inline ones.
            h = engine_handlers.REGISTRY.get(verb)
            if h:
                h(self, pl, opp, amt, tgt, extra, source, n)
                self.sba()
        # remaining grounded verbs (scry, search, counter, …) are no-ops in this minimal engine

    @staticmethod
    def _parse_boost(amt: str):
        """A '+p/+t' modifier as an (int, int) pair, or None if either side is variable (X/X,
        +1/+0_per_…) — a boost we can't evaluate is abstained on rather than guessed."""
        parts = str(amt).replace("+", "").split("/")
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    def _make_token(self, spec: str) -> Card | None:
        """Build a Card from a 'P_T_color[_and_color]_subtype…_creature' token spec (the §111 token
        descriptor the interpreter emits). Only creature tokens affect this engine's board; everything
        else (Treasure, Food, copies, …) returns None and the create is a no-op."""
        parts = spec.split("_")
        if "creature" not in parts:
            return None
        parts = parts[:parts.index("creature")]          # drop any trailing rider clauses
        nums = [int(x) for x in parts[:2] if x.lstrip("-").isdigit()]
        if len(nums) < 2:
            return None                                  # X/X or malformed P/T — abstain
        rest = parts[2:]
        subs = {x.capitalize() for x in rest if x not in _COLOR and x not in ("and", "artifact")}
        types = {"Creature"} | ({"Artifact"} if "artifact" in rest else set())
        name = (" ".join(sorted(subs)) or "Creature") + " Token"
        return Card(name=name, cost=Counter(), types=types, subtypes=subs,
                    power=nums[0], toughness=nums[1])

    def _draw(self, pl, k):
        for _ in range(k):
            if pl.library:
                pl.hand.append(pl.library.pop())
            else:
                pl.life = -999; self.log(f"{pl.name} draws from empty library — loses", 2)
        self.log(f"{pl.name} draws {k} (hand {len(pl.hand)})", 2)

    def _destroy(self, perm, regenerable=True):
        # §701.15 regeneration replaces a DESTROY (the destroy verb, lethal combat/damage) — but not a
        # 0-toughness death or a sacrifice; sba passes regenerable=False once it has decided the creature
        # truly dies. A no-op unless a handler set the 'regen_shield' flag.
        if regenerable and "regen_shield" in perm.flags:
            perm.flags.discard("regen_shield"); perm.tapped = True; perm.dmg = 0
            self.log(f"{perm.card.name} regenerates", 2)
            return
        self.p[perm.ctrl].bf.remove(perm)
        self.p[perm.ctrl].grave.append(perm.card)

    def _damage_player(self, who, n):
        """Apply n damage to a player through its prevention shield (§615). Returns damage actually dealt
        (so lifelink/feedback reflect the post-prevention amount). A no-op when who.prevent == 0, so the
        demo stays byte-identical until a prevent_damage handler raises a shield."""
        if who.prevent and n > 0:
            p = min(who.prevent, n)
            who.prevent -= p
            n -= p
            if p:
                self.log(f"prevents {p} damage to {who.name}", 2)
        who.life -= n
        return n

    # ---- combat ------------------------------------------------------------------------------------
    def combat(self, pl, opp):
        attackers = [c for c in pl.creatures()
                     if not c.tapped and not c.sick and not c.has("defender") and "cant_attack" not in c.flags]
        if not attackers:
            return
        self.log(f"{pl.name} attacks with {', '.join(a.card.name for a in attackers)}")
        for a in attackers:
            if not a.has("vigilance"):
                a.tapped = True
        blockers = [c for c in opp.creatures() if not c.tapped]
        for a in attackers:
            block = self._choose_block(a, blockers, opp)
            if block:
                blockers.remove(block)
                self.log(f"{opp.name}'s {block.card.name} blocks {a.card.name}", 2)
                self._fight(a, block)
            else:
                dealt = self._damage_player(opp, a.power)
                if a.has("lifelink"):
                    pl.life += dealt
                self.log(f"{a.card.name} hits {opp.name} for {a.power} (life {opp.life})", 2)
        self.sba()

    def _choose_block(self, attacker, blockers, opp):
        # flying can only be blocked by flying/reach; block to kill if possible, else chump if lethal
        if "cant_be_blocked" in attacker.flags:
            return None
        legal = [b for b in blockers if "cant_block" not in b.flags
                 and (not attacker.has("flying") or b.has("flying") or b.has("reach"))]
        kill = [b for b in legal if b.power >= attacker.toughness]
        if kill:
            return min(kill, key=lambda b: b.toughness)
        if opp.life <= attacker.power and legal:
            return max(legal, key=lambda b: b.toughness)   # chump to survive
        return None

    def _fight(self, a, b):
        b.dmg += a.power
        a.dmg += b.power
        if a.has("deathtouch") and a.power > 0:
            b.dmg = max(b.dmg, b.toughness)
        if b.has("deathtouch") and b.power > 0:
            a.dmg = max(a.dmg, a.toughness)

    # ---- state-based actions -----------------------------------------------------------------------
    def sba(self):
        for pl in self.p:
            for perm in list(pl.bf):
                if "Creature" in perm.card.types and (perm.toughness <= 0 or perm.dmg >= perm.toughness):
                    if perm.toughness > 0 and "regen_shield" in perm.flags:   # lethal DAMAGE -> regenerate
                        perm.flags.discard("regen_shield"); perm.tapped = True; perm.dmg = 0
                        self.log(f"{perm.card.name} regenerates", 2)
                        continue
                    self.log(f"{perm.card.name} dies", 2)
                    self._destroy(perm, regenerable=False)   # already decided it dies (0-toughness or chose not to regen)
        for i, pl in enumerate(self.p):
            if pl.life <= 0 and not self.over:
                self.over = True
                self.winner = self.p[1 - i].name

    # ---- AI turn -----------------------------------------------------------------------------------
    def take_turn(self):
        pl = self.p[self.active]
        opp = self.p[1 - self.active]
        self.turn += 1
        self.log(f"=== Turn {self.turn}: {pl.name} (life {pl.life} / {opp.name} {opp.life}) ===", 0)
        for perm in pl.bf:                                   # untap
            if "doesnt_untap" not in perm.flags:
                perm.tapped = False
            perm.sick = False
        pl.lands_played = 0
        pl.pool.clear()
        if self.turn > 1:
            self._draw(pl, 1)
        self._main(pl, opp)                                  # main 1
        self.combat(pl, opp)                                 # combat
        if not self.over:
            self._main(pl, opp)                              # main 2 (cast leftover)
        for perm in pl.bf:                                   # cleanup: end-of-turn boosts/grants wear off
            perm.boost = (0, 0)
            perm.dmg = 0
            perm.granted_eot.clear()
        self.active = 1 - self.active

    def _main(self, pl, opp):
        # play a land (prefer one that adds a color we lack)
        lands = [c for c in pl.hand if c.is_land]
        if lands and pl.lands_played == 0:
            self.play_land(pl, lands[0])
        # cast the most expensive affordable spell, repeatedly; creatures/permanents preferred
        progress = True
        while progress and not self.over:
            progress = False
            playable = [c for c in pl.hand if not c.is_land and self._can_pay(pl, c.cost)]
            playable.sort(key=lambda c: (c.is_permanent, sum(c.cost.values())), reverse=True)
            if playable:
                self.cast(pl, playable[0])
                progress = True

    def run(self, max_turns=40):
        while not self.over and self.turn < max_turns:
            self.take_turn()
        print()
        if self.over:
            print(f"*** {self.winner} wins on turn {self.turn} "
                  f"(Alice {self.p[0].life}, Bob {self.p[1].life}) ***")
        else:
            print(f"*** turn limit — Alice {self.p[0].life}, Bob {self.p[1].life} ***")


def _deck(cards, spec):
    return [cards[name] for name, n in spec for _ in range(n)]


def audit():
    """Execution coverage: of every grounded effect instance across the whole oracle, how many does
    the engine actually execute vs no-op? This is the EXECUTION counterpart to coverage.py's
    INTERPRETATION %, and the honest measure of the interpretation→execution gap."""
    db = sim.load_db()
    seen, run = Counter(), Counter()
    for facts in db.values():
        for ab in facts.get("abilities", {}).values():
            for _seq, verb, *_ in ab.get("effects", []):
                seen[verb] += 1
                if verb in HANDLED_VERBS:
                    run[verb] += 1
    total, executed = sum(seen.values()), sum(run.values())
    handled = sorted(HANDLED_VERBS & set(seen))
    print(f"execution coverage: {executed}/{total} effect instances ({100 * executed / total:.1f}%)")
    print(f"  {len(handled)} verbs executed, {len(set(seen) - HANDLED_VERBS)} distinct verbs still no-op")
    print("  top no-op verbs (interpreted but not executed):")
    for v, c in seen.most_common():
        if v not in HANDLED_VERBS and c >= 100:
            print(f"    {v:24} {c}")


def demo():
    cards = load_deck_cards()
    # mono-color real-card decks (clean mana). Green ground beatdown vs blue flyers + burn-free control:
    # exercises mana dorks, combat with blocking, flying evasion (green can't block flyers), and a pump.
    green = _deck(cards, [("Forest", 12), ("Llanowar Elves", 4), ("Elvish Mystic", 3),
                          ("Grizzly Bears", 6), ("Craw Wurm", 4), ("Giant Growth", 4)])
    blue = _deck(cards, [("Island", 13), ("Storm Crow", 4), ("Wind Drake", 5),
                         ("Coral Eel", 4), ("Bay Falcon", 4)])
    print("Engine demo — green ground vs blue flyers, a full game from grounded facts:\n")
    Game(green, blue, seed=4).run()


# Load the pluggable verb handlers AFTER every class/constant/helper above is defined, so the handler
# modules can `from engine import Perm, Card, _COLOR, …` without a circular import. HANDLED_VERBS is the
# union of the inline branches and everything the registry contributed.
import engine_handlers                            # noqa: E402  (intentionally late — see above)

engine_handlers.load()
HANDLED_VERBS = _INLINE_VERBS | frozenset(engine_handlers.REGISTRY)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "audit":
        audit()
    else:
        demo()
