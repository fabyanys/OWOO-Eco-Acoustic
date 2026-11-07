"""
OWOO Eco-Acoustic — PDF MRV & Bio-Acoustic Report Generator
-----------------------------------------------------------
Menghasilkan dokumen laporan PDF vektor beresolusi tinggi sesuai konfigurasi filter aktif:
  1. Grafik Tren Acoustic Complexity Index (ACI) [Terpisah]
  2. Grafik Tren Normalized Difference Soundscape Index (NDSI) [Terpisah]
  3. Grafik Distribusi Klasifikasi Audio per End Node (Biodiversity, Threat, Noise)
  4. Tabel Ringkasan Report Tiap End Node & Lokasi Penempatan
  5. Kesimpulan Akhir Evaluasi Kategori ACI (Rendah, Sedang, Tinggi)
"""

import io
import os
from datetime import datetime

from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, "static", "img", "owoo-logo.png")

# Palette
COL_DEEP_EMERALD = colors.HexColor("#061712")
COL_FOREST_HEADER = colors.HexColor("#0A281E")
COL_EMERALD_ACCENT = colors.HexColor("#059669")
COL_NEON_MINT = colors.HexColor("#10B981")
COL_CRIMSON = colors.HexColor("#E11D48")
COL_CYAN = colors.HexColor("#0284C7")
COL_AMBER = colors.HexColor("#D97706")
COL_SLATE_DARK = colors.HexColor("#0F172A")
COL_SLATE_MUTED = colors.HexColor("#475569")
COL_BORDER = colors.HexColor("#CBD5E1")
COL_BG_LIGHT = colors.HexColor("#F8FAFC")
COL_BG_MINT = colors.HexColor("#ECFDF5")


def classify_aci_value(aci_value: float | None, is_raw_scale: bool = False) -> dict:
    """
    Mengklasifikasikan nilai rata-rata ACI ke dalam 3 kategori:
      - Skala ternormalisasi Edge-AI (0.00 - 1.00):
          * Rendah : < 0.50
          * Sedang : 0.50 s/d < 0.70
          * Tinggi : >= 0.70
      - Skala mentah spektral (> 1.5, mis. 100 - 250):
          * Rendah : < 145.0
          * Sedang : 145.0 s/d < 175.0
          * Tinggi : >= 175.0
    """
    if aci_value is None:
        return {
            "category": "Belum Ada Data",
            "level_code": "NONE",
            "color": COL_SLATE_MUTED,
            "bg_color": colors.HexColor("#F1F5F9"),
            "threshold_desc": "-",
        }

    low_thr = 145.0 if is_raw_scale else 0.50
    high_thr = 175.0 if is_raw_scale else 0.70

    if aci_value < low_thr:
        return {
            "category": "RENDAH",
            "level_code": "LOW",
            "color": COL_CRIMSON,
            "bg_color": colors.HexColor("#FFF1F2"),
            "threshold_desc": f"< {low_thr:.2f}",
        }
    if aci_value < high_thr:
        return {
            "category": "SEDANG",
            "level_code": "MEDIUM",
            "color": COL_AMBER,
            "bg_color": colors.HexColor("#FFFBEB"),
            "threshold_desc": f"{low_thr:.2f} - {high_thr - (0.01 if not is_raw_scale else 0.1):.2f}",
        }
    return {
        "category": "TINGGI",
        "level_code": "HIGH",
        "color": COL_EMERALD_ACCENT,
        "bg_color": colors.HexColor("#ECFDF5"),
        "threshold_desc": f">= {high_thr:.2f}",
    }


def _short_ts(ts: str) -> str:
    if not ts:
        return ""
    s = str(ts).strip()
    if len(s) >= 16:
        # "YYYY-MM-DD HH:MM:SS" -> "MM-DD HH:MM"
        return s[5:16]
    return s


def _build_aci_chart_drawing(chronological_logs: list[dict], is_raw_scale: bool, width: float = 495, height: float = 172) -> Drawing:
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=COL_BG_LIGHT, strokeColor=COL_BORDER, strokeWidth=0.8, rx=6, ry=6))

    pad_l, pad_r, pad_t, pad_b = 44, 18, 34, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    x0, y0 = pad_l, pad_b

    if not chronological_logs:
        d.add(
            String(
                width / 2,
                height / 2,
                "Tidak ada data log ACI untuk konfigurasi filter yang dipilih.",
                fontName="Helvetica-Oblique",
                fontSize=9.5,
                fillColor=COL_SLATE_MUTED,
                textAnchor="middle",
            )
        )
        return d

    vals = [float(r["aci"]) for r in chronological_logs]
    if is_raw_scale:
        y_min = min(100.0, min(vals) * 0.92)
        y_max = max(220.0, max(vals) * 1.06)
        thr_low, thr_high = 145.0, 175.0
    else:
        y_min = 0.0
        y_max = 1.0 if max(vals) <= 1.0 else max(vals) * 1.05
        thr_low, thr_high = 0.50, 0.70

    def map_y(v: float) -> float:
        clamped = max(y_min, min(y_max, v))
        return y0 + ((clamped - y_min) / (y_max - y_min or 1.0)) * plot_h

    # Background category bands (Rendah / Sedang / Tinggi)
    y_low_line = map_y(thr_low)
    y_high_line = map_y(thr_high)

    d.add(Rect(x0, y0, plot_w, max(0, y_low_line - y0), fillColor=colors.HexColor("#FFE4E6"), strokeColor=None))
    d.add(Rect(x0, y_low_line, plot_w, max(0, y_high_line - y_low_line), fillColor=colors.HexColor("#FEF3C7"), strokeColor=None))
    d.add(Rect(x0, y_high_line, plot_w, max(0, (y0 + plot_h) - y_high_line), fillColor=colors.HexColor("#D1FAE5"), strokeColor=None))

    # Grid lines & Y labels
    steps = 5
    for i in range(steps + 1):
        val_tick = y_min + (y_max - y_min) * (i / steps)
        yy = map_y(val_tick)
        d.add(Line(x0, yy, x0 + plot_w, yy, strokeColor=colors.HexColor("#CBD5E1"), strokeWidth=0.5))
        d.add(
            String(
                x0 - 6,
                yy - 3,
                f"{val_tick:.2f}" if not is_raw_scale else f"{val_tick:.0f}",
                fontName="Helvetica",
                fontSize=7.5,
                fillColor=COL_SLATE_MUTED,
                textAnchor="end",
            )
        )

    # Threshold dashed markers (legend placed cleanly in top-right header margin)
    d.add(Line(x0, y_high_line, x0 + plot_w, y_high_line, strokeColor=COL_EMERALD_ACCENT, strokeWidth=0.85, strokeDashArray=[3, 3]))
    d.add(Line(x0, y_low_line, x0 + plot_w, y_low_line, strokeColor=COL_CRIMSON, strokeWidth=0.85, strokeDashArray=[3, 3]))
    d.add(
        String(
            x0,
            height - 26,
            f"Kriteria Ambang Kategori ACI:   Tinggi >= {thr_high:.2f}   |   Sedang {thr_low:.2f} - {thr_high - 0.01:.2f}   |   Rendah < {thr_low:.2f}",
            fontName="Helvetica",
            fontSize=7.2,
            fillColor=COL_SLATE_MUTED,
        )
    )

    # Plot coordinates
    n = len(vals)
    pts = []
    for idx, val in enumerate(vals):
        xx = x0 + (plot_w * idx / (n - 1)) if n > 1 else (x0 + plot_w / 2)
        yy = map_y(val)
        pts.append((xx, yy))

    # Area polygon fill + PolyLine
    if len(pts) >= 2:
        poly_coords = [pts[0][0], y0]
        for px, py in pts:
            poly_coords.extend([px, py])
        poly_coords.extend([pts[-1][0], y0])
        d.add(Polygon(poly_coords, fillColor=colors.Color(0.02, 0.59, 0.41, alpha=0.18), strokeColor=None))

        flat_line = []
        for px, py in pts:
            flat_line.extend([px, py])
        d.add(PolyLine(flat_line, strokeColor=COL_EMERALD_ACCENT, strokeWidth=1.9))

    # Points & X-axis labels
    tick_indices = sorted(list({0, n // 4, n // 2, (3 * n) // 4, n - 1})) if n > 1 else [0]
    for idx, (px, py) in enumerate(pts):
        pt_col = COL_CRIMSON if vals[idx] < thr_low else COL_EMERALD_ACCENT
        if n <= 45 or idx in tick_indices:
            d.add(Circle(px, py, 2.3, fillColor=pt_col, strokeColor=colors.white, strokeWidth=0.6))
        if idx in tick_indices:
            d.add(
                String(
                    px,
                    y0 - 13,
                    _short_ts(chronological_logs[idx]["recorded_at"]),
                    fontName="Helvetica",
                    fontSize=7,
                    fillColor=COL_SLATE_MUTED,
                    textAnchor="middle",
                )
            )

    # Axes border & top stats summary
    d.add(Line(x0, y0, x0, y0 + plot_h, strokeColor=COL_SLATE_MUTED, strokeWidth=0.9))
    d.add(Line(x0, y0, x0 + plot_w, y0, strokeColor=COL_SLATE_MUTED, strokeWidth=0.9))

    avg_v = sum(vals) / len(vals)
    d.add(
        String(
            x0,
            height - 15,
            f"Sampel: {len(vals)} uplink   |   Rata-rata ACI: {avg_v:.3f}   |   Min: {min(vals):.3f}   |   Max: {max(vals):.3f}",
            fontName="Helvetica-Bold",
            fontSize=8,
            fillColor=COL_SLATE_DARK,
        )
    )
    return d


def _build_ndsi_chart_drawing(chronological_logs: list[dict], width: float = 495, height: float = 172) -> Drawing:
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=COL_BG_LIGHT, strokeColor=COL_BORDER, strokeWidth=0.8, rx=6, ry=6))

    pad_l, pad_r, pad_t, pad_b = 44, 18, 34, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    x0, y0 = pad_l, pad_b

    if not chronological_logs:
        d.add(
            String(
                width / 2,
                height / 2,
                "Tidak ada data log NDSI untuk konfigurasi filter yang dipilih.",
                fontName="Helvetica-Oblique",
                fontSize=9.5,
                fillColor=COL_SLATE_MUTED,
                textAnchor="middle",
            )
        )
        return d

    vals = [max(-1.0, min(1.0, float(r["ndsi"]))) for r in chronological_logs]
    y_min, y_max = -1.0, 1.0

    def map_y(v: float) -> float:
        return y0 + ((v - y_min) / (y_max - y_min)) * plot_h

    y_zero = map_y(0.0)
    # Biophony (+) top half vs Anthrophony (-) bottom half
    d.add(Rect(x0, y_zero, plot_w, (y0 + plot_h) - y_zero, fillColor=colors.HexColor("#E0F2FE"), strokeColor=None))
    d.add(Rect(x0, y0, plot_w, y_zero - y0, fillColor=colors.HexColor("#FFE4E6"), strokeColor=None))

    for tick_v in (-1.0, -0.5, 0.0, 0.5, 1.0):
        yy = map_y(tick_v)
        is_zero = abs(tick_v) < 1e-6
        d.add(
            Line(
                x0,
                yy,
                x0 + plot_w,
                yy,
                strokeColor=COL_SLATE_MUTED if is_zero else colors.HexColor("#CBD5E1"),
                strokeWidth=1.0 if is_zero else 0.5,
                strokeDashArray=[4, 2] if is_zero else None,
            )
        )
        d.add(
            String(
                x0 - 6,
                yy - 3,
                f"{tick_v:+.1f}" if not is_zero else "0.0",
                fontName="Helvetica-Bold" if is_zero else "Helvetica",
                fontSize=7.5,
                fillColor=COL_SLATE_MUTED,
                textAnchor="end",
            )
        )

    d.add(
        String(
            x0,
            height - 26,
            "Referensi Rasio NDSI:   Nilai > 0.0 = Dominan Biophony (Satwa Hutan)   |   Nilai < 0.0 = Dominan Anthrophony (Gangguan Mesin)",
            fontName="Helvetica",
            fontSize=7.2,
            fillColor=COL_SLATE_MUTED,
        )
    )

    n = len(vals)
    pts = []
    for idx, val in enumerate(vals):
        xx = x0 + (plot_w * idx / (n - 1)) if n > 1 else (x0 + plot_w / 2)
        yy = map_y(val)
        pts.append((xx, yy))

    if len(pts) >= 2:
        poly_coords = [pts[0][0], y_zero]
        for px, py in pts:
            poly_coords.extend([px, py])
        poly_coords.extend([pts[-1][0], y_zero])
        d.add(Polygon(poly_coords, fillColor=colors.Color(0.01, 0.52, 0.78, alpha=0.18), strokeColor=None))

        flat_line = []
        for px, py in pts:
            flat_line.extend([px, py])
        d.add(PolyLine(flat_line, strokeColor=COL_CYAN, strokeWidth=1.9))

    tick_indices = sorted(list({0, n // 4, n // 2, (3 * n) // 4, n - 1})) if n > 1 else [0]
    for idx, (px, py) in enumerate(pts):
        pt_col = COL_CRIMSON if vals[idx] < 0 else COL_CYAN
        if n <= 45 or idx in tick_indices or vals[idx] < 0:
            d.add(Circle(px, py, 2.3, fillColor=pt_col, strokeColor=colors.white, strokeWidth=0.6))
        if idx in tick_indices:
            d.add(
                String(
                    px,
                    y0 - 13,
                    _short_ts(chronological_logs[idx]["recorded_at"]),
                    fontName="Helvetica",
                    fontSize=7,
                    fillColor=COL_SLATE_MUTED,
                    textAnchor="middle",
                )
            )

    d.add(Line(x0, y0, x0, y0 + plot_h, strokeColor=COL_SLATE_MUTED, strokeWidth=0.9))
    d.add(Line(x0, y0, x0 + plot_w, y0, strokeColor=COL_SLATE_MUTED, strokeWidth=0.9))

    avg_v = sum(vals) / len(vals)
    d.add(
        String(
            x0,
            height - 15,
            f"Sampel: {len(vals)} uplink   |   Rata-rata NDSI: {avg_v:+.3f}   |   Min: {min(vals):+.3f}   |   Max: {max(vals):+.3f}",
            fontName="Helvetica-Bold",
            fontSize=8,
            fillColor=COL_SLATE_DARK,
        )
    )
    return d


def _build_distribution_bar_drawing(summaries: list[dict], width: float = 495, height: float = 182) -> Drawing:
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=COL_BG_LIGHT, strokeColor=COL_BORDER, strokeWidth=0.8, rx=6, ry=6))

    pad_l, pad_r, pad_t, pad_b = 42, 18, 30, 34
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    x0, y0 = pad_l, pad_b

    if not summaries:
        d.add(
            String(
                width / 2,
                height / 2,
                "Tidak ada data End Node untuk ditampilkan.",
                fontName="Helvetica-Oblique",
                fontSize=9.5,
                fillColor=COL_SLATE_MUTED,
                textAnchor="middle",
            )
        )
        return d

    # Legend at top
    legend_items = [
        ("Ambient Hutan (Biodiversity)", COL_NEON_MINT),
        ("Chainsaw (Threat)", COL_CRIMSON),
        ("Rain/Wind (Noise)", COL_CYAN),
    ]
    lx = x0
    for label, col in legend_items:
        d.add(Rect(lx, height - 18, 9, 9, fillColor=col, strokeColor=None, rx=2, ry=2))
        d.add(String(lx + 13, height - 16.5, label, fontName="Helvetica-Bold", fontSize=7.5, fillColor=COL_SLATE_DARK))
        lx += 155

    max_count = 1
    for s in summaries:
        max_count = max(
            max_count,
            int(s.get("biodiversity_count") or 0),
            int(s.get("threat_count") or 0),
            int(s.get("noise_count") or 0),
        )
    y_top = max(4, int(max_count * 1.18) + 1)

    for i in range(5):
        val_tick = round(y_top * (i / 4))
        yy = y0 + (val_tick / y_top) * plot_h
        d.add(Line(x0, yy, x0 + plot_w, yy, strokeColor=colors.HexColor("#CBD5E1"), strokeWidth=0.5))
        d.add(String(x0 - 6, yy - 3, str(val_tick), fontName="Helvetica", fontSize=7.5, fillColor=COL_SLATE_MUTED, textAnchor="end"))

    group_count = len(summaries)
    slot_w = plot_w / group_count
    bar_w = min(18.0, max(6.0, (slot_w * 0.68) / 3.0))

    for idx, s in enumerate(summaries):
        cx = x0 + slot_w * (idx + 0.5)
        counts = [
            (int(s.get("biodiversity_count") or 0), COL_NEON_MINT),
            (int(s.get("threat_count") or 0), COL_CRIMSON),
            (int(s.get("noise_count") or 0), COL_CYAN),
        ]
        start_x = cx - (1.5 * bar_w) - 2
        for b_idx, (cnt, col) in enumerate(counts):
            bx = start_x + b_idx * (bar_w + 2)
            bh = (cnt / y_top) * plot_h
            if bh > 0:
                d.add(Rect(bx, y0, bar_w, max(1.5, bh), fillColor=col, strokeColor=None))
                d.add(
                    String(
                        bx + bar_w / 2,
                        y0 + bh + 3,
                        str(cnt),
                        fontName="Helvetica-Bold",
                        fontSize=6.8,
                        fillColor=COL_SLATE_DARK,
                        textAnchor="middle",
                    )
                )

        short_name = str(s.get("node_name") or f"Node-{idx+1}").replace("SylvaSense ", "")
        if len(short_name) > 16:
            short_name = short_name[:15] + "…"
        d.add(
            String(
                cx,
                y0 - 14,
                short_name,
                fontName="Helvetica-Bold",
                fontSize=7.3,
                fillColor=COL_SLATE_DARK,
                textAnchor="middle",
            )
        )

    d.add(Line(x0, y0, x0, y0 + plot_h, strokeColor=COL_SLATE_MUTED, strokeWidth=0.9))
    d.add(Line(x0, y0, x0 + plot_w, y0, strokeColor=COL_SLATE_MUTED, strokeWidth=0.9))
    return d


def generate_pdf_report_bytes(
    logs: list[dict],
    summaries: list[dict],
    filters_meta: dict,
    user: dict,
) -> bytes:
    """
    Membangun file laporan PDF lengkap sesuai filter yang dipilih pada tab Reports.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=15 * mm,
        title="Laporan Monitoring Bio-Akustik OWOO — Katingan Mentaya Project",
        author="OWOO Eco-Acoustic Command Center",
    )

    styles = getSampleStyleSheet()
    st_section = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11.5,
        leading=15,
        textColor=COL_FOREST_HEADER,
        spaceBefore=10,
        spaceAfter=5,
    )
    st_body = ParagraphStyle(
        "BodySmall",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.8,
        leading=13,
        textColor=COL_SLATE_DARK,
        alignment=TA_JUSTIFY,
    )
    st_cell = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.6,
        leading=10.2,
        textColor=COL_SLATE_DARK,
    )
    st_cell_bold = ParagraphStyle(
        "TableCellBold",
        parent=st_cell,
        fontName="Helvetica-Bold",
    )
    st_th = ParagraphStyle(
        "TableHeader",
        parent=st_cell,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )

    story = []

    # ---------------- 1. HEADER BANNER ---------------- #
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB")
    logo_flowable = Spacer(1, 1)
    if os.path.exists(LOGO_PATH):
        logo_flowable = RLImage(LOGO_PATH, width=34, height=34)

    header_title_html = (
        "<font name='Helvetica-Bold' size='12' color='#00FF88'>OWOO ECO-ACOUSTIC — LAPORAN MONITORING</font><br/>"
        "<font name='Helvetica' size='8.3' color='#D1FAE5'>"
        "Audit Bio-Akustik &amp; Deteksi Ancaman Hutan LoRaWAN — Katingan Mentaya Project"
        "</font>"
    )
    st_hdr_left = ParagraphStyle("HdrLeft", parent=styles["Normal"], leading=14.5)
    header_meta_html = (
        f"<font name='Helvetica-Bold' size='7.8' color='#FFFFFF'>Dicetak:</font> "
        f"<font name='Helvetica' size='7.8' color='#A7F3D0'>{now_str}</font><br/>"
        f"<font name='Helvetica-Bold' size='7.8' color='#FFFFFF'>Operator:</font> "
        f"<font name='Helvetica' size='7.8' color='#A7F3D0'>{user.get('full_name', '-')} ({user.get('role', '-')})</font>"
    )

    header_table = Table(
        [[logo_flowable, Paragraph(header_title_html, st_hdr_left), Paragraph(header_meta_html, ParagraphStyle("HdrR", alignment=TA_RIGHT, leading=11))]],
        colWidths=[44, 296, 155],
    )
    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), COL_DEEP_EMERALD),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("ROUNDEDCORNERS", [6, 6, 6, 6]),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 7))

    # ---------------- 2. ACTIVE FILTER & KPI SUMMARY STRIP ---------------- #
    chronological = list(reversed(logs))
    aci_values = [float(r["aci"]) for r in logs]
    ndsi_values = [float(r["ndsi"]) for r in logs]
    is_raw_scale = any(v > 1.5 for v in aci_values) if aci_values else False

    avg_aci = (sum(aci_values) / len(aci_values)) if aci_values else None
    min_aci = min(aci_values) if aci_values else None
    max_aci = max(aci_values) if aci_values else None
    avg_ndsi = (sum(ndsi_values) / len(ndsi_values)) if ndsi_values else None

    bio_total = sum(1 for r in logs if str(r.get("condition", "")).lower() == "biodiversity")
    threat_total = sum(1 for r in logs if str(r.get("condition", "")).lower() == "threat")
    noise_total = sum(1 for r in logs if str(r.get("condition", "")).lower() == "noise")

    aci_eval = classify_aci_value(avg_aci, is_raw_scale=is_raw_scale)

    filter_info_html = (
        f"<b>Filter End Node:</b> {filters_meta.get('node_label', 'Semua End Node')} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Kondisi:</b> {filters_meta.get('condition_label', 'Semua Kondisi')} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"<b>Rentang Tanggal:</b> {filters_meta.get('date_label', 'Seluruh Waktu')}"
        + (f" &nbsp;&nbsp;|&nbsp;&nbsp; <b>Kata Kunci:</b> {filters_meta['search']}" if filters_meta.get("search") else "")
    )
    filter_box = Table([[Paragraph(filter_info_html, st_cell)]], colWidths=[495])
    filter_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), COL_BG_MINT),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#A7F3D0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(filter_box)
    story.append(Spacer(1, 7))

    kpi_cells = [
        Paragraph(f"<font size='7' color='#475569'>TOTAL TRANSMISI UPLINK</font><br/><font name='Helvetica-Bold' size='12' color='#0F172A'>{len(logs)} Paket</font>", styles["Normal"]),
        Paragraph(
            f"<font size='7' color='#475569'>RATA-RATA INDEKS ACI</font><br/>"
            f"<font name='Helvetica-Bold' size='12' color='#059669'>{f'{avg_aci:.3f}' if avg_aci is not None else '--'}</font> "
            f"<font name='Helvetica-Bold' size='8' color='{aci_eval['color'].hexval()}'>({aci_eval['category']})</font>",
            styles["Normal"],
        ),
        Paragraph(
            f"<font size='7' color='#475569'>RATA-RATA INDEKS NDSI</font><br/>"
            f"<font name='Helvetica-Bold' size='12' color='#0284C7'>{f'{avg_ndsi:+.3f}' if avg_ndsi is not None else '--'}</font>",
            styles["Normal"],
        ),
        Paragraph(
            f"<font size='7' color='#475569'>DISTRIBUSI KLASIFIKASI</font><br/>"
            f"<font name='Helvetica-Bold' size='8.5' color='#059669'>Bio: {bio_total}</font> &bull; "
            f"<font name='Helvetica-Bold' size='8.5' color='#E11D48'>Threat: {threat_total}</font> &bull; "
            f"<font name='Helvetica-Bold' size='8.5' color='#0284C7'>Noise: {noise_total}</font>",
            styles["Normal"],
        ),
    ]
    kpi_table = Table([kpi_cells], colWidths=[108, 132, 108, 147])
    kpi_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.6, COL_BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(kpi_table)

    # ---------------- 3. SEPARATE ACI & NDSI CHARTS ---------------- #
    story.append(Paragraph("1. Grafik Tren Acoustic Complexity Index (ACI)", st_section))
    story.append(_build_aci_chart_drawing(chronological, is_raw_scale=is_raw_scale))

    story.append(Paragraph("2. Grafik Tren Normalized Difference Soundscape Index (NDSI)", st_section))
    story.append(_build_ndsi_chart_drawing(chronological))

    # ---------------- 4. CLASSIFICATION DISTRIBUTION PER END NODE ---------------- #
    story.append(
        KeepTogether(
            [
                Paragraph("3. Grafik Distribusi Klasifikasi Audio per End Node", st_section),
                _build_distribution_bar_drawing(summaries),
            ]
        )
    )

    # ---------------- 5. NODE SUMMARY TABLE ---------------- #
    story.append(Paragraph("4. Tabel Ringkasan Report Tiap End Node &amp; Lokasi Penempatan", st_section))

    table_rows = [
        [
            Paragraph("End Node", st_th),
            Paragraph("Lokasi Penempatan &amp; Koordinat", st_th),
            Paragraph("Topic MQTT ChirpStack", st_th),
            Paragraph("Uplink", st_th),
            Paragraph("Avg ACI", st_th),
            Paragraph("Avg NDSI", st_th),
            Paragraph("Bio / Thr / Noi", st_th),
            Paragraph("Kategori ACI", st_th),
        ]
    ]

    for s in summaries:
        node_avg_aci = s.get("avg_aci")
        node_eval = classify_aci_value(node_avg_aci, is_raw_scale=is_raw_scale)
        node_avg_ndsi = s.get("avg_ndsi")
        loc_html = (
            f"<b>{s.get('location_name', '-')}</b><br/>"
            f"<font size='6.8' color='#64748B'>({float(s.get('latitude', 0)):.4f}, {float(s.get('longitude', 0)):.4f})</font>"
        )
        cat_color_hex = node_eval["color"].hexval()
        table_rows.append(
            [
                Paragraph(str(s.get("node_name", "-")), st_cell_bold),
                Paragraph(loc_html, st_cell),
                Paragraph(f"<font name='Courier' size='6.5'>{s.get('mqtt_topic', '-')}</font>", st_cell),
                Paragraph(str(s.get("total_uplinks") or 0), st_cell),
                Paragraph(f"{node_avg_aci:.3f}" if node_avg_aci is not None else "--", st_cell_bold),
                Paragraph(f"{node_avg_ndsi:+.3f}" if node_avg_ndsi is not None else "--", st_cell),
                Paragraph(
                    f"<font color='#059669'>{s.get('biodiversity_count') or 0}</font> / "
                    f"<font color='#E11D48'><b>{s.get('threat_count') or 0}</b></font> / "
                    f"<font color='#0284C7'>{s.get('noise_count') or 0}</font>",
                    st_cell,
                ),
                Paragraph(f"<font name='Helvetica-Bold' color='{cat_color_hex}'>{node_eval['category']}</font>", st_cell),
            ]
        )

    if len(table_rows) == 1:
        table_rows.append([Paragraph("Tidak ada data End Node sesuai filter.", st_cell)] + [""] * 7)

    sum_table = Table(table_rows, colWidths=[66, 108, 102, 34, 42, 44, 52, 47], repeatRows=1)
    sum_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), COL_FOREST_HEADER),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COL_BG_LIGHT]),
                ("GRID", (0, 0), (-1, -1), 0.5, COL_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(sum_table)
    story.append(Spacer(1, 8))

    # ---------------- 6. FINAL CONCLUSION: ACI CATEGORY (RENDAH / SEDANG / TINGGI) ---------------- #
    low_thr = 145.0 if is_raw_scale else 0.50
    high_thr = 175.0 if is_raw_scale else 0.70

    low_nodes, med_nodes, high_nodes = [], [], []
    for s in summaries:
        if s.get("avg_aci") is None or (s.get("total_uplinks") or 0) == 0:
            continue
        ev = classify_aci_value(float(s["avg_aci"]), is_raw_scale=is_raw_scale)
        label_item = f"{s['node_name']} ({s['avg_aci']:.3f})"
        if ev["level_code"] == "LOW":
            low_nodes.append(label_item)
        elif ev["level_code"] == "MEDIUM":
            med_nodes.append(label_item)
        elif ev["level_code"] == "HIGH":
            high_nodes.append(label_item)

    if avg_aci is None:
        conclusion_headline = "KESIMPULAN KATEGORI ACI: BELUM TERDAPAT SAMPEL DATA PADA FILTER INI"
        conclusion_paragraphs = [
            "Berdasarkan konfigurasi filter yang dipilih, belum ditemukan rekaman transmisi uplink dari End Node. "
            "Silakan perluas rentang tanggal atau pilih filter 'Semua Kondisi' untuk memperoleh evaluasi kategori ACI."
        ]
    else:
        cat_label = aci_eval["category"]
        conclusion_headline = (
            f"KESIMPULAN AKHIR: DATA TERMASUK KE DALAM ACI KATEGORI {cat_label} "
            f"(Rata-rata ACI = {avg_aci:.3f})"
        )

        if aci_eval["level_code"] == "HIGH":
            interp_text = (
                f"Berdasarkan evaluasi terhadap <b>{len(logs)} rekaman data</b> pada kawasan Katingan Mentaya Project "
                f"(rentang nilai ACI <b>{min_aci:.3f}</b> hingga <b>{max_aci:.3f}</b> dengan rata-rata <b>{avg_aci:.3f}</b>), "
                f"data pemantauan secara keseluruhan diklasifikasikan ke dalam <b>ACI Kategori TINGGI (&ge; {high_thr:.2f})</b>. "
                "Kategori Tinggi menunjukkan tingkat kompleksitas variasi frekuensi bio-akustik yang kaya dan menandakan aktivitas "
                "keanekaragaman hayati (<i>Ambient Hutan / Biodiversity</i> seperti burung dan serangga kanopi) yang aktif serta sehat."
            )
        elif aci_eval["level_code"] == "MEDIUM":
            interp_text = (
                f"Berdasarkan evaluasi terhadap <b>{len(logs)} rekaman data</b> pada kawasan Katingan Mentaya Project "
                f"(rentang nilai ACI <b>{min_aci:.3f}</b> hingga <b>{max_aci:.3f}</b> dengan rata-rata <b>{avg_aci:.3f}</b>), "
                f"data pemantauan secara keseluruhan diklasifikasikan ke dalam <b>ACI Kategori SEDANG ({low_thr:.2f} &ndash; {high_thr - 0.01:.2f})</b>. "
                "Kategori Sedang mengindikasikan aktivitas vokalisasi satwa hutan berada pada tingkat moderat atau sebagian dipengaruhi oleh "
                "suara latar belakang cuaca alam seperti hujan dan angin (<i>Rain/Wind - Noise</i>)."
            )
        else:
            interp_text = (
                f"Berdasarkan evaluasi terhadap <b>{len(logs)} rekaman data</b> pada kawasan Katingan Mentaya Project "
                f"(rentang nilai ACI <b>{min_aci:.3f}</b> hingga <b>{max_aci:.3f}</b> dengan rata-rata <b>{avg_aci:.3f}</b>), "
                f"data pemantauan secara keseluruhan diklasifikasikan ke dalam <b>ACI Kategori RENDAH (&lt; {low_thr:.2f})</b>. "
                "Kategori Rendah menandakan rendahnya variasi spektral bio-akustik satwa yang umumnya berasosiasi dengan gangguan kebisingan "
                "mekanis berfrekuensi monoton (<i>Chainsaw / Threat</i>) atau tekanan pada kualitas habitat hutan."
            )

        ndsi_and_threat_text = (
            f"Dari sisi keseimbangan lanskap suara, rata-rata <b>NDSI tercatat sebesar {avg_ndsi:+.3f}</b> "
            f"dengan rincian deteksi klasifikasi audio meliputi <b>{bio_total} kejadian Ambient Hutan (Biodiversity)</b>, "
            f"<b>{noise_total} kejadian Rain/Wind (Noise)</b>, dan <b>{threat_total} kejadian Chainsaw (Threat)</b>. "
        )
        if threat_total > 0:
            ndsi_and_threat_text += (
                f"<b>Peringatan Tindak Lanjut:</b> Ditemukan <b>{threat_total} indikasi ancaman gergaji mesin (Chainsaw)</b> "
                "pada periode laporan ini sehingga disarankan verifikasi patroli lapangan pada titik lokasi End Node terkait."
            )
        else:
            ndsi_and_threat_text += (
                "Tidak ditemukan anomali ancaman pembalakan liar yang signifikan pada data terfilter, sehingga kondisi kawasan terpantau kondusif."
            )

        breakdown_text = (
            f"<b>Pemetaan Kategori ACI per End Node:</b><br/>"
            f"&bull; <b>Kategori Tinggi (&ge; {high_thr:.2f}):</b> {', '.join(high_nodes) if high_nodes else 'Tidak ada'}<br/>"
            f"&bull; <b>Kategori Sedang ({low_thr:.2f} &ndash; {high_thr - 0.01:.2f}):</b> {', '.join(med_nodes) if med_nodes else 'Tidak ada'}<br/>"
            f"&bull; <b>Kategori Rendah (&lt; {low_thr:.2f}):</b> {', '.join(low_nodes) if low_nodes else 'Tidak ada'}"
        )
        conclusion_paragraphs = [interp_text, ndsi_and_threat_text, breakdown_text]

    conc_elements = [Paragraph("5. Kesimpulan Akhir &amp; Klasifikasi Kategori ACI", st_section)]
    conc_inner_rows = [
        [
            Paragraph(
                f"<font name='Helvetica-Bold' size='10' color='{aci_eval['color'].hexval()}'>{conclusion_headline}</font>",
                styles["Normal"],
            )
        ]
    ]
    for p_html in conclusion_paragraphs:
        conc_inner_rows.append([Paragraph(p_html, st_body)])

    conc_box = Table(conc_inner_rows, colWidths=[495])
    conc_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), aci_eval["bg_color"]),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 1.0, aci_eval["color"]),
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, aci_eval["color"]),
                ("LEFTPADDING", (0, 0), (-1, -1), 11),
                ("RIGHTPADDING", (0, 0), (-1, -1), 11),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    conc_elements.append(conc_box)
    story.append(KeepTogether(conc_elements))

    doc.build(story)
    return buf.getvalue()
