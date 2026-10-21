# Installing and using Monoke

This guide covers installation, the first archive, every screen in the web app, sync
behavior, filters, exports, configuration, backups and troubleshooting.

- [1. What Monoke does](#1-what-monoke-does)
- [2. Requirements](#2-requirements)
- [3. Install](#3-install)
- [4. Start the web app](#4-start-the-web-app)
- [5. Archive your first creator](#5-archive-your-first-creator)
- [6. Tour of the web app](#6-tour-of-the-web-app)
  - [6.1 Global controls](#61-global-controls)
  - [6.2 Home / Creators](#62-home--creators)
  - [6.3 Creator page](#63-creator-page)
  - [6.4 Post page](#64-post-page)
  - [6.5 Gallery](#65-gallery)
  - [6.6 Fullscreen viewer](#66-fullscreen-viewer)
  - [6.7 Links explorer](#67-links-explorer)
  - [6.8 Bookmarks](#68-bookmarks)
- [7. How sync works](#7-how-sync-works)
- [8. CLI reference](#8-cli-reference)
- [9. Configuration](#9-configuration)
- [10. Data, backup and maintenance](#10-data-backup-and-maintenance)
- [11. Exports](#11-exports)
- [12. Troubleshooting](#12-troubleshooting)
- [13. Privacy, security and limits](#13-privacy-security-and-limits)
- [14. Development and tests](#14-development-and-tests)
- [15. Uninstall or reset](#15-uninstall-or-reset)

---

## 1. What Monoke does

Monoke is a local, single-user web app that turns a [cum.st](https://cum.st) creator page
into a searchable SQLite archive.

It crawls the site's JSON API and stores:

- creator metadata;
- every post, its title, caption HTML, publication date and tags;
- every link found in every caption, normalized and classified;
- attachment metadata (kind, mime type, size, dimensions, duration, variants, thumbnail);
- sync history and run statistics;
- your local post and media bookmarks.

It deliberately does **not** download media, mirror files, host a public service, monitor
link rot, archive comments or send telemetry.

The web app is server-rendered FastAPI + Jinja2 with htmx for live filtering and
Server-Sent Events for crawl progress. There is no npm build step and no external CDN.

## 2. Requirements

| Item | Requirement |
|---|---|
| Python | **3.14** (the repository pins it in `.python-version`) |
| Package runner | **[uv](https://docs.astral.sh/uv/)** recommended |
| OS | Linux, macOS or Windows |
| Disk | A few MB per creator for the SQLite database; media is not stored |
| Network | Access to `cum.st`, its API, `img.cum.st` and edge media hosts |
| Browser | Any current browser with JavaScript enabled |

Monoke is developed and tested on Linux, but it is ordinary Python and should run
anywhere Python 3.14 and uv run.

## 3. Install

### 3.1 Install uv

Linux or macOS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Confirm:

```bash
uv --version
```

Alternatives are listed in the [uv documentation](https://docs.astral.sh/uv/getting-started/installation/),
including Homebrew, WinGet, Scoop and pipx.

### 3.2 Get the project

```bash
git clone https://github.com/infinite-elixir/Monoke.git monoke
cd monoke
```

### 3.3 Install dependencies

```bash
uv sync
```

`uv` reads `.python-version` and will provision Python 3.14 if it is not already
installed. It creates `.venv/` and installs Monoke in editable mode plus the test
dependencies.

Runtime dependencies only:

```bash
uv sync --no-dev
```

Verify the entry point:

```bash
uv run monoke --version
uv run monoke --help
```

### 3.4 Optional: install without uv

If you already have Python 3.14 and prefer plain pip:

```bash
python3.14 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e .
monoke --version
```

In that environment, use `monoke ...` instead of `uv run monoke ...` for every command
below.

## 4. Start the web app

```bash
uv run monoke serve
```

Output points at **<http://127.0.0.1:8765>**. Check it from another terminal:

```bash
curl 127.0.0.1:8765/healthz
```

Expected response looks like:

```json
{"ok":true,"version":"0.1.0","data_dir":"/path/to/monoke/data"}
```

### Server options

| Option | Meaning |
|---|---|
| `--host HOST` | Bind address (default `127.0.0.1`). |
| `--port PORT` | HTTP port (default `8765`). |
| `--reload` | Restart on source changes; for development. |
| `--debug` | Verbose request logging and automatic client debug panel. |

Examples:

```bash
uv run monoke serve --port 9000
uv run monoke serve --debug
MONOKE_PORT=9000 uv run monoke serve
```

Stop the server with `Ctrl+C`.

> Run exactly **one** uvicorn worker. The sync progress bus and active-run registry live
> in process memory. Multiple workers would split SSE clients from the crawl that is
> publishing progress.

### Keeping it running

For everyday use, run `uv run monoke serve` in one terminal and leave it open. If you
want it always running, use your OS service manager (systemd user unit, launchd agent or
Task Scheduler) and point it at `uv run monoke serve`. Keep the bind address local.

## 5. Archive your first creator

Open <http://127.0.0.1:8765> and paste a creator URL:

```text
https://cum.st/creators/patreon/274608
```

Accepted forms:

```text
https://cum.st/creators/{service}/{creator_id}
https://cum.st/creators/{service}/{creator_id}/post/{post_id}
cum.st/creators/{service}/{creator_id}
/creators/{service}/{creator_id}
```

Old `kemono.cr`, `kemono.su` and `coomer.party` links are rejected with a hint, because
the API shape is different.

Press **Archive**. Monoke creates the creator row and starts the crawl in the background;
the page redirects immediately to the creator page.

### What happens during the first crawl

1. Fetch the creator profile.
2. Walk the post list in pages of 50 until an empty page, reconciling against the API's
   reported `total`.
3. Fetch full details for every post not already in the database.
4. Extract links from each caption, classify them and resolve shorteners where possible.
5. Store post, files, links and tags in one transaction per post.
6. Record a run with counts, duration and warnings.

For the reference 782-post creator this is about 800 API requests and typically 4–6
minutes at the default concurrency of 4. Monoke paces requests to roughly 4 req/s,
honors `Retry-After`, and stops if cum.st keeps returning `429`.

You can watch the live progress bar (`page`, `posts`, `links`), and press **Cancel** to
stop. A cancelled or interrupted crawl is safe: re-run Sync later and Monoke continues
with any posts still missing.

### CLI alternative

```bash
uv run monoke sync https://cum.st/creators/patreon/274608
```

Sample output:

```text
  78/782 posts · 3.9/s · Overwatch Public Model Ports
  ...
782/782 posts · 1217 links · 717 files · 251.3s (16 pages, 0 errors)
```

## 6. Tour of the web app

![Home page](../example_screenshots/Screenshot%20from%202026-10-21%2018-52-12.png)

### 6.1 Global controls

The header is the same on every page:

- **Monoke** — back to the creator list.
- **Creators** — home, add creator, creator cards.
- **Links** — the archive-wide link explorer.
- **Bookmarks** — your local post and media library.
- **Theme** — toggles dark/light. The choice is saved in `localStorage` and follows the
  OS preference on first visit.
- **Blur: on/off** — blurs thumbnails and gallery tiles by default. The choice persists.

The footer shows the active data directory, the last sync time and the app version.

Keyboard:
- `Tab` walks the page normally; there is a skip link at the top.
- In the gallery lightbox: `←`/`→` change item, `Esc` closes.
- In the fullscreen viewer: see [6.6](#66-fullscreen-viewer).

### 6.2 Home / Creators

The home page has two parts:

1. **Creator URL form** — paste a creator or post URL and press **Archive**.
2. **Creator cards** — one card per archived creator with:
   - avatar thumbnail, creator name and service;
   - last sync time;
   - post, link, file and video counts;
   - Drive/MEGA/direct-media badges;
   - **Open**, **Gallery**, **Sync** and **Delete** buttons.

**Sync** on a card starts the same background sync as on the creator page. **Delete**
asks for confirmation and removes that creator plus all of its posts, links, files, tags
and bookmarks from the database. Media was never stored, so nothing else is deleted.

Adding a creator that already exists redirects to its page instead of creating a
duplicate.

### 6.3 Creator page

![Creator page](../example_screenshots/Screenshot%20from%202026-10-21%2018-52-26.png)

The creator page is the main browsing screen.

**Header actions**

| Button | Action |
|---|---|
| **View latest** | Opens the fullscreen viewer at the newest post with media. |
| **Gallery** | Opens the per-creator media grid. |
| **Videos** | Appears when the archive has videos; opens the gallery filtered to video. |
| **Sync** | Starts an incremental background sync. Disabled while a run is active. |
| **Delete** | Confirms, then deletes this creator's whole archive. |

**Stat strip**

Posts, links, files, images, videos, GIFs, audio (when present), direct links, Drive and
MEGA counts. Clicking the Images/Videos/GIFs/Audio numbers applies that media filter to
the post list.

**Categories sidebar**

Counts for Drive, MEGA, direct media, video hosts, cloud, social, shortener, site assets,
images and other. Clicking one filters the post list to posts containing that category.
Counts follow the other active filters.

**Filters**

All filters combine and are reflected in the URL, so a filtered view can be bookmarked.

| Filter | Meaning |
|---|---|
| **Search** | Text search over title, caption and links. |
| **Category** | Posts containing at least one link of that class. |
| **Media** | Posts with attachments of a kind: image, video, GIF, audio. |
| **Tag** | Upstream cum.st tag, when the creator has tags. |
| **Availability** | `Alive` = still present upstream; `Dead` = no longer returned by the list walk. |
| **Links** | `With links` or `Without links`. |
| **From / To** | Publication date range, inclusive. |
| **Sort** | Newest, oldest or title A–Z. |

Filters apply automatically as you change them (the list updates in place via htmx).
**Clear** resets everything.

**Post rows**

Each row shows the post thumbnail, date, link count, file count, title, tags, and a
**Bookmark** button. The whole row links to the post page. Pagination is 50 posts per
page.

**Sync progress**

While a sync is running, a progress panel appears under the header with the current
phase, elapsed time, a bar, `done / total posts`, links found and a **Cancel** button.
Progress is streamed over SSE and survives a page reload while the server keeps running.

### 6.4 Post page

Click a post row to open the post. The page has:

**Header**
- Breadcrumb and creator name.
- Post title, publication date, tags (clicking a tag filters the creator page) and a
  **Bookmark** toggle.
- **Open original** opens the post on cum.st in a new tab.
- **Previous / Next** move to the older/newer neighbouring post in the creator's archive
  (cum.st's own post order, not the current filter).

**Caption**
- Sanitized HTML from cum.st. Links open in a new tab with `nofollow`/`noopener`.
- Images are lazy-loaded.
- If the post has no caption, a muted placeholder is shown.

**Links**
- Grouped by category in priority order: Drive, MEGA, direct media, video host, cloud,
  social, shortener, site asset, image, other.
- Labels, full URL preview and per-link **Copy** / **Open** buttons.
- **Copy all links** copies every link in the post, one per line.
- Shortened URLs show the resolved target when available; unresolved ones are marked
  `not resolved`.

**Attachments**
- Each attachment shows its normalized kind, mime type, size, dimensions and duration.
- **Open** opens the edge media URL in a new tab.
- **Copy link** copies that URL.
- **Load** streams the file into the page — image, audio or video — without writing it to
  disk. Videos with variants expose a variant selector (for example original/720p/240p).
- **Bookmark** on an image/GIF/video saves that media item to the local media library.
- A post with no files shows `no attachments`.

**Footer navigation** repeats Previous / Back to creator / Next.

### 6.5 Gallery

![Gallery](../example_screenshots/Screenshot%20from%202026-10-21%2018-52-54.png)

Each creator has a media grid at `/c/{id}/gallery`.

- **Header** shows image, video and post counts plus **Open viewer**, **Posts** and
  **Links** buttons.
- **Search** matches post title and caption.
- **Kind** filters all media, images, videos or GIFs. Audio is not shown in the grid.
- **Sort** supports newest, oldest, longest first and shortest first. Duration sorts
  apply to videos/GIFs; still images sink below timed media. In a pure image gallery the
  duration options are hidden.
- **Tag** filters by upstream tag when available.
- **Orientation** filters portrait, landscape or square.
- **Minimum size** filters by attachment bytes.
- **From / To** filter by publication date.
- **Apply** runs the filters; **Clear** resets them.

Each tile shows a blurred thumbnail (unless blur is off), its `Bookmark` control, title,
dimensions and file size. Clicking a tile opens the lightweight lightbox: arrow keys move,
`Esc` closes, and links go to the post or the original file after **Load**.

Use **Open viewer** for the fullscreen experience across posts.

### 6.6 Fullscreen viewer

Open it from **View latest** on the creator page or **Open viewer** on the gallery.

- Shows one media item at a time with a counter (`3 / 42`).
- Media is loaded from the cum.st edge host; nothing is cached to disk.
- Videos have native controls; images fill the stage.
- The first-run hint shows `↑` newer post, `↓` older post, `←` previous image,
  `→` next image, plus `Esc` to close and `F` for fullscreen. The hint disappears as soon
  as you move.
- Fullscreen is requested automatically on first interaction where the browser allows
  it; `F` toggles it manually.

**Keyboard**

| Key | Action |
|---|---|
| `←` / `→` | Previous / next media in the post. |
| `↑` / `↓` | Newer / older post. |
| `b` | Bookmark or unbookmark the current media item. |
| `Shift+B` | Bookmark or unbookmark the current post. |
| `f` | Toggle fullscreen. |
| `Esc` | Exit fullscreen first, then close the viewer. |
| `Tab` / `Shift+Tab` | Keep focus inside the viewer. |

**Mouse and touch** — wheel up/down moves between posts, wheel left/right moves between
media. Swipe horizontally for media and vertically for posts. Click zones on the left,
right, top and bottom edges do the same.

Query parameters (useful for bookmarks):

```text
/c/{creator}/viewer                    # latest post with media
/c/{creator}/viewer?post={post_id}     # a specific post
/c/{creator}/viewer?post={post_id}&i=2 # third media item in that post
/c/{creator}/viewer?from=gallery       # return to the gallery on close
/c/{creator}/viewer?hint=0             # skip the first-run hint
/c/{creator}/viewer?quiet=1            # minimal page for diagnostics
```

The viewer also exposes read-only JSON at `/c/{creator}/viewer/data?post={post_id}`.

### 6.7 Links explorer

Open **Links** in the header for the archive-wide view, or **Links** on a creator page for
a creator-scoped view. This is the fastest way to answer "where did I save that MEGA
folder?"

**Summary strip** shows totals per category for the current filter.

**Filters**

| Filter | Meaning |
|---|---|
| **Search** | Matches link label or URL. |
| **Creator** | Archive-wide view only; restrict to one creator. |
| **Shortener** | `Resolved`, `Unresolved` or any. |
| **Sort** | Newest, category or host. |
| **Layout** | Grouped by category or one flat list. |
| **Categories** | Multi-select checkboxes. Every category is selected by default so no link is hidden until you ask. |

Filters apply automatically. **Clear** resets to every category.

**Rows**

Each row links back to its post and shows the creator, post title, date, category badge,
label, URL (resolved target when known) and a copy button. The button copies the resolved
URL when available, otherwise the original.

**Exports**

The current filter and sort are applied to the export, not just the visible page:

- **Export CSV** — `/links.csv` or `/c/{id}/links.csv`
- **Export JSON** — `/links.json` or `/c/{id}/links.json`

See [11. Exports](#11-exports) for columns.

### 6.8 Bookmarks

![Bookmarks](../example_screenshots/Screenshot%20from%202026-10-21%2018-53-01.png)

Bookmarks are local to your database and survive re-syncs. Upstream deletion does not
remove them.

- **All / Posts / Media** tabs with counts.
- **Creator filter** narrows the library (shown once you have more than one creator).
- Post bookmarks show title, creator, date, "Bookmarked N ago" and a remove button.
- Media bookmarks show a thumbnail tile with kind and dimensions, links to the post and
  the original file, and a remove button.
- **Posts** and **Media** tabs paginate independently (50 posts, 60 media items).

Bookmark controls appear on creator post rows, post pages, attachment rows, gallery
tiles and inside the fullscreen viewer.

## 7. How sync works

### Web Sync

Pressing **Archive** or **Sync** starts a crawl in the background:

1. Walk all list pages (16 calls for ~800 posts).
2. Compare the returned post IDs with the database.
3. Fetch details only for posts that are not stored yet.
4. Reconcile the stored set against the list: posts absent upstream are flagged
   `missing_upstream` and shown as **Dead** under Availability. They are never deleted.
5. Update the creator's last-sync time and record a run.

Web sync does not re-fetch details for posts already stored. If a creator edits an old
post and you need the new caption or attachments, run a full sync from the CLI:

```bash
uv run monoke sync https://cum.st/creators/patreon/274608 --full
```

### CLI sync modes

| Command | Behavior |
|---|---|
| `monoke sync URL` | Incremental: list pages + details for new posts. |
| `monoke sync URL --full` | Re-fetch every post detail (same as `--deep`). |
| `monoke sync URL --limit 50` | Fetch at most 50 post details; useful for testing. |
| `monoke sync URL --no-cache` | Ignore `api_cache` and refetch. |
| `monoke sync URL --no-resolve` | Skip third-party shortener resolution. |
| `monoke sync URL --concurrency 2` | Lower parallelism if cum.st is rate limiting. |
| `monoke sync URL --json` | Print the run result as JSON for scripting. |

### Rate limits and failures

- Requests are paced, jittered and parallelized at 4 by default.
- `429` responses honor `Retry-After` with a global cooldown.
- Six consecutive blocks stop the crawl and mark the run blocked.
- Individual post failures are collected; if more than half of the pending details fail,
  the run is marked failed. Re-running retries missing posts.
- Interrupted, cancelled or failed runs are safe to re-run.

## 8. CLI reference

```text
monoke [--version] COMMAND ...

Commands:
  serve     run the local web app
  sync      crawl a creator URL
  db        database utilities
```

### `monoke serve`

```bash
uv run monoke serve [--host HOST] [--port PORT] [--reload] [--debug]
```

### `monoke sync`

```bash
uv run monoke sync URL
    [--full | --deep]
    [--limit N]
    [--concurrency N]
    [--no-cache]
    [--no-resolve]
    [--json]
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Run completed. Individual post errors may still have been reported. |
| `1` | Run failed for another reason. |
| `2` | Invalid URL or invalid arguments. |
| `3` | cum.st blocked the crawl (`429`/DDOS-Guard). |
| `130` | Cancelled with `Ctrl+C`. |

### `monoke db`

```bash
uv run monoke db stats
uv run monoke db reclassify --dry-run
uv run monoke db reclassify
```

- `stats` prints row counts per table and the database size.
- `reclassify` recomputes normalized attachment kinds from stored mime type, filename,
  variants and duration, keeping the upstream kind in `files.raw_kind`. It is idempotent
  and makes no network calls. Use `--dry-run` first to see gif → video style transitions.

### Reclassifying after an upgrade

If you already have an archive from an older build and media kinds look wrong:

```bash
uv run monoke db reclassify --dry-run   # report changes
uv run monoke db reclassify             # write them
```

No re-crawl is needed.

## 9. Configuration

Configuration is environment-based. Set variables before `uv run monoke ...`.

| Variable | Default | Purpose |
|---|---|---|
| `MONOKE_DATA_DIR` | `data` | Database and debug-log directory. |
| `MONOKE_HOST` | `127.0.0.1` | HTTP bind address. |
| `MONOKE_PORT` | `8765` | HTTP port. |
| `MONOKE_SITE_BASE` | `https://cum.st` | Site base URL used for "Open original". |
| `MONOKE_API_BASE` | `https://cum.st/api/v1` | API base URL. |
| `MONOKE_IMG_BASE` | `https://img.cum.st` | Thumbnail/creator-art host. |
| `MONOKE_CONCURRENCY` | `4` | Parallel post-detail requests. |
| `MONOKE_PAGE_SIZE` | `50` | List page size (the API caps this at 50). |
| `MONOKE_TIMEOUT` | `20` | Request timeout in seconds. |
| `MONOKE_CACHE_TTL` | `60` | API cache freshness in seconds. |
| `MONOKE_USER_AGENT` | `Monoke/0.1 (local private archive)` | Outbound user agent. |
| `MONOKE_RESOLVE` | `1` | Resolve shortener redirects during sync. |
| `MONOKE_RESOLVE_BUDGET` | `100` | Maximum shorteners resolved per crawl. |
| `MONOKE_RESOLVE_CONCURRENCY` | `2` | Parallel shortener requests. |
| `MONOKE_RESOLVE_TIMEOUT` | `8` | Shortener request timeout in seconds. |
| `MONOKE_RESOLVE_TTL_DAYS` | `30` | How long a stored shortener resolution is reused. |
| `MONOKE_DEBUG` | `0` | Verbose logging and client debug panel. |
| `MONOKE_DEBUG_LOG_MAX_BYTES` | `2097152` | Rotate the debug log at this size. |
| `MONOKE_DEBUG_LOG_BACKUPS` | `3` | Number of rotated debug logs kept. |

Examples:

```bash
MONOKE_DATA_DIR=$HOME/monoke-data uv run monoke serve
MONOKE_CONCURRENCY=2 uv run monoke sync URL
MONOKE_RESOLVE=0 uv run monoke sync URL --no-resolve
```

`serve --host` and `serve --port` override the environment for that process.

> If you change `MONOKE_DATA_DIR`, the web app and CLI must use the same value or they
> will read different archives.

## 10. Data, backup and maintenance

### Files

```text
data/
  monoke.db          # SQLite database (WAL mode)
  monoke.db-wal      # present while the database is open
  monoke.db-shm
  monoke.debug.log   # only in debug mode
```

### Tables

`creators`, `posts`, `files`, `links`, `tags`, `runs`, `api_cache`,
`link_resolutions`, `post_bookmarks`, `media_bookmarks`.

Raw post JSON is stored in `posts.raw_json`, so extraction can be re-run without
re-fetching. `api_cache` is the "don't refetch" layer; entries older than 7 days are
pruned after a crawl. Only `revisions_count` is stored; comments are out of scope.

### Backup

Stop the server, then copy the database and any WAL/SHM files:

```bash
cp data/monoke.db data/monoke.db-wal data/monoke.db-shm /path/to/backup/
```

If the WAL files do not exist, that is normal. You can also use SQLite's backup command:

```bash
sqlite3 data/monoke.db ".backup '/path/to/monoke-backup.db'"
```

### Check size

```bash
uv run monoke db stats
```

### Delete one creator

Use **Delete** on the creator page or card and confirm. This removes the creator's posts,
links, files, tags and bookmarks. Media is not touched because Monoke never stored it.

### Reclaim space

After deleting creators, SQLite may keep free pages. Stop the server and run:

```bash
sqlite3 data/monoke.db "VACUUM;"
```

## 11. Exports

The links explorer exports exactly the current filter and sort.

**CSV columns**

```text
creator, service, post_id, post_title, published, category, label, url, resolved_url, origin
```

- `published` is ISO-8601 in UTC.
- `category` is the normalized class (`google_drive`, `mega`, `direct_media`,
  `video_host`, `shortener`, `social`, `cloud_other`, `site_asset`, `image`, `other`).
- `url` is the original extracted URL; `resolved_url` is the shortener target when known.
- CSV is RFC 4180 quoted UTF-8 with `\n` row endings.

**JSON** is an array of objects with the same keys and ISO dates.

File names are `monoke-{service}-{creator_id}-links-{YYYYMMDD}.csv` for a creator, or
`monoke-archive-links-{YYYYMMDD}.csv` archive-wide.

## 12. Troubleshooting

### `monoke: command not found`

Use `uv run monoke ...`, or activate the virtual environment first
(`source .venv/bin/activate`).

### `uv sync` cannot find Python 3.14

```bash
uv python install 3.14
uv sync
```

Check with `uv run python --version`.

### Port 8765 is already in use

```bash
uv run monoke serve --port 9000
# or find the process
ss -ltnp | grep 8765
```

### The page says 0 posts or the creator has no media

- Confirm the URL is a current `cum.st/creators/...` URL, not an old kemono URL.
- Check the creator's page on cum.st in a browser.
- Run a sync and watch for errors: `uv run monoke sync URL --json`.
- Some creators genuinely have no attachments; the post page will say
  `no attachments`.

### Sync is slow or stops with `429` / blocked

cum.st rate-limits sustained crawls. Wait a minute, then re-run. Lower parallelism if
needed:

```bash
MONOKE_CONCURRENCY=2 uv run monoke sync URL
```

The crawl always stops after repeated blocks rather than hammering the site. Re-running
is safe and resumes with missing posts.

### Sync says many posts failed

Partial failures are normal on a flaky network. Just run Sync again; stored posts are
skipped and only missing details are fetched. Use `--full` only when you deliberately
want to refetch everything.

### Progress bar does not move

- Make sure you are running one server process, not multiple workers.
- If you put Monoke behind a reverse proxy, disable response buffering for
  `/events/*`; SSE needs an unbuffered stream.
- Try `?quiet=1` on the page to rule out a browser extension; it disables live progress
  and other scripts.

### Thumbnails are broken

Thumbnails come from `img.cum.st`; the app does not proxy them. Check that your browser
can reach that host and that an extension or DNS blocker is not blocking it. The archive
data is still intact.

### Load does nothing for an attachment

Media is hot-linked from cum.st edge hosts and must be allowed by your browser, network
or ad blocker. Try **Open** to load the file in a new tab. If one edge host is unhealthy,
copy the URL and retry later; the site picks hosts independently.

### Shorteners show as unresolved

Resolution depends on the third-party host and is best-effort. It is cached, budgeted and
never blocks a crawl. Use `--no-resolve` if you prefer not to contact shortener hosts at
all. The original URL is always kept.

### The interface looks wrong after an update

Hard-refresh the page (`Ctrl+Shift+R` / `Cmd+Shift+R`). CSS and JS are served with cache
busting, but an aggressive browser cache can still hold old assets.

### Debugging a page

```bash
uv run monoke serve --debug
```

- Server log: `data/monoke.debug.log`, rotated at 2 MB with three backups.
- Client panel: bottom-right, records console errors, uncaught errors, promise rejections
  and failed htmx requests, capped at 400 lines.
- Force it for one page: `http://127.0.0.1:8765/?debug=1`.
- Disable all client diagnostics for one page: `http://127.0.0.1:8765/?quiet=1`.

### Database is locked

Only one server process should use the database at a time. Stop duplicate `monoke serve`
processes. Prefer not to run the CLI sync at the same moment as a large web sync, though
short concurrent reads are fine with WAL mode.

### Media kinds look wrong

```bash
uv run monoke db reclassify --dry-run
uv run monoke db reclassify
```

Then reload the post or gallery.

## 13. Privacy, security and limits

- Monoke binds `127.0.0.1` by default and has **no authentication**. Do not expose it to
  the internet. If you bind `0.0.0.0`, anyone on that network can browse your archive,
  including adult content.
- Outbound traffic goes to cum.st, its API, its image/edge hosts and — unless disabled —
  shortener hosts. There is no telemetry, analytics or external asset loading.
- Monoke never writes media to disk. **Load** streams bytes into your browser only.
- Captions are sanitized with an allowlist before rendering; links open with
  `noopener noreferrer nofollow`.
- The cum.st API is unofficial and can change without notice. Breakage is isolated to
  `cumst/client.py` and `cumst/media.py` and surfaced as an error rather than silent bad
  data.
- Link health is not monitored. A stored MEGA or Drive link may be dead; Monoke only
  resolves shortener redirects.
- Posts that vanish upstream are flagged, not deleted, so your archive stays complete.

## 14. Development and tests

```bash
uv run pytest                    # offline suite
MONOKE_LIVE=1 uv run pytest -m live
uv run monoke serve --reload
```

Project layout:

```text
src/monoke/
  config.py            settings and data dir
  db.py                connection, migrations, repositories
  models.py            API payload models
  cli.py               monoke serve | sync | db
  cumst/               URL parsing, HTTP client, media URLs
  crawler/             page walk, detail fan-out, event bus
  extract/             links, classification, shorteners, sanitizer, export
  web/                 FastAPI app, routes, filters, tasks, templates, static
tests/
  fixtures/cumst/      captured API payloads
```

Design notes live in [PLAN.md](../PLAN.md); the backlog is in [tickets/](../tickets/).

## 15. Uninstall or reset

To start over completely:

1. Stop the server.
2. Delete the database (and optionally the whole data directory):

```bash
rm data/monoke.db data/monoke.db-wal data/monoke.db-shm
rm -f data/monoke.debug.log
```

3. Restart `uv run monoke serve` and add creators again.

To remove the application itself, delete the cloned directory and its `.venv/`. If you
used plain pip, also remove the environment where you installed it. Monoke writes nothing
outside its data directory and your OS caches.
