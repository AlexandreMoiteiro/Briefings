# Streamlit app — Piper PA-28 (W&B + Forecast + PDF) — clean v2
# Fixes:
# - Default legs: LPSO / LPSO / LPEV / LPCB
# - Load fleet from GitHub Gist file: sevenair_pa28_fleet.json
# - Display & write units: lb (kg) and US gal (L)
# - Fuel consumption fixed at 10 US gal/h
#
# Requirements:
#   streamlit
#   requests
#   pypdf>=4.2.0
#   reportlab
#   pytz

import io
import json
import math
import datetime as dt
from pathlib import Path
from typing import Dict, Optional, Tuple, List

import pytz
import requests
import streamlit as st

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas


# ============================================================
# CONFIG
# ============================================================
PDF_TEMPLATE = "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
LISBON_TZ = pytz.timezone("Europe/Lisbon")

WIND_WINDOW_H = 1  # fixed ±1h

# Your gist file name (from your screenshot)
GIST_FILE = "sevenair_pa28_fleet.json"

# Fuel policy
FUEL_BURN_GPH = 10.0  # fixed 10 US gal/h


# ============================================================
# UI STYLE
# ============================================================
st.set_page_config(page_title="PA-28 — Mass & Balance + PDF", layout="wide", initial_sidebar_state="collapsed")
st.markdown(
    """
    <style>
      .block-container { max-width: 1250px !important; }
      .hdr{font-size:1.25rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:8px;margin:2px 0 14px}
      .chip{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef2f7;margin-left:8px;font-size:.85rem}
      .mb-table{border-collapse:collapse;width:100%;font-size:.92rem}
      .mb-table th{border-bottom:2px solid #cbd0d6;text-align:left}
      .mb-table td{padding:3px 6px;border-bottom:1px dashed #e5e7ec;vertical-align:top}
      .hint{font-size:.85rem;color:#6b7280}
      .ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown('<div class="hdr">Piper PA-28 — Mass & Balance + Forecast + PDF</div>', unsafe_allow_html=True)


# ============================================================
# UNITS
# ============================================================
KG_TO_LB = 2.2046226218
LB_TO_KG = 1.0 / KG_TO_LB

L_TO_USGAL = 0.2641720524
USGAL_TO_L = 1.0 / L_TO_USGAL

def fmt_lb_kg(lb: float, nd_lb=0, nd_kg=0) -> str:
    kg = lb * LB_TO_KG
    return f"{lb:.{nd_lb}f} ({kg:.{nd_kg}f})"

def fmt_gal_l(gal: float, nd_gal=1, nd_l=1) -> str:
    l = gal * USGAL_TO_L
    return f"{gal:.{nd_gal}f} ({l:.{nd_l}f})"

def fmt_int_lb_kg(lb: float) -> str:
    kg = lb * LB_TO_KG
    return f"{int(round(lb))} ({int(round(kg))})"

def fmt_int_gal_l(gal: float) -> str:
    l = gal * USGAL_TO_L
    return f"{int(round(gal))} ({int(round(l))})"


# ============================================================
# APPROVED AIRFIELDS DB (copied from your Tecnam app)
# ============================================================
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
    "LPSO": {"name": "Ponte de Sôr", "lat": 39.2117, "lon": -8.0578, "elev_ft": 390.0,
             "runways": [{"id": "03", "qfu": 30.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True},
                         {"id": "21", "qfu": 210.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True}]},
    "LPMT": {"name": "Montijo", "lat": 38.7039, "lon": -9.0350, "elev_ft": 46.0,
             "runways": [{"id": "07", "qfu": 70.0, "toda": 2448.0, "lda": 2448.0, "slope_pc": 0.0, "paved": True},
                         {"id": "25", "qfu": 250.0, "toda": 2448.0, "lda": 2448.0, "slope_pc": 0.0, "paved": True},
                         {"id": "01", "qfu": 10.0, "toda": 2187.0, "lda": 2187.0, "slope_pc": 0.0, "paved": True},
                         {"id": "19", "qfu": 190.0, "toda": 2187.0, "lda": 2187.0, "slope_pc": 0.0, "paved": True}]},
    "LPFR": {"name": "Faro", "lat": 37.0144, "lon": -7.9658, "elev_ft": 24.0,
             "runways": [{"id": "10", "qfu": 100.0, "toda": 2490.0, "lda": 2490.0, "slope_pc": 0.0, "paved": True},
                         {"id": "28", "qfu": 280.0, "toda": 2490.0, "lda": 2490.0, "slope_pc": 0.0, "paved": True}]},
}


# ============================================================
# GIST — fleet load
# ============================================================
def gist_headers(token: str) -> dict:
    # Fine-grained PATs work with Bearer
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gist_load_fleet(token: str, gist_id: str) -> Tuple[Optional[dict], Optional[str]]:
    try:
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(token), timeout=20)
        if r.status_code != 200:
            return None, f"GitHub error {r.status_code}: {r.text}"
        data = r.json()
        files = data.get("files", {})
        if GIST_FILE not in files:
            return None, f"Gist does not contain '{GIST_FILE}'. Found: {list(files.keys())}"
        content = files[GIST_FILE].get("content")
        if not content:
            return None, f"'{GIST_FILE}' is empty."
        return json.loads(content), None
    except Exception as e:
        return None, str(e)


# ============================================================
# FORECAST (Open-Meteo)
# ============================================================
def _utc_hour(dtu: dt.datetime) -> dt.datetime:
    return dtu.replace(minute=0, second=0, microsecond=0, tzinfo=dt.timezone.utc)

def round_dir_10(deg: float) -> int:
    d = int(round(deg / 10.0) * 10) % 360
    return 360 if d == 0 else d

def fmt_wind(dir_deg: int, spd_kt: int) -> str:
    return f"{dir_deg:03d}/{spd_kt:02d}"

@st.cache_data(ttl=900, show_spinner=False)
def om_hourly(lat: float, lon: float, start_date_iso: str, end_date_iso: str) -> dict:
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
    r = requests.get(OPENMETEO_URL, params=params, timeout=25)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "detail": r.text, "params": params}
    data = r.json()
    h = data.get("hourly", {})

    times = h.get("time", []) or []
    wspd = h.get("wind_speed_10m", []) or []
    wdir = h.get("wind_direction_10m", []) or []
    temp = h.get("temperature_2m", []) or []
    qnh  = h.get("pressure_msl", []) or []

    rows = []
    for i, t in enumerate(times):
        dtu = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        rows.append({
            "dt": dtu,
            "wind_kt": float(wspd[i]) if i < len(wspd) and wspd[i] is not None else 0.0,
            "wind_dir": float(wdir[i]) if i < len(wdir) and wdir[i] is not None else 0.0,
            "temp_c": float(temp[i]) if i < len(temp) and temp[i] is not None else None,
            "qnh_hpa": float(qnh[i]) if i < len(qnh) and qnh[i] is not None else None,
        })
    return {"hours": rows, "params": params}

def pick_samples(hours: List[dict], target_utc: dt.datetime, window_h: int = 1) -> List[dict]:
    lo = target_utc - dt.timedelta(hours=window_h)
    hi = target_utc + dt.timedelta(hours=window_h)
    return [h for h in hours if lo <= h["dt"] <= hi]

def vector_mean_wind(samples: List[dict]) -> Tuple[int, int]:
    if not samples:
        return 0, 0
    u_sum, v_sum = 0.0, 0.0
    for s in samples:
        spd = float(s["wind_kt"])
        dir_from = float(s["wind_dir"]) % 360.0
        th = math.radians(dir_from)
        u_sum += -spd * math.sin(th)
        v_sum += -spd * math.cos(th)
    u = u_sum / len(samples)
    v = v_sum / len(samples)
    spd = math.sqrt(u*u + v*v)
    dir_from = (math.degrees(math.atan2(u, v)) + 180.0) % 360.0
    return round_dir_10(dir_from), int(round(spd))

def nearest_hour(hours: List[dict], target_utc: dt.datetime) -> Optional[dict]:
    if not hours:
        return None
    return min(hours, key=lambda h: abs(h["dt"] - target_utc))


# ============================================================
# W&B math
# ============================================================
def compute_total(weight_by_station_lb: Dict[str, float], arm_by_station_in: Dict[str, float]) -> Tuple[float, float, float]:
    w = sum(weight_by_station_lb.values())
    m = sum(float(weight_by_station_lb[k]) * float(arm_by_station_in[k]) for k in weight_by_station_lb.keys())
    cg = (m / w) if w > 0 else 0.0
    return w, m, cg


# ============================================================
# CG CHART mapping (your corrected points)
# ============================================================
Y_BY_WEIGHT = [
    (1200, 72),
    (2050, 245),
    (2200, 276),
    (2295, 294),
    (2355, 307),
    (2440, 322),
    (2515, 338),
    (2550, 343),
]
X_AT = {
    (82, 1200): 182, (82, 2050): 134,
    (83, 1200): 199, (83, 2138): 155,
    (84, 1200): 213, (84, 2200): 178,
    (85, 1200): 229,
    (86, 1200): 245,
    (87, 1200): 262,
    (88, 1200): 277,
    (85, 2295): 202,
    (86, 2355): 228,
    (87, 2440): 255,
    (88, 2515): 285,
    (89, 1200): 293, (89, 2550): 315,
    (90, 1200): 308, (90, 2550): 345,
    (91, 1200): 323, (91, 2550): 374,
    (92, 1200): 340, (92, 2550): 404,
    (93, 1200): 355, (93, 2550): 435,
}

def clamp(v, lo, hi): return max(lo, min(hi, v))

def lerp(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def interp_1d(x, pts):
    pts = sorted(pts, key=lambda p: p[0])
    x = clamp(float(x), pts[0][0], pts[-1][0])
    for i in range(len(pts)-1):
        x0, y0 = pts[i]
        x1, y1 = pts[i+1]
        if x0 <= x <= x1:
            return lerp(x, x0, x1, y0, y1)
    return pts[-1][1]

def y_from_weight(w): return float(interp_1d(w, Y_BY_WEIGHT))

def build_cg_line(cg_int: int):
    y0 = y_from_weight(1200)
    y1 = y_from_weight(2550)
    x0 = float(X_AT[(cg_int, 1200)])
    if (cg_int, 2550) in X_AT:
        x1 = float(X_AT[(cg_int, 2550)])
        return (x0, y0), (x1, y1)

    cands = [w for (cg, w) in X_AT.keys() if cg == cg_int and w != 1200]
    w_mid = max(cands)
    x_mid = float(X_AT[(cg_int, w_mid)])
    y_mid = y_from_weight(w_mid)
    slope = 0.0 if y_mid == y0 else (x_mid - x0) / (y_mid - y0)
    x1 = x0 + slope * (y1 - y0)
    return (x0, y0), (x1, y1)

CG_LINES = {cg: build_cg_line(cg) for cg in range(82, 94)}

def x_on_line(cg_int: int, y: float) -> float:
    (x0, y0), (x1, y1) = CG_LINES[cg_int]
    if y1 == y0:
        return x0
    t = (y - y0) / (y1 - y0)
    return x0 + t * (x1 - x0)

def cg_wt_to_xy(cg_in: float, wt_lb: float) -> Tuple[float, float]:
    y = y_from_weight(wt_lb)
    cg_in = clamp(float(cg_in), 82.0, 93.0)
    c0 = int(math.floor(cg_in))
    c1 = min(93, c0 + 1)
    if c0 < 82:
        c0, c1 = 82, 83
    x0 = x_on_line(c0, y)
    x1 = x_on_line(c1, y)
    x = x0 if c0 == c1 else lerp(cg_in, c0, c1, x0, x1)
    return float(x), float(y)


# ============================================================
# PDF helpers (Tecnam-style)
# ============================================================
def read_pdf_bytes() -> bytes:
    p = Path(PDF_TEMPLATE)
    if not p.exists():
        raise FileNotFoundError(f"PDF template not found: {PDF_TEMPLATE}")
    return p.read_bytes()

def get_field_names(template_bytes: bytes) -> List[str]:
    names = set()
    reader = PdfReader(io.BytesIO(template_bytes))
    try:
        fd = reader.get_fields()
        if fd:
            names.update(fd.keys())
    except Exception:
        pass
    try:
        for page in reader.pages:
            if "/Annots" in page:
                for a in page["/Annots"]:
                    obj = a.get_object()
                    if obj.get("/T"):
                        names.add(str(obj["/T"]))
    except Exception:
        pass
    return sorted(names)

def fill_pdf_writer(template_bytes: bytes, fields: dict) -> Tuple[PdfWriter, PdfReader]:
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

    return writer, reader

def make_chart_overlay(page_w: float, page_h: float, points: List[dict], legend_xy=(500, 320), marker_r=4) -> bytes:
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=(page_w, page_h))

    for p in points:
        x, y = cg_wt_to_xy(p["cg"], p["wt"])
        r, g, b = p["rgb"]
        c.setFillColorRGB(r, g, b)
        c.circle(x, y, marker_r, fill=1, stroke=0)

    lx, ly = legend_xy
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(lx, ly, "Legend")
    ly -= 14
    c.setFont("Helvetica", 9)
    for p in points:
        r, g, b = p["rgb"]
        c.setFillColorRGB(r, g, b)
        c.rect(lx, ly - 7, 10, 10, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(lx + 14, ly - 5, p["label"])
        ly -= 14

    c.showPage()
    c.save()
    bio.seek(0)
    return bio.read()


# ============================================================
# SESSION INIT
# ============================================================
if "fleet" not in st.session_state:
    st.session_state.fleet = {}  # will load from gist

if "fleet_loaded" not in st.session_state:
    st.session_state.fleet_loaded = False

if not st.session_state.fleet_loaded:
    token = st.secrets.get("GITHUB_GIST_TOKEN", "")
    gist_id = st.secrets.get("GITHUB_GIST_ID_PA28", "")
    if token and gist_id:
        gdata, gerr = gist_load_fleet(token, gist_id)
        if gdata is not None:
            st.session_state.fleet = gdata
        else:
            st.session_state["_gist_err"] = gerr
            st.session_state.fleet = {}
    else:
        st.session_state["_gist_err"] = "Missing GITHUB_GIST_TOKEN or GITHUB_GIST_ID_PA28 in Streamlit secrets."
        st.session_state.fleet = {}
    st.session_state.fleet_loaded = True

if "flight_date" not in st.session_state:
    st.session_state.flight_date = dt.datetime.now(LISBON_TZ).date()

if "dep_time_utc" not in st.session_state:
    st.session_state.dep_time_utc = (dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)).time()

if "arr_time_utc" not in st.session_state:
    st.session_state.arr_time_utc = (dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=2)).time()

# Correct defaults (as you requested)
DEFAULT_LEGS = [
    {"role": "Departure",   "icao": "LPSO"},
    {"role": "Arrival",     "icao": "LPSO"},
    {"role": "Alternate 1", "icao": "LPEV"},
    {"role": "Alternate 2", "icao": "LPCB"},
]
if "legs" not in st.session_state:
    st.session_state.legs = [dict(x) for x in DEFAULT_LEGS]
else:
    # if user had old legs saved, keep them; but if empty, reset
    if not st.session_state.legs or len(st.session_state.legs) != 4:
        st.session_state.legs = [dict(x) for x in DEFAULT_LEGS]

# MET widget keys
for i in range(4):
    st.session_state.setdefault(f"temp_{i}", 15)
    st.session_state.setdefault(f"qnh_{i}", 1013)
    st.session_state.setdefault(f"wdir_{i}", 240)
    st.session_state.setdefault(f"wspd_{i}", 8)
    st.session_state.setdefault(f"manual_{i}", False)

# W&B defaults (inputs in kg and liters)
st.session_state.setdefault("front_kg", 80.0)
st.session_state.setdefault("rear_kg", 0.0)
st.session_state.setdefault("bag_kg", 5.0)
st.session_state.setdefault("fuel_l_to", 80.0)
st.session_state.setdefault("enrt_min", 60)
st.session_state.setdefault("taxi_min", 10)

# Arms defaults (inches)
st.session_state.setdefault("arm_front", 80.5)
st.session_state.setdefault("arm_rear", 118.1)
st.session_state.setdefault("arm_fuel", 95.0)
st.session_state.setdefault("arm_bag", 142.8)


# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.subheader("Fleet (PA-28)")
    if st.session_state.get("_gist_err"):
        st.warning(st.session_state["_gist_err"])
    else:
        st.caption(f"Loaded from Gist: {GIST_FILE}")
        st.caption(f"Registrations: {len(st.session_state.fleet)}")

    st.markdown("---")
    st.subheader("Fuel")
    st.write(f"Fuel burn fixed: **{FUEL_BURN_GPH:.0f} US gal/h**")


# ============================================================
# FORECAST CALLBACKS
# ============================================================
def fetch_forecast_for_leg(i: int):
    icao = st.session_state[f"icao_{i}"]
    ad = AERODROMES_DB[icao]
    flight_date = st.session_state.flight_date
    start_iso = flight_date.strftime("%Y-%m-%d")
    end_iso = start_iso

    dep_dt = dt.datetime.combine(flight_date, st.session_state.dep_time_utc).replace(tzinfo=dt.timezone.utc)
    arr_dt = dt.datetime.combine(flight_date, st.session_state.arr_time_utc).replace(tzinfo=dt.timezone.utc)
    target = dep_dt if i == 0 else arr_dt
    target = _utc_hour(target)

    resp = om_hourly(ad["lat"], ad["lon"], start_iso, end_iso)
    if "error" in resp:
        st.session_state["_fetch_msg"] = f"{icao}: {resp['error']}"
        st.session_state["_fetch_ok"] = False
        return

    hours = resp["hours"]
    samples = pick_samples(hours, target, window_h=WIND_WINDOW_H)
    if not samples:
        near = nearest_hour(hours, target)
        samples = [near] if near else []

    wdir10, wspd = vector_mean_wind(samples)
    near = nearest_hour(hours, target)

    if near and near.get("temp_c") is not None:
        st.session_state[f"temp_{i}"] = int(round(near["temp_c"]))
    if near and near.get("qnh_hpa") is not None:
        st.session_state[f"qnh_{i}"] = int(round(near["qnh_hpa"]))

    st.session_state[f"wdir_{i}"] = int(wdir10)
    st.session_state[f"wspd_{i}"] = int(wspd)

    st.session_state["_fetch_msg"] = f"{icao}: wind {fmt_wind(int(wdir10), int(wspd))}"
    st.session_state["_fetch_ok"] = True

def fetch_forecast_all():
    ok = 0
    err = 0
    for i in range(4):
        if st.session_state.get(f"manual_{i}", False):
            continue
        fetch_forecast_for_leg(i)
        if st.session_state.get("_fetch_ok"):
            ok += 1
        else:
            err += 1
    st.session_state["_fetch_msg_all"] = f"Updated {ok} legs, {err} errors."
    st.session_state["_fetch_ok_all"] = (err == 0)


# ============================================================
# TABS
# ============================================================
tab1, tab2, tab3, tab4 = st.tabs(["Flight", "Airfields & Forecast", "Weight & Balance", "PDF"])


# ----------------------------
# TAB 1 — Flight
# ----------------------------
with tab1:
    c1, c2, c3 = st.columns([0.34, 0.33, 0.33])
    with c1:
        st.session_state.flight_date = st.date_input("Flight date (Europe/Lisbon)", value=st.session_state.flight_date)
    with c2:
        st.session_state.dep_time_utc = st.time_input("Departure time (UTC)", value=st.session_state.dep_time_utc, step=3600)
    with c3:
        st.session_state.arr_time_utc = st.time_input("Arrival time (UTC)", value=st.session_state.arr_time_utc, step=3600)

    st.markdown("### Aircraft")

    regs = sorted(list(st.session_state.fleet.keys())) if st.session_state.fleet else ["(no fleet loaded)"]
    reg = st.selectbox("Registration", regs, key="selected_reg")
    st.session_state["reg"] = reg

    ac = st.session_state.fleet.get(reg, {}) if st.session_state.fleet else {}

    # Your gist provides empty_weight_lb + empty_moment_inlb (per your screenshot)
    ew_lb_default = ac.get("empty_weight_lb", None)
    em_inlb_default = ac.get("empty_moment_inlb", None)

    st.markdown("<span class='hint'>Empty values come from Gist (per registration).</span>", unsafe_allow_html=True)

    colA, colB = st.columns(2)
    with colA:
        ew_lb = st.number_input(
            "Empty weight (lb) (kg)",
            value=float(ew_lb_default) if ew_lb_default is not None else 0.0,
            step=1.0,
            format="%.0f",
        )
        em_inlb = st.number_input(
            "Empty moment (in-lb)",
            value=float(em_inlb_default) if em_inlb_default is not None else 0.0,
            step=1.0,
            format="%.0f",
        )
        st.caption(f"Empty weight display: **{fmt_int_lb_kg(ew_lb)}**")
    with colB:
        # Arms are static in your case
        st.session_state["arm_front"] = st.number_input("Front seats arm (in)", value=float(st.session_state["arm_front"]), step=0.1, format="%.1f")
        st.session_state["arm_rear"]  = st.number_input("Rear seats arm (in)",  value=float(st.session_state["arm_rear"]),  step=0.1, format="%.1f")
        st.session_state["arm_fuel"]  = st.number_input("Fuel arm (in)",       value=float(st.session_state["arm_fuel"]),  step=0.1, format="%.1f")
        st.session_state["arm_bag"]   = st.number_input("Baggage arm (in)",    value=float(st.session_state["arm_bag"]),   step=0.1, format="%.1f")

    st.session_state["_empty"] = {"ew_lb": ew_lb, "em_inlb": em_inlb}


# ----------------------------
# TAB 2 — Airfields & Forecast
# ----------------------------
with tab2:
    st.markdown("### Approved Airfields (DEP / ARR / ALT1 / ALT2) + Forecast (Open-Meteo)")

    icao_options = sorted(AERODROMES_DB.keys())

    for i, leg in enumerate(st.session_state.legs):
        role = leg["role"]
        default_icao = leg["icao"]

        st.markdown(f"#### {role}")
        c1, c2, c3, c4, c5 = st.columns([0.26, 0.18, 0.14, 0.21, 0.21])

        with c1:
            icao = st.selectbox(
                "ICAO",
                icao_options,
                index=icao_options.index(default_icao) if default_icao in icao_options else 0,
                key=f"icao_{i}",
            )
            st.session_state.legs[i]["icao"] = icao
            ad = AERODROMES_DB[icao]
            st.caption(f"{ad['name']} — Elev {ad['elev_ft']:.0f} ft")

        with c2:
            flight_date = st.session_state.flight_date
            dep_dt = dt.datetime.combine(flight_date, st.session_state.dep_time_utc).replace(tzinfo=dt.timezone.utc)
            arr_dt = dt.datetime.combine(flight_date, st.session_state.arr_time_utc).replace(tzinfo=dt.timezone.utc)
            used = dep_dt if i == 0 else arr_dt
            st.write("Time used (UTC)")
            st.code(used.strftime("%Y-%m-%d %H:00Z"), language="text")

        with c3:
            st.checkbox("Manual MET", key=f"manual_{i}")

        with c4:
            st.number_input("OAT (°C)", key=f"temp_{i}", step=1)
            st.number_input("QNH (hPa)", key=f"qnh_{i}", min_value=900, max_value=1050, step=1)

        with c5:
            st.number_input("Wind FROM (°)", key=f"wdir_{i}", min_value=0, max_value=360, step=1)
            st.number_input("Wind speed (kt)", key=f"wspd_{i}", min_value=0, step=1)

        b1, b2 = st.columns([0.22, 0.78])
        with b1:
            st.button(
                f"Fetch forecast ({role})",
                on_click=fetch_forecast_for_leg,
                args=(i,),
                disabled=bool(st.session_state.get(f"manual_{i}", False)),
                key=f"fetch_btn_{i}",
            )
        with b2:
            st.markdown(
                f"<span class='chip'>Wind {fmt_wind(int(st.session_state[f'wdir_{i}']), int(st.session_state[f'wspd_{i}']))}</span>"
                f"<span class='chip'>Temp {int(st.session_state[f'temp_{i}'])}°C</span>"
                f"<span class='chip'>QNH {int(st.session_state[f'qnh_{i}'])}</span>",
                unsafe_allow_html=True,
            )

    st.markdown("---")
    cF1, cF2 = st.columns([0.25, 0.75])
    with cF1:
        st.button("Fetch forecast for ALL legs", type="primary", on_click=fetch_forecast_all, key="fetch_all")
    with cF2:
        if st.session_state.get("_fetch_msg_all"):
            if st.session_state.get("_fetch_ok_all"):
                st.success(st.session_state["_fetch_msg_all"])
            else:
                st.warning(st.session_state["_fetch_msg_all"])


# ----------------------------
# TAB 3 — Weight & Balance
# ----------------------------
with tab3:
    st.markdown("### Weight & Balance — 3 states (Empty / Takeoff / Landing)")

    empty = st.session_state.get("_empty", {"ew_lb": 0.0, "em_inlb": 0.0})
    ew_lb = float(empty["ew_lb"])
    em_inlb = float(empty["em_inlb"])

    st.markdown("#### Payload (inputs in kg / liters)")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.number_input("Front seats (kg)", min_value=0.0, step=0.5, key="front_kg")
    with col2:
        st.number_input("Rear seats (kg)", min_value=0.0, step=0.5, key="rear_kg")
    with col3:
        st.number_input("Baggage (kg)", min_value=0.0, step=0.5, key="bag_kg")
    with col4:
        st.number_input("Fuel at takeoff (L)", min_value=0.0, step=1.0, key="fuel_l_to")

    st.markdown("#### Landing fuel (simple)")
    cF1, cF2 = st.columns(2)
    with cF1:
        st.number_input("Enroute time to destination (min)", min_value=0, step=5, key="enrt_min")
    with cF2:
        st.number_input("Taxi/runup allowance (min)", min_value=0, step=1, key="taxi_min")

    # Fuel burn fixed 10 GPH
    total_min = int(st.session_state["enrt_min"]) + int(st.session_state["taxi_min"])
    used_gal = FUEL_BURN_GPH * (total_min / 60.0)

    fuel_gal_to = float(st.session_state["fuel_l_to"]) * L_TO_USGAL
    fuel_gal_ldg = max(0.0, fuel_gal_to - used_gal)

    st.caption(f"Fuel at takeoff: **{fmt_gal_l(fuel_gal_to)} US gal (L)**")
    st.caption(f"Fuel at landing (computed): **{fmt_gal_l(fuel_gal_ldg)} US gal (L)**")

    # Convert weights to lb
    front_lb = float(st.session_state["front_kg"]) * KG_TO_LB
    rear_lb  = float(st.session_state["rear_kg"])  * KG_TO_LB
    bag_lb   = float(st.session_state["bag_kg"])   * KG_TO_LB

    # For W&B we need fuel weight in lb; since your sheet uses “Fuel (48 gal max)”
    # We must use a weight-per-gallon. Keep it simple: 6.0 lb/gal (AVGAS typical).
    avgas_lb_per_gal = 6.0
    fuel_lb_to = fuel_gal_to * avgas_lb_per_gal
    fuel_lb_ldg = fuel_gal_ldg * avgas_lb_per_gal

    # Arms (in)
    arms = {
        "empty": (em_inlb / ew_lb) if ew_lb > 0 else 0.0,  # derive CG from moment/weight
        "front": float(st.session_state["arm_front"]),
        "rear": float(st.session_state["arm_rear"]),
        "fuel": float(st.session_state["arm_fuel"]),
        "baggage": float(st.session_state["arm_bag"]),
    }

    # Moments
    m_empty = em_inlb
    m_front = front_lb * arms["front"]
    m_rear  = rear_lb  * arms["rear"]
    m_bag   = bag_lb   * arms["baggage"]
    m_fuel_to  = fuel_lb_to  * arms["fuel"]
    m_fuel_ldg = fuel_lb_ldg * arms["fuel"]

    # States totals
    def totals(fuel_lb, m_fuel):
        w = ew_lb + front_lb + rear_lb + bag_lb + fuel_lb
        m = m_empty + m_front + m_rear + m_bag + m_fuel
        cg = (m / w) if w > 0 else 0.0
        return w, m, cg

    w_empty, m_empty_state, cg_empty = ew_lb, m_empty, arms["empty"]
    w_to, m_to, cg_to = totals(fuel_lb_to, m_fuel_to)
    w_ldg, m_ldg, cg_ldg = totals(fuel_lb_ldg, m_fuel_ldg)

    st.markdown("#### Summary")
    rows = [
        ("Empty",   w_empty, cg_empty, m_empty_state),
        ("Takeoff", w_to,    cg_to,    m_to),
        ("Landing", w_ldg,   cg_ldg,   m_ldg),
    ]
    html = ["<table class='mb-table'><tr><th>State</th><th>Weight lb (kg)</th><th>CG (in)</th><th>Moment (in-lb)</th></tr>"]
    for name, w, cg, m in rows:
        html.append(f"<tr><td><b>{name}</b></td><td>{fmt_int_lb_kg(w)}</td><td>{cg:.1f}</td><td>{m:.0f}</td></tr>")
    html.append("</table>")
    st.markdown("".join(html), unsafe_allow_html=True)

    st.session_state["_wb"] = {
        "ew_lb": ew_lb,
        "em_inlb": em_inlb,
        "front_lb": front_lb,
        "rear_lb": rear_lb,
        "bag_lb": bag_lb,
        "fuel_gal_to": fuel_gal_to,
        "fuel_gal_ldg": fuel_gal_ldg,
        "fuel_lb_to": fuel_lb_to,
        "fuel_lb_ldg": fuel_lb_ldg,
        "arms": arms,
        "states": {
            "empty":   {"w": w_empty, "m": m_empty_state, "cg": cg_empty},
            "takeoff": {"w": w_to,    "m": m_to,         "cg": cg_to},
            "landing": {"w": w_ldg,   "m": m_ldg,        "cg": cg_ldg},
        },
    }


# ----------------------------
# TAB 4 — PDF
# ----------------------------
with tab4:
    st.markdown("### Generate filled PDF")

    template_bytes = read_pdf_bytes()
    fields_in_pdf = get_field_names(template_bytes)

    with st.expander("PDF field names (debug)", expanded=False):
        st.write(fields_in_pdf)

    wb = st.session_state.get("_wb", {})
    states = wb.get("states", {})
    if not states:
        st.info("Go to 'Weight & Balance' tab first.")
        st.stop()

    reg = st.session_state.get("reg", "") or ""
    date_str = st.session_state.flight_date.strftime("%d/%m/%Y")

    def met(i: int):
        return {
            "temp": int(st.session_state[f"temp_{i}"]),
            "qnh": int(st.session_state[f"qnh_{i}"]),
            "wdir": int(st.session_state[f"wdir_{i}"]),
            "wspd": int(st.session_state[f"wspd_{i}"]),
        }

    # NOTE: These field names MUST match your edited PDF.
    # You said “field names are now correct” — keep these consistent with your template.
    fields = {
        "Date": date_str,
        "Aircraft_Reg": reg,

        # Loading Data — include units in parentheses
        "Weight_EMPTY":   fmt_int_lb_kg(wb["ew_lb"]),
        "Datum_EMPTY":    f"{wb['arms']['empty']:.1f}",
        "Moment_EMPTY":   f"{wb['em_inlb']:.0f}",

        "Weight_FRONT":   fmt_int_lb_kg(wb["front_lb"]),
        "Moment_FRONT":   f"{(wb['front_lb'] * wb['arms']['front']):.0f}",

        "Weight_REAR":    fmt_int_lb_kg(wb["rear_lb"]),
        "Moment_REAR":    f"{(wb['rear_lb'] * wb['arms']['rear']):.0f}",

        # Fuel row: show gallons (liters)
        "Weight_FUEL":    f"{fmt_int_gal_l(wb['fuel_gal_to'])}",
        "Moment_FUEL":    f"{(wb['fuel_lb_to'] * wb['arms']['fuel']):.0f}",

        "Weight_BAGGAGE": fmt_int_lb_kg(wb["bag_lb"]),
        "Moment_BAGGAGE": f"{(wb['bag_lb'] * wb['arms']['baggage']):.0f}",

        "Weight_RAMP":    fmt_int_lb_kg(states["takeoff"]["w"]),
        "Datum_RAMP":     f"{states['takeoff']['cg']:.1f}",
        "Moment_RAMP":    f"{states['takeoff']['m']:.0f}",

        "Weight_TAKEOFF": fmt_int_lb_kg(states["takeoff"]["w"]),
        "Datum_TAKEOFF":  f"{states['takeoff']['cg']:.1f}",
        "Moment_TAKEOFF": f"{states['takeoff']['m']:.0f}",
    }

    # Legs — this assumes your edited PDF has these names; adjust if needed
    legs = st.session_state.legs
    mapping = [
        ("DEPARTURE",   legs[0]["icao"], met(0)),
        ("ARRIVAL",     legs[1]["icao"], met(1)),
        ("ALTERNATE_1", legs[2]["icao"], met(2)),
        ("ALTERNATE_2", legs[3]["icao"], met(3)),
    ]

    for prefix, icao, mv in mapping:
        ad = AERODROMES_DB.get(icao)
        fields[f"Airfield_{prefix}"] = icao
        if ad:
            fields[f"Elevation_{prefix}"] = f"{int(round(ad['elev_ft']))}"
        fields[f"QNH_{prefix}"] = f"{mv['qnh']}"
        fields[f"Temperature_{prefix}"] = f"{mv['temp']}"
        fields[f"Wind_{prefix}"] = fmt_wind(mv["wdir"], mv["wspd"])

    st.markdown("#### CG chart points")
    st.write(
        f"Empty: {states['empty']['w']:.0f} lb @ {states['empty']['cg']:.1f} in | "
        f"Takeoff: {states['takeoff']['w']:.0f} lb @ {states['takeoff']['cg']:.1f} in | "
        f"Landing: {states['landing']['w']:.0f} lb @ {states['landing']['cg']:.1f} in"
    )

    if st.button("Generate filled PDF", type="primary"):
        writer, reader = fill_pdf_writer(template_bytes, fields)

        # Overlay on PAGE 0 only
        page0 = reader.pages[0]
        pw = float(page0.mediabox.width)
        ph = float(page0.mediabox.height)

        points = [
            {"label": "Empty",   "cg": float(states["empty"]["cg"]),   "wt": float(states["empty"]["w"]),   "rgb": (0.10, 0.60, 0.10)},
            {"label": "Takeoff", "cg": float(states["takeoff"]["cg"]), "wt": float(states["takeoff"]["w"]), "rgb": (0.10, 0.30, 0.85)},
            {"label": "Landing", "cg": float(states["landing"]["cg"]), "wt": float(states["landing"]["w"]), "rgb": (0.85, 0.20, 0.20)},
        ]
        overlay_bytes = make_chart_overlay(pw, ph, points, legend_xy=(500, 320), marker_r=4)
        overlay_page = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
        writer.pages[0].merge_page(overlay_page)

        out = io.BytesIO()
        writer.write(out)
        out.seek(0)

        file_name = f"{reg}_PA28_MB_Perf.pdf" if reg and "(no fleet" not in reg else "PA28_MB_Perf.pdf"
        st.download_button("Download PDF", data=out.getvalue(), file_name=file_name, mime="application/pdf")
        st.success("PDF generated. Review before flight.")

