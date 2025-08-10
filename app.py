# app.py
# Flight Briefing — Professional layout
# RAW (EN): charts + single live link (METAR/TAF/SIGMET) + optional raw GAMET
# DETAILED (PT): analyzes current METAR/TAF, optional GAMET, and EACH chart:
#    1) Overview  2) Portugal  3) Alentejo  (operational focus)
# Images kept high-quality. GIF supported (1st frame). PDF & image uploads supported.
#
# Pages:
#   /Weather -> live METAR/TAF (CheckWX) + SIGMET LPPC (AWC)
#
# secrets:
#   OPENAI_API_KEY, CHECKWX_API_KEY
#   (optional) GAMET_URL -> if provided, app tries to fetch GAMET automatically

import io
import os
import base64
import tempfile
from typing import Dict, Any, List, Tuple, Optional

import streamlit as st
from PIL import Image, ImageSequence
from fpdf import FPDF
import fitz  # PyMuPDF
import requests
from openai import OpenAI

APP_WEATHER_URL = "https://briefings.streamlit.app/Weather"  # single link placed on PDF covers

# ---------- Page setup & styles ----------
st.set_page_config(page_title="Flight Briefings", layout="wide")
st.markdown("""
<style>
  :root { --muted:#6b7280; --line:#e5e7eb; }
  .app-title { font-size: 2.1rem; font-weight: 800; margin: 0 0 .25rem 0;}
  .muted { color: var(--muted); margin-bottom: .75rem;}
  .section { margin-top: 18px; }
  .card { border: 1px solid var(--line); border-radius: 16px; padding: 16px 18px; box-shadow: 0 1px 4px rgba(0,0,0,.06); background: #fff; }
  .grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 16px; }
  .grid-3 { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 16px; }
  .label { font-weight: 600; margin-bottom: 6px; }
  .btn-row button { margin-right: 8px; }
</style>
""", unsafe_allow_html=True)

client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ---------- Helpers ----------
def load_first_pdf_page(pdf_bytes: bytes, dpi: int = 250) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    return Image.open(io.BytesIO(png)).convert("RGB").copy()

def gif_first_frame(file_bytes: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(file_bytes))
    im.seek(0)
    return im.convert("RGB").copy()

def to_png_bytes(img: Image.Image) -> io.BytesIO:
    """Save full quality PNG (no optimize) to preserve sharpness for PDF."""
    out = io.BytesIO()
    img.save(out, format="PNG")  # no optimize, keep detail
    out.seek(0)
    return out

def ensure_png_bytes(uploaded_file) -> io.BytesIO:
    if uploaded_file.type == "application/pdf":
        img = load_first_pdf_page(uploaded_file.read(), dpi=300)
    elif uploaded_file.type.lower() in ("image/gif",):
        img = gif_first_frame(uploaded_file.read())
    else:
        img = Image.open(uploaded_file).convert("RGB").copy()
    return to_png_bytes(img)

def b64_png(img_bytes: io.BytesIO) -> str:
    return base64.b64encode(img_bytes.getvalue()).decode("utf-8")

# ---------- GPT-5 prompts (PT) ----------
def analyze_chart_pt(kind: str, img_b64: str) -> str:
    """
    For each chart, produce: Overview -> Portugal -> Alentejo (operational, no lists, no guessing).
    kind: "SIGWX" | "SPC" | "WindTemp" | "Other"
    """
    sys = (
        "És meteorologista aeronáutico sénior. Analisa o chart fornecido em Português, com foco operacional. "
        "Escreve 3 blocos curtos, em prosa contínua (sem listas): "
        "1) Visão geral do chart; 2) Portugal; 3) Alentejo. "
        "Usa apenas informação visível; se algo estiver ilegível diz 'ilegível'. Não inventes dados."
    )
    user = f"Tipo de chart: {kind}. Faz por favor: 1) overview; 2) em Portugal; 3) no Alentejo."
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role":"system","content":[{"type":"input_text","text":sys}]},
                {"role":"user","content":[
                    {"type":"input_text","text":user},
                    {"type":"input_image","image_data":img_b64,"mime_type":"image/png"}
                ]},
            ],
            max_output_tokens=1500,
            temperature=0.14,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível analisar o chart (erro: {e})."

def analyze_metar_taf_pt(icao: str, metar: str, taf: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Em Português, interpreta METAR e TAF de forma contínua (sem listas), "
        "explicando os códigos e implicações operacionais. Sê claro, objetivo e realista; sem adivinhações."
    )
    user = f"Aeródromo {icao}. METAR:\n{metar}\n\nTAF:\n{taf}"
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role":"system","content":[{"type":"input_text","text":sys}]},
                {"role":"user","content":[{"type":"input_text","text":user}]},
            ],
            max_output_tokens=1200,
            temperature=0.14,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível interpretar METAR/TAF (erro: {e})."

def analyze_gamet_pt(gamet_text: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Em Português, explica o GAMET num parágrafo corrido, "
        "detalhando fenómenos, níveis e impacto operacional. Usa apenas o texto fornecido."
    )
    user = gamet_text
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role":"system","content":[{"type":"input_text","text":sys}]},
                {"role":"user","content":[{"type":"input_text","text":user}]},
            ],
            max_output_tokens=1200,
            temperature=0.14,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível interpretar o GAMET (erro: {e})."

# ---------- Data sources ----------
def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

def fetch_metar_now(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        return data[0].get("raw") or data[0].get("raw_text","") if isinstance(data[0], dict) else str(data[0])
    except Exception:
        return ""

def fetch_taf_now(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        return data[0].get("raw") or data[0].get("raw_text","") if isinstance(data[0], dict) else str(data[0])
    except Exception:
        return ""

def fetch_gamet_optional() -> str:
    """
    Option 1: If secrets has GAMET_URL, fetch text from there (must return raw text).
    Option 2: If not provided or fails, return empty -> user can paste manually.
    """
    url = st.secrets.get("GAMET_URL","").strip()
    if not url:
        return ""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        text = r.text.strip()
        # keep it simple; expecting plain text
        return text
    except Exception:
        return ""

# ---------- PDFs (high quality pictures) ----------
class Brand:
    title = "Flight Briefings"
    line = (229, 231, 235)  # #e5e7eb

def draw_header_bar(pdf: FPDF, text: str):
    pdf.set_fill_color(245, 247, 250)
    pdf.set_draw_color(*Brand.line)
    pdf.set_line_width(0.3)
    pdf.set_font("Arial", "B", 18)
    pdf.cell(0, 12, text, ln=True, align="C", border="B")

def place_fullwidth_image(pdf: FPDF, png_bytes: io.BytesIO, max_h_pad: int = 58):
    """
    Place image keeping maximum sharpness. Convert to temp PNG without recompressing.
    """
    max_w = pdf.w - 22
    max_h = pdf.h - max_h_pad
    img = Image.open(png_bytes)
    iw, ih = img.size
    r = min(max_w / iw, max_h / ih)
    w, h = int(iw * r), int(ih * r)
    x = (pdf.w - w) // 2
    y = pdf.get_y() + 4
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp, format="PNG")
        path = tmp.name
    pdf.image(path, x=x, y=y, w=w, h=h)
    os.remove(path)
    pdf.ln(h + 6)

class RawPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, pilot: str, aircraft: str, callsign: str, reg: str, date_str: str, time_utc: str, mission: str):
        self.add_page(orientation="L")
        self.set_xy(0, 40)
        self.set_font("Arial","B",28); self.cell(0, 14, "Weather Briefing (RAW)", ln=True, align="C")
        self.set_font("Arial","",13)
        self.ln(2)
        self.cell(0, 8, f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}", ln=True, align="C")
        self.cell(0, 8, f"Date: {date_str}   UTC: {time_utc}", ln=True, align="C")
        self.ln(6)
        self.set_font("Arial","I",12)
        self.cell(0, 8, f"Current METAR / TAF / SIGMET: {APP_WEATHER_URL}", ln=True, align="C")
        if mission:
            self.ln(4); self.set_font("Arial","",12); self.multi_cell(0, 7, f"Remarks: {mission}", align="C")

    def gamet_raw(self, gamet_text: str):
        if not gamet_text.strip():
            return
        self.add_page(orientation="P")
        draw_header_bar(self, "GAMET (RAW)")
        self.set_font("Arial","",12)
        self.ln(4)
        self.multi_cell(0, 7, gamet_text)

    def chart(self, title: str, subtitle: str, img_png: io.BytesIO):
        self.add_page(orientation="L")
        draw_header_bar(self, title)
        if subtitle:
            self.set_font("Arial","I",12); self.cell(0, 8, subtitle, ln=True, align="C")
        place_fullwidth_image(self, img_png)

class DetailedPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, pilot: str, aircraft: str, callsign: str, reg: str, date_str: str, time_utc: str, mission: str):
        self.add_page(orientation="L")
        self.set_xy(0, 40)
        self.set_font("Arial","B",28); self.cell(0, 14, "Briefing Detalhado (PT)", ln=True, align="C")
        self.set_font("Arial","",13)
        self.ln(2)
        self.cell(0, 8, f"Piloto: {pilot}   Aeronave: {aircraft}   Callsign: {callsign}   Matrícula: {reg}", ln=True, align="C")
        self.cell(0, 8, f"Data: {date_str}   UTC: {time_utc}", ln=True, align="C")
        self.ln(6)
        self.set_font("Arial","I",12)
        self.cell(0, 8, f"METAR / TAF / SIGMET atualizados: {APP_WEATHER_URL}", ln=True, align="C")
        if mission:
            self.ln(4); self.set_font("Arial","",12); self.multi_cell(0, 7, f"Notas: {mission}", align="C")

    def metar_taf_block(self, analyses: List[Tuple[str,str]]):
        if not analyses: return
        self.add_page(orientation="P")
        draw_header_bar(self, "METAR / TAF — Interpretação (PT)")
        self.set_font("Arial","",12)
        self.ln(2)
        for icao, text in analyses:
            self.set_font("Arial","B",13); self.cell(0, 8, icao, ln=True)
            self.set_font("Arial","",12); self.multi_cell(0, 7, text)
            self.ln(2)

    def gamet_block(self, gamet_text: str, analysis_pt: str):
        if not gamet_text.strip(): return
        self.add_page(orientation="P")
        draw_header_bar(self, "GAMET — Interpretação (PT)")
        self.ln(2)
        self.set_font("Arial","B",12); self.cell(0,8,"Texto (RAW):", ln=True)
        self.set_font("Arial","",12); self.multi_cell(0,7,gamet_text)
        self.ln(4)
        self.set_font("Arial","B",12); self.cell(0,8,"Interpretação:", ln=True)
        self.set_font("Arial","",12); self.multi_cell(0,7,analysis_pt)

    def chart_block(self, title: str, subtitle: str, img_png: io.BytesIO, analysis_pt: str):
        self.add_page(orientation="L")
        draw_header_bar(self, title)
        if subtitle:
            self.set_font("Arial","I",12); self.cell(0, 8, subtitle, ln=True, align="C")
        # image upper half
        max_w = self.w - 22
        max_h = (self.h // 2) - 16
        img = Image.open(img_png)
        iw, ih = img.size
        r = min(max_w/iw, max_h/ih)
        w, h = int(iw*r), int(ih*r)
        x = (self.w - w)//2
        y = self.get_y() + 4
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h)
        os.remove(path)
        self.ln(h + 8)
        # text lower half
        self.set_font("Arial","",12)
        self.multi_cell(0, 7, analysis_pt)

# ---------- UI ----------
st.markdown('<div class="app-title">Flight Briefings</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Raw (EN) for instructor • Detailed (PT) for your prep</div>', unsafe_allow_html=True)
st.divider()

# Pilot/Aircraft block
st.markdown("#### Pilot & Aircraft")
colA, colB, colC = st.columns(3)
with colA:
    pilot = st.text_input("Pilot name", "")
    callsign = st.text_input("Mission callsign", "")
with colB:
    aircraft_type = st.text_input("Aircraft type", "Tecnam P2008")
    registration = st.text_input("Registration", "")
with colC:
    flight_date = st.date_input("Flight date")
    time_utc = st.text_input("UTC time", "")

st.markdown("#### Mission")
mission = st.text_input("Remarks", "")

# Aerodromes for METAR/TAF (and for Weather page)
st.markdown("#### Aerodromes")
default_icaos = "LPPT, LPBJ, LEBZ"
icaos_str = st.text_input("ICAO list (comma-separated)", value=default_icaos)
icaos = [x.strip().upper() for x in icaos_str.split(",") if x.strip()]

# GAMET
st.markdown("#### GAMET")
gamet_text_default = fetch_gamet_optional()
gamet_text = st.text_area("Paste GAMET here (optional — if left blank and GAMET_URL is set, the app tries to fetch it)", value=gamet_text_default, height=120)

# Charts upload
st.markdown("#### Charts")
st.caption("Upload SIGWX / Surface Pressure (SPC) / Wind & Temp. Accepts PDF/PNG/JPG/JPEG/GIF.")
uploads = st.file_uploader("Upload charts", type=["pdf","png","jpg","jpeg","gif"], accept_multiple_files=True, label_visibility="collapsed")

chart_rows: List[Dict[str,Any]] = []
if uploads:
    for idx, f in enumerate(uploads):
        img_png = ensure_png_bytes(f)
        with st.container():
            c1, c2, c3 = st.columns([0.34, 0.33, 0.33])
            with c1:
                guess = 0
                name = (f.name or "").lower()
                if "spc" in name or "press" in name: guess = 1
                elif "wind" in name or "temp" in name: guess = 2
                kind = st.selectbox(f"Chart type #{idx+1}", ["SIGWX","SPC","Wind & Temp","Other"], index=guess, key=f"kind_{idx}")
            with c2:
                title = st.text_input("Title", value=( "Significant Weather Chart (SIGWX)" if kind=="SIGWX"
                                                       else "Surface Pressure Chart (SPC)" if kind=="SPC"
                                                       else "Wind and Temperature Chart" if kind=="Wind & Temp"
                                                       else "Weather Chart"),
                                      key=f"title_{idx}")
            with c3:
                subtitle = st.text_input("Subtitle (optional)", value="", key=f"subtitle_{idx}")
        chart_rows.append({"kind": kind, "title": title, "subtitle": subtitle, "img_png": img_png})

# Generate buttons
st.markdown('<div class="section"></div>', unsafe_allow_html=True)
b1, b2 = st.columns([0.5, 0.5])
gen_both = b1.button("Generate PDFs (RAW EN + DETAILED PT)", type="primary", use_container_width=True)

# ---------- Generate PDFs ----------
if gen_both:
    date_str = str(flight_date)

    # RAW
    raw_pdf = RawPDF()
    raw_pdf.cover(pilot, aircraft_type, callsign, registration, date_str, time_utc, mission)
    # Optional: include raw GAMET for the instructor to see the exact text (no analysis)
    raw_pdf.gamet_raw(gamet_text)
    for ch in chart_rows:
        raw_pdf.chart(ch["title"], ch["subtitle"], ch["img_png"])
    raw_name = "briefing_raw.pdf"
    raw_pdf.output(raw_name)

    # DETAILED
    det_pdf = DetailedPDF()
    det_pdf.cover(pilot, aircraft_type, callsign, registration, date_str, time_utc, mission)

    # 1) METAR/TAF (current) — interpret in PT
    metar_analyses: List[Tuple[str,str]] = []
    for icao in icaos:
        metar = fetch_metar_now(icao)
        taf = fetch_taf_now(icao)
        if metar or taf:
            analysis = analyze_metar_taf_pt(icao, metar, taf)
            metar_analyses.append((icao, analysis))
    det_pdf.metar_taf_block(metar_analyses)

    # 2) GAMET — interpret if present
    if gamet_text.strip():
        gamet_pt = analyze_gamet_pt(gamet_text)
        det_pdf.gamet_block(gamet_text, gamet_pt)

    # 3) Charts — each with Overview -> Portugal -> Alentejo
    for ch in chart_rows:
        txt = analyze_chart_pt(
            kind=("SIGWX" if ch["kind"]=="SIGWX" else ("SPC" if ch["kind"]=="SPC" else ("WindTemp" if ch["kind"]=="Wind & Temp" else "Other"))),
            img_b64=b64_png(ch["img_png"])
        )
        det_pdf.chart_block(ch["title"], ch["subtitle"], ch["img_png"], txt)

    det_name = "briefing_detalhado.pdf"
    det_pdf.output(det_name)

    # Downloads
    d1, d2 = st.columns(2)
    with d1:
        with open(raw_name, "rb") as f:
            st.download_button("Download RAW (EN)", data=f.read(), file_name=raw_name, mime="application/pdf", use_container_width=True)
    with d2:
        with open(det_name, "rb") as f:
            st.download_button("Download DETAILED (PT)", data=f.read(), file_name=det_name, mime="application/pdf", use_container_width=True)

# Footer link to weather
st.divider()
st.markdown(f"**Live Weather page:** {APP_WEATHER_URL}")





