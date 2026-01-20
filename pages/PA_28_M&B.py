import io
import datetime as dt
import numpy as np

import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas


# ============================================================
# CONFIG
# ============================================================

PDF_TEMPLATE = "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"
GRAPH_PAGE_INDEX = 1   # página onde está o gráfico (0-based)

# --- Pontos de calibração (CG in / Weight lb / x / y)
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


# ============================================================
# AFFINE TRANSFORM (CG/WT → PDF x/y)
# ============================================================

def solve_affine(points):
    A, bx, by = [], [], []
    for cg, wt, x, y in points:
        A.append([cg, wt, 1])
        bx.append(x)
        by.append(y)

    A = np.array(A)
    bx = np.array(bx)
    by = np.array(by)

    ax = np.linalg.lstsq(A, bx, rcond=None)[0]
    ay = np.linalg.lstsq(A, by, rcond=None)[0]
    return ax, ay


AX, AY = solve_affine(CAL_POINTS)


def cg_wt_to_xy(cg, wt):
    x = AX[0] * cg + AX[1] * wt + AX[2]
    y = AY[0] * cg + AY[1] * wt + AY[2]
    return x, y


# ============================================================
# PDF HELPERS
# ============================================================

def read_pdf():
    with open(PDF_TEMPLATE, "rb") as f:
        return f.read()


def fill_pdf(template_bytes, fields):
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()

    for p in reader.pages:
        writer.add_page(p)

    root = reader.trailer["/Root"]
    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): True})

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)

    return writer


def draw_overlay(page_w, page_h, points):
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=(page_w, page_h))

    # pontos
    for p in points:
        x, y = cg_wt_to_xy(p["cg"], p["wt"])
        r, g, b = p["rgb"]
        c.setFillColorRGB(r, g, b)
        c.circle(x, y, 4, fill=1)

    # legenda
    lx, ly = 460, 360
    c.setFont("Helvetica-Bold", 9)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(lx, ly, "Legend")
    ly -= 14

    c.setFont("Helvetica", 9)
    for p in points:
        r, g, b = p["rgb"]
        c.setFillColorRGB(r, g, b)
        c.rect(lx, ly - 6, 10, 10, fill=1)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(lx + 14, ly - 4, p["label"])
        ly -= 14

    c.showPage()
    c.save()
    bio.seek(0)
    return bio.read()


# ============================================================
# STREAMLIT UI (MINIMAL)
# ============================================================

st.set_page_config(page_title="PA-28 PDF Tester", layout="centered")
st.title("PA-28 – M&B PDF Tester (Clean)")

st.caption("Preenche tabela + desenha CG chart (Empty / Takeoff / Landing)")

# --- valores de teste (hardcoded, realistas)
EMPTY_WT = 1650
EMPTY_CG = 85.0

TO_WT = 2550
TO_CG = 86.0

LDG_WT = 2400
LDG_CG = 85.7


# ============================================================
# PDF FIELDS (NOMES REAIS DO TEMPLATE)
# ============================================================

fields = {
    "Date": dt.datetime.now().strftime("%d/%m/%Y"),
    "Aircraft_Reg": "OE-KPD",

    # Weight table (labels já existem no PDF)
    "Weight(lbs)_1": f"{EMPTY_WT}",
    "Weight(lbs)_2": "340",
    "Weight(lbs)_3": "0",
    "Weight(lbs)_4": "288",
    "Weight(lbs)_5": "40",
    "Weight(lbs)_6": f"{TO_WT}",

    "Moment(in-lbs)_1": f"{int(EMPTY_WT * EMPTY_CG)}",
    "Moment(in-lbs)_2": f"{int(340 * 85.5)}",
    "Moment(in-lbs)_3": "0",
    "Moment(in-lbs)_4": f"{int(288 * 95.0)}",
    "Moment(in-lbs)_5": f"{int(40 * 142.8)}",
    "Moment(in-lbs)_6": f"{int(TO_WT * TO_CG)}",
}


# ============================================================
# GENERATE
# ============================================================

if st.button("Generate PDF", type="primary"):
    tpl = read_pdf()
    writer = fill_pdf(tpl, fields)

    reader = PdfReader(io.BytesIO(tpl))
    page = reader.pages[GRAPH_PAGE_INDEX]
    pw = float(page.mediabox.width)
    ph = float(page.mediabox.height)

    points = [
        {"label": "Empty", "cg": EMPTY_CG, "wt": EMPTY_WT, "rgb": (0.1, 0.6, 0.1)},
        {"label": "Takeoff", "cg": TO_CG, "wt": TO_WT, "rgb": (0.1, 0.3, 0.85)},
        {"label": "Landing", "cg": LDG_CG, "wt": LDG_WT, "rgb": (0.85, 0.2, 0.2)},
    ]

    overlay = draw_overlay(pw, ph, points)
    writer.pages[GRAPH_PAGE_INDEX].merge_page(
        PdfReader(io.BytesIO(overlay)).pages[0]
    )

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)

    st.download_button(
        "Download PDF",
        data=out.getvalue(),
        file_name="PA28_MB_Test_Clean.pdf",
        mime="application/pdf",
    )

    st.success("PDF gerado. Verifica gráfico e tabela.")


