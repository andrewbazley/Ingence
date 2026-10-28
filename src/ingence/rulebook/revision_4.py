"""Revision 4 of the shipped Rulebook: the rules developed after revisions 0 to 3.

The revision is shipped, and :func:`ingence.rulebook.load.load_revisions` loads it with revisions 0
to 3. Earlier versions kept these rules as a project's own revision, written into the project's
``rulebook`` folder. A project that still holds that file has it skipped, and the choices a person
made in it are kept (:mod:`ingence.rulebook.retired`). The revision is one dict, declared in Python
where it was written and measured.

The revision renames what it changes. A rule of revisions 0 to 3 whose reading or defeat conditions
change is superseded and declared again under a name of its own, so the earlier sentence stays
readable beside the one in force. The copy rule, whose reading did not change, is declared again at
version 2.

What the revision declares:

- ``program_from_extension``, the program-format defeater, named by the stage and role rules.
- ``pipeline_stage_from_name``, which reads pipeline stages only.
- ``file_role_unless_program``.
- ``name_family_without_stages``, with its two runnable defeaters.
- ``same_name_identity``, which compares every component but the stage words within one folder and
  keeps each number in the place the name wrote it.
- ``filesystem_noise_from_spelling``, which reads how a name is spelled.
- ``acquisition_stage_from_format`` and its defeater ``later_stage_from_name``.
- ``derivation_within_name_identity``, the first proposing rule, which draws within the identity.
- ``doubt_in_a_folder_name`` and ``doubt_to_confirm_in_a_folder_name``, over all 38 terms of the
  negative lexicon, three of them graded low.
- ``replicate_from_indexed_label``, which concludes the sense and the index that a bound label
  states, where two numbers for one sense is a conflict.
- ``stated_count_may_mark_replicates``, which raises a name that states a count as a cardinal on a
  derivation and uncertainly as an ordinal anywhere else.
- ``indexed_label_may_mark_replicates``, the first rule raised about a scope and not about an entry.
  It asks whether a label that the Dictionary misses marks replicates.
- ``content_says_do_not_analyse``, which records a note in a file's own text and obeys nothing.
- ``content_unreadable``, which replaces the stub rule. A file whose content cannot be read is
  raised as empty, dehydrated, a link without a target or no regular file, and nothing is inferred
  about an archive.
- ``installed_software_in_folder``, which raises a folder laid out the way an installed program is.
  The stage, role, unreadable-content, copy and derivation rules name it as a defeater for what lies
  beneath it, so firings inside bundled software are published as conflicting and not hidden. The
  copy rule is declared again for it as ``same_observed_metadata_outside_programs``.
- ``unbound_word_in_a_kinds_place``, which raises a word that nothing binds, standing where words of
  one kind stand in a folder's sibling names, one question per word.
- ``binding_is_ambiguous_outside_programs``, the shipped ambiguity flag declared again to name the
  installed-program defeater. A code identifier beneath a program that happens to spell two senses
  is raised and marked. The Review queue sets a question aside where every file raising it lies
  beneath one, says so, and does not ask a person about it.
"""

from __future__ import annotations

import copy

from ingence.textword.slots import DEFAULTS as WORD_QUESTION_DEFAULTS

__all__ = ["REVISION"]

_QUALITY = {"type": "enum", "choices": ["exact", "case_folded", "loose"]}

#: The senses that mean nobody chose to make an entry, as the shipped noise rule names them.
_NOISE_SENSES = [
    "macos_metadata",
    "windows_metadata",
    "linux_metadata",
    "editor_backup",
    "version_control",
    "language_cache",
    "sync_artifact",
    "archive_helper",
]

#: The stage senses that place a file in a pipeline. The Dictionary's other stage senses are
#: transformations applied at a stage, and one, ``unaffiliated``, is no stage at all.
#:
#: ``pooled`` is a position and not a transformation. An analysis that rests on several independent
#: replicates is a different kind of thing from an analysis of one, and the difference is what a
#: reader of the result needs to know. It sits after ``analysis``.
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

#: The stage senses that place a file past acquisition: the pipeline stages after it, and the
#: transformations that change what was acquired.
#:
#: The test for membership is a word that says the file is not what came off the instrument.
#: ``binned``, ``converted`` and ``corrected`` change the data. ``subset`` and ``annotated`` change
#: what the file holds without changing a pixel, and they are members too. ``calibrated`` stays out
#: while it collides with a calibration file. ``tracked`` and ``split`` are not stage senses.
#: ``tracked`` says that a file is an analysis, and ``split`` is too ambiguous across users.
_LATER_STAGES = [
    "preprocessed",
    "intermediate",
    "processed",
    "analysis",
    "pooled",
    "quantification",
    "summary",
    "report",
    "export",
    "normalised",
    "aligned",
    "filtered",
    "smoothed",
    "denoised",
    "deconvolved",
    "segmented",
    "stitched",
    "cropped",
    "resized",
    "downsampled",
    "upsampled",
    "projected",
    "merged",
    "masked",
    "thresholded",
    "binned",
    "converted",
    "corrected",
    "subset",
    "annotated",
]

_NOT_NOISE_NOR_PLACEHOLDER = ["filesystem_noise_from_spelling", "placeholder_from_entry_kind"]

#: Every format that the Dictionary declares a program (``program: true``), scripts and macros
#: included. A macro named for the movies it processes was once given the role ``movie``, because
#: only four script formats were listed here. A test holds this list to the Dictionary's
#: declarations.
_PROGRAM_FORMATS = [
    "python_source",
    "notebook_format",
    "shell_script",
    "sql_source",
    "python_wheel",
    "python_bytecode",
    "executable_format",
    "c_source",
    "c_header",
    "javascript_source",
    "perl_source",
    "r_source",
    "r_markdown",
    "java_source",
    "java_bytecode",
    "fortran_module",
    "matlab_source",
    "imagej_macro",
    "beanshell_script",
    "clojure_source",
    "ruby_source",
    "groovy_script",
]

#: The rules a program's scope does not defer: each states a physical fact about a file that is
#: as true inside a program as outside it. The facts are its format, that it is a program, that its
#: bytes match another's, that it is a link or a tool's artifact, or that it could not be read.
_TRUE_INSIDE_A_PROGRAM = [
    "program_from_extension",
    "format_from_extension_unless_contradicted",
    "copies_by_sampled_digest",
    "identical_by_content_digest",
    "same_array_shape",
    "same_column_schema",
    "placeholder_from_entry_kind",
    "filesystem_noise_from_spelling",
    "access_failure_is_concentrated",
    "comprehension_failure_is_concentrated",
    # A file's time is read from the filesystem and its own content, as true inside a program as
    # outside it.
    "earliest_credible_time",
]

#: Every term of the negative lexicon, kept whole.
_DOUBT_TERMS = [
    "wrong",
    "bad",
    "broken",
    "invalid",
    "failed",
    "contaminated",
    "do_not_use",
    "ignore",
    "deprecated",
    "obsolete",
    "delete",
    "archive_only",
    "temporary",
    "scratch",
    "test_artifact",
    "debug",
    "dummy",
    "example",
    "final_final",
    "use_this_one",
    "version_soup",
    "emphatic",
    "untitled",
    "default_name",
    "new_folder",
    "keyboard_mash",
    "questionable",
    "unsure",
    "check",
    "fixme",
    "todo",
    "redo",
    "recovered",
    "conflicted",
    "duplicate",
    "excluded",
    "quarantine",
    "superseded_by",
]

#: The three terms that are least trustworthy. They raise a name only beside another term or ask a
#: person to confirm.
_LOW_STRENGTH_DOUBT = ["keyboard_mash", "questionable", "quarantine"]

#: The general-purpose containers that a scientific format is commonly written on. On real
#: laboratory folders, every disagreement between content and extension was one of these: a vendor
#: format on HDF5, a report on PDF, a stream on gzip, a database on OLE storage, an image set on ISO
#: media. Reading them as a contradiction of the extension was wrong all but once.
_CONTAINER_MEDIA_TYPES = [
    "application/gzip",
    "application/pdf",
    "application/x-hdf5",
    "application/x-ole-storage",
    "application/x-tar",
    "application/x-xz",
    "application/x-bzip2",
    "application/vnd.sqlite3",
    "application/x-iso9660-image",
    "application/x-elf",
    "application/x-mach-binary",
    "video/mp4",
    "video/quicktime",
]

#: The container whose use only a laboratory can say. Programs use many ZIP files directly, such as
#: ROI sets and some instruments' image files, so the folder around a ZIP file says more than its
#: format. Ingence raises the ZIP format as a question, which the queue ranks by how many files ask
#: it.
_UNCLEAR_MEDIA_TYPES = ["application/zip"]

#: What a note written as a file name is: one declaration for every rule that reads it. The note
#: rule claims one by these thresholds, and the two rules that leave a note out read the same two
#: parameters, each at this default. Each rule gets a fresh copy, so the written YAML holds no
#: anchors.
_NOTE_THRESHOLDS: list[dict[str, object]] = [
    {
        "name": "minimum_words",
        "type": "integer",
        "default": 4,
        "minimum": 2,
        "maximum": 50,
        "description": (
            "The fewest words a note's name holds. Tuned together in the three rules that read "
            "it: the note rule, content_unreadable and empty_replicate_is_no_data_point."
        ),
    },
    {
        "name": "note_extensions",
        "type": "string_list",
        "default": ["txt", "text", "md", "(none)"],
        "description": (
            "The extensions a note is written with; (none) for none. Tuned together in the three "
            "rules that read it: the note rule, content_unreadable and "
            "empty_replicate_is_no_data_point."
        ),
    },
]


def _note_thresholds() -> list[dict[str, object]]:
    """The note's two thresholds, copied afresh for one rule."""
    return copy.deepcopy(_NOTE_THRESHOLDS)


#: Group resolutions: approving a core set of terms resolves every experiment that shares it.
#: Identical sets are a group per experiment (each day its own) and one across the experiments. A
#: superset or a subset shares a core common to them all, and each word added or missing is its own
#: question, asked first. The rule does not recurse. In version 2, approvals form a lattice of
#: additive derivations (``derivations: lattice``, the default). The earlier reading is available as
#: ``each_deviation``.
_GROUP_RESOLUTION_RULES: list[dict[str, object]] = [
    {
        "id": "group_resolved_within_experiment",
        "version": "3",
        "predicate": "resolved_group_within_experiment",
        "effect": "groups",
        "primitive": "equivalent",
        "respect": "resolved_group_within_experiment",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
        "statement": (
            "Files are one group in their experiment when the experiment holds a set of terms a "
            "person approved (under the lattice, the most specific it holds; under each "
            "deviation, a core exactly or with a few words added or missing); each experiment is "
            "its own group; unless a member is filesystem noise or a placeholder."
        ),
        "rationale": (
            "Approving a core set of terms resolves every occurrence that shares it, and "
            "identical sets become a group per experiment, each day its own. Approval never "
            "merges experiments into one unit, and flows only to what contains the approved "
            "set."
        ),
    },
    {
        "id": "group_resolved_across_experiments",
        "version": "3",
        "predicate": "resolved_group_across_experiments",
        "effect": "groups",
        "primitive": "equivalent",
        "respect": "resolved_group_across_experiments",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
        "statement": (
            "Files are one group across experiments when each experiment {proposal_rule} "
            "proposes over holds a set of terms a person approved, read from {term_source} "
            "(acquisitions being the files {acquisition_rules} concluded on) as words of "
            "{minimum_word_characters} or more characters, holding no digit and not matched to a "
            "sense of kind {ignored_theme_kinds} nor naming a date or a repeat, approvals "
            "spreading as {derivations}: under "
            "the lattice, an experiment holding the approved set, the most specific it holds "
            "naming its group, and the closed sets of {minimum_core_terms} or more terms (at most "
            "{maximum_concepts}) asked most general first (those {minimum_experiments} or more "
            "experiments hold), each derivation once every set it adds to is approved; under each "
            "deviation, identical, holding the core and more, or "
            "holding at least {minimum_core_terms} of its words and no other, the group the terms "
            "they all share, and each word an experiment adds or lacks asked about; unless a "
            "member is filesystem noise or a placeholder."
        ),
        "rationale": (
            "Identical sets are one group across the experiments, and for supersets and "
            "subsets the shared core is concluded as a group common to them all; it does not "
            "recurse into finer groups. For example, a group of four conditions is probably "
            "also a group in another experiment holding those four and a fifth. The two "
            "thresholds are unmeasured defaults. Approval cannot apply to something it does "
            "not contain; ask the most common core first, then its derivations as additions, "
            "none about a missing term; a confirmed derivation flags its own."
        ),
        "parameters": [
            {
                "name": "derivations",
                "type": "enum",
                "choices": ["lattice", "each_deviation"],
                "default": "lattice",
                "description": (
                    "How approvals spread: the lattice asks the most general shared sets first "
                    "and each addition once what it adds to is approved; each deviation "
                    "asks every word an experiment adds to or lacks from an approved core."
                ),
            },
            {
                "name": "minimum_experiments",
                "type": "integer",
                "default": 5,
                "minimum": 2,
                "maximum": 1000,
                "description": (
                    "The fewest experiments a most general shared set is held by for the lattice "
                    "to ask it; additions to an approved set are asked whatever their number."
                ),
            },
            {
                "name": "maximum_concepts",
                "type": "integer",
                "default": 5000,
                "minimum": 1,
                "maximum": 1000000,
                "description": (
                    "The most shared sets of words the lattice keeps; where reached, the "
                    "Groupings page says so rather than cutting silently."
                ),
            },
            {
                "name": "proposal_rule",
                "type": "string",
                "default": "experiments_may_belong_together",
                "description": "The rule whose experiments the cores are resolved over.",
            },
            {
                "name": "term_source",
                "type": "enum",
                "choices": ["folder_and_acquisitions", "folder_and_files", "folder_name"],
                "default": "folder_and_acquisitions",
                "description": (
                    "Where an experiment's terms are read: its folder's name and its "
                    "acquisitions' names (what an instrument wrote), every file's name, or its "
                    "folder's name alone."
                ),
            },
            {
                "name": "acquisition_rules",
                "type": "string_list",
                "default": ["acquisition_stage_from_format"],
                "description": "The rules whose files are an experiment's acquisitions.",
            },
            {
                "name": "minimum_word_characters",
                "type": "integer",
                "default": 2,
                "minimum": 1,
                "maximum": 50,
                "description": "The shortest word that is a term (WT is one).",
            },
            {
                "name": "ignored_theme_kinds",
                "type": "string_list",
                # A connecting word is bound so that no rule reads it as a subject. Measured on a
                # copy of a real folder, the lattice asked one as an addition.
                "default": ["stage", "replicate", "connective"],
                "description": "A word matched to a sense of these kinds is not a term.",
            },
            {
                "name": "minimum_core_terms",
                "type": "integer",
                "default": 2,
                "minimum": 1,
                "maximum": 50,
                "description": "The fewest terms a shared core keeps.",
            },
        ],
    },
    {
        "id": "group_series_across_experiments",
        "version": "1",
        "predicate": "resolved_group_across_experiments",
        "effect": "groups",
        "primitive": "equivalent",
        "respect": "approved_group_series",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
        "statement": (
            "Files are one series across experiments when each experiment holds a set of terms a "
            "person approved, the {scale} set it holds naming the series, so a day holding an "
            "approved derivation stays in its core's series beside its derivation's group; under "
            "the lattice only; unless a member is filesystem noise or a placeholder."
        ),
        "rationale": (
            "Groups sit at scales, a day's replicates and an experiment series across days, "
            "and approval never merges experiments into one unit. A grouping rule keys a file "
            "once, so the series is a rule of its own beside the group of the most specific "
            "set."
        ),
        "parameters": [
            {
                "name": "scale",
                "type": "enum",
                "choices": ["most_specific", "most_general"],
                "default": "most_general",
                "description": "Which approved set an experiment holds names its series.",
            },
        ],
    },
    {
        "id": "group_resolution_deviation",
        "version": "3",
        "predicate": "group_resolution_deviation",
        "effect": "flags",
        "tier": "defeasible",
        "level": "folder",
        "defeated_by": ["installed_software_in_folder"],
        "statement": (
            "An experiment's folder is raised with each group question it holds, asked first "
            "where {asked_first}: under the lattice, each shared set of words asked now (a most "
            "general set, or an addition to sets a person approved); under each deviation, each "
            "word it adds to, or misses from, a core set of terms a person approved and the "
            "experiment otherwise shares, one question per word and core; unless the folder is "
            "laid out as an installed program."
        ),
        "rationale": (
            "Each deviation, a word added or a word missing, is left as its own group for "
            "confirmation; and a group is asked again only when new files raise a new "
            "question, a plain new replicate joining its group unasked. A deviation is one "
            "answer that settles a group, so it leads the queue. The most common core first, "
            "then derivations as additions."
        ),
        "parameters": [
            {
                "name": "asked_first",
                "type": "boolean",
                "default": True,
                "description": "Whether its questions lead the list of questions.",
            },
        ],
    },
]


#: Derivations looked for within an experiment: the name identity compared within the experiment a
#: file lies in, across its stage subfolders. The derivation within a name identity draws its pairs
#: from this comparison. The block is removed whole, and ``derivation_within_name_identity`` then
#: draws within ``same_name_identity`` again.
_DERIVATION_SCOPE_RULES: list[dict[str, object]] = [
    {
        "id": "same_name_identity_within_experiment",
        "version": "1",
        "predicate": "same_component_values",
        "effect": "groups",
        "primitive": "equivalent",
        "respect": "name_identity",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
        "statement": (
            "Two files share a name identity within an experiment when, compared within one "
            "{compared_within} -- the experiment the experiment rules conclude a file lies in, "
            "across its subfolders, where the folders between agree on their names but for a word "
            "or an indexed label matched to a sense of kind {ignored_subfolder_theme_kinds}, and "
            "one "
            "folder for a file beneath no experiment -- they agree on every component of kind "
            "{component_kinds}, in the same places for a component of kind "
            "{ordered_component_kinds}, leaving out a word or an indexed label matched to a sense "
            "of "
            "kind {ignored_theme_kinds}, and each carries {minimum_components} or more of them, "
            "unless a member is filesystem noise or a placeholder."
        ),
        "rationale": (
            "One experiment's chain is commonly split across its stage subfolders -- raw, "
            "processed, analysis -- and an identity kept to one folder never drew such a pair "
            "(the evaluation generator). A stage subfolder's name is left out as a "
            "stage word is left out of a file's, and every other folder between the experiment "
            "and the file keeps the identity apart, so two replicate days holding the same names "
            "are never one identity. The identity within one folder stays as it was, and a "
            "person's verdicts on its classes with it."
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
                "description": (
                    "A word or indexed label in a folder between the experiment and the file, "
                    "matched to a sense of these kinds, is left out, so a stage subfolder is "
                    "no place of its own. Replicates are not left out here: a replicate's "
                    "folder keeps its files apart from another replicate's."
                ),
            },
            {
                "name": "component_kinds",
                "type": "string_list",
                "default": ["word", "number", "indexed", "quantity", "date", "version"],
                "description": "Every kind of name component but the extension.",
            },
            {
                "name": "ignored_theme_kinds",
                "type": "string_list",
                "default": ["stage", "replicate"],
                "description": "As the identity within one folder leaves them out.",
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
                "description": "As the identity within one folder keeps their places.",
            },
        ],
    },
]


#: A derivation from the acquisition a file is named for. On real laboratory folders, no name
#: identity held both an acquisition and its analysis, so the derivation within a name identity
#: found nothing. This block groups files by the acquisition that each name holds whole, and the
#: derivation within a name identity draws within that grouping. It is a block of its own, so it is
#: removed whole.
_NAMED_FOR_AN_ACQUISITION_RULES: list[dict[str, object]] = [
    {
        "id": "named_for_an_acquisition",
        "version": "2",
        "predicate": "named_for_an_acquisition",
        "effect": "groups",
        "primitive": "equivalent",
        "respect": "acquisition_named",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
        "statement": (
            "An acquisition and every file named for it are one group, a file being named for an "
            "acquisition where its name holds the acquisition's name whole, of "
            "{minimum_key_characters} or more letters and digits, one of them a letter, looked "
            "for in the file's experiment (its folder where it lies beneath none), the nearest "
            "where several share the name and none where two are equally near, and the longest "
            "held where a name holds several; and a file whose whole name, stem and extension, "
            "of as many letters and digits, another file's name holds, with every file named for "
            "it, is such a group of its own where neither reaches an acquisition, the whole name "
            "looked for in the experiment and among the folders beside the file's; and a file "
            "named as an image in its folder is, with one word at either end matched to one of "
            "{annotation_senses}, named for that image, a file of a format in {image_formats}, "
            "whatever its stage; a whole name found nowhere there is looked for upward, the "
            "nearest folder up to {whole_name_levels_up} above the file's own, below the pool's "
            "first level, holding exactly one file of that name beneath it giving the source, "
            "one of another experiment only where its experiment folder agrees in name with a "
            "folder between the two, which also breaks a tie, and a name sought there only where "
            "its stem has as many letters and digits; never a backup, a file whose "
            "extension, or whose words after "
            "the name it holds in that file's own format, are spelled only by "
            "{not_made_from_senses}; unless a member is filesystem noise or a placeholder."
        ),
        "rationale": (
            "A tool names what it writes after the movie it read, with words of its own "
            "beside it, and a name identity cannot join the two because those words are not "
            "stage words, so no identity class holds an acquisition and its analysis "
            "together. The experiment rules already match an acquisition's name held "
            "whole; this reads the same match per file. An image viewer appends to the whole "
            "name of what it opened, extension included (a channel split named '<movie>.tif - "
            "C=0.tif'), and the file opened need not be an instrument's own format, nor lie "
            "in the analysis file's folder, so such files form no group otherwise. "
            "And a label, mask or annotation is made from the image it is named for, whatever "
            "the image's stage (machine-learning datasets do this constantly); the derivation "
            "wanted an acquisition at the source, so a volume and its labels formed nothing."
        ),
        "parameters": [
            {
                "name": "minimum_key_characters",
                "type": "integer",
                "default": 6,
                "minimum": 1,
                "maximum": 200,
                "description": (
                    "The fewest letters and digits an acquisition's name needs before another "
                    "file is matched on it, as the experiment rules have it: 01.nd2 names nothing."
                ),
            },
            {
                "name": "whole_name_levels_up",
                "type": "integer",
                "default": 4,
                "minimum": 0,
                "maximum": 20,
                "description": (
                    "How many folders above its own a file's held whole name is looked for, "
                    "where its experiment and the folders beside it hold none: the nearest "
                    "folder holding exactly one file of that name beneath it gives the "
                    "source, never the pool's root. A raw branch and an analysis branch side "
                    "by side meet a few folders up; 4 reaches a project's top from a day "
                    "folder's subfolder (unmeasured); 0 turns the search off."
                ),
            },
            {
                "name": "not_made_from_senses",
                "type": "string_list",
                "default": ["backup", "kept_original", "copy"],
                "description": (
                    "A file named for another whose own extension is spelled only by these "
                    "senses (hela_table.csv.bak, dapi_fit.py.orig, track_dmso.py.old), or "
                    "whose words after the name it holds all are while it keeps that file's "
                    "format (hela_041_old.nd2), is a backup of it and forms no derivation; "
                    "the copy rules read it. A copy in another format (hela_041_copy.tif) is "
                    "a conversion, made from it."
                ),
            },
            {
                "name": "annotation_senses",
                "type": "string_list",
                "default": ["annotation", "annotated", "masked"],
                "description": (
                    "The senses a word means an annotation by, read from the Dictionary: the "
                    "annotation role (labels, rois), the annotated transformation and a mask. "
                    "A segmentation is a processed image and is left out."
                ),
            },
            {
                "name": "image_formats",
                "type": "string_list",
                "default": ["image_format", "imaging_format"],
                "description": (
                    "The formats an annotated image may be in: the image type, and the volumes "
                    "the clinical pack reads (.nii, .nii.gz)."
                ),
            },
        ],
    },
    {
        "id": "derivation_named_for_an_acquisition",
        "version": "6",
        "predicate": "derives_from_named_acquisition",
        "effect": "proposes",
        "primitive": "derives",
        "specialization": "processed_from",
        "tier": "defeasible",
        "level": "file",
        "candidates_within": "named_for_an_acquisition",
        "defeated_by": ["installed_software_in_folder"],
        "statement": (
            "A file whose name holds an acquisition's name whole, or another file's whole file "
            "name, stem and extension, is proposed as processed from that acquisition or file, or "
            "from the file of its group whose whole file name its name holds, whatever stage "
            "either states, never from another file "
            "named for it by time alone; never where a file of its own class of "
            "{link_by_link_within}, in another folder, was made between the two by more than "
            "{tolerance_seconds} seconds "
            "(that grouping's rule proposes the link); a step in {stage_transitions} "
            "corroborating it where both state one; and the pair is blocked where either file "
            "lies in a folder laid out as an installed program."
        ),
        "rationale": (
            "A tool names what it writes after the movie it read, so the name held whole says "
            "which acquisition a file was made from and which way. All derivations come "
            "before stage assignment. The direction is the name's, never a stage word's; two "
            "outputs of one acquisition are siblings unless other evidence joins them, so "
            "each is proposed from the acquisition, one pair a file, and a chain a name "
            "identity joins across folders stays link by link. A file whose whole name "
            "another's holds is the source whatever it is, an instrument's acquisition or "
            "not, since the whole name with its extension is the evidence."
        ),
        "parameters": [
            {
                "name": "link_by_link_within",
                "type": "string",
                "default": "same_name_identity_within_experiment",
                "description": (
                    "The grouping whose class, holding a file made between the acquisition and "
                    "the target in another folder, leaves the pair to that grouping's own link; "
                    "blank proposes every file from its acquisition."
                ),
            },
            {
                "name": "tolerance_seconds",
                "type": "integer",
                "default": 60,
                "minimum": 0,
                "maximum": 86400,
                "description": (
                    "How far apart two files' earliest credible times must be before the earlier "
                    "is read as made first."
                ),
            },
            {
                "name": "stage_transitions",
                "type": "mapping",
                "default": {
                    "raw": "processed",
                    "acquisition": "processed",
                    "processed": "analysis",
                    "analysis": "quantification",
                    "quantification": "summary",
                },
                "description": (
                    "Which stage may follow which: a step agreeing with the derivation "
                    "corroborates it, and none is needed."
                ),
            },
        ],
    },
]


#: The groupings whose rules read a pair of one name in sibling folders first: a name identity
#: within an experiment, and every grouping of copies.
_JOINED_ELSEWHERE = [
    "same_name_identity_within_experiment",
    "identical_by_content_digest",
    "copies_by_sampled_digest",
    "identical_by_declared_digest",
    "identical_by_declared_sha256",
    "copies_a_person_answered",
    "archive_link_copy_of_a_file",
]

#: The senses that keep two sibling folders apart: a learning dataset's splits, and an experiment's
#: conditions (control and treated, wild type and knockout). Each numbers different things alike.
_APART_FOLDER_SENSES = ["cross_validation", "control", "treatment", "wild_type", "knockout"]

#: The roles that describe a folder and do not hold its data. A README in each of two sibling
#: folders is each folder's own and never one thing held twice.
_FOLDER_DESCRIBING_ROLES = ["readme", "licence", "changelog", "notes", "metadata"]

#: The derivations that names beside one another give. A table named for a series of numbered files
#: beside it summarizes them. Files of one whole name in sibling folders are about one thing,
#: directed where the folders' own words say which holds the source. It is a block of its own, so it
#: is removed whole.
_NAMED_BESIDE_RULES: list[dict[str, object]] = [
    {
        "id": "named_for_a_series",
        "version": "1",
        "predicate": "named_for_a_series",
        "effect": "groups",
        "primitive": "equivalent",
        "respect": "series_summarised",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
        "statement": (
            "A series of files in one folder whose names agree but for a number at their end, "
            "{minimum_series_members} or more of them, and every table of a format in "
            "{table_formats} in that folder whose name is the series' shared name with one word, "
            "at either end, matched to one of {summary_senses}, are one group; unless a member is "
            "filesystem noise or a placeholder."
        ),
        "rationale": (
            "A results workbook is named for the movies it summarizes (hela_results.xlsx "
            "beside hela_001.nd2 to hela_010.nd2), and no name identity joins them, since the "
            "workbook's name has no number and the movies' have no word. The word that says a "
            "table holds results is read from the Dictionary's senses."
        ),
        "parameters": [
            {
                "name": "minimum_series_members",
                "type": "integer",
                "default": 3,
                "minimum": 2,
                "maximum": 1000,
                "description": (
                    "The fewest numbered files that make a series: two are a pair, and two "
                    "outputs stay siblings."
                ),
            },
            {
                "name": "summary_senses",
                "type": "string_list",
                "default": ["analysis", "summary", "quantification"],
                "description": (
                    "The senses a word says a table holds results by: results, summary, "
                    "measurements and their spellings in the Dictionary."
                ),
            },
            {
                "name": "table_formats",
                "type": "string_list",
                "default": ["tabular_format"],
                "description": "The formats a table summarizing a series is in.",
            },
        ],
    },
    {
        "id": "derivation_summarising_a_series",
        "version": "1",
        "predicate": "derives_into_a_series_summary",
        "effect": "proposes",
        "primitive": "derives",
        "specialization": "processed_from",
        "tier": "defeasible",
        "level": "file",
        "candidates_within": "named_for_a_series",
        "defeated_by": ["installed_software_in_folder"],
        "statement": (
            "A table named for a series of numbered files beside it is proposed as made from each "
            "file of the series, one pair a file, never one file of the series from another, and "
            "the pair is blocked where either file lies in a folder laid out as an installed "
            "program."
        ),
        "rationale": (
            "The table summarizes the series, so each file of it is one of its sources; one pair "
            "a file is what a proposal and the Graph can show, and the files of the series are "
            "siblings."
        ),
        "parameters": [],
    },
    {
        "id": "one_name_in_sibling_folders",
        "version": "1",
        "predicate": "one_name_in_sibling_folders",
        "effect": "groups",
        "primitive": "equivalent",
        "respect": "one_name_beside",
        "tier": "defeasible",
        "level": "file",
        "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
        "statement": (
            "Files of one whole name in folders of one parent, {maximum_folders} folders or "
            "fewer, are one group, each folder whose own words are matched to one of "
            "{source_folder_senses} and none of {product_folder_senses} said to hold sources, and "
            "the reverse products; two files whose folders say neither are paired only where the "
            "name holds a number or a word no sense matches; never two folders whose names agree "
            "but for their numbers and their words matched to {apart_folder_senses}, nor two "
            "experiments, nor a file of a role in {ignored_roles}; unless a member is filesystem "
            "noise or a placeholder."
        ),
        "rationale": (
            "A dataset keeps an image and its label under one name in two folders "
            "(images/a.tif, masks/a.tif), which a name identity within one folder never joins; "
            "this widens it to sibling folders only, never the whole tree. "
            "A name in many sibling folders is the layout's common name (each sample's "
            "image.tif), not one thing held twice."
        ),
        "parameters": [
            {
                "name": "maximum_folders",
                "type": "integer",
                "default": 4,
                "minimum": 2,
                "maximum": 1000,
                "description": (
                    "The most sibling folders one name may lie in and form a group: a "
                    "dataset's images, labels, masks and predictions are four; a name in more "
                    "is each sample's own."
                ),
            },
            {
                "name": "apart_folder_senses",
                "type": "string_list",
                "default": _APART_FOLDER_SENSES,
                "description": (
                    "Two sibling folders whose names agree but for their numbers and words "
                    "bound to these senses hold different things numbered alike, and no file "
                    "of one is paired with a file of the other: a learning dataset's train, "
                    "test and validation splits, and an experiment's conditions."
                ),
            },
            {
                "name": "ignored_roles",
                "type": "string_list",
                "default": _FOLDER_DESCRIBING_ROLES,
                "description": (
                    "The roles of a file that describes its folder rather than holding its data: "
                    "a README in each of two sibling folders is each folder's own (the evaluation "
                    "generator's pool)."
                ),
            },
            {
                "name": "source_folder_senses",
                "type": "string_list",
                "default": ["raw", "acquisition", "image", "input_data"],
                "description": (
                    "The senses a folder's words say it holds what came first by: raw, recorded, "
                    "images, input."
                ),
            },
            {
                "name": "product_folder_senses",
                "type": "string_list",
                "default": [
                    "annotation",
                    "annotated",
                    "masked",
                    "segmented",
                    "reference_data",
                    "analysis",
                    "processed",
                ],
                "description": (
                    "The senses a folder's words say it holds what was made from them by: "
                    "labels, masks, segmentations, ground truth, output, results and "
                    "processed."
                ),
            },
        ],
    },
    {
        "id": "derivation_of_one_name_in_sibling_folders",
        "version": "1",
        "predicate": "derives_within_one_name_beside",
        "effect": "proposes",
        "primitive": "derives",
        "specialization": "processed_from",
        "tier": "defeasible",
        "level": "file",
        "candidates_within": "one_name_in_sibling_folders",
        "defeated_by": ["installed_software_in_folder"],
        "statement": (
            "Of two files of one name in sibling folders, the one in a folder whose words say it "
            "holds products is proposed as made from the one in a folder whose words say it holds "
            "sources, this rule proposing {direction} pairs only, never where both files share a "
            "class of {unless_joined_by}, and the pair is blocked where either file lies in a "
            "folder laid out as an installed program."
        ),
        "rationale": (
            "The folders' own words give the direction where they can: an image is the source of "
            "the mask or label of its name. Two files of one name identity within "
            "their experiment are left to that identity's rules, which read their clocks."
        ),
        "parameters": [
            {
                "name": "direction",
                "type": "enum",
                "default": "directed",
                "choices": ["directed", "undirected"],
                "description": "The pairs the folders' words direct.",
            },
            {
                "name": "unless_joined_by",
                "type": "string_list",
                "default": _JOINED_ELSEWHERE,
                "description": (
                    "A pair whose ends share a class of one of these groupings is that grouping's "
                    "rules': a name identity within an experiment, whose rules read the clocks, "
                    "and the copy rules, since two copies of one file are copies before they are "
                    "related; empty proposes it here too."
                ),
            },
        ],
    },
    {
        "id": "related_by_one_name_in_sibling_folders",
        "version": "1",
        "predicate": "derives_within_one_name_beside",
        "effect": "proposes",
        "primitive": "concerns",
        "tier": "defeasible",
        "level": "file",
        "candidates_within": "one_name_in_sibling_folders",
        "defeated_by": ["installed_software_in_folder"],
        "statement": (
            "Two files of one name in sibling folders whose folders' words give no direction are "
            "proposed once as related, with neither said to be made from the other, this rule "
            "proposing {direction} pairs only, never where both files share a class of "
            "{unless_joined_by}, and the pair is blocked where either file lies in a folder laid "
            "out as an installed program."
        ),
        "rationale": (
            "Where nothing says which of two files of one name was made from the other "
            "(left/a.tif, right/a.tif), the pair stays undirected, a person's Related, so the "
            "Graph's tree view can show it."
        ),
        "parameters": [
            {
                "name": "direction",
                "type": "enum",
                "default": "undirected",
                "choices": ["directed", "undirected"],
                "description": "Neither file as the source.",
            },
            {
                "name": "unless_joined_by",
                "type": "string_list",
                "default": _JOINED_ELSEWHERE,
                "description": "As the derivation of one name in sibling folders reads it.",
            },
        ],
    },
]


#: A derivation that a script's own calls give: a script reads one file by a literal name and writes
#: another. This block also declares again what a derivation's ends may be (the shipped rule is
#: superseded in ``changes``). An end is never a program, and no stage or role is asked of either
#: end. It is a block of its own, so it is removed whole with that one change.
_SCRIPT_CALL_RULES: list[dict[str, object]] = [
    {
        "id": "derivation_endpoints_hold_roles_or_a_scripts_calls",
        "version": "2",
        "predicate": "endpoint_is_not_of_an_excluded_class",
        "effect": "constraint",
        "primitive": "derives",
        "tier": "entailed",
        "level": "file",
        "statement": (
            "A derivation is considered only where neither end is of a class in {excluded_classes}."
        ),
        "rationale": (
            "All derivations come before stage assignment. A program performs a step and is "
            "neither what the step read nor what it made (raw data is not derived from a "
            "python script, nor the reverse)."
        ),
        "parameters": [
            {
                "name": "excluded_classes",
                "type": "string_list",
                "default": _PROGRAM_FORMATS,
                "description": (
                    "The classes never at either end of a derivation: every format the "
                    "Dictionary declares a program, as the program rule concludes them."
                ),
            },
        ],
    },
    {
        "id": "derivation_by_a_scripts_calls",
        "version": "1",
        "predicate": "derives_from_script_calls",
        "effect": "proposes",
        "primitive": "derives",
        "specialization": "processed_from",
        "tier": "defeasible",
        "level": "file",
        "candidates_within": "co_mentioned",
        "defeated_by": ["installed_software_in_folder"],
        "statement": (
            "A file is proposed as processed from another where one script of type "
            "{describing_formats} reads the other and writes it, by literal names its calls "
            "state, each resolved at {minimum_resolution} or better within the script's own folder "
            "or experiment, and the pair is blocked where either lies in a folder laid out as an "
            "installed program."
        ),
        "rationale": (
            "A script's calls are testimony written at the time, and they say which file was "
            "read and which written, which a mention found in its text cannot; so the "
            "direction is the script's own and never a stage word's. It states what the "
            "script would do, not what a run did, so it proposes. A bare name found by name "
            "in the script's experiment is weak evidence for a reference and is admitted "
            "here, where the script's two calls must both hold."
        ),
        "parameters": [
            {
                "name": "describing_formats",
                "type": "string_list",
                "default": ["python_source", "matlab_source", "imagej_macro"],
                "description": (
                    "The script types whose calls are read: the languages the dive reads calls in."
                ),
            },
            {
                "name": "minimum_resolution",
                "type": "enum",
                "default": "unique_basename",
                "choices": ["exact", "unique_suffix", "unique_basename"],
                "description": (
                    "How firmly each name must have resolved: beside the script is exact, and "
                    "found by name among its experiment's files is unique_basename."
                ),
            },
        ],
    },
]


#: What reads the derivations, after them. All derivations come before stage assignment. A file
#: whose own stage disagrees with a derivation proposed into it is raised for a person. It is a
#: block of its own, so it is removed whole.
_STAGE_AFTER_DERIVATION_RULES: list[dict[str, object]] = [
    {
        "id": "stage_disagrees_with_a_derivation",
        "version": "2",
        "predicate": "stage_disagrees_with_a_derivation",
        "effect": "flags",
        "tier": "defeasible",
        "level": "file",
        "statement": (
            "A file is raised where it was proposed as made from another and the latest stage its "
            "own name or format states is earlier, by {stage_transitions}, than the latest its "
            "source states, or is where data begins while its source states none, one question "
            "for each derivation rule, pair of stages and folder."
        ),
        "rationale": (
            "A file made from another is later than it. The derivation is found from its own "
            "evidence and the stage from the name or the format, and where the two disagree a "
            "person is asked, rather than either being settled without a word."
        ),
        "parameters": [
            {
                "name": "stage_transitions",
                "type": "mapping",
                "default": {
                    "raw": "processed",
                    "acquisition": "processed",
                    "processed": "analysis",
                    "analysis": "quantification",
                    "quantification": "summary",
                },
                "description": "Which stage follows which, as the derivations read them.",
            },
        ],
    },
]


#: The copy rule, fixed in the shipped book. Version 1 names ``blake2b``, a digest that no method
#: computes, so it never groups anything. This block declares the rule again at version 2, as the
#: book changes a rule. It names the whole-file digest that a dive computes wherever two files'
#: samples agree (``blake3_whole``), so a dive finds copies in a new project.
_COPIES_BY_WHOLE_DIGEST: dict[str, object] = {
    "id": "identical_by_content_digest",
    "version": "2",
    "predicate": "same_content_digest",
    "effect": "groups",
    "primitive": "identical",
    "tier": "entailed",
    "level": "file",
    "statement": (
        "Two items hold the same bytes when their {digest_algorithm} digests over the whole "
        "content agree."
    ),
    "rationale": (
        "The only substitutive relation the engine has, so everything that counts a pool's real "
        "size or treats one file as a stand-in for another rests on it; agreement on a full "
        "digest is a fact rather than an inference, which is why this is the one grouping at the "
        "entailed level. The digest named is the one a dive computes."
    ),
    "parameters": [
        {
            "name": "digest_algorithm",
            "type": "string",
            "default": "blake3_whole",
            "description": (
                "Which digest settles it: the whole-file digest a dive computes wherever two "
                "files' samples agree. Recorded on the conclusion rather than assumed, so that a "
                "pool hashed under one algorithm and compared under another is a detectable state "
                "rather than a silent mismatch."
            ),
        }
    ],
}


#: The rule that orders a series of replicates, declared again at version 2. The shipped rule named
#: no index to draw pairs within, so it could never be handed a pair and proposed nothing. This one
#: draws within the replicates of one condition and pairs only neighbours. It puts a set in order
#: only when exactly one counting part of its names varies. A set whose names differ in two parts,
#: whose numbers tie or whose number does not parse is not put in order at all
#: (``rulebook/predicates/series.py``).
_SUCCEEDS_IN_A_SERIES: dict[str, object] = {
    "id": "succeeds_by_series_index",
    "version": "2",
    "predicate": "succeeds_by_ordered_token",
    # Shipped off. Whether a replicate's number is an order is a person's to say: an instrument's
    # own numbering is the order files were made in, and a label somebody typed often names
    # replicates made side by side. A project turns it on with a change file,
    # ``enable: succeeds_by_series_index``, in its ``rulebook`` folder.
    "enabled": False,
    "effect": "proposes",
    "primitive": "succeeds",
    "tier": "defeasible",
    "level": "file",
    "candidates_within": "replicates_of_one_condition",
    "statement": (
        "One entry follows another in a series when both are replicates of one condition, the "
        "two are neighbours once the replicates are put in order by their one counting part "
        "({ordering_kind}), and the numbers count and compare as numbers; replicates whose "
        "names differ in more than one part, whose numbers tie, do not count or run against the "
        "files' own clocks by more than {tolerance_seconds} seconds are not put in order. "
        "Shipped off; a project turns it on."
    ),
    "rationale": (
        "An index is somebody counting, and the count is the order; recording it as succession "
        "rather than as derivation matters because the second replicate is not made from the "
        "first. Only neighbours are paired, so ten replicates make nine pairs and the pairs "
        "close to the total order a series has. A set that cannot be put in order says why and "
        "proposes nothing, because a wrong order looks exactly like a right one. A dose ladder "
        "is never a series: a dose is a condition, and replicates hold their doses fixed. A "
        "number orders its set only where the numbers count (001, 002; not 488, 561 or 1, 10, "
        "100), and a set whose files' own clocks run against the numbers is not put in order. "
        "The rule is shipped off, because whether a replicate's number is an order in time is "
        "for a person to decide."
    ),
    "parameters": [
        {
            "name": "ordering_kind",
            "type": "string",
            "default": "indexed",
            "description": (
                "The component kind that carries the order. Indexed counts by a running number "
                "written alone (001, 002) and by an indexed label (rep1, rep2), where the numbers "
                "count; where they tie, are no numbers or do not count (488, 561), the set "
                "abstains."
            ),
        },
        {
            "name": "tolerance_seconds",
            "type": "integer",
            "default": 60,
            "minimum": 0,
            "maximum": 86400,
            "description": (
                "How far the files' own clocks (an instrument's or a document's) may run against "
                "the order the numbers give before the set is not put in order."
            ),
        },
    ],
}


#: Tables with the same columns, and arrays of the same shape, declared at version 2. Version 1 said
#: that the tables' guessed column types agreed, which the digest never held. Types are a guess over
#: the first rows, and a type in the key would split one export in two on which rows were read. The
#: digest holds the column names, their order and the delimiter, and the sentence says so. The array
#: digest holds the shape, the element type, the storage order and the number of dimensions. Both
#: name the two defeaters that the other file-level groupings name, so their classes publish.
_SAME_COLUMNS: dict[str, object] = {
    "id": "same_column_schema",
    "version": "2",
    "predicate": "same_extracted_shape",
    "effect": "groups",
    "primitive": "equivalent",
    "specialization": "same_schema_as",
    "tier": "defeasible",
    "level": "file",
    "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
    "statement": (
        "Two tables have the same columns when their column names, in the same order, and "
        "their delimiter agree, and each table's number of columns is at least "
        "{minimum_columns}. The kinds of values in the columns are not compared."
    ),
    "rationale": (
        "Column names are independent of every naming convention, so this grouping still holds "
        "on a pool whose vocabulary the Dictionary has never seen. Two tables with the same "
        "columns often come from one export or one analysis, and they can also come from "
        'unrelated studies that used the same program. The grouping says "same columns" and '
        "nothing about which experiment a table belongs to. Column types are left out because "
        "they are a guess over the first rows, and one export would split in two on which rows "
        "were sampled."
    ),
    "parameters": [
        {
            "name": "minimum_columns",
            "type": "integer",
            "default": 2,
            "minimum": 1,
            "maximum": 1000,
            "description": (
                "How many columns a table needs before its columns are treated as a signal. A "
                "single-column table matches many tables by accident."
            ),
        }
    ],
}

_SAME_ARRAY_SHAPE: dict[str, object] = {
    "id": "same_array_shape",
    "version": "2",
    "predicate": "same_extracted_shape",
    "effect": "groups",
    "primitive": "equivalent",
    "specialization": "same_shape_as",
    "tier": "defeasible",
    "level": "file",
    "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
    "statement": (
        "Two stored arrays have the same shape when their shape, element type and storage "
        "order agree, and each array's number of dimensions is at least {minimum_dimensions}."
    ),
    "rationale": (
        "A shared shape and element type is evidence of a shared acquisition setting or a "
        "shared processing step, and it holds when a file has been renamed by somebody who did "
        "not know what it was. It is not evidence of one experiment: two unrelated runs can "
        "write arrays of one shape."
    ),
    "parameters": [
        {
            "name": "minimum_dimensions",
            "type": "integer",
            "default": 2,
            "minimum": 1,
            "maximum": 16,
            "description": (
                "How many dimensions an array needs. Two, because a single length is a "
                "coincidence rather than a shape."
            ),
        }
    ],
}


REVISION: dict[str, object] = {
    "revision": 4,
    "summary": (
        "The rules developed since the first four revisions: the proposing rules and the defeat "
        "conditions they wait on, the group, derivation, word and pack questions, programs as a "
        "scope every rule defers beneath, and copies by the digest a dive computes."
    ),
    # Every rule defers beneath an installed program, flags included. One declaration replaces an
    # edit per rule, so a rule added later defers without anybody remembering to say so. Each rule
    # it reaches names the scope as a defeater and becomes a version of its own (``+d1``). The rules
    # listed state a physical fact.
    "defer_beneath": [
        {
            "scope": "installed_software_in_folder",
            "except": _TRUE_INSIDE_A_PROGRAM,
            "rationale": (
                "A copy of Fiji is one thing, a program, and nothing inside it is an experiment's "
                "data: its jars state no replicate count, its folders no doubt, its license files "
                "no role. Run two raised seven rules' findings inside one; only the rules stating "
                "a physical fact -- a format, a digest, a link -- read the same inside as outside."
            ),
        }
    ],
    "changes": [
        {"supersede": "stage_from_name"},
        {"supersede": "file_role_from_name"},
        {"supersede": "same_name_family"},
        # Noise is read from how a name is spelled, which the census keeps as no component.
        {"supersede": "filesystem_noise_from_name"},
        {"supersede": "raw_data_from_acquisition_format"},
        {"supersede": "projection_derives_from_acquisition"},
        # Revision 0's doubt rule names no terms and so never runs. These two name them all.
        {"supersede": "authored_name_doubt"},
        # Declared again naming the installed-software defeater, which also makes its classes
        # publish, as a defeasible rule that names a defeater does.
        {"supersede": "same_observed_metadata"},
        # Declared again naming it too: a code identifier inside a program is not a term that a
        # laboratory uses, and a question raised only there is set aside and not asked.
        {"supersede": "binding_is_ambiguous"},
        # A container that a format is written on is not a contradiction of the extension, and only
        # a contradiction retracts a format claim. The reading splits in two, so the format rule is
        # declared again naming the half that retracts it.
        {"supersede": "extension_contradicts_content"},
        {"supersede": "format_from_extension"},
        # A folder whose words nobody taught the engine is a program's folder more often than a
        # laboratory's, and the rule says so.
        {"supersede": "vocabulary_unread_in_folder"},
        # A copy claim rests on content, and the whole-file digest that the engine computes is
        # BLAKE3's. The rule is declared again below at version 2 (``_COPIES_BY_WHOLE_DIGEST``). The
        # two reference rules shipped with no index, so they could never be handed a pair. They are
        # declared again drawing on reference resolution, which is the index that a mention gives.
        {"supersede": "references_a_declared_output"},
        {"supersede": "derives_from_declared_reference"},
        # A script's calls are roles too. No role or stage is asked of either end, only that neither
        # is a program (``_SCRIPT_CALL_RULES``).
        {"supersede": "derivation_endpoints_hold_eligible_roles"},
        # No group is formed by dates or replicate numbers. A key made of a name's indexed labels
        # alone is the identifier (``s1`` in every experiment is one class). One acquisition's files
        # are grouped by the rules that read its name.
        {"supersede": "same_acquisition_event"},
        # No group is formed by dates or replicate numbers. A version family's key is every part of
        # a folder's name but its version marker, so two folders that differ only by a date or a
        # count are never one family.
        {
            "override": "same_version_family",
            "parameters": {
                "component_kinds": ["word", "quantity", "date", "number", "indexed"],
            },
        },
    ],
    "rules": [
        _COPIES_BY_WHOLE_DIGEST,
        _SUCCEEDS_IN_A_SERIES,
        _SAME_COLUMNS,
        _SAME_ARRAY_SHAPE,
        {
            "id": "program_from_extension",
            "version": "3",
            "predicate": "class_from_bound_sense",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["content_contradicts_extension"],
            "statement": (
                "A file whose {slot} matches one of {senses} at quality "
                "{minimum_match_quality} or better, is a program, unless its bytes contradict "
                "its extension."
            ),
            "rationale": (
                "A script named analysis performs an analysis and is not the analysis stage of "
                "any data, and a script named for a figure makes one rather than being one; the "
                "extension says which, and it is the match a format is read from, so the same "
                "content signature retracts it."
            ),
            "parameters": [
                {
                    "name": "senses",
                    "type": "string_list",
                    "default": _PROGRAM_FORMATS,
                    "description": (
                        "The format senses that mean a program: every one the Dictionary "
                        "declares a program, scripts and macros among them, so "
                        "a macro named for the movies it processes is never read as a movie. "
                        "Named as senses, so a laboratory whose scripts are written in "
                        "something else extends the Dictionary."
                    ),
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
            "version": "2",
            "predicate": "sense_from_name_tokens",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["program_from_extension", "installed_software_in_folder"],
            "statement": (
                "A file sits at the pipeline stage its name states, where the name's free words "
                "reach {minimum_senses} or more distinct senses of kind {theme_kind} among "
                "{senses}, unless the file is a program or lies in a folder laid out as an "
                "installed program."
            ),
            "rationale": (
                "Every field that produces data has a raw form, an intermediate form and a "
                "result, and the words for those are shared across all of them; a transformation "
                "applied at a stage says what was done to a file and not where it sits, and a "
                "program that names a stage performs it rather than sitting at it."
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
            ],
        },
        {
            "id": "file_role_unless_program",
            "version": "2",
            "predicate": "sense_from_name_tokens",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["program_from_extension", "installed_software_in_folder"],
            "statement": (
                "A file serves the purpose its name states, where the name's free words reach "
                "{minimum_senses} or more distinct senses of kind {theme_kind}, unless the file "
                "is a program or lies in a folder laid out as an installed program."
            ),
            "rationale": (
                "The name is the only evidence of what a file is for short of reading it, and a "
                "program named for what it makes is not the thing it makes."
            ),
            "parameters": [
                {
                    "name": "theme_kind",
                    "type": "string",
                    "default": "file_role",
                    "description": "The axis to read.",
                },
                {
                    "name": "minimum_senses",
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 10,
                    "description": "How many distinct roles a name needs.",
                },
            ],
        },
        {
            "id": "name_family_without_stages",
            "version": "1",
            "predicate": "same_component_values",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "name_family",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
            "statement": (
                "Two items share a name family when they agree on every component of kind "
                "{component_kinds}, leaving out a word matched to a sense of kind "
                "{ignored_theme_kinds}, and each carries {minimum_components} or more of them, "
                "unless a member is filesystem noise or a placeholder."
            ),
            "rationale": (
                "The stem that survives stripping markers, stages and extensions is what a "
                "projection and its acquisition have in common, and a cache file or a link that "
                "happens to share the words is not a member of anybody's family."
            ),
            "parameters": [
                {
                    "name": "component_kinds",
                    "type": "string_list",
                    "default": ["word"],
                    "description": "Which components form the family.",
                },
                {
                    "name": "ignored_theme_kinds",
                    "type": "string_list",
                    "default": ["stage"],
                    "description": (
                        "A word matched to a sense of these kinds is left out. **Stage words and "
                        "no others**: whether the family should also forget which replicate a "
                        "file is, as the identity does, is a decision of its own and the "
                        "design asks for it only of the identity."
                    ),
                },
                {
                    "name": "minimum_components",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 20,
                    "description": "How many words must agree.",
                },
            ],
        },
        {
            "id": "same_name_identity",
            "version": "4",
            "predicate": "same_component_values",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "name_identity",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
            "statement": (
                "Two items share a name identity when, compared within one {compared_within}, "
                "they agree on every component of kind {component_kinds}, in the same places "
                "for a component of kind {ordered_component_kinds}, leaving out a word or an "
                "indexed label matched to a sense of kind {ignored_theme_kinds}, and each carries "
                "{minimum_components} or more of them, unless a member is filesystem noise or a "
                "placeholder."
            ),
            "rationale": (
                "Inside a pipeline's output bundle every file shares its words and differs in "
                "its numbers, indices and quantities, which is where the sample and the "
                "acquisition are written; identical names recur across output folders, so the "
                "identity is kept to one folder, and stage words are left out because the two "
                "ends of a derivation differ in exactly those. Replicate components are left "
                "out for the same reason one level down: an experiment is what its replicates "
                "have in common, so two files marked rep1 and rep2 are one experiment and an "
                "index that separated them would make every replicate its own. A number's "
                "place is kept because a plate reader writes a well, a field and a frame as "
                "numbers in fixed positions, and compared as a set they said a different tile "
                "was the same thing."
            ),
            "parameters": [
                {
                    "name": "compared_within",
                    "type": "enum",
                    "default": "folder",
                    "choices": ["pool", "folder"],
                    "description": "Where two names are compared.",
                },
                {
                    "name": "component_kinds",
                    "type": "string_list",
                    "default": ["word", "number", "indexed", "quantity", "date", "version"],
                    "description": "Every kind of name component but the extension.",
                },
                {
                    "name": "ignored_theme_kinds",
                    "type": "string_list",
                    "default": ["stage", "replicate"],
                    "description": (
                        "A word, or an indexed component's label, matched to a sense of these "
                        "kinds is left out. Ignoring a kind removes a distinction, and a "
                        "replicate index is exactly a distinction an experiment must not make."
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
                    "default": ["number", "quantity"],
                    "description": (
                        "Every occurrence of a component of these kinds is compared in the "
                        "place the name wrote it, and not only as a value the name carries. "
                        "**Numbers**: a well, a field and a frame are numbers in fixed "
                        "positions, and read as a set they made A1_03_1_2_DAPI_001 and "
                        "A1_03_1_3_DAPI_002 one identity. **And quantities** (position "
                        "matters, quantities keep their place): a name that gives one amount "
                        "to one reagent and another to the next names a condition, and read "
                        "as a set, 10 of one and 0 of the other made one identity with 0 of "
                        "one and 10 of the other."
                    ),
                },
            ],
        },
        {
            "id": "filesystem_noise_from_spelling",
            "version": "1",
            "predicate": "sense_from_name_spelling",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "statement": (
                "An item whose name is spelled, as a {positions}, like one of {senses} was "
                "made by a tool rather than by a person and is set aside, except where the "
                "spelling is {excluded_surfaces}."
            ),
            "rationale": (
                "Operating systems, editors and sync clients mark what they make by a whole name "
                "or by an affix, and the census keeps neither as a component, so the name's own "
                "spelling is the only place the engine can read them; an office suite's lock "
                "file beside a table is somebody at work, which is why its prefix is left out."
            ),
            "parameters": [
                {
                    "name": "senses",
                    "type": "string_list",
                    "default": _NOISE_SENSES,
                    "description": "The senses that mean nobody chose to make the item.",
                },
                {
                    "name": "positions",
                    "type": "string_list",
                    "default": ["whole", "prefix", "suffix"],
                    "description": (
                        "Where a spelling is read: as the whole name, at its start, or at its "
                        "end. A spelling in the middle of a name is not read."
                    ),
                },
                {
                    "name": "excluded_surfaces",
                    "type": "string_list",
                    "default": ["~$"],
                    "description": ("Spellings never read: an office suite's lock file."),
                },
            ],
        },
        {
            "id": "acquisition_stage_from_format",
            "version": "3",
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
                "reads the spelling's origin, because images are one type."
            ),
            "parameters": [
                {
                    "name": "senses",
                    "type": "string_list",
                    "default": ["image_format", "ephys_format"],
                    "description": (
                        "The format senses that mean data as an instrument produced it. Spectra, "
                        "reads and their conversions are left out, because their formats also "
                        "hold identification results, references and assemblies."
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
            "id": "analysis_stage_from_derived_sense",
            "version": "1",
            "predicate": "class_from_bound_sense",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["program_from_extension"],
            "statement": (
                "A file whose {slot} matches one of {senses} at quality "
                "{minimum_match_quality} or better, sits at the {concludes} stage, unless the "
                "file is a program."
            ),
            "rationale": (
                "A trajectory is computed and never acquired: something tracked particles or ran "
                "a simulation, and the file is that result. Reading it as a stage through the "
                "sense rather than through a new surface is what keeps Traj_ meaning a "
                "trajectory: adding it as a second spelling of a stage would make it stand "
                "between two senses at equal precedence, and a word standing between two senses "
                "resolves to neither, so one good match would become two abstentions."
            ),
            "parameters": [
                {
                    "name": "senses",
                    "type": "string_list",
                    "default": ["trajectory"],
                    "description": (
                        "Senses naming a derived measurement rather than a part of an "
                        "acquisition. Only trajectory qualifies today: every other sense of its "
                        "kind names a region, a plane, a tile or a sweep, which are parts of how "
                        "something was acquired and not results computed from it."
                    ),
                },
                {
                    "name": "concludes",
                    "type": "string",
                    "default": "analysis",
                    "description": "The stage concluded.",
                },
                {
                    "name": "minimum_match_quality",
                    **_QUALITY,
                    "default": "case_folded",
                    "description": (
                        "A word, so case may be folded. Traj_ is written with a capital as often "
                        "as not, and it is a word rather than a unit symbol."
                    ),
                },
                {
                    "name": "slot",
                    "type": "enum",
                    "default": "name",
                    "choices": ["extension", "name"],
                    "description": "Read from the name's own words, never the extension.",
                },
            ],
        },
        {
            "id": "content_unreadable",
            "version": "7",
            "predicate": "content_unreadable",
            "effect": "flags",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A file whose content cannot be read is raised, saying which way it is out of "
                "reach -- an empty output, where it holds no bytes and a file of the same name "
                "but for its numbers holds bytes in its folder or a folder beside it, or a file "
                "of its batch (the same extension and first and last name parts) holds bytes in "
                "its folder; empty; "
                "empty and untouched since {written_before}; an archive link, whose text names "
                "another server's share; a link whose target lies outside the root it was found "
                "under; a link whose target is gone; or no regular file -- and marked where it "
                "lies in a folder laid out as an installed program, where an empty file is "
                "ordinary; and, where {notes_as_names}, an empty file with an extension among "
                "{note_extensions} whose name is {minimum_words} or more words of letters written "
                "with spaces is a note and not raised."
            ),
            "rationale": (
                "This replaces empty_and_untouched. An empty file is included in the analysis "
                "but marked as empty: it cannot be scanned deeply or hashed, and being "
                "dehydrated only means it is old. The rule states the fact and infers nothing "
                "about an archive: the file keeps its name, its place, its family and its "
                "identity, and loses only a digest and a deep reading. An empty tracking file "
                "beside replicates that hold content is a real, empty output, and a link that "
                "an archiving system left in place of an archived file is an archive link, "
                "each named so a person reads it without a glossary. Empty replicates from a lack "
                "of data are not data points, and batch pipelines propagate them. A batch's "
                "empty output is known by its template, where the movie names differ by a "
                "word. A note written as a file name is a note, its name its content, never "
                "an empty file. What a note is, read by this rule's own thresholds, the note "
                "rule's defaults. The note thresholds are three copies, one in each of this "
                "rule, empty_replicate_is_no_data_point and note_written_as_a_file_name: tune "
                "the three together, or a file claimed a note is raised here all the same."
            ),
            "parameters": [
                {
                    "name": "notes_as_names",
                    "type": "boolean",
                    "default": True,
                    "description": "Whether an empty file whose name is a sentence is a note.",
                },
                *_note_thresholds(),
                {
                    "name": "written_before",
                    "type": "string",
                    "default": "2025-09-16T00:00:00Z",
                    "description": (
                        "An empty file nothing has written to since this date is raised as "
                        "dehydrated or archived rather than merely empty. Written as a date "
                        "and resolved once, so two runs a year apart agree; a blank raises "
                        "everything empty as empty."
                    ),
                },
            ],
        },
        {
            "id": "later_stage_from_name",
            "version": "1",
            "predicate": "sense_from_name_tokens",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "statement": (
                "A file's own name states a stage past acquisition where its free words reach "
                "{minimum_senses} or more distinct senses of kind {theme_kind} among {senses}, "
                "matched at quality {minimum_match_quality} or better."
            ),
            "rationale": (
                "Instrument software saves a deconvolved, stitched or cropped image in its own "
                "format, so an acquisition extension does not by itself mean acquired data, and "
                "the name is where a person or a tool said what was done to it."
            ),
            "parameters": [
                {
                    "name": "theme_kind",
                    "type": "string",
                    "default": "stage",
                    "description": "The axis read.",
                },
                {
                    "name": "senses",
                    "type": "string_list",
                    "default": _LATER_STAGES,
                    "description": "The stage senses that place a file past acquisition.",
                },
                {
                    "name": "minimum_senses",
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 10,
                    "description": "How many distinct later stages the name needs.",
                },
                {
                    "name": "minimum_match_quality",
                    **_QUALITY,
                    "default": "case_folded",
                    "description": "How firmly a stage word must have matched.",
                },
            ],
        },
        {
            "id": "derivation_within_name_identity",
            "version": "7",
            "predicate": "derives_within_a_name_identity",
            "effect": "proposes",
            "primitive": "derives",
            "specialization": "processed_from",
            "tier": "defeasible",
            "level": "file",
            "candidates_within": "same_name_identity_within_experiment",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "Two files sharing a name identity within their experiment, in two of its folders "
                "(only so: {across_folders_only}), are proposed as one processed from the other, "
                "the earlier as the source, where their earliest credible times by one kind of "
                "clock are more than {tolerance_seconds} seconds apart ({direction}), never where "
                "another file of the identity, in another folder than the target's, was made "
                "between them (a chain is proposed link by link), nor where one is the recording "
                "the other is named for (proposed by {unless_joined_by}'s own rule), a step in "
                "{stage_transitions} corroborating it "
                "where both state a stage, and the pair is blocked where either file lies in a "
                "folder laid out as an installed program."
            ),
            "rationale": (
                "An identity says two files are about one sample and one acquisition and says "
                "nothing about which produced which. All derivations come before stage "
                "assignment. The earlier file is the source, by the time each file first "
                "existed as far as its own records say, and never by its stage words, which "
                "now only corroborate; two files no time orders are proposed as related by "
                "the rule beside this one. A chain is proposed link by link, never the "
                "closure; two files of one folder are siblings; a content clock is never read "
                "against a filesystem one, nor a copy's. Two outputs named for one recording "
                "are joined here where they share an identity across folders, and a sibling "
                "in the target's own folder never stands between."
            ),
            "parameters": [
                {
                    "name": "across_folders_only",
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Only two files in different folders of the experiment: two of one "
                        "folder are siblings."
                    ),
                },
                {
                    "name": "direction",
                    "type": "enum",
                    "default": "earliest_first",
                    "choices": ["earliest_first", "undirected"],
                    "description": "The earlier file as the source.",
                },
                {
                    "name": "tolerance_seconds",
                    "type": "integer",
                    "default": 60,
                    "minimum": 0,
                    "maximum": 86400,
                    "description": (
                        "How far apart the two files' earliest credible times must be before the "
                        "earlier is read as the source."
                    ),
                },
                {
                    "name": "unless_joined_by",
                    "type": "string",
                    "default": "named_for_an_acquisition",
                    "description": (
                        "Where one end is the recording the other's name holds, by this grouping, "
                        "its own rule proposes the pair; blank proposes it here too."
                    ),
                },
                {
                    "name": "stage_transitions",
                    "type": "mapping",
                    "default": {
                        "raw": "processed",
                        "acquisition": "processed",
                        "processed": "analysis",
                        "analysis": "quantification",
                        "quantification": "summary",
                    },
                    "description": (
                        "Which stage may follow which: a step agreeing with the order "
                        "corroborates it, and none is needed."
                    ),
                },
            ],
        },
        {
            "id": "related_within_name_identity",
            "version": "3",
            "predicate": "derives_within_a_name_identity",
            "effect": "proposes",
            "primitive": "concerns",
            "tier": "defeasible",
            "level": "file",
            "candidates_within": "same_name_identity_within_experiment",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "Two files sharing a name identity within their experiment, in two of its folders "
                "(only so: {across_folders_only}), are proposed as related, which if either was "
                "made from the other not known, where no earliest credible time of one kind is "
                "more than {tolerance_seconds} seconds from the other's ({direction}), never where "
                "one is the recording the other is named for ({unless_joined_by}), and the pair is "
                "blocked where either file lies in a folder laid out as an installed program."
            ),
            "rationale": (
                "Where neither evidence nor time says which of two files was made from the "
                "other, the pair stays undirected, a person's Related, rather than being "
                "given a direction by its stage words. In two folders only. Two outputs named "
                "for one recording are related here where no time orders them."
            ),
            "parameters": [
                {
                    "name": "across_folders_only",
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Only two files in different folders of the experiment: two of one "
                        "folder are siblings."
                    ),
                },
                {
                    "name": "direction",
                    "type": "enum",
                    "default": "undirected",
                    "choices": ["earliest_first", "undirected"],
                    "description": "Neither file as the source.",
                },
                {
                    "name": "tolerance_seconds",
                    "type": "integer",
                    "default": 60,
                    "minimum": 0,
                    "maximum": 86400,
                    "description": (
                        "How far apart the two files' earliest credible times must be before the "
                        "earlier is read as the source."
                    ),
                },
                {
                    "name": "unless_joined_by",
                    "type": "string",
                    "default": "named_for_an_acquisition",
                    "description": "As the derivation within a name identity reads it.",
                },
            ],
        },
        {
            "id": "references_a_mentioned_file",
            "version": "1",
            "predicate": "references_by_declared_path",
            "effect": "proposes",
            "primitive": "references",
            "tier": "defeasible",
            "level": "file",
            "candidates_within": "mentioned",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A file is proposed as referencing another where one of its mentions resolves to "
                "it, in its own folder, rooted under its root, or from a root of the pool, at "
                "resolution {minimum_resolution} or better, and the pair is blocked where either "
                "file lies in a folder laid out as an installed program."
            ),
            "rationale": (
                "A mention names a file and says nothing about whether it was read or written, "
                "so the relation is references and never a derivation on its own; a mention that "
                "names nothing in the pool, several things, or another machine abstains with its "
                "reason, and a code include is never resolved against data."
            ),
            "parameters": [
                {
                    "name": "minimum_resolution",
                    "type": "enum",
                    "default": "unique_suffix",
                    "choices": ["exact", "unique_suffix", "unique_basename", "ambiguous"],
                    "description": (
                        "How firmly a mention must resolve. Exact is its own folder or its root; "
                        "unique_suffix admits a path found only from a root of the pool, which "
                        "rests on the laboratory treating that root as its base."
                    ),
                },
            ],
        },
        {
            "id": "derivation_named_by_a_describing_file",
            "version": "2",
            "predicate": "derives_from_declared_reference",
            "effect": "proposes",
            "primitive": "derives",
            "specialization": "processed_from",
            "tier": "defeasible",
            "level": "file",
            "candidates_within": "co_mentioned",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "Two files one file of type {describing_formats} or role {describing_roles} "
                "mentions, each resolved at {minimum_resolution} or better, are proposed as one "
                "processed from the other, the earlier as the source, where time orders them "
                "({ordered_by_time}) and their earliest credible times are more than "
                "{tolerance_seconds} seconds apart ({direction}), a step in {stage_transitions} "
                "corroborating it where both state a stage, and the pair is blocked where either "
                "lies in a folder laid out as an installed program."
            ),
            "rationale": (
                "A script, a notebook or a workspace naming two files is testimony written at "
                "the time, and it still cannot say which was read and which written, and a "
                "file that only lists things, a configuration naming twenty inputs, describes "
                "no process at all. All derivations come before stage assignment. The earlier "
                "file is the source, never the one a stage word places first; two files no "
                "time orders are proposed as related by the rule beside this."
            ),
            "parameters": [
                {
                    "name": "ordered_by_time",
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Whether the earlier file is read as the source. Off: two inputs of "
                        "one script were made in turn and neither from the other."
                    ),
                },
                {
                    "name": "direction",
                    "type": "enum",
                    "default": "earliest_first",
                    "choices": ["earliest_first", "undirected"],
                    "description": "The earlier file as the source.",
                },
                {
                    "name": "tolerance_seconds",
                    "type": "integer",
                    "default": 60,
                    "minimum": 0,
                    "maximum": 86400,
                    "description": (
                        "How far apart the two files' earliest credible times must be before the "
                        "earlier is read as the source."
                    ),
                },
                {
                    "name": "describing_formats",
                    "type": "string_list",
                    "default": [
                        "python_source",
                        "r_source",
                        "r_markdown",
                        "matlab_source",
                        "shell_script",
                        "perl_source",
                        "notebook_format",
                        "imagej_macro",
                        "flowjo_workspace",
                        "log_file",
                    ],
                    "description": (
                        "The file types whose files describe a process: scripts, notebooks, "
                        "macros, an analysis workspace and a run's log."
                    ),
                },
                {
                    "name": "minimum_resolution",
                    "type": "enum",
                    "default": "unique_suffix",
                    "choices": ["exact", "unique_suffix", "unique_basename", "ambiguous"],
                    "description": (
                        "How firmly each end must have been named. A path matched only by its "
                        "last name places a pair and licenses no derivation."
                    ),
                },
                {
                    "name": "describing_roles",
                    "type": "string_list",
                    "default": ["protocol", "notes"],
                    "description": (
                        "The roles a named file may play that describe a process. Metadata is "
                        "left out: a sidecar names its data and describes no step."
                    ),
                },
                {
                    "name": "stage_transitions",
                    "type": "mapping",
                    "default": {
                        "raw": "processed",
                        "acquisition": "processed",
                        "processed": "analysis",
                        "analysis": "quantification",
                        "quantification": "summary",
                    },
                    "description": (
                        "Which stage may follow which: a step agreeing with the order "
                        "corroborates it, and none is needed."
                    ),
                },
            ],
        },
        {
            "id": "related_by_a_describing_file",
            "version": "1",
            "predicate": "derives_from_declared_reference",
            "effect": "proposes",
            "primitive": "concerns",
            "tier": "defeasible",
            "level": "file",
            "candidates_within": "co_mentioned",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "Two files one file of type {describing_formats} or role {describing_roles} "
                "mentions, each resolved at {minimum_resolution} or better, are proposed as "
                "related where time does not order them ({ordered_by_time}), or no earliest "
                "credible time is more than {tolerance_seconds} seconds from the other's "
                "({direction}), and the pair is blocked where either lies in a folder laid out as "
                "an installed program."
            ),
            "rationale": (
                "Where neither the describing file nor time says which of two files "
                "was made from the other, the pair stays undirected, a person's Related, rather "
                "than being given a direction by its stage words."
            ),
            "parameters": [
                {
                    "name": "ordered_by_time",
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Whether the earlier file is read as the source. Off: two inputs of "
                        "one script were made in turn and neither from the other."
                    ),
                },
                {
                    "name": "direction",
                    "type": "enum",
                    "default": "undirected",
                    "choices": ["earliest_first", "undirected"],
                    "description": "Neither file as the source.",
                },
                {
                    "name": "tolerance_seconds",
                    "type": "integer",
                    "default": 60,
                    "minimum": 0,
                    "maximum": 86400,
                    "description": "As the derivation named by a describing file reads it.",
                },
                {
                    "name": "describing_formats",
                    "type": "string_list",
                    "default": [
                        "python_source",
                        "r_source",
                        "r_markdown",
                        "matlab_source",
                        "shell_script",
                        "perl_source",
                        "notebook_format",
                        "imagej_macro",
                        "flowjo_workspace",
                        "log_file",
                    ],
                    "description": "As the derivation named by a describing file reads them.",
                },
                {
                    "name": "describing_roles",
                    "type": "string_list",
                    "default": ["protocol", "notes"],
                    "description": "As the derivation named by a describing file reads them.",
                },
                {
                    "name": "minimum_resolution",
                    "type": "enum",
                    "default": "unique_suffix",
                    "choices": ["exact", "unique_suffix", "unique_basename", "ambiguous"],
                    "description": "As the derivation named by a describing file reads it.",
                },
            ],
        },
        {
            "id": "doubt_in_a_folder_name",
            "version": "1",
            "predicate": "authored_name_doubt",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "statement": (
                "A folder is raised, as {raises}, when its own name carries {minimum_terms} or "
                "more of {senses}, where one of {low_strength_senses} counts only beside another."
            ),
            "rationale": (
                "Nothing machine-generated emits final_final or use_this_one, so a hit is evidence "
                "a person typed the name and was unsure; what the doubt means is not the engine's "
                "to say, which is why this raises rather than concludes."
            ),
            "parameters": [
                {
                    "name": "raises",
                    "type": "enum",
                    "default": "doubt",
                    "choices": ["doubt", "confirmation"],
                    "description": "What the rule raises a name for.",
                },
                {
                    "name": "minimum_terms",
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 10,
                    "description": "How many counted terms raise a name.",
                },
                {
                    "name": "senses",
                    "type": "string_list",
                    "default": _DOUBT_TERMS,
                    "description": "The doubt terms, every one the lexicon holds.",
                },
                {
                    "name": "low_strength_senses",
                    "type": "string_list",
                    "default": _LOW_STRENGTH_DOUBT,
                    "description": "The terms too common in ordinary names to raise one alone.",
                },
            ],
        },
        {
            "id": "doubt_to_confirm_in_a_folder_name",
            "version": "1",
            "predicate": "authored_name_doubt",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "statement": (
                "A folder is raised, as {raises} for a person to give, when {minimum_terms} or "
                "more of {senses} are in its own name and every one of them is one of "
                "{low_strength_senses}."
            ),
            "rationale": (
                "A folder named weird or on hold is probably about what the word says, and only a "
                "person can confirm it, so these names are raised apart from doubt, by a rule of "
                "their own, and a reader can take them last."
            ),
            "parameters": [
                {
                    "name": "raises",
                    "type": "enum",
                    "default": "confirmation",
                    "choices": ["doubt", "confirmation"],
                    "description": "What the rule raises a name for.",
                },
                {
                    "name": "minimum_terms",
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 10,
                    "description": "How many low-strength terms raise a name.",
                },
                {
                    "name": "senses",
                    "type": "string_list",
                    "default": _DOUBT_TERMS,
                    "description": "The doubt terms, every one the lexicon holds.",
                },
                {
                    "name": "low_strength_senses",
                    "type": "string_list",
                    "default": _LOW_STRENGTH_DOUBT,
                    "description": "The terms raised only for a person to confirm.",
                },
            ],
        },
        {
            "id": "replicate_from_indexed_label",
            "version": "1",
            "predicate": "index_of_bound_label",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["program_from_extension"],
            "statement": (
                "A file is the repetition its name states, when an indexed label matched at "
                "quality {minimum_match_quality} or better matches a sense of kind "
                "{theme_kind}, unless the file is a program."
            ),
            "rationale": (
                "The sense and the index together are the fact: a sense alone says only that "
                "a repetition happened, and an index alone is a position with no meaning "
                "attached. Counted per sense and never summed, because three technical "
                "replicates of one dish is n = 1 and a tool reporting three replicates "
                "without saying which kind invites the commonest statistical error in the "
                "field. The bare replicate sense is an unspecified kind and is never assumed "
                "biological. A script named for a replicate processes one and is not one, "
                "which is why a program defeats it."
            ),
            "parameters": [
                {
                    "name": "theme_kind",
                    "type": "string",
                    "default": "replicate",
                    "description": (
                        "The axis whose matched labels are read. Every sense on it counts, so "
                        "what the experiment identity leaves out and what this concludes are "
                        "the same set."
                    ),
                },
                {
                    "name": "minimum_match_quality",
                    "default": "exact",
                    "description": (
                        "How a label must have matched. Exact, because a label reached by "
                        "stripping punctuation is a guess about one or two characters."
                    ),
                    **_QUALITY,
                },
            ],
        },
        {
            "id": "stated_count_may_mark_replicates",
            "version": "2",
            "predicate": "stated_count_may_mark_replicates",
            "effect": "flags",
            "tier": "defeasible",
            "level": "file",
            "statement": (
                "A name whose indexed label matches a sense of kind {theme_kind}, stating a "
                "count of {maximum_count} or fewer, is raised: as a cardinal where a rule in "
                "{derivation_rules} concluded about the file, and as an ordinal where a rule "
                "in {observation_rules} did, whatever the workflow says; otherwise as a "
                "cardinal where a rule in {stage_rules} places the file past the rung where "
                "data enters, and as an ordinal the engine cannot confirm everywhere else."
            ),
            "rationale": (
                "N=3 is a count, and which count depends on where the file sits. On an "
                "analysis, a summary or a pooled result it says what the file rests on; on "
                "raw data it is more often somebody numbering the Nth of their replicates "
                "while collecting them. The same string means both and nothing in it says "
                "which, so the second reading is raised and never concluded. It decides "
                "nothing about the file carrying it: what it does is raise the expectation "
                "that siblings exist, which a later rule reads. The bound is here and not in "
                "the tokenizer, because a large N is a true count and a false replicate design "
                "and only a reader tells those apart."
            ),
            "parameters": [
                {
                    "name": "maximum_count",
                    "type": "integer",
                    "default": 9,
                    "minimum": 1,
                    "maximum": 99,
                    "description": (
                        "How large a stated count may be and still describe a replicate set. "
                        "A bound, and past it the number is a true count of something that is "
                        "not a replicate design."
                    ),
                },
                {
                    "name": "theme_kind",
                    "type": "string",
                    "default": "count",
                    "description": (
                        "The axis whose matched labels are read. Not replicate: a stated count "
                        "is a cardinality and an indexed label an ordinal, and on one axis "
                        "the rule that reads a position would conclude one as the other."
                    ),
                },
                {
                    "name": "stage_rules",
                    "type": "string_list",
                    "default": [
                        "pipeline_stage_from_name",
                        "acquisition_stage_from_format",
                        "analysis_stage_from_derived_sense",
                    ],
                    "description": (
                        "The rules whose conclusion is read as this file's stage. Named "
                        "rather than matched on the concluded value, so a revision renaming "
                        "one passes its own name and no rule becomes a stage rule by "
                        "happening to conclude a word a workflow uses as a rung."
                    ),
                },
                {
                    "name": "observation_rules",
                    "type": "string_list",
                    "default": ["acquisition_stage_from_format"],
                    "description": (
                        "Rules whose conclusion makes the file an observation whatever the "
                        "workflow says: an instrument's own extension is what the instrument "
                        "wrote, and no declaration outranks it."
                    ),
                },
                {
                    "name": "derivation_rules",
                    "type": "string_list",
                    "default": ["program_from_extension"],
                    "description": (
                        "Rules whose conclusion makes the file a derivation whatever the "
                        "workflow says: a program exists to do something to something else, "
                        "so a script stating N=3 states what it rests on."
                    ),
                },
            ],
        },
        {
            "id": "indexed_label_may_mark_replicates",
            "version": "1",
            "predicate": "indexed_label_may_mark_replicates",
            "effect": "flags",
            "tier": "defeasible",
            "level": "root",
            "statement": (
                "A label the Dictionary places nowhere is raised, for a person to say whether "
                "it marks replicates, when its indices are exactly 1 to n for some n from 2 to "
                "{maximum_index} and it appears in {minimum_folders} or more distinct folders."
            ),
            "rationale": (
                "The engine is not entitled to decide that V means version, variant, volume or "
                "replicate; it can only notice a shape and ask. A short label it has no sense "
                "for, numbered from one to a small n and recurring in more than one folder, is "
                "the shape of a replicate marker and not of a version number, which is "
                "unbounded, or of a channel, which is the same set every time. Answering is a "
                "Dictionary edit at USER origin, after which every rule reading replicate "
                "senses picks it up. Folders and not experiments: an experiment's identity can "
                "only leave out an index whose label matches a replicate sense, which is what "
                "this rule exists to discover, so counting its classes would count the "
                "replicates rather than the experiments."
            ),
            "parameters": [
                {
                    "name": "maximum_index",
                    "type": "integer",
                    "default": 9,
                    "minimum": 2,
                    "maximum": 99,
                    "description": (
                        "How far a run from one may reach and still look like a replicate set. "
                        "Past it the number is a version or a frame, which are unbounded."
                    ),
                },
                {
                    "name": "minimum_folders",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 100,
                    "description": (
                        "How many distinct folders the label must appear in. Two is what "
                        "separates a laboratory that marks replicates this way from one folder "
                        "that happened to hold a V1."
                    ),
                },
            ],
        },
        {
            "id": "installed_software_in_folder",
            "version": "3",
            "predicate": "installed_software_in_scope",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "statement": (
                "A folder is raised as an installed program where it directly holds every part "
                "a program the Dictionary names lays out side by side, naming the program; as a "
                "package where it holds a kind of package's packaging (a setup.py or a "
                "requirements.txt beside Python modules, an R package's description and "
                "namespace, a MATLAB class folder), naming the kind; where at least "
                "{minimum_code_share} of its files are one kind's code and an archive of its own "
                "name ending in one of {archive_suffixes} lies beside it; where its own name ends "
                "in one of {bundle_name_suffixes}; where it directly holds "
                "{minimum_layout_folders} or more folders named among {layout_folder_names}; or "
                "where {minimum_program_files} or more files with an extension among "
                "{program_extensions} make up at least {minimum_program_share} of its files; a "
                "file named among {manifest_names} beside any of these is cited, and raises "
                "nothing alone."
            ),
            "rationale": (
                "A laboratory drive holds programs as well as data, and stubs, copy classes, "
                "proposed pairs and mentions came disproportionately from a bundled "
                "distribution and a tool's trees. Excluding them by name would break the de "
                "novo rule and hide them from the person whose drive it is; the layout of an "
                "installed program is the same on every machine, because packaging made it "
                "so, and reading it generalises. Files of one size are not read: that is what "
                "uncompressed microscopy of one geometry looks like. The rule raises and "
                "concludes nothing; the rules naming it as a defeater say what follows for "
                "what lies beneath the folder. A program is recognised from its top folder, "
                "by the parts a known program carries side by side or by the name packaging "
                "gives it, and everything beneath is the program's. A laboratory's own "
                'package is found by "file structure and type", not by a name somebody built '
                "in. A package is recognised by its packaging, a laboratory's own as much as "
                "anybody's, and a folder mostly of one kind's code by the archive of its own "
                "name it was shared as; the programs and packages are Dictionary data, the "
                "core's packaging conventions and a pack of named scientific suites a person "
                "may switch off, and not a parameter here (a laboratory's own program "
                "is declared in a pack of its own). A folder of scripts with no packaging is "
                "still not raised: on real laboratory folders, the one folder that a share of "
                "scripts would have raised held a laboratory's experiments beneath it."
            ),
            "parameters": [
                {
                    "name": "archive_suffixes",
                    "type": "string_list",
                    "default": [".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar"],
                    "description": (
                        "Endings of an archive's name, compared without case: a folder of "
                        "code with an archive of its own name beside it (dapi.zip, "
                        "dapi-0.3.tar.gz) was shared as that archive."
                    ),
                },
                {
                    "name": "minimum_code_share",
                    "type": "real",
                    "default": 0.5,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": (
                        "The share of a folder's files that must be one kind's code for the "
                        "archive beside it to make it a package. Read on the "
                        "laboratory's folders: most archives lying beside a folder of their own "
                        "name were a copy of the data the folder holds, with no code in it, and "
                        "the folders of code held nothing but code and a folder of example data, "
                        "so any share between the two separates them; a half is the middle."
                    ),
                },
                {
                    "name": "bundle_name_suffixes",
                    "type": "string_list",
                    "default": [".app"],
                    "description": (
                        "Endings of a folder's own name that packaging gives an application: "
                        "a macOS application is a folder so named, and Fiji keeps the name on "
                        "every platform. Raised as an application bundle where no known "
                        "program's parts name it."
                    ),
                },
                {
                    "name": "layout_folder_names",
                    "type": "string_list",
                    "default": [
                        "bin",
                        "sbin",
                        "lib",
                        "lib64",
                        "libexec",
                        "include",
                        "share",
                        "site-packages",
                        "dist-packages",
                        "Frameworks",
                        "__pycache__",
                        "node_modules",
                    ],
                    "description": (
                        "Folder names packaging conventions give an installed program's parts, "
                        "compared without case. Two of them side by side is a layout; one alone "
                        "is a word a laboratory might use."
                    ),
                },
                {
                    "name": "minimum_layout_folders",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 20,
                    "description": "How many of those folders the folder must hold directly.",
                },
                {
                    "name": "program_extensions",
                    "type": "string_list",
                    "default": [
                        "h",
                        "hpp",
                        "so",
                        "dylib",
                        "dll",
                        "a",
                        "o",
                        "lib",
                        "pyc",
                        "pyd",
                        "class",
                        "jar",
                        "exe",
                    ],
                    "description": (
                        "Extensions of compiled objects, libraries and headers, which a program "
                        "carries and a laboratory's analysis does not. A laboratory's own "
                        "scripts are left out on purpose: code a person wrote is not an "
                        "installed program."
                    ),
                },
                {
                    "name": "minimum_program_files",
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100000,
                    "description": (
                        "How many such files the folder must hold directly, so a folder with a "
                        "stray header is not a program's."
                    ),
                },
                {
                    "name": "minimum_program_share",
                    "type": "real",
                    "default": 0.5,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "The share of the folder's files they must make up.",
                },
                {
                    "name": "manifest_names",
                    "type": "string_list",
                    "default": [
                        "setup.py",
                        "setup.cfg",
                        "pyproject.toml",
                        "package.json",
                        "CMakeLists.txt",
                        "Makefile",
                        "configure",
                        "Cargo.toml",
                    ],
                    "description": (
                        "Packaging files, compared without case: cited where a layout signal "
                        "raises the folder, and never enough alone."
                    ),
                },
            ],
        },
        {
            "id": "binding_is_ambiguous_outside_programs",
            "version": "1",
            "predicate": "readings_conflict",
            "effect": "flags",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "An item is raised when a reading of {reading} found {minimum_alternatives} or "
                "more senses standing equal with nothing to separate them, and marked where it "
                "lies in a folder laid out as an installed program, whose names are code."
            ),
            "rationale": (
                "The Dictionary holding a token twice is a fact a person can act on, as the "
                "shipped rule says; declared again because half fragments of code identifiers "
                "inside bundled software, a class name that happened to spell two senses, are "
                "not terms anybody uses and nobody should be asked about. Marked rather than "
                "hidden, so the queue can say what it set aside."
            ),
            "parameters": [
                {
                    "name": "reading",
                    "type": "string",
                    "default": "dictionary_binding",
                    "description": "Which reading is compared.",
                },
                {
                    "name": "minimum_alternatives",
                    "type": "integer",
                    "default": 2,
                    "minimum": 2,
                    "maximum": 100,
                    "description": (
                        "How many senses must stand equal. Two is the minimum a conflict can "
                        "have, and raising it hides exactly the cases that are easiest to fix."
                    ),
                },
            ],
        },
        {
            "id": "same_observed_metadata_outside_programs",
            "version": "2",
            "predicate": "same_observed_metadata",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "observed_metadata",
            # A copy-and-paste signal and not a copy claim, so the tier is the one for a reading
            # that explains the facts and does not follow from them.
            "tier": "abductive",
            "level": "file",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "Two items were plausibly copied or pasted from one another when they agree "
                "on all of {compared_facts}, unless one lies in a folder laid out as an "
                "installed program; whether they hold the same bytes is a separate question a "
                "digest answers."
            ),
            "rationale": (
                "The key leaves out birth time. With birth time in the key, the rule proved "
                "unreliable as a copy rule. Without it, the rule is good for detecting "
                "copy-and-paste. Two copies of one file rarely share a birth instant. On real "
                "laboratory folders, nearly half of the files were born after they were last "
                "modified, so the strict key found many classes where few were "
                "byte-identical and fewer were copies at all. Name, size and modification "
                "time is the shape of a copy-and-paste, which is worth raising and is not "
                "identity: a copy claim rests on a digest, which the rules beside this one "
                "make."
            ),
            "parameters": [
                {
                    "name": "compared_facts",
                    "type": "string_list",
                    "default": ["name", "size_bytes", "modified_ns", "entry_type"],
                    "description": (
                        "The filesystem facts that must agree. **Birth time is left out**: it "
                        "is the fact a copy does not preserve, so requiring it found "
                        "same-instant siblings inside installed software and missed copies a "
                        "person made."
                    ),
                },
            ],
        },
        {
            "id": "copies_by_sampled_digest",
            "version": "1",
            "predicate": "same_content_digest",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "sampled_content",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["content_unreadable"],
            "statement": (
                "Two items hold the same bytes as far as a sample can say when their "
                "{digest_algorithm} digests agree, unless a file's content could not be read "
                "at all, in which case a digest of it says nothing."
            ),
            "rationale": (
                "A copy claim needs content, and a standard dive computes a digest over the "
                "head, the tail and the size of every file it examines. That is what a "
                "laboratory's dive has, and it is strong enough to claim a copy defeasibly "
                "and far too weak to claim identity, which is why the whole-file digest keeps "
                "that word and the entailed tier to itself. What retracts it is content that "
                "was never there to read: every empty file in a pool shares one digest, and a "
                "dive skipping them is why this rarely fires."
            ),
            "parameters": [
                {
                    "name": "digest_algorithm",
                    "type": "string",
                    "default": "blake3_quick",
                    "description": (
                        "The identity method whose digest settles it. The sampled one: its "
                        "scope travels in the key, so a sampled digest never groups with a "
                        "whole one."
                    ),
                },
            ],
        },
        {
            "id": "claim_class_a_person_answered",
            "version": "1",
            "predicate": "answered_claim_class",
            "effect": "flags",
            "tier": "defeasible",
            "level": "root",
            "statement": (
                "Every conclusion one rule made with one value about a file is no longer that "
                "rule's reading alone where a person answered that the rule read the whole class "
                "wrongly: the answer, read from the Record at the run's watermark, is recorded "
                "fired on each conclusion of the class that can be retracted, which is then "
                "published as conflicting and never removed. It raises nothing itself."
            ),
            "rationale": (
                "The design's class handle: 'this is wrong' about one file is about "
                "the class the rule read alike, so a person's answer is recorded once, on the "
                "class, and the next inference reads it for every member. "
                "A defeat condition is a rule, so the retraction names one a person can read, as "
                "a person's answer on a set of copies does."
            ),
        },
        {
            "id": "copies_a_person_answered",
            "version": "1",
            "predicate": "answered_copies",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "copies_a_person_answered",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
            "statement": (
                "Files are copies of one file when a person answered so about the files a copy "
                "rule showed them, sets sharing a file being one set, and the files of a set a "
                "person answered is unrelated are no longer supposed copies by a rule that could "
                "only suppose it, the bytes a whole-file digest proved equal staying equal, unless "
                "a member is filesystem noise or a placeholder."
            ),
            "rationale": (
                "A person's answer on a set of copies changed no conclusion, and a set called "
                "unrelated was still called copies. This rule reads the answers from the "
                'Record at the run\'s watermark: "Copies" groups the set as the person said, '
                'and "Unrelated" is the defeater recorded, fired, on every copy claim about '
                "the set that can be retracted, which is then published as conflicting and "
                "never removed; equal bytes are a fact the answer grants."
            ),
        },
        {
            "id": "archive_link_copy_of_a_file",
            "version": "2",
            "predicate": "archive_link_names_a_file",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "archived_copy",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A link an archive left, whose text names another server's share, is a copy of the "
                "one real file in the pool of its name, or, where several share its name, of the "
                "one whose folders agree with the most folders the link's text names, and never of "
                "a file whose size differs from the size a listing declares for the file behind "
                "the link; neither is opened; unless it lies in a folder laid out as an installed "
                "program."
            ),
            "rationale": (
                "Expect many copies and backups that are only an archive's link, and an "
                "archive link to a file that appears elsewhere is a copy of it, 'definitely "
                "yes'. A link states no size, so name and path are what match; two files "
                "agreeing equally match nothing. Where a listing declares the size of the "
                "file behind a link it lists, that size is read, a file of another size is "
                "not the copy, and an agreeing size is cited."
            ),
        },
        {
            "id": "declared_digest_contradicted",
            "version": "1",
            "predicate": "declared_digest_contradicted",
            "effect": "classifies",
            "tier": "entailed",
            "level": "file",
            "statement": (
                "A file's declared {digest_algorithm} digest is contradicted where the engine "
                "read its bytes under the same method and found another value."
            ),
            "rationale": (
                "A digest an archive declares is a third party's "
                "statement about bytes; where the engine has read the same bytes under the same "
                "method, a different value is a fact, and it retracts every identity that rested "
                "on the declaration."
            ),
            "parameters": [
                {
                    "name": "digest_algorithm",
                    "type": "string",
                    "default": "md5",
                    "description": "The method the listing declared and the engine read.",
                },
            ],
        },
        {
            "id": "identical_by_declared_digest",
            "version": "1",
            "predicate": "same_declared_digest",
            "effect": "groups",
            "primitive": "identical",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["declared_digest_contradicted"],
            "statement": (
                "Two files hold the same bytes, as their listings declare it, when the "
                "{digest_algorithm} digests the listings state for their whole content agree; "
                "unless the engine read a file's bytes and found another digest."
            ),
            "rationale": (
                "Identical means proven byte equality, "
                "and the engine did not read these bytes, so a declared digest groups one level "
                "below an observed one (identical_by_content_digest stays entailed and stays "
                "for digests the engine computed), retracted where the engine's own reading of "
                "the same method disagrees."
            ),
            "parameters": [
                {
                    "name": "digest_algorithm",
                    "type": "string",
                    "default": "md5",
                    "description": "The declared method whose digests settle it.",
                },
            ],
        },
        {
            "id": "declared_sha256_contradicted",
            "version": "1",
            "predicate": "declared_digest_contradicted",
            "effect": "classifies",
            "tier": "entailed",
            "level": "file",
            "statement": (
                "A file's declared {digest_algorithm} digest is contradicted where the engine "
                "read the file's bytes under that method and found another value."
            ),
            "rationale": (
                "An archive's SHA-256 is proof, and where it falls short in any way the "
                "engine hashes itself; where the engine's own reading disagrees, its reading "
                "is the fact."
            ),
            "parameters": [
                {
                    "name": "digest_algorithm",
                    "type": "string",
                    "default": "sha256",
                    "description": "The declared method checked against the engine's reading.",
                },
            ],
        },
        {
            "id": "identical_by_declared_sha256",
            "version": "1",
            "predicate": "same_declared_digest",
            "effect": "groups",
            "primitive": "identical",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["declared_sha256_contradicted"],
            "statement": (
                "Two files hold the same bytes when the {digest_algorithm} digests their "
                "listings declare for their whole content agree; unless the engine read a file's "
                "bytes under that method and found another digest."
            ),
            "rationale": (
                "An archive's SHA-256 can count as proof, but where that proof is "
                "insufficient in any way, the engine hashes things itself. So a declared "
                "SHA-256 is entailed, another declared method stays one tier below "
                "(identical_by_declared_digest), a missing or partial declaration leaves the "
                "engine's own digests to decide where it has the bytes, and the engine's "
                "remote whole reading computes SHA-256 to check one. **Defeasible until the "
                "engine observes SHA-256 itself**: an entailed rule names no defeater, and "
                "the engine records no SHA-256 of a local file yet, so its contradiction "
                "could never fire; entailed once it can."
            ),
            "parameters": [
                {
                    "name": "digest_algorithm",
                    "type": "string",
                    "default": "sha256",
                    "description": "The declared method that is proof.",
                },
            ],
        },
        {
            "id": "declared_attribute_to_clarify",
            "version": "1",
            "predicate": "declared_attribute_to_clarify",
            "effect": "flags",
            "tier": "defeasible",
            "level": "file",
            "statement": (
                "A file is raised with each attribute its listing declares, by the attribute's "
                "name, so a person clarifies what it says once for every file stating it; "
                "nothing is concluded from it and no vocabulary holds it."
            ),
            "rationale": (
                "No archive's terminology is pre-packed; the engine adapts to whatever "
                "archive it meets and makes it easy for a person to clarify terms. An "
                "attribute a converter passed through is asked about."
            ),
        },
        {
            "id": "declared_copy_window_exceeded",
            "version": "1",
            "predicate": "declared_copy_window_exceeded",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "statement": (
                "A folder is raised where at least {minimum_files} of its files, and at least "
                "{minimum_share} of them, carry a copy time its listing declared, and those times "
                "span more than {burst_window_seconds} seconds: the copy window failing to hold."
            ),
            "rationale": (
                "The declared copy window can be a day, and where it changes or fails to "
                "hold, flag it so it can be adjusted. The parameters are "
                "folder_copied_by_declared_clocks's, so the two read one window."
            ),
            "parameters": [
                {
                    "name": "minimum_files",
                    "type": "integer",
                    "default": 2,
                    "minimum": 2,
                    "maximum": 5000,
                    "description": "How many files with a declared copy time it must hold.",
                },
                {
                    "name": "minimum_share",
                    "type": "real",
                    "default": 0.8,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "The share of its files that must carry one.",
                },
                {
                    "name": "burst_window_seconds",
                    "type": "integer",
                    "default": 86400,
                    "minimum": 1,
                    "maximum": 31536000,
                    "description": "The copy window, as folder_copied_by_declared_clocks reads it.",
                },
            ],
        },
        {
            "id": "broken_addresses_in_folder",
            "version": "1",
            "predicate": "broken_addresses_in_folder",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "statement": (
                "A folder is raised once where at least {minimum_files} of the files directly in "
                "it have an address that answered not found or moved, or an address that could not "
                "be read as one, as their listing or a reading of the address says, and none of "
                "their bytes was read; the finding counts them by how their addresses answered."
            ),
            "rationale": (
                "Record broken addresses, with no need to chase them down at this time. An "
                "archive can list hundreds of files whose addresses answer not found, and "
                "that is one fact about the folder rather than a question per file. A broken "
                "address says nothing about whether a file exists elsewhere, so nothing is "
                "concluded about the files themselves."
            ),
            "parameters": [
                {
                    "name": "minimum_files",
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 100000,
                    "description": (
                        "How many of a folder's files must have a broken address. One, "
                        "because every broken address is to be recorded, and the folder, not "
                        "the file, is what is raised."
                    ),
                },
            ],
        },
        {
            "id": "content_contradicts_extension",
            "version": "1",
            "predicate": "extension_contradicts_content",
            "effect": "flags",
            "tier": "defeasible",
            "level": "file",
            "statement": (
                "A file is raised when the {detector} its bytes carry belongs to a format "
                "whose extensions do not include the one it is named with, and is not one of "
                "{container_media_types} or {unclear_media_types}, which a format is written "
                "on rather than contradicted by; this rule reads a disagreement as a "
                "{raises}."
            ),
            "rationale": (
                "The shipped rule read every disagreement as a contradiction and retracted "
                "the format claim of each, which was wrong wherever a vendor format written "
                "on HDF5 is not a mislabelled file. A true contradiction -- a JPEG named .tif "
                "-- is still what retracts a format read from an extension, and that is all "
                "this half raises."
            ),
            "parameters": [
                {
                    "name": "detector",
                    "type": "string",
                    "default": "signature",
                    "description": (
                        "The type detector whose opinion must stand behind a disagreement, as "
                        "the shipped rule requires: a signature, compared against the "
                        "extensions its format declares."
                    ),
                },
                {
                    "name": "raises",
                    "type": "enum",
                    "choices": ["any_disagreement", "contradiction", "container"],
                    "default": "contradiction",
                    "description": "Which half of a disagreement this rule reads.",
                },
                {
                    "name": "container_media_types",
                    "type": "string_list",
                    "default": _CONTAINER_MEDIA_TYPES,
                    "description": (
                        "The general-purpose containers a format is commonly written on. A "
                        "signature naming one of these is read by the rule beside this one."
                    ),
                },
                {
                    "name": "unclear_media_types",
                    "type": "string_list",
                    "default": _UNCLEAR_MEDIA_TYPES,
                    "description": (
                        "The containers whose use only a laboratory can say, raised as a "
                        "question by the rule beside this one."
                    ),
                },
            ],
        },
        {
            "id": "format_built_on_a_container",
            "version": "2",
            "predicate": "extension_contradicts_content",
            "effect": "flags",
            "tier": "defeasible",
            "level": "file",
            "statement": (
                "A file is raised as built on a container when the {detector} its bytes carry "
                "is one of {container_media_types}, and as a question when it is one of "
                "{unclear_media_types}, whose use a laboratory says, unless its extension names a "
                "format outside {archive_types}; this rule reads a disagreement as a {raises}."
            ),
            "rationale": (
                "A container format is recorded as its own kind, built on a container. It is "
                "a fact about the format and worth recording -- a reader knows it can open "
                "the file with an HDF5 library -- and it retracts nothing, which is the whole "
                "difference from a contradiction. ZIP is the exception: a program writes its "
                "own format as one, a person writes an archive as one, and which it is here "
                "is a question ranked by how many files ask it."
            ),
            "parameters": [
                {
                    "name": "detector",
                    "type": "string",
                    "default": "signature",
                    "description": "The type detector whose opinion stands behind the reading.",
                },
                {
                    "name": "raises",
                    "type": "enum",
                    "choices": ["any_disagreement", "contradiction", "container"],
                    "default": "container",
                    "description": "Which half of a disagreement this rule reads.",
                },
                {
                    "name": "container_media_types",
                    "type": "string_list",
                    "default": _CONTAINER_MEDIA_TYPES,
                    "description": "The containers a format is written on.",
                },
                {
                    "name": "unclear_media_types",
                    "type": "string_list",
                    "default": _UNCLEAR_MEDIA_TYPES,
                    "description": "The containers whose use only a laboratory can say.",
                },
                {
                    "name": "archive_types",
                    "type": "string_list",
                    "default": ["archive_format", "compressed_archive", "compression_format"],
                    "description": (
                        "The file types that are archives. An unclear container under an "
                        "extension the Dictionary names as any other format is that program's "
                        "own, and is not asked about."
                    ),
                },
            ],
        },
        {
            "id": "format_from_extension_unless_contradicted",
            "version": "1",
            "predicate": "format_from_extension",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["content_contradicts_extension"],
            "statement": (
                "A file is of the format its extension names, where that extension binds "
                "cleanly to a single sense of kind {theme_kind}, unless its own content "
                "contradicts the extension."
            ),
            "rationale": (
                "The shipped rule, declared again because the fact that retracts it split in "
                "two: a container a format is written on no longer retracts the format read "
                "from the extension, and a real contradiction still does."
            ),
            "parameters": [
                {
                    "name": "theme_kind",
                    "type": "string",
                    "default": "format",
                    "description": (
                        "The axis an extension is matched on, as the shipped rule declares it: an "
                        "extension slot may match a format and nothing else."
                    ),
                },
            ],
        },
        {
            "id": "content_says_do_not_analyse",
            "version": "1",
            "predicate": "content_holds_a_phrase",
            "effect": "flags",
            "tier": "defeasible",
            "level": "file",
            "statement": (
                "A file whose own text holds one of {phrases} is raised, because somebody left "
                "a note about it, and nothing skips a file for what it says."
            ),
            "rationale": (
                "A note in a file saying not to analyze it is a finding and never an "
                "instruction. It reads what a byte scan found, which is opt-in per project, "
                "and a laboratory that writes the note differently edits this sentence."
            ),
            "parameters": [
                {
                    "name": "phrases",
                    "type": "string_list",
                    "default": [
                        "donotanalyse",
                        "do-not-analyse",
                        "do_not_analyse",
                        "donotanalyze",
                        "do-not-analyze",
                        "do_not_analyze",
                        "notforanalysis",
                        "excludefromanalysis",
                        "donotuse",
                        "do-not-use",
                        "do_not_use",
                    ],
                    "description": (
                        "The notes a person writes inside a file to say what should happen to "
                        "it, as the negative lexicon spells them. A phrase a byte scan was not "
                        "asked to look for is never found, so the section must be scanned."
                    ),
                },
            ],
        },
        {
            "id": "earliest_credible_time",
            "version": "3",
            "predicate": "earliest_credible_time",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["placeholder_from_entry_kind"],
            "statement": (
                "A file first existed at the time its own content states, where a dive read one; "
                "else at its birth, where the birth is not later than its modification; else at "
                "its modification, a birth after the modification being a copy's; a time before "
                "{earliest_credible_year}, or later than the run that observed the file by more "
                "than {future_tolerance_seconds} seconds, is impossible and set aside; an Office "
                "document's creation stamp, as {template_stamp_observers} read it, written to the "
                "second at midnight with {template_stamp_minimum_gap_days} or more days between "
                "it and the same document's own last save, is the template's it was made from, "
                "kept as the document says and not believed, and the document's own last save "
                "is taken where it is earlier than the filesystem's times by more than that "
                "tolerance; "
                "and the source of the time is named with it, unless the file is a placeholder, "
                "whose own times are when it was left."
            ),
            "rationale": (
                "Prefer when a file first existed over what is evidently a copy's time, by an "
                "interpretable rule naming its source. A copy keeps its modification time and "
                "is born when it lands, so a copied file is often born after it was last "
                "modified. Where the birth comes after the modification "
                "time, as in a copy, the modification time is used, and impossible times, "
                "such as a file from 1969, are ignored. A workbook or presentation made from "
                "a blank template carries the template's creation stamp (2006, at midnight "
                "UTC) while its own modification stamp is the real save, so a midnight stamp "
                "years before the document's own save is the template's. A stamp stating a "
                "day alone, and a PDF's dates, are never judged so. Known limit: an untouched "
                "copy of a blank template, saved by nobody, holds the template's stamp as "
                "both its creation and its last save, and cannot be told from a real "
                "document's without a list of known template dates, so it is believed."
            ),
            "parameters": [
                {
                    "name": "earliest_credible_year",
                    "type": "integer",
                    "default": 1990,
                    "minimum": 1970,
                    "maximum": 2100,
                    "description": (
                        "No time before this year is credible. Measured on laboratory "
                        "folders: every time well after it, none near 1970; set it lower "
                        "for older data."
                    ),
                },
                {
                    "name": "future_tolerance_seconds",
                    "type": "integer",
                    "default": 86400,
                    "minimum": 0,
                    "maximum": 31536000,
                    "description": (
                        "How far past the run that observed a file one of its times may lie, "
                        "for a clock's skew between machines."
                    ),
                },
                {
                    "name": "template_stamp_observers",
                    "type": "string_list",
                    "default": ["office", "spreadsheet"],
                    "description": (
                        "The readers of an Office package's own properties whose creation "
                        "stamp may be judged a blank template's; a PDF's dates are never judged. "
                        "An empty list judges none."
                    ),
                },
                {
                    "name": "template_stamp_minimum_gap_days",
                    "type": "integer",
                    "default": 365,
                    "minimum": 1,
                    "maximum": 36500,
                    "description": (
                        "How long before a document's own last save a creation stamp at "
                        "midnight UTC must lie to be judged the template's it was made from. "
                        "Microsoft's blank workbook and presentation are stamped 2006, seventeen "
                        "years and more before documents saved from them now; a year keeps a "
                        "document really begun at midnight and saved within the year."
                    ),
                },
            ],
        },
        {
            "id": "copied_in_bulk",
            "version": "2",
            "predicate": "copied_in_bulk",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "statement": (
                "A folder is raised as copied in bulk where it directly holds {minimum_files} or "
                "more files, at least {minimum_share} of them born after their modification, "
                "their births within {burst_window_seconds} seconds of one another, and where, by "
                "the days {time_rule} gives, a file that {stage_rules} place at a stage other than "
                "{entry_stages} first existed before every file at one of those."
            ),
            "rationale": (
                "Copied in bulk is a real phenomenon to anticipate. A copy gives every file a "
                "new birth and keeps its modification, so a folder copied whole holds files "
                "born after they were made and born together; a birth there is the copy's, "
                "which is what a person reading one needs to know. Copy-paste events should "
                "not beg attention. The copy itself is information (folder_copied_in_bulk), "
                "and a copied folder is asked about only where its times put a later stage "
                "before its raw data."
            ),
            "parameters": [
                {
                    "name": "minimum_files",
                    "type": "integer",
                    "default": 5,
                    "minimum": 2,
                    "maximum": 100000,
                    "description": "How many files with a birth the folder must directly hold.",
                },
                {
                    "name": "minimum_share",
                    "type": "real",
                    "default": 0.8,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "The share of them that must be born after their modification.",
                },
                {
                    "name": "burst_window_seconds",
                    "type": "integer",
                    "default": 600,
                    "minimum": 1,
                    "maximum": 604800,
                    "description": (
                        "How close together their births must be. Measured on two server "
                        "folders: where four in five were born late, ten minutes "
                        "held nearly every such folder's births and a minute most of them."
                    ),
                },
                {
                    "name": "stage_rules",
                    "type": "string_list",
                    "default": [
                        "acquisition_stage_from_format",
                        "pipeline_stage_from_name",
                        "analysis_stage_from_derived_sense",
                    ],
                    "description": "The rules whose conclusions place a file at a stage.",
                },
                {
                    "name": "entry_stages",
                    "type": "string_list",
                    "default": ["raw", "acquisition"],
                    "description": "The stages where data enters.",
                },
                {
                    "name": "time_rule",
                    "type": "string",
                    "default": "earliest_credible_time",
                    "description": "The rule whose conclusion says when a file first existed.",
                },
            ],
        },
        {
            "id": "empty_replicate_is_no_data_point",
            "version": "3",
            "predicate": "empty_among_data",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A file holding no bytes is no data point where a file of its name but for its "
                "numbers holds bytes in its folder or a folder beside it, or a file of its batch "
                "(the same extension and first and last name parts) holds bytes in its folder: "
                "an empty replicate or output, left out of weighing an analysis, and never a note "
                "written as a file name where {notes_as_names} (an extension among "
                "{note_extensions} and a name of {minimum_words} or more words of letters written "
                "with spaces); unless it lies in a folder laid out as an installed program."
            ),
            "rationale": (
                "An empty replicate from a lack of data is not a data point, since it lacks "
                "data; it is excluded from weighing down an analysis where other replicates "
                "from the same condition have acceptable data, or considered an outlier. "
                "Batch pipelines propagate empties through. Information, never a question. "
                "What a note is, read by this rule's own thresholds, the note rule's "
                "defaults. The note thresholds are three copies, one in each of this rule, "
                "content_unreadable and note_written_as_a_file_name: tune the three together, "
                "or a file claimed a note is left out of the data points here all the same."
            ),
            "parameters": [
                {
                    "name": "notes_as_names",
                    "type": "boolean",
                    "default": True,
                    "description": "Whether an empty file whose name is a sentence is a note.",
                },
                *_note_thresholds(),
            ],
        },
        {
            "id": "note_written_as_a_file_name",
            "version": "1",
            "predicate": "note_in_a_file_name",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "An empty file with an extension among {note_extensions} whose name, the "
                "extension set aside, is {minimum_words} or more words of letters written with "
                "spaces and holding no digit is a note, its name its content; unless it lies in a "
                "folder laid out as an installed program."
            ),
            "rationale": (
                "Yes, an empty text file whose name reads as a sentence is a note and its "
                "name is the content, not an empty file. People leave notes this way for the "
                "next reader. A digit is a data-like piece, and a name built from a template "
                "with underscores is a data name. The two thresholds are unmeasured."
            ),
            "parameters": _note_thresholds(),
        },
        {
            "id": "folder_copied_in_bulk",
            "version": "1",
            "predicate": "copied_in_bulk_from_contents",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "folder",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A folder was copied here whole, on the day its files arrived, where it "
                "directly holds {minimum_files} or more files with a birth, at least "
                "{minimum_share} of them born after their modification, their births within "
                "{burst_window_seconds} seconds of one another; unless it is laid out as an "
                "installed program."
            ),
            "rationale": (
                "A copy-paste event needs no large response: the engine explains what "
                "happened without asking for attention. So the copy is a conclusion about the "
                "folder, shown in one sentence, and asked about only where it changes an "
                "order (copied_in_bulk, version 2). The thresholds are the flag's, as "
                "measured."
            ),
            "parameters": [
                {
                    "name": "minimum_files",
                    "type": "integer",
                    "default": 5,
                    "minimum": 2,
                    "maximum": 100000,
                    "description": "How many files with a birth the folder must directly hold.",
                },
                {
                    "name": "minimum_share",
                    "type": "real",
                    "default": 0.8,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "The share of them that must be born after their modification.",
                },
                {
                    "name": "burst_window_seconds",
                    "type": "integer",
                    "default": 600,
                    "minimum": 1,
                    "maximum": 604800,
                    "description": "How close together their births must be.",
                },
            ],
        },
        {
            "id": "folder_copied_by_declared_clocks",
            "version": "1",
            "predicate": "copied_in_bulk_by_declared_clocks",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "folder",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A folder's files were copied in together, on the day an archive's listing says "
                "its copies arrived, where at least {minimum_files} of the files directly in it, "
                "and at least {minimum_share} of them, carry a copy time the listing declared, "
                "all within {burst_window_seconds} seconds of one another; unless it is laid out "
                "as an installed program."
            ),
            "rationale": (
                "An archive's write time and an upload time are a copy's arrival, not the "
                "file's own time, so no rule orders files by them; what they say is that the "
                "folder's files arrived together, which is what folder_copied_in_bulk infers "
                "from births on a disk. Here the archive states it, so one file suffices; the "
                "day-long window is unmeasured."
            ),
            "parameters": [
                {
                    "name": "minimum_files",
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 100000,
                    "description": "How many files with a declared copy time it must hold.",
                },
                {
                    "name": "minimum_share",
                    "type": "real",
                    "default": 0.8,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "The share of its files that must carry one.",
                },
                {
                    "name": "burst_window_seconds",
                    "type": "integer",
                    "default": 86400,
                    "minimum": 1,
                    "maximum": 31536000,
                    "description": "How close together the declared copy times must be.",
                },
            ],
        },
        {
            "id": "roi_archive_from_members",
            "version": "1",
            "predicate": "format_from_archive_members",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["placeholder_from_entry_kind"],
            "statement": (
                "An archive is a {format} where a dive listed every member, each is a file of "
                "{member_extensions}, and each begins with the header {header_observation} "
                "counts, unless the archive is a placeholder."
            ),
            "rationale": (
                "If a Fiji ROI archive can be confidently identified, it should be, by a peek "
                "at its members rather than by its name. Fiji's ROI Manager saves regions as "
                "a ZIP of .roi files, each beginning with ImageJ's header, which a dive reads "
                "in memory and never writes out."
            ),
            "parameters": [
                {
                    "name": "member_extensions",
                    "type": "string_list",
                    "default": ["roi"],
                    "description": "The extensions every member must carry.",
                },
                {
                    "name": "header_observation",
                    "type": "string",
                    "default": "archive.imagej_roi_headers",
                    "description": (
                        "The archive reading's count of members beginning with the program's "
                        "header, which must be every member."
                    ),
                },
                {
                    "name": "format",
                    "type": "string",
                    "default": "fiji_roi_zip",
                    "description": "The format concluded, a sense of the Dictionary's.",
                },
            ],
        },
        {
            "id": "roi_archive_named_but_unlisted",
            "version": "1",
            "predicate": "archive_named_but_unlisted",
            "effect": "flags",
            "tier": "defeasible",
            "level": "file",
            "statement": (
                "An archive whose extension is a {archive_senses} and whose name says "
                "{named_senses} is raised until a dive lists its members."
            ),
            "rationale": (
                "A name like RoiSet.zip is weaker evidence than a listing, so it asks rather "
                "than concludes; the listing settles it, and the question goes quiet once a "
                "dive has made one."
            ),
            "parameters": [
                {
                    "name": "archive_senses",
                    "type": "string_list",
                    "default": ["archive_format"],
                    "description": "The formats an archive's extension matches.",
                },
                {
                    "name": "named_senses",
                    "type": "string_list",
                    "default": ["region_of_interest"],
                    "description": (
                        "What a name says the archive holds, matched or among a conflicting "
                        "word's possible matches."
                    ),
                },
            ],
        },
        {
            "id": "vocabulary_unread_in_folder_outside_programs",
            "version": "1",
            "predicate": "vocabulary_unread_in_scope",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A folder is raised when {minimum_occurrences} or more of its {denominator} "
                "went to the Dictionary and more than {maximum_unbound_share} of them reached "
                "no sense, and marked where the folder is laid out as an installed program or "
                "lies beneath one, whose words are code."
            ),
            "rationale": (
                "The shipped rule, declared again naming the program scope. A folder of a "
                "compiler's headers is illegible to a scientific Dictionary and always will "
                "be, and telling a person to teach it vocabulary there spends their attention "
                "on somebody else's software. Marked and still raised, so the Dashboard can "
                "say what it set aside and why."
            ),
            "parameters": [
                {
                    "name": "denominator",
                    "type": "enum",
                    "choices": ["word_occurrences", "all_kinds"],
                    "default": "word_occurrences",
                    "description": (
                        "Which occurrences count: words alone, or every component but the "
                        "extension. Words, because a date is not an unknown word."
                    ),
                },
                {
                    "name": "maximum_unbound_share",
                    "type": "real",
                    "default": 0.8,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "The share of unread occurrences a folder may have.",
                },
                {
                    "name": "minimum_occurrences",
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 1000000,
                    "description": "How many occurrences a share needs to mean anything.",
                },
            ],
        },
        {
            "id": "experiment_folder_from_contents",
            "version": "3",
            "predicate": "experiment_from_contents",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "folder",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A folder is where one experiment's material is bound together when files in it "
                "are an instrument's acquisitions or are named for one (an acquisition's name, of "
                "{minimum_key_characters} or more letters and digits, held whole inside theirs), "
                "or "
                "its subfolders holding such material are all linked by the acquisition names they "
                "share and are not each complete; and its parent's own files do not hold the same "
                "material, nor are its parent's subfolders one experiment split up. It is an "
                "experiment where acquisitions and files named for them lie beneath, acquisitions "
                "where only acquisitions do, and analysis where only files named for acquisitions "
                "held elsewhere do. A folder whose subfolders, {minimum_items} or more of them, "
                "each hold one acquisition's files written from one template, or "
                "{minimum_alike_items} or more of them hold the same files and folders of files "
                "under names with the same words, and they are not a series of days (dated on "
                "more than one day and saying the same beside what counts, or no more than "
                "{most_folders_a_date} of them on any one date), "
                "holds that run's outputs, and each of those subfolders is a part of it; where "
                "that folder is the only one holding material in the folder above, and the "
                "folder above is not itself bound further up, the folder above is where the "
                "run's material is bound, as analysis where nothing names an acquisition; unless "
                "the folder is laid out as an installed program."
            ),
            "rationale": (
                "Experiment folders are identified first by their contents' structure (raw "
                "and analysed data of the same groups beneath one title), names after, and "
                "the policy is to be tested. An instrument's format says where data enters, "
                "and analysis tools name what they write after the movie they read (a prefix, "
                "a suffix, a second extension), so a folder holding both, and nothing above "
                "it holding them too, is where one experiment's material is bound. Read from "
                "declared formats and names held inside names, never from what a word means. "
                "A batch's per-item output folders, many of them under two runs, were each an "
                "analysis; they are one run's outputs. A tool writing one folder per item "
                "writes the same few files into each (a tracks table, a settings file) and "
                "names the folder after the item; each such folder was an experiment of its "
                "own, and the run's experiment is the folder holding its output folder."
            ),
            "parameters": [
                {
                    "name": "minimum_alike_items",
                    "type": "integer",
                    "default": 11,
                    "minimum": 2,
                    "maximum": 1000,
                    "description": (
                        "The fewest sibling subfolders holding the same files, under names with "
                        "the same words, that make one run's per-item outputs. Measured on "
                        "generated trees: 10 by chance where a condition's days were at most six "
                        "(19 the fewest of a run's items), and none once series of days are read "
                        "as days (27); 11 kept."
                    ),
                },
                {
                    "name": "most_folders_a_date",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 1000,
                    "description": (
                        "The most sibling folders on one date that still read as a series of "
                        "days: a daily series holds one folder a day, two where a day was "
                        "done again, and a run's items share a few days, many to a day; "
                        "unmeasured."
                    ),
                },
                {
                    "name": "minimum_items",
                    "type": "integer",
                    "default": 5,
                    "minimum": 2,
                    "maximum": 1000,
                    "description": (
                        "The fewest per-item subfolders of one template that make one run's "
                        "outputs; unmeasured."
                    ),
                },
                {
                    "name": "minimum_key_characters",
                    "type": "integer",
                    "default": 6,
                    "minimum": 1,
                    "maximum": 200,
                    "description": (
                        "The fewest letters and digits an acquisition's name needs, one of them a "
                        "letter, before another file is matched on it: 01.nd2 names nothing."
                    ),
                },
            ],
        },
        {
            "id": "experiment_folder_from_dated_name",
            "version": "2",
            "predicate": "experiment_from_dated_name",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "folder",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A folder is an experiment where its name holds a date and a word, as the "
                "tokenizer read them, no folder above it has such a name, and it is not one "
                "item's folder among a run's per-item outputs named with the same words as its "
                "fellows; unless the folder is laid out as an installed program."
            ),
            "rationale": (
                "The first guess at an experiment, read second after a folder's contents. A "
                "folder named by a year or a month alone holds experiments, and one inside a "
                "dated folder is part of it. It reads only the dates the tokeniser "
                "recognises, so a day written with a month's word is not yet one. A tool "
                "names each per-item folder after its item, often a dated movie, so each of a "
                "run's item folders was an experiment; one whose fellows are named with other "
                "words keeps its dated name."
            ),
        },
        {
            "id": "replicates_of_one_condition",
            "version": "1",
            "predicate": "same_component_values",
            "effect": "groups",
            "primitive": "equivalent",
            "specialization": "same_condition_as",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
            "statement": (
                "Files are replicates of one condition when, compared within one "
                "{compared_within}, they agree on every component of kind {component_kinds}, in "
                "the "
                "same places for a component of kind {ordered_component_kinds}, leaving out a word "
                "or an indexed label matched to a sense of kind {ignored_theme_kinds} and every "
                "bare "
                "number, and each carries {minimum_components} or more of them, unless a member is "
                "filesystem noise or a placeholder."
            ),
            "rationale": (
                "Grouping has levels, and the first is 'do these two .nd2 files, which look "
                "like raw data, replicates from the same condition in one experiment, go "
                "together?'. A bare number is how an instrument or a person counts movies and "
                "fields; a quantity with its unit is a condition and keeps its place, and the "
                "extension is kept, so a movie and its table are two series."
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
            "id": "experiments_may_belong_together",
            "version": "4",
            "predicate": "experiments_may_belong_together",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "statement": (
                "An experiment's folder is raised with the group it is proposed into, where "
                "experiments (the folders {experiment_rules} concluded on, made one set by "
                "{policy}) are proposed as belonging together where each is among the other's "
                "{neighbours} closest by the words, file names and folder they share (none held "
                "by more than {common_share} of them unless no more than one experiment and its "
                "closest hold it; no word matched to a sense of kind {ignored_theme_kinds}), at "
                "similarity {minimum_similarity} or more and on {minimum_shared} or more different "
                "shared features, a group larger than "
                "{maximum_group_size} cut again more strictly, "
                "and every group raised, each as one question, unless {maximum_groups} (0 for "
                "none) caps them."
            ),
            "rationale": (
                "'do these two experiments go together' is the question; clustering proposes "
                "named groups, never hundreds, approved at the highest level a group appears "
                "and inherited beneath; groups distinguish experiments, not conditions within "
                "one. A proposal is a question: a person's approval, never the clustering, "
                "makes a group. A name, better a dated name, first, then what the folder "
                "holds ('name_first'); and no cap on the groups proposed, the Dashboard "
                "showing 50 at a time. A stage word links no experiments (two unrelated "
                "experiments split into raw, processed and analysis were one proposal named "
                "by a stage), a word in a folder's name and in its files' is one shared "
                "feature, and a small pool's replicate days are no longer cut as common."
            ),
            "parameters": [
                {
                    "name": "ignored_theme_kinds",
                    "type": "string_list",
                    "default": ["stage", "replicate", "connective"],
                    "description": "A word matched to a sense of these kinds links no experiments.",
                },
                {
                    "name": "experiment_rules",
                    "type": "string_list",
                    "default": [
                        "experiment_folder_from_contents",
                        "experiment_folder_from_dated_name",
                    ],
                    "description": "The rule concluding by what a folder holds, then by its name.",
                },
                {
                    "name": "policy",
                    "type": "enum",
                    "choices": ["outermost_evidence", "structure_then_name", "name_first"],
                    "default": "name_first",
                    "description": (
                        "How the two rules' folders become one set of experiments; a name, "
                        "better a dated name, first, then what the folder holds."
                    ),
                },
                {
                    "name": "neighbours",
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 50,
                    "description": "How many closest experiments each may be linked to.",
                },
                {
                    "name": "minimum_similarity",
                    "type": "real",
                    "default": 0.2,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "The least cosine similarity an edge needs.",
                },
                {
                    "name": "minimum_shared",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 50,
                    "description": "The fewest features two linked experiments share.",
                },
                {
                    "name": "maximum_group_size",
                    "type": "integer",
                    "default": 8,
                    "minimum": 2,
                    "maximum": 1000,
                    "description": "A larger group is cut again at a higher similarity.",
                },
                {
                    "name": "maximum_groups",
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "maximum": 5000,
                    "description": (
                        "The most groups raised, the rest counted; 0 raises every group (the "
                        "Dashboard shows 50 at a time)."
                    ),
                },
                {
                    "name": "common_share",
                    "type": "real",
                    "default": 0.25,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "A feature more experiments than this share links nothing.",
                },
            ],
        },
        {
            "id": "unbound_word_in_a_kinds_place",
            "version": "1",
            "predicate": "unbound_word_where_a_kind_stands",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A word nothing matches is raised, for a person to say what it is, where it stands "
                "in the same place as at least {minimum_bound} words matched to one kind, in "
                "{minimum_names} or more names of one folder written from one template, and at "
                "least {agreement} of the matched words in that place share that kind."
            ),
            "rationale": (
                "Ask about one word at a time; one answer for every place it stands. Siblings "
                "are written from a template, so a place in it holds one kind of thing: three "
                "reagents in the second place of a folder's names make the fourth word there "
                "a question about a reagent, asked once with the three shown. The rule raises "
                "and concludes nothing: the word stays unbound until a person says what it "
                "is, and the kind is a hint that follows from the answer. The three "
                "parameters are unmeasured defaults until a person has read a sample of what "
                "they raise: two bound words is the fewest that make a pattern, three "
                "quarters the fewest that let one stray kind not spoil the rest, three names "
                "the fewest that make a template."
            ),
            "parameters": [
                {
                    "name": "minimum_bound",
                    "type": "integer",
                    "default": WORD_QUESTION_DEFAULTS["minimum_bound"],
                    "minimum": 1,
                    "maximum": 20,
                    "description": "How many matched words must stand in the place, on one kind.",
                },
                {
                    "name": "agreement",
                    "type": "real",
                    "default": WORD_QUESTION_DEFAULTS["agreement"],
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": (
                        "The share of the matched words in the place on the leading kind."
                    ),
                },
                {
                    "name": "minimum_names",
                    "type": "integer",
                    "default": WORD_QUESTION_DEFAULTS["minimum_names"],
                    "minimum": 2,
                    "maximum": 1000,
                    "description": "How many names of one folder must share a template.",
                },
            ],
        },
        {
            "id": "experiments_approved_together",
            "version": "1",
            "predicate": "approved_experiment_group",
            "effect": "groups",
            "primitive": "equivalent",
            "respect": "approved_experiment_group",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": _NOT_NOISE_NOR_PLACEHOLDER,
            "statement": (
                "Files are one group of experiments when each lies beneath an experiment of a "
                "group {proposal_rule} proposed and a person approved, unless a member is "
                "filesystem noise or a placeholder."
            ),
            "rationale": (
                "Approve at the highest level a group appears, and everything under it "
                "inherits approval as grouped. The clustering only asks; this rule reads the "
                "answer from the Record and groups every file beneath the approved "
                "experiments together, so an approval is one decision and not one per file."
            ),
            "parameters": [
                {
                    "name": "proposal_rule",
                    "type": "string",
                    "default": "experiments_may_belong_together",
                    "description": "The rule whose proposals a person approves.",
                },
            ],
        },
        *_GROUP_RESOLUTION_RULES,
        {
            "id": "date_neighbourhood_of_a_file",
            "version": "1",
            "predicate": "name_date_neighbourhood",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "file",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A file's name gives it a date neighborhood: a date its day, a month its month, "
                "two or more dates the span between them; a file whose name holds none takes the "
                "nearest dated folder's above it where {inherit_from_folders}; a neighborhood is "
                "never the file's own time; unless it lies in a folder laid out as an installed "
                "program."
            ),
            "rationale": (
                "A date in a folder's or a file's name is a general date neighbourhood, more "
                "descriptive than exact, and different from the file's own date; a folder's "
                "date and a file's own date code are factored into a file's date. The "
                "earliest credible time still reads the file's own clocks."
            ),
            "parameters": [
                {
                    "name": "inherit_from_folders",
                    "type": "boolean",
                    "default": True,
                    "description": "Whether a file with no date draws on its folder's.",
                },
            ],
        },
        {
            "id": "date_neighbourhood_of_a_folder",
            "version": "1",
            "predicate": "name_date_neighbourhood",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "folder",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A folder's name gives it a date neighborhood, a span where it holds two dates, "
                "or the nearest dated folder's above it where {inherit_from_folders}; unless it is "
                "laid out as an installed program."
            ),
            "rationale": (
                "A date in a folder's title is the general date neighbourhood of what it "
                "holds, descriptive rather than exact."
            ),
            "parameters": [
                {
                    "name": "inherit_from_folders",
                    "type": "boolean",
                    "default": False,
                    "description": "Whether a folder with no date draws on the one above it.",
                },
            ],
        },
        {
            "id": "domain_pack_would_read_this_pool",
            "version": "4",
            "predicate": "domain_pack_would_read",
            "effect": "flags",
            "tier": "defeasible",
            "level": "root",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A root is raised with each shipped domain pack the project does not load that "
                "would read {minimum_spellings} or more of the spellings nothing reads in it, "
                "words and extensions each counted once, with at least {minimum_share} of "
                "them or of the items carrying any unread spelling (counted by {share_of}), "
                "or whose instrument's format {minimum_files} or more of its files carry; "
                "one question per pack, the counts its reason. A project loading every pack is "
                "asked instead about each loaded pack no name in the pool spells a word of. Its "
                "questions are asked first, before the group questions, where {asked_first}."
            ),
            "rationale": (
                "Load only the named packs, and propose packs from what the engine finds "
                "('this folder holds ND2 files and buffer names: load microscopy and "
                "biochemistry'). A yes is a confirm on pack:<domain>, which a later run reads "
                "to load it. An instrument's format is evidence enough on its own, so a "
                "folder of ND2 files alone proposes microscopy; and a project on every pack "
                "is asked about the ones reading nothing, accept keeping one and refuse "
                "stopping it. The thresholds are unmeasured. Either share may reach the "
                "least, the spellings' or the entries', since a folder writing many words of "
                "its own can drown a pack reading most of its files, and a folder of many "
                "files of one word drowns one the other way."
            ),
            "parameters": [
                {
                    "name": "minimum_files",
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 1000,
                    "description": (
                        "The fewest files carrying a pack's instrument format to propose it alone."
                    ),
                },
                {
                    "name": "minimum_spellings",
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "The fewest unread spellings a pack must read to be proposed.",
                },
                {
                    "name": "minimum_share",
                    "type": "real",
                    "default": 0.02,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": (
                        "The least share, of the unread spellings or of the items carrying "
                        "one, that the pack reads. The default is version 2's, still unmeasured: "
                        "a pack reading one in fifty is worth one question."
                    ),
                },
                {
                    "name": "share_of",
                    "type": "enum",
                    "choices": ["either", "entries", "spellings"],
                    "default": "either",
                    "description": (
                        "What the share is of: either (whichever is larger), items (files and "
                        "folders carrying an unread spelling), or spellings as version 2 counted."
                    ),
                },
                {
                    # Without this, a pack's question ranks far behind the group questions, whose
                    # words a loaded pack changes. A question whose answer changes the words that
                    # others are asked in is asked before them (the review queue puts it ahead of
                    # every other question asked first).
                    "name": "asked_first",
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Whether its questions lead the list of questions, before the group "
                        "questions asked first: a pack loaded changes the words a group is "
                        "asked in."
                    ),
                },
            ],
        },
        {
            "id": "software_outside_the_catalogue",
            "version": "1",
            "predicate": "software_outside_the_catalogue",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "statement": (
                "A folder is asked whether it is a program where it directly shows "
                "{minimum_evidence} or more of a program's structure (a subfolder named among "
                "{layout_folder_names}, a file with an extension among {program_extensions}, a "
                "file named among {marker_names}), and no program or package the Dictionary "
                "declares, nor a name ending in one of {bundle_name_suffixes}, matches it or a "
                "folder above it; answered per folder, a program or not."
            ),
            "rationale": (
                "Software outside the catalogue is recognised by its folder system and raised "
                "for a question, with 'this is not a program' offered. The answer is the "
                "per-folder program answer. The evidence threshold is unmeasured."
            ),
            "parameters": [
                {
                    "name": "layout_folder_names",
                    "type": "string_list",
                    "default": [
                        "bin",
                        "sbin",
                        "lib",
                        "lib64",
                        "libexec",
                        "include",
                        "plugins",
                        "jars",
                        "Frameworks",
                        "site-packages",
                    ],
                    "description": "Folder names packaging gives a program's parts.",
                },
                {
                    "name": "program_extensions",
                    "type": "string_list",
                    "default": [
                        "so",
                        "dylib",
                        "dll",
                        "a",
                        "o",
                        "lib",
                        "pyd",
                        "class",
                        "jar",
                        "exe",
                        "h",
                        "hpp",
                    ],
                    "description": "Compiled code, libraries and headers, never a script.",
                },
                {
                    "name": "marker_names",
                    "type": "string_list",
                    "default": ["license", "licence", "copying", "version", "changelog"],
                    "description": "A program's papers, compared without their extension.",
                },
                {
                    "name": "bundle_name_suffixes",
                    "type": "string_list",
                    "default": [".app"],
                    "description": "Endings of a folder's name that packaging gives a program.",
                },
                {
                    "name": "minimum_evidence",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 3,
                    "description": "How many of the three kinds of structure it shows.",
                },
            ],
        },
        {
            "id": "miscellaneous_files_to_approve",
            "version": "2",
            "predicate": "miscellaneous_files_to_approve",
            "effect": "flags",
            "tier": "defeasible",
            "level": "folder",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "In a pool where some folder is an experiment, a folder is raised as a person's "
                "miscellaneous files, kept as it is, where it neither is nor lies in a folder "
                "{experiment_rules} concluded on, {minimum_files} or more of the files beneath it "
                "lie outside every such folder and make up at least {minimum_outside_share} of "
                "them, and no folder above it is raised; and, where {organising_folders}, a "
                "folder not in an experiment directly holding {minimum_experiments_held} or more; "
                "unless it is laid out as an installed program."
            ),
            "rationale": (
                "Files users keep on their servers, perhaps unrelated to one experiment or "
                "data collection, are user-specific miscellaneous files they can approve; "
                "keep their structure and never force them into experiments. The outermost "
                "such folder is asked once for all it holds. It may hold an experiment."
            ),
            "parameters": [
                {
                    "name": "experiment_rules",
                    "type": "string_list",
                    "default": [
                        "experiment_folder_from_contents",
                        "experiment_folder_from_dated_name",
                    ],
                    "description": "The rules whose folders are experiments or analyses.",
                },
                {
                    "name": "minimum_files",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "The fewest files a folder holds beneath it to be asked about.",
                },
                {
                    "name": "minimum_outside_share",
                    "type": "real",
                    "default": 0.5,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": (
                        "The least share of a folder's files that must lie outside every "
                        "experiment for the folder to be offered; unmeasured."
                    ),
                },
                {
                    "name": "organising_folders",
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Whether a folder that directly holds several experiments, such as a "
                        "folder for a year or a project, is offered too. Off by default, because "
                        "each such folder would raise a question of its own."
                    ),
                },
                {
                    "name": "minimum_experiments_held",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "How many experiments such a folder holds directly.",
                },
            ],
        },
        {
            "id": "miscellaneous_files_approved",
            "version": "1",
            "predicate": "approved_miscellaneous",
            "effect": "classifies",
            "tier": "defeasible",
            "level": "folder",
            "defeated_by": ["installed_software_in_folder"],
            "statement": (
                "A folder is a person's miscellaneous files where their latest answer on it keeps "
                "it so, and nothing beneath it is moved or regrouped; unless it is laid out as an "
                "installed program."
            ),
            "rationale": (
                "Miscellaneous files are the user's to approve. The claim is their answer, "
                "read from the Record, never the engine's guess."
            ),
        },
        *_DERIVATION_SCOPE_RULES,
        *_SCRIPT_CALL_RULES,
        *_NAMED_FOR_AN_ACQUISITION_RULES,
        *_NAMED_BESIDE_RULES,
        *_STAGE_AFTER_DERIVATION_RULES,
    ],
}
