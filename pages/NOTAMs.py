# pages/NOTAMs.py — Visualização estilizada dos NOTAMs
from typing import Dict, Any, List
import streamlit as st, requests, json

st.set_page_config(page_title="NOTAMs", layout="wide")

# === ESTILOS CSS ===
st.markdown("""
<style>
/* Oculta a sidebar */
[data-testid="stSidebar"], [data-testid="stSidebarNav"],
[data-testid="stSidebarCollapseButton"] { display: none !important; }

/* Cores e variáveis */
:root {
  --line: #e5e7eb;
  --muted: #6b7280;
  --primary: #f97316;
  --background: #f9fafb;
  --card-bg: #ffffff;
  --card-border: #e5e7eb;
}

/* Tipografia */
.page-title {
  font-size: 2.25rem;
  font-weight: 800;
  margin: 0 0 .75rem;
  color: #111827;
}
.subtle {
  color: var(--muted);
  margin-bottom: 0.75rem;
  font-size: 0.95rem;
}
.monos {
  font-family: ui-monospace, Menlo, Consolas, monospace;
  white-space: pre-wrap;
  color: #1f2937;
  font-size: 0.93rem;
  line-height: 1.4;
}

/* Card NOTAM */
.notam-card {
  background-color: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 0.5rem;
  padding: 1rem;
  margin-bottom: 1rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}

/* ICAO title */
.icao-title {
  font-size: 1.25rem;
  font-weight: 600;
  margin-top: 1.5rem;
  margin-bottom: 0.5rem;
  color: #1f2937;
}
</style>
""", unsafe_allow_html=True)

# === FUNÇÕES AUXILIARES ===
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

# === TÍTULO DA PÁGINA ===
st.markdown('<div class="page-title">NOTAMs Viewer</div>', unsafe_allow_html=True)

# === INPUT ICAOs + REFRESH ===
col = st.columns([0.75, 0.25])
with col[0]:
    icaos_str = st.text_input("ICAOs (separados por vírgula)", value="LPSO, LPCB, LPEV")
with col[1]:
    if st.button("🔁 Atualizar"):
        st.cache_data.clear()

# === CARREGAMENTO E EXIBIÇÃO DOS NOTAMs ===
data = load_notams()
m = data.get("map") or {}

for icao in [x.strip().upper() for x in icaos_str.split(",") if x.strip()]:
    st.markdown(f'<div class="icao-title">{icao}</div>', unsafe_allow_html=True)
    items: List[str] = list((m.get(icao) or []))
    if not items:
        st.markdown('<div class="subtle">Nenhum NOTAM disponível.</div>', unsafe_allow_html=True)
    else:
        for n in items:
            st.markdown(f'''
<div class="notam-card">
  <div class="monos">{n}</div>
</div>
''', unsafe_allow_html=True)

