import streamlit as st
import requests, time, re
from bs4 import BeautifulSoup
from typing import Dict, List, Tuple

# ---------- Page config (no sidebar) ----------
st.set_page_config(page_title="Live Weather", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stHamburger"] { display:none !important; }
.block-container { padding-top: 1.2rem; }
.title { font-size: 1.8rem; font-weight: 800; margin-bottom: .25rem; }
.muted { color: #6b7280; margin-bottom: 1rem; }
.monos { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; white-space: pre-wrap; font-size: .92rem; }
</style>
""", unsafe_allow_html=True)

BASE_URL = "https://brief-ng.ipma.pt/"
SHOW_SIGMET_URL = "https://brief-ng.ipma.pt/?page=showSIGMET"

IPMA_USER = st.secrets.get("IPMA_USER", "")
IPMA_PASS = st.secrets.get("IPMA_PASS", "")

# Optional: CheckWX for METAR/TAF if you want to add later
CHECKWX_API_KEY = st.secrets.get("CHECKWX_API_KEY", "")

# ---------- Helpers ----------
HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BriefingsApp/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
    "Referer": BASE_URL,
}

def guess_login_fields(inputs: Dict[str, str]) -> Tuple[str, str]:
    """Try to find the username/password field names by common patterns."""
    user_keys = ["username", "user", "usr", "login", "email"]
    pass_keys = ["password", "pass", "pwd"]
    u_key = next((k for k in inputs if any(x in k.lower() for x in user_keys)), "")
    p_key = next((k for k in inputs if any(x in k.lower() for x in pass_keys)), "")
    return u_key, p_key

def parse_form(html: str) -> Tuple[str, str, Dict[str, str]]:
    """Return (action_url, method, payload) for the first visible login form."""
    soup = BeautifulSoup(html, "html.parser")
    # Try obvious ids/names first
    form = soup.select_one("form#login, form#frmLogin, form[name='login'], form[action*='login'], form[action*='Login']")
    if not form:
        # Fallback: first <form> that contains a password input
        for f in soup.find_all("form"):
            if f.find("input", {"type": "password"}):
                form = f
                break
    if not form:
        return "", "get", {}

    action = form.get("action") or SHOW_SIGMET_URL
    # Make absolute if needed
    if action.startswith("/"):
        action_url = BASE_URL.rstrip("/") + action
    elif action.startswith("http"):
        action_url = action
    else:
        action_url = BASE_URL + action.lstrip("./")

    method = (form.get("method") or "post").lower()

    payload: Dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        val = inp.get("value") or ""
        # don’t prefill file/submit inputs
        if itype in ["submit", "button", "image", "file", "checkbox", "radio"]:
            if itype in ["checkbox", "radio"] and inp.has_attr("checked"):
                payload[name] = val or "on"
            continue
        payload[name] = val
    return action_url, method, payload

def login_and_fetch_sigmet_gamet(user: str, pwd: str, ttl: int = 120) -> Dict[str, List[str]]:
    """
    Logs in with username/password and fetches SIGMET/GAMET from showSIGMET.
    Caches cookies in session_state for 'ttl' seconds to avoid logging in every call.
    """
    now = time.time()
    cache = st.session_state.get("ipma_cache", {})
    if cache and (now - cache.get("ts", 0) < ttl):
        s = cache["session"]
    else:
        s = requests.Session()
        s.headers.update(HDRS)

        # 1) Load page (get login form)
        r0 = s.get(SHOW_SIGMET_URL, timeout=12, allow_redirects=True)
        r0.raise_for_status()

        action_url, method, payload = parse_form(r0.text)

        # If there is no password field in the form, maybe already logged in
        if not any(k for k in payload if "pass" in k.lower() or "pwd" in k.lower()):
            # Try #showLogin anchor (forces login screen on some systems)
            r0b = s.get(SHOW_SIGMET_URL + "#showLogin", timeout=12, allow_redirects=True)
            r0b.raise_for_status()
            action_url, method, payload = parse_form(r0b.text)

        # Fill credentials
        u_key, p_key = guess_login_fields(payload)
        if not u_key or not p_key:
            # Try some common fallbacks
            if "usr" in payload and "pwd" in payload:
                u_key, p_key = "usr", "pwd"
            elif "username" in payload and "password" in payload:
                u_key, p_key = "username", "password"
            elif "user" in payload and "password" in payload:
                u_key, p_key = "user", "password"

        if u_key and p_key:
            payload[u_key] = user
            payload[p_key] = pwd
        else:
            # Last-resort guess – some legacy forms use these:
            payload.update({"usr": user, "pwd": pwd})

        # 2) Submit
        if method == "get":
            r1 = s.get(action_url, params=payload, timeout=12, allow_redirects=True)
        else:
            r1 = s.post(action_url, data=payload, timeout=12, allow_redirects=True)
        r1.raise_for_status()

        # Heuristic: check if we are logged in
        html1 = r1.text
        looks_logged = ("Logged in as" in html1) or ("logout" in html1.lower()) or ("Saved flights" in html1)
        if not looks_logged:
            # Some portals require a second redirect to the overview
            r1b = s.get(SHOW_SIGMET_URL, timeout=12, allow_redirects=True)
            html1 = r1b.text
            looks_logged = ("Logged in as" in html1) or ("logout" in html1.lower()) or ("SIGMET/AIRMET/GAMET" in html1)

        if not looks_logged:
            return {"sigmet": [], "gamet": []}

        st.session_state["ipma_cache"] = {"session": s, "ts": now}

    # 3) Fetch the SIGMET page with live session
    r2 = s.get(SHOW_SIGMET_URL, timeout=12)
    r2.raise_for_status()
    soup = BeautifulSoup(r2.text, "html.parser")
    content = soup.select_one("#divContent")
    if not content:
        return {"sigmet": [], "gamet": []}

    for br in content.find_all("br"):
        br.replace_with("\n")
    text = re.sub(r"[ \t]+\n", "\n", content.get_text("\n")).strip()

    # GAMET blocks
    gamet = [m.group(0).strip() for m in re.finditer(r"(?ms)^LPPC\s+GAMET.*?(?:\n\n|$)", text)]
    if not gamet and "GAMET" in text:
        # fallback: whole LPPC section that mentions GAMET
        m = re.search(r"(?ms)^LPPC.*?(?:\n\n|$)", text)
        if m and "GAMET" in m.group(0):
            gamet = [m.group(0).strip()]

    # SIGMET blocks (LPPC)
    sigmet = []
    for m in re.finditer(r"(?ms)^(?:LPPC\s+)?SIGMET.*?(?:\n\n|$)", text):
        blk = m.group(0).strip()
        if "LPPC" in blk:
            sigmet.append(blk)

    return {"sigmet": sigmet, "gamet": gamet}

# ---------- UI ----------
st.markdown('<div class="title">Live Weather</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Auto-login to IPMA • LPPC SIGMET & GAMET</div>', unsafe_allow_html=True)

if not IPMA_USER or not IPMA_PASS:
    st.error("Set IPMA_USER and IPMA_PASS in .streamlit/secrets.toml")
    st.stop()

data = login_and_fetch_sigmet_gamet(IPMA_USER, IPMA_PASS, ttl=180)

col1, col2 = st.columns(2)
with col1:
    st.subheader("SIGMET (LPPC)")
    if data["sigmet"]:
        for s in data["sigmet"]:
            st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
            st.markdown("---")
    else:
        st.info("No active LPPC SIGMET found.")

with col2:
    st.subheader("GAMET (LPPC)")
    if data["gamet"]:
        for g in data["gamet"]:
            st.markdown(f'<div class="monos">{g}</div>', unsafe_allow_html=True)
            st.markdown("---")
    else:
        st.info("No LPPC GAMET found.")


