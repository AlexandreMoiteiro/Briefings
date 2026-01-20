import streamlit as st
import datetime as dt
import json
import math
import io
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import pytz
import requests
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

from reportlab.pdfgen import canvas
from reportlab.lib import colors


# ============================================================
# CONFIG
# ============================================================
PDF_TEMPLATE = "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
LISBON_TZ = pytz.timezone("Europe/Lisbon")
WIND_WINDOW_H = 1  # fixed ±1 hour

# Gist config
GIST_FILE_PA28 = "sevenair_pa28_fleet.json"  # must match your gist file name exactly

# PA-28 constants (fixed)
PA28 = {
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

LB_TO_KG = 0.45359237
USG_TO_L = 3.785411784


# ============================================================
# STYLE
# ============================================================
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

st.markdown('<div class="hdr">Piper PA-28 — Mass & Balance + Forecast + PDF</div>', unsafe_allow_html=True)


# ============================================================
# APPROVED AIRFIELDS DB (same idea as Tecnam)
# ============================================================
AERODROMES_DB = {
    "LPSO": {"name": "Ponte de Sôr", "lat": 39.2117, "lon": -8.0578, "elev_ft": 390.0,
             "runways": [{"id": "03", "qfu": 30.0, "toda": 1800.0, "lda": 1800.0},
                         {"id": "21", "qfu": 210.0, "toda": 1800.0, "lda": 1800.0}]},
    "LPEV": {"name": "Évora", "lat": 38.5297, "lon": -7.8919, "elev_ft": 807.0,
             "runways": [{"id": "01", "qfu": 10.0, "toda": 1300.0, "lda": 1300.0},
                         {"id": "19", "qfu": 190.0, "toda": 1300.0, "lda": 1300.0},
                         {"id": "07", "qfu": 70.0, "toda": 1300.0, "lda": 1300.0},
                         {"id": "25", "qfu": 250.0, "toda": 1300.0, "lda": 1300.0}]},
    "LPCB": {"name": "Castelo Branco", "lat": 39.8483, "lon": -7.4417, "elev_ft": 1251.0,
             "runways": [{"id": "16", "qfu": 160.0, "toda": 1460.0, "lda": 1460.0},
                         {"id": "34", "qfu": 340.0, "toda": 1460.0, "lda": 1460.0}]},
    "LPCS": {"name": "Cascais", "lat": 38.7256, "lon": -9.3553, "elev_ft": 326.0,
             "runways": [{"id": "17", "qfu": 170.0, "toda": 1400.0, "lda": 1400.0},
                         {"id": "35", "qfu": 350.0, "toda": 1400.0, "lda": 1400.0}]},
    "LPFR": {"name": "Faro", "lat": 37.0144, "lon": -7.9658, "elev_ft": 24.0,
             "runways": [{"id": "10", "qfu": 100.0, "toda": 2490.0, "lda": 2490.0},
                         {"id": "28", "qfu": 280.0, "toda": 2490.0, "lda": 2490.0}]},
    "LPMT": {"name": "Montijo", "lat": 38.7039, "lon": -9.0350, "elev_ft": 46.0,
             "runways": [{"id": "07", "qfu": 70.0, "toda": 2448.0, "lda": 2448.0},
                         {"id": "25", "qfu": 250.0, "toda": 2448.0, "lda": 2448.0}]},
}


# ============================================================
# Helpers
# ============================================================
def lbs_to_kg(lb: float) -> float:
    return float(lb) * LB_TO_KG

def usg_to_l(usg: float) -> float:
    return float(usg) * USG_TO_L

def fmt_wind(dir_deg: int, spd_kt: int) -> str:
    d = int(dir_deg) % 360
    if d == 0:
        d = 360
    return f"{d:03d}/{int(spd_kt):02d}"

def round_dir_10(deg: float) -> int:
    d = int(round(float(deg) / 10.0) * 10) % 360
    return 360 if d == 0 else d

def utc_hour(dt_utc: dt.datetime) -> dt.datetime:
    return dt_utc.replace(minute=0, second=0, microsecond=0, tzinfo=dt.timezone.utc)

def lerp(a, b, t): return a + (b - a) * t

def fmt_hm(total_min: int) -> str:
    if total_min <= 0:
        return "0min"
    h, m = divmod(int(total_min), 60)
    if h == 0:
        return f"{m}min"
    return f"{h}h" if m == 0 else f"{h}h{m:02d}min"

def best_runway_for_wind(runways: List[dict], wind_dir_from: int, wind_kt: int) -> dict:
    """
    Choose runway that maximizes headwind component.
    Headwind component = V * cos(angle), where angle between runway heading and wind-from direction.
    """
    wd = float(wind_dir_from) % 360.0
    v = float(wind_kt)

    best = None
    best_hw = -1e9
    for rw in runways:
        qfu = float(rw.get("qfu", 0.0)) % 360.0
        ang = math.radians((wd - qfu + 540) % 360 - 180)  # shortest signed angle
        headwind = v * math.cos(ang)
        # tie-break: longer TODA
        score = (headwind, float(rw.get("toda", 0.0)))
        if best is None or score > (best_hw, float(best.get("toda", 0.0))):
            best = rw
            best_hw = headwind
    return best or runways[0]


# ============================================================
# Open-Meteo
# ============================================================
@st.cache_data(ttl=900, show_spinner=False)
def om_hourly(lat: float, lon: float, start_iso: str, end_iso: str) -> dict:
    params = {
        "latitude": round(float(lat), 4),
        "longitude": round(float(lon), 4),
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,pressure_msl",
        "timezone": "UTC",
        "windspeed_unit": "kn",
        "temperature_unit": "celsius",
        "pressure_unit": "hPa",
        "start_date": start_iso,
        "end_date": end_iso,
    }
    r = requests.get(OPENMETEO_URL, params=params, timeout=25)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "detail": r.text}
    return r.json().get("hourly", {}) or {}

def parse_hours(hourly: dict) -> List[dict]:
    times = hourly.get("time", []) or []
    ws = hourly.get("wind_speed_10m", []) or []
    wd = hourly.get("wind_direction_10m", []) or []
    temp = hourly.get("temperature_2m", []) or []
    qnh = hourly.get("pressure_msl", []) or []
    out = []
    for i, t in enumerate(times):
        dtu = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        out.append({
            "dt": dtu,
            "wind_kt": float(ws[i]) if i < len(ws) and ws[i] is not None else None,
            "wind_dir": float(wd[i]) if i < len(wd) and wd[i] is not None else None,
            "temp_c": float(temp[i]) if i < len(temp) and temp[i] is not None else None,
            "qnh_hpa": float(qnh[i]) if i < len(qnh) and qnh[i] is not None else None,
        })
    return out

def pick_samples(hours: List[dict], target: dt.datetime, window_h: int = 1) -> List[dict]:
    lo = target - dt.timedelta(hours=window_h)
    hi = target + dt.timedelta(hours=window_h)
    return [h for h in hours if lo <= h["dt"] <= hi]

def vector_mean_wind(samples: List[dict]) -> Tuple[int, int]:
    # Wind FROM direction; use meteorological convention
    u_sum = 0.0
    v_sum = 0.0
    n = 0
    for s in samples:
        if s["wind_kt"] is None or s["wind_dir"] is None:
            continue
        spd = float(s["wind_kt"])
        d = float(s["wind_dir"]) % 360.0
        th = math.radians(d)
        # from-direction -> vector pointing opposite in u/v, but consistent use for averaging
        u_sum += -spd * math.sin(th)
        v_sum += -spd * math.cos(th)
        n += 1
    if n == 0:
        return 0, 0
    u = u_sum / n
    v = v_sum / n
    spd = math.sqrt(u*u + v*v)
    dir_from = (math.degrees(math.atan2(u, v)) + 180.0) % 360.0
    return round_dir_10(dir_from), int(round(spd))

def nearest(hours: List[dict], target: dt.datetime) -> Optional[dict]:
    if not hours:
        return None
    return min(hours, key=lambda h: abs(h["dt"] - target))


# ============================================================
# Gist load
# ============================================================
def gist_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gist_load_pa28(token: str, gist_id: str) -> Tuple[Optional[dict], Optional[str]]:
    try:
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(token), timeout=20)
        if r.status_code != 200:
            return None, f"GitHub error {r.status_code}: {r.text}"
        data = r.json()
        files = data.get("files", {}) or {}
        if GIST_FILE_PA28 not in files or files[GIST_FILE_PA28].get("content") is None:
            return None, f"File '{GIST_FILE_PA28}' not found in gist."
        return json.loads(files[GIST_FILE_PA28]["content"]), None
    except Exception as e:
        return None, str(e)


# ============================================================
# PDF + chart mapping
# ============================================================
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

def chart_point(cg_in: float, weight_lb: float) -> Tuple[float, float]:
    cg = float(cg_in)
    cg = max(82.0, min(93.0, cg))
    g0 = int(math.floor(cg))
    g1 = min(93, g0 + 1)

    def on_line(g: int, w: float) -> Tuple[float, float]:
        ref = CHART_REF[g]
        w1 = 1200.0
        w2 = float(ref["w2"])
        x1, y1 = ref["p1"]
        x2, y2 = ref["p2"]
        t = (w - w1) / (w2 - w1) if (w2 - w1) != 0 else 0.0
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    p0 = on_line(g0, weight_lb)
    if g1 == g0:
        return p0
    p1 = on_line(g1, weight_lb)
    frac = (cg - g0) / (g1 - g0)
    return (p0[0] + frac * (p1[0] - p0[0]), p0[1] + frac * (p1[1] - p0[1]))

def make_chart_overlay(empty_cg, empty_w, to_cg, to_w, ldg_cg, ldg_w) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))  # A4 points

    def draw_state(color_rgb, cg, w):
        xb, yb = chart_point(cg, 1200.0)
        x, y = chart_point(cg, w)
        c.setStrokeColorRGB(*color_rgb)
        c.setLineWidth(2)
        c.line(xb, yb, x, y)
        c.setFillColorRGB(*color_rgb)
        c.circle(x, y, 5, stroke=1, fill=1)

    draw_state((0.12, 0.70, 0.20), empty_cg, empty_w)  # Empty (green)
    draw_state((0.15, 0.35, 0.95), to_cg, to_w)        # Takeoff (blue)
    draw_state((0.90, 0.20, 0.20), ldg_cg, ldg_w)      # Landing (red)

    # Legend
    lx, ly = 470, 520
    c.setFont("Helvetica", 10)
    c.setFillColor(colors.black)
    c.drawString(lx, ly + 78, "Legend")
    items = [
        ("Empty", colors.Color(0.12, 0.70, 0.20)),
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

def read_pdf_bytes() -> bytes:
    p = Path(PDF_TEMPLATE)
    if not p.exists():
        raise FileNotFoundError(f"PDF template not found: {PDF_TEMPLATE}")
    return p.read_bytes()

def fill_pdf(template_bytes: bytes, fields: dict, overlay_first_page: Optional[bytes]) -> bytes:
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    root = reader.trailer["/Root"]
    if "/AcroForm" not in root:
        raise RuntimeError("Template has no AcroForm.")
    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    try:
        writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): True})
    except Exception:
        pass

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)

    if overlay_first_page:
        ov = PdfReader(io.BytesIO(overlay_first_page))
        writer.pages[0].merge_page(ov.pages[0])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ============================================================
# State init
# ============================================================
def init_state():
    st.session_state.setdefault("flight_date", dt.datetime.now(LISBON_TZ).date())
    st.session_state.setdefault("dep_time_utc", dt.time(19, 0))
    st.session_state.setdefault("arr_time_utc", dt.time(20, 0))

    # defaults exactly as requested
    st.session_state.setdefault("legs4", [
        {"role": "Departure", "icao": "LPSO"},
        {"role": "Arrival", "icao": "LPSO"},
        {"role": "Alternate 1", "icao": "LPEV"},
        {"role": "Alternate 2", "icao": "LPCB"},
    ])

    # MET values + manual toggles (set BEFORE widgets)
    for i in range(4):
        st.session_state.setdefault(f"manual_{i}", False)
        st.session_state.setdefault(f"temp_{i}", 15)   # int
        st.session_state.setdefault(f"qnh_{i}", 1013)  # int
        st.session_state.setdefault(f"wdir_{i}", 240)  # int
        st.session_state.setdefault(f"wspd_{i}", 8)    # int
        st.session_state.setdefault(f"rw_{i}", None)
        st.session_state.setdefault(f"toda_{i}", 0)
        st.session_state.setdefault(f"lda_{i}", 0)

    st.session_state.setdefault("fleet_pa28", {})
    st.session_state.setdefault("fleet_loaded", False)

init_state()


# ============================================================
# Load fleet from Gist once
# ============================================================
if not st.session_state.fleet_loaded:
    token = st.secrets.get("GITHUB_GIST_TOKEN", "")
    gist_id = st.secrets.get("GITHUB_GIST_ID_PA28", "")
    if token and gist_id:
        fleet, err = gist_load_pa28(token, gist_id)
        if fleet is not None:
            st.session_state.fleet_pa28 = fleet
        else:
            st.warning(f"PA-28 fleet gist not loaded: {err}")
    else:
        st.warning("Missing secrets: GITHUB_GIST_TOKEN and/or GITHUB_GIST_ID_PA28")
    st.session_state.fleet_loaded = True


# ============================================================
# Tabs
# ============================================================
tab1, tab2, tab3, tab4 = st.tabs(["Flight", "Airfields & Forecast", "Weight & Balance", "PDF"])


# ============================================================
# TAB 1 — Flight
# ============================================================
with tab1:
    c1, c2, c3 = st.columns([0.45, 0.275, 0.275])
    with c1:
        st.write("**Flight date (Europe/Lisbon)**")
        st.session_state.flight_date = st.date_input(
            "Flight date",
            value=st.session_state.flight_date,
            label_visibility="collapsed",
        )
    with c2:
        st.write("**Departure time (UTC)**")
        st.session_state.dep_time_utc = st.time_input(
            "Departure time",
            value=st.session_state.dep_time_utc,
            step=3600,
            label_visibility="collapsed",
        )
    with c3:
        st.write("**Arrival time (UTC)**")
        st.session_state.arr_time_utc = st.time_input(
            "Arrival time",
            value=st.session_state.arr_time_utc,
            step=3600,
            label_visibility="collapsed",
        )

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.subheader("Aircraft")

    regs = sorted(list(st.session_state.fleet_pa28.keys()))
    if not regs:
        st.error(f"No registrations loaded. Check gist secrets and that the file is named '{GIST_FILE_PA28}'.")
        regs = ["OE-KPD"]

    reg = st.selectbox("Registration", regs, key="reg_select")
    st.session_state["reg"] = reg

    ac = st.session_state.fleet_pa28.get(reg, {})
    ew = ac.get("empty_weight_lb")
    em = ac.get("empty_moment_inlb")

    a1, a2 = st.columns(2)
    with a1:
        st.number_input("Basic empty weight (lb)", value=float(ew) if ew else 0.0, disabled=True)
        st.caption(f"({lbs_to_kg(float(ew) if ew else 0.0):.1f} kg)")
    with a2:
        st.number_input("Empty moment (in-lb)", value=float(em) if em else 0.0, disabled=True)
        st.caption("Moment units: in-lb")


# ============================================================
# TAB 2 — Airfields & Forecast
# ============================================================
with tab2:
    st.subheader("Approved Airfields (DEP / ARR / ALT1 / ALT2) + Forecast (Open-Meteo)")

    icao_options = sorted(AERODROMES_DB.keys())

    def target_time_for_role(role: str) -> dt.datetime:
        # DEP uses departure time; ARR/ALT1/ALT2 use arrival time (NOT +1h)
        t = st.session_state.dep_time_utc if role == "Departure" else st.session_state.arr_time_utc
        return utc_hour(dt.datetime.combine(st.session_state.flight_date, t).replace(tzinfo=dt.timezone.utc))

    def fetch_leg(i: int):
        role = st.session_state.legs4[i]["role"]
        icao = st.session_state[f"icao_{i}"]
        ad = AERODROMES_DB[icao]

        start_iso = st.session_state.flight_date.strftime("%Y-%m-%d")
        hourly = om_hourly(ad["lat"], ad["lon"], start_iso, start_iso)
        if "error" in hourly:
            st.error(f"{icao}: Forecast error {hourly.get('error')} {hourly.get('detail','')}")
            return

        hours = parse_hours(hourly)
        target = target_time_for_role(role)

        samples = pick_samples(hours, target, window_h=WIND_WINDOW_H)
        if not samples:
            near = nearest(hours, target)
            samples = [near] if near else []

        wdir10, wspd = vector_mean_wind(samples)
        near = nearest(hours, target)

        if near and near.get("temp_c") is not None:
            st.session_state[f"temp_{i}"] = int(round(near["temp_c"]))
        if near and near.get("qnh_hpa") is not None:
            st.session_state[f"qnh_{i}"] = int(round(near["qnh_hpa"]))

        st.session_state[f"wdir_{i}"] = int(wdir10)
        st.session_state[f"wspd_{i}"] = int(wspd)

        st.success(f"{icao}: {target.strftime('%Y-%m-%d %H:00Z')} — Wind {fmt_wind(wdir10, wspd)}")

    if st.button("Fetch forecast for all legs", type="primary"):
        for i in range(4):
            if not st.session_state.get(f"manual_{i}", False):
                fetch_leg(i)

    st.markdown("<hr/>", unsafe_allow_html=True)

    for i, leg in enumerate(st.session_state.legs4):
        role = leg["role"]
        st.markdown(f"### {role}")

        # ICAO widget (default comes from session_state.legs4)
        default_icao = leg["icao"]
        idx = icao_options.index(default_icao) if default_icao in icao_options else 0
        icao = st.selectbox("ICAO", icao_options, index=idx, key=f"icao_{i}")
        st.session_state.legs4[i]["icao"] = icao

        ad = AERODROMES_DB[icao]
        st.caption(f"{ad['name']} — Elev {ad['elev_ft']:.0f} ft")

        t_used = target_time_for_role(role)
        st.code(f"Time used (UTC): {t_used.strftime('%Y-%m-%d %H:00Z')}", language="text")

        c1, c2, c3, c4, c5 = st.columns([0.16, 0.16, 0.17, 0.25, 0.26])

        with c1:
            st.checkbox("Manual MET", key=f"manual_{i}")
        with c2:
            if st.button(f"Fetch ({role})", key=f"fetch_{i}", disabled=bool(st.session_state.get(f"manual_{i}", False))):
                fetch_leg(i)
        with c3:
            # Runway autoselect based on wind
            wdir = int(st.session_state[f"wdir_{i}"])
            wspd = int(st.session_state[f"wspd_{i}"])
            best = best_runway_for_wind(ad["runways"], wdir, wspd)
            rw_ids = [r["id"] for r in ad["runways"]]
            default_rw = best["id"]
            default_rw_idx = rw_ids.index(default_rw) if default_rw in rw_ids else 0
            rw_id = st.selectbox("Runway", rw_ids, index=default_rw_idx, key=f"rw_{i}")
            rw = next(r for r in ad["runways"] if r["id"] == rw_id)
            st.session_state[f"toda_{i}"] = int(round(float(rw["toda"])))
            st.session_state[f"lda_{i}"] = int(round(float(rw["lda"])))
            st.session_state[f"qfu_{i}"] = int(round(float(rw.get("qfu", 0.0))))

        with c4:
            # FIX: all numeric types are INT (min/max/value/step)
            temp = st.number_input("OAT (°C)", value=int(st.session_state[f"temp_{i}"]), step=1, key=f"temp_{i}")
            qnh = st.number_input("QNH (hPa)", min_value=900, max_value=1050, value=int(st.session_state[f"qnh_{i}"]), step=1, key=f"qnh_{i}")
        with c5:
            wdir = st.number_input("Wind FROM (°)", min_value=0, max_value=360, value=int(st.session_state[f"wdir_{i}"]), step=1, key=f"wdir_{i}")
            wspd = st.number_input("Wind speed (kt)", min_value=0, value=int(st.session_state[f"wspd_{i}"]), step=1, key=f"wspd_{i}")

        # apply rounding to tens for stored use + display
        wdir10 = round_dir_10(int(wdir))
        st.session_state[f"wdir_{i}"] = wdir10

        st.markdown(
            f"<span class='pill'>Wind {fmt_wind(wdir10, int(wspd))}</span>"
            f"<span class='pill'>Temp {int(temp)}°C</span>"
            f"<span class='pill'>QNH {int(qnh)}</span>",
            unsafe_allow_html=True,
        )


# ============================================================
# TAB 3 — Weight & Balance
# ============================================================
with tab3:
    st.subheader("Weight & Balance")

    reg = st.session_state.get("reg", "")
    ac = st.session_state.fleet_pa28.get(reg, {})
    ew_lb = ac.get("empty_weight_lb")
    em_inlb = ac.get("empty_moment_inlb")

    if ew_lb is None or em_inlb is None:
        st.error("Empty weight / moment missing for this registration in the PA-28 gist.")
        st.stop()

    ew_lb = float(ew_lb)
    em_inlb = float(em_inlb)

    col1, col2, col3 = st.columns([0.36, 0.32, 0.32])
    with col1:
        st.markdown("#### Loads")
        front_lb = st.number_input("Pilot + front passenger (lb)", min_value=0.0, value=170.0, step=1.0)
        rear_lb  = st.number_input("Rear seats (lb)", min_value=0.0, value=0.0, step=1.0)
        bag_lb   = st.number_input("Baggage (lb)", min_value=0.0, value=0.0, step=1.0)
        fuel_usg = st.number_input("Fuel (USG)", min_value=0.0, max_value=float(PA28["max_fuel_usg"]), value=0.0, step=0.5)

        st.caption(f"Fuel: {fuel_usg:.1f} USG ({usg_to_l(fuel_usg):.1f} L)")

    with col2:
        st.markdown("#### Arms (in)")
        arm_front = st.number_input("Front seats arm (in)", value=float(PA28["arm_front_in"]), step=0.1)
        arm_rear  = st.number_input("Rear seats arm (in)",  value=float(PA28["arm_rear_in"]), step=0.1)
        arm_fuel  = st.number_input("Fuel arm (in)",        value=float(PA28["arm_fuel_in"]), step=0.1)
        arm_bag   = st.number_input("Baggage arm (in)",     value=float(PA28["arm_baggage_in"]), step=0.1)

    with col3:
        st.markdown("#### Fuel planning")
        gph = st.number_input("Fuel flow (USG/h)", min_value=5.0, max_value=20.0, value=10.0, step=0.5)  # default 10

        taxi_min = st.number_input("Start-up & taxi (min)", min_value=0, value=15, step=1)
        climb_min = st.number_input("Climb (min)", min_value=0, value=10, step=1)
        enrt_min = st.number_input("Enroute (min)", min_value=0, value=60, step=5)
        desc_min = st.number_input("Descent (min)", min_value=0, value=10, step=1)

        alt_min = st.number_input("Alternate (min)", min_value=0, value=45, step=5)
        reserve_min = 45

        def usg_from_min(m): return round(float(gph) * (float(m) / 60.0), 2)

        trip_min = int(climb_min) + int(enrt_min) + int(desc_min)
        trip_usg = usg_from_min(trip_min)
        cont_usg = round(0.05 * trip_usg, 2)
        cont_min = int(round(0.05 * trip_min))

        taxi_usg = usg_from_min(taxi_min)
        alt_usg = usg_from_min(alt_min)
        res_usg = usg_from_min(reserve_min)

        req_usg = round(taxi_usg + trip_usg + cont_usg + alt_usg + res_usg, 2)
        extra_usg = max(0.0, round(float(fuel_usg) - req_usg, 2))

        st.write(f"Required ramp fuel: **{req_usg:.2f} USG** ({usg_to_l(req_usg):.1f} L)")
        if float(fuel_usg) < req_usg:
            st.error("Fuel loaded is insufficient for the plan.")

    fuel_lb = float(fuel_usg) * PA28["fuel_density_lb_per_usg"]

    m_empty = em_inlb
    m_front = float(front_lb) * float(arm_front)
    m_rear  = float(rear_lb) * float(arm_rear)
    m_bag   = float(bag_lb) * float(arm_bag)
    m_fuel  = fuel_lb * float(arm_fuel)

    ramp_w = ew_lb + float(front_lb) + float(rear_lb) + float(bag_lb) + fuel_lb
    ramp_m = m_empty + m_front + m_rear + m_bag + m_fuel
    ramp_cg = ramp_m / ramp_w

    takeoff_w = ramp_w + PA28["taxi_allowance_lb"]
    takeoff_m = ramp_m + PA28["taxi_allowance_moment_inlb"]
    takeoff_cg = takeoff_m / takeoff_w

    # landing = takeoff - TRIP burn
    trip_burn_lb = trip_usg * PA28["fuel_density_lb_per_usg"]
    landing_w = takeoff_w - trip_burn_lb
    landing_m = takeoff_m - (trip_burn_lb * float(arm_fuel))
    landing_cg = landing_m / landing_w

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
        st.write(f"MTOW {PA28['mtow_lb']:.0f} lb → {'OK' if takeoff_w <= PA28['mtow_lb'] else 'OVER'}")

    st.session_state["_wb"] = {
        "ew_lb": ew_lb,
        "em_inlb": em_inlb,
        "empty_cg": em_inlb / ew_lb,
        "front_lb": float(front_lb),
        "rear_lb": float(rear_lb),
        "bag_lb": float(bag_lb),
        "fuel_usg": float(fuel_usg),
        "fuel_lb": fuel_lb,
        "arm_front": float(arm_front),
        "arm_rear": float(arm_rear),
        "arm_fuel": float(arm_fuel),
        "arm_bag": float(arm_bag),
        "ramp_w": ramp_w,
        "ramp_m": ramp_m,
        "ramp_cg": ramp_cg,
        "takeoff_w": takeoff_w,
        "takeoff_m": takeoff_m,
        "takeoff_cg": takeoff_cg,
        "landing_w": landing_w,
        "landing_m": landing_m,
        "landing_cg": landing_cg,
        "fuel_plan": {
            "taxi_min": int(taxi_min), "taxi_usg": taxi_usg,
            "trip_min": int(trip_min), "trip_usg": trip_usg,
            "cont_min": int(cont_min), "cont_usg": cont_usg,
            "alt_min": int(alt_min), "alt_usg": alt_usg,
            "res_min": int(reserve_min), "res_usg": res_usg,
            "req_usg": req_usg,
            "extra_usg": extra_usg,
        },
        "fuel_flow_gph": float(gph),
    }


# ============================================================
# TAB 4 — PDF
# ============================================================
with tab4:
    st.subheader("Generate PDF")

    if "_wb" not in st.session_state:
        st.info("Go to 'Weight & Balance' first.")
        st.stop()

    wb = st.session_state["_wb"]
    template_bytes = read_pdf_bytes()
    field_names = sorted((PdfReader(io.BytesIO(template_bytes)).get_fields() or {}).keys())

    with st.expander("PDF field names", expanded=False):
        st.write(field_names)

    # Build performance rows from tab2 state
    role_to_suffix = {
        "Departure": "DEPARTURE",
        "Arrival": "ARRIVAL",
        "Alternate 1": "ALTERNATE_1",
        "Alternate 2": "ALTERNATE_2",
    }

    perf_rows = []
    for i, leg in enumerate(st.session_state.legs4):
        role = leg["role"]
        icao = st.session_state.get(f"icao_{i}", leg["icao"])
        ad = AERODROMES_DB[icao]

        qfu = int(round(float(st.session_state.get(f"qfu_{i}", ad["runways"][0]["qfu"]))))
        toda = int(st.session_state.get(f"toda_{i}", int(ad["runways"][0]["toda"])))
        lda = int(st.session_state.get(f"lda_{i}", int(ad["runways"][0]["lda"])))

        temp = int(st.session_state.get(f"temp_{i}", 15))
        qnh = int(st.session_state.get(f"qnh_{i}", 1013))
        wdir = int(st.session_state.get(f"wdir_{i}", 240))
        wspd = int(st.session_state.get(f"wspd_{i}", 8))

        elev_ft = float(ad["elev_ft"])
        pa_ft = elev_ft + (1013 - qnh) * 30.0
        isa_temp = 15.0 - 2.0 * (elev_ft / 1000.0)
        da_ft = pa_ft + 120.0 * (temp - isa_temp)

        perf_rows.append({
            "role": role, "suffix": role_to_suffix[role],
            "icao": icao, "qfu": qfu,
            "elev_ft": int(round(elev_ft)),
            "qnh": qnh, "temp": temp,
            "wdir": wdir, "wspd": wspd,
            "pa_ft": int(round(pa_ft)),
            "da_ft": int(round(da_ft)),
            "toda": toda, "lda": lda,
            "todr": 0, "ldr": 0, "roc": 0,
        })

    # fields dict
    def w_with_kg(lb): return f"{lb:.0f} ({lbs_to_kg(lb):.0f} kg)"
    def fuel_with_l(usg): return f"{usg:.2f} ({usg_to_l(usg):.0f} L)"

    fields = {}
    fields["Date"] = st.session_state.flight_date.strftime("%d/%m/%Y")
    fields["Aircraft_Reg"] = st.session_state.get("reg", "")

    # LOADING DATA
    fields["Weight_EMPTY"] = w_with_kg(wb["ew_lb"])
    fields["Moment_EMPTY"] = f"{wb['em_inlb']:.0f}"
    fields["Datum_EMPTY"]  = f"{wb['empty_cg']:.1f}"

    fields["Weight_FRONT"] = w_with_kg(wb["front_lb"])
    fields["Moment_FRONT"] = f"{(wb['front_lb']*wb['arm_front']):.0f}"

    fields["Weight_REAR"] = w_with_kg(wb["rear_lb"])
    fields["Moment_REAR"] = f"{(wb['rear_lb']*wb['arm_rear']):.0f}"

    # fuel: show lb+kg AND also USG+L
    fields["Weight_FUEL"] = f"{wb['fuel_lb']:.0f} ({lbs_to_kg(wb['fuel_lb']):.0f} kg) / {fuel_with_l(wb['fuel_usg'])} USG"
    fields["Moment_FUEL"] = f"{(wb['fuel_lb']*wb['arm_fuel']):.0f}"

    fields["Weight_BAGGAGE"] = w_with_kg(wb["bag_lb"])
    fields["Moment_BAGGAGE"] = f"{(wb['bag_lb']*wb['arm_bag']):.0f}"

    fields["Weight_RAMP"] = w_with_kg(wb["ramp_w"])
    fields["Moment_RAMP"] = f"{wb['ramp_m']:.0f}"
    fields["Datum_RAMP"]  = f"{wb['ramp_cg']:.1f}"

    fields["Weight_TAKEOFF"] = w_with_kg(wb["takeoff_w"])
    fields["Moment_TAKEOFF"] = f"{wb['takeoff_m']:.0f}"
    fields["Datum_TAKEOFF"]  = f"{wb['takeoff_cg']:.1f}"

    # taxi allowance (if those exist)
    if "-760" in field_names:
        fields["-760"] = f"{int(PA28['taxi_allowance_moment_inlb'])}"
    if "95 5" in field_names:
        fields["95 5"] = f"{PA28['taxi_allowance_arm_in']:.1f}"

    # Fuel planning
    plan = wb["fuel_plan"]
    def fuel_cell(usg): return f"{usg:.2f} ({usg_to_l(usg):.0f} L)"

    fields["Start-up_and_Taxi_TIME"] = fmt_hm(plan["taxi_min"])
    fields["Start-up_and_Taxi_FUEL"] = fuel_cell(plan["taxi_usg"])
    fields["CLIMB_TIME"] = ""  # optional; you can split trip if you want later
    fields["CLIMB_FUEL"] = ""
    fields["ENROUTE_TIME"] = ""
    fields["ENROUTE_FUEL"] = ""
    fields["DESCENT_TIME"] = ""
    fields["DESCENT_FUEL"] = ""

    fields["TRIP_TIME"] = fmt_hm(plan["trip_min"])
    fields["TRIP_FUEL"] = fuel_cell(plan["trip_usg"])

    fields["Contingency_TIME"] = fmt_hm(plan["cont_min"])
    fields["Contingency_FUEL"] = fuel_cell(plan["cont_usg"])

    fields["ALTERNATE_TIME"] = fmt_hm(plan["alt_min"])
    fields["ALTERNATE_FUEL"] = fuel_cell(plan["alt_usg"])

    fields["RESERVE_TIME"] = fmt_hm(plan["res_min"])
    fields["RESERVE_FUEL"] = fuel_cell(plan["res_usg"])

    fields["REQUIRED_TIME"] = fmt_hm(plan["taxi_min"] + plan["trip_min"] + plan["cont_min"] + plan["alt_min"] + plan["res_min"])
    fields["REQUIRED_FUEL"] = fuel_cell(plan["req_usg"])

    fields["EXTRA_TIME"] = fmt_hm(int(round((plan["extra_usg"] / wb["fuel_flow_gph"]) * 60))) if wb["fuel_flow_gph"] > 0 else "0min"
    fields["EXTRA_FUEL"] = fuel_cell(plan["extra_usg"])

    total_usg = plan["req_usg"] + plan["extra_usg"]
    total_min = int(round((total_usg / wb["fuel_flow_gph"]) * 60)) if wb["fuel_flow_gph"] > 0 else 0
    fields["Total_TIME"] = fmt_hm(total_min)
    fields["Total_FUEL"] = fuel_cell(total_usg)

    # Airfields/perf fields
    for r in perf_rows:
        suf = r["suffix"]
        fields[f"Airfield_{suf}"] = r["icao"]
        fields[f"RWY_QFU_{suf}"] = f"{r['qfu']:03d}"
        fields[f"Elevation_{suf}"] = f"{r['elev_ft']}"
        fields[f"QNH_{suf}"] = f"{r['qnh']}"
        fields[f"Temperature_{suf}"] = f"{r['temp']}"
        fields[f"Wind_{suf}"] = f"{fmt_wind(r['wdir'], r['wspd'])}"
        fields[f"Pressure_Alt _{suf}"] = f"{r['pa_ft']}"
        fields[f"Density_Alt_{suf}"] = f"{r['da_ft']}"
        fields[f"TODA_{suf}"] = f"{r['toda']}"
        fields[f"LDA_{suf}"] = f"{r['lda']}"
        fields[f"TODR_{suf}"] = f"{r['todr']}"
        fields[f"LDR_{suf}"] = f"{r['ldr']}"
        fields[f"ROC_{suf}"] = f"{r['roc']}"

    overlay = make_chart_overlay(
        empty_cg=wb["empty_cg"], empty_w=wb["ew_lb"],
        to_cg=wb["takeoff_cg"], to_w=wb["takeoff_w"],
        ldg_cg=wb["landing_cg"], ldg_w=wb["landing_w"],
    )

    if st.button("Generate filled PDF", type="primary"):
        out_pdf = fill_pdf(template_bytes, fields, overlay_first_page=overlay)
        fname = f"{st.session_state.get('reg','PA28')}_PA28_MB_Perf.pdf".replace(" ", "_")
        st.download_button("Download PDF", data=out_pdf, file_name=fname, mime="application/pdf")
        st.success("PDF generated.")


