# app.py — Briefings (sem IA) — PDF Landscape com capa + Charts + Flight Plan + Rotas + NOTAMs + M&B
from typing import Dict, Any, List, Tuple
import io, os, re, tempfile
import streamlit as st
from PIL import Image
from fpdf import FPDF
import fitz  # PyMuPDF

# ---------- Config página & estilos ----------
st.set_page_config(page_title="Briefings", layout="wide")
st.markdown("""
<style>
:root { --muted:#6b7280; --line:#e5e7eb; --ink:#0f172a; --bg:#ffffff; }
.app-top { display:flex; align-items:center; gap:.75rem; flex-wrap:wrap; margin: .25rem 0 .5rem }
.app-title { font-size: 2.2rem; font-weight: 800; margin: 0 }
.btnbar a{display:inline-block;padding:6px 10px;border:1px solid var(--line);
  border-radius:8px;text-decoration:none;font-weight:600;color:#111827;background:#f8fafc}
.btnbar a:hover{background:#f1f5f9}
.section-card{ border:1px solid var(--line); border-radius:14px; padding:14px 16px; background:var(--bg); }
hr{border:none;border-top:1px solid var(--line); margin:12px 0}
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }
</style>
""", unsafe_allow_html=True)

# ---------- Links topo ----------
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
    """Aceita PDF/PNG/JPG/JPEG/GIF e devolve bytes PNG (primeira página no caso de PDF)."""
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
    """Imagem -> 1 página PDF full-bleed A4 (landscape)."""
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

# ---------- Charts helpers ----------
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

    def page_landscape(self):
        if self.cur_orientation != "L":
            self.add_page(orientation="L")
        else:
            self.add_page()

    def add_fullbleed_image(self, img_png: io.BytesIO):
        max_w = self.w - 22; max_h = self.h - 58
        img = Image.open(img_png); iw, ih = img.size
        r = min(max_w / iw, max_h / ih); w, h = int(iw * r), int(ih * r)
        x = (self.w - w) // 2; y = self.get_y() + 6
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp, format="PNG"); path = tmp.name
        self.image(path, x=x, y=y, w=w, h=h); os.remove(path)
        self.ln(h + 10)

    def section_cover(self, mission_no, pilot, aircraft, callsign, reg, date_str, time_utc,
                      links: Dict[str, int], ext_ipma_url: str):
        self.page_landscape()
        # Pré-atribuir temporariamente destinos aos links internos (evita erro do FPDF)
        for k, lid in (links or {}).items():
            try:
                self.set_link(lid, y=0, page=self.page_no())  # temporário, será atualizado nas secções
            except Exception:
                pass

        self.set_xy(0, 22)
        self.set_font("Helvetica","B",30)
        self.cell(0, 16, "Briefing", ln=True, align="C")

        self.set_font("Helvetica","",14)
        if any([mission_no, pilot, aircraft, callsign, reg]):
            self.cell(0, 9, f"Mission: {mission_no}    Pilot: {pilot}    Aircraft: {aircraft}    Callsign: {callsign}    Reg: {reg}",
                      ln=True, align="C")
        if date_str or time_utc:
            self.cell(0, 9, f"Date: {date_str}    UTC: {time_utc}", ln=True, align="C")
        self.ln(6)

        # Índice simples
        left = 30
        lh = 9
        self.set_font("Helvetica","B",14); self.cell(0,10,"Índice", ln=True, align="C")
        self.set_font("Helvetica","",12)

        # 1. Externo (IPMA)
        self.set_text_color(20,40,120)
        self.set_xy(left, self.get_y()); self.cell(0, lh, "METARs, TAFs, SIGMET & GAMET (IPMA)", link=ext_ipma_url, ln=True)
        self.set_text_color(0,0,0)

        def item(label: str, key: str):
            link_id = links.get(key)
            self.set_xy(left, self.get_y())
            if link_id:
                self.cell(0, lh, label, link=link_id, ln=True)
            else:
                self.cell(0, lh, label, ln=True)

        item("Charts", "charts")
        item("Flight Plan", "flight_plan")
        item("Rotas", "routes")
        item("NOTAMs", "notams")
        item("Mass & Balance", "mass_balance")

    def section_anchor(self, title: str, link_id: int):
        self.page_landscape()
        self.draw_header_band(title)
        # Atualiza destino real do link interno para esta página
        self.set_link(link_id, y=self.get_y(), page=self.page_no())

# ---------- UI: Abas ----------
tab_mission, tab_charts, tab_pairs, tab_notams, tab_fpmb, tab_generate = st.tabs(
    ["Missão", "Charts", "Rotas", "NOTAMs", "Flight Plan & M&B", "Gerar PDF"]
)

# Missão
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

# Charts
with tab_charts:
    st.markdown("### Charts")
    st.caption("Carrega SIGWX / Surface Pressure (SPC) / Winds & Temps / Outros. Aceita PDF/PNG/JPG/JPEG/GIF.")
    preview_w = st.slider("Largura da pré-visualização (px)", min_value=240, max_value=640, value=460, step=10)
    uploads = st.file_uploader("Upload charts", type=["pdf","png","jpg","jpeg","gif"], accept_multiple_files=True)

    charts: List[Dict[str,Any]] = []
    if uploads:
        for idx, f in enumerate(uploads):
            raw = read_upload_bytes(f); mime = f.type or ""
            img_png = ensure_png_from_bytes(raw, mime)
            name = safe_str(getattr(f, "name", "")) or "(sem nome)"
            col_img, col_meta = st.columns([0.5, 0.5])
            with col_img:
                try: st.image(img_png.getvalue(), caption=name, width=preview_w)
                except Exception: st.write(name)
            with col_meta:
                kind_guess = guess_chart_kind_from_name(name)
                kind = st.selectbox(f"Tipo do chart #{idx+1}", ["SIGWX","SPC","Wind & Temp","Other"],
                                    index=["SIGWX","SPC","Wind & Temp","Other"].index(kind_guess),
                                    key=f"kind_{idx}")
                title_default = default_title_for_kind(kind)
                title = st.text_input("Título", value=title_default, key=f"title_{idx}")
                subtitle = st.text_input("Subtítulo (opcional)", value="", key=f"subtitle_{idx}")
                order_val = st.number_input("Ordem", min_value=1, max_value=len(uploads)+10, value=idx+1, step=1, key=f"ord_{idx}")
            charts.append({"kind": kind, "title": title, "subtitle": subtitle, "img_png": img_png, "order": order_val, "filename": name})

# Rotas
with tab_pairs:
    st.markdown("### Rotas")
    st.caption("Para cada rota (ex.: LPSO-LPCB) carrega um Navlog e o respetivo mapa VFR. Aceita PDF/PNG/JPG/JPEG.")
    num_pairs = st.number_input("Número de pares (Rotas)", min_value=0, max_value=10, value=0, step=1)
    pairs: List[Dict[str, Any]] = []
    for i in range(int(num_pairs)):
        with st.expander(f"Rota #{i+1}", expanded=False):
            route = safe_str(st.text_input("ROTA (ex.: LPSO-LPCB)", key=f"pair_route_{i}")).upper().strip()
            c1, c2 = st.columns(2)
            with c1:
                nav_file = st.file_uploader(f"Navlog ({route or 'ROTA'})", type=["pdf","png","jpg","jpeg"], key=f"pair_nav_{i}")
            with c2:
                vfr_file = st.file_uploader(f"VFR Map ({route or 'ROTA'})", type=["pdf","png","jpg","jpeg"], key=f"pair_vfr_{i}")
            pairs.append({"route": route, "nav": nav_file, "vfr": vfr_file})

# NOTAMs (PDF/Imagem embutido)
with tab_notams:
    st.markdown("### NOTAMs")
    st.caption("Carrega o PDF oficial de NOTAMs (ou imagem). Será inserido no PDF final na secção NOTAMs.")
    notams_upload = st.file_uploader("NOTAMs (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])

# Flight Plan & M&B
with tab_fpmb:
    st.markdown("### Flight Plan & M&B")
    c1, c2 = st.columns(2)
    with c1:
        fp_upload = st.file_uploader("Flight Plan (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])
        if fp_upload: st.success(f"Flight Plan carregado: {safe_str(fp_upload.name)}")
    with c2:
        mb_upload = st.file_uploader("Mass & Balance (PDF/PNG/JPG)", type=["pdf","png","jpg","jpeg"])
        if mb_upload: st.success(f"M&B carregado: {safe_str(mb_upload.name)}")

# Gerar
with tab_generate:
    gen_pdf = st.button("Generate PDF")

# ---------- Inserir externos no PDF (com PyMuPDF) ----------
def merge_external_after(main: fitz.Document, insert_pos: int, upload, orientation_for_images="L") -> int:
    if upload is None:
        return insert_pos
    raw = read_upload_bytes(upload)
    mime = (getattr(upload, "type", "") or "").lower()
    if mime == "application/pdf":
        ext = fitz.open(stream=raw, filetype="pdf")
    else:
        ext_bytes = image_bytes_to_pdf_bytes_fullbleed(raw, orientation=orientation_for_images)
        ext = fitz.open(stream=ext_bytes, filetype="pdf")
    main.insert_pdf(ext, start_at=insert_pos)
    insert_pos += ext.page_count
    ext.close()
    return insert_pos

# ---------- Geração do PDF na ordem pedida ----------
if gen_pdf:
    pdf = BriefPDF(orientation="L", unit="mm", format="A4")

    # Links internos
    links = {
        "charts": pdf.add_link(),
        "flight_plan": pdf.add_link(),
        "routes": pdf.add_link(),
        "notams": pdf.add_link(),
        "mass_balance": pdf.add_link(),
    }

    # CAPA
    pdf.section_cover(
        mission_no=safe_str(locals().get("mission_no","")),
        pilot=safe_str(locals().get("pilot","")),
        aircraft=safe_str(locals().get("aircraft_type","")),
        callsign=safe_str(locals().get("callsign","")),
        reg=safe_str(locals().get("registration","")),
        date_str=safe_str(locals().get("flight_date","")),
        time_utc=safe_str(locals().get("time_utc","")),
        links=links,
        ext_ipma_url=IPMA_URL
    )

    # CHARTS
    pdf.section_anchor("Charts", links["charts"])
    charts_local: List[Dict[str,Any]] = locals().get("charts", [])
    if charts_local:
        for c in sorted(charts_local, key=chart_sort_key):
            title, subtitle, img_png = c["title"], c["subtitle"], c["img_png"]
            pdf.page_landscape()
            pdf.draw_header_band(title or "Chart")
            if subtitle:
                pdf.set_font("Helvetica","I",12); pdf.cell(0,9,subtitle, ln=True, align="C")
            pdf.add_fullbleed_image(img_png)
    else:
        pdf.set_font("Helvetica","I",12); pdf.ln(6); pdf.cell(0,8,"(Sem charts carregados)", ln=True, align="C")

    # Exportar parte FPDF para começarmos a mesclar anexos
    base_bytes = fpdf_to_bytes(pdf)
    main_doc = fitz.open(stream=base_bytes, filetype="pdf")
    insert_pos = main_doc.page_count

    # FLIGHT PLAN (secção + ficheiro)
    sec_fp = BriefPDF(orientation="L", unit="mm", format="A4")
    sec_fp.section_anchor("Flight Plan", links["flight_plan"])
    sec_fp_bytes = fpdf_to_bytes(sec_fp)
    sec_fp_doc = fitz.open(stream=sec_fp_bytes, filetype="pdf")
    main_doc.insert_pdf(sec_fp_doc, start_at=insert_pos); insert_pos += sec_fp_doc.page_count; sec_fp_doc.close()
    insert_pos = merge_external_after(main_doc, insert_pos, locals().get("fp_upload"))

    # ROTAS (secção + pares)
    sec_routes = BriefPDF(orientation="L", unit="mm", format="A4")
    sec_routes.section_anchor("Rotas", links["routes"])
    sec_routes_bytes = fpdf_to_bytes(sec_routes)
    sec_routes_doc = fitz.open(stream=sec_routes_bytes, filetype="pdf")
    main_doc.insert_pdf(sec_routes_doc, start_at=insert_pos); insert_pos += sec_routes_doc.page_count; sec_routes_doc.close()

    nav_pairs: List[Dict[str, Any]] = locals().get("pairs", [])
    for p in (nav_pairs or []):
        route = p.get("route") or "ROTA"
        # mini separador por rota
        sr = BriefPDF(orientation="L", unit="mm", format="A4")
        sr.page_landscape(); sr.draw_header_band(f"Rota — {route}")
        sr.set_font("Helvetica","",12); sr.ln(4); sr.cell(0,8,"Navlog e Mapa VFR nas páginas seguintes.", ln=True, align="C")
        sr_bytes = fpdf_to_bytes(sr)
        sr_doc = fitz.open(stream=sr_bytes, filetype="pdf")
        main_doc.insert_pdf(sr_doc, start_at=insert_pos); insert_pos += sr_doc.page_count; sr_doc.close()
        insert_pos = merge_external_after(main_doc, insert_pos, p.get("nav"))
        insert_pos = merge_external_after(main_doc, insert_pos, p.get("vfr"), orientation_for_images="L")

    # NOTAMs (secção + ficheiro)
    sec_notams = BriefPDF(orientation="L", unit="mm", format="A4")
    sec_notams.section_anchor("NOTAMs", links["notams"])
    sec_notams_bytes = fpdf_to_bytes(sec_notams)
    sec_notams_doc = fitz.open(stream=sec_notams_bytes, filetype="pdf")
    main_doc.insert_pdf(sec_notams_doc, start_at=insert_pos); insert_pos += sec_notams_doc.page_count; sec_notams_doc.close()
    insert_pos = merge_external_after(main_doc, insert_pos, locals().get("notams_upload"))

    # M&B (secção + ficheiro)
    sec_mb = BriefPDF(orientation="L", unit="mm", format="A4")
    sec_mb.section_anchor("Mass & Balance", links["mass_balance"])
    sec_mb_bytes = fpdf_to_bytes(sec_mb)
    sec_mb_doc = fitz.open(stream=sec_mb_bytes, filetype="pdf")
    main_doc.insert_pdf(sec_mb_doc, start_at=insert_pos); insert_pos += sec_mb_doc.page_count; sec_mb_doc.close()
    insert_pos = merge_external_after(main_doc, insert_pos, locals().get("mb_upload"))

    # Exportar
    final_bytes = main_doc.tobytes()
    main_doc.close()

    final_name = f"Briefing - Missao {safe_str(locals().get('mission_no') or 'X')}.pdf"
    st.download_button("Download PDF", data=final_bytes, file_name=final_name,
                       mime="application/pdf", use_container_width=True)

