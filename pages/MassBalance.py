# Streamlit app – Tecnam P2008 (M&B + Performance)
# Layout: modern tabs + Windy hour picker + auto best runway
# Calculations + PDF: EXACTLY like the original script you provided
#
# Requirements:
#   streamlit
#   requests
#   pytz
#   pdfrw==0.4
#   pypdf>=4.2.0
#   fpdf
#
# Secrets:
#   WINDY_API_KEY

import streamlit as st
import datetime as dt
import pytz
from pathlib import Path
import unicodedata
from math import cos, sin, radians, sqrt, atan2, degrees
import json
import requests

# PDF form filling / merging
from pdfrw import PdfReader as Rd_pdfrw, PdfWriter as Wr_pdfrw, PdfDict
from pypdf import PdfReader as Rd_pypdf, PdfWriter as Wr_pypdf
from fpdf import FPDF

# -----------------------------
# App setup & styles
# -----------------------------
st.set_page_config(
    page_title="Tecnam P2008 – Mass & Balance & Performance",
    layout="wide",
    initial_sidebar_state="collapsed",   # keep sidebar closed by default
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1180px !important; }
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

def ascii_safe(text):
    if not isinstance(text, str):
        return str(text)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

# -----------------------------
# Fixed Aircraft Data (Tecnam P2008)
# -----------------------------
AC = {
    "name": "Tecnam P2008 JC",
    "fuel_arm": 2.209,
    "pilot_arm": 1.800,
    "baggage_arm": 2.417,
    "max_takeoff_weight": 650.0,
    "max_fuel_volume": 124.0,      # liters
    "max_passenger_weight": 230.0, # student + instructor total
    "max_baggage_weight": 20.0,
    "cg_limits": (1.841, 1.978),   # m
    "fuel_density": 0.72,          # kg/L
    "units": {"weight": "kg", "arm": "m"},
}

# -----------------------------
# Aerodrome DB (Portugal) with both RWY ends (QFU/TODA/LDA)
# You can extend this dict with more aerodromes as needed.
# -----------------------------
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
    # Extras (examples):
    "LPCS": {
        "name": "Cascais",
        "lat": 38.725556, "lon": -9.355278, "elev_ft": 326.0,
        "runways": [
            {"id": "17", "qfu": 170.0, "toda": 1700.0, "lda": 1700.0, "slope_pc": 0.0, "paved": True},
            {"id": "35", "qfu": 350.0, "toda": 1700.0, "lda": 1700.0, "slope_pc": 0.0, "paved": True},
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
}

# -----------------------------
# Performance Tables (AFM extracts) – same as your original
# Distances in meters; ROC in ft/min
# -----------------------------
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

# VY/ROC kept for the calc page (same as your original)
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

# -----------------------------
# Helpers & original interpolation/corrections
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

def wind_head_component(runway_qfu_deg, wind_dir_deg, wind_speed):
    if runway_qfu_deg is None or wind_dir_deg is None: return 0.0
    diff = radians((wind_dir_deg - runway_qfu_deg) % 360)
    return wind_speed * cos(diff)

def wind_components(qfu_deg, wind_dir_deg, wind_speed):
    diff = radians((wind_dir_deg - qfu_deg) % 360)
    hw = wind_speed * cos(diff)
    cw = wind_speed * sin(diff)
    side = "R" if cw > 0 else ("L" if cw < 0 else "")
    return hw, abs(cw), side

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
# Windy API (hourly) — ICON-EU default
# -----------------------------
WINDY_ENDPOINT = "https://api.windy.com/api/point-forecast/v2"

@st.cache_data(ttl=900, show_spinner=False)
def windy_point_forecast(lat, lon, model, api_key):
    headers = {"Content-Type": "application/json"}
    body = {
        "lat": round(float(lat), 3),
        "lon": round(float(lon), 3),
        "model": model,                  # "iconEu" default
        "parameters": ["wind","temp","pressure","windGust"],
        "levels": ["surface"],
        "key": api_key,
    }
    r = requests.post(WINDY_ENDPOINT, headers=headers, data=json.dumps(body), timeout=20)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "detail": r.text}
    return r.json()

def windy_hours(resp):
    if not resp or "ts" not in resp: return []
    out = []
    for i, tms in enumerate(resp["ts"]):
        t = dt.datetime.utcfromtimestamp(tms/1000).replace(tzinfo=dt.timezone.utc)
        out.append((i, t.strftime("%Y-%m-%d %H:00Z")))
    return out

def windy_unpack_at(resp, idx):
    def getv(key):
        arr = resp.get(key, [])
        return arr[idx] if arr and idx < len(arr) else None
    u = getv("wind_u-surface"); v = getv("wind_v-surface")
    if u is None or v is None: return None
    speed_ms = sqrt(u*u + v*v)
    dir_deg = (degrees(atan2(-u, -v)) + 360.0) % 360.0
    speed_kt = speed_ms * 1.94384
    temp_val = getv("temp-surface")
    temp_c = None
    if temp_val is not None:
        temp_c = float(temp_val)
        if temp_c > 100: temp_c -= 273.15  # Kelvin -> C
        temp_c = round(temp_c, 1)
    pres_pa = getv("pressure-surface")
    qnh_hpa = round(pres_pa/100.0, 1) if pres_pa is not None else None
    gust = getv("gust-surface")
    return {
        "wind_dir": round((dir_deg or 0)),
        "wind_kt": round((speed_kt or 0)),
        "temp": temp_c if temp_c is not None else 15.0,
        "qnh": qnh_hpa if qnh_hpa is not None else 1013.0,
        "gust_kt": round(gust*1.94384) if gust is not None else None,
    }

# -----------------------------
# UI Header
# -----------------------------
st.markdown('<div class="mb-header">Tecnam P2008 – Mass & Balance & Performance</div>', unsafe_allow_html=True)

# -----------------------------
# Tabs
# -----------------------------
tab_setup, tab_aero, tab_wb, tab_perf, tab_pdf = st.tabs([
    "1) Flight & Model", "2) Aerodromes & MET", "3) Weight & Balance",
    "4) Performance & Fuel", "5) PDF"
])

# ---- 1) Flight & Model ----
with tab_setup:
    c1, c2 = st.columns([0.55, 0.45])
    with c1:
        st.markdown("### Flight time (UTC)")
        today = dt.datetime.utcnow().date()
        default_time = (dt.datetime.utcnow() + dt.timedelta(hours=1)).time().replace(second=0, microsecond=0)
        flight_date = st.date_input("Date (UTC)", value=today)
        flight_time = st.time_input("Hour (UTC, hourly forecast)", value=default_time, step=3600)
        FORECAST_DT_UTC = dt.datetime.combine(flight_date, flight_time).replace(tzinfo=dt.timezone.utc)
        st.info(f"Forecast hour: {FORECAST_DT_UTC.strftime('%Y-%m-%d %H:%MZ')}")

    with c2:
        st.markdown("### Forecast model")
        model = st.selectbox("Windy model", ["iconEu", "gfs", "arome"], index=0)
        st.session_state["_windy_model"] = model

# ---- helpers: original PA/DA + best runway ----
def pa_da(elev_ft, qnh_hpa, temp_c):
    pa_ft = elev_ft + (1013.25 - qnh_hpa) * 27.0
    isa_temp = 15.0 - 2.0 * (pa_ft/1000.0)
    da_ft = pa_ft + (120.0 * (temp_c - isa_temp))
    return pa_ft, da_ft

def choose_best_runway(ad, temp_c, qnh, wind_dir, wind_kt):
    pa_ft, da_ft = pa_da(ad["elev_ft"], qnh, temp_c)
    candidates = []
    for rw in ad["runways"]:
        qfu = rw["qfu"]; paved = rw["paved"]; slope_pc = rw["slope_pc"]
        hw, xw_abs, side = wind_components(qfu, wind_dir, wind_kt)

        # ORIGINAL interpolation (no headwind applied to TODR/LDR)
        to_gr = bilinear(pa_ft, temp_c, TAKEOFF, 'GR')
        to_50 = bilinear(pa_ft, temp_c, TAKEOFF, '50ft')
        ldg_gr = bilinear(pa_ft, temp_c, LANDING, 'GR')
        ldg_50 = bilinear(pa_ft, temp_c, LANDING, '50ft')

        # ORIGINAL corrections only to GR (for display) — not to TODR/LDR
        to_gr_corr  = to_corrections_takeoff(to_gr,  hw, paved=paved, slope_pc=slope_pc)
        ldg_gr_corr = ldg_corrections(ldg_gr, hw, paved=paved, slope_pc=slope_pc)

        feasible = (to_50 <= rw["toda"]) and (ldg_50 <= rw["lda"])
        candidates.append({
            "id": rw["id"], "qfu": qfu, "toda": rw["toda"], "lda": rw["lda"], "paved": paved, "slope_pc": slope_pc,
            "hw": hw, "xw": xw_abs, "xw_side": side,
            "to_gr": to_gr_corr, "to_50": to_50, "ldg_gr": ldg_gr_corr, "ldg_50": ldg_50,
            "feasible": feasible, "pa_ft": pa_ft, "da_ft": da_ft
        })
    feas = [c for c in candidates if c["feasible"]]
    pool = feas if feas else candidates
    best = sorted(pool, key=lambda c: (c["feasible"], c["hw"], -c["xw"]), reverse=True)[0]
    return best, candidates

# ---- 2) Aerodromes & MET ----
with tab_aero:
    st.markdown("### Aerodromes (Departure, Arrival, Alternate) + MET (Windy hourly)")
    AERO_SEQUENCE = ["Departure", "Arrival", "Alternate"]

    perf_rows = []
    for i, role in enumerate(AERO_SEQUENCE):
        c1, c2, c3 = st.columns([0.34, 0.33, 0.33])

        with c1:
            icao_options = sorted(AERODROMES_DB.keys())
            icao_default = ["LPSO","LPEV","LPCB"][i] if i < 3 else icao_options[0]
            icao = st.selectbox(f"{role} – Aerodrome", options=icao_options,
                                index=icao_options.index(icao_default), key=f"icao_{i}")
            ad = AERODROMES_DB[icao]
            st.caption(f"{ad['name']}  •  Lat {ad['lat']:.4f}, Lon {ad['lon']:.4f}  •  Elev {ad['elev_ft']:.0f} ft")

        with c2:
            # Local MET inputs (you can overwrite the forecast)
            temp_key = f"temp_{i}"; qnh_key = f"qnh_{i}"; wdir_key = f"wdir_{i}"; wspd_key = f"wspd_{i}"
            if temp_key not in st.session_state: st.session_state[temp_key] = 15.0
            if qnh_key  not in st.session_state: st.session_state[qnh_key]  = 1013.0
            if wdir_key not in st.session_state: st.session_state[wdir_key] = 0.0
            if wspd_key not in st.session_state: st.session_state[wspd_key] = 0.0

            temp = st.number_input("OAT (°C)", value=float(st.session_state[temp_key]), step=0.1, key=temp_key)
            qnh  = st.number_input("QNH (hPa)", min_value=900.0, max_value=1050.0, value=float(st.session_state[qnh_key]), step=0.1, key=qnh_key)
            wdir = st.number_input("Wind FROM (°)", min_value=0.0, max_value=360.0, value=float(st.session_state[wdir_key]), step=1.0, key=wdir_key)
            wspd = st.number_input("Wind speed (kt)", min_value=0.0, value=float(st.session_state[wspd_key]), step=1.0, key=wspd_key)

        with c3:
            api_key = st.secrets.get("WINDY_API_KEY", "")
            if st.button("Fetch Windy hours", key=f"fetch_{i}"):
                if not api_key:
                    st.error("Add WINDY_API_KEY to secrets.")
                else:
                    resp = windy_point_forecast(ad["lat"], ad["lon"], st.session_state.get("_windy_model","iconEu"), api_key)
                    if "error" in resp:
                        st.error(f"Windy error: {resp.get('error')} {resp.get('detail','')}")
                    else:
                        st.session_state[f"windy_resp_{i}"] = resp
                        st.session_state[f"windy_hours_{i}"] = windy_hours(resp)
                        st.session_state[f"sel_idx_{i}"] = 0
                        st.success(f"Loaded {len(st.session_state[f'windy_hours_{i}'])} hours.")

            hours = st.session_state.get(f"windy_hours_{i}", [])
            if hours:
                labels = [h[1] for h in hours]
                cur_idx = st.session_state.get(f"sel_idx_{i}", 0)
                sel_label = st.selectbox("Forecast hour (UTC)", options=labels,
                                         index=min(cur_idx, len(labels)-1), key=f"label_{i}")
                # store desired index EARLY (before widgets rebuild next run)
                st.session_state[f"sel_idx_{i}"] = labels.index(sel_label)

                if st.button("Apply forecast to fields", key=f"apply_{i}"):
                    resp = st.session_state.get(f"windy_resp_{i}")
                    idx = st.session_state.get(f"sel_idx_{i}", 0)
                    met = windy_unpack_at(resp, hours[idx][0]) if resp else None
                    # set widget-bound keys BEFORE rerun (via flags at session_state top)
                    if met:
                        st.session_state[f"_pending_apply_{i}"] = met
                        st.success(f"Applied {sel_label}.")
                        st.experimental_rerun()

            # If there is pending apply, update the widget keys BEFORE compute
            pend = st.session_state.get(f"_pending_apply_{i}")
            if pend:
                st.session_state[temp_key] = float(pend["temp"])
                st.session_state[qnh_key]  = float(pend["qnh"])
                st.session_state[wdir_key] = float(pend["wind_dir"])
                st.session_state[wspd_key] = float(pend["wind_kt"])
                st.session_state[f"_pending_apply_{i}"] = None

        # AUTO-SELECT BEST RWY using ORIGINAL numbers
        best, _cands = choose_best_runway(
            ad, float(st.session_state[temp_key]), float(st.session_state[qnh_key]),
            float(st.session_state[wdir_key]), float(st.session_state[wspd_key])
        )

        # Display quick summary (not written to PDF — PDF sticks to original fields only)
        st.markdown(
            f"🧭 **Selected runway:** {best['id']} "
            f"<span class='chip'>QFU {best['qfu']:.0f}°</span>"
            f"<span class='chip'>TODA {best['toda']:.0f} m</span>"
            f"<span class='chip'>LDA {best['lda']:.0f} m</span>"
            f"<span class='chip'>HW {best['hw']:.0f} kt</span>",
            unsafe_allow_html=True
        )

        perf_rows.append({
            'role': role, 'icao': icao, 'qfu': best['qfu'],
            'pa_ft': best['pa_ft'], 'da_ft': best['da_ft'],
            'to_gr': best['to_gr'], 'to_50': best['to_50'],
            'ldg_gr': best['ldg_gr'], 'ldg_50': best['ldg_50'],
            'toda_av': best['toda'], 'lda_av': best['lda'],
            'hw_comp': best['hw'],
        })

    # Summary (like your table)
    def fmt(v): return f"{v:.0f}" if isinstance(v, (int,float)) else str(v)
    st.markdown("#### Performance summary")
    st.markdown(
        "<table class='mb-table'><tr><th>Leg/Aerodrome</th><th>QFU</th><th>PA ft</th><th>DA ft</th>"
        "<th>TO GR (m)*</th><th>TODR 50ft (m)</th><th>LND GR (m)*</th><th>LDR 50ft (m)</th>"
        "<th>TODA Av</th><th>LDA Av</th></tr>" +
        "".join([
            f"<tr><td>{r['role']} {r['icao']}</td><td>{fmt(r['qfu'])}</td><td>{fmt(r['pa_ft'])}</td><td>{fmt(r['da_ft'])}</td>"
            f"<td>{fmt(r['to_gr'])}</td><td>{fmt(r['to_50'])}</td><td>{fmt(r['ldg_gr'])}</td><td>{fmt(r['ldg_50'])}</td>"
            f"<td>{fmt(r['toda_av'])}</td><td>{fmt(r['lda_av'])}</td></tr>"
            for r in perf_rows
        ]) + "</table>",
        unsafe_allow_html=True
    )

# ---- 3) Weight & Balance (same fields/logic as original)
with tab_wb:
    st.markdown("### Weight & Balance")
    c1, c2 = st.columns([0.55, 0.45])
    with c1:
        ew = st.number_input("Empty Weight (kg)", min_value=0.0, value=0.0, step=1.0)
        ew_moment = st.number_input("Empty Weight Moment (kg·m)", min_value=0.0, value=0.0, step=0.1)
        student = st.number_input("Student Weight (kg)", min_value=0.0, value=0.0, step=1.0)
        instructor = st.number_input("Instructor Weight (kg)", min_value=0.0, value=0.0, step=1.0)
        baggage = st.number_input("Baggage (kg)", min_value=0.0, value=0.0, step=1.0)
        fuel_l = st.number_input("Fuel (L)", min_value=0.0, value=0.0, step=1.0)
    with c2:
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

        # Limits warnings (like your original)
        if pilot > AC["max_passenger_weight"]:
            st.error(f"Passengers over limit: {pilot:.1f} kg > {AC['max_passenger_weight']:.0f} kg")
        if baggage > AC["max_baggage_weight"]:
            st.error(f"Baggage over limit: {baggage:.1f} kg > {AC['max_baggage_weight']:.0f} kg")
        if fuel_l > AC["max_fuel_volume"]:
            st.error(f"Fuel over tank capacity: {fuel_l:.1f} L > {AC['max_fuel_volume']:.0f} L")
        lo, hi = AC["cg_limits"]
        if total_weight > 0 and (cg < lo or cg > hi):
            st.error(f"CG out of limits: {cg:.3f} m not in [{lo:.3f}, {hi:.3f}] m")
        if total_weight > AC["max_takeoff_weight"]:
            st.error(f"MTOW exceeded: {total_weight:.1f} kg > {AC['max_takeoff_weight']:.0f} kg")

# ---- 4) Fuel Planning (exactly like your original: 20 L/h default)
with tab_perf:
    st.markdown("### Fuel Planning (assume 20 L/h by default)")
    RATE_LPH = 20.0
    c1, c2, c3, c4 = st.columns(4)

    def time_to_liters(h=0, m=0, rate=RATE_LPH):
        return rate * (h + m/60.0)

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
    cont_l = 0.05*trip_l
    req_ramp = time_to_liters(0, su_min) + trip_l + cont_l + time_to_liters(0, alt_min) + time_to_liters(0, reserve_min)
    extra_l = time_to_liters(0, extra_min)
    total_ramp = req_ramp + extra_l

    st.markdown(f"- **Trip Fuel**: {trip_l:.1f} L")
    st.markdown(f"- **Contingency 5%**: {cont_l:.1f} L")
    st.markdown(f"- **Required Ramp Fuel** (1+5+6+7+8): **{req_ramp:.1f} L**")
    st.markdown(f"- **Extra**: {extra_l:.1f} L")
    st.markdown(f"- **Total Ramp Fuel**: **{total_ramp:.1f} L**")

# ---- 5) PDF – fill exactly like your original (+ same calc page)
with tab_pdf:
    st.markdown("### PDF – M&B and Performance Data Sheet")
    APP_DIR = Path(__file__).parent
    PDF_TEMPLATE = APP_DIR / "TecnamP2008MBPerformanceSheet_MissionX.pdf"

    reg = st.text_input("Aircraft Registration", value="CS-XXX")
    mission = st.text_input("Mission #", value="001")
    utc_today = dt.datetime.now(pytz.UTC)
    date_str = st.text_input("Date (DD/MM/YYYY)", value=utc_today.strftime("%d/%m/%Y"))

    # Original field map (min)
    FIELD_MAP = {
        "Textbox19": reg,       # Registration
        "Textbox18": date_str,  # Date
    }

    # Button
    if st.button("Generate filled PDF"):
        if not PDF_TEMPLATE.exists():
            st.error(f"Template not found: {PDF_TEMPLATE}")
            st.stop()

        # Prepare the calc page (like original)
        calc_pdf_path = APP_DIR / f"_calc_{reg}_{mission}.pdf"
        calc = FPDF()
        calc.set_auto_page_break(auto=True, margin=12)
        calc.add_page()
        calc.set_font("Arial", "B", 14)
        calc.cell(0, 8, ascii_safe("Tecnam P2008 – Calculations (Summary)"), ln=True)

        # W&B block
        calc.set_font("Arial", "B", 12); calc.cell(0, 7, ascii_safe("Weight & Balance"), ln=True)
        calc.set_font("Arial", size=10)
        calc.cell(0, 6, ascii_safe(f"EW: {ew:.1f} kg | EW Moment: {ew_moment:.2f} kg·m | Pilot: {student+instructor:.1f} kg | Baggage: {baggage:.1f} kg | Fuel: {fuel_l:.1f} L"), ln=True)
        # Recompute totals for safety (same as tab)
        fuel_wt = fuel_l * AC['fuel_density']
        m_empty = ew_moment
        m_pilot = (student+instructor) * AC['pilot_arm']
        m_bag = baggage * AC['baggage_arm']
        m_fuel = fuel_wt * AC['fuel_arm']
        total_weight = ew + (student+instructor) + baggage + fuel_wt
        total_moment = m_empty + m_pilot + m_bag + m_fuel
        cg = (total_moment/total_weight) if total_weight>0 else 0.0
        calc.cell(0, 6, ascii_safe(f"Total Weight: {total_weight:.1f} kg | Total Moment: {total_moment:.2f} kg·m | CG: {cg:.3f} m"), ln=True)

        # Performance per aerodrome (original figures)
        calc.ln(2)
        calc.set_font("Arial", "B", 12); calc.cell(0, 7, ascii_safe("Performance per Aerodrome"), ln=True)
        calc.set_font("Arial", size=10)
        for r in [x for x in locals().get('perf_rows', [])]:
            calc.set_font("Arial", "B", 10)
            calc.cell(0, 6, ascii_safe(f"{r['role']} – {r['icao']} (QFU {r['qfu']:.0f}°)"), ln=True)
            calc.set_font("Arial", size=10)
            calc.cell(0, 5, ascii_safe(f"PA: {r['pa_ft']:.0f} ft | DA: {r['da_ft']:.0f} ft | HW Comp: {r['hw_comp']:.0f} kt"), ln=True)
            calc.cell(0, 5, ascii_safe(f"TO GR*: {r['to_gr']:.0f} m | TODR 50ft: {r['to_50']:.0f} m | LND GR*: {r['ldg_gr']:.0f} m | LDR 50ft: {r['ldg_50']:.0f} m"), ln=True)
            calc.cell(0, 5, ascii_safe(f"TODA Avail: {r['toda_av']:.0f} m | LDA Avail: {r['lda_av']:.0f} m"), ln=True)
            calc.ln(1)

        # Fuel block (original)
        calc.ln(2)
        calc.set_font("Arial", "B", 12); calc.cell(0, 7, ascii_safe("Fuel Planning (20 L/h)"), ln=True)
        calc.set_font("Arial", size=10)
        calc.cell(0, 5, ascii_safe(f"Trip: {trip_l:.1f} L | Cont 5%: {cont_l:.1f} L | Required Ramp: {req_ramp:.1f} L | Extra: {extra_l:.1f} L | Total Ramp: {total_ramp:.1f} L"), ln=True)
        calc.output(str(calc_pdf_path))

        # Fill PDF exactly like original
        def load_pdf_any(path: Path):
            try:
                return "pdfrw", Rd_pdfrw(str(path))
            except Exception:
                try:
                    return "pypdf", Rd_pypdf(str(path))
                except Exception as e:
                    raise RuntimeError(f"Could not read PDF: {e}")

        def pdfrw_set_field(fields, name, value, color_rgb=None):
            for f in fields:
                if f.get('/T') and f['/T'][1:-1] == name:
                    f.update(PdfDict(V=str(value)))
                    f.update(PdfDict(AP=None))
                    if color_rgb:
                        r, g, b = color_rgb
                        f.update(PdfDict(DA=f"{r/255:.3f} {g/255:.3f} {b/255:.3f} rg /Helv 10 Tf"))
                    break

        engine, reader = load_pdf_any(PDF_TEMPLATE)
        out_main_path = APP_DIR / f"MB_Performance_{reg}_{mission}.pdf"

        if engine == "pdfrw" and hasattr(reader, 'Root') and '/AcroForm' in reader.Root:
            fields = reader.Root.AcroForm.Fields
            for k, v in FIELD_MAP.items():
                pdfrw_set_field(fields, k, v)

            # Weight/CG colors (original style)
            wt_color = (30,150,30) if total_weight <= AC['max_takeoff_weight'] else (200,0,0)
            lo, hi = AC['cg_limits']
            if cg < lo or cg > hi: cg_color = (200,0,0)
            else:
                margin = 0.05*(hi-lo)
                cg_color = (200,150,30) if (cg<lo+margin or cg>hi-margin) else (30,150,30)
            pdfrw_set_field(fields, "Textbox14", f"{total_weight:.1f}", wt_color)
            pdfrw_set_field(fields, "Textbox16", f"{cg:.3f}", cg_color)
            pdfrw_set_field(fields, "Textbox17", f"{AC['max_takeoff_weight']:.0f}")

            # Page 2 (Departure leg only — same as your original example)
            if perf_rows:
                dep = perf_rows[0]
                pdfrw_set_field(fields, "Textbox22", dep['icao'])
                pdfrw_set_field(fields, "Textbox50", f"{dep['pa_ft']:.0f}")
                pdfrw_set_field(fields, "Textbox49", f"{dep['da_ft']:.0f}")
                pdfrw_set_field(fields, "Textbox47", f"{int(dep['toda_av'])}/{int(dep['lda_av'])}")
                pdfrw_set_field(fields, "Textbox45", f"{dep['to_50']:.0f}")
                pdfrw_set_field(fields, "Textbox41", f"{dep['ldg_50']:.0f}")

            writer = Wr_pdfrw()
            writer.write(str(out_main_path), reader)

            # Merge calc page (same as original)
            base = Rd_pypdf(str(out_main_path))
            calc_doc = Rd_pypdf(str(calc_pdf_path))
            merger = Wr_pypdf()
            for p in base.pages: merger.add_page(p)
            for p in calc_doc.pages: merger.add_page(p)
            with open(out_main_path, "wb") as f: merger.write(f)

        else:
            # Fallback: pypdf basic fill (like your original fallback) + append calc
            base_r = Rd_pypdf(str(PDF_TEMPLATE))
            merger = Wr_pypdf()
            for p in base_r.pages: merger.add_page(p)
            if "/AcroForm" in base_r.trailer["/Root"]:
                merger._root_object.update({"/AcroForm": base_r.trailer["/Root"]["/AcroForm"]})
                merger._root_object["/AcroForm"].update({"/NeedAppearances": True})
            merger.update_page_form_field_values(base_r.pages[0], FIELD_MAP)
            calc_doc = Rd_pypdf(str(calc_pdf_path))
            for p in calc_doc.pages: merger.add_page(p)
            with open(out_main_path, "wb") as f: merger.write(f)

        st.success("PDF generated successfully!")
        with open(out_main_path, 'rb') as f:
            st.download_button("Download PDF", f, file_name=out_main_path.name, mime="application/pdf")



