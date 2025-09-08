# app.py — Briefings com editor de NOTAMs, GAMET e SIGMET (via Gist)
# + Weather CHARTS + METAR/TAF (interpretação curta) + PDFs + Emparelhamento Navlog↔VFR por ROTA
from typing import Dict, Any, List, Tuple, Optional
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
st.markdown(
    """
<style>
:root { --muted:#6b7280; --line:#e5e7eb; --pastel:#5a7fb3; }
.app-top { display:flex; align-items:center; gap:.5rem .6rem; flex-wrap:wrap; margin:.1rem 0 .5rem; }
.app-title { font-size: 2.2rem; font-weight: 800; margin: 0 .25rem 0 0; }
.small { font-size:.92rem; color:var(--muted); }
.monos{font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap}
hr{border:none;border-top:1px solid var(--line);margin:12px 0}
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }
.block-label{font-weight:700;margin:.25rem 0}
.section-card{border:1px solid var(--line); border-radius:12px; padding:12px 14px; background:#fff}
.kv{display:grid;grid-template-columns:140px 1fr; gap:.35rem .8rem}
.kv .k{color:#374151}
.kv .v{color:#111827}
.btnbar a{display:inline-block;padding:6px 10px;border:1px solid var(--line);border-radius:8px;text-decoration:none;font-weight:600;color:#111827;background:#f8fafc}
.btnbar a:hover{background:#f1f5f9}
</style>
""",
    unsafe_allow_html=True,
)

# ---------- OpenAI client ----------
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ---------- Constantes úteis ----------
LPSO_ARP = (39.211667, -8.057778)  # LPSO (ARP)

# ---------- Utils ----------
def ascii_safe(text: str) -> str:
    if text is None:
        return ""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return (
        t.replace("\u00A0", " ")
         .replace("\u2009", " ")
         .replace("\u2013", "-")
         .replace("\u2014", "-")
         .replace("\uFEFF", "")
    )

def parse_icaos(s: str) -> List[str]:
    tokens = re.split(r"[,\s]+", (s or "").strip(), flags=re.UNICODE)
    return [t.upper() for t in tokens if t]

# ---------- Image helpers ----------
def load_first_pdf_page(pdf_bytes: bytes, dpi: int = 450) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    return Image.open(io.BytesIO(png)).convert("RGB").copy()

def gif_first_frame(file_bytes: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(file_bytes))
    im.seek(0)
    return im.convert("RGB").copy()

def to_png_bytes(img: Image.Image) -> io.BytesIO:
    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out

def ensure_png_from_bytes(file_bytes: bytes, mime: str) -> io.BytesIO:
    """Aceita PDF/PNG/JPG/JPEG/GIF e devolve bytes PNG (ou placeholder)."""
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
            bio = io.BytesIO()
            ph.save(bio, format="PNG")
            bio.seek(0)
            return bio

# ---------- Texto auxiliar de PDFs ----------
def extract_validity(s: str) -> str:
    u = (s or "").upper()
    m = re.search(r"VALID\s*([0-3]?\d/?[0-2]?\dZ\s*-\s*[0-3]?\d/?[0-2]?\dZ|[0-2]?\d{2,3}Z)", u)
    if m:
        return m.group(0).strip()
    m2 = re.search(r"\b([01]?\d|2[0-3])(?:00)?Z\b(\s*-\s*([01]?\d|2[0-3])(?:00)?Z\b)?", u)
    if m2:
        return m2.group(0).strip()
    return ""

def derive_default_title(kind: str, filename: str, text_hint: str) -> str:
    base = (
        "Significant Weather Chart (SIGWX)" if kind == "SIGWX" else
        "Surface Pressure Chart (SPC)" if kind == "SPC" else
        "Wind and Temperature Chart" if kind == "Wind & Temp" else
        "Weather Chart"
    )
    short = extract_validity(filename) or extract_validity(text_hint)
    return f"{base}{' — ' + short if short else ''}"

# ---------- Ordenação lógica de charts ----------
_KIND_RANK = {"SPC": 1, "SIGWX": 2, "Wind & Temp": 3, "Other": 9}

def _chart_sort_key(c: Dict[str, Any]) -> Tuple[int, int]:
    kind = c.get("kind", "Other")
    rank = _KIND_RANK.get(kind, 9)
    order = int(c.get("order", 9999) or 9999)
    return (rank, order)

# ---------- METAR/TAF (CheckWX) ----------
def cw_headers() -> Dict[str, str]:
    key = st.secrets.get("CHECKWX_API_KEY", "\n").strip()
    return {"X-API-Key": key} if key else {}

def fetch_metar_now(icao: str) -> str:
    try:
        hdr = cw_headers()
        if not hdr:
            return ""
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=hdr, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text", "") or ""
        return str(data[0])
    except Exception:
        return ""

def fetch_taf_now(icao: str) -> str:
    try:
        hdr = cw_headers()
        if not hdr:
            return ""
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=hdr, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text", "") or ""
        return str(data[0])
    except Exception:
        return ""

# ---------- Gist helpers: GAMET & NOTAMs ----------

def _get_gamet_secrets() -> Tuple[str, str, str]:
    token = (st.secrets.get("GAMET_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("GAMET_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("GAMET_GIST_FILENAME") or st.secrets.get("GIST_FILENAME") or "").strip()
    return token, gid, fn

def gamet_gist_config_ok() -> bool:
    token, gid, fn = _get_gamet_secrets()
    return all([token, gid, fn])

@st.cache_data(ttl=90)
def load_gamet_from_gist() -> Dict[str, Any]:
    if not gamet_gist_config_ok():
        return {"text": "", "updated_utc": None}
    try:
        token, gid, fn = _get_gamet_secrets()
        r = requests.get(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            timeout=12,
        )
        r.raise_for_status()
        files = r.json().get("files", {})
        file_obj = files.get(fn)
        if not file_obj:
            return {"text": "", "updated_utc": None}
        content = file_obj.get("content", "")
        try:
            return json.loads(content)
        except Exception:
            return {"text": content, "updated_utc": None}
    except Exception:
        return {"text": "", "updated_utc": None}

def save_gamet_to_gist(text: str) -> Tuple[bool, str]:
    token, gid, fn = _get_gamet_secrets()
    if not all([token, gid, fn]):
        return False, "Faltam segredos do GAMET (TOKEN/ID/FILENAME)."
    try:
        payload = {"updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), "text": (text or "").strip()}
        body = {"files": {fn: {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}}
        r = requests.patch(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            json=body, timeout=12,
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, "GAMET guardado no Gist."
    except Exception as e:
        return False, f"Erro a gravar GAMET no Gist: {e}"

# NOTAMs (apenas editor/Gist; não entram no Detailed)

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
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
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

def save_notams_to_gist(new_map: Dict[str, List[str]]) -> Tuple[bool, str]:
    if not notam_gist_config_ok():
        return False, "Segredos NOTAM_GIST_* em falta."
    try:
        token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
        gid   = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
        fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
        payload = {
            "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
            "map": {k: [s for s in v if str(s).strip()] for k, v in new_map.items()},
        }
        body = {"files": {fn: {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}}
        r = requests.patch(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            json=body, timeout=12,
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, "NOTAMs guardados no Gist."
    except Exception as e:
        return False, f"Erro a gravar no Gist: {e}"

# ---------- Gist helpers: SIGMET ----------

def _get_sigmet_secrets() -> Tuple[str, str, str]:
    token = (st.secrets.get("SIGMET_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("SIGMET_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("SIGMET_GIST_FILENAME") or "").strip()
    return token, gid, fn

def sigmet_gist_config_ok() -> bool:
    token, gid, fn = _get_sigmet_secrets()
    return all([token, gid, fn])

@st.cache_data(ttl=90)
def load_sigmet_from_gist() -> Dict[str, Any]:
    if not sigmet_gist_config_ok():
        return {"text": "", "updated_utc": None}
    try:
        token, gid, fn = _get_sigmet_secrets()
        r = requests.get(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            timeout=12,
        )
        r.raise_for_status()
        files = r.json().get("files", {})
        file_obj = files.get(fn)
        if not file_obj:
            return {"text": "", "updated_utc": None}
        content = file_obj.get("content", "")
        try:
            return json.loads(content)
        except Exception:
            return {"text": content, "updated_utc": None}
    except Exception:
        return {"text": "", "updated_utc": None}

def save_sigmet_to_gist(text: str) -> Tuple[bool, str]:
    token, gid, fn = _get_sigmet_secrets()
    if not all([token, gid, fn]):
        return False, "Faltam segredos do SIGMET (TOKEN/ID/FILENAME)."
    try:
        payload = {"updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), "text": (text or "").strip()}
        body = {"files": {fn: {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}}
        r = requests.patch(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            json=body, timeout=12,
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, "SIGMET guardado no Gist."
    except Exception as e:
        return False, f"Erro a gravar SIGMET no Gist: {e}"

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
                {"role": "system", "content": prompt_system},
                {"role": "user",   "content": prompt_user},
            ],
            max_tokens=max_tokens,
            temperature=0.15,
        )
        content = (r2.choices[0].message.content or "").strip()
        return ascii_safe(content) if content else ""
    except Exception as e2:
        return ascii_safe(f"Analise indisponivel (erro IA: {e2})")

# ---------- Prompts (PT-PT) ----------

def analyze_chart_pt(kind: str, img_b64: str, filename_hint: str = "") -> str:
    """Analisa chart com 5 blocos curtos e objetivos, usando apenas a imagem."""
    try:
        model_name = st.secrets.get("OPENAI_MODEL_VISION", "gpt-4o").strip() or "gpt-4o"
    except Exception:
        model_name = "gpt-4o"
    sys = (
        "Es meteorologista aeronautico senior. Escreve em PT-PT, conciso mas rigoroso, "
        "em texto corrido (sem listas), com 5 blocos curtos e objetivos, usando apenas informacao visivel no chart:\n"
        "1) Quadro sinoptico — centros/gradientes, jatos (eixo/FL/isotacas), frentes (tipo/movimento), validades.\n"
        "2) Portugal continental — vento SFC/altitudes usuais, visibilidade/tecto, precipitacao/fase, nebulosidade com bases/tops, 0°C, gelo/turbulencia/cisalhamento, CB/TCU.\n"
        "3) Alentejo (incl. LPSO) — recomendacoes operacionais (altitudes/rotas a preferir/evitar, riscos).\n"
        "4) Simbologia — identifica e explica marcadores-chave e limites.\n"
        "5) Nuvens & Fenomenos — cadeia tipica por frente, gelo/turbulencia, impacto VFR/IFR. Se algo nao estiver no chart, escreve 'nao indicado'."
    )
    user_txt = f"Tipo de chart: {kind}. Ficheiro: {filename_hint}"
    if not (st.secrets.get("OPENAI_API_KEY") or "").strip():
        return "Analise de imagem desativada (OPENAI_API_KEY em falta)."
    try:
        r = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": sys},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_txt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    ],
                },
            ],
            max_tokens=1100,
            temperature=0.1,
        )
        out = (r.choices[0].message.content or "").strip()
        return ascii_safe(out) if out else "Analise indisponivel."
    except Exception as e:
        return ascii_safe(f"Analise indisponivel (erro IA: {e})")


def analyze_metar_taf_pt(icao: str, metar: str, taf: str) -> str:
    """Interpretação curta/telegráfica sem 'palha'."""
    sys = (
        "Es meteorologista aeronautico senior. PT-PT. Responde de forma telegráfica e concisa (max ~8 linhas). "
        "Usa apenas informacao presente nos reports, sem glossarios nem explicacoes didaticas. "
        "Inclui: hora, vento (dir/vel/raj), visibilidade, fenomenos, nuvens com alturas (e oktas entre parenteses), T/Td, QNH. "
        "No TAF: janela de validade e cada segmento BECMG/TEMPO/PROB com efeito pratico (1 frase por segmento). "
        "Se algo nao existir, escreve 'nao presente'. Termina com 'Impacto' (VFR/IFR + 2-3 riscos)."
    )
    user = f"Aerodromo {icao}\n\nMETAR (RAW):\n{metar}\n\nTAF (RAW):\n{taf}"
    return gpt_text(prompt_system=sys, prompt_user=user, max_tokens=700)


def analyze_sigmet_pt(sigmet_text: str) -> str:
    if not sigmet_text.strip():
        return ""
    sys = (
        "Es meteorologista aeronautico senior. PT-PT. Interpreta o SIGMET LPPC: fenomeno, area/limites, niveis/FL, "
        "validade, movimento/intensidade e impacto operacional (VFR/IFR). Explica abreviaturas essenciais apenas se usadas."
    )
    return gpt_text(prompt_system=sys, prompt_user=sigmet_text, max_tokens=700)


def analyze_gamet_pt(gamet_text: str) -> str:
    if not gamet_text.strip():
        return ""
    lat, lon = LPSO_ARP
    sys = (
        "Es meteorologista aeronautico senior. PT-PT. Interpreta o GAMET LPPC: fenomenos, niveis/camadas, areas, "
        "validade/horas e PROB/TEMPO/BECMG. Indica claramente se as coordenadas/areas abrangem LPSO "
        f"(≈ {lat:.6f}, {lon:.6f}). Conclui com: 'Abrange LPSO' / 'Nao abrange LPSO' / 'Indeterminado'."
    )
    user = f"Texto integral do GAMET:\n{gamet_text}\n\nReferencia: LPSO."
    return gpt_text(prompt_system=sys, prompt_user=user, max_tokens=1000)

# ---------- PDF helpers ----------
PASTEL = (90, 127, 179)  # azul suave


def draw_header(pdf: FPDF, text: str) -> None:
    pdf.set_draw_color(229, 231, 235)
    pdf.set_line_width(0.3)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, ascii_safe(text), ln=True, align="C", border="B")


def place_image_full(pdf: FPDF, img_png: io.BytesIO, max_h_pad: int = 58) -> None:
    max_w = pdf.w - 22
    max_h = pdf.h - max_h_pad
    img = Image.open(img_png)
    iw, ih = img.size
    r = min(max_w / iw, max_h / ih)
    w, h = int(iw * r), int(ih * r)
    x = (pdf.w - w) // 2
    y = pdf.get_y() + 6
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp, format="PNG")
        path = tmp.name
    pdf.image(path, x=x, y=y, w=w, h=h)
    os.remove(path)
    pdf.ln(h + 10)


def image_bytes_to_pdf_bytes(title: str, img_png: io.BytesIO, orientation: str = "P") -> bytes:
    """Cria um PDF de 1 página com uma imagem (para poder inserir via PyMuPDF)."""
    doc = FPDF(orientation=orientation, unit="mm", format="A4")
    doc.add_page(orientation=orientation)
    draw_header(doc, title)
    place_image_full(doc, img_png, max_h_pad=58)
    data = doc.output(dest="S")
    return data if isinstance(data, (bytes, bytearray)) else data.encode("latin-1")


def fpdf_to_bytes(doc: FPDF) -> bytes:
    data = doc.output(dest="S")
    return data if isinstance(data, (bytes, bytearray)) else data.encode("latin-1")

# ---------- PDF classes ----------
class DetailedPDF(FPDF):
    def header(self) -> None: ...
    def footer(self) -> None: ...

    def metar_taf_block(self, analyses: List[Tuple[str, str, str, str]]) -> None:
        self.add_page(orientation="P")
        draw_header(self, "METAR / TAF — Interpretacao (PT, resumida)")
        self.set_font("Helvetica", "", 12)
        self.ln(2)
        for icao, metar_raw, taf_raw, analysis in analyses:
            self.set_font("Helvetica", "B", 13)
            self.cell(0, 8, ascii_safe(icao), ln=True)
            if metar_raw:
                self.set_font("Helvetica", "B", 12)
                self.cell(0, 7, "METAR (RAW):", ln=True)
                self.set_font("Helvetica", "", 12)
                self.multi_cell(0, 7, ascii_safe(metar_raw))
                self.ln(1)
            if taf_raw:
                self.set_font("Helvetica", "B", 12)
                self.cell(0, 7, "TAF (RAW):", ln=True)
                self.set_font("Helvetica", "", 12)
                self.multi_cell(0, 7, ascii_safe(taf_raw))
                self.ln(1)
            self.set_font("Helvetica", "B", 12)
            self.cell(0, 7, "Interpretacao:", ln=True)
            self.set_font("Helvetica", "", 12)
            self.multi_cell(0, 7, ascii_safe(analysis or "Sem interpretacao."))
            self.ln(2)

    def sigmet_block(self, sigmet_text: str, analysis_pt: str) -> None:
        if not sigmet_text.strip():
            return
        self.add_page(orientation="P")
        draw_header(self, "SIGMET (LPPC) — Interpretacao (PT)")
        self.ln(2)
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, "Texto (RAW):", ln=True)
        self.set_font("Helvetica", "", 12)
        self.multi_cell(0, 7, ascii_safe(sigmet_text))
        self.ln(4)
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, "Interpretacao:", ln=True)
        self.set_font("Helvetica", "", 12)
        self.multi_cell(0, 7, ascii_safe(analysis_pt))

    def gamet_block(self, gamet_text: str, analysis_pt: str) -> None:
        if not gamet_text.strip():
            return
        self.add_page(orientation="P")
        draw_header(self, "GAMET — Interpretacao (PT)")
        self.ln(2)
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, "Texto (RAW):", ln=True)
        self.set_font("Helvetica", "", 12)
        self.multi_cell(0, 7, ascii_safe(gamet_text))
        self.ln(4)
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 8, "Interpretacao:", ln=True)
        self.set_font("Helvetica", "", 12)
        self.multi_cell(0, 7, ascii_safe(analysis_pt))

    def chart_block(self, title: str, subtitle: str, img_png: io.BytesIO, analysis_pt: str) -> None:
        self.add_page(orientation="P")
        draw_header(self, ascii_safe(title))
        if subtitle:
            self.set_font("Helvetica", "I", 12)
            self.cell(0, 9, ascii_safe(subtitle), ln=True, align="C")
        max_w = self.w - 22
        max_h = (self.h // 2) - 18
        img = Image.open(img_png)
        iw, ih = img.size
        r = min(max_w / iw, max_h / ih)
        w, h = int(iw * r), int(ih * r)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            path = tmp.name
        self.image(path, x=(self.w - w)//2, y=self.get_y() + 6, w=w, h=h)
        os.remove(path)
        self.ln(h + 12)
        self.set_font("Helvetica", "", 12)
        self.multi_cell(0, 7, ascii_safe(analysis_pt or " "))

    def glossary_page(self) -> None:
        self.add_page(orientation="P")
        draw_header(self, "Glossario — Simbologia, Nuvens & Fenomenos")
        self.set_font("Helvetica", "", 12)
        txt = (
            "Cobertura (oktas): FEW 1–2; SCT 3–4; BKN 5–7; OVC 8.\n"
            "Frentes: fria (triangulos); quente (semicirculos); oclusao (mix); estacionaria (pouco movimento).\n"
            "Sequencias tipicas: quente → CI/CS/AS→NS; fria rapida → AC/TCU/CB; oclusao → AS/NS+CB/TCU.\n"
            "SIGWX: jatos, turbulencia (serrilhado/sombreado), gelo (ICE), CB/TCU com tops/bases, EMBD/OCNL/FRQ.\n"
            "Abreviaturas: BECMG/TEMPO/PROB, VRB, G, CAVOK, RVR, QNH/QFE, NSC/NCD, TOP/BASE, BTN, MOV.\n"
        )
        self.multi_cell(0, 7, ascii_safe(txt))


class FinalBriefPDF(FPDF):
    def header(self) -> None: ...
    def footer(self) -> None: ...

    def cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc) -> None:
        self.add_page(orientation="L")
        self.set_xy(0, 36)
        self.set_font("Helvetica", "B", 28)
        self.cell(0, 14, "Briefing", ln=True, align="C")
        self.ln(2)
        self.set_font("Helvetica", "", 13)
        self.cell(0, 8, ascii_safe(f"Mission: {mission_no}"), ln=True, align="C")
        if pilot or aircraft or callsign or reg:
            self.cell(0, 8, ascii_safe(f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}"), ln=True, align="C")
        if date_str or time_utc:
            self.cell(0, 8, ascii_safe(f"Date: {date_str}   UTC: {time_utc}"), ln=True, align="C")
        # Links (apenas Weather e NOTAMs)
        self.ln(4)
        self.set_text_color(*PASTEL)
        self.set_font("Helvetica", "", 12)
        self.cell(0, 8, ascii_safe("Weather page: ") + APP_WEATHER_URL, ln=True, align="C", link=APP_WEATHER_URL)
        self.cell(0, 8, ascii_safe("NOTAMs page: ") + APP_NOTAMS_URL, ln=True, align="C", link=APP_NOTAMS_URL)
        self.set_text_color(0, 0, 0)

    def flightplan_image_portrait(self, title: str, img_png: io.BytesIO) -> None:
        self.add_page(orientation="P")
        draw_header(self, ascii_safe(title))
        place_image_full(self, img_png)

    def charts_only(self, charts: List[Tuple[str, str, io.BytesIO]]) -> None:
        for (title, subtitle, img_png) in charts:
            self.add_page(orientation="L")
            draw_header(self, ascii_safe(title))
            if subtitle:
                self.set_font("Helvetica", "I", 12)
                self.cell(0, 9, ascii_safe(subtitle), ln=True, align="C")
            place_image_full(self, img_png)


# ---------- UI: header + botões "outras páginas" ----------
st.markdown(
    f'''<div class="app-top"><div class="app-title">Briefings</div>
    <span class="btnbar">
      <a href="{APP_WEATHER_URL}" target="_blank">Weather</a>
      <a href="{APP_NOTAMS_URL}" target="_blank">NOTAMs</a>
      <a href="{APP_VFRMAP_URL}" target="_blank">VFR Map</a>
      <a href="{APP_MNB_URL}" target="_blank">Mass & Balance</a>
    </span></div>''',
    unsafe_allow_html=True,
)

# ---------- Abas ----------
tab_mission, tab_notams, tab_sigmet_gamet, tab_charts, tab_pairs, tab_generate = st.tabs(
    ["Missão", "NOTAMs", "SIGMET & GAMET", "Charts", "Navlog ↔ VFR (Rotas)", "Gerar PDFs"]
)

# ---------- Missão ----------
with tab_mission:
    st.markdown("### Dados da Missão")
    colA, colB, colC = st.columns(3)
    with colA:
        pilot = st.text_input("Pilot name", "Alexandre Moiteiro")
        callsign = st.text_input("Mission callsign", "")
    with colB:
        aircraft_type = st.text_input("Aircraft type", "Tecnam P2008")
        registration = st.text_input("Registration", "CS-XXX")
    with colC:
        mission_no = st.text_input("Mission number", "")
        flight_date = st.date_input("Flight date")
        time_utc = st.text_input("UTC time", "")

    st.markdown("#### Aerodromes")
    c1, c2 = st.columns(2)
    with c1:
        icaos_metar_str = st.text_input(
            "ICAO list for METAR/TAF (comma / space / newline)", value="LPPT LPBJ LEBZ"
        )
        icaos_metar = parse_icaos(icaos_metar_str)
    with c2:
        icaos_notam_str = st.text_input(
            "ICAO list for NOTAMs (comma / space / newline)", value="LPPC(Enroute) LPSO LPCB LPEV"
        )
        icaos_notam = parse_icaos(icaos_notam_str)

# ---------- NOTAMs ----------
with tab_notams:
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
    for i, icao in enumerate(icaos_notam if 'icaos_notam' in locals() else []):
        with edit_cols[i % 3]:
            initial_text = "\n\n".join(existing_map.get(icao, [])) if existing_map.get(icao) else ""
            editors_notam[icao] = st.text_area(
                f"{icao} — NOTAMs",
                value=initial_text,
                placeholder=(
                    "Ex.: AERODROME BEACON ONLY FLASH-GREEN LIGHT OPERATIVE.\n"
                    "FROM: 29th Jul 2025 15:10 TO: 29th Sep 2025 18:18 EST\n\nOutro NOTAM aqui..."
                ),
                key=f"ed_notam_{icao}",
                height=160,
            )

    # Guardar (sempre merge com o existente)
    if st.button("Guardar NOTAMs no Gist"):
        new_map: Dict[str, List[str]] = {}
        new_map.update(existing_map)  # merge
        for icao in (icaos_notam if 'icaos_notam' in locals() else []):
            new_map[icao] = parse_block_to_list(editors_notam.get(icao, ""))
        ok, msg = save_notams_to_gist(new_map)
        if ok:
            st.success(msg)
            st.cache_data.clear()
        else:
            st.error(msg)

# ---------- SIGMET & GAMET ----------
with tab_sigmet_gamet:
    st.markdown("### SIGMET & GAMET")
    # GAMET
    _gamet_obj = load_gamet_from_gist()
    _gamet_initial = (_gamet_obj.get("text") or "").strip()

    gamet_text = st.text_area(
        "GAMET — texto integral",
        value=_gamet_initial,
        height=220,
        key="gamet_editor",
        placeholder="Ex.: LPPC FIR GAMET VALID 12/06Z-12/12Z\n...",
    )
    if st.button("Guardar GAMET no Gist"):
        ok, msg = save_gamet_to_gist(gamet_text)
        if ok:
            st.success(msg)
            try:
                st.cache_data.clear()
            except Exception:
                pass
        else:
            st.error(msg)

    st.divider()

    # SIGMET
    _sigmet_obj = load_sigmet_from_gist()
    _sigmet_initial = (_sigmet_obj.get("text") or "").strip()
    sigmet_text = st.text_area(
        "SIGMET (LPPC) — texto integral",
        value=_sigmet_initial,
        height=160,
        key="sigmet_editor",
        placeholder=(
            "Ex.: LPPC SIGMET 2 VALID 12/09Z-12/13Z LPPC-\nSEV TURB FCST BTN FL080/FL200 MOV NE 20KT ..."
        ),
    )
    if st.button("Guardar SIGMET no Gist"):
        ok, msg = save_sigmet_to_gist(sigmet_text)
        if ok:
            st.success(msg)
            try:
                st.cache_data.clear()
            except Exception:
                pass
        else:
            st.error(msg)

# ---------- Charts ----------
with tab_charts:
    st.markdown("### Charts (SIGWX / SPC / Wind & Temp / Other)")
    st.caption("Aceita PDF/PNG/JPG/JPEG/GIF (para PDF lemos a 1.ª página).")
    use_ai_for_charts = st.toggle("Analisar charts com IA", value=True, help="Marcado por omissao")
    preview_w = st.slider("Largura da pré-visualização (px)", min_value=240, max_value=640, value=420, step=10)
    uploads = st.file_uploader(
        "Upload charts", type=["pdf", "png", "jpg", "jpeg", "gif"], accept_multiple_files=True, label_visibility="collapsed"
    )

    def _base_title_for_kind(k: str) -> str:
        return {
            "SIGWX": "Significant Weather Chart (SIGWX)",
            "SPC": "Surface Pressure Chart (SPC)",
            "Wind & Temp": "Wind and Temperature Chart",
        }.get(k, "Weather Chart")

    charts: List[Dict[str, Any]] = []
    if uploads:
        for idx, f in enumerate(uploads):
            raw = f.read()
            mime = f.type or ""
            img_png = ensure_png_from_bytes(raw, mime)
            name = f.name or "(sem nome)"
            col_img, col_meta = st.columns([0.35, 0.65])
            with col_img:
                try:
                    st.image(img_png.getvalue(), caption=name, width=preview_w)
                except Exception:
                    st.write(name)
            with col_meta:
                kind = st.selectbox(
                    f"Tipo do chart #{idx+1}", ["SIGWX", "SPC", "Wind & Temp", "Other"], index=0, key=f"kind_{idx}"
                )
                title_default = _base_title_for_kind(kind)
                title = st.text_input("Título", value=title_default, key=f"title_{idx}")
                subtitle = st.text_input("Subtítulo (opcional)", value="", key=f"subtitle_{idx}")
                order_val = st.number_input(
                    "Ordem", min_value=1, max_value=len(uploads) + 10, value=idx + 1, step=1, key=f"ord_{idx}"
                )
            charts.append(
                {"kind": kind, "title": title, "subtitle": subtitle, "img_png": img_png, "order": order_val, "filename": name}
            )

# ---------- Navlog ↔ VFR (pares por ROTA) ----------
with tab_pairs:
    st.markdown("### Emparelhamento Navlog ↔ VFR por ROTA")
    st.caption("Para cada ROTA (ex.: LPSO-LPCB, LPSO-LPEV) carrega um Navlog e o respetivo mapa VFR. Aceita PDF/PNG/JPG/JPEG/GIF.")

    num_pairs = st.number_input("Número de pares (Rotas)", min_value=0, max_value=8, value=0, step=1)
    pairs: List[Dict[str, Any]] = []
    for i in range(int(num_pairs)):
        with st.expander(f"Rota #{i+1}", expanded=False):
            route = st.text_input(f"ROTA (ex.: LPSO-LPCB)", key=f"pair_route_{i}").upper().strip()
            c1, c2 = st.columns(2)
            with c1:
                nav_file = st.file_uploader(
                    f"Navlog ({route or 'ROTA'})", type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"pair_nav_{i}"
                )
            with c2:
                vfr_file = st.file_uploader(
                    f"VFR Map ({route or 'ROTA'})", type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"pair_vfr_{i}"
                )
            pairs.append({"route": route, "nav": nav_file, "vfr": vfr_file})

# ---------- Gerar PDFs ----------
with tab_generate:
    st.markdown("### PDFs")
    col_pdfs = st.columns(2)
    with col_pdfs[0]:
        gen_det = st.button("Generate Detailed (PT)")
    with col_pdfs[1]:
        gen_final = st.button("Generate Final Briefing (EN)")

# ---------- Detailed (PT) ----------
if 'gen_det' in locals() and gen_det:
    # Aeródromos METAR/TAF da aba Missão
    icaos_metar_local = locals().get("icaos_metar", [])
    # Textos SIGMET/GAMET
    sigmet_text_local = locals().get("sigmet_text", "")
    _sigmet_initial_local = locals().get("_sigmet_initial", "")
    gamet_text_local = locals().get("gamet_text", "")
    _gamet_initial_local = locals().get("_gamet_initial", "")

    # CHARTS PRIMEIRO ("Weather"=charts)
    det_pdf = DetailedPDF()
    charts_local: List[Dict[str, Any]] = locals().get("charts", [])
    if charts_local:
        grouped: Dict[str, List[Dict[str, Any]]] = {"SPC": [], "SIGWX": [], "Wind & Temp": [], "Other": []}
        for c in charts_local:
            grouped.setdefault(c["kind"], []).append(c)
        for k in list(grouped.keys()):
            grouped[k] = sorted(grouped[k], key=_chart_sort_key)
        for kind in ["SPC", "SIGWX", "Wind & Temp", "Other"]:
            for ch in grouped.get(kind, []):
                title, subtitle, img_png, fname = ch["title"], ch["subtitle"], ch["img_png"], ch.get("filename", "")
                analysis_txt = ""
                if locals().get("use_ai_for_charts", False):
                    try:
                        analysis_txt = analyze_chart_pt(
                            kind, base64.b64encode(img_png.getvalue()).decode("utf-8"), filename_hint=fname
                        )
                    except Exception:
                        analysis_txt = "Analise indisponivel."
                det_pdf.chart_block(title, subtitle, img_png, analysis_txt)

    # METAR/TAF (RAW + interpretação curta)
    if icaos_metar_local:
        metar_analyses: List[Tuple[str, str, str, str]] = []
        for icao in icaos_metar_local:
            metar_raw = fetch_metar_now(icao) or ""
            taf_raw = fetch_taf_now(icao) or ""
            analysis = (
                analyze_metar_taf_pt(icao, metar_raw, taf_raw)
                if (metar_raw or taf_raw)
                else "Sem METAR/TAF disponiveis neste momento."
            )
            metar_analyses.append((icao, metar_raw, taf_raw, analysis))
        det_pdf.metar_taf_block(metar_analyses)

    # SIGMET
    sigmet_for_pdf = (sigmet_text_local or _sigmet_initial_local or "").strip()
    if sigmet_for_pdf:
        sigmet_analysis = analyze_sigmet_pt(sigmet_for_pdf)
        det_pdf.sigmet_block(sigmet_for_pdf, sigmet_analysis)

    # GAMET
    gamet_for_pdf = (gamet_text_local or _gamet_initial_local or "").strip()
    if gamet_for_pdf:
        gamet_analysis = analyze_gamet_pt(gamet_for_pdf)
        det_pdf.gamet_block(gamet_for_pdf, gamet_analysis)

    # Glossário
    det_pdf.glossary_page()

    det_name = f"Briefing Detalhado - Missao {locals().get('mission_no') or 'X'}.pdf"
    det_pdf.output(det_name)
    with open(det_name, "rb") as f:
        st.download_button(
            "Download Detailed (PT)", data=f.read(), file_name=det_name, mime="application/pdf", use_container_width=True
        )

# ---------- Final Briefing (EN) ----------
if 'gen_final' in locals() and gen_final:
    fb = FinalBriefPDF()
    fb.cover(
        mission_no=locals().get("mission_no", ""),
        pilot=locals().get("pilot", ""),
        aircraft=locals().get("aircraft_type", ""),
        callsign=locals().get("callsign", ""),
        reg=locals().get("registration", ""),
        date_str=str(locals().get("flight_date", "")),
        time_utc=locals().get("time_utc", ""),
    )

    # CHARTS FIRST (Weather=charts). Sem páginas só de títulos.
    charts_local: List[Dict[str, Any]] = locals().get("charts", [])
    if charts_local:
        ordered = [(c["title"], c["subtitle"], c["img_png"]) for c in sorted(charts_local, key=_chart_sort_key)]
        fb.charts_only(ordered)

    # Flight Plan (se imagem). PDF será inserido mais abaixo via merge.
    fp_upload = locals().get("fp_upload", None)
    fp_img_png: Optional[io.BytesIO] = None
    fp_pdf_bytes: Optional[bytes] = None
    fp_is_pdf = False
    if fp_upload:
        raw = fp_upload.read()
        if (fp_upload.type or "").lower() == "application/pdf":
            fp_pdf_bytes = raw
            fp_is_pdf = True
        else:
            fp_img_png = ensure_png_from_bytes(raw, fp_upload.type or "")
            fb.flightplan_image_portrait("Flight Plan", fp_img_png)

    # Exporta base
    fb_bytes: bytes = fpdf_to_bytes(fb)
    final_bytes = fb_bytes

    # Merge com: Flight Plan PDF -> Pares Navlog/VFR por ROTA -> (opcional) M&B PDF
    nav_pairs: List[Dict[str, Any]] = locals().get("pairs", [])
    mb_upload = locals().get("mb_upload", None)

    try:
        main = fitz.open(stream=fb_bytes, filetype="pdf")
        insert_pos = main.page_count

        # 1) Flight Plan PDF (se existir)
        if fp_is_pdf and fp_pdf_bytes:
            try:
                fp_doc = fitz.open(stream=fp_pdf_bytes, filetype="pdf")
                main.insert_pdf(fp_doc, start_at=insert_pos)
                insert_pos += fp_doc.page_count
                fp_doc.close()
            except Exception:
                pass

        # 2) Pares Navlog↔VFR por ROTA — sem páginas "apenas título"
        for p in (nav_pairs or []):
            route = (p.get("route") or "").upper()

            # Navlog
            nv = p.get("nav")
            if nv is not None:
                raw = nv.read()
                if (nv.type or "").lower() == "application/pdf":
                    try:
                        nv_doc = fitz.open(stream=raw, filetype="pdf")
                        main.insert_pdf(nv_doc, start_at=insert_pos)
                        insert_pos += nv_doc.page_count
                        nv_doc.close()
                    except Exception:
                        pass
                else:
                    img_png = ensure_png_from_bytes(raw, nv.type or "")
                    nv_bytes = image_bytes_to_pdf_bytes(f"Navlog — {route or 'ROTA'}", img_png, orientation="P")
                    nv_doc = fitz.open(stream=nv_bytes, filetype="pdf")
                    main.insert_pdf(nv_doc, start_at=insert_pos)
                    insert_pos += nv_doc.page_count
                    nv_doc.close()

            # VFR
            vf = p.get("vfr")
            if vf is not None:
                raw = vf.read()
                if (vf.type or "").lower() == "application/pdf":
                    try:
                        vf_doc = fitz.open(stream=raw, filetype="pdf")
                        main.insert_pdf(vf_doc, start_at=insert_pos)
                        insert_pos += vf_doc.page_count
                        vf_doc.close()
                    except Exception:
                        pass
                else:
                    img_png = ensure_png_from_bytes(raw, vf.type or "")
                    vf_bytes = image_bytes_to_pdf_bytes(f"VFR Map — {route or 'ROTA'}", img_png, orientation="L")
                    vf_doc = fitz.open(stream=vf_bytes, filetype="pdf")
                    main.insert_pdf(vf_doc, start_at=insert_pos)
                    insert_pos += vf_doc.page_count
                    vf_doc.close()

        # 3) Mass & Balance PDF (opcional)
        if mb_upload is not None:
            try:
                mb_bytes = mb_upload.getvalue() if hasattr(mb_upload, "getvalue") else mb_upload.read()
                mb_doc = fitz.open(stream=mb_bytes, filetype="pdf")
                if mb_doc.page_count > 0:
                    main.insert_pdf(mb_doc, start_at=insert_pos)
                    insert_pos += mb_doc.page_count
                mb_doc.close()
            except Exception:
                pass

        final_bytes = main.tobytes()
        main.close()
    except Exception:
        pass

    final_name = f"Briefing - Missao {locals().get('mission_no') or 'X'}.pdf"
    st.download_button(
        "Download Final Briefing (EN)", data=final_bytes, file_name=final_name, mime="application/pdf", use_container_width=True
    )

