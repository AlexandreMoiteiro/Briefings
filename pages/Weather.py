# pages/IPMA_Test.py
import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re
import time

SHOW_SIGMET_URL = "https://brief-ng.ipma.pt/?page=showSIGMET"

IPMA_USER = st.secrets.get("IPMA_USER", "")
IPMA_PASS = st.secrets.get("IPMA_PASS", "")

st.title("IPMA SIGMET/GAMET Test")

st.write("Diagnostics", {
    "has_user": bool(IPMA_USER),
    "has_pass": bool(IPMA_PASS),
})
st.write("SHOW_SIGMET_URL =", SHOW_SIGMET_URL)


def parse_form(html):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if not form:
        return SHOW_SIGMET_URL, "post", {}

    action = (form.get("action") or "").strip()
    method = (form.get("method") or "post").lower()
    if not action or action.startswith("#") or "javascript:" in action.lower():
        action = SHOW_SIGMET_URL
    action_url = urljoin(SHOW_SIGMET_URL, action)

    payload = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        val = inp.get("value") or ""
        if itype in ["submit", "button", "image", "file"]:
            continue
        payload[name] = val
    return action_url, method, payload


def login_and_fetch_sigmet_gamet(user, pwd):
    s = requests.Session()

    # 1) Load initial page
    r0 = s.get(SHOW_SIGMET_URL, timeout=12)
    r0.raise_for_status()
    action_url, method, payload = parse_form(r0.text)

    with st.expander("Form detection"):
        st.write({"action_url": action_url, "method": method, "payload_keys": list(payload.keys())})

    # Fill creds
    payload.update({
        "username": user,
        "password": pwd,
        "usr": user,
        "pwd": pwd
    })

    # 2) Submit login
    if method == "get":
        r1 = s.get(action_url, params=payload, timeout=12)
    else:
        r1 = s.post(action_url, data=payload, timeout=12)
    r1.raise_for_status()

    with st.expander("Login outcome"):
        st.write({"cookies": s.cookies.get_dict()})
        st.code(r1.text[:500])

    # 3) Fetch SIGMET page after login
    r2 = s.get(SHOW_SIGMET_URL, timeout=12)
    r2.raise_for_status()
    soup = BeautifulSoup(r2.text, "html.parser")
    content = soup.select_one("#divContent")
    if not content:
        st.warning("No #divContent found.")
        return [], []

    for br in content.find_all("br"):
        br.replace_with("\n")
    text = re.sub(r"[ \t]+\n", "\n", content.get_text("\n")).strip()

    gamet = [m.group(0).strip() for m in re.finditer(r"(?ms)^LPPC\s+GAMET.*?(?:\n\n|$)", text)]
    sigmet = [m.group(0).strip() for m in re.finditer(r"(?ms)^SIGMET.*?(?:\n\n|$)", text)]

    return sigmet, gamet


if IPMA_USER and IPMA_PASS:
    sigmet, gamet = login_and_fetch_sigmet_gamet(IPMA_USER, IPMA_PASS)
    st.subheader("SIGMET (LPPC)")
    st.write(sigmet or "No active LPPC SIGMET found.")

    st.subheader("GAMET (LPPC)")
    st.write(gamet or "No LPPC GAMET found.")
else:
    st.error("Missing IPMA_USER and/or IPMA_PASS in secrets.toml")




