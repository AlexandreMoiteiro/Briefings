# pages/Weather.py
import streamlit as st
import requests
import datetime
import time
import re
from typing import List, Dict
from bs4 import BeautifulSoup

# ================= Page setup (no sidebar) =================
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

# ================= Config =================
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]
CHECKWX_API_KEY = st.secrets.get("CHECKWX_API_KEY", "")
IPMA_SHOWSIGMET_URL = st.secrets.get("IPMA_SHOWSIGMET_URL", "https://brief-ng.ipma.pt/?page=showSIGMET")
IPMA_COOKIE = st.secrets.get("IPMA_COOKIE", "")  # e.g. 'l=en; PHPSESSID=...; ss=...; usr=Spitzer'

# ================= Helpers =================
def _cw_headers() -> Dict[str, str]:
    return {"X-API-Key": CHECKWX_API_KEY} if CHECKWX_API_KEY else {}

def _ipma_headers() -> Dict[str, str]:
    h = {"User-Agent": "Mozilla/5.0 (BriefingsApp/1.0)"}
    if IPMA_COOKIE:
        h["Cookie"] = IPMA_COOKIE
    # Be generous with headers to look like a real browser
    h["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    h["Accept-Language"] = "en-US,en;q=0.9,pt;q=0.8"
    h["Referer"] = "https://brief-ng.ipma.pt/"
    return h

# ================= METAR / TAF (CheckWX) =================
@st.cache_data(ttl=90)
def fetch_metar(icao: str) -> str:
    if not CHECKWX_API_KEY:
        return ""
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=_cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=90)
def fetch_taf(icao: str) -> str:
    if not CHECKWX_API_KEY:
        return ""
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=_cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text") or ""
        return str(data[0])
    except Exception:
        return ""

# ================= SIGMET & GAMET (IPMA showSIGMET HTML) =================
@st.cache_data(ttl=60)
def fetch_sigmet_gamet() -> Dict[str, List[str]]:
    """
    Requests the authenticated showSIGMET page and extracts LPPC SIGMET & GAMET text.
    Needs in secrets:
      IPMA_SHOWSIGMET_URL
      IPMA_COOKIE  (single line: l=en; PHPSESSID=...; ss=...; usr=Spitzer)
    Returns: {"sigmet": [...], "gamet": [...]}
    """
    url = IPMA_SHOWSIGMET_URL
    if not url:
        return {"sigmet": [], "gamet": []}
    try:
        bust = "&" if "?" in url else "?"
        r = requests.get(f"{url}{bust}_ts={int(time.time())}", headers=_ipma_headers(), timeout=12, allow_redirects=True)
        r.raise_for_status()
        html = r.text

        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one("#divContent")
        if not content:
            return {"sigmet": [], "gamet": []}

        # Normalize <br> → \n and extract clean text
        for br in content.find_all("br"):
            br.replace_with("\n")
        text = content.get_text("\n")
        text = re.sub(r"[ \t]+\n", "\n", text).strip()

        # GAMET blocks
        gamet = [m.group(0).strip() for m in re.finditer(r"(?ms)^LPPC\s+GAMET.*?(?:\n\n|$)", text)]
        if not gamet:
            # fallback: section with LPPC (...) that contains GAMET
            sec = re.search(r"(?ms)^LPPC\s*\(.*?\)\s*\n(.*?)(?:\n\n|$)", text)
            if sec and "GAMET" in sec.group(0):
                gamet = [sec.group(0).strip()]

        # SIGMET blocks mentioning LPPC
        sigmet = []
        for m in re.finditer(r"(?ms)^(?:LPPC\s+)?SIGMET.*?(?:\n\n|$)", text):
            blk = m.group(0).strip()
            if "LPPC" in blk:
                sigmet.append(blk)

        return {"sigmet": sigmet, "gamet": gamet}
    except Exception:
        return {"sigmet": [], "gamet": []}

# ================= Optional: quick diagnostics =================
with st.expander("IPMA connection diagnostics", expanded=False):
    try:
        test = requests.get(
            (IPMA_SHOWSIGMET_URL or "") + (("&" if "?" in (IPMA_SHOWSIGMET_URL or "") else "?") + f"_ts={int(time.time())}" if IPMA_SHOWSIGMET_URL else ""),
            headers=_ipma_headers(), timeout=12, allow_redirects=True
        )
        looks_logged = ("Logged in as" in test.text) or ("logout" in test.text.lower())
        has_div = ("id=\"divContent\"" in test.text) or ("id='divContent'" in test.text)
        st.write({"status": test.status_code, "final_url": test.url, "looks_logged": looks_logged, "has_divContent": has_div})
        st.code(test.text[:800], language="html")
    except Exception as e:
        st.write({"error": str(e)})

# ================= UI =================
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

# SIGMET / GAMET (IPMA)
data_ipma = fetch_sigmet_gamet()

st.subheader("SIGMET (LPPC)")
if not data_ipma["sigmet"]:
    st.info("No active LPPC SIGMETs.")
else:
    for s in data_ipma["sigmet"]:
        st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
        st.markdown("---")

st.subheader("GAMET (LPPC)")
if not data_ipma["gamet"]:
    st.info("No LPPC GAMET available.")
else:
    for g in data_ipma["gamet"]:
        st.markdown(f'<div class="monos">{g}</div>', unsafe_allow_html=True)
        st.markdown("---")

st.caption(f"Last updated: {datetime.datetime.utcnow():%Y-%m-%d %H:%M:%SZ} UTC")

