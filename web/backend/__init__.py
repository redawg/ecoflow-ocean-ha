"""EcoFlow Ocean web backend package."""

from __future__ import annotations

import sys
from pathlib import Path

# Bundled client lives next to the HA custom component (and in the container at /app).
_ROOT = Path(__file__).resolve().parents[2]
_CLIENT = _ROOT / "custom_components" / "ecoflow_ocean"
if _CLIENT.is_dir() and str(_CLIENT) not in sys.path:
    sys.path.insert(0, str(_CLIENT))
