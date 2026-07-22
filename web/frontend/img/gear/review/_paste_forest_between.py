"""Paste the good forest between-only gear close-up onto forest installed plates."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

ASSETS = Path(r"C:\Users\andre\.cursor\projects\e-Cursor-Projects-ecoflow-ocean-ha\assets")
IMG = Path(r"E:\Cursor\Projects\ecoflow-ocean-ha\web\frontend\img")
REVIEW = IMG / "gear" / "review"


def soft_mask(w: int, h: int, solid: float, fade: float) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    px = mask.load()
    x0 = int(w * solid)
    x1 = int(w * fade)
    for x in range(w):
        if x <= x0:
            a = 255
        elif x >= x1:
            a = 0
        else:
            a = int(255 * (1 - (x - x0) / max(1, x1 - x0)))
        for y in range(h):
            px[x, y] = a
    # also soft top/bottom a bit so it blends with house framing
    return mask.filter(ImageFilter.GaussianBlur(radius=1.2))


def main() -> None:
    gear = Image.open(ASSETS / "forest-gear-between-only.png").convert("RGB")
    # Prefer v3b house for overall framing (cars etc. come from each target)
    # Gear close-up already framed similarly to left wall — resize to plate size
    # and keep mostly left content.

    master_out = IMG / "house-forest-installed-v1.png"
    # Start from v3b full house (better overall) then overlay good gear left
    base = Image.open(ASSETS / "house-forest-installed-v3b.png").convert("RGB")
    gear_r = gear.resize(base.size, Image.Resampling.LANCZOS)
    w, h = base.size
    # Gear close-up is more zoomed; scale gear content to match wall height better
    # Fit gear into left region: paste scaled gear into a destination box
    # Destination box approx left wall gear area on full house
    dst_box = (40, 360, 560, 900)  # x0,y0,x1,y1
    dw, dh = dst_box[2] - dst_box[0], dst_box[3] - dst_box[1]
    # Source from gear close-up: full equipment band
    src = gear.crop((int(gear.width * 0.08), int(gear.height * 0.18), int(gear.width * 0.78), int(gear.height * 0.92)))
    src = src.resize((dw, dh), Image.Resampling.LANCZOS)

    composed = base.copy()
    # Build local mask for dst box with soft right edge
    local = Image.new("L", (dw, dh), 255)
    lpx = local.load()
    fade_start = int(dw * 0.78)
    for x in range(dw):
        a = 255 if x < fade_start else int(255 * (1 - (x - fade_start) / max(1, dw - fade_start)))
        for y in range(dh):
            # soft top/bottom
            if y < 12:
                a2 = int(a * (y / 12))
            elif y > dh - 12:
                a2 = int(a * ((dh - y) / 12))
            else:
                a2 = a
            lpx[x, y] = a2
    composed.paste(src, (dst_box[0], dst_box[1]), local)
    composed.save(master_out)
    print("master", master_out)

    # preview
    composed.crop((150, 480, 560, 820)).resize((700, 560), Image.Resampling.LANCZOS).save(
        REVIEW / "forest-composited-zoom.png"
    )

    # Apply same overlay to all forest variants (keep right side cars)
    for p in sorted(IMG.glob("house-forest-installed-*.png")):
        if p.name == "house-forest-installed-v1.png":
            continue
        tgt = Image.open(p).convert("RGB")
        if tgt.size != composed.size:
            overlay = composed.resize(tgt.size, Image.Resampling.LANCZOS)
        else:
            overlay = composed
        # Soft left composite from updated master onto each variant
        mask = soft_mask(tgt.width, tgt.height, 0.32, 0.42)
        out = Image.composite(overlay, tgt, mask)
        out.save(p)
        print("updated", p.name)

    print("done")


if __name__ == "__main__":
    main()
