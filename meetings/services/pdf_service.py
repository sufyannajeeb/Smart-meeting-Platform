"""PDF rendering for meeting transcripts.

ReportLab silently maps every codepoint that a registered TTF font does not
cover onto glyph 0 (.notdef), which renders as a black box. The previous font
lookup took the first file that merely *existed* on disk, so an Indic-language
transcript was drawn with a font that had no Devanagari, Tamil, Telugu or
Malayalam glyphs and every translated word came out as a row of boxes. Fonts
here are probed for real glyph coverage, the transcript is split into per-font
runs so nothing falls through to .notdef, and any codepoint that genuinely has
no glyph anywhere is dropped and logged instead of becoming a box.
"""

import io
import logging
import os
import re
import unicodedata
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

logger = logging.getLogger(__name__)

BASE_FONT = "Helvetica"
BASE_BOLD_FONT = "Helvetica-Bold"

FONT_DIR_ENV = "SMARTMEET_PDF_FONT_DIR"

_WIN_ANSI = (
    frozenset(range(0x20, 0x7F))
    | frozenset(range(0xA0, 0x100))
    | frozenset(
        {
            0x20AC, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030,
            0x0160, 0x2039, 0x0152, 0x017D, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022,
            0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x017E, 0x0178,
        }
    )
)

_CID_RANGES = {
    "STSong-Light": ((0x3000, 0x303F), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF)),
    "MSung-Light": ((0x3000, 0x303F), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF)),
    "HeiseiKakuGo-W5": ((0x3000, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF)),
    "HeiseiMin-W3": ((0x3000, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF)),
    "HYSMyeongJo-Medium": ((0x1100, 0x11FF), (0x3000, 0x303F), (0x3130, 0x318F), (0xAC00, 0xD7AF), (0xFF00, 0xFFEF)),
}

_FONT_SOURCES = (
    (
        (
            (r"C:\Windows\Fonts\segoeui.ttf", 0),
            (r"C:\Windows\Fonts\arial.ttf", 0),
            (r"C:\Windows\Fonts\tahoma.ttf", 0),
            (r"C:\Windows\Fonts\micross.ttf", 0),
            (r"C:\Windows\Fonts\verdana.ttf", 0),
            ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", 0),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
        ),
        (
            (r"C:\Windows\Fonts\segoeuib.ttf", 0),
            (r"C:\Windows\Fonts\arialbd.ttf", 0),
            (r"C:\Windows\Fonts\tahomabd.ttf", 0),
            (r"C:\Windows\Fonts\micross.ttf", 0),
            (r"C:\Windows\Fonts\verdanab.ttf", 0),
            ("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", 0),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
        ),
    ),
    (
        (
            (r"C:\Windows\Fonts\Nirmala.ttc", 0),
            (r"C:\Windows\Fonts\NirmalaUI.ttf", 0),
            (r"C:\Windows\Fonts\NirmalaB.ttf", 0),
        ),
        (
            (r"C:\Windows\Fonts\Nirmala.ttc", 1),
            (r"C:\Windows\Fonts\NirmalaB.ttf", 0),
            (r"C:\Windows\Fonts\NirmalaB.ttf", 0),
        ),
    ),
    (
        (
            (r"C:\Windows\Fonts\msyh.ttc", 0),
            (r"C:\Windows\Fonts\simhei.ttf", 0),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ),
        (
            (r"C:\Windows\Fonts\msyhbd.ttc", 0),
            (r"C:\Windows\Fonts\simhei.ttf", 0),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
        ),
    ),
    (
        (
            (r"C:\Windows\Fonts\msjh.ttc", 0),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ),
        (
            (r"C:\Windows\Fonts\msjhbd.ttc", 0),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
        ),
    ),
    (
        (
            (r"C:\Windows\Fonts\msgothic.ttc", 0),
            ("/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf", 0),
        ),
        (
            (r"C:\Windows\Fonts\msgothic.ttc", 0),
            ("/usr/share/fonts/truetype/noto/NotoSansJP-Bold.ttf", 0),
        ),
    ),
    (
        (
            (r"C:\Windows\Fonts\malgun.ttf", 0),
            (r"C:\Windows\Fonts\YuGothR.ttc", 0),
            ("/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf", 0),
        ),
        (
            (r"C:\Windows\Fonts\malgunbd.ttf", 0),
            (r"C:\Windows\Fonts\YuGothB.ttc", 0),
            ("/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf", 0),
        ),
    ),
    (
        (
            (r"C:\Windows\Fonts\Nirmala.ttf", 0),
            (r"C:\Windows\Fonts\NirmalaUI.ttf", 0),
            ("/usr/share/fonts/truetype/noto/NotoSansMalayalam-Regular.ttf", 0),
            (os.path.join("static", "fonts", "NotoSansMalayalam-Regular.ttf"), 0),
            (os.path.join("static", "fonts", "Manjari-Regular.ttf"), 0),
        ),
        (
            (r"C:\Windows\Fonts\NirmalaB.ttf", 0),
            (r"C:\Windows\Fonts\NirmalaUIB.ttf", 0),
            ("/usr/share/fonts/truetype/noto/NotoSansMalayalam-Bold.ttf", 0),
            (os.path.join("static", "fonts", "NotoSansMalayalam-Bold.ttf"), 0),
            (os.path.join("static", "fonts", "Manjari-Bold.ttf"), 0),
        ),
    ),
)

_LINUX_SCRIPT_FONTS = (
    "NotoSansDevanagari",
    "NotoSansBengali",
    "NotoSansTamil",
    "NotoSansTelugu",
    "NotoSansKannada",
    "NotoSansMalayalam",
    "NotoSansGurmukhi",
    "NotoSansGujarati",
    "NotoNaskhArabic",
    "NotoSansArabic",
    "NotoSansHebrew",
    "NotoSansThai",
)

_LINUX_FONT_DIRS = (
    "/usr/share/fonts/truetype/noto",
    "/usr/share/fonts/TTF",
    "/usr/local/share/fonts",
)

_CID_FONTS = ("STSong-Light", "MSung-Light", "HeiseiKakuGo-W5", "HeiseiMin-W3", "HYSMyeongJo-Medium")

_FONT_COVERAGE: dict[str, frozenset[int]] | None = None
_FONT_ORDER: tuple[str, ...] = ()

_WHITESPACE_RE = re.compile(r"[^\S\n]+")
_DROP_CATEGORIES = frozenset({"Cf", "Co", "Cs", "Cn", "Zl", "Zp"})


def _extra_font_paths() -> list[str]:
    raw = os.getenv(FONT_DIR_ENV, "").strip()
    if not raw or not os.path.isdir(raw):
        return []
    found = []
    try:
        for entry in sorted(os.listdir(raw)):
            if entry.lower().endswith((".ttf", ".ttc", ".otf")):
                found.append(os.path.join(raw, entry))
    except OSError as exc:
        logger.warning("Could not read %s: %s", FONT_DIR_ENV, exc)
    return found


def _register_candidates(candidates, registered: dict[tuple[str, int], str], taken: set[str]) -> list[str | None]:
    names: list[str | None] = []
    for path, subfont_index in candidates:
        key = (path, subfont_index)
        if key in registered:
            names.append(registered[key])
            continue
        if not os.path.isfile(path):
            names.append(None)
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        name = stem if subfont_index == 0 else f"{stem}-{subfont_index}"
        while name in taken:
            name = f"{name}_"
        try:
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=subfont_index))
        except Exception as exc:
            logger.warning("Skipping font %s (%s): %s", name, path, exc)
            names.append(None)
            continue
        taken.add(name)
        registered[key] = name
        names.append(name)
    return names


def _glyph_codepoints(font_name: str) -> frozenset[int]:
    font = pdfmetrics.getFont(font_name)
    face = getattr(font, "face", None)
    char_to_glyph = getattr(face, "charToGlyph", None)
    if not char_to_glyph:
        return frozenset()
    return frozenset(code for code, glyph in char_to_glyph.items() if glyph)


def _build_source_list() -> tuple:
    sources = list(_FONT_SOURCES)
    for noto_name in _LINUX_SCRIPT_FONTS:
        regular, bold = [], []
        for directory in _LINUX_FONT_DIRS:
            regular.append((os.path.join(directory, f"{noto_name}-Regular.ttf"), 0))
            bold.append((os.path.join(directory, f"{noto_name}-Bold.ttf"), 0))
        sources.append((tuple(regular), tuple(bold)))
    extra = _extra_font_paths()
    if extra:
        sources.append((tuple((path, 0) for path in extra), tuple((path, 0) for path in extra)))
    return tuple(sources)


def _register_fonts() -> tuple[dict[str, frozenset[int]], tuple[str, ...]]:
    global _FONT_COVERAGE, _FONT_ORDER
    if _FONT_COVERAGE is not None:
        return _FONT_COVERAGE, _FONT_ORDER

    coverage: dict[str, frozenset[int]] = {}
    regular_order: list[str] = []
    bold_order: list[str] = []
    bold_of: dict[str, str] = {}
    taken = set(pdfmetrics.getRegisteredFontNames())

    for regular_candidates, bold_candidates in _build_source_list():
        registered: dict[tuple[str, int], str] = {}
        regulars = _register_candidates(regular_candidates, registered, taken)
        bolds = _register_candidates(bold_candidates, registered, taken) if bold_candidates else []
        for index, regular in enumerate(regulars):
            if regular is None:
                continue
            regular_codepoints = _glyph_codepoints(regular)
            if not regular_codepoints:
                logger.warning("Font %s exposes no glyphs, ignoring it", regular)
                continue

            coverage[regular] = regular_codepoints
            regular_order.append(regular)

            bold = regular
            if index < len(bolds) and bolds[index] is not None:
                bold_codepoints = _glyph_codepoints(bolds[index])
                if bold_codepoints:
                    coverage[bolds[index]] = bold_codepoints
                    bold_order.append(bolds[index])
                    bold_of[bolds[index]] = bolds[index]
                    bold = bolds[index]
                else:
                    logger.warning("Font %s exposes no glyphs, using regular for bold", bolds[index])

            bold_of[regular] = bold
            try:
                pdfmetrics.registerFontFamily(
                    regular, normal=regular, bold=bold, italic=regular, boldItalic=bold
                )
            except Exception as exc:
                logger.debug("Could not register font family %s: %s", regular, exc)

    for cid_name in _CID_FONTS:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(cid_name))
        except Exception as exc:
            logger.debug("CID font %s unavailable: %s", cid_name, exc)
            continue
        codepoints: set[int] = set()
        for start, end in _CID_RANGES.get(cid_name, ()):
            codepoints.update(range(start, end + 1))
        coverage[cid_name] = frozenset(codepoints)
        regular_order.append(cid_name)
        bold_of[cid_name] = cid_name

    coverage[BASE_FONT] = _WIN_ANSI
    regular_order.append(BASE_FONT)
    coverage[BASE_BOLD_FONT] = _WIN_ANSI
    bold_order.append(BASE_BOLD_FONT)
    bold_of[BASE_FONT] = BASE_BOLD_FONT
    bold_of[BASE_BOLD_FONT] = BASE_BOLD_FONT

    order = regular_order + bold_order
    if len(regular_order) == 1:
        logger.warning(
            "No Unicode font could be registered; non-Latin transcripts will lose characters. "
            "Install a Noto font or point %s at a directory of .ttf files.", FONT_DIR_ENV
        )

    _FONT_COVERAGE = coverage
    _FONT_ORDER = tuple(order)
    logger.info("PDF fonts registered: %s", ", ".join(regular_order))
    return _FONT_COVERAGE, _FONT_ORDER


def _normalize_text(text: str) -> str:
    cleaned = []
    for char in text:
        category = unicodedata.category(char)
        if category in _DROP_CATEGORIES:
            continue
        cleaned.append(" " if category == "Cc" else char)
    return _WHITESPACE_RE.sub(" ", "".join(cleaned)).strip()


def _choose_fonts(text: str) -> dict[str, str]:
    coverage, order = _register_fonts()
    distinct = {ord(char) for char in text}
    if not distinct:
        return {}
    ranked = sorted(order, key=lambda name: -len(distinct & coverage[name]))
    char_font: dict[str, str] = {}
    for code in distinct:
        for name in ranked:
            if code in coverage[name]:
                char_font[chr(code)] = name
                break
    return char_font


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _runs(text: str, char_font: dict[str, str]) -> tuple[list[tuple[str, str]], set[str]]:
    runs: list[tuple[str, str]] = []
    dropped: set[str] = set()
    current_font = None
    current: list[str] = []
    for char in text:
        font_name = char_font.get(char)
        if font_name is None:
            dropped.add(char)
            continue
        if font_name == current_font:
            current.append(char)
        else:
            if current:
                runs.append((current_font, "".join(current)))
            current_font = font_name
            current = [char]
    if current:
        runs.append((current_font, "".join(current)))
    return runs, dropped


def _markup(text: str, char_font: dict[str, str]) -> str:
    if not text:
        return ""
    runs, dropped = _runs(text, char_font)
    if dropped:
        logger.warning(
            "Dropped %d character(s) with no glyph in any available font: %s",
            len(dropped),
            " ".join(f"U+{ord(c):04X}" for c in sorted(dropped)[:20]),
        )
    return "".join(
        f'<font name="{font_name}">{_escape(run_text)}</font>' for font_name, run_text in runs if run_text
    )


def _paragraph(text: str, char_font: dict[str, str], style: ParagraphStyle) -> Paragraph:
    return Paragraph(_markup(text, char_font) or " ", style)


def _label_paragraph(label: str, value: str, char_font: dict[str, str], style: ParagraphStyle) -> Paragraph:
    markup = f'<font name="{BASE_BOLD_FONT}"><b>{_escape(label)}</b></font>'
    rendered = _markup(value, char_font)
    if rendered:
        markup += f'<font name="{BASE_FONT}"> </font>{rendered}'
    return Paragraph(markup, style)


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

    document_title = _normalize_text("SmartMeet AI \u2014 Meeting Transcript")
    date_label = _normalize_text(meeting_date.strftime("%d %b %Y, %I:%M %p")) if meeting_date else ""
    meta_rows = [
        ("Meeting: ", title),
        ("Room code: ", room_code),
        ("Host: ", host_name),
    ]
    if meeting_date:
        meta_rows.append(("Date: ", date_label))
    meta_rows.append(("Language: ", language_label))

    body_lines = [line for line in (_normalize_text(l) for l in (transcript_text or "").splitlines()) if line]

    probe = "\n".join([document_title, *(_normalize_text(f"{label}{value}") for label, value in meta_rows), *body_lines])
    char_font = _choose_fonts(probe)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title", parent=styles["Heading1"], fontSize=18, spaceAfter=12, fontName=BASE_FONT
    )
    meta_style = ParagraphStyle(
        "Meta", parent=styles["Normal"], fontSize=10, textColor="#555555", spaceAfter=6, fontName=BASE_FONT
    )
    heading_style = ParagraphStyle("SectionHeading", parent=styles["Heading2"], fontName=BASE_FONT)
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=11, leading=16, spaceAfter=8, fontName=BASE_FONT
    )

    story = [_paragraph(document_title, char_font, title_style)]
    for label, value in meta_rows:
        story.append(_label_paragraph(label, _normalize_text(str(value)), char_font, meta_style))

    story.append(Spacer(1, 0.2 * inch))
    story.append(
        Paragraph(f'<font name="{BASE_BOLD_FONT}"><b>Transcript</b></font>', heading_style)
    )
    story.append(Spacer(1, 0.1 * inch))

    if body_lines:
        for line in body_lines:
            story.append(_paragraph(line, char_font, body_style))
    else:
        story.append(
            Paragraph(
                f'<font name="{BASE_FONT}"><i>No transcript text available.</i></font>', body_style
            )
        )

    doc.build(story)
    buffer.seek(0)
    return buffer.read()
