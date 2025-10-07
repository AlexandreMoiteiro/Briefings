# app.py — NAVLOG Performance v5
# Reqs: streamlit
# 👉 Filosofia: TOC/TOD é um **novo fix**. Cada perna pode ter 1 ou 2 segmentos (antes/depois do marcador),
#    checkpoints reiniciam a contagem após o marcador, e mostro TH/MH, GS/TAS, ETO e EFOB.
#    Botão **“Construir próxima perna”**: acrescenta esta perna à pilha e pré‑preenche a seguinte.

import streamlit as st
import datetime as dt
import math
from typing import Optional, Tuple

st.set_page_config(page_title="NAVLOG — Performance v5", layout="wide", initial_sidebar_state="collapsed")

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

# Vento (triângulo)
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

# Altimetria simplificada

def pressure_alt(alt_ft: float, qnh_hpa: float) -> float:
    return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0

# ROC / ROD basais (permite override manual)
# Padrão: Tabela simples (P2008, fator conservador) + opção de editar manualmente
ROC_TABLE = {0:835, 2000:726, 4000:617, 6000:509, 8000:401, 10000:294}

def roc_lookup(pa_ft: float) -> float:
    keys = sorted(ROC_TABLE.keys())
    pa = max(min(pa_ft, keys[-1]), keys[0])
    p0 = max(k for k in keys if k<=pa); p1 = min(k for k in keys if k>=pa)
    if p0==p1: return ROC_TABLE[p0]
    t=(pa-p0)/(p1-p0); return ROC_TABLE[p0] + t*(ROC_TABLE[p1]-ROC_TABLE[p0])

# ===== Estado =====

def ensure(k, v):
    if k not in st.session_state: st.session_state[k] = v

ensure("mag_var", 1)
ensure("mag_is_e", False)
ensure("qnh", 1013)
ensure("start_clock", "")
ensure("tas_climb", 70)   # <= pediste defaults 70/85
ensure("tas_cruise", 85)
ensure("ff_climb_lph", 20.0)
ensure("ff_cruise_lph", 18.0)
ensure("ff_descent_lph", 15.0)
ensure("rod_fpm", 700)
ensure("roc_override", 0)  # 0 = usar lookup
ensure("legs", [])        # lista de pernas já "fixadas"
ensure("carry_alt", 0.0)
ensure("carry_efob", 85.0)

# ===== Cabeçalho compacto =====
st.title("NAVLOG — Performance v5")
with st.form("hdr", clear_on_submit=False):
    c1,c2,c3,c4 = st.columns(4)
    with c1:
        st.session_state.qnh = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh))
        st.session_state.start_clock = st.text_input("Hora de descolagem (HH:MM) — opcional", st.session_state.start_clock)
    with c2:
        st.session_state.mag_var = st.number_input("Mag Variation (°)", 0, 30, int(st.session_state.mag_var))
        st.session_state.mag_is_e = st.selectbox("Variação E/W", ["W","E"], index=(1 if st.session_state.mag_is_e else 0))=="E"
    with c3:
        st.session_state.tas_climb = st.number_input("TAS Climb (kt)", 40, 120, int(st.session_state.tas_climb))
        st.session_state.tas_cruise = st.number_input("TAS Cruise (kt)", 40, 140, int(st.session_state.tas_cruise))
    with c4:
        st.session_state.ff_climb_lph = st.number_input("FF Climb (L/h)", 0.0, 40.0, float(st.session_state.ff_climb_lph), step=0.1)
        st.session_state.ff_cruise_lph = st.number_input("FF Cruise (L/h)", 0.0, 40.0, float(st.session_state.ff_cruise_lph), step=0.1)
        st.session_state.ff_descent_lph = st.number_input("FF Descent (L/h)", 0.0, 40.0, float(st.session_state.ff_descent_lph), step=0.1)
    c5,c6 = st.columns(2)
    with c5:
        st.session_state.roc_override = st.number_input("ROC manual (ft/min) — 0 usa tabela", 0, 3000, int(st.session_state.roc_override))
        st.session_state.rod_fpm = st.number_input("ROD (ft/min)", 100, 2000, int(st.session_state.rod_fpm))
    with c6:
        st.session_state.carry_efob = st.number_input("EFOB atual (L)", 0.0, 500.0, float(st.session_state.carry_efob), step=0.1)
        st.session_state.carry_alt = st.number_input("Alt atual (ft)", 0.0, 30000.0, float(st.session_state.carry_alt), step=50.0)
    st.form_submit_button("Aplicar parâmetros")

# ===== Funções de cálculo =====

def segment_from_to(tc_true: float, tas: float, gs_hint: Optional[float], w_from: int, w_kt: int,
                    alt0: float, alt1: float, ff_lph: float) -> dict:
    """Calcula um segmento com vento. Se gs_hint for None, calcula via triângulo. Retorna dict.
    Campos: name, TH, MH, WCA, GS, TAS, time_sec, dist_nm, burn_L, alt0, alt1.
    """
    wca, th, gs = wind_triangle(tc_true, tas, w_from, w_kt)
    mh = apply_var(th, st.session_state.mag_var, st.session_state.mag_is_e)
    out = {"TH": th, "MH": mh, "WCA": wca, "GS": max(gs,1e-6), "TAS": tas,
           "alt0": alt0, "alt1": alt1, "ff": ff_lph}
    return out

# ===== Entrada da NOVA perna =====
st.subheader("Perna atual — entrada")
colA,colB,colC,colD = st.columns(4)
with colA:
    TC = st.number_input("True Course (°T)", 0.0, 359.9, 90.0, step=0.1)
    Dist = st.number_input("Distância total (nm)", 0.0, 500.0, 10.0, step=0.1)
with colB:
    Alt0 = st.number_input("Alt início (ft)", 0.0, 30000.0, float(st.session_state.carry_alt), step=50.0)
    Alt1 = st.number_input("Alt alvo (ft)", 0.0, 30000.0, 4000.0, step=50.0)
with colC:
    W_from = st.number_input("Vento FROM (°T)", 0, 360, 180, step=1)
    W_kt   = st.number_input("Vento (kt)", 0, 150, 15, step=1)
with colD:
    CK = st.number_input("Checkpoints a cada (min)", 1, 10, 2, step=1)

# ==== Cálculo do Segmento A (até Alt1) ====
pa0 = pressure_alt(Alt0, st.session_state.qnh)
ROC = float(st.session_state.roc_override) if st.session_state.roc_override>0 else roc_lookup(pa0)

profile = "LEVEL" if abs(Alt1-Alt0) < 1e-6 else ("CLIMB" if Alt1>Alt0 else "DESCENT")

if profile == "CLIMB":
    segA = segment_from_to(TC, float(st.session_state.tas_climb), None, W_from, W_kt, Alt0, Alt1, float(st.session_state.ff_climb_lph))
    t_need_min = (Alt1-Alt0)/max(ROC,1e-6)
    d_need = segA["GS"]*(t_need_min/60.0)
    if d_need <= Dist:
        reached = True
        segA["name"] = "Climb → TOC"
        segA["time_sec"] = round_to_10s(t_need_min*60.0)
        segA["dist_nm"] = d_need
    else:
        reached = False
        segA["name"] = "Climb (não atinge)"
        tA = (Dist/max(segA["GS"],1e-6))*3600.0
        segA["time_sec"] = round_to_10s(tA)
        segA["dist_nm"] = Dist
        Alt1 = Alt0 + ROC*(tA/60.0)  # redefine alvo real (fim da perna)
        segA["alt1"] = Alt1
elif profile == "DESCENT":
    segA = segment_from_to(TC,  float(st.session_state.tas_cruise)-20, None, W_from, W_kt, Alt0, Alt1, float(st.session_state.ff_descent_lph))
    t_need_min = (Alt0-Alt1)/max(float(st.session_state.rod_fpm),1e-6)
    d_need = segA["GS"]*(t_need_min/60.0)
    if d_need <= Dist:
        reached = True
        segA["name"] = "Descent → TOD"
        segA["time_sec"] = round_to_10s(t_need_min*60.0)
        segA["dist_nm"] = d_need
    else:
        reached = False
        segA["name"] = "Descent (não atinge)"
        tA = (Dist/max(segA["GS"],1e-6))*3600.0
        segA["time_sec"] = round_to_10s(tA)
        segA["dist_nm"] = Dist
        Alt1 = max(0.0, Alt0 - float(st.session_state.rod_fpm)*(tA/60.0))
        segA["alt1"] = Alt1
else:  # LEVEL
    reached = False
    segA = segment_from_to(TC, float(st.session_state.tas_cruise), None, W_from, W_kt, Alt0, Alt0, float(st.session_state.ff_cruise_lph))
    tA = (Dist/max(segA["GS"],1e-6))*3600.0
    segA["name"] = "Level"
    segA["time_sec"] = round_to_10s(tA)
    segA["dist_nm"] = Dist

segA["burn_L"] = segA["ff"] * (segA["time_sec"]/3600.0)

# ==== Segmento B (cruise após marcador, se existir) ====
segB = None
if profile in ("CLIMB","DESCENT") and reached and segA["dist_nm"] < Dist:
    segB = segment_from_to(TC, float(st.session_state.tas_cruise), None, W_from, W_kt, Alt1, Alt1, float(st.session_state.ff_cruise_lph))
    rem = max(0.0, Dist - segA["dist_nm"])
    segB["name"] = "Cruise (após TOC)" if profile=="CLIMB" else "Cruise (após TOD)"
    segB["time_sec"] = round_to_10s((rem/max(segB["GS"],1e-6))*3600.0)
    segB["dist_nm"] = rem
    segB["burn_L"] = segB["ff"] * (segB["time_sec"]/3600.0)

segments = [segA] + ([segB] if segB else [])
TOTAL_SEC = sum(int(s["time_sec"]) for s in segments)
TOTAL_BURN = _r_tenth(sum(float(s["burn_L"]) for s in segments))
END_ALT = segments[-1]["alt1"]

# ==== ETO/EFOB timeline ====
start_clock = st.session_state.start_clock.strip()
clock = None
if start_clock:
    try:
        h,m = map(int, start_clock.split(":"))
        clock = dt.datetime.combine(dt.date.today(), dt.time(hour=h, minute=m))
    except Exception:
        clock = None

EFOB0 = float(st.session_state.carry_efob)

def advance(t_sec, burn_l):
    global clock, EFOB0
    eto=""
    if clock:
        clock = clock + dt.timedelta(seconds=int(t_sec))
        eto = clock.strftime("%H:%M")
    EFOB0 = max(0.0, _r_tenth(EFOB0 - float(burn_l)))
    return eto, EFOB0

# ===== Saída — clara e separada =====
st.markdown("---")
st.subheader("Resultados da Perna")

# Segmento 1
st.markdown(f"### Segmento 1 — {segA['name']}")
s1a,s1b,s1c,s1d = st.columns(4)
s1a.metric("Alt ini→fim (ft)", f"{int(round(segA['alt0']))} → {int(round(segA['alt1']))}")
s1b.metric("TH/MH (°)", f"{_r_angle(segA['TH'])}T / { _r_angle(segA['MH']) }M")
s1c.metric("GS/TAS (kt)", f"{_r_unit(segA['GS'])} / {_r_unit(segA['TAS'])}")
s1d.metric("WCA (°)", f"{_r_unit(segA['WCA'])}")
s1e,s1f,s1g,s1h = st.columns(4)
s1e.metric("Tempo", mmss_from_seconds(int(segA['time_sec'])))
s1f.metric("Dist (nm)", f"{segA['dist_nm']:.1f}")
s1g.metric("Burn (L)", f"{_r_tenth(segA['burn_L']):.1f}")
if start_clock:
    eto1, efob1 = advance(segA['time_sec'], segA['burn_L'])
    s1h.metric("ETO / EFOB", f"{eto1 or '—'} / {efob1:.1f} L")
else:
    s1h.metric("EFOB", f"{_r_tenth(EFOB0 - segA['burn_L']):.1f} L")
    EFOB0 = max(0.0, _r_tenth(EFOB0 - segA['burn_L']))

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
    s2d.metric("WCA (°)", f"{_r_unit(segB['WCA'])}")
    s2e,s2f,s2g,s2h = st.columns(4)
    s2e.metric("Tempo", mmss_from_seconds(int(segB['time_sec'])))
    s2f.metric("Dist (nm)", f"{segB['dist_nm']:.1f}")
    s2g.metric("Burn (L)", f"{_r_tenth(segB['burn_L']):.1f}")
    if start_clock:
        eto2, efob2 = advance(segB['time_sec'], segB['burn_L'])
        s2h.metric("ETO / EFOB", f"{eto2 or '—'} / {efob2:.1f} L")
    else:
        s2h.metric("EFOB", f"{_r_tenth(EFOB0 - segB['burn_L']):.1f} L")
        EFOB0 = max(0.0, _r_tenth(EFOB0 - segB['burn_L']))

# Totais
st.markdown("---")
end_eto = ""
if start_clock and segments:
    # clock já foi avançado acima
    end_eto = clock.strftime("%H:%M") if 'clock' in globals() and clock else ""
st.markdown(f"**Totais** — ETE {hhmmss_from_seconds(TOTAL_SEC)} • Burn {TOTAL_BURN:.1f} L" + (f" • **ETO fim {end_eto}**" if end_eto else ""))
end_efob = max(0.0, _r_tenth(float(st.session_state.carry_efob) - sum(s['burn_L'] for s in segments)))
st.markdown(f"**EFOB** — Start {float(st.session_state.carry_efob):.1f} L → End {end_efob:.1f} L")

# ===== Checkpoints (sempre por segmento; tempo reinicia após marcador) =====

def checkpoints(seg: dict, every_min: int):
    rows=[]; t=0
    while t + every_min*60 <= seg['time_sec']:
        t += every_min*60
        d = seg['GS']*(t/3600.0)
        burn = seg['ff']*(t/3600.0)
        # ETO/EFOB relativos ao segmento (se houver relógio, calcula absoluto)
        if start_clock:
            # recomputar ETO: base é o início do segmento
            pass
        rows.append({
            "T+ (min)": int(t/60),
            "Dist desde início do segmento (nm)": round(d,1),
            "GS (kt)": _r_unit(seg['GS']),
            "EFOB (L)": max(0.0, _r_tenth(float(st.session_state.carry_efob) - burn))
        })
    return rows

st.subheader("Checkpoints")
cpA = checkpoints(segA, int(CK))
st.markdown("**Até {}**".format("TOC" if segB and profile=="CLIMB" else "TOD" if segB else "fim"))
st.dataframe(cpA, use_container_width=True)
if segB:
    cpB = checkpoints(segB, int(CK))
    st.markdown("**Após {} (T+ reinicia)**".format("TOC" if profile=="CLIMB" else "TOD"))
    st.dataframe(cpB, use_container_width=True)

# ===== Botão: construir a PRÓXIMA perna (mantendo esta na UI) =====
if st.button("➕ Construir próxima perna (usar fim desta como início)", type="primary"):
    # Guardar esta perna na pilha e preparar a próxima
    st.session_state.legs.append({
        "TC": TC, "Dist": Dist, "Alt0": segments[0]['alt0'], "Alt1": segments[-1]['alt1'],
        "W": (W_from, W_kt), "CK": CK, "segments": segments,
        "totals": {"sec": TOTAL_SEC, "burn": TOTAL_BURN}, "end_efob": end_efob,
    })
    st.session_state.carry_alt = float(END_ALT)
    st.session_state.carry_efob = float(end_efob)
    st.experimental_rerun()

# ===== Pilha de pernas construídas (mostradas por ordem) =====
if st.session_state.legs:
    st.markdown("---")
    st.subheader("Pernas já construídas")
    for i,leg in enumerate(st.session_state.legs, start=1):
        st.markdown(f"### Perna {i} — TC {leg['TC']:.0f}° • {leg['Dist']:.1f} nm • Alt fim {int(round(leg['Alt1']))} ft • EFOB fim {leg['end_efob']:.1f} L")
        for j,s in enumerate(leg['segments'], start=1):
            st.markdown(f"**Segmento {j} — {s['alt0']:.0f}→{s['alt1']:.0f} ft | TH/MH {_r_angle(s['TH'])}/{_r_angle(s['MH'])} | GS/TAS {_r_unit(s['GS'])}/{_r_unit(s['TAS'])} kt | Tempo {mmss_from_seconds(int(s['time_sec']))} | Dist {s['dist_nm']:.1f} nm | Burn {_r_tenth(s['burn_L']):.1f} L")
        st.divider()


