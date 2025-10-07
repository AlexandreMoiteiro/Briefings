# app.py — NAVLOG Performance v6 (AFM Tabelas ↔ Manual)
# Reqs: streamlit
# - Modo de performance selecionável: **AFM (tabelas)** ou **Manual**
# - AFM: usa tabelas (ROC/Vy/CRUISE) com correção por PA/OAT; inputs de RPM para climb/cruise
# - Manual: TAS/FF fixos (climb/cruise)
# - TOC/TOD é NOVO FIX; checkpoints reiniciam; ETO/EFOB por segmento
# - Botão “Construir próxima perna” mantém as anteriores e pré‑preenche a seguinte

import streamlit as st
import datetime as dt
import math
from typing import Optional, Tuple

st.set_page_config(page_title="NAVLOG — Performance v6", layout="wide", initial_sidebar_state="collapsed")

# ===== Helpers =====

def round_to_10s(sec: float) -> int:
    if sec <= 0: return 0
    s = int(round(sec/10.0)*10)
    return max(s, 10)

def mmss_from_seconds(tsec: int) -> str:
    m = tsec // 60; s = tsec % 60
    return f"{m:02d}:{s:02d}"

def hhmmss_from_seconds(tsec: int) -> str:
    h = tsec // 3600; m = (tsec % 3600)//60; s = tsec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def _r_angle(x: float) -> int: return int(round(float(x))) % 360

def _r_unit(x: float) -> int: return int(round(float(x)))

def _r_tenth(x: float) -> float: return round(float(x), 1)

def wrap360(x: float) -> float:
    x = math.fmod(float(x), 360.0)
    return x + 360.0 if x < 0 else x

def angle_diff(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0

def apply_var(true_deg: float, var_deg: float, east_is_negative: bool=False) -> float:
    return wrap360(true_deg - var_deg if east_is_negative else true_deg + var_deg)

from math import sin, asin, radians, degrees

def wind_triangle(tc_deg: float, tas_kt: float, wind_from_deg: float, wind_kt: float):
    if tas_kt <= 0: return 0.0, wrap360(tc_deg), 0.0
    delta = radians(angle_diff(wind_from_deg, tc_deg))
    cross = wind_kt * sin(delta)
    s = max(-1.0, min(1.0, cross/max(tas_kt,1e-9)))
    wca = degrees(asin(s))
    th  = wrap360(tc_deg + wca)
    gs  = max(0.0, tas_kt*math.cos(radians(wca)) - wind_kt*math.cos(delta))
    return wca, th, gs

# ===== Tabelas AFM (Tecnam P2008 simplificado) =====
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

def isa_temp(pa_ft): return 15.0 - 2.0*(pa_ft/1000.0)

def pressure_alt(alt_ft: float, qnh_hpa: float) -> float:
    return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0

def clamp(v,lo,hi): return max(lo,min(hi,v))

def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

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

# ===== Estado =====

def ensure(k, v):
    if k not in st.session_state: st.session_state[k] = v

ensure("mode", "AFM")  # AFM | Manual
ensure("mag_var", 1); ensure("mag_is_e", False)
ensure("qnh", 1013); ensure("oat", 15)
ensure("start_clock", "")
# AFM inputs
ensure("rpm_climb", 2250); ensure("rpm_cruise", 2000)
# Manual inputs
ensure("tas_climb", 70); ensure("tas_cruise", 85)
ensure("ff_climb_lph", 20.0); ensure("ff_cruise_lph", 18.0); ensure("ff_descent_lph", 15.0)
# comum
ensure("rod_fpm", 700)
ensure("legs", [])
ensure("carry_alt", 0.0)
ensure("carry_efob", 85.0)

# ===== Cabeçalho =====
st.title("NAVLOG — Performance v6 (AFM tabelas ou Manual)")
with st.form("hdr", clear_on_submit=False):
    m1,m2,m3 = st.columns([1.2,1,1])
    with m1:
        st.session_state.mode = st.radio("Modo de performance", ["AFM","Manual"], index=(0 if st.session_state.mode=="AFM" else 1), horizontal=True)
        st.session_state.qnh = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh))
        st.session_state.oat = st.number_input("OAT (°C)", -40, 50, int(st.session_state.oat))
        st.session_state.start_clock = st.text_input("Hora off-blocks (HH:MM) — opcional", st.session_state.start_clock)
    with m2:
        st.session_state.mag_var = st.number_input("Mag Var (°)", 0, 30, int(st.session_state.mag_var))
        st.session_state.mag_is_e = st.selectbox("Variação E/W", ["W","E"], index=(1 if st.session_state.mag_is_e else 0))=="E"
        st.session_state.rod_fpm = st.number_input("ROD (ft/min)", 100, 2000, int(st.session_state.rod_fpm))
    with m3:
        if st.session_state.mode=="AFM":
            st.session_state.rpm_climb = st.number_input("Climb RPM (AFM)", 1800, 2388, int(st.session_state.rpm_climb), step=10)
            st.session_state.rpm_cruise = st.number_input("Cruise RPM (AFM)", 1800, 2388, int(st.session_state.rpm_cruise), step=10)
        else:
            st.session_state.tas_climb = st.number_input("TAS Climb (kt)", 40, 120, int(st.session_state.tas_climb))
            st.session_state.tas_cruise = st.number_input("TAS Cruise (kt)", 40, 140, int(st.session_state.tas_cruise))
            st.session_state.ff_climb_lph = st.number_input("FF Climb (L/h)", 0.0, 40.0, float(st.session_state.ff_climb_lph), step=0.1)
            st.session_state.ff_cruise_lph = st.number_input("FF Cruise (L/h)", 0.0, 40.0, float(st.session_state.ff_cruise_lph), step=0.1)
        st.session_state.ff_descent_lph = st.number_input("FF Descent (L/h)", 0.0, 40.0, float(st.session_state.ff_descent_lph), step=0.1)
        st.session_state.carry_efob = st.number_input("EFOB atual (L)", 0.0, 500.0, float(st.session_state.carry_efob), step=0.1)
        st.session_state.carry_alt = st.number_input("Alt atual (ft)", 0.0, 30000.0, float(st.session_state.carry_alt), step=50.0)
    st.form_submit_button("Aplicar parâmetros")

# ===== Entrada da NOVA perna =====
st.subheader("Perna atual — entrada")
a1,a2,a3,a4 = st.columns(4)
with a1:
    TC = st.number_input("True Course (°T)", 0.0, 359.9, 90.0, step=0.1)
    Dist = st.number_input("Distância total (nm)", 0.0, 500.0, 10.0, step=0.1)
with a2:
    Alt0 = st.number_input("Alt início (ft)", 0.0, 30000.0, float(st.session_state.carry_alt), step=50.0)
    Alt1 = st.number_input("Alt alvo (ft)", 0.0, 30000.0, 4000.0, step=50.0)
with a3:
    W_from = st.number_input("Vento FROM (°T)", 0, 360, 180, step=1)
    W_kt   = st.number_input("Vento (kt)", 0, 150, 15, step=1)
with a4:
    CK = st.number_input("Checkpoints a cada (min)", 1, 10, 2, step=1)

# ===== Performance para esta perna (TAS/FF by mode) =====
pa0 = pressure_alt(Alt0, st.session_state.qnh)
Vy = vy_interp_enroute(pa0)
ROC = roc_interp_enroute(pa0, st.session_state.oat)

if st.session_state.mode=="AFM":
    # climb
    tas_climb = Vy
    ff_climb = cruise_lookup(Alt0 + 0.5*max(0.0, Alt1-Alt0), int(st.session_state.rpm_climb), st.session_state.oat)[1]
    # cruise @ PA alvo
    tas_crz_tab, ff_crz_tab = cruise_lookup(pressure_alt(Alt1, st.session_state.qnh), int(st.session_state.rpm_cruise), st.session_state.oat)
    tas_cruise = tas_crz_tab
    ff_cruise  = ff_crz_tab
else:
    tas_climb = float(st.session_state.tas_climb)
    ff_climb  = float(st.session_state.ff_climb_lph)
    tas_cruise = float(st.session_state.tas_cruise)
    ff_cruise  = float(st.session_state.ff_cruise_lph)

ff_descent = float(st.session_state.ff_descent_lph)

# ===== Headings & GS =====
_, TH_climb, GS_climb = wind_triangle(TC, tas_climb, W_from, W_kt)
_, TH_cruise, GS_cruise = wind_triangle(TC, tas_cruise, W_from, W_kt)
MH_climb  = apply_var(TH_climb, st.session_state.mag_var, st.session_state.mag_is_e)
MH_cruise = apply_var(TH_cruise, st.session_state.mag_var, st.session_state.mag_is_e)

# ===== Segmentos =====
profile = "LEVEL" if abs(Alt1-Alt0) < 1e-6 else ("CLIMB" if Alt1>Alt0 else "DESCENT")
segA = {}; segB = None; reached=False

if profile=="CLIMB":
    t_need_min = (Alt1-Alt0)/max(ROC,1e-6)
    d_need = GS_climb*(t_need_min/60.0)
    if d_need <= Dist:
        reached=True
        tA = round_to_10s(t_need_min*60.0)
        segA = {"name":"Climb → TOC","TH":TH_climb,"MH":MH_climb,"GS":GS_climb,"TAS":tas_climb,
                "time_sec":tA,"dist_nm":d_need,"burn_L":ff_climb*(tA/3600.0),"alt0":Alt0,"alt1":Alt1,"ff":ff_climb}
        rem = max(0.0, Dist-d_need)
        if rem>0:
            tB = round_to_10s((rem/max(GS_cruise,1e-6))*3600.0)
            segB = {"name":"Cruise (após TOC)","TH":TH_cruise,"MH":MH_cruise,"GS":GS_cruise,"TAS":tas_cruise,
                    "time_sec":tB,"dist_nm":rem,"burn_L":ff_cruise*(tB/3600.0),"alt0":Alt1,"alt1":Alt1,"ff":ff_cruise}
        END_ALT = Alt1
    else:
        tA = round_to_10s((Dist/max(GS_climb,1e-6))*3600.0)
        gained = ROC*(tA/60.0)
        END_ALT = Alt0 + gained
        segA = {"name":"Climb (não atinge)","TH":TH_climb,"MH":MH_climb,"GS":GS_climb,"TAS":tas_climb,
                "time_sec":tA,"dist_nm":Dist,"burn_L":ff_climb*(tA/3600.0),"alt0":Alt0,"alt1":END_ALT,"ff":ff_climb}
elif profile=="DESCENT":
    _, TH_desc, GS_desc = wind_triangle(TC, max(tas_cruise-20,40), W_from, W_kt)
    MH_desc = apply_var(TH_desc, st.session_state.mag_var, st.session_state.mag_is_e)
    t_need_min = (Alt0-Alt1)/max(float(st.session_state.rod_fpm),1e-6)
    d_need = GS_desc*(t_need_min/60.0)
    if d_need <= Dist:
        reached=True
        tA = round_to_10s(t_need_min*60.0)
        segA = {"name":"Descent → TOD","TH":TH_desc,"MH":MH_desc,"GS":GS_desc,"TAS":max(tas_cruise-20,40),
                "time_sec":tA,"dist_nm":d_need,"burn_L":ff_descent*(tA/3600.0),"alt0":Alt0,"alt1":Alt1,"ff":ff_descent}
        rem = max(0.0, Dist-d_need)
        if rem>0:
            tB = round_to_10s((rem/max(GS_cruise,1e-6))*3600.0)
            segB = {"name":"Cruise (após TOD)","TH":TH_cruise,"MH":MH_cruise,"GS":GS_cruise,"TAS":tas_cruise,
                    "time_sec":tB,"dist_nm":rem,"burn_L":ff_cruise*(tB/3600.0),"alt0":Alt1,"alt1":Alt1,"ff":ff_cruise}
        END_ALT = Alt1
    else:
        tA = round_to_10s((Dist/max(GS_desc,1e-6))*3600.0)
        lost = float(st.session_state.rod_fpm)*(tA/60.0)
        END_ALT = max(0.0, Alt0 - lost)
        segA = {"name":"Descent (não atinge)","TH":TH_desc,"MH":MH_desc,"GS":GS_desc,"TAS":max(tas_cruise-20,40),
                "time_sec":tA,"dist_nm":Dist,"burn_L":ff_descent*(tA/3600.0),"alt0":Alt0,"alt1":END_ALT,"ff":ff_descent}
else:
    tA = round_to_10s((Dist/max(GS_cruise,1e-6))*3600.0)
    END_ALT = Alt0
    segA = {"name":"Level","TH":TH_cruise,"MH":MH_cruise,"GS":GS_cruise,"TAS":tas_cruise,
            "time_sec":tA,"dist_nm":Dist,"burn_L":ff_cruise*(tA/3600.0),"alt0":Alt0,"alt1":END_ALT,"ff":ff_cruise}

segments = [segA] + ([segB] if segB else [])
TOTAL_SEC = sum(int(s['time_sec']) for s in segments)
TOTAL_BURN = _r_tenth(sum(float(s['burn_L']) for s in segments))

# ===== Timeline ETO/EFOB =====
start_clock = st.session_state.start_clock.strip()
clock = None
if start_clock:
    try:
        h,m = map(int, start_clock.split(":"))
        clock = dt.datetime.combine(dt.date.today(), dt.time(hour=h, minute=m))
    except Exception:
        clock = None

def advance_clock(clock, t_sec):
    if clock is None: return None
    return clock + dt.timedelta(seconds=int(t_sec))

# ===== Apresentação =====
st.markdown("---")
st.subheader("Resultados da Perna")

# Segmento 1
st.markdown(f"### Segmento 1 — {segA['name']}")
s1a,s1b,s1c,s1d = st.columns(4)
s1a.metric("Alt ini→fim (ft)", f"{int(round(segA['alt0']))} → {int(round(segA['alt1']))}")
s1b.metric("TH/MH (°)", f"{_r_angle(segA['TH'])}T / { _r_angle(segA['MH']) }M")
s1c.metric("GS/TAS (kt)", f"{_r_unit(segA['GS'])} / {_r_unit(segA['TAS'])}")
s1d.metric("FF (L/h)", f"{_r_unit(segA['ff'])}")
s1e,s1f,s1g,s1h = st.columns(4)
s1e.metric("Tempo", mmss_from_seconds(int(segA['time_sec'])))
s1f.metric("Dist (nm)", f"{segA['dist_nm']:.1f}")
s1g.metric("Burn (L)", f"{_r_tenth(segA['burn_L']):.1f}")
ETO1 = advance_clock(clock, segA['time_sec'])
if ETO1: s1h.metric("ETO", ETO1.strftime('%H:%M'))

# Marcador
if segB:
    label = "TOC" if profile=="CLIMB" else "TOD"
    st.info(f"{label} — {mmss_from_seconds(int(segA['time_sec']))} • {segA['dist_nm']:.1f} nm desde o início")

# Segmento 2
if segB:
    st.markdown(f"### Segmento 2 — {segB['name']}")
    s2a,s2b,s2c,s2d = st.columns(4)
    s2a.metric("Alt ini→fim (ft)", f"{int(round(segB['alt0']))} → {int(round(segB['alt1']))}")
    s2b.metric("TH/MH (°)", f"{_r_angle(segB['TH'])}T / { _r_angle(segB['MH']) }M")
    s2c.metric("GS/TAS (kt)", f"{_r_unit(segB['GS'])} / {_r_unit(segB['TAS'])}")
    s2d.metric("FF (L/h)", f"{_r_unit(segB['ff'])}")
    s2e,s2f,s2g,s2h = st.columns(4)
    s2e.metric("Tempo", mmss_from_seconds(int(segB['time_sec'])))
    s2f.metric("Dist (nm)", f"{segB['dist_nm']:.1f}")
    s2g.metric("Burn (L)", f"{_r_tenth(segB['burn_L']):.1f}")
    if ETO1:
        ETO2 = advance_clock(ETO1, segB['time_sec'])
        s2h.metric("ETO", ETO2.strftime('%H:%M'))

# Totais
st.markdown("---")
st.markdown(f"**Totais** — ETE {hhmmss_from_seconds(TOTAL_SEC)} • Burn {TOTAL_BURN:.1f} L")

# ===== Checkpoints (por segmento, T+=0 após marcador) =====

def checkpoints(seg: dict, every_min: int, base_clock: Optional[dt.datetime], efob_start: float):
    rows=[]; t=0
    while t + every_min*60 <= seg['time_sec']:
        t += every_min*60
        d = seg['GS']*(t/3600.0)
        burn = seg['ff']*(t/3600.0)
        eto = (base_clock + dt.timedelta(seconds=t)).strftime('%H:%M') if base_clock else ""
        efob = max(0.0, _r_tenth(efob_start - burn))
        rows.append({"T+ (min)": int(t/60), "Dist (nm)": round(d,1), "GS (kt)": _r_unit(seg['GS']), "ETO": eto, "EFOB (L)": efob})
    return rows

st.subheader("Checkpoints")
EF0 = float(st.session_state.carry_efob)
base1 = advance_clock(clock, 0) if clock else None
cpA = checkpoints(segA, int(CK), base1, EF0)
st.markdown("**Até {}**".format("TOC" if segB and profile=="CLIMB" else "TOD" if segB else "fim"))
st.dataframe(cpA, use_container_width=True)

if segB:
    EF1 = max(0.0, _r_tenth(EF0 - segA['burn_L']))
    base2 = advance_clock(clock, segA['time_sec']) if clock else None
    cpB = checkpoints(segB, int(CK), base2, EF1)
    st.markdown("**Após {} (T+ reinicia)**".format("TOC" if profile=="CLIMB" else "TOD"))
    st.dataframe(cpB, use_container_width=True)

# ===== EFOB final =====
EF_END = max(0.0, _r_tenth(float(st.session_state.carry_efob) - sum(s['burn_L'] for s in segments)))
st.markdown(f"**EFOB** — Start {float(st.session_state.carry_efob):.1f} L → End {EF_END:.1f} L")

# ===== Construir próxima perna =====
if st.button("➕ Construir próxima perna (usar fim desta como início)", type="primary"):
    st.session_state.legs.append({
        "TC": TC, "Dist": Dist, "Alt0": segments[0]['alt0'], "Alt1": END_ALT,
        "W": (W_from, W_kt), "CK": CK, "segments": segments,
        "totals": {"sec": TOTAL_SEC, "burn": TOTAL_BURN}, "end_efob": EF_END,
        "mode": st.session_state.mode,
        "oat": st.session_state.oat, "qnh": st.session_state.qnh,
        "rpm": (st.session_state.rpm_climb, st.session_state.rpm_cruise) if st.session_state.mode=="AFM" else None,
        "tasff": (tas_climb, tas_cruise, ff_climb, ff_cruise),
    })
    st.session_state.carry_alt = float(END_ALT)
    st.session_state.carry_efob = float(EF_END)
    st.experimental_rerun()

# ===== Pernas construídas =====
if st.session_state.legs:
    st.markdown("---")
    st.subheader("Pernas já construídas")
    for i,leg in enumerate(st.session_state.legs, start=1):
        st.markdown(f"### Perna {i} — TC {leg['TC']:.0f}° • {leg['Dist']:.1f} nm • Alt fim {int(round(leg['Alt1']))} ft • EFOB fim {leg['end_efob']:.1f} L • Modo {leg['mode']}")
        for j,s in enumerate(leg['segments'], start=1):
            st.markdown(f"**Segmento {j} — {s['name']}**  | Alt {s['alt0']:.0f}→{s['alt1']:.0f} ft | TH/MH {_r_angle(s['TH'])}/{_r_angle(s['MH'])} | GS/TAS {_r_unit(s['GS'])}/{_r_unit(s['TAS'])} kt | Tempo {mmss_from_seconds(int(s['time_sec']))} | Dist {s['dist_nm']:.1f} nm | Burn {_r_tenth(s['burn_L']):.1f} L")
        st.divider()



