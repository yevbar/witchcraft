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
from card_effects import Effect, _target, _amount, _is_compound_object, _kw_ok, _TGT

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
start: rclause | oclause | pclause | dclause | mclause | cclause | tclause | gclause | aclause
     | deqclause | dteqclause | dtmclause | ddivclause | bcmclause | chsclause | rvclause | pvclause

rclause: RVERB quant? robj fromphrase? zonephrase? trailer?   -> ret   // 'return': strip from/to
oclause: OVERB quant? objall trailer?            -> imperative  // object verbs: object spans everything
pclause: psubj? PVERB pbody                       -> pcount      // player-count verbs: NP is the AMOUNT
dclause: dsrc DEALS damamt DMG TOPREP dtarget     -> deal        // '<source> deals N damage to <target>'
mclause: mtgt GETS PTDELTA mdur?                  -> boost       // '<target> gets +N/+N [duration]'

// deal_damage VARIANTS (the basic dclause abstains on these — they lack a single-token amount before
// 'damage', or carry an 'equal to <amount>' / 'that much' / 'divided' rider). The discriminator is
// purely lexical: 'damage equal to … to …' (amount-first) vs 'damage to … equal to …' (target-first)
// are disjoint by whether EQUALTO precedes or follows the 'to <target>' prep phrase.
deqclause: dsrc DEALS DMG EQUALTO deqamt TOPREP dteqtgt          -> deal_eq   // 'deals damage equal to <amt> to <tgt>'
dteqclause: dsrc DEALS DMG TOPREP dteqtgt EQUALTO dvamt          -> deal_teq  // 'deals damage to <tgt> equal to <amt>'
dtmclause: dsrc DEALS THATMUCH DMG TOPREP dtarget               -> deal_tm   // 'deals that much damage to <tgt>'
ddivclause: dsrc DEALS damamt DMG DIVIDED ddivtgt              -> deal_div  // 'deals N damage divided as you choose among <tgts>'

gclause: gtgt? GVERB gkw mdur?                    -> grant       // '<target> gains/has <KEYWORD> [duration]'
aclause: gtgt GVERB QUOTED mdur?                  -> grant_ab    // '<target> has/gains "<ability>" [duration]'

// CHOOSE family (§700.2). Bare imperative 'choose <quant> <thing>' only; the regex `_choose` DROPS
// any leading subject, so subject-prefixed forms are deferred to the regex (no leading-subject rule
// here — abstaining is safe and avoids the greedy subject-swallow ambiguity). The chosen thing spans
// to end as opaque tokens (rejoined to text and slugged, like the regex `(.+?)$`). 'choose' folds the
// quant exactly: {a,an,one} -> q="", else q=slug(quant)+"_".
chsclause: CHS_CHOOSE chsquant chsrest            -> chs
chsquant: QUANT                                   // reuse the shared QUANT terminal (no new quant terminal)
chsrest: chstok+                                  // the chosen-thing NP, opaque to end (rejoined + slugged)
chstok: WORD | NUM | QUANT | TOPREP | FROM | ZONE | EQUALTO | THATMANY | ONPREP | COUNTER
      | DEALS | DMG | GETS | GVERB | PVERB | PUT | TOKEN | DIVIDED | THATMUCH | PTDELTA | MDUR | CCOUNT
cclause: csubj? PUT ccount ckind COUNTER ONPREP ctarget   -> putctr  // 'put <N> <kind> counter(s) on <tgt>'
tclause: ccreator? CVERB CCOUNT cspec TOKEN cforeach? ctail?  -> create  // 'create N <spec> token[s] [for each X]'

// BECOMES (the dominant 'animate to a N/N' shape): '<tgt> becomes/is/are [a] N/N <typetail>
// [with <kw>] [until end of turn]'. We own ONLY this P/T-bearing shape (the `_becomes` template);
// copy/color/base-pt/added/type variants stay with the regex (faithful-or-abstain). The whole
// post-P/T span is captured raw and the regex's non-greedy g3 (type tail) is reconstructed exactly
// in the transformer (`_bcm_g3`) — the 'with <kw>' and a trailing 'until end of turn' are stripped.
bcmclause: bcmtgt BCM_COP quant? BCM_PT bcmtail?   -> bcmbecomes

bcmtgt: (WORD | QUANT | NUM)+            // the permanent receiving the animate (stops at the copula)
bcmtail: (WORD | QUANT | NUM | PTDELTA | TOPREP | FROM | ZONE | GETS | DEALS | DMG | MDUR | TOKEN | BCM_PT | BCM_COP | EQUALTO | COUNTER | ONPREP)+  -> bcmtail  // raw post-P/T span

// REVEAL — the structured 'reveal the top N cards of <owner> library' (imperative or subject-form) and
// 'reveals their hand'. The clause body is captured as a flat token run and re-joined in the transformer,
// which applies the same fixed-frame regex the templates use (faithful by construction) and slugs the
// owner/subject via card_effects._target. A subject before the verb is allowed (closed _PLAYER phrase).
// rvclause/pvclause carry NEGATIVE rule priority so that when a sentence is ALSO parseable as another
// family (e.g. 'Whenever you reveal …, <src> deals N damage to <tgt>' — really a deal_damage clause
// whose 'reveal' sits in a leading wrapper that the regex leaf swallows into dsrc), Earley's ambiguity
// resolver prefers the competing (deal/…) parse, leaving the reveal/prevent grounding to the cases
// where it is the ONLY parse. On a pure 'reveal the top …' / 'prevent the next …' clause there is no
// competitor, so these rules still win. (The transformer additionally abstains on any non-frame body.)
rvclause.-2: rvsubj? RVREVEAL rvbody       -> reveal
rvsubj: (WORD | QUANT | NUM | ZONE)+        // player phrase before 'reveals' (validated as _PLAYER)
rvbody: (WORD | QUANT | NUM | ZONE | TOPREP | FROM | EQUALTO | THATMANY)+

// PREVENT_DAMAGE — the two clean dominant frames: 'prevent the next N damage that would be dealt
// [this turn] to <target> [this turn]' (_prevent) and 'prevent all [combat] damage that would be dealt
// this turn' (_fog). Body captured flat and parsed by the same frame regexes in the transformer.
pvclause.-2: PVPREVENT pvbody               -> prevent
pvbody: (WORD | QUANT | NUM | ZONE | TOPREP | FROM | DMG | EQUALTO | THATMANY | MDUR)+

ccreator: (WORD | QUANT)+               // optional creator player phrase ('target opponent creates …')
cspec: (WORD | NUM)+                    // the token descriptor (P/T + colors + types) up to 'token[s]'
cforeach: FOREACH cfeword               // 'for each <X>' — regex keeps only the FIRST word of X
cfeword: WORD | NUM | QUANT | PTDELTA    // first word may be a '+1/+1' counter kind (lexed as PTDELTA)
ctail: (WORD | NUM | QUANT | TOPREP | FROM | ZONE | DEALS | DMG | GETS | PTDELTA | TOKEN | MDUR)*  -> ctail  // dropped (regex's trailing '.*')

psubj: (WORD | QUANT)+                  // a player phrase before the verb (you / each player / target player)
pbody: (WORD | NUM | QUANT)+            // amount (+ object word: 'cards'/'life')
dsrc: (WORD | QUANT)+                   // damage source (DROPPED — implicit self, matching the regex)
damamt: NUM | QUANT | WORD             // single-token damage amount (N / X)
dtarget: (WORD | QUANT | NUM | ZONE | EQUALTO)+   // basic target NP (no TOPREP: internal 'to' -> abstain). 'equal to' stays content here.
dteqtgt: (WORD | QUANT | NUM | ZONE)+   // variant target NP — stops at TOPREP and at EQUALTO (the rider boundary)
deqamt: (WORD | QUANT | NUM | ZONE)+    // amount-first amount: stops at the FIRST 'to' (regex non-greedy); an internal ' to ' -> won't parse -> abstain
dvamt: (WORD | QUANT | NUM | ZONE | TOPREP | EQUALTO)+   // target-first amount: runs to end of string
ddivtgt: (WORD | QUANT | NUM | ZONE | TOPREP | EQUALTO)+ // divided targets: run to end (grounded raw, like the regex)
mtgt: (WORD | QUANT)+                   // the creature getting the P/T boost
mdur: MDUR
gtgt: (WORD | QUANT | NUM)+             // the permanent/player receiving the grant (stops at gains/has/have)
gkw: WORD (WORD | NUM | QUANT | TOPREP | FROM | ZONE)*   // keyword phrase: first token a plain WORD (so 'gains 3 life' -> pcount, not here)
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
robj: (WORD | ZONE | EQUALTO)+          // return object stops at from/to; 'equal to' stays content
objall: (WORD | TOPREP | ZONE | FROM | EQUALTO)+   // object verbs: 'equal to' stays content ('destroy each … equal to N')

RVERB: "return"
OVERB: %(verbs)s
CVERB.3: /\bcreates?\b/
CCOUNT.3: /\b(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|[0-9]+)\b/
TOKEN.4: /\btokens?\b/
FOREACH.4: /\bfor each\b/
PVERB.2: /\b(?:draws|draw|mills|mill|scries|scry|surveil|loses|lose|discards|discard)\b/
RVREVEAL.3: /\breveals?\b/
PVPREVENT.3: /\bprevent\b/
DEALS.2: /\bdeals?\b/
DMG.2: /\bdamage\b/
GETS.2: /\bgets?\b/
GVERB.3: /\b(?:gains?|has|have)\b/
QUOTED.5: /"[^"]*"/                    // a quoted ability (bounded — an unanchored .* poisons the dynamic lexer)
CHS_CHOOSE.3: /\bchooses?\b/         // 'choose'/'chooses' — the §700.2 choice verb (namespaced; below DIVIDED's 'choose')
PUT.3: /\bputs?\b/
COUNTER.4: /\bcounters?\b/
ONPREP.3: /\bon\b/
THATMANY.4: /\bthat many\b/
PTDELTA.4: /[+-](?:\d+|x)\/[+-](?:\d+|x)/
BCM_PT.5: /(?:[0-9]|x|\*)+\/(?:[0-9]|x|\*)+/   // a set base P/T ('2/1','x/x','*/*') — regex `[\dX*]+/[\dX*]+` (input is lowercased). Outranks WORD so the P/T slot is unambiguous.
BCM_COP.4: /\b(?:becomes?|are|is)\b/             // the becomes/is/are copula (the optional 'a/an' reuses QUANT, not a new terminal)
MDUR.3: /\b(?:until end of turn|until end of combat|until your next turn|until end of your next turn|this turn)\b/
DIVIDED.4: /\bdivided as you choose among\b/
THATMUCH.4: /\bthat much\b/
EQUALTO.3: /\bequal to\b/
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


# a grant keyword phrase is FAITHFUL only if it is a single clean §702 keyword (optionally with its
# standard 'from/of' qualifier or a numeric parameter). The regex `_gains_perm` greedily slugs a
# trailing duration/condition ('flying as long as …', 'haste until your next turn') or a multi-keyword
# list ('hexproof and indestructible') into ONE extra — a LOSSY tuple. We ABSTAIN on those (the slug
# would carry clause-junk), keeping only the clean grant; the regex fallback owns the lossy whole.
_KW_LOSSY = re.compile(r"_until_|_as_long_as_|_this_turn|_this_combat|_during_|_for_as_long|"
                       r"_unless_|_whenever_|_if_|_as_though_|_where_|_permanently$|_and_")


# A faithful grant target is a NOUN PHRASE. Two ways the greedy `gtgt` over-matches and must abstain:
#  (1) it binds to a clause-internal 'has/have' ("…didn't HAVE defender", "…as long as it HAS flying"),
#      leaving verb material in the target;
#  (2) the leaf is fed a sentence whose leading wrapper the production chain would have peeled but here
#      hasn't ("Until end of turn, <t> gains …", "If …, <t> gains …", "When you do, … and it gains …"),
#      so the target swallows a whole preceding clause.
# Either way the target carries clause-level material no `_TGT` noun phrase holds -> abstain (the regex
# leaf abstains too; in production the wrapper chain peels the lead and the clean residue re-enters).
_TGT_BAD = re.compile(
    r"^(?:until |if |when |whenever |at |for each|as you |after |during your turn\b)|"   # swallowed lead
    r",|"                                                                                 # comma = two clauses
    r"\b(?:has|have|gains?|gets?|can|could|can't|cannot|doesn't|didn't|assigns?|is|are|"
    r"becomes?|loses?|create|creates?|return|returns?|exile|exiles?|put|puts?|destroy|"
    r"destroys?|draws?|deals?|sacrifices?|adds?|attacks?|blocks?)\b|"                      # embedded verb
    r"\bas long as\b|\bas though\b|\bin addition\b|\band they\b|\band it\b|\bexcept\b|\balso\b",
    re.I)


def _clean_kw(extra: str) -> bool:
    """True iff `extra` (a slugged keyword grant) is a single faithful §702 keyword, not a lossy
    duration/condition tail or a crammed multi-keyword list."""
    if _KW_LOSSY.search(extra):
        return False                           # duration/condition tail or 'kw and kw' list -> lossy
    if "_or_" in extra and not extra.startswith("protection_from"):
        return False                           # 'kw or kw' list (protection's own 'or from' is fine)
    return True


def _ret_ambiguous(s: str) -> bool:
    if "," in s:
        return True                            # coordinated multi-object list
    return len(re.findall(r"\b(?:to|into|onto)\b", _UPTOQ.sub(" ", s))) >= 2


# the regex `_becomes` type tail is `([\w' -]*?)(?: with [\w, ]+?)?(?: until end of turn)?$` — a
# NON-GREEDY g3. We reconstruct it EXACTLY: g3 is the shortest prefix of the post-P/T span T whose
# every consumed char is in `[\w' -]` (so it can't cross a comma) such that the remainder matches
# the optional ' with <kw>' + trailing ' until end of turn'. Returns None if no such split (the
# clause isn't the clean pt shape — abstain). Validated tuple-identical on every pt clause.
_BCM_G3CHAR = re.compile(r"[\w' -]")
_BCM_REM = re.compile(r"^(?: with [\w, ]+?)?(?: until end of turn)?$", re.I)
# The greedy `bcmtgt` can swallow a leading wrapper ('Until end of turn, …', 'If …, it', 'you may
# have …') that the regex's anchored `_TGT` would never match — grounding those is LOSSY (wrong
# target). Gate the target on the EXACT `_TGT` noun-phrase regex: only own clauses whose subject is a
# legitimate `_TGT` (faithful-or-abstain). In production the wrapper chain peels the lead and re-feeds
# the clean residue, so abstaining here loses nothing.
from card_effects import _TGT as _BCM_TGT_SRC
_BCM_TGT = re.compile(r"(?:" + _BCM_TGT_SRC + r")$", re.I)


def _bcm_g3(tail: str):
    n = len(tail)
    for i in range(0, n + 1):
        if i > 0 and not _BCM_G3CHAR.match(tail[i - 1]):
            break                              # g3 (`[\w' -]*`) can't include this char -> stop
        if _BCM_REM.match(tail[i:]):
            return tail[:i]
    return None


# 'gain control of <X>' (§720 control-change) is owned by the grant rule's gc-branch (gkw starts
# with 'control'). The regex templates `_control`/`_control_subj` recognise EXACTLY two trailing
# durations — ' until end of turn' and ' for as long as …' (the latter slurps to end) — and leave
# every OTHER duration ('until your next turn', 'this turn', 'during their next turn') inside the
# object span. `_gc_dursplit` reproduces that split byte-for-byte: it folds any lark-lexed MDUR back
# into the object first (only the two recognised forms are peeled), so the slugged object/target and
# the duration extra are identical to the regex.
def _gc_dursplit(obj_full: str):
    """(object_text, duration_extra) for a 'control of <object_full>' span, faithful to the regex."""
    s = obj_full.strip()
    i = s.find(" for as long as ")
    if i >= 0:
        return s[:i].strip(), ground.slug(s[i:])          # ' for as long as …' -> slug to end
    if s.endswith(" until end of turn"):
        return s[:-len(" until end of turn")].strip(), "until_end_of_turn"
    return s, "-"


# the regex `_control`/`_control_subj` only ground when the controlled OBJECT (and, for the subject
# form, the gaining player) match the `_TGT` noun-phrase pattern anchored to the span — a comma, a
# trailing clause ('… instead if you control …'), an anaphor-name ('Starke'), or a run-on coordinated
# imperative ('untap target creature and gain control of it' — the regex leaf grounds that as UNTAP,
# an earlier template) all FAIL `_TGT`. We validate the raw span against the same pattern so lark
# grounds gain_control on EXACTLY the clauses the regex does, and abstains everywhere else.
_GC_TGT = re.compile(r"^(?:" + _BCM_TGT_SRC + r")$", re.I)


# CHOOSE family. The EXACT quantifier set the regex `_choose` alternation `(a|an|one|two|three|up to
# \w+|one or more|any number of|another|target|the)` SELECTS, minus alternatives it never reaches
# ('one or more' is shadowed by 'one') and minus 'the'/'up to <non-numeric>' (rare / split-divergent).
_CHS_UPTO = {"one", "two", "three", "four", "five", "x"}   # 'up to <N>' the shared QUANT can also yield
_CHS_QUANTS = ({"a", "an", "one", "two", "three", "another", "target", "any number of"}
               | {"up to " + n for n in _CHS_UPTO})
# 'choose <obj>' is grounded by TWO regex templates in PRECEDENCE order: the generic `_verb_target`
# (`^(\w+) (<_TGT>)$`) fires FIRST whenever the FULL object is a `_TGT` noun phrase, slugging it WHOLE
# (article kept) via `_target`; only otherwise does `_choose` fire, folding the article (a/an/one -> "").
# So an object that is itself a `_TGT` keeps its leading 'a/an/one'; one that isn't drops it. Mirror that.
_CHS_TGT = re.compile(r"(?i)^(?:" + _TGT + r")$")


# REVEAL frame regexes — the EXACT fixed frames of the regex templates this rule replaces. The lark rule
# only certifies the clause starts with 'reveal[s]' (and optionally a player subject); the faithful body
# parse is these frames, so the grounded tuple is byte-identical to `_reveal_top`/`_subject_reveal_top`/
# `_reveal_hand`. Anything outside these frames (the open-ended `_reveal_generic`/`_reveal_among`/
# `_subject_obj_verb` slugs) is left to the regex (abstain) — faithful-or-abstain.
_RV_TOP = re.compile(r"^the top (?:(\w+) )?cards? of ([\w' ]+?) librar(?:y|ies)$", re.I)          # _reveal_top
_RV_SUBJ_TOP = re.compile(r"^the top (?:(\w+) )?cards? of (?:their|its owner's|your) library$", re.I)  # _subject_reveal_top body
_RV_SUBJ_HAND = re.compile(r"^their hand$", re.I)                                                  # _reveal_hand body

# PREVENT_DAMAGE frame regexes — the clean dominant frames (`_prevent`, `_fog`). The variable-scope
# `_prevent_all_scoped`, the consequent `_prevent_that`, and the `_prevent_next_source` shield carry
# open-ended `.+?` slugs; they stay with the regex (abstain).
_PV_FOG = re.compile(r"^all (combat )?damage that would be dealt this turn$", re.I)                # _fog body
_PV_NEXT = re.compile(r"^the next (\w+) damage that would be dealt (?:this turn )?to (.+)$", re.I)  # _prevent body


_PARSER = Lark(_GRAMMAR % {"verbs": _verb_alt()}, parser="earley", lexer="dynamic")


class _Quant(str):
    pass


class _ChsQuant(str):     # the choose-family quantifier (folded into the chosen-thing slug)
    pass


class _ChsRest(str):      # the chosen-thing noun phrase (opaque, slugged like the regex `(.+?)$`)
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


class _DTgt(str):      # a deal-variant target (amount-first / target-first 'equal to' forms)
    pass


class _EqAmt(str):     # an 'equal to <amount>' span (either ordering)
    pass


class _DivTgt(str):    # the 'divided as you choose among <targets>' span (raw-slugged)
    pass


class _RvSubj(str):    # a player subject before 'reveal[s]' (validated against _PLAYER)
    pass


class _RvBody(str):    # the reassembled reveal clause body (everything after 'reveal[s]')
    pass


class _PvBody(str):    # the reassembled prevent clause body (everything after 'prevent')
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


class _BcmTgt(str):       # the becomes target NP (the permanent being animated)
    pass


class _BcmTail(str):      # the raw post-P/T span (kept only so the parse consumes it; g3 is sliced)
    pass


@v_args(inline=True)
class _ToEffect(Transformer):
    def quant(self, tok):
        return _Quant(str(tok))

    # --- CHOOSE family --------------------------------------------------------
    def chstok(self, tok):
        return str(tok)

    def chsquant(self, tok):
        return _ChsQuant(str(tok))

    def chsrest(self, *toks):
        return _ChsRest(" ".join(str(t) for t in toks))

    def chs(self, *args):
        quant = next((str(a) for a in args if isinstance(a, _ChsQuant)), None)
        rest = next((str(a) for a in args if isinstance(a, _ChsRest)), None)
        if quant is None or rest is None:
            return None
        quant = quant.strip().lower()
        rest = rest.strip()
        # Only own the quantifiers the regex `_choose` alternation actually selects (case-folded). The
        # shared QUANT terminal is broader (four/five/all/each/x, 'up to that many'); restricting here
        # keeps us identical-or-abstain — anything else defers to the regex.
        if quant not in _CHS_QUANTS:
            return None                            # 'four'/'five'/'all'/'x'/'up to that many' etc. -> regex
        if not rest:
            return None                            # empty chosen-thing (the modal 'choose one —' has '—')
        # The regex's `(.+?)` is opaque text we rejoin from tokens; a char outside the WORD class
        # (em-dash bullet of a MODAL list, a `{..}` mana symbol, a comma list) won't tokenize here ->
        # abstain (defer to the regex / unit-handler layer, per the directive's modal carve-out).
        full = (quant + " " + rest).strip()
        if _is_compound_object(full) or _is_compound_object(rest):
            return None                            # run-on second effect ('… then …', '… and <verb> …')
        if _CHS_TGT.match(full):
            # `_verb_target` precedence: the WHOLE object is a `_TGT` -> slug it via `_target`, article kept.
            return Effect("choose", "-", _target(full))
        q = "" if quant in ("a", "an", "one") else ground.slug(quant) + "_"
        return Effect("choose", "-", q + ground.slug(rest))

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

    # --- deal_damage VARIANTS -------------------------------------------------
    # span markers (distinct classes so the variant transformers pick the right slot)
    def dteqtgt(self, *toks):
        return _DTgt(" ".join(str(t) for t in toks))

    def deqamt(self, *toks):
        return _EqAmt(" ".join(str(t) for t in toks))

    def dvamt(self, *toks):
        return _EqAmt(" ".join(str(t) for t in toks))

    def ddivtgt(self, *toks):
        return _DivTgt(" ".join(str(t) for t in toks))

    def _coord(self, s: str) -> bool:
        # a coordinated / multi-clause span the regex would slug whole (lossy) — abstain (faithful-or-abstain)
        return "," in s or bool(re.search(r"\b(?:and|then|gains?|draws?|loses?)\b", s))

    def _tgt_wrapped(self, s: str) -> bool:
        # the variant target swallowed a trailing wrapper / conditional clause ('… unless that player
        # sacrifices it', '… if you do') — the regex's constrained _TGT wouldn't reach here; abstain.
        return bool(re.search(r"\b(?:unless|if|until|whenever|where)\b", s))

    def deal_eq(self, *args):           # 'deals damage equal to <amt> to <tgt>'
        amt = next((str(a) for a in args if isinstance(a, _EqAmt)), None)
        tgt = next((str(a) for a in args if isinstance(a, _DTgt)), None)
        if amt is None or tgt is None:
            return None
        amt, tgt = amt.strip(), tgt.strip()
        if not amt or not tgt or self._coord(amt) or self._coord(tgt) or self._tgt_wrapped(tgt):
            return None
        if re.search(r"\b(?:to|into|onto)\b", amt):
            return None                 # deqamt swallowed a 'to' as a WORD -> the split is wrong -> abstain
        return Effect("deal_damage", "equal_to_" + ground.slug(amt), _target(tgt))

    def deal_teq(self, *args):          # 'deals damage to <tgt> equal to <amt>'
        amt = next((str(a) for a in args if isinstance(a, _EqAmt)), None)
        tgt = next((str(a) for a in args if isinstance(a, _DTgt)), None)
        if amt is None or tgt is None:
            return None
        amt, tgt = amt.strip(), tgt.strip()
        if tgt == "itself":
            return None                 # 'X deals damage to itself equal to Y' is _damage_self (source-as-target); abstain
        if not amt or not tgt or self._coord(amt) or self._coord(tgt) or self._tgt_wrapped(tgt):
            return None
        return Effect("deal_damage", "equal_to_" + ground.slug(amt), _target(tgt))

    def deal_tm(self, *args):           # 'deals that much damage to <tgt>'
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        if tgt is None:
            return None
        tgt = tgt.strip().lower()
        if not tgt or self._coord(tgt):
            return None
        return Effect("deal_damage", "that_amount", _target(tgt))

    def deal_div(self, *args):          # 'deals N damage divided as you choose among <tgts>'
        amt = next((str(a) for a in args if isinstance(a, _Amt)), None)
        tgt = next((str(a) for a in args if isinstance(a, _DivTgt)), None)
        if amt is None or tgt is None:
            return None
        amt, tgt = amt.strip(), tgt.strip()
        if not tgt:
            return None
        # the regex slugs the divided-target OPAQUELY (no coordination split), so 'and/or' lists are
        # faithful here; only a genuine following clause ('then …') would be lossy (the splitter removed
        # those already) — mirror the regex exactly.
        if re.search(r"\bthen\b", tgt):
            return None
        n = _amount(amt)
        amount = n if n is not None else ground.slug(amt)
        return Effect("deal_damage", amount, ground.slug(tgt), "divided")   # target RAW-slugged (matches regex)

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

    def ccreator(self, *toks):
        return _Creator(" ".join(str(t) for t in toks))

    def cspec(self, *toks):
        return _Spec(" ".join(str(t) for t in toks))

    def cfeword(self, tok):
        return _FEWord(str(tok))

    def cforeach(self, _fe, word):
        return word                              # pass the _FEWord up (the FOREACH literal is dropped)

    def ctail(self, *toks):
        return _CTail(" ".join(str(t) for t in toks))

    def create(self, *args):
        creator = next((a for a in args if isinstance(a, _Creator)), None)
        spec = next((str(a) for a in args if isinstance(a, _Spec)), None)
        fe = next((str(a) for a in args if isinstance(a, _FEWord)), None)
        count = next((str(a).lower() for a in args
                      if not isinstance(a, (_Creator, _Spec, _FEWord, _CTail))
                      and re.fullmatch(r"(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|[0-9]+)",
                                       str(a).lower())), None)
        if spec is None or count is None:
            return None
        sl = spec.lower()
        # 'create a number of <spec> tokens equal to <X>' is the count-scaled _create_equal template
        # (the count word is 'a' and the spec starts 'number of …') — defer to the regex.
        if sl.startswith("number of"):
            return None
        # 'create a token that's a copy of …' / 'X tokens that are copies of …' is the _create_copy
        # template; here cspec greedily ran past the real boundary to a LATER 'token' (e.g. 'artifact
        # token you control') and would emit a garbled spec — defer to the regex copy handler.
        if re.search(r"\b(?:token|tokens|copy|copies)\b", sl):
            return None
        # A comma or 'then' in the trailing remainder signals a multi-token list ('… token, a 2/2 …')
        # or a sequenced second effect ('… token, then draw …') — the regex crams the whole run into
        # the spec (lossy), and lark dropping it would drop conjuncts. Abstain: regex chain owns these.
        tail = next((str(a) for a in args if isinstance(a, _CTail)), "")
        if "," in tail or re.search(r"\bthen\b", tail):
            return None
        # CREATOR: closed player allow-list (reuse _PLAYER) — a greedy non-player prefix -> abstain.
        cre = None
        if creator is not None:
            c = creator.strip().lower()
            if c == "you":
                cre = None                       # 'you' is the default controller (regex: cond='-')
            elif _PLAYER.match(c):
                cre = c
            else:
                return None                      # non-player creator phrase -> regex chain owns it
        n = _amount(count)
        amt = n if n is not None else "X"        # _amount('x') -> 'X'; digits/number words -> int
        if fe is not None:
            amt = f"{amt}_per_{ground.slug(fe)}"  # regex keeps only the first word of 'for each X'
        cond = ("creator_" + _target(cre)) if cre else "-"
        return Effect("create", amt, "token", ground.slug(spec), cond)

    def gtgt(self, *toks):
        return _Tgt(" ".join(str(t) for t in toks))

    def gkw(self, *toks):
        return _Body(" ".join(str(t) for t in toks))     # reuse _Body marker for the keyword phrase

    def _grant_dur(self, tgt, dur):
        """Resolve the (amount, cond, target-text) for a grant, handling the EOT duration and a trailing
        'perpetually' adverb (cond=perpetual, mirroring `boost`). Returns None on an unsupported/ambiguous
        duration so the regex fallback owns it; else (amount, cond, tgt_text)."""
        t = (tgt.strip() if tgt else "")
        perpetual = t.lower().endswith(" perpetually")
        if perpetual:
            t = t[:-len(" perpetually")].strip()       # '<X> perpetually gains …' -> cond=perpetual
        amount, cond = "-", "-"
        if dur is not None:
            if perpetual or dur.strip().lower() != "until end of turn":
                return None                            # other duration (regex slugs it, lossy) / both -> abstain
            amount = "until_end_of_turn"
        elif perpetual:
            cond = "perpetual"
        return amount, cond, t

    def grant(self, *args):
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        phrase = next((str(a) for a in args if isinstance(a, _Body)), None)
        dur = next((str(a) for a in args if isinstance(a, _Dur)), None)
        if phrase is None:
            return None
        # 'gain(s) <amount> life' is gain_life — the 'gain(s)' verb is now owned by this rule (removed
        # from PVERB to kill the pcount<->grant ambiguity). Reproduce pcount's gain_life tuple exactly;
        # abstain on a duration/perpetual or a non-player subject (pcount's domain handles only those).
        toks = phrase.strip().lower().split()
        if toks and toks[-1] == "life":
            amt_s = " ".join(toks[:-1]).strip()
            n = 1 if amt_s in ("a", "an") else (None if amt_s == "" else _amount(amt_s))
            if n is None or dur is not None:
                return None
            t = tgt.strip().lower() if tgt else ""
            if t and (t.endswith(" perpetually") or _TGT_BAD.search(t) or not _PLAYER.match(t)):
                return None
            return Effect("gain_life", n, _target(t) if t else "you")
        # 'gain(s) control of <X> [until end of turn | for as long as …]' is §720 gain_control. The
        # 'gain'/'gains' verb routes here (removed from PVERB), and gkw greedily captures 'control of …'
        # (so `_kw_ok` returns None below) — own that shape here, reproducing `_control`/`_control_subj`.
        if toks and toks[0] == "control":
            verb = next((str(a).lower() for a in args if not isinstance(a, (_Tgt, _Body, _Dur))), "")
            obj_full = " ".join(toks[1:])
            if obj_full.startswith("of "):                # '(?:of )?' in the regex
                obj_full = obj_full[3:]
            if dur is not None:
                obj_full = (obj_full + " " + dur.strip()).strip()   # fold MDUR back; gc-split re-peels
            obj, dur_extra = _gc_dursplit(obj_full)
            if not obj or not _GC_TGT.match(obj):
                return None                              # object isn't a clean <TGT> noun phrase -> abstain
            tt = tgt.strip().lower() if tgt else None
            # SHAPE A ('_control'): implicit/you subject, no 'by_'. The regex's leading '(?:you )?' is a
            # LITERAL prefix, reached only when there is no subject OR the subject is exactly 'you' and the
            # verb is 'gain' (regex needs the literal 'gain '; 'you gains …' falls through to _control_subj).
            if tt is None or (tt == "you" and verb == "gain"):
                return Effect("gain_control", "-", _target(obj), dur_extra)
            # SHAPE B ('_control_subj'): '<subject> gains? control of <X> [dur]' -> extra='by_<subject>',
            # cond=duration. The gaining player must be a clean §720 controller phrase — `_PLAYER` (a
            # closed allow-list) rejects a greedy gtgt that swallowed a run-on coordinated imperative
            # ('untap … and'), a 'may' wrapper, or a compound 'X and Y each' subject (all of which the
            # regex leaf would ground as a DIFFERENT verb or not at all) -> abstain. (Faithful: a couple
            # of exotic but real subjects — 'target opponent chosen at random' — also fall here; the
            # regex fallback still owns them.)
            if not _PLAYER.match(tt):
                return None
            return Effect("gain_control", "-", _target(obj), "by_" + _target(tt), dur_extra)
        kw = _kw_ok(phrase.strip())
        if not kw or not _clean_kw(kw):
            return None                          # not a clean single §702 keyword grant -> abstain
        res = self._grant_dur(tgt, dur)
        if res is None:
            return None
        amount, cond, ttext = res
        if ttext and _TGT_BAD.search(ttext):
            return None                          # target carries clause material (over-match) -> abstain
        who = _target(ttext) if ttext else _target("~")
        return Effect("grant_keyword", amount, who, kw, cond)

    def grant_ab(self, *args):
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        quoted = next((str(a) for a in args
                       if not isinstance(a, (_Tgt, _Dur)) and str(a).startswith('"')), None)
        dur = next((str(a) for a in args if isinstance(a, _Dur)), None)
        if tgt is None or quoted is None or len(quoted) < 2:
            return None
        res = self._grant_dur(tgt, dur)
        if res is None:
            return None
        amount, cond, ttext = res
        if not ttext or _TGT_BAD.search(ttext):
            return None                          # empty / clause-laden target -> abstain
        ab = ground.slug(quoted[1:-1])[:160]      # strip the surrounding quotes (regex '"(.+)"')
        if not ab:
            return None
        return Effect("grant_ability", amount, _target(ttext), ab, cond)

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

    def bcmtgt(self, *toks):
        return _BcmTgt(" ".join(str(t) for t in toks))

    def bcmtail(self, *toks):
        return _BcmTail(" ".join(str(t) for t in toks))    # value unused; presence consumes the span

    def bcmbecomes(self, *args):
        # '<tgt> becomes/is/are [a] N/N <typetail> [with <kw>] [until end of turn]' — the dominant
        # `_becomes` animate. We OWN only this P/T-bearing shape; copy/color/base-pt/added/type
        # variants stay with the regex. Output must be byte-identical to `_becomes`.
        tgt = next((a for a in args if isinstance(a, _BcmTgt)), None)
        # the P/T is the BCM_PT Token (kept raw so we can slice the original tail by its end_pos)
        pt = next((a for a in args if not isinstance(a, (_BcmTgt, _BcmTail, _Quant))
                   and "/" in str(a)), None)
        quant = next((str(a) for a in args if isinstance(a, _Quant)), None)
        if tgt is None or pt is None:
            return None
        if quant is not None and quant.strip().lower() not in ("a", "an"):
            return None                        # regex's optional slot here is ONLY 'a'/'an' -> abstain
        tgt = str(tgt).strip()
        if not tgt or not _BCM_TGT.fullmatch(tgt):
            return None                        # subject isn't a legitimate `_TGT` NP (swallowed wrapper) -> abstain
        # slice the post-P/T span from the ORIGINAL (lowercased) source via the token's end position —
        # this is exactly the regex's T (the string g3/with/until-end-of-turn consume). Reconstructing
        # from joined tokens would lose original spacing/punctuation, so we slice instead.
        src = getattr(self, "_src", None)
        if src is None or not hasattr(pt, "end_pos") or pt.end_pos is None:
            return None
        tail = src[pt.end_pos:]
        g3 = _bcm_g3(tail)
        if g3 is None:
            return None                        # remainder isn't a clean 'with/until end of turn' -> abstain
        amount = str(pt).upper()               # P/T verbatim; input was lowercased so re-upper X ('x/x'->'X/X')
        return Effect("becomes", amount, _target(tgt), ground.slug(g3) or "-")

    # --- REVEAL ---------------------------------------------------------------
    def rvsubj(self, *toks):
        return _RvSubj(" ".join(str(t) for t in toks))

    def rvbody(self, *toks):
        return _RvBody(" ".join(str(t) for t in toks))

    def reveal(self, *args):
        subj = next((str(a) for a in args if isinstance(a, _RvSubj)), None)
        body = next((str(a) for a in args if isinstance(a, _RvBody)), None)
        if body is None:
            return None
        body = body.strip().lower()
        if subj is None:
            # IMPERATIVE 'reveal the top N cards of <owner> library' (_reveal_top). Other imperative
            # 'reveal <object>' shapes (_reveal_generic/_reveal_among) carry an open `.+?` slug -> abstain.
            m = _RV_TOP.match(body)
            if not m:
                return None
            n = _amount(m.group(1)) if m.group(1) else 1
            if n is None:
                return None                        # non-numeric count word -> regex's _reveal_generic owns it
            owner = m.group(2).strip().lower()
            tgt = "top_of_library" if owner == "your" else "top_of_" + ground.slug(owner) + "_library"
            return Effect("reveal", n if n is not None else 1, tgt)
        # SUBJECT-FORM. Only a clean closed player phrase (faithful; a swallowed/compound subject abstains).
        s = subj.strip().lower()
        if not _PLAYER.match(s):
            return None
        m = _RV_SUBJ_TOP.match(body)               # '<player> reveals the top N cards of their/your library'
        if m:
            n = _amount(m.group(1)) if m.group(1) else 1
            return Effect("reveal", n if n is not None else 1, _target(s))
        if _RV_SUBJ_HAND.match(body):              # '<player> reveals their hand'
            return Effect("reveal", "-", _target(s), "hand")
        return None                                # any other subject-reveal slug -> regex (_subject_obj_verb)

    # --- PREVENT_DAMAGE -------------------------------------------------------
    def pvbody(self, *toks):
        return _PvBody(" ".join(str(t) for t in toks))

    def prevent(self, *args):
        body = next((str(a) for a in args if isinstance(a, _PvBody)), None)
        if body is None:
            return None
        body = body.strip().lower()
        if _PV_FOG.match(body):                    # 'all [combat] damage that would be dealt this turn' (_fog)
            m = _PV_FOG.match(body)
            return Effect("prevent_damage", "all", "combat" if m.group(1) else "all")
        m = _PV_NEXT.match(body)                   # 'the next N damage that would be dealt [this turn] to <span>'
        if not m:
            return None                            # _prevent_all_scoped / _prevent_that / shield -> regex
        n = _amount(m.group(1))
        amt = n if n is not None else "X"
        span = m.group(2).strip()
        # Mirror the regex's optional trailing ' this turn': the template's `(?:this turn )?to … (?: this
        # turn)?` consumes a LEADING 'this turn' (when the body reads 'dealt this turn to <span>') and then
        # <span> has no trailing 'this turn'; otherwise the trailing ' this turn' is the optional suffix and
        # is stripped off <span>. (When neither holds — junk after 'this turn' — span keeps it, matching the
        # regex's greedy `_TGT` swallow, e.g. 'any target this turn by a source of your choice'.)
        lead_this_turn = "dealt this turn to " in body
        if not lead_this_turn and span.endswith(" this turn"):
            span = span[:-len(" this turn")].strip()
        # The regex's `_prevent` target is `(any number of targets|{_TGT})` — a clean NP that the `_TGT`
        # alternatives can't reach across a comma, a '/', a 'divided as you choose' rider, or a ', where
        # X is …' scaling appendix (those clauses make the template FAIL, so the regex abstains). Lark's
        # WORD swallows them, which would emit a garbled target slug — a LOSSY net-new fact. Abstain.
        if "," in span or "/" in span or "divided as you choose" in span or " where " in span:
            return None
        if span == "any number of targets":
            tgt = "any_number_of_targets"          # _prevent's special-case
        else:
            tgt = _target(span)
        return Effect("prevent_damage", str(amt) if isinstance(amt, int) else amt, tgt)


class _Zone:
    def __init__(self, verb):
        self.verb = verb


class _Trailer:
    pass


class _Creator(str):
    pass


class _Spec(str):
    pass


class _FEWord(str):
    pass


class _CTail(str):
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
    _T._src = s                                # the lowercased source, so bcmbecomes can slice the raw P/T tail
    e = _T.transform(tree)
    e = e.children[0] if hasattr(e, "children") else e
    return e if isinstance(e, Effect) else None
