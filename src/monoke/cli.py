"""Command line entry point: ``monoke serve | sync | db``."""

from __future__ import annotations

import argparse
import sys

from .config import VERSION, get_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monoke",
        description="Local archive and link index for cum.st creators.",
    )
    parser.add_argument("--version", action="version", version=f"monoke {VERSION}")

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    serve = sub.add_parser("serve", help="run the local web app")
    serve.add_argument("--host", default=None, help="bind address (default: from config)")
    serve.add_argument("--port", type=int, default=None, help="port (default: from config)")
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")

    sync = sub.add_parser("sync", help="crawl a creator URL")
    sync.add_argument("url", help="https://cum.st/creators/{service}/{id}")
    sync.add_argument("--full", action="store_true", help="re-fetch every post detail")
    sync.add_argument("--deep", action="store_true", help="alias for --full")
    sync.add_argument("--limit", type=int, default=None, help="stop after N post details")
    sync.add_argument("--concurrency", type=int, default=None, help="parallel detail requests")
    sync.add_argument("--no-cache", action="store_true", help="bypass the response cache")
    sync.add_argument(
        "--no-resolve",
        action="store_true",
        help="skip third-party shortener resolution (fast, offline crawl)",
    )
    sync.add_argument("--json", action="store_true", help="print the run result as JSON")

    db = sub.add_parser("db", help="database utilities")
    db_sub = db.add_subparsers(dest="db_command", required=True, metavar="DB_COMMAND")
    db_sub.add_parser("stats", help="print row counts and database size")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        from .web.app import run

        settings = get_settings()
        return run(
            host=args.host or settings.host,
            port=args.port or settings.port,
            reload=args.reload,
        )

    if args.command == "sync":
        from .crawler.crawl import run_sync

        return run_sync(args)

    if args.command == "db":
        if args.db_command == "stats":
            from .db import print_stats

            return print_stats()

    parser.error(f"unknown command: {args.command!r}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
