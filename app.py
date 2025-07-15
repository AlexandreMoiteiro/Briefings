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
import airportsdata
from metar.Metar import Metar
import requests
import json

# --- EMAIL SETTINGS ---
ADMIN_EMAIL = "alexandre.moiteiro@gmail.com"
WEBSITE_LINK = "https://mass-balance.streamlit.app/"
SENDGRID_API_KEY = st.secrets["SENDGRID_API_KEY"]
SENDER_EMAIL = "alexandre.moiteiro@students.sevenair.com"

openai.api_key = st.secrets["OPENAI_API_KEY"]
AIRPORTS = airportsdata.load('ICAO')

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

def get_aerodrome_info(icao):
    info = AIRPORTS.get(icao.upper())
    if not info:
        return "", icao.upper()
    lat = f"{abs(info['lat']):.4f}{'N' if info['lat'] >= 0 else 'S'}"
    lon = f"{abs(info['lon']):.4f}{'E' if info['lon'] >= 0 else 'W'}"
    name = info['name'].title()
    return f"{name}, {info['country']} {lat} {lon}", name.upper()

def clean_markdown(text):
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"#+\s?", "", text)
    text = re.sub(r"[*•\-]\s+", "", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"[_`]", "", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()

def ai_chart_analysis(img_base64, chart_type, user_area_desc):
    sys_prompt = (
        "You are a student pilot preparing a preflight weather briefing. Analyze the attached aviation weather chart, focusing on the specified area but also considering the broader context and any significant patterns, movements, or developments shown elsewhere in the chart that could influence conditions in your area during the period of interest. "
        "Describe how weather systems, trends, and nearby phenomena could evolve and impact the area of focus, including possible changes or risks during the flight window. "
        "Avoid bullets, bold, lists, or headings. Write a detailed, readable, and practical paragraph as a student would brief out loud. "
        "Mention the key weather features (fronts, clouds, winds, visibility, temperatures, pressure, hazards), and connect them to both the local area and the bigger weather picture."
    )
    area = user_area_desc.strip() or "Portugal"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Focus on: {area}."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                ]
            }
        ],
        max_tokens=800,
        temperature=0.35
    )
    return clean_markdown(response.choices[0].message.content)

def ai_sigmet_summary(sigmet_text):
    prompt = (
        "You are a student pilot. Given these SIGMET/AIRMET/GAMET en-route weather warnings, write a short flowing English summary, no more than a paragraph, in practical preflight style. "
        "Do NOT use bullet points or formatting. Mention the key weather hazards, their likely effect on the route, and main recommendations."
    )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": sigmet_text}
        ],
        max_tokens=160,
        temperature=0.25
    )
    return clean_markdown(response.choices[0].message.content.strip())

def brief_metar_taf_comment(metar_code, taf_code):
    prompt = (
        "Given this METAR and TAF, write a very brief and practical summary for pilots (one or two sentences max). "
        "Mention main weather concerns or favorable aspects, but keep it short and simple. No formatting or Markdown, just clear English."
    )
    content = f"METAR:\n{metar_code}\nTAF:\n{taf_code}"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": content}
        ],
        max_tokens=90,
        temperature=0.2
    )
    return clean_markdown(response.choices[0].message.content.strip())

def brief_notam_comment(notams, icao):
    text = "\n".join([f"{n['num']}: {n['text']}" for n in notams if n['num'].strip() or n['text'].strip()])
    if not text.strip():
        return ""
    prompt = (
        f"You are a student pilot. Given these NOTAMs for {icao}, write a very brief summary (one or two sentences, no formatting) of the main operational points and anything of special attention. Only mention what is truly relevant."
    )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text}
        ],
        max_tokens=90,
        temperature=0.18
    )
    return clean_markdown(response.choices[0].message.content.strip())

# decode_metar and decode_taf as previously defined ...

class BriefingPDF(FPDF):
    def header(self): pass
    def footer(self):
        self.set_y(-13)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 7, ascii_safe(f"Page {self.page_no()}"), align='C')
    def section_header(self, title):
        self.set_font("Arial", 'B', 15)
        self.set_text_color(28, 44, 80)
        self.cell(0, 10, ascii_safe(title), ln=True)
        self.set_draw_color(70, 130, 180)
        self.set_line_width(1.0)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(5)
        self.set_line_width(0.2)
    def add_section_page(self, title):
        self.add_page()
        self.section_header(title)
    def cover_page(self, pilot, aircraft, date, time_utc, callsign, mission):
        self.add_page()
        self.set_xy(0,38)
        self.set_font("Arial", 'B', 23)
        self.set_text_color(28, 44, 80)
        self.cell(0, 15, ascii_safe("Preflight Weather Briefing & NOTAMs"), ln=True, align='C')
        self.ln(10)
        self.set_font("Arial", '', 14)
        self.set_text_color(44,44,44)
        self.cell(0, 8, ascii_safe(f"Pilot: {pilot}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Aircraft: {aircraft}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Callsign: {callsign}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Mission #: {mission}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Date: {date}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Flight Time (UTC): {time_utc}"), ln=True, align='C')
        self.ln(30)
    def chart_section(self, title, img_bytes, ai_text, user_desc="", source=None):
        section_title = title if not source else f"{title} ({source})"
        self.add_section_page(section_title)
        if user_desc.strip():
            self.set_font("Arial", 'I', 11)
            self.set_text_color(70,70,70)
            self.cell(0, 7, ascii_safe(f"Area/focus: {user_desc.strip()}"), ln=True)
            self.set_text_color(0,0,0)
        self.ln(2)
        chart_img_path = f"tmp_chart_{ascii_safe(section_title).replace(' ','_')}.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.set_font("Arial", '', 11)
        self.image(chart_img_path, x=22, w=168)
        self.ln(7)
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ascii_safe(ai_text))
        self.ln(2)
    # ... metar_taf_section, enroute_section, notam_section as in previous code ...

def send_report_email(to_email, subject, body, filename, filedata):
    html_body = f"""
    <html>
    <body>
        <h2>Weather & NOTAM Briefing Submitted</h2>
        <pre>{body}</pre>
        <p style='margin-top:1.5em;'>See attached PDF for details.</p>
        <p>Generated via {WEBSITE_LINK}</p>
    </body>
    </html>
    """
    data = {
        "personalizations": [
            {
                "to": [{"email": to_email}],
                "subject": subject
            }
        ],
        "from": {"email": SENDER_EMAIL},
        "content": [
            {
                "type": "text/html",
                "value": html_body
            }
        ],
        "attachments": [{
            "content": base64.b64encode(filedata).decode(),
            "type": "application/pdf",
            "filename": filename,
            "disposition": "attachment"
        }]
    }
    headers = {
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type": "application/json"
    }
    resp = requests.post("https://api.sendgrid.com/v3/mail/send", data=json.dumps(data), headers=headers)
    if resp.status_code >= 400:
        st.warning(f"PDF generated but failed to send email (SendGrid error: {resp.text})")

# --- Streamlit UI ---

# (METAR/TAF, NOTAM, SIGMET blocks as before...)

with st.expander("3. Significant Weather Charts (SIGWX)", expanded=True):
    if "sigwx_charts" not in st.session_state:
        st.session_state["sigwx_charts"] = []
    num_sigwx = st.number_input("Number of SIGWX charts", min_value=1, max_value=4, value=len(st.session_state["sigwx_charts"]) or 1, step=1, key="num_sigwx")
    # Ensure list is the correct size
    while len(st.session_state["sigwx_charts"]) < num_sigwx:
        st.session_state["sigwx_charts"].append({"file":None, "img_bytes":None, "desc":"Portugal", "source":""})
    while len(st.session_state["sigwx_charts"]) > num_sigwx:
        st.session_state["sigwx_charts"].pop()
    for idx, sig in enumerate(st.session_state["sigwx_charts"]):
        with st.expander(f"SIGWX Chart {idx+1}", expanded=True):
            sig["file"] = st.file_uploader(f"Upload SIGWX Chart {idx+1}", type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"sigwx{idx}")
            sig["source"] = st.text_input("Source", value=sig.get("source",""), key=f"sigwxsrc{idx}")
            sig["desc"] = st.text_input("SIGWX: Area/focus for analysis", value=sig.get("desc","Portugal"), key=f"sigwxdesc{idx}")
            if sig["file"]:
                if sig["file"].type == "application/pdf":
                    pdf_bytes = sig["file"].read()
                    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = pdf_doc.load_page(0)
                    pix = page.get_pixmap()
                    sig_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
                else:
                    sig_img = Image.open(sig["file"]).convert("RGB").copy()
                _, sig_img_bytes = downscale_image(sig_img)
                sig["img_bytes"] = sig_img_bytes
                st.image(sig_img, caption="SIGWX Chart (full chart will be included in PDF)")

with st.expander("4. Wind and Temperature Chart", expanded=True):
    wind_temp_file = st.file_uploader("Upload Wind and Temperature Chart (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="windtemp")
    wind_temp_desc = st.text_input("Wind/Temp Chart: Area/focus for analysis", value="Portugal", key="windtempdesc")
    wind_temp_bytes = None
    if wind_temp_file:
        if wind_temp_file.type == "application/pdf":
            pdf_bytes = wind_temp_file.read()
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = pdf_doc.load_page(0)
            pix = page.get_pixmap()
            wind_temp_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
        else:
            wind_temp_img = Image.open(wind_temp_file).convert("RGB").copy()
        _, wind_temp_bytes = downscale_image(wind_temp_img)
        st.image(wind_temp_img, caption="Wind and Temperature Chart (included in PDF)")

with st.expander("5. Surface Pressure Chart (SPC)", expanded=True):
    spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
    spc_desc = st.text_input("SPC: Area/focus for analysis", value="Portugal", key="spcdesc")
    spc_full_bytes = None
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
        st.image(spc_img, caption="SPC: Full Chart (included in PDF)")

# ... sigmet_block, notam_block, and the PDF/email generation as before

ready = (
    (spc_full_bytes is not None) and
    any(sig.get("img_bytes") for sig in st.session_state.get("sigwx_charts", [])) and
    (wind_temp_bytes is not None)
)
if ready:
    if st.button("Generate PDF Report"):
        with st.spinner("Preparing your preflight briefing..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=14)
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission)
            # METAR/TAF
            metar_taf_pairs = [
                entry for entry in st.session_state.metar_taf_pairs
                if entry['metar'].strip() or entry['taf'].strip() or entry['icao'].strip()
            ]
            if metar_taf_pairs:
                pdf.metar_taf_section(metar_taf_pairs)
            # SIGMET/AIRMET/GAMET
            sigmet_gamet_text = st.session_state.get("sigmet_area", "")
            sigmet_ai_summary = ai_sigmet_summary(sigmet_gamet_text) if sigmet_gamet_text.strip() else ""
            pdf.enroute_section(sigmet_gamet_text, sigmet_ai_summary)
            # SIGWX (each, with source)
            for sig in st.session_state["sigwx_charts"]:
                if sig.get("img_bytes"):
                    sigwx_base64 = base64.b64encode(sig["img_bytes"].getvalue()).decode("utf-8")
                    sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", sig["desc"])
                    pdf.chart_section(
                        title="Significant Weather Chart (SIGWX)",
                        img_bytes=sig["img_bytes"],
                        ai_text=sigwx_ai_text,
                        user_desc=sig["desc"],
                        source=sig["source"]
                    )
            # Wind/Temp Chart
            windtemp_base64 = base64.b64encode(wind_temp_bytes.getvalue()).decode("utf-8")
            windtemp_ai_text = ai_chart_analysis(windtemp_base64, "Wind/Temp", wind_temp_desc)
            pdf.chart_section(
                title="Wind and Temperature Chart",
                img_bytes=wind_temp_bytes,
                ai_text=windtemp_ai_text,
                user_desc=wind_temp_desc,
                source=None
            )
            # SPC (Full chart!)
            spc_base64 = base64.b64encode(spc_full_bytes.getvalue()).decode("utf-8")
            spc_ai_text = ai_chart_analysis(spc_base64, "SPC", spc_desc)
            pdf.chart_section(
                title="Surface Pressure Chart (SPC)",
                img_bytes=spc_full_bytes,
                ai_text=spc_ai_text,
                user_desc=spc_desc
            )
            pdf.notam_section(st.session_state.notam_data)
            out_pdf = f"weather_and_notam_{ascii_safe(mission)}.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                pdf_bytes = f.read()
                st.download_button(
                    label="Download Preflight Weather Briefing PDF",
                    data=pdf_bytes,
                    file_name=out_pdf,
                    mime="application/pdf"
                )
            # Email to admin
            try:
                email_body = (
                    f"Pilot: {pilot}\n"
                    f"Aircraft: {aircraft}\n"
                    f"Callsign: {callsign}\n"
                    f"Mission: {mission}\n"
                    f"Date: {date}\n"
                    f"Expected Time (UTC): {time_utc}\n"
                    f"PDF attached."
                )
                send_report_email(
                    ADMIN_EMAIL,
                    subject=f"Weather/NOTAM Report submitted: Mission {mission}",
                    body=email_body,
                    filename=out_pdf,
                    filedata=pdf_bytes
                )
                st.success("PDF generated and sent to admin!")
            except Exception as e:
                st.warning(f"PDF generated, but failed to email admin: {e}")
else:
    st.info("Fill all sections and upload all charts before generating your PDF.")





