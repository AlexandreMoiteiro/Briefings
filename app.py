import streamlit as st
import fitz  # PyMuPDF
from PIL import Image
import io
import openai

openai.api_key = st.secrets["OPENAI_API_KEY"]

# Helper to extract text from a PDF (mission objectives)
def extract_text_from_pdf(pdf_file):
    text = ""
    doc = fitz.open(stream=pdf_file.read(), filetype="pdf")
    for page in doc:
        text += page.get_text()
    return text

# Helper for displaying image from upload (PDF page as image)
def get_pdf_first_page_image(pdf_file):
    doc = fitz.open(stream=pdf_file.read(), filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap()
    img_bytes = pix.tobytes("png")
    return Image.open(io.BytesIO(img_bytes))

# ---- Mission Objectives Step ----
mission_file = st.file_uploader("Upload Mission Objectives (PDF, PNG, JPG, GIF, or text file)", 
                                type=["pdf", "png", "jpg", "jpeg", "gif", "txt"])
mission_text = ""
if mission_file:
    if mission_file.type == "application/pdf":
        mission_text = extract_text_from_pdf(mission_file)
        st.success("PDF extracted.")
    elif mission_file.type in ["image/png", "image/jpeg", "image/gif"]:
        img = Image.open(mission_file)
        st.image(img, caption="Uploaded Mission Image")
        mission_text = st.text_area("Describe the mission objective in your own words (or paste text from image)")
    elif mission_file.type == "text/plain":
        mission_text = mission_file.read().decode("utf-8")
    else:
        st.warning("Unsupported file type.")

if mission_text:
    st.markdown("#### AI Summary of Mission Objectives")
    if st.button("Summarize with AI"):
        # -- GPT integration here --
        import openai
        summary = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Summarize the following mission objectives for a pre-flight briefing, focusing on essential points:"},
                {"role": "user", "content": mission_text}
            ]
        )["choices"][0]["message"]["content"]
        st.info(summary)

# ---- Weather Chart Step ----
weather_files = st.file_uploader(
    "Upload Weather Charts (Surface Pressure, Significant Weather, etc.; PDF, PNG, JPG, GIF)", 
    type=["pdf", "png", "jpg", "jpeg", "gif"], 
    accept_multiple_files=True
)
user_obs = st.text_area("Describe what you see in the weather charts (fronts, systems, etc.)")

if st.button("AI Weather Interpretation"):
    prompt = (
        "You are an aviation meteorologist. Given the following description and chart types, "
        "provide a detailed, operational weather briefing for a pilot. "
        f"Description: {user_obs}"
    )
    # -- GPT integration here --
    weather_summary = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Provide a weather interpretation for a pre-flight briefing."},
            {"role": "user", "content": prompt}
        ]
    )["choices"][0]["message"]["content"]
    st.info(weather_summary)


