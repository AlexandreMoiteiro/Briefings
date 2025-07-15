import streamlit as st
from PIL import Image, ImageDraw
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

def draw_crop_rectangle(img, crop_box, outline="red", width=5):
    """Draws a rectangle (crop_box = left, upper, right, lower) on the image."""
    img = img.copy()
    draw = ImageDraw.Draw(img)
    draw.rectangle(crop_box, outline=outline, width=width)
    return img

def ai_chart_analysis(img_base64, chart_type, bullet_style=True):
    # Use "practical pilot preflight bullets" style
    if chart_type == "SPC":
        prompt = (
            "You are a pilot preparing a preflight weather briefing. You've focused your attention on the boxed area shown in the attached surface pressure chart. "
            "Summarize the synoptic situation, significant weather features, and expectations for VFR/IFR in that boxed region. "
            + ("Present your notes as clear, concise bullet points, suitable for a real pilot's preflight briefing. Avoid technical jargon and robotic style."
            if bullet_style else
            "Write your notes in full sentences as if making personal pilot notes, without using bullet points or lists. Be concise.")
        )
    else:
        prompt = (
            "You are a pilot preparing a preflight weather briefing. After reviewing the attached significant weather chart (SIGWX), write practical briefing notes about weather, turbulence, clouds, icing, and flight hazards across the area shown. "
            + ("Use clear, concise bullet points, suitable for a pilot's preflight briefing."
            if bullet_style else
            "Use full sentences as if making personal pilot notes, without using bullet points.")
        )
    messages = [
        {"role": "system", "content": "You are a pilot writing a preflight weather briefing for other pilots."},
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
        temperature=0.4
    )
    return response.choices[0].message.content

class BriefingPDF(FPDF):
    def cover_page(self, mission, pilot, aircraft, date, callsign, remarks=""):
        self.add_page()
        # Nice dark header
        self.set_fill_color(36, 44, 74)
        self.rect(0, 0, 210, 35, 'F')
        self.set_xy(10, 8)
        self.set_text_color(255,255,255)
        self.set_font("Arial", 'B', 21)
        self.cell(0, 11, "Preflight Weather Briefing", ln=True)
        self.set_font("Arial", '', 12)
        self.ln(2)
        self.set_text_color(0,0,0)
        self.set_font("Arial", '', 13)
        self.ln(18)
        self.set_x(12)
        self.set_font("Arial", 'B', 12)
        self.cell(37, 8, "Mission Number:", 0)
        self.set_font("Arial", '', 12)
        self.cell(0, 8, str(mission), ln=1)
        self.set_x(12)
        self.set_font("Arial", 'B', 12)
        self.cell(37, 8, "Pilot:", 0)
        self.set_font("Arial", '', 12)
        self.cell(0, 8, pilot, ln=1)
        self.set_x(12)
        self.set_font("Arial", 'B', 12)
        self.cell(37, 8, "Aircraft:", 0)
        self.set_font("Arial", '', 12)
        self.cell(0, 8, aircraft, ln=1)
        self.set_x(12)
        self.set_font("Arial", 'B', 12)
        self.cell(37, 8, "Callsign:", 0)
        self.set_font("Arial", '', 12)
        self.cell(0, 8, callsign, ln=1)
        self.set_x(12)
        self.set_font("Arial", 'B', 12)
        self.cell(37, 8, "Date:", 0)
        self.set_font("Arial", '', 12)
        self.cell(0, 8, date, ln=1)
        if remarks:
            self.set_x(12)
            self.set_font("Arial", 'B', 12)
            self.cell(37, 8, "Remarks:", 0)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 8, remarks)
        self.ln(8)

    def chart_section_spc(self, full_img_bytes, cropped_img_bytes, ai_text):
        self.add_page()
        self.set_font("Arial", 'B', 14)
        self.cell(0, 9, "Surface Pressure Chart (SPC)", ln=True)
        self.set_font("Arial", 'I', 11)
        self.cell(0, 8, "Full chart with boxed analysis area (red).", ln=True)
        # Insert full chart with rectangle
        chart_img_path = "spc_full_rect.png"
        with open(chart_img_path, "wb") as f:
            f.write(full_img_bytes.getvalue())
        self.image(chart_img_path, x=17, w=170)
        self.ln(3)
        self.set_font("Arial", 'I', 11)
        self.cell(0, 8, "Cropped area (zoom-in):", ln=True)
        # Insert cropped area
        chart_img_crop_path = "spc_cropped.png"
        with open(chart_img_crop_path, "wb") as f:
            f.write(cropped_img_bytes.getvalue())
        self.image(chart_img_crop_path, x=48, w=110)
        self.ln(6)
        # AI Notes
        self.set_font("Arial", '', 11)
        self.multi_cell(0, 8, ai_text)
        self.ln(1)

    def chart_section_sigwx(self, img_bytes, ai_text):
        self.add_page()
        self.set_font("Arial", 'B', 14)
        self.cell(0, 9, "Significant Weather Chart (SIGWX)", ln=True)
        self.set_font("Arial", 'I', 11)
        self.cell(0, 8, "Full chart for briefing area.", ln=True)
        chart_img_path = "sigwx_full.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.image(chart_img_path, x=17, w=170)
        self.ln(7)
        self.set_font("Arial", '', 11)
        self.multi_cell(0, 8, ai_text)
        self.ln(1)

st.title("Preflight Briefing Package (SPC & SIGWX)")

st.markdown("""
**1. Upload your SPC and crop the box for analysis (a red box will be shown on the full chart).**  
**2. Upload SIGWX (full chart is used).**  
**3. Fill in your briefing info and generate your PDF.**  
**SPC: AI focuses only on the area inside the red box (do NOT mention an area name).**
""")

# --- SPC Upload and Crop ---
st.subheader("Surface Pressure Chart (SPC)")
spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
cropped_spc_img, spc_img_bytes, spc_full_img_bytes, spc_full_img_for_box, crop_box = None, None, None, None, None
if spc_file:
    if spc_file.type == "application/pdf":
        pdf_bytes = spc_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        spc_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        spc_img = Image.open(spc_file).convert("RGB").copy()
    st.markdown("**Crop the box for the area you want analyzed (AI focuses only inside this box, but the full chart with the box will appear in the PDF).**")
    cropped_spc_img = st_cropper(
        spc_img,
        aspect_ratio=None,
        box_color='red',
        return_type='image',
        realtime_update=True,
        key="spc_crop"
    )
    # Get crop coordinates from session state for rectangle overlay
    crop_info = st.session_state.get("spc_crop_crop_box", None)
    if crop_info:
        left = int(crop_info['left'])
        top = int(crop_info['top'])
        right = left + int(crop_info['width'])
        bottom = top + int(crop_info['height'])
        crop_box = (left, top, right, bottom)
        spc_img_with_box = draw_crop_rectangle(spc_img, crop_box, outline="red", width=5)
    else:
        spc_img_with_box = spc_img
    _, spc_img_bytes = downscale_image(cropped_spc_img)
    _, spc_full_img_bytes = downscale_image(spc_img_with_box)
    _, spc_full_img_for_box = downscale_image(spc_img)  # for pdf
    st.image(spc_img_with_box, caption="Full SPC Chart with Red Box (will appear in PDF)")
    st.image(cropped_spc_img, caption="Cropped Area (AI analyzes this only, also in PDF)")

# --- SIGWX Upload (no crop) ---
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
    _, sigwx_img_bytes = downscale_image(sigwx_img)
    st.image(sigwx_img, caption="Full SIGWX Chart (will appear in PDF)")

# --- Briefing Metadata ---
st.subheader("Briefing Information")
col1, col2 = st.columns(2)
with col1:
    mission = st.text_input("Mission Number", "")
    pilot = st.text_input("Pilot", "")
with col2:
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")
    date = st.date_input("Date", datetime.date.today())
remarks = st.text_area("Remarks / Route / Objectives (optional)", "")

if st.button("Generate PDF Report", disabled=not (spc_img_bytes and sigwx_img_bytes and mission and pilot and aircraft and callsign)):
    with st.spinner("Generating PDF and calling AI..."):
        pdf = BriefingPDF()
        pdf.set_auto_page_break(auto=True, margin=12)
        pdf.cover_page(mission, pilot, aircraft, str(date), callsign, remarks)
        # AI analysis: SPC (cropped box), full image for PDF
        spc_base64 = base64.b64encode(spc_img_bytes.getvalue()).decode("utf-8")
        spc_ai_text = ai_chart_analysis(spc_base64, "SPC", bullet_style=True)
        pdf.chart_section_spc(
            full_img_bytes=spc_full_img_bytes,
            cropped_img_bytes=spc_img_bytes,
            ai_text=spc_ai_text
        )
        # AI analysis: SIGWX (full chart)
        sigwx_base64 = base64.b64encode(sigwx_img_bytes.getvalue()).decode("utf-8")
        sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", bullet_style=True)
        pdf.chart_section_sigwx(
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

st.caption("SPC: Full chart with red analysis box plus zoom-in and notes. SIGWX: Full chart and notes. Bullet points style, practical for real-world briefing.")



