# pages/NOTAMs.py — mostra os NOTAMs guardados no Gist
# Redesign de UI/UX: cards, badges, toolbar fixa, estado vazio e filtro rápido
from __future__ import annotations
from typing import Dict, Any, List
import streamlit as st, requests, json
from datetime import datetime

# ——————————————————————————————————————————————————————————
# Configuração de página e estilos globais
# ——————————————————————————————————————————————————————————
st.set_page_config(page_title="NOTAMs", layout="wide")

st.markdown(
    """
<style>
/* Remover barra lateral */
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stSidebarCollapseButton"] { display:none !important; }

/* Variáveis de cor (adaptam-se a dark/light) */
:root { --line:#e5e7eb; --muted:#6b7280; --panel:#ffffff; --code:#f8fafc; --ring:#eef2ff; }
@media (prefers-color-scheme: dark){ :root{ --line:#1f2937; --muted:#9aa0a6; --panel:#0b0f19; --code:#0f1625; --ring:#0f172a; } }

/* Container mais estreito para melhor legibilidade */
.main .block-container { max-width: 1080px; padding-top: 0.5rem; }

/* Título e subtítulo */
.page-title{font-size:2rem;font-weight:800;margin:0 0 .25rem}
.subtle{color:var(--muted);margin-bottom:1rem}

/* Toolbar fixa no topo */
.toolbar{position:sticky;top:0;z-index:1000;background:var(--panel);border-bottom:1px solid var(--line);padding:.75rem 0 .5rem;margin-bottom:1rem}
.toolbar .row{display:flex;gap:.5rem;align-items:end}
.toolbar .row > div{flex:1}
.toolbar .btns{display:flex;gap:.5rem;justify-content:flex-end}
.btn{display:inline-flex;align-items:center;gap:.5rem;border:1px solid var(--line);background:var(--panel);padding:.5rem .75rem;border-radius:.75rem;font-weight:600}
.btn.primary{background:#111827;color:#fff;border-color:#111827}
@media (prefers-color-scheme: dark){ .btn.primary{background:#2563eb;border-color:#2563eb} }

/* Chips de navegação dos ICAOs */
.chips{display:flex;flex-wrap:wrap;gap:.5rem;margin:.25rem 0 1rem}
.chip{display:inline-block;padding:.375rem .625rem;border:1px solid var(--line);border-radius:999px;text-decoration:none;color:inherit}
.chip:hover{box-shadow:0 0 0 3px var(--ring)}

/* Card por aeródromo */
.card{border:1px solid var(--line);border-radius:1rem;padding:1rem;background:var(--panel);margin-bottom:1rem}
.card-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:.5rem}
.card-title{font-weight:800;font-size:1.125rem;letter-spacing:.5px}
.badge{font-size:.75rem;border:1px solid var(--line);border-radius:999px;padding:.125rem .5rem}

/* NOTAM individual */
.notam{font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;background:var(--code);border:1px dashed var(--line);padding:.75rem;border-radius:.75rem}
.rule{height:1px;background:var(--line);margin:.75rem 0}

/* Estados vazios */
.empty{border:1px dashed var(--line);border-radius:.75rem;padding:1rem;color:var(--muted);text-align:center}

/* Pequenos detalhes */
.kv{display:flex;gap:.5rem;align-items:center;color:var(--muted);font-size:.875rem}
.dot{width:.375rem;height:.375rem;background:var(--muted);border-radius:999px;display:inline-block}
</style>
""",
    unsafe_allow_html=True,
)

# ——————————————————————————————————————————————————————————
# Helpers
# ——————————————————————————————————————————————————————————

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


def parse_iso_utc(s: str | None) -> datetime | None:
    if not s: return None
    try:
        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def clean_icaos(raw: str) -> List[str]:
    icaos = [x.strip().upper() for x in (raw or "").split(",") if x.strip()]
    # Apenas códigos com 4 letras; únicos e na ordem fornecida
    seen = set(); out: List[str] = []
    for c in icaos:
        if len(c) == 4 and c.isalnum() and c not in seen:
            out.append(c); seen.add(c)
    return out

# ——————————————————————————————————————————————————————————
# Cabeçalho
# ——————————————————————————————————————————————————————————
st.markdown('<div class="page-title">NOTAMs</div>', unsafe_allow_html=True)

# Toolbar fixa com inputs
with st.container():
    st.markdown('<div class="toolbar">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([0.60, 0.25, 0.15])
    with c1:
        icaos_str = st.text_input(
            "ICAOs (separados por vírgulas)",
            value="LPSO, LPCB, LPEV",
            placeholder="Ex.: LPPT, LPPR, LPMR",
            help="Introduza códigos ICAO de 4 letras. Ex.: LPPT",
        )
    with c2:
        quick_filter = st.text_input(
            "Filtro rápido (texto)",
            value="",
            placeholder="Pesquisar nos textos dos NOTAMs…",
        )
    with c3:
        if st.button("Atualizar", type="primary", use_container_width=True):
            st.cache_data.clear()
    st.markdown('</div>', unsafe_allow_html=True)

# Subtítulo com metadados
_data = load_notams()
updated_dt = parse_iso_utc(_data.get("updated_utc"))
updated_str = updated_dt.strftime("%d %b %Y %H:%M UTC") if updated_dt else "—"

meta_cols = st.columns([0.75, 0.25])
with meta_cols[0]:
    st.markdown(
        f"<div class='subtle kv'><span class='dot'></span><span>Última atualização:</span> <b>{updated_str}</b></div>",
        unsafe_allow_html=True,
    )
with meta_cols[1]:
    total_icaos = len((_data.get("map") or {}).keys())
    st.markdown(
        f"<div class='subtle kv' style='justify-content:flex-end'>ICAOs disponíveis no Gist: <b>{total_icaos}</b></div>",
        unsafe_allow_html=True,
    )

# Aviso de configuração em falta
if not notam_gist_config_ok():
    st.markdown(
        """
<div class="empty">
<p><strong>Configuração do Gist em falta</strong></p>
<p>Defina <code>NOTAM_GIST_TOKEN</code>, <code>NOTAM_GIST_ID</code> e <code>NOTAM_GIST_FILENAME</code> em <em>st.secrets</em> para carregar dados.</p>
</div>
""",
        unsafe_allow_html=True,
    )

m: Dict[str, List[str]] = _data.get("map") or {}

# Chips de navegação para os ICAOs solicitados
icaos = clean_icaos(icaos_str)
if icaos:
    chips = "".join([f"<a class='chip' href='#icao-{c}'>{c}</a>" for c in icaos])
    st.markdown(f"<div class='chips'>{chips}</div>", unsafe_allow_html=True)

# Renderizar secções por ICAO
for icao in icaos:
    items: List[str] = list((m.get(icao) or []))

    # Aplicar filtro rápido (case-insensitive)
    if quick_filter:
        q = quick_filter.lower().strip()
        items = [n for n in items if q in n.lower()]

    count = len(items)
    st.markdown(
        f"""
<div class="card" id="icao-{icao}">
  <div class="card-hd">
    <div class="card-title">{icao}</div>
    <div class="badge">{count} NOTAM{'s' if count!=1 else ''}</div>
  </div>
""",
        unsafe_allow_html=True,
    )

    if not items:
        st.markdown("<div class='empty'>Sem NOTAMs para este ICAO {msg}</div>".format(
            msg=f"(filtrados por \"{quick_filter}\")" if quick_filter else ""
        ), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)  # fechar .card
        continue

    # Listar NOTAMs em blocos monoespaçados
    for idx, n in enumerate(items):
        st.markdown(f"<div class='notam'>{n}</div>", unsafe_allow_html=True)
        if idx < len(items) - 1:
            st.markdown("<div class='rule'></div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)  # fechar .card

# Rodapé leve
st.markdown(
    """
<div class="subtle" style="text-align:center;margin-top:1.25rem">
  Interface melhorada · sem rótulos “live/saved” · ⌘/Ctrl+K para pesquisar no browser
</div>
""",
    unsafe_allow_html=True,
)



