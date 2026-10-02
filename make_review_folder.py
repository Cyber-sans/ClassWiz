"""Copy 20 varied documents from the 10k benchmark into review_samples/ with their pipeline output.
Read-only with respect to the extractor: it only reads ingest_10k and the source PDFs."""
import csv
import json
import os
import random
import shutil

import ingest
from extract import PDF_DIR

OUT = "review_samples"
recs = sorted((r for r in ingest.read_records("ingest_10k") if "content" in r), key=lambda r: r["source_filename"])


def code(r):
    return r.get("provenance", {}).get("bailii_database") or ""


def counts(r):
    return r["content"]["block_counts"]


ocr = lambda r: r["text_source"]["ocr_pages"] > 0
CATS = [  # (label, how many, predicate)
    ("digital court judgment", 3, lambda r: not ocr(r) and code(r).startswith(("EWHC", "EWCA", "IEHC", "IECA", "UKSC")) and r["page_count"] >= 8),
    ("digital tribunal decision (long)", 2, lambda r: not ocr(r) and code(r) in ("UKAITUR", "UKFTT/TC", "UKEAT") and r["page_count"] >= 6),
    ("short tribunal decision", 4, lambda r: not ocr(r) and code(r) in ("UKET", "NIIT", "UKAITUR", "UKFTT/PC") and r["page_count"] <= 2),
    ("regulator decision", 2, lambda r: not ocr(r) and code(r) in ("UKICO", "UKIntelP", "ScotIC")),
    ("scanned judgment (OCR)", 2, lambda r: ocr(r) and r["document_type"] == "CASE_JUDGMENT"),
    ("scanned law report (OCR)", 2, lambda r: ocr(r) and r["document_type"] == "LAW_REPORT"),
    ("law report with text layer", 3, lambda r: not ocr(r) and r["document_type"] == "LAW_REPORT"),
    ("structurally complex (tables, lists, footnotes)", 2,
     lambda r: not ocr(r) and counts(r).get("table", 0) >= 2 and counts(r).get("list_item", 0) >= 10 and r["page_count"] >= 10),
]
rng = random.Random(2026)
picked, used = [], set()
for label, k, pred in CATS:
    pool = [r for r in recs if pred(r) and r["source_filename"] not in used]
    rng.shuffle(pool)
    for r in pool[:k]:
        used.add(r["source_filename"])
        picked.append((label, r))

if os.path.isdir(OUT):
    shutil.rmtree(OUT)
os.makedirs(os.path.join(OUT, "pdf"))
os.makedirs(os.path.join(OUT, "output"))
rows = []
for i, (label, r) in enumerate(picked, 1):
    name = r["source_filename"]
    shutil.copy2(os.path.join(PDF_DIR, name), os.path.join(OUT, "pdf", name))
    out_name = name[:-4] + ".json"
    with open(os.path.join(OUT, "output", out_name), "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    rows.append({"n": i, "category": label, "pdf": f"pdf/{name}", "output": f"output/{out_name}",
                 "document_type": r["document_type"], "type_confidence": r.get("type_confidence"),
                 "type_source": r.get("type_source"), "pages": r["page_count"], "ocr_pages": r["text_source"]["ocr_pages"],
                 "court": r.get("court"), "title": r.get("title"), "date": r.get("judgment_date") or r.get("date"),
                 "source_url": r.get("source_url")})
with open(os.path.join(OUT, "manifest.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=1)
for x in rows:
    print(f"{x['n']:2d} {x['category'][:34]:34s} {x['pdf'][4:32]:28s} {x['document_type']:13s} {x['type_confidence']} pp={x['pages']} ocr={x['ocr_pages']} | {str(x['title'])[:50]}")
