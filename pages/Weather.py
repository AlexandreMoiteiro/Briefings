~import streamlit as st
import requests
import datetime

st.set_page_config(page_title="Live Weather", layout="wide")

# ===== CONFIG =====
CHECKWX_API_KEY = st.secrets["CHECKWX_API_KEY"]
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

# ===== FUNCTIONS =====
def fetch_metar(icao):
    url = f"https://api.checkwx.com/metar/{icao}/decoded"
    headers = {"X-API-Key": CHECKWX_API_KEY}
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code == 200 and r.json().get("data"):
        return r.json()["data"][0]
    return None

def fetch_taf(icao):
    url = f"https://api.checkwx.com/taf/{icao}/decoded"
    headers = {"X-API-Key": CHECKWX_API_KEY}
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code == 200 and r.json().get("data"):
        return r.json()["data"][0]
    return None

def fetch_sigmet_lppc():
    """Fetch LPPC FIR SIGMET from Aviation Weather Center (AWC) International API"""
    url = "https://aviationweather.gov/api/data/isigmet"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            sigmets = r.json()
            return [
                s for s in sigmets
                if "fir" in s and s["fir"].upper() == "LPPC"
            ]
    except Exception:
        return []
    return []

# ===== UI =====
st.title("🌍 Live Weather - METAR / TAF / SIGMET LPPC")

# ICAO input
icao_list = st.text_input("Enter ICAO codes separated by commas:", value=",".join(DEFAULT_ICAOS))
icaos = [code.strip().upper() for code in icao_list.split(",") if code.strip()]

col1, col2, col3 = st.columns(3)

# METAR
with col1:
    st.subheader("METAR")
    for icao in icaos:
        metar = fetch_metar(icao)
        if metar:
            st.markdown(f"**{icao}** - {metar.get('raw_text', 'N/A')}")
        else:
            st.warning(f"{icao}: No METAR available")

# TAF
with col2:
    st.subheader("TAF")
    for icao in icaos:
        taf = fetch_taf(icao)
        if taf:
            st.markdown(f"**{icao}** - {taf.get('raw_text', 'N/A')}")
        else:
            st.warning(f"{icao}: No TAF available")

# SIGMET LPPC
with col3:
    st.subheader("SIGMET LPPC")
    sigmets = fetch_sigmet_lppc()
    if sigmets:
        for s in sigmets:
            st.markdown(f"- **{s.get('hazard', 'Unknown')}**: {s.get('validtimefrom')} → {s.get('validtimeto')}")
            st.code(s.get("rawtext", ""))
    else:
        st.info("No SIGMETs currently active for LPPC FIR.")

# Timestamp
st.caption(f"Last updated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%SZ')} UTC")



