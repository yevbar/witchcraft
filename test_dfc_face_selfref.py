"""DFC face self-reference normalization: 'transforms into <own face name>' -> 'transforms into ~'.

A transform/modal-DFC card refers to its OWN faces by name ('Whenever this creature enters or transforms
into Brigid, Clachan's Heart, ...'). card_corpus replaces the full '// ' name but not a single face, so the
comma in a legendary face name ('Brigid, Clachan's Heart') split the trigger from its body downstream. Each
MULTI-WORD face is now normalized to '~'; single-word split-card faces ('Fire'/'Ice') are left alone.
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus


def _units(name, text):
    return [u.raw for u in card_corpus.units_of({"name": name, "text": text})]


def test_multiword_face_normalized():
    raws = _units(
        "Brigid, Clachan's Heart // Brigid, Doun's Mind",
        "Whenever this creature enters or transforms into Brigid, Clachan's Heart, create a 1/1 green and "
        "white Kithkin creature token.")
    assert raws == ["Whenever ~ enters or transforms into ~, create a 1/1 green and white Kithkin creature token."]


def test_single_word_split_faces_left_alone():
    # 'Fire'/'Ice' are common words — must NOT be replaced (would corrupt unrelated text)
    raws = _units("Fire // Ice", "Fire deals 2 damage divided as you choose among one or two targets.")
    assert raws == ["Fire deals 2 damage divided as you choose among one or two targets."]


def test_non_dfc_unchanged():
    # a card with no '// ' name is completely unaffected by the face logic
    raws = _units("Grizzly Bears", "Vanilla.")
    assert raws == ["Vanilla."]


if __name__ == "__main__":
    test_multiword_face_normalized()
    test_single_word_split_faces_left_alone()
    test_non_dfc_unchanged()
    print("ok")
