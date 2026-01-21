# Streamlit app – Piper PA-28 (Sevenair) – M&B + MET + Fuel + PDF
# - Loads fleet (EW + EW moment) from GitHub Gist file: sevenair_pa28_fleet.json
# - UI in kg / L, PDF in lb / USG with metric in parentheses
# - Open-Meteo forecast: uses vector-mean wind around target hour (±1h) and rounds wind dir to nearest 10°
# - Fills the provided PDF and draws CG chart (Empty/Takeoff/Landing) on page 0

import io
import json
import datetime as dt
from math import sin, cos, radians, sqrt, atan2, degrees

import streamlit as st
import requests
import pytz

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas

# -----------------------------
# App setup
# -----------------------------
st.set_page_config(page_title="PA-28 – M&B + Performance Sheet", layout="wide", initial_sidebar_state="collapsed")
st.markdown(
    """
    <style>
      .block-container { max-width: 1250px !important; }
      .hdr{font-size:1.25rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:8px;margin:2px 0 14px}
      .hint{font-size:.85rem;color:#6b7280}
      .pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef2f7;margin-left:8px;font-size:.85rem}
      .ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
      .box{border:1px solid #e5e7ec;border-radius:12px;padding:12px}
      .tight td{padding:2px 6px}
    </style>
    """,
    unsafe_allow_html=True,
)
st.markdown('<div class="hdr">Piper PA-28 – Mass & Balance + Weather + Fuel + PDF</div>', unsafe_allow_html=True)

# -----------------------------
# Constants
# -----------------------------
PDF_TEMPLATE = "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"

# Fixed arms (inches)
ARM_FRONT = 80.5
ARM_REAR = 118.1
ARM_FUEL = 95.0
ARM_BAG = 142.8

# Taxi allowance fixed on the sheet (row 8): -8 lb at 95.5 in
TAXI_ALLOW_LB = 8.0
TAXI_ALLOW_ARM = 95.5

# Limits (Normal)
MTOW_LB = 2550.0
MLW_LB = 2440.0
MAX_FUEL_USG = 48.0

# Fuel density (Avgas) – consistent with your Tecnam usage
FUEL_DENS_KG_PER_L = 0.72

KG_TO_LB = 2.2046226218
LB_TO_KG = 1.0 / KG_TO_LB
L_TO_USG = 0.2641720524
USG_TO_L = 1.0 / L_TO_USG

# -----------------------------
# Minimal aerodrome DB (expand later if you want)
# -----------------------------
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
    "LPPT": {"name": "Lisboa", "lat": 38.7742, "lon": -9.1342, "elev_ft": 374.0,
             "runways": [{"id": "02", "qfu": 20.0, "toda": 3805.0, "lda": 3805.0},
                         {"id": "20", "qfu": 200.0, "toda": 3805.0, "lda": 3805.0}]},
    "LPMT": {"name": "Montijo", "lat": 38.7039, "lon": -9.0350, "elev_ft": 46.0,
             "runways": [{"id": "07", "qfu": 70.0, "toda": 2448.0, "lda": 2448.0},
                         {"id": "25", "qfu": 250.0, "toda": 2448.0, "lda": 2448.0},
                         {"id": "01", "qfu": 10.0, "toda": 2187.0, "lda": 2187.0},
                         {"id": "19", "qfu": 190.0, "toda": 2187.0, "lda": 2187.0}]},
}

ICAO_OPTIONS = sorted(AERODROMES_DB.keys())

# -----------------------------
# Open-Meteo forecast (hourly) – vector mean (±1h)
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

    times = h.get("time", []) or []
    wspd_kn = h.get("wind_speed_10m", []) or []
    wdir = h.get("wind_direction_10m", []) or []
    temp_c = h.get("temperature_2m", []) or []
    qnh_hpa = h.get("pressure_msl", []) or []

    rows = []
    for i, t in enumerate(times):
        dt_utc = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        spd = float(wspd_kn[i]) if i < len(wspd_kn) and wspd_kn[i] is not None else 0.0
        ddeg = float(wdir[i]) if i < len(wdir) and wdir[i] is not None else 0.0  # FROM
        # convert to u/v in m/s (meteorological, vector-mean friendly)
        spd_ms = spd * 0.514444
        theta = radians(ddeg)
        u = -spd_ms * sin(theta)
        v = -spd_ms * cos(theta)
        temp = float(temp_c[i]) if i < len(temp_c) and temp_c[i] is not None else None
        qnh = float(qnh_hpa[i]) if i < len(qnh_hpa) and qnh_hpa[i] is not None else None
        rows.append({"t": dt_utc, "u": u, "v": v, "temp": temp, "qnh": qnh})
    return {"rows": rows}

def round_dir10(deg_0_360: float) -> int:
    d = int(round(deg_0_360 / 10.0) * 10) % 360
    return 360 if d == 0 else d

def wind_from_uv(u_ms: float, v_ms: float):
    spd_ms = sqrt(u_ms*u_ms + v_ms*v_ms)
    spd_kt = spd_ms * 1.94384
    d_from = (degrees(atan2(u_ms, v_ms)) + 180.0) % 360.0
    return d_from, spd_kt

def met_at(ad, target_dt_utc: dt.datetime):
    start_iso = target_dt_utc.date().strftime("%Y-%m-%d")
    end_iso = start_iso
    resp = om_point_forecast(ad["lat"], ad["lon"], start_iso, end_iso)
    if "error" in resp:
        return None, f"{resp.get('error')} {resp.get('detail','')}"
    rows = resp.get("rows", [])
    if not rows:
        return None, "No forecast rows"

    # nearest hour
    idx = min(range(len(rows)), key=lambda i: abs(rows[i]["t"] - target_dt_utc))
    # vector mean ±1 hour
    idxs = [j for j in (idx-1, idx, idx+1) if 0 <= j < len(rows)]
    u = sum(rows[j]["u"] for j in idxs) / len(idxs)
    v = sum(rows[j]["v"] for j in idxs) / len(idxs)

    temps = [rows[j]["temp"] for j in idxs if rows[j]["temp"] is not None]
    qnhs = [rows[j]["qnh"] for j in idxs if rows[j]["qnh"] is not None]
    temp = sum(temps)/len(temps) if temps else None
    qnh = sum(qnhs)/len(qnhs) if qnhs else None

    d_from, spd_kt = wind_from_uv(u, v)
    d10 = round_dir10(d_from)
    spd = int(round(spd_kt))
    return {
        "temp_c": int(round(temp)) if temp is not None else 15,
        "qnh_hpa": int(round(qnh)) if qnh is not None else 1013,
        "wind_dir": int(d10),
        "wind_kt": int(spd),
        "label": rows[idx]["t"].strftime("%Y-%m-%d %H:00Z"),
    }, None

# -----------------------------
# Gist load (fleet)
# -----------------------------
GIST_FILE = "sevenair_pa28_fleet.json"

def gist_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gist_load_fleet(token, gist_id):
    r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(token), timeout=15)
    if r.status_code != 200:
        return None, f"GitHub error {r.status_code}: {r.text}"
    data = r.json()
    files = data.get("files", {}) or {}
    if GIST_FILE not in files or files[GIST_FILE].get("content") is None:
        return None, f"File {GIST_FILE} not found in gist"
    content = files[GIST_FILE]["content"]
    return json.loads(content), None

# -----------------------------
# CG chart mapping (your corrected base points)
# -----------------------------
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

    # corrected 85–88 base points:
    (85, 1200): 229, (85, 2295): 202,
    (86, 1200): 245, (86, 2355): 228,
    (87, 1200): 262, (87, 2440): 255,
    (88, 1200): 277, (88, 2515): 285,

    (89, 1200): 293, (89, 2550): 315,
    (90, 1200): 308, (90, 2550): 345,
    (91, 1200): 323, (91, 2550): 374,
    (92, 1200): 340, (92, 2550): 404,
    (93, 1200): 355, (93, 2550): 435,
}

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def lerp(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def interp_1d(x, pts):
    pts = sorted(pts, key=lambda p: p[0])
    x = clamp(x, pts[0][0], pts[-1][0])
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= x <= x1:
            return lerp(x, x0, x1, y0, y1)
    return pts[-1][1]

def y_from_weight(w):
    return float(interp_1d(float(w), Y_BY_WEIGHT))

def build_cg_line(cg_int: int):
    y0 = y_from_weight(1200)
    y1 = y_from_weight(2550)
    if (cg_int, 1200) not in X_AT:
        raise KeyError(f"Missing base point CG {cg_int} @1200")
    x0 = float(X_AT[(cg_int, 1200)])
    p0 = (x0, y0)
    if (cg_int, 2550) in X_AT:
        x1 = float(X_AT[(cg_int, 2550)])
        return p0, (x1, y1)
    candidates = [w for (cg, w) in X_AT.keys() if cg == cg_int and w != 1200]
    w_mid = max(candidates)
    x_mid = float(X_AT[(cg_int, w_mid)])
    y_mid = y_from_weight(w_mid)
    slope_dx_dy = 0.0 if y_mid == y0 else (x_mid - x0) / (y_mid - y0)
    x1 = x0 + slope_dx_dy * (y1 - y0)
    return p0, (x1, y1)

CG_LINES = {cg: build_cg_line(cg) for cg in range(82, 94)}

def x_on_cg_line(cg_int: int, y: float) -> float:
    (x0, y0), (x1, y1) = CG_LINES[cg_int]
    if y1 == y0:
        return x0
    t = (y - y0) / (y1 - y0)
    return x0 + t * (x1 - x0)

def cg_wt_to_xy(cg_in: float, wt_lb: float):
    y = y_from_weight(wt_lb)
    cg_in = clamp(float(cg_in), 82.0, 93.0)
    c0 = int(cg_in // 1)
    c1 = min(93, c0 + 1)
    if c0 < 82:
        c0, c1 = 82, 83
    x0 = x_on_cg_line(c0, y)
    x1 = x_on_cg_line(c1, y)
    x = lerp(cg_in, c0, c1, x0, x1) if c1 != c0 else x0
    return float(x), float(y)

def make_overlay_pdf(page_w, page_h, points, legend_xy=(500, 320), marker_r=4):
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=(page_w, page_h))

    for p in points:
        x, y = cg_wt_to_xy(p["cg"], p["wt"])
        rr, gg, bb = p["rgb"]
        c.setFillColorRGB(rr, gg, bb)
        c.circle(x, y, marker_r, fill=1, stroke=0)

    # Legend (English)
    lx, ly = legend_xy
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(lx, ly, "Legend")
    ly -= 14

    c.setFont("Helvetica", 9)
    for p in points:
        rr, gg, bb = p["rgb"]
        c.setFillColorRGB(rr, gg, bb)
        c.rect(lx, ly - 7, 10, 10, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(lx + 14, ly - 5, p["label"])
        ly -= 14

    c.showPage()
    c.save()
    bio.seek(0)
    return bio.read()

# -----------------------------
# PDF fill (Tecnam-style)
# -----------------------------
def read_pdf_bytes() -> bytes:
    with open(PDF_TEMPLATE, "rb") as f:
        return f.read()

def fill_pdf_writer(template_bytes: bytes, fields: dict) -> PdfWriter:
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

# -----------------------------
# Formatting helpers
# -----------------------------
def fmt_hm(total_min: int) -> str:
    if total_min is None or total_min <= 0:
        return "0min"
    h, m = divmod(int(round(total_min)), 60)
    if h == 0:
        return f"{m}min"
    return f"{h}h" if m == 0 else f"{h}h{m:02d}min"

def lbs(kg: float) -> float:
    return float(kg) * KG_TO_LB

def kg(lb: float) -> float:
    return float(lb) * LB_TO_KG

def usg_from_l(lit: float) -> float:
    return float(lit) * L_TO_USG

def l_from_usg(gal: float) -> float:
    return float(gal) * USG_TO_L

def fuel_lb_from_l(lit: float) -> float:
    return lbs(float(lit) * FUEL_DENS_KG_PER_L)

def moment_inlb(w_lb: float, arm_in: float) -> float:
    return float(w_lb) * float(arm_in)

def wind_str(dir_deg: int, spd_kt: int) -> str:
    d = int(dir_deg) % 360
    d = 360 if d == 0 else d
    return f"{d:03d}/{int(spd_kt):02d}"

def isa_temp_c(elev_ft: float) -> float:
    return 15.0 - 2.0 * (float(elev_ft) / 1000.0)

def pressure_alt_ft(elev_ft: float, qnh_hpa: float) -> float:
    return float(elev_ft) + (1013.0 - float(qnh_hpa)) * 30.0

def density_alt_ft(pa_ft: float, oat_c: float, elev_ft: float) -> float:
    return float(pa_ft) + 120.0 * (float(oat_c) - isa_temp_c(elev_ft))

# -----------------------------
# Session state init
# -----------------------------
if "fleet" not in st.session_state:
    st.session_state.fleet = {}
if "fleet_loaded" not in st.session_state:
    st.session_state.fleet_loaded = False

if "legs" not in st.session_state:
    # Defaults requested: lpso lpso lpev lpcb
    st.session_state.legs = [
        {"role": "Departure", "icao": "LPSO"},
        {"role": "Arrival", "icao": "LPSO"},
        {"role": "Alternate 1", "icao": "LPEV"},
        {"role": "Alternate 2", "icao": "LPCB"},
    ]

if "times" not in st.session_state:
    # Separate departure/arrival time (UTC)
    now_utc = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    st.session_state.times = {
        "dep_utc": (now_utc + dt.timedelta(hours=1)).time(),
        "arr_utc": (now_utc + dt.timedelta(hours=2)).time(),
    }

if "flight_date" not in st.session_state:
    st.session_state.flight_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).date()

if "met" not in st.session_state:
    st.session_state.met = [None, None, None, None]

# -----------------------------
# Load fleet from gist (no UI editing)
# -----------------------------
def ensure_fleet_loaded():
    if st.session_state.fleet_loaded:
        return
    token = st.secrets.get("GITHUB_GIST_TOKEN", "")
    gist_id = st.secrets.get("GITHUB_GIST_ID_PA28", "")
    if not token or not gist_id:
        st.session_state.fleet = {}
        st.session_state.fleet_loaded = True
        return
    data, err = gist_load_fleet(token, gist_id)
    if err:
        st.session_state.fleet = {}
    else:
        st.session_state.fleet = data or {}
    st.session_state.fleet_loaded = True

ensure_fleet_loaded()

def fleet_regs():
    regs = list(st.session_state.fleet.keys())
    regs.sort()
    return regs

def get_ew_moment_for_reg(reg: str):
    # Expect your gist already normalized. Typical keys: ew_lb, ew_moment_inlb
    d = st.session_state.fleet.get(reg, {}) if reg else {}
    ew = d.get("ew_lb", d.get("ew", d.get("empty_weight_lb")))
    em = d.get("ew_moment_inlb", d.get("ew_moment", d.get("empty_moment_inlb")))
    return ew, em

# -----------------------------
# Tabs
# -----------------------------
tab_setup, tab_aero, tab_wb, tab_fuel, tab_pdf = st.tabs(
    ["1) Aircraft & Flight", "2) Aerodromes & Weather", "3) Weight & Balance", "4) Fuel Planning", "5) PDF"]
)

# -----------------------------
# 1) Aircraft & Flight
# -----------------------------
with tab_setup:
    c1, c2, c3 = st.columns([0.36, 0.32, 0.32])
    with c1:
        regs = fleet_regs()
        if not regs:
            st.error("Fleet not loaded. Add Streamlit secrets: GITHUB_GIST_TOKEN and GITHUB_GIST_ID_PA28.")
            reg = st.text_input("Aircraft Reg (fallback)", value="CS-XXX")
        else:
            reg = st.selectbox("Aircraft Reg", regs, index=0)
        st.session_state["reg"] = reg

    with c2:
        st.session_state.flight_date = st.date_input("Flight date (Europe/Lisbon)", value=st.session_state.flight_date)
    with c3:
        mission = st.text_input("Mission/Ref (optional)", value=st.session_state.get("mission", ""))
        st.session_state["mission"] = mission

    t1, t2 = st.columns(2)
    with t1:
        dep_time = st.time_input("Departure time (UTC)", value=st.session_state.times["dep_utc"], step=3600)
        st.session_state.times["dep_utc"] = dep_time
    with t2:
        arr_time = st.time_input("Arrival time (UTC)", value=st.session_state.times["arr_utc"], step=3600)
        st.session_state.times["arr_utc"] = arr_time

    st.markdown(
        "<div class='hint'>Weather uses Open-Meteo hourly model. Wind is vector-mean of ±1h around the selected hour and direction rounded to nearest 10°.</div>",
        unsafe_allow_html=True,
    )

# -----------------------------
# 2) Aerodromes & Weather
# -----------------------------
with tab_aero:
    st.markdown("### Aerodromes")
    rows = []
    for i, leg in enumerate(st.session_state.legs):
        role = leg["role"]
        col1, col2, col3 = st.columns([0.22, 0.25, 0.53])
        with col1:
            st.write(f"**{role}**")
        with col2:
            icao = st.selectbox(
                f"{role} ICAO",
                options=ICAO_OPTIONS,
                index=ICAO_OPTIONS.index(leg["icao"]) if leg["icao"] in ICAO_OPTIONS else 0,
                key=f"icao_{i}",
            )
            st.session_state.legs[i]["icao"] = icao
        with col3:
            ad = AERODROMES_DB[icao]
            st.write(f"{ad['name']}  · Elev {ad['elev_ft']:.0f} ft")

        # runway selector (compact)
        rw_ids = [r["id"] for r in ad["runways"]]
        rw_default = rw_ids[0]
        rw = st.selectbox(f"{role} RWY", rw_ids, index=0, key=f"rwy_{i}")
        rw_obj = next(r for r in ad["runways"] if r["id"] == rw)

        # time target: dep for Departure, arrival time for others (as you wanted)
        tgt_time = st.session_state.times["dep_utc"] if role == "Departure" else st.session_state.times["arr_utc"]
        target_dt_utc = dt.datetime.combine(st.session_state.flight_date, tgt_time).replace(tzinfo=dt.timezone.utc)

        rows.append({"i": i, "role": role, "icao": icao, "ad": ad, "rwy": rw_obj, "target": target_dt_utc})

    st.markdown("---")
    cbtn, cinfo = st.columns([0.22, 0.78])
    with cbtn:
        if st.button("Fetch model weather", type="primary"):
            for r in rows:
                met, err = met_at(r["ad"], r["target"])
                if err:
                    st.session_state.met[r["i"]] = None
                    st.error(f"{r['icao']} ({r['role']}): {err}")
                else:
                    st.session_state.met[r["i"]] = met
                    st.success(f"{r['icao']} ({r['role']}): {met['label']}  ·  {wind_str(met['wind_dir'], met['wind_kt'])}  ·  {met['temp_c']}°C  ·  QNH {met['qnh_hpa']}")

    with cinfo:
        st.markdown("<div class='hint'>Departure uses Departure time; Arrival and both Alternates use Arrival time.</div>", unsafe_allow_html=True)

    st.markdown("### Current model values")
    for r in rows:
        met = st.session_state.met[r["i"]]
        if not met:
            st.warning(f"{r['role']} {r['icao']}: no model data yet (press Fetch).")
            continue

        ad = r["ad"]
        pa = pressure_alt_ft(ad["elev_ft"], met["qnh_hpa"])
        da = density_alt_ft(pa, met["temp_c"], ad["elev_ft"])

        st.markdown(
            f"<div class='box'>"
            f"<b>{r['role']} · {r['icao']}</b> "
            f"<span class='pill'>RWY {r['rwy']['id']} / QFU {r['rwy']['qfu']:.0f}°</span>"
            f"<span class='pill'>TODA {r['rwy']['toda']:.0f} m</span>"
            f"<span class='pill'>LDA {r['rwy']['lda']:.0f} m</span>"
            f"<br><span class='hint'>"
            f"{met['label']} · Wind {wind_str(met['wind_dir'], met['wind_kt'])} · OAT {met['temp_c']}°C · QNH {met['qnh_hpa']} · PA {pa:.0f} ft · DA {da:.0f} ft"
            f"</span></div>",
            unsafe_allow_html=True,
        )

# -----------------------------
# 3) Weight & Balance (UI in kg/L, computations in lb/in-lb)
# -----------------------------
with tab_wb:
    st.markdown("### Weight & Balance")

    reg = st.session_state.get("reg", "")
    ew_lb, ew_m_inlb = get_ew_moment_for_reg(reg)

    if ew_lb is None or ew_m_inlb is None:
        st.error("This aircraft has no ew_lb / ew_moment_inlb in the fleet gist.")
        st.stop()

    # Inputs
    c1, c2, c3 = st.columns([0.34, 0.33, 0.33])
    with c1:
        st.markdown("**Crew / pax (kg)**")
        student_kg = st.number_input("Student", min_value=0.0, value=70.0, step=0.5)
        instructor_kg = st.number_input("Instructor", min_value=0.0, value=0.0, step=0.5)
        rear_kg = st.number_input("Rear passengers", min_value=0.0, value=0.0, step=0.5)
    with c2:
        st.markdown("**Baggage / Fuel**")
        baggage_kg = st.number_input("Baggage (kg)", min_value=0.0, value=0.0, step=0.5)
        fuel_l = st.number_input("Fuel (L)", min_value=0.0, value=0.0, step=1.0)
        st.markdown(f"<div class='hint'>Max fuel: {MAX_FUEL_USG:.0f} USG ({l_from_usg(MAX_FUEL_USG):.0f} L)</div>", unsafe_allow_html=True)
    with c3:
        st.markdown("**Fixed arms (in)**")
        st.write(f"Front: **{ARM_FRONT:.1f}**")
        st.write(f"Rear: **{ARM_REAR:.1f}**")
        st.write(f"Fuel: **{ARM_FUEL:.1f}**")
        st.write(f"Baggage: **{ARM_BAG:.1f}**")

    # Conversions
    front_lb = lbs(student_kg + instructor_kg)
    rear_lb = lbs(rear_kg)
    bag_lb = lbs(baggage_kg)
    fuel_lb = fuel_lb_from_l(fuel_l)
    fuel_usg = usg_from_l(fuel_l)

    # Moments
    m_empty = float(ew_m_inlb)
    m_front = moment_inlb(front_lb, ARM_FRONT)
    m_rear = moment_inlb(rear_lb, ARM_REAR)
    m_fuel = moment_inlb(fuel_lb, ARM_FUEL)
    m_bag = moment_inlb(bag_lb, ARM_BAG)

    # Ramp totals
    ramp_w = float(ew_lb) + front_lb + rear_lb + fuel_lb + bag_lb
    ramp_m = m_empty + m_front + m_rear + m_fuel + m_bag
    ramp_cg = (ramp_m / ramp_w) if ramp_w > 0 else 0.0

    # Takeoff totals (minus taxi allowance)
    to_w = ramp_w - TAXI_ALLOW_LB
    to_m = ramp_m - moment_inlb(TAXI_ALLOW_LB, TAXI_ALLOW_ARM)
    to_cg = (to_m / to_w) if to_w > 0 else 0.0

    # Store for later tabs
    st.session_state["_wb"] = {
        "ew_lb": float(ew_lb),
        "ew_m_inlb": float(ew_m_inlb),
        "front_lb": float(front_lb),
        "rear_lb": float(rear_lb),
        "bag_lb": float(bag_lb),
        "fuel_lb": float(fuel_lb),
        "fuel_l": float(fuel_l),
        "fuel_usg": float(fuel_usg),
        "ramp_w": float(ramp_w),
        "ramp_m": float(ramp_m),
        "ramp_cg": float(ramp_cg),
        "to_w": float(to_w),
        "to_m": float(to_m),
        "to_cg": float(to_cg),
    }

    # Summary
    mtow_ok = to_w <= MTOW_LB
    fuel_ok = fuel_usg <= MAX_FUEL_USG + 1e-6

    st.markdown("#### Summary")
    st.markdown(
        f"<div class='box'>"
        f"Empty: <b>{ew_lb:.0f} lb</b> ({kg(ew_lb):.0f} kg) · CG {float(ew_m_inlb)/float(ew_lb):.1f} in<br>"
        f"Ramp: <b>{ramp_w:.0f} lb</b> ({kg(ramp_w):.0f} kg) · CG {ramp_cg:.1f} in<br>"
        f"Takeoff: <b>{to_w:.0f} lb</b> ({kg(to_w):.0f} kg) · CG {to_cg:.1f} in<br>"
        f"Fuel: <b>{fuel_usg:.1f} USG</b> ({fuel_l:.0f} L) · {fuel_lb:.0f} lb ({kg(fuel_lb):.0f} kg)<br>"
        f"<span class='{ 'ok' if mtow_ok else 'bad' }'>MTOW {MTOW_LB:.0f} lb: {'OK' if mtow_ok else 'NOK'}</span>"
        f" · <span class='{ 'ok' if fuel_ok else 'bad' }'>Max Fuel {MAX_FUEL_USG:.0f} USG: {'OK' if fuel_ok else 'NOK'}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

# -----------------------------
# 4) Fuel Planning (simple, clean; used for Landing CG)
# -----------------------------
with tab_fuel:
    st.markdown("### Fuel Planning (used to compute Landing weight/CG)")

    wb = st.session_state.get("_wb", {})
    fuel_l_loaded = wb.get("fuel_l", 0.0)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        rate_lph = st.number_input("Fuel flow (L/h)", min_value=5.0, max_value=80.0, value=36.0, step=0.5)
        taxi_min = st.number_input("(1) Start-up & Taxi (min)", min_value=0, value=15, step=1)
        climb_min = st.number_input("(2) Climb (min)", min_value=0, value=10, step=1)
    with c2:
        enrt_h = st.number_input("(3) Enroute (h)", min_value=0, value=1, step=1)
        enrt_min = st.number_input("(3) Enroute (min)", min_value=0, value=0, step=5)
        desc_min = st.number_input("(4) Descent (min)", min_value=0, value=10, step=1)
    with c3:
        alt_min = st.number_input("(7) Alternate (min)", min_value=0, value=45, step=5)
        reserve_min = 45
        st.write("Reserve: **45 min** (fixed)")
    with c4:
        st.markdown("**Policy**")
        st.write("Contingency: **5% of Trip**")

    def l_from_min(mins, rate=rate_lph):
        return float(rate) * (float(mins) / 60.0)

    enrt_min_eff = enrt_h * 60 + enrt_min
    trip_min = climb_min + enrt_min_eff + desc_min
    trip_l = l_from_min(trip_min)
    cont_l = 0.05 * trip_l
    cont_min = int(round(0.05 * trip_min))

    taxi_l = l_from_min(taxi_min)
    alt_l = l_from_min(alt_min)
    reserve_l = l_from_min(reserve_min)

    req_ramp_l = taxi_l + trip_l + cont_l + alt_l + reserve_l
    extra_l = max(0.0, fuel_l_loaded - req_ramp_l)

    if fuel_l_loaded + 1e-6 < req_ramp_l:
        st.error(f"Fuel insufficient: loaded {fuel_l_loaded:.0f} L, required {req_ramp_l:.0f} L.")

    st.session_state["_fuel"] = {
        "rate_lph": float(rate_lph),
        "taxi_min": int(taxi_min),
        "climb_min": int(climb_min),
        "enrt_min": int(enrt_min_eff),
        "desc_min": int(desc_min),
        "trip_min": int(trip_min),
        "cont_min": int(cont_min),
        "alt_min": int(alt_min),
        "reserve_min": int(reserve_min),
        "taxi_l": float(taxi_l),
        "trip_l": float(trip_l),
        "cont_l": float(cont_l),
        "alt_l": float(alt_l),
        "reserve_l": float(reserve_l),
        "req_ramp_l": float(req_ramp_l),
        "extra_l": float(extra_l),
    }

    # Landing after Trip (destination landing) – subtract trip fuel (climb+enroute+descent) from Takeoff
    wb = st.session_state.get("_wb", {})
    takeoff_w = wb.get("to_w", 0.0)
    takeoff_m = wb.get("to_m", 0.0)

    trip_burn_lb = fuel_lb_from_l(trip_l)
    landing_w = takeoff_w - trip_burn_lb
    landing_m = takeoff_m - moment_inlb(trip_burn_lb, ARM_FUEL)
    landing_cg = (landing_m / landing_w) if landing_w > 0 else 0.0

    st.session_state["_landing"] = {
        "w_lb": float(landing_w),
        "m_inlb": float(landing_m),
        "cg_in": float(landing_cg),
        "trip_burn_lb": float(trip_burn_lb),
    }

    # Small summary
    st.markdown(
        f"<div class='box'>"
        f"Trip burn: <b>{trip_l:.0f} L</b> ({usg_from_l(trip_l):.1f} USG) · {trip_burn_lb:.0f} lb<br>"
        f"Landing estimate (after Trip): <b>{landing_w:.0f} lb</b> ({kg(landing_w):.0f} kg) · CG {landing_cg:.1f} in"
        f"</div>",
        unsafe_allow_html=True,
    )

# -----------------------------
# 5) PDF
# -----------------------------
with tab_pdf:
    st.markdown("### Generate filled PDF")

    reg = st.session_state.get("reg", "")
    date_str = st.session_state.flight_date.strftime("%d/%m/%Y")
    mission = (st.session_state.get("mission", "") or "").strip().replace(" ", "_")

    wb = st.session_state.get("_wb", {})
    fu = st.session_state.get("_fuel", {})
    ld = st.session_state.get("_landing", {})

    if not wb:
        st.warning("Go to Weight & Balance tab first.")
        st.stop()

    # Empty CG from fleet numbers
    empty_cg = (wb["ew_m_inlb"] / wb["ew_lb"]) if wb["ew_lb"] > 0 else 0.0
    to_cg = wb.get("to_cg", 0.0)
    to_w = wb.get("to_w", 0.0)
    landing_cg = ld.get("cg_in", to_cg)
    landing_w = ld.get("w_lb", to_w)

    # Build MET/perf rows for PDF page 2
    legs = st.session_state.legs
    met_list = st.session_state.met

    def get_leg_bundle(i):
        icao = legs[i]["icao"]
        ad = AERODROMES_DB[icao]
        rw = ad["runways"][0]
        # UI saved runway choice exists (same key as tab_aero)
        rw_id = st.session_state.get(f"rwy_{i}", rw["id"])
        rw = next((r for r in ad["runways"] if r["id"] == rw_id), rw)

        met = met_list[i] or {"temp_c": 15, "qnh_hpa": 1013, "wind_dir": 240, "wind_kt": 8, "label": ""}
        pa = pressure_alt_ft(ad["elev_ft"], met["qnh_hpa"])
        da = density_alt_ft(pa, met["temp_c"], ad["elev_ft"])
        return {
            "icao": icao,
            "rw": rw,
            "ad": ad,
            "met": met,
            "pa": pa,
            "da": da,
        }

    bundles = [get_leg_bundle(i) for i in range(4)]

    # -----------------------------
    # Field map (exact names, no fallbacks)
    # -----------------------------
    def w_with_kg(lb_val: float) -> str:
        return f"{lb_val:.0f} ({kg(lb_val):.0f}kg)"

    def fuel_weight_cell(lb_val: float, usg_val: float, l_val: float) -> str:
        return f"{lb_val:.0f} ({usg_val:.1f}G/{l_val:.0f}L)"

    def fuel_cell_usg_l(l_val: float) -> str:
        return f"{usg_from_l(l_val):.1f}G ({l_val:.0f}L)"

    # Page 0 (Loading data)
    fields = {
        # Page 1 (index 1) header
        "Date": date_str,
        "Aircraft_Reg": reg,

        "Weight_EMPTY": w_with_kg(wb["ew_lb"]),
        "Datum_EMPTY": f"{empty_cg:.1f}",
        "Moment_EMPTY": f"{wb['ew_m_inlb']:.0f}",

        "Weight_FRONT": w_with_kg(wb["front_lb"]),
        "Moment_FRONT": f"{moment_inlb(wb['front_lb'], ARM_FRONT):.0f}",

        "Weight_REAR": w_with_kg(wb["rear_lb"]),
        "Moment_REAR": f"{moment_inlb(wb['rear_lb'], ARM_REAR):.0f}",

        "Weight_FUEL": fuel_weight_cell(wb["fuel_lb"], wb["fuel_usg"], wb["fuel_l"]),
        "Moment_FUEL": f"{moment_inlb(wb['fuel_lb'], ARM_FUEL):.0f}",

        "Weight_BAGGAGE": w_with_kg(wb["bag_lb"]),
        "Moment_BAGGAGE": f"{moment_inlb(wb['bag_lb'], ARM_BAG):.0f}",

        "Weight_RAMP": w_with_kg(wb["ramp_w"]),
        "Datum_RAMP": f"{wb['ramp_cg']:.1f}",
        "Moment_RAMP": f"{wb['ramp_m']:.0f}",

        "Weight_TAKEOFF": w_with_kg(wb["to_w"]),
        "Datum_TAKEOFF": f"{wb['to_cg']:.1f}",
        "Moment_TAKEOFF": f"{wb['to_m']:.0f}",

        "MTOW": f"{MTOW_LB:.0f}",
        "MLW": f"{MLW_LB:.0f}",
    }

    # Page 1 (index 1) legs field suffixes
    suf_map = {
        0: "DEPARTURE",
        1: "ARRIVAL",
        2: "ALTERNATE_1",
        3: "ALTERNATE_2",
    }

    for i, suf in suf_map.items():
        b = bundles[i]
        met = b["met"]
        rw = b["rw"]
        ad = b["ad"]

        fields[f"Airfield_{suf}"] = b["icao"]
        fields[f"RWY_QFU_{suf}"] = f"{int(round(rw['qfu'])):03d}"
        fields[f"Elevation_{suf}"] = f"{int(round(ad['elev_ft']))}"
        fields[f"QNH_{suf}"] = f"{int(round(met['qnh_hpa']))}"
        fields[f"Temperature_{suf}"] = f"{int(round(met['temp_c']))}"
        fields[f"Wind_{suf}"] = wind_str(met["wind_dir"], met["wind_kt"])
        if suf == "DEPARTURE":
            fields["Pressure_Alt _DEPARTURE"] = f"{int(round(b['pa']))}"
        else:
            fields[f"Pressure_Alt_{suf}"] = f"{int(round(b['pa']))}"
        fields[f"Density_Alt_{suf}"] = f"{int(round(b['da']))}"

        # Runway distances available (sheet asks TODA/LDA; performance calcs can be added later)
        fields[f"TODA_{suf}"] = f"{int(round(rw['toda']))}"
        fields[f"LDA_{suf}"] = f"{int(round(rw['lda']))}"
        # Leave these blank for now (you can later add POH tables)
        fields[f"TODR_{suf}"] = ""
        fields[f"LDR_{suf}"] = ""
        fields[f"ROC_{suf}"] = ""

    # Fuel planning fields (Time / Fuel). Fuel in USG + (L)
    def set_fuel_line(prefix_time, prefix_fuel, mins, lit):
        fields[prefix_time] = fmt_hm(int(mins))
        fields[prefix_fuel] = fuel_cell_usg_l(float(lit))

    set_fuel_line("Start-up_and_Taxi_TIME", "Start-up_and_Taxi_FUEL", fu.get("taxi_min", 0), fu.get("taxi_l", 0.0))
    set_fuel_line("CLIMB_TIME", "CLIMB_FUEL", fu.get("climb_min", 0), l_from_usg(0.0) if False else l_from_usg(0.0))  # unused, keep clean
    # For clarity: derive liters from minutes for each block
    rate = float(fu.get("rate_lph", 36.0))
    def l_from_min(mins): return rate * (float(mins)/60.0)

    set_fuel_line("CLIMB_TIME", "CLIMB_FUEL", fu.get("climb_min", 0), l_from_min(fu.get("climb_min", 0)))
    set_fuel_line("ENROUTE_TIME", "ENROUTE_FUEL", fu.get("enrt_min", 0), l_from_min(fu.get("enrt_min", 0)))
    set_fuel_line("DESCENT_TIME", "DESCENT_FUEL", fu.get("desc_min", 0), l_from_min(fu.get("desc_min", 0)))

    trip_min = int(fu.get("trip_min", 0))
    trip_l = float(fu.get("trip_l", 0.0))
    set_fuel_line("TRIP_TIME", "TRIP_FUEL", trip_min, trip_l)

    set_fuel_line("Contingency_TIME", "Contingency_FUEL", fu.get("cont_min", 0), fu.get("cont_l", 0.0))
    set_fuel_line("ALTERNATE_TIME", "ALTERNATE_FUEL", fu.get("alt_min", 0), fu.get("alt_l", 0.0))
    set_fuel_line("RESERVE_TIME", "RESERVE_FUEL", fu.get("reserve_min", 45), fu.get("reserve_l", 0.0))

    req_min = int(fu.get("taxi_min", 0) + fu.get("trip_min", 0) + fu.get("cont_min", 0) + fu.get("alt_min", 0) + fu.get("reserve_min", 45))
    req_l = float(fu.get("req_ramp_l", 0.0))
    fields["REQUIRED_TIME"] = fmt_hm(req_min)
    fields["REQUIRED_FUEL"] = fuel_cell_usg_l(req_l)

    extra_l = float(fu.get("extra_l", 0.0))
    fields["EXTRA_TIME"] = fmt_hm(int(round((extra_l / rate) * 60))) if rate > 0 else "0min"
    fields["EXTRA_FUEL"] = fuel_cell_usg_l(extra_l)

    total_l = req_l + extra_l
    total_min = req_min + int(round((extra_l / rate) * 60)) if rate > 0 else req_min
    fields["Total_TIME"] = fmt_hm(total_min)
    fields["Total_FUEL"] = fuel_cell_usg_l(total_l)

    # -----------------------------
    # Build PDF + overlay CG chart on page 0
    # -----------------------------
    if st.button("Generate filled PDF", type="primary"):
        try:
            template_bytes = read_pdf_bytes()
            reader = PdfReader(io.BytesIO(template_bytes))
            writer = fill_pdf_writer(template_bytes, fields)

            # Overlay chart points on page 0
            page0 = reader.pages[0]
            page_w = float(page0.mediabox.width)
            page_h = float(page0.mediabox.height)

            points = [
                {"label": "Empty",   "cg": float(empty_cg),   "wt": float(wb["ew_lb"]), "rgb": (0.10, 0.60, 0.10)},
                {"label": "Takeoff", "cg": float(to_cg),      "wt": float(to_w),        "rgb": (0.10, 0.30, 0.85)},
                {"label": "Landing", "cg": float(landing_cg), "wt": float(landing_w),   "rgb": (0.85, 0.20, 0.20)},
            ]
            overlay_bytes = make_overlay_pdf(page_w, page_h, points, legend_xy=(500, 320), marker_r=4)
            overlay_page = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
            writer.pages[0].merge_page(overlay_page)

            out = io.BytesIO()
            writer.write(out)
            out.seek(0)

            fname = f"{(mission + '_') if mission else ''}{reg}_PA28_MB_Perf.pdf"
            st.download_button("Download PDF", data=out.getvalue(), file_name=fname, mime="application/pdf")
            st.success("PDF generated.")
        except FileNotFoundError:
            st.error(f"Template not found. Put {PDF_TEMPLATE} in the repo root (same level as app.py).")
        except Exception as e:
            st.error(f"Could not generate PDF: {e}")

