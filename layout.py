"""Read-once layout cache (SSD, SQLite): every text line of every page with position and font.

One pass over a PDF captures everything later stages need, so structure and metadata rules can be
re-run from the cache at ~1,000 docs/sec without touching the HDD again:

  page  = {"w", "h", "src": "text" | "ocr" | "needs_ocr" | "empty", "img": bool, "lines": [...], "tables": [...]}
  line  = [x0, y0, x1, y1, font_size, flags, text, block_no]      flags: 1 bold, 2 italic, 4 rotated
  table = {"bbox": [x0, y0, x1, y1], "rows": [[cell, ...], ...]}  (ruled tables only)

Image-only pages are OCRed inline (Windows OCR) unless ocr=False, in which case they are marked
"needs_ocr" and picked up by a later pass.
"""
import json
import os
import sqlite3
import time
import zlib

import pymupdf

from extract import PDF_DIR
from textcache import CACHE_DIR

LAYOUT_DB = os.path.join(CACHE_DIR, "layout.sqlite")
MIN_PAGE_CHARS = 25          # fewer characters than this and the page is treated as an image
TABLE_MIN_DRAWINGS = 8       # only look for ruled tables on pages that actually have rules
MAX_TABLE_PAGES = 40         # per document; table detection is the one slow call
BOLD, ITALIC, ROTATED = 1, 2, 4

_con = None
_TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES


def init_worker():
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    try:
        pymupdf.no_recommend_layout()                # silences the "consider pymupdf_layout" notice
    except Exception:
        pass


def connect(readonly=True):
    os.makedirs(CACHE_DIR, exist_ok=True)
    if readonly and os.path.exists(LAYOUT_DB):
        return sqlite3.connect(f"file:{LAYOUT_DB}?mode=ro", uri=True)
    con = sqlite3.connect(LAYOUT_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("CREATE TABLE IF NOT EXISTS docs (name TEXT PRIMARY KEY, pages INTEGER, ocr_pages INTEGER, "
                "ocr_ms INTEGER, read_ms INTEGER, layout BLOB) WITHOUT ROWID")
    con.commit()
    return con


def _text_lines(page):
    lines = []
    for bno, b in enumerate(page.get_text("dict", flags=_TEXT_FLAGS)["blocks"]):
        if b["type"] != 0:
            continue
        for ln in b["lines"]:
            spans = ln["spans"]
            text = "".join(s["text"] for s in spans)
            if not text.strip():
                continue
            n = bold = ital = 0
            sizes = {}
            for s in spans:
                k = len(s["text"].strip())
                if not k:
                    continue
                n += k
                sizes[round(s["size"], 1)] = sizes.get(round(s["size"], 1), 0) + k
                f = s["flags"]
                if f & 16 or "bold" in s["font"].lower():
                    bold += k
                if f & 2 or "italic" in s["font"].lower() or "oblique" in s["font"].lower():
                    ital += k
            flags = (BOLD if bold >= 0.6 * n else 0) | (ITALIC if ital >= 0.6 * n else 0)
            d = ln["dir"]
            if abs(d[0]) < 0.95:
                flags |= ROTATED
            x0, y0, x1, y1 = ln["bbox"]
            lines.append([round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1), max(sizes, key=sizes.get), flags,
                          text.rstrip(), bno])
    return lines


def _tables(page):
    out = []
    try:
        for t in page.find_tables().tables:
            rows = [[(c or "").strip() for c in r] for r in t.extract()]
            rows = [r for r in rows if any(r)]
            if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
                out.append({"bbox": [round(v, 1) for v in t.bbox], "rows": rows})
    except Exception:
        pass
    return out


def read_pdf(name, ocr=True):
    """-> (pages, ocr_pages, ocr_ms, read_ms, layout dict). pages == -1 if the file cannot be opened."""
    t0 = time.perf_counter()
    ocr_ms = ocr_pages = pending = 0
    out = []
    try:
        with pymupdf.open(os.path.join(PDF_DIR, name)) as doc:
            n = doc.page_count
            table_budget = MAX_TABLE_PAGES
            for page in doc:
                if page.rotation:
                    page.remove_rotation()           # so text direction and coordinates are as the page is viewed
                w, h = page.rect.width, page.rect.height
                lines = _text_lines(page)
                chars = sum(len(l[6]) for l in lines)
                img = False
                if chars < 400 or len(out) < 2:           # is there a page-sized image (scan / OCR layer)?
                    try:
                        img = any((i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]) > 0.5 * w * h
                                  for i in page.get_image_info())
                    except Exception:
                        img = False
                rec = {"w": round(w, 1), "h": round(h, 1), "src": "text", "img": img, "lines": lines}
                if chars < MIN_PAGE_CHARS or (img and chars < 200):
                    if ocr:
                        t1 = time.perf_counter()
                        try:
                            import ocr as _ocr
                            got = [l + [i] for i, l in enumerate(_ocr.ocr_page(page))]
                            if sum(len(l[6]) for l in got) >= chars:
                                rec["lines"] = got
                            rec["src"] = "ocr"
                        except Exception as e:
                            rec["src"], rec["err"] = "needs_ocr", str(e)[:120]
                        ocr_ms += int((time.perf_counter() - t1) * 1000)
                        ocr_pages += 1
                    else:
                        rec["src"] = "needs_ocr"
                        pending += 1
                elif table_budget > 0:
                    try:
                        nd = len(page.get_cdrawings())
                    except Exception:
                        nd = 0
                    if nd >= TABLE_MIN_DRAWINGS:
                        table_budget -= 1
                        tabs = _tables(page)
                        if tabs:
                            rec["tables"] = tabs
                out.append(rec)
        return n, (ocr_pages if not pending else -pending), ocr_ms, int((time.perf_counter() - t0) * 1000) - ocr_ms, {"pages": out}
    except Exception as e:
        return -1, 0, 0, int((time.perf_counter() - t0) * 1000), {"pages": [], "err": str(e)[:200]}


def get_many(names, ocr=True):
    """Worker-side: [(name, pages, layout)] plus rows not cached yet (to be stored by the parent)."""
    global _con
    if _con is None:
        _con = connect(readonly=True)
    have = {}
    for i in range(0, len(names), 400):
        chunk = names[i:i + 400]
        q = "SELECT name, pages, layout FROM docs WHERE name IN (%s)" % ",".join("?" * len(chunk))
        try:
            for name, pages, blob in _con.execute(q, chunk):
                lay = json.loads(zlib.decompress(blob))
                if ocr and any(pg["src"] in ("needs_ocr", "empty") or (pg["src"] == "text" and pg.get("img")
                                                                         and sum(len(l[6]) for l in pg["lines"]) < 200)
                               for pg in lay.get("pages", [])):
                    continue                       # cached without OCR: re-read this one with OCR now
                have[name] = (pages, lay)
        except sqlite3.OperationalError:
            break
    out, new = [], []
    for name in names:
        hit = have.get(name)
        if hit is None:
            pages, ocr_pages, ocr_ms, read_ms, lay = read_pdf(name, ocr)
            blob = zlib.compress(json.dumps(lay, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 3)
            new.append((name, pages, ocr_pages, ocr_ms, read_ms, blob))
            hit = (pages, lay)
        out.append((name, hit[0], hit[1]))
    return out, new


def store(con, rows):
    if rows:
        con.executemany("INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?)", rows)
        con.commit()
