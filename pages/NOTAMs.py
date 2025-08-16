from typing import Dict, Any, List
import streamlit as st, requests, json

st.set_page_config(page_title="NOTAMs", layout="wide")
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"],
[data-testid="stSidebarCollapseButton"] {
    display:none !important;
}
:root {
    --line:#e5e7eb;
    --muted:#6b7280;
    --card-bg:#ffffff;
    --border:#e0e0e0;
    --shadow:0 1px 4px rgba(0,0,0,0.05);
    --accent:#ea580c;
}
body { background-color: #f9fafb; }
.page-title {
    font-size: 2rem;
    font-weight: 800;
    margin-bottom: .25rem;
}
.subtle {
    color: var(--muted);
    margin-bottom: 1rem;
}
.card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    box-shadow: var(--shadow);
    margin-bottom: 1.25rem;
}
.monos {
    font-family: ui-monospace, Menlo, Consolas, monospace;
    white-space: pre-wrap;
    font-size: 0.95rem;
    color: #1f2937;
}
.updated-info {
    font-size: 0.9rem;
    color: var(--muted);
    margin-bottom: 2rem;
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

# Título
st.markdown('<div class="page-title">NOTAMs</div>', unsafe_allow_html=True)

# Inputs
col = st.columns([0.75, 0.25])
with col[0]:
    icaos_str = st.text_input("🛫 ICAOs (separados por vírgula)", value="LPSO, LPCB, LPEV")
with col[1]:
    if st.button("🔄 Atualizar"):
        st.cache_data.clear()

# Carregar dados
data = load_notams()
m = data.get("map") or {}
updated_utc = data.get("updated_utc")

# Mostrar info de atualização
if updated_utc:
    st.markdown(f'<div class="updated-info">Última atualização: {updated_utc} UTC</div>', unsafe_allow_html=True)

# Listar NOTAMs
for icao in [x.strip().upper() for x in icaos_str.split(",") if x.strip()]:
    st.subheader(f"📍 {icao}")
    items: List[str] = list((m.get(icao) or []))
    if not items:
        st.write("— Nenhum NOTAM disponível.")
    else:
        for n in items:
            st.markdown(f'<div class="card"><div class="monos">{n}</div></div>', unsafe_allow_html=True)




