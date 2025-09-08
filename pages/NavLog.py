

# app.py — NAVLOG (pontos + legs) com TOC/TOD correto, AFM perf e export p/ "NAVLOG - FORM.pdf"
# Reqs: streamlit, pypdf, pytz

import streamlit as st
import datetime as dt
import pytz
import io, json, unicodedata, re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from math import sin, asin, radians, degrees, fmod

# ========================= PDF helpers =========================
try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, TextStringObject
    PYPDF_OK = True
except Exception:
    PYPDF_OK = False

def ascii_safe(x: str) -> str:
    return unicodedata.normalize("NFKD", str(x or "")).encode("ascii","ignore").decode("ascii")

def read_pdf_bytes(paths: List[str]) -> bytes:
    for p in paths:
        if Path(p).exists():
            return Path(p).read_bytes()
    raise FileNotFoundError(paths)

def get_fields_and_meta(template_bytes: bytes):
    reader = PdfReader(io.BytesIO(template_bytes))
    field_names, maxlens = set(), {}
    try:
        fd = reader.get_fields() or {}
        field_names |= set(fd.keys())
        for k,v in fd.items():
            ml = v.get("/MaxLen")
            if ml: maxlens[k] = int(ml)
    except: pass
    try:
        for page in reader.pages:
            if "/Annots" in page:
                for a in page["/Annots"]:
                    obj = a.get_object()
                    if obj.get("/T"):
                        nm = str(obj["/T"]); field_names.add(nm)
                        ml = obj.get("/MaxLen")
                        if ml: maxlens[nm] = int(ml)
    except: pass
    return field_names, maxlens

def fill_pdf(template_bytes: bytes, fields: dict) -> bytes:
    if not PYPDF_OK: raise RuntimeError("pypdf missing")
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()
    for p in reader.pages: writer.add_page(p)
    root = reader.trailer["/Root"]
    if "/AcroForm" not in root: raise RuntimeError("Template has no AcroForm")
    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    try:
        writer._root_object["/AcroForm"].update({
            NameObject("/NeedAppearances"): True,
            NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g")
        })
    except: pass
    str_fields = {k:(str(v) if v is not None else "") for k,v in fields.items()}
    for page in writer.pages:
        writer.update_page_form_field_values(page, str_fields)
    bio = io.BytesIO(); writer.write(bio); return bio.getvalue()

def put(out: dict, fieldset: set, key: str, value: str, maxlens: Dict[str,int]):
    if key in fieldset:
        s = "" if value is None else str(value)
        if key in maxlens and len(s) > maxlens[key]:
            s = s[:maxlens[key]]
        out[key] = s

# ========================= Wind & helpers =========================
def wrap360(x): x=fmod(x,360.0); return x+360 if x<0 else x
def angle_diff(a,b): return (a-b+180)%360-180
def wind_triangle(tc_deg,tas_kt,wind_from_deg,wind_kt):
    if tas_kt<=0: return 0.0,wrap360(tc_deg),0.0
    beta=radians(angle_diff(wind_from_deg,tc_deg))
    cross=wind_kt*sin(beta)                 # componente lateral
    head=wind_kt*(1-(sin(beta)**2))**0.5    # cos(beta)
    s=max(-1.0,min(1.0,cross/max(tas_kt,1e-9)))
    wca=degrees(asin(s))
    gs = tas_kt*(1 - s*s)**0.5 - head       # tas*cos(wca) - headwind
    th=wrap360(tc_deg+wca)
    return wca,th,max(0.0,gs)

def apply_var(true_deg,var_deg,east_is_negative=False):
    # East is least (−), West is best (+)
    return wrap360(true_deg - var_deg if east_is_negative else true_deg + var_deg)

def parse_hhmm(s:str):
    s=(s or "").strip()
    for fmt in ("%H:%M","%H%M"):
        try: return dt.datetime.strptime(s,fmt).time()
        except: pass
    return None
def add_minutes(t:dt.time,m:int):
    if not t: return None
    today=dt.date.today(); base=dt.datetime.combine(today,t)
    return (base+dt.timedelta(minutes=m)).time()

# ========================= AFM tables (650 kg) =========================
def clamp(v,lo,hi): return max(lo,min(hi,v))
def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

# EN-ROUTE ROC (flaps UP) @ 650 kg (AFM WH5-11)
ROC_ENROUTE = {
    0:{-25:981,0:835,25:704,50:586},  2000:{-25:870,0:726,25:597,50:481},
    4000:{-25:759,0:617,25:491,50:377},6000:{-25:648,0:509,25:385,50:273},
    8000:{-25:538,0:401,25:279,50:170},10000:{-25:428,0:294,25:174,50:66},
    12000:{-25:319,0:187,25:69,50:-37},14000:{-25:210,0:80,25:-35,50:-139},
}
ROC_FACTOR = 0.90  # AFM: "apply a factored rate of climb of 0.90"

# Vy (KIAS) @ 650 kg (en-route climb)
VY_ENROUTE = {0:67,2000:67,4000:67,6000:67,8000:67,10000:67,12000:67,14000:67}

# Cruise perf (PA → rpm → (KTAS, FF L/h)) (AFM WH5-12..13)
CRUISE={
    0:{1800:(82,15.3),1900:(89,17.0),2000:(95,18.7),2100:(101,20.7),2250:(110,24.6),2388:(118,26.9)},
    2000:{1800:(82,15.3),1900:(88,16.6),2000:(94,17.5),2100:(100,19.9),2250:(109,23.5)},
    4000:{1800:(81,15.1),1900:(88,16.2),2000:(94,17.5),2100:(100,19.2),2250:(108,22.4)},
    6000:{1800:(81,14.9),1900:(87,15.9),2000:(93,17.1),2100:(99,18.5),2250:(108,21.3)},
    8000:{1800:(81,14.9),1900:(86,15.6),2000:(92,16.7),2100:(98,18.0),2250:(107,20.4)},
    10000:{1800:(85,15.4),1900:(91,16.4),2000:(91,16.4),2100:(97,17.5),2250:(106,19.7)},
}
def isa_temp(pa_ft): return 15.0 - 2.0*(pa_ft/1000.0)

def cruise_lookup(pa_ft: float, rpm: int, oat_c: Optional[float]) -> Tuple[float,float]:
    pas=sorted(CRUISE.keys()); pa_c=clamp(pa_ft,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    def val(pa):
        table=CRUISE[pa]
        if rpm in table: return table[rpm]
        rpms=sorted(table.keys())
        if rpm<rpms[0]: lo,hi=rpms[0],rpms[1]
        elif rpm>rpms[-1]: lo,hi=rpms[-2],rpms[-1]
        else:
            lo=max([r for r in rpms if r<=rpm]); hi=min([r for r in rpms if r>=rpm])
        (tas_lo,ff_lo),(tas_hi,ff_hi)=table[lo],table[hi]
        t=(rpm-lo)/(hi-lo) if hi!=lo else 0.0
        return (tas_lo + t*(tas_hi-tas_lo), ff_lo + t*(ff_hi-ff_lo))
    tas0,ff0=val(p0); tas1,ff1=val(p1)
    tas=interp1(pa_c,p0,p1,tas0,tas1); ff=interp1(pa_c,p0,p1,ff0,ff1)
    # Correções OAT (AFM): +15°C → KTAS -2%, FF -2.5%; -15°C → KTAS +1%, FF +3%
    if oat_c is not None:
        dev=oat_c - isa_temp(pa_c)
        if dev>0:
            tas *= 1.0 - 0.02*(dev/15.0)
            ff  *= 1.0 - 0.025*(dev/15.0)
        elif dev<0:
            tas *= 1.0 + 0.01*((-dev)/15.0)
            ff  *= 1.0 + 0.03*((-dev)/15.0)
    return max(0.0,tas), max(0.0,ff)

def roc_interp_enroute(pa, temp_c):
    pas=sorted(ROC_ENROUTE.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    temps=[-25,0,25,50]; t=clamp(temp_c,temps[0],temps[-1])
    if t<=0: t0,t1=-25,0
    elif t<=25: t0,t1=0,25
    else: t0,t1=25,50
    v00, v01 = ROC_ENROUTE[p0][t0], ROC_ENROUTE[p0][t1]
    v10, v11 = ROC_ENROUTE[p1][t0], ROC_ENROUTE[p1][t1]
    v0 = interp1(t, t0, t1, v00, v01); v1 = interp1(t, t0, t1, v10, v11)
    roc = interp1(pa_c, p0, p1, v0, v1)
    return max(1.0, roc * ROC_FACTOR)  # aplica 0.90 do AFM

def vy_interp_enroute(pa):
    table=VY_ENROUTE; pas=sorted(table.keys())
    pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    return interp1(pa_c, p0, p1, table[p0], table[p1])  # kt ref (tratado como TAS p/ GS)

# ========================= Aerodromes =========================
AEROS={
 "LPSO":{"elev":390,"freq":"119.805"},
 "LPEV":{"elev":807,"freq":"122.705"},
 "LPCB":{"elev":1251,"freq":"122.300"},
 "LPCO":{"elev":587,"freq":"118.405"},
 "LPVZ":{"elev":2060,"freq":"118.305"},
}
def aero_elev(icao): return int(AEROS.get(icao,{}).get("elev",0))
def aero_freq(icao): return AEROS.get(icao,{}).get("freq","")

# ========================= App UI =========================
st.set_page_config(page_title="NAVLOG", layout="wide", initial_sidebar_state="collapsed")
st.title("Navigation Plan & Inflight Log — Tecnam P2008")

DEFAULT_STUDENT="AMOIT"; DEFAULT_AIRCRAFT="P208"; DEFAULT_CALLSIGN="RVP"
REGS=["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"]
PDF_TEMPLATE_PATHS=["NAVLOG - FORM.pdf"]  # PDF ao lado do app.py

# Header
c1,c2,c3=st.columns(3)
with c1:
    aircraft=st.text_input("Aircraft",DEFAULT_AIRCRAFT)
    registration=st.selectbox("Registration",REGS,index=0)
    callsign=st.text_input("Callsign",DEFAULT_CALLSIGN)
with c2:
    student=st.text_input("Student",DEFAULT_STUDENT)
    lesson = st.text_input("Lesson","")
    instrutor = st.text_input("Instrutor","")   # <- nome do campo no PDF
with c3:
    dept=st.selectbox("Departure",list(AEROS.keys()),index=0)
    arr =st.selectbox("Arrival", list(AEROS.keys()),index=1)
    altn=st.selectbox("Alternate",list(AEROS.keys()),index=2)

startup_str=st.text_input("Startup (HH:MM)","")

# Atmosfera & navegação
c4,c5,c6=st.columns(3)
with c4:
    qnh=st.number_input("QNH (hPa)",900,1050,1013,step=1)
    cruise_alt=st.number_input("Cruise Altitude (ft)",0,14000,3000,step=100)
    initial_alt=st.number_input("Altitude inicial (ft AMSL)",0,20000,0,step=50,
                                help="Se 0, usa a elevação do DEP; útil p/ sair de circuito a 1400 ft, etc.")
with c5:
    temp_c=st.number_input("OAT (°C)",-40,50,15,step=1)
    var_deg=st.number_input("Mag Variation (°)",0,30,1,step=1)
    var_is_e=(st.selectbox("E/W",["W","E"],index=0)=="E")
with c6:
    wind_from=st.number_input("Wind FROM (°TRUE)",0,360,0,step=1)
    wind_kt=st.number_input("Wind (kt)",0,120,0,step=1)
    target_arr_alt=st.number_input("Altitude alvo na chegada (ft AMSL)",0,20000,0,step=50,
                                   help="Normalmente a elevação do ARR; ajusta se necessário.")

# Performance / Consumos (sempre AFM)
c7,c8,c9=st.columns(3)
with c7:
    rpm_climb  = st.number_input("Climb RPM (AFM)",1800,2388,2250,step=10)
    rpm_cruise = st.number_input("Cruise RPM (AFM)",1800,2388,2000,step=10)
with c8:
    rpm_descent= st.number_input("Descent RPM (se NÃO idle)",1700,2300,1800,step=10)
    idle_mode  = st.checkbox("Descent mostly IDLE", value=True)
with c9:
    rod_fpm=st.number_input("ROD (ft/min)",200,1500,700,step=10)
    idle_ff=st.number_input("Idle FF (L/h) (if IDLE)", 0.0, 20.0, 5.0, step=0.1)
    start_fuel=st.number_input("Fuel inicial (EFOB_START) [L]",0.0,1000.0,0.0,step=1.0)

# Velocidades ref (para GS/tempo)
cruise_ref_kt = st.number_input("Cruise speed (kt)", 40, 140, 80, step=1)
descent_ref_kt= st.number_input("Descent speed (kt)", 40, 120, 65, step=1)

# ===== ROUTE INPUT (textarea) =====
def parse_route_text(txt:str) -> List[str]:
    tokens = re.split(r"[,\s→\-]+", (txt or "").strip())
    return [t for t in tokens if t]

st.markdown("#### Route (DEP … ARR)")
default_route = f"{dept} {arr}"
route_text = st.text_area("Pontos (separados por espaço, vírgulas ou '->')",
                          value=st.session_state.get("route_text", default_route))
apply_route = st.button("Aplicar rota")

if "points" not in st.session_state:
    st.session_state.points = [dept, arr]

if apply_route:
    pts = parse_route_text(route_text)
    if len(pts) < 2: pts = [dept, arr]
    st.session_state.points = pts
    st.session_state.route_text = " ".join(pts)

points = st.session_state.points
# manter DEP/ARR em sincronia com os selects
if points: points[0] = dept
if len(points)>=2: points[-1] = arr

# ===== LEGS (deduzidos dos points) =====
def blank_leg(): return {"From":"","To":"","TC":0.0,"Dist":0.0}
if "legs" not in st.session_state: st.session_state.legs = []

target_legs = max(0,len(points)-1)
legs = st.session_state.legs
if len(legs) < target_legs: legs += [blank_leg() for _ in range(target_legs - len(legs))]
elif len(legs) > target_legs: legs = legs[:target_legs]
for i in range(target_legs):
    legs[i]["From"]=points[i]; legs[i]["To"]=points[i+1]
st.session_state.legs = legs

st.markdown("#### Legs (distância **do ponto anterior**)")
legs_cfg = {
    "From": st.column_config.TextColumn("From", disabled=True),
    "To":   st.column_config.TextColumn("To", disabled=True),
    "TC":   st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
    "Dist": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
}
legs_view = st.data_editor(legs, hide_index=True, use_container_width=True,
                           column_config=legs_cfg, num_rows="fixed", key="legs_table")
for i,row in enumerate(legs_view):
    legs[i]["TC"]  = float(row.get("TC") or 0.0)
    legs[i]["Dist"]= float(row.get("Dist") or 0.0)

N = len(legs)  # nº de legs sem TOC/TOD

# ===== Cálculos (TOC/TOD + consumos AFM) =====
def pressure_alt(alt_ft, qnh_hpa): return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0

dep_elev = aero_elev(dept); arr_elev = aero_elev(arr)
start_alt = float(initial_alt) if initial_alt>0 else float(dep_elev)
end_alt   = float(target_arr_alt) if target_arr_alt>0 else float(arr_elev)
pa_start  = pressure_alt(start_alt, qnh); pa_cruise = pressure_alt(cruise_alt, qnh); pa_arr=pressure_alt(end_alt,qnh)

vy_kt = vy_interp_enroute(pa_start)           # ← Vy do AFM (en-route)
tas_climb   = vy_kt                           # para GS/tempo
tas_cruise  = float(cruise_ref_kt)
tas_descent = float(descent_ref_kt)

tc_first = float(legs[0]["TC"]) if N>0 else 0.0
tc_last  = float(legs[-1]["TC"]) if N>0 else 0.0

roc = roc_interp_enroute(pa_start, temp_c)    # ft/min (com fator 0.90)
delta_climb = max(0.0, cruise_alt - start_alt)
climb_min = delta_climb / max(roc,1e-6)
_,_, gs_climb = wind_triangle(tc_first, tas_climb, wind_from, wind_kt)
climb_nm = gs_climb * (climb_min/60.0)

delta_desc = max(0.0, cruise_alt - end_alt)
desc_min = delta_desc / max(rod_fpm,1e-6)
_,_, gs_desc = wind_triangle(tc_last, tas_descent, wind_from, wind_kt)
desc_nm = gs_desc * (desc_min/60.0)

# Consumos AFM (com correções OAT)
pa_mid_climb = start_alt + 0.5*delta_climb
pa_mid_desc  = end_alt   + 0.5*delta_desc
_, ff_climb  = cruise_lookup(pa_mid_climb, int(rpm_climb),  temp_c)
_, ff_cruise = cruise_lookup(pa_cruise,   int(rpm_cruise),  temp_c)
ff_descent = float(idle_ff) if idle_mode else cruise_lookup(pa_mid_desc, int(rpm_descent), temp_c)[1]

# distribuir climb/desc pelos legs
base_dists = [float(l["Dist"] or 0.0) for l in legs]
climb_consumed = [0.0]*N; rem=float(climb_nm)
for i,d in enumerate(base_dists):
    use=min(d,max(0.0,rem)); climb_consumed[i]=use; rem-=use
desc_consumed = [0.0]*N; rem=float(desc_nm)
for j in range(N-1,-1,-1):
    d=base_dists[j]; use=min(d,max(0.0,rem)); desc_consumed[j]=use; rem-=use

# localizar TOC/TOD
idx_toc = next((i for i,u in enumerate(climb_consumed) if sum(climb_consumed[:i+1])>=climb_nm-1e-6 and u>0), None)
idx_tod = None; acc=0.0
for j in range(N-1,-1,-1):
    acc += desc_consumed[j]
    if desc_consumed[j]>0.0 and acc>=desc_nm-1e-6:
        idx_tod=j; break

# sequência final para UI/PDF
final_rows=[]
for i,L in enumerate(legs):
    final_rows.append({"kind":"LEG","i":i,"TC":L["TC"],"Dist":L["Dist"],"From":L["From"],"To":L["To"]})
    if idx_toc is not None and i==idx_toc: final_rows.append({"kind":"TOC","i":i,"TC":L["TC"],"To":L["To"]})
    if idx_tod is not None and i==idx_tod: final_rows.append({"kind":"TOD","i":i,"TC":L["TC"],"To":L["To"]})

startup = parse_hhmm(startup_str)
takeoff = add_minutes(startup,15) if startup else None
clock = takeoff

def min_int(x: float) -> int: return int(round(x))

total_dist = sum(base_dists); total_ete = total_burn = 0.0; efob=float(start_fuel)
calc_rows=[]
for row in final_rows:
    kind=row["kind"]
    if kind=="LEG":
        i=row["i"]; tc=row["TC"]; dist=row["Dist"]; from_nm=row["From"]; to_nm=row["To"]
        eff=max(0.0, float(dist) - climb_consumed[i] - desc_consumed[i])
        tas=tas_cruise; phase_ff=ff_cruise
        _, th, gs = wind_triangle(float(tc), float(tas), wind_from, wind_kt)
        mh=apply_var(th, var_deg, var_is_e)
        ete_min=(60.0*eff/max(gs,1e-6)) if eff>0 else 0.0; burn=phase_ff*(ete_min/60.0)
        eto_str=""
        if clock: clock=add_minutes(clock,min_int(ete_min)); eto_str=clock.strftime("%H:%M")
        total_ete+=ete_min; total_burn+=burn; efob=max(0.0, efob-burn)
        calc_rows.append({
            "Leg": f"{from_nm}→{to_nm}", "ToName": to_nm,
            "CruiseALT": str(int(round(cruise_alt))),
            "TC":round(tc,0),"TH":round(th,0),"MH":round(mh,0),
            "TAS":round(tas,0),"GS":round(gs,0),"FF":round(phase_ff,1),
            "Dist":round(float(dist),1),"ETE":min_int(ete_min),"ETO":eto_str,
            "Burn":round(burn,1),"EFOB":round(efob,1),
        })
    elif kind=="TOC":
        to_nm=row["To"]; tc=row["TC"]; tas=tas_climb; phase_ff=ff_climb
        _, th, gs = wind_triangle(float(tc), float(tas), wind_from, wind_kt)
        mh=apply_var(th, var_deg, var_is_e)
        ete_min=climb_min; burn=phase_ff*(ete_min/60.0)
        eto_str=""
        if clock: clock=add_minutes(clock,min_int(ete_min)); eto_str=clock.strftime("%H:%M")
        total_ete+=ete_min; total_burn+=burn; efob=max(0.0, efob-burn)
        calc_rows.append({
            "Leg":"TOC","ToName":"TOC","CruiseALT":str(int(round(cruise_alt))),
            "TC":round(tc,0),"TH":round(th,0),"MH":round(mh,0),
            "TAS":round(tas,0),"GS":round(gs,0),"FF":round(phase_ff,1),
            "Dist":0.0,"ETE":min_int(ete_min),"ETO":eto_str,"Burn":round(burn,1),"EFOB":round(efob,1),
        })
    elif kind=="TOD":
        to_nm=row["To"]; tc=row["TC"]; tas=tas_descent; phase_ff=ff_descent
        _, th, gs = wind_triangle(float(tc), float(tas), wind_from, wind_kt)
        mh=apply_var(th, var_deg, var_is_e)
        ete_min=desc_min; burn=phase_ff*(ete_min/60.0)
        eto_str=""
        if clock: clock=add_minutes(clock,min_int(ete_min)); eto_str=clock.strftime("%H:%M")
        total_ete+=ete_min; total_burn+=burn; efob=max(0.0, efob-burn)
        calc_rows.append({
            "Leg":"TOD","ToName":"TOD","CruiseALT":str(int(round(end_alt))),
            "TC":round(tc,0),"TH":round(th,0),"MH":round(mh,0),
            "TAS":round(tas,0),"GS":round(gs,0),"FF":round(phase_ff,1),
            "Dist":0.0,"ETE":min_int(ete_min),"ETO":eto_str,"Burn":round(burn,1),"EFOB":round(efob,1),
        })

eta = clock; landing = eta; shutdown = add_minutes(eta,5) if eta else None

# ===== Tabela (sem Freq; com FF L/h). 'Leg' (A→B) e 'ToName' (para PDF Name)
st.markdown("#### Flight plan (TOC/TOD auto)")
column_config={
    "Leg":   st.column_config.TextColumn("Leg / Marker"),
    "ToName":st.column_config.TextColumn("To (Name)", disabled=True),
    "CruiseALT": st.column_config.TextColumn("Cruise ALT (ref)"),
    "TC":     st.column_config.NumberColumn("TC (°T)", disabled=True),
    "TH":     st.column_config.NumberColumn("TH (°T)", disabled=True),
    "MH":     st.column_config.NumberColumn("MH (°M)", disabled=True),
    "TAS":    st.column_config.NumberColumn("TAS (kt)", disabled=True),
    "GS":     st.column_config.NumberColumn("GS (kt)", disabled=True),
    "FF":     st.column_config.NumberColumn("FF (L/h)", disabled=True),
    "Dist":   st.column_config.NumberColumn("Dist (nm)", disabled=True),
    "ETE":    st.column_config.NumberColumn("ETE (min)", disabled=True),
    "ETO":    st.column_config.TextColumn("ETO", disabled=True),
    "Burn":   st.column_config.NumberColumn("Burn (L)", disabled=True),
    "EFOB":   st.column_config.NumberColumn("EFOB (L)", disabled=True),
}
st.data_editor(calc_rows, hide_index=True, use_container_width=True,
               num_rows="fixed", column_config=column_config, key="calc_table_readonly")

tot_line = f"**Totais** — Dist {total_dist:.1f} nm • ETE {int(total_ete)//60}h{int(total_ete)%60:02d} • Burn {total_burn:.1f} L • EFOB {efob:.1f} L"
if eta:
    tot_line += f" • **ETA {eta.strftime('%H:%M')}** • **Landing {landing.strftime('%H:%M')}** • **Shutdown {shutdown.strftime('%H:%M')}**"
st.markdown(tot_line)

# ===== Guardar / Carregar plano (JSON) =====
st.markdown("### Flight plan — guardar / carregar (JSON)")
def snapshot_plan() -> dict:
    return {
        "meta": {"aircraft": aircraft, "registration": registration, "callsign": callsign,
                 "student": student, "lesson": lesson, "instrutor": instrutor},
        "route_points": points,
        "legs": [{"TC":l["TC"], "Dist":l["Dist"]} for l in legs],
        "atmo_nav": {"qnh": qnh, "cruise_alt": cruise_alt, "initial_alt": initial_alt,
                     "target_arr_alt": target_arr_alt, "temp_c": temp_c,
                     "var_deg": var_deg, "var_is_e": var_is_e,
                     "wind_from": wind_from, "wind_kt": wind_kt,
                     "cruise_ref_kt": cruise_ref_kt, "descent_ref_kt": descent_ref_kt},
        "perf": {"rpm_climb": rpm_climb, "rpm_cruise": rpm_cruise,
                 "rpm_descent": rpm_descent, "idle_mode": idle_mode,
                 "rod_fpm": rod_fpm, "idle_ff": idle_ff},
        "times_fuel": {"startup": startup_str, "start_fuel": start_fuel},
    }
def apply_plan(data: dict):
    st.session_state.points = list(data.get("route_points") or [dept, arr])
    src_legs = data.get("legs") or []
    target = max(0,len(st.session_state.points)-1)
    st.session_state.legs = []
    for i in range(target):
        tc = float(src_legs[i]["TC"]) if i < len(src_legs) and "TC" in src_legs[i] else 0.0
        di = float(src_legs[i]["Dist"]) if i < len(src_legs) and "Dist" in src_legs[i] else 0.0
        st.session_state.legs.append({"From":st.session_state.points[i],
                                      "To":st.session_state.points[i+1],
                                      "TC":tc,"Dist":di})
    st.session_state["route_text"] = " ".join(st.session_state.points)
    # devolvo valores para repor widgets (guardados em session_state)
    state = {}
    for grp in ("atmo_nav","perf","times_fuel","meta"):
        if grp in data:
            for k,v in data[grp].items(): state[k]=v
    return state

plan_json = json.dumps(snapshot_plan(), ensure_ascii=False, indent=2)
st.download_button("💾 Download plano (JSON)", data=plan_json.encode("utf-8"),
                   file_name=f"navlog_{ascii_safe(registration)}.json",
                   mime="application/json")
uploaded = st.file_uploader("📤 Carregar plano (JSON)", type=["json"])
if uploaded is not None:
    try:
        data = json.loads(uploaded.read().decode("utf-8"))
        restored = apply_plan(data)
        for k,v in restored.items(): st.session_state[k]=v
        st.success("Plano carregado.")
        st.experimental_rerun()
    except Exception as e:
        st.error(f"Falha a carregar JSON: {e}")

# ====== PDF export (+ auxiliar para listar campos) ======
st.markdown("### PDF export")
show_fields = st.checkbox("Mostrar nomes de campos do PDF (debug)")

try:
    template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
except Exception as e:
    template_bytes = None
    st.error(f"Não foi possível ler o PDF: {e}")

if template_bytes:
    fieldset, maxlens = get_fields_and_meta(template_bytes)
    if show_fields:
        st.code("\n".join(sorted(fieldset)))
    try:
        named: Dict[str,str] = {}
        # Cabeçalho
        put(named, fieldset, "Aircraft", aircraft, maxlens)
        put(named, fieldset, "Registration", registration, maxlens)
        put(named, fieldset, "Callsign", callsign, maxlens)
        put(named, fieldset, "Student", student, maxlens)
        put(named, fieldset, "Lesson", lesson, maxlens)
        put(named, fieldset, "Instrutor", instrutor, maxlens)   # <- nome correto

        put(named, fieldset, "Dept_Airfield", dept, maxlens)
        put(named, fieldset, "Arrival_Airfield", arr, maxlens)
        put(named, fieldset, "Alternate", altn, maxlens)
        put(named, fieldset, "Alt_Alternate", str(aero_elev(altn)), maxlens)

        put(named, fieldset, "Dept_Comm", aero_freq(dept), maxlens)
        put(named, fieldset, "Arrival_comm", aero_freq(arr), maxlens)
        put(named, fieldset, "Enroute_comm", "123.755", maxlens)

        isa_dev = round(temp_c - isa_temp(pressure_alt(aero_elev(dept), qnh)))
        put(named, fieldset, "QNH", f"{int(round(qnh))}", maxlens)
        put(named, fieldset, "temp_isa_dev", f"{int(round(temp_c))} / {isa_dev}", maxlens)
        put(named, fieldset, "wind", f"{int(round(wind_from)):03d}/{int(round(wind_kt)):02d}", maxlens)
        put(named, fieldset, "mag_var", f"{var_deg:.1f}{'E' if var_is_e else 'W'}", maxlens)
        put(named, fieldset, "flt_lvl_altitude", f"{int(round(cruise_alt))}", maxlens)

        # Horas
        takeoff = add_minutes(parse_hhmm(startup_str),15) if startup_str else None
        last_eto = calc_rows[-1]["ETO"] if calc_rows and calc_rows[-1]["ETO"] else ""
        put(named, fieldset, "Startup", startup_str, maxlens)
        put(named, fieldset, "Takeoff", takeoff.strftime("%H:%M") if takeoff else "", maxlens)
        put(named, fieldset, "Landing", last_eto, maxlens)
        put(named, fieldset, "Shutdown", "", maxlens)
        put(named, fieldset, "ETD/ETA", f"{(takeoff.strftime('%H:%M') if takeoff else '')} / {last_eto}", maxlens)

        # Nº de legs (sem TOC/TOD)
        put(named, fieldset, "Leg_Number", str(N), maxlens)

        # Linhas (Name = destino do leg; TOC/TOD literal)
        for i, r in enumerate(calc_rows[:11], start=1):
            s=str(i)
            put(named, fieldset, f"Name{s}", r["ToName"], maxlens)  # <- só o ponto "To"
            put(named, fieldset, f"Alt{s}",  r["CruiseALT"], maxlens)
            put(named, fieldset, f"FREQ{s}", "", maxlens)
            put(named, fieldset, f"TCRS{s}", f"{int(round(float(r['TC'])))}", maxlens)
            put(named, fieldset, f"THDG{s}", f"{int(round(float(r['TH'])))}", maxlens)
            put(named, fieldset, f"MHDG{s}", f"{int(round(float(r['MH'])))}", maxlens)
            put(named, fieldset, f"TAS{s}",  f"{int(round(float(r['TAS'])))}", maxlens)
            put(named, fieldset, f"GS{s}",   f"{int(round(float(r['GS'])))}", maxlens)
            put(named, fieldset, f"Dist{s}", f"{r['Dist']}", maxlens)
            put(named, fieldset, f"ETE{s}",  f"{int(round(float(r['ETE'])))}", maxlens)
            put(named, fieldset, f"ETO{s}",  r["ETO"], maxlens)
            put(named, fieldset, f"PL_BO{s}", f"{r['Burn']}", maxlens)
            put(named, fieldset, f"EFOB{s}",  f"{r['EFOB']}", maxlens)

        # Totais
        put(named, fieldset, "ETE_Total", f"{int(round(total_ete))}", maxlens)
        put(named, fieldset, "Dist_Total", f"{total_dist:.1f}", maxlens)
        put(named, fieldset, "PL_BO_TOTAL", f"{total_burn:.1f}", maxlens)
        put(named, fieldset, "EFOB_TOTAL", f"{efob:.1f}", maxlens)

        if st.button("Gerar PDF preenchido", type="primary"):
            out = fill_pdf(template_bytes, named)
            safe_reg = ascii_safe(registration)
            safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
            filename = f"{safe_date}_{safe_reg}_NAVLOG.pdf"
            st.download_button("Download PDF", data=out, file_name=filename, mime="application/pdf")
            st.success("PDF gerado. Revê antes do voo.")
    except Exception as e:
        st.error(f"Erro ao preparar/gerar PDF: {e}")

