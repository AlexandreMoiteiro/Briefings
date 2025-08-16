# pages/NOTAMs.py — mostra os NOTAMs guardados no Gist (UI/UX melhorado)
from __future__ import annotations
from typing import Dict, Any, List
import streamlit as st, requests, json
from datetime import datetime, timezone
import re

# ————————————————————————————————————————————————————————————————
# Configuração base
# ————————————————————————————————————————————————————————————————
st.set_page_config(page_title="NOTAMs", page_icon="🛫", layout="wide")

# ————————————————————————————————————————————————————————————————
# Estilos (dark/light) e componentes visuais
# ————————————————————————————————————————————————————————————————
st.markdown(
    """
<style>
:root{
  --bg: var(--background, #0b0f19);
  --card: rgba(255,255,255,.04);
  --muted:#6b7280;
  --line: rgba(148,163,184,.22);
  --accent: linear-gradient(90deg,#22d3ee, #a78bfa 40%, #f472b6);
}
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stSidebarCollapseButton"]{display:none!important}

/**** Header ****/
.app-header{display:flex;align-items:center;gap:.75rem;margin:0 0 .5rem}
.app-title{font-size:2rem;font-weight:900;letter-spacing:-.02em;line-height:1.1}
.app-title span{background:var(--accent);-webkit-background-clip:text;background-clip:text;color:transparent}
.app-subtle{color:var(--muted)}
.badge{display:inline-flex;align-items:center;gap:.4rem;padding:.25rem .5rem;border:1px solid var(--line);border-radius:999px;font-size:.775rem}
.badge .dot{width:.5rem;height:.5rem;border-radius:999px;background:#22d3ee;box-shadow:0 0 8px #22d3ee66}

/**** Controlo ****/
.toolbar{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:.25rem 0 1rem}
.toolbar .field{min-width:260px}

/**** Tabs ****/
.stTabs [data-baseweb="tab"]{font-weight:600}

/**** Cartões ****/
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.card{border:1px solid var(--line);border-radius:14px;padding:12px;background:var(--card);backdrop-filter:blur(6px)}
.card h4{margin:.2rem 0 .6rem;font-size:1rem}
.code{font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word}
.hr{height:1px;background:var(--line);margin:.5rem 0}

/**** Empty state ****/
.empty{border:1.5px dashed var(--line);border-radius:14px;padding:18px;text-align:center;color:var(--muted)}

/**** Pills de ICAO ****/
.pills{display:flex;flex-wrap:wrap;gap:6px;margin-top:.25rem}
.pill{border:1px solid var(--line);border-radius:999px;padding:.15rem .55rem;font-size:.8rem}

/**** Ajustes do Streamlit ****/
/* Ocultar linhas extra dos expanders e dar mais conforto */
details > summary{cursor:pointer}

</style>
""",
    unsafe_allow_html=True,
)

# ————————————————————————————————————————————————————————————————
# Helpers
# ————————————————————————————————————————————————————————————————

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


def fmt_updated(ts: str | None) -> str:
    if not ts:
        return "sem info"
    try:
        # lida com 'Z'
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        local = dt.astimezone()
        return local.strftime("%d %b %Y, %H:%M %Z")
    except Exception:
        return str(ts)


def normalize_icaos(raw: str) -> List[str]:
    # Aceita separação por vírgulas, espaços e quebras de linha
    parts = re.split(r"[\s,;]+", (raw or "").strip())
    icaos = [p.upper() for p in parts if p]
    # Limpa duplicados mantendo ordem
    seen = set()
    out: List[str] = []
    for x in icaos:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out

# ————————————————————————————————————————————————————————————————
# UI — Header
# ————————————————————————————————————————————————————————————————
with st.container():
    c1, c2 = st.columns([0.75, 0.25], vertical_alignment="center")
    with c1:
        st.markdown(
            '<div class="app-header">\n' \
            '  <div style="font-size:1.6rem">🛫</div>' \
            '  <div class="app-title">NOTAMs <span>Viewer</span></div>' \
            '</div>',
            unsafe_allow_html=True,
        )
        st.caption("Visualização rápida dos NOTAMs guardados no Gist.")
    with c2:
        if st.button("🔄 Atualizar", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

# ————————————————————————————————————————————————————————————————
# Controlo — filtros e entradas
# ————————————————————————————————————————————————————————————————
with st.container():
    st.markdown('<div class="toolbar">', unsafe_allow_html=True)
    icaos_default = "LPSO, LPCB, LPEV"
    icaos_str = st.text_input("ICAOs", value=icaos_default, placeholder="LPSO, LPPT, LPFR…", help="Separar por vírgulas, espaços ou linhas.", key="icaos", label_visibility="visible")
    search = st.text_input("Filtrar NOTAMs", value="", placeholder="palavra-chave…", help="Filtra pelo texto do NOTAM.", key="filter")
    compact = st.toggle("Vista compacta", value=False)
    st.markdown('</div>', unsafe_allow_html=True)

# ————————————————————————————————————————————————————————————————
# Dados
# ————————————————————————————————————————————————————————————————
data = load_notams()
m: Dict[str, List[str]] = data.get("map") or {}
updated_utc = data.get("updated_utc")

# Info topo: estado e hora
left, right = st.columns([0.7, 0.3])
with left:
    pills_html = ''.join([f'<span class="pill">{p}</span>' for p in normalize_icaos(icaos_str)])
    st.markdown(f"<div class='pills'>{pills_html}</div>", unsafe_allow_html=True)
with right:
    st.markdown(
        f"<div class='badge' style='justify-content:flex-end'><span class='dot'></span> Atualizado: {fmt_updated(updated_utc)}</div>",
        unsafe_allow_html=True,
    )

# ————————————————————————————————————————————————————————————————
# Renderização por ICAO em tabs
# ————————————————————————————————————————————————————————————————
icaos = normalize_icaos(icaos_str)

if not icaos:
    st.markdown('<div class="empty">Indique pelo menos um ICAO para começar.</div>', unsafe_allow_html=True)
else:
    # construir tabs com contagem
    labels = []
    datasets: List[List[str]] = []
    for icao in icaos:
        items = list((m.get(icao) or []))
        if search:
            s = search.lower().strip()
            items = [n for n in items if s in n.lower()]
        labels.append(f"{icao} ({len(items)})")
        datasets.append(items)

    if len(labels) == 1:
        # Sem tabs, só uma secção
        icao = icaos[0]
        items = datasets[0]
        st.markdown(f"#### {icao}")
        if not items:
            st.markdown('<div class="empty">Sem NOTAMs para mostrar.</div>', unsafe_allow_html=True)
        else:
            # download de todos para este ICAO
            joined = "\n\n".join(items)
            st.download_button(
                label="⬇️ Descarregar NOTAMs (.txt)",
                data=joined,
                file_name=f"{icao}_NOTAMs.txt",
                mime="text/plain",
            )
            if compact:
                for i, n in enumerate(items, 1):
                    st.code(n, language=None)
                    if i != len(items):
                        st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="grid">', unsafe_allow_html=True)
                for n in items:
                    st.markdown('<div class="card">', unsafe_allow_html=True)
                    # Expander se longo
                    if len(n) > 260:
                        with st.expander(n[:120] + "…", expanded=False):
                            st.markdown(f"<div class='code'>{n}</div>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div class='code'>{n}</div>", unsafe_allow_html=True)
                    st.markdown('</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
    else:
        tabs = st.tabs(labels)
        for idx, tab in enumerate(tabs):
            with tab:
                icao = icaos[idx]
                items = datasets[idx]
                if not items:
                    st.markdown('<div class="empty">Sem NOTAMs para mostrar.</div>', unsafe_allow_html=True)
                else:
                    joined = "\n\n".join(items)
                    st.download_button(
                        label=f"⬇️ Descarregar {icao} (.txt)",
                        data=joined,
                        file_name=f"{icao}_NOTAMs.txt",
                        mime="text/plain",
                        key=f"dl-{icao}",
                    )
                    if compact:
                        for i, n in enumerate(items, 1):
                            st.code(n, language=None)
                            if i != len(items):
                                st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
                    else:
                        st.markdown('<div class="grid">', unsafe_allow_html=True)
                        for n in items:
                            st.markdown('<div class="card">', unsafe_allow_html=True)
                            if len(n) > 260:
                                with st.expander(n[:120] + "…", expanded=False):
                                    st.markdown(f"<div class='code'>{n}</div>", unsafe_allow_html=True)
                            else:
                                st.markdown(f"<div class='code'>{n}</div>", unsafe_allow_html=True)
                            st.markdown('</div>', unsafe_allow_html=True)
                        st.markdown('</div>', unsafe_allow_html=True)

# ————————————————————————————————————————————————————————————————
# Mensagens de ajuda/erro
# ————————————————————————————————————————————————————————————————
if not notam_gist_config_ok():
    with st.container(border=True):
        st.error("⚙️ Falta configurar o acesso ao Gist (NOTAM_GIST_TOKEN, NOTAM_GIST_ID e NOTAM_GIST_FILENAME em st.secrets).", icon="⚠️")
        st.markdown(
            "- **NOTAM_GIST_TOKEN**: token pessoal do GitHub com acesso a gists.\n"
            "- **NOTAM_GIST_ID**: ID do gist que contém os NOTAMs.\n"
            "- **NOTAM_GIST_FILENAME**: nome do ficheiro JSON dentro do gist.")



