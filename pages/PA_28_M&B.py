import io
import datetime as dt

import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.pdfgen import canvas


# ============================================================
# CONFIG
# ============================================================
PDF_TEMPLATE = "RVP.CFI.067.02PiperPA28MBandPerformanceSheet.pdf"
GRAPH_PAGE_INDEX = 0  # sempre 0 (primeira página)

# -----------------------------
# Coordenadas medidas em https://www.pdf-coordinates.com/
# Sistema: PDF standard bottom-left (0,0)
# -----------------------------

# y vs weight (lbs -> y)
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

# (cg integer, weight) -> x  (y vem de Y_BY_WEIGHT)
X_AT = {
    (82, 1200): 182, (82, 2050): 134,
    (83, 1200): 199, (83, 2138): 155,
    (84, 1200): 213, (84, 2200): 178,
    (85, 1200): 229, (85, 2295): 202,
    (86, 1200): 229, (86, 2355): 228,
    (87, 1200): 229, (87, 2440): 255,
    (88, 1200): 229, (88, 2515): 285,
    (89, 1200): 293, (89, 2550): 315,
    (90, 1200): 308, (90, 2550): 345,
    (91, 1200): 323, (91, 2550): 374,
    (92, 1200): 340, (92, 2550): 404,
    (93, 1200): 355, (93, 2550): 435,
}

# lista para debug: pontos (cg, weight, x, y) como tu enviaste (para desenhar cruzes)
DEBUG_POINTS = [
    (82, 1200, 182, 72),
    (82, 2050, 134, 245),
    (83, 1200, 199, 72),
    (83, 2138, 155, 260),
    (84, 1200, 213, 71),
    (84, 2200, 178, 276),
    (85, 1200, 229, 73),
    (85, 2295, 202, 294),
    (86, 2355, 228, 307),
    (87, 2440, 255, 322),
    (88, 2515, 285, 338),
    (89, 1200, 293, 73),
    (89, 2550, 315, 343),
    (90, 1200, 308, 72),
    (90, 2550, 345, 343),
    (91, 1200, 323, 72),
    (91, 2550, 374, 343),
    (92, 1200, 340, 73),
    (92, 2550, 404, 343),
    (93, 1200, 355, 72),
    (93, 2550, 435, 344),
]


# ============================================================
# Small math helpers
# ============================================================
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


def y_from_weight(w):
    return float(interp_1d(float(w), Y_BY_WEIGHT))


def build_cg_line(cg_int: int):
    """
    Define a reta do CG=cg_int com dois pontos:
      - base em weight=1200
      - topo em weight=2550
    Se não existir ponto medido no topo, extrapola com o ponto intermédio mais alto que tens.
    """
    y0 = y_from_weight(1200)
    y1 = y_from_weight(2550)

    if (cg_int, 1200) not in X_AT:
        raise KeyError(f"Missing base point for CG {cg_int} at 1200 lbs")

    x0 = float(X_AT[(cg_int, 1200)])
    p0 = (x0, y0)

    # topo medido?
    if (cg_int, 2550) in X_AT:
        x1 = float(X_AT[(cg_int, 2550)])
        return p0, (x1, y1)

    # procurar um ponto intermédio para extrapolar (82..88 têm um cada)
    candidates = [w for (cg, w) in X_AT.keys() if cg == cg_int and w != 1200]
    if not candidates:
        # sem dados: devolve vertical (fallback defensivo)
        return p0, p0

    w_mid = max(candidates)
    x_mid = float(X_AT[(cg_int, w_mid)])
    y_mid = y_from_weight(w_mid)

    if y_mid == y0:
        x1 = x_mid
    else:
        slope_dx_dy = (x_mid - x0) / (y_mid - y0)
        x1 = x0 + slope_dx_dy * (y1 - y0)

    return p0, (x1, y1)


# pré-calcular retas CG 82..93
CG_LINES = {cg: build_cg_line(cg) for cg in range(82, 94)}


def x_on_cg_line(cg_int: int, y: float) -> float:
    (x0, y0), (x1, y1) = CG_LINES[cg_int]
    if y1 == y0:
        return x0
    t = (y - y0) / (y1 - y0)
    return x0 + t * (x1 - x0)


def cg_wt_to_xy(cg_in: float, wt_lb: float):
    """
    ✅ Mapeamento correto do gráfico:
      - cada CG inteiro é uma reta inclinada (x depende de y)
      - CG decimal interpola entre as duas retas (mesmo y)
      - y vem da escala weight->y medida
    """
    y = y_from_weight(wt_lb)

    cg_in = clamp(float(cg_in), 82.0, 93.0)
    c0 = int(cg_in // 1)
    c1 = min(93, c0 + 1)
    if c0 < 82:
        c0, c1 = 82, 83

    x0 = x_on_cg_line(c0, y)
    x1 = x_on_cg_line(c1, y)
    x = lerp(cg_in, c0, c1, x0, x1) if c1 != c0 else x0
    return float(x), float(y)


# ============================================================
# PDF fill helpers (estilo Tecnam)
# ============================================================
def read_pdf_bytes() -> bytes:
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


# ============================================================
# Overlay drawing
# ============================================================
def draw_cross(c, x, y, size=4):
    c.setLineWidth(1)
    c.line(x - size, y, x + size, y)
    c.line(x, y - size, x, y + size)


def make_overlay_pdf(page_w, page_h, points, legend_xy=(500, 320), marker_r=4, draw_debug=False):
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=(page_w, page_h))

    # main points
    for p in points:
        x, y = cg_wt_to_xy(p["cg"], p["wt"])
        rr, gg, bb = p["rgb"]
        c.setFillColorRGB(rr, gg, bb)
        c.circle(x, y, marker_r, fill=1, stroke=0)

    # optional debug: draw crosses at your measured calibration points
    if draw_debug:
        c.setStrokeColorRGB(0.85, 0.0, 0.85)  # magenta
        for cg, wt, x, y in DEBUG_POINTS:
            draw_cross(c, float(x), float(y), size=3)

    # legend
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

    if draw_debug:
        c.setFillColorRGB(0.0, 0.0, 0.0)
        c.setFont("Helvetica", 8)
        c.drawString(lx, ly - 2, "Debug crosses = measured points")

    c.showPage()
    c.save()
    bio.seek(0)
    return bio.read()


# ============================================================
# Streamlit UI (clean)
# ============================================================
st.set_page_config(page_title="PA-28 PDF Tester (full)", layout="centered")
st.title("PA-28 – PDF Tester (full)")
st.caption("Preenche a tabela + desenha CG chart com linhas inclinadas (uma reta por CG).")

# Simple inputs (para testar rapidamente)
col1, col2 = st.columns(2)
with col1:
    reg = st.text_input("Aircraft Reg", value="OE-KPD")
    date_str = st.text_input("Date", value=dt.datetime.now().strftime("%d/%m/%Y"))
with col2:
    draw_debug = st.checkbox("Draw debug crosses (measured points)", value=True)
    st.caption("Se estiver ON, desenha cruzes magenta nos pontos que mediste (deve bater exatamente).")

st.subheader("Test points (same idea as before)")
cA, cB, cC = st.columns(3)
with cA:
    empty_wt = st.number_input("Empty weight (lbs)", value=1650, step=10)
    empty_cg = st.number_input("Empty CG (in)", value=85.0, step=0.1, format="%.1f")
with cB:
    to_wt = st.number_input("Takeoff weight (lbs)", value=2550, step=10)
    to_cg = st.number_input("Takeoff CG (in)", value=89.0, step=0.1, format="%.1f")
with cC:
    ldg_wt = st.number_input("Landing weight (lbs)", value=2400, step=10)
    ldg_cg = st.number_input("Landing CG (in)", value=87.0, step=0.1, format="%.1f")

# Table test values (generic)
st.subheader("Table fill (generic test values)")
t1, t2, t3, t4, t5 = st.columns(5)
with t1:
    basic_empty_wt = st.number_input("1) Basic Empty Wt", value=1650, step=10)
    basic_empty_arm = st.number_input("1) CG/Arm (in)", value=85.0, step=0.1, format="%.1f")
with t2:
    front_wt = st.number_input("2) Front seats Wt", value=340, step=10)
    front_arm = st.number_input("2) Arm (in)", value=80.5, step=0.1, format="%.1f")
with t3:
    rear_wt = st.number_input("3) Rear seats Wt", value=0, step=10)
    rear_arm = st.number_input("3) Arm (in)", value=118.1, step=0.1, format="%.1f")
with t4:
    fuel_wt = st.number_input("4) Fuel Wt (lbs)", value=288, step=10)
    fuel_arm = st.number_input("4) Arm (in)", value=95.0, step=0.1, format="%.1f")
with t5:
    bag_wt = st.number_input("5) Baggage Wt", value=40, step=10)
    bag_arm = st.number_input("5) Arm (in)", value=142.8, step=0.1, format="%.1f")


def moment(w, arm):
    return int(round(float(w) * float(arm)))


# IMPORTANT:
# No teu template:
#  - linha 1 usa "Weight(lbs)" e "Moment(In-Lbs)" (sem sufixo)
#  - linhas seguintes usam _1, _2, _3, _4...
fields = {
    "Date": date_str,
    "Aircraft_Reg": reg,

    # 1. Basic Empty Weight
    "Weight(lbs)": f"{int(basic_empty_wt)}",
    "Moment(In-Lbs)": f"{moment(basic_empty_wt, basic_empty_arm)}",

    # 2. Pilot and front Passenger
    "Weight(lbs)_1": f"{int(front_wt)}",
    "Moment(In-Lbs)_1": f"{moment(front_wt, front_arm)}",

    # 3. Passengers (rear seats)
    "Weight(lbs)_2": f"{int(rear_wt)}",
    "Moment(In-Lbs)_2": f"{moment(rear_wt, rear_arm)}",

    # 4. Fuel
    "Weight(lbs)_3": f"{int(fuel_wt)}",
    "Moment(In-Lbs)_3": f"{moment(fuel_wt, fuel_arm)}",

    # 5. Baggage
    "Weight(lbs)_4": f"{int(bag_wt)}",
    "Moment(In-Lbs)_4": f"{moment(bag_wt, bag_arm)}",
}

# Show computed coordinates in UI (so you can sanity-check)
st.subheader("Computed PDF coordinates (bottom-left)")
xE, yE = cg_wt_to_xy(empty_cg, empty_wt)
xT, yT = cg_wt_to_xy(to_cg, to_wt)
xL, yL = cg_wt_to_xy(ldg_cg, ldg_wt)
st.code(
    "\n".join([
        f"Empty   CG={empty_cg:.1f}, W={empty_wt:.0f} -> x={xE:.1f}, y={yE:.1f}",
        f"Takeoff CG={to_cg:.1f}, W={to_wt:.0f} -> x={xT:.1f}, y={yT:.1f}",
        f"Landing CG={ldg_cg:.1f}, W={ldg_wt:.0f} -> x={xL:.1f}, y={yL:.1f}",
    ]),
    language="text"
)

if st.button("Generate PDF", type="primary"):
    template_bytes = read_pdf_bytes()
    reader = PdfReader(io.BytesIO(template_bytes))

    writer = fill_pdf(template_bytes, fields)

    gp = reader.pages[GRAPH_PAGE_INDEX]
    page_w = float(gp.mediabox.width)
    page_h = float(gp.mediabox.height)

    points = [
        {"label": "Empty",   "cg": float(empty_cg), "wt": float(empty_wt), "rgb": (0.10, 0.60, 0.10)},
        {"label": "Takeoff", "cg": float(to_cg),    "wt": float(to_wt),    "rgb": (0.10, 0.30, 0.85)},
        {"label": "Landing", "cg": float(ldg_cg),   "wt": float(ldg_wt),   "rgb": (0.85, 0.20, 0.20)},
    ]

    overlay_bytes = make_overlay_pdf(
        page_w, page_h,
        points,
        legend_xy=(500, 320),
        marker_r=4,
        draw_debug=draw_debug,
    )
    overlay_page = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
    writer.pages[GRAPH_PAGE_INDEX].merge_page(overlay_page)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)

    st.download_button(
        "Download PDF",
        data=out.getvalue(),
        file_name="PA28_test_full.pdf",
        mime="application/pdf",
    )
    st.success("Gerado. Se as cruzes magenta baterem nos pontos do gráfico, o mapeamento está certo.")

