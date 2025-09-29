# Streamlit app – Tecnam P2008 (M&B + Performance) – EN + Windy
# Reqs: streamlit, pytz, pypdf, requests

import streamlit as st
import datetime as dt
from pathlib import Path
import pytz
import unicodedata
from math import cos, sin, radians, atan2, sqrt
from typing import Dict, List, Optional, Tuple
import io
import requests

# PDF
try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject
    PYPDF_OK = True
except Exception:
    PYPDF_OK = False

# ============ Basic setup ============
def ascii_safe(text):
    if not isinstance(text, str):
        return str(text)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

def fmt_hm(total_min: int) -> str:
    if total_min <= 0: return "0min"
    h, m = divmod(int(round(total_min)), 60)
    if h == 0: return f"{m}min"
    return f"{h}h" if m == 0 else f"{h}h{m:02d}min"

st.set_page_config(
    page_title="Tecnam P2008 – Mass & Balance & Performance (EN, Windy)",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
.block-container { max-width: 1120px !important; }
.mb-header{font-size:1.3rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:6px;margin-bottom:8px}
.section-title{font-weight:700;margin:14px 0 6px 0}
.mb-summary-row{display:flex;justify-content:space-between;margin:4px 0}
.ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
.mb-table{border-collapse:collapse;width:100%;font-size:.95rem}
.mb-table th{border-bottom:2px solid #cbd0d6;text-align:left}
.mb-table td{padding:4px 6px;border-bottom:1px dashed #e5e7ec;vertical-align:top}
.hint{font-size:.85rem;color:#6b7280}
.chip{display:inline-block;padding:0 6px;border-radius:10px;background:#f3f4f6;margin-left:6px}
.tight{margin-top:6px;margin-bottom:6px}
.kpi{display:flex;gap:10px;flex-wrap:wrap;margin:4px 0}
.kpi span{background:#f3f4f6;border-radius:10px;padding:2px 8px}
</style>
""", unsafe_allow_html=True)

# ============ Aircraft data & limits ============
AC = {
    "name": "Tecnam P2008 JC",
    "fuel_arm": 2.209, "pilot_arm": 1.800, "baggage_arm": 2.417,
    "max_takeoff_weight": 650.0,
    "max_fuel_volume": 124.0,  # L (aircraft tank max, not policy)
    "max_passenger_weight": 230.0,  # student+instructor
    "max_baggage_weight": 20.0,
    "cg_limits": (1.841, 1.978),
    "fuel_density": 0.72,
}
CROSSWIND_YELLOW = 8.0
CROSSWIND_RED = 15.0

# ============ Aerodrome database (coords + per-runway data) ============
# QFU is TRUE heading (deg) used for wind components (approx magnetic OK for components).
# Declared distances from AIP (TODA/LDA per runway end).
AERODROMES_DB: Dict[str, dict] = {
    "LPSO": {
        "name": "Ponte de Sor",
        "lat": 39.211545, "lon": -8.057787, "elev_ft": 390.0,
        "runways": {
            "03": {"qfu": 26.0,  "toda": 1800.0, "lda": 1800.0, "paved": True, "slope_pc": 0.0},
            "21": {"qfu": 206.0, "toda": 1800.0, "lda": 1800.0, "paved": True, "slope_pc": 0.0},
        }
    },
    "LPEV": {
        "name": "Évora",
        "lat": 38.53048, "lon": -7.89639, "elev_ft": 807.0,
        "runways": {
            # AD 2.13: 01: TORA 1260 TODA 1300 LDA 1245 | 19: TORA 1245 TODA 1300 LDA 1260
            # 07/25: 530/530
            "01": {"qfu": 6.0,   "toda": 1300.0, "lda": 1245.0, "paved": True, "slope_pc": 0.0},
            "19": {"qfu": 186.0, "toda": 1300.0, "lda": 1260.0, "paved": True, "slope_pc": 0.0},
            "07": {"qfu": 74.0,  "toda": 530.0,  "lda": 530.0,  "paved": True, "slope_pc": 0.0},
            "25": {"qfu": 254.0, "toda": 530.0,  "lda": 530.0,  "paved": True, "slope_pc": 0.0},
        }
    },
    "LPCB": {
        "name": "Castelo Branco",
        "lat": 39.848333, "lon": -7.441667, "elev_ft": 1251.0,
        "runways": {
            # AD 2.13: 16: TODA 1520 LDA 1460 | 34: TODA 1520 (starter ext) LDA 1460
            "16": {"qfu": 158.0, "toda": 1520.0, "lda": 1460.0, "paved": True, "slope_pc": 0.0},
            "34": {"qfu": 338.0, "toda": 1520.0, "lda": 1460.0, "paved": True, "slope_pc": 0.0},
        }
    },
}
DEFAULT_LEGS = [
    {"role": "Departure", "icao": "LPSO"},
    {"role": "Arrival",   "icao": "LPEV"},
    {"role": "Alternate", "icao": "LPCB"},
]

# ============ Performance tables (AFM extracts) ============
TAKEOFF = {
    0:     {"GR":{-25:144, 0:182, 25:224, 50:272, "ISA":207}, "50ft":{-25:304,0:379,25:463,50:557,"ISA":428}},
    1000:  {"GR":{-25:157, 0:198, 25:245, 50:297, "ISA":222}, "50ft":{-25:330,0:412,25:503,50:605,"ISA":458}},
    2000:  {"GR":{-25:172, 0:216, 25:267, 50:324, "ISA":238}, "50ft":{-25:359,0:448,25:547,50:658,"ISA":490}},
    3000:  {"GR":{-25:188, 0:236, 25:292, 50:354, "ISA":256}, "50ft":{-25:391,0:487,25:595,50:717,"ISA":525}},
    4000:  {"GR":{-25:205, 0:258, 25:319, 50:387, "ISA":275}, "50ft":{-25:425,0:530,25:648,50:780,"ISA":562}},
    5000:  {"GR":{-25:224, 0:283, 25:349, 50:423, "ISA":295}, "50ft":{-25:463,0:578,25:706,50:850,"ISA":603}},
    6000:  {"GR":{-25:246, 0:309, 25:381, 50:463, "ISA":318}, "50ft":{-25:505,0:630,25:770,50:927,"ISA":646}},
    7000:  {"GR":{-25:269, 0:339, 25:418, 50:507, "ISA":342}, "50ft":{-25:551,0:687,25:840,50:1011,"ISA":693}},
    8000:  {"GR":{-25:295, 0:371, 25:458, 50:555, "ISA":368}, "50ft":{-25:601,0:750,25:917,50:1104,"ISA":744}},
    9000:  {"GR":{-25:323, 0:407, 25:502, 50:609, "ISA":397}, "50ft":{-25:657,0:819,25:1002,50:1205,"ISA":800}},
    10000: {"GR":{-25:354, 0:446, 25:551, 50:668, "ISA":428}, "50ft":{-25:718,0:895,25:1095,50:1318,"ISA":859}},
}
LANDING = {
    0:     {"GR":{-25:149,0:164,25:179,50:194,"ISA":173}, "50ft":{-25:358,0:373,25:388,50:403,"ISA":382}},
    1000:  {"GR":{-25:154,0:170,25:186,50:201,"ISA":178}, "50ft":{-25:363,0:379,25:395,50:410,"ISA":387}},
    2000:  {"GR":{-25:160,0:176,25:192,50:209,"ISA":183}, "50ft":{-25:369,0:385,25:401,50:418,"ISA":392}},
    3000:  {"GR":{-25:166,0:183,25:200,50:216,"ISA":189}, "50ft":{-25:375,0:392,25:409,50:425,"ISA":398}},
    4000:  {"GR":{-25:172,0:190,25:207,50:225,"ISA":195}, "50ft":{-25:381,0:399,25:416,50:434,"ISA":404}},
    5000:  {"GR":{-25:179,0:197,25:215,50:233,"ISA":201}, "50ft":{-25:388,0:406,25:424,50:442,"ISA":410}},
    6000:  {"GR":{-25:186,0:205,25:223,50:242,"ISA":207}, "50ft":{-25:395,0:414,25:432,50:451,"ISA":416}},
    7000:  {"GR":{-25:193,0:212,25:232,50:251,"ISA":213}, "50ft":{-25:402,0:421,25:441,50:460,"ISA":422}},
    8000:  {"GR":{-25:200,0:221,25:241,50:261,"ISA":220}, "50ft":{-25:410,0:430,25:450,50:470,"ISA":429}},
    9000:  {"GR":{-25:208,0:229,25:250,50:271,"ISA":227}, "50ft":{-25:417,0:438,25:459,50:480,"ISA":436}},
    10000: {"GR":{-25:217,0:238,25:260,50:282,"ISA":234}, "50ft":{-25:426,0:447,25:469,50:491,"ISA":443}},
}
ROC = {
    650:{0:{-25:951,0:805,25:675,50:557,"ISA":725},2000:{-25:840,0:696,25:568,50:453,"ISA":638},4000:{-25:729,0:588,25:462,50:349,"ISA":551},6000:{-25:619,0:480,25:357,50:245,"ISA":464},8000:{-25:509,0:373,25:251,50:142,"ISA":377},10000:{-25:399,0:266,25:146,50:39,"ISA":290},12000:{-25:290,0:159,25:42,50:-64,"ISA":204},14000:{-25:181,0:53,25:-63,50:-166,"ISA":117}},
    600:{0:{-25:1067,0:913,25:776,50:652,"ISA":829},2000:{-25:950,0:799,25:664,50:542,"ISA":737},4000:{-25:833,0:685,25:552,50:433,"ISA":646},6000:{-25:717,0:571,25:441,50:324,"ISA":555},8000:{-25:602,0:458,25:330,50:215,"ISA":463},10000:{-25:486,0:345,25:220,50:106,"ISA":372},12000:{-25:371,0:233,25:110,50:-2,"ISA":280},14000:{-25:257,0:121,25:0,50:-109,"ISA":189}},
    550:{0:{-25:1201,0:1038,25:892,50:760,"ISA":948},2000:{-25:1077,0:916,25:773,50:644,"ISA":851},4000:{-25:953,0:795,25:654,50:527,"ISA":754},6000:{-25:830,0:675,25:536,50:411,"ISA":657},8000:{-25:707,0:555,25:419,50:296,"ISA":560},10000:{-25:584,0:435,25:301,50:181,"ISA":462},12000:{-25:462,0:315,25:184,50:66,"ISA":365},14000:{-25:341,0:196,25:68,50:-48,"ISA":268}},
}
VY = {650:{0:70,2000:69,4000:68,6000:67,8000:65,10000:64,12000:63,14000:62},
      600:{0:70,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:62},
      550:{0:69,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:61}}

# ============ Math helpers ============
def clamp(v, lo, hi): return max(lo, min(hi, v))

def interp1(x, x0, x1, y0, y1):
    if x1 == x0: return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def bilinear(pa, temp, table, key):
    pas = sorted(table.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])
    temps = [-25, 0, 25, 50]
    t = clamp(temp, temps[0], temps[-1])
    if t <= 0: t0, t1 = -25, 0
    elif t <= 25: t0, t1 = 0, 25
    else: t0, t1 = 25, 50
    v00 = table[p0][key][t0]; v01 = table[p0][key][t1]
    v10 = table[p1][key][t0]; v11 = table[p1][key][t1]
    v0 = interp1(t, t0, t1, v00, v01)
    v1 = interp1(t, t0, t1, v10, v11)
    return interp1(pa_c, p0, p1, v0, v1)

def roc_interp(pa, temp, weight):
    w = clamp(weight, 550.0, 650.0)
    def roc_for_w(w_):
        tab = ROC[int(w_)]
        pas = sorted(tab.keys())
        pa_c = clamp(pa, pas[0], pas[-1])
        p0 = max([p for p in pas if p <= pa_c])
        p1 = min([p for p in pas if p >= pa_c])
        temps = [-25, 0, 25, 50]
        t = clamp(temp, temps[0], temps[-1])
        if t <= 0: t0, t1 = -25, 0
        elif t <= 25: t0, t1 = 0, 25
        else: t0, t1 = 25, 50
        v00 = tab[p0][t0]; v01 = tab[p0][t1]
        v10 = tab[p1][t0]; v11 = tab[p1][t1]
        v0 = interp1(t, t0, t1, v00, v01)
        v1 = interp1(t, t0, t1, v10, v11)
        return interp1(pa_c, p0, p1, v0, v1)
    if w <= 600: return interp1(w, 550, 600, roc_for_w(550), roc_for_w(600))
    return interp1(w, 600, 650, roc_for_w(600), roc_for_w(650))

def vy_interp(pa, weight):
    w_choice = 550 if weight <= 575 else (600 if weight <= 625 else 650)
    table = VY[w_choice]
    pas = sorted(table.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])
    return interp1(pa_c, p0, p1, table[p0], table[p1])

def wind_components(runway_qfu_deg, wind_dir_deg, wind_speed):
    if runway_qfu_deg is None or wind_dir_deg is None or wind_speed is None:
        return 0.0, 0.0
    diff = ((wind_dir_deg - runway_qfu_deg + 180) % 360) - 180
    hw = wind_speed * cos(radians(diff))
    cw = wind_speed * sin(radians(diff))
    # clamp numerically
    hw = max(-abs(wind_speed), min(abs(wind_speed), hw))
    cw = max(-abs(wind_speed), min(abs(wind_speed), cw))
    return hw, cw

def to_corrections_takeoff_gr(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = float(ground_roll)
    if headwind_kt >= 0: gr -= 5.0 * headwind_kt
    else: gr += 15.0 * abs(headwind_kt)
    if paved: gr *= 0.90
    slope_pc = clamp(slope_pc, -5.0, 5.0)
    gr *= (1.0 + 0.07 * slope_pc)
    return max(gr, 0.0)

def ldg_corrections_gr(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = float(ground_roll)
    if headwind_kt >= 0: gr -= 4.0 * headwind_kt
    else: gr += 13.0 * abs(headwind_kt)
    if paved: gr *= 0.90
    slope_pc = clamp(slope_pc, -5.0, 5.0)
    gr *= (1.0 - 0.03 * slope_pc)
    return max(gr, 0.0)

# ============ Windy integration ============
WINDY_ENDPOINT = "https://api.windy.com/api/point-forecast/v2"
DEFAULT_MODEL = "IconEU"  # per your preference

def _dir_speed_from_uv(u: float, v: float) -> Tuple[float, float]:
    # From u (m/s East) and v (m/s North) to direction-from (deg) and speed (kt)
    spd_ms = sqrt(u*u + v*v)
    # Direction from which the wind blows:
    # meteorological convention (0 = from North, clockwise)
    # Using atan2(-u, -v) yields direction-from
    deg = (atan2(-u, -v) * 180.0 / 3.141592653589793) % 360.0
    spd_kt = spd_ms * 1.9438444924406046
    return deg, spd_kt

def windy_point_forecast(lat: float, lon: float, dt_utc: dt.datetime, model: str = DEFAULT_MODEL):
    api_key = st.secrets.get("WINDY_API_KEY", "")
    if not api_key:
        raise RuntimeError("WINDY_API_KEY missing in Streamlit secrets")

    payload = {
        "lat": lat, "lon": lon,
        "model": model,
        "parameters": ["wind", "temp", "pressure"],
        "levels": ["surface"],
        "key": api_key,
    }
    r = requests.post(WINDY_ENDPOINT, json=payload, timeout=20)
    r.raise_for_status()
    data = r.json()

    # timestamps in milliseconds since epoch (UTC); pick nearest to selected dt_utc
    ts = data.get("ts") or data.get("timestamp")  # "ts" expected
    if not ts:
        raise RuntimeError("Windy response missing 'ts' timestamps")
    target_ms = int(dt_utc.replace(minute=0, second=0, microsecond=0).timestamp() * 1000)
    idx = min(range(len(ts)), key=lambda i: abs(ts[i] - target_ms))

    u = data["wind_u-surface"][idx]
    v = data["wind_v-surface"][idx]
    temp_k = data["temp-surface"][idx]
    pres_pa = data["pressure-surface"][idx]

    wind_dir, wind_kt = _dir_speed_from_uv(u, v)
    temp_c = float(temp_k) - 273.15  # K -> °C
    qnh_hpa = float(pres_pa) / 100.0  # Pa -> hPa (QNH ~ MSL pressure)

    return {
        "idx": idx,
        "timestamp_ms": ts[idx],
        "wind_dir": wind_dir,
        "wind_kt": wind_kt,
        "temp_c": temp_c,
        "qnh": qnh_hpa,
        "model": model,
    }

def pick_best_runway(icao: str, wind_dir: float, wind_kt: float):
    ad = AERODROMES_DB.get(icao.upper())
    if not ad: return None
    best = None
    for rwy_id, rwy in ad["runways"].items():
        hw, cw = wind_components(rwy["qfu"], wind_dir, wind_kt)
        item = {
            "id": rwy_id, "qfu": rwy["qfu"],
            "toda": rwy["toda"], "lda": rwy["lda"],
            "paved": rwy["paved"], "slope_pc": rwy["slope_pc"],
            "hw": hw, "cw": cw
        }
        if best is None:
            best = item
        else:
            # prefer max headwind; if both tailwind, prefer less tailwind magnitude; tie-breaker: less |cw|
            if (item["hw"] >= 0 and (best["hw"] < 0 or item["hw"] > best["hw"])) \
               or (item["hw"] < 0 and best["hw"] < 0 and item["hw"] > best["hw"]) \
               or (abs(item["hw"] - best["hw"]) < 1e-6 and abs(item["cw"]) < abs(best["cw"])):
                best = item
    return best

def cw_class(cw_abs: float) -> str:
    if cw_abs > CROSSWIND_RED: return "bad"
    if cw_abs >= CROSSWIND_YELLOW: return "warn"
    return "ok"

# ============ State bootstrapping ============
if "legs" not in st.session_state:
    st.session_state.legs = []
    for i, leg in enumerate(DEFAULT_LEGS):
        icao = leg["icao"]
        ad = AERODROMES_DB[icao]
        # seed wind/met with zeros; users can fetch via Windy
        st.session_state.legs.append({
            "role": leg["role"],
            "icao": icao,
            "elev_ft": ad["elev_ft"],
            "wind_dir": 0.0,
            "wind_kt": 0.0,
            "qnh": 1013.0,
            "temp": 15.0,
            # selected runway end (auto after fetch)
            "rwy": None,     # "03", etc.
            "qfu": None,
            "toda": None,
            "lda": None,
            "paved": True,
            "slope_pc": 0.0,
        })

# ============ Header ============
st.markdown('<div class="mb-header">Tecnam P2008 – Mass & Balance & Performance</div>', unsafe_allow_html=True)

# ============ Top row: Time & Weather (Windy) ============
tcol1, tcol2, tcol3 = st.columns([0.44, 0.28, 0.28])
with tcol1:
    st.markdown("### Weather (Windy)")
    # UTC date + hour (hourly only)
    today_utc = dt.datetime.now(dt.timezone.utc).date()
    sel_date = st.date_input("UTC date", value=today_utc)
    sel_hour = st.selectbox("UTC hour (Windy is hourly)", options=list(range(0,24)), index=dt.datetime.now(dt.timezone.utc).hour)
with tcol2:
    model = st.selectbox("Model", options=["IconEU","ECMWF","GFS"], index=0)
    st.caption("Default is ICON-EU, per your preference.")
with tcol3:
    if st.button("Fetch forecast from Windy for all legs", type="primary"):
        # Update all legs and selected runway using Windy
        when = dt.datetime(sel_date.year, sel_date.month, sel_date.day, sel_hour, 0, 0, tzinfo=dt.timezone.utc)
        try:
            for i, leg in enumerate(st.session_state.legs):
                icao = leg["icao"]
                ad = AERODROMES_DB[icao]
                met = windy_point_forecast(ad["lat"], ad["lon"], when, model=model)
                # round sensible values
                wind_dir = float(round(met["wind_dir"])) % 360
                wind_kt = float(round(met["wind_kt"]))
                temp_c = float(round(met["temp_c"]))
                qnh = float(round(met["qnh"], 1))
                # pick runway
                best = pick_best_runway(icao, wind_dir, wind_kt)
                # write to session before widgets are created; then rerun
                leg.update({
                    "wind_dir": wind_dir, "wind_kt": wind_kt,
                    "temp": temp_c, "qnh": qnh,
                    "rwy": best["id"] if best else None,
                    "qfu": best["qfu"] if best else None,
                    "toda": best["toda"] if best else None,
                    "lda": best["lda"] if best else None,
                    "paved": True if best is None else AERODROMES_DB[icao]["runways"][best["id"]]["paved"],
                    "slope_pc": 0.0 if best is None else AERODROMES_DB[icao]["runways"][best["id"]]["slope_pc"],
                })
            st.rerun()
        except Exception as e:
            st.error(f"Windy fetch failed: {e}")

# ============ Main columns ============
left, _, right = st.columns([0.42,0.02,0.56], gap="large")

# ----- Left: Weight & balance -----
with left:
    st.markdown("### Weight & balance (inputs)")
    ew = st.number_input("Empty weight (kg)", min_value=0.0, value=0.0, step=1.0)
    ew_moment = st.number_input("Empty weight moment (kg·m)", min_value=0.0, value=0.0, step=0.1)
    student = st.number_input("Student weight (kg)", min_value=0.0, value=0.0, step=1.0)
    instructor = st.number_input("Instructor weight (kg)", min_value=0.0, value=0.0, step=1.0)
    baggage = st.number_input("Baggage (kg)", min_value=0.0, value=0.0, step=1.0)
    fuel_l = st.number_input("Fuel (L) for M&B", min_value=0.0, value=0.0, step=1.0)

    # immediate exceedance warnings
    if student + instructor > AC["max_passenger_weight"]:
        st.warning(f"Passengers exceed limit: {student+instructor:.0f} kg > {AC['max_passenger_weight']:.0f} kg")
    if baggage > AC["max_baggage_weight"]:
        st.warning(f"Baggage exceeds limit: {baggage:.0f} kg > {AC['max_baggage_weight']:.0f} kg")
    if fuel_l > AC["max_fuel_volume"]:
        st.warning(f"Fuel volume exceeds tank capacity: {fuel_l:.0f} L > {AC['max_fuel_volume']:.0f} L")

    pilot = student + instructor
    fuel_wt = fuel_l * AC['fuel_density']
    m_empty = ew_moment
    m_pilot = pilot * AC['pilot_arm']
    m_bag = baggage * AC['baggage_arm']
    m_fuel = fuel_wt * AC['fuel_arm']
    total_weight = ew + pilot + baggage + fuel_wt
    total_moment = m_empty + m_pilot + m_bag + m_fuel
    cg = (total_moment/total_weight) if total_weight>0 else 0.0

    remaining_by_mtow = max(0.0, AC['max_takeoff_weight'] - (ew + pilot + baggage + fuel_wt))
    remaining_by_tank = max(0.0, AC['max_fuel_volume']*AC['fuel_density'] - fuel_wt)
    remaining_fuel_weight = min(remaining_by_mtow, remaining_by_tank)
    remaining_fuel_l = remaining_fuel_weight / AC['fuel_density']
    limit_label = "Tank" if remaining_by_tank < remaining_by_mtow else "MTOW"

    def w_color(val, limit):
        if val > limit: return 'bad'
        if val > 0.95*limit: return 'warn'
        return 'ok'
    def cg_color_val(cg_val, limits):
        lo, hi = limits
        margin = 0.05*(hi-lo)
        if cg_val<lo or cg_val>hi: return 'bad'
        if cg_val<lo+margin or cg_val>hi-margin: return 'warn'
        return 'ok'

    st.markdown("#### Summary")
    st.markdown(f"<div class='mb-summary-row'><div>Possible extra fuel</div><div><b>{remaining_fuel_l:.1f} L</b> <span class='hint'>(limited by <i>{limit_label}</i>)</span></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>Total weight</div><div class='{w_color(total_weight, AC['max_takeoff_weight'])}'><b>{total_weight:.1f} kg</b><span class='chip'>≤ {AC['max_takeoff_weight']:.0f}</span></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>Total moment</div><div><b>{total_moment:.2f} kg·m</b></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>CG</div><div class='{cg_color_val(cg, AC['cg_limits'])}'><b>{cg:.3f} m</b><span class='chip'>{AC['cg_limits'][0]:.3f} – {AC['cg_limits'][1]:.3f} m</span></div></div>", unsafe_allow_html=True)

    # M&B table
    rows = [("Empty weight", ew, (ew_moment/ew if ew>0 else 0.0), m_empty),
            ("Fuel", fuel_wt, AC['fuel_arm'], m_fuel),
            ("Pilot & Passenger", pilot, AC['pilot_arm'], m_pilot),
            ("Baggage", baggage, AC['baggage_arm'], m_bag)]
    tbl = ("<table class='mb-table tight'><tr>"
           "<th>Item</th><th>Weight (kg)</th><th>Arm (m)</th><th>Moment (kg·m)</th></tr>"
           + "".join([f"<tr><td>{n}</td><td>{w:.1f}</td><td>{a:.3f}</td><td>{m:.2f}</td></tr>" for n,w,a,m in rows])
           + f"<tr><td><b>Total</b></td><td><b>{total_weight:.1f}</b></td><td><b>{cg:.3f}</b></td><td><b>{total_moment:.2f}</b></td></tr></table>")
    st.markdown(tbl, unsafe_allow_html=True)

# ----- Right: Aerodromes & performance -----
with right:
    st.markdown("### Aerodromes & performance")

    perf_rows = []
    slope_warn = False

    for i, leg in enumerate(st.session_state.legs):
        icao = leg["icao"]
        ad = AERODROMES_DB[icao]
        with st.expander(f"{leg['role']} – {icao} ({AERODROMES_DB[icao]['name']})", expanded=(i==0)):
            # Aerodrome selector (from DB)
            icao_sel = st.selectbox("Aerodrome", options=list(AERODROMES_DB.keys()),
                                    index=list(AERODROMES_DB.keys()).index(icao), key=f"icao_{i}")
            if icao_sel != icao:
                st.session_state.legs[i]["icao"] = icao_sel
                st.session_state.legs[i]["elev_ft"] = AERODROMES_DB[icao_sel]["elev_ft"]
                st.session_state.legs[i]["rwy"] = None
                st.session_state.legs[i]["qfu"] = None
                st.session_state.legs[i]["toda"] = None
                st.session_state.legs[i]["lda"] = None
                st.rerun()
            icao = icao_sel
            ad = AERODROMES_DB[icao]

            # Met (editable; Windy can prefill)
            wind_dir = st.number_input("Wind direction (deg FROM)", 0.0, 360.0, float(leg["wind_dir"]), 1.0, key=f"wdir_{i}")
            wind_kt  = st.number_input("Wind speed (kt)", min_value=0.0, value=float(leg["wind_kt"]), step=1.0, key=f"wspd_{i}")
            temp     = st.number_input("Temperature (°C)", -40.0, 60.0, float(leg["temp"]), 0.1, key=f"temp_{i}")
            qnh      = st.number_input("QNH (hPa)", 900.0, 1050.0, float(leg["qnh"]), 0.1, key=f"qnh_{i}")

            # Auto-pick runway if not set (or if user changed wind)
            if leg["rwy"] is None and wind_kt is not None:
                best = pick_best_runway(icao, wind_dir, wind_kt)
                if best:
                    st.session_state.legs[i].update({
                        "rwy": best["id"], "qfu": best["qfu"],
                        "toda": best["toda"], "lda": best["lda"],
                        "paved": best["paved"], "slope_pc": best["slope_pc"],
                    })
                    st.rerun()

            # Show chosen runway (read-only) and derive declared distances/QFU
            if leg["rwy"] is not None:
                rwy = ad["runways"][leg["rwy"]]
                qfu = rwy["qfu"]; toda_av = rwy["toda"]; lda_av = rwy["lda"]
            else:
                # fallback: first runway in DB
                first_id, first = next(iter(ad["runways"].items()))
                st.session_state.legs[i]["rwy"] = first_id
                st.session_state.legs[i]["qfu"] = first["qfu"]
                st.session_state.legs[i]["toda"] = first["toda"]
                st.session_state.legs[i]["lda"] = first["lda"]
                qfu, toda_av, lda_av = first["qfu"], first["toda"], first["lda"]

            st.info(
                f"**Selected runway:** {st.session_state.legs[i]['rwy']}  •  "
                f"QFU **{int(round(qfu)):03d}°**  •  "
                f"TODA **{toda_av:.0f} m**  •  LDA **{lda_av:.0f} m**",
                icon="🧭"
            )

            paved = True
            slope_pc = 0.0
            if abs(slope_pc) > 3.0: slope_warn = True

            # PA/DA (per your formula)
            # PA = elev_ft + (1013 − QNH) * 30
            elev = ad["elev_ft"]
            pa_ft = elev + (1013.0 - qnh) * 30.0
            isa_temp = 15 - 2*(elev/1000)  # ISA from elevation
            da_ft = pa_ft + (120*(temp - isa_temp))

            # Interpolations (tabulated by PA & OAT)
            to_gr_tab  = bilinear(pa_ft, temp, TAKEOFF, 'GR')
            to_50_tab  = bilinear(pa_ft, temp, TAKEOFF, '50ft')
            ldg_gr_tab = bilinear(pa_ft, temp, LANDING, 'GR')
            ldg_50_tab = bilinear(pa_ft, temp, LANDING, '50ft')

            # Corrections in ground roll
            hw, cw = wind_components(qfu, wind_dir, wind_kt)
            to_gr_corr  = to_corrections_takeoff_gr(to_gr_tab, hw, paved=paved, slope_pc=slope_pc)
            ldg_gr_corr = ldg_corrections_gr(ldg_gr_tab, hw, paved=paved, slope_pc=slope_pc)

            # ROC & Vy
            roc_val = roc_interp(pa_ft, temp, total_weight) if total_weight>0 else 0.0
            vy_val  = vy_interp(pa_ft, total_weight) if total_weight>0 else 0.0

            # Percentage of runway used
            tod_pct = (to_50_tab / toda_av * 100.0) if toda_av > 0 else 0.0
            ldg_pct = (ldg_50_tab / lda_av * 100.0) if lda_av > 0 else 0.0

            # Crosswind status chip
            cw_abs = abs(cw)
            cw_cls = cw_class(cw_abs)
            cw_label = f"<span class='{cw_cls}'>XW {cw_abs:.0f} kt</span>"
            hw_label = f"HW {max(0.0, hw):.0f} kt" if hw >= 0 else f"TW {abs(hw):.0f} kt"

            st.markdown(
                f"<div class='kpi'><span>{hw_label}</span><span>{cw_label}</span>"
                f"<span>PA {pa_ft:.0f} ft / DA {da_ft:.0f} ft</span>"
                f"<span>Vy {vy_val:.0f} kt</span><span>ROC {roc_val:.0f} ft/min</span></div>",
                unsafe_allow_html=True
            )

            # collect row
            perf_rows.append({
                "role": leg["role"], "icao": icao, "qfu": qfu,
                "elev_ft": elev, "qnh": qnh, "temp": temp,
                "pa_ft": pa_ft, "da_ft": da_ft, "isa_temp": isa_temp,
                "to_gr": to_gr_corr, "to_50": to_50_tab,
                "ldg_gr": ldg_gr_corr, "ldg_50": ldg_50_tab,
                "toda_av": toda_av, "lda_av": lda_av,
                "hw_comp": hw, "cw_comp": cw, "paved": paved, "slope_pc": slope_pc,
                "roc": roc_val, "vy": vy_val,
                "wind_dir": wind_dir, "wind_kt": wind_kt,
                "tod_pct": tod_pct, "ldg_pct": ldg_pct,
            })

    if slope_warn:
        st.warning("Runway slope > 3% — confirm values; corrections may be large.")

# ===== Performance summary =====
st.markdown("### Performance summary")
for r in perf_rows:
    r['tod_ok'] = r['to_50'] <= r['toda_av']
    r['ldg_ok'] = r['ldg_50'] <= r['lda_av']
    r['tod_margin'] = r['toda_av'] - r['to_50']
    r['ldg_margin'] = r['lda_av'] - r['ldg_50']

def fmt(v): return f"{v:.0f}" if isinstance(v, (int,float)) else str(v)
def status_cell(ok, margin, pct=None):
    cls = 'ok' if ok else 'bad'
    sign = '+' if margin >= 0 else '−'
    pct_str = f" • {pct:.0f}%" if (pct is not None and pct>0) else ""
    return f"<span class='{cls}'>{'OK' if ok else 'NOK'} ({sign}{abs(margin):.0f} m){pct_str}</span>"

st.markdown(
    "<table class='mb-table'><tr>"
    "<th>Leg/Aerodrome</th><th>QFU</th><th>PA/DA ft</th>"
    "<th>TODR 50ft</th><th>TODA</th><th>Takeoff fit</th>"
    "<th>LDR 50ft</th><th>LDA</th><th>Landing fit</th>"
    "<th>Wind (H/C)</th><th>ROC (ft/min)</th><th>Vy (kt)</th>"
    "</tr>" +
    "".join([
        f"<tr>"
        f"<td>{r['role']} {r['icao']}</td>"
        f"<td>{fmt(r['qfu'])}</td>"
        f"<td>{fmt(r['pa_ft'])}/{fmt(r['da_ft'])}</td>"
        f"<td>{fmt(r['to_50'])}</td><td>{fmt(r['toda_av'])}</td>"
        f"<td>{status_cell(r['tod_ok'], r['tod_margin'], r['tod_pct'])}</td>"
        f"<td>{fmt(r['ldg_50'])}</td><td>{fmt(r['lda_av'])}</td>"
        f"<td>{status_cell(r['ldg_ok'], r['ldg_margin'], r['ldg_pct'])}</td>"
        f"<td>{('HW' if r['hw_comp']>=0 else 'TW')} {abs(r['hw_comp']):.0f} / {abs(r.get('cw_comp',0)):.0f} kt</td>"
        f"<td>{fmt(r.get('roc',0))}</td><td>{fmt(r.get('vy',0))}</td>"
        f"</tr>"
        for r in perf_rows
    ]) + "</table>", unsafe_allow_html=True
)

# ===== Fuel planning (same logic as your file) =====
st.markdown("### Fuel planning")
RATE_LPH = 20.0
simple_policy = st.checkbox(
    "Use simplified policy: Taxi=15min; ignore 2,3,4 & 6,7,8; 1h in (5) and 1h in (9)",
    value=True
)
POLICY_TAXI_MIN = 15
POLICY_TRIP_MIN = 60
POLICY_BLOCK9_MIN = 60

c1, c2, c3, c4 = st.columns([0.25,0.25,0.25,0.25])

def time_to_liters(h=0, m=0, rate=RATE_LPH):
    return rate * (h + m/60.0)

with c1:
    if simple_policy:
        su_min = POLICY_TAXI_MIN
        st.markdown(f"**Start-up & taxi (1)**: {su_min} min *(policy)*")
    else:
        su_min = st.number_input("Start-up & taxi (min) (1)", min_value=0, value=POLICY_TAXI_MIN, step=1)
    climb_min = st.number_input("Climb (min) (2)", min_value=0, value=15, step=1, disabled=simple_policy)
with c2:
    enrt_h = st.number_input("Enroute (h) (3)", min_value=0, value=2, step=1, disabled=simple_policy)
    enrt_min = st.number_input("Enroute (min) (3)", min_value=0, value=15, step=1, disabled=simple_policy)
with c3:
    desc_min = st.number_input("Descent (min) (4)", min_value=0, value=15, step=1, disabled=simple_policy)
    alt_min = st.number_input("Alternate (min) (7)", min_value=0, value=60, step=5, disabled=simple_policy)
with c4:
    reserve_min = st.number_input("Reserve (min) (8)", min_value=0, value=45, step=5, disabled=simple_policy)
    extra_min_user = st.number_input("Extra (min) (10) (manual if normal mode)", min_value=0, value=0, step=5, disabled=simple_policy)

if simple_policy:
    trip_min = POLICY_TRIP_MIN
    block9_min = POLICY_BLOCK9_MIN
    trip_l   = time_to_liters(0, trip_min)
    block9_l = time_to_liters(0, block9_min)
    req_ramp = time_to_liters(0, su_min) + trip_l + block9_l
    diff_l = fuel_l - req_ramp
    extra_l = max(0.0, diff_l)
    extra_min = int(round((extra_l / RATE_LPH) * 60))
    missing_l = max(0.0, -diff_l)
    total_ramp = req_ramp + extra_l
    req_ramp_min = su_min + trip_min + block9_min
    total_ramp_min = req_ramp_min + extra_min

    climb_min_eff = 0
    enrt_min_eff = 0
    desc_min_eff = 0
    cont_min = 0
    cont_l = 0.0
else:
    climb_min_eff   = climb_min
    enrt_min_eff    = enrt_h*60 + enrt_min
    desc_min_eff    = desc_min
    alt_min_eff     = alt_min
    reserve_min_eff = reserve_min
    trip_min = climb_min_eff + enrt_min_eff + desc_min_eff
    trip_l   = time_to_liters(0, trip_min)
    cont_l   = 0.05 * trip_l
    cont_min = int(round(0.05 * trip_min))
    extra_min = extra_min_user
    extra_l   = time_to_liters(0, extra_min)
    req_ramp = time_to_liters(0, su_min) + trip_l + cont_l + time_to_liters(0, alt_min_eff) + time_to_liters(0, reserve_min_eff)
    total_ramp = req_ramp + extra_l
    req_ramp_min = su_min + climb_min_eff + enrt_min_eff + desc_min_eff + alt_min_eff + reserve_min_eff
    total_ramp_min = req_ramp_min + extra_min
    block9_min = 0
    missing_l  = 0.0

st.markdown(f"- **(1) Start-up & taxi**: {su_min} min → {time_to_liters(0, su_min):.1f} L")
st.markdown(f"- **(5) Trip**: {trip_min} min → {trip_l:.1f} L" + ("  *(policy)*" if simple_policy else ""))
if simple_policy:
    st.markdown(f"- **(9)**: {block9_min} min → {time_to_liters(0, block9_min):.1f} L  *(policy)*")
else:
    st.markdown(f"- **(6) Contingency 5%**: {cont_l:.1f} L")
    st.markdown(f"- **(7) Alternate**: {alt_min} min → {time_to_liters(0, alt_min):.1f} L")
    st.markdown(f"- **(8) Reserve**: {reserve_min} min → {time_to_liters(0, reserve_min):.1f} L")

st.markdown(f"- **Required ramp fuel**: **{req_ramp:.1f} L**  *(time: {fmt_hm(req_ramp_min)})*")
st.markdown(f"- **Extra**: {extra_l:.1f} L" + ("  *(auto to match M&B)*" if simple_policy else ""))
st.markdown(f"- **Total ramp (planned)**: **{total_ramp:.1f} L**  *(time: {fmt_hm(total_ramp_min)})*")
st.markdown(f"- **Fuel loaded (M&B)**: **{fuel_l:.1f} L**")
if simple_policy and missing_l > 0.1:
    st.error(f"Short of policy by {missing_l:.1f} L (Taxi 15min + 1h in (5) + 1h in (9)).")
st.markdown(f"- **Could still carry**: **{remaining_fuel_l:.1f} L** (limited by **{'Tank' if remaining_by_tank < remaining_by_mtow else 'MTOW'}**).")

# ===== PDF export (same as your file; field autodiscovery) =====
st.markdown("### PDF export (Tecnam P2008 – M&B and Performance Data Sheet)")
PDF_TEMPLATE_PATHS = [
    "TecnamP2008MBPerformanceSheet_MissionX.pdf",           # repo root
    "/mnt/data/TecnamP2008MBPerformanceSheet_MissionX.pdf", # uploaded
]

reg_input = st.text_input("Aircraft registration", value="")
date_str = st.text_input("Date (dd/mm/yyyy)",
                         dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%d/%m/%Y"))

def read_pdf_bytes(paths) -> bytes:
    for path_str in paths:
        p = Path(path_str)
        if p.exists():
            return p.read_bytes()
    raise FileNotFoundError(f"Template not found in any known path: {paths}")

def get_field_names(template_bytes: bytes) -> set:
    names = set()
    reader = PdfReader(io.BytesIO(template_bytes))
    try:
        fd = reader.get_fields()
        if fd: names.update(fd.keys())
    except Exception:
        pass
    try:
        for page in reader.pages:
            if "/Annots" in page:
                for a in page["/Annots"]:
                    obj = a.get_object()
                    if obj.get("/T"): names.add(str(obj["/T"]))
    except Exception:
        pass
    return names

def fill_pdf(template_bytes: bytes, fields: dict) -> bytes:
    if not PYPDF_OK: raise RuntimeError("pypdf not available. Add 'pypdf' to requirements.txt")
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()
    for page in reader.pages: writer.add_page(page)
    root = reader.trailer["/Root"]
    if "/AcroForm" not in root: raise RuntimeError("Template PDF has no AcroForm/fields.")
    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    try: writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): True})
    except Exception: pass
    for page in writer.pages: writer.update_page_form_field_values(page, fields)
    bio = io.BytesIO(); writer.write(bio); return bio.getvalue()

def put_any(out: dict, fieldset: set, keys, value: str):
    if isinstance(keys, str): keys = [keys]
    for k in keys:
        if k in fieldset: out[k] = value

named_map: Dict[str,str] = {}
try:
    template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
    fieldset = get_field_names(template_bytes)

    # M&B
    put_any(named_map, fieldset, "EmptyWeight_W", f"{ew:.1f}")
    put_any(named_map, fieldset, "EmptyWeight_A", f"{(ew_moment/ew if ew>0 else 0.0):.3f}")
    put_any(named_map, fieldset, "EmptyWeight_M", f"{(ew_moment):.2f}")
    put_any(named_map, fieldset, "Fuel_W", f"{fuel_wt:.1f}")
    put_any(named_map, fieldset, "Fuel_M", f"{m_fuel:.2f}")
    put_any(named_map, fieldset, "Pilot&Passenger_W", f"{pilot:.1f}")
    put_any(named_map, fieldset, "Pilot&Passenger_M", f"{m_pilot:.2f}")
    put_any(named_map, fieldset, "Baggage_W", f"{baggage:.1f}")
    put_any(named_map, fieldset, "Baggage_M", f"{m_bag:.2f}")
    put_any(named_map, fieldset, "TOTAL_W", f"{total_weight:.2f}")
    put_any(named_map, fieldset, "TOTAL_M", f"{total_moment:.2f}")
    put_any(named_map, fieldset, "CG", f"{cg:.3f}")
    put_any(named_map, fieldset, "Aircraf_Reg", reg_input or "")
    put_any(named_map, fieldset, "Date", date_str)

    # Performance per leg (Dep/Arr/Alt)
    roles = {"Departure": "Dep", "Arrival": "Arr", "Alternate": "Alt"}
    by_role = {r["role"]: r for r in perf_rows} if perf_rows else {}
    for role, suf in roles.items():
        r = by_role.get(role)
        if not r: continue
        put_any(named_map, fieldset, f"Airfield_{suf}", r["icao"])
        put_any(named_map, fieldset, f"QFU_{suf}", f"{int(round(r['qfu'])):03d}")
        if suf == "Dep": put_any(named_map, fieldset, "Elev_Dep", f"{r['elev_ft']:.0f}")
        elif suf == "Arr": put_any(named_map, fieldset, "Elev_Arr", f"{r['elev_ft']:.0f}")
        else: put_any(named_map, fieldset, "Elev_Alt", f"{r['elev_ft']:.0f}")

        put_any(named_map, fieldset, f"QNH_{suf}", f"{r['qnh']:.0f}")
        put_any(named_map, fieldset, f"Temp_{suf}", f"{r['temp']:.0f}")
        put_any(named_map, fieldset, f"Wind_{suf}", f"{int(r['wind_dir']):03d}/{int(round(r['wind_kt'])):02d}")
        put_any(named_map, fieldset, f"PA_{suf}", f"{r['pa_ft']:.0f}")
        put_any(named_map, fieldset, f"DA_{suf}", f"{r['da_ft']:.0f}")
        put_any(named_map, fieldset, f"TODA_{suf}", f"{r['toda_av']:.0f}")

        # Percentage inside TODR/LDR strings (as in your file)
        tod_str = f"{r['to_50']:.0f} ({r['tod_pct']:.0f}%)" if r.get('tod_pct') is not None else f"{r['to_50']:.0f}"
        ldr_str = f"{r['ldg_50']:.0f} ({r['ldg_pct']:.0f}%)" if r.get('ldg_pct') is not None else f"{r['ldg_50']:.0f}"

        put_any(named_map, fieldset, f"TODR_{suf}", tod_str)
        put_any(named_map, fieldset, f"LDA_{suf}", f"{r['lda_av']:.0f}")
        put_any(named_map, fieldset, f"LDR_{suf}", ldr_str)
        put_any(named_map, fieldset, f"ROC_{suf}", f"{r.get('roc', 0):.0f}")

    # Fuel (same mapping & logic as your file)
    def dash_if(cond, val):
        return val if not cond else "-"

    put_any(named_map, fieldset, "Taxi_T", fmt_hm(su_min))
    put_any(named_map, fieldset, "Taxi_F", f"{int(round(RATE_LPH * su_min/60))}")
    put_any(named_map, fieldset, "Trip_T", fmt_hm(trip_min))
    put_any(named_map, fieldset, "Trip_F", f"{int(round(RATE_LPH * (trip_min/60)))}")

    put_any(named_map, fieldset, "Climb_T", dash_if(simple_policy, fmt_hm(0)))
    put_any(named_map, fieldset, "Climb_F", dash_if(simple_policy, "0"))
    put_any(named_map, fieldset, "Enroute_T", dash_if(simple_policy, fmt_hm(0)))
    put_any(named_map, fieldset, "Enroute_F", dash_if(simple_policy, "0"))
    put_any(named_map, fieldset, "Descent_T", dash_if(simple_policy, fmt_hm(0)))
    put_any(named_map, fieldset, "Descent_F", dash_if(simple_policy, "0"))

    if simple_policy:
        put_any(named_map, fieldset, "Contingency_T", "-")
        put_any(named_map, fieldset, "Contingency_F", "-")
        put_any(named_map, fieldset, "Alternate_T", "-")
        put_any(named_map, fieldset, "Alternate_F", "-")
        put_any(named_map, fieldset, "Reserve_T", fmt_hm(60))
        put_any(named_map, fieldset, "Reserve_F", f"{int(round(time_to_liters(0,60)))}")
    else:
        put_any(named_map, fieldset, "Contingency_T", fmt_hm(cont_min))
        put_any(named_map, fieldset, "Contingency_F", f"{int(round(cont_l))}")
        put_any(named_map, fieldset, "Alternate_T", fmt_hm(alt_min))
        put_any(named_map, fieldset, "Alternate_F", f"{int(round(time_to_liters(0, alt_min)))}")
        put_any(named_map, fieldset, "Reserve_T", fmt_hm(reserve_min))
        put_any(named_map, fieldset, "Reserve_F", f"{int(round(time_to_liters(0, reserve_min)))}")

    put_any(named_map, fieldset, "Ramp_T", fmt_hm(req_ramp_min))
    put_any(named_map, fieldset, "Ramp_F", f"{int(round(req_ramp))}")
    put_any(named_map, fieldset, "Extra_T", fmt_hm(extra_min))
    put_any(named_map, fieldset, "Extra_F", f"{int(round(extra_l))}")
    put_any(named_map, fieldset, "Total_T", fmt_hm(total_ramp_min))
    put_any(named_map, fieldset, "Total_F", f"{int(round(total_ramp))}")

    if st.button("Generate filled PDF", type="primary"):
        try:
            out_bytes = fill_pdf(template_bytes, named_map)
            st.download_button("Download PDF", data=out_bytes, file_name="P2008_MB_Perf_AllLegs.pdf", mime="application/pdf")
            st.success("PDF generated. Review before flight.")
        except Exception as e:
            st.error(f"Could not generate PDF: {e}")
except Exception as e:
    st.error(f"Cannot prepare PDF mapping: {e}")

