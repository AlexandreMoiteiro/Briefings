
from typing import Dict, Any, List
import streamlit as st, requests, json

st.set_page_config(page_title="NOTAMs", layout="wide")

# 💄 CSS Style
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stSidebarCollapseButton"] {
    display:none !important;
}
:root {
    --line:#e5e7eb;
    --muted:#6b7280;
    --bg-section:#f9fafb;
    --border:#d1d5db;
    --accent:#f97316;
}
.page-title {
    font-size:2.5rem;
    font-weight:700;
    margin-bottom:0.25rem;
    color:#111827;
}
.subtle {
    color:var(--muted);
    margin-bottom:1rem;
    font-size:0.9rem;
}
.notam-box {
    background-color: white;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1rem;
    box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}
.icao-section {
    background-color: var(--bg-section);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 2rem;
}
.monos {
    font-family: ui-monospace, Menlo, Consolas, monospace;
    white-space: pre-wrap;
    color: #1f2937;
    font-size: 0.95rem;
    margin-bottom: 0.5rem;
}
.input-row {
    display: flex;
    gap: 1rem;
    margin-top: 1rem;
    margin-bottom: 2rem;
}
</style>
""", unsafe_allow_html=True)

# ✅ Functions
def notam_gist_config_ok() -> bool:
    token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("NOTAM_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
    return bool(token and gid and fn)

@st.cache_data(ttl=60)
def load_notams() -> Dict[str, Any]:
    if not notam_gist_config_ok():
        return {"map": {}, "updated_utc": None}
    try:
        token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
        gid   = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
        fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
        r = requests.get(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept":"application/vnd.github+json"},
            timeout=10,
        )
        r.raise_for_status()
        files = r.json().get("files", {})
        obj = files.get(fn) or {}
        content = (obj.get("content") or "").strip()
        if not content: return {"map": {}, "updated_utc": None}
        js = json.loads(content)
        if isinstance(js, dict) and "map" in js:
            return {"map": js.get("map") or {}, "updated_utc": js.get("updated_utc")}
        if isinstance(js, dict):
            upd = js.get("updated_utc") if "updated_utc" in js else None
            m = {k: v for k, v in js.items() if isinstance(v, list)}
            return {"map": m, "updated_utc": upd}
        return {"map": {}, "updated_utc": None}
    except Exception:
        return {"map": {}, "updated_utc": None}

# ✅ Title
st.markdown('<div class="page-title">NOTAMs</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Display of NOTAMs stored in the configured GitHub Gist</div>', unsafe_allow_html=True)

# ✅ Input section
st.markdown('<div class="input-row">', unsafe_allow_html=True)
icaos_str = st.text_input("Enter ICAO codes (comma-separated)", value="LPSO, LPCB, LPEV", label_visibility="collapsed")
if st.button("🔄 Refresh"):
    st.cache_data.clear()
st.markdown('</div>', unsafe_allow_html=True)

# ✅ Display NOTAMs
data = load_notams()
m = data.get("map") or {}

for icao in [x.strip().upper() for x in icaos_str.split(",") if x.strip()]:
    st.markdown(f'<div class="icao-section"><h4>{icao}</h4>', unsafe_allow_html=True)
    items: List[str] = list((m.get(icao) or []))
    if not items:
        st.write("— No NOTAMs found —")
    else:
        for n in items:
            st.markdown(f'<div class="notam-box monos">{n}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
