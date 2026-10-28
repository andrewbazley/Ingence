"""Revision 5, file types and writers: what a file's type is from its own bytes, and which
program wrote it from its own tags.

Two rules, each reading what a content pass left on a file and the file-type table
(``dictionary/file_types.yaml``):

- ``format_from_content``: the format whose signature the bytes carry, which stands whatever
  the extension says, so a PNG named ``.csv`` is an image once the extension's claim is
  retracted;
- ``stage_from_writer_tags`` (predicate ``writer_from_content_tags``): the stage or role the
  program named in the file's tags gives it. An assumption, at the abductive tier and said as
  one in its statement: the program named is taken to be the one that made the file, a stage
  the file's own name states overrides it, and two kinds of program named together leave it
  unconcluded;
- ``export_stage_from_inner_format`` (predicate ``derived_from_inner_format``): a file named
  with another kind of file's extension before its own (``x.nd2.csv``) was exported from a
  file of that kind, so it sits at the export stage. An assumption too, defeated where the
  bytes contradict the outer extension or the name states a stage; the grouping by the
  acquisition a file is named for (revision 4) already joins such a file to its source.

The two stage rules carry "stage" in their ids, because a claim's predicate is
``classifies.<rule id>``. The completeness counts, the search scale, the classes page and the
evaluation's scoring read stage claims by that. Each of the two rules also names itself in the three
lists that those readers keep.
"""

from __future__ import annotations

from ingence.rulebook.revision_4 import _CONTAINER_MEDIA_TYPES, _UNCLEAR_MEDIA_TYPES

__all__ = ["CHANGES", "RULES"]

#: The general-purpose containers a format is written on, and the one whose use only a
#: laboratory can say, as revision 4's content rules declare them: one list, read here so the
#: two readings cannot drift apart.
#: The readings the TIFF reader leaves a writer's name in. Not the description's kind: an
#: ``ImageJ=`` description is a hyperstack convention acquisition programs write too.
_TAG_KEYS = ["image.software", "image.ome_creator", "image.vendor_tags"]

#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = [
    {
        "id": "format_from_content",
        "version": "1",
        "predicate": "format_from_content",
        "effect": "classifies",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": ["format_built_on_a_container"],
        "statement": (
            "A file is of the format whose signature its bytes carry, as the {detector} read "
            "it, where the file-type table names a format of that signature and it is not one "
            "of {container_media_types} or {unclear_media_types}, which a format is written on "
            "rather than being; read from the content, this stands whatever the extension says, "
            "unless the container rule raises the signature as a container's."
        ),
        "rationale": (
            "Content only retracted a format read from an extension: a JPEG named .tif lost its "
            "TIFF claim and gained nothing, so a lying extension left the file with no type at "
            "all. The signature is fixed by the format's own specification, and the table says "
            "which format each marks, so the type is read from the bytes and the extension is "
            "the name somebody gave it. A container's signature concludes nothing, since a "
            "vendor writes its own format on one, which is the reading the container rule takes."
        ),
        "parameters": [
            {
                "name": "detector",
                "type": "string",
                "default": "signature",
                "description": (
                    "The type detector whose opinion stands behind the reading: a signature, "
                    "compared against the table's rows."
                ),
            },
            {
                "name": "container_media_types",
                "type": "string_list",
                "default": _CONTAINER_MEDIA_TYPES,
                "description": (
                    "The general-purpose containers a format is written on; a signature naming "
                    "one concludes no format."
                ),
            },
            {
                "name": "unclear_media_types",
                "type": "string_list",
                "default": _UNCLEAR_MEDIA_TYPES,
                "description": "The containers whose use only a laboratory can say.",
            },
        ],
    },
    {
        "id": "stage_from_writer_tags",
        "version": "1",
        "predicate": "writer_from_content_tags",
        "effect": "classifies",
        "tier": "abductive",
        "level": "file",
        "defeated_by": ["pipeline_stage_from_name", "later_stage_from_name"],
        "statement": (
            "A file whose tags ({tag_keys}) name a program the file-type table lists as a "
            "writer sits at the {acquisition_concludes} stage where that program acquires, at "
            "the {analysis_concludes} stage where it analyses, and is a {figure_concludes} "
            "where it draws figures, and nothing where programs of two kinds are named; this "
            "assumes the program named in the tags is the one that made the file, and a stage "
            "the file's own name states overrides it."
        ),
        "rationale": (
            "A TIFF is written by a microscope's software and by an image editor alike, so its "
            "extension says nothing about whether it is recorded data, and the engine never "
            "read the Software tag, the OME creator or the vendor tags that do. The program "
            "named there is the best explanation of where the file came from, not a fact: a "
            "file analysis software saved keeps the acquisition software's marks, and a tag is "
            "copied with the pixels, which is why the rule is an assumption a person can "
            "reject once for the class, a name's own stage word defeats, and two kinds of "
            "program named together leave unconcluded."
        ),
        "parameters": [
            {
                "name": "tag_keys",
                "type": "string_list",
                "default": _TAG_KEYS,
                "description": (
                    "The readings a writer's name is looked for in: the Software tag, the OME "
                    "creator and the vendor tags present. Not the description's kind: an "
                    "ImageJ-shaped description is a convention acquisition programs write too."
                ),
            },
            {
                "name": "acquisition_concludes",
                "type": "string",
                "default": "acquisition",
                "description": "The stage a file written by acquisition software sits at.",
            },
            {
                "name": "analysis_concludes",
                "type": "string",
                "default": "processed",
                "description": (
                    "The stage a file written by analysis software sits at: derived from "
                    "something, past acquisition."
                ),
            },
            {
                "name": "figure_concludes",
                "type": "string",
                "default": "figure",
                "description": "What a file written by a drawing program is: a figure.",
            },
        ],
    },
]

#: What this module changes in rules an earlier revision declared (supersede, override, enable).
#: Nothing. The export rule could defeat the acquisition stage if ``acquisition_stage_from_format``
#: were declared again at a new version that names it. But one revision says one thing about a
#: rule (``rulebook/load.py``), so a rule that changes is renamed. The stage lists, the classes
#: page, the experiment places and revision 4's own tests read the acquisition rule's id, so the
#: rule keeps it. The acquisition rule reads the extension slot, which for ``x.nd2.csv`` is the
#: table's, so it concludes nothing on an export.
CHANGES: list[dict[str, object]] = []

RULES.append(
    {
        "id": "export_stage_from_inner_format",
        "version": "1",
        "predicate": "derived_from_inner_format",
        "effect": "classifies",
        "tier": "abductive",
        "level": "file",
        "defeated_by": [
            "content_contradicts_extension",
            "pipeline_stage_from_name",
            "later_stage_from_name",
        ],
        "statement": (
            "A file whose name ends in two extensions, both kinds of file the file-type table "
            "knows and not two spellings of one, the pair no spelling of its own, neither a "
            "compression wrapped around the other, the inner one neither an ordinary word as "
            "well nor a document's and the outer one not an instrument's own, was written out "
            "of a file of the inner "
            "kind and sits at the {concludes} stage; this assumes the inner extension is the "
            "source's and not a word, and bytes contradicting the outer extension or a stage "
            "the file's own name states defeat it."
        ),
        "rationale": (
            "A tool exporting a table from an image names the table after the image, "
            "extension included, and the census already keeps that two-part suffix. The "
            "extension slot reads the outer kind, so nothing said the table was derived; this "
            "says so, as an assumption a person can reject once for the class, and leaves the "
            "join to the source to the rule grouping files by the acquisition they are named "
            "for."
        ),
        "parameters": [
            {
                "name": "concludes",
                "type": "string",
                "default": "export",
                "description": "The stage an export sits at: written out of another file.",
            },
        ],
    }
)
