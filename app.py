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

def downscale_image(img, width=1200):
    # Higher res for PDF quality
    if img.width > width:
        ratio = width / img.width
        new_size = (width, int(img.height * ratio))
        img = img.resize(new_size)
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG", optimize=True)
    img_bytes.seek(0)
    return img, img_bytes

def ai_spc_analysis(crop_base64, focus_desc):
    prompt = (
    "You are an aviation meteorology instructor. The image is a surface pressure chart (SPC) showing a selected region (e.g., Portugal or the Iberian Peninsula). "
    "Provide a concise, structured report for a flight briefing PDF. Phrase your report as: "
    "'In the area over [describe area in the image, e.g., Portugal or the Iberian Peninsula], the situation is as follows: ...'. "
    "Summarize: synoptic situation, fronts, wind, cloud cover, and VFR/IFR/weather hazards. "
    "Only base your analysis on the area shown in the image."
)
    if focus_desc.strip():
        user_focus = f"Focus on: {focus_desc.strip()}"
    else:
        user_focus = "Brief only for the region in the cropped image."
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_focus},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{crop_base64}"}}
                ]
            }
        ],
        max_tokens=650,
        temperature=0.45
    )
    return response.choices[0].message.content

def ai_sigwx_analysis(full_base64, focus_desc):
    prompt = (
        "You are an aviation meteorology instructor. Analyze the attached significant weather chart (SIGWX) for a flight briefing PDF. "
        "If a focus is provided, only analyze that region, otherwise cover the whole area. Structure your report with: "
        "1) Cloud types, amounts, altitudes; 2) Turbulence areas/severity; 3) Significant phenomena (CBs, icing, etc.); "
        "4) Freezing levels and flight hazards."
    )
    if focus_desc.strip():
        user_focus = f"Focus on: {focus_desc.strip()}"
    else:
        user_focus = "Brief for the whole area of the chart."
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_focus},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{full_base64}"}}
                ]
            }
        ],
        max_tokens=650,
        temperature=0.45
    )
    return response.choices[0].message.content

class BriefingPDF(FPDF):
    def cover(self, mission, date, pilot, aircraft, callsign):
        self.add_page()
        self.set_fill_color(34, 34, 34)
        self.rect(0, 0, 210, 40, 'F')
        self.set_font("Arial", 'B', 22)
        self.set_text_color(255,255,255)
        self.set_xy(10, 14)
        self.cell(0, 12, "Preflight Briefing Package", ln=1, align='L')
        self.set_font("Arial", '', 13)
        self.cell(0, 8, f"Mission: {mission}", ln=1)
        self.cell(0, 8, f"Date: {date}", ln=1)
        self.cell(0, 8, f"Pilot: {pilot}", ln=1)
        self.cell(0, 8, f"Aircraft: {aircraft}", ln=1)
        self.cell(0, 8, f"Callsign: {callsign}", ln=1)
        self.ln(10)

    def add_section(self, title, images, captions, ai_text):
        self.add_page()
        self.set_font("Arial", 'B', 16)
        self.set_text_color(0, 0, 0)
        self.cell(0, 12, title, ln=1)
        self.ln(3)
        for img_bytes, cap in zip(images, captions):
            if img_bytes:
                img_path = f"tmp_{hash(img_bytes)}.png"
                with open(img_path, "wb") as f:
                    f.write(img_bytes.getvalue())
                self.image(img_path, x=18, w=175)
                if cap:
                    self.set_font("Arial", 'I', 11)
                    self.cell(0, 9, cap, ln=1)
                self.ln(2)
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ai_text)
        self.ln(2)

st.title("Preflight Briefing PDF: SPC + SIGWX")
st.markdown("""
- Upload your **Surface Pressure Chart (SPC)** and crop/select your focus area.  
- Upload your **Significant Weather Chart (SIGWX)** (full chart, no cropping).  
- Add a note for AI to focus its analysis if you wish.  
- The PDF will include: a cover, both full charts, the cropped SPC focus, and structured AI analyses.
""", unsafe_allow_html=True)

col1, col2 = st.columns(2)
with col1:
    mission = st.text_input("Mission number", "")
    date = st.date_input("Date", datetime.date.today())
with col2:
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")

st.markdown("### Surface Pressure Chart (SPC)")
spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
spc_img, spc_img_bytes, cropped_spc_img, cropped_spc_bytes = None, None, None, None

if spc_file:
    if spc_file.type == "application/pdf":
        pdf_bytes = spc_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        spc_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        spc_img = Image.open(spc_file).convert("RGB").copy()
    spc_img, spc_img_bytes = downscale_image(spc_img, width=1200)
    st.image(spc_img, caption="Full SPC Chart (will be shown in PDF)")

    st.markdown("**Crop/select the focus area for analysis (e.g., Portugal, Iberian Peninsula).**")
    cropped_spc_img = st_cropper(
        spc_img,
        aspect_ratio=None,
        box_color='red',
        return_type='image',
        realtime_update=True,
        key="spc_crop"
    )
    st.image(cropped_spc_img, caption="SPC Focus Area for AI Analysis")
    cropped_spc_img, cropped_spc_bytes = downscale_image(cropped_spc_img, width=600)

spc_focus = st.text_input("SPC: Describe the focus area (e.g., 'over Portugal', optional)", "")

st.markdown("---")
st.markdown("### Significant Weather Chart (SIGWX)")
sigwx_file = st.file_uploader("Upload SIGWX/SWC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="sigwx")
sigwx_img, sigwx_img_bytes = None, None

if sigwx_file:
    if sigwx_file.type == "application/pdf":
        pdf_bytes = sigwx_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        sigwx_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        sigwx_img = Image.open(sigwx_file).convert("RGB").copy()
    sigwx_img, sigwx_img_bytes = downscale_image(sigwx_img, width=1200)
    st.image(sigwx_img, caption="Full SIGWX Chart (will be shown in PDF)")

sigwx_focus = st.text_input("SIGWX: Describe the focus for AI (optional)", "")

can_generate = all([
    mission, pilot, aircraft, callsign, date,
    spc_img_bytes, cropped_spc_bytes, sigwx_img_bytes
])

if st.button("Generate PDF Briefing", disabled=not can_generate):
    with st.spinner("Calling AI and generating PDF..."):
        # SPC AI
        spc_crop_base64 = base64.b64encode(cropped_spc_bytes.getvalue()).decode("utf-8")
        spc_report = ai_spc_analysis(spc_crop_base64, spc_focus)
        # SIGWX AI
        sigwx_base64 = base64.b64encode(sigwx_img_bytes.getvalue()).decode("utf-8")
        sigwx_report = ai_sigwx_analysis(sigwx_base64, sigwx_focus)

        # PDF
        pdf = BriefingPDF()
        pdf.cover(mission, str(date), pilot, aircraft, callsign)
        pdf.add_section(
            "Surface Pressure Chart (SPC)",
            images=[spc_img_bytes, cropped_spc_bytes],
            captions=["Full SPC Chart", "Focus area for analysis (see report below)"],
            ai_text=spc_report
        )
        pdf.add_section(
            "Significant Weather Chart (SIGWX)",
            images=[sigwx_img_bytes],
            captions=["Full SIGWX Chart"],
            ai_text=sigwx_report
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

st.caption("This PDF contains both full charts and structured AI briefings based on your selected/focus areas.")



