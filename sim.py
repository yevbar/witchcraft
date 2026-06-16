"""sim.py — a small, reviewable Python shim that EXECUTES real cards from the grounded facts.

The point: the facts emitted by transpile_card (ability / effect / printed_keyword / mana_ability …)
are a real, executable IR. This shim loads them from datalog/cards.dl and runs a tiny game state — no
card logic is hardcoded here, only a handler per GROUNDED verb (the same closed vocabulary the eventual
C++ engine will switch on). If a card's text wasn't interpreted, the card simply has no facts to run
(faithful: we never fake behavior).

Run: python3 build_cards.py && python3 sim.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_DL = Path(__file__).parent / "datalog" / "cards.dl"
_ROW = re.compile(r'^(\w+)\((.*)\)\.$')


def _args(s: str):
    return [a.strip().strip('"') for a in re.findall(r'"[^"]*"|[^,]+', s)]


def load_db():
    """Parse cards.dl into {cid: {keywords, mana:[(cost,colors)], abilities:{aid:{kind,cost,trigger,
    effects:[(seq,verb,amount,target)]}}}}. The sim consumes only the relations it executes."""
    db: dict = {}
    for line in _DL.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        rel, a = m.group(1), _args(m.group(2))
        if rel == "name":
            db.setdefault(a[0], {}).update(name=a[1])
        elif rel == "printed_keyword":
            db.setdefault(a[0], {}).setdefault("keywords", set()).add(a[1])
        elif rel == "mana_ability":
            db.setdefault(a[0], {}).setdefault("mana", {}).setdefault(a[1], [])
        elif rel == "adds_mana":
            db.setdefault(a[0], {}).setdefault("mana", {}).setdefault(a[1], []).append(a[2])
        elif rel == "card_ability":
            db.setdefault(a[0], {}).setdefault("abilities", {})[a[1]] = {"kind": a[2], "effects": []}
        elif rel == "ability_cost":
            db[a[0]]["abilities"][a[1]]["cost"] = a[2]
        elif rel == "ability_trigger":
            db[a[0]]["abilities"][a[1]]["trigger"] = a[2]
        elif rel == "class_level":                           # §717 a Class's '{cost}: Level N' level-up step
            db.setdefault(a[0], {}).setdefault("class_levels", []).append((a[1], a[2]))  # (cost, level)
        elif rel == "card_effect":
            extra = a[6] if len(a) > 6 else "-"
            cond = a[7] if len(a) > 7 else "-"
            db.setdefault(a[0], {}).setdefault("abilities", {}).setdefault(
                a[1], {"kind": "spell", "effects": []})["effects"].append(
                    (int(a[2]), a[3], a[4], a[5], extra, cond))
        elif rel == "card_escape_generic":                   # §702.166 escape mana cost (generic portion)
            db.setdefault(a[0], {}).setdefault("escape", {})["generic"] = int(a[1])
        elif rel == "card_escape_pip":                       # escape mana cost (a colored pip)
            db.setdefault(a[0], {}).setdefault("escape", {}).setdefault("pips", {})[a[1]] = int(a[2])
        elif rel == "card_escape_exile":                     # escape additional cost: exile N other GY cards
            db.setdefault(a[0], {}).setdefault("escape", {})["exile"] = int(a[1])
        elif rel == "modal":                                 # §700.2 modal spell: choose `count` mode(s)
            db.setdefault(a[0], {})["modal"] = a[1]
        elif rel == "mode_option":                           # one offered mode; its effects live in abilities[mode]
            db.setdefault(a[0], {}).setdefault("modes", []).append(a[1])
        elif rel == "static":                                # §700.2 descriptive static slugs (e.g. the modal
            db.setdefault(a[0], {}).setdefault("statics", []).append(a[1])   # 'choose both if commander' rider)
        elif rel == "static_player":                         # §604 continuous PLAYER permission (extra lands, skip untap, …)
            db.setdefault(a[0], {}).setdefault("static_player", set()).add(a[1])
    return db


@dataclass
class Permanent:
    name: str
    power: int = 0
    toughness: int = 0
    tapped: bool = False
    boost: tuple = (0, 0)            # until-end-of-turn P/T modifier

    @property
    def pt(self):
        return self.power + self.boost[0], self.toughness + self.boost[1]


@dataclass
class Player:
    name: str
    life: int = 20
    hand: list = field(default_factory=list)
    library: list = field(default_factory=list)
    battlefield: list = field(default_factory=list)
    pool: list = field(default_factory=list)


class Game:
    def __init__(self):
        self.p = [Player("Alice"), Player("Bob")]
        self.db = load_db()
        self.log: list[str] = []

    def _say(self, msg):
        self.log.append(msg)
        print("   " + msg)

    # --- the grounded-verb handlers: one per rules-defined action -----------------------------------
    def apply(self, verb, amount, target, extra="-", *, me, opp, tgt_perm=None, tgt_player=None):
        n = int(amount) if str(amount).lstrip("-").isdigit() else amount
        if verb == "deal_damage":
            if tgt_perm:
                tgt_perm.boost = (tgt_perm.boost[0], tgt_perm.boost[1] - n)
                self._say(f"deal {n} to {tgt_perm.name} (now {tgt_perm.pt[0]}/{tgt_perm.pt[1]})")
                self._check_death(tgt_perm)
            else:
                pl = tgt_player or opp
                pl.life -= n
                self._say(f"deal {n} damage to {pl.name} (life {pl.life})")
        elif verb == "draw":
            pl = tgt_player or me
            for _ in range(n if isinstance(n, int) else 1):
                pl.hand.append(pl.library.pop(0) if pl.library else "(empty)")
            self._say(f"{pl.name} draws {n} (hand: {len(pl.hand)})")
        elif verb == "gain_life":
            pl = tgt_player or me
            pl.life += n
            self._say(f"{pl.name} gains {n} life (life {pl.life})")
        elif verb == "lose_life":
            pl = tgt_player or me
            pl.life -= n
            self._say(f"{pl.name} loses {n} life (life {pl.life})")
        elif verb == "modify_pt":
            dp, dt = (int(x) for x in amount.replace("+", " ").replace("/", " ").split())
            tgt_perm.boost = (tgt_perm.boost[0] + dp, tgt_perm.boost[1] + dt)
            self._say(f"{tgt_perm.name} gets {amount} (now {tgt_perm.pt[0]}/{tgt_perm.pt[1]})")
        elif verb == "destroy":
            self._destroy(tgt_perm, opp)
        elif verb in ("tap", "untap"):
            tgt_perm.tapped = (verb == "tap")
            self._say(f"{verb} {tgt_perm.name}")
        elif verb == "add_mana":
            colors = extra.split("_") * (n if isinstance(n, int) else 1)
            me.pool.extend(colors)
            self._say(f"add {extra} mana (pool: {me.pool})")
        elif verb == "put_counter":
            if tgt_perm and extra.count("/") == 1:
                dp, dt = (int(x) for x in extra.replace("+", " ").split("/"))
                tgt_perm.power += dp
                tgt_perm.toughness += dt
                self._say(f"put {extra} counter on {tgt_perm.name} (now {tgt_perm.pt[0]}/{tgt_perm.pt[1]})")
            else:
                self._say(f"put {amount} {extra} counter(s) on {target}")
        elif verb == "create":
            self._say(f"create {amount} {extra} token(s)")
        elif verb == "discard":
            pl = tgt_player or me
            for _ in range(n if isinstance(n, int) else 1):
                if pl.hand:
                    pl.hand.pop()
            self._say(f"{pl.name} discards {n} (hand: {len(pl.hand)})")
        elif verb == "shuffle":
            self._say(f"{me.name} shuffles library")
        elif verb == "grant_keyword":
            self._say(f"{tgt_perm.name if tgt_perm else target} gains {extra} (until end of turn)")
        else:
            self._say(f"[unhandled grounded verb: {verb}]")

    def _check_death(self, perm):
        if perm.pt[1] <= 0:
            self._say(f"{perm.name} has 0 toughness — dies")
            for pl in self.p:
                if perm in pl.battlefield:
                    pl.battlefield.remove(perm)

    def _destroy(self, perm, owner):
        self._say(f"destroy {perm.name}")
        for pl in self.p:
            if perm in pl.battlefield:
                pl.battlefield.remove(perm)

    # --- playing real cards from their facts --------------------------------------------------------
    def cast_spell(self, cid, *, me, opp, tgt_perm=None, tgt_player=None):
        card = self.db.get(cid, {})
        ab = next((v for v in card.get("abilities", {}).values() if v["kind"] == "spell"), None)
        self._say(f"cast {card.get('name', cid)}")
        if not ab:
            self._say("(no interpreted spell ability — abstained)")
            return
        did_optional = True
        for _seq, verb, amt, tgt, extra, cond in sorted(ab["effects"]):
            if cond == "may":
                did_optional = True   # the shim always takes optional riders; an AI would choose
                self._say(f"(optional) you may {verb}")
            if cond == "if_you_did" and not did_optional:
                continue
            self.apply(verb, amt, tgt, extra, me=me, opp=opp, tgt_perm=tgt_perm, tgt_player=tgt_player)

    def tap_for_mana(self, perm, cid, *, me):
        mana = self.db.get(cid, {}).get("mana", {})
        cost, colors = next(iter(mana.items()))
        perm.tapped = True
        me.pool.extend(colors)
        self._say(f"tap {perm.name} for {colors} (pool: {me.pool})")


def demo():
    g = Game()
    a, b = g.p
    a.library = ["Forest", "Island", "Mountain", "Swamp"]
    print("Scenario — real cards executed from grounded facts:\n")

    print(">> Alice's Llanowar Elves taps for mana")
    elves = Permanent("Llanowar Elves", 1, 1, tapped=False)
    a.battlefield.append(elves)
    g.tap_for_mana(elves, "llanowar_elves", me=a)

    print(">> Bob has a Grizzly Bears (2/2); Alice casts Giant Growth on it? no — on her own, then Lightning Bolt Bob's")
    bears = Permanent("Grizzly Bears", 2, 2)
    b.battlefield.append(bears)
    print(">> Alice casts Lightning Bolt at Bob's Grizzly Bears")
    g.cast_spell("lightning_bolt", me=a, opp=b, tgt_perm=bears)

    print(">> Alice casts Lightning Bolt at Bob's face")
    g.cast_spell("lightning_bolt", me=a, opp=b)

    print(">> Alice casts Divination (draw two)")
    g.cast_spell("divination", me=a, opp=b)

    print(">> Alice casts Giant Growth on her Llanowar Elves (+3/+3)")
    g.cast_spell("giant_growth", me=a, opp=b, tgt_perm=elves)

    print(">> Alice casts Healing Salve (modal — the 'gain 3 life' mode)")
    g.cast_spell("healing_salve", me=a, opp=b, tgt_player=a)

    print(f"\nFinal — Alice life {a.life} hand {len(a.hand)}; Bob life {b.life}; "
          f"Alice board {[f'{p.name} {p.pt[0]}/{p.pt[1]}' for p in a.battlefield]}; "
          f"Bob board {[p.name for p in b.battlefield]}")


if __name__ == "__main__":
    demo()
