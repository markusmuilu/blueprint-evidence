# CHANGELOG

What moved the numbers. Dev numbers are measured with `eval.py --split dev`; test was run only at the
end of a session. Accuracy counts an abstention as wrong; precision is correct / answered.

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
