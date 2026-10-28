# Licensing of Ingence artifacts

Ingence is released under more than one licence because it produces more than one
kind of artifact. Each artifact class carries the licence appropriate to it.

| Artifact | Licence | File |
|---|---|---|
| Source code, and any software documentation bundled with the package | MIT | `../LICENSE` |
| Synthetic datasets, and de-identified graph and benchmark data | CC0-1.0 | `CC0-1.0.txt` |
| The technical report, at `../manuscript/` | CC-BY-4.0 | `CC-BY-4.0.txt` |
| Third-party dependencies and any bundled assets | their own original licences and notices, retained unchanged | `../NOTICE` |
| The vendored UCUM table, at `../vendor/ucum/` | UCUM 1.1 (Regenstrief Institute) | `UCUM-1.1.md` |
| The two fonts the Dashboard uses, at `../src/ingence/web/vendor/atkinson-hyperlegible/` and `../src/ingence/web/vendor/ibm-plex-mono/` | SIL Open Font License 1.1 (Braille Institute of America; IBM Corp.), unchanged | `OFL-1.1-Atkinson-Hyperlegible.txt`, `OFL-1.1-IBM-Plex-Mono.txt` |

**UCUM is referenced, never merged, and the licence is why.** Its terms permit
reproducing and distributing the Work complete and unaltered with its copyright
notice, a reference to the licence and the warranty disclaimer, and expressly
forbid adding, deleting or modifying its field names, contents, descriptions or
comments, or using it to create a derivative work. Copying UCUM codes into
`src/ingence/dictionary/builtin/units.yaml` as that file's own data would be
such a derivative. So a unit may carry a `ucum` key holding a code as a
*pointer* to the vendored table, the table stays whole and untouched under
`vendor/ucum/`, and a test asserts both: that every code named resolves, and
that the file's digest has not changed.

The vendored table is **not shipped in the wheel**. `pyproject.toml` does not
include `vendor/`, so an installed Ingence redistributes nothing of the Work;
validating this repository's own references is a development concern.

No dataset is published from this repository until its de-identification has been
verified. That check is a release gate, not a convention.
