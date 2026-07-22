"""
Restore Forest liked v4 plates (correct inverter, original proportions),
then only: erase overhead EMT + draw near-top between-battery pipes.
No gear rescale / stretch.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

IMG = Path(r"E:\Cursor\Projects\ecoflow-ocean-ha\web\frontend\img")
ASSETS = Path(r"C:\Users\andre\.cursor\projects\e-Cursor-Projects-ecoflow-ocean-ha\assets")
REVIEW = IMG / "gear" / "review"

V4_MAP = {
    "house-forest-installed-bay1-tesla-model3-v1.png": "house-forest-installed-bay1-tesla-model3-v4.png",
    "house-forest-installed-bay1-tesla-modely-v1.png": "house-forest-installed-bay1-tesla-modely-v4.png",
    "house-forest-installed-bay2-rivian-r1s-v1.png": "house-forest-installed-bay2-rivian-r1s-v4.png",
    "house-forest-installed-bay2-rivian-r1t-v1.png": "house-forest-installed-bay2-rivian-r1t-v4.png",
    "house-forest-installed-bay2-tesla-model3-v1.png": "house-forest-installed-bay2-tesla-model3-v4.png",
    "house-forest-installed-bay2-tesla-modely-v1.png": "house-forest-installed-bay2-tesla-modely-v4.png",
}

# Geometry from clean v4 analysis (bay2 tesla-modely)
PACK_GAPS = [(268, 279), (301, 311), (335, 347)]  # between 4 packs
INV_GAP = (235, 246)  # inverter -> first pack
PIPE_Y = 528  # a little below pack tops (~510)
DROP_CENTERS = [257, 290, 323, 359]
TRUNK = (210, 498, 485, 524)  # x0,y0,x1,y1 overhead trunk


def erase_overhead(bgr: np.ndarray) -> np.ndarray:
    out = bgr.copy()
    h, w = out.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    x0, y0, x1, y1 = TRUNK
    cv2.rectangle(mask, (x0, y0), (x1, y1), 255, -1)
    for cx in DROP_CENTERS:
        cv2.rectangle(mask, (cx - 5, 508), (cx + 5, 540), 255, -1)
    # also drop into inverter top area
    cv2.rectangle(mask, (215, 505), (235, 535), 255, -1)
    # slight dilate for pipe edges
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    # do not erase bright pack faces
    L = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)[:, :, 0]
    mask[(L > 200) & (np.arange(h)[:, None] > 530)] = 0
    return cv2.inpaint(out, mask, 4, cv2.INPAINT_TELEA)


def draw_between_pipes(bgr: np.ndarray) -> np.ndarray:
    out = bgr.copy()
    yc = PIPE_Y
    for xl, xr in [INV_GAP, *PACK_GAPS]:
        cv2.rectangle(out, (xl, yc - 2), (xr, yc + 2), (158, 160, 164), -1)
        cv2.line(out, (xl, yc - 2), (xr, yc - 2), (200, 202, 206), 1)
        cv2.rectangle(out, (xl - 1, yc - 3), (xl + 2, yc + 3), (112, 114, 118), -1)
        cv2.rectangle(out, (xr - 2, yc - 3), (xr + 1, yc + 3), (112, 114, 118), -1)
    return out


def soft_left(gear: np.ndarray, base: np.ndarray, solid=0.34, fade=0.44) -> np.ndarray:
    h, w = base.shape[:2]
    if gear.shape[:2] != (h, w):
        gear = cv2.resize(gear, (w, h), interpolation=cv2.INTER_LINEAR)
    alpha = np.zeros((h, w), np.float32)
    x0, x1 = int(w * solid), int(w * fade)
    alpha[:, :x0] = 1.0
    if x1 > x0:
        alpha[:, x0:x1] = np.linspace(1.0, 0.0, x1 - x0, dtype=np.float32)[None, :]
    a = alpha[:, :, None]
    return (gear.astype(np.float32) * a + base.astype(np.float32) * (1 - a)).astype(np.uint8)


def main() -> None:
    # 1) Restore liked v4 plates
    for live, src in V4_MAP.items():
        data = cv2.imread(str(ASSETS / src))
        cv2.imwrite(str(IMG / live), data)
        print("restored", live)

    # 2) Fix conduit on each liked plate in place
    donor = None
    for live in V4_MAP:
        p = IMG / live
        bgr = cv2.imread(str(p))
        out = draw_between_pipes(erase_overhead(bgr))
        cv2.imwrite(str(p), out)
        if "bay2-tesla-modely" in live:
            donor = out
            cv2.imwrite(str(REVIEW / "forest-final-conduit-zoom.png"), out[490:700, 200:500])
            cv2.imwrite(str(REVIEW / "forest-final-conduit-full.png"), out)
        print("conduit", live)

    assert donor is not None

    # 3) Master: liked fixed gear left + empty garage right from assets v1
    empty = cv2.imread(str(ASSETS / "house-forest-installed-v1.png"))
    master = soft_left(donor, empty)
    cv2.imwrite(str(IMG / "house-forest-installed-v1.png"), master)
    print("master")

    # 4) bay1 rivians + duals: cars from assets/current right, gear from donor
    for name in [
        "house-forest-installed-bay1-rivian-r1s-v1.png",
        "house-forest-installed-bay1-rivian-r1t-v1.png",
    ]:
        src = ASSETS / name
        base = cv2.imread(str(src if src.exists() else IMG / name))
        cv2.imwrite(str(IMG / name), soft_left(donor, base))
        print("synced", name)

    for p in sorted(IMG.glob("house-forest-installed-dual-*.png")):
        asset = ASSETS / p.name
        base = cv2.imread(str(asset if asset.exists() else p))
        cv2.imwrite(str(p), soft_left(donor, base))
        print("dual", p.name)

    print("done")


if __name__ == "__main__":
    main()
