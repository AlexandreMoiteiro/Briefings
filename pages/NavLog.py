

# app.py — NAVLOG com Waypoints+Segmentos (TOC/TOD corretos) + AFM perf + export p/ "NAVLOG - FORM.pdf"
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
    except:
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
    except:
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
        writer._root_object["/AcroForm"].update({
            NameObject("/NeedAppearances"): True,
            NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g")
        })
    except:
        pass
    str_fields = {k: (str(v) if v is not None else "") for k, v in fields.items()}
    for page in writer.pages:
        writer.update_page_form_field_values(page, str_fields)
    bio = io.BytesIO(); writer.write(bio); return bio.getvalue()

def put(out: dict, fieldset: set, key: str, value: str, maxlens: Dict[str, int]):
    if key in fieldset:
        s = "" if value is None else str(value)
        if key in maxlens and len(s) > maxlens[key]:
            s = s[:maxlens[key]]
        out[key] = s

# ========================= Wind & helpers =========================
def wrap360(x): x=fmod(x,360.0); return x+360 if x<0 else x
def angle_diff(a, b): return (a - b + 180) % 360 - 180

def wind_triangle(tc_deg, tas_kt, wind_from_deg, wind_kt):
    # Retorna (WCA, TH, GS). Fórmula robusta: usa relações trig p/ evitar cos() negativo mal condicionado.
    if tas_kt <= 0: return 0.0, wrap360(tc_deg), 0.0
    beta = radians(angle_diff(wind_from_deg, tc_deg))
    cross = wind_kt * sin(beta)                 # componente transversal do vento
    s = max(-1.0, min(1.0, cross / max(tas_kt, 1e-9)))
    wca = degrees(asin(s))
    # componente de frente/cauda = W * cos(beta) = W * sqrt(1 - sin^2(beta))
    head = (wind_kt ** 2 - cross ** 2) ** 0.5 if wind_kt >= abs(cross) else 0.0
    gs = max(0.0, tas_kt * (1 - s**2) ** 0.5 - head)
    th = wrap360(tc_deg + wca)
    return wca, th, gs

def apply_var(true_deg, var_deg, east_is_negative=False):
    # East is least (−), West is best (+)
    return wrap360(true_deg - var_deg if east_is_negative else true_deg + var_deg)

def parse_hhmm(s: str):
    s = (s or "").strip()
    for fmt in ("%H:%M", "%H%M"):
        try:
            return dt.datetime.strptime(s, fmt).time()
        except:
            pass
    return None

def add_minutes(t: dt.time, m: int):
    if not t: return None
    today = dt.date.today(); base = dt.datetime.combine(today, t)
    return (base + dt.timedelta(minutes=m)).time()

# ========================= AFM tables (excerto) =========================
def clamp(v, lo, hi): return max(lo, min(hi, v))
def interp1(x, x0, x1, y0, y1):
    if x1 == x0: return y0
    t = (x - x0) / (x1 - x0); return y0 + t * (y1 - y0)

# En-route ROC (flaps UP) @ 650 kg
ROC = {
    650: {
        0: {-25:951, 0:805, 25:675, 50:557},
        2000: {-25:840, 0:696, 25:568, 50:453},
        4000: {-25:729, 0:588, 25:462, 50:349},
        6000: {-25:619, 0:480, 25:357, 50:245},
        8000: {-25:509, 0:373, 25:251, 50:142},
        10000: {-25:399, 0:266, 25:146, 50:39},
        12000: {-25:290, 0:159, 25:42, 50:-64},
        14000: {-25:181, 0:53, 25:-63, 50:-166}
    },
}
VY = {650: {0:70, 2000:69, 4000:68, 6000:67, 8000:65, 10000:64, 12000:63, 14000:62}}

# Cruise perf (PA → rpm → (TAS kt, FF L/h)) — usaremos só o FF; a TAS de cruzeiro será fixa = 80 kt
CRUISE = {
    0: {2000:(95,18.7),2100:(101,20.7),2250:(110,24.6)},
    2000: {2000:(94,17.5),2100:(100,19.9),2250:(109,23.5)},
    4000: {2000:(94,17.5),2100:(100,19.2),2250:(108,22.4)},
    6000: {2000:(93,17.1),2100:(99,18.5),2250:(108,21.3)},
    8000: {2000:(92,16.7),2100:(98,18.0),2250:(107,20.4)},
    10000:{2000:(91,16.4),2100:(97,17.5),2250:(106,19.7)},
}
def isa_temp(pa_ft): return 15.0 - 2.0 * (pa_ft / 1000.0)
def cruise_lookup_ff(pa_ft: float, rpm: int, oat_c: Optional[float]) -> float:
    # devolve apenas FF (L/h); TAS será fixada a 80 kt
    pas = sorted(CRUISE.keys()); pa_c = clamp(pa_ft, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    def ff_at(pa):
        table = CRUISE[pa]
        if rpm in table: return table[rpm][1]
        rpms = sorted(table.keys())
        if rpm < rpms[0]: lo, hi = rpms[0], rpms[1]
        elif rpm > rpms[-1]: lo, hi = rpms[-2], rpms[-1]
        else:
            lo = max([r for r in rpms if r <= rpm]); hi = min([r for r in rpms if r >= rpm])
        ff_lo = table[lo][1]; ff_hi = table[hi][1]
        t = (rpm - lo) / (hi - lo) if hi != lo else 0.0
        return ff_lo + t * (ff_hi - ff_lo)
    ff0 = ff_at(p0); ff1 = ff_at(p1)
    ff = interp1(pa_c, p0, p1, ff0, ff1)
    # Correção simples por OAT vs ISA (±15°C → FF −2.5/+3%)
    if oat_c is not None:
        dev = oat_c - isa_temp(pa_c)
        if dev > 0:
            ff *= 1.0 - 0.025 * (dev / 15.0)
        elif dev < 0:
            ff *= 1.0 + 0.03 * ((-dev) / 15.0)
    return max(0.0, ff)

def roc_interp(pa, temp_c):
    tab = ROC[650]
    pas = sorted(tab.keys()); pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    temps = [-25, 0, 25, 50]; t = clamp(temp_c, temps[0], temps[-1])
    if t <= 0: t0, t1 = -25, 0
    elif t <= 25: t0, t1 = 0, 25
    else: t0, t1 = 25, 50
    v00, v01 = tab[p0][t0], tab[p0][t1]
    v10, v11 = tab[p1][t0], tab[p1][t1]
    v0 = interp1(t, t0, t1, v00, v01); v1 = interp1(t, t0, t1, v10, v11)
    return max(1.0, interp1(pa_c, p0, p1, v0, v1))

def vy_interp(pa):
    table = VY[650]; pas = sorted(table.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    return interp1(pa_c, p0, p1, table[p0], table[p1])

# ========================= Aerodromes (freqs numéricas) =========================
AEROS = {
    "LPSO":{"elev":390,"freq":"119.805"},
    "LPEV":{"elev":807,"freq":"122.705"},
    "LPCB":{"elev":1251,"freq":"122.300"},
    "LPCO":{"elev":587,"freq":"118.405"},
    "LPVZ":{"elev":2060,"freq":"118.305"},
}
def aero_elev(icao): return int(AEROS.get(icao, {}).get("elev", 0))
def aero_freq(icao): return AEROS.get(icao, {}).get("freq", "")

# ========================= App UI =========================
st.set_page_config(page_title="NAVLOG", layout="wide", initial_sidebar_state="collapsed")
st.title("Navigation Plan & Inflight Log — Tecnam P2008")

DEFAULT_STUDENT = "AMOIT"; DEFAULT_AIRCRAFT = "P208"; DEFAULT_CALLSIGN = "RVP"
REGS = ["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"]
PDF_TEMPLATE_PATHS = ["NAVLOG - FORM.pdf"]  # PDF na raiz do repo

# Header
c1, c2, c3 = st.columns(3)
with c1:
    aircraft = st.text_input("Aircraft", DEFAULT_AIRCRAFT)
    registration = st.selectbox("Registration", REGS, index=0)
    callsign = st.text_input("Callsign", DEFAULT_CALLSIGN)
with c2:
    student = st.text_input("Student", DEFAULT_STUDENT)
    lesson = st.text_input("Lesson", "")
    instructor = st.text_input("Instructor", "")
with c3:
    dept = st.selectbox("Departure", list(AEROS.keys()), index=0)
    arr  = st.selectbox("Arrival",  list(AEROS.keys()), index=1)
    altn = st.selectbox("Alternate",list(AEROS.keys()), index=2)

startup_str = st.text_input("Startup (HH:MM)", "")

# Atmosfera & navegação (globais)
c4, c5, c6 = st.columns(3)
with c4:
    qnh = st.number_input("QNH (hPa)", 900, 1050, 1013, step=1)
    cruise_alt = st.number_input("Cruise Altitude (ft)", 0, 14000, 3000, step=100)
with c5:
    temp_c = st.number_input("OAT (°C)", -40, 50, 15, step=1)
    var_deg = st.number_input("Mag Variation (°)", 0, 30, 1, step=1)
    var_is_e = (st.selectbox("E/W", ["W", "E"], index=0) == "E")
with c6:
    wind_from = st.number_input("Wind FROM (°TRUE)", 0, 360, 0, step=1)
    wind_kt = st.number_input("Wind (kt)", 0, 120, 0, step=1)
    enroute_comm = st.text_input("Enroute frequency", "123.755")

# Performance set
c7, c8, c9 = st.columns(3)
with c7:
    rpm_cruise = st.number_input("Cruise RPM (para FF AFM)", 1800, 2388, 2000, step=10)
with c8:
    rpm_descent = st.number_input("Descent RPM (usado se não IDLE)", 1700, 2300, 1800, step=10)
    idle_mode = st.checkbox("Descent mostly IDLE", value=True)
with c9:
    rod_fpm = st.number_input("ROD (ft/min)", 200, 1500, 700, step=10)
    idle_ff = st.number_input("Idle FF (L/h) (if IDLE)", 0.0, 20.0, 5.0, step=0.1)
    start_fuel = st.number_input("Fuel inicial (EFOB_START) [L]", 0.0, 1000.0, 0.0, step=1.0)

# ===== Waypoints & Segmentos =====
# Nº de waypoints intermédios (entre Departure e Arrival)
K = st.number_input("Nº de waypoints intermédios", 0, 10, 3)

def blank_wp(): return {"Name":"", "Alt/FL":"", "Freq":""}
if "wps" not in st.session_state:
    st.session_state.wps = [blank_wp() for _ in range(K)]

# redimensionar lista de WPs intermédios mantendo conteúdo
wps = st.session_state.wps
if len(wps) < K: wps += [blank_wp() for _ in range(K - len(wps))]
elif len(wps) > K: wps = wps[:K]
st.session_state.wps = wps

# construir lista total de waypoints: [DEP] + intermédios + [ARR]
def build_wp_list():
    total = []
    total.append({"Name":dept, "Alt/FL":str(aero_elev(dept)), "Freq":aero_freq(dept)})
    total += st.session_state.wps
    total.append({"Name":arr, "Alt/FL":str(aero_elev(arr)), "Freq":aero_freq(arr)})
    return total

st.markdown("#### Waypoints (intermédios)")
wp_editor_cols = {
    "Name":   st.column_config.TextColumn("Name / Lat,Long"),
    "Alt/FL": st.column_config.TextColumn("Alt/FL (num)"),
    "Freq":   st.column_config.TextColumn("Freq"),
}
st.session_state.wps = st.data_editor(st.session_state.wps, hide_index=True, use_container_width=True,
                                      num_rows="fixed", column_config=wp_editor_cols, key="wp_editor")

WPS_ALL = build_wp_list()
M = len(WPS_ALL)                 # nº total de wps (inc. dep e arr)
SEG_N = max(0, M - 1)            # nº de segmentos (legs operacionais) = M-1

# Tabela de segmentos (From → To) com TC e Dist editáveis
def blank_seg(_from, _to):
    return {"From":_from, "To":_to, "TC":0.0, "Dist":0.0}

if "segs" not in st.session_state:
    st.session_state.segs = [blank_seg(WPS_ALL[i]["Name"], WPS_ALL[i+1]["Name"]) for i in range(SEG_N)]

# reaplicar From/To com base nos WPs atuais e ajustar nº de linhas
def rebuild_segs():
    new = []
    for i in range(max(0, len(WPS_ALL) - 1)):
        _from = WPS_ALL[i]["Name"]; _to = WPS_ALL[i+1]["Name"]
        # tentar reutilizar valores anteriores se existir linha equivalente pela posição
        if i < len(st.session_state.segs):
            prev = st.session_state.segs[i]
            new.append({"From":_from, "To":_to, "TC":prev.get("TC",0.0), "Dist":prev.get("Dist",0.0)})
        else:
            new.append({"From":_from, "To":_to, "TC":0.0, "Dist":0.0})
    st.session_state.segs = new

rebuild_segs()

st.markdown("#### Segmentos (From → To)")
seg_editor_cols = {
    "From": st.column_config.TextColumn("From", disabled=True),
    "To":   st.column_config.TextColumn("To",   disabled=True),
    "TC":   st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
    "Dist": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
}
st.session_state.segs = st.data_editor(st.session_state.segs, hide_index=True, use_container_width=True,
                                       num_rows="fixed", column_config=seg_editor_cols, key="seg_editor")

SEGS = st.session_state.segs  # lista de segmentos editada

# ===== Cálculos (TOC/TOD por segmentos) =====
def pressure_alt(elev_ft, qnh_hpa): return float(elev_ft) + (1013.0 - float(qnh_hpa)) * 30.0

# TAS fixas pedidas
TAS_CRUISE = 80.0
TAS_DESCENT = 65.0

def compute_climb(dep_elev, cruise_alt, qnh, oat_c, tc_first):
    pa_dep = pressure_alt(dep_elev, qnh)
    roc = roc_interp(pa_dep, oat_c)     # ft/min
    vy  = vy_interp(pa_dep)             # TAS de subida (kt)
    _,_, gs_climb = wind_triangle(float(tc_first), float(vy), wind_from, wind_kt)
    delta = max(0.0, cruise_alt - dep_elev)
    t_min = delta / max(roc, 1e-6)
    d_nm  = gs_climb * (t_min/60.0)
    # consumo na subida: usar FF a meio (aprox conservadora com 2250rpm) — podes ajustar
    pa_mid = dep_elev + 0.5*delta
    ff = cruise_lookup_ff(pa_mid, 2250, oat_c)
    return t_min, d_nm, ff, vy, gs_climb

def compute_descent(arr_elev, cruise_alt, qnh, oat_c, tc_last, rod_fpm, rpm_desc, idle_mode, idle_ff):
    delta = max(0.0, cruise_alt - arr_elev)
    t_min = delta / max(rod_fpm,1e-6)
    # GS de descida com TAS=65
    _,_, gs_des = wind_triangle(float(tc_last), float(TAS_DESCENT), wind_from, wind_kt)
    d_nm = gs_des * (t_min/60.0)
    # consumo
    if idle_mode:
        ff = float(idle_ff)
    else:
        pa_mid = arr_elev + 0.5*delta
        ff = cruise_lookup_ff(pa_mid, int(rpm_desc), oat_c)
    return t_min, d_nm, ff, TAS_DESCENT, gs_des

dep_elev = aero_elev(dept); arr_elev = aero_elev(arr)
tc_first = float(SEGS[0]["TC"]) if SEGS else 0.0
tc_last  = float(SEGS[-1]["TC"]) if SEGS else 0.0

climb_min, climb_nm, climb_ff, tas_climb, gs_climb = compute_climb(dep_elev, cruise_alt, qnh, temp_c, tc_first)
desc_min,  desc_nm,  desc_ff, tas_des,   gs_des   = compute_descent(arr_elev, cruise_alt, qnh, temp_c, tc_last, rod_fpm, rpm_descent, idle_mode, idle_ff)

# consumo de climb distribuído pelos segmentos (do início)
climb_consumed = [0.0]*len(SEGS)
rem = float(climb_nm)
for i, seg in enumerate(SEGS):
    d = float(seg.get("Dist") or 0.0)
    use = min(d, max(0.0, rem))
    climb_consumed[i] = use
    rem -= use

# índice de TOC = primeiro segmento onde se esgota climb_nm (ou o último que ainda consome)
idx_toc = None; acc = 0.0
for i, use in enumerate(climb_consumed):
    acc += use
    if use > 0.0 and acc >= climb_nm - 1e-6:
        idx_toc = i
        break

# consumo de descent distribuído pelos segmentos (a partir do fim)
desc_consumed = [0.0]*len(SEGS)
rem = float(desc_nm)
for j in range(len(SEGS)-1, -1, -1):
    d = float(SEGS[j].get("Dist") or 0.0)
    use = min(d, max(0.0, rem))
    desc_consumed[j] = use
    rem -= use

# índice de TOD = primeiro segmento a partir do fim onde se esgota desc_nm
idx_tod = None; acc = 0.0
for j in range(len(SEGS)-1, -1, -1):
    acc += desc_consumed[j]
    if desc_consumed[j] > 0.0 and acc >= desc_nm - 1e-6:
        idx_tod = j
        break

# ===== construir linhas finais (inclui TOC/TOD) =====
startup = parse_hhmm(startup_str)
takeoff = add_minutes(startup, 15) if startup else None
clock = takeoff

# Dist total real = soma de Dist dos segmentos
total_dist = sum(float(seg.get("Dist") or 0.0) for seg in SEGS)
total_ete = total_burn = 0.0
efob = float(start_fuel)

calc_rows = []

# LINHA: segmento i mapeado para o waypoint "To" i+1
for i, seg in enumerate(SEGS):
    from_wp = WPS_ALL[i]
    to_wp   = WPS_ALL[i+1]
    name = to_wp["Name"]; alt_txt = to_wp["Alt/FL"]; freq = to_wp["Freq"]
    tc = float(seg.get("TC") or 0.0); dist = float(seg.get("Dist") or 0.0)

    # inserir TOC antes de calcular o ETE do que sobra do segmento (se TOC cai neste segmento)
    if idx_toc is not None and i == idx_toc:
        # TOC row
        _, th_toc, gs_toc = wind_triangle(tc, tas_climb, wind_from, wind_kt)
        mh_toc = apply_var(th_toc, var_deg, var_is_e)
        burn_toc = climb_ff * (climb_min/60.0)
        total_ete += climb_min; total_burn += burn_toc; efob = max(0.0, efob - burn_toc)
        eto_str = ""
        if clock:
            clock = add_minutes(clock, int(round(climb_min)))
            eto_str = clock.strftime("%H:%M")
        calc_rows.append({
            "Name":"TOC","Alt/FL":str(int(round(cruise_alt))),"Freq":"",
            "TC":round(tc,0),"TH":round(th_toc,0),"MH":round(mh_toc,0),
            "TAS":round(tas_climb,0),"GS":round(gs_toc,0),
            "Dist":0.0,"ETE":round(climb_min,0),"ETO":eto_str,
            "Burn":round(burn_toc,1),"EFOB":round(efob,1)
        })

    # inserir TOD se cair neste segmento (NOTA: TOD costuma cair perto do final)
    if idx_tod is not None and i == idx_tod:
        tas_tod = tas_des
        _, th_tod, gs_tod = wind_triangle(tc, tas_tod, wind_from, wind_kt)
        mh_tod = apply_var(th_tod, var_deg, var_is_e)
        burn_tod = desc_ff * (desc_min/60.0)
        total_ete += desc_min; total_burn += burn_tod; efob = max(0.0, efob - burn_tod)
        eto_str = ""
        if clock:
            clock = add_minutes(clock, int(round(desc_min)))
            eto_str = clock.strftime("%H:%M")
        calc_rows.append({
            "Name":"TOD","Alt/FL":str(aero_elev(arr)), "Freq":"",
            "TC":round(tc,0),"TH":round(th_tod,0),"MH":round(mh_tod,0),
            "TAS":round(tas_tod,0),"GS":round(gs_tod,0),
            "Dist":0.0,"ETE":round(desc_min,0),"ETO":eto_str,
            "Burn":round(burn_tod,1),"EFOB":round(efob,1)
        })

    # parte efetiva do segmento (removendo climb/desc consumidos)
    used_climb = climb_consumed[i] if i < len(climb_consumed) else 0.0
    used_desc  = desc_consumed[i] if i < len(desc_consumed)  else 0.0
    eff_dist = max(0.0, dist - used_climb - used_desc)

    # Cruise TAS fixa 80 kt; FF de AFM
    tas_seg = TAS_CRUISE
    _, th, gs = wind_triangle(tc, tas_seg, wind_from, wind_kt)
    mh = apply_var(th, var_deg, var_is_e)

    ete_min = (60.0*eff_dist/max(gs,1e-6)) if eff_dist > 0 else 0.0
    pa_cruise = pressure_alt(cruise_alt, qnh)
    ff_cruise = cruise_lookup_ff(pa_cruise, int(rpm_cruise), temp_c)
    burn = ff_cruise * (ete_min/60.0)

    total_ete += ete_min; total_burn += burn; efob = max(0.0, efob - burn)

    eto_str = ""
    if clock:
        clock = add_minutes(clock, int(round(ete_min)))
        eto_str = clock.strftime("%H:%M")

    calc_rows.append({
        "Name":name,"Alt/FL":alt_txt,"Freq":freq,
        "TC":round(tc,0),"TH":round(th,0),"MH":round(mh,0),
        "TAS":round(tas_seg,0),"GS":round(gs,0),
        "Dist":round(dist,1),     # mostra a Dist original do segmento
        "ETE":round(ete_min,0),"ETO":eto_str,
        "Burn":round(burn,1),"EFOB":round(efob,1)
    })

# ETA/Landing/Shutdown
eta = clock
landing = eta
shutdown = add_minutes(eta, 5) if eta else None

# ===== Tabela final (read-only) =====
st.markdown("#### Flight plan (com TOC/TOD e segmentos)")
column_config={
    "Name":   st.column_config.TextColumn("Destino / Marker"),
    "Alt/FL": st.column_config.TextColumn("Alt/FL"),
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
               num_rows="fixed", column_config=column_config, key="final_table")

# Totais
tot_line = f"**Totais** — Dist {total_dist:.1f} nm • ETE {int(total_ete)//60}h{int(total_ete)%60:02d} • Burn {total_burn:.1f} L • EFOB {efob:.1f} L"
if eta:
    tot_line += f" • **ETA {eta.strftime('%H:%M')}** • **Landing {landing.strftime('%H:%M')}** • **Shutdown {shutdown.strftime('%H:%M')}**"
st.markdown(tot_line)

# ====== PDF export ======
st.markdown("### PDF export")

template_bytes = None
try:
    template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
except Exception as e:
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

        # Condições
        pa_dep = pressure_alt(aero_elev(dept), qnh)
        isa_dev = round(temp_c - isa_temp(pa_dep))
        put(named, fieldset, "QNH", f"{int(round(qnh))}", maxlens)
        put(named, fieldset, "temp_isa_dev", f"{int(round(temp_c))} / {isa_dev}", maxlens)
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

        # Leg number = nº de segmentos
        put(named, fieldset, "Leg_Number", str(SEG_N), maxlens)

        # Mapear linhas: primeiro exportamos TOC, depois o que houver (até 11 linhas no PDF)
        export_rows = calc_rows[:11]

        for i, r in enumerate(export_rows, start=1):
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
