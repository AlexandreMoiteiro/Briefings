# app.py — NAVLOG v9 (AFM) — UI limpa, fluxo lógico e acumulados por leg
# - Cada leg é uma caixinha: header compacto + expander de detalhes
# - Resumo global no topo (ETE total / Burn total / EFOB final)
# - TOC/TOD destacados (sem sobrepor checkpoints)
# - ROC/ROD visíveis
# - Acumulado (tempo e combustível) ao fim de cada leg
# - Cruise RPM = 2100 por defeito

import streamlit as st
import datetime as dt
import math
from math import sin, asin, radians, degrees

# ====== CONFIG ======
st.set_page_config(page_title="NAVLOG v9 (AFM) — Clean Flow", layout="wide", initial_sidebar_state="collapsed")

# ====== UTILS ======
rt10 = lambda s: max(10, int(round(s/10.0)*10)) if s>0 else 0
mmss = lambda t: f"{t//60:02d}:{t%60:02d}"
hhmmss = lambda t: f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
rang = lambda x: int(round(float(x))) % 360
rint = lambda x: int(round(float(x)))
r10f = lambda x: round(float(x), 1)

def wrap360(x):
    x = math.fmod(float(x), 360.0)
    return x + 360 if x < 0 else x

def angdiff(a, b):
    return (a - b + 180) % 360 - 180

def wind_triangle(tc, tas, wdir, wkt):
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
press_alt = lambda alt, qnh: float(alt) + (1013.0 - float(qnh))*30.0
clamp = lambda v, lo, hi: max(lo, min(hi, v))

def interp1(x, x0, x1, y0, y1):
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t*(y1 - y0)

def cruise_lookup(pa, rpm, oat, weight):
    rpm = min(int(rpm), 2265)
    pas = sorted(CRUISE.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])
    table0 = CRUISE[p0]; table1 = CRUISE[p1]

    def v(tab):
        rpms = sorted(tab.keys())
        if rpm in tab: return tab[rpm]
        if rpm < rpms[0]: lo, hi = rpms[0], rpms[1]
        elif rpm > rpms[-1]: lo, hi = rpms[-2], rpms[-1]
        else:
            lo = max([r for r in rpms if r <= rpm])
            hi = min([r for r in rpms if r >= rpm])
        (tas_lo, ff_lo), (tas_hi, ff_hi) = tab[lo], tab[hi]
        t = (rpm - lo) / (hi - lo) if hi != lo else 0
        return (tas_lo + t*(tas_hi - tas_lo), ff_lo + t*(ff_hi - ff_lo))

    tas0, ff0 = v(table0); tas1, ff1 = v(table1)
    tas = interp1(pa_c, p0, p1, tas0, tas1)
    ff  = interp1(pa_c, p0, p1, ff0,  ff1)

    if oat is not None:
        dev = oat - isa_temp(pa_c)
        if dev > 0:
            tas *= 1 - 0.02*(dev/15.0)
            ff  *= 1 - 0.025*(dev/15.0)
        elif dev < 0:
            tas *= 1 + 0.01*((-dev)/15.0)
            ff  *= 1 + 0.03*((-dev)/15.0)

    tas *= (1.0 + 0.033*((650.0 - float(weight))/100.0))
    return max(0.0, tas), max(0.0, ff)

def roc_interp(pa, temp):
    pas = sorted(ROC_ENR.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])
    temps = [-25,0,25,50]
    t = clamp(temp, temps[0], temps[-1])
    if t <= 0: t0, t1 = -25, 0
    elif t <= 25: t0, t1 = 0, 25
    else: t0, t1 = 25, 50
    v00, v01 = ROC_ENR[p0][t0], ROC_ENR[p0][t1]
    v10, v11 = ROC_ENR[p1][t0], ROC_ENR[p1][t1]
    v0 = interp1(t, t0, t1, v00, v01)
    v1 = interp1(t, t0, t1, v10, v11)
    return max(1.0, interp1(pa_c, p0, p1, v0, v1) * ROC_FACTOR)

def vy_interp(pa):
    pas = sorted(VY.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])
    return interp1(pa_c, p0, p1, VY[p0], VY[p1])

# ====== STATE ======
def ens(k, v): return st.session_state.setdefault(k, v)

ens("mag_var", 1)
ens("mag_is_e", False)
ens("qnh", 1013)
ens("oat", 15)
ens("weight", 650.0)
ens("rpm_climb", 2250)
ens("rpm_cruise", 2100)  # por defeito
ens("rpm_desc", 1800)
ens("desc_angle", 3.0)
ens("start_clock", "")
ens("start_efob", 85.0)
ens("legs", [])
ens("computed", [])

# ====== STYLE ======
CSS = """
<style>
.card{border:1px solid #e7e7e9;border-radius:14px;padding:14px 16px;margin-bottom:12px;background:#fff;
      box-shadow:0 1px 2px rgba(0,0,0,0.04)}
.hrow{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 10px 0}
.kpi{background:#fafafa;border:1px solid #eee;border-radius:10px;padding:8px 10px;min-width:120px}
.tl{position:relative;margin:8px 0 10px 0}
.tl .bar{height:6px;background:#eef1f5;border-radius:3px}
.tl .tick{position:absolute;top:10px;width:2px;height:14px;background:#333}
.tl .cp-lbl{position:absolute;top:28px;transform:translateX(-50%);text-align:center;font-size:11px;color:#333;white-space:nowrap}
.tl .tocdot,.tl .toddot{position:absolute;top:-6px;width:14px;height:14px;border-radius:50%;transform:translateX(-50%);
                         border:2px solid #fff;box-shadow:0 0 0 2px rgba(0,0,0,0.15)}
.tl .tocdot{background:#1f77b4}
.tl .toddot{background:#d62728}
.tl .head{display:flex;justify-content:space-between;font-size:12px;color:#555;margin-bottom:6px}
.badge{display:inline-block;background:#eef1f5;border-radius:999px;padding:2px 8px;font-size:11px;margin-left:6px}
.leg-head{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.leg-title{font-weight:600;font-size:1.05rem}
.sep{height:1px;background:#eee;margin:8px 0}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ====== TIMELINE ======
def timeline(seg, cps, start_label, end_label, toc_tod=None):
    total = max(1, int(seg['time']))
    html = f"<div class='tl'><div class='head'><div>{start_label}</div>" \
           f"<div>GS {rint(seg['GS'])} kt · TAS {rint(seg['TAS'])} kt · FF {rint(seg['ff'])} L/h</div>" \
           f"<div>{end_label}</div></div><div class='bar'></div>"
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

# ====== CÁLCULO DE UMA LEG ======
def build_segments(tc, dist, alt0, alt1, wfrom, wkt, ck_min, params):
    qnh, oat, mag_var, mag_is_e = params['qnh'], params['oat'], params['mag_var'], params['mag_is_e']
    rpm_climb, rpm_cruise, rpm_desc, desc_angle, weight = params['rpm_climb'], params['rpm_cruise'], params['rpm_desc'], params['desc_angle'], params['weight']

    pa0 = press_alt(alt0, qnh); pa1 = press_alt(alt1, qnh); pa_avg = (pa0 + pa1)/2.0
    Vy  = vy_interp(pa0)
    ROC = roc_interp(pa0, oat)  # ft/min
    TAS_climb = Vy
    FF_climb  = cruise_lookup(alt0 + 0.5*max(0.0, alt1-alt0), int(rpm_climb), oat, weight)[1]
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
    for s in segments:
        s["burn"] = s["ff"] * (s["time"] / 3600.0)

    tot_sec = sum(s['time'] for s in segments)
    tot_burn = r10f(sum(s['burn'] for s in segments))

    def cps(seg, every_min, base_clk, efob_start):
        out = []; t = 0
        while t + every_min*60 <= seg['time']:
            t += every_min*60
            d = seg['GS']*(t/3600.0)
            burn = seg['ff']*(t/3600.0)
            eto = (base_clk + dt.timedelta(seconds=t)).strftime('%H:%M') if base_clk else ""
            efob = max(0.0, r10f(efob_start - burn))
            out.append({"t":t, "min":int(t/60), "nm":round(d,1), "eto":eto, "efob":efob})
        return out

    return {
        "segments": segments,
        "tot_sec": tot_sec,
        "tot_burn": tot_burn,
        "roc": ROC,
        "rod": ROD,
        "toc_tod": toc_tod_marker,
        "ck_func": cps
    }

# ====== RECOMPUTE (com acumulados) ======
def recompute_all():
    st.session_state.computed = []
    params = dict(
        qnh=st.session_state.qnh, oat=st.session_state.oat, mag_var=st.session_state.mag_var,
        mag_is_e=st.session_state.mag_is_e, rpm_climb=st.session_state.rpm_climb,
        rpm_cruise=st.session_state.rpm_cruise, rpm_desc=st.session_state.rpm_desc,
        desc_angle=st.session_state.desc_angle, weight=st.session_state.weight
    )

    base_time = None
    if st.session_state.start_clock.strip():
        try:
            h, m = map(int, st.session_state.start_clock.split(":"))
            base_time = dt.datetime.combine(dt.date.today(), dt.time(h, m))
        except:
            base_time = None

    carry_efob = float(st.session_state.start_efob)
    clock = base_time
    cum_sec = 0
    cum_burn = 0.0

    for idx, leg in enumerate(st.session_state.legs):
        res = build_segments(
            tc=leg['TC'], dist=leg['Dist'], alt0=leg['Alt0'], alt1=leg['Alt1'],
            wfrom=leg['Wfrom'], wkt=leg['Wkt'], ck_min=leg['CK'], params=params
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
            segs[0]["clock_end"] = (base1 + dt.timedelta(seconds=segs[0]['time'])).strftime('%H:%M')
        else:
            segs[0]["clock_start"] = 'T+0'
            segs[0]["clock_end"] = mmss(segs[0]['time'])
        if len(segs) > 1:
            base2 = (base1 + dt.timedelta(seconds=segs[0]['time'])) if base1 else None
            if base2:
                segs[1]["clock_start"] = base2.strftime('%H:%M')
                segs[1]["clock_end"] = (base2 + dt.timedelta(seconds=segs[1]['time'])).strftime('%H:%M')
            else:
                segs[1]["clock_start"] = 'T+0'
                segs[1]["clock_end"] = mmss(segs[1]['time'])

        # checkpoints
        cpA = res["ck_func"](segs[0], int(leg['CK']), base1, EF0)
        cpB = res["ck_func"](segs[1], int(leg['CK']),
                             (base1 + dt.timedelta(seconds=segs[0]['time'])) if base1 and len(segs)>1 else None,
                             max(0.0, r10f(EF0 - segs[0]['burn'])) if len(segs)>1 else None) if len(segs)>1 else []

        # atualizar carries e acumulados
        clock = (clock + dt.timedelta(seconds=res['tot_sec'])) if clock else None
        carry_efob = max(0.0, r10f(carry_efob - sum(s['burn'] for s in segs)))
        carry_alt = segs[-1]['alt1']
        cum_sec += res['tot_sec']
        cum_burn = r10f(cum_burn + res['tot_burn'])

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
            "cum_burn": cum_burn
        })

# ====== CRUD ======
def add_leg(prefill=None):
    d = dict(TC=90.0, Dist=10.0, Alt0=0.0, Alt1=4000.0, Wfrom=180, Wkt=15, CK=2)
    if prefill: d.update(prefill)
    st.session_state.legs.append(d)
    recompute_all()

def update_leg(i, vals):
    st.session_state.legs[i].update(vals)
    recompute_all()

def delete_leg(i):
    st.session_state.legs.pop(i)
    recompute_all()

# ====== HEADER GLOBAL ======
st.title("NAVLOG — v9 (AFM) • Fluxo Limpo")
with st.form("hdr"):
    c1, c2, c3, c4 = st.columns(4)
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
        st.session_state.desc_angle = st.number_input("Ângulo desc (°)", 1.0, 6.0, float(st.session_state.desc_angle), step=0.1)
    st.session_state.start_efob = st.number_input("EFOB inicial (L)", 0.0, 200.0, float(st.session_state.start_efob), step=0.5)
    st.form_submit_button("Aplicar parâmetros")

# ====== AÇÕES ======
act1, act2 = st.columns([1,3])
with act1:
    if st.button("➕ Nova leg", type="primary", use_container_width=True):
        if st.session_state.computed:
            pref = dict(Alt0=r10f(st.session_state.computed[-1]["carry_alt_after"]),
                        Alt1=r10f(st.session_state.computed[-1]["carry_alt_after"]))
        elif st.session_state.legs:
            pref = dict(Alt0=st.session_state.legs[-1]['Alt1'], Alt1=st.session_state.legs[-1]['Alt1'])
        else:
            pref = None
        add_leg(prefill=pref)
with act2:
    st.caption("Fluxo: define parâmetros globais → cria legs → edita nos cartões. Cada edição recalcula e **propaga** para as seguintes.")

st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

# ====== CONTEÚDO ======
if not st.session_state.legs:
    st.info("Sem legs ainda. Clica **Nova leg** para começar.")
else:
    # Garantir cálculo
    recompute_all()

    # Resumo global (sempre visível no topo)
    total_time = sum(c["tot_sec"] for c in st.session_state.computed)
    total_burn = r10f(sum(c["tot_burn"] for c in st.session_state.computed))
    efob_final = st.session_state.computed[-1]['carry_efob_after']
    s1, s2, s3 = st.columns(3)
    with s1: st.metric("ETE total", hhmmss(total_time))
    with s2: st.metric("Burn total (L)", f"{total_burn:.1f}")
    with s3: st.metric("EFOB final (L)", f"{efob_final:.1f}")

    st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

    # Lista de legs (caixinhas)
    for i, leg in enumerate(st.session_state.legs):
        comp = st.session_state.computed[i]
        segA = comp['segments'][0]
        dist_total_leg = sum(s['dist'] for s in comp['segments'])

        # ==== CARTÃO ====
        st.markdown("<div class='card'>", unsafe_allow_html=True)

        # Header compacto da leg
        hc1, hc2, hc3, hc4, hc5, hc6 = st.columns([3,2,2,2,2,2])
        with hc1:
            st.markdown(f"<div class='leg-head'><span class='leg-title'>Leg {i+1}</span>"
                        f"<span class='badge'>{segA['name'].split('→')[0]}</span></div>", unsafe_allow_html=True)
        with hc2: st.metric("ETE", hhmmss(comp["tot_sec"]))
        with hc3: st.metric("Burn (L)", f"{comp['tot_burn']:.1f}")
        with hc4: st.metric("ROC (ft/min)", rint(comp["roc"]))
        with hc5: st.metric("ROD (ft/min)", rint(comp["rod"]))
        with hc6: st.metric("Dist (nm)", f"{dist_total_leg:.1f}")

        # Detalhes & edição num expander (fluxo mais limpo)
        with st.expander("Detalhes e edição desta leg", expanded=False):
            # Inputs
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                TC = st.number_input(f"True Course (°T) — L{i+1}", 0.0, 359.9, float(leg['TC']), step=0.1, key=f"TC_{i}")
                Dist = st.number_input(f"Distância (nm) — L{i+1}", 0.0, 500.0, float(leg['Dist']), step=0.1, key=f"Dist_{i}")
            with c2:
                Alt0 = st.number_input(f"Alt início (ft) — L{i+1}", 0.0, 30000.0, float(leg['Alt0']), step=50.0, key=f"Alt0_{i}")
                Alt1 = st.number_input(f"Alt alvo (ft) — L{i+1}", 0.0, 30000.0, float(leg['Alt1']), step=50.0, key=f"Alt1_{i}")
            with c3:
                Wfrom = st.number_input(f"Vento FROM (°T) — L{i+1}", 0, 360, int(leg['Wfrom']), step=1, key=f"Wfrom_{i}")
                Wkt   = st.number_input(f"Vento (kt) — L{i+1}", 0, 150, int(leg['Wkt']), step=1, key=f"Wkt_{i}")
            with c4:
                CK = st.number_input(f"Checkpoints (min) — L{i+1}", 1, 10, int(leg['CK']), step=1, key=f"CK_{i}")

            b1, b2, b3 = st.columns([1,1,6])
            with b1:
                if st.button("Atualizar", key=f"upd_{i}", use_container_width=True):
                    update_leg(i, dict(TC=TC, Dist=Dist, Alt0=Alt0, Alt1=Alt1, Wfrom=Wfrom, Wkt=Wkt, CK=CK))
            with b2:
                if st.button("Apagar", key=f"del_{i}", use_container_width=True):
                    delete_leg(i)
                    st.stop()

            st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

            # Segmento 1
            st.markdown(f"**Segmento 1 — {segA['name']}**")
            s1a, s1b, s1c, s1d = st.columns(4)
            s1a.metric("Alt ini→fim (ft)", f"{int(round(segA['alt0']))} → {int(round(segA['alt1']))}")
            s1b.metric("TH/MH (°)", f"{rang(segA['TH'])}T / {rang(segA['MH'])}M")
            s1c.metric("GS/TAS (kt)", f"{rint(segA['GS'])} / {rint(segA['TAS'])}")
            s1d.metric("FF (L/h)", f"{rint(segA['ff'])}")
            s1e, s1f, s1g = st.columns(3)
            s1e.metric("Tempo", mmss(segA['time']))
            s1f.metric("Dist (nm)", f"{segA['dist']:.1f}")
            s1g.metric("Burn (L)", f"{r10f(segA['burn']):.1f}")

            st.caption("Checkpoints do Segmento 1")
            start_lbl = segA.get("clock_start", "T+0"); end_lbl = segA.get("clock_end", mmss(segA['time']))
            timeline(segA, comp["cpA"], start_lbl, end_lbl,
                     toc_tod=comp["toc_tod"] if comp["toc_tod"] and comp["segments"].index(segA)==0 else None)

            # Segmento 2 (se existir)
            if len(comp['segments']) > 1:
                segB = comp['segments'][1]
                st.info(("TOC" if comp["toc_tod"] and comp["toc_tod"]["type"]=="TOC" else "TOD") +
                        f" — {mmss(comp['segments'][0]['time'])} • {comp['segments'][0]['dist']:.1f} nm desde o início")
                st.markdown(f"**Segmento 2 — {segB['name']}**")
                s2a, s2b, s2c, s2d = st.columns(4)
                s2a.metric("Alt ini→fim (ft)", f"{int(round(segB['alt0']))} → {int(round(segB['alt1']))}")
                s2b.metric("TH/MH (°)", f"{rang(segB['TH'])}T / {rang(segB['MH'])}M")
                s2c.metric("GS/TAS (kt)", f"{rint(segB['GS'])} / {rint(segB['TAS'])}")
                s2d.metric("FF (L/h)", f"{rint(segB['ff'])}")
                s2e, s2f, s2g = st.columns(3)
                s2e.metric("Tempo", mmss(segB['time']))
                s2f.metric("Dist (nm)", f"{segB['dist']:.1f}")
                s2g.metric("Burn (L)", f"{r10f(segB['burn']):.1f}")

                st.caption("Checkpoints do Segmento 2")
                start_lbl2 = segB.get("clock_start", "T+0"); end_lbl2 = segB.get("clock_end", mmss(segB['time']))
                timeline(segB, comp["cpB"], start_lbl2, end_lbl2,
                         toc_tod=comp["toc_tod"] if comp["toc_tod"] and comp["segments"].index(segB)==1 else None)

        # Rodapé do cartão: totais e ACUMULADOS (pedido)
        st.markdown("<div class='sep'></div>", unsafe_allow_html=True)
        colA, colB, colC = st.columns(3)
        with colA:
            st.markdown(f"**Totais da leg** — ETE {hhmmss(comp['tot_sec'])} • Burn {comp['tot_burn']:.1f} L")
        with colB:
            EF_START = comp['segments'][0].get("EFOB_start", None)
            if EF_START is not None:
                st.markdown(f"**EFOB** — Start {EF_START:.1f} L → End {comp['carry_efob_after']:.1f} L")
        with colC:
            st.markdown(f"**Acumulado até Leg {i+1}** — Tempo {hhmmss(comp['cum_sec'])} • Fuel {comp['cum_burn']:.1f} L")

        st.markdown("</div>", unsafe_allow_html=True)  # end card


