import io
import datetime as dt
import numpy as np

import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas


PDF_TEMPLATE = "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"

# Pontos de calibração (CG in / Weight lb / x / y) — os teus
CAL_POINTS = [
    (82, 1200, 182, 72),
    (82, 2050, 134, 245),
    (84, 2200, 178, 276),
    (85, 2295, 202, 294),
    (86, 2355, 228, 307),
    (87, 2440, 255, 322),
    (88, 2515, 285, 338),
    (89, 2550, 315, 343),
    (93, 2550, 435, 344),
]


def solve_affine(points):
    A, bx, by = [], [], []
    for cg, wt, x, y in points:
        A.append([cg, wt, 1])
        bx.append(x)
        by.append(y)

    A = np.array(A, dtype=float)
    bx = np.array(bx, dtype=float)
    by = np.array(by, dtype=float)

    ax = np.linalg.lstsq(A, bx, rcond=None)[0]
    ay = np.linalg.lstsq(A, by, rcond=None)[0]
    return ax, ay


AX, AY = solve_affine(CAL_POINTS)


def cg_wt_to_xy(cg, wt):
    x = AX[0] * cg + AX[1] * wt + AX[2]
    y = AY[0] * cg + AY[1] * wt + AY[2]
    return float(x), float(y)


def read_pdf_bytes():
    with open(PDF_TEMPLATE, "rb") as f:
        return f.read()


def detect_graph_page(reader: PdfReader) -> int:
    """
    Tenta encontrar a página do gráfico por texto.
    Se falhar (texto como imagem), devolve 0.
    """
    needles = ["C.G. ENVELOPE", "C.G. LOCATION", "WEIGHT", "NORMAL CATEGORY"]
    for i, p in enumerate(reader.pages):
        try:
            txt = (p.extract_text() or "").upper()
        except Exception:
            txt = ""
        if txt and any(n in txt for n in needles):
            return i
    return 0


def fill_pdf_form(template_bytes: bytes, fields: dict) -> PdfWriter:
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    root = reader.trailer["/Root"]
    if "/AcroForm" not in root:
        raise RuntimeError("Template PDF has no AcroForm/fields.")

    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    try:
        writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): True})
    except Exception:
        pass

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)

    return writer


def make_overlay_pdf(page_w, page_h, points, legend_xy=(460, 360), marker_r=4):
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=(page_w, page_h))

    # markers + labels
    for p in points:
        x, y = cg_wt_to_xy(p["cg"], p["wt"])
        r, g, b = p["rgb"]
        c.setFillColorRGB(r, g, b)
        c.circle(x, y, marker_r, fill=1, stroke=0)

        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 8)
        c.drawString(x + marker_r + 2, y - 3, p["label"])

    # legend
    lx, ly = legend_xy
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 9)
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


# ---------------- UI ----------------
st.set_page_config(page_title="PA-28 PDF Tester (Clean)", layout="centered")
st.title("PA-28 – PDF Tester (Clean)")
st.caption("Agora desenha no sítio certo: página do gráfico auto-detetada (fallback = página 1).")

template_bytes = read_pdf_bytes()
reader = PdfReader(io.BytesIO(template_bytes))

graph_page_index = detect_graph_page(reader)
st.write(f"Graph page index (0-based): **{graph_page_index}**")

# Valores genéricos (só teste)
EMPTY_WT, EMPTY_CG = 1650, 85.0
TO_WT, TO_CG = 2550, 86.0
LDG_WT, LDG_CG = 2400, 85.7

# Preenchimento mínimo só para confirmar campos
fields = {
    "Date": dt.datetime.now().strftime("%d/%m/%Y"),
    "Aircraft_Reg": "OE-KPD",

    # ATENÇÃO: este template faz somas internas.
    # Para testar, mete só linhas 1..5.
    "Weight(lbs)_1": f"{EMPTY_WT}",
    "Moment(in-lbs)_1": f"{int(EMPTY_WT * EMPTY_CG)}",

    "Weight(lbs)_2": "340",
    "Moment(in-lbs)_2": f"{int(340 * 80.5)}",

    "Weight(lbs)_3": "0",
    "Moment(in-lbs)_3": "0",

    "Weight(lbs)_4": "288",
    "Moment(in-lbs)_4": f"{int(288 * 95.0)}",

    "Weight(lbs)_5": "40",
    "Moment(in-lbs)_5": f"{int(40 * 142.8)}",
}

if st.button("Generate PDF", type="primary"):
    writer = fill_pdf_form(template_bytes, fields)

    gp = reader.pages[graph_page_index]
    pw = float(gp.mediabox.width)
    ph = float(gp.mediabox.height)

    points = [
        {"label": "Empty",   "cg": EMPTY_CG, "wt": EMPTY_WT, "rgb": (0.10, 0.60, 0.10)},
        {"label": "Takeoff", "cg": TO_CG,    "wt": TO_WT,    "rgb": (0.10, 0.30, 0.85)},
        {"label": "Landing", "cg": LDG_CG,   "wt": LDG_WT,   "rgb": (0.85, 0.20, 0.20)},
    ]

    overlay = make_overlay_pdf(pw, ph, points, legend_xy=(460, 360), marker_r=4)
    overlay_page = PdfReader(io.BytesIO(overlay)).pages[0]
    writer.pages[graph_page_index].merge_page(overlay_page)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)

    st.download_button(
        "Download PDF",
        data=out.getvalue(),
        file_name="PA28_test_clean_fixedpage.pdf",
        mime="application/pdf",
    )
    st.success("Gerado. Agora os pontos/legenda estão na página do gráfico certa.")


