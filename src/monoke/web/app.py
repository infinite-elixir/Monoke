"""Application factory and uvicorn runner."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import VERSION
from .routes import router
from .templating import STATIC_DIR, render

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Monoke",
        version=VERSION,
        description="Local archive and link index for cum.st creators.",
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return render(request, "404.html", status_code=404)
        return await http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        log.exception("unhandled web error", exc_info=exc)
        try:
            return render(request, "500.html", status_code=500)
        except Exception:  # pragma: no cover - template failure fallback
            return PlainTextResponse("Internal Server Error", status_code=500)

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
