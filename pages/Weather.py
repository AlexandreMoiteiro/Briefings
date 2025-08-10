import streamlit as st
import requests
import datetime
from typing import List, Dict, Any

# ============= Page setup (no sidebar) =============
st.set_page_config(page_title="Live Weather", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
/* Hide sidebar + hamburger */
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stHamburger"] { display: none !important; }
.block-container { padding-top: 1.2rem; }
.title { font-size: 1.8rem; font-weight: 800; margin-bottom: .25rem;}
.muted { color: #6b7280; margin-bottom: 1rem;}
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
.card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); background: #fff; }
.card h3 { margin: 0 0 6px; }
.monos { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size: .92rem; white-space: pre-wrap; }
.section { margin-top: 16px; }
</style>
""", unsafe_allow_html=True)

# ============= Config =============
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]
CHECKWX_API_KEY = st.secrets.get("CHECKWX_API_KEY", "")

def _cw_headers() -> Dict[str,str]:
    return {"X-API-Key": CHECKWX_API_KEY} if CHECKWX_API_KEY else {}

# ============= Data fetchers =============
@st.cache_data(ttl=90)
def fetch_metar(icao: str) -> str:
    if not CHECKWX_API_KEY: return ""
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=_cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=90)
def fetch_taf(icao: str) -> str:
    if not CHECKWX_API_KEY: return ""
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=_cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=120)
def fetch_sigmet_lppc() -> List[str]:
    """
    International SIGMETs (worldwide) via AWC Data API.
    We request JSON and filter for LPPC in either FIR field or raw text.
    """
    url = "https://aviationweather.gov/api/data/isigmet"
    params = {"format": "json"}  # JSON response (worldwide)
    out: List[str] = []
    try:
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        payload = r.json()
        # API may return a list (JSON) or GeoJSON-like features under "features"
        items = payload if isinstance(payload, list) else payload.get("features", []) or []
        for item in items:
            props: Dict[str, Any] = {}
            if isinstance(item, dict) and "properties" in item:
                props = item["properties"]
            elif isinstance(item, dict):
                props = item
            raw = (props.get("raw") or props.get("raw_text") or props.get("sigmet_text") or props.get("report") or "").strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            if not raw:
                # Sometimes text is under different key names
                raw = (item.get("raw") if isinstance(item, dict) else "") or ""
                raw = str(raw).strip()
            # Filter LPPC either by field or by appearance in text
            if raw and (fir == "LPPC" or " LPPC " in f" {raw} " or "FIR LPPC" in raw or " LPPC FIR" in raw):
                out.append(raw)
    except Exception:
        return []
    return out

@st.cache_data(ttl=120)
def fetch_airmet_for(icao: str) -> List[str]:
    """
    AIRMET via CheckWX (coverage is often limited outside CONUS; may return empty).
    We aggregate any raw text we find per ICAO.
    """
    results: List[str] = []
    if not CHECKWX_API_KEY: return results
    try:
        r = requests.get(f"https://api.checkwx.com/airmet/{icao}", headers=_cw_headers(), timeout=10)
        if r.status_code != 200: return results
        data = r.json().get("data", [])
        for it in data:
            # CheckWX AIRMET responses can be objects with various fields; try raw/raw_text
            txt = (it.get("raw") if isinstance(it, dict) else "") or (it.get("raw_text") if isinstance(it, dict) else "") or ""
            if not txt and isinstance(it, dict) and "hazard" in it:
                # Build a minimal line from decoded fields
                hz = it.get("hazard", {})
                cat = it.get("category","")
                tfrom = it.get("timestamp",{}).get("from","")
                tto = it.get("timestamp",{}).get("to","")
                txt = f"{icao} {cat} {hz} {tfrom}→{tto}".strip()
            if txt:
                results.append(txt)
    except Exception:
        return results
    return results

# ============= UI =============
st.markdown('<div class="title">Live Weather</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Latest METAR, TAF and LPPC SIGMET</div>', unsafe_allow_html=True)

# ICAO input
icao_str = st.text_input("ICAO (comma-separated)", value=",".join(DEFAULT_ICAOS))
ICAOS = [c.strip().upper() for c in icao_str.split(",") if c.strip()]

# METAR/TAF grid
st.markdown('<div class="grid">', unsafe_allow_html=True)
for icao in ICAOS:
    metar = fetch_metar(icao)
    taf = fetch_taf(icao)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f"<h3>{icao}</h3>", unsafe_allow_html=True)
    st.caption("METAR")
    st.markdown(f'<div class="monos">{metar or "—"}</div>', unsafe_allow_html=True)
    st.caption("TAF")
    st.markdown(f'<div class="monos">{taf or "—"}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

st.divider()

# SIGMET (LPPC only)
st.subheader("SIGMET (LPPC)")
sigmets = fetch_sigmet_lppc()
if not sigmets:
    st.info("No active LPPC SIGMETs.")
else:
    for s in sigmets:
        st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
        st.markdown("---")

# AIRMET (best effort via CheckWX, may be empty in EUR)
st.subheader("AIRMET (per ICAO, via CheckWX)")
if not CHECKWX_API_KEY:
    st.caption("Set CHECKWX_API_KEY in secrets to enable AIRMET.")
else:
    any_airmet = False
    for icao in ICAOS:
        items = fetch_airmet_for(icao)
        if items:
            any_airmet = True
            st.markdown(f"**{icao}**")
            for t in items:
                st.markdown(f'<div class="monos">{t}</div>', unsafe_allow_html=True)
            st.markdown("---")
    if not any_airmet:
        st.info("No AIRMETs returned for these ICAOs at this time.")

# GAMET: no universal public API → handled in main app via user paste
st.subheader("GAMET")
st.caption("GAMET is not available via a universal public API; include it in the main app when generating PDFs (paste raw text).")

# Timestamp
st.caption(f"Last updated: {datetime.datetime.utcnow():%Y-%m-%d %H:%M:%SZ} UTC")




