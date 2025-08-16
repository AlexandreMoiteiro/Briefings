# pages/NOTAMs.py — NOTAMs do Gist com UI clean e segregada por ICAO
from __future__ import annotations
from typing import Dict, Any, List, Tuple
from datetime import datetime, timezone
import streamlit as st, requests, json, io

# ---------- Config & Estilos ----------
st.set_page_config(page_title="NOTAMs", layout="wide")
st.markdown("""
<style>
/* Ocultar sidebar */
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stSidebarCollapseButton"] { display:none !important; }

/* Tokens de design */
:root{
  --line:#e5e7eb;
  --muted:#6b7280;
  --fg:#0f172a;
  --bg:#ffffff;
  --soft:#f8fafc;
  --accent:#2563eb;
}
@media (prefers-color-scheme: dark){
  :root{
    --line:#2a2f3a;
    --muted:#9aa2b2;
    --fg:#e5e7eb;
    --bg:#0b0f19;
    --soft:#0f1524;
    --accent:#3b82f6;
  }
}

/* Tipografia e utilitários */
.page-title{font-size:2rem;font-weight:800;margin:0 0 .25rem;color:var(--fg)}
.subtle{color:var(--muted);margin-bottom:.75rem}
.monos{font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap}

/* Layout do topo */
.topbar{display:flex;gap:.75rem;flex-wrap:wrap;align-items:flex-end;margin:.5rem 0 1rem}

/* Cards de ICAO */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px;margin-top:.5rem}
.card{background:var(--bg);border:1px solid var(--line);border-radius:14px;padding:14px}
.card h3{display:flex;align-items:center;justify-content:space-between;margin:.25rem 0 .5rem;font-size:1.05rem}
.badge{display:inline-flex;align-items:center;gap:.35rem;font-size:.8rem;border:1px solid var(--line);padding:.1rem .5rem;border-radius:999px;color:var(--muted)}
.actions{display:flex;gap:.5rem;flex-wrap:wrap}

/* Itens NOTAM */
.notam{background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:.6rem .7rem;margin:.4rem 0}
.sep{height:8px}

/* Barra info */
.info{display:flex;gap:1rem;flex-wrap:wrap;align-items:center;margin:.25rem 0 1rem}
.dot{width:6px;height:6px;border-radius:999px;background:var(--muted);display:inline-block;margin:0 .5rem}
</style>
""", unsafe_allow_html=True)

# ---------- Helpers ----------
def notam_gist_config_ok() -> bool:
    token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("NOTAM_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
    return bool(token and gid and fn)

@st.cache_data(ttl=60)
def load_notams() -> Dict[str, Any]:
    """Lê o Gist e devolve {'map': {ICAO:[...strings...]}, 'updated_utc': str|None}."""
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

def parse_icaos(raw: str) -> List[str]:
    return sorted({x.strip().upper() for x in (raw or "").split(",") if x.strip()})

def fmt_updated(ts: Any) -> str:
    if not ts:
        return "—"
    try:
        # tenta ISO 8601; se vier só com 'Z', converte
        s = str(ts).replace("Z","+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%d %b %Y %H:%M UTC")
    except Exception:
        return str(ts)

def join_notams(lines: List[str]) -> str:
    return "\n\n".join(lines or [])

# ---------- UI ----------
st.markdown('<div class="page-title">NOTAMs</div>', unsafe_allow_html=True)

data = load_notams()
m: Dict[str, List[str]] = data.get("map") or {}
updated_str = fmt_updated(data.get("updated_utc"))

with st.container():
    # Barra de controlo
    st.markdown(
        f"""
        <div class="info">
          <span class="subtle">Atualizado: <strong>{updated_str}</strong></span>
          <span class="dot"></span>
          <span class="subtle">Fonte: Gist (cache 60s)</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    colA, colB, colC = st.columns([0.55, 0.25, 0.20])
    with colA:
        icaos_str = st.text_input("ICAOs (separados por vírgula)", value="LPSO, LPCB, LPEV", help="Ex.: LPPT, LPFR, LPPR")
    with colB:
        filter_str = st.text_input("Filtro de texto (opcional)", value="", help="Filtra NOTAMs que contenham este texto")
    with colC:
        refrescar = st.button("🔄 Refresh", help="Limpa a cache e volta a carregar")

    if refrescar:
        st.cache_data.clear()

# Build lista de ICAOs e exportações
icaos = parse_icaos(icaos_str)
total_por_icao: List[Tuple[str,int]] = []
for icao in icaos:
    items = list(m.get(icao) or [])
    if filter_str:
        f = filter_str.lower()
        items = [n for n in items if f in n.lower()]
    total_por_icao.append((icao, len(items)))

# Export global (depois de filtrar)
todos_txt = []
for icao, cnt in total_por_icao:
    items = list(m.get(icao) or [])
    if filter_str:
        f = filter_str.lower()
        items = [n for n in items if f in n.lower()]
    if items:
        todos_txt.append(f"### {icao}\n" + join_notams(items))
blob = "\n\n---\n\n".join(todos_txt) if todos_txt else ""
if blob:
    st.download_button(
        "⬇️ Exportar tudo (.txt)",
        data=blob.encode("utf-8"),
        file_name="notams.txt",
        mime="text/plain",
        use_container_width=True,
    )

# Grid de cards
st.markdown('<div class="grid">', unsafe_allow_html=True)
for icao, count in total_por_icao:
    items = list(m.get(icao) or [])
    if filter_str:
        f = filter_str.lower()
        items = [n for n in items if f in n.lower()]

    # Cabeçalho do card
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(
        f"""<h3>
            <span>{icao}</span>
            <span class="badge">{count} NOTAM{'s' if count!=1 else ''}</span>
        </h3>""",
        unsafe_allow_html=True,
    )

    # Ações do card
    with st.container():
        col1, col2 = st.columns([0.6, 0.4])
        with col1:
            st.caption("Resultados listados abaixo · clique para copiar do bloco")
        with col2:
            if items:
                st.download_button(
                    "Guardar .txt",
                    data=join_notams(items).encode("utf-8"),
                    file_name=f"{icao}_notams.txt",
                    mime="text/plain",
                    key=f"dl-{icao}",
                    use_container_width=True,
                )
            else:
                st.button("Sem dados", disabled=True, key=f"nodata-{icao}", use_container_width=True)

    # Lista de NOTAMs
    if not items:
        st.write("—")
    else:
        for idx, n in enumerate(items, start=1):
            st.markdown(f'<div class="notam monos">{n}</div>', unsafe_allow_html=True)
            st.markdown('<div class="sep"></div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)  # /card
st.markdown('</div>', unsafe_allow_html=True)  # /grid



