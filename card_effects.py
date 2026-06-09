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
_TGT = (r"(?:any target|another target|a (?:second|third|fourth|fifth) target|up to \w+ target[\w' -]*?|any number of target[\w' -]*?|"
        r"target (?:[\w']+, )+(?:or |and )?[\w']+(?: with [\w' ]+?)?|"   # type-list target: 'target artifact, creature, or land [with flying]'
        r"(?:\w+ )?target [\w' -]+?|"
        r"each [\w' -]+?|all [\w' -]+?|(?:attacking|blocking) [\w' -]+?|"
        r"(?:other |another |all )?[\w' -]+? (?:you control|your opponents control|an opponent controls|they control) (?:with|of|that are|that have|named) [\w' +/-]+?|"   # qualified subset
        r"(?:[\w-]+ )?[\w-]+ (?:you control|you don't control|your opponents control|an opponent controls|they control)|"
        r"enchanted \w+|equipped \w+|the exiled cards?|those [\w-]+|"
        r"(?:that|the) [\w' -]+?'s (?:controller|owner)|that [\w'-]+|"
        r"(?:the )?(?:defending|attacking|active|target|chosen) player|the player|each player|that player's controller|"
        r"~|it|them|they|her|him|you|its controller|its owner|their controller)")


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
    m = re.fullmatch(r"(one|two|three|four|five|six) mana of the chosen color", w, re.I)
    if m:
        return ["chosen_color"] * _NUMWORD[m.group(1).lower()]
    m = re.fullmatch(r"(one|two|three|four|five|six) mana in any combination of colors", w, re.I)
    if m:
        return ["any_combination"] * _NUMWORD[m.group(1).lower()]
    m = re.fullmatch(r"([\dx]+) mana in any combination of (?:colors|\{[^}]+\}(?: and/or \{[^}]+\})*)", w, re.I)
    if m:                                            # 'X mana in any combination of {W} and/or {U}' etc.
        return ["any_combination_" + (m.group(1).lower())]
    m = re.fullmatch(r"(?:one|a) mana of any color in your commander's color identity", w, re.I)
    if m:
        return ["commander_color_identity"]      # §903.4 color identity restricts which colors
    m = re.fullmatch(r"(?:one|a) mana of any (?:color|type) that a land (you control|an opponent controls) could produce", w, re.I)
    if m:
        return ["land_could_produce_" + ground.slug(m.group(1))]
    m = re.fullmatch(r"(?:one|a) mana of any type that (?:that |the )?land (?:produced|could produce)", w, re.I)
    if m:
        return ["that_land_type"]
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


@_t(rf"^({_TGT}) draws? (a card|\w+) cards?$|^({_TGT}) draws? (a) card$")
def _draw_tgt(m):
    who, amt = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    n = 1 if amt in ("a", "a card") else _amount(amt)
    return Effect("draw", n, _target(who)) if n is not None else None


@_t(rf"^(?:({_TGT}) )?draws? (a card|\w+) cards? for each (.+?)$")
def _draw_foreach(m):
    base = "1" if m.group(2) in ("a", "a card") else (str(_amount(m.group(2))) if _amount(m.group(2)) is not None else ground.slug(m.group(2)))
    return Effect("draw", base + "_per_" + ground.slug(m.group(3)), _target(m.group(1) or "you"))


@_t(rf"^(?:~|.+?) deals (\w+|\d+) damage to ({_TGT})$")
def _damage(m):
    n = _amount(m.group(1))
    return Effect("deal_damage", n if n is not None else "X", _target(m.group(2)))


@_t(rf"^(?:~|it|.+?) deals damage equal to (.+?) to ({_TGT})$")
def _damage_equal(m):
    return Effect("deal_damage", "equal_to_" + ground.slug(m.group(1)), _target(m.group(2)))


@_t(rf"^(?:~|it|.+?) deals that much damage to ({_TGT})$")
def _damage_that_much(m):
    """'<source> deals that much damage to <target>' — damage equal to a just-named amount (§120)."""
    return Effect("deal_damage", "that_amount", _target(m.group(1)))


@_t(rf"^(?:~|it|.+?) deals damage to ({_TGT}) equal to (.+?)$")
def _damage_to_equal(m):
    """'<source> deals damage to <target> equal to <amount>' — the target-first phrasing of §120."""
    return Effect("deal_damage", "equal_to_" + ground.slug(m.group(2)), _target(m.group(1)))


@_t(r"^(?:~|it|.+?) deals (\d+|x|\w+) damage divided as you choose among (.+?)$")
def _damage_divided(m):
    """'<source> deals N damage divided as you choose among <targets>' — divided damage (§601.2d)."""
    n = _amount(m.group(1))
    return Effect("deal_damage", n if n is not None else ground.slug(m.group(1)),
                  ground.slug(m.group(2)), "divided")


@_t(rf"^({_TGT}) deals damage to itself equal to (.+?)$")
def _damage_self(m):
    """'<creature> deals damage to itself equal to <amount>' — self-directed damage (§120)."""
    return Effect("deal_damage", "equal_to_" + ground.slug(m.group(2)), _target(m.group(1)), "itself")


@_t(rf"^({_TGT}) reveals? the top (?:(\w+) )?cards? of (?:their|its owner's|your) library$")
def _subject_reveal_top(m):
    n = _amount(m.group(2)) if m.group(2) else 1
    return Effect("reveal", n if n is not None else 1, _target(m.group(1)))


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


@_t(rf"^(?:({_TGT}) )?gains? (\w+) life for each (.+?)$")
def _gain_foreach(m):
    n = _amount(m.group(2))
    return Effect("gain_life", (str(n) if n is not None else ground.slug(m.group(2))) + "_per_" + ground.slug(m.group(3)),
                  _target(m.group(1) or "you"))


@_t(rf"^(?:({_TGT}) )?loses? (\w+) life$")
def _lose(m):
    n = _amount(m.group(2))
    return Effect("lose_life", n, _target(m.group(1) or "you")) if n is not None else None


@_t(rf"^(?:({_TGT}) )?loses? half (?:your |their |his or her |its )?life(?:,? rounded (up|down))?$")
def _lose_half(m):
    """'<target> loses half [your/their] life[, rounded up/down]' — a fractional life loss (§119.4
    rounding). Amount is the slug 'half' (with rounding) so the engine halves current life faithfully."""
    amt = "half" + ("_rounded_" + m.group(2) if m.group(2) else "")
    return Effect("lose_life", amt, _target(m.group(1) or "you"))


@_t(rf"^put ({_TGT}) into your hand$")
def _to_hand(m):
    return Effect("return_to_hand", "-", _target(m.group(1)))


@_t(rf"^return ({_TGT}) from your graveyard to your hand$")
def _regrowth(m):
    return Effect("return_to_hand", "-", _target(m.group(1)), "from_graveyard")


@_t(r"^exile the top (?:(\w+) )?cards? of ([\w' ]+?) librar(?:y|ies)$")
def _exile_top(m):
    n = _amount(m.group(1)) if m.group(1) else 1
    owner = "library" if m.group(2).lower() == "your" else "top_of_" + ground.slug(m.group(2)) + "_library"
    return Effect("exile", n, "top_of_library" if m.group(2).lower() == "your" else owner) if n is not None else None


@_t(r"^reveal the top (?:(\w+) )?cards? of ([\w' ]+?) librar(?:y|ies)$")
def _reveal_top(m):
    n = _amount(m.group(1)) if m.group(1) else 1
    tgt = "top_of_library" if m.group(2).lower() == "your" else "top_of_" + ground.slug(m.group(2)) + "_library"
    return Effect("reveal", n, tgt) if n is not None else None


@_t(r"^manifest the top card of your library$")
def _manifest_top(m):
    return Effect("manifest", 1, "top_of_library")


@_t(r"^clash with an opponent$")
def _clash(m):
    return Effect("clash", "-", "you")


@_t(rf"^({_TGT}) gets? ([+-](?:\d+|X)/[+-](?:\d+|X)) (until end of turn|until end of combat|until your next turn|until end of your next turn|this turn)$")
def _boost(m):
    # P/T delta may be a §107.3 variable X ('-X/-X'); recorded verbatim, still grounded in modify_pt.
    # Non-default durations (§611) are kept in the cond slot so the boost's lifetime isn't lost.
    dur = "-" if m.group(3).lower() == "until end of turn" else ground.slug(m.group(3))
    return Effect("modify_pt", m.group(2).replace(" ", ""), _target(m.group(1)), "-", dur)


@_t(rf"^({_TGT}) gets? ([+-]\d+/[+-]\d+) or ([+-]\d+/[+-]\d+)(?: until end of turn)?$")
def _boost_choice(m):
    """'<X> gets +N/-N or -N/+N [until end of turn]' — a §107.3 pump where the controller chooses one
    of two deltas; both options recorded in the amount slug (still grounded in modify_pt)."""
    return Effect("modify_pt", f"{m.group(2)}_or_{m.group(3)}", _target(m.group(1)))


@_t(rf"^({_TGT}) gets? ([+-]\d+/[+-]\d+) until end of turn for each (.+?)$")
def _boost_foreach(m):
    """'<X> gets +N/+N until end of turn for each <thing>' — a count-scaled pump (§107.3)."""
    return Effect("modify_pt", m.group(2) + "_per_" + ground.slug(m.group(3)),
                  _target(m.group(1)), "until_end_of_turn")


@_t(rf"^({_TGT}) gets? ([+-]\d+/[+-]\d+) for as long as (.+?)$")
def _boost_aslongas(m):
    """'<X> gets +N/+N for as long as <cond>' — a duration-bounded pump (§611)."""
    return Effect("modify_pt", m.group(2), _target(m.group(1)), "for_as_long_as_" + ground.slug(m.group(3)))


@_t(rf"^({_TGT}) gets? (?:an additional )?([+-](?:\d+|X)/[+-](?:\d+|X))$")
def _boost_bare(m):
    # bare P/T delta with no stated duration — the duration (if any) is supplied by a wrapper such as
    # _UNTIL ('Until end of turn, <X> gets +N/+N'); on its own it is a continuous modify_pt. 'an
    # additional' (stacking conditional anthems) is semantically the same continuous P/T boost.
    return Effect("modify_pt", m.group(2).replace(" ", ""), _target(m.group(1)))


@_t(rf"^counter (target [\w ]+? spell[\w ]*?|{_TGT})$")
def _counter(m):
    return Effect("counter", "-", _target(m.group(1)))


@_t(rf"^(?:({_TGT}) )?scr(?:y|ies) (\w+)$")
def _scry(m):
    n = _amount(m.group(2))
    return Effect("scry", n, _target(m.group(1) or "you")) if n is not None else None


@_t(rf"^surveil (\w+)$")
def _surveil(m):
    n = _amount(m.group(1))
    return Effect("surveil", n, "you") if n is not None else None


@_t(rf"^(?:({_TGT}) )?mills? (a card|\w+) cards?$")
def _mill(m):
    n = 1 if m.group(2) == "a card" else _amount(m.group(2))
    return Effect("mill", n, _target(m.group(1) or "you")) if n is not None else None


@_t(rf"^(?:{_TGT} )?(tap|untap)s? ({_TGT})$")
def _taputap(m):
    return Effect(m.group(1).lower(), "-", _target(m.group(2)))


@_t(rf"^return ({_TGT}) to (?:its owner's hand|your hand|their owners' hands?|its owner's hands?)$")
def _bounce(m):
    return Effect("return_to_hand", "-", _target(m.group(1)))


_RET_DEST = {"hand": "return_to_hand", "battlefield": "return_to_battlefield",
             "library": "put_on_top", "graveyard": "put_in_graveyard"}


@_t(rf"^(?:{_TGT} )?returns? (.+?) to (?:its |their |your |his or her |the |a |an |owners?'? |owner's )*(hand|battlefield|library|graveyard)s?(?: under [\w' ]+ control)?(?: attached to [\w' ]+?)?( tapped)?(?: with \w+ [\w/+ ]*?counters? on it)?(?: at the beginning of [\w' ]+?)?$")
def _return_zone(m):
    """GENERIC 'Return <object> to <zone>' — hand/battlefield/library/graveyard (§614/§400). Object is a
    faithful noun-phrase slug (compound-guarded); the destination picks the grounded verb. Runs after
    the precise return templates."""
    if _is_compound_object(m.group(1)):
        return None
    return Effect(_RET_DEST[m.group(2).lower()], "-", _target(m.group(1)), "tapped" if m.group(3) else "-")


@_t(r"^return to [\w' ]*?(hand|battlefield|library|graveyard)s? (.+?)$")
def _return_zone_rev(m):
    """Reversed phrasing 'Return to <zone> <object>' ('Return to your hand all cards …')."""
    if _is_compound_object(m.group(2)):
        return None
    return Effect(_RET_DEST[m.group(1).lower()], "-", _target(m.group(2)))


@_t(r"^you get ((?:\{e\})+)$")
def _get_energy(m):
    return Effect("get_energy", m.group(1).count("{"), "you")


@_t(r"^you get (twice |half )?that many (\{e\})$")
def _get_energy_that_many(m):
    """'you get that many {E}' — an anaphoric energy count (§107.3 / §107.16). Mirrors _get_energy's
    tuple but with a 'that_amount' count instead of a literal symbol count."""
    return Effect("get_energy", _that_amt(m.group(1), None), "you")


# 'you get {TK}'/'you get {A}' — ticket (Unfinity) and acorn counters; like energy, these are player
# resource counters gained via 'get'. Faithful to the _gets_counter convention (put_counter + kind).
@_t(r"^you get ((?:\{tk\})+)$")
def _get_ticket(m):
    return Effect("put_counter", m.group(1).count("{"), "you", "ticket")


@_t(r"^you get ((?:\{a\})+)$")
def _get_acorn(m):
    return Effect("put_counter", m.group(1).count("{"), "you", "acorn")


@_t(rf"^(?:({_TGT}) )?adds? (?:an additional |additional )?(.+)$")
def _add_mana(m):
    """'Add {G}' / '<player> adds {G}' / 'add an additional {C}' as an EFFECT (§106)."""
    prod = _mana_production(m.group(2))
    if not prod:
        return None
    return Effect("add_mana", len(prod), _target(m.group(1) or "you"), "_".join(dict.fromkeys(prod)))


@_t(rf"^({_TGT}) perpetually gets ([+-]\d+/[+-]\d+)$")
def _perpetual(m):
    return Effect("modify_pt", m.group(2), _target(m.group(1)), "-", "perpetual")


@_t(rf"^switch ({_TGT})'s power and toughness(?: until end of turn)?$")
def _switch_pt(m):
    return Effect("switch_pt", "-", _target(m.group(1)))


@_t(r"^put a number of ([+-]\d+/[+-]\d+|[\w ]+?) counters? on (.+?) equal to (.+?)$")
def _put_counter_equal(m):
    """'Put a number of <kind> counters on <object> equal to <count>' — count-scaled counters (§122)."""
    if _is_compound_object(m.group(2)):
        return None
    kind = m.group(1) if "/" in m.group(1) else ground.slug(m.group(1))
    return Effect("put_counter", "equal_to_" + ground.slug(m.group(3)), _target(m.group(2)), kind)


@_t(r"^distribute (\w+) ([+-]\d+/[+-]\d+|[\w ]+?) counters? among (.+?)$")
def _distribute_counters(m):
    """'Distribute N <kind> counters among <targets>' — §122 counter placement spread over multiple
    targets; the target set is a faithful slug, 'distributed' recorded in the cond slot."""
    n = _amount(m.group(1))
    kind = m.group(2) if "/" in m.group(2) else ground.slug(m.group(2))
    return Effect("put_counter", n if n is not None else "X", _target(m.group(3)), kind, "distributed")


@_t(rf"^(?:({_TGT}) )?gets? (a|an|one|two|three|x|\w+) (poison|energy|experience) counters?$")
def _gets_counter(m):
    """'<player> gets N poison/energy/experience counters' — §122 player counters gained via 'get'
    (poison §104.3d/§122.1, energy §107.16, experience §122). Subject defaults to you."""
    n = _amount(m.group(2))
    return Effect("put_counter", n if n is not None else "X", _target(m.group(1) or "you"), m.group(3).lower())


@_t(rf"^(?:{_TGT} )?puts? (up to \w+|a|an|one|two|three|x|\w+) ([+-]\d+/[+-]\d+|[\w ]+?) counters? on (.+?)$")
def _put_counter(m):
    """'[<player>] put(s) N <kind> counter(s) on <object>' — the object captured as a faithful noun-
    phrase slug (compound-guarded so '… and <effect>' splits instead of being swallowed)."""
    if _is_compound_object(m.group(3)):
        return None
    q = m.group(1).lower()
    n = ("up_to_" + (str(_amount(q[6:])) if _amount(q[6:]) is not None else q[6:])) if q.startswith("up to ") else _amount(m.group(1))
    return Effect("put_counter", n if n is not None else "X", _target(m.group(3)),
                  ground.slug(m.group(2)) if "/" not in m.group(2) else m.group(2))


@_t(r"^put them back in any order$")
def _put_back_any_order(m):
    """'Put them back in any order' — reorder looked-at cards on top of the library (§401, scry-like)."""
    return Effect("put_on_top", "-", "them", "any_order")


@_t(rf"^put (its|all|all of its) counters on ({_TGT})$")
def _move_counters(m):
    """'Put its/all counters on <target>' — moving existing counters (§122) to another permanent."""
    return Effect("put_counter", ground.slug(m.group(1)), _target(m.group(2)), "moved")


@_t(rf"^move (a|an|one|two|three|x|\w+) ([+-]\d+/[+-]\d+|[\w ]+?) counters? from ({_TGT}) (?:onto|to) ({_TGT})$")
def _move_counter_from(m):
    """'Move N <kind> counter(s) from <X> onto <Y>' — relocating counters between permanents (§122).
    Source recorded in cond, destination is the target."""
    n = _amount(m.group(1))
    kind = m.group(2) if "/" in m.group(2) else ground.slug(m.group(2))
    return Effect("put_counter", n if n is not None else "X", _target(m.group(4)), kind, "moved_from_" + _target(m.group(3)))


@_t(rf"^put that many ([+-]\d+/[+-]\d+|[\w ]+?) counters? on ({_TGT})$")
def _put_counter_many(m):
    return Effect("put_counter", "that_amount", _target(m.group(2)),
                  ground.slug(m.group(1)) if "/" not in m.group(1) else m.group(1))


@_t(rf"^create (a|one|two|three|x|\w+) tokens? that(?:'s| are) (?:a )?cop(?:y|ies) of ({_TGT})(?:, except (?:it has |they have |it's |they're )?(.+?))?$")
def _create_copy(m):
    """'Create [N] token(s) that's a copy of <X>[, except <mods>]' — token copy creation (§111/§707).
    The 'except' clause (added haste, altered P/T/color, granted abilities) is kept as a faithful slug."""
    n = _amount(m.group(1))
    amt = n if n is not None else "X"
    extra = "copy_of_" + _target(m.group(2)) + ("_except_" + ground.slug(m.group(3)) if m.group(3) else "")
    return Effect("create", amt, "token", extra)


@_t(r"^create (a|one|two|three|x|\w+) cop(?:y|ies) of (.+?)(?:, except (.+?))?$")
def _create_copy_of(m):
    """'Create [N] copy/copies of <X>[, except <mods>]' — a §707 token copy of a card/permanent (the
    'token that's a copy' wording elided, e.g. 'Create a copy of the chosen card')."""
    if _is_compound_object(m.group(2)):
        return None
    n = _amount(m.group(1))
    amt = n if n is not None else "X"
    extra = "copy_of_" + ground.slug(m.group(2)) + ("_except_" + ground.slug(m.group(3)) if m.group(3) else "")
    return Effect("create", amt, "token", extra)


@_t(r"^discard your hand$")
def _discard_hand(m):
    return Effect("discard", "all", "you")


@_t(r"^(?:after this (?:phase|main phase), )?there is an additional combat phase(?: followed by an additional main phase)?$")
def _extra_combat(m):
    return Effect("extra_combat", "-", "you")


@_t(r"^end the turn$")
def _end_turn(m):
    """'End the turn' — the §724 expedited end-of-turn effect."""
    return Effect("end_the_turn", "-", "you")


@_t(rf"^(?:({_TGT}) )?skips? (?:your|its|their|his or her) (?:next )?([\w ]+? (?:step|phase)|turn)$")
def _skip(m):
    """'Skip your <step/phase/turn>' — a §500.7/§502+ skip effect (grounded skip action)."""
    return Effect("skip", "-", _target(m.group(1) or "you"), ground.slug(m.group(2)))


@_t(r"^(?:you )?create a number of (.+?) tokens? equal to (.+?)$")
def _create_equal(m):
    """'Create a number of <X> tokens equal to <count>' — count-scaled token creation (§111)."""
    return Effect("create", "equal_to_" + ground.slug(m.group(2)), "token", ground.slug(m.group(1)))


@_t(rf"^(?:({_TGT}) )?creates? (a|an|one|two|three|x|\w+) (.+?) tokens?(?: for each (.+?))?(?: .*)?$")
def _create_token(m):
    n = _amount(m.group(2))
    amt = (n if n is not None else "X")
    if m.group(4):
        amt = f"{amt}_per_{ground.slug(m.group(4))}"
    # the creator (group 1) defaults to the controller; a non-default creator is kept in the cond slot.
    creator = _target(m.group(1)) if m.group(1) and m.group(1).lower() != "you" else "-"
    cond = "creator_" + creator if creator != "-" else "-"
    return Effect("create", amt, "token", ground.slug(m.group(3)), cond)


@_t(rf"^(?:({_TGT}) )?(?:loses?|lose|wins?|win) the game$")
def _game_end(m):
    verb = "win_game" if re.search(r"win", m.group(0), re.I) else "lose_game"
    return Effect(verb, "-", _target(m.group(1) or "you"))


@_t(rf"^(?:cast|play) ({_TGT}) for as long as it remains exiled(?:, and mana of any (?:type|color) can be spent to (?:cast|play) it)?$")
def _cast_while_exiled(m):
    """'You may cast/play <X> for as long as it remains exiled[, and mana of any type …]' — an
    impulse-draw style exile-cast permission (§601.3e); 'while_exiled' kept in the extra slot."""
    return Effect("cast", "-", _target(m.group(1)), "while_exiled")


@_t(r"^cast (the copy|that card|it|~|that [\w ]+?)(?: this turn| without paying its mana cost)?$")
def _cast_plain(m):
    return Effect("cast", "-", _target(m.group(1)))


@_t(r"^put a ([\w ]+?) card from your hand onto the battlefield( tapped)?$")
def _put_from_hand(m):
    return Effect("return_to_battlefield", "-", ground.slug(m.group(1)) + "_card",
                  "from_hand_tapped" if m.group(2) else "from_hand")


_ZONE = {"hand": "put_in_hand", "graveyard": "put_in_graveyard"}


@_t(r"^(?:put )?((?:(?! into )(?! and ).)+?) into (?:your|its owner's|their) (hand|graveyard)$")
def _put_zone(m):
    """'Put <cards> into your hand/graveyard' — a §400.7 zone change of looked-at/revealed cards. The
    object excludes ' into '/' and ' so a compound ('… into your hand and the rest into your
    graveyard') won't be swallowed whole — it falls through to the body splitter and each half (the
    second being the verb-less 'the rest into your graveyard') parses as its own grounded zone-move."""
    if re.search(r"\bputs?\b", m.group(1), re.I):   # a declarative '<subject> puts …' is _subject_puts' job
        return None
    return Effect(_ZONE[m.group(2)], "-", "you", ground.slug(m.group(1)))


def _that_amt(mult, plus):
    """'that much/many' with an optional 'twice/half' multiplier or 'plus N' rider -> an amount slug."""
    a = "that_amount"
    if mult:
        a = ground.slug(mult.strip()) + "_" + a
    if plus:
        a = a + "_" + ground.slug(plus.strip())
    return a


@_t(rf"^(?:({_TGT}) )?loses? (twice |half )?that much life( plus \w+| minus \w+)?$")
def _lose_that_much(m):
    return Effect("lose_life", _that_amt(m.group(2), m.group(3)), _target(m.group(1) or "you"))


@_t(rf"^(?:({_TGT}) )?discards? (\w+|any number of|up to \w+) cards?(?: at random)?$")
def _discard(m):
    spec = m.group(2).lower()
    if spec == "any number of":
        return Effect("discard", "any", _target(m.group(1) or "you"))
    up_to = spec.startswith("up to ")
    n = _amount(spec[6:] if up_to else spec)
    if n is None:
        return None
    return Effect("discard", n, _target(m.group(1) or "you"), "up_to" if up_to else "-")


@_t(rf"^(?:({_TGT}) )?discards? (their hand|those cards|that card|all the cards in their hand)$")
def _discard_set(m):
    return Effect("discard", "-", _target(m.group(1) or "you"), ground.slug(m.group(2)))


@_t(rf"^(?:({_TGT}) )?shuffles?(?: (?:your|their|his or her) library| (?:it|them|.+?) into (?:your|their|its owner's|their owner's) library)?$")
def _shuffle(m):
    return Effect("shuffle", "-", _target(m.group(1) or "you"))


@_t(rf"^(?:({_TGT}) )?draws? (an|a|\w+) additional cards?$")
def _draw_additional(m):
    n = 1 if m.group(2) in ("a", "an") else _amount(m.group(2))
    return Effect("draw", n if n is not None else 1, _target(m.group(1) or "you"), "additional")


@_t(rf"^(?:({_TGT}) )?looks? at (?:the top (?:(\w+) )?cards? of )?({_TGT}|their|his or her)(?:'s)? (?:hand|library)$")
def _look_at(m):
    n = _amount(m.group(2)) if m.group(2) else 1
    owner = "their" if m.group(3).lower() in ("their", "his or her") else _target(m.group(3))
    return Effect("look", n if n is not None else 1, owner, "by_" + _target(m.group(1)) if m.group(1) else "-")


@_t(rf"^(?:({_TGT}) )?(?:gains?|ha(?:s|ve)) ([\w ]+?) until end of turn$")
def _gain_kw_eot(m):
    kw = ground.slug(m.group(2))
    if kw not in ground.keyword_abilities() and kw.split("_")[0] not in ground.keyword_abilities():
        return None                           # only a real §702 keyword grant — else abstain
    return Effect("grant_keyword", "until_end_of_turn", _target(m.group(1) or "~"), kw)


@_t(rf"^prevent the next (\w+) damage that would be dealt (?:this turn )?to (any number of targets|{_TGT})(?: this turn)?$")
def _prevent(m):
    n = _amount(m.group(1))
    tgt = "any_number_of_targets" if m.group(2).lower() == "any number of targets" else _target(m.group(2))
    return Effect("prevent_damage", n if n is not None else "X", tgt)


@_t(rf"^the next (\w+) damage that would be dealt to ({_TGT}) this turn is dealt to ({_TGT})(?: instead)?$")
def _redirect(m):
    """'The next N damage that would be dealt to <A> this turn is dealt to <B> instead' — a §614.9
    damage redirection; recorded as redirect_damage from A to B."""
    n = _amount(m.group(1))
    return Effect("redirect_damage", n if n is not None else "X", _target(m.group(3)), "from_" + _target(m.group(2)))


@_t(rf"^all (combat )?damage that would be dealt to ({_TGT})(?: this turn| by [\w' -]+?)? is dealt to ({_TGT})(?: instead)?$")
def _redirect_all(m):
    """'All [combat] damage that would be dealt to <A> is dealt to <B> instead' — a §614.9 blanket
    damage redirection (Pariah / Palisade Giant / Maze of Ith family)."""
    return Effect("redirect_damage", "all" + ("_combat" if m.group(1) else ""), _target(m.group(3)), "from_" + _target(m.group(2)))


@_t(rf"^change the targets? of ({_TGT})(?: with a single target)?$")
def _change_targets(m):
    """'Change the target(s) of <spell/ability>' — §115.7 target change."""
    return Effect("change_targets", "-", _target(m.group(1)))


@_t(rf"^({_TGT})'s owner puts? it on (?:their choice of )?the top or(?: the)? bottom of their library$")
def _owner_puts(m):
    """'<X>'s owner puts it on their choice of the top or bottom of their library' (§401) — owner-choice
    library placement."""
    return Effect("put_on_top", "-", _target(m.group(1)), "owner_choice_top_or_bottom")


@_t(r"^prevent all (combat )?damage that would be dealt this turn$")
def _fog(m):
    return Effect("prevent_damage", "all", "combat" if m.group(1) else "all")


@_t(r"^prevent (that damage|the next (\w+) damage|(\w+) of that damage)$")
def _prevent_that(m):
    """'Prevent that damage' / 'Prevent the next N damage' / 'Prevent N of that damage' (§615) — the
    consequent of an 'if damage would be dealt …' clause; the wrapper supplies the condition."""
    n = _amount(m.group(2) or m.group(3)) if (m.group(2) or m.group(3)) else None
    return Effect("prevent_damage", n if n is not None else "that", "-")


def _prevent_scope(kind: str, scope: str) -> "Effect":
    """Build the prevent_damage tuple for a 'Prevent all <kind> damage …' clause (§615), recording the
    scope as a faithful descriptive slug in the extra slot. The self-reference '~' is mapped to 'self'
    so it survives slugging (slug() would otherwise drop the bare '~'), matching the '~'->'self' target
    convention used by `_prevent`/`_redirect`. Convention: prevent_damage("all", "-", <scope_slug>)."""
    kind = (kind or "").strip()
    scope = ((kind + " ") if kind else "") + scope.strip()
    scope = re.sub(r"~", "self", scope)
    return Effect("prevent_damage", "all", "-", ground.slug(scope))


@_t(r"^prevent all (combat |noncombat )?damage that would be dealt (.+?)$")
def _prevent_all_scoped(m):
    """'Prevent all [combat|noncombat] damage that would be dealt <scope>' (§615) — the PASSIVE frame.
    <scope> spans the 'to <recipient>' / 'by <source-class>' / 'to and dealt by …' / 'this turn …'
    riders (e.g. 'to ~', 'to you and other permanents you control', 'to ~ by creatures',
    'to and dealt by ~ this turn'); the whole span is recorded as a faithful slug."""
    return _prevent_scope(m.group(1), m.group(2))


@_t(r"^prevent all (combat |noncombat )?damage (?:that )?([\w'~ -]+?) would deal( to .+?)?( this turn| this combat)?$")
def _prevent_all_source(m):
    """'Prevent all [combat|noncombat] damage <source> would deal [to <X>] [this turn]' (§615) — the
    ACTIVE / source-first frame ('… target creature would deal this turn', '… a source of your choice
    would deal this turn', '… that black sources and red sources would deal this turn'). The source
    class is recorded as a 'by <source>' scope rider, plus any 'to <recipient>' and 'this turn'
    suffix, so the slug stays consistent with the passive frame's 'by …'/'to …' scope vocabulary."""
    scope = "by " + m.group(2).strip()
    if m.group(3):
        scope += " " + m.group(3).strip()
    if m.group(4):
        scope += " " + m.group(4).strip()
    return _prevent_scope(m.group(1), scope)


@_t(r"^([\w' ]+?) (\d+|one|two|three|four|five|x)$")
def _kwaction_n(m):
    """A §701 keyword action that takes a number — 'Monstrosity 3', 'Amass 2', 'Proliferate'… ."""
    v = ground.slug(m.group(1))
    n = _amount(m.group(2))
    return Effect(v, n if n is not None else "-", "you") if v in ground.keyword_actions() else None


@_t(r"^([\w' ]+)$")
def _bare_action(m):
    """A bare §701 keyword action with no target — 'investigate', 'venture into the dungeon',
    'the ring tempts you', 'open an attraction'…  Whole clause must slug to a grounded action."""
    v = ground.slug(m.group(1))
    return Effect(v, "-", "you") if v in ground.keyword_actions() else None


@_t(rf"^(?:({_TGT}) )?draws? (twice |half )?that many cards( plus \w+| minus \w+)?$")
def _draw_that_many(m):
    return Effect("draw", _that_amt(m.group(2), m.group(3)), _target(m.group(1) or "you"))


@_t(rf"^(?:({_TGT}) )?mills? (twice |half )?that many cards( plus \w+| minus \w+)?$")
def _mill_that_many(m):
    """'<player> mills that many cards' — the count is an anaphoric 'that many' (a just-named amount,
    §107.3), mirroring _draw_that_many. Subject defaults to you."""
    return Effect("mill", _that_amt(m.group(2), m.group(3)), _target(m.group(1) or "you"))


@_t(rf"^(?:({_TGT}) )?discards? (twice |half )?that many cards( plus \w+| minus \w+)?(?: at random)?$")
def _discard_that_many(m):
    """'<player> discards that many cards' — anaphoric 'that many' count (§107.3). Subject defaults to
    you. (regex drops the optional 'at random' rider, as _discard does.)"""
    return Effect("discard", _that_amt(m.group(2), m.group(3)), _target(m.group(1) or "you"))


# amount-EXPRESSION variants for the card-flow verbs (faithful subject + amount): 'up to N',
# 'equal to <X>', 'as many … as <X>', 'half X' — discovered via the spaCy gap analysis but
# implemented as precise regex (spaCy dropped the subject/amount).
@_t(rf"^(?:({_TGT}) )?(draws?|mills?) (up to \w+ cards?|cards? equal to .+?|as many cards as .+?|half(?: of)? .+?)$")
def _flow_amount(m):
    """'<player> draws/mills <amount-expr>' — 'up to N', 'cards equal to <X>', 'as many cards as <X>',
    'half [of] <X>' — faithful subject + amount. (spaCy-discovered gap, regex-implemented.)"""
    verb = "draw" if m.group(2).lower().startswith("draw") else "mill"
    expr = m.group(3).strip()
    mm = re.match(r"up to (\w+) cards?$", expr, re.I)
    if mm:
        n = _amount(mm.group(1))
        amt = "up_to_" + (str(n) if n is not None else ground.slug(mm.group(1)))
    elif re.match(r"cards? equal to ", expr, re.I):
        amt = "equal_to_" + ground.slug(re.sub(r"^cards? equal to ", "", expr, flags=re.I))
    elif re.match(r"as many cards as ", expr, re.I):
        amt = "as_many_as_" + ground.slug(re.sub(r"^as many cards as ", "", expr, flags=re.I))
    else:
        amt = "half_" + ground.slug(re.sub(r"^half(?: of)? ", "", expr, flags=re.I))
    return Effect(verb, amt, _target(m.group(1) or "you"))


@_t(rf"^(?:({_TGT}) )?gains? (twice |half )?that much life( plus \w+| minus \w+)?$")
def _gain_that_much(m):
    return Effect("gain_life", _that_amt(m.group(2), m.group(3)), _target(m.group(1) or "you"))


@_t(rf"^attach (~|it|{_TGT}) to ({_TGT})$")
def _attach(m):
    """'Attach <equipment/aura> to <target>' — the §701.3 attach keyword action."""
    return Effect("attach", "-", _target(m.group(2)), _target(m.group(1)))


@_t(r"^you become the monarch$")
def _monarch(m):
    return Effect("become_monarch", "-", "you")


@_t(r"^(?:if it's neither day nor night, )?it becomes (day|night)(?: as ~ enters)?$")
def _day_night(m):
    """'It becomes day/night' — a §726 day-and-night designation change."""
    return Effect("becomes_" + m.group(1).lower(), "-", "-")


@_t(rf"^({_TGT}) assigns no combat damage this turn$")
def _no_combat_damage(m):
    """'<X> assigns no combat damage this turn' — a §510.1c damage-assignment effect."""
    return Effect("assign_no_combat_damage", "-", _target(m.group(1)))


@_t(rf"^the next time a source of your choice would deal damage to ({_TGT}) this turn, prevent that damage$")
def _prevent_next_source(m):
    """'The next time a source of your choice would deal damage to <X> this turn, prevent that damage'
    — a §615 prevention shield."""
    return Effect("prevent_damage", "next", _target(m.group(1)))


@_t(r'^you get an emblem with "(.+)"$')
def _emblem(m):
    """'You get an emblem with "<ability>"' — an emblem (§114); the granted ability is slugged."""
    return Effect("get_emblem", "-", "you", ground.slug(m.group(1))[:160])


@_t(r"^you take the initiative$")
def _initiative(m):
    return Effect("take_initiative", "-", "you")


@_t(r"^(?:you )?skip your (draw step|next draw step|untap step|combat phase|next combat phase|draw|next turn|next combat)$")
def _skip(m):
    return Effect("skip", "-", ground.slug(m.group(1)))


@_t(rf"^(?:you )?(?:gain )?control (?:of )?({_TGT})( until end of turn| for as long as .+?)?$")
def _control(m):
    return Effect("gain_control", "-", _target(m.group(1)),
                  "until_end_of_turn" if m.group(2) and "end of turn" in m.group(2) else
                  (ground.slug(m.group(2)) if m.group(2) else "-"))


@_t(rf"^({_TGT}) gains? control of ({_TGT})( until end of turn| for as long as .+?)?$")
def _control_subj(m):
    """'<player> gains control of <X>' — §720 control-change with an explicit gaining player; the new
    controller is recorded in the cond slot."""
    dur = "until_end_of_turn" if m.group(3) and "end of turn" in m.group(3) else \
        (ground.slug(m.group(3)) if m.group(3) else "-")
    return Effect("gain_control", "-", _target(m.group(2)), "by_" + _target(m.group(1)), dur)


def _is_compound_object(s: str) -> bool:
    """True if a captured 'object' actually runs on into a SECOND effect ('… and gain control of it',
    '… then exile it') rather than being a single (possibly qualified) noun phrase. Distinguishes a
    real conjunction-of-effects from an in-target qualifier ('toughness 4 or greater', 'red or green'):
    only ' and '/' then ' FOLLOWED BY a new predicate (a grounded verb or a player/pronoun subject)
    counts. ' or ' never splits effects in card text, so it's left alone."""
    if re.search(r" then |[:;]", s, re.I):
        return True
    for seg in re.split(r" and ", s, flags=re.I)[1:]:
        w = (seg.split() or [""])[0].lower().rstrip("s")
        if w in _PREDICATE_LEADS or w in ground.effect_verbs() or (w + "s") in ground.effect_verbs():
            return True
    return False


# words that signal a SECOND effect after 'and'/'then' — player/pronoun subjects, plus the base form
# of common grounded verbs (so '… and put it …', '… and create a token' are recognized as run-ons even
# though the grounded slug is 'put_counter'/'create…'). Used to decide whether to split vs. keep whole.
_PREDICATE_LEADS = frozenset({
    "you", "its", "their", "they",
    "gain", "draw", "deal", "lose", "put", "create", "return", "exile", "destroy", "tap", "untap",
    "sacrifice", "search", "reveal", "shuffle", "mill", "scry", "choose", "discard", "counter", "copy",
    "remove", "prevent", "regenerate", "goad", "attach", "cast", "play",
})


@_t(rf"^(\w+) ({_TGT})$")
def _verb_target(m):
    """Generic '<grounded verb> <target>' — regenerate/goad/detain/sacrifice/tap/… target X.
    parse_effect's grounded() check rejects any first-word that isn't a rules action. Abstains when the
    captured target runs on into a second effect, so the body splitter handles each half."""
    if _is_compound_object(m.group(2)):
        return None
    return Effect(ground.slug(m.group(1)), "-", _target(m.group(2)))


@_t(rf"^(?:({_TGT}) )?pays? ((?:(?:\w+|one or more|X) )?(?:\{{[^}}]+\}})+|\w+ life|any amount of (?:\{{[^}}]+\}}|mana))( to end this effect| any number of times)?$")
def _pay(m):
    # the optional leading count covers energy/mana paid by quantity ('pay eight {E}', 'pay one or more {E}').
    extra = "to_end_effect" if m.group(3) and "end" in m.group(3) else ("repeatable" if m.group(3) else "-")
    return Effect("pay", ground.slug(m.group(2)), _target(m.group(1) or "you"), extra)


@_t(r"^flip a coin( until you lose a flip)?$")
def _flip(m):
    return Effect("flip_coin", "until_lose" if m.group(1) else "-", "you")


@_t(rf"^({_TGT}) blocks ({_TGT}) (?:this turn |this combat )?if able$")
def _must_block_tgt(m):
    """'<A> blocks <B> this turn if able' — a §509 block requirement directed at a creature."""
    return Effect("must_block", "-", _target(m.group(1)), _target(m.group(2)))


@_t(rf"^({_TGT}) blocks (?:this turn|this combat|each combat)?(?: if able)$")
def _must_block_able(m):
    """'<A> blocks this turn if able' — a §509 block requirement with no specific attacker."""
    return Effect("must_block", "-", _target(m.group(1)))


@_t(rf"^({_TGT}) must be blocked(?: this turn| this combat)?(?: if able)?$")
def _must_be_blocked(m):
    """'<X> must be blocked [this turn] [if able]' — a §509 block requirement (lure-like)."""
    return Effect("must_be_blocked", "-", _target(m.group(1)))


@_t(rf"^({_TGT}) attacks?(?: (?!each combat|this turn|this combat)({_TGT}))?(?: each combat| this turn| this combat)? if able$")
def _must_attack(m):
    """'<X> attacks [<player>] [each combat/this turn/this combat] if able' — a §508 attack
    requirement, optionally directed at a specific player/planeswalker."""
    return Effect("must_attack", "-", _target(m.group(1)), _target(m.group(2)) if m.group(2) else "-")


@_t(r"^(?:you |players )?don't lose (?:this|unspent|all unspent)?\s*(?:\w+ )?mana as steps and phases end$")
def _retain_mana(m):
    """'you don't lose [this/unspent/<color>] mana as steps and phases end' — §500.4 mana retention."""
    return Effect("retain_mana", "-", "you")


@_t(r"^the (\w+) cost is equal to its mana cost$")
def _granted_keyword_cost(m):
    """'The <keyword> cost is equal to its mana cost.' — the cost specification that accompanies a
    granted alternative-cost keyword (flashback/scavenge/retrace … §702). Grounds only if the keyword
    is in the §702 roster; recorded as that keyword grant with a cost-equals-mana-cost descriptor."""
    kw = ground.slug(m.group(1))
    if kw not in ground.keyword_abilities():
        return None
    return Effect("grant_keyword", kw, "it", "cost_equals_mana_cost")


@_t(r"^sacrifice (a|an|another|two|three) ([\w ~']+?)$")
def _sacrifice_a(m):
    n = _amount(m.group(1))
    return Effect("sacrifice", n if isinstance(n, int) else "-", ground.slug(m.group(1) + " " + m.group(2)))


@_t(rf"^({_TGT}) sacrifices? (it|that [\w]+|them|those [\w]+)$")
def _sacrifice_subj(m):
    """'<player> sacrifices it/that creature/them' — a §701.17 sacrifice directed at a named permanent
    by an explicit player (e.g. 'Its controller sacrifices it')."""
    return Effect("sacrifice", "-", _target(m.group(2)), "by_" + _target(m.group(1)))


@_t(r"^put (.+?) on the bottom(?: of your library)?(?: in (?:a |any )?(?:random )?order)?$")
def _put_bottom(m):
    # 'of your library' is the §401 default zone and may be elided ('put the rest on the bottom …').
    return Effect("put_on_bottom", "-", "library", ground.slug(m.group(1)))


@_t(rf"^put ({_TGT}) into (?:its owner's|their owner's|your) library (\w+) from the top$")
def _put_library_position(m):
    """'Put <X> into its owner's library Nth from the top' — §401 library placement at a specific
    depth (Bury in Books, Temporal Spring). Position recorded in the extra slot."""
    return Effect("put_on_top", "-", _target(m.group(1)), m.group(2).lower() + "_from_top")


_LIB_OWNER = r"(?:your|their|its owner's|their owners?'|that player's|his or her|the|a)"


@_t(rf"^put ({_TGT}) on the bottom of {_LIB_OWNER} library$")
def _put_bottom_tgt(m):
    return Effect("put_on_bottom", "-", _target(m.group(1)))


@_t(rf"^put ({_TGT}) on top(?: of {_LIB_OWNER} library)?(?: in any order)?$")
def _put_top_tgt(m):
    # the bare 'put that card on top' form (after a shuffle) refers to the library top by §401 default.
    # 'in any order' is the §401 reorder rider when placing multiple cards (Goblin/Dwarven Recruiter).
    return Effect("put_on_top", "-", _target(m.group(1)))


@_t(rf"^put (.+?)(?: from your hand)? on (top|the bottom) of {_LIB_OWNER} (?:libraries|library)(?: in (?:any|a random) order)?$")
def _put_cards_library(m):
    """'Put <cards> [from your hand] on top/bottom of <owner>'s library [in any order]' — §401 library
    placement of a set of cards (object as a faithful slug)."""
    if _is_compound_object(m.group(1)):
        return None
    return Effect("put_on_top" if m.group(2).lower() == "top" else "put_on_bottom", "-", ground.slug(m.group(1)))


@_t(r"^look at the top (?:(\w+) )?cards? of your library$")
def _look_top(m):
    n = _amount(m.group(1)) if m.group(1) else 1
    return Effect("look", n, "top_of_library") if n is not None else None


@_t(r"^look at that many cards from the top of your library$")
def _look_that_many(m):
    return Effect("look", "that_amount", "top_of_library")


@_t(rf"^({_TGT}) can't (be blocked|block or be blocked|attack or block|block|attack)(?: ({_TGT}))? this turn$")
def _cant_combat(m):
    extra = _target(m.group(3)) if m.group(3) else "-"
    return Effect("cant_" + m.group(2).replace(" ", "_"), "-", _target(m.group(1)), extra)


@_t(r"^((?:[\w' -]+ )?creatures?(?: with(?:out)? [\w' -]+?)?) can't (be blocked|attack or block|block|attack)(?: this turn)?$")
def _cant_combat_set(m):
    """'<creature set> can't block/attack [this turn]' — a §508/§509 combat restriction on a subset
    ('Creatures without flying can't block this turn')."""
    return Effect("cant_" + m.group(2).replace(" ", "_"), "-", ground.slug(m.group(1)))


@_t(rf"^return ({_TGT}) from your graveyard to the battlefield( tapped)?$")
def _reanimate(m):
    return Effect("return_to_battlefield", "-", _target(m.group(1)), "tapped" if m.group(2) else "-")


@_t(r"^(?:you |they )?puts? (.+?)( from [\w' ]+? (?:graveyard|hand|exile))? onto the battlefield(?: under [\w' ]+? control)?( tapped)?(?: attached to [\w' ~]+?)?(?: with (?:\w+) [\w/+ ]*?counters? on it)?$")
def _reanimate_put(m):
    """'Put <card> [from a graveyard/hand/exile] onto the battlefield [under <controller>'s control]
    [tapped] [attached to <X>]' — reanimation / put-into-play (§614). Object captured as a faithful
    slug; compound-guarded. The source and tapped state are recorded only when actually stated."""
    if _is_compound_object(m.group(1)):
        return None
    src = "from_" + m.group(2).strip().split()[-1] if m.group(2) else "-"
    extra = (src + "_tapped").lstrip("-_") if (src != "-" and m.group(3)) else (
        "tapped" if m.group(3) else src)
    return Effect("return_to_battlefield", "-", _target(m.group(1)), extra)


@_t(rf"^return ({_TGT}) to the battlefield(?: under (?:your|its owner's|that player's) control)?( tapped)?$")
def _return_bf(m):
    """'Return <X> to the battlefield [under its owner's control]' — a battlefield return (§614),
    typically a death/leaves trigger's reanimation of the just-departed object."""
    return Effect("return_to_battlefield", "-", _target(m.group(1)), "tapped" if m.group(2) else "-")


@_t(rf"^exile ({_TGT}) until ~ leaves the battlefield$")
def _exile_until(m):
    return Effect("exile", "-", _target(m.group(1)), "until_self_leaves")


@_t(rf"^exile ({_TGT}) with (\w+) (\w[\w ]*?) counters? on it$")
def _exile_with_counters(m):
    """'Exile <X> with N <kind> counters on it' — exile that arrives with counters (suspend etc.)."""
    return Effect("exile", "-", _target(m.group(1)), ground.slug(m.group(2) + "_" + m.group(3)))


@_t(r"^copy (that spell|that ability|it|target [\w ]+?|~)$")
def _copy(m):
    return Effect("copy", "-", _target(m.group(1)))


@_t(r"^(?:you )?(?:may )?choose (?:a )?new targets? for (?:the|that|each|those) (?:copy|copies)$")
def _new_targets(m):
    return Effect("choose_new_targets", "-", "copy")


@_t(rf'^({_TGT}) (?:has|have|gains?) "(.+)"( until end of turn)?$')
def _grant_ability(m):
    """In-body granted ability '<who> has/gains "<ability>" [until end of turn]' (§613.6) — the
    ability text is slugged (quote-free), so even a rare bad split can't emit unbalanced quotes."""
    ab = ground.slug(m.group(2))[:160]
    return Effect("grant_ability", "until_end_of_turn" if m.group(3) else "-", _target(m.group(1)), ab) if ab else None


@_t(r"^amass ([\w ]+?) (\d+|one|two|three|x)$")
def _amass(m):
    n = _amount(m.group(2))
    return Effect("amass", n if n is not None else 1, "you", ground.slug(m.group(1)))


@_t(rf"^(?:{_TGT} )?choose(?:s)? (a|an|one|two|three|up to \w+|one or more|any number of|another|target|the) (.+?)$")
def _choose(m):
    """'Choose <quantifier> <thing>' — a §700.2 choice (a color, a creature type, target(s), …). The
    chosen thing is a faithful noun-phrase slug; the quantifier is folded into it."""
    if _is_compound_object(m.group(2)):
        return None
    q = "" if m.group(1) in ("a", "an", "one") else ground.slug(m.group(1)) + "_"
    return Effect("choose", "-", q + ground.slug(m.group(2)))


@_t(rf"^(?:({_TGT}) )?(?:takes?|take) (an|one|two|three|\w+) extra turns? after this one$")
def _extra_turn(m):
    """'<player> takes N extra turn(s) after this one' — extra turn(s) (§500.7)."""
    n = _amount(m.group(2))
    return Effect("extra_turn", n if n is not None else "-", _target(m.group(1) or "you"))


@_t(r"^(?:you )?choose (?:a|an|one|two|three|up to \w+|x)(?: ([\w ]+?))? (?:from|of) (?:it|among them|them|those|that player's hand|its owner's hand|target [\w ]+?)$")
def _choose_from(m):
    """'[You] choose <quantifier> [<card-kind>] from/of it/among them/those/a hand' — a §700.2 choice
    over a set ('choose one of them', 'choose two cards from it')."""
    return Effect("choose", "-", "you", ground.slug(m.group(1)) if m.group(1) else "from_set")


@_t(rf"^({_TGT}) reveals? their hand$")
def _reveal_hand(m):
    return Effect("reveal", "-", _target(m.group(1)), "hand")


@_t(r"^reveal (?:a|an|one|up to \w+) ([\w ]+?) from among them$")
def _reveal_among(m):
    """'Reveal a <card-kind> from among them' — revealing a card out of a looked-at set (§701.16)."""
    return Effect("reveal", "-", "you", ground.slug(m.group(1)))


@_t(r"^reveal (.+?)$")
def _reveal_generic(m):
    """GENERIC 'Reveal <object>' (§701.16) — 'reveal your hand', 'reveal the top card of your library',
    'reveal it', 'reveal the cards you drew'. Object as a faithful slug; compound-guarded."""
    if _is_compound_object(m.group(1)):
        return None
    return Effect("reveal", "-", "you", ground.slug(m.group(1)))


@_t(rf"^({_TGT}) (?:doesn't|don't) untap during (?:its controller's|their controller's|their controllers'|your|their)( next)? untap steps?(?: for as long as .+?)?$")
def _doesnt_untap_eff(m):
    return Effect("doesnt_untap", "-", _target(m.group(1)), "next" if m.group(1) and m.group(2) else "-")


@_t(rf"^({_TGT}) can ((?:attack|block)\b[\w' -]*? as though (?:it|they) (?:had|didn't have|don't have) [\w' -]+?)$")
def _as_though_combat(m):
    """'<subj> can attack/block … as though it had/didn't have <ability>' — an §722 as-though combat
    permission (e.g. attack despite §702.3 defender, block fliers as though it had reach). Recorded as
    a §613.6 ability grant whose granted permission is a faithful descriptive slug — every term grounds."""
    return Effect("grant_ability", "-", _target(m.group(1)), "can_" + ground.slug(m.group(2)))


@_t(rf"^(?:({_TGT}) )?can block (an additional creature|any number of creatures|up to \w+ additional creatures|an additional \w+ creatures?)(?: this turn| each combat)?$")
def _can_block_more(m):
    """'<subj> can block an additional creature / any number of creatures [this turn/each combat]' — a
    §509 multi-block permission, recorded as a §613.6 ability grant."""
    return Effect("grant_ability", "-", _target(m.group(1) or "self"), "can_block_" + ground.slug(m.group(2)))


@_t(r"^cast (.+?) without paying (?:its|their) mana costs?$")
def _cast_free(m):
    return Effect("cast", "-", _target(m.group(1)), "without_paying_mana_cost")


@_t(r"^you get (a|an|one|two|three|\w+) experience counters?$")
def _experience(m):
    n = _amount(m.group(1))
    return Effect("put_counter", n if n is not None else 1, "you", "experience")


@_t(rf"^(?:({_TGT}) )?gains? life equal to (.+?)$")
def _gain_equal(m):
    return Effect("gain_life", "equal_to_" + ground.slug(m.group(2)), _target(m.group(1) or "you"))


@_t(rf"^(?:({_TGT}) )?loses? life equal to (.+?)$")
def _lose_equal(m):
    return Effect("lose_life", "equal_to_" + ground.slug(m.group(2)), _target(m.group(1) or "you"))


@_t(rf"^({_TGT}) shuffles? (?:their|its owner's|his or her) ([\w ]+?) into (?:their|its owner's|his or her) library$")
def _shuffle_subj(m):
    """'<player> shuffles their graveyard / hand and graveyard into their library' — a shuffle
    (§103.2/§701.19); the source zone(s) are recorded as a slug."""
    return Effect("shuffle", "-", _target(m.group(1)), "from_" + ground.slug(m.group(2)))


@_t(r"^roll (a|an|one|two|three|\w+) (d\d+)s?$")
def _roll(m):
    n = _amount(m.group(1))
    return Effect("roll_die", n if n is not None else 1, "you", m.group(2).lower())


_SIDED = {"four": 4, "six": 6, "eight": 8, "ten": 10, "twelve": 12, "twenty": 20, "100": 100}


@_t(r"^roll (a|an|one|two|three|\w+) ([\w]+)-sided (?:die|dice)$")
def _roll_sided(m):
    n = _amount(m.group(1))
    sides = _SIDED.get(m.group(2).lower()) or (int(m.group(2)) if m.group(2).isdigit() else None)
    return Effect("roll_die", n if n is not None else 1, "you", f"d{sides}") if sides else None


@_t(rf"^(?:{_TGT} )?plays? (that card|those cards|them|it|~|the (?:top|exiled) cards?[\w ]*?|the top card of (?:their|your|his or her) library|that [\w ]+?)(?: this turn| until [\w ' ]+| if able)?$")
def _play(m):
    return Effect("play", "-", _target(m.group(1)))


@_t(r"^play (?:an additional|up to (?:one|two|\w+) additional) lands?(?: this turn)?$")
def _extra_land(m):
    """'[You may] play an additional land this turn' — a one-shot extra-land permission (§116.2a/
    §505.5b). The 'you may' prefix is handled by the wrapper layer, so this matches the bare verb."""
    return Effect("play", "-", "you", "additional_land_this_turn")


@_t(rf"^return ({_TGT}) to the battlefield(?: transformed)?(?: under [\w' ]+? control)?( tapped)?$")
def _return_bf(m):
    return Effect("return_to_battlefield", "-", _target(m.group(1)), "tapped" if m.group(2) else "-")


@_t(rf"^({_TGT}) becomes? a copy of ({_TGT}|that card|the chosen card)(?:, except (.+?))?(?: until end of turn)?$")
def _becomes_copy(m):
    """'<target> becomes a copy of <X>[, except <mods>]' — a §707 copy effect (clones, Vesuva, etc.);
    the copied object and any 'except' overrides are recorded as a faithful slug."""
    extra = "copy_of_" + _target(m.group(2)) + ("_except_" + ground.slug(m.group(3)) if m.group(3) else "")
    return Effect("becomes", "-", _target(m.group(1)), extra)


@_t(rf"^({_TGT}) (?:becomes?|is|are) (?:an? )?([\dX*]+/[\dX*]+)([\w' -]*?)(?: with [\w, ]+?)?(?: until end of turn)?$")
def _becomes(m):
    """'<target> becomes a N/N [colors/types] [creature] [until end of turn]' — animate / set P/T
    & types (§613.3 / §205). The type tail is recorded as a descriptive slug."""
    return Effect("becomes", m.group(2), _target(m.group(1)), ground.slug(m.group(3)) or "-")


@_t(rf"^({_TGT}) becomes? the (.+?) of your choice(?: until end of turn)?$")
def _becomes_choice(m):
    return Effect("becomes", "-", _target(m.group(1)), "chosen_" + ground.slug(m.group(2)))


@_t(rf"^({_TGT}) (?:becomes?|is|are) (white|blue|black|red|green|colorless|all colors|the color of your choice|that color|the chosen color)(?: in addition to its other colors)?(?: until end of turn)?$")
def _becomes_color(m):
    """'<target> becomes <color> [until end of turn]' — a §105/§613 color-change."""
    return Effect("becomes", "-", _target(m.group(1)), ground.slug(m.group(2)))


@_t(r"^(?:it is|they are|this permanent is|those permanents are) still (?:an? )?lands?$")
def _still_land(m):
    """'It's still a land' / 'They're still lands' — the §305 type-retention clarification on an
    animated land (it keeps being a land)."""
    return Effect("becomes", "-", "it", "still_a_land")


@_t(rf"^({_TGT}) (?:is|are|becomes?) an? ([\w' -]*?(?:artifact|enchantment|land|creature|planeswalker|Aura|Equipment|Plains|Island|Swamp|Mountain|Forest)s?)(?: in addition to its other types)?(?: until end of turn| for as long as (.+?))?$")
def _becomes_type(m):
    """'<target> is/becomes a[n] <permanent/land type> [until end of turn | for as long as <cond>]' —
    a §205 card-type set/change (restricted to type words so it can't false-match a P/T or arbitrary
    noun). A 'for as long as' duration (§611) is kept in the cond slot."""
    cond = "for_as_long_as_" + ground.slug(m.group(3)) if m.group(3) else "-"
    return Effect("becomes", "-", _target(m.group(1)), ground.slug(m.group(2)), cond)


@_t(rf"^({_TGT}) (?:isn't|aren't|is not|are not) an? ([\w' -]+?)(?: until end of turn)?$")
def _becomes_not(m):
    """'<target> isn't a <type>' — a §205 type REMOVAL (devotion gods that aren't creatures below
    threshold, 'isn't a creature'); recorded as a 'not_<type>' becomes effect."""
    return Effect("becomes", "-", _target(m.group(1)), "not_" + ground.slug(m.group(2)))


@_t(rf"^({_TGT}) (?:becomes?|is|are) an? ([\w' -]+?) with base power and toughness (\d+/\d+)(?: in addition to its other types)?(?: until end of turn)?$")
def _becomes_base_pt(m):
    """'<target> becomes/is a <colors/types> creature with base power and toughness N/N' — animate to a
    new creature with set base P/T (§208/§613.3); the type descriptor is a faithful slug."""
    return Effect("becomes", m.group(3), _target(m.group(1)), "base_pt_" + ground.slug(m.group(2)))


@_t(rf"^({_TGT}) (?:has|have|with) base power and toughness (\d+/\d+)(?: until end of turn)?$")
def _base_pt(m):
    """'<target> has base power and toughness N/N [until end of turn]' — a §208/§613.3 base-P/T set."""
    return Effect("becomes", m.group(2), _target(m.group(1)), "base_pt")


@_t(rf"^({_TGT}) (?:is|are|becomes?) every (creature|basic land|nonbasic land|land) type(?: in addition to (?:its|their) other types)?(?: until end of turn)?$")
def _all_types(m):
    """'<target> is every creature/basic land type' — a §205 all-types effect (changeling / Dryad of
    the Ilysian Grove omni-land)."""
    return Effect("becomes", "-", _target(m.group(1)), "every_" + ground.slug(m.group(2)) + "_type")


@_t(rf"^({_TGT}) (?:is|are|becomes?) an? ((?:white|blue|black|red|green|colorless)(?: (?:and )?(?:white|blue|black|red|green|colorless))* [\w' -]+?)(?: in addition to its other (?:types and colors|colors and types|types|colors))?(?: until end of turn)?$")
def _becomes_color_type(m):
    """'<target> is a <color(s)> <type(s)>' (e.g. 'is a black Zombie') — a §105/§205 colour-and-type
    setting continuous effect (§613). Anchored on a color word so it can't match an arbitrary noun."""
    return Effect("becomes", "-", _target(m.group(1)), ground.slug(m.group(2)))


@_t(rf"^({_TGT}) (?:is|are) also an? ([\w' ,-]+?)(?: in addition to its other types)?(?: until end of turn)?$")
def _type_also(m):
    """'<target> is also a <type(s)>' (e.g. 'is also a Cleric, Rogue, Warrior, and Wizard') — a §205
    type ADDITION continuous effect; the added type(s) recorded as a faithful slug."""
    return Effect("becomes", "-", _target(m.group(1)), "added_" + ground.slug(m.group(2)))


@_t(rf"^({_TGT}) (?:is|are|becomes?) the chosen (color|type)(?: in addition to its other (?:types|colors))?(?: until end of turn)?$")
def _becomes_chosen(m):
    """'<target> is the chosen color/type [in addition to its other types]' — a §105/§205 set to a
    previously chosen color or type."""
    return Effect("becomes", "-", _target(m.group(1)), "chosen_" + m.group(2).lower())


@_t(rf"^({_TGT}) (?:is|are|becomes?) an? ([\w' -]+?) in addition to its other (?:types|colors)(?: until end of turn)?$")
def _type_add(m):
    """'<target> is a <type/color> in addition to its other types' — a §205/§105 type/color addition."""
    return Effect("becomes", "-", _target(m.group(1)), "added_" + ground.slug(m.group(2)))


@_t(rf"^({_TGT}) loses? all (?:other )?abilities(?: until end of turn)?$")
def _lose_abilities(m):
    """'<target> loses all abilities [until end of turn]' — a §613.6 ability-removal effect."""
    return Effect("lose_abilities", "-", _target(m.group(1)))


@_t(rf"^({_TGT}) loses? (this ability|[\w, ]+?)(?: until end of turn)?$")
def _lose_specific(m):
    """'<target> loses this ability / <keyword(s)>' — a §613.6 removal of a specific ability/keyword.
    Only fires when each named item is a §702 keyword (or the self-reference 'this ability')."""
    what = m.group(2).strip().lower()
    if what == "this ability":
        return Effect("lose_abilities", "-", _target(m.group(1)), "this_ability")
    kws = _kw_list(what)
    return Effect("lose_abilities", "-", _target(m.group(1)), "_".join(kws)) if kws else None


@_t(rf"^(?:({_TGT}) )?enters with (\w+) (?:additional )?([+-]\d+/[+-]\d+|[\w]+) counters? on it$")
def _enters_counters_eff(m):
    """'<X> enters with N [additional] <kind> counter(s) on it' — an ETB counter placement (§614/§122),
    used both standalone and inside replacement wrappers ('If …, that creature enters with …')."""
    n = _amount(m.group(2))
    kind = m.group(3) if "/" in m.group(3) else ground.slug(m.group(3))
    return Effect("put_counter", n if n is not None else 1, _target(m.group(1) or "self"), kind, "on_enter")


@_t(rf"^remove (a|an|one|two|three|all|any number of|x|\w+) (?:([+-]\d+/[+-]\d+|[\w ]+?) )?counters? from ({_TGT})$")
def _remove_counter(m):
    n = _amount(m.group(1))
    if n is None:
        n = "all" if m.group(1).lower() == "all" else ("any" if m.group(1).lower() == "any number of" else "X")
    kind = "-" if not m.group(2) else (m.group(2) if "/" in m.group(2) else ground.slug(m.group(2)))
    return Effect("remove_counter", n, _target(m.group(3)), kind)


@_t(rf"^({_TGT}) discards? that card$")
def _discard_that(m):
    return Effect("discard", "that_amount", _target(m.group(1)))


@_t(rf"^({_TGT}) is goaded$")
def _is_goaded(m):
    """'<creature> is goaded' — the §701.38 goad keyword action applied as a continuous effect."""
    return Effect("goad", "-", _target(m.group(1)))


@_t(rf"^remove ({_TGT}) from combat$")
def _remove_from_combat(m):
    """'Remove <X> from combat' — the §506.4 remove-from-combat action."""
    return Effect("remove_from_combat", "-", _target(m.group(1)))


@_t(rf"^(?:({_TGT}) )?endures? (\w+)$")
def _endure(m):
    """'<X> endures N' — the Endure keyword action (put N +1/+1 counters or make an N/N token)."""
    n = _amount(m.group(2))
    return Effect("endure", n if n is not None else "-", _target(m.group(1) or "~")) if "endure" in ground.effect_verbs() else None


@_t(rf"^({_TGT}) fights ({_TGT})$")
def _fight(m):
    """'<A> fights <B>' — the §701.12 fight keyword action (each deals damage equal to its power to
    the other). Recorded as a single grounded fight effect between the two creatures."""
    return Effect("fight", "-", _target(m.group(1)), _target(m.group(2)))


@_t(rf"^have ({_TGT}) fight ({_TGT})$")
def _have_fight(m):
    """'Have <A> fight <B>' — the causative form of the §701.12 fight keyword action."""
    return Effect("fight", "-", _target(m.group(1)), _target(m.group(2)))


@_t(rf"^(?:then )?({_TGT}) fight each other$")
def _fight_each(m):
    """'<those creatures> fight each other' — a reciprocal §701.12 fight."""
    return Effect("fight", "-", _target(m.group(1)), "each_other")


@_t(rf"^({_TGT}) phases? (out|in)(?: until [\w' ]+)?$")
def _phase(m):
    """'<permanent> phases out/in' — phasing (§702.26/§502.15)."""
    return Effect("phase_" + m.group(2).lower(), "-", _target(m.group(1)))


@_t(rf"^(?:({_TGT}) )?(?:gains?|ha(?:s|ve)) ([\w ]+?)$")
def _gains_perm(m):
    """'<target> gains/has <kw>' with NO duration — a permanent keyword grant (§613)."""
    kw = _kw_ok(m.group(2))
    return Effect("grant_keyword", "-", _target(m.group(1) or "~"), kw) if kw else None


@_t(rf"^({_TGT}) sacrifices? (a|an|one|two|three|\w+) (.+)$")
def _sacrifice_subj(m):
    return Effect("sacrifice", "-", _target(m.group(1)), ground.slug(m.group(2) + " " + m.group(3)))


@_t(rf"^have ({_TGT}) deals? (\w+) damage to ({_TGT})$")
def _have_deal(m):
    n = _amount(m.group(2))
    return Effect("deal_damage", n if n is not None else "X", _target(m.group(3)), "by_" + _target(m.group(1)))


@_t(rf"^have ({_TGT}) deals? damage equal to (.+?) to ({_TGT})$")
def _have_deal_equal(m):
    """'have <X> deal damage equal to <amount> to <Y>' — directed damage by another source (§120)."""
    return Effect("deal_damage", "equal_to_" + ground.slug(m.group(2)), _target(m.group(3)), "by_" + _target(m.group(1)))


@_t(rf"^have ({_TGT}) gets? ([+-]\d+/[+-]\d+) until end of turn$")
def _have_get(m):
    return Effect("modify_pt", m.group(2), _target(m.group(1)))


@_t(rf"^tap or untap ({_TGT})$")
def _tap_or_untap(m):
    return Effect("untap", "-", _target(m.group(1)), "or_tap")


@_t(rf"^(?:(a creature destroyed this way|{_TGT}|they) )?can't be regenerated(?: this turn)?$")
def _cant_regen(m):
    return Effect("cant_be_regenerated", "-", _target(m.group(1) or "it"))


@_t(r"^damage can't be prevented(?: this turn)?$")
def _cant_prevent(m):
    """'Damage can't be prevented this turn' — a §615 damage-prevention lockout."""
    return Effect("cant_prevent_damage", "-", "-")


@_t(r"^(?:you )?(?:may )?spend mana as though it were mana of any (?:color|type)(?: to cast .+?)?$")
def _spend_as(m):
    """'spend mana as though it were mana of any color [to cast …]' — a §106.6 mana-spending permission."""
    return Effect("spend_mana_as", "-", "you", "any_color")


@_t(r"^mana of any (?:type|color) can be spent to (?:cast|play) (.+?)$")
def _mana_any_for(m):
    """'Mana of any type can be spent to cast <X>' — the §106.6 spend-as permission scoped to a spell/
    card (often the rider on an impulse-cast); the scope is a faithful slug."""
    return Effect("spend_mana_as", "-", "you", "any_color_for_" + ground.slug(m.group(1)))


@_t(r"^all creatures? able to block ({0}) (?:this turn |this combat )?do so$".format(_TGT))
def _lure(m):
    """'All creatures able to block <X> this turn do so' — a §509 lure block requirement on a target."""
    return Effect("lure", "-", _target(m.group(1)))


# a comma-separated ENUMERATION of multiple distinct sought cards ('a white card, a blue card, … and a
# green card' / 'a Plains card, an Island card, …') — a SET search (§701.18), structurally different from
# a single sought card that's one of a TYPE disjunction ('a basic Forest, Plains, or Island card'). We
# ABSTAIN on the set form (no faithful single-target tuple) and OWN the disjunction form.
_SEARCH_MULTI = re.compile(r" cards?,\s+(?:a |an )|\b(?:cards?|named .+) and (?:a |an )", re.I)
# a captured search-object that runs on into a SECOND effect after a comma ('… card, exile that card',
# '… card, put it into your hand') — the comma is an effect boundary, not part of the noun phrase. The
# body splitter normally peels these, but guard here so the relaxed capture can't swallow a run-on.
_SEARCH_RUNON = re.compile(r",\s+(?:" + "|".join(sorted(ground.effect_verbs() | _PREDICATE_LEADS)) + r")\b", re.I)


def _is_single_sought_card(s: str) -> bool:
    """True if a captured search-object is ONE faithful sought card — a (possibly type-disjunction,
    possibly comma'd) noun phrase ending in 'card'/'cards', or a 'named <X>' phrase (whose name may
    itself contain a comma, e.g. 'Chandra, Bold Pyromancer'). Abstains on multi-card enumerations and on
    objects that run on into a second effect, so the relaxed comma capture stays faithful-or-abstain."""
    if _is_compound_object(s) or _SEARCH_MULTI.search(s) or _SEARCH_RUNON.search(s):
        return False
    return bool(re.search(r"(?:cards?|named .+)$", s, re.I))


@_t(r"^search your library for (.+?)$")
def _search(m):
    obj = m.group(1)
    if "," in obj and not _is_single_sought_card(obj):
        return None                          # multi-card set / run-on '… and put it …' -> let splitter handle
    if "," not in obj and _is_compound_object(obj):
        return None
    return Effect("search", "-", ground.slug(obj))


@_t(rf"^(?:({_TGT}) )?(?:may )?search(?:es)? ([\w' ,/-]+?(?:graveyard|hand|library|exile)[\w' ,/-]*?) for (.+?)$")
def _search_zones(m):
    """'[<player>] search[es] <…graveyard/hand/library…> for <X>' — a §701.18 search across zones; the
    searcher (if named) and searched zones are recorded, the sought card as the target."""
    obj = m.group(3)
    if "," in obj and not _is_single_sought_card(obj):
        return None
    if "," not in obj and _is_compound_object(obj):
        return None
    who = _target(m.group(1)) if m.group(1) else "you"
    return Effect("search", "-", ground.slug(obj), ground.slug(m.group(2)) + ("_by_" + who if who != "you" else ""))


@_t(rf"^put ({_TGT}) onto the battlefield( tapped)?$")
def _to_battlefield(m):
    return Effect("return_to_battlefield", "-", _target(m.group(1)), "tapped" if m.group(2) else "-")


@_t(r"^put a ([\w ]+?) card from among them onto the battlefield( tapped)?$")
def _put_among_bf(m):
    """'Put a <kind> card from among them onto the battlefield' — putting a looked-at card into play."""
    return Effect("return_to_battlefield", "-", ground.slug(m.group(1)) + "_card",
                  "from_among_tapped" if m.group(2) else "from_among")


# grounded verbs whose SOLE argument is the object they affect (no amount, no destination) — a generic
# imperative 'VERB <object>' grounds faithfully as VERB(target=<object slug>). Excludes amount-verbs
# (draw/mill/scry), destination-verbs (return/put), and prep-structured ones (deal … to).
_OBJ_VERBS = frozenset({"exile", "destroy", "tap", "untap", "sacrifice", "regenerate", "goad", "detain",
                        "counter", "transform", "populate", "fight", "behold", "suspect", "abandon",
                        "cloak", "double", "triple", "blight", "meld", "convert", "exchange"})
# structural markers that make a generic object capture unsafe (a colon-cost, scaling, or condition) —
# but NOT ' and '/' or ', which are handled by the predicate-aware _is_compound_object (so type unions
# like 'instant or sorcery card', 'artifacts and enchantments' stay intact while effect conjunctions
# split).
_OBJ_BAD = re.compile(r"[:;]|\bequal to\b|\bfor each\b|\bunless\b|\bwhere\b|\bif\b", re.I)


@_t(r"^(\w+) (.+?)$")
def _generic_object_verb(m):
    """LAST-RESORT generic leaf: an imperative 'VERB <object>' for an object-only grounded verb, with
    the object captured as a faithful noun-phrase slug ('exile that card from your graveyard'). Abstains
    on compound/nested/qualified objects (and/or/comma/colon/equal-to/…) so it can't emit a lossy fact;
    those need a specific template. Runs after every specific pattern."""
    v = ground.slug(m.group(1))
    if v not in _OBJ_VERBS or _OBJ_BAD.search(m.group(2)) or _is_compound_object(m.group(2)):
        return None
    return Effect(v, "-", ground.slug(m.group(2)))


_SUBJ_OBJ_VERBS = {"exiles": "exile", "reveals": "reveal", "searches": "search"}


_PUT_DEST = [(re.compile(r"on top of .*library", re.I), "put_on_top"),
             (re.compile(r"on the bottom of .*library", re.I), "put_on_bottom"),
             (re.compile(r"into .*graveyard", re.I), "put_in_graveyard"),
             (re.compile(r"into .*hand", re.I), "put_in_hand"),
             (re.compile(r"onto the battlefield", re.I), "return_to_battlefield")]


@_t(rf"^({_TGT}) puts? (.+?) (on top of .+?|on the bottom of .+?|into .+?|onto the battlefield)$")
def _subject_puts(m):
    """A declarative '<subject> puts <object> <destination>' ('Target opponent puts the cards from
    their hand on top of their library') — destination picks the grounded zone-move verb; the object is
    a faithful slug (compound-guarded)."""
    if _is_compound_object(m.group(2)):
        return None
    dest = next((v for pat, v in _PUT_DEST if pat.search(m.group(3))), None)
    if not dest:
        return None
    return Effect(dest, "-", _target(m.group(2)), "by_" + _target(m.group(1)))


@_t(rf"^({_TGT}) (exiles|reveals|searches) (.+?)$")
def _subject_obj_verb(m):
    """A declarative '<subject> exiles/reveals/searches <object>' ('Each player exiles the top card of
    their library', 'Target opponent reveals their hand') — the actor is the subject, the affected
    object a faithful slug (compound-guarded). Object-verbs that lack a subject-form template."""
    if _is_compound_object(m.group(3)):
        return None
    return Effect(_SUBJ_OBJ_VERBS[m.group(2).lower()], "-", _target(m.group(3)), "by_" + _target(m.group(1)))


@_t(rf"^({_TGT}) (\w+)$")
def _subject_action(m):
    """A §701 keyword action performed by an object — 'it explores', 'it connives', 'that creature
    investigates'. Third-person 's' is stripped to match the grounded action."""
    base = ground.slug(m.group(2)).rstrip("s") or ground.slug(m.group(2))
    if ground.slug(m.group(2)) in ground.keyword_actions():
        base = ground.slug(m.group(2))
    elif base not in ground.keyword_actions():
        return None
    return Effect(base, "-", _target(m.group(1)))


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
_SUBJ_MAY = re.compile(rf"^({_TGT}) may (.+)$", re.I)
_IF_YOU_DO = re.compile(r"^if you do,?\s+(.+)$", re.I)
_IF_COND = re.compile(r"^if (?!you do\b)(.+?), (.+)$", re.I)
_UNLESS_PAY = re.compile(r"^(.+?) unless (?:its controller|you|that player|they) pays? (.+)$", re.I)
_UNLESS = re.compile(r"^(.+?) unless (.+)$", re.I)
_DELAYED_LEAD = re.compile(r"^at (the beginning of [\w' ]+?|end of combat|the next [\w' ]+?), (.+)$", re.I)
_WHEN_LEAD = re.compile(r"^(?:when|whenever) (.+? (?:dies|leaves the battlefield|enters|attacks|blocks|"
                        r"deals (?:combat )?damage[\w' ]*?|is dealt damage|would [\w' ]+?|becomes [\w' ]+?|"
                        r"casts? [\w' ]+?|taps? [\w' ]+?)(?: this turn| next turn)?), (.+)$", re.I)
_DELAYED = re.compile(r"^(.+?) (?:at the beginning of (?:the next turn's upkeep|your next upkeep|"
                      r"the next end step|your next end step|the next turn's end step|your upkeep)|"
                      r"at end of combat|at the beginning of the next turn)$", re.I)
_NEXT_TIME = re.compile(r"^the next time (.+? would .+?)(?: this turn)?, (.+)$", re.I)
_HAVE = re.compile(rf"^have ({_TGT}) (.+)$", re.I)
_UNTIL = re.compile(r"^until (end of turn|your next turn|the end of your next turn|end of combat),\s+(.+)$", re.I)
_IF_TRAIL = re.compile(r"^(.+?) if (.+)$", re.I)


def _kw_ok(phrase: str):
    kw = ground.slug(phrase)
    return kw if (kw in ground.keyword_abilities() or kw.split("_")[0] in ground.keyword_abilities()) else None


def _kw_list(s: str):
    """Parse a keyword list 'first strike, vigilance, and trample' -> [kw,…] iff EVERY item grounds in
    §702, else None. Handles comma and/or 'and' separators (the common multi-keyword grant form)."""
    parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", s) if p.strip()]
    kws = [_kw_ok(p) for p in parts]
    return kws if parts and all(kws) else None


_EOT_PUMP = re.compile(rf"^({_TGT}) gets? ([+-]\d+/[+-]\d+)(?: and (?:gains?|has|have) ([\w, ]+?))? until end of turn$", re.I)
_EOT_GRANTS = re.compile(rf"^({_TGT}) (?:gains?|has|have) ([\w, ]+?) until end of turn$", re.I)
_EOT_PUMP_CANT = re.compile(rf"^({_TGT}) gets? ([+-]\d+/[+-]\d+) until end of turn and (can't (?:be blocked|block|attack)) this turn$", re.I)
_PERM_GRANTS = re.compile(rf"^({_TGT}) (?:gains?|has|have) ([\w, ]+?)$", re.I)


def _eot_compound(s: str):
    """A compound buff -> MULTIPLE effects: '<t> gets +N/+N and gains first strike, vigilance, and
    trample …', '<t> gains flying and lifelink …'. Keyword lists may be comma- and/or 'and'-separated;
    abstains unless EVERY granted word is a real §702 keyword. Normalizes a LEADING 'Until end of turn,
    …' to the suffix form first — else the body splitter peels '… and gains trample' into a subject-less
    clause and wrongly grants it to self."""
    lead = re.match(r"^until end of turn,\s+(.+)$", s, re.I)
    if lead and "until end of turn" not in lead.group(1).lower():
        s = lead.group(1) + " until end of turn"
    # animate / turn-into form: '<subj> loses all abilities and (has|is|becomes) <…>' — the second
    # predicate shares the subject, so reattach it before parsing (the splitter would drop it).
    m = re.match(rf"^({_TGT}) loses? all (?:other )?abilities and (has|have|is|are|becomes?|gains?) (.+?)$", s, re.I)
    if m:
        tail = parse_clause(f"{m.group(1)} {m.group(2)} {m.group(3)}")
        return [Effect("lose_abilities", "-", _target(m.group(1))), tail] if tail else None
    m = _EOT_PUMP_CANT.match(s)
    if m:
        who = _target(m.group(1))
        return [Effect("modify_pt", m.group(2), who),
                Effect(m.group(3).replace("can't ", "cant_").replace(" ", "_"), "-", who)]
    m = _EOT_PUMP.match(s)
    if m:
        who = _target(m.group(1))
        out = [Effect("modify_pt", m.group(2), who)]
        if m.group(3):
            kws = _kw_list(m.group(3))
            if not kws:
                return None
            out += [Effect("grant_keyword", "until_end_of_turn", who, kw) for kw in kws]
        return out
    m = _EOT_GRANTS.match(s)
    if m:
        kws = _kw_list(m.group(2))
        if kws:
            who = _target(m.group(1))
            return [Effect("grant_keyword", "until_end_of_turn", who, kw) for kw in kws]
    m = _PERM_GRANTS.match(s)         # permanent (no-duration) multi-keyword grant — single is _gains_perm's
    if m:
        kws = _kw_list(m.group(2))
        if kws and len(kws) > 1:
            who = _target(m.group(1))
            return [Effect("grant_keyword", "-", who, kw) for kw in kws]
    return None


_LARK_LEAF = None


def _lark_leaf(s):
    """LARK-FIRST leaf parser for the migrated verb families (lazy-imported to avoid the import cycle:
    card_lark imports card_effects). Returns an Effect for a clause shape lark has taken over, else
    None (the regex leaf then handles it). This is how the card pipeline shifts off regex family-by-
    family — cards are a formulaic sublanguage a CFG parses more faithfully than accreted regex."""
    global _LARK_LEAF
    if _LARK_LEAF is None:
        try:
            from card_lark import parse_clause_lark
            _LARK_LEAF = parse_clause_lark
        except Exception:
            _LARK_LEAF = lambda _s: None
    return _LARK_LEAF(s)


def parse_clauses(sentence: str) -> "list | None":
    """parse a clause into one OR MORE effects (compound until-EOT buffs yield several); else None."""
    multi = _eot_compound(sentence.strip().rstrip("."))
    if multi:
        return multi
    e = parse_clause(sentence)
    return [e] if e else None


def parse_clause(sentence: str) -> "Effect | None":
    """Like parse_effect, but recognizes the optional/conditional wrappers that dominate the tail:
    'you may <effect>' -> cond='may'; 'if you do, <effect>' -> cond='if_you_did' (follows an optional);
    'if <condition>, <effect>' -> cond=<condition slug> (a descriptive predicate, like a trigger slug).
    Abstains if the inner effect isn't grounded."""
    s = sentence.strip().rstrip(".").strip()
    s = re.sub(r"^(it's|they're|you're|it’s|they’re)\b", lambda m: {"it's": "it is", "they're":
               "they are", "you're": "you are", "it’s": "it is", "they’re": "they are"}[m.group(1).lower()],
               s, flags=re.I)                                    # expand leading contraction
    s = re.sub(r"^(?:then|otherwise),?\s+", "", s, flags=re.I)   # discourse lead — 'Then/Otherwise shuffle'
    s = re.sub(r"\balso (gains?|gets?|has|have)\b", r"\1", s, flags=re.I)  # 'X also gains trample' -> 'X gains trample'
    s = re.sub(r"^(they|those [\w-]+|these [\w-]+) each\b", r"\1", s, flags=re.I)  # 'They each get +N/+N' -> 'They get'
    s = re.sub(r"\s+instead$", "", s, flags=re.I)               # replacement tail — 'exile it instead' -> 'exile it'
    s = re.sub(r"^instead,?\s+", "", s, flags=re.I)             # replacement lead — 'instead draw a card' -> 'draw a card'
    s = re.sub(r",? rounded (?:up|down)$", "", s, flags=re.I)    # 'mill half their library, rounded down'
    s = re.sub(r" this way$| that way$", "", s, flags=re.I)      # anaphoric tail — 'exile the cards revealed this way'
    # trailing variable definition '…, where X is <count>' (§107.3) — parse the head and fold the
    # definition into the amount when the head's amount is that variable, else just drop the def.
    mw = re.match(r"^(.+?),? where ([a-z]) (?:is|are|equals?) (.+)$", s, re.I)
    if mw:
        inner = parse_clause(mw.group(1))
        if not inner:
            return None
        var = mw.group(2).upper()
        if str(inner.amount).upper() == var:
            return _dc.replace(inner, amount=var + "_" + ground.slug(mw.group(3)))
        return inner
    # trailing 'for each <X>' (§107.3) — a count-scaled effect; fold into the amount (or extra if the
    # effect has no numeric amount). Generalizes the per-verb for-each templates.
    mfe = re.match(r"^(.+?) for each (.+)$", s, re.I)
    if mfe and not re.search(r"\b(deals?|gets?|put|gains?|loses?|draws?|create|mill|distributes?)\b", mfe.group(2), re.I):
        inner = parse_clause(mfe.group(1))
        if inner:
            per = ground.slug(mfe.group(2))
            if str(inner.amount) not in ("-", "X"):
                return _dc.replace(inner, amount=f"{inner.amount}_per_{per}")
            if inner.extra == "-":
                return _dc.replace(inner, extra=f"per_{per}")
            return inner
    # leading 'For each <X>, <effect>' (§107.3) — the effect happens once per X; fold the per-scaling
    # into the amount (or extra) exactly like the trailing for-each form.
    mfl = re.match(r"^for each (.+?), (.+)$", s, re.I)
    if mfl:
        inner = parse_clause(mfl.group(2))
        if inner:
            per = ground.slug(mfl.group(1))
            if str(inner.amount) not in ("-", "X"):
                return _dc.replace(inner, amount=f"{inner.amount}_per_{per}")
            return _dc.replace(inner, extra=f"per_{per}") if inner.extra == "-" else inner
    m = _MAY.match(s)
    if m:
        return _combine(parse_clause(m.group(1)), "may")
    m = _SUBJ_MAY.match(s)            # '<subject> may <effect>' — reattach subject, mark optional
    if m:
        return _combine(parse_clause(f"{m.group(1)} {m.group(2)}"), "may")
    m = _IF_YOU_DO.match(s)
    if m:
        return _combine(parse_clause(m.group(1)), "if_you_did")
    m = _IF_COND.match(s)
    if m:
        return _combine(parse_clause(m.group(2)), ground.slug(m.group(1)), suffix=True)
    m = _UNLESS_PAY.match(s)
    if m:
        return _combine(parse_clause(m.group(1)), "unless_pay_" + ground.slug(m.group(2)))
    m = _UNLESS.match(s)
    if m:
        return _combine(parse_clause(m.group(1)), "unless_" + ground.slug(m.group(2)))
    m = _DELAYED.match(s)
    if m:
        return _combine(parse_clause(m.group(1)), "delayed")
    m = _DELAYED_LEAD.match(s)        # leading delayed trigger: 'At the beginning of the next end step, <effect>'
    if m:
        return _combine(parse_clause(m.group(2)), "delayed_" + ground.slug(m.group(1)))
    m = _WHEN_LEAD.match(s)           # nested/delayed trigger: 'When <event>, <effect>' inside a body
    if m:
        return _combine(parse_clause(m.group(2)), "when_" + ground.slug(m.group(1)))
    m = _NEXT_TIME.match(s)           # one-shot replacement: 'The next time <X> would <event>, <repl>'
    if m:
        return _combine(parse_clause(m.group(2)), "next_time_" + ground.slug(m.group(1)))
    m = _UNTIL.match(s)
    if m:
        inner = parse_clause(m.group(2))
        if not inner:
            return None
        return inner if inner.cond != "-" else _dc.replace(inner, cond="until_" + ground.slug(m.group(1)))
    e = _lark_leaf(s) or parse_effect(s)   # LARK-FIRST leaf (migrated families); regex leaf as fallback
    if e:
        return e
    m = _HAVE.match(s)             # causative 'have <X> <effect>' — FALLBACK (specific have-templates win
    if m:                         # first in parse_effect); reattach the subject so <X> performs the effect
        return parse_clause(f"{m.group(1)} {m.group(2)}")
    m = _IF_TRAIL.match(s)         # '<effect> if <condition>' — trailing conditional
    if m:
        return _combine(parse_clause(m.group(1)), ground.slug(m.group(2)), suffix=True)
    return None


def _combine(inner, cond, suffix=False):
    """Attach a wrapper condition to an already-parsed inner clause. If the inner clause already carries
    a condition (e.g. a nested 'you may'), keep both by joining them ('<inner>__if_<cond>' for the
    if-style wrappers, '<cond>__<inner>' otherwise) so no grounded condition is silently dropped."""
    if not inner:
        return None
    if inner.cond == "-":
        return _dc.replace(inner, cond=cond)
    joined = inner.cond + "__if_" + cond if suffix else cond + "__" + inner.cond
    return _dc.replace(inner, cond=joined)


if __name__ == "__main__":
    for s in ["Draw a card", "Draw two cards", "~ deals 3 damage to any target",
              "Destroy all creatures", "Destroy target creature", "You gain 3 life",
              "Target creature gets +1/+1 until end of turn", "Counter target spell",
              "Scry 2", "Target player draws two cards", "Exile target permanent",
              "Return target creature to its owner's hand", "Untap target creature",
              "Goad target creature"]:
        print(f"  {s:50} -> {parse_effect(s)}")
