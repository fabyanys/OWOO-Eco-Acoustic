import io
import os
import csv
import json
import secrets
from datetime import datetime
from functools import wraps

import requests
from PIL import Image, ImageDraw
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

import database as db
from mqtt_service import broadcaster, mqtt_manager
from pdf_report import generate_pdf_report_bytes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TILES_DIR = os.path.join(BASE_DIR, "data", "tiles")
SECRET_FILE = os.path.join(BASE_DIR, "data", ".flask_secret")

os.makedirs(TILES_DIR, exist_ok=True)


def _load_or_create_secret() -> str:
    env_sec = os.environ.get("OWOO_SECRET_KEY")
    if env_sec:
        return env_sec
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "r", encoding="utf-8") as f:
            val = f.read().strip()
            if val:
                return val
    sec = secrets.token_hex(32)
    with open(SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(sec)
    return sec


app = Flask(__name__)
app.secret_key = _load_or_create_secret()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

_tile_http = requests.Session()
_tile_http.headers.update({"User-Agent": "OWOO-EcoAcoustic-RaspberryPi/1.0"})


# ---------------- AUTH & RBAC HELPERS ---------------- #

def get_current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.get_user_by_id(int(uid))


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Sesi berakhir. Silakan login kembali."}), 401
            return redirect(url_for("auth_page"))
        return fn(*args, **kwargs)

    return wrapper


def editor_required(fn):
    """
    Hanya mengizinkan Conservation Operator dan IoT Network Engineer untuk mengubah data
    (menambah/menghapus End Node maupun mengubah konfigurasi server MQTT).
    Forest Ranger dan MRV Auditor hanya memiliki akses lihat & unduh laporan.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"ok": False, "error": "Sesi berakhir. Silakan login kembali."}), 401
        if not user.get("can_modify"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Akses ditolak: Peran '{user.get('role')}' hanya dapat melihat data dan mengunduh laporan.",
                    }
                ),
                403,
            )
        return fn(*args, **kwargs)

    return wrapper


def _sanitize_node_for_user(node: dict | None, user: dict | None) -> dict | None:
    if not node:
        return None
    out = dict(node)
    if not user or not user.get("can_modify"):
        raw_k = out.get("node_key") or ""
        out["node_key"] = f"{raw_k[:4]}••••••••••••{raw_k[-4:]}" if len(raw_k) >= 8 else "••••••••"
    return out


# ---------------- PAGE ROUTES ---------------- #

@app.route("/login", methods=["GET"])
def auth_page():
    if get_current_user():
        return redirect(url_for("dashboard_page"))
    return render_template("auth.html")


@app.route("/logout", methods=["GET", "POST"])
def logout_route():
    user = get_current_user()
    if user:
        db.write_server_log("AUTH", f"User logged out: '{user['username']}'")
    session.clear()
    if request.method == "POST" or request.path.startswith("/api/"):
        return jsonify({"ok": True})
    return redirect(url_for("auth_page"))


@app.route("/", methods=["GET"])
@login_required
def dashboard_page():
    user = get_current_user()
    return render_template("dashboard.html", user=user)


# ---------------- AUTH API ---------------- #

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip()
    password = data.get("password") or ""

    if not identifier or not password:
        return jsonify({"ok": False, "error": "Username/email dan password wajib diisi."}), 400

    user = db.authenticate_user(identifier, password)
    if not user:
        return jsonify({"ok": False, "error": "Username/email atau password tidak sesuai."}), 401

    session["user_id"] = user["id"]
    return jsonify({"ok": True, "user": user})


@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.get_json(silent=True) or {}
    full_name = (data.get("full_name") or "").strip()
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    role = (data.get("role") or "Conservation Operator").strip()

    if not full_name or not username or not email or not password:
        return jsonify({"ok": False, "error": "Seluruh kolom registrasi wajib diisi."}), 400
    if len(username) < 3:
        return jsonify({"ok": False, "error": "Username minimal 3 karakter."}), 400
    if "@" not in email or "." not in email:
        return jsonify({"ok": False, "error": "Format alamat email tidak valid."}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "Password minimal 6 karakter."}), 400

    try:
        user = db.create_user(full_name=full_name, username=username, email=email, password=password, role=role)
        session["user_id"] = user["id"]
        return jsonify({"ok": True, "user": user})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# ---------------- MONITORING & END NODES API ---------------- #

@app.route("/api/bootstrap", methods=["GET"])
@login_required
def api_bootstrap():
    user = get_current_user()
    nodes = [_sanitize_node_for_user(n, user) for n in db.list_end_nodes()]
    timeseries = db.get_timeseries_data(limit=48)
    summaries = db.get_node_summary_reports()
    logs = db.query_telemetry_logs(limit=150)
    server_logs = db.read_local_server_logs(max_lines=70)
    mqtt_status = mqtt_manager.get_status()

    return jsonify(
        {
            "ok": True,
            "user": user,
            "nodes": nodes,
            "timeseries": timeseries,
            "summaries": summaries,
            "logs": logs,
            "server_logs": server_logs,
            "mqtt": mqtt_status,
        }
    )


@app.route("/api/timeseries", methods=["GET"])
@login_required
def api_timeseries():
    node_id = request.args.get("node_id", type=int)
    limit = request.args.get("limit", default=48, type=int)
    return jsonify({"ok": True, "timeseries": db.get_timeseries_data(node_id=node_id, limit=limit)})


@app.route("/api/nodes", methods=["GET"])
@login_required
def api_list_nodes():
    user = get_current_user()
    nodes = [_sanitize_node_for_user(n, user) for n in db.list_end_nodes()]
    return jsonify({"ok": True, "nodes": nodes})


@app.route("/api/nodes", methods=["POST"])
@editor_required
def api_create_node():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    location_name = (data.get("location_name") or "").strip()
    app_id = (data.get("app_id") or "").strip()
    dev_eui = (data.get("dev_eui") or "").strip()
    node_key = (data.get("node_key") or "").strip()

    try:
        latitude = float(data.get("latitude"))
        longitude = float(data.get("longitude"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Koordinat Latitude & Longitude harus berupa angka desimal valid."}), 400

    if not name or not location_name or not app_id or not dev_eui:
        return jsonify({"ok": False, "error": "Nama Node, Lokasi Penempatan, App ID, dan DevEUI wajib diisi."}), 400

    if "/" in app_id or "/" in dev_eui or "+" in app_id or "#" in dev_eui:
        return jsonify({"ok": False, "error": "App ID dan DevEUI tidak boleh mengandung karakter wildcard MQTT ('/', '+', '#')."}), 400

    try:
        node = db.create_end_node(
            name=name,
            location_name=location_name,
            latitude=latitude,
            longitude=longitude,
            app_id=app_id,
            dev_eui=dev_eui,
            node_key=node_key,
        )
        mqtt_manager.sync_subscriptions()
        broadcaster.publish("node_added", {"node_id": node["id"], "mqtt": mqtt_manager.get_status()})
        return jsonify({"ok": True, "node": node, "mqtt": mqtt_manager.get_status()})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/nodes/<int:node_id>", methods=["GET"])
@login_required
def api_node_detail(node_id: int):
    user = get_current_user()
    node = db.get_end_node(node_id)
    if not node:
        return jsonify({"ok": False, "error": "End Node tidak ditemukan."}), 404
    history = db.get_timeseries_data(node_id=node_id, limit=30)
    recent_logs = db.query_telemetry_logs(node_id=node_id, limit=25)
    return jsonify({"ok": True, "node": _sanitize_node_for_user(node, user), "history": history, "logs": recent_logs})


@app.route("/api/nodes/<int:node_id>", methods=["DELETE"])
@editor_required
def api_delete_node(node_id: int):
    removed = db.delete_end_node(node_id)
    if not removed:
        return jsonify({"ok": False, "error": "End Node tidak ditemukan."}), 404
    mqtt_manager.sync_subscriptions()
    broadcaster.publish("node_deleted", {"node_id": node_id, "mqtt": mqtt_manager.get_status()})
    return jsonify({"ok": True, "removed": removed, "mqtt": mqtt_manager.get_status()})


# ---------------- REPORTS, SERVER LOGS, CSV & PDF EXPORT ---------------- #

@app.route("/api/reports", methods=["GET"])
@login_required
def api_reports():
    node_id = request.args.get("node_id", type=int)
    condition = request.args.get("condition", default="all", type=str)
    search = request.args.get("search", default="", type=str)
    date_from = request.args.get("date_from", default="", type=str)
    date_to = request.args.get("date_to", default="", type=str)
    limit = request.args.get("limit", default=300, type=int)

    logs = db.query_telemetry_logs(
        node_id=node_id,
        condition=condition,
        search=search,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    summaries = db.get_node_summary_reports(
        node_id=node_id,
        condition=condition,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )
    server_logs = db.read_local_server_logs(max_lines=80)

    return jsonify(
        {
            "ok": True,
            "logs": logs,
            "summaries": summaries,
            "server_logs": server_logs,
        }
    )


@app.route("/api/reports/export.csv", methods=["GET"])
@login_required
def api_export_csv():
    node_id = request.args.get("node_id", type=int)
    condition = request.args.get("condition", default="all", type=str)
    search = request.args.get("search", default="", type=str)
    date_from = request.args.get("date_from", default="", type=str)
    date_to = request.args.get("date_to", default="", type=str)

    logs = db.query_telemetry_logs(
        node_id=node_id,
        condition=condition,
        search=search,
        date_from=date_from,
        date_to=date_to,
        limit=None,
    )

    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(
        [
            "Log ID",
            "Waktu Server Lokal",
            "Nama End Node",
            "Lokasi Penempatan",
            "Latitude",
            "Longitude",
            "ChirpStack App ID",
            "ChirpStack DevEUI",
            "MQTT Topic",
            "ACI (Acoustic Complexity Index)",
            "NDSI (Normalized Difference Soundscape Index)",
            "Klasifikasi Audio",
            "Kategori Kondisi",
            "Confidence (%)",
            "RSSI (dBm)",
            "SNR (dB)",
            "Key Verified",
        ]
    )

    for row in logs:
        writer.writerow(
            [
                row["id"],
                row["recorded_at"],
                row["node_name"],
                row["location_name"],
                f"{row['latitude']:.6f}",
                f"{row['longitude']:.6f}",
                row["app_id"],
                row["dev_eui"],
                row["mqtt_topic"],
                f"{row['aci']:.4f}",
                f"{row['ndsi']:.4f}",
                row["classification"],
                row["condition"],
                f"{(row['confidence'] or 0) * 100:.1f}",
                row["rssi"],
                row["snr"],
                "Verified" if row["key_verified"] else "Unverified",
            ]
        )

    csv_bytes = io.BytesIO(output.getvalue().encode("utf-8"))
    ts_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"owoo_katingan_report_{ts_name}.csv"
    db.write_server_log("REPORT", f"CSV report exported ({len(logs)} rows) -> {filename}")

    return send_file(
        csv_bytes,
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/reports/export.pdf", methods=["GET"])
@login_required
def api_export_pdf():
    user = get_current_user()
    node_id = request.args.get("node_id", type=int)
    condition = request.args.get("condition", default="all", type=str)
    search = request.args.get("search", default="", type=str)
    date_from = request.args.get("date_from", default="", type=str)
    date_to = request.args.get("date_to", default="", type=str)

    logs = db.query_telemetry_logs(
        node_id=node_id,
        condition=condition,
        search=search,
        date_from=date_from,
        date_to=date_to,
        limit=None,
    )
    summaries = db.get_node_summary_reports(
        node_id=node_id,
        condition=condition,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )

    node_label = "Semua End Node & Lokasi"
    if node_id:
        nd = db.get_end_node(node_id)
        if nd:
            node_label = f"{nd['name']} — {nd['location_name']}"

    cond_map = {
        "all": "Semua Kondisi (3 Kelas)",
        "threat": "Chainsaw (Threat)",
        "biodiversity": "Ambient Hutan (Biodiversity)",
        "noise": "Rain/Wind (Noise)",
    }
    cond_label = cond_map.get((condition or "all").lower(), condition)

    if date_from and date_to:
        date_label = f"{date_from} s/d {date_to}"
    elif date_from:
        date_label = f"Mulai {date_from}"
    elif date_to:
        date_label = f"Sampai {date_to}"
    else:
        date_label = "Seluruh Waktu Rekaman"

    filters_meta = {
        "node_label": node_label,
        "condition_label": cond_label,
        "date_label": date_label,
        "search": search.strip(),
    }

    pdf_data = generate_pdf_report_bytes(logs=logs, summaries=summaries, filters_meta=filters_meta, user=user)
    ts_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"owoo_katingan_report_{ts_name}.pdf"
    db.write_server_log("REPORT", f"PDF report exported by '{user['username']}' ({len(logs)} rows) -> {filename}")

    return send_file(
        io.BytesIO(pdf_data),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


# ---------------- MQTT SERVER SETTINGS & SSE STREAM ---------------- #

@app.route("/api/mqtt/settings", methods=["GET"])
@login_required
def api_get_mqtt_settings():
    user = get_current_user()
    settings = db.get_mqtt_settings()
    if not user or not user.get("can_modify"):
        settings["mqtt_password"] = ""
    return jsonify({"ok": True, "settings": settings, "status": mqtt_manager.get_status()})


@app.route("/api/mqtt/settings", methods=["POST"])
@editor_required
def api_save_mqtt_settings():
    data = request.get_json(silent=True) or {}
    host = (data.get("mqtt_host") or "127.0.0.1").strip()
    try:
        port = int(data.get("mqtt_port") or 1883)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Port MQTT harus berupa angka (misal 1883)."}), 400

    username = (data.get("mqtt_username") or "").strip()
    password = data.get("mqtt_password") or ""
    enforce_key = bool(data.get("enforce_node_key", False))

    cfg = db.update_mqtt_settings(host=host, port=port, username=username, password=password, enforce_node_key=enforce_key)
    mqtt_manager.reload_connection()
    return jsonify({"ok": True, "settings": cfg, "status": mqtt_manager.get_status()})


@app.route("/api/stream")
@login_required
def api_sse_stream():
    user = get_current_user()

    def event_stream():
        client_queue = broadcaster.subscribe()
        try:
            yield f"data: {json.dumps({'event': 'connected', 'data': mqtt_manager.get_status()})}\n\n"
            while True:
                try:
                    msg = client_queue.get(timeout=15.0)
                    if msg.get("event") == "telemetry" and isinstance(msg.get("data"), dict):
                        payload_copy = dict(msg["data"])
                        if "node" in payload_copy:
                            payload_copy["node"] = _sanitize_node_for_user(payload_copy["node"], user)
                        yield f"data: {json.dumps({'event': 'telemetry', 'data': payload_copy})}\n\n"
                    else:
                        yield f"data: {json.dumps(msg)}\n\n"
                except Exception:
                    yield ": keepalive\n\n"
        finally:
            broadcaster.unsubscribe(client_queue)

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return Response(event_stream(), headers=headers)


# ---------------- SATELLITE FOREST TILE CACHE & OFFLINE FALLBACK ---------------- #

def _generate_offline_canopy_tile(z: int, x: int, y: int) -> bytes:
    """Generates a dark-emerald bio-acoustic canopy texture tile when Raspberry Pi is 100% offline."""
    img = Image.new("RGB", (256, 256), (9, 31, 22))
    draw = ImageDraw.Draw(img)
    seed_val = (z * 73856093) ^ (x * 19349663) ^ (y * 83492791)
    for i in range(0, 256, 32):
        draw.line([(i, 0), (i, 256)], fill=(14, 46, 33), width=1)
        draw.line([(0, i), (256, i)], fill=(14, 46, 33), width=1)
    for k in range(28):
        cx = ((seed_val >> (k % 16)) + k * 37) % 240 + 8
        cy = ((seed_val >> ((k + 5) % 16)) + k * 53) % 240 + 8
        r = 10 + (k % 18)
        shade = (12 + (k % 10), 42 + (k % 24), 28 + (k % 14))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=shade)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


@app.route("/api/tiles/<int:z>/<int:x>/<int:y>.jpg")
def serve_forest_tile(z: int, x: int, y: int):
    tile_path = os.path.join(TILES_DIR, str(z), str(x), f"{y}.jpg")
    if os.path.exists(tile_path):
        return send_file(tile_path, mimetype="image/jpeg", max_age=86400 * 7)

    remote_url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    try:
        resp = _tile_http.get(remote_url, timeout=3.5)
        if resp.status_code == 200 and resp.content:
            os.makedirs(os.path.dirname(tile_path), exist_ok=True)
            with open(tile_path, "wb") as f:
                f.write(resp.content)
            return Response(resp.content, mimetype="image/jpeg")
    except Exception:
        pass

    fallback = _generate_offline_canopy_tile(z, x, y)
    return Response(fallback, mimetype="image/jpeg")


# ---------------- BOOTSTRAP ENTRYPOINT ---------------- #

def initialize_services():
    db.init_db()
    mqtt_manager.start()


if __name__ == "__main__":
    initialize_services()
    host = os.environ.get("OWOO_HOST", "0.0.0.0")
    port = int(os.environ.get("OWOO_PORT", "5000"))
    print("=" * 68)
    print("  OWOO Eco-Acoustic LoRaWAN MRV & Biodiversity Monitoring Server")
    print(f"  Local URL   : http://127.0.0.1:{port}")
    print(f"  Network URL : http://{host}:{port}")
    print("  MQTT Topic  : application/<appid>/device/<devui>/event/up")
    print("=" * 68)
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=12)
    except ImportError:
        app.run(host=host, port=port, threaded=True)
