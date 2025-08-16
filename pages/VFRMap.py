import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
from folium.plugins import MarkerCluster
import re
from math import radians, sin, cos, sqrt, atan2
from datetime import timedelta

# Configurar página
st.set_page_config(page_title="Portugal VFR — Plano de Voo", layout="wide")

# Sessão para plano de voo
if "rota" not in st.session_state:
    st.session_state["rota"] = []

# Distância Haversine (NM)
def haversine_nm(lat1, lon1, lat2, lon2):
    R = 6371.0  # raio da Terra em km
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    km = R * c
    return km * 0.539957  # km para NM

# Conversor de coordenadas
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

# Parse de dados
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
            code = ident or name
            rows.append({"source":"AD","code":code,"name":name,"city":city,"lat":lat,"lon":lon})
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

# Carregar dados
ad_df = parse_ad(pd.read_csv("AD-HEL-ULM.csv"))
loc_df = parse_localidades(pd.read_csv("Localidades-Nova-versao-230223.csv"))
all_df = pd.concat([ad_df, loc_df], ignore_index=True)

# Filtros
col1, col2, col3 = st.columns([1,1,6])
with col1:
    show_ad = st.checkbox("AD/HEL/ULM", value=True)
with col2:
    show_loc = st.checkbox("Localidades", value=True)
with col3:
    filtro = st.text_input("🔍 Filtro (código, nome, cidade)", "")

if filtro:
    f = filtro.lower()
    ad_df = ad_df[ad_df.apply(lambda r: f in str(r["code"]).lower() or f in str(r["name"]).lower() or f in str(r.get("city","")).lower(), axis=1)]
    loc_df = loc_df[loc_df.apply(lambda r: f in str(r["code"]).lower() or f in str(r["name"]).lower() or f in str(r.get("sector","")).lower(), axis=1)]

# Mapa central
mean_lat = all_df["lat"].mean()
mean_lon = all_df["lon"].mean()
m = folium.Map(location=[mean_lat, mean_lon], zoom_start=6, control_scale=True)

# Agrupar pontos
cluster = MarkerCluster(name="Pontos").add_to(m)

# Função de popup com botão
def make_popup(p):
    info = f"<b>{p['code']}</b><br>{p['name']}<br>Lat: {p['lat']:.4f}<br>Lon: {p['lon']:.4f}<br>"
    button = f"""
    <form action="" method="post">
        <input type="hidden" name="code" value="{p['code']}">
        <input type="submit" value="➕ Adicionar à rota" style="margin-top:5px;">
    </form>
    """
    return folium.Popup(info + button, max_width=250)

# Adicionar marcadores
for _, p in pd.concat([ad_df if show_ad else pd.DataFrame(), loc_df if show_loc else pd.DataFrame()]).iterrows():
    folium.Marker(
        location=[p["lat"], p["lon"]],
        tooltip=f"{p['code']} — {p['name']}",
        popup=make_popup(p),
        icon=folium.Icon(color="blue" if p["source"] == "AD" else "green", icon="info-sign")
    ).add_to(cluster)

# Traçar rota
rota = st.session_state["rota"]
if len(rota) >= 2:
    coords = [(p["lat"], p["lon"]) for p in rota]
    folium.PolyLine(coords, color="red", weight=4, opacity=0.8, tooltip="Rota").add_to(m)

# Mostrar mapa
st_data = st_folium(m, height=700, returned_objects=["last_object_clicked"])

# Adicionar ponto se clicado
clicked = st_data.get("last_object_clicked")
if clicked:
    lat, lon = round(clicked["lat"], 5), round(clicked["lng"], 5)
    for _, row in all_df.iterrows():
        if abs(row["lat"] - lat) < 0.0005 and abs(row["lon"] - lon) < 0.0005:
            if row["code"] not in [p["code"] for p in rota]:
                st.session_state["rota"].append(dict(row))
                st.experimental_rerun()

# Mostrando plano de voo
st.markdown("### ✈️ Plano de Voo")
if not rota:
    st.info("Nenhum ponto selecionado ainda. Clique nos pontos no mapa para adicioná-los.")
else:
    for i, p in enumerate(rota):
        st.write(f"{i+1}. {p['code']} — {p['name']} ({p['lat']:.4f}, {p['lon']:.4f})")

    # Distância total
    total_nm = 0
    for i in range(len(rota)-1):
        total_nm += haversine_nm(rota[i]["lat"], rota[i]["lon"], rota[i+1]["lat"], rota[i+1]["lon"])

    cruise = st.number_input("Velocidade de cruzeiro (nós)", value=90, min_value=30, max_value=300)
    if cruise > 0:
        time_hours = total_nm / cruise
        duration = timedelta(hours=time_hours)
        st.success(f"🧭 Distância total: {total_nm:.1f} NM — Tempo estimado: {str(duration)[:-3]}")

    if st.button("🗑️ Limpar rota"):
        st.session_state["rota"] = []
        st.experimental_rerun()



