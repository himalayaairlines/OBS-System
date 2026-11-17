"""The QR menu as one PDF a passenger can keep on the phone for the flight.

The printed posters come first, one per page, exactly as the crew hang them;
then every section and item with its price, in English and Chinese, so the
file is still complete when a poster is missing or out of date.
"""

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

GREEN = colors.HexColor("#2E6B3E")
GOLD = colors.HexColor("#B4862A")
RED = colors.HexColor("#D4452B")
MUTED = colors.HexColor("#6B6F66")
LINE = colors.HexColor("#E6DEC9")


CJK_FONTS = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",       # Debian: fonts-wqy-microhei (installed in the Dockerfile)
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",   # a Windows server
)


def _font() -> tuple[str, bool]:
    """A font embedded in the file so Chinese shows on every phone. Returns
    (font name, has Chinese). Without a CJK font the list is English only."""
    for path in CJK_FONTS:
        if Path(path).is_file():
            try:
                pdfmetrics.registerFont(TTFont("MenuCJK", path, subfontIndex=0))
                return "MenuCJK", True
            except Exception:  # noqa: BLE001 - try the next one
                continue
    return "Helvetica", False


def _fit(path: Path, max_w: float, max_h: float) -> Image:
    reader = ImageReader(str(path))
    w, h = reader.getSize()
    scale = min(max_w / w, max_h / h)
    return Image(str(path), width=w * scale, height=h * scale)


def build_menu_pdf(data: dict, media_dir: Path) -> bytes:
    """`data` is the public menu payload: settings, sections (with items), posters."""
    font, cjk = _font()
    settings = data.get("settings") or {}
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=settings.get("title_en") or "Onboard Menu", author="Himalaya Airlines")
    page_w = A4[0] - 28 * mm
    page_h = A4[1] - 28 * mm

    h1 = ParagraphStyle("h1", fontName=font, fontSize=22, leading=26, textColor=GREEN)
    sub = ParagraphStyle("sub", fontName=font, fontSize=10.5, leading=14, textColor=MUTED)
    h2 = ParagraphStyle("h2", fontName=font, fontSize=14, leading=18, textColor=GREEN, spaceBefore=10, spaceAfter=4)
    h2_offer = ParagraphStyle("h2o", parent=h2, textColor=RED)
    cell = ParagraphStyle("cell", fontName=font, fontSize=9.5, leading=12)
    cell_zh = ParagraphStyle("cellzh", fontName=font, fontSize=8.5, leading=11, textColor=MUTED)
    price_style = ParagraphStyle("price", fontName=font, fontSize=10, leading=12, textColor=GOLD, alignment=2)
    foot = ParagraphStyle("foot", fontName=font, fontSize=8, leading=10.5, textColor=MUTED)

    story = []
    # 1. The posters, one per page.
    for poster in data.get("posters") or []:
        path = media_dir / str(poster.get("image") or "")
        if path.is_file():
            story.append(_fit(path, page_w, page_h))
            story.append(PageBreak())

    # 2. The itemised menu.
    story.append(Paragraph(escape(settings.get("title_en") or "Onboard Menu")
                           + (f" · {escape(settings['title_zh'])}" if cjk and settings.get("title_zh") else ""), h1))
    if settings.get("subtitle_en"):
        story.append(Paragraph(escape(settings["subtitle_en"]), sub))
    story.append(Spacer(1, 6))
    currency = settings.get("currency") or "NPR"

    def is_combo(s):
        return "combo" in (s.get("name_en") or "").lower() or "套餐" in (s.get("name_zh") or "")

    def is_offer(s):
        return not is_combo(s) and any(k in ((s.get("name_en") or "") + (s.get("name_zh") or "")).lower()
                                       for k in ("offer", "dhamaka", "优惠", "特惠"))

    sections = [s for s in data.get("sections") or [] if s.get("items")]
    ordered = [s for s in sections if is_offer(s)] + [s for s in sections if not is_offer(s) and not is_combo(s)] \
        + [s for s in sections if is_combo(s)]
    for section in ordered:
        name = escape(section.get("name_en") or "")
        if cjk and section.get("name_zh"):
            name += f" · {escape(section['name_zh'])}"
        story.append(Paragraph(name, h2_offer if is_offer(section) else h2))
        rows = []
        for item in section["items"]:
            label = Paragraph(escape(item.get("name_en") or ""), cell)
            parts = [label]
            if cjk and item.get("name_zh"):
                parts.append(Paragraph(escape(item["name_zh"]), cell_zh))
            if item.get("desc_en"):
                parts.append(Paragraph(escape(item["desc_en"]), cell_zh))
            price = "" if item.get("price") in (None, "") else f"{currency} {float(item['price']):,.0f}"
            rows.append([parts, Paragraph(price, price_style)])
        table = Table(rows, colWidths=[page_w - 34 * mm, 34 * mm])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(table)

    footer = settings.get("footer_en") or ""
    if footer:
        story.append(Spacer(1, 10))
        for line in footer.split("\n"):
            if line.strip():
                story.append(Paragraph("• " + escape(line.strip().lstrip("-•* ")), foot))
    doc.build(story)
    return buffer.getvalue()
