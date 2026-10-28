"""The contract: record types, identity, tiers, relations, invariants.

These declarations are stable. Everything else in the engine reads them instead of restating them.

`schema`      every record type, every field, every enum, versioned
`ids`         canonical encoding and the identity functions
`tiers`       the two orthogonal tier axes and the absence states
`relations`   the eight primitives and the specialization catalog
`invariants`  the ten checks that block publication
`registry`    the generic registry that makes optional components removable
`project`     project directory discovery and configuration
"""
