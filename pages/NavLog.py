# app.py — NAVLOG (Waypoints → Legs) + TOC/TOD corretos + AFM perf + export p/ "NAVLOG - FORM.pdf"
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
    str_fields = {k: (str(v) if v is not None else "") for k, v in fields.items()}
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
    cross=wind_kt*sin(beta)
    s=max(-1.0,min(1.0,cross/max(tas_kt,1e-9)))
    wca=degrees(asin(s))
    gs=tas_kt*(1.0 - s*s) ** 0.5 - wind_kt*(1.0 - (sin(beta)**2)) ** 0.5  # tas*cos(wca) - W*cos(beta)
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

# ========================= AFM tables (excerto) =========================
def clamp(v,lo,hi): return max(lo,min(hi,v))
def interp1(x,x0,x1,y0,y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0+t*(y1-y0)

# En-route ROC (flaps UP) @ 650 kg
ROC = {
    650:{0:{-25:951,0:805,25:675,50:557},2000:{-25:840,0:696,25:568,50:453},4000:{-25:729,0:588,25:462,50:349},
         6000:{-25:619,0:480,25:357,50:245},8000:{-25:509,0:373,25:251,50:142},10000:{-25:399,0:266,25:146,50:39},
         12000:{-25:290,0:159,25:42,50:-64},14000:{-25:181,0:53,25:-63,50:-166}},
}
VY = {650:{0:70,2000:69,4000:68,6000:67,8000:65,10000:64,12000:63,14000:62}}

# Cruise perf (PA → rpm → (TAS kt, FF L/h)) — só usamos FF daqui; TAS de cruzeiro é fixo (80 kt)
CRUISE={
    0:{2000:(95,18.7),2100:(101,20.7),2250:(110,24.6)},
    2000:{2000:(94,17.5),2100:(100,19.9),2250:(109,23.5)},
    4000:{2000:(94,17.5),2100:(100,19.2),2250:(108,22.4)},
    6000:{2000:(93,17.1),2100:(99,18.5),2250:(108,21.3)},
    8000:{2000:(92,16.7),2100:(98,18.0),2250:(107,20.4)},
    10000:{2000:(91,16.4),2100:(97,17.5),2250:(106,19.7)},
}
def isa_temp(pa_ft): return 15.0 - 2.0*(pa_ft/1000.0)
def cruise_ff_lookup(pa_ft: float, rpm: int, oat_c: Optional[float]) -> float:
    # devolve só FF (L/h) interpolado; TAS não interessa (vamos usar 80 kt ref)
    pas=sorted(CRUISE.keys()); pa_c=clamp(pa_ft,pas[0],pas[-1])
    p0=max([p for p in pas if p<=pa_c]); p1=min([p for p in pas if p>=pa_c])
    def val(pa):
        table=CRUISE[pa]; rpms=sorted(table.keys())
        if rpm in table: return table[rpm][1]
        if rpm<rpms[0]: lo,hi=rpms[0],rpms[1]
        elif rpm>rpms[-1]: lo,hi=rpms[-2],rpms[-1]
        else:
            lo=max([r for r in rpms if r<=rpm]); hi=min([r for r in rpms if r>=rpm])
        ff_lo, ff_hi = table[lo][1], table[hi][1]
        t=(rpm-lo)/(hi-lo) if hi!=lo else 0.0
        return ff_lo + t*(ff_hi-ff_lo)
    ff0=val(p0); ff1=val(p1)
    ff=interp1(pa_c,p0,p1,ff0,ff1)
    # correção simples por OAT vs ISA (±15°C → FF −2.5/+3%)
    if oat_c is not None:
        dev=oat_c - isa_temp(pa_c)
        if dev>0:
            ff *= 1.0 - 0.025*(dev/15.0)
        elif dev<0:
            ff *= 1.0 + 0.03*((-dev)/15.0)
    return max(0.0,ff)

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

# ========================= Aerodromes (freqs numéricas) =========================
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
    lesson = st.text_input("Lesson", "")
    instructor = st.text_input("Instructor", "")
with c3:
    dept=st.selectbox("Departure",list(AEROS.keys()),index=0)
    arr =st.selectbox("Arrival",list(AEROS.keys()),index=1)
    altn=st.selectbox("Alternate",list(AEROS.keys()),index=2)

startup_str=st.text_input("Startup (HH:MM)","")

# Atmosfera & navegação
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

# Performance
c7,c8,c9=st.columns(3)
with c7:
    tas_cruise_ref = st.number_input("Cruise TAS ref (kt)", 50, 140, 80, step=1)  # FIXO por requisito
    rpm_cruise=st.number_input("Cruise RPM (para FF)",1800,2388,2000,step=10)
with c8:
    rpm_descent=st.number_input("Descent RPM (if not IDLE)",1700,2300,1800,step=10)
    idle_mode = st.checkbox("Descent mostly IDLE", value=True)
with c9:
    rod_fpm=st.number_input("ROD (ft/min)",200,1500,700,step=10)
    idle_ff=st.number_input("Idle FF (L/h) (if IDLE)", 0.0, 20.0, 5.0, step=0.1)
    start_fuel=st.number_input("Fuel inicial (EFOB_START) [L]",0.0,1000.0,0.0,step=1.0)

# ========================= Waypoints base =========================
def blank_wp():
    return {"Name":"", "Alt/FL":"", "Freq":""}

# N waypoints → legs = N-1
N = st.number_input("Nº de waypoints (inclui Departure e Arrival)", 2, 12, 5)

if "waypoints" not in st.session_state:
    st.session_state.waypoints = [blank_wp() for _ in range(N)]

# resize mantendo o último como Arrival
def resize_wps(target_n:int):
    wps = st.session_state.waypoints
    if len(wps) < target_n:
        add = target_n - len(wps)
        insert_at = max(0, len(wps) - 1)
        for _ in range(add):
            wps.insert(insert_at, blank_wp())
    elif len(wps) > target_n:
        remove = len(wps) - target_n
        for _ in range(remove):
            if len(wps) > 1:
                wps.pop(-2)  # remove penúltimo
    st.session_state.waypoints = wps

resize_wps(N)
wps = st.session_state.waypoints

# Força Departure e Arrival (nome/freq/alt)
wps[0]["Name"]=dept; wps[0]["Alt/FL"]=str(aero_elev(dept)); wps[0]["Freq"]=aero_freq(dept)
wps[-1]["Name"]=arr;  wps[-1]["Alt/FL"]=str(aero_elev(arr));  wps[-1]["Freq"]=aero_freq(arr)

st.markdown("### Waypoints")
wp_cfg={
    "Name":   st.column_config.TextColumn("Name / Lat,Long"),
    "Alt/FL": st.column_config.TextColumn("Alt/FL (num)"),
    "Freq":   st.column_config.TextColumn("Freq"),
}
wps_edit = st.data_editor(
    wps,
    hide_index=True,
    use_container_width=True,
    num_rows="fixed",
    column_config=wp_cfg,
    key="wp_editor"
)

# aplicar edições (sem mexer em dep/arr)
for i in range(len(wps)):
    if i==0:
        wps[i].update({"Alt/FL":str(aero_elev(dept)), "Freq":aero_freq(dept)})
    elif i==len(wps)-1:
        wps[i].update({"Alt/FL":str(aero_elev(arr)),  "Freq":aero_freq(arr)})
    else:
        wps[i]["Name"]   = wps_edit[i]["Name"]
        wps[i]["Alt/FL"] = wps_edit[i]["Alt/FL"]
        wps[i]["Freq"]   = wps_edit[i]["Freq"]

# ========================= Legs editáveis (entre nós) =========================
# Um leg tem: From (wp[i]) → To (wp[i+1]) com TC e Dist "entre" os dois
def blank_leg():
    return {"From":"", "To":"", "TC":0.0, "Dist":0.0}

legs_base = []
for i in range(len(wps)-1):
    legs_base.append({
        "From": wps[i]["Name"],
        "To":   wps[i+1]["Name"],
        "TC":   0.0,
        "Dist": 0.0
    })

st.markdown("### Legs (entre waypoints)")
leg_cfg={
    "From":  st.column_config.TextColumn("From", disabled=True),
    "To":    st.column_config.TextColumn("To", disabled=True),
    "TC":    st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
    "Dist":  st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
}
legs_edit = st.data_editor(
    legs_base,
    hide_index=True,
    use_container_width=True,
    num_rows="fixed",
    column_config=leg_cfg,
    key="legs_editor"
)

# ========================= Cálculos TOC/TOD e linhas finais =========================
def pressure_alt(elev_ft, qnh_hpa): return float(elev_ft) + (1013.0 - float(qnh_hpa))*30.0

def compute_climb(dep_elev, cruise_alt, qnh, oat_c, tc_first):
    pa_dep = pressure_alt(dep_elev, qnh)
    roc = roc_interp(pa_dep, oat_c)     # ft/min
    vy  = vy_interp(pa_dep)             # kt (TAS de subida)
    _,_, gs_climb = wind_triangle(float(tc_first), float(vy), wind_from, wind_kt)
    delta = max(0.0, cruise_alt - dep_elev)
    t_min = delta / max(roc,1e-6)
    d_nm  = gs_climb * (t_min/60.0)
    # FF durante climb — aproximação: usa FF de 2250 rpm a PA média
    pa_mid = dep_elev + 0.5*delta
    ff = cruise_ff_lookup(pa_mid, 2250, oat_c)
    return t_min, d_nm, ff, vy, gs_climb

def compute_descent(arr_elev, cruise_alt, qnh, oat_c, tc_last, rod_fpm, rpm_desc, idle_mode, idle_ff):
    delta = max(0.0, cruise_alt - arr_elev)
    t_min = delta / max(rod_fpm,1e-6)
    # TAS de descida ≈ TAS cruzeiro ref (conservador)
    tas_last = float(tas_cruise_ref)
    _,_, gs_des = wind_triangle(float(tc_last), float(tas_last), wind_from, wind_kt)
    d_nm = gs_des * (t_min/60.0)
    # consumo
    if idle_mode:
        ff = float(idle_ff)
    else:
        pa_mid = arr_elev + 0.5*delta
        ff = cruise_ff_lookup(pa_mid, int(rpm_desc), oat_c)
    return t_min, d_nm, ff, tas_last, gs_des

dep_elev = aero_elev(dept); arr_elev = aero_elev(arr)
tc_first = float(legs_edit[0]["TC"] if legs_edit else 0.0)
tc_last  = float(legs_edit[-1]["TC"] if legs_edit else 0.0)

climb_min, climb_nm, climb_ff, tas_climb, gs_climb = compute_climb(dep_elev, cruise_alt, qnh, temp_c, tc_first)
desc_min,  desc_nm,  desc_ff, tas_des,   gs_des   = compute_descent(arr_elev, cruise_alt, qnh, temp_c, tc_last, rod_fpm, rpm_descent, idle_mode, idle_ff)

# Distribuir climb_nm desde o início pelos legs; descent desde o fim
climb_use = [0.0]*len(legs_edit)
rem = float(climb_nm)
for i, leg in enumerate(legs_edit):
    d = float(leg["Dist"])
    use = min(d, max(0.0, rem))
    climb_use[i] = use; rem -= use

desc_use = [0.0]*len(legs_edit)
rem = float(desc_nm)
for j in range(len(legs_edit)-1, -1, -1):
    d = float(legs_edit[j]["Dist"])
    use = min(d, max(0.0, rem))
    desc_use[j] = use; rem -= use

# Índices onde TOC/TOD caem
idx_toc = next((i for i,v in enumerate(climb_use) if v>0 and sum(climb_use[:i+1])>=climb_nm-1e-6), None)
idx_tod = None
acc=0.0
for j in range(len(desc_use)-1, -1, -1):
    acc += desc_use[j]
    if desc_use[j]>0 and acc>=desc_nm-1e-6:
        idx_tod = j
        break

# Construir linhas finais (com TOC/TOD como "virtual legs" de Dist=0 e ETE específico)
startup = parse_hhmm(startup_str)
takeoff = add_minutes(startup,15) if startup else None
clock = takeoff

rows=[]  # linhas finais (leg-level + TOC/TOD)
total_dist = sum(float(l["Dist"]) for l in legs_edit)  # soma real da rota
total_ete = total_burn = 0.0
efob = float(start_fuel)

for i, leg in enumerate(legs_edit):
    from_nm = leg["From"]; to_nm = leg["To"]
    tc = float(leg["TC"]); dist = float(leg["Dist"])

    # inserir TOC aqui?
    if idx_toc is not None and i==idx_toc:
        # Heading = TC deste leg, TAS=t as_climb (VY), GS=gs_climb, ETE = climb_min
        _, th_toc, _ = wind_triangle(tc, tas_climb, wind_from, wind_kt)
        mh_toc = apply_var(th_toc, var_deg, var_is_e)
        burn = climb_ff * (climb_min/60.0)
        total_ete += climb_min; total_burn += burn; efob = max(0.0, efob-burn)
        eto_str=""
        if clock:
            clock = add_minutes(clock, int(round(climb_min)))
            eto_str = clock.strftime("%H:%M")
        rows.append({
            "From":"—", "To":"TOC", "Alt/FL":str(int(round(cruise_alt))), "Freq":"",
            "TC":round(tc,0), "TH":round(th_toc,0), "MH":round(mh_toc,0),
            "TAS":round(tas_climb,0), "GS":round(gs_climb,0),
            "Dist":0.0, "ETE":round(climb_min,0), "ETO":eto_str,
            "Burn":round(burn,1), "EFOB":round(efob,1)
        })

    # parâmetros do leg efetivo (cruzeiro 80 kt, removendo partes de climb/desc)
    tas_leg = float(tas_cruise_ref)
    ff_leg = cruise_ff_lookup(pressure_alt(cruise_alt, qnh), int(rpm_cruise), temp_c)
    _, th, gs = wind_triangle(tc, tas_leg, wind_from, wind_kt)
    mh = apply_var(th, var_deg, var_is_e)

    effective_dist = max(0.0, dist - climb_use[i] - desc_use[i])
    ete_min = (60.0*effective_dist/max(gs,1e-6)) if effective_dist>0 else 0.0
    burn = ff_leg * (ete_min/60.0)
    total_ete += ete_min; total_burn += burn; efob = max(0.0, efob-burn)

    eto_str=""
    if clock:
        clock = add_minutes(clock, int(round(ete_min)))
        eto_str = clock.strftime("%H:%M")

    rows.append({
        "From":from_nm, "To":to_nm, "Alt/FL":"", "Freq":"",
        "TC":round(tc,0), "TH":round(th,0), "MH":round(mh,0),
        "TAS":round(tas_leg,0), "GS":round(gs,0),
        "Dist":round(dist,1), "ETE":round(ete_min,0), "ETO":eto_str,
        "Burn":round(burn,1), "EFOB":round(efob,1)
    })

    # inserir TOD aqui?
    if idx_tod is not None and i==idx_tod:
        _, th_tod, _ = wind_triangle(tc, tas_cruise_ref, wind_from, wind_kt)
        mh_tod = apply_var(th_tod, var_deg, var_is_e)
        burn = desc_ff * (desc_min/60.0)
        total_ete += desc_min; total_burn += burn; efob = max(0.0, efob-burn)
        eto_str=""
        if clock:
            clock = add_minutes(clock, int(round(desc_min)))
            eto_str = clock.strftime("%H:%M")
        rows.append({
            "From":"TOD", "To":"—", "Alt/FL":str(aero_elev(arr)), "Freq":"",
            "TC":round(tc,0), "TH":round(th_tod,0), "MH":round(mh_tod,0),
            "TAS":round(tas_cruise_ref,0), "GS":round(gs_des,0),
            "Dist":0.0, "ETE":round(desc_min,0), "ETO":eto_str,
            "Burn":round(burn,1), "EFOB":round(efob,1)
        })

eta = clock
landing = eta
shutdown = add_minutes(eta,5) if eta else None

# ========================= Tabela final =========================
st.markdown("### Flight plan (derivado) — inclui TOC/TOD")
col_cfg = {
    "From":  st.column_config.TextColumn("From", disabled=True),
    "To":    st.column_config.TextColumn("To", disabled=True),
    "Alt/FL":st.column_config.TextColumn("Alt/FL", disabled=True),
    "Freq":  st.column_config.TextColumn("Freq", disabled=True),
    "TC":    st.column_config.NumberColumn("TC (°T)", disabled=True),
    "TH":    st.column_config.NumberColumn("TH (°T)", disabled=True),
    "MH":    st.column_config.NumberColumn("MH (°M)", disabled=True),
    "TAS":   st.column_config.NumberColumn("TAS (kt)", disabled=True),
    "GS":    st.column_config.NumberColumn("GS (kt)", disabled=True),
    "Dist":  st.column_config.NumberColumn("Dist (nm)", disabled=True),
    "ETE":   st.column_config.NumberColumn("ETE (min)", disabled=True),
    "ETO":   st.column_config.TextColumn("ETO", disabled=True),
    "Burn":  st.column_config.NumberColumn("Burn (L)", disabled=True),
    "EFOB":  st.column_config.NumberColumn("EFOB (L)", disabled=True),
}
st.data_editor(rows, hide_index=True, use_container_width=True, num_rows="fixed", column_config=col_cfg, key="final_table")

tot_line = f"**Totais** — Dist {total_dist:.1f} nm • ETE {int(total_ete)//60}h{int(total_ete)%60:02d} • Burn {total_burn:.1f} L • EFOB {efob:.1f} L"
if eta:
    tot_line += f" • **ETA {eta.strftime('%H:%M')}** • **Landing {landing.strftime('%H:%M')}** • **Shutdown {shutdown.strftime('%H:%M')}**"
st.markdown(tot_line)

# ========================= PDF export =========================
st.markdown("### PDF export")
template_bytes = None
try:
    template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
except Exception as e:
    st.error(f"Não foi possível ler o PDF do repositório: {e}")

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

        # Condições
        pa_dep = pressure_alt(aero_elev(dept), qnh)
        isa_dev = round(temp_c - isa_temp(pa_dep))
        put(named, fieldset, "QNH", f"{int(round(qnh))}", maxlens)
        put(named, fieldset, "temp_isa_dev", f"{int(round(temp_c))} / {isa_dev}", maxlens)
        put(named, fieldset, "wind", f"{int(round(wind_from)):03d}/{int(round(wind_kt)):02d}", maxlens)
        put(named, fieldset, "mag_var", f"{var_deg:.1f}{'E' if var_is_e else 'W'}", maxlens)
        put(named, fieldset, "flt_lvl_altitude", f"{int(round(cruise_alt))}", maxlens)

        # Horas
        takeoff_str = add_minutes(parse_hhmm(startup_str),15).strftime("%H:%M") if parse_hhmm(startup_str) else ""
        eta_str     = eta.strftime("%H:%M") if eta else ""
        landing_str = landing.strftime("%H:%M") if landing else ""
        shutdown_str= shutdown.strftime("%H:%M") if shutdown else ""
        put(named, fieldset, "Startup", startup_str, maxlens)
        put(named, fieldset, "Takeoff", takeoff_str, maxlens)
        put(named, fieldset, "Landing", landing_str, maxlens)
        put(named, fieldset, "Shutdown", shutdown_str, maxlens)
        put(named, fieldset, "ETD/ETA", f"{takeoff_str} / {eta_str}", maxlens)

        # Leg number = nº de legs base (entre nós)
        put(named, fieldset, "Leg_Number", str(len(legs_edit)), maxlens)

        # Export das primeiras 11 linhas da TABELA FINAL (inclui TOC/TOD e legs)
        # Mapear para o formulário existente (Name/Alt/FREQ/TCRS/THDG/MHDG/TAS/GS/Dist/ETE/ETO/PL_BO/EFOB)
        # Usamos From→To como Name; Alt/Freq vazios (já em cabeçalho)
        for i, r in enumerate(rows[:11], start=1):
            s=str(i)
            nm = (r["From"] or "") + (" → " if r["From"] or r["To"] else "") + (r["To"] or "")
            put(named, fieldset, f"Name{s}", nm, maxlens)
            put(named, fieldset, f"Alt{s}",  r.get("Alt/FL",""), maxlens)
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


