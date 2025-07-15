import streamlit as st
from PIL import Image
import io

from streamlit_cropper import st_cropper

st.title("SPC Cropper Demo")

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
        "<span style='color:red; font-weight:bold;'>Only the selected (cropped) area will be analyzed by the AI! Drag and resize the box to include just Portugal and neighboring airspace.</span>",
        unsafe_allow_html=True
    )
    cropped_img = st_cropper(
        img,
        aspect_ratio=None,
        box_color='red',
        return_type='image',
        realtime_update=True
    )
    st.image(cropped_img, caption="Cropped Area", use_container_width=True)





