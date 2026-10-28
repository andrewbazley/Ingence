"""One builder for every run record.

A run record says what produced a run, so that a result can be explained, compared and
reproduced. Every command builds its record here, and the builder fills the provenance. The
caller does not have to remember it. The provenance fields are:

- ``engine_version`` (the package) and ``engine_commit`` (``core/provenance.engine_commit``).
- ``config_hash``: the digest of the settings the run was bound by, as canonical JSON, after any
  depth profile was applied, so two runs under one policy share it whatever the file's notation.
- ``library_versions``: :data:`RESULT_LIBRARIES`, and each optional reader library installed.
- ``platform``: the machine the run happened on.

Callers pass through the parameters that only they know: ``record_watermark`` and ``job_id``
(durable jobs), ``date_years`` (the tokeniser's years), ``hash_mode`` and ``hash_mode_set_by``
(the dive), and ``warnings`` (the bounded tally). :func:`missing_provenance` names what a
published record lacks, and a test holds every command to it.
"""

from __future__ import annotations

import functools
import platform as platform_module
import time
from contextvars import ContextVar
from functools import cache
from importlib import metadata
from typing import TYPE_CHECKING, Any

from ingence.core.ids import CaseSensitivity, canonical_json, digest_text
from ingence.core.provenance import engine_commit
from ingence.core.schema import PlatformRecord, RunRecord

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ingence.core.schema import WarningTally
    from ingence.core.settings import Settings
    from ingence.extractors.captured import WarningTallies

__all__ = [
    "OPTIONAL_LIBRARIES",
    "REQUIRED_PROVENANCE",
    "RESULT_LIBRARIES",
    "build_run_record",
    "config_hash",
    "counting_warnings",
    "library_versions",
    "missing_provenance",
    "platform_now",
    "warnings_counted",
]

#: The libraries whose behaviour can change a result, always recorded: the query engine and
#: the columnar format every table is read through, the validation every record passes, the digest
#: every identity rests on, the arrays the readers decode into, and the base install's three
#: readers of PDFs, workbooks and legacy Office files.
RESULT_LIBRARIES: tuple[str, ...] = (
    "duckdb",
    "pyarrow",
    "pydantic",
    "blake3",
    "numpy",
    "pypdf",
    "openpyxl",
    "olefile",
    "pyyaml",
)

#: The optional readers' and detectors' libraries, recorded where installed: an optional
#: component present changes what a run can find, and absent it is simply not listed.
OPTIONAL_LIBRARIES: tuple[str, ...] = (
    "tifffile",
    "Pillow",
    "nbformat",
    "datasketch",
    "pyahocorasick",
    "imagehash",
    "pylibCZIrw",
    "python-magic",
    "magika",
)

#: What every published run must state. ``engine_commit`` is required wherever the engine can
#: say it (a checkout or a stamped build). An installed engine that cannot is not faulted for it.
REQUIRED_PROVENANCE: tuple[str, ...] = (
    "engine_version",
    "engine_commit",
    "config_hash",
    "library_versions",
    "platform",
)


@cache
def library_versions() -> tuple[tuple[str, str], ...]:
    """Each result library's installed version, then each optional one present, sorted by name."""
    found: dict[str, str] = {}
    for name in RESULT_LIBRARIES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = "absent"
    for name in OPTIONAL_LIBRARIES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return tuple(sorted(found.items()))


def config_hash(settings: Settings) -> str:
    """The digest of the settings a run was bound by: canonical JSON, so order never counts."""
    return digest_text(canonical_json(settings.model_dump(mode="json")))


def platform_now(case_sensitivity: CaseSensitivity | str | None = None) -> PlatformRecord:
    """The machine this process runs on, with the first root's case label where one is known."""
    return PlatformRecord(
        system=platform_module.system(),
        release=platform_module.release(),
        machine=platform_module.machine(),
        python_version=platform_module.python_version(),
        case_sensitivity=CaseSensitivity(case_sensitivity or CaseSensitivity.UNKNOWN),
    )


def _platform_of(project: Any) -> PlatformRecord:
    record = getattr(project, "platform_record", None)
    if callable(record):
        return PlatformRecord(**record())
    return platform_now()


#: The tallies of the pass now running under :func:`counting_warnings`, if one is.
_COUNTING: ContextVar[WarningTallies | None] = ContextVar("counting_warnings", default=None)


def counting_warnings[**P, T](source: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Count what libraries say while a pass runs onto the run record it builds.

    The census, inference and the dive all count warnings. The schema gives every run record
    ``warnings``. The dive fills it around each file it reads. A pass decorated with this runs
    inside :func:`~ingence.extractors.captured.tallied`, and :func:`build_run_record`, called
    within it, adds the tallies to the record it builds, under ``source`` (the pass's name). The
    counts are bounded as the dive's are: exact counts, a few words a kind. A pass called inside
    another that counts (a census a job runs within one) adds to the outer one's tallies instead
    of starting its own.
    """

    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(function)
        def counted(*args: P.args, **kwargs: P.kwargs) -> T:
            if _COUNTING.get() is not None:
                return function(*args, **kwargs)
            from ingence.extractors.captured import tallied

            with tallied(source) as tallies:
                token = _COUNTING.set(tallies)
                try:
                    return function(*args, **kwargs)
                finally:
                    _COUNTING.reset(token)

        return counted

    return decorate


def build_run_record(
    *,
    run_id: str,
    started_at: str,
    profile: str,
    settings: Settings | None,
    project: Any = None,
    parent_run_id: str | None = None,
    ended_at: str | None = None,
    record_watermark: int | None = None,
    job_id: str | None = None,
    date_years: tuple[int, int] | None = None,
    hash_mode: str | None = None,
    hash_mode_set_by: str | None = None,
    warnings: Iterable[WarningTally] = (),
    warnings_tallied: bool | None = None,
    **fields: Any,
) -> RunRecord:
    """Build one command's run record with its provenance filled.

    ``settings`` are the settings the run was bound by (after any profile), ``project`` the
    project it ran in, whose first root's case label the platform carries. Every other field a
    command states about itself (``counts``, ``components``, ``rulebook_hash`` and the rest) is
    passed through ``fields`` as the schema names it.

    ``warnings`` are the pass's own tallies. Within a pass decorated with
    :func:`counting_warnings`, the function adds what libraries said so far to them.
    ``warnings_tallied`` True says the pass counted its warnings. Each pass that counts says so
    (the dive does, and a census and an inference count, which makes it True whatever it says).
    None, the default, says nothing, and a reader shows the run's empty ``warnings`` as unknown.
    """
    from ingence import __version__

    counting = _COUNTING.get()
    counted = tuple(warnings) + (counting.tallies() if counting is not None else ())

    return RunRecord(
        run_id=run_id,
        parent_run_id=parent_run_id,
        started_at=started_at,
        ended_at=ended_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        engine_version=__version__,
        engine_commit=engine_commit(),
        profile=profile,
        config_hash=None if settings is None else config_hash(settings),
        library_versions=library_versions(),
        platform=fields.pop("platform", None) or _platform_of(project),
        record_watermark=record_watermark,
        job_id=job_id,
        date_years=date_years,
        hash_mode=hash_mode,
        hash_mode_set_by=hash_mode_set_by,
        warnings=counted,
        warnings_tallied=True if counting is not None else warnings_tallied,
        **fields,
    )


def missing_provenance(record: RunRecord) -> tuple[str, ...]:
    """The provenance fields a published run lacks, in :data:`REQUIRED_PROVENANCE`'s order."""
    missing: list[str] = []
    for name in REQUIRED_PROVENANCE:
        value = getattr(record, name)
        if name == "engine_commit" and engine_commit() is None:
            continue
        if value is None or value in ((), ""):
            missing.append(name)
    return tuple(missing)


#: The schema whose runs first counted their warnings (``RunRecord.warnings``).
WARNINGS_SCHEMA = "1.22"


def warnings_counted(record: RunRecord) -> bool:
    """Whether a run's ``warnings`` is a count, or the default of an engine that never counted.

    An older run's warnings are unknown, not none. A run written before schema 1.22 reads its
    warnings as ``()`` through the compatibility projection, which would say it raised none. The
    schema that wrote it says which, so a reader shows the older run's as unknown.

    A census or inference run written at schema 1.22 before warning counting was added is unknown
    too. At that time only the dive counted, and the empty warnings of the others said "none".
    A later run says whether it counted (``warnings_tallied``). For a run written before that
    field, the function reads ``hash_mode_set_by``, which says who chose the digest. Every 1.22
    dive fills it and no other pass does.
    """
    from ingence.core.versions import is_newer

    if is_newer(WARNINGS_SCHEMA, than=record.schema_version):
        return False
    if record.warnings_tallied is not None:
        return record.warnings_tallied
    return record.hash_mode_set_by is not None
