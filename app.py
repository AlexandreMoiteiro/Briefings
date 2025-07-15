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

SPC_PROMPT = """
You are an aviation meteorology instructor. Based only on the attached surface pressure chart, write a concise, structured, and realistic weather briefing focused ONLY on the area of Portugal or the Iberian Peninsula (or as specified by the user).
- Organize your briefing as bullet points (•) or numbered items, covering:
    • The synoptic situation (pressure systems, fronts)
    • Any approaching or passing fronts
    • Expected wind direction/speed at low level
    • Notable clouds, weather, and any VFR/IFR risks
    • Any relevant trends
Do NOT use Markdown, bold, or asterisks. Write just in clear English, as for a professional pilot briefing. If there are no notable risks, say so. Do NOT invent information—describe only what you see in the chart.
"""

SIGWX_PROMPT = """
You are an aviation meteorology instructor. Based only on the attached significant weather chart, write a concise, structured, and realistic weather briefing focused ONLY on the area of Portugal or the Iberian Peninsula (or as specified by the user).
- Organize your briefing as bullet points (•) or numbered items, covering:
    • Clouds, CBs, thunderstorms
    • Turbulence (areas and severity)
    • Icing and freezing levels
    • Other relevant hazards
Do NOT use Markdown, bold, or asterisks. Write just in clear English, as for a professional pilot briefing. If there are no notable risks, say so. Do NOT invent information—describe only what you see in the chart.
"""

def downscale_image(img, width=1300):
    if img.width > width:
        ratio = width / img.width
        img = img.resize((width, int(img.height * ratio)))
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG", optimize=True)
    img_bytes.seek(0)
    return img, img_bytes

def ai_briefing(image_base64, prompt, user_focus=""):
    focus_text = f"Focus ONLY on: {user_focus.strip()}" if user_focus.strip() else ""
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": focus_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                ]
            }
        ],
        max_tokens=900,
        temperature=0.33
    )
    return response.choices[0].message.content

# PDF class styled like Mass & Balance report
class MBStylePDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            # Cover: shaded band
            self.set_fill_color(34,34,34)
            self.rect(0, 0, 210, 20, 'F')
            self.set_font("Arial", 'B', 16)
            self.set_text_color(255,255,255)
            self.set_xy(10, 8)
            self.cell(0, 10, "Preflight Briefing Report", ln=1, align='L')
            self.set_text_color(0,0,0)
            self.ln(12)
        else:
            self.set_y(12)

    def cover(self, mission, date, pilot, aircraft, callsign):
        self.add_page()
        self.set_y(25)
        self.set_font("Arial", 'B', 13)
        self.cell(0, 10, f"Mission: {mission}", ln=1)
        self.set_font("Arial", '', 12)
        self.cell(0, 8, f"Date: {date}", ln=1)
        self.cell(0, 8, f"Pilot: {pilot}", ln=1)
        self.cell(0, 8, f"Aircraft: {aircraft}", ln=1)
        self.cell(0, 8, f"Callsign: {callsign}", ln=1)
        self.ln(3)

    def add_chart(self, title, img_bytes, ai_text):
        self.add_page()
        self.set_font("Arial", 'B', 14)
        self.set_text_color(33,33,33)
        self.cell(0, 11, title, ln=1)
        self.ln(1)
        img_path = f"tmp_{hash(img_bytes)}.png"
        with open(img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.image(img_path, x=17, w=175)
        self.ln(5)
        # Format AI text as bullets
        self.set_font("Arial", '', 11)
        lines = ai_text.split("\n")
        for line in lines:
            l = line.strip()
            # Bullet (starts with - or • or number)
            if l.startswith("- ") or l.startswith("• "):
                self.set_x(23)
                self.cell(6, 7, u"\u2022", align="R")  # real bullet
                self.multi_cell(0, 7, l[2:])
            elif l[:2].isdigit() and l[2:4] in [". ", ") "]:
                self.set_x(23)
                self.cell(8, 7, l[:2], align='R')
                self.multi_cell(0, 7, l[4:])
            elif l:
                self.set_x(17)
                self.multi_cell(0, 7, l)
            else:
                self.ln(1)
        self.ln(1)

st.title("Preflight Briefing PDF (Mass & Balance Style)")
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
spc_img, spc_img_bytes = None, None
if spc_file:
    if spc_file.type == "application/pdf":
        pdf_bytes = spc_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        spc_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        spc_img = Image.open(spc_file).convert("RGB").copy()
    spc_img, spc_img_bytes = downscale_image(spc_img, width=1300)
    st.image(spc_img, caption="SPC Chart (full)")
spc_focus = st.text_input("SPC briefing focus (optional, e.g. 'over Portugal')", "")

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
    sigwx_img, sigwx_img_bytes = downscale_image(sigwx_img, width=1300)
    st.image(sigwx_img, caption="SIGWX Chart (full)")
sigwx_focus = st.text_input("SIGWX briefing focus (optional, e.g. 'over Portugal')", "")

can_generate = all([
    mission, pilot, aircraft, callsign, date,
    spc_img_bytes, sigwx_img_bytes
])

if st.button("Generate PDF Briefing", disabled=not can_generate):
    with st.spinner("Calling AI and generating PDF..."):
        spc_base64 = base64.b64encode(spc_img_bytes.getvalue()).decode("utf-8")
        spc_report = ai_briefing(spc_base64, SPC_PROMPT, spc_focus)
        sigwx_base64 = base64.b64encode(sigwx_img_bytes.getvalue()).decode("utf-8")
        sigwx_report = ai_briefing(sigwx_base64, SIGWX_PROMPT, sigwx_focus)

        pdf = MBStylePDF()
        pdf.cover(mission, str(date), pilot, aircraft, callsign)
        pdf.add_chart("Surface Pressure Chart (SPC)", spc_img_bytes, spc_report)
        pdf.add_chart("Significant Weather Chart (SIGWX)", sigwx_img_bytes, sigwx_report)
        out_pdf = "Preflight_Briefing.pdf"
        pdf.output(out_pdf)
        with open(out_pdf, "rb") as f:
            st.download_button(
                label="Download Briefing PDF",
                data=f,
                file_name=out_pdf,
                mime="application/pdf"
            )
        st.success("PDF generated successfully!")

st.caption("PDF styled for clarity, with real bullet points. Charts are shown in full, briefing is clean and structured.")





