import streamlit as st
import requests
import datetime as dt
from zoneinfo import ZoneInfo

# Page setup FIRST — ensures sidebar default collapsed
st.set_page_config(
    page_title="Weather (Live)",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Force sidebar width = 0 when collapsed, normal when open
st.markdown("""
<style>
[data-testid="stSidebar"][aria-expanded="false"] { 
  min-width: 0 !important; max-width: 0 !important; width: 0 !important;
}
[data-testid="stSidebar"][aria-expanded="true"] { 
  min-width: 300px !important; max-width: 300px !important; width: 300px !important;
  box-shadow: 0 1px 4px rgba(0,0,0,.08);
}
</style>
""", unsafe_allow_html=True)

st.title("Current Weather")

CHECKWX_API_KEY = st.secrets["CHECKWX_API_KEY"]

# --- Helper to convert Zulu time to Portugal local ---
def zulu_to_portugal(zulu_str: str) -> str:
    try:
        z_dt = dt.datetime.strptime(zulu_str, "%Y-%m-%d %H:%MZ")
        z_dt = z_dt.replace(tzinfo=dt.timezone.utc)
        local_dt = z_dt.astimezone(ZoneInfo("Europe/Lisbon"))
        return local_dt.strftime("%H:%M")
    except Exception:
        return ""

# --- Fetch METAR ---
def get_metar(icao):
    url = f"https://api.checkwx.com/metar/{icao}/decoded"
    headers = {"X-API-Key": CHECKWX_API_KEY}
    r = requests.get(url, headers=headers)
    data = r.json()
    if not data.get("data"):
        return None
    metar = data["data"][0]
    obs_time = dt.datetime.strptime(metar["observed"], "%Y-%m-%dT%H:%M:%S+00:00")
    obs_str = obs_time.strftime("%Y-%m-%d %H:%MZ")
    local_str = obs_time.astimezone(ZoneInfo("Europe/Lisbon")).strftime("%H:%M")
    return {
        "raw": metar["raw_text"],
        "obs": f"{obs_str} ({local_str} Portugal)"
    }

# --- Fetch TAF ---
def get_taf(icao):
    url = f"https://api.checkwx.com/taf/{icao}/decoded"
    headers = {"X-API-Key": CHECKWX_API_KEY}
    r = requests.get(url, headers=headers)
    data = r.json()
    if not data.get("data"):
        return None
    taf = data["data"][0]
    issue_time = dt.datetime.strptime(taf["timestamp"]["issued"], "%Y-%m-%dT%H:%M:%S+00:00")
    issue_str = issue_time.strftime("%Y-%m-%d %H:%MZ")
    local_str = issue_time.astimezone(ZoneInfo("Europe/Lisbon")).strftime("%H:%M")
    return {
        "raw": taf["raw_text"],
        "issued": f"{issue_str} ({local_str} Portugal)"
    }

# --- Fetch SIGMET for LPPC FIR ---
def get_sigmet_lppc():
    url = "https://aviationweather.gov/api/data/isigmet"
    params = {"format": "json", "fir": "LPPC"}
    r = requests.get(url, params=params)
    data = r.json()
    sigmets = []
    for sig in data:
        if "LPPC" in sig.get("fir", ""):
            valid_from = sig.get("validTimeFrom", "").replace("T", " ").replace("Z", "Z")
            valid_to = sig.get("validTimeTo", "").replace("T", " ").replace("Z", "Z")
            sigmets.append({
                "raw": sig.get("rawSigmet", ""),
                "valid": f"{valid_from} - {valid_to}"
            })
    return sigmets

# --- Display defaults ---
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

cols = st.columns(len(DEFAULT_ICAOS))
for idx, icao in enumerate(DEFAULT_ICAOS):
    metar = get_metar(icao)
    taf = get_taf(icao)
    with cols[idx]:
        st.subheader(icao)
        if metar:
            st.text(f"METAR ({metar['obs']}):")
            st.code(metar['raw'], language="none")
        if taf:
            st.text(f"TAF ({taf['issued']}):")
            st.code(taf['raw'], language="none")

st.divider()
st.subheader("SIGMETs - FIR LPPC")
sigmets = get_sigmet_lppc()
if sigmets:
    for sig in sigmets:
        st.text(f"Valid: {sig['valid']}")
        st.code(sig['raw'], language="none")
else:
    st.info("No active SIGMETs for LPPC FIR.")






