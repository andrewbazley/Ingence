"""The Graph workspace's data: Wheel, Lineage and Timeline.

Three modes of one workspace answer three questions, each from its own bounded route:

- Wheel (``explorer/wheel``, ``explorer/files``): what is in a root or folder, how many
  experiment units there are, and what has been examined or joined by provenance. An
  aggregate-first read of the run's :mod:`~ingence.api.v1.explorer.projection`, never the
  run's entries whole.

Every route serves one run, names it, and states its caps and what they left out, exactly or as
unknown. Nothing is drawn that the run does not hold.
"""
