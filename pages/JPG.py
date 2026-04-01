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
# CSS
# ─────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;600&display=swap');

  html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
  h1 { font-family: 'DM Mono', monospace !important; letter-spacing: -1px; }
  h3 { font-family: 'DM Mono', monospace !important; font-size: 0.95rem !important; color: #374151; }

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

  /* Grelha de thumbnails do modo arranjo */
  .thumb-label {
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    color: #6b7280;
    text-align: center;
    margin-top: 2px;
  }
  .pair-box {
    background: #f8f9fb;
    border: 1px solid #e3e6ea;
    border-radius: 10px;
    padding: 0.8rem;
    margin-bottom: 0.6rem;
  }

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
# Funções core — imagem
# ─────────────────────────────────────────────

def _pixmap_to_pil(pix: fitz.Pixmap, bg=(255, 255, 255)) -> Image.Image:
    if pix.alpha:
        img = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
        bg_img = Image.new("RGB", img.size, bg)
        bg_img.paste(img, mask=img.split()[3])
        return bg_img
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def _preprocess_pdf(pdf_bytes: bytes) -> bytes:
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


def render_page_thumb(page: fitz.Page, max_px: int = 200) -> Image.Image:
    """Thumbnail de baixa resolução para UI — independente do DPI de exportação."""
    zoom = max_px / max(page.rect.width, page.rect.height)
    mat  = fitz.Matrix(zoom, zoom)
    pix  = page.get_pixmap(matrix=mat, alpha=False, annots=True, colorspace=fitz.csRGB)
    return _pixmap_to_pil(pix)


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
            return img if img.width == tw else img.resize(
                (tw, round(img.height * tw / img.width)), Image.LANCZOS)
        left, right = sw(left), sw(right)
        H = max(left.height, right.height)
        canvas = Image.new("RGB", (tw * 2 + gap_px, H), bg)
        canvas.paste(left,  (0,           (H - left.height)  // 2))
        canvas.paste(right, (tw + gap_px, (H - right.height) // 2))
        return canvas

    th = max(left.height, right.height)
    def sh(img):
        return img if img.height == th else img.resize(
            (round(img.width * th / img.height), th), Image.LANCZOS)
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
    """PIL Images → PDF via PyMuPDF (new_page + insert_image)."""
    out_doc = fitz.open()
    for img in images:
        w, h = img.size
        page = out_doc.new_page(width=w, height=h)
        buf  = io.BytesIO()
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


def thumb_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ─────────────────────────────────────────────
# Processadores de alto nível
# ─────────────────────────────────────────────

def process_pairs(pairs_indices: list, doc: fitz.Document, opts: dict):
    """
    Recebe lista de tuplos (left_idx, right_idx|None) e um doc aberto.
    Devolve (out_bytes, mime, ext, list[PIL merged]).
    """
    dpi, fmt        = opts["dpi"], opts["fmt"]
    align_by, gap   = opts["align_by"], opts["gap_px"]
    bg, sharpen     = opts["bg"], opts["sharpen"]
    n_pairs         = len(pairs_indices)
    progress        = st.progress(0, text="A rasterizar páginas…")
    merged_images   = []

    for i, (li, ri) in enumerate(pairs_indices):
        left  = render_page(doc.load_page(li), dpi, bg)
        right = render_page(doc.load_page(ri), dpi, bg) if ri is not None else Image.new("RGB", left.size, bg)
        merged = merge_side_by_side(left, right, align_by=align_by, gap_px=gap, bg=bg)
        if sharpen:
            merged = apply_sharpen(merged)
        merged_images.append(merged)
        progress.progress((i + 1) / n_pairs, text=f"Par {i + 1}/{n_pairs}…")

    progress.empty()

    if len(merged_images) == 1:
        out  = encode_image(merged_images[0], fmt)
        ext  = "png" if fmt == "PNG" else "jpg"
        mime = "image/png" if fmt == "PNG" else "image/jpeg"
    else:
        out  = images_to_pdf_bytes(merged_images)
        ext  = "pdf"
        mime = "application/pdf"

    return out, mime, ext, merged_images


def process_normal(pdf_bytes: bytes, opts: dict):
    pdf_bytes = _preprocess_pdf(pdf_bytes)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        n = doc.page_count
        if n < 1:
            raise ValueError("PDF inválido (sem páginas).")
        pairs = [(i * 2, i * 2 + 1 if i * 2 + 1 < n else None) for i in range(math.ceil(n / 2))]
        out, mime, ext, merged = process_pairs(pairs, doc, opts)
    return out, mime, ext, n, merged


def process_dual(pdf_a: bytes, pdf_b: bytes, opts: dict):
    pdf_a = _preprocess_pdf(pdf_a)
    pdf_b = _preprocess_pdf(pdf_b)
    dpi, fmt        = opts["dpi"], opts["fmt"]
    align_by, gap   = opts["align_by"], opts["gap_px"]
    bg, sharpen     = opts["bg"], opts["sharpen"]

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
    progress.progress(0.9, text="A juntar…")

    merged = merge_side_by_side(left, right, align_by=align_by, gap_px=gap, bg=bg)
    if sharpen:
        merged = apply_sharpen(merged)
    progress.empty()

    out  = encode_image(merged, fmt)
    ext  = "png" if fmt == "PNG" else "jpg"
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return out, mime, ext, merged


# ─────────────────────────────────────────────
# Helpers de UI
# ─────────────────────────────────────────────

def show_result(out_bytes, mime, ext, fname, n_pages, pairs_count, dpi):
    size_kb  = len(out_bytes) / 1024
    size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
    badge    = {"pdf": "badge-pdf", "png": "badge-png", "jpg": "badge-jpg"}[ext]
    pages_info = f"{n_pages} pág. → {pairs_count} par(es) &nbsp;·&nbsp;" if n_pages else ""
    st.markdown(
        f'<div class="result-meta"><strong>{fname}</strong>'
        f'<span class="badge {badge}">{ext.upper()}</span>'
        f' &nbsp;·&nbsp; {pages_info} {dpi} dpi &nbsp;·&nbsp; {size_str}</div>',
        unsafe_allow_html=True,
    )


def show_previews(merged_images, preview_width, preview_1to1):
    if len(merged_images) == 1:
        st.image(make_preview(merged_images[0], preview_width, preview_1to1))
    else:
        cols = st.columns(min(len(merged_images), 3))
        for idx, img in enumerate(merged_images):
            with cols[idx % 3]:
                st.image(make_preview(img, preview_width // 3, False), caption=f"Par {idx + 1}")


# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️  Opções")
    dpi      = st.slider("DPI", 72, 900, 500, 50,
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
    "**Modo normal** — pares automáticos (1–2 págs → imagem; 3+ págs → PDF).  \n"
    "**Modo dual** — 1.ª página de dois PDFs lado a lado.  \n"
    "**Modo arranjo** — escolhe manualmente quais páginas ficam juntas."
)
st.divider()


# ─────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────
tab_normal, tab_dual, tab_arrange = st.tabs([
    "📄  Modo normal",
    "🔀  Modo dual",
    "🎛️  Modo arranjo",
])


# ══════════════════════════════════════════════
# Tab Normal
# ══════════════════════════════════════════════
with tab_normal:
    files = st.file_uploader(
        "Escolher PDFs", type=["pdf"], accept_multiple_files=True, key="normal_up",
        help="1–2 págs → imagem; 3+ págs → PDF com pares automáticos"
    )
    if not files:
        st.info("⬆️  Arraste ou escolha um ou mais PDFs para começar.", icon="📂")
    else:
        for f in files:
            try:
                out_bytes, mime, ext, n_pages, merged = process_normal(f.read(), OPTS)
                base  = f.name.rsplit(".", 1)[0]
                fname = f"{base}_merged.{ext}"
                show_result(out_bytes, mime, ext, fname, n_pages, len(merged), dpi)
                show_previews(merged, preview_width, preview_1to1)
                st.download_button(
                    f"⬇️  Descarregar {fname}", data=out_bytes,
                    file_name=fname, mime=mime, use_container_width=True,
                )
            except Exception as e:
                st.error(f"**{f.name}**: {e}", icon="❌")
            st.markdown("<hr>", unsafe_allow_html=True)


# ══════════════════════════════════════════════
# Tab Dual
# ══════════════════════════════════════════════
with tab_dual:
    st.markdown("Carregue **dois PDFs**. O resultado é uma imagem com a **1.ª página de cada PDF** lado a lado.")
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
            show_result(out_bytes, mime, ext, fname, None, 1, dpi)
            st.image(make_preview(merged_img, preview_width, preview_1to1))
            st.download_button(
                f"⬇️  Descarregar {fname}", data=out_bytes,
                file_name=fname, mime=mime, use_container_width=True,
            )
        except Exception as e:
            st.error(f"{e}", icon="❌")
    elif file_a or file_b:
        st.warning(f"Falta carregar o **{'PDF direito (B)' if file_a else 'PDF esquerdo (A)'}**.", icon="⚠️")
    else:
        st.info("⬆️  Carregue os dois PDFs acima para começar.", icon="📂")


# ══════════════════════════════════════════════
# Tab Arranjo
# ══════════════════════════════════════════════
with tab_arrange:
    st.markdown(
        "Carregue um PDF e **escolha manualmente** quais páginas ficam juntas. "
        "Cada par produz uma imagem lado a lado; o conjunto gera um PDF."
    )

    arr_file = st.file_uploader("Escolher PDF", type=["pdf"], key="arrange_up")

    if not arr_file:
        st.info("⬆️  Carregue um PDF para começar.", icon="📂")
    else:
        # ── Carregar thumbnails (cache por nome+tamanho do ficheiro) ──────────
        arr_bytes = arr_file.read()
        arr_bytes = _preprocess_pdf(arr_bytes)
        cache_key = f"arr_thumbs_{arr_file.name}_{len(arr_bytes)}"

        if cache_key not in st.session_state:
            with fitz.open(stream=arr_bytes, filetype="pdf") as _doc:
                n_arr = _doc.page_count
                thumbs = [render_page_thumb(_doc.load_page(i)) for i in range(n_arr)]
            st.session_state[cache_key]         = thumbs
            st.session_state[cache_key + "_n"]  = n_arr
            st.session_state[cache_key + "_b"]  = arr_bytes
            # Estado dos pares: lista de [left_idx, right_idx|None]
            # Inicializa com pares sequenciais
            default_pairs = [
                [i * 2, i * 2 + 1 if i * 2 + 1 < n_arr else None]
                for i in range(math.ceil(n_arr / 2))
            ]
            st.session_state[cache_key + "_pairs"] = default_pairs

        thumbs   : list        = st.session_state[cache_key]
        n_arr    : int         = st.session_state[cache_key + "_n"]
        arr_bytes: bytes       = st.session_state[cache_key + "_b"]
        pairs_state: list      = st.session_state[cache_key + "_pairs"]

        # ── Galeria de páginas disponíveis ────────────────────────────────────
        st.markdown("### Páginas disponíveis")
        st.caption("Números de página abaixo de cada thumbnail (base 1).")
        THUMB_COLS = 6
        g_cols = st.columns(THUMB_COLS)
        for i, thumb in enumerate(thumbs):
            with g_cols[i % THUMB_COLS]:
                st.image(thumb_to_bytes(thumb), use_container_width=True)
                st.markdown(f'<div class="thumb-label">Pág. {i + 1}</div>', unsafe_allow_html=True)

        st.divider()

        # ── Editor de pares ───────────────────────────────────────────────────
        st.markdown("### Pares de páginas")
        st.caption(
            "Cada linha é um par. Escolha qual página fica à esquerda e à direita. "
            "Use **Branco** no lado direito para deixar metade em branco."
        )

        page_options_left  = [f"Pág. {i + 1}" for i in range(n_arr)]
        page_options_right = ["Branco"] + [f"Pág. {i + 1}" for i in range(n_arr)]

        new_pairs = []
        for pair_i, pair in enumerate(pairs_state):
            li, ri = pair
            with st.container():
                c1, c2, c3, c4, c5 = st.columns([0.12, 2, 0.3, 2, 0.5])
                with c1:
                    st.markdown(f"**{pair_i + 1}.**")
                with c2:
                    left_sel = st.selectbox(
                        "Esquerda", page_options_left,
                        index=li,
                        key=f"pair_{cache_key}_{pair_i}_L",
                        label_visibility="collapsed",
                    )
                with c3:
                    st.markdown("<div style='text-align:center;padding-top:6px'>↔</div>", unsafe_allow_html=True)
                with c4:
                    right_default = 0 if ri is None else ri + 1  # offset pelo "Branco"
                    right_sel = st.selectbox(
                        "Direita", page_options_right,
                        index=right_default,
                        key=f"pair_{cache_key}_{pair_i}_R",
                        label_visibility="collapsed",
                    )
                with c5:
                    remove = st.button("✕", key=f"pair_{cache_key}_{pair_i}_del",
                                       help="Remover este par")

            if not remove:
                li_new = page_options_left.index(left_sel)
                ri_new = None if right_sel == "Branco" else page_options_right.index(right_sel) - 1
                new_pairs.append([li_new, ri_new])

        # ── Botões de gestão de pares ──────────────────────────────────────────
        bc1, bc2, bc3 = st.columns([1, 1, 2])
        with bc1:
            if st.button("＋  Adicionar par", use_container_width=True):
                new_pairs.append([0, None])
        with bc2:
            if st.button("↺  Repor sequencial", use_container_width=True):
                new_pairs = [
                    [i * 2, i * 2 + 1 if i * 2 + 1 < n_arr else None]
                    for i in range(math.ceil(n_arr / 2))
                ]

        # Guarda estado actualizado
        st.session_state[cache_key + "_pairs"] = new_pairs

        # ── Preview dos pares seleccionados (thumbnails) ──────────────────────
        if new_pairs:
            st.divider()
            st.markdown("### Preview dos pares")
            PAIR_COLS = min(len(new_pairs), 3)
            p_cols = st.columns(PAIR_COLS)
            for idx, (li, ri) in enumerate(new_pairs):
                with p_cols[idx % PAIR_COLS]:
                    lt = thumbs[li]
                    rt = thumbs[ri] if ri is not None else Image.new("RGB", lt.size, (240, 242, 245))
                    # Merge thumbnail inline
                    merged_thumb = merge_side_by_side(lt, rt, align_by=align_by, gap_px=2, bg=(240, 242, 245))
                    st.image(thumb_to_bytes(merged_thumb), use_container_width=True,
                             caption=f"Par {idx + 1}: pág. {li + 1} + {'branco' if ri is None else f'pág. {ri + 1}'}")

        # ── Gerar ─────────────────────────────────────────────────────────────
        st.divider()
        if not new_pairs:
            st.warning("Adicione pelo menos um par para gerar.", icon="⚠️")
        else:
            if st.button("🚀  Gerar", type="primary", use_container_width=True):
                try:
                    with fitz.open(stream=arr_bytes, filetype="pdf") as doc:
                        pairs_tuples = [(li, ri) for li, ri in new_pairs]
                        out_bytes, mime, ext, merged = process_pairs(pairs_tuples, doc, OPTS)

                    base  = arr_file.name.rsplit(".", 1)[0]
                    fname = f"{base}_arranjo.{ext}"
                    show_result(out_bytes, mime, ext, fname, n_arr, len(merged), dpi)
                    show_previews(merged, preview_width, preview_1to1)
                    st.download_button(
                        f"⬇️  Descarregar {fname}", data=out_bytes,
                        file_name=fname, mime=mime, use_container_width=True,
                    )
                except Exception as e:
                    st.error(f"{e}", icon="❌")
