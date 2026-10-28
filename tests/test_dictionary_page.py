"""The Dictionary page's routes.

List, filter, search and page the vocabulary; open one term; add a spelling or a new meaning;
inactivate and bring back; export a person's own dictionary; accept or refuse a shipped domain
pack; install and remove a laboratory's pack. Every write goes through the Record's one way in
(or, for a pack's files, the named workspace write) and refuses in words what it cannot do.
Every name here is invented.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
import yaml

from ingence.api.apply import DefinitionApplier
from ingence.api.routes.dictionary import (
    DictionaryRefusedError,
    add_spelling,
    export,
    inactivate,
    install_from_text,
    listing,
    packs,
    remove,
    term_detail,
)
from ingence.api.server import ANSWER_ROUTE, WRITE_ROUTES, DashboardServer
from ingence.core.project import Project
from ingence.core.schema import DictionaryOrigin, RecordEventKind
from ingence.dictionary.answers import pack_section_from_answers
from ingence.dictionary.store import DictionaryStore
from ingence.record.log import read_events
from ingence.record.questions import term_target
from ingence.scan.census import run_census
from ingence.storage.catalog import open_catalog

MOMENT = "2026-09-30T02:30:00Z"
#: A word the shipped Dictionary places nowhere, asserted below rather than assumed.
UNBOUND = "zorbexin"


@pytest.fixture
def project(tmp_path) -> Project:
    pool = tmp_path / "pool"
    (pool / "day_kelt").mkdir(parents=True)
    for index in (1, 2, 3):
        (pool / "day_kelt" / f"plate_{UNBOUND}_{index}.csv").write_text("a,b\n")
    (pool / "day_kelt" / "raw_brisa.csv").write_text("a,b\n")
    project = Project.create(tmp_path / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    return project


def _list(project: Project, **kwargs):
    with open_catalog(project.path) as catalog:
        return listing(catalog, project.path, **kwargs)


def _add(project: Project, **kwargs):
    arguments = {"actor": "kelt", "occurred_at": MOMENT, "spelling": UNBOUND, "term": "plate"}
    arguments.update(kwargs)
    with open_catalog(project.path) as catalog:
        return add_spelling(catalog, project.path, **arguments)


def test_the_page_lists_every_term_fifty_a_page_with_counts_and_origins(project) -> None:
    page = _list(project)
    assert len(page["rows"]) == 50
    assert page["terms"] == page["listed"] > 50
    assert page["counted"] is True
    # Ordered by how many names each binds, most first.
    assert page["rows"][0]["names"] >= page["rows"][1]["names"] >= 1
    plate = next(row for row in page["rows"] if row["term"] == "plate")
    assert plate["names"] == 3
    assert {"origin": "core", "source": "process"} in plate["origins"]
    assert any(item["spelling"] == "plate" for item in plate["surfaces"])
    second = _list(project, offset=50)
    assert second["offset"] == 50
    assert {row["term"] for row in second["rows"]}.isdisjoint(row["term"] for row in page["rows"])


def test_a_search_answers_by_a_spelling_or_a_term_and_filters_by_origin_and_kind(project) -> None:
    found = _list(project, text="plates")
    assert found["rows"][0]["term"] == "plate"
    domain = _list(project, origin="domain")
    assert domain["rows"] and all(
        any(item["origin"] == "domain" for item in row["origins"]) for row in domain["rows"]
    )
    units = _list(project, kind="unit")
    assert units["rows"] and all("unit" in row["kinds"] for row in units["rows"])
    assert units["listed"] == dict(_list(project)["kinds"])["unit"]
    assert _list(project, text="nothing-spells-this")["listed"] == 0


def test_a_spelling_added_to_a_sense_is_a_word_answer_and_binds_on_the_next_rebind(project) -> None:
    event = _add(project)
    assert event.kind is RecordEventKind.ASSIGN
    assert event.target == term_target(UNBOUND)
    payload = json.loads(event.payload)
    assert (payload["answer"], payload["theme_kind"], payload["raised"]) == (
        "plate",
        "vessel",
        UNBOUND,
    )
    hit = DictionaryStore.for_project(project.path).look_up(UNBOUND)
    assert hit is not None and hit.sense.term == "plate"
    assert hit.sense.origin is DictionaryOrigin.USER
    yours = _list(project, origin="yours")
    assert [row["term"] for row in yours["rows"]] == ["plate"]
    run_census(project, run_id="run_20260101T000002Z_222222222222", started_at="t", rebind=True)
    detail = _detail(project, "plate")
    assert detail["names"] == 3
    assert detail["history"][0]["what"] == f"added the spelling {UNBOUND}"


def _detail(project: Project, term: str):
    with open_catalog(project.path) as catalog:
        return term_detail(catalog, project.path, term)


def test_a_new_meaning_is_a_definition_with_its_kind(project) -> None:
    event = _add(project, term="zorbexin_assay", theme_kind="technique")
    assert event.kind is RecordEventKind.DEFINE
    detail = _detail(project, "zorbexin_assay")
    assert detail["kinds"] == ["technique"]
    assert detail["senses"][0]["origin"] == "yours"
    assert detail["history"][0]["what"] == f"defined {UNBOUND} as a new meaning"


def test_an_add_refuses_in_words_what_it_cannot_write(project) -> None:
    with pytest.raises(DictionaryRefusedError, match="spelling is needed"):
        _add(project, spelling="  ")
    with pytest.raises(DictionaryRefusedError, match="reads as 2"):
        _add(project, spelling="plate_kelt")
    with pytest.raises(DictionaryRefusedError, match="say what kind"):
        _add(project, term="brand_new_meaning")
    with pytest.raises(DictionaryRefusedError, match="one word"):
        _add(project, term="two words", theme_kind="technique")
    with pytest.raises(DictionaryRefusedError, match="kind vessel"):
        _add(project, theme_kind="unit")
    with pytest.raises(DictionaryRefusedError, match="already a spelling"):
        _add(project, spelling="plates")
    with pytest.raises(DictionaryRefusedError, match="names who"):
        _add(project, actor="")
    assert read_events(project.path) == ()
    with open_catalog(project.path) as catalog:
        inactivate(
            catalog, project.path, actor="kelt", occurred_at=MOMENT, term="plate", spelling="plates"
        )
    with pytest.raises(DictionaryRefusedError, match="plates as plate is inactive since"):
        _add(project, spelling="plates")


def test_an_inactive_spelling_is_listed_greyed_with_who_and_when(project) -> None:
    with open_catalog(project.path) as catalog:
        inactivate(
            catalog, project.path, actor="kelt", occurred_at=MOMENT, term="plate", spelling="plates"
        )
    detail = _detail(project, "plate")
    [plates] = [item for item in detail["surfaces"] if item["spelling"] == "plates"]
    assert plates["inactive"]["by"] == "kelt"
    assert plates["inactive"]["since"] == MOMENT
    assert detail["history"][0]["what"] == "inactivated the spelling plates"
    assert [row["term"] for row in _list(project, origin="inactive")["rows"]] == ["plate"]


def test_the_export_is_the_persons_pack_section_with_inactivations_listed(project) -> None:
    _add(project)
    with open_catalog(project.path) as catalog:
        inactivate(catalog, project.path, actor="kelt", occurred_at=MOMENT, term="raw")
    found = export(project.path)
    document = yaml.safe_load(found["text"])
    assert found["filename"].endswith(".yaml")
    assert [entry["term"] for entry in document["entries"]] == ["plate"]
    assert document["inactive"][0]["term"] == "raw"
    assert found["exported"] == 1 and found["inactive"] == 1
    assert "loading this file inactivates nothing" in found["text"]
    # The command-line export is unchanged: it lists no inactivations unless asked.
    assert "inactive" not in yaml.safe_load(
        pack_section_from_answers(project.path, section="x").text
    )


def test_an_exported_file_installs_as_a_laboratory_pack_and_is_removed(project) -> None:
    _add(project, term="zorbexin_assay", theme_kind="technique")
    found = export(project.path)
    installed = install_from_text(project.path, found["filename"], found["text"], actor="kelt")
    name = installed["installed"]
    assert Project.open(project.path).settings.dictionary.packs == (name,)
    listed = packs_of(project)
    assert [pack["name"] for pack in listed["laboratory"]] == [name]
    detail = _detail(project, "zorbexin_assay")
    assert {item["origin"] for item in detail["senses"]} == {"yours", "laboratory"}
    with pytest.raises(DictionaryRefusedError, match="lists no entries"):
        install_from_text(project.path, "notes.yaml", "just: words\n", actor="kelt")
    remove(project.path, name, actor="kelt")
    assert Project.open(project.path).settings.dictionary.packs == ()
    with pytest.raises(DictionaryRefusedError, match="no pack"):
        remove(project.path, name, actor="kelt")


def packs_of(project: Project):
    with open_catalog(project.path) as catalog:
        return packs(catalog, project.path)


def test_a_domain_accepted_through_the_answer_route_is_a_prior_and_loads_either_way(
    project,
) -> None:
    from ingence.api.routes.decide import Decision, DecisionContext, decide_target

    before = next(item for item in packs_of(project)["domain"] if item["domain"] == "microscopy")
    assert (before["loaded"], before["why"]) == (True, "loaded: every domain's words load")
    with open_catalog(project.path) as catalog:
        event = decide_target(
            DecisionContext(catalog, project.path),
            Decision(target=before["target"], word="accept"),
            actor="kelt",
            occurred_at=MOMENT,
        )
    assert event.kind is RecordEventKind.CONFIRM
    after = next(item for item in packs_of(project)["domain"] if item["domain"] == "microscopy")
    assert (after["loaded"], after["accepted"], after["answer"]["by"]) == (True, True, "kelt")
    assert after["why"].startswith("loaded, and a prior")
    assert any(
        any(origin["source"] == "microscopy" for origin in row["origins"])
        for row in _list(project, origin="domain", limit=500)["rows"]
    )


def test_the_packs_say_what_loads_and_why(project) -> None:
    listed = packs_of(project)
    assert listed["mode"] == "all"
    microscopy = next(item for item in listed["domain"] if item["domain"] == "microscopy")
    assert microscopy["loaded"] is True
    assert microscopy["target"] == "pack:microscopy"
    assert microscopy["senses"] > 0


# ------------------------------------------------------------------------ through the server


@pytest.fixture
def served(project, monkeypatch, tmp_path):
    # Who decides is kept in a folder of the test's own, never in the person's configuration.
    monkeypatch.setenv("INGENCE_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(DefinitionApplier, "default_executor", "thread")
    server = DashboardServer(project.path, port=0, warm_search=False)
    server.start()
    yield server, project
    server.stop()


def _post(server, route: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        server.url.rstrip("/") + route, method="POST", data=json.dumps(body).encode()
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Ingence-Write", "1")
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status), json.loads(response.read())
    except urllib.error.HTTPError as error:
        found = json.loads(error.read())
        error.close()
        return int(error.code), found


def _get(server, path: str) -> dict:
    with urllib.request.urlopen(server.url.rstrip("/") + path) as response:
        return json.loads(response.read())


def test_the_server_reads_and_writes_through_the_one_way_in(served) -> None:
    server, _ = served
    assert "/api/dictionary/pack" in WRITE_ROUTES
    page = _get(server, "/api/dictionary?q=plate")
    assert page["rows"][0]["term"] == "plate"
    assert _get(server, "/api/dictionary/term?term=plate")["term"] == "plate"
    status, written = _post(
        server,
        ANSWER_ROUTE,
        {"dictionary": "inactivate", "actor": "kelt", "term": "plate", "spelling": "plates"},
    )
    assert status == 201, written
    assert written["target"] == "surface:plate:plates"
    status, refused = _post(
        server,
        ANSWER_ROUTE,
        {"dictionary": "add", "actor": "kelt", "term": "plate", "spelling": ""},
    )
    assert status == 400 and "spelling is needed" in refused["error"]
    # A shipped domain pack is refused on the answer route's own proposal path.
    status, answered = _post(
        server, ANSWER_ROUTE, {"target": "pack:microscopy", "word": "refuse", "actor": "kelt"}
    )
    assert status == 201, answered
    assert (
        next(
            item
            for item in _get(server, "/api/dictionary/packs")["domain"]
            if item["domain"] == "microscopy"
        )["answer"]["word"]
        == "refuse"
    )
    text = yaml.safe_dump(
        {"section": "vorn-lab", "entries": [{"term": "vorn_stain", "surfaces": ["vornstain"]}]}
    )
    status, installed = _post(
        server,
        "/api/dictionary/pack",
        {"install": {"filename": "vorn.yaml", "text": text}, "actor": "kelt"},
    )
    assert status == 201, installed
    assert installed["installed"] == "vorn-lab"
    status, refused = _post(
        server, "/api/dictionary/pack", {"remove": "nothing-installed", "actor": "kelt"}
    )
    assert status == 400 and "no pack" in refused["error"]
    status, _ = _post(server, "/api/dictionary/pack", {"remove": "vorn-lab", "actor": "kelt"})
    assert status == 201
    export = _get(server, "/api/dictionary/export")
    assert "surface" not in export["text"] or "plates" in export["text"]
    assert yaml.safe_load(export["text"])["inactive"][0]["spelling"] == "plates"


def test_the_dictionary_section_joins_the_parameters_page_after_app_js() -> None:
    import re

    from ingence.api.server import WEB_ROOT

    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    script = (WEB_ROOT / "dictionary.js").read_text(encoding="utf-8")
    catalog = json.loads((WEB_ROOT / "help" / "catalog.json").read_text(encoding="utf-8"))
    order = re.findall(r'<script src="/([\w/.]+)"></script>', page)
    assert order.index("app.js") < order.index("dictionary.js")
    assert '<button data-view="dictionary"' in page
    assert "PAGES[page].push('dictionary')" in script
    for key in set(re.findall(r"'data-help': '([\w.]+)'", script)) | {"section.dictionary"}:
        assert key in catalog["entries"], key
    assert "window.ingenceDictionaryExport" in script
    assert "window.ingenceDictionaryImport" in script


@pytest.mark.parametrize(
    ("filename", "document", "words"),
    [
        # A number for the spellings must not break every read after install.
        (
            "zephbad.yaml",
            {"section": "zephbad", "entries": [{"term": "zeph", "surfaces": 5}]},
            "entry 1 \\(zeph\\): its spellings are a list",
        ),
        # Brackets forgotten: each letter became a spelling.
        (
            "zephletters.yaml",
            {"section": "zephletters", "entries": [{"term": "zeph", "surfaces": "kelt"}]},
            "entry 1 \\(zeph\\): its spellings are a list",
        ),
        (
            "zephkind.yaml",
            {
                "section": "zephkind",
                "entries": [{"term": "zeph", "surfaces": ["zephyl"], "theme_kind": 7}],
            },
            "entry 1 \\(zeph\\): its kind is a word",
        ),
        (
            "zephnoterm.yaml",
            {"section": "zephnoterm", "entries": [{"surfaces": ["zephyl"]}]},
            "entry 1 has no term",
        ),
        (
            "zephout.yaml",
            {"section": "../../../zephout", "entries": [{"term": "zeph"}]},
            "not a plain name",
        ),
        (
            "../../x/../evil.yaml",
            {"section": "zephevil", "entries": [{"term": "zeph"}]},
            "not a plain name",
        ),
        (
            ".hidden.yaml",
            {"section": "zephhidden", "entries": [{"term": "zeph"}]},
            "not a plain name",
        ),
        (
            "microscopy.yaml",
            {"section": "microscopy", "entries": [{"term": "zeph"}]},
            "ships with Ingence",
        ),
        # What only the loader refuses: the composition is made before anything is installed.
        (
            "zephlayout.yaml",
            {"section": "zephlayout", "entries": [{"term": "zeph"}], "layouts": 5},
            "cannot be read as a pack",
        ),
    ],
)
def test_a_pack_file_is_read_before_it_is_installed_and_refused_in_words(
    project, filename, document, words
) -> None:
    with pytest.raises(DictionaryRefusedError, match=words):
        install_from_text(project.path, filename, yaml.safe_dump(document), actor="kelt")
    assert not (project.path / "dictionary" / "packs").exists() or not any(
        (project.path / "dictionary" / "packs").iterdir()
    )
    assert Project.open(project.path).settings.dictionary.packs == ()
    DictionaryStore.for_project(project.path)  # every read still works


def test_a_later_answer_naming_an_inactive_meaning_or_spelling_is_refused_in_words(
    project,
) -> None:
    """An answer naming what a person inactivated is refused and nothing is written.

    Writing it would say it was recorded and do nothing. The answer route's writers refuse it, as
    the page's own Add does."""
    from ingence.api.routes.decide import (
        AnswerRefusedError,
        correct_word,
        define_word,
        prepare_answer,
    )

    with open_catalog(project.path) as catalog:
        inactivate(
            catalog,
            project.path,
            actor="kelt",
            occurred_at=MOMENT,
            term="plate",
            spelling="plates",
        )
        written = len(read_events(project.path))
        with pytest.raises(
            AnswerRefusedError, match="plates as plate is inactive since 2026-09-30"
        ):
            correct_word(
                catalog,
                project.path,
                actor="kelt",
                occurred_at=MOMENT,
                word="plates",
                answer="plate",
                theme_kind="vessel",
            )
        with pytest.raises(
            AnswerRefusedError, match="bring it back in the Dictionary section of Engine"
        ):
            prepare_answer(
                catalog,
                rule_id="any_rule",
                raised="Plates",
                kind=RecordEventKind.ASSIGN,
                answer="plate",
                theme_kind="vessel",
            )
        inactivate(catalog, project.path, actor="kelt", occurred_at=MOMENT, term="template")
        written += 1
        with pytest.raises(AnswerRefusedError, match="template is inactive since 2026-09-30"):
            define_word(
                catalog,
                project.path,
                actor="kelt",
                occurred_at=MOMENT,
                word=UNBOUND,
                answer="template",
                theme_kind="file_role",
            )
        with pytest.raises(AnswerRefusedError, match="template is inactive since"):
            correct_word(
                catalog,
                project.path,
                actor="kelt",
                occurred_at=MOMENT,
                word=UNBOUND,
                answer="template",
                theme_kind="file_role",
            )
    assert len(read_events(project.path)) == written


def test_a_spelling_another_answer_holds_is_replaced_only_when_the_person_says_so(project) -> None:
    """One answer per spelling stands on ``term:<spelling casefolded>``, the latest in force.

    Adding a spelling that a person's answer already gives another meaning, or writes in another
    case, would take it silently. The page says so first and writes only with ``replace``."""
    from ingence.api.routes.dictionary import answer_on

    _add(project, spelling="Zephyl", term="plate")
    held = answer_on(project.path, "zephyl")
    assert (held["spelling"], held["means"], held["by"]) == ("Zephyl", "plate", "kelt")
    before = len(read_events(project.path))
    with pytest.raises(
        DictionaryRefusedError,
        match=(
            r"Zephyl already means plate \(kelt's answer of 2026-09-30\); "
            r"adding it here would replace that answer"
        ),
    ):
        _add(project, spelling="zephyl", term="template")
    with pytest.raises(DictionaryRefusedError, match="Zephyl already means plate"):
        _add(project, spelling="zephyl", term="plate")
    assert len(read_events(project.path)) == before
    event = _add(project, spelling="zephyl", term="template", replace=True)
    assert event.target == term_target("zephyl")
    hit = DictionaryStore.for_project(project.path).look_up("zephyl")
    assert hit is not None and hit.sense.term == "template"
    assert answer_on(project.path, "nothing-answered") is None


def test_who_installed_or_removed_a_pack_and_when_is_kept_and_a_removed_pack_s_files_stay(
    project,
) -> None:
    """A pack is files, never a decision in the Record, so install and removal are logged.

    Each install and removal is a line in the workspace's pack log, naming who did it. A removed
    pack's files move to the project's removed packs, so removal does not delete the project's
    only copy."""
    from ingence.api.routes.dictionary import PACK_LOG, REMOVED_PACKS, pack_write

    text = yaml.safe_dump(
        {"section": "vorn-lab", "entries": [{"term": "vorn_stain", "surfaces": ["vornstain"]}]}
    )
    with pytest.raises(DictionaryRefusedError, match="names who"):
        pack_write(project.path, {"install": {"filename": "vorn.yaml", "text": text}})
    pack_write(project.path, {"install": {"filename": "vorn.yaml", "text": text}, "actor": "kelt"})
    pack_write(project.path, {"remove": "vorn-lab", "actor": "kelt"})
    lines = [
        json.loads(line)
        for line in (project.path / PACK_LOG).read_text(encoding="utf-8").splitlines()
    ]
    assert [(line["act"], line["pack"], line["actor"]) for line in lines] == [
        ("install", "vorn-lab", "kelt"),
        ("remove", "vorn-lab", "kelt"),
    ]
    assert all(line["at"] and line["digest"] for line in lines)
    [kept] = list((project.path / REMOVED_PACKS).iterdir())
    assert kept.name.startswith("vorn-lab-") and (kept / "pack.yaml").is_file()
    assert Project.open(project.path).settings.dictionary.packs == ()


def test_a_pack_file_larger_than_a_decision_installs_from_the_page(served) -> None:
    """A pack file up to its own ceiling installs, and a larger one is refused in plain words.

    The pack route must not apply the decision's 64 KB ceiling or say it in developer words."""
    from ingence.api.routes.dictionary import PACK_BODY_BYTES

    server, _ = served
    entries = [{"term": f"vorn_{index}", "surfaces": [f"vornword{index}"]} for index in range(3000)]
    text = yaml.safe_dump({"section": "vorn-big", "entries": entries})
    assert len(text) > 64 * 1024
    status, found = _post(
        server,
        "/api/dictionary/pack",
        {"install": {"filename": "vorn-big.yaml", "text": text}, "actor": "kelt"},
    )
    assert status == 201, found
    # Declared larger than the ceiling: refused on the declaration, before any body is read.
    import http.client
    from urllib.parse import urlparse

    address = urlparse(server.url)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=30)
    connection.putrequest("POST", "/api/dictionary/pack")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("X-Ingence-Write", "1")
    connection.putheader("Content-Length", str(PACK_BODY_BYTES + 1))
    connection.endheaders()
    response = connection.getresponse()
    status, refused = response.status, json.loads(response.read())
    connection.close()
    assert status == 400
    assert "installs here" in refused["error"] and "teach pack install" in refused["error"]


def test_the_page_says_accepted_and_refused_in_plain_words() -> None:
    """The page says "refused" for a refused pack, and uses plain words, not the engine's.

    The engine's words ("bindings", "section", "the settings name no choice") do not appear in
    the page."""
    from ingence.api.server import WEB_ROOT

    script = (WEB_ROOT / "dictionary.js").read_text(encoding="utf-8")
    catalog = json.loads((WEB_ROOT / "help" / "catalog.json").read_text(encoding="utf-8"))
    assert "word}ed by" not in script
    assert "refuse: 'refused'" in script
    assert "bindings" not in script.split("VIEWS.dictionary")[0].split("*/", 1)[1]
    assert "the settings name no choice" not in script
    for key in ("dictionary.import", "dictionary.export"):
        assert "section" not in catalog["entries"][key]["text"]


def test_a_spelling_written_as_a_number_is_told_to_be_text_and_a_layouts_section_installs(
    project,
) -> None:
    """A list holding a number is told to be text, and a section of program layouts alone installs.

    A list holding a number is not refused as "not a list in brackets". A section of program
    layouts alone, as the core's program_layouts.yaml is written, is not refused for listing no
    entries."""
    numbered = {"section": "zephnum", "entries": [{"term": "zeph_laser", "surfaces": [405]}]}
    with pytest.raises(DictionaryRefusedError, match='spellings are text: write 405 as "405"'):
        install_from_text(project.path, "zephnum.yaml", yaml.safe_dump(numbered), actor="kelt")
    layouts = {
        "section": "zephsuite",
        "theme_kind": "software",
        "entries": [],
        "layouts": [{"program": "Zephsuite", "holds": ["zeph.jar", "zephplugins/"]}],
    }
    installed = install_from_text(
        project.path, "zephsuite.yaml", yaml.safe_dump(layouts), actor="kelt"
    )
    assert installed["installed"] == "zephsuite"
    with pytest.raises(DictionaryRefusedError, match="lists no entries"):
        install_from_text(
            project.path, "zephempty.yaml", yaml.safe_dump({"section": "zephempty"}), actor="kelt"
        )


def test_two_removals_in_one_second_keep_two_folders_side_by_side(project, monkeypatch) -> None:
    """A removed pack's folder is named to the second, and a second removal in that second works.

    The second removal must not move its files inside the first's folder."""
    from ingence.api.routes.dictionary import REMOVED_PACKS, remove
    from ingence.record import log

    monkeypatch.setattr(log, "now", lambda: MOMENT)

    text = yaml.safe_dump(
        {"section": "vorn-lab", "entries": [{"term": "vorn_stain", "surfaces": ["vornstain"]}]}
    )
    for _ in range(2):
        install_from_text(project.path, "vorn.yaml", text, actor="kelt")
        remove(project.path, "vorn-lab", actor="kelt")
    kept = sorted((project.path / REMOVED_PACKS).iterdir())
    assert len(kept) == 2
    assert all((folder / "pack.yaml").is_file() for folder in kept)
