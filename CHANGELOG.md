# CHANGELOG

What moved the numbers. Dev numbers are measured with `eval.py --split dev`; test was run only at the
end of a session. Accuracy counts an abstention as wrong; precision is correct / answered.

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
