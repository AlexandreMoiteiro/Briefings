~# app.py
# Flight Briefings — RAW (EN) + DETAILED (PT)
# - High-quality charts in PDFs
# - Cover with full flight details + single live link to /Weather
# - Detailed (PT): METAR/TAF analysis (current) + per-chart analysis (General → Portugal → Alentejo)
# - Raw (EN): charts + optional GAMET raw + single live link
# - No sidebar; English UI
# Requires: OPENAI_API_KEY, CHECKWX_API_KEY in .streamlit/secrets.toml

import io
import os
import base64
import tempfile
from typing import Dict, Any, List, Tuple

import streamlit as st
from PIL import Image
from fpdf import FPDF
import fitz  # PyMuPDF
import requests
from openai import OpenAI

APP_WEATHER_URL = "https://briefings.streamlit.app/Weather"  # single live link used in PDFs

# ---------------- UI base ----------------
st.set_page_config(page_title="Flight Briefings", layout="wide")
st.markdown(
    """
    <style>
      .app-title { font-size: 2.2rem; font-weight: 800; margin-bottom: .25rem;}
      .muted { color: #6b7280; margin-bottom: 1rem;}
      .section { margin-top: 12px; }
      .card { border: 1px solid #e5e7eb; border-radius: 14px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); background: #fff; }
      .grid2 { display:grid; grid-template-columns: 1fr 1fr; gap: 16px; }
      .grid3 { display:grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
      .btn-row button { margin-right: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ---------------- Helpers ----------------
def read_pdf_first_page(pdf_bytes: bytes, dpi: int = 300) -> Image.Image:
    """Render first page of PDF to high-res PNG."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=dpi)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()

def read_gif_first_frame(file_bytes: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(file_bytes))
    im.seek(0)
    return im.convert("RGB").copy()

def to_png_bytes(img: Image.Image) -> io.BytesIO:
    """Save image as PNG (no extra optimize to preserve quality)."""
    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out

def chart_block_uploader() -> List[Dict[str, Any]]:
    st.markdown("#### Charts")
    st.caption("Upload SIGWX / Surface Pressure (SPC) / Wind & Temp charts (PDF, PNG, JPG, JPEG, GIF).")
    files = st.file_uploader(
        "Upload charts",
        type=["pdf","png","jpg","jpeg","gif"],
        accept_multiple_files=True,
        label_visibility="collapsed"
    )
    items: List[Dict[str, Any]] = []
    if not files:
        return items
    for idx, f in enumerate(files):
        try:
            if f.type == "application/pdf":
                img = read_pdf_first_page(f.read(), dpi=300)   # higher DPI for clarity
            elif f.type.lower() == "image/gif":
                img = read_gif_first_frame(f.read())
            else:
                img = Image.open(f).convert("RGB").copy()

            img_bytes = to_png_bytes(img)

            with st.container():
                c1, c2, c3 = st.columns([0.32,0.38,0.30])
                base = (f.name or "").lower()
                # guess type
                guess = "SIGWX"
                if "spc" in base or "press" in base or "pressure" in base:
                    guess = "Surface Pressure (SPC)"
                elif "wind" in base or "temp" in base:
                    guess = "Wind & Temp"
                kind = c1.selectbox(
                    f"Chart type (#{idx+1})",
                    ["SIGWX","Surface Pressure (SPC)","Wind & Temp","Other"],
                    index=["SIGWX","Surface Pressure (SPC)","Wind & Temp","Other"].index(guess),
                    key=f"kind_{idx}"
                )
                title = c2.text_input("Title", value=kind if kind!="Other" else "Weather Chart", key=f"title_{idx}")
                subtitle = c3.text_input("Subtitle (optional)", value="", key=f"subtitle_{idx}")
            items.append({"kind": kind, "title": title, "subtitle": subtitle, "img_bytes": img_bytes})
        except Exception as e:
            st.error(f"Failed to read {f.name}: {e}")
    return items

def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

@st.cache_data(ttl=90)
def fetch_metar(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=90)
def fetch_taf(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text") or ""
        return str(data[0])
    except Exception:
        return ""

# ---------------- GPT-5 Analysis (Portuguese) ----------------
def ai_meteor_pt(text: str, msg_type: str, icao: str) -> str:
    """Analyze METAR or TAF in Portuguese (operational, no lists)."""
    sys = (
        "És meteorologista aeronáutico sénior. Interpreta a mensagem em português, explicando a semântica dos códigos "
        "e implicações operacionais para o voo, em texto corrido, sem listas. Se algo não estiver presente, não inventes."
    )
    user = f"Tipo: {msg_type}. Aeródromo: {icao}. Texto:\n{text}"
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role":"system","content":[{"type":"input_text","text":sys}]},
                {"role":"user","content":[{"type":"input_text","text":user}]}
            ],
            max_output_tokens=900,
            temperature=0.15,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível analisar {msg_type} agora (erro: {e})."

def ai_chart_pt(kind: str, img_b64: str) -> str:
    """Per-chart analysis in PT: General → Portugal → Alentejo, strictly from visible info."""
    sys = (
        "És meteorologista aeronáutico sénior. Analisa o chart em português e em texto corrido (sem listas), "
        "usando SÓ o que está visível. Estrutura obrigatória: 1) Visão Geral; 2) Portugal; 3) Foco no Alentejo. "
        "Assinala explicitamente se algo estiver ilegível. Mantém foco operacional (turbulência, gelo, frentes, jatos, FL, validade)."
    )
    user = f"Tipo de chart: {kind}. Produz relato com os 3 blocos na ordem pedida."
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role":"system","content":[{"type":"input_text","text":sys}]},
                {"role":"user","content":[
                    {"type":"input_text","text":user},
                    {"type":"input_image","image_data":img_b64,"mime_type":"image/png"}
                ]}
            ],
            max_output_tokens=1400,
            temperature=0.14,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível analisar o chart agora (erro: {e})."

# ---------------- PDF Classes (high-quality image placement) ----------------
class CoverMixin:
    def cover(self, title: str, pilot: str, ac_type: str, callsign: str, reg: str, date_str: str, time_utc: str, remarks: str, live_url: str):
        self.add_page(orientation="L")
        self.set_xy(0, 42)
        self.set_font("Helvetica", "B", 28)
        self.cell(0, 16, title, ln=True, align="C")
        self.ln(2)
        self.set_font("Helvetica", "", 13)
        self.cell(0, 8, f"Pilot: {pilot}    Aircraft: {ac_type}    Callsign: {callsign}    Registration: {reg}", ln=True, align="C")
        self.cell(0, 8, f"Date: {date_str}    Time (UTC): {time_utc}", ln=True, align="C")
        if remarks:
            self.ln(2)
            self.set_font("Helvetica", "I", 12)
            self.cell(0, 7, f"Remarks: {remarks}", ln=True, align="C")
        self.ln(10)
        self.set_font("Helvetica", "", 12)
        self.cell(0, 8, f"Current METAR/TAF/SIGMET: {live_url}", ln=True, align="C")  # single link

class RawPDF(FPDF, CoverMixin):
    def header(self): pass
    def footer(self): pass
    def add_chart(self, title: str, subtitle: str, img_bytes: io.BytesIO):
        self.add_page(orientation="L")
        self.set_font("Helvetica","B",16)
        self.cell(0,10,title, ln=True, align="C")
        if subtitle:
            self.set_font("Helvetica","I",12)
            self.cell(0,8,subtitle, ln=True, align="C")
        self.ln(1)
        # High-quality fit into page
        max_w = self.w - 26
        max_h = self.h - 40
        img = Image.open(img_bytes)
        iw, ih = img.size
        r = min(max_w/iw, max_h/ih)
        w, h = int(iw*r), int(ih*r)
        x = (self.w - w)//2
        y = self.get_y() + 2
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h)
        os.remove(path)

class DetailedPDF(FPDF, CoverMixin):
    def header(self): pass
    def footer(self): pass
    def add_metar_taf_analysis(self, blocks: List[Tuple[str, str, str]]):
        """blocks: list of (ICAO, METAR, TAF) with AI analysis added."""
        self.add_page(orientation="P")
        self.set_font("Helvetica","B",18)
        self.cell(0,12,"METAR / TAF (Análise)", ln=True, align="C")
        self.set_font("Helvetica","",12)
        for icao, metar, taf in blocks:
            self.set_font("Helvetica","B",13)
            self.cell(0,8,f"{icao}", ln=True)
            if metar:
                self.set_font("Helvetica","",12)
                self.cell(0,6,"METAR (raw):", ln=True)
                self.multi_cell(0,6,metar)
                self.set_font("Helvetica","I",11)
                self.multi_cell(0,6, ai_meteor_pt(metar, "METAR", icao))
            if taf:
                self.set_font("Helvetica","",12)
                self.cell(0,6,"TAF (raw):", ln=True)
                self.multi_cell(0,6,taf)
                self.set_font("Helvetica","I",11)
                self.multi_cell(0,6, ai_meteor_pt(taf, "TAF", icao))
            self.ln(2)

    def add_chart_analysis(self, title: str, subtitle: str, img_bytes: io.BytesIO, kind: str):
        self.add_page(orientation="L")
        self.set_font("Helvetica","B",16)
        self.cell(0,10,title, ln=True, align="C")
        if subtitle:
            self.set_font("Helvetica","I",12)
            self.cell(0,8,subtitle, ln=True, align="C")
        self.ln(2)
        # Place image top half, high quality
        max_w = self.w - 26
        max_h = (self.h // 2) - 14
        img = Image.open(img_bytes)
        iw, ih = img.size
        r = min(max_w/iw, max_h/ih)
        w, h = int(iw*r), int(ih*r)
        x = (self.w - w)//2
        y = self.get_y() + 2
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h)
        os.remove(path)
        # Analysis text under the image
        self.ln(h + 6)
        self.set_font("Helvetica","",12)
        img_b64 = base64.b64encode(img_bytes.getvalue()).decode("utf-8")
        analysis = ai_chart_pt(kind, img_b64)
        self.multi_cell(0,7, analysis)

# ---------------- UI ----------------
st.markdown('<div class="app-title">Flight Briefings</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">RAW (EN) for instructor • DETAILED (PT) for prep</div>', unsafe_allow_html=True)
st.divider()

# Flight details (for cover)
st.markdown("#### Flight Details")
cA, cB, cC = st.columns(3)
with cA:
    pilot_name = st.text_input("Pilot Name", "")
    callsign = st.text_input("Mission Callsign", "")
with cB:
    aircraft_type = st.text_input("Aircraft Type", "Tecnam P2008")
    registration = st.text_input("Aircraft Registration", "")
with cC:
    date_str = st.text_input("Flight Date (YYYY-MM-DD)", "")
    time_utc = st.text_input("Time (UTC)", "")

remarks = st.text_input("Mission Remarks", "")

# Aerodromes for METAR/TAF (used for DETAILED analysis and Weather page)
st.markdown("#### Aerodromes (for live page and METAR/TAF analysis)")
icaos_text = st.text_input("ICAOs (comma-separated)", "LPPT, LPBJ, LEBZ")
icaos = [i.strip().upper() for i in icaos_text.split(",") if i.strip()]

# GAMET/SIGMET raw (optional to include in RAW)
st.markdown("#### Optional: Paste GAMET/SIGMET/AIRMET (raw) to include in RAW")
gamet_raw = st.text_area("Paste text here (optional):", value="", height=120)

# Charts
chart_items = chart_block_uploader()

st.markdown('<div class="btn-row">', unsafe_allow_html=True)
generate = st.button("Generate RAW (EN) + DETAILED (PT)", type="primary")
st.markdown('</div>', unsafe_allow_html=True)

# ---------------- Generate PDFs ----------------
if generate:
    # Prepare METAR/TAF blocks for detailed
    metar_taf_blocks: List[Tuple[str,str,str]] = []
    for icao in icaos:
        if len(icao) == 4:
            metar = fetch_metar(icao)
            taf = fetch_taf(icao)
            if metar or taf:
                metar_taf_blocks.append((icao, metar, taf))

    # RAW PDF
    raw_pdf = RawPDF()
    raw_pdf.cover(
        title="Weather Briefing (RAW)",
        pilot=pilot_name, ac_type=aircraft_type or "Tecnam P2008",
        callsign=callsign, reg=registration, date_str=date_str, time_utc=time_utc,
        remarks=remarks, live_url=APP_WEATHER_URL
    )
    # Optional GAMET/SIGMET/AIRMET raw page
    if gamet_raw.strip():
        raw_pdf.add_page(orientation="P")
        raw_pdf.set_font("Helvetica","B",16)
        raw_pdf.cell(0,12,"GAMET / SIGMET / AIRMET (raw)", ln=True, align="C")
        raw_pdf.set_font("Helvetica","",12)
        raw_pdf.multi_cell(0,7, gamet_raw.strip())

    for ch in chart_items:
        raw_pdf.add_chart(ch["title"], ch.get("subtitle",""), ch["img_bytes"])

    raw_name = "briefing_raw.pdf"
    raw_pdf.output(raw_name)

    # DETAILED PDF
    det_pdf = DetailedPDF()
    det_pdf.cover(
        title="Briefing Detalhado (PT)",
        pilot=pilot_name, ac_type=aircraft_type or "Tecnam P2008",
        callsign=callsign, reg=registration, date_str=date_str, time_utc=time_utc,
        remarks=remarks, live_url=APP_WEATHER_URL
    )
    # METAR/TAF analysis section
    if metar_taf_blocks:
        det_pdf.add_metar_taf_analysis(metar_taf_blocks)

    # Per-chart analysis (Geral → Portugal → Alentejo)
    for ch in chart_items:
        det_pdf.add_chart_analysis(ch["title"], ch.get("subtitle",""), ch["img_bytes"], ch["kind"])

    det_name = "briefing_detalhado.pdf"
    det_pdf.output(det_name)

    col1, col2 = st.columns(2)
    with col1:
        with open(raw_name, "rb") as f:
            st.download_button("Download RAW (EN)", f.read(), file_name=raw_name, mime="application/pdf", use_container_width=True)
    with col2:
        with open(det_name, "rb") as f:
            st.download_button("Download DETAILED (PT)", f.read(), file_name=det_name, mime="application/pdf", use_container_width=True)

st.divider()
st.markdown(f"**Live Weather page:** {APP_WEATHER_URL}")






