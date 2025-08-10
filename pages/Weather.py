# pages/Weather.py
# Clean live page: METAR/TAF (CheckWX), LPPC SIGMET (AWC), LPPC AIRMET (CheckWX),
# GAMET LPPC via (A) CheckWX -> (B) custom endpoint (GAMET_URL in secrets) -> (C) manual paste box.
#
# Defaults: LPPT, LPBJ, LEBZ
# No sidebar; tidy cards; tabs; optional auto-refresh.

import streamlit as st
import requests
import datetime
import time

# ---------- PAGE CONFIG ----------
st.set_page_config(page_title="Live Weather", layout="wide")

# ---------- HIDE SIDEBAR/FOOTER ----------
st.markdown(
    """
    <style>
      #MainMenu {visibility: hidden;}
      footer {visibility: hidden;}
      [data-testid="stSidebar"] {display: none;}
      body { background: #fbfbfc; }
      .topbar {
        display: flex; align-items: center; justify-content: space-between;
        gap: 12px; margin-bottom: 10px;
      }
      .title { font-weight: 800; font-size: 1.6rem; }
      .muted { color: #6b7280; font-size: .95rem; }
      .pill {
        display:inline-block; padding: 6px 10px; border:1px solid #e5e7eb; border-radius: 999px;
        background:#fff; font-size:.9rem; color:#374151;
      }
      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 14px; }
      .card {
        border: 1px solid #e5e7eb; border-radius: 14px; padding: 14px 16px;
        box-shadow: 0 1px 2px rgba(0,0,0,.04); background: #fff;
      }
      .card h3 { margin: 0 0 6px; font-size: 1.05rem; }
      .monos {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: .92rem; white-space: pre-wrap;
        background: #f8fafc; padding: 10px 12px; border-radius: 12px; border:1px solid #e6e9ee;
      }
      .section-subtle { color:#4b5563; font-size:.95rem; }
    </style>
    """,
    unsafe_allow_html=True
)

# ---------- CONFIG ----------
CHECKWX_API_KEY = st.secrets.get("CHECKWX_API_KEY", "")
CUSTOM_GAMET_URL = st.secrets.get("GAMET_URL", "")  # Optional: your own endpoint returning LPPC GAMET raw text
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

def _cw_headers():
    return {"X-API-Key": CHECKWX_API_KEY} if CHECKWX_API_KEY else {}

# ---------- FETCHERS ----------
@st.cache_data(ttl=90)
def fetch_metar_raw(icao: str) -> str:
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
def fetch_taf_raw(icao: str) -> str:
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
def fetch_sigmet_lppc_awc() -> list[str]:
    """International SIGMETs (global) filtered for LPPC (Lisbon FIR). Public endpoint."""
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
    """AIRMET via CheckWX for LPPC (decoded -> raw)."""
    if not CHECKWX_API_KEY:
        return []
    try:
        r = requests.get("https://api.checkwx.com/airmet/LPPC/decoded", headers=_cw_headers(), timeout=12)
        if r.status_code == 200:
            data = r.json().get("data", [])
            out = []
            for it in data:
                txt = it.get("raw") or it.get("raw_text") or it.get("report") or ""
                if txt.strip(): out.append(txt.strip())
            if out: return out
        r2 = requests.get("https://api.checkwx.com/airmet/LPPC", headers=_cw_headers(), timeout=12)
        if r2.status_code == 200:
            data = r2.json().get("data", [])
            out = []
            for it in data:
                txt = it if isinstance(it, str) else (it.get("raw") or it.get("raw_text") or it.get("report") or "")
                if str(txt).strip(): out.append(str(txt).strip())
            return out
    except Exception:
        return []
    return []

@st.cache_data(ttl=180)
def fetch_gamet_lppc() -> list[str]:
    """
    GAMET LPPC via:
      A) CheckWX -> /gamet/LPPC (if available on your plan)
      B) Custom endpoint in secrets (GAMET_URL) that returns raw GAMET text
    Returns list of strings (each a GAMET block).
    """
    # A) CheckWX
    if CHECKWX_API_KEY:
        try:
            r = requests.get("https://api.checkwx.com/gamet/LPPC", headers=_cw_headers(), timeout=12)
            if r.status_code == 200:
                data = r.json().get("data", [])
                out = []
                for it in data:
                    txt = it if isinstance(it, str) else (it.get("raw") or it.get("raw_text") or it.get("report") or "")
                    if str(txt).strip(): out.append(str(txt).strip())
                if out: return out
        except Exception:
            pass
    # B) Custom endpoint
    if CUSTOM_GAMET_URL:
        try:
            r = requests.get(CUSTOM_GAMET_URL, timeout=10)
            if r.status_code == 200 and r.text.strip():
                # Allow multi-GAMET separated by blank lines
                blocks = [b.strip() for b in r.text.replace("\r\n", "\n").split("\n\n") if b.strip()]
                if blocks: return blocks
        except Exception:
            pass
    return []

# ---------- HEADER ----------
with st.container():
    colL, colR = st.columns([0.7, 0.3])
    with colL:
        st.markdown('<div class="topbar"><div><div class="title">Live Weather</div><div class="muted">METAR • TAF • SIGMET (LPPC) • AIRMET (LPPC) • GAMET</div></div></div>', unsafe_allow_html=True)
    with colR:
        auto = st.toggle("Auto-refresh (60s)", value=False)
        refresh = st.button("Refresh now")

if auto or refresh:
    st.cache_data.clear()
    if auto:
        time.sleep(0.2)

# ---------- AIRFIELDS INPUT ----------
icao_str = st.text_input("Airfields (ICAO, comma-separated)", value="LPPT,LPBJ,LEBZ")
icaos = [x.strip().upper() for x in icao_str.split(",") if x.strip()]
st.markdown(f'<span class="pill">Airfields: {", ".join(icaos)}</span>', unsafe_allow_html=True)

# ---------- TABS ----------
tab1, tab2 = st.tabs(["Airfields (METAR & TAF)", "LPPC Advisories (SIGMET / AIRMET / GAMET)"])

with tab1:
    st.markdown('<div class="section-subtle">Raw strings for briefing/validation.</div>', unsafe_allow_html=True)
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

with tab2:
    c1, c2, c3 = st.columns(3)

    # SIGMET
    with c1:
        st.subheader("SIGMET (LPPC)")
        sigs = fetch_sigmet_lppc_awc()
        if not sigs:
            st.info("No active SIGMET for LPPC FIR.")
        else:
            for s in sigs:
                st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
                st.markdown("---")

    # AIRMET
    with c2:
        st.subheader("AIRMET (LPPC)")
        if not CHECKWX_API_KEY:
            st.warning("Add CHECKWX_API_KEY in secrets to enable AIRMET.")
        airs = fetch_airmet_lppc_checkwx()
        if airs:
            for a in airs:
                st.markdown(f'<div class="monos">{a}</div>', unsafe_allow_html=True)
                st.markdown("---")
        elif CHECKWX_API_KEY:
            st.info("No active AIRMET for LPPC or endpoint not available on your plan.")

    # GAMET
    with c3:
        st.subheader("GAMET (LPPC)")
        gamets = fetch_gamet_lppc()
        manual = st.text_area("Paste GAMET (raw) if needed:", value="", height=140)
        if gamets:
            for g in gamets:
                st.markdown(f'<div class="monos">{g}</div>', unsafe_allow_html=True)
                st.markdown("---")
        if manual.strip():
            st.markdown("**Manual GAMET (raw):**")
            st.markdown(f'<div class="monos">{manual.strip()}</div>', unsafe_allow_html=True)

# ---------- TIMESTAMP ----------
st.caption(f"Last updated: {datetime.datetime.utcnow():%Y-%m-%d %H:%M:%SZ} UTC")




