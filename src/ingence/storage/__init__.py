"""Immutable run directories, atomic commit, and the current-run pointer.

`layout`       directory shape and one explicit Arrow schema per table
`commit`       the transactional write protocol
`index_store`  append-only Parquet reader and writer for the Index
`catalog`      DuckDB views over the committed run
`compact`      fragment compaction
`journal`      the structured run journal
"""
