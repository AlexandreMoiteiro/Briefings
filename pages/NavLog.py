# app.py — NAVLOG v10 (AFM-only, UI limpa, herança simples)
# Principais mudanças v10
# - Modelo de estado simples: cada perna = {inputs, segments, end_alt, end_efob, ete_sec}
# - "Guardar perna" grava e propaga Alt/EFOB/Tempo para a próxima, sem side-effects
# - UI em 3 blocos: Cabeçalho · Perna atual · Resultados (cartões) + Histórico compacto
# - Badge TOC/TOD destacado e SEM sobreposições; checkpoints com tabela + timeline limpa

import streamlit as st, datetime as dt, math
from math import sin, asin, radians, degrees
from typing import Dict, List

st.set_page_config(page_title="NAVLOG v10 (AFM)", layout="wide", initial_sidebar_state="collapsed")

# ==== util ====
rt10=lambda s:max(10,int(round(s/10.0)*10)) if s>0 else 0
mmss=lambda t:f"{t//60:02d}:{t%60:02d}"
hhmmss=lambda t:f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
rang=lambda x:int(round(float(x)))%360
rint=lambda x:int(round(float(x)))
r10f=lambda x:round(float(x),1)

def wrap360(x): x=math.fmod(float(x),360.0); return x+360 if x<0 else x

def angdiff(a,b): return (a-b+180)%360-180

def wind_triangle(tc,tas,wdir,wkt):
    if tas<=0: return 0.0, wrap360(tc), 0.0
    d=radians(angdiff(wdir,tc)); cross=wkt*sin(d)
    s=max(-1,min(1,cross/max(tas,1e-9))); wca=degrees(asin(s))
    th=wrap360(tc+wca); gs=max(0.0, tas*math.cos(radians(wca)) - wkt*math.cos(d))
    return wca, th, gs

apply_var=lambda th,var,east_is_neg=False: wrap360(th - var if east_is_neg else th + var)

# ==== Tabelas AFM (resumo) ====
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
isa_temp=lambda pa:15.0-2.0*(pa/1000.0)
press_alt=lambda alt,qnh: float(alt)+(1013.0-float(qnh))*30.0
clamp=lambda v,lo,hi:max(lo,min(hi,v))

def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

def cruise_lookup(pa,rpm,oat,weight):
    rpm=min(int(rpm),2265);
    pas=sorted(CRUISE.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    def v(tab):
        rpms=sorted(tab.keys())
        if rpm in tab: return tab[rpm]
        if rpm<rpms[0]: lo,hi=rpms[0],rpms[1]
        elif rpm>rpms[-1]: lo,hi=rpms[-2],rpms[-1]
        else:
            lo=max([r for r in rpms if r<=rpm]); hi=min([r for r in rpms if r>=rpm])
        (tas_lo,ff_lo),(tas_hi,ff_hi)=tab[lo],tab[hi]; t=(rpm-lo)/(hi-lo) if hi!=lo else 0
        return (tas_lo+t*(tas_hi-tas_lo), ff_lo+t*(ff_hi-ff_lo))
    tas0,ff0=v(CRUISE[p0]); tas1,ff1=v(CRUISE[p1])
    tas=interp1(pa_c,p0,p1,tas0,tas1); ff=interp1(pa_c,p0,p1,ff0,ff1)
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

# ==== estado simples ====
ens=lambda k,v: st.session_state.setdefault(k,v)
ens("qnh",1013); ens("oat",15); ens("mag_var",1); ens("mag_is_e",False); ens("weight",650.0)
ens("rpm_climb",2250); ens("rpm_cruise",2100); ens("rpm_desc",1800); ens("desc_angle",3.0)
ens("start_clock",""); ens("carry_alt",0.0); ens("carry_efob",85.0); ens("legs",[])

# ==== helpers de UI ====
def render_history():
    st.subheader("Histórico")
    if not st.session_state.legs:
        st.caption("(sem pernas guardadas)")
    else:
        for i,leg in enumerate(st.session_state.legs, start=1):
            st.markdown(f"**Perna {i}** · TC {rint(leg['inputs']['TC'])}°T · {leg['inputs']['Dist']:.1f} nm · ETE {mmss(leg['ete_sec'])} · End Alt {int(leg['end_alt'])} ft · EFOB {leg['end_efob']:.1f} L")
            for j,seg in enumerate(leg['segments'], start=1):
                st.caption(f"Seg{j}: {seg['name']} · TH/MH {rang(seg['TH'])}T/{rang(seg['MH'])}M · GS/TAS {rint(seg['GS'])}/{rint(seg['TAS'])} · {mmss(seg['time'])} · {seg['dist']:.1f} nm · Burn {r10f(seg['burn']):.1f} L")

# ==== cabeçalho ====
st.title("NAVLOG — Performance v10 (AFM)")
with st.expander("Cabeçalho / Performance", expanded=True):
    c1,c2,c3,c4=st.columns(4)
    with c1:
        st.session_state.qnh=st.number_input("QNH (hPa)",900,1050,int(st.session_state.qnh))
        st.session_state.oat=st.number_input("OAT (°C)",-40,50,int(st.session_state.oat))
    with c2:
        st.session_state.mag_var=st.number_input("Mag Var (°)",0,30,int(st.session_state.mag_var))
        st.session_state.mag_is_e = st.selectbox("Var E/W",["W","E"],index=(1 if st.session_state.mag_is_e else 0))=="E"
    with c3:
        st.session_state.weight=st.number_input("Peso (kg)",450.0,700.0,float(st.session_state.weight),step=1.0)
        st.session_state.start_clock=st.text_input("Hora off-blocks (HH:MM)",st.session_state.start_clock)
    with c4:
        st.session_state.rpm_climb=st.number_input("Climb RPM",1800,2265,int(st.session_state.rpm_climb),step=5)
        st.session_state.rpm_cruise=st.number_input("Cruise RPM",1800,2265,int(st.session_state.rpm_cruise),step=5)
        st.session_state.rpm_desc=st.number_input("Descent RPM",1600,2265,int(st.session_state.rpm_desc),step=5)
        st.session_state.desc_angle=st.number_input("Ângulo desc (°)",1.0,6.0,float(st.session_state.desc_angle),step=0.1)

# Mostra histórico primeiro (pedido do utilizador)
render_history()

# ==== entrada perna ====
st.subheader("Perna atual — entrada")
a1,a2,a3,a4=st.columns(4)
with a1:
    TC=st.number_input("True Course (°T)",0.0,359.9,90.0,step=0.1)
    Dist=st.number_input("Distância (nm)",0.0,500.0,10.0,step=0.1)
with a2:
    Alt0=st.number_input("Alt início (ft)",0.0,30000.0,float(st.session_state.carry_alt),step=50.0)
    Alt1=st.number_input("Alt alvo (ft)",0.0,30000.0,4000.0,step=50.0)
with a3:
    Wfrom=st.number_input("Vento FROM (°T)",0,360,180,step=1)
    Wkt=st.number_input("Vento (kt)",0,150,15,step=1)
with a4:
    CK=st.number_input("Checkpoints (min)",1,10,2,step=1)

# ==== cálculo perna atual ====
pa0=press_alt(Alt0,st.session_state.qnh); pa1=press_alt(Alt1,st.session_state.qnh); pa_avg=(pa0+pa1)/2
Vy=vy_interp(pa0); ROC=roc_interp(pa0,st.session_state.oat)
TAS_climb=Vy; FF_climb=cruise_lookup(Alt0+0.5*max(0.0,Alt1-Alt0),int(st.session_state.rpm_climb),st.session_state.oat,st.session_state.weight)[1]
TAS_cru,FF_cru=cruise_lookup(pa1,int(st.session_state.rpm_cruise),st.session_state.oat,st.session_state.weight)
TAS_desc,FF_desc=cruise_lookup(pa_avg,int(st.session_state.rpm_desc),st.session_state.oat,st.session_state.weight)
_,THc,GScl=wind_triangle(TC,TAS_climb,Wfrom,Wkt); _,THr,GScr=wind_triangle(TC,TAS_cru,Wfrom,Wkt); _,THd,GSde=wind_triangle(TC,TAS_desc,Wfrom,Wkt)
MHc=apply_var(THc,st.session_state.mag_var,st.session_state.mag_is_e); MHr=apply_var(THr,st.session_state.mag_var,st.session_state.mag_is_e); MHd=apply_var(THd,st.session_state.mag_var,st.session_state.mag_is_e)
ROD=max(100.0, GSde*5.0*(st.session_state.desc_angle/3.0))

profile = "LEVEL" if abs(Alt1-Alt0)<1e-6 else ("CLIMB" if Alt1>Alt0 else "DESCENT")
segA={}; segB=None; END_ALT=Alt0
if profile=="CLIMB":
    t_need=(Alt1-Alt0)/max(ROC,1e-6); d_need=GScl*(t_need/60)
    if d_need<=Dist:
        tA=rt10(t_need*60); segA={"tag":"CLIMB","name":"Climb → TOC","TH":THc,"MH":MHc,"GS":GScl,"TAS":TAS_climb,"ff":FF_climb,"time":tA,"dist":d_need,"alt0":Alt0,"alt1":Alt1}
        rem=Dist-d_need
        if rem>0:
            tB=rt10((rem/max(GScr,1e-6))*3600); segB={"tag":"CRUISE","name":"Cruise (após TOC)","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tB,"dist":rem,"alt0":Alt1,"alt1":Alt1}
        END_ALT=Alt1
    else:
        tA=rt10((Dist/max(GScl,1e-6))*3600); gained=ROC*(tA/60); END_ALT=Alt0+gained
        segA={"tag":"CLIMB","name":"Climb (não atinge)","TH":THc,"MH":MHc,"GS":GScl,"TAS":TAS_climb,"ff":FF_climb,"time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT}
elif profile=="DESCENT":
    t_need=(Alt0-Alt1)/max(ROD,1e-6); d_need=GSde*(t_need/60)
    if d_need<=Dist:
        tA=rt10(t_need*60); segA={"tag":"DESC","name":"Descent → TOD","TH":THd,"MH":MHd,"GS":GSde,"TAS":TAS_desc,"ff":FF_desc,"time":tA,"dist":d_need,"alt0":Alt0,"alt1":Alt1}
        rem=Dist-d_need
        if rem>0:
            tB=rt10((rem/max(GScr,1e-6))*3600); segB={"tag":"CRUISE","name":"Cruise (após TOD)","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tB,"dist":rem,"alt0":Alt1,"alt1":Alt1}
        END_ALT=Alt1
    else:
        tA=rt10((Dist/max(GSde,1e-6))*3600); lost=ROD*(tA/60); END_ALT=max(0.0,Alt0-lost)
        segA={"tag":"DESC","name":"Descent (não atinge)","TH":THd,"MH":MHd,"GS":GSde,"TAS":TAS_desc,"ff":FF_desc,"time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT}
else:
    tA=rt10((Dist/max(GScr,1e-6))*3600); segA={"tag":"CRUISE","name":"Level","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT}

segments=[segA]+([segB] if segB else [])
for s in segments: s["burn"]=s["ff"]*(s["time"]/3600)
TOT_SEC=sum(s['time'] for s in segments); TOT_BURN=r10f(sum(s['burn'] for s in segments))

# ==== checkpoints helper ====
start_txt=st.session_state.start_clock.strip(); base=None
if start_txt:
    try:
        h,m=map(int,start_txt.split(":")); base=dt.datetime.combine(dt.date.today(), dt.time(h,m))
    except: base=None

def cps(seg,every_min,base_clk,efob_start):
    out=[]; t=0
    while t+every_min*60<=seg['time']:
        t+=every_min*60; d=seg['GS']*(t/3600.0); burn=seg['ff']*(t/3600.0)
        eto=(base_clk+dt.timedelta(seconds=t)).strftime('%H:%M') if base_clk else ""
        efob=max(0.0, r10f(efob_start-burn))
        out.append({"t":t,"min":int(t/60),"nm":round(d,1),"eto":eto,"efob":efob})
    return out

# ==== UI styles ====
CSS="""
<style>
*{letter-spacing:0.1px}
.card{border:1px solid #e9eef5;border-radius:14px;padding:16px 18px;margin:12px 0;background:#fff;box-shadow:0 2px 6px rgba(16,24,40,.06)}
.hlite{background:#f6f9ff;border:1px solid #dbe7ff}
.badge{display:inline-block;padding:6px 12px;border-radius:999px;background:#155eef; color:#fff; font-weight:700; letter-spacing:0.3px}
.sep{height:1px;background:#edf0f3;margin:10px 0}
.small{font-size:12px;color:#667085}
.tl{position:relative;margin:8px 0 6px 0;padding-top:18px}
.tl .bar{height:3px;background:#e6eaf0;border-radius:2px}
.tl .tick{position:absolute;top:6px;width:2px;height:10px;background:#111827}
.tl .lbl{position:absolute;top:18px;transform:translateX(-50%);text-align:center;font-size:11px;color:#111827;white-space:nowrap}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ==== resultados (cartões) ====
st.subheader("Resultados da Perna")

# Seg 1
st.markdown(f"#### Segmento 1 — {segA['name']}")
st.markdown("<div class='card'>", unsafe_allow_html=True)
col1,col2,col3,col4=st.columns(4)
col1.metric("Alt ini→fim (ft)",f"{int(round(segA['alt0']))} → {int(round(segA['alt1']))}")
col2.metric("TH/MH (°)",f"{rang(segA['TH'])}T / {rang(segA['MH'])}M")
col3.metric("GS/TAS (kt)",f"{rint(segA['GS'])} / {rint(segA['TAS'])}")
col4.metric("FF (L/h)",f"{rint(segA['ff'])}")
row2,row3=st.columns(2)
row2.metric("Tempo",mmss(segA['time']))
row3.metric("Dist & Burn",f"{segA['dist']:.1f} nm · {r10f(segA['burn'])} L")
if segA['tag']=="CLIMB": st.caption(f"Vy {rint(Vy)} kt · ROC {rint(ROC)} fpm")
elif segA['tag']=="DESC": st.caption(f"ROD auto {rint(ROD)} fpm (ângulo {r10f(st.session_state.desc_angle)}°)")

# timeline + tabela
EF0=float(st.session_state.carry_efob); base1=base
cpA=cps(segA,int(CK),base1,EF0)
start_lbl = base1.strftime('%H:%M') if base1 else 'T+0'
end_lbl = (base1+dt.timedelta(seconds=segA['time'])).strftime('%H:%M') if base1 else mmss(segA['time'])
# timeline limpa
html = f"<div class='tl'><div class='bar'></div>"+''.join([f"<div class='tick' style='left:{(c['t']/segA['time'])*100:.2f}%;'></div><div class='lbl' style='left:{(c['t']/segA['time'])*100:.2f}%;'>T+{c['min']}m<br/>{c['nm']} nm<br/>{c['eto']}<br/>EFOB {c['efob']:.1f}</div>" for c in cpA])+"</div>"
st.markdown(html, unsafe_allow_html=True)
# tabela
if cpA:
    st.table({"T+ (min)":[c['min'] for c in cpA],"NM":[c['nm'] for c in cpA],"ETO":[c['eto'] for c in cpA],"EFOB":[c['efob'] for c in cpA]})
st.markdown("</div>", unsafe_allow_html=True)

# TOC/TOD
if segB:
    label = "TOC" if segA['tag']=="CLIMB" else "TOD"
    st.markdown(f"<div class='sep'></div><div class='badge'>{label}</div> <span class='small'>· {mmss(segA['time'])} · {segA['dist']:.1f} nm desde o início</span><div class='sep'></div>", unsafe_allow_html=True)

# Seg 2
if segB:
    st.markdown(f"#### Segmento 2 — {segB['name']}")
    st.markdown("<div class='card hlite'>", unsafe_allow_html=True)
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Alt ini→fim (ft)",f"{int(round(segB['alt0']))} → {int(round(segB['alt1']))}")
    c2.metric("TH/MH (°)",f"{rang(segB['TH'])}T / {rang(segB['MH'])}M")
    c3.metric("GS/TAS (kt)",f"{rint(segB['GS'])} / {rint(segB['TAS'])}")
    c4.metric("FF (L/h)",f"{rint(segB['ff'])}")
    r2a,r2b=st.columns(2)
    r2a.metric("Tempo",mmss(segB['time']))
    r2b.metric("Dist & Burn",f"{segB['dist']:.1f} nm · {r10f(segB['burn']):.1f} L")
    EF1=max(0.0, r10f(EF0-segA['burn'])); base2=(base1+dt.timedelta(seconds=segA['time'])) if base1 else None
    cpB=cps(segB,int(CK),base2,EF1)
    html2 = f"<div class='tl'><div class='bar'></div>"+''.join([f"<div class='tick' style='left:{(c['t']/segB['time'])*100:.2f}%;'></div><div class='lbl' style='left:{(c['t']/segB['time'])*100:.2f}%;'>T+{c['min']}m<br/>{c['nm']} nm<br/>{c['eto']}<br/>EFOB {c['efob']:.1f}</div>" for c in cpB])+"</div>"
    st.markdown(html2, unsafe_allow_html=True)
    if cpB:
        st.table({"T+ (min)":[c['min'] for c in cpB],"NM":[c['nm'] for c in cpB],"ETO":[c['eto'] for c in cpB],"EFOB":[c['efob'] for c in cpB]})
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")
EF_END=max(0.0, r10f(float(st.session_state.carry_efob)-sum(s['burn'] for s in segments)))
st.markdown(f"**Totais** — ETE {hhmmss(TOT_SEC)} · Burn {r10f(sum(s['burn'] for s in segments)):.1f} L · EFOB fim {EF_END:.1f} L")

# ==== guardar / preparar próxima ====
st.subheader("Ações")
a1,a2 = st.columns(2)
with a1:
    if st.button("💾 Guardar perna no histórico", type="primary"):
        st.session_state.legs.append({
            "inputs": {"TC":TC,"Dist":Dist,"Alt0":Alt0,"Alt1":Alt1,"Wfrom":Wfrom,"Wkt":Wkt,"CK":CK,
                       "rpm":{"climb":st.session_state.rpm_climb,"cruise":st.session_state.rpm_cruise,"desc":st.session_state.rpm_desc}},
            "segments": segments,
            "end_alt": float(segments[-1]['alt1']),
            "end_efob": EF_END,
            "ete_sec": TOT_SEC
        })
        st.success("Perna guardada no histórico.")
with a2:
    if st.button("➕ Definir próxima perna (não guarda)"):
        st.session_state.carry_alt=float(segments[-1]['alt1'])
        st.session_state.carry_efob=EF_END
        st.info(f"Próxima perna iniciará a {int(st.session_state.carry_alt)} ft · EFOB {st.session_state.carry_efob:.1f} L")

st.caption("Dica: usa 'Guardar' para manter a perna no histórico. 'Definir próxima' apenas propaga Alt/EFOB sem gravar.")

# ==== histórico (já renderizado no topo) ====
st.markdown("")




