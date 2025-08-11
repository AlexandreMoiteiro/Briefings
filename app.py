# app.py
# Briefings — charts-only PDFs (RAW EN / Detailed PT)
# Links to Weather (live), NOTAMs (live), M&B/Performance, VFR Map

from typing import Dict, Any, List
import io, os, base64, tempfile, unicodedata
import streamlit as st
from PIL import Image
from fpdf import FPDF
import fitz  # PyMuPDF
from openai import OpenAI

# --------- URLs (ajusta se mudares os nomes das páginas) ----------
APP_WEATHER_URL = "https://briefings.streamlit.app/Weather"
APP_NOTAMS_URL  = "https://briefings.streamlit.app/NOTAMs"

# --------- Página & estilos ----------
st.set_page_config(page_title="Briefings", layout="wide")
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }
:root { --muted:#6b7280; --line:#e5e7eb; --pastel:#5a7fb3; }
.app-title { font-size: 2.1rem; font-weight: 800; margin: 0 0 .25rem; }
.muted { color: var(--muted); margin-bottom: .75rem; }
.section { margin-top: 18px; }
</style>
""", unsafe_allow_html=True)

# --------- IA (analise de charts PT) ----------
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

def ascii_safe(text: str) -> str:
    if text is None: return ""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return (t.replace("\u00A0", " ").replace("\u2009"," ").replace("\u2013","-").replace("\u2014","-").replace("\uFEFF",""))

def analyze_chart_pt(kind: str, img_b64: str) -> str:
    sys = ("Es meteorologista aeronautico senior. Analisa o chart fornecido (PT), em prosa continua, "
           "com 3 blocos: 1) Visao geral; 2) Portugal; 3) Alentejo. Sem listas. Usa so informacao visivel.")
    user = f"Tipo: {kind}. Faz a analise pedida."
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
            max_output_tokens=1500   # sem 'temperature'
        )
        return ascii_safe((resp.output_text or "").strip())
    except Exception as e:
        return ascii_safe(f"Nao foi possivel analisar o chart (erro: {e}).")

# --------- helpers de imagem ----------
def load_first_pdf_page(pdf_bytes: bytes, dpi: int = 300):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf"); page = doc.load_page(0)
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    return Image.open(io.BytesIO(png)).convert("RGB").copy()

def gif_first_frame(file_bytes: bytes):
    im = Image.open(io.BytesIO(file_bytes)); im.seek(0)
    return im.convert("RGB").copy()

def to_png_bytes(img: Image.Image) -> io.BytesIO:
    out = io.BytesIO(); img.save(out, format="PNG"); out.seek(0); return out

def ensure_png_bytes(uploaded):
    if uploaded.type == "application/pdf":
        img = load_first_pdf_page(uploaded.read(), dpi=300)
    elif uploaded.type.lower() == "image/gif":
        img = gif_first_frame(uploaded.read())
    else:
        img = Image.open(uploaded).convert("RGB").copy()
    return to_png_bytes(img)

def b64_png(img_bytes: io.BytesIO) -> str:
    return base64.b64encode(img_bytes.getvalue()).decode("utf-8")

# --------- PDF helpers (Helvetica/ASCII) ----------
PASTEL = (90, 127, 179)  # azul pastel discreto

def draw_header(pdf: FPDF, text: str):
    pdf.set_draw_color(229,231,235); pdf.set_line_width(0.3)
    pdf.set_font("Helvetica","B",18)
    pdf.cell(0, 12, ascii_safe(text), ln=True, align="C", border="B")

def place_image_full(pdf: FPDF, png_bytes: io.BytesIO, max_h_pad: int=58):
    max_w = pdf.w - 22; max_h = pdf.h - max_h_pad
    img = Image.open(png_bytes); iw, ih = img.size
    r = min(max_w/iw, max_h/ih); w, h = int(iw*r), int(ih*r)
    x = (pdf.w - w)//2; y = pdf.get_y() + 6
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp, format="PNG"); path = tmp.name
    pdf.image(path, x=x, y=y, w=w, h=h); os.remove(path); pdf.ln(h+10)

class RawPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, pilot, aircraft, callsign, reg, date_str, time_utc, mission):
        self.add_page(orientation="L"); self.set_xy(0,36)
        self.set_font("Helvetica","B",28); self.cell(0,14,"Briefing", ln=True, align="C")
        self.ln(2); self.set_font("Helvetica","",13)
        if pilot or aircraft or callsign or reg:
            self.cell(0,8,ascii_safe(f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}"), ln=True, align="C")
        if date_str or time_utc:
            self.cell(0,8,ascii_safe(f"Date: {date_str}   UTC: {time_utc}"), ln=True, align="C")
        # links (pastel, só URL)
        self.ln(6); self.set_font("Helvetica","I",12)
        self.set_text_color(*PASTEL); self.cell(0,7,APP_WEATHER_URL, ln=True, align="C", link=APP_WEATHER_URL)
        self.cell(0,7,APP_NOTAMS_URL,  ln=True, align="C", link=APP_NOTAMS_URL)
        self.set_text_color(0,0,0)
        if mission:
            self.ln(6); self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(f"Mission: {mission}"), align="C")
    def chart_full(self, title, subtitle, img_png):
        self.add_page(orientation="L"); draw_header(self,ascii_safe(title))
        if subtitle: self.set_font("Helvetica","I",12); self.cell(0,9,ascii_safe(subtitle), ln=True, align="C")
        place_image_full(self, img_png)

class DetailedPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, pilot, aircraft, callsign, reg, date_str, time_utc, mission):
        self.add_page(orientation="L"); self.set_xy(0,36)
        self.set_font("Helvetica","B",28); self.cell(0,14,"Briefing Detalhado (PT)", ln=True, align="C")
        self.ln(2); self.set_font("Helvetica","",13)
        if pilot or aircraft or callsign or reg:
            self.cell(0,8,ascii_safe(f"Piloto: {pilot}   Aeronave: {aircraft}   Callsign: {callsign}   Matricula: {reg}"), ln=True, align="C")
        if date_str or time_utc:
            self.cell(0,8,ascii_safe(f"Data: {date_str}   UTC: {time_utc}"), ln=True, align="C")
        # links (pastel, só URL)
        self.ln(6); self.set_font("Helvetica","I",12)
        self.set_text_color(*PASTEL); self.cell(0,7,APP_WEATHER_URL, ln=True, align="C", link=APP_WEATHER_URL)
        self.cell(0,7,APP_NOTAMS_URL,  ln=True, align="C", link=APP_NOTAMS_URL)
        self.set_text_color(0,0,0)
        if mission:
            self.ln(6); self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(f"Notas: {mission}"), align="C")
    def chart_block(self, title, subtitle, img_png, analysis_pt):
        self.add_page(orientation="L"); draw_header(self,ascii_safe(title))
        if subtitle: self.set_font("Helvetica","I",12); self.cell(0,9,ascii_safe(subtitle), ln=True, align="C")
        # imagem
        max_w = self.w - 22; max_h = (self.h // 2) - 18
        img = Image.open(img_png); iw, ih = img.size
        r = min(max_w/iw, max_h/ih); w, h = int(iw*r), int(ih*r)
        x = (self.w - w)//2; y = self.get_y() + 6
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG"); path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h); os.remove(path); self.ln(h+12)
        # texto
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(analysis_pt))

# --------- UI topo ----------
st.markdown('<div class="app-title">Briefings</div>', unsafe_allow_html=True)
st.divider()

# Acesso rápido às páginas
col_links = st.columns(4)
with col_links[0]:
    st.page_link("pages/Weather.py", label="Open Weather (Live) 🌤️", icon="🌤️")
with col_links[1]:
    st.page_link("pages/NOTAMs.py", label="Open NOTAMs (Live) 📄", icon="📄")
with col_links[2]:
    st.page_link("pages/MassBalance.py", label="Mass & Balance / Performance ✈️", icon="🧮", disabled=True)
with col_links[3]:
    st.page_link("pages/VFRMap.py", label="VFR Map 🗺️", icon="🗺️", disabled=True)

st.divider()

# --------- Pilot & Aircraft ---------
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

# Só Mission (sem Remarks)
st.markdown("#### Mission")
mission = st.text_input("Mission", "")

# --------- Charts upload ----------
st.markdown("#### Charts")
st.caption("Upload SIGWX / Surface Pressure (SPC) / Wind & Temp. Accepts PDF/PNG/JPG/JPEG/GIF.")
uploads = st.file_uploader("Upload charts", type=["pdf","png","jpg","jpeg","gif"], accept_multiple_files=True, label_visibility="collapsed")

chart_rows: List[Dict[str,Any]] = []
if uploads:
    for idx, f in enumerate(uploads):
        img_png = ensure_png_bytes(f)
        c1, c2, c3 = st.columns([0.34, 0.33, 0.33])
        with c1:
            guess = 0; name = (f.name or "").lower()
            if "spc" in name or "press" in name: guess = 1
            elif "wind" in name or "temp" in name: guess = 2
            kind = st.selectbox(f"Chart type #{idx+1}", ["SIGWX","SPC","Wind & Temp","Other"], index=guess, key=f"kind_{idx}")
        with c2:
            title = st.text_input("Title", value=("Significant Weather Chart (SIGWX)" if kind=="SIGWX" else
                                                  "Surface Pressure Chart (SPC)" if kind=="SPC" else
                                                  "Wind and Temperature Chart" if kind=="Wind & Temp" else
                                                  "Weather Chart"), key=f"title_{idx}")
        with c3:
            subtitle = st.text_input("Subtitle (optional)", value="", key=f"subtitle_{idx}")
        chart_rows.append({"kind": kind, "title": title, "subtitle": subtitle, "img_png": img_png})

# --------- Gerar PDFs (apenas charts) ----------
st.markdown('<div class="section"></div>', unsafe_allow_html=True)
colGen1, colGen2 = st.columns(2)
gen_raw = colGen1.button("Generate Briefing (EN)", type="primary")
gen_det = colGen2.button("Generate Detailed (PT)")

def make_raw_pdf():
    pdf = RawPDF()
    pdf.cover(pilot, aircraft_type, callsign, registration, str(flight_date), time_utc, mission)
    for ch in chart_rows:
        pdf.chart_full(ch["title"], ch["subtitle"], ch["img_png"])
    name = "briefing.pdf"; pdf.output(name)
    with open(name, "rb") as f:
        st.download_button("Download Briefing (EN)", data=f.read(), file_name=name, mime="application/pdf", use_container_width=True)

def make_detailed_pdf():
    pdf = DetailedPDF()
    pdf.cover(pilot, aircraft_type, callsign, registration, str(flight_date), time_utc, mission)
    for ch in chart_rows:
        txt = analyze_chart_pt(
            kind=("SIGWX" if ch["kind"]=="SIGWX" else "SPC" if ch["kind"]=="SPC" else "WindTemp" if ch["kind"]=="Wind & Temp" else "Other"),
            img_b64=b64_png(ch["img_png"])
        )
        pdf.chart_block(ch["title"], ch["subtitle"], ch["img_png"], txt)
    name = "briefing_detalhado.pdf"; pdf.output(name)
    with open(name, "rb") as f:
        st.download_button("Download Detailed (PT)", data=f.read(), file_name=name, mime="application/pdf", use_container_width=True)

if gen_raw:
    make_raw_pdf()
if gen_det:
    make_detailed_pdf()

st.divider()
st.markdown(f"**Live Weather page:** {APP_WEATHER_URL}")
st.markdown(f"**Live NOTAMs page:** {APP_NOTAMS_URL}")








