"""The vocabulary a person gave when reviewing a run's word questions.

- ATP, ADP and TCEP join the core, each a sense of its own; insulin, asked about beside them, does
  not.
- A Fiji ROI archive is confirmed by a peek at a ZIP's members, never by its name alone: every
  member a ``.roi`` beginning with ImageJ's header. A name like ``RoiSet.zip`` only asks.
- The extensions a person taught or approved bind after a dot, and a word spelled like one keeps its
  own sense in a name.
- No vendor-wide assumption: a spelling's origin is declared on its own sense, and no rule reads a
  vendor to say what a file holds.

Every file and archive here is built from its format's structure with invented names.
"""

from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING

import pytest

from fixtures.revision_4 import REVISION
from ingence.core.ids import PROJECT_SCOPE, dictionary_term_id
from ingence.core.outcomes import ValueKind
from ingence.core.schema import (
    BindingAlternative,
    DictionaryBinding,
    DictionaryOrigin,
    Entry,
    MatchQuality,
    Observation,
)
from ingence.core.tiers import ExtractionTier, ResolutionStatus
from ingence.dictionary.store import BUILTIN_DIRECTORY, ORIGINS, DictionaryStore, load_sections
from ingence.extractors.archive import IMAGEJ_ROI_HEADER, ArchiveExtractor
from ingence.extractors.base import ExtractionContext
from ingence.extractors.limits import Limits
from ingence.infer.subjects import EntryRecords
from ingence.rulebook.predicates import load_builtins
from ingence.rulebook.registry import PREDICATES

if TYPE_CHECKING:
    from pathlib import Path

load_builtins()
STORE = DictionaryStore.builtin()
FORMAT = frozenset({"format"})


# ---------------------------------------------------------------------------
# ATP, ADP and TCEP.
# ---------------------------------------------------------------------------


def test_atp_adp_and_tcep_are_core_reagents_each_its_own_and_insulin_is_not_the_core_s() -> None:
    core = {sense.term for section in load_sections(BUILTIN_DIRECTORY) for sense in section.senses}
    terms = {word: STORE.look_up(word) for word in ("ATP", "adp", "Tcep")}
    assert {word: hit.sense.term for word, hit in terms.items() if hit} == {
        "ATP": "adenosine_triphosphate",
        "adp": "adenosine_diphosphate",
        "Tcep": "tris_carboxyethyl_phosphine",
    }
    assert all(hit.sense.theme_kind == "reagent" for hit in terms.values() if hit)
    assert {hit.sense.term for hit in terms.values() if hit} <= core, "the core, not a pack"
    # Insulin stays out of the core. It is the biochemistry pack's, with the other public biology
    # words.
    insulin = STORE.look_up("insulin")
    assert insulin is not None
    assert insulin.sense.term not in core
    assert insulin.sense.domain == "biochemistry"


# ---------------------------------------------------------------------------
# The extensions a person taught or approved.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extension", "sense"),
    [
        ("prt", "plate_reader_protocol"),
        ("lut", "lookup_table"),
        ("src", "java_source"),
        ("tmp", "temporary_file"),
        ("vtk", "vtk_data"),
        ("coo", "mitograph_output"),
        ("gnet", "mitograph_output"),
        ("mitograph", "mitograph_output"),
        ("config", "configuration_file"),
        ("jp2", "image_format"),
        ("cr2", "image_format"),
        ("fig", "matlab_figure"),
        ("gbk", "annotation_format"),
        ("enw", "citation_format"),
        ("enl", "reference_library"),
        ("frm", "database_table_file"),
        ("myd", "database_table_file"),
        ("myi", "database_table_file"),
        ("opt", "database_table_file"),
        ("cpl", "executable_format"),
        ("textclipping", "text_clipping"),
        ("ijm", "imagej_macro"),
    ],
)
def test_an_extension_he_taught_or_approved_binds_after_a_dot(extension: str, sense: str) -> None:
    hit = STORE.look_up(extension, permitted_kinds=FORMAT)
    assert hit is not None and hit.sense.term == sense


@pytest.mark.parametrize(
    ("word", "sense"), [("tmp", "temporary"), ("config", "configuration"), ("fig", "figure")]
)
def test_a_word_spelled_like_a_new_extension_keeps_its_own_sense_in_a_name(
    word: str, sense: str
) -> None:
    hit = STORE.look_up(word, excluded_kinds=FORMAT)
    assert hit is not None and hit.sense.term == sense
    for surface in ("prt", "lut", "coo", "gnet", "mitograph", "frm", "opt", "cpl", "src"):
        assert STORE.look_up(surface, excluded_kinds=FORMAT) is None, surface


# ---------------------------------------------------------------------------
# No vendor-wide assumption.
# ---------------------------------------------------------------------------


def test_every_declared_origin_is_a_spelling_of_the_sense_declaring_it() -> None:
    """A vendor's spelling says who wrote it, on its own sense; a vendor names no type."""
    declared = [sense for sense in STORE.senses() if sense.properties.get(ORIGINS)]
    assert declared, "the image type declares its proprietary spellings' origins"
    for sense in declared:
        origins = sense.properties[ORIGINS]
        assert set(origins) <= set(sense.surfaces), sense.term
        assert all(
            origin.get("written_by") in {"instrument", "software"} for origin in origins.values()
        )
    # Gen5's protocol is its own type and never an image, whoever wrote it.
    protocol = STORE.sense_for_term("plate_reader_protocol")
    assert protocol is not None and "prt" not in STORE.sense_for_term("image_format").surfaces


def test_no_rule_reads_a_vendor_to_say_what_a_file_holds() -> None:
    """Not every proprietary extension is an image."""
    for rule in REVISION["rules"]:
        names = {parameter["name"] for parameter in rule.get("parameters", ())}
        assert not any("vendor" in name for name in names), rule["id"]
        if rule["predicate"] == "class_from_format_origin":
            assert "written_by" in names and "senses" in names, rule["id"]


# ---------------------------------------------------------------------------
# A Fiji ROI archive, confirmed by a peek at its members.
# ---------------------------------------------------------------------------


def _roi(name: str) -> bytes:
    """An ImageJ region of interest: the header, a version and a rectangle, all invented."""
    return IMAGEJ_ROI_HEADER + b"\x00\xe3\x01\x00" + b"\x00\x0a\x00\x0a\x00\x20\x00\x20" + bytes(52)


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def _listing(path: Path) -> tuple[Observation, ...]:
    """What a dive's archive reading records of a ZIP, as the observations it stores."""
    result = ArchiveExtractor().extract(
        ExtractionContext(
            entry_id="entry_zip",
            path=path,
            relative_path=path.name,
            extensions=frozenset({".zip"}),
            size_bytes=path.stat().st_size,
            limits=Limits(),
        )
    )
    return tuple(
        Observation(
            observation_id=f"observation_{index}",
            entry_id="entry_zip",
            run_id="r",
            observer="archive",
            observer_version=result.extractor_version,
            observed_at="t",
            key=reading.key,
            value_kind=reading.kind,
            value_text=reading.value if reading.kind is ValueKind.TEXT else None,
            value_integer=reading.value if reading.kind is ValueKind.INTEGER else None,
            value_real=reading.value if reading.kind is ValueKind.REAL else None,
            value_structure=reading.value if reading.kind is ValueKind.STRUCTURE else None,
            extraction_tier=ExtractionTier.MECHANICALLY_EXPOSED,
        )
        for index, reading in enumerate(result.readings)
    )


_MEMBERS_RULE = {
    "member_extensions": ["roi"],
    "header_observation": "archive.imagej_roi_headers",
    "format": "fiji_roi_zip",
}


def test_a_zip_of_imagej_regions_is_a_fiji_roi_archive(tmp_path: Path) -> None:
    listing = _listing(
        _zip(tmp_path / "kelt.zip", {f"000{n}-0100-0200.roi": _roi(str(n)) for n in range(3)})
    )
    by_key = {observation.key: observation for observation in listing}
    assert json.loads(by_key["archive.member_extensions"].value_structure) == {"roi": 3}
    assert by_key["archive.imagej_roi_headers"].value_integer == 3
    concluded = PREDICATES.get("format_from_archive_members").evaluate(listing, _MEMBERS_RULE)
    assert concluded is not None and concluded.values == ("fiji_roi_zip",)


@pytest.mark.parametrize(
    ("members", "reason"),
    [
        ({"a.roi": b"not a region", "b.roi": b"nor this"}, "0 of its 2 members begin"),
        ({"a.roi": _roi("a"), "vorn.csv": b"x,y\n"}, "members of other kinds (.csv)"),
        ({}, "it holds no member"),
    ],
)
def test_a_zip_whose_members_are_not_all_imagej_regions_is_not_one_and_says_why(
    tmp_path: Path, members: dict[str, bytes], reason: str
) -> None:
    listing = _listing(_zip(tmp_path / "brisa.zip", members))
    predicate = PREDICATES.get("format_from_archive_members")
    assert predicate.evaluate(listing, _MEMBERS_RULE) is None
    assert reason in predicate.explain_abstention(listing, _MEMBERS_RULE).reason


def test_an_archive_nobody_listed_is_unexamined() -> None:
    predicate = PREDICATES.get("format_from_archive_members")
    assert predicate.evaluate((), _MEMBERS_RULE) is None
    explained = predicate.explain_abstention((), _MEMBERS_RULE)
    assert explained.status is ResolutionStatus.UNEXAMINED


def _binding(term: str, *, extension: bool = False) -> DictionaryBinding:
    """A word bound to one sense, or the extension bound to a format."""
    return DictionaryBinding(
        binding_id=f"binding_{term}",
        component_id=f"component_{term}",
        entry_id="entry_zip",
        run_id="r",
        resolver="test",
        resolver_version="1",
        lookup_scope=PROJECT_SCOPE,
        constraint="extension:format" if extension else None,
        extraction_tier=ExtractionTier.SEMANTIC,
        resolution_status=ResolutionStatus.RESOLVED,
        term_id=_term_id(term),
        term=term,
        quality=MatchQuality.EXACT,
        theme_kind="format" if extension else "acquisition",
    )


def _term_id(term: str) -> str:
    return dictionary_term_id(term=term, scope=PROJECT_SCOPE, origin=DictionaryOrigin.BUILTIN.value)


def _standing_between(*terms: str) -> DictionaryBinding:
    """A word the Dictionary could not settle, standing between these senses (``Roi``)."""
    return DictionaryBinding(
        binding_id="binding_contested",
        component_id="component_contested",
        entry_id="entry_zip",
        run_id="r",
        resolver="test",
        resolver_version="1",
        lookup_scope=PROJECT_SCOPE,
        extraction_tier=ExtractionTier.SEMANTIC,
        resolution_status=ResolutionStatus.EXAMINED_CONFLICTING,
        alternative_count=len(terms),
        alternatives=tuple(
            BindingAlternative(
                term_id=_term_id(term),
                theme_kind="acquisition",
                origin=DictionaryOrigin.BUILTIN,
                origin_rank=0,
                scope=PROJECT_SCOPE,
            )
            for term in terms
        ),
    )


def _named_zip(*observations: Observation, word: DictionaryBinding | None = None) -> EntryRecords:
    entry = Entry(
        entry_id="entry_zip",
        root_id="root",
        relative_path="kelt/RoiSet.zip",
        name="RoiSet.zip",
        depth=2,
        entry_type="file",
        extension="zip",
        first_seen_run="r",
        last_seen_run="r",
    )
    return EntryRecords(
        entry=entry,
        bindings=(
            _binding("archive_format", extension=True),
            word or _binding("region_of_interest"),
        ),
        observations=observations,
    )


_NAMED_RULE = {"archive_senses": ["archive_format"], "named_senses": ["region_of_interest"]}


def test_a_zip_named_for_regions_and_never_listed_asks_and_a_listing_quiets_it(
    tmp_path: Path,
) -> None:
    predicate = PREDICATES.get("archive_named_but_unlisted")
    raised = predicate.evaluate(_named_zip(), _NAMED_RULE)
    assert raised is not None and raised.values == ("region_of_interest",)
    listing = _listing(_zip(tmp_path / "RoiSet.zip", {"0001.roi": _roi("1")}))
    assert predicate.evaluate(_named_zip(*listing), _NAMED_RULE) is None
    assert (
        "listed its members"
        in predicate.explain_abstention(_named_zip(*listing), _NAMED_RULE).reason
    )


def test_a_name_whose_word_stands_between_senses_still_asks_where_one_is_the_named_one() -> None:
    """``Roi`` stands between an annotation and a region of interest; the archive still asks."""
    predicate = PREDICATES.get("archive_named_but_unlisted")
    contested = _named_zip(word=_standing_between("annotation", "region_of_interest"))
    raised = predicate.evaluate(contested, _NAMED_RULE)
    assert raised is not None and raised.values == ("region_of_interest",)
    elsewhere = _named_zip(word=_standing_between("annotation", "vorn_marker"))
    assert predicate.evaluate(elsewhere, _NAMED_RULE) is None
