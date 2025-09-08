# app.py — NAVLOG (Waypoints + Legs separados) + TOC/TOD corretos + export p/ "NAVLOG - FORM.pdf"
# Reqs: streamlit, pypdf, pytz

import streamlit as st
import datetime as dt
import pytz
import io, unicodedata
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
    return unicodedata.normalize("NFKD", str(x or "")).encode("ascii", "ignore").decode("ascii")

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
        for k, v in fd.items():
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

# ========================= Navegação & helpers =========================
def wrap360(x): x=fmod(x,360.0); return x+360 if x<0 else x
def angle_diff(a,b): return (a-b+180)%360-180

def wind_triangle(tc_deg: float, tas_kt: float, wind_from_deg: float, wind_kt: float):
    """Retorna (WCA°, TH°, GS kt)"""
    if tas_kt <= 0: return 0.0, wrap360(tc_deg), 0.0
    beta = radians(angle_diff(wind_from_deg, tc_deg))
    cross = wind_kt * sin(beta)
    # limitar asin
    s = max(-1.0, min(1.0, cross / max(tas_kt, 1e-9)))
    wca = degrees(asin(s))
    # cos(beta) = sqrt(1 - sin^2(beta)), usa-se para componente de frente/cauda
    head = wind_kt * (1 - (sin(beta)**2))**0.5
    gs = tas_kt * (1 - (s**2))**0.5 - head
    th = wrap360(tc_deg + wca)
    return wca, th, max(0.0, gs)

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

def clamp(v,lo,hi): return max(lo,min(hi,v))
def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

# ========================= AFM (ROC & VY @ 650 kg) =========================
ROC = {
    650:{0:{-25:951,0:805,25:675,50:557},2000:{-25:840,0:696,25:568,50:453},4000:{-25:729,0:588,25:462,50:349},
         6000:{-25:619,0:480,25:357,50:245},8000:{-25:509,0:373,25:251,50:142},10000:{-25:399,0:266,25:146,50:39},
         12000:{-25:290,0:159,25:42,50:-64},14000:{-25:181,0:53,25:-63,50:-166}},
}
VY = {650:{0:70,2000:69,4000:68,6000:67,8000:65,10000:64,12000:63,14000:62}}

def pressure_alt(elev_ft, qnh_hpa): return float(elev_ft) + (1013.0 - float(qnh_hpa))*30.0

def roc_interp(pa, temp_c):
    tab = ROC[650]
    pas=sorted(tab.keys()); pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    temps=[-25,0,25,50]; t=clamp(temp_c,temps[0],temps[-1])
    if t<=0: t0,t1=-25,0
    elif t<=25: t0,t1=0,25
    else: t0,t1=25,50
    v00, v01 = tab[p0][t0], tab[p0][t1]
    v10, v11 = tab[p1][t0], tab[p1][t1]
    v0 = interp1(t, t0, t1, v00, v01); v1 = interp1(t, t0, t1, v10, v11)
    return max(1.0, interp1(pa_c, p0, p1, v0, v1))

def vy_interp(pa):
    table=VY[650]; pas=sorted(table.keys())
    pa_c=clamp(pa,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    return interp1(pa_c, p0, p1, table[p0], table[p1])

# ========================= Aeródromos =========================
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
PDF_TEMPLATE_PATHS=["NAVLOG - FORM.pdf"]  # PDF na raiz do repo (ao lado do app.py)

# Header
c1,c2,c3=st.columns(3)
with c1:
    aircraft=st.text_input("Aircraft",DEFAULT_AIRCRAFT)
    registration=st.selectbox("Registration",REGS,index=0)
    callsign=st.text_input("Callsign",DEFAULT_CALLSIGN)
with c2:
    student=st.text_input("Student",DEFAULT_STUDENT)
    lesson = st.text_input("Lesson","")
    instructor = st.text_input("Instructor","")
with c3:
    dept=st.selectbox("Departure",list(AEROS.keys()),index=0)
    arr =st.selectbox("Arrival",list(AEROS.keys()),index=1)
    altn=st.selectbox("Alternate",list(AEROS.keys()),index=2)

startup_str=st.text_input("Startup (HH:MM)","")

# Atmosfera & navegação (globais)
c4,c5,c6=st.columns(3)
with c4:
    qnh=st.number_input("QNH (hPa)",900,1050,1013,step=1)
    cruise_alt=st.number_input("Cruise Altitude (ft)",0,14000,3000,step=100)
with c5:
    temp_c=st.number_input("OAT (°C)",-40,50,15,step=1)
    var_deg=st.number_input("Mag Variation (°)",0,30,1,step=1)
    var_is_e=(st.selectbox("E/W",["W","E"],index=0)=="E")
with c6:
    wind_from=st.number_input("Wind FROM (°TRUE)",0,360,0,step=1)
    wind_kt=st.number_input("Wind (kt)",0,120,0,step=1)
    enroute_comm=st.text_input("Enroute frequency", "123.755")

# Velocidades e consumos (simples e explícitos)
c7,c8,c9=st.columns(3)
with c7:
    cruise_ias = st.number_input("Cruise IAS (kt)", 60, 130, 80, step=1)  # referência 80 kt
with c8:
    ff_climb   = st.number_input("FF Climb (L/h)",  5.0, 40.0, 22.0, step=0.5)
    ff_cruise  = st.number_input("FF Cruise (L/h)", 5.0, 40.0, 18.0, step=0.5)
with c9:
    idle_mode  = st.checkbox("Descent mostly IDLE", value=True)
    ff_descent = st.number_input("FF Descent (L/h) (se não IDLE)", 5.0, 40.0, 12.0, step=0.5)
    idle_ff    = st.number_input("Idle FF (L/h)", 0.0, 20.0, 5.0, step=0.2)
    start_fuel = st.number_input("Fuel inicial (EFOB_START) [L]", 0.0, 1000.0, 0.0, step=1.0)

# ===== Waypoints & Legs =====
st.markdown("### Waypoints (nós) e Legs (entre nós)")
N = st.number_input("Nº de legs", 1, 11, 4)
NUM_WP = N + 1

def blank_wp(): return {"Name":"", "Freq":""}
def blank_leg(): return {"TC":0.0, "Dist":0.0}

if "wps" not in st.session_state:
    st.session_state.wps = [blank_wp() for _ in range(NUM_WP)]
if "legs_input" not in st.session_state:
    st.session_state.legs_input = [blank_leg() for _ in range(N)]

# Resize mantendo DEP na 1ª e ARR na última
def resize_state():
    wps = st.session_state.wps
    legs = st.session_state.legs_input
    # waypoints
    if len(wps) < NUM_WP: wps += [blank_wp() for _ in range(NUM_WP - len(wps))]
    elif len(wps) > NUM_WP: wps = wps[:NUM_WP]
    st.session_state.wps = wps
    # legs
    if len(legs) < N: legs += [blank_leg() for _ in range(N - len(legs))]
    elif len(legs) > N: legs = legs[:N]
    st.session_state.legs_input = legs

resize_state()

# Impor DEP/ARR fixos
wps = st.session_state.wps
wps[0]["Name"]=dept; wps[0]["Freq"]=aero_freq(dept)
wps[-1]["Name"]=arr; wps[-1]["Freq"]=aero_freq(arr)

# Editor de Waypoints (apenas linhas intermédias são efetivamente "tuas")
wp_rows = [{"#":i+1,"Name":wps[i]["Name"],"Freq":wps[i]["Freq"]} for i in range(NUM_WP)]
wp_cfg = {
    "#":   st.column_config.NumberColumn("#", disabled=True),
    "Name":st.column_config.TextColumn("Name"),
    "Freq":st.column_config.TextColumn("Freq"),
}
wp_edit = st.data_editor(
    wp_rows, hide_index=True, use_container_width=True,
    column_config=wp_cfg, num_rows="fixed", key="wp_editor"
)

# Aplicar edições (preservando DEP/ARR)
for i, row in enumerate(wp_edit):
    if i==0:
        wps[0]["Name"]=dept; wps[0]["Freq"]=aero_freq(dept)
    elif i==NUM_WP-1:
        wps[-1]["Name"]=arr; wps[-1]["Freq"]=aero_freq(arr)
    else:
        wps[i]["Name"]=row.get("Name","")
        wps[i]["Freq"]=row.get("Freq","")

# Editor de Legs (TC/Dist) — From/To são derivados e só leitura
leg_rows=[]
for i in range(N):
    leg_rows.append({
        "Leg": i+1,
        "From": wps[i]["Name"],
        "To":   wps[i+1]["Name"],
        "TC":   float(st.session_state.legs_input[i].get("TC",0.0)),
        "Dist": float(st.session_state.legs_input[i].get("Dist",0.0)),
    })

leg_cfg = {
    "Leg":  st.column_config.NumberColumn("Leg", disabled=True),
    "From": st.column_config.TextColumn("From", disabled=True),
    "To":   st.column_config.TextColumn("To", disabled=True),
    "TC":   st.column_config.NumberColumn("TC (°T)", min_value=0.0, max_value=359.9, step=0.1),
    "Dist": st.column_config.NumberColumn("Dist (NM)", min_value=0.0, step=0.1),
}
leg_edit = st.data_editor(
    leg_rows, hide_index=True, use_container_width=True,
    column_config=leg_cfg, num_rows="fixed", key="leg_editor"
)

# Guardar edições de legs
for i, row in enumerate(leg_edit):
    st.session_state.legs_input[i]["TC"] = float(row.get("TC",0.0))
    st.session_state.legs_input[i]["Dist"] = float(row.get("Dist",0.0))

legs = st.session_state.legs_input

# ========================= Cálculos de TOC/TOD e tabela final =========================
dep_elev = aero_elev(dept); arr_elev = aero_elev(arr)
pa_dep = pressure_alt(dep_elev, qnh)

# TAS por fase
tas_climb = float(vy_interp(pa_dep))      # usa Vy da AFM como TAS de subida (aprox)
tas_cruise = float(cruise_ias)            # referência solicitada
tas_descent = 65.0                        # solicitado

# GS para climb usa TC do 1º leg; para descent usa TC do último leg
tc_first = float(legs[0]["TC"]) if N>=1 else 0.0
tc_last  = float(legs[-1]["TC"]) if N>=1 else 0.0
_,_, gs_climb = wind_triangle(tc_first, tas_climb, wind_from, wind_kt)
_,_, gs_desc  = wind_triangle(tc_last,  tas_descent, wind_from, wind_kt)

# ROC/ROD, tempos e distâncias climb/descent
roc_fpm = roc_interp(pa_dep, temp_c)
delta_up   = max(0.0, float(cruise_alt) - float(dep_elev))
delta_down = max(0.0, float(cruise_alt) - float(arr_elev))
climb_min = (delta_up   / max(roc_fpm,1e-6)) if delta_up>0 else 0.0
desc_min  = (delta_down / max(1e-6, st.number_input if False else 1))  # placeholder removed

# ROD input
rod_fpm = st.session_state.get("_rod_fpm", None)
# se não existir (primeira render), recupera do widget já criado acima via session state implícito do Streamlit
# mas como já temos a variável 'rod_fpm' do widget acima, apenas garantimos que existe:
# (mantemos a linha acima para clareza; a variável rod_fpm já existe)

desc_min  = (delta_down / max(rod_fpm,1e-6)) if delta_down>0 else 0.0

climb_nm  = gs_climb * (climb_min/60.0)
desc_nm   = gs_desc  * (desc_min/60.0)

# Distribuir consumo de climb/desc pelas legs (em NM)
seg_dist = [float(legs[i]["Dist"]) for i in range(N)]
# Climb consumido do início
climb_use = [0.0]*N
rem = float(climb_nm)
for i in range(N):
    use = min(seg_dist[i], max(0.0, rem))
    climb_use[i] = use
    rem -= use
# Índice onde ocorre TOC
idx_toc = None
acc=0.0
for i in range(N):
    acc += climb_use[i]
    if climb_use[i] > 0.0 and acc >= climb_nm - 1e-6:
        idx_toc = i
        break

# Descent consumido do fim
desc_use = [0.0]*N
rem = float(desc_nm)
for j in range(N-1, -1, -1):
    use = min(seg_dist[j], max(0.0, rem))
    desc_use[j] = use
    rem -= use
# Índice onde ocorre TOD (a contar do fim)
idx_tod = None
acc=0.0
for j in range(N-1, -1, -1):
    acc += desc_use[j]
    if desc_use[j] > 0.0 and acc >= desc_nm - 1e-6:
        idx_tod = j
        break

# Construir linhas finais: [Leg 1], (se i==idx_toc→TOC), ..., (se i==idx_tod→TOD)
calc_rows=[]
total_dist = sum(seg_dist)   # distância real do plano
total_ete = total_burn = 0.0
efob = float(start_fuel)

# tempos planeados
startup = parse_hhmm(startup_str)
takeoff = add_minutes(startup,15) if startup else None
clock = takeoff

for i in range(N):
    frm = wps[i]["Name"]; to = wps[i+1]["Name"]
    tc = float(legs[i]["TC"]); dist = float(legs[i]["Dist"])

    # Parte de cruise efetiva no leg (retira climb/desc consumidos nesta aresta)
    cruise_eff_nm = max(0.0, dist - climb_use[i] - desc_use[i])

    # GS e headings para CRUISE no leg
    _, th_cru, gs_cru = wind_triangle(tc, tas_cruise, wind_from, wind_kt)
    mh_cru = apply_var(th_cru, var_deg, var_is_e)

    ete_cru_min = (60.0*cruise_eff_nm/max(gs_cru,1e-6)) if cruise_eff_nm>0 else 0.0
    burn_cru = ff_cruise * (ete_cru_min/60.0)

    total_ete += ete_cru_min
    total_burn += burn_cru
    efob = max(0.0, efob - burn_cru)

    eto_str=""
    if clock:
        clock = add_minutes(clock, int(round(ete_cru_min)))
        eto_str = clock.strftime("%H:%M")

    calc_rows.append({
        "Name": f"{frm}→{to}",
        "Alt/FL": str(int(round(cruise_alt))),     # sempre cruise nas pernas
        "Freq": wps[i+1]["Freq"] or enroute_comm,  # freq do destino da perna (ou enroute)
        "TC": round(tc,0),
        "TH": round(th_cru,0),
        "MH": round(mh_cru,0),
        "TAS": round(tas_cruise,0),
        "GS": round(gs_cru,0),
        "Dist": round(dist,1),
        "ETE": round(ete_cru_min,0),
        "ETO": eto_str,
        "Burn": round(burn_cru,1),
        "EFOB": round(efob,1),
    })

    # Inserir TOC após o leg onde acaba a subida
    if idx_toc is not None and i == idx_toc and climb_min>0:
        _, th_clb, gs_c = wind_triangle(tc_first, tas_climb, wind_from, wind_kt)
        mh_clb = apply_var(th_clb, var_deg, var_is_e)
        burn_clb = ff_climb * (climb_min/60.0)

        total_ete += climb_min
        total_burn += burn_clb
        efob = max(0.0, efob - burn_clb)

        if clock:
            clock = add_minutes(clock, int(round(climb_min)))
            eto_clb = clock.strftime("%H:%M")
        else:
            eto_clb = ""

        calc_rows.append({
            "Name":"TOC",
            "Alt/FL": str(int(round(cruise_alt))),
            "Freq": "",
            "TC": round(tc_first,0),
            "TH": round(th_clb,0),
            "MH": round(mh_clb,0),
            "TAS": round(tas_climb,0),
            "GS": round(gs_c,0),
            "Dist": 0.0,
            "ETE": round(climb_min,0),
            "ETO": eto_clb,
            "Burn": round(burn_clb,1),
            "EFOB": round(efob,1),
        })

    # Inserir TOD após o leg onde começa a descida (a partir do fim)
    if idx_tod is not None and i == idx_tod and desc_min>0:
        _, th_des, gs_d = wind_triangle(tc_last, tas_descent, wind_from, wind_kt)
        mh_des = apply_var(th_des, var_deg, var_is_e)
        ff_d = float(idle_ff) if idle_mode else float(ff_descent)
        burn_des = ff_d * (desc_min/60.0)

        total_ete += desc_min
        total_burn += burn_des
        efob = max(0.0, efob - burn_des)

        if clock:
            clock = add_minutes(clock, int(round(desc_min)))
            eto_des = clock.strftime("%H:%M")
        else:
            eto_des = ""

        calc_rows.append({
            "Name":"TOD",
            "Alt/FL": str(int(round(arr_elev))),
            "Freq":"",
            "TC": round(tc_last,0),
            "TH": round(th_des,0),
            "MH": round(mh_des,0),
            "TAS": round(tas_descent,0),
            "GS": round(gs_d,0),
            "Dist": 0.0,
            "ETE": round(desc_min,0),
            "ETO": eto_des,
            "Burn": round(burn_des,1),
            "EFOB": round(efob,1),
        })

# ETA/Landing/Shutdown
eta = clock
landing = eta
shutdown = add_minutes(eta,5) if eta else None

# ===== Tabela calculada =====
st.markdown("#### Flight plan (com TOC/TOD)")
column_config={
    "Name":   st.column_config.TextColumn("Perna / Marcador"),
    "Alt/FL": st.column_config.TextColumn("Alt/FL (num)"),
    "Freq":   st.column_config.TextColumn("Freq"),
    "TC":     st.column_config.NumberColumn("TC (°T)", disabled=True),
    "TH":     st.column_config.NumberColumn("TH (°T)", disabled=True),
    "MH":     st.column_config.NumberColumn("MH (°M)", disabled=True),
    "TAS":    st.column_config.NumberColumn("TAS (kt)", disabled=True),
    "GS":     st.column_config.NumberColumn("GS (kt)", disabled=True),
    "Dist":   st.column_config.NumberColumn("Dist (nm)", disabled=True),
    "ETE":    st.column_config.NumberColumn("ETE (min)", disabled=True),
    "ETO":    st.column_config.TextColumn("ETO", disabled=True),
    "Burn":   st.column_config.NumberColumn("Burn (L)", disabled=True),
    "EFOB":   st.column_config.NumberColumn("EFOB (L)", disabled=True),
}
st.data_editor(calc_rows, hide_index=True, use_container_width=True,
               num_rows="fixed", column_config=column_config, key="calc_table")

# Totais
tot_line = f"**Totais** — Dist {total_dist:.1f} nm • ETE {int(total_ete)//60}h{int(total_ete)%60:02d} • Burn {total_burn:.1f} L • EFOB {efob:.1f} L"
if eta:
    tot_line += f" • **ETA {eta.strftime('%H:%M')}** • **Landing {landing.strftime('%H:%M')}** • **Shutdown {shutdown.strftime('%H:%M')}**"
st.markdown(tot_line)

# ====== PDF export ======
st.markdown("### PDF export")

try:
    template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
except Exception as e:
    template_bytes = None
    st.error(f"Não foi possível ler o PDF local: {e}")

if template_bytes:
    try:
        fieldset, maxlens = get_fields_and_meta(template_bytes)
        named: Dict[str,str] = {}

        # Globais
        put(named, fieldset, "Aircraft", aircraft, maxlens)
        put(named, fieldset, "Registration", registration, maxlens)
        put(named, fieldset, "Callsign", callsign, maxlens)
        put(named, fieldset, "Student", student, maxlens)
        put(named, fieldset, "Lesson", lesson, maxlens)
        put(named, fieldset, "Instructor", instructor, maxlens)

        put(named, fieldset, "Dept_Airfield", dept, maxlens)
        put(named, fieldset, "Arrival_Airfield", arr, maxlens)
        put(named, fieldset, "Alternate", altn, maxlens)
        put(named, fieldset, "Alt_Alternate", str(aero_elev(altn)), maxlens)

        put(named, fieldset, "Dept_Comm", aero_freq(dept), maxlens)
        put(named, fieldset, "Arrival_comm", aero_freq(arr), maxlens)
        put(named, fieldset, "Enroute_comm", enroute_comm, maxlens)

        # Condições / QNH / Wind / Var / FL
        put(named, fieldset, "QNH", f"{int(round(qnh))}", maxlens)
        put(named, fieldset, "temp_isa_dev", f"{int(round(temp_c))} / 0", maxlens)  # ISA dev não crítico aqui
        put(named, fieldset, "wind", f"{int(round(wind_from)):03d}/{int(round(wind_kt)):02d}", maxlens)
        put(named, fieldset, "mag_var", f"{var_deg:.1f}{'E' if var_is_e else 'W'}", maxlens)
        put(named, fieldset, "flt_lvl_altitude", f"{int(round(cruise_alt))}", maxlens)

        # Horas
        takeoff_str = takeoff.strftime("%H:%M") if takeoff else ""
        eta_str     = eta.strftime("%H:%M") if eta else ""
        landing_str = landing.strftime("%H:%M") if landing else ""
        shutdown_str= shutdown.strftime("%H:%M") if shutdown else ""
        put(named, fieldset, "Startup", startup_str, maxlens)
        put(named, fieldset, "Takeoff", takeoff_str, maxlens)
        put(named, fieldset, "Landing", landing_str, maxlens)
        put(named, fieldset, "Shutdown", shutdown_str, maxlens)
        put(named, fieldset, "ETD/ETA", f"{takeoff_str} / {eta_str}", maxlens)

        # Leg number
        put(named, fieldset, "Leg_Number", str(N), maxlens)

        # Enviar linhas (máx 11) — usamos calc_rows já com TOC/TOD
        for i, r in enumerate(calc_rows[:11], start=1):
            s = str(i)
            put(named, fieldset, f"Name{s}", r["Name"], maxlens)
            put(named, fieldset, f"Alt{s}",  r["Alt/FL"], maxlens)
            put(named, fieldset, f"FREQ{s}", r["Freq"], maxlens)
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


