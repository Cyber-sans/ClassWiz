"""End-to-end classifier: PDF -> head text (parallel) -> hashed n-grams -> linear model.

    python classify.py --bench            # held-out random sample, timed, writes results_bench.csv
    python classify.py --all              # whole folder (NOT run as part of the proof of concept)
"""
import argparse
import collections
import csv
import time

import joblib
import numpy as np

from extract import WORKERS, extract_many, list_pdfs, split_samples
from features import MIN_TEXT, doc_string, make_vectorizer

THRESHOLD = 0.60      # below this top-class probability the document is UNKNOWN
BATCH = 512


def classify(names, out_csv, workers=WORKERS):
    clf = joblib.load("model.joblib")
    vec = make_vectorizer()
    classes = clf.classes_
    counts = collections.Counter()
    t0 = time.perf_counter()
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "label", "confidence", "pages", "has_text", "snippet"])

        def flush(batch):
            proba = clf.predict_proba(vec.transform(doc_string(n, t) for n, _, t in batch))
            top = proba.argmax(1)
            for (name, pages, text), k, p in zip(batch, top, proba[np.arange(len(batch)), top]):
                label = classes[k] if pages >= 0 and p >= THRESHOLD else "UNKNOWN"
                counts[label] += 1
                w.writerow([name, label, f"{p:.3f}", pages, int(len(text) >= MIN_TEXT), text[:300]])

        batch = []
        for rec in extract_many(names, workers):
            batch.append(rec)
            if len(batch) >= BATCH:
                flush(batch)
                batch = []
        if batch:
            flush(batch)
    return time.perf_counter() - t0, counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bench", action="store_true")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--out")
    a = ap.parse_args()

    if a.bench:
        total, (_train, names) = split_samples()
        out = a.out or "results_bench.csv"
    else:
        names = list_pdfs()
        total, out = len(names), a.out or "results_all.csv"
    dt, counts = classify(names, out, a.workers)
    rate = len(names) / dt
    print(f"docs={len(names)} workers={a.workers} secs={dt:.1f} docs_per_sec={rate:.1f}")
    print(f"total_pdfs={total} est_full_run_minutes={total / rate / 60:.1f}")
    for k, v in counts.most_common():
        print(f"  {k:22s} {v:6d} {v / len(names):6.1%}")
    print("wrote", out)
