"""The search vocabulary: what makes a file a product and what a good mark is.

Search shows final-stage things, such as graphpad files and Adobe illustrator files, including good
analysis folders. The two defaults, products and the good mark, are each built so that removing one
is cheap. A product is a Dictionary sense carrying ``product: true`` (as ``program: true`` marks a
format that is a program), so removing the lines removes the products. ``good`` is a sense of the
``quality`` kind in a section of its own carrying ``mark: good``, so deleting the file removes the
mark.
"""

from __future__ import annotations

from ingence.dictionary.store import DictionaryStore


def test_products_are_the_senses_marked_product_and_a_veto_is_removing_the_lines() -> None:
    store = DictionaryStore.builtin()
    products = {sense.term for sense in store.senses() if sense.properties.get("product")}
    assert {"figure", "plot", "presentation", "illustrator_document", "prism_project"} <= products
    assert "readme" not in products


def test_good_is_a_quality_mark_beside_the_doubt_words_and_never_among_them() -> None:
    store = DictionaryStore.builtin()
    good = next(sense for sense in store.senses() if sense.term == "good")
    assert good.theme_kind == "quality"
    assert good.properties["mark"] == "good"
    assert [s.term for s in store.senses() if s.properties.get("mark") == "good"] == ["good"]
    doubt = {sense.term for sense in store.senses() if sense.section == "negative_lexicon"}
    assert "good" not in doubt
    assert good.section != "negative_lexicon"
