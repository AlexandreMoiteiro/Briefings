
# app.py — Briefings (reworked)
# - No AI features (removed all GPT usage and "Detailed" report)
# - Single PDF output (landscape pages)
# - Cleaner UI, better chart titles, and logical PDF structure
# - NOTAMs now uploaded as a PDF and embedded/merged into the final PDF
# - First page contains hyperlinks: (1) IPMA portal for METAR/TAF/SIGMET/GAMET,
#   (2) internal links to each section inside the PDF

from typing import Dict, Any, List, Tuple, Optional
import io, os, re, tempfile
import datetime as dt

import streamlit as st
from PIL import Image
from fpdf import FPDF
import fitz  # PyMuPDF
import requests

# ---------- External pages (updated) ----------
IPMA_PORTAL_URL = "https://brief-ng.ipma.pt/#showLogin"  # New weather portal

# ---------- Página & estilos ----------
st.set_page_config(page_title="Briefings", layout="wide")
st.markdown(
    """
<style>
:root { --muted:#6b7280; --line:#e5e7eb; --ink:#111827; --bg:#ffffff; --card:#ffffff; --accent:#0ea5e9; }
.app-top { display:flex; align-items:center; justify-content:space-between; gap:.75rem; flex-wrap:wrap; margin-bottom:.35rem; }
.brand { display:flex; align-items:center; gap:.6rem; }
.app-title { font-size: 2.2rem; font-weight: 800; margin: 0; }
.small { font-size:.92rem; color:var(--muted); }
hr{border:none;border-top:1px solid var(--line);margin:12px 0}
.section-card{border:1px solid var(--line); border-radius:14px; padding:14px 16px; background:var(--card); box-shadow:0 1px 2px rgba(0,0,0,.03)}
.block-label{font-weight:700;margin:.25rem 0}
.kv{display:grid;grid-template-columns:180px 1fr; gap:.35rem .8rem}
.kv .k{color:#374151}
.kv .v{color:var(--ink)}
.btnbar a{display:inline-block;padding:7px 12px;border:1px solid var(--line);border-radius:10px;text-decoration:none;font-weight:600;color:var(--ink);background:#f8fafc}
.btnbar a:hover{background:#eef3f8}
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stSidebarCollapseButton"], header [data-testid="baseButton-headerNoPadding"] { display:none !important; }
</style>
""",
    unsafe_allow_html=True,
)

PASTEL = (14, 165, 233)  # accent for headings

# ---------- Utils ----------
def safe_str(x) -> str:
    return "" if x is None or x is Ellipsis else str(x)

def parse_icaos(s: str) -> List[str]:
    tokens = re.split(r"[,\s]+", (s or "").strip())
    return [t.upper() for t in tokens if t]

def read_upload_bytes(upload) -> bytes:
    try:
        return upload.getvalue() if hasattr(upload, "getvalue") else upload.read()
    except Exception:
        return b""

# ---------- METAR/TAF (CheckWX) ----------
def _cw_headers() -> Dict[str, str]:
    key = st.secrets.get("CHECKWX_API_KEY", "\n").strip()
    return {"X-API-Key": key} if key else {}

@st.cache_data(ttl=90)
def fetch_metar_now(icao: str) -> str:
    try:
        hdr = _cw_headers()
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

@st.cache_data(ttl=90)
def fetch_taf_now(icao: str) -> str:
    try:
        hdr = _cw_headers()
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

# ---------- Imaging helpers ----------

def _ensure_rgb_png_bytes(file_bytes: bytes) -> bytes:
    """Best-effort: open any common image and return PNG RGB bytes."""
    try:
        img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        out = io.BytesIO(); img.save(out, format="PNG"); out.seek(0)
        return out.getvalue()
    except Exception:
        return file_bytes

def pdf_page_to_pngs(pdf_bytes: bytes, dpi: int = 300) -> List[bytes]:
    out: List[bytes] = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for i in range(doc.page_count):
        pix = doc.load_page(i).get_pixmap(dpi=dpi)
        out.append(pix.tobytes("png"))
    doc.close()
    return out

# ---------- Chart helpers ----------
CHART_TYPES = [
    "Significant Weather (SIGWX)",
    "Surface Pressure (MSLP)",
    "Winds & Temperatures Aloft",
    "Other",
]

_KIND_RANK = {"Surface Pressure (MSLP)": 1, "Significant Weather (SIGWX)": 2, "Winds & Temperatures Aloft": 3, "Other": 9}

def _chart_sort_key(c: Dict[str, Any]) -> Tuple[int, int]:
    kind = c.get("kind", "Other")
    rank = _KIND_RANK.get(kind, 9)
    order = int(c.get("order", 9999) or 9999)
    return (rank, order)

# ---------- PDF generator (all landscape) ----------
class BriefPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="L", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=10)
        self.toc_links: Dict[str, int] = {}  # map section -> link id

    # simple header/footer (kept minimal)
    def header(self):
        pass
    def footer(self):
        pass

    def _hline(self):
        self.set_draw_color(229, 231, 235)
        self.set_line_width(0.3)
        y = self.get_y()
        self.line(10, y, self.w - 10, y)

    def heading(self, text: str):
        self.set_text_color(*PASTEL)
        self.set_font("Helvetica", "B", 20)
        self.cell(0, 12, text, ln=True, align="C")
        self.set_text_color(0, 0, 0)

    def subheading(self, text: str, center: bool = True):
        self.set_font("Helvetica", "I", 12)
        self.cell(0, 8, text, ln=True, align=("C" if center else "L"))

    def add_cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc, icaos_metar: List[str]):
        self.add_page("L")
        self.heading("Briefing")
        self.ln(2)
        self.set_font("Helvetica", "", 13)
        info = []
        if mission_no:
            info.append(f"Mission: {mission_no}")
        if pilot or aircraft or callsign or reg:
            info.append(f"Pilot: {pilot}   Aircraft: {aircraft}   Callsign: {callsign}   Reg: {reg}")
        if date_str or time_utc:
            info.append(f"Date: {date_str}   UTC: {time_utc}")
        for line in info:
            self.cell(0, 8, line, ln=True, align="C")
        self.ln(3)

        # Table of contents shortcuts
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 9, "Quick Links", ln=True, align="L")
        self.set_font("Helvetica", "", 12)

        # Create internal links for sections
        for key in ["SECTION_METAR_TAF_SIGMET_GAMET", "SECTION_WEATHER_CHARTS", "SECTION_NAV_VFR", "SECTION_FLIGHT_PLAN", "SECTION_MASS_BALANCE", "SECTION_NOTAMS"]:
            self.toc_links[key] = self.add_link()

        # 1) External link (IPMA) + internal jump to section
        self.set_text_color(0, 102, 204)
        self.cell(0, 7, "METARs / TAFs / SIGMET / GAMET — IPMA Portal", ln=True, link=IPMA_PORTAL_URL)
        self.set_text_color(0, 0, 0)
        self.cell(0, 6, "(Go to section in this PDF)", ln=True, link=self.toc_links["SECTION_METAR_TAF_SIGMET_GAMET"])
        self.ln(2)

        # 2) Weather Charts (internal)
        self.set_text_color(0, 102, 204)
        self.cell(0, 7, "Weather Charts (SIGWX / MSLP / Winds Aloft)", ln=True, link=self.toc_links["SECTION_WEATHER_CHARTS"])
        self.set_text_color(0, 0, 0)

        # Then the rest
        self.cell(0, 7, "Navlog & VFR by Route", ln=True, link=self.toc_links["SECTION_NAV_VFR"])
        self.cell(0, 7, "Flight Plan", ln=True, link=self.toc_links["SECTION_FLIGHT_PLAN"])
        self.cell(0, 7, "Mass & Balance", ln=True, link=self.toc_links["SECTION_MASS_BALANCE"])
        self.cell(0, 7, "NOTAMs (embedded PDF)", ln=True, link=self.toc_links["SECTION_NOTAMS"])

        self.ln(4)
        self.set_font("Helvetica", "", 11)
        if icaos_metar:
            self.multi_cell(0, 6, "ICAOs for METAR/TAF: " + ", ".join(icaos_metar))

    def _place_image_full(self, img_bytes: bytes):
        # Scale an image to fit a landscape A4 page with margins
        max_w, max_h = self.w - 20, self.h - 20
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        iw, ih = img.size
        r = min(max_w / iw, max_h / ih)
        w, h = int(iw * r), int(ih * r)
        x, y = (self.w - w) / 2, (self.h - h) / 2
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, "PNG"); path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h)
        try:
            os.remove(path)
        except Exception:
            pass

    def section_metar_taf_sigmet_gamet(self, icaos: List[str], sigmet_text: str, gamet_text: str):
        self.add_page("L"); self.set_link(self.toc_links.get("SECTION_METAR_TAF_SIGMET_GAMET", 0))
        self.heading("METAR / TAF / SIGMET / GAMET")
        self.subheading("Source: IPMA Portal (external) • This section shows raw text for your selected aerodromes")
        self.ln(2)
        self.set_font("Helvetica", "", 12)
        # METAR/TAF raw
        for icao in icaos:
            metar_raw = fetch_metar_now(icao) or ""
            taf_raw = fetch_taf_now(icao) or ""
            if not (metar_raw or taf_raw):
                continue
            self.set_font("Helvetica", "B", 13)
            self.cell(0, 8, icao, ln=True)
            if metar_raw:
                self.set_font("Helvetica", "B", 12); self.cell(0, 7, "METAR:", ln=True)
                self.set_font("Helvetica", "", 12); self.multi_cell(0, 6, metar_raw)
            if taf_raw:
                self.set_font("Helvetica", "B", 12); self.cell(0, 7, "TAF:", ln=True)
                self.set_font("Helvetica", "", 12); self.multi_cell(0, 6, taf_raw)
            self.ln(1)
        # SIGMET / GAMET raw
        if sigmet_text.strip():
            self.set_font("Helvetica", "B", 12); self.cell(0, 7, "SIGMET (RAW):", ln=True)
            self.set_font("Helvetica", "", 12); self.multi_cell(0, 6, sigmet_text.strip()); self.ln(2)
        if gamet_text.strip():
            self.set_font("Helvetica", "B", 12); self.cell(0, 7, "GAMET (RAW):", ln=True)
            self.set_font("Helvetica", "", 12); self.multi_cell(0, 6, gamet_text.strip()); self.ln(2)

    def section_weather_charts(self, charts: List[Tuple[str, str, bytes]]):
        # charts: list of (title, subtitle, PNG bytes)
        if not charts:
            return
        for idx, (title, subtitle, img_png) in enumerate(charts):
            self.add_page("L")
            if idx == 0:
                self.set_link(self.toc_links.get("SECTION_WEATHER_CHARTS", 0))
            self.heading(title or "Weather Chart")
            if subtitle:
                self.subheading(subtitle)
            self._place_image_full(img_png)

    def section_pairs(self, pairs_png_pages: List[bytes]):
        if not pairs_png_pages:
            return
        for i, page_png in enumerate(pairs_png_pages):
            self.add_page("L")
            if i == 0:
                self.set_link(self.toc_links.get("SECTION_NAV_VFR", 0))
            self.heading("Navlog & VFR by Route")
            self._place_image_full(page_png)

    def section_single_block(self, title: str, pages_png: List[bytes], toc_key: str):
        if not pages_png:
            return
        for i, page_png in enumerate(pages_png):
            self.add_page("L")
            if i == 0:
                self.set_link(self.toc_links.get(toc_key, 0))
            self.heading(title)
            self._place_image_full(page_png)

# ---------- UI topo ----------
st.markdown(
    f'''<div class="app-top">
      <div class="brand">
        <div class="app-title">Briefings</div>
      </div>
      <span class="btnbar">
        <a href="{IPMA_PORTAL_URL}" target="_blank">IPMA Portal</a>
      </span>
    </div>''',
    unsafe_allow_html=True,
)

# ---------- Abas ----------
(
    tab_mission,
    tab_met_sig_gam,
    tab_charts,
    tab_pairs,
    tab_fpmb,
    tab_notams,
    tab_generate,
) = st.tabs([
    "Missão",
    "METAR/TAF & SIGMET/GAMET",
    "Weather Charts",
    "Navlog ↔ VFR (Rotas)",
    "Flight Plan & M&B",
    "NOTAMs (PDF)",
    "Gerar PDF",
])

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

    st.markdown("#### Aerodromes (METAR/TAF)")
    icaos_metar_str = st.text_input(
        "ICAO list (comma / space / newline)", value="LPPT LPBJ LEBZ"
    )
    icaos_metar = parse_icaos(icaos_metar_str)

# ---------- METAR/TAF + SIGMET/GAMET (raw text areas) ----------
with tab_met_sig_gam:
    st.markdown("### METAR/TAF & SIGMET/GAMET (RAW)")
    st.caption("Os METAR/TAF são obtidos via CheckWX se tiveres a API key nas secrets. SIGMET/GAMET insere manualmente.")
    col1, col2 = st.columns(2)
    with col1:
        sigmet_text = st.text_area("SIGMET (LPPC) — texto integral", height=180, placeholder="Ex.: LPPC SIGMET 2 VALID 12/09Z-12/13Z ...")
    with col2:
        gamet_text = st.text_area("GAMET — texto integral", height=180, placeholder="Ex.: LPPC FIR GAMET VALID 12/06Z-12/12Z\n...")

# ---------- Charts ----------
with tab_charts:
    st.markdown("### Weather Charts (SIGWX / MSLP / Winds Aloft / Other)")
    st.caption("Aceita PDF/PNG/JPG/JPEG/GIF. Para PDF convertemos cada página para imagem.")
    preview_w = st.slider("Largura da pré-visualização (px)", min_value=240, max_value=640, value=420, step=10)
    uploads = st.file_uploader("Upload charts", type=["pdf","png","jpg","jpeg","gif"], accept_multiple_files=True, label_visibility="collapsed")

    charts: List[Dict[str,Any]] = []
    if uploads:
        for idx, f in enumerate(uploads):
            raw = read_upload_bytes(f)
            name = safe_str(getattr(f, "name", "")) or f"chart_{idx+1}"
            kind = st.selectbox(
                f"Tipo do chart #{idx+1}", CHART_TYPES, index=0, key=f"kind_{idx}"
            )
            # Better default titles per type
            default_title = {
                "Significant Weather (SIGWX)": "Significant Weather (SIGWX)",
                "Surface Pressure (MSLP)": "Surface Pressure (MSLP)",
                "Winds & Temperatures Aloft": "Winds & Temperatures Aloft",
            }.get(kind, "Weather Chart")
            title = st.text_input("Título", value=default_title, key=f"title_{idx}")
            subtitle = st.text_input("Subtítulo (opcional)", value="", key=f"subtitle_{idx}")
            order_val = st.number_input("Ordem", min_value=1, max_value=(len(uploads)+10), value=(idx+1), step=1, key=f"ord_{idx}")

            # Preview
            if (f.type or "").lower() == "application/pdf":
                pages = pdf_page_to_pngs(raw, dpi=180)
                if pages:
                    st.image(pages[0], caption=f"{name} (p.1)", width=preview_w)
                    charts.append({"kind": kind, "title": title, "subtitle": subtitle, "pages": pages, "order": order_val})
            else:
                png = _ensure_rgb_png_bytes(raw)
                st.image(png, caption=name, width=preview_w)
                charts.append({"kind": kind, "title": title, "subtitle": subtitle, "pages": [png], "order": order_val})

# ---------- Navlog ↔ VFR (pares por ROTA) ----------
with tab_pairs:
    st.markdown("### Emparelhamento Navlog ↔ VFR por ROTA")
    st.caption("Para cada ROTA (ex.: LPSO-LPCB, LPSO-LPEV) carrega um Navlog e o respetivo mapa VFR. Aceita PDF/PNG/JPG/JPEG/GIF.")
    num_pairs = st.number_input("Número de pares (Rotas)", min_value=0, max_value=8, value=0, step=1)
    pairs_pages: List[bytes] = []
    for i in range(int(num_pairs)):
        with st.expander(f"Rota #{i+1}", expanded=False):
            route = safe_str(st.text_input("ROTA (ex.: LPSO-LPCB)", key=f"pair_route_{i}")).upper().strip()
            c1, c2 = st.columns(2)
            with c1:
                nav_file = st.file_uploader(f"Navlog ({route or 'ROTA'})", type=["pdf","png","jpg","jpeg","gif"], key=f"pair_nav_{i}")
            with c2:
                vfr_file = st.file_uploader(f"VFR Map ({route or 'ROTA'})", type=["pdf","png","jpg","jpeg","gif"], key=f"pair_vfr_{i}")
            # Convert each to images (landscape pages)
            for up in [nav_file, vfr_file]:
                if up is not None:
                    raw = read_upload_bytes(up)
                    if (up.type or "").lower() == "application/pdf":
                        pairs_pages.extend(pdf_page_to_pngs(raw, dpi=220))
                    else:
                        pairs_pages.append(_ensure_rgb_png_bytes(raw))

# ---------- Flight Plan & M&B ----------
with tab_fpmb:
    st.markdown("### Flight Plan & Mass & Balance")
    c1, c2 = st.columns(2)
    with c1:
        fp_upload = st.file_uploader("Flight Plan (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])  # portrait PDFs will be rasterized
        if fp_upload:
            st.success(f"Flight Plan carregado: {safe_str(fp_upload.name)}")
    with c2:
        mb_upload = st.file_uploader("Mass & Balance (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])  # portrait PDFs will be rasterized
        if mb_upload:
            st.success(f"M&B carregado: {safe_str(mb_upload.name)}")

# ---------- NOTAMs (PDF embedded) ----------
with tab_notams:
    st.markdown("### NOTAMs (PDF)")
    st.caption("Carrega um PDF de NOTAMs (ou imagens). Será incorporado no PDF final.")
    notams_upload = st.file_uploader("NOTAMs (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])  # embedded as images
    if notams_upload:
        if (notams_upload.type or "").lower() == "application/pdf":
            try:
                thumbs = pdf_page_to_pngs(read_upload_bytes(notams_upload), dpi=140)
                if thumbs:
                    st.image(thumbs[0], caption="Pré-visualização (p.1)", width=480)
            except Exception:
                st.warning("Não foi possível pré-visualizar o PDF, mas será incorporado.")
        else:
            st.image(read_upload_bytes(notams_upload), caption=safe_str(notams_upload.name), width=480)

# ---------- Gerar PDF ----------
with tab_generate:
    st.markdown("### Saídas")
    gen_final = st.button("Generate Briefing PDF (EN)")

# ---------- Build Final PDF (all landscape pages) ----------
if gen_final:
    pdf = BriefPDF()

    # Collect data for sections
    # Charts (flatten into ordered list of image pages with titles)
    charts_local: List[Dict[str,Any]] = locals().get("charts", [])
    ordered_charts: List[Tuple[str, str, bytes]] = []
    if charts_local:
        # sort by kind then order
        for c in sorted(charts_local, key=_chart_sort_key):
            for page in c.get("pages", [])
:
                ordered_charts.append((c.get("title","Weather Chart"), c.get("subtitle",""), page))

    # Nav/VFR pages already converted
    pairs_png_pages: List[bytes] = locals().get("pairs_pages", [])

    # Flight Plan pages
    fp_pages: List[bytes] = []
    if locals().get("fp_upload") is not None:
        raw = read_upload_bytes(fp_upload)
        if (fp_upload.type or "").lower() == "application/pdf":
            fp_pages = pdf_page_to_pngs(raw, dpi=220)
        else:
            fp_pages = [_ensure_rgb_png_bytes(raw)]

    # M&B pages
    mb_pages: List[bytes] = []
    if locals().get("mb_upload") is not None:
        raw = read_upload_bytes(mb_upload)
        if (mb_upload.type or "").lower() == "application/pdf":
            mb_pages = pdf_page_to_pngs(raw, dpi=220)
        else:
            mb_pages = [_ensure_rgb_png_bytes(raw)]

    # NOTAMs pages
    notams_pages: List[bytes] = []
    if locals().get("notams_upload") is not None:
        raw = read_upload_bytes(notams_upload)
        if (notams_upload.type or "").lower() == "application/pdf":
            notams_pages = pdf_page_to_pngs(raw, dpi=220)
        else:
            notams_pages = [_ensure_rgb_png_bytes(raw)]

    # COVER (with links)
    pdf.add_cover(
        mission_no=locals().get("mission_no",""),
        pilot=locals().get("pilot",""),
        aircraft=locals().get("aircraft_type",""),
        callsign=locals().get("callsign",""),
        reg=locals().get("registration",""),
        date_str=str(locals().get("flight_date","")),
        time_utc=locals().get("time_utc",""),
        icaos_metar=locals().get("icaos_metar", []),
    )

    # SECTION 1 — METAR/TAF/SIGMET/GAMET (raw)
    pdf.section_metar_taf_sigmet_gamet(
        icaos=locals().get("icaos_metar", []),
        sigmet_text=locals().get("sigmet_text", ""),
        gamet_text=locals().get("gamet_text", ""),
    )

    # SECTION 2 — Weather Charts
    pdf.section_weather_charts(ordered_charts)

    # SECTION 3 — Navlog & VFR by Route
    pdf.section_pairs(pairs_png_pages)

    # SECTION 4 — Flight Plan
    pdf.section_single_block("Flight Plan", fp_pages, toc_key="SECTION_FLIGHT_PLAN")

    # SECTION 5 — Mass & Balance
    pdf.section_single_block("Mass & Balance", mb_pages, toc_key="SECTION_MASS_BALANCE")

    # SECTION 6 — NOTAMs
    pdf.section_single_block("NOTAMs", notams_pages, toc_key="SECTION_NOTAMS")

    # Output
    out_name = f"Briefing - Mission {safe_str(locals().get('mission_no') or 'X')}.pdf"
    data = pdf.output(dest="S")
    data = data if isinstance(data, (bytes, bytearray)) else str(data).encode("latin-1")
    st.download_button(
        "Download Briefing PDF (EN)",
        data=data,
        file_name=out_name,
        mime="application/pdf",
        use_container_width=True,
    )
