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
            "You are an aviation meteorologist. Analyze the uploaded surface pressure chart (SPC)."
            " Focus your analysis on the cropped area (for example, over Portugal or the Iberian Peninsula),"
            " but refer to what the area shows in a briefing format for pilots. Start your answer like: "
            "'The area shown above Portugal...'. Include: "
            "- Synoptic situation and pressure systems\n"
            "- Fronts in/near the focus area\n"
            "- Wind and cloud cover\n"
            "- Expected weather and flight category (VFR/IFR)\n"
            "Do not mention the word 'cropped'."
        )
    else:
        sys_prompt = (
            "You are an aviation meteorologist. Analyze the uploaded significant weather chart (SIGWX). "
            "Give a concise, structured briefing for pilots, including:\n"
            "- Cloud types/amounts, altitudes\n"
            "- Turbulence, significant weather phenomena (CBs, icing, etc.)\n"
            "- Freezing levels, visibility, and any flight hazards in the shown area.\n"
            "Do not mention the word 'image' or 'uploaded'."
        )
    user_prompt = "Please focus your analysis on: " + user_area_desc if user_area_desc.strip() else "Provide a pilot-oriented briefing for the area described."
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
        max_tokens=700,
        temperature=0.5
    )
    return response.choices[0].message.content

class CleanBriefingPDF(FPDF):
    def cover_page(self, mission_number, pilot, aircraft, date, callsign):
        self.add_page()
        self.set_font("Helvetica", 'B', 28)
        self.set_text_color(28, 34, 87)
        self.cell(0, 25, "Preflight Weather Briefing", ln=1, align='C')
        self.set_text_color(0,0,0)
        self.ln(8)
        self.set_font("Helvetica", '', 15)
        self.cell(0, 12, f"Mission Number: {mission_number}", ln=1, align='C')
        self.cell(0, 12, f"Pilot: {pilot}", ln=1, align='C')
        self.cell(0, 12, f"Aircraft: {aircraft}", ln=1, align='C')
        self.cell(0, 12, f"Callsign: {callsign}", ln=1, align='C')
        self.cell(0, 12, f"Date: {date}", ln=1, align='C')
        self.ln(15)

    def section_header(self, title):
        self.set_font("Helvetica", 'B', 19)
        self.set_text_color(28, 34, 87)
        self.cell(0, 14, title, ln=1)
        self.set_text_color(0,0,0)
        self.ln(2)

    def add_chart_with_label(self, label, img_bytes):
        self.set_font("Helvetica", 'I', 12)
        self.cell(0, 8, label, ln=1)
        self.ln(1)
        chart_img_path = f"tmp_{label.replace(' ','_')}.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.image(chart_img_path, x=25, w=160)
        self.ln(4)

    def add_analysis(self, text):
        self.set_font("Helvetica", '', 12)
        self.multi_cell(0, 9, text)
        self.ln(4)

# --- STREAMLIT APP ---

st.title("Preflight Briefing Package: SPC & SIGWX")

st.markdown("""
- **SPC**: Upload the chart, crop/select the area you want analyzed.  
  The PDF will include the full chart and the cropped area.
- **SIGWX**: Upload the chart (no crop; whole chart included and analyzed).
- Enter mission info, then generate a clean, print-ready PDF briefing.
""", unsafe_allow_html=True)

st.subheader("Mission Info")
mission_number = st.text_input("Mission Number", "")
pilot = st.text_input("Pilot", "")
aircraft = st.text_input("Aircraft", "")
callsign = st.text_input("Callsign", "")
date = st.date_input("Date", datetime.date.today())

st.subheader("Surface Pressure Chart (SPC)")
spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
spc_focus = st.text_input("Describe area for AI to brief (e.g. 'Portugal', 'Iberian Peninsula', etc.):", key="spcfocus")

spc_full_img, spc_cropped_img, spc_full_img_bytes, spc_cropped_img_bytes = None, None, None, None
if spc_file:
    if spc_file.type == "application/pdf":
        pdf_bytes = spc_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        spc_full_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        spc_full_img = Image.open(spc_file).convert("RGB").copy()
    st.write("Crop/select the region you want AI to brief about (e.g., Portugal).")
    spc_cropped_img = st_cropper(
        spc_full_img,
        aspect_ratio=None,
        box_color='red',
        return_type='image',
        realtime_update=True,
        key="spc_crop"
    )
    st.image(spc_cropped_img, caption="Selected SPC Area for AI Briefing")
    spc_full_img, spc_full_img_bytes = downscale_image(spc_full_img)
    spc_cropped_img, spc_cropped_img_bytes = downscale_image(spc_cropped_img)

st.subheader("Significant Weather Chart (SIGWX)")
sigwx_file = st.file_uploader("Upload SIGWX/SWC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="sigwx")
sigwx_focus = st.text_input("If you want, describe the region or weather to focus on (optional):", key="sigwxfocus")

sigwx_full_img, sigwx_full_img_bytes = None, None
if sigwx_file:
    if sigwx_file.type == "application/pdf":
        pdf_bytes = sigwx_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        sigwx_full_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        sigwx_full_img = Image.open(sigwx_file).convert("RGB").copy()
    st.image(sigwx_full_img, caption="Full SIGWX Chart")
    sigwx_full_img, sigwx_full_img_bytes = downscale_image(sigwx_full_img)

if st.button("Generate PDF Report", disabled=not (spc_full_img_bytes and spc_cropped_img_bytes and sigwx_full_img_bytes)):
    with st.spinner("Generating PDF and calling AI..."):
        pdf = CleanBriefingPDF()
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.cover_page(mission_number, pilot, aircraft, str(date), callsign)

        # --- SPC Section ---
        pdf.section_header("Surface Pressure Chart (SPC)")
        pdf.add_chart_with_label("Full Chart", spc_full_img_bytes)
        pdf.add_chart_with_label("Selected Area", spc_cropped_img_bytes)
        # AI analysis using cropped image, but report refers to area/region
        spc_cropped_base64 = base64.b64encode(spc_cropped_img_bytes.getvalue()).decode("utf-8")
        spc_ai_text = ai_chart_analysis(spc_cropped_base64, "SPC", spc_focus)
        pdf.add_analysis(spc_ai_text)

        # --- SIGWX Section ---
        pdf.section_header("Significant Weather Chart (SIGWX)")
        pdf.add_chart_with_label("Full Chart", sigwx_full_img_bytes)
        sigwx_base64 = base64.b64encode(sigwx_full_img_bytes.getvalue()).decode("utf-8")
        sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", sigwx_focus)
        pdf.add_analysis(sigwx_ai_text)

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

st.caption("Charts and analysis will appear in the PDF, clean and ready for briefing or printing.")






