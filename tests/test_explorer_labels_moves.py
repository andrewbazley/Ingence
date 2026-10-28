"""A person's display label follows its file when the file moves.

The label also follows when the file is copied and the original removed. A label is an
``annotate`` event on ``label:<entry_id>``. A walk that finds a file at a new path confirms the
move by the file's own number or its birth time, and every decision about the file is read at
its new place (``record.log.at_new_places``). A copy keeping its original's name, size and time,
the original removed, is read as a move on every system, and the move says it was made by a
copy. Nothing is written to move a label: the Record on disk is unchanged, and the label says
where it was written.

Every name is invented.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.request
from typing import TYPE_CHECKING, Any

from ingence.storage.catalog import open_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from ingence.core.project import Project


def _project(base: Path) -> tuple[Project, Path]:
    from ingence.core.project import Project
    from ingence.scan.census import run_census

    pool = base / "pool"
    for folder in ("kelt", "vorn"):
        (pool / folder).mkdir(parents=True)
    for name in ("zib_trell_01.csv", "zib_trell_02.csv"):
        (pool / "kelt" / name).write_text(f"a\n{name}\n", encoding="utf-8")
    project = Project.create(base / "project", created_at="t", roots=[pool])
    run_census(project, run_id="run_20260101T000001Z_111111111111", started_at="t")
    return project, pool


def _ids(project: Project) -> dict[str, str]:

    with open_catalog(project.path) as catalog:
        return {
            str(path): str(entry)
            for path, entry in catalog.query(
                "SELECT relative_path, entry_id FROM entries WHERE entry_type = 'file'"
            ).fetchall()
        }


def _labels(project: Project, config: Path) -> list[dict[str, Any]]:
    from ingence.api.server import DashboardServer

    server = DashboardServer(project.path, port=0, config_home=config, warm_search=False)
    server.start()
    try:
        with urllib.request.urlopen(f"{server.url}api/v1/explorer/labels", timeout=30) as found:
            return list(json.loads(found.read())["labels"])
    finally:
        server.stop()


def test_a_label_follows_its_file_when_it_moves_or_is_copied_on_every_system(
    tmp_path: Path,
) -> None:
    from ingence.api.routes.labels import write_labels
    from ingence.record.log import read_events
    from ingence.scan.census import run_census

    project, pool = _project(tmp_path)
    before = _ids(project)
    moved_old = before["kelt/zib_trell_01.csv"]
    copied_old = before["kelt/zib_trell_02.csv"]
    write_labels(
        project.path,
        actor="a. person",
        occurred_at="2026-01-01T00:00:01Z",
        labels=[
            {"entry_id": moved_old, "label": "the first trell table"},
            {"entry_id": copied_old, "label": "the second trell table"},
        ],
    )
    written = [event.target for event in read_events(project.path, held=False, moved=False)]
    # A rename into another folder keeps the file's number and birth: a move.
    os.rename(pool / "kelt" / "zib_trell_01.csv", pool / "vorn" / "zib_trell_01.csv")
    # A copy under the same name, size and time, the original then removed: a move by a copy.
    shutil.copy2(pool / "kelt" / "zib_trell_02.csv", pool / "vorn" / "zib_trell_02.csv")
    (pool / "kelt" / "zib_trell_02.csv").unlink()
    run_census(project, run_id="run_20260101T000002Z_222222222222", started_at="t")
    after = _ids(project)
    moved_new = after["vorn/zib_trell_01.csv"]
    copy_new = after["vorn/zib_trell_02.csv"]
    assert moved_new != moved_old and copy_new != copied_old

    listed = {item["entry_id"]: item for item in _labels(project, tmp_path / "config")}
    assert listed[moved_new]["label"] == "the first trell table"
    assert listed[moved_new]["written_at"] == f"label:{moved_old}"
    # One answer on every system, whether or not the system records birth times: a copy keeping
    # its original's name, size and time, the original removed, is read as a move. Its label
    # follows it, and the move says it was made by a copy (another file number on one volume, or
    # a later birth).
    assert listed[copy_new]["label"] == "the second trell table"
    assert listed[copy_new]["written_at"] == f"label:{copied_old}"
    with open_catalog(project.path) as catalog:
        how = dict(
            catalog.query(
                "SELECT entry_id, moved_by FROM entries WHERE moved_from IS NOT NULL"
            ).fetchall()
        )
    assert how[copy_new] == "copied"
    assert how[moved_new] in ("same_file", "birth_time")
    # The Record on disk is what was written: nothing was appended to move a label.
    assert [event.target for event in read_events(project.path, held=False, moved=False)] == written
