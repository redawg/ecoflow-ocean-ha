"""Multi-site EcoFlow hub: one MQTT session per account, filtered site views."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from pyecoflowocean import EcoflowOcean
from pyecoflowocean.const import (
    PRODUCT_TYPE_EV_CHARGER,
    PRODUCT_TYPE_OCEAN_PANEL,
    PRODUCT_TYPE_POWER_OCEAN_PRO,
)
from pyecoflowocean.overhead import estimate_inverter_overhead_w, measure_inverter_overhead_w
from .config import Settings, SiteConfig
from .history import HistoryStore
from .mqtt_multi import MultiDeviceMqtt

_LOGGER = logging.getLogger(__name__)

INVERTER_TYPES = {"83", "85", "86", "87", "88"}


@dataclass
class DeviceSnapshot:
    serial: str
    name: str
    product_type: str
    kind: str
    site_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    online: bool | None = None
    mqtt: bool = False
    error: str | None = None


@dataclass
class SiteFilter:
    id: str
    label: str
    serials: frozenset[str] | None


class AccountHub:
    """One EcoFlow account: shared login/MQTT, one or more site filters."""

    def __init__(
        self,
        settings: Settings,
        history: HistoryStore,
        sites: list[SiteConfig],
    ) -> None:
        if not sites:
            raise ValueError("AccountHub requires at least one site")
        self.settings = settings
        self.history = history
        self.email = sites[0].email
        self.password = sites[0].password
        self.region = sites[0].region
        self.site_filters: dict[str, SiteFilter] = {
            s.id: SiteFilter(id=s.id, label=s.label, serials=s.serials) for s in sites
        }
        self.devices: dict[str, DeviceSnapshot] = {}
        self._api: EcoflowOcean | None = None
        self._mqtt: MultiDeviceMqtt | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self.ready = False
        self.last_error: str | None = None

    @property
    def site_ids(self) -> list[str]:
        return list(self.site_filters.keys())

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await self._discover_and_connect(loop)
            self.ready = True
            self.last_error = None
        except Exception as err:
            self.last_error = str(err)
            _LOGGER.exception("Account hub start failed for %s: %s", self.email, err)
            raise

        self._tasks.append(asyncio.create_task(self._rest_poll_loop(), name=f"rest-{self.email}"))
        self._tasks.append(asyncio.create_task(self._sample_loop(), name=f"sample-{self.email}"))
        self._tasks.append(asyncio.create_task(self._prune_loop(), name=f"prune-{self.email}"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._mqtt is not None:
            await self._mqtt.stop()
            self._mqtt = None
        if self._api is not None:
            await self._api.close()
            self._api = None

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    _ = queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:
                    dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)

    def site_summary(self, site_id: str) -> dict[str, Any]:
        filt = self.site_filters[site_id]
        devices = self._devices_for_site(site_id)
        return {
            "id": site_id,
            "label": filt.label,
            "ready": self.ready,
            "error": self.last_error,
            "mqtt_connected": bool(self._mqtt and self._mqtt.is_connected),
            "device_count": len(devices),
            "online_count": sum(1 for d in devices if d.online is True),
        }

    def overview(self, site_id: str) -> dict[str, Any]:
        if site_id not in self.site_filters:
            raise KeyError(site_id)
        filt = self.site_filters[site_id]
        devices = self._devices_for_site(site_id)
        inverter = next((d for d in devices if d.kind == "inverter"), None)
        panel = next((d for d in devices if d.kind == "panel"), None)
        ev = next((d for d in devices if d.kind == "ev_charger"), None)
        return {
            "site_id": site_id,
            "site_label": filt.label,
            "ready": self.ready,
            "error": self.last_error,
            "devices": [self._device_dict(d) for d in devices],
            "inverter": self._device_dict(inverter) if inverter else None,
            "panel": self._device_dict(panel) if panel else None,
            "ev_charger": self._device_dict(ev) if ev else None,
            "power_flow": self._power_flow(inverter, panel, ev),
            "mqtt_connected": bool(self._mqtt and self._mqtt.is_connected),
        }

    async def refresh_device(self, serial: str) -> DeviceSnapshot | None:
        snap = self.devices.get(serial)
        if not snap or not self._api:
            return None
        await self._refresh_one(serial, snap)
        return snap

    def _devices_for_site(self, site_id: str) -> list[DeviceSnapshot]:
        return [d for d in self.devices.values() if d.site_id == site_id]

    def _assign_site(self, serial: str) -> str | None:
        """Map a serial to a site filter for this account."""
        upper = serial.upper()
        claimed = [
            filt for filt in self.site_filters.values() if filt.serials and upper in filt.serials
        ]
        if len(claimed) == 1:
            return claimed[0].id
        if len(claimed) > 1:
            _LOGGER.warning("Serial %s matches multiple sites; using %s", serial, claimed[0].id)
            return claimed[0].id

        # Unlisted serial: assign to the single catch-all site (serials=None), if any.
        catch_alls = [filt for filt in self.site_filters.values() if filt.serials is None]
        if len(catch_alls) == 1:
            return catch_alls[0].id
        if len(catch_alls) > 1:
            _LOGGER.warning(
                "Serial %s has no SITE_*_SERIALS match and multiple catch-all sites; skipping",
                serial,
            )
            return None
        _LOGGER.info("Serial %s not assigned to any site filter; skipping", serial)
        return None

    async def _discover_and_connect(self, loop: asyncio.AbstractEventLoop) -> None:
        api = EcoflowOcean(
            self.email,
            self.password,
            region=self.region,
            product_type=PRODUCT_TYPE_POWER_OCEAN_PRO,
        )
        await api.login()
        discovered = await api.get_devices()
        if not discovered:
            await api.close()
            raise RuntimeError(f"No EcoFlow Ocean devices found for {self.email}")

        primary = next(
            (d for d in discovered if d.product_type in INVERTER_TYPES),
            discovered[0],
        )
        api._serial_number = primary.serial_number  # noqa: SLF001
        api._product_type = primary.product_type  # noqa: SLF001
        await api._detect_region(primary.serial_number, primary.product_type)  # noqa: SLF001

        self._api = api
        _LOGGER.info("Account %s: discovered %d device(s)", self.email, len(discovered))

        mqtt_map: dict[str, str] = {}
        for device in discovered:
            kind = _kind_for(device.product_type)
            if kind == "other":
                _LOGGER.info(
                    "Skipping unsupported product type %s (%s)",
                    device.product_type,
                    device.serial_number,
                )
                continue
            site_id = self._assign_site(device.serial_number)
            if site_id is None:
                continue
            snap = DeviceSnapshot(
                serial=device.serial_number,
                name=device.name or device.serial_number,
                product_type=device.product_type,
                kind=kind,
                site_id=site_id,
            )
            self.devices[device.serial_number] = snap
            mqtt_map[device.serial_number] = device.product_type
            await self._refresh_one(device.serial_number, snap)

        if not mqtt_map:
            raise RuntimeError(
                f"No supported Ocean devices assigned to sites for {self.email}. "
                "Check SITE_*_SERIALS."
            )

        async def _on_update(serial: str) -> None:
            await self._on_mqtt_update(serial)

        self._mqtt = MultiDeviceMqtt(
            api._auth,  # noqa: SLF001
            mqtt_map,
            loop=loop,
            on_update=_on_update,
        )
        try:
            await self._mqtt.start()
            for snap in self.devices.values():
                snap.mqtt = True
        except Exception as err:
            _LOGGER.warning("MQTT unavailable for %s, REST-only: %s", self.email, err)
            for snap in self.devices.values():
                snap.mqtt = False

    async def _on_mqtt_update(self, serial: str) -> None:
        snap = self.devices.get(serial)
        if not snap or not self._mqtt:
            return
        state = self._mqtt.get_state(serial, snap.product_type)
        if state is None:
            return
        snap.state = state.as_dict()
        snap.online = getattr(state, "online", True)
        snap.error = None
        await self.publish(
            {
                "type": "site",
                "site_id": snap.site_id,
                "data": self.overview(snap.site_id) if snap.site_id else None,
            }
        )

    async def _refresh_one(self, serial: str, snap: DeviceSnapshot) -> None:
        if not self._api:
            return
        try:
            if self._mqtt:
                mqtt_state = self._mqtt.get_state(serial, snap.product_type)
                if mqtt_state is not None and any(
                    v is not None and v != 0 and v != {}
                    for k, v in mqtt_state.as_dict().items()
                    if k not in ("serial_number", "updated_at", "online")
                ):
                    snap.state = mqtt_state.as_dict()
                    snap.online = True
                    snap.error = None
                    if snap.site_id:
                        await self.publish(
                            {
                                "type": "site",
                                "site_id": snap.site_id,
                                "data": self.overview(snap.site_id),
                            }
                        )
                    return

            state = await self._api.get_system_state(serial, product_type=snap.product_type)
            snap.state = state.as_dict()
            snap.online = getattr(state, "online", None)
            snap.error = None
            if snap.site_id:
                await self.publish(
                    {
                        "type": "site",
                        "site_id": snap.site_id,
                        "data": self.overview(snap.site_id),
                    }
                )
        except Exception as err:
            snap.error = str(err)
            _LOGGER.warning("Refresh failed for %s: %s", serial, err)

    async def _rest_poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.rest_poll_interval_s)
            async with self._lock:
                for serial, snap in list(self.devices.items()):
                    await self._refresh_one(serial, snap)

    async def _sample_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.sample_interval_s)
            try:
                await self._sample_once()
            except Exception as err:
                _LOGGER.warning("History sample failed: %s", err)

    async def _sample_once(self) -> None:
        for snap in self.devices.values():
            state = snap.state
            site_id = snap.site_id or ""
            if snap.kind == "inverter":
                await self.history.record(
                    serial=snap.serial,
                    product_type=snap.product_type,
                    site_id=site_id,
                    solar_w=_num(state.get("solar_power_w")),
                    grid_w=_num(state.get("grid_power_w")),
                    battery_w=_num(state.get("battery_power_w")),
                    home_w=_num(state.get("home_power_w")),
                    soc=_num(state.get("battery_soc")),
                )
            elif snap.kind == "panel":
                await self.history.record(
                    serial=snap.serial,
                    product_type=snap.product_type,
                    site_id=site_id,
                    home_w=_num(state.get("home_power_w")),
                    grid_w=_num(state.get("grid_import_power_w")),
                    charge_w=_num(state.get("ev_charge_power_w")),
                )
                circuit_powers = state.get("circuit_power_w") or {}
                if isinstance(circuit_powers, dict) and circuit_powers:
                    await self.history.record_circuits(
                        serial=snap.serial,
                        site_id=site_id,
                        powers=circuit_powers,
                    )
            elif snap.kind == "ev_charger":
                await self.history.record(
                    serial=snap.serial,
                    product_type=snap.product_type,
                    site_id=site_id,
                    charge_w=_num(state.get("charge_power_w")),
                )

        # Per-site component / overhead snapshot (panel vs inverter overnight).
        for site_id in self.site_ids:
            flow = self.overview(site_id).get("power_flow") or {}
            if (
                flow.get("solar_w") is None
                and flow.get("branch_load_w") is None
                and flow.get("home_w") is None
            ):
                continue
            panel = next(
                (d for d in self.devices.values() if d.site_id == site_id and d.kind == "panel"),
                None,
            )
            await self.history.record_overhead(
                site_id=site_id,
                serial=panel.serial if panel else "",
                solar_w=_num(flow.get("solar_w")),
                home_w=_num(flow.get("home_w")),
                grid_w=_num(flow.get("grid_w")),
                battery_w=_num(flow.get("battery_w")),
                branch_w=_num(flow.get("branch_load_w")),
                feed_w=_num(flow.get("inverter_feed_w")),
                system_overhead_w=_num(flow.get("system_overhead_w")),
                panel_overhead_w=_num(flow.get("panel_overhead_w")),
                inverter_overhead_w=_num(flow.get("inverter_overhead_w")),
                night=bool(flow.get("overhead_night")),
            )

    async def _prune_loop(self) -> None:
        while True:
            await asyncio.sleep(86400)
            deleted = await self.history.prune(self.settings.history_retention_days)
            if deleted:
                _LOGGER.info("Pruned %d old history rows", deleted)

    @staticmethod
    def _device_dict(snap: DeviceSnapshot | None) -> dict[str, Any] | None:
        if snap is None:
            return None
        return {
            "serial": snap.serial,
            "name": snap.name,
            "product_type": snap.product_type,
            "kind": snap.kind,
            "site_id": snap.site_id,
            "online": snap.online,
            "mqtt": snap.mqtt,
            "error": snap.error,
            "state": snap.state,
        }

    @staticmethod
    def _power_flow(
        inverter: DeviceSnapshot | None,
        panel: DeviceSnapshot | None,
        ev: DeviceSnapshot | None,
    ) -> dict[str, Any]:
        inv = inverter.state if inverter else {}
        pan = panel.state if panel else {}
        evs = ev.state if ev else {}
        solar_strings = _solar_strings(inv)
        solar_from_strings = sum(max(float(s["power_w"]), 0.0) for s in solar_strings)
        solar_w = inv.get("solar_power_w")
        if solar_w is None:
            # `_solar_strings` already defaults missing/stale string readings
            # to 0.0, so this is a real (if possibly all-zero) measurement,
            # not "unknown" — using `and solar_from_strings` here used to
            # skip this assignment whenever strings totalled exactly 0
            # (falsy), leaving solar_w as None and silently kicking the
            # battery balance formula below back to the noisy per-pack sum.
            solar_w = solar_from_strings

        # Site grid: prefer the inverter's own site grid meter (field 515) —
        # it's a direct utility-tie CT reading. The panel's "grid_import"
        # aggregate (field 964, same guessed-aggregate family as
        # hall_total/channel_sum, both already found unreliable) has shown a
        # near-constant ~-225 to -245W offset regardless of actual solar,
        # battery, or load conditions — including a moment where the
        # inverter meter cleanly read 0.0 (confirmed against the EcoFlow app
        # showing no export) while the panel aggregate still read -228W.
        #
        # Field 515 is frequently *absent* rather than present-and-zero:
        # like other fields on this device, protobuf omits it from the wire
        # entirely when the true value is exactly 0 — so "missing" here
        # usually means "no exchange," not "unknown." Only fall back to the
        # panel aggregate for genuinely large exchanges (≥500W), which is
        # the regime it was originally added for; below that, trust a
        # missing inverter reading as 0 rather than the unreliable aggregate.
        inv_grid = _num(inv.get("grid_power_w"))
        pan_grid = _num(pan.get("grid_import_power_w"))
        if inv_grid is not None:
            grid_w = inv_grid
        elif pan_grid is not None and abs(pan_grid) >= 500:
            grid_w = pan_grid
        else:
            grid_w = 0.0

        solar_n = _num(solar_w)
        feed_raw = _num(pan.get("inverter_feed_power_w"))
        feed_w = abs(feed_raw) if feed_raw is not None else None

        # Battery: the per-pack current sum (inv.battery_power_w) has shown
        # ~4x overcounting (all packs echo a near-identical shared-bus
        # current, so summing 4 packs quadruples it) and can be clobbered by
        # an upstream "assume idle at ≥95% SOC" fallback that overwrites a
        # real, fresher pack reading with 0. Prefer solving the balance with
        # the panel's directly-measured inverter feed instead — it's a real
        # CT reading with no per-pack aggregation involved:
        #   feed = solar − battery − inverter_overhead
        #   ⇒ battery = solar − feed − inverter_overhead
        # inverter_overhead used to be a flat 75W here, ground-truthed
        # against the EcoFlow app on 2026-07-18 at near-idle throughput
        # (solar≈16W, feed≈138W). But a full-SOC/BMS-idle snapshot on
        # 2026-07-19 (solar≈12,152W, feed≈11,538W, battery≈0 independently)
        # isolated a genuine residual of 614.4W at that throughput — a flat
        # 75W badly undershoots once solar climbs into the multi-kW range.
        # `estimate_inverter_overhead_w` models this as base+percentage of
        # solar instead (see pyecoflowocean/overhead.py for the fit).
        pack_battery_n = _num(inv.get("battery_power_w"))
        if solar_n is not None and feed_raw is not None:
            battery_n = solar_n - feed_raw - estimate_inverter_overhead_w(solar_n)
        else:
            battery_n = pack_battery_n

        # At a full state of charge the BMS won't accept any more charge
        # current, so a positive ("charging"-looking) balance-formula result
        # here is just solar the inverter is curtailing/clipping before it
        # ever reaches the battery — not real charge current. Ground-truthed
        # 2026-07-19: solar≈10.4kW, feed≈9.9kW yielded battery_n≈+494W
        # ("charging") while the individual pack readings summed to ~15W
        # (idle/balancing) and the EcoFlow app showed no charging — both at
        # soc=100%. Suppress the apparent-charging artifact once the BMS
        # reports itself topped off.
        #
        # The same overhead model also produces small *negative* residuals
        # (false "discharging") when solar is high and the pack bank is
        # idle at 100%. Ground-truthed 2026-07-20: solar≈11.9kW, feed≈11.3kW,
        # overhead_est≈603W → battery_n≈−62W while pack sum≈+14W and SOC=100%
        # with heavy solar export powering the house. Reconfirmed 2026-07-21:
        # solar≈12.8kW exporting ~12kW, packs each ±6W, but inv.battery_power_w
        # was absent so the old packs_idle check never fired and residuals of
        # −3…−50W leaked as "discharge". Use per-pack watts when the bank
        # total is missing; keep real kW-scale discharge at full SOC intact.
        FULL_SOC_NO_CHARGE_PCT = 99.0
        FULL_SOC_IDLE_BAND_W = 200.0
        PACK_IDLE_W = 100.0
        soc_n = _num(inv.get("battery_soc"))
        if (
            battery_n is not None
            and soc_n is not None
            and soc_n >= FULL_SOC_NO_CHARGE_PCT
        ):
            if battery_n > 0:
                battery_n = 0.0
            elif abs(battery_n) < FULL_SOC_IDLE_BAND_W and _battery_bank_idle(
                inv, pack_battery_n, PACK_IDLE_W
            ):
                battery_n = 0.0

        panel_home_n = _num(pan.get("home_power_w"))
        panel_self_n = _num(pan.get("panel_self_consumption_w"))
        exporting = grid_w is not None and grid_w < -NIGHT_EXPORT_MAX_W
        branch_w = _branch_load_w(
            pan.get("circuit_power_w") or {},
            feed_w=feed_w,
            filter_export_phantoms=bool(
                (solar_n is not None and solar_n >= NIGHT_SOLAR_MAX_W) or exporting
            ),
        )

        # Site home load ("essential load" — whatever's actually riding on
        # the battery-backed bus): user-confirmed against the EcoFlow app's
        # own "House / essential load" tile, 2026-07-19 — the app reads
        # 263W while every circuit *except* the inverter-feed pair (ch38/40,
        # literally labeled "Inverter feed L1"/"L2" in the panel's own
        # config — that's the inverter's own output into the panel bus, not
        # a house breaker) sums to the same ballpark. hall_total_power_w
        # tracked ~half the inverter feed instead (nowhere close), and the
        # balance formula (solar+grid−battery) also drifted far from the
        # app whenever the true grid meter and hall_total disagreed. So:
        # home = sum(|circuit power|) over all circuits except 38/40.
        # branch_w already implements exactly that (_branch_load_w, with a
        # phantom-CT-coupling filter for legs that echo the feed during
        # heavy export). Fall back to the balance formula, then the panel's
        # own aggregate, only when no circuit data is available at all.
        if branch_w is not None:
            home_w = branch_w
        elif solar_n is not None and grid_w is not None and battery_n is not None:
            home_w = solar_n + grid_w - battery_n
            if home_w < 0:
                home_w = 0.0
        elif panel_home_n is not None:
            home_w = panel_home_n
        else:
            home_w = _num(inv.get("home_power_w"))

        draw = _component_overhead(
            solar_w=solar_n,
            home_w=home_w,
            battery_w=battery_n,
            grid_w=grid_w,
            branch_w=branch_w,
            feed_w=feed_w,
            panel_self_consumption_w=panel_self_n,
        )

        return {
            "solar_w": solar_w,
            "grid_w": grid_w,
            "battery_w": battery_n,
            "battery_pack_raw_w": pack_battery_n,
            "home_w": home_w,
            "soc": inv.get("battery_soc"),
            "work_mode": inv.get("work_mode"),
            "storm_mode": pan.get("storm_mode"),
            "storm_watch": pan.get("storm_watch"),
            "storm_enabled": pan.get("storm_enabled"),
            "backup_reserve_soc": pan.get("backup_reserve_soc"),
            "solar_backup_reserve_soc": pan.get("solar_backup_reserve_soc"),
            "backup_soc_limit": inv.get("backup_soc_limit"),
            "online": inv.get("online") if inverter else None,
            "panel_home_w": pan.get("home_power_w"),
            "panel_grid_import_w": pan.get("grid_import_power_w"),
            "inverter_feed_w": pan.get("inverter_feed_power_w"),
            "panel_self_consumption_w": pan.get("panel_self_consumption_w"),
            "branch_load_w": branch_w,
            "system_overhead_w": draw["system_overhead_w"],
            "panel_overhead_w": draw["panel_overhead_w"],
            "inverter_overhead_w": draw["inverter_overhead_w"],
            "conversion_loss_est_w": draw["conversion_loss_est_w"],
            "overhead_night": draw["night"],
            "overhead_note": draw["note"],
            "solar_strings": solar_strings,
            "ev_charge_w": _ev_charge_watts(evs, pan),
            "vehicle_connected": evs.get("vehicle_connected"),
            "charging_active": evs.get("charging_active"),
            # Voltages when the panel / inverter / EV report them.
            "grid_voltage_v": _num(pan.get("grid_voltage_v")),
            "grid_voltage_l1_v": _num(pan.get("grid_voltage_l1_v")),
            "grid_voltage_l2_v": _num(pan.get("grid_voltage_l2_v")),
            "phase_a_voltage_v": _num(inv.get("phase_a_voltage_v")),
            "phase_b_voltage_v": _num(inv.get("phase_b_voltage_v")),
            "ev_output_voltage_v": _num(evs.get("output_voltage_v")),
            "updated_at": inv.get("updated_at") or pan.get("updated_at") or evs.get("updated_at"),
        }


FEED_CIRCUITS: frozenset[int] = frozenset({38, 40})
NIGHT_SOLAR_MAX_W = 50.0
NIGHT_EXPORT_MAX_W = 100.0  # |export| below this → treat as night-measureable


def _branch_load_w(
    circuit_power: Any,
    *,
    feed_w: float | None = None,
    filter_export_phantoms: bool = False,
) -> float | None:
    """Sum |watts| on non-feed circuits.

    While exporting, some 240V legs report multi‑kW “loads” that closely
    *mirror* the inverter feed reading (CT/bus coupling) rather than a real
    house breaker — their telltale signature is tracking feed's magnitude,
    not merely being large. Filter only circuits sitting within a tight band
    around feed (±30%), rather than anything above some fraction of it —
    the earlier ">= 35% of feed" cutoff was catching genuine big appliances
    (AC, oven, EV circuit, etc.) too, silently zeroing them out of the house
    total whenever they switched on during the day (user-reported 2026-07-19:
    "house total is not updating when large loads come on").
    """
    if not isinstance(circuit_power, dict) or not circuit_power:
        return None
    feed = float(feed_w or 0.0)
    use_phantom_filter = filter_export_phantoms and feed > 500
    total = 0.0
    seen = False
    for key, value in circuit_power.items():
        try:
            channel = int(key)
            watts = abs(float(value))
        except (TypeError, ValueError):
            continue
        if channel in FEED_CIRCUITS:
            continue
        if use_phantom_filter and abs(watts - feed) <= 0.3 * feed:
            continue
        total += watts
        seen = True
    return total if seen else 0.0


def _component_overhead(
    *,
    solar_w: float | None,
    home_w: float | None,
    battery_w: float | None = None,
    grid_w: float | None,
    branch_w: float | None,
    feed_w: float | None,
    panel_self_consumption_w: float | None = None,
) -> dict[str, Any]:
    """Estimate panel vs inverter draw.

    panel_overhead: prefer the Smart Panel's own directly-reported figure —
    hall_total_power_w (967) − channel_sum_power_w (966), i.e. the panel's own
    relay/electronics/display draw that never shows up as a branch circuit.
    User-confirmed field semantics, 2026-07-18 — available at any time of day,
    not just the quiet-night window.

    inverter_overhead: the inverter's own PCS/fan draw that never crosses the
    panel's feed CT, measured as solar − battery − feed
    (`measure_inverter_overhead_w`). Whenever `battery_w` was itself derived
    upstream from this same solar/feed balance (the common case — see
    `estimate_inverter_overhead_w` in pyecoflowocean/overhead.py), this just
    echoes that model estimate back rather than an independent measurement.
    It only becomes a genuine residual measurement in moments where
    `battery_w` comes from an independent source — e.g. the BMS-idle
    full-SOC clamp below, or raw per-pack current when solar/feed are
    unavailable. Those moments are what the base+percentage model in
    `overhead.py` is calibrated against.

    Night (solar≈0, not exporting, quiet meters) is still used as a fallback
    for panel_overhead when the 967−966 reading isn't available:
      panel_overhead  ≈ feed − branch   (panel bus residual / electronics)
      inverter_overhead ≈ solar − battery − feed

    Day (no night split, no direct panel reading): combined system overhead +
    datasheet conversion estimate only.
    """
    solar = float(solar_w or 0.0)
    home = float(home_w or 0.0)
    battery = float(battery_w) if battery_w is not None else None
    grid = float(grid_w) if grid_w is not None else None
    branch = float(branch_w) if branch_w is not None else None
    feed = float(feed_w) if feed_w is not None else None

    system = None
    if branch is not None:
        system = max(0.0, home - branch)

    quiet_night = (
        solar < NIGHT_SOLAR_MAX_W
        and (grid is None or grid > -NIGHT_EXPORT_MAX_W)
        and feed is not None
        and branch is not None
        and feed < 2500
        and branch < 2500
    )
    panel_oh: float | None = None
    inv_oh: float | None = None
    conversion: float | None = None
    note = ""

    if panel_self_consumption_w is not None:
        panel_oh = max(0.0, float(panel_self_consumption_w))
        note = "panel ≈ hall_total−channel_sum (967−966, direct panel reading)"
        if feed is not None and battery is not None:
            inv_oh = measure_inverter_overhead_w(solar_w=solar, battery_w=battery, feed_w=feed)
            note += "; inverter ≈ solar−battery−feed"
    elif quiet_night:
        panel_oh = max(0.0, feed - branch)
        if battery is not None:
            inv_oh = measure_inverter_overhead_w(solar_w=solar, battery_w=battery, feed_w=feed)
            note = "Night split active: panel ≈ feed−branch, inverter ≈ solar−battery−feed"
        else:
            note = "Night split active: panel ≈ feed−branch"
    elif solar >= NIGHT_SOLAR_MAX_W:
        conversion = round(solar * 0.025, 1)
        note = (
            "Daytime — panel/inverter lines fill in after solar≈0 tonight; "
            f"conversion≈2.5% of solar ({conversion:.0f} W est)"
        )
    else:
        note = "Waiting for a quiet night baseline (solar≈0, feed/branch < 2.5 kW)"

    return {
        "system_overhead_w": round(system, 1) if system is not None else None,
        "panel_overhead_w": round(panel_oh, 1) if panel_oh is not None else None,
        "inverter_overhead_w": round(inv_oh, 1) if inv_oh is not None else None,
        "conversion_loss_est_w": conversion,
        "night": quiet_night,
        "note": note,
    }


def _solar_strings(inv: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose MPPT string slots for the flow UI.

    Ocean Pro deployments commonly have 5 strings; some configs go up to 8.
    Always return a contiguous 1..N list (default N=5) so the UI can draw one
    connector per string into the Solar aggregate box.
    """
    powers = inv.get("mppt_string_power_w") or {}
    max_slots = 8
    default_slots = 5
    highest = 0
    for idx in range(1, max_slots + 1):
        if powers.get(idx, powers.get(str(idx))) is not None:
            highest = idx
    count = min(max_slots, max(default_slots, highest))
    out: list[dict[str, Any]] = []
    for idx in range(1, count + 1):
        watts = powers.get(idx, powers.get(str(idx)))
        if watts is None:
            watts = 0.0
        else:
            watts = float(watts)
        out.append(
            {
                "id": idx,
                "label": f"String {idx}",
                "power_w": watts,
                "active": True,
            }
        )
    return out


def _ev_charge_watts(evs: dict[str, Any], pan: dict[str, Any]) -> float | None:
    """Only report EV watts when charging/connected; hide idle decoder junk."""
    if evs.get("charging_active") is True or evs.get("vehicle_connected") is True:
        return evs.get("charge_power_w") or pan.get("ev_charge_power_w") or 0.0
    panel_ev = pan.get("ev_charge_power_w")
    if panel_ev is not None and panel_ev > 0:
        return panel_ev
    if evs:
        return 0.0
    return None


class MultiSiteManager:
    """Groups sites by EcoFlow account and exposes per-site views."""

    def __init__(self, settings: Settings, history: HistoryStore) -> None:
        self.settings = settings
        self.history = history
        self.accounts: list[AccountHub] = []
        self._site_to_account: dict[str, AccountHub] = {}
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._relay_tasks: list[asyncio.Task[Any]] = []
        self.ready = False
        self.last_error: str | None = None

    @property
    def sites(self) -> list[SiteConfig]:
        return list(self.settings.sites)

    async def start(self) -> None:
        by_email: dict[str, list[SiteConfig]] = {}
        for site in self.settings.sites:
            by_email.setdefault(site.email.lower(), []).append(site)

        errors: list[str] = []
        for email, site_list in by_email.items():
            hub = AccountHub(self.settings, self.history, site_list)
            try:
                await hub.start()
            except Exception as err:
                errors.append(f"{email}: {err}")
                _LOGGER.error("Failed to start account %s: %s", email, err)
                # Keep hub for error reporting if it partially started
            self.accounts.append(hub)
            for site in site_list:
                self._site_to_account[site.id] = hub
            self._relay_tasks.append(
                asyncio.create_task(self._relay_account(hub), name=f"relay-{email}")
            )

        self.ready = any(a.ready for a in self.accounts)
        self.last_error = "; ".join(errors) if errors else None
        if not self.ready:
            raise RuntimeError(self.last_error or "No sites started")

    async def stop(self) -> None:
        for task in self._relay_tasks:
            task.cancel()
        if self._relay_tasks:
            await asyncio.gather(*self._relay_tasks, return_exceptions=True)
        self._relay_tasks.clear()
        for hub in self.accounts:
            await hub.stop()

    async def _relay_account(self, hub: AccountHub) -> None:
        queue = hub.subscribe()
        try:
            while True:
                event = await queue.get()
                await self._publish(event)
        except asyncio.CancelledError:
            raise
        finally:
            hub.unsubscribe(queue)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def _publish(self, event: dict[str, Any]) -> None:
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    _ = queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:
                    dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)

    def list_sites(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for site in self.settings.sites:
            hub = self._site_to_account.get(site.id)
            if hub is None:
                out.append(
                    {
                        "id": site.id,
                        "label": site.label,
                        "ready": False,
                        "error": "not started",
                        "mqtt_connected": False,
                        "device_count": 0,
                        "online_count": 0,
                    }
                )
            else:
                out.append(hub.site_summary(site.id))
        return out

    def overview(self, site_id: str) -> dict[str, Any]:
        hub = self._site_to_account.get(site_id)
        if hub is None:
            raise KeyError(site_id)
        return hub.overview(site_id)

    def default_site_id(self) -> str:
        # Prefer a site that already has devices (e.g. Desert while Forest is pending).
        with_devices: list[str] = []
        ready: list[str] = []
        for site in self.settings.sites:
            hub = self._site_to_account.get(site.id)
            if hub is None or not hub.ready:
                continue
            ready.append(site.id)
            if hub.site_summary(site.id).get("device_count", 0) > 0:
                with_devices.append(site.id)
        if with_devices:
            return with_devices[0]
        if ready:
            return ready[0]
        return self.settings.sites[0].id


def _kind_for(product_type: str) -> str:
    if product_type == PRODUCT_TYPE_OCEAN_PANEL:
        return "panel"
    if product_type == PRODUCT_TYPE_EV_CHARGER:
        return "ev_charger"
    if product_type in INVERTER_TYPES:
        return "inverter"
    return "other"


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _battery_bank_idle(
    inv: dict[str, Any],
    pack_battery_n: float | None,
    pack_idle_w: float,
) -> bool:
    """True when pack telemetry says the battery bank is effectively idle.

    Prefer the combined `battery_power_w` when present. When that bank total is
    missing (common on the live MQTT path), fall back to per-pack `power_w`:
    if every reported pack is within the idle band, the bank is idle.
    """
    if pack_battery_n is not None:
        return abs(pack_battery_n) < pack_idle_w

    packs = inv.get("battery_packs") or []
    if not isinstance(packs, list) or not packs:
        return False

    known: list[float] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        watts = _num(pack.get("power_w"))
        if watts is not None:
            known.append(watts)
    if not known:
        return False
    return all(abs(watts) < pack_idle_w for watts in known)
