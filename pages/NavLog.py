# app.py — NAVLOG PDF Filler + Wind Triangle + TOC/TOD (sem sidebar, sem tabelas duplicadas)
# Reqs: streamlit, pytz, pypdf

import streamlit as st
import datetime as dt
import pytz
from pathlib import Path
import io
import unicodedata
from typing import List, Dict, Optional, Tuple
from math import sin, cos, asin, radians, degrees, fmod

# ============ PDF helpers ============
try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, TextStringObject
    PYPDF_OK = True
except Exception:
    PYPDF_OK = False

def ascii_safe(text: str) -> str:
    if text is None: return ""
    return unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode('ascii')

def read_pdf_bytes(paths: List[str]) -> bytes:
    for pstr in paths:
        p = Path(pstr)
        if p.exists():
            return p.read_bytes()
    raise FileNotFoundError(f"Template not found in any known path: {paths}")

def get_field_names(template_bytes: bytes) -> set:
    names = set()
    reader = PdfReader(io.BytesIO(template_bytes))
    try:
        fd = reader.get_fields()
        if fd: names.update(fd.keys())
    except Exception:
        pass
    try:
        for page in reader.pages:
            if "/Annots" in page:
                for a in page["/Annots"]:
                    obj = a.get_object()
                    if obj.get("/T"): names.add(str(obj["/T"]))
    except Exception:
        pass
    return names

def fill_pdf(template_bytes: bytes, fields: dict) -> bytes:
    if not PYPDF_OK:
        raise RuntimeError("pypdf not available. Add 'pypdf' to requirements.txt")
    reader = PdfReader(io.BytesIO(template_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    root = reader.trailer["/Root"]
    if "/AcroForm" not in root:
        raise RuntimeError("Template PDF has no AcroForm/fields.")
    writer._root_object.update({NameObject("/AcroForm"): root["/AcroForm"]})
    try:
        # Fonte auto (font size 0) para caber no campo
        writer._root_object["/AcroForm"].update({NameObject("/NeedAppearances"): True})
        writer._root_object["/AcroForm"].update({NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g")})
    except Exception:
        pass
    # garantir strings
    str_fields = {k: ("" if v is None else str(v)) for k, v in fields.items()}
    for page in writer.pages:
        writer.update_page_form_field_values(page, str_fields)
    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()

def put_any(out: dict, fieldset: set, keys, value: str):
    if isinstance(keys, str): keys = [keys]
    for k in keys:
        if k in fieldset:
            out[k] = "" if value is None else str(value)

# ============ Time helpers ============
def parse_hhmm(s: str) -> Optional[dt.time]:
    s = (s or "").strip()
    if not s: return None
    for fmt in ("%H:%M", "%H%M"):
        try:
            return dt.datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    return None

def add_minutes_to_time(t: dt.time, minutes: int, tzinfo=pytz.timezone("Europe/Lisbon")) -> dt.time:
    today = dt.date.today()
    base = tzinfo.localize(dt.datetime.combine(today, t))
    new_dt = base + dt.timedelta(minutes=int(minutes or 0))
    return new_dt.timetz().replace(tzinfo=None)

# ============ Wind triangle ============
def wrap360(x):
    x = fmod(x, 360.0)
    return x + 360.0 if x < 0 else x

def angle_diff(a, b):
    return (a - b + 180.0) % 360.0 - 180.0

def wind_triangle(true_course_deg, tas_kt, wind_dir_from_deg, wind_kt):
    """
    Devolve (WCA_deg, TH_deg, GS_kt)
    Entradas:
      TC (°TRUE), TAS (kt), Vento FROM (°TRUE), Vento (kt)
    """
    if tas_kt <= 0:
        return 0.0, wrap360(true_course_deg), 0.0
    beta = radians(angle_diff(wind_dir_from_deg, true_course_deg))
    cross = wind_kt * sin(beta)
    head  = wind_kt * cos(beta)
    s = max(-1.0, min(1.0, cross / max(tas_kt, 1e-9)))
    wca = degrees(asin(s))
    gs  = tas_kt * cos(radians(wca)) - head
    th  = wrap360(true_course_deg + wca)
    return wca, th, max(0.0, gs)

def apply_variation(true_deg, variation_deg, east_is_negative: bool):
    """MH = TH - Var(E) / + Var(W)."""
    return wrap360(true_deg - variation_deg if east_is_negative else true_deg + variation_deg)

# ============ AFM cruise corrections (±15 °C) ============
# TAS −2% (a +15°C) / +1% (a −15°C); Fuel −2.5% / +3% (aprox linear)
def apply_oat_corrections(tas_base, ff_base, oat_dev_c):
    tas = tas_base
    ff  = ff_base
    if oat_dev_c > 0:
        tas *= 1.0 - 0.02 * (oat_dev_c / 15.0)
        ff  *= 1.0 - 0.025 * (oat_dev_c / 15.0)
    elif oat_dev_c < 0:
        tas *= 1.0 + 0.01 * (abs(oat_dev_c) / 15.0)
        ff  *= 1.0 + 0.03 * (abs(oat_dev_c) / 15.0)
    return tas, ff

def isa_at(pa_ft: float) -> float:
    return 15.0 - 2.0 * (float(pa_ft)/1000.0)

# ============ Aeródromos: altitudes + frequências ============
AERODROMES = {
    "LPSO": {"elev_ft": 390,  "freqs": ["119.805 (Ponte de Sor INFO)", "123.755 (Lisboa Information)"]},
    "LPEV": {"elev_ft": 807,  "freqs": ["122.705 (Évora INFO)", "123.755 (Lisboa Information)", "131.055 (Lisboa Information)"]},
    "LPCB": {"elev_ft": 1251, "freqs": ["130.905 (Lisboa Information)", "132.305 (Lisboa Control HN)", "123.755 (Lisboa Information)"]},
    "LPCO": {"elev_ft": 587,  "freqs": ["130.905 (Lisboa Information)", "132.305 (Lisboa Control HN)"]},
    "LPVZ": {"elev_ft": 2060, "freqs": ["130.905 (Lisboa Information)", "132.305 (Lisboa Control HN)"]},
}
def default_comms(aero: str) -> str:
    if not aero: return ""
    return " / ".join(AERODROMES.get(aero.upper(), {}).get("freqs", []))
def default_elev(aero: str) -> int:
    return int(AERODROMES.get(aero.upper(), {}).get("elev_ft", 0) or 0)

# ============ App ============
st.set_page_config(page_title="NAVLOG — Log & Performance", layout="wide", initial_sidebar_state="collapsed")
st.title("Navigation Plan & Inflight Log")

# Defaults
DEFAULT_STUDENT  = "A. Moiteiro"
DEFAULT_AIRCRAFT = "Tecnam P2008"
SEVENAIR_P2008_REGS = ["CS-ECC", "CS-ECD", "CS-DHS", "CS-DHT", "CS-DHU", "CS-DHV", "CS-DHW"]

PDF_TEMPLATE_PATHS = [
    "/mnt/data/NAVLOG - FORM.pdf",
    "NAVLOG - FORM.pdf",
]

# ===== Header =====
st.subheader("Header")
c1, c2, c3 = st.columns([1,1,1])
with c1:
    aircraft = st.text_input("Aircraft", DEFAULT_AIRCRAFT)
    registration = st.selectbox("Registration", SEVENAIR_P2008_REGS, index=0)
    callsign = st.text_input("Callsign", "RVP")
with c2:
    student = st.text_input("Student", DEFAULT_STUDENT)
    dept_airfield = st.selectbox("Departure (ICAO)", ["LPSO","LPEV","LPCB","LPCO","LPVZ"], index=0)
    arr_airfield = st.selectbox("Arrival (ICAO)", ["","LPSO","LPEV","LPCB","LPCO","LPVZ"], index=1)
with c3:
    alternate_airfield = st.selectbox("Alternate (ICAO)", ["","LPSO","LPEV","LPCB","LPCO","LPVZ"], index=2)
    startup_str = st.text_input("Startup (HH:MM)", "")
    shutdown_str = st.text_input("Shutdown (HH:MM)", "")

# variação e vento globais (sem sidebar)
st.subheader("Variation & Wind (global) + Cruise")
cv, cw1, cw2, cperf = st.columns([1,1,1,2])
with cv:
    var_deg = st.number_input("Mag variation (°) (def. 1W)", min_value=0.0, max_value=30.0, value=1.0, step=0.1)
    # por defeito 1W → east_is_negative=False
    var_is_east = (st.selectbox("Var (E/W)", ["W","E"], index=0) == "E")
with cw1:
    default_wdir = st.number_input("Wind FROM (°TRUE)", min_value=0.0, max_value=360.0, value=0.0, step=1.0)
with cw2:
    default_wspd = st.number_input("Wind (kt)", min_value=0.0, max_value=200.0, value=0.0, step=1.0)
with cperf:
    cruise_alt_ft = st.number_input("Cruise Altitude (ft)", min_value=0, max_value=14000, value=3000, step=100)
    roc_fpm = st.number_input("Climb rate ROC (ft/min)", min_value=100, max_value=1500, value=700, step=10)
    rod_fpm = st.number_input("Descent rate ROD (ft/min)", min_value=100, max_value=1500, value=500, step=10)

# Comms + Altitudes automáticas
dept_comm = st.text_input("Departure Comms/Freq", default_comms(dept_airfield))
enroute_comm = st.text_input("Enroute Comms/Freq", "Lisboa Information 123.755")
arrival_comm = st.text_input("Arrival Comms/Freq", default_comms(arr_airfield))
dep_elev_ft = st.number_input("Departure Elev (ft)", min_value=0, max_value=5000, value=default_elev(dept_airfield), step=1)
arr_elev_ft = st.number_input("Arrival Elev (ft)", min_value=0, max_value=5000, value=default_elev(arr_airfield), step=1)
alt_elev_ft = st.number_input("Alternate Elev (ft)", min_value=0, max_value=5000, value=default_elev(alternate_airfield), step=1)

# ===== Legs (um único editor) =====
st.subheader("Legs (1–11, sem Ident, sem duplicação de tabelas)")
st.caption("Preenche Name/Alt/Freq, **TC/TAS/Dist**, vento/OAT. O app calcula internamente TH/MH/GS/ETE/ETO/Burn/EFOB e exporta para o PDF.")
N = st.number_input("Nº de legs (sem contar TOC/TOD)", min_value=1, max_value=11, value=6, step=1)

if "legs" not in st.session_state:
    st.session_state.legs = [
        {"Name":"", "Alt/FL":"", "Freq":"",
         "TC":0.0, "TAS":0.0, "Dist":0.0,
         "WindFROM":default_wdir, "Wind":default_wspd, "OAT":15.0}
        for _ in range(int(N))
    ]
# ajusta comprimento
cur = st.session_state.legs
if len(cur) != int(N):
    if len(cur) < int(N):
        cur += [{"Name":"", "Alt/FL":"", "Freq":"",
                 "TC":0.0, "TAS":0.0, "Dist":0.0,
                 "WindFROM":default_wdir, "Wind":default_wspd, "OAT":15.0}
                for _ in range(int(N)-len(cur))]
    else:
        st.session_state.legs = cur[:int(N)]

legs = st.data_editor(
    st.session_state.legs,
    num_rows="fixed",
    use_container_width=True,
    column_config={
        "Name": st.column_config.TextColumn("Name / Lat,Long"),
        "Alt/FL": st.column_config.TextColumn("Alt / FL"),
        "Freq": st.column_config.TextColumn("Freq."),
        "TC": st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
        "TAS": st.column_config.NumberColumn("TAS (kt) (0=auto)", step=1.0, min_value=0.0),
        "Dist": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
        "WindFROM": st.column_config.NumberColumn("Wind FROM (°T)", step=1.0, min_value=0.0, max_value=360.0),
        "Wind": st.column_config.NumberColumn("Wind (kt)", step=1.0, min_value=0.0),
        "OAT": st.column_config.NumberColumn("OAT (°C)", step=0.5, min_value=-60.0, max_value=60.0),
    },
    hide_index=True,
    key="legs_editor"
)

# ===== Cálculos (internos) =====
BASE_TAS = 95.0   # kt @ SL (aprox)
BASE_FF  = 18.7   # L/h @ SL (aprox)

def compute_leg(tc, tas, dist, wdir, wspd, oat, var_deg, var_is_east, pa_ft=0.0) -> Tuple[Dict,str,float,float,float,float]:
    # TAS/FF automáticos (se TAS=0)
    if tas <= 0:
        tas_auto, ff_auto = apply_oat_corrections(BASE_TAS, BASE_FF, oat - isa_at(pa_ft))
        tas, ff_lph = tas_auto, ff_auto
    else:
        # FF ajustado pela OAT (base→corrigido)
        _, ff_lph = apply_oat_corrections(BASE_TAS, BASE_FF, oat - isa_at(pa_ft))
    wca, th, gs = wind_triangle(tc, tas, wdir, wspd)
    mh = apply_variation(th, var_deg, var_is_east)
    ete_min = (60.0 * dist / gs) if gs > 0 else 0.0
    burn = ff_lph * (ete_min/60.0)
    return (
        {"TC":f"{tc:.1f}", "TAS":f"{tas:.0f}", "GS":f"{gs:.0f}", "TH":f"{th:06.2f}",
         "MH":f"{mh:06.2f}", "WCA":f"{wca:+.1f}", "ETE(min)":f"{ete_min:.0f}", "PL_B/O (L)":f"{burn:.1f}"},
        f"{ete_min:.0f}", gs, ff_lph, tas, burn
    )

def compute_toc_tod_nm(cruise_alt_ft, roc_fpm, rod_fpm, gs_climb, gs_descent, dep_elev_ft=0, arr_elev_ft=0):
    climb_h = max(0.0, (cruise_alt_ft - dep_elev_ft) / max(roc_fpm, 1e-6)) / 60.0
    desc_h  = max(0.0, (cruise_alt_ft - arr_elev_ft) / max(rod_fpm, 1e-6)) / 60.0
    d_climb = gs_climb * climb_h
    d_desc  = gs_descent * desc_h
    return d_climb, d_desc, climb_h*60.0, desc_h*60.0

# GS do 1º e último leg para estimar dist TOC/TOD
first_leg = legs[0] if legs else None
last_leg  = legs[-1] if legs else None
def gs_for(r):
    _, _, gs, _, _, _ = compute_leg(
        float(r.get("TC") or 0.0),
        float(r.get("TAS") or 0.0),
        1.0,
        float(r.get("WindFROM") or default_wdir),
        float(r.get("Wind") or default_wspd),
        float(r.get("OAT") or 15.0),
        var_deg, var_is_east
    )
    return gs
gs_first = gs_for(first_leg) if first_leg else 90.0
gs_last  = gs_for(last_leg)  if last_leg  else 90.0

d_toc_nm, d_tod_nm, climb_min, desc_min = compute_toc_tod_nm(
    cruise_alt_ft, roc_fpm, rod_fpm, gs_first, gs_last, dep_elev_ft, arr_elev_ft
)

# Inserções virtuais TOC/TOD (Dist=0, não duplicamos tabela visível)
cum = 0.0
insert_after_idx_toc = None
for idx, r in enumerate(legs):
    d = float(r.get("Dist") or 0.0)
    if insert_after_idx_toc is None and cum + d >= d_toc_nm:
        insert_after_idx_toc = idx
    cum += d
cum_back = 0.0
insert_before_idx_tod = None
for idx in range(len(legs)-1, -1, -1):
    d = float(legs[idx].get("Dist") or 0.0)
    if insert_before_idx_tod is None and cum_back + d >= d_tod_nm:
        insert_before_idx_tod = idx
    cum_back += d

final_legs: List[Dict] = []
for i, r in enumerate(legs):
    final_legs.append({**r})
    if insert_after_idx_toc is not None and i == insert_after_idx_toc:
        final_legs.append({**r, "Name":"TOC", "Alt/FL":f"FL{int(round(cruise_alt_ft/100))}", "Dist":0.0})
    if insert_before_idx_tod is not None and i == insert_before_idx_tod:
        final_legs.append({**r, "Name":"TOD", "Alt/FL":"", "Dist":0.0})

# Tempos base: Takeoff = Startup +15; Landing = Shutdown −5; ETA = Takeoff + ETE_total
startup_t = parse_hhmm(startup_str)
shutdown_t = parse_hhmm(shutdown_str)
takeoff_t = add_minutes_to_time(startup_t, 15) if startup_t else None
landing_t = add_minutes_to_time(shutdown_t, -5) if shutdown_t else None

# Percorrer e calcular tudo (sem mostrar segunda tabela)
total_dist = total_ete = total_burn = 0.0
efob_start = st.number_input("Fuel inicial (EFOB_START) [L]", min_value=0.0, value=0.0, step=1.0)
efob_running = efob_start

# ETO acumulado parte do TAKEOFF (não do startup)
clock = takeoff_t
calc_rows: List[Dict] = []
for r in final_legs:
    tc   = float(r.get("TC") or 0.0)
    tas  = float(r.get("TAS") or 0.0)
    dist = float(r.get("Dist") or 0.0)
    wdir = float(r.get("WindFROM") or default_wdir)
    wspd = float(r.get("Wind") or default_wspd)
    oat  = float(r.get("OAT") or 15.0)

    line, ete_min_str, gs, ff_lph, tas_eff, burn = compute_leg(tc, tas, dist, wdir, wspd, oat, var_deg, var_is_east)
    ete_min = float(ete_min_str)

    eto_str = ""
    if clock:
        clock = add_minutes_to_time(clock, int(round(ete_min)))
        eto_str = clock.strftime("%H:%M")

    total_dist += dist
    total_ete += ete_min
    total_burn += burn
    efob_running = max(0.0, efob_running - burn)

    calc_rows.append({
        "Name": r.get("Name",""), "Alt/FL": r.get("Alt/FL",""), "Freq": r.get("Freq",""),
        **line, "ETO": eto_str, "EFOB (L)": f"{efob_running:.1f}", "Dist": f"{dist:.1f}"
    })

# ETA calculada
eta_calc = None
if takeoff_t:
    eta_calc = add_minutes_to_time(takeoff_t, int(round(total_ete)))

# Mostrar apenas totais (sem segunda tabela)
tot_line = f"**Totais** — Dist {total_dist:.1f} nm • ETE {int(total_ete)//60}h{int(total_ete)%60:02d} • Burn {total_burn:.1f} L • EFOB final {efob_running:.1f} L"
if eta_calc:
    tot_line += f" • **ETA** {eta_calc.strftime('%H:%M')}"
st.markdown(tot_line)

# ====== PDF export ======
st.subheader("PDF export")
safe_reg = ascii_safe(registration or "REG")
safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
filename = f"{safe_date}_{safe_reg}_NAVLOG.pdf"

try:
    template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
    fieldset = get_field_names(template_bytes)
    named_map: Dict[str, str] = {}

    # Cabeçalho — só o que existir no PDF
    put_any(named_map, fieldset, ["Aircraft","Aircraf","Aircraft_Type"], aircraft)
    put_any(named_map, fieldset, ["Registration","REG"], registration)
    put_any(named_map, fieldset, ["Callsign","CALLSIGN"], callsign)
    put_any(named_map, fieldset, ["Student","STUDENT"], student)

    put_any(named_map, fieldset, ["Dept_Airfield","Departure","Dept"], dept_airfield)
    put_any(named_map, fieldset, ["Arrival_Airfield","Arrival"], arr_airfield)
    put_any(named_map, fieldset, ["Alternate","Alternate_Airfield"], alternate_airfield)

    # Altitudes automáticas (se as caixas existirem)
    put_any(named_map, fieldset, ["Dept_Elev","Departure_Elev","Alt_Dep"], str(dep_elev_ft))
    put_any(named_map, fieldset, ["Arrival_Elev","Alt_Arr"], str(arr_elev_ft))
    put_any(named_map, fieldset, ["Alternate_Elev","Alt_Alt"], str(alt_elev_ft))

    # Tempos planeados
    takeoff_str = takeoff_t.strftime("%H:%M") if takeoff_t else ""
    landing_str = landing_t.strftime("%H:%M") if landing_t else ""
    eta_str_final = eta_calc.strftime("%H:%M") if eta_calc else ""
    put_any(named_map, fieldset, "Startup", startup_str)
    put_any(named_map, fieldset, "Takeoff", takeoff_str)
    put_any(named_map, fieldset, "Landing", landing_str)
    put_any(named_map, fieldset, "Shutdown", shutdown_str)
    # Alguns NAVLOGs têm um único campo "ETD/ETA"
    put_any(named_map, fieldset, ["ETD/ETA","ETD_ETA"], f"{takeoff_str} / {eta_str_final}")

    put_any(named_map, fieldset, ["Dept_Comm","Departure_Comms"], default_comms(dept_airfield) if not dept_comm else dept_comm)
    put_any(named_map, fieldset, ["Enroute_comm","Enroute_Comms"], enroute_comm)
    put_any(named_map, fieldset, ["Arrival_comm","Arrival_Comms"], default_comms(arr_airfield) if not arrival_comm else arrival_comm)

    # Legs 1..11 — campos típicos do NAVLOG:
    # Namei, Alti, TCRSi, THDGi, MHDGi, GSi, Disti, ETEi, ETOi, PL_BOi, EFOBi, FREQi
    for i, r in enumerate(calc_rows[:11], start=1):
        suf = str(i)
        put_any(named_map, fieldset, [f"Name{suf}","Name_"+suf], r["Name"])
        put_any(named_map, fieldset, [f"Alt{suf}","Alt_"+suf],  r["Alt/FL"])
        put_any(named_map, fieldset, [f"FREQ{suf}","FREQ_"+suf], r.get("Freq",""))
        put_any(named_map, fieldset, [f"TCRS{suf}","TCRS_"+suf], r["TC"])
        put_any(named_map, fieldset, [f"THDG{suf}","THDG_"+suf], r["TH"])
        put_any(named_map, fieldset, [f"MHDG{suf}","MHDG_"+suf], r["MH"])
        put_any(named_map, fieldset, [f"GS{suf}","GS_"+suf],     r["GS"])
        put_any(named_map, fieldset, [f"Dist{suf}","Dist_"+suf], r["Dist"])
        put_any(named_map, fieldset, [f"ETE{suf}","ETE_"+suf],   r["ETE(min)"])
        put_any(named_map, fieldset, [f"ETO{suf}","ETO_"+suf],   r["ETO"])
        put_any(named_map, fieldset, [f"PL_BO{suf}","PL_BO_"+suf], r["PL_B/O (L)"])
        put_any(named_map, fieldset, [f"EFOB{suf}","EFOB_"+suf], r["EFOB (L)"])

    # Totais (se existirem)
    put_any(named_map, fieldset, ["ETE_Total","ETE_TOTAL"], str(int(round(total_ete))))
    put_any(named_map, fieldset, ["Dist_Total","DIST_TOTAL"], f"{total_dist:.1f}")
    put_any(named_map, fieldset, ["PL_BO_TOTAL","PLBO_TOTAL"], f"{total_burn:.1f}")
    put_any(named_map, fieldset, ["EFOB_TOTAL","EFOB_TOTAL_"], f"{efob_running:.1f}")

    if st.button("Gerar PDF preenchido", type="primary"):
        try:
            out_bytes = fill_pdf(template_bytes, named_map)
            st.download_button("Download PDF", data=out_bytes, file_name=filename, mime="application/pdf")
            st.success("PDF gerado. Revê antes do voo.")
        except Exception as e:
            st.error(f"Erro ao gerar PDF: {e}")

except Exception as e:
    st.error("Não foi possível preparar o mapeamento do PDF: "
             f"{e}\nConfere se o ficheiro existe em /mnt/data ou no diretório atual.")


