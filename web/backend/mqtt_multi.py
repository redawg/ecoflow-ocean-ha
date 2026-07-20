"""Single EcoFlow MQTT connection for multiple device serials."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import ssl
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paho.mqtt.client import Client, ConnectFlags, MQTTMessage
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from pyecoflowocean.auth import EcoflowAuth
from pyecoflowocean.const import (
    PRODUCT_TYPE_EV_CHARGER,
    PRODUCT_TYPE_OCEAN_PANEL,
    PRODUCT_TYPE_POWER_OCEAN_PRO,
)
from pyecoflowocean.ev_charger_decoder import parse_ev_charger_flat_telemetry
from pyecoflowocean.panel_decoder import parse_panel_flat_telemetry
from pyecoflowocean.parser import merge_telemetry, parse_flat_telemetry, parse_mqtt_payload

_LOGGER = logging.getLogger(__name__)
LATEST_QUOTAS_INTERVAL = 20


def _stable_client_id(user_id: str) -> str:
    token = hashlib.md5(user_id.encode(), usedforsecurity=False).hexdigest()[:16].upper()
    return f"ANDROID_{token}_{user_id}"


class MultiDeviceMqtt:
    """One cloud MQTT session; telemetry keyed by serial number."""

    def __init__(
        self,
        auth: EcoflowAuth,
        devices: dict[str, str],
        *,
        loop: asyncio.AbstractEventLoop,
        on_update: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        # devices: serial -> product_type
        self._auth = auth
        self._devices = dict(devices)
        self._loop = loop
        self._on_update = on_update
        self._client: Client | None = None
        self._dump_remaining = int(os.environ.get("MQTT_DUMP_N", "0") or 0)
        self._dump_dir = Path(os.environ.get("MQTT_DUMP_DIR", "/data/mqtt_dump"))
        self._poll_task: asyncio.Task[Any] | None = None
        self._connected = False
        self._telemetry: dict[str, dict[str, Any]] = {sn: {} for sn in devices}
        self._telemetry_ts: dict[str, dict[str, float]] = {sn: {} for sn in devices}

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected()

    def get_state(self, serial: str, product_type: str):
        flat = self._telemetry.get(serial) or {}
        if not flat:
            return None
        try:
            if product_type == PRODUCT_TYPE_OCEAN_PANEL:
                return parse_panel_flat_telemetry(serial, flat)
            if product_type == PRODUCT_TYPE_EV_CHARGER:
                return parse_ev_charger_flat_telemetry(serial, flat)
            return parse_flat_telemetry(serial, flat)
        except Exception as err:
            _LOGGER.debug("Ignoring undecodable MQTT state for %s: %s", serial, err)
            return None

    async def start(self) -> None:
        cert = self._auth.mqtt_cert
        user_id = self._auth.user_id
        if not cert or not user_id:
            raise RuntimeError("MQTT credentials unavailable — log in first")

        host = cert.get("url") or "mqtt.ecoflow.com"
        port = int(cert.get("port") or 8883)
        username = cert.get("certificateAccount")
        password = cert.get("certificatePassword")
        if not username or not password:
            raise RuntimeError("MQTT cert missing certificateAccount/certificatePassword")

        client = Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=_stable_client_id(user_id),
            clean_session=True,
        )
        client.username_pw_set(username, password)
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.tls_insecure_set(False)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        self._client = client
        _LOGGER.info(
            "Connecting shared EcoFlow MQTT %s:%s for %d device(s)",
            host,
            port,
            len(self._devices),
        )
        client.connect_async(host, port, keepalive=15)
        client.loop_start()
        self._poll_task = asyncio.create_task(self._poll_latest_quotas(user_id))

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False

    def _topics(self, user_id: str) -> list[tuple[str, int]]:
        topics: list[tuple[str, int]] = []
        for sn in self._devices:
            topics.extend(
                [
                    (f"/app/device/property/{sn}", 1),
                    (f"/app/device/status/{sn}", 1),
                    (f"/app/{user_id}/{sn}/thing/property/get_reply", 1),
                ]
            )
        return topics

    def _on_connect(
        self,
        client: Client,
        userdata: Any,
        flags: ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None = None,
    ) -> None:
        if reason_code.is_failure:
            _LOGGER.error("Shared EcoFlow MQTT connect failed: %s", reason_code)
            self._connected = False
            return
        user_id = self._auth.user_id
        if not user_id:
            return
        topics = self._topics(user_id)
        client.subscribe(topics)
        self._connected = True
        _LOGGER.info("Shared EcoFlow MQTT connected (%d topics)", len(topics))
        for sn in self._devices:
            self._publish_latest_quotas(user_id, sn)

    def _on_disconnect(
        self,
        client: Client,
        userdata: Any,
        flags: Any,
        reason_code: ReasonCode,
        properties: Properties | None = None,
    ) -> None:
        if reason_code.is_failure:
            _LOGGER.warning("Shared EcoFlow MQTT disconnected: %s", reason_code)
        self._connected = False

    def _serial_from_topic(self, topic: str) -> str | None:
        for sn in self._devices:
            if sn in topic:
                return sn
        return None

    def _on_message(self, client: Client, userdata: Any, message: MQTTMessage) -> None:
        serial = self._serial_from_topic(message.topic)
        if not serial:
            return
        product_type = self._devices[serial]
        # TEMP: raw-capture panel and inverter payloads, so the user can
        # correlate raw wire field numbers to the EcoFlow app's live readouts
        # (watch the app while a capture burst runs, then diff timestamps).
        if self._dump_remaining > 0 and product_type in (
            PRODUCT_TYPE_OCEAN_PANEL,
            PRODUCT_TYPE_POWER_OCEAN_PRO,
        ):
            try:
                self._dump_dir.mkdir(parents=True, exist_ok=True)
                idx = 200 - self._dump_remaining
                path = self._dump_dir / f"{serial}_{idx:03d}.bin"
                path.write_bytes(message.payload)
                meta = self._dump_dir / f"{serial}_{idx:03d}.txt"
                meta.write_text(
                    f"topic={message.topic}\nlen={len(message.payload)}\n"
                    f"captured_at={datetime.now(timezone.utc).isoformat()}\n",
                    encoding="utf-8",
                )
                self._dump_remaining -= 1
                _LOGGER.info("Dumped MQTT payload %s (%d left)", path.name, self._dump_remaining)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("MQTT dump failed: %s", err)
                self._dump_remaining = 0
        update = parse_mqtt_payload(message.payload, serial, product_type)
        if not update:
            return
        self._telemetry[serial] = merge_telemetry(
            self._telemetry.get(serial, {}),
            update,
            field_ts=self._telemetry_ts.setdefault(serial, {}),
        )
        self._schedule_update(serial)

    def _publish_latest_quotas(self, user_id: str, serial: str) -> None:
        if not self._client or not self._client.is_connected():
            return
        payload = json.dumps(
            {
                "from": "Android",
                "id": str(uuid.uuid4().int % 10_000_000_000),
                "version": "1.1",
                "moduleType": 0,
                "operateType": "latestQuotas",
                "params": {},
            }
        )
        topic = f"/app/{user_id}/{serial}/thing/property/get"
        self._client.publish(topic, payload, qos=1)

    async def _poll_latest_quotas(self, user_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(LATEST_QUOTAS_INTERVAL)
                for sn in self._devices:
                    self._publish_latest_quotas(user_id, sn)
        except asyncio.CancelledError:
            raise

    def _schedule_update(self, serial: str) -> None:
        if not self._on_update:
            return

        async def _run() -> None:
            if self._on_update:
                await self._on_update(serial)

        def _create_task() -> None:
            asyncio.create_task(_run())

        self._loop.call_soon_threadsafe(_create_task)
