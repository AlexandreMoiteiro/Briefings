# pages/NOTAMs.py — AVWX (NOTAMs)
# -------------------------------
from typing import Dict, Any, List
import streamlit as st, requests

st.set_page_config(page_title="NOTAMs (Live)", layout="wide")
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
:root { --line:#e5e7eb; --muted:#6b7280; }
.page-title{font-size:2rem;font-weight:800;margin:0 0 .25rem}
.subtle{color:var(--muted);margin-bottom:.75rem}
.row{padding:10px 0 14px;border-bottom:1px solid var(--line)}
.monos{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.95rem;white-space:pre-wrap}
</style>
""", unsafe_allow_html=True)

def avwx_headers() -> Dict[str,str]:
    token = (st.secrets.get("AVWX_TOKEN") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}

@st.cache_data(ttl=120)
def fetch_notams(icao: str) -> List[str]:
    """Fetch NOTAMs via AVWX. Returns list of raw strings."""
    try:
        if not avwx_headers():
            return []
        r = requests.get(
            f"https://avwx.rest/api/notam/{icao}",
            headers=avwx_headers(),
            params={"format": "json"},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json() or []
        out: List[str] = []
        for it in j:
            raw = (it.get("raw") or it.get("text") or it.get("notam") or "").strip()
            if raw:
                out.append(raw)
        return out
    except Exception:
        return []

st.markdown('<div class="page-title">NOTAMs (Live)</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Type ICAOs (comma-separated) and press Refresh</div>', unsafe_allow_html=True)

col = st.columns([0.75,0.25])
with col[0]:
    icaos_str = st.text_input("ICAO list", value="LPSO, LPCB, LPEV")
with col[1]:
    if st.button("Refresh"):
        st.cache_data.clear()

for icao in [x.strip().upper() for x in icaos_str.split(",") if x.strip()]:
    st.markdown(f"### {icao}")
    items = fetch_notams(icao)
    if not items:
        st.write("—")
    else:
        for n in items:
            st.markdown(f'<div class="monos">{n}</div>', unsafe_allow_html=True)
            st.markdown("---")


# -----------------------------------------------
# pages/Weather.py — CheckWX primary + AVWX fallback
# -----------------------------------------------
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

DEFAULT_ICAOS = ["LPPT","LPBJ","LEBZ"]

def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

def avwx_headers() -> Dict[str,str]:
    token = (st.secrets.get("AVWX_TOKEN") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}

# ---- helpers ----

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
    d_pt = d_utc.astimezone(ZoneInfo("Europe/Lisbon"))
    return f"{d_utc.strftime('%Y-%m-%d %H:%MZ')} ({d_pt.strftime('%H:%M')} Portugal)"

