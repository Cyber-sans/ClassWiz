"""Second-pass corrections that need the visual rows and the source-path year, applied after
meta.extract + provenance: party fallbacks from merged rows, old-report case selection by year,
OCR law-report titles, date re-selection when the text date contradicts the source path."""
import re

import meta

_ROLE = r"Claimants?|Respondents?|Appellants?|Applicants?|Defendants?|Plaintiffs?|Pursuers?|Defenders?|Petitioners?"
_NAME_ROLE = re.compile(r"^(.{2,110}?)[\s.\-—–_]*\b(%s|A\s?pp\w{1,3}\s?\w{0,3}ant\S{0,2}|Respond\w{2,5})\W{0,3}$" % _ROLE, re.I)
_ROLE_NAME = re.compile(r"^(%s)\s*:?\s+(.{2,110})$" % _ROLE, re.I)
_A_V_B = re.compile(r"^(.{2,90}?)\s+(?:v\.?|-v-|–v–|AND)\s+(.{2,90})$")
_BAD_NAME = re.compile(r"Case No|Appeal No|Tribunal|Reporter|Privy Council|\b(?:19|20)\d\d\.|FROM$|^before\b|\bBL O/|\bACT (?:19|20)\d\d|"
                       r"^(?:the )?(?:representative|property|case ref)|^(?:app\w{2,5}ant|respond\w+|limited|ltd|trustees\))\.?$|"
                       r"^(?:and|first|second|third|claimants?|defendants?|pursuers?|defenders?|applicants?)$|for the$|"
                       r"\b(?:Presenting|Higher|Senior) Officer\b|Officer,? (?:for|of) H|\binstructed\b|\bof counsel\b|;|"
                       r"^\(|\(Respondent to|\(Applicants? for", re.I)
_BAD_CASE = re.compile(r"\bFROM\b| A ppellant")
_LEAD = re.compile(r"^(?:By|BY)\s+(?=[A-Z])")
_ROLE_TAIL = re.compile(r"\s*[–—-]\s*(?:First|Second|Third|1st|2nd)?\s*(?:Appellant|Respondent|Claimant|Defendant|Applicant)s?$", re.I)
_TRAIL = re.compile(r"\s+(?:Background(?: and pleadings)?|BACKGROUND.*|DECISION|Introduction)$")
_JUDGE_OK = re.compile(r"^[A-Z(][^\d]{2,70}$")
_OLD_SERIES = ("Morison's", "Brown's Supplement", "Elchies", "Hailes")
_CAPS_V = re.compile(r"^[\[(]?([A-Z][A-Z0-9'’.,&() \-]{2,70}?)\s+[Vv]\.?\s+([A-Z][A-Z0-9'’.,&() \-]{2,80}?)[.\])]*$")
_RUNHEAD = re.compile(r"[,;]?\s*[IJ\[]?\s*The Scott\w+ La\w+ Reporter.*$", re.I)


def _bad(name):
    return len(name) > 110 or bool(_BAD_NAME.search(name)) or bool(_BAD_CASE.search(name)) or len(name.split()) > 16 \
        or sum(c.isalpha() for c in name) < 1 or name.count("(") != name.count(")")


def _role_word(w):
    w = w.lower()
    for r in ("claimant", "respondent", "appellant", "applicant", "defendant", "plaintiff", "pursuer", "defender", "petitioner"):
        if w.startswith(r[:5]):
            return r
    return "respondent" if w.startswith("resp") else "appellant"


def row_parties(blocks):
    """Parties from header rows where name and role share a visual line ('JOHN SMITH Appellant')."""
    head = [b for b in blocks if b.get("z") == "header" and b["t"] in ("line", "paragraph", "list_item")][:40]
    out = []
    for i, b in enumerate(head):
        t = b["text"].strip()
        if len(t) > 130:
            continue
        m = _ROLE_NAME.match(t)
        if m and not re.match(r"(?i)representative|in person|:", m.group(2)):
            out.append({"role": _role_word(m.group(1)), "name": meta._clean_party(m.group(2))})
            continue
        m = _NAME_ROLE.match(t)
        if m and not re.match(r"(?i)for the|and$|the$", m.group(1).strip()):
            out.append({"role": _role_word(m.group(2)), "name": meta._clean_party(m.group(1))})
            continue
        if i and re.fullmatch(r"(?i)claimants?\s+(?:and\s+)?respondents?", head[i - 1]["text"].strip()):
            m = _A_V_B.match(t)
            if m:
                return [{"role": "claimant", "name": meta._clean_party(m.group(1))},
                        {"role": "respondent", "name": meta._clean_party(m.group(2))}]
    out = [p for p in out if p["name"] and not _bad(p["name"])]
    seen, res = set(), []
    for p in out:
        if (p["role"], p["name"]) not in seen:
            seen.add((p["role"], p["name"]))
            res.append(p)
    return res


def _set_case(rec, parties):
    rec["parties"] = parties
    name = meta.make_case_name(parties)
    if name:
        rec["case_name"] = rec["title"] = name


def apply(rec, page_texts, content, ocr_first_page):
    stem = rec["file"].rsplit(".", 1)[0]
    dbyear = rec.get("year") if rec.get("provenance", {}).get("bailii_year") else None
    series = rec.get("report_series") or ""
    cite = rec.get("reporter_citation") or rec.get("neutral_citation") or rec.get("bailii_citation") or stem

    # 1. old Scottish dictionaries: the page often holds several cases; the file's case is the one dated in the source-path year
    if series.startswith(_OLD_SERIES) and dbyear:
        text = "\n".join(page_texts)
        rx = re.compile(r"\b%d[.,;]?\s+([A-Za-z]{3,9})\.?,?\s+(\d{1,2})\.?\s*\n?\s*(?:[-—*' ]*)([A-Z].{2,90}?)\s+(?:a[gqf]\w{3,6}[t!l]|contra)\s+(.{2,90}?)[.,]?\s*(?:\n|$)" % dbyear)
        m = rx.search(text)
        if m and (meta.garbled(m.group(3)) or meta.garbled(m.group(4))):
            rec["parties"], rec["case_name"], rec["title"] = [], None, cite      # the heading is there but the OCR of the names is not trustworthy
        elif m:
            a, b = meta._clean_party(m.group(3)), meta._clean_party(m.group(4))
            if a and b:
                _set_case(rec, [{"role": "party a", "name": a}, {"role": "party b", "name": b}])
            mon = meta._MONTHS.get(m.group(1)[:3].lower())
            if mon and 1 <= int(m.group(2)) <= 31:
                rec["judgment_date"] = rec["date"] = f"{dbyear:04d}-{mon:02d}-{int(m.group(2)):02d}"
                rec["date_confidence"], rec["judgment_date_source"] = "high", "report heading for the source-path year"
        elif (rec.get("judgment_date") or rec.get("date") or "0000")[:4] != str(dbyear):
            rec["parties"], rec["case_name"], rec["title"] = [], None, cite      # cannot tell which case on the page it is

    # 2. scanned law reports (OCR): title from the capitalised 'A v. B' heading; nothing else is reliable
    if rec["document_type"] == "LAW_REPORT" and ocr_first_page:
        rec["judges"] = []
        rec["catchwords"] = rec["editorial_catchwords"] = []
        found = None
        if "Scottish Law Reporter" in series:
            raw = [_RUNHEAD.sub("", l.strip()) for l in "\n".join(page_texts[:2]).split("\n")]
            for i, line in enumerate(raw):
                m = _CAPS_V.match(line)
                if not (m and sum(c.isupper() for c in line) > 0.7 * sum(c.isalpha() for c in line)):
                    continue
                left = m.group(1)
                wrapped = re.match(r"(?:LIMITED|LTD|COMPANY|TRUSTEES\)?|AND OTHERS|OTHERS)\b", left) or left.count(")") > left.count("(")
                if wrapped and i and not re.search(r"DIVISION|COURT|LORDS|\d", raw[i - 1]):
                    m = _CAPS_V.match(raw[i - 1].rstrip("- ") + ("" if raw[i - 1].endswith("-") else " ") + line) or m
                right = re.sub(r"\.\s*\(.*$", "", m.group(2))
                if not (meta.garbled(m.group(1)) or meta.garbled(right)):
                    found = (meta._clean_party(m.group(1)), meta._clean_party(right))
                break
        if found and all(found) and not any(_bad(x) for x in found):
            _set_case(rec, [{"role": "party a", "name": found[0]}, {"role": "party b", "name": found[1]}])
        elif "Scottish Law Reporter" in series or any(_bad(p["name"]) for p in rec.get("parties") or []):
            rec["parties"], rec["case_name"], rec["title"] = [], None, cite

    # 3. parties missing or implausible: fall back to rows where name and role sit on one visual line
    parties = rec.get("parties") or []
    changed = False
    for p in parties:
        n = _ROLE_TAIL.sub("", _LEAD.sub("", p["name"]))
        changed |= n != p["name"]
        p["name"] = n
    if changed and len(parties) >= 2:
        _set_case(rec, parties)
    if rec["document_type"] == "LAW_REPORT" and any(_bad(p["name"]) for p in parties):
        rec["parties"], rec["case_name"], rec["title"] = [], None, cite
        parties = []
    if rec["document_type"] == "CASE_JUDGMENT" and (len(parties) < 2 or any(_bad(p["name"]) for p in parties)):
        rp = row_parties(content["blocks"])
        roles = {p["role"] for p in rp}
        if len(rp) >= 2 and len(roles) >= 2:
            _set_case(rec, rp)
            rec["parties_source"] = "visual rows"
        elif rp and parties and all(_bad(p["name"]) for p in parties):
            rec["parties"], rec["case_name"], rec["title"] = rp, rp[0]["name"], rp[0]["name"]
            rec["parties_source"] = "visual rows"
        elif any(_bad(p["name"]) for p in parties):
            rec["parties"] = [p for p in parties if not _bad(p["name"])]
            rec["case_name"] = meta.make_case_name(rec["parties"]) if len(rec["parties"]) >= 2 else None
            rec["title"] = rec["case_name"]
    if rec.get("title") and (_bad(rec["title"]) and " v " not in rec["title"][:120] or len(rec["title"]) > 220):
        rec["title"] = None
    if not rec.get("title"):
        rec["title"] = cite

    for p in rec.get("parties") or []:
        p["name"] = _TRAIL.sub("", p["name"])
    if rec.get("title"):
        rec["title"] = _TRAIL.sub("", rec["title"])
        if rec.get("case_name"):
            rec["case_name"] = _TRAIL.sub("", rec["case_name"])
    if rec.get("judges"):
        rec["judges"] = [j.rstrip("]") for j in rec["judges"] if _JUDGE_OK.match(j) and len(j.split()) <= 9
                         and not re.search(r"(?i)\b(?:plaintiff|defendant|claimant|respondent|appellant|the main|this)\b", j)]

    # 3b. the signature block at the end is the best witness for the decision date
    cur = rec.get("judgment_date") or rec.get("date")
    court = rec.get("court") or ""
    weak = rec.get("judgment_date_source") in ("date-line", "first-date", "dated", "tail-last", "re-selected")
    if rec["document_type"] == "CASE_JUDGMENT" and (not cur or (dbyear and cur[:4] != str(dbyear)) or "Employment" in court
                                                    or "Industrial" in court or (weak and "Tribunal" in court)):
        sig = " \u2016 ".join(b["text"] for b in content["blocks"][-10:] if b["t"] != "table" and len(b.get("text", "")) < 160)
        sig = re.sub(r"[_.…]{2,}", " ", sig)
        cands = []
        for pos, d in meta.find_dates(sig):
            pre = sig[max(0, pos - 48): pos].lower()
            if re.search(r"sent to|entered in|copied to|issued to|register|letter|email|hearing|heard", pre):
                continue
            cands.append(d)
        if dbyear:
            cands = [d for d in cands if d[:4] == str(dbyear)]
        if cands and cands[0] != cur:
            rec["date_from_text_rejected"] = cur
            rec["judgment_date"] = rec["date"] = cands[0]
            rec["judgment_date_source"] = "signature block"
            rec["date_confidence"] = "high" if dbyear else "medium"

    # 4. a text date that contradicts the source-path year is replaced by one that agrees, or dropped
    if dbyear and str(rec.get("date_confidence", "")).startswith("low"):
        wrong = rec.get("judgment_date") or rec.get("date")
        last = meta.flat(page_texts[-1])[-1500:] if page_texts else ""
        first = meta.flat(" ".join(page_texts[:2]))[:3000]
        cands = [d for _, d in meta.find_dates(last) if d[:4] == str(dbyear)]
        pick = cands[-1] if cands else next((d for _, d in meta.find_dates(first) if d[:4] == str(dbyear)), None)
        rec["date_from_text_rejected"] = wrong
        rec["judgment_date"] = rec["date"] = pick
        rec["date_confidence"] = "medium (re-selected to agree with source-path year)" if pick else None
        rec["judgment_date_source"] = "re-selected" if pick else None
    return rec
