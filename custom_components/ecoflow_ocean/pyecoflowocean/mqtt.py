"""EcoFlow mobile app MQTT listener for live Power Ocean telemetry."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ssl
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from paho.mqtt.client import Client, ConnectFlags, MQTTMessage
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from .auth import EcoflowAuth
from .parser import merge_telemetry, parse_flat_telemetry, parse_mqtt_payload

_LOGGER = logging.getLogger(__name__)

LATEST_QUOTAS_INTERVAL = 20


def _stable_client_id(user_id: str) -> str:
    """Generate a per-process MQTT client id (EcoFlow disconnects duplicate ids)."""
    token = hashlib.md5(f"{user_id}-{uuid.uuid4().hex}".encode(), usedforsecurity=False).hexdigest()[:16].upper()
    return f"ANDROID_{token}_{user_id}"


class EcoflowMqttListener:
    """Subscribe to EcoFlow cloud MQTT and accumulate live telemetry."""

    def __init__(
        self,
        auth: EcoflowAuth,
        serial_number: str,
        *,
        product_type: str = "83",
        loop: asyncio.AbstractEventLoop,
        on_update: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._auth = auth
        self._serial_number = serial_number
        self._product_type = product_type
        self._loop = loop
        self._on_update = on_update
        self._client: Client | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._connected = False
        self._telemetry: dict[str, Any] = {}
        self._telemetry_ts: dict[str, float] = {}

    @property
    def telemetry(self) -> dict[str, Any]:
        return dict(self._telemetry)

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected()

    def get_state(self):
        """Return parsed state from accumulated MQTT telemetry."""
        from .const import PRODUCT_TYPE_EV_CHARGER, PRODUCT_TYPE_OCEAN_PANEL

        if self._product_type == PRODUCT_TYPE_OCEAN_PANEL:
            return self.get_panel_state()
        if self._product_type == PRODUCT_TYPE_EV_CHARGER:
            return self.get_ev_charger_state()
        return self.get_inverter_state()

    def get_inverter_state(self):
        """Return parsed inverter state from MQTT telemetry."""
        from .models import EcoflowOceanState

        if not self._telemetry:
            return None
        return parse_flat_telemetry(self._serial_number, self._telemetry)

    def get_panel_state(self):
        """Return parsed Ocean Panel state from MQTT telemetry."""
        from .panel_decoder import parse_panel_flat_telemetry

        if not self._telemetry:
            return None
        return parse_panel_flat_telemetry(self._serial_number, self._telemetry)

    def get_ev_charger_state(self):
        """Return parsed EV charger state from MQTT telemetry."""
        from .ev_charger_decoder import parse_ev_charger_flat_telemetry

        if not self._telemetry:
            return None
        return parse_ev_charger_flat_telemetry(self._serial_number, self._telemetry)

    async def start(self) -> None:
        """Connect and subscribe to EcoFlow MQTT topics."""
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
        _LOGGER.info("Connecting EcoFlow MQTT %s:%s for %s", host, port, self._serial_number)
        client.connect_async(host, port, keepalive=15)
        client.loop_start()

        self._poll_task = asyncio.create_task(self._poll_latest_quotas(user_id))

    async def stop(self) -> None:
        """Disconnect MQTT listener."""
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
        sn = self._serial_number
        return [
            (f"/app/device/property/{sn}", 1),
            (f"/app/device/status/{sn}", 1),
            (f"/app/{user_id}/{sn}/thing/property/get_reply", 1),
        ]

    def _get_topic(self, user_id: str) -> str:
        return f"/app/{user_id}/{self._serial_number}/thing/property/get"

    def _on_connect(
        self,
        client: Client,
        userdata: Any,
        flags: ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None = None,
    ) -> None:
        if reason_code.is_failure:
            _LOGGER.error("EcoFlow MQTT connect failed: %s", reason_code)
            self._connected = False
            return

        user_id = self._auth.user_id
        if not user_id:
            return

        topics = self._topics(user_id)
        client.subscribe(topics)
        self._connected = True
        _LOGGER.info("EcoFlow MQTT connected, subscribed to %s", [t[0] for t in topics])
        self._publish_latest_quotas(user_id)

    def _on_disconnect(
        self,
        client: Client,
        userdata: Any,
        flags: Any,
        reason_code: ReasonCode,
        properties: Properties | None = None,
    ) -> None:
        if reason_code.is_failure:
            _LOGGER.warning("EcoFlow MQTT disconnected: %s", reason_code)
        self._connected = False

    def _on_message(self, client: Client, userdata: Any, message: MQTTMessage) -> None:
        update = parse_mqtt_payload(
            message.payload, self._serial_number, self._product_type
        )
        if not update:
            _LOGGER.debug(
                "Ignored undecodable MQTT message on %s (%d bytes)",
                message.topic,
                len(message.payload),
            )
            return

        self._telemetry = merge_telemetry(self._telemetry, update, field_ts=self._telemetry_ts)
        _LOGGER.debug(
            "MQTT update on %s: bpSoc=%s sysLoadPwr=%s sysGridPwr=%s mpptPwr=%s",
            message.topic,
            self._telemetry.get("bpSoc"),
            self._telemetry.get("sysLoadPwr"),
            self._telemetry.get("sysGridPwr"),
            self._telemetry.get("mpptPwr"),
        )
        self._schedule_update()

    def _publish_latest_quotas(self, user_id: str) -> None:
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
        topic = self._get_topic(user_id)
        info = self._client.publish(topic, payload, qos=1)
        _LOGGER.debug("Published latestQuotas to %s (mid=%s)", topic, info.mid)

    async def _poll_latest_quotas(self, user_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(LATEST_QUOTAS_INTERVAL)
                self._publish_latest_quotas(user_id)
        except asyncio.CancelledError:
            raise

    def _schedule_update(self) -> None:
        if not self._on_update:
            return

        async def _run() -> None:
            if self._on_update:
                await self._on_update()

        def _create_task() -> None:
            asyncio.create_task(_run())

        self._loop.call_soon_threadsafe(_create_task)
