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

def ai_chart_analysis(img_base64, chart_type, user_area_desc, extra_instruction="", summarized=False):
    sys_prompt = (
        "You are a student pilot preparing a preflight weather briefing. "
        "Write an analysis for our own flight, always in the first person plural ('We can expect...'). "
        "Give a practical, operational summary for our area, mentioning any big-picture trends or systems that could affect us. "
        "Never use formatting, lists or bullets. Never use 'pilots should' or give external advice. Speak in the first person. "
        "If extra instructions are given, follow them. "
    )
    area = user_area_desc.strip() or "Portugal"
    if summarized:
        prompt = "In one or two sentences, summarize what we should expect at the indicated flight levels for our flight, in the first person plural, for this wind and temperature chart."
    else:
        prompt = f"Focus on: {area}. {extra_instruction}"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                ]
            }
        ],
        max_tokens=300 if summarized else 850,
        temperature=0.25 if summarized else 0.33
    )
    return clean_markdown(response.choices[0].message.content)

def ai_sigmet_summary(sigmet_text):
    prompt = (
        "Write a short summary in English of these SIGMET/AIRMET/GAMET en-route weather warnings, in the first person plural and student pilot style. "
        "Do not use formatting. Do not say 'pilots should'. Instead, use 'We can expect... We may encounter...'."
    )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": sigmet_text}
        ],
        max_tokens=180,
        temperature=0.19
    )
    return clean_markdown(response.choices[0].message.content.strip())

def brief_metar_taf_comment(metar_code, taf_code):
    prompt = (
        "Given this METAR and TAF, write a very brief summary as a student for our own flight (one or two sentences). "
        "Speak in first person plural, avoid generic advice. Just say what we expect or any main issue."
    )
    content = f"METAR:\n{metar_code}\nTAF:\n{taf_code}"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": content}
        ],
        max_tokens=80,
        temperature=0.18
    )
    return clean_markdown(response.choices[0].message.content.strip())

def brief_notam_comment(notams, icao):
    text = "\n".join([f"{n['num']}: {n['text']}" for n in notams if n['num'].strip() or n['text'].strip()])
    if not text.strip():
        return ""
    prompt = (
        f"Given these NOTAMs for {icao}, write a very short summary in the first person plural (e.g., 'We should take note that...'), as a student pilot would for a preflight briefing."
    )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text}
        ],
        max_tokens=80,
        temperature=0.14
    )
    return clean_markdown(response.choices[0].message.content.strip())

def decode_metar(metar_code):
    try:
        m = Metar(metar_code)
        station = m.station_id or "Unknown"
        info, name = get_aerodrome_info(station)
        result = []
        if info:
            result.append(f"{station}: {info}")
        else:
            result.append(f"{station}")
        if m.time:
            obs = m.time
            result.append(f"Observation time: [Day: {obs.day:02d}] [Time: {obs.hour:02d}{obs.minute:02d}]")
        if m.wind_speed:
            ws = m.wind_speed.value('MPS')
            wd = m.wind_dir.value() if m.wind_dir else None
            if wd:
                result.append(f"Wind: {wd}° at {ws:.1f} m/s")
            else:
                result.append(f"Wind: variable at {ws:.1f} m/s")
        if m.vis:
            vis = m.vis.value('KM')
            if "CAVOK" in metar_code:
                result.append("Visibility: 10km or more (CAVOK)")
            else:
                result.append(f"Visibility: {vis} km")
        skystr = []
        if m.sky:
            cb = any([s[0] == "CB" for s in m.sky])
            if "CAVOK" in metar_code:
                skystr.append("No cloud below 1500m and no Cumulonimbus")
            else:
                for s in m.sky:
                    typ, height = s[0], s[1]*30.48 if s[1] else None
                    if typ == "CB":
                        skystr.append("Cumulonimbus present")
                    elif height is not None:
                        skystr.append(f"{typ} at {int(height)}m")
                if not skystr:
                    skystr.append("No significant clouds reported")
            result.append("; ".join(skystr))
        else:
            result.append("No cloud below 1500m and no Cumulonimbus")
        wx = getattr(m, "weather", [])
        if not wx or (len(wx) == 1 and wx[0] == ""):
            result.append("No significant weather phenomena")
        else:
            result.append(f"Weather phenomena: {'; '.join(wx)}")
        if m.temp:
            result.append(f"Air Temp: {m.temp.value():.0f}°C")
        if m.dewpt:
            result.append(f"Dew Point: {m.dewpt.value():.0f}°C")
        if m.press:
            result.append(f"QNH: {m.press.value():.0f} hPa")
        return "\n".join(result)
    except Exception as e:
        return f"Could not decode METAR: {e}"

def decode_taf(taf_code):
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
    lines = []
    lines.append(f"{icao}: {name}, {country} {lat_str} {lon_str}")
    obs_time = re.search(r'(\d{2})(\d{2})(\d{2})Z', taf_code)
    if obs_time:
        lines.append(f"Observation time: [Day {obs_time.group(1)} {obs_time.group(2)}:00]")
    period = re.search(r'(\d{2})(\d{2})/(\d{2})(\d{2})', taf_code)
    if period:
        lines.append(f"Forecast start: [Day {period.group(1)} {period.group(2)}:00] Until: [Day {period.group(3)} {period.group(4)}:00]")
    taf_main = taf_code.split('\n')[0]
    wind_match = re.search(r'(VRB|\d{3})(\d{2,3})KT', taf_main)
    wind_dir = wind_match.group(1) if wind_match else "variable"
    wind_spd = wind_match.group(2) if wind_match else ""
    wind_str = f"Wind: {wind_dir if wind_dir != 'VRB' else 'variable'}"
    wind_speed = f"{float(wind_spd)*0.514:.1f} m/s ({wind_spd}kt)" if wind_spd else ""
    vis_match = re.search(r' (\d{4}) ', taf_main)
    vis_str = "Visibility: 10km or more (CAVOK)" if "CAVOK" in taf_main or (vis_match and int(vis_match.group(1)) >= 9999) else f"Visibility: {int(vis_match.group(1))/1000:.0f}km" if vis_match else ""
    clouds = []
    if "CAVOK" in taf_main:
        clouds.append("No cloud below 1500m and no Cumulonimbus")
    else:
        cloud_matches = re.findall(r'(FEW|SCT|BKN|OVC)(\d{3})', taf_main)
        for typ, lvl in cloud_matches:
            height = int(lvl)*30.48
            clouds.append(f"{typ} at {int(height)}m")
        if not clouds:
            clouds.append("No significant clouds reported")
    clouds_str = "; ".join(clouds)
    wx_str = "No significant weather phenomena" if not re.search(r'(RA|SN|TS|FG|BR)', taf_main) else ""
    lines.extend([wind_str, wind_speed, vis_str, clouds_str, wx_str])
    return "\n".join([l for l in lines if l.strip()])

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
    def metar_taf_section(self, pairs):
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
            self.set_font("Arial", 'B', 13)
            self.set_text_color(40,40,40)
            self.cell(0, 8, "Decoded METAR:", ln=True)
            self.set_font("Arial", '', 12)
            self.set_text_color(0,0,0)
            self.multi_cell(0, 8, ascii_safe(decode_metar(metar_code)))
            self.ln(5)
            self.set_font("Arial", 'B', 13)
            self.set_text_color(40,40,40)
            self.cell(0, 8, "TAF (Raw):", ln=True)
            self.set_font("Arial", '', 12)
            self.set_text_color(0,0,0)
            self.multi_cell(0, 8, ascii_safe(taf_code))
            self.ln(2)
            self.set_font("Arial", 'B', 13)
            self.set_text_color(40,40,40)
            self.cell(0, 8, "Decoded TAF:", ln=True)
            self.set_font("Arial", '', 12)
            self.set_text_color(0,0,0)
            self.multi_cell(0, 8, ascii_safe(decode_taf(taf_code)))
            self.ln(6)
            if metar_code.strip() or taf_code.strip():
                self.set_font("Arial", 'I', 11)
                self.set_text_color(80, 56, 0)
                try:
                    comment = brief_metar_taf_comment(metar_code, taf_code)
                    self.multi_cell(0, 8, f"Summary: {ascii_safe(comment)}")
                except Exception as e:
                    self.multi_cell(0, 8, f"(Short comment failed: {e})")
            self.ln(6)
    def enroute_section(self, text, ai_summary):
        if text.strip():
            self.add_section_page("En-route Weather Warnings (SIGMET/AIRMET/GAMET)")
            self.set_font("Arial", 'B', 13)
            self.cell(0, 8, "Raw Text:", ln=True)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 8, ascii_safe(text))
            self.ln(2)
            if ai_summary:
                self.set_font("Arial", 'B', 13)
                self.cell(0, 8, "Summary:", ln=True)
                self.set_font("Arial", '', 12)
                self.multi_cell(0, 8, ascii_safe(ai_summary))
                self.ln(4)
    def chart_section(self, title, img_bytes, ai_text, user_desc="", extra_labels=None):
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
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ascii_safe(ai_text))
        self.ln(2)
    def notam_section(self, notam_data):
        if not notam_data:
            return
        self.add_section_page("NOTAM Information")
        for entry in notam_data:
            if entry["aero"].strip():
                info, name = get_aerodrome_info(entry["aero"])
                self.set_font("Arial", 'B', 18)
                self.set_text_color(28, 44, 80)
                self.cell(0, 12, ascii_safe(f"{entry['aero'].upper()} ({name})"), ln=True)
                self.ln(3)
            self.set_text_color(0,0,0)
            self.set_font("Arial", '', 12)
            for nidx, notam in enumerate(entry["notams"], 1):
                if notam["num"].strip() or notam["text"].strip():
                    self.set_font("Arial",'B',12)
                    self.cell(0, 8, f"NOTAM: {notam['num']}", ln=True)
                    self.set_font("Arial",'',12)
                    self.multi_cell(0, 8, ascii_safe(notam["text"]))
                    self.ln(3)
            ai_summary = brief_notam_comment(entry["notams"], entry["aero"])
            if ai_summary:
                self.set_font("Arial", 'I', 11)
                self.set_text_color(80, 56, 0)
                self.multi_cell(0, 8, f"Summary: {ascii_safe(ai_summary)}")
                self.ln(6)
            else:
                self.ln(6)

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

st.title("Preflight Weather Briefing and NOTAMs")

with st.expander("1. Pilot/Aircraft Info", expanded=True):
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft (e.g., Tecnam P2008 (CS-ECD))", "")
    callsign = st.text_input("Callsign", "")
    mission = st.text_input("Mission #", "")
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

def sigwx_block():
    if "sigwx_charts" not in st.session_state:
        st.session_state.sigwx_charts = []
    st.subheader("3. Significant Weather Charts (SIGWX)")
    chart_list = st.session_state.sigwx_charts
    for i in range(len(chart_list)):
        chart = chart_list[i]
        with st.expander(f"SIGWX {i+1}", expanded=True):
            chart["source"] = st.text_input("SIGWX Source/Organization", value=chart.get("source",""), key=f"sigwx_source_{i}")
            chart["desc"] = st.text_input("Area/focus for analysis", value=chart.get("desc","Portugal"), key=f"sigwx_desc_{i}")
            chart["extra"] = st.text_area("Extra instructions to AI (optional)", value=chart.get("extra",""), key=f"sigwx_extra_{i}")
            chart_file = st.file_uploader("Upload SIGWX/SWC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"sigwx_file_{i}")
            if chart_file:
                if chart_file.type == "application/pdf":
                    pdf_bytes = chart_file.read()
                    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = pdf_doc.load_page(0)
                    pix = page.get_pixmap()
                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
                else:
                    img = Image.open(chart_file).convert("RGB").copy()
                _, img_bytes = downscale_image(img)
                chart["img_bytes"] = img_bytes
                st.image(img, caption="SIGWX: Full Chart (included in PDF)")
                # Generate or preview analysis
                if "ai_text" not in chart or st.button(f"Regenerate AI analysis for SIGWX {i+1}", key=f"sigwx_regen_{i}"):
                    if img_bytes:
                        img_b64 = base64.b64encode(img_bytes.getvalue()).decode("utf-8")
                        ai_text = ai_chart_analysis(img_b64, "SIGWX", chart["desc"], chart.get("extra",""))
                        chart["ai_text"] = ai_text
                chart["ai_text"] = st.text_area("Edit/Approve AI Analysis", value=chart.get("ai_text",""), key=f"sigwx_aitxt_{i}", height=150)
            else:
                chart["img_bytes"] = None
                chart["ai_text"] = ""
    addcol, rmcol = st.columns([0.24,0.24])
    if addcol.button("Add SIGWX chart"):
        chart_list.append({"source": "", "desc": "Portugal", "extra": "", "img_bytes": None, "ai_text": ""})
    if len(chart_list) > 1 and rmcol.button("Remove last SIGWX chart"):
        chart_list.pop()

def windtemp_block():
    if "windtemp_charts" not in st.session_state:
        st.session_state.windtemp_charts = []
    st.subheader("4. Wind and Temperature Charts")
    chart_list = st.session_state.windtemp_charts
    for i in range(len(chart_list)):
        chart = chart_list[i]
        with st.expander(f"Wind/Temp Chart {i+1}", expanded=True):
            chart["levels"] = st.text_input("Applicable Flight Levels (e.g., FL050-FL120)", value=chart.get("levels",""), key=f"windtemp_levels_{i}")
            chart["desc"] = st.text_input("Area/focus for analysis", value=chart.get("desc","Portugal"), key=f"windtemp_desc_{i}")
            chart["extra"] = st.text_area("Extra instructions to AI (optional)", value=chart.get("extra",""), key=f"windtemp_extra_{i}")
            chart_file = st.file_uploader("Upload Wind/Temp Chart (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"windtemp_file_{i}")
            if chart_file:
                if chart_file.type == "application/pdf":
                    pdf_bytes = chart_file.read()
                    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = pdf_doc.load_page(0)
                    pix = page.get_pixmap()
                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
                else:
                    img = Image.open(chart_file).convert("RGB").copy()
                _, img_bytes = downscale_image(img)
                chart["img_bytes"] = img_bytes
                st.image(img, caption="Wind & Temp Chart (included in PDF)")
                # Generate or preview analysis
                if "ai_text" not in chart or st.button(f"Regenerate AI analysis for Wind/Temp {i+1}", key=f"windtemp_regen_{i}"):
                    if img_bytes:
                        img_b64 = base64.b64encode(img_bytes.getvalue()).decode("utf-8")
                        ai_text = ai_chart_analysis(
                            img_b64,
                            "Wind and Temperature Chart",
                            chart["desc"],
                            chart.get("extra",""),
                            summarized=True
                        )
                        chart["ai_text"] = ai_text
                chart["ai_text"] = st.text_area("Edit/Approve AI Analysis", value=chart.get("ai_text",""), key=f"windtemp_aitxt_{i}", height=90)
            else:
                chart["img_bytes"] = None
                chart["ai_text"] = ""
    addcol, rmcol = st.columns([0.24,0.24])
    if addcol.button("Add Wind/Temp chart"):
        chart_list.append({"levels": "", "desc": "Portugal", "extra": "", "img_bytes": None, "ai_text": ""})
    if len(chart_list) > 1 and rmcol.button("Remove last Wind/Temp chart"):
        chart_list.pop()

def spc_block():
    if "spc_charts" not in st.session_state:
        st.session_state.spc_charts = []
    st.subheader("5. Surface Pressure Charts (SPC)")
    chart_list = st.session_state.spc_charts
    for i in range(len(chart_list)):
        chart = chart_list[i]
        with st.expander(f"SPC {i+1}", expanded=True):
            chart["desc"] = st.text_input("Area/focus for analysis", value=chart.get("desc","Portugal"), key=f"spc_desc_{i}")
            chart["extra"] = st.text_area("Extra instructions to AI (optional)", value=chart.get("extra",""), key=f"spc_extra_{i}")
            chart_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"spc_file_{i}")
            if chart_file:
                if chart_file.type == "application/pdf":
                    pdf_bytes = chart_file.read()
                    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = pdf_doc.load_page(0)
                    pix = page.get_pixmap()
                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
                else:
                    img = Image.open(chart_file).convert("RGB").copy()
                _, img_bytes = downscale_image(img)
                chart["img_bytes"] = img_bytes
                st.image(img, caption="SPC: Full Chart (included in PDF)")
                if "ai_text" not in chart or st.button(f"Regenerate AI analysis for SPC {i+1}", key=f"spc_regen_{i}"):
                    if img_bytes:
                        img_b64 = base64.b64encode(img_bytes.getvalue()).decode("utf-8")
                        ai_text = ai_chart_analysis(img_b64, "SPC", chart["desc"], chart.get("extra",""))
                        chart["ai_text"] = ai_text
                chart["ai_text"] = st.text_area("Edit/Approve AI Analysis", value=chart.get("ai_text",""), key=f"spc_aitxt_{i}", height=150)
            else:
                chart["img_bytes"] = None
                chart["ai_text"] = ""
    addcol, rmcol = st.columns([0.24,0.24])
    if addcol.button("Add SPC chart"):
        chart_list.append({"desc": "Portugal", "extra": "", "img_bytes": None, "ai_text": ""})
    if len(chart_list) > 1 and rmcol.button("Remove last SPC chart"):
        chart_list.pop()

def sigmet_block():
    st.subheader("6. En-route Weather Warnings (SIGMET/AIRMET/GAMET)")
    return st.text_area("SIGMET/AIRMET/GAMET:", height=110, key="sigmet_area")

def notam_block():
    if "notam_data" not in st.session_state:
        st.session_state.notam_data = [{"aero": "", "notams": [{"num":"", "text":""}]}]
    st.subheader("7. NOTAMs by Aerodrome")
    for idx, entry in enumerate(st.session_state.notam_data):
        with st.expander(f"NOTAMs for Aerodrome {idx+1}", expanded=True):
            entry["aero"] = st.text_input("Aerodrome ICAO or Name", value=entry["aero"], key=f"notam_aero_{idx}")
            num_notams = len(entry["notams"])
            for nidx in range(num_notams):
                cols = st.columns([0.22, 0.78])
                entry["notams"][nidx]["num"] = cols[0].text_input("NOTAM Number", value=entry["notams"][nidx]["num"], key=f"notam_num_{idx}_{nidx}")
                entry["notams"][nidx]["text"] = cols[1].text_area(f"NOTAM {nidx+1} Text", value=entry["notams"][nidx]["text"], key=f"notam_text_{idx}_{nidx}")
            col_add, col_rm = st.columns([0.22,0.22])
            if col_add.button("Add NOTAM", key=f"addnotam_{idx}"):
                entry["notams"].append({"num":"","text":""})
            if num_notams > 1 and col_rm.button("Remove NOTAM", key=f"rmnotam_{idx}"):
                entry["notams"].pop()
    btncols = st.columns([0.23,0.23])
    if btncols[0].button("Add Aerodrome NOTAM"):
        st.session_state.notam_data.append({"aero":"", "notams":[{"num":"","text":""}]})
    if len(st.session_state.notam_data)>1 and btncols[1].button("Remove Last Aerodrome NOTAM"):
        st.session_state.notam_data.pop()

metar_taf_block()
sigwx_block()
windtemp_block()
spc_block()
sigmet_gamet_text = sigmet_block()
notam_block()

ready = (
    len([c for c in st.session_state.get("sigwx_charts", []) if c.get("img_bytes")]) > 0
    and len([c for c in st.session_state.get("windtemp_charts", []) if c.get("img_bytes")]) > 0
    and len([c for c in st.session_state.get("spc_charts", []) if c.get("img_bytes")]) > 0
)

if ready:
    if st.button("Generate PDF Report"):
        with st.spinner("Preparing your preflight briefing..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=14)
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission)
            metar_taf_pairs = [
                entry for entry in st.session_state.metar_taf_pairs
                if entry['metar'].strip() or entry['taf'].strip() or entry['icao'].strip()
            ]
            if metar_taf_pairs:
                pdf.metar_taf_section(metar_taf_pairs)
            sigmet_ai_summary = ai_sigmet_summary(sigmet_gamet_text) if sigmet_gamet_text.strip() else ""
            pdf.enroute_section(sigmet_gamet_text, sigmet_ai_summary)
            # SIGWX
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title=f"Significant Weather Chart (SIGWX) [{chart.get('source','')}]",
                        img_bytes=chart["img_bytes"],
                        ai_text=chart.get("ai_text",""),
                        user_desc=chart.get("desc",""),
                        extra_labels=[f"Source: {chart.get('source','')}".strip()] if chart.get('source','') else None
                    )
            # Wind/Temp
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title="Wind and Temperature Chart",
                        img_bytes=chart["img_bytes"],
                        ai_text=chart.get("ai_text",""),
                        user_desc=chart.get("desc",""),
                        extra_labels=[f"Flight Levels: {chart.get('levels','')}"] if chart.get('levels','') else None
                    )
            # SPC
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title="Surface Pressure Chart (SPC)",
                        img_bytes=chart["img_bytes"],
                        ai_text=chart.get("ai_text",""),
                        user_desc=chart.get("desc","")
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
    st.info("Fill all sections and upload at least one chart of each type before generating your PDF.")




