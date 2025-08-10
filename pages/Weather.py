import streamlit as st
import requests
import time
import re
from bs4 import BeautifulSoup

st.set_page_config(page_title="Weather Test", layout="wide", initial_sidebar_state="collapsed")

# ---------- Funções ----------
def ipma_headers():
    """Cabeçalhos para o pedido ao IPMA."""
    h = {"User-Agent": "Mozilla/5.0"}
    cookie = st.secrets.get("IPMA_COOKIE", "")
    if cookie:
        h["Cookie"] = cookie
    return h

def fetch_sigmet_gamet():
    """Vai buscar SIGMET e GAMET do IPMA."""
    url = st.secrets.get("IPMA_SHOWSIGMET_URL", "")
    if not url:
        st.error("IPMA_SHOWSIGMET_URL não definido no secrets.toml")
        return {"sigmet": [], "gamet": []}

    # Cache-buster para evitar resultados antigos
    test_url = url + ("&" if "?" in url else "?") + f"_ts={int(time.time())}"
    try:
        r = requests.get(test_url, headers=ipma_headers(), timeout=12)
        r.raise_for_status()
        html = r.text

        # Verificar se estamos autenticados
        if "Logged in as" not in html:
            st.warning("⚠️ Não parece estar autenticado. Verifica a IPMA_COOKIE.")
        
        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one("#divContent")
        if not content:
            st.error("❌ Não encontrei a divContent na página.")
            return {"sigmet": [], "gamet": []}

        # Trocar <br> por quebras de linha
        for br in content.find_all("br"):
            br.replace_with("\n")
        text = re.sub(r"[ \t]+\n", "\n", content.get_text("\n")).strip()

        # Procurar GAMET
        gamet_blocks = [m.group(0).strip() for m in re.finditer(r"(?ms)^LPPC\s+GAMET.*?(?:\n\n|$)", text)]
        if not gamet_blocks and "GAMET" in text:
            gamet_blocks = [line for line in text.split("\n") if "GAMET" in line]

        # Procurar SIGMET
        sigmet_blocks = [m.group(0).strip() for m in re.finditer(r"(?ms)SIGMET.*?(?:\n\n|$)", text)]

        return {"sigmet": sigmet_blocks, "gamet": gamet_blocks}

    except Exception as e:
        st.error(f"Erro ao buscar SIGMET/GAMET: {e}")
        return {"sigmet": [], "gamet": []}

# ---------- Interface ----------
st.title("SIGMET / GAMET - IPMA Test")

data = fetch_sigmet_gamet()

st.subheader("SIGMET (LPPC)")
if data["sigmet"]:
    for s in data["sigmet"]:
        st.code(s)
else:
    st.info("Nenhum SIGMET encontrado.")

st.subheader("GAMET (LPPC)")
if data["gamet"]:
    for g in data["gamet"]:
        st.code(g)
else:
    st.info("Nenhum GAMET encontrado.")

