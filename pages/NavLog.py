
# app.py — NAVLOG Performance v3
# Reqs: streamlit
# - Entrada manual por perna: TC, Dist (nm), Alt início, Alt alvo, Vento FROM/kt
# - Vento usado para TH/MH e GS; var magnética aplicada
# - Se a distância for insuficiente para atingir o alvo, calcula altitude ao fim da perna
# - Se atingir o alvo, separa explicitamente em TOC/TOD como "fix" e apresenta 2 segmentos
# - Checkpoints reiniciam em 0 ao passar no TOC/TOD (são como novo fix)
# - Botão "Adicionar perna" acumula no histórico; as anteriores ficam visíveis

import streamlit as st
import math
from typing import Optional, Tuple

st.set_page_config(page_title="NAVLOG — Performance v3", layout="wide", initial_sidebar_state="collapsed")

# ===== Helpers =====

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

def clamp(v, lo, hi): return max(lo, min(hi, v))

def wrap360(x: float) -> float:
    x = math.fmod(x, 360.0)
    return x + 360.0 if x < 0 else x

def angle_diff(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0

def wind_triangle(tc_deg: float, tas_kt: float, wind_from_deg: float, wind_kt: float):
    if tas_kt <= 0: return 0.0, wrap360(tc_deg), 0.0
    delta = math.radians(angle_diff(wind_from_deg, tc_deg))
    cross = wind_kt * math.sin(delta)
    s = max(-1.0, min(1.0, cross/max(tas_kt,1e-9)))
    wca = math.degrees(math.asin(s))
    th  = wrap360(tc_deg + wca)
    gs  = max(0.0, tas_kt*math.cos(math.radians(wca)) - wind_kt*math.cos(delta))
    return wca, th, gs

def apply_var(true_deg, var_deg, east_is_negative=False):
    return wrap360(true_deg - var_deg if east_is_negative else true_deg + var_deg)

# ===== Atmosfera/Perf (P2008 simplificado) =====
ROC_ENROUTE = {0:{-25:981,0:835,25:704,50:586},2000:{-25:870,0:726,25:597,50:481},4000:{-25:759,0:617,25:491,50:377},6000:{-25:648,0:509,25:385,50:273},8000:{-25:538,0:401,25:279,50:170},10000:{-25:428,0:294,25:174,50:66},12000:{-25:319,0:187,25:69,50:-37},14000:{-25:210,0:80,25:-35,50:-139}}
ROC_FACTOR = 0.90
VY_ENROUTE = {0:67,2000:67,4000:67,6000:67,8000:67,10000:67,12000:67,14000:67}
CRUISE = {0:{1800:(82,15.3),1900:(89,17.0),2000:(95,18.7),2100:(101,20.7),2250:(110,24.6),2388:(118,26.9)},2000:{1800:(82,15.3),1900:(88,16.6),2000:(94,17.5),2100:(100,19.9),2250:(109,23.5)},4000:{1800:(81,15.1),1900:(88,16.2),2000:(94,17.5),2100:(100,19.2),2250:(108,22.4)},6000:{1800:(81,14.9),1900:(87,15.9),2000:(93,17.1),2100:(99,18.5),2250:(108,21.3)},8000:{1800:(81,14.9),1900:(86,15.6),2000:(92,16.7),2100:(98,18.0),2250:(107,20.4)},10000:{1800:(85,15.4),1900:(91,16.4),2000:(91,16.4),2100:(97,17.5),2250:(106,19.7)}}

def isa_temp(pa_ft): return 15.0 - 2.0*(pa_ft/1000.0)

def pressure_alt(alt_ft, qnh_hpa): return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0

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

ensure("qnh",1013); ensure("temp_c",15)
ensure("var_deg",1); ensure("var_is_e",False)
ensure("rpm_climb",2250); ensure("rpm_cruise",2000)
ensure("descent_ff",15.0); ensure("rod_fpm",700); ensure("start_fuel",85.0)
ensure("cruise_ref_kt",90); ensure("descent_ref_kt",65)
ensure("next_alt_start", 0.0)
ensure("legs_history", [])

# =========================================================
# Parâmetros base
# =========================================================
st.title("NAVLOG — Performance v3 (TOC/TOD como fix + histórico)")
with st.form("hdr", clear_on_submit=False):
    c1,c2,c3 = st.columns(3)
    with c1:
        qnh = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh), step=1)
        oat = st.number_input("OAT (°C)", -40, 50, int(st.session_state.temp_c), step=1)
    with c2:
        var_deg = st.number_input("Mag Variation (°)", 0, 30, int(st.session_state.var_deg), step=1)
        var_is_e = st.selectbox("Variação E/W", ["W","E"], index=(1 if st.session_state.var_is_e else 0))=="E"
    with c3:
        rpm_cl = st.number_input("Climb RPM", 1800, 2388, int(st.session_state.rpm_climb), step=10)
        rpm_cr = st.number_input("Cruise RPM",1800, 2388, int(st.session_state.rpm_cruise), step=10)
        ff_ds  = st.number_input("Descent FF (L/h)", 0.0, 30.0, float(st.session_state.descent_ff), step=0.1)
        rod    = st.number_input("ROD (ft/min)", 200, 1500, int(st.session_state.rod_fpm), step=10)
    if st.form_submit_button("Aplicar"):
        st.session_state.qnh=qnh; st.session_state.temp_c=oat
        st.session_state.var_deg=var_deg; st.session_state.var_is_e=var_is_e
        st.session_state.rpm_climb=rpm_cl; st.session_state.rpm_cruise=rpm_cr
        st.session_state.descent_ff=ff_ds; st.session_state.rod_fpm=rod
        st.success("Parâmetros aplicados.")

# =========================================================
# Nova perna — entrada
# =========================================================
st.subheader("Nova Perna")
cc1,cc2,cc3,cc4 = st.columns(4)
with cc1:
    tc_true = st.number_input("True Course (°T)", 0.0, 359.9, 90.0, step=0.1)
    dist_nm = st.number_input("Distância (nm)", 0.0, 999.9, 10.0, step=0.1)
with cc2:
    alt_start = st.number_input("Alt início (ft)", 0.0, 20000.0, float(st.session_state.next_alt_start), step=50.0)
    alt_target = st.number_input("Alt alvo (ft)", 0.0, 20000.0, 4000.0, step=50.0)
with cc3:
    wind_from = st.number_input("Vento FROM (°T)", 0, 360, 180, step=1)
    wind_kt   = st.number_input("Vento (kt)", 0, 120, 15, step=1)
with cc4:
    ck_every  = st.number_input("Checkpoints a cada (min)", 1, 10, 2, step=1)

# ===== Cálculo da perna =====
pa_start  = pressure_alt(alt_start, st.session_state.qnh)
vy_kt     = vy_interp_enroute(pa_start)
roc_fpm   = roc_interp_enroute(pa_start, st.session_state.temp_c)

_ , ff_climb  = cruise_lookup(alt_start + 0.5*max(0.0, alt_target-alt_start), int(st.session_state.rpm_climb),  st.session_state.temp_c)
crz_tas_tab , crz_ff_tab = cruise_lookup(pressure_alt(alt_target, st.session_state.qnh), int(st.session_state.rpm_cruise), st.session_state.temp_c)
crz_tas = st.session_state.cruise_ref_kt or crz_tas_tab

# Fase 1 (climb/level/descent)
profile = "CLIMB" if alt_target>alt_start+1e-6 else ("DESCENT" if alt_target<alt_start-1e-6 else "LEVEL")
segA = None; segB = None

if profile=="CLIMB":
    _, thA, gsA = wind_triangle(tc_true, vy_kt, wind_from, wind_kt)
    t_need_min = (alt_target - alt_start)/max(roc_fpm,1e-6)
    d_need = gsA * (t_need_min/60.0)
    if d_need <= dist_nm:  # chega ao TOC
        tA = round_to_10s(t_need_min*60.0)
        segA = {"name":"Climb → TOC","TH":thA,"MH":apply_var(thA, st.session_state.var_deg, st.session_state.var_is_e),
                "GS":gsA,"TAS":vy_kt,"time":tA,"dist":d_need,"burn":ff_climb*(tA/3600.0),
                "alt0":alt_start,"alt1":alt_target}
        # segmento B (cruise)
        _, thB, gsB = wind_triangle(tc_true, crz_tas, wind_from, wind_kt)
        rem = max(0.0, dist_nm - d_need)
        tB = round_to_10s( (rem/max(gsB,1e-6))*3600.0 ) if rem>0 else 0
        segB = {"name":"Cruise (após TOC)","TH":thB,"MH":apply_var(thB, st.session_state.var_deg, st.session_state.var_is_e),
                "GS":gsB,"TAS":crz_tas,"time":tB,"dist":rem,"burn":crz_ff_tab*(tB/3600.0),
                "alt0":alt_target,"alt1":alt_target}
        end_alt = alt_target
    else:  # não atinge
        tA = round_to_10s( (dist_nm/max(gsA,1e-6))*3600.0 )
        gained = roc_fpm * (tA/60.0)
        end_alt = alt_start + gained
        segA = {"name":"Climb (não atinge alvo)","TH":thA,"MH":apply_var(thA, st.session_state.var_deg, st.session_state.var_is_e),
                "GS":gsA,"TAS":vy_kt,"time":tA,"dist":dist_nm,"burn":ff_climb*(tA/3600.0),
                "alt0":alt_start,"alt1":end_alt}
elif profile=="DESCENT":
    _, thA, gsA = wind_triangle(tc_true, st.session_state.descent_ref_kt, wind_from, wind_kt)
    t_need_min = (alt_start - alt_target)/max(float(st.session_state.rod_fpm),1e-6)
    d_need = gsA * (t_need_min/60.0)
    if d_need <= dist_nm:  # chega ao TOD
        tA = round_to_10s(t_need_min*60.0)
        segA = {"name":"Descent → TOD","TH":thA,"MH":apply_var(thA, st.session_state.var_deg, st.session_state.var_is_e),
                "GS":gsA,"TAS":st.session_state.descent_ref_kt,"time":tA,"dist":d_need,"burn":st.session_state.descent_ff*(tA/3600.0),
                "alt0":alt_start,"alt1":alt_target}
        # segmento B (cruise)
        _, thB, gsB = wind_triangle(tc_true, crz_tas, wind_from, wind_kt)
        rem = max(0.0, dist_nm - d_need)
        tB = round_to_10s( (rem/max(gsB,1e-6))*3600.0 ) if rem>0 else 0
        segB = {"name":"Cruise (após TOD)","TH":thB,"MH":apply_var(thB, st.session_state.var_deg, st.session_state.var_is_e),
                "GS":gsB,"TAS":crz_tas,"time":tB,"dist":rem,"burn":crz_ff_tab*(tB/3600.0),
                "alt0":alt_target,"alt1":alt_target}
        end_alt = alt_target
    else:
        tA = round_to_10s( (dist_nm/max(gsA,1e-6))*3600.0 )
        lost = float(st.session_state.rod_fpm) * (tA/60.0)
        end_alt = max(0.0, alt_start - lost)
        segA = {"name":"Descent (não atinge alvo)","TH":thA,"MH":apply_var(thA, st.session_state.var_deg, st.session_state.var_is_e),
                "GS":gsA,"TAS":st.session_state.descent_ref_kt,"time":tA,"dist":dist_nm,"burn":st.session_state.descent_ff*(tA/3600.0),
                "alt0":alt_start,"alt1":end_alt}
else:  # LEVEL
    _, thA, gsA = wind_triangle(tc_true, crz_tas, wind_from, wind_kt)
    tA = round_to_10s( (dist_nm/max(gsA,1e-6))*3600.0 )
    end_alt = alt_start
    segA = {"name":"Level","TH":thA,"MH":apply_var(thA, st.session_state.var_deg, st.session_state.var_is_e),
            "GS":gsA,"TAS":crz_tas,"time":tA,"dist":dist_nm,"burn":crz_ff_tab*(tA/3600.0),
            "alt0":alt_start,"alt1":end_alt}

segments = [segA] + ([segB] if segB else [])
TOTAL_SEC = sum(int(s['time']) for s in segments)
TOTAL_BURN = _round_tenth(sum(float(s['burn']) for s in segments))

# ===== Apresentação (separação visual forte) =====
st.markdown("---")
st.subheader("Resultados da Perna")

# Badges topo
colt1,colt2,colt3,colt4 = st.columns(4)
with colt1:
    st.metric("Perfil", "CLIMB" if profile=="CLIMB" else ("DESCENT" if profile=="DESCENT" else "LEVEL"))
with colt2:
    st.metric("GS (seg A)", _round_unit(segA['GS']))
with colt3:
    st.metric("GS (seg B)", _round_unit(segB['GS']) if segB else 0)
with colt4:
    st.metric("Alt fim (ft)", _round_alt(end_alt))

# Cards por segmento
for idx, s in enumerate(segments, start=1):
    st.markdown(f"#### Segmento {idx} — {s['name']}")
    c1,c2,c3,c4,c5,c6 = st.columns(6)
    c1.write(f"Alt: {_round_alt(s['alt0'])}→{_round_alt(s['alt1'])} ft")
    c2.write(f"TH/MH: {_round_angle(s['TH'])}°T / {_round_angle(s['MH'])}°M")
    c3.write(f"GS/TAS: {_round_unit(s['GS'])}/{_round_unit(s['TAS'])} kt")
    c4.write(f"Tempo: {mmss_from_seconds(int(s['time']))}")
    c5.write(f"Dist: {s['dist']:.1f} nm")
    c6.write(f"Burn: {_round_tenth(s['burn']):.1f} L")
    st.divider()

# TOC/TOD claramente assinalado
if segB:
    label = "TOC" if profile=="CLIMB" else "TOD"
    st.success(f"{label} em {mmss_from_seconds(int(segA['time']))} • {segA['dist']:.1f} nm desde o início")

# Totais
st.markdown(f"**Totais** — ETE {hhmmss_from_seconds(TOTAL_SEC)} • Burn {TOTAL_BURN:.1f} L")

# ===== Checkpoints (reiniciam no TOC/TOD) =====
st.markdown("### Checkpoints")

def checkpoints_for(seg, step_min):
    rows=[]; t=0
    while t+step_min*60 <= seg['time']:
        t += step_min*60
        dist = seg['GS']*(t/3600.0)
        rows.append({"T+ (min)": int(t/60), "Dist (nm)": round(dist,1)})
    return rows

cpA = checkpoints_for(segA, ck_every)
if cpA:
    st.markdown("**Desde início até {}**".format("TOC" if segB and profile=="CLIMB" else "TOD" if segB else "fim"))
    st.dataframe(cpA, use_container_width=True)

if segB:
    cpB = checkpoints_for(segB, ck_every)
    if cpB:
        st.markdown("**Após {} (tempo recomeça em 0)**".format("TOC" if profile=="CLIMB" else "TOD"))
        st.dataframe(cpB, use_container_width=True)

# ===== Adicionar perna ao histórico =====
st.markdown("---")
add = st.button("Adicionar perna ao histórico", type="primary")

# EFOB simples (sem taxi)
start_fuel = st.session_state.start_fuel
burn_total = sum(float(s['burn']) for s in segments)
efob_end = max(0.0, _round_tenth(start_fuel - burn_total))

if add:
    leg = {
        "inputs": {"TC": tc_true, "Dist": dist_nm, "Alt0": alt_start, "Alt1": alt_target, "W": (wind_from, wind_kt)},
        "segments": segments,
        "label": ("TOC" if (segB and profile=="CLIMB") else ("TOD" if (segB and profile=="DESCENT") else "")),
        "totals": {"sec": TOTAL_SEC, "burn": burn_total},
        "end_alt": end_alt,
        "efob_end": efob_end,
        "check_A": cpA,
        "check_B": checkpoints_for(segB, ck_every) if segB else []
    }
    st.session_state.legs_history.append(leg)
    st.session_state.next_alt_start = float(end_alt)
    st.success("Perna adicionada — podes introduzir a próxima.")

# ===== Histórico (todas as pernas anteriores + atual opcionalmente) =====
st.subheader("Histórico de Pernas")
if st.session_state.legs_history:
    for i,leg in enumerate(st.session_state.legs_history, start=1):
        with st.expander(f"Perna {i}: TC {leg['inputs']['TC']:.0f}° • {leg['inputs']['Dist']:.1f} nm • Alt {int(leg['inputs']['Alt0'])}→{int(leg['inputs']['Alt1'])} ft " + (f"• {leg['label']}" if leg['label'] else "")):
            st.write(f"Totais: {hhmmss_from_seconds(int(leg['totals']['sec']))} • Burn {_round_tenth(leg['totals']['burn']):.1f} L • Alt fim {int(_round_alt(leg['end_alt']))} ft • EFOB {leg['efob_end']:.1f} L")
            for j,s in enumerate(leg['segments'], start=1):
                st.markdown(f"**Segmento {j} — {s['name']}**  | Alt {int(_round_alt(s['alt0']))}→{int(_round_alt(s['alt1']))} ft | TH/MH {_round_angle(s['TH'])}/{_round_angle(s['MH'])} | GS/TAS {_round_unit(s['GS'])}/{_round_unit(s['TAS'])} kt | Tempo {mmss_from_seconds(int(s['time']))} | Dist {s['dist']:.1f} nm | Burn {_round_tenth(s['burn']):.1f} L")
            if leg['check_A']:
                st.caption("Checkpoints até marcador")
                st.dataframe(leg['check_A'], use_container_width=True)
            if leg['check_B']:
                st.caption("Checkpoints após marcador (tempo reinicia)")
                st.dataframe(leg['check_B'], use_container_width=True)
else:
    st.caption("(Ainda sem pernas no histórico)")

