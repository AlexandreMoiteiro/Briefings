# app.py — NAVLOG v10 (AFM-only)
# Streamlit app para planeamento por pernas com TOC/TOD como novo FIX.
# TAS/FF (climb/cruise/descent) por tabelas; ROD da descida via ângulo.
# UI reorganizada com cartões por perna, controlo de CRUD, e recálculo em cascata.

import streamlit as st, datetime as dt, math
from math import sin, asin, radians, degrees

st.set_page_config(page_title="NAVLOG v10 (AFM)", layout="wide", initial_sidebar_state="collapsed")

# ===================== Utils =====================
rt10 = lambda s: max(10,int(round(s/10.0)*10)) if s>0 else 0
mmss = lambda t: f"{t//60:02d}:{t%60:02d}"
hhmmss = lambda t: f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
rang = lambda x: int(round(float(x)))%360
rint = lambda x: int(round(float(x)))
r10f = lambda x: round(float(x),1)

def wrap360(x): 
    x=math.fmod(float(x),360.0); 
    return x+360 if x<0 else x

def angdiff(a,b): 
    return (a-b+180)%360-180

def wind_triangle(tc,tas,wdir,wkt):
    if tas<=0: return 0.0, wrap360(tc), 0.0
    d=radians(angdiff(wdir,tc)); cross=wkt*sin(d)
    s=max(-1,min(1,cross/max(tas,1e-9))); wca=degrees(asin(s))
    th=wrap360(tc+wca); gs=max(0.0, tas*math.cos(radians(wca)) - wkt*math.cos(d))
    return wca, th, gs

apply_var = lambda th, var, east_is_neg=False: wrap360(th - var if east_is_neg else th + var)

# ===================== AFM tables (resumo Tecnam P2008) =====================
ROC_ENR={0:{-25:981,0:835,25:704,50:586},2000:{-25:870,0:726,25:597,50:481},4000:{-25:759,0:617,25:491,50:377},6000:{-25:648,0:509,25:385,50:273},8000:{-25:538,0:401,25:279,50:170},10000:{-25:428,0:294,25:174,50:66},12000:{-25:319,0:187,25:69,50:-37},14000:{-25:210,0:80,25:-35,50:-139}}
VY={0:67,2000:67,4000:67,6000:67,8000:67,10000:67,12000:67,14000:67}
ROC_FACTOR=0.90
CRUISE={
  0:{1800:(82,15.3),1900:(89,17.0),2000:(95,18.7),2100:(101,20.7),2250:(110,24.6),2388:(118,27.7)},
  2000:{1800:(81,15.5),1900:(87,17.0),2000:(93,18.8),2100:(99,20.9),2250:(108,25.0)},
  4000:{1800:(79,15.2),1900:(86,16.5),2000:(92,18.1),2100:(98,19.2),2250:(106,23.9)},
  6000:{1800:(78,14.9),1900:(85,16.1),2000:(91,17.5),2100:(97,19.2),2250:(105,22.7)},
  8000:{1800:(78,14.9),1900:(84,15.7),2000:(90,17.0),2100:(96,18.5),2250:(104,21.5)},
  10000:{1800:(78,15.5),1900:(82,15.5),2000:(89,16.6),2100:(95,17.9),2250:(103,20.5)},
}
isa_temp = lambda pa: 15.0 - 2.0*(pa/1000.0)
press_alt = lambda alt,qnh: float(alt)+(1013.0-float(qnh))*30.0
clamp = lambda v,lo,hi: max(lo,min(hi,v))

def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

def cruise_lookup(pa,rpm,oat,weight):
    rpm=min(int(rpm),2265)
    pas=sorted(CRUISE.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c]); table0=CRUISE[p0]; table1=CRUISE[p1]
    def v(tab):
        rpms=sorted(tab.keys())
        if rpm in tab: return tab[rpm]
        if rpm<rpms[0]: lo,hi=rpms[0],rpms[1]
        elif rpm>rpms[-1]: lo,hi=rpms[-2],rpms[-1]
        else:
            lo=max([r for r in rpms if r<=rpm]); hi=min([r for r in rpms if r>=rpm])
        (tas_lo,ff_lo),(tas_hi,ff_hi)=tab[lo],tab[hi]; t=(rpm-lo)/(hi-lo) if hi!=lo else 0
        return (tas_lo+t*(tas_hi-tas_lo), ff_lo+t*(ff_hi-ff_lo))
    tas0,ff0=v(table0); tas1,ff1=v(table1)
    tas=interp1(pa_c,p0,p1,tas0,tas1); ff=interp1(pa_c,p0,p1,ff0,ff1)
    if oat is not None:
        dev=oat-isa_temp(pa_c)
        if dev>0: tas*=1-0.02*(dev/15.0); ff*=1-0.025*(dev/15.0)
        elif dev<0: tas*=1+0.01*((-dev)/15.0); ff*=1+0.03*((-dev)/15.0)
    tas*= (1.0 + 0.033*((650.0-float(weight))/100.0))
    return max(0.0,tas), max(0.0,ff)

def roc_interp(pa,temp):
    pas=sorted(ROC_ENR.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    temps=[-25,0,25,50]; t=clamp(temp,temps[0],temps[-1])
    if t<=0: t0,t1=-25,0
    elif t<=25: t0,t1=0,25
    else: t0,t1=25,50
    v00,v01=ROC_ENR[p0][t0],ROC_ENR[p0][t1]; v10,v11=ROC_ENR[p1][t0],ROC_ENR[p1][t1]
    v0=interp1(t,t0,t1,v00,v01); v1=interp1(pa_c,p0,p1,v10,v11)
    return max(1.0, interp1(pa_c,p0,p1,v0,v1)*ROC_FACTOR)

def vy_interp(pa):
    pas=sorted(VY.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    return interp1(pa_c,p0,p1,VY[p0],VY[p1])

# ===================== Estado / Defaults =====================
def ens(k,v): return st.session_state.setdefault(k,v)
# Globais
ens("mag_var",1); ens("mag_is_e",False); ens("qnh",1013); ens("oat",15); ens("weight",650.0)
ens("rpm_climb",2250); ens("rpm_cruise",2100); ens("rpm_desc",1800); ens("desc_angle",3.0)
ens("start_clock","")
# Modelo de pernas (cada perna guarda inputs; outputs são calculados sempre que muda algo)
ens("legs", [])  # lista de dicts {"id":int, "inputs":{...}}
ens("next_leg_id", 1)

# ===================== Componentes UI =====================
CARD_CSS = """
<style>
.card{border:1px solid #e6e6e6;border-radius:16px;padding:16px;margin:8px 0;background:#fff;box-shadow:0 1px 6px rgba(0,0,0,0.05)}
.card .title{font-weight:600;font-size:17px;margin-bottom:10px}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef4ff;color:#2f5aff;font-size:12px;border:1px solid #d8e3ff}
.banner{margin:6px 0;padding:8px 12px;border-radius:12px;background:#f6faff;border:1px dashed #b8d1ff;color:#1e3a8a;font-size:13px}
.tl{position:relative;margin:10px 0 8px 0;padding:22px 0 0 0}
.tl .bar{height:4px;background:#ededed;border-radius:3px}
.tl .tick{position:absolute;top:10px;width:2px;height:12px;background:#333}
.tl .lbl{position:absolute;top:28px;transform:translateX(-50%);text-align:center;font-size:11px;color:#333;white-space:nowrap}
.tl .head{display:flex;justify-content:space-between;font-size:12px;color:#666;margin:6px 0}
.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.grid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
.kv{background:#fafafa;border:1px solid #eee;border-radius:12px;padding:10px}
.kv h4{margin:0 0 6px 0;font-size:13px;color:#555}
.kv .v{font-size:16px;font-weight:600}
.sep{height:10px}
</style>
"""
st.markdown(CARD_CSS, unsafe_allow_html=True)

def timeline(seg, cps, start_label, end_label):
    total=max(1,int(seg['time'])); bars=[]
    for cp in cps:
        pct=(cp['t']/total)*100.0
        bars.append(f"<div class='tick' style='left:{pct:.2f}%;'></div>")
        lbl = f"<div class='lbl' style='left:{pct:.2f}%;'><div>T+{cp['min']}m • {cp['nm']}nm</div>" + \
              (f"<div>{cp['eto']}</div>" if cp['eto'] else "") + \
              f"<div>EFOB {cp['efob']:.1f}</div></div>"
        bars.append(lbl)
    html=f"<div class='tl'><div class='head'><div>{start_label}</div><div>GS {rint(seg['GS'])} kt · TAS {rint(seg['TAS'])} kt · FF {rint(seg['ff'])} L/h</div><div>{end_label}</div></div><div class='bar'></div>{''.join(bars)}</div>"
    st.markdown(html, unsafe_allow_html=True)

def cps(seg,every_min,base_clk,efob_start):
    out=[]; t=0
    while t+every_min*60<=seg['time']:
        t+=every_min*60; d=seg['GS']*(t/3600.0); burn=seg['ff']*(t/3600.0)
        eto=(base_clk+dt.timedelta(seconds=t)).strftime('%H:%M') if base_clk else ""
        efob=max(0.0, r10f(efob_start-burn))
        out.append({"t":t,"min":int(t/60),"nm":round(d,1),"eto":eto,"efob":efob})
    return out

# ===================== Cálculo de uma perna =====================
def compute_leg(inputs, globals_):
    TC=inputs["TC"]; Dist=inputs["Dist"]; Alt0=inputs["Alt0"]; Alt1=inputs["Alt1"]
    Wfrom=inputs["Wfrom"]; Wkt=inputs["Wkt"]; CK=inputs["CK"]

    qnh=globals_["qnh"]; oat=globals_["oat"]; var=globals_["mag_var"]; varE=globals_["mag_is_e"]
    weight=globals_["weight"]; rpm_climb=globals_["rpm_climb"]; rpm_cruise=globals_["rpm_cruise"]; rpm_desc=globals_["rpm_desc"]; desc_angle=globals_["desc_angle"]

    pa0=press_alt(Alt0,qnh); pa1=press_alt(Alt1,qnh); pa_avg=(pa0+pa1)/2
    Vy=vy_interp(pa0); ROC=roc_interp(pa0,oat)                  # ft/min (ENR ajustado)
    TAS_climb=Vy                                               # suposição: TAS≈Vy para subida curta
    FF_climb=cruise_lookup(Alt0+0.5*max(0.0,Alt1-Alt0),int(rpm_climb),oat,weight)[1]
    TAS_cru,FF_cru=cruise_lookup(pa1,int(rpm_cruise),oat,weight)
    TAS_desc,FF_desc=cruise_lookup(pa_avg,int(rpm_desc),oat,weight)

    _,THc,GScl=wind_triangle(TC,TAS_climb,Wfrom,Wkt)
    _,THr,GScr=wind_triangle(TC,TAS_cru,Wfrom,Wkt)
    _,THd,GSde=wind_triangle(TC,TAS_desc,Wfrom,Wkt)
    MHc=apply_var(THc,var,varE); MHr=apply_var(THr,var,varE); MHd=apply_var(THd,var,varE)
    ROD = max(100.0, GSde * 5.0 * (desc_angle/3.0))            # ft/min

    profile = "LEVEL" if abs(Alt1-Alt0)<1e-6 else ("CLIMB" if Alt1>Alt0 else "DESCENT")
    segA={}; segB=None; END_ALT=Alt0; TOC_TOD=None

    if profile=="CLIMB":
        t_need=(Alt1-Alt0)/max(ROC,1e-6); d_need=GScl*(t_need/60)
        if d_need<=Dist:
            tA=rt10(t_need*60); segA={"name":"Climb → TOC","TH":THc,"MH":MHc,"GS":GScl,"TAS":TAS_climb,"ff":FF_climb,"time":tA,"dist":d_need,"alt0":Alt0,"alt1":Alt1}
            rem=Dist-d_need
            if rem>0:
                tB=rt10((rem/max(GScr,1e-6))*3600); segB={"name":"Cruise (após TOC)","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tB,"dist":rem,"alt0":Alt1,"alt1":Alt1}
            END_ALT=Alt1
            TOC_TOD=("TOC", segA['time'], segA['dist'])
        else:
            tA=rt10((Dist/max(GScl,1e-6))*3600); gained=ROC*(tA/60); END_ALT=Alt0+gained
            segA={"name":"Climb (não atinge)","TH":THc,"MH":MHc,"GS":GScl,"TAS":TAS_climb,"ff":FF_climb,"time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT}
    elif profile=="DESCENT":
        t_need=(Alt0-Alt1)/max(ROD,1e-6); d_need=GSde*(t_need/60)
        if d_need<=Dist:
            tA=rt10(t_need*60); segA={"name":"Descent → TOD","TH":THd,"MH":MHd,"GS":GSde,"TAS":TAS_desc,"ff":FF_desc,"time":tA,"dist":d_need,"alt0":Alt0,"alt1":Alt1}
            rem=Dist-d_need
            if rem>0:
                tB=rt10((rem/max(GScr,1e-6))*3600); segB={"name":"Cruise (após TOD)","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tB,"dist":rem,"alt0":Alt1,"alt1":Alt1}
            END_ALT=Alt1
            TOC_TOD=("TOD", segA['time'], segA['dist'])
        else:
            tA=rt10((Dist/max(GSde,1e-6))*3600); lost=ROD*(tA/60); END_ALT=max(0.0,Alt0-lost)
            segA={"name":"Descent (não atinge)","TH":THd,"MH":MHd,"GS":GSde,"TAS":TAS_desc,"ff":FF_desc,"time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT}
    else:
        tA=rt10((Dist/max(GScr,1e-6))*3600); segA={"name":"Level","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT}

    segments=[segA]+([segB] if segB else [])
    for s in segments: s["burn"]=s["ff"]*(s["time"]/3600)
    TOT_SEC=sum(s['time'] for s in segments); TOT_BURN=r10f(sum(s['burn'] for s in segments))

    return {
        "profile": profile,
        "segments": segments,
        "tot_sec": TOT_SEC,
        "tot_burn": TOT_BURN,
        "end_alt": END_ALT,
        "Vy": Vy,
        "ROC": ROC,  # ft/min
        "ROD": ROD,  # ft/min
        "TOC_TOD": TOC_TOD
    }

# ===================== Sidebar: Parâmetros Globais =====================
st.title("NAVLOG — Performance v10 (AFM)")
with st.sidebar:
    st.header("Parâmetros Globais")
    st.session_state.qnh=st.number_input("QNH (hPa)",900,1050,int(st.session_state.qnh))
    st.session_state.oat=st.number_input("OAT (°C)",-40,50,int(st.session_state.oat))
    c1,c2=st.columns(2)
    with c1:
        st.session_state.mag_var=st.number_input("Mag Var (°)",0,30,int(st.session_state.mag_var))
    with c2:
        st.session_state.mag_is_e = st.selectbox("Var E/W",["W","E"],index=(1 if st.session_state.mag_is_e else 0))=="E"
    st.session_state.weight=st.number_input("Peso (kg)",450.0,700.0,float(st.session_state.weight),step=1.0)
    st.session_state.start_clock=st.text_input("Hora off-blocks (HH:MM)",st.session_state.start_clock)
    st.divider()
    st.caption("RPM (defaults AFM/operacional)")
    c3,c4,c5=st.columns(3)
    with c3:
        st.session_state.rpm_climb=st.number_input("Climb RPM",1800,2265,int(st.session_state.rpm_climb),step=5)
    with c4:
        st.session_state.rpm_cruise=st.number_input("Cruise RPM",1800,2265,int(st.session_state.rpm_cruise),step=5)
    with c5:
        st.session_state.rpm_desc=st.number_input("Descent RPM",1600,2265,int(st.session_state.rpm_desc),step=5)
    st.session_state.desc_angle=st.number_input("Ângulo Descida (°)",1.0,6.0,float(st.session_state.desc_angle),step=0.1)

globals_pack = dict(
    qnh=st.session_state.qnh, oat=st.session_state.oat, mag_var=st.session_state.mag_var,
    mag_is_e=st.session_state.mag_is_e, weight=st.session_state.weight,
    rpm_climb=st.session_state.rpm_climb, rpm_cruise=st.session_state.rpm_cruise,
    rpm_desc=st.session_state.rpm_desc, desc_angle=st.session_state.desc_angle
)

# ===================== Helpers de estado das pernas =====================
def new_leg_inputs(prev_end_alt=None, carry_efob=85.0):
    return {
        "TC": 90.0, "Dist": 10.0,
        "Alt0": float(prev_end_alt if prev_end_alt is not None else 0.0),
        "Alt1": 4000.0,
        "Wfrom": 180, "Wkt": 15, "CK": 2,
        "carry_efob": float(carry_efob)
    }

def add_leg_below():
    prev_alt = st.session_state.legs[-1]["computed"]["end_alt"] if st.session_state.legs else 0.0
    prev_efob = st.session_state.legs[-1]["runtime"]["efob_end"] if st.session_state.legs else 85.0
    lid = st.session_state.next_leg_id; st.session_state.next_leg_id += 1
    st.session_state.legs.append({
        "id": lid,
        "inputs": new_leg_inputs(prev_alt, prev_efob),
        "computed": None,
        "runtime": {"efob_end": prev_efob}
    })

def delete_leg(idx):
    if 0 <= idx < len(st.session_state.legs):
        st.session_state.legs.pop(idx)
        recalc_from(0)

def recalc_from(start_idx):
    # Recalcula perna a perna, propagando Alt0 e EFOB
    base_txt=st.session_state.start_clock.strip(); base=None
    if base_txt:
        try:
            h,m=map(int,base_txt.split(":")); base=dt.datetime.combine(dt.date.today(), dt.time(h,m))
        except: base=None

    carry_alt = 0.0
    efob = 85.0
    current_clock = base
    for i in range(len(st.session_state.legs)):
        leg = st.session_state.legs[i]
        if i==0:
            # primeira perna usa inputs existentes (Alt0 pode ser user)
            pass
        else:
            # herdar fim da anterior
            leg["inputs"]["Alt0"] = carry_alt
            leg["inputs"]["carry_efob"] = efob

        # calcular
        comp = compute_leg(leg["inputs"], globals_pack)
        leg["computed"] = comp

        # EFOB fim
        burn_total = comp["tot_burn"]
        efob_end = max(0.0, r10f(leg["inputs"]["carry_efob"] - burn_total))
        leg["runtime"]["efob_end"] = efob_end

        # preparar herança para próxima
        carry_alt = comp["end_alt"]
        efob = efob_end

        # relógio base para próximos timelines
        if current_clock is not None:
            current_clock = current_clock + dt.timedelta(seconds=comp["tot_sec"])

# Inicial: se não houver pernas, cria uma
if not st.session_state.legs:
    add_leg_below()
# Cálculo inicial/corrente
recalc_from(0)

# ===================== Render de uma perna =====================
def render_leg_card(i, leg):
    inputs = leg["inputs"]; comp = leg["computed"]
    st.markdown(f"<div class='card'>", unsafe_allow_html=True)
    st.markdown(f"<div class='title'>Perna {i+1} <span class='badge'>Perfil: {comp['profile']}</span></div>", unsafe_allow_html=True)

    c1,c2,c3,c4 = st.columns(4)
    with c1:
        inputs["TC"] = st.number_input(f"True Course (°T) — P{i+1}", 0.0, 359.9, float(inputs["TC"]), step=0.1, key=f"TC_{leg['id']}")
        inputs["Dist"] = st.number_input(f"Distância (nm) — P{i+1}", 0.0, 500.0, float(inputs["Dist"]), step=0.1, key=f"DIST_{leg['id']}")
    with c2:
        inputs["Alt0"] = st.number_input(f"Alt início (ft) — P{i+1}", 0.0, 30000.0, float(inputs["Alt0"]), step=50.0, key=f"ALT0_{leg['id']}")
        inputs["Alt1"] = st.number_input(f"Alt alvo (ft) — P{i+1}", 0.0, 30000.0, float(inputs["Alt1"]), step=50.0, key=f"ALT1_{leg['id']}")
    with c3:
        inputs["Wfrom"] = st.number_input(f"Vento FROM (°T) — P{i+1}", 0, 360, int(inputs["Wfrom"]), step=1, key=f"WFROM_{leg['id']}")
        inputs["Wkt"] = st.number_input(f"Vento (kt) — P{i+1}", 0, 150, int(inputs["Wkt"]), step=1, key=f"WKT_{leg['id']}")
    with c4:
        inputs["CK"] = st.number_input(f"Checkpoints (min) — P{i+1}", 1, 10, int(inputs["CK"]), step=1, key=f"CK_{leg['id']}")
        inputs["carry_efob"] = st.number_input(f"EFOB início (L) — P{i+1}", 0.0, 300.0, float(inputs["carry_efob"]), step=0.1, key=f"EFOB0_{leg['id']}")

    # Recalcular esta e seguintes ao clicar
    ac1, ac2, ac3 = st.columns([1,1,6])
    with ac1:
        if st.button("💾 Atualizar", key=f"UPD_{leg['id']}"):
            recalc_from(i)
            st.rerun()
    with ac2:
        if st.button("🗑️ Apagar", key=f"DEL_{leg['id']}"):
            delete_leg(i)
            st.rerun()

    st.markdown("<div class='sep'></div>", unsafe_allow_html=True)

    # Métricas principais (inclui ROC/ROD)
    g1,g2,g3,g4 = st.columns(4)
    sA = comp["segments"][0]
    g1.metric("Alt ini→fim (ft)", f"{int(round(sA['alt0']))} → {int(round(comp['end_alt']))}")
    g2.metric("TH/MH (°)", f"{rang(sA['TH'])}T / {rang(sA['MH'])}M")
    g3.metric("GS/TAS (kt)", f"{rint(sA['GS'])} / {rint(sA['TAS'])}")
    g4.metric("FF (L/h)", f"{rint(sA['ff'])}")

    h1,h2,h3,h4 = st.columns(4)
    h1.metric("ETE", hhmmss(comp["tot_sec"]))
    h2.metric("Dist total (nm)", f"{sum(s['dist'] for s in comp['segments']):.1f}")
    h3.metric("Burn total (L)", f"{r10f(sum(s['burn'] for s in comp['segments'])):.1f}")
    h4.metric("EFOB fim (L)", f"{st.session_state.legs[i]['runtime']['efob_end']:.1f}")

    # ROC / ROD destacados
    r1, r2, r3 = st.columns(3)
    r1.metric("Vy (kt @ PA0)", f"{rint(comp['Vy'])}")
    r2.metric("ROC (ft/min @ ENR)", f"{rint(comp['ROC'])}")
    r3.metric("ROD alvo (ft/min)", f"{rint(comp['ROD'])}")

    # Banner TOC/TOD se aplicável (sem sobrepor timeline)
    if comp["TOC_TOD"]:
        tag, t_s, d_nm = comp["TOC_TOD"]
        st.markdown(f"<div class='banner'>📍 {tag} — {mmss(t_s)} • {d_nm:.1f} nm desde o início do segmento</div>", unsafe_allow_html=True)

    # Timeline Segmento 1
    st.caption(f"Segmento 1 — {sA['name']}")
    base_txt=st.session_state.start_clock.strip(); base=None
    if base_txt:
        try:
            h,m=map(int,base_txt.split(":")); base=dt.datetime.combine(dt.date.today(), dt.time(h,m))
            # ajustar base pela soma do tempo das pernas anteriores
            if i>0:
                secs_prev = sum(p["computed"]["tot_sec"] for p in st.session_state.legs[:i])
                base = base + dt.timedelta(seconds=secs_prev)
        except: base=None

    EF0=float(inputs["carry_efob"])
    cpA=cps(sA,int(inputs["CK"]),base,EF0)
    start_lbl = base.strftime('%H:%M') if base else 'T+0'
    end_lbl = (base+dt.timedelta(seconds=sA['time'])).strftime('%H:%M') if base else mmss(sA['time'])
    timeline(sA, cpA, start_lbl, end_lbl)

    # Segmento 2 se existir
    if len(comp["segments"])>1:
        sB = comp["segments"][1]
        st.caption(f"Segmento 2 — {sB['name']}")
        EF1=max(0.0, r10f(EF0 - sA['burn']))
        base2=(base+dt.timedelta(seconds=sA['time'])) if base else None
        cpB=cps(sB,int(inputs["CK"]),base2,EF1)
        start_lbl2 = base2.strftime('%H:%M') if base2 else 'T+0'
        end_lbl2 = (base2+dt.timedelta(seconds=sB['time'])).strftime('%H:%M') if base2 else mmss(sB['time'])
        timeline(sB, cpB, start_lbl2, end_lbl2)

    st.markdown("</div>", unsafe_allow_html=True)  # fecha card

# ===================== Render: Pernas =====================
st.subheader("Plano por Pernas")
for idx, leg in enumerate(st.session_state.legs):
    render_leg_card(idx, leg)

# Botão para criar nova perna (fica naturalmente em baixo)
if st.button("➕ Nova Perna (criar cartão em baixo)", type="primary"):
    add_leg_below()
    st.rerun()

# ===================== Histórico resumido =====================
st.markdown("---")
st.subheader("Histórico Resumido")
if not st.session_state.legs:
    st.caption("(vazio)")
else:
    for i, leg in enumerate(st.session_state.legs, start=1):
        comp=leg["computed"]
        st.markdown(f"**Perna {i}** — Perfil {comp['profile']} · ETE {hhmmss(comp['tot_sec'])} · Burn {r10f(comp['tot_burn']):.1f} L · End Alt {int(round(comp['end_alt']))} ft")
        for j, seg in enumerate(comp['segments'], start=1):
            st.caption(f"Seg {j} — {seg['name']} · TH/MH {rang(seg['TH'])}T/{rang(seg['MH'])}M · GS/TAS {rint(seg['GS'])}/{rint(seg['TAS'])} kt · FF {rint(seg['ff'])} L/h · {mmss(seg['time'])} · {seg['dist']:.1f} nm · Burn {r10f(seg['burn']):.1f} L")






