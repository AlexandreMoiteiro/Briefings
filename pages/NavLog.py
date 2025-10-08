"""
NAVLOG v12 — UI Stepper (Streamlit)
• UI totalmente nova com navegação por **etapas** (1) Configurar → (2) Legs → (3) Revisar → (4) Exportar
• Editor em **tabela** para legs + ações rápidas (Climb/Level/Descent)
• **KPIs ao vivo** na direita (dock), modo **impressão**, presets, e validação de entradas
• Mesma matemática/performance preservadas (Tecnam P2008), código mais modular e comentado

⚠️ Ferramenta de planejamento. Valide sempre com AFM/POH.
"""

from __future__ import annotations
import streamlit as st
import datetime as dt
import math
import pandas as pd
from typing import Optional, Dict, List, Tuple
from math import sin, asin, radians, degrees

# ==========================
# PAGE CONFIG & THEME
# ==========================
st.set_page_config(
    page_title="NAVLOG v12 — UI Stepper",
    layout="wide",
    initial_sidebar_state="expanded",
)

THEME_CSS = """
<style>
:root{
  --ink:#0f172a; --muted:#64748b; --bd:#e2e8f0; --soft:#f8fafc; --card:#fff;
  --pill:#eef2ff; --pill-bd:#c7d2fe; --ok:#10b981; --warn:#f59e0b; --err:#ef4444;
}
html, body, [data-testid="stAppViewContainer"]{background:#f6f7fb}
.card{border:1px solid var(--bd);border-radius:14px;padding:16px;background:var(--card);box-shadow:0 1px 2px rgba(0,0,0,.04)}
.header{display:flex;gap:12px;align-items:center;margin-bottom:8px}
.h1{font-weight:800;font-size:22px;color:var(--ink)}
.small{color:var(--muted);font-size:12px}
.pill{display:inline-block;padding:4px 10px;border-radius:999px;background:var(--pill);border:1px solid var(--pill-bd);
     font-size:12px;color:#1e293b}
.sep{height:1px;background:var(--bd);margin:10px 0}
.mono{font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace}

/* Stepper */
.stepper{display:flex;gap:10px;align-items:center;margin-bottom:12px}
.step{display:flex;align-items:center;gap:8px;padding:6px 10px;border:1px solid var(--bd);border-radius:12px;background:#fff}
.step.active{border-color:#6366f1;background:#eef2ff}
.step .num{width:22px;height:22px;border-radius:999px;display:grid;place-items:center;background:#6366f1;color:#fff;font-weight:700}
.step .label{font-weight:600}

/* KPIs side dock */
.dock{position:sticky;top:12px}
.kpi{background:var(--soft);border:1px solid var(--bd);border-radius:12px;padding:10px;margin-bottom:8px}
.kpi .lab{font-size:12px;color:var(--muted)}
.kpi .val{font-weight:700;font-size:20px;color:var(--ink)}

/* Timeline */
.tl{position:relative;margin:8px 0 26px 0;padding-bottom:46px}
.tl .head{display:flex;justify-content:space-between;font-size:12px;color:#334155;margin-bottom:6px}
.tl .bar{height:8px;background:#e5e7eb;border-radius:4px}
.tl .tick{position:absolute;top:10px;width:2px;height:14px;background:#1f2937}
.tl .cp-lbl{position:absolute;top:32px;transform:translateX(-50%);text-align:center;font-size:11px;color:#111827;white-space:nowrap}
.tl .tocdot,.tl .toddot{position:absolute;top:-6px;width:14px;height:14px;border-radius:50%;transform:translateX(-50%);
                         border:2px solid #fff;box-shadow:0 0 0 2px rgba(0,0,0,0.15)}
.tl .tocdot{background:#2563eb}
.tl .toddot{background:#ef4444}

/* Print */
.print [data-testid="stSidebar"], .print .stepper, .print .dock {display:none}
.print .card, .print .kpi{box-shadow:none}
</style>
"""
st.markdown(THEME_CSS, unsafe_allow_html=True)

# ==========================
# SMALL UTILS
# ==========================
rt10   = lambda s: max(10, int(round(s/10.0)*10)) if s>0 else 0
mmss   = lambda t: f"{t//60:02d}:{t%60:02d}"
hhmmss = lambda t: f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
rang   = lambda x: int(round(float(x))) % 360
rint   = lambda x: int(round(float(x)))
r10f   = lambda x: round(float(x), 1)
clamp  = lambda v, lo, hi: max(lo, min(hi, v))

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

# ==========================
# AFM TABLES (Tecnam P2008 — resumo)
# ==========================
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

# ==========================
# PERFORMANCE LOOKUPS
# ==========================

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
    t = clamp(temp_c, -25, 50)
    if t <= 0: t0, t1 = -25, 0
    elif t <= 25: t0, t1 = 0, 25
    else: t0, t1 = 25, 50
    v00, v01 = ROC_ENR[p0][t0], ROC_ENR[p0][t1]
    v10, v11 = ROC_ENR[p1][t0], ROC_ENR[p1][t1]
    v0 = interp1(t, t0, t1, v00, v01)
    v1 = interp1(t, p0, p1, v0, v0) if False else interp1(t, t0, t1, v10, v11)
    return max(1.0, interp1(pa_c, p0, p1, v0, v1) * ROC_FACTOR)


def vy_interp(pa: float) -> float:
    pas = sorted(VY.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    return interp1(pa_c, p0, p1, VY[p0], VY[p1])

# ==========================
# STATE & PRESETS
# ==========================

def ens(k, v): return st.session_state.setdefault(k, v)

ens("step", 1)
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
ens("legs", [])
ens("computed", [])
ens("print_mode", False)
ens("auto_carry_alt", True)

PRESETS = {
    "Economy": dict(rpm_cruise=2000, rpm_climb=2200, rpm_desc=1800, weight=620),
    "Normal":  dict(rpm_cruise=2100, rpm_climb=2250, rpm_desc=1850, weight=650),
    "Rápido":  dict(rpm_cruise=2250, rpm_climb=2265, rpm_desc=2000, weight=660),
}

# ==========================
# CORE — BUILD & RECOMPUTE
# ==========================

def build_segments(tc: float, dist_nm: float, alt0_ft: float, alt1_ft: float,
                   wind_from: int, wind_kt: int, ck_min: int, params: Dict) -> Dict:
    qnh, oat, mag_var, mag_is_e = params['qnh'], params['oat'], params['mag_var'], params['mag_is_e']
    rpm_climb, rpm_cruise, rpm_desc = params['rpm_climb'], params['rpm_cruise'], params['rpm_desc']
    desc_angle, weight = params['desc_angle'], params['weight']

    pa0 = press_alt(alt0_ft, qnh); pa1 = press_alt(alt1_ft, qnh); pa_avg = (pa0 + pa1)/2.0
    Vy  = vy_interp(pa0)
    ROC = roc_interp(pa0, oat)

    TAS_climb = Vy
    FF_climb  = cruise_lookup(alt0_ft + 0.5*max(0.0, alt1_ft-alt0_ft), int(rpm_climb), oat, weight)[1]
    TAS_cru, FF_cru   = cruise_lookup(pa1, int(rpm_cruise), oat, weight)
    TAS_desc, FF_desc = cruise_lookup(pa_avg, int(rpm_desc),   oat, weight)

    _, THc, GScl = wind_triangle(tc, TAS_climb, wind_from, wind_kt)
    _, THr, GScr = wind_triangle(tc, TAS_cru,  wind_from, wind_kt)
    _, THd, GSde = wind_triangle(tc, TAS_desc, wind_from, wind_kt)

    MHc = apply_var(THc, mag_var, mag_is_e)
    MHr = apply_var(THr, mag_var, mag_is_e)
    MHd = apply_var(THd, mag_var, mag_is_e)

    ROD = max(100.0, GSde * 5.0 * (desc_angle / 3.0))

    profile = "LEVEL" if abs(alt1_ft - alt0_ft) < 1e-6 else ("CLIMB" if alt1_ft > alt0_ft else "DESCENT")
    segA: Dict = {}
    segB: Optional[Dict] = None
    end_alt = alt0_ft
    toc_tod_marker = None

    if profile == "CLIMB":
        t_need_min = (alt1_ft - alt0_ft) / max(ROC, 1e-6)
        d_need_nm  = GScl * (t_need_min / 60.0)
        if d_need_nm <= dist_nm:
            tA = rt10(t_need_min * 60)
            segA = {"name":"Climb → TOC", "TH":THc, "MH":MHc, "GS":GScl, "TAS":TAS_climb, "ff":FF_climb,
                    "time":tA, "dist":d_need_nm, "alt0":alt0_ft, "alt1":alt1_ft}
            rem = dist_nm - d_need_nm
            if rem > 0:
                tB = rt10((rem / max(GScr, 1e-9)) * 3600)
                segB = {"name":"Cruise (após TOC)", "TH":THr, "MH":MHr, "GS":GScr, "TAS":TAS_cru, "ff":FF_cru,
                        "time":tB, "dist":rem, "alt0":alt1_ft, "alt1":alt1_ft}
            end_alt = alt1_ft
            toc_tod_marker = {"type":"TOC", "t": rt10(t_need_min*60)}
        else:
            tA = rt10((dist_nm / max(GScl, 1e-9)) * 3600)
            gained = ROC * (tA / 60.0)
            end_alt = alt0_ft + gained
            segA = {"name":"Climb (não atinge)", "TH":THc, "MH":MHc, "GS":GScl, "TAS":TAS_climb, "ff":FF_climb,
                    "time":tA, "dist":dist_nm, "alt0":alt0_ft, "alt1":end_alt}

    elif profile == "DESCENT":
        t_need_min = (alt0_ft - alt1_ft) / max(ROD, 1e-6)
        d_need_nm  = GSde * (t_need_min / 60.0)
        if d_need_nm <= dist_nm:
            tA = rt10(t_need_min * 60)
            segA = {"name":"Descent → TOD", "TH":THd, "MH":MHd, "GS":GSde, "TAS":TAS_desc, "ff":FF_desc,
                    "time":tA, "dist":d_need_nm, "alt0":alt0_ft, "alt1":alt1_ft}
            rem = dist_nm - d_need_nm
            if rem > 0:
                tB = rt10((rem / max(GScr, 1e-9)) * 3600)
                segB = {"name":"Cruise (após TOD)", "TH":THr, "MH":MHr, "GS":GScr, "TAS":TAS_cru, "ff":FF_cru,
                        "time":tB, "dist":rem, "alt0":alt1_ft, "alt1":alt1_ft}
            end_alt = alt1_ft
            toc_tod_marker = {"type":"TOD", "t": rt10(t_need_min*60)}
        else:
            tA = rt10((dist_nm / max(GSde, 1e-9)) * 3600)
            lost = ROD * (tA / 60.0)
            end_alt = max(0.0, alt0_ft - lost)
            segA = {"name":"Descent (não atinge)", "TH":THd, "MH":MHd, "GS":GSde, "TAS":TAS_desc, "ff":FF_desc,
                    "time":tA, "dist":dist_nm, "alt0":alt0_ft, "alt1":end_alt}

    else:  # LEVEL
        tA = rt10((dist_nm / max(GScr, 1e-9)) * 3600)
        segA = {"name":"Level", "TH":THr, "MH":MHr, "GS":GScr, "TAS":TAS_cru, "ff":FF_cru,
                "time":tA, "dist":dist_nm, "alt0":alt0_ft, "alt1":end_alt}

    segments = [segA] + ([segB] if segB else [])
    for s in segments:
        s["burn"] = s["ff"] * (s["time"] / 3600.0)

    tot_sec  = sum(s['time'] for s in segments)
    tot_burn = r10f(sum(s['burn'] for s in segments))

    def make_checkpoints(seg: Dict, every_min: int, base_clk: Optional[dt.datetime], efob_start: Optional[float]):
        max_ticks = 8
        step_min = max(1, every_min)
        duration_min = max(1, seg['time']//60)
        n_ticks = min(max_ticks, duration_min // step_min)
        if n_ticks == 0:
            return []
        seconds_between = seg['time'] // (n_ticks + 1)
        out = []
        t = 0
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

    for idx, leg in enumerate(st.session_state.legs):
        res = build_segments(
            tc=leg['TC'], dist_nm=leg['Dist'], alt0_ft=leg['Alt0'], alt1_ft=leg['Alt1'],
            wind_from=leg['Wfrom'], wind_kt=leg['Wkt'], ck_min=leg['CK'], params=params,
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
# SIDEBAR: STEP NAV & GLOBALS
# ==========================

with st.sidebar:
    st.markdown("### Navegação")
    st.session_state.step = st.radio("Etapa", options=[1,2,3,4], index=st.session_state.step-1,
                                     format_func=lambda i: {1:"1) Configurar",2:"2) Legs",3:"3) Revisar",4:"4) Exportar"}[i])

    st.markdown("---")
    st.markdown("### ✈️ Parâmetros Globais")
    col0, col1 = st.columns(2)
    with col0:
        st.session_state.qnh = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh))
        st.session_state.weight = st.number_input("Peso (kg)", 450.0, 700.0, float(st.session_state.weight), step=1.0)
    with col1:
        st.session_state.oat = st.number_input("OAT (°C)", -40, 50, int(st.session_state.oat))
        st.session_state.start_efob  = st.number_input("EFOB inicial (L)", 0.0, 200.0, float(st.session_state.start_efob), step=0.5)

    st.session_state.mag_var = st.number_input("Mag Var (°)", 0, 30, int(st.session_state.mag_var))
    st.session_state.mag_is_e = st.selectbox("Var E/W", ["W","E"], index=(1 if st.session_state.mag_is_e else 0)) == "E"

    st.markdown("### ⚙️ Performance")
    st.session_state.rpm_climb  = st.slider("Climb RPM", 1800, 2265, int(st.session_state.rpm_climb), step=5)
    st.session_state.rpm_cruise = st.slider("Cruise RPM", 1800, 2265, int(st.session_state.rpm_cruise), step=5)
    st.session_state.rpm_desc   = st.slider("Descent RPM", 1600, 2265, int(st.session_state.rpm_desc), step=5)
    st.session_state.desc_angle = st.slider("Ângulo desc (°)", 1.0, 6.0, float(st.session_state.desc_angle), step=0.1)

    st.markdown("### 🕒 Voo")
    st.session_state.start_clock = st.text_input("Hora off-blocks (HH:MM)", st.session_state.start_clock)

    st.markdown("### Presets")
    preset = st.selectbox("Selecionar", ["—"]+list(PRESETS.keys()))
    if preset != "—":
        p = PRESETS[preset]
        st.session_state.rpm_cruise = p["rpm_cruise"]
        st.session_state.rpm_climb  = p["rpm_climb"]
        st.session_state.rpm_desc   = p["rpm_desc"]
        st.session_state.weight     = p["weight"]
        st.success(f"Preset aplicado: {preset}")

    st.markdown("### Opções")
    st.session_state.auto_carry_alt = st.toggle("Auto: Alt0(n)=Alt1(n-1)", value=st.session_state.auto_carry_alt)
    st.session_state.print_mode     = st.toggle("Modo impressão", value=st.session_state.print_mode)

    if st.button("Recalcular", use_container_width=True):
        if st.session_state.legs:
            recompute_all()

# Print class
st.markdown('<script>document.documentElement.classList.'
            '+ ("add" if st.session_state.print_mode else "remove") + '("print")</script>', unsafe_allow_html=True)

# ==========================
# HEADER & LIVE KPIs DOCK
# ==========================

st.markdown('<div class="header"><div class="h1">NAVLOG — v12 • UI Stepper</div>'
            '<div class="small">Planeamento • Tecnam P2008</div></div>', unsafe_allow_html=True)

# recompute for KPIs preview
if st.session_state.legs:
    recompute_all()
    total_time = sum(c["tot_sec"] for c in st.session_state.computed)
    total_burn = r10f(sum(c["tot_burn"] for c in st.session_state.computed))
    efob_final = st.session_state.computed[-1]['carry_efob_after']
else:
    total_time = 0
    total_burn = 0.0
    efob_final = st.session_state.start_efob

left, right = st.columns([4,1])
with right:
    st.markdown('<div class="dock">', unsafe_allow_html=True)
    st.markdown(f"<div class='kpi'><div class='lab'>ETE total</div><div class='val mono'>{hhmmss(total_time)}</div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='kpi'><div class='lab'>Burn total (L)</div><div class='val mono'>{total_burn:.1f}</div></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='kpi'><div class='lab'>EFOB final (L)</div><div class='val mono'>{efob_final:.1f}</div></div>", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with left:
    # STEP INDICATOR
    st.markdown('<div class="stepper">' + ''.join([
        f"<div class='step {'active' if st.session_state.step==i else ''}'><div class='num'>{i}</div><div class='label'>{lbl}</div></div>"
        for i,lbl in [(1,'Configurar'),(2,'Legs'),(3,'Revisar'),(4,'Exportar')]
    ]) + '</div>', unsafe_allow_html=True)

    # ========== STEP 1: CONFIGURAR ==========
    if st.session_state.step == 1:
        st.markdown("#### Dicas rápidas")
        st.info("Ajuste parâmetros globais na esquerda. Use **Presets** para começar rápido. Depois avance para **Legs**.")
        st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("<div class='card'><b>Validação</b><br><span class='small'>Campos inválidos serão destacados no editor de legs.</span></div>", unsafe_allow_html=True)
        with c2:
            st.markdown("<div class='card'><b>Auto-carry</b><br><span class='small'>Quando ligado, Alt0(n) = Alt1(n-1) ao aplicar.</span></div>", unsafe_allow_html=True)

    # ========== STEP 2: LEGS ==========
    if st.session_state.step == 2:
        st.subheader("Construtor de Legs")
        colA, colB, colC = st.columns([1,1,6])
        with colA:
            if st.button("➕ Nova leg", use_container_width=True):
                if st.session_state.legs and st.session_state.auto_carry_alt:
                    last_alt = st.session_state.legs[-1]['Alt1']
                    st.session_state.legs.append(dict(TC=90.0, Dist=10.0, Alt0=float(last_alt), Alt1=float(last_alt), Wfrom=180, Wkt=15, CK=2, _del=False))
                else:
                    st.session_state.legs.append(dict(TC=90.0, Dist=10.0, Alt0=0.0, Alt1=4000.0, Wfrom=180, Wkt=15, CK=2, _del=False))
        with colB:
            if st.button("🗑️ Apagar marcadas", use_container_width=True):
                st.session_state.legs = [l for l in st.session_state.legs if not l.get('_del', False)]
        with colC:
            st.caption("Dica: use as ações rápidas abaixo para preencher colunas comuns.")

        if not st.session_state.legs:
            st.info("Sem legs ainda. Clique em **Nova leg**.")
        else:
            df = pd.DataFrame(st.session_state.legs)
            cols = ["TC","Dist","Alt0","Alt1","Wfrom","Wkt","CK","_del"]
            for c in cols:
                if c not in df.columns: df[c] = 0
            df = df[cols]

            edited = st.data_editor(
                df,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "TC":   st.column_config.NumberColumn("True Course (°T)", min_value=0.0, max_value=359.9, step=0.1, format="%.1f"),
                    "Dist": st.column_config.NumberColumn("Distância (nm)",  min_value=0.0, max_value=500.0, step=0.1, format="%.1f"),
                    "Alt0": st.column_config.NumberColumn("Alt início (ft)",  min_value=0.0, max_value=30000.0, step=50.0),
                    "Alt1": st.column_config.NumberColumn("Alt alvo (ft)",    min_value=0.0, max_value=30000.0, step=50.0),
                    "Wfrom":st.column_config.NumberColumn("Vento FROM (°T)",  min_value=0,   max_value=360, step=1),
                    "Wkt":  st.column_config.NumberColumn("Vento (kt)",       min_value=0,   max_value=150, step=1),
                    "CK":   st.column_config.NumberColumn("Checkpoints (min)",min_value=1,   max_value=10, step=1),
                    "_del": st.column_config.CheckboxColumn("Apagar", help="Marque e clique no botão acima"),
                },
                hide_index=True,
            )

            # Quick actions row
            qa1, qa2, qa3, qa4 = st.columns(4)
            with qa1:
                if st.button("Level: Alt1 = Alt0", use_container_width=True):
                    edited["Alt1"] = edited["Alt0"]
            with qa2:
                if st.button("Climb: +2000 ft", use_container_width=True):
                    edited["Alt1"] = edited["Alt0"] + 2000
            with qa3:
                if st.button("Desc: −2000 ft", use_container_width=True):
                    edited["Alt1"] = (edited["Alt0"] - 2000).clip(lower=0)
            with qa4:
                if st.button("Dist: arred. 0.1 nm", use_container_width=True):
                    edited["Dist"] = (edited["Dist"].astype(float)).round(1)

            # Apply
            if st.button("Aplicar & Recalcular", type="primary"):
                legs_new: List[Dict] = []
                prev_alt1: Optional[float] = None
                errors = []
                for idx, row in edited.iterrows():
                    tc, dist = float(row["TC"]), float(row["Dist"])
                    alt0, alt1 = float(row["Alt0"]), float(row["Alt1"])
                    wfrom, wkt, ck = int(row["Wfrom"]), int(row["Wkt"]), int(row["CK"])
                    if not (0.0 <= tc < 360.0): errors.append(f"Linha {idx+1}: TC fora do intervalo")
                    if dist <= 0: errors.append(f"Linha {idx+1}: Dist deve ser > 0")
                    if st.session_state.auto_carry_alt and prev_alt1 is not None:
                        alt0 = float(prev_alt1)
                    legs_new.append(dict(TC=tc, Dist=dist, Alt0=alt0, Alt1=alt1, Wfrom=wfrom, Wkt=wkt, CK=ck))
                    prev_alt1 = alt1
                if errors:
                    st.error("\n".join(errors))
                else:
                    st.session_state.legs = legs_new
                    recompute_all()
                    st.success("Recalcular concluído.")

    # ========== STEP 3: REVISAR ==========
    if st.session_state.step == 3:
        st.subheader("Revisão do Voo")
        if not st.session_state.computed:
            st.info("Sem dados ainda. Vá para **Legs**, aplique e volte aqui.")
        else:
            # Overview table
            rows = []
            for i, comp in enumerate(st.session_state.computed, start=1):
                segs = comp['segments']
                label = profile_label(segs)
                dist_total_leg = sum(s['dist'] for s in segs)
                rows.append(dict(
                    Leg=i, Perfil=label, ETE=hhmmss(comp['tot_sec']), Burn=f"{comp['tot_burn']:.1f}", Dist=f"{dist_total_leg:.1f}",
                    ROC=rint(comp['roc']), ROD=rint(comp['rod']), Tempo_Acum=hhmmss(comp['cum_sec']), Fuel_Acum=f"{comp['cum_burn']:.1f}",
                ))
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

            st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
            # Phase totals
            climb_s = level_s = desc_s = 0
            for comp in st.session_state.computed:
                for seg in comp["segments"]:
                    name = seg["name"].lower()
                    if "climb" in name: climb_s += seg["time"]
                    elif "descent" in name: desc_s += seg["time"]
                    else: level_s += seg["time"]
            c1, c2, c3, c4 = st.columns(4)
            with c1: st.metric("Tempo em Climb", hhmmss(climb_s))
            with c2: st.metric("Tempo em Level (inclui Cruise)", hhmmss(level_s))
            with c3: st.metric("Tempo em Descent", hhmmss(desc_s))
            with c4: st.metric("Verificação (≈ ETE total)", hhmmss(climb_s+level_s+desc_s))

            st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
            # Consolidated timeline
            st.markdown("**Timeline consolidada**")
            total_elapsed = 0
            for comp in st.session_state.computed:
                for idx, seg in enumerate(comp['segments']):
                    start_lbl = seg.get("clock_start", f"T+{mmss(total_elapsed)}")
                    end_lbl   = seg.get("clock_end", f"T+{mmss(total_elapsed + seg['time'])}")
                    timeline(seg, comp['cpA'] if idx==0 else comp['cpB'], start_lbl, end_lbl,
                             toc_tod=comp["toc_tod"] if comp["toc_tod"] and comp["segments"].index(seg)==idx else None)
                    total_elapsed += seg['time']

            # Per-leg timelines
            st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
            st.markdown("**Timelines por leg**")
            for i, comp in enumerate(st.session_state.computed, start=1):
                with st.expander(f"Leg {i}"):
                    segA = comp['segments'][0]
                    timeline(segA, comp['cpA'], segA.get("clock_start","T+0"), segA.get("clock_end", mmss(segA['time'])),
                             toc_tod=comp["toc_tod"] if comp["toc_tod"] and comp["segments"].index(segA)==0 else None)
                    if len(comp['segments'])>1:
                        segB = comp['segments'][1]
                        timeline(segB, comp['cpB'], segB.get("clock_start","T+0"), segB.get("clock_end", mmss(segB['time'])),
                                 toc_tod=comp["toc_tod"] if comp["toc_tod"] and comp["segments"].index(segB)==1 else None)

    # ========== STEP 4: EXPORTAR ==========
    if st.session_state.step == 4:
        st.subheader("Exportar / Imprimir")
        if not st.session_state.computed:
            st.info("Nada para exportar ainda. Vá para **Legs** e aplique.")
        else:
            legs_csv = pd.DataFrame(st.session_state.legs).to_csv(index=False).encode()
            st.download_button("⬇️ CSV das legs", legs_csv, file_name="navlog_legs.csv", mime="text/csv")

            seg_rows = []
            for i, comp in enumerate(st.session_state.computed, start=1):
                for j, seg in enumerate(comp['segments'], start=1):
                    seg_rows.append({
                        "leg": i, "segmento": j, "nome": seg['name'],
                        "alt0_ft": seg['alt0'], "alt1_ft": seg['alt1'],
                        "TH": rang(seg['TH']), "MH": rang(seg['MH']), "GS": rint(seg['GS']),
                        "TAS": rint(seg['TAS']), "FF_Lph": rint(seg['ff']), "tempo_s": seg['time'],
                        "dist_nm": round(seg['dist'],1), "burn_L": r10f(seg['burn']),
                    })
            seg_csv = pd.DataFrame(seg_rows).to_csv(index=False).encode()
            st.download_button("⬇️ CSV de segmentos", seg_csv, file_name="navlog_segmentos.csv", mime="text/csv")

            ck_rows = []
            for i, comp in enumerate(st.session_state.computed, start=1):
                for seg, cps in zip(comp['segments'], [comp['cpA']] + ([comp['cpB']] if len(comp['segments'])>1 else [])):
                    for k, cp in enumerate(cps, start=1):
                        ck_rows.append({"leg": i, "seg": seg['name'], "ordem": k, "T+min": cp['min'], "NM": cp['nm'], "ETO": cp['eto'], "EFOB_L": cp['efob']})
            if ck_rows:
                ck_csv = pd.DataFrame(ck_rows).to_csv(index=False).encode()
                st.download_button("⬇️ CSV de checkpoints", ck_csv, file_name="navlog_checkpoints.csv", mime="text/csv")
            else:
                st.caption("Sem checkpoints gerados (pernas muito curtas ou CK elevado).")

            st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
            st.caption("Dica: ative **Modo impressão** na navegação para ocultar controles e imprimir.")




