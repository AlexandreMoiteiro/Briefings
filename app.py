# app.py — Briefings (no AI) — A4 Landscape, fast & lean
# Order: Cover → Charts → Flight Plan → Routes → NOTAMs → Mass & Balance
from typing import Dict, Any, List, Tuple, Optional
import io, os, tempfile
import streamlit as st
from PIL import Image
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
A4_PORTRAIT = fitz.paper_size("a4")          # (width=595, height=842) pt
A4_LANDSCAPE = (A4_PORTRAIT[1], A4_PORTRAIT[0])  # (842, 595) pt

def safe_str(x) -> str:
    try: return "" if x is None else str(x)
    except Exception: return ""

def read_upload_bytes(upload) -> bytes:
    if upload is None: return b""
    try: return upload.getvalue() if hasattr(upload, "getvalue") else upload.read()
    except Exception: return b""

def mm_to_pt(mm: float) -> float:
    return mm * 72.0 / 25.4

def compress_image_to_jpeg(img_bytes: bytes, max_px: int = 2200, quality: int = 82) -> bytes:
    """Resize (keeping aspect) so max side <= max_px and save as JPEG to cut size."""
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        scale = min(1.0, float(max_px) / max(w, h)) if max(w, h) > max_px else 1.0
        if scale < 1.0:
            img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
        return out.getvalue()
    except Exception:
        return img_bytes  # fallback

def detect_chart_kind(name: str) -> str:
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

# ---------- Preview helper (lightweight) ----------
def preview_first_page_as_png(file_bytes: bytes, mime: str, dpi: int = 150) -> bytes:
    """For UI preview only (fast). If PDF, render first page at low DPI; else just JPEG-compress."""
    try:
        if (mime or "").lower() == "application/pdf":
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            pix = doc.load_page(0).get_pixmap(dpi=dpi)
            return pix.tobytes("png")
        # image
        return compress_image_to_jpeg(file_bytes, max_px=1600, quality=75)
    except Exception:
        return b""

# ---------- Overlay decorations (PyMuPDF) ----------
def add_header_text(page: fitz.Page, title: str, add_index_link: bool = True):
    """Draw a small header text (centered) and optional '← Index' link top-right. Subtle and unobtrusive."""
    pw, ph = page.rect.width, page.rect.height
    top_pad = mm_to_pt(8)
    # centered title
    page.insert_textbox(
        fitz.Rect(mm_to_pt(10), top_pad, pw - mm_to_pt(10), top_pad + mm_to_pt(8)),
        title, fontsize=11, fontname="helv", align=1, color=(0,0,0)
    )
    if add_index_link:
        label = "← Index"
        w = page.get_text_length(label, fontsize=10, fontname="helv")
        x2 = pw - mm_to_pt(8)
        x1 = x2 - w - mm_to_pt(3)
        y1 = mm_to_pt(6.5); y2 = y1 + mm_to_pt(6)
        rect = fitz.Rect(x1, y1, x2, y2)
        page.insert_textbox(rect, label, fontsize=10, fontname="helv", align=2, color=(0,0,0))
        page.insert_link({"kind": fitz.LINK_GOTO, "from": rect, "page": 0})

def add_cover_links(doc: fitz.Document, rects_mm: Dict[str, Tuple[float,float,float,float]],
                    targets: Dict[str, Optional[int]], ipma_url: str):
    """Clickable cover links (page 0)."""
    if doc.page_count == 0: return
    page0 = doc.load_page(0)
    for key, (x, y, w, h) in rects_mm.items():
        rect = fitz.Rect(mm_to_pt(x), mm_to_pt(y), mm_to_pt(x+w), mm_to_pt(y+h))
        if key == "ipma":
            page0.insert_link({"kind": fitz.LINK_URI, "from": rect, "uri": ipma_url})
        else:
            p = targets.get(key)
            if p is not None:
                page0.insert_link({"kind": fitz.LINK_GOTO, "from": rect, "page": int(p)})

def set_bookmarks(doc: fitz.Document, marks: List[Tuple[int, str]]):
    """Add PDF outline (bookmarks). marks: list of (page0, title)."""
    toc = []
    for p0, title in marks:
        if p0 is not None:
            toc.append([1, title, p0 + 1])  # PyMuPDF expects 1-based pages here
    if toc:
        doc.set_toc(toc)

# ---------- Cover builder (clean numbered index) ----------
def build_cover(doc: fitz.Document, info: Dict[str, str]) -> Dict[str, Tuple[float,float,float,float]]:
    page = doc.new_page(width=A4_LANDSCAPE[0], height=A4_LANDSCAPE[1])
    pw, ph = page.rect.width, page.rect.height

    # Title
    page.insert_textbox(
        fitz.Rect(0, mm_to_pt(18), pw, mm_to_pt(18) + mm_to_pt(12)),
        "Briefing", fontsize=32, fontname="helv", align=1, color=(0,0,0)
    )
    # Info line
    info_parts = []
    if info.get("mission"): info_parts.append(f"Mission: {info['mission']}")
    if info.get("pilot"): info_parts.append(f"Pilot: {info['pilot']}")
    if info.get("aircraft"): info_parts.append(f"Aircraft: {info['aircraft']}")
    if info.get("callsign"): info_parts.append(f"Callsign: {info['callsign']}")
    if info.get("reg"): info_parts.append(f"Reg: {info['reg']}")
    if info_parts:
        page.insert_textbox(
            fitz.Rect(0, mm_to_pt(32), pw, mm_to_pt(32) + mm_to_pt(8)),
            "   ".join(info_parts), fontsize=14, fontname="helv", align=1, color=(0,0,0)
        )
    # Date/UTC
    date_line = "   ".join([s for s in [f"Date: {info.get('date','')}" if info.get("date") else "",
                                        f"UTC: {info.get('utc','')}" if info.get("utc") else ""] if s])
    if date_line:
        page.insert_textbox(
            fitz.Rect(0, mm_to_pt(40), pw, mm_to_pt(40) + mm_to_pt(8)),
            date_line, fontsize=14, fontname="helv", align=1, color=(0,0,0)
        )

    # Numbered index (simple & visible)
    items = [
        ("ipma", "METARs, TAFs, SIGMET & GAMET (IPMA)"),
        ("charts", "Charts"),
        ("flight_plan", "Flight Plan"),
        ("routes", "Routes"),
        ("notams", "NOTAMs"),
        ("mass_balance", "Mass & Balance"),
    ]
    rects_mm: Dict[str, Tuple[float,float,float,float]] = {}
    x_num = mm_to_pt(35); x_lbl = mm_to_pt(60); y = mm_to_pt(80); step = mm_to_pt(16.5)

    for i, (key, label) in enumerate(items, start=1):
        num = f"{i:02d}"
        # number
        page.insert_text(fitz.Point(x_num, y), num, fontsize=28, fontname="helv", color=(0.35,0.5,0.7))
        # label
        page.insert_text(fitz.Point(x_lbl, y), label, fontsize=18, fontname="helv", color=(0,0,0))
        # divider
        page.draw_line(fitz.Point(x_lbl, y + mm_to_pt(6.5)), fitz.Point(x_lbl + mm_to_pt(210), y + mm_to_pt(6.5)),
                       color=(0.86,0.88,0.9), width=0.7)
        # clickable rect area (mm values to return)
        rects_mm[key] = ( (x_lbl/72*25.4) - 2.0, (y/72*25.4) - 7.0, 215.0, 14.0 )
        y += step

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

# Charts (auto-detect kind; title auto-filled — you can override)
with tab_charts:
    st.markdown("### Charts")
    st.caption("Upload SIGWX / Surface Pressure (SPC) / Winds & Temps / Other. Accepts PDF/PNG/JPG/JPEG/GIF.")
    preview_w = st.slider("Preview width (px)", min_value=240, max_value=640, value=460, step=10)
    uploads = st.file_uploader("Upload charts", type=["pdf","png","jpg","jpeg","gif"], accept_multiple_files=True)

    charts: List[Dict[str,Any]] = []
    if uploads:
        for idx, f in enumerate(uploads):
            raw = read_upload_bytes(f); mime = f.type or ""
            prev = preview_first_page_as_png(raw, mime, dpi=130)
            name = safe_str(getattr(f, "name", "")) or "(untitled)"
            kind = detect_chart_kind(name)
            auto_title = default_title_for_kind(kind)
            col_img, col_meta = st.columns([0.55, 0.45])
            with col_img:
                if prev: st.image(prev, caption=name, width=preview_w)
                else: st.write(name)
            with col_meta:
                st.markdown(f"**Detected:** {kind}")
                title = st.text_input("Title (optional)", value=auto_title, key=f"title_{idx}")
                order_val = st.number_input("Order", min_value=1, max_value=len(uploads)+10, value=idx+1, step=1, key=f"ord_{idx}")
            charts.append({"order": order_val, "title": title.strip(), "upload": f, "mime": mime, "kind": kind, "name": name})

# Flight Plan & M&B
with tab_fpmb:
    st.markdown("### Flight Plan & M&B")
    c1, c2 = st.columns(2)
    with c1:
        fp_upload = st.file_uploader("Flight Plan (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])
        if fp_upload: st.success(f"Flight Plan loaded: {safe_str(fp_upload.name)}")
    with c2:
        mb_upload = st.file_uploader("Mass & Balance (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])
        if mb_upload: st.success(f"M & B loaded: {safe_str(mb_upload.name)}")

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
    st.caption("Upload the official NOTAMs PDF (or image). It will be appended to the NOTAMs section.")
    notams_upload = st.file_uploader("NOTAMs (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])

# Generate
with tab_generate:
    gen_pdf = st.button("Generate PDF")

# ---------- Building with PyMuPDF only (fast & small) ----------
def open_upload_as_doc(upload) -> Optional[fitz.Document]:
    """Open upload as a PyMuPDF Document. Images are converted to a single-page JPEG-based PDF."""
    if upload is None: return None
    raw = read_upload_bytes(upload)
    if not raw: return None
    m = (getattr(upload, "type", "") or "").lower()
    if m == "application/pdf":
        return fitz.open(stream=raw, filetype="pdf")
    # image -> compress & embed into a new PDF page
    jpeg = compress_image_to_jpeg(raw, max_px=2200, quality=82)
    doc = fitz.open()
    page = doc.new_page(width=A4_LANDSCAPE[0], height=A4_LANDSCAPE[1])
    # fit image within margins under a small header band
    margin = mm_to_pt(10)
    header_h = mm_to_pt(12)
    rect = fitz.Rect(margin, margin + header_h, A4_LANDSCAPE[0]-margin, A4_LANDSCAPE[1]-margin)
    page.insert_image(rect, stream=jpeg, keep_proportion=True)
    add_header_text(page, "")  # just the Index link
    return doc

def append_section_docs(main_doc: fitz.Document, docs: List[fitz.Document], section_title_first: Optional[str] = None,
                        add_titles: bool = True) -> Optional[int]:
    """Append docs; return the 0-based page of the first appended page. Overlay header / index link."""
    start = None
    for i, d in enumerate(docs):
        if d is None: continue
        p0 = main_doc.page_count
        main_doc.insert_pdf(d, start_at=p0)
        if start is None: start = p0
        # overlay header text (subtle) on appended pages
        if add_titles:
            title = section_title_first if (i == 0 and section_title_first) else None
            for p in range(p0, p0 + d.page_count):
                page = main_doc.load_page(p)
                add_header_text(page, title or "", add_index_link=True)
        d.close()
    return start

def add_charts(main_doc: fitz.Document, chart_items: List[Dict[str, Any]]) -> Optional[int]:
    """Append charts; PDFs inserted as-is; Images compressed and placed on new pages. Adds header with title."""
    if not chart_items: return None
    start = None
    for item in chart_items:
        up = item["upload"]; mime = item["mime"]; title = item["title"] or default_title_for_kind(item["kind"])
        raw = read_upload_bytes(up)
        if not raw: continue
        if mime.lower() == "application/pdf":
            d = fitz.open(stream=raw, filetype="pdf")
            p0 = main_doc.page_count
            main_doc.insert_pdf(d, start_at=p0)
            if start is None: start = p0
            # overlay header title on each inserted page
            for p in range(p0, p0 + d.page_count):
                page = main_doc.load_page(p)
                add_header_text(page, title, add_index_link=True)
            d.close()
        else:
            # image => compress and put on a new page
            jpeg = compress_image_to_jpeg(raw, max_px=2200, quality=82)
            page = main_doc.new_page(width=A4_LANDSCAPE[0], height=A4_LANDSCAPE[1])
            # header
            add_header_text(page, title, add_index_link=True)
            # image area below header
            margin = mm_to_pt(10); header_h = mm_to_pt(12)
            rect = fitz.Rect(margin, margin + header_h, A4_LANDSCAPE[0]-margin, A4_LANDSCAPE[1]-margin)
            page.insert_image(rect, stream=jpeg, keep_proportion=True)
            if start is None: start = page.number
    return start

# ---------- Generate PDF ----------
if gen_pdf:
    # Build document
    main = fitz.open()

    # Cover
    cover_rects = build_cover(main, {
        "mission": safe_str(mission_no),
        "pilot": safe_str(pilot),
        "aircraft": safe_str(aircraft_type),
        "callsign": safe_str(callsign or "RVP"),
        "reg": safe_str(registration),
        "date": safe_str(flight_date),
        "utc": safe_str(time_utc),
    })

    # Charts (sorted by 'order')
    charts_sorted = sorted(locals().get("charts", []), key=lambda x: int(x.get("order", 9999)))
    charts_start = add_charts(main, charts_sorted)

    # Flight Plan
    fp_doc = open_upload_as_doc(locals().get("fp_upload"))
    fp_start = append_section_docs(main, [fp_doc], section_title_first="Flight Plan")

    # Routes  (append each nav & vfr in order provided)
    route_docs: List[fitz.Document] = []
    for p in (locals().get("pairs") or []):
        for up in [p.get("nav"), p.get("vfr")]:
            d = open_upload_as_doc(up)
            if d: route_docs.append(d)
    routes_start = append_section_docs(main, route_docs, section_title_first="Routes")

    # NOTAMs
    notams_doc = open_upload_as_doc(locals().get("notams_upload"))
    notams_start = append_section_docs(main, [notams_doc], section_title_first="NOTAMs")

    # M & B
    mb_doc = open_upload_as_doc(locals().get("mb_upload"))
    mb_start = append_section_docs(main, [mb_doc], section_title_first="Mass & Balance")

    # Cover links + bookmarks
    add_cover_links(main, cover_rects, {
        "ipma": None,
        "charts": charts_start,
        "flight_plan": fp_start,
        "routes": routes_start,
        "notams": notams_start,
        "mass_balance": mb_start,
    }, IPMA_URL)

    set_bookmarks(main, [
        (0, "Cover"),
        (charts_start, "Charts"),
        (fp_start, "Flight Plan"),
        (routes_start, "Routes"),
        (notams_start, "NOTAMs"),
        (mb_start, "Mass & Balance"),
    ])

    # Export
    final_bytes = main.tobytes()
    main.close()

    final_name = f"Briefing - Mission {safe_str(mission_no or 'X')}.pdf"
    st.download_button("Download PDF", data=final_bytes, file_name=final_name,
                       mime="application/pdf", use_container_width=True)


