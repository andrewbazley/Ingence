"""Ingence is a read-only indexing and inference engine and local Dashboard for exploring
scientific filesystems: it surveys what is there, and proposes how files relate using a
Dictionary and a Rulebook you can read and edit.

Two versions matter and they move independently.

``__version__`` is the package version. ``SCHEMA_VERSION`` is the version of the
frozen record contract in :mod:`ingence.core.schema`, stamped on every artifact
the engine writes. A package release that changes no record type leaves the
schema version alone. A change to a record type raises the schema version.
"""

__all__ = ["SCHEMA_VERSION", "__version__"]

#: The package version, ``0.MINOR.PATCH`` with ``.devN`` while pre-alpha (the README's "Versions"):
#: MINOR moves when a project becomes unreadable by the previous package (a schema minor bump, or
#: the Dashboard API's major), PATCH for every other merged change. 0.2 is schema 1.22 and /api/v1.
#: 0.3 is schema 1.23. 0.4 is schema 1.24 and /api/v1.
__version__ = "0.4.0"

#: Version of the frozen record contract. Stamped on every artifact.
#: Bumping the minor component means a field was added compatibly. Bumping the major component
#: means an existing field changed meaning or disappeared.
#:
#: 1.6 added ``Rule.defeated_by`` and the ``RuleFailure`` record.
#: 1.7 added ``RunRecord.recognised``: the engine's own files that a census recognised by their
#: layout, and whether it indexed them.
#: 1.8 added ``Rule.candidates_within``: the index within which a proposing rule draws its
#: candidate pairs.
#: 1.9 added ``Rule.status`` and the ``enumerates`` effect: the scan layer's own rules, declared
#: and provisional.
#: 1.10 added the ``Finding`` record: what a flagging rule raised about a subject. A finding is
#: never a claim.
#: 1.11 added ``Entry.symlink_state``: whether a symlink's target is there.
#: 1.12 stores ``Entry.inode`` unsigned. A real inode may lie at or above 2**63, and a signed
#: column refused one before the census could publish.
#: 1.13 added ``RunRecord.excluded``: the names a census left out on purpose, counted where each
#: was left out, by reason and type.
#: 1.14 added ``RunRecord.mentions``: a run's mentions tallied by the mentioning file's extension
#: and by shape.
#: 1.15 added ``Finding.defeaters``: what a flagging rule names as a reason that its finding may
#: not deserve attention. Defeaters are evaluated and recorded, and never hide the finding.
#: 1.16 added ``ScopeKind.FILE_TYPE``: files of one kind as a scope, beside the four regions.
#: This makes "in my ND2 files that word means this" sayable without naming a folder.
#: 1.17 added ``RunRecord.extraction_ceiling_seconds``: the wall clock that one file's supervised
#: reading was allowed, as a base and a rate per gigabyte. A reader can then see what bounded a
#: run, and need not infer it from a constant.
#: 1.18 added ``RunRecord.resolutions``: what an inference's reference resolution made of a
#: run's mentions, by the mentioning file's extension, shape and outcome. It also added
#: ``Root.case_established_by``: how a root's case label was read from its own names. It also
#: added ``RunRecord.packs``: the laboratory packs that a run's vocabulary loaded.
#: 1.19 added ``SymlinkState.OUTSIDE_ROOT``: a link whose target is written as a path outside
#: the root it was found under. The state is read from the link's text, and the target is never
#: ``stat``-ed.
#: 1.20 added ``RecordEventKind.NOTE``: a person's words about a target. A note decides nothing
#: and is never the decision in force, so a note on a row left without a verdict is kept. It also
#: added ``RunRecord.rebind_spellings``: the spellings that a partial rebind read again.
#: 1.21 added ``SourceScope.DECLARED``: what a third party states about a file, an archive's
#: listing above all. It is kept apart from what was observed and from what a person said. The
#: census reads a listing as a root (``listing:<archive>:<identifier>``) with every fact that the
#: listing does not state absent.
#: 1.22 carries several fields at once. ``RecordEventKind.WITHDRAW`` lets a person who goes back
#: to an earlier run withdraw later decisions and delete none. ``Entry.moved_from`` and
#: ``Entry.moved_by`` record a file at a new path, the entry it came from, and how that was
#: known. ``Entry.extraction_profile`` records the depth at which its content was read.
#: ``NameSlot.case_whole`` holds the whole spelling that a capital divided, for the binder's
#: choice. ``ScanOutcome.CHANGED_DURING_READ`` is a new outcome. The run record gains the Record
#: watermark (``record_watermark``), the engine's commit, the durable job that produced the run,
#: the digest that a content pass computed and what chose it, the years that a run read as dates,
#: and bounded warning tallies (``WarningTally``).
#: 1.23 added ``ProjectIdentity.name`` in ``project.json``: what a person called the project, so
#: that another computer shows the name and not the folder's. The field is written only where a
#: person gave a name, so a project nobody named holds none. An earlier build does not know the
#: field and cannot open a project that holds it. A build from 0.3.0 on says so in words (the
#: README's "Versions": a schema minor that makes a project unreadable by the previous package
#: moves the package's minor).
#: 1.24 (0.4.0) carries what 0.4.0 added to what a project holds. On the run record, it adds the
#: content pass's clocks and worker count (``wall_seconds``, ``work_seconds``, ``workers``).
#: It adds ``MatchQuality.CORRECTED`` (a spelling bound to a near Dictionary spelling) and the
#: ``AreaKind`` and ``AssumptionKind`` values. It adds new observation keys (``series.key``,
#: ``series.size_class``, ``name.domain_profile``), a run's ``workflows.json``, and the readouts
#: under ``projections/readouts/``. In the job ledger, it adds the ``cancelled`` state and a
#: person's stop requests. A 1.23 project opens and is upgraded where it is read. A run that it
#: holds reads the new run columns as null (the compatibility projection), and nothing of it is
#: read again. Its ledger is marked as one that may hold the new state the first time this engine
#: writes to it. A build before 0.4.0 refuses a run that this one published, in words
#: (``core.versions``).
SCHEMA_VERSION = "1.24"
