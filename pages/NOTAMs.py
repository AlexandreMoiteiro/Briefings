# pages/NOTAMs.py — mostra os NOTAMs guardados no Gist (sem rótulos “live/saved”)
from typing import Dict, Any, List
import streamlit as st, requests, json
from io import StringIO
from datetime import datetime

# ——————————————————————————————————————————————————————
# Configuração base
# ——————————————————————————————————————————————————————
st.set_page_config(
    page_title="NOTAMs",
    layout="wide",
    page_icon="🛫",
    menu_items={
        "About": "🛫 NOTAMs — visual limpo e minimalista, com cache de 60s."
    },
)

# Estilos globais (claro/escuro) + componentes
st.markdown("""
<style>
:root{
  --line:#e5e7eb; --muted:#6b7280; --bg:#ffffff; --card:#ffffff; --text:#0f172a;
}
html[data-theme="dark"]:root{
  --line:#2a2f37; --muted:#9aa3af; --bg:#0b0f16; --card:#0f141c; --text:#e5e7eb;
}
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stSidebarCollapseButton"] { display:none !important; }

.page-wrap{max-width:1320px;margin:0 auto;}
.page-title{font-size:2.1rem;font-weight:800;margin:0 0 .25rem;letter-spacing:-.02em;}
.subtle{color:var(--muted);margin-bottom:1rem;}
.row{display:flex;gap:.75rem;flex-wrap:wrap;align-items:end}
.row .col{flex:1 1 240px;min-width:240px}
.card{
  background:var(--card);
  border:1px solid var(--line);
  border-radius:16px;
  padding:16px 16px 12px;
  box-shadow:0 1px 1px rgba(0,0,0,.02), 0 8px 24px rgba(0,0,0,.04);
  margin-bottom:14px;
}
.card-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:.25rem}
.badge{
  display:inline-flex;align-items:center;gap:.5ch;
  border:1px solid var(--line);border-radius:999px;padding:.25rem .6rem;
  font-size:.82rem;font-weight:700;letter-spacing:.02em;
  background:linear-gradient(180deg, rgba(0,0,0,.04), transparent);
}
.badge.gray{opacity:.7}
.count{opacity:.85;font-weight:700}
.btns{display:flex;gap:.5rem;flex-wrap:wrap}
.btn{
  display:inline-flex;align-items:center;gap:.5ch;
  border:1px solid var(--line);border-radius:10px;padding:.35rem .7rem;
  background:transparent;cursor:pointer;font-size:.85rem;text-decoration:none;color:inherit;
}
.btn:hover{background:rgba(127,127,127,.08)}
.monos{font-family:ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;line-height:1.4}
.sep{border:none;border-top:1px dashed var(--line);margin:.6rem 0}
.empty{color:var(--muted);text-align:center;padding:.75rem 0;font-style:italic}
.hint{font-size:.84rem;color:var(--muted)}
.control-note{font-size:.82rem;color:var(--muted);margin:.25rem 0 0}
</style>
""", unsafe_allow_html=True)

# ——————————————————————————————————————————————————————
# Utilitários
# ——————————————————————————————————————————————————————
def notam_gist_config_ok() -> bool:
    token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("NOTAM_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
    return bool(token and gid and fn)

@st.cache_data(ttl=60, show_spinner=False)
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

def fmt_updated(ts: Any) -> str:
    if not ts: return "—"
    s = str(ts)
    # tenta ISO; cai para string crua caso falhe
    try:
        # remove Z se existir
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return s

def as_txt_block(icao: str, items: List[str]) -> str:
    buf = StringIO()
    buf.write(f"{icao}\n")
    buf.write("=" * len(icao) + "\n\n")
    if not items:
        buf.write("— Sem NOTAMs —\n")
    else:
        for i, n in enumerate(items, 1):
            buf.write(f"[{i}] {n}\n")
            buf.write("-" * 80 + "\n")
    return buf.getvalue()

def render_copy_button(key: str, text: str, label: str = "Copiar tudo"):
    # truque simples: text_area + copy do navegador
    with st.popover(label, use_container_width=False):
        st.text_area("Conteúdo", value=text, height=180, label_visibility="collapsed", key=f"ta_{key}")

# ——————————————————————————————————————————————————————
# Cabeçalho
# ——————————————————————————————————————————————————————
st.markdown('<div class="page-wrap">', unsafe_allow_html=True)
st.markdown('<div class="page-title">🛫 NOTAMs</div>', unsafe_allow_html=True)

data = load_notams()
m = data.get("map") or {}
updated_str = fmt_updated(data.get("updated_utc"))

total_icaos = len(m)
total_notams = sum(len(v or []) for v in m.values()) if m else 0
st.markdown(
    f'<div class="subtle">Atualização: <strong>{updated_str}</strong> · '
    f'Cache: 60s · ICAOs no Gist: <strong>{total_icaos}</strong> · NOTAMs: <strong>{total_notams}</strong></div>',
    unsafe_allow_html=True
)

# ——————————————————————————————————————————————————————
# Controlo (inputs)
# ——————————————————————————————————————————————————————
with st.container():
    st.markdown('<div class="row">', unsafe_allow_html=True)

    with st.container():
        st.markdown('<div class="col">', unsafe_allow_html=True)
        icaos_str = st.text_input(
            "ICAOs (separados por vírgulas)",
            value="LPSO, LPCB, LPEV",
            placeholder="Ex.: LPPT, LPPR, LPMR",
            help="Ordem é preservada. Espaços são ignorados."
        )
        st.markdown('</div>', unsafe_allow_html=True)

    with st.container():
        st.markdown('<div class="col">', unsafe_allow_html=True)
        filtro_txt = st.text_input(
            "Filtro de texto (opcional)",
            value="",
            placeholder="Filtra NOTAMs que contenham este texto (case-insensitive)."
        )
        st.markdown('<p class="control-note">Dica: use códigos (ex.: RWY, TWY, ILS, NAV) ou trechos de Q-line.</p>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with st.container():
        st.markdown('<div class="col">', unsafe_allow_html=True)
        colA, colB = st.columns([1,1], vertical_alignment="bottom")
        with colA:
            compact = st.checkbox("Modo compacto", value=False, help="Remove separadores entre NOTAMs.")
        with colB:
            if st.button("🔄 Atualizar", use_container_width=True, type="secondary"):
                st.cache_data.clear()
                st.toast("Cache limpa. A recarregar…", icon="🔄")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

# Aviso de configuração (se necessário)
if not notam_gist_config_ok():
    st.warning("Configuração do Gist em falta: defina `NOTAM_GIST_TOKEN`, `NOTAM_GIST_ID` e `NOTAM_GIST_FILENAME` em `st.secrets`.")
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# ——————————————————————————————————————————————————————
# Renderização por ICAO
# ——————————————————————————————————————————————————————
def normalize_icaos(raw: str) -> List[str]:
    return [x.strip().upper() for x in raw.split(",") if x.strip()]

icaos = normalize_icaos(icaos_str)

for icao in icaos:
    items: List[str] = list((m.get(icao) or []))
    if filtro_txt:
        ft = filtro_txt.lower()
        items = [n for n in items if ft in n.lower()]

    # Cabeçalho do cartão
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="card-head">', unsafe_allow_html=True)
    if items:
        st.markdown(f'<div class="badge">{icao} <span class="count">· {len(items)} NOTAM(s)</span></div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="badge gray">{icao} <span class="count">· 0</span></div>', unsafe_allow_html=True)

    # Botões de ação
    txt_block = as_txt_block(icao, items)
    st.markdown('<div class="btns">', unsafe_allow_html=True)
    render_copy_button(key=f"copy_{icao}", text=txt_block, label="📋 Copiar tudo")
    st.download_button("⬇️ Baixar .txt", data=txt_block, file_name=f"{icao}_NOTAMs.txt", mime="text/plain", use_container_width=False, key=f"dl_{icao}")
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)  # /card-head

    # Lista de NOTAMs
    if not items:
        st.markdown('<div class="empty">— Sem NOTAMs —</div>', unsafe_allow_html=True)
    else:
        for idx, n in enumerate(items):
            st.markdown(f'<div class="monos">{n}</div>', unsafe_allow_html=True)
            if not compact and idx < len(items)-1:
                st.markdown('<hr class="sep"/>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)  # /card

# Rodapé leve
st.markdown('<div class="hint">Dica: clique em “Atualizar” para limpar o cache (TTL 60s).</div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)  # /page-wrap


