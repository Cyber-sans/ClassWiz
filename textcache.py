"""Read-once PDF text cache on the SSD (SQLite, zlib-compressed text).

Each PDF is opened at most once: the head pages and the last page are extracted with line
breaks kept (pages separated by form feeds) and stored. Every later pass - classification,
metadata extraction, re-runs after regex changes - reads only the cache.
"""
import os
import sqlite3
import zlib

import pymupdf

from extract import PDF_DIR

CACHE_DIR = os.environ.get("LWIZ_CACHE") or os.path.join(os.environ["LOCALAPPDATA"], "lwiz_classifier")
CACHE_DB = os.path.join(CACHE_DIR, "textcache.sqlite")

HEAD_PAGES = 5
HEAD_CHARS = 15000
TAIL_CHARS = 3000
PAGE_SEP = "\f"
TAIL_SEP = "\f\x00TAIL\f"      # separates head pages from the text of the final page

_con = None


def _init_worker():
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)


def connect(readonly=True):
    os.makedirs(CACHE_DIR, exist_ok=True)
    if readonly and os.path.exists(CACHE_DB):
        return sqlite3.connect(f"file:{CACHE_DB}?mode=ro", uri=True)
    con = sqlite3.connect(CACHE_DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("CREATE TABLE IF NOT EXISTS docs (name TEXT PRIMARY KEY, pages INTEGER, text BLOB) WITHOUT ROWID")
    con.commit()
    return con


def read_pdf(name):
    """(pages, text). pages == -1 if the file cannot be opened."""
    try:
        with pymupdf.open(os.path.join(PDF_DIR, name)) as doc:
            n = doc.page_count
            parts, size, i = [], 0, 0
            while i < min(n, HEAD_PAGES) and size < HEAD_CHARS:
                t = doc[i].get_text("text")
                parts.append(t)
                size += len(t)
                i += 1
            text = PAGE_SEP.join(parts)[:HEAD_CHARS]
            if i < n:
                text += TAIL_SEP + doc[n - 1].get_text("text")[-TAIL_CHARS:]
        return n, text
    except Exception:
        return -1, ""


def get_many(names):
    """Worker-side: [(name, pages, text)] for names, plus the rows that were not cached yet."""
    global _con
    if _con is None:
        _con = connect(readonly=True)
    have = {}
    for i in range(0, len(names), 500):
        chunk = names[i:i + 500]
        q = "SELECT name, pages, text FROM docs WHERE name IN (%s)" % ",".join("?" * len(chunk))
        try:
            for name, pages, blob in _con.execute(q, chunk):
                have[name] = (pages, zlib.decompress(blob).decode("utf-8"))
        except sqlite3.OperationalError:      # table not created yet
            break
    out, new = [], []
    for name in names:
        hit = have.get(name)
        if hit is None:
            hit = read_pdf(name)
            new.append((name, hit[0], zlib.compress(hit[1].encode("utf-8"), 1)))
        out.append((name, hit[0], hit[1]))
    return out, new


def store(con, rows):
    if rows:
        con.executemany("INSERT OR REPLACE INTO docs VALUES (?,?,?)", rows)
        con.commit()


def split_text(text):
    """-> (head_pages: list[str], tail: str)"""
    head, _, tail = text.partition(TAIL_SEP)
    return head.split(PAGE_SEP), tail
