# pages/Weather.py — Enhanced Design for METAR/TAF (CheckWX) + SIGMET LPPC (AWC) + GAMET (Gist)
from typing import List, Dict, Any, Optional
import datetime as dt, json, requests
from zoneinfo import ZoneInfo
import streamlit as st

# ---------- Page ----------
st.set_page_config(page_title="Weather", layout="wide")

# ---------- Styles ----------
st.markdown("""
<style>
/* Hide sidebar */
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
header [data-testid="baseButton-headerNoPadding"] { display:none !important; }

/* Tokens */
:root {
    --line: #e5e7eb;
    --muted: #6b7280;
    --vfr: #16a34a;
    --mvfr: #f59e0b;
    --ifr: #ef4444;
    --lifr: #7c3aed;
    --background: #f9fafb;
}

/* Page */
body { background-color: var(--background); }
.page-title {
    font-size: 2.4rem;
    font-weight: 800;
    margin: 0.5rem 0 0.25rem;
}
.subtle {
    color: var(--muted);
    margin: 0 0 1.5rem;
    font-size: 1.05rem;
}

/* Card */
.card {
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 16px 20px;
    background: #ffffff;
    margin-bottom: 16px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.04);
}
.card h3 {
    margin: 0 0 10px;
    font-size: 1.1rem;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
}
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-weight: 700;
    font-size: 0.75rem;
    color: #fff;
    margin-left: 10px;
    vertical-align: middle;
}
.vfr { background: var(--vfr); }
.mvfr { background: var(--mvfr); }
.ifr { background: var(--ifr); }
.lifr { background: var(--lifr); }

.meta {
    font-size: .85rem;
    color: var(--muted);
    margin-left: auto;
    margin-top: 6px;
}

/* Typography */
.monos {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
    font-size: .95rem;
    white-space: pre-wrap;
    line-height: 1.6;
    background: #f3f4f6;
    padding: 12px;
    border-radius: 10px;
    color: #111827;
    margin-top: 10px;
    overflow-x: auto;
}

/* Section spacing */
.section {
    margin-top: 32px;
}
</style>
""", unsafe_allow_html=True)

# ---------- Defaults ----------
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

# ---------- Helpers ----------
def cw_headers() -> Dict[str, str]:
    key = st.secrets.get("CHECKWX_API_KEY", "")
    return {"X-API-Key": key} if key else {}

def parse_iso_utc(s: str) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.datetime.fromisoformat(s)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
            try:
                return dt.datetime.strptime(s, fmt)
            except Exception:
                pass
    return None

def zulu_plus_pt(d: Optional[dt.datetime]) -> str:
    if d is None:
        return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    d_utc = d.astimezone(dt.timezone.utc)
    d_pt = d_utc.astimezone(ZoneInfo("Europe/Lisbon"))
    return f"{d_utc.strftime('%Y-%m-%d %H:%MZ')} ({d_pt.strftime('%H:%M')} Portugal)"

# ---------- Data (CheckWX/AWC) ----------
@st.cache_data(ttl=75)
def fetch_metar_decoded(icao: str) -> Optional[Dict[str, Any]]:
    try:
        hdr = cw_headers()
        if not hdr:
            return None
        r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers=hdr, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else None
    except Exception:
        return None

@st.cache_data(ttl=75)
def fetch_metar_raw(icao: str) -> str:
    try:
        hdr = cw_headers()
        if not hdr:
            return ""
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=hdr, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text", "") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=75)
def fetch_taf_decoded(icao: str) -> Optional[Dict[str, Any]]:
    try:
        hdr = cw_headers()
        if not hdr:
            return None
        r = requests.get(f"https://api.checkwx.com/taf/{icao}/decoded", headers=hdr, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else None
    except Exception:
        return None

@st.cache_data(ttl=75)
def fetch_taf_raw(icao: str) -> str:
    try:
        hdr = cw_headers()
        if not hdr:
            return ""
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=hdr, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return ""
        if isinstance(data[0], dict):
            return data[0].get("raw") or data[0].get("raw_text", "") or ""
        return str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=120)
def fetch_sigmet_lppc() -> List[str]:
    try:
        r = requests.get("https://aviationweather.gov/api/data/isigmet",
                         params={"loc": "eur", "format": "json"}, timeout=12)
        r.raise_for_status()
        js = r.json()
        items = js if isinstance(js, list) else js.get("features", []) or []
        out: List[str] = []
        for it in items:
            props = it.get("properties", {}) if isinstance(it, dict) else {}
            if not props and isinstance(it, dict):
                props = it
            raw = (props.get("raw") or props.get("raw_text") or props.get("sigmet_text") or "").strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            if not raw:
                continue
            if fir == "LPPC" or " LPPC " in f" {raw} " or "FIR LPPC" in raw or " LPPC FIR" in raw:
                out.append(raw)
        return out
    except Exception:
        return []

# ---------- GAMET (Gist) ----------
def gamet_gist_config_ok() -> bool:
    return bool(
        st.secrets.get("GAMET_GIST_TOKEN", "") and
        st.secrets.get("GAMET_GIST_ID", "") and
        st.secrets.get("GAMET_GIST_FILENAME", "")
    )

@st.cache_data(ttl=90)
def load_gamet() -> Dict[str, Any]:
    if not gamet_gist_config_ok():
        return {"text": "", "updated_utc": None}
    try:
        token = st.secrets["GAMET_GIST_TOKEN"]
        gid = st.secrets["GAMET_GIST_ID"]
        fn = st.secrets["GAMET_GIST_FILENAME"]
        r = requests.get(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}"},
            timeout=10
        )
        r.raise_for_status()
        files = r.json().get("files", {})
        if fn in files and "content" in files[fn]:
            content = files[fn]["content"]
            try:
                return json.loads(content)
            except Exception:
                return {"text": content, "updated_utc": None}
    except Exception:
        pass
    return {"text": "", "updated_utc": None}

# ---------- UI: Header ----------
st.markdown('<div class="page-title">Weather</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">METAR · TAF · SIGMET (LPPC) · GAMET</div>', unsafe_allow_html=True)

# Query params -> ICAOs
try:
    q = st.query_params
    raw = q.get("icao", "")
    if isinstance(raw, list):
        raw = ",".join(raw)
except Exception:
    raw = ""
if not raw:
    raw = ",".join(DEFAULT_ICAOS)

# Controls
cc1, cc2 = st.columns([0.75, 0.25])
with cc1:
    icaos_input = st.text_input("ICAO list (comma-separated)", value=raw)
with cc2:
    if st.button("Refresh"):
        st.cache_data.clear()

icaos = [x.strip().upper() for x in icaos_input.split(",") if x.strip()]

# ---------- METAR/TAF (stacked cards) ----------
for icao in icaos:
    metar_dec = fetch_metar_decoded(icao)
    metar_raw = fetch_metar_raw(icao)
    taf_raw = fetch_taf_raw(icao)

    cat = (metar_dec or {}).get("flight_category", "").upper()
    klass = {"VFR": "vfr", "MVFR": "mvfr", "IFR": "ifr", "LIFR": "lifr"}.get(cat, "")
    badge = f'<span class="badge {klass}" title="{cat}">{cat}</span>' if klass else ""

    obs = zulu_plus_pt(parse_iso_utc((metar_dec or {}).get("observed")))

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<h3>{icao} {badge}' + (f'<span class="meta">Observed {obs}</span>' if obs else "") + '</h3>', unsafe_allow_html=True)
    st.markdown(f'<div class="monos"><strong>METAR</strong> {metar_raw or "—"}\n\n<strong>TAF</strong> {taf_raw or "—"}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# ---------- SIGMET LPPC ----------
st.markdown("### ⚠️ SIGMET (LPPC)")
sigs = fetch_sigmet_lppc()
if not sigs:
    st.write("—")
else:
    for s in sigs:
        st.markdown(f'<div class="card monos">{s}</div>', unsafe_allow_html=True)

# ---------- GAMET ----------
st.markdown("### 🛰️ GAMET")
gamet = load_gamet()
text = (gamet.get("text") or "").strip()
if text:
    st.markdown(f'<div class="card monos">{text}</div>', unsafe_allow_html=True)
else:
    st.write("—")






