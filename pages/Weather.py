# pages/Weather.py
# Live METAR / TAF / SIGMET (LPPC via AWC International SIGMET API)
# - Defaults: LPPT, LPBJ, LEBZ
# - ?icao=AAA ou ?icao=AAA,BBB para custom

from typing import List, Dict, Any
import streamlit as st
import requests

st.set_page_config(page_title="Weather (Live)", layout="wide")

st.markdown(
    """
    <style>
      .title { font-size: 1.8rem; font-weight: 800; margin-bottom: .25rem;}
      .muted { color: #6b7280; margin-bottom: 1rem;}
      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }
      .card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); background: #fff; }
      .card h3 { margin: 0 0 6px; }
      .monos { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size: .92rem; white-space: pre-wrap; }
      .section { margin-top: 16px; }
    </style>
    """,
    unsafe_allow_html=True
)

def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

@st.cache_data(ttl=90)
def fetch_metar(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=cw_headers(), timeout=10)
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
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text") or ""
        return str(data[0])
    except Exception:
        return ""

def awc_params() -> Dict[str,str]:
    p: Dict[str,str] = {"loc":"eur", "format":"json"}
    key = st.secrets.get("AWC_API_KEY","")  # opcional
    if key:
        p["api_key"] = key
    return p

@st.cache_data(ttl=120)
def fetch_sigmet_lppc() -> List[str]:
    """Busca International SIGMETs (região EUR) via AWC e filtra FIR=LPPC."""
    try:
        r = requests.get("https://aviationweather.gov/api/data/isigmet", params=awc_params(), timeout=12)
        r.raise_for_status()
        data = r.json()
        out: List[str] = []
        # Estruturas variam; tentamos várias chaves e fallback pelo 'raw' contendo LPPC
        for item in data if isinstance(data, list) else data.get("features", []) or []:
            # pode vir como lista simples (json) OU geojson em features
            obj: Dict[str,Any]
            raw = ""
            fir = ""
            if isinstance(item, dict) and "properties" in item:
                obj = item["properties"]
                raw = obj.get("raw","") or obj.get("sigmet_text","") or ""
                fir = (obj.get("fir","") or obj.get("firid","") or obj.get("firId","") or "").upper()
            elif isinstance(item, dict):
                obj = item
                raw = obj.get("raw","") or obj.get("sigmet_text","") or ""
                fir = (obj.get("fir","") or obj.get("firid","") or obj.get("firId","") or "").upper()
            else:
                raw = str(item)
                fir = ""
            text = (raw or "").strip()
            # filtro LPPC: por campo FIR quando disponível ou por texto contendo ' LPPC ' / ' FIR LPPC '
            if text:
                if fir == "LPPC" or " LPPC " in f" {text} " or "FIR LPPC" in text or " LPPC FIR" in text:
                    out.append(text)
        return out
    except Exception:
        return []

# Query params
q = st.query_params
raw_icaos = q.get("icao","")
if isinstance(raw_icaos, list):
    raw_icaos = ",".join(raw_icaos)
icaos: List[str] = []
if raw_icaos:
    for part in raw_icaos.split(","):
        p = part.strip().upper()
        if len(p)==4: icaos.append(p)
if not icaos:
    icaos = ["LPPT","LPBJ","LEBZ"]

st.markdown('<div class="title">Weather (Live)</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Latest METAR, TAF and LPPC SIGMET</div>', unsafe_allow_html=True)
st.write("Airfields:", ", ".join(icaos))

# METAR/TAF grid
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

# LPPC SIGMET
st.divider()
st.subheader("SIGMET (LPPC)")
sigs = fetch_sigmet_lppc()
if not sigs:
    st.write("—")
else:
    for s in sigs:
        st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
        st.markdown("---")


