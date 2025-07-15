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

def ai_pdf_summary(pdf_bytes):
    # Extract text from the first page and ask for summary in first person
    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = pdf_doc[0].get_text().strip()
    sys_prompt = (
        "You are a student pilot writing a briefing. "
        "Summarize the following mission objectives in the first person, naturally and simply, as if you are briefing your instructor. Be concise, direct, and do not use headings, just a short paragraph."
    )
    user_prompt = f"MISSION OBJECTIVES:\n{text}"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt}
        ],
        max_tokens=300,
        temperature=0.4
    )
    return response.choices[0].message.content

def ai_chart_analysis(img_base64, chart_type, user_area_desc):
    if chart_type == "SPC":
        sys_prompt = (
            "Write a preflight weather analysis for the cropped area of the Surface Pressure Chart (SPC). "
            "Use the first person ('I', 'we'), short sentences, and no more than 5-6 sentences. "
            "Describe what I should expect in the area regarding synoptic situation, pressure, fronts, expected winds, clouds, VFR/IFR impact. "
            "Do not mention automation or use technical language."
        )
    else:
        sys_prompt = (
            "Write a first-person, short analysis of the Significant Weather Chart (SIGWX) for preflight briefing. "
            "No more than 5-6 sentences, use 'I/we', and speak simply: main weather, turbulence, icing, visibility, hazards for the described area."
        )
    user_prompt = f"Focus on: {user_area_desc.strip()}" if user_area_desc.strip() else "Focus on Portugal."
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
        max_tokens=420,
        temperature=0.5
    )
    return response.choices[0].message.content

def ai_decode(code, code_type):
    prompts = {
        "NOTAM": (
            "Rewrite this NOTAM briefly and in first person, with all abbreviations decoded and only the relevant operational info, as I would say in a preflight briefing. Max 2-3 sentences."
        ),
        "METAR": (
            "Decode this METAR in first person and briefly, as a student would summarize to their instructor in a preflight briefing. No more than 3-4 sentences. Use simple, clear language and mention VFR/IFR if needed."
        ),
        "TAF": (
            "Decode this TAF briefly, in first person, as a student would summarize to their instructor. Max 3-4 sentences. Focus on what I/we should expect and main changes in the period."
        ),
        "GAMET": (
            "Summarize this GAMET in first person and briefly, as a student would in a preflight briefing. No more than 3-4 sentences. Focus on practical operational impacts."
        ),
    }
    sys_prompt = prompts.get(code_type, "Rewrite this code in plain language, in first person, briefly.")
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": code}
        ],
        max_tokens=350,
        temperature=0.3
    )
    return response.choices[0].message.content

def ai_conclusion(mission, metars, tafs, gamets, spc_text, sigwx_text):
    # Use all the pieces for a conclusion in first person
    summary = f"""
Mission: {mission}
Weather briefing METARs: {metars}
TAFs: {tafs}
GAMETs: {gamets}
SIGWX: {sigwx_text}
SPC: {spc_text}
"""
    sys_prompt = (
        "You are a student pilot finishing a preflight briefing. "
        "Write a short, first-person conclusion (dispatch criteria) considering all previous weather, focusing on whether conditions for departure and arrival are met, and mentioning any doubts or cautions. No more than 3-4 sentences."
    )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": summary}
        ],
        max_tokens=200,
        temperature=0.4
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
        # Understated, no big block
        if self.page_no() == 1:
            pass
        else:
            self.set_font('Arial', 'B', 13)
            self.cell(0, 8, ascii_safe("Preflight Briefing"), align='C', ln=1)
            self.ln(2)
    def footer(self):
        self.set_y(-10)
        self.set_font('Arial', 'I', 7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 7, ascii_safe(f"Page {self.page_no()}"), align='C')
    def section_header(self, title):
        self.set_font("Arial", 'B', 13)
        self.set_text_color(0,0,0)
        self.cell(0, 8, ascii_safe(title), ln=True)
        self.set_draw_color(120, 120, 120)
        self.set_line_width(0.6)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)
        self.set_line_width(0.2)
    def cover_page(self, mission, callsign, date):
        self.add_page()
        self.set_font("Arial", 'B', 16)
        self.set_text_color(30,30,30)
        self.cell(0, 12, ascii_safe("Preflight Briefing Package"), ln=True, align='C')
        self.ln(6)
        self.set_font("Arial", '', 12)
        self.cell(0, 9, ascii_safe(f"Mission: {mission}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Callsign: {callsign}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Date: {date}"), ln=True, align='C')
        self.ln(12)
    def add_section(self, title, body):
        self.section_header(title)
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ascii_safe(render_markdown_like(body)))
        self.ln(2)
    def chart_section(self, title, img_bytes, ai_text, user_desc=""):
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
        self.image(chart_img_path, x=25, w=160)
        self.ln(6)
        clean_text = render_markdown_like(ai_text)
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ascii_safe(clean_text))
        self.ln(2)
    def met_section(self, raw_code, decoded, section_title="METAR/TAF/NOTAM"):
        self.section_header(section_title)
        self.set_font("Arial", 'I', 11)
        self.cell(0, 7, ascii_safe(raw_code), ln=True)
        self.ln(1)
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ascii_safe(render_markdown_like(decoded)))
        self.ln(2)

st.title("Preflight Briefing (Mission, Weather, NOTAMs)")

with st.expander("1. Mission Objectives PDF", expanded=True):
    mission_pdf_file = st.file_uploader("Upload the MISSION OBJECTIVES PDF (only the relevant page is used):", type=["pdf"], key="missionpdf")
    mission_objective_summary = ""
    if mission_pdf_file:
        mission_pdf_bytes = mission_pdf_file.read()
        mission_objective_summary = ai_pdf_summary(mission_pdf_bytes)
        st.success("Mission objectives summary ready!")

with st.expander("2. Flight Info", expanded=True):
    callsign = st.text_input("Callsign", "")
    date = st.date_input("Date", datetime.date.today())
    time_slot = st.text_input("Designated time slot", "")

# --- METAR/TAF/GAMET
with st.expander("3. Weather Briefing (METAR/TAF/GAMET)", expanded=True):
    metars = st.text_area("Paste METARs here (one per line):", height=80, key="metar_area")
    tafs = st.text_area("Paste TAFs here (one per line):", height=80, key="taf_area")
    gamets = st.text_area("Paste GAMETs here (one per line):", height=80, key="gamet_area")

# --- SIGWX Chart
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

# --- SPC Chart
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

# --- NOTAMs ---
with st.expander("6. NOTAMs (optional):"):
    notams = st.text_area("Paste NOTAMs here (one per line):", height=80, key="notam_area")

ready = mission_pdf_file and st.session_state.get("sigwx_img_bytes") and st.session_state.get("spc_full_bytes") and st.session_state.get("cropped_spc_bytes")
if ready:
    if st.button("Generate PDF Report"):
        with st.spinner("Preparing your preflight briefing..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=13)
            # Cover page: only mission, callsign, date
            pdf.cover_page(mission_objective_summary, callsign, str(date))

            # 1. Mission objectives (already summarized)
            pdf.add_section("Summary of Mission Objectives, Time Slot and Callsign", mission_objective_summary + (f"\n\nDesignated Time Slot: {time_slot}\nCallsign: {callsign}" if time_slot or callsign else ""))

            # 2. Weather Briefing: METAR/TAF/GAMET (decoded in first person, short)
            metar_decoded = ""
            for m in (metars or "").split('\n'):
                m = m.strip()
                if m:
                    metar_decoded += ai_decode(m, "METAR") + "\n"
            taf_decoded = ""
            for t in (tafs or "").split('\n'):
                t = t.strip()
                if t:
                    taf_decoded += ai_decode(t, "TAF") + "\n"
            gamet_decoded = ""
            for g in (gamets or "").split('\n'):
                g = g.strip()
                if g:
                    gamet_decoded += ai_decode(g, "GAMET") + "\n"
            weather_summary = metar_decoded + taf_decoded + gamet_decoded
            pdf.add_section("Weather Briefing: METAR/TAFs/GAMET", weather_summary.strip())

            # 3. SIGWX (analyze with AI)
            sigwx_base64 = base64.b64encode(st.session_state["sigwx_img_bytes"].getvalue()).decode("utf-8")
            sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", st.session_state["sigwx_desc"])
            pdf.chart_section(
                title="Significant Weather Chart (SIGWX)",
                img_bytes=st.session_state["sigwx_img_bytes"],
                ai_text=sigwx_ai_text,
                user_desc=st.session_state["sigwx_desc"]
            )

            # 4. SPC (analyze with AI; use crop for analysis, full chart in PDF)
            spc_base64 = base64.b64encode(st.session_state["cropped_spc_bytes"].getvalue()).decode("utf-8")
            spc_ai_text = ai_chart_analysis(spc_base64, "SPC", st.session_state["spc_desc"])
            pdf.chart_section(
                title="Surface Pressure Chart (SPC)",
                img_bytes=st.session_state["spc_full_bytes"],
                ai_text=spc_ai_text,
                user_desc=st.session_state["spc_desc"]
            )

            # 5. Conclusion (AI)
            conclusion_text = ai_conclusion(
                mission_objective_summary,
                metar_decoded, taf_decoded, gamet_decoded,
                spc_ai_text, sigwx_ai_text
            )
            pdf.add_section("Conclusion / Dispatch Criteria", conclusion_text)

            # 6. NOTAMs (decoded)
            for n in (notams or "").split('\n'):
                n = n.strip()
                if n:
                    decoded = ai_decode(n, "NOTAM")
                    pdf.met_section(n, decoded, section_title="NOTAM")

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
    st.info("Upload all required files and inputs before generating your PDF.")

st.caption("Order: Mission Objectives summary, Weather Briefing, SIGWX, SPC, Conclusion/Dispatch, then NOTAMs.")


