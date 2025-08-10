import streamlit as st
import requests
import datetime
from typing import List, Dict, Any
from bs4 import BeautifulSoup
import re

# --- DIAGNOSTIC: test IPMA session from server ---
def _ipma_headers_for_diagnostics():
    h = {}
    bearer = st.secrets.get("IPMA_BEARER", "")
    cookie = st.secrets.get("IPMA_COOKIE", "")
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    if cookie:
        h["Cookie"] = cookie
    h["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BriefingsApp/1.0"
    h["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    h["Accept-Language"] = "en-US,en;q=0.9,pt;q=0.8"
    h["Referer"] = "https://brief-ng.ipma.pt/"
    return h

def ipma_connectivity_check():
    url = st.secrets.get("IPMA_SHOWSIGMET_URL", "")
    if not url:
        return {"ok": False, "msg": "Missing IPMA_SHOWSIGMET_URL in secrets."}
    try:
        # cache-buster para evitar páginas antigas
        import time
        test_url = url + ("&" if "?" in url else "?") + f"_ts={int(time.time())}"
        r = requests.get(test_url, headers=_ipma_headers_for_diagnostics(), timeout=12, allow_redirects=True)
        snippet = (r.text or "")[:800]
        return {
            "ok": r.ok,
            "status": r.status_code,
            "final_url": r.url,
            "content_type": r.headers.get("content-type",""),
            "looks_logged": ("Logged in as" in r.text) or ("logout" in r.text.lower()),
            "has_divContent": ("id=\"divContent\"" in r.text) or ("id='divContent'" in r.text),
            "snippet": snippet
        }
    except Exception as e:
        return {"ok": False, "msg": f"Exception: {e}"}

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

DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]
CHECKWX_API_KEY = st.secrets.get("CHECKWX_API_KEY", "")

def _cw_headers() -> Dict[str, str]:
    return {"X-API-Key": CHECKWX_API_KEY} if CHECKWX_API_KEY else {}

# ---------------- METAR/TAF (CheckWX) ----------------
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

# ---------------- SIGMET/GAMET from IPMA showSIGMET page ----------------
def _ipma_headers_from_secrets() -> Dict[str, str]:
    h: Dict[str, str] = {}
    bearer = st.secrets.get("IPMA_BEARER", "")
    cookie = st.secrets.get("IPMA_COOKIE", "")
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    if cookie:
        h["Cookie"] = cookie
    h["User-Agent"] = "Mozilla/5.0 (compatible; BriefingsApp/1.0)"
    return h

@st.cache_data(ttl=90)
def fetch_sigmet_gamet_from_ipma_page() -> Dict[str, List[str]]:
    """
    Requests the authenticated showSIGMET HTML page and extracts LPPC SIGMET & GAMET text.
    Secrets required:
      IPMA_SHOWSIGMET_URL = "https://brief-ng.ipma.pt/?page=showSIGMET"
      IPMA_COOKIE = "...cookie..." (or IPMA_BEARER = "eyJ...")
    Returns: {"sigmet": [...], "gamet": [...]}
    """
    url = st.secrets.get("IPMA_SHOWSIGMET_URL", "")
    if not url:
        return {"sigmet": [], "gamet": []}
    try:
        r = requests.get(url, headers=_ipma_headers_from_secrets(), timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one("#divContent")
        if not content:
            return {"sigmet": [], "gamet": []}
        for br in content.find_all("br"):
            br.replace_with("\n")
        text = content.get_text("\n")
        text = re.sub(r"[ \t]+\n", "\n", text).strip()

        # Extract GAMET LPPC blocks
        gamet_blocks: List[str] = []
        # Primary: blocks starting with LPPC GAMET ...
        for m in re.finditer(r"(?ms)^LPPC\s+GAMET.*?(?:\n\n|$)", text):
            gamet_blocks.append(m.group(0).strip())
        # Fallback: detect section containing 'LPPC (' and 'GAMET'
        if not gamet_blocks:
            sec = re.search(r"(?ms)^LPPC\s*\(.*?\)\s*\n(.*?)(?:\n\n|$)", text)
            if sec and "GAMET" in sec.group(0):
                gamet_blocks = [sec.group(0).strip()]

        # Extract SIGMET LPPC blocks
        sigmet_blocks: List[str] = []
        for m in re.finditer(r"(?ms)^(?:LPPC\s+)?SIGMET.*?(?:\n\n|$)", text):
            blk = m.group(0).strip()
            if "LPPC" in blk:
                sigmet_blocks.append(blk)

        return {"sigmet": sigmet_blocks, "gamet": gamet_blocks}
    except Exception:
        return {"sigmet": [], "gamet": []}

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

# SIGMET & GAMET from IPMA
data_ipma = fetch_sigmet_gamet_from_ipma_page()

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





