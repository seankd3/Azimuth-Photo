"""How a photo becomes bytes.

Decode, render, cache, serve. This layer may use :mod:`photo`, and nothing from
``features``. If something here needs a route, a worker or the database, it
belongs a layer up.
"""
