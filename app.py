import streamlit as st
from PIL import Image
import openai
import io
import base64
from streamlit_cropper import st_cropper
from fpdf import FPDF
import fitz
import datetime

openai.api_key = st.secrets["OPENAI_API_KEY"]

def downscale_image(img, width=1300):
    if img.width > width:
        ratio = width / img.width
        img = img.resize((width, int(img.height * ratio)))
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG", optimize=True)
    img_bytes.seek(0)
    return img, img_bytes

def ai_briefing(image_base64, prompt, user_focus=""):
    if user_focus.strip():
        focus_text = f"Foca a análise em: {user_focus.strip()}"
    else:
        focus_text = ""
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": focus_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                ]
            }
        ],
        max_tokens=800,
        temperature=0.35
    )
    return response.choices[0].message.content

class CleanPDF(FPDF):
    def cover(self, mission, date, pilot, aircraft, callsign):
        self.add_page()
        self.set_fill_color(24,24,24)
        self.rect(0, 0, 210, 40, 'F')
        self.set_font("Arial", 'B', 22)
        self.set_text_color(255,255,255)
        self.set_xy(12, 15)
        self.cell(0, 12, "Preflight Briefing", ln=1, align='L')
        self.set_font("Arial", '', 13)
        self.cell(0, 8, f"Missão: {mission}", ln=1)
        self.cell(0, 8, f"Data: {date}", ln=1)
        self.cell(0, 8, f"Piloto: {pilot}", ln=1)
        self.cell(0, 8, f"Aeronave: {aircraft}", ln=1)
        self.cell(0, 8, f"Callsign: {callsign}", ln=1)
        self.ln(5)
    def add_chart(self, title, img_bytes, ai_text):
        self.add_page()
        self.set_font("Arial", 'B', 14)
        self.set_text_color(0,0,0)
        self.cell(0, 10, title, ln=1)
        self.ln(2)
        img_path = f"tmp_{hash(img_bytes)}.png"
        with open(img_path, "wb") as f:
            f.write(img_bytes.getvalue())
        self.image(img_path, x=12, w=185)
        self.ln(7)
        self.set_font("Arial", '', 12)
        self.multi_cell(0, 8, ai_text)
        self.ln(3)

st.title("Preflight Briefing PDF (Clean)")
col1, col2 = st.columns(2)
with col1:
    mission = st.text_input("Mission number", "")
    date = st.date_input("Date", datetime.date.today())
with col2:
    pilot = st.text_input("Pilot", "")
    aircraft = st.text_input("Aircraft", "")
    callsign = st.text_input("Callsign", "")

st.markdown("### Surface Pressure Chart (SPC)")
spc_file = st.file_uploader("Upload SPC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="spc")
spc_img, spc_img_bytes, crop_img, crop_img_bytes = None, None, None, None
if spc_file:
    if spc_file.type == "application/pdf":
        pdf_bytes = spc_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        spc_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        spc_img = Image.open(spc_file).convert("RGB").copy()
    spc_img, spc_img_bytes = downscale_image(spc_img, width=1300)
    st.image(spc_img, caption="SPC Chart")
    st.markdown("Selecione a zona de análise (pode ser Portugal, Ibéria, etc).")
    crop_img = st_cropper(
        spc_img,
        aspect_ratio=None,
        box_color='red',
        return_type='image',
        realtime_update=True,
        key="spc_crop"
    )
    crop_img, crop_img_bytes = downscale_image(crop_img, width=600)
spc_focus = st.text_input("Foco do briefing SPC (opcional, ex: 'sobre Portugal')", "")

st.markdown("---")
st.markdown("### Significant Weather Chart (SIGWX)")
sigwx_file = st.file_uploader("Upload SIGWX/SWC (PDF, PNG, JPG, JPEG, GIF):", type=["pdf", "png", "jpg", "jpeg", "gif"], key="sigwx")
sigwx_img, sigwx_img_bytes = None, None
if sigwx_file:
    if sigwx_file.type == "application/pdf":
        pdf_bytes = sigwx_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        sigwx_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        sigwx_img = Image.open(sigwx_file).convert("RGB").copy()
    sigwx_img, sigwx_img_bytes = downscale_image(sigwx_img, width=1300)
    st.image(sigwx_img, caption="SIGWX Chart")
sigwx_focus = st.text_input("Foco do briefing SIGWX (opcional)", "")

can_generate = all([
    mission, pilot, aircraft, callsign, date,
    spc_img_bytes, crop_img_bytes, sigwx_img_bytes
])

if st.button("Gerar PDF Briefing", disabled=not can_generate):
    with st.spinner("A gerar PDF e a pedir análise ao AI..."):
        spc_prompt = (
            "Imagina que és instrutor de meteorologia aeronáutica. Vais dar um briefing a um aluno sobre a área de Portugal/Ibéria com base no gráfico de pressão à superfície anexo. "
            "A tua análise deve ser breve, clara e realista, apenas para essa zona: descreve a situação sinóptica relevante, passagem ou aproximação de frentes, tipo de tempo e vento previstos a baixa altitude, e tendências próximas. "
            "Não uses listas nem títulos, escreve em texto corrido, como num briefing oral profissional. Não inventes nada, só interpreta o que vês no gráfico."
        )
        sigwx_prompt = (
            "Imagina que és instrutor de meteorologia aeronáutica e vais dar um briefing sobre tempo significativo na área de Portugal/Ibéria usando o chart anexo. "
            "Analisa só essa área: refere a presença/ausência de nuvens significativas, trovoadas, zonas de turbulência ou gelo, níveis de congelamento e outros fenómenos perigosos. "
            "Não uses listas nem títulos, escreve em parágrafos, como se estivesses a falar para um piloto. Se não houver perigos diz isso claramente. Só interpreta o que vês no chart, não inventes informação."
        )
        spc_crop_base64 = base64.b64encode(crop_img_bytes.getvalue()).decode("utf-8")
        spc_report = ai_briefing(spc_crop_base64, spc_prompt, spc_focus)
        sigwx_base64 = base64.b64encode(sigwx_img_bytes.getvalue()).decode("utf-8")
        sigwx_report = ai_briefing(sigwx_base64, sigwx_prompt, sigwx_focus)

        pdf = CleanPDF()
        pdf.cover(mission, str(date), pilot, aircraft, callsign)
        pdf.add_chart("Surface Pressure Chart (SPC)", spc_img_bytes, spc_report)
        pdf.add_chart("Significant Weather Chart (SIGWX)", sigwx_img_bytes, sigwx_report)
        out_pdf = "Preflight_Briefing.pdf"
        pdf.output(out_pdf)
        with open(out_pdf, "rb") as f:
            st.download_button(
                label="Descarregar PDF do Briefing",
                data=f,
                file_name=out_pdf,
                mime="application/pdf"
            )
        st.success("PDF gerado com sucesso!")

st.caption("O briefing é escrito em linguagem natural e profissional. O SIGWX é analisado na totalidade. SPC só a área selecionada.")





