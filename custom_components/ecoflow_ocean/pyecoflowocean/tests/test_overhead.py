"""Tests for the inverter overhead (conversion-loss) model."""

from __future__ import annotations

from pyecoflowocean.overhead import estimate_inverter_overhead_w, measure_inverter_overhead_w


def test_estimate_matches_low_ground_truth_point() -> None:
    # solar≈16W quiet-night baseline should land close to the ~75W floor.
    assert estimate_inverter_overhead_w(16.0) == 75.7


def test_estimate_matches_high_ground_truth_point() -> None:
    # solar≈12,152W peak-throughput baseline should land close to 614W,
    # not the old flat 75W constant.
    estimate = estimate_inverter_overhead_w(12152.0)
    assert 600.0 < estimate < 630.0


def test_estimate_scales_with_solar() -> None:
    low = estimate_inverter_overhead_w(100.0)
    high = estimate_inverter_overhead_w(5000.0)
    assert high > low


def test_estimate_handles_none_and_negative() -> None:
    assert estimate_inverter_overhead_w(None) == 75.0
    assert estimate_inverter_overhead_w(-50.0) == 75.0


def test_measure_direct_residual() -> None:
    assert measure_inverter_overhead_w(solar_w=12152.0, battery_w=0.0, feed_w=11538.0) == 614.0


def test_measure_handles_negative_feed_sign() -> None:
    # feed_w can arrive signed (export convention); magnitude is what matters.
    assert measure_inverter_overhead_w(solar_w=1000.0, battery_w=0.0, feed_w=-900.0) == 100.0


def test_measure_clamps_at_zero() -> None:
    assert measure_inverter_overhead_w(solar_w=100.0, battery_w=50.0, feed_w=200.0) == 0.0


def test_measure_returns_none_when_inputs_missing() -> None:
    assert measure_inverter_overhead_w(solar_w=None, battery_w=0.0, feed_w=100.0) is None
    assert measure_inverter_overhead_w(solar_w=100.0, battery_w=0.0, feed_w=None) is None
