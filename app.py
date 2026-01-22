# app.py — Briefings (no AI) — A4 Landscape
# Structure: Cover → Weather → NOTAM → PERF/M&B → FUEL → FPL → PROFILE → Nav
from typing import Dict, Any, List, Tuple, Optional
import io, os, tempfile
import streamlit as st
from PIL import Image, ImageOps
from fpdf import FPDF
import fitz  # PyMuPDF

# ---------- Page config & styles ----------
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
</style>
""", unsafe_allow_html=True)

# ---------- Top links ----------
IPMA_URL = "https://brief-ng.ipma.pt/#showLogin"
APP_VFRMAP_URL  = "https://briefings.streamlit.app/VFRMap"
APP_MNB_URL     = "https://briefings.streamlit.app/MassBalance"
APP_NAV_LOG     = "https://briefings.streamlit.app/NavLog"
APP_JPG         = "https://briefings.streamlit.app/JPG"

st.markdown(
    f'''<div class="app-top">
           <div class="app-title">Briefings</div>
           <span class="btnbar">
             <a href="{IPMA_URL}" target="_blank">Weather (IPMA)</a>
             <a href="{APP_VFRMAP_URL}" target="_blank">VFR Map</a>
             <a href="{APP_MNB_URL}" target="_blank">Mass & Balance</a>
             <a href="{APP_NAV_LOG}" target="_blank">NavLog</a>
             <a href="{APP_JPG}" target="_blank">JPG</a>
           </span>
         </div>''',
    unsafe_allow_html=True
)

# ---------- Structure (no TEM) ----------
STRUCTURE = [
    ("weather", "Weather", [
        ("pressure", "Pressure chart"),
        ("sigwx", "SIGWX chart"),
        ("wind", "Wind chart"),
        ("sat", "Satellite/Radar"),
        ("metar_taf", "METAR/TAF"),
        ("sigmet_gamet", "SIGMET/GAMET"),
    ]),
    ("notam", "NOTAM", [
        ("airfields", "Airfields: Destination/Alternate/Diversion"),
        ("active", "Active Areas / FIR (En route / Nav Warnings)"),
    ]),
    ("perf_mb", "PERF/M&B", []),
    ("fuel", "FUEL", []),
    ("fpl", "FPL", []),
    ("profile", "PROFILE", [
        ("objectives", "Objectives"),
        ("dep_enr_arr", "Dep/En route/Arr"),
        ("area_work", "Area work"),
    ]),
    ("nav", "Nav", [
        ("overview", "Overview"),
        ("navlog", "NavLog"),
        ("toc_esa_msa_tod", "TOC / ESA / MSA / TOD"),
        ("time_fuel", "Time and fuel"),
        ("dest_altn_div", "Destination / Alternates / En route diversion"),
        ("notam_route", "Notam / Area affecting the route"),
        ("specials", "Specials"),
    ]),
]

# ---------- Utils ----------
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

def image_bytes_to_pdf_bytes_fullbleed(img_bytes: bytes, orientation: str = "L") -> bytes:
    """Image -> single-page full-bleed A4 PDF (landscape)."""
    doc = FPDF(orientation=orientation, unit="mm", format="A4")
    doc.add_page(orientation=orientation)

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img = ImageOps.exif_transpose(img)

    max_w, max_h = doc.w, doc.h
    iw, ih = img.size
    r = min(max_w/iw, max_h/ih)
    w, h = iw*r, ih*r
    x, y = (doc.w - w) / 2.0, (doc.h - h) / 2.0

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp, "PNG")
        path = tmp.name

    doc.image(path, x=x, y=y, w=w, h=h)
    os.remove(path)

    data = doc.output(dest="S")
    return data if isinstance(data, (bytes, bytearray)) else str(data).encode("latin-1")

def fpdf_to_bytes(doc: FPDF) -> bytes:
    data = doc.output(dest="S")
    return data if isinstance(data, (bytes, bytearray)) else str(data).encode("latin-1")

def mm_to_pt(mm: float) -> float:
    return mm * 72.0 / 25.4

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

# ---------- PDF look ----------
PASTEL = (90, 127, 179)

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
        Cover with clean numbered index.
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

        x_num = 35.0
        x_lbl = 60.0
        y = 80.0
        step = 16.5

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

def make_section_title_pdf(title: str) -> bytes:
    tmp = BriefPDF(orientation="L", unit="mm", format="A4")
    tmp.add_page(orientation="L")
    tmp.draw_header_band(title)
    tmp.set_font("Helvetica", "", 12)
    tmp.set_text_color(107, 114, 128)
    tmp.cell(0, 10, "", ln=True, align="C")
    return fpdf_to_bytes(tmp)

def make_text_block_pdf(section_title: str, subtitle: str, body: str) -> bytes:
    tmp = BriefPDF(orientation="L", unit="mm", format="A4")
    tmp.add_page(orientation="L")
    tmp.draw_header_band(section_title)

    tmp.set_font("Helvetica", "B", 14)
    tmp.ln(4)
    tmp.cell(0, 10, subtitle, ln=True, align="L")

    tmp.set_font("Helvetica", "", 12)
    tmp.set_text_color(15, 23, 42)

    # Simple wrapped multi_cell
    tmp.multi_cell(0, 7, body.strip() if body else "")
    return fpdf_to_bytes(tmp)

# ---------- PyMuPDF link helpers ----------
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
    """
    Tiny, rounded, low-contrast back chip on every page (except the cover).
    """
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

# ---------- Session state defaults ----------
def ss_init(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default

ss_init("mission_no", "")
ss_init("pilot", "Alexandre Moiteiro")
ss_init("callsign", "RVP")
ss_init("aircraft_type", "Tecnam P2008")
ss_init("registration", "CS-DHS")
ss_init("flight_date", None)
ss_init("time_utc", "")

# ---------- UI: Tabs ----------
tab_mission, tab_weather, tab_notam, tab_perfmb, tab_fuel, tab_fpl, tab_profile, tab_nav, tab_generate = st.tabs(
    ["Mission", "Weather", "NOTAM", "PERF/M&B", "FUEL", "FPL", "PROFILE", "Nav", "Generate PDF"]
)

# Mission
with tab_mission:
    st.markdown("### Mission")
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

# Weather
with tab_weather:
    st.markdown("### Weather")
    st.caption("Upload PDF/PNG/JPG/JPEG/GIF. PDFs keep all pages.")
    weather_subs = next(s for s in STRUCTURE if s[0] == "weather")[2]
    for subkey, label in weather_subs:
        ss_init(f"wx_{subkey}", None)
        st.session_state[f"wx_{subkey}"] = st.file_uploader(
            label, type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"u_wx_{subkey}"
        )

# NOTAM
with tab_notam:
    st.markdown("### NOTAM")
    st.caption("Upload the official NOTAMs PDFs/images per category.")
    notam_subs = next(s for s in STRUCTURE if s[0] == "notam")[2]
    for subkey, label in notam_subs:
        ss_init(f"notam_{subkey}", None)
        st.session_state[f"notam_{subkey}"] = st.file_uploader(
            label, type=["pdf", "png", "jpg", "jpeg"], key=f"u_notam_{subkey}"
        )

# PERF/M&B
with tab_perfmb:
    st.markdown("### PERF/M&B")
    ss_init("perf_upload", None)
    ss_init("mb_upload", None)
    c1, c2 = st.columns(2)
    with c1:
        st.session_state.perf_upload = st.file_uploader("Performance (PDF/PNG/JPG)", type=["pdf", "png", "jpg", "jpeg"], key="u_perf")
    with c2:
        st.session_state.mb_upload = st.file_uploader("Mass & Balance (PDF/PNG/JPG)", type=["pdf", "png", "jpg", "jpeg"], key="u_mb")

# FUEL
with tab_fuel:
    st.markdown("### FUEL")
    ss_init("fuel_upload", None)
    st.session_state.fuel_upload = st.file_uploader("Fuel plan / fuel sheet (PDF/PNG/JPG)", type=["pdf", "png", "jpg", "jpeg"], key="u_fuel")

# FPL
with tab_fpl:
    st.markdown("### FPL")
    ss_init("fpl_upload", None)
    st.session_state.fpl_upload = st.file_uploader("Flight Plan (PDF/PNG/JPG)", type=["pdf", "png", "jpg", "jpeg"], key="u_fpl")

# PROFILE
with tab_profile:
    st.markdown("### PROFILE")
    st.caption("Fill text (will be rendered into PDF pages). You can leave blank sections out.")
    ss_init("profile_objectives", "")
    ss_init("profile_dep_enr_arr", "")
    ss_init("profile_area_work", "")
    st.session_state.profile_objectives = st.text_area("Objectives", st.session_state.profile_objectives, height=120)
    st.session_state.profile_dep_enr_arr = st.text_area("Dep / En route / Arr", st.session_state.profile_dep_enr_arr, height=140)
    st.session_state.profile_area_work = st.text_area("Area work", st.session_state.profile_area_work, height=120)

# Nav
with tab_nav:
    st.markdown("### Nav")
    st.caption("Text blocks become PDF pages. NavLog/VFR map can be attached as files if you want.")
    ss_init("nav_overview_text", "")
    ss_init("nav_toc_text", "")
    ss_init("nav_time_fuel_text", "")
    ss_init("nav_dest_altn_div_text", "")
    ss_init("nav_notam_route_text", "")
    ss_init("nav_specials_text", "")
    ss_init("navlog_upload", None)
    ss_init("vfrmap_upload", None)

    st.session_state.nav_overview_text = st.text_area("Overview", st.session_state.nav_overview_text, height=120)

    c1, c2 = st.columns(2)
    with c1:
        st.session_state.navlog_upload = st.file_uploader("NavLog (PDF/PNG/JPG)", type=["pdf", "png", "jpg", "jpeg"], key="u_navlog")
    with c2:
        st.session_state.vfrmap_upload = st.file_uploader("VFR Map (PDF/PNG/JPG)", type=["pdf", "png", "jpg", "jpeg"], key="u_vfrmap")

    st.session_state.nav_toc_text = st.text_area("TOC / ESA / MSA / TOD", st.session_state.nav_toc_text, height=120)
    st.session_state.nav_time_fuel_text = st.text_area("Time and fuel", st.session_state.nav_time_fuel_text, height=120)
    st.session_state.nav_dest_altn_div_text = st.text_area("Destination / Alternates / En route diversion", st.session_state.nav_dest_altn_div_text, height=120)
    st.session_state.nav_notam_route_text = st.text_area("Notam / Area affecting the route", st.session_state.nav_notam_route_text, height=120)
    st.session_state.nav_specials_text = st.text_area("Specials", st.session_state.nav_specials_text, height=120)

# Generate
with tab_generate:
    gen_pdf = st.button("Generate PDF", use_container_width=True)

# ---------- PDF generation ----------
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

if gen_pdf:
    # Build cover in FPDF first
    pdf = BriefPDF(orientation="L", unit="mm", format="A4")
    cover_items = [(k, title) for (k, title, _subs) in STRUCTURE]
    cover_rects_mm = pdf.cover_with_numbered_index(
        mission_no=safe_str(st.session_state.mission_no),
        pilot=safe_str(st.session_state.pilot),
        aircraft=safe_str(st.session_state.aircraft_type),
        callsign=safe_str(st.session_state.callsign),
        reg=safe_str(st.session_state.registration),
        date_str=safe_str(st.session_state.flight_date),
        time_utc=safe_str(st.session_state.time_utc),
        items=cover_items,
    )
    cover_bytes = fpdf_to_bytes(pdf)
    main_doc = fitz.open(stream=cover_bytes, filetype="pdf")

    section_start: Dict[str, Optional[int]] = {k: None for (k, _t, _s) in STRUCTURE}

    # --- Weather ---
    section_start["weather"] = insert_pdf_bytes(main_doc, make_section_title_pdf("Weather"))
    for subkey, _label in next(s for s in STRUCTURE if s[0] == "weather")[2]:
        append_upload(main_doc, st.session_state.get(f"wx_{subkey}"))

    # --- NOTAM ---
    section_start["notam"] = insert_pdf_bytes(main_doc, make_section_title_pdf("NOTAM"))
    for subkey, _label in next(s for s in STRUCTURE if s[0] == "notam")[2]:
        append_upload(main_doc, st.session_state.get(f"notam_{subkey}"))

    # --- PERF/M&B ---
    section_start["perf_mb"] = insert_pdf_bytes(main_doc, make_section_title_pdf("PERF/M&B"))
    append_upload(main_doc, st.session_state.get("perf_upload"))
    append_upload(main_doc, st.session_state.get("mb_upload"))

    # --- FUEL ---
    section_start["fuel"] = insert_pdf_bytes(main_doc, make_section_title_pdf("FUEL"))
    append_upload(main_doc, st.session_state.get("fuel_upload"))

    # --- FPL ---
    section_start["fpl"] = insert_pdf_bytes(main_doc, make_section_title_pdf("FPL"))
    append_upload(main_doc, st.session_state.get("fpl_upload"))

    # --- PROFILE (text → pdf pages only if content exists) ---
    section_start["profile"] = insert_pdf_bytes(main_doc, make_section_title_pdf("PROFILE"))
    if (st.session_state.profile_objectives or "").strip():
        insert_pdf_bytes(main_doc, make_text_block_pdf("PROFILE", "Objectives", st.session_state.profile_objectives))
    if (st.session_state.profile_dep_enr_arr or "").strip():
        insert_pdf_bytes(main_doc, make_text_block_pdf("PROFILE", "Dep / En route / Arr", st.session_state.profile_dep_enr_arr))
    if (st.session_state.profile_area_work or "").strip():
        insert_pdf_bytes(main_doc, make_text_block_pdf("PROFILE", "Area work", st.session_state.profile_area_work))

    # --- Nav (mix text + attachments) ---
    section_start["nav"] = insert_pdf_bytes(main_doc, make_section_title_pdf("Nav"))
    if (st.session_state.nav_overview_text or "").strip():
        insert_pdf_bytes(main_doc, make_text_block_pdf("Nav", "Overview", st.session_state.nav_overview_text))

    # Attachments first (common workflow)
    append_upload(main_doc, st.session_state.get("navlog_upload"))
    append_upload(main_doc, st.session_state.get("vfrmap_upload"))

    # Text blocks
    if (st.session_state.nav_toc_text or "").strip():
        insert_pdf_bytes(main_doc, make_text_block_pdf("Nav", "TOC / ESA / MSA / TOD", st.session_state.nav_toc_text))
    if (st.session_state.nav_time_fuel_text or "").strip():
        insert_pdf_bytes(main_doc, make_text_block_pdf("Nav", "Time and fuel", st.session_state.nav_time_fuel_text))
    if (st.session_state.nav_dest_altn_div_text or "").strip():
        insert_pdf_bytes(main_doc, make_text_block_pdf("Nav", "Destination / Alternates / En route diversion", st.session_state.nav_dest_altn_div_text))
    if (st.session_state.nav_notam_route_text or "").strip():
        insert_pdf_bytes(main_doc, make_text_block_pdf("Nav", "Notam / Area affecting the route", st.session_state.nav_notam_route_text))
    if (st.session_state.nav_specials_text or "").strip():
        insert_pdf_bytes(main_doc, make_text_block_pdf("Nav", "Specials", st.session_state.nav_specials_text))

    # Cover links
    add_cover_links(main_doc, cover_rects_mm, section_start)

    # Back-to-index chip
    add_back_to_index_badge(main_doc)

    # Export
    final_bytes = main_doc.tobytes()
    main_doc.close()

    final_name = f"Briefing - Mission {safe_str(st.session_state.mission_no) or 'X'}.pdf"
    st.download_button(
        "Download PDF",
        data=final_bytes,
        file_name=final_name,
        mime="application/pdf",
        use_container_width=True
    )
