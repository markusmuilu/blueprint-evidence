"""Ask one question about one drawing and get a JSON answer plus an evidence PNG.

    python ask.py data/swiss_dwellings/site1000_bldg2082_floor9385.pdf "How many windows are drawn on this floor plan?"

The drawing path is relative to the dataset root (--data or STOA_DATA).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bpqa.data import Question, data_root
from bpqa.router import Context, solve

REPO = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("question")
    ap.add_argument("--data")
    ap.add_argument("--pdf-only", action="store_true", help="ignore DXF/IFC twins of a PDF")
    ap.add_argument("--out", default=str(REPO / "out" / "evidence"))
    args = ap.parse_args()
    root = data_root(args.data)
    q = Question(qid=-1, path=args.path.replace("\\", "/"), question=args.question)
    ctx = Context(root=root, q=q, out_dir=Path(args.out), evidence=True, pdf_only=args.pdf_only)
    query, a = solve(ctx)
    print(json.dumps({"query": query.as_dict(), **a.as_dict()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
