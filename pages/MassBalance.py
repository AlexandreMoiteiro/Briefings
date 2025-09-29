
# Streamlit app – Tecnam P2008 (M&B + Performance)
# Compatible with GitHub + Streamlit Cloud
# Requirements (requirements.txt):
#   streamlit
#   requests
#   pytz
#   pdfrw==0.4
#   pypdf>=4.2.0
#
# Secrets:
#   WINDY_API_KEY = "<your windy point-forecast key>"
#   # Optional (auto-load fleet EW/moment if present)
#   # GITHUB_GIST_TOKEN = "ghp_..."   # PAT (Gists: Read & write)
#   # GITHUB_GIST_ID    = "..."       # Gist id containing fleet_p2008.json

import streamlit as st
import datetime as dt
from pathlib import Path
import pytz
import unicodedata
from math import cos, sin, radians, atan2, degrees, sqrt
import json
import requests

from pdfrw import PdfReader as Rd_pdfrw, PdfWriter as Wr_pdfrw, PdfDict
from pypdf import PdfReader as Rd_pypdf, PdfWriter as Wr_pypdf

# =========================
# Helpers & Style
# =========================

def ascii_safe(text):
    if not isinstance(text, str):
        return str(text)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

st.set_page_config(
    page_title="Tecnam P2008 – Mass & Balance & Performance",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1160px !important; }
      .mb-header{font-size:1.3rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:6px;margin-bottom:8px}
      .section-title{font-weight:700;margin:14px 0 6px 0}
      .mb-summary-row{display:flex;justify-content:space-between;margin:4px 0}
      .ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
      .mb-table{border-collapse:collapse;width:100%;font-size:.95rem}
      .mb-table th{border-bottom:2px solid #cbd0d6;text-align:left}
      .mb-table td{padding:3px 6px;border-bottom:1px dashed #e5e7ec}
      .chip{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef2f7;margin-left:8px;font-size:.85rem}
      .chip-red{background:#fde8e8}.chip-yellow{background:#fff6db}.chip-green{background:#e8f7ec}
    </style>
    """,
    unsafe_allow_html=True,
)

# Use only the filename (relative path), per your request
PDF_TEMPLATE = Path("TecnamP2008MBPerformanceSheet_MissionX.pdf")

# =========================
# Fixed Aircraft Data (Tecnam P2008)
# =========================
AC = {
    "name": "Tecnam P2008 JC",
    "fuel_arm": 2.209,
    "pilot_arm": 1.800,
    "baggage_arm": 2.417,
    "max_takeoff_weight": 650.0,
    "max_fuel_volume": 124.0,  # liters
    "max_passenger_weight": 230.0,
    "max_baggage_weight": 20.0,
    "cg_limits": (1.841, 1.978),
    "fuel_density": 0.72,  # kg/L
    "units": {"weight": "kg", "arm": "m"},
}

# =========================
# Aerodrome DB (Portugal) – AIP-based declared distances, RWY-by-RWY
# =========================
AERODROMES_DB = {
    "LPSO": {  # eAIP LPSO AD 2.13: both ends 1800
        "name": "Ponte de Sor",
        "lat": 39.211667, "lon": -8.057778, "elev_ft": 390.0,
        "runways": [
            {"id": "03", "qfu": 30.0,  "tora": 1800.0, "toda": 1800.0, "asda": 1800.0, "lda": 1800.0, "paved": True, "slope_pc": 0.0},
            {"id": "21", "qfu": 210.0, "tora": 1800.0, "toda": 1800.0, "asda": 1800.0, "lda": 1800.0, "paved": True, "slope_pc": 0.0},
        ],
    },
    "LPEV": {  # eAIP LPEV AD 2.13: direction-dependent
        "name": "Évora",
        "lat": 38.529722, "lon": -7.891944, "elev_ft": 807.0,
        "runways": [
            {"id": "01", "qfu": 10.0,  "tora": 1260.0, "toda": 1300.0, "asda": 1300.0, "lda": 1245.0, "paved": True, "slope_pc": 0.0},
            {"id": "19", "qfu": 190.0, "tora": 1245.0, "toda": 1300.0, "asda": 1300.0, "lda": 1260.0, "paved": True, "slope_pc": 0.0},
            # Short cross RWY (kept for completeness)
            {"id": "07", "qfu": 70.0,  "tora": 530.0,  "toda": 530.0,  "asda": 530.0,  "lda": 530.0,  "paved": True, "slope_pc": 0.0},
            {"id": "25", "qfu": 250.0, "tora": 530.0,  "toda": 530.0,  "asda": 530.0,  "lda": 530.0,  "paved": True, "slope_pc": 0.0},
        ],
    },
    "LPCB": {  # eAIP LPCB AD 2.13: 16 has TORA 1460/TODA 1520; 34 has TORA/TODA 1520; both LDA 1460
        "name": "Castelo Branco",
        "lat": 39.848333, "lon": -7.441667, "elev_ft": 1251.0,
        "runways": [
            {"id": "16", "qfu": 160.0, "tora": 1460.0, "toda": 1520.0, "asda": 1520.0, "lda": 1460.0, "paved": True, "slope_pc": 0.0},
            {"id": "34", "qfu": 340.0, "tora": 1520.0, "toda": 1520.0, "asda": 1520.0, "lda": 1460.0, "paved": True, "slope_pc": 0.0},
        ],
    },
    # (Optional extras)
    "LPCS": {
        "name": "Cascais",
        "lat": 38.725556, "lon": -9.355278, "elev_ft": 326.0,
        "runways": [
            {"id": "17", "qfu": 170.0, "tora": 1700.0, "toda": 1700.0, "asda": 1700.0, "lda": 1700.0, "paved": True, "slope_pc": 0.0},
            {"id": "35", "qfu": 350.0, "tora": 1700.0, "toda": 1700.0, "asda": 1700.0, "lda": 1700.0, "paved": True, "slope_pc": 0.0},
        ],
    },
    "LPPT": {
        "name": "Lisboa",
        "lat": 38.774167, "lon": -9.134167, "elev_ft": 355.0,
        "runways": [
            {"id": "02", "qfu": 20.0,  "tora": 3805.0, "toda": 3805.0, "asda": 3805.0, "lda": 3715.0, "paved": True, "slope_pc": 0.0},
            {"id": "20", "qfu": 200.0, "tora": 3805.0, "toda": 3805.0, "asda": 3805.0, "lda": 3715.0, "paved": True, "slope_pc": 0.0},
        ],
    },
    "LPFR": {
        "name": "Faro",
        "lat": 37.014444, "lon": -7.965833, "elev_ft": 24.0,
        "runways": [
            {"id": "10", "qfu": 100.0, "tora": 2490.0, "toda": 2490.0, "asda": 2490.0, "lda": 2490.0, "paved": True, "slope_pc": 0.0},
            {"id": "28", "qfu": 280.0, "tora": 2490.0, "toda": 2490.0, "asda": 2490.0, "lda": 2490.0, "paved": True, "slope_pc": 0.0},
        ],
    },
}

# =========================
# AFM Performance Tables (same as your reference)
# =========================
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
    650:{
        0:{-25:951,0:805,25:675,50:557,"ISA":725},
        2000:{-25:840,0:696,25:568,50:453,"ISA":638},
        4000:{-25:729,0:588,25:462,50:349,"ISA":551},
        6000:{-25:619,0:480,25:357,50:245,"ISA":464},
        8000:{-25:509,0:373,25:251,50:142,"ISA":377},
        10000:{-25:399,0:266,25:146,50:39,"ISA":290},
        12000:{-25:290,0:159,25:42,50:-64,"ISA":204},
        14000:{-25:181,0:53,25:-63,50:-166,"ISA":117},
    },
    600:{
        0:{-25:1067,0:913,25:776,50:652,"ISA":829},
        2000:{-25:950,0:799,25:664,50:542,"ISA":737},
        4000:{-25:833,0:685,25:552,50:433,"ISA":646},
        6000:{-25:717,0:571,25:441,50:324,"ISA":555},
        8000:{-25:602,0:458,25:330,50:215,"ISA":463},
        10000:{-25:486,0:345,25:220,50:106,"ISA":372},
        12000:{-25:371,0:233,25:110,50:-2,"ISA":280},
        14000:{-25:257,0:121,25:0,50:-109,"ISA":189},
    },
    550:{
        0:{-25:1201,0:1038,25:892,50:760,"ISA":948},
        2000:{-25:1077,0:916,25:773,50:644,"ISA":851},
        4000:{-25:953,0:795,25:654,50:527,"ISA":754},
        6000:{-25:830,0:675,25:536,50:411,"ISA":657},
        8000:{-25:707,0:555,25:419,50:296,"ISA":560},
        10000:{-25:584,0:435,25:301,50:181,"ISA":462},
        12000:{-25:462,0:315,25:184,50:66,"ISA":365},
        14000:{-25:341,0:196,25:68,50:-48,"ISA":268},
    }
}
VY = {
    650:{0:70,2000:69,4000:68,6000:67,8000:65,10000:64,12000:63,14000:62},
    600:{0:70,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:62},
    550:{0:69,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:61},
}

XW_GREEN_MAX = 8
XW_YELLOW_MAX = 15

# =========================
# Interpolation & Corrections
# =========================

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def interp1(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def bilinear(pa, temp, table, key):
    pas = sorted(table.keys())
    pa = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa])
    p1 = min([p for p in pas if p >= pa])
    temps = [-25, 0, 25, 50]
    t = clamp(temp, temps[0], temps[-1])
    if t <= 0:
        t0, t1 = -25, 0
    elif t <= 25:
        t0, t1 = 0, 25
    else:
        t0, t1 = 25, 50
    v00 = table[p0][key][t0]
    v01 = table[p0][key][t1]
    v10 = table[p1][key][t0]
    v11 = table[p1][key][t1]
    v0 = interp1(t, t0, t1, v00, v01)
    v1 = interp1(t, t0, t1, v10, v11)
    return interp1(pa, p0, p1, v0, v1)

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
        if t <= 0:
            t0, t1 = -25, 0
        elif t <= 25:
            t0, t1 = 0, 25
        else:
            t0, t1 = 25, 50
        v00 = tab[p0][t0]
        v01 = tab[p0][t1]
        v10 = tab[p1][t0]
        v11 = tab[p1][t1]
        v0 = interp1(t, t0, t1, v00, v01)
        v1 = interp1(t, t0, t1, v10, v11)
        return interp1(pa_c, p0, p1, v0, v1)
    if w <= 600:
        return interp1(w, 550, 600, roc_for_w(550), roc_for_w(600))
    else:
        return interp1(w, 600, 650, roc_for_w(600), roc_for_w(650))

def wind_components(qfu_deg, wind_dir_deg, wind_speed):
    """Headwind (+ tailwind negative), crosswind abs and side (R/L)."""
    if qfu_deg is None or wind_dir_deg is None or wind_speed is None:
        return 0.0, 0.0, ""
    diff = radians((wind_dir_deg - qfu_deg) % 360)
    hw = wind_speed * cos(diff)
    cw = wind_speed * sin(diff)
    side = "R" if cw > 0 else ("L" if cw < 0 else "")
    return hw, abs(cw), side

def to_corrections_takeoff(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = float(ground_roll)
    if headwind_kt >= 0:
        gr = gr - 5.0 * headwind_kt
    else:
        gr = gr + 15.0 * abs(headwind_kt)
    if paved:
        gr *= 0.9
    if slope_pc:
        gr *= (1.0 + 0.07 * (slope_pc/1.0))
    return max(gr, 0.0)

def ldg_corrections(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = float(ground_roll)
    if headwind_kt >= 0:
        gr = gr - 4.0 * headwind_kt
    else:
        gr = gr + 13.0 * abs(headwind_kt)
    if paved:
        gr *= 0.9
    if slope_pc:
        gr *= (1.0 - 0.03 * (slope_pc/1.0))
    return max(gr, 0.0)

def xw_chip_class(xw_abs):
    if xw_abs <= XW_GREEN_MAX: return "chip chip-green"
    if xw_abs <= XW_YELLOW_MAX: return "chip chip-yellow"
    return "chip chip-red"

# =========================
# Windy API (hourly forecast)
# =========================
WINDY_ENDPOINT = "https://api.windy.com/api/point-forecast/v2"

@st.cache_data(ttl=900, show_spinner=False)
def windy_point_forecast(lat, lon, model, params, api_key):
    headers = {"Content-Type": "application/json"}
    body = {
        "lat": round(float(lat), 3),
        "lon": round(float(lon), 3),
        "model": model,               # default "iconEu"
        "parameters": params,         # ["wind","temp","pressure","windGust"]
        "levels": ["surface"],
        "key": api_key,
    }
    try:
        r = requests.post(WINDY_ENDPOINT, headers=headers, data=json.dumps(body), timeout=20)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "detail": r.text}
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def windy_hours_for_date(resp, target_date=None):
    if not resp or "ts" not in resp or not resp["ts"]:
        return []
    hours = []
    for i, tms in enumerate(resp["ts"]):
        dt_utc = dt.datetime.utcfromtimestamp(tms/1000.0).replace(tzinfo=dt.timezone.utc)
        if (target_date is None) or (dt_utc.date() == target_date):
            hours.append((i, dt_utc, dt_utc.strftime("%Y-%m-%d %H:00Z")))
    return hours

def windy_find_index_for_ts(resp, target_ts_ms):
    """Return exact index for target timestamp (ms), or nearest by absolute delta."""
    if not resp or "ts" not in resp or not resp["ts"]:
        return None
    ts_list = resp["ts"]
    try:
        return ts_list.index(target_ts_ms)
    except ValueError:
        # nearest
        diffs = [abs(t - target_ts_ms) for t in ts_list]
        return diffs.index(min(diffs))

def windy_unpack_at(resp, idx):
    if idx is None: return None
    def getv(key):
        arr = resp.get(key, [])
        return arr[idx] if arr and idx < len(arr) else None
    u = getv("wind_u-surface")
    v = getv("wind_v-surface")
    gust = getv("gust-surface")
    if u is None or v is None: return None
    speed_ms = sqrt(u*u + v*v)
    dir_deg = (degrees(atan2(-u, -v)) + 360.0) % 360.0  # meteorological dir (from)
    speed_kt = speed_ms * 1.94384
    temp_val = getv("temp-surface")
    temp_c = None
    if temp_val is not None:
        temp_c = float(temp_val)
        if temp_c > 100:  # Kelvin → °C
            temp_c -= 273.15
        temp_c = round(temp_c, 1)
    pres_pa = getv("pressure-surface")
    qnh_hpa = round(pres_pa/100.0, 1) if pres_pa is not None else None
    return {
        "wind_dir": round(dir_deg),
        "wind_kt": round(speed_kt),
        "wind_gust_kt": round(gust * 1.94384) if gust is not None else None,
        "temp": temp_c,
        "qnh": qnh_hpa
    }

# =========================
# Fleet persistence (optional: GitHub Gist auto-load)
# =========================
DEFAULT_FLEET = {
    "CS-DHS": {"ew": None, "ew_moment": None},
    "CS-DHT": {"ew": None, "ew_moment": None},
    "CS-DHU": {"ew": None, "ew_moment": None},
    "CS-DHV": {"ew": None, "ew_moment": None},
    "CS-DHW": {"ew": None, "ew_moment": None},
    "CS-ECB": {"ew": None, "ew_moment": None},
    "CS-ECC": {"ew": None, "ew_moment": None},
    "CS-ECD": {"ew": None, "ew_moment": None},
}

def gist_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gist_load_fleet(token, gist_id, filename="fleet_p2008.json"):
    try:
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(token), timeout=15)
        if r.status_code != 200:
            return None, f"GitHub error {r.status_code}: {r.text}"
        data = r.json()
        files = data.get("files", {})
        if filename in files and files[filename].get("content") is not None:
            content = files[filename]["content"]
            return json.loads(content), None
        return None, "Gist file not found; using defaults."
    except Exception as e:
        return None, str(e)

# =========================
# UI – Tabs
# =========================

st.markdown('<div class="mb-header">Tecnam P2008 – Mass & Balance & Performance</div>', unsafe_allow_html=True)

tab_setup, tab_aero, tab_wb, tab_perf, tab_pdf = st.tabs([
    "1) Flight & Aircraft", "2) Aerodromes", "3) Weight & Balance",
    "4) Performance & Fuel", "5) PDF"
])

# ---- 1) Flight & Aircraft ----
with tab_setup:
    # Auto-load fleet JSON once (if secrets present)
    if "fleet" not in st.session_state:
        fleet = DEFAULT_FLEET.copy()
        token = st.secrets.get("GITHUB_GIST_TOKEN", "")
        gist_id = st.secrets.get("GITHUB_GIST_ID", "")
        if token and gist_id:
            data, err = gist_load_fleet(token, gist_id)
            if data:
                fleet.update(data)
        st.session_state.fleet = fleet

    regs = sorted(list(st.session_state.fleet.keys()))
    default_time = (dt.datetime.utcnow() + dt.timedelta(hours=1)).time().replace(second=0, microsecond=0)
    col1, col2 = st.columns([0.55, 0.45])
    with col1:
        st.markdown("### Flight data (UTC)")
        flight_date = st.date_input("Date (UTC)", value=dt.datetime.utcnow().date(), key="flight_date_utc")
        mission = st.text_input("Mission #", value=st.session_state.get("mission", "001"), key="mission")
    with col2:
        st.markdown("### Aircraft")
        selected_reg = st.selectbox("Registration", regs, index=0, key="reg")

    # Store for later use
    st.session_state["date_str"] = dt.datetime.combine(flight_date, dt.time(0,0)).strftime("%d/%m/%Y")

# ---- 2) Aerodromes (select which three) ----
with tab_aero:
    if "legs" not in st.session_state:
        st.session_state.legs = [
            {"role": "Departure", "icao": "LPSO"},
            {"role": "Arrival",   "icao": "LPEV"},
            {"role": "Alternate", "icao": "LPCB"},
        ]
    st.markdown("### Select aerodromes (three legs used for the PDF)")
    icao_options = sorted(AERODROMES_DB.keys())

    for i, leg in enumerate(st.session_state.legs):
        ad = AERODROMES_DB[leg["icao"]]
        c1, c2 = st.columns([0.4, 0.6])
        with c1:
            icao = st.selectbox(f"{leg['role']} – ICAO", options=icao_options,
                                index=icao_options.index(leg["icao"]), key=f"leg_icao_{i}")
            st.session_state.legs[i]["icao"] = icao
        with c2:
            ad = AERODROMES_DB[icao]
            st.write(f"**{ad['name']}** — Lat {ad['lat']:.5f}, Lon {ad['lon']:.5f}, Elev {ad['elev_ft']:.0f} ft")
            rwy_desc = ", ".join([f"{rw['id']}: TODA {rw['toda']:.0f} / LDA {rw['lda']:.0f}" for rw in ad["runways"]])
            st.caption(f"Runways: {rwy_desc}")

# ---- 3) Weight & Balance ----
with tab_wb:
    st.markdown("### Weight & Balance")
    reg = st.session_state.get("reg", "CS-XXX")
    fleet = st.session_state.fleet
    ew_default  = float(fleet.get(reg, {}).get("ew") or 0.0)
    ewm_default = float(fleet.get(reg, {}).get("ew_moment") or 0.0)

    c1, c2 = st.columns([0.55, 0.45])
    with c1:
        # Read-only EW & moment (maintained in the fleet JSON)
        ew = st.number_input("Empty Weight (kg)", min_value=0.0, value=ew_default, step=0.1, disabled=True)
        ew_moment = st.number_input("Empty Weight Moment (kg·m)", min_value=0.0, value=ewm_default, step=0.01, disabled=True)

        student    = float(st.number_input("Student weight (kg)",   min_value=0.0, value=0.0, step=0.5))
        instructor = float(st.number_input("Instructor weight (kg)",min_value=0.0, value=0.0, step=0.5))
        baggage    = float(st.number_input("Baggage (kg)",          min_value=0.0, value=0.0, step=0.5))
        fuel_l     = float(st.number_input("Fuel (L)",              min_value=0.0, value=0.0, step=0.5))

    with c2:
        pilot = student + instructor
        fuel_wt = fuel_l * AC['fuel_density']
        m_empty = ew_moment
        m_pilot = pilot * AC['pilot_arm']
        m_bag   = baggage * AC['baggage_arm']
        m_fuel  = fuel_wt * AC['fuel_arm']
        total_weight = ew + pilot + baggage + fuel_wt
        total_moment = m_empty + m_pilot + m_bag + m_fuel
        cg = (total_moment/total_weight) if total_weight>0 else 0.0

        remaining_by_mtow = max(0.0, AC['max_takeoff_weight'] - (ew + pilot + baggage + fuel_wt))
        remaining_by_tank = max(0.0, AC['max_fuel_volume']*AC['fuel_density'] - fuel_wt)
        remaining_fuel_weight = min(remaining_by_mtow, remaining_by_tank)
        remaining_fuel_l = remaining_fuel_weight / AC['fuel_density']
        limit_label = "Tank Capacity" if remaining_by_tank < remaining_by_mtow else "Maximum Weight"

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
        st.markdown(f"<div class='mb-summary-row'><div>Remaining possible fuel</div><div><b>{remaining_fuel_l:.1f} L</b> ({limit_label})</div></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='mb-summary-row'><div>Total Weight</div><div class='{w_color(total_weight, AC['max_takeoff_weight'])}'><b>{total_weight:.1f} kg</b></div></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='mb-summary-row'><div>Total Moment</div><div><b>{total_moment:.2f} kg·m</b></div></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='mb-summary-row'><div>CG</div><div class='{cg_color_val(cg, AC['cg_limits'])}'><b>{cg:.3f} m</b></div></div>", unsafe_allow_html=True)

        # Limits/alerts (kept)
        if pilot > AC["max_passenger_weight"]:
            st.error(f"Passengers over limit: {pilot:.1f} kg > {AC['max_passenger_weight']:.0f} kg")
        if baggage > AC["max_baggage_weight"]:
            st.error(f"Baggage over limit: {baggage:.1f} kg > {AC['max_baggage_weight']:.0f} kg")
        if fuel_l > AC["max_fuel_volume"]:
            st.error(f"Fuel volume over limit: {fuel_l:.1f} L > {AC['max_fuel_volume']:.0f} L")
        if total_weight > AC["max_takeoff_weight"]:
            st.error(f"MTOW exceeded: {total_weight:.1f} kg > {AC['max_takeoff_weight']:.0f} kg")
        lo, hi = AC["cg_limits"]
        if total_weight > 0 and (cg < lo or cg > hi):
            st.error(f"CG out of limits: {cg:.3f} m not in [{lo:.3f}, {hi:.3f}] m")

    st.session_state["_wb"] = {
        "ew": ew, "ew_moment": ew_moment,
        "total_weight": total_weight, "total_moment": total_moment,
        "cg": cg, "fuel_l": fuel_l, "pilot": pilot, "baggage": baggage
    }

# ---- 4) Performance & Fuel (single-hour Windy, applied to all ADs) ----
with tab_perf:
    st.markdown("### Performance & Fuel")
    # --- Fuel policy first (as requested) ---
    policy = st.radio("Fuel policy", options=["Simplified (fixed 90 L ramp)", "Detailed (rate-based)"], index=0, horizontal=True)
    if policy.startswith("Simplified"):
        total_ramp = 90.0
        st.info("Using simplified policy: Total Ramp Fuel = 90 L")
        trip_l = cont_l = req_ramp = extra_l = 0.0
        su_min = climb_min = enrt_h = enrt_min = desc_min = alt_min = reserve_min = extra_min = 0
    else:
        RATE_LPH = st.number_input("Planned consumption (L/h)", min_value=10.0, max_value=40.0, value=20.0, step=0.5)
        c1, c2, c3, c4 = st.columns([0.25,0.25,0.25,0.25])
        def time_to_liters(h=0, m=0, rate=RATE_LPH): return rate * (h + m/60.0)
        with c1:
            su_min = int(st.number_input("Start-up & Taxi (min)", min_value=0, value=15, step=1))
            climb_min = int(st.number_input("Climb (min)", min_value=0, value=15, step=1))
        with c2:
            enrt_h = int(st.number_input("Enroute (h)", min_value=0, value=2, step=1))
            enrt_min = int(st.number_input("Enroute (min)", min_value=0, value=15, step=1))
        with c3:
            desc_min = int(st.number_input("Descent (min)", min_value=0, value=15, step=1))
            alt_min = int(st.number_input("Alternate (min)", min_value=0, value=60, step=5))
        with c4:
            reserve_min = int(st.number_input("Reserve (min)", min_value=0, value=45, step=5))
            extra_min = int(st.number_input("Extra (min)", min_value=0, value=0, step=5))
        trip_l = time_to_liters(0, climb_min) + time_to_liters(enrt_h, enrt_min) + time_to_liters(0, desc_min)
        cont_l = 0.05*trip_l
        req_ramp = time_to_liters(0, su_min) + trip_l + cont_l + time_to_liters(0, alt_min) + time_to_liters(0, reserve_min)
        extra_l = time_to_liters(0, extra_min)
        total_ramp = req_ramp + extra_l

        st.markdown(f"- **Trip Fuel**: {trip_l:.1f} L")
        st.markdown(f"- **Contingency 5%**: {cont_l:.1f} L")
        st.markdown(f"- **Required Ramp Fuel** (1+5+6+7+8): **{req_ramp:.1f} L**")
        st.markdown(f"- **Extra**: {extra_l:.1f} L")
    st.markdown(f"- **Total Ramp Fuel**: **{total_ramp:.1f} L**")

    # --- Windy single-hour fetch & apply to ALL aerodromes ---
    st.markdown("### MET (Windy) – single hour for all aerodromes")
    windy_models = {"ICON-EU (default)": "iconEu", "GFS": "gfs", "AROME": "arome"}
    model_label = st.selectbox("Windy model", list(windy_models.keys()), index=0)
    WINDY_MODEL = windy_models[model_label]

    api_key = st.secrets.get("WINDY_API_KEY", "")
    if not api_key:
        st.warning("WINDY_API_KEY missing in secrets; you can still enter MET manually later (not shown here).")

    if "met" not in st.session_state:
        st.session_state.met = [{"temp": 15.0, "qnh": 1013.0, "wind_dir": 0.0, "wind_kt": 0.0} for _ in range(3)]
    if "forecast" not in st.session_state:
        st.session_state.forecast = [None, None, None]
    if "shared_hours" not in st.session_state:
        st.session_state.shared_hours = []  # list of (idx, dt, label)
    if "shared_ts_list" not in st.session_state:
        st.session_state.shared_ts_list = []  # underlying timestamps (ms)
    if "chosen_ts_ms" not in st.session_state:
        st.session_state.chosen_ts_ms = None

    colA, colB = st.columns([0.5, 0.5])
    with colA:
        if st.button("Fetch available hours (from first leg)"):
            if api_key:
                legs = st.session_state.legs
                ad0 = AERODROMES_DB[legs[0]["icao"]]
                resp0 = windy_point_forecast(ad0["lat"], ad0["lon"], WINDY_MODEL, ["wind","temp","pressure","windGust"], api_key)
                if "error" in resp0:
                    st.error(f"Windy error: {resp0.get('error')} {resp0.get('detail','')}")
                else:
                    st.session_state.forecast[0] = resp0
                    target_date = st.session_state.get("flight_date_utc", dt.datetime.utcnow().date())
                    hours = windy_hours_for_date(resp0, target_date)
                    if not hours:
                        hours = windy_hours_for_date(resp0, None)  # fallback: all
                    st.session_state.shared_hours = hours
                    st.session_state.shared_ts_list = [int(resp0["ts"][h[0]]) for h in hours]
                    if hours:
                        st.session_state.chosen_ts_ms = int(resp0["ts"][hours[0][0]])
                    st.experimental_rerun()
            else:
                st.error("Windy API key not set.")

    with colB:
        if st.session_state.shared_hours:
            labels = [h[2] for h in st.session_state.shared_hours]
            default_label = labels[0]
            chosen_label = st.selectbox("Choose forecast hour (UTC)", options=labels, index=0)
            # map back to timestamp
            idx = labels.index(chosen_label)
            resp0 = st.session_state.forecast[0]
            h_idx = st.session_state.shared_hours[idx][0]
            st.session_state.chosen_ts_ms = int(resp0["ts"][h_idx])

    if st.session_state.shared_hours and st.button("Apply hour to all aerodromes & compute"):
        legs = st.session_state.legs
        perf_rows = []
        for i, leg in enumerate(legs):
            ad = AERODROMES_DB[leg["icao"]]
            # Get/refresh forecast for each leg (cached call)
            resp = windy_point_forecast(ad["lat"], ad["lon"], WINDY_MODEL, ["wind","temp","pressure","windGust"], api_key)
            if "error" in resp:
                st.error(f"{leg['icao']}: Windy error {resp.get('error')}")
                continue
            st.session_state.forecast[i] = resp
            # Find index for chosen timestamp
            idx = windy_find_index_for_ts(resp, st.session_state.chosen_ts_ms)
            met = windy_unpack_at(resp, idx)
            if not met:
                st.warning(f"{leg['icao']}: No MET for chosen hour; using defaults.")
                met = {"temp": 15.0, "qnh": 1013.0, "wind_dir": 0.0, "wind_kt": 0.0}
            # Persist
            st.session_state.met[i] = met

            # Auto-select runway & compute performance
            pa_ft = ad["elev_ft"] + (1013.25 - float(met["qnh"])) * 27.0
            isa_temp = 15.0 - 2.0*(pa_ft/1000.0)
            da_ft = pa_ft + (120.0 * (float(met["temp"]) - isa_temp))

            candidates = []
            for rw in ad["runways"]:
                qfu = rw["qfu"]; paved = rw["paved"]; slope_pc = rw["slope_pc"]
                hw, xw_abs, side = wind_components(qfu, float(met["wind_dir"]), float(met["wind_kt"]))
                to_gr = bilinear(pa_ft, float(met["temp"]), TAKEOFF, 'GR')
                to_50 = bilinear(pa_ft, float(met["temp"]), TAKEOFF, '50ft')
                ldg_gr = bilinear(pa_ft, float(met["temp"]), LANDING, 'GR')
                ldg_50 = bilinear(pa_ft, float(met["temp"]), LANDING, '50ft')
                to_gr_corr  = to_corrections_takeoff(to_gr,  hw, paved=paved, slope_pc=slope_pc)
                ldg_gr_corr = ldg_corrections(ldg_gr, hw, paved=paved, slope_pc=slope_pc)
                used_toda_pct = (to_50/rw["toda"]*100.0) if rw["toda"]>0 else 0.0
                used_lda_pct  = (ldg_50/rw["lda"]*100.0) if rw["lda"]>0 else 0.0
                feasible = (to_50 <= rw["toda"]) and (ldg_50 <= rw["lda"])
                candidates.append({
                    "id": rw["id"], "qfu": qfu, "toda_av": rw["toda"], "lda_av": rw["lda"],
                    "paved": paved, "slope_pc": slope_pc,
                    "hw_comp": hw, "xw_abs": xw_abs, "xw_side": side,
                    "to_gr": to_gr_corr, "to_50": to_50, "ldg_gr": ldg_gr_corr, "ldg_50": ldg_50,
                    "feasible": feasible, "pa_ft": pa_ft, "da_ft": da_ft,
                    "used_toda_pct": used_toda_pct, "used_lda_pct": used_lda_pct,
                })
            feas = [c for c in candidates if c["feasible"]]
            pool = feas if feas else candidates
            best = sorted(pool, key=lambda c: (c["feasible"], c["hw_comp"], -c["xw_abs"]), reverse=True)[0]

            perf_rows.append({
                "role": leg["role"], "icao": leg["icao"], "name": ad["name"],
                "lat": ad["lat"], "lon": ad["lon"], "elev_ft": ad["elev_ft"],
                "rwy": best["id"], "qfu": best["qfu"], "toda_av": best["toda_av"], "lda_av": best["lda_av"],
                "slope_pc": best["slope_pc"], "paved": best["paved"],
                "temp": float(met["temp"]), "qnh": float(met["qnh"]),
                "wind_dir": float(met["wind_dir"]), "wind_kt": float(met["wind_kt"]),
                "pa_ft": best["pa_ft"], "da_ft": best["da_ft"],
                "to_gr": best["to_gr"], "to_50": best["to_50"], "ldg_gr": best["ldg_gr"], "ldg_50": best["ldg_50"],
                "hw_comp": best["hw_comp"], "xw_abs": best["xw_abs"], "xw_side": best["xw_side"],
                "used_toda_pct": best["used_toda_pct"], "used_lda_pct": best["used_lda_pct"]
            })

            # Selected runway summary chips
            xw_cls = xw_chip_class(best["xw_abs"])
            st.markdown(
                f"🧭 **{leg['role']} {leg['icao']} → RWY {best['id']}** "
                f"<span class='chip'>QFU {best['qfu']:.0f}°</span>"
                f"<span class='chip'>TODA {best['toda_av']:.0f} m</span>"
                f"<span class='chip'>LDA {best['lda_av']:.0f} m</span>"
                f"<span class='chip'>HW {best['hw_comp']:.0f} kt</span>"
                f"<span class='{xw_cls}'>XW {best['xw_side']} {best['xw_abs']:.0f} kt</span>"
                f"<span class='chip'>Use TO {best['used_toda_pct']:.0f}%</span>"
                f"<span class='chip'>Use LDG {best['used_lda_pct']:.0f}%</span>",
                unsafe_allow_html=True
            )

        # Save for PDF tab
        st.session_state["_perf_rows"] = perf_rows

    # Summary table (if available)
    perf_rows = st.session_state.get("_perf_rows", [])
    if perf_rows:
        st.markdown("#### Performance summary")
        def fmt(v): return f"{v:.0f}" if isinstance(v, (int,float)) else str(v)
        rows_html = []
        for r in perf_rows:
            rows_html.append(
                f"<tr>"
                f"<td>{r['role']} {r['icao']}</td><td>{r['rwy']}</td>"
                f"<td>{fmt(r['qfu'])}</td><td>{fmt(r['pa_ft'])}</td><td>{fmt(r['da_ft'])}</td>"
                f"<td>{fmt(r['to_gr'])}</td><td>{fmt(r['to_50'])}</td>"
                f"<td>{fmt(r['ldg_gr'])}</td><td>{fmt(r['ldg_50'])}</td>"
                f"<td>{fmt(r['toda_av'])}</td><td>{fmt(r['lda_av'])}</td>"
                f"<td>{fmt(r['used_toda_pct'])}%</td><td>{fmt(r['used_lda_pct'])}%</td>"
                f"</tr>"
            )
        st.markdown(
            "<table class='mb-table'><tr>"
            "<th>Leg / ICAO</th><th>RWY</th><th>QFU</th><th>PA ft</th><th>DA ft</th>"
            "<th>TO GR (m)*</th><th>TODR 50ft (m)</th><th>LND GR (m)*</th><th>LDR 50ft (m)</th>"
            "<th>TODA</th><th>LDA</th><th>%TODA used</th><th>%LDA used</th></tr>" +
            "".join(rows_html) + "</table>",
            unsafe_allow_html=True
        )

    # Persist fuel for PDF tab
    st.session_state["_fuel"] = {
        "policy": policy, "trip_l": trip_l, "cont_l": cont_l, "req_ramp": req_ramp,
        "extra_l": extra_l, "total_ramp": total_ramp,
        "su_min": su_min, "climb_min": climb_min, "enrt_h": enrt_h, "enrt_min": enrt_min,
        "desc_min": desc_min, "alt_min": alt_min, "reserve_min": reserve_min, "extra_min": extra_min
    }

# =========================
# 5) PDF – Fill template ONLY (no extra pages)
# =========================
with tab_pdf:
    st.markdown("### PDF – M&B and Performance Data Sheet")
    reg = st.session_state.get("reg", "CS-XXX")
    mission = st.session_state.get("mission", "001")
    date_str = st.session_state.get("date_str", dt.datetime.now(pytz.UTC).strftime("%d/%m/%Y"))

    # --- Field mapping helpers ---
    def load_pdf_any(path: Path):
        try:
            return "pdfrw", Rd_pdfrw(str(path))
        except Exception:
            try:
                return "pypdf", Rd_pypdf(str(path))
            except Exception as e:
                raise RuntimeError(f"Cannot read PDF: {e}")

    # Known field names from your template
    BASE_FIELDS = {
        "Registration": ["Textbox19"],
        "Date": ["Textbox18"],
        "TotalWeight": ["Textbox14"],
        "CG": ["Textbox16"],
        "MTOW": ["Textbox17"],
    }
    # Departure page fields (as per your earlier code)
    DEP_FIELDS = {
        "ICAO":     ["Textbox22"],
        "PA":       ["Textbox50"],
        "DA":       ["Textbox49"],
        "TODA_LDA": ["Textbox47"],   # "TODA/LDA" as "1800/1800"
        "TODR":     ["Textbox45"],   # 50 ft takeoff distance
        "LDR":      ["Textbox41"],   # 50 ft landing distance
        # Extra (if your template has % fields, try these; otherwise no effect)
        "TODR_PCT": ["TODR_pct_Dep", "%TODA_Dep"],
        "LDR_PCT":  ["LDR_pct_Dep", "%LDA_Dep"],
    }

    def pdfrw_set_field(fields, names, value, color_rgb=None):
        if isinstance(names, str):
            names = [names]
        written = False
        for name in names:
            if not name:
                continue
            for f in fields:
                if f.get('/T') and f['/T'][1:-1] == name:
                    f.update(PdfDict(V=str(value)))
                    f.update(PdfDict(AP=None))
                    if color_rgb:
                        r, g, b = color_rgb
                        f.update(PdfDict(DA=f"{r/255:.3f} {g/255:.3f} {b/255:.3f} rg /Helv 10 Tf"))
                    written = True
                    break
            if written:
                break
        return written

    if st.button("Generate filled PDF"):
        if not PDF_TEMPLATE.exists():
            st.error(f"Template not found: {PDF_TEMPLATE}")
            st.stop()

        engine, reader = load_pdf_any(PDF_TEMPLATE)
        wb = st.session_state.get("_wb", {})
        perf_rows = st.session_state.get("_perf_rows", [])
        fuel = st.session_state.get("_fuel", {})
        wt_total = float(wb.get("total_weight", 0.0))
        cg_val = float(wb.get("cg", 0.0))

        # Colors (same logic you used)
        wt_color = (30,150,30) if wt_total <= AC['max_takeoff_weight'] else (200,0,0)
        lo, hi = AC['cg_limits']
        if cg_val < lo or cg_val > hi:
            cg_color = (200,0,0)
        else:
            margin = 0.05*(hi-lo)
            cg_color = (200,150,30) if (cg_val<lo+margin or cg_val>hi-margin) else (30,150,30)

        out_main_path = Path(f"MB_Performance_{reg}_{mission}.pdf")

        if engine == "pdfrw" and hasattr(reader, 'Root') and '/AcroForm' in reader.Root:
            fields = reader.Root.AcroForm.Fields

            # Base/top
            pdfrw_set_field(fields, BASE_FIELDS["Registration"], reg)
            pdfrw_set_field(fields, BASE_FIELDS["Date"], date_str)
            pdfrw_set_field(fields, BASE_FIELDS["TotalWeight"], f"{wt_total:.1f}", wt_color)
            pdfrw_set_field(fields, BASE_FIELDS["CG"], f"{cg_val:.3f}", cg_color)
            pdfrw_set_field(fields, BASE_FIELDS["MTOW"], f"{AC['max_takeoff_weight']:.0f}")

            # Departure block (your template has explicit fields for Dep page)
            if len(perf_rows) >= 1:
                r = perf_rows[0]
                toda_lda_str = f"{int(r['toda_av'])}/{int(r['lda_av'])}"
                to_pct = int(round(r['used_toda_pct']))
                ldg_pct = int(round(r['used_lda_pct']))

                pdfrw_set_field(fields, DEP_FIELDS["ICAO"], r['icao'])
                pdfrw_set_field(fields, DEP_FIELDS["PA"], f"{r['pa_ft']:.0f}")
                pdfrw_set_field(fields, DEP_FIELDS["DA"], f"{r['da_ft']:.0f}")
                pdfrw_set_field(fields, DEP_FIELDS["TODA_LDA"], toda_lda_str)
                pdfrw_set_field(fields, DEP_FIELDS["TODR"], f"{r['to_50']:.0f}")
                pdfrw_set_field(fields, DEP_FIELDS["LDR"], f"{r['ldg_50']:.0f}")
                # Try to write percentages if fields exist
                pdfrw_set_field(fields, DEP_FIELDS["TODR_PCT"], f"{to_pct}%")
                pdfrw_set_field(fields, DEP_FIELDS["LDR_PCT"], f"{ldg_pct}%")

            # Fuel block (write liters + times where available)
            # If Simplified policy, only Total Ramp = 90 L; other fields 0/blank is fine.
            su_min = int(fuel.get("su_min", 0))
            climb_min = int(fuel.get("climb_min", 0))
            enrt_min_total = int(fuel.get("enrt_h", 0))*60 + int(fuel.get("enrt_min", 0))
            desc_min = int(fuel.get("desc_min", 0))
            alt_min = int(fuel.get("alt_min", 0))
            reserve_min = int(fuel.get("reserve_min", 0))
            extra_min = int(fuel.get("extra_min", 0))

            trip_l = float(fuel.get("trip_l", 0.0))
            cont_l = float(fuel.get("cont_l", 0.0))
            req_ramp = float(fuel.get("req_ramp", 0.0))
            extra_l = float(fuel.get("extra_l", 0.0))
            total_ramp = float(fuel.get("total_ramp", 0.0))

            # If your PDF uses the same fields as your earlier sheet, they will be picked up here.
            # (If not, they will simply be ignored; no inspector UI is included.)
            fuel_field_map = {
                # times
                "Taxi_T": su_min, "Climb_T": climb_min, "Enroute_T": enrt_min_total, "Descent_T": desc_min,
                "Alt_T": alt_min, "Res_T": reserve_min, "Extra_T": extra_min,
                "Trip_T": (climb_min + enrt_min_total + desc_min),
                "Cont_T": int(round((climb_min + enrt_min_total + desc_min) * 0.05)),
                "RampReq_T": su_min + (climb_min + enrt_min_total + desc_min) + int(round((climb_min + enrt_min_total + desc_min) * 0.05)) + alt_min + reserve_min,
                "Total_T": su_min + (climb_min + enrt_min_total + desc_min) + int(round((climb_min + enrt_min_total + desc_min) * 0.05)) + alt_min + reserve_min + extra_min,
                # liters
                "Trip_F": f"{trip_l:.1f}", "Cont_F": f"{cont_l:.1f}", "RampReq_F": f"{req_ramp:.1f}",
                "Alt_F": "", "Res_F": "", "Extra_F": f"{extra_l:.1f}", "Total_F": f"{total_ramp:.1f}",
            }
            # Try multiple likely names for each (kept concise)
            FUEL_ALIASES = {
                "Taxi_T": ["Taxi_T","StartTaxi_T","Start_T"],
                "Climb_T":["Climb_T"], "Enroute_T":["Enroute_T","Cruise_T"], "Descent_T":["Descent_T"],
                "Trip_T":["Trip_T"], "Cont_T":["Cont_T","Contingency_T"], "Alt_T":["Alt_T","Alternate_T"],
                "Res_T":["Res_T","Reserve_T","FinalReserve_T"], "RampReq_T":["RampReq_T","RequiredRamp_T"], "Total_T":["Total_T","TotalRamp_T"],
                "Trip_F":["Trip_F"], "Cont_F":["Cont_F","Contingency_F"], "Alt_F":["Alt_F","Alternate_F"],
                "Res_F":["Res_F","Reserve_F","FinalReserve_F"], "RampReq_F":["RampReq_F","RequiredRamp_F"], "Extra_F":["Extra_F"], "Total_F":["Total_F","TotalRamp_F"],
            }
            for k, v in fuel_field_map.items():
                for alias in FUEL_ALIASES.get(k, []):
                    pdfrw_set_field(fields, alias, v)

            # Write out
            writer = Wr_pdfrw()
            writer.write(str(out_main_path), reader)

        else:
            # pypdf fallback – fill only basic top fields
            base_r = Rd_pypdf(str(PDF_TEMPLATE))
            merger = Wr_pypdf()
            for p in base_r.pages: merger.add_page(p)
            if "/AcroForm" in base_r.trailer["/Root"]:
                merger._root_object.update({"/AcroForm": base_r.trailer["/Root"]["/AcroForm"]})
                merger._root_object["/AcroForm"].update({"/NeedAppearances": True})
            merger.update_page_form_field_values(base_r.pages[0], {
                "Textbox19": reg, "Textbox18": date_str,
                "Textbox14": f"{wt_total:.1f}", "Textbox16": f"{cg_val:.3f}",
                "Textbox17": f"{AC['max_takeoff_weight']:.0f}",
            })
            with open(out_main_path, "wb") as f:
                merger.write(f)

        st.success("PDF generated and filled.")
        with open(out_main_path, 'rb') as f:
            st.download_button("Download PDF", f, file_name=out_main_path.name, mime="application/pdf")
