# Running the full corpus job

Two passes over 231,399 PDFs. Pass 1 reads every PDF once into a cache on the SSD. Pass 2 OCRs the scans and
builds one JSON record per document from the cache. Nothing is uploaded anywhere and no PDF is modified.

## What you need

- Windows 10/11 with the English OCR language pack (installed by default on an English Windows).
- Python 3.13 (3.11+ works).
- This whole project folder, including `model.joblib` and `pc_bench.txt` (both are loaded at run time).
- The PDF folder (49.8 GB, flat) and `bailii_cases.db`.
- About 6 GB free on an SSD for the cache and the output.

## Setup (once)

Open PowerShell in the project folder.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Only if the data is not at the original paths, point the job at it (same PowerShell window, before running anything):

```bash
$env:LWIZ_PDF_DIR = "E:\path\to\downloaded\pdf"
```

```bash
$env:LWIZ_SCRAPE_DB = "E:\path\to\bailii_cases.db"
```

```bash
$env:LWIZ_CACHE = "C:\lwiz_cache"
```

`LWIZ_CACHE` is where the cache and the output go. Default: `%LOCALAPPDATA%\lwiz_classifier`. Keep it on an SSD.

Check everything (reads 25 PDFs, takes under a minute). It must end with `READY`.

```bash
.venv\Scripts\python.exe preflight.py
```

## The run

Pass 1, read every PDF into the cache (about 1.8 hours from a hard disk; limited by the disk, not the CPU):

```bash
.venv\Scripts\python.exe ingest.py --all --cache-only --no-ocr
```

Pass 2, OCR the scans and build all records (about 1.9 hours: ~155,000 scanned pages at ~27 pages/s, then ~18 minutes of record building):

```bash
.venv\Scripts\python.exe ingest.py --all
```

Summary and error list:

```bash
.venv\Scripts\python.exe report_run.py
```

A progress line prints every 30 seconds with the count, rate, ETA, OCR pages and crash count. Leave the machine awake
(disable sleep for the duration). Run the commands from the project folder: the model file is loaded from the current directory.

## If it stops (power cut, Ctrl+C, crash)

Run the same command again.

- Pass 1 skips every file already in the cache and carries on.
- Pass 2 keeps all OCR already done (it is saved to the cache as it goes), OCRs only what is left, then rebuilds the
  records. The record build always starts over; that part is only ~18 minutes.
- A PDF that crashes a worker process does not stop the run. The files that were in flight are retried one at a time,
  the culprit is recorded, and everything else continues.

## Where things end up

All under the cache folder (`%LOCALAPPDATA%\lwiz_classifier` unless `LWIZ_CACHE` is set):

| Path | What |
|---|---|
| `layout.sqlite` | the read-once cache, ~3 GB when full. Keep it: any rule change can be re-applied from it in ~18 minutes without touching the PDFs |
| `ingest_all\part-00000.jsonl.gz` … | the records, 4,800 per shard, gzip JSON lines, ~2.5 GB in total |
| `ingest_all\run_manifest_cache.json` | pass 1: counts, timing, files that failed |
| `ingest_all\run_manifest.json` | pass 2: counts, timing, OCR pages, `failed_files` |
| `ingest_all\run_report.json` | from `report_run.py`: totals, document types, hosting and rights breakdown per collection |
| `ingest_all\errors.csv` | every file with an error and the reason |
| `ingest_all\hosting.csv` | one line per document: collection, year, hosting category, rights class / basis / confidence, restriction |

## What to check at the end

- `report_run.py` prints `records: 231399`.
- `with error` should be in the tens (on the 10,000-file benchmark: 1 unreadable PDF, 0 worker failures).
- `still needing OCR: 0`.
- Hosting split close to the benchmark: about 86% hostable, 7% hostable with restrictions, 6% unknown/review, 1% not hostable.

Send back `run_report.json`, `errors.csv` and both manifests. The shards and `layout.sqlite` are the deliverable.

## Record fields worth knowing

- `hosting.category`: `hostable`, `hostable with restrictions`, `not hostable`, `unknown/review`.
- `rights`: `class` (open_licence, public_domain, permission_required, review), `basis` (explicit notice, age, collection rule,
  no evidence), `confidence`, `evidence`. `verified` is always false: this is triage, not a legal determination.
- `restrictions`: anonymity order, reporting restriction, private hearing, anonymised parties, withdrawn at source, with the
  text that triggered each. Kept separate from `rights`.
- `title` falls back to the citation whenever the party names are missing, garbled or truncated.
- `outcome` is filled only from explicit decision text; otherwise null, with the looser guess in `outcome_unconfirmed`.
