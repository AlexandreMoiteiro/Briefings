from typing import Dict, Any, List
import streamlit as st, requests, json

st.set_page_config(page_title="NOTAMs", layout="wide")

# CSS PERSONALIZADO
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"],
[data-testid="stSidebarCollapseButton"] {
    display: none !important;
}
:root {
    --border: #e5e7eb;
    --muted: #6b7280;
    --bg-card: #f9f9f9;
    --shadow: 0 2px 6px rgba(0,0,0,0.05);
}
html, body {
    font-family: "Segoe UI", sans-serif;
}
.page-title {
    font-size: 2.25rem;
    font-weight: 800;
    margin-bottom: 0.25rem;
}
.subtle {
    color: var(--muted);
    margin-bottom: 1.25rem;
}
.card {
    background-color: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem 1.25rem;
    box-shadow: var(--shadow);
    margin-bottom: 2rem;
}
.card h3 {
    margin: 0 0 0.75rem;
}
.monos {
    font-family: ui-monospace, Menlo, Consolas, monospace;
    background: #ffffff;
    padding: 0.5rem;
    border-radius: 6px;
    white-space: pre-wrap;
    border: 1px solid var(--border);
}
.input-box input {
    padding: 0.5rem !important;
    border-radius: 8px !important;
}
button[kind="secondary"] {
    border-radius: 8px !important;
    padding: 0.4rem 1rem !important;
}
</style>
""", unsafe_allow_html=True)

# CONFIG CHECK
def notam_gist_config_ok() -> bool:
    token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("NOTAM_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
    return bool(token and gid and fn)

# LOAD FUNCTION
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
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        r.raise_for_status()
        files = r.json().get("files", {})
        obj = files.get(fn) or {}
        content = (obj.get("content") or "").strip()
        if not content:
            return {"map": {}, "updated_utc": None}
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

# TÍTULO
st.markdown('<div class="page-title">NOTAMs</div>', unsafe_allow_html=True)

# ENTRADA E REFRESH
col1, col2 = st.columns([0.8, 0.2])
with col1:
    icaos_str = st.text_input("🔎 Buscar ICAOs (ex: LPSO, LPCB, LPEV)", value="LPSO, LPCB, LPEV", key="icao_input")
with col2:
    if st.button("🔄 Atualizar"):
        st.cache_data.clear()

# DADOS
data = load_notams()
m = data.get("map") or {}

# DATA DE ATUALIZAÇÃO
if data.get("updated_utc"):
    st.markdown(f"<div class='subtle'>Última atualização: {data['updated_utc']}</div>", unsafe_allow_html=True)

# EXIBIÇÃO POR ICAO
for icao in [x.strip().upper() for x in icaos_str.split(",") if x.strip()]:
    items: List[str] = list((m.get(icao) or []))
    st.markdown(f'<div class="card"><h3>{icao}</h3>', unsafe_allow_html=True)
    if not items:
        st.markdown('<div class="subtle">Nenhum NOTAM disponível.</div></div>', unsafe_allow_html=True)
    else:
        for n in items:
            st.markdown(f'<div class="monos">{n}</div><br>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)



