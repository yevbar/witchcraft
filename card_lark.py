"""card_lark.py — a lark CFG for the card-effect clause sublanguage, transforming to the SAME
`Effect(verb, amount, target, extra, cond)` IR the regex templates produce.

This is the principled replacement for the accreted regex in `card_effects.py`: terse card clauses
are a *regular* sublanguage (not free English, which is why spaCy mis-roots them), so a context-free
grammar parses them deterministically and faithfully. The grammar owns the STRUCTURE (verb, object
span, prepositional zone, amount, wrapper conditions); the formal fragments (mana `{..}`, P/T `±n/±n`)
stay masked at the lexer level (the right place for a regular sublanguage); and the transformer reuses
`card_effects._target`/`_amount` so every slug is byte-identical to the regex output.

Built incrementally and OVERLAP-GATED against the regex (`card_effects.parse_clause`) corpus-wide: a
clause shape is only owned here once `lark(clause) == regex(clause)` on every card that grounds it.
"""
from __future__ import annotations

import re

from lark import Lark, Transformer, v_args

import ground
from card_effects import Effect, _target, _amount, _is_compound_object

# verbs whose grounded name == lemma (the simple object verbs); zone verbs handled separately.
# pure OBJECT verbs (the NP after the verb is the TARGET). Player-count verbs (mill/draw/discard/scry,
# where the NP is the AMOUNT and the subject is a player) are a separate production, added next.
_SIMPLE = {"destroy": "destroy", "exile": "exile", "tap": "tap", "untap": "untap",
           "sacrifice": "sacrifice", "counter": "counter", "regenerate": "regenerate",
           "goad": "goad", "detain": "detain"}
_ZONE = {"hand": "return_to_hand", "battlefield": "return_to_battlefield",
         "library": "put_on_top", "graveyard": "put_in_graveyard"}

# player-count verbs: the subject is a PLAYER and the NP after the verb is the AMOUNT. The trailing
# object word disambiguates the grounded verb (gain/lose need 'life'; draw/mill/discard need 'card[s]';
# scry/surveil take a bare number). Defaults subject to 'you' (imperative mood).
_PVERB = {"draw": "draw", "draws": "draw", "mill": "mill", "mills": "mill",
          "scry": "scry", "scries": "scry", "surveil": "surveil",
          "gain": "gain_life", "gains": "gain_life", "lose": "lose_life", "loses": "lose_life",
          "discard": "discard", "discards": "discard"}
_NEEDS_CARD = {"draw", "mill", "discard"}
_NEEDS_LIFE = {"gain_life", "lose_life"}

_GRAMMAR = r"""
start: rclause | oclause | pclause | dclause | mclause | cclause

rclause: RVERB quant? robj fromphrase? zonephrase? trailer?   -> ret   // 'return': strip from/to
oclause: OVERB quant? objall trailer?            -> imperative  // object verbs: object spans everything
pclause: psubj? PVERB pbody                       -> pcount      // player-count verbs: NP is the AMOUNT
dclause: dsrc DEALS damamt DMG TOPREP dtarget     -> deal        // '<source> deals N damage to <target>'
mclause: mtgt GETS PTDELTA mdur?                  -> boost       // '<target> gets +N/+N [duration]'
cclause: csubj? PUT ccount ckind COUNTER ONPREP ctarget   -> putctr  // 'put <N> <kind> counter(s) on <tgt>'

psubj: (WORD | QUANT)+                  // a player phrase before the verb (you / each player / target player)
pbody: (WORD | NUM | QUANT)+            // amount (+ object word: 'cards'/'life')
dsrc: (WORD | QUANT)+                   // damage source (DROPPED — implicit self, matching the regex)
damamt: NUM | QUANT | WORD             // single-token damage amount (N / X)
dtarget: (WORD | QUANT | NUM | ZONE)+   // target NP (no TOPREP: an internal 'to' -> abstain to regex)
mtgt: (WORD | QUANT)+                   // the creature getting the P/T boost
mdur: MDUR
csubj: (WORD | QUANT | NUM | ZONE)+     // a player phrase before 'put' (DROPPED — must be a clean player, else abstain)
ccount: THATMANY | QUANT | WORD | NUM   // the counter count: 'a'/'two'/'up to N'/'that many'/N/X/word
ckind: PTDELTA | ckwords               // the counter KIND: a P/T delta (kept verbatim) or word(s) -> slugged
ckwords: WORD+                          // kind words; can't cross COUNTER (own terminal) -> stops at the counter
// the object after 'on' spans to end but must NOT re-cross a structural 'counter'/'on'/'into'/'onto':
// the regex binds the FIRST 'counter on', so a clause with a second one ('… and a +1/+1 counter on Y',
// '… into your hand') is a run-on/zone-move -> no parse -> abstain to the regex (faithful-or-abstain).
ctarget: (WORD | QUANT | NUM | ZONE | PTDELTA | THATMANY)+   // object after 'on' (spans to end)

zonephrase: TOPREP zwords? ZONE        -> zone
fromphrase: FROM zwords? ZONE          -> source
zwords: (WORD | TOPREP)+
trailer: BOUND (WORD | TOPREP | ZONE | QUANT | NUM)*   -> trailer
quant: QUANT
robj: (WORD | ZONE)+                    // return object stops at from/to
objall: (WORD | TOPREP | ZONE | FROM)+

RVERB: "return"
OVERB: %(verbs)s
PVERB.2: /\b(?:draws|draw|mills|mill|scries|scry|surveil|gains|gain|loses|lose|discards|discard)\b/
DEALS.2: /\bdeals?\b/
DMG.2: /\bdamage\b/
GETS.2: /\bgets?\b/
PUT.3: /\bputs?\b/
COUNTER.4: /\bcounters?\b/
ONPREP.3: /\bon\b/
THATMANY.4: /\bthat many\b/
PTDELTA.4: /[+-](?:\d+|x)\/[+-](?:\d+|x)/
MDUR.3: /\b(?:until end of turn|until end of combat|until your next turn|until end of your next turn|this turn)\b/
QUANT.2: /\b(?:up to (?:one|two|three|four|five|that many|x|[0-9]+)|any number of|a|an|one|two|three|four|five|target|all|each|another|x)\b/
TOPREP.2: /\b(?:to|into|onto)\b/
FROM.2: /\bfrom\b/
ZONE.2: /\b(?:hand|battlefield|library|graveyard)\b/
BOUND.3: /\b(?:until|unless|for each)\b/
WORD: /[\w',+\/~*-]+/
NUM: /[0-9]+/

%%ignore /\s+/
"""


def _verb_alt():
    vs = sorted(_SIMPLE, key=len, reverse=True)
    return " | ".join('"%s"' % v for v in vs)


# rider patterns lark defers to the regex (its convention is better there): the structured
# 'exile the top N of <library>' form, and the suspend-style 'exile X with N counters on it'.
_TOPLIB = re.compile(r"^the top (?:\w+ )?cards? of .*librar(?:y|ies)$", re.I)
_WITHCTR = re.compile(r"\bwith \w+ [\w/+ ]*?counters? on it$", re.I)
_COORD = re.compile(r"^(?:or|and)\s", re.I)        # 'tap or untap …' — a coordinated verb the leaf split wrong
# an object that swallowed a following clause ('Destroy X, then ~ deals damage to Y') — the leaf must
# stop at the first verb; abstain so the upstream sentence-splitter / wrapper chain owns the sequence.
_MULTICLAUSE = re.compile(r"\bthen\b|\bdeals?\s+\S+\s+damage\b", re.I)

# 'return' abstain guards: an object-internal preposition ('attached to it', 'equal to X') or a
# coordinated multi-object list ('return A, B, and C to …') makes the flat from/to split ambiguous;
# defer to the regex (faithful-or-abstain). 'up to N' is a quant, not a dest prep, so strip it first.
_UPTOQ = re.compile(r"\bup to (?:one|two|three|four|five|that many|x|[0-9]+)\b", re.I)


# a player subject for the count verbs is a CLOSED phrase; anything else (a swallowed first clause,
# a 'may'/'who'/'unless' wrapper, a coordinated 'X and Y') means the greedy psubj over-matched -> abstain
# and let the regex wrapper/split chain own it (faithful-or-abstain).
_PLAYER = re.compile(
    r"^(?:~|you|they|it|them|"
    r"target player|target opponent|"
    r"each player|each opponent|each other player|"
    r"that player|that opponent|the player|another player|those players|these players|"
    r"(?:the )?(?:defending|attacking|active|chosen) player|"
    r"its controller|its owner|"
    r"[\w'-]+(?: [\w'-]+)*'s controller)$", re.I)


def _ret_ambiguous(s: str) -> bool:
    if "," in s:
        return True                            # coordinated multi-object list
    return len(re.findall(r"\b(?:to|into|onto)\b", _UPTOQ.sub(" ", s))) >= 2


_PARSER = Lark(_GRAMMAR % {"verbs": _verb_alt()}, parser="earley", lexer="dynamic")


class _Quant(str):
    pass


class _Source:
    def __init__(self, zone):
        self.zone = zone


class _Subj(str):
    pass


class _Body(str):
    pass


class _Amt(str):
    pass


class _Tgt(str):
    pass


class _Dur(str):
    pass


class _CSubj(str):
    pass


class _CCount(str):
    pass


class _CKind(str):
    pass


class _CTarget(str):
    pass


@v_args(inline=True)
class _ToEffect(Transformer):
    def quant(self, tok):
        return _Quant(str(tok))

    def obj(self, *toks):
        return " ".join(str(t) for t in toks)

    def robj(self, *toks):
        return " ".join(str(t) for t in toks)

    def objall(self, *toks):
        return " ".join(str(t) for t in toks)

    def zwords(self, *toks):
        return " ".join(str(t) for t in toks)

    def zone(self, prep, *rest):
        sub = " ".join(str(r) for r in rest).lower()        # zwords (optional) + ZONE
        z = next((zz for w, zz in _ZONE.items() if w in sub), None)
        return _Zone(z)

    def source(self, frm, *rest):
        sub = " ".join(str(r) for r in rest).lower()        # zwords (optional) + ZONE
        w = next((w for w in _ZONE if w in sub), None)
        return _Source(w)

    def trailer(self, *toks):
        # a trailing wrapper ('until ~ leaves', 'for each …') — the LEAF stops at the object; the
        # parse_clause wrapper chain handles the wrapper. Marker so imperative() drops it.
        return _Trailer()

    def _assemble(self, rest):
        if any(isinstance(a, _Trailer) for a in rest):
            return None, None, None            # trailing wrapper -> regex chain owns it
        quant = next((str(a) for a in rest if isinstance(a, _Quant)), None)
        zone = next((a for a in rest if isinstance(a, _Zone)), None)
        obj = next((a for a in rest if isinstance(a, str) and not isinstance(a, _Quant)), None)
        otext = (((quant + " ") if quant else "") + (obj or "")).strip()
        return quant, zone, otext

    def ret(self, verb, *rest):
        if any(isinstance(a, _Trailer) for a in rest):
            return None                        # trailing wrapper -> regex chain owns it
        quant = next((str(a) for a in rest if isinstance(a, _Quant)), None)
        zone = next((a for a in rest if isinstance(a, _Zone)), None)
        src = next((a for a in rest if isinstance(a, _Source)), None)
        robj = next((a for a in rest if isinstance(a, str) and not isinstance(a, _Quant)), None)
        if zone is None or zone.verb is None or robj is None:
            return None                        # 'return' needs a 'to <zone>' + object to ground
        otext = (((quant + " ") if quant else "") + robj).strip()
        # CONSISTENT (faithful-replacement): object stops at from/to; source -> extra; dest -> verb.
        extra = ("from_" + src.zone) if (src and src.zone) else "-"
        return Effect(zone.verb, "-", _target(otext), extra)

    def imperative(self, verb, *rest):
        verb = str(verb).lower()
        quant, _zone, otext = self._assemble(rest)
        if otext is None:
            return None
        if _TOPLIB.match(otext) or _WITHCTR.search(otext) or _COORD.match(otext) or _MULTICLAUSE.search(otext):
            return None                        # defer to the regex (better convention / coordinated verb)
        if verb == "sacrifice":
            n = _amount(quant) if quant in ("a", "an", "another", "two", "three") else None
            return Effect("sacrifice", n if isinstance(n, int) else "-", _target(otext))
        if verb in _SIMPLE:
            return Effect(_SIMPLE[verb], "-", _target(otext))
        return None

    def dsrc(self, *toks):
        return _Subj(" ".join(str(t) for t in toks))   # the source is dropped (reuse _Subj marker)

    def damamt(self, tok):
        return _Amt(str(tok))

    def dtarget(self, *toks):
        return _Tgt(" ".join(str(t) for t in toks))

    def deal(self, *args):
        amt = next((str(a) for a in args if isinstance(a, _Amt)), None)
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        if amt is None or tgt is None:
            return None
        n = _amount(amt)
        if n is None:
            return None                        # non-numeric amount ('that much' etc) -> regex variant
        tgt = tgt.strip().lower()
        if re.search(r"\b(?:and|then|gains?|draws?|loses?|deals?)\b", tgt) or "," in tgt:
            return None                        # coordinated/multi-clause target -> regex chain owns it
        return Effect("deal_damage", n, _target(tgt))

    def mtgt(self, *toks):
        return _Tgt(" ".join(str(t) for t in toks))

    def mdur(self, tok):
        return _Dur(str(tok))

    def boost(self, *args):
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        dur = next((str(a) for a in args if isinstance(a, _Dur)), None)
        pt = next((str(a) for a in args if re.match(r"^[+-]", str(a))), None)   # the P/T delta
        if tgt is None or pt is None:
            return None
        tgt = tgt.strip().lower()
        if _MULTICLAUSE.search(tgt) or "," in tgt:
            return None                        # multi-clause subject -> regex chain owns it
        perpetual = tgt.endswith(" perpetually")
        if perpetual:
            tgt = tgt[:-len(" perpetually")].strip()   # '<X> perpetually gets …' -> cond=perpetual
        cond = "-"
        if dur:
            if perpetual:
                return None                    # both a duration and 'perpetually' -> ambiguous, abstain
            d = dur.strip().lower()
            cond = "-" if d == "until end of turn" else ground.slug(d)
        elif perpetual:
            cond = "perpetual"
        return Effect("modify_pt", pt.replace(" ", "").upper(), _target(tgt), "-", cond)   # X stays uppercase

    def psubj(self, *toks):
        return _Subj(" ".join(str(t) for t in toks))

    def pbody(self, *toks):
        return _Body(" ".join(str(t) for t in toks))

    def pcount(self, *args):
        subj = next((str(a) for a in args if isinstance(a, _Subj)), None)
        body = next((str(a) for a in args if isinstance(a, _Body)), "")
        verb = next((str(a).lower() for a in args if not isinstance(a, (_Subj, _Body))), "")
        g = _PVERB.get(verb)
        if g is None:
            return None
        if subj is not None and not _PLAYER.match(subj.strip()):
            return None                        # greedy psubj swallowed non-player text -> abstain
        body = body.strip().lower()
        if "for each" in body or body.endswith("per turn") or " per " in body:
            return None                        # per-X amount or per-turn limit -> regex chain owns it
        who = _target(subj) if subj else "you"
        if g in _NEEDS_LIFE:
            if not body.endswith("life"):
                return None                    # 'gain control'/'gains flying' is NOT gain_life
            amt_s = body[:-4].strip()
        elif g in _NEEDS_CARD:
            if body.endswith("at random"):
                body = body[:-len("at random")].strip()   # regex drops 'at random'
            m = re.match(r"^(.*?)\s*cards?$", body)
            if not m:
                return None                    # 'discard their hand'/'discard your hand' -> other template
            amt_s = m.group(1).strip()
        else:                                  # scry / surveil — bare number, no object word
            amt_s = body
        if amt_s == "any number of":
            return Effect("discard", "any", who) if g == "discard" else None
        up_to = amt_s.startswith("up to ")
        if up_to:
            amt_s = amt_s[6:].strip()
        if amt_s in ("a", "an"):
            n = 1
        elif amt_s == "":
            return None
        else:
            n = _amount(amt_s)
        if n is None:
            return None
        if g == "discard":
            return Effect("discard", n, who, "up_to" if up_to else "-")
        if up_to:
            return None                        # 'up to N' only modeled for discard in the regex
        return Effect(g, n, who)

    def csubj(self, *toks):
        return _CSubj(" ".join(str(t) for t in toks))

    def ccount(self, tok):
        return _CCount(str(tok))

    def ckwords(self, *toks):
        return _CKind(" ".join(str(t) for t in toks))

    def ckind(self, tok):
        # PTDELTA arrives as a raw Token (no ckwords reduction); ckwords arrives already wrapped.
        return tok if isinstance(tok, _CKind) else _CKind(str(tok))

    def ctarget(self, *toks):
        return _CTarget(" ".join(str(t) for t in toks))

    def putctr(self, *args):
        subj = next((str(a) for a in args if isinstance(a, _CSubj)), None)
        count = next((str(a) for a in args if isinstance(a, _CCount)), None)
        kind = next((str(a) for a in args if isinstance(a, _CKind)), None)
        tgt = next((str(a) for a in args if isinstance(a, _CTarget)), None)
        if count is None or kind is None or tgt is None:
            return None
        # SUBJECT (regex's non-capturing '(?:<TGT> )?puts?' — DROPPED). Only own a clean player phrase;
        # a compound/wrapper subject ('each player chooses … and puts', 'may') -> abstain to the regex.
        if subj is not None:
            s = subj.strip().lower()
            if s.endswith(" may"):
                s = s[:-4].strip()                # the 'may' wrapper is stripped above the leaf in prod
            if not _PLAYER.match(s):
                return None
        count = count.strip().lower()
        kind = kind.strip()
        tgt = tgt.strip().lower()
        # The dynamic lexer can still re-lex a 'counter'/'into'/'onto' as a bare WORD inside the kind or
        # target span. The regex binds the FIRST 'counter on', so any such re-crossing means we mis-split
        # a multi-counter list ('your choice of a vigilance counter, …'), or a zone-move ('… counters on
        # them into your hand', '… onto the battlefield … counter on it') — abstain, the regex owns those.
        kl = kind.lower()
        if re.search(r"\bcounters?\b", kl) or re.search(r"\b(?:into|onto|battlefield|graveyard|library|hand)\b", kl):
            return None
        if "," in kind:
            return None                           # regex kind span ([\w ]+?) is comma-free -> abstain on lists
        if re.search(r"\b(?:into|onto)\b", tgt):
            return None
        # KIND: a P/T delta is kept verbatim (with the '/'); else slug the word(s). Regex's PTDELTA is
        # digits-only ([+-]\d+/[+-]\d+), so '+x/+x' falls through to its [\w ]+? branch and FAILS to
        # match -> abstain to stay faithful. A 'number of'/'same number' kind is the equal-to/copy form.
        if "/" in kind:
            if re.search(r"[a-z]", kind):
                return None                       # '+x/+x' etc. — regex abstains here
            kind_slug = kind
        else:
            if kind.startswith("number of") or "same number" in kind:
                return None                       # 'put a number of … equal to' / copy form -> regex
            kind_slug = ground.slug(kind)
        # COUNT -> amount, faithful to `_put_counter`/`_put_counter_many`. The regex count is a SINGLE
        # word (or 'up to <word>'); a multi-word QUANT ('any number of') the regex can't ground -> abstain.
        if " " in count and not count.startswith("up to ") and count != "that many":
            return None
        if count == "that many":
            if "/" not in kind:
                return None                       # 'that many <named>' -> regex's lossy 'many_<kind>'/X path
            amt = "that_amount"
        elif count.startswith("up to "):
            rest = count[6:].strip()
            a = _amount(rest)
            if a is None:
                return None                       # 'up to that many' etc. — regex can't ground it
            amt = "up_to_" + str(a)
        else:
            a = _amount(count)
            amt = a if a is not None else "X"
        # TARGET: a run-on object ('… and gain control of it') is a second effect; abstain so the
        # splitter owns it (faithful to `_put_counter`'s `_is_compound_object` guard). Also abstain on
        # a following clause the leaf must NOT swallow: a 'then'/'deals N damage' sequencer (the regex's
        # ordered templates ground that as the OTHER verb — `_MULTICLAUSE`), an ' and it <verb>' run-on
        # that `_is_compound_object` misses ('it' isn't a predicate-lead there), a ', where X is …' /
        # 'for each …' scaling appendix, and an ' or remove … counter' alternative.
        if _MULTICLAUSE.search(tgt) or _is_compound_object(tgt):
            return None
        if re.search(r"\band (?:it|they|you) \w", tgt):   # ' and it deals …' — a new clause the leaf split owns
            return None
        if ", where " in tgt or tgt.endswith(", where") or "for each " in tgt or " or remove " in tgt:
            return None
        return Effect("put_counter", amt, _target(tgt), kind_slug)


class _Zone:
    def __init__(self, verb):
        self.verb = verb


class _Trailer:
    pass


_T = _ToEffect()


def parse_clause_lark(clause: str):
    """A card-effect clause -> Effect via the CFG, or None (abstain). Case-folded; the grammar owns the
    imperative core + zone-moves so far."""
    s = clause.strip().rstrip(".").lower()
    if s.startswith("return ") and _ret_ambiguous(s):
        return None                            # ambiguous from/to split — defer to regex
    try:
        tree = _PARSER.parse(s)
    except Exception:
        return None
    e = _T.transform(tree)
    e = e.children[0] if hasattr(e, "children") else e
    return e if isinstance(e, Effect) else None
