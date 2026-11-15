#!/usr/bin/env bash
# Redeploy the live app so the new code is served at once: stop the running app (old containers otherwise keep
# serving for a while), deploy with 3 containers, then check that the page and one answer come back.
set -e
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
.venv/Scripts/python -m modal app stop -y blueprint-evidence || true
BPE_WARM=${BPE_WARM:-1} BPE_MAX_CONTAINERS=10 .venv/Scripts/python -m modal deploy deploy/modal_app.py | tail -1
U=https://muilu-markus--blueprint-evidence.us-east.modal.run
for i in 1 2 3 4 5 6; do sleep 15; n=$(curl -s -m 60 "$U/api/drawings" | grep -o '"path"' | wc -l); [ "$n" -gt 0 ] && break; done
echo "drawings: $n"
curl -s -m 120 -X POST "$U/api/ask" -H "Content-Type: application/json" \
  -d '{"path":"data/swiss_dwellings/site3387_bldg5238_floor23498.pdf","question":"What is the floor area of the bathrooms?"}' | grep -o '"value":[^,]*' | head -1
