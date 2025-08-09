# app.py
import streamlit as st
from PIL import Image
import io
import base64
import datetime
import unicodedata
import airportsdata
import tempfile
import os
import time
import requests
from typing import Optional, Tuple, Dict, Any, List, Set

# ==========================
# OpenAI (Responses API)
# ==========================
from openai import OpenAI
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])  # expects key in .streamlit/secrets.toml

# ==========================
# CONSTANTES / DADOS
# ==========================
AIRPORTS = airportsdata.load('ICAO')  # { 'LPPT': {'name': '...', 'lat':..., 'lon':..., 'country': 'Portugal', ...} }

# Mapeamento básico ICAO -> FIR (Europa focado; facilmente estensível)
# Nota: Portugal tem 2 FIRs: LPPC (Lisboa, continental) e LPPO (Santa Maria, Açores/Oceanic)
PORTUGAL_AZORES_ICAO: Set[str] = {
    "LPAZ","LPLA","LPPD","LPPI","LPFL","LPHR","LPGR","LPSJ","LPCR","LPFR"  # LPFR é Faro (continente) -> LPPC; mantido só a título de exemplo
}
# Corrige a lista para incluir só Açores e remover LPFR/Faro (continente):
PORTUGAL_AZORES_ICAO = {"LPAZ","LPLA","LPPD","LPPI","LPFL","LPHR","LPGR","LPSJ"}

FIR_BY_PREFIX = {
    # Portugal
    "LP": "LPPC",   # fallback se não constar na lista de Açores
    # Espanha (simplificado; Madrid FIR é LECM e Barcelona FIR é LECB)
    "LE": "LECM",
    # França (Paris FIR principal, simplificação)
    "LF": "LFFF",
    # UK
    "EG": "EGTT",
    # Irlanda
    "EI": "EISN",
    # Alemanha (simplificado, múltiplas FIRs na prática)
    "ED": "EDGG",
    # Itália (simplificado)
    "LI": "LIRR",
}

# Idiomas suportados
LANGS = {"Português": "pt", "English": "en"}

TXT = {
    "pt": {
        "title": "Preflight Weather Briefing",
        "pilot_info": "Pilot/Aircraft Info",
        "pilot": "Piloto",
        "aircraft": "Aeronave",
        "callsign": "Callsign",
        "mission": "Missão",
        "date": "Data",
        "utc": "Hora prevista (UTC)",
        "metar_taf_title": "METAR/TAF por Aeródromo",
        "add_aerodrome": "Adicionar Aeródromo (METAR/TAF)",
        "icao": "ICAO",
        "metar": "METAR",
        "taf": "TAF",
        "sigwx": "Significant Weather Chart (SIGWX)",
        "windtemp": "Wind and Temperature Chart",
        "spc": "Surface Pressure Chart (SPC)",
        "area_focus": "Área/foco para análise",
        "subtitle_org": "Entidade emissora",
        "subtitle_fl": "Níveis de voo (ex.: FL050–FL340)",
        "subtitle_valid": "Validade do chart (ex.: 09Z–12Z)",
        "upload": "Upload",
        "gamet": "GAMET/SIGMET/AIRMET (Raw)",
        "btn_detailed": "Gerar PDF COMPLETO (detalhado, português)",
        "btn_raw": "Gerar PDF RAW (entregar, inglês)",
        "fill_any": "Preenche pelo menos uma secção (METAR/TAF, GAMET ou um chart) para gerar os PDFs.",
        "down_detailed": "Download Detailed Weather Briefing PDF",
        "down_raw": "Download RAW Weather Briefing PDF",
        "model": "Modelo",
        "strict": "Modo estrito (anti-alucinação)",
        "auto_fetch": "Ir buscar METAR/TAF automaticamente",
        "fir_code": "FIR para SIGMET (ex.: LPPC)",
        "fetch_now": "Atualizar agora",
        "warn_icao": "ICAO desconhecido",
        "sigmet_title": "SIGMETs recentes (texto cru)",
        "auto_fir": "Detetar FIR automaticamente a partir dos ICAO"
    },
    "en": {
        "title": "Preflight Weather Briefing",
        "pilot_info": "Pilot/Aircraft Info",
        "pilot": "Pilot",
        "aircraft": "Aircraft",
        "callsign": "Callsign",
        "mission": "Mission",
        "date": "Date",
        "utc": "Expected Flight Time (UTC)",
        "metar_taf_title": "METAR/TAF per Aerodrome",
        "add_aerodrome": "Add Aerodrome (METAR/TAF)",
        "icao": "ICAO",
        "metar": "METAR",
        "taf": "TAF",
        "sigwx": "Significant Weather Chart (SIGWX)",
        "windtemp": "Wind and Temperature Chart",
        "spc": "Surface Pressure Chart (SPC)",
        "area_focus": "Area/focus for analysis",
        "subtitle_org": "Issuing Organization",
        "subtitle_fl": "Flight Levels (e.g., FL050–FL340)",
        "subtitle_valid": "Chart Validity (e.g., 09Z–12Z)",
        "upload": "Upload",
        "gamet": "GAMET/SIGMET/AIRMET (Raw)",
        "btn_detailed": "Generate FULL PDF (detailed)",
        "btn_raw": "Generate RAW PDF (delivery)",
        "fill_any": "Fill at least one section (METAR/TAF, GAMET or a chart) to generate PDFs.",
        "down_detailed": "Download Detailed Weather Briefing PDF",
        "down_raw": "Download RAW Weather Briefing PDF",
        "model": "Model",
        "strict": "Strict mode (anti-hallucination)",
        "auto_fetch": "Auto-fetch METAR/TAF",
        "fir_code": "FIR for SIGMET (e.g., LPPC)",
        "fetch_now": "Refresh now",
        "warn_icao": "Unknown ICAO",
        "sigmet_title": "Recent SIGMETs (raw)",
        "auto_fir": "Auto-detect FIR from ICAOs"
    }
}

# ==========================
# UTILS
# ==========================
def ascii_safe(text):
    return unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode('ascii')

def downscale_image(img: Image.Image, width: int = 1300):
    if img.width > width:
        ratio = width / img.width
        img = img.resize((width, int(img.height * ratio)))
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG", optimize=True)
    img_bytes.seek(0)
    return img, img_bytes

# ==========================
# FIR helpers
# ==========================

def icao_to_fir(icao: str) -> Optional[str]:
    """Heurística simples para atribuir FIR por ICAO.
    - Açores -> LPPO; resto de Portugal -> LPPC
    - Outros países por prefixo (simplificação)
    """
    if not icao or len(icao) != 4:
        return None
    icao = icao.upper()
    if icao.startswith("LP"):
        if icao in PORTUGAL_AZORES_ICAO:
            return "LPPO"
        return "LPPC"
    pref = icao[:2]
    return FIR_BY_PREFIX.get(pref)

# ==========================
# FETCHERS: METAR/TAF & SIGMET (AVWX ou CheckWX)
# ==========================
# Define em secrets:
# OPENAI_API_KEY = "..."
# (Opcional) AVWX_API_KEY = "..."  ou  CHECKWX_API_KEY = "..."

@st.cache_data(ttl=300)
def fetch_metar_taf(icao: str) -> Tuple[str, str, Optional[str]]:
    icao = (icao or '').strip().upper()
    if not icao:
        return "", "", None

    avwx_key = st.secrets.get("AVWX_API_KEY")
    checkwx_key = st.secrets.get("CHECKWX_API_KEY")

    try:
        if avwx_key:
            headers = {"Authorization": avwx_key}
            m = requests.get(f"https://avwx.rest/api/metar/{icao}", headers=headers, params={"format": "json"}, timeout=10)
            t = requests.get(f"https://avwx.rest/api/taf/{icao}", headers=headers, params={"format": "json"}, timeout=10)
            m.raise_for_status(); t.raise_for_status()
            metar = m.json().get("raw", "").strip()
            taf = t.json().get("raw", "").strip()
            return metar, taf, "avwx"
        elif checkwx_key:
            headers = {"X-API-Key": checkwx_key}
            m = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers=headers, timeout=10)
            t = requests.get(f"https://api.checkwx.com/taf/{icao}/decoded", headers=headers, timeout=10)
            m.raise_for_status(); t.raise_for_status()
            mj = m.json(); tj = t.json()
            metar = mj.get("data", [""])[0] if mj.get("data") else ""
            taf = tj.get("data", [""])[0] if tj.get("data") else ""
            if isinstance(metar, dict):
                metar = metar.get("raw_text") or metar.get("raw") or ""
            if isinstance(taf, dict):
                taf = taf.get("raw_text") or taf.get("raw") or ""
            return (metar or "").strip(), (taf or "").strip(), "checkwx"
        else:
            return "", "", None
    except Exception:
        return "", "", None

@st.cache_data(ttl=300)
def get_sigmet_checkwx(fir):
    url = f"https://api.checkwx.com/sigmet/{fir}/decoded"
    headers = {"X-API-Key": CHECKWX_API_KEY}
    try:
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return ""
        data = resp.json()
        if not data.get("data"):
            return ""
        sigmets = []
        for sig in data["data"]:
            # opcional: filtrar fenómenos menos relevantes
            phenomenon = sig.get("phenomenon", "").upper()
            if phenomenon in ["VA", "RDOACT CLD"]:  # Vulcão e radiação — ignorar se quiseres só VFR
                continue
            sigmets.append(sig.get("raw", ""))
        return "\n\n".join(sigmets) if sigmets else ""
    except Exception as e:
        return ""

".join(texts), "avwx"
        elif checkwx_key:
            headers = {"X-API-Key": checkwx_key}
            r = requests.get(f"https://api.checkwx.com/sigmet/{fir}", headers=headers, timeout=12)
            r.raise_for_status()
            data = r.json().get("data", [])
            texts = []
            for it in data:
                if isinstance(it, dict):
                    raw = it.get("raw_text") or it.get("raw") or it.get("report")
                else:
                    raw = str(it)
                if raw: texts.append(raw)
            return "

".join(texts), "checkwx"
        else:
            return "", None
    except Exception:
        return "", None

# ==========================
# PROMPTS / GPT-5 HELPERS
# ==========================

def gpt5_vision_explain(prompt_sys: str, user_text: str, img_base64: Optional[str] = None,
                        model: str = "gpt-5", max_tokens: int = 1800, temperature: float = 0.15,
                        retries: int = 2, strict: bool = False) -> str:
    content = [{"type": "input_text", "text": user_text}]
    if img_base64:
        content.append({
            "type": "input_image",
            "image_data": img_base64,
            "mime_type": "image/png"
        })

    if strict:
        prompt_sys += "
Regra estrita: se algum detalhe nao estiver visivel/legivel no chart, diz explicitamente 'ilegivel/nao visivel' e NAO infiras."

    for attempt in range(retries + 1):
        try:
            resp = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": [{"type": "input_text", "text": prompt_sys}]},
                    {"role": "user",   "content": content}
                ],
                max_output_tokens=max_tokens,
                temperature=temperature,
            )
            return (resp.output_text or "").strip()
        except Exception as e:
            if attempt == retries:
                return f"Nao foi possivel analisar agora (erro: {e})."
            time.sleep(0.8 * (attempt + 1))


def ai_sigwx_chart_analysis(img_base64: str, chart_type: str, user_area_desc: str, lang: str, model: str, strict: bool) -> str:
    if lang == "en":
        sys = (
            "You are a senior aviation meteorologist. Explain the SIGWX chart in continuous, 
"
            "operational prose using only what is visible. If a detail is unclear or unreadable, say so explicitly 
"
            "and do not infer. Resolve ambiguities by stating the assumption and why (standard conventions). Be precise."
        )
        user = (
            f"Analyze this SIGWX focusing on {user_area_desc}. Cover fronts, cloud types/levels, turbulence, icing, convection, 
"
            f"freezing levels, visibility constraints, surface features, pressure data, flight levels, UTC times, symbols, notes/legend. 
"
            f"Keep 1–2 coherent paragraphs."
        )
    else:
        sys = (
            "Es meteorologista aeronáutico sénior. Explica o SIGWX em texto corrido e operacional, 
"
            "usando apenas o que esta visivel. Se algo estiver ilegivél/ambiguo, diz explicitamente e nao infiras. 
"
            "Resolve ambiguidades indicando a suposicao e o motivo (convenções aeronáuticas). Se preciso, usa termos portugueses padrao."
        )
        user = (
            f"Analisa este SIGWX com foco em {user_area_desc}. Fala de frentes, tipos/níveis de nuvens, turbulência, gelo, convecção, 
"
            f"níveis de congelamento, restrições de visibilidade, elementos de superficie, dados de pressão, níveis de voo, horas UTC, 
"
            f"símbolos e notas/legenda. Mantem 1–2 parágrafos."
        )
    return gpt5_vision_explain(sys, user, img_base64=img_base64, model=model, max_tokens=1800, temperature=0.14, strict=strict)


def ai_spc_chart_analysis(img_base64: str, chart_type: str, user_area_desc: str, lang: str, model: str, strict: bool) -> str:
    if lang == "en":
        sys = (
            "You are a senior aviation meteorologist. Explain the Surface Pressure Chart in flowing prose, no lists. 
"
            "Use only what is visible. Flag uncertainties explicitly."
        )
        user = (
            f"Interpret the SPC with special attention to {user_area_desc}. Discuss isobars/spacing, H/L centers, fronts (likely clouds/hazards), 
"
            f"wind patterns, pressure gradients and operational impact, weather symbols/zones, validity (UTC) and issuer. One coherent paragraph."
        )
    else:
        sys = (
            "Es meteorologista aeronáutico sénior. Explica o SPC em texto corrido (sem listas), 
"
            "usando apenas o que esta visivel. Assinala incertezas." 
        )
        user = (
            f"Interpreta o SPC com foco em {user_area_desc}. Fala de isóbaras/gradiente, centros A/B, frentes (nuvens/perigos), 
"
            f"padrões de vento, impacto operacional, símbolos/zonas, validade (UTC) e emissor. Um parágrafo."
        )
    return gpt5_vision_explain(sys, user, img_base64=img_base64, model=model, max_tokens=1600, temperature=0.14, strict=strict)


def ai_windtemp_chart_analysis(img_base64: str, chart_type: str, user_area_desc: str, lang: str, model: str, strict: bool) -> str:
    if lang == "en":
        sys = (
            "You are a senior aviation meteorologist. Explain winds/temperatures in continuous prose. Only use visible data; avoid assumptions."
        )
        user = (
            f"Analyze the wind/temperature chart emphasizing {user_area_desc}. Summarize wind direction/speed by flight level, temperatures, 
"
            f"jet streams (axes/width/core speeds), turbulence/icing markers, levels shown, validity and issuer. Concise but operational."
        )
    else:
        sys = (
            "Es meteorologista aeronáutico sénior. Explica vento/temperatura em texto corrido, usando apenas o que ves, sem extrapolar."
        )
        user = (
            f"Analisa o chart de vento/temperatura com foco em {user_area_desc}. Resume direcao/intensidade por nivel, temperaturas, 
"
            f"eixos/forca de jet streams, eventuais simbolos de turbulencia/gelo, niveis representados, validade e emissor."
        )
    return gpt5_vision_explain(sys, user, img_base64=img_base64, model=model, max_tokens=1600, temperature=0.14, strict=strict)


def ai_metar_taf_analysis(raw_text: str, msg_type: str = "METAR/TAF", icao: str = "", lang: str = "pt", model: str = "gpt-5", strict: bool = False) -> str:
    if lang == "en":
        sys = (
            "You are a senior aviation meteorologist. Read and interpret the message in fluent prose. 
"
            "Explain codes as you go, state operational implications, avoid bullet points."
        )
        user = f"Interpret this {msg_type} for {icao or 'the aerodrome'} in one coherent explanation:
{raw_text}"
    else:
        sys = (
            "Es meteorologista aeronáutico sénior. Lê e interpreta a mensagem em texto corrido, explicando os códigos à medida que avanças 
"
            "e destacando implicações operacionais. Sem listas."
        )
        user = f"Interpreta este {msg_type} para {icao or 'o aeródromo'} numa explicação coerente:
{raw_text}"

    if strict:
        sys += "
Regra: nao adivinhar informacao ausente; assinalar incerteza explicitamente."

    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": sys}]},
                {"role": "user",   "content": [{"type": "input_text", "text": user}]}
            ],
            max_output_tokens=1200,
            temperature=0.16
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Nao foi possivel interpretar agora (erro: {e})."


def ai_gamet_analysis(gamet_text: str, lang: str = "pt", model: str = "gpt-5", strict: bool = False) -> str:
    if lang == "en":
        sys = ("You are a senior aviation meteorologist. Explain a GAMET/SIGMET/AIRMET in one flowing paragraph, "
               "clarifying abbreviations inline, focusing on flight impact.")
        user = f"Explain this message in continuous prose:
{gamet_text}"
    else:
        sys = ("Es meteorologista aeronáutico sénior. Explica um GAMET/SIGMET/AIRMET num parágrafo corrido, "
               "esclarecendo abreviaturas no contexto e focando impacto no voo.")
        user = f"Explica este texto em prosa contínua:
{gamet_text}"

    if strict:
        sys += "
Regra: nao inventar; assinalar incertezas."

    try:
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": sys}]},
                {"role": "user",   "content": [{"type": "input_text", "text": user}]}
            ],
            max_output_tokens=900,
            temperature=0.15
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Nao foi possivel interpretar agora (erro: {e})."

# ==========================
# PDF CLASSES
# ==========================
from fpdf import FPDF
import fitz

class BriefingPDF(FPDF):
    def header(self):
        pass
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
    def metar_taf_section(self, pairs, lang: str, model: str, strict: bool):
        self.add_page(orientation='P')
        self.set_font("Arial", 'B', 20)
        self.cell(0, 12, "METAR/TAF", ln=True, align='C')
        self.set_font("Arial", '', 12)
        for entry in pairs:
            icao = entry['icao'].upper()
            self.set_font("Arial", 'B', 14)
            self.cell(0, 9, f"{icao}", ln=True)
            self.set_font("Arial", '', 12)
            if entry.get("metar"," ").strip():
                self.cell(0, 7, "METAR (Raw):", ln=True)
                self.multi_cell(0, 7, ascii_safe(entry['metar']))
                ai_text = ai_metar_taf_analysis(entry["metar"], msg_type="METAR", icao=icao, lang=lang, model=model, strict=strict)
                self.set_font("Arial", 'I', 11)
                self.multi_cell(0, 7, ascii_safe(ai_text))
            if entry.get("taf"," ").strip():
                self.cell(0, 7, "TAF (Raw):", ln=True)
                self.multi_cell(0, 7, ascii_safe(entry['taf']))
                ai_text = ai_metar_taf_analysis(entry["taf"], msg_type="TAF", icao=icao, lang=lang, model=model, strict=strict)
                self.set_font("Arial", 'I', 11)
                self.multi_cell(0, 7, ascii_safe(ai_text))
            self.ln(5)
    def gamet_page(self, gamet, lang: str, model: str, strict: bool):
        if gamet and gamet.strip():
            self.add_page(orientation='P')
            self.set_font("Arial", 'B', 16)
            self.cell(0, 12, "GAMET/SIGMET/AIRMET", ln=True, align='C')
            self.ln(2)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 7, ascii_safe(gamet))
            ai_text = ai_gamet_analysis(gamet, lang=lang, model=model, strict=strict)
            self.set_font("Arial", 'I', 11)
            self.multi_cell(0, 7, ascii_safe(ai_text))
    def chart_section(self, charts, lang: str, model: str, strict: bool):
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
                img = Image.open(chart["img_bytes"]) if isinstance(chart["img_bytes"], str) else Image.open(chart["img_bytes"])  # BytesIO
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
            if entry.get("metar"," ").strip():
                self.multi_cell(0, 7, ascii_safe(entry['metar']))
                self.ln(2)
            if entry.get("taf"," ").strip():
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
                img = Image.open(chart["img_bytes"]) if isinstance(chart["img_bytes"], str) else Image.open(chart["img_bytes"])  # BytesIO
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

# ==========================
# FUNÇÃO PARA ROTEAR CHARTS
# ==========================

def obter_ai_texto_chart(chart: Dict[str, Any], lang: str, model: str, strict: bool):
    img_b64 = base64.b64encode(chart["img_bytes"].getvalue()).decode("utf-8")  # sem prefixo
    title = chart.get("title", "").lower()
    area = chart.get("desc", "Portugal")
    if "sigwx" in title:
        return ai_sigwx_chart_analysis(img_b64, chart.get("title"), area, lang=lang, model=model, strict=strict)
    elif "pressure" in title or "spc" in title:
        return ai_spc_chart_analysis(img_b64, chart.get("title"), area, lang=lang, model=model, strict=strict)
    elif "wind" in title:
        return ai_windtemp_chart_analysis(img_b64, chart.get("title"), area, lang=lang, model=model, strict=strict)
    else:
        return ai_sigwx_chart_analysis(img_b64, chart.get("title"), area, lang=lang, model=model, strict=strict)

# ==========================
# UI
# ==========================
st.set_page_config(page_title="Preflight Weather Briefing", layout="wide")
st.title(TXT["pt"]["title"])  # título é igual nas duas línguas

ui_lang = st.sidebar.selectbox("Language / Idioma", list(LANGS.keys()), index=0)
lang = LANGS[ui_lang]

model = st.sidebar.selectbox(TXT[lang]["model"], ["gpt-5", "gpt-5-mini"], index=0)
strict_mode = st.sidebar.toggle(TXT[lang]["strict"], value=True)
auto_fetch = st.sidebar.toggle(TXT[lang]["auto_fetch"], value=True)
auto_fir = st.sidebar.toggle(TXT[lang]["auto_fir"], value=True)
fir_default = "LPPC"  # Lisboa FIR por omissão
fir_code = st.sidebar.text_input(TXT[lang]["fir_code"], value=fir_default)
refresh_now = st.sidebar.button(TXT[lang]["fetch_now"])  # força refetch

with st.expander(TXT[lang]["pilot_info"], expanded=True):
    pilot = st.text_input(TXT[lang]["pilot"], "")
    aircraft = st.text_input(TXT[lang]["aircraft"], "")
    callsign = st.text_input(TXT[lang]["callsign"], "")
    mission = st.text_input(TXT[lang]["mission"], "")
    date = st.date_input(TXT[lang]["date"], datetime.date.today())
    time_utc = st.text_input(TXT[lang]["utc"], "")

# ============ METAR/TAF BLOCK ============
if "metar_taf_pairs" not in st.session_state:
    st.session_state.metar_taf_pairs = []

st.subheader(TXT[lang]["metar_taf_title"])
cols_add, cols_rem = st.columns([0.4,0.6])
if cols_add.button(TXT[lang]["add_aerodrome"]):
    st.session_state.metar_taf_pairs.append({"icao":"", "metar":"", "taf":""})

remove_indexes: List[int] = []
icao_set: Set[str] = set()
for i, entry in enumerate(st.session_state.metar_taf_pairs):
    cols = st.columns([0.18,0.35,0.35,0.06,0.06])
    entry["icao"] = cols[0].text_input(TXT[lang]["icao"], value=entry.get("icao",""), key=f"icao_{i}").upper()

    if entry["icao"]:
        icao_set.add(entry["icao"])  # para deteção automática de FIR

    # Fetch automático METAR/TAF por ICAO
    if auto_fetch and entry["icao"] and (refresh_now or not entry.get("_fetched")):
        metar, taf, src = fetch_metar_taf(entry["icao"])  # cacheado 5 min
        if metar or taf:
            entry["metar"] = metar
            entry["taf"] = taf
            entry["_fetched"] = True
        else:
            entry["_fetched"] = False

    entry["metar"] = cols[1].text_area(TXT[lang]["metar"], value=entry.get("metar",""), key=f"metar_{i}", height=70)
    entry["taf"] = cols[2].text_area(TXT[lang]["taf"], value=entry.get("taf",""), key=f"taf_{i}", height=70)

    warn_placeholder = cols[3].empty()
    if entry["icao"] and entry["icao"].upper() not in AIRPORTS:
        warn_placeholder.warning(TXT[lang]["warn_icao"])  # avisa mas não bloqueia

    if cols[4].button("❌", key=f"remove_metar_taf_{i}"):
        remove_indexes.append(i)

for idx in sorted(remove_indexes, reverse=True):
    st.session_state.metar_taf_pairs.pop(idx)

# Auto-deteção de FIR a partir dos ICAO introduzidos
if auto_fir and icao_set:
    # Escolhe o primeiro FIR inferido; se existirem múltiplos, mantém o atual e avisa
    inferred = {icao: icao_to_fir(icao) for icao in sorted(icao_set)}
    fir_candidates = {fir for fir in inferred.values() if fir}
    if len(fir_candidates) == 1:
        only_fir = list(fir_candidates)[0]
        if only_fir and only_fir != fir_code:
            fir_code = only_fir
    elif len(fir_candidates) > 1:
        st.info(f"ICAO de diferentes FIR detectados: {inferred}. Mantido FIR manual: {fir_code}")

# ============ SIGWX/WIND/SPC BLOCKS ============

def chart_block_multi(chart_key: str, label: str, title_base: str, subtitle_label: str):
    if chart_key not in st.session_state:
        st.session_state[chart_key] = []
    st.subheader(label)
    cols_add, cols_rem = st.columns([0.6,0.4])
    if cols_add.button(f"+ {label}"):
        st.session_state[chart_key].append({"desc": "Portugal", "img_bytes": None, "title": title_base, "subtitle": ""})
    remove_indexes = []
    for i, chart in enumerate(st.session_state[chart_key]):
        with st.expander(f"{label} {i+1}", expanded=True):
            cols = st.columns([0.6,0.34,0.06])
            chart["desc"] = cols[0].text_input(TXT[lang]["area_focus"], value=chart.get("desc","Portugal"), key=f"{chart_key}_desc_{i}")
            chart["subtitle"] = cols[1].text_input(subtitle_label, value=chart.get("subtitle",""), key=f"{chart_key}_subtitle_{i}")
            if cols[2].button("❌", key=f"remove_{chart_key}_{i}"):
                remove_indexes.append(i)
            chart_file = st.file_uploader(f"{TXT[lang]['upload']} {label} (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"{chart_key}_file_{i}")
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

chart_block_multi("sigwx_charts", TXT[lang]["sigwx"], TXT[lang]["sigwx"], TXT[lang]["subtitle_org"])
chart_block_multi("windtemp_charts", TXT[lang]["windtemp"], TXT[lang]["windtemp"], TXT[lang]["subtitle_fl"])
chart_block_multi("spc_charts", TXT[lang]["spc"], TXT[lang]["spc"], TXT[lang]["subtitle_valid"])

# ============ SIGMET (texto cru) ============
st.subheader(TXT[lang]["sigmet_title"])
if st.button(TXT[lang]["fetch_now"], key="fetch_sigmet_now"):
    st.cache_data.clear()  # refresh imediato

sigmet_text, sigmet_src = ("", None)
if fir_code:
    sigmet_text, sigmet_src = fetch_sigmet(fir_code)

st.session_state["gamet_raw"] = st.text_area(TXT[lang]["gamet"], value=sigmet_text or st.session_state.get("gamet_raw", ""), height=120)

# ============ READINESS ============
ready = (
    any([c.get("img_bytes") for c in st.session_state.get("sigwx_charts", [])]) or
    any([c.get("img_bytes") for c in st.session_state.get("windtemp_charts", [])]) or
    any([c.get("img_bytes") for c in st.session_state.get("spc_charts", [])]) or
    len([e for e in st.session_state.get("metar_taf_pairs", []) if e.get("metar"," ").strip() or e.get("taf"," ").strip()]) > 0 or
    st.session_state.get("gamet_raw"," ").strip()
)

col1, col2 = st.columns(2)

# ============ BOTÕES DE PDF ============
if ready:
    if col1.button(TXT[lang]["btn_detailed"]):
        with st.spinner("Preparando PDF detalhado..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission[:60])
            metar_taf_pairs = [entry for entry in st.session_state.get("metar_taf_pairs", []) if entry.get("metar"," ").strip() or entry.get("taf"," ").strip()]
            gamet = st.session_state.get("gamet_raw", "")
            pdf.metar_taf_section(metar_taf_pairs, lang=lang, model=model, strict=strict_mode)
            pdf.gamet_page(gamet, lang=lang, model=model, strict=strict_mode)

            # Charts com AI
            charts_all = []
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    ai_text = obter_ai_texto_chart(chart, lang=lang, model=model, strict=strict_mode)
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text, "subtitle": chart.get("subtitle","")})
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    ai_text = obter_ai_texto_chart(chart, lang=lang, model=model, strict=strict_mode)
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text, "subtitle": chart.get("subtitle","")})
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    ai_text = obter_ai_texto_chart(chart, lang=lang, model=model, strict=strict_mode)
                    charts_all.append({"title": chart.get("title"), "img_bytes": chart["img_bytes"], "ai_text": ai_text, "subtitle": chart.get("subtitle","")})

            pdf.chart_section(charts_all, lang=lang, model=model, strict=strict_mode)
            out_pdf = f"weather_briefing_detailed_{ascii_safe(mission)[:40]}.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                st.download_button(
                    label=TXT[lang]["down_detailed"],
                    data=f.read(),
                    file_name=out_pdf,
                    mime="application/pdf"
                )

    if col2.button(TXT[lang]["btn_raw"]):
        with st.spinner("Preparando PDF raw..."):
            pdf = RawLandscapePDF()
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission[:60])
            metar_taf_pairs = [entry for entry in st.session_state.get("metar_taf_pairs", []) if entry.get("metar"," ").strip() or entry.get("taf"," ").strip()]
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
            out_pdf = f"weather_briefing_raw_{ascii_safe(mission)[:40]}.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                st.download_button(
                    label=TXT[lang]["down_raw"],
                    data=f.read(),
                    file_name=out_pdf,
                    mime="application/pdf"
                )
else:
    st.info(TXT[lang]["fill_any"])


# requirements.txt
# -----------------
# streamlit
# pillow
# requests
# openai>=1.40.0
# fpdf2
# pymupdf
# airportsdata





