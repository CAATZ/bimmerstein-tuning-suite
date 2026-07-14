"""Build the BimmerStein Tuning Suite user manual PDF from its Markdown source."""
from __future__ import annotations

import argparse
from html import escape
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "manual" / "USER_MANUAL.md"
OUTPUT_DIR = ROOT / "output" / "pdf"
OUTPUT_NAME = "BimmerStein-Tuning-Suite-User-Manual.pdf"
TEMP_DIR = ROOT / "tmp" / "pdfs"


def _inline(text: str) -> str:
    rendered = escape(text, quote=True)
    rendered = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", rendered)
    rendered = re.sub(r"`(.+?)`", r'<font name="Courier">\1</font>', rendered)

    def link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(("https://", "http://")):
            return f'<link href="{target}" color="#b42931">{label}</link>'
        return label

    return re.sub(r"\[([^]]+)\]\(([^)]+)\)", link, rendered)


def _font_paths() -> tuple[Path | None, Path | None, Path | None]:
    candidates = [
        (
            Path(r"C:\Windows\Fonts\segoeui.ttf"),
            Path(r"C:\Windows\Fonts\segoeuib.ttf"),
            Path(r"C:\Windows\Fonts\seguisb.ttf"),
        ),
        (
            ROOT / "resources" / "fonts" / "JetBrainsMono-Regular.ttf",
            ROOT / "resources" / "fonts" / "JetBrainsMono-Bold.ttf",
            ROOT / "resources" / "fonts" / "JetBrainsMono-Bold.ttf",
        ),
    ]
    for regular, bold, semibold in candidates:
        if regular.is_file() and bold.is_file() and semibold.is_file():
            return regular, bold, semibold
    return None, None, None


def _register_fonts() -> tuple[str, str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular, bold, semibold = _font_paths()
    if regular is None:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Bold"
    pdfmetrics.registerFont(TTFont("ManualSans", str(regular)))
    pdfmetrics.registerFont(TTFont("ManualSansBold", str(bold)))
    pdfmetrics.registerFont(TTFont("ManualSansSemibold", str(semibold)))
    pdfmetrics.registerFontFamily(
        "ManualSans",
        normal="ManualSans",
        bold="ManualSansBold",
        italic="ManualSans",
        boldItalic="ManualSansBold",
    )
    return "ManualSans", "ManualSansBold", "ManualSansSemibold"


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm

    regular, bold, semibold = _register_fonts()
    base = getSampleStyleSheet()
    return {
        "regular": regular,
        "bold": bold,
        "semibold": semibold,
        "body": ParagraphStyle(
            "ManualBody",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=9.2,
            leading=12.8,
            textColor=colors.HexColor("#222832"),
            spaceAfter=2.5 * mm,
        ),
        "h2": ParagraphStyle(
            "ManualH2",
            parent=base["Heading1"],
            fontName=bold,
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#141a22"),
            spaceBefore=1 * mm,
            spaceAfter=5 * mm,
        ),
        "h3": ParagraphStyle(
            "ManualH3",
            parent=base["Heading2"],
            fontName=semibold,
            fontSize=11.5,
            leading=14,
            textColor=colors.HexColor("#b42931"),
            spaceBefore=2.5 * mm,
            spaceAfter=1.8 * mm,
        ),
        "bullet": ParagraphStyle(
            "ManualBullet",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8.9,
            leading=12.2,
            textColor=colors.HexColor("#222832"),
            leftIndent=5 * mm,
            firstLineIndent=-3.6 * mm,
            bulletIndent=0,
            spaceAfter=1.2 * mm,
        ),
        "caption": ParagraphStyle(
            "ManualCaption",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=7.8,
            leading=10,
            textColor=colors.HexColor("#66707d"),
            alignment=TA_CENTER,
            spaceBefore=1.2 * mm,
            spaceAfter=3 * mm,
        ),
        "callout": ParagraphStyle(
            "ManualCallout",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8.8,
            leading=12.3,
            textColor=colors.HexColor("#20252d"),
            alignment=TA_LEFT,
        ),
        "table_head": ParagraphStyle(
            "ManualTableHead",
            parent=base["BodyText"],
            fontName=semibold,
            fontSize=7.2,
            leading=8.6,
            textColor=colors.white,
        ),
        "table_body": ParagraphStyle(
            "ManualTableBody",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=7.0,
            leading=8.4,
            textColor=colors.HexColor("#252b34"),
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName=bold,
            fontSize=31,
            leading=35,
            textColor=colors.white,
            alignment=TA_LEFT,
            spaceAfter=5 * mm,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=15,
            leading=20,
            textColor=colors.HexColor("#d8dde6"),
            alignment=TA_LEFT,
        ),
        "cover_meta": ParagraphStyle(
            "CoverMeta",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#aeb6c2"),
            alignment=TA_LEFT,
        ),
    }


def _image_flowable(path: Path, caption: str, available_width: float, max_height: float, styles):
    from reportlab.lib import colors
    from reportlab.platypus import Image, KeepTogether, Paragraph, Table, TableStyle

    if not path.is_file():
        raise FileNotFoundError(path)
    image = Image(str(path))
    scale = min(available_width / image.imageWidth, max_height / image.imageHeight, 1.0)
    image.drawWidth = image.imageWidth * scale
    image.drawHeight = image.imageHeight * scale
    frame = Table([[image]], colWidths=[image.drawWidth + 8], rowHeights=[image.drawHeight + 8])
    frame.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#101215")),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#c6cbd3")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    return KeepTogether([frame, Paragraph(_inline(caption), styles["caption"])])


def _table_flowable(rows: list[list[str]], styles, available_width: float):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    rendered = []
    for row_index, row in enumerate(normalized):
        style = styles["table_head"] if row_index == 0 else styles["table_body"]
        rendered.append([Paragraph(_inline(value), style) for value in row])
    widths = [available_width * fraction for fraction in (
        ([0.18, 0.20, 0.62] if column_count == 3 else [1.0 / column_count] * column_count)
    )]
    table = Table(rendered, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#232a34")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f2f4f7")]),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c9ced6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _parse_markdown(text: str, styles, available_width: float):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, Spacer, Table, TableStyle

    lines = text.splitlines()
    first_break = lines.index("<!-- pagebreak -->")
    lines = lines[first_break + 1:]
    story = []
    index = 0

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line == "<!-- pagebreak -->":
            story.append(PageBreak())
            index += 1
            continue
        if line.startswith("## "):
            story.append(Paragraph(_inline(line[3:]), styles["h2"]))
            index += 1
            continue
        if line.startswith("### "):
            story.append(Paragraph(_inline(line[4:]), styles["h3"]))
            index += 1
            continue
        image_match = re.fullmatch(r"!\[([^]]+)\]\(([^)]+)\)", line)
        if image_match:
            image_path = (SOURCE.parent / image_match.group(2)).resolve()
            story.append(_image_flowable(
                image_path,
                image_match.group(1),
                available_width,
                92 * mm,
                styles,
            ))
            index += 1
            continue
        if line.startswith("> "):
            parts = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                parts.append(lines[index].strip().lstrip("> "))
                index += 1
            callout = Table([[Paragraph(_inline(" ".join(parts)), styles["callout"]) ]],
                            colWidths=[available_width])
            callout.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff3e1")),
                ("LINEBEFORE", (0, 0), (0, -1), 4, colors.HexColor("#e5484d")),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#efc68f")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.extend([callout, Spacer(1, 3 * mm)])
            continue
        if line.startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            rows = [
                [cell.strip() for cell in row.strip("|").split("|")]
                for row in table_lines
            ]
            if len(rows) > 1 and all(re.fullmatch(r"[-: ]+", cell) for cell in rows[1]):
                rows.pop(1)
            story.extend([_table_flowable(rows, styles, available_width), Spacer(1, 3 * mm)])
            continue
        if line.startswith("- ") or re.match(r"\d+\. ", line):
            numbered = bool(re.match(r"\d+\. ", line))
            item_number = 1
            while index < len(lines):
                candidate = lines[index].strip()
                if numbered:
                    match = re.match(r"(\d+)\. (.+)", candidate)
                    if not match:
                        break
                    item_number = int(match.group(1))
                    body = match.group(2)
                    marker = f"{item_number}."
                else:
                    if not candidate.startswith("- "):
                        break
                    body = candidate[2:]
                    marker = "[ ]" if body.startswith("[ ] ") else "-"
                    if body.startswith("[ ] "):
                        body = body[4:]
                story.append(Paragraph(
                    f"<b>{escape(marker)}</b>&nbsp;&nbsp;{_inline(body)}",
                    styles["bullet"],
                ))
                index += 1
            story.append(Spacer(1, 1.5 * mm))
            continue
        if line.startswith("```"):
            index += 1
            code = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            block = Table([[Paragraph(
                f'<font name="Courier">{escape("<br/>".join(code))}</font>',
                styles["table_body"],
            )]], colWidths=[available_width])
            block.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef1f5")),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#c5cad2")),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.extend([block, Spacer(1, 2 * mm)])
            continue

        paragraph = [line]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if not candidate or candidate.startswith(("#", "- ", ">", "|", "```", "![")) \
                    or candidate == "<!-- pagebreak -->" or re.match(r"\d+\. ", candidate):
                break
            paragraph.append(candidate)
            index += 1
        story.append(Paragraph(_inline(" ".join(paragraph)), styles["body"]))

    return story


def build_pdf(output: Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        Image,
        NextPageTemplate,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
    )

    styles = _styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    page_width, page_height = A4
    left = 18 * mm
    right = 18 * mm
    body_width = page_width - left - right

    def cover_page(canvas, _document) -> None:
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#10151c"))
        canvas.rect(0, 0, page_width, page_height, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#e5484d"))
        canvas.rect(0, page_height - 14 * mm, page_width, 14 * mm, fill=1, stroke=0)
        canvas.rect(left, 34 * mm, 42 * mm, 2.2 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#252c36"))
        canvas.circle(page_width - 22 * mm, 22 * mm, 42 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#e5484d"))
        canvas.circle(page_width - 22 * mm, 22 * mm, 27 * mm, fill=1, stroke=0)
        canvas.restoreState()

    def body_page(canvas, document) -> None:
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#1a2029"))
        canvas.rect(0, page_height - 13 * mm, page_width, 13 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#e5484d"))
        canvas.rect(0, page_height - 14.2 * mm, page_width, 1.2 * mm, fill=1, stroke=0)
        canvas.setFont(styles["semibold"], 8.2)
        canvas.setFillColor(colors.white)
        canvas.drawString(left, page_height - 8.5 * mm, "BimmerStein Tuning Suite")
        canvas.setFont(styles["regular"], 7.2)
        canvas.setFillColor(colors.HexColor("#cbd1da"))
        canvas.drawRightString(page_width - right, page_height - 8.5 * mm,
                               "ECU Calibration and Data Logging")
        canvas.setStrokeColor(colors.HexColor("#d8dce2"))
        canvas.line(left, 13 * mm, page_width - right, 13 * mm)
        canvas.setFont(styles["regular"], 7.5)
        canvas.setFillColor(colors.HexColor("#6d7580"))
        canvas.drawString(left, 8.5 * mm, "User Manual - Version 0.1.0 Beta 1")
        canvas.drawRightString(page_width - right, 8.5 * mm, str(document.page))
        canvas.restoreState()

    document = BaseDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=left,
        rightMargin=right,
        topMargin=22 * mm,
        bottomMargin=18 * mm,
        title="BimmerStein Tuning Suite User Manual",
        author="CAATZ and contributors",
        subject="ECU calibration editor and data logger user manual",
        creator="BimmerStein Tuning Suite documentation build",
    )
    cover_frame = Frame(left, 24 * mm, body_width, page_height - 48 * mm,
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    body_frame = Frame(left, 18 * mm, body_width, page_height - 40 * mm,
                       leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    document.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame], onPage=cover_page,
                     autoNextPageTemplate="Body"),
        PageTemplate(id="Body", frames=[body_frame], onPage=body_page),
    ])

    icon_path = ROOT / "resources" / "icons" / "app.ico"
    cover = [Spacer(1, 26 * mm)]
    if icon_path.is_file():
        icon = Image(str(icon_path), width=28 * mm, height=28 * mm)
        cover.extend([icon, Spacer(1, 12 * mm)])
    cover.extend([
        Paragraph("BimmerStein<br/>Tuning Suite", styles["cover_title"]),
        Paragraph("ECU Calibration and Data Logging", styles["cover_subtitle"]),
        Spacer(1, 15 * mm),
        Paragraph("USER MANUAL", styles["cover_meta"]),
        Paragraph("Version 0.1.0 Beta 1 - Windows x64", styles["cover_meta"]),
        Spacer(1, 42 * mm),
        Paragraph(
            "Read the safety section before editing a calibration. Keep an untouched backup "
            "of every original file.",
            styles["cover_meta"],
        ),
        NextPageTemplate("Body"),
        PageBreak(),
    ])

    markdown = SOURCE.read_text(encoding="utf-8")
    story = cover + _parse_markdown(markdown, styles, body_width)
    document.build(story)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the BimmerStein user manual PDF")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / OUTPUT_NAME)
    args = parser.parse_args(argv)
    output = build_pdf(args.output.resolve())
    print(f"User manual written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
