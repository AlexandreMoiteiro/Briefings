# pages/Weather.py
# Clean weather dashboard (no sidebar, METAR+TAF together, shows saved manual SIGMET)
# - METAR/TAF via CheckWX (needs CHECKWX_API_KEY in secrets)
# - SIGMET LPPC is read from the saved store (Gist if configured, else /mnt/data)
# - GAMET via optional secrets.GAMET_URL
# - Defaults ICAOs: LPPT, LPBJ, LEBZ | supports ?icao=AAA or ?icao=AAA,BBB

from typing import List, Dict, Any, Optional
import datetime as dt, json, os, requests
from zoneinfo import ZoneInfo
import streamlit as st

st.set_page_config(page_title="Weather (Live)", layout="wide")

# Hide sidebar completely
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }

:root { --line:#e5e7eb; --muted:#6b7280; --vfr:#16a34a; --mvfr:#f59e0b; --ifr:#ef4444; --lifr:#7c3aed; }
.page-title{font-size:2rem;font-weight:800;margin:0 0 .25rem}
.subtle{color:var(--muted);margin-bottom:.75rem}
.row{padding:8px 0 14px;border-bottom:1px solid var(--line)}
.row h3{margin:0 0 6px;font-size:1.1rem}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-weight:700;font-size:.80rem;color:#fff;margin-left:8px;vertical-align:middle}
.vfr{background:var(--vfr)} .mvfr{background:var(--mvfr)} .ifr{background:var(--ifr)} .lifr{background:var(--lifr)}
.muted{color:var(--muted)}
.monos{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono",monospace;font-size:.95rem;white-space:pre-wrap}
.info-line{font-size:.92rem;color:var(--muted)}
</style>
""", unsafe_allow_html=True)

DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]
LOCAL_SIGMET_PATH = "/mnt/data/sigmet_lppc.json"

def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

# robust time parsing
def parse_iso_utc(s: str) -> Optional[dt.datetime]:
    if not s: return None
    try:
        if s.endswith("Z"): return dt.datetime.fromisoformat(s.replace("Z","+00:00"))
        return dt.datetime.fromisoformat(s)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z","%Y-%m-%d %H:%M:%S%z"):
            try: return dt.datetime.strptime(s, fmt)
            except Exception: pass
    return None

def zulu_plus_portugal(d: Optional[dt.datetime]) -> str:
    if d is None: return ""
    if d.tzinfo is None: d = d.replace(tzinfo=dt.timezone.utc)
    d_utc = d.astimezone(dt.timezone.utc)
    d_pt = d_utc.astimezone(ZoneInfo("Europe/Lisbon"))
    return f"{d_utc.strftime('%Y-%m-%d %H:%MZ')} ({d_pt.strftime('%H:%M')} Portugal)"

# fetchers
@st.cache_data(ttl=75)
def fetch_metar_decoded(icao: str) -> Optional[Dict[str,Any]]:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else None
    except Exception:
        return None

@st.cache_data(ttl=75)
def fetch_metar_raw(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text","") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=75)
def fetch_taf_decoded(icao: str) -> Optional[Dict[str,Any]]:
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}/decoded", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else None
    except Exception:
        return None

@st.cache_data(ttl=75)
def fetch_taf_raw(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text","") or ""
        return str(data[0])
    except Exception:
        return ""

# read-only SIGMET from stored location (same logic as app.py)
def gist_config_ok() -> bool:
    return bool(st.secrets.get("GIST_TOKEN","") and st.secrets.get("GIST_ID","") and st.secrets.get("GIST_FILENAME",""))

def load_sigmet_from_gist() -> Optional[Dict[str,Any]]:
    try:
        token = st.secrets["GIST_TOKEN"]; gist_id = st.secrets["GIST_ID"]; filename = st.secrets["GIST_FILENAME"]
        r = requests.get(f"https://api.github.com/gists/{gist_id}", headers={"Authorization": f"token {token}"}, timeout=10)
        r.raise_for_status()
        files = r.json().get("files", {})
        if filename in files and "content" in files[filename]:
            content = files[filename]["content"]
            try:
                return json.loads(content)
            except Exception:
                return {"text": content, "updated_utc": None}
    except Exception:
        return None
    return None

def load_sigmet_local() -> Optional[Dict[str,Any]]:
    try:
        if os.path.exists(LOCAL_SIGMET_PATH):
            with open(LOCAL_SIGMET_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None
    return None

def load_sigmet() -> Dict[str,Any]:
    data = load_sigmet_from_gist() if gist_config_ok() else None
    if data: return data
    data = load_sigmet_local()
    return data or {"text": "", "updated_utc": None}

@st.cache_data(ttl=90)
def fetch_gamet_live() -> str:
    url = (st.secrets.get("GAMET_URL","") or "").strip()
    if not url: return ""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        if "application/json" in (r.headers.get("Content-Type","").lower()):
            j = r.json()
            for k in ("text","gamet","raw","message","body","data"):
                v = j.get(k)
                if isinstance(v, str): return v.strip()
                if isinstance(v, list): return "\n".join(map(str, v)).strip()
            return str(j)
        return r.text.strip()
    except Exception:
        return ""

def flight_cat_badge(decoded: Optional[Dict[str,Any]]) -> str:
    if not decoded: return ""
    cat = (decoded.get("flight_category") or "").upper()
    klass = {"VFR":"vfr","MVFR":"mvfr","IFR":"ifr","LIFR":"lifr"}.get(cat,"")
    return f'<span class="badge {klass}">{cat}</span>' if klass else ""

def parse_query_icaos(defaults: List[str]) -> List[str]:
    q = st.query_params
    raw = q.get("icao","")
    if isinstance(raw, list): raw = ",".join(raw)
    lst: List[str] = []
    if raw:
        for p in raw.split(","):
            p = p.strip().upper()
            if len(p)==4: lst.append(p)
    return lst or defaults

# UI
st.markdown('<div class="page-title">Weather (Live)</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Latest METAR, TAF, saved LPPC SIGMET, and GAMET</div>', unsafe_allow_html=True)

icaos = parse_query_icaos(DEFAULT_ICAOS)
col1, col2 = st.columns([0.7, 0.3])
with col1:
    icaos_input = st.text_input("ICAO list (comma-separated)", value=",".join(icaos))
with col2:
    if st.button("Refresh"):
        st.cache_data.clear()
icaos = [x.strip().upper() for x in icaos_input.split(",") if x.strip()]

# METAR + TAF per aerodrome
for icao in icaos:
    metar_dec = fetch_metar_decoded(icao)
    metar_raw = fetch_metar_raw(icao)
    taf_dec = fetch_taf_decoded(icao)
    taf_raw = fetch_taf_raw(icao)

    badge = flight_cat_badge(metar_dec)

    metar_obs = ""
    if metar_dec and metar_dec.get("observed"):
        metar_obs = zulu_plus_portugal(parse_iso_utc(metar_dec["observed"]))

    taf_issued = ""
    if taf_dec:
        issued = (taf_dec.get("timestamp", {}) or {}).get("issued") or taf_dec.get("issued") or ""
        taf_issued = zulu_plus_portugal(parse_iso_utc(issued)) if issued else ""

    st.markdown('<div class="row">', unsafe_allow_html=True)
    hdr = f"<h3>{icao} {badge}</h3>"
    sub = f'<span class="info-line">Observed: {metar_obs}</span>' if metar_obs else ""
    st.markdown(hdr + sub, unsafe_allow_html=True)
    st.markdown(
        f'<div class="monos"><strong>METAR</strong> {metar_raw or "—"}'
        + (f"\n\n<strong>TAF</strong> {taf_raw or '—'}")
        + (f"\n\n<strong>TAF Issued</strong> {taf_issued}" if taf_issued else ""),
        unsafe_allow_html=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

# SIGMET saved (read-only)
st.subheader("SIGMET (LPPC) — Saved")
saved = load_sigmet()
if saved.get("text"):
    if saved.get("updated_utc"):
        st.markdown(f'<div class="info-line">Last saved (UTC): {saved["updated_utc"]}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="monos">{saved["text"]}</div>', unsafe_allow_html=True)
else:
    st.write("—")

# GAMET Live
st.subheader("GAMET (Live)")
gamet = fetch_gamet_live()
if gamet:
    st.markdown(f'<div class="monos">{gamet}</div>', unsafe_allow_html=True)
    st.download_button("Download GAMET as .txt", data=gamet, file_name="gamet.txt")
else:
    st.write("No GAMET available. Add a 'GAMET_URL' to secrets to enable live GAMET here.")




