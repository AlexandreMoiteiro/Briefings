# app.py — NAVLOG v9 (AFM) — UI focado no que interessa
# Vistas: Planeamento (inputs) • Briefing (o que ver) • Papel (copiar para NAVLOG)
# Mantém: TOC/TOD, holds, CK checkpoints, ETO/clock, acumulados, export/import
# Sem tabelas (só cards/chips/metrics). ROC reage à OAT. FF de climb usa PA média.

import streamlit as st
import datetime as dt
import math, json
from math import sin, asin, radians, degrees

# ====== CONFIG ======
st.set_page_config(page_title="NAVLOG v9 (AFM) — UI Focado", layout="wide", initial_sidebar_state="collapsed")

# ====== UTILS ======
rt10 = lambda s: max(10, int(round(s/10.0)*10)) if s>0 else 0
mmss = lambda t: f"{t//60:02d}:{t%60:02d}"
hhmmss = lambda t: f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
rang  = lambda x: int(round(float(x))) % 360
rint  = lambda x: int(round(float(x)))
r10f  = lambda x: round(float(x), 1)

def wrap360(x):
    x = math.fmod(float(x), 360.0)
    return x + 360 if x < 0 else x

def angdiff(a, b): return (a - b + 180) % 360 - 180

def wind_triangle(tc, tas, wdir, wkt):
    if tas <= 0: return 0.0, wrap360(tc), 0.0
    d = radians(angdiff(wdir, tc)); cross = wkt * sin(d)
    s = max(-1, min(1, cross / max(tas, 1e-9)))
    wca = degrees(asin(s)); th = wrap360(tc + wca)
    gs = max(0.0, tas * math.cos(radians(wca)) - wkt * math.cos(d))
    return wca, th, gs

apply_var = lambda th, var, east_is_neg=False: wrap360(th - var if east_is_neg else th + var)

# ====== AFM TABLES (Tecnam P2008 — resumo) ======
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
        if dev > 0: 
            tas *= 1 - 0.02*(dev/15.0); ff *= 1 - 0.025*(dev/15.0)
        elif dev < 0: 
            tas *= 1 + 0.01*((-dev)/15.0); ff *= 1 + 0.03*((-dev)/15.0)

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

# ====== STATE ======
def ens(k, v): return st.session_state.setdefault(k, v)
ens("mag_var", 1); ens("mag_is_e", False); ens("qnh", 1013); ens("oat", 15); ens("weight", 650.0)
ens("rpm_climb", 2250); ens("rpm_cruise", 2100); ens("rpm_desc", 1800); ens("desc_angle", 3.0)
ens("start_clock", ""); ens("start_efob", 85.0)
ens("legs", [])         # cada leg: {TC, Dist, Alt0, Alt1, Wfrom, Wkt, CK, HoldMin, HoldFF}
ens("computed", [])
ens("view", "Planeamento")   # Planeamento / Briefing / Papel
ens("ck_default", 2)
ens("timeline_default", False)
ens("compact_inputs", True)

# ====== STYLE ======
CSS = """
<style>
:root{--card:#fff;--muted:#6b7280;--line:#e5e7eb;--chip:#f3f4f6}
*{font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Arial}
.card{border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:10px 0;background:var(--card);box-shadow:0 1px 1px rgba(0,0,0,.03)}
.badge{background:var(--chip);border:1px solid var(--line);border-radius:999px;padding:2px 8px;font-size:11px;margin-left:6px}
.leg-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.leg-title{font-weight:600;font-size:1.05rem}
.sep{height:1px;background:var(--line);margin:10px 0}
.kvs{display:flex;gap:8px;flex-wrap:wrap}
.kv{background:var(--chip);border:1px solid var(--line);border-radius:10px;padding:6px 8px;font-size:12px}
.tl{position:relative;margin:8px 0 18px 0;padding-bottom:46px}
.tl .bar{height:6px;background:#eef1f5;border-radius:3px}
.tl .tick{position:absolute;top:10px;width:2px;height:14px;background:#333}
.tl .cp-lbl{position:absolute;top:32px;transform:translateX(-50%);text-align:center;font-size:11px;color:#333;white-space:nowrap}
.tl .tocdot,.tl .toddot{position:absolute;top:-6px;width:14px;height:14px;border-radius:50%;transform:translateX(-50%);border:2px solid #fff;box-shadow:0 0 0 2px rgba(0,0,0,0.15)}
.tl .tocdot{background:#1f77b4}
.tl .toddot{background:#d62728}
.spacer{height:6px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{background:var(--chip);border:1px solid var(--line);border-radius:999px;padding:6px 10px;font-size:13px}
.big{font-size:14px;font-weight:600}
.note{color:#6b7280;font-size:12px}
.sticky{position:sticky;top:0;background:#ffffffcc;backdrop-filter:saturate(140%) blur(4px);z-index:50;border-bottom:1px solid var(--line);padding-bottom:6px}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ====== TIMELINE ======
def timeline(seg, cps, start_label, end_label, toc_tod=None):
    total = max(1, int(seg['time']))
    html = f"<div class='tl'><div class='bar'></div>"
    parts = []
    for cp in cps:
        pct = (cp['t']/total)*100.0
        parts.append(f"<div class='tick' style='left:{pct:.2f}%;'></div>")
        lbl = f"<div class='cp-lbl' style='left:{pct:.2f}%;'><div>T+{cp['min']}m</div><div>{cp['nm']} nm</div>" + \
              (f"<div>{cp['eto']}</div>" if cp['eto'] else "") + f"<div>EFOB {cp['efob']:.1f}</div></div>"
        parts.append(lbl)
    if toc_tod is not None and 0 < toc_tod['t'] < total:
        pct = (toc_tod['t']/total)*100.0
        cls = 'tocdot' if toc_tod['type'] == 'TOC' else 'toddot'
        parts.append(f"<div class='{cls}' title='{toc_tod['type']}' style='left:{pct:.2f}%;'></div>")
    html += ''.join(parts) + "</div>"
    st.markdown(html, unsafe_allow_html=True)
    st.caption(f"GS {rint(seg['GS'])} kt · TAS {rint(seg['TAS'])} kt · FF {rint(seg['ff'])} L/h  |  {start_label} → {end_label}")
    st.markdown("<div class='spacer'></div>", unsafe_allow_html=True)

# ====== PERFIL ======
def leg_profile_label(segments, has_hold=False):
    lbl = "—"
    if segments:
        n = len(segments); s0 = segments[0]['name']
        if "Climb" in s0:
            lbl = "Climb + Cruise" if n > 1 and "Cruise" in segments[1]['name'] else ("Climb (não atinge)" if "não atinge" in s0 else "Climb")
        elif "Descent" in s0:
            lbl = "Descent + Cruise" if n > 1 and "Cruise" in segments[1]['name'] else ("Descent (não atinge)" if "não atinge" in s0 else "Descent")
        elif "Level" in s0:
            lbl = "Level"
        else:
            lbl = s0
    if has_hold: lbl += " + Hold"
    return lbl

# ====== CÁLCULO DA LEG ======
def build_segments(tc, dist, alt0, alt1, wfrom, wkt, ck_min, params, hold_min=0.0, hold_ff_input=0.0):
    qnh, oat, mag_var, mag_is_e = params['qnh'], params['oat'], params['mag_var'], params['mag_is_e']
    rpm_climb, rpm_cruise, rpm_desc, desc_angle, weight = params['rpm_climb'], params['rpm_cruise'], params['rpm_desc'], params['desc_angle'], params['weight']

    pa0 = press_alt(alt0, qnh); pa1 = press_alt(alt1, qnh); pa_avg = (pa0 + pa1)/2.0
    Vy  = vy_interp(pa0)
    ROC = roc_interp(pa0, oat)  # OAT em efeito
    TAS_climb = Vy
    FF_climb  = cruise_lookup((pa0 + pa1)/2.0, int(rpm_climb), oat, weight)[1]
    TAS_cru, FF_cru = cruise_lookup(pa1, int(rpm_cruise), oat, weight)
    TAS_desc, FF_desc = cruise_lookup(pa_avg, int(rpm_desc), oat, weight)

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
        t_need = (alt1 - alt0) / max(ROC, 1e-6)  # min
        d_need = GScl * (t_need / 60.0)
        if d_need <= dist:
            tA = rt10(t_need * 60)
            segA = {"name":"Climb → TOC", "TH":THc, "MH":MHc, "GS":GScl, "TAS":TAS_climb, "ff":FF_climb, "time":tA, "dist":d_need, "alt0":alt0, "alt1":alt1}
            rem = dist - d_need
            if rem > 0:
                tB = rt10((rem / max(GScr, 1e-9)) * 3600)
                segB = {"name":"Cruise (após TOC)", "TH":THr, "MH":MHr, "GS":GScr, "TAS":TAS_cru, "ff":FF_cru, "time":tB, "dist":rem, "alt0":alt1, "alt1":alt1}
            END_ALT = alt1
            toc_tod_marker = {"type":"TOC", "t": rt10(t_need*60)}
        else:
            tA = rt10((dist / max(GScl, 1e-9)) * 3600)
            gained = ROC * (tA / 60.0)
            END_ALT = alt0 + gained
            segA = {"name":"Climb (não atinge)", "TH":THc, "MH":MHc, "GS":GScl, "TAS":TAS_climb, "ff":FF_climb, "time":tA, "dist":dist, "alt0":alt0, "alt1":END_ALT}
    elif profile == "DESCENT":
        t_need = (alt0 - alt1) / max(ROD, 1e-6)
        d_need = GSde * (t_need / 60.0)
        if d_need <= dist:
            tA = rt10(t_need * 60)
            segA = {"name":"Descent → TOD", "TH":THd, "MH":MHd, "GS":GSde, "TAS":TAS_desc, "ff":FF_desc, "time":tA, "dist":d_need, "alt0":alt0, "alt1":alt1}
            rem = dist - d_need
            if rem > 0:
                tB = rt10((rem / max(GScr, 1e-9)) * 3600)
                segB = {"name":"Cruise (após TOD)", "TH":THr, "MH":MHr, "GS":GScr, "TAS":TAS_cru, "ff":FF_cru, "time":tB, "dist":rem, "alt0":alt1, "alt1":alt1}
            END_ALT = alt1
            toc_tod_marker = {"type":"TOD", "t": rt10(t_need*60)}
        else:
            tA = rt10((dist / max(GSde, 1e-9)) * 3600)
            lost = ROD * (tA / 60.0)
            END_ALT = max(0.0, alt0 - lost)
            segA = {"name":"Descent (não atinge)", "TH":THd, "MH":MHd, "GS":GSde, "TAS":TAS_desc, "ff":FF_desc, "time":tA, "dist":dist, "alt0":alt0, "alt1":END_ALT}
    else:
        tA = rt10((dist / max(GScr, 1e-9)) * 3600)
        segA = {"name":"Level", "TH":THr, "MH":MHr, "GS":GScr, "TAS":TAS_cru, "ff":FF_cru, "time":tA, "dist":dist, "alt0":alt0, "alt1":END_ALT}

    segments = [segA] + ([segB] if segB else [])

    # HOLD
    hold_min = max(0.0, float(hold_min))
    if hold_min > 0.0:
        hold_ff = float(hold_ff_input)
        if hold_ff <= 0:
            _, hold_ff_auto = cruise_lookup(pa1, int(rpm_cruise), oat, weight)
            hold_ff = hold_ff_auto
        hold_sec = rt10(hold_min * 60.0)
        segH = {"name":"Hold/Espera","TH":segments[-1]["TH"],"MH":segments[-1]["MH"],
                "GS":0.0,"TAS":0.0,"ff":hold_ff,"time":hold_sec,"dist":0.0,"alt0":END_ALT,"alt1":END_ALT}
        segments.append(segH)

    # burn por segmento
    for s in segments:
        s["burn"] = s["ff"] * (s["time"] / 3600.0)

    tot_sec = sum(s['time'] for s in segments)
    tot_burn = r10f(sum(s['burn'] for s in segments))

    def cps(seg, every_min, base_clk, efob_start):
        out = []; t = 0
        if every_min <= 0: return out
        while t + every_min*60 <= seg['time']:
            t += every_min*60
            d = seg['GS']*(t/3600.0)
            burn = seg['ff']*(t/3600.0)
            eto = (base_clk + dt.timedelta(seconds=t)).strftime('%H:%M') if base_clk else ""
            efob = max(0.0, r10f(efob_start - burn))
            out.append({"t":t, "min":int(t/60), "nm":round(d,1), "eto":eto, "efob":efob})
        return out

    return {"segments":segments,"tot_sec":tot_sec,"tot_burn":tot_burn,
            "roc":roc_interp(pa0, oat),
            "rod":max(100.0,(wind_triangle(tc,cruise_lookup(pa_avg,int(rpm_desc),oat,weight)[0],0,0)[2])*5.0*(params['desc_angle']/3.0)),
            "toc_tod":toc_tod_marker,"ck_func":cps}

# ====== RECOMPUTE ======
def recompute_all():
    st.session_state.computed = []
    p = dict(qnh=st.session_state.qnh, oat=st.session_state.oat, mag_var=st.session_state.mag_var,
             mag_is_e=st.session_state.mag_is_e, rpm_climb=st.session_state.rpm_climb,
             rpm_cruise=st.session_state.rpm_cruise, rpm_desc=st.session_state.rpm_desc,
             desc_angle=st.session_state.desc_angle, weight=st.session_state.weight)

    base_time = None
    if st.session_state.start_clock.strip():
        try:
            h, m = map(int, st.session_state.start_clock.split(":"))
            base_time = dt.datetime.combine(dt.date.today(), dt.time(h, m))
        except: base_time = None

    carry_efob = float(st.session_state.start_efob)
    clock = base_time
    cum_sec = 0; cum_burn = 0.0

    for idx, leg in enumerate(st.session_state.legs):
        res = build_segments(
            tc=leg['TC'], dist=leg['Dist'], alt0=leg['Alt0'], alt1=leg['Alt1'],
            wfrom=leg['Wfrom'], wkt=leg['Wkt'], ck_min=leg['CK'], params=p,
            hold_min=leg.get('HoldMin', 0.0), hold_ff_input=leg.get('HoldFF', 0.0)
        )
        EF0 = carry_efob
        segs = res["segments"]

        segs[0]["EFOB_start"] = EF0
        if len(segs) > 1:
            running_burn = segs[0]['burn']
            for k in range(1, len(segs)):
                segs[k]["EFOB_start"] = max(0.0, r10f(EF0 - running_burn))
                running_burn += segs[k]['burn']

        base1 = clock
        t_cursor = 0
        for k, seg in enumerate(segs):
            if base1:
                seg["clock_start"] = (base1 + dt.timedelta(seconds=t_cursor)).strftime('%H:%M')
                seg["clock_end"]   = (base1 + dt.timedelta(seconds=t_cursor + seg['time'])).strftime('%H:%M')
            else:
                seg["clock_start"] = 'T+{}'.format(mmss(t_cursor))
                seg["clock_end"]   = 'T+{}'.format(mmss(t_cursor + seg['time']))
            t_cursor += seg['time']

        cp_list = []
        for k, seg in enumerate(segs):
            base_k = (clock + dt.timedelta(seconds=sum(s['time'] for s in segs[:k]))) if clock else None
            efob_k = segs[k].get("EFOB_start", EF0)
            cp_list.append(res["ck_func"](seg, int(st.session_state.legs[idx]['CK']), base_k, efob_k) if seg['GS']>0 else [])

        clock = (clock + dt.timedelta(seconds=res['tot_sec'])) if clock else None
        carry_efob = max(0.0, r10f(carry_efob - sum(s['burn'] for s in segs)))
        carry_alt = segs[-1]['alt1']
        cum_sec += res['tot_sec']; cum_burn = r10f(cum_burn + res['tot_burn'])

        st.session_state.computed.append({
            "segments": segs, "tot_sec": res["tot_sec"], "tot_burn": res["tot_burn"],
            "roc": res["roc"], "rod": res["rod"], "toc_tod": res["toc_tod"],
            "cps": cp_list, "carry_efob_after": carry_efob, "carry_alt_after": carry_alt,
            "cum_sec": cum_sec, "cum_burn": cum_burn
        })

# ====== CRUD / UTIL ======
def add_leg():
    d = dict(TC=0.0, Dist=0.0, Alt0=0.0, Alt1=0.0, Wfrom=0, Wkt=0, CK=int(st.session_state.ck_default), HoldMin=0.0, HoldFF=0.0)
    st.session_state.legs.append(d); recompute_all()

def update_leg(i, vals): st.session_state.legs[i].update(vals); recompute_all()
def delete_leg(i): st.session_state.legs.pop(i); recompute_all()
def move_leg(i, direction):
    j = i + (-1 if direction=="up" else 1)
    if 0 <= j < len(st.session_state.legs):
        st.session_state.legs[i], st.session_state.legs[j] = st.session_state.legs[j], st.session_state.legs[i]
        recompute_all()
def duplicate_leg(i):
    st.session_state.legs.insert(i+1, dict(st.session_state.legs[i])); recompute_all()

def apply_ck_to_all(ck_val:int):
    for i in range(len(st.session_state.legs)):
        st.session_state.legs[i]["CK"] = int(ck_val)
    recompute_all()

def export_json():
    payload = dict(
        qnh=st.session_state.qnh, oat=st.session_state.oat, mag_var=st.session_state.mag_var, mag_is_e=st.session_state.mag_is_e,
        rpm_climb=st.session_state.rpm_climb, rpm_cruise=st.session_state.rpm_cruise, rpm_desc=st.session_state.rpm_desc,
        desc_angle=st.session_state.desc_angle, weight=st.session_state.weight,
        start_clock=st.session_state.start_clock, start_efob=st.session_state.start_efob,
        ck_default=st.session_state.ck_default, legs=st.session_state.legs
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)

def import_json(file_bytes: bytes):
    try:
        obj = json.loads(file_bytes.decode("utf-8"))
        if isinstance(obj, dict) and "legs" in obj: legs = obj["legs"]
        elif isinstance(obj, list): legs = obj
        else: st.error("Formato inválido."); return
        req = {"TC","Dist","Alt0","Alt1","Wfrom","Wkt","CK","HoldMin","HoldFF"}
        clean = []
        for l in legs:
            if not isinstance(l, dict): continue
            if not req.issubset(l.keys()): continue
            clean.append({
                "TC": float(l["TC"]), "Dist": float(l["Dist"]),
                "Alt0": float(l["Alt0"]), "Alt1": float(l["Alt1"]),
                "Wfrom": int(l["Wfrom"]), "Wkt": int(l["Wkt"]),
                "CK": int(l["CK"]), "HoldMin": float(l["HoldMin"]), "HoldFF": float(l["HoldFF"])
            })
        st.session_state.legs = clean; recompute_all()
        st.success(f"Importadas {len(clean)} legs.")
    except Exception as e:
        st.error(f"Erro ao importar: {e}")

# ====== HEADER / MODO ======
st.markdown("<div class='sticky'>", unsafe_allow_html=True)
h1, h2, h3, h4 = st.columns([3,3,2,4])
with h1:
    st.title("NAVLOG — v9 (AFM)")
with h2:
    st.session_state.view = st.radio("Vista", ["Planeamento","Briefing","Papel"], horizontal=True, label_visibility="collapsed")
with h3:
    if st.button("➕ Nova leg", use_container_width=True): add_leg()
with h4:
    c41, c42 = st.columns([1,1])
    with c41:
        uploaded = st.file_uploader("Importar JSON", type=["json"], label_visibility="collapsed")
        if uploaded is not None: import_json(uploaded.read())
    with c42:
        st.download_button("Exportar JSON", data=export_json(), file_name="navlog_legs.json", mime="application/json")
st.markdown("</div>", unsafe_allow_html=True)

# ====== PARÂMETROS ESSENCIAIS ======
with st.form("hdr"):
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        st.session_state.qnh = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh))
        st.session_state.oat = st.number_input("OAT (°C)", -40, 50, int(st.session_state.oat))
    with b2:
        st.session_state.start_efob = st.number_input("EFOB inicial (L)", 0.0, 200.0, float(st.session_state.start_efob), step=0.5)
        st.session_state.start_clock = st.text_input("Hora off-blocks (HH:MM)", st.session_state.start_clock)
    with b3:
        st.session_state.weight = st.number_input("Peso (kg)", 450.0, 700.0, float(st.session_state.weight), step=1.0)
        st.session_state.ck_default = st.number_input("Checkpoints (min) por defeito", 1, 10, int(st.session_state.ck_default), step=1)
    with b4:
        st.session_state.mag_var = st.number_input("Mag Var (°)", 0, 30, int(st.session_state.mag_var))
        st.session_state.mag_is_e = st.selectbox("Var E/W", ["W","E"], index=(1 if st.session_state.mag_is_e else 0)) == "E"
    c1, c2, c3 = st.columns(3)
    with c1: st.session_state.rpm_climb  = st.number_input("Climb RPM", 1800, 2265, int(st.session_state.rpm_climb), step=5)
    with c2: st.session_state.rpm_cruise = st.number_input("Cruise RPM", 1800, 2265, int(st.session_state.rpm_cruise), step=5)
    with c3: st.session_state.rpm_desc   = st.number_input("Descent RPM", 1600, 2265, int(st.session_state.rpm_desc), step=5)
    st.session_state.desc_angle = st.number_input("Ângulo de descida (°)", 1.0, 6.0, float(st.session_state.desc_angle), step=0.1)
    st.checkbox("Timeline expandida por defeito", value=st.session_state.timeline_default, key="timeline_default")
    submitted = st.form_submit_button("Aplicar parâmetros")
if submitted: recompute_all()

# Botão: aplicar CK por defeito
if st.button("Aplicar CK a todas as legs"): apply_ck_to_all(st.session_state.ck_default)

st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

# ====== CONTEÚDO ======
if not st.session_state.legs:
    st.info("Clica **Nova leg** para começar a planear.")
else:
    recompute_all()

    # Resumo global sempre visível
    total_time = sum(c["tot_sec"] for c in st.session_state.computed)
    total_burn = r10f(sum(c["tot_burn"] for c in st.session_state.computed))
    efob_final = st.session_state.computed[-1]['carry_efob_after']
    st.markdown("<div class='kvs'>"
                + f"<div class='kv'>⏱️ ETE total: <b>{hhmmss(total_time)}</b></div>"
                + f"<div class='kv'>⛽ Burn total: <b>{total_burn:.1f} L</b></div>"
                + f"<div class='kv'>🧯 EFOB final: <b>{efob_final:.1f} L</b></div>"
                + "</div>", unsafe_allow_html=True)

    # ====== VISTA: PLANEAMENTO (inputs claros, sem dispersão) ======
    if st.session_state.view == "Planeamento":
        for i, leg in enumerate(st.session_state.legs):
            comp = st.session_state.computed[i]
            segs = comp["segments"]
            has_hold = any(s["name"].startswith("Hold") for s in segs)
            profile = leg_profile_label(segs, has_hold=has_hold)
            dist_total_leg = sum(s["dist"] for s in segs)

            # CARTÃO da LEG (inputs minimalistas)
            st.markdown("<div class='card'>", unsafe_allow_html=True)
            t1, t2, t3, t4, t5 = st.columns([4,2,2,2,2])
            with t1:
                st.markdown(f"<div class='leg-head'><span class='leg-title'>Leg {i+1}</span>"
                            f"<span class='badge'>{profile}</span></div>", unsafe_allow_html=True)
            with t2: st.metric("ETE", hhmmss(comp["tot_sec"]))
            with t3: st.metric("Burn (L)", f"{comp['tot_burn']:.1f}")
            with t4: st.metric("Dist (nm)", f"{dist_total_leg:.1f}")
            with t5: st.metric("ROC ft/min", rint(comp["roc"]))

            # Entrada essencial
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                TC   = st.number_input(f"True Course (°T) — L{i+1}", 0.0, 359.9, float(leg["TC"]), step=0.1, key=f"TC_{i}")
                Dist = st.number_input(f"Dist (nm) — L{i+1}", 0.0, 500.0, float(leg["Dist"]), step=0.1, key=f"Dist_{i}")
            with c2:
                Alt0 = st.number_input(f"Alt início (ft) — L{i+1}", 0.0, 30000.0, float(leg["Alt0"]), step=50.0, key=f"Alt0_{i}")
                Alt1 = st.number_input(f"Alt alvo (ft) — L{i+1}", 0.0, 30000.0, float(leg["Alt1"]), step=50.0, key=f"Alt1_{i}")
            with c3:
                Wfrom = st.number_input(f"Vento FROM (°T) — L{i+1}", 0, 360, int(leg["Wfrom"]), step=1, key=f"Wfrom_{i}")
                Wkt   = st.number_input(f"Vento (kt) — L{i+1}", 0, 150, int(leg["Wkt"]), step=1, key=f"Wkt_{i}")
            with c4:
                CK      = st.number_input(f"Checkpoints (min) — L{i+1}", 1, 10, int(leg["CK"]), step=1, key=f"CK_{i}")
                HoldMin = st.number_input(f"Espera (min) — L{i+1}", 0.0, 180.0, float(leg.get("HoldMin", 0.0)), step=0.5, key=f"HoldMin_{i}")
                HoldFF  = st.number_input(f"FF espera (L/h) — L{i+1} (0=auto)", 0.0, 60.0, float(leg.get("HoldFF", 0.0)), step=0.1, key=f"HoldFF_{i}")

            b1, b2, b3, b4, _ = st.columns([1,1,1,1,6])
            with b1:
                if st.button("Guardar", key=f"save_{i}"):
                    update_leg(i, dict(TC=TC, Dist=Dist, Alt0=Alt0, Alt1=Alt1, Wfrom=Wfrom, Wkt=Wkt, CK=CK, HoldMin=HoldMin, HoldFF=HoldFF))
            with b2:
                if st.button("⬆️", key=f"up_{i}"): move_leg(i, "up"); st.stop()
            with b3:
                if st.button("⬇️", key=f"down_{i}"): move_leg(i, "down"); st.stop()
            with b4:
                if st.button("🧬", key=f"dup_{i}"): duplicate_leg(i); st.stop()
            if st.button("🗑️ Apagar", key=f"del_{i}"): delete_leg(i); st.stop()

            # DICAS/ALERTAS
            warns = []
            if leg["Dist"] == 0 and abs(leg["Alt1"] - leg["Alt0"]) > 50:
                warns.append("Distância 0 com variação de altitude.")
            if any("não atinge" in s["name"] for s in comp["segments"]):
                warns.append("Perfil não atinge a altitude-alvo.")
            if comp["carry_efob_after"] <= 0:
                warns.append("EFOB no fim da leg é 0 (ou negativo).")
            if warns: st.warning(" | ".join(warns))

            st.markdown("</div>", unsafe_allow_html=True)  # /card

    # ====== VISTA: BRIEFING (o que precisas ver — sem procurar) ======
    if st.session_state.view == "Briefing":
        # “Flight strip”: chips por leg, com o essencial que escreves no papel
        st.subheader("Flight strip")
        for i, leg in enumerate(st.session_state.legs):
            comp = st.session_state.computed[i]; segs = comp["segments"]
            s0 = segs[0]; has_hold = any(s["name"].startswith("Hold") for s in segs)
            # Escolher o segmento “ativo” para vetores
            show_seg = segs[0]  # primeiro é climb/desc/level até TOC/TOD
            MH = rang(show_seg["MH"]); TH = rang(show_seg["TH"])
            GS = rint(show_seg["GS"]); TAS = rint(show_seg["TAS"])
            ETE = hhmmss(comp["tot_sec"]); BURN = f"{comp['tot_burn']:.1f}"
            EFOB_end = f"{comp['carry_efob_after']:.1f}"
            alt_lbl = f"{int(round(s0['alt0']))}→{int(round(segs[-1]['alt1']))} ft"
            toc_tod = comp["toc_tod"]
            toc_chip = ""
            if toc_tod:
                mins = int(round(toc_tod["t"]/60))
                toc_chip = f"<span class='chip'>🎯 {toc_tod['type']} T+{mins}m</span>"

            st.markdown(
                "<div class='card'>"
                f"<div class='chips'>"
                f"<span class='chip big'>Leg {i+1}</span>"
                f"<span class='chip'>MH <b>{MH:03d}°M</b></span>"
                f"<span class='chip'>GS <b>{GS} kt</b></span>"
                f"<span class='chip'>ETE <b>{ETE}</b></span>"
                f"<span class='chip'>Burn <b>{BURN} L</b></span>"
                f"<span class='chip'>EFOB fim <b>{EFOB_end} L</b></span>"
                f"<span class='chip'>Alt <b>{alt_lbl}</b></span>"
                f"{toc_chip}"
                + ("<span class='chip'>🕘 CP: "+str(int(st.session_state.legs[i]['CK']))+" min</span>" if st.session_state.legs[i]['CK']>0 else "")
                + ("<span class='chip'>⏳ Hold "+str(st.session_state.legs[i].get('HoldMin',0.0))+" min</span>" if has_hold else "")
                + "</div></div>",
                unsafe_allow_html=True
            )

        # Timeline (opcional) — expander por leg com CPs e ETO/EFOB
        st.subheader("Timelines (opcional)")
        for i, leg in enumerate(st.session_state.legs):
            comp = st.session_state.computed[i]; segs = comp["segments"]
            with st.expander(f"Timeline — Leg {i+1}", expanded=st.session_state.timeline_default):
                for idx_seg, seg in enumerate(segs):
                    if seg["GS"] <= 0: continue
                    start_lbl = seg.get("clock_start", "T+0"); end_lbl = seg.get("clock_end", mmss(seg["time"]))
                    cps = comp["cps"][idx_seg] if idx_seg < len(comp["cps"]) else []
                    mark = comp["toc_tod"] if (comp["toc_tod"] and idx_seg==0 and ("Climb" in seg["name"] or "Descent" in seg["name"])) else None
                    timeline(seg, cps, start_lbl, end_lbl, toc_tod=mark)

    # ====== VISTA: PAPEL (copiar para o NAVLOG físico) ======
    if st.session_state.view == "Papel":
        st.subheader("Campos para passar para o NAVLOG (sem contas)")
        st.caption("Ordem por leg, já com variação magnética e vento aplicados.")
        # Por leg: MH, GS, ETE (mm:ss), Burn (L), ETO (se hora inicial), EFOB no fim
        for i, leg in enumerate(st.session_state.legs):
            comp = st.session_state.computed[i]; segs = comp["segments"]
            s0 = segs[0]  # vector de saída
            MH = rang(s0["MH"]); GS = rint(s0["GS"])
            ETE = hhmmss(comp["tot_sec"]); BURN = f"{comp['tot_burn']:.1f}"
            EFOB_end = f"{comp['carry_efob_after']:.1f}"
            start_clock = s0.get("clock_start",""); end_clock = s0.get("clock_end","")
            toc_tod = comp["toc_tod"]
            toc = ""
            if toc_tod:
                mins = int(round(toc_tod["t"]/60))
                toc = f" · 🎯 {toc_tod['type']} T+{mins}m"

            # Linha compacta por leg (sem tabela)
            st.markdown(
                f"<div class='card'>"
                f"<div class='big'>Leg {i+1}</div>"
                f"<div class='note'>Escrever no papel:</div>"
                f"<div class='chips'>"
                f"<span class='chip'>MH <b>{MH:03d}°M</b></span>"
                f"<span class='chip'>GS <b>{GS} kt</b></span>"
                f"<span class='chip'>ETE <b>{ETE}</b></span>"
                f"<span class='chip'>Burn <b>{BURN} L</b></span>"
                f"<span class='chip'>EFOB fim <b>{EFOB_end} L</b></span>"
                + (f"<span class='chip'>Início {start_clock}</span><span class='chip'>Fim {end_clock}</span>" if start_clock else "")
                + (f"<span class='chip'>{toc}</span>" if toc else "")
                + f"</div></div>",
                unsafe_allow_html=True
            )

    # ====== TOTAIS POR FASE ======
    climb_s = 0; level_s = 0; desc_s = 0
    for comp in st.session_state.computed:
        for seg in comp["segments"]:
            n = seg["name"].lower()
            if "climb" in n: climb_s += seg["time"]
            elif "descent" in n: desc_s += seg["time"]
            else: level_s += seg["time"]
    st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
    s1, s2, s3, s4 = st.columns(4)
    with s1: st.metric("Tempo em Climb", hhmmss(climb_s))
    with s2: st.metric("Tempo em Level (inclui Cruise/Hold)", hhmmss(level_s))
    with s3: st.metric("Tempo em Descent", hhmmss(desc_s))
    with s4: st.metric("Verificação (≈ ETE total)", hhmmss(climb_s + level_s + desc_s))

