# pages/Weather.py
# Polished Weather Dashboard (Live)
# - METAR/TAF via CheckWX
# - SIGMET LPPC via AWC International SIGMET
# - Optional GAMET via secrets.GAMET_URL
# - Better layout: cards, tabs, flight category badge, auto-refresh, add ICAO

from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional
import re
import time

import streamlit as st
import requests

# ---------- Page setup & styles ----------
st.set_page_config(page_title="Weather (Live)", layout="wide")

st.markdown("""
<style>
  :root { --muted:#6b7280; --line:#e5e7eb; --bg:#ffffff; }
  .title { font-size: 2rem; font-weight: 800; margin-bottom: .25rem;}
  .muted { color: var(--muted); margin-bottom: 1rem;}
  .toolbar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin: 8px 0 14px;}
  .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
  .card { border: 1px solid var(--line); background:var(--bg); border-radius: 14px; padding: 14px 16px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
  .h3 { font-size:1.05rem; font-weight:700; margin: 0 0 6px;}
  .monos { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; white-space: pre-wrap; }
  .kvs { display:grid; grid-template-columns: 1fr 1fr; gap:8px; margin: 8px 0;}
  .kv { border:1px solid var(--line); border-radius:10px; padding:8px 10px; }
  .kv label { display:block; font-size:.8rem; color:var(--muted); margin-bottom:2px;}
  .badge { display:inline-block; padding:4px 8px; border-radius:999px; font-size:.75rem; font-weight:700; border:1px solid var(--line); }
  .vfr  { background:#e8fbea; }
  .mvfr { background:#fff5da; }
  .ifr  { background:#ffe4e6; }
  .lifr { background:#ffd1d1; }
  .countpill { display:inline-block; padding:3px 8px; border-radius:999px; background:#f3f4f6; font-size:.75rem; margin-left:6px;}
  .linkhint { font-size:.9rem; color:var(--muted); }
</style>
""", unsafe_allow_html=True)

# ---------- Config ----------
DEFAULT_ICAOS = ["LPPT", "LPBJ", "LEBZ"]

# ---------- Helpers ----------
def cw_headers() -> Dict[str,str]:
    key = st.secrets.get("CHECKWX_API_KEY","")
    return {"X-API-Key": key} if key else {}

@st.cache_data(ttl=60)
def fetch_metar(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        return data[0].get("raw") or data[0].get("raw_text","") if isinstance(data[0], dict) else str(data[0])
    except Exception:
        return ""

@st.cache_data(ttl=60)
def fetch_taf(icao: str) -> str:
    try:
        r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=cw_headers(), timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data: return ""
        return data[0].get("raw") or data[0].get("raw_text","") if isinstance(data[0], dict) else str(data[0])
    except Exception:
        return ""

def awc_params() -> Dict[str,str]:
    return {"loc":"eur", "format":"json"}

@st.cache_data(ttl=90)
def fetch_sigmet_lppc() -> List[str]:
    """International SIGMETs (EUR) via AWC; filter LPPC."""
    try:
        r = requests.get("https://aviationweather.gov/api/data/isigmet", params=awc_params(), timeout=12)
        r.raise_for_status()
        data = r.json()
        items: List[Any] = data if isinstance(data, list) else data.get("features", []) or []
        out: List[str] = []
        for item in items:
            props: Dict[str,Any] = {}
            if isinstance(item, dict) and "properties" in item:
                props = item["properties"]
            elif isinstance(item, dict):
                props = item
            raw = (props.get("raw") or props.get("sigmet_text") or str(item) or "").strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            if not raw:
                continue
            if fir == "LPPC" or " LPPC " in f" {raw} " or "FIR LPPC" in raw or " LPPC FIR" in raw:
                out.append(raw)
        return out
    except Exception:
        return []

def _json_to_text(j: Any) -> str:
    if isinstance(j, str):
        return j.strip()
    if isinstance(j, list):
        parts: List[str] = []
        for it in j:
            if isinstance(it, str):
                parts.append(it.strip())
            elif isinstance(it, dict):
                for k in ("text","gamet","raw","message","body"):
                    if k in it and isinstance(it[k], str):
                        parts.append(it[k].strip()); break
        return "\n".join([p for p in parts if p])
    if isinstance(j, dict):
        for k in ("text","gamet","raw","message","body","data"):
            v = j.get(k)
            if isinstance(v, str):
                return v.strip()
            if isinstance(v, (list, dict)):
                return _json_to_text(v)
    return ""

@st.cache_data(ttl=60)
def fetch_gamet_live() -> str:
    url = (st.secrets.get("GAMET_URL","") or "").strip()
    if not url:
        return ""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        ct = r.headers.get("Content-Type","").lower()
        if "application/json" in ct:
            return _json_to_text(r.json())
        return r.text.strip()
    except Exception:
        return ""

# ---------- Decoders / Flight Category ----------
VIS_RE = re.compile(r"\s(\d{4})(?:\s|$)")  # simple meters (e.g., 8000)
VIS_SM_RE = re.compile(r"\s(\d{1,2}(?:\s?\d/\d)?)(SM)\b")  # e.g., 3SM or 1 1/2SM
CEIL_RE = re.compile(r"\s(BKN|OVC)(\d{3})\b")  # hundreds of feet AGL

def parse_visibility(metar: str) -> Optional[int]:
    """Return visibility in meters if possible."""
    if not metar: return None
    m = VIS_RE.search(metar)
    if m:
        try:
            return int(m.group(1))
        except:
            pass
    m2 = VIS_SM_RE.search(metar)
    if m2:
        # convert statute miles to meters (1 SM ≈ 1609 m)
        frac = m2.group(1).strip()
        try:
            if " " in frac:
                a, b = frac.split()
                whole = int(a)
                num, den = b.split("/")
                val = whole + (int(num)/int(den))
            elif "/" in frac:
                num, den = frac.split("/")
                val = int(num)/int(den)
            else:
                val = float(frac)
            return int(val * 1609.34)
        except:
            return None
    return None

def parse_ceiling(metar: str) -> Optional[int]:
    """Return ceiling (lowest BKN/OVC base) in feet."""
    if not metar: return None
    bases = []
    for m in CEIL_RE.finditer(metar):
        try:
            bases.append(int(m.group(2)) * 100)  # e.g., OVC007 -> 700 ft
        except:
            pass
    return min(bases) if bases else None

def flight_category(metar: str) -> Tuple[str, str]:
    """
    Return (category, css_class). Simplified FAA thresholds:
      LIFR: ceiling < 500 or vis < 1600 m
      IFR:  ceiling < 1000 or vis < 4800 m
      MVFR: ceiling < 3000 or vis < 8000 m
      else VFR
    """
    vis = parse_visibility(metar)
    ceil = parse_ceiling(metar)
    # Defaults high to avoid false alarms
    v = vis if vis is not None else 99999
    c = ceil if ceil is not None else 99999
    if c < 500 or v < 1600: return ("LIFR", "lifr")
    if c < 1000 or v < 4800: return ("IFR", "ifr")
    if c < 3000 or v < 8000: return ("MVFR", "mvfr")
    return ("VFR", "vfr")

def brief_highlights(metar: str) -> Dict[str, str]:
    """Quick highlights for the Overview tab."""
    if not metar: return {}
    cat, cls = flight_category(metar)
    vis = parse_visibility(metar)
    ceil = parse_ceiling(metar)
    return {
        "Category": cat,
        "Visibility": f"{vis} m" if vis is not None else "—",
        "Ceiling": f"{ceil} ft" if ceil is not None else "—",
    }

# ---------- Query params & UI toolbar ----------
qp = st.query_params
raw_icaos = qp.get("icao","")
if isinstance(raw_icaos, list):
    raw_icaos = ",".join(raw_icaos)
icaos: List[str] = []
if raw_icaos:
    for part in raw_icaos.split(","):
        p = part.strip().upper()
        if len(p) == 4:
            icaos.append(p)
if not icaos:
    icaos = DEFAULT_ICAOS.copy()

st.markdown('<div class="title">Weather (Live)</div>', unsafe_allow_html=True)
st.markdown('<div class="muted">Latest METAR, TAF, LPPC SIGMET, and GAMET</div>', unsafe_allow_html=True)

with st.container():
    col1, col2, col3, col4 = st.columns([0.35, 0.2, 0.2, 0.25])
    with col1:
        new_icao = st.text_input("Add ICAO", placeholder="e.g., LPPT").upper().strip()
    with col2:
        add_btn = st.button("Add")
    with col3:
        refresh = st.button("Refresh")
    with col4:
        auto = st.checkbox("Auto-refresh", value=False)
        interval = st.selectbox("Interval (s)", [30, 60, 90, 120], index=1) if auto else None

if add_btn and new_icao:
    if len(new_icao) == 4 and new_icao.isalnum():
        if new_icao not in icaos:
            icaos.append(new_icao)
            st.toast(f"Added {new_icao}", icon="✅")
    else:
        st.toast("Invalid ICAO format", icon="⚠️")

# Keep a shareable link with current list
share_link = f"?icao={','.join(icaos)}" if icaos != DEFAULT_ICAOS else ""
st.caption(f"Share this view: {st.request.url}?icao={','.join(icaos)}" if share_link else f"Default view: {st.request.url}")

if refresh:
    st.cache_data.clear()
if auto:
    st_autorefresh = st.experimental_rerun  # alias to be explicit
    # Let Streamlit refresh itself; a simple sleep avoids hammering during render
    time.sleep(0.2)
    st.experimental_set_query_params(icao=",".join(icaos))
    st.experimental_rerun()

# ---------- METAR / TAF cards ----------
st.markdown('<div class="grid">', unsafe_allow_html=True)
for icao in icaos:
    metar = fetch_metar(icao)
    taf = fetch_taf(icao)
    cat, cls = flight_category(metar) if metar else ("—", "")
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<div class="h3">{icao} <span class="badge {cls}">{cat}</span></div>', unsafe_allow_html=True)

    tabs = st.tabs(["Overview", "Raw"])
    with tabs[0]:
        kv = brief_highlights(metar)
        st.markdown('<div class="kvs">', unsafe_allow_html=True)
        for k, v in kv.items():
            st.markdown(f'<div class="kv"><label>{k}</label><div class="monos">{v}</div></div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        if not metar and not taf:
            st.caption("No data available.")
    with tabs[1]:
        st.caption("METAR")
        st.markdown(f'<div class="monos">{metar or "—"}</div>', unsafe_allow_html=True)
        st.caption("TAF")
        st.markdown(f'<div class="monos">{taf or "—"}</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# ---------- LPPC SIGMET ----------
st.divider()
sigs = fetch_sigmet_lppc()
st.subheader(f"SIGMET (LPPC)  {f'· {len(sigs)} active' if sigs else '· 0'}")
if not sigs:
    st.write("—")
else:
    for s in sigs:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

# ---------- GAMET (Live) ----------
st.divider()
st.subheader("GAMET (Live)")
gamet_text = fetch_gamet_live()
if gamet_text:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(f'<div class="monos">{gamet_text}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
else:
    st.caption("No live GAMET. Set `GAMET_URL` in secrets to enable.")




