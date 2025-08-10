import streamlit as st
from fpdf import FPDF
import datetime

# Link fixo para a página Weather
WEATHER_PAGE_URL = "https://SEU-APP-NO-STREAMLIT.app/Weather"

st.set_page_config(page_title="Flight Briefing Tool", layout="wide")
st.title("Flight Briefing Tool")

icao_input = st.text_input("Introduza ICAO(s) separados por vírgula", "LPPT, LPBJ, LEBZ")

col1, col2 = st.columns(2)

# Função para gerar Raw PDF
def generate_raw_pdf(icaos):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(200, 10, txt="Flight Briefing (RAW)", ln=True, align="C")
    pdf.ln(10)

    pdf.set_font("Arial", size=12)
    pdf.multi_cell(0, 10, f"Current METAR, TAF & SIGMET: {WEATHER_PAGE_URL}?icao={','.join(icaos)}")

    pdf.ln(10)
    pdf.cell(0, 10, f"Charts and NOTAMs here...", ln=True)
    return pdf.output(dest="S").encode("latin-1")

# Função para gerar Detailed PDF
def generate_detailed_pdf(icaos):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(200, 10, txt="Flight Briefing (DETAILED - PT)", ln=True, align="C")
    pdf.ln(10)

    pdf.set_font("Arial", size=12)
    pdf.multi_cell(0, 10, "Este documento contém interpretações detalhadas em português para preparar o briefing...")
    # Aqui entraria a lógica para interpretação automática

    pdf.ln(10)
    pdf.cell(0, 10, "Charts e informações adicionais...", ln=True)
    return pdf.output(dest="S").encode("latin-1")

with col1:
    if st.button("Gerar RAW PDF"):
        pdf_bytes = generate_raw_pdf([icao.strip().upper() for icao in icao_input.split(",")])
        st.download_button("Download RAW PDF", data=pdf_bytes, file_name="briefing_raw.pdf", mime="application/pdf")

with col2:
    if st.button("Gerar Detailed PDF"):
        pdf_bytes = generate_detailed_pdf([icao.strip().upper() for icao in icao_input.split(",")])
        st.download_button("Download Detailed PDF", data=pdf_bytes, file_name="briefing_detailed.pdf", mime="application/pdf")


