# Monoke — final plan (cum.st)

A local, single-user web app that turns a **cum.st** creator page into a clean, searchable index
of every post and every link inside it. Paste a URL, crawl, browse, re-sync.

**Target site:** OnlyHaven — `https://cum.st`
**Reference creator:** `https://cum.st/creators/patreon/274608` (Nyl, 782 posts)
**Status:** plan v2, rewritten 2026-10-20 after kemono.cr went away. Tickets in [`tickets/`](tickets/README.md).

---

## 1. Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | Collection method | cum.st JSON API only. No headless browser, no DOM scraping. |
| 2 | Link policy | Store **every** link found in captions. Show every link by default, classified and facet-filterable. |
| 3 | Link health checks | None automatic. No probing of Drive/MEGA/third parties. |
| 4 | "MP4" | Any direct media: `.mp4`, `.webm`, `.mov`, `.gif`, `.m4v`, catbox and similar. Video *pages* (redgifs, x.com) are a separate class. |
| 5 | Exports | CSV + JSON of the links table, plus per-post "copy all links". |
| 6 | Media files | Not downloaded. Attachments are listed with metadata; media is fetched only when the user clicks **Load**, and only into the browser. Monoke never writes media to disk. |
| 7 | Completeness | Good enough: page walk until an empty page, reconcile against the API's `total`. |
| 8 | Scope | One creator URL per sync. The DB holds many creators. |
| 9 | Missing media | A post with no attachments is shown as "no attachments". There is no upstream `flagged` field anymore. |
| 10 | Audience | Private, local-only. Bind `127.0.0.1`. No auth. |
| 11 | Stack | Python 3.14, `uv`, FastAPI, SQLite, Jinja2, httpx, BeautifulSoup. |
| 12 | Concurrency | 4 parallel detail requests, 100–300 ms jitter, exponential backoff. |

## 2. Non-goals

- Mirroring media bytes.
- Public hosting, accounts, multi-user, sharing.
- Headless-browser fallback.
- Link-rot monitoring, scheduling, notifications.
- DMs, importer flows, comments archival.

## 3. Verified facts about cum.st (probed live 2026-10-20)

- Server-rendered **SvelteKit** app, title "Creator "Nyl" on Patreon · OnlyHaven".
  Unlike kemono, the HTML is real (227 KB for the creator page) and embeds every server fetch
  as `<script type="application/json" data-sveltekit-fetched data-url="...">`.
- JSON API, same origin, **no special header and no auth**:
  `/api/v1/{service}/user/{creator_id}/…`
- Endpoints:

  | Call | Returns |
  |---|---|
  | `GET /{service}/user/{id}/profile` | creator metadata |
  | `GET /{service}/user/{id}/posts?o=0&n=50&compact=1&sort=recent` | `{total, posts[50]}` |
  | `GET /{service}/user/{id}/post/{post_id}?include=linked,revisions` | full post |

- Query params that work: `o` (offset), `n` (**capped at 50**, larger values are ignored),
  `sort=recent|oldest|top`, `q=<text>` (server-side search), `tag=<slug>`, `compact=1`.
  `sort=popular` is invalid and returns `total: 0` — never emit it.
- Reference creator: **total 782**, `o=0…730` → 50 each, `o=780` → 2, `o=782` → `[]`.
  16 pages. Search `q=overwatch` → 41; `tag=public-model-release` → 12.
- Profile payload: `id, name, service, displayName, avatarThumbhash, headerThumbhash,
  indexed, updated, contentUpdated, bookmarked, hasTags, gender`. **No `post_count`** —
  take `total` from the list call.
- Post detail payload: `id, service, caption, captionHtml, title, added, published,
  attachments, links, tags, comments, prev, next, creator, linked, revisions`.
- `caption` is raw HTML; `captionHtml` is the same content already sanitized and with
  `target="_blank" rel="noopener noreferrer nofollow ugc"` on links. **The site's own `links`
  array is empty on every post sampled** (even when the caption contains Drive + MEGA), so we
  do our own extraction from `caption`.
- `attachments[]`: `position, id, sha256, kind, mimeType, bytes, storageKey, variants[{name,bytes}],
  originalFilename, width, height, durationMs, previewThumbhash`.
  `kind ∈ {image, gif, video, audio, file}`. Duplicate attachments occur (same sha256 twice) —
  dedupe by `sha256`.
- Media IS reachable (unlike kemono's dead CDN): range requests return `206` from all of
  `e1.cum.st`, `e2.cum.st`, `e3.cum.st`. Files that kemono had flagged as missing-files are
  alive here (verified: `69a4cc…` gif, 14 432 bytes, 206 on all three hosts).
- URL construction (from the client bundle):
  - media: `https://e{1,2,3}.cum.st/media/{sha256}/original.{ext}`
  - video variants: `https://e{1,2,3}.cum.st/media/{sha256}/original.mp4|720p.mp4|240p.mp4`
  - thumbnails: `https://img.cum.st/thumbnail/{sha256}/preview.webp`
  - creator art: `https://img.cum.st/creator/{service}/{id}/avatar.webp?v={updated}`
    and `.../header.webp?v={updated}`, `.../header-full.webp`
  - the site picks an edge at random per sha; we pick deterministically (`sha[0] % 3`) so links
    are stable.
- Caching: `cache-control: public, max-age=60, stale-while-revalidate=300` — much fresher than
  kemono's 12 h. Behind DDOS-Guard, `robots.txt` is `Allow: /`.
- Short bursts (~10 rapid requests) succeed, but sustained ~13 req/s trips DDOS-Guard:
  `429` with `retry-after: 60` (seen on the first full crawl). Monoke paces to ~4 req/s,
  honors `Retry-After` with a global cooldown, and stops after six consecutive 429s.
- Services seen in the UI: `patreon`, `fansly`, `onlyfans` (nav filters). Treat the slug as
  open, with a known list for validation hints.
- Post URLs on the site: `https://cum.st/creators/{service}/{creator_id}/post/{post_id}`.

## 4. Architecture

```
Browser ──► FastAPI @ 127.0.0.1:8765
              ├─ routes + Jinja2 templates (htmx for filters, SSE for progress)
              ├─ crawler (asyncio, 4 concurrent, jitter + backoff)
              ├─ extractor (bs4/lxml → links, tags, media)
              └─ SQLite  data/monoke.db  [ creators posts files links tags runs ]
                                         [ api_cache link_resolutions ]
cum.st API ◄── /api/v1/...   (plain JSON, no auth)
```

No build step, no npm, no CDN assets. htmx is vendored as one file.

### Project layout

```
src/monoke/
  config.py            settings, data dir, defaults
  db.py                connection, migrations, repositories
  models.py            pydantic models for API payloads
  cumst/urls.py        URL parsing + service registry
  cumst/client.py      httpx client: retry, cache, concurrency
  cumst/media.py       edge/thumbnail URL builders
  crawler/crawl.py     page walk, detail fan-out, persistence
  crawler/events.py    in-process event bus for SSE
  extract/links.py     anchors + bare URLs
  extract/classify.py  normalization + categories
  extract/shortener.py redirect resolution
  extract/sanitize.py  allowlist HTML sanitizer
  extract/export.py    CSV / JSON
  web/app.py           FastAPI app factory
  web/routes.py        routes
  web/templates/       Jinja2
  web/static/          css, one htmx file
  cli.py               `monoke serve | sync | db`
tests/
  fixtures/cumst/*.json
```

## 5. Data model

`creators`, `posts`, `files`, `links`, `tags`, `runs`, `api_cache`, `link_resolutions`.
Full DDL in [tickets/T0.2](tickets/T0.2-schema-and-repositories.md).

Key rules:
- `UNIQUE(service, service_id)` on creators, `UNIQUE(creator_id, service_id)` on posts,
  `UNIQUE(post_id, sha256)` on files, `UNIQUE(post_id, normalized_url)` on links.
- Raw post JSON kept in `posts.raw_json` so extraction can be re-run without refetching.
- `api_cache` is the "don't refetch" layer; every network read goes through it.
- Only `revisions_count` is stored. Comments are ignored entirely (out of scope).

## 6. Crawl & sync

**Full crawl**
1. Parse URL → `(service, creator_id)`.
2. `GET profile` → creator metadata, avatar/header URLs.
3. Page walk `o = 0, 50, 100, …` until an **empty page**; read `total` from every response
   (they should agree); dedupe IDs.
4. For unseen IDs: `GET post?include=linked,revisions` with concurrency 4, jitter, 3×
   exponential backoff on 429/5xx/timeout.
5. Persist post + files + links + tags in one transaction per post.
6. Record a `run`; reconcile `posts_seen` vs `total`; warn on mismatch, don't fail.

**Incremental sync** — list pages only (16 calls), then details for new IDs plus a refresh
window of the newest 100 posts (the list response has no per-post `edited` field).
`--deep` re-fetches every detail.

**Disappearance** — a stored post absent from a full list walk is flagged `missing_upstream`,
never deleted.

Expected cost: first crawl ≈ 800 API calls, 4–6 min at concurrency 4. Sync ≈ 20 s.

## 7. Link classification

Extract from `caption` (raw HTML), normalize (lowercase host, drop fragments/`utm_*`), dedupe
per post, then classify:

| Category | Rule |
|---|---|
| `google_drive` | `drive.google.com`, `docs.google.com` |
| `mega` | `mega.nz`, `mega.co.nz` — including after shortener resolution |
| `direct_media` | path ends `.mp4/.webm/.mov/.gif/.m4v`; `files.catbox.moe` |
| `video_host` | `redgifs.com`, `gfycat`, `twitter.com`/`x.com` media |
| `shortener` | `bit.ly`, `t.co`, `tinyurl.com`, `is.gd` — unresolved target |
| `social` | twitter/x posts, tumblr, pixiv |
| `cloud_other` | dropbox, onedrive |
| `site_asset` | any `e{1,2,3}.cum.st` / `img.cum.st` URL (rare in captions) |
| `other` | everything else |

Attachments are not links — they live in their own table and section with full metadata
(sha256, mime, bytes, dimensions, variants).

Shortener resolution: one redirect-following request, 2 concurrent, cached forever in
`link_resolutions`. Failures are swallowed; the post is never blocked.

## 8. UI

- `/` — paste-a-URL form, creator cards (post count, last sync, link counts), Sync.
- `/c/{creator}` — post list with facets: category, tag, has-links, has-attachments, date,
  search, sort. Filters are URL-encoded and bookmarkable.
- `/c/{creator}/p/{post}` — sanitized caption, full link table with category badges, labels,
  copy buttons; attachments section with thumbnail, size, dimensions, duration, edge link and a
  per-attachment **Load** button (no auto-fetch, no disk write).
- `/c/{creator}/links` — flat link explorer: every link, grouped/sortable by category, search,
  export CSV/JSON.
- Sync progress via SSE: `page 3/16 · 120/782 posts · 41 links`.
- NSFW thumb blur toggle persisted in `localStorage`; dark/light theme.

## 9. Runbook

```bash
uv sync
uv run monoke serve                 # http://127.0.0.1:8765
uv run monoke sync https://cum.st/creators/patreon/274608
uv run monoke sync <url> --full --concurrency 4
uv run pytest
```

Data lives in `./data/` (override with `MONOKE_DATA_DIR`).

## 10. Risks

| Risk | Mitigation |
|---|---|
| cum.st API is unofficial and may change | Isolated in `cumst/client.py` + `cumst/media.py`; fails loudly with the response body |
| Site's own `links` field is empty/unreliable | We never depend on it; our extractor is the product |
| Shortener resolution hits third parties | Cached forever, 2 concurrent, 8 s timeout, failures ignored |
| Duplicate attachments | `UNIQUE(post_id, sha256)` + explicit dedupe |
| Edge host flakiness | Deterministic host choice + a `--edge` override; all three verified |
| Adult content in a local UI | Blur-by-default toggle, private bind, no telemetry |
| Offset pagination shifts on a live feed | Dedupe by ID, empty-page termination, `total` reconciliation |
| Python 3.14 wheel gaps | `uv` lockfile; nh3 has abi3 wheels, fall back to a bs4 allowlist |
