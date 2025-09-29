import io
from datetime import datetime

import fitz  # PyMuPDF
from PIL import Image, ImageFilter
import streamlit as st

# -----------------------------
# Setup – minimal, clean
# -----------------------------
st.set_page_config(
    page_title="PDF 2p → Merge JPG/PNG",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<div style='display:flex;align-items:center;gap:.6rem'>
  <span style='font-size:1.3rem;font-weight:800'>PDF 2 páginas → imagem lado a lado</span>
  <span style='padding:.15rem .6rem;border-radius:999px;background:#eef2f7;font-size:.85rem'>v1.5</span>
</div>
""", unsafe_allow_html=True)

st.caption("Converta PDFs de 2 páginas. Pré‑visualização compacta; download mantém resolução total.")

# -----------------------------
# Core helpers (foco em nitidez)
# -----------------------------

def _pixmap_to_pil(pix: fitz.Pixmap, bg=(255, 255, 255)) -> Image.Image:
    if pix.alpha:  # compor sobre fundo
        img = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
        bg_img = Image.new("RGB", img.size, bg)
        bg_img.paste(img, mask=img.split()[3])
        return bg_img
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def render_page(page: fitz.Page, dpi: int, bg=(255, 255, 255)) -> Image.Image:
    # 72 dpi -> zoom 1.0
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    # renderizando com aparências de formulários / anotações ativadas
    pix = page.get_pixmap(matrix=mat, alpha=False, annots=True, colorspace=fitz.csRGB)
    return _pixmap_to_pil(pix, bg=bg)


def merge_h(img_left: Image.Image, img_right: Image.Image, match="height", gap_px: int = 0, bg=(255, 255, 255)) -> Image.Image:
    # match pode ser "height" (padrão) ou "width"
    if match == "width":
        w = max(img_left.width, img_right.width)
        if img_left.width != w:
            h = int(img_left.height * (w / img_left.width))
            img_left = img_left.resize((w, h), Image.LANCZOS)
        if img_right.width != w:
            h = int(img_right.height * (w / img_right.width))
            img_right = img_right.resize((w, h), Image.LANCZOS)
        total_h = max(img_left.height, img_right.height)
        total_w = w * 2 + gap_px
        canvas = Image.new("RGB", (total_w, total_h), bg)
        y1 = (total_h - img_left.height)//2
        y2 = (total_h - img_right.height)//2
        canvas.paste(img_left, (0, y1))
        canvas.paste(img_right, (w + gap_px, y2))
        return canvas
    # match por altura (mais seguro para formulários)
    h = max(img_left.height, img_right.height)
    if img_left.height != h:
        new_w = int(img_left.width * (h / img_left.height))
        img_left = img_left.resize((new_w, h), Image.LANCZOS)
    if img_right.height != h:
        new_w = int(img_right.width * (h / img_right.height))
        img_right = img_right.resize((new_w, h), Image.LANCZOS)
    total_w = img_left.width + img_right.width + gap_px
    canvas = Image.new("RGB", (total_w, h), bg)
    canvas.paste(img_left, (0, 0))
    canvas.paste(img_right, (img_left.width + gap_px, 0))
    return canvas


def preprocess_pdf(bytes_in: bytes) -> bytes:
    """Garante que campos de formulário tenham 'appearance streams' antes de rasterizar.
    Atualiza widgets e flattens, devolvendo bytes do PDF já preparado."""
    with fitz.open(stream=bytes_in, filetype="pdf") as d:
        changed = False
        for page in d:
            # Atualiza aparências de widgets/form fields (AcroForm)
            try:
                ws = page.widgets()
                if ws:
                    for w in ws:
                        w.update()
                        changed = True
            except Exception:
                pass
        if changed:
            out = d.tobytes(deflate=True, garbage=3)
            return out
        return bytes_in


def convert_pdf(file_bytes: bytes, dpi: int, fmt: str, gap_px: int, match: str, bg=(255,255,255), sharpen: bool=False):
    file_bytes = preprocess_pdf(file_bytes)
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        if doc.page_count < 2:
            raise ValueError("Este PDF tem menos de 2 páginas.")
        p1, p2 = doc.load_page(0), doc.load_page(1)
        img1 = render_page(p1, dpi, bg)
        img2 = render_page(p2, dpi, bg)
        merged = merge_h(img1, img2, match=match, gap_px=gap_px, bg=bg)
        if sharpen:
            merged = merged.filter(ImageFilter.UnsharpMask(radius=0.8, percent=120, threshold=3))
        bio = io.BytesIO()
        if fmt == "PNG":
            merged.save(bio, format="PNG", optimize=True)
            mime, ext = "image/png", "png"
        else:
            merged.save(bio, format="JPEG", quality=97, subsampling=0, optimize=True)
            mime, ext = "image/jpeg", "jpg"
        bio.seek(0)
        return bio.read(), mime, ext, merged.size
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        if doc.page_count < 2:
            raise ValueError("Este PDF tem menos de 2 páginas.")
        p1, p2 = doc.load_page(0), doc.load_page(1)
        img1 = render_page(p1, dpi, bg)
        img2 = render_page(p2, dpi, bg)
        merged = merge_h(img1, img2, match=match, gap_px=gap_px, bg=bg)
        if sharpen:
            merged = merged.filter(ImageFilter.UnsharpMask(radius=1.2, percent=140, threshold=2))
        bio = io.BytesIO()
        if fmt == "PNG":
            merged.save(bio, format="PNG", optimize=True)
            mime, ext = "image/png", "png"
        else:
            merged.save(bio, format="JPEG", quality=95, subsampling=0, optimize=True)
            mime, ext = "image/jpeg", "jpg"
        bio.seek(0)
        return bio.read(), mime, ext, merged.size

# -----------------------------
# Sidebar – poucas opções
# -----------------------------
with st.sidebar:
    st.subheader("Opções")
    dpi = st.slider("DPI (resolução do ficheiro)", 150, 900, 450, 50)
    fmt = st.radio("Formato do ficheiro", ["PNG", "JPG"], index=0)
    gap_px = st.number_input("Espaço entre páginas (px)", 0, 100, 0, 1)
    match = st.radio("Alinhar por", ["height", "width"], index=0)
    bg_choice = st.selectbox("Fundo", ["Branco", "Cinza claro", "Preto"], index=0)
    BG = {"Branco": (255,255,255), "Cinza claro": (246,248,251), "Preto": (0,0,0)}[bg_choice]
    sharpen = st.checkbox("Aumentar nitidez (Unsharp Mask)", True)

    st.markdown("---")
    st.subheader("Pré‑visualização")
    preview_max_px = st.slider("Largura máxima da preview (px)", 600, 2200, 1100, 100,
                               help="Não afeta o ficheiro gerado; só a imagem mostrada no site.")
    show_1to1 = st.toggle("Mostrar 1:1 (sem redimensionar)", value=False)  # ignora preview_max_px quando ligado
# -----------------------------
with st.sidebar:
    dpi = st.slider("DPI", 150, 900, 450, 50)
    fmt = st.radio("Formato", ["PNG", "JPG"], index=0, help="PNG mantém texto mais nítido (sem perdas)")
    gap_px = st.number_input("Espaço entre páginas (px)", 0, 100, 0, 1)
    match = st.radio("Alinhar por", ["height", "width"], index=0)
    bg_choice = st.selectbox("Fundo", ["Branco", "Cinza claro", "Preto"], index=0)
    BG = {"Branco": (255,255,255), "Cinza claro": (246,248,251), "Preto": (0,0,0)}[bg_choice]
    sharpen = st.checkbox("Aumentar nitidez (Unsharp Mask)", True)

# -----------------------------
# Uploader – simples e direto
# -----------------------------
files = st.file_uploader("PDFs (2 páginas)", type=["pdf"], accept_multiple_files=True)

CARD_CSS = """
<style>
.card{border:1px solid #e5e7eb;border-radius:16px;padding:14px;margin:14px auto;max-width:1200px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.title{display:flex;justify-content:space-between;gap:.6rem;align-items:center}
.meta{font-size:.9rem;color:#6b7280}
.preview img{border-radius:12px;border:1px solid #eef2f7}
</style>
"""
st.markdown(CARD_CSS, unsafe_allow_html=True)

if files:
    for f in files:
        try:
            out_bytes, mime, ext, size = convert_pdf(f.read(), dpi=dpi, fmt=fmt, gap_px=gap_px, match=match, bg=BG, sharpen=sharpen)
            name = f.name.rsplit(".", 1)[0] + f"_merged.{ext}"
            w, h = size
            st.markdown(f"<div class='card'><div class='title'><b>{name}</b><span class='meta'>{w}×{h}px • {dpi} dpi • {fmt}</span></div>", unsafe_allow_html=True)
            # preview reduzida (sem perder qualidade no download)
            if show_1to1:
                st.image(out_bytes, use_container_width=True)
            else:
                from PIL import Image
                bio = io.BytesIO(out_bytes)
                im = Image.open(bio)
                im = im.copy()
                im.thumbnail((preview_max_px, int(preview_max_px*1e6)), Image.LANCZOS)
                prev_io = io.BytesIO(); im.save(prev_io, format="PNG")
                st.image(prev_io.getvalue(), use_container_width=False)
            st.download_button("⬇️ Download", data=out_bytes, file_name=name, mime=mime)
            st.markdown("</div>", unsafe_allow_html=True)
        except Exception as e:
            st.error(f"{f.name}: {e}")
else:
    st.info("Escolha um ou mais PDFs acima.")

st.caption("Dica: a preview é compacta; para máxima definição use PNG + DPI 600. ")"Dica: para texto perfeito escolha PNG e DPI 450–600. Se as páginas tiverem tamanhos diferentes, experimente mudar o alinhamento para 'width'.")
