# app.py — Briefings com editor de NOTAMs, GAMET e SIGMET (via Gist) + METAR/TAF + Charts + PDFs + Pares Navlog↔VFR
from typing import Dict, Any, List, Tuple, Optional
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
:root { --muted:#6b7280; --line:#e5e7eb; --pastel:#5a7fb3; }
.app-title { font-size: 2.2rem; font-weight: 800; margin: 0 0 .25rem; }
.small { font-size:.92rem; color:var(--muted); }
.monos{font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap}
hr{border:none;border-top:1px solid var(--line);margin:12px 0}
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }
.toolbar a { margin-right: 10px; }
.section-card{border:1px solid var(--line); border-radius:12px; padding:12px 14px; background:#fff}
</style>
""", unsafe_allow_html=True)

# ---------- OpenAI client ----------
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# ---------- Constantes úteis ----------
LPSO_ARP = (39.211667, -8.057778)  # LPSO (ARP)
PASTEL = (90, 127, 179)  # azul suave

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
    # mantém tokens como "LPPC(ENROUTE)" intactos
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
        # fallback: tenta devolver original se for imagem
        try:
            Image.open(io.BytesIO(file_bytes))
            return io.BytesIO(file_bytes)
        except Exception:
            ph = Image.new("RGB", (800, 600), (245, 246, 248))
            bio = io.BytesIO()
            ph.save(bio, format="PNG")
            bio.seek(0)
            return bio

# ---------- Texto auxiliar / charts ----------
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

# ---------- Gist helpers ----------
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
            timeout=12
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
            json=body, timeout=12
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, "GAMET guardado no Gist."
    except Exception as e:
        return False, f"Erro a gravar GAMET no Gist: {e}"

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
            timeout=10
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

def save_notams_to_gist(new_part: Dict[str, List[str]]) -> Tuple[bool, str]:
    """Atualiza apenas os ICAOs editados, preservando os restantes no Gist."""
    if not notam_gist_config_ok():
        return False, "Segredos NOTAM_GIST_* em falta."
    try:
        # carrega estado atual
        current = load_notams_from_gist()
        base_map: Dict[str, List[str]] = current.get("map") or {}

        # aplica alterações apenas às chaves fornecidas
        for k, v in new_part.items():
            base_map[k] = [s for s in v if str(s).strip()]

        token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
        gid   = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
        fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()

        payload = {
            "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
            "map": base_map
        }
        body = {"files": {fn: {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}}
        r = requests.patch(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            json=body, timeout=12
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, "NOTAMs atualizados no Gist."
    except Exception as e:
        return False, f"Erro a gravar no Gist: {e}"

# ---------- SIGMET helpers ----------
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
            timeout=12
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
            json=body, timeout=12
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, "SIGMET guardado no Gist."
    except Exception as e:
        return False, f"Erro a gravar SIGMET no Gist: {e}"

# ---------- GPT wrapper & Prompts ----------
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
            temperature=0.15
        )
        content = (r2.choices[0].message.content or "").strip()
        return ascii_safe(content) if content else ""
    except Exception as e2:
        return ascii_safe(f"Analise indisponivel (erro IA: {e2})")

def analyze_chart_pt(kind: str, img_b64: str, filename_hint: str = "") -> str:
    # versão reforçada com bloco "Nuvens & Fenómenos" detalhado
    try:
        model_name = st.secrets.get("OPENAI_MODEL_VISION", "gpt-4o").strip() or "gpt-4o"
    except Exception:
        model_name = "gpt-4o"
    sys = (
        "Es meteorologista aeronautico senior. Escreve em PT-PT, conciso e rigoroso, texto corrido (sem listas), com 5 blocos baseados APENAS no chart:\n"
        "1) Quadro sinoptico — centros/gradientes (isobaras/isotermas/isotacas), jatos (eixo, FL, isotacas), frentes (tipo e deslocacao), validades tal como inscritas.\n"
        "2) Portugal continental — litoral/N/C/S: vento (SFC e niveis VFR/IFR), visibilidade/tecto, precipitacao (fase/intensidade), nebulosidade (FEW/SCT/BKN/OVC com bases/tops), 0°C/nivel de congelacao, gelo (lev/mod/sev; camada), turbulencia (lev/mod/sev; camada), cisalhamento e CB/TCU.\n"
        "3) Alentejo (incl. LPSO) — recomendacoes operacionais (altitudes/rotas a preferir/evitar, riscos e alternantes).\n"
        "4) Simbologia — identifica e explica todos os simbolos/linhas/setas/barbulas/etiquetas (incl. TOP/BASE, sombreados, limites, anotações).\n"
        "5) Nuvens & Fenomenos — para cada frente no chart (fria/quente/oclusao/estacionaria) descreve cadeia de nuvens (CI/CS/AC/AS/NS/SC/ST/TCU/CB) e mecanismo (overrunning/levantamento frontal/oclusao fria-quente), precipitacao e perfil termico, gelo (rime/clear/misto; FL provavel), turbulencia (convectiva/orografica/de jato), cisalhamento (baixa/média/alta), e impacto VFR/IFR com acoes praticas. Quando algo nao estiver no chart, escreve 'nao indicado'."
    )
    user_txt = f"Tipo de chart: {kind}. Ficheiro: {filename_hint}"
    if not (st.secrets.get("OPENAI_API_KEY") or "").strip():
        return "Analise de imagem desativada (OPENAI_API_KEY em falta)."
    try:
        r = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": [
                    {"type": "text", "text": user_txt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
                ]},
            ],
            max_tokens=1300,
            temperature=0.1
        )
        out = (r.choices[0].message.content or "").strip()
        return ascii_safe(out) if out else "Analise indisponivel."
    except Exception as e:
        return ascii_safe(f"Analise indisponivel (erro IA: {e})")

def analyze_metar_taf_pt(icao: str, metar: str, taf: str) -> str:
    sys = (
        "Es meteorologista aeronautico senior. Em PT-PT e texto corrido, interpreta exaustivamente METAR e TAF, token a token: "
        "inclui (se existirem) COR/AMD, hora, vento/VRB/rajadas, CAVOK, RVR, visibilidade, fenomenos (+/– RA/TS/BR/FG/SG/PL/GR/GS e 'RE'), "
        "nuvens FEW/SCT/BKN/OVC/NSC/NCD com alturas **e equivalencia em oktas** (FEW 1–2, SCT 3–4, BKN 5–7, OVC 8), "
        "T/Td, QNH/QFE, TREND e RMK. No TAF, explica validade, BECMG/TEMPO/PROB e cada segmento. "
        "Quando uma informacao nao estiver presente, escreve 'nao presente'. "
        "Termina com impacto operacional (VFR/IFR, altitudes recomendadas, riscos concretos) e um mini-glossario dos codigos referidos."
    )
    user = f"Aerodromo {icao}\n\nMETAR (RAW):\n{metar}\n\nTAF (RAW):\n{taf}"
    return gpt_text(prompt_system=sys, prompt_user=user, max_tokens=2300)

def analyze_sigmet_pt(sigmet_text: str) -> str:
    if not sigmet_text.strip():
        return ""
    sys = (
        "Es meteorologista aeronautico senior. Em PT-PT, interpreta o SIGMET LPPC: fenomeno, area/limites, niveis/FL, "
        "validade/hora, movimento/intensidade e impacto operacional (VFR/IFR). "
        "Inclui breve explicacao de abreviaturas e notacoes (BTN/TOP/BASE, EMBD/OCNL/FRQ, SEV TURB/ICE, MOV dir/vel)."
    )
    return gpt_text(prompt_system=sys, prompt_user=sigmet_text, max_tokens=1500)

def analyze_gamet_pt(gamet_text: str) -> str:
    if not gamet_text.strip():
        return ""
    lat, lon = LPSO_ARP
    sys = (
        "Es meteorologista aeronautico senior. Em PT-PT, interpreta o GAMET LPPC: fenomenos, niveis/camadas, areas/subdivisoes, "
        "validade/horas e PROB/TEMPO/BECMG, explicando todos os codigos. Indica se as coordenadas/areas abrangem LPSO "
        f"(≈ {lat:.6f}, {lon:.6f}). "
        "Conclui com uma linha clara: 'Abrange LPSO', 'Nao abrange LPSO' ou 'Indeterminado com o texto dado'."
    )
    user = f"Texto integral do GAMET:\n{gamet_text}\n\nReferencia: LPSO."
    return gpt_text(prompt_system=sys, prompt_user=user, max_tokens=2100)

# ---------- PDF helpers ----------
def draw_header(pdf: FPDF, text: str) -> None:
    pdf.set_draw_color(229, 231, 235)
    pdf.set_line_width(0.3)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, ascii_safe(text), ln=True, align="C", border="B")

def place_image_full(pdf: FPDF, png_bytes: io.BytesIO, max_h_pad: int = 58) -> None:
    max_w = pdf.w - 22
    max_h = pdf.h - max_h_pad
    img = Image.open(png_bytes)
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
    doc = FPDF(orientation=orientation, unit="mm", format="A4")
    doc.add_page(orientation=orientation)
    draw_header(doc, title)
    place_image_full(doc, img_png, max_h_pad=58)
    data = doc.output(dest="S")
    return data if isinstance(data, (bytes, bytearray)) else data.encode("latin-1")

def pdf_embed_pdf_pages(pdf: FPDF, pdf_bytes: bytes, title: str, orientation: str = "P", max_pages: Optional[int] = None) -> None:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total = doc.page_count
    pages = range(total) if max_pages is None else range(min(total, max_pages))
    for i in pages:
        page = doc.load_page(i)
        png = page.get_pixmap(dpi=450).tobytes("png")
        img = Image.open(io.BytesIO(png)).convert("RGB")
        bio = io.BytesIO()
        img.save(bio, format="PNG")
        bio.seek(0)
        pdf.add_page(orientation=orientation)
        draw_header(pdf, ascii_safe(title + (f" — p.{i+1}" if total > 1 else "")))
        place_image_full(pdf, bio, max_h_pad=58)
    doc.close()

def fpdf_to_bytes(doc: FPDF) -> bytes:
    data = doc.output(dest="S")
    return data if isinstance(data, (bytes, bytearray)) else data.encode("latin-1")

# ---------- PDF classes ----------
class DetailedPDF(FPDF):
    def header(self) -> None: pass
    def footer(self) -> None: pass

    def section_page(self, title: str) -> None:
        self.add_page(orientation="P")
        self.set_font("Helvetica", "B", 22)
        self.set_text_color(*PASTEL)
        self.cell(0, 16, ascii_safe(title), ln=True, align="C")
        self.set_text_color(0, 0, 0)

    def metar_taf_block(self, analyses: List[Tuple[str, str, str, str]]) -> None:
        self.add_page(orientation="P")
        draw_header(self, "METAR / TAF — Interpretacao (PT)")
        self.set_font("Helvetica", "", 12); self.ln(2)
        for icao, metar_raw, taf_raw, analysis in analyses:
            self.set_font("Helvetica", "B", 13); self.cell(0, 8, ascii_safe(icao), ln=True)
            if metar_raw:
                self.set_font("Helvetica", "B", 12); self.cell(0, 7, "METAR (RAW):", ln=True)
                self.set_font("Helvetica", "", 12); self.multi_cell(0, 7, ascii_safe(metar_raw)); self.ln(2)
            if taf_raw:
                self.set_font("Helvetica", "B", 12); self.cell(0, 7, "TAF (RAW):", ln=True)
                self.set_font("Helvetica", "", 12); self.multi_cell(0, 7, ascii_safe(taf_raw)); self.ln(2)
            self.set_font("Helvetica", "B", 12); self.cell(0, 7, "Interpretacao:", ln=True)
            self.set_font("Helvetica", "", 12); self.multi_cell(0, 7, ascii_safe(analysis or "Sem interpretacao.")); self.ln(3)

    def sigmet_block(self, sigmet_text: str, analysis_pt: str) -> None:
        if not sigmet_text.strip(): return
        self.add_page(orientation="P"); draw_header(self, "SIGMET (LPPC) — Interpretacao (PT)")
        self.ln(2); self.set_font("Helvetica", "B", 12); self.cell(0, 8, "Texto (RAW):", ln=True)
        self.set_font("Helvetica", "", 12); self.multi_cell(0, 7, ascii_safe(sigmet_text)); self.ln(4)
        self.set_font("Helvetica", "B", 12); self.cell(0, 8, "Interpretacao:", ln=True)
        self.set_font("Helvetica", "", 12); self.multi_cell(0, 7, ascii_safe(analysis_pt))

    def gamet_block(self, gamet_text: str, analysis_pt: str) -> None:
        if not gamet_text.strip(): return
        self.add_page(orientation="P"); draw_header(self, "GAMET — Interpretacao (PT)")
        self.ln(2); self.set_font("Helvetica", "B", 12); self.cell(0, 8, "Texto (RAW):", ln=True)
        self.set_font("Helvetica", "", 12); self.multi_cell(0, 7, ascii_safe(gamet_text)); self.ln(4)
        self.set_font("Helvetica", "B", 12); self.cell(0, 8, "Interpretacao:", ln=True)
        self.set_font("Helvetica", "", 12); self.multi_cell(0, 7, ascii_safe(analysis_pt))

    def chart_block(self, title: str, subtitle: str, img_png: io.BytesIO, analysis_pt: str) -> None:
        self.add_page(orientation="P")
        draw_header(self, ascii_safe(title))
        if subtitle:
            self.set_font("Helvetica", "I", 12); self.cell(0, 9, ascii_safe(subtitle), ln=True, align="C")
        max_w = self.w - 22; max_h = (self.h // 2) - 18
        img = Image.open(img_png); iw, ih = img.size
        r = min(max_w / iw, max_h / ih); w, h = int(iw * r), int(ih * r)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG"); path = tmp.name
        self.image(path, x=(self.w - w)//2, y=self.get_y() + 6, w=w, h=h); os.remove(path); self.ln(h + 12)
        self.set_font("Helvetica", "", 12); self.multi_cell(0, 7, ascii_safe(analysis_pt or " "))

    def glossary_page(self) -> None:
        self.add_page(orientation="P")
        draw_header(self, "Glossario — Simbologia, Nuvens & Fenomenos")
        self.set_font("Helvetica", "", 12)
        txt = (
            "Cobertura (oktas): FEW 1–2; SCT 3–4; BKN 5–7; OVC 8.\n"
            "Frentes: fria (triangulos) — ar frio avanca; quente (semicirculos) — quente sobrepoe; "
            "oclusao (triang.+semicirc.) — mistura; estacionaria — pouco movimento.\n"
            "Sequencias tipicas: frente quente → CI/CS/AS→NS (chuva estratiforme, gelo em camadas); "
            "frente fria rapida → AC/TCU/CB (aguaceiros/TS, turbulencia); "
            "oclusao → mix AS/NS e CB/TCU; estacionaria → precipitacao persistente fraca/mod.\n"
            "SIGWX: jatos (eixo+isotacas), areas de turbulencia (serrilhado/sombreado), gelo (ICE), CB/TCU com tops/bases (ex. TOP FL350), "
            "EMBD/OCNL/FRQ (cobertura/concentracao), SH/TS/RA/PL/SG/GR.\n"
            "Abreviaturas: BECMG/TEMPO/PROB, VRB, G (rajadas), CAVOK, RVR, QNH/QFE, NSC/NCD, TOP/BASE, BTN, MOV dir/vel.\n"
        )
        self.multi_cell(0, 7, ascii_safe(txt))

class FinalBriefPDF(FPDF):
    def header(self) -> None: pass
    def footer(self) -> None: pass

    def cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc) -> None:
        self.add_page(orientation="L"); self.set_xy(0, 36)
        self.set_font("Helvetica", "B", 28); self.cell(0, 14, "Briefing", ln=True, align="C")
        self.ln(2); self.set_font("Helvetica", "", 13)
        self.cell(0, 8, ascii_safe(f"Mission: {mission_no}"), ln=True, align="C")
        if pilot or aircraft or callsign or reg:
            self.cell(0, 8, ascii_safe(f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}"), ln=True, align="C")
        if date_str or time_utc:
            self.cell(0, 8, ascii_safe(f"Date: {date_str}   UTC: {time_utc}"), ln=True, align="C")

    def section_title(self, title: str) -> None:
        self.add_page(orientation="P")
        self.set_font("Helvetica", "B", 20)
        self.set_text_color(*PASTEL)
        self.cell(0, 12, ascii_safe(title), ln=True, align="C")
        self.set_text_color(0, 0, 0)

    def flightplan_image_portrait(self, title: str, img_png: io.BytesIO) -> None:
        self.add_page(orientation="P"); draw_header(self, ascii_safe(title)); place_image_full(self, img_png)

    def charts_only(self, charts: List[Tuple[str, str, io.BytesIO]]) -> None:
        for (title, subtitle, img_png) in charts:
            self.add_page(orientation="L"); draw_header(self, ascii_safe(title))
            if subtitle:
                self.set_font("Helvetica", "I", 12); self.cell(0, 9, ascii_safe(subtitle), ln=True, align="C")
            place_image_full(self, img_png)

    def resources_page(self) -> None:
        self.add_page(orientation="P")
        draw_header(self, "Resources / Links")
        self.set_font("Helvetica", "", 12)
        self.set_text_color(*PASTEL)
        self.cell(0, 8, ascii_safe("Weather page: ") + APP_WEATHER_URL, ln=True, align="L", link=APP_WEATHER_URL)
        self.cell(0, 8, ascii_safe("NOTAMs page: ") + APP_NOTAMS_URL, ln=True, align="L", link=APP_NOTAMS_URL)
        self.cell(0, 8, ascii_safe("VFR Map app: ") + APP_VFRMAP_URL, ln=True, align="L", link=APP_VFRMAP_URL)
        self.cell(0, 8, ascii_safe("Mass & Balance: ") + APP_MNB_URL, ln=True, align="L", link=APP_MNB_URL)
        self.set_text_color(0, 0, 0)

# ---------- Header + toolbar ----------
st.markdown('<div class="app-title">Briefings</div>', unsafe_allow_html=True)
tb1, tb2, tb3, tb4 = st.columns(4)
with tb1: st.page_link("pages/Weather.py", label="Open Weather 🌤️")
with tb2: st.page_link("pages/NOTAMs.py", label="Open NOTAMs 📄")
with tb3: st.page_link("pages/VFRMap.py", label="Open VFR Map 🗺️")
with tb4: st.page_link("pages/MassBalance.py", label="Mass & Balance ✈️")
st.divider()

# ---------- Abas ----------
tab_mission, tab_notams, tab_sigmet_gamet, tab_charts, tab_pairs, tab_generate = st.tabs(
    ["Missão", "NOTAMs", "SIGMET & GAMET", "Charts", "Navlog ↔ VFR (pares)", "Gerar PDFs"]
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
        icaos_metar_str = st.text_input("ICAO list for METAR/TAF (comma / space / newline)", value="LPPT LPBJ LEBZ")
        icaos_metar = parse_icaos(icaos_metar_str)
    with c2:
        # Default inclui LPPC(ENROUTE)
        icaos_notam_str = st.text_input("ICAO list for NOTAMs (comma / space / newline)", value="LPPC(ENROUTE) LPSO LPCB LPEV")
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
    for i, icao in enumerate(icaos_notam):
        with edit_cols[i % 3]:
            initial_text = "\n\n".join(existing_map.get(icao, [])) if existing_map.get(icao) else ""
            editors_notam[icao] = st.text_area(
                f"{icao} — NOTAMs",
                value=initial_text,
                placeholder=("Ex.: AERODROME BEACON ONLY FLASH-GREEN LIGHT OPERATIVE.\n"
                             "FROM: 29th Jul 2025 15:10 TO: 29th Sep 2025 18:18 EST\n\n"
                             "Outro NOTAM aqui..."),
                key=f"ed_notam_{icao}",
                height=160
            )

    # Botão único: atualiza apenas o que editaste; o resto do Gist mantém-se
    if st.button("Guardar NOTAMs no Gist"):
        changed: Dict[str, List[str]] = {}
        for icao in icaos_notam:
            changed[icao] = parse_block_to_list(editors_notam.get(icao, ""))
        ok, msg = save_notams_to_gist(changed)
        if ok:
            st.success(msg)
            st.cache_data.clear()
        else:
            st.error(msg)

# ---------- SIGMET & GAMET ----------
with tab_sigmet_gamet:
    st.markdown("### SIGMET & GAMET")
    _gamet_obj = load_gamet_from_gist()
    _gamet_initial = (_gamet_obj.get("text") or "").strip()

    gamet_text = st.text_area(
        "GAMET — texto integral",
        value=_gamet_initial, height=220, key="gamet_editor",
        placeholder="Ex.: LPPC FIR GAMET VALID 12/06Z-12/12Z\n..."
    )
    c_g = st.columns([0.32, 0.68])
    with c_g[0]:
        if st.button("Guardar GAMET no Gist"):
            ok, msg = save_gamet_to_gist(gamet_text)
            if ok:
                st.success(msg); 
                try: st.cache_data.clear()
                except Exception: pass
            else:
                st.error(msg)

    st.divider()

    _sigmet_obj = load_sigmet_from_gist()
    _sigmet_initial = (_sigmet_obj.get("text") or "").strip()
    sigmet_text = st.text_area(
        "SIGMET (LPPC) — texto integral",
        value=_sigmet_initial, height=160, key="sigmet_editor",
        placeholder="Ex.: LPPC SIGMET 2 VALID 12/09Z-12/13Z LPPC-\nSEV TURB FCST BTN FL080/FL200 MOV NE 20KT ..."
    )
    c_s = st.columns([0.32, 0.68])
    with c_s[0]:
        if st.button("Guardar SIGMET no Gist"):
            ok, msg = save_sigmet_to_gist(sigmet_text)
            if ok:
                st.success(msg); 
                try: st.cache_data.clear()
                except Exception: pass
            else:
                st.error(msg)

# ---------- Charts ----------
with tab_charts:
    st.markdown("### Charts (SIGWX / SPC / Wind & Temp / Other)")
    st.caption("Aceita PDF/PNG/JPG/JPEG/GIF (para PDF lemos a 1.ª página).")
    use_ai_for_charts = st.toggle("Analisar charts com IA", value=True, help="Marcado por omissao")
    preview_w = st.slider("Largura da pré-visualização (px)", min_value=240, max_value=640, value=420, step=10)
    uploads = st.file_uploader("Upload charts", type=["pdf","png","jpg","jpeg","gif"], accept_multiple_files=True, label_visibility="collapsed")

    def _base_title_for_kind(k: str) -> str:
        return {
            "SIGWX": "Significant Weather Chart (SIGWX)",
            "SPC": "Surface Pressure Chart (SPC)",
            "Wind & Temp": "Wind and Temperature Chart",
        }.get(k, "Weather Chart")

    charts: List[Dict[str,Any]] = []
    if uploads:
        for idx, f in enumerate(uploads):
            raw = f.read(); mime = f.type or ""
            img_png = ensure_png_from_bytes(raw, mime)
            name = f.name or "(sem nome)"
            col_img, col_meta = st.columns([0.35, 0.65])
            with col_img:
                try: st.image(img_png.getvalue(), caption=name, width=preview_w)
                except Exception: st.write(name)
            with col_meta:
                kind = st.selectbox(f"Tipo do chart #{idx+1}", ["SIGWX","SPC","Wind & Temp","Other"], index=0, key=f"kind_{idx}")
                title_default = _base_title_for_kind(kind)
                title = st.text_input("Título", value=title_default, key=f"title_{idx}")
                subtitle = st.text_input("Subtítulo (opcional)", value="", key=f"subtitle_{idx}")
                order_val = st.number_input("Ordem", min_value=1, max_value=len(uploads)+10, value=idx+1, step=1, key=f"ord_{idx}")
            charts.append({"kind": kind, "title": title, "subtitle": subtitle, "img_png": img_png, "order": order_val, "filename": name})

# ---------- Navlog ↔ VFR (pares genéricos, NÃO por ICAO) ----------
with tab_pairs:
    st.markdown("### Emparelhamento Navlog ↔ VFR (pares genéricos)")
    st.caption("Cria pares com um **Título** (ex.: “LPCB”, “LPEV”, “Treino”), e carrega **Navlog** e o respetivo **VFR Map**. Aceita PDF/PNG/JPG/JPEG/GIF.")
    num_pairs = st.number_input("Número de pares", min_value=0, max_value=8, value=0, step=1)
    pairs: List[Dict[str, Any]] = []
    for i in range(int(num_pairs)):
        with st.expander(f"Par #{i+1}", expanded=False):
            title_pair = st.text_input(f"Título do par #{i+1}", key=f"pair_title_{i}")
            c1, c2 = st.columns(2)
            with c1:
                nav_file = st.file_uploader(f"Navlog — {title_pair or 'Sem título'}", type=["pdf","png","jpg","jpeg","gif"], key=f"pair_nav_{i}")
            with c2:
                vfr_file = st.file_uploader(f"VFR Map — {title_pair or 'Sem título'}", type=["pdf","png","jpg","jpeg","gif"], key=f"pair_vfr_{i}")
            pairs.append({"title": title_pair.strip(), "nav": nav_file, "vfr": vfr_file})

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
    sigmet_text_local = locals().get("sigmet_text","")
    _sigmet_initial_local = locals().get("_sigmet_initial","")
    gamet_text_local = locals().get("gamet_text","")
    _gamet_initial_local = locals().get("_gamet_initial","")

    # METAR/TAF
    metar_analyses: List[Tuple[str, str, str, str]] = []
    for icao in icaos_metar_local:
        metar_raw = fetch_metar_now(icao) or ""
        taf_raw   = fetch_taf_now(icao) or ""
        analysis  = analyze_metar_taf_pt(icao, metar_raw, taf_raw) if (metar_raw or taf_raw) else "Sem METAR/TAF disponiveis neste momento."
        metar_analyses.append((icao, metar_raw, taf_raw, analysis))

    # SIGMET & GAMET
    sigmet_for_pdf = (sigmet_text_local or _sigmet_initial_local or "").strip()
    sigmet_analysis = analyze_sigmet_pt(sigmet_for_pdf) if sigmet_for_pdf else ""
    gamet_for_pdf = (gamet_text_local or _gamet_initial_local or "").strip()
    gamet_analysis = analyze_gamet_pt(gamet_for_pdf) if gamet_for_pdf else ""

    # Detailed
    det_pdf = DetailedPDF()
    det_pdf.section_page("Observação")
    det_pdf.metar_taf_block(metar_analyses)
    if sigmet_for_pdf:
        det_pdf.section_page("SIGMET")
        det_pdf.sigmet_block(sigmet_for_pdf, sigmet_analysis)
    if gamet_for_pdf:
        det_pdf.section_page("GAMET")
        det_pdf.gamet_block(gamet_for_pdf, gamet_analysis)

    # Charts por secções (SPC → SIGWX → Wind&Temp → Other)
    charts_local: List[Dict[str,Any]] = locals().get("charts", [])
    if charts_local:
        grouped: Dict[str, List[Dict[str,Any]]] = {"SPC": [], "SIGWX": [], "Wind & Temp": [], "Other": []}
        for c in charts_local:
            grouped.setdefault(c["kind"], []).append(c)
        for k in list(grouped.keys()):
            grouped[k] = sorted(grouped[k], key=_chart_sort_key)

        for kind, sec_title in [
            ("SPC","Surface Pressure Charts (SPC)"),
            ("SIGWX","Significant Weather Charts (SIGWX)"),
            ("Wind & Temp","Wind & Temperature Charts"),
            ("Other","Other Charts"),
        ]:
            items = grouped.get(kind, [])
            if not items: continue
            det_pdf.section_page(sec_title)
            for ch in items:
                title, subtitle, img_png, fname = ch["title"], ch["subtitle"], ch["img_png"], ch.get("filename","")
                analysis_txt = ""
                if locals().get("use_ai_for_charts", False):
                    try:
                        analysis_txt = analyze_chart_pt(kind, base64.b64encode(img_png.getvalue()).decode("utf-8"), filename_hint=fname)
                    except Exception:
                        analysis_txt = "Analise indisponivel."
                det_pdf.chart_block(title, subtitle, img_png, analysis_txt)

    det_pdf.glossary_page()

    det_name = f"Briefing Detalhado - Missao {locals().get('mission_no') or 'X'}.pdf"
    det_pdf.output(det_name)
    with open(det_name, "rb") as f:
        st.download_button("Download Detailed (PT)", data=f.read(), file_name=det_name, mime="application/pdf", use_container_width=True)

# ---------- Final Briefing (EN) ----------
def image_bytes_to_section_pdf(section_title: str, page_title: str, raw: bytes, mime: str, orientation: str) -> bytes:
    img_png = ensure_png_from_bytes(raw, mime)
    doc = FPDF(orientation=orientation, unit="mm", format="A4")
    # página de secção
    doc.add_page(orientation="P")
    doc.set_font("Helvetica", "B", 20)
    doc.set_text_color(*PASTEL)
    doc.cell(0, 12, ascii_safe(section_title), ln=True, align="C")
    doc.set_text_color(0,0,0)
    # página com imagem
    doc.add_page(orientation=orientation)
    draw_header(doc, page_title)
    place_image_full(doc, img_png, max_h_pad=58)
    data = doc.output(dest="S")
    return data if isinstance(data, (bytes, bytearray)) else data.encode("latin-1")

if 'gen_final' in locals() and gen_final:
    fb = FinalBriefPDF()
    fb.cover(
        mission_no=locals().get("mission_no",""),
        pilot=locals().get("pilot",""),
        aircraft=locals().get("aircraft_type",""),
        callsign=locals().get("callsign",""),
        reg=locals().get("registration",""),
        date_str=str(locals().get("flight_date","")),
        time_utc=locals().get("time_utc","")
    )

    # Flight Plan (opcional)
    fp_upload = st.session_state.get('fp_upload_obj') if 'fp_upload_obj' in st.session_state else None  # compat
    # Na versão anterior o upload do FP estava noutro tab; mantém a abordagem antiga se aí existir:
    # (Se quiseres, podemos deslocar o FP para um tab próprio; aqui mantenho compatibilidade)
    # Nada a fazer se não existir.

    charts_local: List[Dict[str,Any]] = locals().get("charts", [])
    if charts_local:
        ordered = [(c["title"], c["subtitle"], c["img_png"]) for c in sorted(charts_local, key=_chart_sort_key)]
        fb.section_title("Charts")
        fb.charts_only(ordered)

    fb_bytes: bytes = fpdf_to_bytes(fb)
    final_bytes = fb_bytes

    # Merge: Pares Navlog↔VFR (na ordem definida) -> Resources
    pairs_local: List[Dict[str, Any]] = locals().get("pairs", [])
    try:
        main = fitz.open(stream=fb_bytes, filetype="pdf")
        insert_pos = main.page_count  # inserir no fim

        for p in (pairs_local or []):
            title_pair = p.get("title") or "Sem título"
            # Página de secção
            sec = FinalBriefPDF()
            sec.section_title(f"Navlog & VFR — {title_pair}")
            sec_bytes = fpdf_to_bytes(sec)
            sec_doc = fitz.open(stream=sec_bytes, filetype="pdf")
            main.insert_pdf(sec_doc, start_at=insert_pos); insert_pos += sec_doc.page_count; sec_doc.close()

            # Navlog
            nv = p.get("nav")
            if nv is not None:
                raw = nv.read()
                if (nv.type or "").lower() == "application/pdf":
                    try:
                        nv_doc = fitz.open(stream=raw, filetype="pdf")
                        main.insert_pdf(nv_doc, start_at=insert_pos); insert_pos += nv_doc.page_count; nv_doc.close()
                    except Exception:
                        pass
                else:
                    nv_bytes = image_bytes_to_pdf_bytes(f"Navlog — {title_pair}", ensure_png_from_bytes(raw, nv.type or ""), orientation="P")
                    nv_doc = fitz.open(stream=nv_bytes, filetype="pdf")
                    main.insert_pdf(nv_doc, start_at=insert_pos); insert_pos += nv_doc.page_count; nv_doc.close()

            # VFR
            vf = p.get("vfr")
            if vf is not None:
                raw = vf.read()
                if (vf.type or "").lower() == "application/pdf":
                    try:
                        vf_doc = fitz.open(stream=raw, filetype="pdf")
                        main.insert_pdf(vf_doc, start_at=insert_pos); insert_pos += vf_doc.page_count; vf_doc.close()
                    except Exception:
                        pass
                else:
                    vf_bytes = image_bytes_to_pdf_bytes(f"VFR Map — {title_pair}", ensure_png_from_bytes(raw, vf.type or ""), orientation="L")
                    vf_doc = fitz.open(stream=vf_bytes, filetype="pdf")
                    main.insert_pdf(vf_doc, start_at=insert_pos); insert_pos += vf_doc.page_count; vf_doc.close()

        # Resources page (links)
        res_pdf = FinalBriefPDF(); res_pdf.resources_page()
        res_bytes = fpdf_to_bytes(res_pdf)
        res_doc = fitz.open(stream=res_bytes, filetype="pdf")
        main.insert_pdf(res_doc, start_at=insert_pos); insert_pos += res_doc.page_count; res_doc.close()

        final_bytes = main.tobytes()
        main.close()
    except Exception:
        pass

    final_name = f"Briefing - Missao {locals().get('mission_no') or 'X'}.pdf"
    st.download_button("Download Final Briefing (EN)", data=final_bytes, file_name=final_name, mime="application/pdf", use_container_width=True)

