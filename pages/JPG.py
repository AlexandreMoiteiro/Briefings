import io
import zipfile
from datetime import datetime

import fitz  # PyMuPDF
from PIL import Image
import streamlit as st

# -------------------------
# Helpers
# -------------------------

def pixmap_to_pil(pix: fitz.Pixmap, bg=(255, 255, 255)) -> Image.Image:
    """Convert a PyMuPDF Pixmap to a PIL Image, compositing alpha over bg if needed."""
    if pix.alpha:  # has transparency
        img = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
        background = Image.new("RGB", img.size, bg)
        background.paste(img, mask=img.split()[3])
        return background
    else:
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def render_page_as_image(page: fitz.Page, dpi: int) -> Image.Image:
    # DPI to zoom matrix: 72 dpi is 1.0 zoom
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=True)
    return pixmap_to_pil(pix)


def merge_side_by_side(img_left: Image.Image, img_right: Image.Image, gap_px: int = 0, bg=(255, 255, 255)) -> Image.Image:
    # Resize to same height (max of both) keeping aspect ratio
    h = max(img_left.height, img_right.height)
    if img_left.height != h:
        new_w = int(img_left.width * (h / img_left.height))
        img_left = img_left.resize((new_w, h), Image.LANCZOS)
    if img_right.height != h:
        new_w = int(img_right.width * (h / img_right.height))
        img_right = img_right.resize((new_w, h), Image.LANCZOS)

    total_w = img_left.width + img_right.width + gap_px
    merged = Image.new("RGB", (total_w, h), bg)
    merged.paste(img_left, (0, 0))
    merged.paste(img_right, (img_left.width + gap_px, 0))
    return merged


def pdf_to_merged_jpg(file_bytes: bytes, dpi: int = 200, jpeg_quality: int = 90, swap_pages: bool = False, gap_px: int = 0) -> tuple[bytes, dict]:
    """Return (jpg_bytes, meta) for a PDF expected to have 2 pages."""
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        n = doc.page_count
        if n < 2:
            raise ValueError(f"PDF com {n} página(s). Precisa de pelo menos 2.")
        # Use only first two pages by default
        p1 = doc.load_page(0)
        p2 = doc.load_page(1)
        img1 = render_page_as_image(p1, dpi)
        img2 = render_page_as_image(p2, dpi)
        if swap_pages:
            img1, img2 = img2, img1
        merged = merge_side_by_side(img1, img2, gap_px=gap_px)
        out = io.BytesIO()
        merged.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
        out.seek(0)
        meta = {
            "pages": n,
            "width": merged.width,
            "height": merged.height,
            "dpi": dpi,
            "quality": jpeg_quality,
        }
        return out.read(), meta


# -------------------------
# Streamlit UI
# -------------------------
st.set_page_config(page_title="PDF 2 páginas → JPG lado a lado", page_icon="🖼️", layout="centered")

st.title("🖼️ PDF (duas páginas) → JPG horizontal")
st.markdown(
    "Converte PDFs de 2 páginas em um único JPG com as páginas lado a lado. "
    "Funciona localmente, nada é enviado a servidores terceiros."
)

with st.sidebar:
    st.header("⚙️ Opções")
    dpi = st.slider("Resolução (DPI)", min_value=72, max_value=600, value=200, step=10,
                    help="Quanto maior o DPI, maior a nitidez e o tamanho do arquivo.")
    jpeg_quality = st.slider("Qualidade do JPEG", min_value=50, max_value=100, value=90, step=1,
                             help="90 é um bom equilíbrio entre qualidade e tamanho.")
    gap_px = st.number_input("Espaço (px) entre páginas", min_value=0, max_value=200, value=0, step=1)
    swap_pages = st.checkbox("Trocar ordem das páginas (2 → 1)", value=False)

uploaded_files = st.file_uploader(
    "Carregue um ou mais PDFs (cada um com 2 páginas)",
    type=["pdf"],
    accept_multiple_files=True,
)

if uploaded_files:
    results = []
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in uploaded_files:
            try:
                pdf_bytes = f.read()
                jpg_bytes, meta = pdf_to_merged_jpg(
                    pdf_bytes,
                    dpi=dpi,
                    jpeg_quality=jpeg_quality,
                    swap_pages=swap_pages,
                    gap_px=gap_px,
                )
                base_name = f.name.rsplit(".", 1)[0]
                out_name = f"{base_name}_merged.jpg"
                zf.writestr(out_name, jpg_bytes)
                results.append((f.name, out_name, jpg_bytes, meta))
            except Exception as e:
                st.error(f"❌ {f.name}: {e}")

    # Show previews and individual downloads
    for original_name, out_name, jpg_bytes, meta in results:
        with st.expander(f"✅ {original_name} → {out_name} ({meta['width']}×{meta['height']})"):
            st.image(jpg_bytes, caption=out_name, use_container_width=True)
            st.download_button(
                label="Baixar JPG",
                data=jpg_bytes,
                file_name=out_name,
                mime="image/jpeg",
            )

    # Zip download for batch
    if results:
        zip_buffer.seek(0)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"pdfs_2paginas_merge_{timestamp}.zip"
        st.download_button(
            label="⬇️ Baixar todos (ZIP)",
            data=zip_buffer,
            file_name=zip_name,
            mime="application/zip",
        )
else:
    st.info("Carregue seus PDFs à esquerda para começar.")

# -------------------------
# Rodapé
# -------------------------
st.markdown(
    "---\n"
    "**Dicas**: Se o PDF tiver mais de 2 páginas, apenas as duas primeiras serão usadas.\n"
    "Use DPI maior para qualidade de impressão e menor para visualização rápida."
)

