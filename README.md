# Monoke

A local, single-user web app that turns a [cum.st](https://cum.st) creator page into a clean,
searchable index of every post and every link inside it.

Paste a creator URL, crawl the API into SQLite, browse posts, links and attachments, re-sync
whenever you want. Media is never downloaded by Monoke — attachments are shown as metadata plus
a **Load** button that streams the file into the browser.

## Status

Scaffold (T0.1). The crawler, extractor and UI land in later tickets.

## Requirements

- Python 3.14
- [uv](https://docs.astral.sh/uv/)

## Install

```bash
uv sync
```

## Run

```bash
uv run monoke serve          # http://127.0.0.1:8765
uv run monoke --help
curl 127.0.0.1:8765/healthz
```

Later, once the crawler exists:

```bash
uv run monoke sync https://cum.st/creators/patreon/274608
```

## Data

Everything lives in `./data/` by default. Override with `MONOKE_DATA_DIR`.

## Tests

```bash
uv run pytest                 # offline
MONOKE_LIVE=1 uv run pytest -m live
```

## Notes

- Local and private by design: binds `127.0.0.1`, no auth, no telemetry.
- One uvicorn worker only (the progress event bus is in-process).
- See `PLAN.md` for the design and `tickets/` for the backlog.
