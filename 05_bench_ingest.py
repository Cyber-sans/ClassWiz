"""Ingest benchmarks beyond `ingest.py --bench`:
   ocr     - OCR throughput (pages/sec) at different worker counts, on scanned files from the sample
   profile - where the cached pass spends its time (single process)
"""
import collections
import cProfile
import json
import pstats
import sqlite3
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor

import layout


def _ocr_one(name):
    pages, ocr_pages, ocr_ms, read_ms, _ = layout.read_pdf(name, ocr=True)
    return ocr_pages, ocr_ms


def scanned_names(limit):
    con = layout.connect()
    rows = con.execute("SELECT name, ocr_pages FROM docs WHERE ocr_pages > 0 ORDER BY name").fetchall()
    step = max(1, len(rows) // limit)
    return [r[0] for r in rows[::step]][:limit]


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "ocr":
        names = scanned_names(120)
        for workers in (3, 6, 9, 12):
            t0 = time.perf_counter()
            with ProcessPoolExecutor(workers, initializer=layout.init_worker) as ex:
                res = list(ex.map(_ocr_one, names, chunksize=2))
            dt = time.perf_counter() - t0
            pages = sum(r[0] for r in res)
            cpu = sum(r[1] for r in res) / 1000
            print(f"workers={workers:2d}: {pages} pages in {dt:.1f}s -> {pages / dt:.1f} pages/sec, "
                  f"{cpu / pages * 1000:.0f} ms/page inside the OCR call")
    else:
        import ingest
        import enrich
        from extract import split_samples
        total, slices = split_samples(sizes=ingest.SAMPLE_SIZES)
        names = slices[5][:300]
        prov = enrich.load_provenance()
        ingest._init(True)
        job = [(n, prov.get(n)) for n in names]
        pr = cProfile.Profile()
        pr.enable()
        t0 = time.perf_counter()
        ingest.process(job)
        dt = time.perf_counter() - t0
        pr.disable()
        print(f"{len(names)} cached docs in {dt:.2f}s single process -> {dt / len(names) * 1000:.1f} ms/doc")
        pstats.Stats(pr).sort_stats("cumulative").print_stats(28)
