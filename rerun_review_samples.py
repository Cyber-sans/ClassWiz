"""Re-run the 20 review samples from the cached layout and write output_v2/ plus a field-level change report."""
import json
import os
import shutil

import enrich
import ingest

R = "review_samples"
FIELDS = ["document_type", "title", "parties", "judges", "judgment_date", "outcome", "outcome_source", "outcome_text", "catchwords", "editorial_catchwords",
          "subject_tags", "headnote", "case_segment", "text_quality"]


def short(v):
    if isinstance(v, list) and v and isinstance(v[0], dict):
        v = [f"{p['role']}: {p['name']}" for p in v]
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 420 else s[:420] + "…"


if __name__ == "__main__":
    manifest = json.load(open(os.path.join(R, "manifest.json"), encoding="utf-8"))
    names = [m["pdf"].split("/", 1)[1] for m in manifest]
    tmp = os.path.join(R, "_tmp")
    ingest.run(names, tmp, workers=4, prov=enrich.load_provenance(), quiet=True)
    new = {r["source_filename"]: r for r in ingest.read_records(tmp)}
    shutil.rmtree(tmp)
    out = os.path.join(R, "output_v2")
    os.makedirs(out, exist_ok=True)
    lines = []
    for m in manifest:
        name = m["pdf"].split("/", 1)[1]
        old = json.load(open(os.path.join(R, m["output"]), encoding="utf-8"))
        rec = new[name]
        with open(os.path.join(out, name[:-4] + ".json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        m["output_v2"] = f"output_v2/{name[:-4]}.json"
        lines.append(f"\n## {m['n']}. {name}  ({m['category']})")
        lines.append(f"blocks: {len(old['content']['blocks'])} -> {len(rec['content']['blocks'])}; "
                     f"full_text chars: {len(old['full_text'])} -> {len(rec['full_text'])}")
        changed = False
        for k in FIELDS:
            if old.get(k) != rec.get(k):
                changed = True
                lines.append(f"- {k}:\n    before: {short(old.get(k))}\n    after:  {short(rec.get(k))}")
        if not changed:
            lines.append("- (no change in the audited fields)")
    json.dump(manifest, open(os.path.join(R, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(os.path.join(R, "changes_v2.md"), "w", encoding="utf-8").write("# Review samples: before vs after the audit fixes\n" + "\n".join(lines) + "\n")
    print("\n".join(lines))
