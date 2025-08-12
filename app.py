# app.py — Briefings com editor de NOTAMs e GAMET (ambos via Gist) + METAR/TAF/SIGMET + PDFs

from typing import Dict, Any, List, Tuple
import io, os, re, base64, tempfile, unicodedata, json, datetime as dt
import streamlit as st
from PIL import Image
from fpdf import FPDF
import fitz  # PyMuPDF
import requests
from openai import OpenAI

# ---------- External pages (ajusta se renomeares) ----------
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

def ascii_safe(text: str) -> str:
    if text is None: return ""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii","ignore").decode("ascii")
    return (t.replace("\u00A0"," ").replace("\u2009"," ").replace("\u2013","-")
             .replace("\u2014","-").replace("\uFEFF",""))

# ---------- ICAO parser ----------
def parse_icaos(s: str) -> List[str]:
    tokens = re.split(r"[,\s]+", (s or "").strip(), flags=re.UNICODE)
    return [t.upper() for t in tokens if t]

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

# ---------- METAR/TAF (CheckWX) ----------
def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","").strip()
    return {"X-API-Key": key} if key else {}

def fetch_metar_now(icao: str) -> str:
    """METAR via CheckWX (RAW)."""
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
    """TAF via CheckWX (RAW)."""
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

# ---------- SIGMET LPPC (AWC) ----------
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
    except Exception: return []

# ---------- GAMET (Gist) ----------
def _get_gamet_secrets():
    token = (st.secrets.get("GAMET_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("GAMET_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("GAMET_GIST_FILENAME") or st.secrets.get("GIST_FILENAME") or "").strip()
    return token, gid, fn

def gamet_gist_config_ok() -> bool:
    token, gid, fn = _get_gamet_secrets()
    return all([token, gid, fn])

@st.cache_data(ttl=90)
def load_gamet_from_gist() -> Dict[str,Any]:
    if not gamet_gist_config_ok(): return {"text":"", "updated_utc":None}
    try:
        token, gid, fn = _get_gamet_secrets()
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

def save_gamet_to_gist(text: str) -> tuple[bool, str]:
    """Guarda o GAMET no Gist no formato {"updated_utc":"...", "text":"..."}."""
    token, gid, fn = _get_gamet_secrets()
    if not all([token, gid, fn]):
        return False, "Faltam segredos do GAMET (TOKEN/ID/FILENAME)."
    try:
        payload = {
            "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
            "text": (text or "").strip()
        }
        body = {"files": {fn: {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}}
        r = requests.patch(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept":"application/vnd.github+json"},
            json=body, timeout=12
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, "GAMET guardado no Gist."
    except Exception as e:
        return False, f"Erro a gravar GAMET no Gist: {e}"

# ---------- NOTAMs (Gist) ----------
def notam_gist_config_ok() -> bool:
    token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("NOTAM_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
    return bool(token and gid and fn)

@st.cache_data(ttl=90)
def load_notams_from_gist() -> Dict[str, Any]:
    if not notam_gist_config_ok():
        return {"map": {}, "updated_utc": None}
    try:
        token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
        gid   = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
        fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
        r = requests.get(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept":"application/vnd.github+json"},
            timeout=10,
        )
        r.raise_for_status()
        files = r.json().get("files", {})
        obj = files.get(fn) or {}
        content = (obj.get("content") or "").strip()
        if not content:
            return {"map": {}, "updated_utc": None}
        js = json.loads(content)
        if isinstance(js, dict) and "map" in js:
            return {"map": js.get("map") or {}, "updated_utc": js.get("updated_utc")}
        if isinstance(js, dict):
            upd = js.get("updated_utc") if "updated_utc" in js else None
            m = {k: v for k, v in js.items() if isinstance(v, list)}
            return {"map": m, "updated_utc": upd}
        return {"map": {}, "updated_utc": None}
    except Exception:
        return {"map": {}, "updated_utc": None}

def save_notams_to_gist(new_map: Dict[str, List[str]]) -> tuple[bool, str]:
    """Escreve no Gist o JSON {"updated_utc": "...", "map": {...}}."""
    if not notam_gist_config_ok():
        return False, "Segredos NOTAM_GIST_* em falta."
    try:
        token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
        gid   = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
        fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
        payload = {
            "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
            "map": {k: [s for s in v if str(s).strip()] for k, v in new_map.items()}
        }
        body = {
            "files": { fn: { "content": json.dumps(payload, ensure_ascii=False, indent=2) } }
        }
        r = requests.patch(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept":"application/vnd.github+json"},
            json=body, timeout=12
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, "NOTAMs guardados no Gist."
    except Exception as e:
        return False, f"Erro a gravar no Gist: {e}"

# ---------- GPT wrapper (texto) ----------
def gpt_text(prompt_system: str, prompt_user: str, max_tokens: int = 1200) -> str:
    last_err = ""
    try:
        r = client.responses.create(
            model="gpt-5",
            input=[
                {"role":"system","content":[{"type":"input_text","text":prompt_system}]},
                {"role":"user","content":[{"type":"input_text","text":prompt_user}]},
            ],
            max_output_tokens=max_tokens
        )
        out = getattr(r, "output_text", None)
        if out and out.strip():
            return ascii_safe(out.strip())
        last_err = "(responses returned empty)"
    except Exception as e:
        last_err = f"(responses) {e}"

    try:
        r2 = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role":"system","content":prompt_system},
                {"role":"user","content":prompt_user},
            ],
            max_completion_tokens=max_tokens  # <- correto no fallback
        )
        content = r2.choices[0].message.content
        if content and content.strip():
            return ascii_safe(content.strip())
        return ascii_safe(f"Falha na interpretacao: {last_err}; fallback chat vazio")
    except Exception as e2:
        return ascii_safe(f"Falha na interpretacao: {last_err}; fallback chat: {e2}")

# ---------- Análises (PT) ----------
def analyze_chart_pt(kind: str, img_b64: str) -> str:
    sys = (
        "Es meteorologista aeronautico senior. Analisa o chart fornecido em portugues, SEM listas: "
        "Prosa corrida em 3 blocos: 1) Visao geral; 2) Portugal; 3) Alentejo. "
        "Identifica e nomeia simbolos/anotacoes e conclui com impacto operacional. Usa apenas conteudo visivel."
    )
    user = f"Tipo de chart: {kind}."
    try:
        r = client.responses.create(
            model="gpt-5",
            input=[
                {"role":"system","content":[{"type":"input_text","text":sys}]},
                {"role":"user","content":[
                    {"type":"input_text","text":user},
                    {"type":"input_image","image_data":img_b64,"mime_type":"image/png"}
                ]},
            ],
            max_output_tokens=1600
        )
        return ascii_safe((r.output_text or "").strip())
    except Exception as e:
        return ascii_safe(f"Nao foi possivel analisar o chart (erro: {e}).")

def analyze_metar_taf_pt(icao: str, metar: str, taf: str) -> str:
    sys = ("Es meteorologista aeronautico senior. Em PT e texto corrido, interpreta METAR e TAF, "
           "explicando codigos e impacto operacional para voo. Usa apenas o texto fornecido.")
    user = f"Aerodromo {icao}\nMETAR:\n{metar}\n\nTAF:\n{taf}"
    return gpt_text(sys, user, max_tokens=1200)

def analyze_sigmet_pt(sigmet_text: str) -> str:
    sys = ("Es meteorologista aeronautico senior. Em PT e prosa corrida, interpreta o SIGMET LPPC: "
           "fenomeno, area, niveis/FL, validade/horas, movimento/intensidade, e impacto operacional.")
    return gpt_text(sys, sigmet_text, max_tokens=900)

def analyze_gamet_pt(gamet_text: str) -> str:
    sys = ("Es meteorologista aeronautico senior. Em PT e texto corrido, explica o GAMET LPPC: "
           "fenomenos, niveis, areas e impacto operacional. Usa apenas o texto fornecido.")
    return gpt_text(sys, gamet_text, max_tokens=1200)

# ---------- PDF helpers ----------
PASTEL = (90,127,179)  # azul suave

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

def pdf_embed_first_page(pdf: FPDF, pdf_bytes: bytes, title: str):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    png = page.get_pixmap(dpi=300).tobytes("png")
    img = Image.open(io.BytesIO(png)).convert("RGB")
    bio = io.BytesIO(); img.save(bio, format="PNG"); bio.seek(0)
    pdf.add_page(orientation="L"); draw_header(pdf, ascii_safe(title))
    place_image_full(pdf, bio)

class DetailedPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc):
        self.add_page(orientation="L"); self.set_xy(0,36)
        self.set_font("Helvetica","B",28); self.cell(0,14,"Briefing Detalhado (PT)", ln=True, align="C")
        self.ln(2); self.set_font("Helvetica","",13)
        self.cell(0,8,ascii_safe(f"Missao: {mission_no}"), ln=True, align="C")
        if pilot or aircraft or callsign or reg:
            self.cell(0,8,ascii_safe(f"Piloto: {pilot}   Aeronave: {aircraft}   Callsign: {callsign}   Matricula: {reg}"), ln=True, align="C")
        if date_str or time_utc:
            self.cell(0,8,ascii_safe(f"Data: {date_str}   UTC: {time_utc}"), ln=True, align="C")
        # texto explicativo + links
        self.ln(6); self.set_font("Helvetica","I",12)
        self.set_text_color(*PASTEL)
        self.cell(0,7,ascii_safe("METAR/TAF/GAMET (Weather): ")+APP_WEATHER_URL, ln=True, align="C", link=APP_WEATHER_URL)
        self.cell(0,7,ascii_safe("NOTAMs (pagina dedicada): ")+APP_NOTAMS_URL, ln=True, align="C", link=APP_NOTAMS_URL)
        self.set_text_color(0,0,0)

    def metar_taf_block(self, analyses: List[Tuple[str,str]]):
        self.add_page(orientation="P"); draw_header(self,"METAR / TAF — Interpretacao (PT)")
        self.set_font("Helvetica","",12); self.ln(2)
        for icao, text in analyses:
            self.set_font("Helvetica","B",13); self.cell(0,8,ascii_safe(icao), ln=True)
            self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(text)); self.ln(2)

    def sigmet_block(self, sigmet_text: str, analysis_pt: str):
        if not sigmet_text.strip(): return
        self.add_page(orientation="P"); draw_header(self,"SIGMET (LPPC) — Interpretacao (PT)")
        self.ln(2); self.set_font("Helvetica","B",12); self.cell(0,8,"Texto (RAW):", ln=True)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(sigmet_text)); self.ln(4)
        self.set_font("Helvetica","B",12); self.cell(0,8,"Interpretacao:", ln=True)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(analysis_pt))

    def gamet_block(self, gamet_text: str, analysis_pt: str):
        if not gamet_text.strip(): return
        self.add_page(orientation="P"); draw_header(self,"GAMET — Interpretacao (PT)")
        self.ln(2); self.set_font("Helvetica","B",12); self.cell(0,8,"Texto (RAW):", ln=True)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(gamet_text)); self.ln(4)
        self.set_font("Helvetica","B",12); self.cell(0,8,"Interpretacao:", ln=True)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(analysis_pt))

    def notams_block(self, parsed: List[Tuple[str,str,List[str]]]):
        # Secção textual
        self.add_page(orientation="P"); draw_header(self,"NOTAMs — Texto")
        for icao, analysis, _ in parsed:
            self.set_font("Helvetica","B",12); self.cell(0,8,ascii_safe(icao), ln=True)
            self.set_font("Helvetica","",12)
            self.multi_cell(0,7,ascii_safe(analysis if analysis else "Sem NOTAMs.")); 
            self.ln(2)
        # Apêndice RAW
        self.add_page(orientation="P"); draw_header(self,"NOTAMs — RAW (Apendice)")
        self.set_font("Helvetica","",12); self.ln(2)
        for icao, _, arr in parsed:
            self.set_font("Helvetica","B",12); self.cell(0,8,ascii_safe(icao), ln=True)
            self.set_font("Helvetica","",12)
            if arr:
                for n in arr:
                    self.multi_cell(0,7,ascii_safe(n)); self.ln(1)
            else:
                self.multi_cell(0,7,ascii_safe("Sem entradas."))
            self.ln(2)

    def chart_block(self, title, subtitle, img_png, analysis_pt):
        self.add_page(orientation="L"); draw_header(self,ascii_safe(title))
        if subtitle: self.set_font("Helvetica","I",12); self.cell(0,9,ascii_safe(subtitle), ln=True, align="C")
        max_w = self.w - 22; max_h = (self.h // 2) - 18
        img = Image.open(img_png); iw, ih = img.size
        r = min(max_w/iw, max_h/ih); w, h = int(iw*r), int(ih*r)
        x = (self.w - w)//2; y = self.get_y() + 6
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG"); path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h); os.remove(path); self.ln(h+12)
        self.set_font("Helvetica","",12); self.multi_cell(0,7,ascii_safe(analysis_pt))

class FinalBriefPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc):
        self.add_page(orientation="L"); self.set_xy(0,36)
        self.set_font("Helvetica","B",28); self.cell(0,14,"Briefing", ln=True, align="C")
        self.ln(2); self.set_font("Helvetica","",13)
        self.cell(0,8,ascii_safe(f"Mission: {mission_no}"), ln=True, align="C")
        if pilot or aircraft or callsign or reg:
            self.cell(0,8,ascii_safe(f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}"), ln=True, align="C")
        if date_str or time_utc:
            self.cell(0,8,ascii_safe(f"Date: {date_str}   UTC: {time_utc}"), ln=True, align="C")
        self.ln(6); self.set_font("Helvetica","I",12)
        self.set_text_color(*PASTEL)
        self.cell(0,7,ascii_safe("Weather page: ")+APP_WEATHER_URL, ln=True, align="C", link=APP_WEATHER_URL)
        self.cell(0,7,ascii_safe("NOTAMs page: ")+APP_NOTAMS_URL, ln=True, align="C", link=APP_NOTAMS_URL)
        self.set_text_color(0,0,0)

    def flightplan_image(self, title, img_png):
        self.add_page(orientation="L"); draw_header(self, ascii_safe(title))
        place_image_full(self, img_png)

    def charts_only(self, charts: List[Tuple[str,str,io.BytesIO]]):
        for (title, subtitle, img_png) in charts:
            self.add_page(orientation="L"); draw_header(self, ascii_safe(title))
            if subtitle: self.set_font("Helvetica","I",12); self.cell(0,9,ascii_safe(subtitle), ln=True, align="C")
            place_image_full(self, img_png)

# ---------- UI: header & links ----------
st.markdown('<div class="app-title">Briefings</div>', unsafe_allow_html=True)
links = st.columns(4)
with links[0]:
    st.page_link("pages/Weather.py", label="Open Weather 🌤️")
with links[1]:
    st.page_link("pages/NOTAMs.py", label="Open NOTAMs 📄")
with links[2]:
    st.page_link("pages/VFRMap.py", label="Open VFR Map 🗺️")
with links[3]:
    st.page_link("pages/MassBalance.py", label="Mass & Balance ✈️")

st.divider()

# ---------- Pilot/Aircraft + Mission ----------
colA, colB, colC = st.columns(3)
with colA:
    pilot = st.text_input("Pilot name", "")
    callsign = st.text_input("Mission callsign", "")
with colB:
    aircraft_type = st.text_input("Aircraft type", "Tecnam P2008")
    registration = st.text_input("Registration", "")
with colC:
    mission_no = st.text_input("Mission number", "")
    flight_date = st.date_input("Flight date")
    time_utc = st.text_input("UTC time", "")

# ---------- ICAOs ----------
st.markdown("#### Aerodromes")
c1, c2 = st.columns(2)
with c1:
    icaos_metar_str = st.text_input("ICAO list for METAR/TAF (comma / space / newline)", value="LPPT LPBJ LEBZ")
    icaos_metar = parse_icaos(icaos_metar_str)
with c2:
    icaos_notam_str = st.text_input("ICAO list for NOTAMs (comma / space / newline)", value="LPSO LPCB LPEV")
    icaos_notam = parse_icaos(icaos_notam_str)

# ---------- Editor de NOTAMs (por ICAO) ----------
st.markdown("### NOTAMs (editar e guardar)")
saved_notams = load_notams_from_gist()
existing_map: Dict[str, List[str]] = (saved_notams.get("map") or {}) if isinstance(saved_notams, dict) else {}

def parse_block_to_list(text: str) -> List[str]:
    if not text.strip():
        return []
    parts = re.split(r"\n\s*\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]

edit_cols = st.columns(3)
editors_notam: Dict[str, str] = {}
for i, icao in enumerate(icaos_notam):
    with edit_cols[i % 3]:
        initial_text = ""
        if existing_map.get(icao):
            initial_text = "\n\n".join(existing_map.get(icao, []))
        editors_notam[icao] = st.text_area(
            f"{icao} — NOTAMs",
            value=initial_text,
            placeholder=("Ex.: AERODROME BEACON ONLY FLASH-GREEN LIGHT OPERATIVE.\n"
                         "FROM: 29th Jul 2025 15:10 TO: 29th Sep 2025 18:18 EST\n\n"
                         "Outro NOTAM aqui..."),
            key=f"ed_notam_{icao}",
            height=160
        )

col_save_n = st.columns([0.4,0.3,0.3])
with col_save_n[0]:
    overwrite_all_n = st.checkbox("Substituir TODOS os aerodromos do Gist (NOTAMs)", value=False)
with col_save_n[1]:
    if st.button("Guardar NOTAMs no Gist"):
        new_map: Dict[str, List[str]] = {}
        if not overwrite_all_n:
            new_map.update(existing_map)
        for icao in icaos_notam:
            new_map[icao] = parse_block_to_list(editors_notam.get(icao,""))
        ok, msg = save_notams_to_gist(new_map)
        if ok:
            st.success(msg)
            st.cache_data.clear()
        else:
            st.error(msg)

st.divider()

# ---------- Editor de GAMET ----------
st.markdown("### GAMET (editar e guardar)")
_gamet_obj = load_gamet_from_gist()
_gamet_initial = (_gamet_obj.get("text") or "").strip()

gamet_text = st.text_area(
    "GAMET — cola aqui o texto completo",
    value=_gamet_initial,
    placeholder="Ex.:\nLPPC FIR GAMET VALID 12/06Z-12/12Z\n... (texto integral aqui) ...",
    height=220
)

col_gs = st.columns([0.3,0.7])
with col_gs[0]:
    if st.button("Guardar GAMET no Gist"):
        ok, msg = save_gamet_to_gist(gamet_text)
        if ok:
            st.success(msg); st.cache_data.clear()
        else:
            st.error(msg)

st.divider()

# ---------- Charts upload ----------
st.markdown("#### Charts")
st.caption("Upload SIGWX / SPC / Wind & Temp. Accepts PDF/PNG/JPG/JPEG/GIF.")
uploads = st.file_uploader("Upload charts", type=["pdf","png","jpg","jpeg","gif"],
                           accept_multiple_files=True, label_visibility="collapsed")

charts: List[Dict[str,Any]] = []
if uploads:
    for idx, f in enumerate(uploads):
        img_png = ensure_png_bytes(f)
        c1r, c2r, c3r = st.columns([0.34,0.33,0.33])
        with c1r:
            guess = 0; name = (f.name or "").lower()
            if "spc" in name or "press" in name: guess = 1
            elif "wind" in name or "temp" in name: guess = 2
            kind = st.selectbox(f"Chart type #{idx+1}", ["SIGWX","SPC","Wind & Temp","Other"], index=guess, key=f"kind_{idx}")
        with c2r:
            title = st.text_input("Title", value=("Significant Weather Chart (SIGWX)" if kind=="SIGWX" else
                                                  "Surface Pressure Chart (SPC)" if kind=="SPC" else
                                                  "Wind and Temperature Chart" if kind=="Wind & Temp" else
                                                  "Weather Chart"), key=f"title_{idx}")
        with c3r:
            subtitle = st.text_input("Subtitle (optional)", value="", key=f"subtitle_{idx}")
        charts.append({"kind": kind, "title": title, "subtitle": subtitle, "img_png": img_png})

# ---------- Generate Detailed (PT) ----------
def analyze_notams_text_only(icao: str, notams_raw: List[str]) -> str:
    return "\n\n".join(notams_raw).strip() or "Sem NOTAMs."

gen_det = st.button("Generate Detailed (PT)")
if gen_det:
    # METAR/TAF (texto corrido)
    metar_analyses: List[Tuple[str,str]] = []
    for icao in icaos_metar:
        metar = fetch_metar_now(icao) or ""
        taf   = fetch_taf_now(icao) or ""
        txt = analyze_metar_taf_pt(icao, metar, taf) if (metar or taf) else "Sem METAR/TAF disponiveis neste momento."
        metar_analyses.append((icao, txt))

    # SIGMET
    sigmets = fetch_sigmet_lppc_auto()
    sigmet_text = "\n\n---\n\n".join(sigmets).strip()
    sigmet_analysis = analyze_sigmet_pt(sigmet_text) if sigmet_text else ""

    # GAMET (usar o texto do editor; se vazio, cair para Gist)
    gamet_for_pdf = (gamet_text or _gamet_initial or "").strip()
    gamet_analysis = analyze_gamet_pt(gamet_for_pdf) if gamet_for_pdf else ""

    # NOTAMs para PDF: usar editores locais (se preenchidos) por cima do Gist
    current_map = {**(existing_map or {})}
    for icao in icaos_notam:
        edited = parse_block_to_list(editors_notam.get(icao,""))
        if edited: current_map[icao] = edited
    notam_parsed: List[Tuple[str,str,List[str]]] = []
    for icao in icaos_notam:
        arr = list((current_map or {}).get(icao, []))
        analysis = analyze_notams_text_only(icao, arr)
        notam_parsed.append((icao, analysis, arr))

    # Build PDF
    det_pdf = DetailedPDF()
    det_pdf.cover(mission_no, pilot, aircraft_type, callsign, registration, str(flight_date), time_utc)
    det_pdf.metar_taf_block(metar_analyses)
    if sigmet_text:
        det_pdf.sigmet_block(sigmet_text, sigmet_analysis)
    if gamet_for_pdf:
        det_pdf.gamet_block(gamet_for_pdf, gamet_analysis)
    det_pdf.notams_block(notam_parsed)

    for ch in charts:
        analysis_txt = "—"
        det_pdf.chart_block(ch["title"], ch["subtitle"], ch["img_png"], analysis_txt)

    det_name = f"Briefing Detalhado - Missao {mission_no or 'X'}.pdf"
    det_pdf.output(det_name)
    with open(det_name, "rb") as f:
        st.download_button("Download Detailed (PT)", data=f.read(), file_name=det_name, mime="application/pdf", use_container_width=True)

st.divider()

# ---------- Optional Flight Plan & M&B PDFs ----------
st.markdown("#### Flight Plan (optional image/PDF/GIF)")
fp_upload = st.file_uploader("Upload your flight plan (PDF/PNG/JPG/JPEG/GIF)", type=["pdf","png","jpg","jpeg","gif"], accept_multiple_files=False)
fp_img_png: io.BytesIO | None = None
if fp_upload:
    fp_img_png = ensure_png_bytes(fp_upload)
    st.success("Flight plan will be included in the final briefing.")

st.markdown("#### M&B / Performance PDF (from external app)")
mb_upload = st.file_uploader("Upload M&B/Performance PDF to embed", type=["pdf"], accept_multiple_files=False)

# ---------- Generate Final Briefing (EN) ----------
class FinalBriefPDF(FPDF):
    def header(self): pass
    def footer(self): pass
    def cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc):
        self.add_page(orientation="L"); self.set_xy(0,36)
        self.set_font("Helvetica","B",28); self.cell(0,14,"Briefing", ln=True, align="C")
        self.ln(2); self.set_font("Helvetica","",13)
        self.cell(0,8,ascii_safe(f"Mission: {mission_no}"), ln=True, align="C")
        if pilot or aircraft or callsign or reg:
            self.cell(0,8,ascii_safe(f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}"), ln=True, align="C")
        if date_str or time_utc:
            self.cell(0,8,ascii_safe(f"Date: {date_str}   UTC: {time_utc}"), ln=True, align="C")
        self.ln(6); self.set_font("Helvetica","I",12)
        self.set_text_color(*PASTEL)
        self.cell(0,7,ascii_safe("Weather page: ")+APP_WEATHER_URL, ln=True, align="C", link=APP_WEATHER_URL)
        self.cell(0,7,ascii_safe("NOTAMs page: ")+APP_NOTAMS_URL, ln=True, align="C", link=APP_NOTAMS_URL)
        self.set_text_color(0,0,0)

    def flightplan_image(self, title, img_png):
        self.add_page(orientation="L"); draw_header(self, ascii_safe(title))
        place_image_full(self, img_png)

    def charts_only(self, charts: List[Tuple[str,str,io.BytesIO]]):
        for (title, subtitle, img_png) in charts:
            self.add_page(orientation="L"); draw_header(self, ascii_safe(title))
            if subtitle: self.set_font("Helvetica","I",12); self.cell(0,9,ascii_safe(subtitle), ln=True, align="C")
            place_image_full(self, img_png)

gen_final = st.button("Generate Final Briefing (EN)")
if gen_final:
    fb = FinalBriefPDF()
    fb.cover(mission_no, pilot, aircraft_type, callsign, registration, str(flight_date), time_utc)

    if mb_upload is not None:
        mb_bytes = mb_upload.read()
        pdf_embed_first_page(fb, mb_bytes, "Mass & Balance / Performance")

    if fp_img_png is not None:
        fb.flightplan_image("Flight Plan", fp_img_png)

    if 'charts' in locals():
        fb.charts_only([(c["title"], c["subtitle"], c["img_png"]) for c in charts])

    final_name = f"Briefing - Missao {mission_no or 'X'}.pdf"
    fb.output(final_name)
    with open(final_name, "rb") as f:
        st.download_button("Download Final Briefing (EN)", data=f.read(), file_name=final_name, mime="application/pdf", use_container_width=True)

st.divider()
st.markdown(f"**Weather:** {APP_WEATHER_URL}")
st.markdown(f"**NOTAMs:** {APP_NOTAMS_URL}")
st.markdown(f"**VFR Map:** {APP_VFRMAP_URL}")
st.markdown(f"**M&B / Performance:** {APP_MNB_URL}")






