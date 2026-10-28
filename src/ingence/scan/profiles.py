"""Depth profiles: presets over a budget, not four different engines.

A user should not have to guess whether they want "standard" or "deep". They
should grant an amount of work and see what it bought. So there is one mechanism
underneath, a deepening budget, and a profile is a named set of starting
values for it.

This matters beyond convenience. Four fixed modes mean four code paths, four
things to test and four ways for behaviour to diverge. A budget with presets
means one code path whose limits are data. It also lets a run report what the
budget bought and what remains unexamined. A fixed mode cannot, because it has no
notion of running out.

The budget governs content extraction and nothing else. It never governs how
the engine walks, what it concludes, or how long inference takes. It can
never cause a run to fail. An exhausted budget leaves a complete run in which
the entries not reached are marked unexamined, a state the schema has and a
person can act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ingence.core.settings import HashMode, Settings

__all__ = [
    "DEPTHS",
    "DEPTH_ALIASES",
    "DEPTH_RANK",
    "PROFILES",
    "HashChoice",
    "Profile",
    "ProfileName",
    "RemoteChoice",
    "apply_profile",
    "depth_named",
    "depth_word",
    "hash_choice",
    "remote_choice",
]


class ProfileName(StrEnum):
    """The four presets. Names a user sees."""

    #: Never opens a file. The first view, and the only sane choice for a
    #: network share or a pool of unknown size.
    CENSUS = "census"
    #: Common formats, bounded, with sampled hashing. Normal laptop use.
    STANDARD = "standard"
    #: Broad extraction and whole-file hashing below the threshold. Slower, and
    #: chosen deliberately rather than arrived at.
    DEEP = "deep"
    #: The same work, described as task manifests for a scheduler to execute.
    #: Institutional scale, same semantics.
    CLUSTER = "cluster"


#: The depths a person chooses between, in order. ``CLUSTER`` is deliberately not one:
#: it is the same work described as manifests for a scheduler, which is a question about
#: where the work runs and not how deep it goes.
DEPTHS: tuple[ProfileName, ...] = (ProfileName.CENSUS, ProfileName.STANDARD, ProfileName.DEEP)

#: What a person may type for a depth besides the profile's own name. ``metadata`` is the
#: word for the census profile in every sentence anybody writes about it. The profile's own
#: summary says "metadata only". A user should not have to know that the profile is called
#: ``census`` inside the engine.
DEPTH_ALIASES: dict[str, ProfileName] = {"metadata": ProfileName.CENSUS}


def depth_named(text: str) -> ProfileName:
    """Return the profile one spelling of a depth names, or raise saying which exist.

    Accepts an alias and, for a person who has read the settings file, a profile's own
    name. ``cluster`` resolves here too, because refusing a name the engine has would be
    a second rule to learn; it is left out of :data:`DEPTHS` only as an offered choice.
    """
    wanted = text.strip().casefold()
    if wanted in DEPTH_ALIASES:
        return DEPTH_ALIASES[wanted]
    try:
        return ProfileName(wanted)
    except ValueError:
        offered = ", ".join([*DEPTH_ALIASES, *(depth.value for depth in DEPTHS)])
        message = f"{text!r} is not a depth: choose one of {offered}"
        raise ValueError(message) from None


def depth_word(profile: ProfileName) -> str:
    """The word a person says for a depth: ``metadata`` for the census profile, else its own name.

    The reverse of :func:`depth_named`, for a message that names the depth a project was made
    with: a person typed ``metadata``, and a message naming ``census`` would be naming a word
    they never used.
    """
    for word, named in DEPTH_ALIASES.items():
        if named is profile:
            return word
    return profile.value


@dataclass(frozen=True, slots=True)
class Profile:
    """One preset, and what a person should expect from choosing it."""

    name: ProfileName
    summary: str
    opens_files: bool
    hash_mode: HashMode
    extraction_enabled: bool
    #: Seconds of content work granted. ``None`` means no ceiling, which is
    #: appropriate only where the user has said so explicitly.
    budget_seconds: float | None
    max_sample_bytes: int
    #: Seconds on the wall clock that the whole content pass may take, beside the work
    #: granted. At N workers a work budget buys N times the reading in the same minutes,
    #: and a wall clock is what a person waits on. ``None`` means no ceiling. A person's
    #: own ``extraction.total_seconds`` stands over this where they set one.
    total_seconds: float | None = None
    #: Whether hashing may cross to a network filesystem. Off everywhere except
    #: deep, because hashing across a share is slow and hammers it for everyone
    #: else using it.
    hash_remote: bool = False


PROFILES: dict[ProfileName, Profile] = {
    ProfileName.CENSUS: Profile(
        name=ProfileName.CENSUS,
        summary="Metadata only. Never opens a file.",
        opens_files=False,
        hash_mode=HashMode.NONE,
        extraction_enabled=False,
        budget_seconds=0.0,
        max_sample_bytes=0,
    ),
    ProfileName.STANDARD: Profile(
        name=ProfileName.STANDARD,
        summary="Common formats, bounded reads, sampled hashing.",
        opens_files=True,
        # Sampled rather than whole, because standard is what runs by default
        # and a default that reads every byte of a data pool is a default that
        # gets turned off.
        hash_mode=HashMode.QUICK,
        extraction_enabled=True,
        budget_seconds=900.0,
        max_sample_bytes=4 << 20,
    ),
    ProfileName.DEEP: Profile(
        name=ProfileName.DEEP,
        summary="Broad extraction and whole-file hashing below the threshold.",
        opens_files=True,
        # Full below the size threshold. Failing to detect a duplicate at any
        # scale is unacceptable and a long first scan is acceptable, so the
        # threshold is generous and this mode reads whatever sits under it.
        hash_mode=HashMode.ADAPTIVE,
        extraction_enabled=True,
        budget_seconds=None,
        max_sample_bytes=64 << 20,
        hash_remote=True,
        # Four hours on the wall clock. Without a ceiling, a pass over a share could run for
        # days with nothing to show until it ended. Four hours is a working afternoon, long
        # enough for a pool of workers to read a laboratory's drive at this depth and short
        # enough that what it read is published the same day. What it did not reach stays
        # unexamined and the next dive goes on from there.
        total_seconds=4 * 3600.0,
    ),
    ProfileName.CLUSTER: Profile(
        name=ProfileName.CLUSTER,
        summary="Deep work, emitted as task manifests for a scheduler.",
        opens_files=True,
        hash_mode=HashMode.ADAPTIVE,
        extraction_enabled=True,
        budget_seconds=None,
        max_sample_bytes=64 << 20,
        hash_remote=True,
    ),
}


#: How deep each depth reads, for choosing what a deeper pass must read again. A file
#: read at a lower rank than the pass asks for may lack what the pass would now find.
DEPTH_RANK: dict[str, int] = {
    ProfileName.CENSUS.value: 0,
    ProfileName.STANDARD.value: 1,
    ProfileName.DEEP.value: 2,
    ProfileName.CLUSTER.value: 2,
}


@dataclass(frozen=True, slots=True)
class HashChoice:
    """The digest a dive computes, and who chose it.

    ``mode`` is the per-file mode the dive's settings carry; ``by_need`` says the engine chooses
    by what a comparison needs instead, pool-wide. ``set_by`` is ``settings``, ``profile`` or
    ``need``, as the run record states it.
    """

    mode: HashMode
    by_need: bool
    set_by: str

    @property
    def recorded(self) -> str:
        """The mode as the run record states it."""
        return HashMode.NEED.value if self.by_need else self.mode.value


def hash_choice(settings: Settings, profile: ProfileName) -> HashChoice:
    """Decide the digest a dive at one depth computes. A mode a person set wins.

    The engine never overrules a person's hash mode silently, and hashing stays allowed
    whatever the dive depth. Every mode but ``need`` is a person's, and stands at every depth.
    ``need`` is the engine's to choose: by what comparing the pool needs or, where
    ``need_from`` says ``depth``, by the depth's preset.
    """
    mode = settings.hash.mode
    if mode is not HashMode.NEED:
        return HashChoice(mode=mode, by_need=False, set_by="settings")
    if settings.hash.need_from == "depth":
        return HashChoice(mode=PROFILES[profile].hash_mode, by_need=False, set_by="profile")
    return HashChoice(mode=HashMode.NEED, by_need=True, set_by="need")


@dataclass(frozen=True, slots=True)
class RemoteChoice:
    """Whether a dive leaves files on a network share unhashed, and who chose."""

    #: Whether the modes that hash every file skip a share.
    skip: bool
    #: ``settings`` where a person chose (``false`` or ``always``), ``profile`` where the depth's
    #: preset proposed it.
    set_by: str
    #: Whether ``need``'s digests are withheld on a share too: only a person's ``always``.
    need_too: bool = False


def remote_choice(settings: Settings, profile: ProfileName) -> RemoteChoice:
    """Decide whether a dive at one depth hashes across a share. A person's choice wins.

    ``false`` and ``always`` are a person's, and stand at every depth, as a person's
    ``hash.mode`` does. ``null`` and ``true`` leave the choice to the depth (older settings files
    hold ``true``). Every preset skips a share but deep's, and a depth's skip never withholds
    ``need``'s digests.
    """
    said = settings.hash.skip_remote_filesystems
    if said == "always":
        return RemoteChoice(skip=True, set_by="settings", need_too=True)
    if said is False:
        return RemoteChoice(skip=False, set_by="settings")
    return RemoteChoice(skip=not PROFILES[profile].hash_remote, set_by="profile")


def apply_profile(settings: Settings, profile: ProfileName) -> Settings:
    """Return settings with a profile's starting values applied.

    The function returns new settings and does not mutate. The profile is a starting
    position, the settings file is the user's own, and a run records the result.
    Overwriting the file would make a preset silently permanent.

    :func:`hash_choice` decides the hash mode: a person's mode stands, and the preset's
    applies only where the settings leave it to the depth. :func:`remote_choice` decides
    whether to hash across a share in the same way. The wall clock follows the same rule:
    ``extraction.total_seconds`` is the depth's where the settings leave it unset.
    """
    preset = PROFILES[profile]
    extraction = settings.extraction
    return settings.model_copy(
        update={
            "hash": settings.hash.model_copy(
                update={
                    "mode": hash_choice(settings, profile).mode,
                    "skip_remote_filesystems": remote_choice(settings, profile).skip,
                }
            ),
            "extraction": settings.extraction.model_copy(
                update={
                    "enabled": preset.extraction_enabled,
                    "max_sample_bytes": preset.max_sample_bytes,
                    "deepening_budget_seconds": preset.budget_seconds,
                    "total_seconds": (
                        extraction.total_seconds
                        if extraction.total_seconds is not None
                        else preset.total_seconds
                    ),
                }
            ),
        }
    )
