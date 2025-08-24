from typing import List, Dict, Any, Optional, Tuple
import datetime as dt, json, requests
from zoneinfo import ZoneInfo
import streamlit as st
import re

# ---------- Página ----------
st.set_page_config(page_title="Weather", layout="wide")

# ---------- Estilos ----------
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }

:root {
  --line:#e5e7eb;
  --muted:#6b7280;
  --vfr:#16a34a;
  --mvfr:#f59e0b;
  --ifr:#ef4444;
  --lifr:#7c3aed;
  --gamet:#16a34a;
  --expired:#ef4444;
}

.page-title { font-size:2rem; font-weight:800; margin:0 0 .25rem }
.subtle { color:var(--muted); margin:0 0 1.5rem }

.card {
  padding:10px 0;
  border-bottom:1px solid var(--line);
  margin-bottom:18px;
}
.card:last-of-type { border-bottom: none }

.card h3 {
  margin:0 0 6px;
  font-size:1.05rem;
}

.badge {
  display:inline-block;
  padding:3px 10px;
  border-radius:999px;
  font-weight:700;
  font-size:.80rem;
  color:#fff;
  margin-left:8px;
  vertical-align:middle
}
.vfr { background:var(--vfr) } .mvfr { background:var(--mvfr) }
.ifr { background:var(--ifr) } .lifr { background:var(--lifr) }

.meta { font-size:.9rem; color:var(--muted); margin-left:8px }

.monos {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size:.95rem;
  white-space: pre-wrap;
}

.gamet-valid {
  font-weight: bold;
  color: var(--gamet);
  margin-bottom: 6px;
}
.gamet-expired {
  font-weight: bold;
  color: var(--expired);
  margin-bottom: 6px;
}
</style>
""", unsafe_allow_html=True)

# ---------- Defaults ----------
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

# ---------- Helpers ----------
def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

def parse_iso_utc(s: Optional[str]) -> Optional[dt.datetime]:
    if not s: return None
    try:
        if s.endswith("Z"): return dt.datetime.fromisoformat(s.replace("Z","+00:00"))
        return dt.datetime.fromisoformat(s)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z","%Y-%m-%d %H:%M:%S%z"):
            try: return dt.datetime.strptime(s, fmt)
            except Exception: pass
    return None

def zulu_plus_pt(d: Optional[dt.datetime]) -> str:
    if d is None: return ""
    if d.tzinfo is None: d = d.replace(tzinfo=dt.timezone.utc)
    d_utc = d.astimezone(dt.timezone.utc)
    d_pt  = d_utc.astimezone(ZoneInfo("Europe/Lisbon"))
    return f"{d_utc.strftime('%Y-%m-%d %H:%MZ')} ({d_pt.strftime('%H:%M')} Portugal)"

def parse_gamet_validity(text: str) -> Optional[str]:
    match = re.search(r'VALID (\d{6})/(\d{6})', text)
    if not match: return None
    start_raw, end_raw = match.groups()
    try:
        today = dt.datetime.utcnow()
        start = dt.datetime.strptime(start_raw, "%d%H%M").replace(year=today.year, month=today.month, tzinfo=dt.timezone.utc)
        end   = dt.datetime.strptime(end_raw, "%d%H%M").replace(year=today.year, month=today.month, tzinfo=dt.timezone.utc)
        now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
        status = "active" if start <= now <= end else "expired"
        return f"{start.strftime('%d %b %H:%M')}Z – {end.strftime('%d %b %H:%M')}Z ({status})"
    except Exception:
        return None

def get_query_param_icao() -> str:
    """Compatível com Streamlit antigos (experimental_get_query_params) e novos (st.query_params)."""
    try:
        if hasattr(st, "query_params"):
            val = st.query_params.get("icao", "")
            if isinstance(val, list): return ",".join(val)
            return str(val or "")
        else:
            qp = st.experimental_get_query_params()
            val = qp.get("icao", [""])
            return ",".join(val) if isinstance(val, list) else str(val or "")
    except Exception:
        return ""

# ---------- Data: METAR/TAF via CheckWX ----------
@st.cache_data(ttl=75)
def fetch_metar_decoded(icao: str) -> Optional[Dict[str,Any]]:
    try:
        hdr = cw_headers()
        if not hdr: return None
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers=hdr, timeout=10)
        r.raise_for_status(); data = r.json().get("data", [])
        return data[0] if data else None
    except Exception:
        return None

@st.cache_data(ttl=75)
def fetch_metar_raw(icao: str) -> str:
    try:
        hdr = cw_headers()
        if not hdr: return ""
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=hdr, timeout=10)
        r.raise_for_status(); data = r.json().get("data", [])
        if not data: return ""
        return str(data[0]) if not isinstance(data[0], dict) else (data[0].get("raw") or "")
    except Exception:
        return ""

@st.cache_data(ttl=75)
def fetch_taf_raw(icao: str) -> str:
    try:
        hdr = cw_headers()
        if not hdr: return ""
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=hdr, timeout=10)
        r.raise_for_status(); data = r.json().get("data", [])
        if not data: return ""
        return str(data[0]) if not isinstance(data[0], dict) else (data[0].get("raw") or "")
    except Exception:
        return ""

# ---------- GitHub Gist helpers ----------
def gh_headers(token: Optional[str]) -> Dict[str,str]:
    hdr = {"Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    return hdr

@st.cache_data(ttl=90)
def fetch_gist_file_content(gist_id: str, filename: str, token: Optional[str]) -> Tuple[Optional[str], Dict[str,Any]]:
    """
    Devolve (conteudo_texto | None, debug_info).
    Lida com ficheiros truncados -> segue o raw_url.
    Funciona com Gist público (token None) ou privado (token obrigatório).
    """
    debug: Dict[str,Any] = {"stage":"init", "gist_id":gist_id, "filename":filename, "used_raw_url":False}
    try:
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gh_headers(token), timeout=10)
        debug["stage"] = "gist_meta"
        r.raise_for_status()
        js = r.json()
        files = js.get("files", {}) or {}
        if filename not in files:
            debug["error"] = f"filename '{filename}' não encontrado no gist"
            return None, debug
        fobj = files[filename]
        debug["truncated"] = bool(fobj.get("truncated"))
        # Preferir content se não truncado
        if fobj.get("truncated"):
            raw_url = fobj.get("raw_url")
            if not raw_url:
                debug["error"] = "ficheiro truncado e sem raw_url"
                return None, debug
            rr = requests.get(raw_url, headers=gh_headers(token), timeout=10)
            debug["stage"] = "gist_raw"
            rr.raise_for_status()
            debug["used_raw_url"] = True
            return rr.text, debug
        else:
            content = fobj.get("content")
            if isinstance(content, str):
                return content, debug
            debug["error"] = "content vazio ou não textual"
            return None, debug
    except Exception as e:
        debug["exception"] = str(e)
        return None, debug

def parse_gist_payload(content: Optional[str]) -> Tuple[Dict[str,Any], Optional[str]]:
    """
    Tenta json.loads. Se falhar, assume que o conteúdo é texto simples (SIGMET/GAMET)
    e devolve {"text": content, "updated_utc": None}.
    Retorna (payload, error_str).
    """
    if content is None:
        return {"text":"", "updated_utc":None}, "sem conteúdo"
    try:
        payload = json.loads(content)
        # Normalizar estrutura esperada
        if isinstance(payload, dict):
            text = (payload.get("text") or "").strip() if "text" in payload else ""
            updated = payload.get("updated_utc") if "updated_utc" in payload else None
            # se for um array de blocos, junta
            if not text and isinstance(payload.get("items"), list):
                text = "\n\n".join([str(x) for x in payload["items"]])
            return {"text": text, "updated_utc": updated}, None
        # se for string dentro do JSON
        if isinstance(payload, str):
            return {"text": payload.strip(), "updated_utc": None}, None
        return {"text":"", "updated_utc":None}, "JSON não é dict/str"
    except Exception:
        # Conteúdo não-JSON -> tratar como texto simples
        return {"text": content.strip(), "updated_utc": None}, None

# ---------- Data: GAMET via Gist ----------
def gamet_gist_config_ok() -> bool:
    return bool(st.secrets.get("GAMET_GIST_ID","") and st.secrets.get("GAMET_GIST_FILENAME",""))

@st.cache_data(ttl=90)
def load_gamet() -> Tuple[Dict[str,Any], Dict[str,Any]]:
    if not gamet_gist_config_ok():
        return {"text":"", "updated_utc":None}, {"error":"GAMET Gist não configurado"}
    token = st.secrets.get("GAMET_GIST_TOKEN","") or None  # público se None
    gid   = st.secrets["GAMET_GIST_ID"]
    fn    = st.secrets["GAMET_GIST_FILENAME"]
    content, dbg = fetch_gist_file_content(gid, fn, token)
    payload, perr = parse_gist_payload(content)
    if perr: dbg["payload_warning"] = perr
    return payload, dbg

# ---------- Data: SIGMET via Gist (igual ao GAMET) ----------
def sigmet_gist_config_ok() -> bool:
    return bool(st.secrets.get("SIGMET_GIST_ID","") and st.secrets.get("SIGMET_GIST_FILENAME",""))

@st.cache_data(ttl=90)
def load_sigmet() -> Tuple[Dict[str,Any], Dict[str,Any]]:
    if not sigmet_gist_config_ok():
        return {"text":"", "updated_utc":None}, {"error":"SIGMET Gist não configurado"}
    token = st.secrets.get("SIGMET_GIST_TOKEN","") or None  # público se None
    gid   = st.secrets["SIGMET_GIST_ID"]
    fn    = st.secrets["SIGMET_GIST_FILENAME"]
    content, dbg = fetch_gist_file_content(gid, fn, token)
    payload, perr = parse_gist_payload(content)
    if perr: dbg["payload_warning"] = perr
    return payload, dbg

# ---------- UI ----------
st.markdown('<div class="page-title">Weather</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">METAR · TAF · SIGMET (LPPC) · GAMET</div>', unsafe_allow_html=True)

raw = get_query_param_icao() or ",".join(DEFAULT_ICAOS)
cc1, cc2 = st.columns([0.75,0.25])
with cc1:
    icaos_input = st.text_input("ICAO list (comma-separated)", value=raw)
with cc2:
    if st.button("Refresh"): st.cache_data.clear()

icaos = [x.strip().upper() for x in icaos_input.split(",") if x.strip()]

# ---------- METAR / TAF ----------
for icao in icaos:
    metar_dec = fetch_metar_decoded(icao)
    metar_raw = fetch_metar_raw(icao)
    taf_raw   = fetch_taf_raw(icao)

    cat = (metar_dec or {}).get("flight_category","").upper()
    klass = {"VFR":"vfr","MVFR":"mvfr","IFR":"ifr","LIFR":"lifr"}.get(cat,"")
    badge = f'<span class="badge {klass}">{cat}</span>' if klass else ""
    obs = zulu_plus_pt(parse_iso_utc((metar_dec or {}).get("observed")))

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<h3>{icao} {badge}' + (f'<span class="meta">Observed {obs}</span>' if obs else "") + '</h3>', unsafe_allow_html=True)
    st.markdown(f'<div class="monos"><strong>METAR</strong> {metar_raw or "—"}\n\n<strong>TAF</strong> {taf_raw or "—"}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# ---------- SIGMET LPPC (via Gist) ----------
st.subheader("SIGMET (LPPC)")
sigmet, sig_dbg = load_sigmet()
sig_text = (sigmet.get("text") or "").strip()
sig_updated = zulu_plus_pt(parse_iso_utc(sigmet.get("updated_utc")))
if not sig_text:
    st.write("—")
else:
    if sig_updated:
        st.markdown(f'<div class="meta">Atualizado: {sig_updated}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="card monos">{sig_text}</div>', unsafe_allow_html=True)

# ---------- GAMET ----------
st.subheader("GAMET")
gamet, gamet_dbg = load_gamet()
text = (gamet.get("text") or "").strip()
validity = parse_gamet_validity(text)
gamet_updated = zulu_plus_pt(parse_iso_utc(gamet.get("updated_utc")))
if text:
    st.markdown(f'<div class="card monos">{validity_line}{text}</div>', unsafe_allow_html=True)
else:
    st.write("—")

# ---------- Debug ----------
with st.expander("🔧 Debug (Gists & Config)", expanded=False):
    def show_dbg(title, dbg):
        st.markdown(f"**{title}**")
        st.code(json.dumps(dbg, indent=2, ensure_ascii=False))
    st.markdown("**Secrets configuradas**")
    st.write({
        "GAMET_GIST_ID": bool(st.secrets.get("GAMET_GIST_ID","")),
        "GAMET_GIST_FILENAME": bool(st.secrets.get("GAMET_GIST_FILENAME","")),
        "GAMET_GIST_TOKEN?": bool(st.secrets.get("GAMET_GIST_TOKEN","")),
        "SIGMET_GIST_ID": bool(st.secrets.get("SIGMET_GIST_ID","")),
        "SIGMET_GIST_FILENAME": bool(st.secrets.get("SIGMET_GIST_FILENAME","")),
        "SIGMET_GIST_TOKEN?": bool(st.secrets.get("SIGMET_GIST_TOKEN","")),
        "CHECKWX_API_KEY?": bool(st.secrets.get("CHECKWX_API_KEY","")),
        "streamlit_query_params_api": "st.query_params" if hasattr(st, "query_params") else "experimental_get_query_params"
    })
    show_dbg("GAMET debug", gamet_dbg)
    show_dbg("SIGMET debug", sig_dbg)
