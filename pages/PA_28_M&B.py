# PA_28_M&B.py
# Piper PA-28 — Mass & Balance + Forecast + PDF
# Fixes:
# - Runway selectbox ValueError when ICAO changes
# - Arms removed from UI (fixed constants)
# - Clean int/float usage (no MixedNumericTypes)

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
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)

# -----------------------------
# Aircraft constants (fixed)
# -----------------------------
PA28 = {
    "name": "Piper PA-28 Archer III",
    "mtow_lb": 2550.0,
    "max_fuel_usg": 48.0,
    "fuel_density_lb_per_usg": 6.0,
    "taxi_allowance_lb": -8.0,
    "taxi_allowance_moment_inlb": -760.0,

    # FIXED arms from the sheet (no UI)
    "arm_front_in": 80.5,
    "arm_rear_in": 118.1,
    "arm_fuel_in": 95.0,
    "arm_baggage_in": 142.8,
}

# -----------------------------
# Approved airfields DB
# -----------------------------
AERODROMES_DB = {
    "LPCB": {"name": "Castelo Branco", "lat": 39.8483, "lon": -7.4417, "elev_ft": 1251.0, "runways": [{"id": "16", "qfu": 160.0, "toda": 1460.0, "lda": 1460.0}, {"id": "34", "qfu": 340.0, "toda": 1460.0, "lda": 1460.0}]},
    "LPEV": {"name": "Évora", "lat": 38.5297, "lon": -7.8919, "elev_ft": 807.0, "runways": [{"id": "01", "qfu": 10.0, "toda": 1300.0, "lda": 1300.0}, {"id": "19", "qfu": 190.0, "toda": 1300.0, "lda": 1300.0}, {"id": "07", "qfu": 70.0, "toda": 1300.0, "lda": 1300.0}, {"id": "25", "qfu": 250.0, "toda": 1300.0, "lda": 1300.0}]},
    "LPSO": {"name": "Ponte de Sôr", "lat": 39.2117, "lon": -8.0578, "elev_ft": 390.0, "runways": [{"id": "03", "qfu": 30.0, "toda": 1800.0, "lda": 1800.0}, {"id": "21", "qfu": 210.0, "toda": 1800.0, "lda": 1800.0}]},
    "LPFR": {"name": "Faro", "lat": 37.0144, "lon": -7.9658, "elev_ft": 24.0, "runways": [{"id": "10", "qfu": 100.0, "toda": 2490.0, "lda": 2490.0}, {"id": "28", "qfu": 280.0, "toda": 2490.0, "lda": 2490.0}]},
    "LPCS": {"name": "Cascais", "lat": 38.7256, "lon": -9.3553, "elev_ft": 326.0, "runways": [{"id": "17", "qfu": 170.0, "toda": 1400.0, "lda": 1400.0}, {"id": "35", "qfu": 350.0, "toda": 1400.0, "lda": 1400.0}]},
    # ... (keep the rest of your Tecnam list here; I truncated to keep this readable)
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
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,pressure_msl",
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
    return (r.json().get("hourly", {}) or {})

def om_hours(hourly):
    times = hourly.get("time", []) or []
    out = []
    for i, t in enumerate(times):
        out.append((i, dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)))
    return out

def om_at(hourly, idx):
    def getv(key):
        arr = hourly.get(key, []) or []
        return arr[idx] if idx is not None and idx < len(arr) else None
    temp = getv("temperature_2m")
    wspd = getv("wind_speed_10m")
    wdir = getv("wind_direction_10m")
    qnh  = getv("pressure_msl")
    if None in (temp, wspd, wdir, qnh):
        return None
    wdir10 = round_wind_dir_to_10(wdir)
    wspd_i = int(round(float(wspd)))
    return {"temp_c": int(round(float(temp))), "qnh_hpa": int(round(float(qnh))), "wind_dir": int(wdir10), "wind_kt": int(wspd_i)}

# -----------------------------
# GitHub Gist — PA28 fleet
# -----------------------------
GIST_FILE_PA28 = "sevenair_pa28_fleet.json"

def gist_headers(token: str):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
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
# Runway selection by wind
# -----------------------------
def best_runway_id(ad: dict, wind_from_deg: int) -> str:
    rwys = ad.get("runways", []) or []
    if not rwys:
        return ""
    w = int(wind_from_deg or 0)
    w = 360 if w == 0 else w
    diffs = [ang_diff(float(r.get("qfu", 0.0)), float(w)) for r in rwys]
    return rwys[int(diffs.index(min(diffs)))].get("id", "")

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

    if not st.session_state.get("_pa28_defaults_forced", False):
        st.session_state["legs4"] = [dict(x) for x in DEFAULT_LEGS]
        # clear runway keys (avoid stale runway ids from old ICAO)
        for i in range(4):
            st.session_state.pop(f"rw_{i}", None)
        st.session_state["_pa28_defaults_forced"] = True
    else:
        if "legs4" not in st.session_state or len(st.session_state["legs4"]) != 4:
            st.session_state["legs4"] = [dict(x) for x in DEFAULT_LEGS]

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
        st.session_state.flight_date = st.date_input("Flight date (Europe/Lisbon)", value=st.session_state.flight_date)
    with c2:
        st.session_state.dep_time_utc = st.time_input("Departure time (UTC)", value=st.session_state.dep_time_utc, step=3600)
    with c3:
        st.session_state.arr_time_utc = st.time_input("Arrival time (UTC)", value=st.session_state.arr_time_utc, step=3600)

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.subheader("Aircraft")

    regs = sorted(list((st.session_state.fleet_pa28 or {}).keys()))
    if not regs:
        st.error("No registrations found from the PA-28 gist.")
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

# -----------------------------
# AIRFIELDS & FORECAST TAB
# -----------------------------
with tab_air:
    st.subheader("Approved Airfields (DEP / ARR / ALT1 / ALT2) + Forecast (Open-Meteo)")

    icao_options = sorted(AERODROMES_DB.keys())

    def leg_target_dt_utc(role: str) -> dt.datetime:
        if role == "Departure":
            return dt.datetime.combine(st.session_state.flight_date, st.session_state.dep_time_utc).replace(tzinfo=dt.timezone.utc)
        if role == "Arrival":
            return dt.datetime.combine(st.session_state.flight_date, st.session_state.arr_time_utc).replace(tzinfo=dt.timezone.utc)
        # alternates = ARR + 1h
        base = dt.datetime.combine(st.session_state.flight_date, st.session_state.arr_time_utc).replace(tzinfo=dt.timezone.utc)
        return base + dt.timedelta(hours=1)

    for i, leg in enumerate(st.session_state.legs4):
        role = leg["role"]
        target = leg_target_dt_utc(role)

        st.markdown(f"### {role}")
        left, mid, right = st.columns([0.30, 0.18, 0.52])

        with left:
            icao = st.selectbox("ICAO", options=icao_options,
                                index=icao_options.index(leg["icao"]) if leg["icao"] in icao_options else 0,
                                key=f"icao_{i}")
            st.session_state.legs4[i]["icao"] = icao
            ad = AERODROMES_DB[icao]
            st.caption(f"{ad['name']} — Elev {ad['elev_ft']:.0f} ft")

        with mid:
            st.write("**Time used (UTC)**")
            st.code(target.strftime("%Y-%m-%d %H:00Z"))
            st.checkbox("Manual MET", key=f"manual_{i}")

        with right:
            ad = AERODROMES_DB[st.session_state.legs4[i]["icao"]]

            # int widget keys
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

            st.session_state.met4[i].update({"temp": temp_c, "qnh": qnh, "wind_dir": wdir10, "wind_kt": wspd})

            st.markdown(
                f"<span class='pill'>Wind {fmt_wind(wdir10, wspd)}</span>"
                f"<span class='pill'>Temp {temp_c}°C</span>"
                f"<span class='pill'>QNH {qnh}</span>",
                unsafe_allow_html=True
            )

            # Runway auto-selection + SAFE index handling
            rw_ids = [r["id"] for r in ad["runways"]]
            auto_id = best_runway_id(ad, wdir10)
            if not auto_id and rw_ids:
                auto_id = rw_ids[0]

            # If stored runway invalid for this ICAO, replace with auto
            stored = st.session_state.get(f"rw_{i}", None)
            if stored not in rw_ids:
                st.session_state[f"rw_{i}"] = auto_id

            rw_id = st.selectbox(
                "Runway (auto by wind)",
                rw_ids,
                index=rw_ids.index(st.session_state[f"rw_{i}"]),
                key=f"rw_{i}",
            )
            rw = next(r for r in ad["runways"] if r["id"] == rw_id)

            st.session_state[f"qfu_{i}"] = float(rw.get("qfu", 0.0))
            st.session_state.setdefault(f"toda_{i}", float(rw["toda"]))
            st.session_state.setdefault(f"lda_{i}", float(rw["lda"]))

            st.number_input("TODA (m)", min_value=0.0, value=float(st.session_state[f"toda_{i}"]), step=1.0, key=f"toda_{i}")
            st.number_input("LDA (m)",  min_value=0.0, value=float(st.session_state[f"lda_{i}"]),  step=1.0, key=f"lda_{i}")

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

    col1, col2 = st.columns([0.55, 0.45])

    with col1:
        st.markdown("#### Loads")
        front_lb = st.number_input("Pilot + front passenger (lb)", min_value=0.0, value=170.0, step=1.0)
        rear_lb  = st.number_input("Rear seats (lb)", min_value=0.0, value=0.0, step=1.0)
        bag_lb   = st.number_input("Baggage (lb)", min_value=0.0, value=0.0, step=1.0)
        fuel_usg = st.number_input("Fuel (USG)", min_value=0.0, max_value=float(PA28["max_fuel_usg"]), value=0.0, step=0.5)
        st.caption(f"Fuel: {fuel_usg:.1f} USG ({usg_to_l(fuel_usg):.1f} L)")

    with col2:
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

    # W&B computations (arms fixed)
    fuel_lb = fuel_usg * PA28["fuel_density_lb_per_usg"]

    m_front = front_lb * PA28["arm_front_in"]
    m_rear  = rear_lb * PA28["arm_rear_in"]
    m_bag   = bag_lb * PA28["arm_baggage_in"]
    m_fuel  = fuel_lb * PA28["arm_fuel_in"]

    ramp_w = ew_lb + front_lb + rear_lb + bag_lb + fuel_lb
    ramp_m = em_inlb + m_front + m_rear + m_bag + m_fuel
    ramp_cg = ramp_m / ramp_w

    takeoff_w = ramp_w + PA28["taxi_allowance_lb"]
    takeoff_m = ramp_m + PA28["taxi_allowance_moment_inlb"]
    takeoff_cg = takeoff_m / takeoff_w

    trip_burn_lb = trip_usg * PA28["fuel_density_lb_per_usg"]
    landing_w = takeoff_w - trip_burn_lb
    landing_m = takeoff_m - (trip_burn_lb * PA28["arm_fuel_in"])
    landing_cg = landing_m / landing_w

    st.markdown("<hr/>", unsafe_allow_html=True)
    a, b, c = st.columns(3)
    with a:
        st.write("**Takeoff**")
        st.write(f"{takeoff_w:.0f} lb ({lbs_to_kg(takeoff_w):.0f} kg)")
        st.write(f"CG {takeoff_cg:.2f} in")
    with b:
        st.write("**Landing**")
        st.write(f"{landing_w:.0f} lb ({lbs_to_kg(landing_w):.0f} kg)")
        st.write(f"CG {landing_cg:.2f} in")
    with c:
        st.write("**Limits**")
        st.write(f"MTOW {PA28['mtow_lb']:.0f} lb " + ("✅" if takeoff_w <= PA28["mtow_lb"] else "⚠️"))

    st.session_state["_wb_pa28"] = {
        "ew_lb": ew_lb, "em_inlb": em_inlb,
        "front_lb": front_lb, "rear_lb": rear_lb, "bag_lb": bag_lb,
        "fuel_usg": fuel_usg, "fuel_lb": fuel_lb,
        "ramp_w": ramp_w, "ramp_m": ramp_m, "ramp_cg": ramp_cg,
        "takeoff_w": takeoff_w, "takeoff_m": takeoff_m, "takeoff_cg": takeoff_cg,
        "landing_w": landing_w, "landing_m": landing_m, "landing_cg": landing_cg,
        "fuel_plan": {"taxi_min": taxi_min, "trip_min": trip_min, "trip_usg": trip_usg, "cont_min": cont_min, "cont_usg": cont_usg, "alt_min": alt_min, "alt_usg": alt_usg, "res_min": reserve_min, "res_usg": res_usg, "req_usg": req_usg, "extra_usg": extra_usg},
        "fuel_flow_gph": GPH,
    }

# PDF tab intentionally omitted in this snippet because your last message was about UI/runway/errors.
# You can paste the previous PDF tab (it will work unchanged with these fixes).

