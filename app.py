import streamlit as st
from PIL import Image
import openai
import io
import base64
from streamlit_cropper import st_cropper
from fpdf import FPDF
import fitz
import datetime
import unicodedata
import re
import airportsdata
from metar.Metar import Metar

openai.api_key = st.secrets["OPENAI_API_KEY"]
AIRPORTS = airportsdata.load('ICAO')

def ascii_safe(text):
    if not isinstance(text, str):
        text = str(text)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')

def downscale_image(img, width=900):
    if img.width > width:
        ratio = width / img.width
        new_size = (width, int(img.height * ratio))
        img = img.resize(new_size)
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG", optimize=True)
    img_bytes.seek(0)
    return img, img_bytes

def get_aerodrome_info(icao):
    info = AIRPORTS.get(icao.upper())
    if not info:
        return "", icao.upper()
    lat = f"{abs(info['lat']):.4f}{'N' if info['lat'] >= 0 else 'S'}"
    lon = f"{abs(info['lon']):.4f}{'E' if info['lon'] >= 0 else 'W'}"
    name = info['name'].title()
    return f"{name}, {info['country']} {lat} {lon}", name.upper()

def clean_markdown(text):
    # Remove Markdown and bullet styling
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"#+\s?", "", text)
    text = re.sub(r"[*•\-]\s+", "", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"[_`]", "", text)
    text = re.sub(r"\n{2,}", "\n\n", text)  # max double linebreaks
    return text.strip()

def ai_chart_analysis(img_base64, chart_type, user_area_desc):
    sys_prompt = (
        "You are briefing a preflight. Write a flowing, student-style, operational weather analysis for the selected area of this aviation chart. "
        "Do NOT write a bullet list or use formatting. Speak in the first person plural (e.g., 'We should expect...'). "
        "Make it a practical, concise paragraph a student might present aloud. "
        "Cover the main weather features for this area, including: fronts, clouds, winds, visibility, temperature, pressure, and any hazards. "
        "Do not mention artificial intelligence or automation. Do not use bold, bullets, or Markdown. Do not use headings. Only a well-written paragraph."
    )
    area = user_area_desc.strip() or "Portugal"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Please focus only on: {area}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                ]
            }
        ],
        max_tokens=700,
        temperature=0.4
    )
    return clean_markdown(response.choices[0].message.content)

def ai_sigmet_summary(sigmet_text):
    prompt = (
        "You are a student pilot. Given these SIGMET/AIRMET/GAMET en-route weather warnings, write a short flowing English summary, no more than a paragraph, in practical preflight style. "
        "Do NOT use bullet points or formatting. Mention the key weather hazards and main recommendations."
    )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": sigmet_text}
        ],
        max_tokens=160,
        temperature=0.3
    )
    return clean_markdown(response.choices[0].message.content.strip())

def brief_metar_taf_comment(metar_code, taf_code):
    prompt = (
        "Given this METAR and TAF, write a very brief and practical summary for pilots (one or two sentences max). "
        "Mention main weather concerns or favorable aspects, but keep it short and simple. No formatting or Markdown, just clear English."
    )
    content = f"METAR:\n{metar_code}\nTAF:\n{taf_code}"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": content}
        ],
        max_tokens=90,
        temperature=0.2
    )
    return clean_markdown(response.choices[0].message.content.strip())

def decode_metar(metar_code):
    try:
        m = Metar(metar_code)
        station = m.station_id or "Unknown"
        info, name = get_aerodrome_info(station)
        result = []
        if info:
            result.append(f"{station}: {info}")
        else:
            result.append(f"{station}")
        if m.time:
            obs = m.time
            result.append(f"Observation time: [Day: {obs.day:02d}] [Time: {obs.hour:02d}{obs.minute:02d}]")
        if m.wind_speed:
            ws = m.wind_speed.value('MPS')
            wd = m.wind_dir.value() if m.wind_dir else None
            if wd:
                result.append(f"Wind: {wd}° at {ws:.1f} m/s")
            else:
                result.append(f"Wind: variable at {ws:.1f} m/s")
        if m.vis:
            vis = m.vis.value('KM')
            if "CAVOK" in metar_code:
                result.append("Visibility: 10km or more (CAVOK)")
            else:
                result.append(f"Visibility: {vis} km")
        skystr = []
        if m.sky:
            cb = any([s[0] == "CB" for s in m.sky])
            if "CAVOK" in metar_code:
                skystr.append("No cloud below 1500m and no Cumulonimbus")
            else:
                for s in m.sky:
                    typ, height = s[0], s[1]*30.48 if s[1] else None
                    if typ == "CB":
                        skystr.append("Cumulonimbus present")
                    elif height is not None:
                        skystr.append(f"{typ} at {int(height)}m")
                if not skystr:
                    skystr.append("No significant clouds reported")
            result.append("; ".join(skystr))
        else:
            result.append("No cloud below 1500m and no Cumulonimbus")
        wx = getattr(m, "weather", [])
        if not wx or (len(wx) == 1 and wx[0] == ""):
            result.append("No significant weather phenomena")
        else:
            result.append(f"Weather phenomena: {'; '.join(wx)}")
        if m.temp:
            result.append(f"Air Temp: {m.temp.value():.0f}°C")
        if m.dewpt:
            result.append(f"Dew Point: {m.dewpt.value():.0f}°C")
        if m.press:
            result.append(f"QNH: {m.press.value():.0f} hPa")
        return "\n".join(result)
    except Exception as e:
        return f"Could not decode METAR: {e}"

def decode_taf(taf_code):
    airports = AIRPORTS
    match = re.search(r'\b([A-Z]{4})\b', taf_code)
    icao = match.group(1) if match else "UNKNOWN"
    info = airports.get(icao, None)
    name = info['name'].upper() if info else icao
    country = info['country'] if info else ""
    lat = info['lat'] if info else 0
    lon = info['lon'] if info else 0
    lat_str = f"{abs(lat):.4f}{'N' if lat >= 0 else 'S'}"
    lon_str = f"{abs(lon):.4f}{'E' if lon >= 0 else 'W'}"
    lines = []
    lines.append(f"{icao}: {name}, {country} {lat_str} {lon_str}")
    obs_time = re.search(r'(\d{2})(\d{2})(\d{2})Z', taf_code)
    if obs_time:
        lines.append(f"Observation time: [Day {obs_time.group(1)} {obs_time.group(2)}:00]")
    period = re.search(r'(\d{2})(\d{2})/(\d{2})(\d{2})', taf_code)
    if period:
        lines.append(f"Forecast start: [Day {period.group(1)} {period.group(2)}:00] Until: [Day {period.group(3)} {period.group(4)}:00]")
    taf_main = taf_code.split('\n')[0]
    wind_match = re.search(r'(VRB|\d{3})(\d{2,3})KT', taf_main)
    wind_dir = wind_match.group(1) if wind_match else "variable"
    wind_spd = wind_match.group(2) if wind_match else ""
    wind_str = f"Wind: {wind_dir if wind_dir != 'VRB' else 'variable'}"
    wind_speed = f"{float(wind_spd)*0.514:.1f} m/s ({wind_spd}kt)" if wind_spd else ""
    vis_match = re.search(r' (\d{4}) ', taf_main)
    vis_str = "Visibility: 10km or more (CAVOK)" if "CAVOK" in taf_main or (vis_match and int(vis_match.group(1)) >= 9999) else f"Visibility: {int(vis_match.group(1))/1000:.0f}km" if vis_match else ""
    clouds = []
    if "CAVOK" in taf_main:
        clouds.append("No cloud below 1500m and no Cumulonimbus")
    else:
        cloud_matches = re.findall(r'(FEW|SCT|BKN|OVC)(\d{3})', taf_main)
        for typ, lvl in cloud_matches:
            height = int(lvl)*30.48
            clouds.append(f"{typ} at {int(height)}m")
        if not clouds:
            clouds.append("No significant clouds reported")
    clouds_str = "; ".join(clouds)
    wx_str = "No significant weather phenomena" if not re.search(r'(RA|SN|TS|FG|BR)', taf_main) else ""
    lines.extend([wind_str, wind_speed, vis_str, clouds_str, wx_str])
    return "\n".join([l for l in lines if l.strip()])

def ai_conclusion(metar_taf_pairs, sigmet_text, chart_analyses, notam_data, aircraft="", mission=""):
    summary = []
    summary.append("METAR/TAF Briefing:")
    for idx, entry in enumerate(metar_taf_pairs):
        summary.append(f"Aerodrome: {entry['icao']} - {entry['aerodrome']}")
        summary.append(f"METAR: {entry['metar']}")
        summary.append(f"TAF: {entry['taf']}")
    summary.append("SIGMET/AIRMET/GAMET:")
    summary.append(sigmet_text)
    for chart_type, analysis in chart_analyses.items():
        summary.append(f"{chart_type} Analysis: {analysis}")
    summary.append("NOTAMs:")
    for entry in notam_data:
        aerodrome = entry["aero"]
        for n in entry["notams"]:
            summary.append(f"{aerodrome}: {n['num']} {n['text']}")
    full_context = "\n".join(summary)
    prompt = (
        "You are a flight dispatcher. Based on the following preflight weather briefing and NOTAM information, "
        "write a short but practical operational dispatch decision (go/no-go) considering all the data. "
        "Evaluate weather conditions for departure, arrival, and enroute; consider meteorological minima; highlight critical NOTAMs and any operational limitations. "
        "Give a conclusion for dispatch, using clear aviation English. Do not mention being an AI or generating the report. No formatting or Markdown, just well-written text."
    )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": full_context}
        ],
        max_tokens=260,
        temperature=0.2
    )
    return clean_markdown(response.choices[0].message.content.strip())

class BriefingPDF(FPDF):
    def header(self): pass
    def footer(self):
        self.set_y(-13)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 7, ascii_safe(f"Page {self.page_no()}"), align='C')
    def section_header(self, title):
        self.set_font("Arial", 'B', 15)
        self.set_text_color(28, 44, 80)
        self.cell(0, 10, ascii_safe(title), ln=True)
        self.set_draw_color(70, 130, 180)
        self.set_line_width(1.0)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(5)
        self.set_line_width(0.2)
    def add_section_page(self, title):
        self.add_page()
        self.section_header(title)
    def cover_page(self, pilot, aircraft, date, callsign, mission):
        self.add_page()
        self.set_xy(0,38)
        self.set_font("Arial", 'B', 23)
        self.set_text_color(28, 44, 80)
        self.cell(0, 15, ascii_safe("Preflight Weather Briefing & NOTAMs"), ln=True, align='C')
        self.ln(10)
        self.set_font("Arial", '', 14)
        self.set_text_color(44,44,44)
        self.cell(0, 8, ascii_safe(f"Pilot: {pilot}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Aircraft: {aircraft}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Callsign: {callsign}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Mission #: {mission}"), ln=True, align='C')
        self.cell(0, 8, ascii_safe(f"Date: {date}"), ln=True, align='C')
        self.ln(30)
    def metar_taf_section(self, pairs):
        for i, entry in enumerate(pairs, 1):
            aerodrome = entry['aerodrome']
            icao = entry['icao']
            metar_code = entry['metar']
            taf_code = entry['taf']
            self.add_section_page(f"{icao} ({aerodrome})")
            # METAR
            self.set_font("Arial", 'B', 13)
            self.set_text_color(40,40,40)
            self.cell(0, 8, "METAR (Raw):", ln=True)
            self.set_font("Arial", '', 12)
            self.set_text_color(0,0,0)
            self.multi_cell(0, 8, ascii_safe(metar_code))
            self.ln(2)
            self.set_font("Arial", 'B', 13)
            self.set_text_color(40,40,40)
            self.cell(0, 8, "Decoded METAR:", ln=True)
            self.set_font("Arial", '', 12)
            self.set_text_color(0,0,0)
            self.multi_cell(0, 8, ascii_safe(decode_metar(metar_code)))
            self.ln(5)
            # TAF
            self.set_font("Arial", 'B', 13)
            self.set_text_color(40,40,40)
            self.cell(0, 8, "TAF (Raw):", ln=True)
            self.set_font("Arial", '', 12)
            self.set_text_color(0,0,0)
            self.multi_cell(0, 8, ascii_safe(taf_code))
            self.ln(2)
            self.set_font("Arial", 'B', 13)
            self.set_text_color(40,40,40)
            self.cell(0, 8, "Decoded TAF:", ln=True)
            self.set_font("Arial", '', 12)
            self.set_text_color(0,0,0)
            self.multi_cell(0, 8, ascii_safe(decode_taf(taf_code)))
            self.ln(6)
                        # Short AI comment for METAR/TAF
            if metar_code.strip() or taf_code.strip():
                self.set_font("Arial", 'I', 11)
                self.set_text_color(80, 56, 0)
                try:
                    comment = brief_metar_taf_comment(metar_code, taf_code)
                    self.multi_cell(0, 8, f"Summary: {ascii_safe(comment)}")
                except Exception as e:
                    self.multi_cell(0, 8, f"(Short comment failed: {e})")
            self.ln(6)

    def enroute_section(self, text, ai_summary):
        if text.strip():
            self.add_section_page("En-route Weather Warnings (SIGMET/AIRMET/GAMET)")
            self.set_font("Arial", 'B', 13)
            self.cell(0, 8, "Raw Text:", ln=True)
            self.set_font("Arial", '', 12)
            self.multi_cell(0, 8, ascii_safe(text))
            self.ln(2)
            if ai_summary:
                self.set_font("Arial", 'B', 13)
                self.cell(0, 8, "Summary:", ln=True)
                self.set_font("Arial", '', 12)
                self.multi_cell(0, 8, ascii_safe(ai_summary))
                self.ln(4)

    def chart_section(self, title, img_bytes, ai_text, user_desc=""):
        self.add_section_page(title)
        if user_desc.strip():
            self.set_font("Arial", 'I', 11)
            self.set_text_color(70,70,70)
            self.cell(0, 7, ascii_safe(f"Area/focus: {user_desc.strip()}"), ln=True)
            self.set_text_color(0,0,0)
        self.ln(2)
        chart_img_path = "tmp_chart.png"
        with open(chart_img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.set_font("Arial", '', 11)
        self.image(chart_img_path, x=22, w=168)
        self.ln(7)
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ascii_safe(ai_text))
        self.ln(2)

    def notam_section(self, notam_data):
        if not notam_data:
            return
        self.add_section_page("NOTAM Information")
        for entry in notam_data:
            if entry["aero"].strip():
                info, name = get_aerodrome_info(entry["aero"])
                self.set_font("Arial", 'B', 18)
                self.set_text_color(28, 44, 80)
                self.cell(0, 12, ascii_safe(f"{entry['aero'].upper()} ({name})"), ln=True)
                self.ln(3)
            self.set_text_color(0,0,0)
            self.set_font("Arial", '', 12)
            for nidx, notam in enumerate(entry["notams"], 1):
                if notam["num"].strip() or notam["text"].strip():
                    self.set_font("Arial",'B',12)
                    self.cell(0, 8, f"NOTAM: {notam['num']}", ln=True)
                    self.set_font("Arial",'',12)
                    self.multi_cell(0, 8, ascii_safe(notam["text"]))
                    self.ln(3)
            self.ln(6)

    def conclusion(self, ai_text):
        self.add_section_page("Operational Dispatch Conclusion")
        self.set_font("Arial", '', 13)
        self.multi_cell(0, 8, ascii_safe(ai_text))
        self.ln(2)

# --- STREAMLIT NOTAM and METAR/TAF ENTRY BLOCKS ---

def notam_block():
    if "notam_data" not in st.session_state:
        st.session_state.notam_data = [{"aero": "", "notams": [{"num": "", "text": ""}]}]
    st.subheader("6. NOTAMs by Aerodrome")
    for idx, entry in enumerate(st.session_state.notam_data):
        with st.expander(f"NOTAMs for Aerodrome {idx+1}", expanded=True):
            entry["aero"] = st.text_input("Aerodrome ICAO or Name", value=entry["aero"], key=f"notam_aero_{idx}")
            num_notams = len(entry["notams"])
            for nidx in range(num_notams):
                cols = st.columns([0.26, 0.74])
                entry["notams"][nidx]["num"] = cols[0].text_input(f"NOTAM Number", value=entry["notams"][nidx]["num"], key=f"notam_num_{idx}_{nidx}")
                entry["notams"][nidx]["text"] = cols[1].text_area(f"NOTAM {nidx+1} Text", value=entry["notams"][nidx]["text"], key=f"notam_{idx}_{nidx}")
            col_add, col_rm = st.columns([0.22,0.22])
            if col_add.button("Add NOTAM", key=f"addnotam_{idx}"):
                entry["notams"].append({"num": "", "text": ""})
            if num_notams > 1 and col_rm.button("Remove NOTAM", key=f"rmnotam_{idx}"):
                entry["notams"].pop()
    btncols = st.columns([0.23,0.23])
    if btncols[0].button("Add Aerodrome NOTAM"):
        st.session_state.notam_data.append({"aero":"", "notams":[{"num": "", "text": ""}]})
    if len(st.session_state.notam_data)>1 and btncols[1].button("Remove Last Aerodrome NOTAM"):
        st.session_state.notam_data.pop()

def metar_taf_block():
    if "metar_taf_pairs" not in st.session_state:
        st.session_state.metar_taf_pairs = [{"icao":"", "aerodrome":"", "metar":"", "taf":""}]
    st.subheader("2. METAR/TAF by Aerodrome")
    remove_pair = st.button("Remove last Aerodrome") if len(st.session_state.metar_taf_pairs) > 1 else None
    for i, entry in enumerate(st.session_state.metar_taf_pairs):
        with st.expander(f"METAR/TAF for Aerodrome {i+1}", expanded=True):
            cols = st.columns([0.25, 0.75])
            entry["icao"] = cols[0].text_input("ICAO", value=entry["icao"], key=f"icao_{i}")
            entry["aerodrome"] = cols[1].text_input("Aerodrome Name", value=entry["aerodrome"], key=f"aerodrome_{i}")
            entry["metar"] = st.text_area(f"METAR (raw code)", value=entry["metar"], key=f"metar_{i}")
            entry["taf"] = st.text_area(f"TAF (raw code)", value=entry["taf"], key=f"taf_{i}")
    if st.button("Add another Aerodrome"):
        st.session_state.metar_taf_pairs.append({"icao":"", "aerodrome":"", "metar":"", "taf":""})
    if remove_pair:
        st.session_state.metar_taf_pairs.pop()

def sigmet_block():
    st.subheader("5. En-route Weather Warnings (SIGMET/AIRMET/GAMET)")
    return st.text_area("SIGMET/AIRMET/GAMET:", height=110, key="sigmet_area")

# ---------------------- STREAMLIT MAIN APP -----------------------

st.title("Preflight Weather Briefing and NOTAMs")

with st.expander("1. Pilot/Aircraft Info", expanded=True):
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")
    mission = st.text_input("Mission #", "")
    date = st.date_input("Date", datetime.date.today())

metar_taf_block()

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
        spc_desc = st.text_input("SPC: Area/focus for analysis (optional)", value=st.session_state["spc_desc"], key="spcdesc")
        cropped_spc, cropped_spc_bytes = downscale_image(cropped_spc)
        st.session_state["cropped_spc_bytes"] = cropped_spc_bytes
        st.session_state["spc_desc"] = spc_desc

sigmet_gamet_text = sigmet_block()
notam_block()

ready = (
    st.session_state.get("spc_full_bytes")
    and st.session_state.get("cropped_spc_bytes")
    and st.session_state.get("sigwx_img_bytes")
)
if ready:
    if st.button("Generate PDF Report"):
        with st.spinner("Preparing your preflight briefing..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=14)
            pdf.cover_page(pilot, aircraft, str(date), callsign, mission)
            # Only include METAR/TAF with any field filled
            metar_taf_pairs = [
                entry for entry in st.session_state.metar_taf_pairs
                if entry['metar'].strip() or entry['taf'].strip() or entry['icao'].strip() or entry['aerodrome'].strip()
            ]
            if metar_taf_pairs:
                pdf.metar_taf_section(metar_taf_pairs)
            sigmet_ai_summary = ai_sigmet_summary(sigmet_gamet_text) if sigmet_gamet_text.strip() else ""
            pdf.enroute_section(sigmet_gamet_text, sigmet_ai_summary)
            # SIGWX
            sigwx_base64 = base64.b64encode(st.session_state["sigwx_img_bytes"].getvalue()).decode("utf-8")
            sigwx_ai_text = ai_chart_analysis(sigwx_base64, "SIGWX", st.session_state["sigwx_desc"])
            pdf.chart_section(
                title="Significant Weather Chart (SIGWX)",
                img_bytes=st.session_state["sigwx_img_bytes"],
                ai_text=sigwx_ai_text,
                user_desc=st.session_state["sigwx_desc"]
            )
            # SPC (FIXED: show cropped area, not the full SPC)
            spc_base64 = base64.b64encode(st.session_state["cropped_spc_bytes"].getvalue()).decode("utf-8")
            spc_ai_text = ai_chart_analysis(spc_base64, "SPC", st.session_state["spc_desc"])
            pdf.chart_section(
                title="Surface Pressure Chart (SPC)",
                img_bytes=st.session_state["cropped_spc_bytes"],
                ai_text=spc_ai_text,
                user_desc=st.session_state["spc_desc"]
            )
            pdf.notam_section(st.session_state.notam_data)
            chart_analyses = {
                "SIGWX": sigwx_ai_text,
                "SPC": spc_ai_text
            }
            ai_conc = ai_conclusion(
                metar_taf_pairs,
                sigmet_gamet_text,
                chart_analyses,
                st.session_state.notam_data,
                aircraft=aircraft,
                mission=mission
            )
            pdf.conclusion(ai_conc)
            out_pdf = f"Briefing_{ascii_safe(pilot)}_{ascii_safe(mission)}.pdf"
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







