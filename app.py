import io
import os
import base64
import tempfile
from typing import Dict, Any, List, Tuple
import datetime
import re

import streamlit as st
from PIL import Image
from fpdf import FPDF
import fitz  # PyMuPDF
from bs4 import BeautifulSoup
import requests
from openai import OpenAI

# ================= Page setup (no sidebar) =================
st.set_page_config(page_title="Briefings", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
/* Hide sidebar + hamburger */
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stHamburger"] { display: none !important; }
.block-container { padding-top: 1.2rem; }
.app-title { font-size: 2rem; font-weight: 800; margin-bottom: .5rem;}
.muted { color: #6b7280; }
.grid { display:grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.card { border: 1px solid #e5e7eb; border-radius: 14px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.section-title { font-weight: 700; font-size: 1.05rem; margin: 16px 0 6px; }
.small { font-size:.9rem; color:#6b7280 }
.btn-row button { margin-right: 8px; }
hr { border: none; border-top: 1px solid #eee; margin: 1rem 0; }
</style>
""", unsafe_allow_html=True)

# ===== Keys/clients =====
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))
CHECKWX_API_KEY = st.secrets.get("CHECKWX_API_KEY", "")

# Single link for live weather:
APP_WEATHER_URL = "https://briefings.streamlit.app/Weather"

# ===== Helpers (images) =====
def first_pdf_page(uploaded_pdf_bytes: bytes) -> Image.Image:
    """Render first PDF page to PNG at 300dpi for high quality."""
    doc = fitz.open(stream=uploaded_pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    png = page.get_pixmap(dpi=300).tobytes("png")
    return Image.open(io.BytesIO(png)).convert("RGB").copy()

def image_from_file(uploaded_file) -> Image.Image:
    if uploaded_file.type == "application/pdf":
        return first_pdf_page(uploaded_file.read())
    if uploaded_file.type.lower() == "image/gif":
        im = Image.open(uploaded_file)
        im.seek(0)
        return im.convert("RGB").copy()
    return Image.open(uploaded_file).convert("RGB").copy()

def to_png_bytes(img: Image.Image) -> io.BytesIO:
    out = io.BytesIO()
    img.save(out, format="PNG")  # lossless, no optimize for max fidelity
    out.seek(0)
    return out

def b64_from_bytesio(img_bytes: io.BytesIO) -> str:
    return base64.b64encode(img_bytes.getvalue()).decode("utf-8")

# ===== METAR/TAF (current for Detailed PDF) =====
def _cw_headers() -> Dict[str, str]:
    return {"X-API-Key": CHECKWX_API_KEY} if CHECKWX_API_KEY else {}

def fetch_metar_raw(icao: str) -> str:
    if not CHECKWX_API_KEY:
        return ""
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=_cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text") or ""
        return str(data[0])
    except Exception:
        return ""

def fetch_taf_raw(icao: str) -> str:
    if not CHECKWX_API_KEY:
        return ""
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=_cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text") or ""
        return str(data[0])
    except Exception:
        return ""

# ===== SIGMET/GAMET for Detailed (same parser as Weather) =====
def _ipma_headers_from_secrets() -> Dict[str, str]:
    h: Dict[str, str] = {}
    bearer = st.secrets.get("IPMA_BEARER", "")
    cookie = st.secrets.get("IPMA_COOKIE", "")
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    if cookie:
        h["Cookie"] = cookie
    h["User-Agent"] = "Mozilla/5.0 (compatible; BriefingsApp/1.0)"
    return h

def fetch_sigmet_gamet_from_ipma_page() -> Dict[str, List[str]]:
    url = st.secrets.get("IPMA_SHOWSIGMET_URL", "")
    if not url:
        return {"sigmet": [], "gamet": []}
    try:
        r = requests.get(url, headers=_ipma_headers_from_secrets(), timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one("#divContent")
        if not content:
            return {"sigmet": [], "gamet": []}
        for br in content.find_all("br"):
            br.replace_with("\n")
        text = content.get_text("\n")
        text = re.sub(r"[ \t]+\n", "\n", text).strip()

        gamet_blocks: List[str] = [m.group(0).strip() for m in re.finditer(r"(?ms)^LPPC\s+GAMET.*?(?:\n\n|$)", text)]
        if not gamet_blocks:
            sec = re.search(r"(?ms)^LPPC\s*\(.*?\)\s*\n(.*?)(?:\n\n|$)", text)
            if sec and "GAMET" in sec.group(0):
                gamet_blocks = [sec.group(0).strip()]

        sigmet_blocks: List[str] = []
        for m in re.finditer(r"(?ms)^(?:LPPC\s+)?SIGMET.*?(?:\n\n|$)", text):
            blk = m.group(0).strip()
            if "LPPC" in blk:
                sigmet_blocks.append(blk)

        return {"sigmet": sigmet_blocks, "gamet": gamet_blocks}
    except Exception:
        return {"sigmet": [], "gamet": []}

# ===== AI explainers (Portuguese) =====
def explain_chart_pt(kind: str, img_b64: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Explica o gráfico em português, em texto corrido, sem listas. "
        "Usa apenas o que é visível; se algo estiver ilegível, diz que está ilegível e não inventes."
    )
    user = (
        f"Tipo de gráfico: {kind}.\n"
        "Produz três parágrafos curtos:\n"
        "1) Visão geral do gráfico.\n"
        "2) Situação sobre Portugal.\n"
        "3) Foco no Alentejo (implicações aeronáuticas: vento, nuvens, gelo, turbulência, convecção, níveis/validades).\n"
    )
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": sys}]},
                {"role": "user", "content": [
                    {"type": "input_text", "text": user},
                    {"type": "input_image", "image_data": img_b64, "mime_type": "image/png"}
                ]}
            ],
            max_output_tokens=1500,
            temperature=0.14,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível gerar a análise (erro: {e})."

def explain_metar_taf_pt(icao: str, metar_raw: str, taf_raw: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Explica METAR e TAF em português, em texto corrido, "
        "clarificando códigos e salientando implicações operacionais. Não inventes."
    )
    user = f"Aeródromo: {icao}\nMETAR: {metar_raw or 'N/A'}\nTAF: {taf_raw or 'N/A'}\nEscreve 1-2 parágrafos."
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": sys}]},
                {"role": "user", "content": [{"type": "input_text", "text": user}]}
            ],
            max_output_tokens=900,
            temperature=0.15,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível analisar METAR/TAF (erro: {e})."

def explain_gamet_pt(texto: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Resume e interpreta um GAMET em português, "
        "focando fenómenos, níveis, validades e impacto operacional. Texto corrido, sem listas."
    )
    user = f"GAMET (texto):\n{texto}"
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": sys}]},
                {"role": "user", "content": [{"type": "input_text", "text": user}]}
            ],
            max_output_tokens=900,
            temperature=0.15,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível analisar o GAMET (erro: {e})."

# ===== PDF classes (high quality) =====
class RawPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, pilot, aircraft, callsign, reg, date_str, time_utc, remarks, weather_link):
        self.add_page(orientation="L")
        self.set_xy(0, 45)
        self.set_font("Arial", "B", 30); self.cell(0, 16, "Weather Briefing (RAW)", ln=True, align="C")
        self.ln(4); self.set_font("Arial", "", 13)
        self.cell(0, 8, f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}", ln=True, align="C")
        self.cell(0, 8, f"Date: {date_str}   UTC: {time_utc}   Remarks: {remarks}", ln=True, align="C")
        self.ln(4); self.set_font("Arial", "I", 12)
        self.cell(0, 8, f"Current METAR/TAF/SIGMET/GAMET: {weather_link}", ln=True, align="C")
        self.ln(6)
    def chart_fullpage(self, title: str, subtitle: str, img_bytes: io.BytesIO):
        self.add_page(orientation="L")
        self.set_font("Arial", "B", 18); self.cell(0, 10, title, ln=True, align="C")
        if subtitle:
            self.set_font("Arial", "I", 12); self.cell(0, 8, subtitle, ln=True, align="C")
        max_w = self.w - 28; max_h = self.h - 42  # small margins → bigger image
        img = Image.open(img_bytes)
        iw, ih = img.size
        r = min(max_w / iw, max_h / ih)
        w, h = int(iw * r), int(ih * r)
        x = (self.w - w) // 2
        y = self.get_y() + 2
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG"); path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h); os.remove(path)

class DetailedPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, pilot, aircraft, callsign, reg, date_str, time_utc, remarks, weather_link):
        self.add_page(orientation="L")
        self.set_xy(0, 45)
        self.set_font("Arial", "B", 30); self.cell(0, 16, "Briefing Detalhado (PT)", ln=True, align="C")
        self.ln(4); self.set_font("Arial", "", 13)
        self.cell(0, 8, f"Piloto: {pilot}   Aeronave: {aircraft}   Callsign: {callsign}   Matr.: {reg}", ln=True, align="C")
        self.cell(0, 8, f"Data: {date_str}   UTC: {time_utc}   Missão: {remarks}", ln=True, align="C")
        self.ln(4); self.set_font("Arial", "I", 12)
        self.cell(0, 8, f"Produtos atuais (METAR/TAF/SIGMET/GAMET): {weather_link}", ln=True, align="C")
        self.ln(6)
    def metar_taf_analysis(self, blocks: List[Dict[str, str]]):
        if not blocks:
            return
        self.add_page(orientation="P")
        self.set_font("Arial", "B", 18); self.cell(0, 12, "METAR/TAF (Análise)", ln=True, align="C")
        self.set_font("Arial", "", 12)
        for b in blocks:
            self.set_font("Arial", "B", 13); self.cell(0, 7, b["icao"], ln=True)
            self.set_font("Arial", "", 11); self.multi_cell(0, 6, b.get("explain_pt", ""))
            self.ln(2)
    def gamet_section(self, texts: List[str], explains: List[str]):
        if not texts:
            return
        self.add_page(orientation="P")
        self.set_font("Arial", "B", 18); self.cell(0, 12, "GAMET (LPPC)", ln=True, align="C")
        for raw, exp in zip(texts, explains):
            self.set_font("Arial", "I", 10); self.multi_cell(0, 6, raw); self.ln(2)
            self.set_font("Arial", "", 11); self.multi_cell(0, 6, exp); self.ln(4)
    def chart_explained(self, title: str, subtitle: str, img_bytes: io.BytesIO, analysis_pt: str):
        self.add_page(orientation="L")
        self.set_font("Arial", "B", 18); self.cell(0, 10, title, ln=True, align="C")
        if subtitle:
            self.set_font("Arial", "I", 12); self.cell(0, 8, subtitle, ln=True, align="C")
        max_w = self.w - 28; max_h = (self.h // 2) - 14
        img = Image.open(img_bytes)
        iw, ih = img.size
        r = min(max_w / iw, max_h / ih)
        w, h = int(iw * r), int(ih * r)
        x = (self.w - w) // 2
        y = self.get_y() + 2
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG"); path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h); os.remove(path)
        self.ln(h + 6); self.set_font("Arial", "", 12); self.multi_cell(0, 7, analysis_pt)

# ===== UI =====
st.markdown('<div class="app-title">Flight Briefings</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Raw (EN) for instructor • Detailed (PT) for prep</div>', unsafe_allow_html=True)
st.divider()

# Cover fields
colA, colB = st.columns([0.55, 0.45])
with colA:
    pilot = st.text_input("Pilot name", "")
    aircraft = st.text_input("Aircraft type", "Tecnam P2008")
    callsign = st.text_input("Mission callsign", "")
    reg = st.text_input("Aircraft registration", "")
with colB:
    date_str = st.date_input("Flight date", datetime.date.today()).strftime("%Y-%m-%d")
    time_utc = st.text_input("Flight time (UTC)", "")
    remarks = st.text_input("Mission remarks", "")
    weather_link = APP_WEATHER_URL

# Charts upload
st.markdown('<div class="section-title">Charts</div>', unsafe_allow_html=True)
st.caption("Upload SIGWX / Wind & Temp / Surface Pressure charts (PDF, PNG, JPG, JPEG, GIF). Choose the type for best analysis.")
uploads = st.file_uploader("Upload charts", type=["pdf", "png", "jpg", "jpeg", "gif"], accept_multiple_files=True, label_visibility="collapsed")

# Build chart list with per-item controls
chart_items: List[Dict[str, Any]] = []
if uploads:
    for idx, f in enumerate(uploads):
        try:
            img = image_from_file(f)
            img_bytes = to_png_bytes(img)
            base = (f.name or "").lower()
            guess = 0
            if "spc" in base or "press" in base:
                guess = 1
            elif "wind" in base or "temp" in base:
                guess = 2
            with st.container():
                col1, col2, col3 = st.columns([0.35, 0.4, 0.25])
                with col1:
                    kind = st.selectbox(f"Chart type (#{idx+1})", ["SIGWX", "Surface Pressure (SPC)", "Wind & Temp", "Other"], index=guess, key=f"k{idx}")
                with col2:
                    title = st.text_input("Title", value=kind if kind != "Other" else "Weather Chart", key=f"t{idx}")
                with col3:
                    subtitle = st.text_input("Subtitle (optional)", value="", key=f"s{idx}")
            chart_items.append({"kind": kind, "title": title, "subtitle": subtitle, "img_bytes": img_bytes})
        except Exception as e:
            st.error(f"Failed to read {f.name}: {e}")

# Aerodromes for METAR/TAF analysis (Detailed PDF)
st.markdown('<div class="section-title">Aerodromes for METAR/TAF analysis (Detailed PDF)</div>', unsafe_allow_html=True)
icao_text = st.text_input("ICAO list (comma-separated)", value="LPPT, LPBJ, LEBZ")
icaos = [i.strip().upper() for i in icao_text.split(",") if i.strip()]

# Generate buttons
st.markdown('<div class="btn-row">', unsafe_allow_html=True)
gen_btn = st.button("Generate RAW (EN) + DETAILED (PT)", type="primary")
st.markdown('</div>', unsafe_allow_html=True)

# ===== Generate =====
if gen_btn:
    # RAW
    raw_pdf = RawPDF()
    raw_pdf.cover(pilot, aircraft, callsign, reg, date_str, time_utc, remarks, weather_link)
    for ch in chart_items:
        raw_pdf.chart_fullpage(ch["title"], ch.get("subtitle", ""), ch["img_bytes"])
    raw_name = "briefing_raw.pdf"
    raw_pdf.output(raw_name)

    # DETAILED
    det_pdf = DetailedPDF()
    det_pdf.cover(pilot, aircraft, callsign, reg, date_str, time_utc, remarks, weather_link)

    # METAR/TAF analysis (current)
    met_blocks: List[Dict[str, str]] = []
    for icao in icaos:
        m = fetch_metar_raw(icao)
        t = fetch_taf_raw(icao)
        exp = explain_metar_taf_pt(icao, m, t)
        met_blocks.append({"icao": icao, "explain_pt": exp})
    det_pdf.metar_taf_analysis(met_blocks)

    # GAMET (from IPMA showSIGMET page) + analysis if available
    gt = fetch_sigmet_gamet_from_ipma_page().get("gamet", [])
    if gt:
        exps = [explain_gamet_pt(g) for g in gt]
        det_pdf.gamet_section(gt, exps)

    # Per-chart analysis (General → Portugal → Alentejo)
    for ch in chart_items:
        img_b64 = b64_from_bytesio(ch["img_bytes"])
        analysis = explain_chart_pt(ch["kind"], img_b64)
        det_pdf.chart_explained(ch["title"], ch.get("subtitle", ""), ch["img_bytes"], analysis)

    det_name = "briefing_detalhado.pdf"
    det_pdf.output(det_name)

    c1, c2 = st.columns(2)
    with c1:
        with open(raw_name, "rb") as f:
            st.download_button("Download RAW (EN)", f.read(), file_name=raw_name, mime="application/pdf", use_container_width=True)
    with c2:
        with open(det_name, "rb") as f:
            st.download_button("Download DETAILED (PT)", f.read(), file_name=det_name, mime="application/pdf", use_container_width=True)






