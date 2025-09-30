import math
import fitz  # PyMuPDF

def draw_turnback_arrow_chip(page: fitz.Page):
    """Tiny light-gray chip with a vector ⮌ U-turn arrow, top-right, linking to cover."""
    # Chip rectangle (top-right)
    pw, ph = page.rect.width, page.rect.height
    margin_mm = 7.0
    w_mm, h_mm = 12.0, 10.0
    def mm_to_pt(mm: float) -> float: return mm * 72.0 / 25.4
    left = pw - mm_to_pt(margin_mm + w_mm)
    top  = mm_to_pt(margin_mm)
    rect = fitz.Rect(left, top, left + mm_to_pt(w_mm), top + mm_to_pt(h_mm))

    # Soft background + border
    try:
        page.draw_rect(rect, fill=(0.95, 0.96, 0.98), color=(0.88, 0.90, 0.93), width=0.5)
    except Exception:
        pass  # decoration is optional

    # Vector ⮌: quarter arc + arrow head (no fonts)
    cx = rect.x0 + rect.width * 0.55
    cy = rect.y0 + rect.height * 0.55
    r  = min(rect.width, rect.height) * 0.38
    start, end = 0.0, 3.0*math.pi/4.0

    pts = []
    steps = 10
    for i in range(steps + 1):
        t = start + (end - start) * (i / steps)
        x = cx + r * math.cos(t)
        y = cy - r * math.sin(t)  # PDF Y grows downward
        pts.append((x, y))

    # arc
    page.draw_polyline(pts, width=1.2, color=(0.40, 0.44, 0.50))

    # arrow head
    x2, y2 = pts[-1]; x1, y1 = pts[-2]
    vx, vy = (x2 - x1), (y2 - y1)
    vlen = math.hypot(vx, vy) or 1.0
    ux, uy = vx / vlen, vy / vlen
    head_len = r * 0.9
    ang = math.radians(145)
    def rot(u_x, u_y, a): return (u_x*math.cos(a) - u_y*math.sin(a), u_x*math.sin(a) + u_y*math.cos(a))
    rx1, ry1 = rot(ux, uy, +ang); rx2, ry2 = rot(ux, uy, -ang)

    page.draw_line((x2, y2), (x2 - head_len*rx1, y2 - head_len*ry1), width=1.2, color=(0.40, 0.44, 0.50))
    page.draw_line((x2, y2), (x2 - head_len*rx2, y2 - head_len*ry2), width=1.2, color=(0.40, 0.44, 0.50))

    # clickable link back to cover (page 0)
    page.insert_link({"kind": fitz.LINK_GOTO, "from": rect, "page": 0})
