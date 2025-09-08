
# app.py — NAVLOG PDF Filler + Cálculos (TC→TH/MH, GS, ETE, Burn via tabelas)
# Reqs: streamlit, pytz, pypdf

import streamlit as st
import datetime as dt
import pytz
from pathlib import Path
import io
import unicodedata
from typing import List, Dict, Optional
from math import sin, cos, asin, radians, degrees, fmod

# ===================== PDF helpers =====================
try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, BooleanObject, createStringObject
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
            NameObject("/NeedAppearances"): BooleanObject(True),
            NameObject("/DA"): createStringObject("/Helv 0 Tf 0 g"),
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

# ===================== Tempo =====================
def parse_hhmm(s: str) -> Optional[dt.time]:
    s = (s or "").strip()
    if not s: return None
    for fmt in ("%H:%M", "%H%M"):
        try:
            return dt.datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    return None

def minutes_to_hhmm(m: int) -> str:
    m = max(0, int(round(m)))
    h, mm = divmod(m, 60)
    return f"{h:02d}:{mm:02d}"

def add_minutes_to_time(t: dt.time, minutes: int, tzinfo=pytz.timezone("Europe/Lisbon")) -> dt.time:
    today = dt.date.today()
    base = tzinfo.localize(dt.datetime.combine(today, t))
    return (base + dt.timedelta(minutes=minutes)).timetz().replace(tzinfo=None)

# ===================== Triângulo do vento =====================
def wrap360(x):
    x = fmod(x, 360.0)
    return x + 360.0 if x < 0 else x

def angle_diff(a, b):
    return (a - b + 180.0) % 360.0 - 180.0

def wind_triangle(true_course_deg, tas_kt, wind_dir_from_deg, wind_kt):
    """
    Output: (WCA_deg, TH_deg, GS_kt)
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

# ===================== Tabelas de Cruzeiro (650 kg do AFM) =====================
# Cada nível de PA (ft) tem entradas por RPM: (KTAS, Fuel L/h)
CRUISE_650 = {
    0:    {2250:(118,26.9), 2100:(101,20.7), 2000:(95,18.7), 1900:(89,17.0), 1800:(83,15.6)},
    2000: {2250:(110,24.6), 2100:(100,19.2), 2000:(94,17.5), 1900:(88,16.2), 1800:(82,15.1)},
    4000: {2250:(108,22.4), 2100:(100,19.2), 2000:(94,17.5), 1900:(88,16.2), 1800:(82,15.1)},
    6000: {2250:(106,21.3), 2100:(99,18.5), 2000:(93,17.1), 1900:(87,15.9), 1800:(81,14.9)},
    8000: {2250:(107,20.4), 2100:(98,18.0), 2000:(92,16.7), 1900:(86,15.6), 1800:(80,15.4)},
    10000:{2250:(106,19.7), 2100:(97,17.5), 2000:(91,16.4), 1900:(85,15.4)},  # 1800 ausente; omitido
}
CRUISE_RPMS = sorted({rpm for d in CRUISE_650.values() for rpm in d.keys()})
CRUISE_PAS  = sorted(CRUISE_650.keys())

def interp1(x, x0, x1, y0, y1):
    if x1 == x0: return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def cruise_lookup(pa_ft: float, rpm: float):
    """Interpola KTAS e L/h por PA e RPM (tabela 650 kg)."""
    # PA
    pas = CRUISE_PAS
    pa_c = max(pas[0], min(pas[-1], pa_ft))
    p0 = max([p for p in pas if p <= pa_c])
    p1 = min([p for p in pas if p >= pa_c])
    # RPM (seleciona vizinhos existentes em ambos p0 e p1)
    rpms0 = sorted(CRUISE_650[p0].keys())
    rpms1 = sorted(CRUISE_650[p1].keys())
    rpms = sorted(set(rpms0).intersection(rpms1))
    rpm_c = max(rpms[0], min(rpms[-1], rpm))
    r0 = max([r for r in rpms if r <= rpm_c])
    r1 = min([r for r in rpms if r >= rpm_c])

    def val(pa, r):
        return CRUISE_650[pa][r]

    # interpola em RPM dentro de cada PA
    tas_p0 = interp1(rpm_c, r0, r1, val(p0,r0)[0], val(p0,r1)[0])
    ff_p0  = interp1(rpm_c, r0, r1, val(p0,r0)[1], val(p0,r1)[1])
    tas_p1 = interp1(rpm_c, r0, r1, val(p1,r0)[0], val(p1,r1)[0])
    ff_p1  = interp1(rpm_c, r0, r1, val(p1,r0)[1], val(p1,r1)[1])

    # interpola entre PAs
    tas = interp1(pa_c, p0, p1, tas_p0, tas_p1)
    ff  = interp1(pa_c, p0, p1, ff_p0,  ff_p1)
    return tas, ff

def isa_at(pa_ft: float):
    return 15.0 - 2.0*(pa_ft/1000.0)  # aproximação ISA

def cruise_oat_corrections(tas_kt: float, ff_lph: float, pa_ft: float, oat_c: float):
    """
    AFM (650 kg): KTAS: −2% por +15°C ; +1% por −15°C
                   FF: −2.5% por +15°C ; +3% por −15°C
    Fazemos linear por deg e assimétrico.
    """
    isa = isa_at(pa_ft)
    d = oat_c - isa
    # KTAS
    if d >= 0:
        tas_corr = tas_kt * (1.0 - 0.02*(d/15.0))
        ff_corr  = ff_lph * (1.0 - 0.025*(d/15.0))
    else:
        tas_corr = tas_kt * (1.0 + 0.01*(-d/15.0))
        ff_corr  = ff_lph * (1.0 + 0.03*(-d/15.0))
    return max(0.0, tas_corr), max(0.0, ff_corr)

# ===================== Defaults (frequências e aeródromos) =====================
# Fonte: eAIP Portugal (AIS NAV Portugal)
FREQS = {
    "LPSO": "PONTE DE SOR INFORMATION 119.805",
    "LPEV": "ÉVORA INFORMATION 122.705",
    "LPCO": "COIMBRA INFORMATION 122.905",
    "LPCB": "CASTELO BRANCO INFORMATION 122.555",
    "LPVZ": "VISEU INFORMATION 122.710",
    "ENR":  "LISBOA INFORMATION 123.755",
}

# ===================== App =====================
st.set_page_config(page_title="NAVLOG – Navigation Plan & Inflight Log", layout="wide", initial_sidebar_state="collapsed")
st.title("Navigation Plan & Inflight Log – PDF Filler")

# Carrega template e nomes de campos (para só mostrar aquilo que existe no PDF)
PDF_TEMPLATE_PATHS = ["/mnt/data/NAVLOG - FORM.pdf", "NAVLOG - FORM.pdf"]
try:
    TEMPLATE_BYTES = read_pdf_bytes(PDF_TEMPLATE_PATHS)
    FIELDSET = get_field_names(TEMPLATE_BYTES)
except Exception as e:
    TEMPLATE_BYTES = None
    FIELDSET = set()
    st.error(f"Não foi possível ler o template do PDF: {e}")

# ===== Cabeçalho (apenas campos que existem no PDF) =====
st.header("Cabeçalho do voo")

def field_exists(name: str) -> bool:
    return name in FIELDSET

# Defaults pedidos
DEFAULT_STUDENT = "A. Moiteiro"   # és aluno
DEFAULT_AIRCRAFT = "Tecnam P2008"
DEFAULT_REG = "CS-ECC"            # podes mudar
DEFAULT_DEPT = "LPSO"
DEFAULT_ARR  = "LPEV"

# Alguns templates usam variações de nomes — vamos suportar os mais prováveis
def txt(label, keys, default=""):
    # só mostra se algum dos nomes existe
    names = [k for k in (keys if isinstance(keys, list) else [keys]) if field_exists(k)]
    if not names: 
        return ""  # não mostrar campo que não existe no PDF
    return st.text_input(label, default)

aircraft     = txt("Aircraft", ["Aircraft","Aircraf","Aircraft_Type"], DEFAULT_AIRCRAFT)
registration = txt("Registration", ["Registration","REG"], DEFAULT_REG)
callsign     = txt("Callsign", ["Callsign","CALLSIGN"], "")
lesson       = txt("Lesson", ["Lesson","LESSON"], "")
student      = txt("Student", ["Student","STUDENT"], DEFAULT_STUDENT)
logbook      = txt("Logbook", ["Logbook","LOGBOOK"], "")
grading      = txt("Grading", ["GRADING","Grading"], "")

dept_airfield = txt("Departure Airfield (ICAO)", ["Dept_Airfield","Departure","Dept"], DEFAULT_DEPT)
arr_airfield  = txt("Arrival Airfield (ICAO)",  ["Arrival_Airfield","Arrival"], DEFAULT_ARR)
leg_number    = txt("Leg Number", ["Leg_Number","LegNumber"], "")
alternate     = txt("Alternate (ICAO)", ["Alternate","Alternate_Airfield"], "LPCO")

etd_str  = txt("ETD (HH:MM)", "ETD/ETA", "").split("/")[0].strip() if field_exists("ETD/ETA") else st.text_input("ETD (HH:MM)", "")
eta_str  = ""  # ETA opcional (não forçamos campo se não existir)
startup  = txt("Startup (HH:MM)", "Startup", "")
takeoff  = txt("Takeoff (HH:MM)", "Takeoff", "")
landing  = txt("Landing (HH:MM)", "Landing", "")
shutdown = txt("Shutdown (HH:MM)", "Shutdown", "")

# Comms – valores por defeito pedidos
st.subheader("Comms")
dept_comm    = txt("Departure Comms/Freq", ["Dept_Comm","Departure_Comms"], FREQS.get(dept_airfield or "LPSO", FREQS["LPSO"]))
enroute_comm = txt("Enroute Comms/Freq", ["Enroute_comm","Enroute_Comms"], FREQS["ENR"])
arrival_comm = txt("Arrival Comms/Freq", ["Arrival_comm","Arrival_Comms"], FREQS.get(arr_airfield or "LPEV", FREQS["LPEV"]))

# ===== Secção de Cálculo (não exporta para o PDF) =====
st.header("Cálculo (não exporta para o PDF)")
colA, colB, colC, colD, colE = st.columns(5)
with colA:
    wind_from_deg = st.number_input("Wind FROM (°TRUE)", 0.0, 360.0, 0.0, 1.0)
with colB:
    wind_kt = st.number_input("Wind (kt)", 0.0, 200.0, 0.0, 1.0)
with colC:
    variation_deg = st.number_input("Magnetic variation (°)", 0.0, 30.0, 1.0, 0.1)  # default 1
with colD:
    var_is_east = st.selectbox("Variation East / West", ["West (+)", "East (−)"], index=0) == "East (−)"  # default 1W
with colE:
    start_fuel_l = st.number_input("Fuel inicial (EFOB_START) [L]", min_value=0.0, value=0.0, step=1.0)

colF, colG = st.columns(2)
with colF:
    cruise_pa_ft = st.number_input("Cruise Pressure Altitude (ft)", 0.0, 12000.0, 4000.0, 100.0)
with colG:
    cruise_rpm = st.number_input("Cruise RPM", 1800.0, 2250.0, 2000.0, 10.0)

oat_global = st.number_input("OAT (°C)", -50.0, 60.0, 15.0, 0.5)
auto_compute_eto = st.checkbox("Calcular ETO (ETD + ETE)", value=True)
compute_efob     = st.checkbox("Calcular EFOB cumulativo", value=True)

# ===== Legs (apenas campos que existem no PDF) =====
st.header("Legs (1–11)")

# Vamos construir uma tabela compacta apenas com campos do formulário
# Campos típicos do template: Namei, TCRSi, THDGi, MHDGi, GSi, Disti, ETEi, ETOi, PL_BOi, EFOBi, FREQi
# Inputs do utilizador (mínimos): Name, Dist, TC, TAS (opcional; se vazio, usamos tabela), FREQ (opcional)
ROWS = []
for i in range(1, 12):
    st.markdown(f"**Leg {i}**")
    c1, c2, c3, c4, c5 = st.columns([2,1,1,1,2])
    with c1:
        name = st.text_input("Name / Ident", key=f"name{i}", value="")
    with c2:
        dist = st.number_input("Dist (nm)", min_value=0.0, value=0.0, step=0.1, key=f"dist{i}")
    with c3:
        tc = st.number_input("TC (°TRUE)", min_value=0.0, max_value=359.9, value=0.0, step=0.1, key=f"tc{i}")
    with c4:
        tas_user = st.number_input("TAS (kt) (deixa 0 p/ auto)", min_value=0.0, value=0.0, step=1.0, key=f"tas{i}")
    with c5:
        freq = st.text_input("FREQ (opcional)", key=f"freq{i}", value="")

    # TAS/FF via tabela se não fornecido
    if tas_user > 0:
        tas_eff = tas_user
        ff_eff = cruise_lookup(cruise_pa_ft, cruise_rpm)[1]
        tas_eff, ff_eff = cruise_oat_corrections(tas_eff, ff_eff, cruise_pa_ft, oat_global)
    else:
        tas_base, ff_base = cruise_lookup(cruise_pa_ft, cruise_rpm)
        tas_eff, ff_eff = cruise_oat_corrections(tas_base, ff_base, cruise_pa_ft, oat_global)

    wca, th, gs = wind_triangle(tc, tas_eff, wind_from_deg, wind_kt)
    mh = apply_variation(th, variation_deg, var_is_east)

    ete = (60.0 * dist / gs) if gs > 0 else 0.0
    burn = ff_eff * (ete/60.0)

    # Mostrar resultados do leg (compacto)
    st.caption(f"WCA {wca:+.1f}° • TH {th:06.2f}° • MH {mh:06.2f}° • GS {gs:.0f} kt • ETE {int(round(ete))} min • Burn {burn:.1f} L")

    ROWS.append({
        "Name": name, "Dist": dist, "TC": tc, "TAS": tas_eff,
        "TH": th, "MH": mh, "GS": gs, "ETE": ete, "Burn": burn, "Freq": freq
    })
    st.divider()

# Totais + ETO/EFOB
total_dist = sum(r["Dist"] for r in ROWS)
total_ete  = sum(r["ETE"]  for r in ROWS)
total_burn = sum(r["Burn"] for r in ROWS)

etd_time = parse_hhmm(etd_str)
curr_time_for_eto = etd_time
for r in ROWS:
    if auto_compute_eto and curr_time_for_eto:
        curr_time_for_eto = add_minutes_to_time(curr_time_for_eto, int(round(r["ETE"])))
        r["ETO_str"] = curr_time_for_eto.strftime("%H:%M")
    else:
        r["ETO_str"] = ""

efob_running = start_fuel_l
for r in ROWS:
    if compute_efob:
        efob_running = max(0.0, efob_running - r["Burn"])
        r["EFOB"] = efob_running
    else:
        r["EFOB"] = 0.0

st.markdown(f"**Totais:** Dist {total_dist:.1f} nm • ETE {int(total_ete)//60}h{int(total_ete)%60:02d} • Burn {total_burn:.1f} L")

# ===== Observações (se existir no PDF) =====
observations = st.text_area("OBSERVATIONS", "", height=120) if field_exists("OBSERVATIONS") else ""

# ===== Export PDF =====
st.header("PDF export")

if TEMPLATE_BYTES is None:
    st.warning("Carrega o template primeiro para poderes exportar.")
else:
    named_map: Dict[str, str] = {}

    # Cabeçalho
    put_any(named_map, FIELDSET, ["Aircraft","Aircraf","Aircraft_Type"], aircraft)
    put_any(named_map, FIELDSET, ["Registration","REG"], registration)
    put_any(named_map, FIELDSET, ["Callsign","CALLSIGN"], callsign)
    put_any(named_map, FIELDSET, ["Lesson","LESSON"], lesson)
    put_any(named_map, FIELDSET, ["Student","STUDENT"], student)
    put_any(named_map, FIELDSET, ["Logbook","LOGBOOK"], logbook)
    put_any(named_map, FIELDSET, ["GRADING","Grading"], grading)
    put_any(named_map, FIELDSET, ["Dept_Airfield","Departure","Dept"], dept_airfield)
    put_any(named_map, FIELDSET, ["Arrival_Airfield","Arrival"], arr_airfield)
    put_any(named_map, FIELDSET, ["Leg_Number","LegNumber"], leg_number)
    put_any(named_map, FIELDSET, ["Alternate","Alternate_Airfield"], alternate)
    if field_exists("ETD/ETA"):
        put_any(named_map, FIELDSET, "ETD/ETA", f"{etd_str} / ")
    put_any(named_map, FIELDSET, "Startup", startup)
    put_any(named_map, FIELDSET, "Takeoff", takeoff)
    put_any(named_map, FIELDSET, "Landing", landing)
    put_any(named_map, FIELDSET, "Shutdown", shutdown)

    put_any(named_map, FIELDSET, ["Dept_Comm","Departure_Comms"], dept_comm or FREQS["LPSO"])
    put_any(named_map, FIELDSET, ["Enroute_comm","Enroute_Comms"], enroute_comm or FREQS["ENR"])
    put_any(named_map, FIELDSET, ["Arrival_comm","Arrival_Comms"], arrival_comm or FREQS["LPEV"])

    put_any(named_map, FIELDSET, "OBSERVATIONS", observations)

    # Legs → campos típicos do PDF (Namei, TCRSi, THDGi, MHDGi, GSi, Disti, ETEi, ETOi, PL_BOi, EFOBi, FREQi)
    for i, r in enumerate(ROWS, start=1):
        suf = str(i)
        put_any(named_map, FIELDSET, [f"Name{suf}","Name_"+suf], r["Name"])
        put_any(named_map, FIELDSET, [f"TCRS{suf}","TCRS_"+suf], f"{r['TC']:.1f}")
        put_any(named_map, FIELDSET, [f"THDG{suf}","THDG_"+suf], f"{r['TH']:.2f}")
        put_any(named_map, FIELDSET, [f"MHDG{suf}","MHDG_"+suf], f"{r['MH']:.2f}")
        put_any(named_map, FIELDSET, [f"GS{suf}","GS_"+suf],   f"{r['GS']:.0f}")
        put_any(named_map, FIELDSET, [f"Dist{suf}","Dist_"+suf], f"{r['Dist']:.1f}")
        put_any(named_map, FIELDSET, [f"ETE{suf}","ETE_"+suf],  f"{int(round(r['ETE']))}")
        put_any(named_map, FIELDSET, [f"ETO{suf}","ETO_"+suf],  r.get("ETO_str",""))
        put_any(named_map, FIELDSET, [f"PL_BO{suf}","PL_BO_"+suf], f"{r['Burn']:.1f}")
        put_any(named_map, FIELDSET, [f"EFOB{suf}","EFOB_"+suf], f"{r.get('EFOB',0.0):.1f}")
        put_any(named_map, FIELDSET, [f"FREQ{suf}","FREQ_"+suf], r["Freq"])

    # Totais (se existirem no template)
    put_any(named_map, FIELDSET, ["ETE_Total","ETE_TOTAL"], f"{int(round(total_ete))}")
    put_any(named_map, FIELDSET, ["Dist_Total","DIST_TOTAL"], f"{total_dist:.1f}")
    put_any(named_map, FIELDSET, ["PL_BO_TOTAL","PLBO_TOTAL"], f"{total_burn:.1f}")
    put_any(named_map, FIELDSET, ["EFOB_TOTAL","EFOB_TOTAL_"], f"{efob_running:.1f}")

    safe_reg = ascii_safe(registration or "REG")
    safe_date = dt.datetime.now(pytz.timezone("Europe/Lisbon")).strftime("%Y-%m-%d")
    filename = f"{safe_date}_{safe_reg}_NAVLOG.pdf"

    if st.button("Gerar PDF preenchido", type="primary"):
        try:
            out_bytes = fill_pdf(TEMPLATE_BYTES, named_map)
            st.download_button("Download PDF", data=out_bytes, file_name=filename, mime="application/pdf")
            st.success("PDF gerado. Revê antes do voo.")
        except Exception as e:
            st.error(f"Erro ao gerar PDF: {e}")

# ===================== Extras: seleção rápida de Arrival/Alternates (auto-frequências) =====================
st.header("Seleção rápida de Arrival/Alternates (auto-frequências)")
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    sel_dept = st.selectbox("Departure", ["LPSO","LPEV","LPCO","LPCB","LPVZ"], index=0)
with col2:
    sel_arr = st.selectbox("Arrival", ["LPEV","LPCO","LPCB","LPVZ","LPSO"], index=0)
with col3:
    sel_alt1 = st.selectbox("Alternate 1", ["LPCO","LPCB","LPVZ","LPEV","LPSO"], index=0)
with col4:
    sel_alt2 = st.selectbox("Alternate 2", ["LPCB","LPVZ","LPEV","LPCO","LPSO"], index=1)
with col5:
    if st.button("Aplicar seleção e frequências"):
        # Atualiza campos de texto se existirem
        if field_exists("Dept_Airfield"): st.session_state["Dept_Airfield"] = sel_dept
        if field_exists("Arrival_Airfield"): st.session_state["Arrival_Airfield"] = sel_arr
        # Preenche comms
        if field_exists("Dept_Comm"): st.session_state["Dept_Comm"] = FREQS.get(sel_dept, "")
        if field_exists("Arrival_comm"): st.session_state["Arrival_comm"] = FREQS.get(sel_arr, "")
        st.success("Aplicado. Edita acima se quiseres afinar.")
