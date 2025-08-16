import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import MarkerCluster
import re
from math import radians, sin, cos, sqrt, atan2

# ---------- Configuração ----------
st.set_page_config(page_title="Portugal VFR — Plano de Voo Interativo", layout="wide")

# ---------- Estilos ----------
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

st.markdown("<h1 class='title-header'>Portugal VFR — Plano de Voo Interativo</h1>", unsafe_allow_html=True)
st.markdown("<div class='subtitle'>Clica no mapa para construir o plano de voo · Mede distância em milhas náuticas (NM) · Tempo estimado por velocidade de cruzeiro</div>", unsafe_allow_html=True)

# ---------- Inicializar sessão ----------
if "route" not in st.session_state:
    st.session_state["route"] = []

# ---------- Funções ----------
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
    R = 3440.065  # NM
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
            rows.append({"type":"AD","code":ident,"name":name,"city":city,"lat":lat,"lon":lon})
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
            rows.append({"type":"LOC","code":code,"name":name,"sector":sector,"lat":lat,"lon":lon})
    return pd.DataFrame(rows).dropna(subset=["lat","lon"])

# ---------- Carregar dados ----------
ad_df = parse_ad(pd.read_csv("AD-HEL-ULM.csv"))
loc_df = parse_localidades(pd.read_csv("Localidades-Nova-versao-230223.csv"))
all_df = pd.concat([ad_df, loc_df], ignore_index=True)

# ---------- Mapa ----------
m = folium.Map(location=[39.5, -8.0], zoom_start=6, control_scale=True)

for _, row in all_df.iterrows():
    code = row['code']
    name = row.get('name', '')
    lat, lon = row['lat'], row['lon']
    label = f"<b>{code}</b><br/>{name}<br/>Lat: {lat:.4f}<br/>Lon: {lon:.4f}<br/>Clique para adicionar"
    folium.Marker(
        location=[lat, lon],
        tooltip=label,
        popup=code,
        icon=folium.Icon(color="blue" if row['type'] == "AD" else "green", icon="plus", prefix="fa")
    ).add_to(m)

# ---------- Desenhar plano de voo ----------
if st.session_state.route:
    coords = [(p["lat"], p["lon"]) for p in st.session_state.route]
    folium.PolyLine(coords, color="red", weight=3, opacity=0.7, tooltip="Plano de voo").add_to(m)

# ---------- Mostrar mapa e capturar clique ----------
map_data = st_folium(m, height=600, returned_objects=["last_object_clicked"])

# ---------- Adicionar ponto clicado ----------
if map_data and map_data["last_object_clicked"]:
    clicked_lat = map_data["last_object_clicked"]["lat"]
    clicked_lon = map_data["last_object_clicked"]["lng"]
    for _, row in all_df.iterrows():
        if abs(row["lat"] - clicked_lat) < 0.0005 and abs(row["lon"] - clicked_lon) < 0.0005:
            point = {"code": row["code"], "name": row.get("name", ""), "lat": row["lat"], "lon": row["lon"]}
            if point not in st.session_state.route:
                st.session_state.route.append(point)

# ---------- Mostrar plano de voo atual ----------
st.markdown("### ✈️ Plano de voo atual")
if st.session_state.route:
    df_route = pd.DataFrame(st.session_state.route)
    st.dataframe(df_route[["code", "name", "lat", "lon"]], use_container_width=True)

    # Distância total
    total_nm = 0
    for i in range(len(st.session_state.route) - 1):
        a = st.session_state.route[i]
        b = st.session_state.route[i+1]
        total_nm += haversine_nm(a["lat"], a["lon"], b["lat"], b["lon"])

    # Velocidade de cruzeiro
    crz_speed = st.number_input("Velocidade de cruzeiro (nós)", min_value=30, max_value=200, value=90)
    flight_time = total_nm / crz_speed * 60  # em minutos

    st.success(f"Distância total: **{total_nm:.1f} NM** · Tempo estimado: **{flight_time:.0f} minutos**")
    if st.button("🗑️ Limpar plano de voo"):
        st.session_state.route = []
else:
    st.info("Nenhum ponto ainda no plano de voo. Clique nos marcadores para começar.")



