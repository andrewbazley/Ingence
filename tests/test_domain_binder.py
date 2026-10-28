"""A spelling two domains hold differently is read from the folder's own files.

Every name, folder, domain and word here is invented.

The vocabulary is three invented domains and a universal section: ``zorb`` is a technique in the
``kelt`` domain and a statistic in the universal section; ``vorn`` is a technique in ``kelt`` and
a reagent in ``brisa``, with no universal sense; ``.kz`` files are the ``kelt`` domain's type and
``.bq`` files the ``brisa`` domain's; ``dapu`` is a word only ``kelt`` spells.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import pytest

from ingence.core.schema import DictionaryOrigin, Entry, EntryType, ScanOutcome
from ingence.core.tiers import ResolutionStatus
from ingence.dictionary.discover import (
    DOMAIN_PRIOR_CONSTRAINT,
    DOMAIN_PROFILE_KEY,
    DOMAIN_SKEW_CONSTRAINT,
    DOMAIN_TIE_CONSTRAINT,
    UNIVERSAL_SENSE_CONSTRAINT,
    DomainBinder,
    is_skew_constraint,
    profile_in_words,
    read_name,
    skew_constraint,
)
from ingence.dictionary.normalize import Normaliser
from ingence.dictionary.store import DictionaryStore, Section, Sense

if TYPE_CHECKING:
    from ingence.core.schema import DictionaryBinding


def _sense(
    term: str, surfaces: tuple[str, ...], kind: str, *, section: str, domain: str | None = None
) -> Sense:
    return Sense(
        term=term,
        canonical=term,
        theme_kind=kind,
        section=section,
        surfaces=surfaces,
        origin=DictionaryOrigin.BUILTIN,
        domain=domain,
    )


def _store() -> DictionaryStore:
    kelt = Section(
        name="kelt_words",
        summary="",
        domain="kelt",
        senses=(
            _sense("zorb_scan", ("zorb",), "technique", section="kelt_words", domain="kelt"),
            _sense("vorn_scan", ("vorn",), "technique", section="kelt_words", domain="kelt"),
            _sense("dapu_stain", ("dapu",), "reagent", section="kelt_words", domain="kelt"),
            _sense("nira_dye", ("nira",), "reagent", section="kelt_words", domain="kelt"),
            _sense("kz_format", ("kz",), "format", section="kelt_words", domain="kelt"),
        ),
    )
    brisa = Section(
        name="brisa_words",
        summary="",
        domain="brisa",
        senses=(
            _sense("vorn_salt", ("vorn",), "reagent", section="brisa_words", domain="brisa"),
            _sense("bq_format", ("bq",), "format", section="brisa_words", domain="brisa"),
            _sense("quil_buffer", ("quil",), "reagent", section="brisa_words", domain="brisa"),
        ),
    )
    universal = Section(
        name="statistics",
        summary="",
        senses=(
            _sense("zorb_score", ("zorb",), "statistic", section="statistics"),
            _sense("plain_text", ("txt",), "format", section="statistics"),
        ),
    )
    return DictionaryStore((kelt, brisa, universal))


STORE = _store()
EXTENSIONS = {"kz": frozenset({"kelt"}), "bq": frozenset({"brisa"})}


def _entry(path: str, *, directory: bool = False) -> Entry:
    pure = PurePosixPath(path)
    return Entry(
        entry_id=f"entry_{path}",
        root_id="root_test",
        relative_path=path,
        name=pure.name,
        entry_type=EntryType.DIRECTORY if directory else EntryType.FILE,
        extension="" if directory else pure.suffix.casefold().removeprefix("."),
        compound_extension="",
        size_bytes=0,
        mode=16877 if directory else 33188,
        nlink=1,
        modified_ns=0,
        changed_ns=0,
        resolution_status=ResolutionStatus.UNEXAMINED,
        first_seen_run="run_a",
        last_seen_run="run_a",
        scan_outcome=ScanOutcome.METADATA_ONLY,
    )


def _run(
    paths: list[str], *, priors: frozenset[str] = frozenset(), **settings: int | float
) -> tuple[DomainBinder, dict[str, list[DictionaryBinding]]]:
    """Read every path (folders included), then bind what the domains decide; bindings by path."""
    normaliser = Normaliser(STORE)
    binder = DomainBinder(
        STORE,
        minimum_files=int(settings.get("minimum_files", 4)),
        minimum_share=float(settings.get("minimum_share", 0.6)),
        minimum_ratio=float(settings.get("minimum_ratio", 3.0)),
        question_above_files=int(settings.get("question_above_files", 3)),
        priors=priors,
        extension_domains=EXTENSIONS,
    )
    normaliser.domains = binder
    folders = sorted({str(PurePosixPath(path).parent) for path in paths} - {"."})
    found: dict[str, list[DictionaryBinding]] = {}
    for folder in folders:
        read_name(
            _entry(folder, directory=True), run_id="run_a", normaliser=normaliser, store=STORE
        )
    for path in paths:
        reading = read_name(_entry(path), run_id="run_a", normaliser=normaliser, store=STORE)
        found[path] = list(reading.bindings)
    deferred = binder.bind(run_id="run_a")
    by_entry: dict[str, list[DictionaryBinding]] = {}
    for binding in deferred:
        by_entry.setdefault(binding.entry_id, []).append(binding)
    for path in paths:
        found[path].extend(by_entry.get(f"entry_{path}", []))
    return binder, found


def _of(bindings: list[DictionaryBinding], term_or_alternative: str) -> DictionaryBinding:
    """The binding that resolved to this term, or stands between alternatives naming it."""
    sense = STORE.sense_for_term(term_or_alternative)
    wanted = STORE.term_id(sense) if sense is not None else None
    for binding in bindings:
        if binding.term == term_or_alternative:
            return binding
        if any(alt.term_id == wanted for alt in binding.alternatives):
            return binding
    raise AssertionError(f"no binding of {term_or_alternative}: {[b.term for b in bindings]}")


def test_a_folder_leaning_to_one_domain_reads_the_shared_word_as_that_domain_s() -> None:
    paths = [f"scan/run_zorb_{i:02d}.kz" for i in range(1, 7)]
    binder, found = _run(paths)
    binding = _of(found[paths[0]], "zorb_scan")
    assert binding.resolution_status is ResolutionStatus.RESOLVED
    # The marker cites the deciding folder's entry, as a settled date's observation cites its
    # neighbours.
    assert (binding.term, binding.constraint) == ("zorb_scan", skew_constraint("entry_scan"))
    assert is_skew_constraint(binding.constraint) and is_skew_constraint(DOMAIN_SKEW_CONSTRAINT)
    assert [(alt.shadowed, alt.theme_kind) for alt in binding.alternatives] == [(True, "statistic")]
    assert binder.outcomes == {"skew": 6}
    (observation,) = binder.observations(run_id="run_a")
    assert observation.entry_id == "entry_scan" and observation.key == DOMAIN_PROFILE_KEY
    profile = json.loads(observation.value_structure or "{}")
    # The observation holds the counts and no decision: the decision lives in the
    # binding's constraint.
    assert set(profile) == {"files", "voting", "voting_by", "by_type", "by_word", "by_neighbour"}
    assert (profile["files"], profile["voting"], profile["by_type"]) == (6, 6, {"kelt": 6})
    assert (profile["by_word"], profile["by_neighbour"]) == ({}, {})
    assert profile["voting_by"] == {"by_type": 6, "by_word": 0, "by_neighbour": 0}
    assert binder.decide_profile(profile) == ("kelt", "by_type")
    said = profile_in_words(profile)
    assert said == "6 of 6 files read as a domain's; by their type: 6 kelt"


def test_too_few_voting_files_leave_the_universal_sense_as_the_default() -> None:
    paths = [f"scan/run_zorb_{i:02d}.kz" for i in range(1, 4)]  # under minimum_files 4
    binder, found = _run(paths)
    binding = _of(found[paths[0]], "zorb_score")
    assert (binding.term, binding.constraint) == ("zorb_score", UNIVERSAL_SENSE_CONSTRAINT)
    assert [alt.theme_kind for alt in binding.alternatives] == ["technique"]
    assert binder.outcomes == {"universal": 3}


def test_an_even_skew_with_no_universal_sense_stays_a_tie_asked_only_above_the_file_floor() -> None:
    # Three kelt files and three brisa files: even, so vorn (kelt or brisa, no universal sense)
    # stays conflicting; three files carry it, which is at the floor, so it is not asked.
    paths = [f"mixed/a_vorn_{i}.kz" for i in range(1, 4)] + [f"mixed/b_{i}.bq" for i in range(1, 4)]
    binder, found = _run(paths, question_above_files=3)
    binding = _of(found[paths[0]], "vorn_scan")
    assert binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
    assert binding.constraint == DOMAIN_TIE_CONSTRAINT
    assert binder.outcomes == {"tie_kept": 3}
    # More files than the floor carry it: the tie is a question, as before.
    binder, found = _run(paths, question_above_files=2)
    binding = _of(found[paths[0]], "vorn_scan")
    assert binding.resolution_status is ResolutionStatus.EXAMINED_CONFLICTING
    assert binding.constraint is None
    assert binder.outcomes == {"tie_asked": 3}


def test_a_prior_decides_where_the_files_do_not() -> None:
    paths = [f"mixed/a_vorn_{i}.kz" for i in range(1, 4)] + [f"mixed/b_{i}.bq" for i in range(1, 4)]
    binder, found = _run(paths, priors=frozenset({"brisa"}))
    binding = _of(found[paths[0]], "vorn_salt")
    assert (binding.term, binding.constraint) == ("vorn_salt", DOMAIN_PRIOR_CONSTRAINT)
    assert binder.outcomes == {"prior": 3}


def test_the_nearest_folder_with_enough_files_decides_then_the_root() -> None:
    # The subfolder holds two files (too few); the root holds six kelt files: kelt decides.
    paths = [f"day/sub/run_zorb_{i}.txt" for i in (1, 2)] + [f"day/run_{i}.kz" for i in range(1, 7)]
    binder, found = _run(paths)
    binding = _of(found[paths[0]], "zorb_scan")
    assert binding.constraint == skew_constraint("entry_day")
    profiles = binder.profiles()
    assert profiles[("root_test", "day/sub")]["voting"] == 0
    assert profiles[("root_test", "day")]["voting"] == 6
    assert profiles[("root_test", "")]["voting"] == 6


def test_a_file_of_a_domain_s_type_votes_by_its_type_alone_and_a_tied_word_never_votes() -> None:
    # A file of the kelt type naming two kelt words votes kelt once, by its type; its words vote
    # nothing (the type decides first) and its tied word nothing at all.
    paths = ["scan/dapu_dapu_zorb_vorn_01.kz"]
    binder, _ = _run(paths)
    profile = binder.profiles()[("root_test", "scan")]
    assert (profile["by_type"], profile["by_word"], profile["voting"]) == ({"kelt": 1}, {}, 1)


def test_only_a_technique_an_instrument_a_channel_or_a_label_votes_by_word() -> None:
    # quil is a buffer (a reagent that is no stain or label): it says nothing of a domain.
    binder, _ = _run(["bench/mix_quil_01.txt", "bench/mix_dapu_02.txt"], minimum_files=1)
    profile = binder.profiles()[("root_test", "bench")]
    assert (profile["by_word"], profile["voting"]) == ({"kelt": 1}, 1)


def test_an_export_of_a_domain_s_file_votes_by_the_compound_extension_s_leading_part() -> None:
    from ingence.dictionary.discover import DomainBinder as Binder

    store = STORE
    normaliser = Normaliser(store)
    binder = Binder(store, minimum_files=1, extension_domains=EXTENSIONS)
    normaliser.domains = binder
    entry = _entry("scan/run_01.kz.txt")
    entry = entry.model_copy(update={"compound_extension": "kz.txt"})
    read_name(entry, run_id="run_a", normaliser=normaliser, store=store)
    assert binder.profiles()[("root_test", "scan")]["by_type"] == {"kelt": 1}


def test_slot_neighbours_of_one_domain_vote_for_a_tied_word_last() -> None:
    # Names from one template, the second word dapu, nira or zorb, in files of no domain's type.
    # One distinct neighbour word (dapu) is no paradigm: zorb votes nothing, and with one word
    # file under the floor of three nothing decides, so zorb is the universal sense by default.
    paths = ["t/a_dapu_1.txt", "t/c_zorb_3.txt", "t/d_zorb_4.txt", "t/e_zorb_5.txt"]
    binder, found = _run(paths, minimum_files=3)
    profile = binder.profiles()[("root_test", "t")]
    assert (profile["by_word"], profile["by_neighbour"]) == ({"kelt": 1}, {})
    assert _of(found["t/c_zorb_3.txt"], "zorb_score").constraint == UNIVERSAL_SENSE_CONSTRAINT
    # Two kelt words at the place (dapu, nira): the three zorb files vote kelt by their
    # neighbours, which decides where two word files could not, and the kind says so.
    paths = [
        "t/a_dapu_1.txt",
        "t/b_nira_2.txt",
        "t/c_zorb_3.txt",
        "t/d_zorb_4.txt",
        "t/e_zorb_5.txt",
    ]
    binder, found = _run(paths, minimum_files=3)
    profile = binder.profiles()[("root_test", "t")]
    assert (profile["by_word"], profile["by_neighbour"]) == ({"kelt": 2}, {"kelt": 3})
    assert profile["voting_by"] == {"by_type": 0, "by_word": 2, "by_neighbour": 3}
    assert binder.decide_profile(profile) == ("kelt", "by_neighbour")
    assert _of(found["t/c_zorb_3.txt"], "zorb_scan").constraint == skew_constraint("entry_t")


def test_the_conflict_rule_leaves_a_kept_tie_and_raises_an_asked_one() -> None:
    from ingence.rulebook.predicates.flag import ReadingsConflict

    paths = [f"mixed/a_vorn_{i}.kz" for i in range(1, 4)] + [f"mixed/b_{i}.bq" for i in range(1, 4)]
    _, kept = _run(paths, question_above_files=3)
    _, asked = _run(paths, question_above_files=2)

    class Subject:
        def __init__(self, bindings: list[DictionaryBinding]) -> None:
            self.bindings = tuple(bindings)
            self.components = ()

    rule = ReadingsConflict()
    parameters = {"minimum_alternatives": 2, "reading": "dictionary_binding"}
    assert rule.evaluate(Subject(kept[paths[0]]), parameters) is None
    assert rule.evaluate(Subject(asked[paths[0]]), parameters) is not None


@pytest.mark.parametrize("folder", ["scan", "bench"])
def test_a_magnification_is_the_microscope_s_where_its_folder_is(folder: str) -> None:
    """Over what ships: ``100x`` is a magnification in a folder of a microscope's files, and a
    fold by default elsewhere, the other reading shadowed either way."""
    from ingence.dictionary.file_types import extension_domains

    store = DictionaryStore.builtin()
    normaliser = Normaliser(store)
    normaliser.domains = DomainBinder(
        store, minimum_files=3, extension_domains=extension_domains(), question_above_files=3
    )
    kind = "nd2" if folder == "scan" else "txt"
    # No word of another domain beside it: a file counts once per domain, and a word a second
    # domain spells (a cell is a bench word) would even the skew.
    paths = [f"{folder}/img_100x_{i}.{kind}" for i in range(1, 5)]
    found: list[DictionaryBinding] = []
    for path in paths:
        read_name(_entry(path), run_id="run_a", normaliser=normaliser, store=store)
    found = normaliser.domains.bind(run_id="run_a")
    assert len(found) == 4
    binding = found[0]
    assert binding.resolution_status is ResolutionStatus.RESOLVED
    if folder == "scan":
        # No folder entry was read here, so the marker cites none.
        assert (binding.term, binding.constraint) == ("magnification", DOMAIN_SKEW_CONSTRAINT)
    else:
        assert (binding.term, binding.constraint) == ("fold", UNIVERSAL_SENSE_CONSTRAINT)
    assert len(binding.alternatives) == 1 and binding.alternatives[0].shadowed


# -- the well-grid guard --------------------------------------------------------------------------


def _wells(paths: list[str]) -> tuple[DomainBinder, list[DictionaryBinding]]:
    """Read the shipped Dictionary over a folder of names, and bind what the binder held back."""
    from ingence.dictionary.discover import WELL_GRID_CONSTRAINT

    del WELL_GRID_CONSTRAINT
    store = DictionaryStore.builtin()
    normaliser = Normaliser(store)
    normaliser.domains = DomainBinder(store, minimum_files=3, question_above_files=3)
    for path in paths:
        read_name(_entry(path), run_id="run_a", normaliser=normaliser, store=store)
    return normaliser.domains, normaliser.domains.bind(run_id="run_a")


def test_in_a_well_grid_folder_a_well_shaped_part_binds_no_channel_or_passage() -> None:
    from ingence.dictionary.discover import WELL_GRID_CONSTRAINT

    # Rows A to D across columns 1 to 4: C1 to C4 are wells, and P1 would be one too.
    paths = [f"plate/plate_{row}{column}.tif" for row in "ABCD" for column in range(1, 5)]
    binder, found = _wells(paths)
    assert binder.is_well_grid(("root_test", "plate"), ("word", "indexed", "extension"), 1)
    assert found, "C1 to C4 bind channels through their whole text, so they were held back"
    for binding in found:
        assert binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE
        assert binding.constraint == WELL_GRID_CONSTRAINT
        assert binding.term is None
        assert [alt.shadowed for alt in binding.alternatives] == [True]
    assert binder.outcomes == {"well_unbound": len(found)}


def test_outside_a_grid_the_same_part_binds_as_it_did() -> None:
    # One row only: no grid, so c1 and c2 are the channels they read as.
    paths = [f"scan/image_{part}.tif" for part in ("C1", "C2", "C3")]
    binder, found = _wells(paths)
    assert not binder.is_well_grid(("root_test", "scan"), ("word", "indexed", "extension"), 1)
    assert {binding.resolution_status for binding in found} == {ResolutionStatus.RESOLVED}
    assert {binding.theme_kind for binding in found} == {"channel"}
    assert binder.outcomes == {"well_bound": 3}


def test_a_grid_s_rows_are_contiguous_letters_or_at_least_three() -> None:
    """Two rows apart (A and C) are not a grid.

    Two neighbouring rows (D and E) are, and so are A, C and E.
    """
    shape = ("word", "indexed", "extension")
    binder, _ = _wells(["plate/p_A1.tif", "plate/p_C2.tif"])
    assert not binder.is_well_grid(("root_test", "plate"), shape, 1)
    binder, _ = _wells(["plate/p_D1.tif", "plate/p_E2.tif"])  # a B row reads as a version
    assert binder.is_well_grid(("root_test", "plate"), shape, 1)
    binder, _ = _wells(["plate/p_A1.tif", "plate/p_C2.tif", "plate/p_E1.tif"])
    assert binder.is_well_grid(("root_test", "plate"), shape, 1)


def test_a_grid_needs_two_rows_and_two_columns() -> None:
    # Two rows, one column: no grid.
    _, found = _wells(["plate/p_C1.tif", "plate/p_D1.tif", "plate/p_E1.tif"])
    assert {binding.resolution_status for binding in found} == {ResolutionStatus.RESOLVED}
    # Two contiguous rows and two columns, zero-padded and unpadded alike (a B row with a number
    # reads as a version marker, as before: the guard does not reach it).
    binder, _ = _wells(["plate/p_A01.tif", "plate/p_C02.tif", "plate/p_D03.tif"])
    assert binder.is_well_grid(("root_test", "plate"), ("word", "indexed", "extension"), 1)


def test_a_channel_slot_beside_the_well_slot_stays_a_channel() -> None:
    """``A01_c1``: the well slot spans rows and columns and is unbound; the channel slot beside
    it covers one letter and is bound as the channel it is."""
    paths = [
        f"plate/p_{row}{column:02d}_{channel}.tif"
        for row in "ACD"
        for column in range(1, 4)
        for channel in ("c1", "c2")
    ]
    binder, found = _wells(paths)
    shape = ("word", "indexed", "indexed", "extension")
    assert binder.is_well_grid(("root_test", "plate"), shape, 1)
    assert not binder.is_well_grid(("root_test", "plate"), shape, 2)
    channels = [b for b in found if b.resolution_status is ResolutionStatus.RESOLVED]
    unbound = [b for b in found if b.resolution_status is not ResolutionStatus.RESOLVED]
    # A zero-padded well (C01) binds nothing through its whole text and is never held back. In
    # C01_c1 and C02_c2 the well and the channel read as one component (one normalised value,
    # c1, per name): the grid is read by atom, the channel's writing sits in the
    # channel slot, so the component keeps its channel.
    assert {b.theme_kind for b in channels} == {"channel"} and len(channels) == 18
    assert unbound == []
    assert binder.outcomes == {"well_bound": 18}


def test_an_unbound_well_shaped_part_is_not_held_back() -> None:
    # A01 binds nothing through its whole text: it is bound in the walk, as before, and the grid
    # still counts it.
    store = DictionaryStore.builtin()
    normaliser = Normaliser(store)
    normaliser.domains = DomainBinder(store, minimum_files=3)
    reading = read_name(
        _entry("plate/p_A01.tif"), run_id="run_a", normaliser=normaliser, store=store
    )
    (binding,) = [b for b in reading.bindings if b.constraint == "name:index_label"]
    assert binding.resolution_status is ResolutionStatus.EXAMINED_NO_EVIDENCE
    key = (("root_test", "plate"), ("word", "indexed", "extension"), 1)
    assert normaliser.domains._grid[key] == ({"a"}, {1})
