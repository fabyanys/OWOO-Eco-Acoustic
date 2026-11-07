# OWOO Eco-Acoustic — LoRaWAN Bio-Acoustic Monitoring Command Center

Sistem web monitoring lokal berbasis **Raspberry Pi** & **ChirpStack LoRaWAN** untuk memantau kesehatan ekosistem hutan (**ACI** & **NDSI**) serta mendeteksi ancaman pembalakan liar secara real-time di kawasan **Katingan Mentaya Project, Kalimantan Tengah**.

---

## Fitur Utama

1. **Autentikasi User & Role-Based Access Control (SQLite)**
   - Halaman **Login** & **Registrasi Akun Baru** (/login) dengan password terenkripsi (Werkzeug Scrypt / PBKDF2) di data/owoo.db.
   - **Pembagian Hak Akses Tiap Peran Akun**:
     - **Conservation Operator** & **IoT Network Engineer** (*Read & Write*): Dapat melihat seluruh data monitoring, mengunduh laporan CSV & PDF, menambah/menghapus End Node, serta mengubah konfigurasi server MQTT pada tab **Setting**.
     - **Forest Ranger** & **MRV Auditor** (*View & Download Report Only*): Hanya dapat melihat seluruh data monitoring dan mengunduh laporan CSV & PDF tanpa dapat menambah/menghapus End Node maupun mengubah konfigurasi server.

2. **4 Tab Navigasi Utama & Desain Responsif (Desktop, Tablet, & Smartphone)**
   - **Dashboard (Overview)**: Peta taktis real-time kawasan Katingan Mentaya dengan geofence oktagonal bercahaya, grafik **Acoustic Complexity Index (ACI)**, dan grafik **Normalized Difference Soundscape Index (NDSI)**.
   - **Map (Katingan Mentaya Project)**: Peta sebaran seluruh End Node, tombol **+ Tambah End Node** khusus di tab Map (untuk peran *Conservation Operator* & *IoT Network Engineer*), serta panel inspeksi detail saat End Node diklik (**ACI**, **NDSI**, dan **3 Kondisi Klasifikasi Audio**: `Chainsaw` [Threat], `Ambient Hutan` [Biodiversity], `Rain/Wind` [Noise]).
   - **Reports**: Filter multi-kriteria, **Ekspor CSV**, dan **Ekspor PDF** langsung sesuai konfigurasi filter (berisi grafik ACI & grafik NDSI secara terpisah, grafik distribusi klasifikasi per End Node, tabel ringkasan tiap End Node & lokasi penempatan, serta **kesimpulan evaluasi kategori ACI Rendah / Sedang / Tinggi** di bagian akhir laporan).
   - **Setting**: Konfigurasi koneksi server lokal menuju Broker MQTT ChirpStack beserta daftar topic aktif.

3. **Integrasi ChirpStack LoRaWAN MQTT (`paho-mqtt` + Real-Time SSE)**
   - Otomatis melakukan subscribe ke topic tiap End Node terdaftar:
     ```text
     application/<appid>/device/<devui>/event/up
     ```
   - Mendukung penuh format JSON standar **ChirpStack v4 / v3** (`object`, `objectJSON`, Base64 `data`, maupun JSON langsung).

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
3. Buka browser dari laptop atau HP di jaringan lokal Raspberry Pi:
   ```text
   http://<IP_RASPBERRY_PI>:5000
   ```

---

## Cara Menjalankan di Windows / Laptop

Klik dua kali `run_windows.bat` atau jalankan lewat terminal:
```powershell
py app.py
```
Lalu buka `http://127.0.0.1:5000` di browser.
