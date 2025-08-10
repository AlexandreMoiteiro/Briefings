# pages/Weather.py
# Clean weather dashboard (no cards, METAR+TAF together)
# - METAR/TAF via CheckWX (needs CHECKWX_API_KEY)
# - SIGMET LPPC via AWC (public)
# - GAMET via optional secrets.GAMET_URL
# Default ICAOs: LPPT, LPBJ, LEBZ | supports ?icao=AAA or ?icao=AAA,BBB

from typing import List, Dict, Any, Optional
import datetime as dt
from zoneinfo import ZoneInfo  # stdlib: handles DST for Europe/Lisbon
import streamlit as st
import requests

st.set_page_config(
    page_title="Weather (Live)",
    layout="wide",
    initial_sidebar_state="collapsed",  # <- sidebar hidden by default
)

# ---------------- Styles ----------------
st.markdown("""
<style>
  :root {
    --line:#e5e7eb; --muted:#6b7280;
    --vfr:#16a34a; --mvfr:#f59e0b; --ifr:#ef4444; --lifr:#7c3aed;
  }
  .page-title { font-size: 2rem; font-weight: 800; margin: 0 0 .25rem; }
  .subtle { color: var(--muted); margin-bottom: .75rem; }
  .row { padding: 8px 0 14px; border-bottom: 1px solid var(--line); }
  .row h3 { margin: 0 0 6px; font-size: 1.1rem; }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-weight:700; font-size:.80rem; color:#fff; margin-left:8px; vertical-align:middle; }
  .vfr { background: var(--vfr); }
  .mvfr { background: var(--mvfr); }
  .ifr { background: var(--ifr); }
  .lifr { background: var(--lifr); }
  .muted { color: var(--muted); }
  .monos { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size: .95rem; white-space: pre-wrap; }
</style>
""", unsafe_allow_html=True)

DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

# ------------- Fetchers -------------
@st.cache_data(ttl=75)
def fetch_metar_raw(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        return data[0].get("raw") or data[0].get("raw_text","") if isinstance(data[0], dict) else str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=75)
def fetch_taf_raw(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        return data[0].get("raw") or data[0].get("raw_text","") if isinstance(data[0], dict) else str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=75)
def fetch_metar_decoded(icao: str) -> Optional[Dict[str,Any]]:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else None
    except Exception:
        return None

def awc_params() -> Dict[str,str]:
    return {"loc":"eur", "format":"json"}

@st.cache_data(ttl=120)
def fetch_sigmet_lppc() -> List[str]:
    try:
        r = requests.get("https://aviationweather.gov/api/data/isigmet", params=awc_params(), timeout=12)
        r.raise_for_status()
        js = r.json()
        items = js if isinstance(js, list) else js.get("features", []) or []
        out: List[str] = []
        for it in items:
            props = it.get("properties", {}) if isinstance(it, dict) else {}
            if not props: props = it if isinstance(it, dict) else {}
            raw = (props.get("raw") or props.get("sigmet_text") or str(it) or "").strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            if not raw: continue
            if fir == "LPPC" or " LPPC " in f" {raw} " or "FIR LPPC" in raw or " LPPC FIR" in raw:
                out.append(raw)
        return out
    except Exception:
        return []

@st.cache_data(ttl=90)
def fetch_gamet_live() -> str:
    url = (st.secrets.get("GAMET_URL","") or "").strip()
    if not url:
        return ""
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

# ------------- Utils -------------
def flight_cat_badge(decoded: Optional[Dict[str,Any]]) -> str:
    if not decoded: return ""
    cat = (decoded.get("flight_category") or "").upper()
    klass = {"VFR":"vfr","MVFR":"mvfr","IFR":"ifr","LIFR":"lifr"}.get(cat,"")
    return f'<span class="badge {klass}">{cat}</span>' if klass else ""

def fmt_observed(decoded: Optional[Dict[str,Any]]) -> str:
    """
    Returns 'YYYY-MM-DD HH:MMZ (HH:MM Portugal)' if observed is present.
    Handles DST using Europe/Lisbon.
    """
    if not decoded: return ""
    obs = decoded.get("observed")
    if not obs: return ""
    try:
        # to aware UTC
        t_utc = dt.datetime.fromisoformat(obs.replace("Z","+00:00"))
        if t_utc.tzinfo is None:
            t_utc = t_utc.replace(tzinfo=dt.timezone.utc)
        # convert to Portugal local time
        t_pt = t_utc.astimezone(ZoneInfo("Europe/Lisbon"))
        zulu_str = t_utc.strftime("%Y-%m-%d %H:%MZ")
        pt_str = t_pt.strftime("%H:%M")
        return f"{zulu_str} ({pt_str} Portugal)"
    except Exception:
        return str(obs)

def parse_query_icaos() -> List[str]:
    q = st.query_params
    raw = q.get("icao","")
    if isinstance(raw, list): raw = ",".join(raw)
    lst: List[str] = []
    if raw:
        for p in raw.split(","):
            p = p.strip().upper()
            if len(p)==4: lst.append(p)
    return lst or DEFAULT_ICAOS

# ---------------- Header ----------------
st.markdown('<div class="page-title">Weather (Live)</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Latest METAR, TAF, LPPC SIGMET, and GAMET</div>', unsafe_allow_html=True)

icaos = parse_query_icaos()
col1, col2 = st.columns([0.7, 0.3])
with col1:
    icaos_input = st.text_input("ICAO list (comma-separated)", value=",".join(icaos))
with col2:
    if st.button("Refresh"):
        st.cache_data.clear()

icaos = [x.strip().upper() for x in icaos_input.split(",") if x.strip()]

# ---------------- METAR+TAF (per aerodrome, single block) ----------------
for icao in icaos:
    metar = fetch_metar_raw(icao)
    taf = fetch_taf_raw(icao)
    decoded = fetch_metar_decoded(icao)

    badge = flight_cat_badge(decoded)
    observed = fmt_observed(decoded)
    obs_html = f'<span class="muted">Observed: {observed}</span>' if observed else ""

    st.markdown('<div class="row">', unsafe_allow_html=True)
    st.markdown(f"<h3>{icao} {badge}</h3>{obs_html}", unsafe_allow_html=True)
    st.markdown(f'<div class="monos"><strong>METAR</strong> {metar or "—"}\n\n<strong>TAF</strong> {taf or "—"}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# ---------------- SIGMET (LPPC) ----------------
st.subheader("SIGMET (LPPC)")
sigs = fetch_sigmet_lppc()
if not sigs:
    st.write("—")
else:
    for s in sigs:
        st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
        st.markdown("---")

# ---------------- GAMET (Live) ----------------
st.subheader("GAMET (Live)")
gamet = fetch_gamet_live()
if gamet:
    st.markdown(f'<div class="monos">{gamet}</div>', unsafe_allow_html=True)
    st.download_button("Download GAMET as .txt", data=gamet, file_name="gamet.txt")
else:
    st.write("No GAMET available. Add a 'GAMET_URL' to secrets to enable live GAMET here.")





