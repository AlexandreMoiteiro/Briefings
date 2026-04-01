# app.py — PDF Side-by-Side
# Requisitos: streamlit, pymupdf (fitz), pillow
# Execução: streamlit run app.py

import io
import math
import base64
import json
import streamlit as st
import streamlit.components.v1 as components
import fitz  # PyMuPDF
from PIL import Image, ImageFilter, ImageDraw

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
  .result-meta { font-family: 'DM Mono', monospace; font-size: 0.78rem; color: #6b7280; margin-bottom: 0.5rem; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem;
           font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; margin-left: 6px; }
  .badge-pdf { background: #fef3c7; color: #92400e; }
  .badge-png { background: #dbeafe; color: #1e40af; }
  .badge-jpg { background: #d1fae5; color: #065f46; }
  .thumb-label { font-family: 'DM Mono', monospace; font-size: 0.72rem; color: #6b7280; text-align: center; margin-top: 2px; }
  hr { border: none; border-top: 1px dashed #d1d5db; margin: 1.5rem 0; }
  [data-testid="stSidebar"] { background: #f0f2f5; }
  .stDownloadButton button { border-radius: 8px !important; font-family: 'DM Mono', monospace !important; font-size: 0.82rem !important; }
  .warn-box { background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; padding:8px 12px;
              font-size:0.78rem; color:#92400e; margin-bottom:8px; }
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


def render_page_thumb(page: fitz.Page, max_px: int = 220) -> Image.Image:
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


def fit_two_cards_on_a4(
    left: Image.Image,
    right: Image.Image,
    card_w_cm: float = 13.3,
    card_h_cm: float = 20.5,
    dpi: int = 500,
    mark_len_cm: float = 0.4,
    mark_offset_cm: float = 0.15,
    mark_thick_px: int = 3,
    mark_color=(0, 0, 0),
    bg=(255, 255, 255),
) -> tuple:
    """
    Coloca duas cartas num canvas A4 paisagem (29.7x21 cm).
    Se as cartas forem maiores que o A4, usa o canvas das cartas e desenha
    as marcas de corte por cima da imagem (com linha semi-transparente).
    Devolve (Image, overflow: bool).
    """
    def cm2px(cm): return int(round(cm * dpi / 2.54))

    a4_w = cm2px(29.7)
    a4_h = cm2px(21.0)
    cw   = cm2px(card_w_cm)
    ch   = cm2px(card_h_cm)
    ml   = cm2px(mark_len_cm)
    mo   = cm2px(mark_offset_cm)
    t    = mark_thick_px

    def scale_card(img):
        r = img.width / img.height; tr = cw / ch
        nw, nh = (cw, round(cw / r)) if r > tr else (round(ch * r), ch)
        return img.resize((nw, nh), Image.LANCZOS)

    left_s  = scale_card(left)
    right_s = scale_card(right)

    # Verifica se as cartas cabem no A4
    min_gap_h = cm2px(0.15)
    min_gap_v = cm2px(0.1)
    natural_gap_h = (a4_w - cw * 2) // 3
    natural_gap_v = (a4_h - ch) // 2
    overflow = natural_gap_h < min_gap_h or natural_gap_v < min_gap_v

    if overflow:
        # Canvas = tamanho das cartas + margem mínima para as marcas
        mg = cm2px(0.5)  # margem fixa para as marcas
        canvas_w = mg + cw + mg + cw + mg
        canvas_h = mg + ch + mg
        gap_h = mg
        gap_v = mg
        canvas = Image.new("RGB", (canvas_w, canvas_h), bg)
        lx1 = gap_h
        lx2 = gap_h + cw + gap_h
        ly  = gap_v
    else:
        gap_h = natural_gap_h
        gap_v = natural_gap_v
        canvas = Image.new("RGB", (a4_w, a4_h), bg)
        lx1 = gap_h
        lx2 = gap_h + cw + gap_h
        ly  = gap_v

    def paste_centered(img_s, ox, oy):
        px = ox + (cw - img_s.width)  // 2
        py = oy + (ch - img_s.height) // 2
        canvas.paste(img_s, (px, py))

    paste_centered(left_s,  lx1, ly)
    paste_centered(right_s, lx2, ly)

    # Marcas de corte — se overflow, desenha por cima com linha sólida preta
    ml_eff = min(ml, max(gap_h - mo - 2, 6), max(gap_v - mo - 2, 6))
    ml_eff = max(ml_eff, 6)

    draw = ImageDraw.Draw(canvas)

    def L_mark(cx, cy, dx, dy):
        hx0, hx1 = cx + dx * mo, cx + dx * (mo + ml_eff)
        draw.rectangle([min(hx0,hx1), cy - t//2,
                        max(hx0,hx1), cy + t//2], fill=mark_color)
        vy0, vy1 = cy + dy * mo, cy + dy * (mo + ml_eff)
        draw.rectangle([cx - t//2, min(vy0,vy1),
                        cx + t//2, max(vy0,vy1)], fill=mark_color)

    for ox in (lx1, lx2):
        rx_ = ox + cw; ty_ = ly; by_ = ly + ch
        L_mark(ox,  ty_, -1, -1)
        L_mark(rx_, ty_, +1, -1)
        L_mark(ox,  by_, -1, +1)
        L_mark(rx_, by_, +1, +1)

    return canvas, overflow


# ─────────────────────────────────────────────
# Modo impressão frente/verso
# ─────────────────────────────────────────────

def reorder_for_duplex(pairs: list) -> list:
    """
    Recebe lista de pares e reordena para impressão frente/verso:
    Páginas ímpares (frentes): 1, 3, 5, ...
    Páginas pares  (versos):   2, 4, 6, ...
    → resultado: [par1, par3, par5, ..., par2, par4, par6, ...]
    Assim ao imprimir frente/verso, o verso do par1 é o par2, etc.
    """
    fronts = pairs[0::2]
    backs  = pairs[1::2]
    return fronts + backs


# ─────────────────────────────────────────────
# Processadores de alto nível
# ─────────────────────────────────────────────

def process_pairs(pairs_indices: list, doc: fitz.Document, opts: dict):
    dpi, fmt        = opts["dpi"], opts["fmt"]
    align_by, gap   = opts["align_by"], opts["gap_px"]
    bg, sharpen     = opts["bg"], opts["sharpen"]
    do_crop         = opts.get("crop_marks", False)
    do_duplex       = opts.get("duplex", False)
    n_pairs         = len(pairs_indices)
    progress        = st.progress(0, text="A rasterizar páginas…")
    merged_images   = []
    had_overflow    = False

    for i, (li, ri) in enumerate(pairs_indices):
        left  = render_page(doc.load_page(li), dpi, bg)
        right = render_page(doc.load_page(ri), dpi, bg) if ri is not None else Image.new("RGB", left.size, bg)
        if do_crop:
            merged, overflow = fit_two_cards_on_a4(
                left, right,
                card_w_cm=opts["crop_w"], card_h_cm=opts["crop_h"],
                dpi=dpi,
                mark_len_cm=opts["crop_marklen"],
                mark_offset_cm=0.15,
                bg=bg,
            )
            if overflow:
                had_overflow = True
        else:
            merged = merge_side_by_side(left, right, align_by=align_by, gap_px=gap, bg=bg)
        if sharpen:
            merged = apply_sharpen(merged)
        merged_images.append(merged)
        progress.progress((i + 1) / n_pairs, text=f"Par {i + 1}/{n_pairs}…")

    progress.empty()

    if do_duplex and len(merged_images) > 1:
        merged_images = reorder_for_duplex(merged_images)

    if len(merged_images) == 1:
        out  = encode_image(merged_images[0], fmt)
        ext  = "png" if fmt == "PNG" else "jpg"
        mime = "image/png" if fmt == "PNG" else "image/jpeg"
    else:
        out  = images_to_pdf_bytes(merged_images)
        ext  = "pdf"
        mime = "application/pdf"

    return out, mime, ext, merged_images, had_overflow


def process_normal(pdf_bytes: bytes, opts: dict):
    pdf_bytes = _preprocess_pdf(pdf_bytes)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        n = doc.page_count
        if n < 1:
            raise ValueError("PDF inválido (sem páginas).")
        pairs = [(i * 2, i * 2 + 1 if i * 2 + 1 < n else None) for i in range(math.ceil(n / 2))]
        out, mime, ext, merged, overflow = process_pairs(pairs, doc, opts)
    return out, mime, ext, n, merged, overflow


def process_dual(pdf_a: bytes, pdf_b: bytes, opts: dict):
    pdf_a = _preprocess_pdf(pdf_a)
    pdf_b = _preprocess_pdf(pdf_b)
    dpi, fmt        = opts["dpi"], opts["fmt"]
    align_by, gap   = opts["align_by"], opts["gap_px"]
    bg, sharpen     = opts["bg"], opts["sharpen"]

    progress = st.progress(0, text="A processar PDF A…")
    with fitz.open(stream=pdf_a, filetype="pdf") as da:
        if da.page_count < 1: raise ValueError("PDF A inválido.")
        left = render_page(da.load_page(0), dpi, bg)
    progress.progress(0.5, text="A processar PDF B…")
    with fitz.open(stream=pdf_b, filetype="pdf") as db:
        if db.page_count < 1: raise ValueError("PDF B inválido.")
        right = render_page(db.load_page(0), dpi, bg)
    progress.progress(0.9, text="A juntar…")

    overflow = False
    if opts.get("crop_marks", False):
        merged, overflow = fit_two_cards_on_a4(
            left, right,
            card_w_cm=opts["crop_w"], card_h_cm=opts["crop_h"],
            dpi=dpi, mark_len_cm=opts["crop_marklen"], mark_offset_cm=0.15, bg=bg,
        )
    else:
        merged = merge_side_by_side(left, right, align_by=align_by, gap_px=gap, bg=bg)
    if sharpen:
        merged = apply_sharpen(merged)
    progress.empty()

    out  = encode_image(merged, fmt)
    ext  = "png" if fmt == "PNG" else "jpg"
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return out, mime, ext, merged, overflow


# ─────────────────────────────────────────────
# Helpers de UI
# ─────────────────────────────────────────────

def show_result(out_bytes, mime, ext, fname, n_pages, pairs_count, dpi, overflow=False):
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
    if overflow:
        st.markdown(
            '<div class="warn-box">⚠️ As cartas são maiores que o A4 — as marcas de corte '
            'foram desenhadas por cima da imagem. Reduza as dimensões da carta na sidebar.</div>',
            unsafe_allow_html=True,
        )


def show_previews(merged_images, preview_width, preview_1to1):
    if len(merged_images) == 1:
        st.image(make_preview(merged_images[0], preview_width, preview_1to1))
    else:
        cols = st.columns(min(len(merged_images), 3))
        for idx, img in enumerate(merged_images):
            with cols[idx % 3]:
                st.image(make_preview(img, preview_width // 3, False), caption=f"Pág. {idx + 1}")


def show_download(out_bytes, mime, fname, key):
    st.download_button(
        f"⬇️  Descarregar {fname}", data=out_bytes,
        file_name=fname, mime=mime,
        use_container_width=True, key=key,
    )


# ─────────────────────────────────────────────
# Session state helpers — ratio lock
# ─────────────────────────────────────────────

def _init_state():
    if "crop_w" not in st.session_state:
        st.session_state["crop_w"] = 13.3
    if "crop_h" not in st.session_state:
        st.session_state["crop_h"] = 20.5
    # ratio inicial
    if "crop_ratio" not in st.session_state:
        st.session_state["crop_ratio"] = st.session_state["crop_h"] / st.session_state["crop_w"]

def _on_crop_w_change():
    st.session_state["crop_h"] = round(st.session_state["crop_w"] * st.session_state["crop_ratio"], 1)

def _on_crop_h_change():
    st.session_state["crop_w"] = round(st.session_state["crop_h"] / st.session_state["crop_ratio"], 1)

def _on_ratio_unlock():
    # Recalcula ratio com os valores actuais antes de desligar o lock
    st.session_state["crop_ratio"] = st.session_state["crop_h"] / max(st.session_state["crop_w"], 0.1)

_init_state()


# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️  Opções")

    # Botão de actualizar (força rerun e limpa resultados em cache)
    if st.button("🔄  Actualizar resultados", use_container_width=True,
                 help="Limpa resultados guardados e processa de novo com as opções actuais."):
        # Remove todas as chaves de resultado do session_state
        keys_to_clear = [k for k in st.session_state if
                         k.endswith("_result") or k.startswith("normal_") or k.startswith("dual_")]
        for k in keys_to_clear:
            del st.session_state[k]
        st.rerun()

    st.divider()
    dpi      = st.slider("DPI", 72, 900, 500, 50,
                         help="Resolução de rasterização. Valores altos = mais qualidade e mais tempo.")
    fmt      = st.radio("Formato de saída", ["PNG", "JPG"], horizontal=True)
    align_by = st.radio("Alinhar por", ["height", "width"], horizontal=True)
    gap_px   = st.slider("Espaço entre páginas (px)", 0, 200, 0, 4)
    bg_label = st.selectbox("Cor de fundo", ["Branco", "Cinza claro", "Preto"])
    BG       = {"Branco": (255, 255, 255), "Cinza claro": (240, 242, 245), "Preto": (0, 0, 0)}[bg_label]
    sharpen  = st.toggle("Aumentar nitidez", value=True)

    st.divider()
    st.markdown("**Impressão frente/verso**")
    duplex = st.toggle("Modo frente/verso (1,3,2,4…)", value=False,
                       help="Reordena as páginas para impressão duplex: primeiro todas as frentes, depois todos os versos.")

    st.divider()
    st.markdown("**Marcas de corte (A4 paisagem)**")
    crop_marks = st.toggle("Activar marcas de corte", value=False,
                           help="Escala cada carta para as dimensões definidas e posiciona duas num A4 paisagem.")
    if crop_marks:
        ratio_lock = st.toggle("🔒 Manter proporção", value=True, key="ratio_lock",
                               on_change=_on_ratio_unlock)
        c1, c2 = st.columns(2)
        with c1:
            st.number_input(
                "Largura (cm)", min_value=1.0, max_value=50.0,
                step=0.1, format="%.1f",
                key="crop_w",
                on_change=_on_crop_w_change if ratio_lock else None,
            )
        with c2:
            st.number_input(
                "Altura (cm)", min_value=1.0, max_value=50.0,
                step=0.1, format="%.1f",
                key="crop_h",
                on_change=_on_crop_h_change if ratio_lock else None,
            )
        crop_w    = st.session_state["crop_w"]
        crop_h    = st.session_state["crop_h"]

        # Aviso visual se as cartas não cabem no A4
        def cm2px_check(cm): return int(round(cm * 150 / 2.54))
        a4_w_px = cm2px_check(29.7); a4_h_px = cm2px_check(21.0)
        cw_px   = cm2px_check(crop_w); ch_px   = cm2px_check(crop_h)
        gap_h_check = (a4_w_px - cw_px * 2) // 3
        gap_v_check = (a4_h_px - ch_px) // 2
        if gap_h_check < cm2px_check(0.15) or gap_v_check < cm2px_check(0.1):
            st.markdown(
                '<div class="warn-box">⚠️ Cartas maiores que o A4 — marcas ficarão por cima da imagem.</div>',
                unsafe_allow_html=True,
            )
        crop_marklen = st.slider("Comprimento das marcas (mm)", 2, 15, 4, 1) / 10
    else:
        crop_w, crop_h, crop_marklen = 13.3, 20.5, 0.4
    st.divider()
    st.markdown("**Preview**")
    preview_width = st.slider("Largura máx. (px)", 400, 2000, 900, 100)
    preview_1to1  = st.toggle("Mostrar 1:1", value=False)

OPTS = dict(dpi=dpi, fmt=fmt, align_by=align_by, gap_px=gap_px, bg=BG, sharpen=sharpen,
            crop_marks=crop_marks, crop_w=crop_w, crop_h=crop_h, crop_marklen=crop_marklen,
            duplex=duplex)


# ─────────────────────────────────────────────
# Cabeçalho
# ─────────────────────────────────────────────
st.title("PDF Side-by-Side")
st.caption(
    "**Modo normal** — pares automáticos.  \n"
    "**Modo dual** — 1.ª página de dois PDFs.  \n"
    "**Modo arranjo** — pares manuais com drag-and-drop."
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
                    out_bytes, mime, ext, n_pages, merged, overflow = process_normal(f.read(), OPTS)
                    base  = f.name.rsplit(".", 1)[0]
                    fname = f"{base}_merged.{ext}"
                    st.session_state[fkey] = dict(
                        out_bytes=out_bytes, mime=mime, ext=ext, fname=fname,
                        n_pages=n_pages, n_pairs=len(merged), overflow=overflow,
                        preview_bytes=[make_preview(m, preview_width, preview_1to1) for m in merged],
                    )
                except Exception as e:
                    st.error(f"**{f.name}**: {e}", icon="❌")
            res = st.session_state.get(fkey)
            if res:
                show_result(res["out_bytes"], res["mime"], res["ext"],
                            res["fname"], res["n_pages"], res["n_pairs"], dpi,
                            overflow=res.get("overflow", False))
                if len(res["preview_bytes"]) == 1:
                    st.image(res["preview_bytes"][0])
                else:
                    cols = st.columns(min(len(res["preview_bytes"]), 3))
                    for idx, pb in enumerate(res["preview_bytes"]):
                        with cols[idx % 3]:
                            st.image(pb, caption=f"Pág. {idx + 1}")
                show_download(res["out_bytes"], res["mime"], res["fname"], f"dl_{fkey}")
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

    dkey = f"dual_{getattr(file_a,'name','')}_{getattr(file_a,'size',0)}_{getattr(file_b,'name','')}_{getattr(file_b,'size',0)}"
    if file_a and file_b:
        if dkey not in st.session_state:
            try:
                out_bytes, mime, ext, merged_img, overflow = process_dual(file_a.read(), file_b.read(), OPTS)
                name_a = file_a.name.rsplit(".", 1)[0]
                name_b = file_b.name.rsplit(".", 1)[0]
                fname  = f"{name_a}+{name_b}.{ext}"
                st.session_state[dkey] = dict(
                    out_bytes=out_bytes, mime=mime, ext=ext, fname=fname, overflow=overflow,
                    preview=make_preview(merged_img, preview_width, preview_1to1),
                )
            except Exception as e:
                st.error(f"{e}", icon="❌")
        res = st.session_state.get(dkey)
        if res:
            show_result(res["out_bytes"], res["mime"], res["ext"], res["fname"],
                        None, 1, dpi, overflow=res.get("overflow", False))
            st.image(res["preview"])
            show_download(res["out_bytes"], res["mime"], res["fname"], f"dl_{dkey}")
    elif file_a or file_b:
        st.warning(f"Falta carregar o **{'PDF direito (B)' if file_a else 'PDF esquerdo (A)'}**.", icon="⚠️")
    else:
        st.info("⬆️  Carregue os dois PDFs acima para começar.", icon="📂")


# ══════════════════════════════════════════════
# Tab Arranjo
# ══════════════════════════════════════════════
with tab_arrange:
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

        if cache_key not in st.session_state:
            with fitz.open(stream=arr_bytes, filetype="pdf") as _doc:
                n_arr  = _doc.page_count
                thumbs = [render_page_thumb(_doc.load_page(i), max_px=220) for i in range(n_arr)]
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

        n_arr       = st.session_state[cache_key + "_n"]
        thumbs_b64  = st.session_state[cache_key + "_b64"]
        arr_bytes   = st.session_state[cache_key + "_bytes"]
        saved_pairs = st.session_state[cache_key + "_pairs"]

        thumbs_json = json.dumps(thumbs_b64)
        pairs_json  = json.dumps(saved_pairs)

        html_component = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'DM Sans', 'Segoe UI', sans-serif; background: transparent;
          color: #1f2937; padding: 4px 0 8px 0; }}
  #bank-label, #pairs-label {{ font-size: 0.72rem; font-weight: 600; letter-spacing: .08em;
    text-transform: uppercase; color: #6b7280; margin-bottom: 8px; }}
  #bank {{ display: flex; flex-wrap: wrap; gap: 8px; padding: 10px;
    background: #f3f4f6; border: 1.5px dashed #d1d5db; border-radius: 10px;
    min-height: 80px; margin-bottom: 18px; }}
  .page-chip {{ position: relative; cursor: grab; border-radius: 6px; overflow: hidden;
    border: 2px solid #e5e7eb; background: #fff;
    transition: box-shadow .15s, border-color .15s, transform .1s;
    user-select: none; width: 80px; }}
  .page-chip:hover {{ border-color: #6366f1; box-shadow: 0 2px 8px rgba(99,102,241,.25); }}
  .page-chip.dragging {{ opacity: .4; transform: scale(.96); }}
  .page-chip img {{ width: 100%; display: block; }}
  .page-chip .lbl {{ font-size: 0.62rem; text-align: center; padding: 2px 0 3px;
    color: #6b7280; background: #f9fafb; }}
  #pairs-list {{ display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }}
  .pair-row {{ display: flex; align-items: stretch; gap: 6px; background: #f8f9fb;
    border: 1.5px solid #e3e6ea; border-radius: 10px; padding: 8px 10px; }}
  .pair-num {{ font-size: 0.7rem; font-weight: 700; color: #9ca3af; width: 18px;
    padding-top: 30px; text-align: center; flex-shrink: 0; }}
  .pair-slot {{ width: 100px; min-height: 90px; border: 2px dashed #d1d5db; border-radius: 8px;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    font-size: 0.65rem; color: #9ca3af; transition: border-color .15s, background .15s;
    position: relative; overflow: hidden; flex-shrink: 0; }}
  .pair-slot.over   {{ border-color: #6366f1; background: #eef2ff; }}
  .pair-slot.filled {{ border-style: solid; border-color: #6366f1; background: #fff; }}
  .pair-slot img    {{ width: 100%; display: block; }}
  .pair-slot .slot-lbl {{ font-size: 0.6rem; color: #6b7280; text-align: center;
    padding: 2px 0 3px; width: 100%; background: #f9fafb; }}
  .pair-slot .slot-rm {{ position: absolute; top: 3px; right: 3px; width: 17px; height: 17px;
    background: #ef4444; color: #fff; border-radius: 50%; font-size: 10px; line-height: 17px;
    text-align: center; cursor: pointer; font-weight: 700; display: none; }}
  .pair-slot.filled:hover .slot-rm {{ display: block; }}
  .pair-divider {{ font-size: 1rem; color: #d1d5db; align-self: center; flex-shrink: 0; padding: 0 2px; }}
  .pair-delete {{ align-self: center; background: none; border: none; cursor: pointer;
    color: #d1d5db; font-size: 1rem; padding: 4px; border-radius: 6px;
    transition: color .15s, background .15s; margin-left: auto; flex-shrink: 0; }}
  .pair-delete:hover {{ color: #ef4444; background: #fee2e2; }}
  .btn-row {{ display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }}
  .btn {{ padding: 6px 14px; border-radius: 7px; border: 1.5px solid #d1d5db; background: #fff;
    font-size: 0.78rem; cursor: pointer; font-weight: 500; transition: border-color .15s, background .15s; }}
  .btn:hover {{ border-color: #6366f1; background: #eef2ff; color: #4f46e5; }}
  .btn-primary {{ background: #111827; color: #fff; border-color: #111827; font-weight: 600; }}
  .btn-primary:hover {{ background: #374151; border-color: #374151; color: #fff; }}
  #output {{ font-family: monospace; font-size: 0.72rem; color: #6b7280; margin-top: 4px; }}
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
const THUMBS = {thumbs_json};
const N = THUMBS.length;
let pairs = {pairs_json};
let dragSrc = null;

function render() {{ renderBank(); renderPairs(); }}

function renderBank() {{
  const bank = document.getElementById('bank');
  bank.innerHTML = '';
  for (let i = 0; i < N; i++) {{
    const chip = makeChip(i);
    addChipDrag(chip, i, 'bank', null, null);
    bank.appendChild(chip);
  }}
}}

function makeChip(pageIdx) {{
  const div = document.createElement('div');
  div.className = 'page-chip';
  div.draggable = true;
  div.dataset.page = pageIdx;
  div.innerHTML = `<img src="data:image/png;base64,${{THUMBS[pageIdx]}}" draggable="false">
    <div class="lbl">Pág. ${{pageIdx+1}}</div>`;
  return div;
}}

function renderPairs() {{
  const list = document.getElementById('pairs-list');
  list.innerHTML = '';
  pairs.forEach((p, pi) => {{
    const row = document.createElement('div');
    row.className = 'pair-row';
    row.innerHTML = `<div class="pair-num">${{pi+1}}</div>
      ${{slotHTML(pi,'L')}} <div class="pair-divider">↔</div> ${{slotHTML(pi,'R')}}
      <button class="pair-delete" onclick="removePair(${{pi}})">✕</button>`;
    list.appendChild(row);
  }});
  document.querySelectorAll('.pair-slot').forEach(slot => {{
    slot.addEventListener('dragover', e => {{ e.preventDefault(); slot.classList.add('over'); }});
    slot.addEventListener('dragleave', () => slot.classList.remove('over'));
    slot.addEventListener('drop', e => {{
      e.preventDefault(); slot.classList.remove('over');
      if (dragSrc === null) return;
      const pi = parseInt(slot.dataset.pair), side = slot.dataset.side;
      if (dragSrc.origin === 'slot') pairs[dragSrc.pairIdx][dragSrc.side==='L'?0:1] = -1;
      pairs[pi][side==='L'?0:1] = dragSrc.pageIdx;
      dragSrc = null; render();
    }});
  }});
  document.querySelectorAll('.pair-slot.filled').forEach(slot => {{
    const pi = parseInt(slot.dataset.pair), side = slot.dataset.side;
    const pgIdx = pairs[pi][side==='L'?0:1];
    slot.setAttribute('draggable','true');
    slot.addEventListener('dragstart', e => {{
      dragSrc = {{pageIdx:pgIdx, origin:'slot', pairIdx:pi, side}};
      slot.classList.add('dragging');
    }});
    slot.addEventListener('dragend', () => slot.classList.remove('dragging'));
  }});
}}

function slotHTML(pi, side) {{
  const pageIdx = side==='L' ? pairs[pi][0] : pairs[pi][1];
  const filled = pageIdx >= 0;
  return `<div class="pair-slot ${{filled?'filled':''}}" data-pair="${{pi}}" data-side="${{side}}">
    ${{filled ? `<img src="data:image/png;base64,${{THUMBS[pageIdx]}}" draggable="false">
      <div class="slot-lbl">Pág. ${{pageIdx+1}}</div>
      <div class="slot-rm" onclick="clearSlot(${{pi}},'${{side}}')">✕</div>`
      : `<span>${{side==='L'?'Esquerda':'Direita'}}</span>`}}
  </div>`;
}}

function addChipDrag(chip, pageIdx, origin, pairIdx, side) {{
  chip.addEventListener('dragstart', e => {{
    dragSrc = {{pageIdx, origin, pairIdx, side}}; chip.classList.add('dragging');
  }});
  chip.addEventListener('dragend', () => chip.classList.remove('dragging'));
}}

function clearSlot(pi, side) {{ pairs[pi][side==='L'?0:1]=-1; render(); }}
function removePair(pi) {{ pairs.splice(pi,1); render(); }}
function addPair() {{ pairs.push([0,-1]); render(); }}
function resetPairs() {{
  pairs=[];
  for(let i=0;i<Math.ceil(N/2);i++) pairs.push([i*2, i*2+1<N?i*2+1:-1]);
  render();
}}
function emitPairs() {{
  const invalid = pairs.some(p=>p[0]<0);
  if(invalid) {{ document.getElementById('output').textContent='⚠️ Todos os pares precisam de uma página à esquerda.'; return; }}
  document.getElementById('output').textContent='✓ Pares prontos — confirme abaixo e clique Gerar ficheiro.';
  window.parent.postMessage({{type:'streamlit:setComponentValue', value: JSON.stringify(pairs)}}, '*');
}}
render();
</script>
</body>
</html>
"""

        components.html(html_component, height=max(420, n_arr * 18 + 280), scrolling=True)

        st.divider()
        st.markdown("**Confirmar pares e gerar**")
        st.caption("Clique 🚀 Gerar no editor acima, depois clique **Gerar ficheiro** abaixo.")

        pairs_json_edit = st.text_area(
            "Pares (JSON)", value=json.dumps(saved_pairs),
            height=68, key=f"pairs_json_{cache_key}",
        )

        try:
            edited_pairs = json.loads(pairs_json_edit)
            assert isinstance(edited_pairs, list) and all(
                isinstance(p, list) and len(p) == 2 for p in edited_pairs)
            st.session_state[cache_key + "_pairs"] = edited_pairs
        except Exception:
            st.warning("JSON inválido.", icon="⚠️")
            edited_pairs = saved_pairs

        bc1, _ = st.columns([1, 3])
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
            valid = [p for p in edited_pairs if isinstance(p, list) and len(p)==2 and p[0]>=0]
            if not valid:
                st.error("Não há pares válidos.", icon="❌")
            else:
                try:
                    pairs_tuples = [(p[0], p[1] if p[1]>=0 else None) for p in valid]
                    with fitz.open(stream=arr_bytes, filetype="pdf") as doc:
                        out_bytes, mime, ext, merged, overflow = process_pairs(pairs_tuples, doc, OPTS)
                    base  = arr_file.name.rsplit(".", 1)[0]
                    fname = f"{base}_arranjo.{ext}"
                    st.session_state[cache_key + "_result"] = dict(
                        out_bytes=out_bytes, mime=mime, ext=ext, fname=fname,
                        n_pages=n_arr, n_pairs=len(merged), overflow=overflow,
                        preview_bytes=[make_preview(m, preview_width, False) for m in merged],
                    )
                except Exception as e:
                    st.error(f"{e}", icon="❌")

        res = st.session_state.get(cache_key + "_result")
        if res:
            show_result(res["out_bytes"], res["mime"], res["ext"],
                        res["fname"], res["n_pages"], res["n_pairs"], dpi,
                        overflow=res.get("overflow", False))
            if len(res["preview_bytes"]) == 1:
                st.image(res["preview_bytes"][0])
            else:
                cols = st.columns(min(len(res["preview_bytes"]), 3))
                for idx, pb in enumerate(res["preview_bytes"]):
                    with cols[idx % 3]:
                        st.image(pb, caption=f"Pág. {idx + 1}")
            show_download(res["out_bytes"], res["mime"], res["fname"], "arr_download")
