"""Locate battery/gear region and silver conduit on installed house plates."""
from __future__ import annotations

import os
from PIL import Image
import numpy as np

IMG = r"E:\Cursor\Projects\ecoflow-ocean-ha\web\frontend\img"
OUT = os.path.join(IMG, "gear", "review")


def analyze(name: str, x0: float, y0: float, x1: float, y1: float) -> None:
    path = os.path.join(IMG, name)
    im = Image.open(path).convert("RGB")
    w, h = im.size
    box = (int(w * x0), int(h * y0), int(w * x1), int(h * y1))
    crop = im.crop(box)
    arr = np.asarray(crop)
    # silver-ish: high L, low saturation, mid-high RGB similar
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    mx = np.maximum(np.maximum(r, g), b).astype(np.int16)
    mn = np.minimum(np.minimum(r, g), b).astype(np.int16)
    sat = mx - mn
    lum = (r.astype(np.int16) + g + b) // 3
    silver = (lum > 140) & (sat < 35) & (r > 120) & (g > 120) & (b > 120)
    # white/light packs
    white = (lum > 200) & (sat < 40)
    ys, xs = np.where(silver)
    print(name, "size", w, h, "box", box)
    if len(xs):
        print("  silver bbox local", xs.min(), ys.min(), xs.max(), ys.max(), "count", len(xs))
        print("  silver bbox global", box[0] + xs.min(), box[1] + ys.min(), box[0] + xs.max(), box[1] + ys.max())
    # row sums of silver to find horizontal conduit y
    row = silver.sum(axis=1)
    if row.max() > 0:
        top_rows = np.argsort(row)[-8:]
        print("  silver densest rows (local y):", sorted(int(y) for y in top_rows), "counts", [int(row[y]) for y in sorted(top_rows)])
    # save silver mask overlay
    vis = crop.copy()
    px = vis.load()
    for y, x in zip(*np.where(silver)):
        px[int(x), int(y)] = (255, 0, 0)
    vis.save(os.path.join(OUT, f"mask-silver-{name}"))
    print("  saved mask")


analyze("house-desert-installed-v1.png", 0.0, 0.38, 0.42, 0.78)
analyze("house-forest-installed-v1.png", 0.0, 0.38, 0.36, 0.82)
