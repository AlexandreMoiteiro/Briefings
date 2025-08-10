import streamlit as st
import requests
import datetime
from typing import List, Dict, Any

# ============= Page setup (no sidebar) =============
st.set_page_config(page_title="Live Weather", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
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

DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]
CHECKWX_API_KEY = st.secrets.get("CHECKWX_API_KEY", "")

def _cw_headers() -> Dict[str,str]:
    return {"X-API-Key": CHECKWX_API_KEY} if CHECKWX_API_KEY else {}

# ---------------- METAR/TAF (CheckWX) ----------------
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

# ---------------- SIGMET LPPC (IPMA -> fallback AWC) ----------------
def _ipma_auth_headers(bearer_key: str, cookie_key: str) -> Dict[str,str]:
    h: Dict[str,str] = {}
    bearer = st.secrets.get(bearer_key, "")
    cookie = st.secrets.get(cookie_key, "")
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    elif cookie:
        h["Cookie"] = cookie
    return h

@st.cache_data(ttl=90)
def fetch_sigmet_lppc_ipma() -> List[str]:
    url = st.secrets.get("IPMA_SIGMET_URL", "")
    if not url: return []
    try:
        r = requests.get(url, headers=_ipma_auth_headers("IPMA_SIGMET_BEARER","IPMA_SIGMET_COOKIE"), timeout=12)
        r.raise_for_status()
        out: List[str] = []
        # JSON?
        try:
            js = r.json()
            items = js if isinstance(js, list) else js.get("features", []) or js.get("data", []) or []
            if isinstance(items, list) and items:
                for it in items:
                    props: Dict[str, Any] = {}
                    if isinstance(it, dict) and "properties" in it: props = it["properties"]
                    elif isinstance(it, dict): props = it
                    raw = (props.get("raw") or props.get("raw_text") or props.get("report") or props.get("sigmet_text") or "").strip()
                    if raw: out.append(raw)
            else:
                raw = str(js.get("raw") or js.get("text") or js.get("message") or js.get("sigmet") or "").strip()
                if raw: out.append(raw)
        except ValueError:
            body = (r.text or "").strip()
            if body: out = [body]
        return out
    except Exception:
        return []

@st.cache_data(ttl=90)
def fetch_sigmet_lppc_awc() -> List[str]:
    url = "https://aviationweather.gov/api/data/isigmet"
    params = {"format": "json"}
    out: List[str] = []
    try:
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        payload = r.json()
        items = payload if isinstance(payload, list) else payload.get("features", []) or []
        for item in items:
            props: Dict[str, Any] = {}
            if isinstance(item, dict) and "properties" in item: props = item["properties"]
            elif isinstance(item, dict): props = item
            raw = (props.get("raw") or props.get("raw_text") or props.get("sigmet_text") or props.get("report") or "").strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            if not raw and isinstance(item, dict):
                raw = str(item.get("raw","")).strip()
            if raw and (fir == "LPPC" or " LPPC " in f" {raw} " or "FIR LPPC" in raw or " LPPC FIR" in raw):
                out.append(raw)
    except Exception:
        return []
    return out

def fetch_sigmet_lppc() -> List[str]:
    ipma = fetch_sigmet_lppc_ipma()
    if ipma: return ipma
    return fetch_sigmet_lppc_awc()

# ---------------- GAMET LPPC (IPMA on the same showSIGMET area) ----------------
@st.cache_data(ttl=120)
def fetch_gamet_lppc_ipma() -> List[str]:
    """
    Use a second IPMA endpoint for GAMET (captured from showSIGMET page as well).
    Configure in secrets:
      IPMA_GAMET_URL
      + either IPMA_GAMET_BEARER or IPMA_GAMET_COOKIE
    """
    url = st.secrets.get("IPMA_GAMET_URL", "")
    if not url: return []
    try:
        r = requests.get(url, headers=_ipma_auth_headers("IPMA_GAMET_BEARER","IPMA_GAMET_COOKIE"), timeout=12)
        r.raise_for_status()
        out: List[str] = []
        try:
            js = r.json()
            items = js if isinstance(js, list) else js.get("features", []) or js.get("data", []) or []
            if isinstance(items, list) and items:
                for it in items:
                    props: Dict[str, Any] = {}
                    if isinstance(it, dict) and "properties" in it: props = it["properties"]
                    elif isinstance(it, dict): props = it
                    raw = (props.get("raw") or props.get("raw_text") or props.get("report") or props.get("gamet_text") or props.get("text") or "").strip()
                    if raw: out.append(raw)
            else:
                raw = str(js.get("raw") or js.get("text") or js.get("message") or js.get("gamet") or "").strip()
                if raw: out.append(raw)
        except ValueError:
            body = (r.text or "").strip()
            if body: out = [body]
        return out
    except Exception:
        return []

# ============= UI =============
st.markdown('<div class="title">Live Weather</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Latest METAR, TAF • LPPC SIGMET • LPPC GAMET</div>', unsafe_allow_html=True)

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

# SIGMET (LPPC)
st.subheader("SIGMET (LPPC)")
sigs = fetch_sigmet_lppc()
if not sigs:
    st.info("No active LPPC SIGMETs.")
else:
    for s in sigs:
        st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
        st.markdown("---")

# GAMET (LPPC)
st.subheader("GAMET (LPPC)")
gamets = fetch_gamet_lppc_ipma()
if not gamets:
    st.info("No LPPC GAMET available (configure IPMA_GAMET_URL and IPMA_GAMET_BEARER or IPMA_GAMET_COOKIE in secrets).")
else:
    for g in gamets:
        st.markdown(f'<div class="monos">{g}</div>', unsafe_allow_html=True)
        st.markdown("---")

st.caption(f"Last updated: {datetime.datetime.utcnow():%Y-%m-%d %H:%M:%SZ} UTC")





