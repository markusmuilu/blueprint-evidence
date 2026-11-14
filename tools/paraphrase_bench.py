"""Paraphrase benchmark for question understanding (rules vs rules + language model).

    python tools/paraphrase_bench.py build              # tools/paraphrase_cases.py -> tests/data/paraphrases.jsonl
    python tools/paraphrase_bench.py run                # rules only vs rules + LLM, per split / language / intent
    python tools/paraphrase_bench.py run --split heldout --no-llm
    python tools/paraphrase_bench.py run --show-misses  # print every parse that differs from the expected one

Each case has a text, a language, an intent and the expected canonical parse (the Query the routes accept, see
bpqa/phrasing.py INTENTS). A parse counts as correct when its signature equals the expected one:
(measure, obj, variant, room types, material, effective surface, waste %, ceiling height, tile height, name,
room number, apartment, level). The 'other' intent (property lookups, lists, comparisons) is correct when the
parse does not route to a numeric answer. Cases split 50/50 within each (intent, language) group: 'tune' is for
tuning the prompt, normaliser and rules, 'heldout' is reported and never tuned on.

The rules-plus-LLM column runs the real policy (phrasing.read): rules first, the model only for a missing,
unanswerable or weak rule parse. Calls are cached under out/nemo_cache, so a rerun is free and deterministic.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import os
os.environ.setdefault("NEMO_PARSE_DEADLINE_S", "0")      # batch run: no short live limit on the parse call
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA = REPO / "tests" / "data" / "paraphrases.jsonl"


def _expected(intent: str, text: str, slots: dict | None):
    """The canonical parse of an intent, with the slots a case carries."""
    from bpqa.phrasing import INTENTS
    if intent == "other":
        return None
    if intent == "area_rooms_of_type":
        exp = {"measure": "area", "obj": "room", "variant": "type_sum", "types": sorted(slots["types"]),
               "material": slots.get("material"), "surface": slots.get("surface", "floor")}
        for k in ("waste_pct", "ceiling_m", "tile_m"):
            if k in slots:
                exp[k] = slots[k]
        return exp
    if intent == "area_room_named":
        exp = {"measure": "area", "obj": "room", "label": slots["name"]}
        if "level" in slots:
            exp["level"] = slots["level"]
        return exp
    exp = {"measure": INTENTS[intent][0], "obj": INTENTS[intent][1]}
    if INTENTS[intent][2]:
        exp["variant"] = INTENTS[intent][2]
    if intent == "area_apartment":                       # the apartment letter is in the text
        m = re.search(r"(?:apartment|flat|Wohnung|lägenhet|huoneiston|Asunto|asunto)\s+([A-D])\b", text)
        exp["apartment"] = m.group(1)
    return exp


def build() -> int:
    from paraphrase_cases import AREAS, COUNTS, NAMED, OTHER, T, TAKEOFF
    from paraphrase_cases_extra import EXTRA, NAMED_EXTRA, OTHER_EXTRA, TAKEOFF_EXTRA
    from bpqa.phrasing import INTENTS

    def collect(counts_areas, other, takeoff, named):
        out = []
        for intent, rows in counts_areas.items():
            assert intent in INTENTS, intent
            out += [(intent, lang, text, None) for lang, text in rows]
        out += [("other", lang, text, None) for lang, text in other]
        out += [(T, lang, text, slots) for lang, text, slots in takeoff]
        out += [("area_room_named", lang, text, slots) for lang, text, slots in named]
        return out

    base = collect({**COUNTS, **AREAS}, OTHER, TAKEOFF, NAMED)
    extra = collect(EXTRA, OTHER_EXTRA, TAKEOFF_EXTRA, NAMED_EXTRA)
    rows, seen = [], defaultdict(int)
    groups = defaultdict(list)
    for it in base:
        groups[(it[0], it[1])].append(it)
    for (intent, lang), items in sorted(groups.items()):          # alternate tune / held-out inside each group
        for i, (_, _, text, slots) in enumerate(items):
            rows.append({"id": f"{intent}-{lang}-{i:02d}", "text": text, "lang": lang, "intent": intent,
                         "expected": _expected(intent, text, slots), "split": "tune" if i % 2 == 0 else "heldout",
                         "set": "base"})
    # the extra cases were written after the first tuning round: held-out, except every 7th (tune), so the halves
    # come out equal in size
    for j, (intent, lang, text, slots) in enumerate(extra):
        seen[(intent, lang)] += 1
        rows.append({"id": f"{intent}-{lang}-x{seen[(intent, lang)]:02d}", "text": text, "lang": lang, "intent": intent,
                     "expected": _expected(intent, text, slots), "split": "tune" if j % 7 == 3 else "heldout",
                     "set": "extra"})
    from paraphrase_cases_extra import QA_NOV14
    for k, (lang, text, intent, exp) in enumerate(QA_NOV14):
        rows.append({"id": f"qa1114-{lang}-{k:02d}", "text": text, "lang": lang, "intent": intent, "expected": exp,
                     "split": "tune", "set": "qa"})
    DATA.parent.mkdir(parents=True, exist_ok=True)
    with DATA.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    t = sum(r["split"] == "tune" for r in rows)
    print(f"{len(rows)} paraphrases ({t} tune, {len(rows) - t} heldout), {len({r['intent'] for r in rows})} intents -> {DATA}")
    return 0


def load() -> list[dict]:
    return [json.loads(l) for l in DATA.read_text(encoding="utf-8").splitlines() if l.strip()]


def signature(q, question: str) -> dict | None:
    """The comparable parse of a Query, or None when it is not a measurable question."""
    if not q.ok():
        return None
    from bpqa import walls
    d = {"measure": q.measure, "obj": q.obj}
    tk = q.extra.get("takeoff")
    if tk is not None:
        d["variant"] = "type_sum"
        d["types"] = sorted(tk["types"])
        d["material"] = tk.get("material")
        f, w, _ = walls.surfaces(tk)
        d["surface"] = "floor+walls" if f and w else ("walls" if w else (tk.get("surface") if tk.get("surface") == "ceiling" else "floor"))
        inp = walls.parse_inputs(question, tk)
        for k in ("waste_pct", "ceiling_m", "tile_m"):
            if inp.get(k) is not None:
                d[k] = float(inp[k])
        # the group: wet rooms read as the three wet types, the expected parse names the group
        if tk.get("group") == "wet":
            d["types"] = ["wet"]
        return d
    if q.variant:
        d["variant"] = q.variant
    if q.label:
        d["label"] = q.label
    if q.room_no:
        d["room_no"] = q.room_no
    if q.apartment:
        d["apartment"] = q.apartment
    if q.level:
        d["level"] = q.level
    return d


def matches(case: dict, q) -> bool:
    exp = dict(case["expected"] or {})
    if exp.pop("blocked", False):              # a room type no route filters by: the router abstains
        return bool(q.extra.get("blocked"))
    if case["intent"] == "other":
        return (not q.ok() or q.extra.get("compound") or q.extra.get("unresolved_scope") or q.extra.get("blocked")
                or (q.measure == "text" and q.obj != "scale"))
    if q.extra.get("blocked"):
        return False
    for flag in ("whole_model", "apartment_scope"):     # checked only where a case states them
        if flag in exp and bool(q.extra.get(flag)) != exp.pop(flag):
            return False
    got = signature(q, case["text"])
    if got is None:
        return False
    want = {k: (float(v) if k in ("waste_pct", "ceiling_m", "tile_m") else v) for k, v in exp.items() if v is not None}
    if want.get("variant") is None:
        want.pop("variant", None)
    got = {k: v for k, v in got.items() if v is not None}
    return got == want


def report(rows: list[dict], key: str) -> None:
    by = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        b = by[r[key]]
        b[0] += 1
        b[1] += r["rules_ok"]
        b[2] += r["full_ok"]
    for k, (n, a, b) in sorted(by.items()):
        print(f"  {k:24s} n={n:3d}  rules {a / n:6.1%}  rules+LLM {b / n:6.1%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "run"])
    ap.add_argument("--split", default="both", choices=["tune", "heldout", "both"])
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--show-misses", action="store_true")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", help="write per-case results as JSON")
    a = ap.parse_args()
    if a.cmd == "build":
        return build()
    from bpqa import phrasing
    from bpqa.query import RuleParser
    cases = [c for c in load() if a.split in ("both", c["split"])]

    def one(c):
        t0 = time.time()
        qr = RuleParser().parse(c["text"])
        r_ok = matches(c, qr)
        full_q, f_ok, called, dt = qr, r_ok, False, 0.0
        if not a.no_llm:
            t1 = time.time()
            full_q = phrasing.read(c["text"], use_llm=True)
            dt = time.time() - t1
            called = full_q.parser == "llm" or "weak" in full_q.extra or "llm_rejected" in full_q.extra
            f_ok = matches(c, full_q)
        return {**c, "rules_ok": r_ok, "full_ok": f_ok, "rules_sig": signature(qr, c["text"]),
                "full_sig": signature(full_q, c["text"]), "llm_asked": called, "llm_used": full_q.parser == "llm",
                "t": dt, "reading": full_q.extra.get("reading")}

    with ThreadPoolExecutor(a.threads) as ex:
        res = list(ex.map(one, cases))
    n = len(res)
    for split in (["tune", "heldout"] if a.split == "both" else [a.split]):
        rs = [r for r in res if r["split"] == split]
        if not rs:
            continue
        print(f"\n== {split}  n={len(rs)}  rules {sum(r['rules_ok'] for r in rs) / len(rs):.1%}  "
              f"rules+LLM {sum(r['full_ok'] for r in rs) / len(rs):.1%}  "
              f"LLM asked on {sum(r['llm_asked'] for r in rs)}, used on {sum(r['llm_used'] for r in rs)}")
        print(" by language")
        report(rs, "lang")
        print(" by intent")
        report(rs, "intent")
    used = [r["t"] for r in res if r["llm_used"] or r["llm_asked"]]
    if used:
        used.sort()
        print(f"\nlatency of read() when the model was asked (cached calls included): n={len(used)} "
              f"median {used[len(used) // 2]:.2f}s  p90 {used[int(len(used) * .9)]:.2f}s  max {used[-1]:.2f}s")
    if a.show_misses:
        for r in res:
            if not (r["full_ok"] if not a.no_llm else r["rules_ok"]):
                print(f"MISS [{r['split']}/{r['lang']}/{r['intent']}] {r['text']!r}\n    want {r['expected']}\n"
                      f"    rules {r['rules_sig']}\n    full  {r['full_sig']}")
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
