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
GRAPH_PAGE_INDEX = 0  # CG chart on page 0 (first page)


# ============================================================
# CG CHART COORDINATES (measured on pdf-coordinates.com)
# System: PDF standard bottom-left origin
# ============================================================

# Weight(lbs) -> y (pt)
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

# (CG integer, weight) -> x (pt)
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

# Debug crosses: ONLY the points you measured (fixed!)
DEBUG_POINTS = [
    # bottom line 1200 lbs
    (82, 1200, 182, 72),
    (83, 1200, 199, 72),
    (84, 1200, 213, 71),
    (85, 1200, 229, 73),
    (86, 1200, 229, 73),
    (87, 1200, 229, 73),
    (88, 1200, 229, 73),
    (89, 1200, 293, 73),
    (90, 1200, 308, 72),
    (91, 1200, 323, 72),
    (92, 1200, 340, 73),
    (93, 1200, 355, 72),

    # other measured points
    (82, 2050, 134, 245),
    (83, 2138, 155, 260),
    (84, 2200, 178, 276),
    (85, 2295, 202, 294),
    (86, 2355, 228, 307),
    (87, 2440, 255, 322),
    (88, 2515, 285, 338),

    # top line 2550 lbs
    (89, 2550, 315, 343),
    (90, 2550, 345, 343),
    (91, 2550, 374, 343),
    (92, 2550, 404, 343),
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
    Each CG integer is an inclined line.
    Define by:
      - point at 1200 lbs (must exist)
      - point at 2550 lbs (if missing, extrapolate using the highest intermediate point)
    """
    y0 = y_from_weight(1200)
    y1 = y_from_weight(2550)

    if (cg_int, 1200) not in X_AT:
        raise KeyError(f"Missing base point for CG {cg_int} at 1200")

    x0 = float(X_AT[(cg_int, 1200)])
    p0 = (x0, y0)

    if (cg_int, 2550) in X_AT:
        x1 = float(X_AT[(cg_int, 2550)])
        return p0, (x1, y1)

    candidates = [w for (cg, w) in X_AT.keys() if cg == cg_int and w != 1200]
    w_mid = max(candidates)
    x_mid = float(X_AT[(cg_int, w_mid)])
    y_mid = y_from_weight(w_mid)

    slope_dx_dy = 0.0 if y_mid == y0 else (x_mid - x0) / (y_mid - y0)
    x1 = x0 + slope_dx_dy * (y1 - y0)
    return p0, (x1, y1)


CG_LINES = {cg: build_cg_line(cg) for cg in range(82, 94)}


def x_on_cg_line(cg_int: int, y: float) -> float:
    (x0, y0), (x1, y1) = CG_LINES[cg_int]
    if y1 == y0:
        return x0
    t = (y - y0) / (y1 - y0)
    return x0 + t * (x1 - x0)


def cg_wt_to_xy(cg_in: float, wt_lb: float):
    """
    Correct chart mapping:
      - y from weight
      - x from intersection with inclined CG line
      - decimal CG interpolates between adjacent CG lines at same y
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
# PDF helpers (Tecnam-style)
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
# Overlay (points + legend + optional debug crosses)
# ============================================================
def draw_cross(c, x, y, size=3):
    c.setLineWidth(1)
    c.line(x - size, y, x + size, y)
    c.line(x, y - size, x, y + size)


def make_overlay_pdf(page_w, page_h, points, legend_xy=(500, 320), marker_r=4, draw_debug=False):
    bio = io.BytesIO()
    c = canvas.Canvas(bio, pagesize=(page_w, page_h))

    # points
    for p in points:
        x, y = cg_wt_to_xy(p["cg"], p["wt"])
        rr, gg, bb = p["rgb"]
        c.setFillColorRGB(rr, gg, bb)
        c.circle(x, y, marker_r, fill=1, stroke=0)

    # debug crosses at measured points
    if draw_debug:
        c.setStrokeColorRGB(0.85, 0.0, 0.85)  # magenta
        for _cg, _wt, x, y in DEBUG_POINTS:
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
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 8)
        c.drawString(lx, ly - 2, "Debug crosses = measured points")

    c.showPage()
    c.save()
    bio.seek(0)
    return bio.read()


# ============================================================
# Streamlit UI
# ============================================================
st.set_page_config(page_title="PA-28 PDF Tester", layout="centered")
st.title("PA-28 – PDF Tester (fixed field names + debug + page 2 fill)")
st.caption("Page 0: fills LOADING DATA + draws CG chart. Page 1: fills the whole second page with generic values.")

colA, colB = st.columns(2)
with colA:
    reg = st.text_input("Aircraft_Reg", value="OE-KPD")
with colB:
    date_str = st.text_input("Date (dd/mm/yyyy)", value=dt.datetime.now().strftime("%d/%m/%Y"))

draw_debug = st.checkbox("Draw debug crosses", value=True)

st.subheader("CG chart points")
c1, c2, c3 = st.columns(3)
with c1:
    empty_wt = st.number_input("Empty weight (lbs)", value=1650, step=10)
    empty_cg = st.number_input("Empty CG (in)", value=85.0, step=0.1, format="%.1f")
with c2:
    to_wt = st.number_input("Takeoff weight (lbs)", value=2550, step=10)
    to_cg = st.number_input("Takeoff CG (in)", value=89.0, step=0.1, format="%.1f")
with c3:
    ldg_wt = st.number_input("Landing weight (lbs)", value=2400, step=10)
    ldg_cg = st.number_input("Landing CG (in)", value=87.0, step=0.1, format="%.1f")


def moment(w, arm):
    return int(round(float(w) * float(arm)))


st.subheader("Page 0 LOADING DATA (test values)")
t1, t2, t3, t4, t5 = st.columns(5)
with t1:
    w_empty = st.number_input("Weight_EMPTY", value=1650, step=10)
    d_empty = st.number_input("Datum_EMPTY", value=85.0, step=0.1, format="%.1f")
with t2:
    w_front = st.number_input("Weight_FRONT", value=340, step=10)
    arm_front = st.number_input("Front arm (for Moment_FRONT)", value=80.5, step=0.1, format="%.1f")
with t3:
    w_rear = st.number_input("Weight_REAR", value=0, step=10)
    arm_rear = st.number_input("Rear arm (for Moment_REAR)", value=118.1, step=0.1, format="%.1f")
with t4:
    w_fuel = st.number_input("Weight_FUEL", value=288, step=10)
    arm_fuel = st.number_input("Fuel arm (for Moment_FUEL)", value=95.0, step=0.1, format="%.1f")
with t5:
    w_bag = st.number_input("Weight_BAGGAGE", value=40, step=10)
    arm_bag = st.number_input("Baggage arm (for Moment_BAGGAGE)", value=142.8, step=0.1, format="%.1f")

st.subheader("Page 0 totals")
u1, u2, u3, u4 = st.columns(4)
with u1:
    w_ramp = st.number_input("Weight_RAMP", value=2558, step=10)
    d_ramp = st.number_input("Datum_RAMP", value=89.0, step=0.1, format="%.1f")
with u2:
    w_to = st.number_input("Weight_TAKEOFF", value=2550, step=10)
    d_to = st.number_input("Datum_TAKEOFF", value=89.0, step=0.1, format="%.1f")
with u3:
    mtow = st.number_input("MTOW", value=2550, step=10)
with u4:
    mlw = st.number_input("MLW", value=2440, step=10)

m_empty = moment(w_empty, d_empty)
m_front = moment(w_front, arm_front)
m_rear = moment(w_rear, arm_rear)
m_fuel = moment(w_fuel, arm_fuel)
m_bag = moment(w_bag, arm_bag)
m_ramp = moment(w_ramp, d_ramp)
m_to = moment(w_to, d_to)

st.subheader("Computed (x,y) in PDF points (bottom-left)")
xE, yE = cg_wt_to_xy(empty_cg, empty_wt)
xT, yT = cg_wt_to_xy(to_cg, to_wt)
xL, yL = cg_wt_to_xy(ldg_cg, ldg_wt)
st.code(
    "\n".join([
        f"Empty   CG={empty_cg:.1f}, W={empty_wt:.0f} -> x={xE:.1f}, y={yE:.1f}",
        f"Takeoff CG={to_cg:.1f}, W={to_wt:.0f} -> x={xT:.1f}, y={yT:.1f}",
        f"Landing CG={ldg_cg:.1f}, W={ldg_wt:.0f} -> x={xL:.1f}, y={yL:.1f}",
    ])
)

# ============================================================
# FIELD NAMES (exact, no fallbacks)
# ============================================================
def build_fields():
    fields = {
        # Page 1 (index 1) header
        "Date": date_str,
        "Aircraft_Reg": reg,

        # Page 0 loading data
        "Weight_EMPTY": f"{int(w_empty)}",
        "Datum_EMPTY": f"{float(d_empty):.1f}",
        "Moment_EMPTY": f"{int(m_empty)}",

        "Weight_FRONT": f"{int(w_front)}",
        "Moment_FRONT": f"{int(m_front)}",

        "Weight_REAR": f"{int(w_rear)}",
        "Moment_REAR": f"{int(m_rear)}",

        "Weight_FUEL": f"{int(w_fuel)}",
        "Moment_FUEL": f"{int(m_fuel)}",

        "Weight_BAGGAGE": f"{int(w_bag)}",
        "Moment_BAGGAGE": f"{int(m_bag)}",

        "Weight_RAMP": f"{int(w_ramp)}",
        "Datum_RAMP": f"{float(d_ramp):.1f}",
        "Moment_RAMP": f"{int(m_ramp)}",

        "Weight_TAKEOFF": f"{int(w_to)}",
        "Datum_TAKEOFF": f"{float(d_to):.1f}",
        "Moment_TAKEOFF": f"{int(m_to)}",

        "MTOW": f"{int(mtow)}",
        "MLW": f"{int(mlw)}",
    }

    # ---- Fill ALL Page 1 (index 1) fields with generic values so you can verify mapping ----
    # Airfields
    fields.update({
        "Airfield_DEPARTURE": "LPCS",
        "Airfield_ARRIVAL": "LPPT",
        "Airfield_ALTERNATE_1": "LPMT",
        "Airfield_ALTERNATE_2": "LPSO",
    })

    # RWY / Elev / QNH / Temp / Wind
    for suf in ["DEPARTURE", "ARRIVAL", "ALTERNATE_1", "ALTERNATE_2"]:
        fields[f"RWY_QFU_{suf}"] = "170"
        fields[f"Elevation_{suf}"] = "300"
        fields[f"QNH_{suf}"] = "1015"
        fields[f"Temperature_{suf}"] = "15"
        fields[f"Wind_{suf}"] = "240/08"
        # Pressure Alt names (one has a space)
        if suf == "DEPARTURE":
            fields["Pressure_Alt _DEPARTURE"] = "500"
        else:
            fields[f"Pressure_Alt_{suf}"] = "500"
        fields[f"Density_Alt_{suf}"] = "1200"

        # Performance
        fields[f"TODA_{suf}"] = "1500"
        fields[f"TODR_{suf}"] = "600"
        fields[f"LDA_{suf}"] = "1400"
        fields[f"LDR_{suf}"] = "650"
        fields[f"ROC_{suf}"] = "700"

    # Fuel planning (Time/Fuel) generic
    fields.update({
        "Start-up_and_Taxi_TIME": "15min", "Start-up_and_Taxi_FUEL": "5 L",
        "CLIMB_TIME": "10min", "CLIMB_FUEL": "3 L",
        "ENROUTE_TIME": "1h00min", "ENROUTE_FUEL": "20 L",
        "DESCENT_TIME": "10min", "DESCENT_FUEL": "3 L",
        "TRIP_TIME": "1h20min", "TRIP_FUEL": "26 L",
        "Contingency_TIME": "4min", "Contingency_FUEL": "1 L",
        "ALTERNATE_TIME": "45min", "ALTERNATE_FUEL": "15 L",
        "RESERVE_TIME": "45min", "RESERVE_FUEL": "15 L",
        "REQUIRED_TIME": "3h09min", "REQUIRED_FUEL": "72 L",
        "EXTRA_TIME": "0min", "EXTRA_FUEL": "0 L",
        "Total_TIME": "3h09min", "Total_FUEL": "72 L",
    })

    return fields


if st.button("Generate PDF", type="primary"):
    template_bytes = read_pdf_bytes()
    reader = PdfReader(io.BytesIO(template_bytes))

    fields = build_fields()
    writer = fill_pdf(template_bytes, fields)

    # overlay on page 0
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
        file_name="PA28_tester_fixed_debug_and_page2.pdf",
        mime="application/pdf",
    )
    st.success("Done. Page 0: table + debug crosses + 3 CG points. Page 1: filled with generic values.")

