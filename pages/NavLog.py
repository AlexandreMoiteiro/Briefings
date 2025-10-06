# app.py — NAVLOG (TOC/TOD só para CRUISE, Altitudes por ocorrência, sem duplicar nomes consecutivos, tempos mm:ss, DEP/ARR editáveis, HOLDs, PDF + Relatório)
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

def mmss_from_seconds(sec: int) -> str:
    """Mostra sempre mm:ss mesmo > 60 min (ex: 95 min -> 95:00)."""
    if sec < 0: sec = 0
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"

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

# Altitudes DEP/ARR editáveis
ensure("fix_dep", True)
ensure("alt_dep_ft", _round_alt(aero_elev(st.session_state.dept)))
ensure("fix_arr", True)
ensure("alt_arr_ft", _round_alt(aero_elev(st.session_state.arr)))
ensure("hold_dep", False)
ensure("hold_dep_min", 0.0)
ensure("hold_arr", False)
ensure("hold_arr_min", 0.0)

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
        if f_dep != st.session_state.dept:
            # atualizar defaults de DEP se o aeródromo mudou (sem forçar)
            st.session_state.alt_dep_ft = _round_alt(aero_elev(f_dep))
        if f_arr != st.session_state.arr:
            st.session_state.alt_arr_ft = _round_alt(aero_elev(f_arr))
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
    alt_set  = [ (r.get("Alt_ft") if r.get("Fix") else None) for r in (alts or [{"Fix":False}]*len(pts)) ]
    alt_fix  = [ bool(r.get("Fix", False)) for r in alts ] if alts else [False]*len(pts)
    hold_on  = [ bool(r.get("Hold", False)) for r in alts ] if alts else [False]*len(pts)
    hold_min = [ float(r.get("Hold_min", 0.0)) for r in alts ] if alts else [0.0]*len(pts)
    data = {
        "version": 2,
        "route_points": pts,
        "legs": [{"TC": float(legs[i].get("TC",0.0)), "Dist": float(legs[i].get("Dist",0.0))} for i in range(len(legs))],
        "alt_set_ft": alt_set, "alt_fixed": alt_fix, "alt_hold_on": hold_on, "alt_hold_min": hold_min,
        "dep_fixed": bool(st.session_state.get("fix_dep", True)),
        "dep_alt_ft": float(st.session_state.get("alt_dep_ft", _round_alt(aero_elev(pts[0])))),
        "arr_fixed": bool(st.session_state.get("fix_arr", True)),
        "arr_alt_ft": float(st.session_state.get("alt_arr_ft", _round_alt(aero_elev(pts[-1])))),
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
            dep_fixed = bool(data.get("dep_fixed", True))
            arr_fixed = bool(data.get("arr_fixed", True))
            st.session_state.fix_dep = dep_fixed
            st.session_state.fix_arr = arr_fixed
            st.session_state.alt_dep_ft = float(data.get("dep_alt_ft", _round_alt(aero_elev(pts[0]))))
            st.session_state.alt_arr_ft = float(data.get("arr_alt_ft", _round_alt(aero_elev(pts[-1]))))
            # por índice
            ar=[]
            aset = data.get("alt_set_ft") or []; afix = data.get("alt_fixed") or []
            hOn  = data.get("alt_hold_on") or []; hMin = data.get("alt_hold_min") or []
            for i,p in enumerate(pts):
                row={"Fix":False,"Point":p,"Alt_ft":float(_round_alt(st.session_state.cruise_alt)),"Hold":False,"Hold_min":0.0}
                if i < len(aset) and i < len(afix) and afix[i] and aset[i] is not None:
                    row["Fix"]=True; row["Alt_ft"]=float(aset[i])
                if i<len(hOn) and i<len(hMin):
                    row["Hold"]=bool(hOn[i]); row["Hold_min"]=float(hMin[i])
                ar.append(row)
            # aplicar DEP/ARR overrides
            if len(ar)>=1: ar[0]["Fix"]=dep_fixed; ar[0]["Alt_ft"]=float(st.session_state.alt_dep_ft)
            if len(ar)>=2: ar[-1]["Fix"]=arr_fixed; ar[-1]["Alt_ft"]=float(st.session_state.alt_arr_ft)
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
    # garantir DEP/ARR do estado (editáveis)
    if out:
        out[0]["Fix"]=bool(st.session_state.fix_dep); out[0]["Alt_ft"]=float(st.session_state.alt_dep_ft)
        out[-1]["Fix"]=bool(st.session_state.fix_arr); out[-1]["Alt_ft"]=float(st.session_state.alt_arr_ft)
    return out

def to_records(obj) -> List[dict]:
    try:
        import pandas as pd
        if isinstance(obj, pd.DataFrame):
            obj = obj.fillna(0)
            return [{k: (clean_point_name(v) if k=="Point" else
                         (float(v) if k in ("Alt_to_ft","Alt_ft","Hold_to_min","Hold_min","Dist","TC","Alt_dep_ft","Alt_arr_ft") else
                          bool(v) if k in ("Fix","Hold","Fix_to","Hold_to","Fix_dep","Fix_arr") else v))
                     for k,v in row.items()} for _, row in obj.iterrows()]
    except Exception:
        pass
    if isinstance(obj, list):
        rec=[]
        for r in obj:
            d=dict(r)
            if "Point" in d: d["Point"]=clean_point_name(d.get("Point"))
            for numk in ("Alt_to_ft","Alt_ft","Hold_to_min","Hold_min","Dist","TC","Alt_dep_ft","Alt_arr_ft"):
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
            "Alt_to_ft": float(arow.get("Alt_ft", _round_alt(st.session_state.cruise_alt))) if bool(arow.get("Fix", False)) else "",
            "Hold_to": bool(arow.get("Hold", False)),
            "Hold_to_min": float(arow.get("Hold_min", 0.0)),
        })
    return rows

def normalize_from_combined(points: List[str], edited_rows: List[dict],
                            fix_dep: bool, alt_dep_ft: float, fix_arr: bool, alt_arr_ft: float,
                            hold_dep: bool, hold_dep_min: float, hold_arr: bool, hold_arr_min: float
                            ) -> Tuple[List[dict], List[dict]]:
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
    # alt_rows por índice
    out = []
    for i, p in enumerate(points):
        base = {"Fix": False, "Alt_ft": float(_round_alt(st.session_state.cruise_alt)),
                "Hold": False, "Hold_min": 0.0}
        if i == 0:
            base["Fix"] = bool(fix_dep); base["Alt_ft"] = float(alt_dep_ft)
            base["Hold"] = bool(hold_dep); base["Hold_min"] = float(hold_dep_min)
        elif i == len(points)-1:
            base["Fix"] = bool(fix_arr); base["Alt_ft"] = float(alt_arr_ft)
            base["Hold"] = bool(hold_arr); base["Hold_min"] = float(hold_arr_min)
        else:
            r = edited_rows[i-1] if i-1 < len(edited_rows) else {}
            is_fix = bool(r.get("Fix_to", False))
            base["Fix"] = is_fix
            base["Alt_ft"] = (float(r.get("Alt_to_ft", _round_alt(st.session_state.cruise_alt))) if is_fix
                              else float(_round_alt(st.session_state.cruise_alt)))
            base["Hold"] = bool(r.get("Hold_to", False))
            base["Hold_min"] = float(r.get("Hold_to_min", 0.0))
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
    st.markdown("**DEP/ARR (editáveis)**")
    cda, cdb, cdc, cdd = st.columns(4)
    with cda:
        f_fix_dep = st.checkbox("Fixar DEP?", value=bool(st.session_state.fix_dep))
        f_alt_dep = st.number_input("Alt DEP (ft)", 0.0, 20000.0, float(st.session_state.alt_dep_ft), step=50.0)
    with cdb:
        f_hold_dep = st.checkbox("HOLD no DEP?", value=bool(st.session_state.hold_dep))
        f_hold_dep_min = st.number_input("Min HOLD DEP", 0.0, 180.0, float(st.session_state.hold_dep_min), step=1.0)
    with cdc:
        f_fix_arr = st.checkbox("Fixar ARR?", value=bool(st.session_state.fix_arr))
        f_alt_arr = st.number_input("Alt ARR (ft)", 0.0, 20000.0, float(st.session_state.alt_arr_ft), step=50.0)
    with cdd:
        f_hold_arr = st.checkbox("HOLD no ARR?", value=bool(st.session_state.hold_arr))
        f_hold_arr_min = st.number_input("Min HOLD ARR", 0.0, 180.0, float(st.session_state.hold_arr_min), step=1.0)

    combined_submit = st.form_submit_button("Aplicar plano (TC/Dist/Altitudes/HOLDs)")
    if combined_submit:
        recs = to_records(combined_edited)
        plan_rows, alt_rows = normalize_from_combined(
            st.session_state.points, recs,
            f_fix_dep, f_alt_dep, f_fix_arr, f_alt_arr,
            f_hold_dep, f_hold_dep_min, f_hold_arr, f_hold_arr_min
        )
        st.session_state.plan_rows = plan_rows
        st.session_state.alt_rows  = alt_rows
        st.session_state.combined_rows = build_combined_rows(st.session_state.points, plan_rows, alt_rows)
        # guardar DEP/ARR personalizados
        st.session_state.fix_dep = f_fix_dep; st.session_state.alt_dep_ft = float(f_alt_dep)
        st.session_state.fix_arr = f_fix_arr; st.session_state.alt_arr_ft = float(f_alt_arr)
        st.session_state.hold_dep = f_hold_dep; st.session_state.hold_dep_min = float(f_hold_dep_min)
        st.session_state.hold_arr = f_hold_arr; st.session_state.hold_arr_min = float(f_hold_arr_min)
        if st.session_state.auto_fix_edits:
            crz=_round_alt(st.session_state.cruise_alt)
            for i,r in enumerate(st.session_state.alt_rows):
                try:
                    if abs(float(r.get("Alt_ft", crz)) - float(crz)) >= 1 and not bool(r.get("Fix", False)):
                        r["Fix"] = True
                except Exception:
                    pass
        st.session_state["__alts_applied_at__"] = dt.datetime.utcnow().isoformat()
        st.success("Plano aplicado (TC/Dist/Altitudes/HOLDs).")

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
# Cálculo (perfil com TOC/TOD apenas para CRUISE)
# =========================================================
points = st.session_state.points
legs   = st.session_state.plan_rows
alts   = st.session_state.alt_rows
N = len(legs)

def pressure_alt(alt_ft, qnh_hpa): return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0
# DEP/ARR agora são livres (não forçados à elevação)
dep_alt_cmd = float(alts[0]["Alt_ft"]) if alts else 0.0
arr_alt_cmd = float(alts[-1]["Alt_ft"]) if alts else 0.0
altn_elev = _round_alt(aero_elev(st.session_state.altn))

start_alt = float(dep_alt_cmd)
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

# ---- Perfil alvo por índice (usa alt_rows — DEP/ARR não forçados)
A_target=[]
for i,p in enumerate(points):
    r = alts[i] if i < len(alts) else {"Fix":False,"Alt_ft":cruise_alt}
    if bool(r.get("Fix", False)):
        A_target.append(float(r.get("Alt_ft", cruise_alt)))
    else:
        A_target.append(float(cruise_alt))

# ======= Sequência + nomes (sem duplicar consecutivos) =======
rows=[]; seq_points=[]
efob=float(st.session_state.start_fuel)

startup = parse_hhmm(st.session_state.startup)
takeoff = add_seconds(startup, int(st.session_state.taxi_min*60)) if startup else None
clock = takeoff

_last_label = None
_occ_counter: Dict[str,int] = {}

def _occ_label_actual_fix(name: str) -> str:
    """
    Evita duplicar fixes consecutivos (VACOR -> VACOR), mas
    distingue quando o mesmo fix reaparece mais tarde (VACOR (2)).
    """
    global _last_label
    k = clean_point_name(name)
    # Se for o mesmo imediatamente anterior, não incrementa
    if _last_label == k:
        return name
    _occ_counter[k] = _occ_counter.get(k, 0) + 1
    _last_label = k
    n = _occ_counter[k]
    return f"{name} ({n})" if n > 1 else name

# primeira linha (DEP)
first_label = _occ_label_actual_fix(points[0])
seq_points.append({"name": first_label, "base_name": points[0], "alt": _round_alt(A_target[0]),
                   "tc":"", "th":"", "mc":"", "mh":"", "tas":"", "gs":"", "dist":"",
                   "ete_sec":0, "eto": (takeoff.strftime("%H:%M") if takeoff else ""),
                   "burn":"", "efob": efob, "leg_idx": None, "phase":"DEP"})

def add_seg(phase, frm, to, i_leg, d_nm, tas, ff_lph, alt_start_ft, rate_fpm, is_to_actual_point: bool):
    # cálculo e logging do segmento
    global clock, efob
    if d_nm <= 1e-9: return alt_start_ft
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

    # Label do destino: só aplicar contador a fixes "reais"; TOC/TOD não mexem no contador
    label_to = _occ_label_actual_fix(to) if is_to_actual_point else to
    seq_points.append({
        "name": label_to, "base_name": to, "alt": _round_alt(alt_end),
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

def add_hold(point_name, minutes, alt_now, is_actual_point: bool=True):
    global clock, efob
    if minutes<=0: return alt_now
    ete_sec = round_to_10s(minutes*60.0)
    burn_raw = st.session_state.hold_ff_lph * (ete_sec/3600.0)
    eto = ""
    if clock:
        clock = add_seconds(clock, int(ete_sec))
        eto = clock.strftime("%H:%M")
    # Label do HOLD segue a mesma regra (não duplicar consecutivo)
    label = _occ_label_actual_fix(point_name) if is_actual_point else point_name
    efob_local = max(0.0, _round_tenth(efob - burn_raw))
    rows.append({
        "Fase":"⟳", "Leg/Marker": f"HOLD @{label}",
        "ALT (ft)": f"{fmt(alt_now,'alt')}→{fmt(alt_now,'alt')}",
        "TC (°T)":"", "TH (°T)":"", "MC (°M)":"", "MH (°M)":"",
        "TAS (kt)": _round_unit(st.session_state.hold_ref_kt), "GS (kt)":"",
        "FF (L/h)": _round_unit(st.session_state.hold_ff_lph),
        "Dist (nm)":"", "ETE (mm:ss)": mmss_from_seconds(int(ete_sec)), "ETO": eto,
        "Burn (L)": fmt(burn_raw,'fuel'), "EFOB (L)": fmt(efob_local,'fuel')
    })
    seq_points.append({
        "name": label, "base_name": point_name, "alt": _round_alt(alt_now),
        "tc":"", "th":"", "mc":"", "mh":"",
        "tas": _round_unit(st.session_state.hold_ref_kt), "gs":"",
        "wca": 0.0, "dist":"", "ete_sec": int(ete_sec), "ete_raw": float(ete_sec),
        "eto": eto, "burn": float(burn_raw), "rate_fpm": 0.0,
        "phase":"HOLD", "efob": float(efob_local), "leg_idx": None
    })
    efob = efob_local
    return alt_now

toc_list=[]; tod_list=[]
cur_alt = float(A_target[0])
CRZ = float(st.session_state.cruise_alt)

for i in range(N):
    frm_base, to_base = legs[i]["From"], legs[i]["To"]
    d_leg = float(dist[i]); tc_i = float(tcs[i])

    # GS por fase
    wdir,wkt = leg_wind(i)
    _,_,gsC = wind_triangle(tc_i, vy_kt, wdir, wkt)
    _,_,gsR = wind_triangle(tc_i, float(st.session_state.cruise_ref_kt), wdir, wkt)
    _,_,gsD = wind_triangle(tc_i, float(st.session_state.descent_ref_kt), wdir, wkt)
    gsC = max(gsC,1e-6); gsR=max(gsR,1e-6); gsD=max(gsD,1e-6)

    alt_to = float(A_target[i+1])
    remain = d_leg

    frm = _occ_label_actual_fix(frm_base)  # origem é um fix real
    to_point_is_actual = True

    # ---------- SUBIDA ----------
    if alt_to > cur_alt + 1e-6:
        # TOC só quando atravessa CRZ
        if cur_alt < CRZ <= alt_to:
            t_to_crz = (CRZ - cur_alt) / max(roc,1e-6)
            d_to_crz = clamp(gsC * (t_to_crz/60.0), 0.0, remain)
            if d_to_crz > 1e-9:
                name_toc = "TOC" if not toc_list else f"TOC-{len(toc_list)+1}"
                cur_alt = add_seg("CLIMB", frm, name_toc, i, d_to_crz, vy_kt, ff_climb, cur_alt, roc, is_to_actual_point=False)
                toc_list.append((i, d_to_crz, name_toc))
                frm = name_toc
                remain -= d_to_crz
                cur_alt = CRZ
        # continuar até ao alvo (sem TOC extra)
        if alt_to > cur_alt + 1e-6 and remain > 1e-9:
            t_up = (alt_to - cur_alt)/max(roc,1e-6)
            d_up = clamp(gsC * (t_up/60.0), 0.0, remain)
            # se não esgota a perna, o destino intermediário não é um fix real
            to_point_is_actual = abs(d_up-remain) < 1e-9
            cur_alt = add_seg("CLIMB", frm, to_base if to_point_is_actual else "CLB", i, d_up, vy_kt, ff_climb, cur_alt, roc, is_to_actual_point=to_point_is_actual)
            frm = to_base if to_point_is_actual else frm
            remain -= d_up
            cur_alt = alt_to

    # ---------- DESCIDA ----------
    elif alt_to < cur_alt - 1e-6:
        # TOD apenas quando sais do CRZ
        if abs(cur_alt - CRZ) < 1e-6 and alt_to < CRZ and remain > 1e-9:
            t_down = (cur_alt - alt_to) / max(st.session_state.rod_fpm,1e-6)
            d_down = gsD * (t_down/60.0)
            if d_down < remain - 1e-9:
                d_cruise = remain - d_down
                name_tod = "TOD" if not tod_list else f"TOD-{len(tod_list)+1}"
                cur_alt = add_seg("CRUISE", frm, name_tod, i, d_cruise, float(st.session_state.cruise_ref_kt), ff_cruise, cur_alt, 0.0, is_to_actual_point=False)
                tod_list.append((i, d_cruise, name_tod))
                frm = name_tod
                remain = d_down
        if remain > 1e-9:
            cur_alt = add_seg("DESCENT", frm, to_base, i, remain, float(st.session_state.descent_ref_kt), ff_descent, cur_alt, float(st.session_state.rod_fpm), is_to_actual_point=True)
            frm = to_base; remain = 0.0
            cur_alt = alt_to

    # ---------- CRUISE ----------
    if remain > 1e-9 and frm != to_base:
        cur_alt = add_seg("CRUISE", frm, to_base, i, remain, float(st.session_state.cruise_ref_kt), ff_cruise, cur_alt, 0.0, is_to_actual_point=True)
        remain = 0.0

    # ---------- HOLD no destino (índice i+1) ----------
    to_row = alts[i+1] if (i+1) < len(alts) else {}
    if bool(to_row.get("Hold")) and float(to_row.get("Hold_min", 0)) > 0:
        cur_alt = add_hold(points[i+1], float(to_row["Hold_min"]), cur_alt, is_actual_point=True)

# HOLD no DEP/ARR (se marcado nos controlos)
if bool(st.session_state.hold_dep) and float(st.session_state.hold_dep_min) > 0:
    _ = add_hold(points[0], float(st.session_state.hold_dep_min), float(A_target[0]), is_actual_point=True)
if bool(st.session_state.hold_arr) and float(st.session_state.hold_arr_min) > 0:
    _ = add_hold(points[-1], float(st.session_state.hold_arr_min), float(A_target[-1]), is_actual_point=True)

eta = clock
shutdown = add_seconds(eta, 5*60) if eta else None

# ==== Totais ====
phase_secs = {"CLIMB":0, "CRUISE":0, "DESCENT":0, "HOLD":0}
phase_burn = {"CLIMB":0.0, "CRUISE":0.0, "DESCENT":0.0, "HOLD":0.0}
for p in seq_points:
    ph = p.get("phase")
    if ph in phase_secs:
        phase_secs[ph] += int(p.get("ete_sec",0))
        phase_burn[ph] += float(p.get("burn",0.0))

taxi_min = int(st.session_state.taxi_min)
fuel_taxi = st.session_state.taxi_ff_lph * (taxi_min / 60.0)

# Holds por ocorrência (nome com sufixo (n) só quando reaparece mais tarde)
holds_by_occ = {}
for p in seq_points:
    if p.get("phase")=="HOLD":
        nm = p.get("name","")
        holds_by_occ.setdefault(nm, {"sec":0,"burn":0.0})
        holds_by_occ[nm]["sec"]  += int(p.get("ete_sec",0))
        holds_by_occ[nm]["burn"] += float(p.get("burn",0.0))

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
    total_secs = sum(phase_secs.values())
    total_fuel = sum(phase_burn.values()) + fuel_taxi
    st.metric("Tempo total", mmss_from_seconds(total_secs))
    st.metric("Consumo total (L)", f"{_round_tenth(total_fuel)}")
    st.metric("EFOB final (L)", f"{_round_tenth(efob)}")

# =========================================================
# NAVLOG final (linhas + holds)
# =========================================================
st.subheader("NAVLOG completo")
st.dataframe(rows, use_container_width=True)

# =========================================================
# Observações (resumo por fase + holds)
# =========================================================
st.subheader("Observações / Totais por fase")

obs_table = []
for ph in ["CLIMB","CRUISE","DESCENT","HOLD"]:
    if phase_secs[ph] > 0 or phase_burn[ph] > 0:
        obs_table.append({
            "Fase": ph,
            "Tempo (mm:ss)": mmss_from_seconds(phase_secs[ph]),
            "Consumo (L)": f"{_round_tenth(phase_burn[ph])}"
        })
if holds_by_occ:
    obs_table.append({"Fase": "—", "Tempo (mm:ss)": "", "Consumo (L)": ""})
    for k,v in holds_by_occ.items():
        obs_table.append({
            "Fase": f"HOLD @{k}",
            "Tempo (mm:ss)": mmss_from_seconds(v["sec"]),
            "Consumo (L)": f"{_round_tenth(v['burn'])}"
        })

st.dataframe(obs_table, use_container_width=True)

# =========================================================
# PDF & Relatório
# =========================================================
st.subheader("Gerar PDF / Relatório")

pdf_ready = PYPDF_OK and REPORTLAB_OK
if not pdf_ready:
    st.warning("⚠️ pypdf e reportlab são necessários para gerar PDF.")
else:
    cpdf1, cpdf2 = st.columns(2)
    with cpdf1:
        if st.button("📄 Gerar NAVLOG (PDF Form)"):
            try:
                pdf_bytes = read_pdf_bytes(tuple(PDF_TEMPLATE_PATHS))
                fieldset, maxlens = get_form_fields(pdf_bytes)

                pdf_fields = {}
                put(pdf_fields, fieldset, "ACFT", st.session_state.registration, maxlens)
                put(pdf_fields, fieldset, "CALLSIGN", st.session_state.callsign, maxlens)
                put(pdf_fields, fieldset, "ROUTE", st.session_state.route_text, maxlens)
                put(pdf_fields, fieldset, "STUDENT", st.session_state.student, maxlens)
                put(pdf_fields, fieldset, "INSTRUCTOR", st.session_state.instrutor, maxlens)
                put(pdf_fields, fieldset, "LESSON", st.session_state.lesson, maxlens)
                put(pdf_fields, fieldset, "DEP", st.session_state.dept, maxlens)
                put(pdf_fields, fieldset, "ARR", st.session_state.arr, maxlens)
                put(pdf_fields, fieldset, "ALT", st.session_state.altn, maxlens)
                put(pdf_fields, fieldset, "CRZ_ALT", int(st.session_state.cruise_alt), maxlens)
                put(pdf_fields, fieldset, "WIND", f"{int(st.session_state.wind_from)}/{int(st.session_state.wind_kt)}", maxlens)
                put(pdf_fields, fieldset, "QNH", int(st.session_state.qnh), maxlens)
                put(pdf_fields, fieldset, "OAT", int(st.session_state.temp_c), maxlens)

                # Preencher campos de legs (se houver correspondência no PDF)
                for i, r in enumerate(rows):
                    for k, v in r.items():
                        field_key = f"{k}_{i+1}"
                        put(pdf_fields, fieldset, field_key, v, maxlens)

                filled_bytes = fill_pdf(pdf_bytes, pdf_fields)
                pdf_filename = f"NAVLOG_{st.session_state.callsign}_{st.session_state.dept}_{st.session_state.arr}.pdf"
                st.download_button("⬇️ Download PDF preenchido", data=filled_bytes, file_name=pdf_filename, mime="application/pdf")
            except Exception as e:
                st.error(f"Erro ao gerar PDF: {e}")

    with cpdf2:
        if st.button("🧾 Gerar Relatório (PDF texto)"):
            try:
                buffer = io.BytesIO()
                doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
                styles = getSampleStyleSheet()
                story = []

                story.append(Paragraph(f"<b>Navigation Log — {st.session_state.callsign}</b>", styles["Title"]))
                story.append(Spacer(1, 6))
                story.append(Paragraph(f"<b>Route:</b> {st.session_state.route_text}", styles["Normal"]))
                story.append(Paragraph(f"<b>Student:</b> {st.session_state.student} — <b>Lesson:</b> {st.session_state.lesson}", styles["Normal"]))
                story.append(Paragraph(f"<b>Instructor:</b> {st.session_state.instrutor}", styles["Normal"]))
                story.append(Spacer(1, 6))
                story.append(Paragraph(f"<b>DEP:</b> {st.session_state.dept} — <b>ARR:</b> {st.session_state.arr} — <b>ALT:</b> {st.session_state.altn}", styles["Normal"]))
                story.append(Paragraph(f"<b>CRZ:</b> {int(st.session_state.cruise_alt)} ft — <b>OAT:</b> {int(st.session_state.temp_c)} °C — <b>Wind:</b> {int(st.session_state.wind_from)}°/{int(st.session_state.wind_kt)} kt", styles["Normal"]))
                story.append(Spacer(1, 12))

                # Tabela NAVLOG
                tbl_data = [list(rows[0].keys())] + [[str(v) for v in r.values()] for r in rows]
                tbl = LongTable(tbl_data, repeatRows=1)
                tbl.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.grey),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                    ('GRID', (0,0), (-1,-1), 0.25, colors.black),
                    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold')
                ]))
                story.append(KeepTogether(tbl))
                story.append(PageBreak())

                # Tabela Observações
                story.append(Paragraph("<b>Resumo por Fase / Holds</b>", styles["Heading2"]))
                obs_tbl_data = [list(obs_table[0].keys())] + [[str(v) for v in r.values()] for r in obs_table]
                obs_tbl = LongTable(obs_tbl_data, repeatRows=1)
                obs_tbl.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.grey),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                    ('GRID', (0,0), (-1,-1), 0.25, colors.black),
                    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold')
                ]))
                story.append(obs_tbl)
                story.append(Spacer(1, 12))

                story.append(Paragraph(f"<b>Total Time:</b> {mmss_from_seconds(total_secs)} — <b>Total Fuel:</b> {_round_tenth(total_fuel)} L — <b>EFOB final:</b> {_round_tenth(efob)} L", styles["Normal"]))

                doc.build(story)
                pdf_data = buffer.getvalue()
                buffer.close()
                pdf_name = f"NAVLOG_REPORT_{st.session_state.callsign}_{st.session_state.dept}_{st.session_state.arr}.pdf"
                st.download_button("⬇️ Download Relatório PDF", data=pdf_data, file_name=pdf_name, mime="application/pdf")
            except Exception as e:
                st.error(f"Erro ao gerar relatório: {e}")

# =========================================================
# Fim
# =========================================================
st.markdown("---")
st.caption("NAVLOG app — ajustado para TOC/TOD só para cruzeiro, nomes não duplicados, tempos mm:ss e altitudes DEP/ARR editáveis.")

    
