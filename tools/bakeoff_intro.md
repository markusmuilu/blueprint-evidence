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
