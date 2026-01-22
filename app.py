# app.py — Briefings (no AI) — A4 Landscape
# Order: Cover → Weather → NOTAM → PERF/M&B → FPL → Nav (route pairs like old)
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
.small-muted { color: #6b7280; font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

# ---------- Top links ----------
IPMA_URL = "https://brief-ng.ipma.pt/#showLogin"
APP_VFRMAP_URL  = "https://briefings.streamlit.app/VFRMap"
APP_NAV_LOG     = "https://briefings.streamlit.app/NavLog"
APP_JPG         = "https://briefings.streamlit.app/JPG"

# rename the old Mass&Balance app label
APP_TECNAM_P2008_MNB_URL = "https://briefings.streamlit.app/MassBalance"

# new PA-28 M&B app button (update if your deployed path is different)
APP_PA28_MNB_URL = "https://briefings.streamlit.app/PA_28_MassBalance"

st.markdown(
    f'''<div class="app-top">
           <div class="app-title">Briefings</div>
           <span class="btnbar">
             <a href="{IPMA_URL}" target="_blank">Weather (IPMA)</a>
             <a href="{APP_VFRMAP_URL}" target="_blank">VFR Map</a>
             <a href="{APP_TECNAM_P2008_MNB_URL}" target="_blank">TECNAM_P2008_M&amp;B</a>
             <a href="{APP_PA28_MNB_URL}" target="_blank">PA_28_M&amp;B</a>
             <a href="{APP_NAV_LOG}" target="_blank">NavLog</a>
             <a href="{APP_JPG}" target="_blank">JPG</a>
           </span>
         </div>''',
    unsafe_allow_html=True
)

# ---------- Weather structure (SIGMET/GAMET removed; "Outros" before Satellite/Radar) ----------
WEATHER_CATS: List[Tuple[str, str]] = [
    ("pressure",   "Pressure chart"),
    ("sigwx",      "SIGWX chart"),
    ("wind",       "Wind chart"),
    ("other",      "Outros"),
    ("sat",        "Satellite/Radar"),
    ("metar_taf",  "METAR/TAF"),
]
WEATHER_RANK = {k: i for i, (k, _) in enumerate(WEATHER_CATS, start=1)}

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
    """Image -> single-page A4 PDF (landscape), centered (no crop)."""
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
    """Return a PyMuPDF Document for a PDF upload, or an image converted to PDF."""
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
    return fpdf_to_bytes(tmp)

def make_subsection_title_pdf(section: str, subtitle: str) -> bytes:
    tmp = BriefPDF(orientation="L", unit="mm", format="A4")
    tmp.add_page(orientation="L")
    tmp.draw_header_band(section)
    tmp.ln(8)
    tmp.set_font("Helvetica", "B", 20)
    tmp.cell(0, 16, subtitle, ln=True, align="C")
    tmp.set_font("Helvetica", "", 12)
    tmp.set_text_color(107, 114, 128)
    tmp.cell(0, 8, " ", ln=True, align="C")
    return fpdf_to_bytes(tmp)

# ---------- PyMuPDF link helpers ----------
def add_cover_links(doc: fitz.Document, rects_mm: Dict[str, Tuple[float, float, float, float]],
                    targets: Dict[str, Optional[int]]):
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

# ---------- Session state helpers ----------
def ss_init(key: str, default):
    if key not in st.session_state:
        st.session_state[key] = default

# Mission defaults
ss_init("mission_no", "")
ss_init("pilot", "Alexandre Moiteiro")
ss_init("callsign", "RVP")
ss_init("aircraft_type", "Tecnam P2008")
ss_init("registration", "CS-DHS")
ss_init("flight_date", None)
ss_init("time_utc", "")

# ---------- UI: Tabs ----------
tab_mission, tab_weather, tab_notam, tab_perfmb, tab_fpl, tab_nav, tab_generate = st.tabs(
    ["Mission", "Weather", "NOTAM", "PERF/M&B", "FPL", "Routes / Nav", "Generate PDF"]
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

def order_key(cat_key: str, file_idx: int) -> str:
    return f"ord__{cat_key}__{file_idx}"

def render_multi_upload_with_order(section_key: str, cat_key: str, label: str, types: List[str]):
    """
    Multi-file uploader + per-file order numbers.
    Returns list of dicts: [{upload, order, cat_key, label, idx}]
    """
    st.markdown(f"#### {label}")
    up = st.file_uploader(
        label,
        type=types,
        accept_multiple_files=True,
        key=f"up__{section_key}__{cat_key}",
        help="Pode carregar mais do que um ficheiro. Use o campo 'Order' para organizar.",
    )
    items: List[Dict[str, Any]] = []
    if up:
        st.caption("Organização: menor 'Order' aparece primeiro.")
        for i, f in enumerate(up):
            ok = order_key(f"{section_key}__{cat_key}", i)
            ss_init(ok, i + 1)
            cols = st.columns([0.65, 0.35])
            with cols[0]:
                st.write(f"• {safe_str(getattr(f, 'name', 'file'))}")
            with cols[1]:
                st.session_state[ok] = st.number_input(
                    "Order",
                    min_value=1,
                    max_value=9999,
                    value=int(st.session_state[ok]),
                    step=1,
                    key=f"ni__{ok}",
                    label_visibility="visible",
                )
            items.append({
                "upload": f,
                "order": int(st.session_state[ok]),
                "cat_key": cat_key,
                "cat_label": label,
                "idx": i,
            })
    st.divider()
    return items

# Weather
with tab_weather:
    st.markdown("### Weather")
    st.markdown('<div class="small-muted">PDF/PNG/JPG/JPEG/GIF — pode carregar vários por item e ordenar.</div>',
                unsafe_allow_html=True)

    weather_all: List[Dict[str, Any]] = []
    for cat_key, label in WEATHER_CATS:
        weather_all.extend(
            render_multi_upload_with_order(
                section_key="weather",
                cat_key=cat_key,
                label=label,
                types=["pdf", "png", "jpg", "jpeg", "gif"],
            )
        )
    st.session_state["weather_items"] = weather_all

# NOTAM
with tab_notam:
    st.markdown("### NOTAM")
    st.markdown('<div class="small-muted">Separado em PIB e SUP. Pode carregar vários e ordenar.</div>',
                unsafe_allow_html=True)

    t_pib, t_sup = st.tabs(["PIB", "SUP"])

    with t_pib:
        notam_pib = render_multi_upload_with_order(
            section_key="notam_pib",
            cat_key="pib",
            label="PIB NOTAMs",
            types=["pdf", "png", "jpg", "jpeg"],
        )
        st.session_state["notam_pib_items"] = notam_pib

    with t_sup:
        notam_sup = render_multi_upload_with_order(
            section_key="notam_sup",
            cat_key="sup",
            label="SUP / Supplements",
            types=["pdf", "png", "jpg", "jpeg"],
        )
        st.session_state["notam_sup_items"] = notam_sup

# PERF/M&B (same thing)
with tab_perfmb:
    st.markdown("### PERF/M&B")
    st.markdown('<div class="small-muted">Aqui pode anexar vários documentos (performance + mass &amp; balance, etc.) e ordenar.</div>',
                unsafe_allow_html=True)
    perfmb_items = render_multi_upload_with_order(
        section_key="perfmb",
        cat_key="perfmb",
        label="PERF/M&B documents",
        types=["pdf", "png", "jpg", "jpeg"],
    )
    st.session_state["perfmb_items"] = perfmb_items

# FPL
with tab_fpl:
    st.markdown("### FPL")
    ss_init("fpl_upload", None)
    st.session_state.fpl_upload = st.file_uploader(
        "Flight Plan (PDF/PNG/JPG)",
        type=["pdf", "png", "jpg", "jpeg"],
        key="u_fpl_single",
    )

# Routes / Nav (like old: pairs)
with tab_nav:
    st.markdown("### Routes / Nav")
    st.caption("Como antigamente: por cada rota (ex.: LPSO-LPCB) anexar Navlog + VFR Map. (PDF/PNG/JPG/JPEG)")

    num_pairs = st.number_input("Number of route pairs", min_value=0, max_value=12, value=0, step=1, key="num_pairs")
    pairs: List[Dict[str, Any]] = []
    for i in range(int(num_pairs)):
        with st.expander(f"Route #{i+1}", expanded=False):
            route = safe_str(st.text_input("ROUTE (e.g., LPSO-LPCB)", key=f"pair_route_{i}")).upper().strip()
            c1, c2 = st.columns(2)
            with c1:
                nav_file = st.file_uploader(
                    f"Navlog ({route or 'ROUTE'})",
                    type=["pdf", "png", "jpg", "jpeg"],
                    key=f"pair_nav_{i}",
                )
            with c2:
                vfr_file = st.file_uploader(
                    f"VFR Map ({route or 'ROUTE'})",
                    type=["pdf", "png", "jpg", "jpeg"],
                    key=f"pair_vfr_{i}",
                )
            pairs.append({"route": route, "nav": nav_file, "vfr": vfr_file})
    st.session_state["pairs"] = pairs

# Generate
with tab_generate:
    gen_pdf = st.button("Generate PDF", use_container_width=True)

# ---------- PDF generation helpers ----------
def insert_pdf_bytes(main_doc: fitz.Document, pdf_bytes: bytes) -> int:
    """Insert a PDF (bytes) at end. Returns start page index (0-based)."""
    start = main_doc.page_count
    d = fitz.open(stream=pdf_bytes, filetype="pdf")
    main_doc.insert_pdf(d, start_at=start)
    d.close()
    return start

def append_upload(main_doc: fitz.Document, upload) -> Optional[int]:
    """Append upload document (keeps all pages for PDFs). Returns start page or None."""
    ext = open_upload_as_pdf(upload, orientation_for_images="L")
    if not ext:
        return None
    start = main_doc.page_count
    main_doc.insert_pdf(ext, start_at=start)
    ext.close()
    return start

def append_items_sorted(main_doc: fitz.Document, items: List[Dict[str, Any]]):
    """Append items sorted by user order, then by category rank, then by list index."""
    def sk(it):
        return (
            int(it.get("order", 9999)),
            int(WEATHER_RANK.get(it.get("cat_key", ""), 9999)),
            int(it.get("idx", 9999)),
        )
    for it in sorted(items or [], key=sk):
        append_upload(main_doc, it.get("upload"))

def append_items_sorted_simple(main_doc: fitz.Document, items: List[Dict[str, Any]]):
    """Append items sorted by user order then by idx (for NOTAM/PERFMB)."""
    def sk(it):
        return (int(it.get("order", 9999)), int(it.get("idx", 9999)))
    for it in sorted(items or [], key=sk):
        append_upload(main_doc, it.get("upload"))

# ---------- PDF generation ----------
if gen_pdf:
    # Cover
    cover_doc = BriefPDF(orientation="L", unit="mm", format="A4")

    cover_items = [
        ("weather", "Weather"),
        ("notam", "NOTAM"),
        ("perfmb", "PERF/M&B"),
        ("fpl", "FPL"),
        ("nav", "Nav"),
    ]

    cover_rects_mm = cover_doc.cover_with_numbered_index(
        mission_no=safe_str(st.session_state.mission_no),
        pilot=safe_str(st.session_state.pilot),
        aircraft=safe_str(st.session_state.aircraft_type),
        callsign=safe_str(st.session_state.callsign),
        reg=safe_str(st.session_state.registration),
        date_str=safe_str(st.session_state.flight_date),
        time_utc=safe_str(st.session_state.time_utc),
        items=cover_items,
    )

    main_doc = fitz.open(stream=fpdf_to_bytes(cover_doc), filetype="pdf")
    section_start: Dict[str, Optional[int]] = {k: None for (k, _) in cover_items}

    # --- Weather ---
    section_start["weather"] = insert_pdf_bytes(main_doc, make_section_title_pdf("Weather"))
    weather_items = st.session_state.get("weather_items", []) or []

    # Optional: insert a small subsection title page per category if that category has any files
    # (looks cleaner inside Weather; comment out if you prefer no separators)
    for cat_key, cat_label in WEATHER_CATS:
        cat_files = [it for it in weather_items if it.get("cat_key") == cat_key]
        if not cat_files:
            continue
        insert_pdf_bytes(main_doc, make_subsection_title_pdf("Weather", cat_label))
        # within category, keep user's order, then idx
        append_items_sorted_simple(main_doc, cat_files)

    # --- NOTAM (PIB then SUP, each ordered) ---
    section_start["notam"] = insert_pdf_bytes(main_doc, make_section_title_pdf("NOTAM"))
    pib_items = st.session_state.get("notam_pib_items", []) or []
    sup_items = st.session_state.get("notam_sup_items", []) or []

    if pib_items:
        insert_pdf_bytes(main_doc, make_subsection_title_pdf("NOTAM", "PIB"))
        append_items_sorted_simple(main_doc, pib_items)
    if sup_items:
        insert_pdf_bytes(main_doc, make_subsection_title_pdf("NOTAM", "SUP"))
        append_items_sorted_simple(main_doc, sup_items)

    # --- PERF/M&B ---
    section_start["perfmb"] = insert_pdf_bytes(main_doc, make_section_title_pdf("PERF/M&B"))
    perfmb_items = st.session_state.get("perfmb_items", []) or []
    append_items_sorted_simple(main_doc, perfmb_items)

    # --- FPL ---
    section_start["fpl"] = insert_pdf_bytes(main_doc, make_section_title_pdf("FPL"))
    append_upload(main_doc, st.session_state.get("fpl_upload"))

    # --- Nav (route pairs like old; optional separators per route) ---
    section_start["nav"] = insert_pdf_bytes(main_doc, make_section_title_pdf("Nav"))
    pairs_local: List[Dict[str, Any]] = st.session_state.get("pairs", []) or []
    for p in pairs_local:
        route = safe_str(p.get("route", "")).strip()
        nav_up = p.get("nav")
        vfr_up = p.get("vfr")

        # Optional: route separator page (nice for readability)
        if route:
            insert_pdf_bytes(main_doc, make_subsection_title_pdf("Nav", route))

        # Keep the old principle: Navlog then VFR map
        append_upload(main_doc, nav_up)
        append_upload(main_doc, vfr_up)

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

