# Streamlit app – Tecnam P2008 (M&B + Performance) – v5.0
# Requirements:
#   streamlit
#   requests
#   pdfrw==0.4
#   pypdf>=4.2.0
#   fpdf
#
# Secrets required:
#   WINDY_API_KEY
#   GITHUB_GIST_TOKEN
#   GITHUB_GIST_ID
#
# Notes:
# - Forecast selection is HOURLY (independent from ETD). ICON-EU default.
# - Fleet EW/Moment are persisted in a GitHub Gist file "fleet_p2008.json".
# - PDF is FILLED (not only a report). A field inspector is included to help map fields if needed.

import streamlit as st
import datetime as dt
from math import cos, sin, radians, sqrt, atan2, degrees
import json
import requests
import unicodedata
from pathlib import Path

# PDF filling/merging
from pdfrw import PdfReader as Rd_pdfrw, PdfWriter as Wr_pdfrw, PdfDict
from pypdf import PdfReader as Rd_pypdf, PdfWriter as Wr_pypdf
from fpdf import FPDF

# -----------------------------
# App setup & styles
# -----------------------------
st.set_page_config(
    page_title="Tecnam P2008 – Mass & Balance & Performance",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1200px !important; }
      .mb-header{font-size:1.35rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:8px;margin:2px 0 14px}
      .mb-table{border-collapse:collapse;width:100%;font-size:.95rem}
      .mb-table th{border-bottom:2px solid #cbd0d6;text-align:left}
      .mb-table td{padding:3px 6px;border-bottom:1px dashed #e5e7ec}
      .ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
      .cwok{color:#1d8533}.cwwarn{color:#d8aa22}.cwbad{color:#c21c1c}
      .mb-summary{display:flex;justify-content:space-between;margin:4px 0}
      .chip{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef2f7;margin-left:8px;font-size:.85rem}
      .chip-red{background:#fde8e8}.chip-yellow{background:#fff6db}.chip-green{background:#e8f7ec}
    </style>
    """,
    unsafe_allow_html=True,
)

def ascii_safe(text):
    if not isinstance(text, str):
        return str(text)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

# -----------------------------
# Constants & Data
# -----------------------------
PDF_TEMPLATE_PATHS = [
    "/mnt/data/TecnamP2008MBPerformanceSheet_MissionX.pdf",  # uploaded path (per your message)
    "TecnamP2008MBPerformanceSheet_MissionX.pdf",            # repo root fallback
]

AC = {
    "name": "Tecnam P2008 JC",
    "fuel_arm": 2.209,
    "pilot_arm": 1.800,
    "baggage_arm": 2.417,
    "max_takeoff_weight": 650.0,
    "max_fuel_volume": 124.0,      # L
    "max_passenger_weight": 230.0, # total students+instructor
    "max_baggage_weight": 20.0,
    "cg_limits": (1.841, 1.978),   # m
    "fuel_density": 0.72,          # kg/L
}

# Aerodrome DB (Portugal) – each RWY direction with declared distances
AERODROMES_DB = {
    "LPSO": {
        "name": "Ponte de Sor",
        "lat": 39.211667, "lon": -8.057778, "elev_ft": 390.0,
        "runways": [
            {"id": "03", "qfu": 30.0,  "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True},
            {"id": "21", "qfu": 210.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPEV": {
        "name": "Évora",
        "lat": 38.529722, "lon": -7.891944, "elev_ft": 807.0,
        "runways": [
            {"id": "01", "qfu": 10.0,  "toda": 1300.0, "lda": 1245.0, "slope_pc": 0.0, "paved": True},
            {"id": "19", "qfu": 190.0, "toda": 1300.0, "lda": 1245.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPCB": {
        "name": "Castelo Branco",
        "lat": 39.848333, "lon": -7.441667, "elev_ft": 1251.0,
        "runways": [
            {"id": "16", "qfu": 160.0, "toda": 1520.0, "lda": 1460.0, "slope_pc": 0.0, "paved": True},
            {"id": "34", "qfu": 340.0, "toda": 1520.0, "lda": 1460.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPCS": {
        "name": "Cascais",
        "lat": 38.725556, "lon": -9.355278, "elev_ft": 326.0,
        "runways": [
            {"id": "17", "qfu": 170.0, "toda": 1700.0, "lda": 1700.0, "slope_pc": 0.0, "paved": True},
            {"id": "35", "qfu": 350.0, "toda": 1700.0, "lda": 1700.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPFR": {
        "name": "Faro",
        "lat": 37.014444, "lon": -7.965833, "elev_ft": 24.0,
        "runways": [
            {"id": "10", "qfu": 100.0, "toda": 2490.0, "lda": 2490.0, "slope_pc": 0.0, "paved": True},
            {"id": "28", "qfu": 280.0, "toda": 2490.0, "lda": 2490.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPPT": {
        "name": "Lisboa",
        "lat": 38.774167, "lon": -9.134167, "elev_ft": 355.0,
        "runways": [
            {"id": "02", "qfu": 20.0,  "toda": 3805.0, "lda": 3715.0, "slope_pc": 0.0, "paved": True},
            {"id": "20", "qfu": 200.0, "toda": 3805.0, "lda": 3715.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPPR": {
        "name": "Porto",
        "lat": 41.248056, "lon": -8.681111, "elev_ft": 227.0,
        "runways": [
            {"id": "17", "qfu": 170.0, "toda": 3480.0, "lda": 3200.0, "slope_pc": 0.0, "paved": True},
            {"id": "35", "qfu": 350.0, "toda": 3480.0, "lda": 3200.0, "slope_pc": 0.0, "paved": True},
        ],
    },
}

# Performance tables (AFM extracts) – same as your base
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
VY = {
    650:{0:70,2000:69,4000:67,6000:66,8000:65,10000:64,12000:63,14000:62},
    600:{0:70,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:62},
    550:{0:69,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:61},
}

# Crosswind thresholds (per your request)
XW_GREEN_MAX = 8    # ≤ 8 kt green
XW_YELLOW_MAX = 15  # >8 & ≤15 yellow; >15 red

# -----------------------------
# Helpers
# -----------------------------
def clamp(v, lo, hi): return max(lo, min(hi, v))

def interp1(x, x0, x1, y0, y1):
    if x1 == x0: return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def bilinear(pa, temp, table, key):
    pas = sorted(table.keys())
    pa = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa])
    p1 = min([p for p in pas if p >= pa])
    temps = [-25, 0, 25, 50]
    t = clamp(temp, temps[0], temps[-1])
    if t <= 0: t0, t1 = -25, 0
    elif t <= 25: t0, t1 = 0, 25
    else: t0, t1 = 25, 50
    v00 = table[p0][key][t0]; v01 = table[p0][key][t1]
    v10 = table[p1][key][t0]; v11 = table[p1][key][t1]
    v0 = interp1(t, t0, t1, v00, v01)
    v1 = interp1(t, t0, t1, v10, v11)
    return interp1(pa, p0, p1, v0, v1)

def roc_interp(pa, temp, weight):
    w = clamp(weight, 550.0, 650.0)
    def roc_for_w(w_):
        tab = ROC[int(w_)]; pas = sorted(tab.keys())
        pa_c = clamp(pa, pas[0], pas[-1])
        p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
        temps = [-25, 0, 25, 50]
        t = clamp(temp, temps[0], temps[-1])
        if t <= 0: t0, t1 = -25, 0
        elif t <= 25: t0, t1 = 0, 25
        else: t0, t1 = 25, 50
        v00 = tab[p0][t0]; v01 = tab[p0][t1]; v10 = tab[p1][t0]; v11 = tab[p1][t1]
        v0 = interp1(t, t0, t1, v00, v01); v1 = interp1(t, t0, t1, v10, v11)
        return interp1(pa_c, p0, p1, v0, v1)
    if w <= 600: return interp1(w, 550, 600, roc_for_w(550), roc_for_w(600))
    else: return interp1(w, 600, 650, roc_for_w(600), roc_for_w(650))

def wind_components(qfu_deg, wind_dir_deg, wind_speed):
    """Return (headwind, crosswind_abs, side_str 'R'/'L'/'')."""
    if qfu_deg is None or wind_dir_deg is None or wind_speed is None:
        return 0.0, 0.0, ""
    diff = radians((wind_dir_deg - qfu_deg) % 360)
    hw = wind_speed * cos(diff)
    cw = wind_speed * sin(diff)
    side = "R" if cw > 0 else ("L" if cw < 0 else "")
    return hw, abs(cw), side

def xw_class(xw_abs):
    if xw_abs <= XW_GREEN_MAX: return "chip chip-green", "cwok"
    if xw_abs <= XW_YELLOW_MAX: return "chip chip-yellow", "cwwarn"
    return "chip chip-red", "cwbad"

def to_corrections_takeoff(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = float(ground_roll)
    if headwind_kt >= 0: gr -= 5.0 * headwind_kt
    else: gr += 15.0 * abs(headwind_kt)
    if paved: gr *= 0.9
    if slope_pc: gr *= (1.0 + 0.07 * (slope_pc/1.0))
    return max(gr, 0.0)

def ldg_corrections(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = float(ground_roll)
    if headwind_kt >= 0: gr -= 4.0 * headwind_kt
    else: gr += 13.0 * abs(headwind_kt)
    if paved: gr *= 0.9
    if slope_pc: gr *= (1.0 - 0.03 * (slope_pc/1.0))
    return max(gr, 0.0)

# -----------------------------
# Windy API (hourly)
# -----------------------------
WINDY_ENDPOINT = "https://api.windy.com/api/point-forecast/v2"

@st.cache_data(ttl=900, show_spinner=False)
def windy_point_forecast(lat, lon, model, params, api_key):
    headers = {"Content-Type": "application/json"}
    body = {
        "lat": round(float(lat), 3),
        "lon": round(float(lon), 3),
        "model": model,                 # "iconEu" default
        "parameters": params,           # ["wind","temp","pressure","windGust"]
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

def windy_list_hours(resp):
    """Return [(idx, 'YYYY-MM-DD HH:00Z'), ...] from Windy response."""
    if not resp or "ts" not in resp or not resp["ts"]:
        return []
    out = []
    for i, tms in enumerate(resp["ts"]):
        dt_utc = dt.datetime.utcfromtimestamp(tms/1000.0).replace(tzinfo=dt.timezone.utc)
        out.append((i, dt_utc.strftime("%Y-%m-%d %H:00Z")))
    return out

def windy_unpack_at(resp, idx):
    if idx is None: return None
    def getv(key):
        arr = resp.get(key, [])
        return arr[idx] if arr and idx < len(arr) else None
    u = getv("wind_u-surface"); v = getv("wind_v-surface"); gust = getv("gust-surface")
    if u is None or v is None: return None
    speed_ms = sqrt(u*u + v*v)
    dir_deg = (degrees(atan2(-u, -v)) + 360.0) % 360.0
    speed_kt = speed_ms * 1.94384
    temp_val = getv("temp-surface")
    temp_c = None
    if temp_val is not None:
        temp_c = float(temp_val)
        if temp_c > 100:  # Kelvin -> C
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

# -----------------------------
# GitHub Gist persistence (fleet)
# -----------------------------
GIST_FILE = "fleet_p2008.json"

def gist_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gist_load_fleet(token, gist_id):
    try:
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(token), timeout=15)
        if r.status_code != 200:
            return None, f"GitHub error {r.status_code}: {r.text}"
        data = r.json()
        files = data.get("files", {})
        if GIST_FILE in files and files[GIST_FILE].get("content") is not None:
            content = files[GIST_FILE]["content"]
            return json.loads(content), None
        return None, "Gist file not found; will create on first save."
    except Exception as e:
        return None, str(e)

def gist_save_fleet(token, gist_id, fleet_dict):
    try:
        payload = {
            "files": {
                GIST_FILE: {
                    "content": json.dumps(fleet_dict, indent=2, sort_keys=True)
                }
            }
        }
        r = requests.patch(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(token), data=json.dumps(payload), timeout=15)
        if r.status_code not in (200, 201):
            return f"GitHub error {r.status_code}: {r.text}"
        return None
    except Exception as e:
        return str(e)

# -----------------------------
# Session defaults
# -----------------------------
if "fleet" not in st.session_state:
    # Seed with a few P2008 regs; you'll persist your own set via Gist.
    st.session_state.fleet = {
        "CS-DHS": {"ew": None, "ew_moment": None},
        "CS-DHU": {"ew": None, "ew_moment": None},
        "CS-DHW": {"ew": None, "ew_moment": None},
        "CS-DHT": {"ew": None, "ew_moment": None},
        "CS-ECC": {"ew": None, "ew_moment": None},
        "CS-ECD": {"ew": None, "ew_moment": None},
    }

DEFAULT_LEGS = [
    {"role": "Departure", "icao": "LPSO"},
    {"role": "Arrival",   "icao": "LPEV"},
    {"role": "Alternate", "icao": "LPCB"},
]
if "legs" not in st.session_state:
    st.session_state.legs = [dict(x) for x in DEFAULT_LEGS]

# Forecast storage per leg
if "forecast" not in st.session_state:
    st.session_state.forecast = [None, None, None]     # raw Windy response per leg
if "hours" not in st.session_state:
    st.session_state.hours = [[], [], []]              # [(idx, "YYYY-MM-DD HH:00Z"), ...]
if "hour_idx" not in st.session_state:
    st.session_state.hour_idx = [None, None, None]     # selected index per leg
if "met" not in st.session_state:
    st.session_state.met = [{"temp": 15.0, "qnh": 1013.0, "wind_dir": 0.0, "wind_kt": 0.0} for _ in range(3)]

# -----------------------------
# Sidebar – Fleet (hidden in expander) + Persistence
# -----------------------------
with st.sidebar:
    st.subheader("⚙️ Settings")
    if "WINDY_API_KEY" not in st.secrets:
        st.error("Missing WINDY_API_KEY in secrets.")
    windy_models = {"ICON-EU (default)": "iconEu", "GFS": "gfs", "AROME": "arome"}
    model_label = st.selectbox("Windy model", list(windy_models.keys()), index=0)
    WINDY_MODEL = windy_models[model_label]
    use_same_hour = st.checkbox("Use the same forecast hour for all aerodromes", True)

    st.markdown("---")
    st.subheader("🛩️ Fleet (EW & Moment)")
    with st.expander("Manage fleet (persisted in GitHub Gist)", expanded=False):
        token = st.secrets.get("GITHUB_GIST_TOKEN", "")
        gist_id = st.secrets.get("GITHUB_GIST_ID", "")
        if token and gist_id:
            if st.button("Load fleet from Gist"):
                gdata, gerr = gist_load_fleet(token, gist_id)
                if gdata is not None:
                    st.session_state.fleet.update(gdata)
                    st.success(f"Loaded {len(gdata)} registrations from Gist.")
                else:
                    st.warning(f"Could not load: {gerr}")
        else:
            st.info("Add GITHUB_GIST_TOKEN and GITHUB_GIST_ID to secrets to enable persistence.")

        regs_all = list(st.session_state.fleet.keys())
        add_reg = st.text_input("Add/Update registration (e.g., CS-ABC)", value="")
        col_add1, col_add2 = st.columns([0.5,0.5])
        with col_add1:
            ew_new = st.number_input("Empty Weight (kg)", min_value=0.0, value=0.0, step=0.1, key="fleet_ew_new")
        with col_add2:
            em_new = st.number_input("Empty Weight Moment (kg·m)", min_value=0.0, value=0.0, step=0.01, key="fleet_em_new")
        if st.button("Save EW/Moment to local session"):
            if add_reg.strip():
                st.session_state.fleet[add_reg.strip().upper()] = {"ew": ew_new, "ew_moment": em_new}
                st.success(f"Saved locally for {add_reg.strip().upper()}.")

        if token and gist_id:
            if st.button("Persist current fleet to Gist"):
                err = gist_save_fleet(token, gist_id, st.session_state.fleet)
                if err: st.error(err)
                else: st.success("Fleet saved to Gist.")

        if regs_all:
            del_reg = st.selectbox("Remove registration", options=[""] + regs_all)
            if st.button("Remove selected") and del_reg:
                st.session_state.fleet.pop(del_reg, None)
                st.success(f"Removed: {del_reg}")

# -----------------------------
# Tabs
# -----------------------------
st.markdown('<div class="mb-header">Tecnam P2008 – Mass & Balance & Performance</div>', unsafe_allow_html=True)
tab_setup, tab_aero, tab_wb, tab_perf, tab_pdf = st.tabs([
    "1) Flight & Aircraft", "2) Aerodromes & MET", "3) Weight & Balance",
    "4) Performance & Fuel", "5) PDF"
])

# ---- 1) Flight & Aircraft ----
with tab_setup:
    c1, c2 = st.columns([0.55, 0.45])
    with c1:
        st.markdown("### Flight data (UTC)")
        today = dt.datetime.utcnow().date()
        default_time = (dt.datetime.utcnow() + dt.timedelta(hours=1)).time().replace(second=0, microsecond=0)
        flight_date = st.date_input("Date (UTC)", value=today)
        flight_time = st.time_input("Planned off-block/takeoff time (UTC)", value=default_time, step=300)
        ETD_UTC = dt.datetime.combine(flight_date, flight_time).replace(tzinfo=dt.timezone.utc)
        st.info(f"ETD UTC: {ETD_UTC.strftime('%Y-%m-%d %H:%MZ')}")

    with c2:
        st.markdown("### Aircraft")
        regs = list(st.session_state.fleet.keys()) or ["CS-XXX"]
        selected_reg = st.selectbox("Registration", regs, key="selected_reg")
        st.caption("EW & Moment are maintained in the sidebar → Fleet settings (persisted to GitHub Gist).")
        st.session_state["reg"] = selected_reg
        st.session_state["etd_utc"] = ETD_UTC

# ---- helper: choose best runway ----
def choose_best_runway(ad, temp_c, qnh, wind_dir, wind_kt):
    pa_ft = ad["elev_ft"] + (1013.25 - qnh) * 27.0
    isa_temp = 15.0 - 2.0*(pa_ft/1000.0)
    da_ft = pa_ft + (120.0 * (temp_c - isa_temp))

    candidates = []
    for rw in ad["runways"]:
        qfu = rw["qfu"]; paved = rw["paved"]; slope_pc = rw["slope_pc"]
        hw, xw_abs, side = wind_components(qfu, wind_dir, wind_kt)
        to_gr = bilinear(pa_ft, temp_c, TAKEOFF, 'GR')
        to_50 = bilinear(pa_ft, temp_c, TAKEOFF, '50ft')
        ldg_gr = bilinear(pa_ft, temp_c, LANDING, 'GR')
        ldg_50 = bilinear(pa_ft, temp_c, LANDING, '50ft')
        to_gr_corr  = to_corrections_takeoff(to_gr,  hw, paved=paved, slope_pc=slope_pc)
        ldg_gr_corr = ldg_corrections(ldg_gr, hw, paved=paved, slope_pc=slope_pc)
        feasible = (to_50 <= rw["toda"]) and (ldg_50 <= rw["lda"])
        candidates.append({
            "id": rw["id"], "qfu": qfu, "toda_av": rw["toda"], "lda_av": rw["lda"],
            "paved": paved, "slope_pc": slope_pc,
            "hw_comp": hw, "xw_abs": xw_abs, "xw_side": side,
            "to_gr": to_gr_corr, "to_50": to_50, "ldg_gr": ldg_gr_corr, "ldg_50": ldg_50,
            "feasible": feasible, "pa_ft": pa_ft, "da_ft": da_ft
        })
    # prefer feasible, then max headwind, then min crosswind
    feasibles = [c for c in candidates if c["feasible"]]
    pool = feasibles if feasibles else candidates
    best = sorted(pool, key=lambda c: (c["feasible"], c["hw_comp"], -c["xw_abs"]), reverse=True)[0]
    return best, candidates

# ---- 2) Aerodromes & MET ----
with tab_aero:
    st.markdown("### Aerodromes (fixed: Departure, Arrival, Alternate) + MET (hourly)")
    st.caption("Wind is used to auto-select the best runway. Choose a Windy forecast hour (UTC). ICON-EU is the default model.")

    perf_rows = []
    for i, leg in enumerate(st.session_state.legs):
        role = leg.get("role", ["Departure","Arrival","Alternate"][i])
        c1, c2, c3 = st.columns([0.36, 0.36, 0.28])

        with c1:
            icao_options = sorted(AERODROMES_DB.keys())
            default_icao = leg.get("icao", icao_options[0])
            icao = st.selectbox(f"{role} – Aerodrome (ICAO)", options=icao_options,
                                index=icao_options.index(default_icao) if default_icao in icao_options else 0,
                                key=f"icao_{i}")
            ad = AERODROMES_DB[icao]
            st.write(f"**{ad['name']}**  \nLat {ad['lat']:.5f}, Lon {ad['lon']:.5f}  \nElev {ad['elev_ft']:.0f} ft")

        with c2:
            # MET fields (editable)
            temp_c = st.number_input("OAT (°C)", value=st.session_state.met[i]["temp"], step=0.1, key=f"temp_{i}")
            qnh    = st.number_input("QNH (hPa)", min_value=900.0, max_value=1050.0, value=st.session_state.met[i]["qnh"], step=0.1, key=f"qnh_{i}")
            wind_dir = st.number_input("Wind FROM (°)", min_value=0.0, max_value=360.0, value=st.session_state.met[i]["wind_dir"], step=1.0, key=f"wdir_{i}")
            wind_kt  = st.number_input("Wind speed (kt)", min_value=0.0, value=st.session_state.met[i]["wind_kt"], step=1.0, key=f"wspd_{i}")

        with c3:
            # Fetch hours and allow selection
            if st.button("Fetch forecast hours (Windy)", key=f"fetch_{i}"):
                api_key = st.secrets.get("WINDY_API_KEY", "")
                if not api_key:
                    st.error("Windy API key not found in secrets (WINDY_API_KEY).")
                else:
                    resp = windy_point_forecast(ad["lat"], ad["lon"], WINDY_MODEL, ["wind","temp","pressure","windGust"], api_key)
                    if "error" in resp:
                        st.error(f"Windy error: {resp.get('error')} {resp.get('detail','')}")
                    else:
                        st.session_state.forecast[i] = resp
                        st.session_state.hours[i] = windy_list_hours(resp)
                        # default selection: nearest to ETD (but user can change freely)
                        if st.session_state.hours[i]:
                            # pick nearest to ETD:
                            ts_list = [h[1] for h in st.session_state.hours[i]]
                            # naive nearest: choose first if not using ETD
                            st.session_state.hour_idx[i] = st.session_state.hours[i][0][0]
                        st.success(f"Loaded {len(st.session_state.hours[i])} hours from Windy.")
                        if use_same_hour:
                            # propagate hours to all legs if empty
                            for j in range(3):
                                if j != i and not st.session_state.hours[j]:
                                    st.session_state.hours[j] = list(st.session_state.hours[i])
                                    st.session_state.forecast[j] = st.session_state.forecast[i]

                        st.experimental_rerun()

            hours = st.session_state.hours[i]
            if hours:
                idxs = [h[0] for h in hours]; labels = [h[1] for h in hours]
                default_idx = st.session_state.hour_idx[i] if st.session_state.hour_idx[i] in idxs else idxs[0]
                sel_label = st.selectbox("Forecast hour (UTC)", options=labels, index=labels.index(hours[idxs.index(default_idx)][1]), key=f"hour_label_{i}")
                # map label back to idx
                sel_idx = hours[labels.index(sel_label)][0]
                if use_same_hour:
                    # apply to all legs that have hours
                    for j in range(3):
                        if st.session_state.hours[j]:
                            st.session_state.hour_idx[j] = sel_idx
                else:
                    st.session_state.hour_idx[i] = sel_idx

                if st.button("Apply hour to fields", key=f"apply_{i}"):
                    resp = st.session_state.forecast[i]
                    idx = st.session_state.hour_idx[i]
                    met = windy_unpack_at(resp, idx)
                    if met:
                        # Update editable widgets for this leg
                        st.session_state[f"temp_{i}"] = met["temp"] if met["temp"] is not None else temp_c
                        st.session_state[f"qnh_{i}"]  = met["qnh"] if met["qnh"] is not None else qnh
                        st.session_state[f"wdir_{i}"] = met["wind_dir"] if met["wind_dir"] is not None else wind_dir
                        st.session_state[f"wspd_{i}"] = met["wind_kt"] if met["wind_kt"] is not None else wind_kt
                        # Store also in met[] for downstream calc
                        st.session_state.met[i].update({
                            "temp": st.session_state[f"temp_{i}"],
                            "qnh": st.session_state[f"qnh_{i}"],
                            "wind_dir": st.session_state[f"wdir_{i}"],
                            "wind_kt": st.session_state[f"wspd_{i}"],
                        })
                        st.success(f"Applied {labels[idxs.index(idx)]}")
                        st.experimental_rerun()
                    else:
                        st.warning("No usable data at selected hour.")

        # Auto-select runway
        best, all_rw = choose_best_runway(ad, float(temp_c), float(qnh), float(wind_dir), float(wind_kt))
        feas = "✅" if best["feasible"] else "⚠️"
        xw_chip_cls, _ = xw_class(best["xw_abs"])
        # UI chips: QFU/TODA/LDA/HW & XW with color
        st.markdown(
            f"🧭 **Selected runway:** {best['id']} "
            f"<span class='chip'>QFU {best['qfu']:.0f}°</span>"
            f"<span class='chip'>TODA {best['toda_av']:.0f} m</span>"
            f"<span class='chip'>LDA {best['lda_av']:.0f} m</span>"
            f"<span class='chip'>HW {best['hw_comp']:.0f} kt</span>"
            f"<span class='{xw_chip_cls}'>XW {best['xw_side']} {best['xw_abs']:.0f} kt</span> {feas}",
            unsafe_allow_html=True
        )

        # Persist leg ICAO
        st.session_state.legs[i] = {"role": role, "icao": icao}

        # Collect for summary/PDF
        perf_rows.append({
            "role": role, "icao": icao, "name": ad["name"],
            "lat": ad["lat"], "lon": ad["lon"], "elev_ft": ad["elev_ft"],
            "rwy": best["id"], "qfu": best["qfu"], "toda_av": best["toda_av"], "lda_av": best["lda_av"],
            "slope_pc": best["slope_pc"], "paved": best["paved"],
            "temp": float(temp_c), "qnh": float(qnh), "wind_dir": float(wind_dir), "wind_kt": float(wind_kt),
            "pa_ft": best["pa_ft"], "da_ft": best["da_ft"],
            "to_gr": best["to_gr"], "to_50": best["to_50"], "ldg_gr": best["ldg_gr"], "ldg_50": best["ldg_50"],
            "hw_comp": best["hw_comp"], "xw_abs": best["xw_abs"], "xw_side": best["xw_side"], "feasible": best["feasible"]
        })

    # Summary table
    st.markdown("#### Performance summary (auto-selected runways)")
    def fmt(v): return f"{v:.0f}" if isinstance(v, (int,float)) else str(v)
    rows_html = []
    for r in perf_rows:
        to_ok  = "ok" if r["to_50"]  <= r["toda_av"] else "bad"
        ldg_ok = "ok" if r["ldg_50"] <= r["lda_av"]  else "bad"
        _, cw_cls = xw_class(r["xw_abs"])
        rows_html.append(
            f"<tr>"
            f"<td>{r['role']} {r['icao']}</td><td>{r['rwy']}</td>"
            f"<td>{fmt(r['qfu'])}</td><td>{fmt(r['pa_ft'])}</td><td>{fmt(r['da_ft'])}</td>"
            f"<td>{fmt(r['to_gr'])}</td>"
            f"<td class='{to_ok}'>{fmt(r['to_50'])}</td>"
            f"<td>{fmt(r['ldg_gr'])}</td>"
            f"<td class='{ldg_ok}'>{fmt(r['ldg_50'])}</td>"
            f"<td>{fmt(r['toda_av'])}</td><td>{fmt(r['lda_av'])}</td>"
            f"<td>{fmt(r['hw_comp'])}</td>"
            f"<td class='{cw_cls}'>{r['xw_side']} {fmt(r['xw_abs'])}</td>"
            f"</tr>"
        )
    st.markdown(
        "<table class='mb-table'><tr>"
        "<th>Leg / ICAO</th><th>RWY</th><th>QFU</th><th>PA ft</th><th>DA ft</th>"
        "<th>TO GR (m)*</th><th>TODR 50ft (m)</th><th>LND GR (m)*</th><th>LDR 50ft (m)</th>"
        "<th>TODA</th><th>LDA</th><th>HW kt</th><th>XW</th></tr>" +
        "".join(rows_html) + "</table>",
        unsafe_allow_html=True
    )
    st.session_state["_perf_rows"] = perf_rows

# ---- 3) Weight & Balance ----
with tab_wb:
    st.markdown("### Weight & Balance")
    reg = st.session_state.get("reg", "CS-XXX")
    fleet = st.session_state.fleet
    ew_default = fleet.get(reg, {}).get("ew")
    ewm_default = fleet.get(reg, {}).get("ew_moment")

    c1, c2 = st.columns([0.55, 0.45])
    with c1:
        # EW & Moment are read-only here (managed in sidebar, persisted in Gist)
        ew = st.number_input("Empty Weight (kg)", min_value=0.0, value=(ew_default or 0.0), step=0.1, disabled=True)
        ew_moment = st.number_input("Empty Weight Moment (kg·m)", min_value=0.0, value=(ewm_default or 0.0), step=0.01, disabled=True)
        student = st.number_input("Student weight (kg)", min_value=0.0, value=0.0, step=0.5)
        instructor = st.number_input("Instructor weight (kg)", min_value=0.0, value=0.0, step=0.5)
        baggage = st.number_input("Baggage (kg)", min_value=0.0, value=0.0, step=0.5)
        fuel_l = st.number_input("Fuel (L)", min_value=0.0, value=0.0, step=0.5)

    with c2:
        pilot = student + instructor
        fuel_wt = fuel_l * AC["fuel_density"]
        m_empty = ew_moment or 0.0
        m_pilot = pilot * AC["pilot_arm"]
        m_bag = baggage * AC["baggage_arm"]
        m_fuel = fuel_wt * AC["fuel_arm"]
        total_weight = (ew or 0.0) + pilot + baggage + fuel_wt
        total_moment = m_empty + m_pilot + m_bag + m_fuel
        cg = (total_moment/total_weight) if total_weight > 0 else 0.0

        rem_by_mtow = max(0.0, AC["max_takeoff_weight"] - ((ew or 0.0) + pilot + baggage + fuel_wt))
        rem_by_tank = max(0.0, AC["max_fuel_volume"]*AC["fuel_density"] - fuel_wt)
        rem_fuel_wt = min(rem_by_mtow, rem_by_tank)
        rem_fuel_l = rem_fuel_wt / AC["fuel_density"]
        limit_label = "Tank Capacity" if rem_by_tank < rem_by_mtow else "Maximum Weight"

        def w_color(val, limit):
            if val > limit: return 'bad'
            if val > 0.95*limit: return 'warn'
            return 'ok'
        def cg_color_val(cg_val, limits):
            lo, hi = limits; margin = 0.05*(hi-lo)
            if cg_val < lo or cg_val > hi: return 'bad'
            if cg_val < lo+margin or cg_val > hi-margin: return 'warn'
            return 'ok'

        st.markdown("#### Summary")
        st.markdown(f"<div class='mb-summary'><div>Remaining possible fuel</div><div><b>{rem_fuel_l:.1f} L</b> ({limit_label})</div></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='mb-summary'><div>Total Weight</div><div class='{w_color(total_weight, AC['max_takeoff_weight'])}'><b>{total_weight:.1f} kg</b></div></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='mb-summary'><div>Total Moment</div><div><b>{total_moment:.2f} kg·m</b></div></div>", unsafe_allow_html=True)
        st.markdown(f"<div class='mb-summary'><div>CG</div><div class='{cg_color_val(cg, AC['cg_limits'])}'><b>{cg:.3f} m</b></div></div>", unsafe_allow_html=True)

        # Safety validations
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

    st.session_state["_wb"] = {"ew": ew, "ew_moment": ew_moment,
                               "total_weight": total_weight, "total_moment": total_moment,
                               "cg": cg, "fuel_l": fuel_l,
                               "pilot": pilot, "baggage": baggage}

# ---- 4) Performance & Fuel ----
with tab_perf:
    st.markdown("### Fuel Planning")
    policy = st.radio("Policy", options=["Simplified (fixed 90 L ramp)", "Detailed (rate-based)"], index=0, horizontal=True)

    if policy.startswith("Simplified"):
        total_ramp = 90.0
        st.info("Using simplified policy: **Total Ramp Fuel = 90 L**")
        trip_l = cont_l = req_ramp = extra_l = 0.0
    else:
        RATE_LPH = st.number_input("Planned consumption (L/h)", min_value=10.0, max_value=40.0, value=20.0, step=0.5)
        c1, c2, c3, c4 = st.columns(4)
        def time_to_liters(h=0, m=0, rate=RATE_LPH): return rate * (h + m/60.0)
        with c1:
            su_min = st.number_input("Start-up & Taxi (min)", min_value=0, value=15, step=1)
            climb_min = st.number_input("Climb (min)", min_value=0, value=15, step=1)
        with c2:
            enrt_h = st.number_input("Enroute (h)", min_value=0, value=2, step=1)
            enrt_min = st.number_input("Enroute (min)", min_value=0, value=15, step=1)
        with c3:
            desc_min = st.number_input("Descent (min)", min_value=0, value=15, step=1)
            alt_min = st.number_input("Alternate (min)", min_value=0, value=60, step=5)
        with c4:
            reserve_min = st.number_input("Reserve (min)", min_value=0, value=45, step=5)
            extra_min = st.number_input("Extra (min)", min_value=0, value=0, step=5)
        trip_l = time_to_liters(0, climb_min) + time_to_liters(enrt_h, enrt_min) + time_to_liters(0, desc_min)
        cont_l = 0.05 * trip_l
        req_ramp = time_to_liters(0, su_min) + trip_l + cont_l + time_to_liters(0, alt_min) + time_to_liters(0, reserve_min)
        extra_l = time_to_liters(0, extra_min)
        total_ramp = req_ramp + extra_l

        st.markdown(f"- **Trip Fuel**: {trip_l:.1f} L")
        st.markdown(f"- **Contingency 5%**: {cont_l:.1f} L")
        st.markdown(f"- **Required Ramp Fuel**: **{req_ramp:.1f} L**")
        st.markdown(f"- **Extra**: {extra_l:.1f} L")

    st.markdown(f"- **Total Ramp Fuel**: **{total_ramp:.1f} L**")
    st.session_state["_fuel"] = {
        "policy": policy, "trip_l": trip_l, "cont_l": cont_l, "req_ramp": req_ramp,
        "extra_l": extra_l, "total_ramp": total_ramp
    }

# ---- 5) PDF ----
with tab_pdf:
    st.markdown("### PDF – M&B and Performance Data Sheet (filled)")
    reg = st.session_state.get("reg", "CS-XXX")
    utc_today = dt.datetime.utcnow()
    date_str = st.text_input("Date (DD/MM/YYYY)", value=utc_today.strftime("%d/%m/%Y"))

    # Try template path(s)
    PDF_TEMPLATE = None
    for p in PDF_TEMPLATE_PATHS:
        if Path(p).exists():
            PDF_TEMPLATE = p
            break
    if not PDF_TEMPLATE:
        st.error("PDF template not found. Expected at /mnt/data/... or repo root.")
        st.stop()

    # --- PDF Field Mapping (adjust here if your field names differ) ---
    # Minimal known mapping (kept from your earlier template); you can use the inspector below to refine.
    FIELD_BASE = {
        "Registration": ["Textbox19", "Reg", "Registration"],
        "Date": ["Textbox18", "Date"],
        "TotalWeight": ["Textbox14", "TOTAL_W"],
        "CG": ["Textbox16", "CG"],
        "MTOW": ["Textbox17", "MTOW"],
    }
    # Per-leg (suffix keys "Dep", "Arr", "Alt")
    FIELD_LEG = {
        "ICAO":   ["Airfield_{leg}", "ICAO_{leg}", "Textbox22" if "{leg}"=="Dep" else ""],
        "PA":     ["PA_{leg}", "Textbox50"],
        "DA":     ["DA_{leg}", "Textbox49"],
        "TODA":   ["TODA_{leg}", "Textbox47"],   # some templates had TODA/LDA combined; we fill individually when possible
        "LDA":    ["LDA_{leg}", "Textbox47"],
        "TODR":   ["TODR_{leg}", "Textbox45"],
        "LDR":    ["LDR_{leg}", "Textbox41"],
        "QFU":    ["QFU_{leg}"],
        "WIND":   ["Wind_{leg}", "WIND_{leg}"],
        "ELEV":   ["Elev_{leg}", "Elevation_{leg}"],
        "TEMP":   ["Temp_{leg}"],
        "QNH":    ["QNH_{leg}"],
        "ROC":    ["ROC_{leg}"],
    }

    # Fill helper
    def pdfrw_set_field(fields, names, value, color_rgb=None):
        if isinstance(names, str): names = [names]
        for name in names:
            if not name: continue
            for f in fields:
                if f.get('/T') and f['/T'][1:-1] == name:
                    f.update(PdfDict(V=str(value)))
                    f.update(PdfDict(AP=None))
                    if color_rgb:
                        r, g, b = color_rgb
                        f.update(PdfDict(DA=f"{r/255:.3f} {g/255:.3f} {b/255:.3f} rg /Helv 10 Tf"))
                    return True
        return False

    # Optional inspector: list field names found in the PDF (for quick mapping)
    with st.expander("PDF fields inspector (optional)"):
        if st.checkbox("Show field names"):
            try:
                r = Rd_pdfrw(PDF_TEMPLATE)
                if hasattr(r, 'Root') and '/AcroForm' in r.Root:
                    fields = r.Root.AcroForm.Fields
                    names = []
                    for f in fields:
                        if f.get('/T'):
                            names.append(f['/T'][1:-1])
                    st.code("\n".join(names))
                else:
                    st.info("No AcroForm fields found with pdfrw.")
            except Exception as e:
                st.warning(f"Inspector error: {e}")

    # Compose leg strings + wind components
    perf_rows_local = st.session_state.get("_perf_rows", [])
    wb = st.session_state.get("_wb", {})
    fuel = st.session_state.get("_fuel", {})

    # Generate and fill PDF
    if st.button("Generate filled PDF"):
        # Create a short calc page (optional, appended)
        calc_pdf_path = f"_calc_{reg}.pdf"
        calc = FPDF(); calc.set_auto_page_break(auto=True, margin=12); calc.add_page()
        calc.set_font("Arial", "B", 14); calc.cell(0, 8, ascii_safe("Tecnam P2008 – Calculations (Summary)"), ln=True)
        calc.set_font("Arial", "B", 12); calc.cell(0, 7, ascii_safe("Weight & Balance"), ln=True)
        calc.set_font("Arial", size=10)
        calc.cell(0, 6, ascii_safe(f"Total Weight: {wb.get('total_weight',0):.1f} kg | "
                                    f"Total Moment: {wb.get('total_moment',0):.2f} kg·m | "
                                    f"CG: {wb.get('cg',0):.3f} m"), ln=True)
        calc.ln(2); calc.set_font("Arial", "B", 12); calc.cell(0, 7, ascii_safe("Aerodrome performance (auto-selected RWYs)"), ln=True)
        calc.set_font("Arial", size=10)

        # Build Wind strings for PDF
        def wind_pdf_str(wdir, wsk, hw, xw_abs, xw_side):
            sign_hw = "+" if hw >= 0 else "–"
            return f"{int(round(wdir))} / {int(round(wsk))} (HW {sign_hw}{int(round(abs(hw)))}, XW {xw_side} {int(round(xw_abs))})"

        for r in perf_rows_local:
            calc.set_font("Arial", "B", 10)
            calc.cell(0, 6, ascii_safe(f"{r['role']} – {r['icao']} RWY {r['rwy']} (QFU {r['qfu']:.0f}°)"), ln=True)
            calc.set_font("Arial", size=10)
            calc.cell(0, 5, ascii_safe(f"PA: {r['pa_ft']:.0f} ft | DA: {r['da_ft']:.0f} ft | "
                                       f"Wind: {wind_pdf_str(r['wind_dir'], r['wind_kt'], r['hw_comp'], r['xw_abs'], r['xw_side'])}"), ln=True)
            calc.cell(0, 5, ascii_safe(f"TO GR*: {r['to_gr']:.0f} m | TODR 50ft: {r['to_50']:.0f} m | "
                                       f"LND GR*: {r['ldg_gr']:.0f} m | LDR 50ft: {r['ldg_50']:.0f} m"), ln=True)
            calc.cell(0, 5, ascii_safe(f"TODA: {r['toda_av']:.0f} m | LDA: {r['lda_av']:.0f} m"), ln=True)
            dep_temp = r.get('temp', 15.0); roc_val = roc_interp(r['pa_ft'], dep_temp, wb.get('total_weight', AC['max_takeoff_weight']))
            calc.cell(0, 5, ascii_safe(f"ROC (est.): {roc_val:.0f} ft/min"), ln=True)
            calc.ln(1)

        # Fuel block
        calc.ln(2); calc.set_font("Arial", "B", 12); calc.cell(0, 7, ascii_safe("Fuel planning"), ln=True)
        calc.set_font("Arial", size=10)
        if fuel.get("policy","").startswith("Simplified"):
            calc.cell(0, 5, ascii_safe(f"Policy: Simplified 90 L | Total Ramp: {fuel.get('total_ramp',0):.1f} L"), ln=True)
        else:
            calc.cell(0, 5, ascii_safe(f"Trip: {fuel.get('trip_l',0):.1f} L | Cont 5%: {fuel.get('cont_l',0):.1f} L | "
                                        f"Required Ramp: {fuel.get('req_ramp',0):.1f} L | Extra: {fuel.get('extra_l',0):.1f} L | "
                                        f"Total Ramp: {fuel.get('total_ramp',0):.1f} L"), ln=True)
        calc.output(calc_pdf_path)

        # Fill the main PDF
        try:
            reader = Rd_pdfrw(PDF_TEMPLATE)
            if not (hasattr(reader, 'Root') and '/AcroForm' in reader.Root):
                raise RuntimeError("PDF has no AcroForm fields for pdfrw.")
            fields = reader.Root.AcroForm.Fields

            # Base fields
            # Registration
            pdfrw_set_field(fields, FIELD_BASE["Registration"], reg)
            # Date
            pdfrw_set_field(fields, FIELD_BASE["Date"], date_str)

            # Totals
            wt = wb.get('total_weight', 0.0)
            cg = wb.get('cg', 0.0)
            pdfrw_set_field(fields, FIELD_BASE["TotalWeight"], f"{wt:.1f}")
            pdfrw_set_field(fields, FIELD_BASE["CG"], f"{cg:.3f}")
            pdfrw_set_field(fields, FIELD_BASE["MTOW"], f"{AC['max_takeoff_weight']:.0f}")

            # Legs (Dep/Arr/Alt mapped to indices 0/1/2)
            def fill_leg(idx, leg_tag):
                r = perf_rows_local[idx]
                # Wind string
                wind_str = wind_pdf_str(r['wind_dir'], r['wind_kt'], r['hw_comp'], r['xw_abs'], r['xw_side'])
                # Try each candidate field name
                for key, candidates in FIELD_LEG.items():
                    names = [c.format(leg=leg_tag) if "{leg}" in c else c for c in candidates]
                    if key == "ICAO":  pdfrw_set_field(fields, names, r['icao'])
                    elif key == "QFU": pdfrw_set_field(fields, names, f"{r['qfu']:.0f}")
                    elif key == "ELEV":pdfrw_set_field(fields, names, f"{r['elev_ft']:.0f}")
                    elif key == "TEMP":pdfrw_set_field(fields, names, f"{r['temp']:.1f}")
                    elif key == "QNH": pdfrw_set_field(fields, names, f"{r['qnh']:.1f}")
                    elif key == "PA":  pdfrw_set_field(fields, names, f"{r['pa_ft']:.0f}")
                    elif key == "DA":  pdfrw_set_field(fields, names, f"{r['da_ft']:.0f}")
                    elif key == "TODA":pdfrw_set_field(fields, names, f"{r['toda_av']:.0f}")
                    elif key == "LDA": pdfrw_set_field(fields, names, f"{r['lda_av']:.0f}")
                    elif key == "TODR":pdfrw_set_field(fields, names, f"{r['to_50']:.0f}")
                    elif key == "LDR": pdfrw_set_field(fields, names, f"{r['ldg_50']:.0f}")
                    elif key == "ROC":
                        roc_val = roc_interp(r['pa_ft'], r.get('temp',15.0), wt if wt>0 else AC['max_takeoff_weight'])
                        pdfrw_set_field(fields, names, f"{roc_val:.0f}")
                    elif key == "WIND": pdfrw_set_field(fields, names, wind_str)

            if len(perf_rows_local) >= 3:
                fill_leg(0, "Dep"); fill_leg(1, "Arr"); fill_leg(2, "Alt")

            # Write base
            writer = Wr_pdfrw(); writer.write("MB_Performance_filled.pdf", reader)

            # Append calc page
            base = Rd_pypdf("MB_Performance_filled.pdf"); calc_doc = Rd_pypdf(calc_pdf_path)
            merger = Wr_pypdf()
            for p in base.pages: merger.add_page(p)
            for p in calc_doc.pages: merger.add_page(p)
            with open("MB_Performance_filled.pdf", "wb") as f:
                merger.write(f)

            st.success("PDF generated & filled!")
            with open("MB_Performance_filled.pdf", "rb") as f:
                st.download_button("Download PDF", f, file_name=f"MB_Performance_{reg}.pdf", mime="application/pdf")

        except Exception as e:
            st.error(f"PDF fill error: {e}")
            st.info("Tip: open the PDF fields inspector above to grab the exact field names and update the FIELD_BASE / FIELD_LEG mapping.")




