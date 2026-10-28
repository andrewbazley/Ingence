"""One form for an extension at every boundary.

A person writes an extension dotted or not, in any case, and as a compound. Each boundary reads
the same form, so ``.bin`` in a hash filter matches a file the census stores as ``bin``.
"""

from __future__ import annotations

import pytest

from ingence.core.extensions import canonical_extension, canonical_extensions
from ingence.core.registry import Registry
from ingence.core.schema import Entry, EntryType
from ingence.core.settings import ExtractionSettings, HashMode, HashSettings, Settings
from ingence.record.targets import extension_target
from ingence.scan.deepen import deepen_entry
from ingence.scan.reuse import digests_wanted, policy_selects, processing_policy


def entry_for(path, name: str) -> Entry:
    return Entry(
        entry_id=f"entry_{name}",
        root_id="root_x",
        relative_path=name,
        name=name,
        entry_type=EntryType.FILE,
        extension=name.rsplit(".", 1)[-1],
        size_bytes=path.stat().st_size,
        first_seen_run="run_1",
        last_seen_run="run_1",
    )


SPELLINGS_OF_TXT = (".txt", "txt", ".TXT", "TXT", " .Txt ", "*.txt")
SPELLINGS_OF_TAR_GZ = (".tar.gz", "tar.gz", ".TAR.GZ", "TAR.GZ", "*.Tar.Gz")


@pytest.mark.parametrize("spelled", SPELLINGS_OF_TXT)
def test_every_spelling_of_one_extension_is_one_form(spelled):
    assert canonical_extension(spelled) == "txt"


@pytest.mark.parametrize("spelled", SPELLINGS_OF_TAR_GZ)
def test_a_compound_keeps_both_parts_in_one_form(spelled):
    assert canonical_extension(spelled) == "tar.gz"


def test_no_extension_is_the_empty_string():
    assert canonical_extension("") == ""
    assert canonical_extension(".") == ""
    assert canonical_extensions(("", ".", "Csv", ".csv", "TXT")) == ("csv", "txt")


@pytest.mark.parametrize("group", [HashSettings, ExtractionSettings])
def test_settings_hold_their_extension_filters_in_the_one_form(group):
    held = group(include_extensions=(".TXT", "csv", "*.Tar.Gz"), exclude_extensions=(".BIN",))
    assert held.include_extensions == ("csv", "tar.gz", "txt")
    assert held.exclude_extensions == ("bin",)


def test_a_settings_file_written_dotted_reads_in_the_one_form():
    loaded = Settings.model_validate(
        {
            "hash": {"include_extensions": [".TIF", "tif"]},
            "extraction": {"exclude_extensions": ["LOG"]},
        }
    )
    assert loaded.hash.include_extensions == ("tif",)
    assert loaded.extraction.exclude_extensions == ("log",)


@pytest.mark.parametrize("spelled", SPELLINGS_OF_TXT)
def test_the_policy_selects_whatever_the_notation_on_either_side(spelled):
    # The census stores ``txt``; the tests of reuse passed ``.txt``. Both select, and both exclude.
    for held in (frozenset({"txt"}), frozenset({".txt"}), frozenset({".TXT"})):
        common = {"extensions": held, "size_bytes": 10, "max_file_bytes": None}
        assert policy_selects(include_extensions=(spelled,), exclude_extensions=(), **common)
        assert not policy_selects(include_extensions=(), exclude_extensions=(spelled,), **common)


@pytest.mark.parametrize("spelled", SPELLINGS_OF_TAR_GZ)
def test_the_policy_matches_a_compound_in_any_notation(spelled):
    held = frozenset({"gz", "tar.gz"})
    assert policy_selects(
        extensions=held,
        size_bytes=10,
        include_extensions=(spelled,),
        exclude_extensions=(),
        max_file_bytes=None,
    )


@pytest.mark.parametrize("spelled", (".BIN", "bin", "Bin", ".bin"))
def test_the_hash_filter_reaches_the_file_whatever_its_spelling(spelled):
    settings = Settings(hash=HashSettings(mode=HashMode.FULL, include_extensions=(spelled,)))
    assert digests_wanted(extensions=frozenset({"bin"}), size_bytes=10, settings=settings) == {
        "whole"
    }
    excluded = Settings(hash=HashSettings(mode=HashMode.FULL, exclude_extensions=(spelled,)))
    assert not digests_wanted(extensions=frozenset({"bin"}), size_bytes=10, settings=excluded)


@pytest.mark.parametrize("spelled", (".BIN", "BIN", ".bin"))
def test_the_dive_digests_a_file_its_filter_names_in_any_notation(tmp_path, spelled):
    path = tmp_path / "sample.bin"
    path.write_bytes(b"\x01" * 4096)
    outcome = deepen_entry(
        entry_for(path, "sample.bin"),
        path,
        run_id="run_1",
        observed_at="t",
        settings=Settings(hash=HashSettings(mode=HashMode.FULL, include_extensions=(spelled,))),
    )
    assert "identity.blake3_whole" in {observation.key for observation in outcome.observations}


def test_the_fingerprint_finds_the_extractor_claiming_an_undotted_extension():
    """The census passes ``csv`` and the readers claim ``.csv``, and the narrowing must match.

    Otherwise a reader's version would be missing from the fingerprint of the files it reads, and
    a bump to it would re-read nothing, while every content router's version sat in every file's
    fingerprint.
    """
    settings = Settings(extraction=ExtractionSettings(enabled=True))

    def build(csv_version: str) -> Registry[str]:
        registry: Registry[str] = Registry("extractor")
        registry.register(
            "tabular", version=csv_version, summary="", attributes={"extensions": ".csv,.tsv"}
        )(lambda: "tabular")
        return registry

    def fingerprint(extensions: frozenset[str], version: str) -> str:
        return processing_policy(
            extensions=extensions,
            size_bytes=10,
            settings=settings,
            extractor_registry=build(version),
        ).fingerprint

    for spelled in ("csv", ".csv", "CSV"):
        assert fingerprint(frozenset({spelled}), "1") != fingerprint(frozenset({spelled}), "2")
    assert fingerprint(frozenset({"csv"}), "1") == fingerprint(frozenset({".CSV"}), "1")


@pytest.mark.parametrize("spelled", (".TAR.GZ", "tar.gz", "*.tar.gz", ". Tar.Gz"))
def test_a_record_target_names_one_extension_whatever_its_spelling(spelled):
    assert extension_target(spelled) == "extension:tar.gz"
