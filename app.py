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

def ai_chart_analysis(img_base64, chart_type, area_desc):
    # Prompts for natural, pilot-written style, not textbook, no lists
    if chart_type == "SPC":
        prompt = (
            f"You are a pilot preparing for a flight. You've just looked at the surface pressure chart (SPC) attached below. "
            f"Focus your attention on the area described by the user: '{area_desc.strip()}'. "
            "Write down, in your own words and in full sentences (not lists), your expectations and notes about the synoptic situation, main weather systems, and anything relevant for VFR/IFR in this region. "
            "Do not use bullet points or lists. Write this as if you were making your own private preflight weather notes, to be included in a briefing report."
        )
    else:
        prompt = (
            f"You are a pilot preparing for a flight. You've just looked at the significant weather chart (SIGWX) attached below, for the whole area shown. "
            "Write your own notes (in full sentences, not lists), highlighting what you expect for weather, turbulence, clouds, icing, and hazards relevant for flight. "
            "Don't use bullet points or lists. Write as if these are your personal, handwritten preflight notes for your briefing package."
        )
    messages = [
        {"role": "system", "content": "You are a pilot preparing for a flight."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
            ]
        }
    ]
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        max_tokens=650,
        temperature=0.5
    )
    return response.choices[0].message.content

class BriefingPDF(FPDF):
    def cover_page(self, mission, pilot, aircraft, date, callsign):
        self.add_page()
        self.set_fill_color(34,34,34)
        self.rect(0, 0, 210, 45, 'F')
        self.set_font("Arial", 'B', 22)
        self.set_text_color(255,255,255)
        self.set_xy(10,12)
        self.cell(0, 15, "Preflight Briefing", ln=True, align='L')
        self.set_font("Arial", '', 13)
        self.cell(0, 9, f"Mission: {mission}", ln=True)
        self.cell(0, 9, f"Pilot: {pilot}", ln=True)
        self.cell(0, 9, f"Aircraft: {aircraft}", ln=True)
        self.cell(0, 9, f"Callsign: {callsign}", ln=True)
        self.cell(0, 9, f"Date: {date}", ln=True)
        self.ln(20)

    def chart_section(self, title, img_bytes, ai_text, focus_area_desc=None):
        self.add_page()
        self.set_font("Arial", 'B', 16)
        self.cell(0, 11, title, ln=True)
        self.ln(2)
        # Focus area for SPC
        if focus_area_desc and focus_area_desc.strip():
            self.set_font("Arial", 'I', 11)
            self.set_text_color(70,70,70)
            self.multi_cell(0, 8, f"Focus area: {focus_area_desc.strip()}")
            self.ln(1)
        self.set_text_color(0,0,0)
        self.set_font("Arial", '', 12)
        # Insert full chart image (centered)
        chart_img_path = "tmp_chart_full.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.image(chart_img_path, x=20, w=170)
        self.ln(3)
        self.set_font("Arial", '', 11)
        self.multi_cell(0, 8, ai_text)
        self.ln(1)

st.title("Preflight Briefing Package (SPC & SIGWX)")

st.markdown("""
Upload your SPC and SIGWX charts.<br>
For SPC, crop the area to be *analyzed* (but the **full chart will be in the PDF**).<br>
For SIGWX, the **full chart** is used for both analysis and PDF.<br>
Enter mission number and info. When ready, generate your PDF report.
""", unsafe_allow_html=True)

# ---- Inputs and Cropping (SPC) ----

st.subheader("Surface Pressure Chart (SPC)")
spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
cropped_spc_img, spc_img_bytes, spc_full_img_bytes = None, None, None
if spc_file:
    if spc_file.type == "application/pdf":
        pdf_bytes = spc_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        spc_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        spc_img = Image.open(spc_file).convert("RGB").copy()
    st.markdown("Crop the SPC chart for analysis (AI will focus here, PDF shows full chart).")
    cropped_spc_img = st_cropper(
        spc_img,
        aspect_ratio=None,
        box_color='red',
        return_type='image',
        realtime_update=True,
        key="spc_crop"
    )
    st.image(spc_img, caption="Full SPC Chart (will appear in PDF)")
    cropped_spc_img, spc_img_bytes = downscale_image(cropped_spc_img)
    _, spc_full_img_bytes = downscale_image(spc_img)

st.subheader("Significant Weather Chart (SIGWX)")
sigwx_file = st.file_uploader("Upload SIGWX/SWC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="sigwx")
sigwx_img_bytes = None
if sigwx_file:
    if sigwx_file.type == "application/pdf":
        pdf_bytes = sigwx_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        sigwx_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        sigwx_img = Image.open(sigwx_file).convert("RGB").copy()
    st.image(sigwx_img, caption="Full SIGWX Chart (will appear in PDF)")
    _, sigwx_img_bytes = downscale_image(sigwx_img)

st.subheader("Briefing Metadata")
col1, col2 = st.columns(2)
with col1:
    mission = st.text_input("Mission Number", "")
    pilot = st.text_input("Pilot", "")
with col2:
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")
    date = st.date_input("Date", datetime.date.today())
spc_desc = st.text_input("SPC: Area or focus (e.g. 'Portugal', 'Iberian Peninsula')", value="Portugal", key="spcdesc")

if st.button("Generate PDF Report", disabled=not (spc_img_bytes and sigwx_img_bytes and mission and pilot and aircraft and callsign)):
    with st.spinner("Preparing PDF and calling AI..."):
        pdf = BriefingPDF()
        pdf.set_auto_page_break(auto=True, margin=12)
        pdf.cover_page(mission, pilot, aircraft, str(date), callsign)
        # AI analysis: SPC (cropped), full image for PDF
        spc_base64 = base64.b64encode(spc_img_bytes.getvalue()).decode("utf-8")
        spc_ai_text = ai_chart_analysis(spc_base64, "SPC", spc_desc)
        pdf.chart_section(
            title="Surface Pressure Chart (SPC)",
            img_bytes=spc_full_img_bytes,
            ai_text=spc_ai_text,
            focus_area_desc=spc_desc
        )
        # AI analysis: SIGWX (full chart)
        sigwx_base64 = base64.b64encode(sigwx_img_bytes.getvalue()).decode("utf-8")
        sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", "")
        pdf.chart_section(
            title="Significant Weather Chart (SIGWX)",
            img_bytes=sigwx_img_bytes,
            ai_text=sigwx_ai_text
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

st.caption("Charts appear in full in the PDF. SPC is analyzed for your chosen focus area, SIGWX for the whole chart. AI text is written as if in a pilot’s own preflight notebook.")



