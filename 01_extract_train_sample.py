"""Extract the head text of the training sample to train_sample.jsonl (and time it)."""
import json
import time

from extract import WORKERS, extract_many, split_samples

if __name__ == "__main__":
    total, (train, _bench) = split_samples()
    t0 = time.perf_counter()
    with open("train_sample.jsonl", "w", encoding="utf-8") as f:
        for name, n, text in extract_many(train):
            f.write(json.dumps({"name": name, "pages": n, "text": text}) + "\n")
    dt = time.perf_counter() - t0
    print(f"total_pdfs={total} sample={len(train)} workers={WORKERS} "
          f"secs={dt:.1f} docs_per_sec={len(train) / dt:.1f}")
