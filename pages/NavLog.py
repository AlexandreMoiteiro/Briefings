# app.py — NAVLOG (TOC/TOD só para CRUISE; DEP/ARR editáveis; tempos normalizados; holds contam em CRUISE)
# Reqs: streamlit, pypdf, reportlab, pytz

import streamlit as st
import datetime as dt
import pytz, io, json, unicodedata, re, math
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from math import sin, asin, radians, degrees, fmod

st.set_page_config(page_title="NAVLOG (PDF + Relatório)", layout="wide", initial_sidebar_state="collapsed")
PDF_TEMPLATE_PATHS = ["NAVLOG_FORM.pdf", "/mnt/data/NAVLOG_FORM.pdf"]

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

# ===== Helpers =====
def clean_point_name(s) -> str:
    txt = unicodedata.normalize("NFKD", str(s or "")).encode("ascii","ignore").decode("ascii")
    return txt.strip().upper()

def _round_alt(x: float) -> int:
    if x is None: return 0
    v = abs(float(x)); base = 50 if v < 1000 else 100
    return int(round(float(x)/base) * base)

def _round_unit(x: float) -> int:
    if x is None: return 0
    return int(round(float(x)))

def _round_tenth(x: float) -> float:
    if x is None: return 0.0
    return round(float(x), 1)

def _round_angle(x: float) -> int:
    if x is None: return 0
    return int(round(float(x))) % 360

def round_to_10s(sec: float) -> int:
    if sec <= 0: return 0
    s = int(round(sec/10.0)*10)
    return max(s, 10)

def mmss_from_seconds(tsec: int) -> str:
    m = tsec // 60; s = tsec % 60
    return f"{m:02d}:{s:02d}"

def hhmmss_from_seconds(tsec: int) -> str:
    h = tsec // 3600; m = (tsec % 3600)//60; s = tsec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def fmt(x: float, kind: str) -> str:
    if kind == "dist":   return f"{round(float(x or 0),1):.1f}"
    if kind == "fuel":   return f"{_round_tenth(x):.1f}"
    if kind == "ff":     return str(_round_unit(x))
    if kind == "speed":  return str(_round_unit(x))
    if kind == "angle":  return str(_round_angle(x))
    if kind == "alt":    return str(_round_alt(x))
    return str(x)

def fmt_min_or_h(total_seconds: int) -> str:
    """ <60min => 'MM min'; >=60min => 'H h MM min' """
    total_seconds = int(total_seconds or 0)
    mins = total_seconds // 60
    if mins < 60:
        return f"{mins} min"
    h = mins // 60
    m = mins % 60
    return f"{h} h {m:02d} min"

def parse_hhmm(s:str):
    s=(s or "").strip()
    for fmt in ("%H:%M:%S","%H:%M","%H%M"):
        try: return dt.datetime.strptime(s,fmt).time()
        except: pass
    return None

def add_seconds(t:dt.time, s:int):
    if not t: return None
    today=dt.date.today(); base=dt.datetime.combine(today,t)
    return (base+dt.timedelta(seconds=int(s))).time()

def clamp(v,lo,hi): return max(lo,min(hi,v))
def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

def wrap360(x): x=fmod(x,360.0); return x+360 if x<0 else x
def angle_diff(a,b): return (a-b+180)%360-180

# ===== Perf (Tecnam P2008 – exemplo) =====
ROC_ENROUTE = {
    0:{-25:981,0:835,25:704,50:586},  2000:{-25:870,0:726,25:597,50:481},
    4000:{-25:759,0:617,25:491,50:377},6000:{-25:648,0:509,25:385,50:273},
    8000:{-25:538,0:401,25:279,50:170},10000:{-25:428,0:294,25:174,50:66},
    12000:{-25:319,0:187,25:69,50:-37},14000:{-25:210,0:80,25:-35,50:-139},
}
ROC_FACTOR = 0.90
VY_ENROUTE = {0:67,2000:67,4000:67,6000:67,8000:67,10000:67,12000:67,14000:67}

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
    if oat_c is not None:
        dev=oat_c - isa_temp(pa_c)
        if dev>0: tas*=1-0.02*(dev/15); ff*=1-0.025*(dev/15)
        elif dev<0: tas*=1+0.01*((-dev)/15); ff*=1+0.03*((-dev)/15)
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
    v0 = interp1(t, t0, t1, v00, v01); v1 = interp1(pa_c, p0, p1, v10, v11)
    return max(1.0, interp1(pa_c, p0, p1, v0, v1) * ROC_FACTOR)

def vy_interp_enroute(pa):
    pas=sorted(VY_ENROUTE.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    return interp1(pa_c, p0, p1, VY_ENROUTE[p0], VY_ENROUTE[p1])

# ===== Wind & Var =====
def wind_triangle(tc_deg: float, tas_kt: float, wind_from_deg: float, wind_kt: float):
    if tas_kt <= 0: return 0.0, wrap360(tc_deg), 0.0
    delta = radians(angle_diff(wind_from_deg, tc_deg))
    cross = wind_kt * sin(delta)
    s = max(-1.0, min(1.0, cross/max(tas_kt,1e-9)))
    wca = degrees(asin(s))
    th  = wrap360(tc_deg + wca)
    gs  = max(0.0, tas_kt*math.cos(radians(wca)) - wind_kt*math.cos(delta))
    return wca, th, gs

def apply_var(true_deg,var_deg,east_is_negative=False):
    return wrap360(true_deg - var_deg if east_is_negative else true_deg + var_deg)

# ===== Aerodromes (ex.) =====
AEROS={
 "LPSO":{"elev":390,"freq":"119.805"},
 "LPEV":{"elev":807,"freq":"122.705"},
 "LPCB":{"elev":1251,"freq":"122.300"},
 "LPCO":{"elev":587,"freq":"118.405"},
 "LPVZ":{"elev":2060,"freq":"118.305"},
}
def aero_elev(icao): return int(AEROS.get(icao,{}).get("elev",0))
def aero_freq(icao): return AEROS.get(icao,{}).get("freq","")

# ===== PDF helpers =====
@st.cache_data(show_spinner=False)
def read_pdf_bytes(paths: Tuple[str, ...]) -> bytes:
    for p in paths:
        if Path(p).exists():
            return Path(p).read_bytes()
    raise FileNotFoundError(paths)

@st.cache_data(show_spinner=False)
def get_form_fields(template_bytes: bytes):
    reader = PdfReader(io.BytesIO(template_bytes))
    fd = reader.get_fields() or {}
    field_names = set(fd.keys())
    maxlens = {}
    for k,v in fd.items():
        ml = v.get("/MaxLen")
        if ml: maxlens[k] = int(ml)
    return field_names, maxlens

def fill_pdf(template_bytes: bytes, fields: dict) -> bytes:
    if not PYPDF_OK: raise RuntimeError("pypdf missing")
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()
    if hasattr(writer, "clone_document_from_reader"):
        writer.clone_document_from_reader(reader)
    else:
        for p in reader.pages: writer.add_page(p)
        acro = reader.trailer["/Root"].get("/AcroForm")
        if acro is not None:
            writer._root_object.update({NameObject("/AcroForm"): acro})
    try:
        acroform = writer._root_object.get("/AcroForm")
        if acroform:
            acroform.update({
                NameObject("/NeedAppearances"): True,
                NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g")
            })
    except Exception:
        pass
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

# =========================================================
# Estado inicial
# =========================================================
def ensure(k, v):
    if k not in st.session_state: st.session_state[k] = v

ensure("aircraft","P208"); ensure("registration","CS-ECC"); ensure("callsign","RVP")
ensure("student","AMOIT"); ensure("lesson",""); ensure("instrutor","")
ensure("dept","LPSO"); ensure("arr","LPEV"); ensure("altn","LPCB")
ensure("startup","")
ensure("qnh",1013); ensure("cruise_alt",4000)
ensure("temp_c",15); ensure("var_deg",1); ensure("var_is_e",False)
ensure("wind_from",0); ensure("wind_kt",17)
ensure("rpm_climb",2250); ensure("rpm_cruise",2000)
ensure("descent_ff",15.0); ensure("rod_fpm",700); ensure("start_fuel",85.0)
ensure("cruise_ref_kt",90); ensure("descent_ref_kt",65)
ensure("use_navaids",False)

# Holding params
ensure("hold_ref_kt", 80)
ensure("hold_ff_lph", 18.0)
ensure("auto_fix_edits", True)

# Taxi
ensure("taxi_min",15)
ensure("taxi_ff_lph",20.0)

# =========================================================
# Cabeçalho / Atmosfera / Perf
# =========================================================
st.title("Navigation Plan & Inflight Log — Tecnam P2008")
with st.form("hdr_perf_form", clear_on_submit=False):
    st.subheader("Identificação e Parâmetros")
    c1,c2,c3 = st.columns(3)
    with c1:
        f_aircraft = st.text_input("Aircraft", st.session_state.aircraft)
        f_registration = st.selectbox("Registration",
                                      ["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"],
                                      index=["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"].index(st.session_state.registration))
        f_callsign = st.text_input("Callsign", st.session_state.callsign)
        f_startup  = st.text_input("Startup (HH:MM ou HH:MM:SS)", st.session_state.startup)
    with c2:
        f_student = st.text_input("Student", st.session_state.student)
        f_lesson  = st.text_input("Lesson (ex: 12)", st.session_state.lesson)
        f_instrut = st.text_input("Instrutor", st.session_state.instrutor)
    with c3:
        f_dep = st.selectbox("Departure", list(AEROS.keys()), index=list(AEROS.keys()).index(st.session_state.dept))
        f_arr = st.selectbox("Arrival",  list(AEROS.keys()), index=list(AEROS.keys()).index(st.session_state.arr))
        f_altn= st.selectbox("Alternate",list(AEROS.keys()), index=list(AEROS.keys()).index(st.session_state.altn))

    st.markdown("---")
    c4,c5,c6 = st.columns(3)
    with c4:
        f_qnh  = st.number_input("QNH (hPa)", 900, 1050, int(st.session_state.qnh), step=1)
        f_crz  = st.number_input("Cruise Altitude (ft)", 0, 14000, int(st.session_state.cruise_alt), step=50)
    with c5:
        f_oat  = st.number_input("OAT (°C)", -40, 50, int(st.session_state.temp_c), step=1)
        f_var  = st.number_input("Mag Variation (°)", 0, 30, int(st.session_state.var_deg), step=1)
        f_varE = (st.selectbox("Variação E/W", ["W","E"], index=(1 if st.session_state.var_is_e else 0))=="E")
    with c6:
        f_wdir = st.number_input("Wind FROM (°TRUE)", 0, 360, int(st.session_state.wind_from), step=1)
        f_wkt  = st.number_input("Wind (kt)", 0, 120, int(st.session_state.wind_kt), step=1)

    c7,c8,c9 = st.columns(3)
    with c7:
        f_rpm_cl = st.number_input("Climb RPM (AFM)", 1800, 2388, int(st.session_state.rpm_climb), step=10)
        f_rpm_cr = st.number_input("Cruise RPM (AFM)", 1800, 2388, int(st.session_state.rpm_cruise), step=10)
    with c8:
        f_ff_ds  = st.number_input("Descent FF (L/h)", 0.0, 30.0, float(st.session_state.descent_ff), step=0.1)
    with c9:
        f_rod    = st.number_input("ROD (ft/min)", 200, 1500, int(st.session_state.rod_fpm), step=10)
        f_fuel0  = st.number_input("Fuel inicial (EFOB_START) [L]", 0.0, 1000.0, float(st.session_state.start_fuel), step=0.1)

    st.markdown("---")
    c10,c11,c12 = st.columns(3)
    with c10:
        f_use_nav = st.checkbox("Mostrar/usar NAVAIDs no PDF", value=bool(st.session_state.use_navaids))
        f_taxi_min = st.number_input("Taxi (min)", 0, 60, int(st.session_state.taxi_min), step=1)
        st.write("Taxi FF (L/h): **20** (fixo)")
    with c11:
        f_spd_cr  = st.number_input("Cruise speed (kt)", 40, 140, int(st.session_state.cruise_ref_kt), step=1)
        f_spd_ds  = st.number_input("Descent speed (kt)", 40, 120, int(st.session_state.descent_ref_kt), step=1)
        f_auto_fix = st.checkbox("Auto-fixar ao editar Alt_ft", value=bool(st.session_state.auto_fix_edits))
    with c12:
        f_hold_spd = st.number_input("Holding speed (kt)", 40, 140, int(st.session_state.hold_ref_kt), step=1)
        f_hold_ff  = st.number_input("Holding FF (L/h)",   0.0, 30.0, float(st.session_state.hold_ff_lph), step=0.1)

    submitted = st.form_submit_button("Aplicar cabeçalho + performance")
    if submitted:
        st.session_state.aircraft=f_aircraft; st.session_state.registration=f_registration; st.session_state.callsign=f_callsign
        st.session_state.startup=f_startup; st.session_state.student=f_student; st.session_state.lesson=f_lesson; st.session_state.instrutor=f_instrut
        st.session_state.dept=f_dep; st.session_state.arr=f_arr; st.session_state.altn=f_altn
        st.session_state.qnh=f_qnh; st.session_state.cruise_alt=f_crz; st.session_state.temp_c=f_oat
        st.session_state.var_deg=f_var; st.session_state.var_is_e=f_varE; st.session_state.wind_from=f_wdir; st.session_state.wind_kt=f_wkt
        st.session_state.rpm_climb=f_rpm_cl; st.session_state.rpm_cruise=f_rpm_cr
        st.session_state.descent_ff=f_ff_ds; st.session_state.rod_fpm=f_rod; st.session_state.start_fuel=f_fuel0
        st.session_state.cruise_ref_kt=f_spd_cr; st.session_state.descent_ref_kt=f_spd_ds
        st.session_state.use_navaids=f_use_nav
        st.session_state.taxi_min=f_taxi_min
        st.session_state.taxi_ff_lph=20.0
        st.session_state.hold_ref_kt = f_hold_spd
        st.session_state.hold_ff_lph = f_hold_ff
        st.session_state.auto_fix_edits = f_auto_fix
        st.success("Parâmetros aplicados.")

# =========================================================
# JSON v2
# =========================================================
st.subheader("Export / Import JSON v2 (rota, TCs/Dist, Altitudes por fix, HOLDs)")
def current_points(): return st.session_state.get("points") or [st.session_state.dept, st.session_state.arr]

def export_json_v2():
    pts   = current_points()
    legs  = st.session_state.get("plan_rows") or []
    alts  = st.session_state.get("alt_rows")  or []
    alt_set  = [ (r.get("Alt_ft") if r.get("Fix") or i in (0, len(pts)-1) else None)
                 for i,r in enumerate(alts) ] if alts else [None]*len(pts)
    alt_fix  = [ bool(r.get("Fix", False)) for r in alts ] if alts else [False]*len(pts)
    hold_on  = [ bool(r.get("Hold", False)) for r in alts ] if alts else [False]*len(pts)
    hold_min = [ float(r.get("Hold_min", 0.0)) for r in alts ] if alts else [0.0]*len(pts)
    data = {
        "version": 2,
        "route_points": pts,
        "legs": [{"TC": float(legs[i].get("TC",0.0)), "Dist": float(legs[i].get("Dist",0.0))} for i in range(len(legs))],
        "alt_set_ft": alt_set, "alt_fixed": alt_fix, "alt_hold_on": hold_on, "alt_hold_min": hold_min,
    }
    dep_code = clean_point_name(pts[0]); arr_code = clean_point_name(pts[-1])
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"), f"route_{dep_code}_{arr_code}.json"

cJ1,cJ2 = st.columns([1,1])
with cJ1:
    jb, jname = export_json_v2()
    st.download_button("💾 Download rota (JSON v2)", data=jb, file_name=jname, mime="application/json")
with cJ2:
    upl = st.file_uploader("📤 Import JSON v2", type=["json"], key="route_json_v2")
    if upl is not None:
        try:
            data = json.loads(upl.read().decode("utf-8"))
            pts = [clean_point_name(p) for p in (data.get("route_points") or current_points())]
            st.session_state.dept, st.session_state.arr = pts[0], pts[-1]
            st.session_state.points = pts
            st.session_state.route_text = " ".join(pts)
            # legs
            rows = [{"From":pts[i-1],"To":pts[i],"TC":0.0,"Dist":0.0} for i in range(1,len(pts))]
            legs_in = data.get("legs") or []
            for i in range(min(len(rows), len(legs_in))):
                rows[i]["TC"]=float(legs_in[i].get("TC",0.0))
                rows[i]["Dist"]=float(legs_in[i].get("Dist",0.0))
            st.session_state.plan_rows = rows
            # alts
            dep_e=_round_alt(aero_elev(pts[0])); arr_e=_round_alt(aero_elev(pts[-1]))
            ar=[]
            aset = data.get("alt_set_ft") or []; afix = data.get("alt_fixed") or []
            hOn  = data.get("alt_hold_on") or []; hMin = data.get("alt_hold_min") or []
            for i,p in enumerate(pts):
                row={"Fix":False,"Point":p,"Alt_ft":float(_round_alt(st.session_state.cruise_alt)),"Hold":False,"Hold_min":0.0}
                if i==0: row["Fix"]=True; row["Alt_ft"]=float(dep_e)
                elif i==len(pts)-1: row["Fix"]=True; row["Alt_ft"]=float(arr_e)
                if i<len(aset) and i<len(afix) and afix[i] and aset[i] is not None: row["Fix"]=True; row["Alt_ft"]=float(aset[i])
                if i<len(hOn) and i<len(hMin): row["Hold"]=bool(hOn[i]); row["Hold_min"]=float(hMin[i])
                ar.append(row)
            st.session_state.alt_rows = ar
            st.session_state.combined_rows = None
            st.success("Rota importada e aplicada.")
        except Exception as e:
            st.error(f"Falha a importar JSON: {e}")

# =========================================================
# Rota
# =========================================================
def parse_route_text(txt:str) -> List[str]:
    tokens = re.split(r"[,\s→\-]+", (txt or "").strip())
    return [clean_point_name(t) for t in tokens if t]

def rebuild_plan_rows(points: List[str], prev: Optional[List[dict]]):
    prev_map = {(clean_point_name(r["From"]),clean_point_name(r["To"])):r for r in (prev or [])}
    rows=[]
    for i in range(1,len(points)):
        frm,to=points[i-1],points[i]
        base={"From":frm,"To":to,"TC":0.0,"Dist":0.0}
        if (frm,to) in prev_map:
            base["TC"]=float(prev_map[(frm,to)].get("TC",0.0))
            base["Dist"]=float(prev_map[(frm,to)].get("Dist",0.0))
        rows.append(base)
    return rows

def rebuild_alt_rows(points: List[str], cruise:int, prev: Optional[List[dict]]):
    out=[]
    prev = prev or []
    for i,p in enumerate(points):
        base = prev[i] if i < len(prev) else {"Fix": False, "Alt_ft": float(_round_alt(cruise)), "Hold": False, "Hold_min": 0.0, "Point": p}
        row = {"Fix": bool(base.get("Fix", False)),
               "Point": p,
               "Alt_ft": float(base.get("Alt_ft", _round_alt(cruise))),
               "Hold": bool(base.get("Hold", False)),
               "Hold_min": float(base.get("Hold_min", 0.0))}
        out.append(row)
    return out

def to_records(obj) -> List[dict]:
    try:
        import pandas as pd
        if isinstance(obj, pd.DataFrame):
            obj = obj.fillna(0)
            return [{k: (clean_point_name(v) if k=="Point" else
                         (float(v) if k in ("Alt_to_ft","Alt_dep_ft","Alt_arr_ft","Hold_to_min","Dist","TC") else
                          bool(v) if k in ("Fix_to","Fix_dep","Fix_arr","Hold_to") else v))
                     for k,v in row.items()} for _, row in obj.iterrows()]
    except Exception:
        pass
    if isinstance(obj, list):
        rec=[]
        for r in obj:
            d=dict(r)
            if "Point" in d: d["Point"]=clean_point_name(d.get("Point"))
            for numk in ("Alt_to_ft","Alt_dep_ft","Alt_arr_ft","Hold_to_min","Dist","TC"):
                if numk in d: d[numk]=float(d[numk])
            for boolk in ("Fix_to","Fix_dep","Fix_arr","Hold_to"):
                if boolk in d: d[boolk]=bool(d[boolk])
            rec.append(d)
        return rec
    return []

# ===== Editor Único (TC/Dist + Alt 'To' + DEP/ARR editáveis) =====
def build_combined_rows(points: List[str], legs: List[dict], alts: List[dict]) -> List[dict]:
    rows=[]
    for i in range(1, len(points)):
        frm, to = points[i-1], points[i]
        leg = (legs[i-1] if i-1 < len(legs) else {"TC":0.0,"Dist":0.0})
        a_to = alts[i] if i < len(alts) else {"Fix": False, "Alt_ft": _round_alt(st.session_state.cruise_alt), "Hold": False, "Hold_min": 0.0}
        a_dep = alts[0] if alts else {"Fix": True, "Alt_ft": _round_alt(st.session_state.cruise_alt)}
        a_arr = alts[-1] if alts else {"Fix": True, "Alt_ft": _round_alt(st.session_state.cruise_alt)}

        rows.append({
            "From": frm,
            "To": to,
            "TC": float(leg.get("TC", 0.0)),
            "Dist": float(leg.get("Dist", 0.0)),
            # Altitude destino (índice i)
            "Fix_to": bool(a_to.get("Fix", False)) if i not in (0, len(points)-1) else bool(a_to.get("Fix", True)),
            "Alt_to_ft": float(a_to.get("Alt_ft", _round_alt(st.session_state.cruise_alt))) if bool(a_to.get("Fix", False)) else float(_round_alt(st.session_state.cruise_alt)),
            "Hold_to": bool(a_to.get("Hold", False)),
            "Hold_to_min": float(a_to.get("Hold_min", 0.0)),
            # DEP/ARR editáveis (visíveis em todas as linhas; só contam na 1ª e última)
            "Fix_dep": bool(a_dep.get("Fix", True)),
            "Alt_dep_ft": float(a_dep.get("Alt_ft", _round_alt(st.session_state.cruise_alt))),
            "Fix_arr": bool(a_arr.get("Fix", True)),
            "Alt_arr_ft": float(a_arr.get("Alt_ft", _round_alt(st.session_state.cruise_alt))),
        })
    return rows

def normalize_from_combined(points: List[str], edited_rows: List[dict]) -> Tuple[List[dict], List[dict]]:
    # plan_rows
    plan_rows=[]
    for i in range(1, len(points)):
        r = edited_rows[i-1] if i-1 < len(edited_rows) else {}
        plan_rows.append({
            "From": points[i-1],
            "To": points[i],
            "TC": float(r.get("TC", 0.0)),
            "Dist": float(r.get("Dist", 0.0)),
        })
    # alt_rows por índice (0..len(points)-1)
    out = []
    # DEP (apenas da 1ª linha editada)
    dep_r = edited_rows[0] if edited_rows else {}
    dep_fix = bool(dep_r.get("Fix_dep", True))
    dep_alt = float(dep_r.get("Alt_dep_ft", _round_alt(aero_elev(points[0]))))
    out.append({"Point": points[0], "Fix": dep_fix, "Alt_ft": dep_alt, "Hold": False, "Hold_min": 0.0})

    # Pontos intermédios (1..len-2)
    for i in range(1, len(points)-1):
        r = edited_rows[i-1] if i-1 < len(edited_rows) else {}
        is_fix = bool(r.get("Fix_to", False))
        alt_to = float(r.get("Alt_to_ft", _round_alt(st.session_state.cruise_alt)))
        hold = bool(r.get("Hold_to", False))
        hold_min = float(r.get("Hold_to_min", 0.0))
        out.append({"Point": points[i], "Fix": is_fix, "Alt_ft": alt_to if is_fix else float(_round_alt(st.session_state.cruise_alt)),
                    "Hold": hold, "Hold_min": hold_min})

    # ARR (apenas da última linha editada)
    arr_r = edited_rows[-1] if edited_rows else {}
    arr_fix = bool(arr_r.get("Fix_arr", True))
    arr_alt = float(arr_r.get("Alt_arr_ft", _round_alt(aero_elev(points[-1]))))
    arr_hold = bool(arr_r.get("Hold_to", False))
    arr_hold_min = float(arr_r.get("Hold_to_min", 0.0))
    out.append({"Point": points[-1], "Fix": arr_fix, "Alt_ft": arr_alt, "Hold": arr_hold, "Hold_min": arr_hold_min})

    return plan_rows, out

default_route = f"{st.session_state.dept} {st.session_state.arr}"
route_text = st.text_area("Rota (DEP … ARR)", value=st.session_state.get("route_text", default_route))

if st.button("Aplicar rota"):
    pts = parse_route_text(route_text) or [clean_point_name(st.session_state.dept), clean_point_name(st.session_state.arr)]
    pts[0]=clean_point_name(st.session_state.dept)
    if len(pts)>=2: pts[-1]=clean_point_name(st.session_state.arr)
    st.session_state.points = pts
    st.session_state.route_text = " ".join(pts)
    st.session_state.plan_rows = rebuild_plan_rows(pts, st.session_state.get("plan_rows"))
    st.session_state.alt_rows  = rebuild_alt_rows(pts, st.session_state.cruise_alt, st.session_state.get("alt_rows"))
    st.session_state.combined_rows = build_combined_rows(st.session_state.points, st.session_state.plan_rows, st.session_state.alt_rows)
    st.success("Rota aplicada.")

# init defaults
if "points" not in st.session_state:
    st.session_state.points = parse_route_text(st.session_state.get("route_text", default_route)) or [st.session_state.dept, st.session_state.arr]
if "plan_rows" not in st.session_state:
    st.session_state.plan_rows = rebuild_plan_rows(st.session_state.points, None)
if "alt_rows" not in st.session_state:
    st.session_state.alt_rows = rebuild_alt_rows(st.session_state.points, st.session_state.cruise_alt, None)
if "combined_rows" not in st.session_state or st.session_state.combined_rows is None:
    st.session_state.combined_rows = build_combined_rows(st.session_state.points, st.session_state.plan_rows, st.session_state.alt_rows)

# ===== Editor Único =====
st.subheader("Plano (TC/Dist + Altitudes no destino + DEP/ARR editáveis)")
combined_cfg = {
    "From": st.column_config.TextColumn("From", disabled=True),
    "To":   st.column_config.TextColumn("To", disabled=True),
    "TC":   st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
    "Dist": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
    "Fix_to": st.column_config.CheckboxColumn("Fixar 'To'?"),
    "Alt_to_ft": st.column_config.NumberColumn("Alt 'To' (ft)", step=50, min_value=0.0),
    "Hold_to": st.column_config.CheckboxColumn("HOLD no 'To'?"),
    "Hold_to_min": st.column_config.NumberColumn("Min no HOLD 'To'", step=1.0, min_value=0.0),
    "Fix_dep": st.column_config.CheckboxColumn("Fix DEP?"),
    "Alt_dep_ft": st.column_config.NumberColumn("Alt DEP (ft)", step=50, min_value=0.0),
    "Fix_arr": st.column_config.CheckboxColumn("Fix ARR?"),
    "Alt_arr_ft": st.column_config.NumberColumn("Alt ARR (ft)", step=50, min_value=0.0),
}
with st.form("combined_form", clear_on_submit=False):
    combined_edited = st.data_editor(
        st.session_state.combined_rows,
        key="combined_table",
        hide_index=True, use_container_width=True, num_rows="fixed",
        column_config=combined_cfg,
        column_order=list(combined_cfg.keys())
    )
    combined_submit = st.form_submit_button("Aplicar plano (TC/Dist/Altitudes/HOLDs)")
    if combined_submit:
        recs = to_records(combined_edited)
        plan_rows, alt_rows = normalize_from_combined(st.session_state.points, recs)
        st.session_state.plan_rows = plan_rows
        st.session_state.alt_rows  = alt_rows
        st.session_state.combined_rows = build_combined_rows(st.session_state.points, plan_rows, alt_rows)
        if st.session_state.auto_fix_edits:
            crz=_round_alt(st.session_state.cruise_alt)
            for i,r in enumerate(st.session_state.alt_rows):
                if i not in (0, len(st.session_state.alt_rows)-1):
                    try:
                        if abs(float(r.get("Alt_ft", crz)) - float(crz)) >= 1 and not bool(r.get("Fix", False)):
                            r["Fix"] = True
                    except Exception:
                        pass
        st.session_state["__alts_applied_at__"] = dt.datetime.utcnow().isoformat()
        st.success("Plano aplicado (TC/Dist/Altitudes/HOLDs).")

# =========================================================
# Cálculo (TOC/TOD só para CRUISE; sem linha DEP nos resultados)
# =========================================================
points = st.session_state.points
legs   = st.session_state.plan_rows
alts   = st.session_state.alt_rows
N = len(legs)

def pressure_alt(alt_ft, qnh_hpa): return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0

start_alt = float(alts[0].get("Alt_ft", _round_alt(aero_elev(points[0]))))
cruise_alt = float(st.session_state.cruise_alt)

pa_start  = pressure_alt(start_alt, st.session_state.qnh)
vy_kt = vy_interp_enroute(pa_start)
tas_climb, tas_cruise, tas_descent = vy_kt, float(st.session_state.cruise_ref_kt), float(st.session_state.descent_ref_kt)
roc = roc_interp_enroute(pa_start, st.session_state.temp_c)
_, ff_climb = cruise_lookup(start_alt + 0.5*max(0.0, cruise_alt-start_alt), int(st.session_state.rpm_climb), st.session_state.temp_c)
_, ff_cruise= cruise_lookup(pressure_alt(cruise_alt, st.session_state.qnh), int(st.session_state.rpm_cruise), st.session_state.temp_c)
ff_descent  = float(st.session_state.descent_ff)

dist = [float(legs[i]["Dist"] or 0.0) for i in range(N)]
tcs  = [float(legs[i]["TC"]   or 0.0) for i in range(N)]

def leg_wind(i:int) -> Tuple[float,float]:
    return (int(st.session_state.wind_from), int(st.session_state.wind_kt))

# ---- Perfil alvo (por índice; fix respeitado; não força DEP/ARR)
A_target=[]
for i,p in enumerate(points):
    r = alts[i] if i < len(alts) else {"Fix":False,"Alt_ft":cruise_alt}
    A_target.append(float(r.get("Alt_ft", cruise_alt)) if bool(r.get("Fix", False)) else float(cruise_alt))

# ======= Construção de segmentos — TOC/TOD apenas quando atravessas CRZ =======
rows=[]          # tabela final (sem "linha DEP")
seq_points=[]    # sequência para PDF/obs
efob=float(st.session_state.start_fuel)

startup = parse_hhmm(st.session_state.startup)
takeoff = add_seconds(startup, int(st.session_state.taxi_min*60)) if startup else None
clock = takeoff

def add_seg(phase, frm, to, i_leg, d_nm, tas, ff_lph, alt_start_ft, rate_fpm):
    """Adiciona segmento se d_nm> ~0; devolve alt_end"""
    nonlocal clock, efob
    if d_nm <= 1e-3:  # ignora muito curtos
        return alt_start_ft
    wdir,wkt = leg_wind(i_leg)
    tc=float(tcs[i_leg]); wca, th, gs = wind_triangle(tc, tas, wdir, wkt)
    mc = apply_var(tc, st.session_state.var_deg, st.session_state.var_is_e)
    mh = apply_var(th, st.session_state.var_deg, st.session_state.var_is_e)

    ete_sec_raw = (60.0 * d_nm / max(gs,1e-6)) * 60.0
    ete_sec = round_to_10s(ete_sec_raw)
    burn_raw = ff_lph * (ete_sec_raw/3600.0)
    alt_end = alt_start_ft + (rate_fpm*(ete_sec_raw/60.0) if phase=="CLIMB" else (-rate_fpm*(ete_sec_raw/60.0) if phase=="DESCENT" else 0.0))

    eto = ""
    if clock:
        clock = add_seconds(clock, int(ete_sec))
        eto = clock.strftime("%H:%M")

    efob = max(0.0, _round_tenth(efob - burn_raw))

    rows.append({
        "Fase": {"CLIMB":"↑","CRUISE":"→","DESCENT":"↓"}[phase],
        "Leg/Marker": f"{frm}→{to}",
        "ALT (ft)": f"{fmt(alt_start_ft,'alt')}→{fmt(alt_end,'alt')}",
        "TC (°T)": _round_angle(tc), "TH (°T)": _round_angle(th),
        "MC (°M)": _round_angle(mc), "MH (°M)": _round_angle(mh),
        "TAS (kt)": _round_unit(tas), "GS (kt)": _round_unit(gs),
        "FF (L/h)": _round_unit(ff_lph),
        "Dist (nm)": fmt(d_nm,'dist'), "ETE (mm:ss)": mmss_from_seconds(int(ete_sec)), "ETO": eto,
        "Burn (L)": fmt(burn_raw,'fuel'), "EFOB (L)": fmt(efob,'fuel')
    })

    seq_points.append({
        "name": to, "alt": _round_alt(alt_end),
        "tc": _round_angle(tc), "th": _round_angle(th),
        "mc": _round_angle(mc), "mh": _round_angle(mh),
        "tas": _round_unit(tas), "gs": _round_unit(gs),
        "wca": round(wca,1),
        "dist": float(f"{d_nm:.3f}"),
        "ete_sec": int(ete_sec), "ete_raw": float(ete_sec_raw),
        "eto": eto, "burn": float(burn_raw),
        "rate_fpm": float(rate_fpm if phase!="CRUISE" else 0.0),
        "phase": phase,
        "efob": float(efob), "leg_idx": int(i_leg)
    })
    return alt_end

def add_marker(name, leg_idx, pos_label):
    """Adiciona um marcador TOC/TOD (não conta dist/tempo/fuel)."""
    rows.append({"Fase":"·","Leg/Marker": f"{name}", "ALT (ft)":"", "TC (°T)":"", "TH (°T)":"",
                 "MC (°M)":"", "MH (°M)":"", "TAS (kt)":"", "GS (kt)":"", "FF (L/h)":"",
                 "Dist (nm)":"", "ETE (mm:ss)":"", "ETO":"", "Burn (L)":"", "EFOB (L)":""})

# Holds: contam como CRUISE nas estatísticas
def add_hold(point_name, minutes, alt_now, i_leg):
    nonlocal clock, efob
    if minutes<=0: return alt_now
    ete_sec = round_to_10s(minutes*60.0)
    burn_raw = st.session_state.hold_ff_lph * (ete_sec/3600.0)
    eto = ""
    if clock:
        clock = add_seconds(clock, int(ete_sec))
        eto = clock.strftime("%H:%M")
    efob_local = max(0.0, _round_tenth(efob - burn_raw))
    # Linha informativa (fase HOLD), mas no total entra como CRUISE
    rows.append({
        "Fase":"⟳", "Leg/Marker": f"HOLD @{point_name}",
        "ALT (ft)": f"{fmt(alt_now,'alt')}→{fmt(alt_now,'alt')}",
        "TC (°T)":"", "TH (°T)":"", "MC (°M)":"", "MH (°M)":"",
        "TAS (kt)": _round_unit(st.session_state.hold_ref_kt), "GS (kt)":"",
        "FF (L/h)": _round_unit(st.session_state.hold_ff_lph),
        "Dist (nm)":"", "ETE (mm:ss)": mmss_from_seconds(int(ete_sec)), "ETO": eto,
        "Burn (L)": fmt(burn_raw,'fuel'), "EFOB (L)": fmt(efob_local,'fuel')
    })
    # Para estatísticas, regista como CRUISE
    seq_points.append({
        "name": point_name, "alt": _round_alt(alt_now),
        "tc":"", "th":"", "mc":"", "mh":"",
        "tas": _round_unit(st.session_state.hold_ref_kt), "gs":"",
        "wca": 0.0, "dist":"", "ete_sec": int(ete_sec), "ete_raw": float(ete_sec),
        "eto": eto, "burn": float(burn_raw), "rate_fpm": 0.0,
        "phase":"CRUISE",  # <- conta em CRUISE
        "efob": float(efob_local), "leg_idx": int(i_leg)
    })
    efob = efob_local
    return alt_now

toc_list=[]; tod_list=[]
cur_alt = float(A_target[0])
CRZ = float(st.session_state.cruise_alt)

for i in range(N):
    frm, to = legs[i]["From"], legs[i]["To"]
    d_leg = float(dist[i]); tc_i = float(tcs[i])

    # GS por fase
    wdir,wkt = leg_wind(i)
    _,_,gsC = wind_triangle(tc_i, vy_kt, wdir, wkt)
    _,_,gsR = wind_triangle(tc_i, float(st.session_state.cruise_ref_kt), wdir, wkt)
    _,_,gsD = wind_triangle(tc_i, float(st.session_state.descent_ref_kt), wdir, wkt)
    gsC = max(gsC,1e-6); gsR=max(gsR,1e-6); gsD=max(gsD,1e-6)

    alt_from = cur_alt
    alt_to = float(A_target[i+1])
    remain = d_leg

    # ---------------- SUBIDA ----------------
    if alt_to > alt_from + 1e-6:
        # atravessa CRZ? -> marca TOC
        if alt_from < CRZ <= alt_to:
            t_to_crz = (CRZ - alt_from)/max(roc,1e-6)
            d_to_crz = clamp(gsC*(t_to_crz/60.0), 0.0, remain)
            if d_to_crz > 1e-3:
                cur_alt = add_seg("CLIMB", frm, "TOC", i, d_to_crz, vy_kt, ff_climb, cur_alt, roc)
                toc_list.append((i, d_to_crz, "TOC"))
                add_marker("TOC", i, d_to_crz)
                frm = "TOC"
                remain -= d_to_crz
                cur_alt = CRZ
        # resto da subida até ao alvo (sem TOC extra)
        if alt_to > cur_alt + 1e-6 and remain > 1e-3:
            t_up = (alt_to - cur_alt)/max(roc,1e-6)
            d_up = clamp(gsC*(t_up/60.0), 0.0, remain)
            cur_alt = add_seg("CLIMB", frm, to if abs(d_up-remain)<1e-9 else to, i, d_up, vy_kt, ff_climb, cur_alt, roc)
            frm = to if abs(d_up-remain)<1e-9 else frm
            remain -= d_up
            cur_alt = alt_to

    # ---------------- DESCIDA ----------------
    elif alt_to < alt_from - 1e-6:
        # sair do CRZ? -> marca TOD
        if abs(alt_from - CRZ) < 1e-6 and alt_to < CRZ and remain > 1e-3:
            # distância necessária de descida desde CRZ até alt_to
            t_down_total = (CRZ - alt_to)/max(st.session_state.rod_fpm,1e-6)
            d_down_total = gsD*(t_down_total/60.0)
            if d_down_total < remain - 1e-3:
                d_cruise = remain - d_down_total
                # CRUISE até TOD
                cur_alt = add_seg("CRUISE", frm, "TOD", i, d_cruise, float(st.session_state.cruise_ref_kt), ff_cruise, cur_alt, 0.0)
                tod_list.append((i, d_cruise, "TOD"))
                add_marker("TOD", i, d_cruise)
                frm = "TOD"
                remain = d_down_total
        # descer o que falta
        if remain > 1e-3:
            cur_alt = add_seg("DESCENT", frm, to, i, remain, float(st.session_state.descent_ref_kt), ff_descent, cur_alt, float(st.session_state.rod_fpm))
            frm = to; remain = 0.0
            cur_alt = alt_to

    # ---------------- CRUISE ----------------
    if remain > 1e-3 and frm != to:
        cur_alt = add_seg("CRUISE", frm, to, i, remain, float(st.session_state.cruise_ref_kt), ff_cruise, cur_alt, 0.0)
        remain = 0.0

    # ---------------- HOLD no destino (i+1) ----------------
    to_row = alts[i+1] if (i+1) < len(alts) else {}
    if bool(to_row.get("Hold")) and float(to_row.get("Hold_min", 0)) > 0:
        cur_alt = add_hold(points[i+1], float(to_row["Hold_min"]), cur_alt, i)

eta = clock
shutdown = add_seconds(eta, 5*60) if eta else None

# ==== Totais ====
phase_secs = {"CLIMB":0, "CRUISE":0, "DESCENT":0}
phase_burn = {"CLIMB":0.0, "CRUISE":0.0, "DESCENT":0.0}
for p in seq_points:
    ph = p.get("phase")
    if ph in phase_secs:
        phase_secs[ph] += int(p.get("ete_sec",0))
        phase_burn[ph] += float(p.get("burn",0.0))

taxi_min = int(st.session_state.taxi_min)
fuel_taxi = st.session_state.taxi_ff_lph * (taxi_min / 60.0)

# =========================================================
# Depuração: Perfil alvo aplicado (por índice)
# =========================================================
st.subheader("Perfil alvo aplicado (por índice)")
_applied_rows = [{"#": i, "Fix?": "✔" if bool(alts[i].get("Fix", False)) else "—",
                  "Fix name": points[i], "Alt alvo (ft)": int(round(A_target[i]))}
                 for i in range(len(points))]
st.dataframe(_applied_rows, use_container_width=True)

# =========================================================
# Resultados (sem 'linha DEP')
# =========================================================
st.subheader("Resultados")
cA,cB,cC = st.columns(3)
with cA:
    st.metric("Vy (kt)", _round_unit(vy_kt))
    st.metric("ROC @ DEP (ft/min)", _round_unit(roc))
    st.metric("ROD (ft/min)", _round_unit(st.session_state.rod_fpm))
with cB:
    st.metric("TAS climb/cruise/descent", f"{_round_unit(tas_climb)} / {_round_unit(tas_cruise)} / {_round_unit(tas_descent)} kt")
    st.metric("FF climb/cruise/descent", f"{_round_unit(ff_climb)} / {_round_unit(ff_cruise)} / {_round_unit(ff_descent)} L/h")
with cC:
    isa_dev = st.session_state.temp_c - isa_temp(pressure_alt(A_target[0], st.session_state.qnh))
    st.metric("ISA dev @ DEP (°C)", int(round(isa_dev)))
    if toc_list: st.write("**TOC**: " + ", ".join([f"{name} L{leg+1}@{fmt(pos,'dist')} nm" for (leg,pos,name) in toc_list]))
    if tod_list: st.write("**TOD**: " + ", ".join([f"{name} L{leg+1}@{fmt(pos,'dist')} nm" for (leg,pos,name) in tod_list]))

st.dataframe(rows, use_container_width=True)

tot_ete_sec = sum(int(p.get('ete_sec',0)) for p in seq_points if isinstance(p.get('ete_sec'), (int,float)))
tot_nm  = sum(float(p['dist']) for p in seq_points if isinstance(p.get('dist'), (int,float)))
tot_bo  = _round_tenth(sum(float(p['burn']) for p in seq_points if isinstance(p.get('burn'), (int,float))))
line = f"**Totais** — Dist {fmt(tot_nm,'dist')} nm • ETE {fmt_min_or_h(int(tot_ete_sec))} • Burn {fmt(tot_bo,'fuel')} L • EFOB {fmt(seq_points[-1]['efob'],'fuel')} L"
if eta: line += f" • **ETA {eta.strftime('%H:%M')}** • **Shutdown {shutdown.strftime('%H:%M')}**"
st.markdown(line)

# =========================================================
# PDF NAVLOG
# =========================================================
st.subheader("Gerar PDF NAVLOG")
try:
    template_bytes = read_pdf_bytes(tuple(PDF_TEMPLATE_PATHS))
    if not PYPDF_OK: raise RuntimeError("pypdf não disponível")
    fieldset, maxlens = get_form_fields(template_bytes)
except Exception as e:
    template_bytes=None; fieldset=set(); maxlens={}
    st.error(f"Não foi possível ler o PDF: {e}")

named: Dict[str,str] = {}
def P(key: str, value: str): put(named, fieldset, key, value, maxlens)
def PAll(keys: List[str], value: str):
    for k in keys:
        if k in fieldset: put(named, fieldset, k, value, maxlens)

if fieldset:
    etd = (add_seconds(parse_hhmm(st.session_state.startup), st.session_state.taxi_min*60).strftime("%H:%M") if st.session_state.startup else "")
    eta_txt = (eta.strftime("%H:%M") if eta else "")
    shutdown_txt = (shutdown.strftime("%H:%M") if shutdown else "")

    PAll(["AIRCRAFT","Aircraft"], st.session_state.aircraft)
    PAll(["REGISTRATION","Registration"], st.session_state.registration)
    PAll(["CALLSIGN","Callsign"], st.session_state.callsign)
    PAll(["ETD/ETA","ETD_ETA"], f"{etd} / {eta_txt}")
    PAll(["STARTUP","Startup"], st.session_state.startup)
    PAll(["TAKEOFF","Takeoff"], etd)
    PAll(["LANDING","Landing"], eta_txt)
    PAll(["SHUTDOWN","Shutdown"], shutdown_txt)
    PAll(["LESSON","Lesson"], st.session_state.lesson)
    PAll(["INSTRUTOR","Instructor","INSTRUCTOR"], st.session_state.instrutor)
    PAll(["STUDENT","Student"], st.session_state.student)

    PAll(["FLT TIME","FLT_TIME","FLIGHT_TIME"], f"{(tot_ete_sec//3600):02d}:{((tot_ete_sec%3600)//60):02d}")
    PAll(["FLIGHT_LEVEL_ALTITUDE","LEVEL_FF","LEVEL F/F","Level_FF"], fmt(cruise_alt,'alt'))

    climb_time_hours = phase_secs["CLIMB"]/3600.0
    climb_fuel_raw = ff_climb * max(0.0, climb_time_hours)
    PAll(["CLIMB FUEL","CLIMB_FUEL"], fmt(climb_fuel_raw,'fuel'))

    dep_elev_txt = fmt(A_target[0],'alt')
    arr_elev_txt = fmt(A_target[-1],'alt')

    PAll(["QNH"], str(int(round(st.session_state.qnh))))
    PAll(["DEPT","DEPARTURE_FREQ","DEPT_FREQ"], aero_freq(points[0]))
    PAll(["ENROUTE","ENROUTE_FREQ"], "123.755")
    PAll(["ARRIVAL","ARRIVAL_FREQ","ARR_FREQ"], aero_freq(points[-1]))
    PAll(["DEPARTURE_AIRFIELD","Departure_Airfield"], points[0])
    PAll(["ARRIVAL_AIRFIELD","Arrival_Airfield"], points[-1])
    PAll(["Leg_Number","LEG_NUMBER"], str(len(points)))
    PAll(["ALTERNATE_AIRFIELD","Alternate_Airfield"], st.session_state.altn)
    PAll(["ALTERNATE_ELEVATION","Alternate_Elevation","TextField_7"], fmt(_round_alt(aero_elev(st.session_state.altn)),'alt'))
    PAll(["WIND","WIND_FROM"], f"{int(round(st.session_state.wind_from)):03d}/{int(round(st.session_state.wind_kt)):02d}")
    isa_dev_i = int(round(st.session_state.temp_c - isa_temp(pressure_alt(A_target[0], st.session_state.qnh))))
    PAll(["TEMP_ISA_DEV","TEMP ISA DEV","TEMP/ISA_DEV"], f"{int(round(st.session_state.temp_c))} / {isa_dev_i}")
    PAll(["MAG_VAR","MAG VAR"], f"{int(round(st.session_state.var_deg))}{'E' if st.session_state.var_is_e else 'W'}")

    # linhas (até 22)
    acc_dist = 0.0; acc_sec = 0
    max_lines = 22
    for idx, p in enumerate(seq_points[:max_lines], start=1):
        tag=f"Leg{idx:02d}_"
        P(tag+"Waypoint", p["name"])
        if p.get("alt")!="": P(tag+"Altitude_FL", fmt(p["alt"],'alt'))

        if p.get("phase") in ("CLIMB","CRUISE","DESCENT"):
            acc_dist += float(p.get("dist") or 0.0)
            acc_sec  += int(p.get("ete_sec",0) or 0)
            P(tag+"True_Course",      fmt(p.get("tc",0), 'angle'))
            P(tag+"True_Heading",     fmt(p.get("th",0), 'angle'))
            P(tag+"Magnetic_Heading", fmt(p.get("mh",0), 'angle'))
            P(tag+"True_Airspeed",    fmt(p.get("tas",0), 'speed'))
            P(tag+"Ground_Speed",     fmt(p.get("gs",0), 'speed'))
            P(tag+"Leg_Distance",     fmt(p.get("dist",0), 'dist'))
            P(tag+"Leg_ETE",          mmss_from_seconds(int(p.get("ete_sec",0))))
            P(tag+"ETO",              p.get("eto",""))
            P(tag+"Planned_Burnoff",  fmt(p.get("burn",0.0), 'fuel'))
            P(tag+"Estimated_FOB",    fmt(p.get("efob",0.0), 'fuel'))
            P(tag+"Cumulative_Distance", fmt(acc_dist,'dist'))
            P(tag+"Cumulative_ETE",      mmss_from_seconds(acc_sec))

    # ===== OBSERVAÇÕES — tempos (min / h) e holds contam no Cruise =====
    obs_lines = [
        f"Start-up & Taxi: {st.session_state.taxi_min} min @ 20 L/h → {fmt(fuel_taxi,'fuel')} L",
        f"Climb: {fmt_min_or_h(phase_secs['CLIMB'])} → {fmt(phase_burn['CLIMB'],'fuel')} L",
        f"Enroute (Cruise): {fmt_min_or_h(phase_secs['CRUISE'])} → {fmt(phase_burn['CRUISE'],'fuel')} L",
        f"Descent: {fmt_min_or_h(phase_secs['DESCENT'])} → {fmt(phase_burn['DESCENT'],'fuel')} L",
    ]
    # Holds por fix (opcional – cada linha em minutos)
    for p in seq_points:
        if p.get("phase")=="CRUISE" and p.get("name","").startswith("HOLD @"):
            pass  # não listar; já tens a linha de HOLD em 'rows'
    P("OBSERVATIONS", "\n".join(obs_lines))

if st.button("Gerar PDF NAVLOG", type="primary"):
    try:
        if not template_bytes: raise RuntimeError("Template PDF não carregado")
        out = fill_pdf(template_bytes, named)
        m = re.search(r'(\d+)', st.session_state.lesson or "")
        lesson_num = m.group(1) if m else "00"
        safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
        filename = f"{safe_date}_LESSON-{lesson_num}_NAVLOG.pdf"
        st.download_button("📄 Download PDF", data=out, file_name=filename, mime="application/pdf")
        st.success("PDF gerado.")
    except Exception as e:
        st.error(f"Erro ao gerar PDF: {e}")

# =========================================================
# Relatório (PDF legível) — mantido igual ao teu, se precisares eu adapto aos novos tempos.
# =========================================================



