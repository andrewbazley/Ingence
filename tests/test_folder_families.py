"""A name family read beside its folder's name.

The folder's name says what an experiment's members share, and only certain class keys apply to
certain files. Every name here is invented.
"""

from __future__ import annotations

from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore
from ingence.infer.folder_families import folder_family

NORMALISER = Normaliser(DictionaryStore.builtin())


def test_a_folder_naming_two_conditions_divides_its_members_by_which_each_restates() -> None:
    family = folder_family(
        "WT kelt ATP ADP",
        ["WT kelt ATP_001.tif", "WT kelt ATP_002.tif", "WT kelt ADP_001.tif"],
        NORMALISER,
    )
    assert family.shared == ("wt", "kelt")
    assert family.varying == ("atp", "adp")
    assert family.subgroups == {
        ("adp",): ("WT kelt ADP_001.tif",),
        ("atp",): ("WT kelt ATP_001.tif", "WT kelt ATP_002.tif"),
    }
    assert family.divides


def test_a_letter_the_folder_spells_out_is_read_as_the_folder_s_word() -> None:
    """``-C`` in a folder named ``Cropped``."""
    family = folder_family("Cropped", ["vorn_3-C.tif", "tamen_1-C.tif"], NORMALISER)
    assert family.abbreviations == {"c": "cropped"}
    assert family.context == ()
    assert not family.divides


def test_a_folder_for_something_gives_its_words_to_every_member() -> None:
    family = folder_family("For Quoltgraph", ["cell_1.vtk", "cell_2.coo"], NORMALISER)
    assert family.context == ("quoltgraph",)
    assert family.shared == () and family.varying == ()
    assert list(family.subgroups) == [()]


def test_members_restating_every_word_are_one_group() -> None:
    family = folder_family("brisa zeph", ["brisa zeph 1.csv", "brisa zeph 2.csv"], NORMALISER)
    assert family.shared == ("brisa", "zeph") and not family.divides
