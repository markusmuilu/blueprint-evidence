# Unseen drawings: how the system does on drawings that are not in Stoa's dataset

Question this answers: Stoa's co-founder said judging will not reward hard-coding. The routes were
developed on Stoa's drawings, so what happens on public drawings Stoa never shipped?

Everything here is measured by `ask.py` (one subprocess per question, CPU only), against reference answers
worked out separately from the files. Numbers are exact counts, nothing is rounded up. No solver code under
`bpqa/` was changed for this test.

## Short version

- 12 public drawings, 50 distinct questions (53 `ask.py` calls: three DXF questions were asked on the
  DXF and again on a PDF rendered from it). **21 of 50 correct (42.0%)**, 6 wrong, 21 abstained,
  and 2 abstentions on questions that have no derivable answer (counted separately, 23 of 50 = 46.0% if
  those two count as handled correctly). When the system answered it was right 21 of 27 times (77.8%).
- IFC generalises: 18 of 23 correct on five models Stoa does not contain, both IFC4 and IFC2x3. The five
  misses are two parser problems, not route problems (see below), and a diagnostic run fixes all of the
  ones it re-tested.
- The DXF route does not generalise at all: 0 of 3, and all three are confident wrong zeros (confidence
  0.95, tier "proven") because it looks for Swiss layer names.
- PDF linework (`pdf-vector`) answered nothing on the three generic plan PDFs (12 questions): 0 correct,
  10 abstentions and 2 correct abstentions (areas of drawings with no scale). It fails safe.
- Raster: 3 of 12 correct (two storey counts, one toilet count by the detector), 1 wrong (balconies, a
  zero), 8 abstained. The room-label route reads only Finnish and Swedish words, so English labels
  ("Bedroom", "Kitchen") are not recognised even though the OCR reads them.

## The drawings

All downloaded as plain files into `data/unseen/<drawing>/` (outside the repository), each with a
`SOURCE.md` (URL, licence, author, sha256), copied into `unseen/SOURCES.md`. None was executed or imported
as code. Not in Stoa's dataset: SHA-256 of every file compared with every file under
`data/stoa-full/junction-dataset/data` (no match), filenames compared (no match), and for the six images and
PDFs a 256-bit difference hash against 11,353 Stoa images in wikimedia, habs, finna, europeana and cubicasa5k
(nearest Hamming distance 34 of 256, `tools/unseen_dedupe.py`; the two nearest, looked at by eye, are different drawings).

| drawing | kind | source | licence |
|---|---|---|---|
| bsmart_building_arch_ifc4 | IFC4, tiny house (2 spaces, 4 walls, no doors) | buildingSMART Sample-Test-Files, Simple-Scene | CC BY 4.0 |
| bsmart_building_arch_ifc2x3 | the same model exported as IFC2x3 | same | CC BY 4.0 |
| bsmart_wall_with_window | IFC4, one wall and one window | same, ISO reference view | CC BY 4.0 |
| ifcopenhouse_ifc4 | IFC4, small house (1 door, 5 windows) | web-ifc test fixtures (IfcOpenShell example) | file-level licence not stated; the example comes from the LGPL-3.0 IfcOpenShell project. The weakest licence in the set |
| schependomlaan_design_ifc | IFC2x3, ArchiCAD, 6 storeys, 100 spaces, 205 doors, 259 windows | buildingsmart-community Community-Sample-Test-Files | CC BY 4.0 |
| kjdraw_architecture_floor_plan | DXF (and a PDF rendered from it), office floor, SYNTHETIC sample | KanJieTeam/kjdraw docs | Apache-2.0 |
| wm_fraser_residence | vector PDF, house plan, no scale | Wikimedia Commons | CC BY-SA 3.0 |
| wm_747_villa | vector PDF, house plan, no scale | Wikimedia Commons | CC BY-SA 3.0 |
| wm_gottlieb_ground | PDF holding one image, hand sketch | Wikimedia Commons | CC BY-SA 3.0 |
| wm_milam_1st_floor | PNG, low-resolution 1960s plan | Wikimedia Commons | public domain |
| wm_tyneside_flat | PNG, two flat plans side by side, clean | Wikimedia Commons | CC BY-SA 4.0 |
| wm_ballymun_flat | PNG, noisy 1965 scan, two flats | Wikimedia Commons | public domain |

Limits of the set, stated plainly: the real-world coverage is thin. The DXF is a synthetic sample from a CAD
tool's documentation (its title block says SYNTHETIC), not an architect's drawing; I found no openly licensed
real-world floor-plan DXF. The two other DXFs in that repository (a site plan and a concept diagram with no
doors) were dropped as not being building floor plans. Two of the Commons PDFs come from the same uploader
as Stoa's own `wikimedia` images (same drafting style, different plans); Stoa has no questions on `wikimedia`.

## Questions and reference answers

`data/unseen/questions.jsonl` (copy: `unseen/questions.jsonl`), same fields as Stoa's (`path`, `question`,
`answer`, `unit`, `type`) plus `ref_method`, `drawing`, `manual_check`, `expected`. Phrasings copy Stoa's
templates (IFC: "How many doors are there in the model 'X'?"; Swiss: "How many rooms labelled 'X' are there on
this floor?"; CubiCasa: "How many bedrooms are on this floor plan?"; takeoff: "How many square meters of tiling
should I order for the bathrooms?"). `tools/unseen_build_questions.py` builds the file.

- IFC answers: computed from the model with ifcopenshell (`by_type` counts, storey containment,
  `NetFloorArea`, wall `Length`, mesh footprint where there is no quantity set), independent of `bpqa/`.
  Same library as the router, so a shared misreading of IFC would not show up here.
- DXF, PDF and image answers: counted by eye on rendered images, flagged "manual, check" in the tables.
  One person, not independently checked. The DXF counts were also read off its layers (12 arcs on A-DOOR,
  12 lines on A-WINDOW).
- Two area questions on scale-less PDFs have no derivable answer; the right outcome is to abstain
  (`expected: abstain`).
- The "tier" column is derived by me from the route and confidence by the rule in the README (structured
  routes at confidence 0.8 or more are "proven", pixel routes "estimate", abstentions "cannot prove");
  `ask.py` itself prints route and confidence only.
- Scoring: counts exact, areas and lengths within 2%, as Stoa does.

## Results

Environment: Windows, Python 3.11, requirements.txt in a fresh venv, CPU torch 2.15.0 and ultralytics 8.4.212
installed so that the raster detector runs (weights `models/detector.pt` copied from the app worktree,
sha256 starts 452b68cd5eb5c11d). Time per call 1.3 to 17.8 s (IFC 1.3 to 9.4 s; the 48 MB Schependomlaan
model 4.1 to 9.4 s; raster calls with OCR up to 17.8 s). Cost 0 USD, no model API calls.

### Totals, distinct questions

The 3 DXF questions also asked on the rendered PDF (3 wrong answers by the DXF twin, 3 abstentions with `--pdf-only`) are left out here and appear in the per-drawing tables.

| class | questions | correct | wrong | abstained | abstained, no answer derivable |
|---|---|---|---|---|---|
| ifc | 23 | 18 | 2 | 3 | 0 |
| dxf | 3 | 0 | 3 | 0 | 0 |
| pdf (no twin) | 12 | 0 | 0 | 10 | 2 |
| image | 12 | 3 | 1 | 8 | 0 |
| **all** | 50 | 21 | 6 | 21 | 2 |

answered 27, correct 21 (precision 21/27 = 77.8%); correct of all 50: 42.0%

## Per drawing

Result: correct, wrong, abstain (no answer although one exists) or abstain-ok (no answer derivable). `s` is wall-clock seconds.

### bsmart_building_arch_ifc4

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many rooms (spaces) are there in the model 'ifc silly sample scene - project'? | 2 | 2 | correct | ifc | proven (0.9) | 4.0 |  |
| 2 | How many walls are there in the model 'ifc silly sample scene - project'? | 4 | 4 | correct | ifc | proven (0.9) | 1.4 |  |
| 3 | How many doors are there in the model 'ifc silly sample scene - project'? | 0 | 0 | correct | ifc | proven (0.9) | 1.4 |  |
| 4 | What is the total length of all walls on '00 groundfloor', in metres? | 15.8 m | 15.8 | correct | ifc | proven (0.9) | 1.6 |  |
| 5 | What is the net floor area of the room 'living room' in the model, in m²? | 18.495 m2 | 24.575 | wrong | ifc | proven (0.9) | 1.5 |  |

### bsmart_building_arch_ifc2x3

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many rooms (spaces) are there in the model 'ifc silly sample scene - project'? | 2 | 2 | correct | ifc | proven (0.9) | 1.5 |  |
| 2 | How many walls are there in the model 'ifc silly sample scene - project'? | 4 | 4 | correct | ifc | proven (0.9) | 1.4 |  |
| 3 | How many doors are there in the model 'ifc silly sample scene - project'? | 0 | 0 | correct | ifc | proven (0.9) | 1.3 |  |
| 4 | What is the total length of all walls on '00 groundfloor', in metres? | 15.8 m | 15.8 | correct | ifc | proven (0.9) | 1.5 |  |
| 5 | What is the net floor area of the room 'living room' in the model, in m²? | 18.495 m2 | 24.575 | wrong | ifc | proven (0.9) | 1.4 |  |

### bsmart_wall_with_window

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many windows are there in the model 'Default Project'? | 1 | 1 | correct | ifc | proven (0.9) | 1.5 |  |
| 2 | How many doors are there in the model 'Default Project'? | 0 | 0 | correct | ifc | proven (0.9) | 1.4 |  |
| 3 | How many walls are there in the model 'Default Project'? | 1 | 1 | correct | ifc | proven (0.9) | 1.5 |  |

### ifcopenhouse_ifc4

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many doors are there in the model 'IfcOpenHouse'? | 1 | 1 | correct | ifc | proven (0.9) | 1.5 |  |
| 2 | How many windows are there in the model 'IfcOpenHouse'? | 5 | 5 | correct | ifc | proven (0.9) | 1.5 |  |
| 3 | How many walls are there in the model 'IfcOpenHouse'? | 4 | 4 | correct | ifc | proven (0.9) | 1.5 |  |
| 4 | How many rooms (spaces) are there in the model 'IfcOpenHouse'? | 0 | 0 | correct | ifc | proven (0.9) | 1.4 |  |

### schependomlaan_design_ifc

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many doors are there in the model '10 Appartementen Schependomlaan'? | 205 | abstain | abstain | ifc | cannot prove (0.0) | 4.1 | ifc: no IFC class for apartment |
| 2 | How many windows are there in the model '10 Appartementen Schependomlaan'? | 259 | abstain | abstain | ifc | cannot prove (0.0) | 4.1 | ifc: no IFC class for apartment |
| 3 | How many rooms (spaces) are there in the model '10 Appartementen Schependomlaan'? | 100 | abstain | abstain | ifc | cannot prove (0.0) | 4.1 | ifc: no IFC class for apartment |
| 4 | How many doors are shown on the floor plan of '01 eerste verdieping'? | 48 | 48 | correct | ifc | proven (0.9) | 9.4 |  |
| 5 | What is the net floor area of the room 'badkamer (3.08)' on '00 begane grond', in m²? | 6.55 m2 | 6.552 | correct | ifc | proven (0.9) | 5.9 |  |
| 6 | How many square meters of tiling should I order for the bathrooms? | 63.72 m2 | 63.72 | correct | ifc | proven (0.9) | 5.7 |  |

### kjdraw_architecture_floor_plan

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many doors (interior doors and entrance doors together) are drawn on this floor pla... | 12 (manual, check) | 0 | wrong | dxf-geometry | proven (0.95) | 1.5 |  |
| 2 | How many windows are drawn on this floor plan? | 6 (manual, check) | 0 | wrong | dxf-geometry | proven (0.95) | 1.5 |  |
| 3 | How many rooms labelled 'OPEN OFFICE' are there on this floor? | 2 (manual, check) | 0 | wrong | dxf-geometry | proven (0.95) | 1.5 |  |
| 4 | How many doors (interior doors and entrance doors together) are drawn on this floor pla... | 12 (manual, check) | 0 | wrong | dxf-geometry | proven (0.95) | 1.5 |  |
| 5 | How many doors (interior doors and entrance doors together) are drawn on this floor pla... (pdf-only) | 12 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.5 | pdf-text: no text-layer rule for this question; pdf-vector: no room shapes found, so the plan region is unknow |
| 6 | How many windows are drawn on this floor plan? | 6 (manual, check) | 0 | wrong | dxf-geometry | proven (0.95) | 1.5 |  |
| 7 | How many windows are drawn on this floor plan? (pdf-only) | 6 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.5 | pdf-text: no text-layer rule for this question; pdf-vector: no room shapes found, so the plan region is unknow |
| 8 | How many rooms labelled 'OPEN OFFICE' are there on this floor? | 2 (manual, check) | 0 | wrong | dxf-geometry | proven (0.95) | 1.4 |  |
| 9 | How many rooms labelled 'OPEN OFFICE' are there on this floor? (pdf-only) | 2 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.5 | pdf-text: no text-layer rule for this question; pdf-vector: no named room shapes found |

### wm_fraser_residence

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many doors (interior doors and entrance doors together) are drawn on this floor pla... | 14 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.6 | pdf-text: no text-layer rule for this question; pdf-vector: no room shapes found, so the plan region is unknow |
| 2 | How many rooms labelled 'BED ROOM' are there on this floor? | 3 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.4 | pdf-text: no text-layer rule for this question; pdf-vector: no named room shapes found |
| 3 | How many rooms labelled 'WC' are there on this floor? | 2 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.5 | pdf-text: no text-layer rule for this question; pdf-vector: no named room shapes found |
| 4 | How many bathtubs are drawn on this floor plan? | 1 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.4 | pdf-text: no text-layer rule for this question; pdf-vector: no room shapes found, so the plan region is unknow |
| 5 | What is the total net area in m² of all rooms labelled 'GARAGE' on this floor? | no answer derivable (manual, check) | abstain | abstain-ok | pdf-text | cannot prove (0.0) | 1.5 | pdf-text: no text-layer rule for this question; pdf-vector: scale unknown (none: no printed scale, scale bar,  |

### wm_747_villa

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many bedrooms are on this floor plan? | 2 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.5 | pdf-text: no text-layer rule for this question; pdf-vector: no PDF rule for count of bedroom |
| 2 | How many kitchens are on this floor plan? | 1 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.4 | pdf-text: no text-layer rule for this question; pdf-vector: no PDF rule for count of kitchen |
| 3 | How many bathrooms or washrooms are on this floor plan, not counting separate WC rooms? | 1 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.4 | pdf-text: no text-layer rule for this question; pdf-vector: no PDF rule for count of bathroom |
| 4 | What is the total net area in m² of all rooms labelled 'KITCHEN' on this floor? | no answer derivable (manual, check) | abstain | abstain-ok | pdf-text | cannot prove (0.0) | 1.5 | pdf-text: no text-layer rule for this question; pdf-vector: scale unknown (none: no printed scale, scale bar,  |

### wm_gottlieb_ground

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many bathrooms or washrooms are on this floor plan, not counting separate WC rooms? | 2 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.5 | pdf-text: no text-layer rule for this question; pdf-vector: PDF page is scanned (no vector linework); needs a  |
| 2 | How many kitchens are on this floor plan? | 1 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.5 | pdf-text: no text-layer rule for this question; pdf-vector: PDF page is scanned (no vector linework); needs a  |
| 3 | How many bedrooms are on this floor plan? | 0 (manual, check) | abstain | abstain | pdf-text | cannot prove (0.0) | 1.4 | pdf-text: no text-layer rule for this question; pdf-vector: PDF page is scanned (no vector linework); needs a  |

### wm_milam_1st_floor

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many rooms labelled 'GARAGE' are there on this floor? | 2 (manual, check) | abstain | abstain | raster-cv | cannot prove (0.0) | 1.3 | raster-cv: no classical-CV detector for room yet |
| 2 | How many floors (storeys) does this floor plan show? | 1 (manual, check) | 1 | correct | raster-cv | estimate (0.85) | 4.7 |  |
| 3 | How many kitchens are on this floor plan? | 1 (manual, check) | abstain | abstain | raster-ocr-labels | cannot prove (0.0) | 1.4 | raster-ocr-labels: only 0 room labels readable; cannot prove absence |

### wm_tyneside_flat

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many bedrooms are on this floor plan? | 5 (manual, check) | abstain | abstain | raster-ocr-labels | cannot prove (0.0) | 10.0 | raster-ocr-labels: only 0 room labels readable; cannot prove absence |
| 2 | How many kitchens are on this floor plan? | 2 (manual, check) | abstain | abstain | raster-ocr-labels | cannot prove (0.0) | 1.6 | raster-ocr-labels: only 0 room labels readable; cannot prove absence |
| 3 | How many floors (storeys) does this floor plan show? | 2 (manual, check) | 2 | correct | raster-cv | estimate (0.75) | 1.5 |  |
| 4 | How many toilet bowls (WC seats) are drawn on this floor plan? | 2 (manual, check) | 2 | correct | raster-detector | estimate (0.79) | 17.8 |  |
| 5 | How many showers and bathtubs are drawn on this floor plan in total? | 2 (manual, check) | abstain | abstain | raster-detector | cannot prove (0.0) | 1.6 | raster-detector: detector switched off for shower_or_bath: on held-out train-split images it was wrong more of |

### wm_ballymun_flat

| # | question | reference | ours | result | route | tier (conf) | s | why not answered / note |
|---|---|---|---|---|---|---|---|---|
| 1 | How many bedrooms are on this floor plan? | 6 (manual, check) | abstain | abstain | raster-ocr-labels | cannot prove (0.0) | 13.7 | raster-ocr-labels: 0 bedroom label(s) read (); this count is not reliable for bedroom on dev, abstaining |
| 2 | How many kitchens are on this floor plan? | 2 (manual, check) | abstain | abstain | raster-ocr-labels | cannot prove (0.0) | 1.5 | raster-ocr-labels: 0 kitchen label(s) read (); this count is not reliable for kitchen on dev, abstaining |
| 3 | How many bathrooms or washrooms are on this floor plan, not counting separate WC rooms? | 2 (manual, check) | abstain | abstain | raster-ocr-labels | cannot prove (0.0) | 1.4 | raster-ocr-labels: 0 bathroom label(s) read (); this count is not reliable for bathroom on dev, abstaining |
| 4 | How many balconies are on this floor plan? | 2 (manual, check) | 0 | wrong | raster-ocr-labels | estimate (0.7) | 1.8 |  |


### Diagnostic runs (not in the totals)

Four extra questions, to separate "the route cannot do it" from "the parser tripped"
(`unseen/questions_diagnostic.jsonl`, results in `unseen/diagnostic.jsonl`):

| question | reference | ours | route |
|---|---|---|---|
| doors in the model (model name left out), Schependomlaan | 205 | 205 | ifc |
| windows in the model (model name left out), Schependomlaan | 259 | 259 | ifc |
| rooms (spaces) in the model (model name left out), Schependomlaan | 100 | 100 | ifc |
| net floor area of 'living room' on '00 groundfloor' (Stoa-style, with a level), IFC4 tiny house | 18.495 m2 | 18.495 | ifc |

The IFC2x3 copy of the tiny house was not re-run with the level phrasing.

## What generalises, what breaks

**IFC route: generalises.** Counts of doors, windows, walls and rooms, a per-storey door count, a room area
from `NetFloorArea`, a storey wall length and a bathroom tiling takeoff were all right on models from two
other authoring tools, in IFC4 and IFC2x3, including a 934-wall, 100-space Dutch apartment block whose
spaces are tied to storeys through aggregation rather than containment (the storey door count 48, the room
6.552 m2 against 6.55 and the takeoff 63.72 m2 over 10 bathrooms all matched). Zero answers were correct
three times (no doors in a model with none). The 5 misses:
1. Three abstentions on Schependomlaan: the model's name, "10 Appartementen Schependomlaan", sits in the
   quoted part of the question and the rule parser reads "Appartementen" as the object "apartment" ("ifc: no
   IFC class for apartment"). Stoa's questions always carry the model name, so any model whose name contains
   an object word would hit this. With the name left out, all three are right (diagnostic).
2. Two wrong answers (IFC4 and IFC2x3 tiny house): "net floor area of the room 'living room' in the model"
   without a level is parsed as the total of all rooms, 24.575 m2, answered at confidence 0.9. Stoa's own
   room-area questions always name a level; with one the answer is 18.495 m2, correct (diagnostic). It is
   still a confident wrong answer to a phrasing the parser should not have taken for a total.
The small sample (5 models, 23 questions) says the IFC rules are not tied to Stoa's 20-odd models. It does not
say the route is complete: nothing here exercises exterior or interior wall filters, slab volumes or
curtain walls.

**DXF route: does not generalise, and fails loudly.** On the kjdraw office plan the route returns 0 doors,
0 windows and 0 rooms labelled 'OPEN OFFICE' at confidence 0.95, tier "proven" (route `dxf-geometry`,
explanation "door leaves on layer DOOR"). The drawing has 12 doors, 6 windows and 2 such rooms; its layers
are called A-DOOR and A-WINDOW, the Swiss data's are DOOR and WINDOW. This is the hard-coding the co-founder
warned about, in its worst form: not an abstention but a proven-looking zero. The same three questions on the
PDF rendered from it (twin present) get the same wrong zeros; with `--pdf-only` they abstain (pdf-text and
pdf-vector find no room shapes). A cheap guard would be to abstain when the DXF has none of the expected
layer names, which is not done here (solver code is out of scope for this test).

**PDF linework (`pdf-vector`, `pdf-text`): fails safe, answers nothing.** On the three generic plan PDFs
(Fraser, 747 villa, Gottlieb: 12 questions) it abstained 10 times with reasons ("no named room shapes
found", "no PDF rule for count of bedroom", "PDF page is scanned") and abstained correctly on the two area
questions of drawings with no scale ("scale unknown (none: no printed scale, scale bar...)"). No wrong
answer, no right answer. Its rules need the room fills, door-swing convention and line styles of Stoa's
sources; these plans use a different drawing convention (black wall fills, labelled rooms without closed
room shapes). The Fraser plan carries its room labels as text, so a label count is within reach of a rule; none exists
for this question type on PDFs.

**Raster: mostly abstains; the little it answers is right except one zero.**
- Storey counts (panel counting) were right on both images they were asked on (1 and 2).
- The toilet detector counted 2 toilet bowls on the Tyneside plans, correct (the evidence image boxes
  both). One question: this is an existence proof, not a rate. Shower/bath is switched off by design and
  abstained.
- Room-label routes (bedrooms, kitchens, bathrooms) abstained on all 8 asks, including the clean Tyneside
  image. I ran RapidOCR on it directly: it reads "Bedroom", "Kitchen", "Shower", "Lounge" correctly. The route
  matches only Finnish and Swedish abbreviations (MH, KH, K, PH ...), so it reported "only 0 room labels
  readable". A vocabulary limit, not an OCR limit, and it is honest: no wrong answer.
- Balconies on the Ballymun scan: answered 0 at confidence 0.7, reference 2 ("balcony" appears twice on
  the plan). The "a zero needs at least three readable room labels" rule did not protect this one, again
  because English words are not in the vocabulary.
- Doors, windows and sinks on these scans were not asked: they need a careful count by eye for a reference,
  which I did not do. The only detector evidence here is the one toilet question.

**Net read for the pitch.** Shown on unseen drawings: structured IFC reading is real and carries over (18 of
23 as run; the diagnostic reruns fix four of the five misses, and the fifth, the IFC2x3 room area, was not
rerun); on vector PDFs and most scans the system abstains instead of guessing (21 abstentions, 2 of them the
correct outcome). Not shown: any accuracy on vector PDFs, DXF or CubiCasa-style scans outside Stoa's
conventions; and the DXF route is wrong with high confidence on a DXF with other layer names. A fair
sentence is "the IFC route holds on unseen models; the DXF and PDF routes are tied to the conventions of
Stoa's sources, and the system says so by abstaining, except the DXF route, which answers wrongly."

## Evidence images

Copied from `out/unseen/evidence/` to `unseen/evidence/` (the full set stays in the ignored `out/`):

1. `unseen/evidence/schependomlaan_doors_level1_ifc_correct.png`: 48 doors on '01 eerste verdieping', numbered
   boxes on the storey plan. Correct. Several boxes stack at the same spot (near boxes 7, 12 and 16), so
   the overlay is less clean than the count is.
2. `unseen/evidence/tyneside_toilets_detector_correct.png`: the two toilet bowls found by the raster
   detector, scores at or above 0.30. Correct.
3. `unseen/evidence/kjdraw_doors_dxf_geometry_wrong_zero.png`: "Answer: 0 count, route: dxf-geometry, door
   leaves on layer DOOR" over a plan in which twelve door swing arcs are plainly drawn (this overlay is the
   PDF rendered from the DXF). Wrong, and shown as proven.

## Caveats

- Small: 12 drawings, 50 questions, 12 of them on images. Percentages above are counts over these, not
  estimates of Stoa's hidden test performance.
- The set is skewed to what is openly licensed: IFC models and clean hobby or historical plans, no scanned
  construction drawing sets, no multi-sheet PDFs, no door schedules.
- IFC references share the router's IFC library; manual references are one person's count.
- The detector weights were copied from the app worktree, not retrained or checked against the latest
  tuned thresholds in other worktrees; the detector sees these scans at default settings.
- Question phrasings are mine, copied from Stoa's templates. Two failures (model name with "Appartementen",
  room area without level) are phrasing-sensitive, which is itself the finding, but a differently phrased
  question set would give different counts.
- The first ask.py runs here used `python -I`, which hides the repo's own modules; those were reruns of
  import errors, not answers, and are not in the results.

## Reproduce

```
.venv\Scripts\python tools\unseen_fetch.py --out ..\data\unseen
.venv\Scripts\python tools\unseen_dxf_to_pdf.py ..\data\unseen\kjdraw_architecture_floor_plan\architecture-floor-plan.dxf ..\data\unseen\kjdraw_architecture_floor_plan\architecture-floor-plan.pdf
.venv\Scripts\python tools\unseen_build_questions.py ..\data\unseen
.venv\Scripts\python tools\unseen_run.py --data ..\data\unseen --out out\unseen
.venv\Scripts\python tools\unseen_report.py out\unseen\results.jsonl
.venv\Scripts\python tools\unseen_dedupe.py --unseen ..\data\unseen --stoa ..\data\stoa-full\junction-dataset\data
```

Single question: `.venv\Scripts\python ask.py "IFC Schependomlaan.ifc" "..." --data ..\data\unseen` with the drawing
path relative to `data/unseen` (for example `schependomlaan_design_ifc/IFC Schependomlaan.ifc`).
