# app.py — PDF Side-by-Side
# Requisitos: streamlit, pymupdf (fitz), pillow
# Execução: streamlit run app.py

import io
import math
import streamlit as st
import fitz  # PyMuPDF
from PIL import Image, ImageFilter

# ─────────────────────────────────────────────
# Configuração da página
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="PDF Side-by-Side",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# CSS personalizado
# ─────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;600&display=swap');

  html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
  h1 { font-family: 'DM Mono', monospace !important; letter-spacing: -1px; }

  .result-meta {
    font-family: 'DM Mono', monospace;
    font-size: 0.78rem;
    color: #6b7280;
    margin-bottom: 0.5rem;
  }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-left: 6px;
  }
  .badge-pdf { background: #fef3c7; color: #92400e; }
  .badge-png { background: #dbeafe; color: #1e40af; }
  .badge-jpg { background: #d1fae5; color: #065f46; }

  hr { border: none; border-top: 1px dashed #d1d5db; margin: 1.5rem 0; }
  [data-testid="stSidebar"] { background: #f0f2f5; }
  .stDownloadButton button {
    border-radius: 8px !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 0.82rem !important;
  }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Funções core
# ─────────────────────────────────────────────

def _pixmap_to_pil(pix: fitz.Pixmap, bg=(255, 255, 255)) -> Image.Image:
    if pix.alpha:
        img = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
        bg_img = Image.new("RGB", img.size, bg)
        bg_img.paste(img, mask=img.split()[3])
        return bg_img
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def _preprocess_pdf(pdf_bytes: bytes) -> bytes:
    """Actualiza appearance streams de AcroForm antes de rasterizar."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as d:
            changed = False
            for page in d:
                for w in (page.widgets() or []):
                    w.update()
                    changed = True
            if changed:
                return d.tobytes(deflate=True, garbage=3)
    except Exception:
        pass
    return pdf_bytes


def render_page(page: fitz.Page, dpi: int, bg=(255, 255, 255)) -> Image.Image:
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, alpha=False, annots=True, colorspace=fitz.csRGB)
    return _pixmap_to_pil(pix, bg=bg)


def merge_side_by_side(
    left: Image.Image,
    right: Image.Image,
    align_by: str = "height",
    gap_px: int = 0,
    bg=(255, 255, 255),
) -> Image.Image:
    if align_by == "width":
        tw = max(left.width, right.width)
        def sw(img):
            return img if img.width == tw else img.resize((tw, round(img.height * tw / img.width)), Image.LANCZOS)
        left, right = sw(left), sw(right)
        H = max(left.height, right.height)
        canvas = Image.new("RGB", (tw * 2 + gap_px, H), bg)
        canvas.paste(left,  (0,           (H - left.height)  // 2))
        canvas.paste(right, (tw + gap_px, (H - right.height) // 2))
        return canvas

    th = max(left.height, right.height)
    def sh(img):
        return img if img.height == th else img.resize((round(img.width * th / img.height), th), Image.LANCZOS)
    left, right = sh(left), sh(right)
    canvas = Image.new("RGB", (left.width + right.width + gap_px, th), bg)
    canvas.paste(left,  (0, 0))
    canvas.paste(right, (left.width + gap_px, 0))
    return canvas


def apply_sharpen(img: Image.Image) -> Image.Image:
    return img.filter(ImageFilter.UnsharpMask(radius=0.8, percent=120, threshold=3))


def encode_image(img: Image.Image, fmt: str) -> bytes:
    bio = io.BytesIO()
    if fmt == "PNG":
        img.save(bio, format="PNG", optimize=True)
    else:
        img.save(bio, format="JPEG", quality=97, subsampling=0, optimize=True)
    return bio.getvalue()


def images_to_pdf_bytes(images: list) -> bytes:
    """
    Converte lista de PIL Images → PDF via PyMuPDF.
    Cria uma página por imagem com as dimensões exactas e insere a imagem nela.
    """
    out_doc = fitz.open()
    for img in images:
        w, h = img.size
        # Cria página com dimensões em pontos (1 px = 1 pt a 72 dpi; usamos px directamente)
        page = out_doc.new_page(width=w, height=h)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        buf.seek(0)
        page.insert_image(page.rect, stream=buf.read())
    data = out_doc.tobytes(deflate=True, garbage=3)
    out_doc.close()
    return data


def make_preview(img: Image.Image, max_width: int, one_to_one: bool) -> bytes:
    if not one_to_one:
        img = img.copy()
        img.thumbnail((max_width, 99_999), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ─────────────────────────────────────────────
# Processadores de alto nível
# ─────────────────────────────────────────────

def process_normal(pdf_bytes: bytes, opts: dict):
    """
    Lê o PDF e devolve (out_bytes, mime, ext, n_pages, list[PIL]) onde:
    - 1–2 págs → PNG/JPG
    - 3+ págs  → PDF com pares
    A barra de progresso é actualizada internamente.
    """
    pdf_bytes = _preprocess_pdf(pdf_bytes)
    dpi, fmt   = opts["dpi"], opts["fmt"]
    align_by, gap_px = opts["align_by"], opts["gap_px"]
    bg, sharpen = opts["bg"], opts["sharpen"]

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        n = doc.page_count
        if n < 1:
            raise ValueError("PDF inválido (sem páginas).")

        n_pairs  = math.ceil(n / 2)
        progress = st.progress(0, text="A rasterizar páginas…")
        pairs    = []

        for i in range(n_pairs):
            li, ri = i * 2, i * 2 + 1
            left  = render_page(doc.load_page(li), dpi, bg)
            right = render_page(doc.load_page(ri), dpi, bg) if ri < n else Image.new("RGB", left.size, bg)
            merged = merge_side_by_side(left, right, align_by=align_by, gap_px=gap_px, bg=bg)
            if sharpen:
                merged = apply_sharpen(merged)
            pairs.append(merged)
            progress.progress((i + 1) / n_pairs, text=f"Par {i + 1}/{n_pairs}…")

        progress.empty()

    if n <= 2:
        out  = encode_image(pairs[0], fmt)
        ext  = "png" if fmt == "PNG" else "jpg"
        mime = "image/png" if fmt == "PNG" else "image/jpeg"
    else:
        out  = images_to_pdf_bytes(pairs)
        ext  = "pdf"
        mime = "application/pdf"

    return out, mime, ext, n, pairs


def process_dual(pdf_a: bytes, pdf_b: bytes, opts: dict):
    pdf_a = _preprocess_pdf(pdf_a)
    pdf_b = _preprocess_pdf(pdf_b)
    dpi, fmt   = opts["dpi"], opts["fmt"]
    align_by, gap_px = opts["align_by"], opts["gap_px"]
    bg, sharpen = opts["bg"], opts["sharpen"]

    progress = st.progress(0, text="A processar PDF A…")
    with fitz.open(stream=pdf_a, filetype="pdf") as da:
        if da.page_count < 1:
            raise ValueError("PDF A inválido.")
        left = render_page(da.load_page(0), dpi, bg)
    progress.progress(0.5, text="A processar PDF B…")
    with fitz.open(stream=pdf_b, filetype="pdf") as db:
        if db.page_count < 1:
            raise ValueError("PDF B inválido.")
        right = render_page(db.load_page(0), dpi, bg)
    progress.progress(0.9, text="A juntar imagens…")

    merged = merge_side_by_side(left, right, align_by=align_by, gap_px=gap_px, bg=bg)
    if sharpen:
        merged = apply_sharpen(merged)
    progress.empty()

    out  = encode_image(merged, fmt)
    ext  = "png" if fmt == "PNG" else "jpg"
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return out, mime, ext, merged


# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️  Opções")
    dpi      = st.slider("DPI", 72, 900, 300, 50,
                         help="Resolução de rasterização. Valores altos = mais qualidade e mais tempo.")
    fmt      = st.radio("Formato de saída", ["PNG", "JPG"], horizontal=True)
    align_by = st.radio("Alinhar por", ["height", "width"], horizontal=True)
    gap_px   = st.slider("Espaço entre páginas (px)", 0, 200, 0, 4)
    bg_label = st.selectbox("Cor de fundo", ["Branco", "Cinza claro", "Preto"])
    BG       = {"Branco": (255, 255, 255), "Cinza claro": (240, 242, 245), "Preto": (0, 0, 0)}[bg_label]
    sharpen  = st.toggle("Aumentar nitidez", value=True)

    st.divider()
    st.markdown("**Preview**")
    preview_width = st.slider("Largura máx. (px)", 400, 2000, 900, 100)
    preview_1to1  = st.toggle("Mostrar 1:1", value=False)

OPTS = dict(dpi=dpi, fmt=fmt, align_by=align_by, gap_px=gap_px, bg=BG, sharpen=sharpen)


# ─────────────────────────────────────────────
# Cabeçalho
# ─────────────────────────────────────────────
st.title("PDF Side-by-Side")
st.caption(
    "**Modo normal** — 1–2 páginas → imagem; 3+ páginas → PDF com pares lado a lado.  \n"
    "**Modo dual** — combina a 1.ª página de dois PDFs distintos numa única imagem."
)
st.divider()


# ─────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────
tab_normal, tab_dual = st.tabs(["📄  Modo normal", "🔀  Modo dual"])

# ── Tab Normal ────────────────────────────────
with tab_normal:
    files = st.file_uploader(
        "Escolher PDFs", type=["pdf"], accept_multiple_files=True, key="normal_up",
        help="1–2 págs → imagem; 3+ págs → PDF"
    )

    if not files:
        st.info("⬆️  Arraste ou escolha um ou mais PDFs para começar.", icon="📂")
    else:
        for f in files:
            try:
                out_bytes, mime, ext, n_pages, pairs = process_normal(f.read(), OPTS)

                base  = f.name.rsplit(".", 1)[0]
                fname = f"{base}_merged.{ext}"
                n_pairs  = len(pairs)
                size_kb  = len(out_bytes) / 1024
                size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
                badge_cls = {"pdf": "badge-pdf", "png": "badge-png", "jpg": "badge-jpg"}[ext]

                st.markdown(
                    f'<div class="result-meta"><strong>{fname}</strong>'
                    f'<span class="badge {badge_cls}">{ext.upper()}</span>'
                    f' &nbsp;·&nbsp; {n_pages} pág. → {n_pairs} par(es)'
                    f' &nbsp;·&nbsp; {dpi} dpi &nbsp;·&nbsp; {size_str}</div>',
                    unsafe_allow_html=True,
                )

                if n_pairs == 1:
                    st.image(make_preview(pairs[0], preview_width, preview_1to1))
                else:
                    cols = st.columns(min(n_pairs, 3))
                    for idx, p in enumerate(pairs):
                        with cols[idx % 3]:
                            st.image(make_preview(p, preview_width // 3, False), caption=f"Par {idx + 1}")

                st.download_button(
                    f"⬇️  Descarregar {fname}", data=out_bytes,
                    file_name=fname, mime=mime, use_container_width=True,
                )

            except Exception as e:
                st.error(f"**{f.name}**: {e}", icon="❌")

            st.markdown("<hr>", unsafe_allow_html=True)

# ── Tab Dual ──────────────────────────────────
with tab_dual:
    st.markdown(
        "Carregue **dois PDFs**. O resultado é uma imagem com a **1.ª página de cada PDF** lado a lado."
    )
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**◀  PDF esquerdo (A)**")
        file_a = st.file_uploader("PDF A", type=["pdf"], key="dual_a", label_visibility="collapsed")
    with col_b:
        st.markdown("**▶  PDF direito (B)**")
        file_b = st.file_uploader("PDF B", type=["pdf"], key="dual_b", label_visibility="collapsed")

    if file_a and file_b:
        try:
            out_bytes, mime, ext, merged_img = process_dual(file_a.read(), file_b.read(), OPTS)
            name_a = file_a.name.rsplit(".", 1)[0]
            name_b = file_b.name.rsplit(".", 1)[0]
            fname  = f"{name_a}+{name_b}.{ext}"
            w, h   = merged_img.size
            size_kb = len(out_bytes) / 1024
            size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
            badge_cls = "badge-png" if ext == "png" else "badge-jpg"

            st.markdown(
                f'<div class="result-meta"><strong>{fname}</strong>'
                f'<span class="badge {badge_cls}">{ext.upper()}</span>'
                f' &nbsp;·&nbsp; {w}×{h} px'
                f' &nbsp;·&nbsp; {dpi} dpi &nbsp;·&nbsp; {size_str}</div>',
                unsafe_allow_html=True,
            )
            st.image(make_preview(merged_img, preview_width, preview_1to1))
            st.download_button(
                f"⬇️  Descarregar {fname}", data=out_bytes,
                file_name=fname, mime=mime, use_container_width=True,
            )
        except Exception as e:
            st.error(f"{e}", icon="❌")

    elif file_a or file_b:
        missing = "PDF direito (B)" if file_a else "PDF esquerdo (A)"
        st.warning(f"Falta carregar o **{missing}**.", icon="⚠️")
    else:
        st.info("⬆️  Carregue os dois PDFs acima para começar.", icon="📂")
