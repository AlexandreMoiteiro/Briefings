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

# --------- AI FUNCTIONS (corridas e explicativas) ---------
def ai_sigwx_chart_analysis(img_base64, chart_type, user_area_desc, lang="pt"):
    if lang == "en":
        prompt = (
            "You are an experienced aviation meteorologist. Looking at the SIGWX chart image provided, explain everything you see as if you were guiding a student pilot or dispatcher, using continuous, natural language. "
            "Cover all relevant meteorological elements, like fronts, cloud types and levels, areas of turbulence, icing, convective activity, freezing levels, visibility, surface features, pressure data, flight levels, UTC times, symbols or numbers, and any textual notes or legend. "
            "Instead of listing items or separating by sections, write in fluent, explanatory paragraphs, linking ideas naturally and providing clear, actionable interpretations. "
            "Clarify any ambiguities, for example if something could mean a time interval or a flight level, explaining what you assume and why, based on context and standard aviation conventions. "
            f"Focus especially on the region of interest: {user_area_desc}, always making sure your explanation flows logically and is easy to follow, just as if you were briefing someone new to SIGWX charts."
        )
    else:
        prompt = (
            "És um meteorologista aeronáutico experiente. Ao olhar para o chart SIGWX enviado, explica tudo o que observas como se estivesses a orientar um aluno-piloto ou despachante, escrevendo sempre em texto corrido, natural e didático. "
            "Inclui todos os aspetos relevantes, como frentes, tipos e níveis de nuvens, áreas de turbulência, gelo, convecção, níveis de congelamento, visibilidade, elementos à superfície, dados de pressão, níveis de voo, horários UTC, símbolos ou anotações numéricas, e qualquer nota ou legenda textual. "
            "Em vez de lista ou tópicos, escreve tudo em parágrafos explicativos, ligando naturalmente os conceitos e apresentando interpretações claras e práticas. "
            "Se surgir alguma ambiguidade, como por exemplo se um valor representa um intervalo horário ou um nível de voo, explica o que assumes e porquê, com base no contexto do chart e nas convenções de meteorologia aeronáutica. "
            f"Foca-te especialmente na área de interesse: {user_area_desc}, garantindo sempre que a explicação segue uma linha lógica, fácil de acompanhar e acessível a quem está a aprender a interpretar charts SIGWX."
        )

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text", "text": "Segue o chart SIGWX para análise completa."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
            ]}
        ],
        max_tokens=1800,
        temperature=0.15
    )
    return response.choices[0].message.content.strip()

def ai_spc_chart_analysis(img_base64, chart_type, user_area_desc, lang="pt"):
    if lang == "en":
        prompt = (
            "You are an experienced aviation meteorologist. Interpret the Surface Pressure Chart (SPC) provided, explaining your analysis in a single, continuous, explanatory paragraph, as if you were teaching a student pilot. "
            "Discuss isobars, pressure values, high and low centers, fronts (explaining their meaning and expected clouds or hazards), wind patterns, pressure gradients and their operational impact, weather symbols, any marked or colored zones, date/time/validity and the agency. "
            "Avoid lists or section titles. Instead, weave all information together in natural language, giving context and relevance for flight planning. "
            f"Give special attention to the region of interest: {user_area_desc}, ensuring your explanation is both informative and easy to follow, just as if you were briefing someone preparing a real flight."
        )
    else:
        prompt = (
            "És meteorologista aeronáutico experiente. Interpreta o Surface Pressure Chart (SPC) enviado, explicando toda a análise em texto corrido e contínuo, como se estivesses a ensinar um aluno-piloto. "
            "Fala sobre isóbaras, valores de pressão, centros de alta e baixa (explicando o significado, nuvens ou perigos que podem trazer), frentes, padrões e intensidade do vento, gradientes de pressão e seu impacto no voo, símbolos meteorológicos, zonas assinaladas ou coloridas, data/hora/validade e agência emissora. "
            "Não uses listas nem títulos de secção – integra toda a informação de forma fluida e lógica, apresentando o contexto e a relevância para o planeamento de voo. "
            f"Dedica especial atenção à região de interesse: {user_area_desc}, garantindo que a explicação é didática, informativa e fácil de seguir, como num briefing real para quem vai voar."
        )

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text", "text": "Segue o chart SPC para análise completa."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
            ]}
        ],
        max_tokens=1600,
        temperature=0.16
    )
    return response.choices[0].message.content.strip()

def ai_windtemp_chart_analysis(img_base64, chart_type, user_area_desc, lang="pt"):
    if lang == "en":
        prompt = (
            "You are an experienced aviation meteorologist. Looking at the wind and temperature chart provided, explain in a single, natural, flowing paragraph everything relevant for a pilot or flight planner: wind direction and speed at different flight levels, temperature values, jet streams and their characteristics, any turbulence or significant weather symbols, the relevant flight levels, time/validity, issuing agency, and any special notes or codes. "
            "Avoid lists or sections – instead, write as if you are guiding a student pilot through the chart, tying together all the details in an accessible, logical way. "
            f"Focus especially on the region of interest: {user_area_desc}, making sure your explanation is instructive, clear and operationally meaningful."
        )
    else:
        prompt = (
            "És meteorologista aeronáutico experiente. Ao analisar o chart de vento e temperatura fornecido, explica tudo o que for relevante para piloto ou planeador de voo num parágrafo corrido e natural: direção e intensidade do vento em cada nível de voo, valores de temperatura, características de jet streams, eventuais símbolos de turbulência ou tempo significativo, níveis de voo representados, horários de validade, agência emissora e eventuais notas ou códigos especiais. "
            "Evita listas ou secções – em vez disso, escreve como se estivesses a orientar um aluno-piloto através do chart, ligando todos os detalhes de forma acessível, lógica e clara. "
            f"Foca-te especialmente na região de interesse: {user_area_desc}, garantindo que a explicação é didática, operacional e fácil de compreender."
        )

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text", "text": "Segue o chart de vento/temperatura para análise completa."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
            ]}
        ],
        max_tokens=1600,
        temperature=0.16
    )
    return response.choices[0].message.content.strip()

def ai_metar_taf_analysis(raw_text, msg_type="METAR/TAF", icao="", lang="pt"):
    if lang == "en":
        prompt = (
            f"You're an experienced aviation meteorologist. Your task is to interpret this {msg_type} for a student pilot. "
            "Explain each code and section fluently, as if you're reading it aloud and teaching as you go. "
            "Write in continuous, natural language without bullet points or numbered sections. "
            "Highlight operational implications and help the student understand not just what it says, but what it means for a real flight. "
        )
    else:
        prompt = (
            f"És um meteorologista aeronáutico experiente. A tua tarefa é interpretar este {msg_type} para um aluno-piloto. "
            "Explica cada parte do código de forma contínua e didática, como se estivesses a ler em voz alta e a ensinar ao mesmo tempo. "
            "Escreve com linguagem fluida, sem usar bullets nem títulos nem secções enumeradas. "
            "Realça o impacto operacional da informação e ajuda o aluno a perceber não só o que está escrito, mas o que significa para o voo real. "
        )
    if icao:
        prompt += f" Este METAR/TAF é do aeródromo {icao}. Se for aplicável ao espaço aéreo português, menciona esse contexto."

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": raw_text}
        ],
        max_tokens=1200,
        temperature=0.18
    )
    return response.choices[0].message.content.strip()

def ai_gamet_analysis(gamet_text, lang="pt"):
    if lang == "en":
        prompt = (
            "You're a meteorologist explaining a GAMET/SIGMET/AIRMET message to a student pilot. "
            "Describe what each abbreviation and section means, but do it in a natural, flowing paragraph. "
            "Avoid bullets or lists. Focus on helping the student understand what the weather means for flight, in a continuous, clear explanation."
        )
    else:
        prompt = (
            "És meteorologista e estás a explicar um GAMET, SIGMET ou AIRMET a um aluno-piloto. "
            "Explica cada parte do texto de forma natural, contínua, e sem listas ou enumerações. "
            "Ajuda o aluno a perceber o que cada abreviatura e fenómeno meteorológico implica para o voo. "
            "Mantém um estilo claro, didático e corrido, como se estivesses a orientar diretamente alguém a preparar o voo."
        )

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": gamet_text}
        ],
        max_tokens=900,
        temperature=0.15
    )
    return response.choices[0].message.content.strip()

# --------- PDF CLASSES E FUNÇÕES AUXILIARES ---------
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

# --------- FUNÇÃO PARA OBTER AI_TEXT CONSOANTE TIPO DE CHART ---------
def obter_ai_texto_chart(chart, lang="pt"):
    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")
    title = chart.get("title", "").lower()
    if "sigwx" in title:
        return ai_sigwx_chart_analysis(img_b64, chart.get("title"), chart.get("desc", "Portugal"), lang=lang)
    elif "pressure" in title or "spc" in title:
        return ai_spc_chart_analysis(img_b64, chart.get("title"), chart.get("desc", "Portugal"), lang=lang)
    elif "wind" in title:
        return ai_windtemp_chart_analysis(img_b64, chart.get("title"), chart.get("desc", "Portugal"), lang=lang)
    else:
        return ai_sigwx_chart_analysis(img_b64, chart.get("title"), chart.get("desc", "Portugal"), lang=lang)

# --------- STREAMLIT APP ---------
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
            # Charts section (all with image + detailed analysis)
            charts_all = []
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    ai_text = obter_ai_texto_chart(chart, lang="pt")
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text, "subtitle": chart.get("subtitle","")})
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    ai_text = obter_ai_texto_chart(chart, lang="pt")
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text, "subtitle": chart.get("subtitle","")})
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    ai_text = obter_ai_texto_chart(chart, lang="pt")
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




