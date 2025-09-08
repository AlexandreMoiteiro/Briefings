# app.py — Briefings com editor de NOTAMs, GAMET e SIGMET (via Gist) + METAR/TAF + Charts + PDFs
from typing import Dict, Any, List, Tuple
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
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }
:root { --muted:#6b7280; --line:#e5e7eb; --pastel:#5a7fb3; }
.app-title { font-size: 2.1rem; font-weight: 800; margin: 0 0 .25rem; }
.section { margin-top: 18px; }
.small { font-size:.92rem; color:var(--muted); }
.monos{font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap}
hr{border:none;border-top:1px solid var(--line);margin:12px 0}
</style>
""", unsafe_allow_html=True)

# ---------- OpenAI client ----------
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ---------- Constantes úteis ----------
LPSO_ARP = (39.211667, -8.057778)  # Ponte de Sor

# ---------- Utils ----------
def ascii_safe(text: str) -> str:
    if text is None: return ""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii","ignore").decode("ascii")
    return (t.replace("\u00A0"," ").replace("\u2009"," ").replace("\u2013","-")
             .replace("\u2014","-").replace("\uFEFF",""))

def parse_icaos(s: str) -> List[str]:
    tokens = re.split(r"[,\s]+", (s or "").strip(), flags=re.UNICODE)
    return [t.upper() for t in tokens if t]

# ---------- Image helpers ----------
def load_first_pdf_page(pdf_bytes: bytes, dpi: int = 450):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf"); page = doc.load_page(0)
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    return Image.open(io.BytesIO(png)).convert("RGB").copy()

def gif_first_frame(file_bytes: bytes):
    im = Image.open(io.BytesIO(file_bytes)); im.seek(0)
    return im.convert("RGB").copy()

def to_png_bytes(img: Image.Image) -> io.BytesIO:
    out = io.BytesIO(); img.save(out, format="PNG"); out.seek(0); return out

def ensure_png_from_bytes(file_bytes: bytes, mime: str) -> io.BytesIO:
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

# ---------- Texto auxiliar de PDFs ----------
def extract_pdf_text_first_page(pdf_bytes: bytes) -> str:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc.load_page(0)
        return page.get_text("text") or ""
    except Exception:
        return ""

# ---------- Ordenação lógica de charts ----------
_KIND_RANK = {"SPC": 1, "SIGWX": 2, "Wind & Temp": 3, "Other": 9}
def _chart_sort_key(c: Dict[str, Any]) -> Tuple[int, int]:
    kind = c.get("kind", "Other")
    rank = _KIND_RANK.get(kind, 9)
    order = int(c.get("order", 9999) or 9999)
    return (rank, order)

# ---------- METAR/TAF ----------
def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","\n").strip()
    return {"X-API-Key": key} if key else {}

def fetch_metar_now(icao: str) -> str:
    try:
        hdr = cw_headers()
        if not hdr: return ""
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=hdr, timeout=10)
        r.raise_for_status(); data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text","") or ""
        return str(data[0])
    except Exception: return ""

def fetch_taf_now(icao: str) -> str:
    try:
        hdr = cw_headers()
        if not hdr: return ""
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=hdr, timeout=10)
        r.raise_for_status(); data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text","") or ""
        return str(data[0])
    except Exception: return ""

# ---------- GPT wrapper ----------
def gpt_text(prompt_system: str, prompt_user: str, max_tokens: int = 900) -> str:
    try:
        model_name = st.secrets.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    except Exception:
        model_name = "gpt-4o-mini"
    try:
        r2 = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role":"system","content":prompt_system},
                {"role":"user","content":prompt_user},
            ],
            max_tokens=max_tokens,
            temperature=0.2
        )
        content = (r2.choices[0].message.content or "").strip()
        return ascii_safe(content) if content else ""
    except Exception as e2:
        return ascii_safe(f"Analise indisponivel (erro IA: {e2})")

# ---------- Prompts melhorados ----------
def analyze_chart_pt(kind: str, img_b64: str, filename_hint: str = "") -> str:
    sys = (
        "Es meteorologista aeronáutico sénior. Responde em PT-PT, texto corrido, em 4 blocos: "
        "1) Visão geral: padrão sinóptico, centros/isóbaras, jatos (FL/isotacas), frentes (tipo/movimento), áreas fenómenos, validade. "
        "2) Portugal continental: litoral/N/C/S — vento, visibilidade/tecto, precipitação/tipo, nebulosidade (FEW/SCT/BKN/OVC com bases/tops e equivalência em oktas), gelo (níveis, intensidade), turbulência (níveis, intensidade), cisalhamento, CB/TCU. "
        "3) Alentejo/LPSO: conselhos operacionais (altitudes/rotas recomendadas/evitadas, riscos, alternantes). "
        "4) Legenda/Simbologia: explica todos os símbolos, linhas, setas, frentes, isotacas, áreas, abreviaturas. "
        "No SPC: relaciona cada frente (fria/quente/oclusão/estacionária) com fenómenos e tipos de nuvens (SC/ST/NS/AS/AC/TCU/CB). "
        "No SIGWX: explica jatos, CB/TCU, gelo/turbulência e simbologia (linhas serrilhadas, triângulos, sombreado). "
        "Termina com impacto operacional VFR/IFR."
    )
    user_txt = f"Tipo: {kind}. Ficheiro: {filename_hint}"
    try:
        r = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role":"system","content":sys},
                {"role":"user","content":[
                    {"type":"text","text":user_txt},
                    {"type":"image_url","image_url":{"url":f"data:image/png;base64,{img_b64}"}}
                ]},
            ],
            max_tokens=1200,
            temperature=0.15
        )
        out = (r.choices[0].message.content or "").strip()
        return ascii_safe(out)
    except Exception as e:
        return ascii_safe(f"Analise indisponivel ({e})")

def analyze_metar_taf_pt(icao: str, metar: str, taf: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Em PT-PT e texto corrido, interpreta exaustivamente METAR e TAF token a token. "
        "Inclui COR/AMD, hora, vento/VRB/rajadas, CAVOK, visibilidade, fenómenos, RVR, nuvens com alturas e equivalência em oktas "
        "(FEW 1-2, SCT 3-4, BKN 5-7, OVC 8), T/Td, QNH/QFE, TREND, RMK. "
        "No TAF: validade, BECMG/TEMPO/PROB, cada linha. "
        "Conclui com impacto operacional VFR/IFR e glossário de abreviaturas."
    )
    user = f"{icao}\nMETAR:\n{metar}\nTAF:\n{taf}"
    return gpt_text(sys, user, max_tokens=2200)

def analyze_sigmet_pt(sigmet_text: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Em PT-PT, interpreta o SIGMET LPPC: fenómeno, área/limites, níveis/FL, validade, movimento. "
        "Explica abreviaturas (BTN, TOP, BASE, EMBD, OCNL, FRQ, SEV TURB, ICE, MOV xxKT) e impacto operacional VFR/IFR."
    )
    return gpt_text(sys, sigmet_text, max_tokens=1400)

# ---------- PDF helpers ----------
PASTEL = (90,127,179)

def draw_header(pdf: FPDF, text: str):
    pdf.set_font("Helvetica","B",16)
    pdf.cell(0, 12, ascii_safe(text), ln=True, align="C", border="B")

def fpdf_to_bytes(doc: FPDF) -> bytes:
    data = doc.output(dest="S")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return data.encode("latin-1")

# ---------- PDF classes ----------
class DetailedPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def chart_block(self, title, subtitle, img_png, analysis):
        self.add_page("P")
        draw_header(self, title)
        if subtitle: self.set_font("Helvetica","I",12); self.cell(0,9,ascii_safe(subtitle), ln=True, align="C")
        img = Image.open(img_png); iw, ih = img.size
        max_w = self.w-22; max_h = self.h//2
        r = min(max_w/iw, max_h/ih)
        w,h=int(iw*r),int(ih*r)
        with tempfile.NamedTemporaryFile(suffix=".png",delete=False) as tmp:
            img.save(tmp,"PNG"); path=tmp.name
        self.image(path, x=(self.w-w)//2, y=self.get_y()+6, w=w,h=h); os.remove(path); self.ln(h+10)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,analysis or "")
    def metar_taf_block(self, analyses):
        self.add_page("P"); draw_header(self,"METAR/TAF")
        for icao,metar,taf,analysis in analyses:
            self.set_font("Helvetica","B",12); self.cell(0,7,icao,ln=True)
            if metar: self.set_font("Helvetica","",11); self.multi_cell(0,6,"METAR: "+metar)
            if taf: self.multi_cell(0,6,"TAF: "+taf)
            self.multi_cell(0,6,analysis or ""); self.ln(3)
    def sigmet_block(self, sigmet_text, analysis):
        if not sigmet_text: return
        self.add_page("P"); draw_header(self,"SIGMET LPPC")
        self.multi_cell(0,6,"Texto: "+sigmet_text); self.ln(2)
        self.multi_cell(0,6,analysis or "")
    def gamet_block(self, gamet_text, analysis):
        if not gamet_text: return
        self.add_page("P"); draw_header(self,"GAMET LPPC")
        self.multi_cell(0,6,"Texto: "+gamet_text); self.ln(2)
        self.multi_cell(0,6,analysis or "")
    def glossary_block(self):
        self.add_page("P"); draw_header(self,"Glossário (Abreviaturas/Simbologia)")
        text=("FEW 1-2 oktas; SCT 3-4; BKN 5-7; OVC 8.\n"
              "CB: cumulonimbus, TCU: towering cumulus.\n"
              "SIGWX: Significant Weather Chart.\n"
              "SPC: Surface Pressure Chart.\n"
              "BECMG: Becoming, TEMPO: Temporary, PROB: Probability.\n"
              "BTN: Between, TOP/BASE: níveis superior/inferior.\n"
              "EMBD: Embedded, OCNL: Occasional, FRQ: Frequent.\n"
              "Isotacas: linhas de igual velocidade do vento.\n"
              "Isóbaras: linhas de igual pressão.\n"
              "Frentes: triângulos (fria), semicírculos (quente), ambos (oclusão).")
        self.set_font("Helvetica","",11); self.multi_cell(0,6,text)

class FinalBriefPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc):
        self.add_page("L")
        self.set_font("Helvetica","B",28); self.cell(0,14,"Briefing", ln=True, align="C")
        self.ln(4); self.set_font("Helvetica","",13)
        self.cell(0,8,ascii_safe(f"Mission: {mission_no}"), ln=True, align="C")
        self.cell(0,8,ascii_safe(f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}"), ln=True, align="C")
        self.cell(0,8,ascii_safe(f"Date: {date_str}   UTC: {time_utc}"), ln=True, align="C")
    def charts_only(self, charts: List[Tuple[str,str,io.BytesIO]]):
        for (title, subtitle, img_png) in charts:
            self.add_page("L"); draw_header(self, ascii_safe(title))
            if subtitle: 
                self.set_font("Helvetica","I",12); self.cell(0,9,ascii_safe(subtitle), ln=True, align="C")
            img = Image.open(img_png); iw, ih = img.size
            max_w = self.w-22; max_h = self.h-60
            r = min(max_w/iw, max_h/ih); w,h=int(iw*r),int(ih*r)
            with tempfile.NamedTemporaryFile(suffix=".png",delete=False) as tmp:
                img.save(tmp,"PNG"); path=tmp.name
            self.image(path, x=(self.w-w)//2, y=self.get_y()+6, w=w,h=h); os.remove(path)

# ---------- UI ----------
st.markdown('<div class="app-title">Briefings</div>', unsafe_allow_html=True)
links = st.columns(4)
with links[0]: st.page_link("pages/Weather.py", label="Open Weather 🌤️")
with links[1]: st.page_link("pages/NOTAMs.py", label="Open NOTAMs 📄")
with links[2]: st.page_link("pages/VFRMap.py", label="Open VFR Map 🗺️")
with links[3]: st.page_link("pages/MassBalance.py", label="Mass & Balance ✈️")

st.divider()

# Pilot/Aircraft info
colA, colB, colC = st.columns(3)
with colA:
    pilot = st.text_input("Pilot", "")
    callsign = st.text_input("Callsign", "")
with colB:
    aircraft_type = st.text_input("Aircraft type", "")
    registration = st.text_input("Registration", "")
with colC:
    mission_no = st.text_input("Mission number", "")
    flight_date = st.date_input("Flight date")
    time_utc = st.text_input("UTC time", "")

# ICAOs
st.markdown("### Aerodromes")
c1,c2 = st.columns(2)
with c1: icaos_metar = parse_icaos(st.text_input("ICAOs for METAR/TAF", "LPPT LPBJ"))
with c2: icaos_notam = parse_icaos(st.text_input("ICAOs for NOTAMs", "LPSO LPCB LPEV LPPC"))

# NOTAMs simplificado
st.markdown("### NOTAMs")
notams_map: Dict[str, List[str]] = {}
for icao in set(icaos_notam)|{"LPPC"}:
    notams_map[icao] = st.text_area(f"{icao} NOTAMs", "", height=120)

st.divider()

# GAMET & SIGMET
st.markdown("### GAMET")
gamet_text = st.text_area("Texto GAMET", "", height=200)
st.markdown("### SIGMET")
sigmet_text = st.text_area("Texto SIGMET", "", height=150)

# Charts
st.markdown("### Weather Charts")
use_ai_for_charts = st.toggle("Analisar charts com IA", True)
uploads = st.file_uploader("Upload charts", type=["pdf","png","jpg","jpeg","gif"], accept_multiple_files=True)

charts: List[Dict[str,Any]] = []
if uploads:
    for idx,f in enumerate(uploads):
        raw=f.read(); img_png=ensure_png_from_bytes(raw,f.type)
        kind = st.selectbox(f"Tipo {idx+1}", ["SPC","SIGWX","Wind & Temp","Other"], 0)
        title = st.text_input(f"Título {idx+1}", kind)
        subtitle = st.text_input(f"Subtítulo {idx+1}","")
        charts.append({"kind":kind,"title":title,"subtitle":subtitle,"img_png":img_png,"filename":f.name,"order":idx+1})

# Navlog & VFR (pares)
st.markdown("### Navlog & VFR (pares por rota)")
navlog_vfr_pairs=[]
pair_count=st.number_input("Nº de pares Navlog+VFR",1,5,1)
for i in range(pair_count):
    st.markdown(f"#### Par {i+1}")
    rota=st.text_input(f"Rota {i+1}","")
    nav=st.file_uploader(f"Navlog {i+1}", type=["pdf","png","jpg","jpeg","gif"])
    vfr=st.file_uploader(f"VFR Map {i+1}", type=["pdf","png","jpg","jpeg","gif"])
    navlog_vfr_pairs.append({"rota":rota,"nav":nav,"vfr":vfr})

# M&B
st.markdown("### Mass & Balance PDF")
mb_upload = st.file_uploader("Upload M&B PDF", type=["pdf"])

# ---------- Geração PDFs ----------
st.markdown("### Generate PDFs")
col=st.columns(2)
with col[0]:
    if st.button("Generate Detailed (PT)"):
        det=DetailedPDF()
        # Weather charts primeiro
        for ch in sorted(charts,key=_chart_sort_key):
            analysis=""
            if use_ai_for_charts:
                analysis=analyze_chart_pt(ch["kind"],base64.b64encode(ch["img_png"].getvalue()).decode(),"")
            det.chart_block(ch["title"],ch["subtitle"],ch["img_png"],analysis)
        # METAR/TAF
        analyses=[]
        for icao in icaos_metar:
            m, t = fetch_metar_now(icao), fetch_taf_now(icao)
            a=analyze_metar_taf_pt(icao,m,t) if (m or t) else ""
            analyses.append((icao,m,t,a))
        det.metar_taf_block(analyses)
        # SIGMET
        if sigmet_text: det.sigmet_block(sigmet_text, analyze_sigmet_pt(sigmet_text))
        # GAMET
        if gamet_text: det.gamet_block(gamet_text, gpt_text("Meteorologista","Texto:"+gamet_text,2000))
        # Glossário
        det.glossary_block()
        st.download_button("Download Detailed", data=fpdf_to_bytes(det),
                           file_name="Detailed.pdf", mime="application/pdf")

with col[1]:
    if st.button("Generate Final Briefing (EN)"):
        fb=FinalBriefPDF(); fb.cover(mission_no,pilot,aircraft_type,callsign,registration,str(flight_date),time_utc)
        # Weather charts logo a seguir à cover
        ordered=[(c["title"],c["subtitle"],c["img_png"]) for c in sorted(charts,key=_chart_sort_key)]
        fb.charts_only(ordered)
        # Navlog+VFR pares
        for pair in navlog_vfr_pairs:
            fb.add_page("L"); draw_header(fb,f"Navlog & VFR — {pair['rota']}")
            if pair["nav"]: st.info(f"Navlog {pair['rota']} embebido")
            if pair["vfr"]: st.info(f"VFR {pair['rota']} embebido")
        # M&B
        if mb_upload: st.info("M&B embebido")
        st.download_button("Download Final Briefing", data=fpdf_to_bytes(fb),
                           file_name="FinalBriefing.pdf", mime="application/pdf")

