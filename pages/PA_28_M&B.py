import io
import datetime as dt

import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas


# =========================
# CONFIG
# =========================
PDF_TEMPLATE = "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"
GRAPH_PAGE_INDEX = 0  # sempre 0, como pediste

# --- Coordenadas (do pdf-coordinates.com), bottom-left PDF standard
# Usamos:
#  - pontos (CG,1200)->x para base
#  - pontos (CG,2550)->x para topo
#  - pontos (weight)->y (usamos a linha CG=82 e topo 2550)
X_BOTTOM = {  # weight = 1200
    82: 182, 83: 199, 84: 213, 85: 229,
    89: 293, 90: 308, 91: 323, 92: 340, 93: 355,
}
X_TOP = {  # weight = 2550
    89: 315, 90: 345, 91: 374, 92: 404, 93: 435,
}

# y vs weight (pontos que forneceste e que fazem sentido)
# (usamos os pontos “progressivos” de 2050..2550, e o 1200)
Y_BY_WEIGHT = [
    (1200, 72),
    (2050, 245),
    (2200, 276),
    (2295, 294),
    (2355, 307),
    (2440, 322),
    (2515, 338),
    (2550, 343),
]


# =========================
# Small math helpers
# =========================
def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def lerp(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def interp_1d(x, pts):
    """piecewise-linear interpolation over pts=[(x, y), ...] sorted by x."""
    pts = sorted(pts, key=lambda p: p[0])
    x = clamp(x, pts[0][0], pts[-1][0])
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= x <= x1:
            return lerp(x, x0, x1, y0, y1)
    return pts[-1][1]

def interp_dict_at(d, x):
    """interpolate integer-key dict at float x"""
    keys = sorted(d.keys())
    x = clamp(x, keys[0], keys[-1])
    k0 = max(k for k in keys if k <= x)
    k1 = min(k for k in keys if k >= x)
    return lerp(x, k0, k1, d[k0], d[k1])

def cg_wt_to_xy(cg_in, wt_lb):
    """
    Mapeamento limpo:
      1) y = interp(weight->y)
      2) x_bottom(cg) e x_top(cg) por interpolação em CG
      3) fração vertical t = (y - y1200)/(y2550 - y1200)
      4) x = x_bottom + t*(x_top - x_bottom)
    """
    y = interp_1d(wt_lb, Y_BY_WEIGHT)

    # bottom x: temos 82..85 e 89..93 (há buraco 86..88); vamos interpolar dentro do alcance disponível
    xb = interp_dict_at(X_BOTTOM, cg_in)

    # top x: só temos 89..93; se cg < 89, clamp em 89 (é o que o gráfico faz também na zona de topo esquerdo)
    xt = interp_dict_at(X_TOP, cg_in)

    y1200 = interp_1d(1200, Y_BY_WEIGHT)
    y2550 = interp_1d(2550, Y_BY_WEIGHT)
    t = 0.0 if y2550 == y1200 else (y - y1200) / (y2550 - y1200)
    t = clamp(t, 0.0, 1.0)

    x = xb + t * (xt - xb)
    return float(x), float(y)


# =========================
# PDF helpers (igual filosofia Tecnam)
# =========================
def read_pdf_bytes():
    with open(PDF_TEMPLATE, "rb") as f:
        return f.read()

def fill_pdf(template_bytes: bytes, fields: dict) -> PdfWriter:
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

def overlay_points(page_w, page_h, points, legend_xy=(500, 320), r=4):
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=(page_w, page_h))

    # pontos
    for p in points:
        x, y = cg_wt_to_xy(p["cg"], p["wt"])
        rr, gg, bb = p["rgb"]
        c.setFillColorRGB(rr, gg, bb)
        c.circle(x, y, r, fill=1, stroke=0)

    # legenda (English)
    lx, ly = legend_xy
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(lx, ly, "Legend")
    ly -= 14

    c.setFont("Helvetica", 9)
    for p in points:
        rr, gg, bb = p["rgb"]
        c.setFillColorRGB(rr, gg, bb)
        c.rect(lx, ly - 7, 10, 10, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(lx + 14, ly - 5, p["label"])
        ly -= 14

    c.showPage()
    c.save()
    bio.seek(0)
    return bio.read()


# =========================
# Streamlit UI (clean)
# =========================
st.set_page_config(page_title="PA-28 PDF Tester (Clean v3)", layout="centered")
st.title("PA-28 – PDF Tester (Clean v3)")
st.caption("Preenche campos certos + desenha 3 pontos no CG chart (página 0).")

# valores genéricos só para teste visual
EMPTY_WT, EMPTY_CG = 1650, 85.0
FRONT_WT, FRONT_ARM = 340, 80.5
REAR_WT, REAR_ARM = 0, 118.1
FUEL_WT, FUEL_ARM = 288, 95.0
BAG_WT, BAG_ARM = 40, 142.8

# takeoff/landing para o gráfico (só exemplo)
TO_WT, TO_CG = 2550, 89.0
LDG_WT, LDG_CG = 2400, 87.0

def moment(w, arm):
    return int(round(w * arm))

# >>>>>> NOMES CERTOS DO PDF <<<<<<
# Linha 1 (Basic Empty Weight) usa campos SEM sufixo:
#   Weight(lbs) e Moment(In-Lbs)
# Linhas seguintes usam _1, _2, _3, _4, _5...
fields = {
    "Date": dt.datetime.now().strftime("%d/%m/%Y"),
    "Aircraft_Reg": "OE-KPD",

    # 1. Basic Empty Weight
    "Weight(lbs)": f"{EMPTY_WT}",
    "Moment(In-Lbs)": f"{moment(EMPTY_WT, EMPTY_CG)}",

    # 2. Pilot and front Passenger
    "Weight(lbs)_1": f"{FRONT_WT}",
    "Moment(In-Lbs)_1": f"{moment(FRONT_WT, FRONT_ARM)}",

    # 3. Passengers rear seats
    "Weight(lbs)_2": f"{REAR_WT}",
    "Moment(In-Lbs)_2": f"{moment(REAR_WT, REAR_ARM)}",

    # 4. Fuel
    "Weight(lbs)_3": f"{FUEL_WT}",
    "Moment(In-Lbs)_3": f"{moment(FUEL_WT, FUEL_ARM)}",

    # 5. Baggage
    "Weight(lbs)_4": f"{BAG_WT}",
    "Moment(In-Lbs)_4": f"{moment(BAG_WT, BAG_ARM)}",
}

if st.button("Generate PDF", type="primary"):
    template_bytes = read_pdf_bytes()
    reader = PdfReader(io.BytesIO(template_bytes))

    writer = fill_pdf(template_bytes, fields)

    p = reader.pages[GRAPH_PAGE_INDEX]
    pw = float(p.mediabox.width)
    ph = float(p.mediabox.height)

    points = [
        {"label": "Empty",   "cg": EMPTY_CG, "wt": EMPTY_WT, "rgb": (0.10, 0.60, 0.10)},
        {"label": "Takeoff", "cg": TO_CG,    "wt": TO_WT,    "rgb": (0.10, 0.30, 0.85)},
        {"label": "Landing", "cg": LDG_CG,   "wt": LDG_WT,   "rgb": (0.85, 0.20, 0.20)},
    ]

    ov = overlay_points(pw, ph, points, legend_xy=(500, 320), r=4)
    ov_page = PdfReader(io.BytesIO(ov)).pages[0]
    writer.pages[GRAPH_PAGE_INDEX].merge_page(ov_page)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)

    st.download_button(
        "Download PDF",
        data=out.getvalue(),
        file_name="PA28_test_clean_v3.pdf",
        mime="application/pdf",
    )
    st.success("PDF gerado (campos certos + gráfico mapeado por interpolação).")
