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
    def sigwx_sections(self, sigwx_list):
        for chart in sigwx_list:
            title = f"Significant Weather Chart (SIGWX) - {chart['source']}"
            self.chart_section(
                title=title,
                img_bytes=chart['img_bytes'],
                ai_text=chart['ai_text'],
                user_desc=chart['desc']
            )
    def chart_section(self, title, img_bytes, ai_text, user_desc=""):
        self.add_section_page(title)
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

# ---- INPUT UI ----

st.title("Preflight Weather Briefing and NOTAMs")

with st.expander("1. Pilot/Aircraft Info", expanded=True):
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
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

metar_taf_block()

# 3. MULTI-SIGWX block
def multi_sigwx_block():
    if "sigwx_charts" not in st.session_state:
        st.session_state.sigwx_charts = []
    st.subheader("3. Significant Weather Charts (SIGWX)")
    num_sigwx = len(st.session_state.sigwx_charts)
    for i in range(num_sigwx):
        chart = st.session_state.sigwx_charts[i]
        with st.expander(f"SIGWX {i+1}", expanded=True):
            chart["source"] = st.text_input("SIGWX Source (ex: IPMA, UKMO, Meteo France, ECMWF)", value=chart.get("source",""), key=f"sigwx_source_{i}")
            chart["desc"] = st.text_input("Area/focus for analysis (default: Portugal)", value=chart.get("desc","Portugal"), key=f"sigwx_desc_{i}")
            chart_file = st.file_uploader("Upload SIGWX/SWC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"sigwx_file_{i}")
            if chart_file:
                if chart_file.type == "application/pdf":
                    pdf_bytes = chart_file.read()
                    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = pdf_doc.load_page(0)
                    pix = page.get_pixmap()
                    sigwx_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
                else:
                    sigwx_img = Image.open(chart_file).convert("RGB").copy()
                _, sigwx_img_bytes = downscale_image(sigwx_img)
                chart["img_bytes"] = sigwx_img_bytes
                st.image(sigwx_img, caption="SIGWX: Full Chart (included in PDF)")
            else:
                chart["img_bytes"] = None
    addcol, rmcol = st.columns([0.24,0.24])
    if addcol.button("Add SIGWX chart"):
        st.session_state.sigwx_charts.append({"source": "", "desc": "Portugal", "img_bytes": None})
    if num_sigwx > 1 and rmcol.button("Remove last SIGWX chart"):
        st.session_state.sigwx_charts.pop()
multi_sigwx_block()

# 4. WIND & TEMP CHART
with st.expander("4. Wind and Temperature Chart", expanded=True):
    windtemp_file = st.file_uploader("Upload Wind/Temp Chart (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="windtemp")
    windtemp_desc = st.text_input("Area/focus for analysis (default: Portugal)", value="Portugal", key="windtempdesc")
    if windtemp_file:
        if windtemp_file.type == "application/pdf":
            pdf_bytes = windtemp_file.read()
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = pdf_doc.load_page(0)
            pix = page.get_pixmap()
            windtemp_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
        else:
            windtemp_img = Image.open(windtemp_file).convert("RGB").copy()
        _, windtemp_img_bytes = downscale_image(windtemp_img)
        st.session_state["windtemp_img_bytes"] = windtemp_img_bytes
        st.image(windtemp_img, caption="Wind & Temp Chart (included in PDF)")
    else:
        st.session_state["windtemp_img_bytes"] = None

# 5. SPC chart
with st.expander("5. Surface Pressure Chart (SPC)", expanded=True):
    spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
    spc_desc = st.text_input("Area/focus for analysis (default: Portugal)", value="Portugal", key="spcdesc")
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
    else:
        st.session_state["spc_full_bytes"] = None
    st.session_state["spc_desc"] = spc_desc

sigmet_gamet_text = sigmet_block()
notam_block()

# READY LOGIC
charts_ready = (
    st.session_state.get("spc_full_bytes")
    and st.session_state.get("windtemp_img_bytes")
    and len([c for c in st.session_state.get("sigwx_charts", []) if c.get("img_bytes")]) > 0
)

if charts_ready:
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
            # --- SIGWX section(s) ---
            sigwx_list = []
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    sigwx_base64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
                    ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", chart.get("desc","Portugal"))
                    sigwx_list.append({
                        "source": chart.get("source",""),
                        "desc": chart.get("desc","Portugal"),
                        "img_bytes": chart["img_bytes"],
                        "ai_text": ai_text
                    })
            pdf.sigwx_sections(sigwx_list)
            # --- Wind and Temperature Chart ---
            windtemp_bytes = st.session_state.get("windtemp_img_bytes")
            windtemp_desc = st.session_state.get("windtempdesc", "Portugal")
            if windtemp_bytes:
                windtemp_base64 = base64.b64encode(windtemp_bytes.getvalue()).decode("utf-8")
                windtemp_ai = ai_chart_analysis(windtemp_base64, "Wind and Temperature Chart", windtemp_desc)
                pdf.chart_section("Wind & Temperature Chart", windtemp_bytes, windtemp_ai, user_desc=windtemp_desc)
            # --- SPC (Full chart!) ---
            spc_bytes = st.session_state.get("spc_full_bytes")
            spc_desc = st.session_state.get("spc_desc", "Portugal")
            if spc_bytes:
                spc_base64 = base64.b64encode(spc_bytes.getvalue()).decode("utf-8")
                spc_ai_text = ai_chart_analysis(spc_base64, "SPC", spc_desc)
                pdf.chart_section("Surface Pressure Chart (SPC)", spc_bytes, spc_ai_text, user_desc=spc_desc)
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
    st.info("Fill all sections and upload all required charts before generating your PDF.")






