"""Rights / licence classification, kept separate from publication restrictions.

Evidence used, strongest first:
  1. an explicit notice or licence statement inside the document
  2. age (copyright expired)
  3. the source collection (BAILII database code) and what kind of body issued the document

The output is a working classification for triage, not a legal determination. Anything resting on a
collection rule alone is marked "collection rule" so it is never mistaken for a verified status.

rights["class"]:
  open_licence          Crown / Parliamentary / EU material reusable under an open licence (OGL, Open Justice,
                        Open Parliament, EU reuse decision)
  public_domain         copyright expired by age
  permission_required   a private rights holder and no open licence identified
  review                rights position not established (foreign courts, restrictive notices, private report series)

hosting["category"] combines that with the *separate* restriction status:
  hostable | hostable with restrictions | not hostable | unknown/review
"""
import datetime
import re

THIS_YEAR = datetime.date.today().year
PD_YEAR = THIS_YEAR - 121            # published by this year: any author's copyright has expired for practical purposes
CROWN_PD_YEAR = THIS_YEAR - 51       # Crown copyright in a published work lasts 50 years from publication

_N_CROWN = re.compile(r"©?\s*crown copyright", re.I)
_N_OPEN = re.compile(r"open government licence|open justice licence|open parliament licence|creative commons", re.I)
_N_RESTRICT = re.compile(
    r"(?:may|must|shall|should) not be reproduced|all rights (?:are )?reserved|not to be reproduced|no part of this (?:publication|transcript|document) may|"
    r"reproduc\w+[^.]{0,60}?without (?:the )?(?:express |prior |written )*(?:consent|permission)|"
    r"copyright (?:in|of) this (?:transcript|judgment|document)[^.]{0,40}(?:belongs|is owned|vests)", re.I)
_N_OTHER = re.compile(r"©\s*(?!crown\b)(?:copyright\s*)?(?:\d{4}\s*)?([A-Z][\w&.,'’ -]{2,50})", re.I)

# UK courts, tribunals and public bodies whose output is Crown copyright (or published under the OGL by the body itself)
_UK_CROWN = re.compile(r"^(?:EWHC|EWCA|EWCOP|EWFC|EWCC|EWCR|EWMC|EWLVT|EWLands|EWPCC|EWCST|UKSC|UKPC|UKUT|UKFTT|UKEAT|UKET|UKAITUR|UKIAT|UKSIAC|"
                       r"UKIPTrib|UKVAT|UKSPC|UKSSCSC|UKIntelP|UKICO|UKIT|UKFSM|SIAC|EWLandRA|CAT|PBRA|PBSA|PBPH|ScotCS|ScotHC|ScotSC|ScotSAC|ScotIC|ScotLC|ScotUT|ScotFAI|"
                       r"NICA|NIHC|NICC|NICty|NIIT|NIFET|NIMaster|NIMag|NISSCSC|NICoroner)(?:/|$)")
_PRIVATE_SERIES = re.compile(r"Scottish Law Reporter|Macqueen|Paton|Paterson|Bligh|Dow|Robinson|Robertson|Shaw|Wilson|Bell|Murray|Maclean|"
                             r"Morison|Brown's|Elchies|Hailes|nominate", re.I)
_FOREIGN = {"ie": "Ireland (Courts Service / Government of Ireland)", "je": "Jersey", "ky": "Cayman Islands", "qa": "Qatar",
            "ae": "DIFC (United Arab Emirates)", "sg": "Singapore", "sh": "St Helena"}


def classify(code, jcode, year, head_text, tail_text, doctype=None, series=None):
    text = (head_text or "")[:8000] + " " + (tail_text or "")[-3000:]
    evidence, notes = [], []
    # an open-licence statement counts only where a notice sits (top of page 1 / foot of the last page): in the body it is usually quoted matter
    crown, restrict = _N_CROWN.search(text), _N_RESTRICT.search(text)
    opened = _N_OPEN.search((head_text or "")[:1500]) or _N_OPEN.search((tail_text or "")[-1500:])
    other = _N_OTHER.search(text)
    if crown:
        evidence.append("notice: Crown copyright")
    if opened:
        evidence.append(f"notice: {opened.group(0)}")
    if restrict:
        evidence.append(f"notice: \"{text[max(0, restrict.start() - 60): restrict.end() + 40].strip()}\"")
    if other and not crown:
        evidence.append(f"notice: © {other.group(1).strip()[:50]}")
    code = code or ""
    r = {"holder": None, "licence": None}

    def done(cls, basis, confidence, **kw):
        r.update({"class": cls, "basis": basis, "confidence": confidence, "evidence": evidence, "notes": notes, "verified": False}, **kw)
        return r

    private_series = bool(series and _PRIVATE_SERIES.search(series))
    if year and year <= PD_YEAR:
        # a reporter who published in his twenties could have died less than 70 years ago if the report is under ~150 years old
        sure = year <= THIS_YEAR - 150 or not private_series
        return done("public_domain", "age", "high" if sure else "medium", licence="none needed",
                    status=f"public domain: published {THIS_YEAR - year} years ago" +
                           ("" if sure else " (private report series: the reporter's date of death has not been checked)"))
    if restrict and not opened:
        return done("review", "explicit notice", "explicit", holder="Crown" if crown else None,
                    status="restrictive reproduction notice in the document: licence or consent needed before hosting")
    if opened:
        return done("open_licence", "explicit notice", "explicit", holder="Crown" if crown else None, licence=opened.group(0),
                    status="open licence stated in the document")
    if private_series:
        return done("review", "collection rule", "low",
                    status=f"privately published report series from {year}: probably out of copyright, but the reporter's term has not been checked")
    uk = bool(_UK_CROWN.match(code)) or code == "UKHL"
    if uk and year and year <= CROWN_PD_YEAR:
        return done("public_domain", "age", "medium", holder="Crown (expired)", licence="none needed",
                    status=f"Crown / Parliamentary copyright expired (published {THIS_YEAR - year} years ago; 50-year term)")
    if crown:
        return done("open_licence", "explicit notice", "explicit", holder="Crown", licence_stated_in_document=False,
                    licence="Open Government Licence v3.0 / Open Justice Licence (presumed: the notice states Crown copyright, not a licence)",
                    status="Crown copyright notice in the document; open licence presumed for Crown judicial material")
    if code == "UKHL":
        return done("open_licence", "collection rule", "medium", holder="Parliament", licence="Open Parliament Licence",
                    status="House of Lords judgment: Parliamentary copyright (inferred from the collection)")
    if uk:
        return done("open_licence", "collection rule", "medium", holder="Crown",
                    licence="Open Government Licence v3.0 / Open Justice Licence",
                    status="UK court, tribunal or public body: Crown copyright (inferred from the collection, no notice in the document)")
    if code == "ECHR":
        return done("review", "collection rule", "low", holder="Council of Europe / European Court of Human Rights",
                    status="Strasbourg court document: Council of Europe reuse terms not established")
    if jcode == "eu" or code.startswith("EU"):
        return done("open_licence", "collection rule", "medium", holder="European Union", licence="EU reuse policy (Decision 2011/833/EU)",
                    status="EU institution document (inferred from the collection)")
    if code == "DRS":
        return done("permission_required", "collection rule", "medium", holder="Nominet UK",
                    status="private dispute-resolution body; no open licence identified")
    if jcode in _FOREIGN:
        return done("review", "collection rule", "low", holder=_FOREIGN[jcode],
                    status="non-UK court: reuse terms not established")
    return done("review", "no evidence", "low", status="source collection not recognised")


def restriction_class(restrictions):
    s = (restrictions or {}).get("status", "")
    if "withdrawn" in s:
        return "withdrawn at source"
    if s.startswith("restricted"):
        return "reporting restriction detected"
    if s == "anonymised":
        return "anonymised / anonymity order"
    return "none detected"


def hosting(rights, restrictions):
    rc = restriction_class(restrictions)
    cls = rights["class"]
    if rc == "withdrawn at source":
        cat = "not hostable"
    elif cls in ("open_licence", "public_domain"):
        cat = "hostable" if rc == "none detected" else "hostable with restrictions"
    elif cls == "permission_required":
        cat = "not hostable"
    else:
        cat = "unknown/review"
    return {"category": cat, "rights_class": cls, "rights_confidence": rights["confidence"], "restriction_class": rc,
            "note": "triage classification, not legal advice; BAILII's own terms on bulk re-publication apply to the source copies"}
