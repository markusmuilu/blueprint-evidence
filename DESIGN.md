# DESIGN.md - Blueprint Evidence

The design system for the demo app in `app/static/`. Tokens live at the top of `app/static/app.css`;
if the CSS and this file disagree, this file wins. Version 2 (2026-11-14) replaces the first build's
pills, chips and tinted boxes with ruled tables and plain coloured text; the audit that drove it is
at the end. Version 2.1 (2026-11-14) is a type pass: no display numeral, no small mono labels, a
library grouped by what the system can do, and a "Try these" list in the empty answer panel.
Version 2.2 (2026-11-14) adds view rotation and mirroring to the viewer toolbar and drag-resizable
side panels.

## Context

- Artifact type: product tool (Operate mode), with one analytical view (Results).
- Positioning: technical, utilitarian.
- Audience: a site engineer checking a quantity before ordering or signing off; at the hackathon,
  judges who want to verify an answer in seconds. Primary action: ask one question about one
  drawing and check the answer against the drawing.
- Adjectives: exact, calm, accountable, dense.
- Visual translations: exact -> mono tabular figures for every number, no rounding in the UI that
  the route did not do; calm -> one accent, hairline rules instead of boxes, no motion except state
  changes; accountable -> every claim in the answer panel is a numbered line, and counted items can
  be located on the drawing; dense -> 13 to 14 px body, 5px row padding, no decorative containers.
- Aesthetic essence: drawing office register.
- References: construction platforms seen at the event booth (a dense professional tool: icon rail,
  main drawing view, right-hand detail panel with plain text labels such as "Where" and "Why", thin
  rules, very little decoration). We take the feel, not the brand. Also Linear and Sentry for density
  with restraint. Avoid generic AI dashboards: pill chips, tinted status badges, card grids, gradient
  hero metrics, glass.
- Mode: dark only. Chosen from the use scene (a dim exhibition hall and site offices in the evening;
  the drawings themselves are white and should be the brightest thing on screen), not by category.
- Constraints: plain HTML, CSS and JS, no build step, served by FastAPI.

## Aesthetic

- Direction: an engineering register. Slate blue-black surfaces, white drawings as the light source,
  structure from type and 1px hairlines, one blueprint-blue accent.
- Defining trait: the drawing is the hero; chrome stays below it in lightness and saturation, and
  nothing in the chrome is a rounded box unless it floats over the drawing.
- Signature move: numbered evidence citations. The "Why" list is numbered like footnotes in square
  mono numerals; counted items carry the same `#n` as the boxes drawn on the overlay and the crops in
  the verification strip, and selecting one puts a reticle on that item in the viewer.

## Typography

- UI: IBM Plex Sans (Google Fonts, OFL), 400/500/600.
- Figures inside tables, file names, codes, citations: IBM Plex Mono (Google Fonts, OFL), 400/500. Mono
  is for data and measurement only, never decoration and never for words: toolbar labels, status
  lines, busy text and format codes (PDF, IFC, DXF) are Sans. Table header labels stay in Sans.
- The answer value is Sans, not mono and not a display size: 26px, weight 500, tabular lining
  figures, letter-spacing 0, with the unit right after it at the same size in weight 400 and fg-2.
  The same applies to the order quantity (20px, Sans 500). Reason: a 40px mono numeral read as a
  marketing hero stat, which the product should not be; the answer is a quantity to check, so it
  sits at the size of a heading.
- Fonts load from Google Fonts with `display=swap`; the fallback is Segoe UI, so on a machine with
  no network the layout holds but the numerals lose Plex's shapes. Checked 2026-11-14 in Chromium:
  Plex Sans 400/500/600 and Plex Mono 400/500 (latin subset) report `loaded`.
- No letter-spacing anywhere except 0, no weights under 400, no text under 12px (the 12px steps are
  citation numerals and chart ticks).
- One superfamily on purpose: Operate surfaces read better with one voice.
- Scale: ratio about 1.2, fixed px. Hierarchy comes from size and weight: section headings are fg
  600, labels are muted 500 at 13px, values are fg 400.

| step | size | use |
|---|---|---|
| xs | 13px | labels, table headers, meta, notes |
| sm | 13px | panel text, ruled rows, section headings |
| base | 14px | body, inputs, a text answer |
| md | 16px | drawing title, Results section headings, headline sentence |
| lg | 20px | order quantity |
| xl | 24px | page title |
| answer | 26px | the numeric answer and its unit (Sans 500) |

- Sentence case everywhere; no uppercase labels and no text-transform. Check results read "Pass"
  and "Fail". Acronyms (PDF, IFC, DXF, OCR) keep their capitals because that is how they are written.

## Color

- Strategy: blueprint blue fits the product and the trade; hue 235 sits outside the indigo/violet band
  (250 to 320). Two hues only in the answer: blue means proven or selected, amber means check this.
  "Cannot prove" is neutral text with its icon: it is an honest abstention, not an error. Red exists
  only for failures of the tool itself (a request that failed). Status never relies on colour alone:
  every tier has an icon and words.
- Distribution: about 85 neutral, 10 drawing white, 5 accent.

| role | value |
|---|---|
| bg | oklch(0.165 0.012 245) |
| well (viewer, rail, inputs) | oklch(0.135 0.010 245) |
| surface (picker, answer panel, dialogs) | oklch(0.190 0.013 245) |
| raised (hover, selected row) | oklch(0.228 0.015 245) |
| overlay (popover, tooltip, 3D card) | oklch(0.262 0.017 245) |
| rule (hairlines between rows and sections) | oklch(0.268 0.014 245) |
| border / strong (control edges) | oklch(0.330 0.016 245) / oklch(0.430 0.018 245) |
| fg / fg-2 / muted | oklch(0.930 0.008 245) / oklch(0.790 0.014 245) / oklch(0.680 0.016 245) |
| accent / hi / fg-on-accent | oklch(0.760 0.105 235) / oklch(0.830 0.085 235) / oklch(0.180 0.030 240) |
| warn (estimate, check, disagreement) | oklch(0.820 0.125 78) |
| err (tool failure only) | oklch(0.720 0.130 25) |

Contrast, computed from the OKLCH values (WCAG ratio on surface / on the viewer well): fg 15.0 / 16.3,
fg-2 9.6 / 10.4, muted 6.4 / 7.0, accent 8.8 / 9.5, warn 10.4 / 11.3, err 7.0 / 7.6, placeholder
4.7 / 5.1, text on the accent button 8.9.

## Spacing, radius, shadow

- Base 4px; scale 4, 8, 12, 16, 20, 24, 32. Rows 4 to 6px vertical padding; 12px between a heading and
  its rows; sections separated by a rule and 12 to 16px.
- Radius: 3px for controls (inputs, buttons, the floating toolbar and 3D panels, dialogs). 0 for rows,
  list items, thumbnails, citation numerals and status markers. No pills anywhere except the circles
  that mirror circles drawn on the plan (disagreement key).
- Shadow: none. Elevation is lightness (well < bg < surface < raised < overlay). The only shadow is
  the reticle's dimming spread, which is functional.

## Layout

- Ask view: rail 52px | drawing library 300px | work area (question bar over the viewer, verification
  strip under it) | answer panel 384px. The library collapses from the rail; under 1080px it floats
  over the work area; under 760px everything stacks and the rail becomes a top bar. The widths are
  defaults: above 1080px both side panels can be resized (see Resizable panels).
- Resizable panels (v2.2): the inner edge of the library (min 240, max 480) and of the answer panel
  (min 300, max 640) is a drag handle. The viewer always keeps at least 480px (the toolbar needs about 410px), so the
  effective maximum shrinks on narrow windows. The handle is a 9px hit area with a short 3px bar at mid height in the
  border colour; hover, drag and keyboard focus turn the bar accent and draw a 1px accent hairline over
  the full edge. It is a `role="separator"` with `aria-valuenow/min/max`: Left and Right arrows move the
  edge by 16px (Shift 48px), Home and End jump to the limits, Enter, Escape or a double-click restore the
  default. Widths are kept in localStorage (`bpe-picker-w`, `bpe-detail-w`, read and written inside
  try/catch, so a blocked store only forgets the width). Below 1080px the handles are hidden and the
  stored widths are ignored. The viewer re-fits on any size change while the view is still at its fit;
  a view the person has zoomed or panned keeps its place.
- Results view: unboxed sections, each a 16px heading over a full-width ruled table; one headline
  sentence instead of hero metrics; source table beside a single-series bar chart. Max width 1320px.
- The viewer is a plain recessed well (no grid texture). Empty and busy states sit at its top left like
  a status line; the busy state is a 2px accent bar across the top edge plus elapsed seconds.

## Components and states

- Ruled key/value table (`.kv`): dt muted at 104px, dd fg; every row has a 1px rule on top. Used for
  the answer facts, Where, the order check comparison, the order sheet totals and the empty answer
  panel (which shows what an answer will contain, row by row).
- Buttons: one primary per view (Ask, Print), filled accent. Secondary: transparent with a 1px border.
  Quiet: text only, underline on hover. Hover lightens, active nudges 1px, disabled 45% opacity, busy
  sets aria-busy and the viewer progress bar.
- Inputs: visible labels above, never placeholder-as-label; focus is a 1px accent border plus 1px ring.
- Focus elsewhere: 2px accent ring offset by 2px of bg; inside lists and rows an inset 2px accent ring.
- Library sources: plain text filters with mono counts; the current one has a 2px accent underline.
- Library groups: under the filters the list is grouped by what the system can do with the file, as
  plain text headings with a count and one sentence each: "BIM model (3D, proven)" (IFC), "CAD
  drawing (vector, proven)" (DXF; none are listed on their own in the dataset, so the group says
  so), "PDF plan (vector, proven; scale found on the sheet)" and "Scanned plan (pixels; estimates to
  check)" (PNG). Eight rows per group, then "Show more (n left)". Grouping uses the catalog's `ext`
  filter, no backend change. Source filter and search narrow every group; empty groups are hidden
  while filtering.
- Try these: the empty answer panel opens with four one-click examples (tiling on a Swiss plan, room
  area from an IFC model with the 3D view, a window count on a scan, and an upload hint), each a text
  command with one muted sentence, followed by the "each answer reads like this" table. A row is
  hidden when its drawing is not in the library (the Modal demo set is a subset).
- Library rows: ruled, no radius; selected row is raised with the file name in accent.
- Examples: a run of accent text commands after an "Examples" label; Finnish ones carry a muted mono
  "fi". The dataset's own questions sit in a disclosure under them.
- Status (answer panel): icon + words in the tier colour, no box: "Proven" (blue), "Estimate, check
  highlighted items" (amber), "Cannot prove" (fg-2) with the reason under it. Dataset answer:
  "matches" (blue, check icon) or "differs" (amber, warning icon).
- Text answers render at body size, clamped to six lines. Numeric answers are 26px (see Typography).
- Verification strip (under the viewer): one 104 x 78 crop per counted item or measured room, square
  corners, 2px border in accent for proven and amber (with the estimate icon) for items to check,
  numbered like the overlay. "Not this one" is a text command under the crop (amber once pressed);
  a removed crop is dashed and dimmed. Live count in mono, Save correction (secondary), Export (quiet).
- Viewer toolbar (v2.2): layer tag, zoom out, level, zoom in, fit | rotate left, rotate right, mirror
  horizontally, original orientation | header strip, open image. Plain 30px icon buttons, no labels;
  each has a text tooltip below it (overlay surface, 13px, with the shortcut) on hover and keyboard
  focus. Mirror is a toggle (accent when on); original orientation is disabled while the view is
  unchanged. Keys: R rotates right, Shift+R left, M mirrors, O restores the original (all while not
  typing in a field). The 3D view hides these buttons.
- View orientation (v2.2): rotation in quarter turns and a left-right mirror are a view transform on
  the image only; the file and the evidence are untouched. Rotation turns the view about its middle and
  keeps the zoom; at fit it re-fits. Pins, the reticle, disagreement circles and added items are placed
  in view space (their rectangles are transformed, circles stay circles), so numbers stay upright and
  never mirror. Verification strip crops are drawn in the same orientation as the viewer. A click for
  "Add missed item" is converted back to image pixels. Opening a different drawing resets the
  orientation; the evidence for the same drawing keeps it.
- Pins: proven items use the overlay's paper blue oklch(0.50 0.13 240), items to check the paper amber,
  so pins hold on the white drawing; a selected pin takes the accent. Number tags are square.
- Witnesses: one ruled row per format (format code in accent mono, route name, value in mono, tier
  icon). A value that disagrees, or stands uncorroborated, is boxed in a 1.5px amber rectangle and set
  in amber. Items where two page-aligned formats disagree are circled on the drawing (solid: counted
  only here; dashed: counted by the other format, missed here); the legend repeats that circle.
- Items list and "Why" list: ruled rows, no outer box, no fill.
- 3D view (IFC): Plan / 3D segmented switch in the viewer toolbar. Model in neutral slate per class;
  with an answer, used elements in the accent, rooms as accent volumes at 50%, deducted openings in
  amber. The storey panel and element card float over the canvas, so they keep a surface, a 1px border
  and 3px radius; inside them everything is ruled rows and text commands ("only", "Reset view").
- Order section and order sheet: order quantity in mono at lg size with its parts; the sheet is a
  dialog with an inputs band between two rules, the per-room ruled table, totals as a ruled list with
  the order quantity under a strong rule, and the order check beside it. Order check verdict: icon +
  words + colour (blue within 2%, amber over or short). Close is a plain X. The printable page is
  light, A4 landscape.
- Cost meter: two 4px log-scale bars, this answer in accent and the model baseline in muted, with the
  assumed prices written under them.
- Errors: toast with the server's message, red border; the previous answer stays.

## Motion

- 120ms for hover and press, 180ms for toasts; one ease-out curve cubic-bezier(0.22, 1, 0.36, 1).
- Only color, opacity and transform change. Pan and zoom are direct manipulation, not animated.
- prefers-reduced-motion: transitions off, the progress bar becomes static.

## Iconography

- Hand-authored inline SVG set on a 16px grid, 1.5px stroke, round caps and joins, currentColor.
  No emoji, no icon font. Icons never sit in a filled tile or circle; the rail mark is a bare glyph.

## Imagery

- The only imagery is the user's drawing and the evidence overlay drawn by the routes.

## Accessibility

- All controls are buttons or links with names; icon-only buttons carry aria-label.
- Viewer is focusable: arrows pan, plus and minus zoom, 0 fits. R, Shift+R, M and O rotate, mirror and
  restore the orientation (aria-keyshortcuts on the buttons). Panel handles are focusable separators.
- Status is never colour alone (icon and words); check results say Pass or Fail.
- Targets at least 24px (text filters 26px, text commands 24px); most controls 30 to 44px.
- aria-live on the answer panel and the busy state.
- Known gap: control borders (border on well, about 1.6:1) are below the 3:1 non-text contrast
  guideline; inputs are still identified by their visible labels and the darker well fill.

## Audit before v2 (2026-11-14)

AI tells found in the first build (file and line at commit 75a61f9):

1. Pill source filters, `.src` with `border-radius: 999px`: `app/static/app.css:185-193`.
2. Pill example chips with an uppercase EN/FI tag: `app.css:220-228`, `app.js:216`.
3. Pill status badge with tinted fill and border: `app.css:361-368`, `app.js:804`.
4. Pill "matches / differs" tag on the dataset answer: `app.css:376-378`, `app.js:791`.
5. Pill ring around a disagreeing witness value: `app.css:442-443`.
6. Icon in a filled accent tile as the logo: `app.css:99-103`, `index.html:40`.
7. Tinted accent squares for selected states (rail, library rows with an inset ring, segmented
   controls): `app.css:111`, `202`, `299`, `495`.
8. Boxed lists with filled rows (items, witnesses, order evidence, dataset questions):
   `app.css:400-404`, `432-436`, `582-583`, `233`.
9. Rounded-card soup on Results: every section a bordered surface box, plus a dashed box for the
   baseline: `app.css:461-463`.
10. Decorative drafting grid behind the viewer: `app.css:240-246`.
11. Centred empty and busy states in the viewer and a generic paragraph as the empty answer panel:
    `app.css:278-287`, `index.html:110-113`, `154-157`.
12. Too many colours in one panel: green, amber, red and blue all used for status: `app.css:25-30`.
13. Answer facts as a four-column label-over-value strip (a small hero-metric row): `app.css:371-373`.
14. A second bordered box under every crop in the strip ("Not this one"): `app.css:341-346`.
15. Filled inputs band in the order sheet and a circled X as the close icon: `app.css:597`,
    `ordersheet.js:231`; pill status tag on the printed sheet: `ordersheet.js:357`.
16. Long text answers set at display weight and size: `app.css:359`; a long witness value overflowed
    the answer panel: `app.css:442`.
17. Phone width: the viewer toolbar was clipped at the left edge: `app.css:289-291`.

Not present before or after: gradients, glow, glass, emoji, eyebrow labels, centred hero copy.

## Slop audit and self-critique (v2, 2026-11-14)

Checked against frontend-design-deslop's slop checklist, impeccable's craft floor and product-mode
rules, and taste-redesign's audit list. The impeccable critique ran in one context without its
detector (its launcher downloads a binary), so it is a degraded run: a design review only.

- Pass: no pills, no tinted badges, no card grid, no nested boxes, no hero-metric strip, no eyebrows,
  no gradient, glass, glow or grid texture, radius 3px or 0, one accent plus amber, tabular mono
  figures with numbers right-aligned, one primary button per view, labelled inputs, designed empty and
  busy states, sentence case, no em dashes in UI copy, phone layout at 390px without horizontal
  page scroll.
- Kept on purpose: floating boxes over the drawing and the 3D canvas (toolbar, storey panel, element
  card, dialog), because they must separate from busy content; filled number tags on pins and crops,
  because they repeat the evidence overlay drawn by the routes; square citation numerals, which are
  the signature; a dashed upload zone, because it is a drop target; a centred bottom toast; the
  amber box on an uncorroborated witness value, which follows the existing witness logic.
- Weak spots: the answer panel is long when witnesses, order and items are all present; the Results
  tables carry long file paths that push the source column off screen; control borders are low
  contrast (see Accessibility).
- Deliberate deviation: one type superfamily (Plex Sans + Plex Mono) instead of a display and body
  pairing, because this is an Operate surface.
- Not done: light mode; a keyboard shortcut list; a full screen-reader pass.

## Type pass (v2.1, 2026-11-14)

Markus's review of v2: the small all-caps-looking text in the top right of the viewer was hard to
read, and the large 47.68 read as an AI dashboard. Findings in the rendered app (computed styles,
Chromium, 1440 and 390 px): there was no `text-transform` or letter-spacing on labels; the top-right
viewer toolbar labels ("Drawing", "Evidence", the zoom level) were set in 12px Plex Mono, which
reads as terminal capitals on a dark ground. The answer was 40px Plex Mono with -0.02em tracking.

Changed: toolbar label and zoom to Sans at 13px; every sentence that was mono (busy text, 3D status,
scale and correction lines, "baseline", "Not measured yet", results sub-heading, format codes) to
Sans; --fs-xs 12px to 13px; the 11px mono items (citation numerals, tags, chart ticks) to 12px; the
answer to 26px Sans 500 with the unit at the same size; the order quantity to Sans; letter-spacing
removed from the page heading. Audit script: every visible text node on the Ask, status and Results
views was checked for text-transform, tracking, weight under 400, size under 12px and size over 26px;
the only hits left are the acronym codes and the 26px answer.

Not changed on purpose: file names, table numerals, citation numerals and the dataset answer in
mono (data); the Results tables; the printable order sheet.
