# app.py — NAVLOG PDF Filler + Wind Triangle + TOC/TOD via AFM tables
# Reqs: streamlit, pytz, pypdf

import streamlit as st
import datetime as dt
import pytz
from pathlib import Path
import io, unicodedata
from typing import List, Dict, Optional, Tuple
from math import sin, cos, asin, radians, degrees, fmod

# ============ PDF helpers ============
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
        # recolhe /MaxLen quando existir
        for k, v in fd.items():
            try:
                ml = v.get("/MaxLen")
                if ml: maxlens[k] = int(ml)
            except Exception:
                pass
    except Exception:
        pass
    # fallback percorrendo anotações
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
        # auto font size (0) to fit; DA must be a PDF string
        writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): True})
        writer._root_object["/AcroForm"].update({NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g")})
    except Exception:
        pass
    # ensure strings
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

def abbrev_comm(text: str) -> str:
    if not text: return ""
    # trocas curtas comuns para caber nos campos
    rep = {
        "Information": "Info",
        "Lisboa": "Lisboa",
        "Ponte de Sor": "Pte Sor",
        "Évora": "Evora",
        "Control": "CTR",
    }
    for a,b in rep.items(): text = text.replace(a,b)
    return text

# ============ Time helpers ============
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

# ============ Wind triangle ============
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

# ============ AFM tables (excerpt) ============
# (mesmos dicionários que me enviaste; apenas os necessários aqui)
ROC = {
    650:{0:{-25:951,0:805,25:675,50:557,"ISA":725},2000:{-25:840,0:696,25:568,50:453,"ISA":638},4000:{-25:729,0:588,25:462,50:349,"ISA":551},6000:{-25:619,0:480,25:357,50:245,"ISA":464},8000:{-25:509,0:373,25:251,50:142,"ISA":377},10000:{-25:399,0:266,25:146,50:39,"ISA":290},12000:{-25:290,0:159,25:42,50:-64,"ISA":204},14000:{-25:181,0:53,25:-63,50:-166,"ISA":117}},
    600:{0:{-25:1067,0:913,25:776,50:652,"ISA":829},2000:{-25:950,0:799,25:664,50:542,"ISA":737},4000:{-25:833,0:685,25:552,50:433,"ISA":646},6000:{-25:717,0:571,25:441,50:324,"ISA":555},8000:{-25:602,0:458,25:330,50:215,"ISA":463},10000:{-25:486,0:345,25:220,50:106,"ISA":372},12000:{-25:371,0:233,25:110,50:-2,"ISA":280},14000:{-25:257,0:121,25:0,50:-109,"ISA":189}},
    550:{0:{-25:1201,0:1038,25:892,50:760,"ISA":948},2000:{-25:1077,0:916,25:773,50:644,"ISA":851},4000:{-25:953,0:795,25:654,50:527,"ISA":754},6000:{-25:830,0:675,25:536,50:411,"ISA":657},8000:{-25:707,0:555,25:419,50:296,"ISA":560},10000:{-25:584,0:435,25:301,50:181,"ISA":462},12000:{-25:462,0:315,25:184,50:66,"ISA":365},14000:{-25:341,0:196,25:68,50:-48,"ISA":268}},
}
VY = {650:{0:70,2000:69,4000:68,6000:67,8000:65,10000:64,12000:63,14000:62},
      600:{0:70,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:62},
      550:{0:69,2000:68,4000:67,6000:66,8000:65,10000:64,12000:63,14000:61}}

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
    table = VY[w_choice]
    pas = sorted(table.keys())
    pa_c = clamp(pa, pas[0], pas[-1])
    p0 = max([p for p in pas if p <= pa_c]); p1 = min([p for p in pas if p >= pa_c])
    return interp1(pa_c, p0, p1, table[p0], table[p1])

# ============ Aerodromes DB ============
AERODROMES = {
    "LPSO": {"elev_ft":390,  "freqs":["119.805 (Pte Sor INFO)","123.755 (Lisboa Info)"]},
    "LPEV": {"elev_ft":807,  "freqs":["122.705 (Evora INFO)","123.755 (Lisboa Info)","131.055 (Lisboa Info)"]},
    "LPCB": {"elev_ft":1251, "freqs":["130.905 (Lisboa Info)","132.305 (Lisboa CTR)","123.755 (Lisboa Info)"]},
    "LPCO": {"elev_ft":587,  "freqs":["130.905 (Lisboa Info)","132.305 (Lisboa CTR)"]},
    "LPVZ": {"elev_ft":2060, "freqs":["130.905 (Lisboa Info)","132.305 (Lisboa CTR)"]},
}
def aero_elev(icao): return int(AERODROMES.get((icao or "").upper(),{}).get("elev_ft",0))
def aero_freqs(icao): return " / ".join(AERODROMES.get((icao or "").upper(),{}).get("freqs",[]))

# ============ App ============
st.set_page_config(page_title="NAVLOG — Log & Performance", layout="wide", initial_sidebar_state="collapsed")
st.title("Navigation Plan & Inflight Log")

DEFAULT_STUDENT="A. Moiteiro"; DEFAULT_AIRCRAFT="Tecnam P2008"
REGS=["CS-ECC","CS-ECD","CS-DHS","CS-DHT","CS-DHU","CS-DHV","CS-DHW"]
PDF_TEMPLATE_PATHS=["/mnt/data/NAVLOG - FORM.pdf","NAVLOG - FORM.pdf"]

# ===== Header =====
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
    shutdown_str = st.text_input("Shutdown (HH:MM)", "")

st.subheader("Atmosfera & Peso (para cálculos AFM)")
c4,c5,c6,c7 = st.columns(4)
with c4:
    qnh_hpa = st.number_input("QNH (hPa)", 900.0, 1050.0, 1013.0, 0.1)
    oat_dep  = st.number_input("OAT departure (°C)", -40.0, 60.0, 15.0, 0.5)
with c5:
    wind_from = st.number_input("Wind FROM (°TRUE)", 0.0, 360.0, 0.0, 1.0)
    wind_kt   = st.number_input("Wind (kt)", 0.0, 200.0, 0.0, 1.0)
with c6:
    var_deg   = st.number_input("Magnetic variation (°)", 0.0, 30.0, 1.0, 0.1)
    var_is_e  = (st.selectbox("Var (E/W)", ["W","E"], index=0) == "E") # default 1W
with c7:
    weight_kg = st.number_input("Weight for performance (kg)", 450.0, 650.0, 650.0, 1.0)
    cruise_alt_ft = st.number_input("Cruise Altitude (ft)", 0, 14000, 3000, 100)

# Frequências & elevações automáticas
dept_comm = st.text_input("Departure Comms", abbrev_comm(aero_freqs(dept)))
enroute_comm = st.text_input("Enroute Comms", "Lisboa Info 123.755")
arrival_comm = st.text_input("Arrival Comms", abbrev_comm(aero_freqs(arr)))
dep_elev_ft = st.number_input("Departure Elev (ft)", 0, 5000, aero_elev(dept), 1)
arr_elev_ft = st.number_input("Arrival Elev (ft)", 0, 5000, aero_elev(arr), 1)
alt_elev_ft = st.number_input("Alternate Elev (ft)",0, 5000, aero_elev(altn), 1)

# ===== Legs (uma única tabela) =====
st.subheader("Legs (1–11) — **sem** duplicação")
N = st.number_input("Nº de legs (sem contar TOC/TOD)", 1, 11, 6)
if "legs" not in st.session_state:
    st.session_state.legs=[{"Name":"", "Alt/FL":"", "Freq":"", "TC":0.0, "TAS":0.0, "Dist":0.0, "OAT":oat_dep} for _ in range(N)]
# ajustar
cur = st.session_state.legs
if len(cur)!=N:
    if len(cur)<N:
        cur += [{"Name":"", "Alt/FL":"", "Freq":"", "TC":0.0, "TAS":0.0, "Dist":0.0, "OAT":oat_dep} for _ in range(N-len(cur))]
    else:
        st.session_state.legs=cur[:N]

legs = st.data_editor(
    st.session_state.legs, num_rows="fixed", use_container_width=True, hide_index=True,
    column_config={
        "Name": st.column_config.TextColumn("Name / Lat,Long"),
        "Alt/FL": st.column_config.TextColumn("Alt / FL"),
        "Freq": st.column_config.TextColumn("Freq."),
        "TC": st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
        "TAS": st.column_config.NumberColumn("TAS (kt) (0=auto)", step=1.0, min_value=0.0),
        "Dist": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
        "OAT": st.column_config.NumberColumn("OAT (°C)", step=0.5, min_value=-60.0, max_value=60.0),
    }, key="legs_editor"
)

# ===== Cálculos (internos, sem tabela extra) =====
def pa_ft(elev_ft, qnh):
    return float(elev_ft) + (1013.0 - float(qnh)) * 30.0

def compute_climb_bits(dep_elev_ft, cruise_alt_ft, qnh, oat_c, weight, tc_first, tas_first):
    pa = pa_ft(dep_elev_ft, qnh)
    roc = max(1.0, roc_interp(pa, oat_c, weight))      # ft/min
    vy  = vy_interp(pa, weight)                         # kt (aprox usado como TAS subida)
    # GS durante subida (usa vento global + TC do 1º leg)
    tas_climb = vy
    wca, th, gs_climb = wind_triangle(tc_first, tas_climb, wind_from, wind_kt)
    delta_ft = max(0.0, cruise_alt_ft - dep_elev_ft)
    t_min = delta_ft / roc
    d_nm  = gs_climb * (t_min/60.0)
    return roc, vy, gs_climb, t_min, d_nm

def compute_descent_bits(arr_elev_ft, cruise_alt_ft, tc_last, tas_last):
    # Descida 3° (≈318 ft/NM)
    delta_ft = max(0.0, cruise_alt_ft - arr_elev_ft)
    d_nm = delta_ft / 318.0
    # GS no último perna
    tas_des = tas_last if tas_last>0 else 95.0
    _, _, gs_des = wind_triangle(tc_last, tas_des, wind_from, wind_kt)
    t_min = 60.0 * d_nm / max(gs_des, 1e-6)
    return gs_des, t_min, d_nm

BASE_TAS, BASE_FF = 95.0, 18.7
def ff_from_oat(oat_c):
    # usa correção do AFM sobre o ponto base
    # ISA aprox na superfície (0 ft) para efeito de correção
    isa = 15.0
    dev = oat_c - isa
    # TAS não é necessário aqui; devolvemos FF corrigido
    _, ff = ( (BASE_TAS * (1.0 - 0.02*dev/15.0) if dev>0 else BASE_TAS * (1.0 + 0.01*(-dev)/15.0)),
              (BASE_FF * (1.0 - 0.025*dev/15.0) if dev>0 else BASE_FF * (1.0 + 0.03*(-dev)/15.0)) )
    return max(0.0, ff)

# TOC/TOD a partir do 1º/último leg
tc_first = float(legs[0].get("TC") or 0.0)
tc_last  = float(legs[-1].get("TC") or 0.0)
tas_first= float(legs[0].get("TAS") or 0.0)
tas_last = float(legs[-1].get("TAS") or 0.0)

roc_fpm, vy_kt, gs_climb, climb_min, climb_nm = compute_climb_bits(dep_elev_ft, cruise_alt_ft, qnh_hpa, oat_dep, weight_kg, tc_first, tas_first)
gs_des, desc_min, desc_nm = compute_descent_bits(arr_elev_ft, cruise_alt_ft, tc_last, tas_last)

# Inserção virtual de TOC/TOD (Dist=0)
cum, toc_idx, tod_idx_from_end = 0.0, None, None
total_dist_nom = sum(float(r.get("Dist") or 0.0) for r in legs)
for i, r in enumerate(legs):
    d = float(r.get("Dist") or 0.0)
    if toc_idx is None and cum + d >= climb_nm: toc_idx = i
    cum += d
cum = 0.0
for j in range(len(legs)-1, -1, -1):
    d = float(legs[j].get("Dist") or 0.0)
    if tod_idx_from_end is None and cum + d >= desc_nm: tod_idx_from_end = j
    cum += d

final_legs=[]
for i, r in enumerate(legs):
    final_legs.append({**r})
    if toc_idx is not None and i==toc_idx:
        final_legs.append({**r, "Name":"TOC", "Alt/FL":f"FL{int(round(cruise_alt_ft/100))}", "Dist":0.0})
    if tod_idx_from_end is not None and i==tod_idx_from_end:
        final_legs.append({**r, "Name":"TOD", "Alt/FL":"", "Dist":0.0})

# ETO/ETA: Takeoff = Startup +15 ; Landing = Shutdown −5 ; ETA = Takeoff + ETE_total
startup_t  = parse_hhmm(startup_str)
shutdown_t = parse_hhmm(shutdown_str)
takeoff_t  = add_minutes_to_time(startup_t, 15) if startup_t else None
landing_t  = add_minutes_to_time(shutdown_t, -5) if shutdown_t else None

# Cálculo por leg (sem mostrar outra tabela)
start_fuel = st.number_input("Fuel inicial (EFOB_START) [L]", 0.0, 999.0, 0.0, 1.0)
clock = takeoff_t
total_dist=total_ete=total_burn=0.0
efob = start_fuel

calc_rows=[]
for r in final_legs:
    name=r.get("Name",""); altfl=r.get("Alt/FL",""); freq=r.get("Freq","")
    tc=float(r.get("TC") or 0.0); tas=float(r.get("TAS") or 0.0)
    dist=float(r.get("Dist") or 0.0); oat=float(r.get("OAT") or oat_dep)

    # TAS automático se 0 → usa BASE_TAS com correção OAT (para cruzeiro)
    if tas<=0:
        # correção simples conforme OAT (cruzeiro)
        dev=oat-15.0
        tas = BASE_TAS*(1.0 - 0.02*dev/15.0) if dev>0 else BASE_TAS*(1.0 + 0.01*(-dev)/15.0)

    wca, th, gs = wind_triangle(tc, tas, wind_from, wind_kt)
    mh = apply_var(th, var_deg, var_is_e)

    ete_min = (60.0*dist/max(gs,1e-6)) if dist>0 else 0.0
    ff = ff_from_oat(oat)
    burn = ff*(ete_min/60.0)
    total_dist += dist; total_ete += ete_min; total_burn += burn
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
tot_line = f"**Totais** — Dist {total_dist:.1f} nm • ETE {int(total_ete)//60}h{int(total_ete)%60:02d} • Burn {total_burn:.1f} L • EFOB {efob:.1f} L"
if eta_calc: tot_line += f" • **ETA {eta_calc.strftime('%H:%M')}**"
st.markdown(tot_line)

# ===== PDF export =====
st.subheader("PDF export")
safe_reg = ascii_safe(registration); safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
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
    # Altitudes dos aeródromos (se houver campos)
    put_any(named, fieldset, ["Alt_Dep","Dept_Elev","Departure_Elev"], dep_elev_ft, maxlens)
    put_any(named, fieldset, ["Alt_Arr","Arrival_Elev"], arr_elev_ft, maxlens)
    put_any(named, fieldset, ["Alt_Alt","Alternate_Elev"], alt_elev_ft, maxlens)

    takeoff_str = (add_minutes_to_time(parse_hhmm(startup_str), 15).strftime("%H:%M") if parse_hhmm(startup_str) else "")
    landing_str = (add_minutes_to_time(parse_hhmm(shutdown_str), -5).strftime("%H:%M") if parse_hhmm(shutdown_str) else "")
    eta_str = eta_calc.strftime("%H:%M") if eta_calc else ""
    put_any(named, fieldset, "Startup", startup_str, maxlens)
    put_any(named, fieldset, "Takeoff", takeoff_str, maxlens)
    put_any(named, fieldset, "Landing", landing_str, maxlens)
    put_any(named, fieldset, "Shutdown", shutdown_str, maxlens)
    put_any(named, fieldset, ["ETD/ETA","ETD_ETA"], f"{takeoff_str} / {eta_str}", maxlens)

    put_any(named, fieldset, ["Dept_Comm","Departure_Comms"], abbrev_comm(dept_comm), maxlens)
    put_any(named, fieldset, ["Enroute_comm","Enroute_Comms"], abbrev_comm(enroute_comm), maxlens)
    put_any(named, fieldset, ["Arrival_comm","Arrival_Comms"], abbrev_comm(arrival_comm), maxlens)

    # Legs 1..11
    for i, r in enumerate(calc_rows[:11], start=1):
        s=str(i)
        put_any(named, fieldset, [f"Name{s}","Name_{s}"], r["Name"], maxlens)
        put_any(named, fieldset, [f"Alt{s}","Alt_{s}"],  r["Alt/FL"], maxlens)
        put_any(named, fieldset, [f"FREQ{s}","FREQ_{s}"], r["Freq"], maxlens)
        put_any(named, fieldset, [f"TCRS{s}","TCRS_{s}"], r["TC"], maxlens)
        put_any(named, fieldset, [f"THDG{s}","THDG_{s}"], r["TH"], maxlens)
        put_any(named, fieldset, [f"MHDG{s}","MHDG_{s}"], r["MH"], maxlens)
        put_any(named, fieldset, [f"GS{s}","GS_{s}"],     r["GS"], maxlens)
        put_any(named, fieldset, [f"Dist{s}","Dist_{s}"], r["Dist"], maxlens)
        put_any(named, fieldset, [f"ETE{s}","ETE_{s}"],   r["ETE(min)"], maxlens)
        put_any(named, fieldset, [f"ETO{s}","ETO_{s}"],   r["ETO"], maxlens)
        put_any(named, fieldset, [f"PL_BO{s}","PL_BO_{s}"], r["PL_B/O (L)"], maxlens)
        put_any(named, fieldset, [f"EFOB{s}","EFOB_{s}"], r["EFOB (L)"], maxlens)

    # Totais (se existirem)
    # (opcional: alguns NAVLOG têm estes campos)
    # put_any(named, fieldset, ["ETE_TOTAL","Dist_TOTAL", ...], ...)

    if st.button("Gerar PDF preenchido", type="primary"):
        try:
            out = fill_pdf(template, named)
            st.download_button("Download PDF", data=out, file_name=filename, mime="application/pdf")
            st.success("PDF gerado. Revê antes do voo.")
        except Exception as e:
            st.error(f"Erro ao gerar PDF: {e}")

except Exception as e:
    st.error(f"Falha a preparar PDF: {e}")



