import streamlit as st
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://brief-ng.ipma.pt/#login"
SIGMET_URL = "https://brief-ng.ipma.pt/#showSIGMET"

USER = st.secrets.get("IPMA_USER", "")
PASS = st.secrets.get("IPMA_PASS", "")

st.set_page_config(page_title="SIGMET/AIRMET/GAMET", layout="centered")
st.title("SIGMET / AIRMET / GAMET (IPMA)")

if not USER or not PASS:
    st.error("Configure IPMA_USER and IPMA_PASS in Streamlit secrets.")
    st.stop()

@st.cache_data(ttl=300)
def fetch_sigmet_text():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context()
        page = context.new_page()

        # Login
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.fill('input[name="username"]', USER)
        page.fill('input[name="password"]', PASS)
        page.click('button:has-text("Login"), input[type="submit"]')
        page.wait_for_timeout(1500)

        # Fetch SIGMET
        page.goto(SIGMET_URL, wait_until="domcontentloaded")
        try:
            text = page.locator("pre").inner_text().strip()
        except Exception:
            text = page.inner_text().strip()

        browser.close()
        return text

if st.button("Fetch now"):
    with st.spinner("Fetching…"):
        txt = fetch_sigmet_text()
    st.success("Done.")
    st.code(txt)

