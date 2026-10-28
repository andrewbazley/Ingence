"""Where each file lies in the experiment above it, for rules that compare within one.

The experiments are the ones the experiment rules concluded, made one set by a policy, exactly as
the clustering of experiments and the group resolutions read them
(:func:`~ingence.rulebook.predicates.experiments.experiment_folders`). The first rule in force, by
identifier, that declares ``experiment_rules`` gives the two rules and the policy. Where no rule
declares them, the two experiment rules under the default policy are taken. A file's experiment is
the nearest such folder above it.

The places are handed to a grouping rule only where one compares names within an experiment
(``compared_within: experiment``), after the classifying phase has said which folders are
experiments. Reference resolution reads them as a plain mapping, a file to its experiment's folder,
and looks for a script's bare file names within the script's own experiment. Nothing here reads a
file or the Record. The module computes everything from the phase's own records.

The module also finds which acquisition a file is named for (:func:`acquisitions_named`). A tool
names what it writes after the movie it read, with words of its own beside it, so a file whose name
holds an acquisition's name whole (its parts in order, as the experiment rules match them) is named
for it. The acquisition is looked for in the file's experiment, or its folder where it lies beneath
none. Where several acquisitions of one name are there, the one whose folder shares the longest
path with the file's wins, and a tie names none. Where a name holds several, the longest held
wins, and a tie names none.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from ingence.core.ids import canonical_json, dictionary_term_id
from ingence.core.schema import EntryType, SourceScope
from ingence.grouping.experiment_links import DEFAULT_POLICY
from ingence.infer.evaluate import parameters_in_force
from ingence.infer.folder_contents import MINIMUM_KEY_CHARACTERS, name_parts
from ingence.infer.subjects import ExperimentPlace, NamedAcquisition
from ingence.rulebook.registry import PredicateNotEvaluableError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ingence.core.schema import BindingAlternative, Rule
    from ingence.infer.subjects import EntryRecords

__all__ = [
    "ACQUISITION_STAGE",
    "DERIVES",
    "EXPERIMENT_RULES",
    "RELATED",
    "acquisitions_named",
    "compares_within_an_experiment",
    "experiment_places",
    "one_name_beside",
    "series_summarised",
    "with_acquisitions_named",
    "with_experiment_places",
    "with_named_beside",
]

#: The stage an acquisition's claim concludes (``acquisition_stage_from_format``).
ACQUISITION_STAGE = "acquisition"


#: The two experiment rules, the structure rule first, where no rule in force names them.
EXPERIMENT_RULES = ("experiment_folder_from_contents", "experiment_folder_from_dated_name")

#: The value of ``compared_within`` that asks for an experiment's scope.
_WITHIN_AN_EXPERIMENT = "experiment"
# A name part's character, as `name_parts` cuts a casefolded name: a whole name inside another
# begins only where a part does.
_PART = re.compile(r"[0-9a-z]")


def _declares(rule: Rule, name: str) -> bool:
    return any(specification.name == name for specification in rule.parameters)


def compares_within_an_experiment(rules: Sequence[Rule]) -> bool:
    """Whether a grouping rule among these compares names within an experiment."""
    return any(
        _declares(rule, "compared_within")
        and parameters_in_force(rule).get("compared_within") == _WITHIN_AN_EXPERIMENT
        for rule in rules
    )


def _experiment_parameters(in_force: Mapping[str, Rule]) -> dict[str, object]:
    """Which experiment rules and policy make the experiments: the first rule's naming them."""
    for _name, rule in sorted(in_force.items()):
        if not _declares(rule, "experiment_rules"):
            continue
        declared = parameters_in_force(rule)
        return {
            "experiment_rules": declared["experiment_rules"],
            "policy": declared["policy"] if _declares(rule, "policy") else DEFAULT_POLICY,
        }
    return {"experiment_rules": list(EXPERIMENT_RULES), "policy": DEFAULT_POLICY}


def experiment_places(
    entries: Sequence[EntryRecords], in_force: Mapping[str, Rule]
) -> dict[str, ExperimentPlace]:
    """Each file beneath an experiment, by identifier, and where it lies in it.

    ``entries`` must carry the classifying rules' claims. A file beneath no experiment is absent.
    Computed once per folder, walking up to the nearest experiment and remembering the way.
    """
    from ingence.rulebook.predicates.experiments import experiment_folders

    parameters = _experiment_parameters(in_force)
    declared = parameters["experiment_rules"]
    named: tuple[object, ...] = (
        (declared,)
        if isinstance(declared, str)
        else tuple(declared)
        if isinstance(declared, (list, tuple))
        else ()
    )
    experiment_rules = frozenset(str(name) for name in named)
    try:
        units, folders = experiment_folders(entries, parameters)
    except PredicateNotEvaluableError:
        # A rule naming other than the two experiment rules names no experiment.
        return {}
    if not units:
        return {}
    parents = {held.entry.entry_id: held.entry.parent_entry_id for held in entries}
    by_id = {held.entry.entry_id: held for held in entries}
    # Each folder's place for what lies directly inside it: the experiment and the way down.
    known: dict[str, ExperimentPlace | None] = {}
    for unit in units:
        claims = tuple(
            sorted(
                claim.claim_id
                for claim in folders[unit].claims
                if claim.rule_id in experiment_rules
                and str(claim.resolution_status) == "resolved"
                and claim.object_reference is not None
            )
        )
        known[unit] = ExperimentPlace(experiment=unit, claims=claims)

    def place_inside(folder: str | None) -> ExperimentPlace | None:
        walked: list[str] = []
        cursor = folder
        while cursor is not None and cursor not in known:
            walked.append(cursor)
            cursor = parents.get(cursor)
        found = known.get(cursor) if cursor is not None else None
        for step in reversed(walked):
            if found is not None:
                found = replace(found, between=(*found.between, by_id[step]))
            known[step] = found
        return found

    places: dict[str, ExperimentPlace] = {}
    for held in entries:
        if held.entry.entry_type is EntryType.DIRECTORY:
            continue
        place = place_inside(held.entry.parent_entry_id)
        if place is not None:
            places[held.entry.entry_id] = place
    return places


def with_experiment_places(
    entries: Sequence[EntryRecords], places: Mapping[str, ExperimentPlace]
) -> tuple[EntryRecords, ...]:
    """Hand each file beneath an experiment where it lies in it, and leave the rest as they are."""
    return tuple(
        replace(held, experiment_place=places[held.entry.entry_id])
        if held.entry.entry_id in places
        else held
        for held in entries
    )


def _acquisition_claim(held: EntryRecords) -> str | None:
    """The resolved classification concluding a file an acquisition, if one stands.

    A file whose type is at the rung where data enters a workflow in force also counts: it is where
    that workflow's data begins, and the claim cited is its format's, the record the type rests on.
    The safeguards of what is named for an acquisition (the fewest characters, the nearest match, a
    tie abstaining, a backup left out) are the caller's and stand unchanged.
    """
    for claim in sorted(held.claims, key=lambda claim: claim.claim_id):
        if (
            claim.predicate.startswith("classifies.")
            and str(claim.resolution_status) == "resolved"
            and claim.object_reference == ACQUISITION_STAGE
        ):
            return claim.claim_id
    extension = (held.entry.compound_extension or held.entry.extension or "").casefold()
    bare = (held.entry.extension or "").casefold()
    if not extension:
        return None
    entering = {
        kind
        for workflow in getattr(held, "workflows", ())
        for kind in getattr(workflow, "entering_types", ())
    }
    if extension not in entering and bare not in entering:
        return None
    for claim in sorted(held.claims, key=lambda claim: claim.claim_id):
        if (
            claim.predicate.startswith("classifies.")
            and "format" in claim.predicate
            and str(claim.resolution_status) == "resolved"
        ):
            return claim.claim_id
    return None


def _parts(held: EntryRecords) -> tuple[str, ...]:
    """A name's parts without its extension, a compound one whole (``kelt_001.ome.tif``)."""
    return name_parts(held.entry.name, held.entry.compound_extension or held.entry.extension)


def _folders_of(path: str) -> tuple[str, ...]:
    return tuple(path.split("/")[:-1])


def _shared(first: tuple[str, ...], second: tuple[str, ...]) -> int:
    shared = 0
    for left, right in zip(first, second, strict=False):
        if left != right:
            break
        shared += 1
    return shared


def acquisitions_named(
    entries: Sequence[EntryRecords],
    places: Mapping[str, ExperimentPlace],
    *,
    minimum_key_characters: int = MINIMUM_KEY_CHARACTERS,
    annotation_senses: frozenset[str] = frozenset(),
    image_formats: frozenset[str] = frozenset(),
    not_made_from_senses: frozenset[str] = frozenset(),
    levels_up: int = 0,
) -> dict[str, NamedAcquisition]:
    """Each acquisition, and each file named for one, by identifier, with the acquisition.

    A whole name not found in the file's own scopes is looked for upward. A viewer's splits sit in
    an analysis branch and their movie in a raw branch, under a common folder some levels above
    (``proj/raw/<day>/zeph.nd2`` and ``proj/analysis/set/sub/zeph.nd2 - C=0.tif``), where each
    branch is an experiment of its own. The file's own folder, then each folder above it, up to
    ``levels_up`` folders and never the pool's root (its first level is a person's), is asked how
    many files of that whole name it holds anywhere beneath it. The nearest holding one is the
    source, and the nearest holding two or more names none. A file of another experiment is taken
    only where its experiment folder agrees with a folder between the two (two days of one series
    never cross; the agreeing day breaks a tie). The search reads one index of whole names by the
    folders above them, built once.

    An annotation named for an image beside it is named for that image. Such a file has a name that
    is an image's, in its folder, and one word at either end bound to a sense of
    ``annotation_senses`` (``vol07_labels.nii`` beside ``vol07.nii``). The image is a file of a
    format in ``image_formats``, whatever its stage. Where that setting is empty, no annotation is
    read so.

    A backup is named for nothing. A file whose extension or last word is spelled only by senses of
    ``not_made_from_senses`` (``zeph_table.csv.bak``, ``kelt_fit.py.orig``) holds its file's whole
    name and was not made from it. The copy rules read it.

    ``entries`` must carry the classifying rules' claims. ``places`` says which experiment a file
    lies in. A file beneath none is looked at within its folder, and for another file's whole name,
    within its folder, the folders beside it and their parent.
    """

    def scope_of(held: EntryRecords) -> str:
        place = places.get(held.entry.entry_id)
        return f"e:{place.experiment}" if place is not None else f"f:{held.entry.parent_entry_id}"

    # A whole name, extension included, is looked for beside the file's folder too, and only for a
    # file beneath no experiment. Otherwise a split in one day's experiment would be given the next
    # day's movie. A viewer's channel splits in ``analysis/`` name a movie in ``movies/`` beside it,
    # or in the folder above (``proj/zeph.tif`` and ``proj/analysis/zeph.tif - C=0.tif``), and a
    # file's own folder never held the two. A file is found under its experiment, under its
    # folder's parent (by a file in that folder or one beside it) and under its own folder (by a
    # file in a folder within it). A file beneath no experiment looks under its folder's parent and
    # under its own folder, so it sees its own folder, the folders beside it, their parent and the
    # folders within its own (``p2/zeph.tif - C=1.tif`` and ``p2/movies/zeph.tif``). It never sees
    # the whole tree.
    folder_parent = {
        held.entry.entry_id: held.entry.parent_entry_id
        for held in entries
        if held.entry.entry_type is EntryType.DIRECTORY
    }

    def beside_scope(held: EntryRecords) -> str:
        parent = held.entry.parent_entry_id
        return f"w:{folder_parent.get(parent or '') or parent}"

    def looked_in(held: EntryRecords) -> tuple[str, ...]:
        """Where a file looks for another's whole name."""
        place = places.get(held.entry.entry_id)
        if place is not None:
            return (f"e:{place.experiment}",)
        return (beside_scope(held), f"w:{held.entry.parent_entry_id}")

    def found_in(held: EntryRecords) -> tuple[str, ...]:
        """Where a file is found by its whole name."""
        place = places.get(held.entry.entry_id)
        own = (beside_scope(held), f"w:{held.entry.parent_entry_id}")
        return (f"e:{place.experiment}", *own) if place is not None else own

    # What each spelling the Dictionary declares may mean, read once, where a rule names the
    # senses a backup is spelled by.
    spelled_by: dict[str, set[str]] = {}
    if not_made_from_senses:
        spellings = next((held.spellings for held in entries if held.spellings), {})
        for term, surfaces in spellings.items():
            for surface in surfaces:
                spelled_by.setdefault(surface.casefold(), set()).add(term)

    files = [held for held in entries if held.entry.entry_type is not EntryType.DIRECTORY]
    # By scope, by the number of parts, every acquisition's name parts with the acquisitions of
    # that name. Each run of a file's consecutive parts is looked up whole. An index by the first
    # part would compare every file with every acquisition of one plate.
    index: dict[str, dict[int, dict[tuple[str, ...], list[tuple[EntryRecords, str]]]]] = {}
    named: dict[str, NamedAcquisition] = {}
    # A name holding another file's whole name names that file. The extensions an experiment's
    # files carry are collected, so a name embedding "<name>.<extension>" is looked up whole and
    # never scanned.
    extensions: dict[str, set[str]] = {}
    acquired: dict[str, set[str]] = {}
    for held in files:
        scope = scope_of(held)
        if held.entry.extension:
            for whole_scope in found_in(held):
                extensions.setdefault(whole_scope, set()).add(held.entry.extension.casefold())
        claim = _acquisition_claim(held)
        if claim is None:
            continue
        parts = _parts(held)
        joined = "".join(parts)
        named[held.entry.entry_id] = NamedAcquisition(
            acquisition=held.entry.entry_id, claim=claim, characters=len(joined)
        )
        if held.entry.extension:
            acquired.setdefault(scope, set()).add(held.entry.extension.casefold())
        if len(joined) < minimum_key_characters or not any(c.isalpha() for c in joined):
            continue
        index.setdefault(scope, {}).setdefault(len(parts), {}).setdefault(parts, []).append(
            (held, claim)
        )
    # The whole names some file holds, by experiment. Only the files of those names are indexed by
    # their whole name, never every file's.
    embeds: dict[str, list[str]] = {}
    wanted: dict[str, set[str]] = {}
    for held in files:
        if held.entry.entry_id in named:
            continue
        scopes = looked_in(held)
        embedded = _embedded_names(
            held, set().union(*(extensions.get(scope, set()) for scope in scopes))
        )
        if embedded:
            embeds[held.entry.entry_id] = embedded
            for scope in scopes:
                wanted.setdefault(scope, set()).update(embedded)
    whole: dict[str, dict[str, list[EntryRecords]]] = {}
    # The same files by their own folder. A split's movie is almost always beside it, looked up at
    # once, and only a split whose folder holds none is compared with the files of its other
    # scopes.
    in_folder: dict[tuple[str | None, str], list[EntryRecords]] = {}
    for held in files:
        spelled = _spelled(held.entry.name)
        holding = [scope for scope in found_in(held) if spelled in wanted.get(scope, ())]
        for scope in holding:
            whole.setdefault(scope, {}).setdefault(spelled, []).append(held)
        if holding:
            in_folder.setdefault((held.entry.parent_entry_id, spelled), []).append(held)
    # Each file's two readings: another file whose whole name it holds, and the acquisition its
    # stem names. The first stands only where it reaches a movie the stem does not contradict.
    made_from: dict[str, str] = {}
    stemmed: dict[str, NamedAcquisition] = {}
    by_id = {held.entry.entry_id: held for held in files}
    # The images of each folder by their name's parts, where an annotation is read.
    images: dict[tuple[str | None, tuple[str, ...]], list[str]] = {}
    if annotation_senses and image_formats:
        for held in files:
            if _concluded(held, image_formats):
                images.setdefault((held.entry.parent_entry_id, _parts(held)), []).append(
                    held.entry.entry_id
                )
    annotated: set[str] = set()
    upward: list[EntryRecords] = []
    for held in files:
        if held.entry.entry_id in named:
            continue
        scope = scope_of(held)
        embedded = embeds.get(held.entry.entry_id, [])
        # Only the names this file embeds are looked up, in each scope it looks in, and whether
        # another file holds each is asked before any is compared. The lookup never covers every
        # name a scope holds, which would compare every file with every name.
        scopes = looked_in(held)
        matched = [
            name
            for name in embedded
            if any(
                other.entry.entry_id != held.entry.entry_id
                for whole_scope in scopes
                for other in whole.get(whole_scope, {}).get(name, ())[:2]
            )
        ]
        if matched:
            longest = max(len(name) for name in matched)
            (name,) = {name for name in matched if len(name) == longest}
            if spelled_by and _a_backup_of(
                held, _after_whole_name(held, name), name, spelled_by, not_made_from_senses
            ):
                continue
            nearest = _nearest_holding(held, name, scopes, whole, in_folder, places)
            if nearest is not None:
                made_from[held.entry.entry_id] = nearest.entry.entry_id
        elif images:
            image = _annotated_image(held, images, annotation_senses)
            if image is not None:
                made_from[held.entry.entry_id] = image
                annotated.add(held.entry.entry_id)
        stem = _stem_match(held, index.get(scope))
        if levels_up and not matched and stem is None and held.entry.entry_id not in made_from:
            upward.append(held)
        # A longer whole name in an acquisition's format that matches no file here names none. A
        # shorter one leaves the stem's acquisition.
        acquisition_like = [
            name
            for name in embedded
            if name not in matched and name.rpartition(".")[2] in acquired.get(scope, set())
        ]
        if acquisition_like and (
            stem is None or max(len(_letters(name)) for name in acquisition_like) > stem.characters
        ):
            continue
        if stem is not None:
            if not matched and spelled_by:
                acquisition = by_id[stem.acquisition]
                if _a_backup_of(
                    held,
                    _after_parts(_parts(held), _parts(acquisition)),
                    acquisition.entry.name,
                    spelled_by,
                    not_made_from_senses,
                ):
                    continue
            stemmed[held.entry.entry_id] = stem

    found_up = _found_upward(
        upward,
        files,
        {
            held.entry.entry_id: held
            for held in entries
            if held.entry.entry_type is EntryType.DIRECTORY
        },
        places,
        levels_up=levels_up,
        minimum_key_characters=minimum_key_characters,
        backup=lambda held, name: (
            bool(spelled_by)
            and _a_backup_of(
                held, _after_whole_name(held, name), name, spelled_by, not_made_from_senses
            )
        ),
    )
    for identifier, (source, _) in found_up.items():
        made_from[identifier] = source
    settled: dict[str, NamedAcquisition | None] = {}
    # A file whose whole name another's holds keys a group of its own where it reaches no
    # acquisition. An image viewer appends to the whole name of what it opened
    # (``zeph_007.tif - C=0.tif``), and the file opened need not be an instrument's own format.
    roots: dict[str, NamedAcquisition] = {}
    # The longest whole name, extension included, by which a file was reached, for each file keying
    # a group. An acquisition too short to be matched by its stem alone is still in its own group
    # where a file holds its whole name.
    reached: dict[str, int] = {}

    def rooted(identifier: str) -> NamedAcquisition:
        """A file keying its own group: the file whose whole name another holds, and no
        acquisition's, so no claim concludes what it is."""
        if identifier not in roots:
            roots[identifier] = NamedAcquisition(
                acquisition=identifier,
                claim="",
                characters=len(_letters(_spelled(by_id[identifier].entry.name))),
            )
        return roots[identifier]

    def resolved(identifier: str, seen: frozenset[str]) -> NamedAcquisition | None:
        """A file's movie, through the file whose whole name it holds where that reaches one the
        stem does not contradict, else the stem's. Where neither reaches an acquisition, the result
        is the file whose whole name it holds, as the first file in the chain."""
        if identifier in named:
            return named[identifier]
        if identifier in settled:
            return settled[identifier]
        stem = stemmed.get(identifier)
        source = made_from.get(identifier)
        found = stem
        if source is not None and source not in seen:
            root = resolved(source, seen | {identifier})
            if root is None and stem is None:
                root = rooted(source)
            if root is not None and (
                stem is None
                or stem.acquisition == root.acquisition
                or stem.characters < (named.get(root.acquisition) or root).characters
            ):
                found = NamedAcquisition(
                    acquisition=root.acquisition,
                    claim=root.claim,
                    characters=len(_letters(_spelled(by_id[source].entry.name))),
                    source="" if source == root.acquisition else source,
                    annotates=source if identifier in annotated else "",
                    levels_up=found_up[identifier][1] if identifier in found_up else 0,
                )
                reached[root.acquisition] = max(reached.get(root.acquisition, 0), found.characters)
        settled[identifier] = found
        return found

    for identifier in sorted(set(made_from) | set(stemmed)):
        chained = resolved(identifier, frozenset())
        if chained is not None:
            named[identifier] = chained
    for identifier in sorted(roots):
        named.setdefault(identifier, roots[identifier])
    for identifier in sorted(reached):
        held_name = named[identifier]
        if held_name.acquisition == identifier and held_name.characters < reached[identifier]:
            named[identifier] = replace(held_name, characters=reached[identifier])
    return named


def series_summarised(
    entries: Sequence[EntryRecords],
    *,
    summary_senses: frozenset[str],
    table_formats: frozenset[str],
    minimum_series_members: int,
) -> dict[str, str]:
    """Each numbered file of a series and each table named for it, by identifier, with the class
    they make.

    A series is the files of one folder whose names agree but for a number at their end
    (``zeph_001.nd2`` ... ``zeph_010.nd2``), ``minimum_series_members`` or more of them. A table
    named for it is a file of a format in ``table_formats`` in that folder whose name is the
    series' shared stem and one word, at either end, bound to a sense in ``summary_senses``
    (``zeph_results.xlsx``). The class's key names the folder, the stem and every such table, so a
    proposing rule pairs each member with each table and never two members.
    """
    if not summary_senses or not table_formats:
        return {}
    files = [held for held in entries if held.entry.entry_type is not EntryType.DIRECTORY]
    series: dict[tuple[str | None, tuple[str, ...]], list[str]] = {}
    for held in files:
        parts = _parts(held)
        if len(parts) >= 2 and parts[-1].isdigit():
            series.setdefault((held.entry.parent_entry_id, parts[:-1]), []).append(
                held.entry.entry_id
            )
    tables: dict[tuple[str | None, tuple[str, ...]], list[str]] = {}
    for held in files:
        parts = _parts(held)
        if len(parts) < 2 or not _concluded(held, table_formats):
            continue
        for word, rest in ((parts[-1], parts[:-1]), (parts[0], parts[1:])):
            place = (held.entry.parent_entry_id, rest)
            if len(series.get(place, ())) >= minimum_series_members and _means(
                held, word, summary_senses
            ):
                tables.setdefault(place, []).append(held.entry.entry_id)
                break
    keyed: dict[str, str] = {}
    for place in sorted(tables, key=lambda held: (str(held[0]), held[1])):
        folder, stem = place
        key = canonical_json(
            {"folder": folder, "series": list(stem), "summaries": sorted(tables[place])}
        )
        for identifier in (*series[place], *tables[place]):
            keyed.setdefault(identifier, key)
    return keyed


#: The two lists of pairs a class of one name in sibling folders keys: those the folders' words
#: direct, and the rest, once each.
DERIVES = "derives"
RELATED = "related"
#: A folder's name as a layout numbers it: ``day1`` and ``day2`` are one shape.
_NUMBERS = re.compile(r"[0-9]+")


def one_name_beside(
    entries: Sequence[EntryRecords],
    places: Mapping[str, ExperimentPlace],
    *,
    source_senses: frozenset[str],
    product_senses: frozenset[str],
    maximum_folders: int,
    ignored_roles: frozenset[str] = frozenset(),
    apart_senses: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Each file whose whole name a file in a sibling folder also has, by identifier, with the
    class they make.

    Sibling folders share a parent (``groundtruth/a.tif`` and ``rawimages/a.tif``), never more of
    the tree. The identity of a name widens from one folder to its siblings only. A name in more
    than ``maximum_folders`` of them is the layout's common name (each sample's ``image.tif``) and
    not one thing held twice, and forms nothing.

    The key names the pairs. One list of pairs runs from a file in a folder whose own words are
    bound to a sense of ``source_senses`` and none of ``product_senses`` to one in a folder whose
    words say the reverse (``derives``). The other holds every other pair once, from the end whose
    identifier sorts first (``related``), and only where the name carries an identifier, a number
    or a word no sense binds (``results.csv`` in three people's folders identifies nothing). The
    function forms no pair of two folders whose names agree but for their numbers and their words of
    ``apart_senses`` (``day1``, ``day2``; ``train``, ``test``; ``control``, ``treated``). Those are
    replicates, splits or conditions of one layout, each holding different things numbered alike.
    It forms no pair of two experiments. It forms no pair with a file that a standing
    classification gives a role of ``ignored_roles`` (a README, each folder's own).
    """
    if maximum_folders < 2:
        return {}
    folders = {
        held.entry.entry_id: held
        for held in entries
        if held.entry.entry_type is EntryType.DIRECTORY
    }
    by_name: dict[tuple[str, str], list[EntryRecords]] = {}
    for held in entries:
        if held.entry.entry_type is EntryType.DIRECTORY:
            continue
        parent = folders.get(held.entry.parent_entry_id or "")
        grandparent = None if parent is None else parent.entry.parent_entry_id
        if grandparent is None or (ignored_roles and _concluded(held, ignored_roles)):
            continue
        by_name.setdefault((grandparent, _spelled(held.entry.name)), []).append(held)
    keyed: dict[str, str] = {}
    reads_as: dict[str, str] = {}
    for (grandparent, name), held_list in sorted(by_name.items()):
        parents = {held.entry.parent_entry_id for held in held_list}
        if len(held_list) < 2 or len(parents) != len(held_list) or len(parents) > maximum_folders:
            continue
        for held in held_list:
            folder = str(held.entry.parent_entry_id)
            if folder not in reads_as:
                record = folders[folder]
                source = _words_mean(record, source_senses)
                product = _words_mean(record, product_senses)
                reads_as[folder] = (
                    "source"
                    if source and not product
                    else "product"
                    if product and not source
                    else ""
                )
        derives: list[list[str]] = []
        related: list[list[str]] = []
        ordered = sorted(held_list, key=lambda held: held.entry.entry_id)
        identified = _carries_an_identifier(ordered[0])
        for at, first in enumerate(ordered):
            for second in ordered[at + 1 :]:
                if _apart(first, second, folders, places, apart_senses):
                    continue
                one, other = (
                    reads_as[str(first.entry.parent_entry_id)],
                    reads_as[str(second.entry.parent_entry_id)],
                )
                if (one, other) == ("source", "product"):
                    derives.append([first.entry.entry_id, second.entry.entry_id])
                elif (one, other) == ("product", "source"):
                    derives.append([second.entry.entry_id, first.entry.entry_id])
                elif identified:
                    related.append([first.entry.entry_id, second.entry.entry_id])
        if not derives and not related:
            continue
        key = canonical_json(
            {
                "beside": grandparent,
                "name": name,
                DERIVES: sorted(derives),
                RELATED: sorted(related),
            }
        )
        for held in held_list:
            keyed[held.entry.entry_id] = key
    return keyed


def _apart(
    first: EntryRecords,
    second: EntryRecords,
    folders: Mapping[str, EntryRecords],
    places: Mapping[str, ExperimentPlace],
    apart_senses: frozenset[str] = frozenset(),
) -> bool:
    """Whether two files of one name in sibling folders are kept apart.

    They are kept apart in three cases. Their folders' names agree but for their numbers and their
    words bound only to ``apart_senses``. Or both names carry such words and the words differ
    (``control_images`` and ``treated_masks``, ``train`` and ``test_masks``), while
    ``control_images`` and ``control_masks`` stay together. Or each lies in an experiment and the
    two differ.
    """
    records = [folders[str(held.entry.parent_entry_id)] for held in (first, second)]
    if len({_shape(record, apart_senses) for record in records}) == 1:
        return True
    if apart_senses:
        words = [
            frozenset(
                part
                for part in name_parts(record.entry.name, None)
                if _means(record, part, apart_senses)
            )
            for record in records
        ]
        if words[0] and words[1] and words[0] != words[1]:
            return True
    experiments = [places.get(held.entry.entry_id) for held in (first, second)]
    return (
        experiments[0] is not None
        and experiments[1] is not None
        and experiments[0].experiment != experiments[1].experiment
    )


def _shape(folder: EntryRecords, apart_senses: frozenset[str]) -> tuple[str, ...]:
    """A folder's name as a layout numbers it: its parts, numbers read as one, and a word bound only
    to ``apart_senses`` left out (``train``, ``test`` and ``val`` are one shape)."""
    return tuple(
        _NUMBERS.sub("#", part)
        for part in name_parts(folder.entry.name, None)
        if not (apart_senses and _means(folder, part, apart_senses))
    )


def _carries_an_identifier(held: EntryRecords) -> bool:
    """Whether a name holds a number, or a word the Dictionary binds to no sense."""
    words: dict[str, list[str]] = {}
    for component in held.components:
        if component.source_scope is not SourceScope.NAME:
            continue
        if component.component_type in _IDENTIFYING_KINDS:
            return True
        if component.component_type == "word":
            words[component.component_id] = []
    for binding in held.bindings:
        if binding.component_id in words:
            words[binding.component_id].append(str(binding.resolution_status))
    return any(
        statuses and set(statuses) == {"examined_no_evidence"} for statuses in words.values()
    )


#: The kinds of a name's components that identify one thing among others: a number, an indexed
#: label (``vorn7``), a date.
_IDENTIFYING_KINDS = frozenset({"number", "indexed", "date"})


def _words_mean(held: EntryRecords, senses: frozenset[str]) -> bool:
    """Whether any word of a name is bound only to senses in ``senses``."""
    words = {
        str(component.raw_value or component.normalized_value).casefold()
        for component in held.components
        if component.component_type == "word"
    }
    return any(_means(held, word, senses) for word in sorted(words))


def _concluded(held: EntryRecords, values: frozenset[str]) -> bool:
    """Whether a standing classification concludes the file one of ``values`` (a format)."""
    return any(
        claim.predicate.startswith("classifies.")
        and str(claim.resolution_status) == "resolved"
        and claim.object_reference in values
        for claim in held.claims
    )


def _annotated_image(
    held: EntryRecords,
    images: Mapping[tuple[str | None, tuple[str, ...]], list[str]],
    senses: frozenset[str],
) -> str | None:
    """The image beside a file whose name is the image's and one word meaning an annotation, at
    either end. The result is None where there is none, or two equally (``a.tif`` and ``a.png``).

    The word is read from what the Dictionary bound it to: a binding resolved to one of
    ``senses``, or one standing between senses that are all of them.
    """
    parts = _parts(held)
    if len(parts) < 2:
        return None
    for word, rest in ((parts[-1], parts[:-1]), (parts[0], parts[1:])):
        found = images.get((held.entry.parent_entry_id, rest), [])
        if len(found) == 1 and found[0] != held.entry.entry_id and _means(held, word, senses):
            return found[0]
    return None


def _means(held: EntryRecords, word: str, senses: frozenset[str]) -> bool:
    """Whether one word of a name, as its part is spelled, is bound only to senses in ``senses``."""
    spelled = {
        component.component_id
        for component in held.components
        if component.component_type == "word"
        and (component.raw_value or component.normalized_value).casefold() == word
    }
    for binding in held.bindings:
        if binding.component_id not in spelled:
            continue
        if str(binding.resolution_status) == "resolved" and binding.term in senses:
            return True
        if (
            str(binding.resolution_status) == "examined_conflicting"
            and binding.alternatives
            and all(_one_of(alternative, senses) for alternative in binding.alternatives)
        ):
            return True
    return False


def _one_of(alternative: BindingAlternative, senses: frozenset[str]) -> bool:
    """Whether a sense a word stood between is one of ``senses``: an alternative carries its
    sense's identifier, which its term, scope and origin make."""
    origin = getattr(alternative.origin, "value", alternative.origin)
    return any(
        dictionary_term_id(term=term, scope=alternative.scope, origin=str(origin))
        == alternative.term_id
        for term in senses
    )


def _spelled(name: str) -> str:
    """A whole file name as names are compared: case folded, spaces and hyphens read as
    underscores (a tool writing ``zeph kelt 120min-1`` as ``zeph_kelt_120min_1``)."""
    return name.casefold().replace(" ", "_").replace("-", "_")


def _letters(name: str) -> str:
    """A name's letters and digits, as a match's length is counted."""
    return "".join(character for character in name if character.isalnum())


def _nearest(held: EntryRecords, others: Sequence[EntryRecords]) -> EntryRecords | None:
    """The one of ``others`` whose folder shares the longest path with the file's; None on a tie.

    The function counts in one pass and never sorts."""
    folders = _folders_of(held.entry.relative_path)
    best: EntryRecords | None = None
    most, ties = -1, 0
    for other in others:
        shared = _shared(folders, _folders_of(other.entry.relative_path))
        if shared > most:
            best, most, ties = other, shared, 1
        elif shared == most:
            ties += 1
    return best if ties == 1 else None


def _nearest_holding(
    held: EntryRecords,
    name: str,
    scopes: Sequence[str],
    whole: Mapping[str, Mapping[str, list[EntryRecords]]],
    in_folder: Mapping[tuple[str | None, str], list[EntryRecords]],
    places: Mapping[str, ExperimentPlace],
) -> EntryRecords | None:
    """The file of a whole name a file's name holds, nearest it: the one in its own folder where
    that holds one (two there are a tie), else the nearest in the scopes it looks in. It is never
    one of another experiment than the file's."""
    here = [
        other
        for other in in_folder.get((held.entry.parent_entry_id, name), ())
        if other.entry.entry_id != held.entry.entry_id
    ]
    if here:
        return here[0] if len(here) == 1 else None
    own = places.get(held.entry.entry_id)
    others: dict[str, EntryRecords] = {}
    for scope in scopes:
        for other in whole.get(scope, {}).get(name, ()):
            place = places.get(other.entry.entry_id)
            if other.entry.entry_id == held.entry.entry_id or (
                own is not None and place is not None and place.experiment != own.experiment
            ):
                continue
            others[other.entry.entry_id] = other
    return _nearest(held, [others[identifier] for identifier in sorted(others)])


def _found_upward(
    lookers: Sequence[EntryRecords],
    files: Sequence[EntryRecords],
    folders: Mapping[str, EntryRecords],
    places: Mapping[str, ExperimentPlace],
    *,
    levels_up: int,
    minimum_key_characters: int,
    backup: Callable[[EntryRecords, str], bool],
) -> dict[str, tuple[str, int]]:
    """Each looker whose whole name was found upward, with its source and how many folders above
    its own; see :func:`acquisitions_named`.

    A source in another experiment is taken only where its experiment folder agrees with a folder
    between the looker and the folder they share. The folders agree on the same name, or the same
    date and words (``raw/2024-03-06_kelt`` with ``analysis/2024-03-06_kelt``), so a split made on
    one day never takes the movie from the day before, and where two days hold one name the agreeing
    day breaks the tie. A held name is sought only where its stem has the rule's minimum letters and
    digits, one a letter, so a name's tail (``008.nd2`` of ``MAX_kelt_008.nd2``) names nothing.

    The work is linear. It reads the names the lookers hold, with every extension the pool's files
    carry. It builds one index, for each file of such a name, of the folders above it (to the
    pool's first level) with how many files of the name each holds beneath it, all together and by
    the signature of each file's experiment folder. Then, for each looker, it makes a few lookups
    per folder it climbs.
    """
    if not lookers or levels_up < 0:
        return {}
    folder_parent = {identifier: held.entry.parent_entry_id for identifier, held in folders.items()}
    extensions = {held.entry.extension.casefold() for held in files if held.entry.extension}
    held_names: dict[str, list[str]] = {}
    for held in lookers:
        names = [
            name
            for name in set(_embedded_names(held, extensions) or ())
            if _long_enough(name.rpartition(".")[0], minimum_key_characters)
        ]
        if names:
            held_names[held.entry.entry_id] = sorted(names, key=lambda name: (-len(name), name))
    if not held_names:
        return {}
    wanted = {name for names in held_names.values() for name in names}

    depths: dict[str, int] = {}

    def depth_of(folder: str) -> int:
        """A folder's depth below the pool's root (the root is 0)."""
        if folder not in depths:
            steps, above = 0, folder_parent.get(folder)
            while above is not None:
                above, steps = folder_parent.get(above), steps + 1
            depths[folder] = steps
        return depths[folder]

    signatures: dict[str, tuple[object, ...]] = {}

    def signature(folder: str) -> tuple[object, ...]:
        if folder not in signatures:
            record = folders.get(folder)
            signatures[folder] = () if record is None else _folder_signature(record)
        return signatures[folder]

    beneath: dict[tuple[str, str], tuple[int, str]] = {}
    agreeing: dict[tuple[str, str, tuple[object, ...]], tuple[int, str]] = {}
    for held in files:
        spelled = _spelled(held.entry.name)
        if spelled not in wanted:
            continue
        place = places.get(held.entry.entry_id)
        key = signature(place.experiment) if place is not None else ()
        folder = held.entry.parent_entry_id
        while folder is not None and depth_of(folder) >= 1:
            count, first = beneath.get((folder, spelled), (0, held.entry.entry_id))
            beneath[(folder, spelled)] = (count + 1, first)
            if key:
                count, first = agreeing.get((folder, spelled, key), (0, held.entry.entry_id))
                agreeing[(folder, spelled, key)] = (count + 1, first)
            folder = folder_parent.get(folder)
    found: dict[str, tuple[str, int]] = {}
    by_id = {held.entry.entry_id: held for held in files}
    for held in lookers:
        for name in held_names.get(held.entry.entry_id, ()):
            folder, level = held.entry.parent_entry_id, 0
            path: list[tuple[object, ...]] = []
            hit: tuple[str, int] | None = None
            seen = False
            while folder is not None and level <= levels_up and depth_of(folder) >= 1:
                count, first = beneath.get((folder, name), (0, ""))
                if count:
                    seen = True
                    agreed = [
                        agreeing[(folder, name, key)]
                        for key in set(path)
                        if key and (folder, name, key) in agreeing
                    ]
                    if sum(number for number, _ in agreed) == 1:
                        hit = (agreed[0][1], level)
                    elif count == 1 and not _another_experiment(held, by_id.get(first), places):
                        hit = (first, level)
                    break
                path.append(signature(folder))
                folder, level = folder_parent.get(folder), level + 1
            if not seen:
                continue
            if hit is not None and hit[0]:
                source = by_id.get(hit[0])
                if source is not None and source is not held and not backup(held, name):
                    found[held.entry.entry_id] = hit
            break
    return found


def _long_enough(stem: str, minimum: int) -> bool:
    """Whether a held name's stem has ``minimum`` letters and digits, one of them a letter."""
    letters = _letters(stem)
    return len(letters) >= minimum and any(character.isalpha() for character in letters)


def _folder_signature(folder: EntryRecords) -> tuple[object, ...]:
    """What a folder's name says, as two folders' names are compared to agree: its dates and its
    words, or its whole name, case folded, where it carries neither."""
    dates = sorted(
        component.normalized_value
        for component in folder.components
        if component.source_scope is SourceScope.NAME and component.component_type == "date"
    )
    words = sorted(
        component.normalized_value
        for component in folder.components
        if component.source_scope is SourceScope.NAME and component.component_type == "word"
    )
    if dates or words:
        return ("named", tuple(dates), tuple(words))
    return ("spelled", folder.entry.name.casefold())


def _another_experiment(
    held: EntryRecords, other: EntryRecords | None, places: Mapping[str, ExperimentPlace]
) -> bool:
    """Whether two files each lie in an experiment and the two differ."""
    if other is None:
        return True
    own, theirs = places.get(held.entry.entry_id), places.get(other.entry.entry_id)
    return own is not None and theirs is not None and own.experiment != theirs.experiment


def _a_backup_of(
    held: EntryRecords,
    after: Sequence[str],
    held_name: str,
    spelled_by: Mapping[str, set[str]],
    senses: frozenset[str],
) -> bool:
    """Whether a file named for another is a backup of it: its own extension is spelled only by
    ``senses`` (``zeph_041.nd2.bak``), or the words after the name it holds all are and it keeps
    that file's format (``zeph_041_old.nd2``). A file in another format (``zeph_041_copy.tif``,
    ``zeph_041_master.xlsx``) is made from it."""

    def only(word: str) -> bool:
        meant = spelled_by.get(word.casefold())
        return bool(meant) and meant is not None and meant <= senses

    own = (held.entry.extension or "").casefold()
    if own and only(own):
        return True
    kept = own == held_name.rpartition(".")[2].casefold()
    return bool(after) and kept and all(only(word) for word in after)


def _after_whole_name(held: EntryRecords, name: str) -> list[str]:
    """The words a file's name writes after the whole name it holds, its own extension left off."""
    spelled = _spelled(held.entry.name)
    own = (held.entry.extension or "").casefold()
    stem = spelled[: -(len(own) + 1)] if own and spelled.endswith(f".{own}") else spelled
    at = stem.find(name)
    tail = stem[at + len(name) :] if at >= 0 else ""
    return [word for word in re.split(r"[^0-9a-z]+", tail) if word]


def _after_parts(parts: Sequence[str], key: Sequence[str]) -> list[str]:
    """The parts of a name after the first place it holds ``key`` whole."""
    for position in range(len(parts) - len(key) + 1):
        if tuple(parts[position : position + len(key)]) == tuple(key):
            return list(parts[position + len(key) :])
    return []


def _stem_match(
    held: EntryRecords,
    by_length: Mapping[int, Mapping[tuple[str, ...], list[tuple[EntryRecords, str]]]] | None,
) -> NamedAcquisition | None:
    """The acquisition whose name's parts the file's name holds whole, longest and nearest."""
    if not by_length:
        return None
    parts = _parts(held)
    found: list[tuple[tuple[str, ...], EntryRecords, str]] = []
    for length, by_name in by_length.items():
        for position in range(len(parts) - length + 1):
            key = parts[position : position + length]
            for acquisition, claim in by_name.get(key, ()):
                found.append((key, acquisition, claim))
    if not found:
        return None
    longest = max(len(key) for key, _, _ in found)
    keys = {key for key, _, _ in found if len(key) == longest}
    if len(keys) != 1:
        return None  # two names held equally: named for neither
    claims = {acquisition.entry.entry_id: claim for key, acquisition, claim in found}
    nearest = _nearest(
        held,
        list(
            {
                acquisition.entry.entry_id: acquisition
                for key, acquisition, _ in found
                if len(key) == longest
            }.values()
        ),
    )
    if nearest is None:
        return None  # two acquisitions of that name equally near: named for neither
    return NamedAcquisition(
        acquisition=nearest.entry.entry_id,
        claim=claims[nearest.entry.entry_id],
        characters=len("".join(next(iter(keys)))),
    )


def _embedded_names(held: EntryRecords, extensions: set[str]) -> list[str] | None:
    """The whole file names a file's name holds, or None where it embeds none.

    A name embeds one where, before its own extension, ``.<extension>`` of a format its
    experiment's files are in stands, followed by anything (a name glued to the next word counts).
    Each name it may hold ends there and begins at the start or after a separator, so few are
    looked up.
    """
    if not extensions:
        return None
    spelled = _spelled(held.entry.name)
    own = (held.entry.extension or "").casefold()
    stem = spelled[: -(len(own) + 1)] if own and spelled.endswith(f".{own}") else spelled
    starts = [
        at
        for at, character in enumerate(stem)
        if _PART.match(character) and (at == 0 or not _PART.match(stem[at - 1]))
    ]
    found: list[str] | None = None
    for extension in sorted(extensions):
        at = stem.find(f".{extension}")
        while at > 0:
            end = at + len(extension) + 1
            found = found or []
            found.extend(stem[start:end] for start in starts if start < at)
            at = stem.find(f".{extension}", at + 1)
    return found


def with_acquisitions_named(
    entries: Sequence[EntryRecords], named: Mapping[str, NamedAcquisition]
) -> tuple[EntryRecords, ...]:
    """Hand each acquisition, and each file named for one, the acquisition."""
    return tuple(
        replace(held, named_acquisition=named[held.entry.entry_id])
        if held.entry.entry_id in named
        else held
        for held in entries
    )


def with_named_beside(
    entries: Sequence[EntryRecords], series: Mapping[str, str], beside: Mapping[str, str]
) -> tuple[EntryRecords, ...]:
    """Hand each file of a series summarised beside it, and each file whose name a sibling folder
    holds too, the class it makes."""
    if not series and not beside:
        return tuple(entries)
    return tuple(
        replace(
            held,
            named_series=series.get(held.entry.entry_id),
            named_beside=beside.get(held.entry.entry_id),
        )
        if held.entry.entry_id in series or held.entry.entry_id in beside
        else held
        for held in entries
    )
