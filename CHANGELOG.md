# CHANGELOG

What moved the numbers. Dev numbers are measured with `eval.py --split dev`; test was run only at the
end of a session. Accuracy counts an abstention as wrong; precision is correct / answered.

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
