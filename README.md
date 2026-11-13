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
               4. raster-cv     classical CV on scans (CubiCasa); abstains where it has no detector
          --> answer JSON {value, unit, route, confidence, evidence, explanation, items}
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

Raster: multi-storey CubiCasa plans paste each floor as its own scan onto a white canvas, so the storey
count is the number of large non-white panels. Nothing else on the scans has a detector yet.

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

### Split

Dev/test is split by drawing, not by question, with a fixed seed (`SPLIT_SEED` in `bpqa/data.py`, 30%
test). All formats and renderings of one drawing land on the same side: a Swiss PDF and its DXF, an IFC
model and the plans rendered from it, all sheets of one drawing set. Rules were developed by looking at
dev failures only.

## Results

RESULTS_PLACEHOLDER

## Limits, stated plainly

- CubiCasa scans are 53% of the questions and only the storey count has a real detector. Everything else
  on scans abstains. `--prior` answers those with the most common dev answer per object type, as a
  labelled baseline (route `raster-prior`, evidence says "PRIOR, not visual proof"). It is in the table
  so a real detector has a number to beat, not because it is a method.
- Text answers (ifc_bench, some opening_design) are free-form; `eval.py` scores them approximately (all
  numbers in the gold answer present within 2%, else 60% token overlap). That scorer is lenient: "B3" is
  scored correct against "B3 (aluminium sliding door)".
- The door-schedule parser is tuned on one sheet (Sports Complex A-801). The CTR schedule uses a different
  layout and abstains.
- `kit-fzk-haus` plan PDFs have no IFC model in the dataset, so model-level questions about them abstain.
- Unnamed numbered rooms in Swiss floors are ambiguous (wet rooms, shafts, balconies). The fixture rule
  is right more often than not, and is where most remaining Swiss area errors are.

## Next steps

1. Raster detectors for the CubiCasa counts, trained or templated per symbol (toilet, sink, door swing,
   window), with the same boxed-and-numbered evidence. This is where most of the remaining score is.
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
