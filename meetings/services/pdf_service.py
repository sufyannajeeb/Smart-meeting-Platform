import io
import logging
import os
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

logger = logging.getLogger(__name__)

_UNICODE_FONT_NAME = None


def _find_unicode_font() -> str | None:
    candidates = [
        "C:\\Windows\\Fonts\\Nirmala.ttf",
        "C:\\Windows\\Fonts\\NirmalaUI.ttf",
        "C:\\Windows\\Fonts\\NirmalaB.ttf",
        "C:\\Windows\\Fonts\\gadal.ttf",
        "C:\\Windows\\Fonts\\Gadugi.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _register_unicode_font() -> str:
    global _UNICODE_FONT_NAME
    if _UNICODE_FONT_NAME:
        return _UNICODE_FONT_NAME

    font_path = _find_unicode_font()
    if font_path:
        try:
            family = os.path.splitext(os.path.basename(font_path))[0]
            pdfmetrics.registerFont(TTFont(family, font_path))
            _UNICODE_FONT_NAME = family
            logger.info("Registered Unicode font: %s (%s)", family, font_path)
            return family
        except Exception as exc:
            logger.warning("Failed to register font %s: %s", font_path, exc)
    return "Helvetica"


def build_transcript_pdf(
    title: str,
    room_code: str,
    host_name: str,
    language_label: str,
    transcript_text: str,
    meeting_date: datetime | None = None,
) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=50, leftMargin=50, topMargin=50, bottomMargin=50)

    font_name = _register_unicode_font()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=18, spaceAfter=12, fontName="Helvetica")
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"], fontSize=10, textColor="#555555", spaceAfter=6, fontName="Helvetica")
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=11, leading=16, spaceAfter=8, fontName=font_name)

    story = []
    story.append(Paragraph("SmartMeet AI — Meeting Transcript", title_style))
    story.append(Paragraph(f"<b>Meeting:</b> {title}", meta_style))
    story.append(Paragraph(f"<b>Room code:</b> {room_code}", meta_style))
    story.append(Paragraph(f"<b>Host:</b> {host_name}", meta_style))
    if meeting_date:
        story.append(Paragraph(f"<b>Date:</b> {meeting_date.strftime('%d %b %Y, %I:%M %p')}", meta_style))
    story.append(Paragraph(f"<b>Language:</b> {language_label}", meta_style))
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("<b>Transcript</b>", styles["Heading2"]))
    story.append(Spacer(1, 0.1 * inch))

    for paragraph in transcript_text.split("\n"):
        paragraph = paragraph.strip()
        if paragraph:
            safe = paragraph.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, body_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()
