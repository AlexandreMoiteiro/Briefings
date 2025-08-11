# app.py
# Briefings (EN raw + PT detailed) — simplified fonts (Helvetica) with ASCII normalization
from typing import Dict, Any, List, Tuple, Optional
import io, os, json, base64, tempfile, datetime as dt, unicodedata
from zoneinfo import ZoneInfo
from pathlib import Path

import requests
import streamlit as st
from PIL import Image
from fpdf import FPDF
import fitz  # PyMuPDF
from openai import OpenAI

APP_WEATHER_URL = "https://briefings.streamlit.app/Weather"

# ---------- Page & Styles ----------
st.set_page_config(page_title="Briefings", layout="wide")
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }
:root { --muted:#6b7280; --line:#e5e7eb; }
.app-title { font-size: 2.1rem; font-weight: 800; margin: 0 0 .25rem; }
.muted { color: var(--muted); margin-bottom: .75rem; }
.section { margin-top: 18px; }
.label { font-weight: 600; margin-bottom: 6px; }
.info-line { font-size:.92rem; color: var(--muted); }
</style>
""", unsafe_allow_html=True)

client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ---------- Text cleaning (ASCII for Helvetica) ----------
def ascii_safe(text: str) -> str:
    if text is None:
        return ""
    # normalize and strip non-ascii (accents, em-dash, etc.)
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    # normalize common dashes/spaces just in case
    return (t.replace("\u00A0", " ")
             .replace("\u2009", " ")
             .replace("\u2013", "-")
             .replace("\u2014", "-")
             .replace("\uFEFF", ""))

# ---------- Image helpers ----------
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

# ---------- CheckWX helpers ----------
def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

def fetch_metar_now(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status(); data = r.json().get("data", [])
        if not data: return ""
        return data[0].get("raw") or data[0].get("raw_text","") if isinstance(data[0], dict) else str(data[0])
    except Exception: return ""

def fetch_taf_now(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status(); data = r.json().get("data", [])
        if not data: return ""
        return data[0].get("raw") or data[0].get("raw_text","") if isinstance(data[0], dict) else str(data[0])
    except Exception: return ""

def fetch_notams(icao: str) -> List[str]:
    try:
        r = requests.get(f"https://api.checkwx.com/notam/{icao}", headers=cw_headers(), timeout=15)
        r.raise_for_status()
        j = r.json()
        arr = j.get("data", []) if isinstance(j, dict) else (j or [])
        out: List[str] = []
        for it in arr:
            if isinstance(it, str):
                out.append(it.strip())
            elif isinstance(it, dict):
                raw = (it.get("raw") or it.get("text") or it.get("notam") or it.get("message") or "")
                if raw: out.append(str(raw).strip())
        return out
    except Exception:
        return []

# ---------- SIGMET LPPC (auto AWC) ----------
def fetch_sigmet_lppc_auto() -> List[str]:
    try:
        r = requests.get("https://aviationweather.gov/api/data/isigmet",
                         params={"loc":"eur","format":"json"}, timeout=12)
        r.raise_for_status(); js = r.json()
        items = js if isinstance(js, list) else js.get("features", []) or []
        out: List[str] = []
        for it in items:
            props = it.get("properties", {}) if isinstance(it, dict) else {}
            if not props and isinstance(it, dict): props = it
            raw = (props.get("raw") or props.get("raw_text") or props.get("sigmet_text") or "").strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            if not raw: continue
            if fir == "LPPC" or " LPPC " in f" {raw} " or "FIR LPPC" in raw or " LPPC FIR" in raw:
                out.append(raw)
        return out
    except Exception:
        return []

# ---------- GAMET (Gist persistence) ----------
def _get_gist_secrets():
    token = (st.secrets.get("GAMET_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("GAMET_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("GAMET_GIST_FILENAME") or st.secrets.get("GIST_FILENAME") or "").strip()
    return token, gid, fn

def gamet_gist_config_ok() -> bool:
    token, gid, fn = _get_gist_secrets()
    return all([token, gid, fn])

def load_gamet_from_gist() -> Dict[str,Any]:
    if not gamet_gist_config_ok(): return {"text":"", "updated_utc":None}
    try:
        token, gid, fn = _get_gist_secrets()
        r = requests.get(f"https://api.github.com/gists/{gid}",
                         headers={"Authorization": f"token {token}", "Accept":"application/vnd.github+json"},
                         timeout=12)
        r.raise_for_status()
        files = r.json().get("files", {})
        file_obj = files.get(fn)
        if not file_obj:
            return {"text":"", "updated_utc":None}
        content = file_obj.get("content","")
        try:
            return json.loads(content)
        except Exception:
            return {"text": content, "updated_utc": None}
    except Exception:
        return {"text":"", "updated_utc":None}

def save_gamet_to_gist(text: str) -> bool:
    if not gamet_gist_config_ok(): return False
    try:
        token, gid, fn = _get_gist_secrets()
        payload = {"text": text.strip(), "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%MZ")}
        files = {fn: {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}
        r = requests.patch(f"https://api.github.com/gists/{gid}",
                           headers={"Authorization": f"token {token}", "Accept":"application/vnd.github+json"},
                           json={"files": files}, timeout=15)
        return r.status_code in (200, 201)
    except Exception:
        return False

# ---------- GPT-5 analyses (PT) ----------
def analyze_chart_pt(kind: str, img_b64: str) -> str:
    sys = ("Es meteorologista aeronautico senior. Analisa o chart fornecido (PT), em prosa continua, "
           "com 3 blocos: 1) Visao geral; 2) Portugal; 3) Alentejo. "
           "Sem listas. Usa so informacao visivel.")
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
            max_output_tokens=1500, temperature=0.14,
        )
        return ascii_safe((resp.output_text or "").strip())
    except Exception as e:
        return ascii_safe(f"Nao foi possivel analisar o chart (erro: {e}).")

def analyze_metar_taf_pt(icao: str, metar: str, taf: str) -> str:
    sys = ("Es meteorologista aeronautico senior. Em PT, interpreta METAR e TAF de forma corrida, "
           "explicando codigos e impacto operacional. Nao inventes.")
    user = f"Aerodromo {icao}. METAR:\n{metar}\n\nTAF:\n{taf}"
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[{"role":"system","content":[{"type":"input_text","text":sys}]},
                   {"role":"user","content":[{"type":"input_text","text":user}]}],
            max_output_tokens=1200, temperature=0.14,
        )
        return ascii_safe((resp.output_text or "").strip())
    except Exception as e:
        return ascii_safe(f"Nao foi possivel interpretar METAR/TAF (erro: {e}).")

def analyze_sigmet_pt(sigmet_text: str) -> str:
    sys = ("Es meteorologista aeronautico senior. Em PT, interpreta o SIGMET (LPPC) de forma corrida e operacional; "
           "fenomeno, area, niveis, validade e impacto.")
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[{"role":"system","content":[{"type":"input_text","text":sys}]},
                   {"role":"user","content":[{"type":"input_text","text":sigmet_text}]}],
            max_output_tokens=900, temperature=0.14,
        )
        return ascii_safe((resp.output_text or "").strip())
    except Exception as e:
        return ascii_safe(f"Nao foi possivel interpretar o SIGMET (erro: {e}).")

def analyze_gamet_pt(gamet_text: str) -> str:
    sys = ("Es meteorologista aeronautico senior. Em PT, explica o GAMET num paragrafo corrido; "
           "fenomenos, niveis e impacto operacional. Usa so o texto.")
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[{"role":"system","content":[{"type":"input_text","text":sys}]},
                   {"role":"user","content":[{"type":"input_text","text":gamet_text}]}],
            max_output_tokens=1200, temperature=0.14,
        )
        return ascii_safe((resp.output_text or "").strip())
    except Exception as e:
        return ascii_safe(f"Nao foi possivel interpretar o GAMET (erro: {e}).")

def analyze_notams_pt(icao: str, notams_raw: List[str]) -> str:
    text = "\n\n".join(notams_raw)
    if not text.strip():
        return "Sem NOTAMs disponiveis para este aerodromo no momento."
    sys = ("Es briefing officer. Em Portugues (sem acentos), resume os NOTAMs abaixo de forma corrida e operacional, "
           "destacando impacto para o voo (pistas/taxiways/iluminacao/NAVAIDs/horarios/restricoes), niveis/horarios "
           "e acoes recomendadas. Nao inventes; usa apenas o texto fornecido.")
    user = f"Aerodromo {icao} — NOTAMs (RAW):\n{text}"
    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[{"role":"system","content":[{"type":"input_text","text":sys}]},
                   {"role":"user","content":[{"type":"input_text","text":user}]}],
            max_output_tokens=1000, temperature=0.12,
        )
        return ascii_safe((resp.output_text or "").strip())
    except Exception as e:
        return ascii_safe(f"Nao foi possivel interpretar os NOTAMs (erro: {e}).")

# ---------- PDF helpers ----------
class Brand: line = (229,231,235)

def draw_header(pdf: FPDF, text: str):
    pdf.set_draw_color(*Brand.line); pdf.set_line_width(0.3)
    pdf.set_font("Helvetica","B",18)
    pdf.cell(0, 12, ascii_safe(text), ln=True, align="C", border="B")

def place_image_full(pdf: FPDF, png_bytes: io.BytesIO, max_h_pad: int=58):
    max_w = pdf.w - 22; max_h = pdf.h - max_h_pad
    img = Image.open(png_bytes); iw, ih = img.size
    r = min(max_w/iw, max_h/ih); w, h = int(iw*r), int(ih*r)
    x = (pdf.w - w)//2; y = pdf.get_y() + 4
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp, format="PNG"); path = tmp.name
    pdf.image(path, x=x, y=y, w=w, h=h); os.remove(path); pdf.ln(h+6)

class RawPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, pilot, aircraft, callsign, reg, date_str, time_utc, mission):
        self.add_page(orientation="L"); self.set_xy(0,40)
        self.set_font("Helvetica","B",28); self.cell(0,14,"Briefing", ln=True, align="C")  # no "RAW"
        self.set_font("Helvetica","",13); self.ln(2)
        self.cell(0,8,ascii_safe(f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}"), ln=True, align="C")
        self.cell(0,8,ascii_safe(f"Date: {date_str}   UTC: {time_utc}"), ln=True, align="C"); self.ln(6)
        self.set_font("Helvetica","I",12); self.cell(0,8,ascii_safe(f"Live METAR / TAF / SIGMET: {APP_WEATHER_URL}"), ln=True, align="C")
        if mission: self.ln(4); self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(f"Remarks: {mission}"), align="C")
    def section_text(self, title: str, body: str):
        self.add_page(orientation="P"); draw_header(self, title)
        self.set_font("Helvetica","",12); self.ln(3); self.multi_cell(0,7,ascii_safe(body))
    def notams_raw(self, icao: str, notams: List[str]):
        if not notams: return
        self.add_page(orientation="P"); draw_header(self, f"NOTAMs - {icao} (RAW)")
        self.set_font("Helvetica","",12); self.ln(3)
        for n in notams:
            self.multi_cell(0,7,ascii_safe(n)); self.ln(2)
    def gamet_raw(self, text):
        if not text.strip(): return
        self.section_text("GAMET (RAW)", text)
    def sigmet_raw(self, text):
        if not text.strip(): return
        self.section_text("SIGMET (LPPC) - RAW", text)
    def chart_full(self, title, subtitle, img_png):
        self.add_page(orientation="L"); draw_header(self,ascii_safe(title))
        if subtitle: self.set_font("Helvetica","I",12); self.cell(0,8,ascii_safe(subtitle), ln=True, align="C")
        place_image_full(self, img_png)

class DetailedPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, pilot, aircraft, callsign, reg, date_str, time_utc, mission):
        self.add_page(orientation="L"); self.set_xy(0,40)
        self.set_font("Helvetica","B",28); self.cell(0,14,"Briefing Detalhado (PT)", ln=True, align="C")
        self.set_font("Helvetica","",13); self.ln(2)
        self.cell(0,8,ascii_safe(f"Piloto: {pilot}   Aeronave: {aircraft}   Callsign: {callsign}   Matricula: {reg}"), ln=True, align="C")
        self.cell(0,8,ascii_safe(f"Data: {date_str}   UTC: {time_utc}"), ln=True, align="C"); self.ln(6)
        self.set_font("Helvetica","I",12); self.cell(0,8,ascii_safe(f"METAR / TAF / SIGMET ao vivo: {APP_WEATHER_URL}"), ln=True, align="C")
        if mission: self.ln(4); self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(f"Notas: {mission}"), align="C")
    def metar_taf_block(self, analyses: List[Tuple[str,str]]):
        if not analyses: return
        self.add_page(orientation="P"); draw_header(self,"METAR / TAF - Interpretacao (PT)")
        self.set_font("Helvetica","",12); self.ln(2)
        for icao, text in analyses:
            self.set_font("Helvetica","B",13); self.cell(0,8,ascii_safe(icao), ln=True)
            self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(text)); self.ln(2)
    def notams_block(self, parsed: List[Tuple[str,str,List[str]]]):
        any_data = any(r for _,_,r in parsed)
        if not any_data: return
        self.add_page(orientation="P"); draw_header(self,"NOTAMs - Interpretacao (PT)")
        for icao, analysis, raws in parsed:
            if not raws: continue
            self.set_font("Helvetica","B",12); self.cell(0,8,ascii_safe(icao), ln=True)
            self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(analysis)); self.ln(2)
    def sigmet_block(self, sigmet_text: str, analysis_pt: str):
        if not sigmet_text.strip(): return
        self.add_page(orientation="P"); draw_header(self,"SIGMET (LPPC) - Interpretacao (PT)")
        self.ln(2); self.set_font("Helvetica","B",12); self.cell(0,8,"Texto (RAW):", ln=True)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(sigmet_text)); self.ln(4)
        self.set_font("Helvetica","B",12); self.cell(0,8,"Interpretacao:", ln=True)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(analysis_pt))
    def gamet_block(self, gamet_text: str, analysis_pt: str):
        if not gamet_text.strip(): return
        self.add_page(orientation="P"); draw_header(self,"GAMET - Interpretacao (PT)")
        self.ln(2); self.set_font("Helvetica","B",12); self.cell(0,8,"Texto (RAW):", ln=True)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(gamet_text)); self.ln(4)
        self.set_font("Helvetica","B",12); self.cell(0,8,"Interpretacao:", ln=True)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(analysis_pt))
    def chart_block(self, title, subtitle, img_png, analysis_pt):
        self.add_page(orientation="L"); draw_header(self,ascii_safe(title))
        if subtitle: self.set_font("Helvetica","I",12); self.cell(0,8,ascii_safe(subtitle), ln=True, align="C")
        # image (upper half)
        max_w = self.w - 22; max_h = (self.h // 2) - 16
        img = Image.open(img_png); iw, ih = img.size
        r = min(max_w/iw, max_h/ih); w, h = int(iw*r), int(ih*r)
        x = (self.w - w)//2; y = self.get_y() + 4
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG"); path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h); os.remove(path); self.ln(h+8)
        # text (lower)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(analysis_pt))

# ---------- UI ----------
st.markdown('<div class="app-title">Briefings</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Raw (EN) for instructor • Detailed (PT) for your prep</div>', unsafe_allow_html=True)
st.divider()

# Pilot/Aircraft
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

st.markdown("#### Remarks / Mission")
mission = st.text_input("Remarks", "")

# Aerodromes for METAR/TAF
st.markdown("#### Aerodromes for METAR/TAF")
icaos_metar_str = st.text_input("ICAO list (comma-separated)", value="LPPT, LPBJ, LEBZ")
icaos_metar = [x.strip().upper() for x in icaos_metar_str.split(",") if x.strip()]

# Aerodromes for NOTAMs
st.markdown("#### Aerodromes for NOTAMs")
icaos_notam_str = st.text_input("ICAO list for NOTAMs (comma-separated)", value="LPSO, LPCB, LPEV")
icaos_notam = [x.strip().upper() for x in icaos_notam_str.split(",") if x.strip()]

# GAMET — Manual (Gist)
st.markdown("#### GAMET (LPPC) — Manual (saved)")
saved_gamet = load_gamet_from_gist()
gamet_text = st.text_area("Paste or edit GAMET (LPPC) here (saved and used in PDFs):",
                          value=saved_gamet.get("text",""), height=140)
if st.button("Save GAMET"):
    if save_gamet_to_gist(gamet_text):
        st.success("GAMET saved to Gist.")
    else:
        st.error("Could not save GAMET. Check Gist token/ID/filename.")

# Charts
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

st.markdown('<div class="section"></div>', unsafe_allow_html=True)
colGen1, colGen2 = st.columns(2)
gen_raw = colGen1.button("Generate RAW (EN)", type="primary")
gen_det = colGen2.button("Generate DETAILED (PT)")

# ---------- Generate RAW ----------
if gen_raw:
    date_str = str(flight_date)

    # Auto SIGMET LPPC
    sigmets = fetch_sigmet_lppc_auto()
    sigmet_text = "\n\n---\n\n".join(sigmets).strip()

    # NOTAMs for NOTAM list
    notams_map: Dict[str,List[str]] = {icao: fetch_notams(icao) for icao in icaos_notam}

    # RAW
    raw_pdf = RawPDF()
    raw_pdf.cover(pilot, aircraft_type, callsign, registration, date_str, time_utc, mission)

    # GAMET RAW
    if gamet_text.strip():
        raw_pdf.gamet_raw(gamet_text)

    # SIGMET RAW
    if sigmet_text:
        raw_pdf.sigmet_raw(sigmet_text)

    # NOTAMs RAW per aerodrome
    for icao, arr in notams_map.items():
        raw_pdf.notams_raw(icao, arr)

    # Charts (full)
    for ch in chart_rows:
        raw_pdf.chart_full(ch["title"], ch["subtitle"], ch["img_png"])

    raw_name = "briefing.pdf"  # no "(RAW)"
    raw_pdf.output(raw_name)
    with open(raw_name, "rb") as f:
        st.download_button("Download Briefing (EN)", data=f.read(), file_name=raw_name, mime="application/pdf", use_container_width=True)

# ---------- Generate DETAILED ----------
if gen_det:
    date_str = str(flight_date)

    # Auto SIGMET LPPC
    sigmets = fetch_sigmet_lppc_auto()
    sigmet_text = "\n\n---\n\n".join(sigmets).strip()

    # DETAILED
    det_pdf = DetailedPDF()
    det_pdf.cover(pilot, aircraft_type, callsign, registration, date_str, time_utc, mission)

    # METAR/TAF interpretations for METAR list
    metar_analyses: List[Tuple[str,str]] = []
    for icao in icaos_metar:
        metar = fetch_metar_now(icao); taf = fetch_taf_now(icao)
        if metar or taf:
            metar_analyses.append((icao, analyze_metar_taf_pt(icao, metar, taf)))
    det_pdf.metar_taf_block(metar_analyses)

    # NOTAMs (interpretation) for NOTAM list
    notams_map: Dict[str,List[str]] = {icao: fetch_notams(icao) for icao in icaos_notam}
    notam_parsed: List[Tuple[str,str,List[str]]] = []
    for icao, arr in notams_map.items():
        analysis = analyze_notams_pt(icao, arr) if arr else "Sem NOTAMs disponiveis."
        notam_parsed.append((icao, analysis, arr))
    det_pdf.notams_block(notam_parsed)

    # SIGMET interpretation (auto)
    if sigmet_text:
        det_pdf.sigmet_block(sigmet_text, analyze_sigmet_pt(sigmet_text))

    # GAMET interpretation (saved)
    if gamet_text.strip():
        det_pdf.gamet_block(gamet_text, analyze_gamet_pt(gamet_text))

    # Charts with 3-block analysis
    for ch in chart_rows:
        txt = analyze_chart_pt(
            kind=("SIGWX" if ch["kind"]=="SIGWX" else "SPC" if ch["kind"]=="SPC" else "WindTemp" if ch["kind"]=="Wind & Temp" else "Other"),
            img_b64=b64_png(ch["img_png"])
        )
        det_pdf.chart_block(ch["title"], ch["subtitle"], ch["img_png"], txt)

    det_name = "briefing_detalhado.pdf"
    det_pdf.output(det_name)
    with open(det_name, "rb") as f:
        st.download_button("Download Detailed (PT)", data=f.read(), file_name=det_name, mime="application/pdf", use_container_width=True)

st.divider()
st.markdown(f"**Live Weather page:** {APP_WEATHER_URL}")








