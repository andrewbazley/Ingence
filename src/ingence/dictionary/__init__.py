"""Term to sense bindings: what words mean in this data pool.

Domain vocabulary belongs in `builtin/*.yaml`, never in Python. A single dictionary hit is weak
evidence. It is enough to propose a meaning and never enough to assert one. Independent hits add up
at the sense level after surface normalization, not across redundant surface forms.
"""
