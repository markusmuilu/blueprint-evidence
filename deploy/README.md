# Public demo on Modal

Blueprint Evidence (`python -m app`) served from Modal on CPU, with a small credited demo set.

**URL:** https://muilu-markus--blueprint-evidence.us-east.modal.run (workspace `muilu-markus`, app
`blueprint-evidence`). Dashboard: https://modal.com/apps/muilu-markus/main/deployed/blueprint-evidence

All commands run from the repo root with the repo venv on Windows (`.venv\Scripts\python`). Modal is
authenticated on this PC (`~/.modal.toml`). First time in a checkout: `.venv\Scripts\pip install -r deploy\requirements.txt`.

## Redeploy after main changes

```
.venv\Scripts\python -m modal deploy deploy\modal_app.py
```

Code (`bpqa/`, `app/`, `tools/`, `deploy/web.py`), `models/`, the demo set and the eval results are added
when the container starts, so a code-only redeploy takes a few seconds and does not rebuild the image. The
image (Debian slim, Python 3.11, CPU torch 2.11, `requirements.txt`, `app/requirements.txt`) rebuilds only
when a requirements file changes (about 80 s).

The demo set and the Results-view data are git-ignored and built locally, once per checkout or when the
selection changes:

```
.venv\Scripts\python deploy\build_demo_set.py
```

It reads `../data/stoa-full/junction-dataset`, `../data/unseen` and the eval runs in
`../stoa-wt-polish/out` (`--stoa`, `--unseen`, `--results` to change them). `modal_app.py` refuses to
deploy without `deploy/demo_data/` or `models/detector_thresholds.json`.

## Warm container for judging

Default: `min_containers=0`, so nothing runs (and nothing is billed) while nobody uses it. A cold start
serves the page in about 5 to 10 s. To keep one container warm, for example Sunday morning:

```
set BPE_WARM=1&& .venv\Scripts\python -m modal deploy deploy\modal_app.py
```

and to go back to scale-to-zero afterwards, redeploy without it (`set BPE_WARM=` first, in the same shell).
In PowerShell: `$env:BPE_WARM=1; .venv\Scripts\python -m modal deploy deploy\modal_app.py` (then
`Remove-Item Env:BPE_WARM`). In Git Bash: `BPE_WARM=1 .venv/Scripts/python -m modal deploy deploy/modal_app.py`.
Verified on 2026-11-14: the warm deploy starts one container with no traffic; the plain redeploy returns to zero.

Cost (rates from `modal billing rates`, 2026-11-14: $0.0473 per core-hour, $0.008 per GiB-hour): the
container requests 4 cores and 4 GiB, so about **$0.22 per hour** while it runs. Warm all day, 12 hours,
is about $2.70; around the clock about $5.30. Scale-to-zero adds only the minutes in use plus the
2-minute scale-down window.

## Logs, usage, stop

```
.venv\Scripts\python -m modal app logs blueprint-evidence        # live request log and [prewarm] lines
.venv\Scripts\python -m modal container list                     # running containers
.venv\Scripts\python -m modal billing report --for today --show-resources
.venv\Scripts\python -m modal app stop blueprint-evidence        # take it offline (the URL stops working)
```

After `app stop`, the redeploy command brings it back at the same URL.

## Nemotron (optional)

The app attaches the Modal Secret `nvidia` (key `NVIDIA_API_KEY`) when it exists at deploy time, and
otherwise deploys with the Nemotron fallback off. To turn it on, Markus runs, once:

```
.venv\Scripts\python -m modal secret create nvidia --from-dotenv C:\Users\Markus\Coding\junction-2026\stoa\.env
```

then the redeploy command above. `--from-dotenv` copies every line of that `.env` into the secret, so it
should hold only `NVIDIA_API_KEY`. With the key attached, anyone using the public URL can trigger
Nemotron calls on that key: they come only after every local route abstained, are throttled
(`NEMO_MIN_INTERVAL`, 1.6 s) and cached per image and prompt. `/api/deploy` shows `nemotron_enabled`.

## What runs

- `modal_app.py`: image and function. CPU only, `cpu=4.0` (physical cores, 8 vCPU), `memory=4096` MiB,
  `max_containers=1`, `@modal.concurrent(max_inputs=8)`, `scaledown_window` 120 s (300 s when warm),
  timeout 600 s. One container, because uploads live in its temp folder; solves are serialised by the
  app's own lock anyway, and the 8 concurrent inputs keep previews, evidence images and static files
  flowing while one solve runs.
- `web.py`: wraps `app/server.py` without changing it. Sets `STOA_DATA` to the demo set; uploads limited
  to PDF, DXF, IFC, PNG and JPG and 30 MB per request (checked before the body is read, and in the
  browser before sending); uploads go to the container's temp folder and are deleted, with every cache
  file derived from them, 15 minutes after upload (`BPE_UPLOAD_TTL`), and in any case when the container
  stops. Adds `/credits`, `/api/credits?path=`, a credit bar on every page naming the licence and credit
  of the drawing on screen, and `/api/deploy` (memory, CPU seconds, uptime; no secrets). At start it
  loads torch, both detector models and the OCR engine (about 5 s), then fills the detector and OCR cache
  for the 7 demo scans (about 80 s), so scan questions on demo drawings answer in under a second;
  `BPE_PREWARM=0` and `BPE_PRECOMPUTE=0` turn these off.
- `build_demo_set.py`: the fixed selection (22 drawings with 769 dataset questions, about 93 MB): 7 Swiss
  floors as PDF + DXF (the two README examples and five test-split floors with area questions), 3 IFC
  models with 2 plans each (FZK-Haus, Esplanades, Molio; no NoDerivatives files), 6 CubiCasa PNGs (two
  each of `high_quality_architectural`, `high_quality`, `colorful`), and two unseen public drawings
  (kjdraw office plan PDF + DXF, Apache-2.0; Tyneside flat PNG, CC BY-SA 4.0, Andy Dingley). Each source
  folder gets a `LICENSE.md` trimmed to its files (the router reads it for the NoDerivatives rule) and
  `credits.json` feeds the credit bar. Stoa's data is for the Junction challenge, non-commercial: it is
  shipped only inside the Modal image, never committed.
- `smoke_test.py [URL]`: tiling on a Swiss PDF (with and without the DXF twin), IFC area, IFC tiling,
  CubiCasa counts, an unseen DXF upload with two questions, the upload limits and the credit endpoints.
  Every ask uses `refresh=true`.

## Measured (2026-11-14, from Espoo to us-east)

| check | result | round trip |
|---|---|---|
| cold start, page served | new container | 5 to 10 s |
| tiling, Swiss PDF, DXF twin | 16.76 m², proven, dxf-geometry | 0.9 s cold, 0.65 s warm |
| tiling, Swiss PDF only | 16.76 m², proven, pdf-vector | 1.0 s cold, 0.8 s warm |
| IFC area, FZK-Haus kitchen | 16.305 m² (dataset 16.31), proven, ifc | 1.0 s cold, 0.7 s warm |
| IFC tiling, Molio (74 MB IFC) | 13.86 m², proven, ifc | 6.4 s first parse, 0.8 s after |
| CubiCasa windows, 10372 | 12 (dataset 12), estimate, raster-detector | 26 s uncached, under 1 s cached |
| upload unseen kjdraw DXF, doors / windows | 12 / 6 (reference 12 / 6), proven, dxf-generic | 0.7 s upload, 0.7 s each ask |
| upload `.txt` / 31 MB | 400 / 413 | 0.6 s / 7.5 s |

About 0.45 s of every round trip is the network from Finland to us-east; the app's own solve times are
in the answer JSON (`time_ms`). Peak process memory seen: 2.9 GB (Molio IFC plus the detectors).
Fresh uploaded scans take 3 to 26 s on CPU depending on image size (tiled detector with test-time
augmentation).
