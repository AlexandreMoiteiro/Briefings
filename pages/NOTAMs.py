# pages/NOTAMs.py
# NOTAMs live simples (CheckWX)

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

def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

@st.cache_data(ttl=120)
def fetch_notams(icao: str) -> List[str]:
    try:
        r = requests.get(f"https://api.checkwx.com/notam/{icao}", headers=cw_headers(), timeout=15)
        r.raise_for_status()
        j = r.json()
        arr = j.get("data", []) if isinstance(j, dict) else (j or [])
        out: List[str] = []
        for it in arr:
            if isinstance(it, str):
                out.append(it.strip())
            elif isinstance(it, dict):
                raw = (it.get("raw") or it.get("text") or it.get("notam") or it.get("message") or "")
                if raw: out.append(str(raw).strip())
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
