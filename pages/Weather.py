import streamlit as st
import requests
import os
from urllib.parse import unquote_plus

st.set_page_config("Weather Live", layout="wide")
st.title("Weather Live — METAR / TAF / SIGMET")

CHECKWX_KEY = st.secrets.get("CHECKWX_API_KEY") or os.getenv("CHECKWX_API_KEY")
BASE = "https://api.checkwx.com/"

def fetch(endpoint: str):
    headers = {"X-API-Key": CHECKWX_KEY} if CHECKWX_KEY else {}
    try:
        r = requests.get(BASE + endpoint, headers=headers, timeout=10)
        r.raise_for_status()
        j = r.json()
        return j.get("data", [])
    except Exception as e:
        return [f"Error: {e}"]

qp = st.experimental_get_query_params()
if "icao" in qp:
    raw = qp.get("icao", [""])[0]
    raw = unquote_plus(raw)
    icaos = [x.strip().upper() for x in raw.split(",") if x.strip()]
else:
    icaos = ["LPPT","LPBJ","LEBZ"]

st.subheader("Airports shown:")
st.write(", ".join(icaos))

more = st.text_input("Add ICAO(s) (comma separated)","")
if st.button("Update list"):
    if more.strip():
        extra = [x.strip().upper() for x in more.split(",") if x.strip()]
        icaos = list(dict.fromkeys(icaos + extra))
        st.experimental_set_query_params(icao=",".join(icaos))
        st.experimental_rerun()

for icao in icaos:
    st.markdown(f"---\n### {icao}")
    metar = fetch(f"metar/{icao}")
    taf = fetch(f"taf/{icao}")
    prefix = icao[:2].upper()
    FIR = "LPPC"
    if prefix == "LP" and icao in {"LPAZ","LPLA","LPPD","LPPI","LPFL","LPHR","LPGR","LPSJ"}:
        FIR = "LPPO"
    elif prefix == "LP":
        FIR = "LPPC"
    sigmet = fetch(f"sigmet/{FIR}/decoded")

    with st.expander("METAR"):
        st.write("\n".join(metar) if metar else "No METAR")
    with st.expander("TAF"):
        st.write("\n".join(taf) if taf else "No TAF")
    with st.expander(f"SIGMET (decoded for {FIR})"):
        if sigmet:
            for s in sigmet:
                if isinstance(s, dict):
                    st.write(s.get("raw") or s.get("report") or s)
                else:
                    st.write(s)
        else:
            st.write("No SIGMET")


