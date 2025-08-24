from typing import List, Dict, Any, Optional, Tuple
import datetime as dt, json, requests, re
from zoneinfo import ZoneInfo
import streamlit as st

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

def get_query_param_icao() -> str:
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

# ---------- GitHub Gist ----------
def gh_headers(token: Optional[str]) -> Dict[str,str]:
    hdr = {"Accept": "application/vnd.github+json"}
    if token: hdr["Authorization"] = f"Bearer {token}"
    return hdr

@st.cache_data(ttl=90)
def fetch_gist_file_content(gist_id: str, filename: str, token: Optional[str]) -> Optional[str]:
    try:
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers=gh_headers(token), timeout=10)
        r.raise_for_status()
        js = r.json()
        files = js.get("files", {}) or {}
        if filename not in files: return None
        fobj = files[filename]
        if fobj.get("truncated"):
            raw_url = fobj.get("raw_url")
            if not raw_url: return None
            rr = requests.get(raw_url, headers=gh_headers(token), timeout=10)
            rr.raise_for_status()
            return rr.text
        else:
            return fobj.get("content")
    except Exception:
        return None

def parse_gist_payload(content: Optional[str]) -> Dict[str,Any]:
    if content is None: return {"text":"", "updated_utc":None}
    try:
        payload = json.loads(content)
        if isinstance(payload, dict):
            return {"text": payload.get("text","").strip(), "updated_utc": payload.get("updated_utc")}
        if isinstance(payload, str):
            return {"text": payload.strip(), "updated_utc": None}
    except Exception:
        pass
    return {"text": content.strip(), "updated_utc": None}

# ---------- Data: GAMET ----------
@st.cache_data(ttl=90)
def load_gamet() -> Dict[str,Any]:
    token = st.secrets.get("GAMET_GIST_TOKEN","") or None
    gid   = st.secrets.get("GAMET_GIST_ID","")
    fn    = st.secrets.get("GAMET_GIST_FILENAME","")
    if not gid or not fn: return {"text":"", "updated_utc":None}
    content = fetch_gist_file_content(gid, fn, token)
    return parse_gist_payload(content)

# ---------- Data: SIGMET ----------
@st.cache_data(ttl=90)
def load_sigmet() -> Dict[str,Any]:
    token = st.secrets.get("SIGMET_GIST_TOKEN","") or None
    gid   = st.secrets.get("SIGMET_GIST_ID","")
    fn    = st.secrets.get("SIGMET_GIST_FILENAME","")
    if not gid or not fn: return {"text":"", "updated_utc":None}
    content = fetch_gist_file_content(gid, fn, token)
    return parse_gist_payload(content)

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

# ---------- SIGMET ----------
st.subheader("SIGMET (LPPC)")
sigmet = load_sigmet()
sig_text = (sigmet.get("text") or "").strip()
if not sig_text:
    st.write("—")
else:
    st.markdown(f'<div class="card monos">{sig_text}</div>', unsafe_allow_html=True)

# ---------- GAMET ----------
st.subheader("GAMET")
gamet = load_gamet()
text = (gamet.get("text") or "").strip()
if text:
    st.markdown(f'<div class="card monos">{text}</div>', unsafe_allow_html=True)
else:
    st.write("—")


