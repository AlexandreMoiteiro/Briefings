# app.py — NAVLOG com cortes (LPSO→TOC, TOC→VACOR, …) na APP e TH no PDF
# Reqs: streamlit, pypdf, pytz

import streamlit as st
import datetime as dt
import pytz, io, json, unicodedata, re, math
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from math import sin, asin, radians, degrees, fmod

# =============== PDF helpers ===============
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
            NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g")
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

# =============== Wind & helpers ===============
def wrap360(x): x=fmod(x,360.0); return x+360 if x<0 else x
def angle_diff(a,b): return (a-b+180)%360-180

# Triângulo do vento (vento-para = from+180)
def wind_triangle(tc_deg: float, tas_kt: float, wind_from_deg: float, wind_kt: float):
    if tas_kt <= 0:
        return 0.0, wrap360(tc_deg), 0.0
    wind_to = wrap360(wind_from_deg + 180.0)
    beta = radians(angle_diff(wind_to, tc_deg))
    cross = wind_kt * sin(beta)              # +vento da esquerda
    head  = wind_kt * math.cos(beta)         # +tailwind / −headwind
    s = max(-1.0, min(1.0, cross/max(tas_kt,1e-9)))
    wca = degrees(asin(s))
    th  = wrap360(tc_deg + wca)
    gs  = max(0.0, tas_kt*math.cos(radians(wca)) + head)
    return wca, th, gs

def apply_var(true_deg,var_deg,east_is_negative=False):
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

# =============== AFM (650 kg) ===============
def clamp(v,lo,hi): return max(lo,min(hi,v))
def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

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
    v0 = interp1(t, t0, t1, v00, v01); v1 = interp1(t, t0, t1, v10, v11)
    return max(1.0, interp1(pa_c, p0, p1, v0, v1) * ROC_FACTOR)

def vy_interp_enroute(pa):
    pas=sorted(VY_ENROUTE.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    return interp1(pa_c, p0, p1, VY_ENROUTE[p0], VY_ENROUTE[p1])

# =============== Aerodromes ===============
AEROS={
 "LPSO":{"elev":390,"freq":"119.805"},
 "LPEV":{"elev":807,"freq":"122.705"},
 "LPCB":{"elev":1251,"freq":"122.300"},
 "LPCO":{"elev":587,"freq":"118.405"},
 "LPVZ":{"elev":2060,"freq":"118.305"},
}
def aero_elev(icao): return int(AEROS.get(icao,{}).get("elev",0))
def aero_freq(icao): return AEROS.get(icao,{}).get("freq","")

# =============== App UI ===============
st.set_page_config(page_title="NAVLOG", layout="wide", initial_sidebar_state="collapsed")
st.title("Navigation Plan & Inflight Log — Tecnam P2008")

DEFAULT_STUDENT="AMOIT"; DEFAULT_AIRCRAFT="P208"; DEFAULT_CALLSIGN="RVP"
REGS=["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"]
PDF_TEMPLATE_PATHS=["NAVLOG - FORM.pdf"]

# Header
c1,c2,c3=st.columns(3)
with c1:
    aircraft=st.text_input("Aircraft",DEFAULT_AIRCRAFT)
    registration=st.selectbox("Registration",REGS,index=0)
    callsign=st.text_input("Callsign",DEFAULT_CALLSIGN)
with c2:
    student=st.text_input("Student",DEFAULT_STUDENT)
    lesson = st.text_input("Lesson","")
    instrutor = st.text_input("Instrutor","")
with c3:
    dept=st.selectbox("Departure",list(AEROS.keys()),index=0)
    arr =st.selectbox("Arrival", list(AEROS.keys()),index=1)
    altn=st.selectbox("Alternate",list(AEROS.keys()),index=2)
startup_str=st.text_input("Startup (HH:MM)","")

# Atmosfera / navegação
c4,c5,c6=st.columns(3)
with c4:
    qnh=st.number_input("QNH (hPa)",900,1050,1013,step=1)
    cruise_alt=st.number_input("Cruise Altitude (ft)",0,14000,3000,step=100)
    initial_alt=st.number_input("Altitude inicial (ft AMSL)",0,20000,0,step=50,
                                help="0 = usa a elevação do DEP.")
with c5:
    temp_c=st.number_input("OAT (°C)",-40,50,15,step=1)
    var_deg=st.number_input("Mag Variation (°)",0,30,1,step=1)
    var_is_e=(st.selectbox("E/W",["W","E"],index=0)=="E")
with c6:
    wind_from=st.number_input("Wind FROM (°TRUE)",0,360,0,step=1)
    wind_kt=st.number_input("Wind (kt)",0,120,0,step=1)
    target_arr_alt=st.number_input("Altitude alvo na chegada (ft AMSL)",0,20000,0,step=50)

# Perf / consumos
c7,c8,c9=st.columns(3)
with c7:
    rpm_climb  = st.number_input("Climb RPM (AFM)",1800,2388,2250,step=10)
    rpm_cruise = st.number_input("Cruise RPM (AFM)",1800,2388,2000,step=10)
with c8:
    rpm_descent= st.number_input("Descent RPM (se NÃO idle)",1700,2300,1800,step=10)
    idle_mode  = st.checkbox("Descent mostly IDLE", value=True)
with c9:
    rod_fpm=st.number_input("ROD (ft/min)",200,1500,700,step=10)
    idle_ff=st.number_input("Idle FF (L/h)", 0.0, 20.0, 5.0, step=0.1)
    start_fuel=st.number_input("Fuel inicial (EFOB_START) [L]",0.0,1000.0,0.0,step=1.0)

# Velocidades ref
cruise_ref_kt = st.number_input("Cruise speed (kt)", 40, 140, 80, step=1)
descent_ref_kt= st.number_input("Descent speed (kt)", 40, 120, 65, step=1)

# ===== ROUTE (textarea) + JSON =====
def parse_route_text(txt:str) -> List[str]:
    tokens = re.split(r"[,\s→\-]+", (txt or "").strip())
    return [t for t in tokens if t]

st.markdown("### Route (DEP … ARR)")
default_route = f"{dept} {arr}"
route_text = st.text_area("Pontos (separados por espaço, vírgulas ou '->')",
                          value=st.session_state.get("route_text", default_route))
c_ra, c_rb = st.columns([1,1])
with c_ra:
    apply_route = st.button("Aplicar rota")
with c_rb:
    def snapshot_route() -> dict:
        return {
            "route_points": st.session_state.get("points", [dept, arr]),
            "legs": [{"TC":l["TC"], "Dist":l["Dist"]} for l in st.session_state.get("legs", [])]
        }
    st.download_button("💾 Download rota (JSON)",
                       data=json.dumps(snapshot_route(), ensure_ascii=False, indent=2).encode("utf-8"),
                       file_name=f"route_{ascii_safe(registration)}.json",
                       mime="application/json")

uploaded = st.file_uploader("📤 Seleciona rota (JSON)", type=["json"])
use_uploaded = st.button("Usar rota do ficheiro")
if use_uploaded and uploaded is not None:
    try:
        data = json.loads(uploaded.read().decode("utf-8"))
        st.session_state.points = list(data.get("route_points") or [dept, arr])
        tgt = max(0,len(st.session_state.points)-1)
        src_legs = data.get("legs") or []
        st.session_state.legs = []
        for i in range(tgt):
            tc = float(src_legs[i]["TC"]) if i < len(src_legs) and "TC" in src_legs[i] else 0.0
            di = float(src_legs[i]["Dist"]) if i < len(src_legs) and "Dist" in src_legs[i] else 0.0
            st.session_state.legs.append({"From":st.session_state.points[i],
                                          "To":st.session_state.points[i+1],
                                          "TC":tc,"Dist":di})
        st.session_state["route_text"] = " ".join(st.session_state.points)
        st.success("Rota carregada do JSON.")
    except Exception as e:
        st.error(f"Falha a carregar JSON: {e}")

if "points" not in st.session_state:
    st.session_state.points = [dept, arr]
if apply_route:
    pts = parse_route_text(route_text)
    if len(pts) < 2: pts = [dept, arr]
    st.session_state.points = pts
    st.session_state.route_text = " ".join(pts)
points = st.session_state.points
if points: points[0]=dept
if len(points)>=2: points[-1]=arr

# LEGS
def blank_leg(): return {"From":"","To":"","TC":0.0,"Dist":0.0}
if "legs" not in st.session_state: st.session_state.legs = []
target_legs = max(0, len(points)-1)
legs = st.session_state.legs
if len(legs) < target_legs: legs += [blank_leg() for _ in range(target_legs - len(legs))]
elif len(legs) > target_legs: legs = legs[:target_legs]
for i in range(target_legs):
    legs[i]["From"]=points[i]; legs[i]["To"]=points[i+1]
st.session_state.legs = legs

st.markdown("### Legs (distância do ponto anterior)")
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

N = len(legs)

# ===== Cálculo (perfil vertical, cortes dentro do leg) =====
def pressure_alt(alt_ft, qnh_hpa): return float(alt_ft) + (1013.0 - float(qnh_hpa))*30.0

dep_elev = aero_elev(dept); arr_elev = aero_elev(arr)
start_alt = float(initial_alt) if initial_alt>0 else float(dep_elev)
end_alt   = float(target_arr_alt) if target_arr_alt>0 else float(arr_elev)

pa_start  = pressure_alt(start_alt, qnh)
pa_cruise = pressure_alt(cruise_alt, qnh)
vy_kt = vy_interp_enroute(pa_start)
tas_climb, tas_cruise, tas_descent = vy_kt, float(cruise_ref_kt), float(descent_ref_kt)

roc = roc_interp_enroute(pa_start, temp_c)                 # ft/min
delta_climb = max(0.0, cruise_alt - start_alt)
delta_desc  = max(0.0, cruise_alt - end_alt)
t_climb_total = delta_climb / max(roc,1e-6)
t_desc_total  = delta_desc  / max(rod_fpm,1e-6)

# FFs (AFM)
pa_mid_climb = start_alt + 0.5*delta_climb
pa_mid_desc  = end_alt   + 0.5*delta_desc
_, ff_climb  = cruise_lookup(pa_mid_climb, int(rpm_climb),  temp_c)
_, ff_cruise = cruise_lookup(pa_cruise,   int(rpm_cruise),  temp_c)
ff_descent   = float(idle_ff) if idle_mode else cruise_lookup(pa_mid_desc, int(rpm_descent), temp_c)[1]

def gs_for(tc, tas): return wind_triangle(float(tc), float(tas), wind_from, wind_kt)[2]

dist = [float(l["Dist"] or 0.0) for l in legs]
gs_climb   = [gs_for(legs[i]["TC"], tas_climb)   for i in range(N)]
gs_cruise  = [gs_for(legs[i]["TC"], tas_cruise)  for i in range(N)]
gs_descent = [gs_for(legs[i]["TC"], tas_descent) for i in range(N)]

# ---- Distribuir CLIMB para a frente
climb_nm   = [0.0]*N
idx_toc = None
rem_t = float(t_climb_total)
for i in range(N):
    if rem_t <= 1e-9: break
    gs = max(gs_climb[i], 1e-6)
    t_full = 60.0 * dist[i] / gs
    use_t = min(rem_t, t_full)
    climb_nm[i] = min(dist[i], gs * use_t / 60.0)
    rem_t -= use_t
    if rem_t <= 1e-9:
        idx_toc = i
        break

# ---- Distribuir DESCENT para trás
descent_nm = [0.0]*N
idx_tod = None
rem_t = float(t_desc_total)
for j in range(N-1, -1, -1):
    if rem_t <= 1e-9: break
    gs = max(gs_descent[j], 1e-6)
    t_full = 60.0 * dist[j] / gs
    use_t = min(rem_t, t_full)
    descent_nm[j] = min(dist[j], gs * use_t / 60.0)
    rem_t -= use_t
    if rem_t <= 1e-9:
        idx_tod = j
        break

# ===== APP: linhas por SEGMENTO (ex.: LPSO→TOC / TOC→VACOR) =====
startup = parse_hhmm(startup_str)
takeoff = add_minutes(startup,15) if startup else None
clock = takeoff

def ceil_pos_minutes(x):  # arredonda ↑ e garante 1 min quando >0
    return max(1, int(math.ceil(x - 1e-9))) if x > 0 else 0

rows=[]; seq_points=[]  # para o PDF

PH_ICON = {"CLIMB":"↑","CRUISE":"→","DESCENT":"↓"}

alt_cursor = float(start_alt)
total_dist = sum(dist); total_ete = total_burn = 0.0; efob=float(start_fuel)

def add_segment(phase:str, from_nm:str, to_nm:str, i_leg:int, d_nm:float, tas:float, ff_lph:float):
    """Acrescenta um segmento de um leg; atualiza relógio, ALT, totais e constrói ponto PDF."""
    global clock, total_ete, total_burn, efob, alt_cursor
    if d_nm <= 1e-9: return

    tc = float(legs[i_leg]["TC"])
    wca, th, gs = wind_triangle(tc, tas, wind_from, wind_kt)

    ete_raw = 60.0 * d_nm / max(gs,1e-6)  # minutos reais
    ete = ceil_pos_minutes(ete_raw)
    burn = ff_lph * (ete_raw/60.0)

    alt_start = alt_cursor
    if phase == "CLIMB":
        alt_end = min(cruise_alt, alt_start + roc * ete_raw)      # <<< FIX: sem /60
    elif phase == "DESCENT":
        alt_end = max(end_alt,   alt_start - rod_fpm * ete_raw)    # <<< FIX: sem /60
    else:
        alt_end = alt_start

    detail = f"{phase.capitalize()} {d_nm:.1f} nm"

    eto = ""
    if clock:
        clock = add_minutes(clock, ete); eto = clock.strftime("%H:%M")
    total_ete += ete; total_burn += burn; efob = max(0.0, efob - burn)
    alt_cursor = alt_end

    rows.append({
        "Fase": PH_ICON[phase],
        "Leg/Marker": f"{from_nm}→{to_nm}",
        "To (Name)": to_nm,
        "ALT (ft)": f"{int(round(alt_start))}→{int(round(alt_end))}",
        "Detalhe": detail,
        "TC (°T)": round(tc,0),
        "TH (°T)": round(th,0),
        "MH (°M)": round(apply_var(th, var_deg, var_is_e),0),
        "TAS (kt)": round(tas,0), "GS (kt)": round(gs,0),
        "FF (L/h)": round(ff_lph,1),
        "Dist (nm)": round(d_nm,1), "ETE (min)": ete, "ETO": eto,
        "Burn (L)": round(burn,1), "EFOB (L)": round(efob,1)
    })

    # Guardar ponto (nome a nome) para PDF
    seq_points.append({
        "name": to_nm, "alt": int(round(alt_end)),
        "tc": int(round(tc)), "th": int(round(th)),
        "mh": int(round(apply_var(th, var_deg, var_is_e))),
        "tas": int(round(tas)), "gs": int(round(gs)),
        "dist": d_nm, "ete": ete, "eto": eto, "burn": burn
    })

# Ponto inicial para o PDF
seq_points.append({"name": dept, "alt": int(round(start_alt)),
                   "tc":"", "th":"", "mh":"", "tas":"", "gs":"", "dist":"", "ete":"", "eto": (clock.strftime("%H:%M") if clock else ""), "burn":""})

for i in range(N):
    leg_from, leg_to = legs[i]["From"], legs[i]["To"]
    d_total  = dist[i]
    d_cl = min(climb_nm[i], d_total)
    d_ds = min(descent_nm[i], d_total - d_cl)
    d_cr = max(0.0, d_total - d_cl - d_ds)

    cur_from = leg_from

    if d_cl > 0:
        to_name = "TOC" if (idx_toc == i and d_cl < d_total) else leg_to
        add_segment("CLIMB", cur_from, to_name, i, d_cl, tas_climb, ff_climb)
        cur_from = to_name

    if d_cr > 0:
        to_name = "TOD" if (idx_tod == i and d_ds > 0) else leg_to
        add_segment("CRUISE", cur_from, to_name, i, d_cr, tas_cruise, ff_cruise)
        cur_from = to_name

    if d_ds > 0:
        add_segment("DESCENT", cur_from, leg_to, i, d_ds, float(descent_ref_kt), ff_descent)

eta = clock
landing = eta
shutdown = add_minutes(eta,5) if eta else None

# ===== Tabela da APP =====
st.markdown("### Flight plan — cortes dentro do leg (App)")
cfg={
    "Fase":      st.column_config.TextColumn("Fase"),
    "Leg/Marker": st.column_config.TextColumn("Leg / Marker"),
    "To (Name)":  st.column_config.TextColumn("To (Name)", disabled=True),
    "ALT (ft)":   st.column_config.TextColumn("ALT (ft)"),
    "Detalhe":    st.column_config.TextColumn("Detalhe"),
    "TC (°T)":    st.column_config.NumberColumn("TC (°T)", disabled=True),
    "TH (°T)":    st.column_config.NumberColumn("TH (°T)", disabled=True),
    "MH (°M)":    st.column_config.NumberColumn("MH (°M)", disabled=True),
    "TAS (kt)":   st.column_config.NumberColumn("TAS (kt)", disabled=True),
    "GS (kt)":    st.column_config.NumberColumn("GS (kt)", disabled=True),
    "FF (L/h)":   st.column_config.NumberColumn("FF (L/h)", disabled=True),
    "Dist (nm)":  st.column_config.NumberColumn("Dist (nm)", disabled=True),
    "ETE (min)":  st.column_config.NumberColumn("ETE (min)", disabled=True),
    "ETO":        st.column_config.TextColumn("ETO", disabled=True),
    "Burn (L)":   st.column_config.NumberColumn("Burn (L)", disabled=True),
    "EFOB (L)":   st.column_config.NumberColumn("EFOB (L)", disabled=True),
}
st.data_editor(rows, hide_index=True, use_container_width=True, num_rows="fixed", column_config=cfg, key="fp_table")

tot_line = f"**Totais** — Dist {sum(float(r['Dist (nm)']) for r in rows):.1f} nm • ETE {int(sum(int(r['ETE (min)']) for r in rows))//60:02d}:{int(sum(int(r['ETE (min)']) for r in rows))%60:02d} • Burn {sum(float(r['Burn (L)']) for r in rows):.1f} L • EFOB {efob:.1f} L"
if eta:
    tot_line += f" • **ETA {eta.strftime('%H:%M')}** • **Landing {landing.strftime('%H:%M')}** • **Shutdown {shutdown.strftime('%H:%M')}**"
st.markdown(tot_line)

# ===== PDF export (nome a nome; inclui TH) =====
st.markdown("### PDF export")
show_fields = st.checkbox("Mostrar nomes de campos do PDF (debug)")

def build_pdf_items_from_points(points):
    items = []
    for idx, p in enumerate(points, start=1):
        it = {
            "Name": p["name"],
            "Alt": str(int(round(p["alt"]))),
            "TC":  (str(p["tc"]) if idx>1 else ""),
            "TH":  (str(p["th"]) if idx>1 else ""),
            "MH":  (str(p["mh"]) if idx>1 else ""),
            "TAS": (str(p["tas"]) if idx>1 else ""),
            "GS":  (str(p["gs"])  if idx>1 else ""),
            "Dist": (f"{p['dist']:.1f}" if idx>1 else ""),
            "ETE":  (str(p["ete"]) if idx>1 else ""),
            "ETO":  (p["eto"] if idx>1 else (p["eto"] or "")),
            "Burn": (f"{p['burn']:.1f}" if idx>1 else ""),
        }
        items.append(it)
    return items

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
        for k,v in {
            "Aircraft": aircraft,
            "Registration": registration,
            "Callsign": callsign,
            "Student": student, "Lesson": lesson, "Instrutor": instrutor,
            "Dept_Airfield": dept, "Arrival_Airfield": arr,
            "Alternate": altn, "Alt_Alternate": str(aero_elev(altn)),
            "Dept_Comm": aero_freq(dept), "Arrival_comm": aero_freq(arr),
            "Enroute_comm": "123.755",
            "QNH": f"{int(round(qnh))}",
            "temp_isa_dev": f"{int(round(temp_c))} / {round(temp_c - isa_temp(pressure_alt(aero_elev(dept), qnh)))}",
            "wind": f"{int(round(wind_from)):03d}/{int(round(wind_kt)):02d}",
            "mag_var": f"{var_deg:.1f}{'E' if var_is_e else 'W'}",
            "flt_lvl_altitude": f"{int(round(cruise_alt))}",
            "Startup": startup_str,
            "Takeoff": add_minutes(parse_hhmm(startup_str),15).strftime("%H:%M") if startup_str else "",
        }.items():
            put(named, fieldset, k, v, maxlens)

        pdf_items = build_pdf_items_from_points(seq_points)
        last_eto = pdf_items[-1]["ETO"] if pdf_items else ""
        put(named, fieldset, "Landing", last_eto, maxlens)
        put(named, fieldset, "Shutdown", (add_minutes(parse_hhmm(last_eto),5).strftime("%H:%M") if last_eto else ""), maxlens)
        put(named, fieldset, "ETD/ETA", f"{(add_minutes(parse_hhmm(startup_str),15).strftime('%H:%M') if startup_str else '')} / {last_eto}", maxlens)

        tot_min = sum(int(it["ETE"] or "0") for it in pdf_items)
        tot_nm  = sum(float(it["Dist"] or 0.0) for it in pdf_items)
        tot_bo  = sum(float(it["Burn"] or 0.0) for it in pdf_items)
        put(named, fieldset, "FLT TIME", f"{tot_min//60:02d}:{tot_min%60:02d}", maxlens)
        for key in ("LEVEL F/F","LEVEL_FF","Level_FF","Level F/F"):
            put(named, fieldset, key, f"{int(round(cruise_alt))} / {ff_cruise:.1f}", maxlens)
        put(named, fieldset, "CLIMB FUEL", f"{ff_climb*(t_climb_total/60.0):.1f}", maxlens)
        put(named, fieldset, "ETE_Total", f"{tot_min}", maxlens)
        put(named, fieldset, "Dist_Total", f"{tot_nm:.1f}", maxlens)
        put(named, fieldset, "PL_BO_TOTAL", f"{tot_bo:.1f}", maxlens)
        put(named, fieldset, "EFOB_TOTAL", f"{max(0.0, float(start_fuel)-tot_bo):.1f}", maxlens)
        put(named, fieldset, "Leg_Number", str(N), maxlens)

        for i, r in enumerate(pdf_items[:11], start=1):
            s=str(i)
            put(named, fieldset, f"Name{s}", r["Name"], maxlens)
            put(named, fieldset, f"Alt{s}",  r["Alt"], maxlens)
            put(named, fieldset, f"FREQ{s}", "", maxlens)
            if r["TC"]!="":   put(named, fieldset, f"TCRS{s}", r["TC"], maxlens)
            if r["TH"]!="":   put(named, fieldset, f"THDG{s}", r["TH"], maxlens)   # TH agora sai no PDF
            if r["MH"]!="":   put(named, fieldset, f"MHDG{s}", r["MH"], maxlens)
            if r["TAS"]!="":  put(named, fieldset, f"TAS{s}",  r["TAS"], maxlens)
            if r["GS"]!="":   put(named, fieldset, f"GS{s}",   r["GS"], maxlens)
            if r["Dist"]!="": put(named, fieldset, f"Dist{s}", r["Dist"], maxlens)
            if r["ETE"]!="":  put(named, fieldset, f"ETE{s}",  r["ETE"], maxlens)
            if r["ETO"]!="":  put(named, fieldset, f"ETO{s}",  r["ETO"], maxlens)
            if r["Burn"]!="": put(named, fieldset, f"PL_BO{s}", r["Burn"], maxlens)

        if st.button("Gerar PDF preenchido", type="primary"):
            out = fill_pdf(template_bytes, named)
            safe_reg = ascii_safe(registration)
            safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
            filename = f"{safe_date}_{safe_reg}_NAVLOG.pdf"
            st.download_button("Download PDF", data=out, file_name=filename, mime="application/pdf")
            st.success("PDF gerado. Revê antes do voo.")
    except Exception as e:
        st.error(f"Erro ao preparar/gerar PDF: {e}")


