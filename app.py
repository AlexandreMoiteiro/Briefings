import streamlit as st
from PIL import Image
import openai
import io
import base64
from streamlit_cropper import st_cropper
from fpdf import FPDF
import fitz
import datetime

openai.api_key = st.secrets["OPENAI_API_KEY"]

def downscale_image(img, width=900):
    if img.width > width:
        ratio = width / img.width
        new_size = (width, int(img.height * ratio))
        img = img.resize(new_size)
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG", optimize=True)
    img_bytes.seek(0)
    return img, img_bytes

def ai_chart_analysis(img_base64, chart_type, user_area_desc):
    if chart_type == "SPC":
        sys_prompt = (
            "You are an aviation meteorology instructor. Analyze the uploaded surface pressure chart (SPC). "
            "Provide a concise, structured report for a flight briefing PDF, including:\n"
            "- Synoptic situation and pressure systems\n"
            "- Location and type of fronts\n"
            "- Wind direction/speed and general cloud cover\n"
            "- Expected weather and flight category (VFR/IFR)\n"
            "Focus only on the selected (cropped) area or described region."
        )
    else:
        sys_prompt = (
            "You are an aviation meteorology instructor. Analyze the uploaded significant weather chart (SIGWX). "
            "Give a concise, structured report for a flight briefing PDF, including:\n"
            "- Cloud types/amounts, altitudes\n"
            "- Turbulence (areas, severity)\n"
            "- Significant weather phenomena (CBs, icing, mountain waves, etc.)\n"
            "- Freezing levels, visibility, and flight hazards\n"
            "Only describe and summarize the area specified by the user (default: Portugal)."
        )
    user_prompt = f"Please focus your analysis on: {user_area_desc.strip()}" if user_area_desc.strip() else "Please provide the briefing for Portugal."
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                ]
            }
        ],
        max_tokens=650,
        temperature=0.5
    )
    return response.choices[0].message.content

class BriefingPDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            pass
        else:
            self.set_font('Arial', 'B', 15)
            self.cell(0, 10, "Preflight Briefing Report", align='C', ln=1)
            self.ln(2)
    def footer(self):
        self.set_y(-13)
        self.set_font('Arial', 'I', 7)
        self.set_text_color(100, 100, 100)
        self.cell(0, 7, "This briefing is generated automatically for assistance. Verify all information with official sources before flight. | Developed by Alexandre Moiteiro", align='C')
    def cover_page(self, mission, pilot, aircraft, date, callsign):
        self.add_page()
        self.set_fill_color(34,34,34)
        self.rect(0, 0, 210, 45, 'F')
        self.set_font("Arial", 'B', 22)
        self.set_text_color(255,255,255)
        self.set_xy(10,12)
        self.cell(0, 15, "Preflight Briefing Package", ln=True, align='L')
        self.set_font("Arial", '', 13)
        self.cell(0, 9, f"Mission: {mission}", ln=True)
        self.cell(0, 9, f"Pilot: {pilot}", ln=True)
        self.cell(0, 9, f"Aircraft: {aircraft}", ln=True)
        self.cell(0, 9, f"Callsign: {callsign}", ln=True)
        self.cell(0, 9, f"Date: {date}", ln=True)
        self.ln(15)
        self.set_text_color(0,0,0)
        self.set_font("Arial", 'I', 13)
        self.multi_cell(0, 8, "This report contains the latest analysis of weather charts for the planned mission. Use official sources for flight decision-making. Briefing generated automatically for preflight assistance.")
    def chart_section(self, title, img_bytes, ai_text, user_desc=""):
        self.add_page()
        self.set_font("Arial", 'B', 17)
        self.cell(0, 10, title, ln=True)
        self.ln(2)
        if user_desc.strip():
            self.set_font("Arial", 'I', 11)
            self.set_text_color(40,40,40)
            self.multi_cell(0, 8, f"User area/focus: {user_desc.strip()}")
            self.ln(2)
        self.set_text_color(0,0,0)
        self.set_font("Arial", '', 12)
        chart_img_path = "tmp_chart.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.image(chart_img_path, x=25, w=160)
        self.ln(4)
        self.set_font("Arial", '', 11)
        self.multi_cell(0, 8, ai_text)
        self.ln(1)

st.title("Preflight Briefing Package (SPC & SIGWX)")
st.caption("Fill mission info, crop the SPC, set SIGWX area, then generate your report.")

# Mission info fields
with st.expander("1. Mission Information", expanded=True):
    mission = st.text_input("Mission (overview/route/objective)", "")
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")
    date = st.date_input("Date", datetime.date.today())

# SPC (Surface Pressure Chart) cropping
with st.expander("2. Surface Pressure Chart (SPC)", expanded=True):
    spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
    if "cropped_spc_bytes" not in st.session_state:
        st.session_state["cropped_spc_bytes"] = None
        st.session_state["spc_desc"] = ""
    if spc_file:
        if spc_file.type == "application/pdf":
            pdf_bytes = spc_file.read()
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = pdf_doc.load_page(0)
            pix = page.get_pixmap()
            spc_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
        else:
            spc_img = Image.open(spc_file).convert("RGB").copy()
        st.markdown("**Crop the SPC chart and click 'Save SPC Crop' to confirm.**")
        cropped_spc = st_cropper(
            spc_img,
            aspect_ratio=None,
            box_color='red',
            return_type='image',
            realtime_update=True,
            key="spc_crop"
        )
        st.image(cropped_spc, caption="SPC: Cropped Area Preview")
        spc_desc = st.text_input("SPC: Area/focus for AI (optional)", value=st.session_state["spc_desc"], key="spcdesc")
        if st.button("Save SPC Crop"):
            cropped_spc, spc_img_bytes = downscale_image(cropped_spc)
            st.session_state["cropped_spc_bytes"] = spc_img_bytes
            st.session_state["spc_desc"] = spc_desc
            st.success("SPC crop saved! You may now close this section.")

# SIGWX (Significant Weather Chart) - No cropping, just upload and set area/focus
with st.expander("3. Significant Weather Chart (SIGWX)", expanded=True):
    sigwx_file = st.file_uploader("Upload SIGWX/SWC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="sigwx")
    if "sigwx_img_bytes" not in st.session_state:
        st.session_state["sigwx_img_bytes"] = None
        st.session_state["sigwx_desc"] = "Portugal"
    if sigwx_file:
        if sigwx_file.type == "application/pdf":
            pdf_bytes = sigwx_file.read()
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = pdf_doc.load_page(0)
            pix = page.get_pixmap()
            sigwx_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
        else:
            sigwx_img = Image.open(sigwx_file).convert("RGB").copy()
        st.image(sigwx_img, caption="SIGWX: Full Chart (No cropping)")
        sigwx_desc = st.text_input("SIGWX: Area/focus for AI (default: Portugal)", value=st.session_state["sigwx_desc"], key="sigwxdesc")
        if st.button("Save SIGWX Chart"):
            sigwx_img, sigwx_img_bytes = downscale_image(sigwx_img)
            st.session_state["sigwx_img_bytes"] = sigwx_img_bytes
            st.session_state["sigwx_desc"] = sigwx_desc
            st.success("SIGWX image saved! You may now close this section.")

# Only enable report button when both are saved
ready = st.session_state.get("cropped_spc_bytes") and st.session_state.get("sigwx_img_bytes")
if ready:
    if st.button("Generate PDF Report"):
        with st.spinner("Generating PDF and calling AI..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=12)
            pdf.cover_page(mission, pilot, aircraft, str(date), callsign)
            # SPC Section
            spc_base64 = base64.b64encode(st.session_state["cropped_spc_bytes"].getvalue()).decode("utf-8")
            spc_ai_text = ai_chart_analysis(spc_base64, "SPC", st.session_state["spc_desc"])
            pdf.chart_section(
                title="Surface Pressure Chart (SPC)",
                img_bytes=st.session_state["cropped_spc_bytes"],
                ai_text=spc_ai_text,
                user_desc=st.session_state["spc_desc"]
            )
            # SIGWX Section (no crop)
            sigwx_base64 = base64.b64encode(st.session_state["sigwx_img_bytes"].getvalue()).decode("utf-8")
            sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", st.session_state["sigwx_desc"])
            pdf.chart_section(
                title="Significant Weather Chart (SIGWX)",
                img_bytes=st.session_state["sigwx_img_bytes"],
                ai_text=sigwx_ai_text,
                user_desc=st.session_state["sigwx_desc"]
            )
            out_pdf = "Preflight_Briefing.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                st.download_button(
                    label="Download Preflight Briefing PDF",
                    data=f,
                    file_name=out_pdf,
                    mime="application/pdf"
                )
            st.success("PDF generated successfully!")
else:
    st.info("You must crop and save the SPC, and save the SIGWX chart, before generating the PDF report.")

st.caption("Crop the SPC and save; upload SIGWX and specify area (default Portugal), then save, before generating your briefing PDF.")







