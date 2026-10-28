# Cytoscape.js, vendored

The Graph page draws with Cytoscape.js, shipped beside the engine so the Dashboard never fetches a
script, a font or a style from a network (invariant 7). It is a web asset, not a Python dependency.

| | |
|---|---|
| Package | `cytoscape` on npm |
| Version | 3.34.3 (pinned) |
| Source | https://registry.npmjs.org/cytoscape/-/cytoscape-3.34.3.tgz, fetched with `npm pack cytoscape@3.34.3` |
| Tarball SHA-256 | `5d9e479216ed58a5f1ac639e746fd7d9770c4393f8c355dd133abc90bcc9ff3f` |
| Tarball integrity (npm) | `sha512-yfYGhRcGAntq6YBD583j4n0Eg3jIxvWmZtz/5uz9UYkeIStSlMxuUja+ec5j3iBD8nv1rwaOAYMW09tBdkSeaQ==` |
| File taken | `package/dist/cytoscape.min.js`, unmodified |
| `cytoscape.min.js` SHA-256 | `5f3b5b529546d5af1fc5628590af033b74511a5b6f789f5f4682845863228b91` |
| License | MIT, `LICENSE` beside it (`package/LICENSE`, unmodified) |
| `LICENSE` SHA-256 | `eb319c6e6f233607f71e8e2f450391751883cfc0eeb3ca7ef574c13d1d9c2203` |

`tests/test_web_vendor.py` checks the file's digest against this table, that the wheel's globs
carry it, and that it reaches for nothing: its only `http://` strings are in the license comments
of the libraries it bundles, and it calls no network interface.

To update: `npm pack cytoscape@<version>`, copy `package/dist/cytoscape.min.js` and
`package/LICENSE` here unmodified, and write the new version and digests into this table.
