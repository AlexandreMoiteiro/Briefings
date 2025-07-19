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

# --------- AI FUNCTIONS (for each chart type) ---------
def ai_sigwx_chart_analysis(img_base64, chart_type, user_area_desc, lang="pt"):
    if lang == "en":
        prompt = (
            "You are an expert in aviation meteorology with a focus on interpreting SIGWX (Significant Weather) charts, especially those issued by AEMET or ICAO.\n\n"
            "Given an input SIGWX chart image, perform a comprehensive analysis of all relevant meteorological elements, including:\n"
            "- Fronts (cold, warm, occluded)\n"
            "- Cloud coverage (type, altitude range, coverage extent)\n"
            "- Areas of turbulence (including intensity and altitudinal or temporal extent)\n"
            "- Icing conditions and convective activity (e.g., CB, TCU)\n"
            "- Temperature 0°C isotherm levels\n"
            "- Visibility symbols (e.g., V1, V5)\n"
            "- Surface features (e.g., SFC indicators)\n"
            "- Pressure data, flight levels (FL), and UTC time intervals\n"
            "- Color-coded or symbol-marked zones (e.g., “1”, “2” annotations)\n"
            "- Any textual notes or legends provided\n\n"
            "Be particularly attentive to:\n"
            "- Differentiating time intervals (e.g., “12/15” as 12:00–15:00 UTC) versus vertical levels (e.g., FL120–FL150).\n"
            "- Matching the numbered annotations to the legend or remarks section.\n"
            "- Interpreting ISO-levels for freezing, and pressure altitude markers.\n\n"
            "Explain the chart in a structured and pedagogical way, appropriate for pilots, dispatchers, or flight planners. Use standard aviation meteorology terminology (ICAO/WMO compliant), but provide clear and actionable interpretations.\n\n"
            "Output your response as:\n"
            "1. General Overview (date, validity, issuing agency)\n"
            "2. Explanation of Fronts and Synoptic Situation\n"
            "3. Description of Weather Phenomena by Region\n"
            "4. Analysis of Hazards (Turbulence, Icing, CB)\n"
            "5. Notes on Freezing Levels, Visibility, and Special Symbols\n"
            "6. Summary for Flight Planning Use\n\n"
            "If any ambiguity arises (e.g., FL vs UTC), clarify your assumption based on chart context and standard interpretation.\n"
            f"\nFocus especially on the region of interest: {user_area_desc}."
        )
    else:
        prompt = (
            "És especialista em meteorologia aeronáutica, com foco na interpretação de charts SIGWX (Significant Weather), especialmente dos emitidos pela AEMET ou ICAO.\n\n"
            "Recebendo um chart SIGWX em imagem, faz uma análise exaustiva de todos os elementos meteorológicos relevantes, incluindo:\n"
            "- Frentes (frias, quentes, ocluídas)\n"
            "- Cobertura de nuvens (tipo, faixa de altitude, extensão)\n"
            "- Áreas de turbulência (incluindo intensidade e extensão altitudinal ou temporal)\n"
            "- Condições de gelo e convecção (CB, TCU)\n"
            "- Níveis da isoterma 0°C\n"
            "- Símbolos de visibilidade (ex: V1, V5)\n"
            "- Elementos à superfície (ex: indicação SFC)\n"
            "- Dados de pressão, níveis de voo (FL) e intervalos horários UTC\n"
            "- Zonas assinaladas por cor ou símbolo (ex: números “1”, “2”)\n"
            "- Todas as notas textuais ou legendas presentes\n\n"
            "Tem particular atenção a:\n"
            "- Diferenciar intervalos horários (ex: “12/15” como 12:00–15:00 UTC) de níveis verticais (ex: FL120–FL150)\n"
            "- Relacionar anotações numeradas com a legenda ou secção de remarks\n"
            "- Interpretar níveis ISO de congelação e marcadores de altitude/pressão\n\n"
            "Explica o chart de forma estruturada e pedagógica, adequada para pilotos, despachantes ou planners. Usa terminologia normalizada (ICAO/WMO), mas fornece interpretações claras e acionáveis.\n\n"
            "Estrutura a resposta assim:\n"
            "1. Visão Geral (data, validade, agência emissora)\n"
            "2. Explicação das Frentes e Situação Sinótica\n"
            "3. Descrição dos Fenómenos por Região\n"
            "4. Análise dos Riscos (Turbulência, Gelo, CB)\n"
            "5. Notas sobre Níveis de Congelamento, Visibilidade e Símbolos Especiais\n"
            "6. Resumo para Planeamento de Voo\n\n"
            "Se surgir ambiguidade (ex: FL vs UTC), esclarece qual a tua suposição com base no contexto do chart e nas convenções.\n"
            f"\nFoco especialmente na área de interesse: {user_area_desc}."
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
            "You are an expert in aviation meteorology with a focus on interpreting Surface Pressure Charts (SPC), especially those issued by ICAO or major meteorological agencies.\n\n"
            "Given an SPC chart image, perform a comprehensive analysis of all meteorologically relevant elements, including:\n"
            "- Isobars and pressure values (hPa)\n"
            "- High (H) and Low (L) pressure centers\n"
            "- Fronts (cold, warm, occluded, stationary, troughs)\n"
            "- Wind direction and speed indicators\n"
            "- Pressure gradients and their operational significance\n"
            "- Any weather symbols (precipitation, fog, etc)\n"
            "- Marked zones or annotations (numbers, colored regions)\n"
            "- Date/time/validity and issuing agency\n"
            "- Any textual notes or legends provided\n\n"
            "Explain the chart in a structured and pedagogical way, appropriate for pilots, dispatchers, or flight planners. Use standard aviation meteorology terminology (ICAO/WMO compliant), but provide clear and actionable interpretations.\n\n"
            "Output your response as:\n"
            "1. General Overview (date, validity, issuing agency)\n"
            "2. Explanation of Pressure Systems and Fronts\n"
            "3. Description of Wind Patterns and Zones\n"
            "4. Analysis of Pressure Gradients and Their Flight Impact\n"
            "5. Notes on Weather Symbols, Marked Zones, and Special Features\n"
            "6. Summary for Flight Planning Use\n\n"
            f"Focus especially on the region of interest: {user_area_desc}."
        )
    else:
        prompt = (
            "És especialista em meteorologia aeronáutica, com foco na interpretação de Surface Pressure Charts (SPC), especialmente os emitidos pela ICAO ou agências meteorológicas oficiais.\n\n"
            "Recebendo uma imagem SPC, faz uma análise exaustiva de todos os elementos meteorológicos relevantes, incluindo:\n"
            "- Isóbaras e valores de pressão (hPa)\n"
            "- Centros de Alta (H) e Baixa (L) explicando o que são e que nuvens/condições atmosféricas/perigos que podem trazer\n"
            "- Frentes (frias, quentes, ocluídas, estacionárias, troughs), explicando o que são e que nuvens/condições atmosféricas/perigos que podem trazer\n"
            "- Indicadores de direção e intensidade do vento\n"
            "- Gradientes de pressão e seu significado operacional\n"
            "- Símbolos meteorológicos (precipitação, nevoeiro, etc)\n"
            "- Zonas ou anotações marcadas (números, regiões coloridas)\n"
            "- Data/hora/validade e agência emissora\n"
            "- Qualquer nota textual ou legenda\n\n"
            "Explica o chart de forma estruturada e pedagógica, adequada para pilotos, despachantes ou planners. Usa terminologia normalizada (ICAO/WMO), mas fornece interpretações claras e acionáveis.\n\n"
            "Estrutura a resposta assim:\n"
            "1. Visão Geral (data, validade, agência emissora)\n"
            "2. Explicação dos Sistemas de Pressão e Frentes\n"
            "3. Descrição dos Padrões de Vento e Zonas\n"
            "4. Análise dos Gradientes de Pressão e Impacto no Voo\n"
            "5. Notas sobre Símbolos Meteorológicos, Zonas Marcadas e Particularidades\n"
            "6. Resumo para Planeamento de Voo\n\n"
            f"Foca especialmente na área de interesse: {user_area_desc}."
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
            "You are an expert in aviation meteorology, specialized in interpreting Wind and Temperature Charts, as used in flight planning and upper-air navigation.\n\n"
            "Given a wind/temperature chart image, analyze in detail all relevant meteorological elements, including:\n"
            "- Wind barbs/arrows (direction and speed at different FLs)\n"
            "- Temperature values (°C, including anomalies)\n"
            "- Flight level coverage (FLxxx)\n"
            "- Jet stream symbols or annotations (including speed and altitude)\n"
            "- Turbulence or significant weather symbols\n"
            "- Geographical reference points and coverage\n"
            "- Time/validity and issuing agency\n"
            "- Any notes, color codes, or legends\n\n"
            "Explain the chart in a structured and pedagogical way, suitable for pilots and flight planners. Use standard ICAO/WMO terminology but provide clear, actionable interpretations.\n\n"
            "Output your response as:\n"
            "1. General Overview (date, validity, issuing agency)\n"
            "2. Wind Patterns and Jet Streams\n"
            "3. Temperature Distribution and Anomalies\n"
            "4. Flight Level and Significant Zones\n"
            "5. Notes on Special Weather or Warnings\n"
            "6. Summary for Operational Flight Use\n\n"
            f"Focus especially on the region of interest: {user_area_desc}."
        )
    else:
        prompt = (
            "És especialista em meteorologia aeronáutica, com experiência em interpretação de Wind and Temperature Charts usados em navegação e planeamento de voo.\n\n"
            "Recebendo uma imagem de chart vento/temperatura, analisa em detalhe todos os elementos relevantes, incluindo:\n"
            "- Bárbaras/setas de vento (direção e intensidade em diferentes FLs)\n"
            "- Valores de temperatura (°C, incluindo anomalias)\n"
            "- Níveis de voo (FLxxx)\n"
            "- Símbolos ou anotações de jet stream (incluindo velocidade e altitude)\n"
            "- Símbolos de turbulência ou tempo significativo\n"
            "- Pontos e áreas geográficas de referência\n"
            "- Data/hora/validade e agência emissora\n"
            "- Notas, códigos de cor ou legendas\n\n"
            "Explica o chart de forma estruturada e pedagógica, para pilotos e planners. Usa terminologia oficial (ICAO/WMO) e interpretações claras e acionáveis.\n\n"
            "Estrutura a resposta assim:\n"
            "1. Visão Geral (data, validade, agência emissora)\n"
            "2. Padrões de Vento e Jet Streams\n"
            "3. Distribuição de Temperatura e Anomalias\n"
            "4. Níveis de Voo e Zonas Significativas\n"
            "5. Notas sobre Tempo Significativo ou Alertas\n"
            "6. Resumo para Operação de Voo\n\n"
            f"Foca especialmente na área de interesse: {user_area_desc}."
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

# --------- RESTO DAS FUNÇÕES (METAR/TAF, GAMET) ---------
def ai_metar_taf_analysis(raw_text, msg_type="METAR/TAF", icao="", lang="pt"):
    if lang == "en":
        prompt = (
            f"Explain this {msg_type} for a pilot preparing for an exam. Decode each section and code, describing what it means, why it's important, and how to interpret it. "
            "Do not omit any part or code. Use a clear, didactic style as if teaching a student.Don't use bullet points, just write as one would read."
        )
    else:
        prompt = (
            f"Explica este {msg_type} para um piloto a preparar-se para exame. Decifra cada secção e código, descrevendo o que significa, porque é importante e como se interpreta. "
            "Não omitas nenhum elemento. Usa um estilo claro e didático como se estivesses a ensinar um aluno.Usa texto corrido, como se estivesses a ler tudo seguido, sem bullet points."
        )
    if icao:
        prompt += f" ICAO: {icao}. Dá especial atenção ao contexto de Portugal se aplicável."
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
            "Explain in clear, didactic detail this GAMET/SIGMET/AIRMET message for a student pilot. "
            "Describe every code, abbreviation, area, meteorological phenomenon, and what it means for flight. "
            "Organize your explanation so it's easy to learn."
        )
    else:
        prompt = (
            "Explica de forma clara e didática este GAMET/SIGMET/AIRMET para um aluno-piloto. "
            "Descreve cada código, abreviatura, área, fenómeno meteorológico e o que significa para o voo. "
            "Organiza a explicação para ser fácil de aprender. Não omitas nada."
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




