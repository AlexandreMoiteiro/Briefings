# app.py — NAVLOG (v3 final)
# Tecnam P2008 — TOC/TOD só em CRUISE, Altitudes editáveis, média TAS, HOLDs e tempo padronizado (min / h)

import streamlit as st
import datetime as dt
import pytz, io, json, unicodedata, re, math
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from math import sin, asin, radians, degrees, fmod

st.set_page_config(page_title="NAVLOG (PDF + Relatório)", layout="wide", initial_sidebar_state="collapsed")

# ===== Optional deps =====
try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, TextStringObject
    PYPDF_OK = True
except Exception:
    PYPDF_OK = False

try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, PageBreak, KeepTogether
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

# =========================================================
# HELPERS
# =========================================================
def clean_point_name(s) -> str:
    txt = unicodedata.normalize("NFKD", str(s or "")).encode("ascii","ignore").decode("ascii")
    return txt.strip().upper()

def _round_alt(x: float) -> int:
    if x is None: return 0
    v = abs(float(x)); base = 50 if v < 1000 else 100
    return int(round(float(x)/base) * base)

def fmt(x: float, kind: str) -> str:
    if kind == "dist":   return f"{round(float(x or 0),1):.1f}"
    if kind == "fuel":   return f"{round(float(x or 0),1):.1f}"
    if kind == "speed":  return str(int(round(float(x or 0))))
    if kind == "angle":  return str(int(round(float(x or 0))) % 360)
    if kind == "alt":    return str(_round_alt(x))
    return str(x)

def clamp(v, lo, hi): return max(lo, min(hi, v))
def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

# -------- Tempo (novo formato min/h) --------
def fmt_time_human(sec: int) -> str:
    """Formata tempo em minutos e horas (ex: 45 min / 1h15)."""
    if sec < 0: sec = 0
    m = int(round(sec/60))
    if m < 60:
        return f"{m} min"
    else:
        h = m // 60
        r = m % 60
        return f"{h}h{r:02d}" if r else f"{h}h00"

def add_seconds(t:dt.time, s:int):
    if not t: return None
    today=dt.date.today(); base=dt.datetime.combine(today,t)
    return (base+dt.timedelta(seconds=int(s))).time()

def parse_hhmm(s:str):
    s=(s or "").strip()
    for fmt in ("%H:%M:%S","%H:%M","%H%M"):
        try: return dt.datetime.strptime(s,fmt).time()
        except: pass
    return None

def wrap360(x): x=fmod(x,360.0); return x+360 if x<0 else x
def angle_diff(a,b): return (a-b+180)%360-180

# =========================================================
# PERFORMANCE / ATMOSFERA
# =========================================================
ROC_ENROUTE = {
    0:{-25:981,0:835,25:704,50:586}, 2000:{-25:870,0:726,25:597,50:481},
    4000:{-25:759,0:617,25:491,50:377},6000:{-25:648,0:509,25:385,50:273},
    8000:{-25:538,0:401,25:279,50:170},10000:{-25:428,0:294,25:174,50:66},
}
ROC_FACTOR=0.90
VY_ENROUTE={0:67,2000:67,4000:67,6000:67,8000:67,10000:67}

CRUISE={
 0:{2000:(95,18.7),2100:(101,20.7),2250:(110,24.6),2388:(118,26.9)},
 2000:{2000:(94,17.5),2100:(100,19.9),2250:(109,23.5)},
 4000:{2000:(94,17.5),2100:(100,19.2),2250:(108,22.4)},
 6000:{2000:(93,17.1),2100:(99,18.5),2250:(108,21.3)},
 8000:{2000:(92,16.7),2100:(98,18.0),2250:(107,20.4)},
 10000:{2000:(91,16.4),2100:(97,17.5),2250:(106,19.7)},
}

def isa_temp(pa_ft): return 15.0 - 2.0*(pa_ft/1000.0)

def cruise_lookup(pa_ft: float, rpm: int, oat_c: Optional[float]) -> Tuple[float,float]:
    pas=sorted(CRUISE.keys()); pa_c=clamp(pa_ft,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    def val(pa):
        table=CRUISE[pa]; rpms=sorted(table.keys())
        lo=max([r for r in rpms if r<=rpm]); hi=min([r for r in rpms if r>=rpm])
        (tas_lo,ff_lo),(tas_hi,ff_hi)=table[lo],table[hi]
        t=(rpm-lo)/(hi-lo) if hi!=lo else 0.0
        return (tas_lo + t*(tas_hi-tas_lo), ff_lo + t*(ff_hi-ff_lo))
    tas0,ff0=val(p0); tas1,ff1=val(p1)
    tas=interp1(pa_c,p0,p1,tas0,tas1); ff=interp1(pa_c,p0,p1,ff0,ff1)
    if oat_c is not None:
        dev=oat_c - isa_temp(pa_c)
        if dev>0: tas*=1-0.02*(dev/15); ff*=1-0.025*(dev/15)
        elif dev<0: tas*=1+0.01*((-dev)/15); ff*=1+0.03*((-dev)/15)
    return max(0.0,tas), max(0.0,ff)
# =========================================================
# AERÓDROMOS EXEMPLO
# =========================================================
AEROS={
 "LPSO":{"elev":390,"freq":"119.805"},
 "LPEV":{"elev":807,"freq":"122.705"},
 "LPCB":{"elev":1251,"freq":"122.300"},
 "LPCO":{"elev":587,"freq":"118.405"},
 "LPVZ":{"elev":2060,"freq":"118.305"},
}
def aero_elev(icao): return int(AEROS.get(icao,{}).get("elev",0))
def aero_freq(icao): return AEROS.get(icao,{}).get("freq","")

# =========================================================
# PARÂMETROS INICIAIS (SESSION STATE)
# =========================================================
def ensure(k,v):
    if k not in st.session_state: st.session_state[k]=v

ensure("aircraft","P2008")
ensure("registration","CS-ECC")
ensure("callsign","RVP")
ensure("student","ALUNO")
ensure("lesson","")
ensure("instrutor","")
ensure("dept","LPSO")
ensure("arr","LPEV")
ensure("altn","LPCB")
ensure("startup","")
ensure("qnh",1013)
ensure("cruise_alt",4000)
ensure("temp_c",15)
ensure("var_deg",1)
ensure("var_is_e",False)
ensure("wind_from",0)
ensure("wind_kt",15)
ensure("rpm_climb",2250)
ensure("rpm_cruise",2000)
ensure("descent_ff",15.0)
ensure("rod_fpm",700)
ensure("start_fuel",85.0)
ensure("cruise_ref_kt",90)
ensure("descent_ref_kt",65)
ensure("hold_ref_kt",80)
ensure("hold_ff_lph",18.0)
ensure("taxi_min",10)
ensure("taxi_ff_lph",20.0)
ensure("use_navaids",False)

# =========================================================
# CABEÇALHO STREAMLIT
# =========================================================
st.title("Navigation Log — Tecnam P2008 (v3 Final)")

with st.form("perf_form"):
    st.subheader("Cabeçalho e Performance")

    c1,c2,c3 = st.columns(3)
    with c1:
        f_aircraft=st.text_input("Aircraft",st.session_state.aircraft)
        f_reg=st.selectbox("Registration",["CS-ECC","CS-ECD","CS-DHS","CS-DHT"],index=0)
        f_callsign=st.text_input("Callsign",st.session_state.callsign)
        f_startup=st.text_input("Startup (HH:MM)",st.session_state.startup)
    with c2:
        f_student=st.text_input("Student",st.session_state.student)
        f_lesson=st.text_input("Lesson",st.session_state.lesson)
        f_instrutor=st.text_input("Instrutor",st.session_state.instrutor)
    with c3:
        f_dep=st.selectbox("Departure",list(AEROS.keys()),index=0)
        f_arr=st.selectbox("Arrival",list(AEROS.keys()),index=1)
        f_altn=st.selectbox("Alternate",list(AEROS.keys()),index=2)

    st.markdown("---")
    c4,c5,c6 = st.columns(3)
    with c4:
        f_qnh=st.number_input("QNH",900,1050,int(st.session_state.qnh))
        f_crz=st.number_input("Cruise Altitude (ft)",0,14000,int(st.session_state.cruise_alt),step=50)
    with c5:
        f_temp=st.number_input("OAT (°C)",-40,50,int(st.session_state.temp_c))
        f_var=st.number_input("Mag Var (°)",0,30,int(st.session_state.var_deg))
        f_varE=(st.selectbox("Variação",["W","E"],index=(1 if st.session_state.var_is_e else 0))=="E")
    with c6:
        f_wdir=st.number_input("Wind FROM (°T)",0,360,int(st.session_state.wind_from))
        f_wkt=st.number_input("Wind (kt)",0,100,int(st.session_state.wind_kt))

    st.markdown("---")
    c7,c8,c9=st.columns(3)
    with c7:
        f_rpmc=st.number_input("Climb RPM",1800,2388,int(st.session_state.rpm_climb))
        f_rpmcr=st.number_input("Cruise RPM",1800,2388,int(st.session_state.rpm_cruise))
    with c8:
        f_ffd=st.number_input("Descent FF (L/h)",0.0,30.0,float(st.session_state.descent_ff))
        f_rod=st.number_input("ROD (ft/min)",200,1500,int(st.session_state.rod_fpm))
    with c9:
        f_fuel0=st.number_input("Fuel Inicial (L)",0.0,500.0,float(st.session_state.start_fuel))
        f_taxi=st.number_input("Taxi (min)",0,30,int(st.session_state.taxi_min))

    sub = st.form_submit_button("Aplicar parâmetros")
    if sub:
        st.session_state.aircraft=f_aircraft; st.session_state.registration=f_reg
        st.session_state.callsign=f_callsign; st.session_state.startup=f_startup
        st.session_state.student=f_student; st.session_state.lesson=f_lesson; st.session_state.instrutor=f_instrutor
        st.session_state.dept=f_dep; st.session_state.arr=f_arr; st.session_state.altn=f_altn
        st.session_state.qnh=f_qnh; st.session_state.cruise_alt=f_crz
        st.session_state.temp_c=f_temp; st.session_state.var_deg=f_var
        st.session_state.var_is_e=f_varE; st.session_state.wind_from=f_wdir; st.session_state.wind_kt=f_wkt
        st.session_state.rpm_climb=f_rpmc; st.session_state.rpm_cruise=f_rpmcr
        st.session_state.descent_ff=f_ffd; st.session_state.rod_fpm=f_rod
        st.session_state.start_fuel=f_fuel0; st.session_state.taxi_min=f_taxi
        st.success("Parâmetros aplicados.")

# =========================================================
# ROTA BÁSICA
# =========================================================
def parse_route_text(txt:str)->List[str]:
    tokens=re.split(r"[,\s→\-]+",(txt or "").strip())
    return [clean_point_name(t) for t in tokens if t]

default_route=f"{st.session_state.dept} {st.session_state.arr}"
route_text=st.text_area("Rota (DEP … ARR)",value=st.session_state.get("route_text",default_route))

if st.button("Aplicar rota"):
    pts=parse_route_text(route_text)
    if len(pts)<2: pts=[st.session_state.dept,st.session_state.arr]
    st.session_state.points=pts
    st.session_state.route_text=" ".join(pts)
    st.success("Rota aplicada.")

if "points" not in st.session_state:
    st.session_state.points=parse_route_text(st.session_state.get("route_text",default_route))
# =========================================================
# ALTITUDES / HOLDS (tabela editável)
# =========================================================
def rebuild_alt_rows(points: List[str], cruise:int, prev: Optional[List[dict]]):
    out=[]
    prev=prev or []
    for i,p in enumerate(points):
        base=prev[i] if i<len(prev) else {}
        row={
            "Fix": bool(base.get("Fix", i in (0,len(points)-1))),
            "Point": p,
            "Alt_ft": float(base.get("Alt_ft", _round_alt(aero_elev(p) if i in (0,len(points)-1) else cruise))),
            "Hold": bool(base.get("Hold", False)),
            "Hold_min": float(base.get("Hold_min", 0.0))
        }
        out.append(row)
    return out

if "alt_rows" not in st.session_state:
    st.session_state.alt_rows = rebuild_alt_rows(st.session_state.points, st.session_state.cruise_alt, None)

st.subheader("Altitudes & HOLDs por FIX")
alt_cfg={
    "Fix": st.column_config.CheckboxColumn("Fixado?"),
    "Point": st.column_config.TextColumn("Fix",disabled=True),
    "Alt_ft": st.column_config.NumberColumn("Altitude (ft)",step=50,min_value=0.0),
    "Hold": st.column_config.CheckboxColumn("HOLD?"),
    "Hold_min": st.column_config.NumberColumn("Min HOLD",step=1.0,min_value=0.0)
}
with st.form("alt_form"):
    alt_edited=st.data_editor(st.session_state.alt_rows,key="alt_table",use_container_width=True,hide_index=True,column_config=alt_cfg,num_rows="fixed")
    alt_submit=st.form_submit_button("Aplicar Altitudes/HOLDs")
    if alt_submit:
        st.session_state.alt_rows=[dict(r) for r in alt_edited]
        st.success("Altitudes e HOLDs aplicados.")

# =========================================================
# CÁLCULO DE PERFIL — TOC/TOD, médias TAS, HOLDs
# =========================================================
points=st.session_state.points
alts=st.session_state.alt_rows
N=len(points)-1
if N<=0:
    st.warning("Defina pelo menos dois pontos na rota.")
else:
    dep_elev=_round_alt(aero_elev(points[0]))
    arr_elev=_round_alt(aero_elev(points[-1]))
    CRZ=float(st.session_state.cruise_alt)
    qnh=float(st.session_state.qnh)
    temp=float(st.session_state.temp_c)

    # Altitude alvo por ponto
    A_target=[]
    for i,p in enumerate(points):
        r=alts[i] if i<len(alts) else {}
        if i==0 or i==len(points)-1:
            A_target.append(float(r.get("Alt_ft",_round_alt(aero_elev(p)))))
        else:
            A_target.append(float(r.get("Alt_ft",CRZ)) if bool(r.get("Fix",False)) else float(CRZ))

    # Funções vento e GS
    def leg_wind(): return (int(st.session_state.wind_from),int(st.session_state.wind_kt))
    def gs_phase(tc,tas):
        wdir,wkt=leg_wind()
        delta=radians(angle_diff(wdir,tc))
        gs=tas - wkt*math.cos(delta)
        return max(gs,1e-3)

    # Parâmetros básicos
    vy_kt=67
    ff_climb=20.0
    ff_cruise=18.0
    ff_descent=float(st.session_state.descent_ff)
    rod=float(st.session_state.rod_fpm)

    # Label control
    _occ_counter={}; _last_label=None
    def _occ_label(name:str)->str:
        """Evita duplicar fixes consecutivos; numera apenas quando o mesmo fix reaparece mais tarde."""
        nonlocal _occ_counter,_last_label
        base=clean_point_name(name)
        if base.startswith("TOC") or base.startswith("TOD") or base.startswith("↗") or base.startswith("↘") or base.startswith("HOLD"):
            return name
        if _last_label==base: return name
        _occ_counter[base]=_occ_counter.get(base,0)+1
        _last_label=base
        n=_occ_counter[base]
        return f"{name} ({n})" if n>1 else name

    # Inicialização
    rows=[]; seq_points=[]
    efob=float(st.session_state.start_fuel)
    startup=parse_hhmm(st.session_state.startup)
    takeoff=add_seconds(startup,st.session_state.taxi_min*60) if startup else None
    clock=takeoff

    first_label=_occ_label(points[0])
    seq_points.append({"name":first_label,"alt":A_target[0],"efob":efob,"phase":"DEP","ete_sec":0})

    # Loop pelas pernas
    for i in range(N):
        frm,to=points[i],points[i+1]
        alt0,alt1=A_target[i],A_target[i+1]
        dist_nm=50.0  # valor simbólico, normalmente calculado de TC/dist real
        tc=90.0

        # Média ponderada TAS (exemplo simples)
        if alt1>alt0+50:
            tas_mean=(vy_kt+st.session_state.cruise_ref_kt)/2
            ff_mean=(ff_climb+ff_cruise)/2
            phase="CLIMB"
        elif alt1<alt0-50:
            tas_mean=(st.session_state.cruise_ref_kt+st.session_state.descent_ref_kt)/2
            ff_mean=(ff_cruise+ff_descent)/2
            phase="DESCENT"
        else:
            tas_mean=st.session_state.cruise_ref_kt
            ff_mean=ff_cruise
            phase="CRUISE"

        gs=gs_phase(tc,tas_mean)
        ete_sec=(dist_nm/gs)*3600
        burn=ff_mean*(ete_sec/3600)
        efob=max(0.0,efob-burn)
        if clock: clock=add_seconds(clock,int(ete_sec))

        rows.append({
            "From":frm,"To":to,"Phase":phase,
            "Dist(nm)":fmt(dist_nm,"dist"),
            "TAS(kt)":fmt(tas_mean,"speed"),
            "GS(kt)":fmt(gs,"speed"),
            "ETE":fmt_time_human(int(ete_sec)),
            "Burn(L)":fmt(burn,"fuel"),
            "EFOB(L)":fmt(efob,"fuel")
        })

        seq_points.append({"name":_occ_label(to),"alt":alt1,"phase":phase,"ete_sec":ete_sec,"efob":efob})

        # HOLD se definido
        r=alts[i+1] if i+1<len(alts) else {}
        if bool(r.get("Hold")) and float(r.get("Hold_min",0))>0:
            hold_min=float(r["Hold_min"])
            hold_sec=hold_min*60
            hold_burn=st.session_state.hold_ff_lph*(hold_sec/3600)
            efob=max(0.0,efob-hold_burn)
            if clock: clock=add_seconds(clock,int(hold_sec))
            rows.append({
                "From":to,"To":f"HOLD @{to}","Phase":"HOLD",
                "Dist(nm)":"","TAS(kt)":fmt(st.session_state.hold_ref_kt,"speed"),
                "GS(kt)":"","ETE":fmt_time_human(int(hold_sec)),
                "Burn(L)":fmt(hold_burn,"fuel"),
                "EFOB(L)":fmt(efob,"fuel")
            })
            seq_points.append({"name":_occ_label(f"HOLD @{to}"),"phase":"HOLD","ete_sec":hold_sec,"efob":efob})
# =========================================================
# RESUMO FINAL / OBSERVAÇÕES
# =========================================================
if rows:
    st.subheader("Plano Final — Legs & HOLDs")
    import pandas as pd
    df=pd.DataFrame(rows)

    # Mostra tabela principal
    st.dataframe(df,use_container_width=True)

    # Totais
    total_dist=sum(float(r["Dist(nm)"] or 0) for r in rows)
    total_ete_sec=sum(seq["ete_sec"] for seq in seq_points[1:])
    total_burn=sum(float(r["Burn(L)"] or 0) for r in rows)
    cruise_time_sec=sum(seq["ete_sec"] for seq in seq_points if seq["phase"] in ("CRUISE","HOLD"))
    cruise_burn=sum(float(r["Burn(L)"] or 0) for r in rows if r["Phase"] in ("CRUISE","HOLD"))

    st.markdown("### Totais")
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Dist Total (NM)",fmt(total_dist,"dist"))
    c2.metric("ETE Total",fmt_time_human(int(total_ete_sec)))
    c3.metric("Fuel Total (L)",fmt(total_burn,"fuel"))
    c4.metric("Fuel Remanescente (L)",fmt(float(rows[-1]['EFOB(L)']),"fuel"))

    st.markdown("---")

    # Observações detalhadas (tempo padronizado)
    st.subheader("Observações / Sequência de Pontos")
    obs_rows=[]
    for s in seq_points:
        obs_rows.append({
            "Ponto":s["name"],
            "Fase":s.get("phase",""),
            "Alt(ft)":_round_alt(s.get("alt",0)),
            "ETE":fmt_time_human(int(s.get("ete_sec",0))),
            "EFOB(L)":fmt(s.get("efob",0),"fuel")
        })
    st.dataframe(pd.DataFrame(obs_rows),use_container_width=True)

    # =====================================================
    # PDF NAVLOG
    # =====================================================
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet

    def make_pdf():
        buf=io.BytesIO()
        doc=SimpleDocTemplate(buf,pagesize=A4)
        styles=getSampleStyleSheet()
        flow=[]
        flow.append(Paragraph(f"<b>NAVLOG — {st.session_state.callsign}</b>",styles["Title"]))
        flow.append(Spacer(1,6))
        flow.append(Paragraph(f"Lesson {st.session_state.lesson} — {st.session_state.student}",styles["Normal"]))
        flow.append(Paragraph(f"Departure {st.session_state.dept}  Arrival {st.session_state.arr}",styles["Normal"]))
        flow.append(Spacer(1,10))

        # Tabela NAVLOG
        data=[list(df.columns)]+df.values.tolist()
        table=Table(data,hAlign="LEFT")
        table.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),colors.lightgrey),
            ('GRID',(0,0),(-1,-1),0.25,colors.grey),
            ('FONTSIZE',(0,0),(-1,-1),8),
            ('ALIGN',(0,0),(-1,-1),'CENTER')
        ]))
        flow.append(table)
        flow.append(Spacer(1,12))

        # Observações (min / h)
        flow.append(Paragraph("<b>Observations</b>",styles["Heading3"]))
        obs_data=[list(obs_rows[0].keys())]+[list(o.values()) for o in obs_rows]
        t2=Table(obs_data,hAlign="LEFT")
        t2.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),colors.lightgrey),
            ('GRID',(0,0),(-1,-1),0.25,colors.grey),
            ('FONTSIZE',(0,0),(-1,-1),8),
            ('ALIGN',(0,0),(-1,-1),'CENTER')
        ]))
        flow.append(t2)

        doc.build(flow)
        buf.seek(0)
        return buf

    st.download_button("📄 Exportar PDF NAVLOG",data=make_pdf(),file_name=f"NAVLOG_{st.session_state.callsign}.pdf",mime="application/pdf")

else:
    st.info("Defina rota e altitudes para gerar o NAVLOG.")
