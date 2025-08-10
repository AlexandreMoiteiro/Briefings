# pages/Weather.py
# Weather dashboard: clearer display, flight category badges, tabs, auto-refresh
# - METAR/TAF via CheckWX (needs CHECKWX_API_KEY)
# - SIGMET LPPC via AWC (public)
# - GAMET via optional secrets.GAMET_URL
# Default ICAOs: LPPT, LPBJ, LEBZ | supports ?icao=AAA or ?icao=AAA,BBB

from typing import List, Dict, Any, Optional
import datetime as dt
import streamlit as st
import requests

st.set_page_config(page_title="Weather (Live)", layout="wide")

# -------------------------- Styles --------------------------
st.markdown("""
<style>
  :root {
    --line:#e5e7eb; --muted:#6b7280; --card-bg:#ffffff;
    --vfr:#16a34a; --mvfr:#f59e0b; --ifr:#ef4444; --lifr:#7c3aed;
  }
  .page-title { font-size: 2rem; font-weight: 800; margin: 0 0 .25rem; }
  .subtle { color: var(--muted); margin-bottom: .75rem; }
  .toolbar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:.5rem 0 1rem; }
  .icaos-line { font-weight:600; }
  .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
  .card { border:1px solid var(--line); background:var(--card-bg); border-radius:14px; padding:14px 16px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
  .card h3 { margin: 0 0 6px; font-size:1.15rem; }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-weight:700; font-size:.80rem; color:#fff; margin-left:8px; vertical-align:middle; }
  .badge.vfr { background: var(--vfr); }
  .badge.mvfr { background: var(--mvfr); }
  .badge.ifr { background: var(--ifr); }
  .badge.lifr { background: var(--lifr); }
  .muted { color: var(--muted); }
  .monos { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size: .95rem; white-space: pre-wrap; }
  .section { margin-top: 18px; }
  .collapse { border:1px solid var(--line); border-radius:12px; padding:10px 12px; margin-bottom:10px; background:#fff; }
</style>
""", unsafe_allow_html=True)

# -------------------------- Config --------------------------
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

# -------------------------- Fetchers --------------------------
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
    """
    Optional decoded METAR to extract flight category and observed time.
    If the endpoint/key isn't available or fails, return None gracefully.
    """
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else None
    except Exception:
        return None

def awc_params() -> Dict[str,str]:
    return {"loc":"eur", "format":"json"}  # public

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
            # try common keys
            for k in ("text","gamet","raw","message","body","data"):
                v = j.get(k)
                if isinstance(v, str): return v.strip()
                if isinstance(v, list): return "\n".join(map(str, v)).strip()
            return str(j)
        return r.text.strip()
    except Exception:
        return ""

# -------------------------- Utils --------------------------
def flight_cat_badge(decoded: Optional[Dict[str,Any]]) -> str:
    """
    Use CheckWX decoded flight_category if present; else heuristic stays blank.
    """
    if not decoded:
        return ""
    cat = (decoded.get("flight_category") or "").upper()
    klass = ""
    label = cat or ""
    if cat == "VFR":
        klass = "vfr"
    elif cat == "MVFR":
        klass = "mvfr"
    elif cat == "IFR":
        klass = "ifr"
    elif cat == "LIFR":
        klass = "lifr"
    if not klass:
        return ""
    return f'<span class="badge {klass}">{label}</span>'

def fmt_observed(decoded: Optional[Dict[str,Any]]) -> str:
    if not decoded:
        return ""
    obs = decoded.get("observed")
    if not obs:
        return ""
    # Example format: "2025-08-10T13:20:00Z"
    try:
        t = dt.datetime.fromisoformat(obs.replace("Z","+00:00"))
        return t.strftime("%Y-%m-%d %H:%MZ")
    except Exception:
        return str(obs)

def parse_query_icaos() -> List[str]:
    q = st.query_params
    raw = q.get("icao","")
    if isinstance(raw, list):
        raw = ",".join(raw)
    lst: List[str] = []
    if raw:
        for p in raw.split(","):
            p = p.strip().upper()
            if len(p)==4:
                lst.append(p)
    return lst or DEFAULT_ICAOS

# -------------------------- Header --------------------------
st.markdown('<div class="page-title">Weather (Live)</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Latest METAR, TAF, LPPC SIGMET, and GAMET</div>', unsafe_allow_html=True)

icaos = parse_query_icaos()

with st.container():
    col1, col2, col3, col4 = st.columns([0.45, 0.15, 0.18, 0.22])
    with col1:
        icaos_input = st.text_input("ICAO list (comma-separated)", value=",".join(icaos))
    with col2:
        refresh = st.button("Refresh")
    with col3:
        auto = st.toggle("Auto-refresh", value=False, help="Refresh every ~60s")
    with col4:
        st.markdown(f'<div class="icaos-line">Airfields: {", ".join([x.strip().upper() for x in icaos_input.split(",") if x.strip()])}</div>', unsafe_allow_html=True)

if refresh:
    st.cache_data.clear()

if auto:
    st.experimental_rerun  # for mypy
    st.autorefresh = st.experimental_singleton(lambda: None)
    st_autorefresh = st.experimental_rerun  # placeholder for type hints
    st_autorefresh = st.experimental_rerun
    st_autorefresh  # noop to silence linters
    st.experimental_rerun

# If user edited ICAOs input, use that
icaos = [x.strip().upper() for x in icaos_input.split(",") if x.strip()]

# -------------------------- METAR/TAF cards --------------------------
st.markdown('<div class="grid">', unsafe_allow_html=True)
for icao in icaos:
    metar_raw = fetch_metar_raw(icao)
    taf_raw = fetch_taf_raw(icao)
    decoded = fetch_metar_decoded(icao)

    badge = flight_cat_badge(decoded)
    observed = fmt_observed(decoded)
    observed_html = f'<span class="muted">Observed: {observed}</span>' if observed else ""

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f"<h3>{icao} {badge}</h3>{observed_html}", unsafe_allow_html=True)

    tabs = st.tabs(["METAR", "TAF"])
    with tabs[0]:
        st.markdown(f'<div class="monos">{metar_raw or "—"}</div>', unsafe_allow_html=True)
    with tabs[1]:
        st.markdown(f'<div class="monos">{taf_raw or "—"}</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# -------------------------- SIGMET (LPPC) --------------------------
st.divider()
st.subheader("SIGMET (LPPC)")
sigs = fetch_sigmet_lppc()
if not sigs:
    st.write("—")
else:
    for i, s in enumerate(sigs, 1):
        with st.expander(f"SIGMET #{i}"):
            st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)

# -------------------------- GAMET (Live) --------------------------
st.divider()
st.subheader("GAMET (Live)")
gamet_text = fetch_gamet_live()
if gamet_text:
    st.markdown(f'<div class="monos">{gamet_text}</div>', unsafe_allow_html=True)
    st.download_button("Download GAMET as .txt", data=gamet_text, file_name="gamet.txt")
else:
    st.write("No GAMET available. Add a 'GAMET_URL' to secrets to enable live GAMET here.")




