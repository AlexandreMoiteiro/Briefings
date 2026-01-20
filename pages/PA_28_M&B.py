# Streamlit app — Piper PA-28 Archer III (Sevenair) — M&B + Forecast + Fuel + PDF — v1.0
#
# Features
# - 4 legs: Departure / Arrival / Alternate 1 / Alternate 2 (approved airfields only)
# - Separate UTC times for departure & arrival; alternates use ARR + 1h
# - Open-Meteo forecast per leg (hourly); manual override per leg
# - Wind formatted and rounded to nearest 10° (e.g. 240/08)
# - Mass & Balance in lbs + (kg); Fuel in USG + (L)
# - Fills the provided PDF form (pypdf) and draws CG chart overlay (3 cases):
#     Empty (green), Takeoff (blue), Landing (red) with legend
#
# Requirements
#   streamlit
#   requests
#   pypdf>=4.2.0
#   reportlab
#   pytz

import datetime as dt
import io
import json
from math import cos, sin, radians, sqrt, atan2, degrees
from pathlib import Path

import pytz
import requests
import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import portrait
from reportlab.lib.colors import Color


# -----------------------------
# App config & style
# -----------------------------
st.set_page_config(
    page_title="Piper PA-28 — M&B + Forecast + PDF",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1200px !important; }
      .hdr{font-size:1.35rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:8px;margin:2px 0 14px}
      .chip{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef2f7;margin-left:8px;font-size:.85rem}
      .ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
      .mb-table{border-collapse:collapse;width:100%;font-size:.95rem}
      .mb-table th{border-bottom:2px solid #cbd0d6;text-align:left}
      .mb-table td{padding:3px 6px;border-bottom:1px dashed #e5e7ec}
      .hint{font-size:.85rem;color:#6b7280}
      .box{border:1px solid #273244;border-radius:12px;padding:12px;background:rgba(255,255,255,0.02)}
      .row{display:flex;gap:10px;flex-wrap:wrap}
      .kpi{border:1px solid #273244;border-radius:12px;padding:10px 12px;background:rgba(255,255,255,0.02)}
      .kpi b{font-size:1.05rem}
      .sub{opacity:.85}
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# Constants
# -----------------------------
PDF_TEMPLATE_PATHS = [
    "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf",  # repo root
]

# PA-28 Archer III (typical for this sheet)
AC = {
    "name": "Piper PA-28 Archer III",
    "max_fuel_usg": 48.0,      # per sheet
    "fuel_lb_per_usg": 6.0,    # AVGAS approx
    "mtow_lb": 2550.0,         # takeoff max per sheet
    "ramp_max_lb": 2558.0,     # ramp max per sheet header
    "taxi_fuel_allow_lb": -8.0,
    "taxi_fuel_allow_moment": -760.0,  # in-lb
    "taxi_fuel_allow_arm_in": 95.5,    # shown on sheet
    "fuel_arm_in_default": 95.0,       # shown on sheet
    "fuel_gal_to_l": 3.78541,
    "lb_to_kg": 0.45359237,
}

# CG chart anchors (PDF points, bottom-left origin) — page 0 (first page)
# Provided by user (pdf-coordinates.com). Weight lines: 1200 lb (bottom) and 2550 lb (top).
CG_ANCHORS = {
    1200: {
        82: (182, 72),
        83: (199, 72),
        84: (213, 71),
        85: (229, 72),
        86: (245, 72),
        87: (262, 72),
        88: (277, 73),
        89: (293, 73),
        90: (308, 72),
        91: (323, 72),
        92: (340, 73),
        93: (355, 72),
    },
    2550: {
        82: (134, 245),
        83: (155, 260),
        84: (178, 276),
        85: (202, 294),
        86: (228, 307),
        87: (255, 322),
        88: (285, 338),
        89: (315, 343),
        90: (345, 343),
        91: (374, 343),
        92: (404, 343),
        93: (435, 344),
    },
}

# Approved Airfields (copied from your Tecnam app)
AERODROMES_DB = {
    "LEBZ": {"name": "Badajoz", "lat": 38.8913, "lon": -6.8214, "elev_ft": 608.0,
        "runways": [{"id": "13", "qfu": 130.0, "toda": 2852.0, "lda": 2852.0, "slope_pc": 0.0, "paved": True},
                    {"id": "31", "qfu": 310.0, "toda": 2852.0, "lda": 2852.0, "slope_pc": 0.0, "paved": True}]},
    "LPBR": {"name": "Braga", "lat": 41.5872, "lon": -8.4451, "elev_ft": 243.0,
        "runways": [{"id": "18", "qfu": 180.0, "toda": 939.0, "lda": 939.0, "slope_pc": 0.0, "paved": True},
                    {"id": "36", "qfu": 360.0, "toda": 939.0, "lda": 939.0, "slope_pc": 0.0, "paved": True}]},
    "LPBG": {"name": "Bragança", "lat": 41.8578, "lon": -6.7074, "elev_ft": 2278.0,
        "runways": [{"id": "02", "qfu": 20.0, "toda": 1700.0, "lda": 1700.0, "slope_pc": 0.0, "paved": True},
                    {"id": "20", "qfu": 200.0, "toda": 1700.0, "lda": 1700.0, "slope_pc": 0.0, "paved": True}]},
    "LPCB": {"name": "Castelo Branco", "lat": 39.8483, "lon": -7.4417, "elev_ft": 1251.0,
        "runways": [{"id": "16", "qfu": 160.0, "toda": 1460.0, "lda": 1460.0, "slope_pc": 0.0, "paved": True},
                    {"id": "34", "qfu": 340.0, "toda": 1460.0, "lda": 1460.0, "slope_pc": 0.0, "paved": True}]},
    "LPCO": {"name": "Coimbra", "lat": 40.1582, "lon": -8.4705, "elev_ft": 570.0,
        "runways": [{"id": "16", "qfu": 160.0, "toda": 923.0, "lda": 923.0, "slope_pc": 0.0, "paved": True},
                    {"id": "34", "qfu": 340.0, "toda": 923.0, "lda": 923.0, "slope_pc": 0.0, "paved": True}]},
    "LPEV": {"name": "Évora", "lat": 38.5297, "lon": -7.8919, "elev_ft": 807.0,
        "runways": [{"id": "01", "qfu": 10.0, "toda": 1300.0, "lda": 1300.0, "slope_pc": 0.0, "paved": True},
                    {"id": "19", "qfu": 190.0, "toda": 1300.0, "lda": 1300.0, "slope_pc": 0.0, "paved": True},
                    {"id": "07", "qfu": 70.0, "toda": 1300.0, "lda": 1300.0, "slope_pc": 0.0, "paved": True},
                    {"id": "25", "qfu": 250.0, "toda": 1300.0, "lda": 1300.0, "slope_pc": 0.0, "paved": True}]},
    "LEMG": {"name": "Málaga", "lat": 36.6749, "lon": -4.4991, "elev_ft": 52.0,
        "runways": [{"id": "12", "qfu": 120.0, "toda": 2750.0, "lda": 2750.0, "slope_pc": 0.0, "paved": True},
                    {"id": "30", "qfu": 300.0, "toda": 2750.0, "lda": 2750.0, "slope_pc": 0.0, "paved": True},
                    {"id": "13", "qfu": 130.0, "toda": 3200.0, "lda": 3200.0, "slope_pc": 0.0, "paved": True},
                    {"id": "31", "qfu": 310.0, "toda": 3200.0, "lda": 3200.0, "slope_pc": 0.0, "paved": True}]},
    "LPSO": {"name": "Ponte de Sôr", "lat": 39.2117, "lon": -8.0578, "elev_ft": 390.0,
        "runways": [{"id": "03", "qfu": 30.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True},
                    {"id": "21", "qfu": 210.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True}]},
    "LEZL": {"name": "Sevilha", "lat": 37.4180, "lon": -5.8931, "elev_ft": 111.0,
        "runways": [{"id": "09", "qfu": 90.0, "toda": 3364.0, "lda": 3364.0, "slope_pc": 0.0, "paved": True},
                    {"id": "27", "qfu": 270.0, "toda": 3364.0, "lda": 3364.0, "slope_pc": 0.0, "paved": True}]},
    "LEVX": {"name": "Vigo", "lat": 42.2318, "lon": -8.6268, "elev_ft": 856.0,
        "runways": [{"id": "01", "qfu": 10.0, "toda": 2385.0, "lda": 2385.0, "slope_pc": 0.0, "paved": True},
                    {"id": "19", "qfu": 190.0, "toda": 2385.0, "lda": 2385.0, "slope_pc": 0.0, "paved": True}]},
    "LPVR": {"name": "Vila Real", "lat": 41.2743, "lon": -7.7205, "elev_ft": 1832.0,
        "runways": [{"id": "02", "qfu": 20.0, "toda": 946.0, "lda": 946.0, "slope_pc": 0.0, "paved": True},
                    {"id": "20", "qfu": 200.0, "toda": 946.0, "lda": 946.0, "slope_pc": 0.0, "paved": True}]},
    "LPVZ": {"name": "Viseu", "lat": 40.7255, "lon": -7.8890, "elev_ft": 2060.0,
        "runways": [{"id": "18", "qfu": 180.0, "toda": 1000.0, "lda": 1000.0, "slope_pc": 0.0, "paved": True},
                    {"id": "36", "qfu": 360.0, "toda": 1000.0, "lda": 1000.0, "slope_pc": 0.0, "paved": True}]},
    "LPCS": {"name": "Cascais", "lat": 38.7256, "lon": -9.3553, "elev_ft": 326.0,
        "runways": [{"id": "17", "qfu": 170.0, "toda": 1400.0, "lda": 1400.0, "slope_pc": 0.0, "paved": True},
                    {"id": "35", "qfu": 350.0, "toda": 1400.0, "lda": 1400.0, "slope_pc": 0.0, "paved": True}]},
    "LPMT": {"name": "Montijo", "lat": 38.7039, "lon": -9.0350, "elev_ft": 46.0,
        "runways": [{"id": "07", "qfu": 70.0, "toda": 2448.0, "lda": 2448.0, "slope_pc": 0.0, "paved": True},
                    {"id": "25", "qfu": 250.0, "toda": 2448.0, "lda": 2448.0, "slope_pc": 0.0, "paved": True},
                    {"id": "01", "qfu": 10.0, "toda": 2187.0, "lda": 2187.0, "slope_pc": 0.0, "paved": True},
                    {"id": "19", "qfu": 190.0, "toda": 2187.0, "lda": 2187.0, "slope_pc": 0.0, "paved": True}]},
    "LPST": {"name": "Sintra", "lat": 38.8311, "lon": -9.3397, "elev_ft": 441.0,
        "runways": [{"id": "17", "qfu": 170.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True},
                    {"id": "35", "qfu": 350.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True}]},
    "LPBJ": {"name": "Beja", "lat": 38.0789, "lon": -7.9322, "elev_ft": 636.0,
        "runways": [{"id": "01L", "qfu": 10.0, "toda": 2448.0, "lda": 2448.0, "slope_pc": 0.0, "paved": True},
                    {"id": "19R", "qfu": 190.0, "toda": 2448.0, "lda": 2448.0, "slope_pc": 0.0, "paved": True},
                    {"id": "01R", "qfu": 10.0, "toda": 3449.0, "lda": 3449.0, "slope_pc": 0.0, "paved": True},
                    {"id": "19L", "qfu": 190.0, "toda": 3449.0, "lda": 3449.0, "slope_pc": 0.0, "paved": True}]},
    "LPFR": {"name": "Faro", "lat": 37.0144, "lon": -7.9658, "elev_ft": 24.0,
        "runways": [{"id": "10", "qfu": 100.0, "toda": 2490.0, "lda": 2490.0, "slope_pc": 0.0, "paved": True},
                    {"id": "28", "qfu": 280.0, "toda": 2490.0, "lda": 2490.0, "slope_pc": 0.0, "paved": True}]},
    "LPPM": {"name": "Portimão", "lat": 37.1493, "lon": -8.58397, "elev_ft": 5.0,
        "runways": [{"id": "11", "qfu": 110.0, "toda": 860.0, "lda": 860.0, "slope_pc": 0.0, "paved": True},
                    {"id": "29", "qfu": 290.0, "toda": 860.0, "lda": 860.0, "slope_pc": 0.0, "paved": True}]},
    "LPPR": {"name": "Porto", "lat": 41.2481, "lon": -8.6811, "elev_ft": 227.0,
        "runways": [{"id": "17", "qfu": 170.0, "toda": 3480.0, "lda": 3480.0, "slope_pc": 0.0, "paved": True},
                    {"id": "35", "qfu": 350.0, "toda": 3480.0, "lda": 3480.0, "slope_pc": 0.0, "paved": True}]},
}

DEFAULT_LEGS4 = [
    {"role": "Departure",   "icao": "LPSO"},
    {"role": "Arrival",     "icao": "LPSO"},
    {"role": "Alternate 1", "icao": "LPEV"},
    {"role": "Alternate 2", "icao": "LPCB"},
]


# -----------------------------
# Small utils
# -----------------------------
def lb_to_kg(lb: float) -> float:
    return float(lb) * AC["lb_to_kg"]

def usg_to_l(usg: float) -> float:
    return float(usg) * AC["fuel_gal_to_l"]

def fmt_hm(total_min: int) -> str:
    total_min = int(total_min or 0)
    h, m = divmod(total_min, 60)
    if h <= 0:
        return f"{m} min"
    if m == 0:
        return f"{h} h"
    return f"{h} h {m:02d} min"

def wind_round10(deg: int) -> int:
    d = int(round((deg % 360) / 10.0) * 10) % 360
    return 360 if d == 0 else d  # show 360 instead of 0

def fmt_wind(deg: int, kt: int) -> str:
    d = wind_round10(int(deg))
    return f"{d:03d}/{int(kt):02d}"

def angle_diff_deg(a, b):
    d = (a - b) % 360
    return d - 360 if d > 180 else d

def wind_components(qfu_deg, wind_dir_from_deg, wind_speed_kt):
    diff = ((wind_dir_from_deg - qfu_deg + 180) % 360) - 180
    hw = wind_speed_kt * cos(radians(diff))     # + headwind, - tailwind
    cw = wind_speed_kt * sin(radians(diff))     # + from right, - from left
    side = "R" if cw > 0 else ("L" if cw < 0 else "")
    return hw, abs(cw), side

def best_runway_for_wind(runways, wind_from_deg, wind_kt):
    best = None
    best_head = -1e9
    for r in runways:
        qfu = float(r.get("qfu", 0.0))
        diff = radians(abs(angle_diff_deg(float(wind_from_deg), qfu)))
        head = float(wind_kt) * cos(diff)
        if head > best_head:
            best_head = head
            best = r
    return best, best_head


# -----------------------------
# Forecast — Open-Meteo (hourly)
# -----------------------------
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

@st.cache_data(ttl=900, show_spinner=False)
def om_point_forecast(lat, lon, start_date_iso, end_date_iso):
    params = {
        "latitude": round(float(lat), 4),
        "longitude": round(float(lon), 4),
        "hourly": ",".join([
            "temperature_2m",
            "wind_speed_10m",
            "wind_direction_10m",
            "pressure_msl",
        ]),
        "timezone": "UTC",
        "windspeed_unit": "kn",
        "temperature_unit": "celsius",
        "pressure_unit": "hPa",
        "start_date": start_date_iso,
        "end_date": end_date_iso,
    }
    r = requests.get(OPENMETEO_URL, params=params, timeout=20)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "detail": r.text, "params": params}
    data = r.json()
    h = data.get("hourly", {}) or {}
    return h

def om_hours(hourly):
    times = hourly.get("time", []) or []
    out = []
    for i, t in enumerate(times):
        dtu = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        out.append((i, dtu))
    return out

def om_unpack_at(hourly, idx):
    def get(key, default=None):
        arr = hourly.get(key, []) or []
        return arr[idx] if idx is not None and idx < len(arr) else default

    temp = get("temperature_2m", None)
    wdir = get("wind_direction_10m", None)
    wspd = get("wind_speed_10m", None)
    qnh  = get("pressure_msl", None)

    if temp is None or wdir is None or wspd is None or qnh is None:
        return None

    return {
        "temp": int(round(float(temp))),
        "qnh": int(round(float(qnh))),
        "wind_dir": int(round(float(wdir))),
        "wind_kt": int(round(float(wspd))),
    }

def pressure_alt_ft(elev_ft: float, qnh_hpa: int) -> float:
    # simple approximation: 30 ft per hPa from 1013
    return float(elev_ft) + (1013.0 - float(qnh_hpa)) * 30.0

def density_alt_ft(pa_ft: float, oat_c: int, elev_ft: float) -> float:
    isa = 15.0 - 2.0 * (float(elev_ft) / 1000.0)
    return float(pa_ft) + 120.0 * (float(oat_c) - isa)


# -----------------------------
# GitHub Gist — PA28 fleet
# -----------------------------
GIST_FILE_PA28 = "sevenair_pa28_fleet.json"

def gist_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gist_load_json(token, gist_id, filename):
    r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(token), timeout=15)
    if r.status_code != 200:
        return None, f"GitHub error {r.status_code}: {r.text}"
    data = r.json()
    files = (data.get("files") or {})
    if filename not in files:
        return None, f"File '{filename}' not found in gist."
    content = files[filename].get("content")
    if content is None:
        return None, "Gist file has no content."
    try:
        return json.loads(content), None
    except Exception as e:
        return None, f"JSON parse error: {e}"

def gist_save_json(token, gist_id, filename, obj):
    payload = {"files": {filename: {"content": json.dumps(obj, indent=2, sort_keys=True)}}}
    r = requests.patch(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(token), data=json.dumps(payload), timeout=15)
    if r.status_code not in (200, 201):
        return f"GitHub error {r.status_code}: {r.text}"
    return None


# -----------------------------
# PDF helpers
# -----------------------------
def read_pdf_bytes(paths) -> bytes:
    for p in paths:
        pp = Path(p)
        if pp.exists():
            return pp.read_bytes()
    raise FileNotFoundError(f"Template not found: {paths}")

def get_field_names(template_bytes: bytes) -> set:
    names = set()
    reader = PdfReader(io.BytesIO(template_bytes))
    fd = reader.get_fields()
    if fd:
        names.update(fd.keys())
    return names

def fill_pdf(template_bytes: bytes, fields: dict) -> bytes:
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()
    for pg in reader.pages:
        writer.add_page(pg)

    root = reader.trailer["/Root"]
    if "/AcroForm" not in root:
        raise RuntimeError("Template PDF has no AcroForm.")
    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    try:
        writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): True})
    except Exception:
        pass

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)

    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()

def merge_overlay_on_page0(filled_pdf_bytes: bytes, overlay_pdf_bytes: bytes) -> bytes:
    base = PdfReader(io.BytesIO(filled_pdf_bytes))
    ov = PdfReader(io.BytesIO(overlay_pdf_bytes))
    writer = PdfWriter()
    for i in range(len(base.pages)):
        p = base.pages[i]
        if i == 0:
            p.merge_page(ov.pages[0])  # page index 0 (always)
        writer.add_page(p)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# -----------------------------
# CG chart overlay
# -----------------------------
def _interp_point(x, x0, x1, p0, p1):
    if x1 == x0:
        return p0
    t = (x - x0) / (x1 - x0)
    return (p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1]))

def chart_line_endpoints_for_cg(cg_in: float):
    # Clamp and interpolate between integer CG anchors (82..93)
    cgs = sorted(CG_ANCHORS[1200].keys())
    cg = max(cgs[0], min(cgs[-1], float(cg_in)))
    c0 = max([c for c in cgs if c <= cg])
    c1 = min([c for c in cgs if c >= cg])

    b0 = CG_ANCHORS[1200][c0]
    b1 = CG_ANCHORS[1200][c1]
    t0 = CG_ANCHORS[2550][c0]
    t1 = CG_ANCHORS[2550][c1]

    p_bot = _interp_point(cg, c0, c1, b0, b1)
    p_top = _interp_point(cg, c0, c1, t0, t1)
    return p_bot, p_top

def point_at_weight_on_cg_line(cg_in: float, weight_lb: float):
    p_bot, p_top = chart_line_endpoints_for_cg(cg_in)
    w0, w1 = 1200.0, 2550.0
    w = max(w0, min(w1, float(weight_lb)))
    t = (w - w0) / (w1 - w0)
    return (p_bot[0] + t * (p_top[0] - p_bot[0]), p_bot[1] + t * (p_top[1] - p_bot[1]))

def build_chart_overlay(page_w, page_h, cases):
    """
    cases: list of dicts:
      { "label": "Empty", "cg": 86.2, "weight": 1650, "color": (r,g,b) 0..1 }
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))

    # Draw the three CG vertical lines + weight point
    for case in cases:
        col = Color(case["color"][0], case["color"][1], case["color"][2], alpha=1.0)
        cg = float(case["cg"])
        wlb = float(case["weight"])

        p_bot, p_top = chart_line_endpoints_for_cg(cg)
        px, py = point_at_weight_on_cg_line(cg, wlb)

        c.setStrokeColor(col)
        c.setLineWidth(2.0)
        c.line(p_bot[0], p_bot[1], p_top[0], p_top[1])

        c.setFillColor(col)
        c.circle(px, py, 5.5, stroke=1, fill=1)

    # Legend (right side of chart area, matching your sheet)
    # Positioned to not collide with the envelope; adjust if you want.
    lx, ly = 470, 285
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(Color(0, 0, 0))
    c.drawString(lx, ly + 52, "Legend")

    c.setFont("Helvetica", 10)
    y = ly + 36
    for case in cases:
        col = Color(case["color"][0], case["color"][1], case["color"][2], alpha=1.0)
        c.setFillColor(col)
        c.rect(lx, y, 10, 10, stroke=0, fill=1)
        c.setFillColor(Color(0, 0, 0))
        c.drawString(lx + 16, y + 1, case["label"])
        y -= 16

    c.showPage()
    c.save()
    return buf.getvalue()


# -----------------------------
# Session state init
# -----------------------------
def ensure_state():
    if "pa28_fleet_loaded" not in st.session_state:
        st.session_state.pa28_fleet_loaded = False

    if "pa28_fleet" not in st.session_state:
        st.session_state.pa28_fleet = {}

    if not st.session_state.pa28_fleet_loaded:
        token = st.secrets.get("GITHUB_GIST_TOKEN", "")
        gist_id = st.secrets.get("GITHUB_GIST_ID_PA28", "")
        if token and gist_id:
            data, err = gist_load_json(token, gist_id, GIST_FILE_PA28)
            if data is not None:
                st.session_state.pa28_fleet = data
        st.session_state.pa28_fleet_loaded = True

    if "flight_date" not in st.session_state:
        st.session_state.flight_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).date()

    if "dep_time_utc" not in st.session_state:
        # next full UTC hour
        nowu = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        st.session_state.dep_time_utc = nowu.time().replace(second=0, microsecond=0)

    if "arr_time_utc" not in st.session_state:
        nowu = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=2)
        st.session_state.arr_time_utc = nowu.time().replace(second=0, microsecond=0)

    if "legs4" not in st.session_state:
        st.session_state.legs4 = [dict(x) for x in DEFAULT_LEGS4]

    if "met4" not in st.session_state:
        st.session_state.met4 = [
            {"temp": 15, "qnh": 1013, "wind_dir": 240, "wind_kt": 8, "manual": False}
            for _ in range(4)
        ]

    if "forecast4" not in st.session_state:
        st.session_state.forecast4 = [None] * 4
    if "hours4" not in st.session_state:
        st.session_state.hours4 = [[] for _ in range(4)]
    if "hour_idx4" not in st.session_state:
        st.session_state.hour_idx4 = [None] * 4

    # Force defaults for ICAO widgets only on first run
    for i, leg in enumerate(DEFAULT_LEGS4):
        st.session_state.setdefault(f"icao_{i}", leg["icao"])

    # Fuel planning defaults
    st.session_state.setdefault("fuel_gph", 10.0)  # USGPH
    st.session_state.setdefault("taxi_min", 15)
    st.session_state.setdefault("climb_min", 10)
    st.session_state.setdefault("enrt_h", 1)
    st.session_state.setdefault("enrt_min", 0)
    st.session_state.setdefault("desc_min", 10)
    st.session_state.setdefault("alt_min", 45)

    # Aircraft selection defaults
    st.session_state.setdefault("registration", "")

ensure_state()


# -----------------------------
# Header
# -----------------------------
st.markdown('<div class="hdr">Piper PA-28 — Mass & Balance + Forecast + PDF</div>', unsafe_allow_html=True)

tabs = st.tabs(["1) Flight", "2) Airfields & Forecast", "3) Weight & Balance", "4) Fuel", "5) PDF"])


# -----------------------------
# 1) Flight
# -----------------------------
with tabs[0]:
    c1, c2, c3 = st.columns([0.42, 0.29, 0.29])

    with c1:
        st.markdown("### Flight")
        st.session_state.flight_date = st.date_input("Flight date (Europe/Lisbon)", value=st.session_state.flight_date)

    with c2:
        st.markdown("### Times (UTC)")
        st.session_state.dep_time_utc = st.time_input("Departure time (UTC)", value=st.session_state.dep_time_utc, step=3600)

    with c3:
        st.markdown("### ")
        st.session_state.arr_time_utc = st.time_input("Arrival time (UTC)", value=st.session_state.arr_time_utc, step=3600)

    st.markdown("### Aircraft")

    fleet = st.session_state.pa28_fleet or {}
    regs = sorted(list(fleet.keys()))
    if not regs:
        st.warning("PA-28 fleet not loaded from Gist. Check Streamlit secrets: GITHUB_GIST_TOKEN and GITHUB_GIST_ID_PA28.")
        regs = ["OE-KPD"]  # fallback list just to keep UI usable

    # keep current selection if still valid
    if not st.session_state.registration:
        st.session_state.registration = regs[0]
    if st.session_state.registration not in regs:
        st.session_state.registration = regs[0]

    reg = st.selectbox("Registration", regs, index=regs.index(st.session_state.registration))
    st.session_state.registration = reg

    # Aircraft-specific items from gist (user will fill in)
    rec = fleet.get(reg, {}) if fleet else {}
    ew_lb = rec.get("empty_weight_lb", None)
    em_inlb = rec.get("empty_moment_inlb", None)

    st.markdown(
        "<div class='row'>"
        f"<div class='kpi'><div class='sub'>Empty weight</div><b>{('—' if ew_lb is None else f'{ew_lb:.0f} lb')} ({('—' if ew_lb is None else f'{lb_to_kg(ew_lb):.0f} kg')})</b></div>"
        f"<div class='kpi'><div class='sub'>Empty moment</div><b>{('—' if em_inlb is None else f'{em_inlb:.0f} in-lb')}</b></div>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.info("If Empty Weight / Moment show as '—', fill them in your Gist for this registration (empty_weight_lb, empty_moment_inlb).")


# -----------------------------
# 2) Airfields & Forecast (4 legs)
# -----------------------------
def leg_target_dt_utc(role: str) -> dt.datetime:
    base_date = st.session_state.flight_date

    if role == "Departure":
        t = st.session_state.dep_time_utc
        return dt.datetime.combine(base_date, t).replace(tzinfo=dt.timezone.utc)

    t_arr = st.session_state.arr_time_utc
    arr_dt = dt.datetime.combine(base_date, t_arr).replace(tzinfo=dt.timezone.utc)

    if role in ("Alternate 1", "Alternate 2"):
        return arr_dt + dt.timedelta(hours=1)

    return arr_dt


with tabs[1]:
    st.markdown("### Approved Airfields (DEP / ARR / ALT1 / ALT2) + Forecast (Open-Meteo)")

    icao_options = sorted(AERODROMES_DB.keys())

    for i, leg in enumerate(st.session_state.legs4):
        role = leg["role"]

        st.markdown(f"#### {role}")
        left, mid, right = st.columns([0.35, 0.22, 0.43])

        with left:
            icao = st.selectbox("ICAO", options=icao_options, key=f"icao_{i}")
            st.session_state.legs4[i]["icao"] = icao

            ad = AERODROMES_DB[icao]
            st.caption(f"{ad['name']} — Elev {ad['elev_ft']:.0f} ft")

            # Runway selection suggested by wind
            met = st.session_state.met4[i]
            best_rw, best_head = best_runway_for_wind(ad["runways"], met["wind_dir"], met["wind_kt"])
            rw_ids = [r["id"] for r in ad["runways"]]
            default_rw = best_rw["id"] if best_rw else rw_ids[0]
            st.session_state.setdefault(f"rw_{i}", default_rw)

            rw_id = st.selectbox("Runway", rw_ids, key=f"rw_{i}")
            rw = next(r for r in ad["runways"] if r["id"] == rw_id)

            hw, xw, side = wind_components(rw["qfu"], met["wind_dir"], met["wind_kt"])
            st.caption(f"Suggested by wind: **{default_rw}** (headwind {best_head:.1f} kt)")

            st.markdown(
                f"<span class='chip'>QFU {rw['qfu']:.0f}°</span>"
                f"<span class='chip'>{'HW' if hw>=0 else 'TW'} {abs(hw):.0f} kt</span>"
                f"<span class='chip'>XW {side} {xw:.0f} kt</span>",
                unsafe_allow_html=True,
            )

        with mid:
            target = leg_target_dt_utc(role)
            st.caption("Time used (UTC)")
            st.code(target.strftime("%Y-%m-%d %H:00Z"))

            manual = st.checkbox("Manual MET", value=bool(st.session_state.met4[i]["manual"]), key=f"manual_{i}")
            st.session_state.met4[i]["manual"] = manual

            if st.button(f"Fetch forecast ({role})", key=f"fetch_{i}"):
                start_iso = st.session_state.flight_date.strftime("%Y-%m-%d")
                end_iso = start_iso
                hourly = om_point_forecast(ad["lat"], ad["lon"], start_iso, end_iso)
                if isinstance(hourly, dict) and hourly.get("time"):
                    st.session_state.forecast4[i] = hourly
                    hours = om_hours(hourly)
                    st.session_state.hours4[i] = hours

                    nearest_idx, nearest_time = min(hours, key=lambda h: abs(h[1] - target))
                    st.session_state.hour_idx4[i] = nearest_idx
                    met_new = om_unpack_at(hourly, nearest_idx)
                    if met_new and not manual:
                        st.session_state.met4[i].update(met_new)
                    st.success(f"Forecast loaded: {nearest_time.strftime('%Y-%m-%d %H:00Z')}")
                else:
                    st.error("Forecast error or no data returned.")

        with right:
            met = st.session_state.met4[i]

            cA, cB = st.columns(2)

            with cA:
                temp = st.number_input("OAT (°C)", value=int(met["temp"]), step=1, key=f"temp_{i}")
                qnh = st.number_input("QNH (hPa)", min_value=900, max_value=1050, value=int(met["qnh"]), step=1, key=f"qnh_{i}")

            with cB:
                wdir = st.number_input("Wind FROM (°)", min_value=0, max_value=360, value=int(met["wind_dir"]), step=1, key=f"wdir_{i}")
                wspd = st.number_input("Wind speed (kt)", min_value=0, value=int(met["wind_kt"]), step=1, key=f"wspd_{i}")

            # write back to state (no widget-key writes)
            st.session_state.met4[i]["temp"] = int(temp)
            st.session_state.met4[i]["qnh"] = int(qnh)
            st.session_state.met4[i]["wind_dir"] = int(wdir)
            st.session_state.met4[i]["wind_kt"] = int(wspd)

            st.markdown(
                f"<div class='box'>"
                f"<div><b>Wind</b> {fmt_wind(wdir, wspd)}</div>"
                f"<div class='hint'>Rounded to 10° for display / PDF</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            pa = pressure_alt_ft(ad["elev_ft"], qnh)
            da = density_alt_ft(pa, temp, ad["elev_ft"])
            st.markdown(
                f"<div class='box'>"
                f"<div><b>PA</b> {pa:.0f} ft</div>"
                f"<div><b>DA</b> {da:.0f} ft</div>"
                f"</div>",
                unsafe_allow_html=True,
            )


# -----------------------------
# 3) Weight & Balance
# -----------------------------
with tabs[2]:
    st.markdown("### Weight & Balance (lbs + kg)")

    reg = st.session_state.registration
    fleet = st.session_state.pa28_fleet or {}
    rec = fleet.get(reg, {}) if fleet else {}

    # Empty from gist
    ew_lb = rec.get("empty_weight_lb", 0.0) or 0.0
    em_inlb = rec.get("empty_moment_inlb", 0.0) or 0.0

    # Arms (datum inches) — user can change (per aircraft)
    # Defaults match the sheet; keep simple and editable.
    cL, cR = st.columns([0.55, 0.45])
    with cL:
        st.markdown("#### Inputs")
        w_front = st.number_input("Pilot & front passenger (lb)", min_value=0.0, value=165.0, step=5.0)
        arm_front = st.number_input("Front seats arm (in)", value=80.5, step=0.1)

        w_rear = st.number_input("Rear passengers (lb)", min_value=0.0, value=0.0, step=5.0)
        arm_rear = st.number_input("Rear seats arm (in)", value=118.1, step=0.1)

        fuel_usg = st.number_input("Fuel (US gal)", min_value=0.0, max_value=float(AC["max_fuel_usg"]), value=0.0, step=1.0)
        arm_fuel = st.number_input("Fuel arm (in)", value=float(AC["fuel_arm_in_default"]), step=0.1)

        w_bag = st.number_input("Baggage (lb)", min_value=0.0, value=0.0, step=5.0)
        arm_bag = st.number_input("Baggage arm (in)", value=142.8, step=0.1)

    # Compute
    fuel_lb = float(fuel_usg) * AC["fuel_lb_per_usg"]
    m_empty = float(em_inlb)
    m_front = float(w_front) * float(arm_front)
    m_rear = float(w_rear) * float(arm_rear)
    m_fuel = float(fuel_lb) * float(arm_fuel)
    m_bag = float(w_bag) * float(arm_bag)

    ramp_w = float(ew_lb) + float(w_front) + float(w_rear) + float(fuel_lb) + float(w_bag)
    ramp_m = m_empty + m_front + m_rear + m_fuel + m_bag
    ramp_cg = (ramp_m / ramp_w) if ramp_w > 0 else 0.0

    taxi_w = AC["taxi_fuel_allow_lb"]
    taxi_m = AC["taxi_fuel_allow_moment"]
    to_w = ramp_w + taxi_w
    to_m = ramp_m + taxi_m
    to_cg = (to_m / to_w) if to_w > 0 else 0.0

    # Landing from fuel burn in Fuel tab (trip burn only)
    fuel_trip_usg = float(st.session_state.get("_fuel_trip_usg", 0.0) or 0.0)
    burn_lb = fuel_trip_usg * AC["fuel_lb_per_usg"]
    ld_w = max(0.0, to_w - burn_lb)
    ld_m = to_m - burn_lb * arm_fuel
    ld_cg = (ld_m / ld_w) if ld_w > 0 else 0.0

    with cR:
        st.markdown("#### Summary")
        st.markdown(
            "<div class='row'>"
            f"<div class='kpi'><div class='sub'>Empty</div><b>{ew_lb:.0f} lb ({lb_to_kg(ew_lb):.0f} kg)</b><div class='hint'>CG {((em_inlb/ew_lb) if ew_lb>0 else 0):.2f} in</div></div>"
            f"<div class='kpi'><div class='sub'>Ramp</div><b>{ramp_w:.0f} lb ({lb_to_kg(ramp_w):.0f} kg)</b><div class='hint'>CG {ramp_cg:.2f} in</div></div>"
            f"<div class='kpi'><div class='sub'>Takeoff</div><b>{to_w:.0f} lb ({lb_to_kg(to_w):.0f} kg)</b><div class='hint'>CG {to_cg:.2f} in</div></div>"
            f"<div class='kpi'><div class='sub'>Landing</div><b>{ld_w:.0f} lb ({lb_to_kg(ld_w):.0f} kg)</b><div class='hint'>CG {ld_cg:.2f} in</div></div>"
            "</div>",
            unsafe_allow_html=True,
        )

        warn = []
        if ramp_w > AC["ramp_max_lb"]:
            warn.append(f"Ramp weight exceeds limit: {ramp_w:.0f} > {AC['ramp_max_lb']:.0f} lb")
        if to_w > AC["mtow_lb"]:
            warn.append(f"Takeoff weight exceeds MTOW: {to_w:.0f} > {AC['mtow_lb']:.0f} lb")
        if warn:
            for w in warn:
                st.error(w)

    st.session_state["_wb"] = {
        "reg": reg,
        "empty_w_lb": float(ew_lb),
        "empty_m_inlb": float(em_inlb),
        "front_w_lb": float(w_front),
        "front_arm_in": float(arm_front),
        "rear_w_lb": float(w_rear),
        "rear_arm_in": float(arm_rear),
        "fuel_usg": float(fuel_usg),
        "fuel_w_lb": float(fuel_lb),
        "fuel_arm_in": float(arm_fuel),
        "bag_w_lb": float(w_bag),
        "bag_arm_in": float(arm_bag),
        "ramp_w_lb": ramp_w,
        "ramp_m_inlb": ramp_m,
        "ramp_cg_in": ramp_cg,
        "to_w_lb": to_w,
        "to_m_inlb": to_m,
        "to_cg_in": to_cg,
        "ld_w_lb": ld_w,
        "ld_m_inlb": ld_m,
        "ld_cg_in": ld_cg,
    }


# -----------------------------
# 4) Fuel
# -----------------------------
with tabs[3]:
    st.markdown("### Fuel Planning (US gal + liters)")

    gph = st.number_input("Fuel consumption (US gal / hour)", min_value=5.0, max_value=20.0, value=float(st.session_state.fuel_gph), step=0.5)
    st.session_state.fuel_gph = float(gph)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        taxi_min = st.number_input("Start-up & Taxi (min)", min_value=0, value=int(st.session_state.taxi_min), step=1)
        climb_min = st.number_input("Climb (min)", min_value=0, value=int(st.session_state.climb_min), step=1)
    with c2:
        enrt_h = st.number_input("Enroute (h)", min_value=0, value=int(st.session_state.enrt_h), step=1)
        enrt_min = st.number_input("Enroute (min)", min_value=0, value=int(st.session_state.enrt_min), step=5)
    with c3:
        desc_min = st.number_input("Descent (min)", min_value=0, value=int(st.session_state.desc_min), step=1)
        alt_min = st.number_input("Alternate (min)", min_value=0, value=int(st.session_state.alt_min), step=5)
    with c4:
        st.markdown("**Reserve**")
        reserve_min = 45
        st.write("45 min (fixed)")

    st.session_state.taxi_min = int(taxi_min)
    st.session_state.climb_min = int(climb_min)
    st.session_state.enrt_h = int(enrt_h)
    st.session_state.enrt_min = int(enrt_min)
    st.session_state.desc_min = int(desc_min)
    st.session_state.alt_min = int(alt_min)

    def usg_from_min(mins):
        return float(gph) * (float(mins) / 60.0)

    enrt_total_min = int(enrt_h) * 60 + int(enrt_min)
    trip_min = int(climb_min) + enrt_total_min + int(desc_min)

    taxi_usg = usg_from_min(taxi_min)
    climb_usg = usg_from_min(climb_min)
    enrt_usg = usg_from_min(enrt_total_min)
    desc_usg = usg_from_min(desc_min)

    trip_usg = usg_from_min(trip_min)
    cont_usg = 0.05 * trip_usg
    cont_min = int(round(0.05 * trip_min))

    alt_usg = usg_from_min(alt_min)
    reserve_usg = usg_from_min(reserve_min)

    required_usg = taxi_usg + trip_usg + cont_usg + alt_usg + reserve_usg
    required_min = int(taxi_min) + trip_min + cont_min + int(alt_min) + reserve_min

    # store trip burn for landing calc (trip only: climb+enroute+descent)
    st.session_state["_fuel_trip_usg"] = float(trip_usg)

    def fmt_usg(v):
        return f"{v:.1f} USG ({usg_to_l(v):.1f} L)"

    rows = [
        ("Start-up & Taxi", int(taxi_min), taxi_usg),
        ("Climb", int(climb_min), climb_usg),
        ("Enroute", int(enrt_total_min), enrt_usg),
        ("Descent", int(desc_min), desc_usg),
        ("Trip Fuel (Climb+Enroute+Descent)", int(trip_min), trip_usg),
        ("Contingency 5% (Trip)", int(cont_min), cont_usg),
        ("Alternate", int(alt_min), alt_usg),
        ("Reserve 45 min", int(reserve_min), reserve_usg),
        ("Required Ramp Fuel", int(required_min), required_usg),
    ]

    html = ["<table class='mb-table'><tr><th>Item</th><th>Time</th><th>Fuel</th></tr>"]
    for name, mins, usg in rows:
        html.append(f"<tr><td>{name}</td><td>{fmt_hm(mins)}</td><td>{fmt_usg(usg)}</td></tr>")
    html.append("</table>")
    st.markdown("".join(html), unsafe_allow_html=True)

    st.session_state["_fuel"] = {
        "gph": float(gph),
        "taxi_min": int(taxi_min),
        "climb_min": int(climb_min),
        "enrt_min": int(enrt_total_min),
        "desc_min": int(desc_min),
        "trip_min": int(trip_min),
        "cont_min": int(cont_min),
        "alt_min": int(alt_min),
        "reserve_min": int(reserve_min),
        "required_min": int(required_min),
        "taxi_usg": float(taxi_usg),
        "climb_usg": float(climb_usg),
        "enrt_usg": float(enrt_usg),
        "desc_usg": float(desc_usg),
        "trip_usg": float(trip_usg),
        "cont_usg": float(cont_usg),
        "alt_usg": float(alt_usg),
        "reserve_usg": float(reserve_usg),
        "required_usg": float(required_usg),
    }


# -----------------------------
# 5) PDF generation
# -----------------------------
with tabs[4]:
    st.markdown("### PDF — Filled M&B and Performance Data Sheet")

    try:
        template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
        fieldset = get_field_names(template_bytes)

        wb = st.session_state.get("_wb", {})
        fuel = st.session_state.get("_fuel", {})

        # Prepare per-leg rows (DEP/ARR/ALT1/ALT2)
        # PDF field suffixes
        role_to_suffix = {
            "Departure": "DEPARTURE",
            "Arrival": "ARRIVAL",
            "Alternate 1": "ALTERNATE_1",
            "Alternate 2": "ALTERNATE_2",
        }

        legs = st.session_state.legs4
        met4 = st.session_state.met4

        perf_rows = []
        for i, leg in enumerate(legs):
            role = leg["role"]
            icao = leg["icao"]
            ad = AERODROMES_DB[icao]
            met = met4[i]

            target = leg_target_dt_utc(role)

            # runway selection from widget
            rw_id = st.session_state.get(f"rw_{i}", ad["runways"][0]["id"])
            rw = next((r for r in ad["runways"] if r["id"] == rw_id), ad["runways"][0])

            pa = pressure_alt_ft(ad["elev_ft"], met["qnh"])
            da = density_alt_ft(pa, met["temp"], ad["elev_ft"])

            perf_rows.append({
                "role": role,
                "icao": icao,
                "rw_qfu": int(round(rw["qfu"])),
                "elev_ft": int(round(ad["elev_ft"])),
                "qnh": int(met["qnh"]),
                "temp": int(met["temp"]),
                "wind_dir": int(met["wind_dir"]),
                "wind_kt": int(met["wind_kt"]),
                "pa_ft": int(round(pa)),
                "da_ft": int(round(da)),
                # Performance numbers are kept manual for now (you can wire POH tables later)
                "toda_m": int(round(rw["toda"])),
                "lda_m": int(round(rw["lda"])),
                "todr_m": 0,
                "ldr_m": 0,
                "roc_fpm": 0,
                "target": target,
            })

        # Fields map (exact names from your updated PDF)
        fields = {}

        def put(name, val):
            if name in fieldset:
                fields[name] = str(val)

        # Top fields
        date_str = st.session_state.flight_date.strftime("%d/%m/%Y")
        put("Date", date_str)
        put("Aircraft_Reg", wb.get("reg", ""))

        put("MTOW", f"{AC['mtow_lb']:.0f}")
        put("MLW", f"{AC['mtow_lb']:.0f}")

        # Loading data (weights in lb + (kg))
        ew_lb = wb.get("empty_w_lb", 0.0)
        em = wb.get("empty_m_inlb", 0.0)
        ew_arm = (em / ew_lb) if ew_lb > 0 else 0.0

        put("Weight_EMPTY", f"{ew_lb:.0f} ({lb_to_kg(ew_lb):.0f} kg)")
        put("Datum_EMPTY", f"{ew_arm:.1f}")
        put("Moment_EMPTY", f"{em:.0f}")

        w_front = wb.get("front_w_lb", 0.0)
        put("Weight_FRONT", f"{w_front:.0f} ({lb_to_kg(w_front):.0f} kg)")
        put("Moment_FRONT", f"{(w_front * wb.get('front_arm_in', 0.0)):.0f}")

        w_rear = wb.get("rear_w_lb", 0.0)
        put("Weight_REAR", f"{w_rear:.0f} ({lb_to_kg(w_rear):.0f} kg)")
        put("Moment_REAR", f"{(w_rear * wb.get('rear_arm_in', 0.0)):.0f}")

        w_fuel = wb.get("fuel_w_lb", 0.0)
        fuel_usg = wb.get("fuel_usg", 0.0)
        put("Weight_FUEL", f"{w_fuel:.0f} ({lb_to_kg(w_fuel):.0f} kg)")
        put("Moment_FUEL", f"{(w_fuel * wb.get('fuel_arm_in', 0.0)):.0f}")

        w_bag = wb.get("bag_w_lb", 0.0)
        put("Weight_BAGGAGE", f"{w_bag:.0f} ({lb_to_kg(w_bag):.0f} kg)")
        put("Moment_BAGGAGE", f"{(w_bag * wb.get('bag_arm_in', 0.0)):.0f}")

        # Ramp / Takeoff
        ramp_w = wb.get("ramp_w_lb", 0.0)
        ramp_m = wb.get("ramp_m_inlb", 0.0)
        ramp_cg = wb.get("ramp_cg_in", 0.0)
        put("Weight_RAMP", f"{ramp_w:.0f} ({lb_to_kg(ramp_w):.0f} kg)")
        put("Datum_RAMP", f"{ramp_cg:.1f}")
        put("Moment_RAMP", f"{ramp_m:.0f}")

        to_w = wb.get("to_w_lb", 0.0)
        to_m = wb.get("to_m_inlb", 0.0)
        to_cg = wb.get("to_cg_in", 0.0)
        put("Weight_TAKEOFF", f"{to_w:.0f} ({lb_to_kg(to_w):.0f} kg)")
        put("Datum_TAKEOFF", f"{to_cg:.1f}")
        put("Moment_TAKEOFF", f"{to_m:.0f}")

        # Airfields block
        for r in perf_rows:
            suf = role_to_suffix[r["role"]]
            put(f"Airfield_{suf}", r["icao"])
            put(f"RWY_QFU_{suf}", f"{r['rw_qfu']:03d}")
            put(f"Elevation_{suf}", f"{r['elev_ft']}")
            put(f"QNH_{suf}", f"{r['qnh']}")
            put(f"Temperature_{suf}", f"{r['temp']}")
            put(f"Wind_{suf}", fmt_wind(r["wind_dir"], r["wind_kt"]))
            put(f"Pressure_Alt_{suf}", f"{r['pa_ft']}")
            put(f"Density_Alt_{suf}", f"{r['da_ft']}")
            put(f"TODA_{suf}", f"{r['toda_m']}")
            put(f"TODR_{suf}", f"{r['todr_m']}")
            put(f"LDA_{suf}", f"{r['lda_m']}")
            put(f"LDR_{suf}", f"{r['ldr_m']}")
            put(f"ROC_{suf}", f"{r['roc_fpm']}")

        # Fuel block (times + USG + (L))
        def fuel_time(name, mins):
            put(name, fmt_hm(int(mins)))

        def fuel_val(name, usg):
            put(name, f"{usg:.1f} USG ({usg_to_l(usg):.1f} L)")

        fuel_time("Start-up_and_Taxi_TIME", fuel.get("taxi_min", 0))
        fuel_time("CLIMB_TIME", fuel.get("climb_min", 0))
        fuel_time("ENROUTE_TIME", fuel.get("enrt_min", 0))
        fuel_time("DESCENT_TIME", fuel.get("desc_min", 0))
        fuel_time("TRIP_TIME", fuel.get("trip_min", 0))
        fuel_time("Contingency_TIME", fuel.get("cont_min", 0))
        fuel_time("ALTERNATE_TIME", fuel.get("alt_min", 0))
        fuel_time("RESERVE_TIME", fuel.get("reserve_min", 45))
        fuel_time("REQUIRED_TIME", fuel.get("required_min", 0))
        fuel_time("EXTRA_TIME", 0)
        fuel_time("Total_TIME", fuel.get("required_min", 0))

        fuel_val("Start-up_and_Taxi_FUEL", fuel.get("taxi_usg", 0.0))
        fuel_val("CLIMB_FUEL", fuel.get("climb_usg", 0.0))
        fuel_val("ENROUTE_FUEL", fuel.get("enrt_usg", 0.0))
        fuel_val("DESCENT_FUEL", fuel.get("desc_usg", 0.0))
        fuel_val("TRIP_FUEL", fuel.get("trip_usg", 0.0))
        fuel_val("Contingency_FUEL", fuel.get("cont_usg", 0.0))
        fuel_val("ALTERNATE_FUEL", fuel.get("alt_usg", 0.0))
        fuel_val("RESERVE_FUEL", fuel.get("reserve_usg", 0.0))
        fuel_val("REQUIRED_FUEL", fuel.get("required_usg", 0.0))
        fuel_val("EXTRA_FUEL", 0.0)
        fuel_val("Total_FUEL", fuel.get("required_usg", 0.0))

        # Build overlay chart (page index 0 only)
        empty_cg = (wb.get("empty_m_inlb", 0.0) / wb.get("empty_w_lb", 1.0)) if wb.get("empty_w_lb", 0.0) > 0 else 0.0
        empty_w = wb.get("empty_w_lb", 0.0)

        cases = [
            {"label": "Empty",   "cg": empty_cg,           "weight": empty_w,              "color": (0.1, 0.7, 0.2)},
            {"label": "Takeoff", "cg": wb.get("to_cg_in", 0.0), "weight": wb.get("to_w_lb", 0.0), "color": (0.15, 0.35, 0.95)},
            {"label": "Landing", "cg": wb.get("ld_cg_in", 0.0), "weight": wb.get("ld_w_lb", 0.0), "color": (0.9, 0.2, 0.2)},
        ]

        # Generate button
        if st.button("Generate filled PDF", type="primary"):
            filled = fill_pdf(template_bytes, fields)

            # overlay uses actual page size
            reader_tmp = PdfReader(io.BytesIO(filled))
            p0 = reader_tmp.pages[0]
            page_w = float(p0.mediabox.width)
            page_h = float(p0.mediabox.height)
            overlay = build_chart_overlay(page_w, page_h, cases)
            final_bytes = merge_overlay_on_page0(filled, overlay)

            file_name = f"{wb.get('reg','PA28')}_MB_Perf.pdf"
            st.download_button("Download PDF", data=final_bytes, file_name=file_name, mime="application/pdf")
            st.success("PDF generated.")

    except Exception as e:
        st.error(f"PDF setup error: {e}")


