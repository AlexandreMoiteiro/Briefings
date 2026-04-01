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


def scale_and_crop_marks(
    img: Image.Image,
    content_w_cm: float = 20.5,
    content_h_cm: float = 13.3,
    dpi: int = 500,
    margin_cm: float = 0.5,
    mark_len_cm: float = 0.4,
    mark_offset_cm: float = 0.1,
    mark_thick_px: int = 2,
    mark_color=(0, 0, 0),
    bg=(255, 255, 255),
) -> Image.Image:
    """
    Escala img para caber em content_w_cm x content_h_cm (mantendo proporcao),
    centra num canvas branco com margem margin_cm a volta,
    e desenha marcas de corte em L nos 4 cantos.
    """
    from PIL import ImageDraw

    def cm2px(cm): return int(round(cm * dpi / 2.54))

    cw = cm2px(content_w_cm)
    ch = cm2px(content_h_cm)
    mg = cm2px(margin_cm)
    ml = cm2px(mark_len_cm)
    mo = cm2px(mark_offset_cm)

    img_ratio    = img.width / img.height
    target_ratio = cw / ch
    if img_ratio > target_ratio:
        new_w, new_h = cw, round(cw / img_ratio)
    else:
        new_w, new_h = round(ch * img_ratio), ch
    img_scaled = img.resize((new_w, new_h), Image.LANCZOS)

    total_w = mg + cw + mg
    total_h = mg + ch + mg
    canvas = Image.new("RGB", (total_w, total_h), bg)

    x0 = mg + (cw - new_w) // 2
    y0 = mg + (ch - new_h) // 2
    canvas.paste(img_scaled, (x0, y0))

    lx, rx = mg, mg + cw
    ty, by = mg, mg + ch
    draw = ImageDraw.Draw(canvas)
    t = mark_thick_px

    def L_mark(cx, cy, dx, dy):
        hx0, hx1 = cx + dx * mo, cx + dx * (mo + ml)
        draw.rectangle([min(hx0,hx1), cy - t//2, max(hx0,hx1), cy + t//2], fill=mark_color)
        vy0, vy1 = cy + dy * mo, cy + dy * (mo + ml)
        draw.rectangle([cx - t//2, min(vy0,vy1), cx + t//2, max(vy0,vy1)], fill=mark_color)

    L_mark(lx, ty, dx=-1, dy=-1)
    L_mark(rx, ty, dx=+1, dy=-1)
    L_mark(lx, by, dx=-1, dy=+1)
    L_mark(rx, by, dx=+1, dy=+1)

    return canvas


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
    do_crop         = opts.get("crop_marks", False)
    n_pairs         = len(pairs_indices)
    progress        = st.progress(0, text="A rasterizar páginas…")
    merged_images   = []

    for i, (li, ri) in enumerate(pairs_indices):
        left  = render_page(doc.load_page(li), dpi, bg)
        right = render_page(doc.load_page(ri), dpi, bg) if ri is not None else Image.new("RGB", left.size, bg)
        merged = merge_side_by_side(left, right, align_by=align_by, gap_px=gap, bg=bg)
        if sharpen:
            merged = apply_sharpen(merged)
        if do_crop:
            merged = scale_and_crop_marks(
                merged,
                content_w_cm=opts["crop_w"], content_h_cm=opts["crop_h"],
                dpi=dpi, margin_cm=opts["crop_margin"],
                mark_len_cm=opts["crop_marklen"], bg=bg,
            )
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
    if opts.get("crop_marks", False):
        merged = scale_and_crop_marks(
            merged,
            content_w_cm=opts["crop_w"], content_h_cm=opts["crop_h"],
            dpi=dpi, margin_cm=opts["crop_margin"],
            mark_len_cm=opts["crop_marklen"], bg=bg,
        )
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
    st.markdown("**Escala + Marcas de corte**")
    crop_marks = st.toggle("Escalar e adicionar marcas de corte", value=False,
                           help="Escala cada imagem para 13.3×20.5 cm e adiciona marcas de corte em L.")
    if crop_marks:
        c1, c2 = st.columns(2)
        with c1:
            crop_w = st.number_input("Largura (cm)", value=20.5, step=0.1, format="%.1f")
        with c2:
            crop_h = st.number_input("Altura (cm)",  value=13.3, step=0.1, format="%.1f")
        crop_margin = st.slider("Margem branca (mm)", 2, 20, 5, 1) / 10  # → cm
        crop_marklen = st.slider("Comprimento das marcas (mm)", 2, 15, 4, 1) / 10
    else:
        crop_w, crop_h, crop_margin, crop_marklen = 20.5, 13.3, 0.5, 0.4

    st.divider()
    st.markdown("**Preview**")
    preview_width = st.slider("Largura máx. (px)", 400, 2000, 900, 100)
    preview_1to1  = st.toggle("Mostrar 1:1", value=False)

OPTS = dict(dpi=dpi, fmt=fmt, align_by=align_by, gap_px=gap_px, bg=BG, sharpen=sharpen,
            crop_marks=crop_marks, crop_w=crop_w, crop_h=crop_h,
            crop_margin=crop_margin, crop_marklen=crop_marklen)


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
            fkey = f"normal_{f.name}_{f.size}"
            if fkey not in st.session_state:
                try:
                    out_bytes, mime, ext, n_pages, merged = process_normal(f.read(), OPTS)
                    base  = f.name.rsplit(".", 1)[0]
                    fname = f"{base}_merged.{ext}"
                    st.session_state[fkey] = dict(
                        out_bytes=out_bytes, mime=mime, ext=ext, fname=fname,
                        n_pages=n_pages, n_pairs=len(merged),
                        preview_bytes=[make_preview(m, preview_width, preview_1to1) for m in merged],
                    )
                except Exception as e:
                    st.error(f"**{f.name}**: {e}", icon="❌")
            res = st.session_state.get(fkey)
            if res:
                show_result(res["out_bytes"], res["mime"], res["ext"],
                            res["fname"], res["n_pages"], res["n_pairs"], dpi)
                if len(res["preview_bytes"]) == 1:
                    st.image(res["preview_bytes"][0])
                else:
                    cols = st.columns(min(len(res["preview_bytes"]), 3))
                    for idx, pb in enumerate(res["preview_bytes"]):
                        with cols[idx % 3]:
                            st.image(pb, caption=f"Par {idx + 1}")
                st.download_button(
                    f"⬇️  Descarregar {res['fname']}", data=res["out_bytes"],
                    file_name=res["fname"], mime=res["mime"],
                    use_container_width=True, key=f"dl_{fkey}",
                )
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

    dkey = f"dual_{getattr(file_a,'name','') }_{getattr(file_a,'size',0)}_{getattr(file_b,'name','') }_{getattr(file_b,'size',0)}"
    if file_a and file_b:
        if dkey not in st.session_state:
            try:
                out_bytes, mime, ext, merged_img = process_dual(file_a.read(), file_b.read(), OPTS)
                name_a = file_a.name.rsplit(".", 1)[0]
                name_b = file_b.name.rsplit(".", 1)[0]
                fname  = f"{name_a}+{name_b}.{ext}"
                st.session_state[dkey] = dict(
                    out_bytes=out_bytes, mime=mime, ext=ext, fname=fname,
                    preview=make_preview(merged_img, preview_width, preview_1to1),
                )
            except Exception as e:
                st.error(f"{e}", icon="❌")
        res = st.session_state.get(dkey)
        if res:
            show_result(res["out_bytes"], res["mime"], res["ext"], res["fname"], None, 1, dpi)
            st.image(res["preview"])
            st.download_button(
                f"⬇️  Descarregar {res['fname']}", data=res["out_bytes"],
                file_name=res["fname"], mime=res["mime"],
                use_container_width=True, key=f"dl_{dkey}",
            )
    elif file_a or file_b:
        st.warning(f"Falta carregar o **{'PDF direito (B)' if file_a else 'PDF esquerdo (A)'}**.", icon="⚠️")
    else:
        st.info("⬆️  Carregue os dois PDFs acima para começar.", icon="📂")


# ══════════════════════════════════════════════
# Tab Arranjo
# ══════════════════════════════════════════════
with tab_arrange:
    import base64
    import json
    import streamlit.components.v1 as components

    st.markdown(
        "Carregue um PDF, **arraste** as páginas para os pares e clique **Gerar**."
    )

    arr_file = st.file_uploader("Escolher PDF", type=["pdf"], key="arrange_up")

    if not arr_file:
        st.info("⬆️  Carregue um PDF para começar.", icon="📂")
    else:
        arr_bytes = arr_file.read()
        arr_bytes = _preprocess_pdf(arr_bytes)
        cache_key = f"arr_{arr_file.name}_{len(arr_bytes)}"

        # ── Gerar thumbnails (apenas uma vez por ficheiro) ────────────────────
        if cache_key not in st.session_state:
            with fitz.open(stream=arr_bytes, filetype="pdf") as _doc:
                n_arr  = _doc.page_count
                thumbs = [render_page_thumb(_doc.load_page(i), max_px=220) for i in range(n_arr)]
            # Codifica em base64 para passar ao componente HTML
            thumbs_b64 = []
            for t in thumbs:
                buf = io.BytesIO()
                t.save(buf, format="PNG")
                thumbs_b64.append(base64.b64encode(buf.getvalue()).decode())
            default_pairs = [
                [i * 2, i * 2 + 1 if i * 2 + 1 < n_arr else -1]
                for i in range(math.ceil(n_arr / 2))
            ]
            st.session_state[cache_key + "_n"]      = n_arr
            st.session_state[cache_key + "_b64"]    = thumbs_b64
            st.session_state[cache_key + "_bytes"]  = arr_bytes
            st.session_state[cache_key + "_pairs"]  = default_pairs

        n_arr       : int   = st.session_state[cache_key + "_n"]
        thumbs_b64  : list  = st.session_state[cache_key + "_b64"]
        arr_bytes   : bytes = st.session_state[cache_key + "_bytes"]
        saved_pairs : list  = st.session_state[cache_key + "_pairs"]

        # ── Componente HTML drag-and-drop ─────────────────────────────────────
        thumbs_json = json.dumps(thumbs_b64)
        pairs_json  = json.dumps(saved_pairs)

        html_component = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'DM Sans', 'Segoe UI', sans-serif;
    background: transparent;
    color: #1f2937;
    padding: 4px 0 8px 0;
  }}

  /* ── Banco de páginas ── */
  #bank-label {{
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: #6b7280;
    margin-bottom: 8px;
  }}
  #bank {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 10px;
    background: #f3f4f6;
    border: 1.5px dashed #d1d5db;
    border-radius: 10px;
    min-height: 80px;
    margin-bottom: 18px;
  }}
  .page-chip {{
    position: relative;
    cursor: grab;
    border-radius: 6px;
    overflow: hidden;
    border: 2px solid #e5e7eb;
    background: #fff;
    transition: box-shadow .15s, border-color .15s, transform .1s;
    user-select: none;
    width: 80px;
  }}
  .page-chip:hover {{ border-color: #6366f1; box-shadow: 0 2px 8px rgba(99,102,241,.25); }}
  .page-chip.dragging {{ opacity: .4; transform: scale(.96); }}
  .page-chip img {{ width: 100%; display: block; }}
  .page-chip .lbl {{
    font-size: 0.62rem;
    text-align: center;
    padding: 2px 0 3px;
    color: #6b7280;
    background: #f9fafb;
  }}
  .page-chip .rm {{
    display: none;
    position: absolute;
    top: 2px; right: 2px;
    width: 16px; height: 16px;
    background: #ef4444;
    color: #fff;
    border-radius: 50%;
    font-size: 9px;
    line-height: 16px;
    text-align: center;
    cursor: pointer;
    font-weight: 700;
  }}
  .page-chip:hover .rm {{ display: block; }}

  /* ── Pares ── */
  #pairs-label {{
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: #6b7280;
    margin-bottom: 8px;
  }}
  #pairs-list {{ display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }}

  .pair-row {{
    display: flex;
    align-items: stretch;
    gap: 6px;
    background: #f8f9fb;
    border: 1.5px solid #e3e6ea;
    border-radius: 10px;
    padding: 8px 10px;
  }}
  .pair-num {{
    font-size: 0.7rem;
    font-weight: 700;
    color: #9ca3af;
    width: 18px;
    padding-top: 30px;
    text-align: center;
    flex-shrink: 0;
  }}
  .pair-slot {{
    width: 100px;
    min-height: 90px;
    border: 2px dashed #d1d5db;
    border-radius: 8px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-size: 0.65rem;
    color: #9ca3af;
    transition: border-color .15s, background .15s;
    position: relative;
    overflow: hidden;
    flex-shrink: 0;
  }}
  .pair-slot.over   {{ border-color: #6366f1; background: #eef2ff; }}
  .pair-slot.filled {{ border-style: solid; border-color: #6366f1; background: #fff; }}
  .pair-slot img    {{ width: 100%; display: block; }}
  .pair-slot .slot-lbl {{
    font-size: 0.6rem;
    color: #6b7280;
    text-align: center;
    padding: 2px 0 3px;
    width: 100%;
    background: #f9fafb;
  }}
  .pair-slot .slot-rm {{
    position: absolute;
    top: 3px; right: 3px;
    width: 17px; height: 17px;
    background: #ef4444;
    color: #fff;
    border-radius: 50%;
    font-size: 10px;
    line-height: 17px;
    text-align: center;
    cursor: pointer;
    font-weight: 700;
    display: none;
  }}
  .pair-slot.filled:hover .slot-rm {{ display: block; }}

  .pair-divider {{
    font-size: 1rem;
    color: #d1d5db;
    align-self: center;
    flex-shrink: 0;
    padding: 0 2px;
  }}
  .pair-delete {{
    align-self: center;
    background: none;
    border: none;
    cursor: pointer;
    color: #d1d5db;
    font-size: 1rem;
    padding: 4px;
    border-radius: 6px;
    transition: color .15s, background .15s;
    margin-left: auto;
    flex-shrink: 0;
  }}
  .pair-delete:hover {{ color: #ef4444; background: #fee2e2; }}

  /* ── Botões ── */
  .btn-row {{ display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }}
  .btn {{
    padding: 6px 14px;
    border-radius: 7px;
    border: 1.5px solid #d1d5db;
    background: #fff;
    font-size: 0.78rem;
    cursor: pointer;
    font-weight: 500;
    transition: border-color .15s, background .15s;
  }}
  .btn:hover {{ border-color: #6366f1; background: #eef2ff; color: #4f46e5; }}
  .btn-primary {{
    background: #111827;
    color: #fff;
    border-color: #111827;
    font-weight: 600;
  }}
  .btn-primary:hover {{ background: #374151; border-color: #374151; color: #fff; }}

  #output {{
    font-family: monospace;
    font-size: 0.72rem;
    color: #6b7280;
    margin-top: 4px;
    word-break: break-all;
  }}
</style>
</head>
<body>

<div id="bank-label">Páginas disponíveis — arraste para um par</div>
<div id="bank"></div>

<div id="pairs-label">Pares</div>
<div id="pairs-list"></div>

<div class="btn-row">
  <button class="btn" onclick="addPair()">＋ Adicionar par</button>
  <button class="btn" onclick="resetPairs()">↺ Repor sequencial</button>
  <button class="btn btn-primary" onclick="emitPairs()">🚀 Gerar</button>
</div>
<div id="output"></div>

<script>
const THUMBS  = {thumbs_json};
const N       = THUMBS.length;
let   pairs   = {pairs_json};   // [[li, ri], ...]  ri=-1 → branco

// ── Drag state ────────────────────────────────────────────────
let dragSrc = null;   // {{ pageIdx, origin: 'bank'|'slot', pairIdx, side }}

// ── Render ────────────────────────────────────────────────────
function render() {{
  renderBank();
  renderPairs();
}}

function chipHTML(pageIdx, showRm, rmCb) {{
  return `<div class="page-chip" draggable="true"
    data-page="${{pageIdx}}"
    onmousedown="event.stopPropagation()"
  >
    <img src="data:image/png;base64,${{THUMBS[pageIdx]}}" draggable="false">
    <div class="lbl">Pág. ${{pageIdx+1}}</div>
    ${{showRm ? `<div class="rm" onclick="${{rmCb}};render()">✕</div>` : ''}}
  </div>`;
}}

function renderBank() {{
  const bank = document.getElementById('bank');
  bank.innerHTML = '';
  for (let i = 0; i < N; i++) {{
    const div = document.createElement('div');
    div.innerHTML = chipHTML(i, false, '');
    const chip = div.firstElementChild;
    addChipDrag(chip, i, 'bank', null, null);
    bank.appendChild(chip);
  }}
}}

function slotHTML(pairIdx, side) {{
  const ri = pairs[pairIdx][1];
  const li = pairs[pairIdx][0];
  const pageIdx = side === 'L' ? li : ri;
  const filled  = pageIdx >= 0;
  return `<div class="pair-slot ${{filled ? 'filled' : ''}}"
    id="slot-${{pairIdx}}-${{side}}"
    data-pair="${{pairIdx}}" data-side="${{side}}"
  >
    ${{filled
      ? `<img src="data:image/png;base64,${{THUMBS[pageIdx]}}" draggable="false">
         <div class="slot-lbl">Pág. ${{pageIdx+1}}</div>
         <div class="slot-rm" onclick="clearSlot(${{pairIdx}},'${{side}}')">✕</div>`
      : `<span>${{side==='L'?'Esquerda':'Direita (opcional)'}}</span>`
    }}
  </div>`;
}}

function renderPairs() {{
  const list = document.getElementById('pairs-list');
  list.innerHTML = '';
  pairs.forEach((p, pi) => {{
    const row = document.createElement('div');
    row.className = 'pair-row';
    row.innerHTML = `
      <div class="pair-num">${{pi+1}}</div>
      ${{slotHTML(pi,'L')}}
      <div class="pair-divider">↔</div>
      ${{slotHTML(pi,'R')}}
      <button class="pair-delete" title="Remover par" onclick="removePair(${{pi}})">✕</button>
    `;
    list.appendChild(row);
  }});
  // Adiciona listeners de drop a cada slot
  document.querySelectorAll('.pair-slot').forEach(slot => {{
    slot.addEventListener('dragover', e => {{
      e.preventDefault();
      slot.classList.add('over');
    }});
    slot.addEventListener('dragleave', () => slot.classList.remove('over'));
    slot.addEventListener('drop', e => {{
      e.preventDefault();
      slot.classList.remove('over');
      if (dragSrc === null) return;
      const pairIdx = parseInt(slot.dataset.pair);
      const side    = slot.dataset.side;
      // Limpa origem se era um slot
      if (dragSrc.origin === 'slot') {{
        pairs[dragSrc.pairIdx][dragSrc.side === 'L' ? 0 : 1] = -1;
      }}
      pairs[pairIdx][side === 'L' ? 0 : 1] = dragSrc.pageIdx;
      dragSrc = null;
      render();
    }});
  }});
  // Chips dentro dos slots também são arrastáveis
  document.querySelectorAll('.pair-slot.filled').forEach(slot => {{
    const img = slot.querySelector('img');
    if (!img) return;
    const pi   = parseInt(slot.dataset.pair);
    const side = slot.dataset.side;
    const pgIdx = pairs[pi][side === 'L' ? 0 : 1];
    slot.setAttribute('draggable', 'true');
    slot.addEventListener('dragstart', e => {{
      dragSrc = {{ pageIdx: pgIdx, origin: 'slot', pairIdx: pi, side }};
      slot.classList.add('dragging');
    }});
    slot.addEventListener('dragend', () => slot.classList.remove('dragging'));
  }});
}}

function addChipDrag(chip, pageIdx, origin, pairIdx, side) {{
  chip.addEventListener('dragstart', e => {{
    dragSrc = {{ pageIdx, origin, pairIdx, side }};
    chip.classList.add('dragging');
  }});
  chip.addEventListener('dragend', () => chip.classList.remove('dragging'));
}}

function clearSlot(pairIdx, side) {{
  pairs[pairIdx][side === 'L' ? 0 : 1] = -1;
  render();
}}

function removePair(pi) {{
  pairs.splice(pi, 1);
  render();
}}

function addPair() {{
  pairs.push([0, -1]);
  render();
}}

function resetPairs() {{
  pairs = [];
  for (let i = 0; i < Math.ceil(N/2); i++) {{
    pairs.push([i*2, i*2+1 < N ? i*2+1 : -1]);
  }}
  render();
}}

function emitPairs() {{
  // Valida: todos os pares têm pelo menos a esquerda preenchida
  const invalid = pairs.some(p => p[0] < 0);
  if (invalid) {{
    document.getElementById('output').textContent = '⚠️ Todos os pares precisam de uma página à esquerda.';
    return;
  }}
  document.getElementById('output').textContent = 'A enviar…';
  // Comunica com Streamlit via query param (hack compatível com st.query_params)
  const encoded = encodeURIComponent(JSON.stringify(pairs));
  window.parent.postMessage({{type:'streamlit:setComponentValue', value: JSON.stringify(pairs)}}, '*');
}}

render();
</script>
</body>
</html>
"""

        # ── Receber valor do componente ───────────────────────────────────────
        result = components.html(html_component, height=max(420, n_arr * 18 + 280), scrolling=True)

        # Como components.html não devolve valor, usamos uma text_area hidden
        # para o utilizador colar ou usar o botão Gerar abaixo em Python
        st.divider()
        st.markdown("**Confirmar pares e gerar**")
        st.caption(
            "Depois de organizar os pares no editor acima, "
            "confirme a lista e clique **Gerar ficheiro**. "
            "O editor actualiza a caixa automaticamente ao clicar 🚀 Gerar — "
            "ou edite manualmente no formato `[[0,1],[2,3],…]` (índices base 0, -1 = branco)."
        )

        pairs_json_edit = st.text_area(
            "Pares (JSON)", value=json.dumps(saved_pairs),
            height=68, key=f"pairs_json_{cache_key}",
            label_visibility="visible",
        )

        # Tenta parsear o JSON editado
        try:
            edited_pairs = json.loads(pairs_json_edit)
            assert isinstance(edited_pairs, list) and all(
                isinstance(p, list) and len(p) == 2 for p in edited_pairs
            )
            st.session_state[cache_key + "_pairs"] = edited_pairs
        except Exception:
            st.warning("JSON inválido — corrija o formato.", icon="⚠️")
            edited_pairs = saved_pairs

        bc1, bc2 = st.columns([1, 3])
        with bc1:
            if st.button("↺  Repor", use_container_width=True, key="arr_reset"):
                edited_pairs = [
                    [i * 2, i * 2 + 1 if i * 2 + 1 < n_arr else -1]
                    for i in range(math.ceil(n_arr / 2))
                ]
                st.session_state[cache_key + "_pairs"] = edited_pairs
                st.session_state.pop(cache_key + "_result", None)
                st.rerun()

        if st.button("🚀  Gerar ficheiro", type="primary", use_container_width=True, key="arr_gen"):
            valid = [p for p in edited_pairs if isinstance(p, list) and len(p) == 2 and p[0] >= 0]
            if not valid:
                st.error("Não há pares válidos para gerar.", icon="❌")
            else:
                try:
                    pairs_tuples = [(p[0], p[1] if p[1] >= 0 else None) for p in valid]
                    with fitz.open(stream=arr_bytes, filetype="pdf") as doc:
                        out_bytes, mime, ext, merged = process_pairs(pairs_tuples, doc, OPTS)
                    base  = arr_file.name.rsplit(".", 1)[0]
                    fname = f"{base}_arranjo.{ext}"
                    # Guarda no session_state para sobreviver ao rerun do download
                    st.session_state[cache_key + "_result"] = {
                        "out_bytes": out_bytes, "mime": mime, "ext": ext,
                        "fname": fname, "n_pages": n_arr, "n_pairs": len(merged),
                        "preview_bytes": [make_preview(m, preview_width, False) for m in merged],
                    }
                except Exception as e:
                    st.error(f"{e}", icon="❌")

        # Mostra resultado persistido (sobrevive a reruns)
        res = st.session_state.get(cache_key + "_result")
        if res:
            show_result(res["out_bytes"], res["mime"], res["ext"],
                        res["fname"], res["n_pages"], res["n_pairs"], dpi)
            if len(res["preview_bytes"]) == 1:
                st.image(res["preview_bytes"][0])
            else:
                cols = st.columns(min(len(res["preview_bytes"]), 3))
                for idx, pb in enumerate(res["preview_bytes"]):
                    with cols[idx % 3]:
                        st.image(pb, caption=f"Par {idx + 1}")
            st.download_button(
                f"⬇️  Descarregar {res['fname']}",
                data=res["out_bytes"], file_name=res["fname"],
                mime=res["mime"], use_container_width=True,
                key="arr_download",
            )
