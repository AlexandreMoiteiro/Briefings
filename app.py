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
import tempfile
import os

openai.api_key = st.secrets["OPENAI_API_KEY"]
AIRPORTS = airportsdata.load('ICAO')

def ascii_safe(text):
    return unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode('ascii')

def downscale_image(img, width=1300):
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

def ai_metar_taf_analysis(raw_text, msg_type="METAR/TAF", icao="", lang="pt"):
    if lang == "en":
        prompt = (
            f"Explain this {msg_type} line by line, every code and section, as if teaching a pilot for an exam. "
            "Do not omit anything. Decode every element in bullet points, including wind, visibility, remarks, QNH, BECMG, TEMPO, etc. "
            "For each code, explain the literal meaning and operational significance for pilots. "
            "If there are multiple lines/periods, explain all. Use very clear language."
        )
    else:
        prompt = (
            f"Explica este {msg_type} linha a linha, cada código, cada secção, como se fosse para um piloto a estudar para exame. "
            "Não omitas nada, explica tudo em bullet points, incluindo vento, visibilidade, remarks, QNH, BECMG, TEMPO, etc. "
            "Para cada código, explica o significado literal e a relevância operacional para pilotos. "
            "Se houver várias linhas/períodos, explica todas. Usa linguagem muito clara."
        )
    if icao:
        prompt += f" ICAO: {icao}. Foco especial: Portugal (se relevante)."
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": raw_text}
        ],
        max_tokens=1400,
        temperature=0.05
    )
    return response.choices[0].message.content.strip()

def ai_gamet_analysis(gamet_text, lang="pt"):
    if lang == "en":
        prompt = (
            "Explain this GAMET/SIGMET/AIRMET warning, line by line, in bullet points. "
            "Decode every code, abbreviation, area, risk, and meteorological phenomenon for a pilot. "
            "For each item, explain literally what it means and its operational impact. Do not omit or summarize anything."
        )
    else:
        prompt = (
            "Explica este GAMET/SIGMET/AIRMET, linha a linha e em bullet points. "
            "Explica cada código, abreviatura, área, risco e fenómeno meteorológico para um piloto. "
            "Para cada item, explica literalmente o que significa e o impacto operacional. Não omitas nem resumas nada."
        )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": gamet_text}
        ],
        max_tokens=1000,
        temperature=0.05
    )
    return response.choices[0].message.content.strip()

def ai_chart_analysis_instructor(img_base64, chart_type, user_area_desc, lang="pt"):
    LEGEND_SIGWX_EN = """
Standard SIGWX chart legend (WMO/ICAO):
- Blue triangles = cold front; red semicircles = warm front; purple alternating triangles/semicircles = occlusion; brown dashed = trough; scalloped/curly = area of significant weather (not a front); bold solid = jet stream; zigzag = turbulence; dashed lines = tropopause or FL contours.
- CB = cumulonimbus, ISOL = isolated, OCNL = occasional, EMBD = embedded, FRQ = frequent, OBSC = obscured.
- Scalloped lines (wavy/curly, not sharp triangles/semicircles): delimit areas of significant cloud/weather, NOT a front.
- All symbols must be interpreted per ICAO Annex 3 / WMO No. 49.
"""
    LEGEND_SIGWX_PT = """
Legenda SIGWX padrão (OMM/OACI):
- Triângulos azuis = frente fria; semicircunferências vermelhas = frente quente; símbolos roxos mistos = oclusão; castanho tracejado = trough; linha ondulada/scalloped = área de tempo significativo (NÃO é frente); traço forte = jetstream; zigzag = turbulência; tracejado = tropopausa ou níveis de voo.
- CB = cumulonimbus, ISOL = isolado, OCNL = ocasional, EMBD = embebido, FRQ = frequente, OBSC = obscurecido.
- Linhas onduladas/scalloped (onduladas/curly, não triângulos ou semicircunferências): delimitam áreas de nuvens ou tempo significativo, NÃO são frentes.
- Todos os símbolos seguem OACI Anexo 3 / OMM Nº 49.
"""
    if lang == "en":
        intro = (
            "You are an aviation meteorology instructor. Given an official SIGWX, wind or pressure chart image as published by ICAO/WMO, help a student pilot to decode every element."
            "\n\nInstructions:"
            "\n- For EVERY symbol, line, color, code, or annotation visible on the chart (even in the legend):"
            "\n  1. Describe **literally** what you see (shape, color, label, code)."
            "\n  2. ONLY interpret or identify if you are 100% sure per WMO/ICAO conventions. If unsure, say so."
            "\n  3. NEVER claim a scalloped/wavy/curly line is a front (cold/warm/occlusion/trough)! Only call a front if the symbol matches standard WMO/ICAO symbology."
            "\n  4. If a legend is present, start by decoding all its items before the rest."
            "\n  5. Do NOT summarize or omit anything. Bullet point every element, even if repeated."
            "\n  6. If relevant, mention impact for Portugal, but do not omit other areas."
            f"\n\nUse this as reference:\n{LEGEND_SIGWX_EN}"
        )
    else:
        intro = (
            "És instrutor de meteorologia aeronáutica. Vais receber um chart SIGWX, vento ou pressão oficial (OACI/OMM). Ajuda um aluno-piloto a decifrar todos os elementos."
            "\n\nInstruções:"
            "\n- Para CADA símbolo, linha, cor, código ou anotação visível (mesmo na legenda):"
            "\n  1. Descreve literalmente o que vês (forma, cor, rótulo, código)."
            "\n  2. Só identifica/interpreta se tiveres 100% de certeza pelas convenções OMM/OACI. Se não souberes, diz."
            "\n  3. NUNCA digas que uma linha ondulada/scalloped é uma frente (fria, quente, oclusão, trough)! Só identifica frente se for símbolo exato segundo OACI/OMM."
            "\n  4. Se houver legenda, começa por explicar todos os itens da legenda antes do resto."
            "\n  5. NÃO resumas nem omitas nada. Faz bullet points para todos os elementos, mesmo que repetidos."
            "\n  6. Se relevante, refere impacto para Portugal, mas não omitas outras áreas."
            f"\n\nUsa isto como referência:\n{LEGEND_SIGWX_PT}"
        )

    if "sigwx" in chart_type.lower():
        tipo = "Significant Weather (SIGWX)"
    elif "pressure" in chart_type.lower() or "spc" in chart_type.lower():
        tipo = "surface pressure"
    elif "wind" in chart_type.lower():
        tipo = "wind/temperature"
    else:
        tipo = "aviation meteorology"

    prompt = intro + f"\n\nThis is a {tipo} chart. Image follows."

    modelo_ai = "gpt-4-vision-preview"
    try:
        response = openai.chat.completions.create(
            model=modelo_ai,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": "Decode every visible symbol, code, or annotation. Be exhaustive. If unsure, say so. Start with legend if visible."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                ]}
            ],
            max_tokens=1800,
            temperature=0.05
        )
    except Exception:
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": "Decode every visible symbol, code, or annotation. Be exhaustive. If unsure, say so. Start with legend if visible."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                ]}
            ],
            max_tokens=1200,
            temperature=0.06
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
    def cover_page(self, pilot, aircraft, date, time_utc, callsign, mission):
        self.add_page(orientation='L')
        self.set_xy(0,65)
        self.set_font("Arial", 'B', 30)
        self.cell(0, 22, ascii_safe("Preflight Weather Briefing"), ln=True, align='C')
        self.ln(10)
        self.set_font("Arial", '', 17)
        self.cell(0, 10, ascii_safe(f"Piloto: {pilot}    Aeronave: {aircraft}    Callsign: {callsign}"), ln=True, align='C')
        self.cell(0, 10, ascii_safe(f"Missão: {mission}    Data: {date}    UTC: {time_utc}"), ln=True, align='C')
        self.ln(30)
    def metar_taf_section(self, pairs):
        self.add_page(orientation='P')
        self.set_font("Arial", 'B', 20)
        self.cell(0, 12, "METAR/TAF", ln=True, align='C')
        self.set_font("Arial", '', 12)
        for entry in pairs:
            icao = entry['icao'].upper()
            self.set_font("Arial", 'B', 14)
            self.cell(0, 9, f"{icao}", ln=True)
            self.set_font("Arial", '', 12)
            if entry.get("metar","").strip():
                self.cell(0, 7, "METAR (Raw):", ln=True)
                self.multi_cell(0, 7, ascii_safe(entry['metar']))
                ai_text = ai_metar_taf_analysis(entry["metar"], msg_type="METAR", icao=icao, lang="pt")
                self.set_font("Arial", 'I', 11)
                self.multi_cell(0, 7, ascii_safe(ai_text))
            if entry.get("taf","").strip():
                self.cell(0, 7, "TAF (Raw):", ln=True)
                self.multi_cell(0, 7, ascii_safe(entry['taf']))
                ai_text = ai_metar_taf_analysis(entry["taf"], msg_type="TAF", icao=icao, lang="pt")
                self.set_font("Arial", 'I', 11)
                self.multi_cell(0, 7, ascii_safe(ai_text))
            self.ln(5)
    def gamet_page(self, gamet):
        if gamet and gamet.strip():
            self.add_page(orientation='P')
            self.set_font("Arial", 'B', 16)
            self.cell(0, 12, "GAMET/SIGMET/AIRMET", ln=True, align='C')
            self.ln(2)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 7, ascii_safe(gamet))
            ai_text = ai_gamet_analysis(gamet, lang="pt")
            self.set_font("Arial", 'I', 11)
            self.multi_cell(0, 7, ascii_safe(ai_text))
    def chart_section(self, charts):
        for i, chart in enumerate(charts):
            self.add_page(orientation='L')
            self.set_font("Arial", 'B', 18)
            self.cell(0, 10, ascii_safe(chart['title']), ln=True, align='C')
            if chart.get("subtitle"):
                self.set_font("Arial", 'I', 14)
                self.cell(0, 8, ascii_safe(chart['subtitle']), ln=True, align='C')
            if chart.get("img_bytes"):
                max_w = self.w - 30
                max_h = self.h - 55
                img = Image.open(chart["img_bytes"])
                iw, ih = img.size
                ratio = min(max_w/iw, max_h/ih)
                final_w, final_h = int(iw*ratio), int(ih*ratio)
                x = (self.w-final_w)//2
                y = self.get_y() + 8
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
                    img.save(tmp_img, format="PNG")
                    tmp_img_path = tmp_img.name
                self.image(tmp_img_path, x=x, y=y, w=final_w, h=final_h)
                os.remove(tmp_img_path)
                self.ln(final_h+5)
            ai_text = chart.get("ai_text", "")
            if ai_text:
                self.set_font("Arial", '', 12)
                self.multi_cell(0, 8, ascii_safe(ai_text))
                self.ln(2)

class RawLandscapePDF(FPDF):
    def __init__(self):
        super().__init__()
    def header(self): pass
    def footer(self): pass
    def cover_page(self, pilot, aircraft, date, time_utc, callsign, mission):
        self.add_page(orientation='L')
        self.set_xy(0,65)
        self.set_font("Arial", 'B', 30)
        self.cell(0, 22, ascii_safe("Weather Briefing"), ln=True, align='C')
        self.ln(10)
        self.set_font("Arial", '', 17)
        self.cell(0, 10, ascii_safe(f"Pilot: {pilot}    Aircraft: {aircraft}    Callsign: {callsign}"), ln=True, align='C')
        self.cell(0, 10, ascii_safe(f"Mission: {mission}    Date: {date}    UTC: {time_utc}"), ln=True, align='C')
        self.ln(30)
    def metar_taf_section(self, pairs):
        self.add_page(orientation='P')
        self.set_font("Arial", 'B', 20)
        self.cell(0, 12, "METAR/TAF", ln=True, align='C')
        self.set_font("Arial", '', 13)
        for entry in pairs:
            icao = entry['icao'].upper()
            self.set_font("Arial", 'B', 14)
            self.cell(0, 9, f"{icao}", ln=True)
            self.set_font("Arial", '', 12)
            if entry.get("metar","").strip():
                self.multi_cell(0, 7, ascii_safe(entry['metar']))
                self.ln(2)
            if entry.get("taf","").strip():
                self.multi_cell(0, 7, ascii_safe(entry['taf']))
            self.ln(3)
    def gamet_page(self, gamet):
        if gamet and gamet.strip():
            self.add_page(orientation='P')
            self.set_font("Arial", 'B', 16)
            self.cell(0, 12, "GAMET/SIGMET/AIRMET", ln=True, align='C')
            self.ln(2)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 7, ascii_safe(gamet))
    def chart_fullpage(self, charts):
        for i, chart in enumerate(charts):
            self.add_page(orientation='L')
            self.set_font("Arial", 'B', 18)
            self.cell(0, 10, ascii_safe(chart['title']), ln=True, align='C')
            if chart.get('subtitle'):
                self.set_font("Arial", 'I', 14)
                self.cell(0, 8, ascii_safe(chart['subtitle']), ln=True, align='C')
            if chart.get("img_bytes"):
                max_w = self.w - 30
                max_h = self.h - 55
                img = Image.open(chart["img_bytes"])
                iw, ih = img.size
                ratio = min(max_w/iw, max_h/ih)
                final_w, final_h = int(iw*ratio), int(ih*ratio)
                x = (self.w-final_w)//2
                y = self.get_y() + 8
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
                    img.save(tmp_img, format="PNG")
                    tmp_img_path = tmp_img.name
                self.image(tmp_img_path, x=x, y=y, w=final_w, h=final_h)
                os.remove(tmp_img_path)

# ----------------- STREAMLIT APP ----------------

st.title("Preflight Weather Briefing")

with st.expander("Pilot/Aircraft Info", expanded=True):
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")
    mission = st.text_input("Mission", "")
    date = st.date_input("Date", datetime.date.today())
    time_utc = st.text_input("Expected Flight Time (UTC)", "")

def metar_taf_block():
    if "metar_taf_pairs" not in st.session_state:
        st.session_state.metar_taf_pairs = []
    st.subheader("METAR/TAF por Aeródromo")
    cols_add, cols_rem = st.columns([0.4,0.6])
    if cols_add.button("Adicionar Aeródromo (METAR/TAF)"):
        st.session_state.metar_taf_pairs.append({"icao":"", "metar":"", "taf":""})
    remove_indexes = []
    for i, entry in enumerate(st.session_state.metar_taf_pairs):
        cols = st.columns([0.18,0.41,0.35,0.06])
        entry["icao"] = cols[0].text_input("ICAO", value=entry.get("icao",""), key=f"icao_{i}")
        entry["metar"] = cols[1].text_area("METAR", value=entry.get("metar",""), key=f"metar_{i}", height=70)
        entry["taf"] = cols[2].text_area("TAF", value=entry.get("taf",""), key=f"taf_{i}", height=70)
        if cols[3].button("❌", key=f"remove_metar_taf_{i}"):
            remove_indexes.append(i)
    for idx in sorted(remove_indexes, reverse=True):
        st.session_state.metar_taf_pairs.pop(idx)

def chart_block_multi(chart_key, label, title_base, subtitle_label):
    if chart_key not in st.session_state:
        st.session_state[chart_key] = []
    st.subheader(label)
    cols_add, cols_rem = st.columns([0.6,0.4])
    if cols_add.button(f"Adicionar {label}"):
        st.session_state[chart_key].append({"desc": "Portugal", "img_bytes": None, "title": title_base, "subtitle": ""})
    remove_indexes = []
    for i, chart in enumerate(st.session_state[chart_key]):
        with st.expander(f"{label} {i+1}", expanded=True):
            cols = st.columns([0.6,0.34,0.06])
            chart["desc"] = cols[0].text_input("Área/foco para análise", value=chart.get("desc","Portugal"), key=f"{chart_key}_desc_{i}")
            chart["subtitle"] = cols[1].text_input(subtitle_label, value=chart.get("subtitle",""), key=f"{chart_key}_subtitle_{i}")
            if cols[2].button("❌", key=f"remove_{chart_key}_{i}"):
                remove_indexes.append(i)
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
    for idx in sorted(remove_indexes, reverse=True):
        st.session_state[chart_key].pop(idx)

# Main form blocks
metar_taf_block()
chart_block_multi("sigwx_charts", "Significant Weather Chart (SIGWX)", "Significant Weather Chart (SIGWX)", "Issuing Organization")
chart_block_multi("windtemp_charts", "Wind and Temperature Chart", "Wind and Temperature Chart", "Flight Levels (e.g. FL050-FL340)")
chart_block_multi("spc_charts", "Surface Pressure Chart (SPC)", "Surface Pressure Chart (SPC)", "Chart Validity Time (e.g. 09Z-12Z)")

st.subheader("GAMET/SIGMET/AIRMET (Raw)")
st.session_state["gamet_raw"] = st.text_area("Paste GAMET/SIGMET/AIRMET here (raw text):", value=st.session_state.get("gamet_raw", ""), height=100)

ready = (
    any([c.get("img_bytes") for c in st.session_state.get("sigwx_charts", [])]) or
    any([c.get("img_bytes") for c in st.session_state.get("windtemp_charts", [])]) or
    any([c.get("img_bytes") for c in st.session_state.get("spc_charts", [])]) or
    len([e for e in st.session_state.get("metar_taf_pairs", []) if e.get("metar","").strip() or e.get("taf","").strip()]) > 0 or
    st.session_state.get("gamet_raw","").strip()
)

col1, col2 = st.columns(2)
if ready:
    if col1.button("Gerar PDF COMPLETO (detalhado, português)"):
        with st.spinner("Preparando PDF detalhado..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission)
            metar_taf_pairs = [
                entry for entry in st.session_state.get("metar_taf_pairs", [])
                if entry.get("metar","").strip() or entry.get("taf","").strip()
            ]
            gamet = st.session_state.get("gamet_raw", "")
            pdf.metar_taf_section(metar_taf_pairs)
            pdf.gamet_page(gamet)
            charts_all = []
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
                    ai_text = ai_chart_analysis_instructor(img_b64, chart.get("title"), chart.get("desc", "Portugal"), lang="pt")
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text, "subtitle": chart.get("subtitle","")})
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
                    ai_text = ai_chart_analysis_instructor(img_b64, chart.get("title"), chart.get("desc", "Portugal"), lang="pt")
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text, "subtitle": chart.get("subtitle","")})
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
                    ai_text = ai_chart_analysis_instructor(img_b64, chart.get("title"), chart.get("desc", "Portugal"), lang="pt")
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text, "subtitle": chart.get("subtitle","")})
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
    if col2.button("Gerar PDF RAW (entregar, inglês)"):
        with st.spinner("Preparando PDF raw..."):
            pdf = RawLandscapePDF()
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission)
            metar_taf_pairs = [
                entry for entry in st.session_state.get("metar_taf_pairs", [])
                if entry.get("metar","").strip() or entry.get("taf","").strip()
            ]
            gamet = st.session_state.get("gamet_raw", "")
            pdf.metar_taf_section(metar_taf_pairs)
            pdf.gamet_page(gamet)
            charts_all = []
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "subtitle": chart.get("subtitle","")})
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "subtitle": chart.get("subtitle","")})
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "subtitle": chart.get("subtitle","")})
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
    st.info("Preenche pelo menos uma secção (METAR/TAF, GAMET ou um chart) para gerar os PDFs.")


