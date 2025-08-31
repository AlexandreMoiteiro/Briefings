from typing import Dict, Any, List, Tuple, Optional
import io, os, re, base64, tempfile, unicodedata, json, datetime as dt
import streamlit as st
from PIL import Image
from fpdf import FPDF
import fitz  # PyMuPDF
import requests
from openai import OpenAI

# =========================
# External pages (ajusta se renomeares)
# =========================
APP_WEATHER_URL = "https://briefings.streamlit.app/Weather"
APP_NOTAMS_URL  = "https://briefings.streamlit.app/NOTAMs"
APP_VFRMAP_URL  = "https://briefings.streamlit.app/VFRMap"
APP_MNB_URL     = "https://briefings.streamlit.app/MassBalance"

# =========================
# Página & estilos
# =========================
st.set_page_config(page_title="Briefings", layout="wide")
st.markdown(
    """
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
""",
    unsafe_allow_html=True,
)

# =========================
# OpenAI client (opcional)
# =========================
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

# =========================
# Constantes úteis
# =========================
# LPSO (Ponte de Sor) ARP — 39°12'42"N 008°03'28"W ≈ 39.211667, -8.057778 (fonte: eAIP Portugal)
LPSO_ARP = (39.211667, -8.057778)
PASTEL = (90, 127, 179)  # azul suave

# =========================
# Utils
# =========================
def ascii_safe(text: Any) -> str:
    if text is None:
        return ""
    t = (
        unicodedata.normalize("NFKD", str(text))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
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


# =========================
# Image helpers
# =========================
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
    """Converte bytes (pdf/gif/img) em PNG bytes, com fallback bruto."""
    try:
        lmime = (mime or "").lower()
        if lmime == "application/pdf":
            img = load_first_pdf_page(file_bytes, dpi=300)
        elif lmime == "image/gif":
            img = gif_first_frame(file_bytes)
        else:
            img = Image.open(io.BytesIO(file_bytes)).convert("RGB").copy()
        return to_png_bytes(img)
    except Exception:
        return io.BytesIO(file_bytes)


# =========================
# PDF text helpers (detect kind/validity/region)
# =========================
_DEF_KINDS = ["SIGWX", "SPC", "Wind & Temp", "Other"]


def extract_pdf_text_first_page(pdf_bytes: bytes) -> str:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc.load_page(0)
        return page.get_text("text") or ""
    except Exception:
        return ""


def guess_chart_kind(name: str, text: str) -> str:
    s = f"{name}\n{text}".lower()
    if any(x in s for x in ["sigwx", "significant weather", "swh", "swm", "swl"]):
        return "SIGWX"
    if any(x in s for x in ["surface pressure", "mslp", "isobar", "spc"]):
        return "SPC"
    if ("wind" in s and ("temp" in s or "temperature" in s)) or re.search(r"\bfl\d{2,3}\b", s):
        return "Wind & Temp"
    return "Other"


def extract_validity(s: str) -> str:
    u = (s or "").upper()
    m = re.search(r"VALID\s*([0-3]?\d/?[0-2]?\dZ\s*-\s*[0-3]?\d/?[0-2]?\dZ|[0-2]?\d{2,3}Z)", u)
    if m:
        return m.group(0).strip()
    m2 = re.search(r"\b([01]?\d|2[0-3])(?:00)?Z\b(\s*-\s*([01]?\d|2[0-3])(?:00)?Z\b)?", u)
    if m2:
        return m2.group(0).strip()
    return ""


def detect_region(s: str) -> str:
    u = (s or "").upper()
    for kw in [
        "IBERIA",
        "IBERIAN",
        "PORTUGAL",
        "EUROPE",
        "NORTH ATLANTIC",
        "N ATLANTIC",
        "ATLANTIC",
        "WESTERN EUROPE",
    ]:
        if kw in u:
            return kw.title()
    return ""


def derive_default_title(kind: str, filename: str, text_hint: str) -> str:
    base = (
        "Significant Weather Chart (SIGWX)"
        if kind == "SIGWX"
        else "Surface Pressure Chart (SPC)"
        if kind == "SPC"
        else "Wind and Temperature Chart"
        if kind == "Wind & Temp"
        else "Weather Chart"
    )
    short = extract_validity(filename) or extract_validity(text_hint)
    return f"{base}{' — '+short if short else ''}"


# =========================
# METAR/TAF (CheckWX)
# =========================
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


# =========================
# Gist helpers (generic) — GAMET/NOTAM/SIGMET
# =========================
def _get_gist_secrets(prefix: str) -> Tuple[str, str, str]:
    token = (st.secrets.get(f"{prefix}_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get(f"{prefix}_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get(f"{prefix}_GIST_FILENAME") or "").strip()
    return token, gid, fn


def _gist_config_ok(prefix: str) -> bool:
    token, gid, fn = _get_gist_secrets(prefix)
    return all([token, gid, fn])


@st.cache_data(ttl=90)
def gist_load_json(prefix: str, fallback_key: str) -> Dict[str, Any]:
    if not _gist_config_ok(prefix):
        return {fallback_key: "", "updated_utc": None}
    try:
        token, gid, fn = _get_gist_secrets(prefix)
        r = requests.get(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            timeout=12,
        )
        r.raise_for_status()
        files = r.json().get("files", {})
        file_obj = files.get(fn) or {}
        content = (file_obj.get("content") or "").strip()
        if not content:
            return {fallback_key: "", "updated_utc": None}
        try:
            return json.loads(content)
        except Exception:
            return {fallback_key: content, "updated_utc": None}
    except Exception:
        return {fallback_key: "", "updated_utc": None}


def gist_save_json(prefix: str, payload_obj: Dict[str, Any]) -> tuple[bool, str]:
    if not _gist_config_ok(prefix):
        return False, f"Segredos {prefix}_GIST_* em falta."
    try:
        token, gid, fn = _get_gist_secrets(prefix)
        payload = {"updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), **payload_obj}
        body = {"files": {fn: {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}}
        r = requests.patch(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            json=body,
            timeout=12,
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, f"{prefix} guardado no Gist."
    except Exception as e:
        return False, f"Erro a gravar {prefix} no Gist: {e}"


@st.cache_data(ttl=90)
def load_gamet_from_gist() -> Dict[str, Any]:
    obj = gist_load_json("GAMET", "text")
    if isinstance(obj, dict) and "text" in obj:
        return {"text": obj.get("text") or "", "updated_utc": obj.get("updated_utc")}
    return {"text": "", "updated_utc": None}


def save_gamet_to_gist(text: str) -> tuple[bool, str]:
    return gist_save_json("GAMET", {"text": (text or "").strip()})


@st.cache_data(ttl=90)
def load_notams_from_gist() -> Dict[str, Any]:
    obj = gist_load_json("NOTAM", "map")
    if not isinstance(obj, dict):
        return {"map": {}, "updated_utc": None}
    if "map" in obj and isinstance(obj.get("map"), dict):
        return {"map": obj.get("map") or {}, "updated_utc": obj.get("updated_utc")}
    m = {k: v for k, v in obj.items() if isinstance(v, list)}
    return {"map": m, "updated_utc": obj.get("updated_utc")}


def save_notams_to_gist(new_map: Dict[str, List[str]]) -> tuple[bool, str]:
    clean = {k: [s for s in v if str(s).strip()] for k, v in (new_map or {}).items()}
    return gist_save_json("NOTAM", {"map": clean})


@st.cache_data(ttl=90)
def load_sigmet_from_gist() -> Dict[str, Any]:
    obj = gist_load_json("SIGMET", "text")
    if isinstance(obj, dict) and "text" in obj:
        return {"text": obj.get("text") or "", "updated_utc": obj.get("updated_utc")}
    return {"text": "", "updated_utc": None}


def save_sigmet_to_gist(text: str) -> tuple[bool, str]:
    return gist_save_json("SIGMET", {"text": (text or "").strip()})


# =========================
# GPT wrappers (texto)
# =========================
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
                {"role": "user", "content": prompt_user},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        content = (r2.choices[0].message.content or "").strip()
        return ascii_safe(content) if content else ""
    except Exception as e2:
        return ascii_safe(f"Analise indisponivel (erro IA: {e2})")


def analyze_chart_pt(kind: str, img_b64: str, filename_hint: str = "") -> str:
    try:
        model_name = st.secrets.get("OPENAI_MODEL_VISION", "gpt-4o").strip() or "gpt-4o"
    except Exception:
        model_name = "gpt-4o"
    sys = (
        "Es meteorologista aeronautico senior. Analisa EXAUSTIVAMENTE o chart em PT-PT, "
        "em prosa corrida (sem listas) e em 3 blocos curtos: "
        "1) Visao geral — padrao sinoptico, frentes (tipo e movimento), centros/pressao, jatos (altitude/noe de isotacas), areas de fenomenos e janelas de validade (horas UTC exatamente como no chart). "
        "2) Portugal continental — detalhe por litoral/N/C/S com ALTURA/NIVEL (SFC/AGL/AMSL/FL) e valores: vento (sfc e niveis usuais VFR/IFR), visibilidade/tecto, precipitacao e tipo, nebulosidade (FEW/SCT/BKN/OVC com alturas tops/bases), 0C/nivel de congelacao, gelo (lev/mod/sev e camadas), turbulencia (lev/mod/sev), cisalhamento, CB/TCU e areas SIGWX. "
        "3) Alentejo (inclui LPSO) — pormenor operacional: rotas/altitudes recomendadas/evitadas, riscos, alternantes. "
        "IDENTIFICA e NOMEIA TODOS os simbolos/etiquetas visiveis (linhas, setas, barbules, isobaras/isotacas/isotermas, limites de areas, legendas e escalas) e explica o seu significado. "
        "Se o chart tiver horarios/validade, escreve-os explicitamente; se algo nao estiver indicado, diz ‘nao indicado no chart’. "
        "Conclui com impacto operacional claro (VFR/IFR) e acoes praticas. Usa apenas informacao visivel; nao inventes."
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
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ]},
            ],
            max_tokens=900,
            temperature=0.2,
        )
        out = (r.choices[0].message.content or "").strip()
        return ascii_safe(out) if out else "Analise indisponivel."
    except Exception as e:
        return ascii_safe(f"Analise indisponivel (erro IA: {e})")


def analyze_metar_taf_pt(icao: str, metar: str, taf: str) -> str:
    sys = (
        "Es meteorologista aeronautico senior. Em PT-PT e TEXTO CORRIDO, faz uma interpretacao exaustiva do METAR e TAF, "
        "decodificando token a token SEM OMITIR o significado de NENHUM codigo (inclui, se existirem: COR/AMD, hora, vento/VRB/GUST, CAVOK, RVR, vis, fenomenos (+/– RA/TS/BR/FG/… e ‘RE’), nuvens FEW/SCT/BKN/OVC/NSC/NCD com alturas, T/Td, QNH/QFE, TREND, RMK). "
        "Para o TAF, explica validade, BECMG/TEMPO/PROB, e cada linha/segmento. "
        "Se um grupo nao existir, indica ‘nao presente’. "
        "Termina com impacto operacional (VFR/IFR, altitudes, riscos) e um mini-glossario das abreviaturas encontradas. Usa apenas o texto fornecido."
    )
    user = f"Aerodromo {icao}\n\nMETAR (RAW):\n{metar}\n\nTAF (RAW):\n{taf}"
    return gpt_text(sys, user, max_tokens=2000)


def analyze_sigmet_pt(sigmet_text: str) -> str:
    if not (sigmet_text or '').strip():
        return ""
    sys = (
        "Es meteorologista aeronautico senior. Em PT-PT e prosa corrida, interpreta o SIGMET LPPC de forma completa: "
        "fenomeno, area/limites, niveis/FL, validade/horas, movimento/intensidade, e impacto operacional (VFR/IFR)."
    )
    return gpt_text(sys, sigmet_text, max_tokens=1200)


def analyze_gamet_pt(gamet_text: str) -> str:
    if not (gamet_text or '').strip():
        return ""
    lat, lon = LPSO_ARP
    sys = (
        "Es meteorologista aeronautico senior. Em PT-PT e texto corrido, explica o GAMET LPPC EXAUSTIVAMENTE: "
        "fenomenos, niveis/camadas, areas e subdivisoes, validades/horas e qualquer PROB/TEMPO/BECMG, interpretando TODOS os codigos SEM omitir significados. "
        "Se houver coordenadas/areas, avalia explicitamente se ABRANGEM o ponto LPSO (Ponte de Sor) ARP 39°12'42\"N 008°03'28\"W (≈ {lat:.6f}, {lon:.6f}). "
        "Escreve no final uma linha clara: ‘Abrange LPSO’, ‘Nao abrange LPSO’ ou ‘Indeterminado com o texto dado’. Usa apenas o texto fornecido; nao inventes."
    )
    user = f"Texto integral do GAMET:\n{gamet_text}\n\nReferencia: LPSO ≈ {lat:.6f}, {lon:.6f}."
    return gpt_text(sys, user, max_tokens=2000)


# =========================
# PDF helpers/classes
# =========================
class DetailedPDF(FPDF):
    def header(self):
        pass

    def footer(self):
        # default overridden at runtime to add page numbers
        pass

    def metar_taf_block(self, analyses: List[Tuple[str, str, str, str]]):
        self.add_page(orientation="P")
        draw_header(self, "METAR / TAF — Interpretacao (PT)")
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
                self.ln(2)
            if taf_raw:
                self.set_font("Helvetica", "B", 12)
                self.cell(0, 7, "TAF (RAW):", ln=True)
                self.set_font("Helvetica", "", 12)
                self.multi_cell(0, 7, ascii_safe(taf_raw))
                self.ln(2)
            self.set_font("Helvetica", "B", 12)
            self.cell(0, 7, "Interpretacao:", ln=True)
            self.set_font("Helvetica", "", 12)
            self.multi_cell(0, 7, ascii_safe(analysis or "Sem interpretacao."))
            self.ln(3)

    def sigmet_block(self, sigmet_text: str, analysis_pt: str):
        if not (sigmet_text or '').strip():
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

    def gamet_block(self, gamet_text: str, analysis_pt: str):
        if not (gamet_text or '').strip():
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

    def chart_block(self, title: str, subtitle: str, img_png: io.BytesIO, analysis_pt: str):
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
        x = (self.w - w) // 2
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            path = tmp.name
        self.image(path, x=x, y=self.get_y() + 6, w=w, h=h)
        os.remove(path)
        self.ln(h + 12)
        self.set_font("Helvetica", "", 12)
        self.multi_cell(0, 7, ascii_safe(analysis_pt or " "))


class FinalBriefPDF(FPDF):
    def header(self):
        pass

    def footer(self):
        pass  # overridden at runtime

    def cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc):
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
        self.ln(6)
        self.set_font("Helvetica", "I", 12)
        self.set_text_color(*PASTEL)
        self.cell(0, 7, ascii_safe("Weather page: ") + APP_WEATHER_URL, ln=True, align="C", link=APP_WEATHER_URL)
        self.cell(0, 7, ascii_safe("NOTAMs page: ") + APP_NOTAMS_URL, ln=True, align="C", link=APP_NOTAMS_URL)
        self.set_text_color(0, 0, 0)

    def flightplan_image_portrait(self, title: str, img_png: io.BytesIO):
        self.add_page(orientation="P")
        draw_header(self, ascii_safe(title))
        place_image_full(self, img_png)

    def charts_only(self, charts: List[Tuple[str, str, io.BytesIO]]):
        for (title, subtitle, img_png) in charts:
            self.add_page(orientation="L")
            draw_header(self, ascii_safe(title))
            if subtitle:
                self.set_font("Helvetica", "I", 12)
                self.cell(0, 9, ascii_safe(subtitle), ln=True, align="C")
            place_image_full(self, img_png)


# ---------- Simple drawing helpers ----------
def draw_header(pdf: FPDF, text: str):
    pdf.set_draw_color(229, 231, 235)
    pdf.set_line_width(0.3)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, ascii_safe(text), ln=True, align="C", border="B")


def place_image_full(pdf: FPDF, png_bytes: io.BytesIO, max_h_pad: int = 58):
    max_w = pdf.w - 22
    max_h = pdf.h - max_h_pad
    img = Image.open(png_bytes)
    iw, ih = img.size
    r = min(max_w / iw, max_h / ih)
    w, h = int(iw * r), int(ih * r)
    x = (pdf.w - w) // 2
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp, format="PNG")
        path = tmp.name
    pdf.image(path, x=x, y=pdf.get_y() + 6, w=w, h=h)
    os.remove(path)
    pdf.ln(h + 10)


def pdf_embed_pdf_pages(pdf: FPDF, pdf_bytes: bytes, title: str, orientation: str = "P", max_pages: Optional[int] = None):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total = doc.page_count
    pages = range(total) if max_pages is None else range(min(total, max_pages))
    for i in pages:
        page = doc.load_page(i)
        png = page.get_pixmap(dpi=450).tobytes("png")
        img = Image.open(io.BytesIO(png)).convert("RGB")
        bio = io.BytesIO(); img.save(bio, format="PNG"); bio.seek(0)
        pdf.add_page(orientation=orientation)
        draw_header(pdf, ascii_safe(title + (f" — p.{i+1}" if total > 1 else "")))
        place_image_full(pdf, bio, max_h_pad=58)


# ---------- Helper robusto: FPDF -> bytes (fpdf vs fpdf2) ----------
def fpdf_to_bytes(doc: FPDF) -> bytes:
    data = doc.output(dest="S")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return data.encode("latin-1")


# =========================
# UI (Tabs for structure)
# =========================
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

_tab_titles = ["Flight & Mission", "NOTAMs", "GAMET", "SIGMET", "Charts", "PDFs"]
TAB_FLIGHT, TAB_NOTAM, TAB_GAMET, TAB_SIGMET, TAB_CHARTS, TAB_PDFS = st.tabs(_tab_titles)

with TAB_FLIGHT:
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
        icaos_notam_str = st.text_input("ICAO list for NOTAMs (comma / space / newline)", value="LPSO LPCB LPEV")
        icaos_notam = parse_icaos(icaos_notam_str)

with TAB_NOTAM:
    st.markdown("### NOTAMs (editar e guardar)")
    saved_notams = load_notams_from_gist()
    existing_map: Dict[str, List[str]] = ((saved_notams.get("map") or {}) if isinstance(saved_notams, dict) else {})

    def parse_block_to_list(text: str) -> List[str]:
        if not (text or '').strip():
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
                placeholder=("Ex.: AERODROME BEACON ONLY FLASH-GREEN LIGHT OPERATIVE.\nFROM: 29th Jul 2025 15:10 TO: 29th Sep 2025 18:18 EST\n\nOutro NOTAM aqui..."),
                key=f"ed_notam_{icao}",
                height=160,
            )

    col_save_n = st.columns([0.4, 0.3, 0.3])
    with col_save_n[0]:
        overwrite_all_n = st.checkbox("Substituir TODOS os aerodromos do Gist (NOTAMs)", value=False)
    with col_save_n[1]:
        if st.button("Guardar NOTAMs no Gist"):
            new_map: Dict[str, List[str]] = {}
            if not overwrite_all_n:
                new_map.update(existing_map)
            for icao in icaos_notam:
                new_map[icao] = parse_block_to_list(editors_notam.get(icao, ""))
            ok, msg = save_notams_to_gist(new_map)
            if ok:
                st.success(msg)
                st.cache_data.clear()
            else:
                st.error(msg)

with TAB_GAMET:
    st.markdown("### GAMET (editar e guardar)")
    _gamet_obj = load_gamet_from_gist()
    _gamet_initial = (_gamet_obj.get("text") or "").strip()

    gamet_text = st.text_area(
        "Texto completo do GAMET",
        value=_gamet_initial,
        placeholder="Ex.: LPPC FIR GAMET VALID 12/06Z-12/12Z\n... (texto integral aqui) ...",
        height=220,
        key="gamet_editor",
    )

    col_gamet = st.columns([0.3, 0.7])
    with col_gamet[0]:
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

with TAB_SIGMET:
    st.markdown("### SIGMET (LPPC) — editar e guardar")
    _sigmet_obj = load_sigmet_from_gist()
    _sigmet_initial = (_sigmet_obj.get("text") or "").strip()

    sigmet_text_manual = st.text_area(
        "Texto integral do SIGMET (um ou mais, separar por ---)",
        value=_sigmet_initial,
        placeholder=("Ex.:\nLPPC SIGMET 1 VALID 121200/121600 LPPC-\n...texto do SIGMET...\n\n---\nLPPC SIGMET 2 VALID ..."),
        height=200,
        key="sigmet_editor",
    )

    c_sig = st.columns([0.3, 0.7])
    with c_sig[0]:
        if st.button("Guardar SIGMET no Gist"):
            ok, msg = save_sigmet_to_gist(sigmet_text_manual)
            if ok:
                st.success(msg)
                try:
                    st.cache_data.clear()
                except Exception:
                    pass
            else:
                st.error(msg)

with TAB_CHARTS:
    st.markdown("#### Charts")
    st.caption("Upload SIGWX / SPC / Wind & Temp. Accepts PDF/PNG/JPG/JPEG/GIF.")
    use_ai_for_charts = st.toggle("Analisar charts com IA", value=True, help="Marcado por omissao")
    preview_w = st.slider("Largura da pre-visualizacao (px)", min_value=240, max_value=640, value=420, step=10)
    uploads = st.file_uploader("Upload charts", type=["pdf", "png", "jpg", "jpeg", "gif"], accept_multiple_files=True, label_visibility="collapsed")

    def _base_title_for_kind(k: str) -> str:
        return {
            "SIGWX": "Significant Weather Chart (SIGWX)",
            "SPC": "Surface Pressure Chart (SPC)",
            "Wind & Temp": "Wind and Temperature Chart",
            "Other": "",
        }.get(k, "")

    global charts
    charts = []
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
                kind = st.selectbox(f"Tipo do chart #{idx+1}", ["SIGWX", "SPC", "Wind & Temp", "Other"], index=0, key=f"kind_{idx}")
                title_default = _base_title_for_kind(kind)
                title = st.text_input("Titulo", value=title_default, key=f"title_{idx}")
                subtitle = st.text_input("Subtitulo (opcional)", value="", key=f"subtitle_{idx}")
                order_val = st.number_input("Ordem", min_value=1, max_value=len(uploads) + 10, value=idx + 1, step=1, key=f"ord_{idx}")
            charts.append({"kind": kind, "title": title, "subtitle": subtitle, "img_png": img_png, "order": order_val, "filename": name})

with TAB_PDFS:
    st.markdown("### PDFs")
    st.subheader("Opcoes de inclusao")
    opt_metar = st.checkbox("Incluir METAR/TAF no Detailed", value=True)
    opt_sigmet = st.checkbox("Incluir SIGMET no Detailed (apenas Gist/Editor)", value=True)
    opt_gamet = st.checkbox("Incluir GAMET no Detailed", value=True)
    opt_charts_analysis = st.checkbox("Incluir analise IA dos charts", value=True)
    opt_toc = st.checkbox("Adicionar Table of Contents ao Detailed", value=True)

    col_pdfs = st.columns(2)
    with col_pdfs[0]:
        gen_det = st.button("Generate Detailed (PT)")

    # Optional Flight Plan & M&B PDFs
    st.markdown("#### Flight Plan (optional image/PDF/GIF)")
    fp_upload = st.file_uploader("Upload your flight plan (PDF/PNG/JPG/JPEG/GIF)", type=["pdf", "png", "jpg", "jpeg", "gif"], accept_multiple_files=False)
    fp_img_png: io.BytesIO | None = None
    fp_pdf_bytes: bytes | None = None
    fp_is_pdf = False
    if fp_upload:
        raw = fp_upload.read()
        if (fp_upload.type or "").lower() == "application/pdf":
            fp_pdf_bytes = raw
            fp_is_pdf = True
            st.success("Flight plan PDF sera embebido preservando as paginas originais.")
        else:
            fp_img_png = ensure_png_from_bytes(raw, fp_upload.type or "")
            st.success("Flight plan sera incluido como pagina em orientacao retrato.")

    st.markdown("#### M&B / Performance PDF (from external app)")
    mb_upload = st.file_uploader("Upload M&B/Performance PDF to embed (todas as paginas)", type=["pdf"], accept_multiple_files=False)

    with col_pdfs[1]:
        gen_final = st.button("Generate Final Briefing (EN)")

    # Store options in session
    st.session_state.update(dict(
        _opt_metar=opt_metar, _opt_sigmet=opt_sigmet, _opt_gamet=opt_gamet,
        _opt_charts_analysis=opt_charts_analysis, _opt_toc=opt_toc,
        _fp_img_png=fp_img_png, _fp_pdf_bytes=fp_pdf_bytes, _fp_is_pdf=fp_is_pdf,
        _mb_present = mb_upload is not None, _mb_uploader=mb_upload,
        gamet_editor = gamet_text, sigmet_editor = sigmet_text_manual,
    ))

st.divider()

# ---------- Generate Detailed (PT) ----------
if 'gen_det' in locals() and gen_det:
    det_pdf = DetailedPDF()

    # Footer: page numbers
    def _footer_with_pagenum(self):
        self.set_y(-12)
        self.set_font('Helvetica', '', 9)
        self.set_text_color(120)
        self.cell(0, 10, f"Page {self.page_no()}", 0, 0, 'C')
    DetailedPDF.footer = _footer_with_pagenum

    toc_entries: List[str] = []

    # METAR/TAF
    if st.session_state.get('_opt_metar', True):
        metar_analyses: List[Tuple[str, str, str, str]] = []
        for icao in icaos_metar:
            metar_raw = fetch_metar_now(icao) or ""
            taf_raw   = fetch_taf_now(icao) or ""
            analysis  = analyze_metar_taf_pt(icao, metar_raw, taf_raw) if (metar_raw or taf_raw) else "Sem METAR/TAF disponiveis neste momento."
            metar_analyses.append((icao, metar_raw, taf_raw, analysis))
        det_pdf.metar_taf_block(metar_analyses)
        toc_entries.append('METAR / TAF — Interpretacao (PT)')

    # SIGMET — apenas Gist/Editor (sem auto)
    sigmet_text_combined = (st.session_state.get('sigmet_editor') or '').strip()
    if not sigmet_text_combined:
        _sig = load_sigmet_from_gist().get('text') or ''
        sigmet_text_combined = _sig.strip()
    if st.session_state.get('_opt_sigmet', True) and sigmet_text_combined:
        sigmet_analysis = analyze_sigmet_pt(sigmet_text_combined)
        det_pdf.sigmet_block(sigmet_text_combined, sigmet_analysis)
        toc_entries.append('SIGMET (LPPC) — Interpretacao (PT)')

    # GAMET
    gamet_for_pdf = (st.session_state.get('gamet_editor') or (_gamet_initial if '_gamet_initial' in locals() else ''))
    gamet_for_pdf = (gamet_for_pdf or '').strip()
    if st.session_state.get('_opt_gamet', True) and gamet_for_pdf:
        gamet_analysis = analyze_gamet_pt(gamet_for_pdf)
        det_pdf.gamet_block(gamet_for_pdf, gamet_analysis)
        toc_entries.append('GAMET — Interpretacao (PT)')

    # Charts
    for ch in sorted(globals().get('charts', []), key=lambda c: c.get("order", 0)):
        title, subtitle, img_png, kind, fname = ch["title"], ch["subtitle"], ch["img_png"], ch["kind"], ch.get("filename", "")
        analysis_txt = ""
        if st.session_state.get('_opt_charts_analysis', True):
            try:
                analysis_txt = analyze_chart_pt(kind, base64.b64encode(img_png.getvalue()).decode("utf-8"), filename_hint=fname)
            except Exception:
                analysis_txt = "Analise indisponivel."
        det_pdf.chart_block(title, subtitle, img_png, analysis_txt)
        toc_entries.append(title)

    # Optional TOC page at the beginning
    final_bytes = fpdf_to_bytes(det_pdf)
    if st.session_state.get('_opt_toc', True) and toc_entries:
        body = fitz.open(stream=final_bytes, filetype='pdf')
        toc_pdf = DetailedPDF()
        draw_header(toc_pdf, 'Table of Contents')
        toc_pdf.set_font('Helvetica','',12)
        for i, entry in enumerate(toc_entries, start=1):
            toc_pdf.cell(0,8,f"{i}. {ascii_safe(entry)}", ln=True)
        merged = fitz.open()
        merged.insert_pdf(fitz.open(stream=fpdf_to_bytes(toc_pdf), filetype='pdf'))
        merged.insert_pdf(body)
        final_bytes = merged.tobytes()
        merged.close(); body.close()

    det_name = f"Briefing Detalhado - Missao {mission_no or 'X'}.pdf"
    st.download_button("Download Detailed (PT)", data=final_bytes, file_name=det_name, mime="application/pdf", use_container_width=True)

# ---------- Generate Final Briefing (EN) ----------
if 'gen_final' in locals() and gen_final:
    fb = FinalBriefPDF()

    def _footer_with_pagenum_final(self):
        self.set_y(-12)
        self.set_font('Helvetica','',9)
        self.set_text_color(120)
        self.cell(0, 10, f"Page {self.page_no()}", 0, 0, 'C')
    FinalBriefPDF.footer = _footer_with_pagenum_final

    fb.cover(mission_no, pilot, aircraft_type, callsign, registration, str(flight_date), time_utc)

    # Se flight plan vier como imagem, inclui
    if st.session_state.get('_fp_img_png') is not None:
        fb.flightplan_image_portrait("Flight Plan", st.session_state['_fp_img_png'])

    # Charts
    if globals().get('charts'):
        ordered = [(c["title"], c["subtitle"], c["img_png"]) for c in sorted(charts, key=lambda c: c.get("order", 0))]
        fb.charts_only(ordered)

    fb_bytes: bytes = fpdf_to_bytes(fb)
    final_bytes = fb_bytes

    # Embedding PDFs
    try:
        main = fitz.open(stream=fb_bytes, filetype="pdf")
        if st.session_state.get('_fp_is_pdf') and st.session_state.get('_fp_pdf_bytes'):
            try:
                fp_doc = fitz.open(stream=st.session_state['_fp_pdf_bytes'], filetype="pdf")
                main.insert_pdf(fp_doc, start_at=1)
                fp_doc.close()
            except Exception:
                pass
        if st.session_state.get('_mb_present') and st.session_state.get('_mb_uploader') is not None:
            try:
                mb_bytes = st.session_state['_mb_uploader'].read()
                mb_doc = fitz.open(stream=mb_bytes, filetype="pdf")
                if mb_doc.page_count > 0:
                    main.insert_pdf(mb_doc)
                mb_doc.close()
            except Exception:
                pass
        final_bytes = main.tobytes()
        main.close()
    except Exception:
        pass

    final_name = f"Briefing - Missao {mission_no or 'X'}.pdf"
    st.download_button("Download Final Briefing (EN)", data=final_bytes, file_name=final_name, mime="application/pdf", use_container_width=True)

st.divider()

