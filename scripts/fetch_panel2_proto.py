#!/usr/bin/env python3
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "custom_components/ecoflow_ocean/pyecoflowocean/panel2.proto"

t = urllib.request.urlopen(
    "https://raw.githubusercontent.com/foxthefox/ioBroker.ecoflow-mqtt/main/lib/dict_data/ef_panel2_data.js",
    timeout=60,
).read().decode()
start = t.index("const protoSource = `") + len("const protoSource = `")
end = t.index("`;", start)
proto = t[start:end]
OUT.write_text(proto, encoding="utf-8")
nums = [int(x) for x in re.findall(r"=\s*(\d{3,4});", proto)]
print(f"Wrote {OUT} ({len(proto)} bytes)")
print("Header" in proto, "DisplayPropertyUpload" in proto)
print("max field", max(nums) if nums else 0)
print(">1000", sorted(set(n for n in nums if n >= 1000))[:30])
