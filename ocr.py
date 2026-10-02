"""OCR for image-only pages using the OCR engine built into Windows (Windows.Media.Ocr).

Chosen because it is already on the machine (no model download), runs ~0.1-0.3 s/page on one
core, and returns word boxes, which the layout code needs for columns and paragraphs.
"""
import asyncio

import pymupdf
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

TARGET_WIDTH_PX = 1350
_engine = None
_loop = None


def _get_engine():
    global _engine, _loop
    if _engine is None:
        _engine = OcrEngine.try_create_from_language(Language("en-GB")) or OcrEngine.try_create_from_user_profile_languages()
        _loop = asyncio.new_event_loop()
    return _engine


async def _recognise(engine, bitmap):
    return await engine.recognize_async(bitmap)


def ocr_page(page, dpi=None):
    """-> list of lines [x0, y0, x1, y1, size, flags, text] in PDF points (same shape as text-layer lines)."""
    engine = _get_engine()
    if dpi is None:                          # ~1,350 px across the page: best speed/accuracy on these scans
        dpi = min(300, max(110, TARGET_WIDTH_PX / max(page.rect.width, 1) * 72))
    scale = dpi / 72.0
    longest = max(page.rect.width, page.rect.height) * scale
    if longest > 9000:                       # engine limit is 10,000 px
        scale *= 9000 / longest
    # render opaque first: 1-bit scans are stencil masks and come out blank on a transparent page
    pix = pymupdf.Pixmap(page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False), 1)
    writer = DataWriter()
    writer.write_bytes(pix.samples)          # RGBA read as BGRA: channel order is irrelevant to OCR
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, pix.width, pix.height)
    result = _loop.run_until_complete(_recognise(engine, bitmap))
    lines = []
    for ln in result.lines:
        words = [(w.bounding_rect.x, w.bounding_rect.y, w.bounding_rect.width, w.bounding_rect.height, w.text) for w in ln.words]
        if not words:
            continue
        # the engine can run a line straight across a column gutter: split at unusually wide gaps
        heights = sorted(h for _, _, _, h, _ in words)
        hmed = heights[len(heights) // 2]
        seg = [words[0]]
        segs = [seg]
        for prev, w in zip(words, words[1:]):
            if w[0] - (prev[0] + prev[2]) > 2.2 * hmed:
                seg = []
                segs.append(seg)
            seg.append(w)
        for seg in segs:
            x0 = min(w[0] for w in seg) / scale
            y0 = min(w[1] for w in seg) / scale
            x1 = max(w[0] + w[2] for w in seg) / scale
            y1 = max(w[1] + w[3] for w in seg) / scale
            lines.append([round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1), round((y1 - y0) * 0.8, 1), 0,
                          " ".join(w[4] for w in seg)])
    return lines
