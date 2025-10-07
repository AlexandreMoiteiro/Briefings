# app.py — NAVLOG Performance-Only
# Reqs: streamlit, pytz (opcional)
# \- Removeu: rota/altitudes/holds/vento/NAVAIDs/JSON/PDF/Relatório
# \- Mantém: Cabeçalho + cálculo de performance simples por perna

import streamlit as st
import datetime as dt
import pytz
import unicodedata
from typing import Optional, Tuple, Dict
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

def apply_var(true_deg, var_deg, east_is_negative=False):
    return (true_deg - var_deg if east_is_negative else true_deg + var_deg) % 360

# ===== Aerodromes (ex.) =====
AEROS: Dict[str, Dict[str, float]] = {
 "LPSO":{"elev":390,"freq":"119.805"},
 "LPEV":{"elev":807,"freq":"122.705"},
 "LPCB":{"elev":1251,"freq":"122.300"},
 "LPCO":{"elev":587,"freq":"118.405"},
 "LPVZ":{"elev":2060,"freq":"118.305"},
}

def aero_elev(icao): return int(AEROS.get(icao,{}).get("elev",0))

# ===== Atmosfera =====
def isa_temp(pa_ft):
    return 15.0 - 2.0*(pa_ft/1000.0)

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

# Cruise lookup (igual ao teu, sem vento)
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

# ROC & Vy (sem vento)
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

# ===== Sessão (defaults básicos) =====
def ensure(k, v):
    if k not in st.session_state: st.session_state[k] = v

ensure("aircraft","P208"); ensure("registration","CS-ECC"); ensure("callsign","RVP")
ensure("student","AMOIT"); ensure("lesson",""); ensure("instrutor","")
ensure("dept","LPSO"); ensure("arr","LPEV"); ensure("altn","LPCB")
ensure("startup","")
ensure("qnh",1013); ensure("cruise_alt",4000)
ensure("temp_c",15); ensure("var_deg",1); ensure("var_is_e",False)
ensure("rpm_climb",2250); ensure("rpm_cruise",2000)
ensure("descent_ff",15.0); ensure("rod_fpm",700); ensure("start_fuel",85.0)
ensure("cruise_ref_kt",90); ensure("descent_ref_kt",65)
# Taxi (só para resumo)
ensure("taxi_min",15); ensure("taxi_ff_lph",20.0)

# =========================================================
# Cabeçalho / Atmosfera / Perf (única secção de edição)
# =========================================================
st.title("NAVLOG — Performance (sem vento/rota)")
with st.form("hdr_perf_form", clear_on_submit=False):
    st.subheader("Identificação e Parâmetros")
    c1,c2,c3 = st.columns(3)
    with c1:
        f_aircraft = st.text_input("Aircraft", st.session_state.aircraft)
        f_registration = st.selectbox("Registration",
                                      ["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"],
                                      index=["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"].index(st.session_state.registration))
        f_callsign = st.text_input("Callsign", st.session_state.callsign)
        f_startup  = st.text_input("Startup (HH:MM ou HH:MM:SS)", st.session_state.startup)
    with c2:
        f_student = st.text_input("Student", st.session_state.student)
        f_lesson  = st.text_input("Lesson (ex: 12)", st.session_state.lesson)
        f_instrut = st.text_input("Instrutor", st.session_state.instrutor)
    with c3:
        f_dep = st.selectbox("Departure", list(AEROS.keys()), index=list(AEROS.keys()).index(st.session_state.dept))
        f_arr = st.selectbox("Arrival",  list(AEROS.keys()), index=list(AEROS.keys()).index(st.session_state.arr))
        f_altn= st.selectbox("Alternate",list(AEROS.keys()), index=list(AEROS.keys()).index(st.session_state.altn))

    st.markdown("---")
    c4,c5,c6 = st.columns(3)
    with c4:
        f_qnh  = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh), step=1)
        f_crz  = st.number_input("Cruise Altitude (ft)", 0, 14000, int(st.session_state.cruise_alt), step=50)
    with c5:
        f_oat  = st.number_input("OAT (°C)", -40, 50, int(st.session_state.temp_c), step=1)
        f_var  = st.number_input("Mag Variation (°)", 0, 30, int(st.session_state.var_deg), step=1)
        f_varE = (st.selectbox("Variação E/W", ["W","E"], index=(1 if st.session_state.var_is_e else 0))=="E")
    with c6:
        f_rpm_cl = st.number_input("Climb RPM (AFM)", 1800, 2388, int(st.session_state.rpm_climb), step=10)
        f_rpm_cr = st.number_input("Cruise RPM (AFM)", 1800, 2388, int(st.session_state.rpm_cruise), step=10)

    c7,c8,c9 = st.columns(3)
    with c7:
        f_ff_ds  = st.number_input("Descent FF (L/h)", 0.0, 30.0, float(st.session_state.descent_ff), step=0.1)
    with c8:
        f_rod    = st.number_input("ROD (ft/min)", 200, 1500, int(st.session_state.rod_fpm), step=10)
        f_fuel0  = st.number_input("Fuel inicial (EFOB_START) [L]", 0.0, 1000.0, float(st.session_state.start_fuel), step=0.1)
    with c9:
        f_spd_cr  = st.number_input("Cruise speed (kt)", 40, 140, int(st.session_state.cruise_ref_kt), step=1)
        f_spd_ds  = st.number_input("Descent speed (kt)", 40, 120, int(st.session_state.descent_ref_kt), step=1)

    submitted = st.form_submit_button("Aplicar parâmetros")
    if submitted:
        st.session_state.aircraft=f_aircraft; st.session_state.registration=f_registration; st.session_state.callsign=f_callsign
        st.session_state.startup=f_startup; st.session_state.student=f_student; st.session_state.lesson=f_lesson; st.session_state.instrutor=f_instrut
        st.session_state.dept=f_dep; st.session_state.arr=f_arr; st.session_state.altn=f_altn
        st.session_state.qnh=f_qnh; st.session_state.cruise_alt=f_crz; st.session_state.temp_c=f_oat
        st.session_state.var_deg=f_var; st.session_state.var_is_e=f_varE
        st.session_state.rpm_climb=f_rpm_cl; st.session_state.rpm_cruise=f_rpm_cr
        st.session_state.descent_ff=f_ff_ds; st.session_state.rod_fpm=f_rod; st.session_state.start_fuel=f_fuel0
        st.session_state.cruise_ref_kt=f_spd_cr; st.session_state.descent_ref_kt=f_spd_ds
        st.success("Parâmetros aplicados.")

# =========================================================
# Cálculos de performance — entrada simples de perna (TC & Dist)
# =========================================================

st.subheader("Perna (entrada manual)")
colA,colB,colC = st.columns(3)
with colA:
    tc_true = st.number_input("True Course (°T)", min_value=0.0, max_value=359.9, value=0.0, step=0.1)
with colB:
    dist_nm = st.number_input("Distância (nm)", min_value=0.0, value=0.0, step=0.1)
with colC:
    # Altitudes de referência para estimar subida/descida
    dep_alt_ft = _round_alt(aero_elev(st.session_state.dept))
    arr_alt_ft = _round_alt(aero_elev(st.session_state.arr))
    a1 = st.number_input("Alt início (ft)", min_value=0.0, step=50.0, value=float(dep_alt_ft))
    a2 = st.number_input("Alt fim (ft)",    min_value=0.0, step=50.0, value=float(arr_alt_ft))

# ===== Cálculos base =====

def pressure_alt(alt_ft, qnh_hpa): return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0

pa_start  = pressure_alt(a1, st.session_state.qnh)
vy_kt     = vy_interp_enroute(pa_start)
roc_fpm   = roc_interp_enroute(pa_start, st.session_state.temp_c)

# TAS/FF
_ , ff_climb  = cruise_lookup(a1 + 0.5*max(0.0, st.session_state.cruise_alt-a1), int(st.session_state.rpm_climb),  st.session_state.temp_c)
_ , ff_cruise = cruise_lookup(pressure_alt(st.session_state.cruise_alt, st.session_state.qnh), int(st.session_state.rpm_cruise), st.session_state.temp_c)
ff_descent    = float(st.session_state.descent_ff)

# Heading/var (sem vento)
th_true = tc_true  # sem vento => TH = TC
mc_mag  = apply_var(tc_true, st.session_state.var_deg, st.session_state.var_is_e)
mh_mag  = apply_var(th_true, st.session_state.var_deg, st.session_state.var_is_e)

# Subida
climb_ft   = max(0.0, st.session_state.cruise_alt - a1)
climb_min  = climb_ft / max(roc_fpm,1e-6)
climb_sec  = round_to_10s(climb_min*60.0)
climb_nm   = (vy_kt * (climb_sec/60.0)) / 60.0
climb_burn = ff_climb * (climb_sec/3600.0)

# Descida
desc_ft   = max(0.0, st.session_state.cruise_alt - a2)
rod_fpm   = float(st.session_state.rod_fpm)
desc_min  = desc_ft / max(rod_fpm,1e-6)
desc_sec  = round_to_10s(desc_min*60.0)
desc_nm   = (st.session_state.descent_ref_kt * (desc_sec/60.0)) / 60.0
desc_burn = ff_descent * (desc_sec/3600.0)

# Cruzeiro (restante distância)
rem_nm = max(0.0, dist_nm - (climb_nm + desc_nm))
# TAS/FF efectivas em cruzeiro a partir da tabela
crz_tas, crz_ff = cruise_lookup(pressure_alt(st.session_state.cruise_alt, st.session_state.qnh), int(st.session_state.rpm_cruise), st.session_state.temp_c)
crz_tas = st.session_state.cruise_ref_kt or crz_tas  # permitir override
cruise_sec = round_to_10s( (rem_nm / max(crz_tas,1e-6)) * 3600.0 ) if rem_nm > 0 else 0
cruise_burn = crz_ff * (cruise_sec/3600.0) if rem_nm > 0 else 0.0

# Ajuste: se subida+descida > distância, avisar
warn_text = ""
if (climb_nm + desc_nm) > dist_nm and dist_nm > 0:
    warn_text = "⚠️ Distância insuficiente para cruzeiro; tempos de subida/descida mostrados independentes da distância."

# Totais
TOTAL_SEC = int(climb_sec + cruise_sec + desc_sec)
TOTAL_BURN = _round_tenth(climb_burn + cruise_burn + desc_burn)

# ===== Saída =====

st.markdown("---")
st.subheader("Resultados de Performance")
if warn_text:
    st.warning(warn_text)

cA,cB,cC = st.columns(3)
with cA:
    st.metric("Vy (kt)", _round_unit(vy_kt))
    st.metric("ROC @ início (ft/min)", _round_unit(roc_fpm))
with cB:
    st.metric("TAS climb/cruise/descent", f"{_round_unit(vy_kt)} / {_round_unit(crz_tas)} / {_round_unit(st.session_state.descent_ref_kt)} kt")
    st.metric("FF climb/cruise/descent", f"{_round_unit(ff_climb)} / {_round_unit(crz_ff)} / {_round_unit(ff_descent)} L/h")
with cC:
    isa_dev = st.session_state.temp_c - isa_temp(pressure_alt(float(a1), st.session_state.qnh))
    st.metric("ISA dev @ início (°C)", int(round(isa_dev)))

st.markdown(f"**Rumo/Curso** — TC={_round_angle(tc_true)}°T • TH={_round_angle(th_true)}°T • MC={_round_angle(mc_mag)}°M • MH={_round_angle(mh_mag)}°M")

st.markdown("\n**Fases**")
col1, col2, col3, col4 = st.columns([2,1,1,1])
col1.write("Fase"); col2.write("Tempo"); col3.write("Dist (nm)"); col4.write("Burn (L)")
col1.write("Subida"); col2.write(mmss_from_seconds(int(climb_sec))); col3.write(f"{climb_nm:.1f}"); col4.write(f"{climb_burn:.1f}")
col1.write("Cruzeiro"); col2.write(mmss_from_seconds(int(cruise_sec))); col3.write(f"{rem_nm:.1f}"); col4.write(f"{cruise_burn:.1f}")
col1.write("Descida"); col2.write(mmss_from_seconds(int(desc_sec))); col3.write(f"{desc_nm:.1f}"); col4.write(f"{desc_burn:.1f}")

st.markdown("---")
st.markdown(f"**Totais** — ETE {hhmmss_from_seconds(TOTAL_SEC)} • Burn {_round_tenth(TOTAL_BURN)} L")

# EFOB simples (sem taxi/holds)
efob_start = float(st.session_state.start_fuel)
efob_end = max(0.0, _round_tenth(efob_start - TOTAL_BURN))
st.markdown(f"**EFOB** — Start {efob_start:.1f} L → End {efob_end:.1f} L")





