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

st.title("SPC AI Analysis – Crop and Analyze Any Area")
st.markdown(
    """
    1. Upload your Surface Pressure Chart (SPC) as PDF, PNG, JPG, or GIF.  
    2. Use the cropping tool to select **any region** you want analyzed.  
    3. Click the analyze button to receive a detailed AI weather interpretation of your selected area.
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

    st.markdown("Use the box below to crop any region of interest.")
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

    user_area = st.text_input(
        "Briefly describe the region or features you want the AI to focus on (optional):",
        placeholder="e.g., Focus on central Portugal, or analyze the cold front in the selected area"
    )

    if st.button("Analyze Cropped Area with AI"):
        with st.spinner("GPT-4o is analyzing your selected area..."):
            system_prompt = (
                "You are an aviation meteorology instructor. Analyze the uploaded surface pressure chart image. "
                "Focus your interpretation ONLY on the cropped area of the chart. Summarize the synoptic situation, expected wind, clouds, precipitation, "
                "and any important hazards for VFR/IFR flights. If the user provided a description of the area or specific features to focus on, tailor your answer accordingly."
            )
            if user_area.strip():
                user_prompt = f"Please focus your analysis on: {user_area.strip()}"
            else:
                user_prompt = "Please provide a detailed weather interpretation for the cropped area."

            try:
                result = openai.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{img_base64}"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=600,
                    temperature=0.5
                )
                gpt_response = result.choices[0].message.content
                st.markdown("### AI Weather Briefing (Cropped Area)")
                st.info(gpt_response)
            except openai.RateLimitError:
                st.error("OpenAI RateLimitError: You have hit your OpenAI API rate or usage limit. Please wait and try again, or check your usage/quota in your OpenAI dashboard.")
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")

else:
    st.info("Upload a Surface Pressure Chart to begin.")

st.caption("The AI analyzes only the region you've selected in the cropper. The interpretation will focus on the cropped area or your description.")



