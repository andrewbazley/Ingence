"""The sample collection: its promises, held on the smallest tier (and one on the middle).

What is held here is what the collection claims to be: deterministic from a tier and a seed (bytes,
names and modification times), written with its key, manifest and datasheet beside the pool and
never inside it, made of the words of its vocabulary only (the shipped Dictionary's, common biology
words and plain English, no invented word), and untidy in the ways its datasheet lists. The engine's
figures over it are never pinned here. A collection is a tripwire for how a drive can look, never a
target to tune a rule against.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import posixpath
import random
import re
import time
from pathlib import Path

import pytest

from ingence import __version__
from ingence.evaluation.key import FAMILIES, PairFamily, load_key
from ingence.evaluation.sample_lab import (
    DATASHEET_FILE,
    GENERATOR_NAME,
    GENERATOR_VERSION,
    KEY_FILE,
    MANIFEST_FILE,
    TIERS,
    UNSCORED_FILE,
    UNSCORED_SECTIONS,
    generate_sample_lab,
)
from ingence.evaluation.sample_lab_words import words


def unknown_words(paths: list[str]) -> set[str]:
    """The words in ``paths`` that the vocabulary does not hold.

    A word is a run of letters and digits. It is known if it is in the vocabulary as it stands,
    without its digits (``Image0012``, ``25Jul2020``), or without its leading digits (``24h``). A
    member's folder may be a first initial and a family name (``MChen``). An editor's lock file
    (``~$name``) keeps its document's name without the first two letters.
    """
    vocabulary = words()
    known = vocabulary.everything()
    families = {name.casefold() for name in vocabulary.family_names}
    unknown: set[str] = set()
    for path in paths:
        for component in path.split("/"):
            tokens = re.findall(r"[A-Za-z0-9\u00b0\u00b5]+", component)
            if component.startswith("~$") and tokens:
                tokens = tokens[1:]
            for token in tokens:
                lowered = token.casefold()
                if lowered.isdigit():
                    continue
                candidates = (
                    lowered,
                    lowered.rstrip("0123456789"),
                    re.sub(r"\d", "", lowered),
                    lowered.lstrip("0123456789"),
                )
                if any(candidate in known for candidate in candidates):
                    continue
                if lowered[1:] in families:
                    continue
                unknown.add(lowered)
    return unknown


def _walk(root: Path) -> list[tuple[str, bytes | str, float]]:
    """Every file and link under ``root``: its path, its bytes (a link: its target) and its time."""
    found: list[tuple[str, bytes | str, float]] = []
    for folder, _, names in os.walk(root):
        for name in names:
            path = Path(folder, name)
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                found.append((relative, "-> " + os.readlink(path), path.lstat().st_mtime))
            else:
                found.append((relative, path.read_bytes(), path.stat().st_mtime))
    return sorted(found)


def _folders(root: Path) -> list[tuple[str, float]]:
    """Every folder under ``root`` (and the root), with its modification time."""
    found = [(".", root.stat().st_mtime)]
    for folder, names, _ in os.walk(root):
        for name in names:
            path = Path(folder, name)
            if not path.is_symlink():
                found.append((path.relative_to(root).as_posix(), path.stat().st_mtime))
    return sorted(found)


def _digest(root: Path) -> str:
    """One digest of the sorted listing and contents under ``root``: path, bytes, time (seconds)."""
    digest = hashlib.sha256()
    for relative, body, stamp in _walk(root):
        digest.update(relative.encode())
        digest.update(body.encode() if isinstance(body, str) else hashlib.sha256(body).digest())
        digest.update(str(int(stamp)).encode())
    return digest.hexdigest()


@pytest.fixture(scope="module")
def small(tmp_path_factory: pytest.TempPathFactory):
    return generate_sample_lab(tmp_path_factory.mktemp("small") / "lab", tier="S", seed=1)


@pytest.fixture(scope="module")
def middle(tmp_path_factory: pytest.TempPathFactory):
    return generate_sample_lab(tmp_path_factory.mktemp("middle") / "lab", tier="M", seed=1)


def test_the_small_tier_generates_in_under_a_minute(tmp_path: Path) -> None:
    started = time.perf_counter()

    written = generate_sample_lab(tmp_path / "lab", tier="S", seed=7)

    assert time.perf_counter() - started < 60
    assert 400 <= written.manifest["counts"]["files"] <= 600


def test_two_small_collections_from_one_seed_are_the_same_in_bytes_names_and_times(
    tmp_path: Path,
) -> None:
    first = generate_sample_lab(tmp_path / "first", tier="S", seed=3)
    second = generate_sample_lab(tmp_path / "second", tier="S", seed=3)

    assert _walk(first.pool) == _walk(second.pool)
    assert _folders(first.pool) == _folders(second.pool)
    for name in (KEY_FILE, UNSCORED_FILE, DATASHEET_FILE):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()
    # The manifest too, but for nothing: it states no path and no machine.
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert _walk(first.pool)  # not vacuous


def test_the_middle_tier_is_the_same_by_a_digest_of_its_listing_and_contents(
    tmp_path: Path,
) -> None:
    first = generate_sample_lab(tmp_path / "first", tier="M", seed=2)
    second = generate_sample_lab(tmp_path / "second", tier="M", seed=2)

    assert _digest(first.pool) == _digest(second.pool)
    assert first.manifest["pool_digest"] == second.manifest["pool_digest"]
    assert first.key_path.read_bytes() == second.key_path.read_bytes()
    entries = first.manifest["counts"]["files"] + first.manifest["links"]["planned"]
    assert 9_500 <= entries <= 10_600


def test_another_seed_writes_another_collection(tmp_path: Path) -> None:
    first = generate_sample_lab(tmp_path / "first", tier="S", seed=3)
    other = generate_sample_lab(tmp_path / "other", tier="S", seed=4)

    assert _walk(first.pool) != _walk(other.pool)
    assert first.manifest["pool_digest"] != other.manifest["pool_digest"]


def test_the_key_the_manifest_and_the_datasheet_are_beside_the_pool_and_never_inside_it(
    small,
    tmp_path: Path,
) -> None:
    beside = small.pool.parent

    for name in (KEY_FILE, UNSCORED_FILE, MANIFEST_FILE, DATASHEET_FILE):
        assert (beside / name).is_file()
    inside = {path.name for path in small.pool.rglob("*")}
    assert not inside & {KEY_FILE, UNSCORED_FILE, MANIFEST_FILE, DATASHEET_FILE}
    assert not any(path.suffix == ".json" for path in small.pool.rglob("*"))
    assert load_key(small.key_path) == small.key
    assert small.key_path.parent == small.pool.parent


def test_a_collection_is_never_written_over_another(tmp_path: Path) -> None:
    generate_sample_lab(tmp_path / "lab", tier="S", seed=0)
    before = _walk(tmp_path / "lab" / "pool")

    with pytest.raises(FileExistsError):
        generate_sample_lab(tmp_path / "lab", tier="S", seed=1)
    with pytest.raises(ValueError, match="no tier"):
        generate_sample_lab(tmp_path / "other", tier="XL", seed=1)

    assert _walk(tmp_path / "lab" / "pool") == before


def test_a_destination_holding_files_the_command_did_not_write_is_refused_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ingence.evaluation.__main__ import main

    (tmp_path / "somebody").mkdir()
    (tmp_path / "somebody" / "thesis.txt").write_text("kept")
    (tmp_path / "a file").write_text("not a folder")
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(FileExistsError, match="did not write"):
        generate_sample_lab(tmp_path / "somebody", tier="S", seed=1)
    with pytest.raises(FileExistsError, match="not a folder"):
        generate_sample_lab(tmp_path / "a file", tier="S", seed=1)
    code = main(["sample-lab", str(tmp_path / "somebody"), "--tier", "S", "--seed", "1"])

    assert code == 2
    assert "refused" in capsys.readouterr().err
    assert [path.name for path in (tmp_path / "somebody").iterdir()] == ["thesis.txt"]
    assert (tmp_path / "somebody" / "thesis.txt").read_text() == "kept"
    assert generate_sample_lab(empty, tier="S", seed=1).pool.is_dir()  # an empty folder is fine


def test_the_key_answers_every_family_and_every_path_it_names_is_in_the_pool(small) -> None:
    present = {path.relative_to(small.pool).as_posix() for path in small.pool.rglob("*")}

    assert set(small.key.families) == set(FAMILIES)
    for name in FAMILIES:
        if name not in {"contradicted_extension", "empty_file"}:
            assert small.key.families[name].expected_count() > 0, name
    for path in small.key.named_paths():
        assert path in present, path
    undecidable = sum(len(family.undecidable) for family in small.key.pair_families())
    assert undecidable > 0


def test_a_group_is_a_project_s_experiments_and_a_named_group_lists_experiment_folders(
    small,
) -> None:
    group = small.key.families["group"]
    assert isinstance(group, PairFamily)
    experiments = {
        path
        for path, value in small.key.families["experiment_folder"].expected.items()
        if value == "experiment"
    }

    assert group.named
    for members in group.named.values():
        assert set(members) <= experiments
    assert not group.complete


def test_every_word_in_a_generated_name_is_in_the_vocabulary(small) -> None:
    paths = [*small.files, *small.key.named_paths()]
    paths += [path.relative_to(small.pool).as_posix() for path in small.pool.rglob("*")]

    assert not unknown_words(paths)


def test_the_middle_tier_names_hold_only_words_of_the_vocabulary(middle) -> None:
    paths = [path.relative_to(middle.pool).as_posix() for path in middle.pool.rglob("*")]

    assert len(paths) > 9_000
    assert not unknown_words(paths)


def test_no_generated_name_holds_a_pseudo_word_and_the_old_list_of_them_is_gone(small) -> None:
    from ingence.evaluation.generate import STEMS, invented_stems

    pseudo = {*STEMS, *invented_stems(600)}
    found = set()
    for path in small.pool.rglob("*"):
        found.update(word.casefold() for word in re.findall(r"[A-Za-z]+", path.name))
    source = Path(__file__).resolve().parents[1] / "src" / "ingence" / "evaluation"

    assert not found & pseudo
    for module in source.glob("sample_lab*.py"):
        text = module.read_text()
        assert "invented_stems" not in text, module.name
        assert "from ingence.evaluation.generate import" not in text, module.name


def test_members_are_ordinary_made_up_people_and_a_member_who_left_is_in_lab_alumni(small) -> None:
    vocabulary = words()
    entries = json.loads(small.unscored_path.read_text())["sections"]["members"]["entries"]
    first = {name.casefold() for name in vocabulary.first_names}
    family = {name.casefold() for name in vocabulary.family_names}

    assert len(entries) == TIERS["S"].members
    for entry in entries:
        folder = entry["folder"].removeprefix("Lab Alumni/")
        tokens = {token.casefold() for token in re.findall(r"[A-Za-z]+", folder)}
        tokens |= {token[1:] for token in tokens}  # an initial and a family name: MChen
        assert tokens & (first | family), folder
        assert entry["name"] == f"{entry['name'].split()[0]} {entry['name'].split()[1]}"
        assert entry["departed"] == entry["folder"].startswith("Lab Alumni/")
    assert any(entry["departed"] for entry in entries)


def test_modification_times_are_set_from_the_seed_and_none_is_after_2025(small) -> None:
    latest = dt.datetime(2025, 12, 31, 23, 59, tzinfo=dt.UTC).timestamp()
    earliest = dt.datetime(2013, 1, 1, tzinfo=dt.UTC).timestamp()

    stamps = [stamp for _, _, stamp in _walk(small.pool)]
    for folder, _, _ in os.walk(small.pool):
        stamps.append(Path(folder).stat().st_mtime)

    assert max(stamps) <= latest
    assert min(stamps) >= earliest
    assert len({int(stamp) for stamp in stamps}) > 100  # not one moment for all


def test_every_file_holds_bytes_of_its_own_unless_the_key_calls_it_a_copy(small) -> None:
    by_bytes: dict[bytes, set[str]] = {}
    for path, body, _ in _walk(small.pool):
        if isinstance(body, bytes) and body:
            by_bytes.setdefault(body, set()).add(path)
    copies = small.key.families["copy"]
    assert isinstance(copies, PairFamily)
    named = {frozenset(pair) for pair in copies.expected}

    assert not copies.ignored
    for paths in by_bytes.values():
        for first in paths:
            for second in paths:
                if first < second:
                    assert frozenset((first, second)) in named, (first, second)
    for first, second in copies.expected:
        assert (small.pool / first).read_bytes() == (small.pool / second).read_bytes()


def test_an_acquisition_named_by_its_instrument_is_never_a_replicate_the_key_names(middle) -> None:
    """Nothing in `Image0012.nd2` says which condition or replicate it is: the key says so."""
    replicates = middle.key.families["replicate"]
    assert isinstance(replicates, PairFamily)
    default = re.compile(r"/(Image\d+|Series \d+|IMG_\d+|Export\d+|gel_\d+)(?: raw)?\.\w+$")
    named = {path for pair in replicates.expected for path in pair}
    unsure = {path for pair in replicates.undecidable for path in pair}

    found = [path for path in middle.files if default.search(path)]

    assert found
    assert not named & set(found)
    assert unsure & set(found)


def test_the_manifest_states_what_the_pool_holds(small) -> None:
    manifest = small.manifest
    walked = _walk(small.pool)
    files = [(path, body) for path, body, _ in walked if isinstance(body, bytes)]

    assert manifest["generator"]["name"] == GENERATOR_NAME
    assert manifest["generator"]["version"] == GENERATOR_VERSION
    assert manifest["package"]["name"] == "ingence"
    assert manifest["package"]["version"] == __version__
    assert "ingence_version" not in manifest
    assert (manifest["tier"], manifest["seed"], manifest["license"]) == ("S", 1, "CC0-1.0")
    assert manifest["counts"]["files"] == len(files) == len(small.files)
    assert manifest["counts"]["members"] == TIERS["S"].members == 5
    assert sum(manifest["counts"]["by_kind"].values()) == len(files)
    assert manifest["bytes"] == sum(len(body) for _, body in files)
    assert manifest["links"]["planned"] > 0
    assert manifest["links"]["made"] is True or "links not made" in manifest["links"]["note"]
    assert json.loads(small.manifest_path.read_text()) == manifest
    stamped = hashlib.sha256()
    for path, body, stamp in walked:
        if isinstance(body, bytes):
            stamped.update(path.encode() + b"\0" + hashlib.sha256(body).digest())
            stamped.update(str(int(stamp)).encode() + b"\n")
    assert stamped.hexdigest() == manifest["pool_digest"]["value"]
    assert {"nd2", "tiff_stack", "tiff_image", "plate_table"} & set(manifest["counts"]["by_kind"])


def test_links_that_are_made_point_at_nothing_and_the_manifest_says_when_none_were_made(
    small,
) -> None:
    links = [path for path in small.pool.rglob("*") if path.is_symlink()]
    if small.manifest["links"]["made"]:
        assert len(links) == small.manifest["links"]["planned"] > 0
        assert all(not path.exists() for path in links)
        targets = [os.readlink(path) for path in links]
        # An archive link names a path on another server; the others name a folder renamed away.
        assert any(target.startswith("/archive-server/lab/") for target in targets)
        assert any(not os.path.isabs(target) for target in targets)
    else:
        assert links == []
        assert "links not made" in small.manifest["links"]["note"]


def test_a_refused_link_is_recorded_in_the_manifest_and_the_rest_is_still_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise PermissionError("a required privilege is not held")

    monkeypatch.setattr(os, "symlink", refuse)

    written = generate_sample_lab(tmp_path / "lab", tier="S", seed=1)

    assert written.manifest["links"]["made"] is False
    assert "links not made" in written.manifest["links"]["note"]
    assert not any(path.is_symlink() for path in written.pool.rglob("*"))
    assert written.manifest["counts"]["files"] > 400


def test_the_unscored_key_documents_each_section_and_lists_the_mess(small) -> None:
    body = json.loads(small.unscored_path.read_text())

    assert body["format"] == "ingence-answer-key-unscored"
    assert set(body["sections"]) == set(UNSCORED_SECTIONS)
    for name, section in body["sections"].items():
        assert section["what"] == UNSCORED_SECTIONS[name]
    entries = {name: section["entries"] for name, section in body["sections"].items()}
    assert len(entries["members"]) == 5
    (departed,) = entries["departed_member"]
    old = Path(small.pool, departed["old_files_folder"])
    assert old.is_dir() and old.name in {"_old", "archive"}
    assert Path(small.pool, departed["handover_note"]).is_file()
    for name in (
        "complete_experiments",
        "abandoned_experiments",
        "analysis_without_raw",
        "analysis_in_another_members_folder",
        "programs",
        "designs",
        "moved_folders",
        "broken_links",
        "backup_folder_copies",
        "near_copies",
        "archive_links",
        "noise",
    ):
        assert entries[name], name
    for entry in entries["analysis_without_raw"]:
        assert entry["missing"]
        assert not any((small.pool / path).exists() for path in entry["missing"])
        assert entry["named_by"]
        for namer in entry["named_by"]:
            text = (small.pool / namer).read_text()
            assert any(path.rsplit("/", 1)[-1] in text for path in entry["missing"]), namer
    for first, second in entries["near_copies"]:
        one, other = (small.pool / first).read_bytes(), (small.pool / second).read_bytes()
        assert len(one) == len(other)
        assert sum(a != b for a, b in zip(one, other, strict=True)) == 1


def test_the_folders_hold_mixed_naming_conventions_and_every_kind_of_mess(small) -> None:
    experiments = [
        path
        for path, value in small.key.families["experiment_folder"].expected.items()
        if value == "experiment"
    ]
    names = [path.rsplit("/", 1)[-1] for path in experiments]
    spellings = {
        "12Oct2026": any(re.search(r"(?<!\d)\d{2}[A-Z][a-z]{2}\d{4}", name) for name in names),
        "Oct-12-2026": any(re.search(r"[A-Z][a-z]{2}-\d{2}-\d{4}", name) for name in names),
        "20261012": any(re.search(r"(?<!\d)20\d{6}(?!\d)", name) for name in names),
        "10-12-2026": any(re.search(r"(?<!\d)\d{2}-\d{2}-\d{4}", name) for name in names),
        "2026_10_12": any(re.search(r"(?<!\d)20\d{2}_\d{2}_\d{2}", name) for name in names),
        "10-12-26": any(re.search(r"(?<!\d)\d{2}-\d{2}-\d{2}(?!\d)", name) for name in names),
    }
    separators = {sep for sep in (" ", "_", "-") if any(sep in name for name in names)}
    paths = list(small.files)

    assert sum(spellings.values()) >= 4, spellings
    assert separators >= {" ", "-"} or separators >= {" ", "_"}
    assert any(re.search(r" copy\.| - Copy\.|Copy of ", path) for path in paths)
    assert any(re.search(r"[_ ](2|new|edit|fixed|final|\d{8})\.\w+$", path) for path in paths)
    assert any("/backup/" in path for path in paths)
    assert any(path.endswith("/.DS_Store") or path.endswith("/Thumbs.db") for path in paths)
    assert any(path.endswith(".nd2") for path in paths)
    assert any(path.endswith((".tif", ".tiff")) for path in paths)
    assert any(path.endswith((".py", ".m")) for path in paths)
    assert any(path.endswith((".pptx", ".docx", ".pdf", ".xlsx")) for path in paths)
    empty_folders = [p for p in small.pool.rglob("*") if p.is_dir() and not any(p.iterdir())]
    assert empty_folders
    empties = [p for p in small.pool.rglob("*") if p.is_file() and p.stat().st_size == 0]
    assert all(p.suffix == ".csv" for p in empties)


def test_a_header_shaped_like_a_czi_is_said_to_be_one_in_every_place_it_matters(small) -> None:
    kinds = small.manifest["counts"]["by_kind"]
    sheet = small.datasheet_path.read_text()

    assert "not readable" in sheet
    assert any("not readable CZI" in note for note in small.manifest["notes"])
    if "czi_shaped_header" in kinds:
        czi = [path for path in small.files if path.endswith(".czi")]
        assert len(czi) == kinds["czi_shaped_header"]
        assert all((small.pool / path).read_bytes()[:10] == b"ZISRAWFILE" for path in czi)


def test_the_datasheet_has_the_datasheet_questions_and_the_collection_s_own_figures(small) -> None:
    sheet = small.datasheet_path.read_text()
    counts = small.manifest["counts"]

    for heading in (
        "## Motivation",
        "## Composition",
        "## Collection process",
        "## Preprocessing, cleaning and labeling",
        "## Uses",
        "## Distribution",
        "## Maintenance",
    ):
        assert heading in sheet
    assert f"{counts['files']:,}" in sheet
    assert "tier **S**, seed **1**" in sheet
    assert "CC0 1.0" in sheet
    assert "not real" in sheet.casefold()
    assert "one person's model" in sheet


def test_nothing_the_collection_ships_holds_a_home_path_or_an_emoji(small) -> None:
    texts = [
        small.datasheet_path.read_text(),
        small.manifest_path.read_text(),
        small.unscored_path.read_text(),
        small.key_path.read_text(),
    ]
    source = Path(__file__).resolve().parents[1] / "src" / "ingence" / "evaluation"
    for name in (
        "sample_lab.py",
        "sample_lab_datasheet.py",
        "sample_lab_drive.py",
        "sample_lab_people.py",
        "sample_lab_shapes.py",
        "documents.py",
    ):
        texts.append((source / name).read_text())
    emoji = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
    for text in texts:
        assert "/Users/" not in text
        assert not emoji.findall(text)


def test_the_command_writes_the_collection_and_says_where(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ingence.evaluation.__main__ import main

    assert main(["sample-lab", str(tmp_path / "made"), "--tier", "S", "--seed", "5"]) == 0

    printed = capsys.readouterr().out
    assert "tier S, seed 5" in printed
    assert (tmp_path / "made" / "pool").is_dir()
    assert (tmp_path / "made" / KEY_FILE).is_file()


def test_the_engine_s_census_reads_the_small_pool_and_the_pool_is_unchanged(
    small, tmp_path: Path
) -> None:
    from ingence.core.ids import run_id
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    before = _walk(small.pool)
    project = Project.create(
        tmp_path / "project", created_at="t", roots=[small.pool], labels=["lab"]
    )

    census = run_census(
        project, run_id=run_id(started_at="2026-01-01T00:00:01Z", nonce="lab"), started_at="t"
    )

    assert census.published
    assert _walk(small.pool) == before


def test_the_existing_scorer_reads_the_key_and_counts_every_family_it_names(
    small, tmp_path: Path
) -> None:
    """The scorer's counts are never pinned here; that it reads this key, and every family, is."""
    from ingence.core.ids import run_id
    from ingence.core.project import Project
    from ingence.evaluation.score import score_project
    from ingence.scan.census import run_census

    project = Project.create(tmp_path / "project", created_at="t", roots=[small.pool])
    run_census(
        project, run_id=run_id(started_at="2026-01-01T00:00:01Z", nonce="scored"), started_at="t"
    )

    report = score_project(small.key_path, project.path)

    (column,) = report["columns"].values()
    assert set(column["families"]) == set(FAMILIES)
    expected = column["families"]["name_date"]["rules"]["tokeniser"]["expected"]
    assert expected == small.key.families["name_date"].expected_count()


def test_every_size_has_a_mac_a_windows_and_a_linux_user_and_the_small_pool_shows_what_they_leave(
    small,
) -> None:
    from ingence.evaluation import sample_lab

    for spec in TIERS.values():
        assert set(sample_lab._SYSTEMS[: spec.members]) == {"mac", "windows", "linux"}
    kinds = small.manifest["counts"]["by_kind"]
    assert kinds["noise_ds_store"] > 0
    assert kinds["noise_thumbs_db"] > 0


def test_system_files_are_copies_inside_a_folder_a_person_copied_into_backup_and_nowhere_else(
    small,
) -> None:
    copies = small.key.families["copy"]
    assert isinstance(copies, PairFamily)
    system = {
        path for path in small.files if path.endswith(("/.DS_Store", "/Thumbs.db")) or "/~$" in path
    }
    named = {path for pair in copies.expected for path in pair}
    inside = {path for path in system if "/backup/" in path}
    outside = system - inside

    assert not copies.ignored
    assert inside <= named
    for first, second in copies.expected:
        # A system file is named as a copy only against its own copy made by copying a folder.
        if first in system or second in system:
            assert "/backup/" in second
    assert outside - named  # most system files outside backup/ are in no copy pair at all
    for text in ("backup/", "anywhere else no two are alike"):
        assert text in small.key.describes["system_files"]


def test_nothing_made_from_a_file_is_older_than_the_file_it_was_made_from(small) -> None:
    derivations = small.key.families["derivation"]
    assert isinstance(derivations, PairFamily)

    assert derivations.expected
    for source, target in derivations.expected:
        assert (small.pool / source).stat().st_mtime <= (small.pool / target).stat().st_mtime, (
            source,
            target,
        )


def test_a_departed_member_s_files_are_none_of_them_newer_than_the_day_they_left(small) -> None:
    body = json.loads(small.unscored_path.read_text())
    (departed,) = body["sections"]["departed_member"]["entries"]
    last_day = dt.datetime(departed["last_active"], 12, 31, 23, 59, tzinfo=dt.UTC).timestamp()

    stamps = [path.lstat().st_mtime for path in (small.pool / departed["folder"]).rglob("*")]

    assert stamps
    assert max(stamps) <= last_day


def test_every_python_script_and_notebook_cell_is_valid_python_and_none_is_ever_run(small) -> None:
    import ast

    scripts = [path for path in small.pool.rglob("*.py") if not path.is_symlink()]
    notebooks = [path for path in small.pool.rglob("*.ipynb") if not path.is_symlink()]

    assert scripts
    for path in scripts:
        ast.parse(path.read_text())
    for path in notebooks:
        for cell in json.loads(path.read_text())["cells"]:
            if cell["cell_type"] == "code":
                ast.parse("".join(cell["source"]))
                assert cell["outputs"] == [] and cell["execution_count"] is None


def test_the_manifest_counts_each_kind_of_mess_and_the_datasheet_says_how_many_here(small) -> None:
    mess = small.manifest["mess"]
    sheet = small.datasheet_path.read_text()

    assert set(mess) == set(UNSCORED_SECTIONS)
    assert mess["members"] == 5
    assert mess["complete_experiments"] > 0 and mess["abandoned_experiments"] > 0
    assert sum(mess[name] for name in ("complete_experiments", "partial_experiments")) > 0
    for name in UNSCORED_SECTIONS:
        assert f"({mess[name]:,} here)" in sheet


def test_a_backup_never_fails_on_a_csv_that_holds_no_digit_to_change() -> None:
    """A TIFF saved as a .csv can hold no digit byte: it is copied whole, with no near-copy."""
    from ingence.evaluation.sample_lab_drive import Drive
    from ingence.evaluation.sample_lab_people import Member
    from ingence.evaluation.sample_lab_shapes import backup_tree

    moment = dt.datetime(2022, 3, 4, 10, tzinfo=dt.UTC)
    member = Member(
        given="Maria",
        family="Chen",
        folder_name="Maria Chen",
        folder="Maria Chen",
        first=2020,
        last=2025,
        departed=False,
        system="mac",
        kinds=("nd2",),
        top="flat",
        year_style="{year}",
        eras=(),
        scripting="none",
        tidy=0.5,
    )
    drive = Drive(random=random.Random("a seed"))
    drive.file(
        "Maria Chen/2022-03-04 HeLa/export.csv", b"II*\x00\x01\x02", moment, kind="tiff_named_csv"
    )

    copied = backup_tree(drive, member, "Maria Chen/2022-03-04 HeLa", moment, near_copy=True)

    assert copied == "Maria Chen/backup/2022-03-04 HeLa"
    assert (
        drive.files[f"{copied}/export.csv"] == drive.files["Maria Chen/2022-03-04 HeLa/export.csv"]
    )
    assert not drive.unscored.get("near_copies")


def test_a_figure_with_no_script_has_its_derivations_undecidable_not_absent(middle) -> None:
    derivations = middle.key.families["derivation"]
    assert isinstance(derivations, PairFamily)
    made = {target for _, target in derivations.expected}
    unsure = {target for _, target in derivations.undecidable}
    copied = {path for pair in middle.key.families["copy"].expected for path in pair}
    figures = [
        path
        for path in middle.files
        if path.endswith("figure.png")
        and "/backup/" not in path
        and "reanalysis" not in path
        and path not in copied
    ]

    assert figures
    for figure in figures:
        assert figure in made or figure in unsure, figure
    assert set(figures) & unsure  # some figure has no script, and says so
    assert set(figures) & made  # and some has one


def test_a_near_copy_keeps_the_answer_about_its_extension() -> None:
    """A TIFF saved as a .csv, backed up and changed by a digit, is still a TIFF saved as a .csv."""
    from ingence.evaluation.sample_lab_drive import Drive
    from ingence.evaluation.sample_lab_people import Member
    from ingence.evaluation.sample_lab_shapes import backup_tree

    moment = dt.datetime(2022, 3, 4, 10, tzinfo=dt.UTC)
    member = Member(
        given="Maria",
        family="Chen",
        folder_name="Maria Chen",
        folder="Maria Chen",
        first=2020,
        last=2025,
        departed=False,
        system="mac",
        kinds=("nd2",),
        top="flat",
        year_style="{year}",
        eras=(),
        scripting="none",
        tidy=0.5,
    )
    drive = Drive(random=random.Random("a seed"))
    original = "Maria Chen/2022-03-04 HeLa/export.csv"
    drive.file(original, b"II*\x00 12345 67890", moment, kind="tiff_named_csv")
    drive.value("contradicted_extension", original, "image/tiff")

    copied = backup_tree(drive, member, "Maria Chen/2022-03-04 HeLa", moment, near_copy=True)

    near = f"{copied}/export.csv"
    assert drive.unscored["near_copies"] == [[original, near]]
    assert drive.files[near] != drive.files[original]
    assert drive.values["contradicted_extension"][near] == "image/tiff"


def _member_names(small) -> dict[str, tuple[int, int]]:
    """Each member's name (in lower case) and the years they were here."""
    body = json.loads(small.unscored_path.read_text())
    return {
        entry["name"].casefold(): (entry["first_year"], entry["last_year"])
        for entry in body["sections"]["members"]["entries"]
    }


def test_no_document_names_a_member_as_its_author_outside_the_years_they_were_there(small) -> None:
    import zipfile

    years = _member_names(small)
    seen = 0
    for path in small.pool.rglob("*"):
        if path.suffix not in {".docx", ".pptx", ".xlsx"} or path.is_symlink():
            continue
        if path.name.startswith("~$"):
            continue
        with zipfile.ZipFile(path) as package:
            core = package.read("docProps/core.xml").decode()
        match = re.search(r"<dc:creator>([^<]*)</dc:creator>", core)
        assert match
        author = match.group(1).casefold()
        if author not in years:
            continue
        seen += 1
        stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
        first, last = years[author]
        assert first <= stamp.year <= last, (path.name, author, stamp)
    assert seen


def test_a_program_is_dated_within_the_years_of_the_member_who_holds_it(small) -> None:
    sections = json.loads(small.unscored_path.read_text())["sections"]
    years = {
        entry["folder"]: (entry["first_year"], entry["last_year"])
        for entry in sections["members"]["entries"]
    }
    folders = [entry["folder"] for entry in sections["programs"]["entries"]]

    assert folders
    for folder in folders:
        owner = next(name for name in years if folder == name or folder.startswith(f"{name}/"))
        first, last = years[owner]
        for path in (small.pool / folder).rglob("*"):
            year = dt.datetime.fromtimestamp(path.lstat().st_mtime, dt.UTC).year
            assert first <= year <= last, (folder, year)


def test_every_size_plans_as_many_broken_links_as_it_asks_for_and_a_name_clash_loses_none(
    small, tmp_path: Path
) -> None:
    def broken(lab) -> int:
        sections = json.loads(lab.unscored_path.read_text())["sections"]
        return len(sections["broken_links"]["entries"])

    assert broken(small) == TIERS["S"].links
    for seed in range(2, 8):
        written = generate_sample_lab(tmp_path / f"seed{seed}", tier="S", seed=seed)
        assert broken(written) == TIERS["S"].links, seed


def test_the_command_function_is_exported_with_the_rest_of_the_module() -> None:
    from ingence.evaluation import sample_lab

    for name in sample_lab.__all__:
        assert hasattr(sample_lab, name), name
    assert "describe" in sample_lab.__all__


def test_the_datasheet_answers_the_questions_the_first_one_skipped(small) -> None:
    sheet = small.datasheet_path.read_text()

    for question in (
        "Is it all possible instances, or a sample? Are there recommended splits?",
        "Are there errors, sources of noise or redundancies?",
        "Is it self-contained?",
        "Has it been used already?",
        "Who created it, and on whose behalf?",
        "Who funded it?",
        "Who maintains it, and how can they be reached?",
        "Do older versions stay available? Can others contribute?",
    ):
        assert question in sheet, question
    assert "Astera Institute funded the work" in sheet
    assert "one person's judgement" in sheet or "that person's judgement" in sheet
    assert (
        "Contact: hello@ingence.org." in sheet
        and "<where corrections to the key are posted>" in sheet
    )
    assert "tested on one Mac" in sheet and "two seconds" in sheet
    flat = " ".join(sheet.split())
    assert "stores times with no time zone" in flat and "as a tar file" in flat
    assert "no rule is tuned to make these numbers look better" in flat
    assert "nobody tunes" not in flat
    # It names its author and its funder, and says nothing about who else did or did not take part.
    for phrase in ("no one else", "no one was", "nobody labeled", "was involved", "were involved"):
        assert phrase not in flat.casefold(), phrase
    assert "Who created it, and on whose behalf?** The Ingence project made the generator" in flat
    assert (
        "files saved again in other folders under their own names or under a copy-of name" in flat
    )
    assert "folders copied in part into a backup folder" in flat
    assert "on any machine" not in sheet
    assert "tier S, seed 1" in sheet  # the runs it names, with a date and no figure
    assert "on 13 October 2026" in " ".join(sheet.split())
    assert "This collection is that one." in sheet
    assert "no funder is named" not in sheet


def test_a_summary_workbook_built_from_the_analysis_tables_is_analysis_where_its_folder_says_so(
    small,
) -> None:
    """The stage of a workbook is declined only where a version chain declines the whole chain."""
    stage = small.key.families["stage"]
    chained = {
        path
        for chain in json.loads(small.unscored_path.read_text())["sections"]["version_chains"][
            "entries"
        ]
        for path in chain
    }
    workbooks = [
        path
        for path in small.files
        if re.search(r"[ _-]summary\.xlsx$", path)
        and "/backup/" not in path
        and not path.startswith("shared/")
        and path not in chained
    ]
    said = {path: stage.expected[path] for path in workbooks if path in stage.expected}
    unsure = [path for path in workbooks if path in stage.undecidable]

    assert workbooks
    assert not set(workbooks) & set(stage.ignored)  # none is declined
    assert set(said.values()) <= {"analysis"}
    assert len(said) + len(unsure) == len(workbooks)  # the rest are undecidable, never another word
    assert "restored_after_review" not in small.key.describes


def _dates_inside(path: Path) -> list[dt.datetime]:
    """The dates a document says it was made and changed: an Office package's, a PDF's."""
    import zipfile

    if path.suffix in {".docx", ".pptx", ".xlsx"}:
        with zipfile.ZipFile(path) as package:
            core = package.read("docProps/core.xml").decode()
        return [
            dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
            for text in re.findall(r'W3CDTF">([^<]+)<', core)
        ]
    text = path.read_bytes().decode("latin-1")
    return [
        dt.datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=dt.UTC)
        for stamp in re.findall(r"/(?:Creation|Mod)Date \(D:(\d{14})Z\)", text)
    ]


def _documents_dated_wrongly(lab) -> tuple[int, list[str]]:
    """How many documents were checked, and which say a date later than their file's time, or a
    year outside the years of the member named as their author."""
    years = _member_names(lab)
    checked = 0
    wrong = []
    for path in lab.pool.rglob("*"):
        if path.is_symlink() or path.suffix not in {".docx", ".pptx", ".xlsx", ".pdf"}:
            continue
        if path.name.startswith("~$"):
            continue
        author = None
        if path.suffix != ".pdf":
            import zipfile

            with zipfile.ZipFile(path) as package:
                found = re.search(
                    r"<dc:creator>([^<]*)</dc:creator>", package.read("docProps/core.xml").decode()
                )
            author = found.group(1).casefold() if found else None
        inner = _dates_inside(path)
        assert inner, path
        checked += 1
        for moment in inner:
            if moment.timestamp() > path.stat().st_mtime + 1 or moment.year > 2025:
                wrong.append(f"{path.name}: dated {moment:%Y-%m-%d} inside")
            elif author in years and not years[author][0] <= moment.year <= years[author][1]:
                wrong.append(f"{path.name}: dated {moment.year} inside, by {author}")
    return checked, wrong


def test_no_document_carries_a_date_later_than_its_own_file_time_or_than_its_author_s_years(
    small, tmp_path: Path
) -> None:
    """Capped like file times: a revision or a report written after a member left, or after 2025,
    says the same day inside as the file does outside. Seed 3 puts documents past the end."""
    other = generate_sample_lab(tmp_path / "other", tier="S", seed=3)

    for lab in (small, other):
        checked, wrong = _documents_dated_wrongly(lab)
        assert checked > 10
        assert not wrong, wrong


def test_a_backup_copy_is_never_dated_before_the_file_it_copies(small, tmp_path: Path) -> None:
    """Dated after a folder's newest file, not its last by name (seed 11 had one 10 days early)."""
    other = generate_sample_lab(tmp_path / "other", tier="S", seed=11)

    for lab in (small, other):
        copies = json.loads(lab.unscored_path.read_text())["sections"]["backup_folder_copies"]
        checked = 0
        for entry in copies["entries"]:
            for path in (lab.pool / entry["copy"]).rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(lab.pool / entry["copy"])
                original = lab.pool / entry["original"] / relative
                assert path.stat().st_mtime >= original.lstat().st_mtime, (path.name, original.name)
                checked += 1
        assert checked > 0


def test_two_collections_made_in_other_processes_with_other_hash_seeds_and_zones_agree(
    tmp_path: Path,
) -> None:
    """The in-process tests cannot see an order that follows the hash seed or a time zone."""
    import subprocess
    import sys

    import ingence

    source = Path(ingence.__file__).resolve().parent.parent  # the source tree under test

    def make(name: str, hash_seed: str, zone: str) -> Path:
        (tmp_path / f"cwd-{name}").mkdir()
        environment = {
            **os.environ,
            "PYTHONHASHSEED": hash_seed,
            "TZ": zone,
            "LC_ALL": "C",
            "PYTHONPATH": str(source),
        }
        command = [sys.executable, "-m", "ingence.evaluation", "sample-lab"]
        command += [str(tmp_path / name), "--tier", "S", "--seed", "1"]
        subprocess.run(
            command, check=True, env=environment, cwd=tmp_path / f"cwd-{name}", capture_output=True
        )
        return tmp_path / name

    first = make("first", "1", "UTC")
    second = make("second", "7", "Pacific/Auckland")

    assert _walk(first / "pool") == _walk(second / "pool")
    assert _folders(first / "pool") == _folders(second / "pool")
    for name in (KEY_FILE, UNSCORED_FILE, MANIFEST_FILE, DATASHEET_FILE):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_what_changes_from_release_to_release_is_set_in_one_place_in_the_renderer(small) -> None:
    from dataclasses import fields, replace

    from ingence.evaluation.sample_lab_datasheet import RELEASE, datasheet

    sheet = small.datasheet_path.read_text()
    placeholders = set(re.findall(r"<[a-z][^<>\n]*>", sheet)) - {"<folder>"}
    values = {getattr(RELEASE, field.name) for field in fields(RELEASE)}

    assert placeholders  # the fields left for whoever archives it
    assert placeholders <= values  # and every one is the renderer's own, nowhere else
    filled = replace(
        RELEASE,
        contact="lab@example.org",
        engine_runs=(("S", 1, True), ("M", 1, False)),
        engine_runs_on="1 January 2027",
    )
    other = " ".join(datasheet(small.manifest, UNSCORED_SECTIONS, filled).split())
    assert "Contact: lab@example.org." in other and "hello@ingence.org" not in other
    assert "on 1 January 2027" in other
    assert (
        "tier M, seed 1 was not scored against the key written with it; the others were." in other
    )
    assert "This collection is one of them." in other


def test_the_datasheet_says_which_tiers_the_engine_read_and_that_each_was_scored(small) -> None:
    flat = " ".join(small.datasheet_path.read_text().split())

    assert "read tier S, seed 1 of this generator version" in flat
    assert "It was scored against the key written with it." in flat
    assert "The middle and large tiers have not been read by the engine." in flat


# ---------------------------------------------------------------------------
# The collection's untidiness: labels, dates, links, programs, copies, backups and the demo.
# ---------------------------------------------------------------------------


def _paths(lab) -> list[str]:
    return sorted(path.relative_to(lab.pool).as_posix() for path in lab.pool.rglob("*"))


def test_a_well_label_is_a_position_never_a_replicate_and_no_greek_letter_marks_a_name(
    small, middle
) -> None:
    greek = re.compile(
        r"(?i)(?<![a-z])(alpha|beta|gamma|delta|epsilon|zeta|theta|kappa|lambda|sigma|omega)(?![a-z])"
    )
    for lab in (small, middle):
        for path in _paths(lab):
            name = path.rsplit("/", 1)[-1]
            assert not re.search(r"(?i)\bwell[ _-]?[A-H]\d{2}\b", name), path
            assert not greek.search(name), path
            assert not re.search(r"[Ͱ-Ͽ]", path), path


def test_replicates_are_labeled_four_ways_and_a_raw_file_keeps_the_instrument_counter(
    middle,
) -> None:
    names = [path.rsplit("/", 1)[-1] for path in _paths(middle)]
    cells = words().biology["cell_lines"]
    cell = "|".join(re.escape(name) for name in cells)

    labels = {
        "rep": any(re.search(r"[ _-]rep\d", name, re.IGNORECASE) for name in names),
        "R": any(re.search(r"[ _-]R\d+[ _.-]", name) for name in names),
        "take": any(re.search(r"[ _-]take\d", name, re.IGNORECASE) for name in names),
        "bare": any(re.search(r"[ _-]\d{2}[ _.-]", name) for name in names),
    }
    # A label shows only in the files a member names (a raw file keeps the instrument's counter).
    assert sum(labels.values()) >= 3, labels
    assert any(
        re.search(rf"(?:{cell})[ _-].*[ _-]\d{{3}}\.(nd2|tif|lif|czi)$", name) for name in names
    )
    # The instrument's own name, with its counter, and the sample's name followed by the counter.
    assert any(re.fullmatch(r"(Image|IMG_|Export|gel_)\d{4}\.\w+", name) for name in names)
    assert any(re.search(rf"(?:^|_)(?:{cell})_.*_\d{{3}}\.(nd2|tif)$", name) for name in names)


def test_dates_in_names_are_the_acquisition_date_in_the_spellings_the_collection_uses(
    middle,
) -> None:
    names = [path.rsplit("/", 1)[-1] for path in _paths(middle)]
    patterns = {
        "12Oct2026": r"(?<!\d)\d{2}[A-Z][a-z]{2}\d{4}(?!\d)",
        "Oct-12-2026": r"[A-Z][a-z]{2}-\d{2}-\d{4}",
        "20261012": r"(?<!\d)20\d{6}(?!\d)",
        "10-12-2026": r"(?<!\d)\d{2}-\d{2}-20\d{2}(?!\d)",
        "2026_10_12": r"(?<!\d)20\d{2}_\d{2}_\d{2}",
        "10-12-26": r"(?<!\d)\d{2}-\d{2}-\d{2}(?![\d-])",
    }

    for spelling, pattern in patterns.items():
        assert any(re.search(pattern, name) for name in names), spelling
    # A name that states a date in a file (not a folder) states the same day as its experiment.
    stated = {
        path: day
        for path, day in middle.key.families["name_date"].expected.items()
        if "." in path.rsplit("/", 1)[-1]
    }
    assert stated
    for path, day in stated.items():
        assert 2013 <= int(day[:4]) <= 2025, path


def test_the_design_of_each_experiment_is_in_the_key_and_a_small_reader_reads_it(middle) -> None:
    from ingence.evaluation.sample_lab_design import read_designs

    designs = read_designs(middle.unscored_path)
    experiments = {
        path
        for path, value in middle.key.families["experiment_folder"].expected.items()
        if value == "experiment"
    }
    by_folder = {design.folder: design for design in designs}

    assert len(designs) > 300
    assert set(by_folder) <= experiments
    assert len(by_folder) == len(designs)
    factors = {factor for design in designs for factor in design.factors()}
    assert factors == {"treatment", "dose", "time"}
    shapes = set()
    for design in designs:
        assert design.cells
        for cell in design.cells:
            assert cell.replicates >= 1
            assert 0 <= cell.raw_files <= cell.replicates
            if design.outcome == "analysis_only":
                assert cell.raw_files == 0
            else:
                assert cell.raw_files == cell.replicates
            if cell.factors == ("treatment",) and cell.level_names == ("control",):
                shapes.add("control")
            elif cell.factors == ("treatment", "dose"):
                shapes.add("treatment x dose")
            elif cell.factors == ("treatment", "time") and cell.level_names[0] == "control":
                shapes.add("control x time")
    assert shapes == {"control", "treatment x dose", "control x time"}
    replicates = sorted({cell.replicates for design in designs for cell in design.cells})
    assert replicates[:3] == [1, 2, 3]  # one to three replicates, and a few experiments with more
    assert any(len({cell.replicates for cell in design.cells}) > 1 for design in designs)


def test_no_module_of_the_generator_imports_anything_from_the_engine() -> None:
    """The key and the design are written from the generator's own choices: nothing the engine
    stores, scans or infers is imported by any `sample_lab*` module."""
    import ast

    source = Path(__file__).resolve().parents[1] / "src" / "ingence" / "evaluation"
    modules = sorted(source.glob("sample_lab*.py"))
    allowed_prefixes = (
        "ingence.evaluation.sample_lab",
        "ingence.evaluation.documents",
        "ingence.evaluation.formats",
        "ingence.evaluation.key",
    )
    imported: set[str] = set()
    for module in modules:
        for node in ast.walk(ast.parse(module.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

    assert len(modules) >= 6
    for name in imported:
        if name == "ingence" or not name.startswith("ingence"):
            continue
        assert name.startswith(allowed_prefixes), name


def test_archive_links_stand_for_the_oldest_raw_files_and_point_at_another_server(middle) -> None:
    sections = json.loads(middle.unscored_path.read_text())["sections"]
    entries = sections["archive_links"]["entries"]
    links = [path for path in middle.pool.rglob("*") if path.is_symlink()]
    archive = [path for path in links if os.readlink(path).startswith("/archive-server/lab/")]
    total = middle.manifest["counts"]["files"] + len(links)
    stage = middle.key.families["stage"]

    assert len(archive) == len(entries) > 0
    assert 0.07 <= len(archive) / total <= 0.13
    assert all(not path.exists() for path in archive)
    for entry in entries[:50]:
        assert entry["target"] == f"/archive-server/lab/{entry['link']}"
        assert entry["was"] in {
            "nd2", "czi_shaped_header", "lif_shaped_header", "oib_shaped_container",
            "tiff_stack", "tiff_image", "plate_table", "xlsx_plate_export",
        }  # fmt: skip
        assert entry["link"] in stage.ignored  # the key accepts any answer about a link
    years = [dt.datetime.fromtimestamp(path.lstat().st_mtime, dt.UTC).year for path in archive]
    all_raw_years = sorted(years)
    assert all_raw_years[len(all_raw_years) // 2] <= 2021  # the older experiments, first


def test_a_copy_of_fiji_is_laid_out_as_an_installed_program(middle) -> None:
    programs = json.loads(middle.unscored_path.read_text())["sections"]["programs"]["entries"]
    fiji = [entry["folder"] for entry in programs if entry["kind"] == "Fiji application"]
    kinds = {entry["kind"] for entry in programs}

    assert fiji
    assert kinds == {"Python package", "MATLAB toolbox", "Fiji application"}
    for folder in fiji:
        root = middle.pool / folder
        assert root.name == "Fiji.app"
        for part in ("jars", "plugins", "luts", "macros"):
            assert any((root / part).iterdir()), part
        assert list((root / "plugins").glob("*.jar"))
        assert list((root / "macros").glob("*.ijm"))
    assert not list(middle.pool.rglob(".git"))
    assert not list(middle.pool.rglob("node_modules"))
    assert not list(middle.pool.rglob("pyvenv.cfg"))


def test_the_middle_tier_holds_each_kind_of_file_the_brief_names_in_a_share_near_its_own(
    middle,
) -> None:
    kinds = middle.manifest["counts"]["by_kind"]
    files = middle.manifest["counts"]["files"]

    def share(*names: str) -> float:
        return sum(kinds.get(name, 0) for name in names) / files

    assert share("tiff_image", "tiff_stack") >= 0.15
    assert share("nd2") >= 0.04
    assert share("csv", "txt_table", "plate_table") >= 0.15
    assert 0.01 <= share("xml_sidecar", "json_sidecar") <= 0.15
    assert 0.01 <= share("mat_file") <= 0.10
    assert share("png", "jpg", "gif") >= 0.02
    assert 0.01 <= share("pdf") <= 0.06
    assert 0.01 <= share("docx", "xlsx", "pptx", "doc", "xls", "ppt") <= 0.06
    assert {"docx", "xlsx", "pptx"} <= set(kinds)
    assert {"doc", "xls", "ppt"} & set(kinds)
    assert 0.005 <= share("zip") <= 0.04
    assert 0.002 <= share("czi_shaped_header", "lif_shaped_header", "oib_shaped_container") <= 0.04
    assert share("python_script", "matlab_script", "notebook", "fiji_macro") >= 0.005
    # Never more than a trace of what the censuses held under one in a hundred.
    assert share("ab1_trace", "plasmid_map", "avi", "svg", "pzfx", "fcs") == 0


def test_a_share_of_the_files_has_its_name_and_size_in_another_place_too(middle) -> None:
    seen: dict[tuple[str, int], int] = {}
    total = 0
    for path in middle.pool.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        total += 1
        key = (path.name, path.stat().st_size)
        seen[key] = seen.get(key, 0) + 1
    shared = sum(count for count in seen.values() if count > 1)

    assert 0.10 <= shared / total <= 0.35


def test_data_may_lie_in_another_member_s_folder_and_analysis_may_be_kept_elsewhere(middle) -> None:
    sections = json.loads(middle.unscored_path.read_text())["sections"]
    data = sections["data_in_another_members_folder"]["entries"]
    elsewhere = sections["analysis_elsewhere"]["entries"]
    experiments = sum(
        1
        for value in middle.key.families["experiment_folder"].expected.values()
        if value == "experiment"
    )

    assert data and elsewhere
    assert 0.02 <= len(data) / experiments <= 0.12
    for entry in data[:20]:
        assert entry["folder"].split("/")[0] != entry["data_in"].split("/")[0]
        assert any((middle.pool / entry["data_in"]).iterdir())
    for entry in elsewhere[:20]:
        assert (middle.pool / entry["analysis"]).is_dir()
        assert not entry["analysis"].startswith(entry["experiment"])


def test_files_are_saved_again_with_a_mark_and_the_marks_are_the_six_the_brief_lists(
    middle,
) -> None:
    chains = json.loads(middle.unscored_path.read_text())["sections"]["version_chains"]["entries"]
    marks = set()
    for chain in chains:
        for later in chain[1:]:
            stem = later.rsplit(".", 1)[0]
            marks.add(
                re.sub(r"^.*?(?:[_ ])(2|new|edit|fixed|final|\d{8})$", r"\1", stem)
                if re.search(r"[_ ](2|new|edit|fixed|final|\d{8})$", stem)
                else "?"
            )
    assert "?" not in marks
    assert len(marks - {m for m in marks if m.isdigit()}) >= 4
    assert any(mark.isdigit() for mark in marks)  # a date


def test_a_backup_holds_part_of_a_folder_inside_the_person_s_folder(middle) -> None:
    copies = json.loads(middle.unscored_path.read_text())["sections"]["backup_folder_copies"]
    entries = copies["entries"]

    assert entries
    assert any(entry["files"] < entry["of"] for entry in entries)
    assert all(entry["files"] <= entry["of"] for entry in entries)
    for entry in entries[:50]:
        member = entry["original"].split("/")[0]
        if member == "Lab Alumni":
            member = "/".join(entry["original"].split("/")[:2])
        assert entry["copy"].startswith(f"{member}/")


def test_the_large_tier_plans_a_few_experiments_with_about_fifty_replicates() -> None:
    assert TIERS["L"].big >= 3 and TIERS["M"].big == 0 == TIERS["S"].big
    assert TIERS["L"].sequencing > 0 and TIERS["L"].odds > 0
    assert TIERS["L"].departed == (1, 5)
    assert TIERS["L"].fijis < TIERS["L"].members


def test_no_live_source_file_calls_anything_messy_or_names_the_old_command() -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "ingence"
    forbidden = re.compile("mes" + "sy", re.IGNORECASE)
    found = [
        path.name
        for path in source.rglob("*")
        if path.suffix in {".py", ".yaml", ".md", ".txt"} and forbidden.search(path.read_text())
    ]

    assert not found


def test_the_demo_command_makes_one_folder_runs_four_steps_and_opens_the_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.cli import main as cli
    from ingence.cli.demo import run_demo

    steps: list[list[str]] = []

    def fake_app(args: list[str], standalone_mode: bool) -> int:
        assert standalone_mode is False
        steps.append(args)
        return 0

    monkeypatch.setattr(cli, "app", fake_app)
    before = sorted(path.name for path in tmp_path.iterdir())

    assert run_demo(tmp_path / "demo") == 0

    assert before == []
    assert [step[0] for step in steps] == ["init", "index", "dive", "infer", "see"]
    assert steps[0][1] == str(tmp_path / "demo" / "project")
    assert steps[0][3] == str(tmp_path / "demo" / "pool")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["demo"]
    assert (tmp_path / "demo" / "pool").is_dir()
    # The project is beside the pool, never inside it; the engine's folder is the one made.
    assert not (tmp_path / "demo" / "pool" / "project").exists()


def test_the_demo_says_its_time_limit_in_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ingence.cli import demo
    from ingence.cli import main as cli
    from ingence.cli.demo import _stop_words, run_demo

    monkeypatch.setattr(cli, "app", lambda args, standalone_mode: 0)
    assert run_demo(tmp_path / "demo") == 0
    said = capsys.readouterr().out
    minutes = demo.DIVE_SECONDS // 60
    assert f"It stops after {minutes} minutes." in said
    assert "seconds" not in said
    assert _stop_words(60) == "1 minute" and _stop_words(90) == "90 seconds"


def test_the_demo_command_refuses_a_folder_that_holds_anything_and_writes_nothing(
    tmp_path: Path,
) -> None:
    from ingence.cli.demo import run_demo

    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "thesis.txt").write_text("kept")

    assert run_demo(tmp_path / "mine") == 2
    assert [path.name for path in (tmp_path / "mine").iterdir()] == ["thesis.txt"]


def test_the_demo_stops_at_the_first_step_that_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.cli import main as cli
    from ingence.cli.demo import run_demo

    ran: list[str] = []

    def fake_app(args: list[str], standalone_mode: bool) -> int:
        ran.append(args[0])
        return 1 if args[0] == "index" else 0

    monkeypatch.setattr(cli, "app", fake_app)

    assert run_demo(tmp_path / "demo") == 1
    assert ran == ["init", "index"]


# ---------------------------------------------------------------------------
# Replicate shares, symbols, macros and the demo.
# ---------------------------------------------------------------------------


def test_the_replicate_labels_follow_the_split_of_forty_twenty_twenty_ten_ten() -> None:
    from ingence.evaluation.sample_lab_people import REPLICATE_STYLES, REPLICATE_WEIGHTS

    assert dict(zip(REPLICATE_STYLES, REPLICATE_WEIGHTS, strict=True)) == {
        "rep": 40,
        "R": 20,
        "bare": 20,
        "take": 10,
        "counter": 10,
    }


def test_the_first_small_collection_sits_in_the_ranges_of_the_parameters(small) -> None:
    """Seed 1 of the small tier is the one the demo and the documentation show."""
    import statistics

    files = [path for path in small.files if "/.DS_Store" not in path and "Thumbs.db" not in path]
    names = [path.rsplit("/", 1)[-1] for path in files]
    date = re.compile(
        r"(?<!\d)(\d{8}|\d{4}_\d{2}_\d{2}|\d{2}-\d{2}-\d{4}|\d{2}-\d{2}-\d{2}"
        r"|\d{1,2}[A-Z][a-z]{2}\d{4}|[A-Z][a-z]{2}-\d{2}-\d{4})(?!\d)"
    )
    parts = [len([part for part in re.split(r"[_\-\s.]", name) if part]) for name in names]
    depth = [path.count("/") - 1 for path in files]  # below the person's folder
    dated = sum(1 for name in names if date.search(name)) / len(names)
    long_names = sum(1 for count in parts if count >= 10) / len(parts)
    kinds = small.manifest["counts"]["by_kind"]
    nd2 = kinds.get("nd2", 0) / small.manifest["counts"]["files"]

    assert 0.04 <= dated <= 0.12, dated
    assert 0.06 <= long_names <= 0.16, long_names
    assert 4 <= statistics.median(parts) <= 8
    assert statistics.median(depth) == 4
    assert 6 <= max(depth) <= 9
    assert 0.10 <= nd2 <= 0.50, nd2


def test_a_few_names_of_a_larger_tier_hold_a_temperature_or_a_length_with_its_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    monkeypatch.setitem(TIERS, "S", replace(TIERS["S"], symbols=3))

    lab = generate_sample_lab(tmp_path / "lab", tier="S", seed=1)

    odd = [path for path in _paths(lab) if re.search(r"[^\x00-\x7f]", path)]
    symbols = set(words().dictionary["symbols"])
    found = {token for path in odd for token in re.findall(r"\d+(?:°C|µm)", path)}
    assert odd
    assert found
    assert {token.casefold() for token in found} <= {symbol.casefold() for symbol in symbols}
    assert not unknown_words(odd)


def test_the_symbols_are_words_the_dictionary_reads_and_only_the_large_tier_plans_them() -> None:
    assert TIERS["L"].symbols >= 10
    assert "°C" in " ".join(words().dictionary["symbols"])
    assert "µm" in " ".join(words().dictionary["symbols"])


def test_a_fiji_macro_opens_processes_saves_and_closes_one_image_at_a_time(middle) -> None:
    derivations = middle.key.families["derivation"]
    assert isinstance(derivations, PairFamily)
    pairs = {tuple(pair) for pair in derivations.expected}
    macros = [
        path
        for path in middle.files
        if path.endswith(".ijm") and "Fiji.app/" not in path and "/backup/" not in path
    ]

    assert macros
    for macro in macros:
        text = (middle.pool / macro).read_text()
        folder = macro.rpartition("/")[0]
        opened = re.findall(r'open\("([^"]+)"\);', text)
        saved = re.findall(r'saveAs\("Tiff", "([^"]+)"\);', text)
        assert opened and len(opened) == len(saved)
        assert text.count("close();") == 2 * len(opened)
        # Each block saves what the key says was made from the file it opened, and nothing else.
        for source, target in zip(opened, saved, strict=True):
            first = posixpath.normpath(posixpath.join(folder, source))
            second = posixpath.normpath(posixpath.join(folder, target))
            if first in derivations.ignored:
                continue  # an archive link: the key accepts any answer about it
            assert (first, second) in pairs, (macro, source, target)


def test_the_demo_names_the_packs_the_words_need_and_prints_one_line_for_each_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ingence.cli import main as cli
    from ingence.cli.demo import run_demo

    steps: list[list[str]] = []

    def fake_app(args: list[str], standalone_mode: bool) -> int:
        steps.append(args)
        if args[0] != "see":
            print("  Next: ingence index -p somewhere")  # a command's own hint
        return 0

    monkeypatch.setattr(cli, "app", fake_app)

    assert run_demo(tmp_path / "demo") == 0

    init = steps[0]
    named = [init[index + 1] for index, word in enumerate(init) if word == "--domain"]
    assert named == list(words().pack_names())
    assert {"microscopy", "biochemistry"} <= set(named)
    printed = capsys.readouterr().out.splitlines()
    assert not any("Next:" in line for line in printed)
    assert [line for line in printed if line.startswith("Step ")]
    assert len(printed) <= 8


def test_the_demo_accepts_a_folder_that_holds_only_the_finder_s_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingence.cli import main as cli
    from ingence.cli.demo import run_demo

    monkeypatch.setattr(cli, "app", lambda args, standalone_mode: 0)
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / ".DS_Store").write_bytes(b"finder")

    assert run_demo(tmp_path / "folder") == 0

    assert (tmp_path / "folder" / ".DS_Store").read_bytes() == b"finder"
    assert (tmp_path / "folder" / "pool").is_dir()
