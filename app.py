# app.py — Briefings (no AI) — A4 Landscape
# Order: Cover → Charts → Flight Plan → Routes → NOTAMs → Mass & Balance
from typing import Dict, Any, List, Tuple, Optional
import io, os, tempfile
import streamlit as st
from PIL import Image
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

# ---------- Utils ----------
def safe_str(x) -> str:
    try: return "" if x is None else str(x)
    except Exception: return ""

def read_upload_bytes(upload) -> bytes:
    if upload is None: return b""
    try: return upload.getvalue() if hasattr(upload, "getvalue") else upload.read()
    except Exception: return b""

def ensure_png_from_bytes(file_bytes: bytes, mime: str) -> io.BytesIO:
    """Accept PDF/PNG/JPG/JPEG/GIF and return PNG bytes (first page if PDF)."""
    try:
        m = (mime or "").lower()
        if m == "application/pdf":
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            page = doc.load_page(0)
            png = page.get_pixmap(dpi=300).tobytes("png")
            return io.BytesIO(png)
        else:
            img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            out = io.BytesIO(); img.save(out, "PNG"); out.seek(0); return out
    except Exception:
        ph = Image.new("RGB", (1200, 800), (245, 246, 248))
        out = io.BytesIO(); ph.save(out, "PNG"); out.seek(0); return out

def image_bytes_to_pdf_bytes_fullbleed(img_bytes: bytes, orientation: str = "L") -> bytes:
    """Image -> single-page full-bleed A4 PDF (landscape)."""
    doc = FPDF(orientation=orientation, unit="mm", format="A4")
    doc.add_page(orientation=orientation)
    max_w, max_h = doc.w, doc.h
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    iw, ih = img.size; r = min(max_w/iw, max_h/ih); w, h = int(iw*r), int(ih*r)
    x, y = (doc.w - w) / 2, (doc.h - h) / 2
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        img.save(tmp, "PNG"); path = tmp.name
    doc.image(path, x=x, y=y, w=w, h=h); os.remove(path)
    data = doc.output(dest="S")
    return data if isinstance(data, (bytes, bytearray)) else str(data).encode("latin-1")

def fpdf_to_bytes(doc: FPDF) -> bytes:
    data = doc.output(dest="S")
    return data if isinstance(data, (bytes, bytearray)) else str(data).encode("latin-1")

def mm_to_pt(mm: float) -> float:
    return mm * 72.0 / 25.4

# ---------- Chart helpers ----------
_KIND_RANK = {"SIGWX": 1, "SPC": 2, "Wind & Temp": 3, "Other": 9}

def guess_chart_kind_from_name(name: str) -> str:
    n = (name or "").upper()
    if "SIGWX" in n or "SIG WEATHER" in n: return "SIGWX"
    if "SPC" in n or "SURFACE" in n or "PRESSURE" in n: return "SPC"
    if "WIND" in n or "TEMP" in n or "ALOFT" in n or "FD" in n: return "Wind & Temp"
    return "Other"

def default_title_for_kind(kind: str) -> str:
    return {
        "SIGWX": "SIGWX — Significant Weather",
        "SPC": "Surface Pressure Chart",
        "Wind & Temp": "Winds & Temperatures Aloft",
        "Other": "Weather Chart",
    }.get(kind, "Weather Chart")

def chart_sort_key(c: Dict[str, Any]) -> Tuple[int, int]:
    rank = _KIND_RANK.get(c.get("kind","Other"), 9)
    order = int(c.get("order", 9999) or 9999)
    return (rank, order)

# ---------- PDF ----------
PASTEL = (90, 127, 179)

class BriefPDF(FPDF):
    def header(self): pass
    def footer(self): pass

    def draw_header_band(self, text: str):
        self.set_draw_color(229,231,235)
        self.set_line_width(0.3)
        self.set_font("Helvetica", "B", 18)
        self.cell(0, 12, text, ln=True, align="C", border="B")

    def add_fullbleed_image(self, img_png: io.BytesIO):
        # place image on current (landscape) page with top margin for header
        max_w = self.w - 22; max_h = self.h - 58
        img = Image.open(img_png); iw, ih = img.size
        r = min(max_w / iw, max_h / ih); w, h = int(iw * r), int(ih * r)
        x = (self.w - w) // 2; y = self.get_y() + 6
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG"); path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h); os.remove(path)
        self.ln(h + 10)

    def cover_with_numbered_index(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc
                                  ) -> Dict[str, Tuple[float,float,float,float]]:
        """
        Cover with a clean numbered index (01–06).
        Returns clickable rectangles (mm) for: ipma, charts, flight_plan, routes, notams, mass_balance
        """
        self.add_page(orientation="L")

        # Title / info
        self.set_xy(0, 20)
        self.set_font("Helvetica","B",32)
        self.cell(0, 16, "Briefing", ln=True, align="C")

        self.set_font("Helvetica","",14)
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
        self.set_font("Helvetica","B",16)
        self.cell(0, 10, "Index", ln=True, align="C")
        self.ln(2)

        items = [
            ("ipma", "METARs, TAFs, SIGMET & GAMET (IPMA)"),
            ("charts", "Charts"),
            ("flight_plan", "Flight Plan"),
            ("routes", "Routes"),
            ("notams", "NOTAMs"),
            ("mass_balance", "Mass & Balance"),
        ]

        rects_mm: Dict[str, Tuple[float,float,float,float]] = {}

        x_num = 35.0
        x_lbl = 60.0
        y     = 80.0
        step  = 16.5

        for i, (key, label) in enumerate(items, start=1):
            num = f"{i:02d}"
            # big number
            self.set_text_color(*PASTEL)
            self.set_xy(x_num, y-8)
            self.set_font("Helvetica","B",28)
            self.cell(0, 16, num, ln=0)
            # label
            self.set_text_color(15, 23, 42)
            self.set_xy(x_lbl, y-6)
            self.set_font("Helvetica","B",18)
            self.cell(0, 13, label, ln=1)
            # divider
            self.set_draw_color(220,224,228); self.set_line_width(0.3)
            self.line(x_lbl, y + 6.5, x_lbl + 210.0, y + 6.5)
            # clickable rect
            rects_mm[key] = (x_lbl - 2.0, y - 7.0, 215.0, 14.0)
            y += step

        self.set_text_color(0,0,0)
        return rects_mm

# ---------- UI: Tabs ----------
tab_mission, tab_charts, tab_fpmb, tab_pairs, tab_notams, tab_generate = st.tabs(
    ["Mission", "Charts", "Flight Plan & M&B", "Routes", "NOTAMs", "Generate PDF"]
)

# Mission
with tab_mission:
    st.markdown("### Mission")
    colA, colB, colC = st.columns(3)
    with colA:
        pilot = st.text_input("Pilot name", "Alexandre Moiteiro")
        callsign = st.text_input("Mission callsign", "RVP")
    with colB:
        aircraft_type = st.text_input("Aircraft type", "Tecnam P2008")
        regs = ["CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW","CS-ECC","CS-ECD"]
        registration = st.selectbox("Registration", regs, index=0)
    with colC:
        mission_no = st.text_input("Mission number", "")
        flight_date = st.date_input("Flight date")
        time_utc = st.text_input("UTC time", "")

# Charts (AUTO titles from detected type; no manual type/title fields)
with tab_charts:
    st.markdown("### Charts")
    st.caption("Upload SIGWX / Surface Pressure (SPC) / Winds & Temps / Other. Accepts PDF/PNG/JPG/JPEG/GIF (PDF uses first page).")
    preview_w = st.slider("Preview width (px)", min_value=240, max_value=640, value=460, step=10)
    uploads = st.file_uploader("Upload charts", type=["pdf","png","jpg","jpeg","gif"], accept_multiple_files=True)

    charts: List[Dict[str,Any]] = []
    if uploads:
        for idx, f in enumerate(uploads):
            raw = read_upload_bytes(f); mime = f.type or ""
            img_png = ensure_png_from_bytes(raw, mime)
            name = safe_str(getattr(f, "name", "")) or "(untitled)"
            kind = guess_chart_kind_from_name(name)
            title = default_title_for_kind(kind)  # AUTO title from type
            # show preview + detected title
            col_img, col_meta = st.columns([0.6, 0.4])
            with col_img:
                try: st.image(img_png.getvalue(), caption=name, width=preview_w)
                except Exception: st.write(name)
            with col_meta:
                st.write(f"**Detected type:** {kind}")
                st.write(f"**Title:** {title}")
            charts.append({"kind": kind, "title": title, "img_png": img_png, "order": idx+1, "filename": name})

# Flight Plan & M&B
with tab_fpmb:
    st.markdown("### Flight Plan & M&B")
    c1, c2 = st.columns(2)
    with c1:
        fp_upload = st.file_uploader("Flight Plan (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])
        if fp_upload: st.success(f"Flight Plan loaded: {safe_str(fp_upload.name)}")
    with c2:
        mb_upload = st.file_uploader("Mass & Balance (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])
        if mb_upload: st.success(f"M&B loaded: {safe_str(mb_upload.name)}")

# Routes
with tab_pairs:
    st.markdown("### Routes")
    st.caption("For each route (e.g., LPSO-LPCB) upload a Navlog and its VFR map. Accepts PDF/PNG/JPG/JPEG.")
    num_pairs = st.number_input("Number of route pairs", min_value=0, max_value=10, value=0, step=1)
    pairs: List[Dict[str, Any]] = []
    for i in range(int(num_pairs)):
        with st.expander(f"Route #{i+1}", expanded=False):
            route = safe_str(st.text_input("ROUTE (e.g., LPSO-LPCB)", key=f"pair_route_{i}")).upper().strip()
            c1, c2 = st.columns(2)
            with c1:
                nav_file = st.file_uploader(f"Navlog ({route or 'ROUTE'})", type=["pdf","png","jpg","jpeg"], key=f"pair_nav_{i}")
            with c2:
                vfr_file = st.file_uploader(f"VFR Map ({route or 'ROUTE'})", type=["pdf","png","jpg","jpeg"], key=f"pair_vfr_{i}")
            pairs.append({"route": route, "nav": nav_file, "vfr": vfr_file})

# NOTAMs
with tab_notams:
    st.markdown("### NOTAMs")
    st.caption("Upload the official NOTAMs PDF (or image). It will be appended into the NOTAMs section of the final PDF.")
    notams_upload = st.file_uploader("NOTAMs (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])

# Generate
with tab_generate:
    gen_pdf = st.button("Generate PDF")

# ---------- PyMuPDF helpers ----------
def open_upload_as_pdf(upload, orientation_for_images="L") -> Optional[fitz.Document]:
    if upload is None: return None
    raw = read_upload_bytes(upload)
    if not raw: return None
    mime = (getattr(upload, "type", "") or "").lower()
    if mime == "application/pdf":
        return fitz.open(stream=raw, filetype="pdf")
    # image -> pdf
    ext_bytes = image_bytes_to_pdf_bytes_fullbleed(raw, orientation=orientation_for_images)
    return fitz.open(stream=ext_bytes, filetype="pdf")

def add_cover_links(doc: fitz.Document, rects_mm: Dict[str, Tuple[float,float,float,float]],
                    targets: Dict[str, Optional[int]], ipma_url: str):
    """Clickable links on the cover (page 0)."""
    if doc.page_count == 0: return
    page0 = doc.load_page(0)
    for key, (x, y, w, h) in rects_mm.items():
        rect = fitz.Rect(mm_to_pt(x), mm_to_pt(y), mm_to_pt(x+w), mm_to_pt(y+h))
        if key == "ipma":
            page0.insert_link({"kind": fitz.LINK_URI, "from": rect, "uri": ipma_url})
        else:
            target = targets.get(key)
            if target is not None:
                page0.insert_link({"kind": fitz.LINK_GOTO, "from": rect, "page": int(target)})

def add_back_to_index_links(doc: fitz.Document, label: str = "Index"):
    """
    Adds a small, subtle 'Index' text link (no box) to every page except the cover.
    Top-right placement; grey text; invisible link rectangle only.
    """
    grey = (0.35, 0.35, 0.35)
    for pno in range(1, doc.page_count):
        page = doc.load_page(pno)
        pw, ph = page.rect.width, page.rect.height
        margin_mm = 6.0
        link_w_mm, link_h_mm = 18.0, 6.5  # small text area
        left = pw - mm_to_pt(margin_mm + link_w_mm)
        top  = mm_to_pt(margin_mm)
        rect = fitz.Rect(left, top, left + mm_to_pt(link_w_mm), top + mm_to_pt(link_h_mm))
        # place text (no border/box)
        page.insert_textbox(rect, label, fontsize=9, fontname="helv", align=1, color=grey)
        # make it clickable
        page.insert_link({"kind": fitz.LINK_GOTO, "from": rect, "page": 0})

# ---------- PDF generation ----------
if gen_pdf:
    pdf = BriefPDF(orientation="L", unit="mm", format="A4")

    # COVER with numbered simple index
    cover_rects_mm = pdf.cover_with_numbered_index(
        mission_no=safe_str(locals().get("mission_no","")),
        pilot=safe_str(locals().get("pilot","")),
        aircraft=safe_str(locals().get("aircraft_type","")),
        callsign=safe_str(locals().get("callsign","")),
        reg=safe_str(locals().get("registration","")),
        date_str=safe_str(locals().get("flight_date","")),
        time_utc=safe_str(locals().get("time_utc","")),
    )

    # CHARTS (immediately after cover) — AUTO TITLES based on detected type
    charts_local: List[Dict[str,Any]] = locals().get("charts", [])
    charts_first_page0: Optional[int] = None
    if charts_local:
        for idx, c in enumerate(sorted(charts_local, key=chart_sort_key)):
            pdf.add_page(orientation="L")
            if charts_first_page0 is None:
                charts_first_page0 = pdf.page_no() - 1  # 0-based
            pdf.draw_header_band(c["title"])  # auto title
            pdf.add_fullbleed_image(c["img_png"])

    # Export skeleton (cover + charts)
    skeleton_bytes = fpdf_to_bytes(pdf)
    main_doc = fitz.open(stream=skeleton_bytes, filetype="pdf")

    # Append in order and record first pages
    current_page_count = main_doc.page_count

    # Flight Plan
    fp_start_page = None
    fp_doc = open_upload_as_pdf(locals().get("fp_upload"))
    if fp_doc:
        fp_start_page = current_page_count
        main_doc.insert_pdf(fp_doc, start_at=current_page_count)
        current_page_count += fp_doc.page_count
        fp_doc.close()

    # Routes
    routes_start_page = None
    pairs_local: List[Dict[str, Any]] = locals().get("pairs", [])
    for p in (pairs_local or []):
        for up in [p.get("nav"), p.get("vfr")]:
            ext = open_upload_as_pdf(up, orientation_for_images="L")
            if ext:
                if routes_start_page is None:
                    routes_start_page = current_page_count
                main_doc.insert_pdf(ext, start_at=current_page_count)
                current_page_count += ext.page_count
                ext.close()

    # NOTAMs
    notams_start_page = None
    notams_doc = open_upload_as_pdf(locals().get("notams_upload"))
    if notams_doc:
        notams_start_page = current_page_count
        main_doc.insert_pdf(notams_doc, start_at=current_page_count)
        current_page_count += notams_doc.page_count
        notams_doc.close()

    # Mass & Balance
    mb_start_page = None
    mb_doc = open_upload_as_pdf(locals().get("mb_upload"))
    if mb_doc:
        mb_start_page = current_page_count
        main_doc.insert_pdf(mb_doc, start_at=current_page_count)
        current_page_count += mb_doc.page_count
        mb_doc.close()

    # Add cover links
    targets = {
        "ipma": None,  # external
        "charts": charts_first_page0,
        "flight_plan": fp_start_page,
        "routes": routes_start_page,
        "notams": notams_start_page,
        "mass_balance": mb_start_page,
    }
    add_cover_links(main_doc, cover_rects_mm, targets, IPMA_URL)

    # Add subtle 'Index' text link (no box) to every page except cover
    add_back_to_index_links(main_doc, label="Index")

    # Export
    final_bytes = main_doc.tobytes()
    main_doc.close()

    final_name = f"Briefing - Mission {safe_str(locals().get('mission_no') or 'X')}.pdf"
    st.download_button("Download PDF", data=final_bytes, file_name=final_name,
                       mime="application/pdf", use_container_width=True)


