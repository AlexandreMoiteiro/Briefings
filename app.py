import streamlit as st
from PIL import Image
import openai
import io
import base64
from streamlit_cropper import st_cropper
from fpdf import FPDF
import fitz
import datetime
import unicodedata
import re

openai.api_key = st.secrets["OPENAI_API_KEY"]

def ascii_safe(text):
    if not isinstance(text, str):
        text = str(text)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

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
            "Write a preflight weather analysis as a student, in natural language, for the cropped area of the Surface Pressure Chart (SPC). "
            "No bullet points, just a paragraph or two: synoptic situation, pressure systems, fronts, expected winds, clouds, VFR/IFR impact. "
            "Do not mention this is automatic or use technical language."
        )
    else:
        sys_prompt = (
            "Write a natural-language, student-style analysis for the Significant Weather Chart (SIGWX). "
            "Paragraphs only (no bullets), focus on clouds, turbulence, significant weather, freezing level, visibility, hazards for the given area. "
            "Do not reference AI or automation."
        )
    user_prompt = f"Please focus on: {user_area_desc.strip()}" if user_area_desc.strip() else "Please focus on Portugal."
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

def ai_decode(code, code_type):
    prompts = {
        "NOTAM": (
            "Rewrite the following NOTAM in plain, student-style English or Portuguese as needed, with all abbreviations decoded and meaning explained clearly and briefly, suitable for a preflight briefing."
        ),
        "METAR": (
            "Decode the following METAR into a natural-language weather summary for a preflight briefing. Use a student-like style, explain all codes and abbreviations, and comment on VFR/IFR suitability."
        ),
        "TAF": (
            "Decode the following TAF into a plain-language forecast suitable for a student preflight briefing. Explain time periods, wind, weather, visibility, and cloud information in a way anyone can understand."
        ),
    }
    sys_prompt = prompts.get(code_type, "Rewrite this code in plain language.")
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": code}
        ],
        max_tokens=500,
        temperature=0.3
    )
    return response.choices[0].message.content

def render_markdown_like(text):
    # Remove markdown formatting (basic)
    lines = text.split('\n')
    final = []
    for line in lines:
        line = re.sub(r'\*\*(.*?)\*\*', r'\1', line)
        line = re.sub(r'\*(.*?)\*', r'\1', line)
        line = line.replace('`', '')
        final.append(line)
    return '\n'.join(final)

class BriefingPDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            pass
        else:
            self.set_font('Arial', 'B', 15)
            self.set_text_color(34, 34, 34)
            self.cell(0, 10, ascii_safe("Preflight Briefing"), align='C', ln=1)
            self.ln(2)
    def footer(self):
        self.set_y(-13)
        self.set_font('Arial', 'I', 7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 7, ascii_safe(f"Page {self.page_no()}"), align='C')
    def section_header(self, title):
        self.set_font("Arial", 'B', 14)
        self.set_text_color(0,0,0)
        self.cell(0, 9, ascii_safe(title), ln=True)
        self.set_draw_color(70, 130, 180) # blue
        self.set_line_width(0.8)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)
        self.set_line_width(0.2)
    def cover_page(self, mission, pilot, aircraft, date, callsign):
        self.add_page()
        self.set_fill_color(34,34,34)
        self.rect(0, 0, 210, 40, 'F')
        self.set_font("Arial", 'B', 21)
        self.set_text_color(255,255,255)
        self.set_xy(12,12)
        self.cell(0, 14, ascii_safe("Preflight Briefing Package"), ln=True, align='L')
        self.set_text_color(0,0,0)
        self.set_xy(10, 40)
        self.set_font("Arial", '', 13)
        self.cell(0, 8, ascii_safe(f"Mission: {mission}"), ln=True)
        self.cell(0, 8, ascii_safe(f"Pilot: {pilot}"), ln=True)
        self.cell(0, 8, ascii_safe(f"Aircraft: {aircraft}"), ln=True)
        self.cell(0, 8, ascii_safe(f"Callsign: {callsign}"), ln=True)
        self.cell(0, 8, ascii_safe(f"Date: {date}"), ln=True)
        self.ln(18)
    def chart_section(self, title, img_bytes, ai_text, user_desc=""):
        self.add_page()
        self.section_header(title)
        if user_desc.strip():
            self.set_font("Arial", 'I', 11)
            self.set_text_color(70,70,70)
            self.cell(0, 7, ascii_safe(f"Area/focus: {user_desc.strip()}"), ln=True)
            self.set_text_color(0,0,0)
        self.ln(1)
        # Center and size image nicely
        chart_img_path = "tmp_chart.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.set_font("Arial", '', 11)
        # Always fit width but never more than 165mm
        self.image(chart_img_path, x=23, w=165)
        self.ln(7)
        clean_text = render_markdown_like(ai_text)
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ascii_safe(clean_text))
        self.ln(1)
    def met_section(self, raw_code, decoded, section_title="METAR/TAF/NOTAM"):
        self.section_header(section_title)
        self.set_font("Arial", 'I', 11)
        self.cell(0, 7, ascii_safe(raw_code), ln=True)
        self.ln(2)
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ascii_safe(render_markdown_like(decoded)))
        self.ln(2)

st.title("Preflight Briefing (SPC, SIGWX, NOTAMs, METARs, TAFs)")

with st.expander("1. Mission Information", expanded=True):
    mission = st.text_input("Mission (overview/route/objective)", "")
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")
    date = st.date_input("Date", datetime.date.today())

# --- SPC Chart ---
with st.expander("2. Surface Pressure Chart (SPC)", expanded=True):
    spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
    if "spc_full_bytes" not in st.session_state:
        st.session_state["spc_full_bytes"] = None
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
        _, spc_full_bytes = downscale_image(spc_img)
        st.session_state["spc_full_bytes"] = spc_full_bytes
        st.image(spc_img, caption="SPC: Full Chart (included in PDF)")
        cropped_spc = st_cropper(
            spc_img,
            aspect_ratio=None,
            box_color='red',
            return_type='image',
            realtime_update=True,
            key="spc_crop"
        )
        st.image(cropped_spc, caption="SPC: Cropped Area (for analysis)")
        spc_desc = st.text_input("SPC: Area/focus for analysis (optional)", value=st.session_state["spc_desc"], key="spcdesc")
        cropped_spc, cropped_spc_bytes = downscale_image(cropped_spc)
        st.session_state["cropped_spc_bytes"] = cropped_spc_bytes
        st.session_state["spc_desc"] = spc_desc

# --- SIGWX Chart ---
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
        _, sigwx_img_bytes = downscale_image(sigwx_img)
        st.session_state["sigwx_img_bytes"] = sigwx_img_bytes
        st.image(sigwx_img, caption="SIGWX: Full Chart (included in PDF)")
        sigwx_desc = st.text_input("SIGWX: Area/focus for analysis (default: Portugal)", value=st.session_state["sigwx_desc"], key="sigwxdesc")
        st.session_state["sigwx_desc"] = sigwx_desc

# --- NOTAMs/METARs/TAFs (Multiple) ---
with st.expander("4. NOTAMs, METARs, TAFs"):
    notams = st.text_area("Paste NOTAMs here (one per line, or blank):", height=60, key="notam_area")
    metars = st.text_area("Paste METARs here (one per line, or blank):", height=60, key="metar_area")
    tafs = st.text_area("Paste TAFs here (one per line, or blank):", height=60, key="taf_area")

ready = st.session_state.get("spc_full_bytes") and st.session_state.get("cropped_spc_bytes") and st.session_state.get("sigwx_img_bytes")
if ready:
    if st.button("Generate PDF Report"):
        with st.spinner("Preparing your preflight briefing..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=12)
            pdf.cover_page(mission, pilot, aircraft, str(date), callsign)
            # SPC
            spc_base64 = base64.b64encode(st.session_state["cropped_spc_bytes"].getvalue()).decode("utf-8")
            spc_ai_text = ai_chart_analysis(spc_base64, "SPC", st.session_state["spc_desc"])
            pdf.chart_section(
                title="Surface Pressure Chart (SPC)",
                img_bytes=st.session_state["spc_full_bytes"],
                ai_text=spc_ai_text,
                user_desc=st.session_state["spc_desc"]
            )
            # SIGWX
            sigwx_base64 = base64.b64encode(st.session_state["sigwx_img_bytes"].getvalue()).decode("utf-8")
            sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", st.session_state["sigwx_desc"])
            pdf.chart_section(
                title="Significant Weather Chart (SIGWX)",
                img_bytes=st.session_state["sigwx_img_bytes"],
                ai_text=sigwx_ai_text,
                user_desc=st.session_state["sigwx_desc"]
            )
            # NOTAMs
            for n in (notams or "").split('\n'):
                n = n.strip()
                if n:
                    decoded = ai_decode(n, "NOTAM")
                    pdf.met_section(n, decoded, section_title="NOTAM")
            # METARs
            for m in (metars or "").split('\n'):
                m = m.strip()
                if m:
                    decoded = ai_decode(m, "METAR")
                    pdf.met_section(m, decoded, section_title="METAR")
            # TAFs
            for t in (tafs or "").split('\n'):
                t = t.strip()
                if t:
                    decoded = ai_decode(t, "TAF")
                    pdf.met_section(t, decoded, section_title="TAF")
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
    st.info("Upload and crop the SPC, upload the SIGWX, and fill the info above before generating your PDF.")

st.caption("Charts are included in the PDF. Paste NOTAMs, METARs, TAFs for natural-language decoding. Layout matches Mass & Balance PDF style. For further customizations, ask!")


