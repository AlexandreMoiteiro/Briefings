import streamlit as st
from PIL import Image
import openai
import io
import base64
from streamlit_cropper import st_cropper

openai.api_key = st.secrets["OPENAI_API_KEY"]

def downscale_image(img, width=900):
    if img.width > width:
        ratio = width / img.width
        new_size = (width, int(img.height * ratio))
        img = img.resize(new_size)
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG", optimize=True)
    img_bytes.seek(0)
    return img, img_bytes

st.title("SPC AI Analysis – Crop to Portugal & Vicinity")
st.markdown(
    """
    **Step 1:** Upload your Surface Pressure Chart (SPC).  
    **Step 2:** Use the cropping tool below to select only Portugal and neighboring regions.  
    <span style='color:red; font-weight:bold;'>⚠️ Only the selected (cropped) area will be analyzed by the AI!</span>
    """, 
    unsafe_allow_html=True
)

spc_file = st.file_uploader(
    "Upload SPC Chart (PDF, PNG, JPG, JPEG, GIF):",
    type=["pdf", "png", "jpg", "jpeg", "gif"]
)

if spc_file:
    if spc_file.type == "application/pdf":
        import fitz
        pdf_bytes = spc_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = pdf_doc.load_page(0)
        pix = page.get_pixmap()
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
    else:
        img = Image.open(spc_file).convert("RGB").copy()
    st.write(f"Type: {type(img)}, Mode: {img.mode}, Size: {img.size}")

    st.markdown(
        "<span style='color:red; font-weight:bold;'>Drag and resize the box to include just Portugal and neighboring airspace. Only the area inside the box will be analyzed.</span>",
        unsafe_allow_html=True
    )
    cropped_img = st_cropper(
        img,
        aspect_ratio=None,
        box_color='red',
        return_type='image',
        realtime_update=True
    )
    st.image(cropped_img, caption="Cropped Area (to be analyzed by AI)", use_container_width=True)

    cropped_img, img_bytes = downscale_image(cropped_img)
    img_base64 = base64.b64encode(img_bytes.getvalue()).decode("utf-8")

    if st.button("Analyze Cropped Area with AI"):
        st.markdown("**Submitting only the selected (cropped) part to the AI. Please wait...**")
        with st.spinner("GPT-4o is analyzing your selected area..."):
            try:
                result = openai.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content":
                            "You are an aviation meteorologist instructor. Analyze the uploaded surface pressure chart image. "
                            "Restrict your interpretation ONLY to the visible area (Portugal and nearby airspace). Ignore any information outside the cropped area. "
                            "Brief the synoptic situation, expected wind, clouds, precipitation, and any important hazards for VFR/IFR flights in Portugal and vicinity. Do not discuss areas outside the cropped chart."
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": f"data:image/png;base64,{img_base64}"
                                }
                            ]
                        }
                    ],
                    max_tokens=500,
                    temperature=0.5
                )
                gpt_response = result.choices[0].message.content
                st.markdown("### AI Weather Briefing (Portugal & Vicinity – Cropped Area)")
                st.info(gpt_response)
            except openai.RateLimitError:
                st.error("OpenAI RateLimitError: You have hit your OpenAI API rate or usage limit. Please wait and try again, or check your usage/quota in your OpenAI dashboard.")
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")

else:
    st.info("Upload a Surface Pressure Chart to begin.")

st.caption("🟢 Only the selected (cropped) area of the chart will be sent for AI analysis. Make sure Portugal is fully included for best results!")





