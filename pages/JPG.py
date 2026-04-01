# app.py — "JPG"
# Requisitos: streamlit, pymupdf (fitz), pillow, reportlab
# Execução: streamlit run app.py

import io
import streamlit as st
import fitz  # PyMuPDF
from PIL import Image, ImageFilter

# -----------------------------
# Configuração básica
# -----------------------------
st.set_page_config(page_title="JPG", layout="wide", initial_sidebar_state="collapsed")
st.title("PDF → Imagem lado a lado")

st.caption(
    "**Modo normal:** PDFs de 1–2 páginas → uma imagem lado a lado. "
    "PDFs com 3+ páginas → PDF com cada par de páginas lado a lado. "
    "**Modo dual:** dois PDFs → imagem com a 1.ª página de cada um lado a lado."
)

# -----------------------------
# Funções utilitárias
# -----------------------------
def _pixmap_to_pil(pix: fitz.Pixmap, bg=(255, 255, 255)) -> Image.Image:
    """Converte Pixmap do PyMuPDF em PIL Image, compondo alpha se necessário."""
    if pix.alpha:
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
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False, annots=True, colorspace=fitz.csRGB)
    return _pixmap_to_pil(pix, bg=bg)


def merge_side_by_side(
    img_left: Image.Image,
    img_right: Image.Image,
    align_by: str = "height",
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


def apply_sharpen(img: Image.Image) -> Image.Image:
    return img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=120, threshold=3))


def pil_to_bytes(img: Image.Image, fmt: str) -> bytes:
    bio = io.BytesIO()
    if fmt == "PNG":
        img.save(bio, format="PNG", optimize=True)
    else:
        img.save(bio, format="JPEG", quality=97, subsampling=0, optimize=True)
    bio.seek(0)
    return bio.read()


def images_to_pdf(images: list[Image.Image]) -> bytes:
    """Converte uma lista de imagens PIL num PDF (uma imagem por página)."""
    if not images:
        raise ValueError("Nenhuma imagem para converter.")
    bio = io.BytesIO()
    rgb_images = [img.convert("RGB") for img in images]
    rgb_images[0].save(
        bio,
        format="PDF",
        save_all=True,
        append_images=rgb_images[1:],
    )
    bio.seek(0)
    return bio.read()


# -----------------------------
# Conversores principais
# -----------------------------

def convert_single_or_pair(
    pdf_bytes: bytes,
    dpi: int,
    fmt: str,
    align_by: str,
    gap_px: int,
    bg: tuple,
    sharpen: bool,
):
    """
    1–2 páginas → bytes de imagem (PNG/JPG) + metadados.
    3+ páginas  → bytes de PDF com pares lado a lado + metadados.
    """
    pdf_bytes = _preprocess_pdf(pdf_bytes)

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        n = doc.page_count
        if n < 1:
            raise ValueError("PDF inválido (sem páginas).")

        if n <= 2:
            # --- comportamento original ---
            i1 = render_page(doc.load_page(0), dpi, bg)
            i2 = render_page(doc.load_page(1), dpi, bg) if n == 2 else Image.new("RGB", i1.size, bg)
            merged = merge_side_by_side(i1, i2, align_by=align_by, gap_px=gap_px, bg=bg)
            if sharpen:
                merged = apply_sharpen(merged)
            out = pil_to_bytes(merged, fmt)
            ext = "png" if fmt == "PNG" else "jpg"
            mime = "image/png" if fmt == "PNG" else "image/jpeg"
            return out, mime, ext, merged.size, "image"

        else:
            # --- multi-página: agrupa dois a dois e gera PDF ---
            merged_pages = []
            for i in range(0, n, 2):
                i1 = render_page(doc.load_page(i), dpi, bg)
                if i + 1 < n:
                    i2 = render_page(doc.load_page(i + 1), dpi, bg)
                else:
                    # número ímpar de páginas: última fica com branco à direita
                    i2 = Image.new("RGB", i1.size, bg)
                merged = merge_side_by_side(i1, i2, align_by=align_by, gap_px=gap_px, bg=bg)
                if sharpen:
                    merged = apply_sharpen(merged)
                merged_pages.append(merged)

            out = images_to_pdf(merged_pages)
            size = merged_pages[0].size
            return out, "application/pdf", "pdf", size, "pdf"


def convert_dual(
    pdf_bytes_a: bytes,
    pdf_bytes_b: bytes,
    dpi: int,
    fmt: str,
    align_by: str,
    gap_px: int,
    bg: tuple,
    sharpen: bool,
):
    """Dois PDFs → imagem com a 1.ª página de cada um lado a lado."""
    pdf_bytes_a = _preprocess_pdf(pdf_bytes_a)
    pdf_bytes_b = _preprocess_pdf(pdf_bytes_b)

    with fitz.open(stream=pdf_bytes_a, filetype="pdf") as doc_a:
        if doc_a.page_count < 1:
            raise ValueError("PDF A inválido (sem páginas).")
        i1 = render_page(doc_a.load_page(0), dpi, bg)

    with fitz.open(stream=pdf_bytes_b, filetype="pdf") as doc_b:
        if doc_b.page_count < 1:
            raise ValueError("PDF B inválido (sem páginas).")
        i2 = render_page(doc_b.load_page(0), dpi, bg)

    merged = merge_side_by_side(i1, i2, align_by=align_by, gap_px=gap_px, bg=bg)
    if sharpen:
        merged = apply_sharpen(merged)

    out = pil_to_bytes(merged, fmt)
    ext = "png" if fmt == "PNG" else "jpg"
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return out, mime, ext, merged.size


# -----------------------------
# Sidebar
# -----------------------------
st.sidebar.header("Opções")
dpi = st.sidebar.slider("DPI (ficheiro)", 150, 900, 600, 50)
fmt = st.sidebar.radio("Formato de imagem", ["PNG", "JPG"], index=0)
align_by = st.sidebar.radio("Alinhar por", ["height", "width"], index=0)
gap_px = st.sidebar.number_input("Espaço entre páginas (px)", min_value=0, max_value=100, value=0, step=1)
bg_label = st.sidebar.selectbox("Fundo", ["Branco", "Cinza claro", "Preto"], index=0)
BG = {"Branco": (255, 255, 255), "Cinza claro": (246, 248, 251), "Preto": (0, 0, 0)}[bg_label]
sharpen = st.sidebar.checkbox("Aumentar nitidez", value=True)

st.sidebar.markdown("---")
preview_width = st.sidebar.slider("Largura máx. da preview (px)", 600, 2000, 1000, 100)
preview_1to1 = st.sidebar.checkbox("Mostrar 1:1 (sem redimensionar)", value=False)

# -----------------------------
# Tabs de modo
# -----------------------------
tab_normal, tab_dual = st.tabs(["📄 Modo normal", "🔀 Modo dual (2 PDFs)"])

# ── Modo normal ──────────────────────────────────────────────────────────────
with tab_normal:
    st.markdown(
        "Carregue um ou mais PDFs. "
        "**1–2 páginas** → imagem PNG/JPG. "
        "**3+ páginas** → PDF com pares lado a lado."
    )
    files = st.file_uploader(
        "PDFs", type=["pdf"], accept_multiple_files=True, key="normal"
    )

    if not files:
        st.info("Escolha um ou mais PDFs acima.")
    else:
        for f in files:
            try:
                pdf_bytes = f.read()
                out_bytes, mime, ext, size, kind = convert_single_or_pair(
                    pdf_bytes,
                    dpi=dpi,
                    fmt=fmt,
                    align_by=align_by,
                    gap_px=gap_px,
                    bg=BG,
                    sharpen=sharpen,
                )

                base_name = f.name.rsplit(".", 1)[0]
                name = f"{base_name}_merged.{ext}"
                w, h = size

                if kind == "pdf":
                    # Conta pares para indicar ao utilizador
                    with fitz.open(stream=pdf_bytes, filetype="pdf") as _d:
                        n_orig = _d.page_count
                    n_pairs = (n_orig + 1) // 2
                    st.write(
                        f"**{name}** — {n_orig} páginas agrupadas em {n_pairs} pares "
                        f"• {dpi} dpi • PDF"
                    )
                    st.info(
                        f"O ficheiro tem {n_orig} páginas, por isso o resultado é um **PDF** "
                        f"com {n_pairs} página(s), cada uma com um par lado a lado."
                    )
                    # Preview: mostra apenas o primeiro par
                    first_pair = Image.open(io.BytesIO(out_bytes))  # PIL lê 1.ª frame
                    buf = io.BytesIO()
                    if not preview_1to1:
                        first_pair.thumbnail((preview_width, 10_000_000), Image.LANCZOS)
                    first_pair.save(buf, format="PNG")
                    st.image(buf.getvalue(), caption="Preview — 1.º par de páginas")
                else:
                    st.write(f"**{name}** — {w}×{h}px • {dpi} dpi • {fmt}")
                    if preview_1to1:
                        st.image(out_bytes)
                    else:
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

# ── Modo dual ─────────────────────────────────────────────────────────────────
with tab_dual:
    st.markdown(
        "Carregue **dois PDFs**. "
        "O resultado será uma imagem com a **1.ª página de cada PDF** lado a lado."
    )
    col_a, col_b = st.columns(2)
    with col_a:
        file_a = st.file_uploader("PDF esquerdo (A)", type=["pdf"], key="dual_a")
    with col_b:
        file_b = st.file_uploader("PDF direito (B)", type=["pdf"], key="dual_b")

    if file_a and file_b:
        try:
            pdf_a = file_a.read()
            pdf_b = file_b.read()

            out_bytes, mime, ext, size = convert_dual(
                pdf_a, pdf_b,
                dpi=dpi,
                fmt=fmt,
                align_by=align_by,
                gap_px=gap_px,
                bg=BG,
                sharpen=sharpen,
            )

            name_a = file_a.name.rsplit(".", 1)[0]
            name_b = file_b.name.rsplit(".", 1)[0]
            out_name = f"{name_a}_{name_b}_dual.{ext}"
            w, h = size

            st.write(f"**{out_name}** — {w}×{h}px • {dpi} dpi • {fmt}")

            if preview_1to1:
                st.image(out_bytes)
            else:
                img = Image.open(io.BytesIO(out_bytes)).copy()
                img.thumbnail((preview_width, 10_000_000), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                st.image(buf.getvalue(), width=preview_width)

            st.download_button(
                "⬇️ Download", data=out_bytes, file_name=out_name, mime=mime
            )

        except Exception as e:
            st.error(f"Erro: {e}")
    elif file_a or file_b:
        st.info("Falta carregar o segundo PDF.")
    else:
        st.info("Carregue os dois PDFs acima.")
