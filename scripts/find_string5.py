#!/usr/bin/env python3
import asyncio, json, os, ssl, time
from paho.mqtt.client import Client
from paho.mqtt.enums import CallbackAPIVersion
from pyecoflowocean import EcoflowOcean
from pyecoflowocean.mqtt import _stable_client_id
from pyecoflowocean.wire_decoder import decode_protobuf, flatten_tree

SN = "HR51ZA1AVH770253"


async def main() -> None:
    api = EcoflowOcean(
        os.environ["ECOFLOW_EMAIL"],
        os.environ["ECOFLOW_PASSWORD"],
        serial_number=SN,
        product_type="88",
    )
    await api.login()
    auth = api._auth
    cert = auth.mqtt_cert
    uid = auth.user_id
    latest: dict[str, float] = {}

    client = Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=_stable_client_id(uid),
    )
    client.username_pw_set(cert["certificateAccount"], cert["certificatePassword"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_msg(_a, _b, msg):
        try:
            json.loads(msg.payload.decode())
            return
        except Exception:
            pass
        try:
            flat = flatten_tree(decode_protobuf(msg.payload))
        except Exception:
            return
        for path, val in flat.items():
            if isinstance(val, (int, float)) and abs(float(val)) < 50000:
                latest[path] = float(val)

    def on_conn(c, _u, _f, rc, _p=None):
        print("connect", rc)
        if getattr(rc, "is_failure", False):
            return
        c.subscribe([(f"/app/device/property/{SN}", 1)])
        c.publish(
            f"/app/{uid}/{SN}/thing/property/get",
            json.dumps(
                {
                    "from": "Android",
                    "id": str(int(time.time() * 1000)),
                    "version": "1.0",
                    "moduleType": 0,
                    "operateType": "latestQuotas",
                    "params": {},
                }
            ),
            qos=1,
        )

    client.on_connect = on_conn
    client.on_message = on_msg
    client.connect(cert.get("url") or "mqtt.ecoflow.com", int(cert.get("port") or 8883), 30)
    client.loop_start()
    await asyncio.sleep(40)
    client.loop_stop()
    client.disconnect()
    await api.close()

    print("=== 1470-1495 ===")
    for path, val in sorted(latest.items()):
        if not path.startswith("1.1."):
            continue
        try:
            num = int(path.split(".")[-1])
        except ValueError:
            continue
        if 1470 <= num <= 1495:
            print(f"  {path} = {val:.2f}")

    print("=== closest to app values ===")
    for target in (3048, 2249, 2205, 2195, 1600):
        ranked = sorted(
            (
                (abs(val - target), path, val)
                for path, val in latest.items()
                if path.startswith("1.1.")
            ),
            key=lambda item: item[0],
        )[:5]
        print(f"~{target}")
        for err, path, val in ranked:
            print(f"  err={err:.1f} {path}={val:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
