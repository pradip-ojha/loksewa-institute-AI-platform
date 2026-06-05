"""Page rasterization + checked-PDF assembly for the answer-sheet pipeline.

Every answer sheet — whether uploaded as a born-digital PDF, a scanned PDF, or a
phone photo — is rendered to a high-DPI PNG per page. Extraction (vision),
line-level coordinates, and annotation all then live in one consistent
image-pixel space, so a bbox returned by the extractor maps directly onto the
pixels the annotator draws on. The checked PDF is rebuilt from the annotated
page images.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import fitz  # PyMuPDF
from PIL import Image

# Default rasterization DPI. High enough for legible handwriting OCR + clean
# annotation, without producing absurd image sizes for GPT-5.5 vision.
DEFAULT_DPI = 200
# Cap any single rendered page so a huge scan can't blow up memory / the vision
# payload. Pages wider/taller than this are downscaled preserving aspect ratio.
MAX_PAGE_PIXELS = 2200


@dataclass
class PageImage:
    """One rendered answer-sheet page in PNG bytes plus its pixel dimensions."""

    page_number: int  # 1-based
    png_bytes: bytes
    width: int
    height: int


def _downscale_if_needed(img: Image.Image) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= MAX_PAGE_PIXELS:
        return img
    scale = MAX_PAGE_PIXELS / float(longest)
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


def _pil_to_png(img: Image.Image) -> tuple[bytes, int, int]:
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), img.width, img.height


def _render_pdf(data: bytes, dpi: int) -> list[PageImage]:
    pages: list[PageImage] = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    with fitz.open(stream=data, filetype="pdf") as doc:
        for index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            img = _downscale_if_needed(img)
            png, w, h = _pil_to_png(img)
            pages.append(PageImage(page_number=index + 1, png_bytes=png, width=w, height=h))
    return pages


def _render_image(data: bytes) -> list[PageImage]:
    img = Image.open(io.BytesIO(data))
    # Honour EXIF orientation from phone cameras so coordinates are upright.
    try:
        from PIL import ImageOps

        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = _downscale_if_needed(img)
    png, w, h = _pil_to_png(img)
    return [PageImage(page_number=1, png_bytes=png, width=w, height=h)]


def render_to_page_images(file_bytes: bytes, mime_type: str, dpi: int = DEFAULT_DPI) -> list[PageImage]:
    """Render an uploaded answer sheet (PDF or image) to one PNG per page."""
    if mime_type == "application/pdf":
        return _render_pdf(file_bytes, dpi)
    if mime_type.startswith("image/"):
        return _render_image(file_bytes)
    raise ValueError(f"Unsupported answer-sheet type for rasterization: {mime_type}")


def build_pdf_from_images(page_pngs: list[bytes]) -> bytes:
    """Assemble a PDF from (annotated) page PNGs, one image per page."""
    if not page_pngs:
        raise ValueError("Cannot build a PDF from zero pages")
    images: list[Image.Image] = []
    for png in page_pngs:
        im = Image.open(io.BytesIO(png))
        images.append(im.convert("RGB"))
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
    return buf.getvalue()
