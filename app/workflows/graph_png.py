from __future__ import annotations

from io import BytesIO
from textwrap import wrap

# A tiny valid PNG used only if Pillow is unavailable at runtime.
_MINIMAL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8ffff3f0005fe02fe0dcdb2b60000000049454e44ae426082"
)


def render_workflow_graph_png(title: str, nodes: list[str]) -> bytes:
    """Render an offline PNG summary for workflow graph endpoints."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return _MINIMAL_PNG

    width = 1100
    margin = 52
    title_h = 74
    box_h = 72
    gap = 34
    height = title_h + margin + len(nodes) * box_h + max(0, len(nodes) - 1) * gap + margin

    bg = (248, 250, 252)
    ink = (17, 24, 39)
    muted = (75, 85, 99)
    box_fill = (255, 255, 255)
    box_outline = (51, 65, 85)
    accent = (14, 116, 144)
    arrow = (71, 85, 105)

    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=28)
    label_font = ImageFont.load_default(size=20)
    small_font = ImageFont.load_default(size=16)

    draw.text((margin, 28), title, fill=ink, font=title_font)
    draw.text((margin, 60), "Offline topology preview", fill=muted, font=small_font)

    box_x = 235
    box_w = width - box_x - margin
    y = title_h + margin

    for index, node in enumerate(nodes, start=1):
        draw.ellipse((margin, y + 18, margin + 38, y + 56), fill=accent, outline=accent)
        num = str(index)
        bbox = draw.textbbox((0, 0), num, font=small_font)
        draw.text(
            (margin + 19 - (bbox[2] - bbox[0]) / 2, y + 37 - (bbox[3] - bbox[1]) / 2),
            num,
            fill=(255, 255, 255),
            font=small_font,
        )

        draw.rounded_rectangle(
            (box_x, y, box_x + box_w, y + box_h),
            radius=8,
            fill=box_fill,
            outline=box_outline,
            width=2,
        )

        wrapped = wrap(node, width=72) or [node]
        text_y = y + 14 if len(wrapped) > 1 else y + 24
        for line in wrapped[:2]:
            draw.text((box_x + 24, text_y), line, fill=ink, font=label_font)
            text_y += 24

        if index < len(nodes):
            x = box_x + box_w // 2
            y1 = y + box_h + 5
            y2 = y + box_h + gap - 6
            draw.line((x, y1, x, y2), fill=arrow, width=3)
            draw.polygon([(x - 7, y2 - 2), (x + 7, y2 - 2), (x, y2 + 10)], fill=arrow)
        y += box_h + gap

    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
