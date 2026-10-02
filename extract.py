"""Fast first-pages text extraction for PDFs (PyMuPDF), parallel over processes."""
import os
import random
import re
from concurrent.futures import ProcessPoolExecutor

import pymupdf

PDF_DIR = os.environ.get("LWIZ_PDF_DIR") or r"D:\Programming\Python\lwiz\NotAScraper\NEW STUFF\downloaded\pdf"
MAX_PAGES = 2      # only the head of the document is needed to tell its type
MAX_CHARS = 3000
WORKERS = os.cpu_count() or 4

_WS = re.compile(r"\s+")


def _init():
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)


def extract_one(name):
    """Return (name, n_pages, text). n_pages == -1 means the file could not be opened."""
    try:
        with pymupdf.open(os.path.join(PDF_DIR, name)) as doc:
            n = doc.page_count
            parts, size = [], 0
            for i in range(min(n, MAX_PAGES)):
                t = doc[i].get_text("text")
                parts.append(t)
                size += len(t)
                if size >= MAX_CHARS:
                    break
        return name, n, _WS.sub(" ", " ".join(parts))[:MAX_CHARS].strip()
    except Exception:
        return name, -1, ""


def extract_many(names, workers=WORKERS, chunksize=32):
    with ProcessPoolExecutor(workers, initializer=_init) as ex:
        yield from ex.map(extract_one, names, chunksize=chunksize)


def list_pdfs():
    return sorted(e.name for e in os.scandir(PDF_DIR) if e.name.lower().endswith(".pdf"))


def split_samples(seed=1234, sizes=(4000, 3000)):
    """Disjoint random samples (so each can be read from a cold disk cache)."""
    names = list_pdfs()
    random.Random(seed).shuffle(names)
    out, start = [], 0
    for s in sizes:
        out.append(names[start:start + s])
        start += s
    return len(names), out
