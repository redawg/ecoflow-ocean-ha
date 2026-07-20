"""SQLite power samples and energy integration for history charts."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

_LOGGER = logging.getLogger(__name__)

# Inverter feed legs — tracked, but excluded from branch-usage totals.
FEED_CIRCUITS: frozenset[int] = frozenset({38, 40})

SCHEMA = """
CREATE TABLE IF NOT EXISTS power_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    serial TEXT NOT NULL,
    product_type TEXT NOT NULL,
    site_id TEXT,
    solar_w REAL,
    grid_w REAL,
    battery_w REAL,
    home_w REAL,
    soc REAL,
    charge_w REAL
);
CREATE INDEX IF NOT EXISTS idx_power_samples_ts ON power_samples(ts);
CREATE INDEX IF NOT EXISTS idx_power_samples_serial_ts ON power_samples(serial, ts);

CREATE TABLE IF NOT EXISTS circuit_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    serial TEXT NOT NULL,
    site_id TEXT,
    powers_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_circuit_samples_ts ON circuit_samples(ts);
CREATE INDEX IF NOT EXISTS idx_circuit_samples_serial_ts ON circuit_samples(serial, ts);
CREATE INDEX IF NOT EXISTS idx_circuit_samples_site_ts ON circuit_samples(site_id, ts);

CREATE TABLE IF NOT EXISTS overhead_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    site_id TEXT,
    serial TEXT,
    solar_w REAL,
    home_w REAL,
    grid_w REAL,
    battery_w REAL,
    branch_w REAL,
    feed_w REAL,
    system_overhead_w REAL,
    panel_overhead_w REAL,
    inverter_overhead_w REAL,
    night INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_overhead_samples_ts ON overhead_samples(ts);
CREATE INDEX IF NOT EXISTS idx_overhead_samples_site_ts ON overhead_samples(site_id, ts);
"""


class HistoryStore:
    """Persist instantaneous power and derive kWh over time windows."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        # Migrate older DBs that predate site_id, then create the site index.
        async with self._db.execute("PRAGMA table_info(power_samples)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "site_id" not in cols:
            await self._db.execute("ALTER TABLE power_samples ADD COLUMN site_id TEXT")
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_power_samples_site_ts "
            "ON power_samples(site_id, ts)"
        )
        await self._db.commit()
        _LOGGER.info("History store ready at %s", self._db_path)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def record(
        self,
        *,
        serial: str,
        product_type: str,
        site_id: str = "",
        solar_w: float | None = None,
        grid_w: float | None = None,
        battery_w: float | None = None,
        home_w: float | None = None,
        soc: float | None = None,
        charge_w: float | None = None,
        ts: float | None = None,
    ) -> None:
        if self._db is None:
            return
        await self._db.execute(
            """
            INSERT INTO power_samples
              (ts, serial, product_type, site_id, solar_w, grid_w, battery_w, home_w, soc, charge_w)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts if ts is not None else time.time(),
                serial,
                product_type,
                site_id or None,
                solar_w,
                grid_w,
                battery_w,
                home_w,
                soc,
                charge_w,
            ),
        )
        await self._db.commit()

    async def record_circuits(
        self,
        *,
        serial: str,
        site_id: str = "",
        powers: dict[int, float],
        ts: float | None = None,
    ) -> None:
        """Persist one panel snapshot of per-channel usage watts (absolute)."""
        if self._db is None or not powers:
            return
        cleaned: dict[str, float] = {}
        for key, value in powers.items():
            try:
                channel = int(key)
                watts = abs(float(value))
            except (TypeError, ValueError):
                continue
            if channel < 1 or channel > 40:
                continue
            cleaned[str(channel)] = round(watts, 3)
        if not cleaned:
            return
        await self._db.execute(
            """
            INSERT INTO circuit_samples (ts, serial, site_id, powers_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                ts if ts is not None else time.time(),
                serial,
                site_id or None,
                json.dumps(cleaned, separators=(",", ":")),
            ),
        )
        await self._db.commit()

    async def prune(self, retention_days: int) -> int:
        if self._db is None or retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        cursor = await self._db.execute(
            "DELETE FROM power_samples WHERE ts < ?",
            (cutoff,),
        )
        deleted = cursor.rowcount or 0
        cursor = await self._db.execute(
            "DELETE FROM circuit_samples WHERE ts < ?",
            (cutoff,),
        )
        deleted += cursor.rowcount or 0
        cursor = await self._db.execute(
            "DELETE FROM overhead_samples WHERE ts < ?",
            (cutoff,),
        )
        deleted += cursor.rowcount or 0
        await self._db.commit()
        return deleted

    async def record_overhead(
        self,
        *,
        site_id: str = "",
        serial: str = "",
        solar_w: float | None = None,
        home_w: float | None = None,
        grid_w: float | None = None,
        battery_w: float | None = None,
        branch_w: float | None = None,
        feed_w: float | None = None,
        system_overhead_w: float | None = None,
        panel_overhead_w: float | None = None,
        inverter_overhead_w: float | None = None,
        night: bool = False,
        ts: float | None = None,
    ) -> None:
        if self._db is None:
            return
        await self._db.execute(
            """
            INSERT INTO overhead_samples (
              ts, site_id, serial, solar_w, home_w, grid_w, battery_w,
              branch_w, feed_w, system_overhead_w, panel_overhead_w,
              inverter_overhead_w, night
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts if ts is not None else time.time(),
                site_id or None,
                serial or None,
                solar_w,
                home_w,
                grid_w,
                battery_w,
                branch_w,
                feed_w,
                system_overhead_w,
                panel_overhead_w,
                inverter_overhead_w,
                1 if night else 0,
            ),
        )
        await self._db.commit()

    async def overhead_series(
        self,
        *,
        hours: float = 18.0,
        site_id: str | None = None,
        bucket_minutes: int = 5,
        night_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Bucketed panel/inverter overhead for overnight charts."""
        if self._db is None:
            return []
        since = time.time() - hours * 3600
        bucket_s = max(bucket_minutes, 1) * 60
        params: list[Any] = [since]
        where = "ts >= ?"
        if site_id:
            where += " AND site_id = ?"
            params.append(site_id)
        if night_only:
            where += " AND night = 1"

        sql = f"""
            SELECT
              CAST(ts / {bucket_s} AS INTEGER) * {bucket_s} AS bucket_ts,
              AVG(solar_w) AS solar_w,
              AVG(home_w) AS home_w,
              AVG(branch_w) AS branch_w,
              AVG(feed_w) AS feed_w,
              AVG(system_overhead_w) AS system_overhead_w,
              AVG(panel_overhead_w) AS panel_overhead_w,
              AVG(inverter_overhead_w) AS inverter_overhead_w,
              MAX(night) AS night
            FROM overhead_samples
            WHERE {where}
            GROUP BY bucket_ts
            ORDER BY bucket_ts
        """
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "ts": row["bucket_ts"],
                "solar_w": row["solar_w"],
                "home_w": row["home_w"],
                "branch_w": row["branch_w"],
                "feed_w": row["feed_w"],
                "system_overhead_w": row["system_overhead_w"],
                "panel_overhead_w": row["panel_overhead_w"],
                "inverter_overhead_w": row["inverter_overhead_w"],
                "night": bool(row["night"]),
            }
            for row in rows
        ]

    async def overhead_stats(
        self,
        *,
        hours: float = 18.0,
        site_id: str | None = None,
    ) -> dict[str, Any]:
        """Median panel/inverter draw across all buckets with a reading.

        Panel aux (hall_total−channel_sum) and inverter aux
        (solar−battery−feed) are both direct/estimated readings available
        at any time of day now, not just overnight — so this aggregates
        every bucket that has a value rather than filtering to `night=1`
        samples (that flag now only fires in the rare case the panel's
        967−966 reading itself is unavailable and we fall back to the
        quiet-night split instead).
        """
        series = await self.overhead_series(
            hours=hours, site_id=site_id, bucket_minutes=5, night_only=False
        )
        panels = [
            float(p["panel_overhead_w"])
            for p in series
            if p.get("panel_overhead_w") is not None
        ]
        invs = [
            float(p["inverter_overhead_w"])
            for p in series
            if p.get("inverter_overhead_w") is not None
        ]
        systems = [
            float(p["system_overhead_w"])
            for p in series
            if p.get("system_overhead_w") is not None
        ]

        def _med(vals: list[float]) -> float | None:
            if not vals:
                return None
            vals = sorted(vals)
            mid = len(vals) // 2
            if len(vals) % 2:
                return round(vals[mid], 1)
            return round((vals[mid - 1] + vals[mid]) / 2.0, 1)

        return {
            "hours": hours,
            "sample_buckets": len(series),
            "panel_overhead_w_median": _med(panels),
            "inverter_overhead_w_median": _med(invs),
            "system_overhead_w_median": _med(systems),
        }

    async def power_series(
        self,
        *,
        hours: float = 24.0,
        serial: str | None = None,
        site_id: str | None = None,
        bucket_minutes: int = 5,
    ) -> list[dict[str, Any]]:
        """Return averaged power buckets for charting."""
        if self._db is None:
            return []

        since = time.time() - hours * 3600
        bucket_s = max(bucket_minutes, 1) * 60
        params: list[Any] = [since]
        where = "ts >= ?"
        if serial:
            where += " AND serial = ?"
            params.append(serial)
        if site_id:
            where += " AND site_id = ?"
            params.append(site_id)

        sql = f"""
            SELECT
              CAST(ts / {bucket_s} AS INTEGER) * {bucket_s} AS bucket_ts,
              AVG(solar_w) AS solar_w,
              AVG(grid_w) AS grid_w,
              AVG(battery_w) AS battery_w,
              AVG(home_w) AS home_w,
              AVG(soc) AS soc,
              AVG(charge_w) AS charge_w
            FROM power_samples
            WHERE {where}
            GROUP BY bucket_ts
            ORDER BY bucket_ts
        """
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "ts": row["bucket_ts"],
                "solar_w": row["solar_w"],
                "grid_w": row["grid_w"],
                "battery_w": row["battery_w"],
                "home_w": row["home_w"],
                "soc": row["soc"],
                "charge_w": row["charge_w"],
            }
            for row in rows
        ]

    async def energy_totals(
        self,
        *,
        hours: float = 24.0,
        serial: str | None = None,
        site_id: str | None = None,
    ) -> dict[str, float]:
        """Trapezoidal integration of power samples → kWh."""
        if self._db is None:
            return {
                "solar_kwh": 0.0,
                "home_kwh": 0.0,
                "grid_import_kwh": 0.0,
                "grid_export_kwh": 0.0,
                "battery_charge_kwh": 0.0,
                "battery_discharge_kwh": 0.0,
                "ev_charge_kwh": 0.0,
            }

        since = time.time() - hours * 3600
        params: list[Any] = [since]
        where = "ts >= ?"
        if serial:
            where += " AND serial = ?"
            params.append(serial)
        if site_id:
            where += " AND site_id = ?"
            params.append(site_id)

        async with self._db.execute(
            f"""
            SELECT ts, solar_w, grid_w, battery_w, home_w, charge_w
            FROM power_samples
            WHERE {where}
            ORDER BY ts
            """,
            params,
        ) as cursor:
            rows = await cursor.fetchall()
        return _integrate_rows(rows)

    async def circuit_energy_totals(
        self,
        *,
        hours: float = 24.0,
        serial: str | None = None,
        site_id: str | None = None,
    ) -> dict[str, Any]:
        """Integrate per-circuit watts → kWh for the window."""
        empty = {
            "hours": hours,
            "sample_count": 0,
            "since_ts": None,
            "until_ts": None,
            "circuits": [],
            "branch_kwh": 0.0,
            "feed_kwh": 0.0,
        }
        if self._db is None:
            return empty

        since = time.time() - hours * 3600
        params: list[Any] = [since]
        where = "ts >= ?"
        if serial:
            where += " AND serial = ?"
            params.append(serial)
        if site_id:
            where += " AND site_id = ?"
            params.append(site_id)

        async with self._db.execute(
            f"""
            SELECT ts, powers_json
            FROM circuit_samples
            WHERE {where}
            ORDER BY ts
            """,
            params,
        ) as cursor:
            rows = await cursor.fetchall()

        if len(rows) < 2:
            empty["sample_count"] = len(rows)
            if rows:
                empty["since_ts"] = rows[0]["ts"]
                empty["until_ts"] = rows[-1]["ts"]
            return empty

        kwh: dict[int, float] = {}
        watt_sum: dict[int, float] = {}
        watt_n: dict[int, int] = {}

        for prev, curr in zip(rows, rows[1:]):
            dt_h = max((curr["ts"] - prev["ts"]) / 3600.0, 0.0)
            if dt_h <= 0:
                continue
            # Cap gaps (hub restarts) so one long hole doesn't invent energy.
            if dt_h > 0.25:  # 15 minutes
                continue
            prev_map = _parse_powers(prev["powers_json"])
            curr_map = _parse_powers(curr["powers_json"])
            channels = set(prev_map) | set(curr_map)
            for ch in channels:
                avg_w = (prev_map.get(ch, 0.0) + curr_map.get(ch, 0.0)) / 2.0
                kwh[ch] = kwh.get(ch, 0.0) + max(avg_w, 0.0) * dt_h / 1000.0

        for row in rows:
            for ch, watts in _parse_powers(row["powers_json"]).items():
                watt_sum[ch] = watt_sum.get(ch, 0.0) + watts
                watt_n[ch] = watt_n.get(ch, 0) + 1

        circuits = []
        branch_kwh = 0.0
        feed_kwh = 0.0
        for ch in sorted(set(kwh) | set(watt_sum)):
            energy = round(kwh.get(ch, 0.0), 3)
            n = watt_n.get(ch, 0)
            avg_w = round(watt_sum[ch] / n, 1) if n else 0.0
            is_feed = ch in FEED_CIRCUITS
            if is_feed:
                feed_kwh += energy
            else:
                branch_kwh += energy
            circuits.append(
                {
                    "channel": ch,
                    "kwh": energy,
                    "avg_w": avg_w,
                    "feed": is_feed,
                }
            )

        circuits.sort(key=lambda c: (-float(c["kwh"]), int(c["channel"])))
        return {
            "hours": hours,
            "sample_count": len(rows),
            "since_ts": rows[0]["ts"],
            "until_ts": rows[-1]["ts"],
            "circuits": circuits,
            "branch_kwh": round(branch_kwh, 3),
            "feed_kwh": round(feed_kwh, 3),
        }


def _parse_powers(raw: str | None) -> dict[int, float]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    out: dict[int, float] = {}
    if not isinstance(data, dict):
        return out
    for key, value in data.items():
        try:
            out[int(key)] = abs(float(value))
        except (TypeError, ValueError):
            continue
    return out


def _integrate_rows(rows: list[aiosqlite.Row]) -> dict[str, float]:
    totals = {
        "solar_kwh": 0.0,
        "home_kwh": 0.0,
        "grid_import_kwh": 0.0,
        "grid_export_kwh": 0.0,
        "battery_charge_kwh": 0.0,
        "battery_discharge_kwh": 0.0,
        "ev_charge_kwh": 0.0,
    }
    if len(rows) < 2:
        return totals

    for prev, curr in zip(rows, rows[1:]):
        dt_h = max((curr["ts"] - prev["ts"]) / 3600.0, 0.0)
        if dt_h <= 0:
            continue

        def avg(a: Any, b: Any) -> float:
            va = float(a or 0.0)
            vb = float(b or 0.0)
            return (va + vb) / 2.0

        solar = avg(prev["solar_w"], curr["solar_w"])
        home = avg(prev["home_w"], curr["home_w"])
        grid = avg(prev["grid_w"], curr["grid_w"])
        batt = avg(prev["battery_w"], curr["battery_w"])
        charge = avg(prev["charge_w"], curr["charge_w"])

        totals["solar_kwh"] += max(solar, 0.0) * dt_h / 1000.0
        totals["home_kwh"] += max(home, 0.0) * dt_h / 1000.0
        totals["ev_charge_kwh"] += max(charge, 0.0) * dt_h / 1000.0
        if grid >= 0:
            totals["grid_import_kwh"] += grid * dt_h / 1000.0
        else:
            totals["grid_export_kwh"] += abs(grid) * dt_h / 1000.0
        if batt >= 0:
            totals["battery_charge_kwh"] += batt * dt_h / 1000.0
        else:
            totals["battery_discharge_kwh"] += abs(batt) * dt_h / 1000.0

    return {k: round(v, 3) for k, v in totals.items()}
