# Monoke tickets

78 tickets across 12 epics, targeting **cum.st** (OnlyHaven) and **pawchive.pw**. Every ticket
is a self-contained markdown file with acceptance criteria, so it can be worked, reviewed, and
closed independently.

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
| T3.5 | [CSV and JSON exporters](T3.5-exports.md) | Extraction | S | T3.2 | done |
| T4.1 | [App shell, templates, static assets](T4.1-shell.md) | UI | S | T0.1 | done |
| T4.2 | [Dashboard and add-creator flow](T4.2-dashboard.md) | UI | M | T4.1, T2.1 | done |
| T4.3 | [Creator page with facets and search](T4.3-creator-page.md) | UI | L | T4.2, T3.2 | done |
| T4.4 | [Post page](T4.4-post-page.md) | UI | M | T4.3, T3.4 | done |
| T4.5 | [Link explorer](T4.5-link-explorer.md) | UI | M | T4.3, T3.5 | done |
| T4.6 | [Live sync progress](T4.6-sync-ui.md) | UI | M | T2.3, T4.2 | done |
| T4.7 | [Per-creator gallery viewer](T4.7-gallery-viewer.md) | UI | M | T4.3, T3.4 | done (caption images deferred) |
| T4.8 | [Gallery tile action menu: bookmark and start viewer here](T4.8-gallery-tile-action-menu.md) | UI | M | T4.7, T7.2, T8.1, T8.4, T9.1, T9.2 | todo (bookmark-only menu landed in T10.1) |
| T4.9 | [Icon-only bookmark controls and viewer fullscreen icon](T4.9-icon-controls.md) | UI | M | T9.2, T9.3, T8.8 | done |
| T4.11 | [Group the gallery by post and paginate by posts](T4.11-gallery-grouped-by-post.md) | UI | L | T4.7, T7.2, T7.6, T4.9 | done |
| T5.1 | [Incremental sync](T5.1-incremental-sync.md) | Sync | M | T2.1, T2.3 | done |
| T5.2 | [Full rescan and run history](T5.2-rescan-history.md) | Sync | S | T2.3, T4.2 | done |
| T5.3 | [Acceptance run, README, packaging](T5.3-acceptance.md) | Ship | M | all | done |
| T6.1 | [Missing favicon.ico returns 404](T6.1-favicon.md) | Debugging & UX | S | T4.1 | done |
| T6.2 | [Add-creator field and button not clickable](T6.2-unclickable-input.md) | Debugging & UX | M | T4.1 | done |
| T6.3 | [Page performance: extension slowdown warnings](T6.3-performance.md) | Debugging & UX | M | T4.1 | done |
| T6.4 | [Debug mode: verbose logging to file](T6.4-debug-mode.md) | Debugging & UX | M | T4.1 | done |
| T6.5 | [Severe memory growth / browser + shell crash](T6.5-memory-leak.md) | Debugging & UX | L | T6.2, T6.3 | done |
| T7.0 | [Audit: onlyfans/39058172 has files but no visible videos](T7.0-onlyfans-media-audit.md) | Media | S | — | done |
| T7.1 | [Normalize attachment kinds and backfill existing files](T7.1-normalize-attachment-kinds.md) | Media | M | T3.4 | done |
| T7.2 | [Make the gallery media-aware (videos included)](T7.2-video-media-gallery.md) | Media | M | T4.7, T7.1 | done |
| T7.3 | [Surface media types on the creator page and post cards](T7.3-media-kind-surfacing.md) | Media | M | T4.3, T7.1 | done |
| T7.4 | [Post page playback for normalized video-backed attachments](T7.4-video-playback-mislabelled.md) | Media | S | T4.4, T7.1 | done |
| T7.5 | [Explain empty tags for upstream creators](T7.5-empty-upstream-tags.md) | Media | S | T4.3 | done |
| T7.6 | [Sort video sections by duration (longest / shortest first)](T7.6-gallery-duration-sort.md) | Media | M | T7.2, T7.4 | done |
| T7.7 | [Gallery image/video/gif tabs instead of a creator-page Videos button](T7.7-gallery-kind-tabs.md) | Media | M | T7.2, T7.6, T4.11, T4.3 | done |
| T8.1 | [Fullscreen viewer route, entry point and data contract](T8.1-viewer-route-data.md) | Viewer | M | T4.7, T7.1 | done |
| T8.2 | [Fullscreen shell and media rendering](T8.2-viewer-fullscreen-shell.md) | Viewer | M | T8.1 | done |
| T8.3 | [Invisible directional controls: images, posts and input methods](T8.3-viewer-directional-controls.md) | Viewer | M | T8.2 | done |
| T8.4 | [First-run control hint, dismissal and viewer lifecycle](T8.4-viewer-hint-lifecycle.md) | Viewer | S | T8.3 | done |
| T8.5 | [Viewer media must fill the viewport and controls must be invisible](T8.5-viewer-invisible-controls-fullscreen-media.md) | Viewer | M | T8.4 | done |
| T8.6 | [Left/right at a post's edge crosses into the neighbouring post](T8.6-viewer-horizontal-post-crossing.md) | Viewer | M | T8.3, T8.5 | done |
| T8.7 | [Preload neighbouring images in the fullscreen viewer](T8.7-viewer-neighbour-image-preload.md) | Viewer | M | T8.1, T8.3, T8.6, T6.5 | done |
| T8.8 | [Preload across post boundaries and add a fullscreen toggle button](T8.8-viewer-cross-post-preload-and-fullscreen.md) | Viewer | M | T8.5, T8.6, T8.7 | done (browser pass manual) |
| T8.9 | [Fluid transitions between viewer media](T8.9-viewer-fluid-transitions.md) | Viewer | M | T8.1, T8.2, T8.3, T8.5, T8.7, T8.8 | todo |
| T8.10 | [Finger-following swipe with rubber-band edges](T8.10-viewer-finger-following-swipe.md) | Viewer | M | T8.3, T8.6, T8.8, T8.9 | todo |
| T9.1 | [Local bookmark storage, repository and toggle endpoints](T9.1-bookmark-storage-and-endpoints.md) | Bookmarks | M | T0.2, T4.4 | done |
| T9.2 | [Bookmark controls on posts, attachments, gallery tiles and the viewer](T9.2-bookmark-controls-ui.md) | Bookmarks | M | T9.1, T4.3, T4.4, T4.7, T8.5 | done (browser pass manual) |
| T9.3 | [Bookmarks library page and navigation](T9.3-bookmarks-library-page.md) | Bookmarks | M | T9.1, T9.2, T4.1, T4.7 | done |
| T9.4 | [Group bookmarks by creator](T9.4-bookmarks-by-creator-and-viewer.md) | Bookmarks | M | T9.3, T4.11 | done |
| T9.5 | [Bookmark media previews are one compact row per creator](T9.5-bookmark-media-preview-row.md) | Bookmarks | S | T9.4 | done |
| T9.6 | [Bookmarks survive creator deletion, plus a creator-scoped bookmark gallery](T9.6-bookmarks-survive-creator-deletion-and-creator-gallery.md) | Bookmarks | L | T9.1, T9.3, T9.4, T9.5, T11.10 | todo |
| T10.1 | [Merge `carasoul-swiping-viewer` without the viewer rewrite](T10.1-merge-carasoul-sans-viewer.md) | Integration | L | T4.9, T4.11, T7.7, T9.3 | done |
| T11.1 | [Merge the gallery lightbox and the fullscreen viewer](T11.1-merge-lightbox-and-viewer.md) | Viewer | L | T4.7, T8.8, T9.2 | done (mobile auto-fullscreen deferred by decision) |
| T11.2 | [Sticky viewer controls, visible post buttons and hide control](T11.2-sticky-viewer-controls.md) | Viewer | M | T11.1 | done (browser pass manual) |
| T11.5 | [Slim viewer bar, top caption and overflow menu](T11.5-slim-viewer-bar-and-overflow-menu.md) | Viewer | S | T11.2 | done |
| T11.3 | [Open media bookmarks in the merged lightbox-viewer](T11.3-bookmark-media-lightbox-viewer.md) | Bookmarks | M | T11.1, T9.4 | done |
| T11.4 | [Remove the first-forward black flash when an image appears](T11.4-image-paint-no-black-flash.md) | Viewer | M | T11.1, T8.7, T8.8 | done |
| T11.6 | [Viewer follows the active gallery filters](T11.6-viewer-follows-gallery-filters.md) | Viewer | M | T11.5, T7.7 | done |
| T11.7 | [Loading indicator for slow viewer media](T11.7-viewer-loading-indicator.md) | Viewer | S | T11.5 | done |
| T11.8 | [Remove the duplicate Gallery button](T11.8-remove-duplicate-gallery-button.md) | UI | S | T4.3 | done |
| T11.9 | [Media bookmark in the viewer top bar, no hint overlay](T11.9-viewer-media-bookmark-and-no-hint.md) | Viewer | M | T11.3, T11.5 | done |
| T11.10 | [Bookmark viewer navigates bookmarks, not post media](T11.10-bookmark-viewer-navigates-bookmarks.md) | Viewer | M | T11.3, T11.9 | done |
| T12.1 | [Pawchive API contract spike and source-adapter architecture](T12.1-pawchive-contract-and-source-architecture.md) | Sources | M | — | todo |
| T12.2 | [Source registry, `SourceRef`, and dual URL parsing](T12.2-source-registry-and-url-parsing.md) | Sources | M | T12.1 | todo |
| T12.3 | [Persist source identity, upstream edit times, and preview-only files](T12.3-source-identity-migration.md) | Sources | M | T12.1 | todo |
| T12.4 | [Extract a source-neutral HTTP transport](T12.4-source-neutral-http-client.md) | Sources | M | T12.2 | todo |
| T12.5 | [Move cum.st behind the source adapter with zero behaviour change](T12.5-cumst-adapter-parity.md) | Sources | M | T12.2, T12.4 | todo |
| T12.6 | [Pawchive API adapter and canonical payload mapping](T12.6-pawchive-adapter.md) | Sources | L | T12.1, T12.2, T12.3, T12.4 | todo |
| T12.7 | [Source-aware extraction and media persistence](T12.7-source-aware-extraction-and-media.md) | Sources | M | T12.5, T12.6 | todo |
| T12.8 | [Crawler and sync loop for multiple sources](T12.8-crawler-and-sync-multi-source.md) | Sources | L | T12.7 | todo |
| T12.9 | [Web UI and media presentation for multiple sources](T12.9-web-ui-source-awareness.md) | Sources | M | T12.8 | todo |
| T12.10 | [Pawchive fixtures, offline tests, live smoke, and docs](T12.10-tests-fixtures-and-docs.md) | Sources | M | T12.9 | todo |
| T12.11 | [Pawchive acceptance run and epic close-out](T12.11-pawchive-acceptance.md) | Sources | S | T12.10 | todo |

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
                          ↘ T4.7 (gallery, after T3.4)
T2.3 + T4.2 → T4.6
T2.1 + T2.3 → T5.1
T3.4 → T7.1 → T7.2 (gallery)
             ↘ T7.3 (creator page)
             ↘ T7.4 (post page)
T7.2 → T7.6 (duration sort)
T4.3 → T7.5 (empty tags)
T7.1 → T8.1 → T8.2 → T8.3 → T8.4 → T8.5 (fullscreen viewer)
T8.3 + T8.5 → T8.6 (left/right crosses posts)
T8.6 → T8.7 → T8.8 (preload across posts, fullscreen button)
T8.7 + T8.8 → T8.9 (fluid viewer transitions)
T8.6 + T8.8 + T8.9 → T8.10 (finger-following swipe)
T0.2 + T4.4 → T9.1 → T9.2 (bookmark controls)
                     ↘ T9.3 (bookmarks library)
T9.3 + T4.11 → T9.4 (bookmarks by creator)
T9.4 → T9.5 (one compact media preview row per creator)
T9.5 + T11.10 → T9.6 (bookmarks survive creator deletion, creator-scoped gallery)
T7.2 + T7.6 + T4.11 → T7.7 (gallery kind tabs)
T9.2 + T9.3 + T8.8 → T4.9 (icon-only bookmark and fullscreen controls)
T4.9 + T7.6 → T4.11 (gallery grouped by post)
T4.9 + T4.11 + T7.7 + T9.3 → T10.1 (port source branch without viewer rewrite)
T4.7 + T8.8 + T9.2 → T11.1 (merge lightbox and viewer, delete viewer page)
T11.1 → T11.2 (sticky bottom controls and hide/show)
T11.2 → T11.5 (slim bar, top caption and overflow menu)
T11.5 + T7.7 → T11.6 (viewer follows the active gallery filters)
T11.5 → T11.7 (loading pill for slow viewer media)
T4.3 → T11.8 (remove the duplicate Gallery button)
T11.1 + T9.4 → T11.3 (media bookmarks open the merged viewer)
T11.1 + T8.7 + T8.8 → T11.4 (decode-before-paint, no forward black flash)
T11.3 + T11.5 → T11.9 (media bookmark in the viewer top bar, no hint)
T11.3 + T11.9 → T11.10 (bookmark viewer plays bookmarks, not post media)
T12.1 (contract + architecture) → T12.2 (registry/ref) → T12.4 (transport)
T12.1 + T12.2 → T12.3 (source identity migration)
T12.2 + T12.4 → T12.5 (cum.st adapter parity)
T12.1 + T12.3 + T12.4 → T12.6 (pawchive adapter)
T12.5 + T12.6 → T12.7 (extraction/media) → T12.8 (crawler) → T12.9 (web UI)
T12.9 → T12.10 (tests/fixtures/docs) → T12.11 (acceptance)
T7.0 audit first
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
5. Attachments show a thumbnail, a working edge URL, and size/dimensions when the source supplies them.
6. `uv run pytest` is green offline.
7. A pawchive.pw creator can be added, synced, browsed, bookmarked and re-synced through the same UI, with preview-only media labelled honestly.
