"""Which spellings of which formats are recorded data: one function, two sources.

Where data begins is read from who writes a spelling. A rule that concludes a file is an
acquisition reads the ``origins`` a format sense declares, spelling by spelling, and admits a
spelling that an instrument writes. Two places can say so:

- the format senses' own ``origins``, as the Dictionary declares them (the sequencing pack
  declares a FASTQ's);
- the file-type table (``dictionary/file_types.yaml``), where a row marked ``can_be_raw: yes``
  makes every extension of its family an instrument's.

:func:`recorded_origins` is the one place that makes that choice, so a rule reads one mapping. A
table that cannot be read (``FileTypeTableError``) leaves the senses' origins as they are.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingence.dictionary.file_types import FileTypeTableError, file_type_table
from ingence.dictionary.store import ORIGINS, WRITTEN_BY

if TYPE_CHECKING:
    from ingence.dictionary.store import DictionaryStore

__all__ = ["ACQUISITION_WRITER", "format_origins", "recorded_origins"]

#: Who writes a spelling that begins a workflow, as the Dictionary declares it.
ACQUISITION_WRITER = "instrument"


def format_origins(vocabulary: DictionaryStore) -> dict[str, dict[str, str]]:
    """Return who writes each declared spelling of each format, by term.

    The function reads the ``origins`` property a format sense declares: a spelling, casefolded,
    and its ``written_by``. A sense that declares none is left out, which a rule reads as "any
    spelling of this sense", never as "no spelling".

    A sense that declares an empty map is in, with nothing. The core's image type declares origins
    and names no writer, because anything writes a TIFF, and the spellings an instrument writes
    arrive with the microscopy pack. If the function left the core's half out, it would read as a
    sense that declares none, and every TIFF would count as an instrument's.
    """
    declared: dict[str, dict[str, str]] = {}
    for sense in vocabulary.senses():
        origins = sense.properties.get(ORIGINS)
        if not isinstance(origins, dict):
            continue
        spelled = declared.setdefault(sense.term, {})
        for spelling, origin in origins.items():
            written_by = origin.get(WRITTEN_BY) if isinstance(origin, dict) else None
            if isinstance(written_by, str):
                spelled[str(spelling).casefold()] = written_by
    return {term: dict(sorted(spelled.items())) for term, spelled in sorted(declared.items())}


def recorded_origins(vocabulary: DictionaryStore) -> dict[str, dict[str, str]]:
    """Who writes each spelling of each format, with the file-types table laid over the senses.

    The senses' ``origins`` come first (:func:`format_origins`). Then, where the file-type table
    can be read, every spelling of a type it marks ``can_be_raw: yes`` is an instrument's, laid
    over whatever the senses said of that spelling. A table that cannot be read changes nothing, so
    the result is exactly what the Dictionary declares.
    """
    origins = format_origins(vocabulary)
    try:
        table = file_type_table()
    except FileTypeTableError:
        return origins
    for row in table.rows:
        if not row.family:
            continue
        # A family the table knows declares its origins. An empty map says "anything writes
        # this", never "any spelling is an instrument's" (as with the core image type).
        held = origins.setdefault(row.family, {})
        if row.can_be_raw == "yes":
            for spelling in row.extensions:
                held[spelling.casefold()] = ACQUISITION_WRITER
    return {term: dict(sorted(spelled.items())) for term, spelled in sorted(origins.items())}
