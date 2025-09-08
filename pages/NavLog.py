# app.py — NAVLOG PDF Filler + Wind Triangle (TC→TH/MH, GS, ETE, Burn, EFOB)
# Reqs: streamlit, pytz, pypdf

import streamlit as st
import datetime as dt
import pytz
from pathlib import Path
import io
import unicodedata
from typing import List, Dict, Optional
from math import sin, cos, asin, radians, degrees, fmod

# ============ PDF helpers ============
try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject
    PYPDF_OK = True
except Exception:
    PYPDF_OK = False

def ascii_safe(text: str) -> str:
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

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
        if fd:
            names.update(fd.keys())
    except Exception:
        pass
    try:
        for page in reader.pages:
            if "/Annots" in page:
                for a in page["/Annots"]:
                    obj = a.get_object()
                    if obj.get("/T"):
                        names.add(str(obj["/T"]))
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
        writer._root_object["/AcroForm"].update({
            NameObject("/NeedAppearances"): True,
            NameObject("/DA"): "/Helv 0 Tf 0 g"
        })
    except Exception:
        pass
    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)
    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()

def put_any(out: dict, fieldset: set, keys, value: str):
    if isinstance(keys, str): keys = [keys]
    for k in keys:
        if k in fieldset:
            out[k] = value

# ============ Time helpers ============
def parse_hhmm(s: str) -> Optional[dt.time]:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%H:%M", "%H%M"):
        try:
            return dt.datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    return None

def minutes_to_hhmm(m: int) -> str:
    if m is None:
        return ""
    m = max(0, int(round(m)))
    h, mm = divmod(m, 60)
    return f"{h:02d}:{mm:02d}"

def add_minutes_to_time(t: dt.time, minutes: int, tzinfo=pytz.timezone("Europe/Lisbon")) -> dt.time:
    """Soma minutos a uma hora naive interpretada no fuso dado, devolve hora (00:00–23:59)."""
    today = dt.date.today()
    base = tzinfo.localize(dt.datetime.combine(today, t))
    new_dt = base + dt.timedelta(minutes=minutes)
    return new_dt.timetz().replace(tzinfo=None)

# ============ Wind triangle ============
def wrap360(x):
    x = fmod(x, 360.0)
    return x + 360.0 if x < 0 else x

def angle_diff(a, b):
    """smallest signed angle a−b (deg) in [-180,180]."""
    return (a - b + 180.0) % 360.0 - 180.0

def wind_triangle(true_course_deg, tas_kt, wind_dir_from_deg, wind_kt):
    """
    devolve (WCA_deg, TH_deg, GS_kt)
    Inputs:
      - true_course_deg = curso pretendido (°TRUE)
      - tas_kt = True Airspeed
      - wind_dir_from_deg = vento 'DE' (°TRUE)
      - wind_kt = intensidade vento
    Fórmulas clássicas.
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

def apply_variation(true_deg, variation_deg, east_variation: bool):
    """MH = TH - Var(E) / + Var(W) — 'East is least, West is best'."""
    return wrap360(true_deg - variation_deg if east_variation else true_deg + variation_deg)

# ============ App ============
st.set_page_config(page_title="NAVLOG – Navigation Plan & Inflight Log", layout="wide", initial_sidebar_state="collapsed")
st.title("Navigation Plan & Inflight Log – PDF Filler")

st.markdown("Preenche **True Course (TC)** e **TAS**; o app calcula **TH/MH**, **GS**, **ETE** e **Burn/EFOB**. Exporta para o PDF do NAVLOG.")

# Local do template (prioriza o /mnt/data com o ficheiro carregado)
PDF_TEMPLATE_PATHS = [
    "/mnt/data/NAVLOG - FORM.pdf",
    "NAVLOG - FORM.pdf",
]

# ----- Defaults pedidos -----
DEFAULT_INSTRUTOR = "A. Moiteiro"
DEFAULT_AIRCRAFT  = "Tecnam P2008"
# Podes editar/expandir esta lista conforme precisares:
SEVENAIR_P2008_REGS = ["CS-ECC", "CS-ECD", "CS-DHS", "CS-DHT", "CS-DHU", "CS-DHV", "CS-DHW"]

# ====== Sidebar: Vento & Variação + FF ======
st.sidebar.header("Vento & Variação")
default_wdir = st.sidebar.number_input("Wind direction FROM (°TRUE)", 0.0, 360.0, 0.0, 1.0)
default_wspd = st.sidebar.number_input("Wind speed (kt)", 0.0, 200.0, 0.0, 1.0)
var_deg = st.sidebar.number_input("Magnetic variation (°)", 0.0, 30.0, 2.0, 0.1)
var_is_east = st.sidebar.toggle("Variation is EAST? (E=− ; W=+)", value=True)
ff_mode = st.sidebar.selectbox("Fuel flow", ["Manual L/h por leg", "Automático (ligar às tabelas em breve)"])
default_ff_lph = st.sidebar.number_input("Se manual: Fuel flow padrão (L/h)", 0.0, 50.0, 20.0, 0.5)

# ====== Cabeçalho / Flight header ======
st.header("Flight header")
c1, c2, c3, c4 = st.columns(4)
with c1:
    aircraft = st.text_input("Aircraft", DEFAULT_AIRCRAFT)
    registration = st.selectbox("Registration", SEVENAIR_P2008_REGS, index=0)
    callsign = st.text_input("Callsign", "")
    lesson = st.text_input("Lesson", "")
with c2:
    instrutor = st.text_input("Instrutor", DEFAULT_INSTRUTOR)
    student = st.text_input("Student", "")
    logbook = st.text_input("Logbook", "")
    grading = st.text_input("Grading", "")
with c3:
    dept_airfield = st.text_input("Departure Airfield (ICAO)", "")
    arr_airfield = st.text_input("Arrival Airfield (ICAO)", "")
    leg_number = st.text_input("Leg Number", "")
    alternate_airfield = st.text_input("Alternate Airfield (ICAO)", "")
with c4:
    etd_str = st.text_input("ETD (HH:MM)", "")
    eta_str = st.text_input("ETA (HH:MM) (opcional)", "")
    startup = st.text_input("Startup (HH:MM)", "")
    takeoff = st.text_input("Takeoff (HH:MM)", "")
    landing = st.text_input("Landing (HH:MM)", "")
    shutdown = st.text_input("Shutdown (HH:MM)", "")

st.subheader("Ops & ATC")
c5, c6, c7, c8 = st.columns(4)
with c5:
    level_ff = st.text_input("Level F/F", "")
    climb_fuel = st.text_input("Climb Fuel", "")
    qnh = st.text_input("QNH", "")
with c6:
    clearances = st.text_area("Clearances", "", height=80)
with c7:
    dept_comm = st.text_area("Departure Comms/Freq", "", height=80)
    enroute_comm = st.text_area("Enroute Comms/Freq", "", height=80)
with c8:
    arrival_comm = st.text_area("Arrival Comms/Freq", "", height=80)

st.subheader("Enroute info (genérico)")
c9, c10, c11, c12 = st.columns(4)
with c9:
    flt_lvl_alt = st.text_input("Flight level / Altitude", "")
with c10:
    wind_info = st.text_input("Wind (dir/kt)", "")
with c11:
    mag_var = st.text_input("Mag. Var", f"{var_deg:.1f}° {'E' if var_is_east else 'W'}")
with c12:
    temp_isa_dev = st.text_input("Temp / ISA Dev", "")

# ====== Legs (até 11) ======
st.header("Legs (até 11)")
st.caption("Introduz **TC (°T)**, **TAS (kt)** e **Dist (nm)**; vento/var na sidebar (ou sobrepõe por leg). OAT serve para consumo automático (quando ligado às tabelas).")

DEFAULT_ROWS = [
    {"Name":"", "Ident":"", "Alt/FL":"", "Freq":"",
     "TC_deg":0.0, "TAS_kt":0.0, "Dist_nm":0.0,
     "WindFROM_deg":default_wdir, "Wind_kt":default_wspd, "OAT_C":15.0,
     "WCA_deg":"", "TH_deg":"", "MH_deg":"", "GS_kt":"", "ETE_min":"",
     "PL_B/O_L":"", "EFOB_L":""}
    for _ in range(11)
]
if "legs" not in st.session_state:
    st.session_state.legs = DEFAULT_ROWS

legs = st.data_editor(
    st.session_state.legs,
    num_rows="fixed",
    use_container_width=True,
    column_config={
        "Name": st.column_config.TextColumn("Name / Lat,Long"),
        "Ident": st.column_config.TextColumn("Ident"),
        "Alt/FL": st.column_config.TextColumn("Alt / FL"),
        "Freq": st.column_config.TextColumn("Freq."),
        "TC_deg": st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
        "TAS_kt": st.column_config.NumberColumn("TAS (kt)", step=1.0, min_value=0.0),
        "Dist_nm": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
        "WindFROM_deg": st.column_config.NumberColumn("Wind FROM (°T)", step=1.0, min_value=0.0, max_value=360.0),
        "Wind_kt": st.column_config.NumberColumn("Wind (kt)", step=1.0, min_value=0.0),
        "OAT_C": st.column_config.NumberColumn("OAT (°C)", step=0.5, min_value=-60.0, max_value=60.0),
        "WCA_deg": st.column_config.TextColumn("WCA (°)"),
        "TH_deg": st.column_config.TextColumn("TH (°T)"),
        "MH_deg": st.column_config.TextColumn("MH (°M)"),
        "GS_kt": st.column_config.TextColumn("GS (kt)"),
        "ETE_min": st.column_config.TextColumn("ETE (min)"),
        "PL_B/O_L": st.column_config.TextColumn("PL B/O (L)"),
        "EFOB_L": st.column_config.TextColumn("EFOB (L)"),
    },
    hide_index=True,
    key="legs_editor"
)

# ====== Cálculos básicos (ETO/ETE/Burn/EFOB) ======
st.header("Cálculos")
c13, c14, c15 = st.columns(3)
with c13:
    start_fuel_l = st.number_input("Fuel inicial (EFOB_START) [L]", min_value=0.0, value=0.0, step=1.0)
with c14:
    auto_compute_eto = st.checkbox("Calcular ETO (ETD + ETE)", value=True)
with c15:
    compute_efob = st.checkbox("Calcular EFOB cumulativo", value=True)

total_dist = 0.0
total_ete_min = 0.0
total_pl_bo = 0.0
efob_running = start_fuel_l

etd_time = parse_hhmm(etd_str)
curr_time_for_eto = etd_time

calc_rows = []
for i, r in enumerate(legs, start=1):
    tc   = float(r.get("TC_deg") or 0.0)
    tas  = float(r.get("TAS_kt") or 0.0)
    dist = float(r.get("Dist_nm") or 0.0)
    wdir = float(r.get("WindFROM_deg") or default_wdir)
    wspd = float(r.get("Wind_kt") or default_wspd)
    oat  = float(r.get("OAT_C") or 15.0)

    wca, th, gs = wind_triangle(tc, tas, wdir, wspd)
    mh = apply_variation(th, var_deg, var_is_east)
    ete = (60.0 * dist / gs) if gs > 0 else 0.0

    # consumo
    if ff_mode.startswith("Manual"):
        ff = default_ff_lph
    else:
        # Placeholder: quando ligares às tabelas de cruzeiro, substitui por lookup (PA/RPM + correções OAT)
        ff = 20.0
    burn = ff * (ete / 60.0)

    # ETO (planeado)
    if auto_compute_eto and curr_time_for_eto:
        curr_time_for_eto = add_minutes_to_time(curr_time_for_eto, int(round(ete)))
        eto_str = curr_time_for_eto.strftime("%H:%M")
    else:
        eto_str = str(r.get("ETO(HH:MM)", "")).strip()

    total_dist += dist
    total_ete_min += ete
    total_pl_bo += burn
    if compute_efob:
        efob_running = max(0.0, efob_running - burn)
        efob_val = efob_running
    else:
        try:
            efob_val = float(str(r.get("EFOB_L","")).strip() or 0.0)
        except Exception:
            efob_val = 0.0

    calc_rows.append({
        **r,
        "WCA_deg": f"{wca:+.1f}",
        "TH_deg":  f"{th:06.2f}",
        "MH_deg":  f"{mh:06.2f}",
        "GS_kt":   f"{gs:.0f}",
        "ETE_min": f"{ete:.0f}",
        "PL_B/O_L": f"{burn:.1f}",
        "EFOB_L":  f"{efob_val:.1f}",
        "ETO(HH:MM)": eto_str
    })

# atualização para exportação
st.session_state.legs = calc_rows

st.markdown(f"**Totais:** Dist {total_dist:.1f} nm • ETE {int(total_ete_min)//60}h{int(total_ete_min)%60:02d} • Burn {total_pl_bo:.1f} L")

# ====== Observações ======
st.header("Observations")
observations = st.text_area("OBSERVATIONS", "", height=120)

# ====== PDF export ======
st.header("PDF export")
safe_reg = ascii_safe(registration or "REG")
safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
filename = f"{safe_date}_{safe_reg}_NAVLOG.pdf"

try:
    template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
    fieldset = get_field_names(template_bytes)

    named_map: Dict[str, str] = {}

    # Cabeçalho – tenta múltiplas chaves comuns
    put_any(named_map, fieldset, ["Aircraft","Aircraf","Aircraft_Type"], aircraft)
    put_any(named_map, fieldset, ["Registration","REG"], registration)
    put_any(named_map, fieldset, ["Callsign","CALLSIGN"], callsign)
    put_any(named_map, fieldset, ["Lesson","LESSON"], lesson)
    put_any(named_map, fieldset, ["Instrutor","INSTRUCTOR"], instrutor)
    put_any(named_map, fieldset, ["Student","STUDENT"], student)
    put_any(named_map, fieldset, ["Logbook","LOGBOOK"], logbook)
    put_any(named_map, fieldset, ["GRADING","Grading"], grading)
    put_any(named_map, fieldset, ["Dept_Airfield","Departure","Dept"], dept_airfield)
    put_any(named_map, fieldset, ["Arrival_Airfield","Arrival"], arr_airfield)
    put_any(named_map, fieldset, ["Leg_Number","LegNumber"], leg_number)
    put_any(named_map, fieldset, ["Alternate","Alternate_Airfield"], alternate_airfield)

    put_any(named_map, fieldset, ["ETD/ETA","ETD_ETA"], f"{etd_str} / {eta_str}")
    put_any(named_map, fieldset, "Startup", startup)
    put_any(named_map, fieldset, "Takeoff", takeoff)
    put_any(named_map, fieldset, "Landing", landing)
    put_any(named_map, fieldset, "Shutdown", shutdown)

    put_any(named_map, fieldset, ["Level F/F","Level_FF"], level_ff)
    put_any(named_map, fieldset, ["Climb_Fuel","ClimbFuel"], climb_fuel)
    put_any(named_map, fieldset, "QNH", qnh)
    put_any(named_map, fieldset, "Clearances", clearances)
    put_any(named_map, fieldset, ["Dept_Comm","Departure_Comms"], dept_comm)
    put_any(named_map, fieldset, ["Enroute_comm","Enroute_Comms"], enroute_comm)
    put_any(named_map, fieldset, ["Arrival_comm","Arrival_Comms"], arrival_comm)

    put_any(named_map, fieldset, ["flt_lvl_altitude","FL_Alt"], flt_lvl_alt)
    put_any(named_map, fieldset, ["wind","Wind_Info"], wind_info)
    put_any(named_map, fieldset, ["mag_var","MagVar"], mag_var)
    put_any(named_map, fieldset, ["temp_isa_dev","Temp_ISA"], temp_isa_dev)

    # Observações
    put_any(named_map, fieldset, "OBSERVATIONS", observations)

    # Legs 1..11 — nomes típicos do template NAVLOG
    # Namei, Alti, TCRSi, THDGi, MHDGi, GSi, Disti, ETEi, ETOi, PL_BOi, EFOBi, FREQi
    for i, r in enumerate(calc_rows, start=1):
        suf = str(i)
        put_any(named_map, fieldset, [f"Name{suf}","Name_"+suf], str(r.get("Name","")))
        put_any(named_map, fieldset, [f"Alt{suf}","Alt_"+suf], str(r.get("Alt/FL","")))
        put_any(named_map, fieldset, [f"TCRS{suf}","TCRS_"+suf], str(r.get("TC_deg","")))
        put_any(named_map, fieldset, [f"THDG{suf}","THDG_"+suf], str(r.get("TH_deg","")))
        put_any(named_map, fieldset, [f"MHDG{suf}","MHDG_"+suf], str(r.get("MH_deg","")))
        put_any(named_map, fieldset, [f"GS{suf}","GS_"+suf],   str(r.get("GS_kt","")))
        put_any(named_map, fieldset, [f"Dist{suf}","Dist_"+suf], str(r.get("Dist_nm","")))
        put_any(named_map, fieldset, [f"ETE{suf}","ETE_"+suf],  str(r.get("ETE_min","")))
        put_any(named_map, fieldset, [f"ETO{suf}","ETO_"+suf],  str(r.get("ETO(HH:MM)","")))
        put_any(named_map, fieldset, [f"PL_BO{suf}","PL_BO_"+suf], str(r.get("PL_B/O_L","")))
        put_any(named_map, fieldset, [f"EFOB{suf}","EFOB_"+suf], str(r.get("EFOB_L","")))
        put_any(named_map, fieldset, [f"FREQ{suf}","FREQ_"+suf], str(r.get("Freq","")))

    # Totais (se existirem no template)
    put_any(named_map, fieldset, ["ETE_Total","ETE_TOTAL"], str(int(round(total_ete_min))))
    put_any(named_map, fieldset, ["Dist_Total","DIST_TOTAL"], f"{total_dist:.1f}")
    put_any(named_map, fieldset, ["PL_BO_TOTAL","PLBO_TOTAL"], f"{total_pl_bo:.1f}")
    put_any(named_map, fieldset, ["EFOB_TOTAL","EFOB_TOTAL_"], f"{efob_running:.1f}")

    if st.button("Gerar PDF preenchido", type="primary"):
        try:
            out_bytes = fill_pdf(template_bytes, named_map)
            st.download_button("Download PDF", data=out_bytes, file_name=filename, mime="application/pdf")
            st.success("PDF gerado. Revê antes do voo.")
        except Exception as e:
            st.error(f"Erro ao gerar PDF: {e}")

except Exception as e:
    st.error(f"Não foi possível preparar o mapeamento do PDF: {e}\n"
             "Confere se o ficheiro existe em /mnt/data ou no diretório atual.")

