#!/usr/bin/env python3
"""
jpg2pdf - Convert a directory of images (JPG/PNG) into a PDF.
"""

import argparse
import io
import os
import sys
import tempfile
from pathlib import Path

import markdown
from bs4 import BeautifulSoup
from PIL import Image
from pypdf import PdfWriter, PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
)

# ---------------------------------------------------------------------------
# Page geometry
# ---------------------------------------------------------------------------
PAGE_W, PAGE_H = letter          # 612 x 792 pts
MARGIN = 0.75 * inch             # 54 pts
CONTENT_W = PAGE_W - 2 * MARGIN  # 504 pts  (7 inches)
CONTENT_H = PAGE_H - 2 * MARGIN  # 684 pts  (9.5 inches)


# ---------------------------------------------------------------------------
# Markdown → reportlab story
# ---------------------------------------------------------------------------

def _rl_styles():
    """Return a dict of named ParagraphStyles for Markdown elements."""
    base = getSampleStyleSheet()

    def make(name, parent_name="Normal", **kwargs):
        parent = base[parent_name] if parent_name in base else base["Normal"]
        return ParagraphStyle(name, parent=parent, **kwargs)

    return {
        "h1": make("h1", "Heading1", fontSize=20, spaceAfter=10, spaceBefore=14),
        "h2": make("h2", "Heading2", fontSize=16, spaceAfter=8,  spaceBefore=12),
        "h3": make("h3", "Heading3", fontSize=13, spaceAfter=6,  spaceBefore=10),
        "h4": make("h4", "Heading4", fontSize=11, spaceAfter=4,  spaceBefore=8),
        "p":  make("p",  "Normal",   fontSize=10, spaceAfter=6,  leading=14),
        "li": make("li", "Normal",   fontSize=10, spaceAfter=3,  leading=14,
                   leftIndent=18, bulletIndent=6),
        "code": make("code", "Code", fontName="Courier", fontSize=9,
                     backColor=colors.HexColor("#f5f5f5"), leading=13,
                     leftIndent=12, rightIndent=12, spaceAfter=6),
        "blockquote": make("blockquote", "Normal", fontSize=10, leading=14,
                           leftIndent=24, textColor=colors.HexColor("#555555"),
                           spaceAfter=6),
        "normal": base["Normal"],
    }


def _escape(text: str) -> str:
    """Escape characters that have special meaning inside reportlab Paragraphs."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def _node_to_story(node, styles, story, list_depth=0):
    """Recursively walk a BeautifulSoup node tree and build a reportlab story."""
    if isinstance(node, str):
        return  # bare strings handled by parent tag

    tag = node.name if hasattr(node, "name") else None
    if tag is None:
        return

    def inline_text(el) -> str:
        """Collect inline text with basic bold/italic/code markup."""
        from bs4 import NavigableString, Tag
        if isinstance(el, NavigableString):
            return _escape(str(el))
        parts = []
        for child in el.children:
            if isinstance(child, NavigableString):
                parts.append(_escape(str(child)))
            elif isinstance(child, Tag):
                if child.name in ("strong", "b"):
                    parts.append(f"<b>{_escape(child.get_text())}</b>")
                elif child.name in ("em", "i"):
                    parts.append(f"<i>{_escape(child.get_text())}</i>")
                elif child.name == "code":
                    parts.append(f"<font name='Courier'>{_escape(child.get_text())}</font>")
                elif child.name == "a":
                    href = child.get("href", "")
                    text = _escape(child.get_text())
                    parts.append(f'<link href="{href}" color="blue">{text}</link>')
                elif child.name == "br":
                    parts.append("<br/>")
                else:
                    parts.append(inline_text(child))
        return "".join(parts)

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = tag[:2] if tag[:2] in styles else "h4"
        story.append(Paragraph(inline_text(node), styles[level]))

    elif tag == "p":
        text = inline_text(node)
        if text.strip():
            story.append(Paragraph(text, styles["p"]))

    elif tag == "hr":
        story.append(HRFlowable(width="100%", thickness=1,
                                color=colors.HexColor("#cccccc"), spaceAfter=6))

    elif tag in ("ul", "ol"):
        for i, child in enumerate(node.children):
            if hasattr(child, "name") and child.name == "li":
                bullet = "•" if tag == "ul" else f"{i}."
                text = inline_text(child)
                story.append(Paragraph(f"{bullet}  {text}", styles["li"]))
                # Handle nested lists inside li
                for sub in child.children:
                    if hasattr(sub, "name") and sub.name in ("ul", "ol"):
                        _node_to_story(sub, styles, story, list_depth + 1)

    elif tag == "li":
        text = inline_text(node)
        story.append(Paragraph(f"•  {text}", styles["li"]))

    elif tag == "pre":
        code_el = node.find("code")
        src = code_el.get_text() if code_el else node.get_text()
        story.append(Preformatted(src, styles["code"]))

    elif tag == "code" and node.parent and node.parent.name != "pre":
        story.append(Paragraph(
            f"<font name='Courier'>{_escape(node.get_text())}</font>",
            styles["p"]))

    elif tag == "blockquote":
        for child in node.children:
            if hasattr(child, "name"):
                text = inline_text(child)
                if text.strip():
                    story.append(Paragraph(text, styles["blockquote"]))

    elif tag == "table":
        # Simple table fallback: render rows as indented paragraphs
        for row in node.find_all("tr"):
            cells = [_escape(td.get_text(strip=True))
                     for td in row.find_all(["th", "td"])]
            story.append(Paragraph("  |  ".join(cells), styles["p"]))

    else:
        # Recurse into unknown/container tags
        for child in node.children:
            _node_to_story(child, styles, story, list_depth)


def markdown_to_pdf(md_path: Path) -> bytes:
    """Convert a Markdown file to PDF bytes using reportlab."""
    md_text = md_path.read_text(encoding="utf-8")
    html = markdown.markdown(
        md_text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )
    soup = BeautifulSoup(html, "html.parser")
    styles = _rl_styles()

    story = []
    for child in soup.children:
        _node_to_story(child, styles, story)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
    )
    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Image → PDF page
# ---------------------------------------------------------------------------

def images_to_pdf(image_paths: list[Path]) -> bytes:
    """Render each image as a separate PDF page using canvas for precise placement."""
    from reportlab.pdfgen import canvas as rl_canvas

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=letter)

    written = 0
    for img_path in image_paths:
        with Image.open(img_path) as im:
            img_w_px, img_h_px = im.size

        if img_w_px == 0 or img_h_px == 0:
            print(f"  Skipping zero-size image: {img_path.name}", file=sys.stderr)
            continue

        aspect = img_h_px / img_w_px

        # Fit to content width; clamp height to content height
        display_w = CONTENT_W
        display_h = display_w * aspect
        if display_h > CONTENT_H:
            display_h = CONTENT_H
            display_w = display_h / aspect

        # Center image horizontally; anchor top-left from top of content area
        x = MARGIN + (CONTENT_W - display_w) / 2
        y = PAGE_H - MARGIN - display_h

        c.drawImage(str(img_path), x, y, width=display_w, height=display_h,
                    preserveAspectRatio=True, mask="auto")
        c.showPage()
        written += 1

    if not written:
        return b""

    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert a directory of JPG/PNG images into a PDF file."
    )
    parser.add_argument("subdir", help="Path to the subdirectory containing images")
    args = parser.parse_args()

    subdir = Path(args.subdir).resolve()
    if not subdir.is_dir():
        print(f"Error: '{subdir}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    # Collect images (jpg/jpeg/png), sorted alphanumerically
    image_paths = sorted(
        p for p in subdir.iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.is_file()
    )

    if not image_paths:
        print("No JPG or PNG images found in the directory.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(image_paths)} image(s).")

    readme = subdir / "README.md"
    parts: list[bytes] = []

    # Optional README → first pages
    if readme.exists():
        print("Converting README.md …")
        readme_pdf = markdown_to_pdf(readme)
        parts.append(readme_pdf)

    # Images → subsequent pages
    print("Rendering images …")
    images_pdf = images_to_pdf(image_paths)
    if images_pdf:
        parts.append(images_pdf)

    if not parts:
        print("Nothing to write.", file=sys.stderr)
        sys.exit(1)

    # Merge PDF parts
    output_path = subdir.parent / f"{subdir.name}.pdf"
    writer = PdfWriter()
    for pdf_bytes in parts:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            writer.add_page(page)

    with open(output_path, "wb") as f:
        writer.write(f)

    total_pages = sum(
        len(PdfReader(io.BytesIO(p)).pages) for p in parts
    )
    print(f"Written {total_pages} page(s) → {output_path}")


if __name__ == "__main__":
    main()
