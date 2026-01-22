# app.py — Briefings (no AI) — A4 Landscape
# Order: Cover → Weather → NOTAM (PIB/SUP) → PERF/M&B → FPL → Routes (NavLog + VFR per route)
from typing import Dict, Any, List, Tuple, Optional
import io, os, tempfile
import streamlit as st
from PIL import Image, ImageOps
from fpdf import FPDF
import fitz  # PyMuPDF

# =========================
# Page config & styles
# =========================
st.set_page_config(page_title="Briefings", layout="wide")
st.markdown("""
<style>
:root { --muted:#6b7280; --line:#e5e7eb; --ink:#0f172a; --bg:#ffffff; --accent:#5a7fb3; }
.app-top { display:flex; align-items:center; gap:.75rem; flex-wrap:wrap; margin:.25rem 0 .6rem }
.app-title { font-size: 2.2rem; font-weight: 800; margin: 0 }
.btnbar a{display:inline-block;padding:6px 10px;border:1px solid var(--line);
  border-radius:8px;text-decoration:none;font-weight:600;color:#111827;background:#f8fafc}
.btnbar a:hover{background:#f1f5f9}
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }
.small-muted { color: #6b7280; font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

# =========================
# Top links (buttons)
# =========================
IPMA_URL = "https://brief-ng.ipma.pt/#showLogin"
APP_VFRMAP_URL       = "https://briefings.streamlit.app/VFRMap"
APP_MNB_TECNAM_URL   = "https://briefings.streamlit.app/MassBalance"   # antiga, agora TECNAM_P2008_M&B
APP_MNB_PA28_URL     = "https://briefings.streamlit.app/PA_28_MassBalance"  # <-- AJUSTA para o URL real da nova app
APP_NAV_LOG_URL      = "https://briefings.streamlit.app/NavLog"
APP_JPG_URL          = "https://briefings.streamlit.app/JPG"

st.markdown(
    f'''<div class="app-top">
           <div class="app-title">Briefings</div>
           <span class="btnbar">
             <a href="{IPMA_URL}" target="_blank">Weather (IPMA)</a>
             <a href="{APP_VFRMAP_URL}" target="_blank">VFR Map</a>
             <a href="{APP_MNB_TECNAM_URL}" target="_blank">TECNAM_P2008_M&amp;B</a>
             <a href="{APP_MNB_PA28_URL}" target="_blank">PA_28_M&amp;B</a>
             <a href="{APP_NAV_LOG_URL}" target="_blank">NavLog</a>
             <a href="{APP_JPG_URL}" target="_blank">JPG</a>
           </span>
         </div>''',
    unsafe_allow_html=True
)

# =========================
# Structure (requested)
# =========================
WEATHER_CATEGORIES = [
    ("pressure",    "Pressure chart"),
    ("sigwx",       "SIGWX chart"),
    ("wind",        "Wind chart"),
    ("sat",         "Satellite/Radar"),
    ("other",       "Outros"),
    ("metar_taf",   "METAR/TAF"),
]
# SIGMET/GAMET removido como pediste

# Main sections on cover (in this order)
COVER_SECTIONS = [
    ("weather",   "Weather"),
    ("notam",     "NOTAM"),
    ("perf_mb",   "PERF/M&B"),
    ("fpl",       "FPL"),
    ("routes",    "Nav / Routes"),
]

PASTEL = (90, 127, 179)

# =========================
# Utils
# =========================
def safe_str(x) -> str:
    try:
        return "" if x is None else str(x)
    except Exception:
        return ""

def read_upload_bytes(upload) -> bytes:
    if upload is None:
        return b""
    try:
        return upload.getvalue() if hasattr(upload, "getvalue") else upload.read()
    except Exception:
        return b""

def fpdf_to_bytes(doc: FPDF) -> bytes:
    data = doc.output(dest="S")
    return data if isinstance(data, (bytes, bytearray)) else str(data).encode("latin-1")

def mm_to_pt(mm: float) -> float:
    return mm * 72.0 / 25.4

def image_bytes_to_pdf_bytes_fullbleed(img_bytes: bytes, orientation: str = "L") -> bytes:
    """Image -> single-page full-bleed A4 PDF (landscape)."""
    doc = FPDF(orientation=orientation, unit="mm", format="A4")
    doc.add_page(orientation=orientation)

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img = ImageOps.exif_transpose(img)

    max_w, max_h = doc.w, doc.h
    iw, ih = img.size
    r = min(max_w / iw, max_h / ih)
    w, h = iw * r, ih * r
    x, y = (doc.w - w) / 2.0, (doc.h - h) / 2.0

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp, "PNG")
        path = tmp.name

    doc.image(path, x=x, y=y, w=w, h=h)
    os.remove(path)
    return fpdf_to_bytes(doc)

def open_upload_as_pdf(upload, orientation_for_images="L") -> Optional[fitz.Document]:
    """Return a PyMuPDF Document for a PDF upload or for an image converted to PDF."""
    if upload is None:
        return None
    raw = read_upload_bytes(upload)
    if not raw:
        return None
    mime = (getattr(upload, "type", "") or "").lower()
    if mime == "application/pdf":
        return fitz.open(stream=raw, filetype="pdf")
    ext_bytes = image_bytes_to_pdf_bytes_fullbleed(raw, orientation=orientation_for_images)
    return fitz.open(stream=ext_bytes, filetype="pdf")

# =========================
# PDF base
# =========================
class BriefPDF(FPDF):
    def header(self): pass
    def footer(self): pass

    def draw_header_band(self, text: str):
        self.set_draw_color(229, 231, 235)
        self.set_line_width(0.3)
        self.set_font("Helvetica", "B", 18)
        self.cell(0, 12, text, ln=True, align="C", border="B")

    def cover_with_numbered_index(
        self,
        mission_no: str,
        pilot: str,
        aircraft: str,
        callsign: str,
        reg: str,
        date_str: str,
        time_utc: str,
        items: List[Tuple[str, str]],
    ) -> Dict[str, Tuple[float, float, float, float]]:
        """
        Cover with numbered index.
        Returns clickable rectangles (mm) per section key.
        """
        self.add_page(orientation="L")

        # Title / info
        self.set_xy(0, 20)
        self.set_font("Helvetica", "B", 32)
        self.cell(0, 16, "Briefing", ln=True, align="C")

        self.set_font("Helvetica", "", 14)
        info = []
        if mission_no: info.append(f"Mission: {mission_no}")
        if pilot: info.append(f"Pilot: {pilot}")
        if aircraft: info.append(f"Aircraft: {aircraft}")
        if callsign: info.append(f"Callsign: {callsign}")
        if reg: info.append(f"Reg: {reg}")
        if info:
            self.cell(0, 9, "   ".join(info), ln=True, align="C")
        if date_str or time_utc:
            self.cell(0, 9, f"Date: {date_str}   UTC: {time_utc}", ln=True, align="C")

        self.ln(8)
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 10, "Index", ln=True, align="C")
        self.ln(2)

        rects_mm: Dict[str, Tuple[float, float, float, float]] = {}
        x_num, x_lbl = 35.0, 60.0
        y, step = 80.0, 16.5

        for i, (key, label) in enumerate(items, start=1):
            num = f"{i:02d}"
            self.set_text_color(*PASTEL)
            self.set_xy(x_num, y - 8)
            self.set_font("Helvetica", "B", 28)
            self.cell(0, 16, num, ln=0)

            self.set_text_color(15, 23, 42)
            self.set_xy(x_lbl, y - 6)
            self.set_font("Helvetica", "B", 18)
            self.cell(0, 13, label, ln=1)

            self.set_draw_color(220, 224, 228)
            self.set_line_width(0.3)
            self.line(x_lbl, y + 6.5, x_lbl + 210.0, y + 6.5)

            rects_mm[key] = (x_lbl - 2.0, y - 7.0, 215.0, 14.0)
            y += step

        self.set_text_color(0, 0, 0)
        return rects_mm

def make_section_title_pdf(title: str, subtitle: str = "") -> bytes:
    tmp = BriefPDF(orientation="L", unit="mm", format="A4")
    tmp.add_page(orientation="L")
    tmp.draw_header_band(title)
    if subtitle:
        tmp.set_font("Helvetica", "I", 12)
        tmp.set_text_color(107, 114, 128)
        tmp.cell(0, 10, subtitle, ln=True, align="C")
    return fpdf_to_bytes(tmp)

# =========================
# PyMuPDF link helpers
# =========================
def add_cover_links(doc: fitz.Document, rects_mm: Dict[str, Tuple[float, float, float, float]],
                    targets: Dict[str, Optional[int]]):
    """Clickable links on the cover (page 0) to section start pages."""
    if doc.page_count == 0:
        return
    page0 = doc.load_page(0)
    for key, (x, y, w, h) in rects_mm.items():
        target = targets.get(key)
        if target is None:
            continue
        rect = fitz.Rect(mm_to_pt(x), mm_to_pt(y), mm_to_pt(x + w), mm_to_pt(y + h))
        page0.insert_link({"kind": fitz.LINK_GOTO, "from": rect, "page": int(target)})

def add_back_to_index_badge(doc: fitz.Document):
    """Tiny, rounded, low-contrast back chip on every page (except the cover)."""
    for pno in range(1, doc.page_count):
        page = doc.load_page(pno)
        pw = page.rect.width

        margin_mm = 6.0
        w_mm, h_mm = 9.5, 8.0
        left = pw - mm_to_pt(margin_mm + w_mm)
        top = mm_to_pt(margin_mm)
        rect = fitz.Rect(left, top, left + mm_to_pt(w_mm), top + mm_to_pt(h_mm))

        stroke = (0.84, 0.87, 0.92)
        fill = (0.98, 0.985, 1.0)
        try:
            page.draw_rect(
                rect,
                color=stroke, fill=fill, width=0.4,
                radius=mm_to_pt(1.2),
                fill_opacity=0.10, stroke_opacity=0.20
            )
        except Exception:
            try:
                page.draw_rect(rect, color=stroke, fill=fill, width=0.3)
            except Exception:
                pass

        pad = mm_to_pt(1.4)
        col = (0.52, 0.56, 0.62)
        width = 0.8

        y_mid = rect.y0 + rect.height * 0.55
        x_right = rect.x1 - pad
        x_head = rect.x0 + pad + mm_to_pt(2.6)

        page.draw_line(fitz.Point(x_right, y_mid), fitz.Point(x_head, y_mid), color=col, width=width)
        head = mm_to_pt(2.2)
        page.draw_line(fitz.Point(x_head, y_mid), fitz.Point(x_head + head, y_mid - head), color=col, width=width)
        page.draw_line(fitz.Point(x_head, y_mid), fitz.Point(x_head + head, y_mid + head), color=col, width=width)
        hook_h = mm_to_pt(2.0)
        page.draw_line(fitz.Point(x_right, y_mid), fitz.Point(x_right, y_mid - hook_h), color=col, width=width * 0.85)

        page.insert_link({"kind": fitz.LINK_GOTO, "from": rect, "page": 0})

# =========================
# Streamlit state helpers
# =========================
def ss_init(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default

def collect_multi_uploads(
    state_key: str,
    label: str,
    types: List[str],
    help_text: str = "",
) -> List[Dict[str, Any]]:
    """
    UI: multi uploader + per-file order inputs.
    Returns list of dicts: {upload, order, name}
    """
    ss_init(state_key, [])
    st.markdown(f"**{label}**")
    if help_text:
        st.markdown(f"<div class='small-muted'>{help_text}</div>", unsafe_allow_html=True)

    uploads = st.file_uploader(
        label,
        type=types,
        accept_multiple_files=True,
        key=f"u_{state_key}",
        label_visibility="collapsed"
    )

    # Keep latest uploads in session
    if uploads is None:
        uploads = []
    st.session_state[state_key] = uploads

    items: List[Dict[str, Any]] = []
    if uploads:
        st.caption("Organizar: define a ordem (1 = primeiro).")
        for i, f in enumerate(uploads):
            name = safe_str(getattr(f, "name", f"file_{i}"))
            ord_key = f"{state_key}_ord_{i}"
            ss_init(ord_key, i + 1)
            c1, c2, c3 = st.columns([0.60, 0.20, 0.20])
            with c1:
                st.write(name)
            with c2:
                st.session_state[ord_key] = st.number_input("Ordem", min_value=1, step=1, value=int(st.session_state[ord_key]), key=f"ni_{ord_key}")
            with c3:
                st.write(f"{safe_str(getattr(f, 'type', ''))}")
            items.append({"upload": f, "order": int(st.session_state[ord_key]), "name": name})

    # Sort before returning
    items.sort(key=lambda d: (d["order"], d["name"]))
    st.divider()
    return items

# =========================
# UI: Tabs
# =========================
tab_mission, tab_weather, tab_notam, tab_perfmb, tab_fpl, tab_routes, tab_generate = st.tabs(
    ["Mission", "Weather", "NOTAM", "PERF/M&B", "FPL", "Routes", "Generate PDF"]
)

# Mission
with tab_mission:
    st.markdown("### Mission")
    ss_init("pilot", "Alexandre Moiteiro")
    ss_init("callsign", "RVP")
    ss_init("aircraft_type", "Tecnam P2008")
    ss_init("registration", "CS-DHS")
    ss_init("mission_no", "")
    ss_init("flight_date", None)
    ss_init("time_utc", "")

    colA, colB, colC = st.columns(3)
    with colA:
        st.session_state.pilot = st.text_input("Pilot name", st.session_state.pilot)
        st.session_state.callsign = st.text_input("Mission callsign", st.session_state.callsign)
    with colB:
        st.session_state.aircraft_type = st.text_input("Aircraft type", st.session_state.aircraft_type)
        regs = ["CS-DHS", "CS-DHT", "CS-DHU", "CS-DHV", "CS-DHW", "CS-ECC", "CS-ECD"]
        idx = regs.index(st.session_state.registration) if st.session_state.registration in regs else 0
        st.session_state.registration = st.selectbox("Registration", regs, index=idx)
    with colC:
        st.session_state.mission_no = st.text_input("Mission number", st.session_state.mission_no)
        st.session_state.flight_date = st.date_input("Flight date")
        st.session_state.time_utc = st.text_input("UTC time", st.session_state.time_utc)

# Weather (multiple per category + ordering)
with tab_weather:
    st.markdown("### Weather")
    st.caption("Podes colocar mais do que um ficheiro por categoria e definir a ordem.")
    wx_items_by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for key, label in WEATHER_CATEGORIES:
        wx_items_by_cat[key] = collect_multi_uploads(
            state_key=f"wx_{key}",
            label=label,
            types=["pdf", "png", "jpg", "jpeg", "gif"],
            help_text="Aceita PDF/PNG/JPG/JPEG/GIF (até 200MB por ficheiro)."
        )
    st.session_state["wx_items_by_cat"] = wx_items_by_cat

# NOTAM (PIB + SUP, each multiple + ordering)
with tab_notam:
    st.markdown("### NOTAM")
    st.caption("Duas abas: PIB e SUP. Podes adicionar vários e organizar a ordem.")
    tab_pib, tab_sup = st.tabs(["PIB", "SUP"])

    with tab_pib:
        notam_pib_items = collect_multi_uploads(
            state_key="notam_pib",
            label="PIB",
            types=["pdf", "png", "jpg", "jpeg"],
            help_text="NOTAMs em formato PIB (PDF/Imagem)."
        )
        st.session_state["notam_pib_items"] = notam_pib_items

    with tab_sup:
        notam_sup_items = collect_multi_uploads(
            state_key="notam_sup",
            label="SUP",
            types=["pdf", "png", "jpg", "jpeg"],
            help_text="NOTAMs / suplementos (PDF/Imagem)."
        )
        st.session_state["notam_sup_items"] = notam_sup_items

# PERF/M&B (same section; multiple allowed)
with tab_perfmb:
    st.markdown("### PERF/M&B")
    st.caption("Perf e Mass & Balance tratados como a mesma secção. Podes anexar vários e ordenar.")
    perfmb_items = collect_multi_uploads(
        state_key="perfmb",
        label="PERF/M&B uploads",
        types=["pdf", "png", "jpg", "jpeg"],
        help_text="PDF/PNG/JPG/JPEG."
    )
    st.session_state["perfmb_items"] = perfmb_items

# FPL (keep, allow multiple + ordering)
with tab_fpl:
    st.markdown("### FPL")
    st.caption("Podes anexar mais do que um (por exemplo FPL + anexos).")
    fpl_items = collect_multi_uploads(
        state_key="fpl_files",
        label="FPL uploads",
        types=["pdf", "png", "jpg", "jpeg"],
        help_text="PDF/PNG/JPG/JPEG."
    )
    st.session_state["fpl_items"] = fpl_items

# Routes (NavLog as before: route pairs with navlog + VFR map)
with tab_routes:
    st.markdown("### Routes")
    st.caption("Como estava antigamente: por rota (ex.: LPSO-LPCB) anexas NavLog e VFR Map.")
    ss_init("num_pairs", 0)
    num_pairs = st.number_input("Number of route pairs", min_value=0, max_value=10, value=int(st.session_state.num_pairs), step=1)
    st.session_state.num_pairs = int(num_pairs)

    pairs: List[Dict[str, Any]] = []
    for i in range(int(num_pairs)):
        with st.expander(f"Route #{i+1}", expanded=False):
            route = safe_str(st.text_input("ROUTE (e.g., LPSO-LPCB)", key=f"pair_route_{i}")).upper().strip()
            c1, c2 = st.columns(2)
            with c1:
                nav_file = st.file_uploader(f"NavLog ({route or 'ROUTE'})", type=["pdf", "png", "jpg", "jpeg"], key=f"pair_nav_{i}")
            with c2:
                vfr_file = st.file_uploader(f"VFR Map ({route or 'ROUTE'})", type=["pdf", "png", "jpg", "jpeg"], key=f"pair_vfr_{i}")
            pairs.append({"route": route, "nav": nav_file, "vfr": vfr_file})
    st.session_state["pairs"] = pairs

# Generate
with tab_generate:
    gen_pdf = st.button("Generate PDF", use_container_width=True)

# =========================
# PDF generation helpers
# =========================
def insert_pdf_bytes(main_doc: fitz.Document, pdf_bytes: bytes) -> int:
    """Insert a PDF (bytes) at end. Returns start page index (0-based) where inserted."""
    start = main_doc.page_count
    d = fitz.open(stream=pdf_bytes, filetype="pdf")
    main_doc.insert_pdf(d, start_at=start)
    d.close()
    return start

def append_upload(main_doc: fitz.Document, upload) -> Optional[int]:
    """Append upload document. Returns start page or None if nothing appended."""
    ext = open_upload_as_pdf(upload, orientation_for_images="L")
    if not ext:
        return None
    start = main_doc.page_count
    main_doc.insert_pdf(ext, start_at=start)
    ext.close()
    return start

def append_many(main_doc: fitz.Document, items: List[Dict[str, Any]]):
    """Append many ordered items (each item has 'upload')."""
    for it in items:
        append_upload(main_doc, it.get("upload"))

# =========================
# Generate PDF
# =========================
if gen_pdf:
    # --- Cover ---
    pdf = BriefPDF(orientation="L", unit="mm", format="A4")
    cover_items = [(k, title) for (k, title) in COVER_SECTIONS]
    cover_rects_mm = pdf.cover_with_numbered_index(
        mission_no=safe_str(st.session_state.get("mission_no")),
        pilot=safe_str(st.session_state.get("pilot")),
        aircraft=safe_str(st.session_state.get("aircraft_type")),
        callsign=safe_str(st.session_state.get("callsign")),
        reg=safe_str(st.session_state.get("registration")),
        date_str=safe_str(st.session_state.get("flight_date")),
        time_utc=safe_str(st.session_state.get("time_utc")),
        items=cover_items,
    )
    main_doc = fitz.open(stream=fpdf_to_bytes(pdf), filetype="pdf")

    section_start: Dict[str, Optional[int]] = {k: None for (k, _t) in COVER_SECTIONS}

    # --- Weather ---
    section_start["weather"] = insert_pdf_bytes(main_doc, make_section_title_pdf("Weather"))
    wx_by_cat: Dict[str, List[Dict[str, Any]]] = st.session_state.get("wx_items_by_cat", {})
    for cat_key, cat_label in WEATHER_CATEGORIES:
        items = wx_by_cat.get(cat_key, []) or []
        if items:
            insert_pdf_bytes(main_doc, make_section_title_pdf("Weather", subtitle=cat_label))
            append_many(main_doc, items)

    # --- NOTAM ---
    section_start["notam"] = insert_pdf_bytes(main_doc, make_section_title_pdf("NOTAM"))
    pib_items = st.session_state.get("notam_pib_items", []) or []
    sup_items = st.session_state.get("notam_sup_items", []) or []
    if pib_items:
        insert_pdf_bytes(main_doc, make_section_title_pdf("NOTAM", subtitle="PIB"))
        append_many(main_doc, pib_items)
    if sup_items:
        insert_pdf_bytes(main_doc, make_section_title_pdf("NOTAM", subtitle="SUP"))
        append_many(main_doc, sup_items)

    # --- PERF/M&B ---
    section_start["perf_mb"] = insert_pdf_bytes(main_doc, make_section_title_pdf("PERF/M&B"))
    perfmb_items = st.session_state.get("perfmb_items", []) or []
    append_many(main_doc, perfmb_items)

    # --- FPL ---
    section_start["fpl"] = insert_pdf_bytes(main_doc, make_section_title_pdf("FPL"))
    fpl_items = st.session_state.get("fpl_items", []) or []
    append_many(main_doc, fpl_items)

    # --- Routes (NavLog + VFR map per route) ---
    section_start["routes"] = insert_pdf_bytes(main_doc, make_section_title_pdf("Nav / Routes"))
    pairs_local: List[Dict[str, Any]] = st.session_state.get("pairs", []) or []
    for idx, p in enumerate(pairs_local, start=1):
        route = safe_str(p.get("route")).upper().strip() or f"ROUTE #{idx}"
        nav_up = p.get("nav")
        vfr_up = p.get("vfr")
        if nav_up or vfr_up:
            insert_pdf_bytes(main_doc, make_section_title_pdf("Route", subtitle=route))
            if nav_up:
                insert_pdf_bytes(main_doc, make_section_title_pdf("Route", subtitle=f"{route} — NavLog"))
                append_upload(main_doc, nav_up)
            if vfr_up:
                insert_pdf_bytes(main_doc, make_section_title_pdf("Route", subtitle=f"{route} — VFR Map"))
                append_upload(main_doc, vfr_up)

    # --- Cover links + back chip ---
    add_cover_links(main_doc, cover_rects_mm, section_start)
    add_back_to_index_badge(main_doc)

    # --- Export ---
    final_bytes = main_doc.tobytes()
    main_doc.close()

    final_name = f"Briefing - Mission {safe_str(st.session_state.get('mission_no')) or 'X'}.pdf"
    st.download_button(
        "Download PDF",
        data=final_bytes,
        file_name=final_name,
        mime="application/pdf",
        use_container_width=True
    )
