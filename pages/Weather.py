# pages/Weather.py
# Live METAR / TAF (CheckWX) + SIGMET (LPPC via AWC) + AIRMET (LPPC via CheckWX) + GAMET (try CheckWX + manual paste)
# Default ICAOs: LPPT, LPBJ, LEBZ
# No sidebar; clean layout

import streamlit as st
import requests
import datetime

# ==== PAGE CONFIG ====
st.set_page_config(page_title="Live Weather", layout="wide")

# ==== HIDE SIDEBAR & FOOTER ====
st.markdown(
    """
    <style>
      #MainMenu {visibility: hidden;}
      footer {visibility: hidden;}
      [data-testid="stSidebar"] {display: none;}
      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
      .card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); background: #fff; }
      .card h3 { margin: 0 0 6px; }
      .monos { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; font-size: .92rem; white-space: pre-wrap; }
    </style>
    """,
    unsafe_allow_html=True
)

# ==== CONFIG ====
CHECKWX_API_KEY = st.secrets.get("CHECKWX_API_KEY", "")
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

def _cw_headers():
    return {"X-API-Key": CHECKWX_API_KEY} if CHECKWX_API_KEY else {}

# ==== FETCHERS ====
@st.cache_data(ttl=90)
def fetch_metar_raw(icao: str) -> str:
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
def fetch_taf_raw(icao: str) -> str:
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
def fetch_sigmet_lppc_awc() -> list[str]:
    """
    AWC International SIGMET (global). Filter for LPPC (Lisbon FIR).
    Public endpoint; no key needed.
    """
    try:
        r = requests.get("https://aviationweather.gov/api/data/isigmet?format=json", timeout=12)
        r.raise_for_status()
        items = r.json()
        out = []
        for it in items:
            firname = (it.get("firname") or "").upper()
            firid = (it.get("firid") or "").upper()
            raw = it.get("rawtext") or it.get("raw") or it.get("sigmet_text") or ""
            if firid == "LPPC" or firname == "LPPC" or "LISBON" in firname:
                if raw.strip():
                    out.append(raw.strip())
        return out
    except Exception:
        return []

@st.cache_data(ttl=120)
def fetch_airmet_lppc_checkwx() -> list[str]:
    """AIRMET via CheckWX for LPPC FIR (decoded or raw)."""
    if not CHECKWX_API_KEY:
        return []
    try:
        # try decoded first
        r = requests.get("https://api.checkwx.com/airmet/LPPC/decoded", headers=_cw_headers(), timeout=12)
        if r.status_code == 200:
            data = r.json().get("data", [])
            out = []
            for it in data:
                text = it.get("raw") or it.get("raw_text") or it.get("report") or ""
                if text.strip():
                    out.append(text.strip())
            if out:
                return out
        # fallback raw
        r2 = requests.get("https://api.checkwx.com/airmet/LPPC", headers=_cw_headers(), timeout=12)
        if r2.status_code == 200:
            data = r2.json().get("data", [])
            out = []
            for it in data:
                text = it if isinstance(it, str) else (it.get("raw") or it.get("raw_text") or it.get("report") or "")
                if str(text).strip():
                    out.append(str(text).strip())
            return out
    except Exception:
        return []
    return []

@st.cache_data(ttl=180)
def fetch_gamet_lppc_checkwx() -> list[str]:
    """
    GAMET via CheckWX (only if your account has access).
    If the endpoint is unavailable, returns [] and we’ll rely on manual paste.
    """
    if not CHECKWX_API_KEY:
        return []
    try:
        # Some CheckWX accounts expose GAMET endpoints as /gamet/{fir}
        r = requests.get("https://api.checkwx.com/gamet/LPPC", headers=_cw_headers(), timeout=12)
        if r.status_code == 200:
            data = r.json().get("data", [])
            out = []
            for it in data:
                text = it if isinstance(it, str) else (it.get("raw") or it.get("raw_text") or it.get("report") or "")
                if str(text).strip():
                    out.append(str(text).strip())
            return out
    except Exception:
        return []
    return []

# ==== UI ====
st.title("🌍 Live Weather — METAR / TAF / SIGMET (LPPC) / AIRMET (LPPC) / GAMET")

icao_str = st.text_input("Enter ICAO codes (comma-separated):", value=",".join(DEFAULT_ICAOS))
icaos = [x.strip().upper() for x in icao_str.split(",") if x.strip()]

st.markdown("### Aerodromes")
st.write(", ".join(icaos))

# Grid: METAR / TAF
st.markdown("### METAR & TAF")
st.markdown('<div class="grid">', unsafe_allow_html=True)
for icao in icaos:
    metar = fetch_metar_raw(icao)
    taf = fetch_taf_raw(icao)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f"<h3>{icao}</h3>", unsafe_allow_html=True)
    st.caption("METAR (raw)")
    st.markdown(f'<div class="monos">{metar or "—"}</div>', unsafe_allow_html=True)
    st.caption("TAF (raw)")
    st.markdown(f'<div class="monos">{taf or "—"}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# SIGMET / AIRMET / GAMET (LPPC)
st.markdown("### LPPC FIR Advisories")
cols = st.columns(3)

with cols[0]:
    st.subheader("SIGMET (LPPC)")
    sigs = fetch_sigmet_lppc_awc()
    if not sigs:
        st.info("No active SIGMET for LPPC FIR.")
    else:
        for s in sigs:
            st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
            st.markdown("---")

with cols[1]:
    st.subheader("AIRMET (LPPC)")
    airs = fetch_airmet_lppc_checkwx()
    if not CHECKWX_API_KEY:
        st.warning("Add CHECKWX_API_KEY in secrets to enable AIRMET.")
    elif not airs:
        st.info("No active AIRMET for LPPC (or endpoint not available for your account).")
    else:
        for a in airs:
            st.markdown(f'<div class="monos">{a}</div>', unsafe_allow_html=True)
            st.markdown("---")

with cols[2]:
    st.subheader("GAMET (LPPC)")
    gamets = fetch_gamet_lppc_checkwx()
    manual = st.text_area("Paste GAMET (raw) if needed:", value="", height=120)
    if gamets:
        for g in gamets:
            st.markdown(f'<div class="monos">{g}</div>', unsafe_allow_html=True)
            st.markdown("---")
    if manual.strip():
        st.markdown("**Manual GAMET (raw):**")
        st.markdown(f'<div class="monos">{manual.strip()}</div>', unsafe_allow_html=True)

# Timestamp
st.caption(f"Last updated: {datetime.datetime.utcnow():%Y-%m-%d %H:%M:%SZ} UTC")



