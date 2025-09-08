# Streamlit app – Navigation Plan & Inflight Log (General Filler)
# Reqs: streamlit, pytz, pypdf

import streamlit as st
import datetime as dt
import pytz
from pathlib import Path
import io
import unicodedata
from typing import List, Dict, Any, Optional

# ============ PDF ============
try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject
    PYPDF_OK = True
except Exception:
    PYPDF_OK = False

# ============ Helpers ============
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
        # Melhor compatibilidade de aparências
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
    """Soma minutos a uma hora 'naive' interpretada no fuso dado, devolve hora (00:00–23:59)."""
    today = dt.date.today()
    base = tzinfo.localize(dt.datetime.combine(today, t))
    new_dt = base + dt.timedelta(minutes=minutes)
    return new_dt.timetz().replace(tzinfo=None)

# ============ App ============
st.set_page_config(page_title="NAVLOG – Navigation Plan & Inflight Log", layout="wide", initial_sidebar_state="collapsed")
st.title("Navigation Plan & Inflight Log – PDF Filler")

st.markdown("""
Este é o preenchimento **geral** do NAVLOG. Introduz os dados principais e os **legs** na tabela.
Mais tarde ligamos cálculos automáticos (TAS/GS, heading com vento, combustíveis) às tuas tabelas.
""")

# Local do template (prioriza o /mnt/data com o teu ficheiro carregado)
PDF_TEMPLATE_PATHS = [
    "/mnt/data/NAVLOG - FORM.pdf",
    "NAVLOG - FORM.pdf",
]

# ====== Cabeçalho / Flight header ======
st.header("Flight header")
c1, c2, c3, c4 = st.columns(4)
with c1:
    aircraft = st.text_input("Aircraft", "")
    registration = st.text_input("Registration", "")
    callsign = st.text_input("Callsign", "")
    lesson = st.text_input("Lesson", "")
with c2:
    instrutor = st.text_input("Instrutor", "")
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
    mag_var = st.text_input("Mag. Var", "")
with c12:
    temp_isa_dev = st.text_input("Temp / ISA Dev", "")

# ====== Legs (até 11) ======
st.header("Legs (até 11)")
st.caption("Preenche manualmente por agora. No próximo passo podemos calcular TAS/GS/HDG automaticamente das tabelas.")

# Modelo de linha
DEFAULT_ROWS = [
    {"Name": "", "Ident": "", "Alt/FL": "", "T_CRS": "", "M_CRS": "", "GS": "", "Dist": "",
     "ETE_min": "", "ETO(HH:MM)": "", "PL_B/O_L": "", "EFOB_L": "",
     "T_HDG": "", "M_HDG": "", "TAS": "", "Freq": ""}
    for _ in range(11)
]
# Sessão
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
        "T_CRS": st.column_config.TextColumn("T CRS"),
        "M_CRS": st.column_config.TextColumn("M CRS"),
        "T_HDG": st.column_config.TextColumn("T HDG"),
        "M_HDG": st.column_config.TextColumn("M HDG"),
        "TAS": st.column_config.TextColumn("TAS"),
        "GS": st.column_config.TextColumn("GS"),
        "Dist": st.column_config.TextColumn("Dist (nm)"),
        "ETE_min": st.column_config.TextColumn("ETE (min)"),
        "ETO(HH:MM)": st.column_config.TextColumn("ETO (HH:MM)"),
        "PL_B/O_L": st.column_config.TextColumn("PL B/O (L)"),
        "EFOB_L": st.column_config.TextColumn("EFOB (L)"),
    },
    hide_index=True,
    key="legs_editor"
)

# ====== Cálculos básicos (totais / ETO / EFOB) ======
st.header("Cálculos básicos")
c13, c14, c15 = st.columns(3)
with c13:
    start_fuel_l = st.number_input("Fuel inicial (EFOB_START) [L]", min_value=0.0, value=0.0, step=1.0)
with c14:
    auto_compute_eto = st.checkbox("Calcular ETO a partir do ETD + ETE", value=True)
with c15:
    compute_efob = st.checkbox("Calcular EFOB (cumulativo) a partir do PL B/O", value=True)

total_dist = 0.0
total_ete_min = 0
total_pl_bo = 0.0
efob_tot = start_fuel_l

etd_time = parse_hhmm(etd_str)
curr_eto = etd_time

calc_rows = []
for i, r in enumerate(legs, start=1):
    # Distância
    try:
        d = float(str(r.get("Dist","")).strip() or 0.0)
    except Exception:
        d = 0.0
    total_dist += d

    # ETE em minutos
    try:
        ete = int(float(str(r.get("ETE_min","")).strip() or 0))
    except Exception:
        ete = 0
    total_ete_min += ete

    # ETO
    eto_str = str(r.get("ETO(HH:MM)", "")).strip()
    if auto_compute_eto and etd_time:
        if curr_eto is None:
            eto_val = ""
        else:
            # soma ETE ao tempo corrente e escreve
            curr_eto = add_minutes_to_time(curr_eto, ete) if i == 1 else add_minutes_to_time(curr_eto, ete)
            eto_val = curr_eto.strftime("%H:%M")
    else:
        eto_val = eto_str

    # Fuel – PL B/O (planeado burn-off) e EFOB
    try:
        pl_bo = float(str(r.get("PL_B/O_L","")).strip() or 0.0)
    except Exception:
        pl_bo = 0.0
    total_pl_bo += pl_bo

    if compute_efob:
        efob_tot = max(0.0, efob_tot - pl_bo)
        efob_val = efob_tot
    else:
        try:
            efob_val = float(str(r.get("EFOB_L","")).strip() or 0.0)
        except Exception:
            efob_val = 0.0

    calc_rows.append({
        **r,
        "ETO(HH:MM)": eto_val,
        "EFOB_L": f"{efob_val:.1f}"
    })

# Totais
efob_total_field = efob_tot if compute_efob else (float(str(legs[-1].get("EFOB_L","") or 0)) if legs else 0.0)
st.markdown(f"**Distância total:** {total_dist:.1f} nm")
st.markdown(f"**ETE total:** {total_ete_min} min  ({total_ete_min//60}h{total_ete_min%60:02d})")
st.markdown(f"**PL B/O total:** {total_pl_bo:.1f} L")
st.markdown(f"**EFOB (final):** {efob_total_field:.1f} L")

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

    # Cabeçalho – nomes conforme o PDF
    put_any(named_map, fieldset, "Aircraft", aircraft)
    put_any(named_map, fieldset, "Registration", registration)
    put_any(named_map, fieldset, "Callsign", callsign)
    put_any(named_map, fieldset, "Lesson", lesson)
    put_any(named_map, fieldset, "Instrutor", instrutor)
    put_any(named_map, fieldset, "Student", student)
    put_any(named_map, fieldset, "Logbook", logbook)
    put_any(named_map, fieldset, "GRADING", grading)  # se existir; o PDF mostra "GRADING LOGBOOK"
    put_any(named_map, fieldset, "Dept_Airfield", dept_airfield)
    put_any(named_map, fieldset, "Arrival_Airfield", arr_airfield)
    put_any(named_map, fieldset, "Leg_Number", leg_number)
    put_any(named_map, fieldset, "Alternate", alternate_airfield)

    put_any(named_map, fieldset, "ETD/ETA", f"{etd_str} / {eta_str}")
    put_any(named_map, fieldset, "Startup", startup)
    put_any(named_map, fieldset, "Takeoff", takeoff)
    put_any(named_map, fieldset, "Landing", landing)
    put_any(named_map, fieldset, "Shutdown", shutdown)

    put_any(named_map, fieldset, "Level F/F", level_ff)
    put_any(named_map, fieldset, "Climb_Fuel", climb_fuel)
    put_any(named_map, fieldset, "QNH", qnh)
    put_any(named_map, fieldset, "Clearances", clearances)
    put_any(named_map, fieldset, "Dept_Comm", dept_comm)
    put_any(named_map, fieldset, "Enroute_comm", enroute_comm)
    put_any(named_map, fieldset, "Arrival_comm", arrival_comm)

    put_any(named_map, fieldset, "flt_lvl_altitude", flt_lvl_alt)
    put_any(named_map, fieldset, "wind", wind_info)
    put_any(named_map, fieldset, "mag_var", mag_var)
    put_any(named_map, fieldset, "temp_isa_dev", temp_isa_dev)

    # Observações
    put_any(named_map, fieldset, "OBSERVATIONS", observations)

    # Legs 1..11 — nomes conforme lista do PDF
    # Campos por índice:
    # Namei, Alt(i), TCRSi, THDGi, MCRSi, MHDGi, TASi, GSi, Disti, ETEi, ETOi, PL_BOi, EFOBi, Freq
    for i, r in enumerate(calc_rows, start=1):
        idx = "" if i == 1 else str(i)  # no PDF, alguns campos do 1º não levam sufixo? (normalmente levam; mantemos padrão com número)
        # Para este PDF específico, os campos são Name1..Name11 etc. Vamos usar sufixo numérico sempre.
        suf = str(i)

        put_any(named_map, fieldset, f"Name{suf}", str(r.get("Name","")))
        put_any(named_map, fieldset, f"Alt{suf}", str(r.get("Alt/FL","")))
        put_any(named_map, fieldset, f"TCRS{suf}", str(r.get("T_CRS","")))
        put_any(named_map, fieldset, f"THDG{suf}", str(r.get("T_HDG","")))
        put_any(named_map, fieldset, f"MCRS{suf}", str(r.get("M_CRS","")))
        put_any(named_map, fieldset, f"MHDG{suf}", str(r.get("M_HDG","")))
        put_any(named_map, fieldset, f"TAS{suf}", str(r.get("TAS","")))
        put_any(named_map, fieldset, f"GS{suf}", str(r.get("GS","")))
        put_any(named_map, fieldset, f"Dist{suf}", str(r.get("Dist","")))
        put_any(named_map, fieldset, f"ETE{suf}", str(r.get("ETE_min","")))
        put_any(named_map, fieldset, f"ETO{suf}", str(r.get("ETO(HH:MM)","")))
        put_any(named_map, fieldset, f"PL_BO{suf}", str(r.get("PL_B/O_L","")))
        put_any(named_map, fieldset, f"EFOB{suf}", str(r.get("EFOB_L","")))
        put_any(named_map, fieldset, f"FREQ{suf}", str(r.get("Freq","")))

    # Totais
    put_any(named_map, fieldset, "ETE_Total", str(total_ete_min))
    put_any(named_map, fieldset, "Dist_Total", f"{total_dist:.1f}")
    put_any(named_map, fieldset, "PL_BO_TOTAL", f"{total_pl_bo:.1f}")
    put_any(named_map, fieldset, "EFOB_TOTAL", f"{efob_total_field:.1f}")

    # Botão
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
