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


def images_to_pdf_bytes(images: list, dpi: int = 300) -> bytes:
    """
    Converte imagens para PDF com dimensões físicas correctas.
    A página é definida em pontos (pt) com base no DPI de rasterização,
    para que a impressora não tente reescalar.
    72 pt = 1 polegada → page_width_pt = pixel_width * 72 / dpi
    """
    out_doc = fitz.open()
    for img in images:
        w_px, h_px = img.size
        w_pt = w_px * 72.0 / dpi
        h_pt = h_px * 72.0 / dpi
        page = out_doc.new_page(width=w_pt, height=h_pt)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        buf.seek(0)
        page.insert_image(page.rect, stream=buf.read())

    out_doc.set_metadata({
        "creator": "PDF Side-by-Side",
        "producer": "PyMuPDF",
    })
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
    card_w_cm: float = 13.0,
    card_h_cm: float = 20.5,
    img_scale: float = 1.0,
    dpi: int = 300,
    mark_len_cm: float = 0.4,
    mark_offset_cm: float = 0.15,
    mark_thick_px: int = 3,
    mark_color=(0, 0, 0),
    bg=(255, 255, 255),
    offset_left=(0.0, 0.0),
    offset_right=(0.0, 0.0),
) -> tuple:
    """
    Lógica:
    - O canvas é sempre A4 paisagem (29.7 × 21 cm).
    - Cada carta ocupa metade do canvas.
    - A imagem é escalada por img_scale relativamente à metade.
    - As marcas de corte são desenhadas numa área card_w_cm × card_h_cm
      centrada em cada metade.
    - Os riscos do meio (topo/base) ficam centrados na metade física do A4,
      que é o local real de corte ao meio.
    """
    def cm2px(cm):
        return int(round(cm * dpi / 2.54))

    a4_w = cm2px(29.7)
    a4_h = cm2px(21.0)
    half_w = a4_w // 2

    cw = cm2px(card_w_cm)
    ch = cm2px(card_h_cm)
    ml = cm2px(mark_len_cm)
    mo = cm2px(mark_offset_cm)
    t = mark_thick_px

    canvas = Image.new("RGB", (a4_w, a4_h), bg)

    def place_card(img, half_x_start, offset_cm):
        ox_px = cm2px(offset_cm[0])
        oy_px = cm2px(offset_cm[1])

        target_w = max(1, int(half_w * img_scale))
        target_h = max(1, int(a4_h * img_scale))

        r = img.width / img.height
        tr = target_w / target_h

        if r > tr:
            nw, nh = target_w, max(1, round(target_w / r))
        else:
            nw, nh = max(1, round(target_h * r)), target_h

        img_s = img.resize((nw, nh), Image.LANCZOS)

        px = half_x_start + (half_w - nw) // 2 + ox_px
        py = (a4_h - nh) // 2 + oy_px

        if nw > half_w or nh > a4_h:
            src_x = max(0, half_x_start - px)
            src_y = max(0, -py)
            src_x2 = src_x + half_w
            src_y2 = src_y + a4_h
            img_s = img_s.crop((
                src_x,
                src_y,
                min(src_x2, img_s.width),
                min(src_y2, img_s.height),
            ))
            px = half_x_start
            py = 0

        canvas.paste(img_s, (px, py))

    place_card(left, 0, offset_left)
    place_card(right, half_w, offset_right)

    mark_x_margin = (half_w - cw) // 2
    mark_y_margin = (a4_h - ch) // 2

    draw = ImageDraw.Draw(canvas)

    def dash_h(cx, cy):
        x0 = cx - ml // 2
        x1 = cx + ml // 2
        y0 = cy - t // 2
        y1 = cy + t // 2
        draw.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], fill=mark_color)

    def dash_v(cx, cy):
        x0 = cx - t // 2
        x1 = cx + t // 2
        y0 = cy - ml // 2
        y1 = cy + ml // 2
        draw.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], fill=mark_color)

    for half_x in (0, half_w):
        mx = half_x + mark_x_margin
        my = mark_y_margin
        rx = mx + cw
        by = my + ch

        # Centro real da metade da folha (onde se corta o A4 ao meio)
        mid_x = half_x + half_w // 2

        # Centro da área da carta
        mid_y = my + ch // 2

        gap = mo + ml // 2

        # Cantos
        for cx, cy, sx, sy in [
            (mx, my, -1, -1), (rx, my, +1, -1),
            (mx, by, -1, +1), (rx, by, +1, +1),
        ]:
            hx0 = cx + sx * mo
            hx1 = cx + sx * (mo + ml)
            draw.rectangle(
                [min(hx0, hx1), cy - t // 2, max(hx0, hx1), cy + t // 2],
                fill=mark_color
            )

            vy0 = cy + sy * mo
            vy1 = cy + sy * (mo + ml)
            draw.rectangle(
                [cx - t // 2, min(vy0, vy1), cx + t // 2, max(vy0, vy1)],
                fill=mark_color
            )

        # Meios dos lados da área de corte
        dash_v(mx - gap, mid_y)
        dash_v(rx + gap, mid_y)

        # Meio superior e inferior no centro real da metade do A4
        dash_h(mid_x, my - gap)
        dash_h(mid_x, by + gap)

    return canvas, False


def add_crop_marks_to_composed(
    img: Image.Image,
    card_w_cm: float = 13.0,
    card_h_cm: float = 20.5,
    dpi: int = 300,
    mark_len_cm: float = 0.4,
    mark_offset_cm: float = 0.15,
    mark_thick_px: int = 3,
    mark_color=(0, 0, 0),
) -> Image.Image:
    """
    Recebe uma imagem já composta (2 cartas lado a lado) e sobrepõe
    marcas de corte nos cantos de cada carta.
    Calcula a posição das marcas com base nas dimensões reais da imagem
    dividida ao meio, mas usa card_w_cm / card_h_cm para determinar onde
    ficam as linhas de corte centradas em cada metade.
    """
    def cm2px(cm):
        return int(round(cm * dpi / 2.54))

    W, H = img.size
    ml = cm2px(mark_len_cm)
    mo = cm2px(mark_offset_cm)
    t = mark_thick_px
    cw = cm2px(card_w_cm)
    ch = cm2px(card_h_cm)

    half_w = W // 2
    margin_x = (half_w - cw) // 2
    margin_y = (H - ch) // 2

    lx1 = margin_x
    lx2 = half_w + margin_x
    ty = margin_y
    by = margin_y + ch

    out = img.copy()
    draw = ImageDraw.Draw(out)

    def L_mark(cx, cy, dx, dy):
        hx0, hx1 = cx + dx * mo, cx + dx * (mo + ml)
        draw.rectangle(
            [min(hx0, hx1), cy - t // 2, max(hx0, hx1), cy + t // 2],
            fill=mark_color
        )
        vy0, vy1 = cy + dy * mo, cy + dy * (mo + ml)
        draw.rectangle(
            [cx - t // 2, min(vy0, vy1), cx + t // 2, max(vy0, vy1)],
            fill=mark_color
        )

    def dash_h(cx, cy):
        x0 = cx - ml // 2
        x1 = cx + ml // 2
        y0 = cy - t // 2
        y1 = cy + t // 2
        draw.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], fill=mark_color)

    def dash_v(cx, cy):
        x0 = cx - t // 2
        x1 = cx + t // 2
        y0 = cy - ml // 2
        y1 = cy + ml // 2
        draw.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], fill=mark_color)

    for ox in (lx1, lx2):
        rx_ = ox + cw
        mid_y = ty + ch // 2
        half_start = 0 if ox == lx1 else half_w
        mid_x = half_start + half_w // 2
        gap = mo + ml // 2

        L_mark(ox,  ty, -1, -1)
        L_mark(rx_, ty, +1, -1)
        L_mark(ox,  by, -1, +1)
        L_mark(rx_, by, +1, +1)

        dash_v(ox - gap, mid_y)
        dash_v(rx_ + gap, mid_y)
        dash_h(mid_x, ty - gap)
        dash_h(mid_x, by + gap)

    return out


def combine_for_duplex_crop(raw_left: list, raw_right: list, opts: dict) -> list:
    """
    Modo duplex COM marcas de corte.
    raw_left[i] / raw_right[i] são as faces frente/verso do par i.
    Cada A4 físico comporta 2 pares:
      Frente do A4: par[i]_frente (esq) + par[i+1]_frente (dir)
      Verso  do A4: par[i+1]_verso (esq) + par[i]_verso   (dir)
    """
    bg = opts.get("bg", (255, 255, 255))
    dpi = opts["dpi"]
    crop_w = opts["crop_w"]
    crop_h = opts["crop_h"]
    img_scale = opts.get("img_scale", 1.0)
    marklen = opts["crop_marklen"]
    n = len(raw_left)

    def make_a4(left_img, right_img):
        img, _ = fit_two_cards_on_a4(
            left_img, right_img,
            card_w_cm=crop_w, card_h_cm=crop_h,
            img_scale=img_scale, dpi=dpi,
            mark_len_cm=marklen, mark_offset_cm=0.15, bg=bg,
        )
        return img

    def blank(ref):
        return Image.new("RGB", ref.size, bg)

    result = []
    i = 0
    while i < n:
        pA_f = raw_left[i]
        pA_v = raw_right[i]
        if i + 1 < n:
            pB_f = raw_left[i + 1]
            pB_v = raw_right[i + 1]
        else:
            pB_f = blank(pA_f)
            pB_v = blank(pA_v)

        frente = make_a4(pA_f, pB_f)
        verso  = make_a4(pB_v, pA_v)
        result += [frente, verso]
        i += 2
    return result


def combine_for_duplex_simple(raw_left: list, raw_right: list, opts: dict) -> list:
    """
    Modo duplex SEM marcas de corte.
    Mesma lógica de pares: 2 pares por A4, frente/verso espelhados.
    """
    bg = opts.get("bg", (255, 255, 255))
    align_by = opts.get("align_by", "height")
    gap = opts.get("gap_px", 0)
    n = len(raw_left)

    def blank(ref):
        return Image.new("RGB", ref.size, bg)

    result = []
    i = 0
    while i < n:
        pA_f = raw_left[i]
        pA_v = raw_right[i]
        if i + 1 < n:
            pB_f = raw_left[i + 1]
            pB_v = raw_right[i + 1]
        else:
            pB_f = blank(pA_f)
            pB_v = blank(pA_v)

        frente = merge_side_by_side(pA_f, pB_f, align_by=align_by, gap_px=gap, bg=bg)
        verso  = merge_side_by_side(pB_v, pA_v, align_by=align_by, gap_px=gap, bg=bg)
        result += [frente, verso]
        i += 2
    return result


# ─────────────────────────────────────────────
# Processadores de alto nível
# ─────────────────────────────────────────────

def process_pairs(pairs_indices: list, doc: fitz.Document, opts: dict):
    dpi, fmt = opts["dpi"], opts["fmt"]
    align_by, gap = opts["align_by"], opts["gap_px"]
    bg, sharpen = opts["bg"], opts["sharpen"]
    do_crop = opts.get("crop_marks", False)
    do_duplex = opts.get("duplex", False)
    n_pairs = len(pairs_indices)
    progress = st.progress(0, text="A rasterizar páginas…")

    raw_left = []
    raw_right = []

    for i, (li, ri) in enumerate(pairs_indices):
        left = render_page(doc.load_page(li), dpi, bg)
        right = render_page(doc.load_page(ri), dpi, bg) if ri is not None else Image.new("RGB", left.size, bg)
        if sharpen:
            left = apply_sharpen(left)
            right = apply_sharpen(right)
        raw_left.append(left)
        raw_right.append(right)
        progress.progress((i + 1) / n_pairs * 0.5, text=f"Rasterizar {i + 1}/{n_pairs}…")

    progress.empty()

    if do_duplex:
        if do_crop:
            merged_images = combine_for_duplex_crop(raw_left, raw_right, opts)
        else:
            merged_images = combine_for_duplex_simple(raw_left, raw_right, opts)
    else:
        merged_images = []
        had_overflow = False
        for i, (left, right) in enumerate(zip(raw_left, raw_right)):
            if do_crop:
                merged, overflow = fit_two_cards_on_a4(
                    left, right,
                    card_w_cm=opts["crop_w"], card_h_cm=opts["crop_h"],
                    img_scale=opts.get("img_scale", 1.0),
                    dpi=dpi,
                    mark_len_cm=opts["crop_marklen"],
                    mark_offset_cm=0.15,
                    bg=bg,
                    offset_left=(opts.get("offset_lx", 0.0), opts.get("offset_ly", 0.0)),
                    offset_right=(opts.get("offset_rx", 0.0), opts.get("offset_ry", 0.0)),
                )
                if overflow:
                    had_overflow = True
            else:
                merged = merge_side_by_side(left, right, align_by=align_by, gap_px=gap, bg=bg)
                overflow = False
            merged_images.append(merged)

        if len(merged_images) == 1:
            out = encode_image(merged_images[0], fmt)
            ext = "png" if fmt == "PNG" else "jpg"
            mime = "image/png" if fmt == "PNG" else "image/jpeg"
        else:
            out = images_to_pdf_bytes(merged_images, dpi)
            ext = "pdf"
            mime = "application/pdf"
        return out, mime, ext, merged_images, had_overflow

    had_overflow = False
    if len(merged_images) == 1:
        out = encode_image(merged_images[0], fmt)
        ext = "png" if fmt == "PNG" else "jpg"
        mime = "image/png" if fmt == "PNG" else "image/jpeg"
    else:
        out = images_to_pdf_bytes(merged_images, dpi)
        ext = "pdf"
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
    dpi, fmt = opts["dpi"], opts["fmt"]
    align_by, gap = opts["align_by"], opts["gap_px"]
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
    progress.progress(0.9, text="A juntar…")

    if sharpen:
        left = apply_sharpen(left)
        right = apply_sharpen(right)

    overflow = False
    if opts.get("crop_marks", False):
        merged, overflow = fit_two_cards_on_a4(
            left, right,
            card_w_cm=opts["crop_w"], card_h_cm=opts["crop_h"],
            img_scale=opts.get("img_scale", 1.0),
            dpi=dpi, mark_len_cm=opts["crop_marklen"], mark_offset_cm=0.15, bg=bg,
            offset_left=(opts.get("offset_lx", 0.0), opts.get("offset_ly", 0.0)),
            offset_right=(opts.get("offset_rx", 0.0), opts.get("offset_ry", 0.0)),
        )
    else:
        merged = merge_side_by_side(left, right, align_by=align_by, gap_px=gap, bg=bg)
    progress.empty()

    out = encode_image(merged, fmt)
    ext = "png" if fmt == "PNG" else "jpg"
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return out, mime, ext, merged, overflow


# ─────────────────────────────────────────────
# Helpers de UI
# ─────────────────────────────────────────────

def show_result(out_bytes, mime, ext, fname, n_pages, pairs_count, dpi, overflow=False):
    size_kb = len(out_bytes) / 1024
    size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
    badge = {"pdf": "badge-pdf", "png": "badge-png", "jpg": "badge-jpg"}[ext]
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
        st.session_state["crop_w"] = 13.0
    if "crop_h" not in st.session_state:
        st.session_state["crop_h"] = 20.5
    if "crop_ratio" not in st.session_state:
        st.session_state["crop_ratio"] = st.session_state["crop_h"] / st.session_state["crop_w"]

def _on_crop_w_change():
    st.session_state["crop_h"] = round(st.session_state["crop_w"] * st.session_state["crop_ratio"], 1)

def _on_crop_h_change():
    st.session_state["crop_w"] = round(st.session_state["crop_h"] / st.session_state["crop_ratio"], 1)

def _on_ratio_toggle():
    st.session_state["crop_ratio"] = st.session_state["crop_h"] / max(st.session_state["crop_w"], 0.1)

_init_state()


# ─────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️  Opções")
    st.divider()
    dpi = st.slider("DPI", 72, 600, 300, 50,
                    help="Resolução de rasterização. Valores altos = mais qualidade e mais tempo.")
    if dpi > 400:
        st.caption("⚠️ DPI alto — processamento pode ser lento em PDFs com muitas páginas.")
    fmt = st.radio("Formato de saída", ["PNG", "JPG"], horizontal=True)
    align_by = st.radio("Alinhar por", ["height", "width"], horizontal=True)
    gap_px = st.slider("Espaço entre páginas (px)", 0, 200, 0, 4)
    bg_label = st.selectbox("Cor de fundo", ["Branco", "Cinza claro", "Preto"])
    BG = {"Branco": (255, 255, 255), "Cinza claro": (240, 242, 245), "Preto": (0, 0, 0)}[bg_label]
    sharpen = st.toggle("Aumentar nitidez", value=True)

    st.divider()
    st.markdown("**Impressão frente/verso**")
    duplex = st.toggle("Modo frente/verso", value=False,
                       help=(
                           "Cada par (frente/verso) ocupa metade de um A4.\n"
                           "O A4 leva 2 pares: frente=[par1 + par2], verso=[par2 + par1] (espelhado).\n"
                           "Imprime frente/verso e corta o A4 ao meio."
                       ))

    st.divider()
    st.markdown("**Marcas de corte (A4 paisagem)**")
    crop_marks = st.toggle("Activar marcas de corte", value=False,
                           help="Posiciona duas cartas num A4 paisagem com marcas de corte nos cantos.")
    if crop_marks:
        ratio_lock = st.toggle("🔒 Manter proporção", value=True, key="ratio_lock",
                               on_change=_on_ratio_toggle)
        c1, c2 = st.columns(2)
        with c1:
            st.number_input(
                "Largura (cm)", min_value=1.0, max_value=50.0,
                value=st.session_state["crop_w"],
                step=0.1, format="%.1f",
                key="crop_w",
                on_change=_on_crop_w_change if ratio_lock else None,
            )
        with c2:
            st.number_input(
                "Altura (cm)", min_value=1.0, max_value=50.0,
                value=st.session_state["crop_h"],
                step=0.1, format="%.1f",
                key="crop_h",
                on_change=_on_crop_h_change if ratio_lock else None,
            )
        crop_w = st.session_state["crop_w"]
        crop_h = st.session_state["crop_h"]

        st.markdown("**Imagem dentro do cartão**")
        st.caption(
            "Ajusta o tamanho da imagem relativamente às linhas de corte. "
            "100% = preenche exatamente. Abaixo de 100% = margem branca interior. "
            "Acima de 100% = sangria (bleed) — a imagem ultrapassa ligeiramente a linha de corte."
        )
        img_scale = st.slider(
            "Escala da imagem (%)", 40, 130, 100, 1,
            help="As linhas de corte não se movem. Só a imagem escala."
        ) / 100.0
        crop_marklen = st.slider("Comprimento das marcas (mm)", 2, 20, 6, 1) / 10

        st.markdown("**Posição das cartas**")
        st.caption("Desloca cada carta dentro da sua metade (em cm). As marcas de corte não se movem.")
        oc1, oc2 = st.columns(2)
        with oc1:
            st.markdown("<div style='font-size:0.78rem;font-weight:600'>◀ Carta esquerda ▶</div>", unsafe_allow_html=True)
            offset_lx = st.slider("← →", -3.0, 3.0, 0.0, 0.1, key="off_lx", format="%.1f cm")
            offset_ly = st.slider("↑ ↓", -3.0, 3.0, 0.0, 0.1, key="off_ly", format="%.1f cm")
        with oc2:
            st.markdown("<div style='font-size:0.78rem;font-weight:600'>◀ Carta direita ▶</div>", unsafe_allow_html=True)
            offset_rx = st.slider("← →", -3.0, 3.0, 0.0, 0.1, key="off_rx", format="%.1f cm")
            offset_ry = st.slider("↑ ↓", -3.0, 3.0, 0.0, 0.1, key="off_ry", format="%.1f cm")
    else:
        crop_w, crop_h, crop_marklen, img_scale = 13.0, 20.5, 0.4, 1.0
        offset_lx = offset_ly = offset_rx = offset_ry = 0.0

    st.divider()
    st.markdown("**Preview**")
    preview_width = st.slider("Largura máx. (px)", 400, 2000, 900, 100)
    preview_1to1 = st.toggle("Mostrar 1:1", value=False)

OPTS = dict(dpi=dpi, fmt=fmt, align_by=align_by, gap_px=gap_px, bg=BG, sharpen=sharpen,
            crop_marks=crop_marks, crop_w=crop_w, crop_h=crop_h, crop_marklen=crop_marklen,
            img_scale=img_scale, duplex=duplex,
            offset_lx=offset_lx, offset_ly=offset_ly,
            offset_rx=offset_rx, offset_ry=offset_ry)


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
tab_normal, tab_dual, tab_arrange, tab_marks = st.tabs([
    "📄  Modo normal",
    "🔀  Modo dual",
    "🎛️  Modo arranjo",
    "✂️  Adicionar marcas",
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
            fbytes_key = f"normal_bytes_{f.name}_{f.size}"
            if fbytes_key not in st.session_state:
                st.session_state[fbytes_key] = f.read()
            pdf_bytes = st.session_state[fbytes_key]

            opts_sig = (
                f"{dpi}_{fmt}_{align_by}_{gap_px}_{bg_label}_{sharpen}_"
                f"crop{crop_marks}_{crop_w}_{crop_h}_{crop_marklen}_{img_scale}_"
                f"dup{duplex}"
            )
            fkey = f"normal_res_{f.name}_{f.size}_{opts_sig}"

            if fkey not in st.session_state:
                old_keys = [k for k in st.session_state if k.startswith(f"normal_res_{f.name}_{f.size}_")]
                for k in old_keys:
                    del st.session_state[k]
                try:
                    out_bytes, mime, ext, n_pages, merged, overflow = process_normal(pdf_bytes, OPTS)
                    base = f.name.rsplit(".", 1)[0]
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

    if file_a and file_b:
        ba_key = f"dual_bytes_a_{file_a.name}_{file_a.size}"
        bb_key = f"dual_bytes_b_{file_b.name}_{file_b.size}"
        if ba_key not in st.session_state:
            st.session_state[ba_key] = file_a.read()
        if bb_key not in st.session_state:
            st.session_state[bb_key] = file_b.read()

        opts_sig_d = (
            f"{dpi}_{fmt}_{align_by}_{gap_px}_{bg_label}_{sharpen}_"
            f"crop{crop_marks}_{crop_w}_{crop_h}_{crop_marklen}_{img_scale}_"
            f"dup{duplex}"
        )
        dkey = f"dual_res_{file_a.name}_{file_a.size}_{file_b.name}_{file_b.size}_{opts_sig_d}"

        if dkey not in st.session_state:
            old_keys = [k for k in st.session_state if k.startswith(f"dual_res_{file_a.name}_{file_a.size}_{file_b.name}_{file_b.size}_")]
            for k in old_keys:
                del st.session_state[k]
            try:
                out_bytes, mime, ext, merged_img, overflow = process_dual(
                    st.session_state[ba_key], st.session_state[bb_key], OPTS)
                name_a = file_a.name.rsplit(".", 1)[0]
                name_b = file_b.name.rsplit(".", 1)[0]
                fname = f"{name_a}+{name_b}.{ext}"
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
                n_arr = _doc.page_count
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
            st.session_state[cache_key + "_n"] = n_arr
            st.session_state[cache_key + "_b64"] = thumbs_b64
            st.session_state[cache_key + "_bytes"] = arr_bytes
            st.session_state[cache_key + "_pairs"] = default_pairs

        n_arr = st.session_state[cache_key + "_n"]
        thumbs_b64 = st.session_state[cache_key + "_b64"]
        arr_bytes = st.session_state[cache_key + "_bytes"]
        saved_pairs = st.session_state[cache_key + "_pairs"]

        thumbs_json = json.dumps(thumbs_b64)
        pairs_json = json.dumps(saved_pairs)

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
function addPair() {{ pairs.push([-1,-1]); render(); }}
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
            valid = [p for p in edited_pairs if isinstance(p, list) and len(p) == 2 and p[0] >= 0]
            if not valid:
                st.error("Não há pares válidos.", icon="❌")
            else:
                try:
                    pairs_tuples = [(p[0], p[1] if p[1] >= 0 else None) for p in valid]
                    with fitz.open(stream=arr_bytes, filetype="pdf") as doc:
                        out_bytes, mime, ext, merged, overflow = process_pairs(pairs_tuples, doc, OPTS)
                    base = arr_file.name.rsplit(".", 1)[0]
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


# ══════════════════════════════════════════════
# Tab Marcas — PDF já composto
# ══════════════════════════════════════════════
with tab_marks:
    st.markdown(
        "Carregue um PDF **já com as duas cartas lado a lado** (gerado pelo Modo normal). "
        "O script **sobrepõe marcas de corte** nas posições corretas sem alterar a imagem."
    )
    st.info(
        "As marcas são centradas em cada metade da página com base nas dimensões "
        "**Largura × Altura** definidas na sidebar (padrão 13 × 20,5 cm).",
        icon="✂️"
    )

    marks_file = st.file_uploader("Escolher PDF composto", type=["pdf"], key="marks_up")

    if not marks_file:
        st.info("⬆️  Carregue o PDF gerado pelo Modo normal.", icon="📂")
    else:
        mbytes_key = f"marks_bytes_{marks_file.name}_{marks_file.size}"
        if mbytes_key not in st.session_state:
            st.session_state[mbytes_key] = marks_file.read()
        pdf_bytes_m = st.session_state[mbytes_key]

        opts_sig_m = f"{dpi}_{crop_w}_{crop_h}_{crop_marklen}"
        mkey = f"marks_res_{marks_file.name}_{marks_file.size}_{opts_sig_m}"

        if mkey not in st.session_state:
            old_keys = [k for k in st.session_state if k.startswith(f"marks_res_{marks_file.name}_{marks_file.size}_")]
            for k in old_keys:
                del st.session_state[k]
            try:
                pdf_bytes_m = _preprocess_pdf(pdf_bytes_m)
                progress_m = st.progress(0, text="A processar…")
                result_imgs = []
                with fitz.open(stream=pdf_bytes_m, filetype="pdf") as doc_m:
                    n_m = doc_m.page_count
                    for pi in range(n_m):
                        page_img = render_page(doc_m.load_page(pi), dpi, (255, 255, 255))
                        marked = add_crop_marks_to_composed(
                            page_img,
                            card_w_cm=crop_w, card_h_cm=crop_h,
                            dpi=dpi,
                            mark_len_cm=crop_marklen,
                        )
                        result_imgs.append(marked)
                        progress_m.progress((pi + 1) / n_m, text=f"Página {pi+1}/{n_m}…")
                progress_m.empty()

                if len(result_imgs) == 1:
                    out_m = encode_image(result_imgs[0], "PNG")
                    ext_m, mime_m = "png", "image/png"
                else:
                    out_m = images_to_pdf_bytes(result_imgs, dpi)
                    ext_m, mime_m = "pdf", "application/pdf"

                base_m = marks_file.name.rsplit(".", 1)[0]
                fname_m = f"{base_m}_marcas.{ext_m}"
                st.session_state[mkey] = dict(
                    out_bytes=out_m, mime=mime_m, ext=ext_m, fname=fname_m,
                    n_pages=n_m,
                    preview_bytes=[make_preview(r, preview_width, False) for r in result_imgs],
                )
            except Exception as e:
                st.error(f"{e}", icon="❌")

        res = st.session_state.get(mkey)
        if res:
            size_kb = len(res["out_bytes"]) / 1024
            size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
            st.markdown(
                f'<div class="result-meta"><strong>{res["fname"]}</strong>'
                f'<span class="badge badge-pdf">{res["ext"].upper()}</span>'
                f' &nbsp;·&nbsp; {res["n_pages"]} pág. &nbsp;·&nbsp; {dpi} dpi &nbsp;·&nbsp; {size_str}</div>',
                unsafe_allow_html=True,
            )
            if len(res["preview_bytes"]) == 1:
                st.image(res["preview_bytes"][0])
            else:
                cols = st.columns(min(len(res["preview_bytes"]), 3))
                for idx, pb in enumerate(res["preview_bytes"]):
                    with cols[idx % 3]:
                        st.image(pb, caption=f"Pág. {idx + 1}")
            show_download(res["out_bytes"], res["mime"], res["fname"], "marks_download")
