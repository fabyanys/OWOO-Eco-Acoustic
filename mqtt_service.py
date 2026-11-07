import re
import json
import hmac
import queue
import base64
import hashlib
import threading
import time
import paho.mqtt.client as mqtt

import database as db

TOPIC_REGEX = re.compile(r"^application/([^/]+)/device/([^/]+)/event/up$", re.IGNORECASE)


class EventBroadcaster:
    """Thread-safe Server-Sent Events (SSE) hub for real-time browser updates."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clients: list[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=64)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def publish(self, event_type: str, data: dict):
        payload = {"event": event_type, "data": data}
        with self._lock:
            stale = []
            for q in self._clients:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    stale.append(q)
            for q in stale:
                if q in self._clients:
                    self._clients.remove(q)


broadcaster = EventBroadcaster()


def _extract_first(d: dict, keys: tuple, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    # Case-insensitive fallback
    lower_map = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        lk = k.lower()
        if lk in lower_map and lower_map[lk] is not None:
            return lower_map[lk]
    return default


def parse_chirpstack_payload(raw_bytes: bytes) -> dict:
    """
    Parses ChirpStack v4/v3 uplink JSON, Base64 'data' frame, or direct MQTT JSON payload.
    Extracts: aci, ndsi, classification, confidence, rssi, snr, key, hmac.
    """
    text = raw_bytes.decode("utf-8", errors="replace").strip()
    outer = json.loads(text)
    if not isinstance(outer, dict):
        raise ValueError("MQTT payload must be a JSON object.")

    # Extract ChirpStack rxInfo RSSI / SNR if available
    rssi = _extract_first(outer, ("rssi",), -78)
    snr = _extract_first(outer, ("snr", "loRaSNR"), 8.5)
    rx_info = outer.get("rxInfo")
    if isinstance(rx_info, list) and len(rx_info) > 0 and isinstance(rx_info[0], dict):
        rssi = rx_info[0].get("rssi", rssi)
        snr = rx_info[0].get("snr", rx_info[0].get("loRaSNR", snr))

    # Candidate inner object from ChirpStack codec
    inner = {}
    if isinstance(outer.get("object"), dict) and outer["object"]:
        inner = outer["object"]
    elif isinstance(outer.get("objectJSON"), str) and outer["objectJSON"].strip():
        try:
            parsed_obj = json.loads(outer["objectJSON"])
            if isinstance(parsed_obj, dict):
                inner = parsed_obj
        except json.JSONDecodeError:
            pass
    elif isinstance(outer.get("data"), str) and outer["data"].strip():
        # Attempt Base64 decode of raw LoRaWAN frmPayload
        try:
            decoded_bytes = base64.b64decode(outer["data"])
            decoded_str = decoded_bytes.decode("utf-8", errors="ignore").strip()
            if decoded_str.startswith("{") and decoded_str.endswith("}"):
                inner = json.loads(decoded_str)
            elif "," in decoded_str:
                # CSV compact format: "0.76,0.68,Ambient Hutan"
                parts = [p.strip() for p in decoded_str.split(",")]
                if len(parts) >= 3:
                    inner = {
                        "aci": float(parts[0]),
                        "ndsi": float(parts[1]),
                        "classification": parts[2],
                    }
        except Exception:
            pass

    merged = {**outer, **inner}

    aci = _extract_first(merged, ("aci", "ACI", "acoustic_complexity", "aci_index", "aci_score"))
    ndsi = _extract_first(merged, ("ndsi", "NDSI", "soundscape_index", "ndsi_index", "ndsi_score"))
    classification = _extract_first(
        merged,
        (
            "classification",
            "audio_classification",
            "klasifikasi",
            "audio_class",
            "sound_class",
            "class",
            "label",
            "condition",
            "kondisi",
            "status",
        ),
        "Ambient Hutan",
    )
    confidence = _extract_first(merged, ("confidence", "conf", "probability", "prob"), 0.94)
    provided_key = _extract_first(merged, ("node_key", "key", "app_key", "device_key", "secret"))
    provided_hmac = _extract_first(merged, ("hmac", "signature", "sig"))

    if aci is None or ndsi is None:
        raise ValueError("Payload tidak memiliki field 'aci' atau 'ndsi'.")

    return {
        "aci": float(aci),
        "ndsi": float(ndsi),
        "classification": classification,
        "confidence": float(confidence) if float(confidence) <= 1.0 else float(confidence) / 100.0,
        "rssi": int(rssi) if rssi is not None else -78,
        "snr": float(snr) if snr is not None else 8.5,
        "provided_key": str(provided_key).strip() if provided_key is not None else None,
        "provided_hmac": str(provided_hmac).strip() if provided_hmac is not None else None,
        "raw_text": text,
    }


def verify_node_credentials(node: dict, parsed: dict, enforce_strict: bool = False) -> tuple[bool, bool]:
    """
    Returns (allowed_to_process, key_verified_flag).
    Validates per-node unique key or HMAC-SHA256 signature if provided or strictly enforced.
    """
    expected_key = (node.get("node_key") or "").strip()
    prov_key = parsed.get("provided_key")
    prov_hmac = parsed.get("provided_hmac")

    if prov_key:
        matched = hmac.compare_digest(prov_key.lower(), expected_key.lower())
        return (matched, matched)

    if prov_hmac:
        msg = f"{parsed['aci']}:{parsed['ndsi']}:{parsed['classification']}".encode("utf-8")
        digest = hmac.new(expected_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        matched = hmac.compare_digest(prov_hmac.lower(), digest.lower())
        return (matched, matched)

    if enforce_strict:
        return (False, False)

    # Standard ChirpStack uplinks are already AES-128 AppKey authenticated at LNS layer
    return (True, True)


class ChirpStackMQTTManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.client: mqtt.Client | None = None
        self.connected = False
        self.last_error: str = ""
        self.subscribed_topics: set[str] = set()
        self.messages_received = 0
        self.last_message_at: str | None = None
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None

    def start(self):
        self._connect_client()
        if self._monitor_thread is None or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(target=self._reconnect_watcher, daemon=True)
            self._monitor_thread.start()

    def _create_paho_client(self) -> mqtt.Client:
        client_id = f"owoo-rpi-monitor-{int(time.time()) % 100000}"
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        else:
            client = mqtt.Client(client_id=client_id)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def _connect_client(self):
        cfg = db.get_mqtt_settings()
        with self._lock:
            if self.client is not None:
                try:
                    self.client.loop_stop()
                    self.client.disconnect()
                except Exception:
                    pass
                self.client = None
                self.connected = False
                self.subscribed_topics.clear()

            client = self._create_paho_client()
            if cfg["mqtt_username"]:
                client.username_pw_set(cfg["mqtt_username"], cfg["mqtt_password"])

            self.client = client
            try:
                client.connect_async(cfg["mqtt_host"], int(cfg["mqtt_port"]), keepalive=60)
                client.loop_start()
                self.last_error = ""
                db.write_server_log("MQTT", f"Connecting to ChirpStack MQTT Broker at {cfg['mqtt_host']}:{cfg['mqtt_port']}...")
            except Exception as exc:
                self.connected = False
                self.last_error = str(exc)
                db.write_server_log("MQTT", f"MQTT Broker standby ({cfg['mqtt_host']}:{cfg['mqtt_port']}): {exc}")

    def reload_connection(self):
        self._connect_client()
        broadcaster.publish("mqtt_status", self.get_status())

    def _reconnect_watcher(self):
        while not self._stop_event.is_set():
            time.sleep(8)
            if not self.connected and self.client is not None:
                cfg = db.get_mqtt_settings()
                try:
                    self.client.reconnect()
                except Exception as exc:
                    self.last_error = f"Menunggu broker {cfg['mqtt_host']}:{cfg['mqtt_port']} ({exc.__class__.__name__})"

    def _on_connect(self, client, userdata, flags, reason_code, *args):
        rc_ok = (reason_code == 0) or (str(reason_code).lower() == "success")
        if rc_ok:
            self.connected = True
            self.last_error = ""
            db.write_server_log("MQTT", "Connected to ChirpStack MQTT Broker.")
            self.sync_subscriptions()
        else:
            self.connected = False
            self.last_error = f"Broker refused connection: {reason_code}"
            db.write_server_log("WARN", self.last_error)
        broadcaster.publish("mqtt_status", self.get_status())

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        self.subscribed_topics.clear()
        broadcaster.publish("mqtt_status", self.get_status())

    def sync_subscriptions(self):
        nodes = db.list_end_nodes()
        desired_topics = {f"application/{n['app_id']}/device/{n['dev_eui']}/event/up" for n in nodes}

        with self._lock:
            if not self.client or not self.connected:
                return

            # Unsubscribe removed topics
            for old_topic in list(self.subscribed_topics - desired_topics):
                try:
                    self.client.unsubscribe(old_topic)
                    self.subscribed_topics.remove(old_topic)
                except Exception:
                    pass

            # Subscribe active node topics
            for topic in desired_topics:
                if topic not in self.subscribed_topics:
                    try:
                        self.client.subscribe(topic, qos=1)
                        self.subscribed_topics.add(topic)
                        db.write_server_log("MQTT", f"Subscribed to End Node topic: {topic}")
                    except Exception as exc:
                        db.write_server_log("ERROR", f"Failed subscribing {topic}: {exc}")

        broadcaster.publish("mqtt_status", self.get_status())

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        self.process_incoming_topic_message(topic, msg.payload)

    def process_incoming_topic_message(self, topic: str, raw_payload: bytes) -> dict | None:
        match = TOPIC_REGEX.match(topic.strip())
        if not match:
            db.write_server_log("WARN", f"Ignored MQTT topic outside ChirpStack pattern: {topic}")
            return None

        app_id, dev_eui = match.group(1), match.group(2)
        node = db.get_end_node_by_topic_ids(app_id, dev_eui)
        if not node:
            db.write_server_log("WARN", f"Received uplink for unregistered node app={app_id}, devEui={dev_eui}")
            return None

        try:
            parsed = parse_chirpstack_payload(raw_payload)
        except Exception as exc:
            db.write_server_log("ERROR", f"Invalid payload on {topic}: {exc}")
            return None

        cfg = db.get_mqtt_settings()
        allowed, key_ok = verify_node_credentials(node, parsed, enforce_strict=cfg["enforce_node_key"])
        if not allowed:
            db.write_server_log(
                "SECURITY",
                f"Rejected uplink on {topic}: Node Key / HMAC mismatch for End Node '{node['name']}'!",
            )
            return None

        entry = db.record_telemetry(
            node_id=node["id"],
            aci=parsed["aci"],
            ndsi=parsed["ndsi"],
            classification_raw=parsed["classification"],
            confidence=parsed["confidence"],
            rssi=parsed["rssi"],
            snr=parsed["snr"],
            key_verified=1 if key_ok else 0,
            raw_payload=parsed["raw_text"],
        )

        if entry:
            self.messages_received += 1
            self.last_message_at = entry["recorded_at"]
            updated_node = db.get_end_node(node["id"])
            broadcaster.publish(
                "telemetry",
                {
                    "log": entry,
                    "node": updated_node,
                    "mqtt": self.get_status(),
                },
            )
        return entry

    def publish_or_simulate_uplink(self, node: dict, payload_dict: dict) -> dict | None:
        """
        If connected to real MQTT broker, publishes straight to `application/<appid>/device/<devui>/event/up`
        so the full ChirpStack MQTT subscriber loop receives it; otherwise processes directly via local pipeline.
        """
        topic = f"application/{node['app_id']}/device/{node['dev_eui']}/event/up"
        raw_bytes = json.dumps(payload_dict).encode("utf-8")

        if self.connected and self.client is not None:
            info = self.client.publish(topic, raw_bytes, qos=1)
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                return {"published_via_mqtt": True, "topic": topic}

        entry = self.process_incoming_topic_message(topic, raw_bytes)
        return {"published_via_mqtt": False, "topic": topic, "entry": entry}

    def get_status(self) -> dict:
        cfg = db.get_mqtt_settings()
        nodes = db.list_end_nodes()
        return {
            "connected": self.connected,
            "host": cfg["mqtt_host"],
            "port": cfg["mqtt_port"],
            "username": cfg["mqtt_username"],
            "enforce_node_key": cfg["enforce_node_key"],
            "registered_nodes": len(nodes),
            "subscribed_count": len(self.subscribed_topics),
            "topics": sorted(list(self.subscribed_topics))
            if self.subscribed_topics
            else [n["mqtt_topic"] for n in nodes],
            "messages_received": self.messages_received,
            "last_message_at": self.last_message_at,
            "last_error": self.last_error,
        }


mqtt_manager = ChirpStackMQTTManager()
