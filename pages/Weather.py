import streamlit as st
import requests, time, re, json
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE = "https://brief-ng.ipma.pt/"
SHOW_SIGMET = "https://brief-ng.ipma.pt/?page=showSIGMET"

USER = st.secrets.get("IPMA_USER", "")
PASS = st.secrets.get("IPMA_PASS", "")

st.set_page_config(page_title="IPMA Test", layout="wide", initial_sidebar_state="collapsed")
st.title("IPMA Auto-Login Test (SIGMET/GAMET)")

st.write("Diagnostics", {"has_user": bool(USER), "has_pass": bool(PASS)})
st.code(f"SHOW_SIGMET = {SHOW_SIGMET}")

HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
    "Referer": BASE,
}

def looks_logged_html(html: str) -> bool:
    h = html.lower()
    return ("logged in as" in h) or ("logout" in h) or ("sigmet/airmet/gamet" in h) or ("saved flights" in h)

def fetch_sigmet_gamet_with_session(s: requests.Session):
    r2 = s.get(SHOW_SIGMET, timeout=12, headers=HDRS)
    r2.raise_for_status()
    soup = BeautifulSoup(r2.text, "html.parser")
    content = soup.select_one("#divContent")
    if not content:
        st.warning("No #divContent found after login.")
        st.code(r2.text[:800], language="html")
        return [], []
    for br in content.find_all("br"):
        br.replace_with("\n")
    text = re.sub(r"[ \t]+\n", "\n", content.get_text("\n")).strip()
    st.subheader("Extracted text sample")
    st.code(text[:800])

    gamet = [m.group(0).strip() for m in re.finditer(r"(?ms)^LPPC\s+GAMET.*?(?:\n\n|$)", text)]
    sigmet = [m.group(0).strip() for m in re.finditer(r"(?ms)^(?:LPPC\s+)?SIGMET.*?(?:\n\n|$)", text)]
    return sigmet, gamet

def try_candidates(user: str, pwd: str):
    s = requests.Session()
    s.headers.update(HDRS)

    # 0) Warm-up: load page (may set lang/session)
    r0 = s.get(SHOW_SIGMET, timeout=12, allow_redirects=True)
    r0.raise_for_status()

    # 1) Likely endpoints & payloads (both classic form and JSON/XHR)
    endpoints = [
        SHOW_SIGMET,                        # sometimes posting to same URL works
        urljoin(SHOW_SIGMET, "?page=doLogin"),
        urljoin(SHOW_SIGMET, "?page=login"),
        urljoin(SHOW_SIGMET, "/login"),
        urljoin(SHOW_SIGMET, "login"),
        urljoin(SHOW_SIGMET, "ajax/login.php"),
        urljoin(SHOW_SIGMET, "js/user.js"),   # some apps post here
    ]
    fields = [
        ("username","password"),
        ("user","password"),
        ("usr","pwd"),
    ]

    attempts = []
    for ep in endpoints:
        for ukey, pkey in fields:
            # Form POST
            attempts.append(("POST", ep, {"data": {ukey: user, pkey: pwd}, "headers": {}}))
            # GET (querystring)
            attempts.append(("GET", ep, {"params": {ukey: user, pkey: pwd}, "headers": {}}))
            # JSON XHR
            attempts.append(("POST", ep, {"json": {ukey: user, pkey: pwd}, "headers": {"X-Requested-With":"XMLHttpRequest", "Content-Type":"application/json"}}))

    # 2) Try them
    for method, url, opts in attempts:
        try:
            if method == "GET":
                r = s.get(url, timeout=12, allow_redirects=True, **opts)
            else:
                r = s.post(url, timeout=12, allow_redirects=True, **opts)
            # If JSON came back simply note it
            ctype = r.headers.get("content-type","")
            ok = False
            if "text/html" in ctype or "<html" in r.text[:200].lower():
                ok = looks_logged_html(r.text)
            else:
                # Some endpoints return JSON like {"ok":true} then you must GET the page
                try:
                    js = r.json()
                    ok = bool(js)  # if any response, try to proceed
                except Exception:
                    ok = False

            st.write({"attempt": f"{method} {url}", "status": r.status_code, "ctype": ctype, "logged_html_heuristic": ok})
            if ok:
                # Confirm by visiting target page
                r_check = s.get(SHOW_SIGMET, timeout=12)
                if looks_logged_html(r_check.text):
                    st.success(f"Logged in via: {method} {url}")
                    st.code(r_check.text[:600], language="html")
                    return s
        except Exception as e:
            st.write({"attempt": f"{method} {url}", "error": str(e)})
            continue

    # 3) Last-resort: force hash login view then reload
    r1b = s.get(SHOW_SIGMET + "#showLogin", timeout=12, allow_redirects=True)
    st.write({"force_showLogin_status": r1b.status_code})
    r1c = s.get(SHOW_SIGMET, timeout=12, allow_redirects=True)
    st.write({"after_force_status": r1c.status_code, "looks_logged": looks_logged_html(r1c.text)})
    if looks_logged_html(r1c.text):
        return s
    return None

if not USER or not PASS:
    st.error("Add IPMA_USER and IPMA_PASS to .streamlit/secrets.toml")
    st.stop()

session = try_candidates(USER, PASS)

if session:
    sig, gam = fetch_sigmet_gamet_with_session(session)
    st.subheader("SIGMET (LPPC)")
    st.write(sig or "No active LPPC SIGMET found.")
    st.subheader("GAMET (LPPC)")
    st.write(gam or "No LPPC GAMET found.")
else:
    st.error("Login did not succeed. Next step: capture the real login request (URL + fields).")
    with st.expander("How to capture the real login request"):
        st.markdown("""
1. Open Firefox → **Network** tab → go to `?page=showSIGMET`.
2. Click the **Login** button on the page (so the site sends the real request).
3. In Network, click the request that happened **when you clicked Login** (usually **XHR**).
4. Copy:
   - **Request URL**
   - **Method (GET/POST)**
   - **Request Headers** (just `X-Requested-With` if present)
   - **Request Body / Form Data** (the **field names** for user & password, e.g. `usr` and `pwd`)
5. Paste those here and I’ll wire them in exactly.
""")





