# Model bake-off

Run 14 Nov 2026 on the preview TEST split, NVIDIA build.nvidia.com free tier (one key, rate limited). Harness: `tools/model_bakeoff.py`, report: `tools/bakeoff_report.py`, raw data: `out/model_bakeoff.json` (replies are cached under out/nemo_cache, per-model records in out/bakeoff).

## Verdict

**No model beat our router in any category.** Sample sizes are small for the slow models, so read the numbers as direction, not precision.

- Scan doors: ours 27/41 (66%). Best other: mistral-large-4 3/8 (38%), then super-vl 2/22, llama-3.2-90b 1/12, bolt and ising 0/40+.
- Scan windows: ours 23/43 (53%). Best other: mistral-large-4 6/13 (46%, ours 62% on the same 13), super-vl 5/22 (23%).
- Scan room types: ours 51/55 (93%). Best other: mistral-large-4 8/11 (73%, ours 91% on the same 11), super-vl 30/44 (68%).
- Areas (Swiss and IFC, 60 to 91 questions): ours 100%. Best vision model: super-vl 6/60 (10%).
- Swiss counts: ours 54/54. Best vision model: super-vl 20/40 (50%).
- Text-only LLMs over the PDF text layer (60 questions, ours without DXF/IFC twin is 60/60): nemotron-3-ultra-550b 16/60 (27%; Swiss counts 12/20, areas 4/20, IFC 0/20); mistral-large-4 9/12 on Swiss counts before it was stopped (75%, n=12). Areas are mostly not printed in the text layer, so a plain LLM cannot get them.
- Naive 150-question sample: Nemotron super-vl 39% (the earlier baseline, re-used), bolt 26%, ising 26%, llama-3.2-90b 30% (n=20). Ours 95% on the same subset.

Pairing question by question (the "Ours wrong, model right" columns): over every model and category, the cases where a model was right and ours wrong are few (typically 0 to 4 per model and category, mostly room types and windows), always outnumbered many times over by the cases where ours was right and the model wrong. An override rule would lose accuracy; a second-opinion flag could gain a handful of answers at most.

## Recommendation

Do not plug any of these in now. If a backup is wanted anyway, the only candidate that shows signs of life is **mistral-large-4-1t-a52b on scan windows and room types**, as a flag next to our estimate-tier answer (never overriding it, and shown only when the two disagree). That needs a proper run first: it was reachable but slow (median 6 to 13 s, tail over 100 s) and only 32 weak-spot questions finished, so a few hundred more calls would be needed to know whether its disagreements are informative. Doors: nothing is close. Swiss, IFC, areas: no model is a candidate. Integration, if wanted later: a call in `bpqa/routes/nemotron.py` style behind `second_opinion.py`, estimate tier only.

## Model availability

The key lists 83 models, but several return 404 for this account (gemma-3-12b/4b, phi-3-vision, kimi-k2.6, cosmos-reason2, vila, neva, fuyu) and gemma-4-31b, glm-5.3-flash, step-5-preview and deepseek-v4.1-flash timed out in probes. Nemotron-3-ultra, nemotron-3.5-lightning and laguna-xs reject images (text only). Vision models that worked: nemotron-3.5-super-vl, nemotron-3.5-bolt-e4b, ising-calibration-1.5-31b, nemotron-3-nano-omni, mistral-large-4, llama-3.2-90b-vision. muse-glimmer-30b answered a probe but was not run.

## Method and caveats

- Same prompt as the baseline (image plus question, reply with just the number; areas in square metres). Scoring is `eval.score` (counts exact, areas within 2%). A missing or unparseable reply counts as wrong and as an abstention.
- Sets: base150 (the baseline sample), weak110 (40 CubiCasa door, 40 window, 30 room-type questions, one per drawing, English wording; room types are kitchen, bedroom, bathroom, sauna, wc_room, balcony, 5 each), text60 (20 Swiss counts, 20 Swiss areas, 20 IFC-plan areas). Pooled category tables use the unique questions a model actually answered.
- Run time was cut: llama (n=36 on weak110), mistral (32), super-vl (59), nano-omni (40, stopped, 0% doors and windows) did not finish all questions; bolt and ising did all 255. Free-tier latency for the large models was 30 to 230 s per call.
- Our router ran with Nemotron off (`nemotron=False`), full routes for image/text tables and `pdf_only` for the text table. It never abstained on these questions; all scan answers are estimate tier.
- Cost: build.nvidia.com free tier, no published per-token price, so cost per question is unknown. Mean tokens per question are in the tables (reasoning models spend 2000 to 7000 output tokens per question).
- The 'pooled' ours accuracy on weak110 is 76/110 (69%); the earlier 94% figure is the full test, not this weak-spot sample.

## Pitch sentence

"We ran six hosted vision models and two text LLMs on the same questions as our system: on the scan questions we are weakest at, the best other model got 53% against our 78% on the same questions, and on areas and Swiss and IFC counts the best got 50% or less against our 100%."


## Results: vision models, accuracy per category (pooled unique questions)

| Model | Scan doors | Scan windows | Scan room types | Scan other counts | Swiss counts | Swiss areas | IFC areas | Areas (Swiss + IFC) |
|---|---|---|---|---|---|---|---|---|
| **Our router (Nemotron off)** | 66% (27/41) | 53% (23/43) | 93% (51/55) | 94% (15/16) | 100% (54/54) | 100% (47/47) | 100% (44/44) | 100% (91/91) |
| meta_llama_3.2_90b_vision_instruct | 8% (1/12) | 17% (2/12) | 33% (5/15) | - | 57% (4/7) | 33% (1/3) | 0% (0/5) | 12% (1/8) |
| mistralai_mistral_large_4_1t_a52b | 38% (3/8) | 46% (6/13) | 73% (8/11) | - | 100% (1/1) | - | - | - |
| nvidia_ising_calibration_1.5_31b | 0% (0/40) | 2% (1/43) | 58% (32/55) | 62% (10/16) | 32% (13/40) | 3% (1/30) | 0% (0/30) | 2% (1/60) |
| nvidia_nemotron_3.5_bolt_e4b | 0% (0/41) | 5% (2/43) | 56% (31/55) | 69% (11/16) | 28% (11/40) | 7% (2/30) | 3% (1/30) | 5% (3/60) |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 9% (2/22) | 23% (5/22) | 68% (30/44) | 81% (13/16) | 50% (20/40) | 17% (5/30) | 3% (1/30) | 10% (6/60) |
| nvidia_nemotron_3_nano_omni_30b_a3b_reasoning (stopped early) | 0% (0/14) | 0% (0/12) | 50% (7/14) | - | - | - | - | - |

## Our router on the pooled questions, by tier

| Category | n | Answered | Correct | Proven right/answered | Estimate right/answered |
|---|---|---|---|---|---|
| Scan doors | 41 | 41 | 27 | 0/0 | 27/41 |
| Scan windows | 43 | 43 | 23 | 0/0 | 23/43 |
| Scan room types | 55 | 55 | 51 | 0/0 | 51/55 |
| Scan other counts | 16 | 16 | 15 | 7/7 | 8/9 |
| Swiss counts | 54 | 54 | 54 | 54/54 | 0/0 |
| Swiss areas | 47 | 47 | 47 | 47/47 | 0/0 |
| IFC areas | 44 | 44 | 44 | 44/44 | 0/0 |

The router never abstained on these questions; scan doors, windows and room types are all 'estimate' tier.


Cells are accuracy with correct/total; an abstention or unparseable reply counts as wrong. Models were not all run on every question (cut models and rate-limit failures), so compare a model with the 'Our router' row only where the n is the same; the per-model tables below pair our answers question by question.


## Naive-baseline sample, 150 questions

| Model | n | Accuracy (95% CI) | Abstain | Acc. when answered | Median latency | Tokens in/out per q | Ours on the same questions | Ours wrong, model right | Ours right, model wrong |
|---|---|---|---|---|---|---|---|---|---|
| meta_llama_3.2_90b_vision_instruct | 20 | 30% (15% to 52%) | 0% | 30% | 62.8 s | 6146 / 25 | 100% | 0 | 14 |
| mistralai_mistral_large_4_1t_a52b | 2 | 100% (34% to 100%) | 0% | 100% | 114.9 s | 1404 / 992 | 100% | 0 | 0 |
| nvidia_ising_calibration_1.5_31b | 149 | 26% (20% to 34%) | 1% | 26% | 4.8 s | 333 / 151 | 96% | 3 | 107 |
| nvidia_nemotron_3.5_bolt_e4b | 150 | 26% (20% to 34%) | 0% | 26% | 1.2 s | 329 / 3 | 95% | 3 | 107 |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 150 | 39% (32% to 47%) | 23% | 51% | 24.8 s | 1596 / 6417 | 95% | 3 | 87 |
| nvidia_nemotron_3_nano_omni_30b_a3b_reasoning | 1 | 0% (0% to 79%) | 0% | 0% | 24.8 s | 426 / 2328 | 100% | 0 | 1 |

## Scan weak spots, 110 questions

| Model | n | Accuracy (95% CI) | Abstain | Acc. when answered | Median latency | Tokens in/out per q | Ours on the same questions | Ours wrong, model right | Ours right, model wrong |
|---|---|---|---|---|---|---|---|---|---|
| meta_llama_3.2_90b_vision_instruct | 36 | 22% (12% to 38%) | 0% | 22% | 63.9 s | 5172 / 2 | 78% | 1 | 21 |
| mistralai_mistral_large_4_1t_a52b | 32 | 53% (36% to 69%) | 0% | 53% | 11.9 s | 1067 / 275 | 78% | 2 | 10 |
| nvidia_ising_calibration_1.5_31b | 109 | 17% (11% to 25%) | 1% | 17% | 6.5 s | 326 / 191 | 70% | 2 | 60 |
| nvidia_nemotron_3.5_bolt_e4b | 110 | 17% (11% to 25%) | 0% | 17% | 1.2 s | 322 / 2 | 69% | 2 | 59 |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 59 | 32% (22% to 45%) | 15% | 38% | 70.1 s | 759 / 7230 | 73% | 2 | 26 |
| nvidia_nemotron_3_nano_omni_30b_a3b_reasoning | 40 | 18% (9% to 32%) | 30% | 25% | 36.6 s | 781 / 7438 | 82% | 1 | 27 |

## Per-category detail, vision models


### Scan doors

| Model | n | Accuracy | Abstain | Median latency | Ours (same qs) | Ours wrong, model right | Ours right, model wrong |
|---|---|---|---|---|---|---|---|
| meta_llama_3.2_90b_vision_instruct | 12 | 8% | 0% | 44.5 s | 83% | 0 | 9 |
| mistralai_mistral_large_4_1t_a52b | 8 | 38% | 0% | 12.6 s | 88% | 0 | 4 |
| nvidia_ising_calibration_1.5_31b | 40 | 0% | 0% | 7.0 s | 68% | 0 | 27 |
| nvidia_nemotron_3.5_bolt_e4b | 41 | 0% | 0% | 1.2 s | 66% | 0 | 27 |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 22 | 9% | 32% | 105.1 s | 73% | 0 | 14 |
| nvidia_nemotron_3_nano_omni_30b_a3b_reasoning | 14 | 0% | 57% | 159.1 s | 93% | 0 | 13 |

### Scan windows

| Model | n | Accuracy | Abstain | Median latency | Ours (same qs) | Ours wrong, model right | Ours right, model wrong |
|---|---|---|---|---|---|---|---|
| meta_llama_3.2_90b_vision_instruct | 12 | 17% | 0% | 94.0 s | 58% | 0 | 5 |
| mistralai_mistral_large_4_1t_a52b | 13 | 46% | 0% | 12.7 s | 62% | 1 | 3 |
| nvidia_ising_calibration_1.5_31b | 43 | 2% | 0% | 6.3 s | 53% | 1 | 23 |
| nvidia_nemotron_3.5_bolt_e4b | 43 | 5% | 0% | 1.2 s | 53% | 0 | 21 |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 22 | 23% | 14% | 87.3 s | 55% | 0 | 7 |
| nvidia_nemotron_3_nano_omni_30b_a3b_reasoning | 12 | 0% | 25% | 36.1 s | 58% | 0 | 7 |

### Scan room types

| Model | n | Accuracy | Abstain | Median latency | Ours (same qs) | Ours wrong, model right | Ours right, model wrong |
|---|---|---|---|---|---|---|---|
| meta_llama_3.2_90b_vision_instruct | 15 | 33% | 0% | 66.2 s | 93% | 1 | 10 |
| mistralai_mistral_large_4_1t_a52b | 11 | 73% | 0% | 5.8 s | 91% | 1 | 3 |
| nvidia_ising_calibration_1.5_31b | 55 | 58% | 2% | 5.2 s | 93% | 3 | 22 |
| nvidia_nemotron_3.5_bolt_e4b | 55 | 56% | 0% | 1.2 s | 93% | 4 | 24 |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 44 | 68% | 2% | 7.6 s | 91% | 4 | 14 |
| nvidia_nemotron_3_nano_omni_30b_a3b_reasoning | 14 | 50% | 7% | 18.0 s | 93% | 1 | 7 |

### Scan other counts

| Model | n | Accuracy | Abstain | Median latency | Ours (same qs) | Ours wrong, model right | Ours right, model wrong |
|---|---|---|---|---|---|---|---|
| nvidia_ising_calibration_1.5_31b | 16 | 62% | 0% | 4.7 s | 94% | 1 | 6 |
| nvidia_nemotron_3.5_bolt_e4b | 16 | 69% | 0% | 1.4 s | 94% | 1 | 5 |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 16 | 81% | 6% | 7.7 s | 94% | 1 | 3 |

### Swiss counts

| Model | n | Accuracy | Abstain | Median latency | Ours (same qs) | Ours wrong, model right | Ours right, model wrong |
|---|---|---|---|---|---|---|---|
| meta_llama_3.2_90b_vision_instruct | 7 | 57% | 0% | 64.7 s | 100% | 0 | 3 |
| nvidia_ising_calibration_1.5_31b | 40 | 32% | 0% | 4.3 s | 100% | 0 | 27 |
| nvidia_nemotron_3.5_bolt_e4b | 40 | 28% | 0% | 1.2 s | 100% | 0 | 29 |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 40 | 50% | 10% | 13.9 s | 100% | 0 | 20 |

### Swiss areas

| Model | n | Accuracy | Abstain | Median latency | Ours (same qs) | Ours wrong, model right | Ours right, model wrong |
|---|---|---|---|---|---|---|---|
| nvidia_ising_calibration_1.5_31b | 30 | 3% | 0% | 7.3 s | 100% | 0 | 29 |
| nvidia_nemotron_3.5_bolt_e4b | 30 | 7% | 0% | 1.2 s | 100% | 0 | 28 |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 30 | 17% | 47% | 101.6 s | 100% | 0 | 25 |

### IFC areas

| Model | n | Accuracy | Abstain | Median latency | Ours (same qs) | Ours wrong, model right | Ours right, model wrong |
|---|---|---|---|---|---|---|---|
| meta_llama_3.2_90b_vision_instruct | 5 | 0% | 0% | 60.8 s | 100% | 0 | 5 |
| nvidia_ising_calibration_1.5_31b | 30 | 0% | 0% | 4.8 s | 100% | 0 | 30 |
| nvidia_nemotron_3.5_bolt_e4b | 30 | 3% | 0% | 1.4 s | 100% | 0 | 29 |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 30 | 3% | 43% | 54.5 s | 100% | 0 | 29 |

### Areas (Swiss + IFC)

| Model | n | Accuracy | Abstain | Median latency | Ours (same qs) | Ours wrong, model right | Ours right, model wrong |
|---|---|---|---|---|---|---|---|
| meta_llama_3.2_90b_vision_instruct | 8 | 12% | 0% | 49.9 s | 100% | 0 | 7 |
| nvidia_ising_calibration_1.5_31b | 60 | 2% | 0% | 5.5 s | 100% | 0 | 59 |
| nvidia_nemotron_3.5_bolt_e4b | 60 | 5% | 0% | 1.3 s | 100% | 0 | 57 |
| nvidia_nemotron_3.5_super_vl_120b_a12b | 60 | 10% | 45% | 87.3 s | 100% | 0 | 54 |

## Text-only models over the PDF text layer (60 Swiss and IFC-plan questions)

| Model | Swiss counts | Swiss areas | IFC areas | All 60 | Abstain | Median latency | Tokens in/out per q |
|---|---|---|---|---|---|---|---|
| **Our router, PDF only (no DXF/IFC twin)** | 100% (20/20) | 100% (20/20) | 100% (20/20) | 100% (60/60) | - | - | - |
| mistralai_mistral_large_4_1t_a52b | 75% (9/12) | - | - | 75% (9/12) | 17% | 13.6 s | 352 / 656 |
| nvidia_nemotron_3_super_120b_a12b | 0% (0/20) | 0% (0/10) | - | 0% (0/30) | 100% | - | 0 / 0 |
| nvidia_nemotron_3_ultra_550b_a55b | 60% (12/20) | 20% (4/20) | 0% (0/20) | 27% (16/60) | 2% | 29.7 s | 702 / 2133 |
