# app.py
# ---------------------------------------------------------------
# NAVLOG VFR/IFR Portugal Continental — Streamlit
# ---------------------------------------------------------------
# Objetivo:
# - VFR: aeródromos/heliportos/ULM + localidades + pontos por clique no mapa
# - IFR: pontos ENR 4.4 via CSV local + airways ENR 3.3 via CSV local
# - VOR: fixes por radial/distância e tracking inbound/outbound por radial
# - LPSO: SID / STAR / Approaches com pontos calculados por performance
# - Navlog: cálculo TT/TH/MH/GS/ETE/Fuel/EFOB, TOC/TOD/STOP
# - PDF: preenche NAVLOG_FORM.pdf e NAVLOG_FORM_1.pdf se existirem no repo
#
# A app NÃO vai buscar o AIP em runtime. Para atualizar dados IFR, corre:
#   python tools/update_ifr_data.py
# e faz commit dos CSV gerados.
# ---------------------------------------------------------------

from __future__ import annotations

import base64
import datetime as dt
import difflib
import io
import json
import math
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Fullscreen, MarkerCluster, MeasureControl
from pdfrw import PageMerge, PdfDict, PdfName, PdfReader, PdfWriter
from streamlit_folium import st_folium

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

# ===============================================================
# CONFIG
# ===============================================================
APP_TITLE = "NAVLOG Portugal — VFR + IFR Low"
ROOT = Path(__file__).parent

CSV_AD = ROOT / "AD-HEL-ULM.csv"
CSV_LOC = ROOT / "Localidades-Nova-versao-230223.csv"
CSV_VOR = ROOT / "NAVAIDS_VOR.csv"
CSV_IFR_POINTS = ROOT / "IFR_POINTS.csv"
CSV_IFR_AIRWAYS = ROOT / "IFR_AIRWAYS.csv"

TEMPLATE_MAIN = ROOT / "NAVLOG_FORM.pdf"
TEMPLATE_CONT = ROOT / "NAVLOG_FORM_1.pdf"

OUTPUT_MAIN = ROOT / "NAVLOG_FILLED.pdf"
OUTPUT_CONT = ROOT / "NAVLOG_FILLED_1.pdf"
OUTPUT_BRIEFING = ROOT / "NAVLOG_LEGS_BRIEFING.pdf"

EARTH_NM = 3440.065
PT_CENTER = (39.55, -8.10)
LPSO_FALLBACK_CENTER = (39.2119, -8.0569)  # usado apenas se o CSV AD ainda não tiver LPSO
PT_BOUNDS = [(36.70, -9.85), (42.25, -6.00)]  # Portugal continental + margem FIR terrestre

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

# Rounding policy
ROUND_TIME_SEC = 60
ROUND_DIST_NM = 0.5
ROUND_FUEL_L = 1.0

# Os CSV operacionais devem estar no repositório. A app não usa fallbacks internos.

# ===============================================================
# STREAMLIT SETUP + STYLE
# ===============================================================
st.set_page_config(page_title="NAVLOG IFR/VFR", page_icon="🧭", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
<style>
:root{
  --bg:#f8fafc; --card:#ffffff; --line:#e2e8f0; --muted:#64748b;
  --text:#0f172a; --accent:#2563eb; --good:#059669; --warn:#d97706;
}
.block-container{padding-top:1.2rem;padding-bottom:2rem;max-width:1500px;}
.nav-card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:14px 16px;margin:10px 0;box-shadow:0 1px 2px rgba(15,23,42,.04)}
.nav-hero{background:linear-gradient(135deg,#eff6ff,#ffffff);border:1px solid #bfdbfe;border-radius:22px;padding:18px 20px;margin-bottom:12px;}
.nav-title{font-size:30px;font-weight:850;letter-spacing:-.03em;color:var(--text);margin:0;}
.nav-sub{font-size:14px;color:var(--muted);margin-top:4px;}
.pill{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 10px;font-size:12px;font-weight:650;margin:3px 4px 3px 0;color:#0f172a;}
.pill-good{border-color:#bbf7d0;background:#f0fdf4;color:#166534;}
.pill-warn{border-color:#fed7aa;background:#fff7ed;color:#9a3412;}
.small-muted{font-size:12px;color:var(--muted)}
.route-token{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;background:#f1f5f9;border:1px solid #e2e8f0;border-radius:8px;padding:1px 6px;}
hr{border:none;border-top:1px solid var(--line);margin:1rem 0;}
</style>
""",
    unsafe_allow_html=True,
)

# ===============================================================
# DATACLASSES
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
    vor_pref: str = "AUTO"  # AUTO | FIXED | NONE
    vor_ident: str = ""
    arc_vor: str = ""
    arc_radius_nm: float = 0.0
    arc_start_radial: float = 0.0
    arc_end_radial: float = 0.0
    arc_direction: str = "CW"
    arc_endpoint: str = ""  # START | END
    uid: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Point":
        return cls(
            code=str(data.get("code") or data.get("name") or "WP").upper(),
            name=str(data.get("name") or data.get("code") or "WP"),
            lat=float(data.get("lat", 0.0)),
            lon=float(data.get("lon", 0.0)),
            alt=float(data.get("alt", 0.0)),
            src=str(data.get("src", "USER")),
            routes=str(data.get("routes", "")),
            remarks=str(data.get("remarks", "")),
            stop_min=float(data.get("stop_min", 0.0)),
            wind_from=data.get("wind_from"),
            wind_kt=data.get("wind_kt"),
            vor_pref=str(data.get("vor_pref", "AUTO")),
            vor_ident=str(data.get("vor_ident", "")),
            arc_vor=str(data.get("arc_vor", "")),
            arc_radius_nm=float(data.get("arc_radius_nm", 0.0) or 0.0),
            arc_start_radial=float(data.get("arc_start_radial", 0.0) or 0.0),
            arc_end_radial=float(data.get("arc_end_radial", 0.0) or 0.0),
            arc_direction=str(data.get("arc_direction", "CW") or "CW"),
            arc_endpoint=str(data.get("arc_endpoint", "")),
            uid=data.get("uid"),
        )

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
        return x
    return round(float(x) / step) * step


def rt(sec: float) -> int:
    return int(round_to_step(sec, ROUND_TIME_SEC))


def rd(nm: float) -> float:
    return round_to_step(nm, ROUND_DIST_NM)


def rf(L: float) -> float:
    return round_to_step(L, ROUND_FUEL_L)


def fmt_unit(x: float) -> str:
    return str(int(round(float(x))))


def fmt_num_clean(x: float, decimals: int = 1) -> str:
    v = round(float(x), decimals)
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.{decimals}f}"


def mmss(sec: float) -> str:
    mins = int(round(float(sec) / 60.0))
    return f"{mins:02d}:00" if mins < 60 else f"{mins // 60:02d}:{mins % 60:02d}:00"


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
    m = re.match(r"^(\d+(?:\.\d+)?)([NSEW])$", token)
    if not m:
        return None
    value, hemi = m.groups()
    raw = value
    if "." in raw:
        # Portugal files often use ddmmss.sssN / dddmmss.sssW
        if is_lon:
            deg = int(raw[0:3]); mins = int(raw[3:5]); secs = float(raw[5:])
        else:
            deg = int(raw[0:2]); mins = int(raw[2:4]); secs = float(raw[4:])
    else:
        if is_lon:
            deg = int(raw[0:3]); mins = int(raw[3:5]); secs = float(raw[5:] or 0)
        else:
            deg = int(raw[0:2]); mins = int(raw[2:4]); secs = float(raw[4:] or 0)
    dd = deg + mins / 60.0 + secs / 3600.0
    if hemi in {"S", "W"}:
        dd = -dd
    return dd


def dd_to_icao(lat: float, lon: float) -> str:
    lat_abs, lon_abs = abs(lat), abs(lon)
    lat_deg = int(lat_abs)
    lon_deg = int(lon_abs)
    lat_min = int(round((lat_abs - lat_deg) * 60))
    lon_min = int(round((lon_abs - lon_deg) * 60))
    if lat_min == 60:
        lat_deg += 1; lat_min = 0
    if lon_min == 60:
        lon_deg += 1; lon_min = 0
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
    # East variation is subtracted from true to magnetic; West is added.
    return wrap360(true_heading - mag_var if is_east else true_heading + mag_var)

# ===============================================================
# DATA LOADING
# ===============================================================
@st.cache_data(show_spinner=False)
def load_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists():
        st.error(f"CSV obrigatório em falta: {path.name}. Coloca este ficheiro na raiz do repositório.")
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as e:
        st.error(f"Não consegui ler {path.name}: {e}")
        return pd.DataFrame()


def parse_ad_df(df: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if df.empty:
        return pd.DataFrame(columns=["code", "name", "lat", "lon", "alt", "src", "routes", "remarks"])
    for line in df.iloc[:, 0].dropna().tolist():
        s = str(line).strip()
        if not s or s.startswith(("Ident", "DEP/")):
            continue
        tokens = s.split()
        coord_toks = [t for t in tokens if re.match(r"^\d+(?:\.\d+)?[NSEW]$", t, re.I)]
        if len(coord_toks) >= 2:
            lat_tok, lon_tok = coord_toks[-2], coord_toks[-1]
            lat = dms_token_to_dd(lat_tok, is_lon=False)
            lon = dms_token_to_dd(lon_tok, is_lon=True)
            if lat is None or lon is None:
                continue
            ident = tokens[0] if re.match(r"^[A-Z0-9]{3,5}$", tokens[0]) else ""
            try:
                name = " ".join(tokens[1 : tokens.index(coord_toks[0])]).strip()
            except Exception:
                name = ident or " ".join(tokens[:3])
            rows.append(
                {
                    "code": clean_code(ident or name),
                    "name": name or ident,
                    "lat": lat,
                    "lon": lon,
                    "alt": 0.0,
                    "src": "AD",
                    "routes": "",
                    "remarks": "",
                }
            )
    return pd.DataFrame(rows)


def parse_loc_df(df: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    if df.empty:
        return pd.DataFrame(columns=["code", "name", "lat", "lon", "alt", "src", "routes", "remarks"])
    for line in df.iloc[:, 0].dropna().tolist():
        s = str(line).strip()
        if not s or "Total de registos" in s:
            continue
        tokens = s.split()
        coord_toks = [t for t in tokens if re.match(r"^\d{6,7}(?:\.\d+)?[NSEW]$", t, re.I)]
        if len(coord_toks) >= 2:
            lat_tok, lon_tok = coord_toks[0], coord_toks[1]
            lat = dms_token_to_dd(lat_tok, is_lon=False)
            lon = dms_token_to_dd(lon_tok, is_lon=True)
            if lat is None or lon is None:
                continue
            try:
                lon_idx = tokens.index(lon_tok)
                code = tokens[lon_idx + 1] if lon_idx + 1 < len(tokens) else ""
                name = " ".join(tokens[: tokens.index(lat_tok)]).strip()
            except Exception:
                code = ""
                name = s[:32]
            rows.append(
                {
                    "code": clean_code(code or name),
                    "name": name or code,
                    "lat": lat,
                    "lon": lon,
                    "alt": 0.0,
                    "src": "VFR",
                    "routes": "",
                    "remarks": "",
                }
            )
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def load_vor(path_str: str) -> pd.DataFrame:
    path = Path(path_str)
    if path.exists():
        try:
            df = pd.read_csv(path)
            df = df.rename(columns={c: c.lower().strip() for c in df.columns})
            rename = {"frequency": "freq_mhz", "freq": "freq_mhz", "latitude": "lat", "longitude": "lon"}
            df = df.rename(columns=rename)
            df["ident"] = df["ident"].astype(str).str.upper().str.strip()
            df["freq_mhz"] = pd.to_numeric(df["freq_mhz"], errors="coerce")
            df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
            df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
            if "name" not in df.columns:
                df["name"] = df["ident"]
            return df.dropna(subset=["ident", "freq_mhz", "lat", "lon"])[["ident", "name", "freq_mhz", "lat", "lon"]]
        except Exception:
            pass
    st.error(f"CSV obrigatório em falta ou inválido: {path.name}.")
    return pd.DataFrame(columns=["ident", "name", "freq_mhz", "lat", "lon"])


@st.cache_data(show_spinner=False)
def load_all_data() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ad = parse_ad_df(load_csv_safe(CSV_AD))
    loc = parse_loc_df(load_csv_safe(CSV_LOC))

    vor = load_vor(str(CSV_VOR)).copy()
    vor_points = pd.DataFrame(
        {
            "code": vor["ident"].astype(str),
            "name": vor["name"].astype(str),
            "lat": vor["lat"],
            "lon": vor["lon"],
            "alt": 0.0,
            "src": "VOR",
            "routes": "",
            "remarks": vor["freq_mhz"].map(lambda x: f"{x:.2f} MHz"),
        }
    )

    ifr = load_csv_safe(CSV_IFR_POINTS).copy()
    if not ifr.empty:
        ifr = ifr.rename(columns={c: c.lower().strip() for c in ifr.columns})
        if "code" not in ifr.columns and "ident" in ifr.columns:
            ifr["code"] = ifr["ident"]
        for col in ["name", "routes", "remarks", "src"]:
            if col not in ifr.columns:
                ifr[col] = "IFR" if col == "src" else ""
        ifr["code"] = ifr["code"].astype(str).str.upper().str.strip()
        ifr["name"] = ifr["name"].fillna(ifr["code"]).astype(str)
        ifr["lat"] = pd.to_numeric(ifr["lat"], errors="coerce")
        ifr["lon"] = pd.to_numeric(ifr["lon"], errors="coerce")
        if "alt" not in ifr.columns:
            ifr["alt"] = 0.0
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
        needed = ["airway", "seq", "point", "lat", "lon"]
        for col in needed:
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
# VOR HELPERS
# ===============================================================
def get_vor(ident: str) -> Optional[Dict[str, Any]]:
    ident = clean_code(ident)
    if not ident:
        return None
    hit = VOR_DF[VOR_DF["ident"].astype(str).str.upper() == ident]
    if hit.empty:
        return None
    r = hit.iloc[0]
    return {"ident": r["ident"], "name": r["name"], "freq_mhz": float(r["freq_mhz"]), "lat": float(r["lat"]), "lon": float(r["lon"])}


def nearest_vor(lat: float, lon: float, limit_nm: Optional[float] = None) -> Optional[Dict[str, Any]]:
    if VOR_DF.empty:
        return None
    best: Optional[Dict[str, Any]] = None
    best_d = 1e9
    for _, r in VOR_DF.iterrows():
        d = gc_dist_nm(lat, lon, float(r["lat"]), float(r["lon"]))
        if d < best_d:
            best_d = d
            best = {"ident": r["ident"], "name": r["name"], "freq_mhz": float(r["freq_mhz"]), "lat": float(r["lat"]), "lon": float(r["lon"]), "dist_nm": d}
    if limit_nm is not None and best and best_d > limit_nm:
        return None
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
    return f"R{radial:03d}/D{int(round(dist)):02d}"


def make_vor_fix(token: str) -> Optional[Point]:
    """Accepts CAS/R180/D12, CAS-180-12, CAS180012, CAS R180 D12 (handled upstream only as single token)."""
    t = token.strip().upper().replace(" ", "")
    patterns = [
        r"^([A-Z0-9]{2,4})/R?(\d{1,3})/D?(\d+(?:\.\d+)?)$",
        r"^([A-Z0-9]{2,4})-R?(\d{1,3})-D?(\d+(?:\.\d+)?)$",
        r"^([A-Z0-9]{2,4})R(\d{1,3})D(\d+(?:\.\d+)?)$",
    ]
    for pat in patterns:
        m = re.match(pat, t)
        if not m:
            continue
        vor = get_vor(m.group(1))
        if not vor:
            return None
        radial = float(m.group(2))
        dist = float(m.group(3))
        lat, lon = dest_point(vor["lat"], vor["lon"], radial, dist)
        code = f"{vor['ident']}R{int(radial):03d}D{dist:g}"
        return Point(code=code, name=f"{vor['ident']} R{int(radial):03d} D{dist:g}", lat=lat, lon=lon, src="VORFIX", remarks=format_vor_id(vor), vor_pref="FIXED", vor_ident=vor["ident"])
    return None

def arc_radials(start_radial: float, end_radial: float, direction: str, step_deg: float) -> List[float]:
    """Generate radials for a DME arc. Direction is CW or CCW as seen around the station."""
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


def make_dme_arc_points(vor_ident: str, radius_nm: float, start_radial: float, end_radial: float, direction: str, step_deg: float, alt_ft: float, prefix: str = "ARC") -> Tuple[List[Point], str]:
    """Create only the initial and final fixes of a DME arc.

    The map renders the segment between those two fixes as a continuous arc. The navlog uses
    the arc length, not the straight chord, whenever these two fixes are consecutive.
    step_deg is kept in the signature for backwards compatibility but is not used for route points.
    """
    vor = get_vor(vor_ident)
    if not vor:
        return [], f"VOR {vor_ident} não encontrado."
    radius = max(0.1, float(radius_nm))
    start_r = wrap360(start_radial)
    end_r = wrap360(end_radial)
    direction = str(direction or "CW").upper()
    if direction not in {"CW", "CCW"}:
        direction = "CW"
    base = clean_code(prefix) or f"{vor['ident']}ARC"

    def _arc_point(radial: float, endpoint: str) -> Point:
        lat, lon = dest_point(vor["lat"], vor["lon"], radial, radius)
        code = f"{vor['ident']}D{radius:g}R{int(round(radial)) % 360:03d}".replace(".", "")[:12]
        return Point(
            code=code,
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

    points = [_arc_point(start_r, "START"), _arc_point(end_r, "END")]
    return points, f"Arco {vor['ident']} DME {radius:g} NM {direction}: ponto inicial R{int(round(start_r)) % 360:03d} e final R{int(round(end_r)) % 360:03d}."


def dme_arc_sweep_deg(A: Dict[str, Any], B: Dict[str, Any]) -> float:
    direction = str(B.get("arc_direction") or A.get("arc_direction") or "CW").upper()
    start = float(B.get("arc_start_radial") or A.get("arc_start_radial") or 0.0)
    end = float(B.get("arc_end_radial") or A.get("arc_end_radial") or 0.0)
    if direction == "CCW":
        return (start - end) % 360.0
    return (end - start) % 360.0


def is_dme_arc_leg(A: Dict[str, Any], B: Dict[str, Any]) -> bool:
    if A.get("src") != "DMEARC" or B.get("src") != "DMEARC":
        return False
    keys = ["arc_vor", "arc_radius_nm", "arc_start_radial", "arc_end_radial", "arc_direction"]
    for k in keys:
        if str(A.get(k, "")) != str(B.get(k, "")):
            return False
    return float(A.get("arc_radius_nm") or 0) > 0


def dme_arc_distance_nm(A: Dict[str, Any], B: Dict[str, Any]) -> float:
    radius = float(B.get("arc_radius_nm") or A.get("arc_radius_nm") or 0.0)
    return 2.0 * math.pi * radius * (dme_arc_sweep_deg(A, B) / 360.0)


def dme_arc_mid_radial(A: Dict[str, Any], B: Dict[str, Any]) -> float:
    direction = str(B.get("arc_direction") or A.get("arc_direction") or "CW").upper()
    start = float(B.get("arc_start_radial") or A.get("arc_start_radial") or 0.0)
    sweep = dme_arc_sweep_deg(A, B)
    return wrap360(start - sweep / 2.0 if direction == "CCW" else start + sweep / 2.0)


def dme_arc_course(A: Dict[str, Any], B: Dict[str, Any]) -> float:
    direction = str(B.get("arc_direction") or A.get("arc_direction") or "CW").upper()
    mid_radial = dme_arc_mid_radial(A, B)
    return wrap360(mid_radial - 90.0 if direction == "CCW" else mid_radial + 90.0)


def dme_arc_polyline(A: Dict[str, Any], B: Dict[str, Any], max_step_deg: float = 2.0) -> List[Tuple[float, float]]:
    vor = get_vor(str(B.get("arc_vor") or A.get("arc_vor") or ""))
    if not vor:
        return [(float(A["lat"]), float(A["lon"])), (float(B["lat"]), float(B["lon"]))]
    radius = float(B.get("arc_radius_nm") or A.get("arc_radius_nm") or 0.0)
    start = float(B.get("arc_start_radial") or A.get("arc_start_radial") or 0.0)
    end = float(B.get("arc_end_radial") or A.get("arc_end_radial") or 0.0)
    direction = str(B.get("arc_direction") or A.get("arc_direction") or "CW").upper()
    radials = arc_radials(start, end, direction, max_step_deg)
    return [dest_point(vor["lat"], vor["lon"], r, radius) for r in radials]


def tracking_instruction(A: Dict[str, Any], B: Dict[str, Any], preferred_vor: str = "") -> str:
    if B.get("navlog_note"):
        return str(B.get("navlog_note"))
    if is_dme_arc_leg(A, B):
        vor = B.get("arc_vor") or A.get("arc_vor") or ""
        radius = B.get("arc_radius_nm") or A.get("arc_radius_nm") or 0
        direction = str(B.get("arc_direction") or A.get("arc_direction") or "CW").upper()
        start = int(round(float(B.get("arc_start_radial") or A.get("arc_start_radial") or 0))) % 360
        end = int(round(float(B.get("arc_end_radial") or A.get("arc_end_radial") or 0))) % 360
        return f"ARC {vor} D{float(radius):g} {direction} R{start:03d}->R{end:03d}"
    # If a point has a fixed VOR, use it; otherwise nearest VOR to the midpoint.
    v = get_vor(preferred_vor) if preferred_vor else None
    if not v:
        mid_lat, mid_lon = point_along_gc(A["lat"], A["lon"], B["lat"], B["lon"], gc_dist_nm(A["lat"], A["lon"], B["lat"], B["lon"]) / 2)
        v = nearest_vor(mid_lat, mid_lon)
    if not v:
        return ""
    radial_a, da = vor_radial_distance(v, A["lat"], A["lon"])
    radial_b, db = vor_radial_distance(v, B["lat"], B["lon"])
    if db < da - 0.3:
        # Going toward station: track reciprocal course inbound on current radial.
        return f"INB {v['ident']} R{radial_a:03d} → station"
    if db > da + 0.3:
        return f"OUTB {v['ident']} R{radial_a:03d}"
    return f"X-RAD {v['ident']} R{radial_a:03d}→R{radial_b:03d}"


# ===============================================================
# LPSO PROCEDURES — SID / STAR / APPROACH
# ===============================================================
# Training charts supplied for LPSO are "FOR TRAINING PURPOSES / VMC ONLY".
# The procedure builder below is deliberately local/offline: no AIP lookup at runtime.
# For altitude-triggered turns, the position is computed from aircraft climb TAS,
# selected ROC, wind and aerodrome elevation. Example: "At 2000 turn RIGHT 319"
# becomes a computed point named "2000 TURN → TRK319" in the navlog.

LPSO_ELEV_FT = 390.0
LPSO_RWY_TRACKS = {"03": 025.0, "21": 206.0}


def db_point(code: str, alt: float = 0.0, src_priority: Optional[List[str]] = None) -> Optional[Point]:
    code = clean_code(code)
    if not code:
        return None
    hit = POINTS_DF[POINTS_DF["code"].astype(str).str.upper() == code]
    if hit.empty:
        return None
    if src_priority:
        order = {s: i for i, s in enumerate(src_priority)}
        hit = hit.assign(_p=hit["src"].map(lambda s: order.get(str(s), 999))).sort_values("_p")
    return df_row_to_point(hit.iloc[0], alt=alt)


def lspo_arp(alt: float = LPSO_ELEV_FT) -> Point:
    p = db_point("LPSO", alt=alt, src_priority=["AD"])
    if p:
        p.name = "PONTE DE SOR"
        p.code = "LPSO"
        p.src = "AD"
        return p
    return Point(code="LPSO", name="PONTE DE SOR", lat=LPSO_FALLBACK_CENTER[0], lon=LPSO_FALLBACK_CENTER[1], alt=alt, src="AD")


def proc_point(code: str, name: str, lat: float, lon: float, alt: float, *, remarks: str = "", navlog_note: str = "", src: str = "PROC") -> Dict[str, Any]:
    p = Point(code=code, name=name, lat=lat, lon=lon, alt=alt, src=src, remarks=remarks, uid=next_uid()).to_dict()
    p["navlog_note"] = navlog_note or code
    p["no_auto_vnav"] = True
    return p


def proc_from_point(p: Point, *, alt: Optional[float] = None, remarks: str = "", navlog_note: str = "") -> Dict[str, Any]:
    p.alt = float(p.alt if alt is None else alt)
    p.uid = next_uid()
    d = p.to_dict()
    d["src"] = "PROC" if d.get("src") not in {"AD", "VOR"} else d.get("src")
    d["remarks"] = remarks or d.get("remarks", "")
    d["navlog_note"] = navlog_note or d.get("code") or d.get("name")
    d["no_auto_vnav"] = True
    return d


def proc_vor_radial(ident: str, radial: float, dist_nm: float, alt: float, code: str = "", name: str = "", *, note: str = "") -> Dict[str, Any]:
    vor = get_vor(ident)
    if not vor:
        raise ValueError(f"VOR {ident} não encontrado no CSV.")
    lat, lon = dest_point(vor["lat"], vor["lon"], radial, dist_nm)
    code = code or f"{ident}R{int(round(radial)) % 360:03d}D{dist_nm:g}".replace(".", "")
    name = name or f"{ident} R{int(round(radial)) % 360:03d} D{dist_nm:g}"
    return proc_point(
        code=code,
        name=name,
        lat=lat,
        lon=lon,
        alt=alt,
        remarks=f"{format_vor_id(vor)} R{int(round(radial)) % 360:03d} D{dist_nm:g}",
        navlog_note=note or name,
    )


def proc_arc_points(ident: str, radius_nm: float, start_radial: float, end_radial: float, direction: str, alt: float, prefix: str = "ARC", note: str = "") -> List[Dict[str, Any]]:
    pts, _ = make_dme_arc_points(ident, radius_nm, start_radial, end_radial, direction, 2.0, alt, prefix)
    out: List[Dict[str, Any]] = []
    for p in pts:
        d = p.to_dict()
        d["src"] = "DMEARC"
        d["no_auto_vnav"] = True
        d["navlog_note"] = note or d.get("name")
        out.append(d)
    return out


def _project_xy_nm(lat: float, lon: float, lat0: float, lon0: float) -> Tuple[float, float]:
    x = (lon - lon0) * 60.0 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 60.0
    return x, y


def _unproject_xy_nm(x: float, y: float, lat0: float, lon0: float) -> Tuple[float, float]:
    lat = lat0 + y / 60.0
    lon = lon0 + x / (60.0 * max(math.cos(math.radians(lat0)), 1e-9))
    return lat, lon


def _track_vec(track_deg: float) -> Tuple[float, float]:
    t = math.radians(track_deg)
    return math.sin(t), math.cos(t)


def intersect_track_radial(
    start_lat: float,
    start_lon: float,
    track_deg: float,
    vor_ident: str,
    radial_deg: float,
    *,
    fallback_dist_nm: float = 20.0,
) -> Tuple[float, float]:
    """Intersection of a track from start point with a VOR radial, using local NM projection.

    If the lines are nearly parallel or the intersection is behind the aircraft, returns a
    conservative fallback point along track.
    """
    vor = get_vor(vor_ident)
    if not vor:
        return dest_point(start_lat, start_lon, track_deg, fallback_dist_nm)
    lat0 = (start_lat + float(vor["lat"])) / 2.0
    lon0 = (start_lon + float(vor["lon"])) / 2.0
    sx, sy = _project_xy_nm(start_lat, start_lon, lat0, lon0)
    vx, vy = _project_xy_nm(float(vor["lat"]), float(vor["lon"]), lat0, lon0)
    dx1, dy1 = _track_vec(track_deg)
    dx2, dy2 = _track_vec(radial_deg)
    # S + t*d1 = V + u*d2
    det = dx1 * (-dy2) - dy1 * (-dx2)
    if abs(det) < 1e-8:
        return dest_point(start_lat, start_lon, track_deg, fallback_dist_nm)
    bx, by = vx - sx, vy - sy
    t = (bx * (-dy2) - by * (-dx2)) / det
    if t < 0:
        return dest_point(start_lat, start_lon, track_deg, fallback_dist_nm)
    return _unproject_xy_nm(sx + t * dx1, sy + t * dy1, lat0, lon0)


def altitude_trigger_turn_point(
    dep: Point,
    runway_track: float,
    trigger_alt_ft: float,
    turn_dir: str,
    new_track: float,
    *,
    note_prefix: str = "",
) -> Dict[str, Any]:
    delta_ft = max(0.0, float(trigger_alt_ft) - float(dep.alt or LPSO_ELEV_FT))
    roc = max(float(st.session_state.roc_fpm), 1.0)
    tas = float(st.session_state.climb_tas)
    wf, wk = int(st.session_state.wind_from), int(st.session_state.wind_kt)
    _, _, gs = wind_triangle(runway_track, tas, wf, wk)
    dist_nm = max(0.1, gs * (delta_ft / roc) / 60.0)
    lat, lon = dest_point(dep.lat, dep.lon, runway_track, dist_nm)
    arrow = "←" if str(turn_dir).upper().startswith("L") else "→"
    note = f"{int(round(trigger_alt_ft))} TURN {arrow} TRK{int(round(new_track)) % 360:03d}"
    if note_prefix:
        note = f"{note_prefix} {note}"
    return proc_point(
        code=note.replace(" ", ""),
        name=note,
        lat=lat,
        lon=lon,
        alt=trigger_alt_ft,
        remarks=f"Performance point: {dist_nm:.1f} NM from LPSO using ROC {roc:.0f} fpm, climb TAS {tas:.0f} kt, wind {wf:03d}/{wk}",
        navlog_note=note,
    )


def resolve_named_proc_point(code: str, alt: float = 3000.0) -> Dict[str, Any]:
    """Return known LPSO chart points, deriving local training fixes when not in CSV."""
    code = clean_code(code)
    p = db_point(code, alt=alt, src_priority=["IFR", "VOR", "AD", "VFR"])
    if p:
        return proc_from_point(p, alt=alt, navlog_note=code)

    if code == "TAGUX":
        return proc_vor_radial("FTM", 149, 50.8, alt, "TAGUX", "TAGUX", note="TAGUX")
    if code == "BORRO":
        # Chart labels BORRO on the NSA R198 inbound family; D29 is readable on the STAR page.
        return proc_vor_radial("NSA", 198, 29.0, alt, "BORRO", "BORRO", note="BORRO")
    if code == "MENDA":
        return proc_vor_radial("FTM", 119, 35.0, alt, "MENDA", "MENDA", note="MENDA FTM R119 D35")
    if code == "SALTE":
        return proc_vor_radial("NSA", 198, 17.0, alt, "SALTE", "SALTE", note="SALTE NSA R198 D17")
    if code == "PORCA":
        magum = db_point("MAGUM", alt=4500.0, src_priority=["IFR"])
        if magum:
            lat, lon = dest_point(magum.lat, magum.lon, 83.0, 15.6)
            return proc_point("PORCA", "PORCA", lat, lon, alt, remarks="Derived from MAGUM 3N: 083° / 15.6 NM", navlog_note="PORCA")
    if code == "TRAMA":
        magum = db_point("MAGUM", alt=4500.0, src_priority=["IFR"])
        if magum:
            lat, lon = dest_point(magum.lat, magum.lon, 80.0, 16.1)
            return proc_point("TRAMA", "TRAMA", lat, lon, alt, remarks="Derived from MAGUM 3S: 080° / 16.1 NM", navlog_note="TRAMA")
    if code == "RAKET":
        lspo = lspo_arp()
        lat, lon = dest_point(lspo.lat, lspo.lon, wrap360(25.0 + 180.0), 4.0)
        return proc_point("RAKET", "RAKET", lat, lon, alt, remarks="GNSS RWY03 FAF, approx. 4 NM final", navlog_note="RAKET FAF")
    if code == "ROSED":
        lspo = lspo_arp()
        lat, lon = dest_point(lspo.lat, lspo.lon, wrap360(206.0 + 180.0), 4.0)
        return proc_point("ROSED", "ROSED", lat, lon, alt, remarks="GNSS RWY21 FAF, approx. 4 NM final", navlog_note="ROSED FAF")
    # Last resort: LPSO ARP placeholder.
    dep = lspo_arp(alt)
    return proc_from_point(dep, alt=alt, navlog_note=code)


def append_unique_points(base: List[Dict[str, Any]], new_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = list(base)
    for p in new_points:
        if out and clean_code(out[-1].get("code")) == clean_code(p.get("code")):
            out[-1].update(p)
        else:
            out.append(p)
    return out


def lspo_sid_points(proc: str, include_departure: bool = True) -> List[Dict[str, Any]]:
    proc = proc.upper().strip()
    dep = lspo_arp()
    dep_d = proc_from_point(dep, alt=LPSO_ELEV_FT, navlog_note="LPSO")

    pts: List[Dict[str, Any]] = [dep_d] if include_departure else []
    if proc.endswith("2N"):
        rwy_track = LPSO_RWY_TRACKS["03"]
        # Common R140 inbound FTM intercept / D28 fix.
        r140_lat, r140_lon = intersect_track_radial(dep.lat, dep.lon, rwy_track, "FTM", 140.0, fallback_dist_nm=8.0)
        r140 = proc_point("INTFTMR140", "INT FTM R140", r140_lat, r140_lon, 2000.0, navlog_note="INT FTM R140")
        ftm_d28 = proc_vor_radial("FTM", 140, 28.0, 2000.0, "FTMD28", "FTM D28", note="FTM D28")
        if proc == "NSA 2N":
            turn = altitude_trigger_turn_point(dep, rwy_track, 1400.0, "LEFT", 339.0)
            nsa_int_lat, nsa_int_lon = intersect_track_radial(turn["lat"], turn["lon"], 339.0, "NSA", 212.0, fallback_dist_nm=23.0)
            nsa_int = proc_point("INTNSAR212", "INT NSA R212", nsa_int_lat, nsa_int_lon, 2000.0, navlog_note="INT NSA R212")
            end = proc_vor_radial("NSA", 212, 23.0, 2000.0, "NSA2NEND", "NSA R212 D23", note="NSA R212 D23")
            pts += [turn, nsa_int, end]
        elif proc == "FTM 2N":
            end = proc_vor_radial("FTM", 140, 34.0, 3000.0, "FTM2NEND", "FTM R140 D34", note="FTM R140 D34")
            pts += [r140, end]
        elif proc == "MAGUM 2N":
            turn2_lat, turn2_lon = intersect_track_radial(ftm_d28["lat"], ftm_d28["lon"], 238.0, "NSA", 225.0, fallback_dist_nm=15.0)
            int_r225 = proc_point("INTNSAR225", "INT NSA R225", turn2_lat, turn2_lon, 2000.0, navlog_note="TRK238 INT NSA R225")
            arc30 = proc_vor_radial("NSA", 225, 30.0, 2000.0, "NSAD30R225", "NSA D30 R225", note="NSA ARC D30")
            magum = resolve_named_proc_point("MAGUM", 3000.0)
            pts += [r140, ftm_d28, int_r225, arc30, magum]
        elif proc == "TAGUX 2N":
            int_r149_lat, int_r149_lon = intersect_track_radial(ftm_d28["lat"], ftm_d28["lon"], 244.0, "FTM", 149.0, fallback_dist_nm=8.0)
            int_r149 = proc_point("INTFTMR149", "INT FTM R149", int_r149_lat, int_r149_lon, 3000.0, navlog_note="TRK244 INT FTM R149")
            d31 = proc_vor_radial("FTM", 149, 31.0, 3000.0, "FTMD31", "FTM D31", note="X FTM D31 3000")
            d40 = proc_vor_radial("FTM", 149, 40.0, 3000.0, "FTMD40", "FTM D40", note="MAINT 3000 UNTIL D40")
            tagux = resolve_named_proc_point("TAGUX", 3000.0)
            pts += [r140, ftm_d28, int_r149, d31, d40, tagux]
    elif proc.endswith("3S"):
        rwy_track = LPSO_RWY_TRACKS["21"]
        if proc in {"NSA 3S", "FTM 3S"}:
            turn = altitude_trigger_turn_point(dep, rwy_track, 2000.0, "RIGHT", 319.0)
            nsa_int_lat, nsa_int_lon = intersect_track_radial(turn["lat"], turn["lon"], 319.0, "NSA", 212.0, fallback_dist_nm=18.0)
            nsa_int = proc_point("INTNSAR212", "INT NSA R212", nsa_int_lat, nsa_int_lon, 2000.0, navlog_note="INT NSA R212")
            ftm139_lat, ftm139_lon = intersect_track_radial(nsa_int["lat"], nsa_int["lon"], 319.0, "FTM", 139.0, fallback_dist_nm=16.0)
            ftm139 = proc_point("XFTMR139", "CROSS FTM R139", ftm139_lat, ftm139_lon, 2000.0, navlog_note="X FTM R139")
            if proc == "NSA 3S":
                end = proc_vor_radial("NSA", 212, 31.0, 2000.0, "NSA3SEND", "NSA R212 D31", note="NSA R212 D31")
                pts += [turn, nsa_int, ftm139, end]
            else:
                ftm = db_point("FTM", alt=3000.0, src_priority=["VOR"]) or Point(code="FTM", name="FTM", lat=0, lon=0, alt=3000, src="VOR")
                pts += [turn, nsa_int, ftm139, proc_from_point(ftm, alt=3000.0, navlog_note="FTM")]
        elif proc == "MAGUM 3S":
            turn = altitude_trigger_turn_point(dep, rwy_track, 2000.0, "RIGHT", 269.0)
            r225_lat, r225_lon = intersect_track_radial(turn["lat"], turn["lon"], 269.0, "NSA", 225.0, fallback_dist_nm=16.0)
            int_r225 = proc_point("INTNSAR225", "INT NSA R225", r225_lat, r225_lon, 2000.0, navlog_note="TRK269 INT NSA R225")
            arc30 = proc_vor_radial("NSA", 225, 30.0, 2000.0, "NSAD30R225", "NSA D30 R225", note="NSA ARC D30")
            magum = resolve_named_proc_point("MAGUM", 3000.0)
            pts += [turn, int_r225, arc30, magum]
        elif proc == "TAGUX 3S":
            r149_lat, r149_lon = intersect_track_radial(dep.lat, dep.lon, rwy_track, "FTM", 149.0, fallback_dist_nm=12.0)
            int_r149 = proc_point("INTFTMR149", "INT FTM R149", r149_lat, r149_lon, 2000.0, navlog_note="INT FTM R149")
            d40 = proc_vor_radial("FTM", 149, 40.0, 2000.0, "FTMD40", "FTM D40", note="MAINT 2000 UNTIL D40")
            tagux = resolve_named_proc_point("TAGUX", 3000.0)
            pts += [int_r149, d40, tagux]
    return pts


def lspo_star_points(proc: str) -> List[Dict[str, Any]]:
    proc = proc.upper().strip()
    pts: List[Dict[str, Any]] = []
    if proc.endswith("3N"):  # RNAV STAR GNSS RWY03 to PORCA
        entry = proc.replace(" 3N", "")
        pts.append(resolve_named_proc_point(entry, 4500.0 if entry in {"MAGUM", "FTM", "NSA", "TAGUX", "BORRO"} else 3500.0))
        ifgd08 = resolve_named_proc_point("PORCA", 1500.0)
        ifgd08["code"] = "IFGD08"
        ifgd08["name"] = "IFGD08"
        ifgd08["navlog_note"] = "IFGD08 1500"
        porca = resolve_named_proc_point("PORCA", 3500.0)
        pts += [ifgd08, porca]
    elif proc.endswith("3S"):  # RNAV STAR GNSS RWY21 to TRAMA
        entry = proc.replace(" 3S", "")
        pts.append(resolve_named_proc_point(entry, 4500.0 if entry in {"MAGUM", "FTM", "NSA", "TAGUX", "BORRO"} else 3500.0))
        ifgd06 = resolve_named_proc_point("TRAMA", 2000.0)
        ifgd06["code"] = "IFGD06"
        ifgd06["name"] = "IFGD06"
        ifgd06["navlog_note"] = "IFGD06 2000"
        trama = resolve_named_proc_point("TRAMA", 3500.0)
        pts += [ifgd06, trama]
    elif proc.endswith("2S"):
        entry = proc.replace(" 2S", "")
        if entry == "TAGUX":
            pts.append(resolve_named_proc_point("TAGUX", 5500.0))
            pts.append(proc_vor_radial("FTM", 149, 37.0, 3500.0, "FTMR149D37", "FTM R149 D37", note="JOIN FTM D37 ARC"))
            pts += proc_arc_points("FTM", 37.0, 149.0, 119.0, "CCW", 3500.0, "FTM37", note="FTM D37 ARC")
            pts.append(resolve_named_proc_point("MENDA", 3500.0))
        elif entry == "MAGUM":
            pts.append(resolve_named_proc_point("MAGUM", 5500.0))
            pts.append(resolve_named_proc_point("ATECA", 3500.0))
            pts.append(resolve_named_proc_point("SALTE", 3000.0))
        elif entry == "FTM":
            ftm = db_point("FTM", alt=5500.0, src_priority=["VOR"])
            if ftm:
                pts.append(proc_from_point(ftm, alt=5500.0, navlog_note="FTM"))
            pts.append(proc_vor_radial("FTM", 137, 32.0, 3000.0, "FTMR137D32", "FTM R137 D32", note="FTM R137 D32"))
            pts.append(resolve_named_proc_point("SALTE", 3000.0))
        elif entry == "NSA":
            nsa = db_point("NSA", alt=4500.0, src_priority=["VOR"])
            if nsa:
                pts.append(proc_from_point(nsa, alt=4500.0, navlog_note="NSA"))
            pts.append(resolve_named_proc_point("SALTE", 3000.0))
        elif entry == "BORRO":
            pts.append(resolve_named_proc_point("BORRO", 5500.0))
            pts.append(resolve_named_proc_point("SALTE", 3000.0))
    return pts


def lspo_approach_points(proc: str, include_missed: bool = False) -> List[Dict[str, Any]]:
    proc = proc.upper().strip()
    pts: List[Dict[str, Any]] = []
    lspo = lspo_arp()
    if proc == "ILS RWY21":
        pts += [
            resolve_named_proc_point("MENDA", 3500.0),
            proc_point("PDSD11", "IF D11 PDS", *dest_point(lspo.lat, lspo.lon, wrap360(206.0 + 180.0), 11.0), 2500.0, navlog_note="IF D11 PDS 2500"),
            proc_point("PDSD64", "FAP D6.4 PDS", *dest_point(lspo.lat, lspo.lon, wrap360(206.0 + 180.0), 6.4), 2500.0, navlog_note="FAP D6.4 PDS 2500"),
            proc_point("PDSD4", "D4 PDS", *dest_point(lspo.lat, lspo.lon, wrap360(206.0 + 180.0), 4.0), 1570.0, navlog_note="D4 PDS 1570"),
            proc_from_point(lspo, alt=390.0, navlog_note="RWY21"),
        ]
    elif proc == "GNSS RWY03":
        pts += [
            resolve_named_proc_point("PORCA", 3500.0),
            proc_point("D8PORCA", "D8 PORCA", *dest_point(resolve_named_proc_point("PORCA", 3500.0)["lat"], resolve_named_proc_point("PORCA", 3500.0)["lon"], 195.0, 8.0), 3500.0, navlog_note="D8 PORCA 3500"),
            resolve_named_proc_point("RAKET", 1500.0),
            proc_from_point(lspo, alt=390.0, navlog_note="RWY03"),
        ]
    elif proc == "GNSS RWY21":
        pts += [
            resolve_named_proc_point("TRAMA", 3500.0),
            proc_point("D6TRAMA", "D6 TRAMA", *dest_point(resolve_named_proc_point("TRAMA", 3500.0)["lat"], resolve_named_proc_point("TRAMA", 3500.0)["lon"], 10.0, 6.0), 2000.0, navlog_note="D6 TRAMA 2000"),
            resolve_named_proc_point("ROSED", 2000.0),
            proc_from_point(lspo, alt=390.0, navlog_note="RWY21"),
        ]
    elif proc == "VOR DME RWY21":
        pts += [
            resolve_named_proc_point("SALTE", 3000.0),
            proc_vor_radial("NSA", 198, 14.0, 2000.0, "NSAD14", "D14 NSA", note="D14 NSA 2000"),
            proc_vor_radial("NSA", 198, 17.0, 2000.0, "NSAD17", "D17 NSA", note="D17 NSA 2000"),
            proc_vor_radial("NSA", 198, 18.0, 1700.0, "NSAD18", "D18 NSA", note="D18 NSA 1700"),
            proc_vor_radial("NSA", 198, 19.0, 1400.0, "NSAD19", "D19 NSA", note="D19 NSA 1400"),
            proc_vor_radial("NSA", 198, 20.0, 1100.0, "NSAD20", "D20 NSA", note="D20 NSA 1100"),
            proc_vor_radial("NSA", 198, 21.0, 900.0, "NSAD21", "D21 NSA MAP", note="MAP D21 NSA"),
        ]
    elif proc == "VOR DME RWY03":
        pts += [
            proc_vor_radial("NSA", 198, 30.6, 1500.0, "NSAD306", "IF D30.6 NSA", note="IF D30.6 NSA 1500"),
            proc_vor_radial("NSA", 198, 26.6, 1500.0, "NSAD266", "FAF D26.6 NSA", note="FAF D26.6 NSA 1500"),
            proc_vor_radial("NSA", 198, 25.6, 1380.0, "NSAD256", "D25.6 NSA", note="D25.6 NSA 1380"),
            proc_vor_radial("NSA", 198, 24.6, 1060.0, "NSAD246", "D24.6 NSA", note="D24.6 NSA 1060"),
            proc_vor_radial("NSA", 198, 23.6, 740.0, "NSAD236", "D23.6 NSA", note="D23.6 NSA 740"),
            proc_vor_radial("NSA", 198, 22.6, 740.0, "NSAD226", "MAP D22.6 NSA", note="MAP D22.6 NSA"),
        ]

    if include_missed:
        pts += lspo_missed_points(proc)
    return pts


def lspo_missed_points(proc: str) -> List[Dict[str, Any]]:
    proc = proc.upper().strip()
    lspo = lspo_arp()
    if proc in {"ILS RWY21", "VOR DME RWY21"}:
        turn = altitude_trigger_turn_point(lspo, LPSO_RWY_TRACKS["21"], 2000.0, "LEFT", 149.0, note_prefix="MA")
        r149_lat, r149_lon = intersect_track_radial(turn["lat"], turn["lon"], LPSO_RWY_TRACKS["21"], "FTM", 149.0, fallback_dist_nm=6.0)
        int149 = proc_point("MAINTFTMR149", "MA INT FTM R149", r149_lat, r149_lon, 2000.0, navlog_note="MA INT FTM R149")
        arc_start = proc_vor_radial("FTM", 149, 37.0, 2000.0, "MAFTMD37R149", "MA FTM D37 R149", note="MA FTM D37 ARC")
        arc = proc_arc_points("FTM", 37.0, 149.0, 119.0, "CCW", 2000.0, "MAFTM37", note="MA FTM D37 ARC")
        menda = resolve_named_proc_point("MENDA", 3500.0)
        return [turn, int149, arc_start] + arc + [menda]
    if proc == "GNSS RWY03":
        turn = altitude_trigger_turn_point(lspo, LPSO_RWY_TRACKS["03"], 2500.0, "LEFT", 195.0, note_prefix="MA")
        porca = resolve_named_proc_point("PORCA", 3500.0)
        return [turn, porca]
    if proc == "GNSS RWY21":
        turn = altitude_trigger_turn_point(lspo, LPSO_RWY_TRACKS["21"], 2500.0, "LEFT", 10.0, note_prefix="MA")
        trama = resolve_named_proc_point("TRAMA", 3500.0)
        return [turn, trama]
    if proc == "VOR DME RWY03":
        turn = altitude_trigger_turn_point(lspo, LPSO_RWY_TRACKS["03"], 2500.0, "RIGHT", 198.0, note_prefix="MA")
        return [turn, proc_vor_radial("NSA", 198, 22.6, 3500.0, "MA_NSAD226", "MA D22.6 NSA", note="MA D22.6 NSA 3500")]
    return []


LPSO_SIDS = ["NSA 2N", "FTM 2N", "MAGUM 2N", "TAGUX 2N", "NSA 3S", "FTM 3S", "MAGUM 3S", "TAGUX 3S"]
LPSO_STARS = [
    "TAGUX 2S", "FTM 2S", "NSA 2S", "MAGUM 2S", "BORRO 2S",
    "TAGUX 3N", "FTM 3N", "NSA 3N", "MAGUM 3N", "BORRO 3N",
    "TAGUX 3S", "FTM 3S", "NSA 3S", "MAGUM 3S", "BORRO 3S",
]
LPSO_APPROACHES = ["ILS RWY21", "GNSS RWY03", "GNSS RWY21", "VOR DME RWY21", "VOR DME RWY03"]


def lspo_procedure_points(kind: str, proc: str, include_departure: bool = True, include_missed: bool = False) -> List[Dict[str, Any]]:
    kind = kind.upper().strip()
    if kind == "SID":
        return lspo_sid_points(proc, include_departure=include_departure)
    if kind == "STAR":
        return lspo_star_points(proc)
    if kind == "APPROACH":
        return lspo_approach_points(proc, include_missed=include_missed)
    return []

# ===============================================================
# ROUTE DATABASE / PARSER
# ===============================================================
def df_row_to_point(r: pd.Series, alt: float = 0.0) -> Point:
    p = Point(
        code=clean_code(r.get("code")),
        name=str(r.get("name") or r.get("code")),
        lat=float(r["lat"]),
        lon=float(r["lon"]),
        alt=float(alt if alt is not None else r.get("alt", 0.0) or 0.0),
        src=str(r.get("src") or "DB"),
        routes=str(r.get("routes") or ""),
        remarks=str(r.get("remarks") or ""),
    )
    if p.src == "VOR":
        p.vor_pref = "FIXED"
        p.vor_ident = p.code
    return p


def search_points(query: str, limit: int = 30, last: Optional[Point] = None) -> pd.DataFrame:
    q = query.strip().lower()
    if not q:
        return POINTS_DF.head(0)
    mask = POINTS_DF.apply(lambda r: q in " ".join(str(v).lower() for v in r.values), axis=1)
    df = POINTS_DF[mask].copy()
    if df.empty:
        return df

    def score(row: pd.Series) -> float:
        code = str(row.get("code") or "").lower()
        name = str(row.get("name") or "").lower()
        sim = difflib.SequenceMatcher(None, q, f"{code} {name}").ratio()
        starts = 1.5 if code.startswith(q) or name.startswith(q) else 0.0
        exact = 3.0 if code == q else 0.0
        src_bonus = {"IFR": 0.35, "VOR": 0.30, "AD": 0.20, "VFR": 0.0}.get(str(row.get("src")), 0.0)
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

    # decimal coordinate: 38.75,-9.12
    m = re.match(r"^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$", raw)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        return Point(code="USERCOORD", name=f"{lat:.4f},{lon:.4f}", lat=lat, lon=lon, alt=default_alt, src="USER"), ""

    # ICAO compact latlon: 3839N00837W or 383930N0083721W
    m = re.match(r"^(\d{4,6}(?:\.\d+)?[NS])(\d{5,7}(?:\.\d+)?[EW])$", raw.upper())
    if m:
        lat = dms_token_to_dd(m.group(1), is_lon=False)
        lon = dms_token_to_dd(m.group(2), is_lon=True)
        if lat is not None and lon is not None:
            return Point(code="LATLON", name=dd_to_icao(lat, lon), lat=lat, lon=lon, alt=default_alt, src="USER"), ""

    q = clean_code(raw)
    exact = POINTS_DF[POINTS_DF["code"].astype(str).str.upper() == q]
    if not exact.empty:
        # Prefer VOR if code is a VOR ident and token is exact VOR, otherwise IFR > AD > VFR.
        priority = {"VOR": 0, "IFR": 1, "AD": 2, "VFR": 3}
        exact = exact.assign(_prio=exact["src"].map(lambda x: priority.get(str(x), 9))).sort_values("_prio")
        return df_row_to_point(exact.iloc[0], alt=default_alt), ""

    fuzzy = search_points(raw, limit=1, last=last)
    if not fuzzy.empty and float(fuzzy.iloc[0].get("_score", 0)) >= 1.1:
        return df_row_to_point(fuzzy.iloc[0], alt=default_alt), f"'{raw}' resolvido como {fuzzy.iloc[0]['code']}"
    return None, f"Não encontrei ponto: {raw}"


def list_airways() -> List[str]:
    if AIRWAYS_DF.empty:
        return []
    return sorted(AIRWAYS_DF["airway"].dropna().astype(str).str.upper().unique().tolist())


def expand_airway(airway: str, start_code: str, end_code: str, default_alt: float) -> Tuple[List[Point], str]:
    airway = airway.upper().strip()
    start_code = clean_code(start_code)
    end_code = clean_code(end_code)
    sub = AIRWAYS_DF[AIRWAYS_DF["airway"].astype(str).str.upper() == airway].sort_values("seq")
    if sub.empty:
        return [], f"Airway {airway} não existe no CSV."
    codes = [clean_code(x) for x in sub["point"].tolist()]
    if start_code not in codes or end_code not in codes:
        return [], f"{airway}: endpoints {start_code}/{end_code} não estão ambos na airway."
    i1, i2 = codes.index(start_code), codes.index(end_code)
    chunk = sub.iloc[min(i1, i2) : max(i1, i2) + 1]
    if i2 < i1:
        chunk = chunk.iloc[::-1]
    pts = [
        Point(code=clean_code(r["point"]), name=clean_code(r["point"]), lat=float(r["lat"]), lon=float(r["lon"]), alt=default_alt, src="IFR", routes=airway, remarks=str(r.get("remarks", "")))
        for _, r in chunk.iterrows()
    ]
    return pts, ""


def tokenize_route_text(text: str) -> List[str]:
    # Keep VOR/R/D tokens intact; split commas, semicolons and whitespace.
    text = text.replace(";", " ").replace(",", " ")
    return [t.strip().upper() for t in re.split(r"\s+", text) if t.strip()]


def parse_route_text(text: str, default_alt: float) -> Tuple[List[Point], List[str]]:
    tokens = tokenize_route_text(text)
    airways_set = set(list_airways())
    out: List[Point] = []
    notes: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "DCT":
            i += 1
            continue
        # POINT AIRWAY POINT syntax. Example: MAGUM UZ218 ATECA
        if i + 2 < len(tokens) and tokens[i + 1] in airways_set:
            p_start, msg1 = resolve_token(tokens[i], default_alt, out[-1] if out else None)
            if msg1:
                notes.append(msg1)
            airway = tokens[i + 1]
            p_end, msg2 = resolve_token(tokens[i + 2], default_alt, p_start)
            if msg2:
                notes.append(msg2)
            if p_start and p_end:
                expanded, msg = expand_airway(airway, p_start.code, p_end.code, default_alt)
                if expanded:
                    if not out or clean_code(out[-1].code) != clean_code(expanded[0].code):
                        out.append(expanded[0])
                    out.extend(expanded[1:])
                else:
                    notes.append(msg + " Usei DCT entre endpoints.")
                    if not out or clean_code(out[-1].code) != p_start.code:
                        out.append(p_start)
                    out.append(p_end)
            i += 3
            continue
        p, msg = resolve_token(tok, default_alt, out[-1] if out else None)
        if msg:
            notes.append(msg)
        if p:
            out.append(p)
        i += 1

    # Assign stable ids
    for p in out:
        p.uid = next_uid()
    return out, notes

# ===============================================================
# ROUTE CALCULATION
# ===============================================================
def next_uid() -> int:
    st.session_state["next_uid"] = int(st.session_state.get("next_uid", 1)) + 1
    return int(st.session_state["next_uid"])


def ensure_point_ids() -> None:
    changed = False
    for i, w in enumerate(st.session_state.get("wps", [])):
        if w.get("uid") is None:
            w["uid"] = next_uid()
            changed = True
    if changed:
        st.session_state.wps = st.session_state.wps


def current_profile() -> Dict[str, float]:
    return {
        "climb_tas": float(st.session_state.climb_tas),
        "cruise_tas": float(st.session_state.cruise_tas),
        "descent_tas": float(st.session_state.descent_tas),
        "fuel_flow_lh": float(st.session_state.fuel_flow_lh),
        "taxi_fuel_l": float(st.session_state.taxi_fuel_l),
    }


def build_route_nodes(user_wps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(user_wps) < 2:
        return []
    p = current_profile()
    out: List[Dict[str, Any]] = []
    for i in range(len(user_wps) - 1):
        A = user_wps[i]
        B = user_wps[i + 1]
        out.append(A.copy())
        dist = gc_dist_nm(A["lat"], A["lon"], B["lat"], B["lon"])
        tc = gc_course_tc(A["lat"], A["lon"], B["lat"], B["lon"])
        wf = wind_for_point(A)[0]
        wk = wind_for_point(A)[1]
        if A.get("no_auto_vnav") or B.get("no_auto_vnav"):
            continue
        if B["alt"] > A["alt"]:
            t_min = (B["alt"] - A["alt"]) / max(float(st.session_state.roc_fpm), 1.0)
            _, _, gs = wind_triangle(tc, p["climb_tas"], wf, wk)
            d_need = gs * t_min / 60.0
            if 0.05 < d_need < dist - 0.05:
                lat, lon = point_along_gc(A["lat"], A["lon"], B["lat"], B["lon"], d_need)
                out.append(
                    Point(code="TOC", name="TOC", lat=lat, lon=lon, alt=B["alt"], src="CALC", uid=next_uid()).to_dict()
                )
        elif B["alt"] < A["alt"]:
            t_min = (A["alt"] - B["alt"]) / max(float(st.session_state.rod_fpm), 1.0)
            _, _, gs = wind_triangle(tc, p["descent_tas"], wf, wk)
            d_need = gs * t_min / 60.0
            if 0.05 < d_need < dist - 0.05:
                lat, lon = point_along_gc(A["lat"], A["lon"], B["lat"], B["lon"], max(0.0, dist - d_need))
                out.append(
                    Point(code="TOD", name="TOD", lat=lat, lon=lon, alt=A["alt"], src="CALC", uid=next_uid()).to_dict()
                )
    out.append(user_wps[-1].copy())
    return out


def wind_for_point(P: Dict[str, Any]) -> Tuple[int, int]:
    if bool(st.session_state.use_global_wind):
        return int(st.session_state.wind_from), int(st.session_state.wind_kt)
    return int(P.get("wind_from") or st.session_state.wind_from), int(P.get("wind_kt") or st.session_state.wind_kt)


def build_legs(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(nodes) < 2:
        return []
    p = current_profile()
    base_dt = None
    if str(st.session_state.start_clock).strip():
        try:
            h, m = map(int, str(st.session_state.start_clock).strip().split(":"))
            base_dt = dt.datetime.combine(dt.date.today(), dt.time(hour=h, minute=m))
        except Exception:
            base_dt = None

    t_cursor = 0
    efob = max(0.0, float(st.session_state.start_efob) - p["taxi_fuel_l"])
    legs: List[Dict[str, Any]] = []

    for i in range(len(nodes) - 1):
        A, B = nodes[i], nodes[i + 1]
        if is_dme_arc_leg(A, B):
            dist_raw = dme_arc_distance_nm(A, B)
            tc = dme_arc_course(A, B)
        else:
            dist_raw = gc_dist_nm(A["lat"], A["lon"], B["lat"], B["lon"])
            tc = gc_course_tc(A["lat"], A["lon"], B["lat"], B["lon"])
        dist = rd(dist_raw)
        wf, wk = wind_for_point(A)
        if B["alt"] > A["alt"] + 1:
            profile = "CLIMB"
            tas = p["climb_tas"]
        elif B["alt"] < A["alt"] - 1:
            profile = "DESCENT"
            tas = p["descent_tas"]
        else:
            profile = "LEVEL"
            tas = p["cruise_tas"]
        _, th, gs = wind_triangle(tc, tas, wf, wk)
        mh = apply_mag_var(th, float(st.session_state.mag_var), bool(st.session_state.mag_is_east))
        ete = rt((dist / max(gs, 1e-9)) * 3600.0) if gs > 0 and dist > 0 else 0
        burn = rf(p["fuel_flow_lh"] * ete / 3600.0)
        efob_start = efob
        efob_end = max(0.0, rf(efob_start - burn))
        clk_start = (base_dt + dt.timedelta(seconds=t_cursor)).strftime("%H:%M") if base_dt else f"T+{mmss(t_cursor)}"
        clk_end = (base_dt + dt.timedelta(seconds=t_cursor + ete)).strftime("%H:%M") if base_dt else f"T+{mmss(t_cursor + ete)}"

        pref_vor = A.get("vor_ident") if A.get("vor_pref") == "FIXED" else ""
        track = tracking_instruction(A, B, pref_vor)
        leg = {
            "i": len(legs) + 1,
            "A": A,
            "B": B,
            "profile": profile,
            "TC": tc,
            "TH": th,
            "MH": mh,
            "TAS": tas,
            "GS": gs,
            "Dist": dist,
            "time_sec": ete,
            "burn": burn,
            "efob_start": efob_start,
            "efob_end": efob_end,
            "clock_start": clk_start,
            "clock_end": clk_end,
            "wind_from": wf,
            "wind_kt": wk,
            "tracking": track,
            "is_dme_arc": is_dme_arc_leg(A, B),
        }
        legs.append(leg)
        t_cursor += ete
        efob = efob_end

        stop_min = float(B.get("stop_min") or 0.0)
        if stop_min > 0:
            stop_sec = rt(stop_min * 60.0)
            stop_burn = rf(p["fuel_flow_lh"] * stop_sec / 3600.0)
            efob_start2 = efob
            efob_end2 = max(0.0, rf(efob_start2 - stop_burn))
            clk_start2 = (base_dt + dt.timedelta(seconds=t_cursor)).strftime("%H:%M") if base_dt else f"T+{mmss(t_cursor)}"
            clk_end2 = (base_dt + dt.timedelta(seconds=t_cursor + stop_sec)).strftime("%H:%M") if base_dt else f"T+{mmss(t_cursor + stop_sec)}"
            legs.append(
                {
                    "i": len(legs) + 1,
                    "A": B,
                    "B": B,
                    "profile": "STOP",
                    "TC": 0,
                    "TH": 0,
                    "MH": 0,
                    "TAS": 0,
                    "GS": 0,
                    "Dist": 0,
                    "time_sec": stop_sec,
                    "burn": stop_burn,
                    "efob_start": efob_start2,
                    "efob_end": efob_end2,
                    "clock_start": clk_start2,
                    "clock_end": clk_end2,
                    "wind_from": wf,
                    "wind_kt": wk,
                    "tracking": "STOP",
                }
            )
            t_cursor += stop_sec
            efob = efob_end2
    return legs


def recalc_route() -> None:
    nodes = build_route_nodes(st.session_state.wps)
    st.session_state.route_nodes = nodes
    st.session_state.legs = build_legs(nodes)

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
    keys = ["code", "name", "lat", "lon", "alt", "src", "routes", "remarks", "stop_min", "vor_pref", "vor_ident", "arc_vor", "arc_radius_nm", "arc_start_radial", "arc_end_radial", "arc_direction", "arc_endpoint"]
    return [
        {k: w.get(k) for k in keys if k in w}
        for w in st.session_state.wps
    ]


def load_routes_from_gist() -> Dict[str, Any]:
    token, gist_id = get_gist_credentials()
    if not token or not gist_id or requests is None:
        return {}
    try:
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers={"Authorization": f"token {token}"}, timeout=10)
        if r.status_code != 200:
            return {}
        files = r.json().get("files", {})
        content = files.get("routes.json", {}).get("content", "{}")
        return json.loads(content or "{}")
    except Exception:
        return {}


def save_routes_to_gist(routes: Dict[str, Any]) -> Tuple[bool, str]:
    token, gist_id = get_gist_credentials()
    if not token or not gist_id or requests is None:
        return False, "Gist desativado: configura GITHUB_TOKEN e ROUTES_GIST_ID."
    try:
        payload = {"files": {"routes.json": {"content": json.dumps(routes, indent=2)}}}
        r = requests.patch(f"https://api.github.com/gists/{gist_id}", headers={"Authorization": f"token {token}"}, json=payload, timeout=10)
        return r.status_code in {200, 201}, "Rotas guardadas." if r.status_code in {200, 201} else f"Erro Gist {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, str(e)

# ===============================================================
# PDF GENERATION
# ===============================================================
def _pdf_key_norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


PDF_ALIASES: Dict[str, List[str]] = {
    "FLIGHT_LEVEL_ALTITUDE": ["FLIGHT_LEVEL/ALTITUDE", "FLIGHT_LEVEL_ALTITUDE", "FLIGHT LEVEL / ALTITUDE", "FLIGHT LEVEL ALTITUDE", "FLIGHT_LEVEL_ALT", "FL_ALT"],
    "TEMP_ISA_DEV": ["TEMP/ISA_DEV", "TEMP / ISA DEV", "TEMP_ISA_DEV", "TEMP ISA DEV", "ISA_DEV", "TEMP_ISA"],
    "MAG_VAR": ["MAG_VAR", "MAG. VAR", "MAG VAR", "MAGVAR"],
    "WIND": ["WIND", "Wind"],
}


def expand_pdf_aliases(data: Dict[str, Any]) -> Dict[str, Any]:
    out = data.copy()
    norm_map = {_pdf_key_norm(k): v for k, v in data.items()}
    for canonical, aliases in PDF_ALIASES.items():
        val = data.get(canonical)
        if val in {None, ""}:
            for a in aliases:
                if a in data and data[a] not in {None, ""}:
                    val = data[a]
                    break
                nv = norm_map.get(_pdf_key_norm(a))
                if nv not in {None, ""}:
                    val = nv
                    break
        if val is not None:
            out[canonical] = val
            for a in aliases:
                out[a] = val
                out[_pdf_key_norm(a)] = val
    for k, v in list(out.items()):
        out[_pdf_key_norm(k)] = v
    return out


def _pdf_page_size(page: Any) -> Tuple[float, float]:
    mb = page.MediaBox
    return float(mb[2]) - float(mb[0]), float(mb[3]) - float(mb[1])


def _stamp_text_center(c: Any, x: float, y: float, text: str, size: float = 6.5) -> None:
    if not text:
        return
    c.setFont("Helvetica-Bold", size)
    c.drawCentredString(x, y, str(text))


def _header_stamp_values(data: Dict[str, Any]) -> Dict[str, str]:
    expanded = expand_pdf_aliases(data)
    return {
        "fl_alt": str(expanded.get("FLIGHT_LEVEL_ALTITUDE", "") or expanded.get("FLIGHT_LEVEL_ALT", "")),
        "wind": str(expanded.get("WIND", "")),
        "mag_var": str(expanded.get("MAG_VAR", "") or expanded.get("MAGVAR", "")),
        "temp_isa": str(expanded.get("TEMP_ISA_DEV", "") or expanded.get("TEMP_ISA", "")),
    }


def _stamp_non_field_navlog_headers(pdf: Any, data: Dict[str, Any], template: Path) -> None:
    """Stamp the four ENROUTE INFORMATION header cells when the template boxes are not AcroForm fields.

    The uploaded Sevenair form has these labels as printed table cells rather than PDF fields,
    so aliases alone cannot populate them. This overlay writes the values into those cells.
    """
    try:
        from reportlab.pdfgen import canvas
    except Exception:
        return

    vals = _header_stamp_values(data)
    if not any(vals.values()):
        return

    for page_index, page in enumerate(pdf.pages):
        page_width, page_height = _pdf_page_size(page)
        # Coordinates are relative to the A5 form area. Page 1 has the table lower; continuation pages have it near the top.
        is_continuation = page_index > 0 or "_1" in template.stem or "CONT" in template.stem.upper()
        if page_width > 650:
            ox = page_width / 2.0 if is_continuation else 0.0
        else:
            ox = 0.0
        # A5 form width used by NAVLOG_FORM.pdf. If the template is already A5, use the whole width.
        cw = min(421.0, page_width - ox)
        y = 504.0 if is_continuation else 367.0

        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=(page_width, page_height))
        # Center positions of the four printed cells.
        _stamp_text_center(c, ox + cw * 0.345, y, vals["fl_alt"], size=6.2)
        _stamp_text_center(c, ox + cw * 0.572, y, vals["wind"], size=6.2)
        _stamp_text_center(c, ox + cw * 0.766, y, vals["mag_var"], size=6.2)
        _stamp_text_center(c, ox + cw * 0.925, y, vals["temp_isa"], size=6.2)
        c.save()
        packet.seek(0)
        overlay = PdfReader(packet).pages[0]
        PageMerge(page).add(overlay).render()


def fill_pdf(template: Path, out: Path, data: Dict[str, Any]) -> Path:
    data_expanded = expand_pdf_aliases(data)
    pdf = PdfReader(str(template))
    if pdf.Root.AcroForm:
        pdf.Root.AcroForm.update(PdfDict(NeedAppearances=True))
    small_field_re = re.compile(r"(Waypoint|Navaid|Identifier|Frequency|Name|Lat|Long|Fix)", re.I)
    for page in pdf.pages:
        annots = getattr(page, "Annots", None)
        if not annots:
            continue
        for a in annots:
            if a.Subtype == PdfName("Widget") and a.T:
                key = str(a.T)[1:-1]
                value = data_expanded.get(key, data_expanded.get(_pdf_key_norm(key)))
                if value is not None:
                    a.update(PdfDict(V=str(value), DV=str(value)))
                    if small_field_re.search(key):
                        # Smaller text so long IFR fix names / VOR radial-distance labels fit the boxes.
                        a.update(PdfDict(DA="/Helv 4.5 Tf 0 g"))
    _stamp_non_field_navlog_headers(pdf, data_expanded, template)
    PdfWriter(str(out), trailer=pdf).write()
    return out


def choose_vor_for_point(P: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(P.get("name", "")).upper().startswith(("TOC", "TOD")):
        return None
    if P.get("vor_pref") == "NONE":
        return None
    if P.get("vor_pref") == "FIXED" and P.get("vor_ident"):
        return get_vor(str(P.get("vor_ident")))
    if P.get("src") == "VOR":
        return get_vor(str(P.get("code") or P.get("name")))
    return nearest_vor(float(P["lat"]), float(P["lon"]))


def fill_leg_payload(d: Dict[str, Any], idx: int, L: Dict[str, Any], acc_d: float, acc_t: int, prefix: str = "Leg") -> None:
    P = L["B"]
    d[f"{prefix}{idx:02d}_Waypoint"] = str(P.get("navlog_note") or P.get("code") or P.get("name"))
    d[f"{prefix}{idx:02d}_Altitude_FL"] = str(int(round(float(P.get("alt", 0)))))
    if L["profile"] != "STOP":
        d[f"{prefix}{idx:02d}_True_Course"] = f"{int(round(L['TC'])):03d}"
        d[f"{prefix}{idx:02d}_True_Heading"] = f"{int(round(L['TH'])):03d}"
        d[f"{prefix}{idx:02d}_Magnetic_Heading"] = f"{int(round(L['MH'])):03d}"
        d[f"{prefix}{idx:02d}_True_Airspeed"] = str(int(round(L["TAS"])))
        d[f"{prefix}{idx:02d}_Ground_Speed"] = str(int(round(L["GS"])))
        d[f"{prefix}{idx:02d}_Leg_Distance"] = f"{L['Dist']:.1f}"
    else:
        for field in ["True_Course", "True_Heading", "Magnetic_Heading", "True_Airspeed", "Ground_Speed"]:
            d[f"{prefix}{idx:02d}_{field}"] = ""
        d[f"{prefix}{idx:02d}_Leg_Distance"] = "0.0"
    d[f"{prefix}{idx:02d}_Cumulative_Distance"] = f"{acc_d:.1f}"
    d[f"{prefix}{idx:02d}_Leg_ETE"] = pdf_time(L["time_sec"])
    d[f"{prefix}{idx:02d}_Cumulative_ETE"] = pdf_time(acc_t)
    d[f"{prefix}{idx:02d}_ETO"] = ""
    d[f"{prefix}{idx:02d}_Planned_Burnoff"] = fmt_unit(L["burn"])
    d[f"{prefix}{idx:02d}_Estimated_FOB"] = fmt_unit(L["efob_end"])
    vor = choose_vor_for_point(P)
    d[f"{prefix}{idx:02d}_Navaid_Identifier"] = format_vor_id(vor)
    d[f"{prefix}{idx:02d}_Navaid_Frequency"] = format_radial_dist(vor, float(P["lat"]), float(P["lon"]))


def build_pdf_payload(legs: List[Dict[str, Any]], header: Dict[str, str], start: int = 0, count: int = 22) -> Dict[str, Any]:
    chunk = legs[start : start + count]
    total_sec = sum(L["time_sec"] for L in legs)
    total_burn = rf(sum(L["burn"] for L in legs))
    total_dist = rd(sum(L["Dist"] for L in legs))
    climb_sec = sum(L["time_sec"] for L in legs if L["profile"] == "CLIMB")
    level_sec = sum(L["time_sec"] for L in legs if L["profile"] == "LEVEL")
    desc_sec = sum(L["time_sec"] for L in legs if L["profile"] == "DESCENT")
    climb_burn = rf(sum(L["burn"] for L in legs if L["profile"] == "CLIMB"))
    d: Dict[str, Any] = {
        "CALLSIGN": header.get("callsign", ""),
        "REGISTRATION": header.get("registration", ""),
        "STUDENT": header.get("student", ""),
        "LESSON": header.get("lesson", ""),
        "INSTRUTOR": header.get("instructor", ""),
        "DEPT": header.get("dept_freq", ""),
        "ENROUTE": header.get("enroute_freq", ""),
        "ARRIVAL": header.get("arrival_freq", ""),
        "ETD/ETA": f"{header.get('etd','')}/{header.get('eta','')}".strip("/"),
        "Departure_Airfield": str(st.session_state.wps[0].get("code") or st.session_state.wps[0].get("name")) if st.session_state.wps else "",
        "Arrival_Airfield": str(st.session_state.wps[-1].get("code") or st.session_state.wps[-1].get("name")) if st.session_state.wps else "",
        "WIND": f"{int(st.session_state.wind_from):03d}/{int(st.session_state.wind_kt):02d}",
        "Wind": f"{int(st.session_state.wind_from):03d}/{int(st.session_state.wind_kt):02d}",
        "MAG_VAR": f"{fmt_num_clean(abs(float(st.session_state.mag_var)))}°{'E' if st.session_state.mag_is_east else 'W'}",
        "MAGVAR": f"{fmt_num_clean(abs(float(st.session_state.mag_var)))}°{'E' if st.session_state.mag_is_east else 'W'}",
        "FLIGHT_LEVEL/ALTITUDE": header.get("fl_alt", ""),
        "FLIGHT_LEVEL_ALTITUDE": header.get("fl_alt", ""),
        "FL_ALT": header.get("fl_alt", ""),
        "TEMP/ISA_DEV": header.get("temp_isa", ""),
        "TEMP_ISA_DEV": header.get("temp_isa", ""),
        "ISA_DEV": header.get("temp_isa", ""),
        "FLT TIME": pdf_time(total_sec),
        "CLIMB FUEL": fmt_unit(climb_burn),
        "OBSERVATIONS": f"Climb {pdf_time(climb_sec)} / Cruise {pdf_time(level_sec)} / Descent {pdf_time(desc_sec)}",
        "Leg_Number": str(len(legs)),
        "AIRCRAFT_TYPE": str(st.session_state.aircraft_type),
    }
    acc_d, acc_t = 0.0, 0
    for i, L in enumerate(chunk, start=1 if start == 0 else 12):
        acc_d = rd(acc_d + L["Dist"])
        acc_t += int(L["time_sec"])
        fill_leg_payload(d, i, L, acc_d, acc_t)
    d["Leg23_Leg_Distance"] = f"{total_dist:.1f}"
    d["Leg23_Leg_ETE"] = pdf_time(total_sec)
    d["Leg23_Planned_Burnoff"] = fmt_unit(total_burn)
    d["Leg23_Estimated_FOB"] = fmt_unit(legs[-1]["efob_end"]) if legs else ""
    return d


def generate_briefing_pdf(path: Path, rows: List[Dict[str, Any]]) -> Optional[Path]:
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except Exception:
        return None

    c = canvas.Canvas(str(path), pagesize=landscape(A4))
    width, height = landscape(A4)
    x0, y = 12 * mm, height - 14 * mm
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x0, y, f"NAVLOG briefing — {st.session_state.aircraft_type}")
    y -= 9 * mm

    headers = list(rows[0].keys()) if rows else []
    col_w = [14, 30, 30, 14, 14, 16, 16, 16, 16, 16, 16, 18, 28, 26, 55]
    col_w = [w * mm for w in col_w[: len(headers)]]

    def draw_header(ypos: float) -> float:
        c.setFont("Helvetica-Bold", 7)
        x = x0
        for h, w in zip(headers, col_w):
            c.drawString(x, ypos, str(h)[:16])
            x += w
        c.line(x0, ypos - 2, x0 + sum(col_w), ypos - 2)
        return ypos - 5 * mm

    y = draw_header(y)
    c.setFont("Helvetica", 7)
    for r in rows:
        if y < 12 * mm:
            c.showPage()
            y = height - 14 * mm
            y = draw_header(y)
            c.setFont("Helvetica", 7)
        x = x0
        for h, w in zip(headers, col_w):
            c.drawString(x, y, str(r.get(h, ""))[:24])
            x += w
        y -= 4.5 * mm
    c.save()
    return path


def summary_metrics(legs: List[Dict[str, Any]]) -> Dict[str, float]:
    return {
        "time": sum(L["time_sec"] for L in legs),
        "dist": rd(sum(L["Dist"] for L in legs)),
        "burn": rf(sum(L["burn"] for L in legs)),
        "efob": legs[-1]["efob_end"] if legs else float(st.session_state.start_efob),
        "legs": len(legs),
    }


def legs_to_dataframe(legs: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    acc_d, acc_t = 0.0, 0
    for L in legs:
        acc_d = rd(acc_d + L["Dist"])
        acc_t += L["time_sec"]
        P = L["B"]
        vor = choose_vor_for_point(P)
        rows.append(
            {
                "Leg": L["i"],
                "From": L["A"].get("code") or L["A"].get("name"),
                "To": P.get("navlog_note") or P.get("code") or P.get("name"),
                "Profile": L["profile"],
                "Alt": int(round(float(P.get("alt", 0)))),
                "TC": f"{int(round(L['TC'])):03d}" if L["profile"] != "STOP" else "",
                "TH": f"{int(round(L['TH'])):03d}" if L["profile"] != "STOP" else "",
                "MH": f"{int(round(L['MH'])):03d}" if L["profile"] != "STOP" else "",
                "TAS": int(round(L["TAS"])),
                "GS": int(round(L["GS"])),
                "Dist": f"{L['Dist']:.1f}",
                "CumDist": f"{acc_d:.1f}",
                "ETE": pdf_time(L["time_sec"]),
                "CumETE": pdf_time(acc_t),
                "Fuel": fmt_unit(L["burn"]),
                "EFOB": fmt_unit(L["efob_end"]),
                "Wind": f"{int(L['wind_from']):03d}/{int(L['wind_kt'])}",
                "VOR": format_vor_id(vor),
                "Radial/Dist": format_radial_dist(vor, float(P["lat"]), float(P["lon"])),
                "Tracking": L.get("tracking", ""),
            }
        )
    return pd.DataFrame(rows)


def route_item15(wps: List[Dict[str, Any]]) -> str:
    if len(wps) < 2:
        return ""
    tokens: List[str] = []
    seq = wps[:]
    # Strip departure/arrival aerodromes if they look like ICAO aerodrome codes.
    if re.fullmatch(r"[A-Z]{4}", clean_code(seq[0].get("code"))):
        seq = seq[1:]
    if seq and re.fullmatch(r"[A-Z]{4}", clean_code(seq[-1].get("code"))):
        seq = seq[:-1]
    for w in seq:
        src = str(w.get("src", "")).upper()
        if src == "CALC":
            continue
        if src == "PROC" and str(w.get("navlog_note", "")).upper().startswith(("MA ", "INT", "TRK", "X ", "D", "IF ", "FAP", "MAP", "MAINT", "CROSS", "2000", "1400", "2500")):
            continue
        code = clean_code(w.get("code") or w.get("name"))
        name_code = clean_code(w.get("name"))
        token = code or name_code
        # O objetivo operacional aqui é preservar os nomes/códigos dos pontos escolhidos
        # (incluindo localidades VFR). Só cai para coordenadas se for um clique/ponto sem nome útil.
        if src == "USER" and (not token or token.startswith("WP")):
            token = dd_to_icao(float(w["lat"]), float(w["lon"]))
        elif not token:
            token = dd_to_icao(float(w["lat"]), float(w["lon"]))
        tokens.append(token)
    return "DCT " + " DCT ".join(tokens) if tokens else ""


def html_pills(items: Iterable[Tuple[str, str]]) -> None:
    html = "".join([f"<span class='pill {klass}'>{label}</span>" for label, klass in items])
    st.markdown(html, unsafe_allow_html=True)

# ===============================================================
# MAP
# ===============================================================
def map_start_center() -> Tuple[float, float]:
    hit = POINTS_DF[POINTS_DF["code"].astype(str).str.upper() == "LPSO"]
    if not hit.empty:
        return float(hit.iloc[0]["lat"]), float(hit.iloc[0]["lon"])
    return LPSO_FALLBACK_CENTER


def make_base_map() -> folium.Map:
    center = map_start_center()
    m = folium.Map(location=center, zoom_start=9, tiles=None, control_scale=True, prefer_canvas=True)
    folium.TileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", name="OSM", attr="© OpenStreetMap").add_to(m)
    folium.TileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", name="OpenTopoMap", attr="© OpenTopoMap").add_to(m)
    folium.TileLayer(
        "https://services.arcgisonline.com/ArcGIS/rest/services/World_Hillshade/MapServer/tile/{z}/{y}/{x}",
        name="Hillshade",
        attr="© Esri",
    ).add_to(m)
    m.fit_bounds(PT_BOUNDS)

    token = get_openaip_token()
    if bool(st.session_state.get("show_openaip", False)) and token:
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


def render_route_map(wps: List[Dict[str, Any]], nodes: List[Dict[str, Any]], legs: List[Dict[str, Any]], *, key: str = "mainmap") -> Dict[str, Any]:
    m = make_base_map()

    # IFR/VOR reference layer, lightweight and clustered.
    if bool(st.session_state.show_ref_points):
        cluster = MarkerCluster(name="Pontos IFR/VFR/VOR", disableClusteringAtZoom=10).add_to(m)
        ref = POINTS_DF
        src_filter = set(st.session_state.ref_layers)
        ref = ref[ref["src"].isin(src_filter)] if src_filter else ref.head(0)
        for _, r in ref.iterrows():
            src = str(r.get("src"))
            color = {"IFR": "#2563eb", "VOR": "#dc2626", "AD": "#111827", "VFR": "#16a34a"}.get(src, "#334155")
            radius = 4 if src in {"IFR", "VOR"} else 3
            folium.CircleMarker(
                (float(r["lat"]), float(r["lon"])),
                radius=radius,
                color=color,
                weight=1,
                fill=True,
                fill_opacity=0.9,
                tooltip=f"[{src}] {r.get('code')} — {r.get('name')} {r.get('routes','')}",
            ).add_to(cluster)

    # Airways layer from CSV: always all loaded airways when enabled.
    if bool(st.session_state.show_airways) and not AIRWAYS_DF.empty:
        for airway, grp in AIRWAYS_DF.groupby("airway"):
            pts = [(float(r["lat"]), float(r["lon"])) for _, r in grp.sort_values("seq").iterrows()]
            if len(pts) >= 2:
                folium.PolyLine(pts, color="#64748b", weight=2, opacity=0.55, tooltip=airway).add_to(m)

    # Route legs.
    for L in legs:
        if L["profile"] == "STOP":
            folium.CircleMarker((L["A"]["lat"], L["A"]["lon"]), radius=8, color="#dc2626", fill=True, fill_opacity=0.7, tooltip="STOP").add_to(m)
            continue
        color = PROFILE_COLORS.get(L["profile"], "#7c3aed")
        latlngs = dme_arc_polyline(L["A"], L["B"]) if L.get("is_dme_arc") else [(L["A"]["lat"], L["A"]["lon"]), (L["B"]["lat"], L["B"]["lon"])]
        arc_txt = " ARC" if L.get("is_dme_arc") else ""
        folium.PolyLine(latlngs, color="#ffffff", weight=8, opacity=1).add_to(m)
        folium.PolyLine(latlngs, color=color, weight=4, opacity=1, tooltip=f"L{L['i']} {L['profile']}{arc_txt} {pdf_time(L['time_sec'])}").add_to(m)


    # Waypoints.
    for idx, w in enumerate(wps, start=1):
        lat, lon = float(w["lat"]), float(w["lon"])
        src = w.get("src", "USER")
        color = {"IFR": "#2563eb", "VOR": "#dc2626", "AD": "#111827", "VFR": "#16a34a", "USER": "#f97316", "VORFIX": "#be123c", "DMEARC": "#0891b2"}.get(src, "#0f172a")
        folium.CircleMarker((lat, lon), radius=6, color="#ffffff", weight=3, fill=True, fill_opacity=1).add_to(m)
        folium.CircleMarker((lat, lon), radius=5, color=color, fill=True, fill_opacity=1, tooltip=f"{idx}. {w.get('code') or w.get('name')} [{src}]").add_to(m)
        label = f"{idx}. {w.get('navlog_note') or w.get('code') or w.get('name')}"
        add_div_marker(
            m,
            lat,
            lon,
            f"<div style='transform:translate(8px,-22px);font-weight:800;font-size:12px;color:#0f172a;text-shadow:-1px -1px 0 white,1px -1px 0 white,-1px 1px 0 white,1px 1px 0 white;white-space:nowrap'>{label}</div>",
        )

    folium.LayerControl(collapsed=False).add_to(m)
    return st_folium(m, width=None, height=720, key=key)

# ===============================================================
# INITIAL SESSION STATE
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
ss("ref_layers", ["IFR", "VOR", "AD", "VFR"])
ss("show_airways", True)
ss("show_openaip", True)
ss("openaip_opacity", 0.65)
ss("saved_routes", {})
ensure_point_ids()

# ===============================================================
# HEADER
# ===============================================================
st.markdown(
    f"""
<div class='nav-hero'>
  <div class='nav-title'>🧭 {APP_TITLE}</div>
  <div class='nav-sub'>Planeamento VFR/IFR low offline por CSV local, com airways, VOR radial fixes, SID/STAR/APP LPSO, navlog e PDF.</div>
</div>
""",
    unsafe_allow_html=True,
)

legs = st.session_state.get("legs", [])
if legs:
    sm = summary_metrics(legs)
    html_pills(
        [
            (f"ETE {pdf_time(sm['time'])}", "pill-good"),
            (f"Dist {sm['dist']:.1f} NM", "pill-good"),
            (f"Fuel {fmt_unit(sm['burn'])} L", "pill-good"),
            (f"EFOB final {fmt_unit(sm['efob'])} L", "pill-good" if sm["efob"] >= 30 else "pill-warn"),
            (f"{sm['legs']} legs", ""),
            (f"{st.session_state.aircraft_type}", ""),
        ]
    )
else:
    html_pills(
        [
            (f"{len(POINTS_DF[POINTS_DF.src == 'IFR'])} IFR pts", ""),
            (f"{len(AIRWAYS_DF.airway.unique()) if not AIRWAYS_DF.empty else 0} airways", ""),
            (f"{len(VOR_DF)} VOR", ""),
            ("PDF templates OK" if TEMPLATE_MAIN.exists() else "PDF template em falta", "pill-good" if TEMPLATE_MAIN.exists() else "pill-warn"),
        ]
    )

# ===============================================================
# TOP SETUP - no sidebar
# ===============================================================
with st.container():
    st.markdown("#### 1 · Setup do voo")
    setup_a, setup_b, setup_c, setup_d = st.columns([1.15, 1.1, 1.1, 0.8], gap="large")

    with setup_a:
        ac_names = list(AIRCRAFT_PROFILES)
        ac = st.selectbox(
            "Aeronave",
            ac_names,
            index=ac_names.index(st.session_state.aircraft_type) if st.session_state.aircraft_type in ac_names else 0,
            key="setup_aircraft_type",
        )
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
            recalc_route()
            st.toast("Rota recalculada")

    st.markdown("<hr>", unsafe_allow_html=True)

# ===============================================================
# TABS
# ===============================================================
tab_route, tab_map, tab_navlog = st.tabs(["1 · Rota", "2 · Mapa / clique", "3 · Navlog / PDF"])

# ---------------------------------------------------------------
# ROUTE TAB
# ---------------------------------------------------------------
with tab_route:
    st.markdown("#### 2 · Construir rota")
    st.caption("Fluxo recomendado: confirma o setup acima → escreve/cola a rota ou pesquisa pontos → revê waypoints → recalcula → segue para mapa ou PDF.")
    left, right = st.columns([1.05, 0.95], gap="large")

    with left:
        st.markdown("#### Construção rápida por texto")
        st.caption("Exemplos: `LPSO MAGUM UZ218 ATECA LPFR`, `LPCS CAS/R180/D12 ESP/R090/D15 LPFR`, `LPPR MANIK UP600 MAGUM`.")
        route_text = st.text_area("Rota", height=92, placeholder="LPSO MAGUM UZ218 ATECA LPFR")
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            if st.button("Substituir rota", type="primary", use_container_width=True):
                pts, notes = parse_route_text(route_text, float(st.session_state.default_alt))
                st.session_state.wps = [p.to_dict() for p in pts]
                recalc_route()
                for n in notes:
                    st.warning(n)
        with c2:
            if st.button("Acrescentar", use_container_width=True):
                last = Point.from_dict(st.session_state.wps[-1]) if st.session_state.wps else None
                pts, notes = parse_route_text(route_text, float(st.session_state.default_alt))
                st.session_state.wps.extend([p.to_dict() for p in pts])
                recalc_route()
                for n in notes:
                    st.warning(n)
        with c3:
            if st.button("Limpar", use_container_width=True):
                st.session_state.wps = []
                st.session_state.route_nodes = []
                st.session_state.legs = []
                st.rerun()

        st.markdown("#### Pesquisa / adicionar ponto")
        q = st.text_input("Pesquisar por código/nome/rota", placeholder="MAGUM, ATECA, CAS, LPSO, Évora…")
        results = search_points(q, limit=12, last=Point.from_dict(st.session_state.wps[-1]) if st.session_state.wps else None)
        if q and results.empty:
            st.info("Sem resultados. Também podes usar coordenadas decimais ou fix VOR tipo CAS/R180/D12.")
        for i, r in results.iterrows():
            cols = st.columns([0.14, 0.60, 0.16, 0.10])
            with cols[0]:
                st.markdown(f"`{r['src']}`")
            with cols[1]:
                st.markdown(f"**{r['code']}** — {r['name']}  ")
                st.caption(f"{float(r['lat']):.5f}, {float(r['lon']):.5f} · {r.get('routes','')}")
            with cols[2]:
                alt = st.number_input("Alt", 0.0, 45000.0, float(st.session_state.default_alt), 100.0, key=f"alt_search_{i}", label_visibility="collapsed")
            with cols[3]:
                if st.button("➕", key=f"add_search_{i}", use_container_width=True):
                    p = df_row_to_point(r, alt=alt)
                    p.uid = next_uid()
                    st.session_state.wps.append(p.to_dict())
                    recalc_route()
                    st.rerun()

        st.markdown("#### Fix VOR / arco DME")
        st.caption("Pontos manuais por coordenada entram pelo mapa. Aqui ficam só fixes IFR úteis: radial/distância e arcos DME.")
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
                    st.error("Formato inválido ou VOR desconhecido. Usa, por exemplo, CAS/R180/D12.")
                else:
                    p.alt = float(radial_alt)
                    p.uid = next_uid()
                    st.session_state.wps.append(p.to_dict())
                    recalc_route()
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
            pts, msg = make_dme_arc_points(str(arc_vor), float(arc_radius), float(arc_start), float(arc_end), str(arc_dir), 2.0, float(arc_alt), "ARC")
            if not pts:
                st.error(msg)
            else:
                st.session_state.wps.extend([p.to_dict() for p in pts])
                recalc_route()
                st.success(msg)
                st.rerun()

        st.markdown("#### Procedimentos LPSO")
        st.caption("SID/STAR/approach para treino. Os turns por altitude são calculados pela performance configurada: TAS subida, ROC e vento.")
        proc_c1, proc_c2, proc_c3 = st.columns([0.9, 1.35, 0.9])
        with proc_c1:
            proc_kind = st.selectbox("Tipo", ["SID", "STAR", "APPROACH"], key="lpso_proc_kind")
        proc_list = LPSO_SIDS if proc_kind == "SID" else (LPSO_STARS if proc_kind == "STAR" else LPSO_APPROACHES)
        with proc_c2:
            proc_name = st.selectbox("Procedimento", proc_list, key="lpso_proc_name")
        with proc_c3:
            insert_mode = st.selectbox("Inserção", ["Acrescentar", "Substituir rota"], key="lpso_proc_insert_mode")
        proc_c4, proc_c5 = st.columns(2)
        with proc_c4:
            include_departure = st.checkbox("SID inclui LPSO como primeiro ponto", value=True, key="lpso_include_departure", disabled=(proc_kind != "SID"))
        with proc_c5:
            include_missed = st.checkbox("Approach inclui missed approach", value=False, key="lpso_include_missed", disabled=(proc_kind != "APPROACH"))
        if st.button("Adicionar procedimento LPSO", use_container_width=True, type="primary"):
            try:
                pts = lspo_procedure_points(proc_kind, proc_name, include_departure=bool(include_departure), include_missed=bool(include_missed))
                if not pts:
                    st.warning("Não foi possível gerar este procedimento.")
                else:
                    if insert_mode == "Substituir rota":
                        st.session_state.wps = pts
                    else:
                        st.session_state.wps = append_unique_points(st.session_state.wps, pts)
                    recalc_route()
                    st.success(f"{proc_kind} {proc_name} adicionado ({len(pts)} pontos).")
                    st.rerun()
            except Exception as e:
                st.error(f"Erro ao gerar procedimento: {e}")

    with right:
        st.markdown("#### Waypoints da rota")
        ensure_point_ids()
        if not st.session_state.wps:
            st.info("Ainda não há waypoints. Usa a caixa de texto, pesquisa ou clica no mapa.")
        remove_idx: Optional[int] = None
        move: Optional[Tuple[int, int]] = None
        for idx, w in enumerate(st.session_state.wps):
            with st.expander(f"{idx+1:02d} · {w.get('navlog_note') or w.get('code') or w.get('name')} · {w.get('src','')}", expanded=False):
                c1, c2 = st.columns([2, 1])
                with c1:
                    w["code"] = st.text_input("Código", w.get("code") or w.get("name") or "WP", key=f"wp_code_{w['uid']}").upper()
                    w["name"] = st.text_input("Nome", w.get("name") or w.get("code") or "WP", key=f"wp_name_{w['uid']}")
                    if w.get("navlog_note"):
                        w["navlog_note"] = st.text_input("Texto no NAVLOG", w.get("navlog_note", ""), key=f"wp_note_{w['uid']}")
                with c2:
                    w["alt"] = st.number_input("Alt ft", 0.0, 45000.0, float(w.get("alt", 0.0)), step=50.0, key=f"wp_alt_{w['uid']}")
                    w["stop_min"] = st.number_input("STOP min", 0.0, 480.0, float(w.get("stop_min", 0.0)), step=1.0, key=f"wp_stop_{w['uid']}")
                c1, c2 = st.columns(2)
                with c1:
                    w["lat"] = st.number_input("Lat", -90.0, 90.0, float(w.get("lat")), step=0.0001, format="%.6f", key=f"wp_lat_{w['uid']}")
                with c2:
                    w["lon"] = st.number_input("Lon", -180.0, 180.0, float(w.get("lon")), step=0.0001, format="%.6f", key=f"wp_lon_{w['uid']}")
                c1, c2, c3 = st.columns(3)
                with c1:
                    w["vor_pref"] = st.selectbox("VOR ref", ["AUTO", "FIXED", "NONE"], index=["AUTO", "FIXED", "NONE"].index(w.get("vor_pref", "AUTO")) if w.get("vor_pref", "AUTO") in ["AUTO", "FIXED", "NONE"] else 0, key=f"wp_vorpref_{w['uid']}")
                with c2:
                    w["vor_ident"] = st.text_input("VOR ident", w.get("vor_ident", ""), key=f"wp_vorid_{w['uid']}").upper()
                with c3:
                    st.caption(format_radial_dist(choose_vor_for_point(w), float(w["lat"]), float(w["lon"])))
                b1, b2, b3 = st.columns(3)
                with b1:
                    if st.button("↑", key=f"up_{w['uid']}", use_container_width=True) and idx > 0:
                        move = (idx, idx - 1)
                with b2:
                    if st.button("↓", key=f"down_{w['uid']}", use_container_width=True) and idx < len(st.session_state.wps) - 1:
                        move = (idx, idx + 1)
                with b3:
                    if st.button("Remover", key=f"rm_{w['uid']}", use_container_width=True):
                        remove_idx = idx
        if move:
            a, b = move
            st.session_state.wps[a], st.session_state.wps[b] = st.session_state.wps[b], st.session_state.wps[a]
            recalc_route()
            st.rerun()
        if remove_idx is not None:
            st.session_state.wps.pop(remove_idx)
            recalc_route()
            st.rerun()
        if st.session_state.wps:
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Aplicar alterações e recalcular", type="primary", use_container_width=True):
                    recalc_route()
                    st.rerun()
            with c2:
                fpl = route_item15(st.session_state.wps)
                st.code(fpl or "—")

    st.markdown("---")
    st.markdown("#### Rotas padrão")
    st.caption("Guardar/carregar aqui evita voltar à aba de dados durante o planeamento.")
    if not st.session_state.saved_routes:
        st.session_state.saved_routes = load_routes_from_gist()
    routes = st.session_state.saved_routes
    rg1, rg2 = st.columns(2)
    with rg1:
        route_name = st.text_input("Guardar rota atual como", "", key="route_save_name")
        if st.button("Guardar rota padrão", use_container_width=True, key="route_save_btn"):
            if not route_name.strip():
                st.warning("Dá um nome à rota.")
            elif not st.session_state.wps:
                st.warning("Não há rota para guardar.")
            else:
                routes[route_name.strip()] = serialize_route()
                ok, msg = save_routes_to_gist(routes)
                st.session_state.saved_routes = routes
                st.success(msg) if ok else st.warning(msg)
    with rg2:
        names = sorted(routes.keys())
        choice = st.selectbox("Carregar rota padrão", [""] + names, key="route_load_choice")
        b_load, b_delete = st.columns(2)
        with b_load:
            if choice and st.button("Carregar", use_container_width=True, key="route_load_btn"):
                st.session_state.wps = []
                for item in routes.get(choice, []):
                    p = Point.from_dict(item)
                    p.uid = next_uid()
                    st.session_state.wps.append(p.to_dict())
                recalc_route()
                st.rerun()
        with b_delete:
            if choice and st.button("Apagar", use_container_width=True, key="route_delete_btn"):
                routes.pop(choice, None)
                ok, msg = save_routes_to_gist(routes)
                st.session_state.saved_routes = routes
                st.success(msg) if ok else st.warning(msg)

# ---------------------------------------------------------------
# MAP TAB
# ---------------------------------------------------------------
with tab_map:
    st.markdown("#### Mapa e pontos por clique")
    top = st.columns([0.85, 1.15, 0.85, 0.85, 1.3])
    with top[0]:
        st.toggle("Pontos ref.", key="show_ref_points")
    with top[1]:
        st.multiselect("Camadas", ["IFR", "VOR", "AD", "VFR"], key="ref_layers")
    with top[2]:
        st.toggle("Airways", key="show_airways")
    with top[3]:
        st.toggle("openAIP", key="show_openaip")
    with top[4]:
        token_status = "OK" if get_openaip_token() else "sem OPENAIP_API_KEY nos secrets"
        st.caption(f"openAIP: {token_status}")
    st.slider("Opacidade openAIP", 0.0, 1.0, key="openaip_opacity", step=0.05)
    st.caption("Mapa centrado em LPSO. Por defeito mostra pontos IFR, VOR, AD, VFR e todas as airways carregadas no CSV.")

    out_map = render_route_map(st.session_state.wps, st.session_state.route_nodes, st.session_state.legs, key="map_tab")
    clicked = out_map.get("last_clicked") if out_map else None
    if clicked:
        with st.form("add_click_form"):
            st.markdown("##### Adicionar último clique")
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                nm = st.text_input("Nome", "WP CLICK")
            with c2:
                alt = st.number_input("Alt", 0.0, 45000.0, float(st.session_state.default_alt), step=100.0)
            with c3:
                st.caption(f"{clicked['lat']:.5f}, {clicked['lng']:.5f}")
            if st.form_submit_button("Adicionar clique"):
                p = Point(code=clean_code(nm) or "CLICK", name=nm, lat=float(clicked["lat"]), lon=float(clicked["lng"]), alt=float(alt), src="USER", uid=next_uid())
                st.session_state.wps.append(p.to_dict())
                recalc_route()
                st.rerun()

# ---------------------------------------------------------------
# NAVLOG/PDF TAB
# ---------------------------------------------------------------
with tab_navlog:
    st.markdown("#### 3 · Rever navlog e gerar PDF")
    if not st.session_state.legs:
        st.info("Cria uma rota e carrega em Recalcular/Gerar para ver o navlog.")
    else:
        df_legs = legs_to_dataframe(st.session_state.legs)
        st.dataframe(df_legs, use_container_width=True, hide_index=True)
        csv = df_legs.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Navlog CSV", csv, file_name="navlog.csv", mime="text/csv")

        st.markdown("#### Cabeçalho para PDF")
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

        st.markdown("#### PDFs")
        cpdf1, cpdf2 = st.columns(2)
        with cpdf1:
            if st.button("Gerar PDF NAVLOG", type="primary", use_container_width=True):
                if not TEMPLATE_MAIN.exists():
                    st.error("NAVLOG_FORM.pdf não encontrado na raiz do repo.")
                else:
                    payload = build_pdf_payload(st.session_state.legs, header, start=0, count=22)
                    out = fill_pdf(TEMPLATE_MAIN, OUTPUT_MAIN, payload)
                    with open(out, "rb") as f:
                        st.download_button("⬇️ NAVLOG principal", f.read(), file_name="NAVLOG_FILLED.pdf", mime="application/pdf", use_container_width=True)

                    if len(st.session_state.legs) > 22 and TEMPLATE_CONT.exists():
                        payload2 = build_pdf_payload(st.session_state.legs, header, start=22, count=11)
                        out2 = fill_pdf(TEMPLATE_CONT, OUTPUT_CONT, payload2)
                        with open(out2, "rb") as f:
                            st.download_button("⬇️ NAVLOG continuação", f.read(), file_name="NAVLOG_FILLED_1.pdf", mime="application/pdf", use_container_width=True)
        with cpdf2:
            if st.button("Gerar briefing PDF", use_container_width=True):
                rows = df_legs.to_dict("records")
                p = generate_briefing_pdf(OUTPUT_BRIEFING, rows)
                if p and p.exists():
                    with open(p, "rb") as f:
                        st.download_button("⬇️ Briefing legs", f.read(), file_name="NAVLOG_LEGS_BRIEFING.pdf", mime="application/pdf", use_container_width=True)
                else:
                    st.error("Instala reportlab para gerar o briefing PDF.")


# Footer warning
st.markdown(
    """
<hr>
<div class='small-muted'>
Ferramenta de planeamento. Confirma sempre cartas, NOTAM, AIP/AIRAC, meteorologia, mínimos IFR/VFR, autorizações ATC e performance real da aeronave antes do voo.
</div>
""",
    unsafe_allow_html=True,
)

