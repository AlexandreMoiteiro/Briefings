import streamlit as st
import requests
from urllib.parse import parse_qs
import os

st.set_page_config(page_title="Weather Live", layout="wide")
st.title("Weather Live – METAR, TAF & SIGMET")

API_KEY = os.getenv("CHECKWX_API_KEY")
BASE_URL = "https://api.checkwx.com/"

# Função para obter dados
def fetch_data(endpoint):
    headers = {"X-API-Key": API_KEY}
    r = requests.get(BASE_URL + endpoint, headers=headers)
    if r.status_code == 200:
        data = r.json()
        if "data" in data:
            return data["data"]
    return ["No data available"]

# Lê parâmetros da URL
query_params = st.query_params
icaos = []
if "icao" in query_params:
    icaos = query_params["icao"].split(",")
else:
    icaos = ["LPPT", "LPBJ", "LEBZ"]

# Input extra para adicionar ICAOs
extra_icao = st.text_input("Adicionar ICAO(s)", "")
if extra_icao:
    icaos.extend([i.strip().upper() for i in extra_icao.split(",")])

# Mostra dados
for icao in icaos:
    st.subheader(f"📍 {icao}")

    metar = fetch_data(f"metar/{icao}")
    taf = fetch_data(f"taf/{icao}")
    sigmet = fetch_data(f"sigmet/{icao}")

    with st.expander("METAR"):
        st.write("\n".join(metar))
    with st.expander("TAF"):
        st.write("\n".join(taf))
    with st.expander("SIGMET"):
        st.write("\n".join(sigmet))

