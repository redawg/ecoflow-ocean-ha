from PIL import Image
import os

gear = r"E:\Cursor\Projects\ecoflow-ocean-ha\web\frontend\img\gear"
for f in [
    "inverter-tower.png",
    "battery-tower.png",
    "ev-charger.png",
    "inverter-angle.png",
    "battery-angle.png",
    "inverter.png",
    "battery.png",
]:
    p = os.path.join(gear, f)
    im = Image.open(p)
    print(f, im.size, im.mode, os.path.getsize(p))

ref = os.path.join(gear, "ref")
for f in sorted(os.listdir(ref)):
    if f.endswith((".png", ".jpg")):
        p = os.path.join(ref, f)
        im = Image.open(p)
        print("ref", f, im.size, os.path.getsize(p))
