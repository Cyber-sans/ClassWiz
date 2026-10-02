"""Full ingestion: PDF -> layout cache (read once, OCR for scans) -> structured content + metadata.

Two-pass run (recommended):
    python ingest.py --all --cache-only --no-ocr   # pass 1: read every PDF once into the SSD cache (disk-bound)
    python ingest.py --all                         # pass 2: OCR the scans, then build every record from the cache
Both passes can be re-run after an interruption: pass 1 skips files already cached, pass 2 re-OCRs nothing.

    python ingest.py --slice 7 ...      # the fresh 10,000-file benchmark sample
    python ingest.py --names a.pdf ...  # specific files

Output: gzip JSONL shards (one record per document) plus run_manifest.json under --out.
"""
import argparse
import collections
import gzip
import json
import os
import re
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

import joblib
import numpy as np

import casefix
import enrich
import fixups
import layout
import meta
import rights
import structure
from classify import THRESHOLD
from extract import WORKERS, list_pdfs, split_samples
from features import doc_string, make_vectorizer
from textcache import CACHE_DIR
from weak_labels import _REPORT_FN

CHUNK = 48
SHARD_DOCS = 4800
SAMPLE_SIZES = (4000, 3000, 1500, 1500, 3000, 3000, 3000, 10000, 1200, 600)   # slice 7 = 10,000-file benchmark, 8 = blind-review set, 9 = final check
TASKS_PER_CHILD = 150          # recycle workers so a slow leak in a native library cannot grow for hours
_WS = re.compile(r"\s+")
_clf = _vec = None
_OCR = True
_CACHE_ONLY = False


def _init(ocr, cache_only=False):
    global _clf, _vec, _OCR, _CACHE_ONLY
    layout.init_worker()
    _OCR, _CACHE_ONLY = ocr, cache_only
    if not cache_only:
        _clf, _vec = joblib.load("model.joblib"), make_vectorizer()


def _classifier_text(pages, page_texts):
    """Same view the model was trained on: first two pages of the text layer (scans had none)."""
    parts, size = [], 0
    for pg, t in zip(pages[:2], page_texts[:2]):
        if pg["src"] != "text":
            continue
        parts.append(t)
        size += len(t)
        if size >= 3000:
            break
    return _WS.sub(" ", " ".join(parts))[:3000].strip()


def build_record(name, npages, lay, prov_rows, label, conf):
    pages = lay.get("pages", [])
    page_texts = structure.native_text(lay)
    srcs = collections.Counter(pg["src"] for pg in pages)
    type_source = "model"
    if label == "LAW_REPORT" and not _REPORT_FN.match(name) and prov_rows and (prov_rows[0][2] or "0") >= "1950":
        label, type_source = "CASE_JUDGMENT", "model, corrected by source path"
    elif label == "UNKNOWN" and prov_rows and npages >= 0:
        label, type_source = ("LAW_REPORT" if _REPORT_FN.match(name) else "CASE_JUDGMENT"), "source path (model unsure)"
    content, full_text = structure.build(lay, label)
    body = [b for b in content["blocks"] if b.get("z") != "header" and b["t"] not in ("table", "marginal_note", "footnote")]
    tail = " ".join(b["text"] for b in body[-14:])[-3500:]
    rec = meta.extract(name, npages, "\f".join(page_texts), label, tail_text=tail or None)
    rec["type_confidence"], rec["type_source"] = round(float(conf), 3), type_source
    code, jcode = enrich.apply_provenance(rec, prov_rows)
    rec = fixups.apply(rec, page_texts, content, bool(pages) and pages[0]["src"] == "ocr")
    rec["_ocr_first"] = bool(pages) and pages[0]["src"] == "ocr"
    rec, content, full_text = casefix.apply(rec, content, full_text)
    head = _WS.sub(" ", " ".join(page_texts[:2]))
    last = _WS.sub(" ", page_texts[-1] if page_texts else "")
    rcode = code
    if code == "Misc":                                   # BAILII's catch-all database: the file name says which court it is
        m = re.match(r"(NICoroner|NIMag)", name)
        rcode = m.group(1) if m else code
    rec["rights"] = rights.classify(rcode, jcode, rec.get("year"), head, last, label, rec.get("report_series"))
    rec["restrictions"] = enrich.restrictions(head[:6000] + " " + last[-2500:], rec.get("parties"), _WS.sub(" ", " ".join(page_texts)))
    rec["hosting"] = rights.hosting(rec["rights"], rec["restrictions"])
    rec["subject_tags"] = enrich.subject_tags(full_text, casefix.tag_words(rec))
    rec.pop("note", None)
    rec["page_count"] = rec.pop("pages")
    rec["source_filename"] = rec.pop("file")
    rec["has_text_layer"] = srcs.get("text", 0) > 0
    rec["text_source"] = {"text_layer_pages": srcs.get("text", 0), "ocr_pages": srcs.get("ocr", 0),
                          "needs_ocr_pages": srcs.get("needs_ocr", 0), "empty_pages": srcs.get("empty", 0),
                          "scanned_with_embedded_text": bool(pages and pages[0]["src"] == "text" and pages[0].get("img"))}
    if lay.get("err"):
        rec["error"] = lay["err"]
    rec["content"] = content
    rec["full_text"] = full_text
    return rec


def process(job):
    names = [n for n, _ in job]
    if os.environ.get("LWIZ_TEST_CRASH") in names:      # fault injection, used only by the recovery test
        os._exit(3)
    docs, new = layout.get_many(names, _OCR)
    stats = collections.Counter()
    for row in new:
        stats["ocr_pages"] += max(row[2], 0)
        stats["ocr_ms"] += row[3]
        stats["read_ms"] += row[4]
        stats["unreadable"] += row[1] < 0
    stats["pdfs_read"] = len(new)
    stats["pages"] = sum(max(p, 0) for _, p, _ in docs)
    if _CACHE_ONLY:
        return None, new, stats, len(names)
    texts = [doc_string(n, _classifier_text(lay.get("pages", []), structure.native_text(lay))) for n, _, lay in docs]
    proba = _clf.predict_proba(_vec.transform(texts))
    top = proba.argmax(1)
    lines = []
    for (name, npages, lay), (_, prov), k, p in zip(docs, job, top, proba[np.arange(len(docs)), top]):
        label = _clf.classes_[k] if npages >= 0 and p >= THRESHOLD else "UNKNOWN"
        try:
            rec = build_record(name, npages, lay, prov, label, p)
        except Exception as e:                                     # never lose a document to a rule bug
            rec = {"source_filename": name, "document_type": label, "page_count": npages, "error": f"{type(e).__name__}: {e}"}
            stats["errors"] += 1
        stats["type:" + rec["document_type"]] += 1
        stats["blocks"] += len(rec.get("content", {}).get("blocks", []))
        stats["chars"] += len(rec.get("full_text", ""))
        lines.append(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
    blob = gzip.compress(("\n".join(lines) + "\n").encode("utf-8"), 4)
    return blob, new, stats, len(names)


class _Shards:
    def __init__(self, out_dir):
        self.dir, self.n, self.in_shard, self.fh, self.bytes = out_dir, 0, 0, None, 0

    def write(self, blob, docs):
        if self.fh is None or self.in_shard >= SHARD_DOCS:
            self.close()
            self.fh = open(os.path.join(self.dir, f"part-{self.n:05d}.jsonl.gz"), "wb")
            self.n, self.in_shard = self.n + 1, 0
        self.fh.write(blob)                              # gzip members concatenate into a valid gzip file
        self.bytes += len(blob)
        self.in_shard += docs

    def close(self):
        if self.fh:
            self.fh.close()
            self.fh = None


def run(names, out_dir, workers=WORKERS, ocr=True, prov=None, cache_only=False, quiet=False):
    """Crash-safe: a worker killed by a bad PDF costs only that file, which is isolated and reported."""
    prov = prov if prov is not None else ({} if cache_only else enrich.load_provenance())
    os.makedirs(out_dir, exist_ok=True)
    con = layout.connect(readonly=False)
    skipped = 0
    if cache_only:                                       # resume: leave alone what is already cached
        have = {r[0] for r in con.execute("SELECT name FROM docs")}
        skipped = sum(1 for n in names if n in have)
        names = [n for n in names if n not in have]
    else:
        for f in os.listdir(out_dir):
            if f.startswith("part-") and f.endswith(".jsonl.gz"):
                os.remove(os.path.join(out_dir, f))
    pending = collections.deque([(n, prov.get(n)) for n in names[i:i + CHUNK]] for i in range(0, len(names), CHUNK))
    shards, stats, suspects, failed = _Shards(out_dir), collections.Counter(), [], []
    total, done, t0, last = len(names), 0, time.perf_counter(), time.perf_counter()

    def accept(result):
        nonlocal done, last
        blob, new, st, n = result
        layout.store(con, new)
        stats.update(st)
        if blob is not None:
            shards.write(blob, n)
        done += n
        if not quiet and time.perf_counter() - last > 30:
            last = time.perf_counter()
            rate = done / (last - t0)
            print(f"  {done}/{total} docs  {rate:.1f}/s  ETA {(total - done) / max(rate, 0.1) / 60:.0f} min  "
                  f"ocr_pages={stats['ocr_pages']} crashes={stats['pool_crashes']}", flush=True)

    while pending:
        inflight = {}
        try:
            with ProcessPoolExecutor(workers, initializer=_init, initargs=(ocr, cache_only),
                                     max_tasks_per_child=TASKS_PER_CHILD) as ex:
                while pending or inflight:
                    while pending and len(inflight) < workers * 3:
                        job = pending.popleft()
                        inflight[ex.submit(process, job)] = job
                    finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
                    for fut in finished:
                        job = inflight[fut]
                        try:
                            result = fut.result()
                        except BrokenProcessPool:
                            raise
                        except Exception as e:           # a Python-level failure of the whole chunk
                            suspects.extend(job)
                            stats["chunk_errors"] += 1
                        else:
                            accept(result)
                        del inflight[fut]
        except BrokenProcessPool:                        # a worker died (native crash / out of memory)
            stats["pool_crashes"] += 1
            for job in inflight.values():
                suspects.extend(job)
    # files that were in flight during a crash: retry one at a time so the culprit is identified exactly
    solo = None
    for item in suspects:
        if solo is None:
            solo = ProcessPoolExecutor(1, initializer=_init, initargs=(ocr, cache_only))
        try:
            accept(solo.submit(process, [item]).result(timeout=900))
        except Exception as e:                           # this file is the one that kills the worker (or hangs it)
            failed.append({"file": item[0], "error": type(e).__name__})
            solo.shutdown(wait=False, cancel_futures=True)
            solo = None
            if not cache_only:
                line = json.dumps({"source_filename": item[0], "document_type": "UNKNOWN", "page_count": -1,
                                   "error": f"worker failed: {type(e).__name__}"})
                shards.write(gzip.compress((line + "\n").encode("utf-8")), 1)
                done += 1
    if solo is not None:
        solo.shutdown()
    shards.close()
    con.close()
    dt = time.perf_counter() - t0
    stats["out_bytes"], stats["skipped_cached"], stats["failed"] = shards.bytes, skipped, len(failed)
    manifest = {"docs": total, "seconds": round(dt, 1), "docs_per_sec": round(total / dt, 2) if dt else None, "ocr": ocr,
                "cache_only": cache_only, "workers": workers, "stats": dict(stats), "failed_files": failed,
                "finished": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(out_dir, "run_manifest_cache.json" if cache_only else "run_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    return dt, stats


def read_records(out_dir):
    for f in sorted(os.listdir(out_dir)):
        if f.startswith("part-") and f.endswith(".jsonl.gz"):
            with gzip.open(os.path.join(out_dir, f), "rt", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        yield json.loads(line)


def report(label, n, dt, st):
    print(f"{label}: docs={n} secs={dt:.1f} docs_per_sec={n / dt:.1f} pdfs_read={st['pdfs_read']} pages={st['pages']} "
          f"ocr_pages={st['ocr_pages']} ocr_cpu_s={st['ocr_ms'] / 1000:.0f} read_cpu_s={st['read_ms'] / 1000:.0f} "
          f"rule_errors={st['errors']} unreadable={st['unreadable']} crashes={st['pool_crashes']} failed={st['failed']} "
          f"skipped_cached={st['skipped_cached']} out_MB={st['out_bytes'] / 1e6:.1f}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--names", nargs="+")
    g.add_argument("--slice", type=int, help="index of a sample slice (7 = fresh 10,000-file benchmark)")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--no-ocr", action="store_true", help="mark image-only pages instead of OCRing them")
    ap.add_argument("--cache-only", action="store_true", help="pass 1: fill the layout cache, write no records")
    ap.add_argument("--out")
    a = ap.parse_args()

    if a.all:
        names, out = list_pdfs(), a.out or os.path.join(CACHE_DIR, "ingest_all")
    elif a.names:
        names, out = a.names, a.out or "ingest_adhoc"
    else:
        total, slices = split_samples(sizes=SAMPLE_SIZES)
        names, out = slices[a.slice], a.out or f"ingest_slice{a.slice}"
    prov = {} if a.cache_only else enrich.load_provenance()
    dt, st = run(names, out, a.workers, not a.no_ocr, prov, a.cache_only)
    report("cache pass" if a.cache_only else "build pass", len(names), dt, st)
    print({k: v for k, v in st.items() if k.startswith("type:")}, "->", out)
