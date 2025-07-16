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

# -------- SETUP & CONSTANTS --------
openai.api_key = st.secrets["OPENAI_API_KEY"]
AIRPORTS = airportsdata.load('ICAO')

def ascii_safe(text):
    if not isinstance(text, str):
        text = str(text)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

def downscale_image(img, width=1100):
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

# ------------- AI PROMPTS (ULTRA-DETAILED) -------------
def ai_chart_analysis_instructor(img_base64, chart_type, user_area_desc):
    area = user_area_desc.strip() or "the selected area"
    if "sigwx" in chart_type.lower():
        prompt = (
            f"You are a meteorology instructor. For this Significant Weather (SIGWX) chart, "
            f"list and explain in exhaustive bullet points EVERY symbol, abbreviation, comment, line, and meteorological feature visible in the chart. "
            f"For each symbol or line, describe what it means in detail, its significance for pilots, and the possible operational implications. "
            f"Do not summarize. Include explanations of all jet streams, turbulence zones, cloud types, fronts, pressure patterns, tropopause levels, and any written comments, even abbreviations. "
            f"Start by decoding the legend if any symbols are present. "
            f"Cover everything visible, especially over Portugal and adjacent Atlantic, but do not skip any feature elsewhere."
        )
    elif "pressure" in chart_type.lower() or "spc" in chart_type.lower():
        prompt = (
            f"You are a meteorology instructor. For this surface pressure chart, in exhaustive bullet points, "
            f"explain every pressure system, front, isobar, symbol, abbreviation, and comment visible. "
            f"For each feature, explain its meteorological meaning, what it tells pilots, and possible operational impact. "
            f"Explicitly decode all numbers, symbols, and lines on the chart, as if teaching a student for an exam. "
            f"Do not summarize or omit any detail. Focus especially on Portugal, but cover all visible details."
        )
    elif "wind" in chart_type.lower():
        prompt = (
            f"You are a meteorology instructor. For this wind and temperature chart, explain in exhaustive bullet points every wind barb, symbol, temperature value, and any other meteorological indicator on the chart, and what it means for a pilot. "
            f"Explicitly decode all symbols and abbreviations. Cover all visible details, focusing on Portugal but not skipping other regions."
        )
    else:
        prompt = (
            f"You are a meteorology instructor. For this chart, explain in exhaustive bullet points every visible symbol, line, meteorological indicator, and text, and decode them in detail for a pilot. "
            f"Cover all features on the chart, focusing on Portugal but explaining every element visible."
        )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text", "text": f"Analyze the entire chart. Pay special attention to Portugal, but explain every symbol or code anywhere in the chart. Do not summarize, be explicit and didactic."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
            ]}
        ],
        max_tokens=3200,  # maximum possible
        temperature=0.12
    )
    return response.choices[0].message.content.strip()

def decode_metar_as_narrative(metar_code):
    try:
        m = Metar(metar_code)
        info, name = get_aerodrome_info(m.station_id or "")
        parts = []
        if info:
            parts.append(f"At {info},")
        else:
            parts.append(f"At {m.station_id or 'Unknown'},")
        if m.time:
            parts.append(f"on day {m.time.day:02d} at {m.time.hour:02d}:{m.time.minute:02d} Zulu,")
        if m.wind_speed:
            ws = m.wind_speed.value('KT')
            wd = m.wind_dir.value() if m.wind_dir else None
            if wd:
                parts.append(f"the wind is from {wd} degrees at {ws:.0f} knots,")
            else:
                parts.append(f"the wind is variable at {ws:.0f} knots,")
        if m.vis:
            vis = m.vis.value('KM')
            if "CAVOK" in metar_code:
                parts.append("visibility is 10 kilometers or more,")
            else:
                parts.append(f"visibility is {vis} kilometers,")
        skystr = []
        if m.sky:
            for s in m.sky:
                typ, height = s[0], s[1]*30.48 if s[1] else None
                if typ == "CB":
                    skystr.append("Cumulonimbus present")
                elif height is not None:
                    skystr.append(f"{typ} at {int(height)} meters")
            if skystr:
                parts.append("clouds: " + "; ".join(skystr) + ",")
            else:
                parts.append("no significant clouds reported,")
        else:
            parts.append("no cloud below 1500 meters and no cumulonimbus,")
        wx = getattr(m, "weather", [])
        if not wx or (len(wx) == 1 and wx[0] == ""):
            parts.append("no significant weather phenomena,")
        else:
            parts.append(f"weather phenomena: {'; '.join(wx)},")
        if m.temp:
            parts.append(f"temperature {m.temp.value():.0f}°C,")
        if m.dewpt:
            parts.append(f"dew point {m.dewpt.value():.0f}°C,")
        if m.press:
            parts.append(f"QNH {m.press.value():.0f} hPa,")
        text = " ".join(parts).strip(",") + "."
        return text.replace(" ,", ",")
    except Exception as e:
        return f"Could not decode METAR: {e}"

def decode_taf_as_narrative(taf_code):
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
    parts = [f"{icao}: {name}, {country} {lat_str} {lon_str}."]
    obs_time = re.search(r'(\d{2})(\d{2})(\d{2})Z', taf_code)
    if obs_time:
        parts.append(f"Observed at day {obs_time.group(1)} at {obs_time.group(2)}:00 Zulu.")
    period = re.search(r'(\d{2})(\d{2})/(\d{2})(\d{2})', taf_code)
    if period:
        parts.append(f"Forecast valid from day {period.group(1)} at {period.group(2)}:00 until day {period.group(3)} at {period.group(4)}:00 Zulu.")
    taf_main = taf_code.split('\n')[0]
    wind_match = re.search(r'(VRB|\d{3})(\d{2,3})KT', taf_main)
    wind_dir = wind_match.group(1) if wind_match else "variable"
    wind_spd = wind_match.group(2) if wind_match else ""
    if wind_dir:
        wind_phrase = f"Wind is variable at {wind_spd} knots." if wind_dir == "VRB" else f"Wind from {wind_dir} degrees at {wind_spd} knots."
        parts.append(wind_phrase)
    vis_match = re.search(r' (\d{4}) ', taf_main)
    if "CAVOK" in taf_main or (vis_match and int(vis_match.group(1)) >= 9999):
        parts.append("Visibility is 10 kilometers or more (CAVOK).")
    elif vis_match:
        parts.append(f"Visibility is {int(vis_match.group(1))/1000:.0f} km.")
    clouds = []
    if "CAVOK" in taf_main:
        clouds.append("No cloud below 1500 meters and no cumulonimbus.")
    else:
        cloud_matches = re.findall(r'(FEW|SCT|BKN|OVC)(\d{3})', taf_main)
        for typ, lvl in cloud_matches:
            height = int(lvl)*30.48
            clouds.append(f"{typ} at {int(height)} meters.")
        if not clouds:
            clouds.append("No significant clouds reported.")
    parts.extend(clouds)
    wx_str = ""
    if re.search(r'(RA|SN|TS|FG|BR)', taf_main):
        wx_str = "Significant weather phenomena are expected."
    else:
        wx_str = "No significant weather phenomena expected."
    parts.append(wx_str)
    return " ".join([l for l in parts if l.strip()])

# --------------- PDF ---------------

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
    def metar_taf_section(self, pairs, mode="detailed"):
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
            if mode == "detailed":
                self.set_font("Arial", 'B', 13)
                self.set_text_color(40,40,40)
                self.cell(0, 8, "Decoded METAR (as read):", ln=True)
                self.set_font("Arial", '', 12)
                self.set_text_color(0,0,0)
                self.multi_cell(0, 8, ascii_safe(decode_metar_as_narrative(metar_code)))
                self.ln(5)
            self.set_font("Arial", 'B', 13)
            self.set_text_color(40,40,40)
            self.cell(0, 8, "TAF (Raw):", ln=True)
            self.set_font("Arial", '', 12)
            self.set_text_color(0,0,0)
            self.multi_cell(0, 8, ascii_safe(taf_code))
            self.ln(2)
            if mode == "detailed":
                self.set_font("Arial", 'B', 13)
                self.set_text_color(40,40,40)
                self.cell(0, 8, "Decoded TAF (as read):", ln=True)
                self.set_font("Arial", '', 12)
                self.set_text_color(0,0,0)
                self.multi_cell(0, 8, ascii_safe(decode_taf_as_narrative(taf_code)))
                self.ln(4)

class RawLandscapePDF(FPDF):
    def __init__(self):
        super().__init__(orientation='L', unit='mm', format='A4')
    def header(self): pass
    def footer(self): pass
    def cover_page(self, pilot, aircraft, date, time_utc, callsign, mission):
        self.add_page()
        self.set_xy(0, 65)
        self.set_font("Arial", 'B', 30)
        self.cell(0, 22, ascii_safe("RAW Preflight Briefing Charts"), ln=True, align='C')
        self.ln(10)
        self.set_font("Arial", '', 17)
        self.cell(0, 10, ascii_safe(f"Pilot: {pilot}    Aircraft: {aircraft}    Callsign: {callsign}"), ln=True, align='C')
        self.cell(0, 10, ascii_safe(f"Mission: {mission}    Date: {date}    UTC: {time_utc}"), ln=True, align='C')
        self.ln(30)
    def chart_fullpage(self, title, img_bytes, user_desc=""):
        self.add_page()
        self.set_font("Arial", 'B', 18)
        self.cell(0, 10, ascii_safe(title), ln=True, align='C')
        if user_desc.strip():
            self.set_font("Arial", 'I', 13)
            self.cell(0, 10, ascii_safe(f"Area/focus: {user_desc.strip()}"), ln=True, align='C')
        chart_img_path = f"tmp_chart_{ascii_safe(title).replace(' ','_')}.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.image(chart_img_path, x=10, y=30, w=270)

# -------------- STREAMLIT --------------
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

def chart_block_multi(chart_key, label, title_base, desc_label="Area/focus for analysis", has_levels=False, has_source=False):
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
            else:
                chart["img_bytes"] = None
    addcol, rmcol = st.columns([0.24,0.24])
    if addcol.button(f"Add {label}"):
        new_chart = {"desc": "Portugal", "img_bytes": None}
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
    title_base="Wind and Temperature Chart", has_levels=True
)
chart_block_multi(
    chart_key="spc_charts", label="Surface Pressure Chart (SPC)",
    title_base="Surface Pressure Chart (SPC)"
)

ready = (
    len([c for c in st.session_state.get("sigwx_charts", []) if c.get("img_bytes")]) > 0
    and len([c for c in st.session_state.get("windtemp_charts", []) if c.get("img_bytes")]) > 0
    and len([c for c in st.session_state.get("spc_charts", []) if c.get("img_bytes")]) > 0
)

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
                pdf.metar_taf_section(metar_taf_pairs, mode="detailed")
            # SIGWX
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
                    ai_text = ai_chart_analysis_instructor(
                        img_b64,
                        chart_type="Significant Weather Chart (SIGWX)",
                        user_area_desc=chart.get("desc", "Portugal")
                    )
                    pdf.add_section_page(f"Significant Weather Chart (SIGWX) [{chart.get('source','')}]")
                    pdf.set_font("Arial", '', 12)
                    pdf.multi_cell(0, 8, ai_text)
            # Wind/Temp
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
                    ai_text = ai_chart_analysis_instructor(
                        img_b64,
                        chart_type="Wind and Temperature Chart",
                        user_area_desc=chart.get("desc", "Portugal")
                    )
                    pdf.add_section_page("Wind and Temperature Chart")
                    pdf.set_font("Arial", '', 12)
                    pdf.multi_cell(0, 8, ai_text)
            # SPC
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
                    ai_text = ai_chart_analysis_instructor(
                        img_b64,
                        chart_type="Surface Pressure Chart (SPC)",
                        user_area_desc=chart.get("desc", "Portugal")
                    )
                    pdf.add_section_page("Surface Pressure Chart (SPC)")
                    pdf.set_font("Arial", '', 12)
                    pdf.multi_cell(0, 8, ai_text)
            out_pdf = f"weather_briefing_detailed_{ascii_safe(mission)}.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                pdf_bytes = f.read()
                st.download_button(
                    label="Download Detailed Weather Briefing PDF",
                    data=pdf_bytes,
                    file_name=out_pdf,
                    mime="application/pdf"
                )
    if col2.button("Gerar PDF RAW (para entregar)"):
        with st.spinner("Preparando PDF raw..."):
            pdf = RawLandscapePDF()
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission)
            # SIGWX
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    title = f"Significant Weather Chart (SIGWX) [{chart.get('source','')}]"
                    pdf.chart_fullpage(title, chart["img_bytes"], user_desc=chart.get("desc",""))
            # Wind/Temp
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    title = f"Wind and Temperature Chart [{chart.get('levels','')}]"
                    pdf.chart_fullpage(title, chart["img_bytes"], user_desc=chart.get("desc",""))
            # SPC
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    title = "Surface Pressure Chart (SPC)"
                    pdf.chart_fullpage(title, chart["img_bytes"], user_desc=chart.get("desc",""))
            out_pdf = f"weather_briefing_raw_{ascii_safe(mission)}.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                pdf_bytes = f.read()
                st.download_button(
                    label="Download RAW Weather Briefing PDF",
                    data=pdf_bytes,
                    file_name=out_pdf,
                    mime="application/pdf"
                )
else:
    st.info("Fill all sections and upload at least one chart of each type before generating your PDFs.")





