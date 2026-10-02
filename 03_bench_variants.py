"""Throughput variants on fresh (cold-cache) samples: worker counts, contiguous block, warm rerun."""
import os

from classify import classify
from extract import list_pdfs, split_samples

if __name__ == "__main__":
    total, (_t, bench, s6, s24) = split_samples(sizes=(4000, 3000, 1500, 1500))
    out = os.devnull
    for label, names, workers in [("cold random, 6 workers", s6, 6),
                                  ("cold random, 24 workers", s24, 24)]:
        dt, _ = classify(names, out, workers)
        print(f"{label}: {len(names) / dt:.1f} docs/sec")
    # contiguous run of files in name order, as a full pass would read them
    names = list_pdfs()
    for start in (50_000, 150_000):
        block = names[start:start + 1500]
        dt, _ = classify(block, out, 12)
        print(f"cold contiguous block @{start}, 12 workers: {len(block) / dt:.1f} docs/sec")
    dt, _ = classify(bench, out, 12)
    print(f"warm (OS-cached) rerun of bench sample, 12 workers: {len(bench) / dt:.1f} docs/sec")
