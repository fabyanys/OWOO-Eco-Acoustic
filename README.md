# OWOO Eco-Acoustic — LoRaWAN Bio-Acoustic Monitoring Command Center

Sistem web monitoring lokal berbasis **Raspberry Pi** & **ChirpStack LoRaWAN** untuk memantau kesehatan ekosistem hutan (**ACI** & **NDSI**) serta mendeteksi ancaman pembalakan liar secara real-time di kawasan **Katingan Mentaya Project, Kalimantan Tengah**.

---

## Fitur Utama

1. **Autentikasi User & Database Lokal (`SQLite`)**
   - Halaman **Login** & **Registrasi Akun Baru** (`/login`) sebelum mengakses dashboard monitoring.
   - Seluruh akun operator tersimpan aman dengan password ter-hash (`Werkzeug Scrypt / PBKDF2`) di `data/owoo.db`.
   - Akun awal default: `admin` / `owoo2026`.

2. **3 Tab Navigasi Utama (Tema Dark Emerald `#061712` + Neon Green `#00FF88` + Crimson `#FF2A4B`)**
   - **Dashboard (Overview)**: Peta taktis real-time kawasan Katingan Mentaya dengan geofence poligonal bercahaya, grafik **Acoustic Complexity Index (ACI)**, dan grafik **Normalized Difference Soundscape Index (NDSI)**.
   - **Map (Katingan Mentaya Project)**: Peta lengkap seluruh lokasi End Node, tombol **+ Tambah End Node** (input Nama, Lokasi Penempatan + pilih koordinat langsung di peta, `App ID`, `DevEUI`, serta `Node Key` unik per node), serta panel inspeksi detail saat End Node diklik (menampilkan **ACI**, **NDSI**, dan **3 Kondisi Klasifikasi Audio**: `Chainsaw` [Threat], `Ambient Hutan` [Biodiversity], dan `Rain/Wind` [Noise]).
   - **Reports & Local Server Logs**: Rekapitulasi seluruh End Node dan lokasinya, grafik tren log historis & distribusi klasifikasi, tampilan live log file server lokal (`logs/telemetry_uplinks.log`), serta fitur **Ekspor CSV**.

3. **Integrasi ChirpStack LoRaWAN MQTT (`paho-mqtt` + Real-Time SSE)**
   - Otomatis melakukan subscribe ke topic tiap End Node terdaftar:
     ```text
     application/<appid>/device/<devui>/event/up
     ```
   - Mendukung penuh format JSON standar **ChirpStack v4 / v3** (`object`, `objectJSON`, maupun Base64 `data`) dan payload JSON langsung.
   - Mendukung penyimpanan cache peta satelit lokal (`data/tiles/`) sehingga tetap berjalan mulus meskipun jaringan **100% Offline LoRaWAN** di lapangan.

---

## Format Payload MQTT ChirpStack

Topic tiap End Node:
```text
application/<appid>/device/<devui>/event/up
```

Contoh payload JSON ChirpStack v4 (hasil decode Payload Codec):
```json
{
  "deviceInfo": {
    "applicationId": "owoo-katingan",
    "devEui": "70b3d57ed006a101",
    "deviceName": "SylvaSense KM-01"
  },
  "object": {
    "aci": 0.78,
    "ndsi": 0.72,
    "classification": "Ambient Hutan",
    "confidence": 0.96,
    "node_key": "9f84c2e10b734d6a81f23c94e50a11b2"
  },
  "rxInfo": [
    { "rssi": -79, "snr": 9.2 }
  ]
}
```
Nilai `classification` otomatis dipetakan ke 3 kondisi:
- `"Chainsaw"` &rarr; **Threat**
- `"Ambient Hutan"` &rarr; **Biodiversity**
- `"Rain/Wind"` &rarr; **Noise**

---

## Cara Deploy ke Raspberry Pi

1. Salin seluruh folder ini ke Raspberry Pi (misal via `scp` atau USB/Git):
   ```bash
   scp -r Website pi@<IP_RASPBERRY_PI>:~/owoo-website
   ```
2. Masuk ke folder pada Raspberry Pi dan jalankan installer otomatis:
   ```bash
   cd ~/owoo-website
   chmod +x setup_rpi.sh
   ./setup_rpi.sh --systemd
   ```
3. Buka browser dari perangkat di jaringan lokal Raspberry Pi:
   ```text
   http://<IP_RASPBERRY_PI>:5000
   ```

---

## Cara Menjalankan & Menguji di Windows / Laptop

1. Klik dua kali `run_windows.bat` atau jalankan lewat terminal:
   ```powershell
   py app.py
   ```
2. Buka `http://127.0.0.1:5000` di browser.
3. Untuk menguji pengiriman paket uplink End Node secara otomatis dari terminal kedua:
   ```powershell
   py simulate_mqtt.py --once
   py simulate_mqtt.py --condition threat --once
   py simulate_mqtt.py --loop --interval 5
   ```
