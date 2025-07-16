import streamlit as st
from PIL import Image
import openai
import io
import base64
from fpdf import FPDF
import fitz
import datetime
import unicodedata
import re
import airportsdata
from metar.Metar import Metar
import requests
import json

from streamlit_cropper import st_cropper

ADMIN_EMAIL = "alexandre.moiteiro@gmail.com"
WEBSITE_LINK = "https://mass-balance.streamlit.app/"
SENDGRID_API_KEY = st.secrets["SENDGRID_API_KEY"]
SENDER_EMAIL = "alexandre.moiteiro@students.sevenair.com"

openai.api_key = st.secrets["OPENAI_API_KEY"]
AIRPORTS = airportsdata.load('ICAO')

st.set_page_config(
    page_title="Briefings Sevenair",
    page_icon="📑",
    layout="wide"
)

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

# -------- AI CHART ANÁLISE COMO INSTRUTOR (BULLET POINTS) --------
def ai_chart_analysis_instructor(img_base64, chart_type, user_area_desc, extra_instruction="", summarized=False):
    area = user_area_desc.strip() or "the selected area"
    prompt = (
        f"You are a meteorology instructor. Analyze this {chart_type} chart. "
        f"List in bullet points ALL meteorologically relevant features, symbols, or phenomena visible in the selected area ({area}), explaining their meaning and possible operational impact for flight. "
        "Do not summarize, explain in detail as if teaching a student who may be questioned about any feature. "
        "Be exhaustive: explain everything visible, including weather systems, jet streams, turbulence, cloud types, abbreviations, fronts, isobars, patterns. "
        "Use clear and didactic language. Respond in English."
    )
    if extra_instruction:
        prompt += " " + extra_instruction.strip()
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Selected area: {area}."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                ]
            }
        ],
        max_tokens=1100,
        temperature=0.19
    )
    return clean_markdown(response.choices[0].message.content)

# -------- METAR/TAF: DESCODIFICAÇÃO EM "NARRATIVA" --------
def decode_metar_narrative(metar_code):
    try:
        m = Metar(metar_code)
        info, name = get_aerodrome_info(m.station_id or "")
        parts = []
        if info:
            parts.append(f"{info}.")
        if m.time:
            parts.append(f"Observed on day {m.time.day:02d} at {m.time.hour:02d}:{m.time.minute:02d} UTC.")
        if m.wind_speed:
            ws = m.wind_speed.value('KT')
            wd = m.wind_dir.value() if m.wind_dir else None
            if wd:
                parts.append(f"Wind from {wd} degrees at {ws:.0f} knots.")
            else:
                parts.append(f"Wind variable at {ws:.0f} knots.")
        if m.vis:
            vis = m.vis.value('KM')
            if "CAVOK" in metar_code:
                parts.append("Visibility is 10 kilometers or more (CAVOK).")
            else:
                parts.append(f"Visibility is {vis} kilometers.")
        skystr = []
        if m.sky:
            cb = any([s[0] == "CB" for s in m.sky])
            if "CAVOK" in metar_code:
                skystr.append("No cloud below 1500 meters and no Cumulonimbus.")
            else:
                for s in m.sky:
                    typ, height = s[0], s[1]*30.48 if s[1] else None
                    if typ == "CB":
                        skystr.append("Cumulonimbus present.")
                    elif height is not None:
                        skystr.append(f"{typ} at {int(height)} meters.")
                if not skystr:
                    skystr.append("No significant clouds reported.")
            parts.extend(skystr)
        else:
            parts.append("No cloud below 1500 meters and no Cumulonimbus.")
        wx = getattr(m, "weather", [])
        if not wx or (len(wx) == 1 and wx[0] == ""):
            parts.append("No significant weather phenomena.")
        else:
            parts.append(f"Weather phenomena: {'; '.join(wx)}.")
        if m.temp:
            parts.append(f"Temperature {m.temp.value():.0f}°C.")
        if m.dewpt:
            parts.append(f"Dew Point {m.dewpt.value():.0f}°C.")
        if m.press:
            parts.append(f"QNH {m.press.value():.0f} hPa.")
        return " ".join(parts)
    except Exception as e:
        return f"Could not decode METAR: {e}"

def decode_taf_narrative(taf_code):
    airports = AIRPORTS
    match = re.search(r'\b([A-Z]{4})\b', taf_code)
    icao = match.group(1) if match else "UNKNOWN"
    info = airports.get(icao, None)
    name = info['name'].upper() if info else icao
    country = info['country'] if info else ""
    lat = info['lat'] if info else 0
    lon = info['lon'] if info else 0
    lat_str = f"{abs(lat):.4f}{'N' if lat >= 0 else 'S'}"
    lon_str = f"{abs(lon):.4f}{'E' if lon >= 0 else 'W'}"
    lines = [f"{icao}: {name}, {country} {lat_str} {lon_str}."]
    obs_time = re.search(r'(\d{2})(\d{2})(\d{2})Z', taf_code)
    if obs_time:
        lines.append(f"Forecast issued on day {obs_time.group(1)} at {obs_time.group(2)}:00 UTC.")
    period = re.search(r'(\d{2})(\d{2})/(\d{2})(\d{2})', taf_code)
    if period:
        lines.append(f"Valid from day {period.group(1)} at {period.group(2)}:00 UTC until day {period.group(3)} at {period.group(4)}:00 UTC.")
    taf_main = taf_code.split('\n')[0]
    wind_match = re.search(r'(VRB|\d{3})(\d{2,3})KT', taf_main)
    wind_dir = wind_match.group(1) if wind_match else "variable"
    wind_spd = wind_match.group(2) if wind_match else ""
    if wind_spd:
        lines.append(f"Wind {wind_dir if wind_dir != 'VRB' else 'variable'} at {wind_spd} knots.")
    vis_match = re.search(r' (\d{4}) ', taf_main)
    if "CAVOK" in taf_main or (vis_match and int(vis_match.group(1)) >= 9999):
        lines.append("Visibility 10 kilometers or more (CAVOK).")
    elif vis_match:
        lines.append(f"Visibility {int(vis_match.group(1))/1000:.0f} km.")
    clouds = []
    if "CAVOK" in taf_main:
        clouds.append("No cloud below 1500 meters and no Cumulonimbus.")
    else:
        cloud_matches = re.findall(r'(FEW|SCT|BKN|OVC)(\d{3})', taf_main)
        for typ, lvl in cloud_matches:
            height = int(lvl)*30.48
            clouds.append(f"{typ} at {int(height)} meters.")
        if not clouds:
            clouds.append("No significant clouds reported.")
    lines.extend(clouds)
    wx_match = re.search(r'(RA|SN|TS|FG|BR)', taf_main)
    if wx_match:
        lines.append(f"Weather phenomena: {wx_match.group(1)}.")
    else:
        lines.append("No significant weather phenomena.")
    return " ".join([l for l in lines if l.strip()])

# -------- PDF CLASSES --------
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
        self.cell(0, 15, ascii_safe("Preflight Weather Briefing"), ln=True, align='C')
        self.ln(10)
        self.set_font("Arial", '', 14)
        self.set_text_color(44,44,44)
        self.cell(0, 8, ascii_safe(f"Pilot: {pilot}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Aircraft: {aircraft}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Callsign: {callsign}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Mission: {mission}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Date: {date}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Flight Time (UTC): {time_utc}"), ln=True, align='C')
        self.ln(30)
    def metar_taf_section(self, pairs, decode=True):
        for i, entry in enumerate(pairs, 1):
            icao = entry['icao'].upper()
            info, aerodrome = get_aerodrome_info(icao)
            metar_code = entry['metar']
            taf_code = entry['taf']
            self.add_section_page(f"{icao} ({aerodrome})")
            self.set_font("Arial", 'B', 13)
            self.set_text_color(40,40,40)
            self.cell(0, 8, "METAR (Raw):", ln=True)
            self.set_font("Arial", '', 12)
            self.set_text_color(0,0,0)
            self.multi_cell(0, 8, ascii_safe(metar_code))
            self.ln(2)
            if decode and metar_code.strip():
                self.set_font("Arial", 'B', 13)
                self.set_text_color(40,40,40)
                self.cell(0, 8, "Decoded METAR:", ln=True)
                self.set_font("Arial", '', 12)
                self.set_text_color(0,0,0)
                self.multi_cell(0, 8, ascii_safe(decode_metar_narrative(metar_code)))
                self.ln(3)
            self.set_font("Arial", 'B', 13)
            self.set_text_color(40,40,40)
            self.cell(0, 8, "TAF (Raw):", ln=True)
            self.set_font("Arial", '', 12)
            self.set_text_color(0,0,0)
            self.multi_cell(0, 8, ascii_safe(taf_code))
            self.ln(2)
            if decode and taf_code.strip():
                self.set_font("Arial", 'B', 13)
                self.set_text_color(40,40,40)
                self.cell(0, 8, "Decoded TAF:", ln=True)
                self.set_font("Arial", '', 12)
                self.set_text_color(0,0,0)
                self.multi_cell(0, 8, ascii_safe(decode_taf_narrative(taf_code)))
                self.ln(4)
    def chart_section(self, title, img_bytes, ai_text, user_desc="", extra_labels=None, show_ai=True):
        self.add_section_page(title)
        if extra_labels:
            self.set_font("Arial", 'B', 12)
            for lab in extra_labels:
                self.cell(0, 7, ascii_safe(lab), ln=True)
        if user_desc.strip():
            self.set_font("Arial", 'I', 11)
            self.set_text_color(70,70,70)
            self.cell(0, 7, ascii_safe(f"Area/focus: {user_desc.strip()}"), ln=True)
            self.set_text_color(0,0,0)
        self.ln(2)
        chart_img_path = f"tmp_chart_{ascii_safe(title).replace(' ','_')}.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.set_font("Arial", '', 11)
        self.image(chart_img_path, x=22, w=168)
        self.ln(7)
        if show_ai and ai_text.strip():
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 8, ascii_safe(ai_text))
        self.ln(2)

def send_report_email(to_email, subject, body, filename, filedata):
    html_body = f"""
    <html>
    <body>
        <h2>Weather Briefing Submitted</h2>
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

# --------------- STREAMLIT APP -----------------
st.title("Preflight Weather Briefing")

with st.expander("1. Pilot/Aircraft Info", expanded=True):
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft (e.g., Tecnam P2008 (CS-ECD))", "")
    callsign = st.text_input("Callsign", "")
    mission = st.text_input("Mission", "")
    date = st.date_input("Date", datetime.date.today())
    time_utc = st.text_input("Expected Flight Time (UTC, e.g. 14:30-16:30)", "")

def metar_taf_block():
    if "metar_taf_pairs" not in st.session_state:
        st.session_state.metar_taf_pairs = [{"icao":"", "metar":"", "taf":""}]
    st.subheader("2. METAR/TAF by Aerodrome")
    remove_pair = st.button("Remove last Aerodrome") if len(st.session_state.metar_taf_pairs) > 1 else None
    for i, entry in enumerate(st.session_state.metar_taf_pairs):
        with st.expander(f"METAR/TAF for Aerodrome {i+1}", expanded=True):
            entry["icao"] = st.text_input("ICAO", value=entry["icao"], key=f"icao_{i}")
            entry["metar"] = st.text_area(f"METAR (raw code)", value=entry["metar"], key=f"metar_{i}")
            entry["taf"] = st.text_area(f"TAF (raw code)", value=entry["taf"], key=f"taf_{i}")
    if st.button("Add another Aerodrome"):
        st.session_state.metar_taf_pairs.append({"icao":"", "metar":"", "taf":""})
    if remove_pair:
        st.session_state.metar_taf_pairs.pop()

def chart_block_multi(
    chart_key, label, title_base,
    desc_label="Area/focus for analysis", extra_label="Extra instructions to AI (optional)",
    has_levels=False, has_source=False, ai_type=None, summarized=False
):
    if chart_key not in st.session_state:
        st.session_state[chart_key] = []
    st.subheader(label)
    chart_list = st.session_state[chart_key]
    for i in range(len(chart_list)):
        chart = chart_list[i]
        with st.expander(f"{label} {i+1}", expanded=True):
            if has_source:
                chart["source"] = st.text_input("Source/Organization", value=chart.get("source",""), key=f"{chart_key}_source_{i}")
            if has_levels:
                chart["levels"] = st.text_input("Applicable Flight Levels (e.g., FL050-FL120)", value=chart.get("levels",""), key=f"{chart_key}_levels_{i}")
            chart["desc"] = st.text_input(desc_label, value=chart.get("desc","Portugal"), key=f"{chart_key}_desc_{i}")
            chart["extra"] = st.text_area(extra_label, value=chart.get("extra",""), key=f"{chart_key}_extra_{i}")
            chart_file = st.file_uploader(
                f"Upload {label} (PDF, PNG, JPG, JPEG, GIF):",
                type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"{chart_key}_file_{i}"
            )
            if chart_file:
                if chart_file.type == "application/pdf":
                    pdf_bytes = chart_file.read()
                    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = pdf_doc.load_page(0)
                    img = Image.open(io.BytesIO(page.get_pixmap().tobytes("png"))).convert("RGB").copy()
                else:
                    img = Image.open(chart_file).convert("RGB").copy()
                _, img_bytes = downscale_image(img)
                chart["img_bytes"] = img_bytes
                st.image(img, caption="Full Chart (included in PDF)")

                # Crop switch
                crop_it = st.toggle("Crop chart before AI analysis?", value=chart.get("crop", True), key=f"{chart_key}_crop_switch_{i}")
                chart["crop"] = crop_it

                if crop_it:
                    st.info("Only the cropped area will be analyzed by AI, but the full chart will be attached to the PDF.")
                    cropped_img = st_cropper(
                        img,
                        aspect_ratio=None,
                        box_color='red',
                        return_type='image',
                        realtime_update=True,
                        key=f"{chart_key}_crop_{i}"
                    )
                    st.image(cropped_img, caption="Selected Area for AI Analysis")
                    _, cropped_bytes = downscale_image(cropped_img)
                else:
                    cropped_img = img
                    cropped_bytes = img_bytes
                    st.info("The entire chart will be analyzed by AI.")

                chart["cropped_img_bytes"] = cropped_bytes
                # AI analysis button label
                gen_label = "Generate AI analysis" if not chart.get("ai_text") else "Regenerate AI analysis"
                if st.button(f"{gen_label} {i+1}", key=f"{chart_key}_regen_{i}"):
                    img_b64 = base64.b64encode(cropped_bytes.getvalue()).decode("utf-8")
                    prompt_chart_type = ai_type or title_base
                    chart["ai_text"] = ai_chart_analysis_instructor(
                        img_b64,
                        chart_type=prompt_chart_type,
                        user_area_desc=chart["desc"],
                        extra_instruction=chart.get("extra", ""),
                        summarized=summarized
                    )
                if chart.get("ai_text"):
                    chart["ai_text"] = st.text_area(
                        "Edit/Approve AI Analysis", value=chart["ai_text"],
                        key=f"{chart_key}_aitxt_{i}", height=230
                    )
            else:
                chart["img_bytes"] = None
                chart["ai_text"] = ""
    addcol, rmcol = st.columns([0.24,0.24])
    if addcol.button(f"Add {label}"):
        new_chart = {"desc": "Portugal", "extra": "", "img_bytes": None, "ai_text": ""}
        if has_source: new_chart["source"] = ""
        if has_levels: new_chart["levels"] = ""
        st.session_state[chart_key].append(new_chart)
    if len(chart_list) > 1 and rmcol.button(f"Remove last {label}"):
        chart_list.pop()

# ---- PAGE BLOCKS ----
metar_taf_block()
chart_block_multi(
    chart_key="sigwx_charts", label="Significant Weather Chart (SIGWX)",
    title_base="Significant Weather Chart (SIGWX)", has_source=True
)
chart_block_multi(
    chart_key="windtemp_charts", label="Wind and Temperature Chart",
    title_base="Wind and Temperature Chart", has_levels=True, summarized=True
)
chart_block_multi(
    chart_key="spc_charts", label="Surface Pressure Chart (SPC)",
    title_base="Surface Pressure Chart (SPC)"
)

# ---- READY STATE ----
ready = (
    len([c for c in st.session_state.get("sigwx_charts", []) if c.get("img_bytes")]) > 0
    and len([c for c in st.session_state.get("windtemp_charts", []) if c.get("img_bytes")]) > 0
    and len([c for c in st.session_state.get("spc_charts", []) if c.get("img_bytes")]) > 0
)

# ------------ BOTÕES DE PDF RAW E DETALHADO -----------------
col1, col2 = st.columns(2)
if ready:
    if col1.button("Gerar PDF COMPLETO (detalhado)"):
        with st.spinner("Preparando PDF detalhado..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=14)
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission)
            metar_taf_pairs = [
                entry for entry in st.session_state.metar_taf_pairs
                if entry['metar'].strip() or entry['taf'].strip() or entry['icao'].strip()
            ]
            if metar_taf_pairs:
                pdf.metar_taf_section(metar_taf_pairs, decode=True)
            # SIGWX
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title=f"Significant Weather Chart (SIGWX) [{chart.get('source','')}]",
                        img_bytes=chart["img_bytes"],
                        ai_text=chart.get("ai_text",""),
                        user_desc=chart.get("desc",""),
                        extra_labels=[f"Source: {chart.get('source','')}".strip()] if chart.get('source','') else None,
                        show_ai=True
                    )
            # Wind/Temp
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title="Wind and Temperature Chart",
                        img_bytes=chart["img_bytes"],
                        ai_text=chart.get("ai_text",""),
                        user_desc=chart.get("desc",""),
                        extra_labels=[f"Flight Levels: {chart.get('levels','')}"] if chart.get('levels','') else None,
                        show_ai=True
                    )
            # SPC
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title="Surface Pressure Chart (SPC)",
                        img_bytes=chart["img_bytes"],
                        ai_text=chart.get("ai_text",""),
                        user_desc=chart.get("desc",""),
                        show_ai=True
                    )
            out_pdf = f"weather_briefing_detalhado_{ascii_safe(mission)}.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                pdf_bytes = f.read()
                st.download_button(
                    label="Download PDF Detalhado",
                    data=pdf_bytes,
                    file_name=out_pdf,
                    mime="application/pdf"
                )
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
                    subject=f"Weather Report submitted: Mission {mission}",
                    body=email_body,
                    filename=out_pdf,
                    filedata=pdf_bytes
                )
                st.success("PDF detalhado gerado e enviado para o admin!")
            except Exception as e:
                st.warning(f"PDF detalhado gerado, mas falhou o envio de email: {e}")

    if col2.button("Gerar PDF RAW (entregar ao instrutor)"):
        with st.spinner("Preparando PDF RAW..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=14)
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission)
            metar_taf_pairs = [
                entry for entry in st.session_state.metar_taf_pairs
                if entry['metar'].strip() or entry['taf'].strip() or entry['icao'].strip()
            ]
            if metar_taf_pairs:
                pdf.metar_taf_section(metar_taf_pairs, decode=False)
            # SIGWX
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title=f"Significant Weather Chart (SIGWX) [{chart.get('source','')}]",
                        img_bytes=chart["img_bytes"],
                        ai_text="",
                        user_desc=chart.get("desc",""),
                        extra_labels=[f"Source: {chart.get('source','')}".strip()] if chart.get('source','') else None,
                        show_ai=False
                    )
            # Wind/Temp
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title="Wind and Temperature Chart",
                        img_bytes=chart["img_bytes"],
                        ai_text="",
                        user_desc=chart.get("desc",""),
                        extra_labels=[f"Flight Levels: {chart.get('levels','')}"] if chart.get('levels','') else None,
                        show_ai=False
                    )
            # SPC
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title="Surface Pressure Chart (SPC)",
                        img_bytes=chart["img_bytes"],
                        ai_text="",
                        user_desc=chart.get("desc",""),
                        show_ai=False
                    )
            out_pdf = f"weather_briefing_raw_{ascii_safe(mission)}.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                pdf_bytes = f.read()
                st.download_button(
                    label="Download PDF RAW (entregar ao instrutor)",
                    data=pdf_bytes,
                    file_name=out_pdf,
                    mime="application/pdf"
                )
else:
    st.info("Fill all sections and upload at least one chart of each type before generating your PDFs.")




