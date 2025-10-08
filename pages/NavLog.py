"""
NAVLOG v11 — Nova UI (Streamlit)

Redesign completo de UX mantendo 100% das funcionalidades:
- Navegação em 4 separadores: **Planejar**, **Editor (Legs)**, **Execução**, **Relatórios**
- Barra-resumo fixa no topo (ETE total, Burn total, EFOB final)
- Editor em grelha com `st.data_editor` (edição em massa, adicionar/remover linhas)
- Cards limpos no separador Execução com timeline simplificada + TOC/TOD
- Verificação e breakdown por fase preservados
- Import/Export JSON, Export CSV (legs, checkpoints e segmentos)

⚠️ Ferramenta de planeamento. Validar com AFM/POH.
"""

from __future__ import annotations
import streamlit as st
import pandas as pd
import datetime as dt
import math
import json
from typing import Optional, Dict, List, Tuple
from math import sin, asin, radians, degrees

# ==========================
# PAGE CONFIG & THEME
# ==========================
st.set_page_config(
    page_title="NAVLOG v11 — Nova UI",
    layout="wide",
    initial_sidebar_state="collapsed",
)

PRIMARY = "#2F6FED"
ACCENT  = "#00B894"
MUTED   = "#667085"
BORDER  = "#E7E7E9"
BG_SOFT = "#F7F9FC"

CSS = f"""
<style>
:root{{--primary:{PRIMARY};--accent:{ACCENT};--muted:{MUTED};--border:{BORDER};--soft:{BG_SOFT}}}
html,body, .main {{ background: var(--soft) !important; }}
.summary-dock{{position:sticky;top:0;z-index:9;background:rgba(255,255,255,0.9);backdrop-filter:saturate(180%) blur(8px);
  border-bottom:1px solid var(--border);padding:8px 6px;margin-bottom:12px;border-radius:12px}}
.kpi{{background:#fff;border:1px solid var(--border);border-radius:12px;padding:10px 12px}}
.pill{{display:inline-block;padding:3px 10px;border-radius:999px;border:1px solid var(--border);background:#fff;}}
.card{{border:1px solid var(--border);border-radius:16px;padding:14px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,0.04);margin-bottom:12px}}
.badge{{display:inline-block;background:#EEF2FF;color:#1D4ED8;border-radius:999px;padding:2px 8px;font-size:11px;margin-left:8px}}
.small{{color:var(--muted);font-size:12px}}
.btnbar{{display:flex;gap:8px;flex-wrap:wrap}}
.sep{{height:1px;background:var(--border);margin:10px 0}}

/* Timeline */
.tl{{position:relative;margin:6px 0 16px 0;padding-bottom:40px}}
.tl .head{{display:flex;justify-content:space-between;font-size:12px;color:#555;margin-bottom:6px}}
.tl .bar{{height:6px;background:#eef1f5;border-radius:3px}}
.tl .tick{{position:absolute;top:8px;width:2px;height:12px;background:#333}}
.tl .cp-lbl{{position:absolute;top:26px;transform:translateX(-50%);text-align:center;font-size:11px;color:#333;white-space:nowrap}}
.tl .tocdot,.tl .toddot{{position:absolute;top:-6px;width:14px;height:14px;border-radius:50%;transform:translateX(-50%);
                         border:2px solid #fff;box-shadow:0 0 0 2px rgba(0,0,0,0.15)}}
.tl .tocdot{{background:#1f77b4}}
.tl .toddot{{background:#d62728}}

/* Tables */
.block-title{{font-weight:700;margin-bottom:6px}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ==========================
# UTILS + MATH
# ==========================
rt10   = lambda s: max(10, int(round(s/10.0)*10)) if s>0 else 0
mmss   = lambda t: f"{t//60:02d}:{t%60:02d}"
hhmmss = lambda t: f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
rang   = lambda x: int(round(float(x))) % 360
rint   = lambda x: int(round(float(x)))
r10f   = lambda x: round(float(x), 1)
clamp  = lambda v, lo, hi: max(lo, min(hi, v))

# angles & wind triangle

def wrap360(x: float) -> float:
    x = math.fmod(float(x), 360.0)
    return x + 360 if x < 0 else x

def angdiff(a: float, b: float) -> float:
    return (a - b + 180) % 360 - 180

def wind_triangle(tc: float, tas: float, wdir: float, wkt: float) -> Tuple[float, float, float]:
    if tas <= 0:
        return 0.0, wrap360(tc), 0.0
    d = radians(angdiff(wdir, tc))
    cross = wkt * sin(d)
    s = max(-1, min(1, cross / max(tas, 1e-9)))
    wca = degrees(asin(s))
    th  = wrap360(tc + wca)
    gs  = max(0.0, tas * math.cos(radians(wca)) - wkt * math.cos(d))
    return wca, th, gs

def apply_var(true_heading: float, variation_deg: float, east_is_neg: bool=False) -> float:
    return wrap360(true_heading - variation_deg if east_is_neg else true_heading + variation_deg)

# AFM TABLES (Tecnam P2008 — resumo)
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
press_alt = lambda alt, qnh: float(alt) + (1013.0 - float(qnh))*30.0

def interp1(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t*(y1 - y0)

# PERFORMANCE LOOKUPS

def cruise_lookup(pa: float, rpm: int, oat: Optional[float], weight_kg: float) -> Tuple[float, float]:
    rpm = min(int(rpm), 2265)
    pas = sorted(CRUISE.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    table0 = CRUISE[p0]; table1 = CRUISE[p1]

    def pick(tab: Dict[int, Tuple[float,float]]):
        rpms = sorted(tab.keys())
        if rpm in tab: return tab[rpm]
        if rpm < rpms[0]: lo, hi = rpms[0], rpms[1]
        elif rpm > rpms[-1]: lo, hi = rpms[-2], rpms[-1]
        else:
            lo = max([r for r in rpms if r <= rpm]); hi = min([r for r in rpms if r >= rpm])
        (tas_lo, ff_lo), (tas_hi, ff_hi) = tab[lo], tab[hi]
        t = (rpm - lo) / (hi - lo) if hi != lo else 0
        return (tas_lo + t*(tas_hi - tas_lo), ff_lo + t*(ff_hi - ff_lo))

    tas0, ff0 = pick(table0); tas1, ff1 = pick(table1)
    tas = interp1(pa_c, p0, p1, tas0, tas1)
    ff  = interp1(pa_c, p0, p1, ff0, ff1)

    if oat is not None:
        dev = oat - isa_temp(pa_c)
        if dev > 0:
            tas *= 1 - 0.02*(dev/15.0); ff *= 1 - 0.025*(dev/15.0)
        elif dev < 0:
            tas *= 1 + 0.01*((-dev)/15.0); ff *= 1 + 0.03*((-dev)/15.0)

    tas *= (1.0 + 0.033*((650.0 - float(weight_kg))/100.0))
    return max(0.0, tas), max(0.0, ff)


def roc_interp(pa: float, temp_c: float) -> float:
    pas = sorted(ROC_ENR.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    temps = [-25,0,25,50]
    t = clamp(temp_c, temps[0], temps[-1])
    if t <= 0: t0, t1 = -25, 0
    elif t <= 25: t0, t1 = 0, 25
    else: t0, t1 = 25, 50
    v00, v01 = ROC_ENR[p0][t0], ROC_ENR[p0][t1]
    v10, v11 = ROC_ENR[p1][t0], ROC_ENR[p1][t1]
    v0 = interp1(t, t0, t1, v00, v01)
    v1 = interp1(t, t0, t1, v10, v11)
    return max(1.0, interp1(pa_c, p0, p1, v0, v1) * ROC_FACTOR)


def vy_interp(pa: float) -> float:
    pas = sorted(VY.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    return interp1(pa_c, p0, p1, VY[p0], VY[p1])

# ==========================
# STATE
# ==========================

def ens(k, v):
    return st.session_state.setdefault(k, v)

# defaults
ens("mag_var", 1)
ens("mag_is_e", False)
ens("qnh", 1013)
ens("oat", 15)
ens("weight", 650.0)
ens("rpm_climb", 2250)
ens("rpm_cruise", 2100)
ens("rpm_desc", 1800)
ens("desc_angle", 3.0)
ens("start_clock", "")
ens("start_efob", 85.0)
ens("legs_df", pd.DataFrame(columns=["TC","Dist","Alt0","Alt1","Wfrom","Wkt","CK"]))
ens("computed", [])

# ==========================
# CORE CALC
# ==========================

def build_segments(tc: float, dist: float, alt0: float, alt1: float,
                   wfrom: int, wkt: int, ck_min: int, params: Dict) -> Dict:
    qnh, oat, mag_var, mag_is_e = params['qnh'], params['oat'], params['mag_var'], params['mag_is_e']
    rpm_climb, rpm_cruise, rpm_desc = params['rpm_climb'], params['rpm_cruise'], params['rpm_desc']
    desc_angle, weight = params['desc_angle'], params['weight']

    pa0 = press_alt(alt0, qnh); pa1 = press_alt(alt1, qnh); pa_avg = (pa0 + pa1)/2.0
    Vy  = vy_interp(pa0)
    ROC = roc_interp(pa0, oat)  # ft/min

    TAS_climb = Vy
    FF_climb  = cruise_lookup(alt0 + 0.5*max(0.0, alt1-alt0), int(rpm_climb), oat, weight)[1]
    TAS_cru, FF_cru   = cruise_lookup(pa1, int(rpm_cruise), oat, weight)
    TAS_desc, FF_desc = cruise_lookup(pa_avg, int(rpm_desc),   oat, weight)

    _, THc, GScl = wind_triangle(tc, TAS_climb, wfrom, wkt)
    _, THr, GScr = wind_triangle(tc, TAS_cru,  wfrom, wkt)
    _, THd, GSde = wind_triangle(tc, TAS_desc, wfrom, wkt)

    MHc = apply_var(THc, mag_var, mag_is_e)
    MHr = apply_var(THr, mag_var, mag_is_e)
    MHd = apply_var(THd, mag_var, mag_is_e)

    ROD = max(100.0, GSde * 5.0 * (desc_angle / 3.0))  # ft/min

    profile = "LEVEL" if abs(alt1 - alt0) < 1e-6 else ("CLIMB" if alt1 > alt0 else "DESCENT")
    segA = {}; segB = None; END_ALT = alt0
    toc_tod_marker = None

    if profile == "CLIMB":
        t_need = (alt1 - alt0) / max(ROC, 1e-6)
        d_need = GScl * (t_need / 60.0)
        if d_need <= dist:
            tA = rt10(t_need * 60)
            segA = {"name":"Climb → TOC","TH":THc,"MH":MHc,"GS":GScl,"TAS":TAS_climb,"ff":FF_climb,
                    "time":tA,"dist":d_need,"alt0":alt0,"alt1":alt1}
            rem = dist - d_need
            if rem > 0:
                tB = rt10((rem / max(GScr, 1e-9)) * 3600)
                segB = {"name":"Cruise (após TOC)","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,
                        "time":tB,"dist":rem,"alt0":alt1,"alt1":alt1}
            END_ALT = alt1
            toc_tod_marker = {"type":"TOC","t": rt10(t_need*60)}
        else:
            tA = rt10((dist / max(GScl, 1e-9)) * 3600)
            gained = ROC * (tA / 60.0)
            END_ALT = alt0 + gained
            segA = {"name":"Climb (não atinge)","TH":THc,"MH":MHc,"GS":GScl,"TAS":TAS_climb,"ff":FF_climb,
                    "time":tA,"dist":dist,"alt0":alt0,"alt1":END_ALT}

    elif profile == "DESCENT":
        t_need = (alt0 - alt1) / max(ROD, 1e-6)
        d_need = GSde * (t_need / 60.0)
        if d_need <= dist:
            tA = rt10(t_need * 60)
            segA = {"name":"Descent → TOD","TH":THd,"MH":MHd,"GS":GSde,"TAS":TAS_desc,"ff":FF_desc,
                    "time":tA,"dist":d_need,"alt0":alt0,"alt1":alt1}
            rem = dist - d_need
            if rem > 0:
                tB = rt10((rem / max(GScr, 1e-9)) * 3600)
                segB = {"name":"Cruise (após TOD)","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,
                        "time":tB,"dist":rem,"alt0":alt1,"alt1":alt1}
            END_ALT = alt1
            toc_tod_marker = {"type":"TOD","t": rt10(t_need*60)}
        else:
            tA = rt10((dist / max(GSde, 1e-9)) * 3600)
            lost = ROD * (tA / 60.0)
            END_ALT = max(0.0, alt0 - lost)
            segA = {"name":"Descent (não atinge)","TH":THd,"MH":MHd,"GS":GSde,"TAS":TAS_desc,"ff":FF_desc,
                    "time":tA,"dist":dist,"alt0":alt0,"alt1":END_ALT}

    else:  # LEVEL
        tA = rt10((dist / max(GScr, 1e-9)) * 3600)
        segA = {"name":"Level","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,
                "time":tA,"dist":dist,"alt0":alt0,"alt1":END_ALT}

    segments = [segA] + ([segB] if segB else [])
    for s in segments:
        s["burn"] = s["ff"] * (s["time"] / 3600.0)

    tot_sec  = sum(s['time'] for s in segments)
    tot_burn = r10f(sum(s['burn'] for s in segments))

    def make_checkpoints(seg: Dict, every_min: int, base_clk: Optional[dt.datetime], efob_start: Optional[float]):
        max_ticks = 8
        step_min = max(1, every_min)
        duration_min = max(1, seg['time']//60)
        n_ticks = min(max_ticks, max(1, duration_min // step_min))
        seconds_between = seg['time'] // (n_ticks + 1)
        out, t = [], 0
        for _ in range(n_ticks):
            t += seconds_between
            d = seg['GS']*(t/3600.0)
            burn = seg['ff']*(t/3600.0)
            eto = (base_clk + dt.timedelta(seconds=t)).strftime('%H:%M') if base_clk else ""
            efob = max(0.0, r10f((efob_start or 0.0) - burn)) if efob_start is not None else 0.0
            out.append({"t":t, "min":int(t/60), "nm":round(d,1), "eto":eto, "efob":efob})
        return out

    return {
        "segments": segments,
        "tot_sec": tot_sec,
        "tot_burn": tot_burn,
        "roc": ROC,
        "rod": ROD,
        "toc_tod": toc_tod_marker,
        "ck_func": make_checkpoints,
    }


def recompute_all():
    st.session_state.computed = []
    params = dict(
        qnh=st.session_state.qnh, oat=st.session_state.oat, mag_var=st.session_state.mag_var,
        mag_is_e=st.session_state.mag_is_e, rpm_climb=st.session_state.rpm_climb,
        rpm_cruise=st.session_state.rpm_cruise, rpm_desc=st.session_state.rpm_desc,
        desc_angle=st.session_state.desc_angle, weight=st.session_state.weight,
    )

    base_time: Optional[dt.datetime] = None
    if st.session_state.start_clock.strip():
        try:
            h, m = map(int, st.session_state.start_clock.split(":"))
            base_time = dt.datetime.combine(dt.date.today(), dt.time(h, m))
        except Exception:
            base_time = None

    carry_efob = float(st.session_state.start_efob)
    clock = base_time
    cum_sec = 0
    cum_burn = 0.0

    legs_df = st.session_state.legs_df.copy()
    if legs_df.empty:
        return

    legs_df = legs_df.fillna(0)
    for i, leg in legs_df.iterrows():
        res = build_segments(
            tc=float(leg['TC']), dist=float(leg['Dist']), alt0=float(leg['Alt0']), alt1=float(leg['Alt1']),
            wfrom=int(leg['Wfrom']), wkt=int(leg['Wkt']), ck_min=int(leg['CK']), params=params
        )

        EF0 = carry_efob
        segs = res["segments"]
        segs[0]["EFOB_start"] = EF0
        if len(segs) > 1:
            EF1 = max(0.0, r10f(EF0 - segs[0]['burn']))
            segs[1]["EFOB_start"] = EF1

        base1 = clock
        if base1:
            segs[0]["clock_start"] = base1.strftime('%H:%M')
            segs[0]["clock_end"]   = (base1 + dt.timedelta(seconds=segs[0]['time'])).strftime('%H:%M')
        else:
            segs[0]["clock_start"] = 'T+0'
            segs[0]["clock_end"]   = mmss(segs[0]['time'])
        if len(segs) > 1:
            base2 = (base1 + dt.timedelta(seconds=segs[0]['time'])) if base1 else None
            if base2:
                segs[1]["clock_start"] = base2.strftime('%H:%M')
                segs[1]["clock_end"]   = (base2 + dt.timedelta(seconds=segs[1]['time'])).strftime('%H:%M')
            else:
                segs[1]["clock_start"] = 'T+0'
                segs[1]["clock_end"]   = mmss(segs[1]['time'])

        cpA = res["ck_func"](segs[0], int(leg['CK']), base1, EF0)
        cpB = res["ck_func"](segs[1], int(leg['CK']),
                              (base1 + dt.timedelta(seconds=segs[0]['time'])) if base1 and len(segs)>1 else None,
                              max(0.0, r10f(EF0 - segs[0]['burn'])) if len(segs)>1 else None) if len(segs)>1 else []

        clock = (clock + dt.timedelta(seconds=res['tot_sec'])) if clock else None
        carry_efob = max(0.0, r10f(carry_efob - sum(s['burn'] for s in segs)))
        carry_alt = segs[-1]['alt1']
        cum_sec  += res['tot_sec']
        cum_burn  = r10f(cum_burn + res['tot_burn'])

        st.session_state.computed.append({
            "segments": segs,
            "tot_sec": res["tot_sec"],
            "tot_burn": res["tot_burn"],
            "roc": res["roc"],
            "rod": res["rod"],
            "toc_tod": res["toc_tod"],
            "cpA": cpA, "cpB": cpB,
            "carry_efob_after": carry_efob,
            "carry_alt_after": carry_alt,
            "cum_sec": cum_sec,
            "cum_burn": cum_burn,
        })

# ==========================
# UI HELPERS
# ==========================

def profile_label(segments: List[Dict]) -> str:
    if not segments: return "—"
    s0 = segments[0]['name']
    if "Climb" in s0:
        if len(segments) > 1 and "Cruise" in segments[1]['name']: return "Climb + Cruise"
        return "Climb (não atinge)" if "não atinge" in s0 else "Climb"
    if "Descent" in s0:
        if len(segments) > 1 and "Cruise" in segments[1]['name']: return "Descent + Cruise"
        return "Descent (não atinge)" if "não atinge" in s0 else "Descent"
    if "Level" in s0: return "Level"
    return s0


def timeline(seg: Dict, cps: List[Dict], start_label: str, end_label: str, toc_tod: Optional[Dict]=None):
    total = max(1, int(seg['time']))
    html = (
        f"<div class='tl'>"
        f"<div class='head'><div>{start_label}</div>"
        f"<div>GS {rint(seg['GS'])} kt · TAS {rint(seg['TAS'])} kt · FF {rint(seg['ff'])} L/h</div>"
        f"<div>{end_label}</div></div><div class='bar'></div>"
    )
    parts = []
    for cp in cps:
        pct = (cp['t']/total)*100.0
        parts.append(f"<div class='tick' style='left:{pct:.2f}%;'></div>")
        lbl = (
            f"<div class='cp-lbl' style='left:{pct:.2f}%;'>"
            f"<div>T+{cp['min']}m</div><div>{cp['nm']} nm</div>"
            f"" + (f"<div>{cp['eto']}</div>" if cp['eto'] else "") + f"<div>EFOB {cp['efob']:.1f}</div></div>"
        )
        parts.append(lbl)
    if toc_tod is not None and 0 < toc_tod['t'] < total:
        pct = (toc_tod['t']/total)*100.0
        cls = 'tocdot' if toc_tod['type'] == 'TOC' else 'toddot'
        parts.append(f"<div class='{cls}' title='{toc_tod['type']}' style='left:{pct:.2f}%;'></div>")
    html += ''.join(parts) + "</div>"
    st.markdown(html, unsafe_allow_html=True)

# ==========================
# HEADER & NAV
# ==========================

st.title("NAVLOG — v11 • Nova UI")

with st.expander("Ajuda rápida", expanded=False):
    st.markdown(
        """
        **Fluxo**
        1) *Planejar*: ajuste parâmetros globais e EFOB inicial
        2) *Editor*: edite todas as legs numa grelha (adição/remoção)
        3) *Execução*: veja cada leg com timeline, TOC/TOD e EFOB
        4) *Relatórios*: exporte CSV/JSON e veja o breakdown final
        """
    )

# PLANEJAR — barra de parâmetros globais
with st.container(border=True):
    c1, c2, c3, c4, c5 = st.columns([1.2,1.2,1.2,1.6,1.2])
    with c1:
        st.session_state.qnh = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh))
        st.session_state.oat = st.number_input("OAT (°C)", -40, 50, int(st.session_state.oat))
    with c2:
        st.session_state.mag_var = st.number_input("Mag Var (°)", 0, 30, int(st.session_state.mag_var))
        st.session_state.mag_is_e = st.selectbox("Var E/W", ["W","E"], index=(1 if st.session_state.mag_is_e else 0)) == "E"
    with c3:
        st.session_state.weight = st.number_input("Peso (kg)", 450.0, 700.0, float(st.session_state.weight), step=1.0)
        st.session_state.start_clock = st.text_input("Hora off-blocks (HH:MM)", st.session_state.start_clock)
    with c4:
        st.session_state.rpm_climb = st.number_input("Climb RPM", 1800, 2265, int(st.session_state.rpm_climb), step=5)
        st.session_state.rpm_cruise = st.number_input("Cruise RPM", 1800, 2265, int(st.session_state.rpm_cruise), step=5)
        st.session_state.rpm_desc = st.number_input("Descent RPM", 1600, 2265, int(st.session_state.rpm_desc), step=5)
    with c5:
        st.session_state.desc_angle = st.number_input("Ângulo desc (°)", 1.0, 6.0, float(st.session_state.desc_angle), step=0.1)
        st.session_state.start_efob = st.number_input("EFOB inicial (L)", 0.0, 200.0, float(st.session_state.start_efob), step=0.5)

st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

# NAV TABS
main_tabs = st.tabs(["🛠️ Planejar","🧭 Editor (Legs)","▶️ Execução","📊 Relatórios"])

# ==========================
# TAB: PLANEJAR (Resumo + Ações)
# ==========================
with main_tabs[0]:
    st.subheader("Resumo & Ações")
    with st.container():
        c1, c2, c3, c4 = st.columns([1,1,1,2])
        with c1:
            if st.button("➕ Nova leg", type="primary", use_container_width=True):
                df = st.session_state.legs_df
                if not st.session_state.computed and not df.empty:
                    # carry alt from last line
                    last = df.iloc[-1]
                    altc = float(last.get('Alt1', 0))
                elif st.session_state.computed:
                    altc = st.session_state.computed[-1]["carry_alt_after"]
                else:
                    altc = 0
                nrow = {"TC":90.0,"Dist":10.0,"Alt0":altc,"Alt1":max(4000.0, altc),"Wfrom":180,"Wkt":15,"CK":2}
                st.session_state.legs_df = pd.concat([df, pd.DataFrame([nrow])], ignore_index=True)
        with c2:
            if st.button("🧹 Limpar legs", use_container_width=True):
                st.session_state.legs_df = pd.DataFrame(columns=["TC","Dist","Alt0","Alt1","Wfrom","Wkt","CK"])
                st.session_state.computed = []
        with c3:
            if st.button("🔁 Recalcular", use_container_width=True):
                recompute_all()
        with c4:
            st.caption("Dica: vá para **Editor** para mexer em várias legs de uma só vez.")

    st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

    # Summary dock
    recompute_all()
    if st.session_state.computed:
        total_time = sum(c["tot_sec"] for c in st.session_state.computed)
        total_burn = r10f(sum(c["tot_burn"] for c in st.session_state.computed))
        efob_final = st.session_state.computed[-1]['carry_efob_after']
        with st.container():
            st.markdown("<div class='summary-dock'>", unsafe_allow_html=True)
            k1,k2,k3 = st.columns(3)
            with k1: st.markdown(f"<div class='kpi'><div class='small'>ETE total</div><div style='font-size:22px;font-weight:700'>{hhmmss(total_time)}</div></div>", unsafe_allow_html=True)
            with k2: st.markdown(f"<div class='kpi'><div class='small'>Burn total</div><div style='font-size:22px;font-weight:700'>{total_burn:.1f} L</div></div>", unsafe_allow_html=True)
            with k3: st.markdown(f"<div class='kpi'><div class='small'>EFOB final</div><div style='font-size:22px;font-weight:700'>{efob_final:.1f} L</div></div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("Sem legs ainda. Use **Nova leg** para começar, ou vá em **Editor (Legs)**.")

# ==========================
# TAB: EDITOR (DATA GRID)
# ==========================
with main_tabs[1]:
    st.subheader("Editor de Legs (edição em massa)")
    df = st.session_state.legs_df.copy()

    col_cfg = {
        "TC": st.column_config.NumberColumn("True Course (°T)", step=0.1, min_value=0.0, max_value=359.9, format="%.1f"),
        "Dist": st.column_config.NumberColumn("Distância (nm)", step=0.1, min_value=0.0, format="%.1f"),
        "Alt0": st.column_config.NumberColumn("Alt início (ft)", step=50.0, min_value=0.0, format="%.0f"),
        "Alt1": st.column_config.NumberColumn("Alt alvo (ft)", step=50.0, min_value=0.0, format="%.0f"),
        "Wfrom": st.column_config.NumberColumn("Vento FROM (°T)", step=1, min_value=0, max_value=360, format="%d"),
        "Wkt": st.column_config.NumberColumn("Vento (kt)", step=1, min_value=0, max_value=150, format="%d"),
        "CK": st.column_config.NumberColumn("Checkpoints (min)", step=1, min_value=1, max_value=10, format="%d"),
    }

    edited = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config=col_cfg,
        key="legs_editor",
    )

    st.session_state.legs_df = edited

    c1,c2,c3 = st.columns([1,1,2])
    with c1:
        if st.button("Salvar & recalcular", type="primary", use_container_width=True):
            recompute_all()
    with c2:
        if st.button("Adicionar linha", use_container_width=True):
            st.session_state.legs_df.loc[len(st.session_state.legs_df)] = {"TC":90.0,"Dist":10.0,"Alt0":0.0,"Alt1":4000.0,"Wfrom":180,"Wkt":15,"CK":2}
    with c3:
        st.caption("Dica: pode apagar linhas diretamente na grelha (menu ••• de cada linha).")

# ==========================
# TAB: EXECUÇÃO (CARDS + TIMELINE)
# ==========================
with main_tabs[2]:
    st.subheader("Execução por Leg")
    recompute_all()

    if not st.session_state.computed:
        st.info("Nenhuma leg calculada. Preencha o **Editor** e clique em *Salvar & recalcular*.")
    else:
        for i, comp in enumerate(st.session_state.computed):
            segA = comp['segments'][0]
            dist_total_leg = sum(s['dist'] for s in comp['segments'])
            prof_lbl = profile_label(comp['segments'])

            st.markdown("<div class='card'>", unsafe_allow_html=True)
            h1,h2,h3,h4,h5,h6 = st.columns([2.2,1.3,1.3,1.3,1.3,1.6])
            with h1: st.markdown(f"<span class='pill'>Leg {i+1}</span> <span class='badge'>{prof_lbl}</span>", unsafe_allow_html=True)
            with h2: st.metric("ETE", hhmmss(comp["tot_sec"]))
            with h3: st.metric("Burn (L)", f"{comp['tot_burn']:.1f}")
            with h4: st.metric("ROC (ft/min)", rint(comp["roc"]))
            with h5: st.metric("ROD (ft/min)", rint(comp["rod"]))
            with h6: st.metric("Dist (nm)", f"{dist_total_leg:.1f}")

            # Segmento 1
            st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
            st.markdown(f"**Segmento 1 — {segA['name']}**")
            s1a, s1b, s1c, s1d = st.columns(4)
            s1a.metric("Alt ini→fim (ft)", f"{int(round(segA['alt0']))} → {int(round(segA['alt1']))}")
            s1b.metric("TH/MH (°)", f"{rang(segA['TH'])}T / {rang(segA['MH'])}M")
            s1c.metric("GS/TAS (kt)", f"{rint(segA['GS'])} / {rint(segA['TAS'])}")
            s1d.metric("FF (L/h)", f"{rint(segA['ff'])}")

            st.caption("Timeline — checkpoints (auto-otimizados)")
            start_lbl = segA.get("clock_start", "T+0"); end_lbl = segA.get("clock_end", mmss(segA['time']))
            timeline(segA, comp["cpA"], start_lbl, end_lbl,
                     toc_tod=comp["toc_tod"] if comp["toc_tod"] and comp["segments"].index(segA)==0 else None)

            # Segmento 2 (se existir)
            if len(comp['segments']) > 1:
                segB = comp['segments'][1]
                st.markdown(f"**Segmento 2 — {segB['name']}**")
                s2a, s2b, s2c, s2d = st.columns(4)
                s2a.metric("Alt ini→fim (ft)", f"{int(round(segB['alt0']))} → {int(round(segB['alt1']))}")
                s2b.metric("TH/MH (°)", f"{rang(segB['TH'])}T / {rang(segB['MH'])}M")
                s2c.metric("GS/TAS (kt)", f"{rint(segB['GS'])} / {rint(segB['TAS'])}")
                s2d.metric("FF (L/h)", f"{rint(segB['ff'])}")
                start_lbl2 = segB.get("clock_start", "T+0"); end_lbl2 = segB.get("clock_end", mmss(segB['time']))
                timeline(segB, comp["cpB"], start_lbl2, end_lbl2,
                         toc_tod=comp["toc_tod"] if comp["toc_tod"] and comp["segments"].index(segB)==1 else None)

            # Rodapé
            EF_START = comp['segments'][0].get("EFOB_start", None)
            efob_str = f"Start {EF_START:.1f} L → End {comp['carry_efob_after']:.1f} L" if EF_START is not None else "—"
            st.markdown(f"<div class='small'>EFOB: {efob_str}</div>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)

# ==========================
# TAB: RELATÓRIOS (EXPORTS + BREAKDOWN)
# ==========================
with main_tabs[3]:
    st.subheader("Relatórios & Export")
    recompute_all()

    # Breakdown por fase
    if st.session_state.computed:
        climb_s = 0; level_s = 0; desc_s = 0
        rows_segments = []
        rows_ck = []
        for idx, comp in enumerate(st.session_state.computed):
            for seg in comp["segments"]:
                name = seg["name"].lower()
                if "climb" in name:
                    climb_s += seg["time"]
                elif "descent" in name:
                    desc_s  += seg["time"]
                else:
                    level_s += seg["time"]
                rows_segments.append({
                    "Leg": idx+1,
                    "Segmento": seg['name'],
                    "Alt0": int(round(seg['alt0'])),
                    "Alt1": int(round(seg['alt1'])),
                    "TH": rang(seg['TH']),
                    "MH": rang(seg['MH']),
                    "GS": rint(seg['GS']),
                    "TAS": rint(seg['TAS']),
                    "FF (L/h)": rint(seg['ff']),
                    "Tempo (mm:ss)": mmss(seg['time']),
                    "Dist (nm)": round(seg['dist'],1),
                    "Burn (L)": r10f(seg['burn']),
                })
            # checkpoints agregados
            for cp in comp['cpA'] + comp['cpB']:
                rows_ck.append({
                    "Leg": idx+1,
                    "T+min": cp['min'],
                    "NM": cp['nm'],
                    "ETO": cp['eto'],
                    "EFOB": cp['efob'],
                })

        c1,c2,c3,c4 = st.columns(4)
        with c1: st.metric("Tempo em Climb", hhmmss(climb_s))
        with c2: st.metric("Tempo em Level (incl. Cruise)", hhmmss(level_s))
        with c3: st.metric("Tempo em Descent", hhmmss(desc_s))
        with c4: st.metric("Verificação (≈ ETE total)", hhmmss(climb_s + level_s + desc_s))

        st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
        st.markdown("<div class='block-title'>Tabela de segmentos</div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(rows_segments), use_container_width=True, hide_index=True)

        st.markdown("<div class='block-title'>Tabela de checkpoints</div>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(rows_ck), use_container_width=True, hide_index=True)

        # Export JSON / CSV
        legs_json = st.session_state.legs_df.to_json(orient='records')
        st.download_button("⬇️ Exportar legs (JSON)", data=legs_json, file_name="legs.json", mime="application/json")
        st.download_button("⬇️ Exportar segmentos (CSV)", data=pd.DataFrame(rows_segments).to_csv(index=False).encode('utf-8'), file_name="segmentos.csv", mime="text/csv")
        st.download_button("⬇️ Exportar checkpoints (CSV)", data=pd.DataFrame(rows_ck).to_csv(index=False).encode('utf-8'), file_name="checkpoints.csv", mime="text/csv")

        # Import JSON de legs
        st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
        up = st.file_uploader("Importar legs (JSON)", type=["json"])
        if up is not None:
            try:
                data = json.load(up)
                st.session_state.legs_df = pd.DataFrame(data)[["TC","Dist","Alt0","Alt1","Wfrom","Wkt","CK"]]
                st.success("Legs importadas com sucesso. Vá ao Editor para rever e recalcular.")
            except Exception as e:
                st.error(f"Falha ao importar JSON: {e}")
    else:
        st.info("Sem dados calculados ainda.")





