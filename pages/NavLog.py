import streamlit as st
import datetime as dt
import math
from math import sin, asin, radians, degrees
from dataclasses import dataclass
from typing import List, Optional

# ====== UTILS / MATH / PERFORMANCE TABLES ======

rt10 = lambda s: max(10, int(round(s / 10.0) * 10)) if s > 0 else 0
mmss = lambda t: f"{t//60:02d}:{t%60:02d}"
hhmmss = lambda t: f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
rang = lambda x: int(round(float(x))) % 360
rint = lambda x: int(round(float(x)))
r10f = lambda x: round(float(x), 1)

def wrap360(x: float) -> float:
    x = math.fmod(float(x), 360.0)
    return x + 360 if x < 0 else x

def angdiff(a: float, b: float) -> float:
    return (a - b + 180) % 360 - 180

def wind_triangle(tc: float, tas: float, wdir: float, wkt: float):
    if tas <= 0:
        return 0.0, wrap360(tc), 0.0
    d = radians(angdiff(wdir, tc))
    cross = wkt * sin(d)
    s = max(-1, min(1, cross / max(tas, 1e-9)))
    wca = degrees(asin(s))
    th = wrap360(tc + wca)
    gs = max(0.0, tas * math.cos(radians(wca)) - wkt * math.cos(d))
    return wca, th, gs

apply_var = lambda th, var, east_is_neg=False: wrap360(th - var if east_is_neg else th + var)

# Performance / tables for climb, cruise, etc.

ROC_ENR = {
    0:   {-25:981,   0:835,   25:704,  50:586},
    2000:{-25:870,   0:726,   25:597,  50:481},
    4000:{-25:759,   0:617,   25:491,  50:377},
    6000:{-25:648,   0:509,   25:385,  50:273},
    8000:{-25:538,   0:401,   25:279,  50:170},
    10000:{-25:428,   0:294,   25:174,  50:66},
    12000:{-25:319,   0:187,   25:69,   50:-37},
    14000:{-25:210,   0:80,    25:-35,  50:-139}
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
clamp = lambda v, lo, hi: max(lo, min(hi, v))

def interp1(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t*(y1 - y0)

def cruise_lookup(pa: float, rpm: int, oat: float, weight: float) -> (float, float):
    rpm = min(int(rpm), 2265)
    pas = sorted(CRUISE.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])
    table0 = CRUISE[p0]
    table1 = CRUISE[p1]

    def lookup_in_table(table):
        rpms = sorted(table.keys())
        if rpm in table:
            return table[rpm]
        if rpm < rpms[0]:
            lo, hi = rpms[0], rpms[1]
        elif rpm > rpms[-1]:
            lo, hi = rpms[-2], rpms[-1]
        else:
            lo = max([r for r in rpms if r <= rpm])
            hi = min([r for r in rpms if r >= rpm])
        (tas_lo, ff_lo), (tas_hi, ff_hi) = table[lo], table[hi]
        t = (rpm - lo) / (hi - lo) if hi != lo else 0
        return (tas_lo + t*(tas_hi - tas_lo), ff_lo + t*(ff_hi - ff_lo))

    tas0, ff0 = lookup_in_table(table0)
    tas1, ff1 = lookup_in_table(table1)
    tas = interp1(pa_c, p0, p1, tas0, tas1)
    ff = interp1(pa_c, p0, p1, ff0, ff1)

    if oat is not None:
        dev = oat - isa_temp(pa_c)
        if dev > 0:
            tas *= 1 - 0.02*(dev/15.0)
            ff *= 1 - 0.025*(dev/15.0)
        elif dev < 0:
            tas *= 1 + 0.01*((-dev)/15.0)
            ff *= 1 + 0.03*((-dev)/15.0)

    # weight correction
    tas *= (1.0 + 0.033*((650.0 - float(weight))/100.0))
    return max(0.0, tas), max(0.0, ff)

def roc_interp(pa: float, temp: float) -> float:
    pas = sorted(ROC_ENR.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])
    temps = [-25, 0, 25, 50]
    t = clamp(temp, temps[0], temps[-1])
    if t <= 0:
        t0, t1 = -25, 0
    elif t <= 25:
        t0, t1 = 0, 25
    else:
        t0, t1 = 25, 50
    v00, v01 = ROC_ENR[p0][t0], ROC_ENR[p0][t1]
    v10, v11 = ROC_ENR[p1][t0], ROC_ENR[p1][t1]
    v0 = interp1(t, t0, t1, v00, v01)
    v1 = interp1(t, t0, t1, v10, v11)
    return max(1.0, interp1(pa_c, p0, p1, v0, v1) * ROC_FACTOR)

def vy_interp(pa: float) -> float:
    pas = sorted(VY.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])
    return interp1(pa_c, p0, p1, VY[p0], VY[p1])


# ====== DATA MODELS ======

@dataclass
class Segment:
    name: str
    alt0: float
    alt1: float
    time_s: int
    dist_nm: float
    gs: float
    tas: float
    ff: float
    burn: float
    th: float
    mh: float
    segment_type: str  # e.g. "climb", "cruise", "descent"
    # extras to attach: efob_start, clock_start, clock_end
    efob_start: Optional[float] = None
    clock_start: Optional[str] = None
    clock_end: Optional[str] = None

@dataclass
class TocTodMarker:
    type: str
    time_s: int

@dataclass
class LegResult:
    segments: List[Segment]
    tot_time: int
    tot_burn: float
    toc_tod: Optional[TocTodMarker]

@dataclass
class FlightPlan:
    leg_inputs: List[dict]
    leg_results: List[LegResult]
    cum_time: int
    cum_burn: float
    final_efob: float


# ====== CORE CALCULATION FUNCTIONS ======

def compute_leg(tc: float, dist_nm: float, alt0: float, alt1: float,
                wfrom: float, wkt: float, ck_min: int,
                params: dict) -> LegResult:
    """
    Compute one leg, returning split segments and totals.
    """
    qnh = params["qnh"]
    oat = params["oat"]
    mag_var = params["mag_var"]
    mag_is_e = params["mag_is_e"]
    rpm_climb = params["rpm_climb"]
    rpm_cruise = params["rpm_cruise"]
    rpm_desc = params["rpm_desc"]
    desc_angle = params["desc_angle"]
    weight = params["weight"]

    # pressure altitudes
    pa0 = press_alt(alt0, qnh)
    pa1 = press_alt(alt1, qnh)
    pa_avg = (pa0 + pa1) / 2.0

    # performance / climb
    ROC = roc_interp(pa0, oat)
    TAS_climb = vy_interp(pa0)
    FF_climb = cruise_lookup(alt0 + 0.5 * max(0, alt1 - alt0), int(rpm_climb), oat, weight)[1]

    # cruise / level
    TAS_cru, FF_cru = cruise_lookup(pa1, int(rpm_cruise), oat, weight)

    # descent
    TAS_desc, FF_desc = cruise_lookup(pa_avg, int(rpm_desc), oat, weight)

    # wind calculations
    _, th_climb, gs_climb = wind_triangle(tc, TAS_climb, wfrom, wkt)
    _, th_cru, gs_cru = wind_triangle(tc, TAS_cru, wfrom, wkt)
    _, th_desc, gs_desc = wind_triangle(tc, TAS_desc, wfrom, wkt)

    mh_climb = apply_var(th_climb, mag_var, mag_is_e)
    mh_cru = apply_var(th_cru, mag_var, mag_is_e)
    mh_desc = apply_var(th_desc, mag_var, mag_is_e)

    # descent rate
    ROD = max(100.0, gs_desc * 5.0 * (desc_angle / 3.0))

    # decide profile
    if abs(alt1 - alt0) < 1e-6:
        profile = "level"
    elif alt1 > alt0:
        profile = "climb"
    else:
        profile = "descent"

    segments: List[Segment] = []
    toc_tod: Optional[TocTodMarker] = None

    if profile == "climb":
        t_need_min = (alt1 - alt0) / ROC
        d_need = gs_climb * (t_need_min / 60.0)
        if d_need <= dist_nm:
            # full climb + cruise
            t_climb_s = rt10(t_need_min * 60)
            seg_climb = Segment(
                name="Climb → TOC", alt0=alt0, alt1=alt1,
                time_s=t_climb_s, dist_nm=d_need, gs=gs_climb,
                tas=TAS_climb, ff=FF_climb, burn=FF_climb*(t_climb_s/3600.0),
                th=th_climb, mh=mh_climb, segment_type="climb"
            )
            segments.append(seg_climb)
            rem = dist_nm - d_need
            if rem > 0:
                t_cruise_s = rt10((rem / gs_cru) * 3600)
                seg_cruise = Segment(
                    name="Cruise after TOC", alt0=alt1, alt1=alt1,
                    time_s=t_cruise_s, dist_nm=rem, gs=gs_cru, tas=TAS_cru,
                    ff=FF_cru, burn=FF_cru*(t_cruise_s/3600.0),
                    th=th_cru, mh=mh_cru, segment_type="cruise"
                )
                segments.append(seg_cruise)
            toc_tod = TocTodMarker(type="TOC", time_s=rt10(t_need_min*60))
        else:
            # climb does not reach target altitude
            t_climb_s = rt10((dist_nm / gs_climb) * 3600)
            gained_alt = ROC * (t_climb_s / 60.0)
            alt_end = alt0 + gained_alt
            seg_climb = Segment(
                name="Climb (not reach)", alt0=alt0, alt1=alt_end,
                time_s=t_climb_s, dist_nm=dist_nm, gs=gs_climb,
                tas=TAS_climb, ff=FF_climb, burn=FF_climb*(t_climb_s/3600.0),
                th=th_climb, mh=mh_climb, segment_type="climb"
            )
            segments.append(seg_climb)

    elif profile == "descent":
        t_need_min = (alt0 - alt1) / ROD
        d_need = gs_desc * (t_need_min / 60.0)
        if d_need <= dist_nm:
            t_desc_s = rt10(t_need_min * 60)
            seg_desc = Segment(
                name="Descent → TOD", alt0=alt0, alt1=alt1,
                time_s=t_desc_s, dist_nm=d_need, gs=gs_desc, tas=TAS_desc,
                ff=FF_desc, burn=FF_desc*(t_desc_s/3600.0),
                th=th_desc, mh=mh_desc, segment_type="descent"
            )
            segments.append(seg_desc)
            rem = dist_nm - d_need
            if rem > 0:
                t_cruise_s = rt10((rem / gs_cru) * 3600)
                seg_cr = Segment(
                    name="Cruise after TOD", alt0=alt1, alt1=alt1,
                    time_s=t_cruise_s, dist_nm=rem, gs=gs_cru, tas=TAS_cru,
                    ff=FF_cru, burn=FF_cru*(t_cruise_s/3600.0),
                    th=th_cru, mh=mh_cru, segment_type="cruise"
                )
                segments.append(seg_cr)
            toc_tod = TocTodMarker(type="TOD", time_s=rt10(t_need_min*60))
        else:
            t_desc_s = rt10((dist_nm / gs_desc) * 3600)
            lost_alt = ROD * (t_desc_s / 60.0)
            alt_end = max(0.0, alt0 - lost_alt)
            seg_desc = Segment(
                name="Descent (not reach)", alt0=alt0, alt1=alt_end,
                time_s=t_desc_s, dist_nm=dist_nm, gs=gs_desc,
                tas=TAS_desc, ff=FF_desc, burn=FF_desc*(t_desc_s/3600.0),
                th=th_desc, mh=mh_desc, segment_type="descent"
            )
            segments.append(seg_desc)

    else:  # level/cruise
        t_lvl_s = rt10((dist_nm / gs_cru) * 3600)
        seg_lvl = Segment(
            name="Level / Cruise", alt0=alt0, alt1=alt1,
            time_s=t_lvl_s, dist_nm=dist_nm, gs=gs_cru, tas=TAS_cru,
            ff=FF_cru, burn=FF_cru*(t_lvl_s/3600.0),
            th=th_cru, mh=mh_cru, segment_type="cruise"
        )
        segments.append(seg_lvl)

    tot_time = sum(seg.time_s for seg in segments)
    tot_burn = sum(seg.burn for seg in segments)

    return LegResult(segments=segments, tot_time=tot_time, tot_burn=tot_burn, toc_tod=toc_tod)


def propagate_flightplan(legs: List[dict], params: dict,
                         start_clock: Optional[dt.datetime], start_efob: float) -> FlightPlan:
    leg_results: List[LegResult] = []
    carry_efob = start_efob
    clock = start_clock
    cum_time = 0
    cum_burn = 0

    for leg_idx, leg in enumerate(legs):
        res = compute_leg(
            tc=leg['TC'], dist_nm=leg['Dist'],
            alt0=leg['Alt0'], alt1=leg['Alt1'],
            wfrom=leg['Wfrom'], wkt=leg['Wkt'],
            ck_min=leg['CK'], params=params
        )

        # assign efob_start, clock_start/clock_end on segments
        for seg in res.segments:
            seg.efob_start = carry_efob

        if clock is not None:
            # set segment clocks
            elapsed = 0
            for seg in res.segments:
                seg.clock_start = (clock + dt.timedelta(seconds=elapsed)).strftime('%H:%M')
                elapsed += seg.time_s
                seg.clock_end = (clock + dt.timedelta(seconds=elapsed)).strftime('%H:%M')
            # update clock
            clock = clock + dt.timedelta(seconds=res.tot_time)

        # reduce EFOB
        carry_efob = max(0.0, carry_efob - res.tot_burn)

        cum_time += res.tot_time
        cum_burn += res.tot_burn

        leg_results.append(res)

    return FlightPlan(leg_inputs=legs, leg_results=leg_results,
                      cum_time=cum_time, cum_burn=cum_burn, final_efob=carry_efob)


# ====== UI / RENDERING HELPERS ======

def timeline_html(seg: Segment, cps: List[dict], toc_tod: Optional[TocTodMarker],
                  start_label: str, end_label: str) -> str:
    total = max(1, seg.time_s)
    html = (
        f"<div class='tl'><div class='head'><div>{start_label}</div>"
        f"<div>GS {rint(seg.gs)} kt · TAS {rint(seg.tas)} kt · FF {rint(seg.ff)} L/h</div>"
        f"<div>{end_label}</div></div><div class='bar'></div>"
    )
    parts = []
    for cp in cps:
        pct = (cp['t'] / total) * 100.0
        parts.append(f"<div class='tick' style='left:{pct:.2f}%;'></div>")
        lbl = (
            f"<div class='cp-lbl' style='left:{pct:.2f}%;'>"
            f"<div>T+{cp['min']}m</div><div>{cp['nm']} nm</div>"
        )
        if cp.get('eto'):
            lbl += f"<div>{cp['eto']}</div>"
        lbl += f"<div>EFOB {cp['efob']:.1f}</div></div>"
        parts.append(lbl)
    if toc_tod is not None and 0 < toc_tod.time_s < total:
        pct = (toc_tod.time_s / total) * 100.0
        cls = 'tocdot' if toc_tod.type == 'TOC' else 'toddot'
        parts.append(f"<div class='{cls}' title='{toc_tod.type}' style='left:{pct:.2f}%;'></div>")
    html += ''.join(parts) + "</div>"
    html += "<div class='spacer'></div>"
    return html

def leg_profile_label(segments: List[Segment]) -> str:
    if not segments:
        return "—"
    name0 = segments[0].name.lower()
    if "climb" in name0:
        # if there's a cruise after
        if len(segments) > 1 and "cruise" in segments[1].name.lower():
            return "Climb + Cruise"
        if "not reach" in segments[0].name.lower():
            return "Climb (não atinge)"
        return "Climb"
    if "descent" in name0:
        if len(segments) > 1 and "cruise" in segments[1].name.lower():
            return "Descent + Cruise"
        if "not reach" in segments[0].name.lower():
            return "Descent (não atinge)"
        return "Descent"
    if "level" in name0 or "cruise" in name0:
        return "Level"
    return segments[0].name

# ====== Streamlit App: state, UI, logic ======

def init_state():
    def ens(key, val):
        if key not in st.session_state:
            st.session_state[key] = val

    ens("mag_var", 1.0)
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
    ens("computed_plan", None)

def clear_computed():
    st.session_state.computed_plan = None

def recompute_plan():
    # parse start_clock
    base_time = None
    if st.session_state.start_clock.strip():
        try:
            h, m = map(int, st.session_state.start_clock.split(":"))
            base_time = dt.datetime.combine(dt.date.today(), dt.time(h, m))
        except:
            base_time = None

    plan = propagate_fflightplan = propagate_flightplan  # alias
    fp = propagate_flightplan(
        st.session_state.legs,
        {
            "qnh": st.session_state.qnh,
            "oat": st.session_state.oat,
            "mag_var": st.session_state.mag_var,
            "mag_is_e": st.session_state.mag_is_e,
            "rpm_climb": st.session_state.rpm_climb,
            "rpm_cruise": st.session_state.rpm_cruise,
            "rpm_desc": st.session_state.rpm_desc,
            "desc_angle": st.session_state.desc_angle,
            "weight": st.session_state.weight,
        },
        base_time,
        st.session_state.start_efob
    )
    st.session_state.computed_plan = fp

def add_leg(prefill=None):
    d = dict(TC=90.0, Dist=10.0, Alt0=0.0, Alt1=4000.0, Wfrom=180, Wkt=15, CK=2)
    if prefill:
        d.update(prefill)
    st.session_state.legs.append(d)
    recompute_plan()

def update_leg(i, vals):
    st.session_state.legs[i].update(vals)
    recompute_plan()

def delete_leg(i):
    st.session_state.legs.pop(i)
    recompute_plan()

def render_css():
    CSS = """
    <style>
    .card{border:1px solid #e7e7e9;border-radius:14px;padding:14px 16px;margin-bottom:14px;background:#fff;
          box-shadow:0 1px 2px rgba(0,0,0,0.04)}
    .hrow{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 10px 0}
    .kpi{background:#fafafa;border:1px solid #eee;border-radius:10px;padding:8px 10px;min-width:120px}
    .tl{position:relative;margin:8px 0 18px 0;padding-bottom:46px}
    .tl .bar{height:6px;background:#eef1f5;border-radius:3px}
    .tl .tick{position:absolute;top:10px;width:2px;height:14px;background:#333}
    .tl .cp-lbl{position:absolute;top:32px;transform:translateX(-50%);text-align:center;font-size:11px;color:#333;white-space:nowrap}
    .tl .tocdot,.tl .toddot{position:absolute;top:-6px;width:14px;height:14px;border-radius:50%;transform:translateX(-50%);
                             border:2px solid #fff;box-shadow:0 0 0 2px rgba(0,0,0,0.15)}
    .tl .tocdot{background:#1f77b4}
    .tl .toddot{background:#d62728}
    .tl .head{display:flex;justify-content:space-between;font-size:12px;color:#555;margin-bottom:6px}
    .badge{display:inline-block;background:#eef1f5;border-radius:999px;padding:2px 8px;font-size:11px;margin-left:6px}
    .leg-head{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
    .leg-title{font-weight:600;font-size:1.05rem}
    .sep{height:1px;background:#eee;margin:8px 0}
    .pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#f6f8fb;border:1px solid #e6e9ef;
         font-size:12px;color:#333}
    .spacer{height:6px}
    </style>
    """
    st.markdown(CSS, unsafe_allow_html=True)

def main():
    st.set_page_config(page_title="NAVLOG v9 (AFM) — Clean Flow", layout="wide", initial_sidebar_state="collapsed")
    init_state()
    render_css()

    st.title("NAVLOG — v9 (AFM) • Fluxo Limpo")

    with st.form("hdr"):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.session_state.qnh = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh))
            st.session_state.oat = st.number_input("OAT (°C)", -40, 50, int(st.session_state.oat))
        with c2:
            st.session_state.mag_var = st.number_input("Mag Var (°)", 0, 30, float(st.session_state.mag_var))
            st.session_state.mag_is_e = st.selectbox("Var E/W", ["W", "E"], index=(1 if st.session_state.mag_is_e else 0)) == "E"
        with c3:
            st.session_state.weight = st.number_input("Peso (kg)", 450.0, 700.0, float(st.session_state.weight), step=1.0)
            st.session_state.start_clock = st.text_input("Hora off-blocks (HH:MM)", st.session_state.start_clock)
        with c4:
            st.session_state.rpm_climb = st.number_input("Climb RPM", 1800, 2265, int(st.session_state.rpm_climb), step=5)
            st.session_state.rpm_cruise = st.number_input("Cruise RPM", 1800, 2265, int(st.session_state.rpm_cruise), step=5)
            st.session_state.rpm_desc = st.number_input("Descent RPM", 1600, 2265, int(st.session_state.rpm_desc), step=5)
            st.session_state.desc_angle = st.number_input("Ângulo desc (°)", 1.0, 6.0, float(st.session_state.desc_angle), step=0.1)
        st.session_state.start_efob = st.number_input("EFOB inicial (L)", 0.0, 200.0, float(st.session_state.start_efob), step=0.5)
        st.form_submit_button("Aplicar parâmetros", on_click=clear_computed)

    act1, act2 = st.columns([1, 3])
    with act1:
        if st.button("➕ Nova leg", type="primary", use_container_width=True):
            if st.session_state.computed_plan:
                last = st.session_state.computed_plan
                # get last leg's final altitude as prefill
                if last.leg_results:
                    last_seg = last.leg_results[-1].segments[-1]
                    pref = dict(Alt0=last_seg.alt1, Alt1=last_seg.alt1)
                else:
                    pref = None
            elif st.session_state.legs:
                pref = dict(Alt0=st.session_state.legs[-1]['Alt1'], Alt1=st.session_state.legs[-1]['Alt1'])
            else:
                pref = None
            add_leg(prefill=pref)
    with act2:
        st.caption("Fluxo: parâmetros globais → criar legs → editar nos cartões. Cada edição recalcula e **propaga**.")

    st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

    if not st.session_state.legs:
        st.info("Sem legs ainda. Clica **Nova leg** para começar.")
        return

    # compute flight plan if not already
    if st.session_state.computed_plan is None:
        recompute_plan()

    fp: FlightPlan = st.session_state.computed_plan

    # Global summary
    total_time = fp.cum_time
    total_burn = round(fp.cum_burn, 1)
    final_efob = fp.final_efob

    s1, s2, s3 = st.columns(3)
    with s1:
        st.metric("ETE total", hhmmss(total_time))
    with s2:
        st.metric("Burn total (L)", f"{total_burn:.1f}")
    with s3:
        st.metric("EFOB final (L)", f"{final_efob:.1f}")

    st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

    # Render each leg
    cumulative_time = 0
    cumulative_burn = 0.0
    for i, leg_in in enumerate(fp.leg_inputs):
        res = fp.leg_results[i]
        profile_lbl = leg_profile_label(res.segments)
        dist_sum = sum(seg.dist_nm for seg in res.segments)

        st.markdown("<div class='card'>", unsafe_allow_html=True)
        # header row
        hc = st.columns([3,2,2,2,2,2,2,2])
        with hc[0]:
            st.markdown(f"<div class='leg-head'><span class='leg-title'>Leg {i+1}</span>"
                        f"<span class='badge'>{profile_lbl}</span></div>", unsafe_allow_html=True)
        with hc[1]: st.metric("ETE", hhmmss(res.tot_time))
        with hc[2]: st.metric("Burn (L)", f"{res.tot_burn:.1f}")
        cumulative_time += res.tot_time
        cumulative_burn += res.tot_burn
        with hc[3]: st.metric("Tempo acum.", hhmmss(cumulative_time))
        with hc[4]: st.metric("Fuel acum. (L)", f"{cumulative_burn:.1f}")
        # For ROC and ROD, (we didn't store ROD in LegResult — you can store if needed)
        # For now, skip or recompute if needed
        with hc[5]: st.metric("ROC (ft/min)", "")
        with hc[6]: st.metric("ROD (ft/min)", "")
        with hc[7]: st.metric("Dist (nm)", f"{dist_sum:.1f}")

        # editing expander
        with st.expander("Detalhes e edição desta leg", expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                TC = st.number_input(f"True Course (°T) — L{i+1}", 0.0, 359.9, float(leg_in['TC']), step=0.1, key=f"TC_{i}")
                Dist = st.number_input(f"Distância (nm) — L{i+1}", 0.0, 500.0, float(leg_in['Dist']), step=0.1, key=f"Dist_{i}")
            with c2:
                Alt0 = st.number_input(f"Alt início (ft) — L{i+1}", 0.0, 30000.0, float(leg_in['Alt0']), step=50.0, key=f"Alt0_{i}")
                Alt1 = st.number_input(f"Alt alvo (ft) — L{i+1}", 0.0, 30000.0, float(leg_in['Alt1']), step=50.0, key=f"Alt1_{i}")
            with c3:
                Wfrom = st.number_input(f"Vento FROM (°T) — L{i+1}", 0, 360, int(leg_in['Wfrom']), step=1, key=f"Wfrom_{i}")
                Wkt = st.number_input(f"Vento (kt) — L{i+1}", 0, 150, int(leg_in['Wkt']), step=1, key=f"Wkt_{i}")
            with c4:
                CK = st.number_input(f"Checkpoints (min) — L{i+1}", 1, 10, int(leg_in['CK']), step=1, key=f"CK_{i}")

            b1, b2, _ = st.columns([1,1,6])
            with b1:
                if st.button("Atualizar", key=f"upd_{i}", use_container_width=True):
                    update_leg(i, dict(TC=TC, Dist=Dist, Alt0=Alt0, Alt1=Alt1, Wfrom=Wfrom, Wkt=Wkt, CK=CK))
            with b2:
                if st.button("Apagar", key=f"del_{i}", use_container_width=True):
                    delete_leg(i)
                    st.stop()

            st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
            # render each segment
            for seg in res.segments:
                st.markdown(f"**Segmento — {seg.name}**")
                s_cols = st.columns(4)
                s_cols[0].metric("Alt ini→fim (ft)", f"{int(round(seg.alt0))} → {int(round(seg.alt1))}")
                s_cols[1].metric("TH/MH (°)", f"{rang(seg.th)}T / {rang(seg.mh)}M")
                s_cols[2].metric("GS/TAS (kt)", f"{rint(seg.gs)} / {rint(seg.tas)}")
                s_cols[3].metric("FF (L/h)", f"{rint(seg.ff)}")
                s2 = st.columns(3)
                s2[0].metric("Tempo", mmss(seg.time_s))
                s2[1].metric("Dist (nm)", f"{seg.dist_nm:.1f}")
                s2[2].metric("Burn (L)", f"{r10f(seg.burn):.1f}")

                # checkpoints for that segment
                # we need to compute the cp list (like your old ck_func)
                def make_cps(segment: Segment, ck_min: int, clock_start: Optional[str], efob_start: float):
                    cps = []
                    t = 0
                    while t + ck_min*60 <= segment.time_s:
                        t += ck_min*60
                        d = segment.gs * (t / 3600.0)
                        burn = segment.ff * (t / 3600.0)
                        eto = (dt.datetime.strptime(clock_start, '%H:%M') + dt.timedelta(seconds=t)).strftime('%H:%M') if clock_start else ""
                        efob = max(0.0, r10f(efob_start - burn))
                        cps.append({"t": t, "min": int(t/60), "nm": round(d,1), "eto": eto, "efob": efob})
                    return cps

                cps = make_cps(seg, leg_in['CK'], seg.clock_start, seg.efob_start)
                html = timeline_html(seg, cps, res.toc_tod, seg.clock_start or "T+0", seg.clock_end or mmss(seg.time_s))
                st.markdown(html, unsafe_allow_html=True)

                if res.toc_tod and seg == res.segments[0]:
                    # marker pill
                    st.markdown(
                        f"<span class='pill'>{res.toc_tod.type} — {mmss(res.tot_time)} • {seg.dist_nm:.1f} nm desde o início</span>",
                        unsafe_allow_html=True
                    )

        st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
        colA, colB, colC = st.columns(3)
        with colA:
            st.markdown(f"**Totais da leg** — ETE {hhmmss(res.tot_time)} • Burn {res.tot_burn:.1f} L")
        with colB:
            ef0 = res.segments[0].efob_start
            if ef0 is not None:
                st.markdown(f"**EFOB** — Start {ef0:.1f} L → End {max(0.0, ef0 - res.tot_burn):.1f} L")
        with colC:
            st.markdown(f"**Acumulado até Leg {i+1}** — Tempo {hhmmss(cumulative_time)} • Fuel {cumulative_burn:.1f} L")

        st.markdown("</div>", unsafe_allow_html=True)

    # final breakdown by phase
    climb_s = 0; level_s = 0; desc_s = 0
    for res in fp.leg_results:
        for seg in res.segments:
            name = seg.name.lower()
            if "climb" in name:
                climb_s += seg.time_s
            elif "descent" in name:
                desc_s += seg.time_s
            else:
                level_s += seg.time_s

    st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
    st.subheader("Totais por fase")
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        st.metric("Tempo em Climb", hhmmss(climb_s))
    with b2:
        st.metric("Tempo em Level (inclui Cruise)", hhmmss(level_s))
    with b3:
        st.metric("Tempo em Descent", hhmmss(desc_s))
    total_check = climb_s + level_s + desc_s
    with b4:
        st.metric("Verificação (≈ ETE total)", hhmmss(total_check))


if __name__ == "__main__":
    main()




