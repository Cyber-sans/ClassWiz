"""Classify + extract metadata, reading each PDF at most once (text cache on the SSD).

    python run_meta.py --bench     # fresh random sample: cold pass, then cached pass, timed
    python run_meta.py --dev       # the 4,000-file dev sample (already cached)
    python run_meta.py --all       # whole folder (NOT run as part of the proof of concept)
"""
import argparse
import collections
import json
import re
import time
from concurrent.futures import ProcessPoolExecutor

import joblib
import numpy as np

import meta
import textcache
from classify import THRESHOLD
from extract import WORKERS, list_pdfs, split_samples
from features import doc_string, make_vectorizer

CHUNK = 64
_WS = re.compile(r"\s+")
_clf = _vec = None


def _init():
    global _clf, _vec
    textcache._init_worker()
    _clf, _vec = joblib.load("model.joblib"), make_vectorizer()


def classifier_text(text):
    """Same view the model was trained on: first two pages, whitespace-collapsed, 3,000 chars."""
    pages, _ = textcache.split_text(text)
    parts, size = [], 0
    for p in pages[:2]:
        parts.append(p)
        size += len(p)
        if size >= 3000:
            break
    return _WS.sub(" ", " ".join(parts))[:3000].strip()


def process(names):
    docs, new = textcache.get_many(names)
    proba = _clf.predict_proba(_vec.transform(doc_string(n, classifier_text(t)) for n, _, t in docs))
    top = proba.argmax(1)
    out = []
    for (name, pages, text), k, p in zip(docs, top, proba[np.arange(len(docs)), top]):
        label = _clf.classes_[k] if pages >= 0 and p >= THRESHOLD else "UNKNOWN"
        rec = meta.extract(name, pages, text, label)
        rec["type_confidence"] = round(float(p), 3)
        out.append(json.dumps(rec, ensure_ascii=False))
    return out, new


def run(names, out_path, workers=WORKERS):
    con = textcache.connect(readonly=False)
    chunks = [names[i:i + CHUNK] for i in range(0, len(names), CHUNK)]
    t0 = time.perf_counter()
    read = 0
    with ProcessPoolExecutor(workers, initializer=_init) as ex, open(out_path, "w", encoding="utf-8") as f:
        for recs, new in ex.map(process, chunks):
            textcache.store(con, new)
            read += len(new)
            f.write("\n".join(recs) + "\n")
    con.close()
    return time.perf_counter() - t0, read


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bench", action="store_true")
    g.add_argument("--dev", action="store_true")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=WORKERS)
    a = ap.parse_args()

    if a.all:
        names = list_pdfs()
        total, out = len(names), "results_meta_all.jsonl"
    else:
        total, (dev, _b, _s6, _s24, bench) = split_samples(sizes=(4000, 3000, 1500, 1500, 3000))
        names, out = (dev, "results_meta_dev.jsonl") if a.dev else (bench, "results_meta_bench.jsonl")

    dt, read = run(names, out, a.workers)
    kind = "cold, every PDF read from disk" if read == len(names) else f"{read} PDFs read, rest from cache"
    print(f"pass 1 ({kind}): docs={len(names)} secs={dt:.1f} docs_per_sec={len(names) / dt:.1f}")
    if read == len(names):
        print(f"total_pdfs={total}  est_first_full_run={total / (len(names) / dt) / 60:.0f} min")
    if a.bench:
        dt2, read2 = run(names, out, a.workers)
        print(f"pass 2 (cached): pdfs_read={read2} secs={dt2:.2f} docs_per_sec={len(names) / dt2:.0f} "
              f"(includes ~2 s worker start-up; see 04_bench_cached.py for the steady-state rate)")
    counts = collections.Counter(json.loads(l)["document_type"] for l in open(out, encoding="utf-8"))
    print(dict(counts), "->", out)
