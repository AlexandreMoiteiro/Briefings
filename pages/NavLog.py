# app.py — NAVLOG v9.1 (AFM-only, UI/UX cleanup)
# - Planeamento por pernas com TOC/TOD como FIX claro (sem sobreposição de checkpoints)
# - Cruise RPM default = 2100 (pedido)
# - UI organizada (cards, separadores, timeline legível)
# - Gestão de legs: histórico em cards; criador da próxima leg aparece por baixo

import streamlit as st, datetime as dt, math
from math import sin, asin, radians, degrees

# =========================
# ====== APP CONFIG =======
# =========================
st.set_page_config(page_title="NAVLOG v9.1 (AFM)", layout="wide", initial_sidebar_state="collapsed")

# -------------------------
# ==== STYLE / THEME  -----
# -------------------------
APP_CSS = """
<style>
/* Global */
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
:root { --card-bg: #ffffff; --card-br: 14px; --muted:#6b7280; --ink:#111827; --bar:#e5e7eb; --tick:#374151; }
h1,h2,h3 { letter-spacing: 0.2px; }
.small { font-size:12px; color:var(--muted); }
.kv { display:flex; gap:10px; flex-wrap:wrap; }
.kv > div { background: var(--card-bg); border-radius: 10px; padding: 10px 12px; border:1px solid #eef; }
.kv .k { font-size:12px; color:var(--muted); }
.kv .v { font-weight:600; }

/* Cards */
.card { background: var(--card-bg); border:1px solid #e6e8f0; border-radius: var(--card-br); padding: 14px 16px; margin: 10px 0; box-shadow: 0 1px 0 rgba(0,0,0,0.02); }
.card h4 { margin: 0 0 8px 0; }
.card .sub { color: var(--muted); font-size: 12px; margin-top: -4px; }

/* Timeline */
.tl{position:relative;margin:10px 0 10px 0;padding-top:22px}
.tl .bar{height:4px;background:var(--bar);border-radius:2px}
.tl .tick{position:absolute;top:6px;width:2px;height:14px;background:var(--tick)}
.tl .lbl{position:absolute;top:26px;transform:translateX(-50%);text-align:center;font-size:11px;color:#333;white-space:nowrap}
.tl .head{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:6px}

/* TOC/TOD marker */
.marker{position:absolute;top:-10px;transform:translateX(-50%);text-align:center}
.marker .dot{width:10px;height:10px;border-radius:50%;background:#2563eb;margin:0 auto;border:2px solid white;box-shadow:0 0 0 2px #2563eb}
.marker .cap{font-size:11px;color:#2563eb;margin-top:4px;font-weight:600}

/* Chips */
.chip{display:inline-block;border:1px solid #dbe2ff;color:#294cff;background:#f8faff;border-radius:999px;padding:4px 10px;font-size:12px;margin-right:8px}
</style>
"""
st.markdown(APP_CSS, unsafe_allow_html=True)

# =========================
# ====== HELPERS ==========
# =========================
rt10 = lambda s: max(10,int(round(s/10.0)*10)) if s>0 else 0
mmss = lambda t: f"{t//60:02d}:{t%60:02d}"
hhmmss = lambda t: f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"
rang = lambda x: int(round(float(x)))%360
rint = lambda x: int(round(float(x)))
r10f = lambda x: round(float(x),1)

def wrap360(x):
    x=math.fmod(float(x),360.0)
    return x+360 if x<0 else x

def angdiff(a,b): return (a-b+180)%360-180

def wind_triangle(tc,tas,wdir,wkt):
    if tas<=0: return 0.0, wrap360(tc), 0.0
    d=radians(angdiff(wdir,tc)); cross=wkt*sin(d)
    s=max(-1,min(1,cross/max(tas,1e-9))); wca=degrees(asin(s))
    th=wrap360(tc+wca); gs=max(0.0, tas*math.cos(radians(wca)) - wkt*math.cos(d))
    return wca, th, gs

def apply_var(th, var, east_is_neg=False):
    # Convenção clássica: se var E e "east_is_neg"=True, aplica -var
    return wrap360(th - var if east_is_neg else th + var)

# =========================
# ====== AFM DATA =========
# (Resumo Tecnam P2008; interpolação bi-linear + correção simples de OAT/Weight)
# =========================
ROC_ENR = {
  0:{-25:981,0:835,25:704,50:586}, 2000:{-25:870,0:726,25:597,50:481},
  4000:{-25:759,0:617,25:491,50:377}, 6000:{-25:648,0:509,25:385,50:273},
  8000:{-25:538,0:401,25:279,50:170}, 10000:{-25:428,0:294,25:174,50:66},
  12000:{-25:319,0:187,25:69,50:-37}, 14000:{-25:210,0:80,25:-35,50:-139}
}
VY = {0:67,2000:67,4000:67,6000:67,8000:67,10000:67,12000:67,14000:67}
ROC_FACTOR = 0.90

# CRUISE[PA][RPM] -> (TAS kt, FF L/h)
CRUISE = {
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
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    table0, table1 = CRUISE[p0], CRUISE[p1]

    def v(tab):
        rpms=sorted(tab.keys())
        if rpm in tab: return tab[rpm]
        if rpm<rpms[0]: lo,hi=rpms[0],rpms[1]
        elif rpm>rpms[-1]: lo,hi=rpms[-2],rpms[-1]
        else:
            lo=max([r for r in rpms if r<=rpm]); hi=min([r for r in rpms if r>=rpm])
        (tas_lo,ff_lo),(tas_hi,ff_hi)=tab[lo],tab[hi]
        t=(rpm-lo)/(hi-lo) if hi!=lo else 0
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

# =========================
# ====== STATE =============
# =========================
def ens(k,v): return st.session_state.setdefault(k,v)
ens("mag_var",1); ens("mag_is_e",False); ens("qnh",1013); ens("oat",15); ens("weight",650.0)
ens("rpm_climb",2250); ens("rpm_cruise",2100); ens("rpm_desc",1800); ens("desc_angle",3.0)  # cruise default=2100
ens("start_clock",""); ens("legs",[]); ens("carry_alt",0.0); ens("carry_efob",85.0)

# =========================
# ====== HEADER FORM ======
# =========================
st.title("NAVLOG — Performance v9.1 (AFM)")
with st.expander("Parâmetros Globais (AFM/Condições)"):
    c1,c2,c3,c4=st.columns(4)
    with c1:
        st.session_state.qnh = st.number_input("QNH (hPa)",900,1050,int(st.session_state.qnh))
        st.session_state.oat = st.number_input("OAT (°C)",-40,50,int(st.session_state.oat))
    with c2:
        st.session_state.mag_var = st.number_input("Mag Var (°)",0,30,int(st.session_state.mag_var))
        st.session_state.mag_is_e = st.selectbox("Var E/W",["W","E"],index=(1 if st.session_state.mag_is_e else 0))=="E"
    with c3:
        st.session_state.weight = st.number_input("Peso (kg)",450.0,700.0,float(st.session_state.weight),step=1.0)
        st.session_state.start_clock = st.text_input("Hora off-blocks (HH:MM)",st.session_state.start_clock)
    with c4:
        st.session_state.rpm_climb = st.number_input("Climb RPM",1800,2265,int(st.session_state.rpm_climb),step=5)
        st.session_state.rpm_cruise = st.number_input("Cruise RPM",1800,2265,int(st.session_state.rpm_cruise),step=5)
        st.session_state.rpm_desc = st.number_input("Descent RPM",1600,2265,int(st.session_state.rpm_desc),step=5)
        st.session_state.desc_angle = st.number_input("Ângulo desc (°)",1.0,6.0,float(st.session_state.desc_angle),step=0.1)

st.markdown("<span class='small'>Dica: Cruise RPM por defeito é 2100. TOC/TOD surgem como marcadores dedicados.</span>", unsafe_allow_html=True)

# =========================
# ====== LEG BUILDER ======
# =========================
st.markdown("## Planeamento da Perna (Atual)")

# Entradas da perna (herdam carry_alt/efob)
a1,a2,a3,a4=st.columns(4)
with a1:
    TC = st.number_input("True Course (°T)",0.0,359.9,90.0,step=0.1)
    Dist = st.number_input("Distância (nm)",0.0,500.0,10.0,step=0.1)
with a2:
    Alt0 = st.number_input("Alt início (ft)",0.0,30000.0,float(st.session_state.carry_alt),step=50.0)
    Alt1 = st.number_input("Alt alvo (ft)",0.0,30000.0,4000.0,step=50.0)
with a3:
    Wfrom = st.number_input("Vento FROM (°T)",0,360,180,step=1)
    Wkt   = st.number_input("Vento (kt)",0,150,15,step=1)
with a4:
    CK    = st.number_input("Checkpoints (min)",1,10,2,step=1)

# =========================
# ====== CÁLCULOS =========
# =========================
pa0=press_alt(Alt0,st.session_state.qnh); pa1=press_alt(Alt1,st.session_state.qnh); pa_avg=(pa0+pa1)/2
Vy=vy_interp(pa0); ROC=roc_interp(pa0,st.session_state.oat)

# TAS (climb usa Vy como TAS simples, conservador)
TAS_climb=Vy
FF_climb = cruise_lookup(Alt0+0.5*max(0.0,Alt1-Alt0),int(st.session_state.rpm_climb),st.session_state.oat,st.session_state.weight)[1]
TAS_cru, FF_cru = cruise_lookup(pa1,int(st.session_state.rpm_cruise),st.session_state.oat,st.session_state.weight)
TAS_desc,FF_desc= cruise_lookup(pa_avg,int(st.session_state.rpm_desc), st.session_state.oat,st.session_state.weight)

# Vento
_,THc,GScl = wind_triangle(TC,TAS_climb,Wfrom,Wkt)
_,THr,GScr = wind_triangle(TC,TAS_cru,Wfrom,Wkt)
_,THd,GSde = wind_triangle(TC,TAS_desc,Wfrom,Wkt)

MHc = apply_var(THc,st.session_state.mag_var,st.session_state.mag_is_e)
MHr = apply_var(THr,st.session_state.mag_var,st.session_state.mag_is_e)
MHd = apply_var(THd,st.session_state.mag_var,st.session_state.mag_is_e)

# Descida por ângulo (ROD ≈ GS * 5 * (âng/3))
ROD = max(100.0, GSde * 5.0 * (st.session_state.desc_angle/3.0))

# Perfil
profile = "LEVEL" if abs(Alt1-Alt0)<1e-6 else ("CLIMB" if Alt1>Alt0 else "DESCENT")

segA={}; segB=None; END_ALT=Alt0; fix_marker=None  # fix_marker = {"label":"TOC/TOD","pct":...}
if profile=="CLIMB":
    t_need=(Alt1-Alt0)/max(ROC,1e-6); d_need=GScl*(t_need/60)
    if d_need<=Dist:
        tA=rt10(t_need*60)
        segA={"name":"Climb → TOC","TH":THc,"MH":MHc,"GS":GScl,"TAS":TAS_climb,"ff":FF_climb,"time":tA,"dist":d_need,"alt0":Alt0,"alt1":Alt1}
        rem=Dist-d_need
        # posição do TOC no timeline do SEG 1
        fix_marker=("TOC", (tA/max(1,segA['time']))*100.0)
        if rem>0:
            tB=rt10((rem/max(GScr,1e-6))*3600)
            segB={"name":"Cruise (após TOC)","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tB,"dist":rem,"alt0":Alt1,"alt1":Alt1}
        END_ALT=Alt1
    else:
        tA=rt10((Dist/max(GScl,1e-6))*3600); gained=ROC*(tA/60); END_ALT=Alt0+gained
        segA={"name":"Climb (não atinge)","TH":THc,"MH":MHc,"GS":GScl,"TAS":TAS_climb,"ff":FF_climb,"time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT}

elif profile=="DESCENT":
    t_need=(Alt0-Alt1)/max(ROD,1e-6); d_need=GSde*(t_need/60)
    if d_need<=Dist:
        tA=rt10(t_need*60)
        segA={"name":"Descent → TOD","TH":THd,"MH":MHd,"GS":GSde,"TAS":TAS_desc,"ff":FF_desc,"time":tA,"dist":d_need,"alt0":Alt0,"alt1":Alt1}
        rem=Dist-d_need
        fix_marker=("TOD", (tA/max(1,segA['time']))*100.0)
        if rem>0:
            tB=rt10((rem/max(GScr,1e-6))*3600)
            segB={"name":"Cruise (após TOD)","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tB,"dist":rem,"alt0":Alt1,"alt1":Alt1}
        END_ALT=Alt1
    else:
        tA=rt10((Dist/max(GSde,1e-6))*3600); lost=ROD*(tA/60); END_ALT=max(0.0,Alt0-lost)
        segA={"name":"Descent (não atinge)","TH":THd,"MH":MHd,"GS":GSde,"TAS":TAS_desc,"ff":FF_desc,"time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT}
else:
    tA=rt10((Dist/max(GScr,1e-6))*3600)
    segA={"name":"Level","TH":THr,"MH":MHr,"GS":GScr,"TAS":TAS_cru,"ff":FF_cru,"time":tA,"dist":Dist,"alt0":Alt0,"alt1":END_ALT}

segments=[segA]+([segB] if segB else [])
for s in segments: s["burn"]=s["ff"]*(s["time"]/3600)
TOT_SEC=sum(s['time'] for s in segments); TOT_BURN=r10f(sum(s['burn'] for s in segments))

# =========================
# ====== CHECKPOINTS ======
# =========================
start_txt=st.session_state.start_clock.strip(); base=None
if start_txt:
    try:
        h,m=map(int,start_txt.split(":")); base=dt.datetime.combine(dt.date.today(), dt.time(h,m))
    except:
        base=None

def cps(seg,every_min,base_clk,efob_start):
    out=[]; t=0
    while t+every_min*60<=seg['time']:
        t+=every_min*60; d=seg['GS']*(t/3600.0); burn=seg['ff']*(t/3600.0)
        eto=(base_clk+dt.timedelta(seconds=t)).strftime('%H:%M') if base_clk else ""
        efob=max(0.0, r10f(efob_start-burn))
        out.append({"t":t,"min":int(t/60),"nm":round(d,1),"eto":eto,"efob":efob})
    return out

def timeline(seg,cps,start_label,end_label, fix=None):
    total=max(1,int(seg['time']))
    ticks=[]; labels=[]
    for cp in cps:
        pct=(cp['t']/total)*100.0
        ticks.append(f"<div class='tick' style='left:{pct:.2f}%;'></div>")
        labels.append(
            f"<div class='lbl' style='left:{pct:.2f}%;'>"
            f"<div>T+{cp['min']}m</div><div>{cp['nm']} nm</div>"
            + (f"<div>{cp['eto']}</div>" if cp['eto'] else "")
            + f"<div>EFOB {cp['efob']:.1f}</div></div>"
        )
    marker_html=""
    if fix is not None:
        fix_name, fix_pct = fix
        marker_html = (
            f"<div class='marker' style='left:{fix_pct:.2f}%;'>"
            f"<div class='dot'></div><div class='cap'>{fix_name}</div></div>"
        )
    html=(f"<div class='tl'><div class='head'><div>{start_label}</div>"
          f"<div>GS {rint(seg['GS'])} kt · TAS {rint(seg['TAS'])} kt · FF {rint(seg['ff'])} L/h</div>"
          f"<div>{end_label}</div></div>"
          f"<div class='bar'></div>{marker_html}{''.join(ticks)}{''.join(labels)}</div>")
    st.markdown(html, unsafe_allow_html=True)

# =========================
# ====== OUTPUT UI ========
# =========================
st.markdown("---")
st.subheader("Resultados da Perna (Atual)")

# CARD Segmento 1
st.markdown(f"<div class='card'><h4>Segmento 1 — {segA['name']}</h4>", unsafe_allow_html=True)
c1,c2,c3,c4 = st.columns(4)
c1.metric("Alt ini→fim (ft)",f"{int(round(segA['alt0']))} → {int(round(segA['alt1']))}")
c2.metric("TH/MH (°)",f"{rang(segA['TH'])}T / {rang(segA['MH'])}M")
c3.metric("GS/TAS (kt)",f"{rint(segA['GS'])} / {rint(segA['TAS'])}")
c4.metric("FF (L/h)",f"{rint(segA['ff'])}")
c5,c6,c7 = st.columns(3)
c5.metric("Tempo",mmss(segA['time']))
c6.metric("Dist (nm)",f"{segA['dist']:.1f}")
c7.metric("Burn (L)",f"{r10f(segA['burn']):.1f}")
EF0=float(st.session_state.carry_efob); base1=base
cpA=cps(segA,int(CK),base1,EF0)
start_lbl = base1.strftime('%H:%M') if base1 else 'T+0'
end_lbl = (base1+dt.timedelta(seconds=segA['time'])).strftime('%H:%M') if base1 else mmss(segA['time'])
st.caption("Timeline e Checkpoints do Segmento 1")
timeline(segA,cpA,start_lbl,end_lbl, fix=fix_marker)
st.markdown("</div>", unsafe_allow_html=True)

# TOC/TOD bridge card (sem sobreposição)
if segB and fix_marker:
    label = "Top of Climb (TOC)" if fix_marker[0]=="TOC" else "Top of Descent (TOD)"
    st.markdown(
        f"<div class='card'><span class='chip'>{label}</span> "
        f"<span class='small'>Alcance a {mmss(segA['time'])} · {segA['dist']:.1f} nm desde o início</span></div>",
        unsafe_allow_html=True
    )

# CARD Segmento 2
if segB:
    st.markdown(f"<div class='card'><h4>Segmento 2 — {segB['name']}</h4>", unsafe_allow_html=True)
    s2a,s2b,s2c,s2d=st.columns(4)
    s2a.metric("Alt ini→fim (ft)",f"{int(round(segB['alt0']))} → {int(round(segB['alt1']))}")
    s2b.metric("TH/MH (°)",f"{rang(segB['TH'])}T / {rang(segB['MH'])}M")
    s2c.metric("GS/TAS (kt)",f"{rint(segB['GS'])} / {rint(segB['TAS'])}")
    s2d.metric("FF (L/h)",f"{rint(segB['ff'])}")
    s2e,s2f,s2g=st.columns(3)
    s2e.metric("Tempo",mmss(segB['time']))
    s2f.metric("Dist (nm)",f"{segB['dist']:.1f}")
    s2g.metric("Burn (L)",f"{r10f(segB['burn']):.1f}")
    EF1=max(0.0, r10f(EF0-segA['burn'])); base2=(base1+dt.timedelta(seconds=segA['time'])) if base1 else None
    st.caption("Timeline e Checkpoints do Segmento 2")
    cpB=cps(segB,int(CK),base2,EF1)
    start_lbl2 = base2.strftime('%H:%M') if base2 else 'T+0'
    end_lbl2 = (base2+dt.timedelta(seconds=segB['time'])).strftime('%H:%M') if base2 else mmss(segB['time'])
    timeline(segB,cpB,start_lbl2,end_lbl2, fix=None)  # Sem marcador extra aqui
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")
TOT_SEC=sum(s['time'] for s in segments); TOT_BURN=r10f(sum(s['burn'] for s in segments))
EF_END=max(0.0, r10f(float(st.session_state.carry_efob)-sum(s['burn'] for s in segments)))

totals = f"**Totais** — ETE {hhmmss(TOT_SEC)} • Burn {TOT_BURN:.1f} L  \n**EFOB** — Start {float(st.session_state.carry_efob):.1f} L → End {EF_END:.1f} L"
st.markdown(f"<div class='card'><h4>Resumo da Perna</h4><div class='sub'>{totals}</div></div>", unsafe_allow_html=True)

# =========================
# ====== HISTÓRICO =========
# =========================
st.markdown("## Histórico de Pernas")
if not st.session_state.legs:
    st.caption("(vazio)")
else:
    for i,leg in enumerate(st.session_state.legs, start=1):
        st.markdown(f"<div class='card'><h4>Perna {i}</h4>", unsafe_allow_html=True)
        for j,seg in enumerate(leg['segments'], start=1):
            st.markdown(
                f"**Seg {j} — {seg['name']}** · TH/MH {rang(seg['TH'])}T/{rang(seg['MH'])}M · "
                f"GS/TAS {rint(seg['GS'])}/{rint(seg['TAS'])} kt · FF {rint(seg['ff'])} L/h · "
                f"{mmss(seg['time'])} · {seg['dist']:.1f} nm · Burn {r10f(seg['burn']):.1f} L"
            )
        st.caption(f"Totais: ETE {hhmmss(leg['tot_sec'])} · Burn {r10f(leg['tot_burn']):.1f} L")
        # Ações rápidas por leg
        ca, cb, cc = st.columns([1,1,6])
        with ca:
            if st.button(f"Usar fim da Perna {i} como início", key=f"use_{i}"):
                # Herdar estado a partir desta leg
                st.session_state.carry_alt=float(leg['segments'][-1]['alt1'])
                # EFOB após a perna i
                used = r10f(sum(s['burn'] for s in leg['segments']))
                st.session_state.carry_efob = max(0.0, r10f(float(st.session_state.carry_efob) - used)) if i==len(st.session_state.legs) else max(0.0, r10f(EF_END))
                st.success(f"Parâmetros atualizados a partir da Perna {i}.")
        st.markdown("</div>", unsafe_allow_html=True)

# =========================
# ====== BUILDER NO FIM ===
# =========================
st.markdown("### Adicionar a Perna Atual ao Histórico")
cadd1, cadd2 = st.columns([1,5])
with cadd1:
    if st.button("➕ Adicionar Perna", type="primary"):
        st.session_state.legs.append({"segments":segments,"tot_sec":TOT_SEC,"tot_burn":TOT_BURN})
        st.session_state.carry_alt=float(segments[-1]['alt1'])
        st.session_state.carry_efob=EF_END
        st.success("Perna adicionada. Parâmetros de início herdados para a próxima (ALT/EFOB).")

with cadd2:
    st.markdown("<span class='small'>A próxima perna herda automaticamente Altitude inicial e EFOB final da perna atual. Podes também clicar em “Usar fim da Perna N como início”.</span>", unsafe_allow_html=True)







