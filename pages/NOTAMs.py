# pages/NOTAMs.py — NOTAMs (Saved via Gist) + notas por ICAO

from typing import Dict, Any, List
import streamlit as st, requests, json

st.set_page_config(page_title="NOTAMs (Saved)", layout="wide")
st.markdown("""
<style>
[data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
[data-testid="stSidebarCollapseButton"] { display:none !important; }
:root { --line:#e5e7eb; --muted:#6b7280; }
.page-title{font-size:2rem;font-weight:800;margin:0 0 .25rem}
.subtle{color:var(--muted);margin-bottom:.75rem}
.row{padding:10px 0 14px;border-bottom:1px solid var(--line)}
.monos{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.95rem;white-space:pre-wrap}
.info{font-size:.92rem;color:var(--muted)}
</style>
""", unsafe_allow_html=True)

def notam_gist_config_ok() -> bool:
    token = (st.secrets.get("NOTAM_GIST_TOKEN") or st.secrets.get("GIST_TOKEN") or "").strip()
    gid   = (st.secrets.get("NOTAM_GIST_ID")    or st.secrets.get("GIST_ID")    or "").strip()
    fn    = (st.secrets.get("NOTAM_GIST_FILENAME") or "").strip()
    return bool(token and gid and fn)

@st.cache_data(ttl=90)
def load_notams_saved() -> Dict[str,Any]:
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
        r.raise_for_status(); files = r.json().get("files", {})
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

st.markdown('<div class="page-title">NOTAMs (Saved)</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Reads NOTAMs from a GitHub Gist you control. Add local notes per aerodrome below.</div>', unsafe_allow_html=True)

col = st.columns([0.65,0.35])
with col[0]:
    icaos_str = st.text_input("ICAO list", value="LPSO, LPCB, LPEV")
with col[1]:
    if st.button("Refresh"):
        st.cache_data.clear()

saved = load_notams_saved()
if saved.get("updated_utc"):
    st.markdown(f'<div class="info">Last saved (UTC): {saved["updated_utc"]}</div>', unsafe_allow_html=True)

m = saved.get("map") or {}
icaos = [x.strip().upper() for x in icaos_str.split(",") if x.strip()]

st.markdown("### Extra notes per ICAO")
ncol1, ncol2, ncol3 = st.columns(3)
ncols = [ncol1, ncol2, ncol3]
notes: Dict[str,str] = {}
for i, icao in enumerate(icaos):
    with ncols[i % 3]:
        notes[icao] = st.text_area(
            f"{icao} — extra notes",
            value="",
            placeholder="Ex.: AERODROME BEACON ONLY FLASH-GREEN LIGHT OPERATIVE.\nFROM: 29th Jul 2025 15:10 TO: 29th Sep 2025 18:18 EST",
            key=f"note_{icao}",
            height=90
        )

st.markdown("---")
for icao in icaos:
    st.markdown(f"## {icao}")
    items: List[str] = list((m.get(icao) or []))
    extra = (notes.get(icao) or "").strip()
    # Mostra bloco interpretado simples (lista + nota)
    compiled = items + ([f"NOTE: {extra}"] if extra else [])
    if not compiled:
        st.write("—")
    else:
        for n in compiled:
            st.markdown(f'<div class="monos">{n}</div>', unsafe_allow_html=True)
            st.markdown("---")

# Export
flat = []
for icao in icaos:
    for n in (m.get(icao) or []):
        flat.append(f"[{icao}] {n}")
    extra = (notes.get(icao) or "").strip()
    if extra:
        flat.append(f"[{icao}][NOTE] {extra}")
if flat:
    st.download_button("Download filtered NOTAMs (.txt)", data="\n\n".join(flat), file_name="notams.txt")

