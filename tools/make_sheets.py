"""Build labelling contact sheets of fixture candidates from a random sample of DEV images.

    python tools/make_sheets.py N_IMAGES OUTDIR
Writes OUTDIR/sheet_XX.png and OUTDIR/index.json (sheet, slot -> image, xywh).
"""
import cv2, sys, os, json, glob, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from bpqa.routes.fixtures import candidates, crop
from bpqa.data import split_of

D = '../data/stoa-preview/junction-dataset-preview/data/cubicasa5k/'
n_img, out = int(sys.argv[1]), sys.argv[2]
os.makedirs(out, exist_ok=True)
files = [f for f in sorted(glob.glob(D + '*.png')) if split_of('data/cubicasa5k/' + os.path.basename(f)) == 'dev']
random.Random(7).shuffle(files)
items = []
used = []
for f in files:
    im = cv2.imread(f)
    cs = candidates(im)
    if len(cs) > 90:
        continue
    used.append(os.path.basename(f))
    for c in cs:
        items.append((os.path.basename(f), c['xywh']))
    if len(used) >= n_img:
        break
print(used)
T, COLS, ROWS = 100, 10, 6
index = []
cache = {}
for s in range(0, len(items), COLS * ROWS):
    sheet = np.full((ROWS * (T + 14), COLS * T, 3), 255, np.uint8)
    for k, (name, xywh) in enumerate(items[s:s + COLS * ROWS]):
        if name not in cache:
            cache = {name: cv2.imread(D + name)}
        im = cache[name]
        g, (x0, y0, x1, y1) = crop(im, xywh, size=T - 4, pad=1.0)
        tile = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        sc = (T - 4) / (x1 - x0)
        x, y, w, h = xywh
        cv2.rectangle(tile, (int((x - x0) * sc) - 1, int((y - y0) * sc) - 1), (int((x + w - x0) * sc), int((y + h - y0) * sc)), (0, 0, 255), 1)
        r, c = divmod(k, COLS)
        oy, ox = r * (T + 14), c * T
        cv2.putText(sheet, str(k), (ox + 2, oy + 11), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 0, 0), 1)
        sheet[oy + 13:oy + 13 + T - 4, ox + 2:ox + 2 + T - 4] = tile
        index.append({"sheet": s // (COLS * ROWS), "slot": k, "image": name, "xywh": list(xywh)})
    cv2.imwrite(f"{out}/sheet_{s // (COLS * ROWS):02d}.png", sheet)
json.dump(index, open(f"{out}/index.json", "w"))
print(len(items), "candidates,", (len(items) + COLS * ROWS - 1) // (COLS * ROWS), "sheets")
