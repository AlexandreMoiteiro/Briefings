# app.py
# ---------------------------------------------------------------
# NAVLOG Portugal — VFR + IFR Low — Streamlit
# SIDs + STARs only
# ---------------------------------------------------------------
# Ficheiros esperados na raiz do repositório:
#   AD-HEL-ULM.csv
#   Localidades-Nova-versao-230223.csv
#   NAVAIDS_VOR.csv
#   IFR_POINTS.csv
#   IFR_AIRWAYS.csv
#   procedures_lpso.json
#   NAVLOG_FORM.pdf       opcional
#   NAVLOG_FORM_1.pdf     opcional
#
# Secrets Streamlit:
#   OPENAIP_API_KEY       opcional
#   GITHUB_TOKEN          opcional
#   ROUTES_GIST_ID        opcional
# ---------------------------------------------------------------

from __future__ import annotations

import datetime as dt
import difflib
import io
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Fullscreen, MarkerCluster, MeasureControl
from streamlit_folium import st_folium

try:
    import requests
except Exception:
    requests = None

try:
    from pdfrw import PdfDict, PdfName, PdfReader, PdfWriter
except Exception:
    PdfDict = PdfName = PdfReader = PdfWriter = None

# ===============================================================
# CONFIG
# ===============================================================
APP_TITLE = "NAVLOG Portugal — VFR + IFR Low"
APP_SUBTITLE = "SIDs + STARs only. Aproximações não são carregadas como procedimentos."
ROOT = Path(__file__).parent

CSV_AD = ROOT / "AD-HEL-ULM.csv"
CSV_LOC = ROOT / "Localidades-Nova-versao-230223.csv"
CSV_VOR = ROOT / "NAVAIDS_VOR.csv"
CSV_IFR_POINTS = ROOT / "IFR_POINTS.csv"
CSV_IFR_AIRWAYS = ROOT / "IFR_AIRWAYS.csv"
PROC_FILE = ROOT / "procedures_lpso.json"

TEMPLATE_MAIN = ROOT / "NAVLOG_FORM.pdf"
TEMPLATE_CONT = ROOT / "NAVLOG_FORM_1.pdf"
OUTPUT_MAIN = ROOT / "NAVLOG_FILLED.pdf"
OUTPUT_CONT = ROOT / "NAVLOG_FILLED_1.pdf"

EARTH_NM = 3440.065
LITERS_PER_USG = 3.785411784
LPSO_FALLBACK_CENTER = (39.2119, -8.0569)
PT_BOUNDS = [(36.70, -9.85), (42.25, -6.00)]
PROCEDURE_KINDS_ALLOWED = {"SID", "STAR"}

PROFILE_COLORS = {
    "CLIMB": "#f97316",
    "LEVEL": "#7c3aed",
    "DESCENT": "#059669",
    "STOP": "#dc2626",
}

AIRCRAFT_PROFILES: Dict[str, Dict[str, float]] = {
    "Tecnam P2008": {
        "climb_tas": 70.0,
        "cruise_tas": 90.0,
        "descent_tas": 90.0,
        "fuel_flow_lh": 20.0,
        "taxi_fuel_l": 3.0,
    },
    "Piper PA-28": {
        "climb_tas": 76.0,
        "cruise_tas": 110.0,
        "descent_tas": 100.0,
        "fuel_flow_lh": 38.0,
        "taxi_fuel_l": 5.0,
    },
}

REG_OPTIONS_TECNAM = ["CS-DHS", "CS-DHT", "CS-DHU", "CS-DHV", "CS-DHW", "CS-ECC", "CS-ECD"]
REG_OPTIONS_PIPER = ["OE-KPD", "OE-KPE", "OE-KPG", "OE-KPP", "OE-KPJ", "OE-KPF"]

ROUND_TIME_SEC = 60
ROUND_DIST_NM = 0.5
ROUND_FUEL_L = 1.0

# O formulário NAVLOG principal tem espaço útil para cerca de 10 legs.
# Se a rota couber aí, a linha seguinte é usada como TOTAL e o PDF é exportado só com a primeira página.
PDF_SINGLE_PAGE_LEG_ROWS = 11
PDF_FULL_TEMPLATE_LEG_ROWS = 22
PDF_TOTAL_ROW_INDEX = 23

# ===============================================================
# STREAMLIT SETUP / STYLE
# ===============================================================
st.set_page_config(page_title="NAVLOG IFR/VFR", page_icon="🧭", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
<style>
:root{--card:#ffffff;--line:#e2e8f0;--muted:#64748b;--text:#0f172a;--accent:#2563eb;}
.block-container{padding-top:1.2rem;padding-bottom:2rem;max-width:1500px;}
.nav-hero{background:linear-gradient(135deg,#eff6ff,#ffffff);border:1px solid #bfdbfe;border-radius:22px;padding:18px 20px;margin-bottom:12px;}
.nav-title{font-size:30px;font-weight:850;letter-spacing:-.03em;color:var(--text);margin:0;}
.nav-sub{font-size:14px;color:var(--muted);margin-top:4px;}
.pill{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 10px;font-size:12px;font-weight:650;margin:3px 4px 3px 0;color:#0f172a;}
.pill-good{border-color:#bbf7d0;background:#f0fdf4;color:#166534;}
.pill-warn{border-color:#fed7aa;background:#fff7ed;color:#9a3412;}
.small-muted{font-size:12px;color:var(--muted)}
hr{border:none;border-top:1px solid var(--line);margin:1rem 0;}
</style>
""",
    unsafe_allow_html=True,
)

# ===============================================================
# DATA MODEL
# ===============================================================
@dataclass
class Point:
    code: str
    name: str
    lat: float
    lon: float
    alt: float = 0.0
    src: str = "USER"
    routes: str = ""
    remarks: str = ""
    stop_min: float = 0.0
    wind_from: Optional[int] = None
    wind_kt: Optional[int] = None
    vor_pref: str = "AUTO"
    vor_ident: str = ""
    arc_vor: str = ""
    arc_radius_nm: float = 0.0
    arc_start_radial: float = 0.0
    arc_end_radial: float = 0.0
    arc_direction: str = "CW"
    arc_endpoint: str = ""
    turn_center_lat: Optional[float] = None
    turn_center_lon: Optional[float] = None
    turn_radius_nm: float = 0.0
    turn_start_course: float = 0.0
    turn_end_course: float = 0.0
    turn_direction: str = "LEFT"
    uid: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Point":
        fields = cls.__dataclass_fields__.keys()
        clean = {k: data.get(k) for k in fields if k in data}
        clean.setdefault("code", str(data.get("code") or data.get("name") or "WP").upper())
        clean.setdefault("name", str(data.get("name") or data.get("code") or "WP"))
        clean.setdefault("lat", float(data.get("lat", 0.0)))
        clean.setdefault("lon", float(data.get("lon", 0.0)))
        return cls(**clean)

# ===============================================================
# GENERAL HELPERS
# ===============================================================
def ss(key: str, default: Any) -> Any:
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def clean_code(x: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").upper().strip())


def round_to_step(x: float, step: float) -> float:
    if step <= 0:
        return float(x)
    return round(float(x) / step) * step


def rt(sec: float) -> int:
    return int(round_to_step(sec, ROUND_TIME_SEC))


def rd(nm: float) -> float:
    return round_to_step(nm, ROUND_DIST_NM)


def rf(litres: float) -> float:
    return round_to_step(litres, ROUND_FUEL_L)


def fmt_unit(x: float) -> str:
    return str(int(round(float(x))))


def fmt_num_clean(x: float, decimals: int = 1) -> str:
    value = round(float(x), decimals)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.{decimals}f}"


def liters_to_usg(liters: float) -> float:
    return float(liters) / LITERS_PER_USG


def fmt_efob_numbers(liters: float, decimals_usg: int = 1) -> str:
    return f"{fmt_unit(liters)} ({fmt_num_clean(liters_to_usg(liters), decimals_usg)})"


def mmss(sec: float) -> str:
    mins = int(round(float(sec) / 60.0))
    if mins < 60:
        return f"{mins:02d}:00"
    return f"{mins // 60:02d}:{mins % 60:02d}:00"


def pdf_time(sec: float) -> str:
    mins = int(round(float(sec) / 60.0))
    if mins >= 60:
        h, m = divmod(mins, 60)
        return f"{h:02d}h{m:02d}"
    return f"{mins:02d}:00"


def wrap360(x: float) -> float:
    return (float(x) % 360.0 + 360.0) % 360.0


def angdiff(a: float, b: float) -> float:
    return (float(a) - float(b) + 180.0) % 360.0 - 180.0


def dms_token_to_dd(token: str, is_lon: bool = False) -> Optional[float]:
    token = str(token).strip().upper()
    match = re.match(r"^(\d+(?:\.\d+)?)([NSEW])$", token)
    if not match:
        return None
    raw, hemi = match.groups()
    if is_lon:
        deg = int(raw[0:3])
        mins = int(raw[3:5])
        secs = float(raw[5:] or 0)
    else:
        deg = int(raw[0:2])
        mins = int(raw[2:4])
        secs = float(raw[4:] or 0)
    value = deg + mins / 60.0 + secs / 3600.0
    return -value if hemi in {"S", "W"} else value


def dd_to_icao(lat: float, lon: float) -> str:
    lat_abs, lon_abs = abs(lat), abs(lon)
    lat_deg, lon_deg = int(lat_abs), int(lon_abs)
    lat_min = int(round((lat_abs - lat_deg) * 60))
    lon_min = int(round((lon_abs - lon_deg) * 60))
    if lat_min == 60:
        lat_deg += 1
        lat_min = 0
    if lon_min == 60:
        lon_deg += 1
        lon_min = 0
    return f"{lat_deg:02d}{lat_min:02d}{'N' if lat >= 0 else 'S'}{lon_deg:03d}{lon_min:02d}{'E' if lon >= 0 else 'W'}"


def gc_dist_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, lam1, phi2, lam2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dphi = phi2 - phi1
    dlam = lam2 - lam1
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return EARTH_NM * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def gc_course_tc(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, lam1, phi2, lam2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlam = lam2 - lam1
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return wrap360(math.degrees(math.atan2(y, x)))


def dest_point(lat: float, lon: float, bearing_deg: float, dist_nm: float) -> Tuple[float, float]:
    theta = math.radians(bearing_deg)
    delta = dist_nm / EARTH_NM
    phi1, lam1 = math.radians(lat), math.radians(lon)
    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi2)))
    y = math.sin(theta) * math.sin(delta) * math.cos(phi1)
    x = math.cos(delta) - math.sin(phi1) * sin_phi2
    lam2 = lam1 + math.atan2(y, x)
    return math.degrees(phi2), ((math.degrees(lam2) + 540) % 360) - 180


def point_along_gc(lat1: float, lon1: float, lat2: float, lon2: float, dist_from_start_nm: float) -> Tuple[float, float]:
    total = gc_dist_nm(lat1, lon1, lat2, lon2)
    if total <= 0:
        return lat1, lon1
    return dest_point(lat1, lon1, gc_course_tc(lat1, lon1, lat2, lon2), min(total, max(0.0, dist_from_start_nm)))


def wind_triangle(tc: float, tas: float, wind_from: float, wind_kt: float) -> Tuple[float, float, float]:
    if tas <= 0:
        return 0.0, wrap360(tc), 0.0
    d = math.radians(angdiff(wind_from, tc))
    cross = wind_kt * math.sin(d)
    s = max(-1.0, min(1.0, cross / max(tas, 1e-9)))
    wca = math.degrees(math.asin(s))
    th = wrap360(tc + wca)
    gs = max(0.0, tas * math.cos(math.radians(wca)) - wind_kt * math.cos(d))
    return wca, th, gs


def apply_mag_var(true_heading: float, mag_var: float, is_east: bool) -> float:
    return wrap360(true_heading - mag_var if is_east else true_heading + mag_var)

# ===============================================================
# CSV LOADING
# ===============================================================
@st.cache_data(show_spinner=False)
def load_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        st.warning(f"CSV em falta: {path.name}")
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        st.error(f"Não consegui ler {path.name}: {exc}")
        return pd.DataFrame()


def parse_ad_df(df: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if df.empty:
        return pd.DataFrame(columns=["code", "name", "lat", "lon", "alt", "src", "routes", "remarks"])
    for line in df.iloc[:, 0].dropna().tolist():
        text = str(line).strip()
        if not text or text.startswith(("Ident", "DEP/")):
            continue
        tokens = text.split()
        coords = [t for t in tokens if re.match(r"^\d+(?:\.\d+)?[NSEW]$", t, re.I)]
        if len(coords) < 2:
            continue
        lat = dms_token_to_dd(coords[-2], False)
        lon = dms_token_to_dd(coords[-1], True)
        if lat is None or lon is None:
            continue
        ident = tokens[0] if re.match(r"^[A-Z0-9]{3,5}$", tokens[0]) else ""
        try:
            name = " ".join(tokens[1:tokens.index(coords[0])]).strip()
        except Exception:
            name = ident or " ".join(tokens[:3])
        rows.append({"code": clean_code(ident or name), "name": name or ident, "lat": lat, "lon": lon, "alt": 0.0, "src": "AD", "routes": "", "remarks": ""})
    return pd.DataFrame(rows)


def parse_loc_df(df: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if df.empty:
        return pd.DataFrame(columns=["code", "name", "lat", "lon", "alt", "src", "routes", "remarks"])
    for line in df.iloc[:, 0].dropna().tolist():
        text = str(line).strip()
        if not text or "Total de registos" in text:
            continue
        tokens = text.split()
        coords = [t for t in tokens if re.match(r"^\d{6,7}(?:\.\d+)?[NSEW]$", t, re.I)]
        if len(coords) < 2:
            continue
        lat = dms_token_to_dd(coords[0], False)
        lon = dms_token_to_dd(coords[1], True)
        if lat is None or lon is None:
            continue
        try:
            lon_idx = tokens.index(coords[1])
            code = tokens[lon_idx + 1] if lon_idx + 1 < len(tokens) else ""
            name = " ".join(tokens[:tokens.index(coords[0])]).strip()
        except Exception:
            code = ""
            name = text[:32]
        rows.append({"code": clean_code(code or name), "name": name or code, "lat": lat, "lon": lon, "alt": 0.0, "src": "VFR", "routes": "", "remarks": ""})
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def load_vor(path_str: str) -> pd.DataFrame:
    path = Path(path_str)
    if not path.exists():
        st.warning(f"CSV em falta: {path.name}")
        return pd.DataFrame(columns=["ident", "name", "freq_mhz", "lat", "lon"])
    df = pd.read_csv(path)
    df = df.rename(columns={c: c.lower().strip() for c in df.columns})
    df = df.rename(columns={"frequency": "freq_mhz", "freq": "freq_mhz", "latitude": "lat", "longitude": "lon"})
    required = {"ident", "freq_mhz", "lat", "lon"}
    if not required.issubset(df.columns):
        st.error("NAVAIDS_VOR.csv precisa de colunas ident, freq_mhz, lat, lon.")
        return pd.DataFrame(columns=["ident", "name", "freq_mhz", "lat", "lon"])
    if "name" not in df.columns:
        df["name"] = df["ident"]
    df["ident"] = df["ident"].astype(str).str.upper().str.strip()
    df["freq_mhz"] = pd.to_numeric(df["freq_mhz"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    return df.dropna(subset=["ident", "freq_mhz", "lat", "lon"])[["ident", "name", "freq_mhz", "lat", "lon"]]


@st.cache_data(show_spinner=False)
def load_all_data() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ad = parse_ad_df(load_csv_safe(CSV_AD))
    loc = parse_loc_df(load_csv_safe(CSV_LOC))
    vor = load_vor(str(CSV_VOR)).copy()

    vor_points = pd.DataFrame()
    if not vor.empty:
        vor_points = pd.DataFrame({
            "code": vor["ident"],
            "name": vor["name"],
            "lat": vor["lat"],
            "lon": vor["lon"],
            "alt": 0.0,
            "src": "VOR",
            "routes": "",
            "remarks": vor["freq_mhz"].map(lambda x: f"{x:.2f} MHz"),
        })

    ifr = load_csv_safe(CSV_IFR_POINTS).copy()
    if not ifr.empty:
        ifr = ifr.rename(columns={c: c.lower().strip() for c in ifr.columns})
        if "code" not in ifr.columns and "ident" in ifr.columns:
            ifr["code"] = ifr["ident"]
        for col in ["name", "routes", "remarks", "src"]:
            if col not in ifr.columns:
                ifr[col] = "IFR" if col == "src" else ""
        if "alt" not in ifr.columns:
            ifr["alt"] = 0.0
        # Normaliza todos os pontos carregados deste CSV como IFR.
        ifr["src"] = "IFR"
        ifr["code"] = ifr["code"].astype(str).str.upper().str.strip()
        ifr["name"] = ifr["name"].fillna(ifr["code"]).astype(str)
        ifr["lat"] = pd.to_numeric(ifr["lat"], errors="coerce")
        ifr["lon"] = pd.to_numeric(ifr["lon"], errors="coerce")
        ifr = ifr.dropna(subset=["code", "lat", "lon"])[["code", "name", "lat", "lon", "alt", "src", "routes", "remarks"]]

    points = pd.concat([ad, loc, vor_points, ifr], ignore_index=True)
    if points.empty:
        points = pd.DataFrame(columns=["code", "name", "lat", "lon", "alt", "src", "routes", "remarks"])
    points["code"] = points["code"].map(clean_code)
    points["name"] = points["name"].fillna(points["code"]).astype(str)
    points["lat"] = pd.to_numeric(points["lat"], errors="coerce")
    points["lon"] = pd.to_numeric(points["lon"], errors="coerce")
    points = points.dropna(subset=["lat", "lon"]).drop_duplicates(subset=["code", "lat", "lon", "src"]).reset_index(drop=True)

    airways = load_csv_safe(CSV_IFR_AIRWAYS).copy()
    if not airways.empty:
        airways = airways.rename(columns={c: c.lower().strip() for c in airways.columns})
        for col in ["airway", "seq", "point", "lat", "lon"]:
            if col not in airways.columns:
                airways[col] = None
        for col in ["route_type", "lower", "upper", "mea", "remarks"]:
            if col not in airways.columns:
                airways[col] = ""
        airways["airway"] = airways["airway"].astype(str).str.upper().str.strip()
        airways["point"] = airways["point"].astype(str).str.upper().str.strip()
        airways["seq"] = pd.to_numeric(airways["seq"], errors="coerce")
        airways["lat"] = pd.to_numeric(airways["lat"], errors="coerce")
        airways["lon"] = pd.to_numeric(airways["lon"], errors="coerce")
        airways = airways.dropna(subset=["airway", "seq", "point", "lat", "lon"]).sort_values(["airway", "seq"])

    return points, vor, airways


POINTS_DF, VOR_DF, AIRWAYS_DF = load_all_data()


def get_openaip_token() -> str:
    token = os.getenv("OPENAIP_API_KEY", os.getenv("OPENAIP_KEY", ""))
    try:
        token = st.secrets.get("OPENAIP_API_KEY", token)
    except Exception:
        pass
    return str(token or "").strip()

# ===============================================================
# VOR + GEOMETRY HELPERS
# ===============================================================
def get_vor(ident: str) -> Optional[Dict[str, Any]]:
    ident = clean_code(ident)
    if not ident or VOR_DF.empty:
        return None
    hit = VOR_DF[VOR_DF["ident"].astype(str).str.upper() == ident]
    if hit.empty:
        return None
    r = hit.iloc[0]
    return {"ident": str(r["ident"]), "name": str(r["name"]), "freq_mhz": float(r["freq_mhz"]), "lat": float(r["lat"]), "lon": float(r["lon"])}


def nearest_vor(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_d = 1e9
    for _, r in VOR_DF.iterrows():
        d = gc_dist_nm(lat, lon, float(r["lat"]), float(r["lon"]))
        if d < best_d:
            best_d = d
            best = {"ident": str(r["ident"]), "name": str(r["name"]), "freq_mhz": float(r["freq_mhz"]), "lat": float(r["lat"]), "lon": float(r["lon"]), "dist_nm": d}
    return best


def vor_radial_distance(vor: Dict[str, Any], lat: float, lon: float) -> Tuple[int, float]:
    radial = int(round(gc_course_tc(vor["lat"], vor["lon"], lat, lon))) % 360
    dist = gc_dist_nm(vor["lat"], vor["lon"], lat, lon)
    return radial, dist


def format_vor_id(vor: Optional[Dict[str, Any]]) -> str:
    if not vor:
        return ""
    return f"{vor['freq_mhz']:.2f} {vor['ident']}"


def format_radial_dist(vor: Optional[Dict[str, Any]], lat: float, lon: float) -> str:
    if not vor:
        return ""
    radial, dist = vor_radial_distance(vor, lat, lon)
    # Se o fix é o próprio VOR/NAVAID, não faz sentido mostrar R000/D00.
    if dist < 0.3:
        return ""
    return f"R{radial:03d}/D{int(round(dist)):02d}"


def make_vor_fix(token: str) -> Optional[Point]:
    text = token.strip().upper().replace(" ", "")
    patterns = [
        r"^([A-Z0-9]{2,4})/R?(\d{1,3})/D?(\d+(?:\.\d+)?)$",
        r"^([A-Z0-9]{2,4})-R?(\d{1,3})-D?(\d+(?:\.\d+)?)$",
        r"^([A-Z0-9]{2,4})R(\d{1,3})D(\d+(?:\.\d+)?)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        vor = get_vor(match.group(1))
        if not vor:
            return None
        radial = float(match.group(2))
        dist = float(match.group(3))
        lat, lon = dest_point(vor["lat"], vor["lon"], radial, dist)
        return Point(
            code=f"{vor['ident']}R{int(radial):03d}D{dist:g}".replace(".", ""),
            name=f"{vor['ident']} R{int(radial):03d} D{dist:g}",
            lat=lat,
            lon=lon,
            src="VORFIX",
            remarks=format_vor_id(vor),
            vor_pref="FIXED",
            vor_ident=vor["ident"],
        )
    return None


def arc_radials(start_radial: float, end_radial: float, direction: str, step_deg: float) -> List[float]:
    step = max(1.0, abs(float(step_deg)))
    start = wrap360(start_radial)
    end = wrap360(end_radial)
    direction = str(direction or "CW").upper()
    radials = [start]
    if direction == "CCW":
        sweep = (start - end) % 360.0
        n = max(1, int(math.ceil(sweep / step)))
        for k in range(1, n):
            radials.append(wrap360(start - k * step))
    else:
        sweep = (end - start) % 360.0
        n = max(1, int(math.ceil(sweep / step)))
        for k in range(1, n):
            radials.append(wrap360(start + k * step))
    if abs(angdiff(radials[-1], end)) > 0.01:
        radials.append(end)
    return radials


def make_dme_arc_points(vor_ident: str, radius_nm: float, start_radial: float, end_radial: float, direction: str, alt_ft: float) -> Tuple[List[Point], str]:
    vor = get_vor(vor_ident)
    if not vor:
        return [], f"VOR {vor_ident} não encontrado."
    radius = max(0.1, float(radius_nm))
    start_r = wrap360(start_radial)
    end_r = wrap360(end_radial)
    direction = str(direction or "CW").upper()
    if direction not in {"CW", "CCW"}:
        direction = "CW"

    def make(radial: float, endpoint: str) -> Point:
        lat, lon = dest_point(vor["lat"], vor["lon"], radial, radius)
        return Point(
            code=f"{vor['ident']}D{radius:g}R{int(round(radial)) % 360:03d}".replace(".", "")[:12],
            name=f"{vor['ident']} D{radius:g} R{int(round(radial)) % 360:03d}",
            lat=lat,
            lon=lon,
            alt=float(alt_ft),
            src="DMEARC",
            remarks=f"{format_vor_id(vor)} ARC {radius:g} NM {direction}",
            vor_pref="FIXED",
            vor_ident=vor["ident"],
            arc_vor=vor["ident"],
            arc_radius_nm=radius,
            arc_start_radial=start_r,
            arc_end_radial=end_r,
            arc_direction=direction,
            arc_endpoint=endpoint,
            uid=next_uid(),
        )

    return [make(start_r, "START"), make(end_r, "END")], f"Arco {vor['ident']} D{radius:g} {direction} R{int(start_r):03d}->R{int(end_r):03d}"


def is_dme_arc_leg(A: Dict[str, Any], B: Dict[str, Any]) -> bool:
    return A.get("src") == "DMEARC" and B.get("src") == "DMEARC" and A.get("arc_vor") == B.get("arc_vor") and float(A.get("arc_radius_nm") or 0) > 0


def dme_arc_sweep_deg(A: Dict[str, Any], B: Dict[str, Any]) -> float:
    direction = str(B.get("arc_direction") or A.get("arc_direction") or "CW").upper()
    start = float(B.get("arc_start_radial") or A.get("arc_start_radial") or 0)
    end = float(B.get("arc_end_radial") or A.get("arc_end_radial") or 0)
    return (start - end) % 360.0 if direction == "CCW" else (end - start) % 360.0


def dme_arc_distance_nm(A: Dict[str, Any], B: Dict[str, Any]) -> float:
    radius = float(A.get("arc_radius_nm") or B.get("arc_radius_nm") or 0)
    return 2 * math.pi * radius * dme_arc_sweep_deg(A, B) / 360.0


def dme_arc_course(A: Dict[str, Any], B: Dict[str, Any]) -> float:
    direction = str(B.get("arc_direction") or A.get("arc_direction") or "CW").upper()
    start = float(B.get("arc_start_radial") or A.get("arc_start_radial") or 0)
    sweep = dme_arc_sweep_deg(A, B)
    mid_radial = wrap360(start - sweep / 2 if direction == "CCW" else start + sweep / 2)
    return wrap360(mid_radial - 90 if direction == "CCW" else mid_radial + 90)


def dme_arc_polyline(A: Dict[str, Any], B: Dict[str, Any], step_deg: float = 2.0) -> List[Tuple[float, float]]:
    vor = get_vor(str(A.get("arc_vor") or B.get("arc_vor") or ""))
    if not vor:
        return [(A["lat"], A["lon"]), (B["lat"], B["lon"])]
    radials = arc_radials(float(A.get("arc_start_radial") or B.get("arc_start_radial") or 0), float(A.get("arc_end_radial") or B.get("arc_end_radial") or 0), str(A.get("arc_direction") or B.get("arc_direction") or "CW"), step_deg)
    radius = float(A.get("arc_radius_nm") or B.get("arc_radius_nm") or 0)
    return [dest_point(vor["lat"], vor["lon"], radial, radius) for radial in radials]


def is_rate_turn_leg(A: Dict[str, Any], B: Dict[str, Any]) -> bool:
    return B.get("src") == "TURN" and B.get("turn_center_lat") is not None and float(B.get("turn_radius_nm") or 0) > 0


def turn_sweep_deg(A: Dict[str, Any], B: Dict[str, Any]) -> float:
    direction = str(B.get("turn_direction") or "LEFT").upper()
    start_course = float(B.get("turn_start_course") or 0)
    end_course = float(B.get("turn_end_course") or 0)
    start_radial = wrap360(start_course + 90 if direction == "LEFT" else start_course - 90)
    end_radial = wrap360(end_course + 90 if direction == "LEFT" else end_course - 90)
    return (start_radial - end_radial) % 360.0 if direction == "LEFT" else (end_radial - start_radial) % 360.0


def turn_distance_nm(A: Dict[str, Any], B: Dict[str, Any]) -> float:
    return 2 * math.pi * float(B.get("turn_radius_nm") or 0) * turn_sweep_deg(A, B) / 360.0


def turn_course(A: Dict[str, Any], B: Dict[str, Any]) -> float:
    return float(B.get("turn_end_course") or gc_course_tc(A["lat"], A["lon"], B["lat"], B["lon"]))


def turn_polyline(A: Dict[str, Any], B: Dict[str, Any], step_deg: float = 3.0) -> List[Tuple[float, float]]:
    center_lat = float(B["turn_center_lat"])
    center_lon = float(B["turn_center_lon"])
    radius = float(B["turn_radius_nm"])
    direction = str(B.get("turn_direction") or "LEFT").upper()
    start_course = float(B.get("turn_start_course") or 0)
    end_course = float(B.get("turn_end_course") or 0)
    start_radial = wrap360(start_course + 90 if direction == "LEFT" else start_course - 90)
    end_radial = wrap360(end_course + 90 if direction == "LEFT" else end_course - 90)
    arc_dir = "CCW" if direction == "LEFT" else "CW"
    return [dest_point(center_lat, center_lon, radial, radius) for radial in arc_radials(start_radial, end_radial, arc_dir, step_deg)]


def xy_nm(lat: float, lon: float, lat0: float, lon0: float) -> Tuple[float, float]:
    return (lon - lon0) * 60.0 * math.cos(math.radians(lat0)), (lat - lat0) * 60.0


def ll_from_xy(x: float, y: float, lat0: float, lon0: float) -> Tuple[float, float]:
    return lat0 + y / 60.0, lon0 + x / (60.0 * max(math.cos(math.radians(lat0)), 1e-9))


def track_vec(track_deg: float) -> Tuple[float, float]:
    t = math.radians(track_deg)
    return math.sin(t), math.cos(t)


def track_intercept_radial(start_lat: float, start_lon: float, track_deg: float, vor_ident: str, radial_deg: float, fallback_nm: float = 20.0) -> Tuple[float, float, bool]:
    vor = get_vor(vor_ident)
    if not vor:
        lat, lon = dest_point(start_lat, start_lon, track_deg, fallback_nm)
        return lat, lon, False
    lat0 = (start_lat + vor["lat"]) / 2.0
    lon0 = (start_lon + vor["lon"]) / 2.0
    sx, sy = xy_nm(start_lat, start_lon, lat0, lon0)
    vx, vy = xy_nm(vor["lat"], vor["lon"], lat0, lon0)
    dx1, dy1 = track_vec(track_deg)
    dx2, dy2 = track_vec(radial_deg)
    det = dx1 * (-dy2) - dy1 * (-dx2)
    if abs(det) < 1e-8:
        lat, lon = dest_point(start_lat, start_lon, track_deg, fallback_nm)
        return lat, lon, False
    bx = vx - sx
    by = vy - sy
    t = (bx * (-dy2) - by * (-dx2)) / det
    if t < 0:
        lat, lon = dest_point(start_lat, start_lon, track_deg, fallback_nm)
        return lat, lon, False
    lat, lon = ll_from_xy(sx + t * dx1, sy + t * dy1, lat0, lon0)
    return lat, lon, True


def track_intercept_dme(start_lat: float, start_lon: float, track_deg: float, vor_ident: str, dme_nm: float, choose: str = "first", fallback_nm: float = 20.0) -> Tuple[float, float, bool]:
    vor = get_vor(vor_ident)
    if not vor:
        lat, lon = dest_point(start_lat, start_lon, track_deg, fallback_nm)
        return lat, lon, False
    lat0 = (start_lat + vor["lat"]) / 2.0
    lon0 = (start_lon + vor["lon"]) / 2.0
    sx, sy = xy_nm(start_lat, start_lon, lat0, lon0)
    cx, cy = xy_nm(vor["lat"], vor["lon"], lat0, lon0)
    dx, dy = track_vec(track_deg)
    fx = sx - cx
    fy = sy - cy
    a = dx * dx + dy * dy
    b = 2 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - dme_nm * dme_nm
    disc = b * b - 4 * a * c
    hits: List[Tuple[float, float, float]] = []
    if disc >= 0:
        root = math.sqrt(disc)
        for t in [(-b - root) / (2 * a), (-b + root) / (2 * a)]:
            if t >= 0:
                lat, lon = ll_from_xy(sx + t * dx, sy + t * dy, lat0, lon0)
                hits.append((t, lat, lon))
    if hits:
        hits.sort(key=lambda x: x[0])
        hit = hits[-1] if choose == "last" else hits[0]
        return hit[1], hit[2], True
    lat, lon = dest_point(start_lat, start_lon, track_deg, fallback_nm)
    return lat, lon, False


def rate_one_radius_nm(gs_kt: float, rate_deg_sec: float = 3.0) -> float:
    omega = math.radians(max(float(rate_deg_sec), 0.1))
    return (max(float(gs_kt), 1.0) / 3600.0) / omega

# ===============================================================
# PROCEDURES JSON POINT CATALOG — SIDs/STARs only
# ===============================================================
@st.cache_data(show_spinner=False)
def load_procedures_file(path_str: str) -> Dict[str, Any]:
    path = Path(path_str)
    if not path.exists():
        return {"procedures": [], "points": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("procedures", [])
        data.setdefault("points", {})
        return data
    except Exception as exc:
        st.error(f"Erro ao ler {path.name}: {exc}")
        return {"procedures": [], "points": {}}


def available_procedures(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    data = load_procedures_file(str(PROC_FILE))
    procedures = data.get("procedures", [])
    procedures = [p for p in procedures if str(p.get("kind", "")).upper() in PROCEDURE_KINDS_ALLOWED]
    if kind:
        procedures = [p for p in procedures if str(p.get("kind", "")).upper() == kind.upper()]
    return procedures


def json_named_point(code: str) -> Optional[Dict[str, Any]]:
    code = clean_code(code)
    if not code:
        return None
    points = load_procedures_file(str(PROC_FILE)).get("points", {})
    raw = points.get(code) or points.get(code.upper())
    if not isinstance(raw, dict):
        return None
    if "lat" not in raw or "lon" not in raw:
        return None
    return {
        "code": code,
        "name": str(raw.get("name") or code),
        "lat": float(raw["lat"]),
        "lon": float(raw["lon"]),
        "alt": float(raw.get("alt", 0) or 0),
        "src": "PROC",
        "routes": "JSON points",
        "remarks": str(raw.get("remarks") or "procedure point"),
    }


@st.cache_data(show_spinner=False)
def load_procedure_point_catalog(path_str: str) -> pd.DataFrame:
    path = Path(path_str)
    cols = ["code", "name", "lat", "lon", "alt", "src", "routes", "remarks"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return pd.DataFrame(columns=cols)

    rows: List[Dict[str, Any]] = []

    def add_point(code: str, name: str, lat: float, lon: float, alt: float, routes: str, remarks: str = "") -> None:
        code = clean_code(code or name)
        if not code:
            return
        rows.append({
            "code": code,
            "name": name or code,
            "lat": float(lat),
            "lon": float(lon),
            "alt": float(alt or 0),
            "src": "PROC",
            "routes": routes,
            "remarks": remarks,
        })

    for code, point in data.get("points", {}).items():
        if isinstance(point, dict) and "lat" in point and "lon" in point:
            add_point(code, str(point.get("name") or code), float(point["lat"]), float(point["lon"]), float(point.get("alt", 0) or 0), "JSON points", str(point.get("remarks") or "procedure point"))

    for proc in data.get("procedures", []):
        kind = str(proc.get("kind", "")).upper()
        if kind not in PROCEDURE_KINDS_ALLOWED:
            continue
        proc_id = str(proc.get("id", "PROC"))
        for seg in proc.get("segments", []):
            typ = str(seg.get("type", "")).lower()
            code = clean_code(seg.get("point") or seg.get("code") or seg.get("name") or "")
            name = str(seg.get("name") or seg.get("note") or seg.get("point") or seg.get("code") or code)
            alt = float(seg.get("alt", 0) or 0)
            if "lat" in seg and "lon" in seg:
                add_point(code, name, float(seg["lat"]), float(seg["lon"]), alt, proc_id, "from SID/STAR segment")
                continue
            named = data.get("points", {}).get(code)
            if isinstance(named, dict) and "lat" in named and "lon" in named:
                add_point(code, str(named.get("name") or name), float(named["lat"]), float(named["lon"]), alt or float(named.get("alt", 0) or 0), proc_id, "from JSON named point")
                continue
            if typ in {"vor_radial_dme", "radial_to_dme"} and seg.get("vor") and seg.get("radial") is not None and seg.get("dme") is not None:
                vor = get_vor(str(seg["vor"]))
                if vor:
                    lat, lon = dest_point(vor["lat"], vor["lon"], float(seg["radial"]), float(seg["dme"]))
                    add_point(
                        code or f"{vor['ident']}R{int(float(seg['radial'])):03d}D{float(seg['dme']):g}",
                        name,
                        lat,
                        lon,
                        alt,
                        proc_id,
                        f"{vor['ident']} R{int(float(seg['radial'])):03d} D{float(seg['dme']):g}",
                    )

    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["code"] = df["code"].map(clean_code)
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    return df.dropna(subset=["code", "lat", "lon"]).drop_duplicates(subset=["code", "lat", "lon"]).reset_index(drop=True)


def point_catalog() -> pd.DataFrame:
    proc_points = load_procedure_point_catalog(str(PROC_FILE))
    if proc_points.empty:
        return POINTS_DF
    return pd.concat([POINTS_DF, proc_points], ignore_index=True).drop_duplicates(subset=["code", "lat", "lon", "src"]).reset_index(drop=True)

# ===============================================================
# POINT LOOKUP / ROUTE PARSER
# ===============================================================
def next_uid() -> int:
    st.session_state["next_uid"] = int(st.session_state.get("next_uid", 1)) + 1
    return int(st.session_state["next_uid"])


def proc_default_alt() -> float:
    return float(st.session_state.get("default_alt", 3000.0))


def df_row_to_point(row: pd.Series, alt: float = 0.0) -> Point:
    point = Point(
        code=clean_code(row.get("code")),
        name=str(row.get("name") or row.get("code")),
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        alt=float(alt if alt else row.get("alt", 0) or 0),
        src=str(row.get("src") or "DB"),
        routes=str(row.get("routes") or ""),
        remarks=str(row.get("remarks") or ""),
    )
    if point.src == "VOR":
        point.vor_pref = "FIXED"
        point.vor_ident = point.code
    return point


def db_point(code: str, alt: float = 0.0, src_priority: Optional[List[str]] = None) -> Optional[Point]:
    code = clean_code(code)
    catalog = point_catalog()
    if not code or catalog.empty or "code" not in catalog.columns:
        return None
    hit = catalog[catalog["code"].astype(str).str.upper().str.strip() == code].copy()
    if hit.empty:
        return None
    if src_priority and "src" in hit.columns:
        order = {str(src): i for i, src in enumerate(src_priority)}
        hit["__prio"] = hit["src"].astype(str).map(lambda src: order.get(src, 999))
        hit = hit.sort_values("__prio")
    return df_row_to_point(hit.iloc[0], alt)


def search_points(query: str, limit: int = 30, last: Optional[Point] = None) -> pd.DataFrame:
    q = query.strip().lower()
    catalog = point_catalog()
    if not q or catalog.empty:
        return catalog.head(0)
    mask = catalog.apply(lambda r: q in " ".join(str(v).lower() for v in r.values), axis=1)
    df = catalog[mask].copy()
    if df.empty:
        return df

    def score(row: pd.Series) -> float:
        code = str(row.get("code") or "").lower()
        name = str(row.get("name") or "").lower()
        sim = difflib.SequenceMatcher(None, q, f"{code} {name}").ratio()
        starts = 1.5 if code.startswith(q) or name.startswith(q) else 0.0
        exact = 3.0 if code == q else 0.0
        src_bonus = {"IFR": 0.35, "VOR": 0.30, "PROC": 0.28, "AD": 0.20, "VFR": 0.0}.get(str(row.get("src")), 0.0)
        near = 0.0
        if last:
            near = 1.0 / (1.0 + gc_dist_nm(last.lat, last.lon, float(row["lat"]), float(row["lon"])))
        return exact + starts + sim + src_bonus + near * 0.25

    df["_score"] = df.apply(score, axis=1)
    return df.sort_values("_score", ascending=False).head(limit)


def resolve_token(token: str, default_alt: float, last: Optional[Point] = None) -> Tuple[Optional[Point], str]:
    raw = token.strip()
    if not raw or raw.upper() == "DCT":
        return None, ""
    fix = make_vor_fix(raw)
    if fix:
        fix.alt = default_alt
        return fix, ""
    match = re.match(r"^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$", raw)
    if match:
        lat = float(match.group(1))
        lon = float(match.group(2))
        return Point(code="USERCOORD", name=f"{lat:.4f},{lon:.4f}", lat=lat, lon=lon, alt=default_alt, src="USER"), ""
    code = clean_code(raw)
    catalog = point_catalog()
    exact = catalog[catalog["code"].astype(str).str.upper() == code]
    if not exact.empty:
        priority = {"VOR": 0, "IFR": 1, "PROC": 2, "AD": 3, "VFR": 4}
        exact = exact.assign(_prio=exact["src"].map(lambda x: priority.get(str(x), 9))).sort_values("_prio")
        return df_row_to_point(exact.iloc[0], default_alt), ""
    fuzzy = search_points(raw, limit=1, last=last)
    if not fuzzy.empty and float(fuzzy.iloc[0].get("_score", 0)) >= 1.1:
        return df_row_to_point(fuzzy.iloc[0], default_alt), f"'{raw}' resolvido como {fuzzy.iloc[0]['code']}"
    return None, f"Não encontrei ponto: {raw}"


def list_airways() -> List[str]:
    if AIRWAYS_DF.empty:
        return []
    return sorted(AIRWAYS_DF["airway"].dropna().astype(str).str.upper().unique())


def expand_airway(airway: str, start_code: str, end_code: str, default_alt: float) -> Tuple[List[Point], str]:
    sub = AIRWAYS_DF[AIRWAYS_DF["airway"].astype(str).str.upper() == airway.upper()].sort_values("seq")
    if sub.empty:
        return [], f"Airway {airway} não existe no CSV."
    codes = [clean_code(x) for x in sub["point"].tolist()]
    start_code = clean_code(start_code)
    end_code = clean_code(end_code)
    if start_code not in codes or end_code not in codes:
        return [], f"{airway}: endpoints {start_code}/{end_code} não estão ambos na airway."
    i1 = codes.index(start_code)
    i2 = codes.index(end_code)
    chunk = sub.iloc[min(i1, i2): max(i1, i2) + 1]
    if i2 < i1:
        chunk = chunk.iloc[::-1]
    points: List[Point] = []
    for _, r in chunk.iterrows():
        points.append(Point(code=clean_code(r["point"]), name=clean_code(r["point"]), lat=float(r["lat"]), lon=float(r["lon"]), alt=default_alt, src="IFR", routes=airway))
    return points, ""


def parse_route_text(text: str, default_alt: float) -> Tuple[List[Point], List[str]]:
    tokens = [t.strip().upper() for t in re.split(r"[\s,;]+", text) if t.strip()]
    airway_set = set(list_airways())
    output: List[Point] = []
    notes: List[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "DCT":
            i += 1
            continue
        if i + 2 < len(tokens) and tokens[i + 1] in airway_set:
            p_start, msg1 = resolve_token(tokens[i], default_alt, output[-1] if output else None)
            p_end, msg2 = resolve_token(tokens[i + 2], default_alt, p_start)
            if msg1:
                notes.append(msg1)
            if msg2:
                notes.append(msg2)
            if p_start and p_end:
                expanded, msg = expand_airway(tokens[i + 1], p_start.code, p_end.code, default_alt)
                if expanded:
                    if not output or clean_code(output[-1].code) != clean_code(expanded[0].code):
                        output.append(expanded[0])
                    output.extend(expanded[1:])
                else:
                    notes.append(msg + " Usei DCT.")
                    if not output or clean_code(output[-1].code) != p_start.code:
                        output.append(p_start)
                    output.append(p_end)
            i += 3
            continue
        p, msg = resolve_token(tokens[i], default_alt, output[-1] if output else None)
        if msg:
            notes.append(msg)
        if p:
            output.append(p)
        i += 1
    for p in output:
        p.uid = next_uid()
    return output, notes

# ===============================================================
# PROCEDURE ENGINE — SIDs/STARs only
# ===============================================================
def make_proc_point(code: str, name: str, lat: float, lon: float, alt: float, *, src: str = "PROC", note: str = "", remarks: str = "", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    point = Point(code=clean_code(code) or "PROC", name=name or code or "PROC", lat=float(lat), lon=float(lon), alt=float(alt), src=src, remarks=remarks, uid=next_uid()).to_dict()
    point["navlog_note"] = note or name or code
    point["no_auto_vnav"] = True
    if extra:
        point.update(extra)
    return point


def proc_static_point(segment: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    code = clean_code(segment.get("point") or segment.get("code"))
    alt = float(segment.get("alt", proc_default_alt()))
    if "lat" in segment and "lon" in segment:
        return make_proc_point(code, segment.get("name") or segment.get("note") or code, float(segment["lat"]), float(segment["lon"]), alt, src="PROC", note=segment.get("note") or code, remarks=segment.get("remarks", "from procedures JSON"))
    named = json_named_point(code)
    if named:
        return make_proc_point(code, named.get("name", code), named["lat"], named["lon"], alt or float(named.get("alt", 0)), src="PROC", note=segment.get("note") or code, remarks=str(named.get("remarks", "from JSON points")))
    point = db_point(code, alt=alt, src_priority=["IFR", "VOR", "PROC", "AD", "VFR"])
    if point:
        d = point.to_dict()
        d["uid"] = next_uid()
        d["navlog_note"] = segment.get("note") or code
        d["no_auto_vnav"] = True
        return d
    raise ValueError(f"Ponto {code} não está nos CSV nem tem lat/lon no JSON.")


def proc_vor_radial_dme(segment: Dict[str, Any]) -> Dict[str, Any]:
    vor = get_vor(str(segment["vor"]))
    if not vor:
        raise ValueError(f"VOR {segment['vor']} não encontrado.")
    radial = float(segment["radial"])
    dme = float(segment["dme"])
    lat, lon = dest_point(vor["lat"], vor["lon"], radial, dme)
    note = segment.get("note") or f"{vor['ident']} R{int(radial):03d} D{dme:g}"
    return make_proc_point(segment.get("code") or note.replace(" ", ""), segment.get("name") or note, lat, lon, float(segment.get("alt", proc_default_alt())), note=note, remarks=f"{format_vor_id(vor)} R{int(radial):03d} D{dme:g}", extra={"vor_pref": "FIXED", "vor_ident": vor["ident"]})


def proc_runway_track_until_alt(segment: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    track = float(segment["track"])
    target_alt = float(segment["alt"])
    start_alt = float(previous.get("alt", segment.get("start_alt", 390)))
    delta_ft = max(0.0, target_alt - start_alt)
    wf, wk = wind_for_point(previous)
    _, _, gs = wind_triangle(track, float(st.session_state.climb_tas), wf, wk)
    minutes = delta_ft / max(float(st.session_state.roc_fpm), 1.0)
    dist_nm = max(0.05, gs * minutes / 60.0)
    lat, lon = dest_point(float(previous["lat"]), float(previous["lon"]), track, dist_nm)
    note = segment.get("note") or f"{int(target_alt)} TURN {segment.get('turn_arrow', '')} TRK{int(segment.get('next_track', track)):03d}".strip()
    return make_proc_point(segment.get("code") or note.replace(" ", ""), note, lat, lon, target_alt, src="PROC_DYNAMIC", note=note, remarks=f"ROC {float(st.session_state.roc_fpm):.0f} fpm, GS climb {gs:.0f} kt, dist {dist_nm:.2f} NM")


def proc_track_to_intercept_radial(segment: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    track = float(segment["track"])
    radial = float(segment["radial"])
    lat, lon, ok = track_intercept_radial(float(previous["lat"]), float(previous["lon"]), track, str(segment["vor"]), radial, float(segment.get("fallback_nm", 20.0)))
    note = segment.get("note") or f"INT {segment['vor']} R{int(radial):03d}"
    return make_proc_point(segment.get("code") or note.replace(" ", ""), note, lat, lon, float(segment.get("alt", previous.get("alt", proc_default_alt()))), src="PROC_DYNAMIC", note=note, remarks="Dynamic radial intercept" if ok else "Fallback point, no forward radial intercept", extra={"vor_pref": "FIXED", "vor_ident": clean_code(segment["vor"])})


def proc_track_to_intercept_dme(segment: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    track = float(segment["track"])
    dme = float(segment["dme"])
    lat, lon, ok = track_intercept_dme(float(previous["lat"]), float(previous["lon"]), track, str(segment["vor"]), dme, str(segment.get("choose", "first")), float(segment.get("fallback_nm", 20.0)))
    note = segment.get("note") or f"{segment['vor']} D{dme:g}"
    return make_proc_point(segment.get("code") or note.replace(" ", ""), note, lat, lon, float(segment.get("alt", previous.get("alt", proc_default_alt()))), src="PROC_DYNAMIC", note=note, remarks="Dynamic DME intercept" if ok else "Fallback point, no forward DME intercept", extra={"vor_pref": "FIXED", "vor_ident": clean_code(segment["vor"])})


def proc_dme_arc(segment: Dict[str, Any]) -> List[Dict[str, Any]]:
    points, msg = make_dme_arc_points(str(segment["vor"]), float(segment["dme"]), float(segment["start_radial"]), float(segment["end_radial"]), str(segment.get("direction", "CW")), float(segment.get("alt", proc_default_alt())))
    output: List[Dict[str, Any]] = []
    for p in points:
        d = p.to_dict()
        d["no_auto_vnav"] = True
        d["navlog_note"] = segment.get("note") or msg
        output.append(d)
    return output


def proc_rate_one_turn(segment: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    start_track = float(segment["start_track"])
    end_track = float(segment["end_track"])
    direction = str(segment.get("direction", "LEFT")).upper()
    wf, wk = wind_for_point(previous)
    _, _, gs = wind_triangle(start_track, float(st.session_state.cruise_tas), wf, wk)
    radius = rate_one_radius_nm(gs, float(segment.get("rate_deg_sec", 3.0)))
    center_bearing = wrap360(start_track - 90 if direction == "LEFT" else start_track + 90)
    center_lat, center_lon = dest_point(float(previous["lat"]), float(previous["lon"]), center_bearing, radius)
    end_radial = wrap360(end_track + 90 if direction == "LEFT" else end_track - 90)
    end_lat, end_lon = dest_point(center_lat, center_lon, end_radial, radius)
    note = segment.get("note") or f"RATE 1 {'←' if direction == 'LEFT' else '→'} TRK{int(end_track):03d}"
    return make_proc_point(segment.get("code") or note.replace(" ", ""), note, end_lat, end_lon, float(segment.get("alt", previous.get("alt", proc_default_alt()))), src="TURN", note=note, remarks=f"Rate one turn GS {gs:.0f} kt radius {radius:.2f} NM", extra={"turn_center_lat": center_lat, "turn_center_lon": center_lon, "turn_radius_nm": radius, "turn_start_course": start_track, "turn_end_course": end_track, "turn_direction": direction})


def build_procedure_points(proc_id: str, proc_instance_id: Optional[str] = None) -> List[Dict[str, Any]]:
    procedure = next((p for p in available_procedures() if p.get("id") == proc_id), None)
    if not procedure:
        raise ValueError(f"Procedimento {proc_id} não encontrado ou não é SID/STAR.")
    kind = str(procedure.get("kind", "")).upper()
    if kind not in PROCEDURE_KINDS_ALLOWED:
        raise ValueError(f"Procedimento {proc_id} ignorado: o app só carrega SID/STAR.")
    instance_id = proc_instance_id or f"{clean_code(proc_id)}-{next_uid()}"
    output: List[Dict[str, Any]] = []
    for segment_index, segment in enumerate(procedure.get("segments", [])):
        typ = str(segment.get("type", "")).lower()
        previous = output[-1] if output else None
        before_len = len(output)
        if typ == "static_point":
            output.append(proc_static_point(segment, previous))
        elif typ in {"vor_radial_dme", "radial_to_dme"}:
            output.append(proc_vor_radial_dme(segment))
        elif typ == "runway_track_until_alt":
            if not previous:
                raise ValueError(f"{proc_id}: runway_track_until_alt precisa de ponto anterior.")
            output.append(proc_runway_track_until_alt(segment, previous))
        elif typ == "track_to_intercept_radial":
            if not previous:
                raise ValueError(f"{proc_id}: track_to_intercept_radial precisa de ponto anterior.")
            output.append(proc_track_to_intercept_radial(segment, previous))
        elif typ == "track_to_intercept_dme":
            if not previous:
                raise ValueError(f"{proc_id}: track_to_intercept_dme precisa de ponto anterior.")
            output.append(proc_track_to_intercept_dme(segment, previous))
        elif typ == "dme_arc":
            output.extend(proc_dme_arc(segment))
        elif typ == "rate_one_turn":
            if not previous:
                raise ValueError(f"{proc_id}: rate_one_turn precisa de ponto anterior.")
            output.append(proc_rate_one_turn(segment, previous))
        else:
            raise ValueError(f"Tipo de segmento desconhecido: {typ}")
        for point in output[before_len:]:
            point["proc_id"] = proc_id
            point["proc_kind"] = kind
            point["proc_instance_id"] = instance_id
            point["proc_segment_index"] = segment_index
            point["proc_generated"] = True
    for order, point in enumerate(output):
        point["proc_id"] = proc_id
        point["proc_kind"] = kind
        point["proc_instance_id"] = instance_id
        point["proc_order"] = order
        point["proc_generated"] = True
    return output


def refresh_procedure_waypoints(wps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Regenera SIDs/STARs sem destruir alterações manuais do utilizador.

    O caso crítico é altitude: se alteras, por exemplo, NSA de 2000 para 4500
    num SID, o recálculo deixa de voltar ao valor original do JSON.
    Também preserva STOP/HOLD, vento local e texto do navlog quando existirem.
    """
    refreshed: List[Dict[str, Any]] = []
    i = 0
    while i < len(wps):
        point = wps[i]
        proc_id = point.get("proc_id")
        instance_id = point.get("proc_instance_id")
        if not proc_id or not instance_id:
            refreshed.append(point)
            i += 1
            continue

        block_start = i
        while i < len(wps) and wps[i].get("proc_instance_id") == instance_id:
            i += 1
        old_block = wps[block_start:i]

        # Preferimos casar por proc_order; se não existir, usamos a posição no bloco.
        old_by_order: Dict[int, Dict[str, Any]] = {}
        for pos, old in enumerate(old_block):
            order = int(old.get("proc_order", pos))
            old_by_order[order] = old

        try:
            new_block = build_procedure_points(str(proc_id), proc_instance_id=str(instance_id))
            for pos, new in enumerate(new_block):
                order = int(new.get("proc_order", pos))
                old = old_by_order.get(order)
                if not old:
                    continue
                # Preserva alterações manuais úteis.
                for key in ["alt", "stop_min", "wind_from", "wind_kt", "vor_pref", "vor_ident"]:
                    if key in old and old.get(key) not in {None, ""}:
                        new[key] = old.get(key)
                # Texto do navlog só é preservado se tiver sido mesmo editado.
                if old.get("navlog_note") and old.get("navlog_note") != new.get("navlog_note"):
                    new["navlog_note"] = old.get("navlog_note")
            refreshed.extend(new_block)
        except Exception as exc:
            st.warning(f"Não consegui regenerar {proc_id}: {exc}. Mantive os pontos antigos.")
            refreshed.extend(old_block)
    return refreshed

# ===============================================================
# ROUTE CALCULATION
# ===============================================================
def ensure_point_ids() -> None:
    for point in st.session_state.get("wps", []):
        if point.get("uid") is None:
            point["uid"] = next_uid()


def current_profile() -> Dict[str, float]:
    return {
        "climb_tas": float(st.session_state.climb_tas),
        "cruise_tas": float(st.session_state.cruise_tas),
        "descent_tas": float(st.session_state.descent_tas),
        "fuel_flow_lh": float(st.session_state.fuel_flow_lh),
        "taxi_fuel_l": float(st.session_state.taxi_fuel_l),
    }


def wind_for_point(point: Dict[str, Any]) -> Tuple[int, int]:
    if bool(st.session_state.use_global_wind):
        return int(st.session_state.wind_from), int(st.session_state.wind_kt)
    return int(point.get("wind_from") or st.session_state.wind_from), int(point.get("wind_kt") or st.session_state.wind_kt)


def build_route_nodes(user_wps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(user_wps) < 2:
        return []
    profile = current_profile()
    output: List[Dict[str, Any]] = []

    for i in range(len(user_wps) - 1):
        A = user_wps[i]
        B = user_wps[i + 1]
        output.append(A.copy())

        if A.get("no_auto_vnav") or B.get("no_auto_vnav") or is_dme_arc_leg(A, B) or is_rate_turn_leg(A, B):
            continue

        dist = gc_dist_nm(A["lat"], A["lon"], B["lat"], B["lon"])
        tc = gc_course_tc(A["lat"], A["lon"], B["lat"], B["lon"])
        wf, wk = wind_for_point(A)
        from_label = str(A.get("code") or A.get("name") or "FROM")
        to_label = str(B.get("code") or B.get("name") or "TO")

        if B["alt"] > A["alt"]:
            t_min = (B["alt"] - A["alt"]) / max(float(st.session_state.roc_fpm), 1.0)
            _, _, gs = wind_triangle(tc, profile["climb_tas"], wf, wk)
            d_need = gs * t_min / 60.0
            if 0.05 < d_need < dist - 0.05:
                lat, lon = point_along_gc(A["lat"], A["lon"], B["lat"], B["lon"], d_need)
                d_from = rd(d_need)
                d_to = rd(dist - d_need)
                p = Point(code="TOC", name="TOC", lat=lat, lon=lon, alt=B["alt"], src="CALC", uid=next_uid()).to_dict()
                p.update({
                    "navlog_note": chr(10).join(["TOC", f"+{d_from:.1f} {compact_nav_token(from_label)}", f"-{d_to:.1f} {compact_nav_token(to_label)}"]),
                    "calc_detail": f"{d_from:.1f} NM from {from_label} / {d_to:.1f} NM to {to_label}",
                    "calc_from_code": from_label,
                    "calc_to_code": to_label,
                    "calc_dist_from_prev": d_from,
                    "calc_dist_to_next": d_to,
                })
                output.append(p)

        elif B["alt"] < A["alt"]:
            t_min = (A["alt"] - B["alt"]) / max(float(st.session_state.rod_fpm), 1.0)
            _, _, gs = wind_triangle(tc, profile["descent_tas"], wf, wk)
            d_need = gs * t_min / 60.0
            if 0.05 < d_need < dist - 0.05:
                d_from = rd(max(0.0, dist - d_need))
                d_to = rd(d_need)
                lat, lon = point_along_gc(A["lat"], A["lon"], B["lat"], B["lon"], d_from)
                p = Point(code="TOD", name="TOD", lat=lat, lon=lon, alt=A["alt"], src="CALC", uid=next_uid()).to_dict()
                p.update({
                    "navlog_note": chr(10).join(["TOD", f"+{d_from:.1f} {compact_nav_token(from_label)}", f"-{d_to:.1f} {compact_nav_token(to_label)}"]),
                    "calc_detail": f"{d_from:.1f} NM from {from_label} / {d_to:.1f} NM to {to_label}",
                    "calc_from_code": from_label,
                    "calc_to_code": to_label,
                    "calc_dist_from_prev": d_from,
                    "calc_dist_to_next": d_to,
                })
                output.append(p)

    output.append(user_wps[-1].copy())
    return output


def tracking_instruction(A: Dict[str, Any], B: Dict[str, Any], preferred_vor: str = "") -> str:
    if B.get("leg_instruction"):
        return str(B.get("leg_instruction"))
    if B.get("navlog_note"):
        return str(B.get("navlog_note"))
    if is_dme_arc_leg(A, B):
        return f"ARC {A.get('arc_vor')} D{float(A.get('arc_radius_nm')):g} {A.get('arc_direction')}"
    if is_rate_turn_leg(A, B):
        arrow = "←" if str(B.get("turn_direction", "LEFT")).upper() == "LEFT" else "→"
        return f"RATE 1 {arrow} TRK{int(round(float(B.get('turn_end_course', 0)))):03d}"
    vor = get_vor(preferred_vor) if preferred_vor else None
    if not vor:
        mid_lat, mid_lon = point_along_gc(A["lat"], A["lon"], B["lat"], B["lon"], gc_dist_nm(A["lat"], A["lon"], B["lat"], B["lon"]) / 2)
        vor = nearest_vor(mid_lat, mid_lon)
    if not vor:
        return ""
    radial_a, dist_a = vor_radial_distance(vor, A["lat"], A["lon"])
    radial_b, dist_b = vor_radial_distance(vor, B["lat"], B["lon"])
    if dist_b < dist_a - 0.3:
        return f"INB {vor['ident']} R{radial_a:03d}"
    if dist_b > dist_a + 0.3:
        return f"OUTB {vor['ident']} R{radial_a:03d}"
    return f"X-RAD {vor['ident']} R{radial_a:03d}->R{radial_b:03d}"


def build_legs(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(nodes) < 2:
        return []
    profile = current_profile()
    base_dt = None
    if str(st.session_state.start_clock).strip():
        try:
            h, m = map(int, str(st.session_state.start_clock).strip().split(":"))
            base_dt = dt.datetime.combine(dt.date.today(), dt.time(h, m))
        except Exception:
            base_dt = None
    t_cursor = 0
    efob = max(0.0, float(st.session_state.start_efob) - profile["taxi_fuel_l"])
    legs: List[Dict[str, Any]] = []

    for i in range(len(nodes) - 1):
        A = nodes[i]
        B = nodes[i + 1]
        if is_dme_arc_leg(A, B):
            dist_raw = dme_arc_distance_nm(A, B)
            tc = dme_arc_course(A, B)
        elif is_rate_turn_leg(A, B):
            dist_raw = turn_distance_nm(A, B)
            tc = turn_course(A, B)
        else:
            dist_raw = gc_dist_nm(A["lat"], A["lon"], B["lat"], B["lon"])
            tc = gc_course_tc(A["lat"], A["lon"], B["lat"], B["lon"])
        dist = rd(dist_raw)
        wf, wk = wind_for_point(A)
        if B["alt"] > A["alt"] + 1:
            leg_profile, tas = "CLIMB", profile["climb_tas"]
        elif B["alt"] < A["alt"] - 1:
            leg_profile, tas = "DESCENT", profile["descent_tas"]
        else:
            leg_profile, tas = "LEVEL", profile["cruise_tas"]
        _, th, gs = wind_triangle(tc, tas, wf, wk)
        mh = apply_mag_var(th, float(st.session_state.mag_var), bool(st.session_state.mag_is_east))
        ete = rt((dist / max(gs, 1e-9)) * 3600.0) if gs > 0 and dist > 0 else 0
        burn = rf(profile["fuel_flow_lh"] * ete / 3600.0)

        hold_min = float(B.get("stop_min") or 0.0)
        hold_sec = rt(hold_min * 60.0) if hold_min > 0 else 0
        hold_dist = rd(gs * hold_sec / 3600.0) if hold_sec > 0 and gs > 0 else 0.0
        hold_burn = rf(profile["fuel_flow_lh"] * hold_sec / 3600.0) if hold_sec > 0 else 0.0

        efob_start = efob
        efob_after_leg = max(0.0, rf(efob_start - burn))
        efob_end = max(0.0, rf(efob_after_leg - hold_burn))
        clk_start = (base_dt + dt.timedelta(seconds=t_cursor)).strftime("%H:%M") if base_dt else f"T+{mmss(t_cursor)}"
        clk_arrive = (base_dt + dt.timedelta(seconds=t_cursor + ete)).strftime("%H:%M") if base_dt else f"T+{mmss(t_cursor + ete)}"
        clk_end = (base_dt + dt.timedelta(seconds=t_cursor + ete + hold_sec)).strftime("%H:%M") if base_dt else f"T+{mmss(t_cursor + ete + hold_sec)}"
        pref_vor = A.get("vor_ident") if A.get("vor_pref") == "FIXED" else ""
        legs.append({
            "i": len(legs) + 1,
            "A": A,
            "B": B,
            "profile": leg_profile,
            "TC": tc,
            "TH": th,
            "MH": mh,
            "TAS": tas,
            "GS": gs,
            "Dist": dist,
            "time_sec": ete,
            "burn": burn,
            "hold_sec": hold_sec,
            "hold_dist": hold_dist,
            "hold_burn": hold_burn,
            "hold_min": hold_min,
            "efob_start": efob_start,
            "efob_after_leg": efob_after_leg,
            "efob_end": efob_end,
            "clock_start": clk_start,
            "clock_arrive": clk_arrive,
            "clock_end": clk_end,
            "wind_from": wf,
            "wind_kt": wk,
            "tracking": tracking_instruction(A, B, pref_vor),
            "is_dme_arc": is_dme_arc_leg(A, B),
            "is_turn": is_rate_turn_leg(A, B),
        })
        t_cursor += ete + hold_sec
        efob = efob_end
    return legs


def recalc_route(refresh_procedures: bool = True) -> None:
    if refresh_procedures and st.session_state.get("wps"):
        st.session_state.wps = refresh_procedure_waypoints(st.session_state.wps)
        ensure_point_ids()
    st.session_state.route_nodes = build_route_nodes(st.session_state.wps)
    st.session_state.legs = build_legs(st.session_state.route_nodes)

# ===============================================================
# GIST ROUTES
# ===============================================================
def get_gist_credentials() -> Tuple[Optional[str], Optional[str]]:
    token = os.getenv("GITHUB_TOKEN")
    gist_id = os.getenv("ROUTES_GIST_ID")
    try:
        token = st.secrets.get("GITHUB_TOKEN", token)
        gist_id = st.secrets.get("ROUTES_GIST_ID", gist_id)
    except Exception:
        pass
    return token, gist_id


def serialize_route() -> List[Dict[str, Any]]:
    return [{k: v for k, v in point.items() if k not in {"uid"}} for point in st.session_state.wps]


def load_routes_from_gist() -> Dict[str, Any]:
    token, gist_id = get_gist_credentials()
    if not token or not gist_id or requests is None:
        return {}
    try:
        response = requests.get(f"https://api.github.com/gists/{gist_id}", headers={"Authorization": f"token {token}"}, timeout=10)
        if response.status_code != 200:
            return {}
        return json.loads(response.json().get("files", {}).get("routes.json", {}).get("content", "{}") or "{}")
    except Exception:
        return {}


def save_routes_to_gist(routes: Dict[str, Any]) -> Tuple[bool, str]:
    token, gist_id = get_gist_credentials()
    if not token or not gist_id or requests is None:
        return False, "Gist desativado."
    try:
        payload = {"files": {"routes.json": {"content": json.dumps(routes, indent=2)}}}
        response = requests.patch(f"https://api.github.com/gists/{gist_id}", headers={"Authorization": f"token {token}"}, json=payload, timeout=10)
        return response.status_code in {200, 201}, "Rotas guardadas." if response.status_code in {200, 201} else f"Erro Gist {response.status_code}"
    except Exception as exc:
        return False, str(exc)

# ===============================================================
# PDF HELPERS
# ===============================================================
def pdf_key_norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


PDF_ALIASES = {
    "FLIGHT_LEVEL_ALTITUDE": ["FLIGHT_LEVEL/ALTITUDE", "FLIGHT LEVEL / ALTITUDE", "FLIGHT LEVEL ALTITUDE", "FL_ALT"],
    "TEMP_ISA_DEV": ["TEMP/ISA_DEV", "TEMP / ISA DEV", "TEMP ISA DEV", "ISA_DEV"],
    "MAG_VAR": ["MAG_VAR", "MAG. VAR", "MAG VAR", "MAGVAR"],
    "WIND": ["WIND", "Wind"],
}


def expand_pdf_aliases(data: Dict[str, Any]) -> Dict[str, Any]:
    output = data.copy()
    norm_map = {pdf_key_norm(k): v for k, v in data.items()}
    for canonical, aliases in PDF_ALIASES.items():
        value = data.get(canonical)
        if value in {None, ""}:
            for alias in aliases:
                value = data.get(alias, norm_map.get(pdf_key_norm(alias)))
                if value not in {None, ""}:
                    break
        if value is not None:
            output[canonical] = value
            for alias in aliases:
                output[alias] = value
                output[pdf_key_norm(alias)] = value
    for k, v in list(output.items()):
        output[pdf_key_norm(k)] = v
    return output


def pdf_page_size(page: Any) -> Tuple[float, float]:
    media_box = page.MediaBox
    return float(media_box[2]) - float(media_box[0]), float(media_box[3]) - float(media_box[1])


def pdf_text_lines(text: str, width: float, size: float, max_lines: int = 3) -> List[str]:
    raw_lines = str(text).splitlines() or [str(text)]
    max_chars = max(3, int(width / max(size * 0.52, 1)))
    lines: List[str] = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            lines.append("")
            continue
        while len(raw) > max_chars and len(lines) < max_lines:
            cut = raw.rfind(" ", 0, max_chars)
            if cut < max_chars * 0.45:
                cut = max_chars
            lines.append(raw[:cut].strip())
            raw = raw[cut:].strip()
        if len(lines) < max_lines:
            lines.append(raw)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    return lines


def pdf_font_size_for_field(name: str, value: str, rect: List[float]) -> float:
    width = max(1.0, rect[2] - rect[0])
    height = max(1.0, rect[3] - rect[1])
    n = len(str(value).replace(chr(10), " "))
    if "Waypoint" in name:
        return 4.1 if n > 18 or chr(10) in str(value) else 4.8
    if any(x in name for x in ["Navaid", "Identifier", "Frequency"]):
        return 4.1 if n > 12 else 4.8
    if name in {"ETD/ETA", "OBSERVATIONS", "CLEARANCES"}:
        return 4.3 if n > 12 else 5.2
    if width < 18 or height < 9:
        return 4.2
    if n > 10:
        return 4.6
    return 5.4


def draw_pdf_field_text(canvas_obj: Any, name: str, rect: List[float], value: Any) -> None:
    text = str(value or "")
    if not text:
        return
    x1, y1, x2, y2 = [float(x) for x in rect]
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    size = pdf_font_size_for_field(name, text, [x1, y1, x2, y2])
    max_lines = 3 if "Waypoint" in name else 2 if chr(10) in text else 1
    if name in {"OBSERVATIONS", "CLEARANCES"}:
        max_lines = max(2, int(height / max(size * 1.15, 1)))
    lines = pdf_text_lines(text, width - 2.0, size, max_lines=max_lines)
    line_h = size * 1.12
    total_h = line_h * len(lines)
    y = y1 + (height - total_h) / 2 + (len(lines) - 1) * line_h + size * 0.20
    left_align = any(x in name for x in ["Waypoint", "OBSERVATIONS", "CLEARANCES", "Departure", "Arrival"])

    for line in lines:
        is_plus = line.strip().startswith("+")
        is_tod = "TOD" in line.strip().upper()
        if is_plus:
            canvas_obj.setFillColorRGB(0.72, 0.12, 0.12)
        elif is_tod:
            canvas_obj.setFillColorRGB(0.80, 0.08, 0.08)
        else:
            canvas_obj.setFillColorRGB(0, 0, 0)
        canvas_obj.setFont("Helvetica-Bold", size)
        if left_align:
            canvas_obj.drawString(x1 + 1.2, y, line)
        else:
            canvas_obj.drawCentredString((x1 + x2) / 2, y, line)
        y -= line_h
    canvas_obj.setFillColorRGB(0, 0, 0)


def stamp_non_field_navlog_headers(pdf: Any, data: Dict[str, Any], template: Path) -> None:
    try:
        from reportlab.pdfgen import canvas
    except Exception:
        return
    values = {
        "fl_alt": str(data.get("FLIGHT_LEVEL_ALTITUDE", "")),
        "wind": str(data.get("WIND", "")),
        "mag_var": str(data.get("MAG_VAR", "")),
        "temp_isa": str(data.get("TEMP_ISA_DEV", "")),
    }
    if not any(values.values()):
        return
    for page_index, page in enumerate(pdf.pages):
        page_width, page_height = pdf_page_size(page)
        is_cont = page_index > 0 or "_1" in template.stem
        ox = page_width / 2 if page_width > 650 and is_cont else 0
        cw = min(421, page_width - ox)
        y = 504 if is_cont else 367
        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=(page_width, page_height))
        c.setFont("Helvetica-Bold", 5.2)
        c.drawCentredString(ox + cw * 0.345, y, values["fl_alt"])
        c.drawCentredString(ox + cw * 0.572, y, values["wind"])
        c.drawCentredString(ox + cw * 0.766, y, values["mag_var"])
        c.drawCentredString(ox + cw * 0.925, y, values["temp_isa"])
        c.save()
        packet.seek(0)
        from pdfrw import PageMerge, PdfReader as _PdfReader
        PageMerge(page).add(_PdfReader(packet).pages[0]).render()


def stamp_pdf_form_values(pdf: Any, data: Dict[str, Any]) -> None:
    try:
        from reportlab.pdfgen import canvas
        from pdfrw import PageMerge, PdfReader as _PdfReader
    except Exception:
        return
    for page in pdf.pages:
        if not getattr(page, "Annots", None):
            continue
        page_width, page_height = pdf_page_size(page)
        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=(page_width, page_height))
        for annot in page.Annots:
            if annot.Subtype == PdfName("Widget") and annot.T and annot.Rect:
                key = str(annot.T)[1:-1]
                value = data.get(key, data.get(pdf_key_norm(key)))
                if value is not None and str(value) != "":
                    draw_pdf_field_text(c, key, [float(x) for x in annot.Rect], value)
        c.save()
        packet.seek(0)
        PageMerge(page).add(_PdfReader(packet).pages[0]).render()


def remove_pdf_widgets(pdf: Any) -> None:
    for page in pdf.pages:
        annots = getattr(page, "Annots", None)
        if not annots:
            continue
        kept = []
        for annot in annots:
            if annot.Subtype != PdfName("Widget"):
                kept.append(annot)
        if kept:
            page.Annots = kept
        else:
            page.Annots = []


def fill_pdf(template: Path, output_path: Path, data: Dict[str, Any], pages_to_keep: Optional[int] = None) -> Path:
    if PdfReader is None or PdfWriter is None or PdfDict is None or PdfName is None:
        raise RuntimeError("pdfrw não está instalado.")
    data = expand_pdf_aliases(data)
    pdf = PdfReader(str(template))
    if pages_to_keep is not None:
        pdf.pages = pdf.pages[:max(1, int(pages_to_keep))]
    if pdf.Root.AcroForm:
        pdf.Root.AcroForm.update(PdfDict(NeedAppearances=True))
    small_re = re.compile(r"(Waypoint|Navaid|Identifier|Frequency|Name|Lat|Long|Fix|ETA|OBSERVATIONS)", re.I)
    for page in pdf.pages:
        if not getattr(page, "Annots", None):
            continue
        for annot in page.Annots:
            if annot.Subtype == PdfName("Widget") and annot.T:
                key = str(annot.T)[1:-1]
                value = data.get(key, data.get(pdf_key_norm(key)))
                if value is not None:
                    annot.update(PdfDict(V=str(value), DV=str(value)))
                    if small_re.search(key):
                        annot.update(PdfDict(DA="/Helv 4 Tf 0 g"))
    # Stamping torna os valores visíveis em leitores que ignoram NeedAppearances
    # e permite texto pequeno/multilinha em fixes e ETD/ETA.
    stamp_pdf_form_values(pdf, data)
    stamp_non_field_navlog_headers(pdf, data, template)
    remove_pdf_widgets(pdf)
    PdfWriter(str(output_path), trailer=pdf).write()
    return output_path


def choose_vor_for_point(point: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    name = str(point.get("name", "")).upper()
    if name.startswith(("TOC", "TOD")):
        return None
    if point.get("vor_pref") == "NONE":
        return None
    if point.get("vor_pref") == "FIXED" and point.get("vor_ident"):
        return get_vor(str(point.get("vor_ident")))
    if point.get("src") == "VOR":
        return get_vor(str(point.get("code") or point.get("name")))
    return nearest_vor(float(point["lat"]), float(point["lon"]))


def aircraft_pdf_code(registration: str = "") -> str:
    reg = clean_code(registration)
    aircraft = str(st.session_state.get("aircraft_type", ""))
    if reg.startswith("OE") or "Piper" in aircraft or "PA-28" in aircraft or "PA28" in aircraft:
        return "PA28"
    if reg.startswith("CS") or "Tecnam" in aircraft or "P2008" in aircraft or "P208" in aircraft:
        return "P208"
    return aircraft or ""


def compact_nav_token(value: Any, max_len: int = 10) -> str:
    s = str(value or "").replace(chr(10), " ").strip().upper()
    if not s:
        return ""
    s = s.replace("TURNTRK", " T")
    s = s.replace("TURN TRK", " T")
    s = s.replace("TRK", "T")
    s = s.replace("INTNSA", "I NSA")
    s = s.replace("INT NSA", "I NSA")
    s = s.replace(" ", "") if len(s) > max_len else s
    return s[:max_len]


def pretty_pdf_waypoint_text(value: Any) -> str:
    lines = [str(x).strip() for x in str(value or "").splitlines() if str(x).strip()]
    if not lines:
        return ""
    first = lines[0]
    first = first.replace("TURNTRK", " TURN T")
    first = first.replace("TURN TRK", " TURN T")
    first = first.replace("INTNSA", "INT NSA R")
    first = first.replace("INT NSA", "INT NSA")
    out = [first[:14]]
    for line in lines[1:3]:
        out.append(line[:14])
    return chr(10).join(out)


def compact_pdf_waypoint(point: Dict[str, Any]) -> str:
    value = point.get("navlog_note") or point.get("code") or point.get("name")
    return pretty_pdf_waypoint_text(value)


def leg_hold_sec(leg: Dict[str, Any]) -> int:
    return int(leg.get("hold_sec") or 0)


def leg_hold_dist(leg: Dict[str, Any]) -> float:
    return float(leg.get("hold_dist") or 0.0)


def leg_hold_burn(leg: Dict[str, Any]) -> float:
    return float(leg.get("hold_burn") or 0.0)


def leg_total_time_sec(leg: Dict[str, Any]) -> int:
    return int(leg.get("time_sec") or 0) + leg_hold_sec(leg)


def leg_total_distance(leg: Dict[str, Any]) -> float:
    return float(leg.get("Dist") or 0.0) + leg_hold_dist(leg)


def leg_total_burn(leg: Dict[str, Any]) -> float:
    return float(leg.get("burn") or 0.0) + leg_hold_burn(leg)


def fmt_with_plus(base: str, plus: str, has_plus: bool) -> str:
    return base + chr(10) + "+" + plus if has_plus else base


def fill_leg_payload(data: Dict[str, Any], idx: int, leg: Dict[str, Any], acc_d: float, acc_t: int, prefix: str = "Leg") -> None:
    point = leg["B"]
    has_hold = leg_hold_sec(leg) > 0
    data[f"{prefix}{idx:02d}_Waypoint"] = compact_pdf_waypoint(point)
    data[f"{prefix}{idx:02d}_Altitude_FL"] = str(int(round(float(point.get("alt", 0)))))
    data[f"{prefix}{idx:02d}_True_Course"] = f"{int(round(leg['TC'])):03d}"
    data[f"{prefix}{idx:02d}_True_Heading"] = f"{int(round(leg['TH'])):03d}"
    data[f"{prefix}{idx:02d}_Magnetic_Heading"] = f"{int(round(leg['MH'])):03d}"
    data[f"{prefix}{idx:02d}_True_Airspeed"] = str(int(round(leg["TAS"])))
    data[f"{prefix}{idx:02d}_Ground_Speed"] = str(int(round(leg["GS"])))
    data[f"{prefix}{idx:02d}_Leg_Distance"] = fmt_with_plus(f"{float(leg['Dist']):.1f}", f"{leg_hold_dist(leg):.1f}", has_hold)
    data[f"{prefix}{idx:02d}_Cumulative_Distance"] = f"{acc_d:.1f}"
    data[f"{prefix}{idx:02d}_Leg_ETE"] = fmt_with_plus(pdf_time(leg["time_sec"]), pdf_time(leg_hold_sec(leg)), has_hold)
    data[f"{prefix}{idx:02d}_Cumulative_ETE"] = pdf_time(acc_t)
    data[f"{prefix}{idx:02d}_ETO"] = ""
    data[f"{prefix}{idx:02d}_Planned_Burnoff"] = fmt_with_plus(fmt_unit(leg["burn"]), fmt_unit(leg_hold_burn(leg)), has_hold)
    data[f"{prefix}{idx:02d}_Estimated_FOB"] = fmt_efob_numbers(leg["efob_end"])
    vor = choose_vor_for_point(point)
    data[f"{prefix}{idx:02d}_Navaid_Identifier"] = format_vor_id(vor)
    data[f"{prefix}{idx:02d}_Navaid_Frequency"] = format_radial_dist(vor, float(point["lat"]), float(point["lon"]))


def fill_total_payload(data: Dict[str, Any], idx: int, total_dist: float, total_sec: int, total_burn: float, final_efob: float, prefix: str = "Leg") -> None:
    data[f"{prefix}{idx:02d}_Waypoint"] = "TOTAL"
    data[f"{prefix}{idx:02d}_Navaid_Identifier"] = ""
    data[f"{prefix}{idx:02d}_Navaid_Frequency"] = ""
    data[f"{prefix}{idx:02d}_Altitude_FL"] = ""
    for field in ["True_Course", "True_Heading", "Magnetic_Heading", "True_Airspeed", "Ground_Speed"]:
        data[f"{prefix}{idx:02d}_{field}"] = ""
    data[f"{prefix}{idx:02d}_Leg_Distance"] = f"{total_dist:.1f}"
    data[f"{prefix}{idx:02d}_Cumulative_Distance"] = f"{total_dist:.1f}"
    data[f"{prefix}{idx:02d}_Leg_ETE"] = pdf_time(total_sec)
    data[f"{prefix}{idx:02d}_Cumulative_ETE"] = pdf_time(total_sec)
    data[f"{prefix}{idx:02d}_Planned_Burnoff"] = fmt_unit(total_burn)
    data[f"{prefix}{idx:02d}_Estimated_FOB"] = fmt_efob_numbers(final_efob)


def build_pdf_payload(
    legs: List[Dict[str, Any]],
    header: Dict[str, str],
    start: int = 0,
    count: int = PDF_FULL_TEMPLATE_LEG_ROWS,
    total_on_next_row: bool = False,
    fill_continuation_total: bool = True,
) -> Dict[str, Any]:
    chunk = legs[start:start + count]
    total_sec = sum(leg_total_time_sec(leg) for leg in legs)
    total_burn = rf(sum(leg_total_burn(leg) for leg in legs))
    total_dist = rd(sum(leg_total_distance(leg) for leg in legs))
    climb_sec = sum(leg_total_time_sec(leg) for leg in legs if leg["profile"] == "CLIMB")
    level_sec = sum(leg_total_time_sec(leg) for leg in legs if leg["profile"] == "LEVEL")
    desc_sec = sum(leg_total_time_sec(leg) for leg in legs if leg["profile"] == "DESCENT")
    climb_burn = rf(sum(leg_total_burn(leg) for leg in legs if leg["profile"] == "CLIMB"))
    final_efob = legs[-1]["efob_end"] if legs else float(st.session_state.start_efob)
    data = {
        "CALLSIGN": header.get("callsign", ""),
        "AIRCRAFT": aircraft_pdf_code(header.get("registration", "")),
        "AIRCRAFT_TYPE": aircraft_pdf_code(header.get("registration", "")),
        "REGISTRATION": header.get("registration", ""),
        "STUDENT": header.get("student", ""),
        "LESSON": header.get("lesson", ""),
        "INSTRUTOR": header.get("instructor", ""),
        "DEPT": header.get("dept_freq", ""),
        "ENROUTE": header.get("enroute_freq", ""),
        "ARRIVAL": header.get("arrival_freq", ""),
        "ETD/ETA": f"{header.get('etd', '')}/{header.get('eta', '')}".strip("/"),
        "Departure_Airfield": str(st.session_state.wps[0].get("code") or st.session_state.wps[0].get("name")) if st.session_state.wps else "",
        "Arrival_Airfield": str(st.session_state.wps[-1].get("code") or st.session_state.wps[-1].get("name")) if st.session_state.wps else "",
        "WIND": f"{int(st.session_state.wind_from):03d}/{int(st.session_state.wind_kt):02d}",
        "MAG_VAR": f"{fmt_num_clean(abs(float(st.session_state.mag_var)))}°{'E' if st.session_state.mag_is_east else 'W'}",
        "FLIGHT_LEVEL_ALTITUDE": header.get("fl_alt", ""),
        "TEMP_ISA_DEV": header.get("temp_isa", ""),
        "FLT TIME": pdf_time(total_sec),
        "CLIMB FUEL": fmt_unit(climb_burn),
        "OBSERVATIONS": f"Climb {pdf_time(climb_sec)} / Cruise {pdf_time(level_sec)} / Descent {pdf_time(desc_sec)}",
        "Leg_Number": str(len(legs)),
        "AIRCRAFT_MODEL": str(st.session_state.aircraft_type),
    }
    acc_d = 0.0
    acc_t = 0
    start_idx = 1 if start == 0 else 12
    for idx, leg in enumerate(chunk, start=start_idx):
        acc_d = rd(acc_d + leg_total_distance(leg))
        acc_t += int(leg_total_time_sec(leg))
        fill_leg_payload(data, idx, leg, acc_d, acc_t)
    if total_on_next_row and start == 0:
        fill_total_payload(data, start_idx + len(chunk), total_dist, total_sec, total_burn, final_efob)
    if fill_continuation_total:
        fill_total_payload(data, PDF_TOTAL_ROW_INDEX, total_dist, total_sec, total_burn, final_efob)
    return data


def legs_to_dataframe(legs: List[Dict[str, Any]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    acc_d = 0.0
    acc_t = 0
    for leg in legs:
        acc_d = rd(acc_d + leg_total_distance(leg))
        acc_t += leg_total_time_sec(leg)
        point = leg["B"]
        vor = choose_vor_for_point(point)
        to_label = point.get("navlog_note") or point.get("code") or point.get("name")
        if point.get("calc_detail"):
            to_label = f"{point.get('code')} · {point.get('calc_detail')}"
        rows.append({
            "Leg": leg["i"],
            "From": leg["A"].get("code") or leg["A"].get("name"),
            "To": to_label,
            "Profile": leg["profile"],
            "Alt": int(round(float(point.get("alt", 0)))),
            "TC": f"{int(round(leg['TC'])):03d}",
            "TH": f"{int(round(leg['TH'])):03d}",
            "MH": f"{int(round(leg['MH'])):03d}",
            "TAS": int(round(leg["TAS"])),
            "GS": int(round(leg["GS"])),
            "Dist": f"{float(leg['Dist']):.1f}",
            "Hold Dist": f"+{leg_hold_dist(leg):.1f}" if leg_hold_sec(leg) else "",
            "CumDist": f"{acc_d:.1f}",
            "ETE": pdf_time(leg["time_sec"]),
            "Hold ETE": f"+{pdf_time(leg_hold_sec(leg))}" if leg_hold_sec(leg) else "",
            "CumETE": pdf_time(acc_t),
            "Fuel": fmt_unit(leg["burn"]),
            "Hold Fuel": f"+{fmt_unit(leg_hold_burn(leg))}" if leg_hold_sec(leg) else "",
            "EFOB": fmt_efob_numbers(leg["efob_end"]),
            "Wind": f"{int(leg['wind_from']):03d}/{int(leg['wind_kt'])}",
            "VOR": format_vor_id(vor),
            "Radial/Dist": format_radial_dist(vor, float(point["lat"]), float(point["lon"])),
            "Tracking": leg.get("tracking", ""),
        })
    return pd.DataFrame(rows)


def style_navlog_dataframe(df: pd.DataFrame):
    def row_style(row: pd.Series) -> List[str]:
        styles = [""] * len(row)
        to_text = str(row.get("To", "")).upper()
        has_hold = bool(str(row.get("Hold ETE", "")).strip())
        if "TOD" in to_text:
            styles = ["background-color: #fff1f2"] * len(row)
        if has_hold:
            styles = ["background-color: #fff7ed"] * len(row)
        return styles
    return df.style.apply(row_style, axis=1)


def route_item15(wps: List[Dict[str, Any]]) -> str:
    if len(wps) < 2:
        return ""
    seq = wps[:]
    if re.fullmatch(r"[A-Z]{4}", clean_code(seq[0].get("code"))):
        seq = seq[1:]
    if seq and re.fullmatch(r"[A-Z]{4}", clean_code(seq[-1].get("code"))):
        seq = seq[:-1]
    tokens: List[str] = []
    for point in seq:
        if str(point.get("src", "")).upper() in {"CALC", "PROC_DYNAMIC", "TURN"}:
            continue
        code = clean_code(point.get("code") or point.get("name"))
        if not code or (str(point.get("src", "")).upper() == "USER" and code.startswith("WP")):
            code = dd_to_icao(float(point["lat"]), float(point["lon"]))
        tokens.append(code)
    return "DCT " + " DCT ".join(tokens) if tokens else ""


def summary_metrics(legs: List[Dict[str, Any]]) -> Dict[str, float]:
    return {
        "time": sum(leg_total_time_sec(leg) for leg in legs),
        "dist": rd(sum(leg_total_distance(leg) for leg in legs)),
        "burn": rf(sum(leg_total_burn(leg) for leg in legs)),
        "efob": legs[-1]["efob_end"] if legs else float(st.session_state.start_efob),
        "legs": len(legs),
    }


def html_pills(items: Iterable[Tuple[str, str]]) -> None:
    st.markdown("".join([f"<span class='pill {klass}'>{label}</span>" for label, klass in items]), unsafe_allow_html=True)

# ===============================================================
# MAP
# ===============================================================
def map_start_center() -> Tuple[float, float]:
    catalog = point_catalog()
    hit = catalog[catalog["code"].astype(str).str.upper() == "LPSO"] if not catalog.empty else pd.DataFrame()
    if not hit.empty:
        return float(hit.iloc[0]["lat"]), float(hit.iloc[0]["lon"])
    return LPSO_FALLBACK_CENTER


def make_base_map() -> folium.Map:
    # Arranca centrado em LPSO, com zoom suficiente para ver a zona de Ponte de Sor
    # e grande parte de Portugal continental sem ficar demasiado afastado.
    m = folium.Map(location=map_start_center(), zoom_start=8, tiles=None, control_scale=True, prefer_canvas=True)
    folium.TileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", name="OSM", attr="© OpenStreetMap").add_to(m)
    folium.TileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", name="OpenTopoMap", attr="© OpenTopoMap").add_to(m)
    folium.TileLayer("https://services.arcgisonline.com/ArcGIS/rest/services/World_Hillshade/MapServer/tile/{z}/{y}/{x}", name="Hillshade", attr="© Esri").add_to(m)
    # Não usar fit_bounds aqui; isso anulava o zoom inicial e abria o mapa demasiado afastado.
    token = get_openaip_token()
    if bool(st.session_state.get("show_openaip", True)) and token:
        folium.TileLayer(
            tiles="https://{s}.api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.png?apiKey=" + token,
            attr="© openAIP",
            name="openAIP",
            overlay=True,
            control=True,
            subdomains="abc",
            opacity=float(st.session_state.get("openaip_opacity", 0.65)),
            max_zoom=20,
        ).add_to(m)
    Fullscreen(position="topleft", title="Fullscreen").add_to(m)
    MeasureControl(position="topleft", primary_length_unit="nautical_miles").add_to(m)
    return m


def add_div_marker(m: folium.Map, lat: float, lon: float, html: str) -> None:
    folium.Marker((lat, lon), icon=folium.DivIcon(html=html, icon_size=(0, 0))).add_to(m)


def render_route_map(wps: List[Dict[str, Any]], nodes: List[Dict[str, Any]], legs: List[Dict[str, Any]], key: str = "mainmap") -> Dict[str, Any]:
    m = make_base_map()
    if bool(st.session_state.show_ref_points):
        cluster = MarkerCluster(name="Pontos IFR/VFR/VOR/PROC", disableClusteringAtZoom=10).add_to(m)
        src_filter = set(st.session_state.ref_layers)
        ref_all = point_catalog()
        ref = ref_all[ref_all["src"].isin(src_filter)] if src_filter and not ref_all.empty else ref_all.head(0)
        for _, r in ref.iterrows():
            src = str(r.get("src"))
            color = {"IFR": "#2563eb", "VOR": "#dc2626", "AD": "#111827", "VFR": "#16a34a", "PROC": "#9333ea"}.get(src, "#334155")
            folium.CircleMarker((float(r["lat"]), float(r["lon"])), radius=4 if src in {"IFR", "VOR", "PROC"} else 3, color=color, weight=1, fill=True, fill_opacity=0.9, tooltip=f"[{src}] {r.get('code')} — {r.get('name')} {r.get('routes', '')}").add_to(cluster)
    if bool(st.session_state.show_airways) and not AIRWAYS_DF.empty:
        for airway, grp in AIRWAYS_DF.groupby("airway"):
            pts = [(float(r["lat"]), float(r["lon"])) for _, r in grp.sort_values("seq").iterrows()]
            if len(pts) >= 2:
                folium.PolyLine(pts, color="#64748b", weight=2, opacity=0.55, tooltip=airway).add_to(m)
    for leg in legs:
        if leg["profile"] == "STOP":
            continue
        color = PROFILE_COLORS.get(leg["profile"], "#7c3aed")
        if leg.get("is_dme_arc"):
            latlngs = dme_arc_polyline(leg["A"], leg["B"])
        elif leg.get("is_turn"):
            latlngs = turn_polyline(leg["A"], leg["B"])
        else:
            latlngs = [(leg["A"]["lat"], leg["A"]["lon"]), (leg["B"]["lat"], leg["B"]["lon"])]
        folium.PolyLine(latlngs, color="#ffffff", weight=8, opacity=1).add_to(m)
        folium.PolyLine(latlngs, color=color, weight=4, opacity=1, tooltip=f"L{leg['i']} {leg['profile']} {pdf_time(leg['time_sec'])}").add_to(m)
    for idx, point in enumerate(wps, start=1):
        lat, lon = float(point["lat"]), float(point["lon"])
        src = point.get("src", "USER")
        color = {"IFR": "#2563eb", "VOR": "#dc2626", "AD": "#111827", "VFR": "#16a34a", "USER": "#f97316", "VORFIX": "#be123c", "DMEARC": "#0891b2", "PROC": "#9333ea", "PROC_DYNAMIC": "#9333ea", "TURN": "#9333ea"}.get(src, "#0f172a")
        folium.CircleMarker((lat, lon), radius=6, color="#fff", weight=3, fill=True, fill_opacity=1).add_to(m)
        folium.CircleMarker((lat, lon), radius=5, color=color, fill=True, fill_opacity=1, tooltip=f"{idx}. {point.get('code') or point.get('name')} [{src}]").add_to(m)
        label = point.get("navlog_note") or point.get("code") or point.get("name")
        label_html = str(label).replace(chr(10), "<br><span style='font-size:10px;font-weight:700'>")
        extra_close = "</span>" if chr(10) in str(label) else ""
        label_color = "#be123c" if clean_code(point.get("code")) == "TOD" else "#0f172a"
        add_div_marker(m, lat, lon, f"<div style='transform:translate(8px,-22px);font-weight:800;font-size:12px;color:{label_color};text-shadow:-1px -1px 0 white,1px -1px 0 white,-1px 1px 0 white,1px 1px 0 white;white-space:nowrap'>{idx}. {label_html}{extra_close}</div>")
    folium.LayerControl(collapsed=False).add_to(m)
    return st_folium(m, width=None, height=720, key=key)

# ===============================================================
# STATE INIT
# ===============================================================
ss("next_uid", 1)
ss("aircraft_type", "Piper PA-28")
if "climb_tas" not in st.session_state:
    prof = AIRCRAFT_PROFILES[st.session_state.aircraft_type]
    st.session_state.climb_tas = prof["climb_tas"]
    st.session_state.cruise_tas = prof["cruise_tas"]
    st.session_state.descent_tas = prof["descent_tas"]
    st.session_state.fuel_flow_lh = prof["fuel_flow_lh"]
    st.session_state.taxi_fuel_l = prof["taxi_fuel_l"]
ss("wind_from", 0)
ss("wind_kt", 0)
ss("use_global_wind", True)
ss("mag_var", 1.0)
ss("mag_is_east", False)
ss("roc_fpm", 600)
ss("rod_fpm", 500)
ss("start_efob", 180.0)
ss("start_clock", "")
ss("default_alt", 3000.0)
ss("wps", [])
ss("route_nodes", [])
ss("legs", [])
ss("show_ref_points", True)
ss("ref_layers", ["IFR", "VOR", "AD", "VFR", "PROC"])
ss("show_airways", True)
ss("show_openaip", True)
ss("openaip_opacity", 0.65)
ss("saved_routes", {})
ensure_point_ids()

# ===============================================================
# UI HEADER
# ===============================================================
st.markdown(f"<div class='nav-hero'><div class='nav-title'>🧭 {APP_TITLE}</div><div class='nav-sub'>{APP_SUBTITLE}</div></div>", unsafe_allow_html=True)

if st.session_state.legs:
    sm = summary_metrics(st.session_state.legs)
    html_pills([
        (f"ETE {pdf_time(sm['time'])}", "pill-good"),
        (f"Dist {sm['dist']:.1f} NM", "pill-good"),
        (f"Fuel {fmt_unit(sm['burn'])} L", "pill-good"),
        (f"EFOB final {fmt_efob_numbers(sm['efob'])}", "pill-good" if sm["efob"] >= 30 else "pill-warn"),
        (f"{sm['legs']} legs", ""),
    ])
else:
    catalog = point_catalog()
    procedures_all = available_procedures()
    sid_count = len([p for p in procedures_all if str(p.get("kind", "")).upper() == "SID"])
    star_count = len([p for p in procedures_all if str(p.get("kind", "")).upper() == "STAR"])
    html_pills([
        (f"{len(catalog[catalog.src == 'IFR']) if 'src' in catalog.columns else 0} IFR pts", ""),
        (f"{len(catalog[catalog.src == 'PROC']) if 'src' in catalog.columns else 0} PROC pts", ""),
        (f"{sid_count} SIDs", "pill-good" if sid_count else "pill-warn"),
        (f"{star_count} STARs", "pill-good" if star_count else "pill-warn"),
        (f"{len(AIRWAYS_DF.airway.unique()) if not AIRWAYS_DF.empty else 0} airways", ""),
        (f"{len(VOR_DF)} VOR", ""),
        ("procedures_lpso.json OK" if PROC_FILE.exists() else "procedures_lpso.json em falta", "pill-good" if PROC_FILE.exists() else "pill-warn"),
    ])

# ===============================================================
# SETUP
# ===============================================================
st.markdown("#### 1 · Setup do voo")
setup_a, setup_b, setup_c, setup_d = st.columns([1.15, 1.1, 1.1, 0.8], gap="large")
with setup_a:
    ac_names = list(AIRCRAFT_PROFILES)
    ac = st.selectbox("Aeronave", ac_names, index=ac_names.index(st.session_state.aircraft_type) if st.session_state.aircraft_type in ac_names else 0)
    if ac != st.session_state.aircraft_type:
        st.session_state.aircraft_type = ac
        prof = AIRCRAFT_PROFILES[ac]
        st.session_state.climb_tas = prof["climb_tas"]
        st.session_state.cruise_tas = prof["cruise_tas"]
        st.session_state.descent_tas = prof["descent_tas"]
        st.session_state.fuel_flow_lh = prof["fuel_flow_lh"]
        st.session_state.taxi_fuel_l = prof["taxi_fuel_l"]
        st.rerun()
    st.number_input("EFOB inicial (L)", 0.0, 300.0, key="start_efob", step=1.0)
    st.text_input("Hora off-blocks / start (HH:MM)", key="start_clock")
with setup_b:
    b1, b2 = st.columns(2)
    with b1:
        st.number_input("TAS subida", 30.0, 250.0, key="climb_tas", step=1.0)
        st.number_input("TAS descida", 30.0, 250.0, key="descent_tas", step=1.0)
        st.number_input("ROC ft/min", 100, 2000, key="roc_fpm", step=50)
    with b2:
        st.number_input("TAS cruzeiro", 30.0, 300.0, key="cruise_tas", step=1.0)
        st.number_input("Consumo L/h", 1.0, 200.0, key="fuel_flow_lh", step=0.5)
        st.number_input("ROD ft/min", 100, 2000, key="rod_fpm", step=50)
with setup_c:
    c1, c2 = st.columns(2)
    with c1:
        st.number_input("Wind FROM (°T)", 0, 360, key="wind_from")
        st.number_input("Mag var (°)", -30.0, 30.0, key="mag_var", step=0.1)
    with c2:
        st.number_input("Wind kt", 0, 200, key="wind_kt")
        st.toggle("Variação EAST", key="mag_is_east")
    st.toggle("Usar vento global", key="use_global_wind")
    st.number_input("Altitude default novos pontos", 0.0, 45000.0, key="default_alt", step=100.0)
with setup_d:
    st.number_input("Taxi fuel (L)", 0.0, 30.0, key="taxi_fuel_l", step=0.5)
    st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    if st.button("Recalcular navlog", type="primary", use_container_width=True):
        recalc_route(refresh_procedures=True)
        st.session_state["_last_calc_sig"] = None
        st.toast("Rota recalculada")


def calculation_signature() -> str:
    return json.dumps({
        "aircraft_type": st.session_state.aircraft_type,
        "climb_tas": float(st.session_state.climb_tas),
        "cruise_tas": float(st.session_state.cruise_tas),
        "descent_tas": float(st.session_state.descent_tas),
        "fuel_flow_lh": float(st.session_state.fuel_flow_lh),
        "taxi_fuel_l": float(st.session_state.taxi_fuel_l),
        "roc_fpm": int(st.session_state.roc_fpm),
        "rod_fpm": int(st.session_state.rod_fpm),
        "wind_from": int(st.session_state.wind_from),
        "wind_kt": int(st.session_state.wind_kt),
        "use_global_wind": bool(st.session_state.use_global_wind),
        "mag_var": float(st.session_state.mag_var),
        "mag_is_east": bool(st.session_state.mag_is_east),
        "start_efob": float(st.session_state.start_efob),
        "start_clock": str(st.session_state.start_clock),
    }, sort_keys=True)


_current_calc_sig = calculation_signature()
if st.session_state.get("_last_calc_sig") != _current_calc_sig and st.session_state.get("wps"):
    recalc_route(refresh_procedures=True)
    st.session_state["_last_calc_sig"] = _current_calc_sig

st.markdown("<hr>", unsafe_allow_html=True)

# ===============================================================
# TABS
# ===============================================================
tab_route, tab_map, tab_navlog = st.tabs(["1 · Rota", "2 · Mapa / clique", "3 · Navlog / PDF"])

with tab_route:
    st.markdown("#### 2 · Construir rota")
    left, right = st.columns([1.05, 0.95], gap="large")
    with left:
        st.markdown("#### Rota por texto")
        route_text = st.text_area("Rota", height=92, placeholder="LPSO NSA MAGUM PORCA TRAMA SALTE MENDA")
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Substituir rota", type="primary", use_container_width=True):
                pts, notes = parse_route_text(route_text, float(st.session_state.default_alt))
                st.session_state.wps = [p.to_dict() for p in pts]
                recalc_route(refresh_procedures=False)
                st.session_state["_last_calc_sig"] = calculation_signature()
                for note in notes:
                    st.warning(note)
        with c2:
            if st.button("Acrescentar", use_container_width=True):
                pts, notes = parse_route_text(route_text, float(st.session_state.default_alt))
                st.session_state.wps.extend([p.to_dict() for p in pts])
                recalc_route(refresh_procedures=False)
                st.session_state["_last_calc_sig"] = calculation_signature()
                for note in notes:
                    st.warning(note)
        with c3:
            if st.button("Limpar", use_container_width=True):
                st.session_state.wps = []
                st.session_state.route_nodes = []
                st.session_state.legs = []
                st.rerun()

        st.markdown("#### Pesquisa")
        q = st.text_input("Pesquisar por código/nome/rota", placeholder="MAGUM, ATECA, PORCA, TRAMA, SALTE, MENDA, NSA, LPSO…")
        results = search_points(q, limit=12, last=Point.from_dict(st.session_state.wps[-1]) if st.session_state.wps else None)
        for i, r in results.iterrows():
            cols = st.columns([0.14, 0.60, 0.16, 0.10])
            with cols[0]:
                st.markdown(f"`{r['src']}`")
            with cols[1]:
                st.markdown(f"**{r['code']}** — {r['name']}")
                st.caption(f"{float(r['lat']):.5f}, {float(r['lon']):.5f} · {r.get('routes', '')}")
            with cols[2]:
                alt = st.number_input("Alt", 0.0, 45000.0, float(st.session_state.default_alt), 100.0, key=f"alt_search_{i}", label_visibility="collapsed")
            with cols[3]:
                if st.button("➕", key=f"add_search_{i}", use_container_width=True):
                    p = df_row_to_point(r, alt)
                    p.uid = next_uid()
                    st.session_state.wps.append(p.to_dict())
                    recalc_route(refresh_procedures=True)
                    st.session_state["_last_calc_sig"] = calculation_signature()
                    st.rerun()

        st.markdown("#### Fix VOR / arco DME")
        fix_c1, fix_c2, fix_c3 = st.columns([1.5, 0.8, 0.8])
        with fix_c1:
            radial_token = st.text_input("Fix VOR radial/distância", placeholder="CAS/R180/D12")
        with fix_c2:
            radial_alt = st.number_input("Alt fix", 0.0, 45000.0, float(st.session_state.default_alt), step=100.0, key="vorfix_alt")
        with fix_c3:
            st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
            if st.button("Adicionar fix", use_container_width=True):
                p = make_vor_fix(radial_token)
                if not p:
                    st.error("Formato inválido ou VOR desconhecido.")
                else:
                    p.alt = float(radial_alt)
                    p.uid = next_uid()
                    st.session_state.wps.append(p.to_dict())
                    recalc_route(refresh_procedures=True)
                    st.session_state["_last_calc_sig"] = calculation_signature()
                    st.rerun()

        vor_idents = sorted([str(x) for x in VOR_DF["ident"].dropna().unique()]) if not VOR_DF.empty else []
        arc_c1, arc_c2, arc_c3, arc_c4, arc_c5, arc_c6 = st.columns([0.9, 0.8, 0.8, 0.8, 0.8, 1.2])
        with arc_c1:
            arc_vor = st.selectbox("VOR arco", vor_idents, key="arc_vor") if vor_idents else st.text_input("VOR arco", "CAS")
        with arc_c2:
            arc_radius = st.number_input("DME NM", 1.0, 80.0, 12.0, step=0.5, key="arc_radius")
        with arc_c3:
            arc_start = st.number_input("Radial início", 0, 359, 180, key="arc_start")
        with arc_c4:
            arc_end = st.number_input("Radial fim", 0, 359, 240, key="arc_end")
        with arc_c5:
            arc_dir = st.selectbox("Sentido", ["CW", "CCW"], key="arc_dir")
        with arc_c6:
            arc_alt = st.number_input("Alt arco", 0.0, 45000.0, float(st.session_state.default_alt), step=100.0, key="arc_alt")
        if st.button("Adicionar arco DME à rota", use_container_width=True):
            pts, msg = make_dme_arc_points(str(arc_vor), float(arc_radius), float(arc_start), float(arc_end), str(arc_dir), float(arc_alt))
            if not pts:
                st.error(msg)
            else:
                st.session_state.wps.extend([p.to_dict() for p in pts])
                recalc_route(refresh_procedures=True)
                st.session_state["_last_calc_sig"] = calculation_signature()
                st.success(msg)
                st.rerun()

        st.markdown("#### Procedimentos externos — só SID / STAR")
        procedures = available_procedures()
        if not procedures:
            st.warning("Coloca procedures_lpso.json na raiz do repo. O app só carrega procedimentos com kind SID ou STAR.")
        else:
            kinds = sorted(set(str(p.get("kind", "PROC")).upper() for p in procedures))
            kind = st.selectbox("Tipo", kinds)
            choices = [p for p in procedures if str(p.get("kind", "")).upper() == kind]
            labels = [f"{p.get('id')} — {p.get('name', '')}" for p in choices]
            selected = st.selectbox("Procedimento", labels)
            mode = st.selectbox("Inserção", ["Acrescentar", "Substituir rota"])
            if st.button("Adicionar SID/STAR", type="primary", use_container_width=True):
                proc_id = selected.split(" — ")[0]
                try:
                    pts = build_procedure_points(proc_id)
                    if mode == "Substituir rota":
                        st.session_state.wps = pts
                    else:
                        st.session_state.wps.extend(pts)
                    recalc_route(refresh_procedures=False)
                    st.session_state["_last_calc_sig"] = calculation_signature()
                    st.success(f"{proc_id} adicionado ({len(pts)} pontos).")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro ao gerar procedimento: {exc}")

        st.markdown("#### Rotas padrão")
        if not st.session_state.saved_routes:
            st.session_state.saved_routes = load_routes_from_gist()
        routes = st.session_state.saved_routes
        rg1, rg2 = st.columns(2)
        with rg1:
            save_name = st.text_input("Guardar rota atual como", "")
            if st.button("Guardar", use_container_width=True):
                if not save_name.strip():
                    st.warning("Dá um nome à rota.")
                elif not st.session_state.wps:
                    st.warning("Não há rota para guardar.")
                else:
                    routes[save_name.strip()] = serialize_route()
                    ok, msg = save_routes_to_gist(routes)
                    st.session_state.saved_routes = routes
                    st.success(msg) if ok else st.warning(msg)
        with rg2:
            names = sorted(routes.keys())
            choice = st.selectbox("Carregar rota", [""] + names)
            l1, l2 = st.columns(2)
            with l1:
                if choice and st.button("Carregar", use_container_width=True):
                    st.session_state.wps = []
                    for item in routes.get(choice, []):
                        p = Point.from_dict(item)
                        p.uid = next_uid()
                        st.session_state.wps.append(p.to_dict())
                    recalc_route(refresh_procedures=True)
                    st.session_state["_last_calc_sig"] = calculation_signature()
                    st.rerun()
            with l2:
                if choice and st.button("Apagar", use_container_width=True):
                    routes.pop(choice, None)
                    ok, msg = save_routes_to_gist(routes)
                    st.session_state.saved_routes = routes
                    st.success(msg) if ok else st.warning(msg)

    with right:
        st.markdown("#### Waypoints")
        ensure_point_ids()
        remove_idx: Optional[int] = None
        move: Optional[Tuple[int, int]] = None
        if not st.session_state.wps:
            st.info("Ainda não há waypoints.")
        for idx, point in enumerate(st.session_state.wps):
            with st.expander(f"{idx + 1:02d} · {point.get('navlog_note') or point.get('code') or point.get('name')} · {point.get('src', '')}", expanded=False):
                c1, c2 = st.columns([2, 1])
                with c1:
                    point["code"] = st.text_input("Código", point.get("code") or point.get("name") or "WP", key=f"wp_code_{point['uid']}").upper()
                    point["name"] = st.text_input("Nome", point.get("name") or point.get("code") or "WP", key=f"wp_name_{point['uid']}")
                    if point.get("navlog_note") is not None:
                        point["navlog_note"] = st.text_input("Texto no NAVLOG", point.get("navlog_note", ""), key=f"wp_note_{point['uid']}")
                with c2:
                    point["alt"] = st.number_input("Alt ft", 0.0, 45000.0, float(point.get("alt", 0)), step=50.0, key=f"wp_alt_{point['uid']}")
                    point["stop_min"] = st.number_input("HOLD/STOP min", 0.0, 480.0, float(point.get("stop_min", 0)), step=1.0, key=f"wp_stop_{point['uid']}")
                c1, c2 = st.columns(2)
                with c1:
                    point["lat"] = st.number_input("Lat", -90.0, 90.0, float(point.get("lat")), step=0.0001, format="%.6f", key=f"wp_lat_{point['uid']}")
                with c2:
                    point["lon"] = st.number_input("Lon", -180.0, 180.0, float(point.get("lon")), step=0.0001, format="%.6f", key=f"wp_lon_{point['uid']}")
                b1, b2, b3 = st.columns(3)
                with b1:
                    if st.button("↑", key=f"up_{point['uid']}", use_container_width=True) and idx > 0:
                        move = (idx, idx - 1)
                with b2:
                    if st.button("↓", key=f"down_{point['uid']}", use_container_width=True) and idx < len(st.session_state.wps) - 1:
                        move = (idx, idx + 1)
                with b3:
                    if st.button("Remover", key=f"rm_{point['uid']}", use_container_width=True):
                        remove_idx = idx
        if move:
            a, b = move
            st.session_state.wps[a], st.session_state.wps[b] = st.session_state.wps[b], st.session_state.wps[a]
            recalc_route(refresh_procedures=True)
            st.session_state["_last_calc_sig"] = calculation_signature()
            st.rerun()
        if remove_idx is not None:
            st.session_state.wps.pop(remove_idx)
            recalc_route(refresh_procedures=True)
            st.session_state["_last_calc_sig"] = calculation_signature()
            st.rerun()
        if st.session_state.wps:
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Aplicar alterações e recalcular", type="primary", use_container_width=True):
                    recalc_route(refresh_procedures=True)
                    st.session_state["_last_calc_sig"] = calculation_signature()
                    st.rerun()
            with c2:
                st.code(route_item15(st.session_state.wps) or "—")

with tab_map:
    st.markdown("#### Mapa e pontos por clique")
    top = st.columns([0.85, 1.15, 0.85, 0.85, 1.3])
    with top[0]:
        st.toggle("Pontos ref.", key="show_ref_points")
    with top[1]:
        st.multiselect("Camadas", ["IFR", "VOR", "AD", "VFR", "PROC"], key="ref_layers")
    with top[2]:
        st.toggle("Airways", key="show_airways")
    with top[3]:
        st.toggle("openAIP", key="show_openaip")
    with top[4]:
        st.caption(f"openAIP: {'OK' if get_openaip_token() else 'sem OPENAIP_API_KEY nos secrets'}")
    st.slider("Opacidade openAIP", 0.0, 1.0, key="openaip_opacity", step=0.05)
    out_map = render_route_map(st.session_state.wps, st.session_state.route_nodes, st.session_state.legs, key="map_tab")
    clicked = out_map.get("last_clicked") if out_map else None
    if clicked:
        with st.form("add_click_form"):
            st.markdown("##### Adicionar último clique")
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                name = st.text_input("Nome", "WP CLICK")
            with c2:
                alt = st.number_input("Alt", 0.0, 45000.0, float(st.session_state.default_alt), step=100.0)
            with c3:
                st.caption(f"{clicked['lat']:.5f}, {clicked['lng']:.5f}")
            if st.form_submit_button("Adicionar clique"):
                p = Point(code=clean_code(name) or "CLICK", name=name, lat=float(clicked["lat"]), lon=float(clicked["lng"]), alt=float(alt), src="USER", uid=next_uid())
                st.session_state.wps.append(p.to_dict())
                recalc_route(refresh_procedures=True)
                st.session_state["_last_calc_sig"] = calculation_signature()
                st.rerun()

with tab_navlog:
    st.markdown("#### 3 · Rever navlog e gerar PDF")
    if not st.session_state.legs:
        st.info("Cria uma rota e recalcula para ver o navlog.")
    else:
        df_legs = legs_to_dataframe(st.session_state.legs)
        st.dataframe(style_navlog_dataframe(df_legs), use_container_width=True, hide_index=True)
        st.download_button("⬇️ Navlog CSV", df_legs.to_csv(index=False).encode("utf-8"), file_name="navlog.csv", mime="text/csv")

        st.markdown("#### Cabeçalho PDF")
        reg_options = REG_OPTIONS_PIPER if "Piper" in st.session_state.aircraft_type else REG_OPTIONS_TECNAM
        c0, c1, c2, c3, c4 = st.columns(5)
        with c0:
            callsign = st.text_input("Callsign", "RVP")
        with c1:
            registration = st.selectbox("Registration", reg_options)
        with c2:
            student = st.text_input("Student", "")
        with c3:
            lesson = st.text_input("Lesson", "")
        with c4:
            instructor = st.text_input("Instructor", "")
        c5, c6, c7, c8, c9 = st.columns(5)
        with c5:
            etd = st.text_input("ETD", "")
        with c6:
            eta = st.text_input("ETA", "")
        with c7:
            dept_freq = st.text_input("FREQ DEPT", "119.805")
        with c8:
            enroute_freq = st.text_input("FREQ ENROUTE", "123.755")
        with c9:
            arrival_freq = st.text_input("FREQ ARRIVAL", "131.675")
        c10, c11 = st.columns(2)
        with c10:
            fl_alt = st.text_input("FLIGHT LEVEL / ALTITUDE", "")
        with c11:
            temp_isa = st.text_input("TEMP / ISA DEV", "")
        header = {
            "callsign": callsign,
            "registration": registration,
            "student": student,
            "lesson": lesson,
            "instructor": instructor,
            "etd": etd,
            "eta": eta,
            "dept_freq": dept_freq,
            "enroute_freq": enroute_freq,
            "arrival_freq": arrival_freq,
            "fl_alt": fl_alt,
            "temp_isa": temp_isa,
        }
        if st.button("Gerar PDF NAVLOG", type="primary", use_container_width=True):
            if not TEMPLATE_MAIN.exists():
                st.error("NAVLOG_FORM.pdf não encontrado.")
            elif PdfReader is None:
                st.error("pdfrw não está instalado. Instala com: pip install pdfrw")
            else:
                try:
                    single_page = len(st.session_state.legs) <= PDF_SINGLE_PAGE_LEG_ROWS
                    payload = build_pdf_payload(
                        st.session_state.legs,
                        header,
                        0,
                        PDF_SINGLE_PAGE_LEG_ROWS if single_page else PDF_FULL_TEMPLATE_LEG_ROWS,
                        total_on_next_row=single_page,
                        fill_continuation_total=not single_page,
                    )
                    out = fill_pdf(TEMPLATE_MAIN, OUTPUT_MAIN, payload, pages_to_keep=1 if single_page else None)
                    with open(out, "rb") as file:
                        st.download_button("⬇️ NAVLOG principal", file.read(), file_name="NAVLOG_FILLED.pdf", mime="application/pdf", use_container_width=True)
                    if len(st.session_state.legs) > PDF_FULL_TEMPLATE_LEG_ROWS and TEMPLATE_CONT.exists():
                        payload2 = build_pdf_payload(st.session_state.legs, header, PDF_FULL_TEMPLATE_LEG_ROWS, 11)
                        out2 = fill_pdf(TEMPLATE_CONT, OUTPUT_CONT, payload2)
                        with open(out2, "rb") as file:
                            st.download_button("⬇️ NAVLOG continuação", file.read(), file_name="NAVLOG_FILLED_1.pdf", mime="application/pdf", use_container_width=True)
                except Exception as exc:
                    st.error(f"Erro ao gerar PDF: {exc}")

st.markdown("<hr><div class='small-muted'>Ferramenta de planeamento. SIDs/STARs only. Aproximações, cartas, NOTAM, AIP/AIRAC, meteorologia, mínimos, autorizações ATC e performance real têm de ser confirmados externamente.</div>", unsafe_allow_html=True)
