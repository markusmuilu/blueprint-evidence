# DESIGN.md - Blueprint Evidence

The design system for the demo app in `app/static/`. Tokens live at the top of `app/static/app.css`;
if the CSS and this file disagree, this file wins.

## Context

- Artifact type: product tool (Operate mode), with one analytical view (Results).
- Positioning: technical, utilitarian.
- Audience: a site engineer checking a quantity before ordering or signing off; at the hackathon,
  judges who want to verify an answer in seconds. Primary action: ask one question about one
  drawing and check the answer against the drawing.
- Adjectives: exact, calm, accountable, dense.
- Visual translations: exact -> mono tabular figures for every number, no rounding in the UI that
  the route did not do; calm -> one accent, no motion except state changes; accountable -> every
  claim in the answer panel is a numbered line, and counted items can be located on the drawing;
  dense -> 13 to 14 px body, tight rows, no decorative cards.
- Aesthetic essence: night drafting table.
- References: admire the layout of construction platforms seen at the event booth (icon rail, main
  drawing view, right-hand detail panel with "where" and "why"), Linear and Sentry for density with
  restraint; avoid generic AI dashboards (gradient hero metrics, card grids, glass).
- Mode: dark only. Chosen from the use scene (a dim exhibition hall and site offices in the evening;
  the drawings themselves are white and should be the brightest thing on screen), not by category.
- Density: dense. Constraints: plain HTML, CSS and JS, no build step, served by FastAPI.

## Aesthetic

- Direction: bespoke, "drafting table": slate blue-black surfaces, white drawings as the light
  source, one blueprint-blue accent for interaction and citations.
- Defining trait: the drawing is the hero; chrome stays below it in lightness and saturation.
- Signature move: numbered evidence citations. The "Why" list is numbered like footnotes in mono
  boxed numerals; counted items carry the same `#n` as the boxes drawn on the overlay, and selecting
  one puts a reticle on that item in the viewer and dims everything else.

## Typography

- UI: IBM Plex Sans (Google Fonts, OFL), 400/500/600.
- Figures, file names, codes, citations: IBM Plex Mono (Google Fonts, OFL), 400/500. Mono is used
  for data and measurement only, never as decoration.
- One superfamily on purpose: Operate surfaces read better with one voice.
- Scale: ratio about 1.2, fixed px (product UI, not fluid).

| step | size | use |
|---|---|---|
| xs | 12px | labels, table headers, meta |
| sm | 13px | panel text, lists, table cells |
| base | 14px | body, inputs |
| md | 17px | drawing title, headline sentence |
| lg | 20px | unit next to the answer |
| xl | 24px | page title |
| display | 44px | the answer value (mono) |

## Color

- Strategy: blueprint blue fits the product name and the trade; hue 235 sits outside the
  indigo/violet band (250 to 320) that marks generic AI UI. Status colors are reserved for answer
  tiers and checks and always come with an icon and words.
- Distribution: about 85 neutral, 10 drawing white, 5 accent.

| role | value |
|---|---|
| bg | oklch(0.165 0.012 245) |
| well (viewer, rail) | oklch(0.135 0.010 245) |
| surface (panels) | oklch(0.198 0.014 245) |
| raised (hover, rows) | oklch(0.232 0.016 245) |
| overlay (popover, tooltip) | oklch(0.268 0.018 245) |
| border / strong | oklch(0.315 0.016 245) / oklch(0.42 0.018 245) |
| fg / fg-2 / muted | oklch(0.930 0.008 245) / oklch(0.780 0.014 245) / oklch(0.690 0.016 245) |
| accent / hi / fg-on-accent | oklch(0.760 0.105 235) / oklch(0.820 0.090 235) / oklch(0.180 0.030 240) |
| proven (success) | oklch(0.790 0.130 155) |
| estimate (warning) | oklch(0.830 0.130 80) |
| cannot prove (error) | oklch(0.730 0.140 25) |

Contrast, computed from the OKLCH values (WCAG ratio on surface / on the viewer well): fg 14.8 / 16.3,
fg-2 9.1 / 10.0, muted 6.6 / 7.2, accent 8.6 / 9.5, proven 9.9, estimate 10.6, cannot prove 7.2,
placeholder 4.6 / 5.1, text on the accent button 8.9.

## Spacing, radius, shadow

- Base 4px; scale 4, 8, 12, 16, 20, 24, 32. Tight inside groups (4 to 8), 16 to 20 between sections.
- Radius: 4px for panels and controls, pill for chips and badges. Nothing larger.
- Shadow: none. Elevation is lightness (well < bg < surface < raised < overlay) plus 1px borders.
  The only shadow is the reticle's dimming spread, which is functional.

## Layout

- Ask view: rail 56px | drawing library 300px | work area (question bar over the viewer) | answer
  panel 380px. The library collapses from the rail; under 1080px it floats over the work area; under
  760px everything stacks.
- Results view: one headline sentence instead of hero metrics, then the source table (grouped rows)
  beside a single-series bar chart, the baseline placeholder below the chart, then the per-object
  table. Max width 1320px.
- Signature layout move: the viewer is a recessed well with a faint 24px drafting grid (it is a
  measurement surface), and the drawing is the only white area on screen.

## Components and states

- Buttons: one primary (Ask, filled accent), secondary (outlined surface), quiet (text). Hover
  lightens, active nudges 1px, disabled 50% opacity, busy sets aria-busy and a progress bar in the
  viewer with elapsed seconds.
- Inputs: visible labels above, never placeholder-as-label; focus ring is a 2px accent box-shadow.
- Tables: text left, numbers right in mono tabular figures, 1px separators, group rows on raised.
- Chips: examples carry a language tag (EN, FI) and show the full question as a tooltip.
- Status badge: icon + words + color: "Proven", "Estimate, check highlighted items", "Cannot
  prove" with the reason underneath.
- Empty states teach: the viewer explains what will be drawn; the answer panel explains what the
  answer will contain; the library explains how to point at a dataset.
- Errors: toast with the server's message; the previous answer stays.

## Motion

- 120ms for hover and press, 180ms for toasts; one ease-out curve cubic-bezier(0.22, 1, 0.36, 1).
- Only color, opacity and transform change. Pan and zoom are direct manipulation, not animated.
- prefers-reduced-motion: transitions off, the progress bar becomes static.

## Iconography

- Hand-authored inline SVG set on a 16px grid, 1.5px stroke, round caps and joins, currentColor.
  No emoji, no icon font.

## Imagery

- The only imagery is the user's drawing and the evidence overlay drawn by the routes.

## Accessibility

- All controls are buttons or links with names; icon-only buttons carry aria-label.
- Viewer is focusable: arrows pan, plus and minus zoom, 0 fits.
- Status is never color alone (icon and text); check results say PASS or FAIL.
- Targets at least 24px; most controls 30 to 40px.
- aria-live on the answer panel and the busy state.

## Slop audit (2026-11-14, first build)

Checked against the deslop checklist and the impeccable craft floor:

- Pass: no purple or indigo, no gradient text, no glass, no glow, no card grid, no hero-metric
  triptych, no eyebrow labels, no emoji icons, no bounce easing, radius 4px, tabular figures in
  tables, one primary button per view, labelled inputs, designed empty and busy states.
- Watch: the viewer grid background is decorative-adjacent; kept because the viewer is a
  measurement surface, at 6% opacity.
- Deliberate deviation: one type superfamily (Plex Sans + Plex Mono) instead of a display and body
  pairing, because this is an Operate surface.
- Not done: light mode; a keyboard shortcut list; a full screen-reader pass.
