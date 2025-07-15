import streamlit as st
from PIL import Image
import openai
import io
import base64
from fpdf import FPDF
import fitz
import datetime
import unicodedata
import re
import airportsdata
from metar.Metar import Metar
import requests
import json

ADMIN_EMAIL = "alexandre.moiteiro@gmail.com"
WEBSITE_LINK = "https://mass-balance.streamlit.app/"
SENDGRID_API_KEY = st.secrets["SENDGRID_API_KEY"]
SENDER_EMAIL = "alexandre.moiteiro@students.sevenair.com"

openai.api_key = st.secrets["OPENAI_API_KEY"]
AIRPORTS = airportsdata.load('ICAO')

# ---- UTILS ----
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
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"#+\s?", "", text)
    text = re.sub(r"[*•\-]\s+", "", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"[_`]", "", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()

def ai_chart_analysis(img_base64, chart_type, user_area_desc, extra_instruction=""):
    sys_prompt = (
        "You are a student pilot preparing a preflight weather briefing. "
        "Write a detailed, natural-English, operational analysis in the first person plural (for example, 'We can expect... Our area will...'). "
        "Analyze the aviation weather chart for the focus area, but also mention relevant patterns, systems, and trends on the chart that might affect us or develop during our flight. "
        "Connect what we see on the chart to what we should expect in our area during the period, and the possible evolution. "
        "Never use bullet points or formatting. Avoid words like 'pilots should remain aware'. Write as if we are briefing our own flight. "
        "Always be practical and clear, in a natural, student-style tone. If user gives additional instructions, follow them."
    )
    area = user_area_desc.strip() or "Portugal"
    if extra_instruction:
        user_text = f"Focus on: {area}. {extra_instruction}"
    else:
        user_text = f"Focus on: {area}."
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                ]
            }
        ],
        max_tokens=850,
        temperature=0.33
    )
    return clean_markdown(response.choices[0].message.content)

def ai_sigmet_summary(sigmet_text):
    prompt = (
        "Write a short summary in English of these SIGMET/AIRMET/GAMET en-route weather warnings, in the first person plural and student pilot style. "
        "Do not use formatting. Do not say 'pilots should'. Instead, use 'We can expect... We may encounter...'."
    )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": sigmet_text}
        ],
        max_tokens=180,
        temperature=0.19
    )
    return clean_markdown(response.choices[0].message.content.strip())

def brief_metar_taf_comment(metar_code, taf_code):
    prompt = (
        "Given this METAR and TAF, write a very brief summary as a student for our own flight (one or two sentences). "
        "Speak in first person plural, avoid generic advice. Just say what we expect or any main issue."
    )
    content = f"METAR:\n{metar_code}\nTAF:\n{taf_code}"
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": content}
        ],
        max_tokens=80,
        temperature=0.18
    )
    return clean_markdown(response.choices[0].message.content.strip())

def brief_notam_comment(notams, icao):
    text = "\n".join([f"{n['num']}: {n['text']}" for n in notams if n['num'].strip() or n['text'].strip()])
    if not text.strip():
        return ""
    prompt = (
        f"Given these NOTAMs for {icao}, write a very short summary in the first person plural (e.g., 'We should take note that...'), as a student pilot would for a preflight briefing."
    )
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text}
        ],
        max_tokens=80,
        temperature=0.14
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

# --- PDF Class (as above, omitted for brevity, but use your last version) ---
# ... (paste the BriefingPDF class from your last code block here) ...

# --- EMAIL SEND ---
def send_report_email(to_email, subject, body, filename, filedata):
    html_body = f"""
    <html>
    <body>
        <h2>Weather & NOTAM Briefing Submitted</h2>
        <pre>{body}</pre>
        <p style='margin-top:1.5em;'>See attached PDF for details.</p>
        <p>Generated via {WEBSITE_LINK}</p>
    </body>
    </html>
    """
    data = {
        "personalizations": [
            {
                "to": [{"email": to_email}],
                "subject": subject
            }
        ],
        "from": {"email": SENDER_EMAIL},
        "content": [
            {
                "type": "text/html",
                "value": html_body
            }
        ],
        "attachments": [{
            "content": base64.b64encode(filedata).decode(),
            "type": "application/pdf",
            "filename": filename,
            "disposition": "attachment"
        }]
    }
    headers = {
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type": "application/json"
    }
    resp = requests.post("https://api.sendgrid.com/v3/mail/send", data=json.dumps(data), headers=headers)
    if resp.status_code >= 400:
        st.warning(f"PDF generated but failed to send email (SendGrid error: {resp.text})")

# --- UI Blocks ---
st.title("Preflight Weather Briefing and NOTAMs")

with st.expander("1. Pilot/Aircraft Info", expanded=True):
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft (e.g., Tecnam P2008 (CS-ECD))", "")
    callsign = st.text_input("Callsign", "")
    mission = st.text_input("Mission #", "")
    date = st.date_input("Date", datetime.date.today())
    time_utc = st.text_input("Expected Flight Time (UTC, e.g. 14:30-16:30)", "")

def metar_taf_block():
    if "metar_taf_pairs" not in st.session_state:
        st.session_state.metar_taf_pairs = [{"icao":"", "metar":"", "taf":""}]
    st.subheader("2. METAR/TAF by Aerodrome")
    remove_pair = st.button("Remove last Aerodrome") if len(st.session_state.metar_taf_pairs) > 1 else None
    for i, entry in enumerate(st.session_state.metar_taf_pairs):
        with st.expander(f"METAR/TAF for Aerodrome {i+1}", expanded=True):
            entry["icao"] = st.text_input("ICAO", value=entry["icao"], key=f"icao_{i}")
            entry["metar"] = st.text_area(f"METAR (raw code)", value=entry["metar"], key=f"metar_{i}")
            entry["taf"] = st.text_area(f"TAF (raw code)", value=entry["taf"], key=f"taf_{i}")
    if st.button("Add another Aerodrome"):
        st.session_state.metar_taf_pairs.append({"icao":"", "metar":"", "taf":""})
    if remove_pair:
        st.session_state.metar_taf_pairs.pop()

def chart_upload_and_analysis_block(key_prefix, label, desc_default="Portugal"):
    if key_prefix not in st.session_state:
        st.session_state[key_prefix] = []
    chart_list = st.session_state[key_prefix]
    st.subheader(label)
    for i in range(len(chart_list)):
        chart = chart_list[i]
        with st.expander(f"{label} {i+1}", expanded=True):
            chart["desc"] = st.text_input("Area/focus for analysis", value=chart.get("desc", desc_default), key=f"{key_prefix}_desc_{i}")
            chart["extra"] = st.text_area("Extra instructions to AI (optional)", value=chart.get("extra",""), key=f"{key_prefix}_extra_{i}")
            chart_file = st.file_uploader(f"Upload chart ({label})", type=["pdf", "png", "jpg", "jpeg", "gif"], key=f"{key_prefix}_file_{i}")
            if chart_file:
                if chart_file.type == "application/pdf":
                    pdf_bytes = chart_file.read()
                    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    page = pdf_doc.load_page(0)
                    pix = page.get_pixmap()
                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
                else:
                    img = Image.open(chart_file).convert("RGB").copy()
                _, img_bytes = downscale_image(img)
                chart["img_bytes"] = img_bytes
                st.image(img, caption=f"{label} (included in PDF)")
                # Generate or preview analysis
                if "ai_text" not in chart or st.button(f"Regenerate AI analysis for {label} {i+1}", key=f"{key_prefix}_regen_{i}"):
                    if img_bytes:
                        img_b64 = base64.b64encode(img_bytes.getvalue()).decode("utf-8")
                        ai_text = ai_chart_analysis(img_b64, label, chart["desc"], chart.get("extra",""))
                        chart["ai_text"] = ai_text
                chart["ai_text"] = st.text_area("Edit/Approve AI Analysis", value=chart.get("ai_text",""), key=f"{key_prefix}_aitxt_{i}", height=150)
            else:
                chart["img_bytes"] = None
                chart["ai_text"] = ""
    addcol, rmcol = st.columns([0.24,0.24])
    if addcol.button(f"Add {label}"):
        chart_list.append({"desc": desc_default, "extra": "", "img_bytes": None, "ai_text": ""})
    if len(chart_list) > 1 and rmcol.button(f"Remove last {label}"):
        chart_list.pop()

def windtemp_block():
    chart_upload_and_analysis_block("windtemp_charts", "Wind and Temperature Chart", desc_default="Portugal")

def spc_block():
    chart_upload_and_analysis_block("spc_charts", "Surface Pressure Chart (SPC)", desc_default="Portugal")

def sigwx_block():
    chart_upload_and_analysis_block("sigwx_charts", "Significant Weather Chart (SIGWX)", desc_default="Portugal")

def sigmet_block():
    st.subheader("6. En-route Weather Warnings (SIGMET/AIRMET/GAMET)")
    return st.text_area("SIGMET/AIRMET/GAMET:", height=110, key="sigmet_area")

def notam_block():
    if "notam_data" not in st.session_state:
        st.session_state.notam_data = [{"aero": "", "notams": [{"num":"", "text":""}]}]
    st.subheader("7. NOTAMs by Aerodrome")
    for idx, entry in enumerate(st.session_state.notam_data):
        with st.expander(f"NOTAMs for Aerodrome {idx+1}", expanded=True):
            entry["aero"] = st.text_input("Aerodrome ICAO or Name", value=entry["aero"], key=f"notam_aero_{idx}")
            num_notams = len(entry["notams"])
            for nidx in range(num_notams):
                cols = st.columns([0.22, 0.78])
                entry["notams"][nidx]["num"] = cols[0].text_input("NOTAM Number", value=entry["notams"][nidx]["num"], key=f"notam_num_{idx}_{nidx}")
                entry["notams"][nidx]["text"] = cols[1].text_area(f"NOTAM {nidx+1} Text", value=entry["notams"][nidx]["text"], key=f"notam_text_{idx}_{nidx}")
            col_add, col_rm = st.columns([0.22,0.22])
            if col_add.button("Add NOTAM", key=f"addnotam_{idx}"):
                entry["notams"].append({"num":"","text":""})
            if num_notams > 1 and col_rm.button("Remove NOTAM", key=f"rmnotam_{idx}"):
                entry["notams"].pop()
    btncols = st.columns([0.23,0.23])
    if btncols[0].button("Add Aerodrome NOTAM"):
        st.session_state.notam_data.append({"aero":"", "notams":[{"num":"","text":""}]})
    if len(st.session_state.notam_data)>1 and btncols[1].button("Remove Last Aerodrome NOTAM"):
        st.session_state.notam_data.pop()

# ---- UI ----
metar_taf_block()
sigwx_block()
windtemp_block()
spc_block()
sigmet_gamet_text = sigmet_block()
notam_block()

# ---- READY & PDF ----
ready = (
    len([c for c in st.session_state.get("sigwx_charts", []) if c.get("img_bytes")]) > 0
    and len([c for c in st.session_state.get("windtemp_charts", []) if c.get("img_bytes")]) > 0
    and len([c for c in st.session_state.get("spc_charts", []) if c.get("img_bytes")]) > 0
)

if ready:
    if st.button("Generate PDF Report"):
        with st.spinner("Preparing your preflight briefing..."):
            pdf = BriefingPDF()
            pdf.set_auto_page_break(auto=True, margin=14)
            pdf.cover_page(pilot, aircraft, str(date), time_utc, callsign, mission)
            metar_taf_pairs = [
                entry for entry in st.session_state.metar_taf_pairs
                if entry['metar'].strip() or entry['taf'].strip() or entry['icao'].strip()
            ]
            if metar_taf_pairs:
                pdf.metar_taf_section(metar_taf_pairs)
            sigmet_ai_summary = ai_sigmet_summary(sigmet_gamet_text) if sigmet_gamet_text.strip() else ""
            pdf.enroute_section(sigmet_gamet_text, sigmet_ai_summary)
            for chart in st.session_state.get("sigwx_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title=f"Significant Weather Chart (SIGWX)",
                        img_bytes=chart["img_bytes"],
                        ai_text=chart.get("ai_text",""),
                        user_desc=chart.get("desc","")
                    )
            for chart in st.session_state.get("windtemp_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title="Wind and Temperature Chart",
                        img_bytes=chart["img_bytes"],
                        ai_text=chart.get("ai_text",""),
                        user_desc=chart.get("desc","")
                    )
            for chart in st.session_state.get("spc_charts", []):
                if chart.get("img_bytes"):
                    pdf.chart_section(
                        title="Surface Pressure Chart (SPC)",
                        img_bytes=chart["img_bytes"],
                        ai_text=chart.get("ai_text",""),
                        user_desc=chart.get("desc","")
                    )
            pdf.notam_section(st.session_state.notam_data)
            out_pdf = f"weather_and_notam_{ascii_safe(mission)}.pdf"
            pdf.output(out_pdf)
            with open(out_pdf, "rb") as f:
                pdf_bytes = f.read()
                st.download_button(
                    label="Download Preflight Weather Briefing PDF",
                    data=pdf_bytes,
                    file_name=out_pdf,
                    mime="application/pdf"
                )
            try:
                email_body = (
                    f"Pilot: {pilot}\n"
                    f"Aircraft: {aircraft}\n"
                    f"Callsign: {callsign}\n"
                    f"Mission: {mission}\n"
                    f"Date: {date}\n"
                    f"Expected Time (UTC): {time_utc}\n"
                    f"PDF attached."
                )
                send_report_email(
                    ADMIN_EMAIL,
                    subject=f"Weather/NOTAM Report submitted: Mission {mission}",
                    body=email_body,
                    filename=out_pdf,
                    filedata=pdf_bytes
                )
                st.success("PDF generated and sent to admin!")
            except Exception as e:
                st.warning(f"PDF generated, but failed to email admin: {e}")
else:
    st.info("Fill all sections and upload at least one chart of each type before generating your PDF.")



