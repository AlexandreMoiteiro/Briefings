import streamlit as st
from pathlib import Path
from PIL import Image
import io

st.set_page_config(page_title="Pre-Flight Briefing Generator", layout="wide")

# Custom CSS for style (similar to your mass-balance app)
def inject_css():
    st.markdown("""
    <style>
    html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', Arial, sans-serif; }
    .stProgress > div > div > div > div { background-color: #4e8af4; }
    .section-title { font-size: 1.22rem; font-weight: 700; margin-bottom: 12px; }
    .footer {margin-top:32px;font-size:0.96rem;color:var(--text-color,#a0a8b6);text-align:center;}
    </style>
    """, unsafe_allow_html=True)

inject_css()

if "step" not in st.session_state:
    st.session_state.step = 1

st.markdown('<div class="section-title">Pre-Flight Briefing Generator</div>', unsafe_allow_html=True)
st.progress((st.session_state.step-1)/4)

# ---- STEP 1: Mission Overview ----
if st.session_state.step == 1:
    st.subheader("1. Mission Overview")
    mission_pdf = st.file_uploader("Upload Mission Objectives PDF (optional)", type=["pdf"])
    callsign = st.text_input("Callsign")
    time_slot = st.text_input("Designated Time Slot (e.g., 14:00-16:00Z)")
    if st.button("Next"):
        if not callsign or not time_slot:
            st.warning("Please fill in the required fields.")
        else:
            st.session_state.mission_pdf = mission_pdf
            st.session_state.callsign = callsign
            st.session_state.time_slot = time_slot
            st.session_state.step += 1

# ---- STEP 2: Weather Briefing ----
elif st.session_state.step == 2:
    st.subheader("2. Weather Briefing")
    st.markdown("Upload your **surface pressure chart** and/or **significant weather chart** (PDF, PNG, JPG).")
    weather_files = st.file_uploader("Upload Weather Charts", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
    st.markdown("Select observed weather features (for the area of your flight):")
    features = st.multiselect(
        "What do you observe?",
        ["Cold front", "Warm front", "Occluded front", "High pressure", "Low pressure", "Showers expected", "Fog/RVR issues", "Turbulence", "Thunderstorms", "None of the above"],
    )
    user_obs = st.text_area("Any other relevant weather observations?", max_chars=500)
    gpt_option = st.checkbox("Use AI to summarize/explain the weather charts (recommended)")
    if st.button("Next"):
        st.session_state.weather_files = weather_files
        st.session_state.features = features
        st.session_state.user_obs = user_obs
        st.session_state.gpt_option = gpt_option
        st.session_state.step += 1
    if st.button("Back"):
        st.session_state.step -= 1

# ---- STEP 3: NOTAMs ----
elif st.session_state.step == 3:
    st.subheader("3. NOTAM Information")
    st.markdown("Paste or summarize the **relevant NOTAMs** for your route/area below:")
    notam_text = st.text_area("NOTAM Summary", max_chars=1000)
    if st.button("Next"):
        st.session_state.notam_text = notam_text
        st.session_state.step += 1
    if st.button("Back"):
        st.session_state.step -= 1

# ---- STEP 4: Mission Details / Clarifications ----
elif st.session_state.step == 4:
    st.subheader("4. Mission-specific Details and Doubts")
    mission_details = st.text_area(
        "Clarify mission-specific details (e.g., Nav Log, doubts, instructions, alternate plans)",
        max_chars=1000
    )
    if st.button("Next"):
        st.session_state.mission_details = mission_details
        st.session_state.step += 1
    if st.button("Back"):
        st.session_state.step -= 1

# ---- STEP 5: Review & Generate PDF ----
elif st.session_state.step == 5:
    st.subheader("5. Review & Generate PDF")
    st.write("**Callsign:**", st.session_state.callsign)
    st.write("**Time Slot:**", st.session_state.time_slot)
    st.write("**Mission Objectives PDF:**", "Uploaded" if st.session_state.mission_pdf else "None")
    st.write("**Weather Files:**", [f.name for f in st.session_state.weather_files] if st.session_state.weather_files else "None")
    st.write("**Selected Weather Features:**", ", ".join(st.session_state.features))
    st.write("**Weather Observations:**", st.session_state.user_obs or "None")
    st.write("**NOTAMs:**", st.session_state.notam_text or "None")
    st.write("**Mission Details:**", st.session_state.mission_details or "None")

    # Simulated AI summary (replace with real GPT call in production)
    if st.session_state.gpt_option:
        st.markdown("#### AI Weather Interpretation")
        ai_obs = (
            "Example: A cold front approaching the west coast will likely bring increasing CB clouds and showers. "
            "Expect lowering pressure and gusty winds. Plan for alternate routes if convective weather worsens."
        )
        st.info(ai_obs)

    st.markdown("You can now generate your PDF briefing package including your uploads and AI summaries (feature in progress).")

    # PDF generation placeholder
    if st.button("Finish / Restart"):
        for key in [
            "step", "mission_pdf", "callsign", "time_slot", "weather_files", "features",
            "user_obs", "gpt_option", "notam_text", "mission_details"
        ]:
            if key in st.session_state: del st.session_state[key]
        st.session_state.step = 1

st.markdown('<div class="footer">Site developed for Sevenair Academy Pre-Flight Briefings. All rights reserved.</div>', unsafe_allow_html=True)

