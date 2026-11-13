import cv2, sys, glob, os
sys.path.insert(0, '.')
import numpy as np
from bpqa.routes.fixtures import candidates
from bpqa.data import split_of
D = '../data/stoa-preview/junction-dataset-preview/data/cubicasa5k/'
SP = os.environ['SP']
ns = []
for f in sorted(glob.glob(D + '*.png')):
    n = os.path.basename(f)
    if split_of('data/cubicasa5k/' + n) != 'dev': continue
    ns.append(len(candidates(cv2.imread(f))))
print(len(ns), sum(ns), np.percentile(ns, [10, 50, 90]))
im = cv2.imread(D + 'high_quality_architectural-10321.png')
for k in candidates(im):
    x, y, w, h = k['xywh']; cv2.rectangle(im, (x, y), (x + w, y + h), (0, 0, 255), 1)
cv2.imwrite(SP + '/cand2.png', cv2.resize(im[250:480, 100:300], None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST))
