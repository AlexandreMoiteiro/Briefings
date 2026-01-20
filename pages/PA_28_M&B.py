# PA_28_M&B.py
# Piper PA-28 — Mass & Balance + Forecast + PDF
# Fixes:
# - No mixed numeric types (all int/float consistent)
# - Defaults forced: LPSO / LPSO / LPEV / LPCB
# - Alternates use ARR + 1h
# - Runway auto-selected by wind direction

import streamlit as st
import datetime as dt
import json
import requests
import unicodedata
from pathlib import Path
import pytz
import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

# -----------------------------
# Page config + minimal style
# -----------------------------
st.set_page_config(
    page_title="Piper PA-28 — Mass & Balance + Forecast + PDF",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1200px !important; }
      .hdr{font-size:1.35rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #222;padding-bottom:8px;margin:2px 0 14px}
      .pill{display:inline-block;padding:2px 10px;border-radius:999px;background:#1b1f28;margin-left:6px;font-size:.85rem}
      hr { border: 0; height: 1px; background: #20242c; margin: 14px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# Utilities
# -----------------------------
def ascii_safe(text):
    if not isinstance(text, str):
        return str(text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")

def fmt_hm(total_min: int) -> str:
    total_min = int(total_min or 0)
    if total_min <= 0:
        return "0min"
    h, m = divmod(total_min, 60)
    if h == 0:
        return f"{m}min"
    return f"{h}h" if m == 0 else f"{h}h{m:02d}min"

def round_wind_dir_to_10(deg: float) -> int:
    if deg is None:
        return 0
    d = int(round(float(deg) / 10.0) * 10) % 360
    return 360 if d == 0 else d

def fmt_wind(dir_deg: int, spd_kt: int) -> str:
    d = int(dir_deg) % 360
    if d == 0:
        d = 360
    return f"{d:03d}/{int(spd_kt):02d}"

def lbs_to_kg(lb: float) -> float:
    return float(lb) * 0.45359237

def usg_to_l(usg: float) -> float:
    return float(usg) * 3.785411784

def ang_diff(a: float, b: float) -> float:
    # minimal absolute angular difference 0..180
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)

# -----------------------------
# Aircraft constants
# -----------------------------
PA28 = {
    "name": "Piper PA-28 Archer III",
    "mtow_lb": 2550.0,
    "utility_max_lb": 2130.0,
    "max_fuel_usg": 48.0,
    "fuel_density_lb_per_usg": 6.0,
    "taxi_allowance_lb": -8.0,
    "taxi_allowance_arm_in": 95.5,
    "taxi_allowance_moment_inlb": -760.0,
    "arm_front_in": 80.5,
    "arm_rear_in": 118.1,
    "arm_fuel_in": 95.0,
    "arm_baggage_in": 142.8,
}

# -----------------------------
# Approved airfields DB (same as your Tecnam approach)
# -----------------------------
AERODROMES_DB = {
    "LEBZ": {"name": "Badajoz", "lat": 38.8913, "lon": -6.8214, "elev_ft": 608.0, "runways": [{"id": "13", "qfu": 130.0, "toda": 2852.0, "lda": 2852.0}, {"id": "31", "qfu": 310.0, "toda": 2852.0, "lda": 2852.0}]},
    "LPBR": {"name": "Braga", "lat": 41.5872, "lon": -8.4451, "elev_ft": 243.0, "runways": [{"id": "18", "qfu": 180.0, "toda": 939.0, "lda": 939.0}, {"id": "36", "qfu": 360.0, "toda": 939.0, "lda": 939.0}]},
    "LPBG": {"name": "Bragança", "lat": 41.8578, "lon": -6.7074, "elev_ft": 2278.0, "runways": [{"id": "02", "qfu": 20.0, "toda": 1700.0, "lda": 1700.0}, {"id": "20", "qfu": 200.0, "toda": 1700.0, "lda": 1700.0}]},
    "LPCB": {"name": "Castelo Branco", "lat": 39.8483, "lon": -7.4417, "elev_ft": 1251.0, "runways": [{"id": "16", "qfu": 160.0, "toda": 1460.0, "lda": 1460.0}, {"id": "34", "qfu": 340.0, "toda": 1460.0, "lda": 1460.0}]},
    "LPCO": {"name": "Coimbra", "lat": 40.1582, "lon": -8.4705, "elev_ft": 570.0, "runways": [{"id": "16", "qfu": 160.0, "toda": 923.0, "lda": 923.0}, {"id": "34", "qfu": 340.0, "toda": 923.0, "lda": 923.0}]},
    "LPEV": {"name": "Évora", "lat": 38.5297, "lon": -7.8919, "elev_ft": 807.0, "runways": [{"id": "01", "qfu": 10.0, "toda": 1300.0, "lda": 1300.0}, {"id": "19", "qfu": 190.0, "toda": 1300.0, "lda": 1300.0}, {"id": "07", "qfu": 70.0, "toda": 1300.0, "lda": 1300.0}, {"id": "25", "qfu": 250.0, "toda": 1300.0, "lda": 1300.0}]},
    "LEMG": {"name": "Málaga", "lat": 36.6749, "lon": -4.4991, "elev_ft": 52.0, "runways": [{"id": "12", "qfu": 120.0, "toda": 2750.0, "lda": 2750.0}, {"id": "30", "qfu": 300.0, "toda": 2750.0, "lda": 2750.0}, {"id": "13", "qfu": 130.0, "toda": 3200.0, "lda": 3200.0}, {"id": "31", "qfu": 310.0, "toda": 3200.0, "lda": 3200.0}]},
    "LPSO": {"name": "Ponte de Sôr", "lat": 39.2117, "lon": -8.0578, "elev_ft": 390.0, "runways": [{"id": "03", "qfu": 30.0, "toda": 1800.0, "lda": 1800.0}, {"id": "21", "qfu": 210.0, "toda": 1800.0, "lda": 1800.0}]},
    "LEZL": {"name": "Seville", "lat": 37.4180, "lon": -5.8931, "elev_ft": 111.0, "runways": [{"id": "09", "qfu": 90.0, "toda": 3364.0, "lda": 3364.0}, {"id": "27", "qfu": 270.0, "toda": 3364.0, "lda": 3364.0}]},
    "LEVX": {"name": "Vigo", "lat": 42.2318, "lon": -8.6268, "elev_ft": 856.0, "runways": [{"id": "01", "qfu": 10.0, "toda": 2385.0, "lda": 2385.0}, {"id": "19", "qfu": 190.0, "toda": 2385.0, "lda": 2385.0}]},
    "LPVR": {"name": "Vila Real", "lat": 41.2743, "lon": -7.7205, "elev_ft": 1832.0, "runways": [{"id": "02", "qfu": 20.0, "toda": 946.0, "lda": 946.0}, {"id": "20", "qfu": 200.0, "toda": 946.0, "lda": 946.0}]},
    "LPVZ": {"name": "Viseu", "lat": 40.7255, "lon": -7.8890, "elev_ft": 2060.0, "runways": [{"id": "18", "qfu": 180.0, "toda": 1000.0, "lda": 1000.0}, {"id": "36", "qfu": 360.0, "toda": 1000.0, "lda": 1000.0}]},
    "LPCS": {"name": "Cascais", "lat": 38.7256, "lon": -9.3553, "elev_ft": 326.0, "runways": [{"id": "17", "qfu": 170.0, "toda": 1400.0, "lda": 1400.0}, {"id": "35", "qfu": 350.0, "toda": 1400.0, "lda": 1400.0}]},
    "LPMT": {"name": "Montijo", "lat": 38.7039, "lon": -9.0350, "elev_ft": 46.0, "runways": [{"id": "07", "qfu": 70.0, "toda": 2448.0, "lda": 2448.0}, {"id": "25", "qfu": 250.0, "toda": 2448.0, "lda": 2448.0}, {"id": "01", "qfu": 10.0, "toda": 2187.0, "lda": 2187.0}, {"id": "19", "qfu": 190.0, "toda": 2187.0, "lda": 2187.0}]},
    "LPST": {"name": "Sintra", "lat": 38.8311, "lon": -9.3397, "elev_ft": 441.0, "runways": [{"id": "17", "qfu": 170.0, "toda": 1800.0, "lda": 1800.0}, {"id": "35", "qfu": 350.0, "toda": 1800.0, "lda": 1800.0}]},
    "LPBJ": {"name": "Beja", "lat": 38.0789, "lon": -7.9322, "elev_ft": 636.0, "runways": [{"id": "01L", "qfu": 10.0, "toda": 2448.0, "lda": 2448.0}, {"id": "19R", "qfu": 190.0, "toda": 2448.0, "lda": 2448.0}, {"id": "01R", "qfu": 10.0, "toda": 3449.0, "lda": 3449.0}, {"id": "19L", "qfu": 190.0, "toda": 3449.0, "lda": 3449.0}]},
    "LPFR": {"name": "Faro", "lat": 37.0144, "lon": -7.9658, "elev_ft": 24.0, "runways": [{"id": "10", "qfu": 100.0, "toda": 2490.0, "lda": 2490.0}, {"id": "28", "qfu": 280.0, "toda": 2490.0, "lda": 2490.0}]},
    "LPPM": {"name": "Portimão", "lat": 37.1493, "lon": -8.58397, "elev_ft": 5.0, "runways": [{"id": "11", "qfu": 110.0, "toda": 860.0, "lda": 860.0}, {"id": "29", "qfu": 290.0, "toda": 860.0, "lda": 860.0}]},
    "LPPR": {"name": "Porto", "lat": 41.2481, "lon": -8.6811, "elev_ft": 227.0, "runways": [{"id": "17", "qfu": 170.0, "toda": 3480.0, "lda": 3480.0}, {"id": "35", "qfu": 350.0, "toda": 3480.0, "lda": 3480.0}]},
}

# -----------------------------
# Forecast (Open-Meteo)
# -----------------------------
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

@st.cache_data(ttl=900, show_spinner=False)
def om_point_forecast(lat, lon, start_date_iso, end_date_iso):
    params = {
        "latitude": round(float(lat), 4),
        "longitude": round(float(lon), 4),
        "hourly": ",".join(["temperature_2m", "wind_speed_10m", "wind_direction_10m", "pressure_msl"]),
        "timezone": "UTC",
        "windspeed_unit": "kn",
        "temperature_unit": "celsius",
        "pressure_unit": "hPa",
        "start_date": start_date_iso,
        "end_date": end_date_iso,
    }
    r = requests.get(OPENMETEO_URL, params=params, timeout=20)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "detail": r.text}
    data = r.json()
    return (data.get("hourly", {}) or {})

def om_hours(hourly):
    times = hourly.get("time", []) or []
    out = []
    for i, t in enumerate(times):
        dtu = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        out.append((i, dtu))
    return out

def om_at(hourly, idx):
    def getv(key):
        arr = hourly.get(key, []) or []
        return arr[idx] if idx is not None and idx < len(arr) else None
    temp = getv("temperature_2m")
    wspd = getv("wind_speed_10m")
    wdir = getv("wind_direction_10m")
    qnh  = getv("pressure_msl")
    if temp is None or wspd is None or wdir is None or qnh is None:
        return None
    wdir10 = round_wind_dir_to_10(wdir)
    wspd_i = int(round(float(wspd)))
    return {
        "temp_c": int(round(float(temp))),
        "qnh_hpa": int(round(float(qnh))),
        "wind_dir": int(wdir10),
        "wind_kt": int(wspd_i),
        "wind_str": fmt_wind(wdir10, wspd_i),
    }

# -----------------------------
# GitHub Gist — PA28 fleet
# -----------------------------
GIST_FILE_PA28 = "sevenair_pa28_fleet.json"

def gist_headers(token: str):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gist_load(token: str, gist_id: str):
    r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(token), timeout=20)
    if r.status_code != 200:
        return None, f"GitHub error {r.status_code}: {r.text}"
    data = r.json()
    files = data.get("files", {}) or {}
    if GIST_FILE_PA28 not in files or files[GIST_FILE_PA28].get("content") is None:
        return None, f"Gist file '{GIST_FILE_PA28}' not found."
    return json.loads(files[GIST_FILE_PA28]["content"]), None

# -----------------------------
# PDF
# -----------------------------
PDF_TEMPLATE_PATHS = ["RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"]

def read_pdf_bytes(paths) -> bytes:
    for path_str in paths:
        p = Path(path_str)
        if p.exists():
            return p.read_bytes()
    raise FileNotFoundError(f"Template not found: {paths}")

def get_field_names(template_bytes: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(template_bytes))
    fd = reader.get_fields() or {}
    return sorted(fd.keys())

def fill_pdf(template_bytes: bytes, fields: dict, overlay_first_page_bytes: bytes | None = None) -> bytes:
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    root = reader.trailer["/Root"]
    if "/AcroForm" not in root:
        raise RuntimeError("Template PDF has no AcroForm/fields.")

    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    try:
        writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): True})
    except Exception:
        pass

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)

    # chart is on page index 0
    if overlay_first_page_bytes:
        ov = PdfReader(io.BytesIO(overlay_first_page_bytes))
        writer.pages[0].merge_page(ov.pages[0])

    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()

# -----------------------------
# CG chart calibration (A4 points, bottom-left origin)
# -----------------------------
CHART_REF = {
    82: {"w2": 2050, "p1": (182, 72), "p2": (134, 245)},
    83: {"w2": 2138, "p1": (199, 72), "p2": (155, 260)},
    84: {"w2": 2200, "p1": (213, 71), "p2": (178, 276)},
    85: {"w2": 2295, "p1": (229, 72), "p2": (202, 294)},
    86: {"w2": 2355, "p1": (245, 72), "p2": (228, 307)},
    87: {"w2": 2440, "p1": (262, 72), "p2": (255, 322)},
    88: {"w2": 2515, "p1": (277, 73), "p2": (285, 338)},
    89: {"w2": 2550, "p1": (293, 73), "p2": (315, 343)},
    90: {"w2": 2550, "p1": (308, 72), "p2": (345, 343)},
    91: {"w2": 2550, "p1": (323, 72), "p2": (374, 343)},
    92: {"w2": 2550, "p1": (340, 73), "p2": (404, 343)},
    93: {"w2": 2550, "p1": (355, 72), "p2": (435, 344)},
}

def chart_point(cg_in: float, weight_lb: float) -> tuple[float, float]:
    cg = float(cg_in)
    g0 = int(max(82, min(93, int(cg // 1))))
    g1 = int(max(82, min(93, g0 + 1)))
    if g1 == g0:
        g1 = g0

    def on_line(g: int, w: float):
        ref = CHART_REF[g]
        w1 = 1200.0
        w2 = float(ref["w2"])
        (x1, y1) = ref["p1"]
        (x2, y2) = ref["p2"]
        t = (float(w) - w1) / (w2 - w1) if (w2 - w1) != 0 else 0.0
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    p0 = on_line(g0, weight_lb)
    p1 = on_line(g1, weight_lb)
    if g1 == g0:
        return p0

    frac = (cg - g0) / (g1 - g0)
    return (p0[0] + frac * (p1[0] - p0[0]), p0[1] + frac * (p1[1] - p0[1]))

def chart_bottom_point(cg_in: float) -> tuple[float, float]:
    return chart_point(cg_in, 1200.0)

def make_chart_overlay(empty_cg, empty_w, to_cg, to_w, ldg_cg, ldg_w) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))

    def draw_state(color_rgb, cg, w):
        (xb, yb) = chart_bottom_point(cg)
        (x, y) = chart_point(cg, w)
        c.setStrokeColorRGB(*color_rgb)
        c.setLineWidth(2)
        c.line(xb, yb, x, y)
        c.setFillColorRGB(*color_rgb)
        c.circle(x, y, 5, stroke=1, fill=1)

    draw_state((0.12, 0.70, 0.20), empty_cg, empty_w)  # green
    draw_state((0.15, 0.35, 0.95), to_cg, to_w)        # blue
    draw_state((0.90, 0.20, 0.20), ldg_cg, ldg_w)      # red

    # legend
    lx, ly = 470, 520
    c.setFont("Helvetica", 10)
    c.setFillColor(colors.black)
    c.drawString(lx, ly + 78, "Legend")
    items = [
        ("Empty",   colors.Color(0.12, 0.70, 0.20)),
        ("Takeoff", colors.Color(0.15, 0.35, 0.95)),
        ("Landing", colors.Color(0.90, 0.20, 0.20)),
    ]
    yy = ly + 58
    for name, col in items:
        c.setFillColor(col)
        c.rect(lx, yy, 10, 10, stroke=0, fill=1)
        c.setFillColor(colors.black)
        c.drawString(lx + 16, yy + 1, name)
        yy -= 16

    c.showPage()
    c.save()
    return buf.getvalue()

# -----------------------------
# Runway selection by wind
# -----------------------------
def best_runway_index(ad: dict, wind_from_deg: int) -> int:
    rwys = ad.get("runways", []) or []
    if not rwys:
        return 0
    w = int(wind_from_deg or 0)
    w = 360 if w == 0 else w
    # Choose QFU closest to wind-from (max headwind)
    diffs = [ang_diff(float(r.get("qfu", 0.0)), float(w)) for r in rwys]
    return int(diffs.index(min(diffs)))

# -----------------------------
# Session defaults / forcing
# -----------------------------
LISBON = pytz.timezone("Europe/Lisbon")

DEFAULT_LEGS = [
    {"role": "Departure",   "icao": "LPSO"},
    {"role": "Arrival",     "icao": "LPSO"},
    {"role": "Alternate 1", "icao": "LPEV"},
    {"role": "Alternate 2", "icao": "LPCB"},
]

def ensure_state():
    st.session_state.setdefault("fleet_pa28", {})
    st.session_state.setdefault("fleet_loaded_pa28", False)

    st.session_state.setdefault("flight_date", dt.datetime.now(LISBON).date())
    st.session_state.setdefault("dep_time_utc", dt.time(19, 0))
    st.session_state.setdefault("arr_time_utc", dt.time(20, 0))

    # Force defaults once per app run (fix for old sessions not matching new defaults)
    if not st.session_state.get("_pa28_defaults_forced", False):
        st.session_state["legs4"] = [dict(x) for x in DEFAULT_LEGS]
        st.session_state["_pa28_defaults_forced"] = True
    else:
        # ensure structure exists
        if "legs4" not in st.session_state or len(st.session_state["legs4"]) != 4:
            st.session_state["legs4"] = [dict(x) for x in DEFAULT_LEGS]

    st.session_state.setdefault("forecast4", [None] * 4)
    st.session_state.setdefault("hours4", [[] for _ in range(4)])
    st.session_state.setdefault("hour_idx4", [None] * 4)
    st.session_state.setdefault("met4", [{"temp": 15, "qnh": 1013, "wind_dir": 240, "wind_kt": 8} for _ in range(4)])

    for i in range(4):
        st.session_state.setdefault(f"manual_{i}", False)

ensure_state()

# -----------------------------
# Load fleet from gist (once)
# -----------------------------
if not st.session_state.fleet_loaded_pa28:
    token = st.secrets.get("GITHUB_GIST_TOKEN", "")
    gist_id = st.secrets.get("GITHUB_GIST_ID_PA28", "")
    if token and gist_id:
        gdata, gerr = gist_load(token, gist_id)
        if gdata is not None:
            st.session_state.fleet_pa28 = gdata
        else:
            st.warning(f"PA-28 fleet gist not loaded: {gerr}")
    else:
        st.warning("Missing secrets: GITHUB_GIST_TOKEN and/or GITHUB_GIST_ID_PA28")
    st.session_state.fleet_loaded_pa28 = True

# -----------------------------
# Header + tabs
# -----------------------------
st.markdown('<div class="hdr">Piper PA-28 — Mass & Balance + Forecast + PDF</div>', unsafe_allow_html=True)

tab_flt, tab_air, tab_wb, tab_pdf = st.tabs(["Flight", "Airfields & Forecast", "Weight & Balance", "PDF"])

# -----------------------------
# FLIGHT TAB
# -----------------------------
with tab_flt:
    c1, c2, c3 = st.columns([0.45, 0.275, 0.275])

    with c1:
        st.write("**Flight date (Europe/Lisbon)**")
        st.session_state.flight_date = st.date_input(
            "Flight date (Europe/Lisbon)",
            value=st.session_state.flight_date,
            label_visibility="collapsed",
        )

    with c2:
        st.write("**Departure time (UTC)**")
        st.session_state.dep_time_utc = st.time_input(
            "Departure time (UTC)",
            value=st.session_state.dep_time_utc,
            step=3600,
            label_visibility="collapsed",
        )

    with c3:
        st.write("**Arrival time (UTC)**")
        st.session_state.arr_time_utc = st.time_input(
            "Arrival time (UTC)",
            value=st.session_state.arr_time_utc,
            step=3600,
            label_visibility="collapsed",
        )

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.subheader("Aircraft")

    regs = sorted(list((st.session_state.fleet_pa28 or {}).keys()))
    if not regs:
        st.error("No registrations found from the PA-28 gist (check token/id and file name).")
        regs = ["OE-KPD"]

    reg = st.selectbox("Registration", regs, key="selected_reg_pa28")
    st.session_state["reg_pa28"] = reg

    ew_lb = st.session_state.fleet_pa28.get(reg, {}).get("empty_weight_lb")
    em_inlb = st.session_state.fleet_pa28.get(reg, {}).get("empty_moment_inlb")

    cA, cB = st.columns(2)
    with cA:
        st.number_input("Basic empty weight (lb)", value=float(ew_lb) if ew_lb else 0.0, disabled=True)
        st.caption(f"({lbs_to_kg(float(ew_lb) if ew_lb else 0.0):.1f} kg)")
    with cB:
        st.number_input("Empty moment (in-lb)", value=float(em_inlb) if em_inlb else 0.0, disabled=True)
        st.caption("Moment units: in-lb")

# -----------------------------
# AIRFIELDS & FORECAST TAB
# -----------------------------
with tab_air:
    st.subheader("Approved Airfields (DEP / ARR / ALT1 / ALT2) + Forecast (Open-Meteo)")

    icao_options = sorted(AERODROMES_DB.keys())

    def leg_target_dt_utc(role: str) -> dt.datetime:
        # DEP uses dep time
        # ARR uses arrival time
        # ALT1/ALT2 use arrival time + 1h (as requested)
        if role == "Departure":
            t = st.session_state.dep_time_utc
            base = dt.datetime.combine(st.session_state.flight_date, t).replace(tzinfo=dt.timezone.utc)
            return base
        elif role == "Arrival":
            t = st.session_state.arr_time_utc
            base = dt.datetime.combine(st.session_state.flight_date, t).replace(tzinfo=dt.timezone.utc)
            return base
        else:
            t = st.session_state.arr_time_utc
            base = dt.datetime.combine(st.session_state.flight_date, t).replace(tzinfo=dt.timezone.utc)
            return base + dt.timedelta(hours=1)

    def fetch_leg(i: int):
        leg = st.session_state.legs4[i]
        icao = leg["icao"]
        ad = AERODROMES_DB[icao]

        start_iso = st.session_state.flight_date.strftime("%Y-%m-%d")
        end_iso = start_iso

        hourly = om_point_forecast(ad["lat"], ad["lon"], start_iso, end_iso)
        if "error" in hourly:
            st.error(f"{icao}: Forecast error: {hourly.get('error')} {hourly.get('detail','')}")
            return

        hours = om_hours(hourly)
        if not hours:
            st.error(f"{icao}: No forecast hours returned.")
            return

        target = leg_target_dt_utc(leg["role"])
        nearest_idx, nearest_time = min(hours, key=lambda h: abs(h[1] - target))

        met = om_at(hourly, nearest_idx)
        if not met:
            st.error(f"{icao}: Could not unpack forecast hour.")
            return

        st.session_state.forecast4[i] = hourly
        st.session_state.hours4[i] = hours
        st.session_state.hour_idx4[i] = nearest_idx

        # apply if not manual
        if not st.session_state.get(f"manual_{i}", False):
            st.session_state.met4[i]["temp"] = int(met["temp_c"])
            st.session_state.met4[i]["qnh"] = int(met["qnh_hpa"])
            st.session_state.met4[i]["wind_dir"] = int(met["wind_dir"])
            st.session_state.met4[i]["wind_kt"] = int(met["wind_kt"])

            # prepare widget keys
            st.session_state[f"temp_{i}"] = int(met["temp_c"])
            st.session_state[f"qnh_{i}"] = int(met["qnh_hpa"])
            st.session_state[f"wdir_{i}"] = int(met["wind_dir"])
            st.session_state[f"wspd_{i}"] = int(met["wind_kt"])

        st.success(f"{icao}: applied {nearest_time.strftime('%Y-%m-%d %H:00Z')} — Wind {met['wind_str']}")

    col_btn, _ = st.columns([0.25, 0.75])
    with col_btn:
        if st.button("Fetch forecast for all legs", type="primary"):
            for i in range(4):
                fetch_leg(i)

    st.markdown("<hr/>", unsafe_allow_html=True)

    for i, leg in enumerate(st.session_state.legs4):
        role = leg["role"]
        target = leg_target_dt_utc(role)

        st.markdown(f"### {role}")
        left, mid, right = st.columns([0.30, 0.18, 0.52])

        with left:
            icao = st.selectbox(
                "ICAO",
                options=icao_options,
                index=icao_options.index(leg["icao"]) if leg["icao"] in icao_options else 0,
                key=f"icao_{i}",
            )
            st.session_state.legs4[i]["icao"] = icao
            ad = AERODROMES_DB[icao]
            st.caption(f"{ad['name']} — Elev {ad['elev_ft']:.0f} ft")

        with mid:
            st.write("**Time used (UTC)**")
            st.code(target.strftime("%Y-%m-%d %H:00Z"))
            st.checkbox("Manual MET", key=f"manual_{i}")
            if st.button(f"Fetch forecast ({role})", key=f"fetch_{i}"):
                fetch_leg(i)

        with right:
            # Initialize widget keys as ints (IMPORTANT)
            st.session_state.setdefault(f"temp_{i}", int(st.session_state.met4[i]["temp"]))
            st.session_state.setdefault(f"qnh_{i}", int(st.session_state.met4[i]["qnh"]))
            st.session_state.setdefault(f"wdir_{i}", int(st.session_state.met4[i]["wind_dir"]))
            st.session_state.setdefault(f"wspd_{i}", int(st.session_state.met4[i]["wind_kt"]))

            cR1, cR2 = st.columns(2)
            with cR1:
                temp_c = int(st.number_input("OAT (°C)", value=int(st.session_state[f"temp_{i}"]), step=1, key=f"temp_{i}"))
                qnh = int(st.number_input("QNH (hPa)", min_value=900, max_value=1050, value=int(st.session_state[f"qnh_{i}"]), step=1, key=f"qnh_{i}"))
            with cR2:
                wdir_in = int(st.number_input("Wind FROM (°)", min_value=0, max_value=360, value=int(st.session_state[f"wdir_{i}"]), step=1, key=f"wdir_{i}"))
                wspd = int(st.number_input("Wind speed (kt)", min_value=0, max_value=200, value=int(st.session_state[f"wspd_{i}"]), step=1, key=f"wspd_{i}"))

            wdir10 = round_wind_dir_to_10(wdir_in)

            # store
            st.session_state.met4[i]["temp"] = temp_c
            st.session_state.met4[i]["qnh"] = qnh
            st.session_state.met4[i]["wind_dir"] = wdir10
            st.session_state.met4[i]["wind_kt"] = wspd

            st.markdown(
                f"<span class='pill'>Wind {fmt_wind(wdir10, wspd)}</span>"
                f"<span class='pill'>Temp {temp_c}°C</span>"
                f"<span class='pill'>QNH {qnh}</span>",
                unsafe_allow_html=True
            )

            # RUNWAY auto selection by wind (default selection index)
            rw_ids = [r["id"] for r in ad["runways"]]
            auto_idx = best_runway_index(ad, wdir10)

            # store auto choice (but allow user override)
            if f"rw_{i}" not in st.session_state:
                st.session_state[f"rw_{i}"] = rw_ids[auto_idx]

            rw_id = st.selectbox("Runway (auto by wind)", rw_ids, index=rw_ids.index(st.session_state[f"rw_{i}"]), key=f"rw_{i}")
            rw = next(r for r in ad["runways"] if r["id"] == rw_id)

            st.session_state.setdefault(f"toda_{i}", float(rw["toda"]))
            st.session_state.setdefault(f"lda_{i}", float(rw["lda"]))

            toda = st.number_input("TODA (m)", min_value=0.0, value=float(st.session_state[f"toda_{i}"]), step=1.0, key=f"toda_{i}")
            lda  = st.number_input("LDA (m)",  min_value=0.0, value=float(st.session_state[f"lda_{i}"]),  step=1.0, key=f"lda_{i}")

            st.session_state[f"qfu_{i}"] = float(rw.get("qfu", 0.0))

    # Prepare perf rows for PDF
    perf_rows = []
    for i, leg in enumerate(st.session_state.legs4):
        icao = leg["icao"]
        ad = AERODROMES_DB[icao]
        qfu = float(st.session_state.get(f"qfu_{i}", ad["runways"][0]["qfu"]))
        toda = float(st.session_state.get(f"toda_{i}", ad["runways"][0]["toda"]))
        lda  = float(st.session_state.get(f"lda_{i}", ad["runways"][0]["lda"]))

        met = st.session_state.met4[i]
        elev_ft = float(ad["elev_ft"])
        qnh = float(met["qnh"])
        temp = float(met["temp"])

        pa_ft = elev_ft + (1013.0 - qnh) * 30.0
        isa_temp = 15.0 - 2.0 * (elev_ft / 1000.0)
        da_ft = pa_ft + (120.0 * (temp - isa_temp))

        st.session_state.setdefault(f"todr_{i}", 0.0)
        st.session_state.setdefault(f"ldr_{i}", 0.0)
        st.session_state.setdefault(f"roc_{i}", 0.0)

        perf_rows.append({
            "role": leg["role"],
            "icao": icao,
            "qfu": qfu,
            "elev_ft": elev_ft,
            "qnh": int(round(qnh)),
            "temp": int(round(temp)),
            "wind_dir": int(met["wind_dir"]),
            "wind_kt": int(met["wind_kt"]),
            "pa_ft": int(round(pa_ft)),
            "da_ft": int(round(da_ft)),
            "toda": int(round(toda)),
            "lda": int(round(lda)),
            "todr": int(round(float(st.session_state.get(f"todr_{i}", 0.0)))),
            "ldr":  int(round(float(st.session_state.get(f"ldr_{i}", 0.0)))),
            "roc":  int(round(float(st.session_state.get(f"roc_{i}", 0.0)))),
        })

    st.session_state["_perf4"] = perf_rows

# -----------------------------
# WEIGHT & BALANCE TAB
# -----------------------------
with tab_wb:
    st.subheader("Weight & Balance (lbs / USG)")

    reg = st.session_state.get("reg_pa28", "")
    ew_lb = float(st.session_state.fleet_pa28.get(reg, {}).get("empty_weight_lb") or 0.0)
    em_inlb = float(st.session_state.fleet_pa28.get(reg, {}).get("empty_moment_inlb") or 0.0)

    if ew_lb <= 0 or em_inlb <= 0:
        st.error("Empty weight / moment missing for this registration in the gist.")
        st.stop()

    c1, c2, c3 = st.columns([0.38, 0.31, 0.31])
    with c1:
        st.markdown("#### Loads")
        front_lb = st.number_input("Pilot + front passenger (lb)", min_value=0.0, value=170.0, step=1.0)
        rear_lb  = st.number_input("Rear seats (lb)", min_value=0.0, value=0.0, step=1.0)
        bag_lb   = st.number_input("Baggage (lb)", min_value=0.0, value=0.0, step=1.0)
        fuel_usg = st.number_input("Fuel (USG)", min_value=0.0, max_value=float(PA28["max_fuel_usg"]), value=0.0, step=0.5)
        st.caption(f"Fuel: {fuel_usg:.1f} USG ({usg_to_l(fuel_usg):.1f} L)")

    with c2:
        st.markdown("#### Arms (in)")
        arm_front = st.number_input("Front seats arm (in)", value=float(PA28["arm_front_in"]), step=0.1)
        arm_rear  = st.number_input("Rear seats arm (in)",  value=float(PA28["arm_rear_in"]), step=0.1)
        arm_fuel  = st.number_input("Fuel arm (in)",        value=float(PA28["arm_fuel_in"]), step=0.1)
        arm_bag   = st.number_input("Baggage arm (in)",     value=float(PA28["arm_baggage_in"]), step=0.1)

    with c3:
        st.markdown("#### Fuel planning (10 USG/h)")
        GPH = st.number_input("Fuel flow (USG/h)", min_value=5.0, max_value=20.0, value=10.0, step=0.5)
        taxi_min  = st.number_input("Start-up & taxi (min)", min_value=0, value=15, step=1)
        climb_min = st.number_input("Climb (min)", min_value=0, value=10, step=1)
        enrt_h = st.number_input("Enroute (h)", min_value=0, value=1, step=1)
        enrt_m = st.number_input("Enroute (min)", min_value=0, value=0, step=5)
        desc_min = st.number_input("Descent (min)", min_value=0, value=10, step=1)
        alt_min = st.number_input("Alternate (min)", min_value=0, value=45, step=5)
        reserve_min = 45

        def usg_from_min(m): return round(GPH * (float(m) / 60.0), 2)

        enrt_min = int(enrt_h) * 60 + int(enrt_m)
        trip_min = int(climb_min) + int(enrt_min) + int(desc_min)
        trip_usg = usg_from_min(trip_min)
        cont_usg = round(0.05 * trip_usg, 2)
        cont_min = int(round(0.05 * trip_min))

        taxi_usg  = usg_from_min(taxi_min)
        alt_usg   = usg_from_min(alt_min)
        res_usg   = usg_from_min(reserve_min)

        req_usg = round(taxi_usg + trip_usg + cont_usg + alt_usg + res_usg, 2)
        extra_usg = max(0.0, round(fuel_usg - req_usg, 2))

        st.write(f"Required ramp fuel: **{req_usg:.2f} USG** ({usg_to_l(req_usg):.1f} L)")
        if fuel_usg < req_usg:
            st.error(f"Fuel loaded is insufficient: {fuel_usg:.2f} < {req_usg:.2f} USG")

    # W&B computations
    fuel_lb = fuel_usg * PA28["fuel_density_lb_per_usg"]

    m_empty = em_inlb
    m_front = front_lb * arm_front
    m_rear  = rear_lb * arm_rear
    m_bag   = bag_lb * arm_bag
    m_fuel  = fuel_lb * arm_fuel

    ramp_w = ew_lb + front_lb + rear_lb + bag_lb + fuel_lb
    ramp_m = m_empty + m_front + m_rear + m_bag + m_fuel
    ramp_cg = (ramp_m / ramp_w) if ramp_w > 0 else 0.0

    takeoff_w = ramp_w + PA28["taxi_allowance_lb"]
    takeoff_m = ramp_m + PA28["taxi_allowance_moment_inlb"]
    takeoff_cg = (takeoff_m / takeoff_w) if takeoff_w > 0 else 0.0

    # Landing = takeoff minus TRIP burn only
    trip_burn_lb = trip_usg * PA28["fuel_density_lb_per_usg"]
    landing_w = takeoff_w - trip_burn_lb
    landing_m = takeoff_m - (trip_burn_lb * arm_fuel)
    landing_cg = (landing_m / landing_w) if landing_w > 0 else 0.0

    st.markdown("<hr/>", unsafe_allow_html=True)
    s1, s2, s3, s4 = st.columns(4)
    with s1:
        st.write("**Ramp**")
        st.write(f"{ramp_w:.0f} lb ({lbs_to_kg(ramp_w):.1f} kg)")
        st.write(f"CG {ramp_cg:.2f} in")
    with s2:
        st.write("**Takeoff**")
        st.write(f"{takeoff_w:.0f} lb ({lbs_to_kg(takeoff_w):.1f} kg)")
        st.write(f"CG {takeoff_cg:.2f} in")
    with s3:
        st.write("**Landing**")
        st.write(f"{landing_w:.0f} lb ({lbs_to_kg(landing_w):.1f} kg)")
        st.write(f"CG {landing_cg:.2f} in")
    with s4:
        st.write("**Limits**")
        st.write(f"MTOW {PA28['mtow_lb']:.0f} lb " + ("✅" if takeoff_w <= PA28["mtow_lb"] else "⚠️"))

    st.session_state["_wb_pa28"] = {
        "ew_lb": ew_lb, "em_inlb": em_inlb,
        "front_lb": front_lb, "rear_lb": rear_lb, "bag_lb": bag_lb,
        "fuel_usg": fuel_usg, "fuel_lb": fuel_lb,
        "arm_front": arm_front, "arm_rear": arm_rear, "arm_fuel": arm_fuel, "arm_bag": arm_bag,
        "ramp_w": ramp_w, "ramp_m": ramp_m, "ramp_cg": ramp_cg,
        "takeoff_w": takeoff_w, "takeoff_m": takeoff_m, "takeoff_cg": takeoff_cg,
        "landing_w": landing_w, "landing_m": landing_m, "landing_cg": landing_cg,
        "fuel_flow_gph": GPH,
        "fuel_plan": {
            "taxi_min": int(taxi_min), "taxi_usg": float(taxi_usg),
            "climb_min": int(climb_min),
            "enrt_min": int(enrt_min),
            "desc_min": int(desc_min),
            "trip_min": int(trip_min), "trip_usg": float(trip_usg),
            "cont_min": int(cont_min), "cont_usg": float(cont_usg),
            "alt_min": int(alt_min), "alt_usg": float(alt_usg),
            "res_min": int(reserve_min), "res_usg": float(res_usg),
            "req_usg": float(req_usg),
            "extra_usg": float(extra_usg),
        }
    }

# -----------------------------
# PDF TAB
# -----------------------------
with tab_pdf:
    st.subheader("PDF — Fill + CG chart overlay")

    template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
    field_names = get_field_names(template_bytes)

    with st.expander("Field names", expanded=False):
        st.write(field_names)

    wb = st.session_state.get("_wb_pa28", {})
    perf = st.session_state.get("_perf4", []) or []

    if not wb:
        st.warning("Open 'Weight & Balance' first.")
        st.stop()

    f = {}

    # Date + registration
    date_str = st.session_state.flight_date.strftime("%d/%m/%Y")
    reg = st.session_state.get("reg_pa28", "")
    f["Date"] = date_str
    f["Aircraft_Reg"] = reg

    def w_with_kg(lb):
        return f"{lb:.0f} ({lbs_to_kg(lb):.0f} kg)"

    def fuel_with_l(usg):
        return f"{usg:.1f} ({usg_to_l(usg):.0f} L)"

    # Basic empty
    f["Weight_EMPTY"] = w_with_kg(wb["ew_lb"])
    f["Moment_EMPTY"] = f"{wb['em_inlb']:.0f}"
    f["Datum_EMPTY"]  = f"{(wb['em_inlb']/wb['ew_lb']):.1f}"

    # Stations
    f["Weight_FRONT"] = w_with_kg(wb["front_lb"])
    f["Moment_FRONT"] = f"{(wb['front_lb']*wb['arm_front']):.0f}"

    f["Weight_REAR"]  = w_with_kg(wb["rear_lb"])
    f["Moment_REAR"]  = f"{(wb['rear_lb']*wb['arm_rear']):.0f}"

    f["Weight_BAGGAGE"] = w_with_kg(wb["bag_lb"])
    f["Moment_BAGGAGE"] = f"{(wb['bag_lb']*wb['arm_bag']):.0f}"

    f["Weight_FUEL"]  = f"{wb['fuel_lb']:.0f} lb ({lbs_to_kg(wb['fuel_lb']):.0f} kg) / {fuel_with_l(wb['fuel_usg'])} USG"
    f["Moment_FUEL"]  = f"{(wb['fuel_lb']*wb['arm_fuel']):.0f}"

    # Totals
    f["Weight_RAMP"] = w_with_kg(wb["ramp_w"])
    f["Moment_RAMP"] = f"{wb['ramp_m']:.0f}"
    f["Datum_RAMP"]  = f"{wb['ramp_cg']:.1f}"

    f["Weight_TAKEOFF"] = w_with_kg(wb["takeoff_w"])
    f["Moment_TAKEOFF"] = f"{wb['takeoff_m']:.0f}"
    f["Datum_TAKEOFF"]  = f"{wb['takeoff_cg']:.1f}"

    # Fuel planning cells (USG + L)
    plan = wb["fuel_plan"]
    def fuel_cell(usg): return f"{usg:.2f} ({usg_to_l(usg):.0f} L)"

    f["Start-up_and_Taxi_TIME"] = fmt_hm(plan["taxi_min"])
    f["Start-up_and_Taxi_FUEL"] = fuel_cell(plan["taxi_usg"])
    f["TRIP_TIME"] = fmt_hm(plan["trip_min"])
    f["TRIP_FUEL"] = fuel_cell(plan["trip_usg"])
    f["Contingency_TIME"] = fmt_hm(plan["cont_min"])
    f["Contingency_FUEL"] = fuel_cell(plan["cont_usg"])
    f["ALTERNATE_TIME"] = fmt_hm(plan["alt_min"])
    f["ALTERNATE_FUEL"] = fuel_cell(plan["alt_usg"])
    f["RESERVE_TIME"] = fmt_hm(plan["res_min"])
    f["RESERVE_FUEL"] = fuel_cell(plan["res_usg"])
    f["REQUIRED_FUEL"] = fuel_cell(plan["req_usg"])
    f["EXTRA_FUEL"] = fuel_cell(plan["extra_usg"])
    f["Total_FUEL"] = fuel_cell(plan["req_usg"] + plan["extra_usg"])

    # Airfields (page 2)
    role_to_suffix = {
        "Departure": "DEPARTURE",
        "Arrival": "ARRIVAL",
        "Alternate 1": "ALTERNATE_1",
        "Alternate 2": "ALTERNATE_2",
    }
    by_role = {r["role"]: r for r in perf} if perf else {}

    for role, suf in role_to_suffix.items():
        r = by_role.get(role)
        if not r:
            continue
        f[f"Airfield_{suf}"] = r["icao"]
        f[f"RWY_{suf}"] = f"{int(round(r['qfu'])):03d}"
        f[f"Elevation_{suf}"] = f"{int(round(r['elev_ft']))}"
        f[f"QNH_{suf}"] = f"{int(r['qnh'])}"
        f[f"Temperature_{suf}"] = f"{int(r['temp'])}"
        f[f"Wind_{suf}"] = f"{fmt_wind(r['wind_dir'], r['wind_kt'])} kt"
        f[f"Pressure_Alt _{suf}"] = f"{int(r['pa_ft'])}"
        f[f"Density_Alt_{suf}"] = f"{int(r['da_ft'])}"
        f[f"TODA_{suf}"] = f"{int(r['toda'])}"
        f[f"LDA_{suf}"]  = f"{int(r['lda'])}"
        f[f"TODR_{suf}"] = f"{int(r['todr'])}"
        f[f"LDR_{suf}"]  = f"{int(r['ldr'])}"
        f[f"ROC_{suf}"]  = f"{int(r['roc'])}"

    # Chart overlay (3 states)
    overlay = make_chart_overlay(
        empty_cg=(wb["em_inlb"]/wb["ew_lb"]),
        empty_w=wb["ew_lb"],
        to_cg=wb["takeoff_cg"],
        to_w=wb["takeoff_w"],
        ldg_cg=wb["landing_cg"],
        ldg_w=wb["landing_w"],
    )

    if st.button("Generate filled PDF", type="primary"):
        out_bytes = fill_pdf(template_bytes, f, overlay_first_page_bytes=overlay)
        mission = ascii_safe(reg).strip().replace(" ", "_") or "PA28"
        file_name = f"{mission}_PA28_MB_Perf.pdf"
        st.download_button("Download PDF", data=out_bytes, file_name=file_name, mime="application/pdf")
        st.success("PDF generated.")

