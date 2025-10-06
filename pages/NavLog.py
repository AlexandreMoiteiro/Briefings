# app.py — NAVLOG (TOC/TOD só no CRUISE; 1 linha por perna com médias ponderadas; DEP/ARR editáveis; holds nas Observations; PDF + Relatório)
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

def obs_time_str(seconds:int) -> str:
    # <60min -> "mm min"; >=60 -> "Hh Mm"
    minutes = int(round(seconds/60))
    if minutes < 60:
        return f"{minutes} min"
    h = minutes // 60
    m = minutes % 60
    return f"{h}h {m}m"

def fmt(x: float, kind: str) -> str:
    if kind == "dist":   return f"{round(float(x or 0),1):.1f}"
    if kind == "fuel":   return f"{_round_tenth(x):.1f}"
    if kind == "ff":     return str(_round_unit(x))
    if kind == "speed":  return str(_round_unit(x))
    if kind == "angle":  return str(_round_angle(x))
    if kind == "alt":    return str(_round_alt(x))
    return str(x)

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
                if i==0: row["Alt_ft"]=float(dep_e)  # default, mas editável mais abaixo
                elif i==len(pts)-1: row["Alt_ft"]=float(arr_e)
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
    dep_e=_round_alt(aero_elev(points[0])); arr_e=_round_alt(aero_elev(points[-1]))
    out=[]
    prev = prev or []
    for i,p in enumerate(points):
        base = prev[i] if i < len(prev) else {"Fix": False, "Alt_ft": float(_round_alt(cruise)), "Hold": False, "Hold_min": 0.0, "Point": p}
        row = {"Fix": bool(base.get("Fix", False)),
               "Point": p,
               "Alt_ft": float(base.get("Alt_ft", _round_alt(cruise))),
               "Hold": bool(base.get("Hold", False)),
               "Hold_min": float(base.get("Hold_min", 0.0))}
        # por defeito, dep/arr recebem elevação, mas NÃO forço Fix=True (editável)
        if i==0 and "Alt_ft" not in base: row["Alt_ft"]=float(dep_e)
        if i==len(points)-1 and "Alt_ft" not in base: row["Alt_ft"]=float(arr_e)
        out.append(row)
    return out

def to_records(obj) -> List[dict]:
    try:
        import pandas as pd
        if isinstance(obj, pd.DataFrame):
            obj = obj.fillna(0)
            return [{k: (clean_point_name(v) if k=="Point" else
                         (float(v) if k in ("Alt_to_ft","Alt_ft","Hold_to_min","Hold_min","Dist","TC","Alt_dep","Alt_arr") else
                          bool(v) if k in ("Fix","Hold","Fix_to","Hold_to","Fix_dep","Fix_arr") else v))
                     for k,v in row.items()} for _, row in obj.iterrows()]
    except Exception:
        pass
    if isinstance(obj, list):
        rec=[]
        for r in obj:
            d=dict(r)
            if "Point" in d: d["Point"]=clean_point_name(d.get("Point"))
            for numk in ("Alt_to_ft","Alt_ft","Hold_to_min","Hold_min","Dist","TC","Alt_dep","Alt_arr"):
                if numk in d: d[numk]=float(d[numk])
            for boolk in ("Fix","Hold","Fix_to","Hold_to","Fix_dep","Fix_arr"):
                if boolk in d: d[boolk]=bool(d[boolk])
            rec.append(d)
        return rec
    return []

# ===== Combined editor (legs + altitude/hold do destino por ÍNDICE) =====
def build_combined_rows(points: List[str], legs: List[dict], alts: List[dict]) -> List[dict]:
    rows=[]
    for i in range(1, len(points)):
        frm, to = points[i-1], points[i]
        leg = (legs[i-1] if i-1 < len(legs) else {"TC":0.0,"Dist":0.0})
        arow = alts[i] if i < len(alts) else {"Fix": False, "Alt_ft": _round_alt(st.session_state.cruise_alt), "Hold": False, "Hold_min": 0.0}
        rows.append({
            "From": frm,
            "To": to,
            "TC": float(leg.get("TC", 0.0)),
            "Dist": float(leg.get("Dist", 0.0)),
            "Fix_to": bool(arow.get("Fix", False)),
            "Alt_to_ft": (float(arow.get("Alt_ft", _round_alt(st.session_state.cruise_alt))) if bool(arow.get("Fix", False)) else float(_round_alt(st.session_state.cruise_alt))),
            "Hold_to": bool(arow.get("Hold", False)),
            "Hold_to_min": float(arow.get("Hold_min", 0.0)),
        })
    return rows

def normalize_from_combined(points: List[str], edited_rows: List[dict], dep_edit: dict, arr_edit: dict) -> Tuple[List[dict], List[dict]]:
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
    # alt_rows por índice (inclui DEP/ARR editáveis)
    out=[]
    for i,p in enumerate(points):
        base = {"Fix": False, "Alt_ft": float(_round_alt(st.session_state.cruise_alt)),
                "Hold": False, "Hold_min": 0.0}
        if i==0:
            base["Fix"]=bool(dep_edit.get("Fix_dep", False))
            base["Alt_ft"]=float(dep_edit.get("Alt_dep", _round_alt(aero_elev(points[0]))))
        elif i==len(points)-1:
            base["Fix"]=bool(arr_edit.get("Fix_arr", False))
            base["Alt_ft"]=float(arr_edit.get("Alt_arr", _round_alt(aero_elev(points[-1]))))
            last = edited_rows[-1] if edited_rows else {}
            base["Hold"]=bool(last.get("Hold_to", False))
            base["Hold_min"]=float(last.get("Hold_to_min", 0.0))
        else:
            r = edited_rows[i-1] if i-1 < len(edited_rows) else {}
            base["Fix"]=bool(r.get("Fix_to", False))
            base["Alt_ft"]=float(r.get("Alt_to_ft", _round_alt(st.session_state.cruise_alt)) if base["Fix"] else _round_alt(st.session_state.cruise_alt))
            base["Hold"]=bool(r.get("Hold_to", False))
            base["Hold_min"]=float(r.get("Hold_to_min", 0.0))
        out.append({"Point": p, **base})
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

# ===== Mini-editor para DEP/ARR (editável) =====
st.subheader("Altitudes DEP/ARR (editáveis)")
dep_alt_default = float(st.session_state.alt_rows[0]["Alt_ft"])
arr_alt_default = float(st.session_state.alt_rows[-1]["Alt_ft"])
dep_fix_default = bool(st.session_state.alt_rows[0].get("Fix", False))
arr_fix_default = bool(st.session_state.alt_rows[-1].get("Fix", False))
with st.form("dep_arr_form", clear_on_submit=False):
    c1,c2 = st.columns(2)
    with c1:
        fix_dep = st.checkbox("Fix DEP?", value=dep_fix_default)
        alt_dep = st.number_input("Alt DEP (ft)", min_value=0.0, step=50.0, value=float(dep_alt_default))
    with c2:
        fix_arr = st.checkbox("Fix ARR?", value=arr_fix_default)
        alt_arr = st.number_input("Alt ARR (ft)", min_value=0.0, step=50.0, value=float(arr_alt_default))
    dep_arr_submit = st.form_submit_button("Aplicar DEP/ARR")

# ===== Editor Único =====
st.subheader("Plano (TC/Dist + Altitude & HOLD no Fix de destino)")
combined_cfg = {
    "From": st.column_config.TextColumn("From", disabled=True),
    "To":   st.column_config.TextColumn("To", disabled=True),
    "TC":   st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
    "Dist": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
    "Fix_to": st.column_config.CheckboxColumn("Fixar altitude no 'To'?"),
    "Alt_to_ft": st.column_config.NumberColumn("Alt 'To' (ft)", step=50, min_value=0.0),
    "Hold_to": st.column_config.CheckboxColumn("HOLD no 'To'?"),
    "Hold_to_min": st.column_config.NumberColumn("Min no HOLD 'To'", step=1.0, min_value=0.0),
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

if combined_submit or dep_arr_submit:
    recs = to_records(combined_edited if combined_submit else st.session_state.combined_rows)
    dep_edit = {"Fix_dep": bool(fix_dep), "Alt_dep": float(alt_dep)}
    arr_edit = {"Fix_arr": bool(fix_arr), "Alt_arr": float(alt_arr)}
    plan_rows, alt_rows = normalize_from_combined(st.session_state.points, recs, dep_edit, arr_edit)
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
    st.success("Plano aplicado (TC/Dist/Altitudes/HOLDs + DEP/ARR).")

# ===== NAVAIDs (opcional) =====
if st.session_state.use_navaids:
    st.subheader("NAVAIDs opcionais (para o PDF)")
    if "nav_rows" not in st.session_state or len(st.session_state.nav_rows) != len(st.session_state.points):
        st.session_state.nav_rows = [{"Point":p,"IDENT":"","FREQ":""} for p in st.session_state.points]
    if [r["Point"] for r in st.session_state.nav_rows] != st.session_state.points:
        old = {i:r for i,r in enumerate(st.session_state.nav_rows)}
        st.session_state.nav_rows = [{"Point":p,"IDENT":old.get(i,{}).get("IDENT",""),
                                      "FREQ":old.get(i,{}).get("FREQ","")} for i,p in enumerate(st.session_state.points)]
    nav_cfg = {"Point": st.column_config.TextColumn("Fix", disabled=True),
               "IDENT": st.column_config.TextColumn("Ident"),
               "FREQ":  st.column_config.TextColumn("Freq")}
    with st.form("navaids_form", clear_on_submit=False):
        edited_nav = st.data_editor(st.session_state.nav_rows, key="navaids_table",
                                    hide_index=True, use_container_width=True, num_rows="fixed",
                                    column_config=nav_cfg, column_order=list(nav_cfg.keys()))
        nav_submit = st.form_submit_button("Aplicar NAVAIDs")
        if nav_submit:
            st.session_state.nav_rows = to_records(edited_nav)
            st.success("NAVAIDs aplicados.")

# =========================================================
# Cálculo (TOC/TOD apenas quando cruzas CRUISE; 1 linha por perna com médias ponderadas)
# =========================================================
points = st.session_state.points
legs   = st.session_state.plan_rows
alts   = st.session_state.alt_rows
N = len(legs)

def pressure_alt(alt_ft, qnh_hpa): return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0
dep_elev  = _round_alt(aero_elev(points[0]))
arr_elev  = _round_alt(aero_elev(points[-1]))
altn_elev = _round_alt(aero_elev(st.session_state.altn))

start_alt = float(alts[0]["Alt_ft"]) if alts else float(dep_elev)
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

def gs_for(i:int, phase:str) -> float:
    wdir,wkt = leg_wind(i)
    tas = vy_kt if phase=="CLIMB" else (tas_cruise if phase=="CRUISE" else tas_descent)
    _,_,gs = wind_triangle(tcs[i], tas, wdir, wkt)
    return max(gs,1e-6)

# ---- Perfil alvo por índice a partir das ALTITUDES APLICADAS ----
A_target=[]
for i,p in enumerate(points):
    r = alts[i] if i < len(alts) else {"Fix":False,"Alt_ft":cruise_alt}
    if bool(r.get("Fix", False)):
        A_target.append(float(r.get("Alt_ft", cruise_alt)))
    else:
        A_target.append(float(cruise_alt))

# ======= Construção (agregando por perna) =======
rows=[]               # tabela principal (sem "DEP")
markers=[]            # TOC/TOD markers
seq_points=[]         # para PDF/relatório
efob=float(st.session_state.start_fuel)

startup = parse_hhmm(st.session_state.startup)
takeoff = add_seconds(startup, int(st.session_state.taxi_min*60)) if startup else None
clock = takeoff

# contagens de fases (inclui sub-segmentos agregados)
phase_secs = {"CLIMB":0, "CRUISE":0, "DESCENT":0, "HOLD":0}
phase_burn = {"CLIMB":0.0, "CRUISE":0.0, "DESCENT":0.0, "HOLD":0.0}

def add_marker(name:str):
    # linha de marcador (TOC/TOD) — sem distância/burn
    global clock
    eto=""
    if clock:
        eto = clock.strftime("%H:%M")
    rows.append({
        "Fase": "•",
        "Leg/Marker": name,
        "ALT (ft)": "",
        "TC (°T)": "", "TH (°T)": "",
        "MC (°M)": "", "MH (°M)": "",
        "TAS (kt)": "", "GS (kt)": "",
        "FF (L/h)": "", "Dist (nm)": "",
        "ETE (mm:ss)": "", "ETO": eto, "Burn (L)": "", "EFOB (L)": fmt(efob,'fuel')
    })
    seq_points.append({"name": name, "phase":"MARK", "eto":eto})

def advance_clock_and_burn(ete_sec_raw: float, ff_lph: float):
    global clock, efob
    ete_sec = round_to_10s(ete_sec_raw)
    burn_raw = ff_lph * (ete_sec_raw/3600.0)
    if clock:
        clock = add_seconds(clock, int(ete_sec))
    efob = max(0.0, _round_tenth(efob - burn_raw))
    return ete_sec, burn_raw

def weighted_avg(values: List[float], weights: List[float]) -> float:
    num = sum(v*w for v,w in zip(values,weights))
    den = sum(weights) if sum(weights)>0 else 1.0
    return num/den

def add_hold(point_name, minutes, i_leg_for_speed):
    global clock, efob
    if minutes<=0: return
    # usa TAS de hold para tabela; estatísticas em fase HOLD
    ete_sec = round_to_10s(minutes*60.0)
    burn_raw = st.session_state.hold_ff_lph * (ete_sec/3600.0)
    eto=""
    if clock: clock = add_seconds(clock, int(ete_sec)); eto=clock.strftime("%H:%M")
    efob = max(0.0, _round_tenth(efob - burn_raw))
    phase_secs["HOLD"] += int(ete_sec)
    phase_burn["HOLD"] += float(burn_raw)

    rows.append({
        "Fase":"⟳", "Leg/Marker": f"HOLD @{point_name}",
        "ALT (ft)": "", "TC (°T)":"", "TH (°T)":"", "MC (°M)":"", "MH (°M)":"",
        "TAS (kt)": _round_unit(st.session_state.hold_ref_kt), "GS (kt)":"",
        "FF (L/h)": _round_unit(st.session_state.hold_ff_lph),
        "Dist (nm)":"", "ETE (mm:ss)": mmss_from_seconds(int(ete_sec)), "ETO": eto,
        "Burn (L)": fmt(burn_raw,'fuel'), "EFOB (L)": fmt(efob,'fuel')
    })
    seq_points.append({"name": f"HOLD @{point_name}", "phase":"HOLD", "ete_sec":int(ete_sec), "burn":float(burn_raw), "eto":eto})

cur_alt = float(A_target[0])
for i in range(N):
    frm, to = legs[i]["From"], legs[i]["To"]
    d_leg = float(dist[i]); tc_i = float(tcs[i])

    # GS por fase
    wdir,wkt = leg_wind(i)
    _,thC,gsC = wind_triangle(tc_i, vy_kt, wdir, wkt)
    _,thR,gsR = wind_triangle(tc_i, float(st.session_state.cruise_ref_kt), wdir, wkt)
    _,thD,gsD = wind_triangle(tc_i, float(st.session_state.descent_ref_kt), wdir, wkt)
    gsC=max(gsC,1e-6); gsR=max(gsR,1e-6); gsD=max(gsD,1e-6)

    alt_to = float(A_target[i+1])
    remain = d_leg

    # Sub-segmentos desta perna (para médias ponderadas)
    seg_speeds_tas=[]; seg_speeds_gs=[]; seg_times=[]
    seg_phase_ff=[]; seg_phase_names=[]

    # ---------- SUBIDA (até CRZ se atravessar, sem marcar TOC se não cruzar CRZ) ----------
    if alt_to > cur_alt + 1e-6:
        # se cruzar CRZ nesta perna -> primeiro até CRZ, marca TOC
        if cur_alt < cruise_alt <= alt_to:
            t_to_crz = (cruise_alt - cur_alt)/max(roc,1e-6)
            d_to_crz = clamp(gsC * (t_to_crz/60.0), 0.0, remain)
            eteC, burnC = advance_clock_and_burn(t_to_crz*60.0, ff_climb)
            seg_speeds_tas.append(vy_kt); seg_speeds_gs.append(gsC); seg_times.append(eteC); seg_phase_ff.append(ff_climb); seg_phase_names.append("CLIMB")
            phase_secs["CLIMB"] += int(eteC); phase_burn["CLIMB"] += float(burnC)
            add_marker("TOC")
            cur_alt = cruise_alt
            remain -= d_to_crz
        # continuar a subir (de CRZ para alvo > CRZ OU de abaixo CRZ até alvo <CRZ sem cruzar CRZ)
        if alt_to > cur_alt + 1e-6 and remain > 1e-9:
            t_up = (alt_to - cur_alt)/max(roc,1e-6)
            d_up = clamp(gsC * (t_up/60.0), 0.0, remain)
            eteC, burnC = advance_clock_and_burn(t_up*60.0, ff_climb)
            seg_speeds_tas.append(vy_kt); seg_speeds_gs.append(gsC); seg_times.append(eteC); seg_phase_ff.append(ff_climb); seg_phase_names.append("CLIMB")
            phase_secs["CLIMB"] += int(eteC); phase_burn["CLIMB"] += float(burnC)
            cur_alt = alt_to
            remain -= d_up

    # ---------- CRUISE ----------
    if abs(cur_alt - cruise_alt) < 1e-6 and abs(alt_to - cruise_alt) < 1e-6 and remain > 1e-9:
        t_cr = (60.0 * remain) / gsR * 60.0  # sec
        eteR, burnR = advance_clock_and_burn(t_cr, ff_cruise)
        seg_speeds_tas.append(float(st.session_state.cruise_ref_kt)); seg_speeds_gs.append(gsR); seg_times.append(eteR); seg_phase_ff.append(ff_cruise); seg_phase_names.append("CRUISE")
        phase_secs["CRUISE"] += int(eteR); phase_burn["CRUISE"] += float(burnR)
        remain = 0.0

    # ---------- DESCIDA ----------
    if alt_to < cur_alt - 1e-6:
        # se sais do CRZ nesta perna -> marca TOD antes de descer
        if abs(cur_alt - cruise_alt) < 1e-6 and alt_to < cruise_alt and remain > 1e-9:
            # coloca TOD e depois toda a descida
            add_marker("TOD")
        if remain > 1e-9:
            # quanto tempo de descida preciso para chegar a alt_to ao fim da perna?
            # se a distância não chegar, ajusto ROD para cumprir (como pediste)
            drop = (cur_alt - alt_to)
            # tempo necessário com ROD atual:
            t_needed_min = drop / max(st.session_state.rod_fpm,1e-6)
            d_needed = gsD * (t_needed_min/60.0)
            if d_needed > remain + 1e-9:
                # ajustar ROD para caber
                rod_adj = drop / max((remain/gsD)*60.0, 1e-6)
            else:
                rod_adj = float(st.session_state.rod_fpm)
            t_down_min = drop / max(rod_adj,1e-6)
            t_down_sec = t_down_min*60.0
            eteD, burnD = advance_clock_and_burn(t_down_sec, ff_descent)
            seg_speeds_tas.append(float(st.session_state.descent_ref_kt)); seg_speeds_gs.append(gsD); seg_times.append(eteD); seg_phase_ff.append(ff_descent); seg_phase_names.append("DESCENT")
            phase_secs["DESCENT"] += int(eteD); phase_burn["DESCENT"] += float(burnD)
            cur_alt = alt_to
            # consumo de distância usado pela descida com rod_adj
            d_use = gsD * (t_down_min/60.0)
            remain = max(0.0, remain - d_use)

    # Se sobrar ‘remain’ sem subida/descida (casos raros), trata como cruise
    if remain > 1e-9:
        t_more = (60.0 * remain) / gsR * 60.0
        eteR, burnR = advance_clock_and_burn(t_more, ff_cruise)
        seg_speeds_tas.append(float(st.session_state.cruise_ref_kt)); seg_speeds_gs.append(gsR); seg_times.append(eteR); seg_phase_ff.append(ff_cruise); seg_phase_names.append("CRUISE")
        phase_secs["CRUISE"] += int(eteR); phase_burn["CRUISE"] += float(burnR)
        remain = 0.0

    # ---- Linha agregada da perna (única) ----
    total_time_sec = sum(seg_times)
    eff_tas = weighted_avg(seg_speeds_tas, seg_times) if seg_times else float(st.session_state.cruise_ref_kt)
    eff_gs  = weighted_avg(seg_speeds_gs,  seg_times) if seg_times else gsR
    eff_ff  = weighted_avg(seg_phase_ff,   seg_times) if seg_times else ff_cruise
    ete_show = round_to_10s(total_time_sec)
    eto_txt = clock.strftime("%H:%M") if clock else ""
    burn_total = sum((ff * (t/3600.0)) for ff,t in zip(seg_phase_ff, seg_times))

    # Cabeçalhos: usa TH/MH do segmento de maior duração (ou cruise se existir)
    dominant_idx = max(range(len(seg_times)), key=lambda k: seg_times[k]) if seg_times else 0
    if seg_times:
        dom_phase = seg_phase_names[dominant_idx]
        if dom_phase=="CLIMB":
            _,th_eff,_ = wind_triangle(tc_i, vy_kt, wdir, wkt)
        elif dom_phase=="DESCENT":
            _,th_eff,_ = wind_triangle(tc_i, float(st.session_state.descent_ref_kt), wdir, wkt)
        else:
            _,th_eff,_ = wind_triangle(tc_i, float(st.session_state.cruise_ref_kt), wdir, wkt)
    else:
        _,th_eff,_ = wind_triangle(tc_i, float(st.session_state.cruise_ref_kt), wdir, wkt)
    tc_eff = tc_i
    mc_eff = apply_var(tc_eff, st.session_state.var_deg, st.session_state.var_is_e)
    mh_eff = apply_var(th_eff, st.session_state.var_deg, st.session_state.var_is_e)

    rows.append({
        "Fase":"→",
        "Leg/Marker": f"{frm}→{to}",
        "ALT (ft)": "",  # perfil detalhado não é mostrado aqui para evitar poluição visual
        "TC (°T)": _round_angle(tc_eff), "TH (°T)": _round_angle(th_eff),
        "MC (°M)": _round_angle(mc_eff), "MH (°M)": _round_angle(mh_eff),
        "TAS (kt)": _round_unit(eff_tas), "GS (kt)": _round_unit(eff_gs),
        "FF (L/h)": _round_unit(eff_ff),
        "Dist (nm)": fmt(d_leg,'dist'),
        "ETE (mm:ss)": mmss_from_seconds(int(ete_show)),
        "ETO": eto_txt,
        "Burn (L)": fmt(burn_total,'fuel'),
        "EFOB (L)": fmt(efob,'fuel')
    })

    # HOLD no destino
    to_row = alts[i+1] if (i+1) < len(alts) else {}
    if bool(to_row.get("Hold")) and float(to_row.get("Hold_min", 0)) > 0:
        add_hold(points[i+1], float(to_row["Hold_min"]), i)

eta = clock
shutdown = add_seconds(eta, 5*60) if eta else None

# =========================================================
# Depuração: Perfil alvo aplicado
# =========================================================
st.subheader("Perfil alvo aplicado (por índice)")
_applied_rows = []
for i,p in enumerate(points):
    r = alts[i] if i < len(alts) else {}
    fixed = bool(r.get("Fix", False))
    altv  = A_target[i]
    _applied_rows.append({"#": i, "Fix?": "✔" if fixed else "—", "Fix name": p, "Alt alvo (ft)": int(round(altv))})
st.dataframe(_applied_rows, use_container_width=True)

# =========================================================
# Resultados
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
    isa_dev = st.session_state.temp_c - isa_temp(pressure_alt(float(alts[0]["Alt_ft"]), st.session_state.qnh))
    st.metric("ISA dev @ DEP (°C)", int(round(isa_dev)))

# TOC/TOD (markers recolhidos acima)
toc_list = [("—", 0.0, m["Leg/Marker"]) for m in rows if m["Leg/Marker"]=="TOC"]  # só para mostrar etiquetas
tod_list = [("—", 0.0, m["Leg/Marker"]) for m in rows if m["Leg/Marker"]=="TOD"]
if any(m["Leg/Marker"]=="TOC" for m in rows): st.write("**TOC** marcado durante a rota.")
if any(m["Leg/Marker"]=="TOD" for m in rows): st.write("**TOD** marcado durante a rota.")

st.dataframe(rows, use_container_width=True)

# Totais (a partir dos acumuladores de fase + holds)
tot_ete_sec = sum(int(m.group(1))*60 for m in [re.match(r"(\d+):(\d+)", r["ETE (mm:ss)"]) for r in rows if r["ETE (mm:ss)"]] if m) \
              + phase_secs["HOLD"]  # ETE de holds já está somado nos acumuladores
tot_nm  = sum(float(r["Dist (nm)"]) for r in rows if r.get("Dist (nm)"))
tot_bo  = _round_tenth(sum(float(r["Burn (L)"]) for r in rows if r.get("Burn (L)")) + phase_burn["HOLD"])
line = f"**Totais** — Dist {fmt(tot_nm,'dist')} nm • ETE {hhmmss_from_seconds(int(tot_ete_sec))} • Burn {fmt(tot_bo,'fuel')} L • EFOB {fmt(efob,'fuel')} L"
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

    PAll(["QNH"], str(int(round(st.session_state.qnh))))
    PAll(["DEPT","DEPARTURE_FREQ","DEPT_FREQ"], aero_freq(points[0]))
    PAll(["ENROUTE","ENROUTE_FREQ"], "123.755")
    PAll(["ARRIVAL","ARRIVAL_FREQ","ARR_FREQ"], aero_freq(points[-1]))
    PAll(["DEPARTURE_AIRFIELD","Departure_Airfield"], points[0])
    PAll(["ARRIVAL_AIRFIELD","Arrival_Airfield"], points[-1])
    PAll(["Leg_Number","LEG_NUMBER"], str(len(points)))
    PAll(["ALTERNATE_AIRFIELD","Alternate_Airfield"], st.session_state.altn)
    PAll(["ALTERNATE_ELEVATION","Alternate_Elevation","TextField_7"], fmt(altn_elev,'alt'))
    PAll(["WIND","WIND_FROM"], f"{int(round(st.session_state.wind_from)):03d}/{int(round(st.session_state.wind_kt)):02d}")
    isa_dev_i = int(round(st.session_state.temp_c - isa_temp(pressure_alt(float(alts[0]['Alt_ft']), st.session_state.qnh))))
    PAll(["TEMP_ISA_DEV","TEMP ISA DEV","TEMP/ISA_DEV"], f"{int(round(st.session_state.temp_c))} / {isa_dev_i}")
    PAll(["MAG_VAR","MAG VAR"], f"{int(round(st.session_state.var_deg))}{'E' if st.session_state.var_is_e else 'W'}")

    # linhas (até 22) — usamos a tabela agregada `rows`
    acc_dist = 0.0; acc_sec = 0
    max_lines = 22
    for idx, r in enumerate(rows[:max_lines], start=1):
        tag = f"Leg{idx:02d}_"
        P(tag+"Waypoint", r["Leg/Marker"])
        if r["Fase"] == "→":
            acc_dist += float(r.get("Dist (nm)",0) or 0.0)
            m = re.match(r"(\d+):(\d+)", r.get("ETE (mm:ss)","") or "")
            ete_sec = (int(m.group(1))*60+int(m.group(2))) if m else 0
            acc_sec += ete_sec
            P(tag+"True_Course",      fmt(r.get("TC (°T)",0),'angle'))
            P(tag+"True_Heading",     fmt(r.get("TH (°T)",0),'angle'))
            P(tag+"Magnetic_Heading", fmt(r.get("MH (°M)",0),'angle'))
            P(tag+"True_Airspeed",    fmt(r.get("TAS (kt)",0),'speed'))
            P(tag+"Ground_Speed",     fmt(r.get("GS (kt)",0),'speed'))
            P(tag+"Leg_Distance",     fmt(r.get("Dist (nm)",0),'dist'))
            P(tag+"Leg_ETE",          r.get("ETE (mm:ss)",""))
            P(tag+"ETO",              r.get("ETO",""))
            P(tag+"Planned_Burnoff",  fmt(r.get("Burn (L)",0),'fuel'))
            P(tag+"Estimated_FOB",    r.get("EFOB (L)",""))
            P(tag+"Cumulative_Distance", fmt(acc_dist,'dist'))
            P(tag+"Cumulative_ETE",      mmss_from_seconds(acc_sec))
        else:
            # marcador/hold
            if r.get("ETO"): P(tag+"ETO", r["ETO"])
            if r.get("EFOB (L)"): P(tag+"Estimated_FOB", r["EFOB (L)"])

    # ===== OBSERVAÇÕES — tempos + holds por ponto =====
    obs_lines = [
        f"Start-up & Taxi: {st.session_state.taxi_min} min @ 20 L/h → {fmt(st.session_state.taxi_ff_lph*(st.session_state.taxi_min/60.0),'fuel')} L",
        f"Climb: {obs_time_str(phase_secs['CLIMB'])} → {fmt(phase_burn['CLIMB'],'fuel')} L",
        f"Enroute (Cruise): {obs_time_str(phase_secs['CRUISE'])} → {fmt(phase_burn['CRUISE'],'fuel')} L",
        f"Descent: {obs_time_str(phase_secs['DESCENT'])} → {fmt(phase_burn['DESCENT'],'fuel')} L",
    ]
    # holds por ponto (sem total)
    for sp in seq_points:
        if sp.get("phase")=="HOLD":
            obs_lines.append(f"HOLD @{sp.get('name','')}: {obs_time_str(int(sp.get('ete_sec',0)))} → {fmt(sp.get('burn',0.0),'fuel')} L")
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
# Relatório (PDF legível)
# =========================================================
st.subheader("Relatório (PDF legível)")
def build_report_pdf():
    if not REPORTLAB_OK: raise RuntimeError("reportlab missing")
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=landscape(A4),
                            leftMargin=16*mm, rightMargin=16*mm,
                            topMargin=12*mm, bottomMargin=12*mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=9.2, leading=12))
    H1=styles["Heading1"]; H2=styles["Heading2"]; Psty=styles["Small"]

    story=[]
    story.append(Paragraph("NAVLOG — Relatório do Planeamento", H1))
    story.append(Spacer(1,4))

    resume = [
        ["DEP / ARR / ALTN", f"{points[0]} / {points[-1]} / {st.session_state.altn}"],
        ["Elev DEP/ARR/ALTN (ft)", f"{fmt(dep_elev,'alt')} / {fmt(arr_elev,'alt')} / {fmt(altn_elev,'alt')}"],
        ["Cruise Alt (ft)", fmt(cruise_alt,'alt')],
        ["Startup / Taxi / ETD", f"{st.session_state.startup} / {st.session_state.taxi_min} min / {(add_seconds(parse_hhmm(st.session_state.startup),st.session_state.taxi_min*60).strftime('%H:%M') if st.session_state.startup else '')}"],
        ["QNH / OAT / ISA dev", f"{int(st.session_state.qnh)} / {int(st.session_state.temp_c)} / {int(round(st.session_state.temp_c - isa_temp(pressure_alt(float(alts[0]['Alt_ft']), st.session_state.qnh))))}"],
        ["Vento FROM / Var", f"{int(round(st.session_state.wind_from)):03d}/{int(round(st.session_state.wind_kt)):02d} / {int(round(st.session_state.var_deg))}{'E' if st.session_state.var_is_e else 'W'}"],
        ["TAS (cl/cru/des)", f"{_round_unit(tas_climb)}/{_round_unit(tas_cruise)}/{_round_unit(tas_descent)} kt"],
        ["FF (cl/cru/des)", f"{_round_unit(ff_climb)}/{_round_unit(ff_cruise)}/{_round_unit(ff_descent)} L/h"],
        ["ROCs/ROD", f"{_round_unit(roc)} ft/min / {_round_unit(st.session_state.rod_fpm)} ft/min"],
        ["Tempos por fase", f"Climb {obs_time_str(phase_secs['CLIMB'])} • Enroute {obs_time_str(phase_secs['CRUISE'])} • Descent {obs_time_str(phase_secs['DESCENT'])} • Holding {obs_time_str(phase_secs['HOLD'])}"],
        ["Totais", f"Dist {fmt(sum(float(r['Dist (nm)']) for r in rows if r.get('Dist (nm)')),'dist')} nm • ETE {hhmmss_from_seconds(int(tot_ete_sec))} • Burn {fmt(tot_bo,'fuel')} L • EFOB {fmt(efob,'fuel')} L"],
        ["TOC/TOD", ("TOC" if any(m['Leg/Marker']=='TOC' for m in rows) else "—") + ("; TOD" if any(m['Leg/Marker']=='TOD' for m in rows) else "") or "—"],
    ]
    t1 = LongTable(resume, colWidths=[64*mm, None], hAlign="LEFT")
    t1.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),0.25,colors.lightgrey),
        ("BACKGROUND",(0,0),(0,-1),colors.whitesmoke),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("FONTSIZE",(0,0),(-1,-1),9),
    ]))
    story.append(t1)
    story.append(PageBreak())

    story.append(Paragraph("Cálculos por perna (agregados)", H2))
    for r in rows:
        if r["Fase"] not in ("→","⟳"):  # markers ou outros
            continue
        head = f"{r['Leg/Marker']}" if r["Fase"]=="→" else f"{r['Leg/Marker']} (HOLD)"
        steps = []
        if r["Fase"]=="→":
            steps = [
                ["1) Curso/vento", f"TC={r['TC (°T)']}°T; TH={r['TH (°T)']}°T; MH={r['MH (°M)']}°M; GS={r['GS (kt)']} kt"],
                ["2) Distância", f"{r['Dist (nm)']} nm"],
                ["3) ETE (10s)", f"{r['ETE (mm:ss)']}"],
                ["4) TAS/FF efetivos (média ponderada)", f"TAS≈{r['TAS (kt)']} kt; FF≈{r['FF (L/h)']} L/h"],
                ["5) Burn / EFOB", f"{r['Burn (L)']} L / {r['EFOB (L)']} L"],
            ]
        else:
            steps = [
                ["1) Tempo de hold", r["ETE (mm:ss)"]],
                ["2) Débito", f"{r['FF (L/h)']} L/h"],
                ["3) Burn / EFOB", f"{r['Burn (L)']} L / {r['EFOB (L)']} L"],
            ]

        t = LongTable([[head,""]]+steps, colWidths=[70*mm, None], hAlign="LEFT")
        t.setStyle(TableStyle([
            ("SPAN",(0,0),(1,0)),("BACKGROUND",(0,0),(1,0),colors.whitesmoke),
            ("GRID",(0,1),(-1,-1),0.25,colors.lightgrey),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("FONTSIZE",(0,0),(-1,-1),9),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),6),
            ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ]))
        story.append(KeepTogether([t, Spacer(1,6)]))

    doc.build(story)
    return bio.getvalue()

if st.button("Gerar Relatório (PDF)"):
    try:
        rep = build_report_pdf()
        m = re.search(r'(\d+)', st.session_state.lesson or "")
        lesson_num = m.group(1) if m else "00"
        safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
        st.download_button("📑 Download Relatório", data=rep,
                           file_name=f"{safe_date}_LESSON-{lesson_num}_NAVLOG_RELATORIO.pdf",
                           mime="application/pdf")
        st.success("Relatório gerado.")
    except Exception as e:
        st.error(f"Erro ao gerar relatório: {e}")





