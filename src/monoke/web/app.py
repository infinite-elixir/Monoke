"""Application factory and uvicorn runner."""

from __future__ import annotations

from fastapi import FastAPI

from ..config import VERSION
from .routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Monoke",
        version=VERSION,
        description="Local archive and link index for cum.st creators.",
    )
    app.include_router(router)
    return app


app = create_app()


def run(*, host: str = "127.0.0.1", port: int = 8765, reload: bool = False) -> int:
    """Serve the app. Reload needs an import string, so the two paths differ."""
    import uvicorn

    if reload:
        uvicorn.run("monoke.web.app:app", host=host, port=port, reload=True)
    else:
        uvicorn.run(app, host=host, port=port)
    return 0
