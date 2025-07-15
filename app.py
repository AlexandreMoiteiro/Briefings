import streamlit as st
from PIL import Image
import openai
import io
import base64
from fpdf import FPDF
import fitz
import datetime

# --- SET YOUR OPENAI API KEY ---
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
            "Generate a concise, structured report suitable for a flight briefing PDF. Your analysis should focus on the region described by the user (not the entire chart), and the region should be referenced by name in your text (e.g., 'the area over Portugal' or 'the Iberian Peninsula'). Structure your report to include:\n"
            "- Synoptic situation and pressure systems\n"
            "- Location and type of fronts\n"
            "- Wind direction/speed and general cloud cover\n"
            "- Expected weather and flight category (VFR/IFR)\n"
            "Begin the report with a phrase clarifying the focus area as described by the user."
        )
    else:
        sys_prompt = (
            "You are an aviation meteorology instructor. Analyze the uploaded significant weather chart (SIGWX). "
            "Generate a concise, structured report suitable for a flight briefing PDF. Focus your analysis on the region described by the user (not the entire chart), referencing that region by name. Structure your report to include:\n"
            "- Cloud types/amounts, altitudes\n"
            "- Turbulence (areas, severity)\n"
            "- Significant weather phenomena (CBs, icing, mountain waves, etc.)\n"
            "- Freezing levels, visibility, and flight hazards\n"
            "Begin the report with a phrase clarifying the focus area as described by the user."
        )
    user_prompt = f"Please focus your analysis on the following region: {user_area_desc.strip()}"
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
        self.set_font('Arial', 'B', 18)
        self.cell(0, 14, "Preflight Briefing Package", align='C', ln=1)
        self.ln(5)

    def chart_section(self, title, img_bytes, ai_text, user_desc=""):
        self.add_page()
        self.set_font("Arial", 'B', 15)
        self.cell(0, 10, title, ln=True)
        self.ln(3)
        # Chart image (full, not cropped)
        chart_img_path = "tmp_chart.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.image(chart_img_path, x=25, w=160)
        self.set_font("Arial", 'I', 10)
        self.set_text_color(120,120,120)
        self.cell(0, 7, "Full chart as provided by user", ln=True)
        self.ln(4)
        # Analysis focus
        if user_desc.strip():
            self.set_font("Arial", 'I', 11)
            self.set_text_color(30,30,30)
            self.multi_cell(0, 8, f"Analysis focus: {user_desc.strip()}")
            self.ln(2)
        # AI analysis
        self.set_font("Arial", '', 12)
        self.set_text_color(0,0,0)
        self.multi_cell(0, 8, ai_text)
        self.ln(1)

    def cover_page(self, mission, pilot, aircraft, date, callsign):
        self.add_page()
        # -- Optional logo placement --
        # self.image("logo.png", x=160, y=10, w=40)   # Uncomment and add logo path if needed
        self.set_font("Arial", 'B', 28)
        self.set_text_color(34, 34, 34)
        self.cell(0, 24, "Preflight Briefing", ln=True, align='C')
        self.ln(16)
        self.set_font("Arial", '', 16)
        self.set_text_color(0,0,0)
        self.cell(0, 12, f"Mission Number: {mission}", ln=True, align='L')
        self.cell(0, 12, f"Pilot: {pilot}", ln=True)
        self.cell(0, 12, f"Aircraft: {aircraft}", ln=True)
        self.cell(0, 12, f"Callsign: {callsign}", ln=True)
        self.cell(0, 12, f"Date: {date}", ln=True)
        self.ln(8)

    def footer(self):
        pass  # No footer

st.set_page_config(page_title="Preflight Briefing", page_icon="🛩️")
st.title("Preflight Briefing Package (SPC & SIGWX)")

st.markdown("""
1. Upload **full** SPC and SIGWX charts (no cropping).
2. Enter mission number, pilot, aircraft, callsign, date, and describe the focus area for the AI analysis.
3. Click **Generate PDF Report**.
""", unsafe_allow_html=True)

# ---- Chart Uploads ----
st.header("Surface Pressure Chart (SPC)")
spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
spc_img_bytes = None
if spc_file:
    if spc_file.type == "application/pdf":
        pdf_bytes = spc_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        spc_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        spc_img = Image.open(spc_file).convert("RGB").copy()
    st.image(spc_img, caption="SPC Chart Preview (full chart)")
    spc_img, spc_img_bytes = downscale_image(spc_img)

st.header("Significant Weather Chart (SIGWX)")
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
    st.image(sigwx_img, caption="SIGWX Chart Preview (full chart)")
    sigwx_img, sigwx_img_bytes = downscale_image(sigwx_img)

# ---- Mission metadata and area descriptions ----
st.header("Briefing Metadata & Area Focus")
with st.form("meta_form"):
    col1, col2 = st.columns(2)
    with col1:
        mission = st.text_input("Mission Number", "")
        pilot = st.text_input("Pilot", "")
        aircraft = st.text_input("Aircraft", "")
        callsign = st.text_input("Callsign", "")
    with col2:
        date = st.date_input("Date", datetime.date.today())
    spc_desc = st.text_input("SPC: Briefly describe focus area (e.g., 'over Portugal')", key="spcdesc")
    sigwx_desc = st.text_input("SIGWX: Briefly describe focus area (e.g., 'the Iberian Peninsula')", key="sigwxdesc")
    generate = st.form_submit_button("Generate PDF Report", disabled=not (spc_img_bytes and sigwx_img_bytes))

if generate:
    with st.spinner("Generating PDF and calling AI..."):
        pdf = BriefingPDF()
        pdf.set_auto_page_break(auto=True, margin=12)
        pdf.cover_page(mission, pilot, aircraft, str(date), callsign)

        if spc_img_bytes:
            spc_base64 = base64.b64encode(spc_img_bytes.getvalue()).decode("utf-8")
            spc_ai_text = ai_chart_analysis(spc_base64, "SPC", spc_desc)
            pdf.chart_section(
                title="Surface Pressure Chart (SPC)",
                img_bytes=spc_img_bytes,
                ai_text=spc_ai_text,
                user_desc=spc_desc
            )
        if sigwx_img_bytes:
            sigwx_base64 = base64.b64encode(sigwx_img_bytes.getvalue()).decode("utf-8")
            sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", sigwx_desc)
            pdf.chart_section(
                title="Significant Weather Chart (SIGWX)",
                img_bytes=sigwx_img_bytes,
                ai_text=sigwx_ai_text,
                user_desc=sigwx_desc
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

st.caption("Charts are used in full. Describe your focus area in text for the AI analysis.")





