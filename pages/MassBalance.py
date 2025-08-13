# --- (mantém os teus imports) ---
import streamlit as st
import datetime
from pathlib import Path
import pytz
import unicodedata
from math import cos, sin, radians
from typing import List, Dict

# NEW: PDF filling
from io import BytesIO
try:
    from pypdf import PdfReader, PdfWriter
except Exception:
    PdfReader = None
    PdfWriter = None
# -------------------------------

# ... (mantém helpers ascii_safe, printable, etc.) ...

# (mantém st.set_page_config e CSS)

# (mantém AC, AERODROMES_DEFAULT, TAKEOFF, LANDING, ROC, VY)

# =========================
# NEW – Vy interpolation
# =========================
def vy_interp(pa_ft, weight):
    w = clamp(weight, 550.0, 650.0)
    def vy_for_w(w_):
        tab = VY[int(w_)]
        pas = sorted(tab.keys())
        pa_c = clamp(pa_ft, pas[0], pas[-1])
        p0 = max([p for p in pas if p <= pa_c])
        p1 = min([p for p in pas if p >= pa_c])
        v0 = tab[p0]; v1 = tab[p1]
        return interp1(pa_c, p0, p1, v0, v1)
    if w <= 600:
        return interp1(w, 550, 600, vy_for_w(550), vy_for_w(600))
    else:
        return interp1(w, 600, 650, vy_for_w(600), vy_for_w(650))

# =========================
# UI – inputs (mantém)
# =========================
st.markdown('<div class="mb-header">Tecnam P2008 – Mass & Balance & Performance</div>', unsafe_allow_html=True)

left, _, right = st.columns([0.42,0.02,0.56], gap="large")

with left:
    st.markdown("### Weight & balance (inputs)")
    ew = st.number_input("Empty weight (kg)", min_value=0.0, value=0.0, step=1.0)
    ew_moment = st.number_input("Empty weight moment (kg*m)", min_value=0.0, value=0.0, step=0.1)
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
    st.markdown(f"<div class='mb-summary-row'><div>Extra fuel possible</div><div><b>{remaining_fuel_l:.1f} L</b> <span class='hint'>(limited by <i>{limit_label}</i>)</span></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>Total weight</div><div class='{w_color(total_weight, AC['max_takeoff_weight'])}'><b>{total_weight:.1f} kg</b><span class='chip'>≤ {AC['max_takeoff_weight']:.0f}</span></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>Total moment</div><div><b>{total_moment:.2f} kg*m</b></div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='mb-summary-row'><div>CG</div><div class='{cg_color_val(cg, AC['cg_limits'])}'><b>{cg:.3f} m</b><span class='chip'>{AC['cg_limits'][0]:.3f} – {AC['cg_limits'][1]:.3f} m</span></div></div>", unsafe_allow_html=True)

    # =========================
    # NEW – M&B table (detalhe)
    # =========================
    st.markdown("#### Mass & Balance table")
    mb_rows = [
        ("EmptyWeight", ew, AC["units"]["arm"], ew_arm, m_empty),
        ("Fuel", fuel_wt, AC["units"]["arm"], AC["fuel_arm"], m_fuel),
        ("Pilot&Passenger", pilot, AC["units"]["arm"], AC["pilot_arm"], m_pilot),
        ("Baggage", baggage, AC["units"]["arm"], AC["baggage_arm"], m_bag),
    ]
    mb_html = (
        "<table class='mb-table'>"
        "<tr><th>Item</th><th>Weight (kg)</th><th>Arm (m)</th><th>Moment (kg*m)</th></tr>" +
        "".join([f"<tr><td>{lbl}</td><td>{w:.1f}</td><td>{arm:.3f}</td><td>{mom:.2f}</td></tr>"
                 for (lbl, w, _, arm, mom) in mb_rows]) +
        f"<tr><td><b>Total</b></td><td><b>{total_weight:.1f}</b></td><td>—</td><td><b>{total_moment:.2f}</b></td></tr>"
        f"<tr><td><b>CG</b></td><td colspan='3'><b>{cg:.3f} m</b> <span class='chip'>{AC['cg_limits'][0]:.3f} – {AC['cg_limits'][1]:.3f} m</span></td></tr>"
        "</table>"
    )
    st.markdown(mb_html, unsafe_allow_html=True)

    # checks adicionais
    if pilot > AC["max_passenger_weight"]:
        st.error(f"Pilot&Passenger > {AC['max_passenger_weight']:.0f} kg.")
    if baggage > AC["max_baggage_weight"]:
        st.error(f"Baggage > {AC['max_baggage_weight']:.0f} kg.")

with right:
    st.markdown("### Aerodromes & performance")
    if 'aerodromes' not in st.session_state:
        st.session_state.aerodromes = AERODROMES_DEFAULT

    perf_rows = []
    slope_warn = False

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

            if abs(slope_pc) > 3.0: slope_warn = True

            # PA/DA (mantém tua lógica)
            pa_ft = elev + (1013.25 - qnh) * 27
            isa_temp = 15 - 2*(pa_ft/1000)
            da_ft = pa_ft + (120*(temp - isa_temp))

            # Interpolação (mantém) + NEW ROC/Vy
            to_gr = bilinear(pa_ft, temp, TAKEOFF, 'GR')
            to_50 = bilinear(pa_ft, temp, TAKEOFF, '50ft')
            ldg_gr = bilinear(pa_ft, temp, LANDING, 'GR')
            ldg_50 = bilinear(pa_ft, temp, LANDING, '50ft')

            hw, cw = wind_components(qfu, wind_dir, wind_kt)
            to_gr_corr = to_corrections_takeoff(to_gr, hw, paved=paved, slope_pc=slope_pc)
            ldg_gr_corr = ldg_corrections(ldg_gr, hw, paved=paved, slope_pc=slope_pc)

            # NEW: ROC e Vy ao peso atual
            roc_ftmin = roc_interp(pa_ft, temp, total_weight)
            vy_kt = vy_interp(pa_ft, total_weight)

            perf_rows.append({
                'role': a['role'], 'icao': icao, 'qfu': qfu,
                'elev_ft': elev, 'qnh': qnh, 'temp': temp,
                'pa_ft': pa_ft, 'da_ft': da_ft, 'isa_temp': isa_temp,
                'to_gr': to_gr_corr, 'to_50': to_50,
                'ldg_gr': ldg_gr_corr, 'ldg_50': ldg_50,
                'toda_av': toda_av, 'lda_av': lda_av,
                'hw_comp': hw, 'cw_comp': cw,
                'paved': paved, 'slope_pc': slope_pc,
                'roc': roc_ftmin, 'vy': vy_kt,
            })

    if slope_warn:
        st.warning("Runway slope > 3% entered — double-check values; performance corrections can be very large.")

# =========================
# Performance summary (ATUALIZADO)
# =========================
st.markdown("### Performance summary")
for r in perf_rows:
    r['tod_ok'] = r['to_50'] <= r['toda_av']
    r['ldg_ok'] = r['ldg_50'] <= r['lda_av']
    r['tod_margin'] = r['toda_av'] - r['to_50']
    r['ldg_margin'] = r['lda_av'] - r['ldg_50']

def fmt(v): return f"{v:.0f}" if isinstance(v, (int,float)) else str(v)
def status_cell(ok, margin):
    cls = 'ok' if ok else 'bad'
    sign = '+' if margin >= 0 else '−'
    return f"<span class='{cls}'>{'OK' if ok else 'NOK'} ({sign}{abs(margin):.0f} m)</span>"

st.markdown(
    "<table class='mb-table'><tr>"
    "<th>Leg/Aerodrome</th><th>QFU</th><th>PA/DA ft</th>"
    "<th>TODR 50ft</th><th>TODA</th><th>Takeoff fit</th>"
    "<th>LDR 50ft</th><th>LDA</th><th>Landing fit</th>"
    "<th>Wind (H/C)</th><th>ROC (ft/min)</th><th>Vy (kt)</th>"
    "</tr>" +
    "".join([
        f"<tr>"
        f"<td>{r['role']} {r['icao']}</td>"
        f"<td>{fmt(r['qfu'])}</td>"
        f"<td>{fmt(r['pa_ft'])}/{fmt(r['da_ft'])}</td>"
        f"<td>{fmt(r['to_50'])}</td><td>{fmt(r['toda_av'])}</td>"
        f"<td>{status_cell(r['tod_ok'], r['tod_margin'])}</td>"
        f"<td>{fmt(r['ldg_50'])}</td><td>{fmt(r['lda_av'])}</td>"
        f"<td>{status_cell(r['ldg_ok'], r['ldg_margin'])}</td>"
        f"<td>{('HW' if r['hw_comp']>=0 else 'TW')} {abs(r['hw_comp']):.0f} / {abs(r.get('cw_comp',0)):.0f} kt</td>"
        f"<td>{fmt(r['roc'])}</td>"
        f"<td>{fmt(r['vy'])}</td>"
        f"</tr>"
        for r in perf_rows
    ]) + "</table>",
    unsafe_allow_html=True
)

# =========================
# Fuel planning (mantém) + tempos formatados
# =========================
RATE_LPH = 20.0
st.markdown("### Fuel planning (assume 20 L/h by default)")

c1, c2, c3, c4 = st.columns([0.25,0.25,0.25,0.25])

def time_to_liters(h=0, m=0, rate=RATE_LPH):
    return rate * (h + m/60.0)

def fmt_time(h=0, m=0):
    return (f"{int(h)}h{int(m):02d}min") if h or m>=60 else f"{int(m)}min"

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
# NEW – Preencher PDF (usa o 1.º aeródromo / Departure)
# =========================
st.markdown("### Export to PDF (official sheet)")

colA, colB = st.columns([0.5,0.5])
with colA:
    reg = st.text_input("Aircraft Reg. (for PDF)", value="CS-XXX")
with colB:
    pdf_template = st.text_input("PDF template path", value=str(Path('/mnt/data/TecnamP2008MBPerformanceSheet_MissionX_organizado.pdf')))

def fill_pdf(fields_dict, template_path):
    if PdfReader is None or PdfWriter is None:
        raise RuntimeError("pypdf is not installed. Add 'pypdf' to requirements.txt.")
    reader = PdfReader(template_path)
    writer = PdfWriter()
    writer.append(reader)
    page0 = writer.pages[0]
    # write fields
    writer.update_page_form_field_values(page0, fields_dict)
    # keep appearances visible
    for j in range(len(writer.pages)):
        writer.pages[j].annotations = writer.pages[j].annotations
    out = BytesIO()
    writer.write(out)
    out.seek(0)
    return out

def deg_pair(qfu):
    q1 = int(round(qfu)) % 360
    q2 = (q1 + 180) % 360
    return f"{q1:03d}º/{q2:03d}º"

def wind_str(wdir, wspd): return f"{int(round(wdir)):03d}º/{int(round(wspd))}"

def hhmm_str(total_min):
    h = total_min // 60
    m = total_min % 60
    return fmt_time(h, m)

if st.button("Gerar PDF preenchido"):
    try:
        if not perf_rows:
            st.error("Sem aeródromos/performance calculada.")
        else:
            dep = [r for r in perf_rows if r['role'].lower().startswith('dep') or r['role']=='Departure']
            r0 = dep[0] if dep else perf_rows[0]

            # pares tempo/litros para os campos
            trip_min = climb_min + enrt_h*60 + enrt_min + desc_min
            req_ramp_min = su_min + trip_min + alt_min + reserve_min  # (ignora contagem 5% em minutos)
            total_min = req_ramp_min + extra_min

            fields = {
                # HEADER / IDENTIFICAÇÃO
                "Textbox18": datetime.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%d/%m/%Y"),  # Date
                "Textbox19": reg,  # Aircraft Reg
                # AIRFIELD
                "Textbox22": r0['icao'],                    # Airfield (ICAO)
                "Textbox23": deg_pair(r0['qfu']),          # RWY QFU "xxxº/yyyº"
                "Textbox53": f"{int(round(r0['elev_ft']))}",# Elevation (ft)
                "Textbox52": f"{r0['qnh']:.0f}",           # QNH (hPa)
                "Textbox51": f"{r0['temp']:.0f}",          # Temperature (ºC)
                "Textbox58": wind_str(st.session_state.aerodromes[0]['wind_dir'],
                                      st.session_state.aerodromes[0]['wind_kt']),  # Wind "dir/kt"
                "Textbox50": f"{int(round(r0['pa_ft']))}", # Pressure Alt (ft)
                "Textbox49": f"{int(round(r0['da_ft']))}", # Density Alt (ft)
                # RUNWAY DATA / PERFORMANCE DISPONÍVEL
                "Textbox47": f"{int(round(r0['toda_av']))}/{int(round(r0['lda_av']))}",  # "TODA/LDA"
                "Textbox45": f"{int(round(r0['to_50']))}",   # TODR (m) 50 ft
                "Textbox39": f"{int(round(r0['ldg_50']))}",  # LDR (m) 50 ft
                "Textbox41": f"{int(round(r0['roc']))}",     # ROC (ft/min)
                "Textbox43": f"{int(round(r0['lda_av']))}",  # LDA (redundante no form, segura
                # LIMITES CG (opcional, campos existem no exemplo)
                # "Textbox5": f"{AC['cg_limits'][0]:.3f}",
                # "Textbox16": f"{(AC['cg_limits'][0]+AC['cg_limits'][1])/2:.3f}",
                # "Textbox6": f"{AC['cg_limits'][1]:.3f}",
                # FUEL PLANNING (pares tempo/litros — mapeamento pelos exemplos no PDF)
                "Textbox59": f"{hhmm_str(su_min)}",            # (1) Start-up & Taxi - tempo
                "Textbox60": f"{time_to_liters(0, su_min):.0f}L",
                "Textbox63": f"{hhmm_str(trip_min)}",          # (5) Trip Fuel - tempo
                "Textbox70": f"{trip_l:.0f}L",
                "Textbox67": f"{hhmm_str(alt_min)}",           # (7) Alternate - tempo
                "Textbox68": f"{time_to_liters(0, alt_min):.0f}L",
                "Textbox64": f"{hhmm_str(reserve_min)}",       # (8) Reserve 45 min - tempo
                "Textbox65": f"{time_to_liters(0, reserve_min):.0f}L",
                "Textbox62": f"{hhmm_str(req_ramp_min)}",      # (9) Required Ramp Fuel - tempo
                "Textbox61": f"{req_ramp:.0f}L",
                "Textbox69": f"{hhmm_str(total_min)}",         # (11) Total Ramp Fuel - tempo
                "Textbox66": f"{total_ramp:.0f}L",
            }
            # Dica: Se o teu PDF tiver outros nomes, imprime-os numa sidebar para ajustar o mapeamento.

            pdf_bytes = fill_pdf(fields, pdf_template)
            st.success("PDF preenchido.")
            st.download_button(
                label="Download PDF preenchido",
                data=pdf_bytes,
                file_name="TecnamP2008_MB_Performance_filled.pdf",
                mime="application/pdf",
            )
    except Exception as e:
        st.error(f"Erro ao gerar PDF: {e}")



