# app.py — NAVLOG v9 (AFM-only + checkpoints por segmento)
# Streamlit app compacta

import streamlit as st
import datetime as dt
import math
from math import sin, asin, radians, degrees

st.set_page_config(page_title="NAVLOG v9 (AFM)", layout="wide", initial_sidebar_state="collapsed")

# ---------- helpers ----------

def r10s(sec: float) -> int:
    if sec <= 0: return 0
    s = int(round(sec/10.0)*10)
    return max(s, 10)

def mmss(t: int) -> str:
    m = t // 60; s = t % 60
    return f"{m:02d}:{s:02d}"

def hhmmss(t: int) -> str:
    h = t // 3600; m = (t % 3600)//60; s = t % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def r_ang(x): return int(round(float(x))) % 360

def r_unit(x): return int(round(float(x)))

def r_1(x): return round(float(x),1)

def wrap360(x):
    x = math.fmod(float(x), 360.0)
    return x + 360.0 if x < 0 else x

def ang_diff(a,b): return (a-b+180)%360-180

def mag_heading(th, var_deg, east_is_neg=False):
    return wrap360(th - var_deg if east_is_neg else th + var_deg)

def wind_triangle(tc_deg, tas_kt, wind_from_deg, wind_kt):
    if tas_kt <= 0: return 0.0, wrap360(tc_deg), 0.0
    delta = radians(ang_diff(wind_from_deg, tc_deg))
    cross = wind_kt * sin(delta)
    s = max(-1.0, min(1.0, cross/max(tas_kt,1e-9)))
    wca = degrees(asin(s))
    th  = wrap360(tc_deg + wca)
    gs  = max(0.0, tas_kt*math.cos(radians(wca)) - wind_kt*math.cos(delta))
    return wca, th, gs

# ---------- AFM tables (resumo P2008 @650kg) ----------
ROC_ENROUTE = {
    0:{-25:981,0:835,25:704,50:586},  2000:{-25:870,0:726,25:597,50:481},
    4000:{-25:759,0:617,25:491,50:377},6000:{-25:648,0:509,25:385,50:273},
    8000:{-25:538,0:401,25:279,50:170},10000:{-25:428,0:294,25:174,50:66},
    12000:{-25:319,0:187,25:69,50:-37},14000:{-25:210,0:80,25:-35,50:-139},
}
ROC_FACTOR = 0.90
VY_ENROUTE = {0:67,2000:67,4000:67,6000:67,8000:67,10000:67,12000:67,14000:67}
CRUISE={
    0:{1800:(82,15.3),1900:(89,17.0),2000:(95,18.7),2100:(101,20.7),2250:(110,24.6),2388:(118,27.7)},
    2000:{1800:(81,15.5),1900:(87,17.0),2000:(93,18.8),2100:(99,20.9),2250:(108,25.0)},
    4000:{1800:(79,15.2),1900:(86,16.5),2000:(92,18.1),2100:(98,19.2),2250:(106,23.9)},
    6000:{1800:(78,14.9),1900:(85,16.1),2000:(91,17.5),2100:(97,19.2),2250:(105,22.7)},
    8000:{1800:(78,14.9),1900:(84,15.7),2000:(90,17.0),2100:(96,18.5),2250:(104,21.5)},
    10000:{1800:(78,15.5),1900:(82,15.5),2000:(89,16.6),2100:(95,17.9),2250:(103,20.5)},
}

def isa_temp(pa_ft): return 15.0 - 2.0*(pa_ft/1000.0)

def pressure_alt(alt_ft, qnh): return float(alt_ft) + (1013.0 - float(qnh))*30.0

def clamp(v,lo,hi): return max(lo,min(hi,v))

def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

def cruise_lookup(pa_ft, rpm, oat_c, weight_kg):
    rpm = min(int(rpm), 2265)
    pas=sorted(CRUISE.keys()); pa_c=clamp(pa_ft,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    def val(pa):
        table=CRUISE[pa]
        rpms=sorted(table.keys())
        if rpm in table: return table[rpm]
        if rpm<rpms[0]: lo,hi=rpms[0],rpms[1]
        elif rpm>rpms[-1]: lo,hi=rpms[-2],rpms[-1]
        else:
            lo=max([r for r in rpms if r<=rpm]); hi=min([r for r in rpms if r>=rpm])
        (t_lo,f_lo),(t_hi,f_hi)=table[lo],table[hi]
        t=(rpm-lo)/(hi-lo) if hi!=lo else 0.0
        return (t_lo + t*(t_hi-t_lo), f_lo + t*(f_hi-f_lo))
    tas0,ff0=val(p0); tas1,ff1=val(p1)
    tas=interp1(pa_c,p0,p1,tas0,tas1); ff=interp1(pa_c,p0,p1,ff0,ff1)
    if oat_c is not None:
        dev=oat_c - isa_temp(pa_c)
        if dev>0: tas*=1-0.02*(dev/15.0); ff*=1-0.025*(dev/15.0)
        elif dev<0: tas*=1+0.01*((-dev)/15.0); ff*=1+0.03*((-dev)/15.0)
    tas *= (1.0 + 0.033*((650.0-float(weight_kg))/100.0))
    return max(0.0,tas), max(0.0,ff)

def roc_interp(pa,temp_c):
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

def vy_interp(pa):
    pas=sorted(VY_ENROUTE.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    return interp1(pa_c, p0, p1, VY_ENROUTE[p0], VY_ENROUTE[p1])

# ---------- state ----------

def ensure(k,v):
    if k not in st.session_state: st.session_state[k]=v

ensure("qnh",1013); ensure("oat",15); ensure("mag_var",1); ensure("mag_is_e",False)
ensure("weight_kg",650.0)
ensure("rpm_climb",2250); ensure("rpm_cruise",2100); ensure("rpm_descent",1800)
ensure("descent_angle",3.0)
ensure("start_clock","")
ensure("carry_alt",0.0); ensure("carry_efob",85.0)
ensure("legs", [])  # histórico

# ---------- header ----------
st.title("NAVLOG — Performance v9 (AFM)")
with st.form("hdr"):
    c1,c2,c3,c4 = st.columns(4)
    with c1:
        st.session_state.qnh = st.number_input("QNH (hPa)", 900,1050, int(st.session_state.qnh))
        st.session_state.oat = st.number_input("OAT (°C)", -40,50, int(st.session_state.oat))
    with c2:
        st.session_state.mag_var = st.number_input("Mag Var (°)",0,30,int(st.session_state.mag_var))
        st.session_state.mag_is_e = st.selectbox("Variação E/W", ["W","E"], index=(1 if st.session_state.mag_is_e else 0))=="E"
    with c3:
        st.session_state.weight_kg = st.number_input("Peso (kg)", 450.0, 700.0, float(st.session_state.weight_kg), step=1.0)
        st.session_state.start_clock = st.text_input("Hora off-blocks (HH:MM)", st.session_state.start_clock)
    with c4:
        st.session_state.rpm_climb = st.number_input("Climb RPM",1800,2265,int(st.session_state.rpm_climb),step=5)
        st.session_state.rpm_cruise= st.number_input("Cruise RPM",1800,2265,int(st.session_state.rpm_cruise),step=5)
        st.session_state.rpm_descent=st.number_input("Descent RPM",1600,2265,int(st.session_state.rpm_descent),step=5)
        st.session_state.descent_angle = st.number_input("Ângulo de descida (°)",1.0,6.0,float(st.session_state.descent_angle),step=0.1)
    st.form_submit_button("Aplicar")

# ---------- nova perna ----------
st.subheader("Perna — entrada manual")
a1,a2,a3,a4 = st.columns(4)
with a1:
    TC   = st.number_input("True Course (°T)",0.0,359.9,90.0,step=0.1)
    Dist = st.number_input("Distância (nm)",0.0,500.0,10.0,step=0.1)
with a2:
    Alt0 = st.number_input("Alt início (ft)",0.0,30000.0,float(st.session_state.carry_alt),step=50.0)
    Alt1 = st.number_input("Alt alvo (ft)",0.0,30000.0,4000.0,step=50.0)
with a3:
    W_from = st.number_input("Vento FROM (°T)",0,360,180,step=1)
    W_kt   = st.number_input("Vento (kt)",0,150,15,step=1)
with a4:
    CK = st.number_input("Checkpoints a cada (min)",1,10,2,step=1)

# ---------- cálculo ----------
pa0 = pressure_alt(Alt0, st.session_state.qnh)
pa1 = pressure_alt(Alt1, st.session_state.qnh)
pa_avg=(pa0+pa1)/2
Vy  = vy_interp(pa0)
ROC = roc_interp(pa0, st.session_state.oat)
TAS_climb = Vy
FF_climb = cruise_lookup(Alt0 + 0.5*max(0.0, Alt1-Alt0), int(st.session_state.rpm_climb), st.session_state.oat, st.session_state.weight_kg)[1]
TAS_cruise, FF_cruise = cruise_lookup(pa1, int(st.session_state.rpm_cruise), st.session_state.oat, st.session_state.weight_kg)
TAS_desc,   FF_desc   = cruise_lookup(pa_avg, int(st.session_state.rpm_descent), st.session_state.oat, st.session_state.weight_kg)
_, THc, GSc = wind_triangle(TC, TAS_climb,  W_from, W_kt)
_, THr, GSr = wind_triangle(TC, TAS_cruise, W_from, W_kt)
_, THd, GSd = wind_triangle(TC, TAS_desc,   W_from, W_kt)
MHc=mag_heading(THc, st.session_state.mag_var, st.session_state.mag_is_e)
MHr=mag_heading(THr, st.session_state.mag_var, st.session_state.mag_is_e)
MHd=mag_heading(THd, st.session_state.mag_var, st.session_state.mag_is_e)
ROD = max(100.0, GSd * 5.0 * (st.session_state.descent_angle/3.0))

profile = "LEVEL" if abs(Alt1-Alt0) < 1e-6 else ("CLIMB" if Alt1>Alt0 else "DESCENT")
segA={}; segB=None; END_ALT=Alt1
if profile=="CLIMB":
    t_need=(Alt1-Alt0)/max(ROC,1e-6)  # min
    d_need=GSc*(t_need/60.0)
    if d_need<=Dist:
        tA=r10s(t_need*60)
        segA={"name":"Climb → TOC","TH":THc,"MH":MHc,"GS":GSc,"TAS":TAS_climb,"ff":FF_climb,
              "time":tA,"dist":d_need,"alt0":Alt0,"alt1":Alt1,"burn":FF_climb*(tA/3600.0)}
        rem=Dist-d_need
        if rem>0:
            tB=r10s((rem/max(GSr,1e-6))*3600.0)
            segB={"name":"Cruise (após TOC)","TH":THr,"MH":MHr,"GS":GSr,"TAS":TAS_cruise,"ff":FF_cruise,
                  "time":tB,"dist":rem,"alt0":Alt1,"alt1":Alt1,"burn":FF_cruise*(tB/3600.0)}
    else:
        tA=r10s((Dist/max(GSc,1e-6))*3600.0)
        gain=ROC*(tA/60.0)
        END_ALT=Alt0+gain
        segA={"name":"Climb (não atinge)","TH":THc,"MH":MHc,"GS":GSc,"TAS":TAS_climb,"ff":FF_climb,
              "time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT,"burn":FF_climb*(tA/3600.0)}
elif profile=="DESCENT":
    t_need=(Alt0-Alt1)/max(ROD,1e-6)
    d_need=GSd*(t_need/60.0)
    if d_need<=Dist:
        tA=r10s(t_need*60)
        segA={"name":"Descent → TOD","TH":THd,"MH":MHd,"GS":GSd,"TAS":TAS_desc,"ff":FF_desc,
              "time":tA,"dist":d_need,"alt0":Alt0,"alt1":Alt1,"burn":FF_desc*(tA/3600.0)}
        rem=Dist-d_need
        if rem>0:
            tB=r10s((rem/max(GSr,1e-6))*3600.0)
            segB={"name":"Cruise (após TOD)","TH":THr,"MH":MHr,"GS":GSr,"TAS":TAS_cruise,"ff":FF_cruise,
                  "time":tB,"dist":rem,"alt0":Alt1,"alt1":Alt1,"burn":FF_cruise*(tB/3600.0)}
    else:
        tA=r10s((Dist/max(GSd,1e-6))*3600.0)
        lost=ROD*(tA/60.0)
        END_ALT=max(0.0, Alt0-lost)
        segA={"name":"Descent (não atinge)","TH":THd,"MH":MHd,"GS":GSd,"TAS":TAS_desc,"ff":FF_desc,
              "time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT,"burn":FF_desc*(tA/3600.0)}
else:
    tA=r10s((Dist/max(GSr,1e-6))*3600.0)
    END_ALT=Alt0
    segA={"name":"Level","TH":THr,"MH":MHr,"GS":GSr,"TAS":TAS_cruise,"ff":FF_cruise,
          "time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT,"burn":FF_cruise*(tA/3600.0)}

segs=[segA]+([segB] if segB else [])
TOT_SEC=sum(int(s['time']) for s in segs)
TOT_BURN=r_1(sum(float(s['burn']) for s in segs))

# ---------- checkpoints helpers ----------

def build_cps(seg, every_min, base_clock, efob_start):
    rows=[]; t=0
    while t + every_min*60 <= seg['time']:
        t += every_min*60
        d = seg['GS']*(t/3600.0)
        burn = seg['ff']*(t/3600.0)
        eto = (base_clock + dt.timedelta(seconds=t)).strftime('%H:%M') if base_clock else ""
        efob = max(0.0, r_1(efob_start - burn))
        rows.append({"t":t, "min":int(t/60), "nm":round(d,1), "eto":eto, "efob":efob})
    return rows

CSS = """
<style>
.tl{position:relative;margin:6px 0 10px 0;padding-top:18px}
.tl .bar{height:3px;background:#e6e6e6;border-radius:2px}
.tl .tick{position:absolute;top:8px;width:2px;height:12px;background:#333}
.tl .lbl{position:absolute;top:22px;transform:translateX(-50%);text-align:center;font-size:11px;color:#333}
.tl .head{display:flex;justify-content:space-between;font-size:12px;color:#666;margin-bottom:4px}
</style>
"""

def timeline(seg, cps, start_label, end_label):
    total = max(1, int(seg.get('time') or seg.get('time_sec')))
    ticks = []
    for cp in cps:
        t = cp.get('t') or cp.get('t_sec')
        pct = (t / total) * 100.0
        ticks.append(f"<div class='tick' style='left:{pct:.2f}%;'></div>")
        lbl = (
            f"<div class='lbl' style='left:{pct:.2f}%;'>"
            f"<div>T+{cp.get('min', cp.get('t_min'))}m</div><div>{cp.get('nm')} nm</div>"
            + (f"<div>{cp.get('eto')}</div>" if cp.get('eto') else "")
            + f"<div>EFOB {cp.get('efob'):.1f}</div></div>"
        )
        ticks.append(lbl)
    css = """
    <style>
      .tl{position:relative;margin:6px 0 10px 0;padding-top:18px}
      .tl .bar{height:3px;background:#e6e6e6;border-radius:2px}
      .tl .tick{position:absolute;top:8px;width:2px;height:12px;background:#333}
      .tl .lbl{position:absolute;top:22px;transform:translateX(-50%);text-align:center;font-size:11px;color:#333}
      .tl .head{display:flex;justify-content:space-between;font-size:12px;color:#666;margin-bottom:4px}
    </style>
    """
    html = (
        css
        + f"""
    <div class='tl'>
      <div class='head'><div>{start_label}</div><div>GS {int(round(seg.get('GS')))} kt · TAS {int(round(seg.get('TAS')))} kt · FF {int(round(seg.get('ff')))} L/h</div><div>{end_label}</div></div>
      <div class='bar'></div>
      {''.join(ticks)}
    </div>
    """
    )
    import streamlit.components.v1 as components
    components.html(html, height=120, scrolling=False)

# ---------- clocks ----------
clock=None
if st.session_state.start_clock.strip():
    try:
        h,m = map(int, st.session_state.start_clock.split(":"))
        clock = dt.datetime.combine(dt.date.today(), dt.time(h,m))
    except Exception: pass

# ---------- apresentação ----------
st.markdown("---")
st.subheader("Resultados da Perna")

st.markdown(f"### Segmento 1 — {segA['name']}")
c1,c2,c3,c4,c5 = st.columns(5)
c1.metric("Alt ini→fim (ft)", f"{int(round(segA['alt0']))} → {int(round(segA['alt1']))}")
c2.metric("TH/MH (°)", f"{r_ang(segA['TH'])}T / { r_ang(segA['MH']) }M")
c3.metric("GS/TAS (kt)", f"{r_unit(segA['GS'])} / {r_unit(segA['TAS'])}")
c4.metric("FF (L/h)", f"{r_unit(segA['ff'])}")
if profile=="CLIMB":
    c5.metric("ROC @ início (ft/min)", r_unit(ROC))
else:
    c5.metric("ROD calc (ft/min)", r_unit(ROD))

b1,b2,b3 = st.columns(3)
b1.metric("Tempo", mmss(int(segA['time'])))
b2.metric("Dist (nm)", f"{segA['dist']:.1f}")
b3.metric("Burn (L)", f"{r_1(segA['burn']):.1f}")

EF0=float(st.session_state.carry_efob)
base1 = clock
cpA = build_cps(segA, int(CK), base1, EF0)
start_lbl = base1.strftime('%H:%M') if base1 else 'T+0'
end_lbl = (base1 + dt.timedelta(seconds=int(segA['time']))).strftime('%H:%M') if base1 else mmss(int(segA['time']))
st.caption("Checkpoints do segmento (T+ reinicia)")
# mostrar ETOs (início/fim do segmento)
st.metric("ETO início/fim", f"{start_lbl} → {end_lbl}")
timeline(segA, cpA, start_lbl, end_lbl)

if segB:
    # TOC/TOD destacado com horas reais
if clock:
    toc_time = (base1 + dt.timedelta(seconds=int(segA['time']))).strftime('%H:%M')
else:
    toc_time = mmss(int(segA['time']))
st.info(("TOC" if profile=="CLIMB" else "TOD") + f" — {toc_time} • {segA['dist']:.1f} nm")
    st.markdown(f"### Segmento 2 — {segB['name']}")
    d1,d2,d3,d4 = st.columns(4)
    d1.metric("Alt ini→fim (ft)", f"{int(round(segB['alt0']))} → {int(round(segB['alt1']))}")
    d2.metric("TH/MH (°)", f"{r_ang(segB['TH'])}T / {r_ang(segB['MH'])}M")
    d3.metric("GS/TAS (kt)", f"{r_unit(segB['GS'])} / {r_unit(segB['TAS'])}")
    d4.metric("FF (L/h)", f"{r_unit(segB['ff'])}")
    e1,e2,e3 = st.columns(3)
    e1.metric("Tempo", mmss(int(segB['time'])))
    e2.metric("Dist (nm)", f"{segB['dist']:.1f}")
    e3.metric("Burn (L)", f"{r_1(segB['burn']):.1f}")
    EF1=max(0.0, r_1(EF0 - segA['burn']))
    base2 = (base1 + dt.timedelta(seconds=int(segA['time']))) if base1 else None
    cpB = build_cps(segB, int(CK), base2, EF1)
    start_lbl2 = base2.strftime('%H:%M') if base2 else 'T+0'
    end_lbl2 = (base2 + dt.timedelta(seconds=int(segB['time']))).strftime('%H:%M') if base2 else mmss(int(segB['time']))
    st.caption("Checkpoints do segmento 2")
    timeline(segB, cpB, start_lbl2, end_lbl2)

st.markdown("---")
TOT_SEC = sum(int(s['time']) for s in segs)
TOT_BURN = r_1(sum(float(s['burn']) for s in segs))
st.markdown(f"**Totais** — ETE {hhmmss(TOT_SEC)} • Burn {TOT_BURN:.1f} L")
EF_END = max(0.0, r_1(float(st.session_state.carry_efob) - sum(s['burn'] for s in segs)))
st.markdown(f"**EFOB** — Start {float(st.session_state.carry_efob):.1f} L → End {EF_END:.1f} L")
st.markdown(f"**Altitude final para próxima perna:** {int(round(segA['alt1'] if not segB else segB['alt1']))} ft")

# ---------- construir próxima perna ----------
if st.button("➕ Construir próxima perna (guardar esta)", type="primary"):
    st.session_state.legs.append({
        "TC": TC, "Dist": Dist, "Alt0": Alt0, "Alt1": Alt1, "wind": f"{W_from:03d}/{W_kt:02d}",
        "segments": segs, "tot_sec": TOT_SEC, "tot_burn": TOT_BURN,
    })
    st.session_state.carry_alt = (segA['alt1'] if not segB else segB['alt1'])
    st.session_state.carry_efob = EF_END
    st.toast("Perna adicionada. Próxima perna pronta.")

# ---------- histórico ----------
st.subheader("Histórico de Pernas")
if st.session_state.legs:
    for idx, L in enumerate(st.session_state.legs, start=1):
        st.markdown(f"**Perna {idx}** — TC {L['TC']:.1f}°T · Dist {L['Dist']:.1f} nm · Vento {L['wind']} · ETE {mmss(L['tot_sec'])} · Burn {L['tot_burn']:.1f} L")
        for j, s in enumerate(L['segments'], start=1):
            st.caption(f"Seg {j}: {s['name']} — TH/MH {r_ang(s['TH'])}T/{r_ang(s['MH'])}M · GS/TAS {r_unit(s['GS'])}/{r_unit(s['TAS'])} kt · {mmss(int(s['time']))}, {s['dist']:.1f} nm, {r_1(s['burn']):.1f} L")
else:
    st.caption("Sem histórico ainda. Guarda a primeira perna para começar.")



