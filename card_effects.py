"""card_effects.py — parse a single oracle EFFECT sentence into a grounded, simulation-ready tuple.

Shared by every ability kind (spell / activated / triggered): the same "deal N damage to TARGET" is
one effect whether it's an instant or a creature's ability. An effect is (verb, amount, target):
  verb    a grounded action (ground.effect_verbs — §701 keyword actions + verified core actions)
  amount  an int, a "+x/+y" boost, or "-" when not applicable
  target  a normalized target spec (any_target, target_creature, all_creatures, you, each_player, …)

Curated, anchored templates — faithful or abstain (returns None), never a lossy guess. This mirrors
the rules-side content builders: a frame we don't recognize cleanly stays uncovered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import ground

_NUMWORD = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "x": "X"}

# a target noun phrase the templates share. Order matters (longest first inside the alternation).
_TGT = (r"(?:any target|target [\w ]+?|each [\w ]+?|all [\w ]+?|\w+ you control|"
        r"enchanted \w+|equipped \w+|that \w+|~|it|you|its controller|its owner|their controller)")


def _amount(s: str):
    s = s.strip().lower()
    if s in _NUMWORD:
        return _NUMWORD[s]
    if s.isdigit():
        return int(s)
    return None


_SYM_RE = re.compile(r"\{[^}]+\}")


def _mana_production(what: str):
    """Parse the object of 'Add …' into a list of GROUNDED mana descriptors, or None if it isn't a
    clean mana production. Each is a rules color (§105/§107.4), 'colorless', 'any_color', or
    'any_one_color'; a choice 'X or Y' becomes 'X_or_Y'. Conditional/variable amounts abstain."""
    w = what.strip().rstrip(".").strip()
    sc = ground.symbol_color()
    syms = _SYM_RE.findall(w)
    if syms and _SYM_RE.sub("", w).strip() == "":
        out = []
        for s in syms:
            if s in sc:
                out.append(sc[s])
            elif s == "{C}":
                out.append("colorless")
            else:
                return None
        return out
    if " or " in w and syms:
        choices = [sc.get(s) or ("colorless" if s == "{C}" else None) for s in syms]
        return ["_or_".join(choices)] if all(choices) else None
    m = re.fullmatch(r"(one|two|three|four|five|six) mana of any color", w, re.I)
    if m:
        return ["any_color"] * _NUMWORD[m.group(1).lower()]
    m = re.fullmatch(r"(one|two|three|four|five|six) mana of any one color", w, re.I)
    if m:
        return ["any_one_color"] * _NUMWORD[m.group(1).lower()]
    return None


def _target(s: str) -> str:
    s = s.strip().rstrip(".")
    if s == "~":
        return "self"                         # the card naming itself — reliably self
    if s == "it":
        return "it"                           # an anaphor (the trigger's subject etc.) — left for the
        # engine to resolve from context; collapsing it to 'self' would be wrong (e.g. 'exile it' where
        # 'it' is the sacrificed permanent, not this card).
    return ground.slug(s) or "self"


@dataclass(frozen=True)
class Effect:
    verb: str
    amount: object   # int | str("X") | str("+x/+y") | "-"
    target: str
    extra: str = "-"  # secondary arg: counter kind, token spec, mana produced, granted keyword
    cond: str = "-"   # optionality/condition: 'may' (optional), 'if_you_did' (follows an optional), '-'

    def grounded(self) -> bool:
        return self.verb in ground.effect_verbs()


# (regex, builder) — builder(match) -> Effect. Each anchored to a clean frame.
_TEMPLATES: list[tuple[re.Pattern, object]] = []


def _t(pat):
    def deco(fn):
        _TEMPLATES.append((re.compile(pat, re.I), fn))
        return fn
    return deco


@_t(rf"^(?:~ |you )?draw (a card|an|\w+) cards?$|^draw (a) card$")
def _draw(m):
    raw = m.group(1) or m.group(2) or "a"
    n = _amount(raw.split()[0]) or (1 if raw.startswith(("a", "an")) else None)
    return Effect("draw", n, "you") if n is not None else None


@_t(rf"^(target [\w ]+?|each [\w ]+?) draws? (\w+) cards?$")
def _draw_tgt(m):
    n = _amount(m.group(2))
    return Effect("draw", n, _target(m.group(1))) if n is not None else None


@_t(rf"^(?:~|.+?) deals (\w+|\d+) damage to ({_TGT})$")
def _damage(m):
    n = _amount(m.group(1))
    return Effect("deal_damage", n if n is not None else "X", _target(m.group(2)))


@_t(rf"^destroy ({_TGT})$")
def _destroy(m):
    return Effect("destroy", "-", _target(m.group(1)))


@_t(rf"^exile ({_TGT})$")
def _exile(m):
    return Effect("exile", "-", _target(m.group(1)))


@_t(rf"^(?:({_TGT}) )?gains? (\w+) life$")
def _gain(m):
    n = _amount(m.group(2))
    return Effect("gain_life", n, _target(m.group(1) or "you")) if n is not None else None


@_t(rf"^(?:({_TGT}) )?loses? (\w+) life$")
def _lose(m):
    n = _amount(m.group(2))
    return Effect("lose_life", n, _target(m.group(1) or "you")) if n is not None else None


@_t(rf"^put ({_TGT}) into your hand$")
def _to_hand(m):
    return Effect("return_to_hand", "-", _target(m.group(1)))


@_t(rf"^return ({_TGT}) from your graveyard to your hand$")
def _regrowth(m):
    return Effect("return_to_hand", "-", _target(m.group(1)), "from_graveyard")


@_t(r"^exile the top (?:(\w+) )?cards? of your library$")
def _exile_top(m):
    n = _amount(m.group(1)) if m.group(1) else 1
    return Effect("exile", n, "top_of_library") if n is not None else None


@_t(r"^reveal the top (?:(\w+) )?cards? of your library$")
def _reveal_top(m):
    n = _amount(m.group(1)) if m.group(1) else 1
    return Effect("reveal", n, "top_of_library") if n is not None else None


@_t(rf"^({_TGT}) gets? ([+-]\d+/[+-]\d+) until end of turn$")
def _boost(m):
    return Effect("modify_pt", m.group(2).replace(" ", ""), _target(m.group(1)))


@_t(rf"^counter (target [\w ]+? spell[\w ]*?|{_TGT})$")
def _counter(m):
    return Effect("counter", "-", _target(m.group(1)))


@_t(rf"^scry (\w+)$")
def _scry(m):
    n = _amount(m.group(1))
    return Effect("scry", n, "you") if n is not None else None


@_t(rf"^surveil (\w+)$")
def _surveil(m):
    n = _amount(m.group(1))
    return Effect("surveil", n, "you") if n is not None else None


@_t(rf"^(?:(target [\w ]+?|each [\w ]+?|you) )?mills? (\w+) cards?$")
def _mill(m):
    n = _amount(m.group(2))
    return Effect("mill", n, _target(m.group(1) or "you")) if n is not None else None


@_t(rf"^(tap|untap) ({_TGT})$")
def _taputap(m):
    return Effect(m.group(1).lower(), "-", _target(m.group(2)))


@_t(rf"^return ({_TGT}) to (?:its owner's hand|your hand|their owners' hands?|its owner's hands?)$")
def _bounce(m):
    return Effect("return_to_hand", "-", _target(m.group(1)))


@_t(r"^add (.+)$")
def _add_mana(m):
    """'Add {G}' / 'Add one mana of any color' as an EFFECT (spell/triggered/activated body), §106."""
    prod = _mana_production(m.group(1))
    if not prod:
        return None
    return Effect("add_mana", len(prod), "you", "_".join(dict.fromkeys(prod)))


@_t(rf"^put (a|an|one|two|three|x|\w+) ([+-]\d+/[+-]\d+|[\w ]+?) counters? on ({_TGT})$")
def _put_counter(m):
    n = _amount(m.group(1))
    return Effect("put_counter", n if n is not None else "X", _target(m.group(3)),
                  ground.slug(m.group(2)) if "/" not in m.group(2) else m.group(2))


@_t(r"^create (a|an|one|two|three|x|\w+) (.+?) tokens?(?: .*)?$")
def _create_token(m):
    n = _amount(m.group(1))
    return Effect("create", n if n is not None else "X", "token", ground.slug(m.group(2)))


@_t(rf"^(?:(target [\w ]+?|each [\w ]+?|you) )?discards? (\w+) cards?(?: at random)?$")
def _discard(m):
    n = _amount(m.group(2))
    return Effect("discard", n, _target(m.group(1) or "you")) if n is not None else None


@_t(r"^shuffle(?: your library| it into your library)?$")
def _shuffle(m):
    return Effect("shuffle", "-", "you")


@_t(rf"^({_TGT}) gains? ([\w ]+?) until end of turn$")
def _gain_kw_eot(m):
    kw = ground.slug(m.group(2))
    if kw not in ground.keyword_abilities() and kw.split("_")[0] not in ground.keyword_abilities():
        return None                           # only a real §702 keyword grant — else abstain
    return Effect("grant_keyword", "until_end_of_turn", _target(m.group(1)), kw)


# bare §701 keyword actions with no target (investigate, populate, proliferate, …).
_BARE_ACTIONS = {"investigate", "populate", "proliferate", "scry", "surveil", "explore",
                 "manifest", "amass", "incubate", "connive", "convoke"}


@_t(r"^(\w+)$")
def _bare_action(m):
    v = ground.slug(m.group(1))
    return Effect(v, "-", "you") if v in _BARE_ACTIONS else None


@_t(rf"^attach (?:~|it) to ({_TGT})$")
def _attach(m):
    return Effect("attach", "-", _target(m.group(1)))


@_t(r"^you become the monarch$")
def _monarch(m):
    return Effect("become_monarch", "-", "you")


@_t(rf"^you (?:gain )?control ({_TGT})$")
def _control(m):
    return Effect("gain_control", "-", _target(m.group(1)))


@_t(rf"^(\w+) ({_TGT})$")
def _verb_target(m):
    """Generic '<grounded verb> <target>' — regenerate/goad/detain/sacrifice/tap/… target X.
    parse_effect's grounded() check rejects any first-word that isn't a rules action."""
    return Effect(ground.slug(m.group(1)), "-", _target(m.group(2)))


@_t(r"^pay ((?:\{[^}]+\})+|\w+ life)$")
def _pay(m):
    return Effect("pay", m.group(1).replace(" ", "_"), "you")


def parse_effect(sentence: str) -> "Effect | None":
    """A single effect sentence -> grounded Effect, or None (abstain). Only emits if verb is grounded."""
    s = sentence.strip().rstrip(".").strip()
    for pat, fn in _TEMPLATES:
        m = pat.match(s)
        if m:
            e = fn(m)
            if e and e.grounded():
                return e
    return None


import dataclasses as _dc

_MAY = re.compile(r"^you may (.+)$", re.I)
_IF_YOU_DO = re.compile(r"^if you do,?\s+(.+)$", re.I)


def parse_clause(sentence: str) -> "Effect | None":
    """Like parse_effect, but recognizes the optional/conditional wrappers that dominate the tail:
    'you may <effect>' -> the effect tagged cond='may'; 'if you do, <effect>' -> cond='if_you_did'
    (it follows an optional). Abstains if the inner effect isn't grounded."""
    s = sentence.strip().rstrip(".").strip()
    m = _MAY.match(s)
    if m:
        e = parse_effect(m.group(1))
        return _dc.replace(e, cond="may") if e else None
    m = _IF_YOU_DO.match(s)
    if m:
        e = parse_effect(m.group(1))
        return _dc.replace(e, cond="if_you_did") if e else None
    return parse_effect(s)


if __name__ == "__main__":
    for s in ["Draw a card", "Draw two cards", "~ deals 3 damage to any target",
              "Destroy all creatures", "Destroy target creature", "You gain 3 life",
              "Target creature gets +1/+1 until end of turn", "Counter target spell",
              "Scry 2", "Target player draws two cards", "Exile target permanent",
              "Return target creature to its owner's hand", "Untap target creature",
              "Goad target creature"]:
        print(f"  {s:50} -> {parse_effect(s)}")
