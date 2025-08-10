# app.py
# Flight Briefing generator — Raw (EN) + Detailed (PT)
# - Per-chart type selection (SIGWX / SPC / Wind&Temp / Other)
# - Detailed (PT): analysis por chart + síntese (Portugal → Alentejo)
# - Raw (EN): charts + link live METAR/TAF/SIGMET
# - Weather page em /Weather (METAR/TAF + SIGMET LPPC via AWC)
# Secrets: .streamlit/secrets.toml com OPENAI_API_KEY e opcional CHECKWX_API_KEY (só para METAR/TAF na página Weather)

import io
import os
import base64
import tempfile
from typing import Dict, Any, List, Tuple

import streamlit as st
from PIL import Image, ImageSequence
from fpdf import FPDF
import fitz  # PyMuPDF
from openai import OpenAI

APP_WEATHER_URL = "https://briefings.streamlit.app/Weather"  # usado no PDF RAW

# ===== UI base =====
st.set_page_config(page_title="Briefings", layout="wide")
st.markdown(
    """
    <style>
      .app-title { font-size: 2rem; font-weight: 800; margin-bottom: .5rem;}
      .muted { color: #6b7280; }
      .grid { display:grid; grid-template-columns: 1fr 1fr; gap: 16px; }
      .card { border: 1px solid #e5e7eb; border-radius: 14px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
      .section-title { font-weight: 700; font-size: 1.05rem; margin: 16px 0 6px; }
      .btn-row button { margin-right: 8px; }
      .small { font-size:.9rem; color:#6b7280 }
    </style>
    """,
    unsafe_allow_html=True,
)

client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ===== Helpers =====
def downscale_image(img: Image.Image, width: int = 1400) -> Tuple[Image.Image, io.BytesIO]:
    if img.width > width:
        r = width / img.width
        img = img.resize((width, int(img.height * r)))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    out.seek(0)
    return img, out

def first_pdf_page(uploaded_pdf_bytes: bytes) -> Image.Image:
    doc = fitz.open(stream=uploaded_pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    png = page.get_pixmap(dpi=200).tobytes("png")
    return Image.open(io.BytesIO(png)).convert("RGB").copy()

def first_frame_from_gif(file_bytes: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(file_bytes))
    im.seek(0)  # 1º frame
    return im.convert("RGB").copy()

# ===== GPT-5 — chart-specific explainers (PT) =====
def gpt5_sigwx_pt(focus: str, img_b64: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Explica o chart SIGWX em português, em texto corrido (sem listas), "
        "usando apenas o que está visível. Se algo estiver ilegível, diz que está ilegível e não infiras."
    )
    user = (
        f"Foco/região: {focus}. Cobre frentes, tipos/níveis de nuvens, convecção, "
        "turbulência/icing, níveis de congelamento, jatos, FL/UTC, símbolos e notas."
    )
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
        return f"Não foi possível gerar análise SIGWX (erro: {e})."

def gpt5_spc_pt(focus: str, img_b64: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Interpreta um Surface Pressure Chart (SPC) em texto corrido, "
        "usando só o que está visível; assinala incertezas sem extrapolar."
    )
    user = (
        f"Foco/região: {focus}. Explica isóbaras/gradiente, centros A/B, frentes e implicações, "
        "padrões de vento, símbolos/zonas e validade UTC."
    )
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
            max_output_tokens=1200,
            temperature=0.14,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível gerar análise SPC (erro: {e})."

def gpt5_windtemp_pt(focus: str, img_b64: str) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Explica o chart de Vento e Temperatura em português, em prosa, "
        "usando só dados visíveis."
    )
    user = (
        f"Foco/região: {focus}. Resume direção/velocidade por níveis, temperaturas, eixos/força de jet streams, "
        "marcadores de turbulência/gelo, níveis representados e validade."
    )
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
            max_output_tokens=1200,
            temperature=0.14,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível gerar análise Wind/Temp (erro: {e})."

def gpt5_overview_pt(images_b64: List[str]) -> str:
    sys = (
        "És meteorologista aeronáutico sénior. Vais fazer uma síntese a partir de múltiplos charts. "
        "Primeiro descreve a situação geral sobre Portugal, depois faz um foco específico no Alentejo. "
        "Escreve em parágrafos corridos, objetivos e operacionais. Usa só o que é visível."
    )
    user = "Tarefa: 1) Análise geral de Portugal. 2) Foco no Alentejo (detalhes, riscos, níveis)."
    content = [{"type":"input_text","text":user}]
    for b64 in images_b64:
        content.append({"type":"input_image","image_data":b64,"mime_type":"image/png"})
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role":"system","content":[{"type":"input_text","text":sys}]},
                {"role":"user","content":content}
            ],
            max_output_tokens=1600,
            temperature=0.14,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Não foi possível gerar a síntese (erro: {e})."

# ===== PDFs =====
class RawPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, mission: str):
        self.add_page(orientation="L")
        self.set_xy(0, 58)
        self.set_font("Arial","B",30)
        self.cell(0,18,"Weather Briefing (RAW)", ln=True, align="C")
        self.ln(4)
        self.set_font("Arial","",13)
        self.cell(0,8,f"Mission: {mission}", ln=True, align="C")
        self.ln(10)
    def live_links(self, icaos: List[str], base_url: str):
        self.add_page(orientation="P")
        self.set_font("Arial","B",18)
        self.cell(0,12,"Current METAR / TAF / SIGMET", ln=True, align="C")
        self.set_font("Arial","",12)
        self.ln(4)
        self.multi_cell(0,7,"This document intentionally omits time-sensitive METAR/TAF/SIGMET text. "
                            "Use the live links below for the latest conditions.")
        self.ln(2)
        unique = sorted({i.upper() for i in icaos if i.strip()})
        if unique:
            for icao in unique:
                url = f"{base_url}?icao={icao}"
                self.set_font("Arial","B",12)
                self.cell(0,8,f"{icao}: {url}", ln=True)
        else:
            url = f"{base_url}"
            self.set_font("Arial","B",12)
            self.cell(0,8,f"{url}", ln=True)
    def chart_fullpage(self, title: str, subtitle: str, img_bytes: io.BytesIO):
        self.add_page(orientation="L")
        self.set_font("Arial","B",18)
        self.cell(0,10,title, ln=True, align="C")
        if subtitle:
            self.set_font("Arial","I",12)
            self.cell(0,8,subtitle, ln=True, align="C")
        max_w = self.w - 30
        max_h = self.h - 50
        img = Image.open(img_bytes)
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

class DetailedPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, mission: str):
        self.add_page(orientation="L")
        self.set_xy(0, 58)
        self.set_font("Arial","B",30)
        self.cell(0,18,"Briefing Detalhado (PT)", ln=True, align="C")
        self.ln(4)
        self.set_font("Arial","",13)
        self.cell(0,8,f"Missão: {mission}", ln=True, align="C")
        self.ln(10)
    def chart_explained(self, title: str, subtitle: str, img_bytes: io.BytesIO, analysis_pt: str):
        self.add_page(orientation="L")
        self.set_font("Arial","B",18)
        self.cell(0,10,title, ln=True, align="C")
        if subtitle:
            self.set_font("Arial","I",12)
            self.cell(0,8,subtitle, ln=True, align="C")
        max_w = self.w - 30
        max_h = (self.h // 2) - 18
        img = Image.open(img_bytes)
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
        self.ln(h + 6)
        self.set_font("Arial","",12)
        self.multi_cell(0,7,analysis_pt)
    def synthesis_pages(self, text_pt: str):
        self.add_page(orientation="P")
        self.set_font("Arial","B",18)
        self.cell(0,12,"Síntese Geral (Portugal) e Foco no Alentejo", ln=True, align="C")
        self.set_font("Arial","",12)
        self.ln(2)
        self.multi_cell(0,7,text_pt)

# ===== Page UI =====
st.markdown('<div class="app-title">Flight Briefings</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Raw (EN) for instructor • Detailed (PT) for prep</div>', unsafe_allow_html=True)
st.divider()

# Default aerodromes for live link
default_icaos = ["LPPT", "LPBJ", "LEBZ"]
icaos_text = st.text_input("ICAOs for live weather (comma-separated)", value=",".join(default_icaos))
icaos = [i.strip().upper() for i in icaos_text.split(",") if i.strip()]

st.markdown('<div class="section-title">Charts</div>', unsafe_allow_html=True)
st.caption("Upload SIGWX / Wind & Temp / Surface Pressure charts (PDF, image, or GIF). Selecione o tipo para melhor análise.")
uploads = st.file_uploader(
    "Upload charts",
    type=["pdf","png","jpg","jpeg","gif"],  # <-- GIF permitido
    accept_multiple_files=True,
    label_visibility="collapsed"
)

# Build chart list com controlo por item
chart_items: List[Dict[str,Any]] = []
if uploads:
    for idx, f in enumerate(uploads):
        try:
            if f.type == "application/pdf":
                img = first_pdf_page(f.read())
            elif f.type in ("image/gif", "image/GIF"):
                img = first_frame_from_gif(f.read())
            else:
                img = Image.open(f).convert("RGB").copy()
            _, img_bytes = downscale_image(img)

            with st.container():
                col1, col2, col3 = st.columns([0.35, 0.35, 0.3])
                with col1:
                    base = (f.name or "").lower()
                    guessed = 0
                    if "spc" in base or "press" in base:
                        guessed = 1
                    elif "wind" in base or "temp" in base:
                        guessed = 2
                    kind = st.selectbox(
                        f"Chart type (#{idx+1})",
                        ["SIGWX","Surface Pressure (SPC)","Wind & Temp","Other"],
                        index=guessed,
                        key=f"kind_{idx}"
                    )
                with col2:
                    title = st.text_input("Title", value=kind if kind!="Other" else "Weather Chart", key=f"title_{idx}")
                with col3:
                    subtitle = st.text_input("Subtitle (optional)", value="", key=f"subtitle_{idx}")
            chart_items.append({"kind": kind, "title": title, "subtitle": subtitle, "img_bytes": img_bytes})
        except Exception as e:
            st.error(f"Failed to read {f.name}: {e}")

st.markdown('<div class="section-title">Mission / Notes</div>', unsafe_allow_html=True)
mission = st.text_input("Mission / Remarks", value="")

st.markdown('<div class="btn-row">', unsafe_allow_html=True)
gen_btn = st.button("Generate RAW (EN) + DETAILED (PT)", type="primary")
st.markdown('</div>', unsafe_allow_html=True)

# ===== Generate PDFs =====
if gen_btn:
    # RAW
    raw_pdf = RawPDF()
    raw_pdf.cover(mission or "")
    raw_pdf.live_links(icaos, APP_WEATHER_URL)
    for ch in chart_items:
        raw_pdf.chart_fullpage(ch["title"], ch.get("subtitle",""), ch["img_bytes"])
    raw_name = "briefing_raw.pdf"
    raw_pdf.output(raw_name)

    # DETAILED
    det_pdf = DetailedPDF()
    det_pdf.cover(mission or "")
    all_b64: List[str] = []
    for ch in chart_items:
        img_b64 = base64.b64encode(ch["img_bytes"].getvalue()).decode("utf-8")
        all_b64.append(img_b64)
        if ch["kind"] == "SIGWX":
            analysis = gpt5_sigwx_pt("Portugal e áreas adjacentes", img_b64)
        elif ch["kind"] == "Surface Pressure (SPC)":
            analysis = gpt5_spc_pt("Portugal e áreas adjacentes", img_b64)
        elif ch["kind"] == "Wind & Temp":
            analysis = gpt5_windtemp_pt("Portugal e áreas adjacentes", img_b64)
        else:
            analysis = gpt5_sigwx_pt("Portugal e áreas adjacentes", img_b64)
        det_pdf.chart_explained(ch["title"], ch.get("subtitle",""), ch["img_bytes"], analysis)

    if all_b64:
        synthesis = gpt5_overview_pt(all_b64)
        det_pdf.synthesis_pages(synthesis)

    det_name = "briefing_detalhado.pdf"
    det_pdf.output(det_name)

    c1, c2 = st.columns(2)
    with c1:
        with open(raw_name, "rb") as f:
            st.download_button("Download RAW (EN)", f.read(), file_name=raw_name, mime="application/pdf", use_container_width=True)
    with c2:
        with open(det_name, "rb") as f:
            st.download_button("Download DETALHADO (PT)", f.read(), file_name=det_name, mime="application/pdf", use_container_width=True)

st.divider()
st.markdown("**Live Weather page:**")
if len(icaos) == 1:
    st.write(f"{APP_WEATHER_URL}?icao={icaos[0]}")
else:
    st.write(APP_WEATHER_URL)




