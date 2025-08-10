# app.py
# Flight Briefing generator — Raw (EN, no summaries) + Detailed (PT)
# - Raw PDF: charts only + live link to Weather page for METAR/TAF/SIGMET
# - Detailed PDF: expert Portuguese explanations of charts (GPT-5)
# - No sidebar; clean UI
# Secrets needed in .streamlit/secrets.toml:
#   OPENAI_API_KEY = "sk-..."
#   CHECKWX_API_KEY = "..."
#
# Weather live page lives at /Weather (see pages/Weather.py)

import io
import os
import base64
import tempfile
from typing import Dict, Any, List, Tuple, Optional

import streamlit as st
from PIL import Image
from fpdf import FPDF
import fitz  # PyMuPDF
from openai import OpenAI

APP_BASE_URL = "https://briefings.streamlit.app/Weather"

# ========== Setup ==========
st.set_page_config(page_title="Briefings", layout="wide")
st.markdown(
    """
    <style>
      .app-title { font-size: 2rem; font-weight: 700; margin-bottom: .25rem;}
      .muted { color: #6b7280; }
      .card { border: 1px solid #e5e7eb; border-radius: 14px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
      .section-title { font-weight: 700; font-size: 1.1rem; margin: 16px 0 6px; }
      .btn-row button { margin-right: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ========== Helpers ==========
def downscale_image(img: Image.Image, width: int = 1400) -> Tuple[Image.Image, io.BytesIO]:
    if img.width > width:
        r = width / img.width
        img = img.resize((width, int(img.height * r)))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    out.seek(0)
    return img, out

def get_first_pdf_page_as_image(uploaded_pdf_bytes: bytes) -> Image.Image:
    doc = fitz.open(stream=uploaded_pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    png = page.get_pixmap(dpi=200).tobytes("png")
    return Image.open(io.BytesIO(png)).convert("RGB").copy()

# ========== GPT-5 (Detailed analysis, Portuguese) ==========
def gpt5_chart_explainer_pt(kind: str, focus: str, img_b64: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Analisa o chart fornecido em português, em texto corrido, "
        "sem listas, explicando exatamente o que se vê e o que implica para o voo. "
        "Usa apenas informação visível. Se algo estiver ilegível, diz que está ilegível e não faças suposições."
    )
    user = (
        f"Chart: {kind}. Foco/região: {focus}. "
        "Explica frentes, níveis de nuvens, jatos, turbulência/gelo, simbologia, FL/UTC e contexto operacional."
    )
    content = [
        {"type": "input_text", "text": user},
        {"type": "input_image", "image_data": img_b64, "mime_type": "image/png"},
    ]
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role": "system", "content": [{"type":"input_text","text": sys}]},
                {"role": "user", "content": content},
            ],
            max_output_tokens=1500,
            temperature=0.15,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível gerar a análise agora (erro: {e})."

# ========== PDF classes ==========
class RawPDF(FPDF):
    def header(self): pass
    def footer(self): pass

    def cover(self, mission: str):
        self.add_page(orientation="L")
        self.set_xy(0, 60)
        self.set_font("Arial", "B", 30)
        self.cell(0, 18, "Weather Briefing (RAW)", ln=True, align="C")
        self.ln(6)
        self.set_font("Arial", "", 14)
        self.cell(0, 10, f"Mission: {mission}", ln=True, align="C")
        self.ln(12)

    def live_links(self, icaos: List[str], base_url: str):
        if not icaos: return
        self.add_page(orientation="P")
        self.set_font("Arial", "B", 18)
        self.cell(0, 12, "Current METAR / TAF / SIGMET", ln=True, align="C")
        self.ln(4)
        self.set_font("Arial", "", 12)
        self.multi_cell(0, 8, "This briefing intentionally omits time-sensitive METAR/TAF/SIGMET text. "
                               "Use the live link(s) below to see the latest data at the time of review.")
        self.ln(4)
        for icao in sorted(set([i.upper() for i in icaos])):
            url = f"{base_url}?icao={icao}"
            # FPDF doesn't support real hyperlinks without link zones; we include the URL text (clickable in most viewers).
            self.set_font("Arial", "B", 12)
            self.cell(0, 8, f"{icao}: {url}", ln=True)

    def chart_fullpage(self, title: str, subtitle: str, img_bytes: io.BytesIO):
        self.add_page(orientation="L")
        self.set_font("Arial", "B", 18)
        self.cell(0, 10, title, ln=True, align="C")
        if subtitle:
            self.set_font("Arial", "I", 13)
            self.cell(0, 8, subtitle, ln=True, align="C")
        self.ln(2)
        max_w = self.w - 30
        max_h = self.h - 50
        img = Image.open(img_bytes)
        iw, ih = img.size
        r = min(max_w / iw, max_h / ih)
        w, h = int(iw * r), int(ih * r)
        x = (self.w - w) // 2
        y = self.get_y()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h)
        os.remove(path)

class DetailedPDF(FPDF):
    def header(self): pass
    def footer(self): pass

    def cover(self, mission: str):
        self.add_page(orientation="L")
        self.set_xy(0, 60)
        self.set_font("Arial", "B", 30)
        self.cell(0, 18, "Briefing Detalhado (PT)", ln=True, align="C")
        self.ln(6)
        self.set_font("Arial", "", 14)
        self.cell(0, 10, f"Missão: {mission}", ln=True, align="C")
        self.ln(12)

    def chart_explained(self, title: str, subtitle: str, img_bytes: io.BytesIO, analysis_pt: str):
        self.add_page(orientation="L")
        self.set_font("Arial", "B", 18)
        self.cell(0, 10, title, ln=True, align="C")
        if subtitle:
            self.set_font("Arial", "I", 13)
            self.cell(0, 8, subtitle, ln=True, align="C")
        self.ln(2)
        max_w = self.w - 30
        max_h = (self.h // 2) - 20
        img = Image.open(img_bytes)
        iw, ih = img.size
        r = min(max_w / iw, max_h / ih)
        w, h = int(iw * r), int(ih * r)
        x = (self.w - w) // 2
        y = self.get_y()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h)
        os.remove(path)
        self.ln(h + 6)
        self.set_font("Arial", "", 12)
        self.multi_cell(0, 7, analysis_pt)

# ========== UI ==========
st.markdown('<div class="app-title">Flight Briefings</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Raw (EN) for instructor • Detailed (PT) for prep</div>', unsafe_allow_html=True)
st.divider()

# Upload charts
st.markdown('<div class="section-title">Charts</div>', unsafe_allow_html=True)
st.caption("Upload SIGWX / Wind & Temp / Surface Pressure charts (PDF or image).")
files = st.file_uploader("Upload charts", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, label_visibility="collapsed")

# ICAOs for live links
st.markdown('<div class="section-title">Aerodromes for live weather</div>', unsafe_allow_html=True)
default_icaos = ["LPPT", "LPBJ", "LEBZ"]
icaos_text = st.text_input("ICAOs (comma-separated)", value=",".join(default_icaos))
icaos = [i.strip().upper() for i in icaos_text.split(",") if i.strip()]

# Mission / notes
colA, colB = st.columns([0.6, 0.4])
with colA:
    mission = st.text_input("Mission / Remarks", value="")
with colB:
    st.markdown('<div class="btn-row">', unsafe_allow_html=True)
    gen_both = st.button("Generate PDFs", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

# Prepare charts
charts: List[Dict[str, Any]] = []
if files:
    for f in files:
        try:
            if f.type == "application/pdf":
                img = get_first_pdf_page_as_image(f.read())
            else:
                img = Image.open(f).convert("RGB").copy()
            _, img_bytes = downscale_image(img)
            # Guess type by filename
            name = (f.name or "").lower()
            if "sigwx" in name:
                title = "Significant Weather Chart (SIGWX)"
            elif "wind" in name or "temp" in name:
                title = "Wind and Temperature Chart"
            elif "pressure" in name or "spc" in name:
                title = "Surface Pressure Chart (SPC)"
            else:
                title = "Weather Chart"
            charts.append({"title": title, "subtitle": "", "img_bytes": img_bytes})
        except Exception as e:
            st.error(f"Failed to read {f.name}: {e}")

# Generate PDFs
if gen_both:
    # RAW (EN) — charts only + live link
    raw_pdf = RawPDF()
    raw_pdf.cover(mission or "")
    raw_pdf.live_links(icaos, APP_BASE_URL)
    for ch in charts:
        raw_pdf.chart_fullpage(ch["title"], ch.get("subtitle",""), ch["img_bytes"])
    raw_name = "briefing_raw.pdf"
    raw_pdf.output(raw_name)

    # DETAILED (PT) — add AI explanations
    det_pdf = DetailedPDF()
    det_pdf.cover(mission or "")
    for ch in charts:
        img_b64 = base64.b64encode(ch["img_bytes"].getvalue()).decode("utf-8")
        kind = ch["title"]
        analysis_pt = gpt5_chart_explainer_pt(kind, "Portugal e áreas adjacentes", img_b64)
        det_pdf.chart_explained(ch["title"], ch.get("subtitle",""), ch["img_bytes"], analysis_pt)
    det_name = "briefing_detalhado.pdf"
    det_pdf.output(det_name)

    c1, c2 = st.columns(2)
    with c1:
        with open(raw_name, "rb") as f:
            st.download_button("Download RAW (EN)", f.read(), file_name=raw_name, mime="application/pdf", use_container_width=True)
    with c2:
        with open(det_name, "rb") as f:
            st.download_button("Download DETALHADO (PT)", f.read(), file_name=det_name, mime="application/pdf", use_container_width=True)

# Quick link to Weather page
st.divider()
st.markdown("**Live Weather page:**")
if len(icaos) == 1:
    st.write(f"{APP_BASE_URL}?icao={icaos[0]}")
else:
    # default link (shows LPPT, LPBJ, LEBZ by default)
    st.write(APP_BASE_URL)





