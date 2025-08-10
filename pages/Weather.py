# pages/Weather.py
# Clean weather dashboard (no sidebar, METAR+TAF together)
# - METAR/TAF via CheckWX (needs CHECKWX_API_KEY in secrets)
# - SIGMET LPPC via AWC (public)
# - GAMET via optional secrets.GAMET_URL
# - Defaults ICAOs: LPPT, LPBJ, LEBZ | supports ?icao=AAA or ?icao=AAA,BBB

from typing import List, Dict, Any, Optional
import datetime as dt
from zoneinfo import ZoneInfo
import streamlit as st
import requests

# ---- Basic page setup (no sidebar) ----
st.set_page_config(page_title="Weather (Live)", layout="wide")

# Hide sidebar and its toggle completely
st.markdown("""
<style>
/* Hide the sidebar and its nav */
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display: none !important; }
/* Hide the sidebar toggle button in header */
button[title="View fullscreen"], button[title="Open the command palette"], 
button[title="Menu"], [data-testid="stSidebarCollapseButton"] { display: none !important; }
/* Optional: hide the top-left hamburger if present in your theme */
header [data-testid="baseButton-headerNoPadding"] { display: none !important; }

/* Minimal, clean styling */
:root { --line:#e5e7eb; --muted:#6b7280; --vfr:#16a34a; --mvfr:#f59e0b; --ifr:#ef4444; --lifr:#7c3aed; }
.page-title { font-size: 2rem; font-weight: 800; margin: 0 0 .25rem; }
.subtle { color: var(--muted); margin-bottom: .75rem; }
.row { padding: 8px 0 14px; border-bottom: 1px solid var(--line); }
.row h3 { margin: 0 0 6px; font-size: 1.1rem; }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; font-weight:700; font-size:.80rem; color:#fff; margin-left:8px; vertical-align:middle; }
.vfr { background: var(--vfr); } .mvfr { background: var(--mvfr); } .ifr { background: var(--ifr); } .lifr { background: var(--lifr); }
.muted { color: var(--muted); }
.monos { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size: .95rem; white-space: pre-wrap; }
</style>
""", unsafe_allow_html=True)

DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

# ---- Robust time parsing ----
def parse_iso_utc(s: str) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.datetime.fromisoformat(s)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
            try: return dt.datetime.strptime(s, fmt)
            except Exception: pass
    return None

def zulu_plus_portugal(d: Optional[dt.datetime]) -> str:
    if d is None:
        return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    d_utc = d.astimezone(dt.timezone.utc)
    d_pt = d_utc.astimezone(ZoneInfo("Europe/Lisbon"))
    return f"{d_utc.strftime('%Y-%m-%d %H:%MZ')} ({d_pt.strftime('%H:%M')} Portugal)"

# ---- Fetchers ----
@st.cache_data(ttl=75)
def fetch_metar_decoded(icao: str) -> Optional[Dict[str,Any]]:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else None
    except Exception:
        return None

@st.cache_data(ttl=75)
def fetch_metar_raw(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text","") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=75)
def fetch_taf_decoded(icao: str) -> Optional[Dict[str,Any]]:
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}/decoded", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else None
    except Exception:
        return None

@st.cache_data(ttl=75)
def fetch_taf_raw(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text","") or ""
        return str(data[0])
    except Exception:
        return ""

def awc_params() -> Dict[str,str]:
    return {"loc":"eur", "format":"json"}  # public

@st.cache_data(ttl=120)
def fetch_sigmet_lppc() -> List[str]:
    """Handle both list and GeoJSON shapes; filter LPPC."""
    try:
        r = requests.get("https://aviationweather.gov/api/data/isigmet", params=awc_params(), timeout=12)
        r.raise_for_status()
        js = r.json()
        items = js if isinstance(js, list) else js.get("features", []) or []
        out: List[str] = []
        for it in items:
            props = it.get("properties", {}) if isinstance(it, dict) else {}
            if not props and isinstance(it, dict):
                props = it
            raw = (props.get("raw") or props.get("raw_text") or props.get("sigmet_text") or "").strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            text = raw or ""
            if not text: 
                continue
            if fir == "LPPC" or " LPPC " in f" {text} " or "FIR LPPC" in text or " LPPC FIR" in text:
                out.append(text)
        return out
    except Exception:
        return []

@st.cache_data(ttl=90)
def fetch_gamet_live() -> str:
    url = (st.secrets.get("GAMET_URL","") or "").strip()
    if not url: return ""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        if "application/json" in (r.headers.get("Content-Type","").lower()):
            j = r.json()
            for k in ("text","gamet","raw","message","body","data"):
                v = j.get(k)
                if isinstance(v, str): return v.strip()
                if isinstance(v, list): return "\n".join(map(str, v)).strip()
            return str(j)
        return r.text.strip()
    except Exception:
        return ""

# ---- Utils ----
def flight_cat_badge(decoded: Optional[Dict[str,Any]]) -> str:
    if not decoded: return ""
    cat = (decoded.get("flight_category") or "").upper()
    klass = {"VFR":"vfr","MVFR":"mvfr","IFR":"ifr","LIFR":"lifr"}.get(cat,"")
    return f'<span class="badge {klass}">{cat}</span>' if klass else ""

def parse_query_icaos(defaults: List[str]) -> List[str]:
    q = st.query_params
    raw = q.get("icao","")
    if isinstance(raw, list): raw = ",".join(raw)
    lst: List[str] = []
    if raw:
        for p in raw.split(","):
            p = p.strip().upper()
            if len(p)==4: lst.append(p)
    return lst or defaults

# ---- UI ----
st.markdown('<div class="page-title">Weather (Live)</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Latest METAR, TAF, LPPC SIGMET, and GAMET</div>', unsafe_allow_html=True)

icaos = parse_query_icaos(DEFAULT_ICAOS)

# Minimal input + refresh inline (no sidebar)
col1, col2 = st.columns([0.7, 0.3])
with col1:
    icaos_input = st.text_input("ICAO list (comma-separated)", value=",".join(icaos))
with col2:
    if st.button("Refresh"):
        st.cache_data.clear()
icaos = [x.strip().upper() for x in icaos_input.split(",") if x.strip()]

# ---- METAR + TAF per aerodrome ----
for icao in icaos:
    metar_dec = fetch_metar_decoded(icao)
    metar_raw = fetch_metar_raw(icao)
    taf_dec = fetch_taf_decoded(icao)
    taf_raw = fetch_taf_raw(icao)

    badge = flight_cat_badge(metar_dec)

    metar_obs_dt = parse_iso_utc(metar_dec.get("observed")) if metar_dec else None
    metar_obs = zulu_plus_portugal(metar_obs_dt)

    taf_issued_dt = None
    if taf_dec:
        issued = (taf_dec.get("timestamp", {}) or {}).get("issued") or taf_dec.get("issued") or ""
        taf_issued_dt = parse_iso_utc(issued)
    taf_issued = zulu_plus_portugal(taf_issued_dt)

    st.markdown('<div class="row">', unsafe_allow_html=True)
    hdr = f"<h3>{icao} {badge}</h3>"
    sub = f'<span class="muted">Observed: {metar_obs}</span>' if metar_obs else ""
    st.markdown(hdr + sub, unsafe_allow_html=True)

    st.markdown(
        f'<div class="monos"><strong>METAR</strong> {metar_raw or "—"}'
        + (f"\n\n<strong>TAF</strong> {taf_raw or '—'}" if True else "")
        + (f"\n\n<strong>TAF Issued</strong> {taf_issued}" if taf_issued else ""),
        unsafe_allow_html=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

# ---- SIGMET (LPPC) ----
st.subheader("SIGMET (LPPC)")
sigs = fetch_sigmet_lppc()
if not sigs:
    st.write("—")
else:
    for s in sigs:
        st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
        st.markdown("---")

# ---- GAMET (Live) ----
st.subheader("GAMET (Live)")
gamet = fetch_gamet_live()
if gamet:
    st.markdown(f'<div class="monos">{gamet}</div>', unsafe_allow_html=True)
    st.download_button("Download GAMET as .txt", data=gamet, file_name="gamet.txt")
else:
    st.write("No GAMET available. Add a 'GAMET_URL' to secrets to enable live GAMET here.")




