# streamlit_app.py
# PA-28 Archer III – M&B + MET (METAR/TAF) + PDF fill + CG chart overlay
# Requires:
#   streamlit
#   requests
#   pypdf>=4.2.0
#   reportlab

import io
import json
import re
import datetime as dt
from math import floor

import requests
import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas


# ============================================================
# CONFIG / CONSTANTS
# ============================================================
PDF_TEMPLATE = "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"
GRAPH_PAGE_INDEX = 0  # first page (0-based)

# PA28 defaults (you can override station arms per aircraft via Gist data)
DEFAULT_ARMS_IN = {
    "front": 80.5,
    "rear": 118.1,
    "fuel": 95.0,
    "baggage": 142.8,
    "taxi_fuel_arm": 95.5,
}
DEFAULT_TAXI_FUEL_ALLOW_LB = -8  # fixed line on sheet

# Limits (static)
MTOW_LB = 2550
MLW_LB = 2440
MAX_FUEL_GAL = 48
MAX_BAGGAGE_LB = 200

# Fuel density (Avgas)
FUEL_LB_PER_GAL = 6.0

# Gist persistence
GIST_FILE = "fleet_pa28.json"
GITHUB_API_GIST = "https://api.github.com/gists"

# NOAA AviationWeather (ADDS)
ADDS_METAR_URL = "https://aviationweather.gov/api/data/metar"
ADDS_TAF_URL = "https://aviationweather.gov/api/data/taf"


# ============================================================
# CG chart mapping (from your coordinates) – bottom-left origin
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

    # corrected 85–88 at 1200
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


# ============================================================
# Helpers
# ============================================================
def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def lerp(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def interp_1d(x, pts):
    pts = sorted(pts, key=lambda p: p[0])
    x = clamp(float(x), float(pts[0][0]), float(pts[-1][0]))
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= x <= x1:
            return lerp(x, x0, x1, y0, y1)
    return float(pts[-1][1])

def y_from_weight(w_lb: float) -> float:
    return float(interp_1d(float(w_lb), Y_BY_WEIGHT))

def build_cg_line(cg_int: int):
    y0 = y_from_weight(1200)
    y1 = y_from_weight(2550)

    x0 = float(X_AT[(cg_int, 1200)])
    p0 = (x0, y0)

    if (cg_int, 2550) in X_AT:
        x1 = float(X_AT[(cg_int, 2550)])
        return p0, (x1, y1)

    # extrapolate from highest intermediate point
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
    c0 = int(floor(cg_in))
    c0 = clamp(c0, 82, 93)
    c1 = min(93, c0 + 1)

    x0 = x_on_cg_line(c0, y)
    x1 = x_on_cg_line(c1, y)
    x = lerp(cg_in, c0, c1, x0, x1) if c1 != c0 else x0
    return float(x), float(y)

def fmt_wind(dir_deg: int | None, spd_kt: int | None) -> str:
    if dir_deg is None or spd_kt is None:
        return ""
    # round direction to nearest 10 degrees, keep 360 instead of 0
    d = int(round(dir_deg / 10.0) * 10) % 360
    if d == 0:
        d = 360
    return f"{d:03d}/{int(spd_kt):02d}"

def safe_int(v, default=0):
    try:
        return int(round(float(v)))
    except Exception:
        return default

def safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def moment_inlbs(weight_lb: float, arm_in: float, rounding: int = 1) -> int:
    # rounding=1 => nearest 1, rounding=10 => nearest 10
    raw = float(weight_lb) * float(arm_in)
    return int(round(raw / rounding) * rounding)


# ============================================================
# Gist
# ============================================================
def gist_headers(token: str):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gist_load_fleet(token: str, gist_id: str):
    r = requests.get(f"{GITHUB_API_GIST}/{gist_id}", headers=gist_headers(token), timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"GitHub gist error {r.status_code}: {r.text}")
    data = r.json()
    files = data.get("files", {})
    if GIST_FILE not in files or files[GIST_FILE].get("content") is None:
        raise RuntimeError(f"Gist file '{GIST_FILE}' not found inside gist.")
    return json.loads(files[GIST_FILE]["content"])


# ============================================================
# METAR/TAF (NOAA AviationWeather)
# ============================================================
WIND_RE = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(G\d{2,3})?KT\b")

def fetch_metar_raw(icao: str) -> str:
    params = {"ids": icao.upper(), "format": "raw", "hours": 24}
    r = requests.get(ADDS_METAR_URL, params=params, timeout=15)
    r.raise_for_status()
    txt = (r.text or "").strip()
    # API returns multiple lines sometimes; first is latest
    return txt.splitlines()[0].strip() if txt else ""

def fetch_taf_raw(icao: str) -> str:
    params = {"ids": icao.upper(), "format": "raw", "hours": 36}
    r = requests.get(ADDS_TAF_URL, params=params, timeout=15)
    r.raise_for_status()
    txt = (r.text or "").strip()
    return txt.splitlines()[0].strip() if txt else ""

def parse_wind_from_text(txt: str):
    m = WIND_RE.search(txt or "")
    if not m:
        return None, None
    d = m.group(1)
    spd = m.group(2)
    if d == "VRB":
        return 0, int(spd)
    return int(d), int(spd)

def taf_choose_segment_wind(taf_raw: str, target_utc: dt.datetime):
    """
    Simple TAF segment picker:
      - splits by FMxxxxxx groups (common)
      - chooses the latest FM group whose start <= target
      - extracts first wind group in that segment
    If can't, returns (None, None).
    """
    if not taf_raw or "TAF" not in taf_raw:
        return None, None

    # Extract issue day/time (DDHHMMZ)
    issue = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", taf_raw)
    if not issue:
        return None, None
    iss_dd, iss_hh, iss_mm = map(int, issue.groups())

    # Guess issue month/year from target
    base = target_utc.replace(hour=iss_hh, minute=iss_mm, second=0, microsecond=0)
    # set day, handle rollover crudely
    try:
        base = base.replace(day=iss_dd)
    except ValueError:
        # fallback: clamp day
        base = base.replace(day=min(iss_dd, 28))

    # Find FM groups
    parts = re.split(r"\b(FM\d{6})\b", taf_raw)
    # parts like: [prefix, 'FMDDHHMM', rest, 'FM...', rest...]
    segments = []
    # prefix segment is valid from issue
    segments.append((base, parts[0]))

    i = 1
    while i < len(parts) - 1:
        fm_tag = parts[i]      # e.g. FM201200
        body = parts[i + 1]    # segment text
        fm = re.match(r"FM(\d{2})(\d{2})(\d{2})", fm_tag)
        if fm:
            dd, hh, mm = map(int, fm.groups())
            seg_start = target_utc.replace(hour=hh, minute=mm, second=0, microsecond=0)
            try:
                seg_start = seg_start.replace(day=dd)
            except ValueError:
                seg_start = seg_start.replace(day=min(dd, 28))
            segments.append((seg_start, fm_tag + body))
        i += 2

    # choose latest seg_start <= target
    segments = sorted(segments, key=lambda x: x[0])
    chosen = segments[0][1]
    for t0, seg in segments:
        if t0 <= target_utc:
            chosen = seg
        else:
            break

    return parse_wind_from_text(chosen)

def get_best_wind(icao: str, target_utc: dt.datetime):
    """
    Prefer TAF wind at target time; fallback to METAR wind.
    Returns: (wind_dir_deg, wind_spd_kt, metar_raw, taf_raw)
    """
    metar = ""
    taf = ""
    try:
        metar = fetch_metar_raw(icao)
    except Exception:
        metar = ""
    try:
        taf = fetch_taf_raw(icao)
    except Exception:
        taf = ""

    d, s = taf_choose_segment_wind(taf, target_utc)
    if d is None or s is None:
        d, s = parse_wind_from_text(metar)
    return d, s, metar, taf


# ============================================================
# PDF fill + overlay
# ============================================================
def fill_pdf(template_bytes: bytes, fields: dict) -> bytes:
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

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

def make_overlay_pdf(page_w, page_h, points, legend_xy=(500, 320), marker_r=4):
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=(page_w, page_h))

    for p in points:
        x, y = cg_wt_to_xy(p["cg"], p["wt"])
        rr, gg, bb = p["rgb"]
        c.setFillColorRGB(rr, gg, bb)
        c.circle(x, y, marker_r, fill=1, stroke=0)

    # legend
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

def merge_overlay_on_page(pdf_bytes: bytes, page_index: int, overlay_pdf_bytes: bytes) -> bytes:
    base = PdfReader(io.BytesIO(pdf_bytes))
    ov = PdfReader(io.BytesIO(overlay_pdf_bytes))
    writer = PdfWriter()
    for i, page in enumerate(base.pages):
        if i == page_index:
            page.merge_page(ov.pages[0])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ============================================================
# Streamlit app
# ============================================================
st.set_page_config(page_title="PA-28 – M&B + MET + PDF", layout="wide")
st.title("Piper PA-28 Archer III – Mass & Balance + MET + PDF")

# ---- Load fleet from Gist
if "fleet" not in st.session_state:
    st.session_state.fleet = None
if "fleet_err" not in st.session_state:
    st.session_state.fleet_err = None

token = st.secrets.get("GITHUB_GIST_TOKEN", "")
gist_id = st.secrets.get("GITHUB_GIST_ID_PA28", "")

with st.sidebar:
    st.subheader("⚙️ Data source")
    st.caption("Fleet loaded from GitHub Gist (fleet_pa28.json).")
    if st.button("Reload fleet from Gist"):
        st.session_state.fleet = None

if st.session_state.fleet is None:
    try:
        if not token or not gist_id:
            raise RuntimeError("Missing secrets: GITHUB_GIST_TOKEN and/or GITHUB_GIST_ID_PA28")
        st.session_state.fleet = gist_load_fleet(token, gist_id)
        st.session_state.fleet_err = None
    except Exception as e:
        st.session_state.fleet_err = str(e)
        st.session_state.fleet = {}

if st.session_state.fleet_err:
    st.error(st.session_state.fleet_err)

fleet = st.session_state.fleet or {}
regs = sorted(list(fleet.keys())) or ["CS-XXX"]

tabs = st.tabs(["1) Aircraft & Times", "2) Aerodromes & MET", "3) Weight & Balance", "4) Fuel & Landing", "5) PDF"])

# ============================================================
# 1) Aircraft & Times
# ============================================================
with tabs[0]:
    c1, c2, c3 = st.columns([0.35, 0.35, 0.30])

    with c1:
        reg = st.selectbox("Aircraft Reg", regs, index=0)
        ac = fleet.get(reg, {}) if isinstance(fleet, dict) else {}
        st.caption("Loaded from Gist for this registration.")

    with c2:
        flight_date = st.date_input("Flight date (UTC)", value=dt.datetime.utcnow().date())
        dep_time = st.time_input("Departure time (UTC)", value=(dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)).time(), step=900)
        arr_time = st.time_input("Arrival time (UTC)", value=(dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=2)).time(), step=900)

        dep_dt = dt.datetime.combine(flight_date, dep_time, tzinfo=dt.timezone.utc)
        arr_dt = dt.datetime.combine(flight_date, arr_time, tzinfo=dt.timezone.utc)

    with c3:
        st.markdown("**Station arms (in)**")
        # Take from gist if present, else defaults
        arm_front = st.number_input("Front", value=safe_float(ac.get("arm_front", DEFAULT_ARMS_IN["front"])), step=0.1, format="%.1f")
        arm_rear = st.number_input("Rear", value=safe_float(ac.get("arm_rear", DEFAULT_ARMS_IN["rear"])), step=0.1, format="%.1f")
        arm_fuel = st.number_input("Fuel", value=safe_float(ac.get("arm_fuel", DEFAULT_ARMS_IN["fuel"])), step=0.1, format="%.1f")
        arm_bag = st.number_input("Baggage", value=safe_float(ac.get("arm_baggage", DEFAULT_ARMS_IN["baggage"])), step=0.1, format="%.1f")
        taxi_fuel_arm = st.number_input("Taxi fuel arm", value=safe_float(ac.get("arm_taxi_fuel", DEFAULT_ARMS_IN["taxi_fuel_arm"])), step=0.1, format="%.1f")

    st.session_state["_meta"] = {
        "reg": reg,
        "date_str": flight_date.strftime("%d/%m/%Y"),
        "dep_dt": dep_dt,
        "arr_dt": arr_dt,
        "arms": {
            "front": arm_front,
            "rear": arm_rear,
            "fuel": arm_fuel,
            "baggage": arm_bag,
            "taxi_fuel": taxi_fuel_arm,
        }
    }

# ============================================================
# 2) Aerodromes & MET
# ============================================================
with tabs[1]:
    st.markdown("### Aerodromes + MET (METAR/TAF)")

    cols = st.columns(4)
    with cols[0]:
        dep_icao = st.text_input("Departure ICAO", value="LPCS").strip().upper()
    with cols[1]:
        arr_icao = st.text_input("Arrival ICAO", value="LPPT").strip().upper()
    with cols[2]:
        alt1_icao = st.text_input("Alternate 1 ICAO", value="LPMT").strip().upper()
    with cols[3]:
        alt2_icao = st.text_input("Alternate 2 ICAO", value="LPSO").strip().upper()

    dep_dt = st.session_state["_meta"]["dep_dt"]
    arr_dt = st.session_state["_meta"]["arr_dt"]

    fetch = st.button("Fetch MET (TAF preferred)")

    if "met" not in st.session_state:
        st.session_state.met = {}

    def met_box(title, icao, target_dt, key_prefix):
        st.markdown(f"#### {title} – {icao} – target **{target_dt.strftime('%Y-%m-%d %H:%MZ')}**")
        if fetch:
            d, s, metar, taf = get_best_wind(icao, target_dt)
            st.session_state.met[key_prefix] = {
                "wind_dir": d, "wind_spd": s,
                "metar": metar, "taf": taf
            }

        data = st.session_state.met.get(key_prefix, {})
        # editable overrides
        wdir = st.number_input(f"{title} wind dir (deg)", min_value=0, max_value=360, value=safe_int(data.get("wind_dir", 0)), step=1, key=f"{key_prefix}_wdir")
        wspd = st.number_input(f"{title} wind spd (kt)", min_value=0, value=safe_int(data.get("wind_spd", 0)), step=1, key=f"{key_prefix}_wspd")

        st.caption(f"Wind shown on PDF: **{fmt_wind(wdir, wspd)}** (dir rounded to 10°)")
        with st.expander("Raw METAR/TAF", expanded=False):
            st.write("**METAR**")
            st.code(data.get("metar", "") or "")
            st.write("**TAF**")
            st.code(data.get("taf", "") or "")

        return {"wind_dir": wdir, "wind_spd": wspd}

    dep_met = met_box("Departure", dep_icao, dep_dt, "dep")
    arr_met = met_box("Arrival", arr_icao, arr_dt, "arr")
    alt1_met = met_box("Alternate 1", alt1_icao, arr_dt, "alt1")  # target arrival time by default
    alt2_met = met_box("Alternate 2", alt2_icao, arr_dt, "alt2")  # target arrival time by default

    st.session_state["_legs"] = {
        "dep": {"icao": dep_icao, "met": dep_met},
        "arr": {"icao": arr_icao, "met": arr_met},
        "alt1": {"icao": alt1_icao, "met": alt1_met},
        "alt2": {"icao": alt2_icao, "met": alt2_met},
    }

# ============================================================
# 3) Weight & Balance
# ============================================================
with tabs[2]:
    st.markdown("### Weight & Balance (lbs / inches / in-lbs)")

    reg = st.session_state["_meta"]["reg"]
    ac = fleet.get(reg, {}) if isinstance(fleet, dict) else {}

    ew_lb = safe_float(ac.get("empty_weight_lb", 0.0))
    ew_arm = safe_float(ac.get("empty_arm_in", 0.0))
    # If gist stores moment, use it; else compute
    ew_moment = safe_float(ac.get("empty_moment_inlb", ew_lb * ew_arm))

    arms = st.session_state["_meta"]["arms"]

    c1, c2 = st.columns([0.45, 0.55])

    with c1:
        st.markdown("#### Aircraft (from Gist)")
        st.number_input("Empty Weight (lb)", value=float(ew_lb), step=1.0, disabled=True)
        st.number_input("Empty Arm (in)", value=float(ew_arm), step=0.1, disabled=True)
        st.number_input("Empty Moment (in-lb)", value=float(ew_moment), step=1.0, disabled=True)

        st.markdown("#### Loads")
        front_lb = st.number_input("Pilot + Front pax (lb)", min_value=0.0, value=340.0, step=5.0)
        rear_lb = st.number_input("Rear pax (lb)", min_value=0.0, value=0.0, step=5.0)
        fuel_gal = st.number_input("Fuel (gal)", min_value=0.0, max_value=float(MAX_FUEL_GAL), value=40.0, step=1.0)
        bag_lb = st.number_input("Baggage (lb)", min_value=0.0, max_value=float(MAX_BAGGAGE_LB), value=40.0, step=5.0)

        st.caption(f"Fuel density assumed: {FUEL_LB_PER_GAL:.1f} lb/gal (Avgas).")

    fuel_lb = fuel_gal * FUEL_LB_PER_GAL

    # Moments
    m_front = moment_inlbs(front_lb, arms["front"], rounding=1)
    m_rear = moment_inlbs(rear_lb, arms["rear"], rounding=1)
    m_fuel = moment_inlbs(fuel_lb, arms["fuel"], rounding=1)
    m_bag = moment_inlbs(bag_lb, arms["baggage"], rounding=1)

    # Ramp totals
    ramp_w = ew_lb + front_lb + rear_lb + fuel_lb + bag_lb
    ramp_m = ew_moment + m_front + m_rear + m_fuel + m_bag
    ramp_cg = (ramp_m / ramp_w) if ramp_w > 0 else 0.0

    # Taxi allowance (fixed line)
    taxi_w = DEFAULT_TAXI_FUEL_ALLOW_LB
    taxi_m = moment_inlbs(taxi_w, arms["taxi_fuel"], rounding=10)  # mimic sheet rounding

    # Takeoff
    to_w = ramp_w + taxi_w
    to_m = ramp_m + taxi_m
    to_cg = (to_m / to_w) if to_w > 0 else 0.0

    with c2:
        st.markdown("#### Computed rows (match sheet)")
        st.write(f"**Fuel weight:** {fuel_lb:.0f} lb")

        st.markdown(
            f"""
- **Ramp weight:** {ramp_w:.0f} lb  | **Ramp CG:** {ramp_cg:.1f} in  | **Ramp moment:** {ramp_m:.0f}
- **Taxi allowance:** {taxi_w:.0f} lb @ {arms['taxi_fuel']:.1f} in  | **Moment:** {taxi_m:.0f}
- **Takeoff weight:** {to_w:.0f} lb | **Takeoff CG:** {to_cg:.1f} in | **Takeoff moment:** {to_m:.0f}
"""
        )

        # simple limit checks
        if bag_lb > MAX_BAGGAGE_LB:
            st.error(f"Baggage over limit: {bag_lb:.0f} > {MAX_BAGGAGE_LB} lb")
        if fuel_gal > MAX_FUEL_GAL:
            st.error(f"Fuel over limit: {fuel_gal:.0f} > {MAX_FUEL_GAL} gal")
        if to_w > MTOW_LB:
            st.error(f"MTOW exceeded: {to_w:.0f} > {MTOW_LB} lb")

    st.session_state["_wb"] = {
        "ew_lb": ew_lb, "ew_arm": ew_arm, "ew_moment": ew_moment,
        "front_lb": front_lb, "rear_lb": rear_lb, "fuel_gal": fuel_gal, "fuel_lb": fuel_lb, "bag_lb": bag_lb,
        "m_front": m_front, "m_rear": m_rear, "m_fuel": m_fuel, "m_bag": m_bag,
        "ramp_w": ramp_w, "ramp_m": ramp_m, "ramp_cg": ramp_cg,
        "taxi_w": taxi_w, "taxi_m": taxi_m,
        "to_w": to_w, "to_m": to_m, "to_cg": to_cg,
    }

# ============================================================
# 4) Fuel & Landing
# ============================================================
with tabs[3]:
    st.markdown("### Fuel planning (simple) + Landing point for CG chart")

    wb = st.session_state.get("_wb", {})
    arms = st.session_state["_meta"]["arms"]

    c1, c2 = st.columns([0.45, 0.55])
    with c1:
        gph = st.number_input("Fuel flow (GPH)", min_value=5.0, max_value=20.0, value=10.0, step=0.5)
        taxi_min = st.number_input("Taxi (min)", min_value=0, value=15, step=1)
        trip_min = st.number_input("Trip (min)", min_value=0, value=60, step=5)
        alt_min = st.number_input("Alternate (min)", min_value=0, value=45, step=5)
        reserve_min = st.number_input("Reserve (min)", min_value=0, value=45, step=5)

    def gal_from_min(mins: int) -> float:
        return float(gph) * (float(mins) / 60.0)

    taxi_gal = gal_from_min(taxi_min)
    trip_gal = gal_from_min(trip_min)
    alt_gal = gal_from_min(alt_min)
    reserve_gal = gal_from_min(reserve_min)

    req_gal = taxi_gal + trip_gal + alt_gal + reserve_gal
    req_lb = req_gal * FUEL_LB_PER_GAL

    # landing (burn trip fuel only for landing point)
    burn_lb = trip_gal * FUEL_LB_PER_GAL
    to_w = wb.get("to_w", 0.0)
    to_m = wb.get("to_m", 0.0)

    ldg_w = max(0.0, to_w - burn_lb)
    ldg_m = to_m - moment_inlbs(burn_lb, arms["fuel"], rounding=1)
    ldg_cg = (ldg_m / ldg_w) if ldg_w > 0 else 0.0

    with c2:
        st.markdown("#### Results")
        st.write(f"**Required fuel:** {req_gal:.1f} gal ({req_lb:.0f} lb)")
        st.write(f"**Takeoff fuel loaded:** {wb.get('fuel_gal',0):.1f} gal")
        if wb.get("fuel_gal", 0.0) < req_gal:
            st.error("Fuel loaded is LESS than required by this simple policy.")
        st.markdown("#### Landing point (for chart)")
        st.write(f"Landing weight: **{ldg_w:.0f} lb**")
        st.write(f"Landing CG: **{ldg_cg:.1f} in**")

    st.session_state["_fuel"] = {
        "gph": gph,
        "taxi_min": taxi_min,
        "trip_min": trip_min,
        "alt_min": alt_min,
        "reserve_min": reserve_min,
        "taxi_gal": taxi_gal,
        "trip_gal": trip_gal,
        "alt_gal": alt_gal,
        "reserve_gal": reserve_gal,
        "req_gal": req_gal,
        "ldg_w": ldg_w,
        "ldg_m": ldg_m,
        "ldg_cg": ldg_cg,
    }

# ============================================================
# 5) PDF
# ============================================================
with tabs[4]:
    st.markdown("### Generate filled PDF")

    meta = st.session_state.get("_meta", {})
    legs = st.session_state.get("_legs", {})
    wb = st.session_state.get("_wb", {})
    fuel = st.session_state.get("_fuel", {})

    # Some generic placeholders (you can wire performance later)
    st.markdown("#### Performance inputs (manual for now)")
    pcols = st.columns(4)
    with pcols[0]:
        toda = st.number_input("TODA (m)", min_value=0, value=1500, step=10)
    with pcols[1]:
        todr = st.number_input("TODR (m)", min_value=0, value=600, step=10)
    with pcols[2]:
        lda = st.number_input("LDA (m)", min_value=0, value=1400, step=10)
    with pcols[3]:
        ldr = st.number_input("LDR (m)", min_value=0, value=650, step=10)

    roc = st.number_input("ROC (ft/min)", min_value=0, value=700, step=10)

    if st.button("Generate PDF", type="primary"):
        # --- Fields (exact names, no fallbacks)
        date_str = meta.get("date_str", dt.datetime.utcnow().strftime("%d/%m/%Y"))
        reg = meta.get("reg", "")

        # page 0 fields
        fields = {
            "Weight_EMPTY": f"{safe_int(wb.get('ew_lb',0))}",
            "Datum_EMPTY": f"{safe_float(wb.get('ew_arm',0)):.1f}",
            "Moment_EMPTY": f"{safe_int(wb.get('ew_moment',0))}",

            "Weight_FRONT": f"{safe_int(wb.get('front_lb',0))}",
            "Moment_FRONT": f"{safe_int(wb.get('m_front',0))}",

            "Weight_REAR": f"{safe_int(wb.get('rear_lb',0))}",
            "Moment_REAR": f"{safe_int(wb.get('m_rear',0))}",

            "Weight_FUEL": f"{safe_int(wb.get('fuel_lb',0))}",
            "Moment_FUEL": f"{safe_int(wb.get('m_fuel',0))}",

            "Weight_BAGGAGE": f"{safe_int(wb.get('bag_lb',0))}",
            "Moment_BAGGAGE": f"{safe_int(wb.get('m_bag',0))}",

            "Weight_RAMP": f"{safe_int(wb.get('ramp_w',0))}",
            "Datum_RAMP": f"{safe_float(wb.get('ramp_cg',0)):.1f}",
            "Moment_RAMP": f"{safe_int(wb.get('ramp_m',0))}",

            "Weight_TAKEOFF": f"{safe_int(wb.get('to_w',0))}",
            "Datum_TAKEOFF": f"{safe_float(wb.get('to_cg',0)):.1f}",
            "Moment_TAKEOFF": f"{safe_int(wb.get('to_m',0))}",

            "MTOW": f"{MTOW_LB}",
            "MLW": f"{MLW_LB}",
        }

        # page 1 fields
        fields.update({
            "Date": date_str,
            "Aircraft_Reg": reg,

            "Airfield_DEPARTURE": legs.get("dep", {}).get("icao", ""),
            "Airfield_ARRIVAL": legs.get("arr", {}).get("icao", ""),
            "Airfield_ALTERNATE_1": legs.get("alt1", {}).get("icao", ""),
            "Airfield_ALTERNATE_2": legs.get("alt2", {}).get("icao", ""),

            "Wind_DEPARTURE": fmt_wind(legs.get("dep", {}).get("met", {}).get("wind_dir"), legs.get("dep", {}).get("met", {}).get("wind_spd")),
            "Wind_ARRIVAL": fmt_wind(legs.get("arr", {}).get("met", {}).get("wind_dir"), legs.get("arr", {}).get("met", {}).get("wind_spd")),
            "Wind_ALTERNATE_1": fmt_wind(legs.get("alt1", {}).get("met", {}).get("wind_dir"), legs.get("alt1", {}).get("met", {}).get("wind_spd")),
            "Wind_ALTERNATE_2": fmt_wind(legs.get("alt2", {}).get("met", {}).get("wind_dir"), legs.get("alt2", {}).get("met", {}).get("wind_spd")),
        })

        # Fill the rest with blanks or quick placeholders (you can wire real calcs later)
        for suf in ["DEPARTURE", "ARRIVAL", "ALTERNATE_1", "ALTERNATE_2"]:
            fields[f"RWY_QFU_{suf}"] = ""
            fields[f"Elevation_{suf}"] = ""
            fields[f"QNH_{suf}"] = ""
            fields[f"Temperature_{suf}"] = ""
            if suf == "DEPARTURE":
                fields["Pressure_Alt _DEPARTURE"] = ""
            else:
                fields[f"Pressure_Alt_{suf}"] = ""
            fields[f"Density_Alt_{suf}"] = ""

            # performance (same numbers to keep it simple for now)
            fields[f"TODA_{suf}"] = f"{int(toda)}"
            fields[f"TODR_{suf}"] = f"{int(todr)}"
            fields[f"LDA_{suf}"] = f"{int(lda)}"
            fields[f"LDR_{suf}"] = f"{int(ldr)}"
            fields[f"ROC_{suf}"] = f"{int(roc)}"

        # Fuel planning (sheet wants TIME/FUEL strings)
        def hm(mins: int) -> str:
            mins = int(max(0, mins))
            h, m = divmod(mins, 60)
            if h == 0:
                return f"{m}min"
            return f"{h}h{m:02d}min" if m else f"{h}h"

        # Convert GPH blocks to "gal" strings (sheet is generic; you can change to L later if quiseres)
        fields.update({
            "Start-up_and_Taxi_TIME": hm(fuel.get("taxi_min", 0)),
            "Start-up_and_Taxi_FUEL": f"{fuel.get('taxi_gal',0):.1f} gal",

            "CLIMB_TIME": "—",
            "CLIMB_FUEL": "—",

            "ENROUTE_TIME": hm(fuel.get("trip_min", 0)),
            "ENROUTE_FUEL": f"{fuel.get('trip_gal',0):.1f} gal",

            "DESCENT_TIME": "—",
            "DESCENT_FUEL": "—",

            "TRIP_TIME": hm(fuel.get("trip_min", 0)),
            "TRIP_FUEL": f"{fuel.get('trip_gal',0):.1f} gal",

            "Contingency_TIME": "—",
            "Contingency_FUEL": "—",

            "ALTERNATE_TIME": hm(fuel.get("alt_min", 0)),
            "ALTERNATE_FUEL": f"{fuel.get('alt_gal',0):.1f} gal",

            "RESERVE_TIME": hm(fuel.get("reserve_min", 45)),
            "RESERVE_FUEL": f"{fuel.get('reserve_gal',0):.1f} gal",

            "REQUIRED_TIME": "—",
            "REQUIRED_FUEL": f"{fuel.get('req_gal',0):.1f} gal",

            "EXTRA_TIME": "—",
            "EXTRA_FUEL": "—",

            "Total_TIME": "—",
            "Total_FUEL": f"{fuel.get('req_gal',0):.1f} gal",
        })

        # Fill base PDF
        template_bytes = open(PDF_TEMPLATE, "rb").read()
        filled = fill_pdf(template_bytes, fields)

        # Overlay points on CG chart (page 0)
        reader = PdfReader(io.BytesIO(template_bytes))
        page0 = reader.pages[GRAPH_PAGE_INDEX]
        page_w = float(page0.mediabox.width)
        page_h = float(page0.mediabox.height)

        empty_point = {"label": "Empty", "cg": safe_float(wb.get("ew_arm",0)), "wt": safe_float(wb.get("ew_lb",0)), "rgb": (0.10, 0.60, 0.10)}
        takeoff_point = {"label": "Takeoff", "cg": safe_float(wb.get("to_cg",0)), "wt": safe_float(wb.get("to_w",0)), "rgb": (0.10, 0.30, 0.85)}
        landing_point = {"label": "Landing", "cg": safe_float(fuel.get("ldg_cg",0)), "wt": safe_float(fuel.get("ldg_w",0)), "rgb": (0.85, 0.20, 0.20)}

        overlay = make_overlay_pdf(page_w, page_h, [empty_point, takeoff_point, landing_point], legend_xy=(500, 320), marker_r=4)
        final_pdf = merge_overlay_on_page(filled, GRAPH_PAGE_INDEX, overlay)

        filename = f"{reg}_PA28_MB_Perf.pdf" if reg else "PA28_MB_Perf.pdf"
        st.download_button("Download PDF", data=final_pdf, file_name=filename, mime="application/pdf")
        st.success("PDF generated. Review before flight.")

