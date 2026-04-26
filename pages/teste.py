# app_multiairport_required_changes.py
# ---------------------------------------------------------------
# Blocos completos para substituir/adicionar no app.py.
# Objetivo:
#   1) Ler vários ficheiros procedures*.json na raiz do repo.
#      Ex.: procedures_lpso.json, procedures_lpcb_lpev.json, procedures_extra.json
#   2) Suportar pontos locais definidos no topo do JSON em "points".
#   3) Guardar metadados airport/kind/source_file/fpl_include nos pontos gerados.
#   4) Não colocar pontos de APPROACH no item 15/FPL.
# ---------------------------------------------------------------

# ===============================================================
# 1) CONFIG — substituir a linha PROC_FILE por isto
# ===============================================================
# Antes:
#   PROC_FILE = ROOT / "procedures_lpso.json"
# Depois:
PROC_GLOB = "procedures*.json"
PROC_FILE = ROOT / "procedures_lpso.json"  # fallback/compatibilidade


# ===============================================================
# 2) PROCEDURE JSON LOADING — substituir load_procedures_file,
#    available_procedures e load_procedure_point_catalog por estes blocos
# ===============================================================
@st.cache_data(show_spinner=False)
def load_all_procedure_files(root_str: str, pattern: str = "procedures*.json") -> Dict[str, Any]:
    root = Path(root_str)
    files = sorted(root.glob(pattern))
    merged: Dict[str, Any] = {
        "files": [],
        "airports": {},
        "points": {},
        "procedures": [],
    }

    # fallback para instalações antigas
    if not files and PROC_FILE.exists():
        files = [PROC_FILE]

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            st.warning(f"Não consegui ler {path.name}: {exc}")
            continue

        merged["files"].append(path.name)

        for airport, meta in (data.get("airports") or {}).items():
            merged["airports"][clean_code(airport)] = meta

        default_airport = clean_code(data.get("airport") or "")

        for code, point in (data.get("points") or {}).items():
            p = dict(point or {})
            code_clean = clean_code(code)
            p.setdefault("code", code_clean)
            p.setdefault("name", code_clean)
            p.setdefault("src", "PROC")
            p.setdefault("routes", "")
            p.setdefault("remarks", "")
            p.setdefault("airport", default_airport)
            p["source_file"] = path.name
            merged["points"][code_clean] = p

        for proc in data.get("procedures", []):
            p = dict(proc or {})
            p.setdefault("airport", default_airport)
            p["airport"] = clean_code(p.get("airport") or default_airport)
            p["source_file"] = path.name
            merged["procedures"].append(p)

    return merged


def procedure_store() -> Dict[str, Any]:
    return load_all_procedure_files(str(ROOT), PROC_GLOB)


@st.cache_data(show_spinner=False)
def load_procedures_file(path_str: str) -> Dict[str, Any]:
    # Mantém compatibilidade com chamadas antigas.
    return procedure_store()


def available_procedures(kind: Optional[str] = None, airport: Optional[str] = None) -> List[Dict[str, Any]]:
    procedures = list(procedure_store().get("procedures", []))
    if kind:
        procedures = [p for p in procedures if str(p.get("kind", "")).upper() == kind.upper()]
    if airport and airport != "Todos":
        procedures = [p for p in procedures if clean_code(p.get("airport")) == clean_code(airport)]
    return procedures


@st.cache_data(show_spinner=False)
def load_procedure_point_catalog(root_str: str, pattern: str = "procedures*.json") -> pd.DataFrame:
    store = load_all_procedure_files(root_str, pattern)
    rows: List[Dict[str, Any]] = []

    def add_point(
        *,
        code: str,
        name: str,
        lat: float,
        lon: float,
        alt: float = 0.0,
        airport: str = "",
        routes: str = "",
        remarks: str = "",
        source_file: str = "",
        fpl_include: Optional[bool] = None,
    ) -> None:
        code_clean = clean_code(code or name)
        if not code_clean:
            return
        rows.append({
            "code": code_clean,
            "name": name or code_clean,
            "lat": float(lat),
            "lon": float(lon),
            "alt": float(alt or 0),
            "src": "PROC",
            "routes": routes or airport,
            "remarks": remarks,
            "airport": clean_code(airport),
            "source_file": source_file,
            "fpl_include": fpl_include,
        })

    # 1) Pontos definidos no topo do JSON: "points"
    for code, p in (store.get("points") or {}).items():
        if "lat" in p and "lon" in p:
            add_point(
                code=code,
                name=str(p.get("name") or code),
                lat=float(p["lat"]),
                lon=float(p["lon"]),
                alt=float(p.get("alt", 0) or 0),
                airport=str(p.get("airport", "")),
                routes=str(p.get("routes", "")),
                remarks=str(p.get("remarks", "")),
                source_file=str(p.get("source_file", "")),
                fpl_include=p.get("fpl_include"),
            )

    # 2) Pontos embebidos diretamente nos segmentos.
    for proc in store.get("procedures", []):
        proc_id = str(proc.get("id", "PROC"))
        airport = clean_code(proc.get("airport", ""))
        source_file = str(proc.get("source_file", ""))
        fpl_default = proc.get("fpl_include_default")
        for seg in proc.get("segments", []):
            typ = str(seg.get("type", "")).lower()
            code = clean_code(seg.get("point") or seg.get("code") or seg.get("name") or "")
            name = str(seg.get("name") or seg.get("note") or seg.get("point") or seg.get("code") or code)
            alt = float(seg.get("alt", 0) or 0)

            if "lat" in seg and "lon" in seg:
                add_point(
                    code=code,
                    name=name,
                    lat=float(seg["lat"]),
                    lon=float(seg["lon"]),
                    alt=alt,
                    airport=airport,
                    routes=proc_id,
                    remarks=str(seg.get("remarks", "from procedure segment")),
                    source_file=source_file,
                    fpl_include=seg.get("fpl_include", fpl_default),
                )
                continue

            if typ in {"vor_radial_dme", "radial_to_dme"} and seg.get("vor") and seg.get("radial") is not None and seg.get("dme") is not None:
                vor = get_vor(str(seg["vor"]))
                if vor:
                    radial = float(seg["radial"])
                    dme = float(seg["dme"])
                    lat, lon = dest_point(vor["lat"], vor["lon"], radial, dme)
                    add_point(
                        code=code or f"{vor['ident']}R{int(radial):03d}D{dme:g}",
                        name=name,
                        lat=lat,
                        lon=lon,
                        alt=alt,
                        airport=airport,
                        routes=proc_id,
                        remarks=f"{vor['ident']} R{int(radial):03d} D{dme:g}",
                        source_file=source_file,
                        fpl_include=seg.get("fpl_include", fpl_default),
                    )

    cols = ["code", "name", "lat", "lon", "alt", "src", "routes", "remarks", "airport", "source_file", "fpl_include"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["code"] = df["code"].map(clean_code)
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    return df.dropna(subset=["code", "lat", "lon"]).drop_duplicates(subset=["code", "lat", "lon", "src"]).reset_index(drop=True)


def point_catalog() -> pd.DataFrame:
    proc_points = load_procedure_point_catalog(str(ROOT), PROC_GLOB)
    if proc_points.empty:
        return POINTS_DF
    return pd.concat([POINTS_DF, proc_points], ignore_index=True).drop_duplicates(subset=["code", "lat", "lon", "src"]).reset_index(drop=True)


def proc_json_point(code: str) -> Optional[Dict[str, Any]]:
    code_clean = clean_code(code)
    point = (procedure_store().get("points") or {}).get(code_clean)
    if not point:
        return None
    if "lat" not in point or "lon" not in point:
        return None
    p = dict(point)
    p.setdefault("code", code_clean)
    p.setdefault("name", code_clean)
    p.setdefault("alt", 0.0)
    p.setdefault("src", "PROC")
    p.setdefault("routes", "")
    p.setdefault("remarks", "")
    return p


# ===============================================================
# 3) PROCEDURE ENGINE — substituir proc_static_point por este
# ===============================================================
def proc_static_point(segment: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    code = clean_code(segment.get("point") or segment.get("code"))
    alt = float(segment.get("alt", proc_default_alt()))

    # 1) Segmento com lat/lon explícito.
    if "lat" in segment and "lon" in segment:
        return make_proc_point(
            code,
            segment.get("name") or segment.get("note") or code,
            float(segment["lat"]),
            float(segment["lon"]),
            alt,
            src="PROC",
            note=segment.get("note") or code,
            remarks=segment.get("remarks", "from procedure segment"),
            extra={
                "fpl_include": segment.get("fpl_include"),
                "airport": clean_code(segment.get("airport", "")),
            },
        )

    # 2) Ponto no topo do JSON, em "points".
    jp = proc_json_point(code)
    if jp:
        return make_proc_point(
            code,
            str(segment.get("name") or segment.get("note") or jp.get("name") or code),
            float(jp["lat"]),
            float(jp["lon"]),
            alt if "alt" in segment else float(jp.get("alt", alt) or alt),
            src=str(jp.get("src") or "PROC"),
            note=segment.get("note") or jp.get("name") or code,
            remarks=str(segment.get("remarks") or jp.get("remarks") or "from procedure points"),
            extra={
                "airport": clean_code(jp.get("airport", segment.get("airport", ""))),
                "source_file": jp.get("source_file", ""),
                "fpl_include": segment.get("fpl_include", jp.get("fpl_include")),
            },
        )

    # 3) Ponto normal no catálogo CSV/procedures.
    point = db_point(code, alt=alt, src_priority=["IFR", "VOR", "PROC", "AD", "VFR"])
    if point:
        d = point.to_dict()
        d["uid"] = next_uid()
        d["navlog_note"] = segment.get("note") or code
        d["no_auto_vnav"] = True
        if segment.get("fpl_include") is not None:
            d["fpl_include"] = segment.get("fpl_include")
        return d

    raise ValueError(f"Ponto {code} não está nos CSV, nem em points{}, nem tem lat/lon no segmento.")


# ===============================================================
# 4) PROCEDURE ENGINE — dentro de build_procedure_points,
#    acrescentar estes metadados nos pontos gerados.
#
# Substituir o loop final que marca proc_id/proc_instance_id por este.
# ===============================================================
def tag_generated_procedure_points(output: List[Dict[str, Any]], procedure: Dict[str, Any], proc_id: str, instance_id: str) -> None:
    proc_kind = str(procedure.get("kind", "PROC")).upper()
    proc_airport = clean_code(procedure.get("airport", ""))
    proc_source = str(procedure.get("source_file", ""))
    fpl_default = procedure.get("fpl_include_default")

    for order, point in enumerate(output):
        point["proc_id"] = proc_id
        point["proc_instance_id"] = instance_id
        point["proc_order"] = order
        point["proc_generated"] = True
        point["proc_kind"] = proc_kind
        point["airport"] = point.get("airport") or proc_airport
        point["source_file"] = point.get("source_file") or proc_source

        if point.get("fpl_include") is None:
            if fpl_default is not None:
                point["fpl_include"] = bool(fpl_default)
            elif proc_kind == "APPROACH":
                point["fpl_include"] = False
            else:
                point["fpl_include"] = True


# No fim de build_procedure_points(), imediatamente antes do return output,
# usa:
#     tag_generated_procedure_points(output, procedure, proc_id, instance_id)
#     return output


# ===============================================================
# 5) FPL / ITEM 15 — substituir route_item15 por este
# ===============================================================
def route_item15(wps: List[Dict[str, Any]]) -> str:
    if len(wps) < 2:
        return ""

    seq = wps[:]

    if re.fullmatch(r"[A-Z]{4}", clean_code(seq[0].get("code"))):
        seq = seq[1:]
    if seq and re.fullmatch(r"[A-Z]{4}", clean_code(seq[-1].get("code"))):
        seq = seq[:-1]

    tokens: List[str] = []
    for point in seq:
        src = str(point.get("src", "")).upper()
        proc_kind = str(point.get("proc_kind", "")).upper()

        # Não meter no FPL pontos calculados, procedimentos de aproximação,
        # thresholds, MAP, FAF, DME stepdown, nem pontos marcados explicitamente.
        if src in {"CALC", "PROC_DYNAMIC", "TURN"}:
            continue
        if proc_kind == "APPROACH":
            continue
        if point.get("fpl_include") is False:
            continue

        code = clean_code(point.get("code") or point.get("name"))
        if not code or (src == "USER" and code.startswith("WP")):
            code = dd_to_icao(float(point["lat"]), float(point["lon"]))
        tokens.append(code)

    # Limpa duplicados consecutivos.
    clean_tokens: List[str] = []
    for token in tokens:
        if not clean_tokens or clean_tokens[-1] != token:
            clean_tokens.append(token)

    return "DCT " + " DCT ".join(clean_tokens) if clean_tokens else ""


# ===============================================================
# 6) UI PROCEDURES — substituir o bloco "Procedimentos externos"
#    dentro da tab Rota por este bloco.
# ===============================================================
st.markdown("#### Procedimentos externos")
procedures = available_procedures()
if not procedures:
    st.warning("Coloca ficheiros procedures*.json na raiz do repo.")
else:
    airport_options = ["Todos"] + sorted({clean_code(p.get("airport")) for p in procedures if clean_code(p.get("airport"))})
    airport_filter = st.selectbox("Aeródromo", airport_options, key="proc_airport_filter")

    filtered_by_airport = available_procedures(airport=airport_filter)
    kinds = sorted(set(str(p.get("kind", "PROC")) for p in filtered_by_airport))
    kind = st.selectbox("Tipo", kinds, key="proc_kind_filter")

    choices = available_procedures(kind=kind, airport=airport_filter)
    labels = [
        f"{p.get('airport', '')} · {p.get('id')} — {p.get('name', '')} [{p.get('source_file', '')}]"
        for p in choices
    ]
    selected = st.selectbox("Procedimento", labels, key="proc_selected_label")
    mode = st.selectbox("Inserção", ["Acrescentar", "Substituir rota"], key="proc_insert_mode")

    if st.button("Adicionar procedimento", type="primary", use_container_width=True):
        selected_index = labels.index(selected)
        proc_id = choices[selected_index].get("id")
        try:
            pts = build_procedure_points(str(proc_id))
            if mode == "Substituir rota":
                st.session_state.wps = pts
            else:
                st.session_state.wps.extend(pts)
            recalc_route(refresh_procedures=False)
            st.session_state["_last_calc_sig"] = calculation_signature()
            st.success(f"{proc_id} adicionado ({len(pts)} pontos).")
            st.rerun()
        except Exception as exc:
            st.error(f"Erro ao gerar procedimento: {exc}")
