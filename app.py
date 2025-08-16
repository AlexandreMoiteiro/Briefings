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
.chart-card{border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:12px}
</style>
""",
    unsafe_allow_html=True,
)

# ---------- OpenAI client ----------
client = OpenAI(api_key=st.secrets.get("OPENAI_API_KEY"))

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

# ---------- Image/PDF helpers ----------

def load_first_pdf_page(pdf_bytes: bytes, dpi: int = 300):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    return Image.open(io.BytesIO(png)).convert("RGB").copy()


def gif_first_frame(file_bytes: bytes):
    im = Image.open(io.BytesIO(file_bytes))
    im.seek(0)
    return im.convert("RGB").copy()


def to_png_bytes(img: Image.Image) -> io.BytesIO:
    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out


def ensure_png_bytes_from_buffer(file_bytes: bytes, mime: str) -> io.BytesIO:
    """Converte qualquer ficheiro suportado para PNG (1.ª página para PDF/GIF)."""
    mt = (mime or "").lower()
    if mt == "application/pdf":
        img = load_first_pdf_page(file_bytes, dpi=300)
    elif mt.endswith("gif"):
        img = gif_first_frame(file_bytes)
    else:
        img = Image.open(io.BytesIO(file_bytes)).convert("RGB").copy()
    return to_png_bytes(img)


def b64_png(img_bytes: io.BytesIO) -> str:
    return base64.b64encode(img_bytes.getvalue()).decode("utf-8")


def extract_text_from_bytes(file_bytes: bytes, mime: str) -> str:
    """Extrai texto de PDF (primeira página). Para imagens devolve string vazia."""
    try:
        if (mime or "").lower() == "application/pdf":
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            page = doc.load_page(0)
            return page.get_text("text") or ""
    except Exception:
        pass
    return ""


def guess_chart_kind_and_meta(filename: str, page_text: str) -> Tuple[str, str, str, int]:
    """Heuristica simples para adivinhar tipo, titulo, subtitulo e prioridade."""
    name = (filename or "").lower()
    text = (page_text or "").lower()
    order_hint = 50  # default

    def contains(*keys):
        return any(k in text for k in keys) or any(k in name for k in keys)

    # Subtitulo: tentar apanhar 'VALID 12/06Z-12/12Z' ou datas/horas
    m = re.search(r"\bvalid\s*([^\n]+)", text, re.I)
    subtitle = m.group(0).strip() if m else ""

    if contains("sigwx", "significant weather", "swh", "swm", "swhh"):
        return "SIGWX", "Significant Weather Chart (SIGWX)", subtitle, 10
    if contains("surface pressure", "mslp", "sea level pressure", "isobar"):
        return "SPC", "Surface Pressure Chart (SPC)", subtitle, 20
    if contains("winds", "wind/temp", "temp aloft", "winds and temperatures", "fl", "jet stream"):
        return "Wind & Temp", "Winds and Temperatures Aloft", subtitle, 30
    # fallback
    return "Other", "Weather Chart", subtitle, 60


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

# ---------- SIGMET LPPC (AWC) ----------

def fetch_sigmet_lppc_auto() -> List[str]:
    try:
        r = requests.get(
            "https://aviationweather.gov/api/data/isigmet",
            params={"loc": "eur", "format": "json"},
            timeout=12,
        )
        r.raise_for_status()
        js = r.json()
        items = js if isinstance(js, list) else js.get("features", []) or []
        out: List[str] = []
        for it in items:
            props = it.get("properties", {}) if isinstance(it, dict) else {}
            if not props and isinstance(it, dict):
                props = it
            raw = (
                props.get("raw")
                or props.get("raw_text")
                or props.get("sigmet_text")
                or ""
            ).strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            if not raw:
                continue
            if (
                fir == "LPPC"
                or " LPPC " in f" {raw} "
                or "FIR LPPC" in raw
                or " LPPC FIR" in raw
            ):
                out.append(raw)
        return out
    except Exception:
        return []

# ---------- Gist helpers: GAMET & NOTAMs ----------

def _get_gamet_secrets():
    token = (
        st.secrets.get("GAMET_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or ""
    ).strip()
    gid = (st.secrets.get("GAMET_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
    fn = (
        st.secrets.get("GAMET_GIST_FILENAME")
        or st.secrets.get("GIST_FILENAME")
        or ""
    ).strip()
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
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
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


def save_gamet_to_gist(text: str) -> tuple[bool, str]:
    token, gid, fn = _get_gamet_secrets()
    if not all([token, gid, fn]):
        return False, "Faltam segredos do GAMET (TOKEN/ID/FILENAME)."
    try:
        payload = {
            "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
            "text": (text or "").strip(),
        }
        body = {"files": {fn: {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}}
        r = requests.patch(
            f"https://api.github.com/gists/{gid}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
            json=body,
            timeout=12,
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, "GAMET guardado no Gist."
    except Exception as e:
        return False, f"Erro a gravar GAMET no Gist: {e}"


# NOTAMs (apenas editor/Gist; **NÃO** entram no PDF detalhado)

def notam_gist_config_ok() -> bool:
    token = (
        st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or ""
    ).strip()
    gid = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
    fn = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
    return bool(token and gid and fn)


@st.cache_data(ttl=90)
def load_notams_from_gist() -> Dict[str, Any]:
    if not notam_gist_config_ok():
        return {"map": {}, "updated_utc": None}
    try:
        token = (
            st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or ""
        ).strip()
        gid = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
        fn = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
        r = requests.get(
            f"https://api.github.com/gists/{gid}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
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
    if not notam_gist_config_ok():
        return False, "Segredos NOTAM_GIST_* em falta."
    try:
        token = (
            st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or ""
        ).strip()
        gid = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
        fn = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
        payload = {
            "updated_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"),
            "map": {k: [s for s in v if str(s).strip()] for k, v in new_map.items()},
        }
        body = {"files": {fn: {"content": json.dumps(payload, ensure_ascii=False, indent=2)}}}
        r = requests.patch(
            f"https://api.github.com/gists/{gid}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
            },
            json=body,
            timeout=12,
        )
        if r.status_code >= 400:
            return False, f"GitHub respondeu {r.status_code}: {r.text}"
        return True, "NOTAMs guardados no Gist."
    except Exception as e:
        return False, f"Erro a gravar no Gist: {e}"

# ---------- GPT wrapper (texto) ----------

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

# ---------- Análises (PT) ----------

def analyze_chart_pt(kind: str, img_b64: str) -> str:
    try:
        model_name = st.secrets.get("OPENAI_MODEL_VISION", "gpt-4o").strip() or "gpt-4o"
    except Exception:
        model_name = "gpt-4o"
    # Prompt reforcado e extremamente descritivo
    sys = (
        "Es meteorologista aeronáutico sénior. Em PORTUGUÊS europeu, escreve SEM listas, em prosa rica e exaustiva,"
        " em 3 blocos: 1) Visão geral (válido/horário, sistemas frontais, centros de pressão com valores, cavados/ cristas,"
        " JS/jet core e isotacas, áreas de wx significativo, simbologia identificada e EXPLICADA sem omitir nada);"
        " 2) Portugal (continente) com foco em gradiente de pressão, vento nos baixos/altos níveis, nebulosidade por tipo"
        " (CB/TCU/AS/NS/SC/AC), precipitação (tipo/intensidade), trovoada/CB (isol/ocnl/frq, topos/FL), gelo (níveis/SEV/ MOD),"
        " turbulência (níveis e intensidade), isoterma 0°C e freezing level, visibilidade/IFR/MVFR/VFR e riscos orográficos);"
        " 3) Alentejo (detalha vento por altitude com conversão das barbas de vento para direção/velocidade em nós,"
        " rajadas, cisalhamento LLWS, teto/visibilidade, crosswind provável em pistas típicas 03/21 se dedutível, impactos em VFR baixo)."
        " Se o chart for SIGWX: explica todas as abreviaturas (ISOL/OCNL/FRQ, CB/TS, ICE MOD/SEV, TURB MOD/SEV, TC, SFC-FLxxx, etc.)."
        " Se for SPC/surface: comenta isóbaras, frentes (quente/fria/ocluída/estacionária), valores MSLP e deslocamentos."
        " Se for Wind & Temp: converte barbas e isotermas, comenta jatos, divergência/convergência, e níveis FL de referência."
        " NÃO inventes dados fora da imagem; se algo não estiver visível, diz explicitamente. Conclui cada bloco com impacto operacional."
    )
    user_txt = f"Tipo de chart: {kind}. Analisa tudo o que for visível na imagem."
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
            max_tokens=900,
            temperature=0.15,
        )
        out = (r.choices[0].message.content or "").strip()
        return ascii_safe(out) if out else "Analise indisponivel."
    except Exception as e:
        return ascii_safe(f"Analise indisponivel (erro IA: {e})")


def analyze_metar_taf_pt(icao: str, metar: str, taf: str) -> str:
    # Explicação completa, token a token, sem omitir códigos
    sys = (
        "Es meteorologista aeronáutico sénior. Em PT-PT e texto corrido (sem listas),"
        " interpreta e DECODIFICA integralmente METAR e TAF do aeródromo indicado."
        " Explica o significado de CADA grupo/código, por ordem de aparecimento, incluindo: estação/hora (Z), vento (dir/vel/rf/VRB),"
        " visibilidade (incl. CAVOK), RVR, tempo presente (intensidade + fenómenos), nebulosidade (quantidade/alturas/tipos CB/TCU),"
        " temperatura/ ponto de orvalho, QNH, tendências (NOSIG), var. de vento, probabilidades (PROB30/40), BECMG/TEMPO/FM/AT,"
        " níveis/horas de validade e remarks caso existam. Não omitas nenhum símbolo.")
    user = f"Aeródromo {icao}\n\nMETAR RAW:\n{metar}\n\nTAF RAW:\n{taf}\n\nTarefa: explicar todos os códigos e impactos operacionais VFR/IFR e vento cruzado se dedutível."
    return gpt_text(sys, user, max_tokens=1600)


def analyze_sigmet_pt(sigmet_text: str) -> str:
    if not sigmet_text.strip():
        return ""
    sys = (
        "Es meteorologista aeronáutico sénior. Em PT e prosa corrida, interpreta o SIGMET LPPC:"
        " fenómeno, área, níveis/FL, validade/horas, movimento/intensidade, e impacto operacional."
        " Explica a sigla de cada código que ocorra no texto."
    )
    return gpt_text(sys, sigmet_text, max_tokens=1200)


def analyze_gamet_pt(gamet_text: str) -> str:
    if not gamet_text.strip():
        return ""
    # Coordenadas oficiais do LPSO (ARP): 391242N 0080328W ≈ 39.2117N, -8.0578
    lpsolat = 39.2117
    lpsolon = -8.0578
    sys = (
        "Es meteorologista aeronáutico sénior. Em PT-PT e texto corrido (sem listas),"
        " explica EXAUSTIVAMENTE o GAMET do FIR LPPC: secções, fenómenos, códigos e níveis,"
        " validade, movimento, áreas com coordenadas e interpretação operacional. Não omitas nenhum código."
        " Quando existirem coordenadas/polígonos, avalia se abrangem ou não o aeródromo LPSO"
        f" (ARP ≈ {lpsolat:.4f}N, {lpsolon:.4f}W / 391242N 0080328W) e justifica geometricamente a conclusão."
        " Se a extensão geográfica não permitir certeza, descreve a incerteza."
    )
    return gpt_text(sys, gamet_text, max_tokens=1800)

# ---------- PDF helpers ----------
PASTEL = (90, 127, 179)  # azul suave


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
    y = pdf.get_y() + 6
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp, format="PNG")
        path = tmp.name
    pdf.image(path, x=x, y=y, w=w, h=h)
    os.remove(path)
    pdf.ln(h + 10)


def pdf_embed_pdf_pages(
    pdf: FPDF,
    pdf_bytes: bytes,
    title: str,
    orientation: str = "P",
    max_pages: int | None = None,
):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total = doc.page_count
    pages = range(total) if max_pages is None else range(min(total, max_pages))
    for i in pages:
        page = doc.load_page(i)
        png = page.get_pixmap(dpi=300).tobytes("png")
        img = Image.open(io.BytesIO(png)).convert("RGB")
        bio = io.BytesIO()
        img.save(bio, format="PNG")
        bio.seek(0)
        pdf.add_page(orientation=orientation)
        draw_header(pdf, ascii_safe(title + (f" — p.{i+1}" if total > 1 else "")))
        place_image_full(pdf, bio, max_h_pad=58)

# ---------- PDF classes ----------


class DetailedPDF(FPDF):
    def header(self):
        pass

    def footer(self):
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
            self.multi_cell(
                0, 7, ascii_safe(analysis or "Sem interpretacao."))
            self.ln(3)

    def sigmet_block(self, sigmet_text: str, analysis_pt: str):
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

    def gamet_block(self, gamet_text: str, analysis_pt: str):
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
        y = self.get_y() + 6
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG")
            path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h)
        os.remove(path)
        self.ln(h + 12)
        self.set_font("Helvetica", "", 12)
        self.multi_cell(0, 7, ascii_safe(analysis_pt or " "))


class FinalBriefPDF(FPDF):
    def header(self):
        pass

    def footer(self):
        pass

    def cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc):
        self.add_page(orientation="L")
        self.set_xy(0, 36)
        self.set_font("Helvetica", "B", 28)
        self.cell(0, 14, "Briefing", ln=True, align="C")
        self.ln(2)
        self.set_font("Helvetica", "", 13)
        self.cell(0, 8, ascii_safe(f"Mission: {mission_no}"), ln=True, align="C")
        if pilot or aircraft or callsign or reg:
            self.cell(
                0,
                8,
                ascii_safe(
                    f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}"
                ),
                ln=True,
                align="C",
            )
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

# ---------- Helper robusto: FPDF -> bytes (fpdf vs fpdf2) ----------

def fpdf_to_bytes(doc: FPDF) -> bytes:
    """Garante bytes tanto em PyFPDF (str) como em fpdf2 (bytes/bytearray)."""
    data = doc.output(dest="S")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    # PyFPDF (string Latin-1)
    return data.encode("latin-1")

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
    # Defaults atualizados
    pilot = st.text_input("Pilot name", "Alexandre Moiteiro")
    callsign = st.text_input("Mission callsign", "")
with colB:
    aircraft_type = st.text_input("Aircraft type", "Tecnam P2008")
    registration = st.text_input("Registration", "CS-XXX")
with colC:
    mission_no = st.text_input("Mission number", "")
    flight_date = st.date_input("Flight date")
    time_utc = st.text_input("UTC time", "")

# ---------- ICAOs ----------
st.markdown("#### Aerodromes")
c1, c2 = st.columns(2)
with c1:
    icaos_metar_str = st.text_input(
        "ICAO list for METAR/TAF (comma / space / newline)", value="LPPT LPBJ LEBZ"
    )
    icaos_metar = parse_icaos(icaos_metar_str)
with c2:
    icaos_notam_str = st.text_input(
        "ICAO list for NOTAMs (comma / space / newline)", value="LPSO LPCB LPEV"
    )
    icaos_notam = parse_icaos(icaos_notam_str)

# ---------- Editor de NOTAMs (por ICAO) ----------

st.markdown("### NOTAMs (editar e guardar)")
saved_notams = load_notams_from_gist()
existing_map: Dict[str, List[str]] = (
    saved_notams.get("map") or {}
) if isinstance(saved_notams, dict) else {}


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
            placeholder=(
                "Ex.: AERODROME BEACON ONLY FLASH-GREEN LIGHT OPERATIVE.\n"
                "FROM: 29th Jul 2025 15:10 TO: 29th Sep 2025 18:18 EST\n\n"
                "Outro NOTAM aqui..."
            ),
            key=f"ed_notam_{icao}",
            height=160,
        )

col_save_n = st.columns([0.4, 0.3, 0.3])
with col_save_n[0]:
    overwrite_all_n = st.checkbox(
        "Substituir TODOS os aerodromos do Gist (NOTAMs)", value=False
    )
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

st.divider()

# ---------- Editor de GAMET ----------

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

# ---------- Charts upload (MELHORADO) ----------

st.markdown("#### Charts")
st.caption(
    "Upload SIGWX / SPC / Wind & Temp. Accepts PDF/PNG/JPG/JPEG/GIF. O tipo, título e validade são sugeridos automaticamente a partir do ficheiro."
)
use_ai_for_charts = st.toggle("Analisar charts com IA", value=True, help="Marcado por omissao")
uploads = st.file_uploader(
    "Upload charts",
    type=["pdf", "png", "jpg", "jpeg", "gif"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)

charts: List[Dict[str, Any]] = []
if uploads:
    # ordenar alfabeticamente por nome para consistência inicial
    for idx, f in enumerate(sorted(uploads, key=lambda x: x.name.lower())):
        raw_bytes = f.getvalue()
        mime = f.type
        page_text = extract_text_from_bytes(raw_bytes, mime)
        kind_guess, title_guess, subtitle_guess, order_hint = guess_chart_kind_and_meta(
            f.name, page_text
        )
        img_png = ensure_png_bytes_from_buffer(raw_bytes, mime)

        st.markdown('<div class="chart-card">', unsafe_allow_html=True)
        top = st.columns([0.18, 0.22, 0.25, 0.2, 0.15])
        with top[0]:
            kind = st.selectbox(
                f"Chart type #{idx+1}",
                ["SIGWX", "SPC", "Wind & Temp", "Other"],
                index=["SIGWX", "SPC", "Wind & Temp", "Other"].index(kind_guess),
                key=f"kind_{idx}",
                help="Classificação sugerida automaticamente (pode alterar).",
            )
        with top[1]:
            title = st.text_input(
                "Title",
                value=title_guess,
                key=f"title_{idx}",
            )
        with top[2]:
            subtitle = st.text_input(
                "Subtitle (e.g., VALID ...)",
                value=subtitle_guess,
                key=f"subtitle_{idx}",
            )
        with top[3]:
            order = st.number_input(
                "Order",
                min_value=1,
                max_value=999,
                value=order_hint + idx,
                key=f"order_{idx}",
                help="Define a posição no PDF Final.",
            )
        with top[4]:
            st.write("\u00A0")
            st.image(img_png, caption=f.name, use_column_width=True)

        st.markdown('</div>', unsafe_allow_html=True)
        charts.append(
            {
                "kind": kind,
                "title": title,
                "subtitle": subtitle,
                "img_png": img_png,
                "order": int(order),
            }
        )

    # ordenar conforme o campo "order" escolhido pelo utilizador
    charts.sort(key=lambda c: c.get("order", 9999))

# ---------- Generate Detailed (PT) ----------


def analyze_notams_text_only(icao: str, notams_raw: List[str]) -> str:
    return "\n\n".join(notams_raw).strip() or "Sem NOTAMs."


st.markdown("### PDFs")
col_pdfs = st.columns(2)
with col_pdfs[0]:
    gen_det = st.button("Generate Detailed (PT)")

if 'gen_det' in locals() and gen_det:
    # METAR/TAF (RAW + interpretação detalhada)
    metar_analyses: List[Tuple[str, str, str, str]] = []
    for icao in icaos_metar:
        metar_raw = fetch_metar_now(icao) or ""
        taf_raw = fetch_taf_now(icao) or ""
        analysis = (
            analyze_metar_taf_pt(icao, metar_raw, taf_raw)
            if (metar_raw or taf_raw)
            else "Sem METAR/TAF disponiveis neste momento."
        )
        metar_analyses.append((icao, metar_raw, taf_raw, analysis))

    # SIGMET
    sigmets = fetch_sigmet_lppc_auto()
    sigmet_text = "\n\n---\n\n".join(sigmets).strip()
    sigmet_analysis = analyze_sigmet_pt(sigmet_text) if sigmet_text else ""

    # GAMET (usar o texto do editor; se vazio, cair para Gist)
    gamet_for_pdf = (gamet_text or _gamet_initial or "").strip()
    gamet_analysis = analyze_gamet_pt(gamet_for_pdf) if gamet_for_pdf else ""

    # Build PDF Detalhado **sem cover** e **sem NOTAMs**
    det_pdf = DetailedPDF()
    det_pdf.metar_taf_block(metar_analyses)
    if sigmet_text:
        det_pdf.sigmet_block(sigmet_text, sigmet_analysis)
    if gamet_for_pdf:
        det_pdf.gamet_block(gamet_for_pdf, gamet_analysis)

    # Charts com analise IA (por omissão ON)
    for ch in charts:
        title, subtitle, img_png, kind = (
            ch["title"],
            ch["subtitle"],
            ch["img_png"],
            ch["kind"],
        )
        analysis_txt = ""
        if use_ai_for_charts:
            try:
                analysis_txt = analyze_chart_pt(kind, b64_png(img_png))
            except Exception:
                analysis_txt = "Analise indisponivel."
        det_pdf.chart_block(title, subtitle, img_png, analysis_txt)

    det_name = f"Briefing Detalhado - Missao {mission_no or 'X'}.pdf"
    det_pdf.output(det_name)
    with open(det_name, "rb") as f:
        st.download_button(
            "Download Detailed (PT)",
            data=f.read(),
            file_name=det_name,
            mime="application/pdf",
            use_container_width=True,
        )

# ---------- Optional Flight Plan & M&B PDFs ----------

st.markdown("#### Flight Plan (optional image/PDF/GIF)")
fp_upload = st.file_uploader(
    "Upload your flight plan (PDF/PNG/JPG/JPEG/GIF)",
    type=["pdf", "png", "jpg", "jpeg", "gif"],
    accept_multiple_files=False,
)
# Guardar bytes e tipo para permitir inserção de PDF original no briefing final
fp_bytes: bytes | None = None
fp_mime: str | None = None
fp_img_png: io.BytesIO | None = None
if fp_upload:
    fp_bytes = fp_upload.getvalue()
    fp_mime = fp_upload.type
    if (fp_mime or "").lower() == "application/pdf":
        st.success("Flight plan em PDF será embutido no briefing final (páginas originais).")
    else:
        fp_img_png = ensure_png_bytes_from_buffer(fp_bytes, fp_mime or "")
        st.success("Flight plan (imagem) será incluído no briefing (página vertical).")

st.markdown("#### M&B / Performance PDF (from external app)")
mb_upload = st.file_uploader(
    "Upload M&B/Performance PDF to embed (todas as páginas)",
    type=["pdf"],
    accept_multiple_files=False,
)

# ---------- Generate Final Briefing (EN) ----------

with col_pdfs[1]:
    gen_final = st.button("Generate Final Briefing (EN)")

if 'gen_final' in locals() and gen_final:
    fb = FinalBriefPDF()
    fb.cover(mission_no, pilot, aircraft_type, callsign, registration, str(flight_date), time_utc)

    # Flight plan: se for imagem, adiciona já; se for PDF, será inserido após gerar bytes
    if fp_img_png is not None:
        fb.flightplan_image_portrait("Flight Plan", fp_img_png)

    if 'charts' in locals():
        fb.charts_only([(c["title"], c["subtitle"], c["img_png"]) for c in charts])

    # Exporta briefing base (robusto para fpdf/fpdf2)
    fb_bytes: bytes = fpdf_to_bytes(fb)
    final_bytes = fb_bytes

    # Abrir documento principal no PyMuPDF para inserir PDFs originais (FP e M&B)
    try:
        main = fitz.open(stream=fb_bytes, filetype="pdf")
        # Inserir Flight Plan PDF (todas as páginas) se aplicável
        if fp_bytes is not None and (fp_mime or "").lower() == "application/pdf":
            fp_doc = fitz.open(stream=fp_bytes, filetype="pdf")
            if fp_doc.page_count > 0:
                main.insert_pdf(fp_doc, from_page=0, to_page=fp_doc.page_count - 1)
            fp_doc.close()
        # Inserir M&B PDF (todas as páginas, sem limite)
        if mb_upload is not None:
            mb_bytes = mb_upload.getvalue()
            mb = fitz.open(stream=mb_bytes, filetype="pdf")
            if mb.page_count > 0:
                main.insert_pdf(mb, from_page=0, to_page=mb.page_count - 1)
            mb.close()
        final_bytes = main.tobytes()
        main.close()
    except Exception:
        # fallback: fica só o briefing sem inserções extras
        final_bytes = fb_bytes

    final_name = f"Briefing - Missao {mission_no or 'X'}.pdf"
    st.download_button(
        "Download Final Briefing (EN)",
        data=final_bytes,
        file_name=final_name,
        mime="application/pdf",
        use_container_width=True,
    )

st.divider()








