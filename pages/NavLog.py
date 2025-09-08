# app.py — NAVLOG completo (TOC/TOD corretos + AFM perf + export p/ "NAVLOG - FORM.pdf")
# Reqs: streamlit, pypdf, pytz

import streamlit as st
import datetime as dt
import pytz
import io, unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from math import sin, cos, asin, radians, degrees, fmod

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
    """
    Usa o método 'wind-from':
      beta = ângulo entre vento (from) e TC
      WCA = asin(W*sin(beta) / TAS)
      GS  = TAS*cos(WCA) - W*cos(beta)
      TH  = TC + WCA
    """
    if tas_kt <= 0: return 0.0, wrap360(tc_deg), 0.0
    beta = radians(angle_diff(wind_from_deg, tc_deg))
    cross = wind_kt * sin(beta)
    head  = wind_kt * cos(beta)
    s = max(-1.0, min(1.0, cross / max(tas_kt, 1e-9)))
    wca = degrees(asin(s))
    gs  = tas_kt * cos(radians(wca)) - head
    th  = wrap360(tc_deg + wca)
    return wca, th, max(0.0, gs)

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

# Cruise perf (PA → rpm → (TAS kt, FF L/h))
CRUISE = {
    0: {2000:(95,18.7),2100:(101,20.7),2250:(110,24.6)},
    2000: {2000:(94,17.5),2100:(100,19.9),2250:(109,23.5)},
    4000: {2000:(94,17.5),2100:(100,19.2),2250:(108,22.4)},
    6000: {2000:(93,17.1),2100:(99,18.5),2250:(108,21.3)},
    8000: {2000:(92,16.7),2100:(98,18.0),2250:(107,20.4)},
    10000:{2000:(91,16.4),2100:(97,17.5),2250:(106,19.7)},
}
def isa_temp(pa_ft): return 15.0 - 2.0 * (pa_ft / 1000.0)
def cruise_lookup(pa_ft: float, rpm: int, oat_c: Optional[float]) -> Tuple[float, float]:
    pas = sorted(CRUISE.keys()); pa_c = clamp(pa_ft, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    def val(pa):
        table = CRUISE[pa]
        if rpm in table: return table[rpm]
        rpms = sorted(table.keys())
        if rpm < rpms[0]: lo, hi = rpms[0], rpms[1]
        elif rpm > rpms[-1]: lo, hi = rpms[-2], rpms[-1]
        else:
            lo = max([r for r in rpms if r <= rpm]); hi = min([r for r in rpms if r >= rpm])
        (tas_lo, ff_lo), (tas_hi, ff_hi) = table[lo], table[hi]
        t = (rpm - lo) / (hi - lo) if hi != lo else 0.0
        return (tas_lo + t*(tas_hi - tas_lo), ff_lo + t*(ff_hi - ff_lo))
    tas0, ff0 = val(p0); tas1, ff1 = val(p1)
    tas = interp1(pa_c, p0, p1, tas0, tas1); ff = interp1(pa_c, p0, p1, ff0, ff1)
    # NOTA: Vamos ignorar o TAS daqui (usamos speed_ref), mas usamos o FF.
    if oat_c is not None:
        dev = oat_c - isa_temp(pa_c)
        if dev > 0:
            ff  *= 1.0 - 0.025 * (dev / 15.0)
        elif dev < 0:
            ff  *= 1.0 + 0.03 * ((-dev) / 15.0)
    return max(0.0, tas), max(0.0, ff)

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
    speed_ref = st.number_input("Cruise reference speed (kt)", 50, 140, 80, step=1)  # 80 kt por defeito
with c8:
    rpm_descent = st.number_input("Descent RPM (se NÃO idle)", 1700, 2300, 1800, step=10)
    idle_mode = st.checkbox("Descent mostly IDLE", value=True)
with c9:
    rod_fpm = st.number_input("ROD (ft/min) (descent)", 200, 1500, 700, step=10)
    idle_ff = st.number_input("Idle FF (L/h)", 0.0, 20.0, 5.0, step=0.1)
    start_fuel = st.number_input("Fuel inicial (EFOB_START) [L]", 0.0, 1000.0, 0.0, step=1.0)

# ===== Legs (segmentos) =====
N = st.number_input("Nº de legs (sem contar TOC/TOD)", 1, 11, 4)

def blank_leg():
    return {"To":"", "Freq":"", "TC":0.0, "Dist":0.0}

if "route_legs" not in st.session_state:
    st.session_state.route_legs = [blank_leg() for _ in range(N)]

# Ajustar nº de legs preservando última linha (vai forçosamente terminar em Arrival)
def resize_legs(target_n: int):
    legs = st.session_state.route_legs
    if len(legs) < target_n:
        legs += [blank_leg() for _ in range(target_n - len(legs))]
    elif len(legs) > target_n:
        legs = legs[:target_n]
    st.session_state.route_legs = legs

resize_legs(N)
legs = st.session_state.route_legs

# Construir tabela editável de legs: From calculado, To editável (mas ultima To = Arrival)
edit_rows = []
from_name = dept
for i in range(N):
    row = legs[i].copy()
    # força última To = Arrival
    if i == N - 1:
        row["To"] = arr
    # Autofreq se for um icao conhecido e freq em branco
    if not row.get("Freq") and row.get("To") in AEROS:
        row["Freq"] = aero_freq(row["To"])
    edit_rows.append({
        "From": from_name,
        "To": row.get("To",""),
        "Freq": row.get("Freq",""),
        "TC": float(row.get("TC") or 0.0),
        "Dist": float(row.get("Dist") or 0.0),
    })
    from_name = row.get("To","")

st.markdown("#### Route legs (edita **To**, **TC**, **Dist**, **Freq**)")
colcfg = {
    "From": st.column_config.TextColumn("From", disabled=True),
    "To":   st.column_config.TextColumn("To (WP/ICAO)"),
    "Freq": st.column_config.TextColumn("Freq"),
    "TC":   st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
    "Dist": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
}
edited_legs = st.data_editor(
    edit_rows, hide_index=True, use_container_width=True,
    num_rows="fixed", column_config=colcfg, key="legs_editor"
)

# Persistir alterações no estado (respeitando Arrival na última linha)
new_from = dept
for i, row in enumerate(edited_legs):
    to_val = row.get("To","")
    if i == N - 1:
        to_val = arr  # força Arrival
    st.session_state.route_legs[i] = {
        "To": to_val,
        "Freq": row.get("Freq",""),
        "TC": float(row.get("TC") or 0.0),
        "Dist": float(row.get("Dist") or 0.0),
    }
    new_from = to_val

# ========= Cálculos de performance / TOC & TOD =========
def pressure_alt(elev_ft, qnh_hpa): return float(elev_ft) + (1013.0 - float(qnh_hpa))*30.0

def compute_climb(dep_elev, cruise_alt, qnh, oat_c, tc_first):
    pa_dep = pressure_alt(dep_elev, qnh)
    roc = roc_interp(pa_dep, oat_c)     # ft/min
    vy  = vy_interp(pa_dep)             # kt ~ IAS ~ TAS para GS
    _,_, gs_climb = wind_triangle(float(tc_first), float(vy), wind_from, wind_kt)
    delta = max(0.0, cruise_alt - dep_elev)
    t_min = delta / max(roc,1e-6)
    d_nm  = gs_climb * (t_min/60.0)
    pa_mid = dep_elev + 0.5*delta
    _, ff = cruise_lookup(pa_mid, 2250, oat_c)  # FF em subida (aprox) @2250
    return t_min, d_nm, ff, vy, gs_climb

def compute_descent(arr_elev, cruise_alt, qnh, oat_c, tc_descent, rod_fpm, rpm_desc, idle_mode, idle_ff):
    delta = max(0.0, cruise_alt - arr_elev)
    t_min = delta / max(rod_fpm,1e-6)
    tas_desc = 65.0  # pedido: 65 kt na descida
    _,_, gs_des = wind_triangle(float(tc_descent), tas_desc, wind_from, wind_kt)
    d_nm = gs_des * (t_min/60.0)
    # consumo
    if idle_mode:
        ff = float(idle_ff)
    else:
        pa_mid = arr_elev + 0.5*delta
        _, ff = cruise_lookup(pa_mid, int(rpm_desc), oat_c)
    return t_min, d_nm, ff, tas_desc, gs_des

# TC do 1º e último leg (para vento na climb/desc)
tc_first = float(st.session_state.route_legs[0]["TC"] if N >= 1 else 0.0)
tc_last  = float(st.session_state.route_legs[-1]["TC"] if N >= 1 else 0.0)

dep_elev = aero_elev(dept); arr_elev = aero_elev(arr)
climb_min, climb_nm, climb_ff, tas_climb, gs_climb = compute_climb(dep_elev, cruise_alt, qnh, temp_c, tc_first)
desc_min,  desc_nm,  desc_ff, tas_des,   gs_des   = compute_descent(arr_elev, cruise_alt, qnh, temp_c, tc_last, rod_fpm, rpm_descent, idle_mode, idle_ff)

# consumo de climb por leg (do início)
leg_dists = [float(st.session_state.route_legs[i]["Dist"] or 0.0) for i in range(N)]
climb_consumed = [0.0]*N; rem = float(climb_nm)
for i in range(N):
    use = min(leg_dists[i], max(0.0, rem))
    climb_consumed[i] = use
    rem -= use
# índice do leg onde termina a climb (TOC)
idx_toc = None; run=0.0
for i in range(N):
    run += climb_consumed[i]
    if climb_consumed[i] > 0.0 and run >= climb_nm - 1e-6:
        idx_toc = i; break

# consumo de descent por leg (do fim)
desc_consumed = [0.0]*N; rem = float(desc_nm)
for j in range(N-1, -1, -1):
    use = min(leg_dists[j], max(0.0, rem))
    desc_consumed[j] = use
    rem -= use
# índice do leg onde começa a descent (TOD)
idx_tod = None; run=0.0
for j in range(N-1, -1, -1):
    run += desc_consumed[j]
    if desc_consumed[j] > 0.0 and run >= desc_nm - 1e-6:
        idx_tod = j; break

# ========= Construção da tabela calculada (com TOC/TOD) =========
startup = parse_hhmm(startup_str)
takeoff = add_minutes(startup, 15) if startup else None
clock = takeoff

total_dist = sum(leg_dists)          # distância real (só legs)
total_ete = total_burn = 0.0
efob = float(start_fuel)

calc_rows = []

def add_row(name, from_nm, to_nm, tc, tas, gs, dist, ete_min, ff, eto_clock, mh):
    calc_rows.append({
        "Seg": name,
        "From": from_nm,
        "To": to_nm,
        "TC": round(tc,0),
        "TH": round(apply_var(mh, 0, False) if False else round(tc,0),0),  # placeholder; TH real posto abaixo
        "MH": round(mh,0),
        "TAS": round(tas,0),
        "GS": round(gs,0),
        "Dist": round(dist,1),
        "ETE": round(ete_min,0),
        "ETO": eto_clock,
        "Burn": round(ff*(ete_min/60.0),1),
        "EFOB": round(efob,1),  # atualizado depois
    })

# Vamos gerar linhas por leg + marcadores TOC/TOD
from_name = dept
for i in range(N):
    leg = st.session_state.route_legs[i]
    to_name = leg["To"] if i < N-1 else arr
    freq_to = leg.get("Freq","")
    tc = float(leg.get("TC") or 0.0)
    dist = float(leg.get("Dist") or 0.0)

    # Velocidades por fase
    tas_cruise = float(speed_ref)   # pedido: 80 kt por defeito
    _, th_cru, gs_cru = wind_triangle(tc, tas_cruise, wind_from, wind_kt)
    mh_cru = apply_var(th_cru, var_deg, var_is_e)

    # Pedaços consumidos por climb/desc neste leg
    c_use = climb_consumed[i]; d_use = desc_consumed[i]
    effective = max(0.0, dist - c_use - d_use)

    # 1) Se TOC cai neste leg, inserir linha TOC (ETE = climb_min, Dist=0)
    if idx_toc is not None and i == idx_toc:
        # TH/MH em subida segue o TC do leg onde termina a climb
        _, th_cl, _ = wind_triangle(tc, tas_climb, wind_from, wind_kt)
        mh_cl = apply_var(th_cl, var_deg, var_is_e)
        ete_cl = climb_min
        if clock: clock = add_minutes(clock, int(round(ete_cl)))
        eto_str = clock.strftime("%H:%M") if clock else ""
        burn_cl = climb_ff * (ete_cl/60.0)
        total_ete += ete_cl; total_burn += burn_cl; efob = max(0.0, efob - burn_cl)
        calc_rows.append({
            "Seg":"TOC", "From":from_name, "To":to_name,
            "TC": round(tc,0), "TH": round(th_cl,0), "MH": round(mh_cl,0),
            "TAS": round(tas_climb,0), "GS": round(gs_climb,0),
            "Dist": 0.0, "ETE": int(round(ete_cl)), "ETO": eto_str,
            "Burn": round(burn_cl,1), "EFOB": round(efob,1)
        })

    # 2) Parte de cruzeiro efetiva do leg
    ete_leg = (60.0*effective/max(gs_cru,1e-6)) if effective>0 else 0.0
    # FF de cruzeiro: usamos lookup para FF (rpm ~ 2000 padrão de tabela; ou poderias adicionar rpm input)
    pa_cru = pressure_alt(cruise_alt, qnh)
    _, ff_cru = cruise_lookup(pa_cru, 2000, temp_c)  # FF aproximado em cruzeiro
    burn_leg = ff_cru * (ete_leg/60.0)
    if clock: clock = add_minutes(clock, int(round(ete_leg)))
    eto_str = clock.strftime("%H:%M") if clock else ""
    total_ete += ete_leg; total_burn += burn_leg; efob = max(0.0, efob - burn_leg)
    calc_rows.append({
        "Seg": f"{from_name}→{to_name}",
        "From": from_name, "To": to_name,
        "TC": round(tc,0), "TH": round(th_cru,0), "MH": round(mh_cru,0),
        "TAS": round(tas_cruise,0), "GS": round(gs_cru,0),
        "Dist": round(dist,1), "ETE": int(round(ete_leg)), "ETO": eto_str,
        "Burn": round(burn_leg,1), "EFOB": round(efob,1)
    })

    # 3) Se TOD cai neste leg, inserir linha TOD (ETE = desc_min, Dist=0)
    if idx_tod is not None and i == idx_tod:
        _, th_des, _ = wind_triangle(tc, tas_des, wind_from, wind_kt)
        mh_des = apply_var(th_des, var_deg, var_is_e)
        ete_ds = desc_min
        if clock: clock = add_minutes(clock, int(round(ete_ds)))
        eto_str = clock.strftime("%H:%M") if clock else ""
        burn_ds = desc_ff * (ete_ds/60.0)
        total_ete += ete_ds; total_burn += burn_ds; efob = max(0.0, efob - burn_ds)
        calc_rows.append({
            "Seg":"TOD", "From":from_name, "To":to_name,
            "TC": round(tc,0), "TH": round(th_des,0), "MH": round(mh_des,0),
            "TAS": round(tas_des,0), "GS": round(gs_des,0),
            "Dist": 0.0, "ETE": int(round(ete_ds)), "ETO": eto_str,
            "Burn": round(burn_ds,1), "EFOB": round(efob,1)
        })

    from_name = to_name  # próximo leg

# ETA/Landing/Shutdown
eta = clock
landing = eta
shutdown = add_minutes(eta, 5) if eta else None

# ===== Tabela calculada =====
st.markdown("#### Calculado (TOC/TOD incluídos; ETE usa distância efetiva)")
calc_cfg = {
    "Seg":  st.column_config.TextColumn("Segmento"),
    "From": st.column_config.TextColumn("From", disabled=True),
    "To":   st.column_config.TextColumn("To", disabled=True),
    "TC":   st.column_config.NumberColumn("TC (°T)", disabled=True),
    "TH":   st.column_config.NumberColumn("TH (°T)", disabled=True),
    "MH":   st.column_config.NumberColumn("MH (°M)", disabled=True),
    "TAS":  st.column_config.NumberColumn("TAS (kt)", disabled=True),
    "GS":   st.column_config.NumberColumn("GS (kt)", disabled=True),
    "Dist": st.column_config.NumberColumn("Dist (nm)", disabled=True),
    "ETE":  st.column_config.NumberColumn("ETE (min)", disabled=True),
    "ETO":  st.column_config.TextColumn("ETO", disabled=True),
    "Burn": st.column_config.NumberColumn("Burn (L)", disabled=True),
    "EFOB": st.column_config.NumberColumn("EFOB (L)", disabled=True),
}
st.data_editor(calc_rows, hide_index=True, use_container_width=True,
               num_rows="fixed", column_config=calc_cfg, key="calc_table")

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
    st.error(f"Não foi possível ler o PDF do repositório: {e}")

if template_bytes:
    try:
        fieldset, maxlens = get_fields_and_meta(template_bytes)
        named: Dict[str, str] = {}

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

        # Leg number (sem TOC/TOD)
        put(named, fieldset, "Leg_Number", str(N), maxlens)

        # Preparar até 11 linhas para o PDF (leg rows + TOC/TOD quando existirem)
        pdf_rows = []
        for row in calc_rows:
            # Para PDF, "Name" será o destino do segmento, exceto TOC/TOD que mantêm o nome
            nm = row["Seg"] if row["Seg"] in ("TOC","TOD") else row["To"]
            pdf_rows.append({
                "Name": nm,
                "Freq": aero_freq(nm) if nm in AEROS else "",
                "TC": row["TC"],
                "TH": row["TH"],
                "MH": row["MH"],
                "TAS": row["TAS"],
                "GS": row["GS"],
                "Dist": row["Dist"],
                "ETE": row["ETE"],
                "ETO": row["ETO"],
                "Burn": row["Burn"],
                "EFOB": row["EFOB"],
            })
        pdf_rows = pdf_rows[:11]

        for i, r in enumerate(pdf_rows, start=1):
            s=str(i)
            put(named, fieldset, f"Name{s}", r["Name"], maxlens)
            # put(named, fieldset, f"Alt{s}",  "", maxlens)  # não preencher Alt{s}
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

