# Streamlit app – Tecnam P2008 (M&B + Performance) – v8.3
# Changes vs v8.2 (principais):
# - Melhorado o bloco de "Fetch forecast for all legs":
#   • Sem st.rerun() (permitindo ver mensagens de sucesso/erro)
#   • Contagem de pernas atualizadas / com erro
#   • Mensagens claras por aeródromo
# - Pequeno ajuste em om_point_forecast para devolver params em caso de erro (útil para debug)
#
# Requirements:
#   streamlit
#   requests
#   pypdf>=4.2.0
#   pytz

import streamlit as st
import datetime as dt
from math import cos, sin, radians, sqrt, atan2, degrees
import json
import requests
import unicodedata
from pathlib import Path
import pytz
import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

# -----------------------------
# App setup & styles
# -----------------------------
st.set_page_config(
    page_title="Tecnam P2008 – Mass & Balance & Performance",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1200px !important; }
      .mb-header{font-size:1.35rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:8px;margin:2px 0 14px}
      .mb-table{border-collapse:collapse;width:100%;font-size:.95rem}
      .mb-table th{border-bottom:2px solid #cbd0d6;text-align:left}
      .mb-table td{padding:3px 6px;border-bottom:1px dashed #e5e7ec}
      .ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
      .cwok{color:#1d8533}.cwwarn{color:#d8aa22}.cwbad{color:#c21c1c}
      .mb-summary{display:flex;justify-content:space-between;margin:4px 0}
      .chip{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef2f7;margin-left:8px;font-size:.85rem}
      .chip-red{background:#fde8e8}.chip-yellow{background:#fff6db}.chip-green{background:#e8f7ec}
      .hint{font-size:.85rem;color:#6b7280}
    </style>
    """,
    unsafe_allow_html=True,
)


def ascii_safe(text):
    if not isinstance(text, str):
        return str(text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def fmt_hm(total_min: int) -> str:
    if total_min is None or total_min <= 0:
        return "0min"
    h, m = divmod(int(round(total_min)), 60)
    if h == 0:
        return f"{m}min"
    return f"{h}h" if m == 0 else f"{h}h{m:02d}min"


# -----------------------------
# Constants & Data
# -----------------------------
PDF_TEMPLATE_PATHS = [
    "TecnamP2008MBPerformanceSheet_MissionX.pdf",
]

AC = {
    "name": "Tecnam P2008 JC",
    "fuel_arm": 2.209,
    "pilot_arm": 1.800,
    "baggage_arm": 2.417,
    "max_takeoff_weight": 650.0,
    "max_fuel_volume": 120.0,      # L
    "max_passenger_weight": 230.0,
    "max_baggage_weight": 20.0,
    "cg_limits": (1.841, 1.978),   # m
    "fuel_density": 0.72,          # kg/L
}

# Aerodromes restricted to Approved Airfields list
# lat/lon approximate published coordinates
# runway headings are approximate QFU, TODA/LDA from approved lengths
AERODROMES_DB = {
    "LEBZ": {
        "name": "Badajoz",
        "lat": 38.8913, "lon": -6.8214, "elev_ft": 608.0,
        "runways": [
            {"id": "13", "qfu": 130.0, "toda": 2852.0, "lda": 2852.0, "slope_pc": 0.0, "paved": True},
            {"id": "31", "qfu": 310.0, "toda": 2852.0, "lda": 2852.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPBR": {
        "name": "Braga",
        "lat": 41.5872, "lon": -8.4451, "elev_ft": 243.0,
        "runways": [
            {"id": "18", "qfu": 180.0, "toda": 939.0, "lda": 939.0, "slope_pc": 0.0, "paved": True},
            {"id": "36", "qfu": 360.0, "toda": 939.0, "lda": 939.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPBG": {
        "name": "Bragança",
        "lat": 41.8578, "lon": -6.7074, "elev_ft": 2278.0,
        "runways": [
            {"id": "02", "qfu": 20.0, "toda": 1700.0, "lda": 1700.0, "slope_pc": 0.0, "paved": True},
            {"id": "20", "qfu": 200.0, "toda": 1700.0, "lda": 1700.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPCB": {
        "name": "Castelo Branco",
        "lat": 39.8483, "lon": -7.4417, "elev_ft": 1251.0,
        "runways": [
            {"id": "16", "qfu": 160.0, "toda": 1460.0, "lda": 1460.0, "slope_pc": 0.0, "paved": True},
            {"id": "34", "qfu": 340.0, "toda": 1460.0, "lda": 1460.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPCO": {
        "name": "Coimbra",
        "lat": 40.1582, "lon": -8.4705, "elev_ft": 570.0,
        "runways": [
            {"id": "16", "qfu": 160.0, "toda": 923.0, "lda": 923.0, "slope_pc": 0.0, "paved": True},
            {"id": "34", "qfu": 340.0, "toda": 923.0, "lda": 923.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPEV": {
        "name": "Évora",
        "lat": 38.5297, "lon": -7.8919, "elev_ft": 807.0,
        "runways": [
            {"id": "01", "qfu": 10.0, "toda": 1300.0, "lda": 1300.0, "slope_pc": 0.0, "paved": True},
            {"id": "19", "qfu": 190.0, "toda": 1300.0, "lda": 1300.0, "slope_pc": 0.0, "paved": True},
            {"id": "07", "qfu": 70.0, "toda": 1300.0, "lda": 1300.0, "slope_pc": 0.0, "paved": True},
            {"id": "25", "qfu": 250.0, "toda": 1300.0, "lda": 1300.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LEMG": {
        "name": "Málaga",
        "lat": 36.6749, "lon": -4.4991, "elev_ft": 52.0,
        "runways": [
            {"id": "12", "qfu": 120.0, "toda": 2750.0, "lda": 2750.0, "slope_pc": 0.0, "paved": True},
            {"id": "30", "qfu": 300.0, "toda": 2750.0, "lda": 2750.0, "slope_pc": 0.0, "paved": True},
            {"id": "13", "qfu": 130.0, "toda": 3200.0, "lda": 3200.0, "slope_pc": 0.0, "paved": True},
            {"id": "31", "qfu": 310.0, "toda": 3200.0, "lda": 3200.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPSO": {
        "name": "Ponte de Sôr",
        "lat": 39.2117, "lon": -8.0578, "elev_ft": 390.0,
        "runways": [
            {"id": "03", "qfu": 30.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True},
            {"id": "21", "qfu": 210.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LEZL": {
        "name": "Sevilha",
        "lat": 37.4180, "lon": -5.8931, "elev_ft": 111.0,
        "runways": [
            {"id": "09", "qfu": 90.0, "toda": 3364.0, "lda": 3364.0, "slope_pc": 0.0, "paved": True},
            {"id": "27", "qfu": 270.0, "toda": 3364.0, "lda": 3364.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LEVX": {
        "name": "Vigo",
        "lat": 42.2318, "lon": -8.6268, "elev_ft": 856.0,
        "runways": [
            {"id": "01", "qfu": 10.0, "toda": 2385.0, "lda": 2385.0, "slope_pc": 0.0, "paved": True},
            {"id": "19", "qfu": 190.0, "toda": 2385.0, "lda": 2385.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPVR": {
        "name": "Vila Real",
        "lat": 41.2743, "lon": -7.7205, "elev_ft": 1832.0,
        "runways": [
            {"id": "02", "qfu": 20.0, "toda": 946.0, "lda": 946.0, "slope_pc": 0.0, "paved": True},
            {"id": "20", "qfu": 200.0, "toda": 946.0, "lda": 946.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPVZ": {
        "name": "Viseu",
        "lat": 40.7255, "lon": -7.8890, "elev_ft": 2060.0,
        "runways": [
            {"id": "18", "qfu": 180.0, "toda": 1000.0, "lda": 1000.0, "slope_pc": 0.0, "paved": True},
            {"id": "36", "qfu": 360.0, "toda": 1000.0, "lda": 1000.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPCS": {
        "name": "Cascais",
        "lat": 38.7256, "lon": -9.3553, "elev_ft": 326.0,
        "runways": [
            # Approved list gives 1400 m
            {"id": "17", "qfu": 170.0, "toda": 1400.0, "lda": 1400.0, "slope_pc": 0.0, "paved": True},
            {"id": "35", "qfu": 350.0, "toda": 1400.0, "lda": 1400.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPMT": {
        "name": "Montijo",
        "lat": 38.7039, "lon": -9.0350, "elev_ft": 46.0,
        "runways": [
            {"id": "07", "qfu": 70.0, "toda": 2448.0, "lda": 2448.0, "slope_pc": 0.0, "paved": True},
            {"id": "25", "qfu": 250.0, "toda": 2448.0, "lda": 2448.0, "slope_pc": 0.0, "paved": True},
            {"id": "01", "qfu": 10.0, "toda": 2187.0, "lda": 2187.0, "slope_pc": 0.0, "paved": True},
            {"id": "19", "qfu": 190.0, "toda": 2187.0, "lda": 2187.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPST": {
        "name": "Sintra",
        "lat": 38.8311, "lon": -9.3397, "elev_ft": 441.0,
        "runways": [
            {"id": "17", "qfu": 170.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True},
            {"id": "35", "qfu": 350.0, "toda": 1800.0, "lda": 1800.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPBJ": {
        "name": "Beja",
        "lat": 38.0789, "lon": -7.9322, "elev_ft": 636.0,
        "runways": [
            {"id": "01L", "qfu": 10.0, "toda": 2448.0, "lda": 2448.0, "slope_pc": 0.0, "paved": True},
            {"id": "19R", "qfu": 190.0, "toda": 2448.0, "lda": 2448.0, "slope_pc": 0.0, "paved": True},
            {"id": "01R", "qfu": 10.0, "toda": 3449.0, "lda": 3449.0, "slope_pc": 0.0, "paved": True},
            {"id": "19L", "qfu": 190.0, "toda": 3449.0, "lda": 3449.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPFR": {
        "name": "Faro",
        "lat": 37.0144, "lon": -7.9658, "elev_ft": 24.0,
        "runways": [
            {"id": "10", "qfu": 100.0, "toda": 2490.0, "lda": 2490.0, "slope_pc": 0.0, "paved": True},
            {"id": "28", "qfu": 280.0, "toda": 2490.0, "lda": 2490.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPPM": {
        "name": "Portimão",
        "lat": 37.1493, "lon": -8.58397, "elev_ft": 5.0,
        "runways": [
            {"id": "11", "qfu": 110.0, "toda": 860.0, "lda": 860.0, "slope_pc": 0.0, "paved": True},
            {"id": "29", "qfu": 290.0, "toda": 860.0, "lda": 860.0, "slope_pc": 0.0, "paved": True},
        ],
    },
    "LPPR": {
        "name": "Porto",
        "lat": 41.2481, "lon": -8.6811, "elev_ft": 227.0,
        "runways": [
            {"id": "17", "qfu": 170.0, "toda": 3480.0, "lda": 3480.0, "slope_pc": 0.0, "paved": True},
            {"id": "35", "qfu": 350.0, "toda": 3480.0, "lda": 3480.0, "slope_pc": 0.0, "paved": True},
        ],
    },
}

# AFM tables
TAKEOFF = {
    0:     {"GR": {-25: 144, 0: 182, 25: 224, 50: 272, "ISA": 207}, "50ft": {-25: 304, 0: 379, 25: 463, 50: 557, "ISA": 428}},
    1000:  {"GR": {-25: 157, 0: 198, 25: 245, 50: 297, "ISA": 222}, "50ft": {-25: 330, 0: 412, 25: 503, 50: 605, "ISA": 458}},
    2000:  {"GR": {-25: 172, 0: 216, 25: 267, 50: 324, "ISA": 238}, "50ft": {-25: 359, 0: 448, 25: 547, 50: 658, "ISA": 490}},
    3000:  {"GR": {-25: 188, 0: 236, 25: 292, 50: 354, "ISA": 256}, "50ft": {-25: 391, 0: 487, 25: 595, 50: 717, "ISA": 525}},
    4000:  {"GR": {-25: 205, 0: 258, 25: 319, 50: 387, "ISA": 275}, "50ft": {-25: 425, 0: 530, 25: 648, 50: 780, "ISA": 562}},
    5000:  {"GR": {-25: 224, 0: 283, 25: 349, 50: 423, "ISA": 295}, "50ft": {-25: 463, 0: 578, 25: 706, 50: 850, "ISA": 603}},
    6000:  {"GR": {-25: 246, 0: 309, 25: 381, 50: 463, "ISA": 318}, "50ft": {-25: 505, 0: 630, 25: 770, 50: 927, "ISA": 646}},
    7000:  {"GR": {-25: 269, 0: 339, 25: 418, 50: 507, "ISA": 342}, "50ft": {-25: 551, 0: 687, 25: 840, 50: 1011, "ISA": 693}},
    8000:  {"GR": {-25: 295, 0: 371, 25: 458, 50: 555, "ISA": 368}, "50ft": {-25: 601, 0: 750, 25: 917, 50: 1104, "ISA": 744}},
    9000:  {"GR": {-25: 323, 0: 407, 25: 502, 50: 609, "ISA": 397}, "50ft": {-25: 657, 0: 819, 25: 1002, 50: 1205, "ISA": 800}},
    10000: {"GR": {-25: 354, 0: 446, 25: 551, 50: 668, "ISA": 428}, "50ft": {-25: 718, 0: 895, 25: 1095, 50: 1318, "ISA": 859}},
}
LANDING = {
    0:     {"GR": {-25: 149, 0: 164, 25: 179, 50: 194, "ISA": 173}, "50ft": {-25: 358, 0: 373, 25: 388, 50: 403, "ISA": 382}},
    1000:  {"GR": {-25: 154, 0: 170, 25: 186, 50: 201, "ISA": 178}, "50ft": {-25: 363, 0: 379, 25: 395, 50: 410, "ISA": 387}},
    2000:  {"GR": {-25: 160, 0: 176, 25: 192, 50: 209, "ISA": 183}, "50ft": {-25: 369, 0: 385, 25: 401, 50: 418, "ISA": 392}},
    3000:  {"GR": {-25: 166, 0: 183, 25: 200, 50: 216, "ISA": 189}, "50ft": {-25: 375, 0: 392, 25: 409, 50: 425, "ISA": 398}},
    4000:  {"GR": {-25: 172, 0: 190, 25: 207, 50: 225, "ISA": 195}, "50ft": {-25: 381, 0: 399, 25: 416, 50: 434, "ISA": 404}},
    5000:  {"GR": {-25: 179, 0: 197, 25: 215, 50: 233, "ISA": 201}, "50ft": {-25: 388, 0: 406, 25: 424, 50: 442, "ISA": 410}},
    6000:  {"GR": {-25: 186, 0: 205, 25: 223, 50: 242, "ISA": 207}, "50ft": {-25: 395, 0: 414, 25: 432, 50: 451, "ISA": 416}},
    7000:  {"GR": {-25: 193, 0: 212, 25: 232, 50: 251, "ISA": 213}, "50ft": {-25: 402, 0: 421, 25: 441, 50: 460, "ISA": 422}},
    8000:  {"GR": {-25: 200, 0: 221, 25: 241, 50: 261, "ISA": 220}, "50ft": {-25: 410, 0: 430, 25: 450, 50: 470, "ISA": 429}},
    9000:  {"GR": {-25: 208, 0: 229, 25: 250, 50: 271, "ISA": 227}, "50ft": {-25: 417, 0: 438, 25: 459, 50: 480, "ISA": 436}},
    10000: {"GR": {-25: 217, 0: 238, 25: 260, 50: 282, "ISA": 234}, "50ft": {-25: 426, 0: 447, 25: 469, 50: 491, "ISA": 443}},
}
ROC = {
    650: {
        0: { -25: 951, 0: 805, 25: 675, 50: 557, "ISA": 725 },
        2000: { -25: 840, 0: 696, 25: 568, 50: 453, "ISA": 638 },
        4000: { -25: 729, 0: 588, 25: 462, 50: 349, "ISA": 551 },
        6000: { -25: 619, 0: 480, 25: 357, 50: 245, "ISA": 464 },
        8000: { -25: 509, 0: 373, 25: 251, 50: 142, "ISA": 377 },
        10000: { -25: 399, 0: 266, 25: 146, 50: 39, "ISA": 290 },
        12000: { -25: 290, 0: 159, 25: 42, 50: -64, "ISA": 204 },
        14000: { -25: 181, 0: 53, 25: -63, 50: -166, "ISA": 117 },
    },
    600: {
        0: { -25: 1067, 0: 913, 25: 776, 50: 652, "ISA": 829 },
        2000: { -25: 950, 0: 799, 25: 664, 50: 542, "ISA": 737 },
        4000: { -25: 833, 0: 685, 25: 552, 50: 433, "ISA": 646 },
        6000: { -25: 717, 0: 571, 25: 441, 50: 324, "ISA": 555 },
        8000: { -25: 602, 0: 458, 25: 330, 50: 215, "ISA": 463 },
        10000: { -25: 486, 0: 345, 25: 220, 50: 106, "ISA": 372 },
        12000: { -25: 371, 0: 233, 25: 110, 50: -2, "ISA": 280 },
        14000: { -25: 257, 0: 121, 25: 0, 50: -109, "ISA": 189 },
    },
    550: {
        0: { -25: 1201, 0: 1038, 25: 892, 50: 760, "ISA": 948 },
        2000: { -25: 1077, 0: 916, 25: 773, 50: 644, "ISA": 851 },
        4000: { -25: 953, 0: 795, 25: 654, 50: 527, "ISA": 754 },
        6000: { -25: 830, 0: 675, 25: 536, 50: 411, "ISA": 657 },
        8000: { -25: 707, 0: 555, 25: 419, 50: 296, "ISA": 560 },
        10000: { -25: 584, 0: 435, 25: 301, 50: 181, "ISA": 462 },
        12000: { -25: 462, 0: 315, 25: 184, 50: 66, "ISA": 365 },
        14000: { -25: 341, 0: 196, 25: 68, 50: -48, "ISA": 268 },
    },
}
VY = {
    650: {0: 70, 2000: 69, 4000: 68, 6000: 67, 8000: 65, 10000: 64, 12000: 63, 14000: 62},
    600: {0: 70, 2000: 68, 4000: 67, 6000: 66, 8000: 65, 10000: 64, 12000: 63, 14000: 62},
    550: {0: 69, 2000: 68, 4000: 67, 6000: 66, 8000: 65, 10000: 64, 12000: 63, 14000: 61},
}

XW_GREEN_MAX = 8
XW_YELLOW_MAX = 15


# -----------------------------
# Helpers
# -----------------------------
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def interp1(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def bilinear(pa, temp, table, key):
    pas = sorted(table.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])

    temps = [-25, 0, 25, 50]
    t = clamp(temp, temps[0], temps[-1])

    if t <= 0:
        t0, t1 = -25, 0
    elif t <= 25:
        t0, t1 = 0, 25
    else:
        t0, t1 = 25, 50

    v00 = table[p0][key][t0]
    v01 = table[p0][key][t1]
    v10 = table[p1][key][t0]
    v11 = table[p1][key][t1]

    v0 = interp1(t, t0, t1, v00, v01)
    v1 = interp1(t, t0, t1, v10, v11)
    return interp1(pa_c, p0, p1, v0, v1)


def roc_interp(pa, temp, weight):
    w = clamp(weight, 550.0, 650.0)

    def roc_for_w(w_):
        tab = ROC[int(w_)]
        pas = sorted(tab.keys())
        pa_c = clamp(pa, pas[0], pas[-1])
        p0 = max([p for p in pas if p <= pa_c])
        p1 = min([p for p in pas if p >= pa_c])

        temps = [-25, 0, 25, 50]
        t = clamp(temp, temps[0], temps[-1])

        if t <= 0:
            t0, t1 = -25, 0
        elif t <= 25:
            t0, t1 = 0, 25
        else:
            t0, t1 = 25, 50

        v00 = tab[p0][t0]
        v01 = tab[p0][t1]
        v10 = tab[p1][t0]
        v11 = tab[p1][t1]

        v0 = interp1(t, t0, t1, v00, v01)
        v1 = interp1(t, t0, t1, v10, v11)
        return interp1(pa_c, p0, p1, v0, v1)

    if w <= 600:
        return interp1(w, 550, 600, roc_for_w(550), roc_for_w(600))
    return interp1(w, 600, 650, roc_for_w(600), roc_for_w(650))


def vy_interp(pa, weight):
    w_choice = 550 if weight <= 575 else (600 if weight <= 625 else 650)
    table = VY[w_choice]
    pas = sorted(table.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])
    return interp1(pa_c, p0, p1, table[p0], table[p1])


def wind_components(qfu_deg, wind_dir_deg, wind_speed):
    if qfu_deg is None or wind_dir_deg is None or wind_speed is None:
        return 0.0, 0.0, ""
    diff = ((wind_dir_deg - qfu_deg + 180) % 360) - 180
    hw = wind_speed * cos(radians(diff))
    cw = wind_speed * sin(radians(diff))
    hw = max(-abs(wind_speed), min(abs(wind_speed), hw))
    cw = max(-abs(wind_speed), min(abs(wind_speed), cw))
    side = "R" if cw > 0 else ("L" if cw < 0 else "")
    return hw, abs(cw), side


def xw_class(xw_abs):
    if xw_abs <= XW_GREEN_MAX:
        return "chip chip-green", "cwok"
    if xw_abs <= XW_YELLOW_MAX:
        return "chip chip-yellow", "cwwarn"
    return "chip chip-red", "cwbad"


def hw_chip_class(hw):
    if hw >= 30:
        return "chip chip-red"
    if hw >= 20:
        return "chip chip-yellow"
    return "chip chip-green"


def to_corrections_takeoff(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = float(ground_roll)

    # wind correction
    if headwind_kt >= 0:
        gr -= 5.0 * headwind_kt
    else:
        gr += 15.0 * abs(headwind_kt)

    # surface correction
    if paved:
        gr *= 0.90

    # slope correction (uphill positive)
    slope_pc = clamp(slope_pc, -5.0, 5.0)
    gr *= (1.0 + 0.07 * slope_pc)

    return max(gr, 0.0)


def ldg_corrections(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = float(ground_roll)

    # wind correction
    if headwind_kt >= 0:
        gr -= 4.0 * headwind_kt
    else:
        gr += 13.0 * abs(headwind_kt)

    # surface correction
    if paved:
        gr *= 0.90

    # slope correction (uphill positive)
    slope_pc = clamp(slope_pc, -5.0, 5.0)
    gr *= (1.0 - 0.03 * slope_pc)

    return max(gr, 0.0)


# -----------------------------
# Forecast provider (Open-Meteo) – usa pressure_msl (QNH)
# -----------------------------
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"


@st.cache_data(ttl=900, show_spinner=False)
def om_point_forecast(lat, lon, start_date_iso, end_date_iso):
    params = {
        "latitude": round(float(lat), 4),
        "longitude": round(float(lon), 4),
        "hourly": ",".join([
            "temperature_2m",
            "wind_speed_10m",
            "wind_direction_10m",
            "wind_gusts_10m",
            "pressure_msl",
        ]),
        "timezone": "UTC",
        "windspeed_unit": "kn",
        "temperature_unit": "celsius",
        "pressure_unit": "hPa",
        "start_date": start_date_iso,
        "end_date": end_date_iso,
    }
    try:
        r = requests.get(OPENMETEO_URL, params=params, timeout=20)
        if r.status_code != 200:
            return {
                "error": f"HTTP {r.status_code}",
                "detail": r.text,
                "params": params,
            }

        data = r.json()
        h = data.get("hourly", {})

        times = h.get("time", []) or []
        wspd_kn = h.get("wind_speed_10m", []) or []
        wdir = h.get("wind_direction_10m", []) or []
        gust_kn = h.get("wind_gusts_10m", []) or []
        temp_c = h.get("temperature_2m", []) or []
        qnh_hpa = h.get("pressure_msl", []) or []

        ts = []
        u_ms = []
        v_ms = []
        gust_ms = []
        temp = []
        press_pa = []

        for i, t in enumerate(times):
            dt_utc = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
            ts.append(int(dt_utc.timestamp() * 1000))

            spd_kn = wspd_kn[i] if i < len(wspd_kn) and wspd_kn[i] is not None else 0.0
            dir_deg = wdir[i] if i < len(wdir) and wdir[i] is not None else 0.0  # FROM
            theta = radians(dir_deg)
            spd_ms = spd_kn * 0.514444
            u_ms.append(-spd_ms * sin(theta))
            v_ms.append(-spd_ms * cos(theta))

            gust_val = gust_kn[i] if i < len(gust_kn) and gust_kn[i] is not None else 0.0
            gust_ms.append(gust_val * 0.514444)

            temp.append(temp_c[i] if i < len(temp_c) else None)

            qnh_val = qnh_hpa[i] if i < len(qnh_hpa) and qnh_hpa[i] is not None else None
            press_pa.append(qnh_val * 100.0 if qnh_val is not None else None)

        return {
            "ts": ts,
            "wind_u-surface": u_ms,
            "wind_v-surface": v_ms,
            "gust-surface": gust_ms,
            "temp-surface": temp,
            "pressure-surface": press_pa,
        }
    except Exception as e:
        return {"error": str(e), "params": params}


def om_list_hours(resp):
    if not resp or "ts" not in resp or not resp["ts"]:
        return []
    result = []
    for i, tms in enumerate(resp["ts"]):
        result.append((i, dt.datetime.utcfromtimestamp(tms / 1000.0).replace(tzinfo=dt.timezone.utc)))
    return result


def om_unpack_at(resp, idx):
    if idx is None:
        return None

    def getv(key):
        arr = resp.get(key, [])
        return arr[idx] if arr and idx < len(arr) else None

    u = getv("wind_u-surface")
    v = getv("wind_v-surface")
    gust = getv("gust-surface")

    if u is None or v is None:
        return None

    speed_ms = sqrt(u * u + v * v)
    dir_deg = (degrees(atan2(u, v)) + 180.0) % 360.0  # direção FROM
    speed_kt = speed_ms * 1.94384

    temp_val = getv("temp-surface")
    pres_pa = getv("pressure-surface")

    return {
        "wind_dir": int(round(dir_deg)),
        "wind_kt": int(round(speed_kt)),
        "wind_gust_kt": int(round(gust * 1.94384)) if gust is not None else None,
        "temp": int(round(float(temp_val))) if temp_val is not None else None,
        "qnh": int(round((pres_pa / 100.0))) if pres_pa is not None else None,
    }


# -----------------------------
# GitHub Gist persistence (fleet)
# -----------------------------
GIST_FILE = "fleet_p2008.json"


def gist_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def gist_load_fleet(token, gist_id):
    try:
        r = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            headers=gist_headers(token),
            timeout=15,
        )
        if r.status_code != 200:
            return None, f"GitHub error {r.status_code}: {r.text}"

        data = r.json()
        files = data.get("files", {})
        if GIST_FILE in files and files[GIST_FILE].get("content") is not None:
            content = files[GIST_FILE]["content"]
            return json.loads(content), None

        return None, "Gist file not found; will create on first save."
    except Exception as e:
        return None, str(e)


def gist_save_fleet(token, gist_id, fleet_dict):
    try:
        payload = {"files": {GIST_FILE: {"content": json.dumps(fleet_dict, indent=2, sort_keys=True)}}}
        r = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers=gist_headers(token),
            data=json.dumps(payload),
            timeout=15,
        )
        if r.status_code not in (200, 201):
            return f"GitHub error {r.status_code}: {r.text}"
        return None
    except Exception as e:
        return str(e)


# -----------------------------
# Session defaults
# -----------------------------
if "fleet" not in st.session_state:
    st.session_state.fleet = {
        "CS-DHS": {"ew": None, "ew_moment": None},
        "CS-DHU": {"ew": None, "ew_moment": None},
        "CS-DHW": {"ew": None, "ew_moment": None},
        "CS-DHT": {"ew": None, "ew_moment": None},
        "CS-ECC": {"ew": None, "ew_moment": None},
        "CS-ECD": {"ew": None, "ew_moment": None},
    }

if "fleet_loaded" not in st.session_state:
    st.session_state.fleet_loaded = False

if not st.session_state.fleet_loaded:
    token = st.secrets.get("GITHUB_GIST_TOKEN", "")
    gist_id = st.secrets.get("GITHUB_GIST_ID", "")
    if token and gist_id:
        gdata, _ = gist_load_fleet(token, gist_id)
        if gdata is not None:
            st.session_state.fleet = gdata
    st.session_state.fleet_loaded = True

DEFAULT_LEGS = [
    {"role": "Departure", "icao": "LPSO"},
    {"role": "Arrival",   "icao": "LPEV"},
    {"role": "Alternate", "icao": "LPCB"},
]

if "legs" not in st.session_state:
    st.session_state.legs = [dict(x) for x in DEFAULT_LEGS]

if "forecast" not in st.session_state:
    st.session_state.forecast = [None, None, None]
if "hours" not in st.session_state:
    st.session_state.hours = [[], [], []]
if "hour_idx" not in st.session_state:
    st.session_state.hour_idx = [None, None, None]
if "met" not in st.session_state:
    st.session_state.met = [
        {"temp": 15, "qnh": 1013, "wind_dir": 0, "wind_kt": 0}
        for _ in range(3)
    ]

if "forecast_target_utc" not in st.session_state:
    st.session_state.forecast_target_utc = (
        dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        + dt.timedelta(hours=1)
    )
if "mission_no" not in st.session_state:
    st.session_state.mission_no = ""
if "flight_date" not in st.session_state:
    st.session_state.flight_date = dt.datetime.now(
        pytz.timezone("Europe/Lisbon")
    ).date()
if "date_str" not in st.session_state:
    st.session_state.date_str = st.session_state.flight_date.strftime("%d/%m/%Y")


# -----------------------------
# Sidebar – Settings & Fleet
# -----------------------------
with st.sidebar:
    st.subheader("⚙️ Settings")
    st.caption("Previsão via Open-Meteo (sem chave).")
    st.markdown("---")

    st.subheader("🛩️ Fleet (EW & Moment)")
    with st.expander("Manage fleet (GitHub Gist)", expanded=False):
        token = st.secrets.get("GITHUB_GIST_TOKEN", "")
        gist_id = st.secrets.get("GITHUB_GIST_ID", "")

        cols = st.columns(3)
        with cols[0]:
            if st.button("Load from Gist (manual)"):
                if token and gist_id:
                    gdata, gerr = gist_load_fleet(token, gist_id)
                    if gdata is not None:
                        st.session_state.fleet = gdata
                        st.success(f"Loaded {len(gdata)} registrations.")
                    else:
                        st.warning(f"Could not load: {gerr}")
                else:
                    st.info("Add GITHUB_GIST_TOKEN and GITHUB_GIST_ID to secrets.")
        with cols[1]:
            if st.button("Save to Gist"):
                if token and gist_id:
                    err = gist_save_fleet(token, gist_id, st.session_state.fleet)
                    if err:
                        st.error(err)
                    else:
                        st.success("Fleet saved to Gist.")
                else:
                    st.info("Add secrets to enable persistence.")

        regs_all = list(st.session_state.fleet.keys())

        add_reg = st.text_input("Registration", value="")
        col_add1, col_add2 = st.columns(2)
        with col_add1:
            ew_new = st.number_input(
                "Empty Weight (kg)",
                min_value=0.0,
                value=0.0,
                step=0.1,
                key="fleet_ew_new",
            )
        with col_add2:
            em_new = st.number_input(
                "EW Moment (kg·m)",
                min_value=0.0,
                value=0.0,
                step=0.01,
                key="fleet_em_new",
            )

        if st.button("Add/Update"):
            if add_reg.strip():
                st.session_state.fleet[add_reg.strip().upper()] = {
                    "ew": ew_new,
                    "ew_moment": em_new,
                }
                st.success(
                    f"Saved {add_reg.strip().upper()} (local state). Use 'Save to Gist' to persist."
                )

        if regs_all:
            del_reg = st.selectbox("Remove registration", options=[""] + regs_all)
            if st.button("Remove"):
                if del_reg:
                    st.session_state.fleet.pop(del_reg, None)
                    st.success(f"Removed: {del_reg}")


# -----------------------------
# Tabs
# -----------------------------
st.markdown(
    '<div class="mb-header">Tecnam P2008 – Mass & Balance & Performance</div>',
    unsafe_allow_html=True,
)

tab_setup, tab_aero, tab_wb, tab_perf, tab_pdf = st.tabs([
    "1) Flight & Aircraft", "2) Aerodromes & MET", "3) Weight & Balance",
    "4) Performance & Fuel", "5) PDF",
])


# ---- 1) Flight & Aircraft ----
with tab_setup:
    c1 = st.columns([1])[0]
    with c1:
        st.markdown("### Aircraft & Flight")

        regs = list(st.session_state.fleet.keys()) or ["CS-XXX"]
        selected_reg = st.selectbox("Registration", regs, key="selected_reg")
        st.session_state["reg"] = selected_reg

        st.session_state.mission_no = st.text_input(
            "Mission number",
            value=st.session_state.mission_no,
        )

        flight_date = st.date_input(
            "Flight date (Europe/Lisbon)",
            value=st.session_state.flight_date,
        )
        st.session_state.flight_date = flight_date
        st.session_state["date_str"] = flight_date.strftime("%d/%m/%Y")


# ---- helper: choose best runway ----
def choose_best_runway(ad, temp_c, qnh, wind_dir, wind_kt, total_weight):
    pa_ft = ad["elev_ft"] + (1013.0 - qnh) * 30.0
    isa_temp = 15.0 - 2.0 * (ad["elev_ft"] / 1000.0)
    da_ft = pa_ft + (120.0 * (temp_c - isa_temp))

    candidates = []
    for rw in ad["runways"]:
        qfu = rw["qfu"]
        paved = rw["paved"]
        slope_pc = rw["slope_pc"]

        hw, xw_abs, side = wind_components(qfu, wind_dir, wind_kt)

        to_gr = bilinear(pa_ft, temp_c, TAKEOFF, "GR")
        to_50 = bilinear(pa_ft, temp_c, TAKEOFF, "50ft")
        ldg_gr = bilinear(pa_ft, temp_c, LANDING, "GR")
        ldg_50 = bilinear(pa_ft, temp_c, LANDING, "50ft")

        to_gr_corr = to_corrections_takeoff(
            to_gr,
            hw,
            paved=paved,
            slope_pc=slope_pc,
        )
        ldg_gr_corr = ldg_corrections(
            ldg_gr,
            hw,
            paved=paved,
            slope_pc=slope_pc,
        )

        feasible = (to_50 <= rw["toda"]) and (ldg_50 <= rw["lda"])
        pct_todr = (to_50 / rw["toda"] * 100) if rw["toda"] > 0 else 0.0
        pct_ldr = (ldg_50 / rw["lda"] * 100) if rw["lda"] > 0 else 0.0

        roc_val = roc_interp(pa_ft, temp_c, total_weight) if total_weight > 0 else 0.0
        vy_val = vy_interp(pa_ft, total_weight) if total_weight > 0 else 0.0

        candidates.append({
            "id": rw["id"],
            "qfu": qfu,
            "toda_av": rw["toda"],
            "lda_av": rw["lda"],
            "paved": paved,
            "slope_pc": slope_pc,
            "hw_comp": hw,
            "xw_abs": xw_abs,
            "xw_side": side,
            "to_gr": to_gr_corr,
            "to_50": to_50,
            "ldg_gr": ldg_gr_corr,
            "ldg_50": ldg_50,
            "feasible": feasible,
            "pa_ft": pa_ft,
            "da_ft": da_ft,
            "pct_todr": pct_todr,
            "pct_ldr": pct_ldr,
            "roc": roc_val,
            "vy": vy_val,
        })

    best = sorted(
        candidates,
        key=lambda c: (c["feasible"], c["hw_comp"], -c["xw_abs"]),
        reverse=True,
    )[0]
    return best, candidates


# ---- 2) Aerodromes & MET ----
with tab_aero:
    st.markdown("### Aerodromes (Departure, Arrival, Alternate) + MET (hourly)")

    col_h1, col_h2 = st.columns([0.55, 0.45])
    with col_h2:
        target_time = st.time_input(
            "Forecast – target hour (UTC)",
            value=st.session_state.forecast_target_utc.time().replace(
                second=0,
                microsecond=0,
            ),
            step=3600,
        )
        st.session_state.forecast_target_utc = dt.datetime.combine(
            st.session_state.flight_date,
            target_time,
        ).replace(tzinfo=dt.timezone.utc)

    # ---- Botão de fetch melhorado ----
    _, c_fetch2 = st.columns([0.6, 0.4])
    with c_fetch2:
        if st.button("Fetch forecast for all legs", type="primary"):
            ok_count = 0
            err_count = 0

            for idx, leg in enumerate(st.session_state.legs):
                icao = leg["icao"]
                ad = AERODROMES_DB[icao]

                start_iso = st.session_state.flight_date.strftime("%Y-%m-%d")
                end_iso = start_iso  # 1 dia chega para a maioria dos casos

                resp = om_point_forecast(ad["lat"], ad["lon"], start_iso, end_iso)
                if "error" in resp:
                    st.error(
                        f"{icao}: Forecast error: {resp.get('error')} {resp.get('detail','')}"
                    )
                    err_count += 1
                    continue

                hours = om_list_hours(resp)
                if not hours:
                    st.warning(f"{icao}: Forecast returned no hours.")
                    err_count += 1
                    continue

                st.session_state.forecast[idx] = resp
                st.session_state.hours[idx] = hours

                # escolher a hora mais próxima do target
                target = st.session_state.forecast_target_utc
                nearest_idx, nearest_time = min(
                    hours,
                    key=lambda h: abs(h[1] - target),
                )
                st.session_state.hour_idx[idx] = nearest_idx

                met = om_unpack_at(resp, nearest_idx)
                if met:
                    # atualizar MET em session_state
                    st.session_state.met[idx]["temp"] = int(met["temp"]) if met["temp"] is not None else st.session_state.met[idx]["temp"]
                    st.session_state.met[idx]["qnh"] = int(met["qnh"]) if met["qnh"] is not None else st.session_state.met[idx]["qnh"]
                    st.session_state.met[idx]["wind_dir"] = int(met["wind_dir"]) if met["wind_dir"] is not None else st.session_state.met[idx]["wind_dir"]
                    st.session_state.met[idx]["wind_kt"] = int(met["wind_kt"]) if met["wind_kt"] is not None else st.session_state.met[idx]["wind_kt"]

                    # refletir nos inputs numéricos
                    st.session_state[f"temp_{idx}"] = float(st.session_state.met[idx]["temp"])
                    st.session_state[f"qnh_{idx}"] = float(st.session_state.met[idx]["qnh"])
                    st.session_state[f"wdir_{idx}"] = float(st.session_state.met[idx]["wind_dir"])
                    st.session_state[f"wspd_{idx}"] = float(st.session_state.met[idx]["wind_kt"])

                    ok_count += 1
                else:
                    st.warning(f"{icao}: Could not unpack MET for selected hour.")
                    err_count += 1

            if ok_count and not err_count:
                st.success(f"Forecast updated for all legs ({ok_count}/3).")
            elif ok_count:
                st.warning(f"Forecast updated for {ok_count} leg(s), {err_count} with errors.")
            else:
                st.error("Could not update forecast for any leg.")

    perf_rows = []

    for i, leg in enumerate(st.session_state.legs):
        role = leg.get("role", ["Departure", "Arrival", "Alternate"][i])
        c1, c2 = st.columns([0.45, 0.55])

        with c1:
            icao_options = sorted(AERODROMES_DB.keys())
            default_icao = leg.get("icao", icao_options[0])

            icao = st.selectbox(
                f"{role} – Aerodrome (ICAO)",
                options=icao_options,
                index=icao_options.index(default_icao)
                if default_icao in icao_options
                else 0,
                key=f"icao_{i}",
            )

            ad = AERODROMES_DB[icao]

            # Reset TODA/LDA quando o aeródromo muda
            icao_state_key = f"last_icao_{i}"
            if st.session_state.get(icao_state_key) != icao:
                st.session_state[icao_state_key] = icao
                st.session_state[f"toda_{i}"] = float(ad["runways"][0]["toda"])
                st.session_state[f"lda_{i}"] = float(ad["runways"][0]["lda"])

            st.write(
                f"**{ad['name']}**  \n"
                f"Lat {ad['lat']:.5f}, Lon {ad['lon']:.5f}  \n"
                f"Elev {ad['elev_ft']:.0f} ft"
            )

            temp_c = int(st.number_input(
                "OAT (°C)",
                value=int(st.session_state.met[i]["temp"]),
                step=1,
                key=f"temp_{i}",
            ))
            qnh = int(st.number_input(
                "QNH (hPa)",
                min_value=900,
                max_value=1050,
                value=int(st.session_state.met[i]["qnh"]),
                step=1,
                key=f"qnh_{i}",
            ))
            wind_dir = int(st.number_input(
                "Wind FROM (°)",
                min_value=0,
                max_value=360,
                value=int(st.session_state.met[i]["wind_dir"]),
                step=1,
                key=f"wdir_{i}",
            ))
            wind_kt = int(st.number_input(
                "Wind speed (kt)",
                min_value=0,
                value=int(st.session_state.met[i]["wind_kt"]),
                step=1,
                key=f"wspd_{i}",
            ))

            paved = st.checkbox(
                "Paved runway",
                value=True,
                key=f"paved_{i}",
            )
            slope_pc = st.number_input(
                "Runway slope (%) (uphill positive)",
                value=0.0,
                step=0.1,
                key=f"slope_{i}",
            )

            toda_av = st.number_input(
                "TODA available (m)",
                min_value=0.0,
                value=st.session_state.get(f"toda_{i}", float(ad["runways"][0]["toda"])),
                step=1.0,
                key=f"toda_{i}",
            )
            lda_av = st.number_input(
                "LDA available (m)",
                min_value=0.0,
                value=st.session_state.get(f"lda_{i}", float(ad["runways"][0]["lda"])),
                step=1.0,
                key=f"lda_{i}",
            )

        with c2:
            hours = st.session_state.hours[i]
            idx = st.session_state.hour_idx[i]
            if hours and idx is not None:
                label = next(
                    (h[1].strftime("%Y-%m-%d %H:00Z") for h in hours if h[0] == idx),
                    None,
                )
                if label:
                    st.caption(f"Forecast hour applied: **{label}**")

        total_weight_for_perf = (
            st.session_state.get("_wb", {}).get("total_weight", 0.0) or 0.0
        )
        best, _ = choose_best_runway(
            ad,
            float(temp_c),
            float(qnh),
            float(wind_dir),
            float(wind_kt),
            total_weight_for_perf,
        )

        # Percentagens recalculadas com TODA/LDA "available"
        pct_todr_av = (best["to_50"] / toda_av * 100) if toda_av > 0 else 0.0
        pct_ldr_av = (best["ldg_50"] / lda_av * 100) if lda_av > 0 else 0.0

        # Feasibility com base em TODA/LDA disponíveis
        tod_ok_av = best["to_50"] <= toda_av
        ldg_ok_av = best["ldg_50"] <= lda_av
        feas = "✅" if tod_ok_av and ldg_ok_av else "⚠️"
        xw_chip_cls, _cwcol = xw_class(best["xw_abs"])

        st.markdown(
            f"🧭 **Selected runway:** {best['id']} "
            f"<span class='chip'>QFU {best['qfu']:.0f}°</span>"
            f"<span class='chip'>TODA {toda_av:.0f} m</span>"
            f"<span class='chip'>LDA {lda_av:.0f} m</span>"
            f"<span class='{hw_chip_class(best['hw_comp'])}'>HW {best['hw_comp']:.0f} kt</span>"
            f"<span class='{xw_chip_cls}'>XW {best['xw_side']} {best['xw_abs']:.0f} kt</span> "
            f"<span class='chip'>TO % {pct_todr_av:.0f}</span>"
            f"<span class='chip'>LD % {pct_ldr_av:.0f}</span> {feas}",
            unsafe_allow_html=True,
        )

        st.session_state.legs[i] = {"role": role, "icao": icao}

        perf_rows.append({
            "role": role,
            "icao": icao,
            "name": ad["name"],
            "lat": ad["lat"],
            "lon": ad["lon"],
            "elev_ft": ad["elev_ft"],
            "rwy": best["id"],
            "qfu": best["qfu"],
            "toda_av": float(toda_av if toda_av else best["toda_av"]),
            "lda_av": float(lda_av if lda_av else best["lda_av"]),
            "slope_pc": slope_pc,
            "paved": paved,
            "temp": int(temp_c),
            "qnh": int(qnh),
            "wind_dir": int(wind_dir),
            "wind_kt": int(wind_kt),
            "pa_ft": best["pa_ft"],
            "da_ft": best["da_ft"],
            "to_gr": best["to_gr"],
            "to_50": best["to_50"],
            "ldg_gr": best["ldg_gr"],
            "ldg_50": best["ldg_50"],
            "hw_comp": best["hw_comp"],
            "xw_abs": best["xw_abs"],
            "xw_side": best["xw_side"],
            "feasible": tod_ok_av and ldg_ok_av,
            "pct_todr": pct_todr_av,
            "pct_ldr": pct_ldr_av,
            "roc": best["roc"],
            "vy": best["vy"],
        })

    st.markdown("#### Performance summary (auto-selected runways)")

    def fmt(v):
        return f"{v:.0f}" if isinstance(v, (int, float)) else str(v)

    def status_cell(ok, margin, pct=None):
        cls = "ok" if ok else "bad"
        sign = "+" if margin >= 0 else "−"
        pct_str = f" • {pct:.0f}%" if (pct is not None and pct > 0) else ""
        return f"<span class='{cls}'>{'OK' if ok else 'NOK'} ({sign}{abs(margin):.0f} m){pct_str}</span>"

    rows_html = []
    for r in perf_rows:
        r["tod_ok"] = r["to_50"] <= r["toda_av"]
        r["ldg_ok"] = r["ldg_50"] <= r["lda_av"]
        r["tod_margin"] = r["toda_av"] - r["to_50"]
        r["ldg_margin"] = r["lda_av"] - r["ldg_50"]

        rows_html.append(
            "<tr>"
            f"<td>{r['role']} {r['icao']}</td>"
            f"<td>{fmt(r['qfu'])}</td>"
            f"<td>{fmt(r['pa_ft'])}/{fmt(r['da_ft'])}</td>"
            f"<td>{fmt(r['to_50'])}</td><td>{fmt(r['toda_av'])}</td>"
            f"<td>{status_cell(r['tod_ok'], r['tod_margin'], r['pct_todr'])}</td>"
            f"<td>{fmt(r['ldg_50'])}</td><td>{fmt(r['lda_av'])}</td>"
            f"<td>{status_cell(r['ldg_ok'], r['ldg_margin'], r['pct_ldr'])}</td>"
            f"<td>{('HW' if r['hw_comp']>=0 else 'TW')} {abs(r['hw_comp']):.0f} / {fmt(r['xw_abs'])} kt</td>"
            f"<td>{fmt(r.get('roc',0))}</td><td>{fmt(r.get('vy',0))}</td>"
            "</tr>"
        )

    st.markdown(
        "<table class='mb-table'><tr>"
        "<th>Leg/ICAO</th><th>QFU</th><th>PA/DA ft</th>"
        "<th>TODR 50ft</th><th>TODA</th><th>Takeoff fit</th>"
        "<th>LDR 50ft</th><th>LDA</th><th>Landing fit</th>"
        "<th>Wind (H/C)</th><th>ROC</th><th>Vy</th></tr>"
        + "".join(rows_html)
        + "</table>",
        unsafe_allow_html=True,
    )

    st.session_state["_perf_rows"] = perf_rows


# ---- 3) Weight & Balance ----
with tab_wb:
    st.markdown("### Weight & Balance")

    reg = st.session_state.get("reg", "CS-XXX")
    fleet = st.session_state.fleet

    ew_default = fleet.get(reg, {}).get("ew")
    ewm_default = fleet.get(reg, {}).get("ew_moment")

    c1, c2 = st.columns([0.55, 0.45])
    with c1:
        ew = st.number_input(
            "Empty Weight (kg)",
            min_value=0.0,
            value=(ew_default or 0.0),
            step=0.1,
            disabled=True,
        )
        ew_moment = st.number_input(
            "Empty Weight Moment (kg·m)",
            min_value=0.0,
            value=(ewm_default or 0.0),
            step=0.01,
            disabled=True,
        )
        student = st.number_input(
            "Student weight (kg)",
            min_value=0.0,
            value=50.0,
            step=0.5,
        )
        instructor = st.number_input(
            "Instructor weight (kg)",
            min_value=0.0,
            value=0.0,
            step=0.5,
        )
        baggage = st.number_input(
            "Baggage (kg)",
            min_value=0.0,
            value=0.0,
            step=0.5,
        )
        fuel_l = st.number_input(
            "Fuel (L)",
            min_value=0.0,
            value=0.0,
            step=0.5,
        )

    with c2:
        pilot = student + instructor
        fuel_wt = fuel_l * AC["fuel_density"]

        m_empty = ew_moment or 0.0
        m_pilot = pilot * AC["pilot_arm"]
        m_bag = baggage * AC["baggage_arm"]
        m_fuel = fuel_wt * AC["fuel_arm"]

        total_weight = (ew or 0.0) + pilot + baggage + fuel_wt
        total_moment = m_empty + m_pilot + m_bag + m_fuel
        cg = (total_moment / total_weight) if total_weight > 0 else 0.0

        rem_by_mtow = max(
            0.0,
            AC["max_takeoff_weight"] - ((ew or 0.0) + pilot + baggage + fuel_wt),
        )
        rem_by_tank = max(
            0.0,
            AC["max_fuel_volume"] * AC["fuel_density"] - fuel_wt,
        )
        rem_fuel_wt = min(rem_by_mtow, rem_by_tank)
        rem_fuel_l = rem_fuel_wt / AC["fuel_density"]
        limit_label = "Tanque" if rem_by_tank < rem_by_mtow else "MTOW"

        def w_color(val, limit):
            if val > limit:
                return "bad"
            if val > 0.95 * limit:
                return "warn"
            return "ok"

        def cg_color_val(cg_val, limits):
            lo, hi = limits
            margin = 0.05 * (hi - lo)
            if cg_val < lo or cg_val > hi:
                return "bad"
            if cg_val < lo + margin or cg_val > hi - margin:
                return "warn"
            return "ok"

        st.markdown("#### Summary")

        st.markdown(
            "<div class='mb-summary'><div>Fuel extra possível</div>"
            f"<div><b>{rem_fuel_l:.1f} L</b> "
            f"<span class='hint'>(limitado por <i>{limit_label}</i>)</span></div></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div class='mb-summary'><div>Total Weight</div>"
            f"<div class='{w_color(total_weight, AC['max_takeoff_weight'])}'>"
            f"<b>{total_weight:.1f} kg</b></div></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div class='mb-summary'><div>Total Moment</div>"
            f"<div><b>{total_moment:.2f} kg·m</b></div></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div class='mb-summary'><div>CG</div>"
            f"<div class='{cg_color_val(cg, AC['cg_limits'])}'>"
            f"<b>{cg:.3f} m</b>"
            f"<span class='chip'>{AC['cg_limits'][0]:.3f} – {AC['cg_limits'][1]:.3f} m</span>"
            "</div></div>",
            unsafe_allow_html=True,
        )

        if pilot > AC.get("max_passenger_weight", 1e9):
            st.error(
                f"Passengers over limit: {pilot:.1f} kg > {AC['max_passenger_weight']:.0f} kg"
            )
        if baggage > AC.get("max_baggage_weight", 1e9):
            st.error(
                f"Baggage over limit: {baggage:.1f} kg > {AC['max_baggage_weight']:.0f} kg"
            )
        if fuel_l > AC["max_fuel_volume"]:
            st.error(
                f"Fuel volume over limit: {fuel_l:.1f} L > {AC['max_fuel_volume']:.0f} L"
            )

        lo, hi = AC["cg_limits"]
        if total_weight > AC["max_takeoff_weight"]:
            st.error(
                f"MTOW exceeded: {total_weight:.1f} kg > {AC['max_takeoff_weight']:.0f} kg"
            )
        if total_weight > 0 and (cg < lo or cg > hi):
            st.error(
                f"CG out of limits: {cg:.3f} m not in [{lo:.3f}, {hi:.3f}] m"
            )

    st.session_state["_wb"] = {
        "ew": ew,
        "ew_moment": ew_moment,
        "total_weight": total_weight,
        "total_moment": total_moment,
        "cg": cg,
        "fuel_l": fuel_l,
        "pilot": pilot,
        "baggage": baggage,
    }


# ---- 4) Fuel planning — DETALHADO apenas ----
with tab_perf:
    st.markdown("### Fuel Planning — Detalhado (EASA-like)")

    # Consumo e fuel que vem do M&B
    RATE_LPH = st.number_input(
        "Débito (L/h)",
        min_value=10.0,
        max_value=40.0,
        value=20.0,
        step=0.5,
    )
    fuel_l_mb = st.session_state.get("_wb", {}).get("fuel_l", 0.0)

    # Entradas detalhadas
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        taxi_min = st.number_input(
            "(1) Start-up & Taxi (min)",
            min_value=0,
            value=15,
            step=1,
        )
        climb_min = st.number_input(
            "(2) Climb (min)",
            min_value=0,
            value=10,
            step=1,
        )
    with c2:
        enrt_h = st.number_input(
            "(3) Enroute (h)",
            min_value=0,
            value=1,
            step=1,
        )
        enrt_min = st.number_input(
            "(3) Enroute (min)",
            min_value=0,
            value=0,
            step=5,
        )
    with c3:
        desc_min = st.number_input(
            "(4) Descent (min)",
            min_value=0,
            value=10,
            step=1,
        )
        alt_min = st.number_input(
            "(7) Alternate (min)",
            min_value=0,
            value=45,
            step=5,
        )
    with c4:
        st.markdown("**(8) Reserve 45 min.**")
        reserve_min = 45
        st.write("Tempo: **45 min** (fixo)")

    def l_from_min(mins, rate=RATE_LPH):
        return round(rate * (mins / 60.0), 1)

    def fmt_l(v):
        return f"{v:.1f} L" if abs(v - round(v)) > 1e-9 else f"{int(round(v))} L"

    # Trip = 2+3+4
    enrt_min_eff = enrt_h * 60 + enrt_min
    trip_min = climb_min + enrt_min_eff + desc_min
    trip_l = l_from_min(trip_min)

    # (6) Contingency 5% do (5)
    cont_min = int(round(0.05 * trip_min))
    cont_l = round(0.05 * trip_l, 1)

    # Litros de cada bloco
    taxi_l = l_from_min(taxi_min)
    climb_l = l_from_min(climb_min)
    enrt_l = l_from_min(enrt_min_eff)
    desc_l = l_from_min(desc_min)
    alt_l = l_from_min(alt_min)
    reserve_l = l_from_min(reserve_min)

    # (9) Required Ramp = 1 + 5 + 6 + 7 + 8
    req_ramp = round(taxi_l + trip_l + cont_l + alt_l + reserve_l, 1)
    req_ramp_min = taxi_min + trip_min + cont_min + alt_min + reserve_min

    # (10) Extra — auto para bater com M&B
    extra_l = max(0.0, round(fuel_l_mb - req_ramp, 1))
    extra_min = int(round((extra_l / RATE_LPH) * 60))

    # (11) Total Ramp = 9 + 10
    total_ramp = round(req_ramp + extra_l, 1)
    total_ramp_min = req_ramp_min + extra_min

    # Aviso de insuficiência de fuel
    if fuel_l_mb < req_ramp:
        st.error(
            f"Fuel insuficiente: carregado {fuel_l_mb:.1f} L, "
            f"requerido {req_ramp:.1f} L."
        )

    # Quadro/Resumo
    st.markdown("#### Quadro (1)–(11)")
    rows = [
        ("(1) Start-up & Taxi", taxi_min, taxi_l),
        ("(2) Climb", climb_min, climb_l),
        ("(3) Enroute", enrt_min_eff, enrt_l),
        ("(4) Descent", desc_min, desc_l),
        ("(5) Trip Fuel (2 + 3 + 4)", trip_min, trip_l),
        ("(6) Contingency 5% (5)", cont_min, cont_l),
        ("(7) Alternate", alt_min, alt_l),
        ("(8) Reserve 45 min.", reserve_min, reserve_l),
        (
            "(9) Required Ramp Fuel (1 + 5 + 6 + 7 + 8)",
            req_ramp_min,
            req_ramp,
        ),
        ("(10) Extra (auto p/ bater M&B)", extra_min, extra_l),
        ("(11) Total Ramp Fuel (9 + 10)", total_ramp_min, total_ramp),
        ("Fuel carregado (M&B)", 0, fuel_l_mb),
    ]

    html = [
        "<table class='mb-table'><tr><th>Item</th><th>Time</th><th>Fuel</th></tr>"
    ]
    for name, mins, liters in rows:
        t = fmt_hm(mins) if mins else "—"
        html.append(
            f"<tr><td>{name}</td><td>{t}</td><td>{fmt_l(liters)}</td></tr>"
        )
    html.append("</table>")

    st.markdown("".join(html), unsafe_allow_html=True)

    # Guardar para o PDF
    st.session_state["_fuel"] = {
        "policy": "Detailed",
        "rate_lph": RATE_LPH,
        "trip_l": trip_l,
        "cont_l": cont_l,
        "req_ramp": req_ramp,
        "extra_l": extra_l,
        "total_ramp": total_ramp,
        "taxi_min": taxi_min,
        "climb_min": climb_min,
        "enrt_min": enrt_min_eff,
        "desc_min": desc_min,
        "alt_min": alt_min,
        "reserve_min": reserve_min,
        "cont_min": cont_min,
        "req_ramp_min": req_ramp_min,
        "total_ramp_min": total_ramp_min,
        "block9_min": 0,
    }


# ---- 5) PDF ----
with tab_pdf:
    st.markdown("### PDF – M&B and Performance Data Sheet (NEW)")

    reg = st.session_state.get("reg", "CS-XXX")
    date_str = st.session_state.get(
        "date_str",
        dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%d/%m/%Y"),
    )
    st.caption(f"Date: **{date_str}** (definido na 1ª aba)")

    roles = {"Departure": "Dep", "Arrival": "Arr", "Alternate": "Alt"}

    def read_pdf_bytes(paths) -> bytes:
        for path_str in paths:
            p = Path(path_str)
            if p.exists():
                return p.read_bytes()
        raise FileNotFoundError(
            f"Template not found in any known path: {paths}"
        )

    def get_field_names(template_bytes: bytes) -> set:
        names = set()
        reader = PdfReader(io.BytesIO(template_bytes))

        # via get_fields
        try:
            fd = reader.get_fields()
            if fd:
                names.update(fd.keys())
        except Exception:
            pass

        # brute force annots
        try:
            for page in reader.pages:
                if "/Annots" in page:
                    for a in page["/Annots"]:
                        obj = a.get_object()
                        if obj.get("/T"):
                            names.add(str(obj["/T"]))
        except Exception:
            pass

        return names

    def put_any(out: dict, fieldset: set, keys, value: str):
        if isinstance(keys, str):
            keys = [keys]
        for k in keys:
            if k in fieldset:
                out[k] = value

    # 🔧 CORRIGIDO: garantir que todos os valores são strings para pypdf 4.x
    def fill_pdf(template_bytes: bytes, fields: dict) -> bytes:
        reader = PdfReader(io.BytesIO(template_bytes))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        root = reader.trailer["/Root"]
        if "/AcroForm" not in root:
            raise RuntimeError("Template PDF has no AcroForm/fields.")

        writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
        try:
            writer._root_object["/AcroForm"].update(
                {NameObject("/NeedAppearances"): True}
            )
        except Exception:
            pass

        # SANITIZE: tudo para string (ou "" se None)
        safe_fields = {}
        for k, v in fields.items():
            if v is None:
                safe_fields[k] = ""
            else:
                safe_fields[k] = str(v)

        for page in writer.pages:
            writer.update_page_form_field_values(page, safe_fields)

        bio = io.BytesIO()
        writer.write(bio)
        return bio.getvalue()

    try:
        template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
        fieldset = get_field_names(template_bytes)
        named_map = {}

        wb = st.session_state.get("_wb", {})
        fuel = st.session_state.get("_fuel", {})
        perf_rows = st.session_state.get("_perf_rows", [])

        # Base / M&B
        put_any(named_map, fieldset, "Aircraf_Reg", reg or "")
        put_any(named_map, fieldset, "Date", date_str)

        ew = wb.get("ew", 0.0)
        ewm = wb.get("ew_moment", 0.0)
        fuel_l = wb.get("fuel_l", 0.0)
        fuel_w = fuel_l * AC["fuel_density"]

        put_any(named_map, fieldset, "EmptyWeight_W", f"{ew:.0f}")
        put_any(
            named_map,
            fieldset,
            "EmptyWeight_A",
            f"{(ewm/ew if ew>0 else 0.0):.3f}",
        )
        put_any(named_map, fieldset, "EmptyWeight_M", f"{ewm:.2f}")

        put_any(named_map, fieldset, "Fuel_W", f"{fuel_w:.0f}")
        put_any(
            named_map,
            fieldset,
            "Fuel_M",
            f"{(fuel_w*AC['fuel_arm']):.2f}",
        )

        put_any(
            named_map,
            fieldset,
            "Pilot&Passenger_W",
            f"{wb.get('pilot',0.0):.0f}",
        )
        put_any(
            named_map,
            fieldset,
            "Pilot&Passenger_M",
            f"{(wb.get('pilot',0.0)*AC['pilot_arm']):.2f}",
        )
        put_any(
            named_map,
            fieldset,
            "Baggage_W",
            f"{wb.get('baggage',0.0):.0f}",
        )
        put_any(
            named_map,
            fieldset,
            "Baggage_M",
            f"{(wb.get('baggage',0.0)*AC['baggage_arm']):.2f}",
        )

        put_any(
            named_map,
            fieldset,
            "TOTAL_W",
            f"{wb.get('total_weight',0.0):.0f}",
        )
        put_any(
            named_map,
            fieldset,
            "TOTAL_M",
            f"{wb.get('total_moment',0.0):.2f}",
        )
        put_any(named_map, fieldset, "CG", f"{wb.get('cg',0.0):.3f}")

        # Per-leg (Departure / Arrival / Alternate)
        by_role = {r["role"]: r for r in perf_rows} if perf_rows else {}
        for role, suf in {"Departure": "Dep", "Arrival": "Arr", "Alternate": "Alt"}.items():
            r = by_role.get(role)
            if not r:
                continue

            put_any(named_map, fieldset, f"Airfield_{suf}", r["icao"])
            put_any(named_map, fieldset, f"QFU_{suf}", f"{int(round(r['qfu'])):03d}")
            put_any(
                named_map,
                fieldset,
                f"Elev_{suf}",
                f"{int(round(r['elev_ft']))}",
            )
            put_any(
                named_map,
                fieldset,
                f"QNH_{suf}",
                f"{int(round(r['qnh']))}",
            )
            put_any(
                named_map,
                fieldset,
                f"Temp_{suf}",
                f"{int(round(r['temp']))}",
            )
            put_any(
                named_map,
                fieldset,
                f"Wind_{suf}",
                f"{int(round(r['wind_dir'])):03d}/{int(round(r['wind_kt'])):02d}",
            )
            put_any(
                named_map,
                fieldset,
                f"PA_{suf}",
                f"{int(round(r['pa_ft']))}",
            )
            put_any(
                named_map,
                fieldset,
                f"DA_{suf}",
                f"{int(round(r['da_ft']))}",
            )
            put_any(
                named_map,
                fieldset,
                f"TODA_{suf}",
                f"{int(round(r['toda_av']))}",
            )

            tod_str = (
                f"{int(round(r['to_50']))} ({int(round(r['pct_todr']))}%)"
            )
            ldr_str = (
                f"{int(round(r['ldg_50']))} ({int(round(r['pct_ldr']))}%)"
            )

            put_any(
                named_map,
                fieldset,
                f"TODR_{suf}",
                tod_str,
            )
            put_any(
                named_map,
                fieldset,
                f"LDA_{suf}",
                f"{int(round(r['lda_av']))}",
            )
            put_any(
                named_map,
                fieldset,
                f"LDR_{suf}",
                ldr_str,
            )
            put_any(
                named_map,
                fieldset,
                f"ROC_{suf}",
                f"{int(round(r.get('roc', 0)))}",
            )

        # Fuel block (PDF quer tempos + litros)
        rate_pdf = float(fuel.get("rate_lph", 20.0))

        def L_from_min(m):
            return int(round(rate_pdf * ((m or 0) / 60.0)))

        taxi_min_pdf = int(round(fuel.get("taxi_min", 0)))
        climb_min_pdf = int(round(fuel.get("climb_min", 0)))
        enrt_min_pdf = int(round(fuel.get("enrt_min", 0)))
        desc_min_pdf = int(round(fuel.get("desc_min", 0)))
        cont_min_pdf = int(round(fuel.get("cont_min", 0)))
        alt_min_pdf = int(round(fuel.get("alt_min", 0)))
        reserve_min_pdf = int(round(fuel.get("reserve_min", 45)))

        trip_min_pdf = int(round(fuel.get("trip_l", 0) / rate_pdf * 60))
        extra_l_pdf = int(round(fuel.get("extra_l", 0)))
        req_ramp_pdf = int(round(fuel.get("req_ramp", 0)))
        total_ramp_pdf = int(round(fuel.get("total_ramp", 0)))

        req_ramp_min_pdf = int(round(fuel.get("req_ramp_min", 0)))
        total_ramp_min_pdf = int(round(
            fuel.get("total_ramp_min", req_ramp_min_pdf)
        ))

        put_any(named_map, fieldset, "Taxi_T", fmt_hm(taxi_min_pdf))
        put_any(
            named_map,
            fieldset,
            "Taxi_F",
            f"{L_from_min(taxi_min_pdf)} L",
        )

        put_any(
            named_map,
            fieldset,
            "Climb_T",
            fmt_hm(climb_min_pdf),
        )
        put_any(
            named_map,
            fieldset,
            "Climb_F",
            f"{L_from_min(climb_min_pdf)} L",
        )

        put_any(
            named_map,
            fieldset,
            "Enroute_T",
            fmt_hm(enrt_min_pdf),
        )
        put_any(
            named_map,
            fieldset,
            "Enroute_F",
            f"{L_from_min(enrt_min_pdf)} L",
        )

        put_any(
            named_map,
            fieldset,
            "Descent_T",
            fmt_hm(desc_min_pdf),
        )
        put_any(
            named_map,
            fieldset,
            "Descent_F",
            f"{L_from_min(desc_min_pdf)} L",
        )

        put_any(
            named_map,
            fieldset,
            "Trip_T",
            fmt_hm(trip_min_pdf),
        )
        put_any(
            named_map,
            fieldset,
            "Trip_F",
            f"{int(round(fuel.get('trip_l',0)))} L",
        )

        put_any(
            named_map,
            fieldset,
            "Contingency_T",
            fmt_hm(cont_min_pdf),
        )
        put_any(
            named_map,
            fieldset,
            "Contingency_F",
            f"{int(round(fuel.get('cont_l',0)))} L",
        )

        put_any(
            named_map,
            fieldset,
            "Alternate_T",
            fmt_hm(alt_min_pdf),
        )
        put_any(
            named_map,
            fieldset,
            "Alternate_F",
            f"{L_from_min(alt_min_pdf)} L",
        )

        put_any(
            named_map,
            fieldset,
            "Reserve_T",
            fmt_hm(reserve_min_pdf),
        )
        put_any(
            named_map,
            fieldset,
            "Reserve_F",
            f"{L_from_min(reserve_min_pdf)} L",
        )

        put_any(
            named_map,
            fieldset,
            "Ramp_T",
            fmt_hm(req_ramp_min_pdf),
        )
        put_any(
            named_map,
            fieldset,
            "Ramp_F",
            f"{req_ramp_pdf} L",
        )

        put_any(
            named_map,
            fieldset,
            "Extra_T",
            fmt_hm(int(round((extra_l_pdf/rate_pdf) * 60))),
        )
        put_any(
            named_map,
            fieldset,
            "Extra_F",
            f"{extra_l_pdf} L",
        )

        put_any(
            named_map,
            fieldset,
            "Total_T",
            fmt_hm(total_ramp_min_pdf),
        )
        put_any(
            named_map,
            fieldset,
            "Total_F",
            f"{total_ramp_pdf} L",
        )

        if st.button("Generate filled PDF", type="primary"):
            try:
                out_bytes = fill_pdf(template_bytes, named_map)

                mission = ascii_safe(
                    st.session_state.get("mission_no", "")
                ).strip().replace(" ", "_")
                mission_part = f"{mission}_" if mission else ""
                file_name = f"{mission_part}{reg}_P2008_MB_Perf.pdf"

                st.download_button(
                    "Download PDF",
                    data=out_bytes,
                    file_name=file_name,
                    mime="application/pdf",
                )
                st.success("PDF generated. Review before flight.")
            except Exception as e:
                st.error(f"Could not generate PDF: {e}")

    except Exception as e:
        st.error(f"Cannot prepare PDF mapping: {e}")
