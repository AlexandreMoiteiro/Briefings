import streamlit as st
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://brief-ng.ipma.pt/#login"
SIGMET_URL = "https://brief-ng.ipma.pt/#showSIGMET"

USER = st.secrets.get("IPMA_USER", "")
PASS = st.secrets.get("IPMA_PASS", "")

st.set_page_config(page_title="SIGMET/AIRMET/GAMET Fetcher", layout="centered")
st.title("SIGMET / AIRMET / GAMET (IPMA)")

if not USER or not PASS:
    st.error("Configure IPMA_USER e IPMA_PASS em secrets.")
    st.stop()

@st.cache_data(ttl=300)  # evita repetir login por 5 min
def fetch_sigmet_text():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # 1) Login
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        # Ajuste os seletores se necessário:
        page.fill('input[name="username"]', USER)
        page.fill('input[name="password"]', PASS)
        # botão de login (tenta variantes comuns)
        page.click('button:has-text("Login"), input[type="submit"]')
        page.wait_for_timeout(1200)

        # 2) Página SIGMET
        page.goto(SIGMET_URL, wait_until="domcontentloaded")

        # 3) Capturar texto (muitas vezes está em <pre>)
        text = ""
        try:
            text = page.locator("pre").inner_text().strip()
        except Exception:
            # fallback: pega o texto inteiro visível
            text = page.inner_text().strip()

        browser.close()
        return text

col1, col2 = st.columns([1,1])
with col1:
    if st.button("Fetch now"):
        with st.spinner("Fetching…"):
            txt = fetch_sigmet_text()
        st.success("Done.")
        st.code(txt)

with col2:
    st.write("Dica: o resultado fica em cache 5 min (TTL). Clique novamente para forçar uma nova busca após isso.")
