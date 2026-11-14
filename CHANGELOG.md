# CHANGELOG

What moved the numbers. Dev numbers are measured with `eval.py --split dev`; test was run only at the
end of a session. Accuracy counts an abstention as wrong; precision is correct / answered.

## 2026-11-14: room-type detector and raster-rooms route, branch `room-detector`

Problem: kitchens, saunas, bathrooms and bedrooms on CubiCasa scans were counted only from OCR of the room
labels, which are often missing or unreadable (kitchens 64% exact on the full test split, most of them
estimates that were about 20% right).

- Room polygons from public CubiCasa5K (`tools/cubicasa_rooms.py`): Space class + NameLabel + fixtures
  inside. A WC room is a Bath space named "WC"; most saunas are "Space Undefined" holding a sauna stove.
  The mapping reproduces Stoa's train-split answers on 99.1-100% of images per type.
- Dataset (`tools/build_rooms_dataset.py`): same allowed plans and the same 624 held-out tune images as the
  fixture detector; 2,678 train / 161 val images.
- Training: Modal GPUs need a payment method on the account (not added), so a `yolo26n` at 640 px trained
  on a Modal CPU container (`tools/train_rooms_modal.py`, mAP50 0.78 after 8 epochs) was the first model;
  the final `yolo26s` at 896 px trained on the PC GPU (batch 4, about 3 GB VRAM) for 11 epochs (val
  mAP50 0.867), stopped early when the GPU got busy. Modal spend about $2.4.
- Route `raster-rooms` (`bpqa/routes/raster_rooms.py`): detector count vs OCR label count; agreement proven
  under a per-type proof rule, disagreement an estimate with both readings, valued by a per-type preference;
  all tuned on the tune images only (`tools/tune_rooms.py`). The router sends raster questions to it; it hands
  every question that is not a room-type count to `raster.answer` unchanged.
- TTA (eight rotations/mirrors) was tried on the tune images: under 1 point of cross-validated gain per type
  for 8x the CPU time, so it is off.

Full set test split (run once, thresholds frozen), exact count: kitchen 64.0% -> 90.8%, bathroom 61.5% ->
85.4%, WC 81.2% -> 89.4%, sauna 63.6% -> 93.4%, bedroom 75.1% -> 81.5%, balcony 85.8% -> 85.8%; all six
70.2% -> 88.1% (10,032 questions). Proven precision: WC 82.0% -> 96.4%, sauna 81.9% -> 96.2%, balcony
87.6% -> 93.6%, kitchen 98.8% both, bathroom 96.8% -> 93.7%; bedrooms are no longer proven (86.4% before).
Preview dev (its images are tune images): 75.4% -> 87.2%.

## 2026-11-14: raster area estimates and scan wall takeoff, branch `raster-area-est`

Problem: on a scan with some printed room areas a takeoff abstained for every room that prints none, and wall
questions on scans abstained. The scale from the printed rooms was already there (`raster_area.py`).

- Rooms without a printed area are answered with region x scale as tier estimate when the scale is established
  (2+ printed rooms agree within 4%). Printed rooms keep their printed value. Each answer states which rooms are
  printed and which measured, the printed rooms that set the scale, and the measured error band; measured rooms
  go into `uncertain`. Without a scale, or without a closed region, the room still abstains with the reason.
- Walls on scans: floor and walls as separate lines (`bpqa/walls.py` summary, same inputs as the vector routes);
  walls = perimeter of the region x ASSUMED 2.5 m (editable), openings not deducted and said so; estimate only;
  `raster-area` joined `WALL_ROUTES` in `takeoff.py` so the router no longer withdraws it.
- Evidence: printed rooms blue with the printed value boxed, measured rooms amber with "measured X m2".

Leave-one-out (`tools/area_eval.py --out`, then `tools/area_band.py`), scale from the other printed rooms, same
region code as before (dev 193 rooms measurable: 18.7% within 2%, 36.8% within 5%, median 9.3%, as in the
`area-route` entry; full test split 1,510 images: 73 rooms, 6.8% / 23.3% / median 11.9%). The headline median
hides a heavy tail, so the band is quoted by the measured room's size (dev + test pooled, 266 rooms, 74 plans):

| measured area | rooms | median error | 8 in 10 within | within 5% | within 10% | signed median |
|---|---|---|---|---|---|---|
| under 3 m2 | 73 | 46.3% | 77% | 13.7% | 24.7% | -46% |
| 3 to 6 m2 | 50 | 13.0% | 38% | 28.0% | 44.0% | -3.6% |
| 6 m2 and over | 143 | 6.5% | 20% | 44.8% | 63.6% | +0.3% |
| all | 266 | 10.6% | 50% | 33.1% | 49.2% | -2.6% |

Under 3 m2 the region is usually a fragment of the room (dev -45%, test -55%, same sign), so a measured room under
3 m2 is not answered (`MIN_MEASURED_M2`); the threshold was read off dev and held on test. Small rooms are what a
wet-room takeoff asks about, so many toilet questions still abstain. The test split was run a second time for
the band; no setting was chosen on it besides confirming the 3 m2 pattern. Caveat: the reference is printed
rooms, which are not the rooms that lack a printed area.

Wall perimeter (`tools/perim_eval.py`): region against the annotated room outlines of CubiCasa's model.svg (read as
data, frame F1_scaled), scale-free. The traced contour is ragged and overstates the walls; the convex hull's perimeter
was chosen from five variants on dev and held on test:

| perimeter of the region | dev (303 rooms, 55 plans) median / 8 in 10 / 9 in 10 | test (116 rooms, 21 plans) |
|---|---|---|
| traced contour, simplified 2 px (first version) | 17.2% / 49.5% / 65.1% | 20.5% / 48.6% / 74.1% |
| contour simplified 12 px | 13.3% / 42.6% / 57.9% | 16.9% / 47.0% / 64.8% |
| convex hull (used) | 7.8% / 26.0% / 39.5% | 10.8% / 30.1% / 41.7% |

Pooled hull: median 8.7%, 8 in 10 within 27%, signed -4% (a concave room comes out short). This is the region, not
the answer: the assumed height and the missing opening deduction come on top, so the wall figure is gross.

Regression: preview dev (5,289 questions) and test (2,701) run with `eval.py` on `main` (03276ca, from a `git archive`
copy) and on this branch: 0 answers differ in value, abstain reason, tier, route or correctness
(dev 3,273 correct, 61.9%; test 1,811 correct, 67.0%). Stoa has no area question on a scan, so the dataset numbers
cannot move. Tests: `tests/test_raster_area.py` (new, synthetic plan), `test_walls.py`, `test_takeoff.py` pass.

## 2026-11-14: wall-surface takeoff, property and free-text questions, generic schedules, branch `walls-text`

Problem: two gaps against Stoa's brief. Tiles in toilets and bathrooms go on the walls, and wall questions
abstained. IFC-Bench free-text questions (0.2% on full-set dev) and the second OpeningDesign schedule layout
abstained, and so would a judge's "what is the fire rating of door D-12".

- `bpqa/walls.py` (new): wall area = outline length x tiled height - openings on that room's walls, floor,
  walls, waste and order quantity as separate lines; inputs editable from the question (ceiling height,
  tiling height, waste %, "no waste", "full height"). Tiles in wet rooms now mean floor + walls ("floor
  tiles" stays floor only). IFC (`ifc_route.py`): outline from the space geometry, height from the space's
  height quantities, else geometry, else storey; openings from IfcDoor / IfcWindow footprints within 0.2 m
  of the outline, deducted by their own z range inside the tiled band; the model's own NetWallArea shown as
  a check (more than 5% apart makes an estimate). Swiss DXF (`dxf_swiss.py`): ROOM_OUTLINE, door leaves,
  window strips, ASSUMED heights stated, tier estimate. The router withdraws other routes' floor-only answers
  to a wall question. Evidence: wall outlines thick, deducted openings boxed amber with their area.
- `tools/wall_eval.py`: against the authoring tools' own quantities in the IFC models (never read by the
  takeoff): perimeter 1,470 spaces 67.3% within 2% (88.7% within 5%), gross wall area 454 spaces 66.5%,
  net wall area 511 spaces 51.5% (median error 1.8%); KIT institute 96% net, Smiley West 76%. Rejected after
  measuring: outline edges along walls only (gross 66.5% -> 46.0%, net 51.5% -> 34.8%) and opening tolerances
  of 0.35 to 0.7 m (worse on four of five models).
- `bpqa/routes/ifc_props.py` (new, route `ifc-props`): typed lookups over the model for questions the
  numeric routes cannot answer (README "Property and free-text questions"); every value read from the file
  and cited by GlobalId; narrow lookups proven, broad ones estimates. Router (`text_routes`): tried before
  abstaining on a parse failure or compound question, and after the numeric routes abstain.
- `bpqa/schedules.py` (new) and `pdf_text.py`: generic schedule tables (header words, drawn grid lines),
  counts sum a Count column, lookups read the named column plus the legend meaning of a code.
- `tools/nemo_props_eval.py`: Nemotron choosing a lookup for questions the rules leave unmapped (60 dev
  questions: 24 mapped, 9 right, about $0.05 at the assumed list price). Off in `eval.py` unless `--nemo`.
- Tests: `tests/test_walls.py` (inputs, surfaces, arithmetic); `tests/test_takeoff.py` unchanged and passing.

| set (full set) | before | after | after proven: n / precision | after estimate: n / precision |
|---|---|---|---|---|
| IFC-Bench dev (439) | 0.2% (1) | 33.3% (146) | 57 / 64.9% | 285 / 38.2% |
| IFC-Bench test (381) | 1.3% (5) | 27.8% (106) | 69 / 39.1% | 242 / 32.6% |
| OpeningDesign dev (44) | 45.5% | 90.9% (40, no wrong answer) | 40 / 100% | - |
| OpeningDesign test (20) | 0% | 70.0% (14; 5 wrong) | 19 / 73.7% | - |

Text answers are scored by `eval.py`'s lenient rule (every gold number present within 2%, else 60% of the gold
words); 20 random dev answers scored right were read by hand, 19 answer the question and 1 is partly right.
OpeningDesign test is not clean: the schedule reader was built looking at the Cannery Trail sheet's layout too.

Regression: preview dev for swiss_dwellings, ifc_models, opening_design and ifc_bench compared per question
with the `polish` run (`out/after2/dev/answers.jsonl` there): 0 questions that were right are wrong now, and no
answer value changed for swiss_dwellings or ifc_models (swiss 99.4%, ifc_models 94.3%, as before). CubiCasa
was not re-run: the changes reach raster questions only through takeoff wall questions, which abstained before.

## 2026-11-14 morning: English and German room labels, evidence colours, app tiers and results, branch `polish`

Problem: on the unseen-drawing set the room-label route read only Finnish and Swedish words, so the two
English-labelled scans (Tyneside, Ballymun) got zeros, and the "a zero needs three readable labels" guard
was passed by OCR noise ("S", "IV"), which let "balconies 0" through as proven on the Ballymun scan.

- `bpqa/routes/labels.py`: English and German room words (BEDROOM, KITCHEN, BATHROOM, TOILET, BALCONY,
  SCHLAFZIMMER, KUCHE, BADEZIMMER, BALKON, ...; two-word labels such as "Bed room" joined first) and English and
  German plan words for the readability guard (LOUNGE, HALL, LANDING, FLUR, WOHNEN, ...). Words of 6+ letters
  also match with one OCR error ("bedrocm3", "bathroon"), marked `fuzzy`; the Finnish abbreviations never
  match fuzzily. BATH, SHOWER and DUSCHE are left out on purpose (also printed next to the fixture symbol).
- `bpqa/routes/raster.py`: a count that needs a fuzzy read is an estimate; a zero on a plan where any label
  was read only fuzzily is an estimate (the OCR misreads this plan). "How many rooms labelled 'X'" on a scan
  counts the readings of X, always as an estimate (no Stoa scan question asks it).
- Tiers on the generic routes: `dxf-generic` answers below confidence 0.8 (rooms counted from text labels
  alone) are estimates; the convention-free PDF reading now answers under its own route name
  `pdf-generic` and is always an estimate. Before, the router marked both "proven".
- `bpqa/evidence.py`: one blueprint blue (oklch 0.50 0.13 240, darkened from the app accent so it holds 5.8:1
  on white) for proven items, amber for items to check, blue tones where rooms must be told apart; strokes and
  number tabs scale with the image so they stay readable when a 4,000 px scan is shown at 1,600 px. The
  detector, label, generic DXF/PDF and Nemotron overlays use it.
- App: names for `dxf-generic`, `pdf-generic`, `nemotron-estimate` and `raster-area`; the answer's own tier and
  reason come first; the Results view names the dataset and split of every run, shows one line per named test
  set with its source file, and fills the naive AI baseline card from `out/baseline_nemotron.json` (our side
  re-scored from the newest preview test run). `eval.py` writes `data=<dataset>` into the summary header.

Regression: preview dev and test answers identical before and after every step (0 of 5,289 and 0 of 2,701
questions differ in value, tier or route): dev 80.5%, test 85.7%. Full set, test split: 39,807 questions, 80.4% (32,012 correct), proven 32,041 at 89.8%, estimates 7,278 at 44.6%, the same accuracy as the detector-v2 numbers. Compared with the detector branch's full CubiCasa run, two answers changed tier and none changed value: on one plan the new plan word PATIO raises the room-label count to 6, the doors >= rooms - 1 check fails, and the door count 4 (correct) becomes an estimate.

Unseen set (`tools/unseen_run.py --data ../data/unseen`, detector on the GPU, Nemotron fallback off; 50
distinct questions, the three DXF questions asked again on the PDF rendered from the DXF are left out):

| format | n | before: correct / wrong / abstain / abstain-ok | after |
|---|---|---|---|
| IFC | 23 | 23 / 0 / 0 / 0 | 23 / 0 / 0 / 0 |
| DXF | 3 | 3 / 0 / 0 / 0 | 3 / 0 / 0 / 0 (the 'OPEN OFFICE' count is now an estimate) |
| PDF, no twin | 12 | 6 / 0 / 4 / 2 | 6 / 0 / 4 / 2 (the 6 answers are now estimates, route `pdf-generic`) |
| image | 12 | 4 / 7 / 1 / 0 | 9 / 3 / 0 / 0 |
| all | 50 | 36 / 7 / 5 / 2 | 41 / 3 / 4 / 2 |

Proven and wrong: 1 before (Ballymun balconies 0), 0 after. Of the 41 correct answers 30 are proven and 11
estimates. The three wrong answers are all estimates of 0: kitchens on Ballymun (the OCR reads "Htchon") and
on Milam (no kitchen label read), balconies on Ballymun ("BALENCY", "talcom": more than one error). With
`--pdf-only` the rendered DXF still gets 1 of 3 (doors). "Before" is main at fb96ee4 with the same setup
(the generality CHANGELOG entry's numbers were without the detector and before tiers).

## 2026-11-14: generality on drawings Stoa did not ship, branch `generality`

Problem: the unseen-drawing test (12 public drawings, `UNSEEN.md` in the unseen worktree) found the
DXF route returning 0 doors, 0 windows and 0 rooms at confidence 0.95 ("proven") on a DXF whose layers
are called A-DOOR and A-WINDOW, the parser reading the model name "10 Appartementen Schependomlaan" as
the object "apartment", "the area of the room 'living room'" without a level answered as the total of
all rooms, and pdf-vector answering nothing on vector PDFs from other offices. Stoa: do not hard-code.

- `bpqa/routes/dxf_swiss.py`: the Swiss rules run only when the file is in the Swiss layer convention
  (`swiss_scheme`: entities on ROOM_OUTLINE and ROOM_TEXT, DOOR / WINDOW / FIXTURE in the layer table;
  true for all 1,040 Swiss DXFs of the full set). Every other DXF goes to the generic route.
- `bpqa/routes/dxf_generic.py` (new, route `dxf-generic`): layer and block names are classified from
  their tokens: AIA/NCS (A-DOOR, A-GLAZ, A-WALL, A-AREA, P-FIXT; modifiers such as -IDEN make door tags
  annotation), ISO 13567 / CI/SfB element codes (21/22 walls, 31 windows, 32 doors, 74 sanitary) and
  plain words in EN, DE, FR, IT, FI, SV, NL, ES. If no layer, block or entity of the asked class
  exists the route abstains and lists the layers it saw; it never returns a count of 0. Doors: door
  block inserts, else swing arcs of 30-180 degrees on door layers, two swings hinged at both ends of
  one opening = one double door. Windows: window block inserts, else window-layer pieces grouped when
  parallel, overlapping along the wall and within a wall thickness. Fixtures from block names, columns
  from closed shapes on column layers, rooms from closed polylines on room/area layers with the label
  inside, else one room per distinct text label (confidence 0.75, below the "proven" line). Units from
  $INSUNITS unless the door-swing radius contradicts it (template default "metres" on a cm drawing).
- `bpqa/query.py`: object words are read outside quoted names first (falls back to the full text
  only when nothing outside names an object). A quote right after "room / space / Raum ..." is that
  room's label, not a model name. Such a question, and an English singular takeoff ("the area of the
  living room", "tiles for the kitchen"), sets `single_room`. Full-set parse diff (127,097 questions):
  1,484 parses change, all ifc_models: 17 object fixes (model 'Duplex Apartment ...'), 14 Dutch
  'doucheruimte (n)' room areas that were parsed as showers, the rest only gain the `single_room` flag.
- `bpqa/routes/scope_guard.py` (new): with `single_room`, the DXF, generic DXF, PDF and IFC routes
  abstain when more than one room matches and list them ("10 rooms match: badkamer (3.08) on 00 begane
  grond, ...") instead of summing.
- `bpqa/routes/pdf_generic.py` (new), used by pdf-vector only where it abstained before: door swings
  (circular 60-120 degree arc with a straight leaf of its radius from the hinge; legend symbols next
  to the word for door or under a legend heading dropped; double doors merged), room-label counts and
  room-type counts (bedroom, kitchen, bathroom, WC, sauna, balcony) from text spans. Confidence 0.7,
  never a 0. As a check on a convention it was not built on: the swing count equals the DXF
  door-leaf count on 60 of the 65 preview Swiss PDFs and on 916 of the 1,040 full-set Swiss PDFs
  (3 abstain; the misses are mostly under-counts, sliding doors have no swing). It is not used on
  Swiss PDFs; the Swiss rule answers there.
- Tests: `tests/test_dxf_generic.py` writes AIA (blocks, A-AREA rooms, door tags), German (bulge
  swings, wrong $INSUNITS, double door, framed windows), ISO 13567 and bare files and checks counts,
  areas, the single-room abstention and that nothing returns 0; `tests/test_query_names.py`.

Unseen set (50 distinct questions, `tools/unseen_run.py`, this venv has no torch, so the raster
detector is off in both runs; with it the toilet question is answered, as in UNSEEN.md):

| class | n | before: correct / wrong / abstain / abstain-ok | after |
|---|---|---|---|
| ifc | 23 | 18 / 2 / 3 / 0 | 23 / 0 / 0 / 0 |
| dxf | 3 | 0 / 3 / 0 / 0 | 3 / 0 / 0 / 0 |
| pdf, no twin | 12 | 0 / 0 / 10 / 2 | 6 / 0 / 4 / 2 |
| image | 12 | 2 / 1 / 9 / 0 | 2 / 1 / 9 / 0 |
| all | 50 | 20 / 6 / 22 / 2 | 34 / 1 / 13 / 2 |

The PDF rendered from the DXF (twin present) went from 3 wrong zeros to 3 correct; with `--pdf-only`
the door count is now 12 (correct), windows and 'OPEN OFFICE' still abstain (text drawn as outlines,
no generic window rule). The one remaining wrong answer is raster-ocr-labels (balconies 0 at 0.7 on
an English-labelled scan), outside this branch.

Regression, preview dev, `eval.py --split dev --sources swiss_dwellings,ifc_models,opening_design`:
Swiss 1694/1704, IFC 395/419, opening_design 20/44 before and after (every row identical); with
`--pdf-only` Swiss 1692/1704, IFC 26/78, opening_design 20/44 before and after. Full-set dev,
ifc_models only: 1655 -> 1682 correct of 2224, precision 99.6% -> 99.6%.

## 2026-11-14: quantity takeoff (room-type areas), branch `area-route`

Problem: Stoa said areas matter most to them ("How many square meters of tiling should I order for the
toilets?"), and judging may ask such questions on any drawing type. Before this branch every takeoff
phrasing was a parser abstention.

- `bpqa/takeoff.py`: takeoff parser (EN, FI with inflected stems and the plan abbreviations, DE, FR,
  IT), room-type classifier for drawing labels, shared answer layout (rooms, areas, total,
  interpretation). Fires on 2 of Stoa's 127,044 questions (both ifc_bench takeoff questions); the
  existing parses are unchanged (`tests/test_takeoff.py`, full-file check).
- Vector routes get a `type_sum` branch only; the existing per-room and per-label rules were not
  touched. Preview dev `eval.py` after the change: Swiss 99.4%, IFC 94.3%, opening_design 45.5%, the
  same as before; ifc_bench answers one more question (wrong against its 18.85 m2 finish-data answer).
- IFC plan sheets: a takeoff without a level uses the storey whose name and space labels are printed
  on the sheet. First version counted plain label hits and picked the wrong storey on a model whose
  floors all have "Bad" and "Gang"; labels are now weighted by 1 / (storeys that have them) and the
  printed storey name counts 3.
- Vector results (`tools/takeoff_eval.py`): Swiss bedroom sums 150/150 (dev), 91/91 (test) against
  Stoa's label-sum answers; every per-room area inside the sums within 2% except one Swiss kitchen on
  test; IFC type sums 56/56 dev, 18/18 test where Stoa asks about every room of the type.

Raster (`bpqa/routes/raster_area.py`): printed areas by OCR, room regions, pixel scale. Measured
against the printed areas, leave-one-out (`tools/area_eval.py`, 2,061 dev images with an upright
OCR pass, 197 plans with 3+ printed areas, 1,471 printed rooms):

| region method | rooms measured | within 2% | within 5% | median error |
|---|---|---|---|---|
| every dark line is a barrier (thickened 2 px) | 65 of 894 (first 1,267 images) | 10.8% | 18.5% | 17.2% |
| walls only, doorway bridging at 10 scale hypotheses | 203 | 10.8% | 27.6% | 15.3% |
| walls only, plus floor under counters and wardrobes along walls | 193 | 18.7% | 36.8% | 9.3% |

Morphology sizes tried (close 3-9 px, open 4-8 px): close 5 / open 4 kept the most rooms; larger
openings lose thin partitions. Gating by rectangularity and by 4+ agreeing scale rooms reached at best
57.6% within 5% on 33 rooms. That is not Stoa's 2%, so measured areas are not answered: a CubiCasa
takeoff uses printed areas only and abstains, with the measured estimate in the reason, when a room
has none. Test split, once, same settings (635 test images, 45 plans, 303 printed rooms): 42 measured, 4.8% within 2%, 21.4% within 5%, median error 12.3%.
## 2026-11-14 night: detector v2 (tiers, test-time augmentation, tiled detector), branch `detector-v2`

### Baseline: cubi2 on the full set's test split, before any change here

`cubi2` (yolo26n from scratch, 80 epochs, imgsz 1280, same data as `cubi1`) and its thresholds, tuned
by main's `tools/tune_detector.py` on the 624 held-out train-split images, run with main's code and
the same flags as the earlier full-set run: `eval.py --data ../data/stoa-full/junction-dataset --split
test --sources cubicasa5k --objects toilet,sink,shower_or_bath,door,window --no-checks` (1,510 images,
14,108 questions in all languages). The overall full-set test accuracy was not run: it needs OCR of every
test image for the label routes (several hours), so this baseline is detector questions only.

| object | n | exact (abstain = wrong) | coverage | precision |
|---|---|---|---|---|
| toilet | 3016 | 81.0% | 96.4% | 84.0% |
| sink | 2982 | 57.5% | 77.3% | 74.5% |
| door | 2274 | 23.2% | 30.4% | 76.3% |
| window | 3020 | 25.0% | 39.1% | 63.9% |
| shower/bath (switched off) | 2816 | 0% | 0% | - |
| **all detector questions** | 14108 | 38.6% | 50.2% | 76.8% |

For comparison, `cubi1` on the same questions: toilets 74.8%, sinks 38.8%, doors 24.9%, windows 18.7%.

### Tiers instead of abstaining

Every detector and label answer now comes with a tier (`tier`, `tier_reason`, `uncertain` in the answer
JSON). "proven" is exactly the set the route answered before (the tuned band, count range and
per-object switch, and no failed check); everything that used to be an abstention is now the route's
best count as an "estimate", with the reason and the boxes or labels to check by eye (amber on the
evidence image). The route abstains only when there is nothing to go on (no model, unreadable image).
A failed consistency check demotes the answer to an estimate instead of withdrawing it. The `--prior`
fallback is gone from the route; `eval.py --prior` reports the dev-majority answer per object as a
baseline column only. `eval.py --no-tiers` reproduces the old behaviour (checked: identical dev numbers
to the README, 64.8% overall, CubiCasa 45.3%, with cubi1).

Checks with an implied value: when a failed check implies a count (a "MH3" label, a summary listing K or
S, floor names, a stair label, bathrooms needing showers), that value replaces the route's count for the
seven checks where, on dev answers that failed the check, the implied value was right more often than
the route (`ADOPT` in `bpqa/checks.py`; dev, cubi1, questions failing each check: numbered-labels 8 vs
0 right, summary-kitchen 18 vs 0, summary-sauna 14 vs 0, utility-kitchen 2 vs 0, floor-names 4 vs 0,
stairs-label 8 vs 2, showers>=bathrooms 66 vs 8; not adopted: sinks 2 vs 10, toilets 4 vs 2,
summary-rooms 10 vs 8, doors 6 vs 6, windows 0 vs 0).

Full set, test split, detector questions, same flags as the baseline (14,108 questions, checks off):

| object | baseline exact | tiers exact | tiers + TTA exact | proven: share / precision | estimate precision |
|---|---|---|---|---|---|
| toilet | 81.0% | 82.5% | 84.9% | 96.8% / 86.1% | 49.0% |
| sink | 57.5% | 64.8% | 69.7% | 83.2% / 76.6% | 35.6% |
| door | 23.2% | 50.0% | 52.0% | 54.5% / 68.5% | 32.1% |
| window | 25.0% | 38.7% | 39.7% | 36.4% / 67.9% | 23.5% |
| shower/bath | 0% | 34.9% | 40.6% | 0% / - | 40.6% |
| **all** | 38.6% | 54.6% | 57.9% | 54.8% / 77.6% | 33.8% |

With tiers alone the proven tier is the old answered set exactly (7,084 answers, 76.8% precise); TTA
then re-tunes each object's policy, so its proven tier is larger (7,738) and slightly more precise.

Preview dev, cubi2 (5,289 questions; the detector's dev images are among its tuning images, so the
detector rows are slightly optimistic):

| | overall | CubiCasa | CubiCasa proven: n / precision | CubiCasa estimate: n / precision |
|---|---|---|---|---|
| before tiers (`--no-tiers`) | 66.2% | 47.9% | 1672 / 83.3% | - |
| tiers, implied values from checks | 76.0% | 65.7% | 1656 / 83.3% | 1252 / 42.5% |
| tiers, implied values, TTA | 76.6% | 66.7% | 1688 / 84.4% | 1220 / 42.3% |

On cubi1 (dev, CubiCasa) the steps were 45.3% -> 60.0% (tiers) -> 62.9% (implied values) -> 64.0% (TTA).

Where estimates are weak (dev, cubi2 + TTA): kitchens (104 estimates, 23% right) and bathrooms (66,
15%): no label or two labels read while the plan has one; the dev-majority baseline answers those
questions right 93% and 91% of the time, but it is a constant, not a reading of the plan, so it is not
used. Shower/bath counts are all estimates (48% right on dev, the constant gets 76%).

### Full set, test split, all CubiCasa questions with the checking layer (cubi2, before the tiled model)

The OCR cache for the 1,510 full-set test images was filled overnight, so the label routes and checks
could run on the full test split too (27,160 CubiCasa questions in all languages). "Before" is main's
behaviour: `--no-tiers` with main's cubi2 thresholds (on dev this reproduces main's numbers exactly).
The other sources are unchanged by this branch (12,647 questions, 12,032 right, 99.0% precise), so the
overall line adds them to both columns.

| object | n | before: exact | before: precision | after: exact | proven: n / precision | estimate: n / precision | dev-majority baseline |
|---|---|---|---|---|---|---|---|
| storey | 3020 | 89.4% | 92.2% | 90.5% | 2928 / 92.2% | 92 / 37.0% | 81.5% |
| balcony | 942 | 71.8% | 87.6% | 85.8% | 772 / 87.6% | 170 / 77.6% | 51.4% |
| WC room | 1450 | 72.3% | 82.0% | 81.2% | 1278 / 82.0% | 172 / 75.6% | 55.0% |
| bedroom | 1914 | 57.6% | 86.4% | 75.1% | 1276 / 86.4% | 638 / 52.7% | 31.5% |
| kitchen | 2090 | 55.3% | 98.8% | 64.0% | 1170 / 98.8% | 920 / 19.8% | 96.1% |
| bathroom | 1484 | 53.2% | 96.8% | 79.0% | 816 / 96.8% | 668 / 57.2% | 88.8% |
| sauna | 2152 | 19.7% | 81.9% | 63.6% | 518 / 81.9% | 1634 / 57.8% | 56.8% |
| toilet | 3016 | 80.3% | 85.0% | 84.9% | 2870 / 86.8% | 146 / 46.6% | 62.5% |
| sink | 2982 | 57.5% | 74.5% | 69.7% | 2394 / 77.9% | 588 / 36.1% | 52.8% |
| door | 2274 | 23.2% | 76.3% | 52.0% | 1150 / 68.5% | 1124 / 35.1% | 12.0% |
| window | 3020 | 24.9% | 64.2% | 39.7% | 1092 / 68.1% | 1928 / 23.5% | 12.9% |
| shower/bath | 2816 | 0% | - | 50.2% | 0 / - | 2816 / 50.2% | 77.3% |
| **CubiCasa** | 27160 | 49.0% | 84.4% | 68.0% | 16264 / 84.8% | 10896 / 43.0% | |
| **all sources** | 39807 | 63.7% | | 76.6% | | | |

One more check came out of the dev analysis: a shower or bathtub found by the detector means at least one
bathroom, so a zero bathroom count becomes an estimate of 1 (on dev, of the bathroom questions with no
label read, the 14 with a detected shower all had one bathroom). Bathrooms on dev 60.8% -> 79.7%, on
the full test split 53.2% -> 79.0% (the estimates are 57% right).

Still below the constant answer: kitchens (estimates 20% right: no label or two labels read while the plan
has one kitchen), bathrooms and showers/baths. Those are the places where the plan's own evidence is
missing or misread; the constant is not used because it does not read the plan.

### Test-time augmentation (`tta` per object in the thresholds file)

The detector runs on the eight rotations and mirrors of the image; boxes are mapped back and either
fused across views (a box found by fewer views is discounted) or the median per-view count is taken,
whichever is better per object on the tune images. The spread of per-view counts is one more reason for
"estimate". Tuned on the 624 held-out train-split images (2-fold cross-validated exact-count accuracy,
every image answered):

| object | cubi1 | cubi1 + TTA | cubi2 | cubi2 + TTA | TTA mode (cubi2) |
|---|---|---|---|---|---|
| toilet | 76.8% | 77.9% | 81.7% | 82.7% | median |
| sink | 58.8% | 63.4% | 65.5% | 70.2% | fuse |
| shower/bath | 28.8% | 35.9% | 36.2% | 41.5% | median |
| door | 42.2% | 45.8% | 48.8% | 51.9% | median |
| window | 37.7% | 39.3% | 41.7% | 43.8% | fuse |

TTA won on every object for both models, so the route uses it for all five (eight detector passes per
image instead of one; still $0; the full-set run took under 1 s per image on the RTX 5070 while it was also training).

### Tiled, pretrained, larger detector (`detector_tiles.pt`, run `tiles_s`)

Dataset `ds_tiles` (`tools/build_tile_dataset.py`): exactly the plans of `ds_cubi`'s train and val
lists, each checked again against the overlap lists (a plan not in `train_clean.txt` or matching a test
image is refused; none was). CubiCasa plans are re-read from the public `F1_original.png` at native
resolution (`ds_cubi` had capped the long side at 2,048 px), resized 2x (long side capped at 4,096 px)
and cut into 1,024 px tiles with 192 px overlap; edge tiles are padded white so YOLO never rescales a
tile. A box stays in a tile when at least 40% of it is inside. Empty tiles are kept 25% of the time when
they hold drawing. Half of the synthetic Swiss renders are tiled at their own scale. 17,596 training
tiles, 251 validation tiles. In native pixels the objects' long side is 9-20 px at the 10th percentile
and 17 px (showers) to 51 px (windows) at the median (300 sampled training plans); 2x doubles that.

Model: `yolo26s` from Ultralytics' official pretrained weights (`yolo26s.pt`, downloaded once), imgsz
1,024, batch 8, the same augmentation as before (flips, no colour jitter, mosaic 0.5, scale 0.3),
trained with a wall-clock budget (`--time`) from 01:35 to about 06:50, RTX 5070. At inference the image
gets the same resize and tiling, tiles go through the model in batches, and boxes found twice in the
overlap are merged (same class, IoU >= 0.5 or the smaller box 60% inside the larger).

Mid-training check (weights after epoch 22 of about 58, tune images, 2-fold cross-validated exact-count
accuracy, every image answered):

| object | cubi2 | cubi2 + TTA | tiles e22 | tiles e22 + TTA |
|---|---|---|---|---|
| toilet | 81.7% | 82.7% | 87.0% | 89.3% |
| sink | 65.5% | 70.2% | 79.8% | 81.8% |
| shower/bath | 36.2% | 41.5% | 72.8% | 74.5% |
| door | 48.8% | 51.9% | 54.6% | 57.3% |
| window | 41.7% | 43.8% | 45.8% | 48.6% |

The epoch-22 tiles + TTA policy on the full set's test split, detector questions, same flags as the
baseline: toilets 89.3%, sinks 78.1%, showers/baths 74.1%, doors 54.1%, windows 46.6%; all 69.1%
(baseline 38.6%, cubi2 + tiers + TTA 57.9%). Close to what the tune images predicted, so the tune-image
comparison transfers. Showers and baths get a proven tier for the first time (the policy is right more
often than wrong).

Final model: 61 epochs in the 5.3 h budget (best epoch 60; tile validation mAP50 0.943, mAP50-95 0.817;
cubi2 on its whole-image validation set: 0.892 and 0.676, not directly comparable). Tuned on the same 624 held-out train-split images as
cubi2. 2-fold cross-validated exact-count accuracy (every image answered), and the proven tier's share
and precision:

| object | cubi2 | cubi2 + TTA | tiles | tiles + TTA | kept | proven share / precision (kept) | estimate precision (kept) |
|---|---|---|---|---|---|---|---|
| toilet | 81.7% | 82.7% | 87.5% | **88.3%** | tiles + TTA | 97.9% / 88.9% | 61.5% |
| sink | 65.5% | 70.2% | 78.2% | **79.3%** | tiles + TTA | 93.0% / 82.0% | 44.2% |
| shower/bath | 36.2% | 41.5% | 72.4% | **77.6%** | tiles + TTA | 98.3% / 78.1% | 50.0% |
| door | 48.8% | 51.9% | **59.1%** | 58.2% | tiles | 68.1% / 71.0% | 33.8% |
| window | 41.7% | 43.8% | 44.7% | **50.0%** | tiles + TTA | 50.0% / 71.2% | 28.8% |

The tiled model wins every object, so the route now uses `detector_tiles.pt` for all five (with TTA for
four of them). `models/detector_thresholds.json` names the model and mode per object.

Preview, cubi2 before this branch (`--no-tiers`, main's thresholds) against the final route:

| | dev overall | dev CubiCasa | test overall | test CubiCasa | test proven: n / precision | test estimate: n / precision |
|---|---|---|---|---|---|---|
| before | 66.2% | 47.9% | 72.3% | 49.2% | 2056 / 95.0% (all answers) | - |
| final | 80.5% | 73.9% | 85.7% | 75.6% | 2292 / 94.1% | 354 / 44.6% |

Preview test per CubiCasa object, before -> final (exact): toilets 85.5% -> 92.1%, sinks 50.0% -> 74.3%,
showers/baths 0% -> 83.3%, doors 18.3% -> 60.0%, windows 31.6% -> 44.7%, bathrooms 43.2% -> 86.5%,
bedrooms 54.9% -> 74.5%, saunas 22.2% -> 63.0%, WC rooms 75.7% -> 91.9%, balconies 76.2% -> 100%,
kitchens 57.4% -> 59.6%, storeys 93.4% -> 96.1%. The preview test has 76 CubiCasa images, so these move
in steps of 1.3 points.

Full set, test split, detector questions, final route, same flags as the baseline (run once):

| object | n | before: exact (precision, coverage) | final: exact | final proven: share / precision | final estimate precision |
|---|---|---|---|---|---|
| toilet | 3016 | 81.0% (84.0%, 96.4%) | 88.3% | 99.7% / 88.5% | 0 of 8 |
| sink | 2982 | 57.5% (74.5%, 77.3%) | 78.7% | 98.2% / 79.6% | 29.6% |
| shower/bath | 2816 | 0% (switched off) | 75.7% | 95.5% / 77.4% | 39.7% |
| door | 2274 | 23.2% (76.3%, 30.4%) | 55.4% | 69.5% / 67.1% | 28.8% |
| window | 3020 | 25.0% (63.9%, 39.1%) | 46.4% | 44.8% / 69.0% | 28.0% |
| **all detector questions** | 14108 | 38.6% (76.8%, 50.2%) | 69.5% | 81.9% / 78.4% | 28.7% |

The tune images predicted 88.3%, 79.3%, 77.6%, 59.1% and 50.0%; doors and windows came out 3-4 points
lower on test. The epoch-22 check had given 69.1% overall on the same questions.

Full set, test split, all CubiCasa questions with the checking layer, final route (27,160 questions):
73.5% (before this branch 49.0%, cubi2 + tiers + TTA 68.0%); proven 19,884 answers at 84.1%, estimates
7,276 at 44.6%. Changes against the cubi2 + TTA column above: toilets 88.3%, sinks 78.7%, showers/baths
76.0%, doors 55.4%, windows 46.4%, and bathrooms 87.9% (the shower check sees more showers). With the
unchanged other sources: **all 39,807 full-set test questions 80.4% (before 63.7%)**.
## 2026-11-14: Nemotron baseline and fallback route, branch `nemotron`

Model: `nvidia/nemotron-3.5-super-vl-120b-a12b` (NVIDIA Build, free tier). Client `bpqa/nemotron.py` (disk
cache keyed by model, prompt, image hash; throttled; retry with backoff; key read from `.env`, never
logged). Everything below is reproducible from the cache in `out/nemo_cache/`.

### 1. Naive AI baseline vs our router (preview TEST split, 150 questions)

Sample (`tools/nemo_baseline.py`, seed 2026, same split function as `eval.py`): CubiCasa counts 50, Swiss counts
40, Swiss areas 30, IFC room areas via the rendered plan PDF 30 (one model, one drawing: Esplanades); parsed
object types round-robined, at most 3 questions per drawing (52 drawings). Whole-model IFC questions
("in the model ...") were left out: no single image can answer them. Nemotron gets the drawing as one image
(PDF page 1 at 150 dpi, long edge 1600 px) and the question with "Reply with just the number, no units and no
explanation". The reply is parsed for a number; no number (or a reply cut off by the token limit) counts as
wrong. Scoring is `eval.score` (counts exact, areas within 2%). Reasoning models spend most of their output on
reasoning: with 4096 max tokens 78 of 150 replies were cut off, so those were asked again with 16384; 35 still
ended without an answer. Only the final call's tokens and latency are counted.

| source | type | n | Nemotron correct | Nemotron answered | ours (router) correct | ours answered | ours, PDF only | tokens in / out per q | latency median / mean | $ per q, measured tokens | $ per q, direct answer |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cubicasa5k | count | 50 | 33 (66.0%) | 46 | 23 (46.0%) | 27 | - | 922 / 3273 | 10s / 25s | $0.052 | $0.0029 |
| swiss_dwellings | count | 40 | 20 (50.0%) | 36 | 40 (100.0%) | 40 | 40/40 | 1858 / 4555 | 14s / 34s | $0.074 | $0.0057 |
| swiss_dwellings | area | 30 | 5 (16.7%) | 16 | 30 (100.0%) | 30 | 30/30 | 1880 / 11012 | 102s / 77s | $0.171 | $0.0058 |
| ifc_models | area | 30 | 1 (3.3%) | 17 | 30 (100.0%) | 30 | 30/30 | 2087 / 9542 | 61s / 73s | $0.149 | $0.0064 |
| **all** | | 150 | **59 (39.3%)** | 115 | **123 (82.0%)** | 127 | 100/100 | 1596 / 6417 | 25s / 48s | $0.101 | $0.0049 |

"Ours (router)" is the shipped router with DXF/IFC twins available, Nemotron fallback off, no `--prior`; it costs
$0 per question and takes seconds (CubiCasa OCR is the slow part). "Ours, PDF only" is `--pdf-only` on the
100 PDF questions, i.e. from the PDF alone, the closest to what Nemotron saw (CubiCasa images have no PDF
form). Precision when Nemotron answers: CubiCasa 71.7%, Swiss 48.1%, IFC 5.9%. Nemotron's per-object result
on CubiCasa (n = 4 to 5 each, so only a rough guide): storeys 4/4, toilets 4/4, bathrooms 5/5, kitchens 3/4,
saunas 3/4, bedrooms 3/4, sinks 3/4, WC rooms 2/4, windows 2/4, showers/baths 2/4, balconies 2/5, doors 0/4;
ours: storeys 4/4, WC rooms 4/4, kitchens 3/4, bathrooms 3/5, balconies 3/5, toilets 3/4, bedrooms 2/4,
sinks 1/4, doors, windows, saunas and showers 0/4 (mostly abstentions). On Swiss floors Nemotron is good at
apartments, rooms by label and bathtubs (5/5 each) and poor at doors (0/5), windows (1/5), washbasins
(1/5), toilets (2/5) and areas.

Cost on a paid frontier API at list price. ASSUMPTION: 3 USD per million input tokens and 15 USD per
million output tokens (a mid-priced frontier model; not a quote, check the current price list, and image
tokenisation differs between vendors). "Measured tokens" prices Nemotron's own token counts, reasoning
included (6417 output tokens per question, so about $0.10 per question, $15 per 150 questions). "Direct
answer" prices the same input tokens and 10 output tokens, which is what a non-reasoning model answering with a
bare number would cost (about $0.005 per question). Our router: $0.000.

What it shows: on this sample the plain vision model gets 39% where the router gets 82% (100% on everything
that has a vector or model source), costs far more per question than our $0, and is slow when it reasons (a
median of 25 s, up to minutes on dense floors). On CubiCasa scans, where our router abstains on half the
questions, the plain model is ahead (66% vs 46% before any prior). The fallback below targets exactly that
gap, with checks the plain prompt lacks. Full records: `out/baseline_nemotron.json` (committed with
`git add -f`, `out/` is otherwise ignored).

### 2. Nemotron fallback route (`bpqa/routes/nemotron.py`) and parse fallback

Route `nemotron-estimate`: used only when every other route abstained (or a consistency check withdrew a raster
answer). JSON with value, regions (bounding boxes in image pixels) and a one-line reading; `validate()`
checks it (finite non-negative value, integer counts, areas within 0.5 to 5000 m2, boxes inside the image, one
region per counted object and at most 40, at least one region for a measurement) and abstains on anything else;
regions are drawn as numbered boxes; answer `tier: "estimate"`, confidence 0.3. Whole-model, compound and
unresolved-scope questions are never sent. Switch: `NEMO_FALLBACK=0` or `eval.py --no-nemo`; default on, but
only for the fallback cases. The router also uses `NemotronParser` when the rule parser returns no query.

Measured on the preview DEV questions that abstain (`tools/nemo_fallback_eval.py`; test not used). The
router without Nemotron answers 3732 of 5289 dev questions and abstains on 1557; 1327 of those are eligible
for the route (numeric, one drawing, an image or PDF), 1300 of them CubiCasa. A seeded sample of 200 eligible
questions, stratified by source and parsed object (round-robin, so the strata are not proportional), went
through the full router:

| | sample (200) | weighted to the 1327 eligible |
|---|---|---|
| valid estimate returned | 169 (84.5%) | about 1145 |
| correct (counts exact, areas within 2%) | 86 (43.0% of the sample, 50.9% of the answered) | about 640 (48%, 95% interval about 41% to 56%) |
| invalid output or no answer, abstained | 31 | about 180 |

By object (CubiCasa part of the sample; n, valid, correct):

| object | n | valid | correct |
|---|---|---|---|
| balcony | 15 | 13 | 3 |
| bathroom | 15 | 13 | 11 |
| bedroom | 15 | 11 | 9 |
| door | 15 | 13 | 2 |
| kitchen | 15 | 13 | 11 |
| sauna | 15 | 13 | 12 |
| shower_or_bath | 15 | 14 | 9 |
| sink | 14 | 13 | 8 |
| storey | 12 | 9 | 7 |
| toilet | 14 | 9 | 7 |
| wc_room | 14 | 12 | 3 |
| window | 14 | 11 | 1 |

Effect on the whole dev split, estimated from the sample: about 640 more correct answers, dev accuracy from
64.8% to about 77%, and precision of answered questions from about 92% to about 83%. The fallback buys coverage
and accuracy by answering questions the other routes could not prove, with answers that are right about half
the time and are labelled as estimates. The strong cases are room-label questions (bathrooms 11/13,
kitchens 11/13, saunas 12/13, bedrooms 9/11); doors (2/13), windows (1/11), WC rooms (3/12) and balconies (3/13)
are mostly wrong and a caller should not rely on them. Invalid outputs were mainly replies with no JSON (the
reasoning used the whole budget) and a few boxes outside the image. The boxes were not scored: the evidence
images show that boxes often sit near the right objects while the count is off (`out/nemo_fallback/evidence/`).
Mean estimated cost on the sample: $0.074 per question at the assumed prices.

Question-parse fallback (`tools/nemo_parse_eval.py`, no reasoning, about 2 s per call): (A) on 80 dev questions
the rule parser handles, in all languages, the typed query matches the rule parser's on 74 (92.5%); before
normalising to the rule parser's conventions (a named room is obj "room" with the name in `label`, no variant
on a named room, scope follows level) it was 64 of 80. (B) On 14 hand-written phrasings and languages the
rules do not cover (Spanish, Swedish, Dutch, Portuguese, Finnish, "loos", "glazed openings", ...) the rule parser
returns a query for 2 (1 correct); the fallback maps all 14 correctly. Those 14 were written by us and one
of them guided the normalisation, so it is a smoke test, not a benchmark. (C) The 130 preview
questions the rules leave unparsed are free-form IFC-Bench text questions about IFC files; the fallback maps 109
to a valid query, but there is no image or route behind them, so nothing changes in the answers.

Not done: the boxes are not verified (no second pass that checks each box with the model or a detector), the
reasoning latency (a median of 60 to 100 s on dense floors) is too slow for interactive use, and 23% of
baseline replies still ran out of tokens at 16384.

## 2026-11-14: pdf-vector route (answers from the PDF alone), branch `pdf-route`

Problem: Swiss questions were answered from the DXF twin of each PDF. A judge may hand over only the
PDF. New route `pdf-vector` (`bpqa/pdfvec.py`, `bpqa/routes/pdf_vector.py`), used when a PDF has no
DXF/IFC twin, and everywhere with `eval.py --pdf-only`.

Developed on dev floors against the DXF twin (`tools/pdf_vs_dxf.py`: share of dev floors where the PDF
count equals the DXF count; the sample grew from 60 to 120 to 200 floors as the rules settled). Steps
that moved it:

| step | doors | windows | toilets | washbasins | bathtubs | showers | room areas within 2% |
|---|---|---|---|---|---|---|---|
| first pass (60 floors) | 86.7% | 71.7% | 96.7% | 86.7% | 83.3% | 86.7% | 1738/1794 |
| room fills must be light (wall hatch tiles had won the room-style vote) (60) | 91.7% | 85.0% | 98.3% | 90.0% | 86.7% | 90.0% | 1838/1838 |
| windows: frames outside room shapes, square and thick-wall frames allowed (60) | | 100% | | | | | |
| doors: style chosen from thin quads at swing arcs, then all quads of that style (60) | 100% | | | | | | |
| fixtures measured in their rotated box; tub, basin size gates from the data; trays need a drain circle (120) | | | 99.2% | 98.3% | 100% | 97.5% | 3391/3391 |
| fixture line style from basins and tubs (a round column in wall style was a "toilet") (200) | 99.5% | 100% | 100% | 99.0% | 100% | 98.5% | 5760/5760 |

Other changes: room shapes keep holes (a balcony round the building is a ring) for containment, while
the area is the drawn outline, which is what the dataset's room areas follow; unnamed numbered rooms
count as indoor if any shape in the fixture line style lies inside (kitchen counters, as FIXTURE
polylines do in the DXF route); IFC-style labels "NAME (number)" match name and number in the same
shape; scale-bar tick labels are taken as centred on their ticks (IfcConvert prints "5 m" centred,
which gave a 2.5% scale conflict before).

Scale was set on every vector PDF: Swiss by scale bar + printed ratio (plus room-area labels where the
sheet prints them), IFC plan PDFs by scale bar + printed ratio. No Swiss or IFC PDF needed the
dimension-string method; it is implemented but untested on real dimension chains.

Result, `eval.py --pdf-only` (dev, then test once): all PDF questions 94.4% / 97.1% accuracy,
98.7% / 98.8% precision; Swiss 98.6% / 98.8% (DXF route on dev Swiss: 98.9%). Table in README.

## 2026-11-13 night: checking layer, overlap check, detector pipeline

### Checking layer (`bpqa/checks.py`)

Before a raster answer leaves the router, the facts every raster route can read off the same image
are gathered once (room labels per type, numbered labels, the apartment summary such as "3H+K+S",
floor names, stair labels, panel count) and every check that relates the asked object to them runs.
A failed check either abstains or lowers confidence; which one was set per check on dev
(`tools/check_eval.py`, which also reports how often the gold answer itself passes the check).
Fired checks go into the answer JSON (`checks`) and into the evidence header, green PASS or red FAIL;
an answer withdrawn by a check still gets its evidence image, headed "ABSTAINED: would answer N".

| check | rule | dev: failing answers that were correct | gold passes |
|---|---|---|---|
| numbered-labels | a label "MH3" or "WC2" means at least 3 or 2 | 0 of 4 | 8/8 |
| summary-rooms | "nH+K" means n-1 bedrooms | 2 of 6 | 20/24 |
| summary-kitchen | a summary listing K, KK, KT, TUPAK means a kitchen | (none failed) | 30/30 |
| summary-sauna | a summary listing S means a sauna | (none failed) | 14/14 |
| utility-kitchen | only an APUK label read means the main kitchen was missed | 0 of 1 | 1/1 |
| floor-names | two or more floor names (1. KRS, 2. KRS, ALAKERTA ...) bound the storey count | 0 of 2 | 3/3 |
| stairs-label | PORRAS, YLOS, ALAS on a one-panel plan means a second storey | 1 of 5 | 4/5 |
| toilets>=wc-rooms | each WC room label holds a WC seat | (for the detector) | 49/50 |
| sinks>=bathrooms+kitchen | each bathroom and the kitchen have a sink | (for the detector) | 128/135 |
| showers>=bathrooms | each bathroom has a shower or bath | (for the detector) | 75/82 |
| doors>=rooms-1 | n room labels need at least n-1 doors | (for the detector) | 88/94 |
| windows>=bedrooms+1 | each bedroom and the living room have a window | (for the detector) | 110/110 |

Tried and dropped: "a summary without S means no sauna" (all 3 answers it failed were correct:
summaries often leave S out) and "a second panel needs room labels" (gold passed it 74% of the time).

Result on CubiCasa (all actions abstain):

| | accuracy | coverage | precision |
|---|---|---|---|
| dev, before | 28.7% | 32.2% | 89.3% |
| dev, with checks | 28.5% | 31.1% | 91.8% |
| test, before | 29.4% | 31.4% | 93.5% |
| test, with checks | 29.1% | 30.4% | 95.7% |

On test the layer withdrew 14 answers, 10 of them wrong. Overall test accuracy 62.3% -> 62.2%,
precision 98.4% -> 98.9%. With `--prior` the fixture-bound checks also act on the prior's answers
(test: 61.2% -> 60.6% accuracy, precision 61.2% -> 64.8%).

### Overlap between Stoa's data and public CubiCasa5K (`tools/overlap.py`)

Every raster image is cropped to its content and hashed with pHash and dHash at 8x8 and 16x16, as is
and mirrored, and also uncropped (the crop is unstable on a few plans with light-grey fills). A pair
matches when the 64-bit pHash passes a loose prefilter, both 256-bit hashes are within 60 bits and the
aspect ratios agree. On the preview test (`tools/test_overlap.py`: half of the preview CubiCasa images
on the Stoa side, every image on the public side resized to 35-90%, JPEG-recompressed, some with a
margin, some mirrored) it finds 120 of 120 overlaps with no false match; true pairs are at most 50
bits apart, the nearest wrong pair 82.

Full run (Stoa full set, 12,893 raster images; CubiCasa5K, 5,000 plans, 12,342 images):

| | |
|---|---|
| Stoa CubiCasa images | 4,787 (3,277 in our train split, 1,510 in test) |
| Stoa CubiCasa images matched to a public plan | 4,787 (all) |
| public plans | 5,000 |
| public plans matching a Stoa train-split image only | 3,245 |
| public plans matching a Stoa test-split image | 1,544 |
| public plans not in Stoa | 211 |
| clean training list (train-only + not in Stoa) | 3,456 |

1,544 is more than 1,510 because 336 matched pairs join different IDs (for example public
colorful/11191 and Stoa colorful-11195): near-identical layouts, likely the same building. A public
plan that matches any test image is excluded, whatever else it matches.

Stoa's CubiCasa counts are the public annotations: with the class mapping in
`tools/cubicasa_svg.py`, the converter's counts equal Stoa's train-split answers on 99.6% (windows) to
100% (sinks, showers/baths) of about 3,300 images per type. ShowerCab, ShowerScreen and
ShowerPlatform are not counted as showers by Stoa.

### Detector pipeline

- `tools/cubicasa_svg.py` converts a CubiCasa5K `model.svg` into boxes for toilet, sink, shower,
  bathtub, door, window (transforms composed down the tree; unit test on a hand-made SVG). The SVG
  coordinates are in the F1_scaled.png frame; the root width/height attributes are not the image size.
- `tools/build_detector_dataset.py` uses only `train_clean.txt`, refuses any plan matching a test image,
  trains on F1_original.png (the image Stoa ships) with boxes scaled from the F1_scaled frame, and holds
  617 train-split plans (624 Stoa images, every preview dev image among them) out of training for
  threshold tuning. Training set: 2,820 CubiCasa plans + 1,376 synthetic renders; validation 19 + 72.
- `tools/render_dxf_synth.py` renders the 724 train-split Swiss DXF floors twice each (1,448 images,
  about 89,600 boxes) with boxes from the DXF route's rules. The 5-vertex DOOR polygon is the opening in
  the wall, the same thing a CubiCasa door box marks.
- `bpqa/routes/detector.py` (`raster-detector`): counts boxes of the asked classes at or above a tuned
  score; abstains when any box scores within a tuned band of the threshold; draws every counted box.
  `tools/tune_detector.py` picks threshold and band per object on held-out train-split images, reports a
  2-fold cross-validated estimate, and switches an object off when its best policy is wrong more often
  than right. Ultralytics runs offline (`bpqa/yolo_env.py`: no telemetry, no auto-install); it still
  fetched its plotting font once at the first training start.

Smoke test, synthetic only (yolo26n from scratch, 15 epochs, imgsz 1024; synthetic val mAP50 0.40).
Exact-count accuracy on the 165 preview dev images, 2-fold cross-validated, no abstention: toilets
18.3%, sinks 7.9%, showers/baths 23.2%, doors 8.6%, windows 10.9%. A constant "1 toilet" gets 65%. The
domain gap is large (clean vector lines vs scanned, hatched, annotated plans), so the tuner switched
all five objects off for this model.

First real run `cubi1`: yolo26n from scratch, 40 epochs, imgsz 1280, batch 8, 2,820 CubiCasa plans +
1,376 synthetic, RTX 5070, about 50 minutes. Validation mAP50 0.836, mAP50-95 0.59 (best epoch, 38).
Tuned on the 624 held-out train-split images (2-fold cross-validated exact-count accuracy without
abstention, then with the tuned band): toilets 76.8% / 74.4% at 93.8% coverage; sinks 58.8% / 37.3% at
50.3%; doors 42.2% / 10.6% at 17.1%; windows 37.7% / 16.2% at 25.2%; showers and baths 24.3%, switched
off. An abstention rule with a tuned number of near-threshold boxes (k) replaced "any box in the band".

On detector answers (dev), the toilets >= WC rooms check removed 7 answers of which 6 were wrong, so it
stays "abstain"; the sinks and doors bounds removed about as many right answers as wrong ones and now
only lower the confidence.

| CubiCasa | accuracy | coverage | precision |
|---|---|---|---|
| dev, labels only (before the detector) | 28.5% | 31.1% | 91.8% |
| dev, with detector | 45.3% | 55.3% | 81.8% |
| test, labels only | 29.1% | 30.4% | 95.7% |
| test, with detector, checks off | 46.0% | 54.5% | 84.4% |
| test, with detector and checks | 45.5% | 52.6% | 86.6% |
| test, with detector, checks and --prior | 66.1% | 97.2% | 68.0% |

Overall test: 62.2% -> 70.5% accuracy (95.2% precision); with `--prior` 78.4% -> 80.9%. Full-set
test split, detector questions only (1,510 images, 14,108 questions, checks off): toilets 74.8% exact,
sinks 38.8%, doors 24.9%, windows 18.7%.

## 2026-11-13 evening: CubiCasa raster counts

Starting point (cubicasa5k, dev): 10.0% accuracy, 11.3% coverage; only the storey count answered.

1. Parser fix: the bathroom question ends with "not counting separate WC rooms" and was parsed as a WC
   room question. Bathroom is now matched first. (No accuracy change on its own; it fixed the labels
   the room-label work was tuned against.)
2. Room labels read with a local OCR model (RapidOCR, ONNX on CPU, installed from PyPI with its models
   bundled). Labels are matched against Finnish abbreviations (MH, K, KK, KEITTIO, S, SAUNA, LH, WC, KH,
   KPH, PH, PESUH, SH, PARVEKE) plus a few Swedish ones (SOVRUM, KOK, BASTU, BADRUM). Each matched label
   is boxed and numbered on the image; other room labels that were read are boxed thin grey so a zero
   can be checked. First pass, all answered: bedroom 65%, kitchen 49%, WC 41%, sauna 71%, balcony 87%
   exact on the dev images cached at that point.
3. OCR in four orientations at 2x scale. The OCR's own 180-degree classifier misses short labels
   (an upside-down "MH" reads "HW"), and many labels run vertically. Overlapping reads are resolved by
   preferring a known room abbreviation, then confidence.
4. Mirrored plans. Several scans are left-right mirrored ("MH" reads "HM", "ET" reads "T3"). OCR also
   runs on the mirrored image; per image, the mirrored reads are used only when they find clearly more
   plan vocabulary (more than 2x + 2) and also more overall read confidence. The first, looser rule
   (1.5x) flipped a normal plan because mirrored garbage spelled "SH+KL".
5. Token hygiene: apartment-type summaries ("3H+K+S", "1H+KK 32.5 m2") are not room labels; tokens mixing
   codes and digits ("TM5ES2,S") are dropped; the same label read twice side by side counts once.
6. Per-type answer policy, set from the dev confusion of predicted vs gold count:
   kitchen, bathroom and sauna are answered only when exactly one label is read (dev: kitchen 1 -> 92%
   correct, 0 -> 6%, 2+ -> 0%; most 2+ cases are a neighbouring flat's labels or a stray "K"/"S");
   bedroom only when at least one MH is read (0 -> 55%); WC rooms always (87%); balconies 0 or 1.
   A zero answer needs at least three readable room labels on the plan.
   Result, cubicasa dev: 10.0% -> 28.7% accuracy, coverage 11.3% -> 32.2%, precision 89.3%.

Tried and not shipped:

- Fixture counts (toilets, sinks, showers/baths) from closed outlines. Candidates are enclosed white
  regions (the inside of a drawn toilet bowl, basin or tub); each gets HOG features of a tight and a wide
  crop plus scale-free shape numbers, and a small MLP is trained from dev image-level counts only
  (multiple-instance counting, no box labels). 5-fold cross-validation on dev images:
  sum-of-scores loss: toilet 6%, sink 6%, shower/bath 5% exact;
  self-training top-k loss: toilet 31%, sink 19%, shower/bath 27% exact, and only 4-25% precise when
  restricted to images with no uncertain candidate. A constant "1 toilet" is right 65% of the time.
  A sheet of the top-scored candidates shows why: whole small rooms, text and hob symbols outrank the
  toilet bowls. Image counts from 165 plans are too weak a signal for this; it needs box-level labels.
  The route code is kept and abstains while no model file exists (`models/` is not committed).
- Hand-labelling fixture candidates on contact sheets (`tools/make_sheets.py`). At the scan resolution
  most toilets and basins are 15-30 px across; labelling 871 crops by eye was slow and uncertain,
  so it was stopped before training.

Test (run once, at the end): cubicasa 10.6% -> 29.4% accuracy, coverage 31.4%, precision 93.5%.
