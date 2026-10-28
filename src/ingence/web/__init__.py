"""The Dashboard's static assets, served by :mod:`ingence.api.server`.

This is a package, not a bare directory, so that the files travel with an installed wheel and
``WEB_ROOT`` resolves the same from a checkout and from site-packages. Nothing here is imported.
The server reads these files off disk.

No asset is fetched at run time. The graph renderer, Cytoscape.js, and the fonts are vendored in
``vendor/`` beside these files and not linked, because the engine must render on a machine that
has never had a network.
"""
