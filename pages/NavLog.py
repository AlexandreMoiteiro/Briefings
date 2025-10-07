# app.py — NAVLOG Performance-focused (com vento, TOC/TOD e checkpoints)
# Reqs: streamlit
# - Sem aeródromos/rota/PDF/relatórios/JSON.
# - Entrada manual por perna: TC, Dist (nm), Alt início/alt alvo, Vento FROM/kt.
# - Calcula climb/desc até atingir a altitude alvo; se não atingir, dá a altitude ao fim da leg.
# - Se atingir, marca TOC/TOD e separa o resto em CRUISE.
# - Mostra burn/velocidades/EFOB por segmento e totais.
# - Gera checkpoints de 2 em 2 minutos (configurável) com distância desde o início.
# - Botão para propagar Alt final/EFOB para a próxima perna.

import streamlit as st
import math
from typing import Optional, Tuple

st.set_page_config(page_title="NAVLOG — Performance v2", layout="wide", initial_sidebar_state="collapsed")

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

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

# ===== Vento / triangulo =====

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

def isa_temp(pa_ft):
    return 15.0 - 2.0*(pa_ft/1000.0)

def pressure_alt(alt_ft, qnh_hpa):
    return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0


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

ensure("aircraft","P208"); ensure("registration","CS-ECC"); ensure("callsign","RVP")
ensure("student","AMOIT"); ensure("lesson",""); ensure("instrutor","")
ensure("qnh",1013); ensure("cruise_alt",4000)
ensure("temp_c",15); ensure("var_deg",1); ensure("var_is_e",False)
ensure("rpm_climb",2250); ensure("rpm_cruise",2000)
ensure("descent_ff",15.0); ensure("rod_fpm",700); ensure("start_fuel",85.0)
ensure("cruise_ref_kt",90); ensure("descent_ref_kt",65)
ensure("last_alt_end", 0.0)  # para encadear pernas
ensure("checkpoint_min", 2)

# =========================================================
# Cabeçalho / Parâmetros base
# =========================================================
st.title("NAVLOG — Performance (vento + TOC/TOD + checkpoints)")
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
        f_qnh  = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh), step=1)
        f_oat  = st.number_input("OAT (°C)", -40, 50, int(st.session_state.temp_c), step=1)
    with c3:
        f_var  = st.number_input("Mag Variation (°)", 0, 30, int(st.session_state.var_deg), step=1)
        f_varE = (st.selectbox("Variação E/W", ["W","E"], index=(1 if st.session_state.var_is_e else 0))=="E")

    st.markdown("---")
    c4,c5,c6 = st.columns(3)
    with c4:
        f_rpm_cl = st.number_input("Climb RPM (AFM)", 1800, 2388, int(st.session_state.rpm_climb), step=10)
        f_rpm_cr = st.number_input("Cruise RPM (AFM)", 1800, 2388, int(st.session_state.rpm_cruise), step=10)
    with c5:
        f_spd_cr  = st.number_input("Cruise speed override (kt)", 40, 140, int(st.session_state.cruise_ref_kt), step=1)
        f_spd_ds  = st.number_input("Descent speed (kt)", 40, 120, int(st.session_state.descent_ref_kt), step=1)
    with c6:
        f_rod    = st.number_input("ROD (ft/min)", 200, 1500, int(st.session_state.rod_fpm), step=10)
        f_ff_ds  = st.number_input("Descent FF (L/h)", 0.0, 30.0, float(st.session_state.descent_ff), step=0.1)

    submitted = st.form_submit_button("Aplicar parâmetros")
    if submitted:
        st.session_state.aircraft=f_aircraft; st.session_state.registration=f_registration; st.session_state.callsign=f_callsign
        st.session_state.qnh=f_qnh; st.session_state.temp_c=f_oat
        st.session_state.var_deg=f_var; st.session_state.var_is_e=f_varE
        st.session_state.rpm_climb=f_rpm_cl; st.session_state.rpm_cruise=f_rpm_cr
        st.session_state.descent_ref_kt=f_spd_ds; st.session_state.cruise_ref_kt=f_spd_cr
        st.session_state.rod_fpm=f_rod; st.session_state.descent_ff=f_ff_ds
        st.success("Parâmetros aplicados.")

# =========================================================
# Perna — entrada manual
# =========================================================

st.subheader("Perna — entrada manual")
colA,colB,colC,colD = st.columns(4)
with colA:
    tc_true = st.number_input("True Course (°T)", min_value=0.0, max_value=359.9, value=90.0, step=0.1)
    alt_start = st.number_input("Alt início (ft)", min_value=0.0, step=50.0, value=float(st.session_state.get("last_alt_end",0.0)))
with colB:
    dist_nm = st.number_input("Distância (nm)", min_value=0.0, value=5.0, step=0.1)
    alt_target = st.number_input("Alt alvo (ft)", min_value=0.0, step=50.0, value=4000.0)
with colC:
    wind_from = st.number_input("Vento FROM (°T)", min_value=0, max_value=360, value=180, step=1)
    wind_kt   = st.number_input("Vento (kt)", min_value=0, max_value=120, value=15, step=1)
with colD:
    ck_every  = st.number_input("Check a cada (min)", min_value=1, max_value=10, value=int(st.session_state.checkpoint_min), step=1)
    st.session_state.checkpoint_min = ck_every

# ===== Cálculo =====
pa_start  = pressure_alt(alt_start, st.session_state.qnh)
vy_kt     = vy_interp_enroute(pa_start)
roc_fpm   = roc_interp_enroute(pa_start, st.session_state.temp_c)

# TAS/FF por fase
_ , ff_climb  = cruise_lookup(alt_start + 0.5*max(0.0, alt_target-alt_start), int(st.session_state.rpm_climb),  st.session_state.temp_c)
crz_tas_tab , crz_ff_tab = cruise_lookup(pressure_alt(alt_target, st.session_state.qnh), int(st.session_state.rpm_cruise), st.session_state.temp_c)
crz_tas = st.session_state.cruise_ref_kt or crz_tas_tab
ff_descent = float(st.session_state.descent_ff)

# GS e headings por fase
_, th_climb, gs_climb = wind_triangle(tc_true, vy_kt, wind_from, wind_kt)
_, th_cruise, gs_cruise = wind_triangle(tc_true, crz_tas, wind_from, wind_kt)

mc_climb = apply_var(tc_true, st.session_state.var_deg, st.session_state.var_is_e)
mh_climb = apply_var(th_climb, st.session_state.var_deg, st.session_state.var_is_e)
mc_cruise= mc_climb
mh_cruise= apply_var(th_cruise, st.session_state.var_deg, st.session_state.var_is_e)

# Direção do perfil (climb, level, descent)
profile = "CLIMB" if alt_target>alt_start+1e-6 else ("DESCENT" if alt_target<alt_start-1e-6 else "LEVEL")

segments = []
end_alt = alt_start

if profile == "CLIMB":
    t_needed_min = (alt_target - alt_start) / max(roc_fpm,1e-6)
    d_needed_nm = gs_climb * (t_needed_min/60.0)
    if d_needed_nm <= dist_nm:  # atinge
        t1_sec = round_to_10s(t_needed_min*60.0)
        d1_nm = d_needed_nm
        burn1 = ff_climb * (t1_sec/3600.0)
        segments.append({
            "name":"Climb (até TOC)",
            "TH": th_climb, "MH": mh_climb, "GS": gs_climb,
            "TAS": vy_kt, "time_sec": t1_sec, "dist_nm": d1_nm, "burn": burn1,
            "start_alt": alt_start, "end_alt": alt_target
        })
        rem_nm = max(0.0, dist_nm - d1_nm)
        if rem_nm>0:
            t2_sec = round_to_10s((rem_nm / max(gs_cruise,1e-6)) * 3600.0)
            burn2 = crz_ff_tab * (t2_sec/3600.0)
            segments.append({
                "name":"Cruise (após TOC)",
                "TH": th_cruise, "MH": mh_cruise, "GS": gs_cruise,
                "TAS": crz_tas, "time_sec": t2_sec, "dist_nm": rem_nm, "burn": burn2,
                "start_alt": alt_target, "end_alt": alt_target
            })
        end_alt = alt_target
    else:  # não atinge dentro da distância
        t_full_sec = round_to_10s((dist_nm / max(gs_climb,1e-6)) * 3600.0)
        burn = ff_climb * (t_full_sec/3600.0)
        gained_ft = roc_fpm * (t_full_sec/60.0)
        end_alt = alt_start + gained_ft
        segments.append({
            "name":"Climb (não atinge alvo)",
            "TH": th_climb, "MH": mh_climb, "GS": gs_climb,
            "TAS": vy_kt, "time_sec": t_full_sec, "dist_nm": dist_nm, "burn": burn,
            "start_alt": alt_start, "end_alt": end_alt
        })
elif profile == "DESCENT":
    rod = float(st.session_state.rod_fpm)
    t_needed_min = (alt_start - alt_target) / max(rod,1e-6)
    # usar GS de descent (aprox com descent TAS)
    _, th_desc, gs_desc = wind_triangle(tc_true, st.session_state.descent_ref_kt, wind_from, wind_kt)
    d_needed_nm = gs_desc * (t_needed_min/60.0)
    if d_needed_nm <= dist_nm:
        t1_sec = round_to_10s(t_needed_min*60.0)
        d1_nm = d_needed_nm
        burn1 = ff_descent * (t1_sec/3600.0)
        segments.append({
            "name":"Descent (até TOD)",
            "TH": th_desc, "MH": apply_var(th_desc, st.session_state.var_deg, st.session_state.var_is_e), "GS": gs_desc,
            "TAS": st.session_state.descent_ref_kt, "time_sec": t1_sec, "dist_nm": d1_nm, "burn": burn1,
            "start_alt": alt_start, "end_alt": alt_target
        })
        rem_nm = max(0.0, dist_nm - d1_nm)
        if rem_nm>0:
            t2_sec = round_to_10s((rem_nm / max(gs_cruise,1e-6)) * 3600.0)
            burn2 = crz_ff_tab * (t2_sec/3600.0)
            segments.append({
                "name":"Cruise (após TOD)",
                "TH": th_cruise, "MH": mh_cruise, "GS": gs_cruise,
                "TAS": crz_tas, "time_sec": t2_sec, "dist_nm": rem_nm, "burn": burn2,
                "start_alt": alt_target, "end_alt": alt_target
            })
        end_alt = alt_target
    else:
        t_full_sec = round_to_10s((dist_nm / max(gs_desc,1e-6)) * 3600.0)
        burn = ff_descent * (t_full_sec/3600.0)
        lost_ft = float(st.session_state.rod_fpm) * (t_full_sec/60.0)
        end_alt = max(0.0, alt_start - lost_ft)
        segments.append({
            "name":"Descent (não atinge alvo)",
            "TH": th_desc, "MH": apply_var(th_desc, st.session_state.var_deg, st.session_state.var_is_e), "GS": gs_desc,
            "TAS": st.session_state.descent_ref_kt, "time_sec": t_full_sec, "dist_nm": dist_nm, "burn": burn,
            "start_alt": alt_start, "end_alt": end_alt
        })
else:  # LEVEL
    t_sec = round_to_10s((dist_nm / max(gs_cruise,1e-6)) * 3600.0)
    burn = crz_ff_tab * (t_sec/3600.0)
    end_alt = alt_start
    segments.append({
        "name":"Level (sem variação de altitude)",
        "TH": th_cruise, "MH": mh_cruise, "GS": gs_cruise,
        "TAS": crz_tas, "time_sec": t_sec, "dist_nm": dist_nm, "burn": burn,
        "start_alt": alt_start, "end_alt": end_alt
    })

# Totais
TOTAL_SEC = sum(int(s["time_sec"]) for s in segments)
TOTAL_BURN = _round_tenth(sum(float(s["burn"]) for s in segments))

# ===== Saída =====
st.markdown("---")
st.subheader("Resultados da Perna")

cA,cB,cC,cD = st.columns(4)
with cA:
    st.metric("Vy (kt)", _round_unit(vy_kt))
    st.metric("ROC @ início (ft/min)", _round_unit(roc_fpm))
with cB:
    st.metric("GS climb/cruise (kt)", f"{_round_unit(segments[0]['GS'])} / {_round_unit(gs_cruise)}")
with cC:
    st.metric("TAS climb/cruise (kt)", f"{_round_unit(vy_kt)} / {_round_unit(crz_tas)}")
with cD:
    isa_dev = st.session_state.temp_c - isa_temp(pressure_alt(float(alt_start), st.session_state.qnh))
    st.metric("ISA dev @ início (°C)", int(round(isa_dev)))

# Chegada a alvo (se aplicável)
if profile != "LEVEL" and segments[0]["end_alt"] == alt_target and len(segments)>=1:
    st.info(f"TOC/TOD atingido em {mmss_from_seconds(int(segments[0]['time_sec']))} • {segments[0]['dist_nm']:.1f} nm desde o início")

# Tabela de segmentos (bem separada)
st.markdown("### Segmentos")
seg_cols = st.columns([3,2,2,2,2,2])
seg_cols[0].write("Segmento")
seg_cols[1].write("Alt ini → fim (ft)")
seg_cols[2].write("TH/MH (°T/°M)")
seg_cols[3].write("GS/TAS (kt)")
seg_cols[4].write("Tempo")
seg_cols[5].write("Dist / Burn")

for s in segments:
    seg_cols = st.columns([3,2,2,2,2,2])
    seg_cols[0].write(s["name"]) 
    seg_cols[1].write(f"{_round_alt(s['start_alt'])} → {_round_alt(s['end_alt'])}")
    seg_cols[2].write(f"{_round_angle(s['TH'])} / {_round_angle(s['MH'])}")
    seg_cols[3].write(f"{_round_unit(s['GS'])} / {_round_unit(s['TAS'])}")
    seg_cols[4].write(mmss_from_seconds(int(s["time_sec"])))
    seg_cols[5].write(f"{s['dist_nm']:.1f} nm / {_round_tenth(s['burn'])} L")

st.markdown("---")
st.markdown(f"**Totais** — ETE {hhmmss_from_seconds(TOTAL_SEC)} • Burn {TOTAL_BURN:.1f} L")

# EFOB simples
ensure("efof_curr", float(st.session_state.start_fuel))
if 'efof_curr' not in st.session_state:
    st.session_state.efof_curr = float(st.session_state.start_fuel)

burn_total = sum(float(s['burn']) for s in segments)
efob_end = max(0.0, _round_tenth(float(st.session_state.start_fuel) - burn_total))
st.markdown(f"**EFOB** — Start {st.session_state.start_fuel:.1f} L → End {efob_end:.1f} L")

# ===== Checkpoints de 2 em 2 minutos =====
st.markdown("### Checkpoints (a cada {} min)".format(ck_every))

cp_rows = []
acc_time = 0
acc_dist = 0.0
idx = 1
seg_ptr = 0
seg_time_left = segments[0]['time_sec'] if segments else 0
seg = segments[0] if segments else None

while acc_time + ck_every*60 <= TOTAL_SEC and seg is not None:
    step = ck_every*60
    # consumir tempo no(s) segmento(s)
    t_left = step
    d_step = 0.0
    while t_left > 0 and seg is not None:
        use = min(t_left, seg_time_left)
        d_step += (seg['GS'] * (use/3600.0))
        t_left -= use
        seg_time_left -= use
        if seg_time_left <= 0 and seg_ptr+1 < len(segments):
            seg_ptr += 1
            seg = segments[seg_ptr]
            seg_time_left = seg['time_sec']
        elif seg_time_left <= 0 and seg_ptr+1 >= len(segments):
            seg = None
            break
    acc_time += step
    acc_dist += d_step
    cp_rows.append({"T+ (min)": idx*ck_every, "Dist desde início (nm)": round(acc_dist,1)})
    idx += 1

if cp_rows:
    c1, c2 = st.columns([1,2])
    with c1:
        st.write("Marca")
        for r in cp_rows: st.write(f"T+{int(r['T+ (min)'])}m")
    with c2:
        st.write("Distância acumulada (nm)")
        for r in cp_rows: st.write(f"{r['Dist desde início (nm)']:.1f}")
else:
    st.caption("(Sem checkpoints dentro do tempo total)")

# ===== Próxima perna =====
st.markdown("---")
coln1, coln2 = st.columns([2,1])
coln1.markdown(f"**Altitude ao fim da perna:** {_round_alt(end_alt)} ft")
use_next = coln2.button("Usar como início da próxima perna")
if use_next:
    st.session_state.last_alt_end = float(end_alt)
    st.success(f"Alt início para a próxima perna definido para {_round_alt(end_alt)} ft.")



