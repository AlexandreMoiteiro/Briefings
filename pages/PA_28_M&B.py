# Streamlit app – Piper PA28 Archer III (Sevenair) – M&B + Weather + PDF fill + CG chart
# Fixes included (requested):
# - MTOW/MLW constants kept at 2550/2550 (POH), and filled into PDF (tries common field-name variants)
# - 182 L displays as 48.0 USG (force when FULL, avoids 48.1)
# - Consumption default corresponds to 10 USG/h (≈ 37.9 L/h)
# - Start-up_and_Taxi_FUEL no longer zero (taxi_l stored in session_state["_fuel"])
# - Summary shows Empty + Takeoff + Landing
# - Removed "Debug: show detected field names"
# - Alternate default time = 60 min
# - PDF NeedAppearances uses BooleanObject(True)
# - Dark mode boxes readable
# - W&B input limits: Fuel max 182.0 L, Baggage max 90.0 kg, Fuel default FULL, Student 50 / Instructor 80 / Baggage 5
# - Weather fetch: DEP uses dep_time_utc; ARR uses arr_time_utc; ALT1/ALT2 use arr_time_utc + 1 hour
# - Wind dir rounded to nearest 10°, vector-mean wind around target hour (idx±1)
# - Aerodromes DB built live from OurAirports + overrides for LPSO/LPEV
# - Auto runway by wind (max headwind then min crosswind)
# - PDF fields: supports 4-column sheet naming (DEPARTURE / ARRIVAL / ALTERNATE_1 / ALTERNATE_2)
# - CG chart: plots ONLY Empty / Takeoff / Landing (no Ramp)

import io
import csv
import json
import unicodedata
import datetime as dt
from math import cos, sin, radians, sqrt, atan2, degrees

import pytz
import requests
import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, BooleanObject

# reportlab for chart overlay
from reportlab.pdfgen import canvas

# -----------------------------
# App setup
# -----------------------------
st.set_page_config(
    page_title="PA28 – M&B + Weather + PDF",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1200px !important; }
      .hdr{font-size:1.25rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:8px;margin:2px 0 14px}
      .chip{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef2f7;margin-left:8px;font-size:.85rem}
      .ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
      .muted{color:#6b7280;font-size:.9rem}
      .box{background:#f8fafc;border:1px solid #e5e7ec;border-radius:12px;padding:12px;border-radius:12px}
      .tbl{border-collapse:collapse;width:100%}
      .tbl th{border-bottom:2px solid #cbd0d6;text-align:left;padding:6px}
      .tbl td{border-bottom:1px dashed #e5e7ec;padding:6px}

      /* Dark mode readability */
      @media (prefers-color-scheme: dark) {
        .hdr{border-bottom:1px solid #374151;}
        .muted{color:#9ca3af;}
        .box{background:#0b1220;border:1px solid #243044;color:#e5e7eb;}
        .chip{background:#111b2b;color:#e5e7eb;}
        .tbl th{border-bottom:2px solid #374151;color:#e5e7eb;}
        .tbl td{border-bottom:1px dashed #374151;color:#e5e7eb;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# Helpers
# -----------------------------
def ascii_safe(text):
    if not isinstance(text, str):
        return str(text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def fmt_hm(total_min: int) -> str:
    if total_min is None or total_min <= 0:
        return "0min"
    h, m = divmod(int(round(total_min)), 60)
    if h == 0:
        return f"{m}min"
    return f"{h}h" if m == 0 else f"{h}h{m:02d}min"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


KG_TO_LB = 2.2046226218
L_TO_USG = 1.0 / 3.785411784
USG_TO_L = 3.785411784

# Fuel density (approx for 100LL): 6.0 lb/USG
FUEL_LB_PER_USG = 6.0

# Sheet maxima (as you wanted)
FUEL_USABLE_USG = 48.0
FUEL_USABLE_L = 182.0          # force sheet value (not 181.70…)
BAGGAGE_MAX_KG = 90.0          # you asked 90 (not 90.7)
BAGGAGE_MAX_LB = BAGGAGE_MAX_KG * KG_TO_LB

# PA28 arms (inches aft of datum) – fixed for this sheet
ARM_FRONT = 80.5
ARM_REAR = 118.1
ARM_FUEL = 95.0
ARM_BAGGAGE = 142.8

# Taxi/runup allowance (sheet shows -8 lb @ 95.5)
TAXI_ALLOW_LB = 8.0
TAXI_ARM = 95.5

# Max weights (from POH and your sheet)
MTOW_LB = 2550.0
MLW_LB = 2550.0

# PDF template
PDF_TEMPLATE_PATHS = ["RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"]

# -----------------------------
# Aerodromes DB (live from OurAirports) + overrides you required
# -----------------------------
OURAIRPORTS_AIRPORTS_CSV = "https://ourairports.com/data/airports.csv"
OURAIRPORTS_RUNWAYS_CSV = "https://ourairports.com/data/runways.csv"

ICAO_SET = sorted({
    "LEBZ","LPBR","LPBG","LPCB","LPCO","LPEV","LEMG","LPSO","LEZL","LEVX","LPVR","LPVZ","LPCS","LPMT","LPST","LPBJ","LPFR","LPPM","LPPR"
})

def _to_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def _ft_to_m(ft):
    return float(ft) * 0.3048

def _rw_ident_to_qfu_deg(ident: str):
    """
    Best-effort heading from runway designator:
      "21" -> 210
      "03" -> 30
      "16L" -> 160
      "36" -> 360
    """
    if not ident:
        return None
    ident = ident.strip().upper()
    if len(ident) < 1:
        return None
    digits = ""
    for ch in ident:
        if ch.isdigit():
            digits += ch
            if len(digits) == 2:
                break
        else:
            break
    if not digits:
        return None
    n = int(digits)
    if n == 0:
        return None
    q = (n * 10) % 360
    return 360.0 if q == 0 else float(q)

@st.cache_data(ttl=7*24*3600, show_spinner=False)
def load_ourairports_csvs():
    def fetch_csv(url):
        r = requests.get(url, timeout=40)
        r.raise_for_status()
        txt = r.content.decode("utf-8", errors="replace")
        return list(csv.DictReader(io.StringIO(txt)))
    return fetch_csv(OURAIRPORTS_AIRPORTS_CSV), fetch_csv(OURAIRPORTS_RUNWAYS_CSV)

def build_aerodromes_db(icaos):
    airports_rows, runways_rows = load_ourairports_csvs()

    a_by_ident = {a.get("ident"): a for a in airports_rows if a.get("ident")}
    r_by_ident = {}
    for r in runways_rows:
        ident = r.get("airport_ident")
        if ident:
            r_by_ident.setdefault(ident, []).append(r)

    db = {}
    for icao in icaos:
        a = a_by_ident.get(icao)
        if not a:
            continue

        name = a.get("name", icao)
        lat = _to_float(a.get("latitude_deg"), 0.0)
        lon = _to_float(a.get("longitude_deg"), 0.0)
        elev_ft = _to_float(a.get("elevation_ft"), 0.0)

        runways = []
        for rw in r_by_ident.get(icao, []):
            length_ft = _to_float(rw.get("length_ft"), None)
            if not length_ft or length_ft <= 0:
                continue
            length_m = float(round(_ft_to_m(length_ft), 0))

            le_ident = (rw.get("le_ident") or "").strip()
            he_ident = (rw.get("he_ident") or "").strip()
            le_hdg = _to_float(rw.get("le_heading_degT"), None)
            he_hdg = _to_float(rw.get("he_heading_degT"), None)

            # fallbacks if dataset headings missing
            if le_hdg is None:
                le_hdg = _rw_ident_to_qfu_deg(le_ident)
            if he_hdg is None:
                he_hdg = _rw_ident_to_qfu_deg(he_ident)

            if le_ident and le_hdg is not None:
                runways.append({"id": le_ident, "qfu": float(le_hdg), "toda": length_m, "lda": length_m})
            if he_ident and he_hdg is not None:
                runways.append({"id": he_ident, "qfu": float(he_hdg), "toda": length_m, "lda": length_m})

        db[icao] = {"name": name, "lat": lat, "lon": lon, "elev_ft": elev_ft, "runways": runways}

    # ---- Overrides you explicitly required (exact QFU etc.)
    # LPSO: RWY03/21 headings 026/206, length 1800m
    if "LPSO" in db:
        db["LPSO"]["name"] = "Ponte de Sôr"
        db["LPSO"]["runways"] = [
            {"id": "03", "qfu": 26.0,  "toda": 1800.0, "lda": 1800.0},
            {"id": "21", "qfu": 206.0, "toda": 1800.0, "lda": 1800.0},
        ]

    # LPEV: remove 04/18; keep 01/19 and 07/25 (headings commonly published 006/186 and 074/254)
    if "LPEV" in db:
        db["LPEV"]["name"] = "Évora"
        keep = {"01","19","07","25"}
        filtered = [r for r in db["LPEV"]["runways"] if r["id"] in keep]
        if not filtered:
            filtered = [
                {"id": "01", "qfu": 6.0,   "toda": 1300.0, "lda": 1300.0},
                {"id": "19", "qfu": 186.0, "toda": 1300.0, "lda": 1300.0},
                {"id": "07", "qfu": 74.0,  "toda": 530.0,  "lda": 530.0},
                {"id": "25", "qfu": 254.0, "toda": 530.0,  "lda": 530.0},
            ]
        for r in filtered:
            if r["id"] == "01": r["qfu"] = 6.0
            if r["id"] == "19": r["qfu"] = 186.0
            if r["id"] == "07": r["qfu"] = 74.0
            if r["id"] == "25": r["qfu"] = 254.0
        db["LPEV"]["runways"] = filtered

    return db

AERODROMES_DB = build_aerodromes_db(ICAO_SET)
ICAO_OPTIONS = sorted(AERODROMES_DB.keys())

# -----------------------------
# Wind/runway helpers
# -----------------------------
def wind_components(qfu_deg, wind_dir_deg, wind_speed_kt):
    # wind_dir is FROM
    diff = ((wind_dir_deg - qfu_deg + 180) % 360) - 180
    hw = wind_speed_kt * cos(radians(diff))
    cw = wind_speed_kt * sin(radians(diff))
    side = "R" if cw > 0 else ("L" if cw < 0 else "")
    return hw, abs(cw), side

def choose_best_runway_by_wind(ad, wind_dir, wind_kt):
    best = None
    for rw in ad.get("runways", []):
        hw, xw, side = wind_components(rw["qfu"], wind_dir, wind_kt)
        cand = {"rw": rw, "hw": hw, "xw": xw, "side": side}
        if best is None:
            best = cand
            continue
        if (cand["hw"] > best["hw"]) or (abs(cand["hw"] - best["hw"]) < 1e-6 and cand["xw"] < best["xw"]):
            best = cand
    return best

def round_wind_dir_10(d):
    if d is None:
        return 0
    v = int(round(float(d) / 10.0) * 10) % 360
    return 360 if v == 0 else v

# -----------------------------
# Weather (Open-Meteo)
# -----------------------------
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

@st.cache_data(ttl=900, show_spinner=False)
def om_point_forecast(lat, lon, start_date_iso, end_date_iso):
    params = {
        "latitude": round(float(lat), 6),
        "longitude": round(float(lon), 6),
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
        return {"error": f"HTTP {r.status_code}", "detail": r.text}
    data = r.json()
    h = data.get("hourly", {}) or {}
    return {
        "time": h.get("time", []) or [],
        "wspd": h.get("wind_speed_10m", []) or [],
        "wdir": h.get("wind_direction_10m", []) or [],
        "temp": h.get("temperature_2m", []) or [],
        "qnh":  h.get("pressure_msl", []) or [],
    }

def om_hours(resp):
    out = []
    for i, t in enumerate(resp.get("time", []) or []):
        dtu = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        out.append((i, dtu))
    return out

def _u_v_from_dirspd(dir_deg_from, spd_kt):
    spd_ms = float(spd_kt) * 0.514444
    th = radians(float(dir_deg_from))
    u = -spd_ms * sin(th)
    v = -spd_ms * cos(th)
    return u, v

def _dirspd_from_uv(u, v):
    spd_ms = sqrt(u*u + v*v)
    dir_deg = (degrees(atan2(u, v)) + 180.0) % 360.0  # FROM
    spd_kt = spd_ms * 1.94384
    return dir_deg, spd_kt

def om_mean_met_at(resp, idx, window=1):
    if idx is None:
        return None
    wdir = resp.get("wdir", [])
    wspd = resp.get("wspd", [])
    temp = resp.get("temp", [])
    qnh = resp.get("qnh", [])
    if not wdir or not wspd:
        return None

    u_sum = 0.0
    v_sum = 0.0
    n = 0
    for j in range(idx - window, idx + window + 1):
        if 0 <= j < len(wdir) and 0 <= j < len(wspd):
            if wdir[j] is None or wspd[j] is None:
                continue
            u, v = _u_v_from_dirspd(wdir[j], wspd[j])
            u_sum += u
            v_sum += v
            n += 1
    if n == 0:
        return None

    dir_deg, spd_kt = _dirspd_from_uv(u_sum / n, v_sum / n)
    t_val = temp[idx] if idx < len(temp) else None
    q_val = qnh[idx] if idx < len(qnh) else None

    return {
        "wind_dir": round_wind_dir_10(dir_deg),
        "wind_kt": int(round(spd_kt)),
        "temp_c": int(round(float(t_val))) if t_val is not None else 15,
        "qnh_hpa": int(round(float(q_val))) if q_val is not None else 1013,
    }

# -----------------------------
# GitHub Gist (fleet)
# -----------------------------
GIST_FILE = "sevenair_pa28_fleet.json"

def gist_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def gist_load(token, gist_id):
    r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gist_headers(token), timeout=20)
    if r.status_code != 200:
        return None, f"GitHub error {r.status_code}: {r.text}"
    data = r.json()
    files = data.get("files", {}) or {}
    if GIST_FILE not in files or files[GIST_FILE].get("content") is None:
        return None, f"Gist file '{GIST_FILE}' not found."
    return json.loads(files[GIST_FILE]["content"]), None

def parse_ew(reg_entry: dict):
    ew = (
        reg_entry.get("ew_lb")
        or reg_entry.get("ew")
        or reg_entry.get("empty_weight_lb")
        or reg_entry.get("empty_weight")
        or 0.0
    )
    mom = (
        reg_entry.get("ew_moment_inlb")
        or reg_entry.get("ew_moment")
        or reg_entry.get("ewm")
        or reg_entry.get("empty_moment_inlb")
        or reg_entry.get("empty_moment")
        or 0.0
    )
    return float(ew), float(mom)

# -----------------------------
# PDF utils
# -----------------------------
def read_pdf_bytes(paths) -> bytes:
    from pathlib import Path
    for path_str in paths:
        p = Path(path_str)
        if p.exists():
            return p.read_bytes()
    raise FileNotFoundError(f"Template not found: {paths}")

def get_field_names(template_bytes: bytes) -> set:
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
    return names

def fill_pdf(template_bytes: bytes, fields: dict) -> bytes:
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)

    root = reader.trailer["/Root"]
    if "/AcroForm" not in root:
        raise RuntimeError("Template PDF has no AcroForm.")
    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    try:
        writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): BooleanObject(True)})
    except Exception:
        pass

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()

# -----------------------------
# CG chart mapping (page 0) – anchors you provided
# -----------------------------
CG_ANCHORS = {
    82: {"w0": 1200, "x0": 182, "y0": 72, "w1": 2050, "x1": 134, "y1": 245},
    83: {"w0": 1200, "x0": 199, "y0": 72, "w1": 2138, "x1": 155, "y1": 260},
    84: {"w0": 1200, "x0": 213, "y0": 71, "w1": 2200, "x1": 178, "y1": 276},
    85: {"w0": 1200, "x0": 229, "y0": 72, "w1": 2295, "x1": 202, "y1": 294},
    86: {"w0": 1200, "x0": 245, "y0": 72, "w1": 2355, "x1": 228, "y1": 307},
    87: {"w0": 1200, "x0": 262, "y0": 72, "w1": 2440, "x1": 255, "y1": 322},
    88: {"w0": 1200, "x0": 277, "y0": 73, "w1": 2515, "x1": 285, "y1": 338},
    89: {"w0": 1200, "x0": 293, "y0": 73, "w1": 2550, "x1": 315, "y1": 343},
    90: {"w0": 1200, "x0": 308, "y0": 72, "w1": 2550, "x1": 345, "y1": 343},
    91: {"w0": 1200, "x0": 323, "y0": 72, "w1": 2550, "x1": 374, "y1": 343},
    92: {"w0": 1200, "x0": 340, "y0": 73, "w1": 2550, "x1": 404, "y1": 343},
    93: {"w0": 1200, "x0": 355, "y0": 72, "w1": 2550, "x1": 435, "y1": 344},
}

def xy_on_cg_line(cg_int: int, weight_lb: float):
    a = CG_ANCHORS[int(cg_int)]
    w0, x0, y0 = a["w0"], a["x0"], a["y0"]
    w1, x1, y1 = a["w1"], a["x1"], a["y1"]
    w = clamp(weight_lb, min(w0, w1), max(w0, w1))
    if w1 == w0:
        return x0, y0
    t = (w - w0) / (w1 - w0)
    x = x0 + t * (x1 - x0)
    y = y0 + t * (y1 - y0)
    return x, y

def xy_from_cg_weight(cg_in: float, weight_lb: float):
    cg = float(cg_in)
    cg = clamp(cg, 82.0, 93.0)
    lo = int(clamp(int(cg // 1), 82, 93))
    hi = int(clamp(lo + 1, 82, 93))
    if hi == lo:
        return xy_on_cg_line(lo, weight_lb)
    x0, y0 = xy_on_cg_line(lo, weight_lb)
    x1, y1 = xy_on_cg_line(hi, weight_lb)
    frac = (cg - lo) / (hi - lo)
    return (x0 + frac * (x1 - x0), y0 + frac * (y1 - y0))

def draw_cg_overlay_on_page0(template_bytes: bytes, points):
    """
    points: list of dicts:
      {"label":"Empty","cg":..., "w":..., "rgb":(r,g,b)}  (r,g,b in 0..1)
    Draws dot + a line (from 1200lb point on that cg line up to the dot), and legend.
    """
    reader = PdfReader(io.BytesIO(template_bytes))
    page0 = reader.pages[0]
    w_pt = float(page0.mediabox.width)
    h_pt = float(page0.mediabox.height)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w_pt, h_pt))

    DOT_R = 5.5

    for p in points:
        cg = float(p["cg"])
        wlb = float(p["w"])
        r, g, b = p["rgb"]

        x_dot, y_dot = xy_from_cg_weight(cg, wlb)
        x_base, y_base = xy_from_cg_weight(cg, 1200.0)

        c.setStrokeColorRGB(r, g, b)
        c.setLineWidth(1.5)
        c.line(x_base, y_base, x_dot, y_dot)

        c.setFillColorRGB(r, g, b)
        c.circle(x_dot, y_dot, DOT_R, fill=1, stroke=0)

    # legend
    legend_x = 500
    legend_y = 300
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(legend_x, legend_y + 70, "Legend")
    c.setFont("Helvetica", 10)

    items = [
        ("Empty",    (0.10, 0.60, 0.15)),
        ("Takeoff",  (0.10, 0.30, 0.85)),
        ("Landing",  (0.85, 0.15, 0.15)),
    ]
    y = legend_y + 50
    for name, rgb in items:
        r, g, b = rgb
        c.setFillColorRGB(r, g, b)
        c.rect(legend_x, y - 7, 10, 10, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(legend_x + 16, y - 5, name)
        y -= 18

    c.showPage()
    c.save()
    buf.seek(0)

    overlay_pdf = PdfReader(buf)
    overlay_page = overlay_pdf.pages[0]

    out_writer = PdfWriter()
    for i, p in enumerate(reader.pages):
        if i == 0:
            p.merge_page(overlay_page)
        out_writer.add_page(p)

    root = reader.trailer["/Root"]
    if "/AcroForm" in root:
        out_writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
        try:
            out_writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): BooleanObject(True)})
        except Exception:
            pass

    out = io.BytesIO()
    out_writer.write(out)
    return out.getvalue()

# -----------------------------
# Session defaults (4 legs: dep, arr, alt1, alt2)
# -----------------------------
DEFAULT_LEGS = [
    {"role": "DEPARTURE",   "icao": "LPSO"},
    {"role": "ARRIVAL",     "icao": "LPSO"},
    {"role": "ALTERNATE_1", "icao": "LPEV"},
    {"role": "ALTERNATE_2", "icao": "LPCB"},
]

if "legs" not in st.session_state:
    st.session_state.legs = [dict(x) for x in DEFAULT_LEGS]

def sync_with_legs():
    n = len(st.session_state.legs)
    if "met" not in st.session_state or not isinstance(st.session_state.met, list):
        st.session_state.met = [None] * n
    elif len(st.session_state.met) != n:
        old = st.session_state.met
        st.session_state.met = (old + [None] * n)[:n]

sync_with_legs()

if "fleet" not in st.session_state:
    st.session_state.fleet = {}
if "fleet_loaded" not in st.session_state:
    st.session_state.fleet_loaded = False

if "flight_date" not in st.session_state:
    st.session_state.flight_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).date()

# Default times only (user can change; no forced dep+1 logic beyond initial default)
if "dep_time_utc" not in st.session_state:
    st.session_state.dep_time_utc = (dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)).time()
if "arr_time_utc" not in st.session_state:
    st.session_state.arr_time_utc = (dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=2)).time()

# -----------------------------
# Sidebar (fleet load only)
# -----------------------------
with st.sidebar:
    st.subheader("🛩️ Fleet")
    st.caption("Loads EW & EW Moment from GitHub Gist.")
    token = st.secrets.get("GITHUB_GIST_TOKEN", "")
    gist_id = st.secrets.get("GITHUB_GIST_ID_PA28", "")
    if st.button("Load fleet from Gist"):
        if not token or not gist_id:
            st.error("Missing secrets: GITHUB_GIST_TOKEN and/or GITHUB_GIST_ID_PA28")
        else:
            data, err = gist_load(token, gist_id)
            if err:
                st.error(err)
            else:
                st.session_state.fleet = data or {}
                st.session_state.fleet_loaded = True
                st.success(f"Loaded {len(st.session_state.fleet)} aircraft.")

    if not st.session_state.fleet_loaded and token and gist_id:
        data, err = gist_load(token, gist_id)
        if data is not None:
            st.session_state.fleet = data or {}
            st.session_state.fleet_loaded = True

# -----------------------------
# Header + tabs
# -----------------------------
st.markdown('<div class="hdr">Piper PA28 Archer III – M&B + Weather + PDF</div>', unsafe_allow_html=True)
tab1, tab2, tab3, tab4 = st.tabs(["1) Flight", "2) Aerodromes & Weather", "3) Weight & Fuel", "4) PDF"])

# -----------------------------
# 1) Flight
# -----------------------------
with tab1:
    c1, c2, c3 = st.columns([0.40, 0.30, 0.30])
    with c1:
        st.markdown("#### Date & Aircraft")
        st.session_state.flight_date = st.date_input("Flight date (Europe/Lisbon)", value=st.session_state.flight_date)

        regs = sorted(st.session_state.fleet.keys()) if st.session_state.fleet else ["(load fleet in sidebar)"]
        reg = st.selectbox("Aircraft Reg.", regs, index=0)
        st.session_state["reg"] = reg

        if reg in st.session_state.fleet:
            ew_lb, ew_mom = parse_ew(st.session_state.fleet[reg])
            ew_kg = ew_lb / KG_TO_LB
            ew_cg = (ew_mom / ew_lb) if ew_lb > 0 else 0.0
            st.markdown(
                f"<div class='box'><b>Empty Weight</b>: {ew_lb:.0f} lb ({ew_kg:.0f} kg)<br>"
                f"<b>Empty Moment</b>: {ew_mom:.0f} in-lb<br>"
                f"<b>Empty CG</b>: {ew_cg:.1f} in</div>",
                unsafe_allow_html=True
            )
        else:
            st.info("Load fleet from Gist to get EW & moment.")

    with c2:
        st.markdown("#### Times (UTC)")
        st.session_state.dep_time_utc = st.time_input("Departure time (UTC)", value=st.session_state.dep_time_utc, step=3600)
        st.session_state.arr_time_utc = st.time_input("Arrival time (UTC)", value=st.session_state.arr_time_utc, step=3600)
        st.markdown("<div class='muted'>Alternates use Arrival + 1 hour.</div>", unsafe_allow_html=True)

    with c3:
        st.markdown("#### Notes")
        st.session_state["mission_no"] = st.text_input("Mission/Ref (optional)", value=st.session_state.get("mission_no", ""))

# -----------------------------
# 2) Aerodromes & Weather
# -----------------------------
with tab2:
    st.markdown("#### Aerodromes (4 legs) + model weather (vector-mean wind)")

    colA, colB = st.columns([0.62, 0.38])
    with colB:
        if st.button("Fetch weather for all legs", type="primary"):
            date_iso = st.session_state.flight_date.strftime("%Y-%m-%d")

            dep_target = dt.datetime.combine(st.session_state.flight_date, st.session_state.dep_time_utc).replace(tzinfo=dt.timezone.utc)
            arr_target = dt.datetime.combine(st.session_state.flight_date, st.session_state.arr_time_utc).replace(tzinfo=dt.timezone.utc)
            alt_target = arr_target + dt.timedelta(hours=1)

            targets = [dep_target, arr_target, alt_target, alt_target]

            ok, err = 0, 0
            for i, leg in enumerate(st.session_state.legs):
                icao = leg["icao"]
                ad = AERODROMES_DB.get(icao)
                if not ad:
                    st.error(f"{leg['role']} {icao}: aerodrome not in DB")
                    err += 1
                    continue

                resp = om_point_forecast(ad["lat"], ad["lon"], date_iso, date_iso)
                if "error" in resp:
                    st.error(f"{leg['role']} {icao}: weather error: {resp.get('error')} {resp.get('detail','')}")
                    err += 1
                    continue

                hours = om_hours(resp)
                if not hours:
                    st.error(f"{leg['role']} {icao}: no hours in model response")
                    err += 1
                    continue

                target = targets[i]
                idx, tsel = min(hours, key=lambda h: abs(h[1] - target))
                met = om_mean_met_at(resp, idx, window=1)
                if not met:
                    st.error(f"{leg['role']} {icao}: could not compute mean MET")
                    err += 1
                    continue

                met["label"] = tsel.strftime("%Y-%m-%d %H:00Z")
                met["target"] = target.strftime("%Y-%m-%d %H:%MZ")
                st.session_state.met[i] = met
                ok += 1

            if ok and not err:
                st.success(f"Weather updated for all legs ({ok}/4).")
            elif ok:
                st.warning(f"Weather updated for {ok} leg(s); {err} with errors.")
            else:
                st.error("No legs updated.")

    for i, leg in enumerate(st.session_state.legs):
        role = leg["role"]
        c1, c2, c3 = st.columns([0.35, 0.35, 0.30])

        with c1:
            icao = st.selectbox(f"{role} ICAO", ICAO_OPTIONS, index=ICAO_OPTIONS.index(leg["icao"]), key=f"icao_{i}")
            st.session_state.legs[i]["icao"] = icao
            ad = AERODROMES_DB[icao]
            st.caption(f"{ad['name']} · Elev {ad['elev_ft']:.0f} ft")

        with c2:
            met = st.session_state.met[i] or {"wind_dir": 240, "wind_kt": 8, "temp_c": 15, "qnh_hpa": 1013, "label": "", "target": ""}
            st.markdown(
                f"<div class='box'><b>Model</b> {met.get('label','')}<br>"
                f"<span class='muted'>Target: {met.get('target','')}</span><br>"
                f"Wind: <b>{met['wind_dir']:03d}/{met['wind_kt']:02d}</b> kt<br>"
                f"OAT: <b>{met['temp_c']}</b> °C · QNH: <b>{met['qnh_hpa']}</b> hPa</div>",
                unsafe_allow_html=True,
            )

        with c3:
            met = st.session_state.met[i] or {"wind_dir": 240, "wind_kt": 8, "temp_c": 15, "qnh_hpa": 1013, "label": "", "target": ""}
            ad = AERODROMES_DB[st.session_state.legs[i]["icao"]]
            best = choose_best_runway_by_wind(ad, met["wind_dir"], met["wind_kt"])

            if not best:
                st.markdown("<div class='box warn'><b>No runway data for this aerodrome.</b></div>", unsafe_allow_html=True)
                st.session_state[f"rwy_{role}"] = ""
                st.session_state[f"qfu_{role}"] = 0
                st.session_state[f"toda_{role}"] = 0
                st.session_state[f"lda_{role}"] = 0
            else:
                rw = best["rw"]
                st.session_state[f"rwy_{role}"] = rw["id"]
                st.session_state[f"qfu_{role}"] = rw["qfu"]
                st.session_state[f"toda_{role}"] = rw["toda"]
                st.session_state[f"lda_{role}"] = rw["lda"]

                st.markdown(
                    f"<div class='box'><b>Auto RWY</b>: {rw['id']} "
                    f"<span class='chip'>QFU {rw['qfu']:.0f}°</span><br>"
                    f"HW {best['hw']:.0f} kt · XW {best['side']} {best['xw']:.0f} kt<br>"
                    f"TODA {rw['toda']:.0f} m · LDA {rw['lda']:.0f} m</div>",
                    unsafe_allow_html=True,
                )

# -----------------------------
# 3) Weight & Fuel
# -----------------------------
with tab3:
    st.markdown("#### Weight & Balance (inputs in kg / L)")

    reg = st.session_state.get("reg", "")
    fleet_ok = reg in st.session_state.fleet

    c1, c2 = st.columns([0.52, 0.48])

    with c1:
        student_kg = st.number_input("Student (kg)", min_value=0.0, value=50.0, step=0.5)
        instructor_kg = st.number_input("Instructor (kg)", min_value=0.0, value=80.0, step=0.5)
        rear_pax_kg = st.number_input("Rear passengers total (kg)", min_value=0.0, value=0.0, step=0.5)
        baggage_kg = st.number_input("Baggage (kg) — max 90", min_value=0.0, max_value=float(BAGGAGE_MAX_KG), value=5.0, step=0.5)
        fuel_l = st.number_input("Fuel (L) — max 182", min_value=0.0, max_value=float(FUEL_USABLE_L), value=float(FUEL_USABLE_L), step=1.0)

        st.markdown("#### Fuel planning (detailed)")

        # FIX: default matches 10 USG/h = 37.854 L/h
        DEFAULT_USGPH = 10.0
        DEFAULT_LPH = DEFAULT_USGPH * USG_TO_L
        rate_lph = st.number_input("Consumption (L/h)", min_value=10.0, max_value=60.0, value=float(round(DEFAULT_LPH, 1)), step=0.5)
        st.caption("Reference: 10 USG/h ≈ 37.9 L/h")

        taxi_min = st.number_input("(1) Start-up & Taxi (min)", min_value=0, value=15, step=1)
        climb_min = st.number_input("(2) Climb (min)", min_value=0, value=10, step=1)
        enrt_h = st.number_input("(3) Enroute (h)", min_value=0, value=1, step=1)
        enrt_min = st.number_input("(3) Enroute (min)", min_value=0, value=0, step=5)
        desc_min = st.number_input("(4) Descent (min)", min_value=0, value=10, step=1)

        # FIX: alternate default now 60 min
        alt_min = st.number_input("(7) Alternate (min)", min_value=0, value=60, step=5)
        reserve_min = 45

    def l_from_min(mins, rate=rate_lph):
        return round(rate * (mins / 60.0), 1)

    enrt_min_eff = enrt_h * 60 + enrt_min
    trip_min = climb_min + enrt_min_eff + desc_min
    trip_l = l_from_min(trip_min)
    cont_min = int(round(0.05 * trip_min))
    cont_l = round(0.05 * trip_l, 1)

    taxi_l = l_from_min(taxi_min)
    climb_l = l_from_min(climb_min)
    enrt_l = l_from_min(enrt_min_eff)
    desc_l = l_from_min(desc_min)

    alt_l = l_from_min(alt_min)
    reserve_l = l_from_min(reserve_min)

    req_ramp_l = round(taxi_l + trip_l + cont_l + alt_l + reserve_l, 1)
    req_ramp_min = taxi_min + trip_min + cont_min + alt_min + reserve_min

    extra_l = max(0.0, round(fuel_l - req_ramp_l, 1))
    extra_min = int(round((extra_l / rate_lph) * 60)) if rate_lph > 0 else 0

    total_ramp_l = round(req_ramp_l + extra_l, 1)
    total_ramp_min = req_ramp_min + extra_min

    # Convert inputs to lb / USG
    front_lb = (student_kg + instructor_kg) * KG_TO_LB
    rear_lb = rear_pax_kg * KG_TO_LB
    bag_lb = baggage_kg * KG_TO_LB

    fuel_usg = fuel_l * L_TO_USG
    # FIX: force FULL to be exactly 48.0 USG
    if abs(fuel_l - FUEL_USABLE_L) < 0.5:
        fuel_usg = FUEL_USABLE_USG
    fuel_lb = fuel_usg * FUEL_LB_PER_USG

    if fleet_ok:
        ew_lb, ew_mom = parse_ew(st.session_state.fleet[reg])
    else:
        ew_lb, ew_mom = 0.0, 0.0

    ew_cg = (ew_mom / ew_lb) if ew_lb > 0 else 0.0

    # moments
    mom_front = front_lb * ARM_FRONT
    mom_rear = rear_lb * ARM_REAR
    mom_fuel = fuel_lb * ARM_FUEL
    mom_bag = bag_lb * ARM_BAGGAGE

    ramp_w = ew_lb + front_lb + rear_lb + fuel_lb + bag_lb
    ramp_m = ew_mom + mom_front + mom_rear + mom_fuel + mom_bag
    ramp_cg = (ramp_m / ramp_w) if ramp_w > 0 else 0.0

    takeoff_w = ramp_w - TAXI_ALLOW_LB
    takeoff_m = ramp_m - (TAXI_ALLOW_LB * TAXI_ARM)
    takeoff_cg = (takeoff_m / takeoff_w) if takeoff_w > 0 else 0.0

    # landing: burn trip fuel only
    burn_usg = trip_l * L_TO_USG
    burn_lb = burn_usg * FUEL_LB_PER_USG
    landing_w = max(0.0, takeoff_w - burn_lb)
    landing_m = takeoff_m - (burn_lb * ARM_FUEL)
    landing_cg = (landing_m / landing_w) if landing_w > 0 else 0.0

    with c2:
        st.markdown("#### Summary")
        st.markdown(
            f"<div class='box'>"
            f"<b>Empty</b>: {ew_lb:.0f} lb ({ew_lb/KG_TO_LB:.0f} kg) · CG {ew_cg:.1f} in<br>"
            f"<b>Takeoff</b>: {takeoff_w:.0f} lb ({takeoff_w/KG_TO_LB:.0f} kg) · CG {takeoff_cg:.1f} in<br>"
            f"<b>Landing</b>: {landing_w:.0f} lb ({landing_w/KG_TO_LB:.0f} kg) · CG {landing_cg:.1f} in"
            f"</div>",
            unsafe_allow_html=True
        )

        def lim_color(w, lim):
            if w > lim:
                return "bad"
            if w > 0.95 * lim:
                return "warn"
            return "ok"

        st.markdown(
            f"<div class='box'><b>Limits</b><br>"
            f"MTOW {MTOW_LB:.0f} lb · <span class='{lim_color(takeoff_w, MTOW_LB)}'>Takeoff {takeoff_w:.0f}</span><br>"
            f"MLW {MLW_LB:.0f} lb · <span class='{lim_color(landing_w, MLW_LB)}'>Landing {landing_w:.0f}</span></div>",
            unsafe_allow_html=True
        )

        st.markdown("#### Fuel planning (for PDF)")
        rows = [
            ("Start-up & Taxi", taxi_min, taxi_l),
            ("Climb", climb_min, climb_l),
            ("Enroute", enrt_min_eff, enrt_l),
            ("Descent", desc_min, desc_l),
            ("Trip Fuel (2+3+4)", trip_min, trip_l),
            ("Contingency 5%", cont_min, cont_l),
            ("Alternate", alt_min, alt_l),
            ("Reserve 45 min", reserve_min, reserve_l),
            ("Required Ramp", req_ramp_min, req_ramp_l),
            ("Extra", extra_min, extra_l),
            ("Total Ramp", total_ramp_min, total_ramp_l),
            ("Fuel loaded", 0, fuel_l),
        ]
        html = ["<table class='tbl'><tr><th>Item</th><th>Time</th><th>Fuel</th></tr>"]
        for name, mins, liters in rows:
            if name == "Fuel loaded" and abs(liters - FUEL_USABLE_L) < 0.5:
                usg = FUEL_USABLE_USG
            else:
                usg = liters * L_TO_USG
            t = fmt_hm(mins) if mins else "—"
            html.append(f"<tr><td>{name}</td><td>{t}</td><td>{usg:.1f} USG ({liters:.1f} L)</td></tr>")
        html.append("</table>")
        st.markdown("".join(html), unsafe_allow_html=True)

    # store for PDF
    st.session_state["_wb"] = {
        "ew_lb": ew_lb, "ew_mom": ew_mom,
        "front_lb": front_lb, "rear_lb": rear_lb, "bag_lb": bag_lb, "fuel_lb": fuel_lb,
        "ramp_w": ramp_w, "ramp_m": ramp_m, "ramp_cg": ramp_cg,
        "takeoff_w": takeoff_w, "takeoff_m": takeoff_m, "takeoff_cg": takeoff_cg,
        "landing_w": landing_w, "landing_m": landing_m, "landing_cg": landing_cg,
        "fuel_l": fuel_l, "fuel_usg": fuel_usg,
    }

    # FIX: store taxi_l so Start-up_and_Taxi_FUEL is not zero
    st.session_state["_fuel"] = {
        "rate_lph": rate_lph,

        "taxi_min": taxi_min, "taxi_l": taxi_l,
        "climb_min": climb_min, "climb_l": climb_l,
        "enrt_min": enrt_min_eff, "enrt_l": enrt_l,
        "desc_min": desc_min, "desc_l": desc_l,

        "trip_min": trip_min, "trip_l": trip_l,
        "cont_min": cont_min, "cont_l": cont_l,
        "alt_min": alt_min, "alt_l": alt_l,
        "reserve_min": reserve_min, "reserve_l": reserve_l,
        "req_min": req_ramp_min, "req_l": req_ramp_l,
        "extra_min": extra_min, "extra_l": extra_l,
        "total_min": total_ramp_min, "total_l": total_ramp_l,
    }

# -----------------------------
# 4) PDF
# -----------------------------
with tab4:
    st.markdown("#### Generate filled PDF")

    try:
        template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
        fieldset = get_field_names(template_bytes)

        wb = st.session_state.get("_wb", {})
        fuel = st.session_state.get("_fuel", {})
        reg = st.session_state.get("reg", "")
        date_str = st.session_state.flight_date.strftime("%d/%m/%Y")

        f = {}

        def put(name, value):
            if name in fieldset:
                f[name] = value

        # --- header
        put("Date", date_str)
        for candidate in ["Aircraft_Reg", "Aircraft_Reg.", "Aircraft Reg.", "Aircraft_Reg__", "Aircraft_Reg_"]:
            if candidate in fieldset:
                put(candidate, reg)

        # --- Fill MTOW / MLW (no optional extras)
        for nm in ["MTOW", "MTOW_LB", "Max_Takeoff_Weight", "Maximum_Takeoff_Weight", "MaxTakeoffWeight", "Max_Takeoff_Wt"]:
            put(nm, f"{MTOW_LB:.0f}")
        for nm in ["MLW", "MLW_LB", "Max_Landing_Weight", "Maximum_Landing_Weight", "MaxLandingWeight", "Max_Landing_Wt"]:
            put(nm, f"{MLW_LB:.0f}")

        # --- Loading data (page 0)
        def w_str(lb):
            kg = lb / KG_TO_LB
            return f"{lb:.0f} ({kg:.0f}kg)"

        def fuel_w_str(fuel_lb, fuel_usg, fuel_l):
            return f"{fuel_lb:.0f} ({fuel_usg:.1f}USG/{fuel_l:.0f}L)"

        # empty
        ew_lb = wb.get("ew_lb", 0.0)
        ew_mom = wb.get("ew_mom", 0.0)
        ew_cg = (ew_mom / ew_lb) if ew_lb > 0 else 82.0

        put("Weight_EMPTY", w_str(ew_lb))
        put("Moment_EMPTY", f"{ew_mom:.0f}")
        put("Datum_EMPTY", f"{ew_cg:.1f}")

        put("Weight_FRONT", w_str(wb.get("front_lb", 0.0)))
        put("Moment_FRONT", f"{(wb.get('front_lb',0.0) * ARM_FRONT):.0f}")

        put("Weight_REAR", w_str(wb.get("rear_lb", 0.0)))
        put("Moment_REAR", f"{(wb.get('rear_lb',0.0) * ARM_REAR):.0f}")

        fuel_usg = wb.get("fuel_usg", 0.0)
        fuel_l = wb.get("fuel_l", 0.0)
        fuel_lb = wb.get("fuel_lb", 0.0)
        put("Weight_FUEL", fuel_w_str(fuel_lb, fuel_usg, fuel_l))
        put("Moment_FUEL", f"{(fuel_lb * ARM_FUEL):.0f}")

        put("Weight_BAGGAGE", w_str(wb.get("bag_lb", 0.0)))
        put("Moment_BAGGAGE", f"{(wb.get('bag_lb',0.0) * ARM_BAGGAGE):.0f}")

        # ramp / takeoff boxes (ok to fill)
        put("Weight_RAMP", w_str(wb.get("ramp_w", 0.0)))
        put("Moment_RAMP", f"{wb.get('ramp_m',0.0):.0f}")
        put("Datum_RAMP", f"{wb.get('ramp_cg',0.0):.1f}")

        put("Weight_TAKEOFF", w_str(wb.get("takeoff_w", 0.0)))
        put("Moment_TAKEOFF", f"{wb.get('takeoff_m',0.0):.0f}")
        put("Datum_TAKEOFF", f"{wb.get('takeoff_cg',0.0):.1f}")

        # --- Airfield blocks (page 1)
        def pa_da(elev_ft, qnh_hpa, oat_c):
            pa_ft = float(elev_ft) + (1013.0 - float(qnh_hpa)) * 30.0
            isa = 15.0 - 2.0 * (float(elev_ft) / 1000.0)
            da_ft = pa_ft + 120.0 * (float(oat_c) - isa)
            return pa_ft, da_ft

        dep_target = dt.datetime.combine(st.session_state.flight_date, st.session_state.dep_time_utc).replace(tzinfo=dt.timezone.utc)
        arr_target = dt.datetime.combine(st.session_state.flight_date, st.session_state.arr_time_utc).replace(tzinfo=dt.timezone.utc)
        alt_target = arr_target + dt.timedelta(hours=1)
        targets = [dep_target, arr_target, alt_target, alt_target]

        for i, leg in enumerate(st.session_state.legs):
            role = leg["role"]  # DEPARTURE / ARRIVAL / ALTERNATE_1 / ALTERNATE_2
            icao = leg["icao"]
            ad = AERODROMES_DB.get(icao, None)
            if not ad:
                continue

            met = st.session_state.met[i] or {"wind_dir": 240, "wind_kt": 8, "temp_c": 15, "qnh_hpa": 1013, "label": ""}
            best = choose_best_runway_by_wind(ad, met["wind_dir"], met["wind_kt"])
            if not best:
                continue
            rw = best["rw"]

            suf = role
            put(f"Airfield_{suf}", icao)
            put(f"RWY_QFU_{suf}", f"{rw['qfu']:.0f}")
            put(f"Elevation_{suf}", f"{ad['elev_ft']:.0f}")
            put(f"QNH_{suf}", f"{met['qnh_hpa']}")
            put(f"Temperature_{suf}", f"{met['temp_c']}")
            put(f"Wind_{suf}", f"{met['wind_dir']:03d}/{met['wind_kt']:02d}")

            pa_ft, da_ft = pa_da(ad["elev_ft"], met["qnh_hpa"], met["temp_c"])

            if f"Pressure_Alt_{suf}" in fieldset:
                put(f"Pressure_Alt_{suf}", f"{pa_ft:.0f}")
            elif suf == "DEPARTURE" and "Pressure_Alt _DEPARTURE" in fieldset:
                put("Pressure_Alt _DEPARTURE", f"{pa_ft:.0f}")

            put(f"Density_Alt_{suf}", f"{da_ft:.0f}")
            put(f"TODA_{suf}", f"{rw['toda']:.0f}")
            put(f"LDA_{suf}", f"{rw['lda']:.0f}")

        # --- Fuel planning fields (USG with liters in parentheses)
        def fuel_str(liters):
            liters = float(liters)
            # FIX: when full, show exactly 48.0 USG
            if abs(liters - float(FUEL_USABLE_L)) < 0.5:
                usg = float(FUEL_USABLE_USG)
            else:
                usg = liters * L_TO_USG
            return f"{usg:.1f} ({liters:.1f}L)"

        put("Start-up_and_Taxi_TIME", fmt_hm(int(fuel.get("taxi_min", 0))))
        put("Start-up_and_Taxi_FUEL", fuel_str(float(fuel.get("taxi_l", 0.0))))

        put("CLIMB_TIME", fmt_hm(int(fuel.get("climb_min", 0))))
        put("CLIMB_FUEL", fuel_str(float(fuel.get("climb_l", 0.0))))

        put("ENROUTE_TIME", fmt_hm(int(fuel.get("enrt_min", 0))))
        put("ENROUTE_FUEL", fuel_str(float(fuel.get("enrt_l", 0.0))))

        put("DESCENT_TIME", fmt_hm(int(fuel.get("desc_min", 0))))
        put("DESCENT_FUEL", fuel_str(float(fuel.get("desc_l", 0.0))))

        put("TRIP_TIME", fmt_hm(int(fuel.get("trip_min", 0))))
        put("TRIP_FUEL", fuel_str(float(fuel.get("trip_l", 0.0))))

        put("Contingency_TIME", fmt_hm(int(fuel.get("cont_min", 0))))
        put("Contingency_FUEL", fuel_str(float(fuel.get("cont_l", 0.0))))

        put("ALTERNATE_TIME", fmt_hm(int(fuel.get("alt_min", 0))))
        put("ALTERNATE_FUEL", fuel_str(float(fuel.get("alt_l", 0.0))))

        put("RESERVE_TIME", fmt_hm(int(fuel.get("reserve_min", 45))))
        put("RESERVE_FUEL", fuel_str(float(fuel.get("reserve_l", 0.0))))

        put("REQUIRED_TIME", fmt_hm(int(fuel.get("req_min", 0))))
        put("REQUIRED_FUEL", fuel_str(float(fuel.get("req_l", 0.0))))

        put("EXTRA_TIME", fmt_hm(int(fuel.get("extra_min", 0))))
        put("EXTRA_FUEL", fuel_str(float(fuel.get("extra_l", 0.0))))

        put("Total_TIME", fmt_hm(int(fuel.get("total_min", 0))))
        put("Total_FUEL", fuel_str(float(fuel.get("total_l", 0.0))))

        # Fill PDF then overlay chart on page 0
        base_filled = fill_pdf(template_bytes, f)

        chart_points = [
            {"label": "Empty",   "cg": ew_cg,                   "w": ew_lb,                 "rgb": (0.10, 0.60, 0.15)},
            {"label": "Takeoff", "cg": wb.get("takeoff_cg", 0), "w": wb.get("takeoff_w",0), "rgb": (0.10, 0.30, 0.85)},
            {"label": "Landing", "cg": wb.get("landing_cg", 0), "w": wb.get("landing_w",0), "rgb": (0.85, 0.15, 0.15)},
        ]

        final_pdf = draw_cg_overlay_on_page0(base_filled, chart_points)

        mission = ascii_safe(st.session_state.get("mission_no", "")).strip().replace(" ", "_")
        mission_part = f"{mission}_" if mission else ""
        out_name = f"{mission_part}{reg}_PA28_MB_Perf.pdf"

        st.download_button(
            "Download PDF",
            data=final_pdf,
            file_name=out_name,
            mime="application/pdf",
            type="primary",
        )

    except Exception as e:
        st.error(f"PDF error: {e}")

