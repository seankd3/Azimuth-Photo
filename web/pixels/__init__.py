"""Pure functions over pixel arrays: the colour mathematics, and nothing else.

Linear in, sRGB out. Fitted against real acceptance data and expensive to be
wrong about, so it imports numpy and its own siblings and never the catalog,
the window, or a file's place on disk. `render.developed` is its one door into
the product; the rest of the tree is queries over facts.
"""
