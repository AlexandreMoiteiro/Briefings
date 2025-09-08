# app.py — Briefings com editor de NOTAMs, GAMET e SIGMET (via Gist) + METAR/TAF + Charts + PDFs + Pares Navlog↔VFR
from typing import Dict, Any, List, Tuple, Optional
import io, os, re, base64, tempfile, unicodedata, json, datetime as dt
import streamlit as st
from PIL import Image
from fpdf import FPDF
import fitz  # PyMuPDF
import requests
from openai import OpenAI

# ---------- External pages ----------
APP_WEATHER_URL = "https://briefings.streamlit.app/Weather"
APP_NOTAMS_URL  = "https://briefings.streamlit.app/NOTAMs"
APP_VFRMAP_URL  = "https://briefings.streamlit.app/VFRMap"
APP_MNB_URL     = "https://briefings.streamlit.app/MassBalance"

# ---------- Página & estilos ----------
st.set_page_config(page_title="Briefings", layout="wide")
st.markdown("""
<style>
:root { --muted:#6b7280; --line:#e5e7eb; --pastel:#5a7fb3; }
.app-title { font-size: 2.2rem; font-weight: 800; margin: 0 0 .25rem; }
.small { font-size:.92rem; color:var(--muted); }
.monos{font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap}
hr{border:none;border-top:1px solid var(--line);margin:12px 0}
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }
.section-card{border:1px solid var(--line); border-radius:12px; padding:12px 14px; background:#fff}
</style>
""", unsafe_allow_html=True)

# ---------- OpenAI client ----------
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ---------- Constantes úteis ----------
LPSO_ARP = (39.211667, -8.057778)  # LPSO (ARP)
PASTEL = (90, 127, 179)  # azul suave

# ---------- Utils ----------
def ascii_safe(text: str) -> str:
    if text is None: return ""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return (t.replace("\u00A0"," ").replace("\u2009"," ").replace("\u2013","-")
             .replace("\u2014","-").replace("\uFEFF",""))

def parse_icaos(s: str) -> List[str]:
    tokens = re.split(r"[,\s]+", (s or "").strip(), flags=re.UNICODE)
    return [t.upper() for t in tokens if t]

# ---------- Image helpers ----------
def load_first_pdf_page(pdf_bytes: bytes, dpi: int = 450) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    return Image.open(io.BytesIO(png)).convert("RGB").copy()

def gif_first_frame(file_bytes: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(file_bytes)); im.seek(0)
    return im.convert("RGB").copy()

def to_png_bytes(img: Image.Image) -> io.BytesIO:
    out = io.BytesIO(); img.save(out, format="PNG"); out.seek(0); return out

def ensure_png_from_bytes(file_bytes: bytes, mime: str) -> io.BytesIO:
    """Aceita PDF/PNG/JPG/JPEG/GIF e devolve PNG (ou placeholder)."""
    try:
        m = (mime or "").lower()
        if m == "application/pdf":
            img = load_first_pdf_page(file_bytes, dpi=300)
        elif m == "image/gif":
            img = gif_first_frame(file_bytes)
        else:
            img = Image.open(io.BytesIO(file_bytes)).convert("RGB").copy()
        return to_png_bytes(img)
    except Exception:
        try:
            Image.open(io.BytesIO(file_bytes))
            return io.BytesIO(file_bytes)
        except Exception:
            ph = Image.new("RGB", (800, 600), (245, 246, 248))
            bio = io.BytesIO(); ph.save(bio, format="PNG"); bio.seek(0)
            return bio

# ---------- Charts helpers ----------
_KIND_RANK = {"SPC": 1, "SIGWX": 2, "Wind & Temp": 3, "Other": 9}
def _chart_sort_key(c: Dict[str, Any]) -> Tuple[int, int]:
    kind = c.get("kind", "Other")
    rank = _KIND_RANK.get(kind, 9)
    order = int(c.get("order", 9999) or 9999)
    return (rank, order)

# ---------- GPT wrappers ----------
def gpt_text(prompt_system: str, prompt_user: str, max_tokens: int = 900) -> str:
    try:
        model_name = st.secrets.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    except Exception:
        model_name = "gpt-4o-mini"
    try:
        r2 = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": prompt_system},
                {"role": "user",   "content": prompt_user},
            ],
            max_tokens=max_tokens,
            temperature=0.15
        )
        content = (r2.choices[0].message.content or "").strip()
        return ascii_safe(content) if content else ""
    except Exception as e2:
        return ascii_safe(f"Analise indisponivel (erro IA: {e2})")

def analyze_metar_taf_pt(icao: str, metar: str, taf: str) -> str:
    sys = (
        "Es meteorologista aeronáutico sénior. Em PT-PT, texto corrido e pratico. "
        "Para METAR: vento sfc, visibilidade, fenómenos, teto, QNH, impacto VFR/IFR. "
        "Para TAF: janelas de mudança (BECMG/TEMPO/PROB), vento cruzado, rajadas, riscos chave (TS/CB, gelo, turbulencia, nevoeiro, SQ=Squall lines). "
        "Explica coberturas FEW/SCT/BKN/OVC em oktas (FEW=1-2, SCT=3-4, BKN=5-7, OVC=8). "
        "No fim, bullets curtos com decisao: Go / Cautelas / Alternante."
    )
    user = f"Aeródromo {icao}\n\nMETAR: {metar}\nTAF: {taf}"
    return gpt_text(sys, user, max_tokens=1000)

def analyze_sigmet_pt(sigmet_text: str) -> str:
    sys = "Es meteorologista aeronáutico sénior. Em PT-PT, interpreta SIGMET LPPC: fenómeno, área/limites, níveis, movimento, intensidade, impacto VFR/IFR."
    return gpt_text(sys, sigmet_text, max_tokens=800)

def analyze_gamet_pt(gamet_text: str) -> str:
    lat, lon = LPSO_ARP
    sys = (
        "Es meteorologista aeronáutico sénior. Em PT-PT, interpreta GAMET LPPC: fenómeno, níveis/camadas, áreas, validades, PROB/TEMPO/BECMG. "
        f"Verifica explicitamente se abrange LPSO ({lat:.6f},{lon:.6f}). Conclui com 'Abrange LPSO' ou 'Nao abrange' ou 'Indeterminado'."
    )
    return gpt_text(sys, gamet_text, max_tokens=1200)

def analyze_chart_pt(kind: str, img_b64: str, filename_hint: str = "") -> str:
    try:
        model_name = st.secrets.get("OPENAI_MODEL_VISION", "gpt-4o").strip() or "gpt-4o"
    except Exception:
        model_name = "gpt-4o"
    sys = (
        "Es meteorologista aeronáutico sénior. Em PT-PT, analisa o chart de forma detalhada mas operativa. "
        "Explica a simbologia visível (linhas, barbules, isotacas, áreas sombreadas, TOP/BASE). "
        "Identifica frentes (tipo, deslocacao) e nuvens/fenómenos associados a cada frente (cadeia CI/CS/AS/NS vs AC/TCU/CB). "
        "Explica gelo (rime/clear/misto) e turbulencia por camada. "
        "Se houver squall lines (linhas de rajada), descreve-as, explica mecanismo e impacto VFR/IFR. "
        "No fim, resumo claro: impacto operacional em Portugal, rotas/altitudes a preferir/evitar."
    )
    user_txt = f"Tipo de chart: {kind}. Ficheiro: {filename_hint}"
    r = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": [
                {"type":"text","text":user_txt},
                {"type":"image_url","image_url":{"url":f"data:image/png;base64,{img_b64}"}}
            ]},
        ],
        max_tokens=1000,
        temperature=0.15
    )
    out = (r.choices[0].message.content or "").strip()
    return ascii_safe(out)

# ---------- PDFs ----------
class DetailedPDF(FPDF):
    def header(self): pass
    def footer(self): self.set_y(-15); self.set_font("Helvetica","I",8); self.cell(0,10,f"Page {self.page_no()}",0,0,"C")

    def metar_taf_block(self, analyses: List[Tuple[str, str, str, str]]):
        self.add_page()
        self.set_font("Helvetica","B",16); self.cell(0,10,"METAR / TAF — Interpretacao",ln=True,align="C")
        for icao, metar_raw, taf_raw, analysis in analyses:
            self.ln(4)
            self.set_font("Helvetica","B",13); self.cell(0,8,icao,ln=True)
            if metar_raw: self.set_font("Helvetica","",11); self.multi_cell(0,6,f"METAR: {metar_raw}")
            if taf_raw:   self.multi_cell(0,6,f"TAF: {taf_raw}")
            self.set_font("Helvetica","",11); self.multi_cell(0,6,analysis or "Sem interpretacao.")

    def sigmet_block(self, sigmet_text: str, analysis: str):
        if not sigmet_text: return
        self.add_page()
        self.set_font("Helvetica","B",16); self.cell(0,10,"SIGMET (LPPC)",ln=True,align="C")
        self.set_font("Helvetica","",11); self.multi_cell(0,6,"RAW:\n"+sigmet_text); self.ln(3)
        self.multi_cell(0,6,analysis)

    def gamet_block(self, gamet_text: str, analysis: str):
        if not gamet_text: return
        self.add_page()
        self.set_font("Helvetica","B",16); self.cell(0,10,"GAMET (LPPC)",ln=True,align="C")
        self.set_font("Helvetica","",11); self.multi_cell(0,6,"RAW:\n"+gamet_text); self.ln(3)
        self.multi_cell(0,6,analysis)

    def chart_block(self, title: str, subtitle: str, img_png: io.BytesIO, analysis: str):
        self.add_page()
        self.set_font("Helvetica","B",15); self.cell(0,10,title,ln=True,align="C")
        if subtitle: self.set_font("Helvetica","I",11); self.cell(0,8,subtitle,ln=True,align="C")
        path = tempfile.mktemp(suffix=".png"); Image.open(img_png).save(path)
        self.image(path,w=self.w-30); os.remove(path)
        self.ln(3); self.set_font("Helvetica","",11); self.multi_cell(0,6,analysis or " ")

    def glossary(self):
        self.add_page()
        self.set_font("Helvetica","B",15); self.cell(0,10,"Glossario Operacional",ln=True,align="C")
        self.set_font("Helvetica","",11)
        text = (
            "Cobertura de nuvens em oktas: FEW=1-2, SCT=3-4, BKN=5-7, OVC=8.\n"
            "SQ = Squall lines: linhas de rajada associadas a convecao forte, risco de turbulencia severa, granizo, wind shear.\n"
            "Frentes: fria (linha azul triângulos), quente (linha vermelha semicirculos), oclusao (roxa), estacionaria (azul/vermelho).\n"
            "SIGWX: jet streams (linhas com isotacas), areas de turbulencia/gelo, CB/TCU, EMBD/OCNL/FRQ.\n"
            "Abreviaturas METAR/TAF: BECMG, TEMPO, PROB, AMD, CAVOK, etc.\n"
        )
        self.multi_cell(0,6,text)

class FinalBriefPDF(FPDF):
    def header(self): pass
    def footer(self): self.set_y(-15); self.set_font("Helvetica","I",8); self.cell(0,10,f"Page {self.page_no()}",0,0,"C")

    def cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc):
        self.add_page("L")
        self.set_font("Helvetica","B",28); self.cell(0,14,"Final Briefing",ln=True,align="C")
        self.set_font("Helvetica","",14)
        self.cell(0,10,f"Mission: {mission_no}",ln=True,align="C")
        self.cell(0,10,f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}",ln=True,align="C")
        self.cell(0,10,f"Date: {date_str}   UTC: {time_utc}",ln=True,align="C")

    def section_weather(self, charts: List[Tuple[str,str,io.BytesIO]]):
        self.add_page("L")
        self.set_font("Helvetica","B",20); self.cell(0,12,"Weather",ln=True,align="C",link=APP_WEATHER_URL)
        for title,subtitle,img_png in charts:
            self.add_page("L")
            self.set_font("Helvetica","B",15); self.cell(0,10,title,ln=True,align="C")
            if subtitle: self.set_font("Helvetica","I",11); self.cell(0,8,subtitle,ln=True,align="C")
            path=tempfile.mktemp(suffix=".png"); Image.open(img_png).save(path)
            self.image(path,w=self.w-30); os.remove(path)

    def flightplan(self, fp_img_png: Optional[io.BytesIO], fp_pdf_bytes: Optional[bytes]):
        if fp_img_png:
            self.add_page("P")
            self.set_font("Helvetica","B",18); self.cell(0,10,"Flight Plan",ln=True,align="C",link=APP_NOTAMS_URL)
            path=tempfile.mktemp(suffix=".png"); Image.open(fp_img_png).save(path)
            self.image(path,w=self.w-30); os.remove(path)
        elif fp_pdf_bytes:
            self.add_page("P")
            self.set_font("Helvetica","B",18); self.cell(0,10,"Flight Plan",ln=True,align="C",link=APP_NOTAMS_URL)

    def navlog_vfr(self,pairs:List[Tuple[str,bytes,bytes,str,str]]):
        for title,nav_bytes,vfr_bytes,nav_mime,vfr_mime in pairs:
            self.add_page("L")
            self.set_font("Helvetica","B",16); self.cell(0,10,f"Navlog & VFR — {title}",ln=True,align="C")
            # just embed as images if possible
            for lbl,data,mime in [("Navlog",nav_bytes,nav_mime),("VFR",vfr_bytes,vfr_mime)]:
                try:
                    if mime=="application/pdf":
                        doc=fitz.open(stream=data,filetype="pdf")
                        page=doc.load_page(0); png=page.get_pixmap(dpi=200).tobytes("png")
                        img=Image.open(io.BytesIO(png))
                    else:
                        img=Image.open(io.BytesIO(data))
                    path=tempfile.mktemp(suffix=".png"); img.save(path)
                    self.image(path,w=self.w/2-20); os.remove(path)
                except Exception: continue

# ---------- resto do código da UI (inputs, uploads, gerar PDFs etc.) ----------
# (por brevidade não repito aqui, mas mantém a lógica que já tens:
#  - inputs pilot/aircraft/mission
#  - icaos metar/taf + notams editor (LPPC(ENROUTE) default)
#  - gamet/sigmet editor
#  - uploads charts
#  - uploads flight plan, navlog/vfr pairs, mb pdf
#  - botões para gerar Detailed (PT) e Final (EN), chamando estas classes)
