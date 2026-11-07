import os
import json
import math
import random
import secrets
import sqlite3
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(DATA_DIR, "owoo.db")
UPLINK_LOG_FILE = os.path.join(LOGS_DIR, "telemetry_uplinks.log")
SERVER_LOG_FILE = os.path.join(LOGS_DIR, "owoo_server.log")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def write_server_log(level: str, message: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level.upper()}] {message}\n"
    try:
        with open(SERVER_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def write_uplink_log(entry: dict):
    ts = entry.get("recorded_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"[{ts}] TOPIC={entry['mqtt_topic']} | NODE=\"{entry['node_name']}\" | "
        f"LOC=\"{entry['location_name']}\" ({entry['latitude']:.4f}, {entry['longitude']:.4f}) | "
        f"ACI={entry['aci']:.3f} | NDSI={entry['ndsi']:+.3f} | "
        f"AUDIO=\"{entry['classification']}\" [{entry['condition'].upper()}] | "
        f"CONF={entry.get('confidence', 0.95)*100:.1f}% | KEY_OK={bool(entry.get('key_verified', 1))}\n"
    )
    try:
        with open(UPLINK_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def normalize_audio_classification(raw_value) -> tuple[str, str]:
    """
    Maps incoming classification label/code into the 3 canonical OWOO conditions:
      1. ('Chainsaw', 'Threat')
      2. ('Ambient Hutan', 'Biodiversity')
      3. ('Rain/Wind', 'Noise')
    """
    if raw_value is None:
        return ("Ambient Hutan", "Biodiversity")

    if isinstance(raw_value, (int, float)):
        idx = int(raw_value)
        if idx == 1 or idx == 2:
            if idx == 1:
                return ("Chainsaw", "Threat")
            return ("Rain/Wind", "Noise")
        return ("Ambient Hutan", "Biodiversity")

    val = str(raw_value).strip().lower()
    if any(k in val for k in ("chainsaw", "threat", "thread", "saw", "logging", "illegal", "gergaji", "motor")):
        return ("Chainsaw", "Threat")
    if any(k in val for k in ("rain", "wind", "noise", "hujan", "angin", "storm", "weather", "geophony")):
        return ("Rain/Wind", "Noise")
    if any(k in val for k in ("ambient", "hutan", "biodiversity", "bird", "insect", "wildlife", "forest", "biophony", "safe", "normal")):
        return ("Ambient Hutan", "Biodiversity")

    return ("Ambient Hutan", "Biodiversity")


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'Conservation Operator',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS end_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location_name TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            app_id TEXT NOT NULL,
            dev_eui TEXT UNIQUE NOT NULL,
            node_key TEXT NOT NULL,
            mqtt_topic TEXT NOT NULL,
            last_aci REAL,
            last_ndsi REAL,
            last_classification TEXT,
            last_condition TEXT,
            last_confidence REAL,
            last_rssi INTEGER,
            last_snr REAL,
            last_seen TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telemetry_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL,
            node_name TEXT NOT NULL,
            location_name TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            app_id TEXT NOT NULL,
            dev_eui TEXT NOT NULL,
            mqtt_topic TEXT NOT NULL,
            aci REAL NOT NULL,
            ndsi REAL NOT NULL,
            classification TEXT NOT NULL,
            condition TEXT NOT NULL,
            confidence REAL DEFAULT 0.94,
            rssi INTEGER DEFAULT -78,
            snr REAL DEFAULT 8.5,
            key_verified INTEGER DEFAULT 1,
            raw_payload TEXT,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (node_id) REFERENCES end_nodes(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_logs_recorded_at ON telemetry_logs(recorded_at DESC);
        CREATE INDEX IF NOT EXISTS idx_logs_node_id ON telemetry_logs(node_id);
        CREATE INDEX IF NOT EXISTS idx_logs_condition ON telemetry_logs(condition);

        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )

    # Default MQTT ChirpStack settings (Local Raspberry Pi Mosquitto / ChirpStack Broker)
    default_settings = {
        "mqtt_host": os.environ.get("MQTT_HOST", "127.0.0.1"),
        "mqtt_port": os.environ.get("MQTT_PORT", "1883"),
        "mqtt_username": os.environ.get("MQTT_USERNAME", ""),
        "mqtt_password": os.environ.get("MQTT_PASSWORD", ""),
        "enforce_node_key": "0",
    }
    for k, v in default_settings.items():
        cur.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)", (k, str(v)))

    # Seed default operator account if empty
    cur.execute("SELECT COUNT(*) AS cnt FROM users")
    if cur.fetchone()["cnt"] == 0:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO users (full_name, username, email, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "OWOO Field Ranger",
                "admin",
                "admin@owoo.eco",
                generate_password_hash("owoo2026"),
                "Lead MRV Coordinator",
                now_str,
            ),
        )
        write_server_log("INFO", "Initialized default user account 'admin' in SQLite database.")

    # Seed realistic Katingan Mentaya Project End Nodes & 24h Eco-Acoustic Telemetry if empty
    cur.execute("SELECT COUNT(*) AS cnt FROM end_nodes")
    if cur.fetchone()["cnt"] == 0:
        _seed_katingan_mentaya_data(cur)

    conn.commit()
    conn.close()


def _seed_katingan_mentaya_data(cur):
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    seed_nodes = [
        {
            "name": "SylvaSense KM-01",
            "location_name": "Zona Buffer Timur — Katingan Hulu",
            "latitude": -2.3850,
            "longitude": 113.2150,
            "app_id": "owoo-katingan",
            "dev_eui": "70b3d57ed006a101",
            "node_key": "9f84c2e10b734d6a81f23c94e50a11b2",
            "profile": "threat_recent",
        },
        {
            "name": "SylvaSense KM-02",
            "location_name": "Zona Inti Gambut — Mentaya Tengah",
            "latitude": -2.6120,
            "longitude": 113.1080,
            "app_id": "owoo-katingan",
            "dev_eui": "70b3d57ed006a102",
            "node_key": "4a17d9c83e2041f5b9028c71d63e44a9",
            "profile": "biodiversity_rich",
        },
        {
            "name": "SylvaSense KM-03",
            "location_name": "Koridor Satwa — Sebangau Barat",
            "latitude": -2.7480,
            "longitude": 113.1680,
            "app_id": "owoo-katingan",
            "dev_eui": "70b3d57ed006a103",
            "node_key": "c72e55a91d0847b39e6401f2a88c30d7",
            "profile": "biodiversity_steady",
        },
        {
            "name": "SylvaSense KM-04",
            "location_name": "Pesisir Sungai Mentaya — Sektor Selatan",
            "latitude": -2.8640,
            "longitude": 113.0520,
            "app_id": "owoo-katingan",
            "dev_eui": "70b3d57ed006a104",
            "node_key": "2d90b6f47c124e8aa5317f09c44b82e1",
            "profile": "weather_noise",
        },
    ]

    rng = random.Random(20261107)

    for n in seed_nodes:
        topic = f"application/{n['app_id']}/device/{n['dev_eui']}/event/up"
        cur.execute(
            """
            INSERT INTO end_nodes (
                name, location_name, latitude, longitude,
                app_id, dev_eui, node_key, mqtt_topic, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                n["name"],
                n["location_name"],
                n["latitude"],
                n["longitude"],
                n["app_id"],
                n["dev_eui"],
                n["node_key"],
                topic,
                now_str,
            ),
        )
        node_id = cur.lastrowid

        # Generate 18 uplinks across the past 24h for rich charts & reports
        last_entry = None
        for step in range(18):
            dt = now - timedelta(minutes=(17 - step) * 80 + rng.randint(0, 12))
            ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            hour_frac = dt.hour + dt.minute / 60.0
            # Natural dawn/dusk chorus curve for tropical Borneo rainforest
            chorus_boost = 0.14 * math.exp(-((hour_frac - 6.0) ** 2) / 5.0) + 0.12 * math.exp(-((hour_frac - 18.0) ** 2) / 5.0)

            prof = n["profile"]
            if prof == "threat_recent" and step >= 15:
                classification, condition = ("Chainsaw", "Threat")
                aci = round(rng.uniform(0.38, 0.52), 3)
                ndsi = round(rng.uniform(-0.72, -0.44), 3)
                conf = round(rng.uniform(0.91, 0.98), 3)
            elif prof == "weather_noise" and step in (7, 8, 16, 17):
                classification, condition = ("Rain/Wind", "Noise")
                aci = round(rng.uniform(0.51, 0.64), 3)
                ndsi = round(rng.uniform(-0.12, 0.18), 3)
                conf = round(rng.uniform(0.87, 0.95), 3)
            else:
                if rng.random() < 0.12:
                    classification, condition = ("Rain/Wind", "Noise")
                    aci = round(rng.uniform(0.50, 0.63), 3)
                    ndsi = round(rng.uniform(-0.05, 0.22), 3)
                    conf = round(rng.uniform(0.86, 0.94), 3)
                else:
                    classification, condition = ("Ambient Hutan", "Biodiversity")
                    aci = round(min(0.96, rng.uniform(0.68, 0.84) + chorus_boost), 3)
                    ndsi = round(min(0.95, rng.uniform(0.62, 0.86) + chorus_boost * 0.4), 3)
                    conf = round(rng.uniform(0.90, 0.99), 3)

            rssi = rng.randint(-108, -72)
            snr = round(rng.uniform(4.2, 11.5), 1)
            payload_obj = {
                "deviceInfo": {"applicationId": n["app_id"], "devEui": n["dev_eui"], "deviceName": n["name"]},
                "object": {"aci": aci, "ndsi": ndsi, "classification": classification, "confidence": conf},
                "rxInfo": [{"rssi": rssi, "snr": snr}],
            }

            cur.execute(
                """
                INSERT INTO telemetry_logs (
                    node_id, node_name, location_name, latitude, longitude,
                    app_id, dev_eui, mqtt_topic, aci, ndsi,
                    classification, condition, confidence, rssi, snr,
                    key_verified, raw_payload, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    n["name"],
                    n["location_name"],
                    n["latitude"],
                    n["longitude"],
                    n["app_id"],
                    n["dev_eui"],
                    topic,
                    aci,
                    ndsi,
                    classification,
                    condition,
                    conf,
                    rssi,
                    snr,
                    1,
                    json.dumps(payload_obj),
                    ts_str,
                ),
            )

            last_entry = {
                "mqtt_topic": topic,
                "node_name": n["name"],
                "location_name": n["location_name"],
                "latitude": n["latitude"],
                "longitude": n["longitude"],
                "aci": aci,
                "ndsi": ndsi,
                "classification": classification,
                "condition": condition,
                "confidence": conf,
                "rssi": rssi,
                "snr": snr,
                "key_verified": 1,
                "recorded_at": ts_str,
            }
            write_uplink_log(last_entry)

        if last_entry:
            cur.execute(
                """
                UPDATE end_nodes
                SET last_aci = ?, last_ndsi = ?, last_classification = ?, last_condition = ?,
                    last_confidence = ?, last_rssi = ?, last_snr = ?, last_seen = ?
                WHERE id = ?
                """,
                (
                    last_entry["aci"],
                    last_entry["ndsi"],
                    last_entry["classification"],
                    last_entry["condition"],
                    last_entry["confidence"],
                    last_entry["rssi"],
                    last_entry["snr"],
                    last_entry["recorded_at"],
                    node_id,
                ),
            )

    write_server_log("INFO", "Seeded 4 Katingan Mentaya Project End Nodes & 24h Eco-Acoustic logs.")


# ---------------- USER AUTHENTICATION ---------------- #

def create_user(full_name: str, username: str, email: str, password: str, role: str = "Conservation Operator"):
    username_clean = username.strip().lower()
    email_clean = email.strip().lower()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pw_hash = generate_password_hash(password)

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (full_name, username, email, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (full_name.strip(), username_clean, email_clean, pw_hash, role.strip(), now_str),
        )
        conn.commit()
        user_id = cur.lastrowid
        write_server_log("AUTH", f"New user registered: username='{username_clean}', email='{email_clean}'")
        return get_user_by_id(user_id)
    except sqlite3.IntegrityError as exc:
        err = str(exc).lower()
        if "username" in err:
            raise ValueError("Username sudah terdaftar. Silakan pilih username lain.")
        if "email" in err:
            raise ValueError("Email sudah terdaftar. Silakan gunakan email lain.")
        raise ValueError("Akun dengan username atau email tersebut sudah ada.")
    finally:
        conn.close()


def authenticate_user(identifier: str, password: str):
    ident = identifier.strip().lower()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username) = ? OR LOWER(email) = ?",
            (ident, ident),
        ).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            write_server_log("AUTH", f"User login successful: '{row['username']}'")
            return {
                "id": row["id"],
                "full_name": row["full_name"],
                "username": row["username"],
                "email": row["email"],
                "role": row["role"],
                "created_at": row["created_at"],
            }
        return None
    finally:
        conn.close()


def get_user_by_id(user_id: int):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, full_name, username, email, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------- END NODE MANAGEMENT ---------------- #

def list_end_nodes():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM end_nodes ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_end_node(node_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM end_nodes WHERE id = ?", (node_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_end_node_by_topic_ids(app_id: str, dev_eui: str):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM end_nodes WHERE LOWER(app_id) = LOWER(?) AND LOWER(dev_eui) = LOWER(?)",
            (app_id.strip(), dev_eui.strip()),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_end_node(
    name: str,
    location_name: str,
    latitude: float,
    longitude: float,
    app_id: str,
    dev_eui: str,
    node_key: str | None = None,
):
    app_clean = app_id.strip()
    dev_clean = dev_eui.strip().lower()
    key_clean = (node_key or "").strip() or secrets.token_hex(16)
    mqtt_topic = f"application/{app_clean}/device/{dev_clean}/event/up"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO end_nodes (
                name, location_name, latitude, longitude,
                app_id, dev_eui, node_key, mqtt_topic, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name.strip(),
                location_name.strip(),
                float(latitude),
                float(longitude),
                app_clean,
                dev_clean,
                key_clean,
                mqtt_topic,
                now_str,
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
        write_server_log(
            "NODE",
            f"Added End Node '{name.strip()}' at '{location_name.strip()}' ({latitude}, {longitude}) -> Topic: {mqtt_topic}",
        )
        return get_end_node(new_id)
    except sqlite3.IntegrityError:
        raise ValueError(f"End Node dengan DevEUI '{dev_clean}' sudah terdaftar.")
    finally:
        conn.close()


def delete_end_node(node_id: int) -> dict | None:
    node = get_end_node(node_id)
    if not node:
        return None
    conn = get_db()
    try:
        conn.execute("DELETE FROM end_nodes WHERE id = ?", (node_id,))
        conn.commit()
        write_server_log("NODE", f"Removed End Node '{node['name']}' (Topic: {node['mqtt_topic']})")
        return node
    finally:
        conn.close()


# ---------------- TELEMETRY & REPORTS ---------------- #

def record_telemetry(
    node_id: int,
    aci: float,
    ndsi: float,
    classification_raw,
    confidence: float = 0.94,
    rssi: int = -78,
    snr: float = 8.5,
    key_verified: int = 1,
    raw_payload: str = "",
    recorded_at: str | None = None,
):
    node = get_end_node(node_id)
    if not node:
        return None

    classification, condition = normalize_audio_classification(classification_raw)
    ts_str = recorded_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    aci_val = round(float(aci), 4)
    ndsi_val = round(max(-1.0, min(1.0, float(ndsi))), 4)
    conf_val = round(max(0.0, min(1.0, float(confidence))), 4)

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO telemetry_logs (
                node_id, node_name, location_name, latitude, longitude,
                app_id, dev_eui, mqtt_topic, aci, ndsi,
                classification, condition, confidence, rssi, snr,
                key_verified, raw_payload, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node["id"],
                node["name"],
                node["location_name"],
                node["latitude"],
                node["longitude"],
                node["app_id"],
                node["dev_eui"],
                node["mqtt_topic"],
                aci_val,
                ndsi_val,
                classification,
                condition,
                conf_val,
                int(rssi) if rssi is not None else -80,
                float(snr) if snr is not None else 7.5,
                int(bool(key_verified)),
                raw_payload,
                ts_str,
            ),
        )
        log_id = cur.lastrowid

        cur.execute(
            """
            UPDATE end_nodes
            SET last_aci = ?, last_ndsi = ?, last_classification = ?, last_condition = ?,
                last_confidence = ?, last_rssi = ?, last_snr = ?, last_seen = ?
            WHERE id = ?
            """,
            (
                aci_val,
                ndsi_val,
                classification,
                condition,
                conf_val,
                int(rssi) if rssi is not None else -80,
                float(snr) if snr is not None else 7.5,
                ts_str,
                node["id"],
            ),
        )
        conn.commit()

        entry = {
            "id": log_id,
            "node_id": node["id"],
            "node_name": node["name"],
            "location_name": node["location_name"],
            "latitude": node["latitude"],
            "longitude": node["longitude"],
            "app_id": node["app_id"],
            "dev_eui": node["dev_eui"],
            "mqtt_topic": node["mqtt_topic"],
            "aci": aci_val,
            "ndsi": ndsi_val,
            "classification": classification,
            "condition": condition,
            "confidence": conf_val,
            "rssi": int(rssi) if rssi is not None else -80,
            "snr": float(snr) if snr is not None else 7.5,
            "key_verified": int(bool(key_verified)),
            "recorded_at": ts_str,
        }
        write_uplink_log(entry)
        return entry
    finally:
        conn.close()


def query_telemetry_logs(
    node_id: int | None = None,
    condition: str | None = None,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = 300,
):
    clauses = []
    params = []

    if node_id:
        clauses.append("node_id = ?")
        params.append(int(node_id))
    if condition and condition.lower() != "all":
        clauses.append("LOWER(condition) = LOWER(?)")
        params.append(condition.strip())
    if search:
        q = f"%{search.strip()}%"
        clauses.append(
            "(node_name LIKE ? OR location_name LIKE ? OR dev_eui LIKE ? OR app_id LIKE ? OR classification LIKE ?)"
        )
        params.extend([q, q, q, q, q])
    if date_from:
        clauses.append("recorded_at >= ?")
        params.append(f"{date_from.strip()} 00:00:00" if len(date_from.strip()) == 10 else date_from.strip())
    if date_to:
        clauses.append("recorded_at <= ?")
        params.append(f"{date_to.strip()} 23:59:59" if len(date_to.strip()) == 10 else date_to.strip())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    conn = get_db()
    try:
        rows = conn.execute(
            f"""
            SELECT id, node_id, node_name, location_name, latitude, longitude,
                   app_id, dev_eui, mqtt_topic, aci, ndsi,
                   classification, condition, confidence, rssi, snr,
                   key_verified, recorded_at
            FROM telemetry_logs
            {where_sql}
            ORDER BY recorded_at DESC, id DESC
            {limit_sql}
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_timeseries_data(node_id: int | None = None, limit: int = 48):
    conn = get_db()
    try:
        if node_id:
            rows = conn.execute(
                """
                SELECT id, node_id, node_name, aci, ndsi, classification, condition, confidence, recorded_at
                FROM telemetry_logs
                WHERE node_id = ?
                ORDER BY recorded_at DESC, id DESC
                LIMIT ?
                """,
                (int(node_id), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, node_id, node_name, aci, ndsi, classification, condition, confidence, recorded_at
                FROM telemetry_logs
                ORDER BY recorded_at DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        result = [dict(r) for r in rows]
        result.reverse()
        return result
    finally:
        conn.close()


def get_node_summary_reports():
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                n.id AS node_id,
                n.name AS node_name,
                n.location_name,
                n.latitude,
                n.longitude,
                n.app_id,
                n.dev_eui,
                n.mqtt_topic,
                n.last_aci,
                n.last_ndsi,
                n.last_classification,
                n.last_condition,
                n.last_seen,
                COUNT(t.id) AS total_uplinks,
                ROUND(AVG(t.aci), 3) AS avg_aci,
                ROUND(MAX(t.aci), 3) AS max_aci,
                ROUND(AVG(t.ndsi), 3) AS avg_ndsi,
                SUM(CASE WHEN t.condition = 'Threat' THEN 1 ELSE 0 END) AS threat_count,
                SUM(CASE WHEN t.condition = 'Biodiversity' THEN 1 ELSE 0 END) AS biodiversity_count,
                SUM(CASE WHEN t.condition = 'Noise' THEN 1 ELSE 0 END) AS noise_count
            FROM end_nodes n
            LEFT JOIN telemetry_logs t ON t.node_id = n.id
            GROUP BY n.id
            ORDER BY n.id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def read_local_server_logs(max_lines: int = 80) -> list[str]:
    if not os.path.exists(UPLINK_LOG_FILE):
        return []
    try:
        with open(UPLINK_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [ln.rstrip("\n") for ln in lines[-max_lines:]]
    except OSError:
        return []


# ---------------- SYSTEM SETTINGS ---------------- #

def get_mqtt_settings() -> dict:
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM system_settings").fetchall()
        cfg = {r["key"]: r["value"] for r in rows}
        return {
            "mqtt_host": cfg.get("mqtt_host", "127.0.0.1"),
            "mqtt_port": int(cfg.get("mqtt_port", "1883") or 1883),
            "mqtt_username": cfg.get("mqtt_username", ""),
            "mqtt_password": cfg.get("mqtt_password", ""),
            "enforce_node_key": cfg.get("enforce_node_key", "0") == "1",
        }
    finally:
        conn.close()


def update_mqtt_settings(host: str, port: int, username: str = "", password: str = "", enforce_node_key: bool = False):
    conn = get_db()
    try:
        pairs = [
            ("mqtt_host", host.strip() or "127.0.0.1"),
            ("mqtt_port", str(int(port))),
            ("mqtt_username", username.strip()),
            ("mqtt_password", password),
            ("enforce_node_key", "1" if enforce_node_key else "0"),
        ]
        for k, v in pairs:
            conn.execute(
                "INSERT INTO system_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, v),
            )
        conn.commit()
        write_server_log("MQTT", f"Updated MQTT Broker configuration to {host}:{port}")
        return get_mqtt_settings()
    finally:
        conn.close()
