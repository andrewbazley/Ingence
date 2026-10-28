# UCUM, vendored complete and unmodified

`ucum-essence.xml` is the Unified Code for Units of Measure essence file, version
2.2, revision-dated 2024-06-17, retrieved from
<https://raw.githubusercontent.com/ucum-org/ucum/main/ucum-essence.xml> on
11 September 2026. SHA-256
`dfccea1b5dc284245ebae97edd1dc03c45864da4e87df55bc9851797b4fd0b61`.

## Copyright notice

The Unified Code for Units of Measure (UCUM), also known as the "UCUM
Specification", is copyright ©1999-2024, Regenstrief Institute, Inc. All rights
reserved.

## Licence

Distributed under the UCUM Copyright Notice and License, version 1.1, June 2024.
The complete text is at [`../../LICENSES/UCUM-1.1.md`](../../LICENSES/UCUM-1.1.md)
and at <https://github.com/ucum-org/ucum/blob/main/LICENSE.md>.

## Disclaimer of warranties

The Work is provided on an "as is" basis, without warranties or conditions of
any kind. See section 7 of the licence for the full disclaimer, and section 8
for the limitation of liability.

## Why it is here, and what may never be done to it

Ingence refers to this table. Ingence never merges the table into its own files.

Section 3(a) of the licence forbids adding, deleting or modifying the Work's
content, including field names, field contents, descriptions and comments. It
also forbids using the Work to create derivative works. Copying UCUM codes into
`src/ingence/dictionary/builtin/units.yaml` as that file's own data would make
such a derivative. The licence does not permit that.

So `units.yaml` stays the authored file. Each unit can have an optional `ucum`
key. The key holds the unit's case-sensitive UCUM code and points at this table.
It is a reference, not a copy.

The table sits here, whole and untouched. The only use of it is a test. The test
checks that every code named in `units.yaml` is a code UCUM defines. Nothing at
runtime reads this file, and nothing writes to it.

`pyproject.toml` leaves this directory out of both the wheel and the sdist. An
installed Ingence does not redistribute the Work. The directory is here so that the repository can
check its own references.

## What the table settles

UCUM keeps case-sensitive and case-insensitive codes in separate columns. This
settles the case of `nM` (nanomolar) against `nm` (nanometre). One is a
concentration and the other is a length, and they differ by nine orders of
magnitude. The Dictionary keeps the two apart too. UCUM confirms that choice
with an outside standard. A new case can be checked against the same table.

The case-sensitive column is ASCII. UCUM writes microgram as `ug`, not `µg`.
In the Dictionary, a spelling with a micro sign or a Greek mu is an alias of a
code. It is not a code of its own.

UCUM's solidus is a "per" operator, and a period means multiplication. So
`ug/mL` is a real compound unit. `ug_mL` is a spelling made up because a file name cannot hold a slash. A
spelling that resolves in this table is a unit. One that does not is a
workaround. The spelling alone cannot tell them apart.
