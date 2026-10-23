# Daku — Style Guide

**Status:** v1.1 · **Applies to:** `web/` (React + Tailwind v4)

Daku is a LAN-only media librarian. The UI should feel like a quiet terminal that grew a
good pair of eyes: dark, dense where it matters, never noisy. Content is the interface —
posters, titles and progress — not chrome. A thin **Marathon broadcast layer** (§11) adds
acid-lime signal moments without turning the app into a poster.

This document is normative. If a component disagrees with it, the component is wrong.

---

## 1. Principles

1. **One accent, used sparingly.** Blue is the action colour. If everything is highlighted,
   nothing is. Volt is a brand signal, not a second accent.
2. **Borders over shadows.** Separation comes from 1px lines and background steps, not
   drop shadows or glassmorphism.
3. **Density is a feature.** The library is for browsing, not for scrolling past whitespace.
   Use the 4px grid; prefer tighter vertical rhythm over airy marketing spacing.
4. **Monospace is the voice.** Caskaydia Cove carries the whole product. Respect its
   metrics: shorter line lengths, no faux letter-spacing, alignment over indentation tricks.
5. **States are visible.** Queued, active, failed, available must be distinguishable by
   text and icon, never colour alone.
6. **No decorative animation.** Motion only explains a state change or a spatial
   relationship.
7. **Broadcast moments are rare.** Marathon-derived graphics appear at brand and
   transition moments only (splash, empty state, 404, one hero band). Never inside forms,
   tables, settings or dialogs.

---

## 2. Colour

### 2.1 Raw palette — Tokyo Night

Canonical variant: **Tokyo Night** (not Storm/Moon unless the open decision is changed).
One colour sits outside the palette: `marathon-volt`, used only by the brand layer (§11).

| Token | Hex | Role |
|---|---|---|
| `night-bg-deep` | `#0c0e14` | Full-bleed backdrop, video letterbox |
| `night-bg` | `#16161e` | App canvas |
| `night-panel` | `#1a1b26` | Cards, sidebar, sheets |
| `night-highlight` | `#292e42` | Hover, selected rows, dividers above base |
| `night-border` | `#3b4261` | Strong borders, focused inputs (with accent) |
| `night-comment` | `#565f89` | Non-essential metadata, disabled glyphs |
| `night-fg-muted` | `#a9b1d6` | Secondary text |
| `night-fg` | `#c0caf5` | Primary text |
| `night-blue` | `#7aa2f7` | Primary action, links, focus |
| `night-blue5` | `#89ddff` | Hover on blue, info |
| `night-cyan` | `#7dcfff` | Series identity |
| `night-green` | `#9ece6a` | Success, available |
| `night-yellow` | `#e0af68` | Warning, paused, stale |
| `night-orange` | `#ff9e64` | Movie identity |
| `night-red` | `#f7768e` | Error, destructive |
| `night-purple` | `#bb9af7` | Anime identity |
| `marathon-volt` | `#c8ff3c` | Brand/broadcast signal only (§11) |

### 2.2 Semantic tokens

Components **must** use semantic tokens, never raw hex values.

| Semantic | Value | Use |
|---|---|---|
| `--bg-canvas` | `night-bg` | Page background |
| `--bg-panel` | `night-panel` | Cards, sidebar, popovers |
| `--bg-panel-alt` | `#1f2335` derived | Nested blocks, code, table header |
| `--bg-hover` | `night-highlight` | Hover backgrounds |
| `--bg-selected` | `rgba(122, 162, 247, 0.12)` | Selected row / active nav item |
| `--border-subtle` | `night-highlight` | Default divider, card border |
| `--border-strong` | `night-border` | Inputs, emphasis |
| `--text-primary` | `night-fg` | Body, headings |
| `--text-secondary` | `night-fg-muted` | Labels, helper text |
| `--text-muted` | `night-comment` | Timestamps, counts — **never** essential copy |
| `--text-disabled` | `#414868` | Disabled controls |
| `--accent` | `night-blue` | Primary buttons, links, focus ring |
| `--accent-hover` | `night-blue5` | Hover on accent surfaces |
| `--success` | `night-green` | Completed / available |
| `--warning` | `night-yellow` | Paused / stale / partial |
| `--danger` | `night-red` | Errors, destructive actions |
| `--info` | `night-blue5` | Neutral notices |
| `--kind-movie` | `night-orange` | Movie identity only |
| `--kind-series` | `night-cyan` | TV series identity only |
| `--kind-anime` | `night-purple` | Anime identity only |
| `--volt` | `marathon-volt` | Brand, empty-state, transmission progress (§11 only) |

### 2.3 Usage rules

- **Accent budget:** at most one primary action per view/section. Secondary actions are
  ghost buttons with `--border-subtle`.
- **Identity is not action.** The three kind colours (`movie`, `series`, `anime`) mark
  *what a title is*. They are never used for buttons, links, focus, errors or status.
  The action accent stays blue for every kind.
- **Kind colour dose is small:** a badge, a 2px rule, a selected filter chip or a poster
  edge — never a card background, page header or panel tint.
- **Volt is a signal, not a status.** `--volt` belongs to the Marathon layer (§11). It is
  never success, never a button, never a link. Keep it off any screen that is not a brand,
  empty, error or download-progress moment.
- **Tinted backgrounds:** status pills use the status colour at 12–16% opacity, with the
  status colour as text at 100%. Never white text on a saturated status fill.
- **Comment grey is not body text.** `#565f89` is 2.8:1 on the panel — it is for
  non-essential metadata only. Secondary copy uses `#a9b1d6` (8.1:1).
- **Posters are the only large colour fields.** Do not put coloured panels behind them.
- **No gradients** except a single bottom-up scrim under text over artwork:
  `linear-gradient(to top, rgba(12,14,20,0.95), transparent)`.
- Destructive confirmation buttons use `--danger` as text/border on transparent, filling
  only on the final confirm.

### 2.4 Media kind identity

| Kind | Colour | Badge label | Never |
|---|---|---|---|
| Movie | `--kind-movie` `#ff9e64` | `MOVIE` | Fill a card or button |
| Series | `--kind-series` `#7dcfff` | `TV` | Replace the action accent |
| Anime | `--kind-anime` `#bb9af7` | `ANIME` | Tint the whole detail page |

Applied in exactly four places:

1. The kind badge on cards and detail headers (`text-micro`, 14% tint background, colour
   text).
2. A 2px top edge on poster artwork (full width, radius follows the artwork).
3. The selected state of the Movies / TV / Anime filter chips.
4. A 2px left rule beside the detail page title.

Everything else is neutral. A grid that mixes kinds stays readable because the colour
dose is tiny. If colour is removed, the badge label must still identify the kind.

### 2.5 Contrast (WCAG AA)

Measured against `--bg-panel` `#1a1b26`:

| Foreground | Ratio | Verdict |
|---|---|---|
| `#c0caf5` primary | 10.59 | AAA |
| `#a9b1d6` secondary | 8.10 | AAA |
| `#7aa2f7` accent | 6.79 | AA+ |
| `#89ddff` accent hover / info | 9.96 | AAA |
| `#9ece6a` success | 9.35 | AAA |
| `#e0af68` warning | 8.55 | AAA |
| `#f7768e` danger | 6.46 | AA+ |
| `#ff9e64` movie | 8.40 | AAA |
| `#7dcfff` series | 9.96 | AAA |
| `#bb9af7` anime | 7.39 | AAA |
| `#c8ff3c` volt | 15.27 | AAA |
| `#565f89` muted | 2.76 | decorative only |

Focus ring: 2px `--accent`, 2px offset, visible on all interactive elements.

---

## 3. Typography

### 3.1 Family

Primary: **Caskaydia Cove Nerd Font** (Cascadia Code derivative, patched).
It is monospaced. Treat every label as if it were code: alignment is free, awkward
line-breaks are not.

```css
@font-face {
  font-family: "Caskaydia Cove NF";
  src:
    local("CaskaydiaCove Nerd Font"),
    local("Caskaydia Cove Nerd Font"),
    url("/fonts/caskaydia-cove-nf.woff2") format("woff2");
  font-weight: 300 700;
  font-style: normal;
  font-display: swap;
}

:root {
  --font-ui: "Caskaydia Cove NF", "CaskaydiaCove Nerd Font Mono",
    "JetBrainsMono Nerd Font", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
```

- **Delivery (decided):** bundle a Latin subset at
  `web/public/fonts/caskaydia-cove-nf.woff2`, with `local()` first so machines that
  already have the Nerd Font installed pay nothing.
- **Subset recipe:** Basic Latin, Latin-1 punctuation and the curated Nerd Font
  codepoints listed in `web/src/styles/glyph-codepoints.txt` (the hybrid icon whitelist),
  weight axis 300–700. Build once and commit the result:

  ```bash
  pyftsubset CaskaydiaCoveNerdFont-Regular.ttf \
    --unicodes-file=web/src/styles/glyph-codepoints.txt \
    --layout-features='kern,liga,calt' \
    --flavor=woff2 \
    --output-file=web/public/fonts/caskaydia-cove-nf.woff2
  ```

- If a Nerd Font glyph is missing from the subset, the component must fall back to an
  inline SVG. A tofu box is a release blocker.
- Disable ligatures for identifiers: hashes, paths, versions, IMDb ids, file sizes.
  `font-variant-ligatures: none` on `.font-data`.
- Fallback is always a monospace family. Never let the UI fall back to a sans-serif.

### 3.2 Scale

Base 14px. Monospace runs wide; long prose is capped at `68ch`.

| Token | Size / line-height | Weight | Use |
|---|---|---|---|
| `text-micro` | 11 / 16 | 500 | Badges, table captions, keyboard hints |
| `text-small` | 12 / 18 | 400 | Helper text, metadata, timestamps |
| `text-body` | 14 / 22 | 400 | Default UI text, table cells |
| `text-body-strong` | 14 / 22 | 600 | Labels, selected nav, emphasis |
| `text-title` | 16 / 24 | 600 | Card titles, section headers |
| `text-h2` | 20 / 28 | 600 | Page sections |
| `text-h1` | 28 / 36 | 700 | Page title (one per view) |
| `text-display` | 40 / 48 | 700 | Empty-state headline only |
| `text-mega` | 64 / 64 | 700 | Index numerals, cropped brand type (§11) |

Rules:

- Max two weights per view: 400 and 600. Reserve 700 for `h1` and the wordmark.
- **No second display face.** Marathon-style condensed headlines are an SVG wordmark
  asset, not a font. Any type above 32px is still Caskaydia, uppercase, `letter-spacing:
  -0.02em`.
- No uppercase prose. Uppercase is limited to `text-micro` eyebrow labels with
  `letter-spacing: 0.08em`.
- No italics in UI copy (Cascadia italics are a different cut; they read as an accident).
- Numbers in tables use `font-variant-numeric: tabular-nums`.
- Truncate single-line titles with ellipsis; never wrap a title to three lines. Long paths
  break-all, never overflow.
- Do not justify text.

### 3.3 Wordmark

`Daku` set in `text-h1`, weight 700, `--text-primary`, with a single leading glyph
(`󰐊`/`▸`) in `--accent` if an icon is wanted. No subtitle in the wordmark itself.

---

## 4. Layout

### 4.1 Grid

- Base unit: **4px**. Approved steps: `4, 8, 12, 16, 20, 24, 32, 40, 48, 64`.
- Component padding: 12px (compact), 16px (default), 24px (page sections).
- Vertical gap between sections: 24px; between related controls: 8px.

### 4.2 App shell

```
┌──────────────┬──────────────────────────────────────────┐
│  sidebar     │  topbar (title, search, primary action)    │
│  232px       ├──────────────────────────────────────────┤
│  nav         │  content (max-width 1440px, 24px gutters)  │
│  status      │                                            │
└──────────────┴──────────────────────────────────────────┘
```

- Sidebar: `--bg-panel`, right border `--border-subtle`. Collapses to a top bar + drawer
  below 900px.
- Topbar: 56px, sticky, `--bg-canvas` at 92% opacity with a 1px bottom border. No blur.
- Content: single column by default; posters use a responsive `auto-fill` grid with
  `minmax(140px, 1fr)` and 16px gaps.
- Breakpoints: `480 / 768 / 900 / 1200 / 1440`. Mobile-first CSS.
- Safe areas: pad for iOS notch/home indicator when installed as a PWA.

### 4.3 Shape

| Element | Radius |
|---|---|
| Buttons, inputs, small controls | 6px |
| Cards, panels, popovers | 8px |
| Pills, avatars | 999px |
| Banners / media artwork | 8px |

Borders are always `1px solid`; never 2px except focus rings and active tabs.

### 4.4 Elevation

Minimalism means almost no shadows.

- Default: none. Cards are defined by `--bg-panel` + `--border-subtle`.
- Popover/dialog: `0 16px 48px rgba(0, 0, 0, 0.55)` and a 1px border.
- Sticky topbar: 1px border only.
- Do not stack shadows. Do not use coloured glows.

---

## 5. Components

Each component lists its states. If a state is not listed, it does not exist yet.

### 5.1 Button

- Height 32px (sm) / 36px (md); padding 12px horizontal; radius 6px; `text-body-strong`.
- **Primary:** `--accent` background, `#16161e` text (dark-on-accent for contrast),
  hover `--accent-hover`. One per view.
- **Secondary:** transparent, `--border-strong` border, `--text-primary`; hover
  `--bg-hover`.
- **Ghost:** no border, `--text-secondary`; hover `--bg-hover` + `--text-primary`.
- **Danger:** transparent with `--danger` text/border; solid `--danger` only on final
  confirmation.
- **Disabled:** 40% opacity, `cursor: not-allowed`, no hover shift.
- **Loading:** label stays, a 12px spinner replaces the leading icon, button is disabled
  but keeps its width.
- Icon-only buttons: 32×32, `aria-label` mandatory, tooltip on hover/focus.

### 5.2 Input / Select / Search

- Height 34px; `--bg-canvas` fill; 1px `--border-subtle`; radius 6px; 10px padding.
- Placeholder `--text-muted`; entered text `--text-primary`.
- Focus: border `--accent` + 2px ring at 30% opacity (do not rely on the ring alone).
- Invalid: border `--danger`; message in `text-small` below, prefixed with `!`.
- Search: leading ``-style glyph or SVG, `Esc` clears, `Enter` submits. Debounce 250ms.
- Selects use the native control styled with `color-scheme: dark`; no custom dropdown
  unless keyboard behaviour is fully reimplemented.

### 5.3 Card / Panel

- `--bg-panel`, 1px `--border-subtle`, radius 8px, padding 16px.
- No shadow. No hover lift. Hover changes border to `--border-strong` only if the whole
  card is clickable.
- Poster card: 2:3 artwork, radius 8px, title `text-title` two-line clamp, meta in
  `text-small`/`--text-secondary`. Progress bar sits flush at the bottom edge.

### 5.4 Status pill / badge

- `text-micro`, uppercase, radius 999px, padding `2px 8px`.
- Background: status colour at 14% opacity. Text: status colour (or `--text-primary` for
  neutral).
- Always includes a label; colour is reinforcement, not the message.
- Mapping:
  - `available` → green
  - `downloading` / `active` → blue pill; the progress fill may use `--volt` (§11)
  - `queued` → muted/neutral
  - `paused` → yellow
  - `stale` / `partial` → yellow with an explicit `STALE` / `PARTIAL` label
  - `failed` / `cancelled` → red

### 5.5 Table

- Header: `text-micro` uppercase, `--text-muted`, `--bg-panel-alt`, 1px bottom border.
- Rows: 40px min height, 1px `--border-subtle` divider, hover `--bg-hover`.
- Numeric columns right-aligned with tabular numerals.
- Sticky header inside scroll containers.
- Empty table is not an empty box: show an empty state instead.

### 5.6 Navigation

- Item height 32px, 8px horizontal padding, radius 6px, `text-body`.
- Default `--text-secondary`; hover `--bg-hover` + `--text-primary`.
- Active: `--bg-selected`, `--text-primary`, 2px `--accent` left rule inset 8px, weight 600.
- Icons 16px, 8px gap, `--text-muted` when inactive.
- Sidebar footer holds version + service status dot (green/red, with text).

### 5.7 Dialog / sheet

- Max width 520px (`sm`) / 720px (`md`); `--bg-panel`; 1px `--border-strong`; radius 8px;
  overlay `rgba(12, 14, 20, 0.7)`.
- Title `text-title`; body `text-body`; actions right-aligned, primary last.
- Focus trap, `Esc` closes, initial focus on the first safe action.
- Mobile: bottom sheet, radius 8px 8px 0 0, drag handle 32×4px `--border-strong`.

### 5.8 Toast

- Bottom-right desktop, top mobile. Max 360px. `--bg-panel` + left 2px status rule.
- Title `text-body-strong`, detail `text-small`. Auto-dismiss 5s; errors persist until
  dismissed. No icons larger than 16px.

### 5.9 Empty / loading / error

- **Empty:** one line of what this is, one line of what to do, one primary action. Centred,
  max 48ch. A single large muted glyph is allowed.
- **Loading:** skeletons use `--bg-hover` with a slow 1.6s opacity pulse; no spinners for
  page-level loads. Spinners only inside buttons.
- **Error:** inline block with `--danger` left rule, plain-language message, and a
  `Retry` secondary button. Never a raw stack trace or exception class.

### 5.10 Media kind components

- **Kind badge:** `text-micro`, uppercase, 14% tint of the kind colour, colour text,
  radius 4px (not a pill — pills are reserved for status). Label is `MOVIE`, `TV` or
  `ANIME`.
- **Poster card:** 2px top edge in the kind colour, artwork 2:3, title and meta neutral.
  The edge is absent in list/table views to avoid a rainbow column.
- **Filter chips:** neutral by default; selected chip uses `--bg-selected` with a 1px
  border and text in the kind colour. Multi-select is allowed; no "all" chip colour.
- **Detail header:** 2px left rule in the kind colour, 12px from the title. One rule per
  page, never one per section.
- Never place two kind colours adjacent as a "legend"; the labels carry the meaning.

---

## 6. Iconography

- **Decided: hybrid.** 16px inline SVG components (Lucide-style geometry) cover every
  action and navigation icon. Nerd Font glyphs are used only for terminal-native concepts
  (file, folder, hash, torrent, terminal) and only from the bundled subset whitelist.
- 16px grid, 1.5px stroke, `currentColor`. 20px only in empty states.
- Never use emoji as UI icons.
- Icons in buttons are leading, 16px, 8px gap. Icon-only buttons always have `aria-label`
  and a tooltip.
- Status is never icon-only.

---

## 7. Motion

- Durations: 120ms (hover/colour), 160ms (popover/dialog), 200ms (drawer/sheet).
- Easing: `cubic-bezier(0.2, 0, 0, 1)` (ease-out). No bounce, no spring, no parallax.
- Animate only `opacity`, `transform`, `background-color`, `border-color`, `color`.
- Progress bars interpolate width/scale linearly; no shimmer.
- Respect `prefers-reduced-motion: reduce`: remove all non-essential transitions and the
  skeleton pulse.

---

## 8. Accessibility

- Minimum target size 32×32 (mobile 44×44).
- Visible focus on every interactive element; never `outline: none` without a replacement.
- Semantics: `nav`, `main`, `header`, `button`, `a`, `dialog`, `table`. Clickable `div` is
  a bug.
- Live regions for download/toast updates (`aria-live="polite"`); errors `assertive`.
- Colour is never the sole carrier of meaning (pair with label/icon).
- Honour `prefers-reduced-motion` and `prefers-contrast: more` (bump borders to
  `--border-strong`, text to `--text-primary`).
- Keyboard: every flow completable without a pointer; `Esc` closes overlays; arrow keys
  move within grids/lists where expected.

---

## 9. Copy

- Sentence case everywhere. No Title Case, no ALL CAPS except micro labels.
- Terse, factual, no exclamation marks. "Download queued", not "Awesome! Your download is
  on its way!".
- Buttons are verbs: `Search`, `Add to library`, `Retry`, `Remove`.
- Errors say what happened and what to do: "qBittorrent did not respond. Check that
  `qbittorrent-nox` is running, then retry."
- Never expose internal codes in the primary message; put stable codes in `text-micro`.
- Empty states invite an action, not an apology.

---

## 10. Tailwind v4 integration

Tokens live in `web/src/styles/theme.css` and are imported once by `index.css`.

```css
@import "tailwindcss";

@theme {
  --color-canvas: #16161e;
  --color-panel: #1a1b26;
  --color-panel-alt: #1f2335;
  --color-hover: #292e42;
  --color-border-subtle: #292e42;
  --color-border-strong: #3b4261;
  --color-fg: #c0caf5;
  --color-fg-muted: #a9b1d6;
  --color-fg-comment: #565f89;
  --color-accent: #7aa2f7;
  --color-accent-hover: #89ddff;
  --color-success: #9ece6a;
  --color-warning: #e0af68;
  --color-danger: #f7768e;
  --color-info: #89ddff;
  --color-kind-movie: #ff9e64;
  --color-kind-series: #7dcfff;
  --color-kind-anime: #bb9af7;
  --color-volt: #c8ff3c;

  --font-sans: "Caskaydia Cove NF", "CaskaydiaCove Nerd Font Mono",
    "JetBrainsMono Nerd Font", ui-monospace, monospace;
  --font-mono: var(--font-sans);

  --radius-control: 6px;
  --radius-panel: 8px;
}

:root {
  color-scheme: dark;
}
```

- Replace the `slate-*` utilities currently used in `HealthCard.tsx` and the hardcoded
  `#080c14`/`#e2e8f0` in `index.css` with these tokens.
- Use utilities (`bg-panel`, `text-fg-muted`, `border-border-subtle`) rather than arbitrary
  values. Arbitrary hex in a component is a review failure.

---

## 11. Marathon broadcast layer

A small, sanctioned borrow from Marathon's poster and broadcast identity: acid lime,
oversized condensed lettering, registration crosses, hazard bands, ticker rails and
technical microcopy. It is seasoning — **at most ~10% of any screen** — applied on top of
Tokyo Night and Caskaydia. The app must still read as a media librarian, not a game menu.

### 11.1 Borrow / don't borrow

| Borrow | Don't borrow |
|---|---|
| Acid-lime signal colour (`--volt`) | Lime page or panel backgrounds |
| Oversized condensed wordmark (SVG asset) | A second display font in the UI |
| Registration crosses at hero corners | Crosses scattered across every card |
| 45° hazard stripe bands | Stripes behind text or inside tables |
| Barcode / ticker rails as dividers | Ticker noise around forms and settings |
| Technical microcopy with codes | Fake terminal output as decoration |
| Cropped type bleeding off-edge | Cropping labels or actionable text |
| Rare RGB-split glitch on artwork | Glitch on body copy or controls |
| Coarse grain on hero surfaces | Grain over text-heavy panels |

### 11.2 Volt

`--volt: #c8ff3c`. High contrast on both directions: 15.27:1 against `#16161e`, so
`#16161e` text on a volt fill is explicitly allowed.

Volt appears in exactly four places:

1. The SVG wordmark and the splash screen.
2. One rule, underline or index numeral in an empty state or the 404 page.
3. The fill of an **active download progress bar** ("transmission"); the status pill
   remains blue and labelled.
4. A single 24px ticker line in the library hero, if that hero exists.

Everything else uses the base palette. Volt is **not** success green, not a button, not a
link, and not a kind colour. Never place volt text directly beside a success pill without
labels separating them.

### 11.3 Graphic devices

- **Registration cross:** 12×12px, 1px `--text-muted` at 60%, four max, inset 16px from
  the corners of a hero or empty state.
- **Hazard band:** 45° repeating gradient, 4px volt / 8px transparent, 48–64px band,
  max one per view, never behind text. Use as a top or bottom edge on an empty state.
- **Tick rail:** a row of 1px vertical bars (heights alternating 8/12/16px, 2px gap),
  `--border-strong`. Used as a divider above metadata; never as a loading indicator.
- **Index numeral:** `text-mega`, 64–96px, either `--border-strong` or `--volt`, cropped
  at the baseline. One per screen (`01`, `02`).
- **Technical microcopy:** `DAKU / LAN-ONLY`, `LIB 28.93`, `T-004 // ANILIST`,
  `TX 12.4 MB/S`. Uppercase, `text-micro`, `--text-muted`, slash-separated. Never invent
  data; every code must be real or clearly decorative (`//`, not a fake version).

### 11.4 Wordmark

The brand is an SVG asset at `web/src/assets/daku-wordmark.svg`, built from condensed,
bevel-cornered letterforms so it does not depend on a font. It may be set in `--volt`,
`--text-primary` or knocked out of a volt block, and may bleed off the left or bottom
edge on splash/404 only. It is never a clickable control and never appears inside tables,
dialogs or the topbar (the topbar uses `text-h1` Caskaydia).

### 11.5 Glitch

- Allowed only on poster hover and the empty-state illustration.
- Two frames, 90–120ms total, RGB split via `text-shadow`/`filter` in
  `#ff007c` and `#7aa2f7`, 2px offset.
- Never loops, never on text under 24px, never on buttons, inputs or links, never more
  than 3 flashes per second.
- Disabled entirely under `prefers-reduced-motion: reduce`.

### 11.6 Grain and texture

- Static SVG `feTurbulence` overlay, 2–4% opacity, hero/empty surfaces only.
- No scanlines, no CRT curvature, no vignette, no paper texture.
- Grain sits behind text and never reduces its contrast.

### 11.7 Where it may appear

| Surface | Marathon treatment |
|---|---|
| Splash / first paint | Full wordmark, volt on `--bg-deep`, crosses, one hazard band |
| Empty library | Index numeral, one device (crosses or hazard), single SVG wordmark |
| 404 / not found | Cropped wordmark, volt rule, technical microcopy |
| Active download hero | Volt progress fill + one tick rail; everything else calm |
| Library hero (optional) | One ticker line, four crosses, no stripes |
| Everything else | Tokyo Night only |

The rule of one: **one graphic device per section**. Crosses *or* stripes *or* a numeral.
If you want two, you are designing a poster, not an app.

---

## 12. Do / Don't

| Do | Don't |
|---|---|
| Use one accent per view | Rainbow nav icons |
| Separate with 1px borders | Drop shadows on every card |
| 14px body, tight 4px rhythm | 18px body, 64px gaps |
| Let posters carry colour | Colour-blocked page headers |
| One kind badge per card | A rainbow of kind borders in tables |
| Sentence case | Title Case Everywhere |
| Text + colour for status | Colour-only dots |
| `tabular-nums` in tables | Proportional numbers in columns |
| Monospace fallback | Sans-serif fallback when the font fails |
| Blue for every action | Kind colour on a primary button |
| One Marathon device per section | Crosses + stripes + glitch on one screen |
| Volt for transmission progress | Volt buttons, links or success pills |

---

## 13. Resolved decisions

| # | Decision |
|---|---|
| 1 | **Palette:** Tokyo Night (`#1a1b26` surfaces, `#16161e` canvas). Not Storm/Moon. |
| 2 | **Typography:** the entire UI is Caskaydia Cove Nerd Font. No sans anywhere, including headings and prose. |
| 3 | **Density:** 14px / 22px body, 32–36px controls, 40px table rows. |
| 4 | **Icons:** hybrid — inline SVG for UI, Nerd Font glyphs only for terminal-native concepts from the bundled whitelist. |
| 5 | **Accent model:** blue is the sole action accent. Movie = orange, series = cyan, anime = purple, used only as small identity marks (badge, poster edge, filter chip, detail rule). |
| 6 | **Font delivery:** bundled Latin subset (`web/public/fonts/caskaydia-cove-nf.woff2`) plus a curated Nerd Font glyph whitelist; `local()` tried first. |
| 7 | **Shell:** desktop sidebar (232px) that collapses to a drawer below 900px; mobile inherits the same navigation. |
| 8 | **Panels:** flat `--bg-panel` with a 1px `--border-subtle`; no shadows except overlays. |
| 9 | **Marathon layer:** allowed as a ~10% broadcast accent on brand/empty/error/progress moments only; never in forms, tables, settings or dialogs. |
| 10 | **Volt:** `#c8ff3c`, four placements only — wordmark/splash, empty/404 rule or numeral, active-download progress fill, optional library hero ticker. Never buttons or status. |
| 11 | **Wordmark:** a condensed bevel-cornered SVG asset; no second display font is added to the UI. |
| 12 | **Graphic devices:** one per section — registration crosses *or* hazard band *or* index numeral. Glitch is hover-only and reduced-motion aware. |
