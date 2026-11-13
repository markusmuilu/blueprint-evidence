"""`python -m app [--port 8000] [--host 127.0.0.1] [--data PATH] [--results PATH]`"""
from __future__ import annotations

import argparse
import os


def main():
    ap = argparse.ArgumentParser(prog="python -m app", description="Blueprint Evidence demo app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data", help="dataset root holding questions.jsonl (default: STOA_DATA, then ../data/stoa-full)")
    ap.add_argument("--results", help="extra folder with eval outputs (<run>/summary.json)")
    ap.add_argument("--gpu", action="store_true", help="allow the detector to use a GPU (default: CPU only)")
    args = ap.parse_args()
    if not args.gpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""      # keep the detector off the GPU
    if args.data:
        os.environ["STOA_DATA"] = args.data
    if args.results:
        os.environ["BPE_RESULTS"] = args.results

    import uvicorn
    from .server import app
    print(f"Blueprint Evidence on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
