# Streamlit app — Tecnam P2008 (M&B + Performance) — EN
# No email. Manual fuel (L) by default. PDF template filling + human narrative page.
# Requirements:
#   streamlit
#   pytz
#   pdfrw==0.4
#   pypdf>=4.2.0
#   fpdf

import streamlit as st
import datetime
from pathlib import Path
import pytz
import unicodedata
from math import cos, radians

# PDF
from pdfrw import PdfReader as Rd_pdfrw, PdfWriter as Wr_pdfrw, PdfDict
from pypdf import PdfReader as Rd_pypdf, PdfWriter as Wr_pypdf
from fpdf import FPDF

# ------------------ helpers & style ------------------
def ascii_safe(text):
    if not isinstance(text, str):
        return str(text)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

def printable(s: str) -> str:
    # ASCII only + replace symbols that can break FPDF line breaking/encodings
    return (
        ascii_safe(str(s))
        .replace("≈", "~")
        .replace("°", " deg")
        .replace("–", "-")
        .replace("—", "-")
        .replace("→", "->")
        .replace("’", "'")
        .replace("·", "*")  # kg·m -> kg*m
    )

st.set_page_config(
    page_title="Tecnam P2008 — Mass & Balance & Performance (EN)",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.block-container { max-width: 1120px !important; }
.mb-header{font-size:1.28rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:6px;margin-bottom:10px}
.section-title{font-weight:700;margin:14px 0 6px 0}
.mb-summary-row{display:flex;justify-content:space-between;margin:4px 0}
.ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
.card{border:1px solid #e5e7ec;border-radius:8px;padding:10px 12px;margin:8px 0}
.card h5{margin:0 0 6px 0}
.row{display:flex;gap:18px;flex-wrap:wrap;font-size:0.95rem}
</style>
""", unsafe_allow_html=True)

APP_DIR = Path(__file__).parent
PDF_TEMPLATE = APP_DIR / "TecnamP2008MBPerformanceSheet_MissionX.pdf"

# ------------------ aircraft data (fixed) ------------------
AC = {
    "name": "Tecnam P2008 JC",
    "fuel_arm": 2.209,
    "pilot_arm": 1.800,
    "baggage_arm": 2.417,
    "max_takeoff_weight": 650.0,
    "max_fuel_volume": 124.0,  # L
    "max_passenger_weight": 230.0,
    "max_baggage_weight": 20.0,
    "cg_limits": (1.841, 1.978),
    "fuel_density": 0.72,  # kg/L
    "units": {"weight": "kg", "arm": "m"},
}

# ------------------ defaults for aerodromes (tweak if needed) ------------------
AERODROMES_DEFAULT = [
    {"role":"Departure","icao":"LPSO","elev_ft":390.0,"qfu":30.0,"toda":1800.0,"lda":1800.0,
     "paved":True,"slope_pc":0.0,"qnh":1013.0,"temp":15.0,"wind_dir":0.0,"wind_kt":0.0},
    {"role":"Arrival","icao":"LPEV","elev_ft":807.0,"qfu":10.0,"toda":1300.0,"lda":1245.0,
     "paved":True,"slope_pc":0.0,"qnh":1013.0,"temp":15.0,"wind_dir":0.0,"wind_kt":0.0},
    {"role":"Alternate","icao":"LPCB","elev_ft":1251.0,"qfu":160.0,"toda":1520.0,"lda":1460.0,
     "paved":True,"slope_pc":0.0,"qnh":1013.0,"temp":15.0,"wind_dir":0.0,"wind_kt":0.0},
]

# ------------------ AFM-derived performance tables (short extracts) ------------------
TAKEOFF = {
    0:     {"GR":{-25:144,0:182,25:224,50:272}, "50ft":{-25:304,0:379,25:463,50:557}},
    1000:  {"GR":{-25:157,0:198,25:245,50:297}, "50ft":{-25:330,0:412,25:503,50:605}},
    2000:  {"GR":{-25:172,0:216,25:267,50:324}, "50ft":{-25:359,0:448,25:547,50:658}},
    3000:  {"GR":{-25:188,0:236,25:292,50:354}, "50ft":{-25:391,0:487,25:595,50:717}},
    4000:  {"GR":{-25:205,0:258,25:319,50:387}, "50ft":{-25:425,0:530,25:648,50:780}},
    5000:  {"GR":{-25:224,0:283,25:349,50:423}, "50ft":{-25:463,0:578,25:706,50:850}},
    6000:  {"GR":{-25:246,0:309,25:381,50:463}, "50ft":{-25:505,0:630,25:770,50:927}},
    7000:  {"GR":{-25:269,0:339,25:418,50:507}, "50ft":{-25:551,0:687,25:840,50:1011}},
    8000:  {"GR":{-25:295,0:371,25:458,50:555}, "50ft":{-25:601,0:750,25:917,50:1104}},
    9000:  {"GR":{-25:323,0:407,25:502,50:609}, "50ft":{-25:657,0:819,25:1002,50:1205}},
    10000: {"GR":{-25:354,0:446,25:551,50:668}, "50ft":{-25:718,0:895,25:1095,50:1318}},
}

LANDING = {
    0:     {"GR":{-25:149,0:164,25:179,50:194}, "50ft":{-25:358,0:373,25:388,50:403}},
    1000:  {"GR":{-25:154,0:170,25:186,50:201}, "50ft":{-25:363,0:379,25:395,50:410}},
    2000:  {"GR":{-25:160,0:176,25:192,50:209}, "50ft":{-25:369,0:385,25:401,50:418}},
    3000:  {"GR":{-25:166,0:183,25:200,50:216}, "50ft":{-25:375,0:392,25:409,50:425}},
    4000:  {"GR":{-25:172,0:190,25:207,50:225}, "50ft":{-25:381,0:399,25:416,50:434}},
    5000:  {"GR":{-25:179,0:197,25:215,50:233}, "50ft":{-25:388,0:406,25:424,50:442}},
    6000:  {"GR":{-25:186,0:205,25:223,50:242}, "50ft":{-25:395,0:414,25:432,50:451}},
    7000:  {"GR":{-25:193,0:212,25:232,50:251}, "50ft":{-25:402,0:421,25:441,50:460}},
    8000:  {"GR":{-25:200,0:221,25:241,50:261}, "50ft":{-25:410,0:430,25:450,50:470}},
    9000:  {"GR":{-25:208,0:229,25:250,50:271}, "50ft":{-25:417,0:438,25:459,50:480}},
    10000: {"GR":{-25:217,0:238,25:260,50:282}, "50ft":{-25:426,0:447,25:469,50:491}},
}

ROC = {
    650:{0:{-25:951,0:805,25:675,50:557},2000:{-25:840,0:696,25:568,50:453},4000:{-25:729,0:588,25:462,50:349},6000:{-25:619,0:480,25:357,50:245},8000:{-25:509,0:373,25:251,50:142},10000:{-25:399,0:266,25:146,50:39},12000:{-25:290,0:159,25:42,50:-64},14000:{-25:181,0:53,25:-63,50:-166}},
    600:{0:{-25:1067,0:913,25:776,50:652},2000:{-25:950,0:799,25:664,50:542},4000:{-25:833,0:685,25:552,50:433},6000:{-25:717,0:571,25:441,50:324},8000:{-25:602,0:458,25:330,50:215},10000:{-25:486,0:345,25:220,50:106},12000:{-25:371,0:233,25:110,50:-2},14000:{-25:257,0:121,25:0,50:-109}},
    550:{0:{-25:1201,0:1038,25:892,50:760},2000:{-25:1077,0:916,25:773,50:644},4000:{-25:953,0:795,25:654,50:527},6000:{-25:830,0:675,25:536,50:411},8000:{-25:707,0:555,25:419,50:296},10000:{-25:584,0:435,25:301,50:181},12000:{-25:462,0:315,25:184,50:66},14000:{-25:341,0:196,25:68,50:-48}},
}

# ------------------ math helpers ------------------
def clamp(v, lo, hi): return max(lo, min(hi, v))

def interp1(x, x0, x1, y0, y1):
    if x1 == x0: return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def bilinear(pa, temp, table, key):
    pas = sorted(table.keys())
    pa = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa]); p1 = min([p for p in pas if p >= pa])
    temps = [-25, 0, 25, 50]
    t = clamp(temp, temps[0], temps[-1])
    if t <= 0: t0, t1 = -25, 0
    elif t <= 25: t0, t1 = 0, 25
    else: t0, t1 = 25, 50
    v00 = table[p0][key][t0]; v01 = table[p0][key][t1]
    v10 = table[p1][key][t0]; v11 = table[p1][key][t1]
    v0 = interp1(t, t0, t1, v00, v01)
    v1 = interp1(t, t0, t1, v10, v11)
    return interp1(pa, p0, p1, v0, v1)

def roc_interp(pa, temp, weight):
    w = clamp(weight, 550.0, 650.0)
    def roc_for_w(w_):
        tab = ROC[int(w_)]
        pas = sorted(tab.keys())
        pa_c = clamp(pa, pas[0], pas[-1])
        p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
        temps = [-25, 0, 25, 50]
        t = clamp(temp, temps[0], temps[-1])
        if t <= 0: t0, t1 = -25, 0
        elif t <= 25: t0, t1 = 0, 25
        else: t0, t1 = 25, 50
        v00 = tab[p0][t0]; v01 = tab[p0][t1]
        v10 = tab[p1][t0]; v11 = tab[p1][t1]
        v0 = interp1(t, t0, t1, v00, v01)
        v1 = interp1(t, t0, t1, v10, v11)
        return interp1(pa_c, p0, p1, v0, v1)
    if w <= 600: return interp1(w, 550, 600, roc_for_w(550), roc_for_w(600))
    else: return interp1(w, 600, 650, roc_for_w(600), roc_for_w(650))

def wind_head_component(runway_qfu_deg, wind_dir_deg, wind_speed):
    if runway_qfu_deg is None or wind_dir_deg is None: return 0.0
    diff = radians((wind_dir_deg - runway_qfu_deg) % 360)
    return wind_speed * cos(diff)

def to_corrections_takeoff(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = ground_roll
    gr = gr - 5.0*headwind_kt if headwind_kt >= 0 else gr + 15.0*abs(headwind_kt)
    if paved: gr *= 0.9
    if slope_pc: gr *= (1.0 + 0.07 * (slope_pc/1.0))
    return max(gr, 0.0)

def ldg_corrections(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = ground_roll
    gr = gr - 4.0*headwind_kt if headwind_kt >= 0 else gr + 13.0*abs(headwind_kt)
    if paved: gr *= 0.9
    if slope_pc: gr *= (1.0 - 0.03 * (slope_pc/1.0))
    return max(gr, 0.0)

# ------------------ UI: W&B ------------------
st.markdown('<div class="mb-header">Tecnam P2008 — Mass & Balance & Performance</div>', unsafe_allow_html=True)

lc, _, rc = st.columns([0.42, 0.02, 0.56], gap="large")

with lc:
    st.markdown("### Weight & balance (inputs)")
    ew = st.number_input("Empty weight (kg)", 0.0, step=1.0)
    ew_moment = st.number_input("Empty weight moment (kg*m)", 0.0, step=0.1)
    student = st.number_input("Student weight (kg)", 0.0, step=1.0)
    instructor = st.number_input("Instructor weight (kg)", 0.0, step=1.0)
    baggage = st.number_input("Baggage (kg)", 0.0, step=1.0)
    fuel_l = st.number_input("Fuel (L)", 0.0, step=1.0)  # manual by default

    pilot = student + instructor
    fuel_wt = fuel_l * AC['fuel_density']

    m_empty = ew_moment
    m_pilot = pilot * AC['pilot_arm']
    m_bag = baggage * AC['baggage_arm']
    m_fuel = fuel_wt * AC['fuel_arm']

    total_weight = ew + pilot + baggage + fuel_wt
    total_moment = m_empty + m_pilot + m_bag + m_fuel
    cg = (total_moment/total_weight) if total_weight > 0 else 0.0

    remaining_by_mtow = max(0.0, AC['max_takeoff_weight'] - (ew + pilot + baggage + fuel_wt))
    remaining_by_tank = max(0.0, AC['max_fuel_volume']*AC['fuel_density'] - fuel_wt)
    remaining_fuel_weight = min(remaining_by_mtow, remaining_by_tank)
    remaining_fuel_l = remaining_fuel_weight / AC['fuel_density']
    limit_label = "Tank capacity" if remaining_by_tank < remaining_by_mtow else "Maximum weight"

    def w_color(val, limit):
        if val > limit: return 'bad'
        if val > 0.95*limit: return 'warn'
        return 'ok'
    def cg_color_val(cg_val, limits):
        lo, hi = limits
        margin = 0.05*(hi-lo)
        if cg_val<lo or cg_val>hi: return 'bad'
        if cg_val<lo+margin or cg_val>hi-margin: return 'warn'
        return 'ok'

    st.markdown("#### Summary")
    st.markdown(f"<div class='mb-summary-row'><div>Extra fuel possible</div><div><b>{remaining_fuel_l:.1f} L</b> (limited by <i>{limit_label}</i>)</div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>Total weight</div><div class='{w_color(total_weight, AC['max_takeoff_weight'])}'><b>{total_weight:.1f} kg</b></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>Total moment</div><div><b>{total_moment:.2f} kg*m</b></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>CG</div><div class='{cg_color_val(cg, AC['cg_limits'])}'><b>{cg:.3f} m</b></div></div>", unsafe_allow_html=True)

# ------------------ UI: Aerodromes & performance ------------------
with rc:
    st.markdown("### Aerodromes & performance")
    if 'aerodromes' not in st.session_state:
        st.session_state.aerodromes = AERODROMES_DEFAULT

    perf_rows = []
    for i, a in enumerate(st.session_state.aerodromes):
        with st.expander(f"{a['role']} — {a['icao']}", expanded=(i==0)):
            icao = st.text_input("ICAO", a['icao'], key=f"icao_{i}")
            qfu = st.number_input("RWY QFU (deg, heading)", 0.0, 360.0, float(a['qfu']), 1.0, key=f"qfu_{i}")
            elev = st.number_input("Elevation (ft)", value=float(a['elev_ft']), step=1.0, key=f"elev_{i}")
            qnh = st.number_input("QNH (hPa)", 900.0, 1050.0, float(a['qnh']), 0.1, key=f"qnh_{i}")
            temp = st.number_input("Temperature (°C)", -40.0, 60.0, float(a['temp']), 0.1, key=f"temp_{i}")
            wind_dir = st.number_input("Wind direction (deg FROM)", 0.0, 360.0, float(a['wind_dir']), 1.0, key=f"wdir_{i}")
            wind_kt = st.number_input("Wind speed (kt)", 0.0, value=float(a['wind_kt']), step=1.0, key=f"wspd_{i}")
            paved = st.checkbox("Paved runway", value=bool(a['paved']), key=f"paved_{i}")
            slope_pc = st.number_input("Runway slope (%) (uphill positive)", value=float(a['slope_pc']), step=0.1, key=f"slope_{i}")
            toda_av = st.number_input("TODA available (m)", 0.0, value=float(a['toda']), step=1.0, key=f"toda_{i}")
            lda_av = st.number_input("LDA available (m)", 0.0, value=float(a['lda']), step=1.0, key=f"lda_{i}")

            st.session_state.aerodromes[i].update({
                "icao":icao,"qfu":qfu,"elev_ft":elev,"qnh":qnh,"temp":temp,"wind_dir":wind_dir,
                "wind_kt":wind_kt,"paved":paved,"slope_pc":slope_pc,"toda":toda_av,"lda":lda_av
            })

            # PA/DA (real, from QNH & OAT)
            pa_ft = elev + (1013.25 - qnh) * 27
            isa_temp = 15 - 2*(pa_ft/1000)
            da_ft = pa_ft + (120*(temp - isa_temp))

            # Interpolate
            to_gr = bilinear(pa_ft, temp, TAKEOFF, 'GR')
            to_50 = bilinear(pa_ft, temp, TAKEOFF, '50ft')
            ldg_gr = bilinear(pa_ft, temp, LANDING, 'GR')
            ldg_50 = bilinear(pa_ft, temp, LANDING, '50ft')

            # Corrections
            hw = wind_head_component(qfu, wind_dir, wind_kt)
            to_gr_corr = to_corrections_takeoff(to_gr, hw, paved=paved, slope_pc=slope_pc)
            ldg_gr_corr = ldg_corrections(ldg_gr, hw, paved=paved, slope_pc=slope_pc)

            perf_rows.append({
                'role': a['role'], 'icao': icao, 'qfu': qfu,
                'elev_ft': elev, 'qnh': qnh, 'temp': temp,
                'pa_ft': pa_ft, 'da_ft': da_ft, 'isa_temp': isa_temp,
                'to_gr': to_gr_corr, 'to_50': to_50,
                'ldg_gr': ldg_gr_corr, 'ldg_50': ldg_50,
                'toda_av': toda_av, 'lda_av': lda_av,
                'hw_comp': hw,
            })

    # Cleaner on-screen cards
    st.markdown("#### Performance summary")
    for r in perf_rows:
        st.markdown(
            f"""
<div class="card">
  <h5>{r['role']} — {r['icao']} (QFU {r['qfu']:.0f}°)</h5>
  <div class="row">
    <div><b>PA:</b> {r['pa_ft']:.0f} ft</div>
    <div><b>DA:</b> {r['da_ft']:.0f} ft</div>
    <div><b>Wind comp:</b> {r['hw_comp']:.0f} kt</div>
    <div><b>TODA/LDA:</b> {int(r['toda_av'])}/{int(r['lda_av'])} m</div>
  </div>
  <div style="margin-top:6px;font-size:0.95rem;">
    <div><b>Take-off:</b> GR ~ {r['to_gr']:.0f} m; over 50 ft ~ {r['to_50']:.0f} m</div>
    <div><b>Landing:</b> GR ~ {r['ldg_gr']:.0f} m; over 50 ft ~ {r['ldg_50']:.0f} m</div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

# ------------------ Fuel planning (20 L/h) ------------------
RATE_LPH = 20.0
st.markdown("### Fuel planning (assume 20 L/h by default)")

c1, c2, c3, c4 = st.columns([0.25,0.25,0.25,0.25])
def time_to_liters(h=0, m=0, rate=RATE_LPH): return rate * (h + m/60.0)

with c1:
    su_min = st.number_input("Start-up & taxi (min)", 0, value=15, step=1)
    climb_min = st.number_input("Climb (min)", 0, value=15, step=1)
with c2:
    enrt_h = st.number_input("Enroute (h)", 0, value=2, step=1)
    enrt_min = st.number_input("Enroute (min)", 0, value=15, step=1)
with c3:
    desc_min = st.number_input("Descent (min)", 0, value=15, step=1)
    alt_min = st.number_input("Alternate (min)", 0, value=60, step=5)
with c4:
    reserve_min = st.number_input("Reserve (min)", 0, value=45, step=5)
    extra_min = st.number_input("Extra (min)", 0, value=0, step=5)

trip_l = time_to_liters(0, climb_min) + time_to_liters(enrt_h, enrt_min) + time_to_liters(0, desc_min)
cont_l = 0.05 * trip_l
req_ramp = time_to_liters(0, su_min) + trip_l + cont_l + time_to_liters(0, alt_min) + time_to_liters(0, reserve_min)
extra_l = time_to_liters(0, extra_min)
total_ramp = req_ramp + extra_l

st.markdown(f"- **Trip fuel**: {trip_l:.1f} L")
st.markdown(f"- **Contingency 5%**: {cont_l:.1f} L")
st.markdown(f"- **Required ramp fuel** (1+5+6+7+8): **{req_ramp:.1f} L**")
st.markdown(f"- **Extra**: {extra_l:.1f} L")
st.markdown(f"- **Total ramp fuel**: **{total_ramp:.1f} L**")

# ------------------ PDF generation ------------------
st.markdown("### PDF — M&B and Performance Data Sheet")
reg = st.text_input("Aircraft registration", "CS-XXX")
mission = st.text_input("Mission #", "001")
utc_today = datetime.datetime.now(pytz.UTC)
date_str = st.text_input("Date (DD/MM/YYYY)", utc_today.strftime("%d/%m/%Y"))

if st.button("Generate filled PDF"):
    if not PDF_TEMPLATE.exists():
        st.error(f"Template not found: {PDF_TEMPLATE}")
        st.stop()

    # 1) Build human narrative calculations page (FPDF) with safe margins + width
    calc_pdf_path = APP_DIR / f"_calc_mission_{mission}.pdf"
    calc = FPDF()
    calc.set_auto_page_break(auto=True, margin=12)
    calc.set_margins(12, 12, 12)
    calc.add_page()
    usable_w = calc.w - calc.l_margin - calc.r_margin
    W = usable_w

    calc.set_font("Arial", "B", 14)
    calc.cell(0, 8, printable("Tecnam P2008 - Calculations (summary)"), ln=True)

    # W&B
    calc.set_font("Arial", "B", 12)
    calc.cell(0, 7, printable("Weight & balance"), ln=True)
    calc.set_font("Arial", size=10)
    calc.multi_cell(W, 5, printable(
        f"Empty weight {ew:.0f} kg (moment {ew_moment:.0f} kg*m). "
        f"Student/Instructor {student:.0f}/{instructor:.0f} kg; baggage {baggage:.0f} kg. "
        f"Fuel {fuel_l:.0f} L (~ {fuel_wt:.0f} kg). Total weight {total_weight:.0f} kg, moment {total_moment:.0f} kg*m; CG {cg:.3f} m. "
        f"Extra fuel possible: {remaining_fuel_l:.1f} L (limited by {limit_label})."
    ))

    # Performance
    calc.ln(2)
    calc.set_font("Arial", "B", 12)
    calc.cell(0, 7, printable("Performance - method & results"), ln=True)
    calc.set_font("Arial", size=10)
    for r in perf_rows:
        calc.set_font("Arial", "B", 10)
        calc.multi_cell(W, 6, printable(f"{r['role']} - {r['icao']} (QFU {r['qfu']:.0f} deg)"))
        calc.set_font("Arial", size=10)
        calc.multi_cell(W, 5, printable(f"Atmospherics: elevation {r['elev_ft']:.0f} ft, QNH {r['qnh']:.1f} -> PA ~ {r['pa_ft']:.0f} ft."))
        calc.multi_cell(W, 5, printable(f"ISA at PA ~ {r['isa_temp']:.1f} C; with OAT {r['temp']:.1f} C -> DA ~ {r['da_ft']:.0f} ft."))
        # Use current session aerodrome flags
        arow = next((a for a in st.session_state.aerodromes if a['icao']==r['icao'] and a['role']==r['role']), None)
        paved_flag = bool(arow['paved']) if arow else True
        slope_val = float(arow['slope_pc']) if arow else 0.0
        calc.multi_cell(W, 5, printable("Method: bilinear interpolation on AFM tables using PA and OAT."))
        calc.multi_cell(W, 5, printable(f"Corrections applied: wind component {r['hw_comp']:.0f} kt, surface {'paved' if paved_flag else 'grass'}, slope {slope_val:.1f}%."))
        calc.multi_cell(W, 5, printable(f"Take-off: ground roll ~ {r['to_gr']:.0f} m; over 50 ft ~ {r['to_50']:.0f} m."))
        calc.multi_cell(W, 5, printable(f"Landing: ground roll ~ {r['ldg_gr']:.0f} m; over 50 ft ~ {r['ldg_50']:.0f} m."))
        calc.multi_cell(W, 5, printable(f"Declared distances: TODA {r['toda_av']:.0f} m; LDA {r['lda_av']:.0f} m."))
        calc.ln(1)

    # Fuel planning short
    calc.ln(2)
    calc.set_font("Arial", "B", 12)
    calc.cell(0, 7, printable("Fuel planning (20 L/h)"), ln=True)
    calc.set_font("Arial", size=10)
    calc.multi_cell(W, 5, printable(
        f"Trip {trip_l:.1f} L; contingency 5% {cont_l:.1f} L; required ramp {req_ramp:.1f} L; extra {extra_l:.1f} L; total ramp {total_ramp:.1f} L."
    ))
    calc.output(str(calc_pdf_path))

    # 2) Fill the form — we try pdfrw first (color support), then PyPDF pass to force appearances
    def load_pdf_any(path: Path):
        try:
            return "pdfrw", Rd_pdfrw(str(path))
        except Exception:
            try:
                return "pypdf", Rd_pypdf(str(path))
            except Exception as e:
                raise RuntimeError(f"Could not read the PDF: {e}")

    engine, reader = load_pdf_any(PDF_TEMPLATE)
    out_main_path = APP_DIR / f"MB_Performance_Mission_{mission}.pdf"

    # Convenience: build role -> row
    role_to_row = {r['role']: r for r in perf_rows}

    # Field helpers
    def pdfrw_set_field(fields, candidates, value, color_rgb=None):
        if not isinstance(candidates, (list, tuple)):
            candidates = [candidates]
        for name in candidates:
            for f in fields:
                if f.get('/T') and f['/T'][1:-1] == name:
                    f.update(PdfDict(V=str(value)))
                    f.update(PdfDict(AP=None))
                    if color_rgb:
                        r, g, b = color_rgb
                        f.update(PdfDict(DA=f"{r/255:.3f} {g/255:.3f} {b/255:.3f} rg /Helv 10 Tf"))
                    return True
        return False

    # Candidate field names (from your template dump). We’ll try them all.
    ROLE_FIELDS = {
        "Departure": {
            # guessed dep column (older linked boxes used 20-31 range)
            "airfield": ["Textbox20","DEP_AIRFIELD"],
            "qfu":      ["Textbox21","DEP_QFU"],
            "elev":     ["Textbox24","DEP_ELEV"],
            "qnh":      ["Textbox25","DEP_QNH"],
            "temp":     ["Textbox26","DEP_TEMP"],
            "wind":     ["Textbox27","DEP_WIND"],
            "pa":       ["Textbox28","DEP_PA"],
            "da":       ["Textbox29","DEP_DA"],
            "toda_lda": ["Textbox30","DEP_TODA_LDA"],
            "todr":     ["Textbox31","DEP_TODR"],
            "ldr":      ["Textbox1","DEP_LDR"],  # fallback
            "roc":      ["Textbox2","DEP_ROC"],  # fallback
        },
        "Arrival": {
            # from dump: 32..36 + 43 used in your file for ARR
            "airfield": ["Textbox32","ARR_AIRFIELD"],
            "qfu":      ["Text2","Textbox23_ARR","ARR_QFU"],
            "elev":     ["Textbox33","ARR_ELEV"],
            "qnh":      ["Textbox36","ARR_QNH"],
            "temp":     ["Textbox34","ARR_TEMP"],
            "wind":     ["Textbox35","ARR_WIND"],
            "pa":       ["Textbox6","ARR_PA"],
            "da":       ["Textbox7","ARR_DA"],
            "toda_lda": ["Textbox43","ARR_TODA_LDA"],
            "todr":     ["Textbox8","ARR_TODR"],
            "ldr":      ["Textbox41","ARR_LDR","Textbox41_ARR"],
            "roc":      ["Textbox39","ARR_ROC","Textbox39_ARR"],
        },
        "Alternate": {
            # from dump: 22,23,53,52,51,58,50,49,47,45,41,39 clearly used
            "airfield": ["Textbox22","ALT_AIRFIELD"],
            "qfu":      ["Textbox23","ALT_QFU"],
            "elev":     ["Textbox53","ALT_ELEV"],
            "qnh":      ["Textbox52","ALT_QNH"],
            "temp":     ["Textbox51","ALT_TEMP"],
            "wind":     ["Textbox58","ALT_WIND"],
            "pa":       ["Textbox50","ALT_PA"],
            "da":       ["Textbox49","ALT_DA"],
            "toda_lda": ["Textbox47","ALT_TODA_LDA"],
            "todr":     ["Textbox45","ALT_TODR"],
            "ldr":      ["Textbox41","ALT_LDR"],
            "roc":      ["Textbox39","ALT_ROC"],
        },
    }
    BASE_FIELDS = {
        "date": ["Textbox18"],
        "reg":  ["Textbox19"],
        # CG/Weight + extras (colors via pdfrw; values also in PyPDF pass)
        "total_w": ["Textbox14","TOTAL_WEIGHT"],
        "cg":      ["Textbox16","CG_VALUE"],
        "mtow":    ["Textbox17","MTOW"],
        "extra_fuel": ["Textbox70","EXTRA_FUEL"],
        "extra_reason": ["EXTRA_REASON"],
    }

    # 2a) pdfrw pass (colors + initial values) if pdfrw loaded
    if engine == "pdfrw" and hasattr(reader, 'Root') and '/AcroForm' in reader.Root:
        fields = reader.Root.AcroForm.Fields
        # Base
        for nm in BASE_FIELDS["date"]: pdfrw_set_field(fields, nm, date_str)
        for nm in BASE_FIELDS["reg"]: pdfrw_set_field(fields, nm, reg)

        # Color coding
        wt_color = (30,150,30) if total_weight <= AC['max_takeoff_weight'] else (200,0,0)
        lo, hi = AC['cg_limits']
        if cg < lo or cg > hi:
            cg_color = (200,0,0)
        else:
            margin = 0.05*(hi-lo)
            cg_color = (200,150,30) if (cg<lo+margin or cg>hi-margin) else (30,150,30)

        pdfrw_set_field(fields, BASE_FIELDS["total_w"], f"{total_weight:.1f}", wt_color)
        pdfrw_set_field(fields, BASE_FIELDS["cg"], f"{cg:.3f}", cg_color)
        pdfrw_set_field(fields, BASE_FIELDS["mtow"], f"{AC['max_takeoff_weight']:.0f}")
        pdfrw_set_field(fields, BASE_FIELDS["extra_fuel"], f"{remaining_fuel_l:.1f} L")
        pdfrw_set_field(fields, BASE_FIELDS["extra_reason"], f"limited by {limit_label}")

        for role, rnames in ROLE_FIELDS.items():
            rr = role_to_row.get(role)
            if not rr: continue
            def _set(cands, val): pdfrw_set_field(fields, cands, val)
            _set(rnames["airfield"], rr["icao"])
            _set(rnames["qfu"], f"{rr['qfu']:.0f} deg")
            _set(rnames["elev"], f"{rr['elev_ft']:.0f}")
            _set(rnames["qnh"], f"{rr['qnh']:.1f}")
            _set(rnames["temp"], f"{rr['temp']:.1f}")
            _set(rnames["wind"], f"{rr['hw_comp']:.0f} kt")          # head/tail comp
            _set(rnames["pa"], f"{rr['pa_ft']:.0f}")
            _set(rnames["da"], f"{rr['da_ft']:.0f}")
            _set(rnames["toda_lda"], f"{int(rr['toda_av'])}/{int(rr['lda_av'])}")
            _set(rnames["todr"], f"{rr['to_50']:.0f}")
            _set(rnames["ldr"], f"{rr['ldg_50']:.0f}")
            try:
                roc_val = roc_interp(rr['pa_ft'], rr['temp'], total_weight)
                _set(rnames["roc"], f"{roc_val:.0f}")
            except Exception:
                pass

        # write
        Wr_pdfrw().write(str(out_main_path), reader)

        # Merge with narrative calc page
        base = Rd_pypdf(str(out_main_path))
        calc_doc = Rd_pypdf(str(calc_pdf_path))
        merger = Wr_pypdf()
        for p in base.pages: merger.add_page(p)
        for p in calc_doc.pages: merger.add_page(p)
        with open(out_main_path, "wb") as f: merger.write(f)

    else:
        # Fallback: just merge template + calc page; fill in PyPDF pass below
        base_r = Rd_pypdf(str(PDF_TEMPLATE))
        calc_r = Rd_pypdf(str(calc_pdf_path))
        merger = Wr_pypdf()
        for p in base_r.pages: merger.add_page(p)
        for p in calc_r.pages: merger.add_page(p)
        with open(out_main_path, "wb") as f: merger.write(f)

    # 2b) PyPDF pass to FORCE appearances and fill all candidate names (robust)
    rd = Rd_pypdf(str(out_main_path))
    wr = Wr_pypdf()

    values = {}
    def add_vals(names, value):
        if not isinstance(names, (list, tuple)):
            names = [names]
        for n in names:
            values[str(n)] = str(value)

    # base
    add_vals(BASE_FIELDS["date"], date_str)
    add_vals(BASE_FIELDS["reg"], reg)
    add_vals(BASE_FIELDS["total_w"], f"{total_weight:.1f}")
    add_vals(BASE_FIELDS["cg"], f"{cg:.3f}")
    add_vals(BASE_FIELDS["mtow"], f"{AC['max_takeoff_weight']:.0f}")
    add_vals(BASE_FIELDS["extra_fuel"], f"{remaining_fuel_l:.1f} L")
    add_vals(BASE_FIELDS["extra_reason"], f"limited by {limit_label}")

    # per role
    for role, rnames in ROLE_FIELDS.items():
        rr = role_to_row.get(role)
        if not rr: continue
        add_vals(rnames["airfield"], rr["icao"])
        add_vals(rnames["qfu"], f"{rr['qfu']:.0f} deg")
        add_vals(rnames["elev"], f"{rr['elev_ft']:.0f}")
        add_vals(rnames["qnh"], f"{rr['qnh']:.1f}")
        add_vals(rnames["temp"], f"{rr['temp']:.1f}")
        add_vals(rnames["wind"], f"{rr['hw_comp']:.0f} kt")
        add_vals(rnames["pa"], f"{rr['pa_ft']:.0f}")
        add_vals(rnames["da"], f"{rr['da_ft']:.0f}")
        add_vals(rnames["toda_lda"], f"{int(rr['toda_av'])}/{int(rr['lda_av'])}")
        add_vals(rnames["todr"], f"{rr['to_50']:.0f}")
        add_vals(rnames["ldr"], f"{rr['ldg_50']:.0f}")
        try:
            roc_val = roc_interp(rr['pa_ft'], rr['temp'], total_weight)
            add_vals(rnames["roc"], f"{roc_val:.0f}")
        except Exception:
            pass

    # Make viewer render appearances
    if "/AcroForm" in rd.trailer["/Root"]:
        rd.trailer["/Root"]["/AcroForm"].update({"/NeedAppearances": True})

    for page in rd.pages:
        wr.add_page(page)
        wr.update_page_form_field_values(page, values)

    with open(out_main_path, "wb") as f:
        wr.write(f)

    st.success("PDF generated successfully!")
    with open(out_main_path, "rb") as f:
        st.download_button("Download PDF", f, file_name=out_main_path.name, mime="application/pdf")


