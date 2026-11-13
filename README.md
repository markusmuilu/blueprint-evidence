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
`--evidence` (render overlays into `out/evidence/`), `--prior` (see below). Results go to
`out/<split>/answers.jsonl`, `summary.txt` and `summary.json`.

One question:

```
.venv\Scripts\python ask.py data/swiss_dwellings/site1000_bldg2082_floor9385.pdf "How many windows are drawn on this floor plan?"
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
