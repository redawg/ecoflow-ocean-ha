"""Prepare HD review cutouts: keep live style, sharper sources."""
from __future__ import annotations

import os
from PIL import Image

ASSETS = r"C:\Users\andre\.cursor\projects\e-Cursor-Projects-ecoflow-ocean-ha\assets"
GEAR = r"E:\Cursor\Projects\ecoflow-ocean-ha\web\frontend\img\gear"
REVIEW = os.path.join(GEAR, "review")
REF = os.path.join(GEAR, "ref")


def remove_bg(path: str, out_path: str, dark: int = 28, light: int = 250) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if r < dark and g < dark and b < dark:
                px[x, y] = (r, g, b, 0)
            elif r > light and g > light and b > light:
                px[x, y] = (r, g, b, 0)
    im.save(out_path)
    print("cut", os.path.basename(out_path), im.size)
    return im


def trim_alpha(im: Image.Image, pad: int = 8) -> Image.Image:
    bbox = im.getbbox()
    if not bbox:
        return im
    l, t, r, b = bbox
    l = max(0, l - pad)
    t = max(0, t - pad)
    r = min(im.width, r + pad)
    b = min(im.height, b + pad)
    return im.crop((l, t, r, b))


def main() -> None:
    os.makedirs(REVIEW, exist_ok=True)

    # HD inverter / battery from generators
    inv_src = os.path.join(ASSETS, "inverter-hd-v3.png")
    bat_src = os.path.join(ASSETS, "battery-hd-v3.png")
    Image.open(inv_src).convert("RGBA").save(os.path.join(REVIEW, "hd-inverter-v3.png"))
    Image.open(bat_src).convert("RGBA").save(os.path.join(REVIEW, "hd-battery-v3.png"))
    inv = remove_bg(inv_src, os.path.join(REVIEW, "hd-inverter-v3-cut.png"))
    bat = remove_bg(bat_src, os.path.join(REVIEW, "hd-battery-v3-cut.png"))
    trim_alpha(inv).save(os.path.join(REVIEW, "hd-inverter-v3-cut.png"))
    trim_alpha(bat).save(os.path.join(REVIEW, "hd-battery-v3-cut.png"))

    # Official EV charger — clean + upscale for HD dashboard use
    ev_off = Image.open(os.path.join(REF, "ev-charger-product.png")).convert("RGBA")
    ev_cut = remove_bg(
        os.path.join(REF, "ev-charger-product.png"),
        os.path.join(REVIEW, "hd-ev-official-cut.png"),
        dark=18,
        light=252,
    )
    ev_cut = trim_alpha(ev_cut, pad=4)
    # Upscale ~1.6x for sharper UI use while keeping official look
    scale = 1.6
    ev_hd = ev_cut.resize(
        (int(ev_cut.width * scale), int(ev_cut.height * scale)),
        Image.Resampling.LANCZOS,
    )
    ev_hd.save(os.path.join(REVIEW, "hd-ev-official-2x.png"))
    print("ev hd", ev_hd.size)

    # Generated EV if present
    for name in ("ev-charger-hd-v3.png", "ev-charger-hd-v3b.png"):
        p = os.path.join(ASSETS, name)
        if os.path.exists(p):
            out = os.path.join(REVIEW, name.replace(".png", "-cut.png").replace("ev-charger", "hd-ev"))
            remove_bg(p, out)
            Image.open(p).convert("RGBA").save(
                os.path.join(REVIEW, name.replace("ev-charger", "hd-ev"))
            )

    # Side-by-side compare strips: current (upscaled soft) vs HD
    def make_compare(current_name: str, hd_path: str, out_name: str, label_h: int = 900) -> None:
        cur = Image.open(os.path.join(GEAR, current_name)).convert("RGBA")
        hd = Image.open(hd_path).convert("RGBA")

        def fit(im: Image.Image) -> Image.Image:
            w = max(1, int(im.width * (label_h / im.height)))
            return im.resize((w, label_h), Image.Resampling.LANCZOS)

        c = fit(cur)
        h = fit(hd)
        gap = 40
        canvas = Image.new("RGBA", (c.width + h.width + gap + 80, label_h + 80), (18, 20, 24, 255))
        canvas.paste(c, (40, 40), c)
        canvas.paste(h, (40 + c.width + gap, 40), h)
        canvas.convert("RGB").save(os.path.join(REVIEW, out_name), quality=93)
        print("compare", out_name)

    make_compare("inverter-tower.png", os.path.join(REVIEW, "hd-inverter-v3-cut.png"), "compare-inverter.jpg")
    make_compare("battery-tower.png", os.path.join(REVIEW, "hd-battery-v3-cut.png"), "compare-battery.jpg")
    make_compare("ev-charger.png", os.path.join(REVIEW, "hd-ev-official-2x.png"), "compare-ev.jpg")

    print("done")


if __name__ == "__main__":
    main()
