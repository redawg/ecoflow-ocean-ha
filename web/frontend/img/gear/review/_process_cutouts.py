"""Strip studio backgrounds from review cutouts and build a wall-row composite."""
from __future__ import annotations

import os
from PIL import Image

REVIEW = os.path.dirname(os.path.abspath(__file__))
ASSETS = r"C:\Users\andre\.cursor\projects\e-Cursor-Projects-ecoflow-ocean-ha\assets"
REF = os.path.join(os.path.dirname(REVIEW), "ref")


def remove_bg(path: str, out_path: str, dark: int = 35, light: int = 248) -> None:
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
    print("cut", out_path, im.size)


def main() -> None:
    os.makedirs(REVIEW, exist_ok=True)

    copies = {
        "01-panel-closed-v2.png": os.path.join(ASSETS, "panel-closed-v2.png"),
        "02-inverter-front-v2.png": os.path.join(ASSETS, "inverter-front-v2.png"),
        "03-battery-front-v2.png": os.path.join(ASSETS, "battery-front-v2.png"),
        "04-ev-charger-front-v2.png": os.path.join(ASSETS, "ev-charger-front-v2.png"),
        "01b-panel-open-official.png": os.path.join(REF, "smart-panel-product.png"),
        "04b-ev-charger-official.png": os.path.join(REF, "ev-charger-product.png"),
    }
    for name, src in copies.items():
        dst = os.path.join(REVIEW, name)
        Image.open(src).convert("RGBA").save(dst)
        print("copied", name)

    for name in [
        "01-panel-closed-v2.png",
        "02-inverter-front-v2.png",
        "03-battery-front-v2.png",
        "04-ev-charger-front-v2.png",
        "01b-panel-open-official.png",
        "04b-ev-charger-official.png",
    ]:
        src = os.path.join(REVIEW, name)
        dst = os.path.join(REVIEW, name.replace(".png", "-cut.png"))
        remove_bg(src, dst)

    # Wall row: panel | inverter | 3 batteries (dashboard/house preview)
    panel = Image.open(os.path.join(REVIEW, "01-panel-closed-v2-cut.png"))
    inv = Image.open(os.path.join(REVIEW, "02-inverter-front-v2-cut.png"))
    bat = Image.open(os.path.join(REVIEW, "03-battery-front-v2-cut.png"))

    target_h = 900

    def fit_h(im: Image.Image, h: int) -> Image.Image:
        w = max(1, int(im.width * (h / im.height)))
        return im.resize((w, h), Image.Resampling.LANCZOS)

    panel_r = fit_h(panel, target_h)
    inv_r = fit_h(inv, int(target_h * 0.96))
    bat_r = fit_h(bat, target_h)
    gap = 28
    n_bats = 3
    total_w = panel_r.width + gap + inv_r.width + gap + n_bats * bat_r.width + (n_bats - 1) * 12 + 80
    total_h = target_h + 80
    row = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    x = 40
    y_base = total_h - 40
    row.paste(panel_r, (x, y_base - panel_r.height), panel_r)
    x += panel_r.width + gap
    row.paste(inv_r, (x, y_base - inv_r.height), inv_r)
    x += inv_r.width + gap
    for _ in range(n_bats):
        row.paste(bat_r, (x, y_base - bat_r.height), bat_r)
        x += bat_r.width + 12
    row_path = os.path.join(REVIEW, "05-wall-row-preview.png")
    row.save(row_path)
    print("row", row_path, row.size)

    # Dark preview card for review HTML readability
    card = Image.new("RGBA", (row.width + 80, row.height + 80), (24, 26, 30, 255))
    card.paste(row, (40, 40), row)
    card.convert("RGB").save(os.path.join(REVIEW, "05-wall-row-preview-dark.jpg"), quality=92)

    for f in sorted(os.listdir(REVIEW)):
        if f.endswith((".png", ".jpg")):
            p = os.path.join(REVIEW, f)
            print(f, os.path.getsize(p))


if __name__ == "__main__":
    main()
