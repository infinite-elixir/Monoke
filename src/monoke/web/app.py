"""Application factory and uvicorn runner."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import VERSION, get_settings
from .routes import router
from .templating import STATIC_DIR, render

log = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(*, debug: bool, log_path: Path) -> None:
    """Send logs to the console, and to a rotating file in debug mode.

    A single root handler keeps uvicorn and the app on the same format. The
    debug file is rotated so a long session cannot fill the disk (T6.5).
    """
    settings = get_settings()
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if debug:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=settings.debug_log_max_bytes,
                backupCount=settings.debug_log_backups,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format=LOG_FORMAT,
        handlers=handlers,
        force=True,
    )
    # uvicorn installs its own handlers only through its log config, which we
    # replace; make its loggers honour our level instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi"):
        logging.getLogger(name).setLevel(logging.INFO)
    logging.getLogger("monoke").setLevel(logging.DEBUG if debug else logging.INFO)
    # The reloader reports every file change at DEBUG; that is just noise.
    logging.getLogger("watchfiles").setLevel(logging.INFO)


def create_app(*, debug: bool = False) -> FastAPI:
    app = FastAPI(
        title="Monoke",
        version=VERSION,
        description="Local archive and link index for cum.st creators.",
    )
    app.state.debug = debug or get_settings().debug
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)

    @app.middleware("http")
    async def revalidate_static(request: Request, call_next):
        """Let a plain reload pick up edited CSS/JS (T8.5).

        Asset URLs carry a content hash, but a normal reload of an unchanged
        URL should still revalidate instead of trusting a stale cached body.
        """
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        """Record method, path, status and timing for every request."""
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            log.warning(
                "%s %s raised after %.1f ms",
                request.method,
                request.url.path,
                elapsed,
                exc_info=True,
            )
            raise
        elapsed = (time.perf_counter() - started) * 1000
        log.debug(
            "%s %s -> %s in %.1f ms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        return response

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


def _console_only_logging() -> None:
    """Drop the debug file handler from this process.

    The uvicorn reloader runs the app in a child process; only that child
    should write the debug file, so the supervisor keeps the console only.
    """
    root = logging.getLogger()
    root.handlers = [
        handler
        for handler in root.handlers
        if not isinstance(handler, logging.handlers.RotatingFileHandler)
    ]


def debug_app() -> FastAPI:
    """App factory for the reloader child, which owns the debug log file."""
    settings = get_settings()
    configure_logging(debug=True, log_path=settings.debug_log_path)
    return create_app(debug=True)


def run(*, host: str = "127.0.0.1", port: int = 8765, reload: bool = False, debug: bool = False) -> int:
    """Serve the app. Reload needs an import string, so the two paths differ."""
    import uvicorn

    settings = get_settings()
    if debug:
        # Make the flag visible to the request handlers and the templates.
        os.environ["MONOKE_DEBUG"] = "1"
        get_settings.cache_clear()
        settings = get_settings()

    configure_logging(debug=debug, log_path=settings.debug_log_path)
    log.debug("debug mode: %s", "on" if debug else "off")
    if debug:
        log.info("debug log file: %s", settings.debug_log_path)

    if reload:
        if debug:
            # The child process serves the app and writes the debug file.
            _console_only_logging()
            target = "monoke.web.app:debug_app"
        else:
            target = "monoke.web.app:app"
        uvicorn.run(
            target,
            host=host,
            port=port,
            reload=True,
            log_level="info",
            log_config=None,
            access_log=False,
        )
    else:
        uvicorn.run(
            create_app(debug=debug),
            host=host,
            port=port,
            log_level="info",
            log_config=None,
            access_log=not debug,
        )
    return 0
