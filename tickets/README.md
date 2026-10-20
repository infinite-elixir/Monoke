# Monoke tickets

23 tickets across 6 epics, targeting **cum.st** (OnlyHaven). Every ticket is a self-contained
markdown file with acceptance criteria, so it can be worked, reviewed, and closed independently.

Plan: [`../PLAN.md`](../PLAN.md)

## Board

| ID | Ticket | Epic | Size | Depends on | Status |
|---|---|---|---|---|---|
| T0.1 | [Project scaffold and configuration](T0.1-scaffold.md) | Foundation | S | — | done |
| T0.2 | [SQLite schema and repositories](T0.2-schema-and-repositories.md) | Foundation | M | T0.1 | done |
| T0.3 | [Test harness and fixtures](T0.3-test-harness.md) | Foundation | S | T0.1 | done |
| T1.1 | [cum.st URL parser and service registry](T1.1-url-parser.md) | Client | S | T0.1 | done |
| T1.2 | [HTTP client: cache, retry, backoff](T1.2-http-client.md) | Client | M | T0.1, T0.2 | done |
| T1.3 | [API endpoints and models](T1.3-api-endpoints.md) | Client | M | T1.2 | done |
| T2.1 | [Full crawl orchestrator](T2.1-crawl-orchestrator.md) | Crawler | M | T1.1, T1.3 | done |
| T2.2 | [Detail fan-out and resumability](T2.2-detail-fanout.md) | Crawler | M | T2.1 | done |
| T2.3 | [Run records, progress events, CLI](T2.3-runs-and-cli.md) | Crawler | M | T2.1 | done |
| T3.1 | [Link extraction from captions](T3.1-link-extraction.md) | Extraction | M | T0.2, T1.3 | done |
| T3.2 | [Normalization and classification](T3.2-classification.md) | Extraction | M | T3.1 | done |
| T3.3 | [Shortener resolution](T3.3-shorteners.md) | Extraction | S | T3.2, T1.2 | done |
| T3.4 | [Attachments, media URLs, tags](T3.4-files-and-flags.md) | Extraction | M | T3.1 | done |
| T3.5 | [CSV and JSON exporters](T3.5-exports.md) | Extraction | S | T3.2 | todo |
| T4.1 | [App shell, templates, static assets](T4.1-shell.md) | UI | S | T0.1 | todo |
| T4.2 | [Dashboard and add-creator flow](T4.2-dashboard.md) | UI | M | T4.1, T2.1 | todo |
| T4.3 | [Creator page with facets and search](T4.3-creator-page.md) | UI | L | T4.2, T3.2 | todo |
| T4.4 | [Post page](T4.4-post-page.md) | UI | M | T4.3, T3.4 | todo |
| T4.5 | [Link explorer](T4.5-link-explorer.md) | UI | M | T4.3, T3.5 | todo |
| T4.6 | [Live sync progress](T4.6-sync-ui.md) | UI | M | T2.3, T4.2 | todo |
| T5.1 | [Incremental sync](T5.1-incremental-sync.md) | Sync | M | T2.1, T2.3 | todo |
| T5.2 | [Full rescan and run history](T5.2-rescan-history.md) | Sync | S | T2.3, T4.2 | todo |
| T5.3 | [Acceptance run, README, packaging](T5.3-acceptance.md) | Ship | M | all | todo |

## Build order

```
T0.1 → T0.2 → T0.3
              ↘ T1.1 → T1.2 → T1.3 → T2.1 → T2.2
                                            ↘ T2.3
T0.2 → T3.1 → T3.2 → T3.3
              ↘ T3.4
T3.2 → T3.5
T0.1 → T4.1 → T4.2 → T4.3 → T4.4
                          ↘ T4.5
T2.3 + T4.2 → T4.6
T2.1 + T2.3 → T5.1
T5.3 last
```

## Sizes

- **S** — up to ~2 hours of focused work
- **M** — half a day
- **L** — about a day

## Definition of done (project)

1. `uv sync && uv run monoke serve` works on a clean checkout.
2. Pasting `https://cum.st/creators/patreon/274608` yields 782 posts and every caption link.
3. A second sync takes seconds and only touches new/edited posts.
4. Every link is visible, classified, searchable, and exportable.
5. Attachments show thumbnail, size, dimensions and a working edge URL.
6. `uv run pytest` is green offline.
