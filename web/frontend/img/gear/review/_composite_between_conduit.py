"""Composite between-battery conduit gear from v3 masters onto all installed plates."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

ASSETS = Path(r"C:\Users\andre\.cursor\projects\e-Cursor-Projects-ecoflow-ocean-ha\assets")
IMG = Path(r"E:\Cursor\Projects\ecoflow-ocean-ha\web\frontend\img")
REVIEW = IMG / "gear" / "review"


def soft_left_mask(w: int, h: int, solid_until: float, fade_to: float) -> Image.Image:
    """Alpha mask: opaque on left, fades to 0 between solid_until and fade_to (fractions of width)."""
    x0 = int(w * solid_until)
    x1 = int(w * fade_to)
    mask = Image.new("L", (w, h), 0)
    px = mask.load()
    for x in range(w):
        if x <= x0:
            a = 255
        elif x >= x1:
            a = 0
        else:
            t = (x - x0) / max(1, x1 - x0)
            a = int(255 * (1 - t))
        for y in range(h):
            px[x, y] = a
    return mask


def composite_site(master: Image.Image, target: Path, solid: float, fade: float) -> None:
    base = Image.open(target).convert("RGB")
    if base.size != master.size:
        master_r = master.resize(base.size, Image.Resampling.LANCZOS)
    else:
        master_r = master
    w, h = base.size
    mask = soft_left_mask(w, h, solid, fade)
    out = Image.composite(master_r, base, mask)
    out.save(target)
    print("updated", target.name)


def main() -> None:
    desert = Image.open(ASSETS / "house-desert-installed-v3.png").convert("RGB")
    forest = Image.open(ASSETS / "house-forest-installed-v3.png").convert("RGB")

    # Promote masters
    desert.save(IMG / "house-desert-installed-v1.png")
    forest.save(IMG / "house-forest-installed-v1.png")
    print("masters promoted to v1")

    # Review crops
    REVIEW.mkdir(parents=True, exist_ok=True)
    w, h = desert.size
    desert.crop((0, int(h * 0.35), int(w * 0.45), int(h * 0.80))).save(
        REVIEW / "desert-v3-final-crop.png"
    )
    w, h = forest.size
    forest.crop((0, int(h * 0.35), int(w * 0.40), int(h * 0.85))).save(
        REVIEW / "forest-v3-final-crop.png"
    )

    # Desert car variants — gear is left wall; fade before garage
    for p in sorted(IMG.glob("house-desert-installed-*.png")):
        if p.name == "house-desert-installed-v1.png":
            continue
        composite_site(desert, p, solid=0.36, fade=0.46)

    # Forest variants
    for p in sorted(IMG.glob("house-forest-installed-*.png")):
        if p.name == "house-forest-installed-v1.png":
            continue
        composite_site(forest, p, solid=0.30, fade=0.40)

    print("done")


if __name__ == "__main__":
    main()
