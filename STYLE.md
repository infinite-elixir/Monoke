# Monoke — UI Style Guide

**Status:** v2.0 · merged from the Tokyo Night / Caskaydia design system and the
accessibility-and-states proposal in `style.md`.

Monoke is a private, local-first media archive. The UI should read like a quiet
terminal that grew a good pair of eyes: dark, dense where it matters, and never
noisy. Content leads; chrome supports it. Colour is a signal, not decoration.

This document is normative. If a component disagrees with it, the component is
wrong. If a design request conflicts with product behaviour or media privacy,
behaviour wins and the constraint is reported.

**Scope of this pass:** foundations, controls, filters, gallery, responsive
behaviour, token-level viewer polish, and documentation. The viewer keeps its
current markup, keyboard model and behavior; only visual tokens changed.

---

## 1. Principles

1. **One action colour.** Blue is the interactive/action colour: links, primary
   actions, active navigation, selection. Gold means *saved* (bookmarks). Coral /
   kind colours appear only as media-kind markers. Volt is brand signal, not a
   second accent.
2. **Borders over shadows.** Separation comes from 1px lines and background
   steps, not drop shadows or glassmorphism.
3. **Density is a feature.** Use the 4px grid. Prefer a tighter vertical rhythm
   over airy marketing spacing, except where prose needs room.
4. **Monospace is the voice.** Caskaydia Cove carries the whole product. Respect
   its metrics: shorter line lengths, no faux letter-spacing, alignment over
   indentation tricks.
5. **States are visible.** Hover, focus, selected, pressed, disabled, loading,
   empty and error are distinguishable by more than colour: text, weight, a
   glyph, an inset rule, or `aria-current` / `aria-pressed`.
6. **No decorative animation.** Motion explains a state change or a spatial
   relationship. Nothing loops for atmosphere.
7. **Broadcast moments are rare.** Marathon-derived devices (volt rule, tick
   rails, regmarks) appear at brand and transition moments only: hero, empty
   state, 404/500, sync progress. Never inside forms, tables or dialogs.
8. **Preserve behaviour.** Routes, query parameters, filter semantics, htmx
   swaps, keyboard shortcuts, bookmarking and media privacy are not styling
   variables.

---

## 2. Colour

### 2.1 Raw palette — Tokyo Night

Canonical variant: **Tokyo Night**. Dark is the only palette.

| Token | Hex | Role |
|---|---|---|
| `night-bg-deep` | `#0c0e14` | Full-bleed backdrop, video letterbox |
| `night-bg` | `#16161e` | App canvas |
| `night-panel` | `#1a1b26` | Cards, sidebar, sheets |
| `night-panel-alt` | `#1f2335` | Inset wells, placeholders |
| `night-highlight` | `#292e42` | Hover, selected rows, dividers above base |
| `night-border` | `#3b4261` | Strong borders, decorative separators |
| `night-comment` | `#565f89` | **Decorative only** — never essential text |
| `night-fg-muted` | `#a9b1d6` | Secondary text |
| `night-fg` | `#c0caf5` | Primary text |
| `night-blue` | `#7aa2f7` | Action / selection accent |
| `night-blue5` | `#89ddff` | Focus ring, hover, info |
| `night-cyan` | `#7dcfff` | GIF kind marker |
| `night-green` | `#9ece6a` | Success only |
| `night-yellow` | `#e0af68` | Bookmark / warning |
| `night-orange` | `#ff9e64` | Video kind marker |
| `night-red` | `#f7768e` | Danger / destructive only |
| `night-purple` | `#bb9af7` | Audio kind marker |
| `marathon-volt` | `#c8ff3c` | Brand signal only (§11) |

### 2.2 Semantic tokens

Components use semantic aliases, never raw palette values.

| Token | Value | Use |
|---|---|---|
| `--bg-deep` | `night-bg-deep` | Backdrop, media letterbox |
| `--bg-canvas` | `night-bg` | Page background |
| `--bg-panel` | `night-panel` | Cards, panels, sidebar |
| `--bg-panel-alt` | `night-panel-alt` | Wells, placeholders |
| `--bg-hover` | `night-highlight` | Pointer hover |
| `--bg-selected` | blue @ 12% | Selected navigation, tabs, rows |
| `--border-subtle` | `night-highlight` | Decorative separators |
| `--border-strong` | `night-border` | Emphasised decorative borders |
| `--border-control` | `#5a6690` | **Input / control boundaries** (3:1+) |
| `--text-primary` | `night-fg` | Body and titles |
| `--text-secondary` | `night-fg-muted` | Supporting text |
| `--text-muted` | `#8a94c0` | Metadata that must be readable |
| `--text-decorative` | `night-comment` | Non-essential decoration only |
| `--text-disabled` | `#6b7aa8` | Disabled labels where legible |
| `--accent` | `night-blue` | Actions, links, selection |
| `--accent-hover` | `night-blue5` | Accent hover |
| `--accent-pressed` | `#6b95ea` | Pressed primary action |
| `--accent-text` | `night-bg` | Text on blue |
| `--focus-ring` | `night-blue5` | **Keyboard focus**, distinct from selection |
| `--kind-image` | `--text-secondary` | Neutral; see §2.4 |
| `--kind-movie` | `night-orange` | Video |
| `--kind-series` | `night-cyan` | GIF |
| `--kind-anime` | `night-purple` | Audio |
| `--kind-bookmark` | `night-yellow` | Saved item |
| `--success` / `--warning` / `--danger` / `--info` | green / yellow / red / blue5 | Semantic status only |
| `--volt` | `marathon-volt` | Brand layer only |

### 2.3 Usage rules

- **Blue** is actions, links, active navigation, selected controls.
- **Gold** is bookmarks/favourites. It is not a general emphasis colour. Do not
  let gold and warning collide in the same view without a text cue.
- **Orange / cyan / purple** identify video / GIF / audio. They never fill a
  card, button or detail page.
- **Green** is success and completed states only.
- **Red/pink** is error and destructive only. It is also the reason images are
  **not** mapped to red as a kind colour (§2.4).
- **Neutral** is everything structural: cards, panels, tables, inactive
  controls, link categories.
- Link categories (Drive, MEGA, direct media, video host, shortener, social,
  image, other) stay neutral by decision. The label carries the meaning; colour
  is reserved for media kind and saved state.

### 2.4 Media kind identity

| Kind | Marker | Non-colour cue |
|---|---|---|
| Image | Neutral dot / neutral `IMAGE` badge | Label |
| Video | Orange dot, 2px tile edge, orange `VIDEO` badge | Play glyph, duration label |
| GIF | Cyan dot, 2px tile edge, cyan `GIF` badge | `GIF` label |
| Audio | Purple badge | `AUDIO` label |
| Bookmarked | Gold border/icon | Filled bookmark glyph, `aria-pressed="true"` |

Applied in exactly these places:

1. The `kind-badge` on attachment rows and detail headers.
2. The 2px top edge of non-image gallery tiles.
3. The kind dot on gallery media tabs.
4. The bookmark control's saved state.
5. The 2px left rule beside a media detail title when a kind is known.

Everything else is neutral. Images deliberately stay neutral: Tokyo Night's red
is the danger colour, and using it for a kind would imply error. This is a
documented deviation from the generic proposal, not an oversight.

### 2.5 Contrast (measured)

Against `--bg-panel` `#1a1b26` unless noted. Measurements were calculated from
the final token values; re-check if a value changes.

| Foreground | Ratio | Verdict |
|---|---:|---|
| `#c0caf5` primary | 10.59 | AAA |
| `#a9b1d6` secondary | 8.10 | AAA |
| `#8a94c0` muted | 5.76 | AA |
| `#8a94c0` muted on hover row `#292e42` | 4.53 | AA |
| `#7aa2f7` accent | 6.79 | AA+ |
| `#89ddff` focus / info | 11.27 | AAA |
| `#9ece6a` success | 9.35 | AAA |
| `#e0af68` bookmark / warning | 8.55 | AAA |
| `#f7768e` danger | 6.46 | AA+ |
| `#ff9e64` video | 8.40 | AAA |
| `#7dcfff` GIF | 9.96 | AAA |
| `#bb9af7` audio | 7.39 | AAA |
| `#c8ff3c` volt | 15.27 | AAA |
| `#565f89` decorative | 2.76 | Decoration only |
| `#5a6690` control border on canvas | 3.20 | UI boundary AA |

Rules:

- Normal text ≥ 4.5:1; large text ≥ 3:1; meaningful boundaries ≥ 3:1.
- Never use `--text-decorative` for information. It may only serve tick rails,
  regmarks and other non-semantic marks.
- Test text on tinted chips and overlays separately; a token that passes on the
  canvas may fail on a raised surface.
- If an accent fails, change the pairing, not the requirement.

---

## 3. Typography

### 3.1 Family

Primary: **Caskaydia Cove Nerd Font**, bundled and used for all UI text. It is
monospaced; treat every label like code.

```css
@font-face {
  font-family: "Caskaydia Cove NF";
  src:
    local("CaskaydiaCove Nerd Font"),
    local("Caskaydia Cove Nerd Font"),
    url("/static/fonts/caskaydia-cove-nf.woff") format("woff");
  font-weight: 300 500;
  font-display: swap;
}
```

- `local()` first; the bundled face is the fallback.
- Fallback is always monospace. Never fall back to a system sans-serif.
- Disable ligatures for identifiers: hashes, paths, versions, file sizes
  (`.font-data`, `.link-url`, `.link-display`, `.sidebar-version`).
- Do not add a second display face. No italics in UI copy.
- No external font requests.

### 3.2 Scale

Base 14px, line-height 22px.

| Role | Size / line-height | Weight | Use |
|---|---|---|---|
| `text-micro` | 11 / 16 | 500 | Eyebrows, table captions, keyboard hints |
| `text-small` | 12 / 18 | 400 | Helper text, metadata, timestamps |
| `text-body` | 14 / 22 | 400 | Default UI, table cells |
| `text-body-strong` | 14 / 22 | 600 | Labels, selected nav, emphasis |
| `text-title` | 16 / 24 | 600 | Card titles, section headers |
| `text-h2` | 20 / 28 | 600 | Page sections |
| `text-h1` | 28 / 36 | 700 | Page title (one per view) |
| `text-display` | 40 / 48 | 700 | Empty-state headline only |

Rules:

- Max two weights per view: 400 and 600. Reserve 700 for `h1` and the wordmark.
- Uppercase is limited to micro eyebrow/table labels with `letter-spacing:
  0.08em`. Never uppercase prose.
- Body line length ≤ 68ch (`.lede`, `.caption`).
- Numbers in tables and counts use `font-variant-numeric: tabular-nums`.
- Truncate single-line titles with ellipsis and provide the full value via a
  `title` or detail view. Never let long titles push layout.
- Essential metadata must not fall below 12px; 11px is for labels and hints,
  not for information the user needs to read.

### 3.3 Wordmark

`Monoke` in `text-h1`, weight 700, `--text-primary`, with a single leading
`▸` in `--accent`. No subtitle in the wordmark.

---

## 4. Layout

### 4.1 Grid and spacing

- Base unit **4px**. Steps: `4, 8, 12, 16, 20, 24, 32, 40, 48, 64`
  (`--step-1 … --step-16`).
- Component padding: 12px compact, 16px default, 24px page sections.
- Vertical gap: 24px between sections, 8px between related controls.
- Content max-width 1440px, 24px gutters.

### 4.2 App shell

```
┌──────────────┬──────────────────────────────────────────┐
│ sidebar 232  │ topbar (context, primary action)          │
│ nav + status ├──────────────────────────────────────────┤
│              │ content (max 1440px, 24px gutters)        │
└──────────────┴──────────────────────────────────────────┘
```

- Sidebar: `--bg-panel`, right border `--border-subtle`. Becomes an off-canvas
  drawer below 900px; Escape closes it and focus returns to the toggle.
- Topbar: 56px, sticky, `--bg-canvas`, 1px bottom border. No blur.
- Content: single column; posters/media use responsive `auto-fill` grids.

### 4.3 60 / 30 / 10 and composition

Use 60/30/10 as a proportion guideline, not a pixel measurement:

- **60% foundation:** canvas, negative space.
- **30% structure:** panels, cards, borders.
- **10% emphasis:** blue actions/selection, gold saved state, kind markers,
  brand volt. Most controls stay neutral until their state calls for colour.

Apply a practical rule of thirds to the main content area: put the focal point
(page heading, active search, selected media, featured creator) near a
third-line where the content supports it. Do not force thirds onto forms or
narrow viewports; alignment and readability win.

### 4.4 Shape and elevation

- Controls: `--radius-control` 6px. Panels/cards: `--radius-panel` 8px.
  Pills: 999px.
- Depth comes from surface steps and 1px borders. Shadows only for overlays
  (viewer bar, popovers).
- Avoid nested bordered panels; use spacing and background steps instead.
- Sticky headers and filter bars must not cover focus rings or anchored content.

---

## 5. Components

### 5.1 Buttons

| Variant | Rest | Hover | Pressed | Disabled |
|---|---|---|---|---|
| Primary `.button-primary` | Blue bg, `--accent-text` | `--accent-hover` | `--accent-pressed` | 40% opacity, not-allowed |
| Secondary `.button` | Transparent, `--border-strong` | `--bg-hover` | `--bg-hover` | 40% opacity |
| Quiet `.button-quiet` | Transparent | `--bg-hover` | `--bg-hover` | 40% opacity |
| Danger `.button-danger` | Transparent, red border/text | Red @ 12% bg | Same | 40% opacity |
| Toggle `[aria-pressed="true"]` | Blue tint + accent border | — | — | — |
| Bookmark saved | Gold tint + gold border/icon | — | — | — |

- Focus: 2px `--focus-ring`, 2px offset. Never remove the outline without a
  replacement.
- Minimum desktop height 36px (32px for `.button-small`); 44px on coarse
  pointers and ≤480px.
- Icon-only buttons need an accessible name. Decorative glyphs get
  `aria-hidden="true"`.
- Destructive actions are visually separated from routine browsing actions.

### 5.2 Inputs, search and selects

- Border `--border-control`; background `--bg-canvas`; radius 6px; min-height
  34px (44px coarse).
- Placeholder uses `--text-muted`, never replaces a persistent label.
- Focus: cyan border + 2px cyan outline, offset 1px.
- Keep native `type="search"`, date and select behaviour. Do not replace them
  with custom widgets for styling.
- Every control has a visible `<label>`; helper/error text is associated with
  `aria-describedby`.
- Validation is written in text and associated with the field. Errors use
  `--danger` plus a `!` prefix and `role="alert"` where appropriate.
- Preserve query values through filtering/navigation unless the route defines
  a reset.

### 5.3 Filters and facets

Every filter must expose all of these states:

1. **Inactive:** neutral surface, readable label, subtle border.
2. **Hover:** `--bg-hover` / stronger border; no layout shift.
3. **Selected:** blue tint + an inset 2px accent rule or underline, plus
   `aria-current="page"` (links) or `aria-pressed="true"` (toggles). The
   selected state never relies on colour alone.
4. **Keyboard focus:** 2px cyan ring, clearly different from the selected blue.
5. **Disabled:** genuinely disabled, not-allowed cursor.
6. **Loading:** the form border signals the htmx request; the existing
   `#filter-status` indicator announces "Updating…".
7. **No results:** an empty state explains that nothing matches and offers
   *All media* / *Clear filters*.

Conventions:

- Checkbox filters: native checkbox + label. `:has(input:checked)` adds a tint,
  weight and inset rule; the tick is the non-colour cue.
- Facet links: `aria-current="true"` on the active item; inset blue rule.
- Gallery kind tabs: neutral label/count plus a small kind dot; active tab gets
  a blue border, tint and underline, with `aria-current="page"`.
- Do not invent new filter semantics or change ordering as part of styling.
- Clear/reset is always visible when filters are active.
- Category colour is intentionally absent; keep the mapping in the label.

### 5.4 Cards, gallery and media

- Cards: `--bg-panel`, 1px `--border-subtle`, 8px radius, 16px padding.
  Hover/focus-within strengthens the border; geometry never changes.
- Gallery grid: `auto-fill minmax(170px, 1fr)`, 12px gap. Two columns ≤680px,
  one ≤480px.
- Thumbnails keep their aspect ratio and `object-fit: cover`. The whole tile is
  not made clickable when nested controls exist; the thumbnail anchor opens the
  viewer and the bookmark button is a sibling.
- Metadata order: title → facts (dimensions/size/date) → category/kind. Long
  names truncate with ellipsis and expose the full value.
- Kind markers: 2px top edge for video/GIF; kind dot on tabs; kind badge on
  attachment rows. Images stay neutral.
- Badges on media use a dark 88% backing and a 1px light border so they remain
  legible over arbitrary imagery.
- Actions are not hover-only. Every action is reachable by touch.
- Missing media has an explicit placeholder; it is never an empty box.
- Empty, loading and error states have explanatory text and an action.

### 5.5 Tables

- Header: `--bg-panel-alt`, micro uppercase, `--text-muted`.
- Rows: 1px bottom border; hover `--bg-hover`.
- URL/ID columns use monospace with ligatures disabled and `overflow-wrap:
  anywhere`.
- Dates and counts use tabular numerals. Numeric alignment must not shift on
  hover.

### 5.6 Navigation

- Sidebar links: 36px desktop, 44px mobile/coarse. Active state: blue left rule,
  blue tint, semibold, `aria-current="page"`.
- Breadcrumbs use `/` separators with `aria-current="page"` on the final item.
- Tabs use `aria-current="page"` and a non-colour underline/tint.
- Skip link is the first focusable element and becomes visible on focus.

### 5.7 Flash, empty and error states

- Flash: panel surface, 2px left rule; success green, error red. Error text is
  written out, not signalled by colour alone.
- Empty state: centered panel, volt top rule (brand moment), explanatory text,
  one primary action.
- 404/500: oversized numeral in `--border-strong` (decorative), volt rule, clear
  path back.
- Loading indicators use the existing htmx indicator; do not add a second
  request-state system.

---

## 6. Iconography

- Prefer inline SVG already used by the app (`bookmark-icon`, viewer icons).
- Do not add an icon dependency for decoration.
- Consistent 1.15–1.2rem size, 2px stroke, round caps/joins.
- Decorative icons: `aria-hidden="true"` + `focusable="false"`.
- Icon-only controls need an accessible name and, where useful, a `title`.
- Never substitute emoji for core interface icons.

---

## 7. Motion

- 120ms for hover/press, 180ms for overlays, 240ms maximum for a deliberate
  transition.
- Animate opacity, colour and small transforms. Do not animate layout.
- No bouncing, parallax, ambient glow or continuous decorative motion.
- Respect reduced motion:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

Essential status text must remain visible when motion is removed.

---

## 8. Accessibility

Target **WCAG 2.2 AA**. Do not claim conformance without testing.

### Keyboard

- Every interactive element is reachable and operable by keyboard, in a logical
  order.
- Use native `<a>`, `<button>`, `<input>`, `<select>`, `<textarea>`.
- Preserve existing shortcuts, including gallery/viewer arrows, Escape and the
  viewer close/fullscreen controls.
- No keyboard traps. If focus is contained in a modal, provide a reliable exit
  and restore focus to the trigger.
- Escape closes the mobile sidebar and the viewer where the existing behaviour
  allows it.
- Focus is never hidden behind a sticky bar or overlay.

### Focus

```css
:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
```

Cyan focus is intentionally distinct from blue selection so a focused selected
item still reads as focused. Do not use `outline: none` without a replacement.

### Touch and semantics

- 44 × 44 CSS px for common controls where layout permits; never below the
  applicable WCAG target-size rule.
- Use meaningful headings in order and landmarks (`header`, `nav`, `main`).
- Form controls have visible labels/accessible names.
- `aria-live="polite"` is used sparingly (flash region, filter status), not on
  every gallery swap.
- Colour is never the only indicator of selection, category, error or status.

---

## 9. Responsive behaviour

Existing breakpoints, driven by content needs:

| Width | Behaviour |
|---|---|
| > 1200px | Full sidebar, facets and multi-column gallery |
| ≤ 1200px | Narrower facets column |
| ≤ 980px | Facets become a horizontal grid; post columns stack |
| ≤ 900px | Sidebar becomes an off-canvas drawer; topbar condenses |
| ≤ 768px | Search no longer spans two columns; hero tightens |
| ≤ 680px | Gallery drops to two columns; post/attachment rows compact |
| ≤ 640px | Bookmark cards compact; creator counts 2-up |
| ≤ 480px | Gallery single column; nav/buttons 44px |

Rules:

- No horizontal page overflow at 320px, 768px, 1024px or 1440px.
- Essential actions are never hover-only.
- Long labels wrap or scroll within their own strip; they are not clipped.
- Use fluid sizing (`min()`, `max()`, `clamp()`, `minmax()`) where it removes a
  fixed-width failure.
- Test at 200% zoom. The layout may reflow; content must remain usable.
- Preserve touch and orientation behaviour in the viewer.

---

## 10. Media privacy and the viewer

These rules are behaviour, not styling preferences.

- Keep blurred-media defaults, the `data-nsfw` attribute, the localStorage
  preference and the user-controlled reveal.
- Do not expose hidden media through CSS, markup attributes, preloading or
  viewer initialisation.
- The blurred thumbnail reveals on hover and keyboard focus today; that is the
  existing contract. Do not widen it.
- Reveal/hide controls are keyboard accessible and their labels reflect the
  real state. The state is not colour-only.
- Do not place text or controls over a thumbnail in a way that blocks the
  reveal action.
- Preserve viewer navigation, close, fullscreen and keyboard shortcuts.
- Overlay text/buttons need a stable backing surface; use the dark 88–92%
  backing, not raw text over an image.
- Do not fetch or preload extra media for decoration, and do not change media
  URLs, caching, permissions or backend privacy checks.

**Current status:** the viewer (`.lightbox`, compact/expanded/browser) received
token-level polish only: control boundaries were raised to ~3:1 against the dark
stage, saved media uses the gold bookmark state, and the focused dialog gets a
cyan stage-edge ring. Its controls already use white focus rings over the dark
stage and 44px targets on coarse pointers. Open → Escape → close restores focus
to the originating tile. A full fullscreen/shortcut/privacy walk remains manual
work (see §15).

---

## 11. Marathon brand layer

Borrow the signal, not the poster.

- **Volt** is used only for: hero accents, the empty-state top rule, the sync
  progress fill, 404/500 rules, and the brand glyph. Never inside forms, tables,
  settings or dialogs.
- Tick rails and registration marks are decorative and `aria-hidden="true"`.
- Grain, glitch and large gradients are not part of the product UI.
- If a screen has no brand moment, that is correct.

---

## 12. htmx and dynamic states

- Keep the existing htmx attributes and targets (`#posts-region`, facets OOB,
  `#filter-status`).
- Server-rendered and htmx-inserted cards share the same classes and tokens.
- During a request: form border indicates pending; the existing indicator says
  "Updating…". Do not make a control look selected before the state is applied.
- Avoid double submits; disable or mark the initiating control busy where the
  current flow supports it.
- After a swap, keep focus sensible. Do not move focus on routine updates.
- Do not duplicate IDs in repeated Jinja partials.
- Styling must survive missing optional metadata and unusually long titles.
- Use existing htmx events/indicators rather than adding a parallel state system.

---

## 13. CSS standards

- Use semantic custom properties for shared colour, spacing, radius and motion.
- Components use clear class names consistent with the current project.
- Avoid broad selectors that restyle unrelated forms/content.
- Avoid `!important` except for the tested reduced-motion rule.
- No raw colour values in templates; use tokens/classes.
- Reuse shared rules instead of duplicating per page.
- `:hover` for pointer feedback, `:focus-visible` for keyboard focus. Never rely
  on hover alone.
- Preserve browser zoom, text resizing and native control semantics.
- Check every token against every surface it appears on.

---

## 14. Rollout order

1. **Audit** — templates, CSS, JS, routes, tests, preserved behaviours.
2. **Foundations** — palette, contrast, typography, spacing, focus. *(done)*
3. **Controls** — buttons, inputs, search, toolbars. *(done)*
4. **Filters** — states, selected cues, loading, empty. *(done)*
5. **Gallery** — cards, kind markers, bookmark state. *(done)*
6. **Responsive** — narrow/medium/wide, overflow, touch. *(done)*
7. **Viewer** — token alignment; behavior unchanged. *(done)*
8. **Verification** — tests, smoke test, contrast re-check.

Do not attempt a broad rewrite in one pass.

---

## 15. Verification checklist

### Visual

- [x] Tokyo Night foundation is consistent across screens.
- [x] Blue remains the action/selection colour; gold means saved; kind colours
      are markers only.
- [x] 60/30/10 balance held; no full-page gradients or glows.
- [x] Typography, spacing, borders and radii use the existing system.
- [x] Link categories stay neutral; media kinds stay consistent.
- [x] Hover, focus, selected, disabled, loading, empty and error states exist.

### Accessibility

- [x] Metadata contrast raised from 2.76:1 to ≥4.53:1 on hover and ≥5.76:1 on
      panels.
- [x] Control boundaries raised to ≥3:1.
- [x] Focus ring is visible, 2px, and distinct from selection.
- [x] Active filters have a non-colour cue and `aria-current`/native tick.
- [x] Form controls keep visible labels.
- [x] Reduced-motion override is present.
- [x] Touch targets raised on coarse pointers.
- [ ] Manual keyboard walk of every screen.
- [ ] 200% zoom and 320px manual check.
- [x] Viewer open/Escape/close and focus restoration smoke check.
- [ ] Viewer fullscreen, every shortcut, and privacy manual walk.

### Interaction

- [x] Filters, query parameters and reset paths unchanged.
- [x] htmx markup/attributes unchanged; tests pass.
- [x] Gallery/viewer hook attributes unchanged.
- [x] Media blur defaults unchanged.
- [ ] Manual smoke test of browse → filter → gallery → viewer.

### Engineering

- [x] No framework or dependency added.
- [x] Routes, data models and API contracts unchanged.
- [x] Existing test suite passes (`439 passed, 1 skipped`).
- [x] Documentation updated.
- [ ] Representative screenshots regenerated after the viewer pass.

Report what was actually verified. An unchecked item is not a failure; it is
work that has not been done yet.

---

## 16. Resolved decisions

1. **Palette:** keep Tokyo Night; map the proposal's roles onto existing hues.
2. **Accent spread:** tiny doses — media kind markers and bookmark state only.
3. **Link categories:** neutral; labels carry meaning.
4. **Typography:** Caskaydia everywhere; no second family.
5. **Muted text:** raised to `#8a94c0`; `#565f89` retained only as decorative.
6. **Focus:** cyan (`#89ddff`) rather than the proposal's gold, to avoid
   colliding with warning/bookmark semantics and with blue selection.
7. **Images:** neutral kind marker; Tokyo Night red is reserved for danger.
8. **Viewer:** behavior and markup preserved; only visual tokens changed.
9. **Volt:** retained as the Marathon brand signal only.
