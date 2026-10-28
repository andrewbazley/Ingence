"""Revision 5, workflows and stages: which workflow an experiment follows, which stage each of
its files is at, and which stages it reached.

Each rule here is declared again under its own identifier at a higher version, which displaces
the revision 4 declaration of the same identifier (``rulebook/resolve.py``: the later version
in force), or is new. Nothing earlier is edited.

- ``acquisition_stage_from_format`` v4: a read file (``fastq``, ``fq``) is recorded data. The
  sequencing format sense declares who writes those two spellings, and the rule names the
  sense; a FASTA, a reference or an assembly, declares no writer and is not admitted.
- ``pipeline_stage_from_name`` v3: a stage word is read only where it stands alone as a part of
  the name (``rawSignal`` and ``crop.raw.bam`` are not at the raw stage), and never on an archive
  bundle (``zeph_RAW.tar`` is named for what it holds).
- ``replicates_of_one_condition`` v2: stage words count, so one file at two processing steps
  (``X.bam`` and ``X.aligned.bam``) is not two replicates of one condition.
- ``same_file_at_two_steps`` and ``same_file_later_step``: the class those names still share,
  read as derivation candidates, the earlier step proposed as the source of the later.
"""

from __future__ import annotations

__all__ = ["CHANGES", "RULES", "WORKFLOW_PREDICATE"]

_QUALITY = {"type": "enum", "choices": ["exact", "case_folded", "loose"]}

#: The shipped noise and placeholder defeaters, as revision 4 names them on every grouping.
_NOT_NOISE_NOR_PLACEHOLDER = ["filesystem_noise_from_spelling", "placeholder_from_entry_kind"]

#: The pipeline stages a name may state, as revision 4 lists them for ``pipeline_stage_from_name``.
_PIPELINE_STAGES = [
    "raw",
    "acquisition",
    "preprocessed",
    "intermediate",
    "processed",
    "analysis",
    "pooled",
    "quantification",
    "summary",
    "report",
    "export",
]

#: The order of steps one file passes through, for ``same_file_later_step``: where both names
#: state a stage, the one reaching further along this list is the later step.
_STEP_ORDER = [
    "raw",
    "acquisition",
    "preprocessed",
    "intermediate",
    "aligned",
    "filtered",
    "normalised",
    "processed",
    "quantification",
    "analysis",
    "pooled",
    "summary",
    "report",
    "export",
]

#: The formats a name never stages: a bundle is named for what it holds.
_BUNDLE_FORMATS = ["archive_format", "compressed_archive"]

#: The rules this module declares, each a dict shaped like revision 4's.
RULES: list[dict[str, object]] = [
    {
        "id": "acquisition_stage_from_format",
        "version": "4",
        "predicate": "class_from_format_origin",
        "effect": "classifies",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": ["content_contradicts_extension", "later_stage_from_name"],
        "statement": (
            "A file whose {slot} matches one of {senses} at quality "
            "{minimum_match_quality} or better, in a spelling written by {written_by} where "
            "the sense declares origins, sits at the {concludes} stage, unless its bytes "
            "contradict its extension or its own name states a later stage."
        ),
        "rationale": (
            "An instrument's own format is written by the thing that acquired the data or "
            "by a converter preserving it, so a file in one is where some workflow's data "
            "begins; it says nothing about which workflow, and the identity a derivation "
            "is drawn within, folder included, is what keeps two workflows apart. It "
            "reads the spelling's origin, because images are one type. Version 4 names the "
            "read format too: a sequencer writes FASTQ, and the sense now says which of its "
            "spellings an instrument writes, so a reference or an assembly in FASTA, which "
            "declares no writer, is not admitted."
        ),
        "parameters": [
            {
                "name": "senses",
                "type": "string_list",
                "default": ["image_format", "ephys_format", "read_format"],
                "description": (
                    "The format senses that mean data as an instrument produced it, each "
                    "admitted spelling by spelling where the sense declares origins. Spectra "
                    "and their conversions are left out, because their formats also hold "
                    "identification results."
                ),
            },
            {
                "name": "written_by",
                "type": "string_list",
                "default": ["instrument"],
                "description": (
                    "Who must write a spelling for it to begin a workflow, where its sense "
                    "declares origins."
                ),
            },
            {
                "name": "concludes",
                "type": "string",
                "default": "acquisition",
                "description": "The stage concluded, so the proposing rule reads it.",
            },
            {
                "name": "minimum_match_quality",
                **_QUALITY,
                "default": "exact",
                "description": "How firmly the extension must have matched: it is a symbol.",
            },
            {
                "name": "slot",
                "type": "enum",
                "default": "extension",
                "choices": ["extension", "name"],
                "description": "Where the match is read: the extension, never a word.",
            },
        ],
    },
    {
        "id": "pipeline_stage_from_name",
        "version": "3",
        "predicate": "stage_from_name_parts",
        "effect": "classifies",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": ["program_from_extension", "installed_software_in_folder"],
        "statement": (
            "A file sits at the pipeline stage its name states, where the parts of its name "
            "standing alone (only so: {whole_parts_only}) reach {minimum_senses} or more distinct "
            "senses of kind {theme_kind} among {senses}, matched at quality "
            "{minimum_match_quality} or better, never where its extension is one of "
            "{not_in_formats}, unless the file is a program or lies in a folder laid out as an "
            "installed program."
        ),
        "rationale": (
            "Every field that produces data has a raw form, an intermediate form and a result, "
            "and the words for those are shared across all of them. A stage word inside "
            "another word, or after the first dot where a tool writes its suffixes, names a "
            "step done to the file and not where it sits; and an archive is named for what it "
            "holds. A program that names a stage performs it rather than sitting at it."
        ),
        "parameters": [
            {
                "name": "theme_kind",
                "type": "string",
                "default": "stage",
                "description": "The axis to read.",
            },
            {
                "name": "senses",
                "type": "string_list",
                "default": _PIPELINE_STAGES,
                "description": (
                    "The pipeline stages. The transformations applied at a stage, such as "
                    "cropped or stitched, are left out, and so is unaffiliated."
                ),
            },
            {
                "name": "minimum_senses",
                "type": "integer",
                "default": 1,
                "minimum": 1,
                "maximum": 10,
                "description": "How many distinct stages a name needs.",
            },
            {
                "name": "minimum_match_quality",
                **_QUALITY,
                "default": "loose",
                "description": "How firmly a stage word must have matched.",
            },
            {
                "name": "whole_parts_only",
                "type": "boolean",
                "default": True,
                "description": (
                    "A stage word counts only where it is one whole part of the name's head, "
                    "cut on an underscore, a dash or a space, before the first dot."
                ),
            },
            {
                "name": "not_in_formats",
                "type": "string_list",
                "default": _BUNDLE_FORMATS,
                "description": "A file of one of these formats is a bundle and is not staged.",
            },
        ],
    },
    {
        "id": "replicates_of_one_condition",
        "version": "2",
        "predicate": "same_component_values",
        "effect": "groups",
        "primitive": "equivalent",
        "specialization": "same_condition_as",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": [*_NOT_NOISE_NOR_PLACEHOLDER, "later_step_of_a_sibling"],
        "statement": (
            "Files are replicates of one condition when, compared within one "
            "{compared_within}, they agree on every component of kind {component_kinds}, in "
            "the same places for a component of kind {ordered_component_kinds}, leaving out a "
            "word or an indexed label matched to a sense of kind {ignored_theme_kinds} and every "
            "bare number, and each carries {minimum_components} or more of them, unless a member "
            "is filesystem noise, a placeholder, or a sibling at a later step."
        ),
        "rationale": (
            "Grouping has levels, and the first is 'do these two .nd2 files, which look "
            "like raw data, replicates from the same condition in one experiment, go "
            "together?'. A bare number is how an instrument or a person counts movies and "
            "fields; a quantity with its unit is a condition and keeps its place, and the "
            "extension is kept, so a movie and its table are two series. Version 2 keeps "
            "version 1's key (a stage word is left out, so a file saved with the step word "
            "and one saved without are still one condition) and leaves out a file that is a "
            "sibling at a later step (X.aligned.bam beside X.bam), which is the same file "
            "twice and no replicate of itself; the rule beside this one proposes the step. "
            "Two later steps beside each other (R1.aligned.bam, R2.aligned.bam) are both left "
            "out and are then no replicates of each other: the known loss."
        ),
        "parameters": [
            {
                "name": "compared_within",
                "type": "enum",
                "default": "folder",
                "choices": ["pool", "folder"],
                "description": "Where two names are compared: one folder's series.",
            },
            {
                "name": "component_kinds",
                "type": "string_list",
                "default": ["word", "indexed", "quantity", "date", "version", "extension"],
                "description": "Every kind of name component but the bare number.",
            },
            {
                "name": "ignored_theme_kinds",
                "type": "string_list",
                "default": ["stage", "replicate"],
                "description": (
                    "A word or indexed label matched to a sense of these kinds is left out."
                ),
            },
            {
                "name": "minimum_components",
                "type": "integer",
                "default": 2,
                "minimum": 1,
                "maximum": 20,
                "description": "How many components each side must carry.",
            },
            {
                "name": "ordered_component_kinds",
                "type": "string_list",
                "default": ["quantity"],
                "description": "Quantities are compared in the place the name wrote them.",
            },
        ],
    },
    {
        "id": "same_file_at_two_steps",
        "version": "1",
        "predicate": "same_component_values",
        "effect": "groups",
        "primitive": "equivalent",
        "respect": "processing_step",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
        "statement": (
            "Two files are one file at two processing steps when, compared within one "
            "{compared_within} (a stage subfolder's name, matched to a sense of kind "
            "{ignored_subfolder_theme_kinds}, left out), they agree on every component of kind "
            "{component_kinds}, in the same places for a component of kind "
            "{ordered_component_kinds}, leaving out a word or an indexed label matched to a "
            "sense of kind {ignored_theme_kinds}, and each carries {minimum_components} or more "
            "of them, unless a member is filesystem noise or a placeholder."
        ),
        "rationale": (
            "A tool keeps a file's name and adds what it did (X.bam, X.aligned.bam; X_raw.nd2, "
            "X_processed.nd2), so names agreeing on every word, number and label but their "
            "stage words are one file seen at two steps. The bare numbers count here, where "
            "the replicates rule leaves them out: the two files of one step are counted "
            "alike, and two numbers are two files."
        ),
        "parameters": [
            {
                "name": "compared_within",
                "type": "enum",
                "default": "experiment",
                "choices": ["pool", "folder", "experiment"],
                "description": (
                    "Where two names are compared: within the experiment above a file, and "
                    "within its folder where no experiment lies above it."
                ),
            },
            {
                "name": "ignored_subfolder_theme_kinds",
                "type": "string_list",
                "default": ["stage"],
                "description": "A stage subfolder is no place of its own.",
            },
            {
                "name": "component_kinds",
                "type": "string_list",
                "default": [
                    "word",
                    "number",
                    "indexed",
                    "quantity",
                    "date",
                    "version",
                    "extension",
                ],
                "description": "Every kind of name component, the bare number included.",
            },
            {
                "name": "ignored_theme_kinds",
                "type": "string_list",
                "default": ["stage"],
                "description": "The stage words, which are what the two names differ by.",
            },
            {
                "name": "minimum_components",
                "type": "integer",
                "default": 2,
                "minimum": 1,
                "maximum": 20,
                "description": "How many components each side must carry.",
            },
            {
                "name": "ordered_component_kinds",
                "type": "string_list",
                "default": ["number", "quantity"],
                "description": "Numbers and quantities keep the places the name wrote them in.",
            },
        ],
    },
    {
        "id": "same_file_later_step",
        "version": "1",
        "predicate": "later_step_of_same_file",
        "effect": "proposes",
        "primitive": "derives",
        "specialization": "processed_from",
        "tier": "defeasible",
        "level": "file",
        "candidates_within": "same_file_at_two_steps",
        "defeated_by": ["installed_software_in_folder"],
        "statement": (
            "Two files that are one file at two processing steps are proposed as the later "
            "made from the earlier: the file whose name states the fewer stage words of kind "
            "{theme_kind} (matched at quality {minimum_match_quality} or better), or the stage "
            "earlier in {stage_order}, is the source of the one stating more or later, link by "
            "link where several steps follow, never where the two reach the same stage, and the "
            "pair is blocked where either file lies in a folder laid out as an installed program."
        ),
        "rationale": (
            "A name that keeps another's and adds a stage word was written by a tool after "
            "reading the other, so the step is a derivation and the direction is the name's "
            "own. No clock is read: a copy's time is a copy's. Two names reaching the same "
            "stage say nothing about which came first and are left alone."
        ),
        "parameters": [
            {
                "name": "theme_kind",
                "type": "string",
                "default": "stage",
                "description": "The axis the stage words are read on.",
            },
            {
                "name": "minimum_match_quality",
                **_QUALITY,
                "default": "loose",
                "description": "How firmly a stage word must have matched.",
            },
            {
                "name": "stage_order",
                "type": "string_list",
                "default": _STEP_ORDER,
                "description": (
                    "The order of the steps a file passes through, earliest first; a stage not "
                    "listed orders nothing."
                ),
            },
        ],
    },
]

RULES.append(
    {
        "id": "later_step_of_a_sibling",
        "version": "1",
        "predicate": "later_step_of_a_sibling",
        "effect": "classifies",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": ["installed_software_in_folder"],
        "statement": (
            "A file is a sibling at a later step where its name, less its numbers and extension "
            "and less each word matched to a sense of kind {theme_kind} at quality "
            "{minimum_match_quality} or better, is the name of a file holding bytes beside it, "
            "unless it lies in a folder laid out as an installed program."
        ),
        "rationale": (
            "A tool that keeps a file's name and adds what it did writes the same file at a "
            "later step, beside the one it read. Read as a defeater by the replicates rule, "
            "which leaves stage words out of its key and would otherwise group the two as "
            "replicates of one condition."
        ),
        "parameters": [
            {
                "name": "theme_kind",
                "type": "string",
                "default": "stage",
                "description": "The axis the stage words are read on.",
            },
            {
                "name": "minimum_match_quality",
                **_QUALITY,
                "default": "loose",
                "description": "How firmly a stage word must have matched.",
            },
        ],
    }
)

#: The one rule that the pass evaluates after the suggester (phase B), by its predicate. It is a
#: classifying rule that reads the workflows in force, which the first phase does not hand out.
WORKFLOW_PREDICATE = "stage_in_workflow"

RULES.append(
    {
        "id": "stage_from_workflow",
        "version": "2",
        "predicate": WORKFLOW_PREDICATE,
        "effect": "classifies",
        "tier": "abductive",
        "level": "file",
        "defeated_by": [
            "program_from_extension",
            "installed_software_in_folder",
            *_NOT_NOISE_NOR_PLACEHOLDER,
        ],
        "statement": (
            "A file no rule reading the file itself placed sits at the stage of the workflow in "
            "force whose stage lists its file type (only so: {by_type}), the folder it lies in "
            "deciding between stages and never contradicted (only so: {by_folder}), or whose "
            "stage lists its name among its name patterns; a suggested workflow read only "
            "inside the experiments it was counted from, at the rungs each reached, or in a "
            "folder one of its stages lists, a declared one within its scope; assumed until a "
            "person refuses or declares one; never for a file whose extension is one of "
            "{not_in_formats}, matching a workflow's exempt patterns, or placed differently by "
            "two workflows in force; and unless the file is a program, filesystem noise, a "
            "placeholder, or lies in a folder laid out as an installed program."
        ),
        "rationale": (
            "A workflow says which types enter and which follow, so a file of a listed type "
            "is at that stage, and a type at two stages is told apart by its folder. The "
            "reading rests on a decision, a person's or the run's own suggestion, so it is "
            "assumed and reversed by one answer on the workflow; and it rests on the "
            "experiments the suggestion was counted from, so a file outside them, which gave "
            "no evidence, is not placed by it, nor a file at a rung its own experiment never "
            "reached. Version 2 leaves a bundle, a noise file and a placeholder "
            "alone, and the rule sits below every rule that read the file itself."
        ),
        "parameters": [
            {
                "name": "by_type",
                "type": "boolean",
                "default": True,
                "description": "Whether a stage's file types place a file at it.",
            },
            {
                "name": "by_folder",
                "type": "boolean",
                "default": True,
                "description": "Whether the folder decides between two stages of one type.",
            },
            {
                "name": "not_in_formats",
                "type": "string_list",
                "default": _BUNDLE_FORMATS,
                "description": "A file of one of these formats is a bundle and is not staged.",
            },
        ],
    }
)

#: The source clock a tool copied: a crop that an image-analysis program cut from a recording keeps
#: the recording's acquisition clock, and the recording carries the same instant as its own.
RULES.extend(
    [
        {
            "id": "same_source_clock",
            "version": "1",
            "predicate": "same_acquisition_clock",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "source_clock",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
            "statement": (
                "Files of one experiment carrying one acquisition instant, read from a "
                "recording's own clock ({own_clock_keys}) or from the source clock a tool copied "
                "into what it wrote ({source_clock_keys}), within a tolerance, in seconds, of "
                "{tolerance_seconds}, are one group, unless a member is filesystem noise or a "
                "placeholder."
            ),
            "rationale": (
                "A crop cut by an image-analysis program from a recording keeps the recording's "
                "acquisition clock in its own metadata, and nothing in its name says which "
                "recording it came from. The instant, read from the two files' own records, is "
                "what joins them; kept within the experiment, so one instant in two experiments "
                "is two groups."
            ),
            "parameters": [
                {
                    "name": "own_clock_keys",
                    "type": "string_list",
                    "default": ["image.acquired_ns"],
                    "description": "The observation a recording's own clock is read from.",
                },
                {
                    "name": "source_clock_keys",
                    "type": "string_list",
                    "default": ["image.source_acquired_ns"],
                    "description": "The observation a copied source clock is read from.",
                },
                {
                    "name": "tolerance_seconds",
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 3600,
                    "description": "How many seconds one instant spans.",
                },
            ],
        },
        {
            "id": "derivation_from_source_clock",
            "version": "1",
            "predicate": "derives_from_source_clock",
            "effect": "proposes",
            "primitive": "derives",
            "specialization": "processed_from",
            "tier": "defeasible",
            "level": "file",
            "candidates_within": "same_source_clock",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "Within a group of one acquisition instant, the one file carrying its own clock "
                "({own_clock_keys}) at the {acquisition_stage} stage is proposed as the source "
                "of every file carrying only a copied clock, never where two recordings share "
                "the instant, and the pair is blocked where either file lies in a folder laid "
                "out as an installed program."
            ),
            "rationale": (
                "A tool copied the clock from what it read, so the recording the clock belongs "
                "to is what the file was made from, whatever either is named. Two recordings "
                "of one instant say nothing about which was read, and the rule abstains."
            ),
            "parameters": [
                {
                    "name": "own_clock_keys",
                    "type": "string_list",
                    "default": ["image.acquired_ns"],
                    "description": "The observation a recording's own clock is read from.",
                },
                {
                    "name": "acquisition_stage",
                    "type": "string",
                    "default": "acquisition",
                    "description": "The stage the recording's claim concludes.",
                },
            ],
        },
    ]
)

#: What this module changes in rules an earlier revision declared (supersede, override, enable).
#: Nothing: each change above is a redeclaration under the same identifier, which displaces.
CHANGES: list[dict[str, object]] = []
