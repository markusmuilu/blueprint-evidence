# What is still hard

Source: `eval.py --split test --sources cubicasa5k`, preview test split, 76 CubiCasa scans, 1,362 questions, Nemotron off (2026-11-14). 80.2% exact overall; doors 63.3%, windows 43.4%, sinks 74.3%. Full test split (README): doors 59.6%, windows 49.2%.

Strong: vector and IFC (Swiss dwellings 99.0% on the full test split, counts 100.0%). Weak: scan doors and windows.

## Failure modes (62 wrong drawings looked at by eye)

| Mode | of 62 | doors | windows | other |
|---|---|---|---|---|
| Counting convention: a window frame with a divider is two annotated windows | 9 |  | 9 |  |
| Other flat or space on the same sheet counted too | 8 | 3 |  | 5 |
| Door missed: no swing arc (sliding, double or plain line) | 5 | 5 |  |  |
| Extra door: cabinet, wardrobe or closet door | 5 | 5 |  |  |
| Unusual or confused fixture symbol | 5 |  |  | 5 |
| Pen marks, handwriting or sketch lines over the plan | 4 | 3 |  | 1 |
| Balcony glazing, porch or terrace openings | 3 | 1 | 2 |  |
| No clear cause found | 3 | 1 | 2 |  |
| Old, yellowed or faint scan | 3 | 1 | 1 | 1 |
| Room label unreadable, missing or upside down | 3 |  |  | 3 |
| Room-type detector wrong where labels do not decide | 3 |  |  | 3 |
| Small item missed (thick wall, crowded corner, hatched wall) | 3 | 1 | 2 |  |
| Window drawn as a faint thin line, missed | 3 |  | 3 |  |
| Annotated count not supported by what is drawn | 2 |  | 2 |  |
| Low resolution or a small plan on a large sheet | 2 | 2 |  |  |
| Plan cut off at the image edge | 1 |  | 1 |  |

- 61 of 102 wrong detector drawings are off by exactly one.
- Windows: 43 wrong drawings, 32 too low. Doors: 22 wrong, 13 too high, 9 too low.
- A window frame with a divider is two windows in the annotation; we count one. This is a convention, not a detection fault.
- Low resolution is rare and not where we lose: narrow drawings are exact on 79% of detector-object drawings, wider ones on 66 to 74%.
- Windows on the architectural scans: exact on 24 of 61 drawings. The dev figure quoted in an estimate (about 78%) is optimistic for them.
- 152 proven answers on this run, 152 right. All 270 wrong questions were estimates (amber).

## What we do now

Wrong counts are amber, never blue (proven needs 98% on at least 100 dev drawings). The numbered boxes let a person fix a count in seconds: "Not this one" removes an extra, "Add missed item" adds a missed one.

## What would fix it

Follow the dataset's window convention from the train split; sliding and double doors as synthetic training examples; hard negatives for cabinets; restrict counts to one flat; filter pen colour; read labels on four rotations; calibrate on architectural scans. None measured yet.

Full page: `index.html`. Every case: `inspected.json`.
