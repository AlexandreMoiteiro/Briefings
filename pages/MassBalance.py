# Streamlit app – Tecnam P2008 (M&B + Performance) – EN, no emails
# Works on GitHub + Streamlit Cloud
# Requirements (requirements.txt):
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

# PDF tools
from pdfrw import PdfReader as Rd_pdfrw, PdfWriter as Wr_pdfrw, PdfDict
from pypdf import PdfReader as Rd_pypdf, PdfWriter as Wr_pypdf
from fpdf import FPDF

# =========================
# Helpers & style
# =========================

def ascii_safe(text):
    if not isinstance(text, str):
        return str(text)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

st.set_page_config(
    page_title="Tecnam P2008 – Mass & Balance & Performance (EN)",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1120px !important; }
      .mb-header{font-size:1.3rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7ec;padding-bottom:6px;margin-bottom:8px}
      .section-title{font-weight:700;margin:14px 0 6px 0}
      .mb-summary-row{display:flex;justify-content:space-between;margin:4px 0}
      .ok{color:#1d8533}.warn{color:#d8aa22}.bad{color:#c21c1c}
      .mb-table{border-collapse:collapse;width:100%;font-size:.95rem}
      .mb-table th{border-bottom:2px solid #cbd0d6;text-align:left}
      .mb-table td{padding:3px 6px;border-bottom:1px dashed #e5e7ec}
    </style>
    """,
    unsafe_allow_html=True,
)

APP_DIR = Path(__file__).parent
PDF_TEMPLATE = APP_DIR / "TecnamP2008MBPerformanceSheet_MissionX.pdf"

# =========================
# Fixed aircraft data (Tecnam P2008)
# =========================
AC = {
    "name": "Tecnam P2008 JC",
    "fuel_arm": 2.209,
    "pilot_arm": 1.800,
    "baggage_arm": 2.417,
    "max_takeoff_weight": 650.0,
    "max_fuel_volume": 124.0,  # liters
    "max_passenger_weight": 230.0,
    "max_baggage_weight": 20.0,
    "cg_limits": (1.841, 1.978),
    "fuel_density": 0.72,  # kg/L
    "units": {"weight": "kg", "arm": "m"},
}

# =========================
# Aerodrome defaults (practical values, can edit in UI)
# =========================
AERODROMES_DEFAULT = [
    {"role":"Departure","icao":"LPSO","elev_ft":390.0,"qfu":30.0,"toda":1800.0,"lda":1800.0,
     "paved":True,"slope_pc":0.0,"qnh":1013.0,"temp":15.0,"wind_dir":0.0,"wind_kt":0.0},
    {"role":"Arrival","icao":"LPEV","elev_ft":807.0,"qfu":10.0,"toda":1300.0,"lda":1245.0,
     "paved":True,"slope_pc":0.0,"qnh":1013.0,"temp":15.0,"wind_dir":0.0,"wind_kt":0.0},
    {"role":"Alternate","icao":"LPCB","elev_ft":1251.0,"qfu":160.0,"toda":1520.0,"lda":1460.0,
     "paved":True,"slope_pc":0.0,"qnh":1013.0,"temp":15.0,"wind_dir":0.0,"wind_kt":0.0},
]

# =========================
# Performance tables (AFM extracts) – distances in m; ROC ft/min
# =========================
TAKEOFF = {
    0:     {"GR":{-25:144, 0:182, 25:224, 50:272, "ISA":207}, "50ft":{-25:304,0:379,25:463,50:557,"ISA":428}},
    1000:  {"GR":{-25:157, 0:198, 25:245, 50:297, "ISA":222}, "50ft":{-25:330,0:412,25:503,50:605,"ISA":458}},
    2000:  {"GR":{-25:172, 0:216, 25:267, 50:324, "ISA":238}, "50ft":{-25:359,0:448,25:547,50:658,"ISA":490}},
    3000:  {"GR":{-25:188, 0:236, 25:292, 50:354, "ISA":256}, "50ft":{-25:391,0:487,25:595,50:717,"ISA":525}},
    4000:  {"GR":{-25:205, 0:258, 25:319, 50:387, "ISA":275}, "50ft":{-25:425,0:530,25:648,50:780,"ISA":562}},
    5000:  {"GR":{-25:224, 0:283, 25:349, 50:423, "ISA":295}, "50ft":{-25:463,0:578,25:706,50:850,"ISA":603}},
    6000:  {"GR":{-25:246, 0:309, 25:381, 50:463, "ISA":318}, "50ft":{-25:505,0:630,25:770,50:927,"ISA":646}},
    7000:  {"GR":{-25:269, 0:339, 25:418, 50:507, "ISA":342}, "50ft":{-25:551,0:687,25:840,50:1011,"ISA":693}},
    8000:  {"GR":{-25:295, 0:371, 25:458, 50:555, "ISA":368}, "50ft":{-25:601,0:750,25:917,50:1104,"ISA":744}},
    9000:  {"GR":{-25:323, 0:407, 25:502, 50:609, "ISA":397}, "50ft":{-25:657,0:819,25:1002,50:1205,"ISA":800}},
    10000: {"GR":{-25:354, 0:446, 25:551, 50:668, "ISA":428}, "50ft":{-25:718,0:895,25:1095,50:1318,"ISA":859}},
}

LANDING = {
    0:     {"GR":{-25:149,0:164,25:179,50:194,"ISA":173}, "50ft":{-25:358,0:373,25:388,50:403,"ISA":382}},
    1000:  {"GR":{-25:154,0:170,25:186,50:201,"ISA":178}, "50ft":{-25:363,0:379,25:395,50:410,"ISA":387}},
    2000:  {"GR":{-25:160,0:176,25:192,50:209,"ISA":183}, "50ft":{-25:369,0:385,25:401,50:418,"ISA":392}},
    3000:  {"GR":{-25:166,0:183,25:200,50:216,"ISA":189}, "50ft":{-25:375,0:392,25:409,50:425,"ISA":398}},
    4000:  {"GR":{-25:172,0:190,25:207,50:225,"ISA":195}, "50ft":{-25:381,0:399,25:416,50:434,"ISA":404}},
    5000:  {"GR":{-25:179,0:197,25:215,50:233,"ISA":201}, "50ft":{-25:388,0:406,25:424,50:442,"ISA":410}},
    6000:  {"GR":{-25:186,0:205,25:223,50:242,"ISA":207}, "50ft":{-25:395,0:414,25:432,50:451,"ISA":416}},
    7000:  {"GR":{-25:193,0:212,25:232,50:251,"ISA":213}, "50ft":{-25:402,0:421,25:441,50:460,"ISA":422}},
    8000:  {"GR":{-25:200,0:221,25:241,50:261,"ISA":220}, "50ft":{-25:410,0:430,25:450,50:470,"ISA":429}},
    9000:  {"GR":{-25:208,0:229,25:250,50:271,"ISA":227}, "50ft":{-25:417,0:438,25:459,50:480,"ISA":436}},
    10000: {"GR":{-25:217,0:238,25:260,50:282,"ISA":234}, "50ft":{-25:426,0:447,25:469,50:491,"ISA":443}},
}

ROC = {
    650:{0:{-25:951,0:805,25:675,50:557,"ISA":725},2000:{-25:840,0:696,25:568,50:453,"ISA":638},4000:{-25:729,0:588,25:462,50:349,"ISA":551},6000:{-25:619,0:480,25:357,50:245,"ISA":464},8000:{-25:509,0:373,25:251,50:142,"ISA":377},10000:{-25:399,0:266,25:146,50:39,"ISA":290},12000:{-25:290,0:159,25:42,50:-64,"ISA":204},14000:{-25:181,0:53,25:-63,50:-166,"ISA":117}},
    600:{0:{-25:1067,0:913,25:776,50:652,"ISA":829},2000:{-25:950,0:799,25:664,50:542,"ISA":737},4000:{-25:833,0:685,25:552,50:433,"ISA":646},6000:{-25:717,0:571,25:441,50:324,"ISA":555},8000:{-25:602,0:458,25:330,50:215,"ISA":463},10000:{-25:486,0:345,25:220,50:106,"ISA":372},12000:{-25:371,0:233,25:110,50:-2,"ISA":280},14000:{-25:257,0:121,25:0,50:-109,"ISA":189}},
    550:{0:{-25:1201,0:1038,25:892,50:760,"ISA":948},2000:{-25:1077,0:916,25:773,50:644,"ISA":851},4000:{-25:953,0:795,25:654,50:527,"ISA":754},6000:{-25:830,0:675,25:536,50:411,"ISA":657},8000:{-25:707,0:555,25:419,50:296,"ISA":560},10000:{-25:584,0:435,25:301,50:181,"ISA":462},12000:{-25:462,0:315,25:184,50:66,"ISA":365},14000:{-25:341,0:196,25:68,50:-48,"ISA":268}},
}

VY = {650:{0:70,2000:69,4000:68,6000:67,8000:65,10000:64,12000:63,14000:62},600:{0:70,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:62},550:{0:69,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:61}}

# =========================
# Interpolation & corrections
# =========================

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def interp1(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def bilinear(pa, temp, table, key):
    pas = sorted(table.keys())
    pa = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa])
    p1 = min([p for p in pas if p >= pa])
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
    return interp1(pa, p0, p1, v0, v1), (p0,p1,t0,t1,v00,v01,v10,v11)

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
        return interp1(pa_c, p0, p1, v0, v1), (p0,p1,t0,t1,v00,v01,v10,v11)
    if w <= 600:
        v_lo, d_lo = roc_for_w(550)
        v_hi, d_hi = roc_for_w(600)
        return interp1(w, 550, 600, v_lo, v_hi), ("550→600", d_lo, d_hi)
    else:
        v_lo, d_lo = roc_for_w(600)
        v_hi, d_hi = roc_for_w(650)
        return interp1(w, 600, 650, v_lo, v_hi), ("600→650", d_lo, d_hi)

def wind_head_component(runway_qfu_deg, wind_dir_deg, wind_speed):
    if runway_qfu_deg is None or wind_dir_deg is None:
        return 0.0
    diff = radians((wind_dir_deg - runway_qfu_deg) % 360)
    return wind_speed * cos(diff)

def to_corrections_takeoff(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = ground_roll
    if headwind_kt >= 0:
        gr = gr - 5.0 * headwind_kt
    else:
        gr = gr + 15.0 * abs(headwind_kt)
    if paved:
        gr *= 0.9
    if slope_pc:
        gr *= (1.0 + 0.07 * (slope_pc/1.0))
    return max(gr, 0.0)

def ldg_corrections(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = ground_roll
    if headwind_kt >= 0:
        gr = gr - 4.0 * headwind_kt
    else:
        gr = gr + 13.0 * abs(headwind_kt)
    if paved:
        gr *= 0.9
    if slope_pc:
        gr *= (1.0 - 0.03 * (slope_pc/1.0))
    return max(gr, 0.0)

# =========================
# UI – inputs
# =========================

st.markdown('<div class="mb-header">Tecnam P2008 – Mass & Balance & Performance</div>', unsafe_allow_html=True)

left, mid, right = st.columns([0.42,0.02,0.56], gap="large")

with left:
    st.markdown("### Weight & balance (inputs)")
    ew = st.number_input("Empty weight (kg)", min_value=0.0, value=0.0, step=1.0)
    ew_moment = st.number_input("Empty weight moment (kg·m)", min_value=0.0, value=0.0, step=0.1)
    ew_arm = (ew_moment/ew) if ew>0 else 0.0
    student = st.number_input("Student weight (kg)", min_value=0.0, value=0.0, step=1.0)
    instructor = st.number_input("Instructor weight (kg)", min_value=0.0, value=0.0, step=1.0)
    baggage = st.number_input("Baggage (kg)", min_value=0.0, value=0.0, step=1.0)
    fuel_l = st.number_input("Fuel (L)", min_value=0.0, value=0.0, step=1.0)

    pilot = student + instructor
    fuel_wt = fuel_l * AC['fuel_density']
    m_empty = ew_moment
    m_pilot = pilot * AC['pilot_arm']
    m_bag = baggage * AC['baggage_arm']
    m_fuel = fuel_wt * AC['fuel_arm']
    total_weight = ew + pilot + baggage + fuel_wt
    total_moment = m_empty + m_pilot + m_bag + m_fuel
    cg = (total_moment/total_weight) if total_weight>0 else 0.0

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
    st.markdown(f"<div class='mb-summary-row'><div>Total moment</div><div><b>{total_moment:.2f} kg·m</b></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>CG</div><div class='{cg_color_val(cg, AC['cg_limits'])}'><b>{cg:.3f} m</b></div></div>", unsafe_allow_html=True)

with right:
    st.markdown("### Aerodromes & performance")
    if 'aerodromes' not in st.session_state:
        st.session_state.aerodromes = AERODROMES_DEFAULT

    perf_rows = []
    for i, a in enumerate(st.session_state.aerodromes):
        with st.expander(f"{a['role']} – {a['icao']}", expanded=(i==0)):
            icao = st.text_input("ICAO", value=a['icao'], key=f"icao_{i}")
            qfu = st.number_input("RWY QFU (deg, heading)", min_value=0.0, max_value=360.0, value=float(a['qfu']), step=1.0, key=f"qfu_{i}")
            elev = st.number_input("Elevation (ft)", value=float(a['elev_ft']), step=1.0, key=f"elev_{i}")
            qnh = st.number_input("QNH (hPa)", min_value=900.0, max_value=1050.0, value=float(a['qnh']), step=0.1, key=f"qnh_{i}")
            temp = st.number_input("Temperature (°C)", min_value=-40.0, max_value=60.0, value=float(a['temp']), step=0.1, key=f"temp_{i}")
            wind_dir = st.number_input("Wind direction (deg FROM)", min_value=0.0, max_value=360.0, value=float(a['wind_dir']), step=1.0, key=f"wdir_{i}")
            wind_kt = st.number_input("Wind speed (kt)", min_value=0.0, value=float(a['wind_kt']), step=1.0, key=f"wspd_{i}")
            paved = st.checkbox("Paved runway", value=bool(a['paved']), key=f"paved_{i}")
            slope_pc = st.number_input("Runway slope (%) (uphill positive)", value=float(a['slope_pc']), step=0.1, key=f"slope_{i}")
            toda_av = st.number_input("TODA available (m)", min_value=0.0, value=float(a['toda']), step=1.0, key=f"toda_{i}")
            lda_av = st.number_input("LDA available (m)", min_value=0.0, value=float(a['lda']), step=1.0, key=f"lda_{i}")

            st.session_state.aerodromes[i].update({"icao":icao,"qfu":qfu,"elev_ft":elev,"qnh":qnh,
                                                   "temp":temp,"wind_dir":wind_dir,"wind_kt":wind_kt,
                                                   "paved":paved,"slope_pc":slope_pc,"toda":toda_av,
                                                   "lda":lda_av})

            # PA/DA
            pa_ft = elev + (1013.25 - qnh) * 27
            isa_temp = 15 - 2*(pa_ft/1000)
            da_ft = pa_ft + (120*(temp - isa_temp))

            # Interpolation (also keeping details for the calculations PDF)
            to_gr, to_dbg = bilinear(pa_ft, temp, TAKEOFF, 'GR')
            to_50, to50_dbg = bilinear(pa_ft, temp, TAKEOFF, '50ft')
            ldg_gr, ldg_dbg = bilinear(pa_ft, temp, LANDING, 'GR')
            ldg_50, l50_dbg = bilinear(pa_ft, temp, LANDING, '50ft')

            hw = wind_head_component(qfu, wind_dir, wind_kt)
            to_gr_corr = to_corrections_takeoff(to_gr, hw, paved=paved, slope_pc=slope_pc)
            ldg_gr_corr = ldg_corrections(ldg_gr, hw, paved=paved, slope_pc=slope_pc)

            perf_rows.append({
                'role': a['role'], 'icao': icao, 'qfu': qfu,
                'pa_ft': pa_ft, 'da_ft': da_ft, 'isa_temp': isa_temp,
                'to_gr': to_gr_corr, 'to_50': to_50,
                'ldg_gr': ldg_gr_corr, 'ldg_50': ldg_50,
                'toda_av': toda_av, 'lda_av': lda_av,
                'hw_comp': hw,
                'dbg': { 'to_gr': to_dbg, 'to_50': to50_dbg, 'ldg_gr': ldg_dbg, 'ldg_50': l50_dbg }
            })

    # Summary table
    def fmt(v):
        return f"{v:.0f}" if isinstance(v, (int,float)) else str(v)

    st.markdown("#### Performance summary")
    st.markdown(
        "<table class='mb-table'><tr><th>Leg/Aerodrome</th><th>QFU</th><th>PA ft</th><th>DA ft</th><th>TO GR (m)*</th><th>TODR 50ft (m)</th><th>LND GR (m)*</th><th>LDR 50ft (m)</th><th>TODA Avail</th><th>LDA Avail</th></tr>" +
        "".join([
            f"<tr><td>{r['role']} {r['icao']}</td><td>{fmt(r['qfu'])}</td><td>{fmt(r['pa_ft'])}</td><td>{fmt(r['da_ft'])}</td><td>{fmt(r['to_gr'])}</td><td>{fmt(r['to_50'])}</td><td>{fmt(r['ldg_gr'])}</td><td>{fmt(r['ldg_50'])}</td><td>{fmt(r['toda_av'])}</td><td>{fmt(r['lda_av'])}</td></tr>"
            for r in perf_rows
        ]) + "</table>",
        unsafe_allow_html=True
    )

# =========================
# Fuel planning (20 L/h default)
# =========================
RATE_LPH = 20.0
st.markdown("### Fuel planning (assume 20 L/h by default)")

c1, c2, c3, c4 = st.columns([0.25,0.25,0.25,0.25])

def time_to_liters(h=0, m=0, rate=RATE_LPH):
    return rate * (h + m/60.0)

with c1:
    su_min = st.number_input("Start-up & taxi (min)", min_value=0, value=15, step=1)
    climb_min = st.number_input("Climb (min)", min_value=0, value=15, step=1)
with c2:
    enrt_h = st.number_input("Enroute (h)", min_value=0, value=2, step=1)
    enrt_min = st.number_input("Enroute (min)", min_value=0, value=15, step=1)
with c3:
    desc_min = st.number_input("Descent (min)", min_value=0, value=15, step=1)
    alt_min = st.number_input("Alternate (min)", min_value=0, value=60, step=5)
with c4:
    reserve_min = st.number_input("Reserve (min)", min_value=0, value=45, step=5)
    extra_min = st.number_input("Extra (min)", min_value=0, value=0, step=5)

trip_l = time_to_liters(0, climb_min) + time_to_liters(enrt_h, enrt_min) + time_to_liters(0, desc_min)
cont_l = 0.05*trip_l
req_ramp = time_to_liters(0, su_min) + trip_l + cont_l + time_to_liters(0, alt_min) + time_to_liters(0, reserve_min)
extra_l = time_to_liters(0, extra_min)

total_ramp = req_ramp + extra_l

st.markdown(f"- **Trip fuel**: {trip_l:.1f} L  ")
st.markdown(f"- **Contingency 5%**: {cont_l:.1f} L  ")
st.markdown(f"- **Required ramp fuel** (1+5+6+7+8): **{req_ramp:.1f} L**  ")
st.markdown(f"- **Extra**: {extra_l:.1f} L  ")
st.markdown(f"- **Total ramp fuel**: **{total_ramp:.1f} L**")

# =========================
# PDF – Fill template + attach calculations page
# =========================

st.markdown("### PDF – M&B and Performance Data Sheet")
reg = st.text_input("Aircraft registration", value="CS-XXX")
mission = st.text_input("Mission #", value="001")

utc_today = datetime.datetime.now(pytz.UTC)
date_str = st.text_input("Date (DD/MM/YYYY)", value=utc_today.strftime("%d/%m/%Y"))

if st.button("Generate filled PDF"):
    if not PDF_TEMPLATE.exists():
        st.error(f"Template not found: {PDF_TEMPLATE}")
        st.stop()

    # Build detailed calculations page (English, no duplication)
    calc_pdf_path = APP_DIR / f"_calc_mission_{mission}.pdf"
    calc = FPDF()
    calc.set_auto_page_break(auto=True, margin=12)
    calc.add_page()
    calc.set_font("Arial", "B", 14)
    calc.cell(0, 8, ascii_safe("Tecnam P2008 – Calculations (summary)"), ln=True)

    # W&B
    calc.set_font("Arial", "B", 12)
    calc.cell(0, 7, ascii_safe("Weight & balance"), ln=True)
    calc.set_font("Arial", size=10)
    calc.cell(0, 6, ascii_safe(f"Inputs: EW {ew:.1f} kg | EW moment {ew_moment:.2f} kg·m | Student {student:.1f} kg | Instructor {instructor:.1f} kg | Baggage {baggage:.1f} kg | Fuel {fuel_l:.1f} L (dens 0.72)"), ln=True)
    calc.cell(0, 6, ascii_safe(f"Moments: empty {m_empty:.2f} | pilot {m_pilot:.2f} | baggage {m_bag:.2f} | fuel {m_fuel:.2f} (kg·m)"), ln=True)
    calc.cell(0, 6, ascii_safe(f"Totals: weight {total_weight:.1f} kg | moment {total_moment:.2f} kg·m | CG {cg:.3f} m | limits {AC['cg_limits'][0]:.3f} – {AC['cg_limits'][1]:.3f} m"), ln=True)
    calc.cell(0, 6, ascii_safe(f"Extra fuel possible: {remaining_fuel_l:.1f} L (limited by {limit_label})"), ln=True)

    # Performance details per aerodrome, showing PA/DA and interpolation brackets
    calc.ln(2)
    calc.set_font("Arial", "B", 12)
    calc.cell(0, 7, ascii_safe("Performance – interpolation details"), ln=True)
    calc.set_font("Arial", size=10)
    for r in perf_rows:
        calc.set_font("Arial", "B", 10)
        calc.cell(0, 6, ascii_safe(f"{r['role']} – {r['icao']} (QFU {r['qfu']:.0f}°)"), ln=True)
        calc.set_font("Arial", size=10)
        calc.cell(0, 5, ascii_safe(f"PA {r['pa_ft']:.0f} ft | ISA temp {r['isa_temp']:.1f} °C | OAT {st.session_state.aerodromes[0]['temp']:.1f} °C | DA {r['da_ft']:.0f} ft"), ln=True)
        # Interpolation steps
        def dbgline(name, dbg):
            p0,p1,t0,t1,v00,v01,v10,v11 = dbg
            return f"{name}: PA[{p0},{p1}] & Temp[{t0},{t1}] ⇒ values ({v00},{v01}; {v10},{v11})"
        calc.cell(0, 5, ascii_safe(dbgline("TO GR bilinear", r['dbg']['to_gr'])), ln=True)
        calc.cell(0, 5, ascii_safe(dbgline("TO 50ft bilinear", r['dbg']['to_50'])), ln=True)
        calc.cell(0, 5, ascii_safe(dbgline("LND GR bilinear", r['dbg']['ldg_gr'])), ln=True)
        calc.cell(0, 5, ascii_safe(dbgline("LND 50ft bilinear", r['dbg']['ldg_50'])), ln=True)
        calc.cell(0, 5, ascii_safe(f"Wind head/tail component: {r['hw_comp']:.0f} kt | Paved: {'yes' if st.session_state.aerodromes[0]['paved'] else 'no'} | Slope: {st.session_state.aerodromes[0]['slope_pc']:.1f}%"), ln=True)
        calc.cell(0, 5, ascii_safe(f"Results: TO GR* {r['to_gr']:.0f} m | TODR 50ft {r['to_50']:.0f} m | LND GR* {r['ldg_gr']:.0f} m | LDR 50ft {r['ldg_50']:.0f} m | TODA Av {r['toda_av']:.0f} | LDA Av {r['lda_av']:.0f}"), ln=True)
        calc.ln(1)

    # Fuel planning
    calc.ln(2)
    calc.set_font("Arial", "B", 12)
    calc.cell(0, 7, ascii_safe("Fuel planning (20 L/h)"), ln=True)
    calc.set_font("Arial", size=10)
    calc.cell(0, 5, ascii_safe(f"Trip {trip_l:.1f} L | Cont 5% {cont_l:.1f} L | Required ramp {req_ramp:.1f} L | Extra {extra_l:.1f} L | Total ramp {total_ramp:.1f} L"), ln=True)

    calc.output(str(calc_pdf_path))

    # Fill the form (page 1/2) with pdfrw if possible to keep colors; otherwise pypdf
    def load_pdf_any(path: Path):
        try:
            return "pdfrw", Rd_pdfrw(str(path))
        except Exception:
            try:
                return "pypdf", Rd_pypdf(str(path))
            except Exception as e:
                raise RuntimeError(f"Could not read the PDF: {e}")

    engine, reader = load_pdf_any(PDF_TEMPLATE)

    FIELD_MAP = {
        "Textbox19": reg,       # Registration
        "Textbox18": date_str,  # Date
    }

    out_main_path = APP_DIR / f"MB_Performance_Mission_{mission}.pdf"

    def pdfrw_set_field(fields, name, value, color_rgb=None):
        for f in fields:
            if f.get('/T') and f['/T'][1:-1] == name:
                f.update(PdfDict(V=str(value)))
                f.update(PdfDict(AP=None))
                if color_rgb:
                    r, g, b = color_rgb
                    f.update(PdfDict(DA=f"{r/255:.3f} {g/255:.3f} {b/255:.3f} rg /Helv 10 Tf"))
                break

    if engine == "pdfrw" and hasattr(reader, 'Root') and '/AcroForm' in reader.Root:
        fields = reader.Root.AcroForm.Fields
        for k, v in FIELD_MAP.items():
            pdfrw_set_field(fields, k, v)
        # Weight & CG colors
        wt_color = (30,150,30) if total_weight <= AC['max_takeoff_weight'] else (200,0,0)
        lo, hi = AC['cg_limits']
        if cg < lo or cg > hi:
            cg_color = (200,0,0)
        else:
            margin = 0.05*(hi-lo)
            cg_color = (200,150,30) if (cg<lo+margin or cg>hi-margin) else (30,150,30)
        pdfrw_set_field(fields, "Textbox14", f"{total_weight:.1f}", wt_color)
        pdfrw_set_field(fields, "Textbox16", f"{cg:.3f}", cg_color)
        pdfrw_set_field(fields, "Textbox17", f"{AC['max_takeoff_weight']:.0f}")
        # Departure examples (extend as needed)
        if perf_rows:
            dep = perf_rows[0]
            pdfrw_set_field(fields, "Textbox22", dep['icao'])
            pdfrw_set_field(fields, "Textbox50", f"{dep['pa_ft']:.0f}")
            pdfrw_set_field(fields, "Textbox49", f"{dep['da_ft']:.0f}")
            pdfrw_set_field(fields, "Textbox47", f"{int(dep['toda_av'])}/{int(dep['lda_av'])}")
            pdfrw_set_field(fields, "Textbox45", f"{dep['to_50']:.0f}")
            pdfrw_set_field(fields, "Textbox41", f"{dep['ldg_50']:.0f}")
            # Extra fuel and constraint (if you have free text fields, map them here)

        writer = Wr_pdfrw()
        writer.write(str(out_main_path), reader)

        # Merge with calculations page
        base = Rd_pypdf(str(out_main_path))
        calc_doc = Rd_pypdf(str(calc_pdf_path))
        merger = Wr_pypdf()
        for p in base.pages: merger.add_page(p)
        for p in calc_doc.pages: merger.add_page(p)
        with open(out_main_path, "wb") as f:
            merger.write(f)

    else:
        base_r = Rd_pypdf(str(PDF_TEMPLATE))
        merger = Wr_pypdf()
        for p in base_r.pages: merger.add_page(p)
        if "/AcroForm" in base_r.trailer["/Root"]:
            merger._root_object.update({"/AcroForm": base_r.trailer["/Root"]["/AcroForm"]})
            merger._root_object["/AcroForm"].update({"/NeedAppearances": True})
        merger.update_page_form_field_values(base_r.pages[0], FIELD_MAP)
        calc_doc = Rd_pypdf(str(calc_pdf_path))
        for p in calc_doc.pages: merger.add_page(p)
        with open(out_main_path, "wb") as f:
            merger.write(f)

    st.success("PDF generated successfully!")
    with open(out_main_path, 'rb') as f:
        st.download_button("Download PDF", f, file_name=out_main_path.name, mime="application/pdf")



