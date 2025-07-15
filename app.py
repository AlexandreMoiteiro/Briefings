import streamlit as st
from PIL import Image
import io

# Only import fitz if a PDF is detected, to avoid issues if it's not installed
st.title("Cropper Debug Test (Image/PDF)")

spc_file = st.file_uploader(
    "Upload SPC Chart (PDF, PNG, JPG, JPEG, GIF):",
    type=["pdf", "png", "jpg", "jpeg", "gif"]
)

if spc_file:
    try:
        img = None
        if spc_file.type == "application/pdf":
            import fitz
            pdf_bytes = spc_file.read()
            pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = pdf_doc.load_page(0)
            pix = page.get_pixmap()
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB").copy()
        else:
            img = Image.open(spc_file).convert("RGB").copy()
        st.write(f"Type: {type(img)} | Mode: {img.mode} | Size: {img.size}")
    except Exception as e:
        st.error(f"Failed to open image: {e}")
        st.stop()

    # Show image as a basic check
    st.image(img, caption="Loaded Image", use_container_width=True)

    # Import cropper and crop
    try:
        from streamlit_cropper import st_cropper
        cropped_img = st_cropper(
            img,
            aspect_ratio=None,
            box_color='red',
            return_type='image',
            realtime_update=True,
            instructions="Crop to Portugal and vicinity."
        )
        st.image(cropped_img, caption="Cropped Area", use_container_width=True)
    except Exception as e:
        st.error(f"Cropper error: {e}")
else:
    st.info("Upload an image or PDF to test cropping.")




