"""``GET /api/v1/themes/map``: the experiments placed in the plane.

The route answers its declared model from the run reads are pinned to. Every experiment of the
themes route appears once, with a place or with no place and a reason (never zero). Its theme and
key are the themes route's, and its standing is the completeness route's fields unchanged. It is
cached beside the themes projection, and a rebuild gives the same text to the digit. It says in the
one error shape when there is no run or no themes component. Every word and person is invented
(``fixtures/theme_pool.py``).
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

pytest.importorskip("sklearn")

from fixtures.theme_pool import OUTLIERS, build_theme_project
from ingence.api import v1
from ingence.api.v1.models import RESPONSE_MODELS, ErrorResponse
from ingence.core.project import Project
from ingence.themes import THEME_ENGINES, mapxy
from ingence.themes import projection as themes_projection_module


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    project, plant = build_theme_project(tmp_path_factory.mktemp("themes_map_api"))
    return v1.context_for(project, settled=None, job_running=False), plant, project


def _get(context, path, **query):
    return v1.dispatch(context, path, {k: [str(v)] for k, v in query.items()})


def test_the_route_answers_its_registered_model(served) -> None:
    context, _, _ = served
    status, payload = _get(context, "themes/map")
    assert status == 200, payload
    v1.DECLARED["themes/map"].model_validate(payload)
    assert v1.DECLARED["themes/map"] in set(RESPONSE_MODELS.values())
    assert payload["run_id"] == context.run_pinned
    assert payload["method"] in ("tsne", "pca")
    assert "approximate" in payload["method_said"]


def test_every_experiment_of_the_themes_route_appears_once(served) -> None:
    context, plant, _ = served
    _, themes = _get(context, "themes")
    _, mapped = _get(context, "themes/map")
    ids = [point["id"] for point in mapped["experiments"]]
    assert ids == [row["id"] for row in themes["experiments"]]
    assert len(set(ids)) == len(ids) == len(plant.documents)
    assert mapped["count"] == len(ids)
    assert mapped["placed"] + mapped["unplaced"] == mapped["count"]


def test_a_place_is_in_the_unit_square_and_no_place_says_why(served) -> None:
    context, plant, _ = served
    _, mapped = _get(context, "themes/map")
    by_folder = {point["folder"]: point for point in mapped["experiments"]}
    for point in mapped["experiments"]:
        if point["x"] is None:
            assert point["y"] is None
            assert point["no_place_reason"]
            assert point["at_this_place"] == 0
        else:
            assert 0.0 <= point["x"] <= 1.0 and 0.0 <= point["y"] <= 1.0
            assert point["no_place_reason"] is None
            assert point["at_this_place"] >= 1
    for unit in OUTLIERS:
        point = by_folder[plant.folders[unit]]
        assert point["x"] is None and point["y"] is None
        assert point["no_place_reason"] == "none of its words are among the words the themes read"
    # Experiments at one place are counted together, itself included.
    at = Counter((p["x"], p["y"]) for p in mapped["experiments"] if p["x"] is not None)
    for point in mapped["experiments"]:
        if point["x"] is not None:
            assert point["at_this_place"] == at[(point["x"], point["y"])]
    assert mapped["unplaced"] >= len(OUTLIERS)


def test_a_point_carries_the_themes_route_s_theme_label_and_key(served) -> None:
    context, _, _ = served
    _, themes = _get(context, "themes")
    _, mapped = _get(context, "themes/map")
    target_of = {t["theme"]: t["target"] for t in themes["themes"]}
    rows = {row["id"]: row for row in themes["experiments"]}
    for point in mapped["experiments"]:
        row = rows[point["id"]]
        assert point["theme"] == row["theme"]
        assert point["theme_label"] == row["theme_label"]
        assert point["broad_theme"] == row["broad_theme"]
        assert point["no_theme_reason"] == row["unplaced_reason"]
        assert point["theme_key"] == (None if row["theme"] is None else target_of[row["theme"]])
        assert point["label"] == row["folder"].rsplit("/", 1)[-1]
    assert any(point["theme"] is None and point["x"] is not None for point in mapped["experiments"])
    assert [t["theme"] for t in mapped["themes"]] == [t["theme"] for t in themes["themes"]]
    for theme in mapped["themes"]:
        assert theme["target"] == target_of[theme["theme"]]
        assert theme["placed"] <= theme["experiments"]
        if theme["placed"]:
            assert 0.0 <= theme["x"] <= 1.0 and 0.0 <= theme["y"] <= 1.0


def test_the_standing_is_the_completeness_route_s_unchanged(served) -> None:
    context, _, _ = served
    status, completeness = _get(context, "completeness")
    assert status == 200, completeness
    _, mapped = _get(context, "themes/map")
    assert mapped["standing_computed"] is True and mapped["standing_said"] is None
    found = {row["id"]: row for row in completeness["experiments"]}
    assert found
    for point in mapped["experiments"]:
        row = found.get(point["id"])
        if row is None:
            assert point["standing"] is None
            continue
        standing = point["standing"]
        for name in (
            "raw_acquisitions",
            "analysis_files",
            "result_tables",
            "has_analysis",
            "share_of_raw_followed_by_analysis",
            "said",
        ):
            assert standing[name] == row[name], name
        assert standing["raw_followed"] == row["raw_followed"]


def test_a_standing_that_could_not_be_counted_says_so_plainly_and_shows_no_cause(
    served, monkeypatch
) -> None:
    import ingence.completeness as completeness_module
    from ingence.api.v1 import themes as themes_routes

    context, _, _ = served

    def failing(*_arguments, **_keywords):
        message = "cannot read /private/secret/place/table.arrow"
        raise RuntimeError(message)

    monkeypatch.setattr(completeness_module, "completeness_of_run", failing)
    themes_routes._STANDING_HELD.clear()
    status, mapped = _get(context, "themes/map")
    assert status == 200
    assert mapped["standing_computed"] is False
    assert mapped["standing_said"] == themes_routes.STANDING_NOT_COUNTED
    assert "secret" not in json.dumps(mapped) and "RuntimeError" not in json.dumps(mapped)
    assert all(point["standing"] is None for point in mapped["experiments"])
    themes_routes._STANDING_HELD.clear()


def test_two_first_reads_of_a_run_count_its_standing_once(served, monkeypatch) -> None:
    import threading
    import time

    import ingence.completeness as completeness_module
    from ingence.api.v1 import themes as themes_routes

    context, _, project = served
    calls: list[str] = []
    original = completeness_module.completeness_of_run

    def slow(project_path, run_id, **keywords):
        calls.append(run_id)
        time.sleep(0.3)
        return original(project_path, run_id, **keywords)

    monkeypatch.setattr(completeness_module, "completeness_of_run", slow)
    themes_routes._STANDING_HELD.clear()
    answers: list[object] = []
    threads = [
        threading.Thread(
            target=lambda: answers.append(themes_routes._standing(project, context.run_pinned))
        )
        for _ in range(4)
    ]
    for each in threads:
        each.start()
    for each in threads:
        each.join()
    assert calls == [context.run_pinned]
    assert len(answers) == 4 and all(found[1] is None for found in answers)
    themes_routes._STANDING_HELD.clear()


def test_the_standing_is_counted_once_for_a_run_and_not_on_every_read(served, monkeypatch) -> None:
    import ingence.completeness as completeness_module
    from ingence.api.v1 import themes as themes_routes

    context, _, _ = served
    calls: list[str] = []
    original = completeness_module.completeness_of_run

    def counting(project, run_id, **kwargs):
        calls.append(run_id)
        return original(project, run_id, **kwargs)

    monkeypatch.setattr(completeness_module, "completeness_of_run", counting)
    themes_routes._STANDING_HELD.clear()
    _, first = _get(context, "themes/map")
    _, second = _get(context, "themes/map")
    assert calls == [context.run_pinned]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


@pytest.fixture
def fresh(served):
    """The map as nobody has read it: nothing in memory and no map file, before and after."""
    context, _, project = served
    folder = project / "projections" / "themes" / context.run_pinned

    def clear() -> None:
        themes_projection_module._MEMORY.clear()
        for path in folder.glob("map-*.json"):
            path.unlink()

    clear()
    yield context, folder
    clear()


def _stored(folder):
    cached = sorted(folder.glob("map-*.json"))
    assert len(cached) == 1
    return cached[0], json.loads(cached[0].read_text(encoding="utf-8"))


def test_it_is_cached_beside_the_projection_and_a_rebuild_is_identical(fresh) -> None:
    context, folder = fresh
    _, first = _get(context, "themes/map")
    path, stored = _stored(folder)
    assert stored["places"] and stored["run_id"] == context.run_pinned
    assert stored["made_with"]["method"] == first["method"]
    # Forget it in memory and on disk: the next read builds it again, to the digit.
    path.unlink()
    themes_projection_module._MEMORY.clear()
    _, second = _get(context, "themes/map")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    _stored(folder)


def test_a_cached_file_is_read_and_not_built(fresh) -> None:
    context, folder = fresh
    _get(context, "themes/map")
    path, stored = _stored(folder)
    themes_projection_module._MEMORY.clear()
    stored["seed"] = 99
    path.write_text(json.dumps(stored, sort_keys=True), encoding="utf-8")
    _, served_again = _get(context, "themes/map")
    assert served_again["seed"] == 99


@pytest.mark.parametrize(
    ("record", "name", "other"),
    [
        ("numpy", None, "0.0"),
        ("scipy", None, "0.0"),
        ("scikit_learn", None, "0.0"),
        ("map_version", None, "0"),
        ("perplexity", "PERPLEXITY", 20.0),
        ("iterations", "ITERATIONS", 500),
        ("tsne_limit", "TSNE_LIMIT", 10),
        ("decimals", "DECIMALS", 4),
        ("minimum_points", "MINIMUM_POINTS", 4),
        ("minimum_tsne", "MINIMUM_TSNE", 9),
    ],
)
def test_a_map_drawn_under_another_setting_or_library_is_drawn_again(
    fresh, monkeypatch, record, name, other
) -> None:
    """A file whose record of what drew it differs in any one entry is refused: the setting
    changed in this process (``name``), or the file was written under another library (``None``:
    its record is edited here, as if a user had upgraded)."""
    context, folder = fresh
    _get(context, "themes/map")
    path, stored = _stored(folder)
    themes_projection_module._MEMORY.clear()
    stored["seed"] = 99
    if name is None:
        stored["made_with"][record] = other
    else:
        monkeypatch.setattr(mapxy, name, other)
    path.write_text(json.dumps(stored, sort_keys=True), encoding="utf-8")
    _, again = _get(context, "themes/map")
    assert again["seed"] != 99, "the changed file was served"
    _, rewritten = _stored(folder)
    assert rewritten["made_with"][record] != (
        other if name is None else stored["made_with"][record]
    )
    assert rewritten["made_with"] == mapxy.made_with()


def test_the_map_reads_the_run_it_is_asked_for(served) -> None:
    context, _, _ = served
    status, payload = _get(context, "themes/map", run="run_20250101T000000Z_zzzzzzzzzzzz")
    assert status == 404
    assert ErrorResponse.model_validate(payload).error.code == "unknown_run"


def test_no_run_and_no_component_are_refused_in_the_one_shape(served, tmp_path) -> None:
    context, _, _ = served
    empty = Project.create(tmp_path / "empty", created_at="t", roots=[tmp_path])
    status, payload = _get(
        v1.context_for(empty.path, settled=None, job_running=False), "themes/map"
    )
    assert status == 404
    ErrorResponse.model_validate(payload)
    THEME_ENGINES.set_enabled("lsa_louvain", enabled=False)
    try:
        status, payload = _get(context, "themes/map")
    finally:
        THEME_ENGINES.set_enabled("lsa_louvain", enabled=True)
    assert status == 503
    assert ErrorResponse.model_validate(payload).error.code == "component_unavailable"


# ---------------------------------------------------------------------------
# The themes first (``themes_only=true``), the layout after.


@pytest.fixture
def no_layout(monkeypatch):
    """A hook on the layout: it counts each time the plane is drawn (and still draws it)."""
    drawn: list[int] = []
    original = mapxy.plane_of

    def counting(vectors, **keywords):
        drawn.append(int(vectors.shape[0]))
        return original(vectors, **keywords)

    monkeypatch.setattr(mapxy, "plane_of", counting)
    return drawn


def test_the_themes_alone_answer_without_drawing_the_layout(fresh, no_layout) -> None:
    context, folder = fresh
    status, only = _get(context, "themes/map", themes_only="true")
    assert status == 200, only
    v1.DECLARED["themes/map"].model_validate(only)
    # The hook: nothing was drawn, nothing was cached, nothing is held in memory.
    assert no_layout == []
    assert list(folder.glob("map-*.json")) == []
    assert not any("::map::" in key for key in themes_projection_module._MEMORY)
    assert only["themes_only"] is True and only["layout_ready"] is False
    assert only["experiments"] == []
    # Said, not drawn at zero: every count of places and every center is null.
    assert only["method"] is None and only["distinct_documents"] is None
    assert only["placed"] is None and only["unplaced"] is None
    assert only["standing_computed"] is False and only["standing_said"]
    assert only["method_said"] == "".join(
        [
            "The places are not drawn yet: ",
            "the first full read of the map draws them once and keeps them.",
        ]
    )
    for theme in only["themes"]:
        assert theme["x"] is None and theme["y"] is None and theme["placed"] is None
        assert theme["label"] and theme["target"] and 0.0 <= theme["stability"] <= 1.0
    # The experiments were counted from the themes projection, and the full read, which does draw
    # the layout, finds the same number.
    _, whole = _get(context, "themes/map")
    assert len(no_layout) == 1
    assert only["count"] == whole["count"] == len(whole["experiments"])


def test_the_themes_alone_are_the_full_answers_themes_without_the_places(fresh) -> None:
    context, _ = fresh
    _, only = _get(context, "themes/map", themes_only="true")
    _, whole = _get(context, "themes/map")
    assert whole["themes_only"] is False and whole["layout_ready"] is True
    assert whole["experiments"] and whole["placed"] is not None
    assert [t["theme"] for t in only["themes"]] == [t["theme"] for t in whole["themes"]]
    for first, second in zip(only["themes"], whole["themes"], strict=True):
        for name in ("label", "person_label", "target", "experiments", "stability"):
            assert first[name] == second[name], name
    assert only["seed"] == whole["seed"] and only["run_id"] == whole["run_id"]


def test_once_the_layout_is_held_the_themes_alone_carry_its_centers(fresh, no_layout) -> None:
    context, folder = fresh
    _, whole = _get(context, "themes/map")
    assert len(no_layout) == 1
    # Held in memory, then held only in its file: either way nothing is drawn again.
    for forget in (False, True):
        if forget:
            themes_projection_module._MEMORY.clear()
        _, only = _get(context, "themes/map", themes_only="true")
        assert len(no_layout) == 1
        assert only["themes_only"] is True and only["layout_ready"] is True
        assert only["experiments"] == []
        assert only["method"] == whole["method"] and only["method_said"] == whole["method_said"]
        assert only["placed"] == whole["placed"] and only["unplaced"] == whole["unplaced"]
        assert only["distinct_documents"] == whole["distinct_documents"]
        assert [(t["x"], t["y"], t["placed"]) for t in only["themes"]] == [
            (t["x"], t["y"], t["placed"]) for t in whole["themes"]
        ]
    assert any(t["x"] is not None for t in only["themes"])
    assert len(list(folder.glob("map-*.json"))) == 1


def test_the_themes_alone_are_refused_in_the_one_shape_where_the_word_is_not_a_yes_or_no(
    served,
) -> None:
    context, _, _ = served
    status, payload = _get(context, "themes/map", themes_only="maybe")
    assert status == 400
    assert ErrorResponse.model_validate(payload).error.parameter == "themes_only"
    status, payload = _get(context, "themes/map", themes_only="false")
    assert status == 200 and payload["themes_only"] is False and payload["experiments"]


def test_the_themes_alone_say_in_the_one_shape_when_there_is_no_run_or_no_component(
    served, tmp_path
) -> None:
    context, _, _ = served
    empty = Project.create(tmp_path / "empty", created_at="t", roots=[tmp_path])
    status, payload = _get(
        v1.context_for(empty.path, settled=None, job_running=False),
        "themes/map",
        themes_only="true",
    )
    assert status == 404
    ErrorResponse.model_validate(payload)
    THEME_ENGINES.set_enabled("lsa_louvain", enabled=False)
    try:
        status, payload = _get(context, "themes/map", themes_only="true")
    finally:
        THEME_ENGINES.set_enabled("lsa_louvain", enabled=True)
    assert status == 503
    assert ErrorResponse.model_validate(payload).error.code == "component_unavailable"


def test_the_themes_alone_answer_while_another_read_draws_the_layout(fresh, monkeypatch) -> None:
    """The first full read takes a minute on a large project; the themes alone do not wait."""
    import threading
    import time

    context, _ = fresh
    started, release = threading.Event(), threading.Event()
    original = mapxy.plane_of

    def slow(vectors, **keywords):
        started.set()
        assert release.wait(30), "the test never released the layout"
        return original(vectors, **keywords)

    monkeypatch.setattr(mapxy, "plane_of", slow)
    answers: list[object] = []
    reader = threading.Thread(target=lambda: answers.append(_get(context, "themes/map")))
    reader.start()
    try:
        assert started.wait(30), "the full read never began drawing"
        began = time.monotonic()
        status, only = _get(context, "themes/map", themes_only="true")
        waited = time.monotonic() - began
        assert status == 200 and only["layout_ready"] is False and only["themes"]
        assert reader.is_alive(), "the layout was drawn before the themes alone were answered"
        assert waited < 10, waited
    finally:
        release.set()
        reader.join(60)
    assert answers and answers[0][0] == 200
