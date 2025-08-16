import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import MarkerCluster
import re
from math import radians, sin, cos, sqrt, atan2

# ---------- Configuração ----------
st.set_page_config(page_title="Portugal VFR — Plano de Voo", layout="wide")

# ---------- Estilo ----------
st.markdown("""
<style>
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

# ---------- Título ----------
st.markdown("<h1 class='title-header'>Portugal VFR — Criador de Plano de Voo</h1>", unsafe_allow_html=True)
st.markdown("<div class='subtitle'>Clica nos pontos do mapa para criar um plano de voo visual.</div>", unsafe_allow_html=True)

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

def haversine_nm(lat1, lon1, lat2, lon2):
    R = 3440.065  # Radius in nautical miles
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
            rows.append({"source":"AD","code":ident,"name":name,"city":city,"lat":lat,"lon":lon})
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

# ---------- Carregamento dos dados ----------
ad_df = parse_ad(pd.read_csv("AD-HEL-ULM.csv"))
loc_df = parse_localidades(pd.read_csv("Localidades-Nova-versao-230223.csv"))

points_df = pd.concat([ad_df, loc_df], ignore_index=True).dropna(subset=["lat", "lon"])
point_map = {row["code"]: row for _, row in points_df.iterrows()}

# ---------- Velocidade de cruzeiro ----------
col1, col2 = st.columns(2)
with col1:
    cruise_speed = st.number_input("Velocidade de cruzeiro (knots)", min_value=1, max_value=500, value=100)

# ---------- Estado do plano de voo ----------
if "flight_plan" not in st.session_state:
    st.session_state.flight_plan = []

def add_point_to_plan(code):
    if code not in st.session_state.flight_plan:
        st.session_state.flight_plan.append(code)

def clear_plan():
    st.session_state.flight_plan = []

# ---------- Mapa inicial ----------
center_lat = points_df["lat"].mean()
center_lon = points_df["lon"].mean()
m = folium.Map(location=[center_lat, center_lon], zoom_start=6, control_scale=True)

cluster = MarkerCluster(name="Pontos").add_to(m)

for _, row in points_df.iterrows():
    tooltip = f"<b>{row['code']}</b><br/>{row['name']}<br/>Lat: {row['lat']:.5f}<br/>Lon: {row['lon']:.5f}"
    folium.Marker(
        location=[row["lat"], row["lon"]],
        icon=folium.Icon(color="blue", icon="info-sign"),
        tooltip=tooltip,
        popup=folium.Popup(f"<b>Adicionar {row['code']}</b>", max_width=300),
    ).add_to(cluster)

# ---------- Interação: clique no mapa ----------
map_data = st_folium(m, width=None, height=600)

if map_data and map_data.get("last_object_clicked"):
    lat_clicked = map_data["last_object_clicked"]["lat"]
    lon_clicked = map_data["last_object_clicked"]["lng"]
    # Tenta encontrar ponto mais próximo (pequena margem de erro)
    for code, row in point_map.items():
        if abs(row["lat"] - lat_clicked) < 0.0005 and abs(row["lon"] - lon_clicked) < 0.0005:
            add_point_to_plan(code)
            break

# ---------- Plano de voo desenhado ----------
if st.session_state.flight_plan:
    coords = [(point_map[code]["lat"], point_map[code]["lon"]) for code in st.session_state.flight_plan]
    folium.PolyLine(coords, color="red", weight=4, opacity=0.8).add_to(m)

    total_nm = 0
    for i in range(len(coords)-1):
        d = haversine_nm(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
        total_nm += d

    time_hours = total_nm / cruise_speed
    hours = int(time_hours)
    minutes = int((time_hours - hours) * 60)

    st.markdown("### 📋 Plano de Voo")
    st.write("→ ".join(st.session_state.flight_plan))
    st.success(f"Distância total: **{total_nm:.1f} NM** — Tempo estimado: **{hours}h {minutes}min** a {cruise_speed} kt")

    if st.button("🗑️ Limpar plano de voo"):
        clear_plan()


