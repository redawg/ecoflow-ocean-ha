"""Inverter self-consumption ("conversion loss") model.

The inverter's own PCS electronics/fans draw a small amount of power that
never shows up as panel feed (circuits 38/40) or battery current — it's
overhead the system pays to run itself. That draw isn't fixed; it scales
with throughput, so a flat constant badly undershoots at high power.

Two ground-truth points anchor the model:

  low:  solar≈16W,     feed≈138W,     app-vs-balance gap ≈70-80W
        (2026-07-18, quiet night, near-idle throughput)
  high: solar≈12,152W, feed≈11,538W,  battery≈0 (full SOC, BMS idle)
        → residual = solar − battery − feed = 614.4W
        (2026-07-19, 12:53pm, near-peak throughput)

Fit base + k*solar through both points:

  k = (614.4 - 75) / 12152 ≈ 0.0444  (~4.4% of solar throughput)

As more full-SOC (or otherwise battery-independent) residual measurements
come in, refit these two constants rather than re-deriving by hand.
"""

from __future__ import annotations

INVERTER_BASE_OVERHEAD_W = 75.0
INVERTER_OVERHEAD_SOLAR_FRACTION = 0.0444


def estimate_inverter_overhead_w(solar_w: float | None) -> float:
    """Model-based overhead estimate, scaled by solar throughput.

    Use this whenever a direct residual measurement isn't available — most
    notably as the assumption baked into the solar/feed battery-balance
    formula, where battery power is the unknown being solved for and can't
    also be used to measure overhead in the same step.
    """
    solar = max(0.0, float(solar_w or 0.0))
    return round(INVERTER_BASE_OVERHEAD_W + INVERTER_OVERHEAD_SOLAR_FRACTION * solar, 1)


def measure_inverter_overhead_w(
    *,
    solar_w: float | None,
    battery_w: float | None,
    feed_w: float | None,
) -> float | None:
    """Direct residual: solar − battery − feed, clamped at zero.

    Only a genuine measurement (not circular with `estimate_inverter_overhead_w`
    above) when `battery_w` is an independent reading — e.g. raw per-pack
    current, or a BMS-idle full-SOC value — rather than itself derived from
    this same solar/feed balance.
    """
    if solar_w is None or feed_w is None:
        return None
    battery = battery_w or 0.0
    return max(0.0, float(solar_w) - float(battery) - abs(float(feed_w)))
