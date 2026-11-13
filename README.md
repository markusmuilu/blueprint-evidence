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
                       |                                   (LLM parser hook for questions the rules miss)
drawing  --> router: cheapest route that can prove the answer
               1. dxf-geometry  DXF layers of the same drawing (Swiss Dwellings)
               2. ifc           IFC entities, storey containment, quantity sets, space geometry
               3. pdf-text      text layer and schedules (door schedule rows)
               3b. pdf-vector   the PDF's own linework, when no DXF/IFC twin exists (or --pdf-only):
                                room shapes, scale, doors, windows, fixtures; scanned PDFs abstain
               4. raster        scans (CubiCasa): panel segmentation, room labels read by local OCR,
                                a trained detector for fixtures, doors and windows;
                                abstains where the count is not provable
          --> checking layer (raster answers): consistency checks across routes; abstain on conflict
          --> answer JSON {value, unit, route, confidence, evidence, explanation, items, checks}
          --> evidence PNG: counted items boxed and numbered, measured rooms filled with their area
```

A route either proves an answer or abstains with a reason, and the router then tries the next one. All
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
| bedrooms, kitchens, saunas, WC rooms, bathrooms, balconies | room labels read by a local OCR model (RapidOCR, ONNX, CPU) in four orientations and on the mirrored image (some scans are mirrored), matched against the Finnish abbreviations (MH, K, KK, S, SAUNA, WC, KH, KPH, PH, PESUH, SH, PARVEKE) and a few Swedish ones; apartment summaries like "3H+K+S" are skipped |
| when to answer | kitchen, bathroom, sauna only when exactly one label is read (two or more is usually a neighbouring flat in the scan); bedrooms when at least one MH is read; WC rooms always; balconies 0 or 1; a zero needs at least three readable room labels |
| toilets, sinks, showers/baths, doors, windows | `raster-detector` route: boxes from a YOLO detector counted above a per-object score threshold; abstains when any box scores close to the threshold (see "Detector") |

Every counted label is boxed and numbered on the scan; the other room labels that were read are boxed in
grey, so a zero answer can be checked too. The OCR is the slow part (about 5-15 s per image for eight
passes); `tools/ocr_cache.py` fills the cache in `out/ocr/` up front.

### Checking layer

Before a raster answer is returned, `bpqa/checks.py` compares it with what the other raster signals
on the same image imply, and abstains when they conflict. Examples: a label "MH3" means at least three
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
  inside the walls, no allowance for cutting waste. Wall surfaces abstain (they need room heights and
  openings). "Toilets" means separate WC rooms; bathrooms are not included, and the answer says so.
  Paint answers the floor area and says walls are not included.
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
- a takeoff answers only from printed areas. A room without one makes the sum abstain, and the
  abstention gives the measured estimate (region x scale) with its dev error, because measured areas
  are not yet within Stoa's 2% (see below; `MEASURED_ANSWERS` in `raster_area.py` switches them on).
  It also abstains when the full eight-pass OCR (when cached) reads more labels of the asked type than
  were located. One-letter labels (S, K) must be upper case.

Takeoff results (every number against a stated reference; dev was used for development, test run once):

| check | reference | dev | test |
|---|---|---|---|
| Swiss bedroom sum (DXF route) | Stoa's "total net area of rooms labelled 'Schlafzimmer' / 'Chambre a coucher' / 'Camera da letto'" | 150/150 within 2% | 91/91 |
| Swiss bedroom sum (`--pdf-only`) | same | 60/60 | - |
| Swiss bathroom, kitchen rooms in the sums | Stoa's per-room areas, every asked room | 158/158 | 155/156 |
| Swiss bathroom, kitchen sums, where Stoa asks about every such room on the floor | sum of Stoa's per-room areas | 18/18 | 19/20 |
| IFC bathroom, WC, kitchen, bedroom sums (models and plan sheets) | sum of Stoa's per-room areas, same completeness rule | 56/56 (60 type sets: 2 abstain, 2 answered with a room list that differs from the asked rooms) | 18/18 (19 sets: 1 differs) |
| IFC rooms in the sums | Stoa's per-room areas | 204/204 | 184/184 |
| CubiCasa measured room area, leave-one-out | the area printed on the plan, held out of the scale | 18.7% within 2%, 36.8% within 5%, median error 9.3% (193 of 1471 printed rooms measurable, 52 of 197 plans) | 4.8% within 2%, 21.4% within 5%, median error 12.3% (42 of 303 printed rooms measurable, 12 of 45 plans; 635 test images OCRed) |

Baselines: before this branch every takeoff question was a parser abstention (0% coverage). The
existing area answers did not change: `eval.py` on the preview dev split gives the same Swiss, IFC
and opening_design rows as the Results table; ifc_bench answers one more question (the duplex
bathroom tiling question, 15.81 m2 from IfcSpace footprints, where the ifc_bench answer is 18.85 m2
from finish data while Stoa's own ifc_models answers give Bathroom 1 (A104) 3.16 m2 as we do).
CubiCasa takeoffs are answered only from printed areas, so their accuracy is the OCR read of those
labels, which has no reference in Stoa's data.

### Detector

Toilets, sinks, showers, bathtubs, doors and windows on the scans are counted by a small YOLO detector
(Ultralytics, `yolo26n` from scratch), with every counted box drawn and numbered. Training data:

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
train-split images (`tools/tune_detector.py`). Test answers are never read while tuning.

## Run it

Windows, Python 3.11:

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
set STOA_DATA=C:\path\to\junction-dataset-preview
.venv\Scripts\python eval.py --split dev --evidence
```

`eval.py` options: `--split dev|test|all`, `--sources swiss_dwellings,ifc_models`, `--limit N`,
`--evidence` (render overlays into `out/evidence/`), `--prior` (see below), `--pdf-only` (PDF questions
only, DXF/IFC twins ignored; `ask.py` takes it too). Results go to
`out/<split>/answers.jsonl`, `summary.txt` and `summary.json`.

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

Tests: `python tests/test_cubicasa_svg.py` (converter, hand-made SVG) and
`python tools/test_overlap.py --work <scratch folder>` (overlap tool on the preview).

### Split

Dev/test is split by drawing, not by question, with a fixed seed (`SPLIT_SEED` in `bpqa/data.py`, 30%
test). All formats and renderings of one drawing land on the same side: a Swiss PDF and its DXF, an IFC
model and the plans rendered from it, all sheets of one drawing set. Rules were developed by looking at
dev failures only.

## Results

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

- CubiCasa scans are 53% of the questions. The detector is good for toilets (about 75% exact) and weak
  for sinks, doors and windows (16-36% exact on test, 60-77% precise when it answers). Showers and baths
  are switched off: 24% exact even before abstention, below a constant answer. Doors and windows are
  many-object counts where one missed or extra box makes the answer wrong.
- `--prior` answers the remaining raster questions with the most common dev answer per object type, as
  a labelled baseline (route `raster-prior`, evidence says "PRIOR, not visual proof"). It is there so
  the detector has a number to beat, not because it is a method.
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
- Room counts follow the labels printed on the plan. An unlabelled open kitchen, a sauna marked only by
  its stove, or a flat partly cut off by the scan edge is not recoverable this way, which is why those
  cases abstain instead of answering.
- The test split holds few drawings for some sources: ifc_models test is a single model (Esplanades,
  630 questions), so its 100% says the rules transfer to one unseen model, not to IFC in general.
- Compound and breakdown questions ("which types and how many of each", "on level 1" without quotes)
  abstain on purpose. Answering them with one number for the whole building was wrong every time.
- Text answers (ifc_bench, some opening_design) are free-form; `eval.py` scores them approximately (all
  numbers in the gold answer present within 2%, else 60% token overlap). That scorer is lenient: "B3" is
  scored correct against "B3 (aluminium sliding door)".
- The door-schedule parser is tuned on one sheet (Sports Complex A-801). The CTR schedule uses a different
  layout and abstains.
- `kit-fzk-haus` plan PDFs have no IFC model in the dataset, so model-level questions about them abstain.
- Takeoff answers are floor areas. Wall areas (tiles on walls, paint) abstain: they need heights and
  openings. A room type the classifier does not know (or a generic "Zimmer") is left out and listed.
- CubiCasa takeoffs need printed areas. Measured room regions are an estimate only (median error 9%
  on dev); furniture drawn against walls, open doorways without swing arcs and stairs are the main
  causes. Printed areas are read from the upright OCR pass only.
- Unnamed numbered rooms in Swiss floors are ambiguous (wet rooms, shafts, balconies). The fixture rule
  is right more often than not, and is where most remaining Swiss area errors are.

## Next steps

1. Detector: longer training, a larger model and tiled inference for the small fixtures; a shower and
   bathtub head that works; a counting policy for doors and windows that tolerates one uncertain box.
2. A cheap vision-LLM route behind the router, called only when no structured route exists, with its
   answer required to point at regions that are then drawn as evidence.
3. Generalise the schedule parser (column detection from header words) to cover other door, window and
   finish schedules in the opening_design sets.

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
