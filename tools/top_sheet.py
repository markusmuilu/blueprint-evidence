"""Dev-only diagnostic: top-scored candidates per image for one head, as a contact sheet."""
import sys, os, json
sys.path.insert(0, '.')
import cv2, numpy as np
from bpqa.routes import fixtures as fx
from bpqa.data import split_of
head, out = sys.argv[1], sys.argv[2]
D = '../data/stoa-preview/junction-dataset-preview/data/cubicasa5k/'
m = fx.load_model('fixtures_mil_toilet_sink_shower_or_bath.npz')
j = [str(h) for h in m['heads']].index(head)
counts = json.load(open('out/fixtures/dev_counts.json'))
stems = sorted(counts)[:30]
T = 100
sheet = np.full((len(stems) * (T + 14), 5 * T, 3), 255, np.uint8)
for r, st in enumerate(stems):
    im = cv2.imread(D + st + '.png')
    z = np.load(f'out/fixtures/{st}.npz')
    if len(z['X']) == 0: continue
    sc = fx.score(m, z['X'].astype(np.float32))[:, j]
    cv2.putText(sheet, f"{st[-12:]} gold={counts[st].get(head)}", (2, r * (T + 14) + 11), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 0, 0), 1)
    for c, i in enumerate(np.argsort(-sc)[:4]):
        g, (x0, y0, x1, y1) = fx.crop(im, tuple(z['boxes'][i]), size=T - 4, pad=1.0)
        t = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        cv2.putText(t, f"{sc[i]:.2f}", (2, 10), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)
        sheet[r * (T + 14) + 14:r * (T + 14) + 14 + T - 4, (c + 1) * T:(c + 1) * T + T - 4] = t
cv2.imwrite(out, sheet)
