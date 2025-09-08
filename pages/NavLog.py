# app.py — NAVLOG completo (AFM perf + PDF NAVLOG)
# Reqs: streamlit, pytz, pypdf

import streamlit as st
import datetime as dt
import pytz
from pathlib import Path
import io, unicodedata
from math import sin, cos, asin, radians, degrees, fmod
from typing import Dict, List, Optional, Tuple

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
        if Path(p).exists(): return Path(p).read_bytes()
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
    if "/AcroForm" not in root: raise RuntimeError("No AcroForm in template")
    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    try:
        writer._root_object["/AcroForm"].update({
            NameObject("/NeedAppearances"): True,
            NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g")
        })
    except: pass
    str_fields={k:(str(v) if v is not None else "") for k,v in fields.items()}
    for page in writer.pages:
        writer.update_page_form_field_values(page, str_fields)
    bio=io.BytesIO(); writer.write(bio); return bio.getvalue()

def put_any(out: dict, fieldset: set, keys, value: str, maxlens: Dict[str,int]=None):
    if isinstance(keys,str): keys=[keys]
    for k in keys:
        if k in fieldset:
            s="" if value is None else str(value)
            if maxlens and k in maxlens and len(s)>maxlens[k]:
                s=s[:maxlens[k]]
            out[k]=s

# ========================= Wind & helpers =========================
def wrap360(x): x=fmod(x,360.0); return x+360 if x<0 else x
def angle_diff(a,b): return (a-b+180)%360-180
def wind_triangle(tc_deg,tas_kt,wind_from_deg,wind_kt):
    if tas_kt<=0: return 0.0,wrap360(tc_deg),0.0
    beta=radians(angle_diff(wind_from_deg,tc_deg))
    cross=wind_kt*sin(beta); head=wind_kt*cos(beta)
    s=max(-1.0,min(1.0,cross/max(tas_kt,1e-9)))
    wca=degrees(asin(s)); gs=tas_kt*cos(radians(wca))-head
    th=wrap360(tc_deg+wca)
    return wca,th,max(0.0,gs)
def apply_var(true_deg,var_deg,east_is_negative=False):
    return wrap360(true_deg - var_deg if east_is_negative else true_deg + var_deg)

def parse_hhmm(s:str): 
    for fmt in("%H:%M","%H%M"):
        try: return dt.datetime.strptime(s,fmt).time()
        except: pass
    return None
def add_minutes(t:dt.time,m:int):
    if not t: return None
    today=dt.date.today(); base=dt.datetime.combine(today,t)
    return (base+dt.timedelta(minutes=m)).time()

# ========================= AFM tables =========================
def clamp(v,lo,hi): return max(lo,min(hi,v))
def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

# Cruise performance (simplified excerto)
CRUISE={ # PA: rpm→(tas,ff)
 0:{2000:(95,18.7),2100:(101,20.7),2250:(110,24.6)},
2000:{2000:(94,17.5),2100:(100,19.9),2250:(109,23.5)},
4000:{2000:(94,17.5),2100:(100,19.2),2250:(108,22.4)},
6000:{2000:(93,17.1),2100:(99,18.5),2250:(108,21.3)},
}
def isa_temp(pa_ft): return 15-2*(pa_ft/1000)
def cruise_lookup(pa,rpm,oat):
    pas=sorted(CRUISE.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    def val(pa): 
        t=CRUISE[pa]; return t.get(rpm,t[min(t.keys())])
    tas0,ff0=val(p0); tas1,ff1=val(p1)
    tas=interp1(pa_c,p0,p1,tas0,tas1); ff=interp1(pa_c,p0,p1,ff0,ff1)
    if oat is not None: # corr OAT simplificada
        dev=oat-isa_temp(pa_c)
        tas*=(1-0.02*(dev/15.0)); ff*=(1-0.025*(dev/15.0))
    return tas,ff

# ========================= Aerodromes =========================
AEROS={
 "LPSO":{"elev":390,"freq":"119.805"},
 "LPEV":{"elev":807,"freq":"122.705"},
 "LPCB":{"elev":1251,"freq":"122.300"},
 "LPCO":{"elev":587,"freq":"118.405"},
 "LPVZ":{"elev":2060,"freq":"118.305"},
}
def aero_elev(icao): return AEROS.get(icao,{}).get("elev",0)
def aero_freq(icao): return AEROS.get(icao,{}).get("freq","")

# ========================= App UI =========================
st.set_page_config(page_title="NAVLOG",layout="wide",initial_sidebar_state="collapsed")
st.title("Navigation Log – Tecnam P2008")

DEFAULT_STUDENT="AMOIT"; DEFAULT_AIRCRAFT="P208"
REGS=["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"]

c1,c2,c3=st.columns(3)
with c1:
    aircraft=st.text_input("Aircraft",DEFAULT_AIRCRAFT)
    registration=st.selectbox("Registration",REGS)
    callsign=st.text_input("Callsign","RVP")
with c2:
    student=st.text_input("Student",DEFAULT_STUDENT)
    dept=st.selectbox("Departure",list(AEROS.keys()),index=0)
    arr=st.selectbox("Arrival",list(AEROS.keys()),index=1)
with c3:
    altn=st.selectbox("Alternate",list(AEROS.keys()),index=2)
    startup_str=st.text_input("Startup (HH:MM)","")

c4,c5,c6=st.columns(3)
with c4: qnh=st.number_input("QNH",900,1050,1013)
with c5: temp=st.number_input("OAT (°C)",-40,50,15)
with c6: weight=st.number_input("Weight (kg)",500,650,650)

c7,c8,c9=st.columns(3)
with c7: wind_dir=st.number_input("Wind FROM °T",0,360,0)
with c8: wind_kt=st.number_input("Wind kt",0,100,0)
with c9:
    var_deg=st.number_input("Variation (°)",0,30,1)
    var_is_e=(st.selectbox("E/W",["W","E"],index=0)=="E")

# Cruise
cruise_alt=st.number_input("Cruise Alt (ft)",0,14000,3000)
rpm=st.number_input("Cruise RPM",1800,2300,2000)

# ===== Legs table =====
N=st.number_input("N legs",1,10,3)
if "legs" not in st.session_state: 
    st.session_state.legs=[{"Name":"","Alt/FL":"","Freq":"","TC":0.0,"Dist":0.0} for _ in range(N)]
cur=st.session_state.legs
if len(cur)!=N:
    cur+=[{"Name":"","Alt/FL":"","Freq":"","TC":0.0,"Dist":0.0} for _ in range(N-len(cur))]
    cur=cur[:N]; st.session_state.legs=cur

# force dep/arr data
cur[0]["Name"]=dept; cur[0]["Alt/FL"]=f"{aero_elev(dept)} ft"; cur[0]["Freq"]=aero_freq(dept)
cur[-1]["Name"]=arr; cur[-1]["Alt/FL"]=f"{aero_elev(arr)} ft"; cur[-1]["Freq"]=aero_freq(arr)

legs=st.data_editor(cur,hide_index=True,use_container_width=True,key="legs_ed")

# ===== Calc =====
startup=parse_hhmm(startup_str); takeoff=add_minutes(startup,15) if startup else None
clock=takeoff; efob=100.0 # assume fuel inicial
calc=[]
total_dist=total_ete=total_burn=0.0
for r in legs:
    name=r["Name"]; tc=float(r.get("TC") or 0); dist=float(r.get("Dist") or 0)
    pa=aero_elev(name)+(1013-qnh)*30
    tas,ff=cruise_lookup(pa,rpm,temp)
    wca,th,gs=wind_triangle(tc,tas,wind_dir,wind_kt)
    mh=apply_var(th,var_deg,var_is_e)
    ete=60*dist/max(gs,1e-6) if dist>0 else 0
    burn=ff*(ete/60.0)
    total_dist+=dist; total_ete+=ete; total_burn+=burn; efob-=burn
    eto=""; 
    if clock: 
        clock=add_minutes(clock,int(round(ete))); eto=clock.strftime("%H:%M")
    calc.append({"Name":name,"Alt":r["Alt/FL"],"Freq":r["Freq"],
                 "TC":f"{tc:.0f}","TH":f"{th:.0f}","MH":f"{mh:.0f}",
                 "GS":f"{gs:.0f}","Dist":f"{dist:.1f}","ETE":f"{ete:.0f}",
                 "ETO":eto,"Burn":f"{burn:.1f}","EFOB":f"{efob:.1f}"})

eta=clock; landing=eta; shutdown=add_minutes(eta,5) if eta else None

st.write(f"Tot Dist {total_dist:.1f} nm • ETE {total_ete:.0f} min • Burn {total_burn:.1f} L")
if eta: st.write(f"ETA {eta.strftime('%H:%M')} • Landing {landing.strftime('%H:%M')} • Shutdown {shutdown.strftime('%H:%M')}")

# ===== PDF =====
PDF_TEMPLATE_PATHS=["/mnt/data/NAVLOG - FORM.pdf","NAVLOG - FORM.pdf"]
try:
    template=read_pdf_bytes(PDF_TEMPLATE_PATHS)
    fields,maxlens=get_fields_and_meta(template); named={}
    put_any(named,fields,"Aircraft",aircraft,maxlens)
    put_any(named,fields,"Registration",registration,maxlens)
    put_any(named,fields,"Callsign",callsign,maxlens)
    put_any(named,fields,"Student",student,maxlens)
    put_any(named,fields,"Departure",dept,maxlens)
    put_any(named,fields,"Arrival",arr,maxlens)
    put_any(named,fields,"Alternate",altn,maxlens)
    put_any(named,fields,"Startup",startup_str,maxlens)
    if takeoff: put_any(named,fields,"Takeoff",takeoff.strftime("%H:%M"),maxlens)
    if landing: put_any(named,fields,"Landing",landing.strftime("%H:%M"),maxlens)
    if shutdown: put_any(named,fields,"Shutdown",shutdown.strftime("%H:%M"),maxlens)
    for i,r in enumerate(calc,1):
        put_any(named,fields,f"Name{i}",r["Name"],maxlens)
        put_any(named,fields,f"Alt{i}",r["Alt"],maxlens)
        put_any(named,fields,f"FREQ{i}",r["Freq"],maxlens)
        put_any(named,fields,f"TCRS{i}",r["TC"],maxlens)
        put_any(named,fields,f"THDG{i}",r["TH"],maxlens)
        put_any(named,fields,f"MHDG{i}",r["MH"],maxlens)
        put_any(named,fields,f"GS{i}",r["GS"],maxlens)
        put_any(named,fields,f"Dist{i}",r["Dist"],maxlens)
        put_any(named,fields,f"ETE{i}",r["ETE"],maxlens)
        put_any(named,fields,f"ETO{i}",r["ETO"],maxlens)
        put_any(named,fields,f"PL_BO{i}",r["Burn"],maxlens)
        put_any(named,fields,f"EFOB{i}",r["EFOB"],maxlens)
    if st.button("Gerar PDF"):
        out=fill_pdf(template,named)
        st.download_button("Download PDF",data=out,file_name="NAVLOG.pdf",mime="application/pdf")
except Exception as e:
    st.error(f"Erro PDF: {e}")
