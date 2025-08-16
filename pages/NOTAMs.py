from typing import Dict, Any, List
from datetime import datetime
import streamlit as st, requests, json, re

# --------------------------- PAGE CONFIG ---------------------------

st.set_page_config(page_title="NOTAMs", layout="wide")

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
body, .main { background-color: var(--bg); }
.page-title { font-size: 2.5rem; font-weight: 800; margin: 0 0 1rem; }
.subtle { color: var(--muted); margin-bottom: 1rem; }
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
.badge-active { background-color: var(--active); color: white; }
.badge-expired { background-color: var(--expired); color: white; }
.badge-nill { background-color: var(--nill); color: white; }
</style>
""", unsafe_allow_html=True)

# --------------------------- GIST CONFIG ---------------------------

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

# --------------------------- DATE PARSING ---------------------------

def parse_notam_dates(text: str):
    match = re.search(r"FROM:\s*(.*?)\s*TO:\s*(.*?)($|\n)", text, re.IGNORECASE)
    if not match:
        return None, None

    from_raw, to_raw = match.group(1), match.group(2)

    def clean_date_str(s: str) -> str:
        s = re.sub(r'\b(\d{1,2})(st|nd|rd|th)\b', r'\1', s)
        s = re.sub(r"\b(UTC|EST|EDT|WEST|CEST|GMT|Z)\b", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    from_str = clean_date_str(from_raw)
    to_str = clean_date_str(to_raw)

    try:
        from_dt = datetime.strptime(from_str, "%d %b %Y %H:%M")
    except Exception:
        from_dt = None

    if to_str.upper() == "PERM":
        to_dt = "PERM"
    else:
        try:
            to_dt = datetime.strptime(to_str, "%d %b %Y %H:%M")
        except Exception:
            to_dt = None

    return from_dt, to_dt

def is_active(from_dt, to_dt):
    now = datetime.utcnow()
    if to_dt == "PERM":
        return True
    if isinstance(to_dt, datetime):
        return to_dt > now
    return False

# --------------------------- INTERFACE ---------------------------

st.markdown('<div class="page-title">NOTAMs Viewer</div>', unsafe_allow_html=True)

col = st.columns([0.75, 0.25])
with col[0]:
    icaos_str = st.text_input("ICAO codes (comma-separated)", value="LPSO, LPCB, LPEV")
with col[1]:
    if st.button("🔄 Refresh"):
        st.cache_data.clear()

data = load_notams()
m = data.get("map") or {}

for icao in [x.strip().upper() for x in icaos_str.split(",") if x.strip()]:
    items: List[str] = list((m.get(icao) or []))
    with st.expander(f"📍 {icao} ({len(items)} NOTAM{'s' if len(items) != 1 else ''})", expanded=True):
        if not items:
            st.markdown('<div class="subtle">No NOTAMs found for this ICAO.</div>', unsafe_allow_html=True)
            continue

        for n in items:
            notam_text = n.strip()
            if notam_text.upper() == "NILL":
                badge_html = '<span class="badge badge-nill">🚫 No NOTAMs reported</span>'
                st.markdown(f'<div class="monos">{badge_html}<br>{notam_text}</div>', unsafe_allow_html=True)
                continue

            from_dt, to_dt = parse_notam_dates(notam_text)
            active = is_active(from_dt, to_dt)
            status = "🟢 Active" if active else "🔴 Expired"
            badge_class = "badge-active" if active else "badge-expired"
            badge_html = f'<span class="badge {badge_class}">{status}</span>'

            st.markdown(f'<div class="monos">{badge_html}<br>{notam_text}</div>', unsafe_allow_html=True)

