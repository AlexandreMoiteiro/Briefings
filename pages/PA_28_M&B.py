# app.py — PA28 Archer III (Sevenair) — M&B + Weather + Performance + PDF + CG + 4-up + Side-by-side first page
# Execução:
#   pip install streamlit requests pytz pypdf reportlab pymupdf pillow numpy
#   streamlit run app.py
#
# Assets esperados (na mesma pasta, ou via upload no tab Performance se quiseres):
#   - RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf   (template M&B)
#   - takeoff: to_ground_roll.jpg + to_ground_roll.json
#   - landing: ldg_ground_roll.pdf + ldg_ground_roll.json
#   - climb:   climb_perf.jpg + climb_perf.json
#
# Opcional (Fleet via Gist):
#   - st.secrets["GITHUB_GIST_TOKEN"]
#   - st.secrets["GITHUB_GIST_ID_PA28"]

import io
import csv
import json
import unicodedata
import datetime as dt
from math import cos, sin, radians, sqrt, atan2, degrees
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz
import requests
import numpy as np
import streamlit as st

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, BooleanObject

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader


# =========================================================
# App setup
# =========================================================
st.set_page_config(
    page_title="PA28 — M&B + Weather + Performance + PDF",
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

st.markdown('<div class="hdr">Piper PA28 Archer III — M&B + Weather + Performance + PDF</div>', unsafe_allow_html=True)


# =========================================================
# Helpers
# =========================================================
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


def _here(name: str) -> Optional[Path]:
    p = Path(name)
    if p.exists():
        return p
    return None


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


# =========================================================
# Constants / PA28 data
# =========================================================
KG_TO_LB = 2.2046226218
L_TO_USG = 1.0 / 3.785411784
USG_TO_L = 3.785411784

FUEL_LB_PER_USG = 6.0

FUEL_USABLE_USG = 48.0
FUEL_USABLE_L = 182.0
BAGGAGE_MAX_KG = 90.0
BAGGAGE_MAX_LB = BAGGAGE_MAX_KG * KG_TO_LB

ARM_FRONT = 80.5
ARM_REAR = 118.1
ARM_FUEL = 95.0
ARM_BAGGAGE = 142.8

TAXI_ALLOW_LB = 8.0
TAXI_ARM = 95.5

MTOW_LB = 2550.0
MLW_LB = 2550.0

PDF_TEMPLATE_PATHS = ["RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"]


# =========================================================
# OurAirports DB + overrides
# =========================================================
OURAIRPORTS_AIRPORTS_CSV = "https://ourairports.com/data/airports.csv"
OURAIRPORTS_RUNWAYS_CSV = "https://ourairports.com/data/runways.csv"

ICAO_SET = sorted({
    "LEBZ","LPBR","LPBG","LPCB","LPCO","LPEV","LEMG","LPSO","LEZL","LEVX","LPVR","LPVZ","LPCS","LPMT",
    "LPST","LPBJ","LPFR","LPPM","LPPR"
})

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

            if le_hdg is None:
                le_hdg = _rw_ident_to_qfu_deg(le_ident)
            if he_hdg is None:
                he_hdg = _rw_ident_to_qfu_deg(he_ident)

            if le_ident and le_hdg is not None:
                runways.append({"id": le_ident, "qfu": float(le_hdg), "toda": length_m, "lda": length_m})
            if he_ident and he_hdg is not None:
                runways.append({"id": he_ident, "qfu": float(he_hdg), "toda": length_m, "lda": length_m})

        db[icao] = {"name": name, "lat": lat, "lon": lon, "elev_ft": elev_ft, "runways": runways}

    # OVERRIDES (dados operacionais reais / consistentes)
    # LPSO: RWY03/21 headings 026/206, length 1800m
    if "LPSO" in db:
        db["LPSO"]["name"] = "Ponte de Sôr"
        db["LPSO"]["runways"] = [
            {"id": "03", "qfu": 26.0,  "toda": 1800.0, "lda": 1800.0},
            {"id": "21", "qfu": 206.0, "toda": 1800.0, "lda": 1800.0},
        ]

    # LPEV: manter 01/19 e 07/25 (remover antigas tipo 04/18)
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


# =========================================================
# Wind/runway helpers
# =========================================================
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


# =========================================================
# Weather (Open-Meteo)
# =========================================================
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


# =========================================================
# Fleet via GitHub Gist (EW + Moment)
# =========================================================
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


# =========================================================
# PDF utils (fields, fill)
# =========================================================
def read_pdf_bytes(paths) -> bytes:
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


# =========================================================
# CG overlay (page 0) — anchors
# =========================================================
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


# =========================================================
# Performance solver assets + math (takeoff/landing/climb)
# =========================================================
ASSETS = {
    "landing": {
        "title": "Landing Ground Roll",
        "bg_default": "ldg_ground_roll.pdf",
        "json_default": "ldg_ground_roll.json",
        "bg_kind": "pdf",
        "page_default": 0,
        "round_to": 5,
        "label_font": 20,
    },
    "takeoff": {
        "title": "Takeoff Ground Roll",
        "bg_default": "to_ground_roll.jpg",
        "json_default": "to_ground_roll.json",
        "bg_kind": "image",
        "page_default": 0,
        "round_to": 5,
        "label_font": 20,
    },
    "climb": {
        "title": "Climb Performance",
        "bg_default": "climb_perf.jpg",
        "json_default": "climb_perf.json",
        "bg_kind": "image",
        "page_default": 0,
        "round_to": 10,
        "label_font": 20,
    },
}

def load_json_asset(mode: str, upload_json=None) -> Dict[str, Any]:
    info = ASSETS[mode]
    if upload_json is not None:
        return json.loads(upload_json.read().decode("utf-8"))
    p = _here(info["json_default"])
    if not p:
        raise FileNotFoundError(f"Não encontrei {info['json_default']}")
    return json.loads(p.read_text(encoding="utf-8"))

@st.cache_data(show_spinner=False)
def render_pdf_to_image(pdf_bytes: bytes, page_index: int, zoom: float) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img

def load_background_asset(mode: str, upload_bg=None, page_index: int = 0, zoom: float = 2.3) -> Image.Image:
    info = ASSETS[mode]
    if info["bg_kind"] == "pdf":
        if upload_bg is not None:
            pdf_bytes = upload_bg.read()
        else:
            p = _here(info["bg_default"])
            if not p:
                raise FileNotFoundError(f"Não encontrei {info['bg_default']}")
            pdf_bytes = p.read_bytes()
        return render_pdf_to_image(pdf_bytes, page_index=page_index, zoom=zoom)

    if upload_bg is not None:
        return Image.open(upload_bg).convert("RGB")

    p = _here(info["bg_default"])
    if not p:
        raise FileNotFoundError(f"Não encontrei {info['bg_default']}")
    return Image.open(p).convert("RGB")

def pt_xy(p: Any) -> Tuple[float, float]:
    if isinstance(p, dict):
        return float(p["x"]), float(p["y"])
    if isinstance(p, (list, tuple)) and len(p) == 2:
        return float(p[0]), float(p[1])
    raise ValueError(f"Invalid point: {p}")

def normalize_panel(panel_pts: Any) -> List[Dict[str, float]]:
    if not isinstance(panel_pts, list) or len(panel_pts) != 4:
        return []
    out = []
    for p in panel_pts:
        x, y = pt_xy(p)
        out.append({"x": x, "y": y})
    return out

def normalize_panels(cap: Dict[str, Any]) -> Dict[str, List[Dict[str, float]]]:
    out = {}
    pc = cap.get("panel_corners", {})
    if not isinstance(pc, dict):
        return out
    for k, pts in pc.items():
        out[k] = normalize_panel(pts)
    return out

def fit_axis_value_from_ticks(ticks: List[Dict[str, float]], coord: str) -> Tuple[float, float]:
    xs = np.array([float(t[coord]) for t in ticks], dtype=float)
    vs = np.array([float(t["value"]) for t in ticks], dtype=float)
    A = np.vstack([xs, np.ones_like(xs)]).T
    a, b = np.linalg.lstsq(A, vs, rcond=None)[0]
    return float(a), float(b)

def axis_value(a: float, b: float, coord_val: float) -> float:
    return a * coord_val + b

def axis_coord_from_value(a: float, b: float, value: float) -> float:
    if abs(a) < 1e-12:
        raise ValueError("Axis fit degenerate (a ~ 0).")
    return (value - b) / a

def line_y_at_x(seg: Dict[str, float], x: float) -> float:
    x1, y1, x2, y2 = map(float, (seg["x1"], seg["y1"], seg["x2"], seg["y2"]))
    if abs(x2 - x1) < 1e-12:
        return y1
    t = (x - x1) / (x2 - x1)
    return y1 + t * (y2 - y1)

def parse_pa_levels_ft(lines: Dict[str, List[Dict[str, float]]]) -> List[Tuple[float, str]]:
    out: List[Tuple[float, str]] = []
    for k, segs in lines.items():
        if not k.startswith("pa_"):
            continue
        if not segs:
            continue
        if k == "pa_sea_level":
            out.append((0.0, k))
            continue
        try:
            out.append((float(k.replace("pa_", "")), k))
        except Exception:
            pass
    out.sort(key=lambda t: t[0])
    return out

def interp_between_levels(v: float, levels: List[Tuple[float, str]]) -> Tuple[Tuple[float, str], Tuple[float, str], float]:
    if not levels:
        raise ValueError("No PA levels available (all pa_* lines empty?).")
    if v <= levels[0][0]:
        return levels[0], levels[0], 0.0
    if v >= levels[-1][0]:
        return levels[-1], levels[-1], 0.0
    for i in range(len(levels) - 1):
        a, ka = levels[i]
        b, kb = levels[i + 1]
        if a <= v <= b:
            alpha = (v - a) / (b - a) if b != a else 0.0
            return (a, ka), (b, kb), float(alpha)
    return levels[-1], levels[-1], 0.0

def round_to_step(x: float, step: float) -> float:
    return step * round(x / step)

def x_of_vertical_ref(seg: Dict[str, float]) -> float:
    return 0.5 * (float(seg["x1"]) + float(seg["x2"]))

def interp_guides_y(
    guides: List[Dict[str, float]],
    x_ref: float,
    y_ref: float,
    x_target: float
) -> Tuple[float, Dict[str, Any]]:
    if not guides:
        return y_ref, {"used": "none"}

    rows = []
    for g in guides:
        yr = line_y_at_x(g, x_ref)
        yt = line_y_at_x(g, x_target)
        rows.append((yr, yt))

    rows.sort(key=lambda t: t[0])

    if y_ref <= rows[0][0]:
        return float(rows[0][1]), {"used": "clamp_low"}
    if y_ref >= rows[-1][0]:
        return float(rows[-1][1]), {"used": "clamp_high"}

    for i in range(len(rows) - 1):
        y0_ref, y0_tgt = rows[i]
        y1_ref, y1_tgt = rows[i + 1]
        if y0_ref <= y_ref <= y1_ref:
            denom = (y1_ref - y0_ref)
            a = 0.0 if abs(denom) < 1e-12 else (y_ref - y0_ref) / denom
            y_tgt = (1 - a) * y0_tgt + a * y1_tgt
            return float(y_tgt), {"used": "interp", "i0": i, "i1": i + 1, "alpha": float(a)}

    return y_ref, {"used": "fallback"}

def pick_guides(cap: Dict[str, Any], mode: str) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
    g = cap.get("guides", {}) or {}
    if mode == "takeoff":
        return g.get("guides_weight", []) or [], g.get("guides_wind", []) or []
    mid = g.get("middle", []) or []
    rgt = g.get("right", []) or []
    if len(mid) == 0 and len(rgt) == 0:
        return g.get("guides_weight", []) or [], g.get("guides_wind", []) or []
    return mid, rgt

def solve_ground_roll(
    cap: Dict[str, Any],
    mode: str,
    oat_c: float,
    pa_ft: float,
    weight_lb: float,
    wind_kt: float
) -> Tuple[float, List[Tuple[Tuple[float, float], Tuple[float, float]]], Dict[str, Any]]:
    ticks = cap["axis_ticks"]
    lines = cap["lines"]
    panels = normalize_panels(cap)

    ax_oat_a, ax_oat_b = fit_axis_value_from_ticks(ticks["oat_c"], "x")
    ax_wt_a, ax_wt_b = fit_axis_value_from_ticks(ticks["weight_x100_lb"], "x")
    ax_wind_a, ax_wind_b = fit_axis_value_from_ticks(ticks["wind_kt"], "x")

    out_axis_key = "ground_roll_ft" if mode == "landing" else "takeoff_gr_ft"
    ax_out_a, ax_out_b = fit_axis_value_from_ticks(ticks[out_axis_key], "y")

    if not lines.get("weight_ref_line"):
        raise ValueError("Missing lines['weight_ref_line']")
    if not lines.get("wind_ref_zero"):
        raise ValueError("Missing lines['wind_ref_zero']")

    x_ref_mid = x_of_vertical_ref(lines["weight_ref_line"][0])
    x_ref_right = x_of_vertical_ref(lines["wind_ref_zero"][0])

    x_oat = axis_coord_from_value(ax_oat_a, ax_oat_b, oat_c)

    pa_levels = parse_pa_levels_ft(lines)
    (lo_ft, k_lo), (hi_ft, k_hi), alpha = interp_between_levels(pa_ft, pa_levels)
    seg_lo = lines[k_lo][0]
    seg_hi = lines[k_hi][0]
    y_entry = (1 - alpha) * line_y_at_x(seg_lo, x_oat) + alpha * line_y_at_x(seg_hi, x_oat)

    x_wt = axis_coord_from_value(ax_wt_a, ax_wt_b, weight_lb / 100.0)

    g_mid, g_right = pick_guides(cap, mode=mode)
    y_mid, dbg_mid = interp_guides_y(g_mid, x_ref=x_ref_mid, y_ref=y_entry, x_target=x_wt)

    x_wind = axis_coord_from_value(ax_wind_a, ax_wind_b, wind_kt)
    y_out, dbg_right = interp_guides_y(g_right, x_ref=x_ref_right, y_ref=y_mid, x_target=x_wind)

    out_val = axis_value(ax_out_a, ax_out_b, y_out)

    segs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    left_panel = panels.get("left") or []
    if not left_panel:
        raise ValueError("Missing panel_corners['left']")
    y_bottom_left = float(left_panel[2]["y"])

    segs.append(((x_oat, y_bottom_left), (x_oat, y_entry)))
    segs.append(((x_oat, y_entry), (x_ref_mid, y_entry)))
    segs.append(((x_ref_mid, y_entry), (x_wt, y_mid)))
    segs.append(((x_wt, y_mid), (x_ref_right, y_mid)))
    segs.append(((x_ref_right, y_mid), (x_wind, y_out)))

    right_panel = panels.get("right") or []
    if not right_panel:
        raise ValueError("Missing panel_corners['right']")
    x_right_edge = float(right_panel[1]["x"])
    segs.append(((x_wind, y_out), (x_right_edge, y_out)))

    debug = {
        "x_oat": x_oat,
        "y_entry": y_entry,
        "x_ref_mid": x_ref_mid,
        "x_wt": x_wt,
        "y_mid": y_mid,
        "x_ref_right": x_ref_right,
        "x_wind": x_wind,
        "y_out": y_out,
        "pa_interp": {"lo": (lo_ft, k_lo), "hi": (hi_ft, k_hi), "alpha": alpha},
        "guide_mid": dbg_mid,
        "guide_right": dbg_right,
    }
    return out_val, segs, debug

def solve_climb(
    cap: Dict[str, Any],
    oat_c: float,
    pa_ft: float
) -> Tuple[float, List[Tuple[Tuple[float, float], Tuple[float, float]]], Dict[str, Any]]:
    ticks = cap["axis_ticks"]
    lines = cap["lines"]
    panels = normalize_panels(cap)

    ax_oat_a, ax_oat_b = fit_axis_value_from_ticks(ticks["oat_c"], "x")
    ax_roc_a, ax_roc_b = fit_axis_value_from_ticks(ticks["roc_fpm"], "y")

    x_oat = axis_coord_from_value(ax_oat_a, ax_oat_b, oat_c)

    pa_levels = parse_pa_levels_ft(lines)
    (lo_ft, k_lo), (hi_ft, k_hi), alpha = interp_between_levels(pa_ft, pa_levels)
    seg_lo = lines[k_lo][0]
    seg_hi = lines[k_hi][0]
    y = (1 - alpha) * line_y_at_x(seg_lo, x_oat) + alpha * line_y_at_x(seg_hi, x_oat)

    roc = axis_value(ax_roc_a, ax_roc_b, y)

    main = panels.get("main") or []
    if not main:
        raise ValueError("Missing panel_corners['main']")
    y_bottom = float(main[2]["y"])
    x_right_edge = float(main[1]["x"])

    segs = [
        ((x_oat, y_bottom), (x_oat, y)),
        ((x_oat, y), (x_right_edge, y)),
    ]

    debug = {
        "x_oat": x_oat,
        "y": y,
        "x_right_edge": x_right_edge,
        "pa_interp": {"lo": (lo_ft, k_lo), "hi": (hi_ft, k_hi), "alpha": alpha},
    }
    return roc, segs, debug


# =========================================================
# Pretty drawing for performance images (no ugly white box)
# =========================================================
def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except Exception:
        return ImageFont.load_default()

def text_bbox(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
    try:
        x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
        return int(x1 - x0), int(y1 - y0)
    except Exception:
        return (8 * len(text), 14)

def place_label_smart(
    draw: ImageDraw.ImageDraw,
    img_w: int,
    img_h: int,
    tip: Tuple[float, float],
    text: str,
    font,
    pad: int = 4,
    safe_margin: int = 8,
) -> Tuple[Tuple[int, int], Tuple[int, int, int, int]]:
    tx, ty = int(tip[0]), int(tip[1])
    tw, th = text_bbox(draw, text, font)
    candidates = [
        (10, -th - 10),
        (-tw - 10, -th - 10),
        (10, 10),
        (-tw - 10, 10),
        (-tw - 10, -th // 2),
        (10, -th // 2),
        (-tw // 2, -th - 12),
        (-tw // 2, 12),
    ]

    def ok(x: int, y: int) -> bool:
        rx0 = x - pad
        ry0 = y - pad
        rx1 = x + tw + pad
        ry1 = y + th + pad
        if rx0 < safe_margin or ry0 < safe_margin:
            return False
        if rx1 > img_w - safe_margin or ry1 > img_h - safe_margin:
            return False
        if rx1 > img_w - 30:
            return False
        return True

    for dx, dy in candidates:
        x = tx + dx
        y = ty + dy
        if ok(x, y):
            rect = (x - pad, y - pad, x + tw + pad, y + th + pad)
            return (x, y), rect

    x = min(max(tx - tw - 10, safe_margin), img_w - tw - safe_margin - 30)
    y = min(max(ty - th - 10, safe_margin), img_h - th - safe_margin)
    rect = (x - pad, y - pad, x + tw + pad, y + th + pad)
    return (x, y), rect

def draw_path_clean(draw: ImageDraw.ImageDraw, segs, color=(255, 140, 0), width=4):
    for p1, p2 in segs:
        draw.line([p1, p2], fill=color, width=width)
    if segs:
        x, y = segs[-1][1]
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline=(255, 255, 255), width=2)

def draw_badge(img: Image.Image, xy: Tuple[int, int], text: str, font) -> Image.Image:
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    pad_x, pad_y = 12, 6
    tw, th = text_bbox(d, text, font)
    x, y = xy
    rect = [x - pad_x, y - pad_y, x + tw + pad_x, y + th + pad_y]

    try:
        d.rounded_rectangle(rect, radius=14, fill=(30, 41, 59, 210))
    except Exception:
        d.rectangle(rect, fill=(30, 41, 59, 210))

    d.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 160))
    d.text((x, y), text, font=font, fill=(255, 255, 255))

    return Image.alpha_composite(base, overlay).convert("RGB")

def make_pretty_perf_image(bg: Image.Image, path, value_text: str, title: str, label_font_size: int = 20) -> Image.Image:
    img = bg.copy()
    d = ImageDraw.Draw(img)

    if path:
        draw_path_clean(d, path)

        tip = path[-1][1]
        font = load_font(label_font_size)
        pos, _ = place_label_smart(d, img.size[0], img.size[1], tip, value_text, font)
        img = draw_badge(img, pos, value_text, font)

    # title top
    title_font = load_font(22)
    d = ImageDraw.Draw(img)
    d.text((18, 14), title, fill=(20, 20, 20), font=title_font)

    return img


# =========================================================
# 4-up pages for performance (3 pages) + append to PDF
# =========================================================
def build_perf_4up_page(images_by_role: List[Tuple[str, Image.Image]], title: str) -> bytes:
    W, H = landscape(A4)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))

    margin = 28
    gap = 14
    header_h = 30
    cell_w = (W - 2 * margin - gap) / 2
    cell_h = (H - 2 * margin - gap - header_h) / 2

    c.setFont("Helvetica-Bold", 18)
    c.drawString(margin, H - margin, title)

    top_y = H - margin - header_h
    positions = [
        (margin, top_y - cell_h),
        (margin + cell_w + gap, top_y - cell_h),
        (margin, top_y - 2 * cell_h - gap),
        (margin + cell_w + gap, top_y - 2 * cell_h - gap),
    ]

    for (label, img), (x, y) in zip(images_by_role, positions):
        c.setFont("Helvetica-Bold", 12)
        c.drawString(x, y + cell_h - 14, label)

        iw, ih = img.size
        scale = min(cell_w / iw, (cell_h - 20) / ih)

        dw, dh = iw * scale, ih * scale
        dx = x + (cell_w - dw) / 2
        dy = y + (cell_h - 22 - dh) / 2

        c.drawImage(ImageReader(img), dx, dy, width=dw, height=dh, preserveAspectRatio=True, mask="auto")
        c.setLineWidth(0.6)
        c.rect(x, y, cell_w, cell_h)

    c.showPage()
    c.save()
    return buf.getvalue()

def append_perf_pages(base_pdf_bytes: bytes, perf: dict) -> bytes:
    reader = PdfReader(io.BytesIO(base_pdf_bytes))
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)

    order = ["DEPARTURE", "ARRIVAL", "ALTERNATE_1", "ALTERNATE_2"]
    def role_label(role):
        info = perf.get(role, {})
        return info.get("inputs", {}).get("label", role)

    pages = [
        ("TAKEOFF — Ground Roll", "takeoff"),
        ("LANDING — Ground Roll", "landing"),
        ("CLIMB — Rate of Climb", "climb"),
    ]

    for title, key in pages:
        imgs = []
        for r in order:
            if r in perf and perf[r].get("imgs", {}).get(key) is not None:
                imgs.append((role_label(r), perf[r]["imgs"][key]))
        if len(imgs) == 4:
            page_pdf = build_perf_4up_page(imgs, title)
            p = PdfReader(io.BytesIO(page_pdf)).pages[0]
            writer.add_page(p)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# =========================================================
# Side-by-side first page (MB PDF -> image -> single-page PDF -> prepend)
# =========================================================
def _pixmap_to_pil(pix: fitz.Pixmap, bg=(255, 255, 255)) -> Image.Image:
    if pix.alpha:
        img = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
        bg_img = Image.new("RGB", img.size, bg)
        bg_img.paste(img, mask=img.split()[3])
        return bg_img
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

def _preprocess_pdf_for_raster(pdf_bytes: bytes) -> bytes:
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as d:
            changed = False
            for page in d:
                try:
                    widgets = page.widgets()
                    if widgets:
                        for w in widgets:
                            w.update()
                            changed = True
                except Exception:
                    pass
            if changed:
                return d.tobytes(deflate=True, garbage=3)
    except Exception:
        pass
    return pdf_bytes

def _render_page_rgb(page: fitz.Page, dpi: int, bg=(255, 255, 255)) -> Image.Image:
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False, annots=True, colorspace=fitz.csRGB)
    return _pixmap_to_pil(pix, bg=bg)

def _merge_side_by_side(img_left: Image.Image, img_right: Image.Image, align_by="height", gap_px=0, bg=(255,255,255)) -> Image.Image:
    if align_by == "width":
        target = max(img_left.width, img_right.width)
        if img_left.width != target:
            h = int(round(img_left.height * (target / img_left.width)))
            img_left = img_left.resize((target, h), Image.LANCZOS)
        if img_right.width != target:
            h = int(round(img_right.height * (target / img_right.width)))
            img_right = img_right.resize((target, h), Image.LANCZOS)
        H = max(img_left.height, img_right.height)
        W = target * 2 + gap_px
        canvas_img = Image.new("RGB", (W, H), bg)
        canvas_img.paste(img_left, (0, (H - img_left.height) // 2))
        canvas_img.paste(img_right, (target + gap_px, (H - img_right.height) // 2))
        return canvas_img

    target = max(img_left.height, img_right.height)
    if img_left.height != target:
        w = int(round(img_left.width * (target / img_left.height)))
        img_left = img_left.resize((w, target), Image.LANCZOS)
    if img_right.height != target:
        w = int(round(img_right.width * (target / img_right.height)))
        img_right = img_right.resize((w, target), Image.LANCZOS)
    W = img_left.width + img_right.width + gap_px
    H = target
    canvas_img = Image.new("RGB", (W, H), bg)
    canvas_img.paste(img_left, (0, 0))
    canvas_img.paste(img_right, (img_left.width + gap_px, 0))
    return canvas_img

def mb_pdf_to_side_by_side_image(pdf_bytes: bytes, dpi: int = 300, align_by="height", gap_px=0, bg=(255,255,255), sharpen=True) -> Image.Image:
    pdf_bytes = _preprocess_pdf_for_raster(pdf_bytes)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.page_count < 1:
            raise ValueError("PDF inválido (sem páginas).")
        i1 = _render_page_rgb(doc.load_page(0), dpi, bg)
        if doc.page_count >= 2:
            i2 = _render_page_rgb(doc.load_page(1), dpi, bg)
        else:
            i2 = Image.new("RGB", i1.size, bg)
        merged = _merge_side_by_side(i1, i2, align_by=align_by, gap_px=gap_px, bg=bg)
        if sharpen:
            merged = merged.filter(ImageFilter.UnsharpMask(radius=0.8, percent=120, threshold=3))
        return merged

def image_to_single_page_pdf(img: Image.Image, dpi: int = 300) -> bytes:
    w_px, h_px = img.size
    w_pt = (w_px / dpi) * 72.0
    h_pt = (h_px / dpi) * 72.0
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w_pt, h_pt))
    c.drawImage(ImageReader(img), 0, 0, width=w_pt, height=h_pt, preserveAspectRatio=True, mask="auto")
    c.showPage()
    c.save()
    return buf.getvalue()

def prepend_pdf(first_page_pdf_bytes: bytes, rest_pdf_bytes: bytes) -> bytes:
    r1 = PdfReader(io.BytesIO(first_page_pdf_bytes))
    r2 = PdfReader(io.BytesIO(rest_pdf_bytes))
    w = PdfWriter()
    for p in r1.pages:
        w.add_page(p)
    for p in r2.pages:
        w.add_page(p)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


# =========================================================
# Session defaults (legs)
# =========================================================
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

if "dep_time_utc" not in st.session_state:
    st.session_state.dep_time_utc = (dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)).time()
if "arr_time_utc" not in st.session_state:
    st.session_state.arr_time_utc = (dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=2)).time()

if "perf" not in st.session_state:
    st.session_state.perf = {}  # results per leg


# =========================================================
# Sidebar (fleet)
# =========================================================
with st.sidebar:
    st.subheader("🛩️ Fleet")
    st.caption("Loads EW & EW Moment from GitHub Gist (optional).")
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


# =========================================================
# Tabs
# =========================================================
tab1, tab2, tab3, tabP, tab4 = st.tabs(["1) Flight", "2) Aerodromes & Weather", "3) Weight & Fuel", "4) Performance", "5) PDF"])


# =========================================================
# 1) Flight
# =========================================================
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


# =========================================================
# 2) Aerodromes & Weather
# =========================================================
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


# =========================================================
# 3) Weight & Fuel
# =========================================================
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
        DEFAULT_USGPH = 10.0
        DEFAULT_LPH = DEFAULT_USGPH * USG_TO_L
        rate_lph = st.number_input("Consumption (L/h)", min_value=10.0, max_value=60.0, value=float(round(DEFAULT_LPH, 1)), step=0.5)
        st.caption("Reference: 10 USG/h ≈ 37.9 L/h")

        taxi_min = st.number_input("(1) Start-up & Taxi (min)", min_value=0, value=15, step=1)
        climb_min = st.number_input("(2) Climb (min)", min_value=0, value=10, step=1)
        enrt_h = st.number_input("(3) Enroute (h)", min_value=0, value=1, step=1)
        enrt_min = st.number_input("(3) Enroute (min)", min_value=0, value=0, step=5)
        desc_min = st.number_input("(4) Descent (min)", min_value=0, value=10, step=1)

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

    st.session_state["_wb"] = {
        "ew_lb": ew_lb, "ew_mom": ew_mom,
        "front_lb": front_lb, "rear_lb": rear_lb, "bag_lb": bag_lb, "fuel_lb": fuel_lb,
        "ramp_w": ramp_w, "ramp_m": ramp_m, "ramp_cg": ramp_cg,
        "takeoff_w": takeoff_w, "takeoff_m": takeoff_m, "takeoff_cg": takeoff_cg,
        "landing_w": landing_w, "landing_m": landing_m, "landing_cg": landing_cg,
        "fuel_l": fuel_l, "fuel_usg": fuel_usg,
    }

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


# =========================================================
# 4) Performance (auto compute for legs)
# =========================================================
def pa_da(elev_ft, qnh_hpa, oat_c):
    pa_ft = float(elev_ft) + (1013.0 - float(qnh_hpa)) * 30.0
    isa = 15.0 - 2.0 * (float(elev_ft) / 1000.0)
    da_ft = pa_ft + 120.0 * (float(oat_c) - isa)
    return pa_ft, da_ft

def perf_inputs_for_leg(role, icao, met, rw, ad, wb):
    pa_ft, da_ft = pa_da(ad["elev_ft"], met["qnh_hpa"], met["temp_c"])
    hw, xw, side = wind_components(rw["qfu"], met["wind_dir"], met["wind_kt"])
    headwind = max(0.0, float(hw))  # clamp tailwind
    return {
        "oat_c": float(met["temp_c"]),
        "pa_ft": float(pa_ft),
        "da_ft": float(da_ft),
        "headwind_kt": float(headwind),
        "crosswind_kt": float(xw),
        "takeoff_w_lb": float(wb["takeoff_w"]),
        "landing_w_lb": float(wb["landing_w"]),
        "runway_id": rw["id"],
        "runway_qfu": float(rw["qfu"]),
        "toda_m": float(rw["toda"]),
        "lda_m": float(rw["lda"]),
        "label": f"{icao} {role.replace('_',' ').title()}",
    }

with tabP:
    st.markdown("#### Performance (Landing / Takeoff / Climb) — auto from legs + weather + W&B")

    cL, cR = st.columns([0.62, 0.38])

    with cR:
        st.markdown("##### Assets")
        upload_to_bg = st.file_uploader("Upload Takeoff BG (jpg)", type=["jpg","jpeg","png"], key="up_to_bg")
        upload_to_json = st.file_uploader("Upload Takeoff JSON", type=["json"], key="up_to_json")
        upload_ldg_bg = st.file_uploader("Upload Landing BG (pdf)", type=["pdf"], key="up_ldg_bg")
        upload_ldg_json = st.file_uploader("Upload Landing JSON", type=["json"], key="up_ldg_json")
        upload_clb_bg = st.file_uploader("Upload Climb BG (jpg)", type=["jpg","jpeg","png"], key="up_clb_bg")
        upload_clb_json = st.file_uploader("Upload Climb JSON", type=["json"], key="up_clb_json")

        st.markdown("##### Compute")
        preview_imgs = st.checkbox("Show preview images", value=True)
        ldg_zoom = st.number_input("Landing PDF zoom", value=2.3, step=0.1)
        compute_perf = st.button("Compute performance for all legs", type="primary")

    if compute_perf:
        wb = st.session_state.get("_wb", None)
        if not wb or wb.get("takeoff_w", 0) <= 0:
            st.error("W&B not ready. Go to tab 'Weight & Fuel' first.")
        else:
            try:
                cap_to = load_json_asset("takeoff", upload_to_json)
                cap_ldg = load_json_asset("landing", upload_ldg_json)
                cap_clb = load_json_asset("climb", upload_clb_json)

                bg_to = load_background_asset("takeoff", upload_to_bg, page_index=0, zoom=1.0)
                bg_ldg = load_background_asset("landing", upload_ldg_bg, page_index=0, zoom=float(ldg_zoom))
                bg_clb = load_background_asset("climb", upload_clb_bg, page_index=0, zoom=1.0)

                perf = {}
                for i, leg in enumerate(st.session_state.legs):
                    role = leg["role"]
                    icao = leg["icao"]
                    ad = AERODROMES_DB.get(icao)
                    if not ad:
                        continue
                    met = st.session_state.met[i] or {"wind_dir":240,"wind_kt":8,"temp_c":15,"qnh_hpa":1013}
                    best = choose_best_runway_by_wind(ad, met["wind_dir"], met["wind_kt"])
                    if not best:
                        continue
                    rw = best["rw"]
                    inp = perf_inputs_for_leg(role, icao, met, rw, ad, wb)

                    raw_to, segs_to, _ = solve_ground_roll(cap_to, mode="takeoff", oat_c=inp["oat_c"], pa_ft=inp["pa_ft"], weight_lb=inp["takeoff_w_lb"], wind_kt=inp["headwind_kt"])
                    to_ft = round_to_step(raw_to, ASSETS["takeoff"]["round_to"])

                    raw_ldg, segs_ldg, _ = solve_ground_roll(cap_ldg, mode="landing", oat_c=inp["oat_c"], pa_ft=inp["pa_ft"], weight_lb=inp["landing_w_lb"], wind_kt=inp["headwind_kt"])
                    ldg_ft = round_to_step(raw_ldg, ASSETS["landing"]["round_to"])

                    raw_roc, segs_roc, _ = solve_climb(cap_clb, oat_c=inp["oat_c"], pa_ft=inp["pa_ft"])
                    roc_fpm = round_to_step(raw_roc, ASSETS["climb"]["round_to"])

                    imgs = {
                        "takeoff": make_pretty_perf_image(bg_to, segs_to, f"{to_ft:.0f} ft", inp["label"], label_font_size=ASSETS["takeoff"]["label_font"]),
                        "landing": make_pretty_perf_image(bg_ldg, segs_ldg, f"{ldg_ft:.0f} ft", inp["label"], label_font_size=ASSETS["landing"]["label_font"]),
                        "climb":   make_pretty_perf_image(bg_clb, segs_roc, f"{roc_fpm:.0f} fpm", inp["label"], label_font_size=ASSETS["climb"]["label_font"]),
                    }

                    perf[role] = {
                        "takeoff_gr_ft": float(to_ft),
                        "landing_gr_ft": float(ldg_ft),
                        "roc_fpm": float(roc_fpm),
                        "inputs": inp,
                        "imgs": imgs,
                    }

                st.session_state.perf = perf
                st.success("Performance computed.")
            except Exception as e:
                st.error(f"Performance error: {e}")

    perf = st.session_state.get("perf", {}) or {}
    if not perf:
        st.info("Compute performance to populate values and images.")
    else:
        st.markdown("##### Results")
        order = ["DEPARTURE", "ARRIVAL", "ALTERNATE_1", "ALTERNATE_2"]

        rows = []
        for r in order:
            if r not in perf:
                continue
            rows.append((
                perf[r]["inputs"]["label"],
                f"{perf[r]['takeoff_gr_ft']:.0f}",
                f"{perf[r]['landing_gr_ft']:.0f}",
                f"{perf[r]['roc_fpm']:.0f}",
                f"{perf[r]['inputs']['headwind_kt']:.0f}",
                perf[r]["inputs"]["runway_id"],
            ))

        st.markdown(
            "<table class='tbl'>"
            "<tr><th>Leg</th><th>TO GR (ft)</th><th>LDG GR (ft)</th><th>ROC (fpm)</th><th>HW (kt)</th><th>RWY</th></tr>"
            + "".join([f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td><td>{e}</td><td>{f}</td></tr>" for a,b,c,d,e,f in rows])
            + "</table>",
            unsafe_allow_html=True,
        )

        if preview_imgs:
            st.markdown("##### Preview images (per leg)")
            for r in order:
                if r not in perf:
                    continue
                st.markdown(f"**{perf[r]['inputs']['label']}**")
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.image(perf[r]["imgs"]["takeoff"], caption="Takeoff", use_container_width=True)
                with c2:
                    st.image(perf[r]["imgs"]["landing"], caption="Landing", use_container_width=True)
                with c3:
                    st.image(perf[r]["imgs"]["climb"], caption="Climb", use_container_width=True)
                st.divider()


# =========================================================
# 5) PDF (fill + CG + performance fields + append 4-up + prepend side-by-side)
# =========================================================
with tab4:
    st.markdown("#### Generate filled PDF")

    opt_col1, opt_col2 = st.columns([0.6, 0.4])
    with opt_col2:
        st.markdown("##### Output options")
        add_perf_pages = st.checkbox("Append 4-up performance pages (3 pages)", value=True)
        add_side_by_side_first = st.checkbox("Add side-by-side preview as FIRST page", value=True)
        sbs_dpi = st.slider("Side-by-side DPI", 200, 600, 300, 50)
        sbs_align = st.radio("Side-by-side align", ["height", "width"], index=0, horizontal=True)
        sbs_gap = st.number_input("Side-by-side gap (px)", min_value=0, max_value=100, value=0, step=1)
        sbs_sharpen = st.checkbox("Side-by-side sharpen", value=True)
        bg_choice = st.selectbox("Side-by-side background", ["White", "Light gray", "Black"], index=0)
        sbs_bg = {"White": (255,255,255), "Light gray": (246,248,251), "Black": (0,0,0)}[bg_choice]

    try:
        template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
        fieldset = get_field_names(template_bytes)

        wb = st.session_state.get("_wb", {})
        fuel = st.session_state.get("_fuel", {})
        reg = st.session_state.get("reg", "")
        date_str = st.session_state.flight_date.strftime("%d/%m/%Y")
        perf = st.session_state.get("perf", {}) or {}

        f = {}

        def put(name, value):
            if name in fieldset:
                f[name] = value

        def put_any(candidates: List[str], value):
            for nm in candidates:
                if nm in fieldset:
                    f[nm] = value
                    return True
            return False

        # header
        put("Date", date_str)
        for candidate in ["Aircraft_Reg", "Aircraft_Reg.", "Aircraft Reg.", "Aircraft_Reg__", "Aircraft_Reg_"]:
            put(candidate, reg)

        # MTOW / MLW
        for nm in ["MTOW", "MTOW_LB", "Max_Takeoff_Weight", "Maximum_Takeoff_Weight", "MaxTakeoffWeight", "Max_Takeoff_Wt"]:
            put(nm, f"{MTOW_LB:.0f}")
        for nm in ["MLW", "MLW_LB", "Max_Landing_Weight", "Maximum_Landing_Weight", "MaxLandingWeight", "Max_Landing_Wt"]:
            put(nm, f"{MLW_LB:.0f}")

        # Loading page 0
        def w_str(lb):
            kg = lb / KG_TO_LB
            return f"{lb:.0f} ({kg:.0f}kg)"

        def fuel_w_str(fuel_lb, fuel_usg, fuel_l):
            return f"{fuel_lb:.0f} ({fuel_usg:.1f}USG/{fuel_l:.0f}L)"

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

        put("Weight_RAMP", w_str(wb.get("ramp_w", 0.0)))
        put("Moment_RAMP", f"{wb.get('ramp_m',0.0):.0f}")
        put("Datum_RAMP", f"{wb.get('ramp_cg',0.0):.1f}")

        put("Weight_TAKEOFF", w_str(wb.get("takeoff_w", 0.0)))
        put("Moment_TAKEOFF", f"{wb.get('takeoff_m',0.0):.0f}")
        put("Datum_TAKEOFF", f"{wb.get('takeoff_cg',0.0):.1f}")

        # Airfield blocks (page 1)
        for i, leg in enumerate(st.session_state.legs):
            role = leg["role"]
            icao = leg["icao"]
            ad = AERODROMES_DB.get(icao, None)
            if not ad:
                continue
            met = st.session_state.met[i] or {"wind_dir": 240, "wind_kt": 8, "temp_c": 15, "qnh_hpa": 1013}
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

        # Fuel planning fields (USG + liters)
        def fuel_str(liters):
            liters = float(liters)
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

        # Performance values -> PDF (best-effort field-name variants)
        if perf:
            for role, data in perf.items():
                suf = role
                put_any([f"TO_GR_{suf}", f"Takeoff_GR_{suf}", f"TakeoffGroundRoll_{suf}", f"TO_GroundRoll_{suf}", f"TakeoffGroundRollFt_{suf}"], f"{data['takeoff_gr_ft']:.0f}")
                put_any([f"LDG_GR_{suf}", f"Landing_GR_{suf}", f"LandingGroundRoll_{suf}", f"LDG_GroundRoll_{suf}", f"LandingGroundRollFt_{suf}"], f"{data['landing_gr_ft']:.0f}")
                put_any([f"ROC_{suf}", f"Climb_ROC_{suf}", f"RateOfClimb_{suf}", f"ROC_FPM_{suf}", f"ClimbFPM_{suf}"], f"{data['roc_fpm']:.0f}")

        # Fill PDF
        base_filled = fill_pdf(template_bytes, f)

        # CG overlay points (Empty/Takeoff/Landing only)
        chart_points = [
            {"label": "Empty",   "cg": ew_cg,                   "w": ew_lb,                 "rgb": (0.10, 0.60, 0.15)},
            {"label": "Takeoff", "cg": wb.get("takeoff_cg", 0), "w": wb.get("takeoff_w",0), "rgb": (0.10, 0.30, 0.85)},
            {"label": "Landing", "cg": wb.get("landing_cg", 0), "w": wb.get("landing_w",0), "rgb": (0.85, 0.15, 0.15)},
        ]
        mb_pdf = draw_cg_overlay_on_page0(base_filled, chart_points)  # "mass & balance" part

        # Build side-by-side FIRST page from the M&B part only (2 pages)
        if add_side_by_side_first:
            sbs_img = mb_pdf_to_side_by_side_image(mb_pdf, dpi=int(sbs_dpi), align_by=sbs_align, gap_px=int(sbs_gap), bg=sbs_bg, sharpen=sbs_sharpen)
            sbs_page_pdf = image_to_single_page_pdf(sbs_img, dpi=int(sbs_dpi))
        else:
            sbs_page_pdf = None

        # Append performance pages (optional)
        final_pdf = mb_pdf
        if add_perf_pages and perf:
            final_pdf = append_perf_pages(final_pdf, perf)

        # Prepend side-by-side page (optional)
        if sbs_page_pdf is not None:
            final_pdf = prepend_pdf(sbs_page_pdf, final_pdf)

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

        with opt_col1:
            st.markdown(
                "<div class='box'>"
                "<b>Output structure</b><br>"
                "• If enabled: Page 1 = Side-by-side preview (M&B p1+p2 as one image)<br>"
                "• Then: original filled M&B pages<br>"
                "• If enabled: +3 pages (Takeoff/Landing/Climb 4-up) at the end"
                "</div>",
                unsafe_allow_html=True
            )

    except Exception as e:
        st.error(f"PDF error: {e}")


