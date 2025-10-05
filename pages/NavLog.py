# app.py — NAVLOG (TOC/TOD dinâmicos, vento por fix opcional, auto-commit & sync com vento geral)
# Reqs: streamlit, pypdf, reportlab, pytz

import streamlit as st
import datetime as dt
import pytz, io, json, unicodedata, re, math
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from math import sin, asin, radians, degrees, fmod, cos

st.set_page_config(page_title="NAVLOG (PDF + Relatório)", layout="wide", initial_sidebar_state="collapsed")
PDF_TEMPLATE_PATHS = ["NAVLOG_FORM.pdf", "NAVLOG_FORM.pdf"]  # procura local

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

# ===== Helpers: rounding & fmt =====
def _round_alt(x: float) -> int:
    if x is None: return 0
    v = abs(float(x))
    base = 50 if v < 1000 else 100
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
    """Múltiplo de 10 s mais próximo (mínimo 10 s se >0)."""
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

# ===== Utils =====
def ascii_safe(x: str) -> str:
    return unicodedata.normalize("NFKD", str(x or "")).encode("ascii","ignore").decode("ascii")

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
    cross = wind_kt * sin(delta)           # vento FROM
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

# ======= FONT PATCH (com toggle e correção de IndirectObject) =======
from pypdf.generic import (
    DictionaryObject, NameObject, TextStringObject,
    IndirectObject, ArrayObject
)

def _embed_arm_into_writer(writer: "PdfWriter", use_arm_font: bool):
    """
    Quando use_arm_font=True:
      - embute Arm-Regular.ttf (ficheiro local pelo nome)
      - mapeia /AcroForm/DR/Font/Arm
      - define /DA global e por widget como '/Arm 10 Tf 0 g'
      - recria cada widget e substitui no array /Annots (evita 'IndirectObject' assignment)
    """
    # Garantir AcroForm
    acroform = writer._root_object.get("/AcroForm")
    if acroform is None:
        acroform = DictionaryObject()
        writer._root_object[NameObject("/AcroForm")] = writer._add_object(acroform)

    def _deref(obj):
        while isinstance(obj, IndirectObject):
            obj = obj.get_object()
        return obj

    if not use_arm_font:
        acroform[NameObject("/NeedAppearances")] = True
        acroform[NameObject("/DA")] = TextStringObject("/Helv 10 Tf 0 g")
        return

    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas

        # localizar TTF ao lado do app
        font_path = None
        for p in ("Arm-Regular.ttf", "./Arm-Regular.ttf"):
            if Path(p).exists():
                font_path = p; break
        if not font_path:
            raise FileNotFoundError("Arm-Regular.ttf não encontrada no diretório do app.")

        # construir mini-PDF para materializar a fonte
        bio = io.BytesIO()
        pdfmetrics.registerFont(TTFont("Arm", font_path))
        c = canvas.Canvas(bio)
        c.setFont("Arm", 10); c.drawString(10, 10, "."); c.save()
        bio.seek(0)
        carrier = PdfReader(bio)

        res = _deref(carrier.pages[0]).get("/Resources")
        font_res = _deref(res).get("/Font") if res else None
        if not font_res:
            raise RuntimeError("Falha a extrair recurso de fonte do carrier.")

        # /AcroForm/DR/Font
        dr = acroform.get("/DR")
        if dr is None:
            dr = DictionaryObject(); acroform[NameObject("/DR")] = dr
        dr_font = dr.get("/Font")
        if dr_font is None:
            dr_font = DictionaryObject(); dr[NameObject("/Font")] = dr_font

        any_font_obj = next(iter(font_res.values()))
        arm_font_ref = writer._add_object(_deref(any_font_obj))
        dr_font[NameObject("/Arm")] = arm_font_ref

        # /DA global
        acroform[NameObject("/DA")] = TextStringObject("/Arm 10 Tf 0 g")
        acroform[NameObject("/NeedAppearances")] = True

        # Recriar widgets e substituir no /Annots
        for page in writer.pages:
            annots_ref = page.get("/Annots")
            if not annots_ref:
                continue
            annots = _deref(annots_ref)
            if not isinstance(annots, ArrayObject):
                continue

            for idx, a in enumerate(list(annots)):
                annot = _deref(a)
                if not isinstance(annot, DictionaryObject):
                    continue
                if annot.get("/Subtype") != "/Widget":
                    continue

                # novo dicionário do widget, sem AP/DA/DR antigos
                new_annot = DictionaryObject()
                for k, v in annot.items():
                    if k in (NameObject("/AP"), NameObject("/DA"), NameObject("/DR")):
                        continue
                    new_annot[NameObject(k)] = v

                # /DA do widget
                new_annot[NameObject("/DA")] = TextStringObject("/Arm 10 Tf 0 g")

                # /DR/Font do widget
                wdr = DictionaryObject()
                wdr_font = DictionaryObject()
                wdr_font[NameObject("/Arm")] = arm_font_ref
                wdr[NameObject("/Font")] = wdr_font
                new_annot[NameObject("/DR")] = wdr

                # adicionar ao writer e substituir a referência no array /Annots
                new_ref = writer._add_object(new_annot)
                annots[idx] = new_ref

    except Exception:
        # fallback para Helv se algo correr mal
        acroform[NameObject("/NeedAppearances")] = True
        acroform[NameObject("/DA")] = TextStringObject("/Helv 10 Tf 0 g")

def fill_pdf(template_bytes: bytes, fields: dict, use_arm_font: bool) -> bytes:
    if not PYPDF_OK: raise RuntimeError("pypdf missing")
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()
    if hasattr(writer, "clone_document_from_reader"):
        writer.clone_document_from_reader(reader)
    else:
        for p in reader.pages:
            writer.add_page(p)
        acro = reader.trailer["/Root"].get("/AcroForm")
        if acro is not None:
            writer._root_object.update({NameObject("/AcroForm"): acro})

    # Embedding/config de fonte (toggle)
    _embed_arm_into_writer(writer, use_arm_font)

    # Preencher campos
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
# Estado inicial + defaults
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
ensure("use_arm_font", False)  # TOGGLE DA FONTE ARM

# Taxi
ensure("taxi_min",15)
ensure("taxi_ff_lph",20.0)

# Vento por fix
ensure("use_wind_by_fix", False)
ensure("last_use_wind_by_fix", False)
ensure("last_wind_from", float(st.session_state.wind_from))
ensure("last_wind_kt",   float(st.session_state.wind_kt))
if "wind_rows" not in st.session_state:
    st.session_state.wind_rows = []

# Rota / tabelas base
def parse_route_text(txt:str) -> List[str]:
    tokens = re.split(r"[,\s→\-]+", (txt or "").strip())
    return [t for t in tokens if t]

default_route = f"{st.session_state.dept} {st.session_state.arr}"
ensure("route_text", default_route)

if "points" not in st.session_state:
    st.session_state.points = parse_route_text(st.session_state.route_text) or [st.session_state.dept, st.session_state.arr]

def rebuild_plan_rows(points: List[str], prev: Optional[List[dict]]):
    prev_map = {(r["From"],r["To"]):r for r in (prev or [])}
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
    dep_e=_round_alt(AEROS.get(points[0],{}).get("elev",0))
    arr_e=_round_alt(AEROS.get(points[-1],{}).get("elev",0))
    prev_map={r["Point"]:r for r in (prev or [])}
    out=[]
    for i,p in enumerate(points):
        if p in prev_map:
            r=prev_map[p].copy()
            if i==0: r["Fix"]=True;  r["Alt_ft"]=float(dep_e)
            elif i==len(points)-1: r["Fix"]=True; r["Alt_ft"]=float(arr_e)
            out.append(r); continue
        if i==0: out.append({"Fix":True,"Point":p,"Alt_ft":float(dep_e)})
        elif i==len(points)-1: out.append({"Fix":True,"Point":p,"Alt_ft":float(arr_e)})
        else: out.append({"Fix":False,"Point":p,"Alt_ft":float(_round_alt(cruise))})
    return out

if "plan_rows" not in st.session_state:
    st.session_state.plan_rows = rebuild_plan_rows(st.session_state.points, None)
if "alt_rows" not in st.session_state:
    st.session_state.alt_rows = rebuild_alt_rows(st.session_state.points, st.session_state.cruise_alt, None)
if "wind_rows" not in st.session_state or len(st.session_state.wind_rows) != len(st.session_state.points):
    st.session_state.wind_rows = [{"Point":p, "FROM": float(st.session_state.wind_from), "KT": float(st.session_state.wind_kt)}
                                  for p in st.session_state.points]

# =========================================================
# UI — formulário de cabeçalho / parâmetros
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
    with c11:
        f_spd_cr  = st.number_input("Cruise speed (kt)", 40, 140, int(st.session_state.cruise_ref_kt), step=1)
        f_spd_ds  = st.number_input("Descent speed (kt)", 40, 120, int(st.session_state.descent_ref_kt), step=1)
    with c12:
        f_use_wbf = st.checkbox("Vento por fix?", value=bool(st.session_state.use_wind_by_fix))
        st.caption("Quando ativo, define Wind FROM/kt por fix; cálculo usa média vetorial por leg.")
        f_use_arm = st.checkbox("Usar fonte ARM (TTF) nos campos do PDF", value=bool(st.session_state.use_arm_font))

    submitted = st.form_submit_button("Aplicar cabeçalho + performance")
    if submitted:
        prev_use = bool(st.session_state.use_wind_by_fix)
        prev_wf  = float(st.session_state.wind_from)
        prev_wk  = float(st.session_state.wind_kt)

        # guardar
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
        st.session_state.use_wind_by_fix=f_use_wbf
        st.session_state.use_arm_font=f_use_arm

        # sync vento por fix
        pts = st.session_state.get("points") or [st.session_state.dept, st.session_state.arr]
        def fill_all_winds_from_general():
            st.session_state.wind_rows = [
                {"Point":p, "FROM": float(st.session_state.wind_from), "KT": float(st.session_state.wind_kt)}
                for p in pts
            ]
        if f_use_wbf and not prev_use:
            fill_all_winds_from_general()
        def all_rows_equal_to(from_deg, kt):
            wr = st.session_state.get("wind_rows") or []
            return len(wr)==len(pts) and all(abs(float(r.get("FROM",from_deg))-from_deg)<1e-9 and
                                             abs(float(r.get("KT",kt))-kt)<1e-9 for r in wr)
        if f_use_wbf and prev_use and ((float(f_wdir)!=prev_wf) or (float(f_wkt)!=prev_wk)):
            if all_rows_equal_to(prev_wf, prev_wk):
                fill_all_winds_from_general()

        st.session_state.last_use_wind_by_fix = f_use_wbf
        st.session_state.last_wind_from = float(st.session_state.wind_from)
        st.session_state.last_wind_kt   = float(st.session_state.wind_kt)

        st.success("Parâmetros aplicados.")

# =========================================================
# Export / Import JSON v2
# =========================================================
st.subheader("Export / Import JSON v2 (rota, TCs/Dist, Altitudes por fix)")

def current_points():
    return st.session_state.get("points") or [st.session_state.dept, st.session_state.arr]

def export_json_v2():
    pts   = current_points()
    legs  = st.session_state.get("plan_rows") or []
    alts  = st.session_state.get("alt_rows")  or []
    alt_set  = [ (r.get("Alt_ft") if r.get("Fix") or i in (0, len(pts)-1) else None)
                 for i,r in enumerate(alts) ] if alts else [None]*len(pts)
    alt_fix  = [ bool(r.get("Fix", False)) for r in alts ] if alts else [False]*len(pts)
    data = {
        "version": 2,
        "route_points": pts,
        "legs": [{"TC": float(legs[i].get("TC",0.0)), "Dist": float(legs[i].get("Dist",0.0))}
                 for i in range(len(legs))],
        "alt_set_ft": alt_set,
        "alt_fixed":  alt_fix,
    }
    dep_code = ascii_safe(pts[0]); arr_code = ascii_safe(pts[-1])
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
            pts = data.get("route_points") or current_points()
            st.session_state.dept, st.session_state.arr = pts[0], pts[-1]
            st.session_state.points = pts
            st.session_state.route_text = " ".join(pts)
            st.session_state.plan_rows = rebuild_plan_rows(pts, st.session_state.get("plan_rows"))
            st.session_state.alt_rows  = rebuild_alt_rows(pts, st.session_state.cruise_alt, st.session_state.get("alt_rows"))
            old_w = {r.get("Point"): r for r in (st.session_state.get("wind_rows") or [])}
            st.session_state.wind_rows = [{"Point":p,
                                           "FROM": float(old_w.get(p,{}).get("FROM", st.session_state.wind_from)),
                                           "KT":   float(old_w.get(p,{}).get("KT",   st.session_state.wind_kt))}
                                          for p in pts]
            st.success("Rota importada e aplicada.")
        except Exception as e:
            st.error(f"Falha a importar JSON: {e}")

# =========================================================
# Rota + Tabelas (com auto-commit)
# =========================================================
route_text = st.text_area("Rota (DEP … ARR)", value=st.session_state.get("route_text"))

if st.button("Aplicar rota"):
    pts = parse_route_text(route_text) or [st.session_state.dept, st.session_state.arr]
    pts[0]=st.session_state.dept
    if len(pts)>=2: pts[-1]=st.session_state.arr
    st.session_state.points = pts
    st.session_state.route_text = " ".join(pts)
    st.session_state.plan_rows = rebuild_plan_rows(pts, st.session_state.get("plan_rows"))
    st.session_state.alt_rows  = rebuild_alt_rows(pts, st.session_state.cruise_alt, st.session_state.get("alt_rows"))
    old_w = {r.get("Point"): r for r in (st.session_state.get("wind_rows") or [])}
    st.session_state.wind_rows = [{"Point":p,
                                   "FROM": float(old_w.get(p,{}).get("FROM", st.session_state.wind_from)),
                                   "KT":   float(old_w.get(p,{}).get("KT",   st.session_state.wind_kt))}
                                  for p in pts]
    st.success("Rota aplicada.")

# Data editors
st.subheader("Legs (TC/Dist)")
leg_cfg = {
    "From": st.column_config.TextColumn("From", disabled=True),
    "To":   st.column_config.TextColumn("To", disabled=True),
    "TC":   st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
    "Dist": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
}
plan_df = st.data_editor(
    st.session_state.plan_rows, key="plan_table",
    hide_index=True, use_container_width=True, num_rows="fixed",
    column_config=leg_cfg, column_order=list(leg_cfg.keys())
)

st.subheader("Altitudes por Fix")
st.caption("Marcar **Fixar?** = altitude *obrigatória*. Não marcado = segue o perfil (Cruise por defeito). DEP/ARR fixos às elevações.")
alt_cfg = {
    "Fix":   st.column_config.CheckboxColumn("Fixar?"),
    "Point": st.column_config.TextColumn("Fix", disabled=True),
    "Alt_ft": st.column_config.NumberColumn("Altitude alvo (ft)", step=50, min_value=0.0),
}
alt_df = st.data_editor(
    st.session_state.alt_rows, key="alt_table",
    hide_index=True, use_container_width=True, num_rows="fixed",
    column_config=alt_cfg, column_order=list(alt_cfg.keys())
)

# VENTO POR FIX — editor + sync
if st.session_state.use_wind_by_fix:
    st.subheader("Vento por Fix")
    cwb1, cwb2 = st.columns([3,1])
    with cwb1:
        wind_cfg = {
            "Point": st.column_config.TextColumn("Fix", disabled=True),
            "FROM":  st.column_config.NumberColumn("Wind FROM (°TRUE)", min_value=0.0, max_value=360.0, step=1.0),
            "KT":    st.column_config.NumberColumn("Wind (kt)", min_value=0.0, max_value=200.0, step=1.0),
        }
        wind_df = st.data_editor(
            st.session_state.wind_rows, key="wind_by_fix_table",
            hide_index=True, use_container_width=True, num_rows="fixed",
            column_config=wind_cfg, column_order=list(wind_cfg.keys())
        )
    with cwb2:
        if st.button("🔁 Aplicar vento geral a todos os fixes"):
            st.session_state.wind_rows = [{"Point":r["Point"], "FROM": float(st.session_state.wind_from), "KT": float(st.session_state.wind_kt)}
                                          for r in st.session_state.wind_rows]
            st.success("Vento geral aplicado a todos os fixes.")

# ===== AUTO-COMMIT =====
def apply_pending_edits():
    if "plan_table" in st.session_state and isinstance(st.session_state.plan_table, list):
        st.session_state.plan_rows = st.session_state.plan_table
    if "alt_table" in st.session_state and isinstance(st.session_state.alt_table, list):
        st.session_state.alt_rows = st.session_state.alt_table
    if st.session_state.use_wind_by_fix and "wind_by_fix_table" in st.session_state and isinstance(st.session_state.wind_by_fix_table, list):
        st.session_state.wind_rows = st.session_state.wind_by_fix_table
apply_pending_edits()

# =========================================================
# Cálculo (perfil com TOC/TOD dinâmicos)
# =========================================================
points = st.session_state.points
legs   = st.session_state.plan_rows
alts   = st.session_state.alt_rows
winds  = st.session_state.wind_rows
N = len(legs)

def pressure_alt(alt_ft, qnh_hpa): return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0
dep_elev  = _round_alt(AEROS.get(points[0],{}).get("elev",0))
arr_elev  = _round_alt(AEROS.get(points[-1],{}).get("elev",0))
altn_elev = _round_alt(AEROS.get(st.session_state.altn,{}).get("elev",0))

start_alt = float(dep_elev)
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

def wind_vec_from(dir_deg: float, spd: float) -> Tuple[float,float]:
    th = radians(dir_deg % 360.0)
    u = -spd * sin(th)  # negativo porque é "from"
    v = -spd * cos(th)
    return u, v

def vec_to_from(u: float, v: float) -> Tuple[float,float]:
    spd = math.hypot(u, v)
    if spd < 1e-9:
        return 0.0, 0.0
    th_to = math.atan2(v, u)
    th_from = (th_to + math.pi) % (2*math.pi)
    return (degrees(th_from) % 360.0), spd

def leg_wind(i:int) -> Tuple[float,float]:
    if not st.session_state.use_wind_by_fix or not winds or len(winds) != len(points):
        return (float(st.session_state.wind_from), float(st.session_state.wind_kt))
    frm = legs[i]["From"]; to = legs[i]["To"]
    idx_from = points.index(frm); idx_to = points.index(to)
    wf = winds[idx_from]; wt = winds[idx_to]
    u1,v1 = wind_vec_from(float(wf.get("FROM", st.session_state.wind_from)), float(wf.get("KT", st.session_state.wind_kt)))
    u2,v2 = wind_vec_from(float(wt.get("FROM",   st.session_state.wind_from)), float(wt.get("KT",   st.session_state.wind_kt)))
    u = 0.5*(u1+u2); v = 0.5*(v1+v2)
    return vec_to_from(u, v)

def gs_for(i:int, phase:str) -> float:
    wdir,wkt = leg_wind(i)
    tas = vy_kt if phase=="CLIMB" else (tas_cruise if phase=="CRUISE" else tas_descent)
    _,_,gs = wind_triangle(tcs[i], tas, wdir, wkt)
    return max(gs,1e-6)

# climb frente
front_climb = [0.0]*N
t_need_climb = max(0.0, cruise_alt - start_alt) / max(roc,1e-6)
rem = t_need_climb
for i in range(N):
    if rem <= 1e-9: break
    t_full = 60.0 * dist[i] / gs_for(i,"CLIMB")
    use = min(rem, t_full)
    front_climb[i] = gs_for(i,"CLIMB") * use / 60.0
    rem -= use

# descidas atrás
back_desc = [0.0]*N
hard_map = {idx: float(r["Alt_ft"]) for idx,r in enumerate(alts) if bool(r.get("Fix"))}
hard_map[0] = start_alt
hard_map[len(points)-1] = float(arr_elev)

def alloc_back_until(index_fix:int, drop_ft:float):
    if drop_ft <= 0: return 0.0
    t_need = drop_ft / max(st.session_state.rod_fpm,1e-6)
    rem = t_need
    for j in range(index_fix-1, -1, -1):
        if rem <= 1e-9: break
        t_full = 60.0 * (dist[j] - back_desc[j] - front_climb[j]) / gs_for(j,"DESCENT")
        use = max(0.0, min(rem, t_full))
        d   = gs_for(j,"DESCENT") * use / 60.0
        back_desc[j] += d
        rem -= use
    return rem

impossible_notes=[]
for fix_idx, alt_ft in sorted(hard_map.items()):
    if fix_idx==0: continue
    if alt_ft < cruise_alt:
        miss = alloc_back_until(fix_idx, cruise_alt - alt_ft)
        if miss and miss>1e-6:
            impossible_notes.append(f"Impossível descer para {int(alt_ft)} ft em {points[fix_idx]} (faltam {miss:.1f} min).")

# overlap climb vs descent
for i in range(N-1, -1, -1):
    overlap = max(0.0, front_climb[i] + back_desc[i] - dist[i])
    if overlap>1e-9:
        take = min(overlap, front_climb[i]); front_climb[i] -= take; overlap -= take
        k=i-1
        while overlap>1e-9 and k>=0:
            can = min(overlap, front_climb[k])
            front_climb[k]-=can; overlap-=can; k-=1

# TOC/TOD
toc_positions=[]; tod_positions=[]
for i in range(N):
    d = dist[i]; d_cl = min(front_climb[i], d)
    d_ds = min(back_desc[i],   d - d_cl)
    d_cr = max(0.0, d - d_cl - d_ds)
    if d_cl > 1e-9 and (d_cr>0 or d_ds>0): toc_positions.append((i, d_cl))
    if d_ds > 1e-9: tod_positions.append((i, d_cl + d_cr))

toc_labels={}
if len(toc_positions)==1: toc_labels[toc_positions[0]] = "TOC"
else:
    for n, key in enumerate(toc_positions, start=1): toc_labels[key] = f"TOC-{n}"

tod_labels={}
if len(tod_positions)==1: tod_labels[tod_positions[0]] = "TOD"
else:
    for n, key in enumerate(tod_positions, start=1): tod_labels[key] = f"TOD-{n}"

# construir segmentos
rows=[]; seq_points=[]
efob=float(st.session_state.start_fuel)

startup = parse_hhmm(st.session_state.startup)
takeoff = add_seconds(startup, int(st.session_state.taxi_min*60)) if startup else None
clock = takeoff

# DEP
seq_points.append({"name": points[0], "alt": _round_alt(start_alt),
                   "tc":"", "th":"", "mc":"", "mh":"", "tas":"", "gs":"", "dist":"",
                   "ete_sec":0, "eto": (takeoff.strftime("%H:%M") if takeoff else ""),
                   "burn":"", "efob": efob, "leg_idx": None})

def add_seg(phase, frm, to, i_leg, d_nm, tas, ff_lph, alt_start_ft, rate_fpm):
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

cur_alt = start_alt
toc_list=[]; tod_list=[]
for i in range(N):
    frm, to = legs[i]["From"], legs[i]["To"]
    d = dist[i]
    d_cl = min(front_climb[i], d)
    d_ds = min(back_desc[i],   d - d_cl)
    d_cr = max(0.0, d - d_cl - d_ds)

    if d_cl > 1e-9:
        name_toc = toc_labels.get((i,d_cl), to)
        if name_toc != to: toc_list.append((i, d_cl, name_toc))
        cur_alt = add_seg("CLIMB", frm, name_toc, i, d_cl, vy_kt, ff_climb, cur_alt, roc)
        frm = name_toc

    if d_cr > 1e-9:
        name_tod = tod_labels.get((i, d_cl+d_cr), to)
        if name_tod != to: tod_list.append((i, d_cl+d_cr, name_tod))
        cur_alt = add_seg("CRUISE", frm, name_tod, i, d_cr, float(st.session_state.cruise_ref_kt), ff_cruise, cur_alt, 0.0)
        frm = name_tod

    if d_ds > 1e-9:
        cur_alt = add_seg("DESCENT", frm, to, i, d_ds, float(st.session_state.descent_ref_kt), ff_descent, cur_alt, st.session_state.rod_fpm)

eta = clock
shutdown = add_seconds(eta, 5*60) if eta else None

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
    isa_dev = st.session_state.temp_c - isa_temp(pressure_alt(dep_elev, st.session_state.qnh))
    st.metric("ISA dev @ DEP (°C)", int(round(isa_dev)))
    if toc_list: st.write("**TOC**: " + ", ".join([f"{name} L{leg+1}@{fmt(pos,'dist')} nm" for (leg,pos,name) in toc_list]))
    if tod_list: st.write("**TOD**: " + ", ".join([f"{name} L{leg+1}@{fmt(pos,'dist')} nm" for (leg,pos,name) in tod_list]))

st.dataframe(rows, use_container_width=True)

tot_ete_sec = sum(int(p.get('ete_sec',0)) for p in seq_points if isinstance(p.get('ete_sec'), (int,float)))
tot_nm  = sum(float(p['dist']) for p in seq_points if isinstance(p.get('dist'), (int,float)))
tot_bo  = _round_tenth(sum(float(p['burn']) for p in seq_points if isinstance(p.get('burn'), (int,float))))
line = f"**Totais** — Dist {fmt(tot_nm,'dist')} nm • ETE {hhmmss_from_seconds(int(tot_ete_sec))} • Burn {fmt(tot_bo,'fuel')} L • EFOB {fmt(seq_points[-1]['efob'],'fuel')} L"
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
def P(key: str, value: str):
    put(named, fieldset, key, value, maxlens)
def PAll(keys: List[str], value: str):
    for k in keys:
        if k in fieldset:
            put(named, fieldset, k, value, maxlens)

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

    climb_time_hours = sum((d / gs_for(i,"CLIMB")) for i,d in enumerate(front_climb) if d > 0.0)
    _, ff_climb_tmp = cruise_lookup(start_alt + 0.5*max(0.0, cruise_alt-start_alt), int(st.session_state.rpm_climb), st.session_state.temp_c)
    climb_fuel_raw = ff_climb_tmp * max(0.0, climb_time_hours)
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
    isa_dev_i = int(round(st.session_state.temp_c - isa_temp(pressure_alt(_round_alt(AEROS.get(points[0],{}).get("elev",0)), st.session_state.qnh))))
    PAll(["TEMP_ISA_DEV","TEMP ISA DEV","TEMP/ISA_DEV"], f"{int(round(st.session_state.temp_c))} / {isa_dev_i}")
    PAll(["MAG_VAR","MAG VAR"], f"{int(round(st.session_state.var_deg))}{'E' if st.session_state.var_is_e else 'W'}")

if st.button("Gerar PDF NAVLOG", type="primary"):
    try:
        if not template_bytes: raise RuntimeError("Template PDF não carregado")
        out = fill_pdf(template_bytes, named, use_arm_font=bool(st.session_state.use_arm_font))
        m = re.search(r'(\d+)', st.session_state.lesson or "")
        lesson_num = m.group(1) if m else "00"
        safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
        filename = f"{safe_date}_LESSON-{lesson_num}_NAVLOG.pdf"
        st.download_button("📄 Download PDF", data=out, file_name=filename, mime="application/pdf")
        st.success("PDF gerado.")
    except Exception as e:
        st.error(f"Erro ao gerar PDF: {e}")

# =========================================================
# Relatório legível (explica contas)
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
        ["Elev DEP/ARR/ALTN (ft)", f"{fmt(_round_alt(AEROS.get(points[0],{}).get('elev',0)),'alt')} / {fmt(_round_alt(AEROS.get(points[-1],{}).get('elev',0)),'alt')} / {fmt(_round_alt(AEROS.get(st.session_state.altn,{}).get('elev',0)),'alt')}"],
        ["Cruise Alt (ft)", fmt(cruise_alt,'alt')],
        ["Startup / Taxi / ETD", f"{st.session_state.startup} / {st.session_state.taxi_min} min / {(add_seconds(parse_hhmm(st.session_state.startup),st.session_state.taxi_min*60).strftime('%H:%M') if st.session_state.startup else '')}"],
        ["QNH / OAT / ISA dev", f"{int(st.session_state.qnh)} / {int(st.session_state.temp_c)} / {int(round(st.session_state.temp_c - isa_temp(pressure_alt(_round_alt(AEROS.get(points[0],{}).get('elev',0)), st.session_state.qnh))))}"],
        ["Vento FROM / Var", f"{int(round(st.session_state.wind_from)):03d}/{int(round(st.session_state.wind_kt)):02d} / {int(round(st.session_state.var_deg))}{'E' if st.session_state.var_is_e else 'W'}"],
        ["TAS (cl/cru/des)", f"{_round_unit(tas_climb)}/{_round_unit(tas_cruise)}/{_round_unit(tas_descent)} kt"],
        ["FF (cl/cru/des)", f"{_round_unit(ff_climb)}/{_round_unit(ff_cruise)}/{_round_unit(ff_descent)} L/h"],
        ["ROCs/ROD", f"{_round_unit(roc)} ft/min / {_round_unit(st.session_state.rod_fpm)} ft/min"],
        ["Totais", f"Dist {fmt(tot_nm,'dist')} nm • ETE {hhmmss_from_seconds(int(tot_ete_sec))} • Burn {fmt(tot_bo,'fuel')} L • EFOB {fmt(seq_points[-1]['efob'],'fuel')} L"],
        ["TOC/TOD", (", ".join([f'{name} L{leg+1}@{fmt(pos,"dist")}nm' for (leg,pos,name) in toc_list]) + ("; " if toc_list and tod_list else "") +
                     ", ".join([f'{name} L{leg+1}@{fmt(pos,"dist")}nm' for (leg,pos,name) in tod_list])) or "—"],
        ["Regras de arredondamento", "Tempo: múltiplo de 10 s (mais próximo, mín. 10 s) • Dist: 0.1 nm • Fuel: 0.1 L • Alt: <1000→50 / ≥1000→100 • Ângulos & velocidades: unidade."],
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

    story.append(Paragraph("Cálculos por segmento (passo a passo)", H2))
    for i, p in enumerate(seq_points):
        if i==0:
            continue
        prev = seq_points[i-1]
        seg = p
        leg_no = seg["leg_idx"]+1 if seg["leg_idx"] is not None else i

        steps = [
            ["1) Entrada", f"TC={seg['tc']}°T; TAS={seg['tas']} kt; Vento base={int(st.session_state.wind_from):03d}/{int(st.session_state.wind_kt):02d} kt" +
                           (" • (vento por fix ativo: média vetorial)" if st.session_state.use_wind_by_fix else "")],
            ["2) Triângulo de vento", f"WCA={seg['wca']}°; TH={seg['th']}°T; MH={seg['mh']}°M; GS={seg['gs']} kt"],
            ["3) Distância", f"{fmt(seg['dist'],'dist')} nm"],
            ["4) ETE bruto", f"{hhmmss_from_seconds(int(seg['ete_raw']))}"],
            ["5) ETE arredondado", f"nearest 10 s → {mmss_from_seconds(seg['ete_sec'])}"],
            ["6) Débito combustível", f"{_round_unit(ff_climb if p['phase']=='CLIMB' else ff_descent if p['phase']=='DESCENT' else ff_cruise)} L/h"],
            ["7) Burn", f"bruto={seg['burn']:.2f} L → arred={fmt(seg['burn'],'fuel')} L"],
            ["8) Perfil vertical", f"{prev['alt']}→{seg['alt']} ft @ {'+' if p['phase']=='CLIMB' else '-' if p['phase']=='DESCENT' else '±0'}{int(abs(seg['rate_fpm']))} ft/min"],
            ["9) ETO / EFOB", f"ETO={seg['eto'] or '—'}; EFOB={fmt(seg['efob'],'fuel')} L"]
        ]
        t = LongTable([ [Paragraph(f"<b>Leg {leg_no}: {prev['name']} → {seg['name']}</b>", Psty), "" ] ] + steps,
                      colWidths=[56*mm, None], hAlign="LEFT")
        t.setStyle(TableStyle([
            ("SPAN",(0,0),(1,0)),
            ("BACKGROUND",(0,0),(1,0),colors.whitesmoke),
            ("GRID",(0,1),(-1,-1),0.25,colors.lightgrey),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("FONTSIZE",(0,0),(-1,-1),9),
            ("LEFTPADDING",(0,0),(-1,-1),4),
            ("RIGHTPADDING",(0,0),(-1,-1),6),
            ("TOPPADDING",(0,0),(-1,-1),3),
            ("BOTTOMPADDING",(0,0),(-1,-1),3),
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


