# pages/NOTAMs.py — Visual aprimorado para exibição de NOTAMs
from typing import Dict, Any, List
import streamlit as st, requests, json

st.set_page_config(page_title="NOTAMs", layout="wide")
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stSidebarCollapseButton"] {
    display: none !important;
}
:root {
    --line: #e5e7eb;
    --muted: #6b7280;
    --bg-light: #f9fafb;
    --card-bg: #ffffff;
    --border-color: #e0e0e0;
    --shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}
body {
    background-color: var(--bg-light);
}
.page-title {
    font-size: 2.2rem;
    font-weight: 700;
    margin-bottom: 1rem;
    color: #111827;
}
.subtle {
    color: var(--muted);
    margin-bottom: 0.5rem;
}
.input-row {
    background: var(--card-bg);
    padding: 1rem;
    border-radius: 8px;
    box-shadow: var(--shadow);
    margin-bottom: 1.5rem;
}
.card {
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    margin-bottom: 1rem;
    box-shadow: var(--shadow);
}
.card pre {
    font-family: ui-monospace, Menlo, Consolas, monospace;
    white-space: pre-wrap;
    font-size: 0.95rem;
}
.icao-title {
    font-size: 1.4rem;
    font-weight: 600;
    margin-top: 2rem;
    margin-bottom: 0.5rem;
    color: #1f2937;
    border-bottom: 1px solid var(--line);
    padding-bottom: 0.25rem;
}
</style>
""", unsafe_allow_html=True)

def notam_gist_config_ok() -> bool:
    token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
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

# Título da página
st.markdown('<div class="page-title">🛬 NOTAMs Viewer</div>', unsafe_allow_html=True)

# Inputs do usuário
with st.container():
    st.markdown('<div class="input-row">', unsafe_allow_html=True)
    col1, col2 = st.columns([0.8, 0.2])
    with col1:
        icaos_str = st.text_input("ICAOs (separados por vírgula)", value="LPSO, LPCB, LPEV")
    with col2:
        if st.button("🔄 Atualizar"):
            st.cache_data.clear()
    st.markdown('</div>', unsafe_allow_html=True)

# Carregar NOTAMs
data = load_notams()
m = data.get("map") or {}

# Exibir NOTAMs ICAO por ICAO
for icao in [x.strip().upper() for x in icaos_str.split(",") if x.strip()]:
    st.markdown(f'<div class="icao-title">{icao}</div>', unsafe_allow_html=True)
    items: List[str] = list((m.get(icao) or []))
    if not items:
        st.info("Nenhum NOTAM disponível.")
    else:
        for n in items:
            st.markdown(f'<div class="card"><pre>{n}</pre></div>', unsafe_allow_html=True)



