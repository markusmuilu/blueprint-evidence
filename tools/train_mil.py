"""Train the fixture classifier from DEV image-level counts only (multiple-instance counting).

Each dev image is a bag of candidate regions; the model scores each candidate per class and the
sum of scores is fitted to the image's gold count. Run with any Python that has torch (CPU is fine):

    python tools/train_mil.py cv        # 5-fold cross-validation on dev images, out-of-fold report
    python tools/train_mil.py final     # train on all dev images, write models/fixtures_mil.npz
"""
import json, sys, random
from pathlib import Path
import numpy as np
import torch, torch.nn as nn

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "out" / "fixtures"
HEADS = sys.argv[2].split(",") if len(sys.argv) > 2 else ["toilet", "sink", "shower_or_bath"]
counts = json.loads((FX / "dev_counts.json").read_text())
bags = []
for stem, c in sorted(counts.items()):
    z = np.load(FX / f"{stem}.npz")
    X = z["X"].astype(np.float32)
    y = np.array([c.get(h, -1) for h in HEADS], np.float32)
    if len(X) and (y >= 0).any():
        bags.append((stem, X, y))
print(len(bags), "bags", HEADS)
D = bags[0][1].shape[2]


def model():
    return nn.Sequential(nn.Linear(D, 64), nn.ReLU(), nn.Dropout(0.2), nn.Linear(64, len(HEADS)))


WARMUP = 15


def train(tr, epochs=60, seed=0):
    torch.manual_seed(seed); random.seed(seed)
    allx = np.concatenate([b[1][:, 0] for b in tr])
    mu, sd = allx.mean(0), allx.std(0) + 1e-4
    net = model()
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    for ep in range(epochs):
        random.shuffle(tr)
        for stem, X, y in tr:
            k = np.random.randint(0, X.shape[1], size=len(X))
            x = torch.tensor((X[np.arange(len(X)), k] - mu) / sd)
            s = torch.sigmoid(net(x))
            yt = torch.tensor(y)
            m = yt >= 0
            cnt = s.sum(0)
            if ep < WARMUP:
                loss = (nn.functional.smooth_l1_loss(cnt[m], yt[m], reduction="sum")
                        + 0.05 * (s * (1 - s)).sum())          # push scores to 0 or 1
            else:
                # self-training MIL: the top-g candidates of each head are this image's positives
                tgt = torch.zeros_like(s)
                for j in range(len(HEADS)):
                    g = int(yt[j])
                    if g > 0:
                        tgt[torch.argsort(-s[:, j].detach())[:g], j] = 1
                w = m.float().unsqueeze(0).expand_as(s)
                loss = nn.functional.binary_cross_entropy(s, tgt, weight=w * (1 + 4 * tgt), reduction="sum")
            opt.zero_grad(); loss.backward(); opt.step()
    return net, mu, sd


def predict(net, mu, sd, X):
    net.eval()
    with torch.no_grad():
        s = torch.sigmoid(net(torch.tensor((X - mu) / sd))).numpy()   # [n, 8, H]
    net.train()
    return s.mean(1)


def decide(s, lo=0.3, hi=0.7):
    n = int((s > 0.5).sum())
    unsure = int(((s > lo) & (s < hi)).sum())
    return n, unsure


if sys.argv[1] == "cv":
    idx = list(range(len(bags)))
    random.Random(1).shuffle(idx)
    res = {h: [] for h in HEADS}
    for f in range(5):
        te = [bags[i] for i in idx[f::5]]
        tr = [bags[i] for i in idx if i not in idx[f::5]]
        net, mu, sd = train(list(tr))
        for stem, X, y in te:
            s = predict(net, mu, sd, X)
            for j, h in enumerate(HEADS):
                if y[j] >= 0:
                    n, u = decide(s[:, j])
                    res[h].append((stem, int(y[j]), n, u, float(s[:, j].sum())))
    for h, r in res.items():
        ok = sum(g == n for _, g, n, _, _ in r)
        conf = [x for x in r if x[3] == 0]
        okc = sum(g == n for _, g, n, _, _ in conf)
        print(f"{h:15s} n={len(r)} exact={ok} ({ok/len(r):.0%})  no-unsure: answered={len(conf)} correct={okc} prec={okc/max(1,len(conf)):.0%}")
    json.dump(res, open(FX / f"cv_{'_'.join(HEADS)}.json", "w"))
else:
    net, mu, sd = train(list(bags), seed=0)
    sdict = {k: v.detach().numpy() for k, v in net.state_dict().items()}
    out = ROOT / "models" / f"fixtures_mil_{'_'.join(HEADS)}.npz"
    out.parent.mkdir(exist_ok=True)
    np.savez(out, W1=sdict["0.weight"], b1=sdict["0.bias"], W2=sdict["3.weight"], b2=sdict["3.bias"],
             mu=mu, sd=sd, heads=np.array(HEADS))
    print("wrote", out)
