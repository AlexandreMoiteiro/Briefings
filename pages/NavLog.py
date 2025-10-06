# =========================
# Tabela Única: Legs + Altitudes/Holds
# =========================
st.subheader("Planeamento — Legs + Altitudes/Holds (tudo numa só tabela)")
st.caption(
    "Edita TC/Dist da perna e a altitude/hold do **destino da perna**. "
    "DEP/ARR ficam sempre fixos às respetivas elevações."
)

points = st.session_state.points
legs   = st.session_state.plan_rows
alts   = st.session_state.alt_rows
N = len(legs)

# Construção da tabela combinada (uma linha por perna; os campos de altitude referem-se ao DESTINO da perna)
combo_rows = []
for i in range(N):
    frm, to = legs[i]["From"], legs[i]["To"]
    tc  = float(legs[i].get("TC", 0.0))
    dst = float(legs[i].get("Dist", 0.0))
    # Alt do destino (índice i+1 em alt_rows)
    to_alt_row = (alts[i+1] if (i+1) < len(alts) else {"Fix": False, "Alt_ft": float(st.session_state.cruise_alt), "Hold": False, "Hold_min": 0.0})
    combo_rows.append({
        "Leg": i+1,
        "From": frm,
        "To": to,
        "TC (°T)": tc,
        "Dist (nm)": dst,
        "Fixar destino?": bool(to_alt_row.get("Fix", False)),
        "Alt destino (ft)": float(to_alt_row.get("Alt_ft", st.session_state.cruise_alt)),
        "Hold no destino?": bool(to_alt_row.get("Hold", False)),
        "Min no hold": float(to_alt_row.get("Hold_min", 0.0)),
    })

# Colunas / edição (From/To/Leg = readonly)
combo_cfg = {
    "Leg":            st.column_config.NumberColumn("Leg", disabled=True),
    "From":           st.column_config.TextColumn("From", disabled=True),
    "To":             st.column_config.TextColumn("To", disabled=True),
    "TC (°T)":        st.column_config.NumberColumn("TC (°T)", step=0.1, min_value=0.0, max_value=359.9),
    "Dist (nm)":      st.column_config.NumberColumn("Dist (nm)", step=0.1, min_value=0.0),
    "Fixar destino?": st.column_config.CheckboxColumn("Fixar destino?"),
    "Alt destino (ft)": st.column_config.NumberColumn("Alt destino (ft)", step=50, min_value=0.0),
    "Hold no destino?": st.column_config.CheckboxColumn("Hold no destino?"),
    "Min no hold":    st.column_config.NumberColumn("Min no hold", step=1.0, min_value=0.0),
}

# Editor sem st.form (para não perder edições); apply num botão
edited_combo = st.data_editor(
    combo_rows,
    key="combo_table",
    hide_index=True,
    use_container_width=True,
    num_rows="fixed",
    column_config=combo_cfg,
    column_order=list(combo_cfg.keys()),
)

if st.button("Aplicar Legs + Altitudes/Holds", type="primary"):
    # 1) Atualizar plan_rows a partir da tabela combinada
    new_legs = []
    for i in range(N):
        row = edited_combo[i]
        new_legs.append({
            "From": points[i],
            "To":   points[i+1],
            "TC":   float(row.get("TC (°T)", 0.0)),
            "Dist": float(row.get("Dist (nm)", 0.0)),
        })
    st.session_state.plan_rows = new_legs

    # 2) Atualizar alt_rows (por índice)
    #    DEP (idx 0) e ARR (idx -1) ficam sempre fixos às elevações.
    dep_elev  = _round_alt(aero_elev(points[0]))
    arr_elev  = _round_alt(aero_elev(points[-1]))
    cruise    = _round_alt(st.session_state.cruise_alt)

    new_alts = []
    for i, p in enumerate(points):
        if i == 0:
            new_alts.append({"Fix": True, "Point": p, "Alt_ft": float(dep_elev), "Hold": False, "Hold_min": 0.0})
        elif i == len(points)-1:
            new_alts.append({"Fix": True, "Point": p, "Alt_ft": float(arr_elev), "Hold": False, "Hold_min": 0.0})
        else:
            # linha da perna i-1 em edited_combo contém os campos do destino p
            src = edited_combo[i-1] if (i-1) < len(edited_combo) else {}
            alt_ft = float(src.get("Alt destino (ft)", cruise))
            fix_on = bool(src.get("Fixar destino?", False))
            hold_on = bool(src.get("Hold no destino?", False))
            hold_min = float(src.get("Min no hold", 0.0))
            new_alts.append({"Fix": fix_on, "Point": p, "Alt_ft": alt_ft, "Hold": hold_on, "Hold_min": hold_min})

    # 2a) Auto-fixar se Alt_ft ≠ cruise (apenas pontos intermédios)
    if st.session_state.auto_fix_edits:
        for i in range(1, len(points)-1):
            try:
                if abs(float(new_alts[i]["Alt_ft"]) - float(cruise)) >= 1 and not bool(new_alts[i]["Fix"]):
                    new_alts[i]["Fix"] = True
            except Exception:
                pass

    st.session_state.alt_rows = new_alts
    st.session_state["__alts_applied_at__"] = dt.datetime.utcnow().isoformat()
    st.success("Legs + Altitudes/Holds aplicados.")
    st.rerun()



