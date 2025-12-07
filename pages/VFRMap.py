import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import MarkerCluster
import re, os

# ---------- Page config ----------
st.set_page_config(
    page_title="Portugal VFR — Localidades + AD/HEL/ULM",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------- Custom CSS ----------
st.markdown("""
<style>
body {
    background: #f3f4f6;
}
.main-block {
    background: #ffffff;
    border-radius: 18px;
    padding: 16px 18px 12px 18px;
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.12);
    margin-bottom: 16px;
}
h1.title-header {
    font-size: 2rem;
    margin-bottom: 0.2rem;
}
.subtitle {
    opacity: 0.9;
    margin-top: 0px;
    margin-bottom: 18px;
    font-size: 0.95rem;
    color: #6b7280;
}
.chip-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 4px;
}
.chip {
    font-size: 0.78rem;
    padding: 3px 8px;
    border-radius: 999px;
    background: #f3f4f6;
    border: 1px solid #e5e7eb;
    color: #4b5563;
}
.stApp iframe, .stApp .stMarkdown iframe {
    opacity: 1 !important;
    filter: none !important;
}
.leaflet-pane, .leaflet-top, .leaflet-bottom {
    opacity: 1 !important;
    filter: none !important;
}
</style>
""", unsafe_allow_html=True)

# ---------- Header ----------
st.markdown("<div class='main-block'>", unsafe_allow_html=True)
st.markdown("<h1 class='title-header'>Portugal VFR — Localidades + AD/HEL/ULM</h1>", unsafe_allow_html=True)

# ---------- Helpers ----------
def dms_to_dd(token: str, is_lon=False):
    token = str(token).strip()
    m = re.match(r"^(\d+(?:\.\d+)?)([NSEW])$", token, re.I)
    if not m:
        return None
    value, hemi = m.groups()
    if "." in value:
        if is_lon:
            deg = int(value[0:3]); minutes = int(value[3:5]); seconds = float(value[5:])
        else:
            deg = int(value[0:2]); minutes = int(value[2:4]); seconds = float(value[4:])
    else:
        if is_lon:
            deg = int(value[0:3]); minutes = int(value[3:5]); seconds = int(value[5:])
        else:
            deg = int(value[0:2]); minutes = int(value[2:4]); seconds = int(value[4:])
    dd = deg + minutes/60 + seconds/3600
    if hemi.upper() in ["S","W"]:
        dd = -dd
    return dd

def parse_ad(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for line in df.iloc[:,0].dropna().tolist():
        s = str(line).strip()
        if not s or s.startswith(("Ident", "DEP/")):
            continue
        tokens = s.split()
        coord_toks = [t for t in tokens if re.match(r"^\d+(?:\.\d+)?[NSEW]$", t)]
        if len(coord_toks) >= 2:
            lat_tok = coord_toks[-2]; lon_tok = coord_toks[-1]
            lat = dms_to_dd(lat_tok, is_lon=False); lon = dms_to_dd(lon_tok, is_lon=True)
            ident = tokens[0] if re.match(r"^[A-Z0-9]{4,}$", tokens[0]) else None
            try:
                name = " ".join(tokens[1:tokens.index(coord_toks[0])]).strip()
            except ValueError:
                name = " ".join(tokens[1:]).strip()
            try:
                lon_idx = tokens.index(lon_tok); city = " ".join(tokens[lon_idx+1:]) or None
            except ValueError:
                city = None
            rows.append({
                "source":"AD/HEL/ULM",
                "ident":ident,
                "name":name,
                "city":city,
                "lat":lat,
                "lon":lon
            })
    return pd.DataFrame(rows).dropna(subset=["lat","lon"])

def parse_localidades(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for line in df.iloc[:,0].dropna().tolist():
        s = str(line).strip()
        if not s or "Total de registos" in s:
            continue
        tokens = s.split()
        coord_toks = [t for t in tokens if re.match(r"^\d{6,7}(?:\.\d+)?[NSEW]$", t)]
        if len(coord_toks) >= 2:
            lat_tok, lon_tok = coord_toks[0], coord_toks[1]
            lat = dms_to_dd(lat_tok, is_lon=False); lon = dms_to_dd(lon_tok, is_lon=True)
            try:
                lon_idx = tokens.index(lon_tok)
            except ValueError:
                continue
            code = tokens[lon_idx+1] if lon_idx+1 < len(tokens) else None
            sector = " ".join(tokens[lon_idx+2:]) if lon_idx+2 < len(tokens) else None
            name = " ".join(tokens[:tokens.index(lat_tok)]).strip()
            rows.append({
                "source":"Localidade",
                "code":code,
                "name":name,
                "sector":sector,
                "lat":lat,
                "lon":lon
            })
    return pd.DataFrame(rows).dropna(subset=["lat","lon"])

# ---------- VOR DB (igual ao NAVLOG) ----------
VOR_CSV = "NAVAIDS_VOR.csv"

def _load_vor_db(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            df = pd.read_csv(path)
            df = df.rename(columns={c: c.lower() for c in df.columns})
            df["ident"] = df["ident"].astype(str).str.upper().str.strip()
            df["freq_mhz"] = pd.to_numeric(df["freq_mhz"], errors="coerce")
            df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
            df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
            df["name"] = df.get("name","")
            df = df.dropna(subset=["ident","freq_mhz","lat","lon"]).reset_index(drop=True)
            return df[["ident","name","freq_mhz","lat","lon"]]
        except Exception:
            pass

    # Fallback básico se o CSV não existir
    fallback = [
        ("CAS", "Cascais DVOR/DME", 114.30, 38.7483, -9.3619),
        ("ESP", "Espichel DVOR/DME", 112.50, 38.4242, -9.1856),
        ("VFA", "Faro DVOR/DME",     112.80, 37.0136, -7.9750),
        ("FTM", "Fátima DVOR/DME",   113.50, 39.6656, -8.4928),
        ("LIS", "Lisboa DVOR/DME",   114.80, 38.8878, -9.1628),
        ("NSA", "Nisa DVOR/DME",     115.50, 39.5647, -7.9147),
        ("PRT", "Porto DVOR/DME",    114.10, 41.2731, -8.6878),
        ("SGR", "Sagres VOR/DME",    113.90, 37.0839, -8.94639),
        ("SRA", "Sintra VORTAC",     112.10, 38.829201, -9.34),
        ("VBZ", "Badajoz VOR/DME",   116.8,  38.889900, -6.815750)
    ]
    return pd.DataFrame(fallback, columns=["ident","name","freq_mhz","lat","lon"])

vor_db = _load_vor_db(VOR_CSV)

# ---------- Data ----------
ad_df = parse_ad(pd.read_csv("AD-HEL-ULM.csv"))
loc_df = parse_localidades(pd.read_csv("Localidades-Nova-versao-230223.csv"))

# ---------- Controls ----------
col1, col2, col3, col4 = st.columns([1,1,1,3])
with col1:
    show_ad = st.checkbox("Aeródromos", value=True)
with col2:
    show_loc = st.checkbox("Localidades", value=True)
with col3:
    show_vor = st.checkbox("VOR", value=True)
with col4:
    query = st.text_input(
        "🔍 Filtrar (código/ident/nome/cidade)",
        "",
        placeholder="Ex: ABRAN, LP0078, Porto..."
    )

def apply_filters(ad_df, loc_df, q):
    if q:
        tq = q.lower().strip()
        ad_df = ad_df[ad_df.apply(
            lambda r: tq in str(r['name']).lower()
                      or tq in str(r.get('ident','')).lower()
                      or tq in str(r.get('city','')).lower(),
            axis=1
        )]
        loc_df = loc_df[loc_df.apply(
            lambda r: tq in str(r['name']).lower()
                      or tq in str(r.get('code','')).lower()
                      or tq in str(r.get('sector','')).lower(),
            axis=1
        )]
    return ad_df, loc_df

ad_f, loc_f = apply_filters(ad_df, loc_df, query)

# Pequeno resumo em "chips" + fecha header
st.markdown("<div class='chip-row'>", unsafe_allow_html=True)
st.markdown(
    f"<div class='chip'>AD/HEL/ULM totais: <b>{len(ad_df)}</b></div>",
    unsafe_allow_html=True
)
st.markdown(
    f"<div class='chip'>Localidades totais: <b>{len(loc_df)}</b></div>",
    unsafe_allow_html=True
)
st.markdown(
    f"<div class='chip'>Filtro → AD: <b>{len(ad_f)}</b> | Loc: <b>{len(loc_f)}</b></div>",
    unsafe_allow_html=True
)
st.markdown(
    f"<div class='chip'>VOR carregados: <b>{len(vor_db)}</b></div>",
    unsafe_allow_html=True
)
st.markdown("</div>", unsafe_allow_html=True)
st.markdown("</div>", unsafe_allow_html=True)  # fecha main-block

# ---------- Map Center ----------
if len(ad_f) + len(loc_f) > 0:
    mean_lat = pd.concat([ad_f["lat"], loc_f["lat"]]).mean()
    mean_lon = pd.concat([ad_f["lon"], loc_f["lon"]]).mean()
else:
    mean_lat, mean_lon = 39.5, -8.0

# ---------- Folium Map ----------
m = folium.Map(
    location=[mean_lat, mean_lon],
    zoom_start=7,
    tiles=None,
    control_scale=True,
    prefer_canvas=True,
)

# Base única: OpenTopoMap (tal como no NAVLOG)
folium.TileLayer(
    "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    attr="© OpenTopoMap",
    name="OpenTopoMap",
    control=False,
).add_to(m)

# ---------- Overlay openAIP (igual ao NAVLOG) ----------
openaip_token = (
    getattr(st, "secrets", {}).get("OPENAIP_KEY")
    if hasattr(st, "secrets") else None
) or os.getenv("OPENAIP_KEY", "e849257999aa8ed820c3a6f7eb40f84e")  # mesmo fallback do NAVLOG

if openaip_token:
    folium.TileLayer(
        tiles=(
            "https://{s}.api.tiles.openaip.net/api/data/openaip/"
            "{z}/{x}/{y}.png?apiKey=" + openaip_token
        ),
        attr="© openAIP",
        name="openAIP (VFR data)",
        overlay=True,
        control=True,
        subdomains="abc",
        opacity=0.6,
        max_zoom=20,
    ).add_to(m)

# ---------- Localidades ----------
if show_loc and not loc_f.empty:
    cluster_loc = MarkerCluster(
        name="Localidades",
        show=True,
        disableClusteringAtZoom=10
    )
    for _, r in loc_f.iterrows():
        code = r.get("code") or "—"
        name = r.get("name", "")
        sector = r.get("sector", "")
        lat = round(r["lat"], 5)
        lon = round(r["lon"], 5)

        tooltip_html = f"""
        <b>{code}</b><br/>
        Nome: {name}<br/>
        Sector: {sector}<br/>
        Lat: {lat}<br/>
        Lon: {lon}
        """

        label_html = f"""
        <div style="font-size:11px;font-weight:600;color:#fff;
        background:rgba(0,0,0,0.6);padding:2px 6px;border-radius:4px;
        border:1px solid rgba(255,255,255,0.35);backdrop-filter:blur(1px);">
            {code}
        </div>
        """

        folium.Marker(
            location=[r["lat"], r["lon"]],
            icon=folium.DivIcon(html=label_html),
            tooltip=tooltip_html
        ).add_to(cluster_loc)
    cluster_loc.add_to(m)

# ---------- AD/HEL/ULM ----------
if show_ad and not ad_f.empty:
    cluster_ad = MarkerCluster(
        name="AD/HEL/ULM",
        show=True,
        disableClusteringAtZoom=10
    )
    for _, r in ad_f.iterrows():
        ident = r.get("ident", "—")
        name = r.get("name", "")
        city = r.get("city", "")
        lat = round(r["lat"], 5)
        lon = round(r["lon"], 5)

        tooltip_html = f"""
        <b>{ident}</b><br/>
        Nome: {name}<br/>
        Cidade: {city}<br/>
        Lat: {lat}<br/>
        Lon: {lon}
        """

        folium.Marker(
            location=[r["lat"], r["lon"]],
            icon=folium.Icon(icon="plane", prefix="fa", color="gray"),
            tooltip=tooltip_html
        ).add_to(cluster_ad)
    cluster_ad.add_to(m)

# ---------- VOR ----------
if show_vor and not vor_db.empty:
    cluster_vor = MarkerCluster(
        name="VOR",
        show=True,
        disableClusteringAtZoom=9
    )
    for _, r in vor_db.iterrows():
        ident = r.get("ident", "")
        name = r.get("name", "")
        freq = r.get("freq_mhz", "")
        lat = float(r["lat"])
        lon = float(r["lon"])

        tooltip_html = f"""
        <b>{ident}</b><br/>
        {name}<br/>
        {freq:.2f} MHz<br/>
        Lat: {lat:.5f}<br/>
        Lon: {lon:.5f}
        """

        folium.CircleMarker(
            location=[lat, lon],
            radius=5,
            color="#e11d48",
            fill=True,
            fill_opacity=0.9,
            tooltip=tooltip_html,
        ).add_to(cluster_vor)
    cluster_vor.add_to(m)

# ---------- Display map ----------
folium.LayerControl(collapsed=False).add_to(m)
st_folium(m, width=None, height=720)

# ---------- Footer ----------
if len(ad_f) == 0 and len(loc_f) == 0:
    st.info("🔍 Nenhum resultado encontrado com esse filtro.")
else:
    st.caption(
        f"📍 Total carregado: {len(ad_df)} AD/HEL/ULM, {len(loc_df)} Localidades. "
        f"Filtro ativo → AD: {len(ad_f)} | Localidades: {len(loc_f)}."
    )


