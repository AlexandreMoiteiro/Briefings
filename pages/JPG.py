# app.py — "JPG"
# Requisitos: streamlit, pymupdf (fitz), pillow
# Execução: streamlit run app.py

import io
import streamlit as st
import fitz  # PyMuPDF
from PIL import Image, ImageFilter

# -----------------------------
# Configuração básica
# -----------------------------
st.set_page_config(page_title="JPG", layout="wide", initial_sidebar_state="collapsed")
st.title("PDF (1-2 páginas) → Imagem lado a lado")

st.caption(
    "Converta PDFs de 1 ou 2 páginas para uma única imagem lado a lado. "
    "Se o PDF tiver só 1 página, a segunda metade fica em branco. "
    "A imagem mostrada é apenas uma *preview*; o download mantém resolução total."
)

# -----------------------------
# Funções utilitárias
# -----------------------------
def _pixmap_to_pil(pix: fitz.Pixmap, bg=(255, 255, 255)) -> Image.Image:
    """Converte Pixmap do PyMuPDF em PIL Image, compondo alpha se necessário."""
    if pix.alpha:  # compor sobre fundo opaco para evitar serrilhado em texto
        img = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
        bg_img = Image.new("RGB", img.size, bg)
        bg_img.paste(img, mask=img.split()[3])
        return bg_img
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

def _preprocess_pdf(pdf_bytes: bytes) -> bytes:
    """Garante appearance streams dos campos de formulário antes de rasterizar."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as d:
            changed = False
            for page in d:
                try:
                    widgets = page.widgets()
                    if widgets:
                        for w in widgets:
                            w.update()
                            changed = True
                except Exception:
                    pass
            if changed:
                return d.tobytes(deflate=True, garbage=3)
    except Exception:
        pass
    return pdf_bytes

def render_page(page: fitz.Page, dpi: int, bg=(255, 255, 255)) -> Image.Image:
    """Rasteriza a página em RGB com anotações/AcroForm ativas."""
    zoom = dpi / 72.0  # 72 dpi = 1.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False, annots=True, colorspace=fitz.csRGB)
    return _pixmap_to_pil(pix, bg=bg)

def merge_side_by_side(
    img_left: Image.Image,
    img_right: Image.Image,
    align_by: str = "height",  # "height" ou "width"
    gap_px: int = 0,
    bg=(255, 255, 255),
) -> Image.Image:
    """Faz merge horizontal alinhando por altura ou largura."""
    if align_by == "width":
        target = max(img_left.width, img_right.width)

        if img_left.width != target:
            h = int(round(img_left.height * (target / img_left.width)))
            img_left = img_left.resize((target, h), Image.LANCZOS)

        if img_right.width != target:
            h = int(round(img_right.height * (target / img_right.width)))
            img_right = img_right.resize((target, h), Image.LANCZOS)

        H = max(img_left.height, img_right.height)
        W = target * 2 + gap_px
        canvas = Image.new("RGB", (W, H), bg)
        canvas.paste(img_left, (0, (H - img_left.height) // 2))
        canvas.paste(img_right, (target + gap_px, (H - img_right.height) // 2))
        return canvas

    # alinhar por altura (padrão)
    target = max(img_left.height, img_right.height)

    if img_left.height != target:
        w = int(round(img_left.width * (target / img_left.height)))
        img_left = img_left.resize((w, target), Image.LANCZOS)

    if img_right.height != target:
        w = int(round(img_right.width * (target / img_right.height)))
        img_right = img_right.resize((w, target), Image.LANCZOS)

    W = img_left.width + img_right.width + gap_px
    H = target
    canvas = Image.new("RGB", (W, H), bg)
    canvas.paste(img_left, (0, 0))
    canvas.paste(img_right, (img_left.width + gap_px, 0))
    return canvas

def convert_pdf_to_image(
    pdf_bytes: bytes,
    dpi: int,
    fmt: str,
    align_by: str,
    gap_px: int,
    bg: tuple,
    sharpen: bool,
):
    """
    Converte primeiro par de páginas do PDF para imagem final e retorna bytes/mime/size.
    - Se o PDF tiver 2+ páginas: usa página 1 e 2.
    - Se o PDF tiver só 1 página: segunda metade fica um bloco vazio (mesmo tamanho da 1ª).
    """
    pdf_bytes = _preprocess_pdf(pdf_bytes)

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.page_count < 1:
            raise ValueError("PDF inválido (sem páginas).")

        # Render primeira página
        p1 = doc.load_page(0)
        i1 = render_page(p1, dpi, bg)

        # Render segunda página se existir, senão cria 'folha em branco'
        if doc.page_count >= 2:
            p2 = doc.load_page(1)
            i2 = render_page(p2, dpi, bg)
        else:
            # página branca do mesmo tamanho da primeira
            i2 = Image.new("RGB", i1.size, bg)

        # Merge lado a lado
        merged = merge_side_by_side(i1, i2, align_by=align_by, gap_px=gap_px, bg=bg)

        # Nitidez opcional
        if sharpen:
            merged = merged.filter(
                ImageFilter.UnsharpMask(radius=0.8, percent=120, threshold=3)
            )

        # Serializar para bytes finais
        bio = io.BytesIO()
        if fmt == "PNG":
            merged.save(bio, format="PNG", optimize=True)
            mime, ext = "image/png", "png"
        else:
            merged.save(bio, format="JPEG", quality=97, subsampling=0, optimize=True)
            mime, ext = "image/jpeg", "jpg"

        bio.seek(0)
        return bio.read(), mime, ext, merged.size

# -----------------------------
# Sidebar (simples, sem clutter)
# -----------------------------
st.sidebar.header("Opções")
dpi = st.sidebar.slider("DPI (ficheiro)", 150, 900, 600, 50)
fmt = st.sidebar.radio("Formato", ["PNG", "JPG"], index=0)
align_by = st.sidebar.radio("Alinhar por", ["height", "width"], index=0)
gap_px = st.sidebar.number_input("Espaço entre páginas (px)", min_value=0, max_value=100, value=0, step=1)
bg_label = st.sidebar.selectbox("Fundo", ["Branco", "Cinza claro", "Preto"], index=0)
BG = {"Branco": (255, 255, 255), "Cinza claro": (246, 248, 251), "Preto": (0, 0, 0)}[bg_label]
sharpen = st.sidebar.checkbox("Aumentar nitidez", value=True)

st.sidebar.markdown("---")
preview_width = st.sidebar.slider("Largura máx. da preview (px)", 600, 2000, 1000, 100)
preview_1to1 = st.sidebar.checkbox("Mostrar 1:1 (sem redimensionar)", value=False)

# -----------------------------
# Uploader e processamento
# -----------------------------
files = st.file_uploader("PDFs (1 ou 2 páginas)", type=["pdf"], accept_multiple_files=True)

if not files:
    st.info("Escolha um ou mais PDFs acima.")
else:
    for f in files:
        try:
            pdf_bytes = f.read()
            out_bytes, mime, ext, size = convert_pdf_to_image(
                pdf_bytes,
                dpi=dpi,
                fmt=fmt,
                align_by=align_by,
                gap_px=gap_px,
                bg=BG,
                sharpen=sharpen,
            )

            name = f.name.rsplit(".", 1)[0] + f"_merged." + ext
            w, h = size

            st.write(f"**{name}** — {w}×{h}px • {dpi} dpi • {fmt}")

            if preview_1to1:
                # mostra a imagem final tal como está (sem redimensionar)
                st.image(out_bytes)
            else:
                # gera preview redimensionada (sem afetar o ficheiro final)
                img = Image.open(io.BytesIO(out_bytes)).copy()
                img.thumbnail((preview_width, 10_000_000), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                st.image(buf.getvalue(), width=preview_width)

            st.download_button(
                "⬇️ Download", data=out_bytes, file_name=name, mime=mime
            )
            st.divider()

        except Exception as e:
            st.error(f"{f.name}: {e}")


