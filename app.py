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
    sys_prompt = (
        "Write a preflight weather analysis in the first person plural (e.g., 'We should expect'), in natural, student-like language, for the cropped area of the chart. "
        "No bullet points. Summarize the weather in a couple of sentences, mentioning clouds, winds, hazards, etc. Do not mention automation or AI."
    )
    area = user_area_desc.strip() or "Portugal"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Please focus on: {area}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                ]
            }
        ],
        max_tokens=420,
        temperature=0.3
    )
    return response.choices[0].message.content

def render_markdown_like(text):
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
        pass
    def footer(self):
        self.set_y(-13)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 7, ascii_safe(f"Page {self.page_no()}"), align='C')
    def section_header(self, title):
        self.set_font("Arial", 'B', 14)
        self.set_text_color(0,0,0)
        self.cell(0, 9, ascii_safe(title), ln=True)
        self.set_draw_color(70, 130, 180)
        self.set_line_width(0.8)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)
        self.set_line_width(0.2)
    def cover_page(self, pilot, aircraft, date, callsign):
        self.add_page()
        self.set_xy(0,30)
        self.set_font("Arial", 'B', 21)
        self.set_text_color(20,20,40)
        self.cell(0, 14, ascii_safe("Preflight Weather Briefing and NOTAMs"), ln=True, align='C')
        self.ln(8)
        self.set_font("Arial", '', 13)
        self.set_text_color(0,0,0)
        self.cell(0, 8, ascii_safe(f"Pilot: {pilot}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Aircraft: {aircraft}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Callsign: {callsign}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Date: {date}"), ln=True, align='C')
        self.ln(10)
    def pair_section(self, section_title, pairs):
        self.section_header(section_title)
        for i, (code, decode) in enumerate(pairs, 1):
            self.set_font("Arial", 'B', 12)
            self.cell(0, 7, ascii_safe(f"{section_title[:-1]} #{i}"), ln=True)
            self.set_font("Arial", '', 11)
            self.multi_cell(0, 7, ascii_safe(code))
            self.set_font("Arial", 'I', 11)
            self.multi_cell(0, 7, ascii_safe(decode))
            self.ln(2)
    def chart_section(self, title, img_bytes, ai_text, user_desc=""):
        self.add_page()
        self.section_header(title)
        if user_desc.strip():
            self.set_font("Arial", 'I', 11)
            self.set_text_color(70,70,70)
            self.cell(0, 7, ascii_safe(f"Area/focus: {user_desc.strip()}"), ln=True)
            self.set_text_color(0,0,0)
        self.ln(1)
        chart_img_path = "tmp_chart.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.set_font("Arial", '', 11)
        self.image(chart_img_path, x=23, w=165)
        self.ln(7)
        clean_text = render_markdown_like(ai_text)
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ascii_safe(clean_text))
        self.ln(1)
    def conclusion(self):
        self.section_header("Conclusion")
        self.set_font("Arial", '', 12)
        txt = (
            "Dispatch criteria include assessing weather conditions for both departure and arrival, "
            "ensuring that the meteorological minima and operational requirements are met, "
            "and verifying the suitability of NOTAMs and other operational information."
        )
        self.multi_cell(0,8, ascii_safe(txt))
        self.ln(2)

st.title("Preflight Weather Briefing and NOTAMs")

with st.expander("1. Pilot/Aircraft Info", expanded=True):
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")
    date = st.date_input("Date", datetime.date.today())

# Dynamic METAR input
if "metar_list" not in st.session_state:
    st.session_state.metar_list = [("", "")]
st.subheader("2. METARs")
remove_metar = st.button("Remove last METAR") if len(st.session_state.metar_list) > 1 else None
for i, (metar, metar_decoded) in enumerate(st.session_state.metar_list):
    col1, col2 = st.columns(2)
    with col1:
        st.session_state.metar_list[i] = (
            st.text_area(f"METAR #{i+1} (raw code)", value=metar, key=f"metar_{i}"),
            st.session_state.metar_list[i][1]
        )
    with col2:
        st.session_state.metar_list[i] = (
            st.session_state.metar_list[i][0],
            st.text_area(f"METAR #{i+1} (decoded/summary)", value=metar_decoded, key=f"metar_decoded_{i}")
        )
if st.button("Add another METAR"):
    st.session_state.metar_list.append(("", ""))

if remove_metar:
    st.session_state.metar_list.pop()

# Dynamic TAF input
if "taf_list" not in st.session_state:
    st.session_state.taf_list = [("", "")]
st.subheader("3. TAFs")
remove_taf = st.button("Remove last TAF") if len(st.session_state.taf_list) > 1 else None
for i, (taf, taf_decoded) in enumerate(st.session_state.taf_list):
    col1, col2 = st.columns(2)
    with col1:
        st.session_state.taf_list[i] = (
            st.text_area(f"TAF #{i+1} (raw code)", value=taf, key=f"taf_{i}"),
            st.session_state.taf_list[i][1]
        )
    with col2:
        st.session_state.taf_list[i] = (
            st.session_state.taf_list[i][0],
            st.text_area(f"TAF #{i+1} (decoded/summary)", value=taf_decoded, key=f"taf_decoded_{i}")
        )
if st.button("Add another TAF"):
    st.session_state.taf_list.append(("", ""))

if remove_taf:
    st.session_state.taf_list.pop()

with st.expander("4. Significant Weather Chart (SIGWX)", expanded=True):
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

with st.expander("5. Surface Pressure Chart (SPC)", expanded=True):
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

with st.expander("6. NOTAMs (optional)"):
    notams = st.text_area("Paste NOTAMs (one per line):", height=80, key="notam_area")

ready = (
    st.session_state.get("spc_full_bytes")
    and st.session_state.get("cropped_spc_bytes")
    and st.session_state.get("sigwx_img_bytes")
)

if ready:
    if st.button("Generate PDF Report"):
        with st.spinner("Preparing your preflight briefing..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=12)
            pdf.cover_page(pilot, aircraft, str(date), callsign)

            # METARs
            metar_pairs = [
                (metar, decode)
                for metar, decode in st.session_state.metar_list
                if metar.strip() or decode.strip()
            ]
            if metar_pairs:
                pdf.pair_section("METARs", metar_pairs)

            # TAFs
            taf_pairs = [
                (taf, decode)
                for taf, decode in st.session_state.taf_list
                if taf.strip() or decode.strip()
            ]
            if taf_pairs:
                pdf.pair_section("TAFs", taf_pairs)

            # SIGWX
            sigwx_base64 = base64.b64encode(st.session_state["sigwx_img_bytes"].getvalue()).decode("utf-8")
            sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", st.session_state["sigwx_desc"])
            pdf.chart_section(
                title="Significant Weather Chart (SIGWX)",
                img_bytes=st.session_state["sigwx_img_bytes"],
                ai_text=sigwx_ai_text,
                user_desc=st.session_state["sigwx_desc"]
            )

            # SPC
            spc_base64 = base64.b64encode(st.session_state["cropped_spc_bytes"].getvalue()).decode("utf-8")
            spc_ai_text = ai_chart_analysis(spc_base64, "SPC", st.session_state["spc_desc"])
            pdf.chart_section(
                title="Surface Pressure Chart (SPC)",
                img_bytes=st.session_state["spc_full_bytes"],
                ai_text=spc_ai_text,
                user_desc=st.session_state["spc_desc"]
            )

            # Conclusion
            pdf.conclusion()

            # NOTAMs
            notam_lines = [n for n in (notams or "").split('\n') if n.strip()]
            if notam_lines:
                pdf.section_header("NOTAM Information Pertinent to Operational Areas")
                pdf.set_font("Arial", '', 11)
                for n in notam_lines:
                    pdf.multi_cell(0, 8, ascii_safe(n))
                    pdf.ln(1)

            out_pdf = "Preflight_Weather_Briefing.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                st.download_button(
                    label="Download Preflight Weather Briefing PDF",
                    data=f,
                    file_name=out_pdf,
                    mime="application/pdf"
                )
            st.success("PDF generated successfully!")
else:
    st.info("Fill all sections and upload/crop both charts before generating your PDF.")

st.caption("Add as many METAR or TAF/decoded pairs as needed. Charts are analyzed automatically. NOTAMs included as plain text.")

