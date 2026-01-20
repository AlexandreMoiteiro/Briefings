import io
import datetime as dt

import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

from reportlab.pdfgen import canvas


# Igual ao Tecnam: só o nome do ficheiro (assumido no root do repo)
PDF_TEMPLATE_PATHS = [
    "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf",
]


def read_pdf_bytes(paths) -> bytes:
    # Igual ao teu padrão: tenta caminhos e lê o primeiro que existir
    for path_str in paths:
        try:
            with open(path_str, "rb") as f:
                return f.read()
        except FileNotFoundError:
            continue
    raise FileNotFoundError(f"Template not found in any known path: {paths}")


def fill_pdf_form(template_bytes: bytes, fields: dict) -> PdfWriter:
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    root = reader.trailer["/Root"]
    if "/AcroForm" not in root:
        raise RuntimeError("Template PDF has no AcroForm/fields.")

    # keep AcroForm + make appearances
    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    try:
        writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): True})
    except Exception:
        pass

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)

    return writer


def cg_to_xy(cg_in, wt_lb, box, cg_rng, wt_rng):
    x0, y0, x1, y1 = box
    cg0, cg1 = cg_rng
    w0, w1 = wt_rng

    # clamp
    if cg1 == cg0 or w1 == w0:
        return x0, y0
    cg_in = max(min(cg_in, cg1), cg0)
    wt_lb = max(min(wt_lb, w1), w0)

    x = x0 + (cg_in - cg0) / (cg1 - cg0) * (x1 - x0)
    y = y0 + (wt_lb - w0) / (w1 - w0) * (y1 - y0)
    return x, y


def make_overlay_pdf(page_w, page_h, *, box, cg_rng, wt_rng, points, legend_xy, marker_r, show_box=True):
    """
    points = [{"label":"Empty","cg":..,"wt":..,"rgb":(r,g,b)}, ...]
    """
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=(page_w, page_h))

    # desenhar a bounding box para ajudar no fine-tune (opcional)
    if show_box:
        c.setLineWidth(1)
        c.setDash(3, 3)
        c.rect(box[0], box[1], box[2] - box[0], box[3] - box[1], stroke=1, fill=0)
        c.setDash()

    # markers
    for p in points:
        x, y = cg_to_xy(p["cg"], p["wt"], box, cg_rng, wt_rng)
        r, g, b = p["rgb"]
        c.setFillColorRGB(r, g, b)
        c.circle(x, y, marker_r, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 8)
        c.drawString(x + marker_r + 2, y - 3, p["label"])

    # legend (English)
    lx, ly = legend_xy
    c.setFont("Helvetica-Bold", 9)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(lx, ly, "Legend")
    ly -= 12

    c.setFont("Helvetica", 9)
    for p in points:
        r, g, b = p["rgb"]
        c.setFillColorRGB(r, g, b)
        c.rect(lx, ly - 7, 10, 10, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(lx + 14, ly - 5, p["label"])
        ly -= 14

    c.showPage()
    c.save()
    bio.seek(0)
    return bio.read()


def merge_overlay(writer: PdfWriter, overlay_bytes: bytes, page_index: int):
    overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
    overlay_page = overlay_reader.pages[0]
    base_page = writer.pages[page_index]
    base_page.merge_page(overlay_page)


st.set_page_config(page_title="PA-28 PDF Chart Tester", layout="wide")
st.title("PA-28 – PDF Fill + CG Chart Overlay (Tester)")

template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
reader0 = PdfReader(io.BytesIO(template_bytes))

# assume gráfico na página 1 (index 0) — podes trocar no selectbox
page_index = st.selectbox("Chart page index", options=list(range(len(reader0.pages))), index=0)

page0 = reader0.pages[page_index]
page_w = float(page0.mediabox.width)
page_h = float(page0.mediabox.height)

st.caption(f"Template: {PDF_TEMPLATE_PATHS[0]} | Page size: {page_w:.0f} x {page_h:.0f}")

# --- sliders de fine-tune do gráfico
st.subheader("1) Chart placement (fine-tune)")

cA, cB, cC = st.columns(3)
with cA:
    x0 = st.slider("Box x0", 0.0, page_w, 70.0, 1.0)
    y0 = st.slider("Box y0", 0.0, page_h, 160.0, 1.0)
with cB:
    x1 = st.slider("Box x1", 0.0, page_w, 330.0, 1.0)
    y1 = st.slider("Box y1", 0.0, page_h, 420.0, 1.0)
with cC:
    cg_min = st.number_input("CG min (in)", value=82.0, step=0.1)
    cg_max = st.number_input("CG max (in)", value=93.0, step=0.1)
    wt_min = st.number_input("WT min (lb)", value=1200.0, step=10.0)
    wt_max = st.number_input("WT max (lb)", value=2600.0, step=10.0)

box = (float(x0), float(y0), float(x1), float(y1))
cg_rng = (float(cg_min), float(cg_max))
wt_rng = (float(wt_min), float(wt_max))

show_box = st.checkbox("Show dashed box (debug)", value=True)

st.subheader("2) Test points (Empty / Takeoff / Landing)")

p1, p2, p3, p4 = st.columns(4)
with p1:
    empty_cg = st.number_input("Empty CG (in)", value=85.0, step=0.1)
    empty_wt = st.number_input("Empty WT (lb)", value=1650.0, step=10.0)
with p2:
    to_cg = st.number_input("Takeoff CG (in)", value=86.0, step=0.1)
    to_wt = st.number_input("Takeoff WT (lb)", value=2550.0, step=10.0)
with p3:
    ldg_cg = st.number_input("Landing CG (in)", value=85.7, step=0.1)
    ldg_wt = st.number_input("Landing WT (lb)", value=2400.0, step=10.0)
with p4:
    marker_r = st.slider("Marker radius", 2, 10, 4, 1)
    legend_x = st.slider("Legend X", 0.0, page_w, float(x1) + 15.0, 1.0)
    legend_y = st.slider("Legend Y", 0.0, page_h, float(y1) - 5.0, 1.0)

points = [
    {"label": "Empty",   "cg": float(empty_cg), "wt": float(empty_wt), "rgb": (0.10, 0.55, 0.10)},
    {"label": "Takeoff", "cg": float(to_cg),    "wt": float(to_wt),    "rgb": (0.10, 0.30, 0.85)},
    {"label": "Landing", "cg": float(ldg_cg),   "wt": float(ldg_wt),   "rgb": (0.85, 0.20, 0.20)},
]

st.subheader("3) Generate PDF")

# valores genéricos só para confirmar “enche”
# (o objetivo aqui é testar o overlay do gráfico)
fields = {
    "Date": dt.datetime.now().strftime("%d/%m/%Y"),
    "Aircraft_Reg": "OE-KPD",
    "Airfield_DEPARTURE": "LPCS",
    "Airfield_ARRIVAL": "LPSO",
    "Airfield_ALTERNATE_1": "LPVR",
    "Airfield_ALTERNATE_2": "LPEV",
    "Wind_DEPARTURE": "240/08",
    "Wind_ARRIVAL": "250/10",
    "Wind_ALTERNATE_1": "230/05",
    "Wind_ALTERNATE_2": "270/12",
}

if st.button("Generate test PDF", type="primary"):
    writer = fill_pdf_form(template_bytes, fields)

    overlay_bytes = make_overlay_pdf(
        page_w,
        page_h,
        box=box,
        cg_rng=cg_rng,
        wt_rng=wt_rng,
        points=points,
        legend_xy=(float(legend_x), float(legend_y)),
        marker_r=int(marker_r),
        show_box=show_box,
    )

    merge_overlay(writer, overlay_bytes, page_index=int(page_index))

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)

    st.download_button(
        "Download generated PDF",
        data=out.getvalue(),
        file_name="PA28_test_chart_overlay.pdf",
        mime="application/pdf",
    )
    st.success("PDF gerado. Abre, vê onde caem os pontos e afina os sliders (box / ranges / legend).")
