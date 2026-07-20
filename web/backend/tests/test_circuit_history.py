"""Tests for per-circuit energy integration."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.history import HistoryStore


@pytest.mark.asyncio
async def test_circuit_energy_integrates_watts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(str(Path(tmp) / "hist.sqlite3"))
        await store.open()
        try:
            base = 1_700_000_000.0
            await store.record_circuits(
                serial="PANEL1",
                site_id="desert",
                powers={4: 100.0, 38: 1000.0, 40: 1000.0},
                ts=base,
            )
            await store.record_circuits(
                serial="PANEL1",
                site_id="desert",
                powers={4: 100.0, 38: 1000.0, 40: 1000.0},
                ts=base + 3600.0,  # 1 hour later
            )
            data = await store.circuit_energy_totals(hours=24, site_id="desert")
            assert data["sample_count"] == 2
            by_ch = {c["channel"]: c for c in data["circuits"]}
            assert by_ch[4]["kwh"] == pytest.approx(0.1, abs=0.001)
            assert data["branch_kwh"] == pytest.approx(0.1, abs=0.001)
            assert data["feed_kwh"] == pytest.approx(2.0, abs=0.001)
            assert by_ch[38]["feed"] is True
        finally:
            await store.close()
