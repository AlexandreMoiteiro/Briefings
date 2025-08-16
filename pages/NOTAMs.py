from typing import Dict, Any, List
import streamlit as st, requests, json

# Configuração da página
st.set_page_config(page_title="NOTAMs", layout="wide")

# Estilo CSS customizado
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
:root {
    --line: #e5e7eb;
    --muted: #6b7280;
    --bg: #f9fafb;
    --notam-border: #d1d5db;
    --notam-bg: #ffffff;
    --notam-font: #111827;
}

body, .main {
    background-color: var(--bg);
}

.page-title {
    font-size: 2.5rem;
    font-weight: 800;
    margin: 0 0 1rem;
}

.subtle {
    color: var(--muted);
    margin-bottom: 1rem;
}

.monos {
    font-family: ui-monospace, Menlo, Consolas, monospace;
    white-space: pre-wrap;
    background: var(--notam-bg);
    border: 1px solid var(--notam-border);
    padding: 0.75rem 1rem;
    border-radius: 6px;
    color: var(--notam-font);
    margin-bottom: 1rem;
    font-size: 0.95rem;
}

input[type="text"] {
    background-color: #fff !important;
    padding: 0.5rem;
    font-size: 1rem;
}

hr {
    border: none;
    border-top: 1px solid var(--line);
    margin: 1rem 0;
}
</style>
""", unsafe_allow_html=True)

# Checa se as configurações do Gist estão ok
def notam_gist_config_ok() -> bool:
    token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("NOTAM_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
    return bool(token and gid and fn)

# Carrega os NOTAMs do Gist (cacheado)
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

# Título da página
st.markdown('<div class="page-title">NOTAMs</div>', unsafe_allow_html=True)

# Entrada de ICAOs e botão de refresh
col = st.columns([0.75, 0.25])
with col[0]:
    icaos_str = st.text_input("ICAOs (separados por vírgulas)", value="LPSO, LPCB, LPEV")
with col[1]:
    if st.button("🔄 Atualizar"):
        st.cache_data.clear()

# Carregamento dos dados
data = load_notams()
m = data.get("map") or {}

# Exibição dos NOTAMs
for icao in [x.strip().upper() for x in icaos_str.split(",") if x.strip()]:
    items: List[str] = list((m.get(icao) or []))
    with st.expander(f"📍 {icao} ({len(items)} NOTAM{'s' if len(items) != 1 else ''})", expanded=True):
        if not items:
            st.markdown('<div class="subtle">Nenhum NOTAM encontrado.</div>', unsafe_allow_html=True)
        else:
            for n in items:
                st.markdown(f'<div class="monos">{n}</div>', unsafe_allow_html=True)



