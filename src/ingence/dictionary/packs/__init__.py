"""The eight shipped domain sections, each in a directory of its own.

Every one loads in every project, level with the universal sections, except for a domain that a
person vetoed (:mod:`~ingence.dictionary.packs.proposals`). A spelling that a domain section and
a universal section both hold is read from the folder's own files where they lean far enough
one way. Otherwise it is read as the universal sense, marked assumed
(:class:`~ingence.dictionary.discover.DomainBinder`). Each carries a manifest (``pack:`` in its
section file), so term identifiers, manifest digests and stored answers stay valid.

Domain vocabulary lives here and never in Python, as in ``builtin/``. Nothing private from the
author's own research reaches a pack: no person, private construct or strain name, project code,
internal abbreviation or date. The public scientific vocabulary that laboratories share (a widely
used cell line, drug, reagent or protein) ships here, each word with the accession of the public
database it comes from.
"""
