# Per-question results, full test split

Every question of the junction-dataset test split with our answer: 39,807 questions, measured on commit
`8d5f0bb` (`eval.py --shard` runs merged, 14 Nov 2026). The test split was never used for tuning; thresholds and
rules were set on the dev split.

One CSV per source. Columns: `drawing` (file name under `data/<source>/`), `question`, `gold` (the dataset's
answer), `our answer` (empty when we declined), `unit`, `status` (`proven`, `estimate` or `cannot`), `correct`
(`yes` / `no`; a declined question counts as wrong). Long free-text values are cut at 300 characters.

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

Made by `tools/export_test_csv.py` from the run's `answers.jsonl`; the same files ship with the app for the
download on the Results page.
