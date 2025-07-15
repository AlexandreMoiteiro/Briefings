import streamlit as st
from PIL import Image
import openai
import io

openai.api_key = st.secrets["OPENAI_API_KEY"]

def downscale_image(img, width=1200):
    # Receives a PIL Image, returns (resized_img, img_bytes as PNG)
    if img.width > width:
        ratio = width / img.width
        new_size = (width, int(img.height * ratio))
        img = img.resize(new_size)
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG", optimize=True)
    img_bytes.seek(0)
    return img, img_bytes

st.title("SPC AI Analysis – Portugal Focused")
spc_file = st.file_uploader(
    "Upload SPC Chart (PDF, PNG, JPG, JPEG, GIF):",
    type=["pdf", "png", "jpg", "jpeg", "gif"]
)

if spc_file:
    # Handle PDF (convert first page to image)
    if spc_file.type == "application/pdf":
        import fitz
        pdf_doc = fitz.open(stream=spc_file.read(), filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    else:
        img = Image.open(spc_file)

    # Downscale image for token/cost efficiency
    img, img_bytes = downscale_image(img)

    st.image(img, caption="SPC Chart for Analysis", use_column_width=True)

    if st.button("Analyze SPC (Portugal & Vicinity Only)"):
        with st.spinner("GPT-4o is analyzing your chart..."):
            result = openai.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content":
                        "You are an aviation meteorologist instructor. Analyze the uploaded surface pressure chart image. "
                        "Restrict your interpretation ONLY to Portugal and nearby airspace. "
                        "Ignore the rest of the chart. Brief the synoptic situation, expected wind, clouds, precipitation, and any important hazards for VFR/IFR flights in Portugal and vicinity. Do not discuss areas outside Portugal and neighboring airspace."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "image": img_bytes.getvalue()}
                        ]
                    }
                ],
                max_tokens=500,
                temperature=0.5
            )
            gpt_response = result.choices[0].message.content
        st.markdown("### AI Weather Briefing for Portugal")
        st.info(gpt_response)
else:
    st.info("Upload a Surface Pressure Chart to start.")

st.caption("This analysis is limited to Portugal and vicinity for maximum efficiency.")



