"""Perceptual identity: reads or looks like the same thing, despite different bytes.

The missing capability and the most interesting one. Structural identity catches
a re-serialised copy. Byte identity catches an exact one. Neither catches the
same figure exported twice at different resolutions, or the same table with one
column added. Those are the cases people actually care about, and they are
invisible to every other identity in the set.

Locality-sensitive, not cryptographic. Every method here is built so that
*similar* inputs give *similar* values, which is the opposite of what a
cryptographic digest is for. That means a value is only meaningful compared with
another value of the same method and version, and never as proof of anything: a
perceptual match nominates a candidate that something stronger must confirm.

Optional by construction. Both methods need a dependency the base install
does not carry, so both are registered and unavailable until somebody opts in.
That is the optional-method design working as intended: the capability is
declared, its cost is visible, and removing it is deleting a module and a
registration line.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from ingence.core.outcomes import IdentityScope
from ingence.identity import IDENTITY_METHODS, IdentityResult

if TYPE_CHECKING:
    from typing import BinaryIO

    from ingence.extractors.base import ExtractionContext

__all__ = ["ImagePerceptualHash", "TextMinHash", "shingles"]

#: Characters per shingle. Long enough that ordinary words do not collide,
#: short enough that a small edit changes only the shingles that overlap it.
SHINGLE_CHARS = 9

#: Permutations in the sketch. More is more accurate and larger. 128 estimates
#: similarity to within a few percent, which is ample for nominating candidates
#: that a real comparison then settles.
SKETCH_PERMUTATIONS = 128

#: Bytes read for a text sketch. A sample, and the scope says so.
TEXT_SAMPLE_BYTES = 1 << 20

#: Shingles handed to the sketch at a time.
#:
#: One shingle at a time was most of a dive's cost on tables. A megabyte of a
#: numeric table is close to a million distinct shingles, and ``MinHash.update`` on
#: each took 11.2 s on one test machine for a synthetic 5 MB CSV, where the text extractor
#: took 0.7 s. ``update_batch`` gives the identical digest, checked, but on the whole list it
#: builds a matrix of every shingle against every permutation, 0.9 GB for that table. In
#: batches of this size the matrix is a megabyte and the sketch takes 1.15 s, so the
#: value, the version and the memory all stay where they were.
UPDATE_BATCH = 1024


def shingles(text: str, *, width: int = SHINGLE_CHARS) -> set[bytes]:
    """Return the overlapping character windows a sketch is built from.

    Whitespace is collapsed and case folded first, so reflowed or recased text
    produces the same shingles. Overlapping rather than adjacent, so an edit
    disturbs only the windows containing it rather than shifting every window
    after it, which is what makes the similarity estimate stable under small
    changes.
    """
    normalised = " ".join(text.casefold().split())
    if len(normalised) < width:
        return set()
    return {
        normalised[index : index + width].encode("utf-8")
        for index in range(len(normalised) - width + 1)
    }


class TextMinHash:
    """A MinHash sketch over character shingles of a file's text.

    Two files sharing most of their text share most of their sketch, and the
    fraction they share estimates how similar they are without either file being
    read again. That is what makes this scale: comparison is between two small
    fixed-size sketches rather than between two documents.
    """

    name = "text_minhash"
    version = "1"
    substitutive = False

    def __init__(self) -> None:
        from datasketch import MinHash

        self._minhash = MinHash

    def supports(self, context: ExtractionContext) -> bool:
        if context.media_type and context.media_type.startswith("text/"):
            return True
        return bool(
            context.extensions
            & {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".log", ".py", ".r"}
        )

    def compute(self, context: ExtractionContext) -> IdentityResult | None:
        # The sample is a setting (``extraction.text_sketch_bytes``). A sketch of another sample
        # is another sketch, so its version names the sample wherever it is not the first one's.
        limits = getattr(context, "limits", None)
        sample = int(getattr(limits, "text_sketch_bytes", 0) or TEXT_SAMPLE_BYTES)
        try:
            with context.path.open("rb") as handle:
                payload = handle.read(sample)
        except OSError:
            return None
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            # Decoding with replacement would sketch the replacement characters
            # as much as the text, so two unrelated binaries would look similar.
            return None

        windows = shingles(text)
        if not windows:
            return None
        sketch = self._minhash(num_perm=SKETCH_PERMUTATIONS)
        ordered = sorted(windows)
        batched = getattr(sketch, "update_batch", None)
        if batched is None:
            # A release without batches: the same digest, one shingle at a time.
            for window in ordered:
                sketch.update(window)
        else:
            for start in range(0, len(ordered), UPDATE_BATCH):
                batched(ordered[start : start + UPDATE_BATCH])
        return IdentityResult(
            method=self.name,
            method_version=(
                self.version if sample == TEXT_SAMPLE_BYTES else f"{self.version}+{sample}"
            ),
            scope=IdentityScope.SAMPLED,
            # Rendered as hex so it stores as a string like every other identity
            # and can be compared without a special reader.
            value=sketch.digest().tobytes().hex(),
            bytes_read=len(payload),
        )


class ImagePerceptualHash:
    """A difference hash over a downscaled greyscale image.

    Survives resizing, recompression and format conversion, which is exactly the
    figure-exported-twice case. It does not survive cropping or rotation, and
    that limitation is worth stating rather than discovering: two crops of one
    image are a case this method will miss.
    """

    name = "image_dhash"
    version = "1"
    substitutive = False

    def __init__(self) -> None:
        import imagehash
        from PIL import Image

        self._imagehash = imagehash
        self._image = Image

    def supports(self, context: ExtractionContext) -> bool:
        if context.media_type and context.media_type.startswith("image/"):
            return True
        return bool(context.extensions & {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"})

    def compute(self, context: ExtractionContext) -> IdentityResult | None:
        # The image must be no larger than the depth's sample. The hash decodes every pixel, so a
        # 200 MB stitched plate image would be read whole over a share even where header-first
        # reading leaves its body alone, and a pass at Standard would spend its minutes on the
        # largest images of each series. An image above the depth's ``max_sample_bytes`` (Standard
        # 4 MiB, Deep 64 MiB) gets no picture fingerprint. It is still read for its header and
        # named by its digest. No value is a missing reading, never a claim that two images differ.
        limits = getattr(context, "limits", None)
        ceiling = getattr(limits, "max_bytes_read", None)
        if ceiling and context.size_bytes is not None and context.size_bytes > ceiling:
            return None
        try:
            # Open the image through the stream the extractor opens, never by name. Given a
            # name, Pillow opens the file a second time and memory-maps an uncompressed image,
            # and on a share mounted over SMB every TIFF hashed that way had its change time
            # moved, where no file read as a stream did. From a stream Pillow reads the same
            # pixels and maps nothing, and a member held in memory is hashed from its bytes.
            # The stream must also have no file descriptor. libtiff decodes a compressed TIFF,
            # and Pillow hands it the stream's descriptor, so libtiff maps the file itself,
            # in C. A stream whose descriptor cannot be asked for makes Pillow hand libtiff the
            # bytes instead: the same value, nothing mapped.
            with context.open() as opened, self._image.open(_without_descriptor(opened)) as image:
                # Decompression-bomb protection is Pillow's own, and it raises
                # rather than allocating, which is the behaviour wanted here.
                value = self._imagehash.dhash(image, hash_size=16)
        except Exception:
            return None
        return IdentityResult(
            method=self.name,
            method_version=self.version,
            scope=IdentityScope.WHOLE,
            value=str(value),
            bytes_read=context.size_bytes,
        )


class _NoDescriptor(io.BufferedReader):
    """A file read through Python's buffer, whose descriptor is never handed on."""

    def fileno(self) -> int:
        raise io.UnsupportedOperation("fileno")


def _without_descriptor(stream: BinaryIO) -> BinaryIO:
    """The same bytes from the same place, with no file descriptor a library could map."""
    if isinstance(stream, io.BufferedReader):
        return _NoDescriptor(stream.raw)
    return stream


@IDENTITY_METHODS.register(
    "text_minhash",
    version="1",
    summary="MinHash sketch over character shingles; nominates similar text",
    requires=("datasketch",),
    attributes={"extensions": ".txt,.md,.csv,.tsv,.json,.xml,.log,.py,.r"},
)
def _build_text_minhash() -> TextMinHash:
    return TextMinHash()


@IDENTITY_METHODS.register(
    "image_dhash",
    version="1",
    summary="difference hash over a downscaled image; survives resize and recompression",
    requires=("imagehash", "PIL"),
    attributes={"extensions": ".png,.jpg,.jpeg,.gif,.bmp,.tif,.tiff"},
)
def _build_image_dhash() -> ImagePerceptualHash:
    return ImagePerceptualHash()
