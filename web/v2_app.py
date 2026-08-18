"""The complete V2 local engine assembly."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import boot
from core.runtime_paths import resolve_runtime_paths
from routes import library, system


CATALOG_NAME = "azimuth-v2.db"


def default_paths() -> tuple[str, str]:
    """Keep V2 state isolated from every inherited catalog and tile cache."""

    runtime = resolve_runtime_paths()
    catalog = str(Path(runtime.catalog_db).with_name(CATALOG_NAME))
    tiles = os.path.join(runtime.cache_dir, "v2-tiles")
    return catalog, tiles


def create_app(*, catalog_path: str | None = None, tile_root: str | None = None) -> FastAPI:
    default_catalog, default_tiles = default_paths()
    catalog_path = os.path.abspath(catalog_path or default_catalog)
    tile_root = os.path.abspath(tile_root or default_tiles)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.closing = False
        app.state.library = boot.OwnedLibrary(catalog_path, tile_root)
        try:
            await app.state.library.run(lambda product: product.start())
            yield
        finally:
            app.state.closing = True
            await app.state.library.close()

    app = FastAPI(
        title="Azimuth Photo",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).with_name("static") / "v2"),
        name="static",
    )
    app.include_router(system.router)
    app.include_router(library.router)
    return app


app = create_app()
