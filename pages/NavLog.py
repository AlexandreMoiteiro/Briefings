# app.py — NAVLOG PDF Filler + Wind/TOC/TOD + AFM-based Fuel (no sidebar)
# Reqs: streamlit, pytz, pypdf

import streamlit as st
import datetime as dt
import pytz
from pathlib import Path
import io, unicodedata
from typing import List, Dict, Optional, Tuple
from math import sin, cos, asin, radians, degrees, fmod

# ========================= PDF helpers =========================
try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, TextStringObject
    PYPDF_OK = True
except Exception:
    PYPDF_OK = False

def ascii_safe(x: str) -> str:
    if x is None: return ""
    return unicodedata.normalize('NFKD', str(x)).encode('ascii','ignore').decode('ascii')

def read_pdf_bytes(paths: List[str]) -> bytes:
    for p in paths:
        pp = Path(p)
        if pp.exists(): return pp.read_bytes()
    raise FileNotFoundError(paths)

def get_fields_and_meta(template_bytes: bytes):
    reader = PdfReader(io.BytesIO(template_bytes))
    field_names, maxlens = set(), {}
    try:
        fd = reader.get_fields() or {}
        field_names |= set(fd.keys())
        for k, v in fd.items():
            try:
                ml = v.get("/MaxLen")
                if ml: maxlens[k] = int(ml)
            except Exception:
                pass
    except Exception:
        pass
    try:
        for page in reader.pages:
            if "/Annots" in page:
                for a in page["/Annots"]:
                    obj = a.get_object()
                    if obj.get("/T"):
                        nm = str(obj["/T"]); field_names.add(nm)
                        ml = obj.get("/MaxLen")
                        if ml: maxlens[nm] = int(ml)
    except Exception:
        pass
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
        # font size 0 (auto) + appearances on
        writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): True})
        writer._root_object["/AcroForm"].update({NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g")})
    except Exception:
        pass
    str_fields = {k: ("" if v is None else str(v)) for k,v in fields.items()}
    for page in writer.pages:
        writer.update_page_form_field_values(page, str_fields)
    bio = io.BytesIO(); writer.write(bio); return bio.getvalue()

def put_any(out: dict, fieldset: set, keys, value: str, maxlens: Dict[str,int]=None):
    if isinstance(keys, str): keys=[keys]
    for k in keys:
        if k in fieldset:
            s = "" if value is None else str(value)
            if maxlens and k in maxlens and len(s) > maxlens[k]:
                s = s[:maxlens[k]]
            out[k] = s

# ========================= Time helpers =========================
def parse_hhmm(s: str) -> Optional[dt.time]:
    s=(s or "").strip()
    for fmt in ("%H:%M","%H%M"):
        try: return dt.datetime.strptime(s, fmt).time()
        except Exception: pass
    return None

def add_minutes_to_time(t: dt.time, minutes: int, tzinfo=pytz.timezone("Europe/Lisbon")) -> Optional[dt.time]:
    if not t: return None
    today = dt.date.today()
    base = tzinfo.localize(dt.datetime.combine(today, t))
    new_dt = base + dt.timedelta(minutes=int(minutes or 0))
    return new_dt.timetz().replace(tzinfo=None)

# ========================= Wind triangle =========================
def wrap360(x): x=fmod(x,360.0); return x+360.0 if x<0 else x
def angle_diff(a,b): return (a-b+180.0)%360.0 - 180.0

def wind_triangle(tc_deg, tas_kt, wind_from_deg, wind_kt):
    if tas_kt<=0: return 0.0, wrap360(tc_deg), 0.0
    beta = radians(angle_diff(wind_from_deg, tc_deg))
    cross = wind_kt * sin(beta); head = wind_kt * cos(beta)
    s = max(-1.0, min(1.0, cross/max(tas_kt,1e-9)))
    wca = degrees(asin(s)); gs = tas_kt*cos(radians(wca)) - head
    th = wrap360(tc_deg + wca)
    return wca, th, max(0.0, gs)

def apply_var(true_deg, var_deg, east_is_negative=False):
    # East is least (−), West is best (+)
    return wrap360(true_deg - var_deg if east_is_negative else true_deg + var_deg)

# ========================= AFM performance tables =========================
# ROC (En-route, flaps UP) + Vy from your AFM extracts
ROC = {
    650:{0:{-25:981,0:835,25:704,50:586,"ISA":755},2000:{-25:870,0:726,25:597,50:481,"ISA":667},
         4000:{-25:759,0:617,25:491,50:377,"ISA":580},6000:{-25:648,0:509,25:385,50:273,"ISA":493},
         8000:{-25:538,0:401,25:279,50:170,"ISA":406},10000:{-25:428,0:294,25:174,50:66,"ISA":319},
         12000:{-25:319,0:187,25:69,50:-37,"ISA":232},14000:{-25:210,0:80,25:-35,50:-139,"ISA":145}},
    600:{0:{-25:1104,0:948,25:809,50:683,"ISA":863},2000:{-25:985,0:832,25:695,50:572,"ISA":770},
         4000:{-25:867,0:717,25:582,50:461,"ISA":677},6000:{-25:750,0:602,25:470,50:351,"ISA":585},
         8000:{-25:632,0:487,25:357,50:240,"ISA":492},10000:{-25:515,0:373,25:245,50:131,"ISA":399},
         12000:{-25:399,0:259,25:134,50:21,"ISA":307},14000:{-25:283,0:145,25:23,50:-88,"ISA":214}},
    550:{0:{-25:1245,0:1078,25:929,50:794,"ISA":987},2000:{-25:1118,0:954,25:807,50:675,"ISA":887},
         4000:{-25:992,0:830,25:686,50:556,"ISA":788},6000:{-25:865,0:707,25:565,50:438,"ISA":688},
         8000:{-25:740,0:584,25:445,50:319,"ISA":589},10000:{-25:614,0:461,25:325,50:202,"ISA":490},
         12000:{-25:489,0:339,25:205,50:84,"ISA":390},14000:{-25:365,0:218,25:86,50:-33,"ISA":291}},
}
VY = {650:{0:67,2000:67,4000:67,6000:67,8000:67,10000:67,12000:67,14000:67},
      600:{0:67,2000:67,4000:67,6000:66,8000:65,10000:64,12000:63,14000:62},
      550:{0:67,2000:67,4000:67,6000:66,8000:66,10000:66,12000:66,14000:66}}

def clamp(v, lo, hi): return max(lo, min(hi, v))
def interp1(x, x0, x1, y0, y1):
    if x1==x0: return y0
    t=(x-x0)/(x1-x0); return y0 + t*(y1-y0)

def roc_interp(pa, temp, weight):
    w = clamp(weight, 550.0, 650.0)
    def roc_for_w(w_):
        tab = ROC[int(w_)]
        pas = sorted(tab.keys())
        pa_c = clamp(pa, pas[0], pas[-1])
        p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
        temps = [-25,0,25,50]; t = clamp(temp, temps[0], temps[-1])
        if t <= 0: t0,t1=-25,0
        elif t <= 25: t0,t1=0,25
        else: t0,t1=25,50
        v00, v01 = tab[p0][t0], tab[p0][t1]
        v10, v11 = tab[p1][t0], tab[p1][t1]
        v0 = interp1(t, t0, t1, v00, v01); v1 = interp1(t, t0, t1, v10, v11)
        return interp1(pa_c, p0, p1, v0, v1)
    if w <= 600: return interp1(w, 550, 600, roc_for_w(550), roc_for_w(600))
    return interp1(w, 600, 650, roc_for_w(600), roc_for_w(650))

def vy_interp(pa, weight):
    w_choice = 550 if weight <= 575 else (600 if weight <= 625 else 650)
    table = VY[w_choice]; pas = sorted(table.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    return interp1(pa_c, p0, p1, table[p0], table[p1])

# -------- Cruise Performance table (PA → {rpm:{tas,ff}}) for weight 650 kg --------
CRUISE = {
    0:    {2388:(118,26.9), 2250:(110,24.6), 2100:(101,20.7), 2000:(95,18.7), 1900:(89,17.0), 1800:(83,15.6)},
    2000: {2250:(109,23.5), 2100:(100,19.9), 2000:(94,17.5), 1900:(88,16.2), 1800:(82,15.3)},
    4000: {2250:(108,22.4), 2100:(100,19.2), 2000:(94,17.5), 1900:(88,16.2), 1800:(82,15.1)},
    6000: {2250:(108,21.3), 2100:(99,18.5), 2000:(93,17.1), 1900:(87,15.9), 1800:(81,14.9)},
    8000: {2250:(107,20.4), 2100:(98,18.0), 2000:(92,16.7), 1900:(86,15.6)},
    10000:{2250:(106,19.7), 2100:(97,17.5), 2000:(91,16.4), 1900:(85,15.4)},
}
# OAT corrections (±15°C → TAS −2/+1%; FF −2.5/+3%)
def apply_cruise_oat_corrections(tas, ff, oat_dev_c):
    if oat_dev_c > 0:
        tas *= 1.0 - 0.02 * (oat_dev_c/15.0)
        ff  *= 1.0 - 0.025* (oat_dev_c/15.0)
    elif oat_dev_c < 0:
        tas *= 1.0 + 0.01 * (abs(oat_dev_c)/15.0)
        ff  *= 1.0 + 0.03 * (abs(oat_dev_c)/15.0)
    return tas, ff

def isa_temp_at(pa_ft: float) -> float:
    return 15.0 - 2.0*(pa_ft/1000.0)

def cruise_lookup(pa_ft: float, rpm: int, oat_c: Optional[float]) -> Tuple[float,float]:
    # interp em PA → vizinhos; interp linear em rpm quando não existe entrada exata
    pas = sorted(CRUISE.keys())
    pa_c = clamp(pa_ft, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])

    def tas_ff_at(pa, rpm):
        table = CRUISE[pa]
        rpms = sorted(table.keys())
        if rpm in table:
            tas, ff = table[rpm]
        else:
            # interp entre dois RPM adjacentes
            rpms_all = sorted(rpms + [rpm])
            idx = rpms_all.index(rpm)
            lo = rpms_all[idx-1] if idx>0 else rpms[0]
            hi = rpms_all[idx+1] if idx<len(rpms_all)-1 else rpms[-1]
            tlo = table[lo]; thi = table[hi]
            t = (rpm - lo)/(hi - lo) if hi!=lo else 0.0
            tas = tlo[0] + t*(thi[0]-tlo[0]); ff = tlo[1] + t*(thi[1]-tlo[1])
        # aplicar OAT correction se fornecido
        if oat_c is not None:
            dev = oat_c - isa_temp_at(pa)
            tas, ff = apply_cruise_oat_corrections(tas, ff, dev)
        return tas, ff

    tas0, ff0 = tas_ff_at(p0, rpm)
    tas1, ff1 = tas_ff_at(p1, rpm)
    tas = interp1(pa_c, p0, p1, tas0, tas1)
    ff  = interp1(pa_c, p0, p1, ff0,  ff1)
    return tas, ff

# ========================= Aerodromes DB =========================
AERODROMES = {
    "LPSO": {"elev_ft":390,  "freqs":["119.805 (Pte Sor INFO)","123.755 (Lisboa Info)"]},
    "LPEV": {"elev_ft":807,  "freqs":["122.705 (Evora INFO)","123.755 (Lisboa Info)","131.055 (Lisboa Info)"]},
    "LPCB": {"elev_ft":1251, "freqs":["130.905 (Lisboa Info)","132.305 (Lisboa CTR)","123.755 (Lisboa Info)"]},
    "LPCO": {"elev_ft":587,  "freqs":["130.905 (Lisboa Info)","132.305 (Lisboa CTR)"]},
    "LPVZ": {"elev_ft":2060, "freqs":["130.905 (Lisboa Info)","132.305 (Lisboa CTR)"]},
}
def aero_elev(icao): return int(AERODROMES.get((icao or "").upper(),{}).get("elev_ft",0))
def aero_freqs(icao): return " / ".join(AERODROMES.get((icao or "").upper(),{}).get("freqs",[]))

# ========================= App =========================
st.set_page_config(page_title="NAVLOG — Log & Performance", layout="wide", initial_sidebar_state="collapsed")
st.title("Navigation Plan & Inflight Log")

DEFAULT_STUDENT="AMOIT"; DEFAULT_AIRCRAFT="P208"
REGS=["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"]
PDF_TEMPLATE_PATHS=["/mnt/data/NAVLOG - FORM.pdf","NAVLOG - FORM.pdf"]

# ----- Header -----
st.subheader("Header")
c1,c2,c3 = st.columns(3)
with c1:
    aircraft = st.text_input("Aircraft", DEFAULT_AIRCRAFT)
    registration = st.selectbox("Registration", REGS, index=0)
    callsign = st.text_input("Callsign", "RVP")
with c2:
    student = st.text_input("Student", DEFAULT_STUDENT)
    dept = st.selectbox("Departure (ICAO)", ["LPSO","LPEV","LPCB","LPCO","LPVZ"], index=0)
    arr  = st.selectbox("Arrival (ICAO)",   ["","LPSO","LPEV","LPCB","LPCO","LPVZ"], index=1)
with c3:
    altn = st.selectbox("Alternate (ICAO)", ["","LPSO","LPEV","LPCB","LPCO","LPVZ"], index=2)
    startup_str  = st.text_input("Startup (HH:MM)", "")
    # ETA é calculada; Landing = ETA; Shutdown = ETA + 5
    cruise_oat = st.number_input("Cruise OAT (°C) (deixa vazio=ISA)", -60.0, 60.0, value=15.0, step=0.5)

st.subheader("Atmosfera & Performance setup")
c4,c5,c6,c7 = st.columns(4)
with c4:
    qnh_hpa  = st.number_input("QNH (hPa)", 900.0, 1050.0, 1013.0, 0.1)
    weight_kg= st.number_input("Weight (kg) (para AFM)", 450.0, 650.0, 650.0, 1.0)
with c5:
    wind_from= st.number_input("Wind FROM (°TRUE)", 0.0, 360.0, 0.0, 1.0)
    wind_kt  = st.number_input("Wind (kt)", 0.0, 200.0, 0.0, 1.0)
with c6:
    var_deg  = st.number_input("Mag variation (°) (def. 1W)", 0.0, 30.0, 1.0, 0.1)
    var_is_e = (st.selectbox("Var (E/W)", ["W","E"], index=0) == "E")  # default 1W
with c7:
    cruise_alt_ft = st.number_input("Cruise Altitude (ft)", 0, 14000, 3000, 100)
    rod_fpm = st.number_input("Descent rate (ROD) [ft/min]", 200, 1500, 700, 10)

# Comms + Elevations
dept_comm = st.text_input("Departure Comms", aero_freqs(dept))
enroute_comm = st.text_input("Enroute Comms", "Lisboa Info 123.755")
arrival_comm = st.text_input("Arrival Comms", aero_freqs(arr))
dep_elev_ft = st.number_input("Departure Elev (ft)", 0, 5000, aero_elev(dept), 1)
arr_elev_ft = st.number_input("Arrival Elev (ft)", 0, 5000, aero_elev(arr), 1)
alt_elev_ft = st.number_input("Alternate Elev (ft)",0, 5000, aero_elev(altn), 1)

# ----- Legs (única tabela) -----
st.subheader("Legs (1–11) — a primeira é o Departure; a última o Arrival; é adicionado um leg 'Alternate'")
N = st.number_input("Nº de legs (sem contar 'Alternate')", 1, 11, 6)
if "legs" not in st.session_state:
    st.session_state.legs=[{"Name":"","Alt/FL":"","Freq":"","TC":0.0,"TAS":0.0,"Dist":0.0} for _ in range(N)]

cur = st.session_state.legs
if len(cur)!=N:
    if len(cur)<N:
        cur += [{"Name":"","Alt/FL":"","Freq":"","TC":0.0,"TAS":0.0,"Dist":0.0} for _ in range(N-len(cur))]
    else:
        st.session_state.legs = cur[:N]

# Forçar 1º/último com info de aeródromos
if st.session_state.legs:
    st.session_state.legs[0]["Name"]   = st.session_state.legs[0].get("Name") or dept
    st.session_state.legs[0]["Alt/FL"] = st.session_state.legs[0].get("Alt/FL") or f"{dep_elev_ft} ft"
    st.session_state.legs[-1]["Name"]   = st.session_state.legs[-1].get("Name") or arr
    st.session_state.legs[-1]["Alt/FL"] = st.session_state.legs[-1].get("Alt/FL") or f"{arr_elev_ft} ft"

legs = st.data_editor(
    st.session_state.legs, num_rows="fixed", use_container_width=True, hide_index=True,
    column_config={
        "Name": st.column_config.TextColumn("Name / Lat,Long"),
        "Alt/FL": st.column_config.TextColumn("Alt / FL"),
        "Freq": st.column_config.TextColumn("Freq."),
        "TC": st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
        "TAS": st.column_config.NumberColumn("TAS (kt) (0=usar tabela cruzeiro)", step=1.0, min_value=0.0),
        "Dist": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
    }, key="legs_editor"
)

# ========================= Cálculos principais =========================
def pa_ft(elev_ft, qnh):
    return float(elev_ft) + (1013.0 - float(qnh)) * 30.0

# TAS/FF por fase via CRUISE table (+correções OAT)
def ff_tas_for_phase(pa_mid_ft, rpm, oat_c_opt: Optional[float]) -> Tuple[float,float]:
    # se oat_c_opt é None ⇒ assume ISA
    tas, ff = cruise_lookup(pa_mid_ft, int(rpm), oat_c_opt)
    return tas, ff

def compute_climb(dep_elev_ft, cruise_alt_ft, qnh, oat_c, weight, tc_first) -> Tuple[float,float,float,float]:
    """return: (time_min, dist_nm, tas_climb, ff_lph)"""
    pa = pa_ft(dep_elev_ft, qnh)
    roc = max(1.0, roc_interp(pa, oat_c, weight))  # ft/min
    vy  = vy_interp(pa, weight)                    # ~KIAS (usaremos como TAS climb)
    tas_climb = max(60.0, float(vy))
    wca, th, gs_climb = wind_triangle(tc_first, tas_climb, wind_from, wind_kt)
    delta_ft = max(0.0, cruise_alt_ft - dep_elev_ft)
    t_min = delta_ft / roc
    d_nm  = gs_climb * (t_min/60.0)
    # consumo: usar tabela de cruzeiro no PA médio com RPM de climb (2250 por defeito)
    climb_rpm = st.number_input("Climb RPM", 1800, 2388, 2250, 10, key="rpm_climb")
    pa_mid = dep_elev_ft + 0.5*delta_ft
    tas_tab, ff_lph = ff_tas_for_phase(pa_mid, climb_rpm, oat_c)
    return t_min, d_nm, tas_climb, ff_lph

def compute_descent(arr_elev_ft, cruise_alt_ft, tc_last) -> Tuple[float,float,float]:
    """return: (time_min, dist_nm, ff_lph)"""
    delta_ft = max(0.0, cruise_alt_ft - arr_elev_ft)
    # dist pela razão escolhida (ROD)
    t_min = delta_ft / max(rod_fpm, 1e-6)
    # GS do último perna
    tas_last = st.session_state.legs[-1].get("TAS") or 0.0
    if not tas_last:
        tas_last = cruise_lookup(cruise_alt_ft, st.session_state.get("rpm_cruise",2000), cruise_oat)[0]
    _, _, gs_des = wind_triangle(tc_last, float(tas_last), wind_from, wind_kt)
    d_nm = gs_des * (t_min/60.0)
    # consumo: usar tabela cruzeiro no PA médio com RPM de descent (default 1800)
    desc_rpm = st.number_input("Descent RPM", 1700, 2250, 1800, 10, key="rpm_descent")
    pa_mid = arr_elev_ft + 0.5*delta_ft
    _, ff_lph = ff_tas_for_phase(pa_mid, desc_rpm, cruise_oat)
    return t_min, d_nm, ff_lph

# RPM cruzeiro (para legs)
rpm_cruise = st.number_input("Cruise RPM (para legs)", 1800, 2388, 2000, 10, key="rpm_cruise")

# Cálculo TOC/TOD
tc_first = float(legs[0].get("TC") or 0.0)
tc_last  = float(legs[-1].get("TC") or 0.0)
climb_min, climb_nm, tas_climb, ff_climb = compute_climb(dep_elev_ft, cruise_alt_ft, qnh_hpa, cruise_oat, weight_kg, tc_first)
desc_min,  desc_nm,  ff_desc  = compute_descent(arr_elev_ft, cruise_alt_ft, tc_last)

# Onde inserir TOC/TOD
cum = 0.0; idx_toc=None
for i, r in enumerate(legs):
    d=float(r.get("Dist") or 0.0)
    if idx_toc is None and cum + d >= climb_nm: idx_toc = i
    cum += d
cum_back = 0.0; idx_tod=None
for j in range(len(legs)-1, -1, -1):
    d=float(legs[j].get("Dist") or 0.0)
    if idx_tod is None and cum_back + d >= desc_nm: idx_tod = j
    cum_back += d

# Construir sequência final (inclui Alternate no fim, dist=0)
final_legs=[]
for i, r in enumerate(legs):
    final_legs.append({**r})
    if idx_toc is not None and i==idx_toc:
        final_legs.append({**r, "Name":"TOC", "Alt/FL":f"FL{int(round(cruise_alt_ft/100))}", "Dist":0.0})
    if idx_tod is not None and i==idx_tod:
        final_legs.append({**r, "Name":"TOD", "Alt/FL":"", "Dist":0.0})
# Alternate leg extra (informativo)
final_legs.append({"Name": f"{altn or 'ALT'}", "Alt/FL": f"{alt_elev_ft} ft", "Freq": aero_freqs(altn), "TC":0.0, "TAS":0.0, "Dist":0.0})

# ETO/ETA: Takeoff = Startup + 15; Landing = ETA; Shutdown = ETA + 5
startup_t = parse_hhmm(startup_str)
takeoff_t = add_minutes_to_time(startup_t, 15) if startup_t else None
clock = takeoff_t

start_fuel = st.number_input("Fuel inicial (EFOB_START) [L]", 0.0, 999.0, 0.0, 1.0)
total_dist = total_ete = total_burn = 0.0
efob = start_fuel

calc_rows=[]
for r in final_legs:
    name=r.get("Name",""); altfl=r.get("Alt/FL",""); freq=r.get("Freq","")
    tc=float(r.get("TC") or 0.0); dist=float(r.get("Dist") or 0.0)

    # TAS e FF para legs “de cruzeiro” vindos da tabela (se o user não puser TAS)
    tas_user = float(r.get("TAS") or 0.0)
    tas_leg, ff_leg = cruise_lookup(cruise_alt_ft, rpm_cruise, cruise_oat)
    tas = tas_user if tas_user>0 else tas_leg

    wca, th, gs = wind_triangle(tc, tas, wind_from, wind_kt)
    mh = apply_var(th, var_deg, var_is_e)

    # ETE/Burn para leg normal
    if name not in ("TOC","TOD"):
        ete_min = (60.0*dist/max(gs,1e-6)) if dist>0 else 0.0
        ff_used = ff_leg
    else:
        # TOC / TOD usam os tempos/fuel das fases calculadas
        if name=="TOC":
            ete_min = climb_min
            ff_used = ff_climb
        else:
            ete_min = desc_min
            ff_used = ff_desc

    burn = ff_used * (ete_min/60.0)
    total_dist += dist
    total_ete  += ete_min
    total_burn += burn
    efob = max(0.0, efob - burn)

    eto_str=""
    if clock:
        clock = add_minutes_to_time(clock, int(round(ete_min)))
        eto_str = clock.strftime("%H:%M")

    calc_rows.append({
        "Name":name, "Alt/FL":altfl, "Freq":freq,
        "TC":f"{tc:.1f}", "TAS":f"{tas:.0f}", "Dist":f"{dist:.1f}",
        "WCA":f"{wca:+.1f}", "TH":f"{th:06.2f}", "MH":f"{mh:06.2f}",
        "GS":f"{gs:.0f}", "ETE(min)":f"{ete_min:.0f}", "ETO":eto_str,
        "PL_B/O (L)":f"{burn:.1f}", "EFOB (L)":f"{efob:.1f}"
    })

eta_calc = add_minutes_to_time(takeoff_t, int(round(total_ete))) if takeoff_t else None
landing_str = eta_calc.strftime("%H:%M") if eta_calc else ""
shutdown_str = add_minutes_to_time(eta_calc, 5).strftime("%H:%M") if eta_calc else ""

# Mostra apenas os totais (não duplicamos tabelas)
tot_line = f"**Totais** — Dist {total_dist:.1f} nm • ETE {int(total_ete)//60}h{int(total_ete)%60:02d} • Burn {total_burn:.1f} L • EFOB {efob:.1f} L"
if eta_calc: tot_line += f" • **ETA {landing_str}** • **Shutdown {shutdown_str}**"
st.markdown(tot_line)

# ========================= PDF export =========================
st.subheader("PDF export")
safe_reg = ascii_safe(registration)
safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
filename = f"{safe_date}_{safe_reg}_NAVLOG.pdf"

try:
    template = read_pdf_bytes(PDF_TEMPLATE_PATHS)
    fieldset, maxlens = get_fields_and_meta(template)
    named = {}

    # Header
    put_any(named, fieldset, ["Aircraft","Aircraf","Aircraft_Type"], aircraft, maxlens)
    put_any(named, fieldset, ["Registration","REG"], registration, maxlens)
    put_any(named, fieldset, ["Callsign","CALLSIGN"], callsign, maxlens)
    put_any(named, fieldset, ["Student","STUDENT"], student, maxlens)
    put_any(named, fieldset, ["Dept_Airfield","Departure","Dept"], dept, maxlens)
    put_any(named, fieldset, ["Arrival_Airfield","Arrival"], arr, maxlens)
    put_any(named, fieldset, ["Alternate","Alternate_Airfield"], altn, maxlens)

    # Altitudes (se o PDF tiver)
    put_any(named, fieldset, ["Alt_Dep","Dept_Elev","Departure_Elev"], dep_elev_ft, maxlens)
    put_any(named, fieldset, ["Alt_Arr","Arrival_Elev"], arr_elev_ft, maxlens)
    put_any(named, fieldset, ["Alt_Alt","Alternate_Elev"], alt_elev_ft, maxlens)

    # Horas
    takeoff_str = takeoff_t.strftime("%H:%M") if takeoff_t else ""
    put_any(named, fieldset, "Startup", startup_str, maxlens)
    put_any(named, fieldset, "Takeoff", takeoff_str, maxlens)
    put_any(named, fieldset, "Landing", landing_str, maxlens)
    put_any(named, fieldset, "Shutdown", shutdown_str, maxlens)
    put_any(named, fieldset, ["ETD/ETA","ETD_ETA"], f"{takeoff_str} / {landing_str}", maxlens)

    put_any(named, fieldset, ["Dept_Comm","Departure_Comms"], dept_comm, maxlens)
    put_any(named, fieldset, ["Enroute_comm","Enroute_Comms"], enroute_comm, maxlens)
    put_any(named, fieldset, ["Arrival_comm","Arrival_Comms"], arrival_comm, maxlens)

    # Legs 1..11
    for i, r in enumerate(calc_rows[:11], start=1):
        s=str(i)
        put_any(named, fieldset, [f"Name{s}","Name_{s}"], r["Name"], maxlens)
        put_any(named, fieldset, [f"Alt{s}","Alt_{s}"],  r["Alt/FL"], maxlens)
        put_any(named, fieldset, [f"FREQ{s}","FREQ_{s}"], r.get("Freq",""), maxlens)
        put_any(named, fieldset, [f"TCRS{s}","TCRS_{s}"], r["TC"], maxlens)
        put_any(named, fieldset, [f"THDG{s}","THDG_{s}"], r["TH"], maxlens)
        put_any(named, fieldset, [f"MHDG{s}","MHDG_{s}"], r["MH"], maxlens)
        put_any(named, fieldset, [f"GS{s}","GS_{s}"],     r["GS"], maxlens)
        put_any(named, fieldset, [f"Dist{s}","Dist_{s}"], r["Dist"], maxlens)
        put_any(named, fieldset, [f"ETE{s}","ETE_{s}"],   r["ETE(min)"], maxlens)
        put_any(named, fieldset, [f"ETO{s}","ETO_{s}"],   r["ETO"], maxlens)
        put_any(named, fieldset, [f"PL_BO{s}","PL_BO_{s}"], r["PL_B/O (L)"], maxlens)
        put_any(named, fieldset, [f"EFOB{s}","EFOB_{s}"], r["EFOB (L)"], maxlens)

    if st.button("Gerar PDF preenchido", type="primary"):
        try:
            out = fill_pdf(template, named)
            st.download_button("Download PDF", data=out, file_name=filename, mime="application/pdf")
            st.success("PDF gerado. Revê antes do voo.")
        except Exception as e:
            st.error(f"Erro ao gerar PDF: {e}")

except Exception as e:
    st.error(f"Falha a preparar PDF: {e}")




