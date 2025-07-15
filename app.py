Perfeito, agora ficou super claro!
Veja como ficará:

---

### **O que vai mudar:**

1. **NOTAMs**:

   * O usuário escolhe o aeródromo (pode ser texto livre ou lista de ICAO).
   * Para cada aeródromo, pode adicionar vários NOTAMs separados.
   * Você pode adicionar/remover aeródromos NOTAMs.

2. **SIGMET/AIRMET/GAMET**:

   * Tudo em um único painel/text area para inserir quantos quiser.
   * Vão para a mesma seção no PDF (“En-route Weather Warnings”).

---

## **Aqui está o código completo, atualizado:**

````python
# ... (restante dos imports e funções permanecem IGUAIS ao código anterior) ...

# -------------- NOTAMS MULTI-AERODROME SECTION ------------------
def notam_block():
    if "notam_data" not in st.session_state:
        st.session_state.notam_data = [{"aero": "", "notams": [""]}]
    st.subheader("6. NOTAMs by Aerodrome")
    for idx, entry in enumerate(st.session_state.notam_data):
        st.markdown(f"**Aerodrome {idx+1}**")
        cols = st.columns([0.6, 0.4])
        entry["aero"] = cols[0].text_input("Aerodrome ICAO or Name", value=entry["aero"], key=f"notam_aero_{idx}")
        num_notams = len(entry["notams"])
        for nidx in range(num_notams):
            entry["notams"][nidx] = cols[1].text_area(f"NOTAM {nidx+1}", value=entry["notams"][nidx], key=f"notam_{idx}_{nidx}")
        col_add, col_rm = st.columns([0.15,0.15])
        if col_add.button("Add NOTAM", key=f"addnotam_{idx}"):
            entry["notams"].append("")
        if num_notams > 1 and col_rm.button("Remove NOTAM", key=f"rmnotam_{idx}"):
            entry["notams"].pop()
    btncols = st.columns([0.25,0.25])
    if btncols[0].button("Add Aerodrome NOTAM"):
        st.session_state.notam_data.append({"aero":"", "notams":[""]})
    if len(st.session_state.notam_data)>1 and btncols[1].button("Remove Last Aerodrome NOTAM"):
        st.session_state.notam_data.pop()

# -------------- SIGMET/AIRMET/GAMET SECTION ------------------
def sigmet_block():
    st.subheader("5. En-route Weather Warnings")
    st.markdown("_Paste all relevant **SIGMET, AIRMET, GAMET** info below (raw or decoded)_")
    return st.text_area("SIGMET/AIRMET/GAMET:", height=110, key="sigmet_area")

# ----------------- MAIN STREAMLIT APP --------------------------

st.title("Preflight Weather Briefing and NOTAMs")

# (info, metar/taf, charts...) <igual ao anterior>

# 1. Pilot/Aircraft Info (igual)
with st.expander("1. Pilot/Aircraft Info", expanded=True):
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")
    date = st.date_input("Date", datetime.date.today())

# 2. METAR/TAF Pairs (igual ao anterior)
if "metar_taf_pairs" not in st.session_state:
    st.session_state.metar_taf_pairs = [("", "")]
st.subheader("2. METAR/TAF Pairs (by Aerodrome)")
remove_pair = st.button("Remove last Aerodrome") if len(st.session_state.metar_taf_pairs) > 1 else None
for i, (metar, taf) in enumerate(st.session_state.metar_taf_pairs):
    st.markdown(f"**Aerodrome #{i+1}**")
    col1, col2 = st.columns(2)
    with col1:
        st.session_state.metar_taf_pairs[i] = (
            st.text_area(f"METAR (raw code)", value=metar, key=f"metar_{i}"),
            st.session_state.metar_taf_pairs[i][1]
        )
    with col2:
        st.session_state.metar_taf_pairs[i] = (
            st.session_state.metar_taf_pairs[i][0],
            st.text_area(f"TAF (raw code)", value=taf, key=f"taf_{i}")
        )
    if st.session_state.metar_taf_pairs[i][0].strip():
        decoded = decode_metar(st.session_state.metar_taf_pairs[i][0])
        st.markdown(f"**Decoded METAR:**\n\n```\n{decoded}\n```")
    if st.session_state.metar_taf_pairs[i][1].strip():
        decoded = decode_taf(st.session_state.metar_taf_pairs[i][1])
        st.markdown(f"**Decoded TAF:**\n\n```\n{decoded}\n```")
if st.button("Add another Aerodrome"):
    st.session_state.metar_taf_pairs.append(("", ""))
if remove_pair:
    st.session_state.metar_taf_pairs.pop()

# 3. SIGWX (igual)
with st.expander("3. Significant Weather Chart (SIGWX)", expanded=True):
    sigwx_file = st.file_uploader("Upload SIGWX/SWC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="sigwx")
    if "sigwx_img_bytes" not in st.session_state:
        st.session_state["sigwx_img_bytes"] = None
        st.session_state["sigwx_desc"] = "Portugal"
    if sigwx_file:
        if sigwx_file.type == "application/pdf":
            pdf_bytes = sigwx_file.read()
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = pdf_doc.load_page(0)
            pix = page.get_pixmap()
            sigwx_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
        else:
            sigwx_img = Image.open(sigwx_file).convert("RGB").copy()
        _, sigwx_img_bytes = downscale_image(sigwx_img)
        st.session_state["sigwx_img_bytes"] = sigwx_img_bytes
        st.image(sigwx_img, caption="SIGWX: Full Chart (included in PDF)")
        sigwx_desc = st.text_input("SIGWX: Area/focus for analysis (default: Portugal)", value=st.session_state["sigwx_desc"], key="sigwxdesc")
        st.session_state["sigwx_desc"] = sigwx_desc

# 4. SPC (igual)
with st.expander("4. Surface Pressure Chart (SPC)", expanded=True):
    spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
    if "spc_full_bytes" not in st.session_state:
        st.session_state["spc_full_bytes"] = None
        st.session_state["cropped_spc_bytes"] = None
        st.session_state["spc_desc"] = ""
    if spc_file:
        if spc_file.type == "application/pdf":
            pdf_bytes = spc_file.read()
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = pdf_doc.load_page(0)
            pix = page.get_pixmap()
            spc_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
        else:
            spc_img = Image.open(spc_file).convert("RGB").copy()
        _, spc_full_bytes = downscale_image(spc_img)
        st.session_state["spc_full_bytes"] = spc_full_bytes
        st.image(spc_img, caption="SPC: Full Chart (included in PDF)")
        cropped_spc = st_cropper(
            spc_img,
            aspect_ratio=None,
            box_color='red',
            return_type='image',
            realtime_update=True,
            key="spc_crop"
        )
        st.image(cropped_spc, caption="SPC: Cropped Area (for analysis)")
        spc_desc = st.text_input("SPC: Area/focus for analysis (opcional)", value=st.session_state["spc_desc"], key="spcdesc")
        cropped_spc, cropped_spc_bytes = downscale_image(cropped_spc)
        st.session_state["cropped_spc_bytes"] = cropped_spc_bytes
        st.session_state["spc_desc"] = spc_desc

# 5. SIGMET/AIRMET/GAMET ALL TOGETHER
sigmet_gamet_text = sigmet_block()

# 6. NOTAMS - NOVO BLOCO
notam_block()

# ---- PDF Generation ----
ready = (
    st.session_state.get("spc_full_bytes")
    and st.session_state.get("cropped_spc_bytes")
    and st.session_state.get("sigwx_img_bytes")
)
if ready:
    if st.button("Generate PDF Report"):
        with st.spinner("Preparing your preflight briefing..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=12)
            pdf.cover_page(pilot, aircraft, str(date), callsign)

            # METAR/TAF
            metar_taf_pairs = [
                (metar, taf)
                for metar, taf in st.session_state.metar_taf_pairs
                if metar.strip() or taf.strip()
            ]
            if metar_taf_pairs:
                pdf.metar_taf_section(metar_taf_pairs)

            # SIGMET/AIRMET/GAMET
            if sigmet_gamet_text.strip():
                pdf.section_header("En-route Weather Warnings (SIGMET/AIRMET/GAMET)")
                pdf.set_font("Arial", '', 11)
                pdf.multi_cell(0, 7, ascii_safe(sigmet_gamet_text))
                pdf.ln(2)

            # SIGWX
            sigwx_base64 = base64.b64encode(st.session_state["sigwx_img_bytes"].getvalue()).decode("utf-8")
            sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", st.session_state["sigwx_desc"])
            pdf.chart_section(
                title="Significant Weather Chart (SIGWX)",
                img_bytes=st.session_state["sigwx_img_bytes"],
                ai_text=sigwx_ai_text,
                user_desc=st.session_state["sigwx_desc"]
            )

            # SPC
            spc_base64 = base64.b64encode(st.session_state["cropped_spc_bytes"].getvalue()).decode("utf-8")
            spc_ai_text = ai_chart_analysis(spc_base64, "SPC", st.session_state["spc_desc"])
            pdf.chart_section(
                title="Surface Pressure Chart (SPC)",
                img_bytes=st.session_state["spc_full_bytes"],
                ai_text=spc_ai_text,
                user_desc=st.session_state["spc_desc"]
            )

            pdf.conclusion()

            # NOTAMs (por aeródromo)
            for entry in st.session_state.notam_data:
                if entry["aero"].strip():
                    pdf.section_header(f"NOTAMs for {entry['aero']}")
                for notam in entry["notams"]:
                    if notam.strip():
                        pdf.set_font("Arial", '', 11)
                        pdf.multi_cell(0, 8, ascii_safe(notam))
                        pdf.ln(1)

            out_pdf = "Preflight_Weather_Briefing.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                st.download_button(
                    label="Download Preflight Weather Briefing PDF",
                    data=f,
                    file_name=out_pdf,
                    mime="application/pdf"
                )
            st.success("PDF generated successfully!")
else:
    st.info("Fill all sections and upload/crop both charts before generating your PDF.")
````

---

**Agora a experiência fica exatamente como você pediu!**
Se quiser algum detalhe visual ou de texto, só falar!



