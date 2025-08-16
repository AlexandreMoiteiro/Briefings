from typing import Dict, Any, List
from datetime import datetime
import streamlit as st, requests, json, re

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
    --active: #10b981;
    --expired: #ef4444;
    --nill: #9ca3af;
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

.badge {
    font-weight: bold;
    display: inline-block;
    margin-bottom: 0.5rem;
    padding: 0.2rem 0.5rem;
    border-radius: 0.375rem;
    font-size: 0.75rem;
}

.badge-active {
    background-color: var(--active);
    color: white;
}

.badge-expired {
    background-color: var(--expired);
    color: white;
}

.badge-nill {
    background-color: var(--nill);
    color: white;
}
</style>
""", unsafe_allow_html=True)

# Verifica se as configs do Gist estão definidas
def notam_gist_config_ok() -> bool:
    token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("NOTAM_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
    return bool(token and gid and fn)

# Carrega os NOTAMs (cache de 60 segundos)
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

# Extrai datas FROM / TO
def parse_notam_dates(text: str):
    match = re.search(r"FROM:\s*(\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+\d{4}\s+\d{2}:\d{2})\s*TO:\s*([A-Za-z0-9:\s]+)", text)
    if not match:
        return None, None

    from_str, to_str = match.group(1), match.group(2).strip()

    # Limpa sufixos (st/nd/rd/th)
    from_str = re.sub(r"(st|nd|rd|th)", "", from_str)
    to_str = re.sub(r"(st|nd|rd|th)", "", to_str)

    # Remove zonas horárias (UTC, EST, etc.)
    to_str = re.sub(r"\b(UTC|EST|EDT|WEST|Z)\b", "", to_str, flags=re.IGNORECASE).strip()
    to_str = re.sub(r"\s+", " ", to_str).strip()

    try:
        from_dt = datetime.strptime(from_str, "%d %b %Y %H:%M")
    except ValueError:
        from_dt = None

    if to_str.upper() == "PERM":
        to_dt = "PERM"
    else:
        try:
            to_dt = datetime.strptime(to_str, "%d %b %Y %H:%M")
        except ValueError:
            to_dt = None

    return from_dt, to_dt

# Verifica se o NOTAM está ativo
def is_active(from_dt, to_dt):
    now = datetime.utcnow()
    if to_dt == "PERM":
        return True
    if isinstance(to_dt, datetime):
        return to_dt > now
    return False

# Título
st.markdown('<div class="page-title">NOTAMs</div>', unsafe_allow_html=True)

# Entrada de ICAOs e botão Atualizar
col = st.columns([0.75, 0.25])
with col[0]:
    icaos_str = st.text_input("ICAOs (separados por vírgulas)", value="LPSO, LPCB, LPEV")
with col[1]:
    if st.button("🔄 Atualizar"):
        st.cache_data.clear()

# Carrega dados
data = load_notams()
m = data.get("map") or {}

# Exibe NOTAMs
for icao in [x.strip().upper() for x in icaos_str.split(",") if x.strip()]:
    items: List[str] = list((m.get(icao) or []))
    with st.expander(f"📍 {icao} ({len(items)} NOTAM{'s' if len(items) != 1 else ''})", expanded=True):
        if not items:
            st.markdown('<div class="subtle">Nenhum NOTAM encontrado.</div>', unsafe_allow_html=True)
        else:
            for n in items:
                notam_text = n.strip()
                # NOTAM NILL
                if notam_text.upper() == "NILL":
                    badge_html = '<span class="badge badge-nill">🚫 Sem NOTAMs</span>'
                    st.markdown(f'<div class="monos">{badge_html}<br>{notam_text}</div>', unsafe_allow_html=True)
                    continue

                from_dt, to_dt = parse_notam_dates(notam_text)
                active = is_active(from_dt, to_dt)
                status = "🟢 Ativo" if active else "🔴 Expirado"
                badge_class = "badge-active" if active else "badge-expired"
                badge_html = f'<span class="badge {badge_class}">{status}</span>'

                st.markdown(f'<div class="monos">{badge_html}<br>{notam_text}</div>', unsafe_allow_html=True)
