#!/usr/bin/env bash
# ==========================================================================
# OWOO Eco-Acoustic LoRaWAN Monitoring — Automated Raspberry Pi Setup Script
# ==========================================================================
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="owoo-monitor"
PORT="${OWOO_PORT:-5000}"
MQTT_HOST="${MQTT_HOST:-127.0.0.1}"
MQTT_PORT="${MQTT_PORT:-1883}"

echo "=================================================================="
echo "  OWOO Bio-Acoustic Command Center — Raspberry Pi Installer"
echo "  Directory : ${APP_DIR}"
echo "=================================================================="

echo "[1/4] Memastikan paket Python3 & venv tersedia..."
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo apt-get install -y python3 python3-venv python3-pip
fi

echo "[2/4] Membuat Python Virtual Environment (.venv_rpi)..."
python3 -m venv "${APP_DIR}/.venv_rpi"
"${APP_DIR}/.venv_rpi/bin/pip" install --upgrade pip
"${APP_DIR}/.venv_rpi/bin/pip" install -r "${APP_DIR}/requirements.txt"

mkdir -p "${APP_DIR}/data/tiles" "${APP_DIR}/logs"

if [[ "${1}" == "--systemd" ]]; then
  echo "[3/4] Mendaftarkan systemd service (${SERVICE_NAME}.service)..."
  CURRENT_USER="$(whoami)"
  SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

  sudo tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=OWOO Eco-Acoustic LoRaWAN Monitoring Web Server
After=network.target mosquitto.service chirpstack.service

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${APP_DIR}
Environment="OWOO_HOST=0.0.0.0"
Environment="OWOO_PORT=${PORT}"
Environment="MQTT_HOST=${MQTT_HOST}"
Environment="MQTT_PORT=${MQTT_PORT}"
ExecStart=${APP_DIR}/.venv_rpi/bin/python ${APP_DIR}/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable "${SERVICE_NAME}.service"
  sudo systemctl restart "${SERVICE_NAME}.service"
  echo "[4/4] Service aktif! Cek status dengan: sudo systemctl status ${SERVICE_NAME}"
else
  echo "[3/4] Instalasi selesai!"
  echo "      Untuk menjalankan manual :"
  echo "        ${APP_DIR}/.venv_rpi/bin/python ${APP_DIR}/app.py"
  echo "      Untuk mengaktifkan autostart saat Raspberry Pi menyala :"
  echo "        ./setup_rpi.sh --systemd"
fi

IP_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "=================================================================="
echo "  Akses Web Monitoring OWOO di: http://${IP_ADDR:-127.0.0.1}:${PORT}"
echo "=================================================================="
