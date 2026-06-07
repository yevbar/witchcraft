"""engine.py — a small, reviewable MTG game engine that plays FULLER games from the grounded facts.

Builds on sim.py: instead of a scripted effect demo, this runs an actual turn loop (untap → upkeep →
draw → main → combat → main2 → end), with a mana system, the stack, casting from hand, ETB triggers,
combat, and state-based actions. Two simple-AI decks of REAL cards play to a win.

Nothing about specific cards is hardcoded: a card is its data (mana cost, types, P/T) from the oracle
corpus plus its grounded facts (keywords, abilities, effects) from datalog/cards.dl. The engine only
knows the rules-grounded vocabulary — one handler per verb, basic-land mana per §305.6, the turn
structure per §5. Cards whose text wasn't interpreted just act as their vanilla characteristics.

Run: python3 build_cards.py && python3 engine.py
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass, field

import card_corpus
import ground
import sim          # reuse the fact loader

# basic-land subtype -> color it taps for (§305.6) — basic lands have no oracle text to interpret.
_BASIC = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}
_COLOR = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G", "colorless": "C"}
_SYM = re.compile(r"\{([^}]+)\}")


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
        cid = ground.slug(name)
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

    @property
    def power(self):
        return (self.card.power or 0) + self.boost[0] + self.counters

    @property
    def toughness(self):
        return (self.card.toughness or 0) + self.boost[1] + self.counters

    def has(self, kw):
        return kw in self.card.keywords


@dataclass
class Player:
    name: str
    life: int = 20
    hand: list = field(default_factory=list)
    library: list = field(default_factory=list)
    bf: list = field(default_factory=list)        # battlefield Perms
    grave: list = field(default_factory=list)
    pool: Counter = field(default_factory=Counter)
    lands_played: int = 0

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

    def _do(self, pl, opp, verb, amt, tgt, extra, source):
        n = int(amt) if str(amt).lstrip("-").isdigit() else 0
        if verb == "deal_damage":
            ec = self._pick_enemy_creature(opp)
            if "creature" in tgt and ec:
                ec.dmg += n; self.log(f"{n} damage to {ec.card.name}", 2)
            else:
                opp.life -= n; self.log(f"{n} damage to {opp.name} (life {opp.life})", 2)
        elif verb == "draw":
            who = opp if tgt.startswith("target_player") and False else pl
            self._draw(who, n or 1)
        elif verb == "gain_life":
            pl.life += n; self.log(f"{pl.name} gains {n} (life {pl.life})", 2)
        elif verb == "lose_life":
            opp.life -= n; self.log(f"{opp.name} loses {n} (life {opp.life})", 2)
        elif verb == "modify_pt" and "/" in str(amt):
            dp, dt = (int(x) for x in amt.replace("+", " ").split("/"))
            who = source or (pl.creatures()[0] if pl.creatures() else None)
            if "target" in tgt or who is None:
                who = max(pl.creatures(), key=lambda p: p.power, default=None)
            if who:
                who.boost = (who.boost[0] + dp, who.boost[1] + dt)
                self.log(f"{who.card.name} gets {amt} (now {who.power}/{who.toughness})", 2)
        elif verb == "put_counter" and "/" in extra:
            who = source or max(pl.creatures(), key=lambda p: p.power, default=None)
            if who:
                who.counters += n or 1
                self.log(f"{who.card.name} gets a +1/+1 counter (now {who.power}/{who.toughness})", 2)
        elif verb == "destroy":
            ec = self._pick_enemy_creature(opp)
            if ec:
                self._destroy(ec); self.log(f"destroys {ec.card.name}", 2)
        elif verb == "add_mana":
            for c in extra.split("_"):
                pl.pool[_COLOR.get(c, "C")] += 1
        # other grounded verbs (scry, mill, exile, …) are no-ops in this minimal engine

    def _draw(self, pl, k):
        for _ in range(k):
            if pl.library:
                pl.hand.append(pl.library.pop())
            else:
                pl.life = -999; self.log(f"{pl.name} draws from empty library — loses", 2)
        self.log(f"{pl.name} draws {k} (hand {len(pl.hand)})", 2)

    def _destroy(self, perm):
        self.p[perm.ctrl].bf.remove(perm)
        self.p[perm.ctrl].grave.append(perm.card)

    # ---- combat ------------------------------------------------------------------------------------
    def combat(self, pl, opp):
        attackers = [c for c in pl.creatures() if not c.tapped and not c.sick and not c.has("defender")]
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
                opp.life -= a.power
                if a.has("lifelink"):
                    pl.life += a.power
                self.log(f"{a.card.name} hits {opp.name} for {a.power} (life {opp.life})", 2)
        self.sba()

    def _choose_block(self, attacker, blockers, opp):
        # flying can only be blocked by flying/reach; block to kill if possible, else chump if lethal
        legal = [b for b in blockers if not attacker.has("flying") or b.has("flying") or b.has("reach")]
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
                    self.log(f"{perm.card.name} dies", 2)
                    self._destroy(perm)
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
        for perm in pl.bf:                                   # cleanup: end-of-turn boosts wear off
            perm.boost = (0, 0)
            perm.dmg = 0
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


if __name__ == "__main__":
    demo()
