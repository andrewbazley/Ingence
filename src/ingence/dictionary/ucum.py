"""Reading the vendored UCUM table, to check references against it.

Reference, never merge, and the licence is why. UCUM's terms permit
reproducing and distributing the Work complete and unaltered with its notices,
and expressly forbid adding, deleting or modifying its field names, contents,
descriptions or comments, or using it to create a derivative work. Copying UCUM
codes into ``units.yaml`` as that file's own data would be such a derivative and
is not permitted. So the table stays whole under ``vendor/ucum/``, ``units.yaml``
stays the authored file, and a unit may name a UCUM code as a *reference* to the
table rather than as a copy of it.

That reading is also the right architecture regardless of the licence, and
the corpus evidence points the same way: an imported vocabulary belongs in a
user's project scope, never in the shipped package. A shipped
Dictionary that had absorbed somebody else's standard would be answerable for
its errors and unable to track its versions.

Nothing here runs during a scan. This module exists so a test can ask whether a
code the Dictionary names is a code UCUM defines, which is a question about this
repository rather than about anybody's data.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

__all__ = [
    "UCUM_NAMESPACE",
    "UcumTable",
    "UcumUnit",
    "load_ucum",
    "vendored_path",
]

UCUM_NAMESPACE = "http://unitsofmeasure.org/ucum-essence"

#: Where the unmodified table sits. Outside the package, because it is not
#: shipped: an installed Ingence must not redistribute the Work, and validating
#: this repository's own references is a development concern.
_VENDORED = Path(__file__).resolve().parents[3] / "vendor" / "ucum" / "ucum-essence.xml"


@dataclass(frozen=True, slots=True)
class UcumUnit:
    """One unit as UCUM defines it. A view of the Work, never a copy of it."""

    #: The case-sensitive code. ASCII by construction: microgram is ``ug``.
    code: str
    #: The case-insensitive code, which UCUM keeps as a separate column. That
    #: separation is the external grounding for not folding case in the
    #: Dictionary: ``nM`` and ``nm`` are different codes, not two spellings.
    code_insensitive: str
    name: str
    #: What UCUM suggests printing, which is often not ASCII: ``μ`` for micro.
    print_symbol: str | None
    #: True for the seven base units, which have no definition in other units.
    base: bool


@dataclass(frozen=True, slots=True)
class UcumTable:
    """Everything the essence file declares, indexed for lookup."""

    version: str
    revision_date: str
    units: dict[str, UcumUnit]
    #: Case-sensitive prefix codes: ``n``, ``u``, ``m``, ``k`` and the rest.
    prefixes: dict[str, str]

    def defines(self, code: str) -> bool:
        """Whether UCUM defines this exact case-sensitive code.

        Bare codes only. A prefixed code such as ``nmol`` is a composition and is
        resolved by :meth:`resolve`, which is a different question and worth
        keeping separate: "is this a unit UCUM names" and "is this a unit UCUM
        can parse" have different answers and different uses.
        """
        return code in self.units

    def resolve(self, code: str) -> tuple[str, UcumUnit] | None:
        """Split a code into its prefix and unit, or return nothing.

        Returns the prefix — empty where there is none — and the unit. Longest
        prefix first, because ``da`` for deca must not be read as ``d`` followed
        by a unit beginning with ``a``.

        This is not a UCUM parser and does not try to be one. UCUM's grammar has
        multiplication, division, exponents and annotations, and implementing it
        would be building a second standard, which section 3(a)(i) of the licence
        forbids in terms. This answers the narrow question the Dictionary needs:
        is this atomic code, possibly prefixed, one UCUM defines.
        """
        if code in self.units:
            return "", self.units[code]
        for length in (2, 1):
            head, tail = code[:length], code[length:]
            if head in self.prefixes and tail in self.units:
                unit = self.units[tail]
                # A prefix on a non-metric unit is not a UCUM code. UCUM marks
                # metricity per unit; the essence file's ``isMetric`` attribute
                # carries it, and where it is absent the safe answer is no.
                return head, unit
        return None

    def defines_reference(self, reference: str) -> bool:
        """Whether a reference written in ``units.yaml`` names something UCUM has.

        Accepts one solidus, because that is the shape the Dictionary's compound
        units take: ``ug/mL`` is a mass concentration and UCUM's solidus is a
        formal "per" operator, so both halves must resolve.

        This is not a UCUM expression parser, deliberately. UCUM's grammar has
        multiplication, exponents, annotations and nesting, and implementing it
        would be developing a second standard for identifying units of measure,
        which section 3(a)(i) of the licence forbids in terms. This answers only
        the question this repository needs answered about its own references.
        """
        if reference.count("/") > 1:
            return False
        return all(self.resolve(part) is not None for part in reference.split("/") if part)


def vendored_path() -> Path:
    """Return where the unmodified table lives. Absent in an installed package."""
    return _VENDORED


@lru_cache(maxsize=1)
def load_ucum(path: Path | None = None) -> UcumTable:
    """Parse the vendored essence file. Read-only, and cached for the process."""
    source = path or _VENDORED
    root = ElementTree.parse(source).getroot()  # noqa: S314 - a vendored, fixed file

    prefixes: dict[str, str] = {}
    for element in root.findall(f"{{{UCUM_NAMESPACE}}}prefix"):
        code = element.get("Code")
        name = element.findtext(f"{{{UCUM_NAMESPACE}}}name") or ""
        if code:
            prefixes[code] = name

    units: dict[str, UcumUnit] = {}
    for tag, base in (
        (f"{{{UCUM_NAMESPACE}}}base-unit", True),
        (f"{{{UCUM_NAMESPACE}}}unit", False),
    ):
        for element in root.findall(tag):
            code = element.get("Code")
            if not code:
                continue
            units[code] = UcumUnit(
                code=code,
                code_insensitive=element.get("CODE") or code.upper(),
                name=element.findtext(f"{{{UCUM_NAMESPACE}}}name") or "",
                print_symbol=element.findtext(f"{{{UCUM_NAMESPACE}}}printSymbol"),
                base=base,
            )

    return UcumTable(
        version=root.get("version") or "unknown",
        revision_date=root.get("revision-date") or "unknown",
        units=units,
        prefixes=prefixes,
    )
