


# app.py — Briefings com METAR/TAF, NOTAMs, GAMET, SIGMET, Charts, PDFs (Detailed vs Final)

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

# ---------- Config página ----------
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

# ---------- OpenAI ----------
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ---------- Utils ----------
def ascii_safe(text: str) -> str:
    if text is None: return ""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii","ignore").decode("ascii")
    return (t.replace("\u00A0"," ").replace("\u2009"," ").replace("\u2013","-")
             .replace("\u2014","-").replace("\uFEFF",""))

def parse_icaos(s: str) -> List[str]:
    tokens = re.split(r"[,\s]+", (s or "").strip(), flags=re.UNICODE)
    return [t.upper() for t in tokens if t]

# ---------- Imagens ----------
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
            ph = Image.new("RGB",(800,600),(245,246,248))
            bio=io.BytesIO(); ph.save(bio,format="PNG"); bio.seek(0); return bio

# ---------- Charts ----------
_KIND_RANK = {"SPC": 1, "SIGWX": 2, "Wind & Temp": 3, "Other": 9}
def _chart_sort_key(c: Dict[str, Any]): return (_KIND_RANK.get(c.get("kind","Other"),9), int(c.get("order",9999)))

# ---------- METAR/TAF ----------
def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","\n").strip()
    return {"X-API-Key": key} if key else {}

def fetch_metar_now(icao: str) -> str:
    try:
        hdr=cw_headers(); 
        if not hdr: return ""
        r=requests.get(f"https://api.checkwx.com/metar/{icao}",headers=hdr,timeout=10)
        r.raise_for_status(); data=r.json().get("data",[])
        if not data: return ""
        if isinstance(data[0],dict): return data[0].get("raw") or data[0].get("raw_text","") or ""
        return str(data[0])
    except: return ""

def fetch_taf_now(icao: str) -> str:
    try:
        hdr=cw_headers(); 
        if not hdr: return ""
        r=requests.get(f"https://api.checkwx.com/taf/{icao}",headers=hdr,timeout=10)
        r.raise_for_status(); data=r.json().get("data",[])
        if not data: return ""
        if isinstance(data[0],dict): return data[0].get("raw") or data[0].get("raw_text","") or ""
        return str(data[0])
    except: return ""

# ---------- GPT ----------
def gpt_text(prompt_system: str, prompt_user: str, max_tokens: int = 900) -> str:
    try: model_name=st.secrets.get("OPENAI_MODEL","gpt-4o-mini").strip() or "gpt-4o-mini"
    except: model_name="gpt-4o-mini"
    try:
        r2=client.chat.completions.create(
            model=model_name,
            messages=[{"role":"system","content":prompt_system},{"role":"user","content":prompt_user}],
            max_tokens=max_tokens, temperature=0.2
        )
        content=(r2.choices[0].message.content or "").strip()
        return ascii_safe(content)
    except Exception as e: return ascii_safe(f"Erro IA: {e}")

# ---------- Prompts ----------
def analyze_metar_taf_pt(icao: str, metar: str, taf: str) -> str:
    sys=("És meteorologista aeronáutico sénior. Em PT-PT, texto conciso e prático. "
         "Para cada METAR/TAF recebido: "
         "- Resume condições atuais (vento sfc, vis, fenómenos, teto, QNH). "
         "- Explica cobertura de nuvens FEW/SCT/BKN/OVC em oktas. "
         "- Destaca riscos operacionais (TS/CB, gelo, turbulência, wind shear, **SQ squall lines**). "
         "- Para o TAF: explica apenas as mudanças relevantes (BECMG/TEMPO/PROB) com impacto VFR/IFR. "
         "- Conclui em 2-3 frases: 'VFR ok', 'Cautela', 'Provável IFR', e alternante sugerido se aplicável.")
    user=f"Aeródromo {icao}\nMETAR raw: {metar}\nTAF raw: {taf}"
    return gpt_text(sys,user,max_tokens=800)

def analyze_sigmet_pt(txt: str) -> str:
    if not txt.strip(): return ""
    sys=("És meteorologista aeronáutico sénior. Em PT-PT, interpreta o SIGMET: fenómeno, área, níveis, movimento, intensidade e impacto VFR/IFR.")
    return gpt_text(sys,txt,max_tokens=600)

def analyze_gamet_pt(txt: str) -> str:
    if not txt.strip(): return ""
    sys=("És meteorologista aeronáutico sénior. Explica o GAMET LPPC de forma objetiva: fenómenos, níveis, áreas, validades. "
         "Indica claramente no fim: 'Abrange LPSO', 'Não abrange LPSO' ou 'Indeterminado'.")
    return gpt_text(sys,txt,max_tokens=900)

def analyze_chart_pt(kind: str, img_b64: str, fname="") -> str:
    sys=("És meteorologista aeronáutico sénior. Em PT-PT, analisa o chart visível (sem inventar). "
         "Explica simbologia (frentes, isóbaras, isotacas, barbules, áreas). "
         "Para SPC: descreve nuvens e fenómenos associados a cada frente. "
         "Para SIGWX: explica jatos, CB/TCU (TOP/BASE), turbulência/gelo. "
         "Identifica sempre squall lines (SQ), o mecanismo e riscos (turbulência, shear, granizo). "
         "Conclui com impacto operacional em Portugal (rotas/altitudes VFR/IFR).")
    user=f"Chart tipo {kind}, ficheiro {fname}"
    try:
        r=client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role":"system","content":sys},
                      {"role":"user","content":[
                          {"type":"text","text":user},
                          {"type":"image_url","image_url":{"url":f"data:image/png;base64,{img_b64}"}}
                      ]}],
            max_tokens=900,temperature=0.2
        )
        return ascii_safe((r.choices[0].message.content or "").strip())
    except Exception as e: return f"Erro IA: {e}"

# ---------- PDF Helpers ----------
def draw_header(pdf:FPDF,text:str):
    pdf.set_font("Helvetica","B",16)
    pdf.cell(0,12,ascii_safe(text),ln=True,align="C"); pdf.ln(4)

def place_image_full(pdf:FPDF,img_png:io.BytesIO,max_h_pad:int=50):
    max_w=pdf.w-22; max_h=pdf.h-max_h_pad
    img=Image.open(img_png); iw,ih=img.size; r=min(max_w/iw,max_h/ih)
    w,h=int(iw*r),int(ih*r); x=(pdf.w-w)//2; y=pdf.get_y()+6
    with tempfile.NamedTemporaryFile(suffix=".png",delete=False) as tmp:
        img.save(tmp,format="PNG"); path=tmp.name
    pdf.image(path,x=x,y=y,w=w,h=h); os.remove(path); pdf.ln(h+10)

# ---------- PDFs ----------
class DetailedPDF(FPDF):
    def header(self): pass
    def footer(self): self.set_y(-15); self.set_font("Helvetica","I",8); self.cell(0,8,f"Page {self.page_no()}",align="C")
    def metar_taf_block(self,analyses:List[Tuple[str,str,str,str]]):
        self.add_page()
        draw_header(self,"METAR / TAF — Interpretação")
        self.set_font("Helvetica","",12)
        for icao,metar_raw,taf_raw,analysis in analyses:
            self.set_font("Helvetica","B",13); self.cell(0,8,icao,ln=True)
            if metar_raw: self.set_font("Helvetica","",12); self.multi_cell(0,6,f"METAR: {metar_raw}")
            if taf_raw: self.multi_cell(0,6,f"TAF: {taf_raw}")
            self.set_font("Helvetica","",12); self.multi_cell(0,6,analysis); self.ln(4)
    def sigmet_block(self,raw,analysis):
        if not raw: return
        self.add_page(); draw_header(self,"SIGMET LPPC")
        self.multi_cell(0,6,raw); self.ln(3); self.multi_cell(0,6,analysis)
    def gamet_block(self,raw,analysis):
        if not raw: return
        self.add_page(); draw_header(self,"GAMET LPPC")
        self.multi_cell(0,6,raw); self.ln(3); self.multi_cell(0,6,analysis)
    def chart_block(self,title,img_png,analysis):
        self.add_page(); draw_header(self,title)
        place_image_full(self,img_png,max_h_pad=90)
        self.multi_cell(0,6,analysis); self.ln(2)
    def glossary(self):
        self.add_page(); draw_header(self,"Glossário Operacional")
        txt=("FEW=1–2 oktas; SCT=3–4; BKN=5–7; OVC=8\n"
             "SQ = Squall line (linha de rajada) → vento súbito, TS/CB, shear, granizo\n"
             "Abreviaturas: TEMPO=temporário; BECMG=gradual; PROB=probabilidade\n"
             "SIGMET: BTN=entre; TOP/BASE=tope/base; EMBD=embebido; OCNL=ocasional; FRQ=frequente")
        self.multi_cell(0,6,txt)

class FinalBriefPDF(FPDF):
    def header(self): pass
    def footer(self): self.set_y(-15); self.set_font("Helvetica","I",8); self.cell(0,8,f"Page {self.page_no()}",align="C")
    def cover(self,mission_no,pilot,aircraft,callsign,reg,date_str,time_utc):
        self.add_page("L"); self.set_font("Helvetica","B",28); self.cell(0,16,"Final Briefing",ln=True,align="C")
        self.ln(4); self.set_font("Helvetica","",13)
        self.cell(0,8,f"Mission: {mission_no}",ln=True,align="C")
        self.cell(0,8,f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}",ln=True,align="C")
        self.cell(0,8,f"Date: {date_str}   UTC: {time_utc}",ln=True,align="C")
    def weather_section(self,charts):
        self.add_page("L"); draw_header(self,"Weather")
        self.set_text_color(0,0,255); self.set_font("Helvetica","I",11)
        self.cell(0,8,f"See live updates: {APP_WEATHER_URL}",ln=True,align="C",link=APP_WEATHER_URL)
        self.set_text_color(0,0,0); self.ln(6)
        for (title,img_png) in charts:
            draw_header(self,title); place_image_full(self,img_png)
    def flightplan_section(self,img_png=None,pdf_bytes=None):
        self.add_page(); draw_header(self,"Flight Plan")
        self.set_text_color(0,0,255); self.set_font("Helvetica","I",11)
        self.cell(0,8,f"Check NOTAMs: {APP_NOTAMS_URL}",ln=True,align="C",link=APP_NOTAMS_URL)
        self.set_text_color(0,0,0); self.ln(6)
        if img_png: place_image_full(self,img_png)
        elif pdf_bytes:
            doc=fitz.open(stream=pdf_bytes,filetype="pdf")
            for i in range(doc.page_count):
                page=doc.load_page(i); png=page.get_pixmap(dpi=300).tobytes("png")
                img=Image.open(io.BytesIO(png)).convert("RGB")
                bio=io.BytesIO(); img.save(bio,format="PNG"); bio.seek(0)
                self.add_page(); place_image_full(self,bio)
    def navlog_vfr_pair(self,title,nav_png,vfr_png):
        self.add_page(); draw_header(self,f"Navlog & VFR — {title}")
        if nav_png: place_image_full(self,nav_png)
        if vfr_png: place_image_full(self,vfr_png)
    def mb_pdf(self,mb_bytes):
        doc=fitz.open(stream=mb_bytes,filetype="pdf")
        for i in range(doc.page_count):
            page=doc.load_page(i); png=page.get_pixmap(dpi=300).tobytes("png")
            img=Image.open(io.BytesIO(png)).convert("RGB")
            bio=io.BytesIO(); img.save(bio,format="PNG"); bio.seek(0)
            self.add_page(); place_image_full(self,bio)

# ---------- UI ----------
st.markdown('<div class="app-title">Briefings</div>', unsafe_allow_html=True)

# Inputs piloto/missão
colA,colB,colC=st.columns(3)
with colA: pilot=st.text_input("Pilot","Alexandre"); callsign=st.text_input("Callsign","")
with colB: aircraft=st.text_input("Aircraft","Tecnam P2008"); reg=st.text_input("Registration","CS-XXX")
with colC: mission_no=st.text_input("Mission",""); flight_date=st.date_input("Date"); time_utc=st.text_input("UTC","")

# ICAOs
st.markdown("#### Aerodromes")
c1,c2=st.columns(2)
with c1: icaos_metar=parse_icaos(st.text_input("For METAR/TAF","LPPT LPBJ LEBZ"))
with c2: icaos_notam=parse_icaos(st.text_input("For NOTAMs","LPSO LPCB LPEV LPPC"))

# Upload charts
st.markdown("#### Charts")
uploads=st.file_uploader("Upload charts",type=["pdf","png","jpg","jpeg","gif"],accept_multiple_files=True)
charts=[]
if uploads:
    for idx,f in enumerate(uploads):
        raw=f.read(); img_png=ensure_png_from_bytes(raw,f.type or ""); name=f.name
        kind=st.selectbox(f"Tipo chart {idx+1}",["SPC","SIGWX","Wind & Temp","Other"],key=f"k{idx}")
        charts.append({"kind":kind,"title":name,"img_png":img_png,"filename":name,"order":idx+1})

# Navlog-VFR pairs
st.markdown("#### Navlog ↔ VFR Pairs")
pairs=[]; n_pairs=st.number_input("Número de pares",1,5,1)
for i in range(int(n_pairs)):
    st.subheader(f"Par {i+1}")
    title=st.text_input(f"Título {i+1}",f"Par {i+1}")
    nav=st.file_uploader(f"Navlog {i+1}",type=["pdf","png","jpg","jpeg","gif"])
    vfr=st.file_uploader(f"VFR {i+1}",type=["pdf","png","jpg","jpeg","gif"])
    nav_png=vfr_png=None
    if nav: nav_png=ensure_png_from_bytes(nav.read(),nav.type)
    if vfr: vfr_png=ensure_png_from_bytes(vfr.read(),vfr.type)
    pairs.append((title,nav_png,vfr_png))

# Flight Plan
st.markdown("#### Flight Plan")
fp=st.file_uploader("Flight plan",type=["pdf","png","jpg","jpeg","gif"])
fp_img=None; fp_pdf=None
if fp:
    if fp.type=="application/pdf": fp_pdf=fp.read()
    else: fp_img=ensure_png_from_bytes(fp.read(),fp.type)

# M&B
mb=st.file_uploader("Mass & Balance / Perf PDF",type=["pdf"]); mb_bytes=mb.read() if mb else None

# Buttons
colPdfs=st.columns(2)
with colPdfs[0]:
    if st.button("Generate Detailed (PT)"):
        # Analyses
        metar_analyses=[]
        for icao in icaos_metar:
            metar=fetch_metar_now(icao); taf=fetch_taf_now(icao)
            analysis=analyze_metar_taf_pt(icao,metar,taf) if (metar or taf) else "Sem dados"
            metar_analyses.append((icao,metar,taf,analysis))
        sigmet_raw=""; sigmet_analysis="" # aqui adaptarias ao gist
        gamet_raw=""; gamet_analysis=""
        pdf=DetailedPDF()
        pdf.metar_taf_block(metar_analyses)
        pdf.sigmet_block(sigmet_raw,sigmet_analysis)
        pdf.gamet_block(gamet_raw,gamet_analysis)
        for ch in sorted(charts,key=_chart_sort_key):
            analysis=analyze_chart_pt(ch["kind"],base64.b64encode(ch["img_png"].getvalue()).decode("utf-8"),ch["filename"])
            pdf.chart_block(ch["title"],ch["img_png"],analysis)
        pdf.glossary()
        out=pdf.output(dest="S").encode("latin-1")
        st.download_button("Download Detailed (PT)",out,file_name=f"Detailed_{mission_no}.pdf")

with colPdfs[1]:
    if st.button("Generate Final Briefing (EN)"):
        fb=FinalBriefPDF()
        fb.cover(mission_no,pilot,aircraft,callsign,reg,str(flight_date),time_utc)
        ordered=[(c["title"],c["img_png"]) for c in sorted(charts,key=_chart_sort_key)]
        fb.weather_section(ordered)
        fb.flightplan_section(fp_img,fp_pdf)
        for title,nav_png,vfr_png in pairs: fb.navlog_vfr_pair(title,nav_png,vfr_png)
        if mb_bytes: fb.mb_pdf(mb_bytes)
        out=fb.output(dest="S").encode("latin-1")
        st.download_button("Download Final Briefing (EN)",out,file_name=f"Final_{mission_no}.pdf")
