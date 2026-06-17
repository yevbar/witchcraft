"""effect_handlers/convert.py — §701.28 CONVERT, reduced to the existing §712 TRANSFORM mechanic.

'Convert ~' / 'convert it' (§701.28a) turns a transforming double-faced permanent over: it changes from its
current face to its other face. The corpus instances are the Transformers cards (Optimus Prime, Ratchet,
Starscream, …), whose card_effect rows always target the SOURCE itself — the verb's target column is either
'it' or the source's own short name (e.g. "ratchet", "starscream"), never another permanent. So convert here
is purely a SELF flip, which is exactly what driver._transform does: it flips instance_of(src) to the back
slug the bridge linked via transform_target, re-materializes the back's printed identity, and re-enters it as
a new object (firing the §603 enters window). We emit a 'convert' engine effect whose applier calls
driver._transform(state, src, ctrl) — the SAME path §712 transform uses (no new engine effect, no driver edit).

FAITHFUL-OR-ABSTAIN: encode self/'it'/a bare self-name token (no target/another/other/each/up_to prefix —
the §701.28 keyword action always converts the source itself). A targeted or other-permanent convert ABSTAINS:
the transform path here flips a GIVEN object (the source), it does not resolve a board target, so we can't
faithfully convert 'target/another creature' through it.

PUBLIC: a permanent flipping faces is public board state; observe.py shows the flipped identity to every seat
(instance_of/printed_* are public battlefield rows), so convert reads identically in perfect and imperfect
information. See effect_handlers/__init__.py for the @encoder/@applier contract.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

# target column shapes that mean "the source itself" (§701.28 always converts the permanent that converts).
_SELF_TGT = {"self", "it", "they", "them", "-", ""}
# prefixes/tokens that mean some OTHER permanent (a real target) -> abstain (the transform path is self-only).
_OTHER_MARKERS = ("target", "another", "other", "each", "up_to", "enchanted", "all_")


@encoder("convert")
def encode_convert(verb, amt, tgt, extra):
    t = str(tgt)
    if t in _SELF_TGT:
        return ("convert", 0, "self")
    if any(m in t for m in _OTHER_MARKERS):
        return None                                          # a real / other-permanent target -> abstain
    # a BARE token (e.g. the source's own short name "ratchet"/"starscream") — §701.28 converts the source.
    return ("convert", 0, "self")


@applier("convert")
def apply_convert(D, state, a, n, tgt, src, ctrl):
    """§701.28a convert the source: flip it to its other face via the §712 transform path. driver._transform
    is a no-op if `src` has no transform_target (a non-DFC), which is the faithful outcome for a permanent
    that can't be converted."""
    has_back = any(o == src for (o, _b) in state.get("transform_target", set()))
    if not has_back:
        print(f"    {a}: {src} has no other face to convert to (no-op)")
        return
    print(f"    {a}: {src} converts (flips to its other face) (§701.28)")
    D._transform(state, src, ctrl)
