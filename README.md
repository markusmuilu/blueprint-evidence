# Blueprint Evidence

Ask a question about a construction drawing and get the answer with the evidence marked on the drawing, so
you can check where every number came from. Built at Junction 2026 for Stoa's challenge "Make Blueprints
Talk"; 2nd place in the Verda challenge.

- **Live app:** https://muilu-markus--blueprint-evidence.us-east.modal.run
- **Results, every test question with our answer:** [results/README.md](results/README.md)
- **Where it fails, with pictures:** [docs/failure_gallery/SUMMARY.md](docs/failure_gallery/SUMMARY.md)
- **Full technical documentation:** [docs/DETAILS.md](docs/DETAILS.md)

Everything in this repository was built at Junction 2026.

## What it does

You give it one drawing (PDF, DXF, IFC model, or a scanned PNG or JPEG) and a question such as "How many
fire doors are on this floor?" or "Kuinka isoja alueita makuuhuoneet ovat yhteensä?". It returns:

- the **value and unit** (a count, an area, a length or a volume),
- a **status**: *Proven* (read from the drawing's own data, or a kind of answer measured at 98% or better),
  *Estimate* (with the reason and how often that kind of answer was right), or *cannot answer* (with the reason),
- the **evidence**: an image of the drawing with the counted items boxed and numbered and the measured rooms
  filled, plus the steps that led to the number.

The app also handles quantity takeoff questions (floor and wall tiles with waste, baseboard metres, slab
areas and concrete volume on IFC models) and several drawings at once.

## How it works

Most drawings carry more structure than their pixels, so a router picks the cheapest route that can prove
the answer and falls back only when it cannot:

1. **DXF layers** of the same drawing (Swiss Dwellings layer convention, or generic layer and block names).
2. **IFC entities**: storeys, quantity sets, space geometry, properties, each value cited by GlobalId.
3. **PDF text and linework**: schedules, room labels, room shapes, door swings, scale.
4. **Scans**: OCR of room labels (Finnish, Swedish, English, German), a room-type detector, and YOLO26
   object detectors for doors, windows and fixtures, run on tiles of the scan.

Questions are read by rules in English, Finnish, German, French, Italian, Danish and Estonian. When the
rules cannot read a question, an NVIDIA Nemotron model maps it to one of a fixed set of question types,
and the result is checked like any other query. In the live app no language model produces a number:
measurement is always done by code on the drawing.

## Training on Verda

The detector experiments ran on a Verda GPU instance (a spot RTX A6000, 48 GB) alongside a local GPU during
the hackathon, with Verda's Junction credits. The shipped detector is a small YOLO26 model trained on tiles
of the scans on the local GPU. On Verda a medium YOLO26 model was trained at several image scales; on the
tuning images it gained about 2 to 3 points on toilets, sinks and showers, but not on doors or windows, and
it needed about 1.5x the CPU time per scan plus a rebuilt calibration table. It is therefore not in the live
app yet, and it is the starting point for improving the scan side. Thanks to Verda for the GPU credits.

## Results

| test set | questions | right | notes |
|---|---|---|---|
| Stoa full set, test split | 39,807 | **84.8%** | Proven answers: 17,497 at 98.7% right |
| by source | | | Swiss dwellings 99.4%, IFC models 87.9%, CubiCasa scans 79.4%, IFC-Bench free text 28.6% |
| same 150 questions, Nemotron with a plain prompt vs this system | 150 | 39.3% vs **94.7%** | `out/baseline_nemotron.json` |
| unseen public drawings, not in Stoa's data | 50 | **82.0%** | 0 wrong Proven answers |

Cost is about EUR 0.15 per 1,000 questions, including the question reader. Details, per-question-type
numbers and the commit they were measured on: [docs/DETAILS.md](docs/DETAILS.md#results).

## Limits

- **Scans are the weak part.** Door counts on scans are right 59.6% of the time and window counts 49.1%
  (many objects per plan, so one missed box makes the answer wrong). These answers are always marked Estimate.
- **Areas on scans** come from printed room areas. Without printed areas the app gives a rough approximation
  (median error about 12%) and says so.
- **Faint or unusual scans** can be misread, for example heater symbols counted as rooms.
- **IFC test coverage is narrow**: the IFC test split is a single model, and free-text IFC questions are mostly wrong.

The full list is in [docs/DETAILS.md](docs/DETAILS.md#limits-stated-plainly).

## Run it

Python 3.11. The model weights (about 45 MB) are in the GitHub release `models-v1`; unzip
`blueprint-evidence-models.zip` in the repository root. The Stoa dataset is not included; point `--data` at
it or set `STOA_DATA`.

```
pip install -r requirements.txt
python ask.py <drawing> "How many windows are drawn on this floor plan?"
python -m app                                            # the web app on http://127.0.0.1:8000
python tools/score_results.py <junction-dataset> results # re-score the published answers
```

No API key is needed for the published numbers. An NVIDIA Build key (`python set_nvidia_key.py`) only
enables the question reader for unusual phrasings.

## Data credits and licences

The dataset is used for the Junction challenge only and is not included here.

| source | licence | credit |
|---|---|---|
| swiss_dwellings | CC BY 4.0 | Swiss Dwellings v3.0.0, Archilyse AG, https://doi.org/10.5281/zenodo.7788422; drawings rendered by the challenge organisers |
| cubicasa5k | CC BY-NC-SA 4.0 (NonCommercial) | CubiCasa5K, (c) 2019 CubiCasa. Kalervo, Ylioinas, Häikiö, Karhu, Kannala, SCIA 2019 |
| ifc_models | per file: CC BY 4.0, CC BY-ND 3.0, MIT, KIT unrestricted | buildingSMART Community Sample-Test-Files (Esplanades by Esplan, Molio Demo-hus), KIT IAI, ResBIM (Xing Liang) |
| ifc_bench | per file; questions CC BY 4.0 | IFC-Bench v2, Sylvain Hellin, TU Munich |
| opening_design | CC BY-SA 4.0 and MIT | OpeningDesign, Madison, Wisconsin |

Evidence images carry their drawing's licence; overlays and crops from CubiCasa5K (including
`docs/failure_gallery/img/`) are CC BY-NC-SA 4.0. The model weights are derived from CubiCasa5K and trained
with Ultralytics YOLO (AGPL-3.0), so they are for non-commercial use. More in
[docs/DETAILS.md](docs/DETAILS.md#data-credits-and-licences).

## Licence

Code: MIT, see `LICENSE`.
