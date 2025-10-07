# app.py — Performance v4
# Reqs: streamlit
# - Entrada manual por perna (TC, Dist, Alt início, Alt alvo, Vento FROM/kt)
# - TOC/TOD é tratado como NOVO FIX: separação física de segmentos e checkpoints reiniciados
# - Histórico de pernas acumulado; botão "Adicionar perna" mantém as anteriores

import streamlit as st
import math
from typing import Optional, Tuple

st.set_page_config(page_title="NAVLOG — Performance v4", layout="wide", initial_sidebar_state="collapsed")

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

# ===== Perf tables (P2008 simplificado) =====
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
ensure("legs", [])

# =========================================================
# Inputs de base
# =========================================================
st.title("NAVLOG — Performance v4 (TOC/TOD = FIX; checkpoints reset; histórico)")
with st.form("base", clear_on_submit=False):
    c1,c2,c3 = st.columns(3)
    with c1:
        qnh = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh))
        oat = st.number_input("OAT (°C)", -40, 50, int(st.session_state.temp_c))
    with c2:
        var_deg = st.number_input("Mag Variation (°)", 0, 30, int(st.session_state.var_deg))
        var_is_e = st.selectbox("Variação E/W", ["W","E"], index=(1 if st.session_state.var_is_e else 0))=="E"
    with c3:
        rpm_cl = st.number_input("Climb RPM", 1800, 2388, int(st.session_state.rpm_climb))
        rpm_cr = st.number_input("Cruise RPM", 1800, 2388, int(st.session_state.rpm_cruise))
        rod    = st.number_input("ROD (ft/min)", 200, 1500, int(st.session_state.rod_fpm))
        ff_ds  = st.number_input("Descent FF (L/h)", 0.0, 30.0, float(st.session_state.descent_ff))
    if st.form_submit_button("Aplicar"):
        st.session_state.qnh=qnh; st.session_state.temp_c=oat
        st.session_state.var_deg=var_deg; st.session_state.var_is_e=var_is_e
        st.session_state.rpm_climb=rpm_cl; st.session_state.rpm_cruise=rpm_cr
        st.session_state.rod_fpm=rod; st.session_state.descent_ff=ff_ds
        st.success("Parâmetros aplicados.")

# =========================================================
# Nova perna
# =========================================================
st.subheader("Nova Perna")
a1,a2,a3,a4 = st.columns(4)
with a1:
    TC = st.number_input("True Course (°T)", 0.0, 359.9, 90.0, step=0.1)
    Dist = st.number_input("Distância (nm)", 0.0, 999.9, 10.0, step=0.1)
with a2:
    Alt0 = st.number_input("Alt início (ft)", 0.0, 20000.0, float(st.session_state.next_alt_start), step=50.0)
    Alt1 = st.number_input("Alt alvo (ft)", 0.0, 20000.0, 4000.0, step=50.0)
with a3:
    W_from = st.number_input("Vento FROM (°T)", 0, 360, 180)
    W_kt   = st.number_input("Vento (kt)", 0, 120, 15)
with a4:
    CK = st.number_input("Checkpoints a cada (min)", 1, 10, 2)
    fuel_start = st.number_input("EFOB inicial (L)", 0.0, 500.0, float(st.session_state.start_fuel))

# ===== Cálculos
pa0 = pressure_alt(Alt0, st.session_state.qnh)
Vy = vy_interp_enroute(pa0)
ROC = roc_interp_enroute(pa0, st.session_state.temp_c)

# TAS/FF base
_ , FF_climb = cruise_lookup(Alt0 + 0.5*max(0.0, Alt1-Alt0), int(st.session_state.rpm_climb), st.session_state.temp_c)
TAS_cruise, FF_cruise = cruise_lookup(pressure_alt(Alt1, st.session_state.qnh), int(st.session_state.rpm_cruise), st.session_state.temp_c)
TAS_cruise = st.session_state.cruise_ref_kt or TAS_cruise

# Headings & GS por fase
_, TH_climb, GS_climb = wind_triangle(TC, Vy, W_from, W_kt)
_, TH_cruise, GS_cruise = wind_triangle(TC, TAS_cruise, W_from, W_kt)
MH_climb  = apply_var(TH_climb, st.session_state.var_deg, st.session_state.var_is_e)
MH_cruise = apply_var(TH_cruise, st.session_state.var_deg, st.session_state.var_is_e)
MC = apply_var(TC, st.session_state.var_deg, st.session_state.var_is_e)

# Segmento A (perfil até alvo) e Segmento B (cruise)
profile = "LEVEL" if abs(Alt1-Alt0) < 1e-6 else ("CLIMB" if Alt1>Alt0 else "DESCENT")
segA = {}
segB = None
reached_marker = False

if profile == "CLIMB":
    t_need_min = (Alt1-Alt0)/max(ROC,1e-6)
    d_need = GS_climb*(t_need_min/60.0)
    if d_need <= Dist:
        reached_marker = True
        tA = round_to_10s(t_need_min*60.0)
        segA = {"name":"Climb → TOC","TH":TH_climb,"MH":MH_climb,"GS":GS_climb,"TAS":Vy,
                "time":tA,"dist":d_need,"burn":FF_climb*(tA/3600.0),"alt0":Alt0,"alt1":Alt1}
        rem = max(0.0, Dist-d_need)
        if rem>0:
            tB = round_to_10s((rem/max(GS_cruise,1e-6))*3600.0)
            segB = {"name":"Cruise (após TOC)","TH":TH_cruise,"MH":MH_cruise,"GS":GS_cruise,"TAS":TAS_cruise,
                    "time":tB,"dist":rem,"burn":FF_cruise*(tB/3600.0),"alt0":Alt1,"alt1":Alt1}
        end_alt = Alt1
    else:
        tA = round_to_10s((Dist/max(GS_climb,1e-6))*3600.0)
        gained = ROC*(tA/60.0)
        end_alt = Alt0 + gained
        segA = {"name":"Climb (não atinge)","TH":TH_climb,"MH":MH_climb,"GS":GS_climb,"TAS":Vy,
                "time":tA,"dist":Dist,"burn":FF_climb*(tA/3600.0),"alt0":Alt0,"alt1":end_alt}
elif profile == "DESCENT":
    _, TH_desc, GS_desc = wind_triangle(TC, st.session_state.descent_ref_kt, W_from, W_kt)
    MH_desc = apply_var(TH_desc, st.session_state.var_deg, st.session_state.var_is_e)
    t_need_min = (Alt0-Alt1)/max(float(st.session_state.rod_fpm),1e-6)
    d_need = GS_desc*(t_need_min/60.0)
    if d_need <= Dist:
        reached_marker = True
        tA = round_to_10s(t_need_min*60.0)
        segA = {"name":"Descent → TOD","TH":TH_desc,"MH":MH_desc,"GS":GS_desc,"TAS":st.session_state.descent_ref_kt,
                "time":tA,"dist":d_need,"burn":float(st.session_state.descent_ff)*(tA/3600.0),"alt0":Alt0,"alt1":Alt1}
        rem = max(0.0, Dist-d_need)
        if rem>0:
            tB = round_to_10s((rem/max(GS_cruise,1e-6))*3600.0)
            segB = {"name":"Cruise (após TOD)","TH":TH_cruise,"MH":MH_cruise,"GS":GS_cruise,"TAS":TAS_cruise,
                    "time":tB,"dist":rem,"burn":FF_cruise*(tB/3600.0),"alt0":Alt1,"alt1":Alt1}
        end_alt = Alt1
    else:
        tA = round_to_10s((Dist/max(GS_desc,1e-6))*3600.0)
        lost = float(st.session_state.rod_fpm)*(tA/60.0)
        end_alt = max(0.0, Alt0 - lost)
        segA = {"name":"Descent (não atinge)","TH":TH_desc,"MH":MH_desc,"GS":GS_desc,"TAS":st.session_state.descent_ref_kt,
                "time":tA,"dist":Dist,"burn":float(st.session_state.descent_ff)*(tA/3600.0),"alt0":Alt0,"alt1":end_alt}
else:
    tA = round_to_10s((Dist/max(GS_cruise,1e-6))*3600.0)
    end_alt = Alt0
    segA = {"name":"Level","TH":TH_cruise,"MH":MH_cruise,"GS":GS_cruise,"TAS":TAS_cruise,
            "time":tA,"dist":Dist,"burn":FF_cruise*(tA/3600.0),"alt0":Alt0,"alt1":end_alt}

segments = [segA] + ([segB] if segB else [])
TOTAL_SEC = sum(int(s['time']) for s in segments)
TOTAL_BURN = _round_tenth(sum(float(s['burn']) for s in segments))

# ===== UI — Apresentação clara =====
st.markdown("---")
st.subheader("Resultados da Perna")

# Painéis-resumo do topo
c1,c2,c3,c4 = st.columns(4)
with c1: st.metric("Seg A", "CLIMB" if profile=="CLIMB" else ("DESCENT" if profile=="DESCENT" else "LEVEL"))
with c2: st.metric("GS seg A (kt)", _round_unit(segA['GS']))
with c3: st.metric("GS seg B (kt)", _round_unit(segB['GS']) if segB else 0)
with c4: st.metric("Alt fim (ft)", _round_alt(end_alt))

# Segmento A (sempre)
st.markdown("#### Segmento 1 — {}".format(segA['name']))
s1a,s1b,s1c = st.columns(3)
s1a.write(f"Alt: {_round_alt(segA['alt0'])}→{_round_alt(segA['alt1'])} ft")
s1b.write(f"TH/MH: {_round_angle(segA['TH'])}°T / {_round_angle(segA['MH'])}°M")
s1c.write(f"GS/TAS: {_round_unit(segA['GS'])}/{_round_unit(segA['TAS'])} kt")
s1d,s1e,s1f = st.columns(3)
s1d.write(f"Tempo: {mmss_from_seconds(int(segA['time']))}")
s1e.write(f"Dist: {segA['dist']:.1f} nm")
s1f.write(f"Burn: {_round_tenth(segA['burn']):.1f} L")

# Linha de separação física no marcador
if reached_marker:
    label = "TOC" if profile=="CLIMB" else "TOD"
    st.success(f"{label} — {mmss_from_seconds(int(segA['time']))} • {segA['dist']:.1f} nm desde o início")

# Segmento B (se existir)
if segB:
    st.markdown("#### Segmento 2 — {}".format(segB['name']))
    s2a,s2b,s2c = st.columns(3)
    s2a.write(f"Alt: {_round_alt(segB['alt0'])}→{_round_alt(segB['alt1'])} ft")
    s2b.write(f"TH/MH: {_round_angle(segB['TH'])}°T / {_round_angle(segB['MH'])}°M")
    s2c.write(f"GS/TAS: {_round_unit(segB['GS'])}/{_round_unit(segB['TAS'])} kt")
    s2d,s2e,s2f = st.columns(3)
    s2d.write(f"Tempo: {mmss_from_seconds(int(segB['time']))}")
    s2e.write(f"Dist: {segB['dist']:.1f} nm")
    s2f.write(f"Burn: {_round_tenth(segB['burn']):.1f} L")

st.divider()
st.markdown(f"**Totais da perna** — ETE {hhmmss_from_seconds(TOTAL_SEC)} • Burn {TOTAL_BURN:.1f} L")

# ===== Checkpoints (reiniciam no marcador) =====

def checkpoints(seg, every_min):
    rows=[]; t=0
    while t+every_min*60 <= seg['time']:
        t += every_min*60
        d = seg['GS']*(t/3600.0)
        rows.append({"T+ (min)": int(t/60), "Dist (nm)": round(d,1)})
    return rows

st.subheader("Checkpoints")
cpA = checkpoints(segA, CK)
if cpA:
    st.markdown("**Desde início até {}**".format("TOC" if reached_marker and profile=="CLIMB" else "TOD" if reached_marker else "fim"))
    st.dataframe(cpA, use_container_width=True)
if segB:
    cpB = checkpoints(segB, CK)
    if cpB:
        st.markdown("**Após {} (tempo recomeça em 0)**".format("TOC" if profile=="CLIMB" else "TOD"))
        st.dataframe(cpB, use_container_width=True)

# ===== Adicionar ao histórico =====
add = st.button("Adicionar perna ao histórico", type="primary")

end_fuel = max(0.0, _round_tenth(fuel_start - (float(segA['burn']) + (float(segB['burn']) if segB else 0.0))))

if add:
    st.session_state.legs.append({
        "inputs": {"TC": TC, "Dist": Dist, "Alt0": Alt0, "Alt1": Alt1, "W": (W_from, W_kt), "CK": CK, "FuelStart": fuel_start},
        "segments": segments,
    	"marker": ("TOC" if (reached_marker and profile=="CLIMB") else ("TOD" if (reached_marker and profile=="DESCENT") else "")),
        "end_alt": segB['alt1'] if segB else segA['alt1'],
        "end_fuel": end_fuel,
        "totals": {"sec": TOTAL_SEC, "burn": float(segA['burn']) + (float(segB['burn']) if segB else 0.0)},
        "check_A": cpA,
        "check_B": cpB if segB else []
    })
    st.session_state.next_alt_start = float(segB['alt1'] if segB else segA['alt1'])
    st.success("Perna adicionada. Podes inserir a próxima imediatamente acima.")

# ===== Histórico de pernas =====
st.subheader("Histórico")
if st.session_state.legs:
    for i,leg in enumerate(st.session_state.legs, start=1):
        title = f"Perna {i}: TC {leg['inputs']['TC']:.0f}° • {leg['inputs']['Dist']:.1f} nm • Alt {int(leg['inputs']['Alt0'])}→{int(leg['inputs']['Alt1'])} ft"
        if leg['marker']: title += f" • {leg['marker']}"
        with st.expander(title):
            st.write(f"Totais: {hhmmss_from_seconds(int(leg['totals']['sec']))} • Burn {_round_tenth(leg['totals']['burn']):.1f} L • Alt fim {_round_alt(leg['end_alt'])} ft • EFOB fim {leg['end_fuel']:.1f} L")
            for j,s in enumerate(leg['segments'], start=1):
                st.markdown(f"**Segmento {j} — {s['name']}**  | Alt {_round_alt(s['alt0'])}→{_round_alt(s['alt1'])} ft | TH/MH {_round_angle(s['TH'])}/{_round_angle(s['MH'])} | GS/TAS {_round_unit(s['GS'])}/{_round_unit(s['TAS'])} kt | Tempo {mmss_from_seconds(int(s['time']))} | Dist {s['dist']:.1f} nm | Burn {_round_tenth(s['burn']):.1f} L")
            if leg['check_A']:
                st.caption("Checkpoints até marcador")
                st.dataframe(leg['check_A'], use_container_width=True)
            if leg['check_B']:
                st.caption("Checkpoints após marcador (T+ reinicia)")
                st.dataframe(leg['check_B'], use_container_width=True)
else:
    st.caption("(Sem pernas ainda)")

