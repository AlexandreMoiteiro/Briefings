# MassBalance.py
import streamlit as st
import datetime
from pathlib import Path
import pytz
import unicodedata
from math import cos, radians
from io import BytesIO

# PDF libs
from pdfrw import PdfReader as Rd_pdfrw, PdfWriter as Wr_pdfrw, PdfDict
from pypdf import PdfReader as Rd_pypdf, PdfWriter as Wr_pypdf
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# =========================
# Helpers & Style
# =========================
def ascii_safe(text):
    if not isinstance(text, str):
        return str(text)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

st.set_page_config(page_title="Tecnam P2008 – Mass & Balance & Performance",
                   layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
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
""", unsafe_allow_html=True)

# =========================
# Fixed Aircraft Data
# =========================
ac = {
    "name": "Tecnam P2008 JC",
    "fuel_arm": 2.209,
    "pilot_arm": 1.800,
    "baggage_arm": 2.417,
    "max_takeoff_weight": 650.0,
    "max_fuel_volume": 124.0,   # L
    "max_passenger_weight": 230.0,
    "max_baggage_weight": 20.0,
    "cg_limits": (1.841, 1.978),
    "fuel_density": 0.72,       # kg/L
    "units": {"weight": "kg", "arm": "m"},
}

# =========================
# Performance Tables (AFM) – m/ft·min
# =========================
TAKEOFF = {
    0:     {"GR":{-25:144,0:182,25:224,50:272,"ISA":207}, "50ft":{-25:304,0:379,25:463,50:557,"ISA":428}},
    1000:  {"GR":{-25:157,0:198,25:245,50:297,"ISA":222}, "50ft":{-25:330,0:412,25:503,50:605,"ISA":458}},
    2000:  {"GR":{-25:172,0:216,25:267,50:324,"ISA":238}, "50ft":{-25:359,0:448,25:547,50:658,"ISA":490}},
    3000:  {"GR":{-25:188,0:236,25:292,50:354,"ISA":256}, "50ft":{-25:391,0:487,25:595,50:717,"ISA":525}},
    4000:  {"GR":{-25:205,0:258,25:319,50:387,"ISA":275}, "50ft":{-25:425,0:530,25:648,50:780,"ISA":562}},
    5000:  {"GR":{-25:224,0:283,25:349,50:423,"ISA":295}, "50ft":{-25:463,0:578,25:706,50:850,"ISA":603}},
    6000:  {"GR":{-25:246,0:309,25:381,50:463,"ISA":318}, "50ft":{-25:505,0:630,25:770,50:927,"ISA":646}},
    7000:  {"GR":{-25:269,0:339,25:418,50:507,"ISA":342}, "50ft":{-25:551,0:687,25:840,50:1011,"ISA":693}},
    8000:  {"GR":{-25:295,0:371,25:458,50:555,"ISA":368}, "50ft":{-25:601,0:750,25:917,50:1104,"ISA":744}},
    9000:  {"GR":{-25:323,0:407,25:502,50:609,"ISA":397}, "50ft":{-25:657,0:819,25:1002,50:1205,"ISA":800}},
    10000: {"GR":{-25:354,0:446,25:551,50:668,"ISA":428}, "50ft":{-25:718,0:895,25:1095,50:1318,"ISA":859}},
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
    650:{0:{-25:951,0:805,25:675,50:557,"ISA":725},2000:{-25:840,0:696,25:568,50:453,"ISA":638},
         4000:{-25:729,0:588,25:462,50:349,"ISA":551},6000:{-25:619,0:480,25:357,50:245,"ISA":464},
         8000:{-25:509,0:373,25:251,50:142,"ISA":377},10000:{-25:399,0:266,25:146,50:39,"ISA":290},
         12000:{-25:290,0:159,25:42,50:-64,"ISA":204},14000:{-25:181,0:53,25:-63,50:-166,"ISA":117}},
    600:{0:{-25:1067,0:913,25:776,50:652,"ISA":829},2000:{-25:950,0:799,25:664,50:542,"ISA":737},
         4000:{-25:833,0:685,25:552,50:433,"ISA":646},6000:{-25:717,0:571,25:441,50:324,"ISA":555},
         8000:{-25:602,0:458,25:330,50:215,"ISA":463},10000:{-25:486,0:345,25:220,50:106,"ISA":372},
         12000:{-25:371,0:233,25:110,50:-2,"ISA":280},14000:{-25:257,0:121,25:0,50:-109,"ISA":189}},
    550:{0:{-25:1201,0:1038,25:892,50:760,"ISA":948},2000:{-25:1077,0:916,25:773,50:644,"ISA":851},
         4000:{-25:953,0:795,25:654,50:527,"ISA":754},6000:{-25:830,0:675,25:536,50:411,"ISA":657},
         8000:{-25:707,0:555,25:419,50:296,"ISA":560},10000:{-25:584,0:435,25:301,50:181,"ISA":462},
         12000:{-25:462,0:315,25:184,50:66,"ISA":365},14000:{-25:341,0:196,25:68,50:-48,"ISA":268}},
}
VY = {650:{0:70,2000:69,4000:68,6000:67,8000:65,10000:64,12000:63,14000:62},
      600:{0:70,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:62},
      550:{0:69,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:61}}

# =========================
# Interpolation helpers
# =========================
def clamp(v, lo, hi): return max(lo, min(hi, v))
def interp1(x, x0, x1, y0, y1):
    if x1 == x0: return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)
def bilinear(pa, temp, table, key):
    pas = sorted(table.keys()); pa = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa]); p1 = min([p for p in pas if p >= pa])
    temps = [-25, 0, 25, 50]; t = clamp(temp, temps[0], temps[-1])
    if   t <= 0:  t0,t1 = -25,0
    elif t <= 25: t0,t1 = 0,25
    else:         t0,t1 = 25,50
    v00 = table[p0][key][t0]; v01 = table[p0][key][t1]
    v10 = table[p1][key][t0]; v11 = table[p1][key][t1]
    v0 = interp1(t, t0, t1, v00, v01); v1 = interp1(t, t0, t1, v10, v11)
    return interp1(pa, p0, p1, v0, v1)

def roc_interp(pa, temp, weight):
    w = clamp(weight, 550.0, 650.0)
    def roc_for(wkey):
        tab = ROC[int(wkey)]; pas = sorted(tab.keys())
        pa_c = clamp(pa, pas[0], pas[-1])
        p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
        temps = [-25,0,25,50]; t = clamp(temp, temps[0], temps[-1])
        if   t <= 0:  t0,t1 = -25,0
        elif t <= 25: t0,t1 = 0,25
        else:         t0,t1 = 25,50
        v00 = tab[p0][t0]; v01 = tab[p0][t1]; v10 = tab[p1][t0]; v11 = tab[p1][t1]
        return interp1(pa_c, p0, p1, interp1(t,t0,t1,v00,v01), interp1(t,t0,t1,v10,v11))
    if w <= 600: return interp1(w, 550, 600, roc_for(550), roc_for(600))
    else:        return interp1(w, 600, 650, roc_for(600), roc_for(650))

# =========================
# Wind & Corrections
# =========================
def wind_head_component(runway_qfu_deg, wind_dir_deg, wind_speed):
    if runway_qfu_deg is None or wind_dir_deg is None: return 0.0
    diff = radians((wind_dir_deg - runway_qfu_deg) % 360)
    return wind_speed * cos(diff)

def to_corrections_takeoff(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = ground_roll
    gr = gr - 5.0*headwind_kt if headwind_kt >= 0 else gr + 15.0*abs(headwind_kt)
    if paved: gr *= 0.9
    if slope_pc: gr *= (1.0 + 0.07*(slope_pc/1.0))
    return max(gr, 0.0)

def ldg_corrections(ground_roll, headwind_kt, paved=False, slope_pc=0.0):
    gr = ground_roll
    gr = gr - 4.0*headwind_kt if headwind_kt >= 0 else gr + 13.0*abs(headwind_kt)
    if paved: gr *= 0.9
    if slope_pc: gr *= (1.0 - 0.03*(slope_pc/1.0))
    return max(gr, 0.0)

# =========================
# Defaults (preenchidos via AIP/SkyVector/Acukwik)
# =========================
DEFAULT_AERODROMES = [
    # LPSO – Ponte de Sor: elev 390 ft; pista 03/21 ~ 1800 m (5906 ft). AIP confere elevação. 
    {"role":"Departure","icao":"LPSO","qfu":30.0,"elev_ft":390.0,"qnh":1013.0,"temp":15.0,
     "wind_dir":0.0,"wind_kt":0.0,"paved":True,"slope_pc":0.0,"toda_avail":1800.0,"lda_avail":1800.0},
    # LPEV – Évora: elev 807 ft; pista 01/19 = 1300 m.
    {"role":"Arrival","icao":"LPEV","qfu":10.0,"elev_ft":807.0,"qnh":1013.0,"temp":15.0,
     "wind_dir":0.0,"wind_kt":0.0,"paved":True,"slope_pc":0.0,"toda_avail":1300.0,"lda_avail":1300.0},
    # LPCB – Castelo Branco: elev 1251 ft; TODA/LDA default 1000 m (ajustável conforme NOTAM/AIP local).
    {"role":"Alternate","icao":"LPCB","qfu":16.0,"elev_ft":1251.0,"qnh":1013.0,"temp":15.0,
     "wind_dir":0.0,"wind_kt":0.0,"paved":True,"slope_pc":0.0,"toda_avail":1000.0,"lda_avail":1000.0},
]

# =========================
# UI – Inputs
# =========================
st.markdown('<div class="mb-header">Tecnam P2008 – Mass & Balance & Performance</div>', unsafe_allow_html=True)
left, mid, right = st.columns([0.42,0.02,0.56], gap="large")

with left:
    st.markdown("### Weight & Balance")
    ew = st.number_input("Empty Weight (kg)", min_value=0.0, value=0.0, step=1.0)
    ew_moment = st.number_input("Empty Weight Moment (kg·m)", min_value=0.0, value=0.0, step=0.1)
    ew_arm = (ew_moment/ew) if ew>0 else 0.0
    student = st.number_input("Student Weight (kg)", min_value=0.0, value=0.0, step=1.0)
    instructor = st.number_input("Instructor Weight (kg)", min_value=0.0, value=0.0, step=1.0)
    baggage = st.number_input("Baggage (kg)", min_value=0.0, value=0.0, step=1.0)
    fuel_l = st.number_input("Fuel (L)", min_value=0.0, value=0.0, step=1.0)

    pilot = student + instructor
    fuel_wt = fuel_l * ac['fuel_density']
    m_empty = ew_moment
    m_pilot = pilot * ac['pilot_arm']
    m_bag = baggage * ac['baggage_arm']
    m_fuel = fuel_wt * ac['fuel_arm']
    total_weight = ew + pilot + baggage + fuel_wt
    total_moment = m_empty + m_pilot + m_bag + m_fuel
    cg = (total_moment/total_weight) if total_weight>0 else 0.0

    remaining_by_mtow = max(0.0, ac['max_takeoff_weight'] - (ew + pilot + baggage + fuel_wt))
    remaining_by_tank = max(0.0, ac['max_fuel_volume']*ac['fuel_density'] - fuel_wt)
    remaining_fuel_weight = min(remaining_by_mtow, remaining_by_tank)
    remaining_fuel_l = remaining_fuel_weight / ac['fuel_density']
    limit_label = "Tank Capacity" if remaining_by_tank < remaining_by_mtow else "Maximum Weight"

    def color_code_weight(val, limit):
        if val > limit: return 'bad'
        if val > 0.95*limit: return 'warn'
        return 'ok'
    def color_code_cg(cg_, limits):
        lo, hi = limits; margin = 0.05*(hi-lo)
        if cg_ < lo or cg_ > hi: return 'bad'
        if cg_ < lo+margin or cg_ > hi-margin: return 'warn'
        return 'ok'

    st.markdown("#### Resumo")
    st.markdown(f"<div class='mb-summary-row'><div>Fuel restante possível</div><div><b>{remaining_fuel_l:.1f} L</b> ({limit_label})</div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>Total Weight</div><div class='{color_code_weight(total_weight, ac['max_takeoff_weight'])}'><b>{total_weight:.1f} kg</b></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>Total Moment</div><div><b>{total_moment:.2f} kg·m</b></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>CG</div><div class='{color_code_cg(cg, ac['cg_limits'])}'><b>{cg:.3f} m</b></div></div>", unsafe_allow_html=True)

with right:
    st.markdown("### Aeródromos & Performance")
    if 'aerodromes' not in st.session_state:
        st.session_state.aerodromes = DEFAULT_AERODROMES

    perf_rows = []
    for i, a in enumerate(st.session_state.aerodromes):
        with st.expander(f"{a['role']} – {a['icao']}", expanded=(i==0)):
            icao = st.text_input("ICAO", value=a['icao'], key=f"icao_{i}")
            qfu = st.number_input("RWY QFU (deg)", min_value=0.0, max_value=360.0, value=float(a['qfu']), step=1.0, key=f"qfu_{i}")
            elev = st.number_input("Elevation (ft)", value=float(a['elev_ft']), step=1.0, key=f"elev_{i}")
            qnh = st.number_input("QNH (hPa)", min_value=900.0, max_value=1050.0, value=float(a['qnh']), step=0.1, key=f"qnh_{i}")
            temp = st.number_input("Temperature (°C)", min_value=-40.0, max_value=60.0, value=float(a['temp']), step=0.1, key=f"temp_{i}")
            wind_dir = st.number_input("Wind direction (deg FROM)", min_value=0.0, max_value=360.0, value=float(a['wind_dir']), step=1.0, key=f"wdir_{i}")
            wind_kt = st.number_input("Wind speed (kt)", min_value=0.0, value=float(a['wind_kt']), step=1.0, key=f"wspd_{i}")
            paved = st.checkbox("Paved runway", value=bool(a['paved']), key=f"paved_{i}")
            slope_pc = st.number_input("Runway slope (%) (uphill positive)", value=float(a['slope_pc']), step=0.1, key=f"slope_{i}")
            toda_av = st.number_input("TODA available (m)", min_value=0.0, value=float(a['toda_avail']), step=1.0, key=f"toda_{i}")
            lda_av = st.number_input("LDA available (m)", min_value=0.0, value=float(a['lda_avail']), step=1.0, key=f"lda_{i}")

            st.session_state.aerodromes[i].update({"icao":icao,"qfu":qfu,"elev_ft":elev,"qnh":qnh,"temp":temp,
                                                   "wind_dir":wind_dir,"wind_kt":wind_kt,"paved":paved,
                                                   "slope_pc":slope_pc,"toda_avail":toda_av,"lda_avail":lda_av})

            pa_ft = elev + (1013.25 - qnh) * 27
            isa_temp = 15 - 2*(pa_ft/1000)
            da_ft = pa_ft + (120*(temp - isa_temp))

            to_gr = bilinear(pa_ft, temp, TAKEOFF, 'GR')
            to_50 = bilinear(pa_ft, temp, TAKEOFF, '50ft')
            ldg_gr = bilinear(pa_ft, temp, LANDING, 'GR')
            ldg_50 = bilinear(pa_ft, temp, LANDING, '50ft')

            hw = wind_head_component(qfu, wind_dir, wind_kt)
            to_gr_corr = to_corrections_takeoff(to_gr, hw, paved=paved, slope_pc=slope_pc)
            ldg_gr_corr = ldg_corrections(ldg_gr, hw, paved=paved, slope_pc=slope_pc)

            # TODR/LDR tomados dos 50ft do AFM; TODA/LDA avail são os declarados (input)
            perf_rows.append({
                'role': a['role'], 'icao': icao,
                'pa_ft': pa_ft, 'da_ft': da_ft,
                'to_gr': to_gr_corr, 'todr': to_50,
                'ldg_gr': ldg_gr_corr, 'ldr': ldg_50,
                'toda_av': toda_av, 'lda_av': lda_av,
                'hw_comp': hw,
            })

    st.markdown("#### Resumo de Performance")
    def fmt(v): return f"{v:.0f}" if isinstance(v,(int,float)) else str(v)
    st.markdown("<table class='mb-table'><tr><th>Leg/Aeródromo</th><th>PA ft</th><th>DA ft</th><th>TO GR (m)*</th><th>TODR 50ft (m)</th><th>LND GR (m)*</th><th>LDR 50ft (m)</th><th>TODA Av</th><th>LDA Av</th></tr>" +
                "".join([f"<tr><td>{r['role']} {r['icao']}</td><td>{fmt(r['pa_ft'])}</td><td>{fmt(r['da_ft'])}</td><td>{fmt(r['to_gr'])}</td><td>{fmt(r['todr'])}</td><td>{fmt(r['ldg_gr'])}</td><td>{fmt(r['ldr'])}</td><td>{fmt(r['toda_av'])}</td><td>{fmt(r['lda_av'])}</td></tr>" for r in perf_rows]) +
                "</table>", unsafe_allow_html=True)

# =========================
# Fuel Planning (20 L/h)
# =========================
st.markdown("### Fuel Planning (assume 20 L/h por defeito)")
rate_lph = 20.0
colA, colB = st.columns([0.5,0.5])
def time_to_liters(hh=0, mm=0, rate=rate_lph): return rate * (hh + mm/60.0)

with colA:
    su_min = st.number_input("Start-up & Taxi (min)", min_value=0, value=15, step=1)
    climb_min = st.number_input("Climb (min)", min_value=0, value=15, step=1)
    enrt_h = st.number_input("Enroute (h)", min_value=0, value=2, step=1)
    enrt_min = st.number_input("Enroute (min)", min_value=0, value=15, step=1)
    desc_min = st.number_input("Descent (min)", min_value=0, value=15, step=1)
with colB:
    alt_min = st.number_input("Alternate (min)", min_value=0, value=60, step=5)
    reserve_min = st.number_input("Reserve (min)", min_value=0, value=45, step=5)
    extra_min = st.number_input("Extra (min)", min_value=0, value=0, step=5)

trip_l = time_to_liters(0, climb_min) + time_to_liters(enrt_h, enrt_min) + time_to_liters(0, desc_min)
req_ramp = time_to_liters(0, su_min) + trip_l + 0.05*trip_l + time_to_liters(0, alt_min) + time_to_liters(0, reserve_min)
extra_l = time_to_liters(0, extra_min)
total_ramp = req_ramp + extra_l

st.markdown(f"- **Trip Fuel**: {trip_l:.1f} L  ")
st.markdown(f"- **Contingency 5%**: {0.05*trip_l:.1f} L  ")
st.markdown(f"- **Required Ramp Fuel** (1+5+6+7+8): **{req_ramp:.1f} L**  ")
st.markdown(f"- **Extra**: {extra_l:.1f} L  ")
st.markdown(f"- **Total Ramp Fuel**: **{total_ramp:.1f} L**")

# =========================
# PDF – preencher template + anexos
# =========================
st.markdown("### PDF – M&B and Performance Data Sheet")
reg = st.text_input("Aircraft Registration", value="CS-XXX")
mission = st.text_input("Mission #", value="001")
date_str = st.text_input("Date (DD/MM/YYYY)", value=datetime.datetime.now(pytz.UTC).strftime("%d/%m/%Y"))

APP_DIR = Path(__file__).parent
pdf_template_path = APP_DIR / "TecnamP2008MBPerformanceSheet_MissionX.pdf"

uploaded = st.file_uploader("PDF template (opcional, se não estiver no repo)", type=["pdf"])
if uploaded is not None:
    tmp = APP_DIR / "template_uploaded.pdf"
    tmp.write_bytes(uploaded.getbuffer())
    pdf_template_path = tmp

if st.button("Gerar PDF preenchido"):
    if not pdf_template_path.exists():
        st.error(f"Template não encontrado: {pdf_template_path}")
        st.stop()

    # --- Map campos (ajusta se o teu PDF tiver outros nomes) ---
    FIELD_MAP = {
        "Textbox19": reg,        # Aircraft Reg.
        "Textbox18": date_str,   # Date
        # Totais (peso/CG/limites) setados mais abaixo com cor
    }

    # Preenche dados do 1º aeródromo (Departure) na página 2
    if perf_rows:
        dep = next((r for r in perf_rows if r['role']=="Departure"), perf_rows[0])
        FIELD_MAP.update({
            "Textbox22": dep['icao'],         # Airfield
            "Textbox50": f"{dep['pa_ft']:.0f}",     # PA
            "Textbox49": f"{dep['da_ft']:.0f}",     # DA
            "Textbox45": f"{dep['todr']:.0f}",      # TODR
            "Textbox41": f"{dep['ldr']:.0f}",       # LDR
        })
    # Fuel planning principais
    FIELD_MAP.update({
        "Textbox61": f"{total_ramp:.0f} L",  # Total Ramp Fuel
    })

    # --- Tenta pdfrw (para setar cor). Se falhar, usa pypdf ---
    def write_with_pdfrw(path_in, field_map):
        reader = Rd_pdfrw(str(path_in))
        fields = reader.Root.AcroForm.Fields if '/AcroForm' in reader.Root else []
        def set_field(name, value, color_rgb=None):
            for f in fields:
                if f.get('/T') and f['/T'][1:-1] == name:
                    f.update(PdfDict(V=str(value)))
                    # cor
                    if color_rgb:
                        r,g,b = color_rgb
                        f.update(PdfDict(DA=f"{r/255:.3f} {g/255:.3f} {b/255:.3f} rg /Helv 10 Tf"))
                    break
        for k,v in field_map.items(): set_field(k, v)

        # Peso/CG com cor
        wt_color = (30,150,30) if total_weight <= ac['max_takeoff_weight'] else (200,0,0)
        lo, hi = ac['cg_limits']; margin = 0.05*(hi-lo)
        if cg < lo or cg > hi: cg_color = (200,0,0)
        elif cg < lo+margin or cg > hi-margin: cg_color = (200,150,30)
        else: cg_color = (30,150,30)
        set_field("Textbox14", f"{total_weight:.1f}", wt_color)  # total weight
        set_field("Textbox16", f"{cg:.3f}", cg_color)            # CG
        set_field("Textbox17", f"{ac['max_takeoff_weight']:.0f}")# MTOW
        set_field("Textbox5", f"{ac['cg_limits'][0]:.3f}")       # CG fwd

        buf = BytesIO(); Wr_pdfrw().write(buf, reader); buf.seek(0)
        return buf.getvalue()

    def write_with_pypdf(path_in, field_map):
        reader = Rd_pypdf(str(path_in)); writer = Wr_pypdf()
        for p in reader.pages: writer.add_page(p)
        if "/AcroForm" in reader.trailer["/Root"]:
            writer._root_object.update({"/AcroForm": reader.trailer["/Root"]["/AcroForm"]})
            writer._root_object["/AcroForm"].update({"/NeedAppearances": True})
        # Page 1 fill basic fields
        writer.update_page_form_field_values(writer.pages[0], field_map)
        # Peso/CG sem cor (limitação do pypdf simples)
        writer.update_page_form_field_values(writer.pages[0], {
            "Textbox14": f"{total_weight:.1f}",
            "Textbox16": f"{cg:.3f}",
            "Textbox17": f"{ac['max_takeoff_weight']:.0f}",
            "Textbox5": f"{ac['cg_limits'][0]:.3f}",
        })
        out = BytesIO(); writer.write(out); out.seek(0)
        return out.getvalue()

    try:
        filled = write_with_pdfrw(pdf_template_path, FIELD_MAP)
    except Exception:
        filled = write_with_pypdf(pdf_template_path, FIELD_MAP)

    # --- Cria página de anexos com cálculos (ReportLab) ---
    annex = BytesIO()
    cnv = canvas.Canvas(annex, pagesize=A4)
    w, h = A4
    y = h - 40
    cnv.setFont("Helvetica-Bold", 12)
    cnv.drawString(40, y, "Annex – Calculations Summary")
    y -= 20
    cnv.setFont("Helvetica", 10)
    def line(txt):
        nonlocal y
        cnv.drawString(40, y, ascii_safe(txt)); y -= 14
        if y < 80:
            cnv.showPage(); y = h - 40; cnv.setFont("Helvetica", 10)
    line(f"Aircraft: {ac['name']}  |  Reg: {reg}  |  Mission: {mission}  |  Date: {date_str}")
    line(f"EW: {ew:.1f} kg  |  EW Moment: {ew_moment:.2f} kg·m  |  Pilot: {student+instructor:.1f} kg  |  Baggage: {baggage:.1f} kg")
    line(f"Fuel: {fuel_l:.1f} L ({fuel_wt:.1f} kg)  |  Total Weight: {total_weight:.1f} kg  |  CG: {cg:.3f} m")
    line(f"Fuel remaining possible: {remaining_fuel_l:.1f} L ({'Limited by tank' if remaining_by_tank<remaining_by_mtow else 'Limited by MTOW'})")
    line("")
    for r in perf_rows:
        line(f"{r['role']} {r['icao']}: PA {r['pa_ft']:.0f} ft, DA {r['da_ft']:.0f} ft, Head/Tailwind comp {r['hw_comp']:.0f} kt")
        line(f"  Takeoff: GR* {r['to_gr']:.0f} m, TODR 50ft {r['todr']:.0f} m  |  Declared TODA Av {r['toda_av']:.0f} m")
        line(f"  Landing: GR* {r['ldg_gr']:.0f} m, LDR 50ft {r['ldr']:.0f} m  |  Declared LDA Av {r['lda_av']:.0f} m")
    line("")
    line(f"Fuel Planning @20 L/h: Trip {trip_l:.1f} L | Cont 5% {0.05*trip_l:.1f} L | Req Ramp {req_ramp:.1f} L | Extra {extra_l:.1f} L | Total Ramp {total_ramp:.1f} L")
    cnv.showPage(); cnv.save()
    annex_pdf = annex.getvalue()

    # --- Junta o anexo ao preenchido ---
    main_reader = Rd_pypdf(BytesIO(filled))
    annex_reader = Rd_pypdf(BytesIO(annex_pdf))
    final_writer = Wr_pypdf()
    for p in main_reader.pages: final_writer.add_page(p)
    for p in annex_reader.pages: final_writer.add_page(p)
    out_path = APP_DIR / f"MB_Performance_{reg}_{mission}.pdf"
    with open(out_path, "wb") as f: final_writer.write(f)

    st.success("PDF gerado com sucesso!")
    with open(out_path, "rb") as f:
        st.download_button("Descarregar PDF", f, file_name=out_path.name, mime="application/pdf")


