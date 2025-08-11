# ---- METAR ----

@st.cache_data(ttl=75)
def fetch_metar_decoded(icao: str) -> Optional[Dict[str,Any]]:
    # Primary: CheckWX decoded
    try:
        if cw_headers():
            r = requests.get(f"https://api.checkwx.com/metar/{icao}/decoded", headers=cw_headers(), timeout=10)
            r.raise_for_status(); data = r.json().get("data", [])
            if data: return data[0]
    except Exception: pass
    # Fallback: AVWX
    try:
        if avwx_headers():
            r = requests.get(f"https://avwx.rest/api/metar/{icao}", headers=avwx_headers(), params={"format":"json"}, timeout=10)
            r.raise_for_status(); j = r.json() or {}
            # Map to minimal decoded the page expects
            flight_rules = (j.get("flight_rules") or "").upper()
            observed = (j.get("time", {}) or {}).get("dt", "")
            return {
                "flight_category": flight_rules,
                "observed": observed,
            }
    except Exception: pass
    return None

@st.cache_data(ttl=75)
def fetch_metar_raw(icao: str) -> str:
    try:
        if cw_headers():
            r = requests.get(f"https://api.checkwx.com/metar/{icao}", headers=cw_headers(), timeout=10)
            r.raise_for_status(); data = r.json().get("data", [])
            if data:
                if isinstance(data[0], dict): return data[0].get("raw") or data[0].get("raw_text","") or ""
                return str(data[0])
    except Exception: pass
    # Fallback AVWX
    try:
        if avwx_headers():
            r = requests.get(f"https://avwx.rest/api/metar/{icao}", headers=avwx_headers(), params={"format":"json"}, timeout=10)
            r.raise_for_status(); j = r.json() or {}
            return (j.get("raw") or j.get("sanitized") or "")
    except Exception: pass
    return ""

# ---- TAF ----

@st.cache_data(ttl=75)
def fetch_taf_decoded(icao: str) -> Optional[Dict[str,Any]]:
    # Primary: CheckWX decoded
    try:
        if cw_headers():
            r = requests.get(f"https://api.checkwx.com/taf/{icao}/decoded", headers=cw_headers(), timeout=10)
            r.raise_for_status(); data = r.json().get("data", [])
            if data: return data[0]
    except Exception: pass
    # Fallback: AVWX
    try:
        if avwx_headers():
            r = requests.get(f"https://avwx.rest/api/taf/{icao}", headers=avwx_headers(), params={"format":"json"}, timeout=10)
            r.raise_for_status(); j = r.json() or {}
            issued = (j.get("time", {}) or {}).get("issued", {})
            issued_dt = issued.get("dt") if isinstance(issued, dict) else ""
            return {
                "timestamp": {"issued": issued_dt},
            }
    except Exception: pass
    return None

@st.cache_data(ttl=75)
def fetch_taf_raw(icao: str) -> str:
    try:
        if cw_headers():
            r = requests.get(f"https://api.checkwx.com/taf/{icao}", headers=cw_headers(), timeout=10)
            r.raise_for_status(); data = r.json().get("data", [])
            if data:
                if isinstance(data[0], dict): return data[0].get("raw") or data[0].get("raw_text","") or ""
                return str(data[0])
    except Exception: pass
    # Fallback AVWX
    try:
        if avwx_headers():
            r = requests.get(f"https://avwx.rest/api/taf/{icao}", headers=avwx_headers(), params={"format":"json"}, timeout=10)
            r.raise_for_status(); j = r.json() or {}
            return (j.get("raw") or j.get("sanitized") or "")
    except Exception: pass
    return ""

# ---- SIGMET (LPPC auto from AWC) ----
@st.cache_data(ttl=120)
def fetch_sigmet_lppc() -> List[str]:
    try:
        r = requests.get("https://aviationweather.gov/api/data/isigmet",
                         params={"loc":"eur","format":"json"}, timeout=12)
        r.raise_for_status(); js = r.json()
        items = js if isinstance(js, list) else js.get("features", []) or []
        out: List[str] = []
        for it in items:
            props = it.get("properties", {}) if isinstance(it, dict) else {}
            if not props and isinstance(it, dict): props = it
            raw = (props.get("raw") or props.get("raw_text") or props.get("sigmet_text") or "").strip()
            fir = (props.get("fir") or props.get("firid") or props.get("firId") or "").upper()
            if not raw: continue
            if fir == "LPPC" or " LPPC " in f" {raw} " or "FIR LPPC" in raw or " LPPC FIR" in raw:
                out.append(raw)
        return out
    except Exception: return []

# ---- Read GAMET from Gist (saved) ----
def gamet_gist_config_ok() -> bool:
    return bool(st.secrets.get("GAMET_GIST_TOKEN","") and st.secrets.get("GAMET_GIST_ID","") and st.secrets.get("GAMET_GIST_FILENAME",""))

@st.cache_data(ttl=90)
def load_gamet_saved() -> Dict[str,Any]:
    if not gamet_gist_config_ok(): return {"text":"", "updated_utc":None}
    try:
        token = st.secrets["GAMET_GIST_TOKEN"]; gid = st.secrets["GAMET_GIST_ID"]; fn = st.secrets["GAMET_GIST_FILENAME"]
        r = requests.get(f"https://api.github.com/gists/{gid}", headers={"Authorization": f"token {token}"}, timeout=10)
        r.raise_for_status(); files = r.json().get("files", {})
        if fn in files and "content" in files[fn]:
            content = files[fn]["content"]
            try: return json.loads(content)
            except Exception: return {"text": content, "updated_utc": None}
    except Exception: pass
    return {"text":"", "updated_utc":None}

# ---- UI ----
st.markdown('<div class="page-title">Weather (Live)</div>', unsafe_allow_html=True)
st.markdown('<div class="subtle">Latest METAR, TAF, LPPC SIGMET (auto), and GAMET (saved)</div>', unsafe_allow_html=True)

q = st.query_params
raw = q.get("icao","")
if isinstance(raw, list): raw = ",".join(raw)
icaos = [p.strip().upper() for p in (raw or ",".join(DEFAULT_ICAOS)).split(",") if p.strip()]
col1, col2 = st.columns([0.7, 0.3])
with col1:
    icaos_input = st.text_input("ICAO list (comma-separated)", value=",".join(icaos))
with col2:
    if st.button("Refresh"):
        st.cache_data.clear()
icaos = [x.strip().upper() for x in icaos_input.split(",") if x.strip()]

# METAR + TAF (single block per aerodrome)
for icao in icaos:
    metar_dec = fetch_metar_decoded(icao); metar_raw = fetch_metar_raw(icao)
    taf_dec = fetch_taf_decoded(icao); taf_raw = fetch_taf_raw(icao)
    cat = (metar_dec or {}).get("flight_category","" ).upper()
    klass = {"VFR":"vfr","MVFR":"mvfr","IFR":"ifr","LIFR":"lifr"}.get(cat,"")
    badge = f'<span class="badge {klass}">{cat}</span>' if klass else ""
    metar_obs = ""
    obs = (metar_dec or {}).get("observed")
    metar_obs = zulu_plus_pt(parse_iso_utc(obs)) if obs else ""

    taf_issued = ""
    ts = (taf_dec or {}).get("timestamp", {})
    issued = ts.get("issued") if isinstance(ts, dict) else None
    taf_issued = zulu_plus_pt(parse_iso_utc(issued)) if issued else ""

    st.markdown('<div class="row">', unsafe_allow_html=True)
    st.markdown(f"<h3>{icao} {badge}</h3>" + (f'<span class="info-line">Observed: {metar_obs}</span>' if metar_obs else ""), unsafe_allow_html=True)
    st.markdown(f'<div class="monos"><strong>METAR</strong> {metar_raw or "—"}

<strong>TAF</strong> {taf_raw or "—"}' + (f"

<strong>TAF Issued</strong> {taf_issued}" if taf_issued else "") + '</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# SIGMET (LPPC auto)
st.subheader("SIGMET (LPPC) — Automatic")
sigs = fetch_sigmet_lppc()
if not sigs:
    st.write("—")
else:
    for s in sigs:
        st.markdown(f'<div class="monos">{s}</div>', unsafe_allow_html=True)
        st.markdown("---")

# GAMET (saved in Gist)
st.subheader("GAMET (Saved)")
gamet_saved = load_gamet_saved()
if gamet_saved.get("text"):
    if gamet_saved.get("updated_utc"):
        st.markdown(f'<div class="info-line">Last saved (UTC): {gamet_saved["updated_utc"]}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="monos">{gamet_saved["text"]}</div>', unsafe_allow_html=True)
    st.download_button("Download GAMET as .txt", data=gamet_saved["text"], file_name="gamet.txt")
else:
    st.write("—")






