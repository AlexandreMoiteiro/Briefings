# Streamlit app — Piper PA-28 (W&B + PDF + Forecast) — v1
# - 4 legs: DEP / ARR / ALT1 / ALT2
# - Separate departure + arrival target hours (UTC)
# - Forecast via Open-Meteo (no key), with model selection
# - Wind improved: vector-mean over a time window, direction rounded to tens (e.g. 240/08)
# - PDF fill uses Tecnam-style approach; CG chart overlay with 3 points (Empty/Takeoff/Landing)

import io
import json
import math
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, List, Optional

import pytz
import requests
import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas


# ============================================================
# PAGE / STYLE
# ============================================================
st.set_page_config(
    page_title="PA-28 — Mass & Balance + PDF",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1250px !important; }
      .hdr{font-size:1.25rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:8px;margin:2px 0 14px}
      .chip{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef2f7;margin-left:8px;font-size:.85rem}
      .mb-table{border-collapse:collapse;width:100%;font-size:.92rem}
      .mb-table th{border-bottom:2px solid #cbd0d6;text-align:left}
      .mb-table td{padding:3px 6px;border-bottom:1px dashed #e5e7ec;vertical-align:top}
      .ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
      .hint{font-size:.85rem;color:#6b7280}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="hdr">Piper PA-28 — Mass & Balance + Forecast + PDF</div>', unsafe_allow_html=True)


# ============================================================
# APPROVED AIRFIELDS — reuse from Tecnam code (same structure)
# (keep only what we need: lat/lon/elev + runways list)
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
    "LPPM": {"name": "Portimão", "lat": 37.1493, "lon": -8.5840, "elev_ft": 5.0,
             "runways": [{"id": "11", "qfu": 110.0, "toda": 860.0, "lda": 860.0, "slope_pc": 0.0, "paved": True},
                         {"id": "29", "qfu": 290.0, "toda": 860.0, "lda": 860.0, "slope_pc": 0.0, "paved": True}]},
    "LPPR": {"name": "Porto", "lat": 41.2481, "lon": -8.6811, "elev_ft": 227.0,
             "runways": [{"id": "17", "qfu": 170.0, "toda": 3480.0, "lda": 3480.0, "slope_pc": 0.0, "paved": True},
                         {"id": "35", "qfu": 350.0, "toda": 3480.0, "lda": 3480.0, "slope_pc": 0.0, "paved": True}]},
}


# ============================================================
# OPEN-METEO — improved wind (vector mean)
# ============================================================
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo models you can request (no key). If one fails, you can switch.
MODEL_OPTIONS = {
    "Best mix (auto)": None,
    "ECMWF IFS": "ecmwf_ifs04",
    "ICON": "icon_seamless",
    "GFS": "gfs_seamless",
}

def _utc_hour(dt_utc: dt.datetime) -> dt.datetime:
    return dt_utc.replace(minute=0, second=0, microsecond=0, tzinfo=dt.timezone.utc)

def round_dir_10(deg: float) -> int:
    d = int(round(deg / 10.0) * 10) % 360
    return 360 if d == 0 else d  # aviation style (360 instead of 000)

def fmt_wind(dir_deg: int, spd_kt: int) -> str:
    return f"{dir_deg:03d}/{spd_kt:02d}"

def om_point_forecast(lat, lon, start_date_iso, end_date_iso, model: Optional[str]):
    params = {
        "latitude": round(float(lat), 4),
        "longitude": round(float(lon), 4),
        "hourly": ",".join([
            "temperature_2m",
            "wind_speed_10m",
            "wind_direction_10m",
            "wind_gusts_10m",
            "pressure_msl",
        ]),
        "timezone": "UTC",
        "windspeed_unit": "kn",
        "temperature_unit": "celsius",
        "pressure_unit": "hPa",
        "start_date": start_date_iso,
        "end_date": end_date_iso,
    }
    if model:
        params["models"] = model

    r = requests.get(OPENMETEO_URL, params=params, timeout=25)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "detail": r.text, "params": params}

    data = r.json()
    h = data.get("hourly", {})
    times = h.get("time", []) or []
    wspd = h.get("wind_speed_10m", []) or []
    wdir = h.get("wind_direction_10m", []) or []
    gust = h.get("wind_gusts_10m", []) or []
    temp = h.get("temperature_2m", []) or []
    qnh = h.get("pressure_msl", []) or []

    out = []
    for i, t in enumerate(times):
        # Open-Meteo gives "YYYY-MM-DDTHH:MM"
        dtu = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        out.append({
            "dt": dtu,
            "wind_kt": float(wspd[i]) if i < len(wspd) and wspd[i] is not None else 0.0,
            "wind_dir": float(wdir[i]) if i < len(wdir) and wdir[i] is not None else 0.0,
            "gust_kt": float(gust[i]) if i < len(gust) and gust[i] is not None else 0.0,
            "temp_c": float(temp[i]) if i < len(temp) and temp[i] is not None else None,
            "qnh_hpa": float(qnh[i]) if i < len(qnh) and qnh[i] is not None else None,
        })
    return {"hours": out, "params": params}

def vector_mean_wind(samples: List[dict]) -> Tuple[int, int, Optional[int]]:
    """
    Aviation-friendly mean:
    - average as vectors (u,v) so direction is stable
    - speed from vector magnitude
    """
    if not samples:
        return 0, 0, None

    u_sum = 0.0
    v_sum = 0.0
    gust_max = None
    for s in samples:
        spd = float(s["wind_kt"])
        # direction is FROM; convert to vector pointing TO by adding 180 in trig sense
        dir_from = float(s["wind_dir"]) % 360.0
        theta = math.radians(dir_from)
        # meteorological convention: u = -V*sin(dir), v = -V*cos(dir) (as in your Tecnam code)
        u = -spd * math.sin(theta)
        v = -spd * math.cos(theta)
        u_sum += u
        v_sum += v

        g = s.get("gust_kt", None)
        if g is not None:
            gust_max = int(round(max(gust_max or 0, float(g))))

    n = len(samples)
    u_mean = u_sum / n
    v_mean = v_sum / n

    spd_mean = math.sqrt(u_mean*u_mean + v_mean*v_mean)
    # back to FROM direction:
    dir_from = (math.degrees(math.atan2(u_mean, v_mean)) + 180.0) % 360.0

    spd_kt = int(round(spd_mean))
    dir_rounded = round_dir_10(dir_from)
    return dir_rounded, spd_kt, gust_max

def pick_samples_around(hours: List[dict], target: dt.datetime, window_hours: int = 1) -> List[dict]:
    """
    Take samples within +/- window_hours from target (UTC).
    """
    if not hours:
        return []
    lo = target - dt.timedelta(hours=window_hours)
    hi = target + dt.timedelta(hours=window_hours)
    return [h for h in hours if lo <= h["dt"] <= hi]


# ============================================================
# W&B — PA-28 logic (lbs/in/in-lbs) but UI in kg/L optional
# ============================================================
KG_TO_LB = 2.2046226218
LB_TO_KG = 1.0 / KG_TO_LB

L_TO_GAL = 0.2641720524
GAL_TO_L = 1.0 / L_TO_GAL

def fmt_hm(total_min: int) -> str:
    if total_min is None or total_min <= 0:
        return "0min"
    h, m = divmod(int(round(total_min)), 60)
    if h == 0:
        return f"{m}min"
    return f"{h}h" if m == 0 else f"{h}h{m:02d}min"

@dataclass
class WBState:
    weight_lb: float
    moment_inlb: float
    cg_in: float

def compute_state(weights_lb: Dict[str, float], arms_in: Dict[str, float]) -> WBState:
    """
    weights_lb keys: empty, front, rear, fuel, baggage
    arms_in keys: empty, front, rear, fuel, baggage (empty arm is the computed CG of empty)
    moment is sum(w*arm)
    """
    w_total = sum(weights_lb.values())
    m_total = 0.0
    for k, w in weights_lb.items():
        m_total += float(w) * float(arms_in[k])
    cg = (m_total / w_total) if w_total > 0 else 0.0
    return WBState(weight_lb=w_total, moment_inlb=m_total, cg_in=cg)


# ============================================================
# CG CHART — coordinates (with your corrected 85–88 @1200)
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
    (85, 1200): 229,  # corrected
    (86, 1200): 245,  # corrected
    (87, 1200): 262,  # corrected
    (88, 1200): 277,  # corrected
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
    if x1 == x0: return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def interp_1d(x, pts):
    pts = sorted(pts, key=lambda p: p[0])
    x = clamp(float(x), pts[0][0], pts[-1][0])
    for i in range(len(pts)-1):
        x0,y0 = pts[i]
        x1,y1 = pts[i+1]
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

    # extrapolate using max intermediate point (must exist for 85–88 etc.)
    cands = [w for (cg,w) in X_AT.keys() if cg == cg_int and w != 1200]
    w_mid = max(cands)
    x_mid = float(X_AT[(cg_int, w_mid)])
    y_mid = y_from_weight(w_mid)
    slope = 0.0 if y_mid == y0 else (x_mid - x0) / (y_mid - y0)
    x1 = x0 + slope * (y1 - y0)
    return (x0, y0), (x1, y1)

CG_LINES = {cg: build_cg_line(cg) for cg in range(82, 94)}

def x_on_line(cg_int: int, y: float) -> float:
    (x0,y0),(x1,y1) = CG_LINES[cg_int]
    if y1 == y0:
        return x0
    t = (y - y0) / (y1 - y0)
    return x0 + t*(x1-x0)

def cg_wt_to_xy(cg_in: float, wt_lb: float) -> Tuple[float,float]:
    y = y_from_weight(wt_lb)
    cg_in = clamp(float(cg_in), 82.0, 93.0)
    c0 = int(math.floor(cg_in))
    c1 = min(93, c0+1)
    if c0 < 82:
        c0, c1 = 82, 83
    x0 = x_on_line(c0, y)
    x1 = x_on_line(c1, y)
    x = x0 if c0 == c1 else lerp(cg_in, c0, c1, x0, x1)
    return float(x), float(y)


# ============================================================
# PDF fill (Tecnam-style) + overlay chart on page 0
# ============================================================
PDF_TEMPLATE = "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"

def read_pdf_bytes() -> bytes:
    p = Path(PDF_TEMPLATE)
    if not p.exists():
        raise FileNotFoundError(f"PDF template not found: {PDF_TEMPLATE}")
    return p.read_bytes()

def fill_pdf(template_bytes: bytes, fields: dict) -> PdfWriter:
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

    return writer

def make_overlay_pdf(page_w, page_h, points, legend_xy=(500, 320), marker_r=4):
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=(page_w, page_h))

    # points
    for p in points:
        x,y = cg_wt_to_xy(p["cg"], p["wt"])
        rr,gg,bb = p["rgb"]
        c.setFillColorRGB(rr,gg,bb)
        c.circle(x,y,marker_r, fill=1, stroke=0)

    # legend
    lx, ly = legend_xy
    c.setFillColorRGB(0,0,0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(lx, ly, "Legend")
    ly -= 14
    c.setFont("Helvetica", 9)
    for p in points:
        rr,gg,bb = p["rgb"]
        c.setFillColorRGB(rr,gg,bb)
        c.rect(lx, ly-7, 10, 10, fill=1, stroke=0)
        c.setFillColorRGB(0,0,0)
        c.drawString(lx+14, ly-5, p["label"])
        ly -= 14

    c.showPage()
    c.save()
    bio.seek(0)
    return bio.read()


# ============================================================
# SESSION DEFAULTS
# ============================================================
LISBON_TZ = pytz.timezone("Europe/Lisbon")

if "flight_date" not in st.session_state:
    st.session_state.flight_date = dt.datetime.now(LISBON_TZ).date()

if "dep_time_utc" not in st.session_state:
    # next whole hour UTC
    st.session_state.dep_time_utc = (dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)).time()

if "arr_time_utc" not in st.session_state:
    st.session_state.arr_time_utc = (dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=2)).time()

DEFAULT_LEGS = [
    {"role": "Departure", "icao": "LPCS"},
    {"role": "Arrival",   "icao": "LPFR"},
    {"role": "Alternate 1", "icao": "LPMT"},
    {"role": "Alternate 2", "icao": "LPSO"},
]
if "legs" not in st.session_state:
    st.session_state.legs = [dict(x) for x in DEFAULT_LEGS]

if "met" not in st.session_state:
    st.session_state.met = [{"temp": 15, "qnh": 1013, "wind_dir": 240, "wind_kt": 8, "gust_kt": None} for _ in range(4)]

if "model_key" not in st.session_state:
    st.session_state.model_key = "ECMWF IFS"


# ============================================================
# UI TABS
# ============================================================
tab1, tab2, tab3, tab4 = st.tabs([
    "1) Flight & Times", "2) Airfields & Forecast", "3) Weight & Balance", "4) PDF"
])


# ----------------------------
# TAB 1 — Flight & Times
# ----------------------------
with tab1:
    c1, c2, c3 = st.columns([0.33, 0.33, 0.34])
    with c1:
        st.session_state.flight_date = st.date_input("Flight date (Europe/Lisbon)", value=st.session_state.flight_date)
    with c2:
        st.session_state.dep_time_utc = st.time_input("Departure time (UTC)", value=st.session_state.dep_time_utc, step=3600)
    with c3:
        st.session_state.arr_time_utc = st.time_input("Arrival time (UTC)", value=st.session_state.arr_time_utc, step=3600)

    st.markdown("<span class='hint'>Dica: para alternates uso a hora de chegada por defeito.</span>", unsafe_allow_html=True)

    st.markdown("### Aircraft / W&B inputs base")
    colA, colB, colC = st.columns([0.34, 0.33, 0.33])
    with colA:
        reg = st.text_input("Aircraft Reg", value=st.session_state.get("reg", "CS-XXX"))
        st.session_state["reg"] = reg
    with colB:
        # you said you input datum/arms per airframe manually later; for now input arms here
        st.session_state["arm_front"] = st.number_input("Front seats arm (in)", value=float(st.session_state.get("arm_front", 80.5)), step=0.1, format="%.1f")
        st.session_state["arm_rear"]  = st.number_input("Rear seats arm (in)", value=float(st.session_state.get("arm_rear", 118.1)), step=0.1, format="%.1f")
    with colC:
        st.session_state["arm_fuel"]  = st.number_input("Fuel arm (in)", value=float(st.session_state.get("arm_fuel", 95.0)), step=0.1, format="%.1f")
        st.session_state["arm_bag"]   = st.number_input("Baggage arm (in)", value=float(st.session_state.get("arm_bag", 142.8)), step=0.1, format="%.1f")

    st.markdown("### Empty aircraft")
    colE1, colE2 = st.columns(2)
    with colE1:
        st.session_state["empty_weight_lb"] = st.number_input("Empty Weight (lbs)", value=float(st.session_state.get("empty_weight_lb", 1650.0)), step=1.0)
    with colE2:
        st.session_state["empty_cg_in"] = st.number_input("Empty CG (in aft datum)", value=float(st.session_state.get("empty_cg_in", 85.0)), step=0.1, format="%.1f")


# ----------------------------
# TAB 2 — Airfields & Forecast
# ----------------------------
with tab2:
    st.markdown("### Approved Airfields (DEP / ARR / ALT1 / ALT2) + Forecast")
    colM1, colM2 = st.columns([0.4, 0.6])
    with colM1:
        st.session_state.model_key = st.selectbox("Forecast model", list(MODEL_OPTIONS.keys()),
                                                  index=list(MODEL_OPTIONS.keys()).index(st.session_state.model_key) if st.session_state.model_key in MODEL_OPTIONS else 0)
    with colM2:
        st.session_state["wind_window_h"] = st.slider("Wind smoothing window (± hours)", min_value=0, max_value=3, value=int(st.session_state.get("wind_window_h", 1)), step=1)

    icao_options = sorted(AERODROMES_DB.keys())

    # leg times: DEP uses dep time; all others default to arr time (but editable per leg if you want later)
    flight_date = st.session_state.flight_date
    dep_dt_utc = dt.datetime.combine(flight_date, st.session_state.dep_time_utc).replace(tzinfo=dt.timezone.utc)
    arr_dt_utc = dt.datetime.combine(flight_date, st.session_state.arr_time_utc).replace(tzinfo=dt.timezone.utc)

    # render each leg
    for i, leg in enumerate(st.session_state.legs):
        role = leg["role"]
        default_icao = leg["icao"]

        st.markdown(f"#### {role}")
        c1, c2, c3, c4 = st.columns([0.30, 0.18, 0.18, 0.34])

        with c1:
            icao = st.selectbox("ICAO", icao_options,
                                index=icao_options.index(default_icao) if default_icao in icao_options else 0,
                                key=f"icao_{i}")
            st.session_state.legs[i]["icao"] = icao
            ad = AERODROMES_DB[icao]
            st.caption(f"{ad['name']} — Elev {ad['elev_ft']:.0f} ft")

        with c2:
            # show which time is used
            use_dt = dep_dt_utc if role == "Departure" else arr_dt_utc
            st.write("Time used (UTC)")
            st.code(use_dt.strftime("%Y-%m-%d %H:00Z"), language="text")

        with c3:
            # manual override toggle
            manual = st.checkbox("Manual MET", value=False, key=f"manual_{i}")
            st.session_state[f"manual_{i}"] = manual

        with c4:
            # MET inputs (editable always)
            met = st.session_state.met[i]
            met["temp"] = int(st.number_input("OAT (°C)", value=int(met["temp"]), step=1, key=f"temp_{i}"))
            met["qnh"] = int(st.number_input("QNH (hPa)", value=int(met["qnh"]), min_value=900, max_value=1050, step=1, key=f"qnh_{i}"))
            met["wind_dir"] = int(st.number_input("Wind FROM (°)", value=int(met["wind_dir"]), min_value=0, max_value=360, step=1, key=f"wdir_{i}"))
            met["wind_kt"] = int(st.number_input("Wind speed (kt)", value=int(met["wind_kt"]), min_value=0, step=1, key=f"wspd_{i}"))
            st.session_state.met[i] = met

        # Button row per leg
        colB1, colB2 = st.columns([0.22, 0.78])
        with colB1:
            if st.button(f"Fetch forecast ({role})", key=f"fetch_one_{i}", disabled=st.session_state.get(f"manual_{i}", False)):
                ad = AERODROMES_DB[st.session_state.legs[i]["icao"]]
                start_iso = flight_date.strftime("%Y-%m-%d")
                end_iso = start_iso
                model = MODEL_OPTIONS.get(st.session_state.model_key, None)
                resp = om_point_forecast(ad["lat"], ad["lon"], start_iso, end_iso, model=model)
                if "error" in resp:
                    st.error(f"{icao}: {resp['error']} {resp.get('detail','')}")
                else:
                    hours = resp["hours"]
                    target = dep_dt_utc if role == "Departure" else arr_dt_utc
                    window = int(st.session_state["wind_window_h"])
                    samples = pick_samples_around(hours, _utc_hour(target), window_hours=window)
                    if not samples:
                        # fallback: nearest hour
                        nearest = min(hours, key=lambda h: abs(h["dt"] - _utc_hour(target)))
                        samples = [nearest]

                    wdir10, wspd, gust = vector_mean_wind(samples)
                    # temp/qnh: nearest hour (stable)
                    nearest = min(hours, key=lambda h: abs(h["dt"] - _utc_hour(target)))
                    temp_c = nearest.get("temp_c", None)
                    qnh = nearest.get("qnh_hpa", None)

                    met = st.session_state.met[i]
                    if temp_c is not None:
                        met["temp"] = int(round(temp_c))
                    if qnh is not None:
                        met["qnh"] = int(round(qnh))
                    met["wind_dir"] = int(wdir10)
                    met["wind_kt"] = int(wspd)
                    met["gust_kt"] = gust
                    st.session_state.met[i] = met

                    st.success(f"{icao}: {fmt_wind(wdir10, wspd)} (smoothed ±{window}h)")

        with colB2:
            met = st.session_state.met[i]
            gust_txt = f" G{met['gust_kt']:02d}" if met.get("gust_kt") else ""
            st.markdown(
                f"<span class='chip'>Wind {fmt_wind(int(met['wind_dir']), int(met['wind_kt']))}{gust_txt}</span>"
                f"<span class='chip'>Temp {int(met['temp'])}°C</span>"
                f"<span class='chip'>QNH {int(met['qnh'])}</span>",
                unsafe_allow_html=True
            )

    st.markdown("---")
    cF1, cF2 = st.columns([0.25, 0.75])
    with cF1:
        if st.button("Fetch forecast for ALL (non-manual)", type="primary"):
            ok = 0
            err = 0
            flight_date = st.session_state.flight_date
            start_iso = flight_date.strftime("%Y-%m-%d")
            end_iso = start_iso
            model = MODEL_OPTIONS.get(st.session_state.model_key, None)
            window = int(st.session_state["wind_window_h"])
            for i, leg in enumerate(st.session_state.legs):
                if st.session_state.get(f"manual_{i}", False):
                    continue
                icao = leg["icao"]
                ad = AERODROMES_DB[icao]
                target = dep_dt_utc if leg["role"] == "Departure" else arr_dt_utc
                resp = om_point_forecast(ad["lat"], ad["lon"], start_iso, end_iso, model=model)
                if "error" in resp:
                    err += 1
                    st.error(f"{icao}: {resp['error']}")
                    continue
                hours = resp["hours"]
                samples = pick_samples_around(hours, _utc_hour(target), window_hours=window)
                if not samples:
                    samples = [min(hours, key=lambda h: abs(h["dt"] - _utc_hour(target)))]
                wdir10, wspd, gust = vector_mean_wind(samples)
                nearest = min(hours, key=lambda h: abs(h["dt"] - _utc_hour(target)))
                temp_c = nearest.get("temp_c", None)
                qnh = nearest.get("qnh_hpa", None)

                met = st.session_state.met[i]
                if temp_c is not None:
                    met["temp"] = int(round(temp_c))
                if qnh is not None:
                    met["qnh"] = int(round(qnh))
                met["wind_dir"] = int(wdir10)
                met["wind_kt"] = int(wspd)
                met["gust_kt"] = gust
                st.session_state.met[i] = met
                ok += 1

            if ok and not err:
                st.success(f"Forecast updated for {ok} leg(s).")
            elif ok:
                st.warning(f"Forecast updated for {ok} leg(s); {err} error(s).")
            else:
                st.error("No legs updated.")

    with cF2:
        st.markdown("<span class='hint'>Se o vento ainda variar muito, troca o modelo (ECMWF/ICON/GFS) e/ou aumenta a janela.</span>", unsafe_allow_html=True)


# ----------------------------
# TAB 3 — Weight & Balance + Fuel
# ----------------------------
with tab3:
    st.markdown("### Weight & Balance (PA-28) — 3 states: Empty / Takeoff / Landing")

    # UI in kg for people + baggage + fuel liters, converted to lbs/gal internally
    colU1, colU2, colU3, colU4 = st.columns(4)
    with colU1:
        front_kg = st.number_input("Front seats (kg)", min_value=0.0, value=float(st.session_state.get("front_kg", 80.0)), step=0.5)
        st.session_state["front_kg"] = front_kg
    with colU2:
        rear_kg = st.number_input("Rear seats (kg)", min_value=0.0, value=float(st.session_state.get("rear_kg", 0.0)), step=0.5)
        st.session_state["rear_kg"] = rear_kg
    with colU3:
        bag_kg = st.number_input("Baggage (kg)", min_value=0.0, value=float(st.session_state.get("bag_kg", 5.0)), step=0.5)
        st.session_state["bag_kg"] = bag_kg
    with colU4:
        fuel_l_to = st.number_input("Fuel at Takeoff (L)", min_value=0.0, value=float(st.session_state.get("fuel_l_to", 80.0)), step=1.0)
        st.session_state["fuel_l_to"] = fuel_l_to

    st.markdown("### Fuel planning (simple, for landing state)")
    colF1, colF2, colF3 = st.columns([0.34, 0.33, 0.33])
    with colF1:
        rate_lph = st.number_input("Burn rate (L/h)", min_value=10.0, max_value=60.0, value=float(st.session_state.get("rate_lph", 30.0)), step=0.5)
        st.session_state["rate_lph"] = rate_lph
    with colF2:
        enrt_min = st.number_input("Enroute time to destination (min)", min_value=0, value=int(st.session_state.get("enrt_min", 60)), step=5)
        st.session_state["enrt_min"] = enrt_min
    with colF3:
        taxi_min = st.number_input("Taxi+runup allowance (min)", min_value=0, value=int(st.session_state.get("taxi_min", 10)), step=1)
        st.session_state["taxi_min"] = taxi_min

    # landing fuel computed
    used_l = rate_lph * ((enrt_min + taxi_min) / 60.0)
    fuel_l_ldg = max(0.0, fuel_l_to - used_l)

    st.caption(f"Computed fuel at landing: {fuel_l_ldg:.1f} L (used {used_l:.1f} L)")

    # conversions
    empty_w_lb = float(st.session_state["empty_weight_lb"])
    empty_cg_in = float(st.session_state["empty_cg_in"])
    front_lb = front_kg * KG_TO_LB
    rear_lb = rear_kg * KG_TO_LB
    bag_lb = bag_kg * KG_TO_LB

    # For PA-28 fuel, the PDF sheet uses "Fuel (48 Gallon maximum)" — so use gallons->lbs:
    # We don't know density from sheet; for now assume AVGAS 6.0 lb/gal (typical). If you want, we make it a constant later.
    avgas_lb_per_gal = float(st.number_input("AVGAS density (lb/gal)", value=float(st.session_state.get("avgas_lb_per_gal", 6.0)), step=0.1, format="%.1f"))
    st.session_state["avgas_lb_per_gal"] = avgas_lb_per_gal

    fuel_gal_to = fuel_l_to * L_TO_GAL
    fuel_lb_to = fuel_gal_to * avgas_lb_per_gal

    fuel_gal_ldg = fuel_l_ldg * L_TO_GAL
    fuel_lb_ldg = fuel_gal_ldg * avgas_lb_per_gal

    # arms
    arms = {
        "empty": empty_cg_in,
        "front": float(st.session_state["arm_front"]),
        "rear": float(st.session_state["arm_rear"]),
        "fuel": float(st.session_state["arm_fuel"]),
        "baggage": float(st.session_state["arm_bag"]),
    }

    # EMPTY state: only empty
    st_empty = compute_state(
        weights_lb={"empty": empty_w_lb, "front": 0.0, "rear": 0.0, "fuel": 0.0, "baggage": 0.0},
        arms_in=arms
    )
    # TAKEOFF
    st_to = compute_state(
        weights_lb={"empty": empty_w_lb, "front": front_lb, "rear": rear_lb, "fuel": fuel_lb_to, "baggage": bag_lb},
        arms_in=arms
    )
    # LANDING
    st_ldg = compute_state(
        weights_lb={"empty": empty_w_lb, "front": front_lb, "rear": rear_lb, "fuel": fuel_lb_ldg, "baggage": bag_lb},
        arms_in=arms
    )

    # show table
    st.markdown("#### States summary (lbs / in / in-lbs)")
    rows = [
        ("Empty",   st_empty.weight_lb, st_empty.cg_in, st_empty.moment_inlb),
        ("Takeoff", st_to.weight_lb,    st_to.cg_in,    st_to.moment_inlb),
        ("Landing", st_ldg.weight_lb,   st_ldg.cg_in,   st_ldg.moment_inlb),
    ]
    html = ["<table class='mb-table'><tr><th>State</th><th>Weight (lb)</th><th>CG (in)</th><th>Moment (in-lb)</th></tr>"]
    for name, w, cg, m in rows:
        html.append(f"<tr><td><b>{name}</b></td><td>{w:.0f}</td><td>{cg:.1f}</td><td>{m:.0f}</td></tr>")
    html.append("</table>")
    st.markdown("".join(html), unsafe_allow_html=True)

    # store for PDF
    st.session_state["_wb_states"] = {
        "empty": st_empty,
        "takeoff": st_to,
        "landing": st_ldg,
        "components": {
            "empty_weight_lb": empty_w_lb,
            "front_lb": front_lb,
            "rear_lb": rear_lb,
            "fuel_lb_to": fuel_lb_to,
            "fuel_lb_ldg": fuel_lb_ldg,
            "bag_lb": bag_lb,
            "arms": arms,
        }
    }


# ----------------------------
# TAB 4 — PDF
# ----------------------------
with tab4:
    st.markdown("### Generate filled PDF")
    template_bytes = read_pdf_bytes()
    reader = PdfReader(io.BytesIO(template_bytes))

    reg = st.session_state.get("reg", "CS-XXX") or ""
    date_str = st.session_state.flight_date.strftime("%d/%m/%Y")

    # Legs mapping for page 2: DEP/ARR/ALT1/ALT2
    leg_by_role = {leg["role"]: leg["icao"] for leg in st.session_state.legs}
    dep_icao = leg_by_role.get("Departure", "")
    arr_icao = leg_by_role.get("Arrival", "")
    alt1_icao = leg_by_role.get("Alternate 1", "")
    alt2_icao = leg_by_role.get("Alternate 2", "")

    # Per-leg MET values
    def met_for(role: str):
        idx = {"Departure":0, "Arrival":1, "Alternate 1":2, "Alternate 2":3}[role]
        return st.session_state.met[idx]

    # W&B states
    wb = st.session_state.get("_wb_states", {})
    st_empty: WBState = wb.get("empty", WBState(0,0,0))
    st_to: WBState = wb.get("takeoff", WBState(0,0,0))
    st_ldg: WBState = wb.get("landing", WBState(0,0,0))

    # Components for loading table
    comp = wb.get("components", {})
    empty_weight_lb = float(comp.get("empty_weight_lb", 0.0))
    arms = comp.get("arms", {"empty":0,"front":0,"rear":0,"fuel":0,"baggage":0})

    front_lb = float(comp.get("front_lb", 0.0))
    rear_lb = float(comp.get("rear_lb", 0.0))
    bag_lb  = float(comp.get("bag_lb", 0.0))
    fuel_lb_to = float(comp.get("fuel_lb_to", 0.0))

    # Build loading data fields (page 0)
    fields = {
        # page 2 header fields
        "Date": date_str,
        "Aircraft_Reg": reg,

        # LOADING DATA (page 0)
        "Weight_EMPTY": f"{empty_weight_lb:.0f}",
        "Datum_EMPTY": f"{float(arms['empty']):.1f}",
        "Moment_EMPTY": f"{(empty_weight_lb*float(arms['empty'])):.0f}",

        "Weight_FRONT": f"{front_lb:.0f}",
        "Moment_FRONT": f"{(front_lb*float(arms['front'])):.0f}",

        "Weight_REAR": f"{rear_lb:.0f}",
        "Moment_REAR": f"{(rear_lb*float(arms['rear'])):.0f}",

        "Weight_FUEL": f"{fuel_lb_to:.0f}",
        "Moment_FUEL": f"{(fuel_lb_to*float(arms['fuel'])):.0f}",

        "Weight_BAGGAGE": f"{bag_lb:.0f}",
        "Moment_BAGGAGE": f"{(bag_lb*float(arms['baggage'])):.0f}",

        # ramp / takeoff totals (as in sheet)
        "Weight_RAMP": f"{st_to.weight_lb:.0f}",
        "Datum_RAMP": f"{st_to.cg_in:.1f}",
        "Moment_RAMP": f"{st_to.moment_inlb:.0f}",

        "Weight_TAKEOFF": f"{st_to.weight_lb:.0f}",
        "Datum_TAKEOFF": f"{st_to.cg_in:.1f}",
        "Moment_TAKEOFF": f"{st_to.moment_inlb:.0f}",
    }

    # Put any values you want fixed later:
    fields["MTOW"] = fields.get("MTOW", "")
    fields["MLW"] = fields.get("MLW", "")

    # Page 2 — airfields + met (4 columns)
    def put_leg(prefix: str, icao: str, met: dict):
        if not icao:
            return
        ad = AERODROMES_DB.get(icao, None)
        fields[f"Airfield_{prefix}"] = icao
        if ad:
            fields[f"Elevation_{prefix}"] = f"{int(round(ad['elev_ft']))}"
        fields[f"QNH_{prefix}"] = f"{int(met.get('qnh', 1013))}"
        fields[f"Temperature_{prefix}"] = f"{int(met.get('temp', 15))}"
        wd = int(met.get("wind_dir", 240))
        ws = int(met.get("wind_kt", 8))
        fields[f"Wind_{prefix}"] = fmt_wind(wd, ws)

        # Leave RWY QFU / Pressure Alt / Density Alt / performance blank for now (you’ll add later)
        fields[f"RWY_QFU_{prefix}"] = fields.get(f"RWY_QFU_{prefix}", "")
        if prefix == "DEPARTURE":
            fields["Pressure_Alt _DEPARTURE"] = fields.get("Pressure_Alt _DEPARTURE", "")
        else:
            fields[f"Pressure_Alt_{prefix}"] = fields.get(f"Pressure_Alt_{prefix}", "")
        fields[f"Density_Alt_{prefix}"] = fields.get(f"Density_Alt_{prefix}", "")

        for k in ["TODA", "TODR", "LDA", "LDR", "ROC"]:
            fields[f"{k}_{prefix}"] = fields.get(f"{k}_{prefix}", "")

    put_leg("DEPARTURE", dep_icao, met_for("Departure"))
    put_leg("ARRIVAL", arr_icao, met_for("Arrival"))
    put_leg("ALTERNATE_1", alt1_icao, met_for("Alternate 1"))
    put_leg("ALTERNATE_2", alt2_icao, met_for("Alternate 2"))

    st.markdown("#### CG chart points used")
    st.write(
        f"Empty: {st_empty.weight_lb:.0f} lb @ {st_empty.cg_in:.1f} in | "
        f"Takeoff: {st_to.weight_lb:.0f} lb @ {st_to.cg_in:.1f} in | "
        f"Landing: {st_ldg.weight_lb:.0f} lb @ {st_ldg.cg_in:.1f} in"
    )

    if st.button("Generate filled PDF", type="primary"):
        writer = fill_pdf(template_bytes, fields)

        # Overlay CG chart on page 0
        gp = reader.pages[0]
        page_w = float(gp.mediabox.width)
        page_h = float(gp.mediabox.height)

        points = [
            {"label": "Empty",   "cg": float(st_empty.cg_in), "wt": float(st_empty.weight_lb), "rgb": (0.10, 0.60, 0.10)},
            {"label": "Takeoff", "cg": float(st_to.cg_in),    "wt": float(st_to.weight_lb),    "rgb": (0.10, 0.30, 0.85)},
            {"label": "Landing", "cg": float(st_ldg.cg_in),   "wt": float(st_ldg.weight_lb),   "rgb": (0.85, 0.20, 0.20)},
        ]
        overlay_bytes = make_overlay_pdf(page_w, page_h, points, legend_xy=(500, 320), marker_r=4)
        overlay_page = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
        writer.pages[0].merge_page(overlay_page)

        out = io.BytesIO()
        writer.write(out)
        out.seek(0)

        file_name = f"{reg}_PA28_MB_Perf.pdf" if reg else "PA28_MB_Perf.pdf"
        st.download_button("Download PDF", data=out.getvalue(), file_name=file_name, mime="application/pdf")
        st.success("PDF generated. Review before flight.")

