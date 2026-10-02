"""Provenance, rights, restriction status and subject tags; reconciles text-derived metadata with
the scraper's own record of where each file came from (bailii_cases.db)."""
import os
import re
import sqlite3
from collections import Counter

from extract import PDF_DIR

SCRAPE_DB = os.environ.get("LWIZ_SCRAPE_DB") or os.path.join(os.path.dirname(os.path.dirname(PDF_DIR)), "bailii_cases.db")

E_W, SCOT, NI, UK, IE = "England and Wales", "Scotland", "Northern Ireland", "United Kingdom", "Ireland"
_JUR = {"uk": UK, "scot": SCOT, "ew": E_W, "ie": IE, "nie": NI, "je": "Jersey", "ky": "Cayman Islands", "qa": "Qatar",
        "eu": "European Union", "sg": "Singapore", "ae": "United Arab Emirates (DIFC)", "sh": "St Helena"}
_EWHC = {"Ch": "Chancery Division", "QB": "Queen's Bench Division", "KB": "King's Bench Division", "Admin": "Administrative Court",
         "Fam": "Family Division", "Comm": "Commercial Court", "TCC": "Technology and Construction Court", "Pat": "Patents Court",
         "IPEC": "Intellectual Property Enterprise Court", "Admlty": "Admiralty Court", "Costs": "Senior Courts Costs Office",
         "Mercantile": "Mercantile Court", "SCCO": "Senior Courts Costs Office", "Exch": "Exchequer", "CP": "Common Pleas",
         "Patents": "Patents Court", "Admiralty": "Admiralty Court", "Chancery": "Chancery Division", "Family": "Family Division"}
_UT = {"IAC": "Immigration and Asylum Chamber", "AAC": "Administrative Appeals Chamber", "TCC": "Tax and Chancery Chamber",
       "LC": "Lands Chamber"}
_FTT = {"PC": "Property Chamber", "TC": "Tax Chamber", "GRC": "General Regulatory Chamber",
        "HESC": "Health, Education and Social Care Chamber", "IAC": "Immigration and Asylum Chamber"}
# BAILII database code -> (court, jurisdiction or None to use the path's, regex that a more specific text-derived court must match)
_CODES = {
    "UKET": ("Employment Tribunal", None, r"Employment Tribunal"),
    "ScotCS": ("Court of Session", SCOT, r"Court of Session|Jury Court"),
    "UKAITUR": ("Upper Tribunal (Immigration and Asylum Chamber)", UK, r"Immigration"),
    "UKIAT": ("Immigration Appeal Tribunal", UK, r"Immigration"),
    "UKICO": ("Information Commissioner's Office", UK, r"Information Commissioner"),
    "EWLVT": ("Leasehold Valuation Tribunal", E_W, r"Leasehold|Property Chamber"),
    "UKIntelP": ("UK Intellectual Property Office", UK, r"Intellectual Property"),
    "UKPC": ("Judicial Committee of the Privy Council", UK, r"Privy Council"),
    "UKHL": ("House of Lords", UK, r"House of Lords"),
    "UKSC": ("Supreme Court of the United Kingdom", UK, r"Supreme Court of the United Kingdom"),
    "ScotIC": ("Scottish Information Commissioner", SCOT, r"Scottish Information"),
    "EWCA/Civ": ("Court of Appeal (Civil Division)", E_W, r"Court of Appeal \(Civil"),
    "EWCA/Crim": ("Court of Appeal (Criminal Division)", E_W, r"Court of Appeal \(Criminal"),
    "DRS": ("Nominet Dispute Resolution Service", UK, r"Nominet"),
    "UR": ("Royal Court of Jersey", "Jersey", r"Jersey"),
    "JRC": ("Royal Court of Jersey", "Jersey", r"Jersey"),
    "JCA": ("Court of Appeal of Jersey", "Jersey", r"Jersey"),
    "NIIT": ("Industrial Tribunal (Northern Ireland)", NI, r"Industrial|Fair Employment"),
    "NIFET": ("Fair Employment Tribunal", NI, r"Fair Employment|Industrial"),
    "IEHC": ("High Court of Ireland", IE, r"High Court of Ireland"),
    "IECA": ("Court of Appeal of Ireland", IE, r"Court of Appeal of Ireland"),
    "IESC": ("Supreme Court of Ireland", IE, r"Supreme Court of Ireland"),
    "IECCA": ("Court of Criminal Appeal (Ireland)", IE, r"Criminal Appeal"),
    "PBRA": ("Parole Board", E_W, r"Parole"), "PBSA": ("Parole Board", E_W, r"Parole"), "PBPH": ("Parole Board", E_W, r"Parole"),
    "UKEAT": ("Employment Appeal Tribunal", UK, r"Employment Appeal"),
    "CAT": ("Competition Appeal Tribunal", UK, r"Competition"),
    "NICA": ("Court of Appeal in Northern Ireland", NI, r"Court of Appeal in Northern"),
    "ScotHC": ("High Court of Justiciary", SCOT, r"Justiciary"),
    "ScotSC": ("Sheriff Court", SCOT, r"Sheriff"),
    "ScotSAC": ("Sheriff Appeal Court", SCOT, r"Sheriff Appeal"),
    "EWCOP": ("Court of Protection", E_W, r"Protection"),
    "EWCC": ("County Court", E_W, r"County Court"),
    "EWCR": ("Crown Court", E_W, r"Crown Court"),
    "QIC": ("Qatar International Court", "Qatar", r"Qatar"),
    "UKSIAC": ("Special Immigration Appeals Commission", UK, r"Special Immigration"),
    "UKIPTrib": ("Investigatory Powers Tribunal", UK, r"Investigatory"),
    "UKVAT": ("VAT and Duties Tribunal", UK, r"VAT"), "UKSPC": ("Special Commissioners of Income Tax", UK, r"Special Commissioners"),
    "UKSSCSC": ("Social Security and Child Support Commissioners", UK, r"Social Security"),
    "NICty": ("County Court (Northern Ireland)", NI, r"County Court"),
    "NICC": ("Crown Court (Northern Ireland)", NI, r"Crown Court"),
    "NIMaster": ("High Court (NI), Master", NI, r"Master"),
    "SIC": ("Scottish Information Commissioner", SCOT, r"Scottish Information"),
    "ScotLC": ("Scottish Land Court", SCOT, r"Land Court"),
    "ScotUT": ("Upper Tribunal for Scotland", SCOT, r"Upper Tribunal for Scotland"),
}


def court_from_code(code):
    if not code:
        return None, None, None
    if code in _CODES:
        return _CODES[code]
    head, _, sub = code.partition("/")
    if head == "EWHC":
        return "High Court of Justice" + (", " + _EWHC[sub] if sub in _EWHC else ""), E_W, (None if sub in _EWHC else r"High Court of Justice")
    if head == "UKUT":
        return "Upper Tribunal" + (f" ({_UT[sub]})" if sub in _UT else ""), UK, None
    if head == "UKFTT":
        return "First-tier Tribunal" + (f" ({_FTT[sub]})" if sub in _FTT else ""), UK, r"First-tier|Information Tribunal"
    if head == "EWFC":
        return "Family Court", E_W, r"Family"
    if head == "NIHC":
        return "High Court of Justice in Northern Ireland", NI, r"Northern Ireland|High Court \(NI\)"
    if head == "GCCI":
        return "Grand Court of the Cayman Islands", "Cayman Islands", r"Cayman"
    if head == "DIFC":
        return "DIFC Courts", "United Arab Emirates (DIFC)", r"DIFC"
    if head in ("SGHC", "SGHCI", "SGCA", "SGCAI"):
        return None, "Singapore", None
    return None, None, None


def load_provenance():
    """filename -> [(source_url, court_code, year, bailii_citation, jurisdiction_code), ...] from the scraper's database."""
    con = sqlite3.connect(f"file:{SCRAPE_DB}?mode=ro", uri=True)
    out = {}
    for path, url, court, year, cite, jur in con.execute(
            "SELECT file_path, source_url, court, year, neutral_citation, jurisdiction FROM cases WHERE format='pdf'"):
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        rows = out.setdefault(name, [])
        if not any(r[0] == url for r in rows):
            rows.append((url, court, year, cite, jur))
    con.close()
    return out


# --------------------------------------------------------------------------- rights
_TRANSCRIBER = re.compile(r"epiq|opus ?2|auscript|marten walsh cherer|smith bernal|wordwave|casetrack|john larking|ubiqus|"
                          r"merrill legal|beverley f\.? nunnery|cater walsh|harry counsell|sellers legal", re.I)
_CROWN = re.compile(r"©?\s*crown copyright", re.I)
_UK_PUBLIC = re.compile(r"^(?:UK|EW|Scot|NI|PB|CAT)")


def rights_status(code, jur_code, year, head_text, doctype):
    notes = []
    if _TRANSCRIBER.search(head_text[:3000]):
        notes.append("transcription-provider notice present; the transcript layout may carry separate rights")
    if year and year < 1900:
        r = {"status": "public domain (copyright expired by age)", "holder": None, "licence": "none required for the text",
             "basis": "document date"}
        notes.append("modern scan/OCR layer and BAILII's compilation may carry separate rights")
    elif _CROWN.search(head_text[:6000]):
        r = {"status": "Crown copyright", "holder": "Crown", "licence": "Open Government Licence v3.0 / Open Justice Licence (presumed)",
             "basis": "explicit Crown copyright notice in the document"}
    elif code in ("DRS",):
        r = {"status": "copyright Nominet UK", "holder": "Nominet UK", "licence": "not established", "basis": "issuing body"}
    elif code and _UK_PUBLIC.match(code):
        r = {"status": "Crown copyright (inferred)", "holder": "Crown",
             "licence": "Open Government Licence v3.0 / Open Justice Licence (presumed)", "basis": "UK court, tribunal or public body"}
    elif jur_code == "ie":
        r = {"status": "copyright Courts Service of Ireland (inferred)", "holder": "Courts Service of Ireland",
             "licence": "Irish PSI re-use licence (presumed)", "basis": "issuing body"}
    elif jur_code in ("je", "ky", "qa", "sg", "ae", "sh", "eu"):
        r = {"status": "copyright of the issuing court or government (inferred)", "holder": None, "licence": "not established",
             "basis": "issuing body"}
    else:
        r = {"status": "unknown", "holder": None, "licence": "not established", "basis": "no evidence"}
    r["verified"] = False
    notes.append("obtained from BAILII, whose terms restrict bulk re-publication: confirm before redistribution")
    r["notes"] = notes
    return r


# --------------------------------------------------------------------------- publication / anonymity restrictions
_R_BOILER = re.compile(r"requirement to ensure that no reporting restriction will be breached[^.]*\.(?:[^.]*\.){0,2}|"
                       r"warning:? reporting restrictions may apply.{0,700}?(?:legal advice\.?|imprisonment\.?|$)|"
                       r"reporting restrictions applied:?\s*no\b|"
                       r"for guidance on whether reporting restrictions apply.{0,160}?legal advice\.?", re.I | re.S)
_ANON_NEG = re.compile(
    r"\(?\bno anonymity (?:direction|order)\b[^.]{0,30}|anonymity (?:direction|order)s? (?:is |was |has |have )?(?:not|none) (?:been |is |was )?(?:made|sought|requested|necessary|required)|"
    r"(?:do|does|did) not (?:make|issue) an? anonymity (?:direction|order)?|decline to make (?:such )?an? (?:anonymity )?(?:direction|order)|"
    r"not (?:consider|find|think|satisfied)[^.]{0,60}(?:make|making) (?:of )?(?:an? |such )?anonymity (?:direction|order)|"
    r"(?:no|not|nor|neither)[^.]{0,70}(?:asked|invited|request\w*|appl\w+|need|reason|sought)[^.]{0,50}(?:make|for|issue) an? anonymity (?:direction|order)[^.]{0,60}|"
    r"(?:whether|if)[^.]{0,60}to make an? anonymity (?:direction|order)|(?:is|was|are) not granted anonymity|can be granted anonymity|"
    r"anonymity (?:direction|order) (?:was |had been )?(?:in place|made) previously|unnecessary to make an? anonymity (?:direction|order)|"
    r"anonymity (?:direction|order)[^.]{0,40}(?:revoked|discharged|lifted|set aside)", re.I)
# formulae specific enough to be searched for in the whole document, not only its first and last pages
_ANON_STRICT = re.compile(
    r"(?:is|are) (?:hereby )?granted anonymity|no report (?:or transcript )?of these proceedings shall|\(anonymity (?:direction|order) (?:made|in force|continued)\)|"
    r"anonymity (?:direction|order) is (?:hereby )?(?:made|necessary|appropriate|continued)|i (?:make|continue|grant) an? anonymity (?:direction|order)|"
    r"anonymity order (?:previously )?granted|(?:we|i) have anonymised|"
    r"names (?:of (?:the )?(?:parties|children|child|individuals|persons|witnesses|family)[^.]{0,40})?have been (?:changed|anonymised)", re.I)
_R_RULES = [(re.compile(p, re.I), k, v) for p, k, v in [
    (r"anonymity (?:direction|order) (?:is |was |has been |has previously been )?(?:made|continued|preserved|maintained|in force|in place)|"
     r"(?:make|makes|made|grant|grants|granted|extend\w*|continue\w*|maintain\w*) (?:an? |the |that )?anonymity (?:direction|order)|"
     r"anonymity (?:direction|order)[^.]{0,60}(?:continues|remains in force)|granted anonymity|anonymity (?:direction|order) is (?:necessary|appropriate)|"
     r"anonymity order (?:previously )?granted|"
     r"anonymisation order (?:is |was |has been )?made|subject of [^.]{0,60}anonymisation order|anonymity is preserved|shall be granted anonymity|"
     r"no report of these proceedings shall (?:directly or indirectly )?identify", "anonymity_order", "made"),
    (r"reporting restrictions? (?:appl|order|remain|are in|is in|imposed|made)|"
     r"(?<!application for a )(?<!application for )restricted reporting order(?![^.]{0,80}(?:refused|not made|declined|not granted))|"
     r"anonymity of [^.]{0,80}must be (?:strictly )?preserved|"
     r"section 4\(2\) of the contempt of court act|s\.? ?11 (?:of the )?contempt of court act|"
     r"sexual offences \(amendment\) act 1992|section 39 of the children and young persons act|"
     r"section 45a? of the youth justice|section 97 of the children act|"
     r"(?<!application )(?:must|shall|may) not be (?:published|reported|identified|named)\b(?! if)|no (?:report|matter)[^.]{0,60}(?:shall|may|must) be published|"
     r"prohibit(?:s|ing)? the publication|not to be published|embargoed", "reporting_restriction", "detected"),
    (r"(?:delivered|given|handed down|heard|held|sitting|sat|hearing) in private|\(in private|\bin camera\b", "private_hearing", True),
    (r"approved for publication|may be published|permission (?:is given )?(?:for|to) (?:this judgment to be )?publi|"
     r"there (?:is|are) no reporting restrictions?|handed down in public|heard in public|in open court", "publication_cleared", True),
    (r"anonymised version|(?:judgment|decision|determination|ruling) (?:has been|is|was) (?:anonymised|redacted)|redacted judgment|"
     r"(?:we|i) have anonymised|names (?:of (?:the )?(?:parties|children|child|individuals|persons|witnesses|family)[^.]{0,40})?have been (?:changed|anonymised)|"
     r"to protect the identit|"
     r"identif(?:y|ied) (?:only )?by (?:the )?(?:initials|letters)", "anonymised_text", True),
]]
_INITIALS = re.compile(r"^(?:(?:[A-Za-z]\.){1,4}[A-Za-z]?|[A-Z]{1,3}|[A-Z]{1,2}(?: [A-Z]{1,2}){1,3}|[A-Z]\d{1,2})$")
_PLACEHOLDER = re.compile(r"^the (?:mother|father|child|applicant|appellant)$", re.I)
_TITLE = re.compile(r"^(?:Mr|Mrs|Ms|Miss)\.? ", re.I)
# short capitals that are organisations or the Crown, not anonymised people
_NOT_INITIALS = set("R REX REGINA I HMRC BBC DWP ICO FCA FSA MOD NHS CPS DPP SRA GMC NMC TFL RBS BT ITV HSE CMA PLC LTD AND THE CQC DBS HMA PF "
                    "IBM AXA UBS RAC KFC DHL UPS TUI EDF BAE AIG GMB TSB HM UK EU ASA DVLA ACAS AG HMAG".split())
_ORG = re.compile(r"\b(?:ltd|limited|plc|llp|inc|gmbh|council|company|trading|t/a|bank|trust|university|ministry|department)\b", re.I)
_SHORT_WORDS = set("I GO TO DO NO MY WE US UP ON IN IT IS BE SO OK TV".split())


def _cypher(tok):
    """A three-letter capital token that reads as a cypher (BXR, ZSY, AAA) rather than a short surname (HAY, ALI, LEE)."""
    if len(tok) < 3:
        return True
    if len(set(tok)) == 1:
        return True
    v = [c in "AEIOU" for c in tok]
    return not (v[1] or (v[0] and v[2]))


def is_initials(name):
    """True if a party is named only by letters (AB, A.B., XY (Jamaica), Mr W) or a placeholder such as 'the mother'."""
    name = name or ""
    titled = bool(_TITLE.match(name.strip()))
    base = _TITLE.sub("", re.sub(r"\s*\(.*$", "", name).strip()).strip(" .,")
    if _PLACEHOLDER.match(base):
        return True
    letters = base.replace(".", "").replace(" ", "")
    if _ORG.search(name) or (titled and len(letters) > 2 and not (re.fullmatch(r"[A-Z]{3}", letters) and _cypher(letters))):
        return False                                      # "PTS (SA) Ltd", "MR ALI"
    if re.fullmatch(r"[A-Z]{3}", base) and not _cypher(base):
        return False
    if all(t in _SHORT_WORDS for t in base.upper().split()) and " " in base:
        return False                                      # "I GO" (a trade mark)
    base = re.sub(r"\.\s+", ".", base)                    # "O. A" -> "O.A"
    if "(" in name and re.fullmatch(r"[A-Za-z]{1,3}", base) and base.upper() not in _NOT_INITIALS:
        return True                                       # "Ad (Cote d'Ivoire)": initials + country, case lost in title-casing
    if not base or base.upper().replace(".", "").replace(" ", "") in _NOT_INITIALS:
        return False
    parts = [x for x in re.split(r",\s*|\s+and\s+|\s*&\s*", base) if x]
    if len(parts) > 1:                                    # "B, C, D and E"
        return all(_INITIALS.match(x) and x.upper() not in _NOT_INITIALS for x in parts)
    return bool(_INITIALS.match(base) or _INITIALS.match(base + "."))


_WITHDRAWN = re.compile(r"deleted (?:by|at the) request of|removed (?:by|at the) request of|judgment (?:has been |was )?(?:withdrawn|removed)", re.I)


def restrictions(text, parties, whole=None):
    """text: the first pages and the end of the document. whole: the full text, searched only for unambiguous formulae."""
    text = _R_BOILER.sub(" ", text)
    out = {"anonymity_order": "not stated", "reporting_restriction": "none detected", "private_hearing": False,
           "publication_cleared": False, "anonymised_text": False, "evidence": []}

    def note(src, m):
        if len(out["evidence"]) < 4:
            out["evidence"].append(src[max(0, m.start() - 60): min(len(src), m.end() + 60)].strip())

    neg = _ANON_NEG.search(text)
    positive = _ANON_NEG.sub(" ", text)
    for rx, key, val in _R_RULES:
        m = rx.search(positive)
        if m:
            out[key] = val
            note(positive, m)
    if whole and out["anonymity_order"] != "made":
        w = _ANON_NEG.sub(" ", _R_BOILER.sub(" ", whole))
        m = _ANON_STRICT.search(w)
        if m:
            out["anonymity_order" if "anonymity" in m.group(0).lower() or "no report" in m.group(0).lower() else "anonymised_text"] = \
                "made" if "anonymity" in m.group(0).lower() or "no report" in m.group(0).lower() else True
            note(w, m)
    if out["anonymity_order"] == "not stated" and neg:
        out["anonymity_order"] = "not made"
        note(text, neg)
    out["anonymised_parties"] = any(is_initials(p.get("name", "")) for p in parties or [])
    out["withdrawn_at_source"] = bool(_WITHDRAWN.search(text[:400]))
    if out["withdrawn_at_source"]:
        out["status"] = "restricted - withdrawn at source"
    elif out["reporting_restriction"] == "detected":
        out["status"] = "restricted - review before publication"
    elif out["anonymity_order"] == "made" or out["anonymised_parties"] or out["anonymised_text"]:
        out["status"] = "anonymised"
    else:
        out["status"] = "no restriction detected"
    return out


# --------------------------------------------------------------------------- subject tags
_TAG_PHRASES = """unfair dismissal|constructive dismissal|wrongful dismissal|redundancy|unlawful deduction from wages|holiday pay|
notice pay|breach of contract|whistleblowing|protected disclosure|disability discrimination|race discrimination|
sex discrimination|age discrimination|religion or belief|pregnancy and maternity|sexual orientation|harassment|victimisation|
reasonable adjustments|equal pay|time limits|strike out|deposit order|employment status|tupe|working time|
leave to remain|entry clearance|deportation|asylum|humanitarian protection|article 8|article 3|private and family life|
human rights|eea regulations|residence card|permanent residence|error of law|internal relocation|country guidance|
points based system|tier 4|tier 1|adult dependent relative|trafficking|statelessness|naturalisation|
freedom of information|environmental information|vexatious request|personal data|legal professional privilege|
commercial interests|national security|law enforcement|information provided in confidence|public interest test|
cost limit|health and safety|formulation of government policy|data protection|subject access|
trade mark|likelihood of confusion|bad faith|passing off|revocation|invalidity|non-use|distinctive character|patent|
inventive step|registered design|copyright|domain name|abusive registration|
income tax|value added tax|vat|corporation tax|capital gains tax|inheritance tax|stamp duty land tax|reasonable excuse|
late filing|late payment|discovery assessment|national insurance|capital allowances|
service charge|leasehold enfranchisement|lease extension|dispensation|consultation requirements|right to manage|
rent repayment order|house in multiple occupation|improvement notice|market rent|ground rent|breach of covenant|forfeiture|
appointment of manager|pitch fee|
judicial review|planning permission|extradition|confiscation|murder|rape|
negligence|personal injury|clinical negligence|injunction|summary judgment|contempt of court|
insolvency|bankruptcy|winding up|liquidation|directors disqualification|
divorce|financial remedy|care order|adoption|child arrangements|parental responsibility|guardianship|
succession|probate|executor|
prescription|servitude|heritable|adjudication|arrestment|bill of exchange|cautioner|tack|teinds|entail|
arbitration|insurance|partnership|parole|defamation|nuisance"""
_TAGS = [t.strip() for t in _TAG_PHRASES.replace("\n", "").split("|") if t.strip()]
_TAG_RE = re.compile(r"\b(?:%s)\b" % "|".join(sorted((re.escape(t) for t in _TAGS), key=len, reverse=True)))


def subject_tags(full_text, catchwords=()):
    counts = Counter(_TAG_RE.findall(full_text[:300000].lower()))      # lower once: far faster than re.I
    tags = [t for t, c in counts.most_common(14) if c >= 2]
    for c in catchwords or []:
        c = c.strip().lower()
        if 2 < len(c) <= 60 and c not in tags:
            tags.append(c)
    return tags[:20]


# --------------------------------------------------------------------------- reconcile with provenance
def apply_provenance(rec, rows):
    """Attach source URL and use the BAILII path (court code, year, citation) to confirm or correct text-derived fields."""
    stem = rec["file"].rsplit(".", 1)[0]
    prov = {"collection": "BAILII (bailii.org) scrape, NotAScraper 'NEW STUFF'", "retrieved_via": "ScraperV2.py",
            "local_path": os.path.join(PDF_DIR, rec["file"])}
    code = jcode = None
    if rows:
        row = rows[0]
        resolved = True
        if len(rows) > 1:
            y = str(rec.get("year") or "")
            nc = (rec.get("neutral_citation") or "") if rec.get("neutral_citation_source") == "text" else ""
            by_nc = [r for r in rows if nc and (r[1] or "").replace("/", " ") in nc.replace("(", "").replace(")", "") and (r[2] or "") in nc] \
                or [r for r in rows if nc and (r[1] or "").split("/")[0] in nc and (r[2] or "") in nc]
            row = by_nc[0] if by_nc else next((r for r in rows if r[2] == y), rows[0])
            resolved = bool(by_nc) or len({(r[1], r[4]) for r in rows}) == 1
            prov["ambiguous_source"] = True
            prov["ambiguity_resolved_by"] = "document citation" if by_nc else ("same court in all candidates" if resolved else None)
            prov["alternative_urls"] = [r[0] for r in rows if r is not row][:5]
        url, code, year, cite, jcode = row
        prov.update({"bailii_database": code, "bailii_year": year})
        rec["source_url"] = url
        rec["bailii_citation"] = cite
        dbyear = int(year) if year and year.isdigit() else None
        ccourt, cjur, family = court_from_code(code)
        tcourt = rec.get("court")
        if not resolved:
            ccourt = None                          # the file on disk may belong to any of the candidate URLs
            rec["source_url_confidence"] = "low (filename shared by several source URLs)"
        if ccourt and not (tcourt and family and re.search(family, tcourt)):
            if tcourt and tcourt != ccourt:
                prov["court_from_text"] = tcourt
            rec["court"] = ccourt
            rec["jurisdiction"] = cjur or _JUR.get(jcode) or rec.get("jurisdiction")
            rec["court_source"] = "bailii path"
        else:
            rec["court_source"] = "document text" if tcourt else None
            if not rec.get("jurisdiction"):
                rec["jurisdiction"] = cjur or _JUR.get(jcode)
        if code == "UKET" and rec.get("jurisdiction") in (None, UK):
            rec["jurisdiction"] = E_W
        if dbyear and resolved:
            d = rec.get("judgment_date") or rec.get("date")
            if d and abs(int(d[:4]) - dbyear) > 1:
                rec["date_confidence"] = "low (year disagrees with source path)"
            elif d:
                rec["date_confidence"] = "high" if int(d[:4]) == dbyear else "medium"
            if rec.get("year") != dbyear:
                rec["year_from_text"] = rec.get("year")
                rec["year"] = dbyear
                rec.pop("year_is_approximate", None)
            rc = rec.get("reporter_citation")
            if rc and rec.get("report_series") and " TC " not in rc:
                rec["reporter_citation"] = f"({dbyear}) " + re.sub(r"^\(\d{4}\)\s*", "", rc)
        if not rec.get("neutral_citation") and cite and re.match(r"\[\d{4}\] (?:UKPC|UKHL|UKSC|EWCA|EWHC|EWCOP|EWFC|UKUT|UKFTT|"
                                                                 r"CSOH|CSIH|HCJAC|IEHC|IESC|IECA|NICA|NIQB|NIKB|JRC|QIC) (?:Civ |Crim |\([A-Z]\) )?\d+\b(?!_)", cite) and resolved:
            rec["neutral_citation"], rec["neutral_citation_source"] = cite.strip(), "bailii index"
        if rec.get("primary_identifier") in (None, stem):
            rec["primary_identifier"] = rec.get("neutral_citation") or (cite.strip() if cite else stem)
    else:
        rec["source_url"] = None
        prov["note"] = "no row for this file in the scraper database"
    rec["provenance"] = prov
    return code, jcode
