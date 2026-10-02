"""Summarise a finished ingestion run: totals, failures, and the rights / hosting breakdown.

    python report_run.py                 # the default full-run folder (<cache>\\ingest_all)
    python report_run.py some_folder

Writes run_report.json, errors.csv (every file that failed or produced an error) and hosting.csv
(one line per document: file, collection, year, hosting category, rights class/basis/confidence, restriction).
"""
import collections
import csv
import json
import os
import sys

import ingest
from textcache import CACHE_DIR


def main(out_dir):
    C = collections.Counter
    n, types, host, rcls, restr, coll, errors = 0, C(), C(), C(), C(), C(), []
    ocr_docs = ocr_pages = unresolved = 0
    with open(os.path.join(out_dir, "hosting.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "collection", "year", "document_type", "hosting", "rights_class", "rights_basis", "rights_confidence", "restriction"])
        for r in ingest.read_records(out_dir):
            n += 1
            types[r.get("document_type")] += 1
            if r.get("error"):
                errors.append((r["source_filename"], r["error"]))
            if "hosting" not in r:
                continue
            h, rt = r["hosting"], r["rights"]
            code = (r.get("provenance") or {}).get("bailii_database")
            host[h["category"]] += 1
            rcls[(rt["class"], rt["basis"], rt["confidence"])] += 1
            restr[h["restriction_class"]] += 1
            coll[(code, h["category"])] += 1
            ts = r.get("text_source") or {}
            ocr_docs += ts.get("ocr_pages", 0) > 0
            ocr_pages += ts.get("ocr_pages", 0)
            unresolved += ts.get("needs_ocr_pages", 0) > 0
            w.writerow([r["source_filename"], code, r.get("year"), r.get("document_type"), h["category"], rt["class"], rt["basis"], rt["confidence"],
                        h["restriction_class"]])
    manifest = {}
    for f in ("run_manifest_cache.json", "run_manifest.json"):
        p = os.path.join(out_dir, f)
        if os.path.exists(p):
            manifest[f] = json.load(open(p, encoding="utf-8"))
    failed = [x["file"] for m in manifest.values() for x in m.get("failed_files", [])]
    with open(os.path.join(out_dir, "errors.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "error"])
        w.writerows(sorted(set(errors)))
    report = {"records": n, "document_types": dict(types), "records_with_error": len(errors), "worker_failures": failed,
              "documents_with_ocr": ocr_docs, "ocr_pages": ocr_pages, "documents_with_pages_still_needing_ocr": unresolved,
              "hosting": dict(host), "restriction": dict(restr),
              "rights": {" / ".join(k): v for k, v in rcls.most_common()},
              "hosting_by_collection": {f"{k[0]} | {k[1]}": v for k, v in sorted(coll.items(), key=lambda t: -t[1])},
              "manifests": manifest}
    with open(os.path.join(out_dir, "run_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print(f"records: {n}   with error: {len(errors)}   worker failures: {len(failed)}   documents OCRed: {ocr_docs} ({ocr_pages} pages)   "
          f"still needing OCR: {unresolved}")
    print("document types:", dict(types))
    for k, v in host.most_common():
        print(f"  {k:28s} {v:7d}  {100 * v / max(sum(host.values()), 1):5.1f}%")
    print("restrictions:", dict(restr))
    print("written: run_report.json, errors.csv, hosting.csv in", out_dir)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(CACHE_DIR, "ingest_all"))
