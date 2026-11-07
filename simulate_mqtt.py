"""
OWOO ChirpStack LoRaWAN MQTT Uplink Simulator
---------------------------------------------
Mengirimkan paket data simulasi End Node (ACI, NDSI, dan Klasifikasi Audio)
ke topic ChirpStack:
    application/<appid>/device/<devui>/event/up

Contoh penggunaan:
    py simulate_mqtt.py --once
    py simulate_mqtt.py --loop --interval 6
    py simulate_mqtt.py --condition threat --once
"""

import argparse
import json
import random
import socket
import time
from datetime import datetime

import paho.mqtt.client as mqtt
import requests

import database as db


def generate_sample_metrics(forced_mode: str | None = None):
    mode = (forced_mode or "").lower()
    if not mode:
        r = random.random()
        if r < 0.18:
            mode = "threat"
        elif r < 0.36:
            mode = "noise"
        else:
            mode = "biodiversity"

    if mode in ("threat", "chainsaw"):
        return {
            "aci": round(random.uniform(0.36, 0.53), 3),
            "ndsi": round(random.uniform(-0.74, -0.42), 3),
            "classification": "Chainsaw",
            "confidence": round(random.uniform(0.91, 0.99), 3),
        }
    if mode in ("noise", "rain", "wind"):
        return {
            "aci": round(random.uniform(0.50, 0.64), 3),
            "ndsi": round(random.uniform(-0.12, 0.21), 3),
            "classification": "Rain/Wind",
            "confidence": round(random.uniform(0.87, 0.95), 3),
        }
    return {
        "aci": round(random.uniform(0.70, 0.93), 3),
        "ndsi": round(random.uniform(0.62, 0.91), 3),
        "classification": "Ambient Hutan",
        "confidence": round(random.uniform(0.92, 0.99), 3),
    }


def send_node_uplink(node: dict, mqtt_host: str, mqtt_port: int, http_url: str, forced_mode: str | None = None):
    metrics = generate_sample_metrics(forced_mode)
    topic = f"application/{node['app_id']}/device/{node['dev_eui']}/event/up"

    chirpstack_payload = {
        "deduplicationId": f"sim-{int(time.time()*1000)}",
        "time": datetime.now().isoformat(),
        "deviceInfo": {
            "applicationId": node["app_id"],
            "devEui": node["dev_eui"],
            "deviceName": node["name"],
        },
        "object": {
            "aci": metrics["aci"],
            "ndsi": metrics["ndsi"],
            "classification": metrics["classification"],
            "confidence": metrics["confidence"],
            "node_key": node["node_key"],
        },
        "rxInfo": [
            {
                "rssi": random.randint(-104, -70),
                "snr": round(random.uniform(4.5, 11.2), 1),
            }
        ],
    }

    # 1. Coba publish langsung ke Broker MQTT ChirpStack / Mosquitto
    try:
        with socket.create_connection((mqtt_host, mqtt_port), timeout=0.35):
            pass
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        else:
            client = mqtt.Client()
        client.connect(mqtt_host, mqtt_port, keepalive=10)
        client.publish(topic, json.dumps(chirpstack_payload), qos=1).wait_for_publish()
        client.disconnect()
        print(
            f"[MQTT OK] {topic} -> ACI={metrics['aci']} | NDSI={metrics['ndsi']:+.3f} | CLASS={metrics['classification']}"
        )
        return
    except Exception:
        pass

    # 2. Fallback ke HTTP ingest lokal jika Mosquitto belum aktif di mesin dev
    resp = requests.post(
        f"{http_url.rstrip('/')}/api/uplink/ingest",
        json={"topic": topic, "payload": chirpstack_payload},
        timeout=5,
    )
    if resp.ok:
        print(
            f"[HTTP-BRIDGE OK] {topic} -> ACI={metrics['aci']} | NDSI={metrics['ndsi']:+.3f} | CLASS={metrics['classification']}"
        )
    else:
        print(f"[FAILED] {topic}: {resp.text}")


def main():
    parser = argparse.ArgumentParser(description="Simulator Uplink MQTT ChirpStack OWOO")
    parser.add_argument("--mqtt-host", default="127.0.0.1", help="IP Broker MQTT ChirpStack")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="Port Broker MQTT ChirpStack")
    parser.add_argument("--http-url", default="http://127.0.0.1:5000", help="URL lokal server OWOO")
    parser.add_argument("--condition", choices=["biodiversity", "threat", "noise"], default=None)
    parser.add_argument("--once", action="store_true", help="Kirim satu kali ke seluruh End Node lalu selesai")
    parser.add_argument("--loop", action="store_true", help="Kirim berkala secara terus-menerus")
    parser.add_argument("--interval", type=float, default=6.0, help="Jeda detik antar paket saat --loop")
    args = parser.parse_args()

    db.init_db()
    nodes = db.list_end_nodes()
    if not nodes:
        print("Belum ada End Node terdaftar di database.")
        return

    if args.once or not args.loop:
        for n in nodes:
            send_node_uplink(n, args.mqtt_host, args.mqtt_port, args.http_url, args.condition)
        return

    print(f"Memulai simulasi berkala setiap {args.interval} detik (Ctrl+C untuk berhenti)...")
    try:
        while True:
            target = random.choice(nodes)
            send_node_uplink(target, args.mqtt_host, args.mqtt_port, args.http_url, args.condition)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nSimulasi dihentikan.")


if __name__ == "__main__":
    main()
