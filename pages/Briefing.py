# pages/Briefing.py
import os
import io
import base64
import tempfile
import unicodedata
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import requests
from PIL import Image
import streamlit as st
from fpdf import FPDF

# Optional (only needed if you upload PDFs as charts)
import fitz  # PyMuPDF

# IA (OpenAI Responses API)
from openai import OpenAI


# ──────────────────────────────────────────────────────────────────────────────
# Config & constants
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Full Briefing", layout="wide")
st.title("Full Briefing — RAW (EN) & DETAILED (PT)")

CHECKWX_KEY = st.secrets.get("CHECKWX_API_KEY") or os.getenv("CHECKWX_API_KEY")
OPENAI_KEY = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
PUBLIC_URL = st.secrets.get("PUBLIC_URL") or os.getenv("PUBLIC_URL") or "https://briefings.streamlit.app"

client: Optional[OpenAI] = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

CHECKWX_BASE = "https://api.checkwx.com/"
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def ascii_safe(text: Any) -> str:
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")


@st.cache_data(ttl=120)
def fetch_checkwx(endpoint: str) -> List[Any]:
    headers = {"X-API-Key": CHECKWX_KEY} if CHECKWX_KEY else {}
    try:
        r = requests.get(CHECKWX_BASE + endpoint, headers=headers, timeout=12)
        r.raise_for_status()
        j = r.json()
        return j.get("data", []) or []
    except Exception as e:
        return [f"Error: {e}"]


AZORES = {"LPAZ", "LPLA", "LPPD", "LPPI", "LPFL", "LPHR", "LPGR", "LPSJ"}
PREFIX_FIR = {"LP": "LPPC", "LE": "LECM", "LF": "LFFF", "EG": "EGTT", "EI": "EISN", "ED": "EDGG", "LI": "LIRR"}


def icao_to_fir(icao: str) -> str:
    if not icao or len(icao) != 4:
        return "LPPC"
    icao = icao.upper()
    if icao.startswith("LP"):
        return "LPPO" if icao in AZORES else "LPPC"
    return PREFIX_FIR.get(icao[:2], "LPPC")


def get_image_bytes(uploaded_file) -> io.BytesIO:
    """Return image bytes (PNGs) from uploaded file (image or first page of PDF)."""
    data = uploaded_file.read()
    if uploaded_file.type == "application/pdf":
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            pix = doc.load_page(0).get_pixmap()
            return io.BytesIO(pix.tobytes("png"))
        except Exception:
            # As a fallback, return the raw bytes (FPDF will likely skip)
            return io.BytesIO(data)
    else:
        return io.BytesIO(data)


def b64_png(img_bytes: io.BytesIO) -> str:
    return base64.b64encode(img_bytes.getvalue()).decode("utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# IA detailed analysis (Português)
# ──────────────────────────────────────────────────────────────────────────────
def ai_analyze_detailed_pt(
    metar_texts: List[str],
    taf_texts: List[str],
    sigmet_texts: List[str],
    chart_images: List[io.BytesIO],
) -> str:
    if not client:
        return "OpenAI key ausente. Define OPENAI_API_KEY em secrets para gerar a análise detalhada."

    system = (
        "És um meteorologista aeronáutico sénior. Explica detalhadamente e de forma didática em português "
        "o conteúdo dos charts e das mensagens METAR/TAF/SIGMET fornecidas. Usa texto corrido, liga conceitos, "
        "e destaca impactos operacionais. Se algo não for legível, assinala explicitamente e não inventes."
    )

    parts = []
    if metar_texts:
        parts.append("METAR RAW:\n" + "\n".join(metar_texts))
    if taf_texts:
        parts.append("TAF RAW:\n" + "\n".join(taf_texts))
    if sigmet_texts:
        parts.append("SIGMET RAW:\n" + "\n".join(sigmet_texts))
    if chart_images:
        parts.append("ANALISAR AS IMAGENS ANEXAS (charts).")

    user_text = "\n\n".join(parts) or "Sem mensagens textuais; analisa apenas as imagens dos charts."

    content = [{"type": "input_text", "text": user_text}]
    for img_b in chart_images:
        content.append({"type": "input_image", "image_data": b64_png(img_b), "mime_type": "image/png"})

    try:
        resp = client.responses.create(
            model="gpt-5",
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system}]},
                {"role": "user", "content": content},
            ],
            max_output_tokens=1500,
            temperature=0.12,
        )
        return (resp.output_text or "").strip()
    except Exception as e:
        return f"Erro IA: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# PDF builders
# ──────────────────────────────────────────────────────────────────────────────
class PDFRaw(FPDF):
    def header(self) -> None:
        pass

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Arial", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, ascii_safe(f"Page {self.page_no()}"), align="C")

    def cover(self, icaos: List[str]) -> None:
        self.add_page()
        self.set_font("Arial", "B", 18)
        self.cell(0, 12, "Flight Briefing — RAW (English)", ln=True, align="C")
        self.ln(4)
        self.set_font("Arial", "", 11)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        self.cell(0, 8, ascii_safe(f"Generated: {now}"), ln=True, align="C")
        self.ln(4)
        self.set_font("Arial", "", 12)
        icaos_q = ",".join(icaos)
        # Link points to your Weather page with live data
        live_url = f"{PUBLIC_URL}/Weather?icao={icaos_q}"
        self.multi_cell(0, 7, ascii_safe(f"Current METAR, TAF & SIGMET (live): {live_url}"))
        self.ln(2)
        self.multi_cell(0, 7, "Charts (static/longer validity) are embedded below. No interpretations included in RAW.")


class PDFDetailed(FPDF):
    def header(self) -> None:
        pass

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Arial", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, ascii_safe(f"Página {self.page_no()}"), align="C")

    def cover(self) -> None:
        self.add_page()
        self.set_font("Arial", "B", 18)
        self.cell(0, 12, "Briefing Detalhado — Português", ln=True, align="C")
        self.ln(4)
        self.set_font("Arial", "", 11)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        self.cell(0, 8, ascii_safe(f"Gerado: {now}"), ln=True, align="C")
        self.ln(6)
        self.set_font("Arial", "", 12)
        self.multi_cell(0, 7, "Documento para estudo pré-briefing: inclui interpretações e contexto, em português.")


def raw_pdf_bytes(icaos: List[str], chart_files) -> bytes:
    pdf = PDFRaw()
    pdf.set_auto_page_break(True, margin=15)
    pdf.cover(icaos)

    # Charts section
    pdf.set_font("Arial", "B", 14)
    pdf.ln(4)
    pdf.cell(0, 8, "Charts:", ln=True)

    for f in (chart_files or []):
        try:
            img_b = get_image_bytes(f)
            img = Image.open(img_b).convert("RGB")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                img.save(tmp, format="PNG")
                tmp_path = tmp.name
            pdf.add_page(orientation="L")
            # fit page
            pdf.image(tmp_path, x=10, y=18, w=pdf.w - 20)
            os.remove(tmp_path)
        except Exception:
            pdf.add_page()
            pdf.set_font("Arial", "", 11)
            pdf.multi_cell(0, 7, "(Failed to insert chart image)")

    return pdf.output(dest="S").encode("latin-1", errors="replace")


def detailed_pdf_bytes(
    icaos: List[str],
    chart_files,
    metar_texts: List[str],
    taf_texts: List[str],
    sigmet_texts: List[str],
    ai_text: str,
) -> bytes:
    pdf = PDFDetailed()
    pdf.set_auto_page_break(True, margin=15)
    pdf.cover()

    # Raw references (timestamped snapshot)
    if metar_texts:
        pdf.set_font("Arial", "B", 13)
        pdf.cell(0, 8, "METAR (registos no momento da geração):", ln=True)
        pdf.set_font("Arial", "", 11)
        for t in metar_texts:
            pdf.multi_cell(0, 7, ascii_safe(t))
        pdf.ln(2)

    if taf_texts:
        pdf.set_font("Arial", "B", 13)
        pdf.cell(0, 8, "TAF (registos no momento da geração):", ln=True)
        pdf.set_font("Arial", "", 11)
        for t in taf_texts:
            pdf.multi_cell(0, 7, ascii_safe(t))
        pdf.ln(2)

    if sigmet_texts:
        pdf.set_font("Arial", "B", 13)
        pdf.cell(0, 8, "SIGMET (decodificado/RAW):", ln=True)
        pdf.set_font("Arial", "", 11)
        for t in sigmet_texts:
            pdf.multi_cell(0, 7, ascii_safe(t))
        pdf.ln(4)

    # IA Analysis
    pdf.set_font("Arial", "B", 13)
    pdf.cell(0, 8, "Análise IA (PT):", ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(0, 7, ascii_safe(ai_text or "(sem análise)"))

    # Chart pages
    for f in (chart_files or []):
        try:
            img_b = get_image_bytes(f)
            img = Image.open(img_b).convert("RGB")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                img.save(tmp, format="PNG")
                tmp_path = tmp.name
            pdf.add_page(orientation="L")
            pdf.image(tmp_path, x=10, y=18, w=pdf.w - 20)
            os.remove(tmp_path)
        except Exception:
            pdf.add_page()
            pdf.set_font("Arial", "", 11)
            pdf.multi_cell(0, 7, "(Falha ao inserir imagem do chart)")

    return pdf.output(dest="S").encode("latin-1", errors="replace")


# ──────────────────────────────────────────────────────────────────────────────
# UI — inputs (no sidebar)
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("ICAO(s)")
if "icaos" not in st.session_state:
    st.session_state.icaos = DEFAULT_ICAOS.copy()

def on_icao_change():
    raw = st.session_state["icaos_input"]
    icaos = [x.strip().upper() for x in raw.split(",") if x.strip()]
    st.session_state.icaos = icaos or DEFAULT_ICAOS
    # Auto-fetch snapshot upon change
    snapshot_fetch()

st.text_input(
    "Enter ICAO(s), comma separated",
    value=", ".join(st.session_state.icaos),
    key="icaos_input",
    on_change=on_icao_change,
)

st.subheader("Upload charts (optional)")
uploaded_files = st.file_uploader(
    "Upload image or PDF (first page). You can select multiple files.",
    type=["png", "jpg", "jpeg", "pdf"],
    accept_multiple_files=True,
)

# ──────────────────────────────────────────────────────────────────────────────
# Fetch section (auto and manual)
# ──────────────────────────────────────────────────────────────────────────────
def snapshot_fetch():
    """Fetch METAR, TAF, SIGMET snapshot for current ICAOs."""
    fetched: Dict[str, Dict[str, str]] = {}
    for icao in st.session_state.icaos:
        m = fetch_checkwx(f"metar/{icao}")
        t = fetch_checkwx(f"taf/{icao}")
        metar_raw = m[0] if m else ""
        taf_raw = t[0] if t else ""
        fetched[icao] = {"metar": metar_raw, "taf": taf_raw}

    # SIGMET by FIRs
    firs = {icao_to_fir(i) for i in st.session_state.icaos}
    sigmets: List[str] = []
    for fir in firs:
        sdat = fetch_checkwx(f"sigmet/{fir}/decoded")
        if sdat:
            for item in sdat:
                if isinstance(item, dict):
                    raw = item.get("raw") or item.get("raw_text") or item.get("report") or ""
                    if raw:
                        sigmets.append(raw)
                else:
                    sigmets.append(str(item))
    st.session_state["snapshot"] = {"metar": fetched, "sigmet": sigmets}

# initial fetch if none
if "snapshot" not in st.session_state:
    snapshot_fetch()

if st.button("Refresh snapshot"):
    st.cache_data.clear()
    snapshot_fetch()
    st.success("Snapshot atualizado.")


# ──────────────────────────────────────────────────────────────────────────────
# Show snapshot
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("Snapshot (for your review)")
snap = st.session_state.get("snapshot", {})
for icao in st.session_state.icaos:
    with st.expander(f"{icao}", expanded=False):
        metar_text = snap.get("metar", {}).get(icao, {}).get("metar", "")
        taf_text = snap.get("metar", {}).get(icao, {}).get("taf", "")
        st.markdown(f"**METAR:**  \n`{metar_text}`")
        st.markdown(f"**TAF:**  \n`{taf_text}`")

with st.expander("SIGMET (by inferred FIRs)", expanded=False):
    sigs = snap.get("sigmet", []) or []
    if sigs:
        for s in sigs:
            st.code(s)
    else:
        st.write("No SIGMET at this time.")


# ──────────────────────────────────────────────────────────────────────────────
# Actions — Generate PDFs
# ──────────────────────────────────────────────────────────────────────────────
colA, colB = st.columns(2)

with colA:
    if st.button("Generate RAW PDF (English, link to live weather)"):
        pdf_bytes = raw_pdf_bytes(st.session_state.icaos, uploaded_files)
        st.download_button(
            "Download RAW PDF",
            data=pdf_bytes,
            file_name="briefing_raw.pdf",
            mime="application/pdf",
        )

with colB:
    if st.button("Generate DETAILED PDF (Português, IA)"):
        # build text lists from snapshot
        metar_list: List[str] = []
        taf_list: List[str] = []
        for icao in st.session_state.icaos:
            metar_list.append(snap.get("metar", {}).get(icao, {}).get("metar", ""))
            taf_list.append(snap.get("metar", {}).get(icao, {}).get("taf", ""))

        sigmet_list: List[str] = snap.get("sigmet", []) or []

        # prepare images for IA
        chart_imgs: List[io.BytesIO] = []
        for f in (uploaded_files or []):
            try:
                chart_imgs.append(get_image_bytes(f))
            except Exception:
                pass

        with st.spinner("A gerar análise detalhada (IA)..."):
            ai_text = ai_analyze_detailed_pt(metar_list, taf_list, sigmet_list, chart_imgs)

        pdf_bytes = detailed_pdf_bytes(st.session_state.icaos, uploaded_files, metar_list, taf_list, sigmet_list, ai_text)
        st.download_button(
            "Download DETAILED PDF",
            data=pdf_bytes,
            file_name="briefing_detailed.pdf",
            mime="application/pdf",
        )
        st.success("Detailed PDF gerado (com análise IA em PT).")
