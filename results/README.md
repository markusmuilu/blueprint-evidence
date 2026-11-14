# Per-question results, full test split

Every question of the junction-dataset test split with our answer: 39,807 questions, measured on commit
`8d5f0bb` (`eval.py --shard` runs merged, 14 Nov 2026). The test split was never used for tuning; thresholds and
rules were set on the dev split.

One CSV per source, one row per question. Question text and the dataset's answers are not published: they belong
to the Stoa challenge dataset. Anyone with the dataset can join on `question_id` and re-score (below).

| Column | Meaning |
|---|---|
| `drawing` | file name of the drawing under `data/<source>/` in the dataset |
| `question_id` | the dataset's question id: the line number in `questions.jsonl`, counted from 0 |
| `question_type` | a short kind read from our parsed query, e.g. `door count`, `room area`, `wall length`, `slab volume`, `text lookup (wall)`; `not parsed` when the parser found no measure |
| `our_answer` | our answer at full precision; empty when we declined |
| `unit` | unit of our answer (`count`, `m2`, `m`, `m3`, or empty for text) |
| `status` | `proven` (the route's tuned conditions for a provable answer held), `estimate` (best answer when they did not), `cannot` (declined) |
| `correct` | `yes` / `no` under eval.py's scoring rule; a declined question counts as `no` |

Scoring rule (`score()` in `eval.py`, by the dataset's question type): counts must match exactly as whole numbers;
areas, lengths and volumes must be within 2% of the dataset's answer; free-text answers are right when every number
in the dataset's answer appears in ours within 2%, or, when it has no numbers, when ours shares at least 60% of its
words.

| Source | Questions | Right | Proven answers | Proven right | File |
|---|---:|---:|---:|---:|---|
| CubiCasa scans | 27,160 | 79.4% | 5,248 | 98.1% | [cubicasa5k.csv](cubicasa5k.csv) |
| Swiss dwellings | 11,408 | 99.4% | 11,408 | 99.4% | [swiss_dwellings.csv](swiss_dwellings.csv) |
| IFC models (building models) | 838 | 87.9% | 753 | 97.9% | [ifc_models.csv](ifc_models.csv) |
| IFC-Bench (free-text questions about models, mostly declined) | 381 | 28.6% | 69 | 40.6% | [ifc_bench.csv](ifc_bench.csv) |
| OpeningDesign | 20 | 70.0% | 19 | 73.7% | [opening_design.csv](opening_design.csv) |
| **All** | **39,807** | **84.8%** | **17,497** | **98.7%** | |

Part of the scan count misses are label noise in the dataset itself (Stoa's Eino on Discord: "humans made the
original dataset, humans can make mistakes of certain margin"); inspected examples are in
[docs/failure_gallery](../docs/failure_gallery/SUMMARY.md).

## Reproduce

With the Stoa dataset (the folder holding `questions.jsonl`) and the repo's requirements installed:

```
python tools/score_results.py <path to junction-dataset> results
```

It joins every row on `question_id`, checks the drawing name matches, scores `our_answer` against the dataset's
answer with `eval.py`'s own `score()`, and prints accuracy and Proven precision per source and overall. On the full
dataset it prints 84.8% right over 39,807 questions and 98.7% right over 17,497 Proven answers, and 0 rows that
differ from the `correct` column.

Made by `tools/export_test_csv.py` from the run's `answers.jsonl`; the same files ship with the app for the
download on the Results page.
