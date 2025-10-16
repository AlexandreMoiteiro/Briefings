# app.py — NAVLOG v10 (AFM) — Focus Mode
# UI simplificada: blocos claros, grelha única para input das legs e tabela única de resultados.
# Cálculos consistentes: ROC usa OAT; FF climb por PA média; holds por leg (0=auto FF).
# Sem “surpresas”: nada escondido atrás de expanders; sem heranças automáticas de valores.

import streamlit as st
import pandas as pd
import datetime as dt
import math, json
from math import sin, asin, radians, degrees

# ========================
# CONFIG & LOOK & FEEL
# ========================
st.set_page_config(page_title="NAVLOG v10 (AFM) — Focus Mode", layout="wide", initial_sidebar_state="collapsed")

CSS = """
<style>
:root{--card:#fff;--muted:#6b7280;--line:#e5e7eb;--chip:#f3f4f6}
*{font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial}
.card{border:1px solid var(--line);border-radius:14px;padding:14px 14px;margin:10px 0;background:var(--card);box-shadow:0 1px 1px rgba(0,0,0,.03)}
.kvarea{display:flex;gap:10px;flex-wrap:wrap}
.kv{background:var(--chip);border:1px solid var(--line);border-radius:10px;padding:8px 10px;font-size:13px}
.hint{color:#6b7280;font-size:12px}
.sep{height:1px;background:var(--line);margin:10px 0}
.small{font-size:12px;color:#374151}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ========================
# HELPERS & PHYSICS
# ========================
rt10  = lambda s: max(10, int(round(s/10.0)*10)) if s>0 else 0
mmss  = lambda t: f"{t//60:02d}:{t%60:02d}"
hhmmss= lambda t: f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
rang  = lambda x: int(round(float(x))) % 360
rint  = lambda x: int(round(float(x)))
r10f  = lambda x: round(float(x), 1)

def wrap360(x): x = math.fmod(float(x), 360.0); return x + 360 if x < 0 else x
def angdiff(a, b): return (a - b + 180) % 360 - 180

def wind_triangle(tc, tas, wdir, wkt):
    if tas <= 0: return 0.0, wrap360(tc), 0.0
    d = radians(angdiff(wdir, tc)); cross = wkt * sin(d)
    s = max(-1, min(1, cross / max(tas, 1e-9)))
    wca = degrees(asin(s)); th = wrap360(tc + wca)
    gs = max(0.0, tas * math.cos(radians(wca)) - wkt * math.cos(d))
    return wca, th, gs

apply_var = lambda th, var, east_is_neg=False: wrap360(th - var if east_is_neg else th + var)

# ===== AFM resumido (Tecnam P2008) =====
ROC_ENR = {
    0:{-25:981,0:835,25:704,50:586}, 2000:{-25:870,0:726,25:597,50:481},
    4000:{-25:759,0:617,25:491,50:377}, 6000:{-25:648,0:509,25:385,50:273},
    8000:{-25:538,0:401,25:279,50:170}, 10000:{-25:428,0:294,25:174,50:66},
    12000:{-25:319,0:187,25:69,50:-37}, 14000:{-25:210,0:80,25:-35,50:-139}
}
VY = {0:67,2000:67,4000:67,6000:67,8000:67,10000:67,12000:67,14000:67}
ROC_FACTOR = 0.90
CRUISE = {
    0:{1800:(82,15.3),1900:(89,17.0),2000:(95,18.7),2100:(101,20.7),2250:(110,24.6),2388:(118,27.7)},
    2000:{1800:(81,15.5),1900:(87,17.0),2000:(93,18.8),2100:(99,20.9),2250:(108,25.0)},
    4000:{1800:(79,15.2),1900:(86,16.5),2000:(92,18.1),2100:(98,19.2),2250:(106,23.9)},
    6000:{1800:(78,14.9),1900:(85,16.1),2000:(91,17.5),2100:(97,19.2),2250:(105,22.7)},
    8000:{1800:(78,14.9),1900:(84,15.7),2000:(90,17.0),2100:(96,18.5),2250:(104,21.5)},
    10000:{1800:(78,15.5),1900:(82,15.5),2000:(89,16.6),2100:(95,17.9),2250:(103,20.5)},
}
isa_temp = lambda pa: 15.0 - 2.0*(pa/1000.0)
press_alt = lambda alt, qnh: float(alt) + (1013.0 - float(qnh)) * 30.0
clamp = lambda v, lo, hi: max(lo, min(hi, v))

def interp1(x, x0, x1, y0, y1):
    if x1 == x0: return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def cruise_lookup(pa, rpm, oat, weight):
    rpm = min(int(rpm), 2265)
    pas = sorted(CRUISE.keys()); pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    table0 = CRUISE[p0]; table1 = CRUISE[p1]

    def v(tab):
        rpms = sorted(tab.keys())
        if rpm in tab: return tab[rpm]
        if rpm < rpms[0]: lo, hi = rpms[0], rpms[1]
        elif rpm > rpms[-1]: lo, hi = rpms[-2], rpms[-1]
        else:
            lo = max([r for r in rpms if r <= rpm]); hi = min([r for r in rpms if r >= rpm])
        (tas_lo, ff_lo), (tas_hi, ff_hi) = tab[lo], tab[hi]
        t = (rpm - lo) / (hi - lo) if hi != lo else 0
        return (tas_lo + t*(tas_hi - tas_lo), ff_lo + t*(ff_hi - ff_lo))

    tas0, ff0 = v(table0); tas1, ff1 = v(table1)
    tas = interp1(pa_c, p0, p1, tas0, tas1); ff = interp1(pa_c, p0, p1, ff0, ff1)

    if oat is not None:
        dev = oat - isa_temp(pa_c)
        if dev > 0: tas *= 1 - 0.02*(dev/15.0); ff *= 1 - 0.025*(dev/15.0)
        elif dev < 0: tas *= 1 + 0.01*((-dev)/15.0); ff *= 1 + 0.03*((-dev)/15.0)

    tas *= (1.0 + 0.033*((650.0 - float(weight))/100.0))
    return max(0.0, tas), max(0.0, ff)

def roc_interp(pa, temp):
    pas = sorted(ROC_ENR.keys()); pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    temps = [-25,0,25,50]; t = clamp(temp, temps[0], temps[-1])
    if t <= 0: t0, t1 = -25, 0
    elif t <= 25: t0, t1 = 0, 25
    else: t0, t1 = 25, 50
    v00, v01 = ROC_ENR[p0][t0], ROC_ENR[p0][t1]
    v10, v11 = ROC_ENR[p1][t0], ROC_ENR[p1][t1]
    v0 = interp1(t, t0, t1, v00, v01); v1 = interp1(t, t0, t1, v10, v11)
    return max(1.0, interp1(pa_c, p0, p1, v0, v1) * ROC_FACTOR)

def vy_interp(pa):
    pas = sorted(VY.keys()); pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    return interp1(pa_c, p0, p1, VY[p0], VY[p1])

# ========================
# STATE
# ========================
def ens(k, v): return st.session_state.setdefault(k, v)
ens("qnh", 1013); ens("oat", 15); ens("mag_var", 1); ens("mag_is_e", False)
ens("rpm_climb", 2250); ens("rpm_cruise", 2100); ens("rpm_desc", 1800)
ens("desc_angle", 3.0); ens("weight", 650.0)
ens("start_clock", ""); ens("start_efob", 85.0)
ens("ck_default", 2)
ens("legs_df", pd.DataFrame(columns=[
    "TC (°T)","Dist (nm)","Alt0 (ft)","Alt1 (ft)","Wind FROM (°T)","Wind (kt)","Hold (min)","Hold FF (L/h,0=auto)","CK (min)"
]))

# ========================
# HEADER — GLOBAL INPUTS
# ========================
st.title("NAVLOG — v10 (AFM) · Focus Mode")

with st.container():
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.session_state.qnh = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh))
        st.session_state.oat = st.number_input("OAT (°C)", -40, 50, int(st.session_state.oat))
    with c2:
        st.session_state.mag_var = st.number_input("Mag Var (°)", 0, 30, int(st.session_state.mag_var))
        st.session_state.mag_is_e = st.selectbox("Variante", ["W → +Var", "E → -Var"], index=(1 if st.session_state.mag_is_e else 0)) == "E"
    with c3:
        st.session_state.weight = st.number_input("Peso (kg)", 450.0, 700.0, float(st.session_state.weight), step=1.0)
        st.session_state.start_efob = st.number_input("EFOB inicial (L)", 0.0, 200.0, float(st.session_state.start_efob), step=0.5)
    with c4:
        st.session_state.start_clock = st.text_input("Hora off-blocks (HH:MM)", st.session_state.start_clock)
        st.caption("Se preencheres a hora, calculo ETOs finais por leg.")

c5, c6, c7, c8 = st.columns(4)
with c5:
    st.session_state.rpm_climb  = st.number_input("Climb RPM", 1800, 2265, int(st.session_state.rpm_climb), step=5)
with c6:
    st.session_state.rpm_cruise = st.number_input("Cruise RPM", 1800, 2265, int(st.session_state.rpm_cruise), step=5)
with c7:
    st.session_state.rpm_desc   = st.number_input("Descent RPM", 1600, 2265, int(st.session_state.rpm_desc), step=5)
with c8:
    st.session_state.desc_angle = st.number_input("Ângulo desc (°)", 1.0, 6.0, float(st.session_state.desc_angle), step=0.1)

st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

# ========================
# LEGS — INPUT GRID
# ========================
st.subheader("1) Preenche as legs numa única grelha")

if st.session_state.legs_df.empty:
    # mostra uma linha vazia para orientar o input
    st.session_state.legs_df = pd.DataFrame([{
        "TC (°T)":0.0,"Dist (nm)":0.0,"Alt0 (ft)":0.0,"Alt1 (ft)":0.0,
        "Wind FROM (°T)":0,"Wind (kt)":0,"Hold (min)":0.0,"Hold FF (L/h,0=auto)":0.0,"CK (min)":st.session_state.ck_default
    }])

edited = st.data_editor(
    st.session_state.legs_df,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "TC (°T)":st.column_config.NumberColumn(format="%.1f", min_value=0.0, max_value=359.9, step=0.1),
        "Dist (nm)":st.column_config.NumberColumn(format="%.1f", min_value=0.0, step=0.1),
        "Alt0 (ft)":st.column_config.NumberColumn(format="%.0f", min_value=0.0, step=50.0),
        "Alt1 (ft)":st.column_config.NumberColumn(format="%.0f", min_value=0.0, step=50.0),
        "Wind FROM (°T)":st.column_config.NumberColumn(format="%d", min_value=0, max_value=360, step=1),
        "Wind (kt)":st.column_config.NumberColumn(format="%d", min_value=0, max_value=150, step=1),
        "Hold (min)":st.column_config.NumberColumn(format="%.1f", min_value=0.0, step=0.5),
        "Hold FF (L/h,0=auto)":st.column_config.NumberColumn(format="%.1f", min_value=0.0, step=0.1),
        "CK (min)":st.column_config.NumberColumn(format="%d", min_value=1, max_value=10, step=1),
    },
    hide_index=True
)
st.session_state.legs_df = edited

cact1, cact2, cact3 = st.columns([2,2,6])
with cact1:
    if st.button("➕ Adicionar linha vazia"):
        st.session_state.legs_df = pd.concat([st.session_state.legs_df, pd.DataFrame([{
            "TC (°T)":0.0,"Dist (nm)":0.0,"Alt0 (ft)":0.0,"Alt1 (ft)":0.0,
            "Wind FROM (°T)":0,"Wind (kt)":0,"Hold (min)":0.0,"Hold FF (L/h,0=auto)":0.0,"CK (min)":st.session_state.ck_default
        }])], ignore_index=True)

with cact2:
    if st.button("🗑️ Limpar todas as linhas"):
        st.session_state.legs_df = pd.DataFrame(columns=st.session_state.legs_df.columns)

st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

# ========================
# ENGINE — CÁLCULO
# ========================
def build_segments(tc, dist, alt0, alt1, wfrom, wkt, params, hold_min=0.0, hold_ff_input=0.0):
    """
    devolve: segments (1..3), tot_sec, tot_burn, toc_tod_marker (dict or None), main_heading_block (TH/MH/WCA/GS)
    """
    qnh, oat, mag_var, mag_is_e = params['qnh'], params['oat'], params['mag_var'], params['mag_is_e']
    rpm_climb, rpm_cruise, rpm_desc, desc_angle, weight = params['rpm_climb'], params['rpm_cruise'], params['rpm_desc'], params['desc_angle'], params['weight']

    pa0 = press_alt(alt0, qnh); pa1 = press_alt(alt1, qnh); pa_avg = (pa0 + pa1)/2.0
    Vy  = vy_interp(pa0)
    ROC = max(1.0, roc_interp(pa0, oat))   # ft/min
    TAS_climb = Vy
    FF_climb  = cruise_lookup((pa0 + pa1)/2.0, int(rpm_climb), oat, weight)[1]
    TAS_cru, FF_cru = cruise_lookup(pa1, int(rpm_cruise), oat, weight)
    TAS_desc, FF_desc = cruise_lookup(pa_avg, int(rpm_desc), oat, weight)

    wca_c, THc, GScl = wind_triangle(tc, TAS_climb, wfrom, wkt)
    wca_r, THr, GScr = wind_triangle(tc, TAS_cru,  wfrom, wkt)
    wca_d, THd, GSde = wind_triangle(tc, TAS_desc, wfrom, wkt)

    MHc = apply_var(THc, mag_var, mag_is_e)
    MHr = apply_var(THr, mag_var, mag_is_e)
    MHd = apply_var(THd, mag_var, mag_is_e)

    ROD = max(100.0, GSde * 5.0 * (desc_angle / 3.0))  # ft/min

    profile = "LEVEL" if abs(alt1 - alt0) < 1e-6 else ("CLIMB" if alt1 > alt0 else "DESCENT")
    segs = []; toc_tod = None; END_ALT = alt0

    if profile == "CLIMB":
        t_need_min = (alt1 - alt0) / ROC
        d_need = GScl * (t_need_min / 60.0)
        if d_need <= dist:
            tA = rt10(t_need_min * 60)
            segs.append({"name":"Climb → TOC","TH":THc,"MH":MHc,"GS":GScl,"TAS":TAS_climb,"ff":FF_climb,"time":tA,"dist":d_need,"alt0":alt0,"alt1":alt1})
            rem = dist - d_need
            if rem > 0:
                tB = rt10((rem / max(GScr, 1e-9)) * 3600)
                segs.append({"name":"Cruise","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tB,"dist":rem,"alt0":alt1,"alt1":alt1})
            END_ALT = alt1
            toc_tod = {"type":"TOC","t":rt10(t_need_min*60)}
        else:
            tA = rt10((dist / max(GScl, 1e-9)) * 3600)
            gained = ROC * (tA / 60.0)
            END_ALT = alt0 + gained
            segs.append({"name":"Climb (não atinge)","TH":THc,"MH":MHc,"GS":GScl,"TAS":TAS_climb,"ff":FF_climb,"time":tA,"dist":dist,"alt0":alt0,"alt1":END_ALT})
    elif profile == "DESCENT":
        t_need_min = (alt0 - alt1) / max(ROD, 1e-6)
        d_need = GSde * (t_need_min / 60.0)
        if d_need <= dist:
            tA = rt10(t_need_min * 60)
            segs.append({"name":"Descent → TOD","TH":THd,"MH":MHd,"GS":GSde,"TAS":TAS_desc,"ff":FF_desc,"time":tA,"dist":d_need,"alt0":alt0,"alt1":alt1})
            rem = dist - d_need
            if rem > 0:
                tB = rt10((rem / max(GScr, 1e-9)) * 3600)
                segs.append({"name":"Cruise","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tB,"dist":rem,"alt0":alt1,"alt1":alt1})
            END_ALT = alt1
            toc_tod = {"type":"TOD","t":rt10(t_need_min*60)}
        else:
            tA = rt10((dist / max(GSde, 1e-9)) * 3600)
            lost = ROD * (tA / 60.0)
            END_ALT = max(0.0, alt0 - lost)
            segs.append({"name":"Descent (não atinge)","TH":THd,"MH":MHd,"GS":GSde,"TAS":TAS_desc,"ff":FF_desc,"time":tA,"dist":dist,"alt0":alt0,"alt1":END_ALT})
    else:
        tA = rt10((dist / max(GScr, 1e-9)) * 3600)
        segs.append({"name":"Level","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tA,"dist":dist,"alt0":alt0,"alt1":END_ALT})

    # HOLD
    hold_min = max(0.0, float(hold_min))
    if hold_min > 0.0:
        hold_ff = float(hold_ff_input)
        if hold_ff <= 0:
            # auto por cruise @ Alt1 com rpm_cruise
            _, hold_ff_auto = cruise_lookup(press_alt(END_ALT, qnh), int(rpm_cruise), oat, weight)
            hold_ff = hold_ff_auto
        hold_sec = rt10(hold_min * 60.0)
        segs.append({"name":"Hold","TH":segs[-1]["TH"],"MH":segs[-1]["MH"],"GS":0.0,"TAS":0.0,"ff":hold_ff,"time":hold_sec,"dist":0.0,"alt0":END_ALT,"alt1":END_ALT})

    # burns
    for s in segs:
        s["burn"] = s["ff"] * (s["time"]/3600.0)

    tot_sec  = sum(s["time"] for s in segs)
    tot_burn = r10f(sum(s["burn"] for s in segs))

    # heading principal a exibir (Cruise se existir; senão primeiro segmento que se move)
    main = next((s for s in segs if s["name"].lower().startswith("cruise")), None)
    if main is None:
        main = next((s for s in segs if s["GS"]>0), segs[0])
    main_block = dict(TH=main["TH"], MH=main["MH"], GS=main["GS"],
                      WCA=rang(wind_triangle(tc, main["TAS"], wfrom, wkt)[0]))

    return segs, tot_sec, tot_burn, toc_tod, main_block

def compute_all(df: pd.DataFrame):
    params = dict(
        qnh=st.session_state.qnh, oat=st.session_state.oat,
        mag_var=st.session_state.mag_var, mag_is_e=st.session_state.mag_is_e,
        rpm_climb=st.session_state.rpm_climb, rpm_cruise=st.session_state.rpm_cruise,
        rpm_desc=st.session_state.rpm_desc, desc_angle=st.session_state.desc_angle,
        weight=st.session_state.weight
    )

    # clock base
    base_time = None
    if st.session_state.start_clock.strip():
        try:
            h,m = map(int, st.session_state.start_clock.split(":"))
            base_time = dt.datetime.combine(dt.date.today(), dt.time(h,m))
        except:
            base_time = None

    rows_out = []
    cum_sec = 0; cum_burn = 0.0
    efob = float(st.session_state.start_efob)

    for i, r in df.reset_index(drop=True).iterrows():
        tc   = float(r.get("TC (°T)", 0.0))
        dist = float(r.get("Dist (nm)", 0.0))
        alt0 = float(r.get("Alt0 (ft)", 0.0))
        alt1 = float(r.get("Alt1 (ft)", 0.0))
        wfrom= int(r.get("Wind FROM (°T)", 0))
        wkt  = int(r.get("Wind (kt)", 0))
        hold_min = float(r.get("Hold (min)", 0.0))
        hold_ff  = float(r.get("Hold FF (L/h,0=auto)", 0.0))
        ckmin    = int(r.get("CK (min)", st.session_state.ck_default))

        segs, tot_sec, tot_burn, toc_tod, main = build_segments(tc, dist, alt0, alt1, wfrom, wkt, params,
                                                                 hold_min=hold_min, hold_ff_input=hold_ff)

        # ETO fim da leg
        eto_end = ""
        if base_time is not None:
            eto_end = (base_time + dt.timedelta(seconds=cum_sec + tot_sec)).strftime("%H:%M")

        # perfil & markers
        s0name = segs[0]["name"]
        if "Climb" in s0name: perfil = "Climb + Cruise" if any(s["name"].startswith("Cruise") for s in segs[1:]) else "Climb"
        elif "Descent" in s0name: perfil = "Descent + Cruise" if any(s["name"].startswith("Cruise") for s in segs[1:]) else "Descent"
        else: perfil = "Level"
        if any(s["name"].startswith("Hold") for s in segs): perfil += " + Hold"

        # avisos simples
        warns = []
        if dist == 0 and abs(alt1-alt0) > 50: warns.append("Dist=0 com Δalt")
        if "não atinge" in s0name: warns.append("Não atinge alvo")
        if efob - tot_burn <= 0: warns.append("EFOB<=0")

        # update acumulados
        efob = max(0.0, r10f(efob - tot_burn))
        cum_sec  += tot_sec
        cum_burn  = r10f(cum_burn + tot_burn)

        # TOC/TOD info
        toc_tod_str = ""
        if toc_tod:
            label = toc_tod["type"]
            toc_tod_str = f"{label} @ T+{mmss(toc_tod['t'])}"

        rows_out.append({
            "Leg": i+1,
            "Perfil": perfil,
            "TH (°T)": rang(main["TH"]),
            "MH (°M)": rang(main["MH"]),
            "WCA (°)": rang(main["WCA"]),
            "GS (kt)": rint(main["GS"]),
            "ETE leg": hhmmss(tot_sec),
            "Burn leg (L)": f"{tot_burn:.1f}",
            "EFOB fim (L)": f"{efob:.1f}",
            "TOC/TOD": toc_tod_str,
            "ETO fim": eto_end,
            "Avisos": " | ".join(warns)
        })

    total_time = sum(build_segments(
        float(r.get("TC (°T)",0)), float(r.get("Dist (nm)",0)), float(r.get("Alt0 (ft)",0)), float(r.get("Alt1 (ft)",0)),
        int(r.get("Wind FROM (°T)",0)), int(r.get("Wind (kt)",0)), params
    )[1] for _, r in df.iterrows())
    total_burn = r10f(sum(float(x["Burn leg (L)"]) for x in rows_out)) if rows_out else 0.0

    return pd.DataFrame(rows_out), total_time, total_burn, efob

# ========================
# RESULTADOS — Tabela Clara
# ========================
st.subheader("2) Resultados por leg (claros e diretos)")

results_df, total_time, total_burn, efob_final = compute_all(st.session_state.legs_df)

if results_df.empty or results_df["Leg"].isna().all():
    st.info("Preenche pelo menos uma leg na grelha acima.")
else:
    st.dataframe(results_df, use_container_width=True, hide_index=True)

    st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
    st.subheader("3) Totais & Exportar")

    st.markdown(
        "<div class='kvarea'>"
        + f"<div class='kv'>⏱️ ETE total: <b>{hhmmss(total_time)}</b></div>"
        + f"<div class='kv'>⛽ Burn total: <b>{total_burn:.1f} L</b></div>"
        + f"<div class='kv'>🧯 EFOB final: <b>{efob_final:.1f} L</b></div>"
        + "</div>", unsafe_allow_html=True
    )

    cexp1, cexp2, _ = st.columns([2,2,8])
    with cexp1:
        st.download_button(
            "⬇️ Exportar resultados (CSV)",
            data=results_df.to_csv(index=False).encode("utf-8"),
            file_name="navlog_resultados.csv",
            mime="text/csv",
        )
    with cexp2:
        payload = dict(
            params=dict(
                qnh=st.session_state.qnh, oat=st.session_state.oat, mag_var=st.session_state.mag_var, mag_is_e=st.session_state.mag_is_e,
                rpm_climb=st.session_state.rpm_climb, rpm_cruise=st.session_state.rpm_cruise, rpm_desc=st.session_state.rpm_desc,
                desc_angle=st.session_state.desc_angle, weight=st.session_state.weight,
                start_clock=st.session_state.start_clock, start_efob=st.session_state.start_efob,
            ),
            legs=st.session_state.legs_df.to_dict(orient="records")
        )
        st.download_button(
            "⬇️ Exportar input (JSON)",
            data=json.dumps(payload, ensure_ascii=False, indent=2),
            file_name="navlog_input.json",
            mime="application/json",
        )

st.caption("Dica: Se quiseres, posso adicionar colunas FROM/TO e magnéticos locais por aeródromo para gerar TC automaticamente.")
