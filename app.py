import streamlit as st
from PIL import Image
import openai
import io
import base64
from streamlit_cropper import st_cropper
from fpdf import FPDF
import fitz
import datetime
import re

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
    # Prompt for a *natural*, hand-written style
    if chart_type == "SPC":
        sys_prompt = (
            "Write a flight briefing in a natural, student-like style, as if you are writing it by hand after reviewing the surface pressure chart (SPC). "
            "Don't use bullet points or sections, just a coherent paragraph or two. Summarize the weather over the selected area, including synoptic situation, pressure systems, fronts, expected winds, clouds, and VFR/IFR implications. "
            "Do not mention that this was generated automatically or reference AI."
        )
    else:
        sys_prompt = (
            "Write a natural, student-like flight briefing after analyzing the significant weather chart (SIGWX). "
            "Do not use bullet points or headings, just write a few paragraphs as you would for a preflight briefing, focusing on clouds, turbulence, significant weather, freezing level, visibility, and any hazards for the area described. "
            "Do not reference AI or automation; it should read like a student's summary."
        )
    user_prompt = f"Please focus your analysis on: {user_area_desc.strip()}" if user_area_desc.strip() else "Please focus on Portugal."
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

def render_markdown_like(text):
    # Converts **text** and *text* to bold/italics in FPDF as a workaround
    # and tries to keep paragraphs clear.
    # For more complex markdown rendering, a full parser is needed (possible to upgrade).
    lines = text.split('\n')
    final = []
    for line in lines:
        # Remove markdown headings, convert bold, italics
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
            self.set_font('Arial', 'B', 14)
            self.cell(0, 7, "Preflight Briefing", align='C', ln=1)
            self.ln(2)
    def footer(self):
        self.set_y(-12)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 8, f"Page {self.page_no()}", align='C')
    def cover_page(self, mission, pilot, aircraft, date, callsign):
        self.add_page()
        self.set_font("Arial", 'B', 22)
        self.set_text_color(40,40,40)
        self.cell(0, 22, "Preflight Briefing", ln=True, align='C')
        self.ln(10)
        self.set_font("Arial", '', 13)
        self.set_text_color(0,0,0)
        self.cell(0, 8, f"Mission: {mission}", ln=True, align='C')
        self.cell(0, 8, f"Pilot: {pilot}", ln=True, align='C')
        self.cell(0, 8, f"Aircraft: {aircraft}", ln=True, align='C')
        self.cell(0, 8, f"Callsign: {callsign}", ln=True, align='C')
        self.cell(0, 8, f"Date: {date}", ln=True, align='C')
        self.ln(15)
    def chart_section(self, title, img_bytes, ai_text, user_desc=""):
        self.add_page()
        self.set_font("Arial", 'B', 15)
        self.cell(0, 10, title, ln=True)
        if user_desc.strip():
            self.set_font("Arial", 'I', 11)
            self.set_text_color(50,50,50)
            self.cell(0, 8, f"Area/focus: {user_desc.strip()}", ln=True)
            self.set_text_color(0,0,0)
        self.ln(2)
        self.set_font("Arial", '', 12)
        chart_img_path = "tmp_chart.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.image(chart_img_path, x=22, w=165)
        self.ln(6)
        self.set_font("Arial", '', 12)
        clean_text = render_markdown_like(ai_text)
        self.multi_cell(0, 8, clean_text)
        self.ln(2)

st.title("Preflight Briefing Package (SPC & SIGWX)")
st.caption("Fill out your mission, upload your SPC (crop for analysis) and SIGWX charts, specify area if needed, and generate your natural-style briefing PDF.")

# Mission info fields
with st.expander("1. Mission Information", expanded=True):
    mission = st.text_input("Mission (overview/route/objective)", "")
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")
    date = st.date_input("Date", datetime.date.today())

# SPC (Surface Pressure Chart) cropping (crop for AI, but include full image in PDF)
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
        # Save full SPC image for PDF
        _, spc_full_bytes = downscale_image(spc_img)
        st.session_state["spc_full_bytes"] = spc_full_bytes
        st.image(spc_img, caption="SPC: Full Chart (will be in PDF)")
        st.markdown("**Crop the SPC chart below (this crop is only for the AI analysis, not for the PDF). Then click 'Save SPC Crop'.**")
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
        if st.button("Save SPC Crop"):
            cropped_spc, cropped_spc_bytes = downscale_image(cropped_spc)
            st.session_state["cropped_spc_bytes"] = cropped_spc_bytes
            st.session_state["spc_desc"] = spc_desc
            st.success("SPC crop for analysis saved!")

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
        st.image(sigwx_img, caption="SIGWX: Full Chart (will be in PDF)")
        sigwx_desc = st.text_input("SIGWX: Area/focus for analysis (default: Portugal)", value=st.session_state["sigwx_desc"], key="sigwxdesc")
        if st.button("Save SIGWX Chart"):
            sigwx_img, sigwx_img_bytes = downscale_image(sigwx_img)
            st.session_state["sigwx_img_bytes"] = sigwx_img_bytes
            st.session_state["sigwx_desc"] = sigwx_desc
            st.success("SIGWX chart saved!")

# Enable PDF only when both are ready
ready = st.session_state.get("spc_full_bytes") and st.session_state.get("cropped_spc_bytes") and st.session_state.get("sigwx_img_bytes")
if ready:
    if st.button("Generate PDF Report"):
        with st.spinner("Preparing your preflight briefing..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=12)
            pdf.cover_page(mission, pilot, aircraft, str(date), callsign)
            # SPC: Use crop for analysis, full chart for PDF
            spc_base64 = base64.b64encode(st.session_state["cropped_spc_bytes"].getvalue()).decode("utf-8")
            spc_ai_text = ai_chart_analysis(spc_base64, "SPC", st.session_state["spc_desc"])
            pdf.chart_section(
                title="Surface Pressure Chart (SPC)",
                img_bytes=st.session_state["spc_full_bytes"],
                ai_text=spc_ai_text,
                user_desc=st.session_state["spc_desc"]
            )
            # SIGWX: Full chart, focus for analysis as given
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
    st.info("Crop and save the SPC for analysis, upload and save the SIGWX, then generate your briefing PDF.")

st.caption("The full SPC and SIGWX charts will be included in your PDF. Crop the SPC for analysis, upload SIGWX and specify area, and you'll get a briefing that sounds natural and ready to present.")






