# pages/Weather.py
# Live METAR / TAF / SIGMET via CheckWX
# - Defaults: LPPT, LPBJ, LEBZ
# - Supports query param ?icao=XXXX (single) or ?icao=AAA,BBB,...
# - No sidebar; clean cards layout

from typing import List, Tuple, Optional, Dict
import streamlit as st
import requests
import urllib.parse

st.set_page_config(page_title="Weather", layout="wide")

# ---------- Styles ----------
st.markdown(
    """
    <style>
      .title { font-size: 1.8rem; font-weight: 700; margin-bottom: .25rem;}
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

# ---------- FIR mapping ----------
PORTUGAL_AZORES_ICAO = {"LPAZ","LPLA","LPPD","LPPI","LPFL","LPHR","LPGR","LPSJ"}
FIR_BY_PREFIX = {
    "LP": "LPPC",
    "LE": "LECM",
    "LF": "LFFF",
    "EG": "EGTT",
    "EI": "EISN",
    "ED": "EDGG",
    "LI": "LIRR",
}
def icao_to_fir(icao: str) -> Optional[str]:
    if not icao or len(icao) != 4: return None
    u = icao.upper()
    if u.startswith("LP"):
        return "LPPO" if u in PORTUGAL_AZORES_ICAO else "LPPC"
    return FIR_BY_PREFIX.get(u[:2])

# ---------- CheckWX helpers ----------
def checkwx_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY", "")
    return {"X-API-Key": key} if key else {}

@st.cache_data(ttl=90)
def fetch_metar(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=checkwx_headers(), timeout=10)
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
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=checkwx_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=120)
def fetch_sigmet(fir: str) -> List[str]:
    if not fir: return []
    try:
        r = requests.get(f"https://api.checkwx.com/sigmet/{fir}/decoded", headers=checkwx_headers(), timeout=12)
        r.raise_for_status()
        out = []
        for it in r.json().get("data", []):
            if isinstance(it, dict):
                raw = it.get("raw") or it.get("raw_text") or it.get("report") or ""
            else:
                raw = str(it)
            raw = (raw or "").strip()
            if raw: out.append(raw)
        return out
    except Exception:
        return []

# ---------- Parse query ----------
query = st.query_params
icaos_param = query.get("icao", "")
if isinstance(icaos_param, list):
    icaos_raw = ",".join(icaos_param)
else:
    icaos_raw = icaos_param or ""

icaos: List[str] = []
if icaos_raw:
    for part in icaos_raw.split(","):
        p = part.strip().upper()
        if len(p) == 4:
            icaos.append(p)

if not icaos:
    icaos = ["LPPT", "LPBJ", "LEBZ"]

# ---------- UI ----------
st.markdown('<div class="title">Weather (Live)</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Latest METAR, TAF and SIGMET</div>', unsafe_allow_html=True)

# Show ICAOs line
st.write("Airfields:", ", ".join(icaos))

# Cards for METAR/TAF
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

# SIGMETs by FIR (unique FIRs for the set of ICAOs)
firs = sorted({icao_to_fir(i) for i in icaos if icao_to_fir(i)})
st.markdown('<div class="section"></div>', unsafe_allow_html=True)
st.subheader("SIGMETs")
if not firs:
    st.write("No FIR inferred.")
else:
    for fir in firs:
        sigmets = fetch_sigmet(fir)
        st.markdown(f"**{fir}**")
        if not sigmets:
            st.write("—")
        else:
            for s in sigmets:
                st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
                st.markdown("---")



