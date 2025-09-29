import io
from datetime import datetime

import fitz  # PyMuPDF
from PIL import Image
import streamlit as st

# ------------------------------------
# App setup & styles (no ZIP; clean UI)
# ------------------------------------
st.set_page_config(
    page_title="PDF 2 páginas → JPG lado a lado",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1200px !important; }
      .mb-header{font-size:1.25rem;font-weight:800;text-transform:uppercase;border-bottom:1px solid #e5e7eb;padding-bottom:8px;margin:4px 0 14px}
      .hint{font-size:.9rem;color:#6b7280}
      .card{border:1px solid #e5e7eb;border-radius:16px;padding:12px;margin-bottom:12px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.04)}
      .chip{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef2f7;margin-left:8px;font-size:.85rem}
      .footer{color:#6b7280;font-size:.85rem;margin-top:12px}
      /* Nicer font rendering */
      html, body, [class^=st-]{font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="mb-header">Streamlit app – PDF (duas páginas) → JPG (merge horizontal) – v1.2</div>', unsafe_allow_html=True)

# ------------------------------------
# Core helpers
# ------------------------------------

def pixmap_to_pil(pix: fitz.Pixmap, bg=(255, 255, 255)) -> Image.Image:
    """Convert a PyMuPDF Pixmap to a PIL Image, compositing alpha over bg if needed."""
    if pix.alpha:  # has transparency
        img = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
        background = Image.new("RGB", img.size, bg)
        background.paste(img, mask=img.split()[3])
        return background
    else:
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def render_page_as_image(page: fitz.Page, dpi: int, bg=(255, 255, 255)) -> Image.Image:
    # DPI to zoom matrix: 72 dpi is 1.0 zoom
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=True)
    return pixmap_to_pil(pix, bg=bg)


def merge_side_by_side(img_left: Image.Image, img_right: Image.Image, gap_px: int = 0, bg=(255, 255, 255), border_px: int = 0) -> Image.Image:
    """Merge two PIL images horizontally, aligning by height, optional gap and border."""
    # align heights
    h = max(img_left.height, img_right.height)
    if img_left.height != h:
        new_w = int(img_left.width * (h / img_left.height))
        img_left = img_left.resize((new_w, h), Image.LANCZOS)
    if img_right.height != h:
        new_w = int(img_right.width * (h / img_right.height))
        img_right = img_right.resize((new_w, h), Image.LANCZOS)

    total_w = img_left.width + img_right.width + gap_px + 2 * border_px
    total_h = h + 2 * border_px
    merged = Image.new("RGB", (total_w, total_h), bg)

    x = border_px
    y = border_px
    merged.paste(img_left, (x, y))
    x += img_left.width + gap_px
    merged.paste(img_right, (x, y))

    return merged


def pdf_to_merged_jpg(file_bytes: bytes, dpi: int = 220, jpeg_quality: int = 90, swap_pages: bool = False, gap_px: int = 0, bg=(255,255,255), border_px: int = 0) -> tuple[bytes, dict]:
    """Return (jpg_bytes, meta) for a PDF expected to have 2 pages (uses first two)."""
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        n = doc.page_count
        if n < 2:
            raise ValueError(f"PDF com {n} página(s). Precisa de pelo menos 2.")
        p1 = doc.load_page(0)
        p2 = doc.load_page(1)
        img1 = render_page_as_image(p1, dpi, bg=bg)
        img2 = render_page_as_image(p2, dpi, bg=bg)
        if swap_pages:
            img1, img2 = img2, img1
        merged = merge_side_by_side(img1, img2, gap_px=gap_px, bg=bg, border_px=border_px)
        out = io.BytesIO()
        merged.save(out, format="JPEG", quality=jpeg_quality, optimize=True, subsampling=0)  # subsampling=0 = fonte mais nítida
        out.seek(0)
        meta = {
            "pages": n,
            "width": merged.width,
            "height": merged.height,
            "dpi": dpi,
            "quality": jpeg_quality,
        }
        return out.read(), meta

# ------------------------------------
# Sidebar – Opções
# ------------------------------------
with st.sidebar:
    st.subheader("⚙️ Opções")
    dpi = st.slider("Resolução (DPI)", min_value=72, max_value=600, value=220, step=10,
                    help="Quanto maior o DPI, maior a nitidez e o tamanho do arquivo.")
    jpeg_quality = st.slider("Qualidade do JPEG", min_value=60, max_value=100, value=92, step=1,
                             help="92 costuma manter a tipografia mais nítida.")
    gap_px = st.number_input("Espaço (px) entre páginas", min_value=0, max_value=200, value=0, step=1)
    border_px = st.number_input("Margem/borda (px) ao redor", min_value=0, max_value=200, value=12, step=1)
    swap_pages = st.checkbox("Trocar ordem das páginas (2 → 1)", value=False)
    bg_choice = st.selectbox("Fundo", ["Branco", "Cinza claro", "Preto"], index=0)
    BG_MAP = {"Branco": (255,255,255), "Cinza claro": (245,247,250), "Preto": (0,0,0)}
    bg = BG_MAP[bg_choice]

# ------------------------------------
# Uploader & processamento (sem ZIP)
# ------------------------------------
uploaded_files = st.file_uploader(
    "Carregue um ou mais PDFs (cada um com 2 páginas)",
    type=["pdf"],
    accept_multiple_files=True,
)

if uploaded_files:
    st.caption("Pré-visualizações e downloads individuais abaixo (sem ZIP).")
    for f in uploaded_files:
        with st.container():
            try:
                pdf_bytes = f.read()
                jpg_bytes, meta = pdf_to_merged_jpg(
                    pdf_bytes,
                    dpi=dpi,
                    jpeg_quality=jpeg_quality,
                    swap_pages=swap_pages,
                    gap_px=gap_px,
                    bg=bg,
                    border_px=border_px,
                )
                base_name = f.name.rsplit(".", 1)[0]
                out_name = f"{base_name}_merged.jpg"
                st.markdown(f"<div class='card'><b>{f.name}</b> <span class='chip'>{meta['width']}×{meta['height']}</span> <span class='chip'>{meta['dpi']} dpi</span></div>", unsafe_allow_html=True)
                st.image(jpg_bytes, caption=out_name, use_container_width=True)
                st.download_button(
                    label="⬇️ Baixar JPG",
                    data=jpg_bytes,
                    file_name=out_name,
                    mime="image/jpeg",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"❌ {f.name}: {e}")
else:
    st.info("Carregue seus PDFs no widget acima para começar.")

st.markdown(
    """
    <div class='footer'>
      Dicas: se o PDF tiver mais de 2 páginas, apenas as duas primeiras serão usadas.

      Use DPI mais alto para impressão. A opção de <i>Qualidade 92</i> e <i>subsampling 0</i> deixa a letra mais bonita no JPG.
    </div>
    """,
    unsafe_allow_html=True,
)
