
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
        writer._root_object["/AcroForm"].update({
            NameObject("/NeedAppearances"): True,
            NameObject("/DA"): "/Helv 0 Tf 0 g"
        })
    except Exception:
        pass
    # garantes que tudo é string antes de escrever
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

def minutes_to_hhmm(m: float) -> str:
    m = max(0, int(round(float(m or 0))))
    h, mm = divmod(m, 60)
    return f"{h:02d}:{mm:02d}"

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

# ============ AFM cruise corrections (tabela de correções) ============
# A partir da página de Cruise Performance (que enviaste):
#  +15 °C OAT  → TAS −2%, Fuel −2.5%
#  −15 °C OAT  → TAS +1%, Fuel +3%
def apply_oat_corrections(tas_base, ff_base, oat_dev_c):
    # oat_dev_c: diferença para ISA/OAT de referência do quadro (aproximação)
    tas = tas_base
    ff  = ff_base
    if oat_dev_c > 0:
        tas *= 1.0 - 0.02 * (oat_dev_c / 15.0)
        ff  *= 1.0 - 0.025 * (oat_dev_c / 15.0)
    elif oat_dev_c < 0:
        tas *= 1.0 + 0.01 * (abs(oat_dev_c) / 15.0)
        ff  *= 1.0 + 0.03 * (abs(oat_dev_c) / 15.0)
    return tas, ff

# ============ App ============
st.set_page_config(page_title="NAVLOG – Navigation Plan & Inflight Log", layout="wide", initial_sidebar_state="collapsed")
st.title("Navigation Plan & Inflight Log – PDF Filler")

# Caminho do template
PDF_TEMPLATE_PATHS = [
    "/mnt/data/NAVLOG - FORM.pdf",
    "NAVLOG - FORM.pdf",
]

# Defaults pedidos
DEFAULT_STUDENT  = "A. Moiteiro"
DEFAULT_AIRCRAFT = "Tecnam P2008"
SEVENAIR_P2008_REGS = ["CS-ECC", "CS-ECD", "CS-DHS", "CS-DHT", "CS-DHU", "CS-DHV", "CS-DHW"]

# Frequências por aeródromo (AIP/PT AIS + FIS)
FREQ_DB = {
    "LPSO": ["119.805 (Ponte de Sor INFO)", "123.755 (Lisboa Information)"],   # AIP AD 2.18
    "LPEV": ["122.705 (Évora INFO)", "123.755 (Lisboa Information)", "131.055 (Lisboa Information)"],  # AIP AD 2.18
    "LPCB": ["130.905 (Lisboa Information)", "132.305 (Lisboa Control HN)", "123.755 (Lisboa Information)"],  # AIP eVFR
    "LPCO": ["130.905 (Lisboa Information)", "132.305 (Lisboa Control HN)"],   # AIP eVFR
    "LPVZ": ["130.905 (Lisboa Information)", "132.305 (Lisboa Control HN)"],   # AIP eVFR
}

# ====== Cabeçalho (apenas campos que existem no NAVLOG típico) ======
st.subheader("Cabeçalho")
c1, c2, c3 = st.columns([1,1,1])
with c1:
    aircraft = st.text_input("Aircraft", DEFAULT_AIRCRAFT)
    registration = st.selectbox("Registration", SEVENAIR_P2008_REGS, index=0)
    callsign = st.text_input("Callsign", "")
with c2:
    student = st.text_input("Student", DEFAULT_STUDENT)
    dept_airfield = st.text_input("Departure (ICAO)", "LPSO")
    arr_airfield = st.selectbox("Arrival (ICAO)", ["", "LPSO","LPEV","LPCB","LPCO","LPVZ"], index=1)
with c3:
    alternate_airfield = st.selectbox("Alternate (ICAO)", ["", "LPSO","LPEV","LPCB","LPCO","LPVZ"], index=2)
    etd_str = st.text_input("ETD (HH:MM)", "")
    eta_str = st.text_input("ETA (HH:MM) (opcional)", "")

# Comms default
def default_comms_for(aero: str) -> str:
    if not aero: return ""
    freqs = FREQ_DB.get(aero.upper(), [])
    return " / ".join(freqs)

dept_comm = st.text_input("Departure Comms/Freq", default_comms_for(dept_airfield))
enroute_comm = st.text_input("Enroute Comms/Freq", "Lisboa Information 123.755")
arrival_comm = st.text_input("Arrival Comms/Freq", default_comms_for(arr_airfield))

# ====== Variação e Vento globais (sem sidebar) ======
st.subheader("Condições (var/vento globais; podes sobrepor por leg)")
colv, colw1, colw2 = st.columns([1,1,1])
with colv:
    var_deg = st.number_input("Magnetic variation (°)", min_value=0.0, max_value=30.0, value=1.0, step=0.1)
    var_is_east = st.selectbox("Var E/W", ["W", "E"], index=0) == "E"  # por defeito 1W
with colw1:
    default_wdir = st.number_input("Wind direction FROM (°TRUE)", min_value=0.0, max_value=360.0, value=0.0, step=1.0)
with colw2:
    default_wspd = st.number_input("Wind speed (kt)", min_value=0.0, max_value=200.0, value=0.0, step=1.0)

# ====== Legs (inputs compactos) ======
st.subheader("Legs — Entradas")
st.caption("Preenche Name/Ident/Alt/Freq, **TC (°T)**, **TAS (kt)** e **Dist (nm)**. Podes sobrepor vento/OAT por leg; caso contrário usa os valores globais.")
N = st.number_input("Nº de legs (1–11)", min_value=1, max_value=11, value=6, step=1)

if "legs_in" not in st.session_state:
    st.session_state.legs_in = [
        {"Name":"", "Ident":"", "Alt/FL":"", "Freq":"",
         "TC":0.0, "TAS":0.0, "Dist":0.0,
         "WindFROM":default_wdir, "Wind":default_wspd, "OAT":15.0}
        for _ in range(int(N))
    ]

# ajustar tamanho se N mudar
cur = st.session_state.legs_in
if len(cur) != int(N):
    if len(cur) < int(N):
        cur += [{"Name":"", "Ident":"", "Alt/FL":"", "Freq":"",
                 "TC":0.0, "TAS":0.0, "Dist":0.0,
                 "WindFROM":default_wdir, "Wind":default_wspd, "OAT":15.0}
                for _ in range(int(N)-len(cur))]
    else:
        st.session_state.legs_in = cur[:int(N)]

legs_in = st.data_editor(
    st.session_state.legs_in,
    num_rows="fixed",
    use_container_width=True,
    column_config={
        "Name": st.column_config.TextColumn("Name / Lat,Long"),
        "Ident": st.column_config.TextColumn("Ident"),
        "Alt/FL": st.column_config.TextColumn("Alt / FL"),
        "Freq": st.column_config.TextColumn("Freq."),
        "TC": st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
        "TAS": st.column_config.NumberColumn("TAS (kt) (0=auto*)", step=1.0, min_value=0.0),
        "Dist": st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
        "WindFROM": st.column_config.NumberColumn("Wind FROM (°T)", step=1.0, min_value=0.0, max_value=360.0),
        "Wind": st.column_config.NumberColumn("Wind (kt)", step=1.0, min_value=0.0),
        "OAT": st.column_config.NumberColumn("OAT (°C)", step=0.5, min_value=-60.0, max_value=60.0),
    },
    hide_index=True,
    key="legs_editor_inputs"
)

# ====== Cálculos ======
st.subheader("Resultados por leg")
start_fuel_l = st.number_input("Fuel inicial (EFOB_START) [L]", min_value=0.0, value=0.0, step=1.0)
auto_compute_eto = st.checkbox("Calcular ETO (ETD + ETE)", value=True)
compute_efob = st.checkbox("Calcular EFOB cumulativo", value=True)
etd_time = parse_hhmm(etd_str)
t_clock = etd_time

# Base (aprox.) para FF/TAS se TAS=0 → usamos “tabela” (valores típicos 2000RPM SL) + correções AFM por OAT
BASE_TAS = 95.0   # kt @ 2000 RPM, SL (aprox do quadro)
BASE_FF  = 18.7   # L/h @ 2000 RPM, SL (aprox do quadro)

def isa_at(pa_ft: float) -> float:
    # ISA: 15°C - 2°C/1000ft * altitude pressão (aprox linear baixa altitude)
    return 15.0 - 2.0 * (float(pa_ft)/1000.0)

total_dist = total_ete = total_burn = 0.0
efob = start_fuel_l
legs_out = []

for i, r in enumerate(legs_in, start=1):
    name = r.get("Name","")
    ident = r.get("Ident","")
    altfl = r.get("Alt/FL","")
    freq = r.get("Freq","")

    tc   = float(r.get("TC") or 0.0)
    tas  = float(r.get("TAS") or 0.0)
    dist = float(r.get("Dist") or 0.0)
    wdir = float(r.get("WindFROM") or default_wdir)
    wspd = float(r.get("Wind") or default_wspd)
    oat  = float(r.get("OAT") or 15.0)

    # Se TAS=0 → calcula automaticamente com base + correção OAT (tabela de correções do AFM)
    pa_ft = 0.0  # por agora assumimos PA≈0 se não fornecido
    if tas <= 0:
        tas_auto, ff_auto = apply_oat_corrections(BASE_TAS, BASE_FF, oat - isa_at(pa_ft))
        tas = tas_auto
        ff_lph = ff_auto
    else:
        # Se o utilizador fornece TAS, FF base ajusta por OAT
        _, ff_lph = apply_oat_corrections(BASE_TAS, BASE_FF, oat - isa_at(pa_ft))

    wca, th, gs = wind_triangle(tc, tas, wdir, wspd)
    mh = apply_variation(th, var_deg, var_is_east)
    ete_min = (60.0 * dist / gs) if gs > 0 else 0.0
    burn = ff_lph * (ete_min/60.0)

    eto_str = ""
    if auto_compute_eto and t_clock:
        t_clock = add_minutes_to_time(t_clock, int(round(ete_min)))
        eto_str = t_clock.strftime("%H:%M")

    total_dist += dist
    total_ete += ete_min
    total_burn += burn
    if compute_efob:
        efob = max(0.0, efob - burn)

    legs_out.append({
        "Name": name, "Ident": ident, "Alt/FL": altfl, "Freq": freq,
        "TC": f"{tc:.1f}", "TAS": f"{tas:.0f}", "Dist": f"{dist:.1f}",
        "TH": f"{th:06.2f}", "MH": f"{mh:06.2f}", "WCA": f"{wca:+.1f}",
        "GS": f"{gs:.0f}", "ETE(min)": f"{ete_min:.0f}", "ETO": eto_str,
        "PL_B/O (L)": f"{burn:.1f}", "EFOB (L)": f"{efob:.1f}"
    })

st.dataframe(legs_out, use_container_width=True)

st.markdown(f"**Totais** — Dist: {total_dist:.1f} nm • ETE: {int(total_ete)//60}h{int(total_ete)%60:02d} • Burn: {total_burn:.1f} L • EFOB final: {efob:.1f} L")

# ====== PDF export (apenas campos que existem no template) ======
st.subheader("PDF export")
safe_reg = ascii_safe(registration or "REG")
safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
filename = f"{safe_date}_{safe_reg}_NAVLOG.pdf"

try:
    template_bytes = read_pdf_bytes(PDF_TEMPLATE_PATHS)
    fieldset = get_field_names(template_bytes)

    named_map: Dict[str, str] = {}

    # Cabeçalho – só escrevemos se existir
    put_any(named_map, fieldset, ["Aircraft","Aircraf","Aircraft_Type"], aircraft)
    put_any(named_map, fieldset, ["Registration","REG"], registration)
    put_any(named_map, fieldset, ["Callsign","CALLSIGN"], callsign)
    put_any(named_map, fieldset, ["Student","STUDENT"], student)
    put_any(named_map, fieldset, ["Dept_Airfield","Departure","Dept"], dept_airfield)
    put_any(named_map, fieldset, ["Arrival_Airfield","Arrival"], arr_airfield)
    put_any(named_map, fieldset, ["Alternate","Alternate_Airfield"], alternate_airfield)
    put_any(named_map, fieldset, ["ETD/ETA","ETD_ETA"], f"{etd_str} / {eta_str}")
    put_any(named_map, fieldset, ["Dept_Comm","Departure_Comms"], dept_comm)
    put_any(named_map, fieldset, ["Enroute_comm","Enroute_Comms"], enroute_comm)
    put_any(named_map, fieldset, ["Arrival_comm","Arrival_Comms"], arrival_comm)

    # Legs 1..11 — nomes típicos do NAVLOG
    # Namei, Alti, TCRSi, THDGi, MHDGi, GSi, Disti, ETEi, ETOi, PL_BOi, EFOBi, FREQi
    for i, r in enumerate(legs_out, start=1):
        suf = str(i)
        put_any(named_map, fieldset, [f"Name{suf}","Name_"+suf], r["Name"])
        put_any(named_map, fieldset, [f"Alt{suf}","Alt_"+suf], r["Alt/FL"])
        put_any(named_map, fieldset, [f"TCRS{suf}","TCRS_"+suf], r["TC"])
        put_any(named_map, fieldset, [f"THDG{suf}","THDG_"+suf], r["TH"])
        put_any(named_map, fieldset, [f"MHDG{suf}","MHDG_"+suf], r["MH"])
        put_any(named_map, fieldset, [f"GS{suf}","GS_"+suf],   r["GS"])
        put_any(named_map, fieldset, [f"Dist{suf}","Dist_"+suf], r["Dist"])
        put_any(named_map, fieldset, [f"ETE{suf}","ETE_"+suf],  r["ETE(min)"])
        put_any(named_map, fieldset, [f"ETO{suf}","ETO_"+suf],  r["ETO"])
        put_any(named_map, fieldset, [f"PL_BO{suf}","PL_BO_"+suf], r["PL_B/O (L)"])
        put_any(named_map, fieldset, [f"EFOB{suf}","EFOB_"+suf], r["EFOB (L)"])
        put_any(named_map, fieldset, [f"FREQ{suf}","FREQ_"+suf], legs_in[i-1].get("Freq",""))

    # Totais (se existirem no template)
    put_any(named_map, fieldset, ["ETE_Total","ETE_TOTAL"], str(int(round(total_ete))))
    put_any(named_map, fieldset, ["Dist_Total","DIST_TOTAL"], f"{total_dist:.1f}")
    put_any(named_map, fieldset, ["PL_BO_TOTAL","PLBO_TOTAL"], f"{total_burn:.1f}")
    put_any(named_map, fieldset, ["EFOB_TOTAL","EFOB_TOTAL_"], f"{efob:.1f}")

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
