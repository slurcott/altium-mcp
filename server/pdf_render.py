"""Render datasheet PDF pages to PNG (pypdfium2, Apache/BSD) and find land-pattern pages.

Used to read package / land-pattern drawings as images. No poppler needed.
"""
import re
from pathlib import Path

LP_WORDS = re.compile(r"EXAMPLE BOARD LAYOUT|LAND PATTERN|PAD PATTERN|RECOMMENDED (?:MINIMUM )?PADS?|"
                      r"RECOMMENDED (?:PCB )?(?:FOOTPRINT|LAYOUT)|"
                      r"PACKAGE OUTLINE|PACKAGE DRAWING|MECHANICAL DATA|EXAMPLE STENCIL", re.I)


def find_drawing_pages(pdf):
    """[(page number 1-based, [matched headings])] for pages that look like package drawings."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    out = []
    for i in range(len(doc)):
        text = doc[i].get_textpage().get_text_range()
        hits = sorted(set(m.upper() for m in LP_WORDS.findall(text)))
        if hits:
            out.append((i + 1, hits))
    return out


def render(pdf, page, out_png, scale=3.0, crop=None):
    """Render one page (1-based) to PNG. crop = (left, top, right, bottom) fractions 0..1."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    img = doc[page - 1].render(scale=scale).to_pil()
    if crop:
        w, h = img.size
        img = img.crop((int(crop[0] * w), int(crop[1] * h), int(crop[2] * w), int(crop[3] * h)))
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)
    return str(out_png), img.size


if __name__ == "__main__":
    import sys
    if sys.argv[1] == "find":
        for p, h in find_drawing_pages(sys.argv[2]):
            print(p, h)
    else:
        crop = tuple(float(v) for v in sys.argv[5].split(",")) if len(sys.argv) > 5 else None
        print(render(sys.argv[2], int(sys.argv[3]), sys.argv[4], crop=crop))
