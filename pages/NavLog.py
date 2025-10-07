# app.py — NAVLOG Performance-Only (com vento por perna e checkpoints de 2 min)
# Reqs: streamlit, pytz (opcional)
# - Foco: Cabeçalho + cálculo de performance por perna, com vento introduzido manualmente
# - Removeu: rota/altitudes/holds/NAVAIDs/JSON/PDF/Relatório/aeródromos

import streamlit as st
import datetime as dt
import pytz
import unicodedata
from typing import Optional, Tuple
from math import sin, asin, radians, degrees, fmod

st.set_page_config(page_title="NAVLOG — Performance", layout="wide", initial_sidebar_state="collapsed")

# ===== Helpers =====
def clean_point_name(s) -> str:
    txt = unicodedata.normalize("NFKD", str(s or "")).encode("ascii","ignore").decode("ascii")
    return txt.strip().upper()

def _round_alt(x: float) -> int:
    if x is None: return 0
    v = abs(float(x)); base = 50 if v < 1000 else 100
    return int(round(float(x)/base) * base)

def _round_unit(x: float) -> int:
    if x is None: return 0
    return int(round(float(x)))

def _round_tenth(x: float) -> float:
    if x is None: return 0.0
    return round(float(x), 1)

def _round_angle(x: float) -> int:
    if x is None: return 0
    return int(round(float(x))) % 360

def mmss_from_seconds(tsec: int) -> str:
    m = tsec // 60; s = tsec % 60
    return f"{m:02d}:{s:02d}"

def hhmmss_from_seconds(tsec: int) -> str:
    h = tsec // 3600; m = (tsec % 3600)//60; s = tsec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def round_to_10s(sec: float) -> int:
    if sec <= 0: return 0
    s = int(round(sec/10.0)*10)
    return max(s, 10)

def wrap360(x):
    x = fmod(x,360.0)
    return x+360 if x<0 else x

def angle_diff(a,b):
    return (a-b+180)%360-180

def apply_var(true_deg, var_deg, east_is_negative=False):
    return wrap360(true_deg - var_deg if east_is_negative else true_deg + var_deg)

# ===== Atmosfera =====
def isa_temp(pa_ft):
    return 15.0 - 2.0*(pa_ft/1000.0)

def pressure_alt(alt_ft, qnh_hpa):
    return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0

# ===== Perf (Tecnam P2008 – exemplo) =====
ROC_ENROUTE = {
    0:{-25:981,0:835,25:704,50:586},  2000:{-25:870,0:726,25:597,50:481},
    4000:{-25:759,0:617,25:491,50:377},6000:{-25:648,0:509,25:385,50:273},
    8000:{-25:538,0:401,25:279,50:170},10000:{-25:428,0:294,25:174,50:66},
    12000:{-25:319,0:187,25:69,50:-37},14000:{-25:210,0:80,25:-35,50:-139},
}
ROC_FACTOR = 0.90
VY_ENROUTE = {0:67,2000:67,4000:67,6000:67,8000:67,10000:67,12000:67,14000:67}

CRUISE={
    0:{1800:(82,15.3),1900:(89,17.0),2000:(95,18.7),2100:(101,20.7),2250:(110,24.6),2388:(118,26.9)},
    2000:{1800:(82,15.3),1900:(88,16.6),2000:(94,17.5),2100:(100,19.9),2250:(109,23.5)},
    4000:{1800:(81,15.1),1900:(88,16.2),2000:(94,17.5),2100:(100,19.2),2250:(108,22.4)},
    6000:{1800:(81,14.9),1900:(87,15.9),2000:(93,17.1),2100:(99,18.5),2250:(108,21.3)},
    8000:{1800:(81,14.9),1900:(86,15.6),2000:(92,16.7),2100:(98,18.0),2250:(107,20.4)},
    10000:{1800:(85,15.4),1900:(91,16.4),2000:(91,16.4),2100:(97,17.5),2250:(106,19.7)},
}

def clamp(v,lo,hi): return max(lo,min(hi,v))

def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

# Cruise lookup
def cruise_lookup(pa_ft: float, rpm: int, oat_c: Optional[float]) -> Tuple[float,float]:
    pas=sorted(CRUISE.keys()); pa_c=clamp(pa_ft,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    def val(pa):
        table=CRUISE[pa]
        if rpm in table: return table[rpm]
        rpms=sorted(table.keys())
        if rpm<rpms[0]: lo,hi=rpms[0],rpms[1]
        elif rpm>rpms[-1]: lo,hi=rpms[-2],rpms[-1]
        else:
            lo=max([r for r in rpms if r<=rpm]); hi=min([r for r in rpms if r>=rpm])
        (tas_lo,ff_lo),(tas_hi,ff_hi)=table[lo],table[hi]
        t=(rpm-lo)/(hi-lo) if hi!=lo else 0.0
        return (tas_lo + t*(tas_hi-tas_lo), ff_lo + t*(ff_hi-ff_lo))
    tas0,ff0=val(p0); tas1,ff1=val(p1)
    tas=interp1(pa_c,p0,p1,tas0,tas1); ff=interp1(pa_c,p0,p1,ff0,ff1)
    if oat_c is not None:
        dev=oat_c - isa_temp(pa_c)
        if dev>0: tas*=1-0.02*(dev/15); ff*=1-0.025*(dev/15)
        elif dev<0: tas*=1+0.01*((-dev)/15); ff*=1+0.03*((-dev)/15)
    return max(0.0,tas), max(0.0,ff)

# ROC & Vy
def roc_interp_enroute(pa, temp_c):
    pas=sorted(ROC_ENROUTE.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    temps=[-25,0,25,50]; t=clamp(temp_c,temps[0],temps[-1])
    if t<=0: t0,t1=-25,0
    elif t<=25: t0,t1=0,25
    else: t0,t1=25,50
    v00, v01 = ROC_ENROUTE[p0][t0], ROC_ENROUTE[p0][t1]
    v10, v11 = ROC_ENROUTE[p1][t0], ROC_ENROUTE[p1][t1]
    v0 = interp1(t, t0, t1, v00, v01); v1 = interp1(pa_c, p0, p1, v10, v11)
    return max(1.0, interp1(pa_c, p0, p1, v0, v1) * ROC_FACTOR)

def vy_interp_enroute(pa):
    pas=sorted(VY_ENROUTE.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    return interp1(pa_c, p0, p1, VY_ENROUTE[p0], VY_ENROUTE[p1])

# ===== Vento / Triângulo do vento =====
import math

def wind_triangle(tc_deg: float, tas_kt: float, wind_from_deg: float, wind_kt: float):
    if tas_kt <= 0: return 0.0, wrap360(tc_deg), 0.0
    delta = radians(angle_diff(wind_from_deg, tc_deg))
    cross = wind_kt * sin(delta)
    s = max(-1.0, min(1.0, cross/max(tas_kt,1e-9)))
    wca = degrees(asin(s))
    th  = wrap360(tc_deg + wca)
    gs  = max(0.0, tas_kt*math.cos(radians(wca)) - wind_kt*math.cos(delta))
    return wca, th, gs

# ===== Sessão (defaults básicos) =====

def ensure(k, v):
    if k not in st.session_state: st.session_state[k] = v

ensure("aircraft","P208"); ensure("registration","CS-ECC"); ensure("callsign","RVP")
ensure("student","AMOIT"); ensure("lesson",""); ensure("instrutor","")
ensure("startup","")
ensure("qnh",1013); ensure("temp_c",15)
ensure("var_deg",1); ensure("var_is_e",False)
ensure("rpm_climb",2250); ensure("rpm_cruise",2000)
ensure("descent_ff",15.0); ensure("rod_fpm",700); ensure("start_fuel",85.0)
ensure("cruise_ref_kt",90); ensure("descent_ref_kt",65)
# Estado cumulativo para "Próxima perna"
ensure("carry_alt_ft", 0.0)
ensure("carry_efoB_L", float(st.session_state.start_fuel))
ensure("carry_time_sec", 0)
ensure("carry_dist_nm", 0.0)

# =========================================================
# Cabeçalho / Perf
# =========================================================
st.title("NAVLOG — Performance (com vento por perna)")
with st.form("hdr_perf_form", clear_on_submit=False):
    st.subheader("Identificação e Parâmetros")
    c1,c2,c3 = st.columns(3)
    with c1:
        f_aircraft = st.text_input("Aircraft", st.session_state.aircraft)
        f_registration = st.selectbox("Registration",
                                      ["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"],
                                      index=["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"].index(st.session_state.registration))
        f_callsign = st.text_input("Callsign", st.session_state.callsign)
    with c2:
        f_student = st.text_input("Student", st.session_state.student)
        f_lesson  = st.text_input("Lesson (ex: 12)", st.session_state.lesson)
        f_instrut = st.text_input("Instrutor", st.session_state.instrutor)
    with c3:
        f_qnh  = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh), step=1)
        f_oat  = st.number_input("OAT (°C)", -40, 50, int(st.session_state.temp_c), step=1)

    st.markdown("---")
    c4,c5,c6 = st.columns(3)
    with c4:
        f_var  = st.number_input("Mag Variation (°)", 0, 30, int(st.session_state.var_deg), step=1)
        f_varE = (st.selectbox("Variação E/W", ["W","E"], index=(1 if st.session_state.var_is_e else 0))=="E")
    with c5:
        f_rpm_cl = st.number_input("Climb RPM (AFM)", 1800, 2388, int(st.session_state.rpm_climb), step=10)
        f_rpm_cr = st.number_input("Cruise RPM (AFM)", 1800, 2388, int(st.session_state.rpm_cruise), step=10)
    with c6:
        f_ff_ds  = st.number_input("Descent FF (L/h)", 0.0, 30.0, float(st.session_state.descent_ff), step=0.1)
        f_rod    = st.number_input("ROD (ft/min)", 200, 1500, int(st.session_state.rod_fpm), step=10)
        f_fuel0  = st.number_input("Fuel inicial (EFOB_START) [L]", 0.0, 1000.0, float(st.session_state.start_fuel), step=0.1)

    submitted = st.form_submit_button("Aplicar parâmetros")
    if submitted:
        st.session_state.aircraft=f_aircraft; st.session_state.registration=f_registration; st.session_state.callsign=f_callsign
        st.session_state.student=f_student; st.session_state.lesson=f_lesson; st.session_state.instrutor=f_instrut
        st.session_state.qnh=f_qnh; st.session_state.temp_c=f_oat
        st.session_state.var_deg=f_var; st.session_state.var_is_e=f_varE
        st.session_state.rpm_climb=f_rpm_cl; st.session_state.rpm_cruise=f_rpm_cr
        st.session_state.descent_ff=f_ff_ds; st.session_state.rod_fpm=f_rod; st.session_state.start_fuel=f_fuel0
        # reset carry if fuel changed
        st.session_state.carry_efoB_L = f_fuel0
        st.success("Parâmetros aplicados.")

# =========================================================
# Entrada da Perna (manual, com vento)
# =========================================================
st.subheader("Perna — entrada manual")
colA,colB,colC,colD = st.columns(4)
with colA:
    tc_true = st.number_input("True Course (°T)", min_value=0.0, max_value=359.9, value=0.0, step=0.1)
    dist_nm = st.number_input("Distância (nm)", min_value=0.0, value=0.0, step=0.1)
with colB:
    alt_ini = st.number_input("Alt início (ft)", min_value=0.0, step=50.0, value=float(st.session_state.carry_alt_ft or 0.0))
    alt_fim = st.number_input("Alt alvo (ft)",   min_value=0.0, step=50.0, value=float(st.session_state.carry_alt_ft or 0.0))
with colC:
    wind_from = st.number_input("Vento FROM (°T)", min_value=0, max_value=360, value=0, step=1)
    wind_kt   = st.number_input("Vento (kt)", min_value=0, max_value=200, value=0, step=1)
with colD:
    checkpoints = st.number_input("Check a cada (min)", min_value=2, max_value=10, value=2, step=1)

# ===== Cálculos de performance para a perna =====
pa_start  = pressure_alt(alt_ini, st.session_state.qnh)
vy_kt     = vy_interp_enroute(pa_start)
roc_fpm   = roc_interp_enroute(pa_start, st.session_state.temp_c)

# TAS/FF
_ , ff_climb  = cruise_lookup(alt_ini + 0.5*max(0.0, alt_fim-alt_ini), int(st.session_state.rpm_climb),  st.session_state.temp_c)
crz_tas_tbl , crz_ff_tbl = cruise_lookup(pressure_alt(alt_fim, st.session_state.qnh), int(st.session_state.rpm_cruise), st.session_state.temp_c)
crz_tas = max(1.0, crz_tas_tbl)  # podemos querer trocar por override no futuro
ff_descent    = float(st.session_state.descent_ff)

# Segmento 1: subir ou descer até alt_fim
alt_delta = alt_fim - alt_ini
if alt_delta > 0:  # CLIMB
    t1_min = alt_delta / max(roc_fpm,1e-6)
    t1_sec = round_to_10s(t1_min*60.0)
    wca1, th1, gs1 = wind_triangle(tc_true, vy_kt, wind_from, wind_kt)
    d1_nm = (gs1 * (t1_sec/3600.0))
    ff1   = ff_climb
elif alt_delta < 0:  # DESCENT
    rod = max(1.0, float(st.session_state.rod_fpm))
    t1_min = abs(alt_delta) / rod
    t1_sec = round_to_10s(t1_min*60.0)
    wca1, th1, gs1 = wind_triangle(tc_true, float(st.session_state.descent_ref_kt), wind_from, wind_kt)
    d1_nm = (gs1 * (t1_sec/3600.0))
    ff1   = ff_descent
else:  # LEVEL
    t1_sec = 0
    wca1, th1, gs1 = wind_triangle(tc_true, crz_tas, wind_from, wind_kt)
    d1_nm = 0.0
    ff1   = 0.0

burn1 = ff1 * (t1_sec/3600.0)

# Segmento 2: cruzeiro para completar a distância
rem_nm = max(0.0, dist_nm - d1_nm)
wca2, th2, gs2 = wind_triangle(tc_true, crz_tas, wind_from, wind_kt)
if rem_nm > 0 and gs2 > 0:
    t2_sec = round_to_10s( (rem_nm / gs2) * 3600.0 )
    burn2  = crz_ff_tbl * (t2_sec/3600.0)
else:
    t2_sec = 0
    burn2  = 0.0

# Totais
TOTAL_SEC = int(t1_sec + t2_sec)
TOTAL_BURN = _round_tenth(burn1 + burn2)

# Headings/Magnetic
mh1 = apply_var(th1, st.session_state.var_deg, st.session_state.var_is_e)
mh2 = apply_var(th2, st.session_state.var_deg, st.session_state.var_is_e)
mc  = apply_var(tc_true, st.session_state.var_deg, st.session_state.var_is_e)

# Checkpoints de X minutos (default 2 min)
check_every = int(checkpoints)
check_rows = []
acc_sec = 0
for k in range(1, 100):  # limite superior arbitrário
    acc_sec = k*check_every*60
    if acc_sec > TOTAL_SEC: break
    if acc_sec <= t1_sec:
        # ainda no seg1
        dist_at = gs1 * (acc_sec/3600.0)
        burn_at = ff1 * (acc_sec/3600.0)
    else:
        dist_at = d1_nm + gs2 * ((acc_sec - t1_sec)/3600.0)
        burn_at = burn1 + crz_ff_tbl * ((acc_sec - t1_sec)/3600.0)
    check_rows.append({
        "T+ (min)": k*check_every,
        "Dist desde início (nm)": round(dist_at,1),
        "EFOB (L)": max(0.0, _round_tenth(st.session_state.carry_efoB_L - burn_at))
    })

# ===== Saída =====

st.markdown("---")
st.subheader("Resultados da Perna")

cA,cB,cC = st.columns(3)
with cA:
    st.metric("Vy (kt)", _round_unit(vy_kt))
    st.metric("ROC @ início (ft/min)", _round_unit(roc_fpm))
with cB:
    st.metric("GS climb/level/cruise (kt)", f"{_round_unit(gs1)} / {(_round_unit(gs2) if t1_sec==0 else '—')} / {_round_unit(gs2)}")
    st.metric("TAS climb/cruise (kt)", f"{_round_unit(vy_kt)} / {_round_unit(crz_tas)}")
with cC:
    isa_dev = st.session_state.temp_c - isa_temp(pressure_alt(float(alt_ini), st.session_state.qnh))
    st.metric("ISA dev @ início (°C)", int(round(isa_dev)))

st.markdown(f"**Cursos/Heading** — TC={_round_angle(tc_true)}°T • TH1={_round_angle(th1)}°T • MH1={_round_angle(mh1)}°M • TH2={_round_angle(th2)}°T • MH2={_round_angle(mh2)}°M • MC={_round_angle(mc)}°M")

col1, col2, col3, col4 = st.columns([2,1,1,1])
col1.write("Segmento"); col2.write("Tempo"); col3.write("Dist (nm)"); col4.write("Burn (L)")
seg1_txt = "Climb" if alt_delta>0 else ("Descent" if alt_delta<0 else "Level")
col1.write(seg1_txt); col2.write(mmss_from_seconds(int(t1_sec))); col3.write(f"{d1_nm:.1f}"); col4.write(f"{burn1:.1f}")
col1.write("Cruise");  col2.write(mmss_from_seconds(int(t2_sec))); col3.write(f"{rem_nm:.1f}"); col4.write(f"{burn2:.1f}")

st.markdown("---")
st.markdown(f"**Totais** — ETE {hhmmss_from_seconds(TOTAL_SEC)} • Burn {TOTAL_BURN:.1f} L")

# EFOB simples
efob_start = float(st.session_state.carry_efoB_L)
efob_end = max(0.0, _round_tenth(efob_start - (burn1+burn2)))
st.markdown(f"**EFOB** — Start {efob_start:.1f} L → End {efob_end:.1f} L")

# Checkpoints
st.subheader(f"Checkpoints de {check_every} min (distância cumulativa)")
st.dataframe(check_rows, use_container_width=True)

# ===== Botão: usar como próxima perna =====
if st.button("Usar estes resultados como ponto de partida da próxima perna", type="primary"):
    st.session_state.carry_alt_ft = float(alt_fim)
    st.session_state.carry_efoB_L = float(efob_end)
    st.session_state.carry_time_sec = int(st.session_state.carry_time_sec + TOTAL_SEC)
    st.session_state.carry_dist_nm  = float(st.session_state.carry_dist_nm + dist_nm)
    st.success("Valores transpostos para a próxima perna (altitude final, EFOB, totais cumulativos).")

# Resumo cumulativo (opcional)
st.markdown("---")
st.subheader("Cumulativos da Sessão")
st.write(f"Tempo total: {hhmmss_from_seconds(int(st.session_state.carry_time_sec))} • Dist total: {st.session_state.carry_dist_nm:.1f} nm • EFOB atual: {st.session_state.carry_efoB_L:.1f} L")




