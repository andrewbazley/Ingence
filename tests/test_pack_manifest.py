"""A pack's manifest, and installing a pack from a file or an address.

- every shipped pack carries a manifest whose digest matches its words;
- a laboratory's pack exported by ``teach export-answers`` carries one;
- a file installs as a directory does, its manifest checked. An address is fetched only when the
  command is run, with a timeout and a size ceiling. A digest or schema mismatch is refused in
  words and nothing is installed;
- a pack exported from a project with an inactivated word installs with every word active. An
  inactivation does not travel, because a new download of a dictionary means every word is active.

The address tests serve a folder over HTTP on this machine (ports 9004 to 9007 first). Nothing
reaches the internet. Every name here is invented.
"""

from __future__ import annotations

import functools
import http.server
import socket
import threading
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from ingence import SCHEMA_VERSION
from ingence.cli.main import app
from ingence.core.ids import PROJECT_SCOPE
from ingence.core.project import Project
from ingence.core.schema import RecordEventKind
from ingence.dictionary.answers import pack_section_from_answers
from ingence.dictionary.laboratory_packs import (
    PackError,
    fetch_pack,
    install_pack_from,
    installed_packs,
)
from ingence.dictionary.pack_manifest import (
    MANIFEST_KEY,
    Manifest,
    read_manifest,
    schema_problem,
    with_manifest,
    words_digest,
)
from ingence.dictionary.store import PACKS_DIRECTORY, DictionaryStore
from ingence.record.log import append_event
from ingence.record.questions import term_target
from ingence.record.targets import sense_target
from ingence.scan.census import run_census

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

RUN = "run_20260101T000001Z_000000000001"


def _section(term: str = "zorbexin_assay", spellings: tuple[str, ...] = ("zorbexin",)) -> dict:
    return {
        "section": "vorn_words",
        "summary": "Words an invented laboratory uses.",
        "theme_kind": "technique",
        "entries": [{"term": term, "surfaces": list(spellings)}],
    }


def _pack_file(base: Path, document: dict, name: str = "vorn-words.yaml") -> Path:
    path = base / name
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _manifested(**changes: str) -> dict:
    manifest = Manifest(
        name="vorn-words", version="1", author="ines", licence="CC0-1.0", domain="kelt"
    )
    return with_manifest(_section(), replace(manifest, **changes))


@pytest.fixture
def project(tmp_path: Path) -> Project:
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "plate_zorbexin_01.csv").write_text("a,b\n1,2\n")
    created = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(created, run_id=RUN, started_at="t")
    return created


# ------------------------------------------------------------------------------ the manifest


@pytest.mark.parametrize(
    "directory", sorted(path for path in PACKS_DIRECTORY.iterdir() if path.is_dir())
)
def test_every_shipped_pack_carries_a_manifest_whose_digest_matches_its_words(directory) -> None:
    (section,) = sorted(directory.glob("*.yaml"))
    document = yaml.safe_load(section.read_text(encoding="utf-8"))
    manifest = read_manifest(document.get(MANIFEST_KEY))
    assert manifest is not None, f"{section.name} carries no manifest"
    assert (manifest.name, manifest.domain) == (directory.name, document["domain"])
    assert manifest.version and manifest.author and manifest.licence
    assert schema_problem(manifest.schema) is None
    assert manifest.digest == words_digest([document]), (
        f"{section.name}'s words changed: give it a new version and write its digest again"
    )


def test_a_manifest_block_changes_nothing_a_run_reads(tmp_path: Path) -> None:
    """The block sits beside the words: the composed vocabulary's fingerprint is the one it has
    without it."""
    from ingence.dictionary.store import load_sections

    for directory in sorted(path for path in PACKS_DIRECTORY.iterdir() if path.is_dir()):
        (section,) = sorted(directory.glob("*.yaml"))
        document = yaml.safe_load(section.read_text(encoding="utf-8"))
        bare = tmp_path / directory.name
        bare.mkdir()
        (bare / section.name).write_text(
            yaml.safe_dump({key: value for key, value in document.items() if key != MANIFEST_KEY}),
            encoding="utf-8",
        )
        assert (
            DictionaryStore(load_sections(directory)).fingerprint()
            == DictionaryStore(load_sections(bare)).fingerprint()
        ), directory.name


def test_a_schema_written_as_a_number_is_refused_and_asked_in_quotes(
    project: Project, tmp_path: Path
) -> None:
    """``schema: 1.30`` reads as the number 1.3, which would pass as older than this engine's."""
    document = _manifested()
    text = yaml.safe_dump(document, sort_keys=False).replace(
        f"schema: '{SCHEMA_VERSION}'", "schema: 1.30"
    )
    assert "schema: 1.30" in text
    path = tmp_path / "vorn-words.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PackError, match="in quotes"):
        install_pack_from(project.path, str(path))


def test_the_digest_is_over_the_words_and_not_the_bytes() -> None:
    document = _section()
    assert words_digest([document]) == words_digest([with_manifest(document, _bare())])
    changed = _section(spellings=("zorbexin", "zorbexins"))
    assert words_digest([changed]) != words_digest([document])


def _bare() -> Manifest:
    return Manifest(name="vorn-words", version="1")


def test_a_schema_newer_than_this_engine_s_is_refused_in_words() -> None:
    major, minor = SCHEMA_VERSION.split(".")
    assert schema_problem(SCHEMA_VERSION) is None
    assert schema_problem(f"{major}.0") is None
    newer = schema_problem(f"{major}.{int(minor) + 1}")
    assert newer is not None and "update Ingence" in newer
    assert schema_problem(f"{int(major) + 1}.0") is not None
    assert schema_problem("soon") is not None


def test_an_exported_pack_carries_a_manifest(project: Project) -> None:
    _define(project, "zorbexin", actor="ines")
    exported = pack_section_from_answers(project.path, section="vorn-words")
    document = yaml.safe_load(exported.text)
    manifest = read_manifest(document[MANIFEST_KEY])
    assert manifest is not None
    assert manifest.name == "vorn-words"
    assert manifest.author == "ines"
    assert manifest.schema == SCHEMA_VERSION
    assert manifest.digest == words_digest([document])
    assert next(iter(document)) == MANIFEST_KEY  # read first


def _define(project: Project, spelling: str, *, actor: str) -> None:
    append_event(
        project.path,
        kind=RecordEventKind.DEFINE,
        actor=actor,
        occurred_at="2026-09-30T02:30:00Z",
        target=term_target(spelling),
        against_run_id=RUN,
        payload={"raised": spelling, "answer": f"{spelling}_assay", "theme_kind": "technique"},
        scope=PROJECT_SCOPE,
    )


# ------------------------------------------------------------------------------ from a file


def test_a_file_installs_as_a_directory_does_its_manifest_checked(
    project: Project, tmp_path: Path
) -> None:
    path = _pack_file(tmp_path, _manifested())
    pack = install_pack_from(project.path, str(path))
    assert pack.name == "vorn-words"
    assert [item.name for item in installed_packs(project.path)] == ["vorn-words"]
    hit = DictionaryStore.for_project(project.path).look_up("zorbexin")
    assert hit is not None and hit.sense.term == "zorbexin_assay"


def test_a_file_whose_words_do_not_match_its_digest_is_refused_and_nothing_installed(
    project: Project, tmp_path: Path
) -> None:
    document = _manifested()
    document["entries"][0]["surfaces"].append("zorbexins")  # changed after it was written
    path = _pack_file(tmp_path, document)
    with pytest.raises(PackError, match="do not match its digest"):
        install_pack_from(project.path, str(path))
    assert installed_packs(project.path) == ()
    assert Project.open(project.path).settings.dictionary.packs == ()


def test_a_file_needing_a_newer_schema_is_refused_and_nothing_installed(
    project: Project, tmp_path: Path
) -> None:
    major, minor = SCHEMA_VERSION.split(".")
    path = _pack_file(tmp_path, _manifested(schema=f"{major}.{int(minor) + 1}"))
    with pytest.raises(PackError, match="update Ingence"):
        install_pack_from(project.path, str(path))
    assert installed_packs(project.path) == ()


def test_a_section_file_without_a_manifest_installs_as_the_page_installs_it(
    project: Project, tmp_path: Path
) -> None:
    path = _pack_file(tmp_path, _section())
    pack = install_pack_from(project.path, str(path))
    assert pack.name == "vorn_words"


def test_a_pack_exported_with_an_inactivated_word_installs_with_every_word_active(
    project: Project, tmp_path: Path
) -> None:
    """Inactivations never travel: a new download of a dictionary means every word is active."""
    _define(project, "zorbexin", actor="ines")
    _define(project, "vornex", actor="tomas")
    append_event(
        project.path,
        kind=RecordEventKind.REJECT,
        actor="ines",
        occurred_at="2026-09-30T02:31:00Z",
        target=sense_target("vornex_assay"),
        against_run_id=RUN,
        payload={"spellings": ["vornex"]},
        scope=PROJECT_SCOPE,
    )
    assert DictionaryStore.for_project(project.path).look_up("vornex") is None
    exported = pack_section_from_answers(project.path, section="vorn-words", inactive=True)
    path = tmp_path / "vorn-words.yaml"
    path.write_text(exported.text, encoding="utf-8")

    pool = tmp_path / "other_pool"
    pool.mkdir()
    other = Project.create(tmp_path / "other", created_at="t", roots=[pool])
    install_pack_from(other.path, str(path))
    store = DictionaryStore.for_project(other.path)
    for spelling, term in (("zorbexin", "zorbexin_assay"), ("vornex", "vornex_assay")):
        hit = store.look_up(spelling)
        assert hit is not None and hit.sense.term == term


# ------------------------------------------------------------------------------ from an address


def _port() -> int:
    for wanted in (9004, 9005, 9006, 9007, 0):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", wanted))
            except OSError:
                continue
            return int(probe.getsockname()[1])
    message = "no port is free"
    raise RuntimeError(message)


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[Path, str, list[str]]]:
    """A folder served over HTTP on this machine, and the paths asked of it."""
    folder = tmp_path / "served"
    folder.mkdir()
    asked: list[str] = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            asked.append(self.path)

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", _port()), functools.partial(Handler, directory=str(folder))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield folder, f"http://127.0.0.1:{server.server_address[1]}", asked
    finally:
        server.shutdown()
        server.server_close()


def test_an_address_is_fetched_checked_and_installed(project: Project, served) -> None:
    folder, base, asked = served
    _pack_file(folder, _manifested())
    pack = install_pack_from(project.path, f"{base}/vorn-words.yaml")
    assert pack.name == "vorn-words"
    assert asked == ["/vorn-words.yaml"]


def test_an_address_whose_words_do_not_match_is_refused_and_nothing_installed(
    project: Project, served
) -> None:
    folder, base, _ = served
    document = _manifested()
    document["entries"][0]["term"] = "zorbexin_other"
    _pack_file(folder, document)
    with pytest.raises(PackError, match="do not match its digest"):
        install_pack_from(project.path, f"{base}/vorn-words.yaml")
    assert installed_packs(project.path) == ()


def test_an_address_serving_a_pack_without_a_manifest_is_refused(project: Project, served) -> None:
    folder, base, _ = served
    _pack_file(folder, _section())
    with pytest.raises(PackError, match="cannot be checked"):
        install_pack_from(project.path, f"{base}/vorn-words.yaml")
    assert installed_packs(project.path) == ()


def test_the_whole_fetch_has_a_deadline(served, monkeypatch) -> None:
    """A server sending a byte at a time inside each socket wait is still stopped at the timeout."""
    import time

    folder, base, _ = served
    _pack_file(folder, _manifested())
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(time, "monotonic", lambda: float(next(clock)))
    with pytest.raises(PackError, match="did not finish within"):
        fetch_pack(f"{base}/vorn-words.yaml", timeout=5.0, largest=1 << 20, chunk=8)


def test_a_redirect_is_said_with_the_address_it_ended_at(project: Project, served) -> None:
    folder, base, _ = served
    _pack_file(folder, _manifested())
    (folder / "moved").mkdir()
    _pack_file(folder / "moved", _manifested(), name="index.html")
    result = CliRunner().invoke(
        app, ["teach", "pack", "install", f"{base}/moved", "-p", str(project.path)]
    )
    said = " ".join(result.output.split())
    assert f"fetched from {base}/moved/" in said


def test_a_download_larger_than_the_ceiling_is_refused(served) -> None:
    folder, base, _ = served
    _pack_file(folder, _manifested())
    with pytest.raises(PackError, match="larger than"):
        fetch_pack(f"{base}/vorn-words.yaml", timeout=5.0, largest=64)


def test_an_address_that_answers_nothing_is_refused_in_words(served) -> None:
    _, base, _ = served
    with pytest.raises(PackError, match="could not be fetched"):
        fetch_pack(f"{base}/absent.yaml", timeout=5.0, largest=1 << 20)


def test_only_a_web_address_is_fetched(project: Project) -> None:
    with pytest.raises(PackError, match="neither a file, a folder nor"):
        install_pack_from(project.path, "ftp://127.0.0.1/vorn-words.yaml")


def test_the_command_says_what_it_fetches_and_from_where(project: Project, served) -> None:
    folder, base, _ = served
    _pack_file(folder, _manifested())
    result = CliRunner().invoke(
        app, ["teach", "pack", "install", f"{base}/vorn-words.yaml", "-p", str(project.path)]
    )
    assert result.exit_code == 0, result.output
    said = " ".join(result.output.split())
    assert f"Fetching vorn-words.yaml from {base}/vorn-words.yaml" in said
    assert "Installed vorn-words 1" in said


def test_the_command_refuses_a_mismatch_in_words(project: Project, served) -> None:
    folder, base, _ = served
    document = _manifested()
    document["entries"][0]["surfaces"] = ["zorbexin", "vornex"]
    _pack_file(folder, document)
    result = CliRunner().invoke(
        app, ["teach", "pack", "install", f"{base}/vorn-words.yaml", "-p", str(project.path)]
    )
    assert result.exit_code == 1
    assert "do not match its digest" in " ".join(result.output.split())
    assert installed_packs(project.path) == ()


def test_a_pack_named_pack_keeps_its_words_beside_its_manifest(
    project: Project, tmp_path: Path
) -> None:
    """Its section file would otherwise be written over the manifest file it is wrapped with."""
    document = {**_section(), "section": "pack"}
    path = _pack_file(tmp_path, document, name="pack.yaml")
    pack = install_pack_from(project.path, str(path))
    assert pack.name == "pack"
    hit = DictionaryStore.for_project(project.path).look_up("zorbexin")
    assert hit is not None and hit.sense.term == "zorbexin_assay"
