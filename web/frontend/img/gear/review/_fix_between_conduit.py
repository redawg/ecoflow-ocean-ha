"""
Remove overhead EMT (pixel inpaint) and draw short between-battery conduits.
Applies to all desert/forest installed house plates.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

IMG = Path(r"E:\Cursor\Projects\ecoflow-ocean-ha\web\frontend\img")
REVIEW = IMG / "gear" / "review"


def is_silver(arr: np.ndarray) -> np.ndarray:
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    lum = (r + g + b) // 3
    sat = mx - mn
    # EMT gray metal: mid-high luminance, low saturation, not pure white stucco
    return (lum > 115) & (lum < 210) & (sat < 28) & ((r - b).clip(-20, 20) > -15)


def is_wallish(arr: np.ndarray) -> np.ndarray:
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    lum = (r + g + b) // 3
    sat = mx - mn
    return (lum > 175) & (sat < 35)


def inpaint_overhead(im: Image.Image, region: tuple[int, int, int, int], upward: int = 18) -> Image.Image:
    """Replace silver conduit pixels by sampling wall pixels from above."""
    x0, y0, x1, y1 = region
    arr = np.asarray(im.convert("RGB")).copy()
    sub = arr[y0:y1, x0:x1]
    silver = is_silver(sub)
    # Dilate slightly to catch pipe edges / clamps
    from PIL import Image as PILImage

    mask_im = PILImage.fromarray((silver.astype(np.uint8) * 255))
    mask_im = mask_im.filter(ImageFilter.MaxFilter(5))
    silver = np.asarray(mask_im) > 127

    h, w = silver.shape
    yy, xx = np.where(silver)
    for y, x in zip(yy, xx):
        gy, gx = y0 + y, x0 + x
        # sample upward until non-silver wallish or fallback average
        found = False
        for dy in range(upward, upward + 25):
            sy = gy - dy
            if sy < 0:
                break
            sample = arr[sy, gx]
            # accept if not silver-like
            lum = int(sample.sum()) // 3
            sat = int(sample.max()) - int(sample.min())
            if lum > 150 and sat < 40:
                arr[gy, gx] = sample
                found = True
                break
        if not found:
            # neighborhood mean of non-silver in sub
            y1s, y2s = max(0, y - 4), min(h, y + 5)
            x1s, x2s = max(0, x - 4), min(w, x + 5)
            patch = sub[y1s:y2s, x1s:x2s]
            m = ~is_silver(patch)
            if m.any():
                arr[gy, gx] = patch[m].mean(axis=0).astype(np.uint8)
    return Image.fromarray(arr)


def detect_pack_gaps(
    im: Image.Image,
    *,
    x0: int,
    x1: int,
    y_top: int,
    y_bot: int,
    min_pack_w: int = 28,
    max_pack_w: int = 90,
) -> list[tuple[int, int, int]]:
    """
    Find bright vertical packs and return gaps as (gap_left, gap_right, y_mid).
    """
    arr = np.asarray(im.convert("RGB"))
    band = arr[y_top:y_bot, x0:x1]
    # bright front faces
    r, g, b = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    lum = (r.astype(np.int16) + g + b) // 3
    sat = np.maximum(np.maximum(r, g), b).astype(np.int16) - np.minimum(
        np.minimum(r, g), b
    ).astype(np.int16)
    bright = (lum > 185) & (sat < 45)
    col = bright.mean(axis=0) > 0.35
    # find runs
    gaps: list[tuple[int, int, int]] = []
    packs: list[tuple[int, int]] = []
    i = 0
    n = len(col)
    while i < n:
        if not col[i]:
            i += 1
            continue
        j = i
        while j < n and col[j]:
            j += 1
        width = j - i
        if min_pack_w <= width <= max_pack_w:
            packs.append((x0 + i, x0 + j - 1))
        i = j
    y_mid = (y_top + y_bot) // 2
    # prefer upper-mid for between connectors
    y_pipe = y_top + int((y_bot - y_top) * 0.38)
    for a, b in zip(packs, packs[1:]):
        gl, gr = a[1] + 1, b[0] - 1
        if gr - gl >= 4:
            gaps.append((gl, gr, y_pipe))
    print(f"  packs={packs}")
    print(f"  gaps={gaps}")
    return gaps


def draw_gap_pipes(
    im: Image.Image,
    gaps: list[tuple[int, int, int]],
    *,
    pipe_h: int = 6,
) -> Image.Image:
    d = ImageDraw.Draw(im)
    body = (162, 166, 172)
    hi = (205, 208, 214)
    lo = (110, 114, 120)
    for xl, xr, yc in gaps:
        # slightly inset so pipe sits clearly in gap
        pad = max(0, (xr - xl - 14) // 2)
        a, b = xl + pad, xr - pad
        if b - a < 6:
            a, b = xl, xr
        y0, y1 = yc - pipe_h // 2, yc + pipe_h // 2
        d.rounded_rectangle([a, y0, b, y1], radius=2, fill=body)
        d.line([(a + 1, y0 + 1), (b - 1, y0 + 1)], fill=hi, width=1)
        d.line([(a + 1, y1), (b - 1, y1)], fill=lo, width=1)
        # collar fittings
        d.rectangle([a - 1, y0 - 1, a + 3, y1 + 1], fill=lo)
        d.rectangle([b - 3, y0 - 1, b + 1, y1 + 1], fill=lo)
    return im


def fix_desert(src: Path, dst: Path) -> None:
    im = Image.open(src).convert("RGB")
    # Overhead trunk + drops region
    im = inpaint_overhead(im, (270, 448, 640, 512), upward=14)
    # Second pass lower for drop stubs into tops
    im = inpaint_overhead(im, (300, 490, 635, 525), upward=20)
    gaps = detect_pack_gaps(im, x0=300, x1=640, y_top=520, y_bot=640, min_pack_w=30, max_pack_w=85)
    # If detection weak, fall back to measured gaps
    if len(gaps) < 2:
        gaps = [
            (355, 375, 555),
            (410, 430, 555),
            (465, 485, 555),
            (520, 540, 555),
        ]
        print("  desert fallback gaps")
    im = draw_gap_pipes(im, gaps, pipe_h=6)
    im.save(dst)
    print("saved", dst.name)


def fix_forest(src: Path, dst: Path) -> None:
    im = Image.open(src).convert("RGB")
    im = inpaint_overhead(im, (175, 525, 515, 575), upward=16)
    im = inpaint_overhead(im, (210, 555, 505, 595), upward=22)
    gaps = detect_pack_gaps(im, x0=230, x1=500, y_top=580, y_bot=700, min_pack_w=28, max_pack_w=80)
    if len(gaps) < 2:
        gaps = [
            (275, 295, 625),
            (325, 345, 625),
            (375, 395, 625),
            (425, 445, 625),
        ]
        print("  forest fallback gaps")
    im = draw_gap_pipes(im, gaps, pipe_h=5)
    im.save(dst)
    print("saved", dst.name)


def crop_preview(path: Path, box_frac, out: Path) -> None:
    im = Image.open(path)
    w, h = im.size
    x0, y0, x1, y1 = box_frac
    im.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1))).save(out)


def apply_all() -> None:
    # Masters first
    desert_master = IMG / "house-desert-installed-v1.png"
    forest_master = IMG / "house-forest-installed-v1.png"
    fix_desert(desert_master, desert_master)
    fix_forest(forest_master, forest_master)

    # Car variants: same geometry for desert (left wall gear)
    for p in sorted(IMG.glob("house-desert-installed-*.png")):
        if p.name == "house-desert-installed-v1.png":
            continue
        fix_desert(p, p)

    for p in sorted(IMG.glob("house-forest-installed-*.png")):
        if p.name == "house-forest-installed-v1.png":
            continue
        fix_forest(p, p)

    crop_preview(
        desert_master,
        (0.0, 0.38, 0.42, 0.78),
        REVIEW / "desert-between-conduit-final-crop.png",
    )
    crop_preview(
        forest_master,
        (0.0, 0.38, 0.36, 0.82),
        REVIEW / "forest-between-conduit-final-crop.png",
    )


if __name__ == "__main__":
    # Test on copies first if env TEST=1
    import sys

    if "--test" in sys.argv:
        fix_desert(IMG / "house-desert-installed-v1.png", REVIEW / "desert-between-v3.png")
        fix_forest(IMG / "house-forest-installed-v1.png", REVIEW / "forest-between-v3.png")
        crop_preview(
            REVIEW / "desert-between-v3.png",
            (0.0, 0.38, 0.42, 0.78),
            REVIEW / "desert-between-v3-crop.png",
        )
        crop_preview(
            REVIEW / "forest-between-v3.png",
            (0.0, 0.38, 0.36, 0.82),
            REVIEW / "forest-between-v3-crop.png",
        )
    else:
        apply_all()
