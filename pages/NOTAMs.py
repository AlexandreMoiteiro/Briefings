# pages/NOTAMs.py — mostra os NOTAMs guardados no Gist (sem rótulos “live/saved”)
from typing import Dict, Any, List
import streamlit as st, requests, json

st.set_page_config(page_title="NOTAMs", layout="wide")

# Estilo customizado: profissional e clean
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stSidebarCollapseButton"] {
    display: none !important;
}
:root {
    --line: #e5e7eb;
    --muted: #6b7280;
    --card-bg: #f9fafb;
    --accent: #ea580c;
    --text: #111827;
    --monospace: ui-monospace, Menlo, Consolas, monospace;
}
html, body, [class*="css"] {
    font-family: 'Segoe UI', sans-serif;
}
.page-title {
    font-size: 2.25rem;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 0.25rem;
}
.subtle {
    color: var(--muted);
    margin-bottom: 1.25rem;
}
.notam-card {
    background: var(--card-bg);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 1.5rem;
}
.icao-header {
    font-size: 1.25rem;
    font-weight: 600;
    color: var(--accent);
    margin-bottom: 0.5rem;
}
.monos {
    font-family: var(--monospace);
    white-space: pre-wrap;
    font-size: 0.95rem;
    color: var(--text);
}
.timestamp {
    font-size: 0.875rem;
    color: var(--muted);
    margin-top: -0.5rem;
    margin-bottom: 1.5rem;
}
</style>
""", unsafe_allow_html=True)

# Função para checar se configuração está correta
def notam_gist_config_ok() -> bool:
    token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
    fn = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
    return bool(token and gid and fn)

# Função para carregar NOTAMs
@st.cache_data(ttl=60)
def load_notams() -> Dict[str, Any]:
    if not notam_gist_config_ok():
        return {"map": {}, "updated_utc": None}
    try:
        token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
        gid = (st.secrets.get("NOTAM_GIST_ID") or st.secrets.get("GIST_ID") or "").strip()
        fn = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
        r = requests.get(
            f"https://api.github.com/gists/{gid}",
            headers={"Authorization": f"token {token}", "Accept":"application/vnd.github+json"},
            timeout=10,
        )
        r.raise_for_status()
        files = r.json().get("files", {})
        content = (files.get(fn) or {}).get("content", "").strip()
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
st.markdown('<div class="page-title">NOTAMs</div>', unsafe_allow_html=True)

# Entrada de ICAOs e botão de refresh
col = st.columns([0.75, 0.25])
with col[0]:
    icaos_str = st.text_input("Aeroportos (ICAOs separados por vírgula)", value="LPSO, LPCB, LPEV")
with col[1]:
    if st.button("Atualizar"):
        st.cache_data.clear()

# Carregamento dos dados
data = load_notams()
m = data.get("map") or {}
updated = data.get("updated_utc")
if updated:
    st.markdown(f'<div class="timestamp">Última atualização: {updated} UTC</div>', unsafe_allow_html=True)

# Exibição dos NOTAMs por ICAO
icaos = [x.strip().upper() for x in icaos_str.split(",") if x.strip()]
for icao in icaos:
    st.markdown(f'<div class="notam-card"><div class="icao-header">{icao}</div>', unsafe_allow_html=True)
    items: List[str] = list((m.get(icao) or []))
    if not items:
        st.write("— Nenhum NOTAM disponível —")
    else:
        for n in items:
            st.markdown(f'<div class="monos">{n}</div><hr>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
