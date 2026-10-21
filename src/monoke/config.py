"""Runtime configuration.

Importable with no side effects: nothing touches the filesystem until
``Settings.ensure_dirs()`` runs, and ``get_settings()`` is cached per process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

VERSION = "0.1.0"

DEFAULT_EDGE_HOSTS = ("https://e1.cum.st", "https://e2.cum.st", "https://e3.cum.st")


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8765

    site_base: str = "https://cum.st"
    api_base: str = "https://cum.st/api/v1"
    img_base: str = "https://img.cum.st"
    edge_hosts: tuple[str, ...] = DEFAULT_EDGE_HOSTS

    concurrency: int = 4
    page_size: int = 50
    request_timeout: float = 20.0
    user_agent: str = "Monoke/0.1 (local private archive)"
    cache_ttl: int = 60

    resolve_shorteners: bool = True
    resolve_budget: int = 100
    resolve_concurrency: int = 2
    resolve_timeout: float = 8.0
    resolution_ttl_days: int = 30

    debug: bool = False
    debug_log_max_bytes: int = 2 * 1024 * 1024
    debug_log_backups: int = 3

    @property
    def db_path(self) -> Path:
        return self.data_dir / "monoke.db"

    @property
    def debug_log_path(self) -> Path:
        return self.data_dir / "monoke.debug.log"

    def ensure_dirs(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        data_dir=Path(_env("MONOKE_DATA_DIR", "data")).expanduser().resolve(),
        host=_env("MONOKE_HOST", "127.0.0.1"),
        port=_env_int("MONOKE_PORT", 8765),
        site_base=_env("MONOKE_SITE_BASE", "https://cum.st"),
        api_base=_env("MONOKE_API_BASE", "https://cum.st/api/v1"),
        img_base=_env("MONOKE_IMG_BASE", "https://img.cum.st"),
        concurrency=_env_int("MONOKE_CONCURRENCY", 4),
        page_size=_env_int("MONOKE_PAGE_SIZE", 50),
        request_timeout=float(_env("MONOKE_TIMEOUT", "20")),
        user_agent=_env("MONOKE_USER_AGENT", "Monoke/0.1 (local private archive)"),
        cache_ttl=_env_int("MONOKE_CACHE_TTL", 60),
        resolve_shorteners=_env_bool("MONOKE_RESOLVE", True),
        resolve_budget=_env_int("MONOKE_RESOLVE_BUDGET", 100),
        resolve_concurrency=max(1, _env_int("MONOKE_RESOLVE_CONCURRENCY", 2)),
        resolve_timeout=float(_env("MONOKE_RESOLVE_TIMEOUT", "8")),
        resolution_ttl_days=max(1, _env_int("MONOKE_RESOLVE_TTL_DAYS", 30)),
        debug=_env_bool("MONOKE_DEBUG", False),
        debug_log_max_bytes=max(
            64 * 1024, _env_int("MONOKE_DEBUG_LOG_MAX_BYTES", 2 * 1024 * 1024)
        ),
        debug_log_backups=max(0, _env_int("MONOKE_DEBUG_LOG_BACKUPS", 3)),
    )
