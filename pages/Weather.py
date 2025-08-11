# pages/Weather.py — Live only (CheckWX), no "saved", no persistence
from typing import List, Dict, Any, Optional
import datetime as dt, requests
from zoneinfo import ZoneInfo
import streamlit as st

st.set_page_config(page_title="Weather", layout="wide")

# --- Styles ---
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

DEFAULT_ICAOS = ["LPPT","LPBJ","LEBZ"]

# --- Helpers ---
def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

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

def zulu_plus_pt(d: Optional[dt.datetime]) -> str:
    if d is None: return ""
    if d.tzinfo is None: d = d.replace(tzinfo=dt.timezone.utc)
    d_utc = d.astimezone(dt.timezone.utc)
    d_pt  = d_utc.astimezone(ZoneInfo("Europe/Lisbon"))
    return f"{d_utc.strftime('%Y-%m-%d %H:%MZ')} ({d_pt.strftime('%H:%M')} Portugal)"

# --- Data (CheckWX live) ---
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
        if isinstance(data[0], dict): return data[0].get("raw") or data[0].get("raw_text","") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=75)
def fetch_taf_decoded(icao: str) -> Optional[Dict[str,Any]]:
    try:
        hdr = cw_headers()
        if not hdr: return None
        r = requests.get(f"https://api.checkwx.com/taf/{icao}/decoded", headers=hdr, timeout=10)
        r.raise_for_status(); data = r.json().get("data", [])
        return data[0] if data else None
    except Exception:
        return None

@st.cache_data(ttl=75)
def fetch_taf_raw(icao: str) -> str:
    try:
        hdr = cw_headers()
        if not hdr: return ""
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=hdr, timeout=10)
        r.raise_for_status(); data = r.json().get("data", [])
        if not data: return ""
        if isinstance(data[0], dict): return data[0].get("raw") or data[0].get("raw_text","") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=120)
def fetch_sigmet_lppc() -> List[str]:
    try:
        r = requests.get(
            "https://aviationweather.gov/api/data/isigmet",
            params={"loc":"eur","format":"json"}, timeout=12
        )
        r.raise_for_status(); js = r.json()
        items = js if isinstance(js, list) else js.get("features", []) or []
        out: List[str] = []
        for it in items:
            props = it.get("properties", {}) if isinstance(it, dict) else {}
            if not props and isinstance(it, dict): props = it
            raw = (props.get("raw") or props.get("raw_text") or props.get("sigmet_text") or "").strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            if raw and (fir == "LPPC" or " LPPC " in f" {raw} " or "FIR LPPC" in raw or " LPPC FIR" in raw):
                out.append(raw)
        return out
    except Exception:
        return []

# --- UI ---
st.markdown('<div class="page-title">Weather</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Live METAR, TAF and LPPC SIGMET</div>', unsafe_allow_html=True)

# Query params → ICAO list
try:
    q = st.query_params
    raw = q.get("icao", "")
    if isinstance(raw, list): raw = ",".join(raw)
except Exception:
    qp = st.experimental_get_query_params(); raw = qp.get("icao", [","])[0]

if not raw:
    raw = ",".join(DEFAULT_ICAOS)
icaos = [p.strip().upper() for p in raw.split(",") if p.strip()]

col1, col2 = st.columns([0.7, 0.3])
with col1:
    icaos_input = st.text_input("ICAO list (comma-separated)", value=",".join(icaos))
with col2:
    if st.button("Refresh"):
        st.cache_data.clear()
icaos = [x.strip().upper() for x in icaos_input.split(",") if x.strip()]

# Per-ICAO block
for icao in icaos:
    metar_dec = fetch_metar_decoded(icao); metar_raw = fetch_metar_raw(icao)
    taf_dec   = fetch_taf_decoded(icao);   taf_raw   = fetch_taf_raw(icao)

    # badge categoria de voo
    cat = (metar_dec or {}).get("flight_category","").upper()
    klass = {"VFR":"vfr","MVFR":"mvfr","IFR":"ifr","LIFR":"lifr"}.get(cat,"")
    badge = f'<span class="badge {klass}">{cat}</span>' if klass else ""

    # horas
    metar_obs = ""
    if metar_dec and metar_dec.get("observed"):
        metar_obs = zulu_plus_pt(parse_iso_utc(metar_dec["observed"]))
    taf_issued = ""
    if taf_dec:
        ts = (taf_dec.get("timestamp") or {}) if isinstance(taf_dec.get("timestamp"), dict) else {}
        issued = ts.get("issued") or taf_dec.get("issued")
        taf_issued = zulu_plus_pt(parse_iso_utc(issued)) if issued else ""

    st.markdown('<div class="row">', unsafe_allow_html=True)
    st.markdown(f"<h3>{icao} {badge}</h3>" + (f'<span class="info-line">Observed: {metar_obs}</span>' if metar_obs else ""), unsafe_allow_html=True)
    st.markdown(
        f'<div class="monos"><strong>METAR</strong> {metar_raw or "—"}\n\n'
        f'<strong>TAF</strong> {taf_raw or "—"}'
        + (f"\n\n<strong>TAF Issued</strong> {taf_issued}" if taf_issued else "")
        + '</div>',
        unsafe_allow_html=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

# SIGMET LPPC
st.subheader("SIGMET (LPPC)")
sigs = fetch_sigmet_lppc()
if not sigs:
    st.write("—")
else:
    for s in sigs:
        st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
        st.markdown("---")






