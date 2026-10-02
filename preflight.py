"""Checks to run before the full two-pass job. Reads at most 25 PDFs.

    python preflight.py            # environment checks + a 25-file end-to-end smoke test
    python preflight.py --no-smoke # environment checks only
"""
import os
import shutil
import subprocess
import sys

ok = True


def check(label, good, detail=""):
    global ok
    ok = ok and bool(good)
    print(f"[{'ok' if good else 'FAIL'}] {label}{(' - ' + str(detail)) if detail else ''}")


def main():
    check("Python 3.11+", sys.version_info >= (3, 11), sys.version.split()[0])
    check("running from the project folder (model.joblib present)", os.path.exists("model.joblib"), os.getcwd())
    try:
        import joblib, numpy, pymupdf, sklearn                                      # noqa: F401
        check("packages import", True, f"pymupdf {pymupdf.__version__}, scikit-learn {sklearn.__version__}")
    except Exception as e:
        check("packages import (pip install -r requirements.txt)", False, e)
        return
    try:
        import ocr
        check("Windows OCR engine (English language pack)", ocr._get_engine() is not None)
    except Exception as e:
        check("Windows OCR engine", False, e)
    from extract import PDF_DIR
    from textcache import CACHE_DIR
    import enrich
    check("PDF folder exists (LWIZ_PDF_DIR)", os.path.isdir(PDF_DIR), PDF_DIR)
    check("scraper database exists (LWIZ_SCRAPE_DB)", os.path.exists(enrich.SCRAPE_DB), enrich.SCRAPE_DB)
    os.makedirs(CACHE_DIR, exist_ok=True)
    free = shutil.disk_usage(CACHE_DIR).free / 1e9
    check("cache/output drive has 10 GB free (LWIZ_CACHE)", free >= 10, f"{CACHE_DIR}: {free:.0f} GB free (needs ~6 GB)")
    if not ok or "--no-smoke" in sys.argv or not os.path.isdir(PDF_DIR):
        return
    names = []
    with os.scandir(PDF_DIR) as it:
        for e in it:
            if e.name.lower().endswith(".pdf"):
                names.append(e.name)
                if len(names) == 25:
                    break
    out = os.path.join(CACHE_DIR, "smoke_test")
    r = subprocess.run([sys.executable, "ingest.py", "--names", *names, "--out", out], capture_output=True, text=True)
    print(r.stdout.strip()[-600:])
    good = r.returncode == 0 and os.path.exists(os.path.join(out, "run_manifest.json"))
    check("25-file smoke test", good, out if good else r.stderr.strip()[-600:])


if __name__ == "__main__":
    main()
    print("\nREADY" if ok else "\nNOT READY - fix the FAIL lines above")
    sys.exit(0 if ok else 1)
