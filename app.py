import streamlit as st
import requests
import os
from textwrap import shorten
from datetime import datetime

st.set_page_config("Flight Briefing Dashboard", layout="wide")
st.title("✈️ Flight Briefing Dashboard")

CHECKWX_KEY = st.secrets.get("CHECKWX_API_KEY") or os.getenv("CHECKWX_API_KEY")
BASE = "https://api.checkwx.com/"

DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

def fetch_checkwx(endpoint: str):
    headers = {"X-API-Key": CHECKWX_KEY} if CHECKWX_KEY else {}
    try:
        r = requests.get(BASE + endpoint, headers=headers, timeout=8)
        r.raise_for_status()
        j = r.json()
        return j.get("data", [])
    except Exception as e:
        return [f"Error fetching: {e}"]

st.markdown(
    """
    <style>
    .card { background: #ffffff; border-radius:14px; padding:14px; box-shadow: 0 6px 18px rgba(0,0,0,0.08); }
    .card h3 { margin:0; }
    .small { color:#666; font-size:12px; }
    </style>
    """, unsafe_allow_html=True
)

st.subheader("Latest METAR & TAF (defaults)")

cols = st.columns(len(DEFAULT_ICAOS))
for col, icao in zip(cols, DEFAULT_ICAOS):
    with col:
        st.markdown(f'<div class="card">', unsafe_allow_html=True)
        st.markdown(f"**{icao}**  ·  <span class='small'>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</span>", unsafe_allow_html=True)
        metar = fetch_checkwx(f"metar/{icao}")
        metar_text = metar[0] if isinstance(metar, list) and metar else ""
        st.markdown(f"**METAR:**  \n`{shorten(metar_text, width=220)}`")
        taf = fetch_checkwx(f"taf/{icao}")
        taf_text = taf[0] if isinstance(taf, list) and taf else ""
        st.markdown(f"**TAF:**  \n`{shorten(taf_text, width=220)}`")
        st.markdown(f"</div>", unsafe_allow_html=True)

st.write("")
st.markdown("### Quick actions")
cols2 = st.columns([1,1,1])
with cols2[0]:
    if st.button("Enter Full Briefing"):
        st.experimental_set_query_params(_page="briefing")
        st.experimental_rerun()
with cols2[1]:
    st.markdown("[Open Weather Live page →](./pages/Weather.py)", unsafe_allow_html=True)
with cols2[2]:
    st.write("")

st.info("Default airports: LPPT, LPBJ, LEBZ. Click 'Enter Full Briefing' to generate PDFs or to analyze charts.")


