# Monoke UI Style Guide

> **Purpose:** Improve Monoke's visual clarity, color, accessibility, and responsive behavior while preserving its existing product behavior and architecture.
>
> **Design direction:** Modern and expressive, dark-first, media-friendly. Use a blue / coral / gold palette, category-colored filters, restrained motion, keyboard support, and a practical interpretation of the 60/30/10 rule.
>
> **Primary instruction for Claude Code:** Treat this file as the visual and interaction contract. Inspect the repository and current UI before editing. Make focused visual improvements to the existing layout; do not rewrite the app, change backend behavior, or introduce a new frontend framework.

---

## 1. Working agreement for implementation

Before changing code:

1. Inspect the repository structure, README, dependency manifests, existing templates, CSS, JavaScript, routes, and tests.
2. Identify the current page structure and how Jinja2, htmx, and existing JavaScript handle navigation, filters, gallery content, keyboard shortcuts, fullscreen viewing, and media privacy.
3. Find existing CSS variables, design tokens, utility classes, and vendored assets. Extend or consolidate these rather than adding a competing system.
4. Record the existing behavior of each interactive component before restyling it.
5. Make the smallest coherent set of changes that improves the UI across the existing screens.

Implementation rules:

- Keep the existing Python, Jinja2, htmx, and asset approach. Do not migrate to React, Vue, Tailwind, or another framework.
- Prefer semantic HTML, CSS custom properties, and small progressive-enhancement JavaScript changes.
- Reuse installed fonts, icons, and libraries where practical. Do not add a dependency solely for decoration.
- Preserve existing routes, query parameters, filter semantics, pagination, search, bookmarks, media viewing, and server-rendered behavior.
- Do not rename backend fields, change data models, or alter API contracts for visual reasons.
- Do not remove existing controls or keyboard shortcuts. If a shortcut or control is unclear, inspect its implementation before modifying it.
- Keep changes focused on styling, layout, accessibility, and interaction feedback. Avoid unrelated refactors.
- Do not claim accessibility conformance without checking contrast and keyboard behavior.
- If a design request conflicts with current product behavior or privacy expectations, preserve behavior and explain the constraint in the final summary.

## 2. Visual principles

### Overall feel

Monoke should feel like a carefully designed personal media archive: dark and immersive, but not monochrome; colorful, but not noisy; information-dense, but easy to scan.

Use:

- Deep navy backgrounds rather than pure black.
- Clearly separated surface layers for navigation, filter controls, cards, and overlays.
- Blue as the primary interactive color.
- Coral and gold as supporting accents with distinct semantic roles.
- Category colors for content types, consistently applied.
- Strong typography hierarchy and intentional alignment.
- Small, purposeful transitions rather than decorative animation everywhere.

Avoid:

- A page made entirely of indistinguishable gray boxes.
- Applying bright accent colors to every element.
- Large gradients, glows, or shadows behind ordinary controls.
- Low-contrast muted text for important information.
- Color as the only indicator of selection, category, error, or status.
- Unnecessary layout shifts, hover-only actions, or tiny click targets.

### 60/30/10 rule

Use 60/30/10 as a visual proportion guideline, not a literal pixel measurement:

- **60% foundation:** primary background, quiet surfaces, and negative space.
- **30% structure:** elevated panels, cards, secondary surfaces, borders, and supporting content.
- **10% emphasis:** primary blue actions, coral highlights, gold accents, selected states, and key focal points.

The 10% accent share is a ceiling for the overall impression, not a requirement that every screen contain all three accents. Most controls should remain neutral until their state or role calls for color.

### Rule of thirds and composition

Apply a practical rule of thirds to page composition:

- Divide the main content area mentally into a 3 × 3 grid.
- Place the main focal point—such as the gallery heading, active search, selected item, or featured media—near a third-line or intersection when the content supports it.
- In gallery layouts, maintain a strong primary content region and align the sidebar, heading, and toolbar deliberately.
- Do not force strict thirds onto every card, form, or responsive viewport. Content readability and responsive flow take priority.
- Use consistent alignment edges. A grid should look intentional rather than arbitrarily offset.
- At narrow widths, switch from desktop composition to a clear vertical hierarchy instead of squeezing a three-column layout into a phone.

## 3. Color system

Create or adapt CSS custom properties in the existing primary stylesheet. Use these values as the starting palette and verify contrast in the implemented UI.

```css
:root {
  color-scheme: dark;

  /* Foundation: 60% */
  --color-bg: #0D111B;
  --color-bg-subtle: #111827;
  --color-surface: #171F2E;
  --color-surface-raised: #202B3D;
  --color-surface-hover: #26354A;

  /* Borders and separators */
  --color-border: #334155;
  --color-border-strong: #52627A;

  /* Text */
  --color-text: #F1F5F9;
  --color-text-secondary: #C0CAD8;
  --color-text-muted: #9AA9BD;
  --color-text-disabled: #748197;

  /* Brand and interactive blue */
  --color-primary: #72A7FF;
  --color-primary-hover: #91BAFF;
  --color-primary-pressed: #4F8DF5;
  --color-primary-soft: #1D3557;
  --color-primary-text: #08172D;

  /* Supporting accents */
  --color-coral: #FF8F82;
  --color-coral-soft: #482A32;
  --color-gold: #F5C76B;
  --color-gold-soft: #40351F;

  /* Semantic status colors */
  --color-success: #68D6A3;
  --color-success-soft: #173B32;
  --color-warning: #F5C76B;
  --color-warning-soft: #40351F;
  --color-danger: #FF8F98;
  --color-danger-soft: #48252F;
  --color-info: #85C9FF;
  --color-info-soft: #1C3549;

  /* Focus and overlays */
  --color-focus: #FFD166;
  --color-overlay: rgb(3 7 18 / 78%);

  /* Shape, spacing, motion */
  --radius-sm: 0.375rem;
  --radius-md: 0.625rem;
  --radius-lg: 0.875rem;
  --radius-xl: 1.125rem;
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.25rem;
  --space-6: 1.5rem;
  --space-8: 2rem;
  --space-10: 2.5rem;
  --motion-fast: 120ms;
  --motion-normal: 180ms;
  --motion-slow: 240ms;
}
```

### Color usage

- **Blue:** primary actions, links, active navigation, focusable selected controls, and the primary selected state.
- **Coral:** image/media category, important visual highlights, or a secondary action where the meaning is clear. Do not use coral for destructive actions unless the control is actually destructive and uses the danger token.
- **Gold:** video category or a secondary highlight, warning, or favorite/bookmark accent when consistent with the existing product semantics.
- **Green:** success and completed states only.
- **Red/pink:** errors and destructive actions only.
- **Neutral surfaces:** most page structure, card backgrounds, and inactive controls.

Do not assign colors based on appearance alone when the color would imply a misleading meaning. If the repository already has established category semantics, map them to the palette consistently.

### Contrast requirements

Target WCAG 2.2 AA:

- Normal text: contrast ratio at least **4.5:1** against its rendered background.
- Large text: at least **3:1**.
- Meaningful UI boundaries, icons, and graphical indicators: at least **3:1** against adjacent colors where WCAG requires it.
- Never use `--color-text-disabled` for essential readable information unless its contrast is verified for the actual use.
- Check text on colored chips, hover surfaces, selected controls, and overlays separately. A color that works on the page background may fail on a different surface.
- Do not rely on a token name or visual intuition as proof of contrast. Test the actual foreground/background pairs in the final CSS.

If a chosen accent fails contrast, darken or lighten the foreground/background pairing rather than lowering the standard.

## 4. Typography

- Use the existing font stack if it is legible and consistent. Do not add an external font request without a clear benefit.
- Prefer a system sans-serif stack for UI text, for example: `system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`.
- Use a monospace stack only for technical metadata where it improves scanning.
- Body text should generally be 14–16px; do not make core UI labels smaller than 13px.
- Use clear type hierarchy: page title, section title, card title, primary metadata, secondary metadata.
- Use font weight and spacing to distinguish hierarchy before introducing more colors.
- Keep line-height comfortable: around 1.45–1.65 for body text and 1.2–1.35 for headings.
- Avoid excessive uppercase labels, tightly tracked text, and all-bold interfaces.
- Truncate long titles only when necessary; provide a way to access the full text where truncation would hide useful information.

Suggested scale (adapt to the current design):

| Role | Suggested size | Weight |
|---|---:|---:|
| Page title | 1.5–2rem | 650–750 |
| Section heading | 1.125–1.375rem | 600–700 |
| Card title | 0.9375–1.0625rem | 550–650 |
| Body / control label | 0.875–1rem | 400–550 |
| Supporting metadata | 0.8125–0.875rem | 400–500 |

## 5. Layout, spacing, and surfaces

- Use a consistent spacing scale based on 4px increments.
- Align page headings, filter areas, and main content to a shared content edge.
- Keep the primary task—the archive/gallery browsing area—visually dominant.
- Separate major regions with spacing, surface contrast, and subtle borders rather than thick outlines around everything.
- Use `--radius-md` for controls and `--radius-lg` for cards/panels unless existing conventions suggest otherwise.
- Keep shadows subtle. Use surface contrast and borders as the primary depth cues.
- Avoid nesting many bordered panels inside one another.
- Keep text blocks at comfortable line lengths; metadata can be compact but must remain readable.
- Ensure sticky headers, filter bars, and overlays do not obscure keyboard focus or anchored content.
- Preserve the current information architecture unless an existing layout makes an interaction hard to find or use.

### Gallery and media cards

- Make thumbnails the visual focus, with a consistent and intentional card rhythm.
- Preserve image aspect ratios where possible; use `object-fit` according to the existing media behavior.
- Keep title, source, date, and category metadata in a predictable order.
- Use neutral card surfaces with restrained borders. Reserve accent color for category indicators, selection, or meaningful actions.
- Make the entire card clickable only if that is already the interaction model or can be implemented without conflicting with nested buttons and links.
- Ensure action buttons remain discoverable on touch devices; do not hide essential actions until hover.
- Show a clear hover and keyboard-focus treatment without changing card geometry.
- Empty, loading, and error states should have useful explanatory text and a deliberate visual treatment.

### Header, navigation, and toolbars

- Keep primary navigation easy to locate and visually distinct from secondary actions.
- Use blue for the active navigation state, with a text or shape indicator as well as color.
- Group related controls and use spacing to clarify their relationships.
- Give icon-only buttons accessible names and tooltips where useful.
- Keep destructive actions visually separate from routine browsing actions.

## 6. Color-coded filters

Filters are a key part of the Monoke experience. Their colors must be consistent, accessible, and understandable.

### Category mapping

First inspect the actual categories and filter types in the code. Apply category colors only to categories that really exist. Use the following suggested mapping when it fits the current data model:

| Category | Suggested accent | Suggested soft background | Meaning |
|---|---|---|---|
| Images / photos | Coral `#FF8F82` | `#482A32` | Visual media |
| Videos | Gold `#F5C76B` | `#40351F` | Moving media |
| Links / articles | Blue `#72A7FF` | `#1D3557` | Web content |
| Favorites / bookmarks | Gold `#F5C76B` | `#40351F` | Saved by user |
| Available / success state | Green `#68D6A3` | `#173B32` | Positive status |
| Warning / needs attention | Gold `#F5C76B` | `#40351F` | Caution |
| Error / destructive state | Pink-red `#FF8F98` | `#48252F` | Error or destructive action |

This is a suggested visual mapping, not permission to change what a filter means. If multiple real categories need distinct colors, add tokens only when needed and verify their contrast.

### Filter states

Every filter must have a distinguishable state:

1. **Inactive:** neutral surface, readable label, subtle border.
2. **Hover:** slightly elevated surface or stronger border; no layout shift.
3. **Selected / active:** category accent or blue selected treatment, plus a non-color indicator such as a checkmark, selected icon, border, or `aria-pressed="true"` for toggle buttons.
4. **Keyboard focus:** a clearly visible focus ring that is not confused with the selected state.
5. **Disabled:** visibly disabled and genuinely disabled in behavior; retain enough contrast to understand the label where practical.
6. **Applied/loading:** communicate loading or applied status when a filter operation takes time.
7. **No results:** explain that no items match and provide a clear way to remove or reset filters.

For selected category chips, prefer a tinted category background and readable foreground. Do not place white text automatically on every accent; choose the foreground that meets contrast.

### Filter interaction guidance

- Use semantic buttons for toggle chips, with `aria-pressed` reflecting state, unless the current implementation uses a proper checkbox or another semantically correct control.
- Use real form labels for checkbox, radio, and select controls.
- Make the selected state clear in both visual styling and accessible state.
- If filters are applied through htmx, retain working form submission, query parameters, history behavior, and keyboard interaction.
- Preserve existing multi-select versus single-select semantics.
- Provide a visible “Clear filters” or “Reset” action when the current UI supports it or when there is a clear need and the existing behavior can be reused.
- Do not create new filter semantics or change result ordering as part of a styling task.
- Ensure chip labels wrap or scroll appropriately on small screens; avoid clipped labels.
- If category color is repeated in a legend, badge, or result card, keep the mapping identical throughout the app.

## 7. Buttons, inputs, and controls

### Buttons

- Primary action: blue background with a foreground that meets contrast requirements.
- Secondary action: raised or neutral surface with a clear border.
- Tertiary action: low-emphasis text or icon button with a generous hit area.
- Destructive action: danger styling, separated from routine actions.
- Provide hover, focus-visible, active/pressed, disabled, and loading states.
- Avoid using color alone to distinguish primary and secondary actions.

### Inputs and search

- Use a clear border and surface contrast to show input boundaries.
- Keep placeholder text secondary; it must not replace a persistent label for important fields.
- Use a visible focus ring and preserve browser-native input behavior unless there is a verified reason to change it.
- Make search clear/reset affordances keyboard accessible and correctly labelled.
- Preserve current query values when the existing app expects them to persist through filtering or navigation.
- Validation and errors should be written in text and associated with the relevant control.

### Icons

- Prefer the icon set already installed in the project.
- Avoid emoji as substitutes for core interface icons.
- Use consistent icon size and stroke weight.
- Decorative icons should be hidden from assistive technology; meaningful icon-only controls need an accessible name.
- Do not introduce an icon dependency solely to restyle a handful of buttons.

## 8. Accessibility and keyboard behavior

Build to WCAG 2.2 AA as the target, while preserving the application's current keyboard model.

### Keyboard

- Every interactive element must be reachable and operable with a keyboard.
- Preserve and test existing shortcuts, especially those related to the gallery, fullscreen viewer, navigation, and closing overlays.
- Use native `<button>`, `<a>`, `<input>`, `<select>`, and form elements rather than clickable `<div>` elements.
- Do not create a keyboard trap in a modal or fullscreen viewer. If focus is intentionally contained inside a modal, provide a reliable exit and restore focus to the trigger on close.
- Support `Escape` to close a modal/viewer where appropriate and consistent with the current behavior.
- Ensure keyboard users can see which element is focused at all times.
- Do not remove browser focus outlines without replacing them with a stronger visible indicator.

### Focus style

Use a consistent `:focus-visible` treatment, adapting for each surface:

```css
:where(a, button, input, select, textarea, [tabindex]):focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 3px;
}

:where(button, input, select, textarea):disabled {
  cursor: not-allowed;
}
```

Avoid applying `tabindex="0"` to non-interactive elements without a specific accessibility reason. Verify the focus ring against both the page background and raised surfaces.

### Touch targets and semantics

- Aim for at least 44 × 44 CSS pixels for common touch controls where layout permits; never go below WCAG's applicable target-size requirements without a documented exception.
- Use meaningful headings in a logical order.
- Use landmarks such as `<header>`, `<nav>`, `<main>`, and `<footer>` appropriately.
- Give form controls visible labels or accessible names.
- Use `aria-live="polite"` sparingly for asynchronous status updates that would otherwise be missed; do not announce entire refreshed galleries unnecessarily.
- When htmx swaps content, ensure focus and screen-reader context remain sensible. Do not move focus on every routine update unless required by the interaction.
- Make links distinguishable from surrounding text without depending on color alone.

### Motion and reduced motion

- Keep transitions short and purposeful, generally 120–240ms.
- Animate opacity, color, and small transforms where useful; avoid large movement, bouncing, and continuous decorative motion.
- Do not animate layout properties if it causes jank or content movement.
- Respect reduced-motion preferences:

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

Review this rule against existing behavior so that it does not accidentally break essential loading or status feedback.

## 9. Responsive behavior

Use the existing breakpoints if they are consistent. If none exist, establish a small set of CSS breakpoints based on content needs rather than device names.

- **Wide desktop:** show the full navigation and filter controls; use a balanced multi-column gallery with comfortable gutters.
- **Small desktop / tablet:** reduce gallery columns, allow secondary controls to wrap, and make filter groups compact without hiding essential actions.
- **Mobile:** use a single-column or content-appropriate compact gallery; stack major regions, keep search and the most important filters easy to reach, and avoid horizontal page overflow.
- Let long filter groups wrap or move into an accessible disclosure/drawer only if the existing app can support it cleanly.
- Any drawer, popover, or modal must work with keyboard, focus, and screen readers.
- Keep controls usable at 200% zoom and at narrow viewport widths.
- Use fluid sizing (`min()`, `max()`, `clamp()`, grid `minmax()`) when it simplifies responsive layout.
- Avoid fixed widths that break on translated text, long labels, or browser zoom.
- Verify fullscreen media viewing on touch and desktop, including close controls and orientation changes where applicable.

Responsive CSS should improve the current layout, not create a parallel mobile implementation without need.

## 10. Media privacy and viewer behavior

Monoke's existing media privacy and viewing behavior must be preserved.

- Do not remove, weaken, or bypass existing blurred-media defaults, privacy settings, or user-controlled reveal behavior.
- Do not expose hidden media by changing default CSS, markup attributes, loading logic, or viewer initialization.
- Preserve the current semantics for reveal/hide controls, including accessible labels that reflect the actual state.
- If media is blurred, the reveal control must be keyboard accessible and its state understandable without color alone.
- Do not place text or controls over a media thumbnail in a way that makes the reveal action inaccessible.
- Preserve fullscreen viewer navigation, close behavior, and any existing keyboard shortcuts.
- Ensure overlay text and buttons have adequate contrast over variable image/video content; use a stable overlay or backing surface when needed.
- Avoid fetching or preloading additional media solely for decorative effects.
- Do not change media source URLs, caching behavior, permissions, or backend privacy checks as part of a UI polish task.

## 11. htmx and dynamic states

Inspect the current htmx setup and retain its conventions.

- Ensure refreshed filter results use the same card and category styling as initial server-rendered results.
- Style loading, success, empty, and error states intentionally.
- Use existing htmx indicators and events where available instead of adding a second request-state system.
- Avoid double-submitting controls or making controls appear unresponsive during requests.
- Do not make a control look selected before its state is actually applied unless optimistic UI is already an intentional behavior.
- Ensure dynamically inserted content does not break keyboard navigation or focus.
- Check for duplicate IDs in Jinja2-rendered repeated components.
- Keep styling resilient when optional metadata is absent or titles are unusually long.
- Avoid inline style duplication in templates when a reusable class or token can express the same state.

## 12. CSS implementation standards

- Use CSS custom properties for shared colors, spacing, radii, and motion values.
- Prefer component-level classes with clear names consistent with the existing project.
- Avoid broad selectors that unexpectedly restyle unrelated forms or content.
- Avoid `!important` except for narrowly justified accessibility overrides, such as a carefully tested reduced-motion rule.
- Do not use color values scattered throughout templates when a token already exists.
- Avoid duplicating the same rules across pages. Reuse existing shared stylesheets.
- Keep selectors specific enough to avoid collisions but not so specific that future changes become difficult.
- Use `:hover` for pointer feedback and `:focus-visible` for keyboard focus; do not rely on hover alone.
- Preserve browser zoom, text resizing, and native control semantics.
- Check that color tokens work across all existing pages, not just the first page inspected.

## 13. Suggested rollout order

Implement in small, verifiable stages:

1. **Audit:** identify templates, shared CSS, interactive components, current tokens, tests, and preserved behaviors.
2. **Tokens and foundations:** introduce the dark palette, typography, spacing, borders, and focus styles in the shared styling layer.
3. **Navigation and controls:** polish header, buttons, search, form controls, and toolbars.
4. **Filters:** apply consistent category colors and distinct selected/focus/disabled/loading states.
5. **Gallery and viewer:** improve cards and media presentation without changing media behavior or privacy.
6. **Responsive pass:** test narrow, medium, and wide layouts; remove overflow and inaccessible hover-only actions.
7. **Verification:** run available tests, inspect representative pages, test keyboard interactions, and measure key contrast pairs.

Do not attempt a broad rewrite in one pass. Prefer small changes that can be checked against existing behavior.

## 14. Verification checklist

Claude Code should verify each applicable item before considering the work complete.

### Visual consistency
- [ ] Dark navy foundation is consistent across existing screens.
- [ ] Blue, coral, and gold have clear, consistent roles and are not overused.
- [ ] The 60/30/10 principle is reflected in the overall visual balance.
- [ ] Page focal points and alignment are intentional; the rule of thirds is applied where appropriate rather than forced.
- [ ] Typography, spacing, borders, radii, and surfaces use a coherent system.
- [ ] Category colors remain consistent across filters, cards, badges, and legends.
- [ ] Loading, empty, error, and disabled states are styled.

### Accessibility
- [ ] Normal text contrast meets 4.5:1 and large text meets 3:1.
- [ ] Relevant UI boundaries and focus indicators have sufficient contrast.
- [ ] Keyboard navigation reaches all interactive controls in a logical order.
- [ ] Focus is visible and not hidden behind sticky elements or overlays.
- [ ] Active filters are distinguishable without color alone.
- [ ] Controls have accessible names and form controls have labels.
- [ ] Modal/viewer focus, Escape behavior, and focus restoration are correct where applicable.
- [ ] Reduced-motion preferences are respected.
- [ ] At 200% zoom and narrow viewport widths, essential content remains usable.

### Responsive and interaction behavior
- [ ] No unintended horizontal overflow at mobile, tablet, and desktop widths.
- [ ] Essential actions are not hover-only.
- [ ] Touch targets are comfortable and meet applicable requirements.
- [ ] Filter behavior, query parameters, and reset/clear behavior still work.
- [ ] htmx updates retain correct styles and sensible focus behavior.
- [ ] Gallery navigation and fullscreen viewer controls still work.
- [ ] Existing keyboard shortcuts still work.
- [ ] Media blurring and privacy behavior are unchanged.

### Engineering
- [ ] No frontend framework migration or unnecessary dependency was added.
- [ ] Existing routes, data models, and API contracts remain unchanged.
- [ ] Shared tokens and styles are reused rather than duplicated.
- [ ] Existing automated tests and project checks pass, or any failures are clearly reported.
- [ ] A manual smoke test was performed on the main browsing, filtering, and media-viewing flows.
- [ ] The final summary lists changed files, behavior checks performed, and any known limitations.

## 15. Final instruction to Claude Code

Inspect first, then implement. The goal is a **focused UI polish**, not a product redesign. Make Monoke more expressive through disciplined color, stronger hierarchy, clear filter states, and responsive composition while preserving existing functionality and privacy. Follow the repository's established conventions, run its available checks, and report what was actually verified. Do not state that an accessibility requirement passes unless it was tested.
