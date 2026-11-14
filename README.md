# Make Blueprints Talk

Junction 2026, Stoa challenge. Answer natural-language questions about construction drawings and show
where every answer came from.

Pre-existing code: none. Everything in this repository was built at Junction 2026.

## Problem

A question like "How many windows are drawn on this floor plan?" or "Kui suur on ruumi ESIK (3-2)
netopõrandapind korrusel 4.korrus?" arrives together with one drawing: a PDF, DXF, IFC model or a
scanned PNG. Counts must be exact, areas, lengths and volumes within 2%. Judging weighs accuracy,
reasoning a human can verify, creativity and low inference cost equally.

## Approach: a router over provable routes, with evidence

The core bet: most drawings carry more structure than their pixels. A PDF of a Swiss floor plan has a
DXF sibling with named layers; a plan rendered from an IFC model has the model next to it; a door
schedule sheet has a text layer. Reading that structure is free, exact and checkable, and it will not be
made obsolete by next year's vision model, because a model would still need to show its work. Vision is
the fallback, not the default.

```
question --> rule parser (EN DE FR IT FI DA ET) --> typed query {measure, object, label, room, apartment, level}
                       |                                   (Nemotron parse fallback for questions the rules miss)
drawing  --> router: cheapest route that can prove the answer
               1. dxf-geometry  DXF layers of the same drawing (Swiss Dwellings layer convention)
                  dxf-generic   any other DXF: AIA / ISO 13567 / plain-word layer and block names;
                                abstains when the asked class is absent, never a proven 0
               2. ifc           IFC entities, storey containment, quantity sets, space geometry
                  ifc-props     free-text and property questions: typed lookups over the model (types,
                                materials, fire rating, named elements, rooms, storeys, systems), every
                                value cited by GlobalId; tried when the numeric routes cannot answer
               3. pdf-text      text layer and schedules (door schedule rows; any other schedule table
                                through the generic reader, columns from header words or the drawn grid)
               3b. pdf-vector   the PDF's own linework, when no DXF/IFC twin exists (or --pdf-only):
                                room shapes, scale, doors, windows, fixtures; scanned PDFs abstain
                   pdf-generic  where pdf-vector does not recognise the convention: door swings and printed
                                room labels; always tier "estimate"
               4. raster        scans: panel segmentation, room labels read by local OCR (Finnish, Swedish,
                                English, German), a room-type detector checked against those labels
                                (raster-rooms), a trained detector for fixtures, doors and windows;
                                every count in a tier: "proven" or "estimate" (with the reason)
               5. nemotron      last resort only: a vision-language model reads the image and must return the
                                value plus boxes of what it relied on; validated, drawn, tier "estimate"
          --> checking layer (raster answers): consistency checks across routes; a conflict makes an estimate
          --> answer JSON {value, unit, route, tier, tier_reason, uncertain, confidence, evidence, explanation,
                           items, checks}
          --> evidence PNG: counted items boxed and numbered (blueprint blue when proven, amber when they need
              a check), measured rooms filled with their area
```

A route either proves an answer or abstains with a reason, and the router then tries the next one. The
raster routes (labels and detector) instead give their best count in one of two tiers: "proven" when the
conditions tuned for a provable answer hold, "estimate" otherwise, with the reason and the boxes or labels
a person should check (drawn amber on the evidence image). They abstain only when there is nothing to go
on. All
routes are deterministic and cost $0 per question in model calls; the eval reports cost per question so
an LLM route can be compared on the same table later.

### Rules that carry the accuracy (all measured on the dev split)

Swiss Dwellings (DXF of the same drawing):

| question | rule |
|---|---|
| doors | 5-vertex door-leaf polylines on layer DOOR (swing arcs over-count double and sliding doors) |
| windows | each window is three parallel strips, or a frame rectangle plus two glazing lines; strips are grouped by orientation and >= 80% overlap along the wall, so adjacent windows that share an edge stay separate |
| apartments | unit labels ("Whg. A", "App. B"); UNIT_OUTLINE polylines over-count |
| toilets vs washbasins | both are 48-vertex ellipses; a washbasin has a drain circle inside, a toilet does not |
| bathtubs, showers | 69-vertex and 5-vertex FIXTURE outlines |
| room area | shoelace area of the ROOM_OUTLINE that contains the room's number label |
| apartment / floor totals | rooms inside the unit outline, outdoor names excluded; rooms printed with a number but no name count only if they contain a sanitary fixture |

IFC: counts are entities of the class (subtypes included; stair flights nested in a stair are not
separate stairs), filtered by storey containment. Room areas come from `NetFloorArea` when the model has
quantity sets, otherwise from the footprint of the space geometry. Wall length, side area and volume,
and slab volume are sums of quantity-set values, converted with the project units.

Raster (CubiCasa scans):

| question | rule |
|---|---|
| storeys | multi-storey plans paste each floor as its own scan onto a white canvas; count the large non-white panels |
| bedrooms, kitchens, saunas, WC rooms, bathrooms, balconies | room labels read by a local OCR model (RapidOCR, ONNX, CPU) in four orientations and on the mirrored image (some scans are mirrored), matched against the Finnish abbreviations (MH, K, KK, S, SAUNA, WC, KH, KPH, PH, PESUH, SH, PARVEKE), a few Swedish ones, and English and German room words (BEDROOM, KITCHEN, BATHROOM, TOILET, BALCONY, SCHLAFZIMMER, KUCHE, BADEZIMMER, BALKON ...; "Bed room" is joined to one word); apartment summaries like "3H+K+S" are skipped |
| OCR errors | an English or German word of 6+ letters also matches with one letter wrong, missing or extra ("bedrocm3", "bathroon"); such a read is marked fuzzy. The short Finnish abbreviations never match fuzzily |
| proven or estimate | proven: kitchen, bathroom, sauna when exactly one label is read (two or more is usually a neighbouring flat in the scan); bedrooms when at least one MH is read; WC rooms always; balconies 0 or 1; a zero needs at least three readable room labels. A count that needs a fuzzy read, and a zero on a plan where any label was read only fuzzily, are estimates (the OCR misreads this plan, so a missing label may be misread beyond recognition). Every other count is an estimate |
| bedrooms, kitchens, saunas, WC rooms, bathrooms, balconies: two readings | `raster-rooms` route: a YOLO room-type detector finds the rooms by what is drawn in them (counters and stove, a toilet room without a shower, sauna benches, beds, the balcony outline), independent of the labels, and its count is set against the OCR label count above. Agreement is proven when the type's tuned rule holds; disagreement is an estimate with both readings, from the reading the type's tuned policy prefers (see "Room-type detector") |
| rooms labelled 'X' | the readings of that label on the scan (spaces and punctuation ignored, a room number allowed); always an estimate, since no Stoa scan question asks it and there is nothing to tune it on |
| toilets, sinks, showers/baths, doors, windows | `raster-detector` route: boxes from a YOLO detector counted above a per-object score threshold, over the eight rotations and mirrors of the image; an estimate when boxes score close to the threshold, the views disagree, or the object is one the detector is wrong on more often than right (see "Detector") |

Every counted label is boxed and numbered on the scan (blue when the count is proven, amber when it is an
estimate); the other room labels that were read are boxed in grey, so a zero answer can be checked too. The OCR is the slow part (about 5-15 s per image for eight
passes); `tools/ocr_cache.py` fills the cache in `out/ocr/` up front.

### Checking layer

Before a raster answer is returned, `bpqa/checks.py` compares it with what the other raster signals
on the same image imply, and makes the answer an estimate when they conflict (for seven checks the
implied value also replaces the count, because on dev it was right more often: a "MH3" label, a summary
listing K or S, floor names, a stair label, bathrooms needing a shower or bath). Examples: a label "MH3" means at least three
bedrooms; an apartment summary "3H+K" means two bedrooms; two floor names on the sheet mean at least
two storeys; every WC room label implies a WC seat, every bathroom and kitchen a sink, every bathroom a
shower or bath, n room labels at least n-1 doors, every bedroom and the living room a window. Each
check that fired is listed in the answer JSON (`checks`) and drawn in the evidence header, green PASS
or red FAIL. On CubiCasa test it raised precision from 93.5% to 95.7% at a cost of one point of
coverage (CHANGELOG.md has the per-check table). `eval.py --no-checks` turns it off.

### PDF without a twin (`pdf-vector`)

If a PDF comes without its DXF or IFC twin (or `--pdf-only` tells the router to ignore twins), the
`pdf-vector` route answers from the PDF's own vector paths and text (`bpqa/pdfvec.py`,
`bpqa/routes/pdf_vector.py`). A page that is mostly one image with little linework is "scanned" and
abstains. The rules are found from the drawing, not hard-coded per style where that was possible:

| question | rule |
|---|---|
| scale | printed ratio ("1:100", "Massstab", "Echelle", "Scala", "Scale") on a page of real paper size; scale bar (tick numbers with "m" on one baseline, fitted linearly); room-area labels ("12.5 m2") inside room shapes; dimension strings on a parallel line. Agreement within 1.5% raises confidence; disagreement over 2% means areas abstain |
| rooms | closed light fills grouped by stroke style; the group whose shapes hold the most labels is the room style. Labels attach to the smallest shape containing them (as in the DXF route). Area is the drawn outline x (metres per point)^2 |
| apartments | unit labels; unit outlines are the coloured closed polylines |
| doors | among thin unfilled four-corner shapes (the opening across the wall), the stroke style that touches door-swing arcs; then every unfilled four-corner shape in that style |
| windows | a light-filled frame outside every room shape (windows sit in the walls) with two glazing lines along it |
| toilets, washbasins | ellipse outlines (many segments, filling pi/4 of their rotated box); a small drain circle inside makes it a washbasin; only in the line style that basins and tubs use |
| bathtubs, showers | many-segment rounded rectangles; four-corner trays with a drain circle |
| when to answer | object counts only when the door convention is recognised (openings with swing arcs); without it a 0 would not be proof |

Every answer draws its evidence on the rendered PDF page from the same shapes that produced it, and
boxes the text that set the scale in blue. `tools/pdf_vs_dxf.py` compares what the route reads with the
DXF twin, per object, on dev floors.

Results with `eval.py --pdf-only` (PDF questions only, twins ignored; full dataset, same split):

| source | type | dev n | dev accuracy | dev coverage | dev precision | test n | test accuracy | test coverage | test precision |
|---|---|---|---|---|---|---|---|---|---|
| swiss_dwellings | count | 17812 | 99.7% | 99.9% | 99.8% | 7756 | 99.8% | 99.9% | 99.9% |
| swiss_dwellings | area | 8364 | 96.4% | 99.9% | 96.5% | 3652 | 96.8% | 100.0% | 96.8% |
| ifc_models (plan PDFs) | area | 1208 | 54.9% | 57.5% | 95.5% | 718 | 87.5% | 89.6% | 97.7% |
| ifc_models (plan PDFs) | count, length, volume | 652 | 0% | 0% | - | 116 | 0% | 0% | - |
| opening_design | all | 44 | 45.5% | 45.5% | 100% | 20 | 0% | 0% | - |
| **all PDFs** | **all** | 28080 | 94.4% | 95.7% | 98.7% | 12262 | 97.1% | 98.2% | 98.8% |

For comparison, the DXF route on the same dev Swiss questions: count 100.0%, area 96.7%, all 98.9%
(pdf-only: 98.6%). Most remaining Swiss area misses are shared with the DXF route (gold totals that
neither route reproduces). IFC plan PDFs: room areas only (the drawn footprint vs. the model's
`NetFloorArea`; corridors differ most); counts abstain because IfcConvert draws door swings as
flattened polylines, so the door convention is not recognised. opening_design answers come from
`pdf-text` (schedules); `pdf-vector` abstains on all of them.

### Quantity takeoff: room-type areas (`bpqa/takeoff.py`)

Stoa's own example: "How many square meters of tiling should I order for the toilets?" The dataset only
asks per-room and per-label areas, so takeoff questions are a layer on top of the existing area rules,
not a change to them.

- **Parsing.** A question with an area unit (m2, square metres, nelio), a material (tiles, laatta,
  flooring, parquet, paint, waterproofing, screed) or an area word, plus a room type, becomes
  `{measure: area, obj: room, variant: type_sum, takeoff: {types, material, surface, aggregation: sum}}`.
  Room types are read in EN and FI (stems, so "kylpyhuoneisiin", "keittioon", "vessoihin" match), DE, FR,
  IT, and from the Finnish plan abbreviations KH, KPH, PH, PESUH, SH, WC, S, MH, OH, K, KK, ET, TK, VH,
  VAR, KHH, RT (case-sensitive, "KH:hon" allowed). "Wet rooms" / "markatilat" means bathroom + WC +
  sauna. It never fires on a question with a quoted label, a room number, an apartment, "named ...",
  or an exclusion clause's room words ("except balconies"); on Stoa's 127,044 questions it changes the
  parse of 2 (ifc_bench takeoff questions), checked by `tests/test_takeoff.py` and a full-file run.
- **Interpretation, stated in every answer:** the sum of net floor areas of the matching rooms as drawn
  inside the walls. Tiles in wet rooms (bathroom, WC, sauna) and any question naming walls add the wall
  surfaces (next section); "floor tiles" stays floor only. "Toilets" means separate WC rooms; bathrooms
  are not included, and the answer says so. Paint answers the floor area and says walls are not included
  unless walls are asked.
- **Room types from drawing labels** (`room_types`): Swiss (Bad, Kuche, Bain, Bagno, Schlafzimmer ...),
  IFC (Bathroom 1, Toilet, TUALETTRUUM, doucheruimte, Bad - Dame, 1IZQ-BAN ...) and Finnish
  abbreviations. Generic "Zimmer" / "Piece" / "Room" are not typed; they are listed in the answer as
  not included. A label naming two types ("Wohnen/Essen", "PH/KHH") counts for both.
- **Answer:** value = total m2; `items` = one line per room (label, number, type, area, where the number
  came from); `explanation` = "16.76 m2 = Bad (7.05) 6.01 + ... " plus the interpretation; evidence PNG =
  the rooms filled and numbered with their areas, the total and "floor area only" in the header.
- **Routes:** DXF room outlines (Swiss), PDF room shapes x scale (`--pdf-only` or no twin), IfcSpace
  areas (NetFloorArea, else footprint). On an IFC plan sheet without a level in the question, the
  storey is the one whose name and (uniqueness-weighted) space labels are printed on the sheet; no
  clear winner abstains. Zero matching rooms abstains with the labels that were read.

### Wall surfaces: tiles on walls (`bpqa/walls.py`)

Tiles in a bathroom go on the walls too. Wall area of a room = outline length x tiled height - the doors
and windows in that room's walls, with every input stated in the answer and its source named:

| input | IFC model | Swiss DXF floor |
|---|---|---|
| outline | IfcSpace geometry projected to the floor (holes add their ring length) | ROOM_OUTLINE polygon |
| height | the space's FinishCeilingHeight / ClearHeight / Height quantity, else its geometry's z extent, else the storey height | ASSUMED 2.50 m (a plan has no heights) |
| tiled height | full height unless the question says "tiles to 1.2 m" | same |
| openings | IfcDoor / IfcWindow whose footprint lies within 0.2 m of the outline along 30% of its width; width x the part of the opening's own z range inside the tiled band | door leaf width, ASSUMED 2.00 m high; window strips, ASSUMED 1.20 m high on a 0.90 m sill |
| waste | 10% cutting allowance unless the question gives one ("with 15% waste", "no waste") | same |

Editable from the question: "ceiling height 2.6 m", "tiles to 1.2 m", "tiling height 210 cm", "12% waste",
"no waste", "full height" (`TAKEOFF_CEILING_M`, `TAKEOFF_WASTE_PCT` change the defaults). The answer lists
floor, walls, waste and order quantity as separate lines; the value is the order quantity when the question
asks what to order / buy / need, else the net area. `items` has one line per room with perimeter, height
and its source, gross wall area, each deducted opening (GlobalId, width, deducted m2) and net wall area;
when the IFC model carries its own NetWallArea it is shown beside ours as a check (more than 5% apart makes
the answer an estimate). Answers resting on an assumed height are estimates. Scans with a pixel scale from
printed room areas answer walls as an estimate (below). Other routes (PDF without a twin) still answer
floor areas only, so the router withdraws their answer to a wall question.

Evidence: the plan with the rooms filled, their wall outline drawn thick, each deducted opening boxed in
amber with its deducted area, and floor and net wall area per room.

Validation (`tools/wall_eval.py`): the quantities the authoring tools wrote into the IFC models, which the
takeoff never reads (it shows NetWallArea only as a check), against what it computes from geometry, every
IfcSpace that carries them, duplicate copies of a model counted once:

| quantity | reference | spaces | within 2% | within 5% | median error |
|---|---|---|---|---|---|
| perimeter | Qto GrossPerimeter, Revit Perimeter, ArchiCAD Umfang (16 models) | 1,470 | 67.3% | 88.7% | 0.0% |
| gross wall area (perimeter x height) | Qto GrossWallArea (5 models) | 454 | 66.5% | 72.0% | 0.0% |
| net wall area (minus openings) | Qto NetWallArea (6 models) | 511 | 51.5% | 56.9% | 1.8% |

Per model, net wall area within 2%: KIT institute (ac20) 96%, Smiley West 76%, FZK house 43% (gross 43%: rooms
under the pitched roof and a gallery open to the floor below, where perimeter x height is not the wall), Molio
31% (gross 68%; windows in thick external walls are missed by the 0.2 m rule), TU Wien escape-route model 29%
(its GrossWallArea implies other room heights than its own height quantities), RWTH Digital Hub 11% (net only).
One model (Gymzaal Amersfoort) stores perimeters about 300x the geometry and is left out as a unit error.
Variants measured and rejected: counting only outline edges that run along a wall footprint (gross 46%, net
35% within 2%: space outlines often sit more than 0.1 m off the walls) and wider opening tolerances (0.35 to
0.7 m: worse on four of five models). There is no reference for the Swiss DXF wall areas: the heights there
are assumptions, which is why those answers are estimates.

Raster scans (`bpqa/routes/raster_area.py`, route `raster-area`):

- printed areas ("12,1 m2", "9.7m2", comma or point decimals) are read from the upright OCR pass and
  attached to the room label just above or beside them; level marks (+22,65) and totals
  (HUONEISTOALA, KERROSALA) are skipped
- room regions: text erased, thin linework removed (furniture, swings, stairs), doorway gaps bridged
  along the wall direction, flood fill from the label, floor under counters and wardrobes along a wall
  given back; a region that reaches the image edge, holds another label or is tiny is unusable
- scale (m2 per pixel) only when 2+ rooms with a printed area and a usable region agree within 4% and
  form the majority; the doorway width (1.0 m) is tried at 10 pixel-per-metre hypotheses and a
  hypothesis counts only if the scale it produces agrees with it within 20%
- a takeoff answers a room with a printed area with that printed value (tier proven when every asked room
  is printed). A room without one is answered with its measured area (region x scale) as tier
  **estimate**, only when the scale is established; the answer names the printed rooms, the measured rooms
  (listed as `uncertain`), the printed rooms that set the scale, and the measured error band. A measured
  region under 3 m2 is not answered (below), and without a scale the sum abstains with the reason.
  `MEASURED_ANSWERS` in `raster_area.py` switches measured answers off. It also abstains when the full
  eight-pass OCR (when cached) reads more labels of the asked type than were located. One-letter labels
  (S, K) must be upper case.
- floor and walls are separate lines. A floor-only question stays floor only. A wall question (or tiles in
  wet rooms, paint on walls and floor) adds walls = perimeter of the room region (convex hull, x scale) x an
  ASSUMED ceiling height (2.5 m, editable from the question like the vector routes, `bpqa/walls.py`);
  openings are not deducted (a scan has no reliable door and window data here), so the wall figure is gross.
  Always an estimate; no scale, or a room without a closed region, abstains.
- measured error band (leave-one-out against printed areas, dev + test pooled, 266 rooms of 74 plans),
  quoted in every answer by the measured room's size: measured 3 to 6 m2: median error 13%, 8 in 10 within
  38%, 28% within 5%; 6 m2 and over: median 6.5%, 8 in 10 within 20%, 45% within 5%. Measured under 3 m2:
  median error 46%, nearly always too small, so those rooms are not answered. Wall perimeters: median 8.7% off
  the annotated room outline, 8 in 10 within 27% (419 rooms), before the height assumption.
- evidence: printed rooms in blue with the printed value boxed; measured rooms in amber with "measured X m2";
  the scale rooms are named in the header.

Takeoff results (every number against a stated reference; dev was used for development, test run once):

| check | reference | dev | test |
|---|---|---|---|
| Swiss bedroom sum (DXF route) | Stoa's "total net area of rooms labelled 'Schlafzimmer' / 'Chambre a coucher' / 'Camera da letto'" | 150/150 within 2% | 91/91 |
| Swiss bedroom sum (`--pdf-only`) | same | 60/60 | - |
| Swiss bathroom, kitchen rooms in the sums | Stoa's per-room areas, every asked room | 158/158 | 155/156 |
| Swiss bathroom, kitchen sums, where Stoa asks about every such room on the floor | sum of Stoa's per-room areas | 18/18 | 19/20 |
| IFC bathroom, WC, kitchen, bedroom sums (models and plan sheets) | sum of Stoa's per-room areas, same completeness rule | 56/56 (60 type sets: 2 abstain, 2 answered with a room list that differs from the asked rooms) | 18/18 (19 sets: 1 differs) |
| IFC rooms in the sums | Stoa's per-room areas | 204/204 | 184/184 |
| CubiCasa measured room area, leave-one-out  | the area printed on the plan, held out of the scale | 18.7% within 2%, 36.8% within 5%, median error 9.3% (193 of 1471 printed rooms measurable, 52 of 197 plans) | 6.8% within 2%, 23.3% within 5%, median error 11.9% (73 of 756 printed rooms measurable, 22 of 112 plans; all 1,510 test images; an earlier run on 635 test images gave 4.8% / 21.4% / 12.3%) |

Baselines: before this branch every takeoff question was a parser abstention (0% coverage). The
existing area answers did not change: `eval.py` on the preview dev split gives the same Swiss, IFC
and opening_design rows as the Results table; ifc_bench answers one more question (the duplex
bathroom tiling question, 15.81 m2 from IfcSpace footprints, where the ifc_bench answer is 18.85 m2
from finish data while Stoa's own ifc_models answers give Bathroom 1 (A104) 3.16 m2 as we do).
CubiCasa takeoffs are answered only from printed areas, so their accuracy is the OCR read of those
labels, which has no reference in Stoa's data.

### Property and free-text questions (`ifc-props`, generic schedules)

"What is the fire rating of door D-12?", "Which rooms are on level 2?", "What material are the walls in room
104?", "Which door types are installed, with quantities?". These do not have one number as the answer, so
`bpqa/routes/ifc_props.py` maps the question to one typed lookup over the model and builds the answer only
from what the lookup returns; every answer cites the entities it read (GlobalIds in `items`).

| lookup | what it reads |
|---|---|
| type breakdown | entities of the class, grouped by IfcRelDefinesByType type name (else ObjectType), counts and shares; per storey, thickness or width x height when asked |
| materials | IfcRelAssociatesMaterial: layer sets with thicknesses, lists, single materials, per type |
| fire rating | any property named like fire rating (FireRating, Feuerwiderstand, brannklasse ...) on the element or its type; when none exists the answer says so and how many elements were searched |
| named element | an element whose Name or Tag is printed in the question ("door D-12", "Fenster-004"); several elements with the same name are answered together |
| room | a space named by number or name ("room 104", "staff room"); elements in it by IfcRelContainedInSpatialStructure / IfcRelSpaceBoundary, else footprint within 0.3 m (stated) |
| storey | named in the question, or "ground floor", "basement", "attic", "level 2" resolved against the storey names and elevations, with the reading stated; unresolvable abstains |
| extremes, statistics | widest door, largest room, tallest ceilings, average / min / max of width, height, area |
| totals | volume, length or area quantities summed per type |
| spaces | spaces on a storey or in the model, by storey and by function (LongName), filtered by room type from their names (bathrooms, balconies) |
| storeys, project, systems, structure | storey elevations and heights; IfcProject / IfcSite / IfcBuilding names, coordinates, addresses; IfcSystem groups with element counts; beams, columns, slabs, load-bearing walls, footings by class |
| property search | any other content word of the question ("glazing", "warranty") matched against property and material names; no match means "not available in this model" with the number of elements searched |

Interior / exterior comes from `Pset_*Common.IsExternal`; when no element carries it, from the CAD layer name
('Aussenwaende', 'A24_Innervegger'), stated in the answer. Element classes are also taken from the model's own
class names (IfcSpaceHeater answers "space heater", IfcDamper "damper"), so MEP classes need no word list.
Placeholder values (Revit writes the property's own name, "Fire Rating", into empty properties) are ignored.

Tiers: narrow lookups (one element, one room, an extreme, the storey list, fire rating, model metadata) are
`proven`; broad keyword-mapped ones (type breakdowns, property search, systems) are `estimate` with the reason
"every value is read from the file, but the lookup was chosen by keyword rules". Evidence: the answer table
beside a plan of the storey holding most cited elements, walls and slabs filled, doors and windows boxed.

Nemotron (only when no rule lookup matches and the fallback is on) is asked to choose one lookup from a
closed schema built from the model's class and storey names; it never supplies a value, and its answers are
estimates. On the first 60 full-set dev questions the rules leave unmapped it chose a runnable lookup for 24,
9 of them right (all the MEP type questions); the 15 wrong ones are mostly questions whose true answer is
"not available" (`tools/nemo_props_eval.py`, about $0.05 at the assumed list price).

PDF schedules (`bpqa/schedules.py`, used by `pdf-text` after the door-schedule parser): a header line holds 3+
schedule header words; column edges come from the table's own drawn vertical rules when there are any,
otherwise from the gaps between header labels; stacked header words join their column. Questions are matched
to rows by codes printed in the table (type marks, room and door numbers, finish codes), "starts with 'X'",
or runs of question words found in the row text; counts sum a Count / Qty column when the table has one;
lookups return the column the question names, with the code's meaning from the legend printed on the sheet
("F4 = RESILIENT FLOORING (T.B.D.)"). The matched rows are boxed on the sheet.

How text answers are scored (`eval.py`, the dataset's lenient rule): when the gold answer contains numbers,
every one of them must appear in our answer within 2%; otherwise 60% of the gold words must appear. Extra
numbers or words in our answer are not penalised, so a longer answer can only gain; "B3" scores right against
"B3 (aluminium sliding door)". As a check on that, 20 randomly drawn dev answers scored right were read by
hand: 19 answer the question, 1 is partly right (it reports 69 flow terminals but no plumbing system).

Results (full set; dev used for development, test run once at the end):

| set | before | after | after: proven n / precision | after: estimate n / precision |
|---|---|---|---|---|
| IFC-Bench dev (439) | 0.2% (1) | 33.3% (146) | 57 / 64.9% | 285 / 38.2% |
| IFC-Bench test (381) | 1.3% (5) | 27.8% (106) | 69 / 39.1% | 242 / 32.6% |
| OpeningDesign dev (44, Sports Complex) | 45.5% (20) | 90.9% (40, none wrong) | 40 / 100% | - |
| OpeningDesign test (20, Cannery Trail) | 0% | 70.0% (14; 5 wrong) | 19 / 73.7% | - |

On IFC-Bench test the narrow "proven" lookups held up worse than on dev (39% vs 65% precise): project
information is answered with the model's placeholder names ("Project Name", "Projektnummer") and decimal
coordinates where the gold answer judges the data as imprecise or prints degrees and minutes, and "largest
room" answers from footprints where the gold says the area properties are empty. Those answers are wrong by
the scorer though their values are in the file; the tier rule was not changed after the test run.

The OpeningDesign test number is not a clean held-out result: the generic schedule reader was built while
looking at the layout of the Cannery Trail sheet (A700) as well as the dev sheet, and its questions had been
printed during development. For IFC-Bench, no test model was opened, but the list of distinct question
texts of all splits (without answers) was read to choose the lookups, and three test answers appeared in an
early sample.

### Detector

Toilets, sinks, showers, bathtubs, doors and windows on the scans are counted by a small YOLO detector
(Ultralytics), with every counted box drawn and numbered. The route uses the tiled `yolo26s` described
below (started from Ultralytics' pretrained weights), which beat the earlier `yolo26n` from scratch
(`cubi2`) on every object on the tune images. Training data:

- Public CubiCasa5K annotations (5,000 plans with SVG annotations), converted by
  `tools/cubicasa_svg.py`. Stoa's CubiCasa images are a subset of CubiCasa5K, and Stoa's counts are
  the annotation counts (the converter reproduces Stoa's train-split answers on 99.6-100% of images).
  So the plans used are restricted by `tools/overlap.py`, which finds every public plan that appears in
  Stoa's data by perceptual hash: plans matching a Stoa **test-split** image (1,544) are never used;
  plans matching only train-split images (3,245) and plans not in Stoa (211) may be. Of those, 617
  plans, including every preview dev image, are held out of training to tune the count thresholds.
- Synthetic plans rendered from train-split Swiss DXF floors (`tools/render_dxf_synth.py`): black on
  white, random scale and rotation, boxes from the same rules the DXF route uses.

The count threshold per object, and an uncertainty band around it, are tuned on the held-out
train-split images (`tools/tune_detector.py`). Test answers are never read while tuning. The tuner
also tries test-time augmentation (the eight rotations and mirrors, boxes fused across views or the
median per-view count) and the tiled detector below; `tools/pick_detector.py` keeps, per object, the
model and mode with the higher cross-validated exact-count accuracy on those images.

Tiled detector (`tools/build_tile_dataset.py`, model `detector_tiles.pt`): the same plans, re-read at
native resolution from the public `F1_original.png`, resized 2x (long side capped at 4,096 px) and cut
into 1,024 px tiles with 192 px overlap (17,596 training tiles), and a `yolo26s` started from
Ultralytics' pretrained weights. At inference the image gets the same resize and tiling; boxes found
twice where tiles overlap are merged (same class, IoU >= 0.5 or the smaller box 60% inside the larger).

### Room-type detector (`raster-rooms`)

Kitchens, bathrooms, WC rooms, saunas, bedrooms and balconies on the scans used to rest on the OCR of the
room labels alone, which fails where a label is missing, mirrored or unreadable (an open kitchen inside
"OH", an unlabelled sauna). `bpqa/routes/raster_rooms.py` adds a second, label-independent reading.

- **Training data** (`tools/cubicasa_rooms.py`, `tools/build_rooms_dataset.py`): the room polygons in the
  public CubiCasa5K `model.svg` (`<g class="Space ...">`, with the room's NameLabel and the fixtures inside
  it), boxed per room type. Mapping, checked against Stoa's train-split answers (agreement on train-split
  images: bedroom 2019/2020, kitchen 2232/2234, bathroom 1659/1659, WC 1622/1623, sauna 2234/2254,
  balcony 989/990): `Space Bath*` named "WC" is a WC room, every other `Space Bath*` (KH, KPH, PH,
  PESUH ...) a bathroom; `Space Bedroom*`; `Space Kitchen*` (kitchenette, open, scullery); `Space Outdoor
  Balcony*` (incl. glazed); `Space Sauna` or any other space holding a sauna stove (most saunas are
  `Space Undefined` with a stove). Same plan rules and the same held-out plans as the fixture detector
  (only `train_clean.txt` plans, test-split matches refused, the 624 tune images never trained on):
  2,678 training and 161 validation images.
- **Model**: `yolo26s` from Ultralytics' pretrained weights, 896 px, trained on the PC GPU
  (`tools/train_detector.py --data .../ds_rooms/data_abs.yaml --weights yolo26s.pt --imgsz 896 --batch 4
  --time 1.0 --copy-as rooms_s896.pt`, then copied to `models/rooms.pt`). `tools/train_rooms_modal.py` is
  the same training for a Modal CPU container (Modal GPUs need a payment method on the account); it was
  used for an earlier `yolo26n` at 640 px. Inference runs on the CPU in the route.
- **Combination** (tuned per type on the tune images only, `tools/tune_rooms.py` ->
  `models/rooms_thresholds.json`): rooms counted at or above a score threshold; when the detector count
  equals the OCR label count the answer is that count, proven when the type's proof rule holds ("agree",
  "agree_ocr" = the OCR reading is one its own rules prove, "agree_clean" = no room scored near the
  threshold; the rule with the most proven answers at >= 95% precision on the tune images, else none);
  when they differ it is an estimate with both readings, valued by the type's preferred reading
  ("det", "ocr", or "ocr_proven" = OCR when its own rules prove it, else the detector). The checking
  layer then runs as before.
- **Evidence**: each counted room boxed and numbered (blue proven, amber estimate), rooms scored near the
  threshold in grey, the labels read boxed thin and numbered L1, L2 ...

Results, exact count, abstentions counted wrong (accuracy; proven answers and their precision in
brackets). Before = main (OCR labels + checking layer), after = raster-rooms + the same checking layer.
Full set test split: 10,032 questions on 1,510 images, run once after the thresholds were frozen. Preview
dev: 1,048 questions; its images are among the tune images, so dev is not held out from the tuning.

| room type | full test before | full test after | preview dev before | preview dev after |
|---|---|---|---|---|
| kitchen | 64.0% (1170, 98.8%) | 90.8% (1008, 98.8%) | 61.4% | 85.1% |
| bathroom | 61.5% (816, 96.8%) | 85.4% (788, 93.7%) | 82.4% | 93.2% |
| WC room | 81.2% (1278, 82.0%) | 89.4% (1160, 96.4%) | 89.3% | 93.3% |
| sauna | 63.6% (518, 81.9%) | 93.4% (1218, 96.2%) | 69.4% | 89.2% |
| bedroom | 75.1% (1276, 86.4%) | 81.5% (none proven) | 76.7% | 78.6% |
| balcony | 85.8% (772, 87.6%) | 85.8% (660, 93.6%) | 87.2% | 87.2% |
| all six | 70.2% | 88.1% | 75.4% | 87.2% |

Bedrooms get no proven tier: no proof rule reached 95% precision on the tune images. Bathroom proven
precision on test (93.7%) is under the 95% it was tuned to. The detector adds about 0.1 s per image on
the CPU (plus about 4 s to load the model once).

### Nemotron

NVIDIA Nemotron (`nvidia/nemotron-3.5-super-vl-120b-a12b`, a vision-language reasoning model, called
through NVIDIA Build's OpenAI-compatible API) is used in three places. It is never the default, and
nothing it says reaches an answer without a check.

1. **Last-resort answer route** (`bpqa/routes/nemotron.py`, route name `nemotron-estimate`). It runs only
   after every other route has abstained for that question: no DXF or IFC twin, no schedule, no vector
   answer, the detector or OCR route abstained or a consistency check withdrew its answer. The drawing
   (a PDF page rendered at 150 dpi, or the scan) is downscaled to 1600 px on the long edge and sent with
   the question. The model must reply with one JSON object: the value, the regions of the drawing it
   relied on as pixel bounding boxes, and one sentence on how it got the value.
2. **Question-parse fallback** (`NemotronParser`, same file). When the rule parser (EN DE FR IT FI DA ET)
   cannot map a question to the typed query, Nemotron is asked for the same typed query as JSON. The
   schema is closed: measure, object, scope and variant come from fixed lists, and a room label, room
   number, apartment letter or level name is accepted only if it appears in the question text. Reasoning
   is switched off for this call (a short classification). Replies are cached by question text.
3. **Naive baseline** (`tools/nemo_baseline.py`), a measurement and not part of the product: the same
   model, a plain prompt that asks for just the number, no regions and no checks, on the same questions
   our router answers. It exists to show what the structured routes add (see "Nemotron results" below).

How the outputs are checked (`validate()` in `bpqa/routes/nemotron.py`):

| check | rule |
|---|---|
| JSON | a JSON object must be found in the reply (code fences tolerated); otherwise abstain |
| value | a finite, non-negative number; an integer for counts; areas between 0.5 and 5000 m2 |
| regions | every region has a bbox of four finite numbers, positive size, inside the image (2% tolerance, then clipped) |
| counts | one region per counted object, so `len(regions) == value`; more than 40 abstains (too many to check one by one); a counted object may not cover half the drawing |
| areas, lengths, volumes | at least one region naming where the value comes from |
| scope | whole-model questions ("in the model 'X'"), compound questions and unresolved scopes are never sent: one image cannot prove them |
| failure | any failed check abstains with the reason (`invalid model output: ...`); the router then reports no answer, as before |

A valid answer is marked `tier: "estimate"` in the answer JSON, with confidence 0.3 and the route name
`nemotron-estimate`. Its evidence PNG draws the validated regions as numbered boxes on the drawing, under a
header that says ESTIMATE and names the model, so a person can see at once whether the boxes sit on
the objects. The evidence is for checking by eye; the route does not verify the boxes itself.

Switches and cost: `NEMO_FALLBACK=0` turns both fallbacks off (default on, but they are reached only in
the fallback cases above); `eval.py --no-nemo` does the same for a run. The API key is read from
`NVIDIA_API_KEY` in the environment or in `.env` (git-ignored) and is never printed, logged or cached.
Every response is cached on disk keyed by model, prompt and image hash, requests are throttled to about
37 per minute with retry and backoff, and each answer records its token use and an estimated cost at an
assumed list price (`NEMO_PRICE_IN`, `NEMO_PRICE_OUT`, default 3 and 15 USD per million tokens; an
assumption, not a quote). On the NVIDIA Build free tier the real cost is 0.

### Nemotron results

Measured with the model as described above; details, per-object numbers and the exact setup are in
`CHANGELOG.md` (section "Nemotron baseline and fallback route"). Preview TEST split, 150 questions, seeded
sample (CubiCasa 50, Swiss counts 40, Swiss areas 30, IFC room areas 30):

| | Nemotron, plain prompt (number only) | our router |
|---|---|---|
| accuracy, all 150 | 39.3% | 94.0% (82.0% when the baseline was recorded; 100% on the 100 PDF questions with PDF only) |
| CubiCasa counts | 66.0% | 82.0% (answers all 50 with a tier; 46.0%, 27 answered, when the baseline was recorded) |
| Swiss counts / areas | 50.0% / 16.7% | 100% / 100% |
| IFC room areas (plan PDF) | 3.3% | 100% |
| median latency | 25 s (reasoning model) | seconds, local |
| cost per question | about $0.10 at an assumed 3 / 15 USD per million input / output tokens, about $0.005 if the model answered without reasoning; free on NVIDIA Build | $0 |

Used as the last-resort route on the preview dev questions that otherwise get no answer (1,327 eligible, a
seeded sample of 200): 85% get a valid estimate and about half of all of them are correct, which raises dev
accuracy from 64.8% to about 77% and lowers the precision of answered questions from about 92% to about 83%.
Room-label questions on CubiCasa (bathrooms, kitchens, saunas, bedrooms) are mostly right; doors, windows,
balconies and WC rooms are mostly wrong. Those answers are `tier: "estimate"` and the evidence image shows the
boxes the model gave.

## Run it

Windows, Python 3.11:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
set STOA_DATA=C:\path\to\junction-dataset-preview
.venv\Scripts\python eval.py --split dev --evidence
```

`eval.py` options: `--split dev|test|all`, `--sources swiss_dwellings,ifc_models`, `--limit N`,
`--evidence` (render overlays into `out/evidence/`), `--prior` (also report the dev-majority baseline per
object; it is never used as an answer), `--no-tiers` (withdraw estimates, the behaviour before tiers),
`--no-checks`, `--pdf-only` (PDF questions only, DXF/IFC twins ignored; `ask.py` takes it too). Results go
to `out/<split>/answers.jsonl`, `summary.txt` and `summary.json`; the summary has accuracy, coverage,
precision, and the number and precision of answers in each tier.

One question:

```
.venv\Scripts\python ask.py data/swiss_dwellings/site1000_bldg2082_floor9385.pdf "How many windows are drawn on this floor plan?"
```

Takeoff questions go through the same command (evidence PNG path in the JSON):

```
.venv\Scripts\python ask.py data/swiss_dwellings/site3387_bldg5238_floor23498.pdf "Kuinka monta neliötä laattaa kylpyhuoneisiin?" --pdf-only
.venv\Scripts\python ask.py data/ifc_models/bsc-molio_molio-with-uris_plan-04-1-sal.pdf "How many square meters of tiling should I order for the toilets?"
.venv\Scripts\python ask.py data/cubicasa5k/high_quality_architectural-10372.png "How many m2 of flooring for the bedrooms?"
```

Takeoff checks: `python tests/test_takeoff.py`; `tools/takeoff_eval.py --split dev` (vector sums vs.
Stoa's area answers); `tools/ocr_r0.py --split dev --limit 2000` then `tools/area_eval.py --split dev
--mode walls` (raster leave-one-out).

Wall takeoff and property questions:

```
.venv\Scripts\python ask.py data/ifc_models/kit-fzk-haus_ac20-fzk-haus.ifc "How many square meters of tiling should I order for the bathrooms?"
.venv\Scripts\python ask.py data/ifc_bench/ac20_arc.ifc "What material are the walls in room 104 made of?"
.venv\Scripts\python tests/test_walls.py
.venv\Scripts\python tools/wall_eval.py --data ../data/stoa-full/junction-dataset      (about 7 minutes)
.venv\Scripts\python tools/nemo_props_eval.py --split dev --limit 60                  (calls Nemotron)
```

The data is never copied into the repo; point at it with `--data` or `STOA_DATA` (default:
`../data/stoa-preview/junction-dataset-preview`).

Detector, from the downloads to a tuned route (paths relative to the repo; the model and thresholds
land in `models/`, which is not committed):

```
.venv\Scripts\pip install ultralytics
.venv\Scripts\pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\python tools/overlap.py --stoa ../data/stoa-full/junction-dataset --public ../data/cubicasa5k-public/cubicasa5k --out out/overlap
.venv\Scripts\python tools/render_dxf_synth.py --data ../data/stoa-full/junction-dataset --out ../data/detector/synth --per 2
.venv\Scripts\python tools/build_detector_dataset.py --public ../data/cubicasa5k-public/cubicasa5k --overlap out/overlap --stoa ../data/stoa-full/junction-dataset --synth ../data/detector/synth --out ../data/detector/ds_cubi
.venv\Scripts\python tools/train_detector.py --data ../data/detector/ds_cubi/data.yaml --name cubi1 --epochs 40 --imgsz 1280
.venv\Scripts\python tools/tune_detector.py --stoa ../data/stoa-full/junction-dataset --images ../data/detector/ds_cubi/tune_images.txt --imgsz 1280
```

## Demo app

Blueprint Evidence is a small web app over the same router: pick a drawing from the dataset or upload
one, ask a question, and get the answer with its status, the route, numbered reasons, checks, cost and
response time next to a zoomable evidence overlay. A second view shows the eval results.

```
.venv\Scripts\pip install -r app\requirements.txt
.venv\Scripts\python -m app
```

Then open http://127.0.0.1:8000. Options: `--port`, `--host`, `--data <dataset root>` (default:
`STOA_DATA`, then `../data/stoa-full/junction-dataset`, then the preview), `--results <folder>` (extra
eval outputs), `--gpu` (by default the detector runs on the CPU).

- Status tiers: the answer's own `tier` and `tier_reason` come first. Generic DXF (`dxf-generic`) and PDF
  (`pdf-generic`) answers below confidence 0.8 are estimates, and `nemotron-estimate` answers are always
  estimates. Without a tier field: "proven" for structured routes (DXF, IFC, PDF text or linework) at
  confidence 0.8 or more, "estimate, check highlighted items" otherwise; "cannot prove" with the router's
  reason when every route abstains.
- Results view: one line per named test set (Stoa full set test split, Stoa preview set test split, the
  150-question Nemotron baseline sample, the unseen public drawings), each with the file it was read from;
  the eval-run selector names the dataset, split and options of every run; the naive AI baseline card is
  filled from `out/baseline_nemotron.json`, with our side re-scored from the newest preview test run.
- Counted items on scans are listed with the numbers drawn on the overlay; selecting one marks it in
  the viewer. If the question is a dataset question, the dataset answer is shown beside the result.
- Uploads (PDF, DXF, IFC, PNG, JPG; a PDF with its DXF twin can go in one upload) are stored in the OS
  temp folder (`%TEMP%\blueprint-evidence\uploads`), never in the repo. Answers and evidence are
  cached in `out/app/`.
- Without `ultralytics` and `torch` the symbol detector is unavailable and those counts abstain
  (cached detections in `out/det/` still answer); the rail's status button says what is missing.
- API: `POST /api/ask {path, question, pdf_only}`, `POST /api/upload`, `GET /api/drawings`,
  `GET /api/drawing`, `GET /api/preview`, `GET /api/evidence/<id>.png`, `GET /api/results[/<run>]`,
  `GET /api/overview`, `GET /api/baseline`, `POST /api/witnesses {path, question}`,
  `POST /api/corrections`, `GET /api/corrections[.csv]`, `GET /api/costs`, `GET /api/witness_summary`,
  `GET /api/status`, `GET /api/ifc3d[?path=]`, `GET /api/ifc3d/geom`, `GET /api/ifc3d/element`; docs at `/api/docs`. The design system is in `DESIGN.md`.

Tests: `python tests/test_cubicasa_svg.py` (converter, hand-made SVG) and
Test-time augmentation and the tiled pretrained detector (each policy is tuned on the same 624 held-out
train-split images; `pick_detector.py` keeps the better one per object by cross-validated exact-count
accuracy and writes `models/detector_thresholds.json`, which names the model and mode per object):

```
set T=--stoa ../data/stoa-full/junction-dataset --images ../data/detector/ds_cubi/tune_images.txt
.venv\Scripts\python tools/tune_detector.py %T% --out models/th_base.json
.venv\Scripts\python tools/tune_detector.py %T% --tta --out models/th_tta.json
.venv\Scripts\python tools/build_tile_dataset.py --ds ../data/detector/ds_cubi --public ../data/cubicasa5k-public/cubicasa5k --overlap out/overlap --out ../data/detector/ds_tiles
.venv\Scripts\python -c "from ultralytics.utils.downloads import attempt_download_asset as d; d('yolo26s.pt')"
.venv\Scripts\python tools/train_detector.py --data ../data/detector/ds_tiles/data.yaml --name tiles_s --weights yolo26s.pt --imgsz 1024 --batch 8 --time 4.5 --copy-as detector_tiles.pt
.venv\Scripts\python tools/tune_detector.py %T% --model models/detector_tiles.pt --tile 1024 --tile-scale 2 --out models/th_tiles.json
.venv\Scripts\python tools/tune_detector.py %T% --model models/detector_tiles.pt --tile 1024 --tile-scale 2 --tta --out models/th_tiles_tta.json
.venv\Scripts\python tools/pick_detector.py models/th_base.json models/th_tta.json models/th_tiles.json models/th_tiles_tta.json --out models/detector_thresholds.json
```

Tests: `python tests/test_cubicasa_svg.py` (converter, hand-made SVG), `python tests/test_tiling.py`
(tiles, seam merging, rotated and mirrored views mapped back) and
`python tools/test_overlap.py --work <scratch folder>` (overlap tool on the preview),
`python tests/test_evidence_marks.py` (marks sidecar in evidence pixels) and `python tests/test_witnesses.py`
(agreement levels, disagreement circles).

### Verify in seconds

Every counted answer gets a verification strip under the drawing: one cropped thumbnail per counted
item, numbered like the overlay, blue when the route proved it and amber when it needs a check. Select a
thumbnail and the viewer zooms to that item with the reticle on it. "Not this one" strikes an item out
and "Add missed item" lets you click the drawing where one was missed; the count in the answer panel
changes as you go, "Save correction" appends the correction to `out/app/corrections.jsonl` and
"Export" downloads all of them as CSV (`GET /api/corrections.csv`). Area answers list each room with
its polygon crop, its area and the scale source; striking a room out takes its area off the total.

Pins work for every route, including DXF and IFC answers drawn in model coordinates. `bpqa/evidence.py`
records every numbered box and every filled room polygon a route draws, in the pixels of the evidence
image, and saves them next to the PNG as `<stem>.marks.json` (with the world-to-pixel transform for
DXF and IFC canvases). The app pins, crops and numbers items from that file, so no route has to convert
its own coordinates and the numbers always match the overlay.

Timing, measured with Playwright on 10 answers (Swiss DXF doors and windows, CubiCasa detector windows,
WC seats and doors, IFC doors on two models, one Swiss apartment area; 154 items in all). "Without the
strip" means selecting each item in the answer panel list so the viewer zooms to it, and capturing the
viewer; "with the strip" means capturing the strip, one capture per screenful of thumbnails:

| | views to look at | app time, total | app time per answer |
|---|---:|---:|---:|
| without the strip | 154 (15.4 per answer) | 29.2 s | 2.9 s |
| with the strip | 26 (2.6 per answer) | 4.9 s | 0.5 s |

These times are the app's render and capture time under automation, not a person's looking time, which
was not measured. What the strip changes is the number of views: one screenful shows about seven items
side by side instead of one item per zoom. The script is `tools/verify_timing.js`.

### 3D view of IFC models, order sheet and order check

For an IFC model, or a plan sheet cut from one, the viewer has a Plan / 3D switch (`app/ifc3d.py`,
`app/static/viewer3d.js`). The geometry comes from IfcOpenShell's own geometry iterator, the kernel the
IFC route already uses for footprints, built once per model in a background thread and cached in
`out/app/ifc3d/` by file name and size (an upload's geometry stays in its upload folder and goes with
it). Each storey is its own gzip-compressed mesh, loaded four at a time, so the first storey is on screen
before the rest arrive. Storeys stack at their own heights and "Separate storeys" pulls them apart, as in
Stoa's layered view; each storey can be hidden or shown alone. After a question, the elements the answer
used are drawn in blue (doors counted, rooms in a sum, as see-through room volumes), openings deducted
from a wall area in amber, the rest of the answer's storey as a faint shell, storeys above it are cut
away and the camera frames the elements. Clicking an element shows its class, name, GlobalId, storey,
its role in the answer and its property sets (`GET /api/ifc3d/element`). If the kernel cannot read a
model the 3D panel says why and the plan view and answer are unaffected. three.js 0.170.0 is loaded from
jsDelivr by an import map; nothing is vendored. Fill the cache ahead of time with
`.venv\Scripts\python -m app.ifc3d <model.ifc> ...`.

Measured on this PC (8 threads, RTX 5070; server build inside the running app, first request for the
model; browser times from opening the 3D view to the first storey drawn and to all storeys drawn, fresh
browser profile, local server, model cache built, three runs each):

| model | IFC size | elements | triangles | geometry gz | server build | browser: first storey / all storeys |
|---|---|---|---|---|---|---|
| FZK-Haus | 2.6 MB | 83 | 22k | 0.12 MB | 2.0 s | 49 to 51 ms / 53 to 64 ms |
| Esplanades | 12.1 MB | 1,958 | 158k | 1.0 MB | 10.1 s | 65 to 74 ms / 85 to 108 ms |
| Molio | 74 MB | 3,498 | 728k | 4.1 MB | 26.3 s (16.7 s standalone) | 79 ms / 146 to 162 ms |
| duplex (IFC-Bench) | 1.7 MB | 215 | 27k | 0.15 MB | 1.2 s | - |
| ac20 (IFC-Bench) | 10.9 MB | 702 | 44k | 0.22 MB | 4.3 s | - |
| Smiley West | 6.1 MB | 832 | 186k | 0.96 MB | 7.5 s | - |

The Molio build peaks at about 0.7 GB above the process baseline. Over the network the geometry
transfer is added (4.1 MB for Molio).

Takeoff answers (floor and wall tiling, room-area sums) get an Order section in the answer panel and an
order sheet (`app/static/ordersheet.js`): one row per room with floor area, perimeter, height, tiled
height, gross wall area, each deducted opening (width, vertical range, deducted m2), net wall area and
the row total, then floor, walls, net, waste and the order quantity. Surfaces, room height, tiled height
and waste are editable and the sheet recomputes from the per-room figures the route returned (each
opening carries its z range, `z0_m` / `z1_m`, so a lower tiled height deducts only the part of a door or
window inside it); with the answer's own inputs it shows the route's numbers unchanged. Checked against
the backend: FZK bathroom tiled to 1.2 m recomputes to 29.98 m2, the same as asking "tiles to 1.2 m".
Export as CSV, or "Print or save PDF" opens a light A4 landscape page for the browser's print dialog.

The order check takes what is written on an order ("184 m2 suspended ceiling", "120 m2 tiles", "12
doors") and compares it with the drawing's quantity: a ceiling is compared with the rooms' net floor
area, flooring with floor area plus waste, paint or walls with net wall area plus waste, anything else
with the order quantity, a count with the count. Within 2% (an exact count for counts) it says the order
matches; otherwise it states the difference in units and percent, with the rooms behind the drawing's
quantity numbered as on the evidence.

### Three witnesses: cross-format agreement

When the same drawing exists in more than one format, the app asks the question of each format on its
own (`bpqa/witnesses.py`, `POST /api/witnesses`): the DXF layers, the IFC model, the PDF alone (twins
hidden) and the PNG (OCR labels and the symbol detector, twins hidden). Nemotron is off for witnesses, so
they share no reading and cost $0 in model calls. The answer panel shows them side by side with the
agreement ("2 of 3 formats agree on 36; PNG differs") and circles the values that disagree. Selecting a
witness shows its own evidence and strip. When two witnesses are drawn on the same page (a Swiss PDF and
its PNG render have the same page frame), their boxes are matched by position and the items only one of
them counted are circled on the drawing, so a disagreement points at the doors to look at, not just at a
number.

Measured on the preview set, dev split: 576 questions on drawings with two or more formats (Swiss
Dwellings DXF + PDF + PNG, IFC plans model + PDF; up to 12 per drawing, text questions excluded), each
witness scored with `eval.py`'s rule (`tools/witness_eval.py`, `out/witness/witness_summary.json`, 30 minutes on
the CPU):

| witnesses | questions | share | router answer correct |
|---|---:|---:|---:|
| all formats that answered agree | 463 | 80.4% | 99.6% (461) |
| a majority agrees, one format differs | 107 | 18.6% | 100% (107) |
| formats disagree, no majority | 1 | 0.2% | 100% (1) |
| only one format answers | 5 | 0.9% | 100% (5) |
| all | 576 | | 99.7% (574) |

| each witness answer | answers | correct |
|---|---:|---:|
| another format gives the same value | 1,340 | 99.7% (1,336) |
| another format gives a different value | 109 | 0.9% (1) |
| no other format answers | 5 | 100% (5, all IFC) |

What this says, plainly: a format's answer that another format matches is almost always right, and one
that another format contradicts is almost always wrong. On this set that mostly sorts the PNG reader:
it answered 307 of 564 questions and was right on 200, all 200 of them matched by the DXF and the PDF,
and all 107 of its contradicted answers were wrong. The router's own answer (from the DXF here) was never
outvoted, so agreement adds little to its accuracy on Swiss plans, where it is already 99.7%. Agreement is
not proof: the router's two wrong answers are areas where the DXF and the PDF agree with each other
(334.17 vs 341.45 m², 34.08 vs 25.54 m²), because both read the same room outlines. Its use is as a
signal next to a proven or estimated answer, and as a pointer to the items to check when a scan reader
disagrees.

`.venv\Scripts\python tools/witness_eval.py --split dev --per-drawing 12` reproduces it;
`--summarize-only` rebuilds the summary from `out/witness/records.jsonl`.

### Cost and latency, measured

`/api/ask` records wall seconds and CPU seconds (process time, all threads) for every answer;
`tools/cost_eval.py` measures them per route on a sample. Cost per 1,000 questions uses ASSUMED prices,
not quotes: $0.05 per vCPU-hour (a general-purpose cloud vCPU) and $1.00 per GPU-hour (an L4 or A10G
class GPU). The detector ran on the CPU; for the detector route, the last column books its wall time on the assumed
GPU price instead. A question that every route abstained on is booked under the first route tried (13 of
the 14 `pdf-text` rows are IFC-plan and OpeningDesign questions that abstained). The answer panel shows the same numbers as a small cost meter next to the baseline.

Preview set, dev split, 89 questions (up to 10 drawings per source, file type and question type, two
questions per drawing), evidence rendering on, Nemotron off, 16-thread CPU
(`.venv\Scripts\python tools/cost_eval.py --split dev --drawings 10 --per 2`, `out/cost/cost_by_route.json`):

| route | questions | answered | median wall | p90 wall | cold median | CPU s, mean | $ per 1,000, CPU | $ per 1,000, detector on a GPU |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `dxf-geometry` | 40 | 40 | 65 ms | 260 ms | 129 ms | 0.09 | $0.0013 | - |
| `ifc` | 15 | 15 | 231 ms | 2.4 s | 161 ms | 0.73 | $0.0101 | - |
| `pdf-text` | 14 | 1 | 1 ms | 610 ms | 1 ms | 0.13 | $0.0018 | - |
| `raster-detector` | 13 | 13 | 1.6 s | 7.6 s | 6.1 s | 30.28 | $0.42 | $1.13 |
| `raster-ocr-labels` | 5 | 5 | 93 ms | 3.5 s | 3.2 s | 10.03 | $0.14 | - |
| `raster-cv` | 2 | 2 | 4.3 s | 6.5 s | 4.3 s | 34.00 | $0.47 | - |
| all sampled questions | 89 | 76 | 82 ms | 2.8 s | 175 ms | 5.93 | $0.0824 | - |
| Nemotron, plain prompt (baseline) | 150 | - | 24.8 s | - | - | - | $101.04 at measured tokens | |

The Nemotron baseline row uses the tokens measured on 150 preview test questions
(`out/baseline_nemotron.json`: 1,596 input and 6,417 output tokens per question on average, most of them
reasoning) at an assumed $3 / $15 per million input / output tokens: about $101 per 1,000 questions,
against $0.08 per 1,000 for the routes on this sample at the assumed CPU price. The detector's CPU mean is
dominated by the first question on each drawing (model load, detection and the OCR the checks read);
later questions on the same drawing take a median 0.08 s because detections are cached per drawing.

### Split

Dev/test is split by drawing, not by question, with a fixed seed (`SPLIT_SEED` in `bpqa/data.py`, 30%
test). All formats and renderings of one drawing land on the same side: a Swiss PDF and its DXF, an IFC
model and the plans rendered from it, all sheets of one drawing set. Rules were developed by looking at
dev failures only.

## Results

### Results by test set

Final numbers of this version, every line from one named set of questions. Accuracy counts an abstention as
wrong; precision is correct / answered; proven and estimate are the two answer tiers with their precision.
Cost is $0 per question on every line (no model calls; Nemotron fallback off).

| test set | questions | accuracy | precision when answered | proven: n / precision | estimate: n / precision | source |
|---|---|---|---|---|---|---|
| Stoa full set, test split | 39,807 | **80.4%** | 81.4% | 32,041 / 89.8% | 7,278 / 44.6% | `eval.py --data ../data/stoa-full/junction-dataset --split test` (684 s with warm OCR and detector caches), `out/test-junction-dataset/summary.txt` |
| Stoa preview set, test split | 2,701 | **85.7%** | 87.5% | 2,292 / 94.1% | 354 / 44.6% | `eval.py --split test`, `out/test/summary.txt` |
| Stoa preview set, dev split (rules developed here) | 5,289 | 80.5% | 84.6% | 4,225 / 91.9% | 808 / 46.5% | `eval.py --split dev`, `out/dev/summary.txt` |
| Naive AI baseline sample: 150 preview test questions | 150 | router **94.0%** (141), Nemotron with a plain prompt 39.3% (59) | Nemotron 51.3% (answers 115) | - | - | `out/baseline_nemotron.json`; router side re-scored from `out/test/answers.jsonl` (82.0% when the baseline was recorded, before tiers) |
| Unseen public drawings (12 drawings not in Stoa's data) | 50 | **82.0%** (41 correct, 3 wrong, 4 abstained, 2 correctly abstained) | 93.2% (41 of 44) | 0 proven answers wrong | the 3 wrong answers are estimates | `tools/unseen_run.py --data ../data/unseen`, detector on, `out/unseen/results.jsonl` |

Full set, test split, by source: CubiCasa scans 73.5% (27,160 questions), Swiss dwellings 99.0% (11,408; counts
100.0%, areas 97.0%), IFC models 87.7% (838; 99.7% precise), IFC-Bench 27.8% (381 free-text questions; 1.3% before the
`ifc-props` route), OpeningDesign 70.0% (20; 0% before the generic schedule reader). With those two sources
the full test split is 80.7% (32,127 of 39,807; computed from the per-source change, the other sources not
re-run). Per CubiCasa question type: storeys 90.5%, toilets 88.3%,
bathrooms 87.9%, balconies 85.8%, WC rooms 81.2%, sinks 78.7%, showers/baths 76.0%, bedrooms 75.1%, kitchens
64.0%, saunas 63.6%, doors 55.4%, windows 46.4%.

Unseen drawings by format (50 distinct questions; the three DXF questions asked again on the PDF rendered from
the DXF are left out):

| format | n | correct | wrong | abstained | correctly abstained |
|---|---|---|---|---|---|
| IFC | 23 | 23 | 0 | 0 | 0 |
| DXF | 3 | 3 | 0 | 0 | 0 |
| PDF, no twin | 12 | 6 | 0 | 4 | 2 |
| image (scans) | 12 | 9 | 3 | 0 | 0 |

Before the English and German room words the scans got 4 correct, 7 wrong (one of them proven: balconies 0
on an English-labelled scan) and 1 abstention. The three remaining wrong answers are estimates of 0 on scans
whose kitchen or balcony label the OCR misreads by more than one letter, or does not read. Details per
question: `UNSEEN.md` (first run) and `out/unseen/tables.md`.

### Detector v2 (tiers, test-time augmentation, tiled detector)

Preview, `eval.py`: "before" is the route before tiers with the `cubi2` detector (`--no-tiers`), "final"
the current route. Proven and estimate are the two answer tiers, with their precision.

| | dev overall | dev CubiCasa | test overall | test CubiCasa | test proven: n / precision | test estimate: n / precision |
|---|---|---|---|---|---|---|
| before | 66.2% | 47.9% | 72.3% | 49.2% | 2056 / 95.0% (all answers) | - |
| final | 80.5% | 73.9% | 85.7% | 75.6% | 2292 / 94.1% | 354 / 44.6% |

The full set's test split (1,510 CubiCasa images, 14,108 detector questions in all languages, checks
off), exact count:

| object | n | before: exact (precision, coverage) | final: exact | final proven: share / precision | final estimate precision |
|---|---|---|---|---|---|
| toilet | 3016 | 81.0% (84.0%, 96.4%) | 88.3% | 99.7% / 88.5% | 0 of 8 |
| sink | 2982 | 57.5% (74.5%, 77.3%) | 78.7% | 98.2% / 79.6% | 29.6% |
| shower/bath | 2816 | 0% (switched off) | 75.7% | 95.5% / 77.4% | 39.7% |
| door | 2274 | 23.2% (76.3%, 30.4%) | 55.4% | 69.5% / 67.1% | 28.8% |
| window | 3020 | 25.0% (63.9%, 39.1%) | 46.4% | 44.8% / 69.0% | 28.0% |
| **all detector questions** | 14108 | 38.6% (76.8%, 50.2%) | 69.5% | 81.9% / 78.4% | 28.7% |

All 27,160 CubiCasa questions of the full test split with the checking layer: 49.0% before, 73.5% final
(proven 84.1% precise, estimates 44.6%). With the other sources, unchanged: all 39,807 full-set test
questions 63.7% before, 80.4% final.

CHANGELOG.md has the per-step numbers (tiers, check values, TTA, tiled model), the tune-image
comparison per object and the full-test-split numbers for every CubiCasa question type.

### Before detector v2 (preview set, historical)

Produced by `eval.py` (dev: 5,289 questions, test: 2,701). Accuracy counts an abstention as wrong;
coverage is the share answered; precision is correct / answered. Cost is $0.0000 per question on every
row, because no route calls a model. "with --prior" adds the labelled dev-majority baseline on raster
questions where the raster route abstains (it changes only the cubicasa5k rows).

| source | type | dev n | dev accuracy | dev coverage | test n | test accuracy | test coverage | test precision | test accuracy with --prior |
|---|---|---|---|---|---|---|---|---|---|
| cubicasa5k | count | 2908 | 45.3% | 55.3% | 1362 | 45.5% | 52.6% | 86.6% | 66.1% |
| **cubicasa5k** | **ALL** | 2908 | 45.3% | 55.3% | 1362 | 45.5% | 52.6% | 86.6% | 66.1% |
| ifc_bench | text | 214 | 0.5% | 2.3% | 35 | 0.0% | 0.0% | 0.0% | 0.0% |
| **ifc_bench** | **ALL** | 214 | 0.5% | 2.3% | 35 | 0.0% | 0.0% | 0.0% | 0.0% |
| ifc_models | area | 266 | 99.2% | 99.2% | 580 | 100.0% | 100.0% | 100.0% | 100.0% |
| ifc_models | count | 103 | 84.5% | 84.5% | 50 | 100.0% | 100.0% | 100.0% | 100.0% |
| ifc_models | length | 16 | 87.5% | 87.5% | 0 | - | - | - | - |
| ifc_models | volume | 34 | 88.2% | 88.2% | 0 | - | - | - | - |
| **ifc_models** | **ALL** | 419 | 94.3% | 94.3% | 630 | 100.0% | 100.0% | 100.0% | 100.0% |
| opening_design | area | 9 | 0.0% | 0.0% | 0 | - | - | - | - |
| opening_design | count | 22 | 68.2% | 68.2% | 17 | 0.0% | 0.0% | 0.0% | 0.0% |
| opening_design | text | 13 | 38.5% | 38.5% | 3 | 0.0% | 0.0% | 0.0% | 0.0% |
| **opening_design** | **ALL** | 44 | 45.5% | 45.5% | 20 | 0.0% | 0.0% | 0.0% | 0.0% |
| swiss_dwellings | area | 536 | 98.1% | 100.0% | 208 | 100.0% | 100.0% | 100.0% | 100.0% |
| swiss_dwellings | count | 1168 | 100.0% | 100.0% | 446 | 100.0% | 100.0% | 100.0% | 100.0% |
| **swiss_dwellings** | **ALL** | 1704 | 99.4% | 100.0% | 654 | 100.0% | 100.0% | 100.0% | 100.0% |
| **ALL** | **ALL** | 5289 | 64.8% | 70.6% | 2701 | 70.5% | 74.0% | 95.2% | 80.9% |

CubiCasa per question type (EN and FI together; precision = correct / answered):

| object | dev n | dev accuracy | dev coverage | dev precision | test n | test accuracy | test coverage | test precision |
|---|---|---|---|---|---|---|---|---|
| storey (panels) | 330 | 87.9% | 96.4% | 91.2% | 152 | 93.4% | 96.1% | 97.3% |
| balcony (labels) | 94 | 74.5% | 80.9% | 92.1% | 42 | 76.2% | 76.2% | 100% |
| WC room (labels) | 150 | 74.7% | 80.0% | 93.3% | 74 | 75.7% | 78.4% | 96.6% |
| bedroom (labels) | 206 | 57.3% | 65.0% | 88.1% | 102 | 54.9% | 62.7% | 87.5% |
| kitchen (labels) | 228 | 50.9% | 54.4% | 93.5% | 94 | 57.4% | 57.4% | 100% |
| bathroom (labels) | 148 | 54.1% | 55.4% | 97.6% | 74 | 43.2% | 45.9% | 94.1% |
| sauna (labels) | 222 | 19.8% | 22.5% | 88.0% | 108 | 22.2% | 24.1% | 92.3% |
| toilet (detector) | 328 | 76.2% | 91.5% | 83.3% | 152 | 72.4% | 90.8% | 79.7% |
| sink (detector) | 330 | 35.8% | 56.4% | 63.4% | 148 | 35.1% | 45.9% | 76.5% |
| door (detector) | 232 | 25.0% | 49.1% | 50.9% | 120 | 31.7% | 46.7% | 67.9% |
| window (detector) | 330 | 18.2% | 31.5% | 57.7% | 152 | 15.8% | 26.3% | 60.0% |
| shower/bath (detector switched off) | 310 | 0% | 0% | - | 144 | 0% | 0% | - |

All rows include the checking layer. The answer policies and checks were set on dev; test was run once
afterwards. The detector's dev images were held out of its training but are among the images its
thresholds were tuned on, so its dev rows are slightly optimistic; test images were never used for
either. Bedrooms are the weakest label type (88% precise): plans that show part of a neighbouring flat
add its MH labels, and a missed label under-counts. With `--prior`, where a route abstains the most
common dev answer is used (labelled `raster-prior`); on test that brings CubiCasa to 66.1%.

The preview test holds 76 CubiCasa images. On the full set's test split (1,510 CubiCasa images, 14,108
detector questions in all languages; checking layer off, because it needs OCR of every image) the
detector gets toilets 74.8% exact (76.7% precise when it answers), sinks 38.8% (68.5%), doors 24.9%
(62.6%), windows 18.7% (60.4%). Command: `eval.py --data ../data/stoa-full/junction-dataset --split test
--sources cubicasa5k --objects toilet,sink,shower_or_bath,door,window --no-checks`.

Why some cells are 0%: opening_design test is the CTR drawing set, whose schedule layout the parser does
not read yet; ifc_bench answers are long free-form tables and the router abstains on them. The dev
ifc_models abstentions are the FZK-Haus plan PDF, whose IFC model is not in the dataset.

## Limits, stated plainly

- CubiCasa scans are 53% of the questions. With the tiled detector, toilets, sinks and showers/baths
  are mostly right; doors and windows remain the weak counts (many objects per plan, where one missed or
  extra box makes the answer wrong). See the full-test-split table above.
- "Proven" means the route's tuned conditions held, not that the answer is certain: the policy was tuned
  to accept an answer when it is right more often than wrong, so proven door and window counts are about
  70% precise. Estimates are much weaker (20-60% per object) and are labelled as such, with the boxes or
  labels to check.
- Estimates for kitchens and bathrooms are below a constant answer (one of each): when no label or two
  labels are read, the plan's own evidence is missing or misread. `eval.py --prior` shows that constant
  as a baseline column; it is never used as an answer.
- Stoa's CubiCasa images are all in the public CubiCasa5K, and Stoa's counts equal its annotations. The
  detector uses those annotations only for plans whose image is in our train split or not in Stoa at
  all; test-split images and anything matching them are excluded by image fingerprint. A judging set
  drawn from the same CubiCasa5K images would be scored by a model that may have seen their public
  annotations, which is a question for Stoa.
- One classical raster detector was tried and not shipped: counting ellipse-like inner contours as toilets and
  sinks. On dev it matched the exact toilet count on 2 of 164 plans (correlation 0.34), far below the
  65% a constant answer gets. Text glyphs and door swings produce too many ellipses.
- A second fixture attempt was not shipped either: candidates are closed white regions (the inside of a
  bowl, basin or tub) and a small MLP on HOG features is trained from dev image counts only (multiple-
  instance counting). Cross-validated on dev it gets the exact toilet count on 31% of plans, sinks 19%,
  showers/baths 27%; small rooms, text and hob symbols outrank the toilet bowls. See `CHANGELOG.md`.
- The English and German room words have no dev data behind them (Stoa's scans are Finnish); they were checked
  on the unseen drawings only, and on Stoa's preview dev and test they change no answer. The proven / estimate
  rules for them are the ones tuned on Finnish labels.
- Room counts follow the labels printed on the plan. An unlabelled open kitchen, a sauna marked only by
  its stove, or a flat partly cut off by the scan edge is not recoverable this way, which is why those
  cases abstain instead of answering.
- The test split holds few drawings for some sources: ifc_models test is a single model (Esplanades,
  630 questions), so its 100% says the rules transfer to one unseen model, not to IFC in general.
- Nemotron estimates are not proofs. On the dev abstentions about half are right, and doors, windows,
  balconies and WC rooms on scans are mostly wrong. The model sees the drawing downscaled to 1600 px, its
  boxes are checked for shape and count but not for position, and 23% of plain-prompt replies ran out of tokens
  even at 16,384 (reasoning). The Nemotron numbers come from one 150-question test sample and a 200-question
  dev sample, so each percentage carries several points of sampling error.
- Compound and breakdown questions ("which types and how many of each", "on level 1" without quotes)
  abstain on purpose. Answering them with one number for the whole building was wrong every time.
- Text answers (ifc_bench, some opening_design) are free-form; `eval.py` scores them approximately (all
  numbers in the gold answer present within 2%, else 60% token overlap). That scorer is lenient: "B3" is
  scored correct against "B3 (aluminium sliding door)", and extra content is never penalised.
- `ifc-props` maps a question to a lookup by keyword rules. Many IFC-Bench gold answers group types differently
  (the family part of a Revit "Family:Type" name, English names the model does not contain) or want
  judgement ("what does this structure suggest"); those stay wrong. Broad lookups are labelled estimates.
- The generic schedule reader needs a header line with 3+ schedule words and was developed on two sheets
  (Sports Complex A-801, Cannery Trail A700); other offices' schedules may need more header words.
- `kit-fzk-haus` plan PDFs have no IFC model in the dataset, so model-level questions about them abstain.
- Wall takeoffs need room outlines: IFC models and Swiss DXF floors. PDFs without a twin and scans answer
  floor areas only and abstain on walls. On drawings the ceiling, door and window heights are stated
  assumptions, so those wall answers are estimates. The whole room outline is taken as wall, so a room open
  to another (a gallery, a kitchen open to the living room) is over-counted; sloped ceilings are not modelled.
  A room type the classifier does not know (or a generic "Zimmer") is left out and listed.
- CubiCasa takeoffs need printed areas. Measured room regions are an estimate only (median error 9%
  on dev); furniture drawn against walls, open doorways without swing arcs and stairs are the main
  causes. Printed areas are read from the upright OCR pass only.
- Unnamed numbered rooms in Swiss floors are ambiguous (wet rooms, shafts, balconies). The fixture rule
  is right more often than not, and is where most remaining Swiss area errors are.

## Next steps

1. Detector: longer training, a larger model and tiled inference for the small fixtures; a shower and
   bathtub head that works; a counting policy for doors and windows that tolerates one uncertain box.
2. Nemotron route: verify the boxes it returns (a second pass per box, or the detector on the cropped region)
   so that a wrong estimate can be withdrawn instead of only drawn; reasoning latency is 60 to 100 s on dense
   floors.
3. Wall takeoff on PDFs without a twin (pdf-vector room shapes and door swings) and windows in thick walls
   (the 0.2 m opening rule misses them on Molio).

## Data credits and licences

The dataset is used for the Junction challenge only and is not included in this repository. Credits as
given in each source's `LICENSE.md`:

| source | licence | credit |
|---|---|---|
| swiss_dwellings | CC BY 4.0 | Swiss Dwellings v3.0.0, Archilyse AG, https://doi.org/10.5281/zenodo.7788422; drawings rendered by the challenge organisers |
| cubicasa5k | CC BY-NC-SA 4.0 (NonCommercial) | CubiCasa5K, (c) 2019 CubiCasa. Kalervo, Ylioinas, Häikiö, Karhu, Kannala, SCIA 2019 |
| ifc_models | per file: CC BY 4.0, CC BY-ND 3.0, MIT, KIT unrestricted | buildingSMART Community Sample-Test-Files (Esplanades by Esplan, Molio Demo-hus), KIT IAI, ResBIM (Xing Liang) |
| ifc_bench | per file; questions CC BY 4.0 | IFC-Bench v2, Sylvain Hellin, TU Munich |
| opening_design | CC BY-SA 4.0 and MIT | OpeningDesign, Madison, Wisconsin |

Evidence images are derived from the drawings and carry the drawing's licence: overlays on CubiCasa
scans are NonCommercial and ShareAlike. For NoDerivatives files (Duplex, Medical-Dental Clinic) no overlay
is rendered at all; the answer JSON lists the counted entities by GlobalId instead. `out/` is not
committed.

## Licence

Code: MIT, see `LICENSE`.
