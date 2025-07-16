import streamlit as st
from PIL import Image
import openai
import io
import base64
from fpdf import FPDF
import fitz
import datetime
import unicodedata

import airportsdata
import re

openai.api_key = st.secrets["OPENAI_API_KEY"]
AIRPORTS = airportsdata.load('ICAO')

def ascii_safe(text):
    return unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode('ascii')

def downscale_image(img, width=1200):
    if img.width > width:
        ratio = width / img.width
        img = img.resize((width, int(img.height * ratio)))
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

def ai_metar_taf_analysis(raw_text, msg_type="METAR/TAF", icao=""):
    prompt = (
        f"You are a meteorology instructor. Analyse the following {msg_type} as if explaining every code and section to a pilot preparing for an exam. "
        "For every element (even the less usual ones like wind shifts, BECMG, TEMPO, remarks, QNH, etc), provide a line-by-line detailed explanation. "
        "Do not summarize or omit anything. Write in bullet points. If the message refers to more than one time period or line, explain each one."
    )
    if icao:
        prompt += f" The ICAO is {icao}."
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": raw_text}
        ],
        max_tokens=2200,
        temperature=0.1
    )
    return response.choices[0].message.content.strip()

def ai_gamet_analysis(gamet_text):
    prompt = (
        "You are a meteorology instructor. Analyse the following GAMET/SIGMET/AIRMET warning in bullet points, explaining every code, line, and abbreviation to a student pilot. "
        "Do not summarize or omit anything. Explain every meteorological risk, area, phenomenon, and abbreviation."
    )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": gamet_text}
        ],
        max_tokens=2200,
        temperature=0.1
    )
    return response.choices[0].message.content.strip()

def ai_chart_analysis_instructor(img_base64, chart_type, user_area_desc):
    area = user_area_desc.strip() or "the selected area"
    if "sigwx" in chart_type.lower():
        prompt = (
            f"You are a meteorology instructor. For this Significant Weather (SIGWX) chart, "
            "list and explain in exhaustive bullet points EVERY symbol, abbreviation, comment, line, and meteorological feature visible in the chart. "
            "For each symbol or line, describe what it means in detail, its significance for pilots, and possible operational implications. "
            "Do not summarize. Include explanations of all jet streams, turbulence zones, cloud types, fronts, pressure patterns, tropopause levels, and any written comments, even abbreviations. "
            "Start by decoding the legend if any symbols are present. Cover everything visible, especially over Portugal and adjacent Atlantic, but do not skip any feature elsewhere."
        )
    elif "pressure" in chart_type.lower() or "spc" in chart_type.lower():
        prompt = (
            "You are a meteorology instructor. For this surface pressure chart, in exhaustive bullet points, "
            "explain every pressure system, front, isobar, symbol, abbreviation, and comment visible. "
            "For each feature, explain its meteorological meaning, what it tells pilots, and possible operational impact. "
            "Explicitly decode all numbers, symbols, and lines on the chart, as if teaching a student for an exam. "
            "Do not summarize or omit any detail. Focus especially on Portugal, but cover all visible details."
        )
    elif "wind" in chart_type.lower():
        prompt = (
            "You are a meteorology instructor. For this wind and temperature chart, explain in exhaustive bullet points every wind barb, symbol, temperature value, and any other meteorological indicator on the chart, and what it means for a pilot. "
            "Explicitly decode all symbols and abbreviations. Cover all visible details, focusing on Portugal but not skipping other regions."
        )
    else:
        prompt = (
            "You are a meteorology instructor. For this chart, explain in exhaustive bullet points every visible symbol, line, meteorological indicator, and text, and decode them in detail for a pilot. "
            "Cover all features on the chart, focusing on Portugal but explaining every element visible."
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
        max_tokens=3200,
        temperature=0.10
    )
    return response.choices[0].message.content.strip()

# --------- PDF TEMPLATES ----------
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
    def metar_taf_gamet_section(self, metars, tafs, gamet):
        # METARs
        for entry in metars:
            icao = entry['icao'].upper()
            self.add_section_page(f"METAR {icao}")
            self.set_font("Arial", 'B', 13)
            self.cell(0, 8, "METAR (Raw):", ln=True)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 8, ascii_safe(entry["metar"]))
            ai_text = ai_metar_taf_analysis(entry["metar"], msg_type="METAR", icao=icao)
            self.ln(2)
            self.set_font("Arial", 'B', 13)
            self.cell(0, 8, "AI Decoded METAR:", ln=True)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 8, ascii_safe(ai_text))
        # TAFs
        for entry in tafs:
            icao = entry['icao'].upper()
            self.add_section_page(f"TAF {icao}")
            self.set_font("Arial", 'B', 13)
            self.cell(0, 8, "TAF (Raw):", ln=True)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 8, ascii_safe(entry["taf"]))
            ai_text = ai_metar_taf_analysis(entry["taf"], msg_type="TAF", icao=icao)
            self.ln(2)
            self.set_font("Arial", 'B', 13)
            self.cell(0, 8, "AI Decoded TAF:", ln=True)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 8, ascii_safe(ai_text))
        # GAMET
        if gamet and gamet.strip():
            self.add_section_page("GAMET/SIGMET/AIRMET")
            self.set_font("Arial", 'B', 13)
            self.cell(0, 8, "GAMET (Raw):", ln=True)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 8, ascii_safe(gamet))
            ai_text = ai_gamet_analysis(gamet)
            self.ln(2)
            self.set_font("Arial", 'B', 13)
            self.cell(0, 8, "AI Decoded GAMET/SIGMET/AIRMET:", ln=True)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 8, ascii_safe(ai_text))
    def chart_section(self, charts):
        for chart in charts:
            self.add_section_page(chart['title'])
            if chart.get("img_bytes"):
                img_bytes = chart["img_bytes"]
                chart_img_path = f"tmp_chart_{ascii_safe(chart['title']).replace(' ','_')}.png"
                with open(chart_img_path, "wb") as f:
                    f.write(img_bytes.getvalue())
                self.image(chart_img_path, x=22, w=168)
                self.ln(5)
            ai_text = chart.get("ai_text", "")
            if ai_text:
                self.set_font("Arial", '', 12)
                self.multi_cell(0, 8, ascii_safe(ai_text))
                self.ln(2)

class RawLandscapePDF(FPDF):
    def __init__(self):
        super().__init__(orientation='L', unit='mm', format='A4')
    def header(self): pass
    def footer(self): pass
    def cover_page(self, pilot, aircraft, date, time_utc, callsign, mission):
        self.add_page()
        self.set_xy(0, 65)
        self.set_font("Arial", 'B', 30)
        self.cell(0, 22, ascii_safe("RAW Preflight Briefing"), ln=True, align='C')
        self.ln(10)
        self.set_font("Arial", '', 17)
        self.cell(0, 10, ascii_safe(f"Pilot: {pilot}    Aircraft: {aircraft}    Callsign: {callsign}"), ln=True, align='C')
        self.cell(0, 10, ascii_safe(f"Mission: {mission}    Date: {date}    UTC: {time_utc}"), ln=True, align='C')
        self.ln(30)
    def metar_taf_gamet_section(self, metars, tafs, gamet):
        # METARs
        for entry in metars:
            icao = entry['icao'].upper()
            self.add_page()
            self.set_font("Arial", 'B', 22)
            self.cell(0, 15, f"METAR {icao}", ln=True, align='C')
            self.set_font("Arial", '', 13)
            self.multi_cell(0, 10, ascii_safe(entry["metar"]))
        # TAFs
        for entry in tafs:
            icao = entry['icao'].upper()
            self.add_page()
            self.set_font("Arial", 'B', 22)
            self.cell(0, 15, f"TAF {icao}", ln=True, align='C')
            self.set_font("Arial", '', 13)
            self.multi_cell(0, 10, ascii_safe(entry["taf"]))
        # GAMET
        if gamet and gamet.strip():
            self.add_page()
            self.set_font("Arial", 'B', 22)
            self.cell(0, 15, "GAMET/SIGMET/AIRMET", ln=True, align='C')
            self.set_font("Arial", '', 13)
            self.multi_cell(0, 10, ascii_safe(gamet))
    def chart_fullpage(self, charts):
        for chart in charts:
            self.add_page()
            self.set_font("Arial", 'B', 18)
            self.cell(0, 10, ascii_safe(chart['title']), ln=True, align='C')
            if chart.get("img_bytes"):
                chart_img_path = f"tmp_chart_{ascii_safe(chart['title']).replace(' ','_')}.png"
                with open(chart_img_path, "wb") as f:
                    f.write(chart["img_bytes"].getvalue())
                self.image(chart_img_path, x=15, y=25, w=270)

# ----------------- STREAMLIT APP ----------------
st.title("Preflight Weather Briefing")

with st.expander("1. Pilot/Aircraft Info", expanded=True):
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")
    mission = st.text_input("Mission", "")
    date = st.date_input("Date", datetime.date.today())
    time_utc = st.text_input("Expected Flight Time (UTC)", "")

def metar_taf_block():
    if "metar_taf_pairs" not in st.session_state:
        st.session_state.metar_taf_pairs = []
    st.subheader("2. METAR/TAF by Aerodrome")
    add_metar = st.button("Add another Aerodrome (METAR/TAF)")
    if add_metar:
        st.session_state.metar_taf_pairs.append({"icao":"", "metar":"", "taf":""})
    for i, entry in enumerate(st.session_state.metar_taf_pairs):
        cols = st.columns([0.15,0.45,0.4])
        entry["icao"] = cols[0].text_input("ICAO", value=entry.get("icao",""), key=f"icao_{i}")
        entry["metar"] = cols[1].text_area("METAR", value=entry.get("metar",""), key=f"metar_{i}", height=80)
        entry["taf"] = cols[2].text_area("TAF", value=entry.get("taf",""), key=f"taf_{i}", height=80)

def chart_block_multi(chart_key, label, title_base, desc_label="Area/focus for analysis"):
    if chart_key not in st.session_state:
        st.session_state[chart_key] = []
    st.subheader(label)
    add_chart = st.button(f"Add {label}")
    if add_chart:
        st.session_state[chart_key].append({"desc": "Portugal", "img_bytes": None, "title": title_base})
    for i, chart in enumerate(st.session_state[chart_key]):
        with st.expander(f"{label} {i+1}", expanded=True):
            chart["desc"] = st.text_input(desc_label, value=chart.get("desc","Portugal"), key=f"{chart_key}_desc_{i}")
            chart_file = st.file_uploader(f"Upload {label} (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"{chart_key}_file_{i}")
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

st.session_state.setdefault("gamet_raw", "")

metar_taf_block()
chart_block_multi("sigwx_charts", "Significant Weather Chart (SIGWX)", "Significant Weather Chart (SIGWX)")
chart_block_multi("windtemp_charts", "Wind and Temperature Chart", "Wind and Temperature Chart")
chart_block_multi("spc_charts", "Surface Pressure Chart (SPC)", "Surface Pressure Chart (SPC)")
st.subheader("6. GAMET/SIGMET/AIRMET (Raw)")
st.session_state["gamet_raw"] = st.text_area("Paste GAMET/SIGMET/AIRMET here (raw text):", value=st.session_state.get("gamet_raw", ""), height=100)

# -------- PDF GENERATION --------------
ready = (
    len(st.session_state.get("metar_taf_pairs", [])) > 0
    and len([c for c in st.session_state.get("sigwx_charts", []) if c.get("img_bytes")]) > 0
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
            metars = [{"icao": e["icao"], "metar": e["metar"]} for e in st.session_state["metar_taf_pairs"] if e["metar"].strip()]
            tafs = [{"icao": e["icao"], "taf": e["taf"]} for e in st.session_state["metar_taf_pairs"] if e["taf"].strip()]
            gamet = st.session_state.get("gamet_raw", "")
            pdf.metar_taf_gamet_section(metars, tafs, gamet)
            # SIGWX
            charts_all = []
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
                    ai_text = ai_chart_analysis_instructor(img_b64, "Significant Weather Chart (SIGWX)", chart.get("desc", "Portugal"))
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text})
            # Wind/Temp
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
                    ai_text = ai_chart_analysis_instructor(img_b64, "Wind and Temperature Chart", chart.get("desc", "Portugal"))
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text})
            # SPC
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
                    ai_text = ai_chart_analysis_instructor(img_b64, "Surface Pressure Chart (SPC)", chart.get("desc", "Portugal"))
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text})
            pdf.chart_section(charts_all)
            out_pdf = f"weather_briefing_detailed_{ascii_safe(mission)}.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                st.download_button(
                    label="Download Detailed Weather Briefing PDF",
                    data=f.read(),
                    file_name=out_pdf,
                    mime="application/pdf"
                )
    if col2.button("Gerar PDF RAW (para entregar)"):
        with st.spinner("Preparando PDF raw..."):
            pdf = RawLandscapePDF()
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission)
            metars = [{"icao": e["icao"], "metar": e["metar"]} for e in st.session_state["metar_taf_pairs"] if e["metar"].strip()]
            tafs = [{"icao": e["icao"], "taf": e["taf"]} for e in st.session_state["metar_taf_pairs"] if e["taf"].strip()]
            gamet = st.session_state.get("gamet_raw", "")
            pdf.metar_taf_gamet_section(metars, tafs, gamet)
            charts_all = []
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"]})
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"]})
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"]})
            pdf.chart_fullpage(charts_all)
            out_pdf = f"weather_briefing_raw_{ascii_safe(mission)}.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                st.download_button(
                    label="Download RAW Weather Briefing PDF",
                    data=f.read(),
                    file_name=out_pdf,
                    mime="application/pdf"
                )
else:
    st.info("Fill all sections and upload at least one chart of each type before generating your PDFs.")








