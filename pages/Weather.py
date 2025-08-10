# pages/Weather.py
import streamlit as st
import requests, time, re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from typing import Dict, List, Tuple

# ---------------- Page config (no sidebar) ----------------
st.set_page_config(page_title="Live Weather", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stHamburger"] { display:none !important; }
.block-container { padding-top: 1.2rem; }
.title { font-size: 1.8rem; font-weight: 800; margin-bottom: .25rem; }
.muted { color: #6b7280; margin-bottom: 1rem; }
.monos { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; white-space: pre-wrap; font-size: .92rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
.card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); background: #fff; }
.card h3 { margin: 0 0 6px; }
</style>
""", unsafe_allow_html=True)

# ---------------- Config ----------------
BASE_URL = "https://brief-ng.ipma.pt/"
SHOW_SIGMET_URL = "https://brief-ng.ipma.pt/?page=showSIGMET"

IPMA_USER = st.secrets.get("IPMA_USER", "")
IPMA_PASS = st.secrets.get("IPMA_PASS", "")
CHECKWX_API_KEY = st.secrets.get("CHECKWX_API_KEY", "")  # optional

DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BriefingsApp/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
    "Referer": BASE_URL,
}

# ---------------- Helpers: CheckWX (optional) ----------------
def _cw_headers() -> Dict[str,str]:
    return {"X-API-Key": CHECKWX_API_KEY} if CHECKWX_API_KEY else {}

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

# ---------------- Helpers: login form parsing ----------------
def guess_login_fields(inputs: Dict[str, str]) -> Tuple[str, str]:
    user_keys = ["username", "user", "usr", "login", "email"]
    pass_keys = ["password", "pass", "pwd"]
    u_key = next((k for k in inputs if any(x in k.lower() for x in user_keys)), "")
    p_key = next((k for k in inputs if any(x in k.lower() for x in pass_keys)), "")
    return u_key, p_key

def parse_form(html: str) -> Tuple[str, str, Dict[str, str]]:
    """
    Return (action_url, method, payload) for the first login form.
    Builds a safe absolute action and captures hidden inputs.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form#login, form#frmLogin, form[name='login'], form[action*='login'], form[action*='Login']")
    if not form:
        for f in soup.find_all("form"):
            if f.find("input", {"type": "password"}):
                form = f
                break
    if not form:
        return SHOW_SIGMET_URL, "post", {}

    action = (form.get("action") or "").strip()
    method = (form.get("method") or "post").lower()
    if (not action) or action.startswith("#") or "javascript:" in action.lower():
        action = SHOW_SIGMET_URL
    action_url = urljoin(SHOW_SIGMET_URL, action)

    payload: Dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        val = inp.get("value") or ""
        if itype in ["submit", "button", "image", "file"]:
            continue
        if itype in ["checkbox", "radio"]:
            if inp.has_attr("checked"):
                payload[name] = val or "on"
            continue
        payload[name] = val
    return action_url, method, payload

# ---------------- IPMA login + fetch SIGMET/GAMET ----------------
def login_and_fetch_sigmet_gamet(user: str, pwd: str, ttl: int = 180) -> Dict[str, List[str]]:
    """
    Logs in using username/password and extracts LPPC SIGMET & GAMET from showSIGMET.
    Caches the requests.Session for 'ttl' seconds in session_state to reduce logins.
    """
    now = time.time()
    cache = st.session_state.get("ipma_cache", {})
    if cache and (now - cache.get("ts", 0) < ttl):
        s: requests.Session = cache["session"]
    else:
        s = requests.Session()
        s.headers.update(HDRS)

        # 1) Load page (get login form)
        r0 = s.get(SHOW_SIGMET_URL, timeout=12, allow_redirects=True)
        r0.raise_for_status()
        action_url, method, payload = parse_form(r0.text)

        # If no password field, try forcing login screen (#showLogin)
        if not any(k for k in payload if "pass" in k.lower() or "pwd" in k.lower()):
            r0b = s.get(SHOW_SIGMET_URL + "#showLogin", timeout=12, allow_redirects=True)
            r0b.raise_for_status()
            action_url, method, payload = parse_form(r0b.text)

        # Fill credentials (best guess)
        u_key, p_key = guess_login_fields(payload)
        if u_key and p_key:
            payload[u_key] = user
            payload[p_key] = pwd
        else:
            # fallbacks used by some legacy portals
            if "usr" in payload: payload["usr"] = user
            if "pwd" in payload: payload["pwd"] = pwd
            payload.setdefault("username", user)
            payload.setdefault("password", pwd)

        # Ensure absolute action URL
        if not action_url.lower().startswith(("http://", "https://")):
            action_url = urljoin(SHOW_SIGMET_URL, action_url)

        # 2) Submit form
        if method == "get":
            r1 = s.get(action_url, params=payload, timeout=12, allow_redirects=True)
        else:
            r1 = s.post(action_url, data=payload, timeout=12, allow_redirects=True)
        r1.raise_for_status()

        # Heuristic: check logged-in indicators
        html1 = r1.text
        looks_logged = ("Logged in as" in html1) or ("logout" in html1.lower()) or ("SIGMET/AIRMET/GAMET" in html1) or ("Saved flights" in html1)
        if not looks_logged:
            # Some portals redirect after login
            r1b = s.get(SHOW_SIGMET_URL, timeout=12, allow_redirects=True)
            r1b.raise_for_status()
            html1 = r1b.text
            looks_logged = ("Logged in as" in html1) or ("logout" in html1.lower()) or ("SIGMET/AIRMET/GAMET" in html1)

        if not looks_logged:
            return {"sigmet": [], "gamet": []}

        st.session_state["ipma_cache"] = {"session": s, "ts": now}

    # 3) Fetch SIGMET/GAMET page with live session
    r2 = s.get(SHOW_SIGMET_URL, timeout=12)
    r2.raise_for_status()
    soup = BeautifulSoup(r2.text, "html.parser")
    content = soup.select_one("#divContent")
    if not content:
        return {"sigmet": [], "gamet": []}

    for br in content.find_all("br"):
        br.replace_with("\n")
    text = re.sub(r"[ \t]+\n", "\n", content.get_text("\n")).strip()

    # Extract GAMET LPPC
    gamet = [m.group(0).strip() for m in re.finditer(r"(?ms)^LPPC\s+GAMET.*?(?:\n\n|$)", text)]
    if not gamet and "GAMET" in text:
        # fallback: first LPPC block mentioning GAMET
        m = re.search(r"(?ms)^LPPC.*?(?:\n\n|$)", text)
        if m and "GAMET" in m.group(0):
            gamet = [m.group(0).strip()]

    # Extract SIGMET LPPC
    sigmet: List[str] = []
    for m in re.finditer(r"(?ms)^(?:LPPC\s+)?SIGMET.*?(?:\n\n|$)", text):
        blk = m.group(0).strip()
        if "LPPC" in blk:
            sigmet.append(blk)

    return {"sigmet": sigmet, "gamet": gamet}

# ---------------- UI ----------------
st.markdown('<div class="title">Live Weather</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Auto-login to IPMA • LPPC SIGMET & GAMET</div>', unsafe_allow_html=True)

# Diagnostics (optional)
with st.expander("Diagnostics", expanded=False):
    st.write({"has_user": bool(IPMA_USER), "has_pass": bool(IPMA_PASS)})
    st.code(f"SHOW_SIGMET_URL = {SHOW_SIGMET_URL}")

if not IPMA_USER or not IPMA_PASS:
    st.error("Set IPMA_USER and IPMA_PASS in .streamlit/secrets.toml")
    st.stop()

data = login_and_fetch_sigmet_gamet(IPMA_USER, IPMA_PASS, ttl=180)

# SIGMET & GAMET columns
c1, c2 = st.columns(2)
with c1:
    st.subheader("SIGMET (LPPC)")
    if data["sigmet"]:
        for s in data["sigmet"]:
            st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
            st.markdown("---")
    else:
        st.info("No active LPPC SIGMET found.")

with c2:
    st.subheader("GAMET (LPPC)")
    if data["gamet"]:
        for g in data["gamet"]:
            st.markdown(f'<div class="monos">{g}</div>', unsafe_allow_html=True)
            st.markdown("---")
    else:
        st.info("No LPPC GAMET found.")

# Optional METAR/TAF at the bottom (only if you set CHECKWX_API_KEY)
if CHECKWX_API_KEY:
    st.divider()
    st.subheader("METAR / TAF (CheckWX)")
    icao_str = st.text_input("ICAO (comma-separated)", value=",".join(DEFAULT_ICAOS))
    ICAOS = [c.strip().upper() for c in icao_str.split(",") if c.strip()]
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



