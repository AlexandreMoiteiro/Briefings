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
    token = avwx_headers()
    if not token:
        return []
    try:
        r = requests.get(
            f"https://avwx.rest/api/notam/{icao}",
            headers=token,
            params={"format": "json"},
            timeout=15,
        )
        if r.status_code == 204:
            return []
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

debug = st.checkbox("Show diagnostics", value=False)

col = st.columns([0.75,0.25])
with col[0]:
    icaos_str = st.text_input("ICAO list", value="LPSO, LPCB, LPEV")
with col[1]:
    if st.button("Refresh"):
        st.cache_data.clear()

for icao in [x.strip().upper() for x in icaos_str.split(",") if x.strip()]:
    st.markdown(f"### {icao}")
    items = fetch_notams(icao)
    if debug:
        st.caption(f"Fetched {len(items)} NOTAM(s) from AVWX for {icao}")
    if not items:
        st.write("—")
    else:
        for n in items:
            st.markdown(f'<div class="monos">{n}</div>', unsafe_allow_html=True)
            st.markdown("---")

