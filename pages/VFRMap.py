import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import MarkerCluster
import re
from math import radians, sin, cos, sqrt, atan2

# ---------- Configuração da página ----------
st.set_page_config(page_title="Portugal VFR — Localidades + AD/HEL/ULM", layout="wide")

# ---------- Estilos ----------
st.markdown("""
<style>
.stApp iframe, .stApp .stMarkdown iframe { opacity: 1 !important; filter: none !important; }
.leaflet-pane, .leaflet-top, .leaflet-bottom { opacity: 1 !important; filter: none !important; }
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
</style>
""", unsafe_allow_html=True)

# ---------- Header ----------
st.markdown("<h1 class='title-header'>Portugal VFR — Localidades + AD/HEL/ULM</h1>", unsafe_allow_html=True)
st.markdown("<div class='subtitle'>App por <b>Alexandre Moiteiro</b></div>", unsafe_allow_html=True)

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

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # km
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
    return R * (2 * atan2(sqrt(a), sqrt(1 - a)))

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
            rows.append({"source":"AD/HEL/ULM","ident":ident,"name":name,"city":city,"lat":lat,"lon":lon})
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
            rows.append({"source":"Localidade","code":code,"name":name,"sector":sector,"lat":lat,"lon":lon})
    return pd.DataFrame(rows).dropna(subset=["lat","lon"])

# ---------- Load Data ----------
ad_df = parse_ad(pd.read_csv("AD-HEL-ULM.csv"))
loc_df = parse_localidades(pd.read_csv("Localidades-Nova-versao-230223.csv"))

# ---------- UI Controls ----------
col1, col2, col3 = st.columns([1,1,6])
with col1:
    show_ad = st.checkbox("Aeródromos", value=True)
with col2:
    show_loc = st.checkbox("Localidades", value=True)
with col3:
    query = st.text_input("🔍 Filtrar (código/ident/nome/cidade)", "", placeholder="Ex: ABRAN, LP0078, Porto...")

def apply_filters(ad_df, loc_df, q):
    if q:
        tq = q.lower().strip()
        ad_df = ad_df[ad_df.apply(lambda r: tq in str(r['name']).lower() or tq in str(r.get('ident','')).lower() or tq in str(r.get('city','')).lower(), axis=1)]
        loc_df = loc_df[loc_df.apply(lambda r: tq in str(r['name']).lower() or tq in str(r.get('code','')).lower() or tq in str(r.get('sector','')).lower(), axis=1)]
    return ad_df, loc_df

ad_f, loc_f = apply_filters(ad_df, loc_df, query)

# ---------- Plano de voo (seleção múltipla) ----------
st.markdown("### ✈️ Criar Plano de Voo")
flight_points = []

names_all = []
point_map = {}

for df in [ad_f, loc_f]:
    for _, row in df.iterrows():
        label = f"{row.get('name')} ({row.get('ident', row.get('code', ''))})"
        names_all.append(label)
        point_map[label] = (row["lat"], row["lon"])

selected_labels = st.multiselect("Seleciona os pontos do plano de voo na ordem desejada:", names_all)

for label in selected_labels:
    latlon = point_map.get(label)
    if latlon:
        flight_points.append({"label": label, "lat": latlon[0], "lon": latlon[1]})

# ---------- Center map ----------
if len(ad_f) + len(loc_f) > 0:
    mean_lat = pd.concat([ad_f["lat"], loc_f["lat"]]).mean()
    mean_lon = pd.concat([ad_f["lon"], loc_f["lon"]]).mean()
else:
    mean_lat, mean_lon = 39.5, -8.0

# ---------- Map setup ----------
m = folium.Map(location=[mean_lat, mean_lon], zoom_start=6, tiles=None, control_scale=True)
sat_tiles = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
sat_attr = "Tiles © Esri, USDA, USGS, AeroGRID, IGN"
folium.TileLayer(tiles=sat_tiles, attr=sat_attr, name="Satélite", control=False, opacity=1).add_to(m)

# ---------- Localidades ----------
if show_loc and not loc_f.empty:
    cluster_loc = MarkerCluster(name="Localidades", show=True, disableClusteringAtZoom=10)
    for _, r in loc_f.iterrows():
        tooltip_html = f"<b>{r.get('name','')}</b><br/>Sector: {r.get('sector','')}<br/>Código: {r.get('code','')}<br/>Lat: {r['lat']:.5f}<br/>Lon: {r['lon']:.5f}"
        folium.Marker(
            location=[r["lat"], r["lon"]],
            icon=folium.Icon(color="green", icon="info-sign"),
            tooltip=tooltip_html
        ).add_to(cluster_loc)
    cluster_loc.add_to(m)

# ---------- AD/HEL/ULM ----------
if show_ad and not ad_f.empty:
    cluster_ad = MarkerCluster(name="AD/HEL/ULM", show=True, disableClusteringAtZoom=10)
    for _, r in ad_f.iterrows():
        tooltip_html = f"<b>{r.get('name','')}</b><br/>Ident: {r.get('ident','')}<br/>Cidade: {r.get('city','')}<br/>Lat: {r['lat']:.5f}<br/>Lon: {r['lon']:.5f}"
        folium.Marker(
            location=[r["lat"], r["lon"]],
            icon=folium.Icon(icon="plane", prefix="fa", color="gray"),
            tooltip=tooltip_html
        ).add_to(cluster_ad)
    cluster_ad.add_to(m)

# ---------- Traçar plano de voo ----------
if len(flight_points) >= 2:
    coords = [(p["lat"], p["lon"]) for p in flight_points]
    folium.PolyLine(coords, color="blue", weight=4, opacity=0.8, tooltip="Plano de voo").add_to(m)

    total_distance = 0
    for i in range(len(coords)-1):
        d = haversine(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
        total_distance += d

    st.success(f"🛫 Distância total do plano de voo: {total_distance:.1f} km")

# ---------- Mostrar mapa ----------
folium.LayerControl(collapsed=True).add_to(m)
st_folium(m, width=None, height=720)

# ---------- Rodapé ----------
st.caption(f"📍 Total: {len(ad_df)} AD/HEL/ULM | {len(loc_df)} Localidades — Filtro → AD: {len(ad_f)} | Localidades: {len(loc_f)}.")


