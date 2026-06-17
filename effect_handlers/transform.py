"""effect_handlers/transform.py — §712 TRANSFORM as a directly-encoded SELF effect verb.

A transforming double-faced permanent's own ability "transform <this>" / "transform it" flips it to its
other face. The driver already owns the mechanic — driver._transform(state, src, ctrl) flips instance_of(src)
to the back slug the bridge linked via transform_target, re-materializes the back's printed identity, and
re-enters it as a new object (firing the §603 enters window). But the raw `transform` effect verb had no
bridge ENCODE entry, so EVERY 'transform this'/'transform it' clause dropped at the encoding step even though
the application path exists (169 self-scope cards: Aberrant Researcher, Afflicted Deserter, the daybound/
nightbound werewolves, the investigate-into-Perfected-Form lines, …). This mirrors the §701.28 convert work
(effect_handlers/convert.py) — convert REDUCES to this same path; here we wire the §712 keyword itself.

FAITHFUL-OR-ABSTAIN: encode self / 'it' / a bare self-name token (the §712 keyword transforms the permanent
whose ability it is). A TARGETED or other-permanent transform ('transform target creature', 'transform each
…', an incubator/clue token) ABSTAINS — driver._transform flips a GIVEN object (the source), it does not
resolve a board target, so we can't faithfully transform another permanent through it. The applier is a no-op
when src has no transform_target (a non-DFC), the faithful outcome for a permanent that can't be transformed.

PUBLIC: a permanent flipping faces is public board state — instance_of / printed_* are public battlefield
rows, so observe.py shows the flipped identity to every seat and transform reads identically in perfect and
imperfect information. See effect_handlers/__init__.py for the @encoder/@applier contract.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

# target column shapes that mean "the source itself" (the §712 keyword transforms the permanent that has it).
_SELF_TGT = {"self", "it", "they", "them", "-", ""}
# prefixes/tokens that mean some OTHER permanent (a real target) -> abstain (the transform path is self-only).
_OTHER_MARKERS = ("target", "another", "other", "each", "up_to", "enchanted", "all_", "_token")


@encoder("transform")
def encode_transform(verb, amt, tgt, extra):
    t = str(tgt)
    if t in _SELF_TGT:
        return ("transform", 0, "self")
    if any(m in t for m in _OTHER_MARKERS):
        return None                                          # a real / other-permanent target -> abstain
    # a BARE token (e.g. the source's own short name) — §712 transforms the source.
    return ("transform", 0, "self")


@applier("transform")
def apply_transform(D, state, a, n, tgt, src, ctrl):
    """§712 transform the source: flip it to its other face. driver._transform is a no-op if `src` has no
    transform_target (a non-DFC / single-faced permanent), the faithful outcome for an untransformable card."""
    has_back = any(o == src for (o, _b) in state.get("transform_target", set()))
    if not has_back:
        print(f"    {a}: {src} has no other face to transform into (no-op)")
        return
    print(f"    {a}: {src} transforms (§712)")
    D._transform(state, src, ctrl)
