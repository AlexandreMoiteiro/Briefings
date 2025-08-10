# pages/Weather.py
# Live METAR/TAF via CheckWX (precisa CHECKWX_API_KEY)
# SIGMET LPPC via AWC International SIGMET (público)
# GAMET (Live) via GAMET_URL (opcional) definido em secrets
# Defaults ICAOs: LPPT, LPBJ, LEBZ  |  ?icao=AAA ou ?icao=AAA,BBB

from typing import List, Dict, Any, Union
import streamlit as st
import requests

st.set_page_config(page_title="Weather (Live)", layout="wide")

st.markdown("""
<style>
  .title { font-size: 1.9rem; font-weight: 800; margin-bottom: .25rem;}
  .muted { color: #6b7280; margin-bottom: 1rem;}
  .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
  .card { border: 1px solid #e5e7eb; background:#fff; border-radius: 14px; padding: 14px 16px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
  .card h3 { margin: 0 0 6px; }
  .monos { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size: .95rem; white-space: pre-wrap; }
</style>
""", unsafe_allow_html=True)

# --------- helpers ---------
def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

@st.cache_data(ttl=60)
def fetch_metar(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        return data[0].get("raw") or data[0].get("raw_text","") if isinstance(data[0], dict) else str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=60)
def fetch_taf(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        return data[0].get("raw") or data[0].get("raw_text","") if isinstance(data[0], dict) else str(data[0])
    except Exception:
        return ""

def awc_params() -> Dict[str,str]:
    # public endpoint; no key needed
    return {"loc":"eur", "format":"json"}

@st.cache_data(ttl=90)
def fetch_sigmet_lppc() -> List[str]:
    """International SIGMETs (EUR) via AWC; filter LPPC."""
    try:
        r = requests.get("https://aviationweather.gov/api/data/isigmet", params=awc_params(), timeout=12)
        r.raise_for_status()
        data = r.json()
        out: List[str] = []
        items: List[Any] = data if isinstance(data, list) else data.get("features", []) or []
        for item in items:
            props: Dict[str,Any] = {}
            if isinstance(item, dict) and "properties" in item:
                props = item["properties"]
            elif isinstance(item, dict):
                props = item
            raw = (props.get("raw") or props.get("sigmet_text") or str(item) or "").strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            if not raw: 
                continue
            if fir == "LPPC" or " LPPC " in f" {raw} " or "FIR LPPC" in raw or " LPPC FIR" in raw:
                out.append(raw)
        return out
    except Exception:
        return []

def _json_to_text(j: Any) -> str:
    """Try to extract a GAMET-like text from flexible JSON responses."""
    if isinstance(j, str):
        return j.strip()
    if isinstance(j, list):
        # join lines if list of strings/objects
        parts: List[str] = []
        for it in j:
            if isinstance(it, str):
                parts.append(it.strip())
            elif isinstance(it, dict):
                # try common keys
                for k in ("text","gamet","raw","message","body"):
                    if k in it and isinstance(it[k], str):
                        parts.append(it[k].strip())
                        break
        return "\n".join([p for p in parts if p])
    if isinstance(j, dict):
        for k in ("text","gamet","raw","message","body","data"):
            v = j.get(k)
            if isinstance(v, str):
                return v.strip()
            if isinstance(v, list) or isinstance(v, dict):
                return _json_to_text(v)
    return ""

@st.cache_data(ttl=60)
def fetch_gamet_live() -> str:
    """Fetch GAMET text from a user-provided endpoint in secrets (GAMET_URL)."""
    url = (st.secrets.get("GAMET_URL","") or "").strip()
    if not url:
        return ""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        ct = r.headers.get("Content-Type","").lower()
        if "application/json" in ct:
            return _json_to_text(r.json())
        return r.text.strip()
    except Exception:
        return ""

# --------- query params ---------
qp = st.query_params
raw_icaos = qp.get("icao","")
if isinstance(raw_icaos, list):
    raw_icaos = ",".join(raw_icaos)
icaos: List[str] = []
if raw_icaos:
    for part in raw_icaos.split(","):
        p = part.strip().upper()
        if len(p)==4:
            icaos.append(p)
if not icaos:
    icaos = ["LPPT","LPBJ","LEBZ"]

# --------- UI header + refresh ---------
st.markdown('<div class="title">Weather (Live)</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Latest METAR, TAF, LPPC SIGMET, and GAMET</div>', unsafe_allow_html=True)
st.write("Airfields:", ", ".join(icaos))

refresh = st.button("Refresh data")
if refresh:
    st.cache_data.clear()

# --------- METAR / TAF ---------
st.markdown('<div class="grid">', unsafe_allow_html=True)
for icao in icaos:
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

# --------- SIGMET (LPPC) ---------
st.divider()
st.subheader("SIGMET (LPPC)")
sigs = fetch_sigmet_lppc()
if not sigs:
    st.write("—")
else:
    for s in sigs:
        st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
        st.markdown("---")

# --------- GAMET (Live) ---------
st.divider()
st.subheader("GAMET (Live)")
gamet_text = fetch_gamet_live()
if gamet_text:
    st.markdown(f'<div class="monos">{gamet_text}</div>', unsafe_allow_html=True)
else:
    st.write("No GAMET available. Configure a 'GAMET_URL' in secrets to enable live GAMET here.")



