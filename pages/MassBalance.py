# =========================
# PDF – Fill template + attach calculations page
# =========================

st.markdown("### PDF – M&B and Performance Data Sheet")
reg = st.text_input("Aircraft registration", value="CS-XXX")
mission = st.text_input("Mission #", value="001")

utc_today = datetime.datetime.now(pytz.UTC)
date_str = st.text_input("Date (DD/MM/YYYY)", value=utc_today.strftime("%d/%m/%Y"))

if st.button("Generate filled PDF"):
    if not PDF_TEMPLATE.exists():
        st.error(f"Template not found: {PDF_TEMPLATE}")
        st.stop()

    # Build detailed calculations page (English, human-readable)
    calc_pdf_path = APP_DIR / f"_calc_mission_{mission}.pdf"
    calc = FPDF()
    calc.set_auto_page_break(auto=True, margin=12)
    calc.add_page()
    calc.set_font("Arial", "B", 14)
    calc.cell(0, 8, ascii_safe("Tecnam P2008 – Calculations (summary)"), ln=True)

    # W&B – concise narrative (no data repetition beyond key figures)
    calc.set_font("Arial", "B", 12); calc.cell(0, 7, ascii_safe("Weight & balance"), ln=True)
    calc.set_font("Arial", size=10)
    calc.multi_cell(0, 5, ascii_safe(
        f"Empty weight {ew:.0f} kg (moment {ew_moment:.0f} kg·m). "
        f"Student/Instructor {student:.0f}/{instructor:.0f} kg; baggage {baggage:.0f} kg. "
        f"Fuel {fuel_l:.0f} L (≈ {fuel_wt:.0f} kg). Total weight {total_weight:.0f} kg, moment {total_moment:.0f} kg·m; CG {cg:.3f} m. "
        f"Extra fuel possible: {remaining_fuel_l:.1f} L (limited by {limit_label})."
    ))

    # Performance – human narrative per aerodrome (PA from QNH/OAT; not assuming ISA)
    calc.ln(2)
    calc.set_font("Arial", "B", 12); calc.cell(0, 7, ascii_safe("Performance – method & results"), ln=True)
    calc.set_font("Arial", size=10)
    for r in perf_rows:
        calc.set_font("Arial", "B", 10)
        calc.cell(0, 6, ascii_safe(f"{r['role']} – {r['icao']} (QFU {r['qfu']:.0f}°)"), ln=True)
        calc.set_font("Arial", size=10)
        calc.multi_cell(0, 5, ascii_safe(
            f"Atmospherics: elevation {r['elev_ft']:.0f} ft, QNH {r['qnh']:.1f} → PA ≈ {r['pa_ft']:.0f} ft. "
            f"ISA at PA ≈ {r['isa_temp']:.1f} °C; with OAT {r['temp']:.1f} °C → DA ≈ {r['da_ft']:.0f} ft."
        ))
        # Method + corrections + results in plain language
        paved_flag = next(a for a in st.session_state.aerodromes if a['icao']==r['icao'])['paved']
        slope_val  = next(a for a in st.session_state.aerodromes if a['icao']==r['icao'])['slope_pc']
        calc.cell(0, 5, ascii_safe("Method: bilinear interpolation on AFM tables using PA and OAT."), ln=True)
        calc.multi_cell(0, 5, ascii_safe(
            f"Corrections: wind component {r['hw_comp']:.0f} kt, surface {'paved' if paved_flag else 'grass'}, slope {slope_val:.1f}%."
        ))
        calc.multi_cell(0, 5, ascii_safe(
            f"Results: TO ground roll ≈ {r['to_gr']:.0f} m; TO distance over 50 ft ≈ {r['to_50']:.0f} m. "
            f"Landing ground roll ≈ {r['ldg_gr']:.0f} m; landing over 50 ft ≈ {r['ldg_50']:.0f} m. "
            f"Declared: TODA {r['toda_av']:.0f} m; LDA {r['lda_av']:.0f} m."
        ))
        calc.ln(1)

    # Fuel planning – concise
    calc.ln(2)
    calc.set_font("Arial", "B", 12); calc.cell(0, 7, ascii_safe("Fuel planning (20 L/h)"), ln=True)
    calc.set_font("Arial", size=10)
    calc.multi_cell(0, 5, ascii_safe(
        f"Trip {trip_l:.1f} L; contingency 5% {cont_l:.1f} L; required ramp {req_ramp:.1f} L; extra {extra_l:.1f} L; total ramp {total_ramp:.1f} L."
    ))
    calc.output(str(calc_pdf_path))

    # Fill the form (page 1/2). Try to auto-detect field names for DEP/ARR/ALT blocks.
    def load_pdf_any(path: Path):
        try:
            return "pdfrw", Rd_pdfrw(str(path))
        except Exception:
            try:
                return "pypdf", Rd_pypdf(str(path))
            except Exception as e:
                raise RuntimeError(f"Could not read the PDF: {e}")

    engine, reader = load_pdf_any(PDF_TEMPLATE)

    FIELD_BASE = {
        "Textbox19": reg,       # Registration (adjust if you renamed)
        "Textbox18": date_str,  # Date
    }
    out_main_path = APP_DIR / f"MB_Performance_Mission_{mission}.pdf"

    def pdfrw_set_field(fields, candidates, value, color_rgb=None):
        if not isinstance(candidates, (list, tuple)): candidates = [candidates]
        for name in candidates:
            for f in fields:
                if f.get('/T') and f['/T'][1:-1] == name:
                    f.update(PdfDict(V=str(value))); f.update(PdfDict(AP=None))
                    if color_rgb:
                        r,g,b = color_rgb
                        f.update(PdfDict(DA=f"{r/255:.3f} {g/255:.3f} {b/255:.3f} rg /Helv 10 Tf"))
                    return True
        return False

    # Candidate field names per block (use the first that exists)
    ROLE_FIELDS = {
        "Departure": {
            "airfield": ["DEP_AIRFIELD","Textbox22"],
            "qfu":      ["DEP_QFU","Textbox23"],
            "elev":     ["DEP_ELEV","Textbox53"],
            "qnh":      ["DEP_QNH","Textbox52"],
            "temp":     ["DEP_TEMP","Textbox51"],
            "wind":     ["DEP_WIND","Textbox58"],
            "pa":       ["DEP_PA","Textbox50"],
            "da":       ["DEP_DA","Textbox49"],
            "toda_lda": ["DEP_TODA_LDA","Textbox47"],
            "todr":     ["DEP_TODR","Textbox45"],
            "ldr":      ["DEP_LDR","Textbox41"],
            "roc":      ["DEP_ROC","Textbox39"],
        },
        "Arrival": {
            "airfield": ["ARR_AIRFIELD","Textbox22_ARR","Textbox32"],
            "qfu":      ["ARR_QFU","Textbox23_ARR","Text2"],
            "elev":     ["ARR_ELEV","Textbox53_ARR","Textbox33"],
            "qnh":      ["ARR_QNH","Textbox52_ARR","Textbox36"],
            "temp":     ["ARR_TEMP","Textbox51_ARR","Textbox34"],
            "wind":     ["ARR_WIND","Textbox58_ARR","Textbox35"],
            "pa":       ["ARR_PA","Textbox50_ARR"],
            "da":       ["ARR_DA","Textbox49_ARR"],
            "toda_lda": ["ARR_TODA_LDA","Textbox47_ARR","Textbox43"],
            "todr":     ["ARR_TODR","Textbox45_ARR"],
            "ldr":      ["ARR_LDR","Textbox41_ARR"],
            "roc":      ["ARR_ROC","Textbox39_ARR"],
        },
        "Alternate": {
            "airfield": ["ALT_AIRFIELD","Textbox22_ALT"],
            "qfu":      ["ALT_QFU","Textbox23_ALT"],
            "elev":     ["ALT_ELEV","Textbox53_ALT"],
            "qnh":      ["ALT_QNH","Textbox52_ALT"],
            "temp":     ["ALT_TEMP","Textbox51_ALT"],
            "wind":     ["ALT_WIND","Textbox58_ALT"],
            "pa":       ["ALT_PA","Textbox50_ALT"],
            "da":       ["ALT_DA","Textbox49_ALT"],
            "toda_lda": ["ALT_TODA_LDA","Textbox47_ALT"],
            "todr":     ["ALT_TODR","Textbox45_ALT"],
            "ldr":      ["ALT_LDR","Textbox41_ALT"],
            "roc":      ["ALT_ROC","Textbox39_ALT"],
        },
    }

    engine_name = engine
    if engine_name == "pdfrw" and hasattr(reader, 'Root') and '/AcroForm' in reader.Root:
        fields = reader.Root.AcroForm.Fields

        # Base fields
        for k, v in FIELD_BASE.items(): pdfrw_set_field(fields, k, v)

        # Weight & CG with color
        wt_color = (30,150,30) if total_weight <= AC['max_takeoff_weight'] else (200,0,0)
        lo, hi = AC['cg_limits']
        if cg < lo or cg > hi:
            cg_color = (200,0,0)
        else:
            margin = 0.05*(hi-lo)
            cg_color = (200,150,30) if (cg<lo+margin or cg>hi-margin) else (30,150,30)
        pdfrw_set_field(fields, ["Textbox14","TOTAL_WEIGHT"], f"{total_weight:.1f}", wt_color)
        pdfrw_set_field(fields, ["Textbox16","CG_VALUE"], f"{cg:.3f}", cg_color)
        pdfrw_set_field(fields, ["Textbox17","MTOW"], f"{AC['max_takeoff_weight']:.0f}")

        # Extra fuel & reason (if you created fields for it)
        pdfrw_set_field(fields, ["EXTRA_FUEL","Textbox70"], f"{remaining_fuel_l:.1f} L")
        pdfrw_set_field(fields, ["EXTRA_REASON"], f"limited by {limit_label}")

        # Fill per role
        role_to_row = {r['role']: r for r in perf_rows}
        for role, names in ROLE_FIELDS.items():
            if role not in role_to_row: continue
            rr = role_to_row[role]
            pdfrw_set_field(fields, names['airfield'], rr['icao'])
            pdfrw_set_field(fields, names['qfu'], f"{rr['qfu']:.0f}°")
            pdfrw_set_field(fields, names['elev'], f"{rr['elev_ft']:.0f}")
            pdfrw_set_field(fields, names['qnh'], f"{rr['qnh']:.1f}")
            pdfrw_set_field(fields, names['temp'], f"{rr['temp']:.1f}")
            pdfrw_set_field(fields, names['wind'], f"{rr['hw_comp']:.0f} kt")
            pdfrw_set_field(fields, names['pa'], f"{rr['pa_ft']:.0f}")
            pdfrw_set_field(fields, names['da'], f"{rr['da_ft']:.0f}")
            pdfrw_set_field(fields, names['toda_lda'], f"{int(rr['toda_av'])}/{int(rr['lda_av'])}")
            pdfrw_set_field(fields, names['todr'], f"{rr['to_50']:.0f}")
            pdfrw_set_field(fields, names['ldr'], f"{rr['ldg_50']:.0f}")
            # Optional ROC estimation
            try:
                roc_val = roc_interp(rr['pa_ft'], rr['temp'], total_weight)
                pdfrw_set_field(fields, names['roc'], f"{roc_val:.0f}")
            except Exception:
                pass

        writer = Wr_pdfrw(); writer.write(str(out_main_path), reader)

        # Merge with calculations page
        base = Rd_pypdf(str(out_main_path))
        calc_doc = Rd_pypdf(str(calc_pdf_path))
        merger = Wr_pypdf()
        for p in base.pages: merger.add_page(p)
        for p in calc_doc.pages: merger.add_page(p)
        with open(out_main_path, "wb") as f: merger.write(f)

    else:
        # Fallback: base form fields only; performance blocks require exact names (pdfrw path preferred)
        base_r = Rd_pypdf(str(PDF_TEMPLATE))
        merger = Wr_pypdf()
        for p in base_r.pages: merger.add_page(p)
        if "/AcroForm" in base_r.trailer["/Root"]:
            merger._root_object.update({"/AcroForm": base_r.trailer["/Root"]["/AcroForm"]})
            merger._root_object["/AcroForm"].update({"/NeedAppearances": True})
        merger.update_page_form_field_values(base_r.pages[0], {"Textbox19": reg, "Textbox18": date_str})
        calc_doc = Rd_pypdf(str(calc_pdf_path))
        for p in calc_doc.pages: merger.add_page(p)
        with open(out_main_path, "wb") as f: merger.write(f)

    st.success("PDF generated successfully!")
    with open(out_main_path, 'rb') as f:
        st.download_button("Download PDF", f, file_name=out_main_path.name, mime="application/pdf")
