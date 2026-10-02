"""Deterministic metadata extraction for classified legal documents.

Everything here is regex / lookup-table / keyword-scoring work on the cached text
(head pages + last page) and the filename. No model calls; ~1 ms per document.
"""
import re

from textcache import split_text

# --------------------------------------------------------------------------- helpers
_WS = re.compile(r"\s+")
_SMALL = {"of", "the", "and", "for", "in", "on", "at", "to", "v", "or", "de", "la", "le", "a", "an", "by"}
_KEEP = {"PLC", "LLP", "NHS", "UK", "HM", "QC", "KC", "HMRC", "DPP", "BBC", "II", "III", "MBE", "OBE", "CBE", "USA", "GP", "NI"}


def flat(s):
    return _WS.sub(" ", s).strip()


_OK_TOKEN = re.compile(r"(?:Ma?c|M['‘’]|O['’]|De|Le|La|Du|Di|Fitz|Van|St)?[A-Z][a-z]+(?:[-'’][A-Za-z]?[a-z]+)*\.?|[A-Z]+(?:[-'’][A-Z]+)*\.?|[a-z]+\.?|"
                       r"M['‘’][A-Z][A-Za-z]+|[A-Z]\.(?:[A-Z]\.)*|&c?\.?|[A-Z][a-z]+[A-Z][a-z]+")


def garbled(raw):
    """True if a party name taken from OCR text shows damage: odd case mixtures, digits or symbols inside words."""
    toks = [t.strip(".,;:()[]\"“”‘’'") for t in flat(raw).split()]
    toks = [t for t in toks if t]
    if not toks:
        return True
    bad = 0
    for t in toks:
        if t.isdigit() or t in ("&", "-", "–", "v", "T/A", "t/a"):
            continue
        if re.search(r"[A-Za-z]\d|\d[A-Za-z]|[^\w&'’‘.\-/]", t) or re.search(r"\w[.:;]\w{3,}", t) or not _OK_TOKEN.fullmatch(t):
            bad += 1
    return bad > 0


def smart_title(s):
    """Title-case names that are shouting; leave mixed-case names alone."""
    s = flat(s)
    letters = [c for c in s if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return s
    out = []
    for i, w in enumerate(s.split(" ")):
        core = w.strip("()[],.;:")
        if core in _KEEP or any(c.isdigit() for c in core) or len(core) == 1:
            out.append(w)
        elif i and core.lower() in _SMALL:
            out.append(w.lower())
        elif core.startswith(("MC", "Mc")) and len(core) > 3:
            out.append(w.replace(core, "Mc" + core[2:].capitalize()))
        else:
            out.append("-".join(p.capitalize() for p in w.split("-")) if "-" in w else
                       w[: len(w) - len(w.lstrip("([\"'‘“"))] + w.lstrip("([\"'‘“").capitalize())
    return " ".join(out).replace("’S ", "’s ").replace("'S ", "'s ")


# --------------------------------------------------------------------------- dates
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MON = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DATE = re.compile(
    r"\b(?:(\d{1,2})\s?(?:st|nd|rd|th)?\.?\s*(?:day\s+of\s+)?" + _MON + r"\.?,?\s+(\d{4})"
    r"|" + _MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?[.,]?\s+(\d{4})"
    r"|(\d{1,2})[/.](\d{1,2})[/.](\d{4}|\d{2}))\b", re.I)


def find_dates(s):
    """[(pos, 'YYYY-MM-DD')] for every parseable date in s."""
    out = []
    for m in _DATE.finditer(s):
        d1, mon1, y1, mon2, d2, y2, d3, m3, y3 = m.groups()
        try:
            if mon1:
                d, mo, y = int(d1), _MONTHS[mon1[:3].lower()], int(y1)
            elif mon2:
                d, mo, y = int(d2), _MONTHS[mon2[:3].lower()], int(y2)
            else:
                d, mo, y = int(d3), int(m3), int(y3)
                if y < 100:
                    y += 2000 if y < 30 else 1900
        except (KeyError, ValueError):
            continue
        if 1 <= d <= 31 and 1 <= mo <= 12 and 1500 <= y <= 2030:
            out.append((m.start(), f"{y:04d}-{mo:02d}-{d:02d}"))
    return out


_CUES_EXPLICIT = re.compile(
    r"date of (?:the )?decision|decision date|date of (?:the )?judgment|judgment date|reasons issued|release date|"
    r"date of release|date of issue", re.I)
_CUE_SENT = re.compile(r"sent to the parties on|entered in (?:the )?register|issued to the parties on", re.I)
_CUES_STRONG = re.compile(
    r"date of (?:the )?decision|decision date|date of (?:the )?judgment|judgment date|judgment delivered|"
    r"reasons issued|release date|date of release|date of issue|handed down|delivered", re.I)
_CUE_PROM = re.compile(r"promulgated", re.I)
_CUE_DATED = re.compile(r"\bdated?\b(?: this)?", re.I)
_CUE_HEARD = re.compile(r"\b(?:heard (?:at|on)|held at|date of hearing|hearing date|at the tribunal|sitting in public)\b", re.I)
_DATE_LINE = re.compile(r"^(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+)?(?:Date:?\s*)?\S.{4,28}$")


def _after(cue, s, window=70, pick=min):
    best = None
    for m in cue.finditer(s):
        ds = find_dates(s[m.end(): m.end() + window])
        if ds:
            best = pick(d for _, d in ds) if pick is max else ds[0][1]
            break
    return best


def judgment_date(lines, fhead, ftail):
    h = fhead[:1500]
    m = _CUE_PROM.search(h[:900])
    if m:      # "Heard on X ... Promulgated on Y (prepared Z)": the latest date is promulgation
        ds = find_dates(h[m.end(): m.end() + 160])
        if ds:
            return max(x for _, x in ds), "promulgated"
    d = _after(_CUES_STRONG, h)
    if d:
        return d, "cue"
    for ln in lines[:45]:           # "Date: 04/12/2018" on the cover sheet of a court judgment
        m = re.match(r"^Date\s*:\s*(.{6,34})$", ln, re.I)
        if m:
            ds = find_dates(m.group(1))
            if ds:
                return ds[0][1], "date-label"
    d = _after(_CUES_EXPLICIT, ftail[-1200:]) or _after(_CUE_DATED, ftail[-450:])
    if d:
        return d, "tail"
    for ln in lines[:70]:           # a line that is nothing but a date
        if len(ln) < 40 and _DATE_LINE.match(ln) and not _CUE_HEARD.search(ln):
            ds = find_dates(ln)
            if ds:
                return ds[0][1], "date-line"
    d = _after(_CUE_DATED, fhead[:3000])
    if d:
        return d, "dated"
    d = _after(_CUE_SENT, ftail[-700:], window=90)
    if d:
        return d, "sent-date"
    ds = find_dates(ftail[-300:])
    if ds:
        return ds[-1][1], "tail-last"
    ds = find_dates(fhead[:400])
    if ds:
        return ds[0][1], "first-date"
    return None, None


def hearing_date(fhead):
    return _after(_CUE_HEARD, fhead[:1200], window=110)


# --------------------------------------------------------------------------- citations
_CODES = (r"UKSC|UKPC|UKHL|EWCA\s+(?:Civ|Crim)|EWHC|EWCOP|EWFC|EWCC|EWCR|UKUT|UKFTT|UKEAT|EAT|UKET|UKIPTrib|"
          r"UKSIAC|UKAITUR|UKIAT|UKVAT|UKSPC|CSOH|CSIH|HCJAC|HCJ|SAC\s+\((?:Civ|Crim)\)|SC\s+[A-Z]{3,4}|FAI|"
          r"IEHC|IESC|IECA|IECCA|NICA|NIQB|NIKB|NICh|NIFam|NICC|NIMaster|NICoroner|NICty|SGHC\(I\)|SGCA\(I\)|"
          r"SGHC|SGCA|QIC\s+\([A-Z]\)|JRC|JCA|PBSA|PBRA|PBPH|ECHR|EUECJ")
NC_RE = re.compile(r"\[((?:19|20)\d\d)\]\s*(%s)\s+(\d{1,5})(?:\s*\((\w{1,10})\))?" % _CODES)
_REPORTS = (r"AC|WLR|All\s?ER(?: \(Comm\)| \(D\))?|QB|KB|Ch|Fam|ICR|IRLR|Imm\s?AR|INLR|EHRR|CMLR|ECR|"
            r"Cr\s?App\s?R(?: \(S\))?|Lloyd['’]s Rep|BCLC|BCC|FSR|RPC|ETMR|STC|TC|SLT|SC(?: \(HL\))?|SCLR|JC|SCCR|"
            r"IR|ILRM|NI|P\s?&\s?CR|EGLR|EG|HLR|L\s?&\s?TR|PIQR|FLR|FCR|UKHRR|EMLR|Bus LR|RTR|Costs LR|"
            r"SFTD|BTC|JLR|SLR|LGR|App Cas|QBD|Ch D|KIR|ITR|CLR|Med LR|BMLR|WTLR|BLR|Con LR|ELR|COD|Env LR|JPL")
RC_RE = re.compile(r"[\[(]((?:1[6-9]|20)\d\d)[\])]\s+(?:(\d{1,3})\s+)?(%s)\s+(\d{1,4})\b" % _REPORTS)
_OLD_RC = re.compile(r"\((1[6-9]\d\d)\)\s+(\d{1,3})\s+([A-Z][A-Za-z.&' ]{1,18}?)\s+(\d{1,4})\b")

_DIV_KEEP = {"EWHC", "UKUT", "UKFTT", "EWCOP", "UKSIAC"}


def norm_nc(m):
    y, code, num, div = m.groups()
    code = flat(code)
    s = f"[{y}] {code} {int(num)}"
    if div and code in _DIV_KEEP and not div.isdigit():
        s += f" ({div})"
    return s


def norm_rc(m):
    y, vol, rep, page = m.groups()
    br = m.group(0)[0]
    return f"{br}{y}{']' if br == '[' else ')'} {vol + ' ' if vol else ''}{flat(rep)} {page}"


_NAME_TOK = re.compile(r"^(?:[A-Z(\[‘'\"][\w’'.&()/\-]*|of|the|and|&|for|de|la|v|ex|p|parte|on|application|plc|ltd|re)$")
_SOLO_TOK = re.compile(r"^[A-Z(][\w’'()&.\-]*$")
_SOLO_STOP = {"In", "See", "The", "And", "As", "Per", "Also", "Cf", "Of", "At", "By", "Tribunal", "Upper", "Court", "Appeal",
              "Supreme", "Decision", "Case", "Cases", "Both", "Following", "Applying", "Citing", "Unreported", "Reported",
              "Number", "Citation", "Neutral", "No", "Nos", "Judge", "Justice", "Lord", "Lady", "House", "Lords", "High",
              "Division", "Chamber", "Civil", "Appeals", "Reference", "Ref", "A", "An", "But", "It", "This", "That"}


def _name_before(s, pos):
    """Case name immediately preceding a citation at s[pos:], or None."""
    look = s[max(0, pos - 140): pos].rstrip(" ,;:(")
    i = max(look.rfind(" v "), look.rfind(" v. "))
    if i >= 0:
        right = look[i:].split(" ", 2)[-1] if look[i:].count(" ") >= 2 else ""
        if right and len(right) <= 80 and not re.search(r"[.;:]\s", right):
            left = []
            for t in reversed(look[:i].split(" ")[-9:]):
                if t and _NAME_TOK.match(t):
                    left.append(t)
                else:
                    break
            while left and left[-1] in ("of", "the", "and", "&", "for", "on", "v", "application"):
                left.pop()
            if left:
                return " ".join(reversed(left)) + " v " + right.strip()
    solo = []
    for t in reversed(look.split(" ")[-6:]):
        if t and _SOLO_TOK.match(t):
            solo.append(t)
        else:
            break
    while solo and solo[-1].strip("()") in _SOLO_STOP:
        solo.pop()
    name = " ".join(reversed(solo))
    return name if len(name) > 2 and not name.strip("()").isdigit() and name not in _SOLO_STOP else None


def cases_cited(full, own):
    seen, out = {re.sub(r' \(\w+\)$', '', o) for o in own}, []
    for rx, norm in ((NC_RE, norm_nc), (RC_RE, norm_rc)):
        for m in rx.finditer(full):
            c = norm(m)
            key = re.sub(r" \(\w+\)$", "", c)
            if key in seen:
                continue
            seen.add(key)
            out.append({"citation": c, "name": _name_before(full, m.start())})
            if len(out) >= 150:
                return out
    return out


_LEG = re.compile(
    r"\b((?:[A-Z][A-Za-z’']+,?|\([A-Z][A-Za-z ]+\))(?:\s+(?:[A-Z][A-Za-z’']+,?|of|and|for|the|to|in|etc\.?|&|\([A-Za-z .,]+\)|\(No\.? ?\d+\))){0,11}?"
    r"\s+(?:Act|Acts|Regulations|Rules|Order|Directive|Measure)(?:\s+\((?:Northern Ireland|Scotland|Wales|England)\))?\s+(?:1[5-9]\d\d|20[0-2]\d))\b")
_LEG_UP = re.compile(r"\b((?:[A-Z][A-Z’',]+|\([A-Z ]+\))(?:\s+(?:[A-Z][A-Z’',&]+|\([A-Z ]+\))){0,10}?\s+(?:ACT|ACTS|REGULATIONS|RULES|ORDER)\s+(?:1[5-9]\d\d|20[0-2]\d))\b")
_LEG_LEAD = re.compile(r"^(?:(?:The|In|Matter|Of|Under|And|Section|Sections|Schedule|Regulation|Rule|Article|Part|"
                       r"Paragraph|Pursuant|To|By|See|An|Amended|For|From|On|As|Whether|That|Where|Both|Act)\s+)+", re.I)
_LEG_PRE = re.compile(r"^.*\b(?:to|of|under|in|and) the (?=[A-Z])|^.* The (?=[A-Z])")
_PROV = re.compile(r"\b([Ss]ections?|[Ss]s?\.|[Rr]egulations?|[Rr]eg\.|[Aa]rticles?|[Rr]ules?|[Pp]aragraphs?)\s?"
                   r"(\d+[A-Z]{0,2}(?:\(\w{1,4}\)){0,3})\s+(?:of|to)\s+(?:the\s+)?")
_ECHR = re.compile(r"\bArticles?\s+(\d{1,2})\b(?=[^.]{0,60}(?:ECHR|Convention|Human Rights))")


def legislation_cited(full):
    acts, order = {}, []
    spans = []
    for rx, up in ((_LEG, False), (_LEG_UP, True)):
        for m in rx.finditer(full):
            name = flat(m.group(1))
            if up:
                name = smart_title(name)
            name = _LEG_PRE.sub("", _LEG_LEAD.sub("", name))
            name = re.sub(r"^(?:the|of|and|to|in|for)\s+", "", name)
            if len(name) < 10 or len(name.split()) < 3 and not name.split()[0][0].isupper():
                continue
            key = name.lower()
            if key not in acts:
                acts[key] = {"name": name, "provisions": []}
                order.append(key)
            spans.append((m.start(1), key))
    # attach "section N of the <Act>" provisions
    for m in _PROV.finditer(full):
        for start, key in spans:
            if 0 <= start - m.end() <= 60:
                p = m.group(1).lower().rstrip("s.") or "s"
                tag = {"section": "s", "s": "s", "regulation": "reg", "reg": "reg", "article": "art",
                       "rule": "r", "paragraph": "para"}.get(p, p) + " " + m.group(2)
                if tag not in acts[key]["provisions"] and len(acts[key]["provisions"]) < 12:
                    acts[key]["provisions"].append(tag)
                break
    out = [acts[k] for k in order[:80]]
    arts = sorted({int(a) for a in _ECHR.findall(full)})
    if arts:
        out.append({"name": "European Convention on Human Rights", "provisions": [f"art {a}" for a in arts]})
    return out


# --------------------------------------------------------------------------- courts
E_W, SCOT, NI, UK, IE = "England and Wales", "Scotland", "Northern Ireland", "United Kingdom", "Ireland"
_EWHC_DIV = {"Ch": "Chancery Division", "QB": "Queen's Bench Division", "KB": "King's Bench Division",
             "Admin": "Administrative Court", "Fam": "Family Division", "Comm": "Commercial Court",
             "TCC": "Technology and Construction Court", "Pat": "Patents Court", "IPEC": "Intellectual Property Enterprise Court",
             "Admlty": "Admiralty Court", "SCCO": "Senior Courts Costs Office", "Costs": "Senior Courts Costs Office",
             "Mercantile": "Mercantile Court"}
_UT_DIV = {"IAC": "Immigration and Asylum Chamber", "LC": "Lands Chamber", "TCC": "Tax and Chancery Chamber",
           "AAC": "Administrative Appeals Chamber"}
_FTT_DIV = {"TC": "Tax Chamber", "PC": "Property Chamber", "GRC": "General Regulatory Chamber",
            "HESC": "Health, Education and Social Care Chamber", "IAC": "Immigration and Asylum Chamber",
            "SEC": "Social Entitlement Chamber"}
CODE_COURT = {
    "UKSC": ("Supreme Court of the United Kingdom", UK), "UKPC": ("Judicial Committee of the Privy Council", UK),
    "UKHL": ("House of Lords", UK), "EWCA Civ": ("Court of Appeal (Civil Division)", E_W),
    "EWCA Crim": ("Court of Appeal (Criminal Division)", E_W), "EWHC": ("High Court of Justice", E_W),
    "EWCOP": ("Court of Protection", E_W), "EWFC": ("Family Court", E_W), "EWCC": ("County Court", E_W),
    "EWCR": ("Crown Court", E_W), "UKUT": ("Upper Tribunal", UK), "UKFTT": ("First-tier Tribunal", UK),
    "UKEAT": ("Employment Appeal Tribunal", UK), "EAT": ("Employment Appeal Tribunal", UK),
    "UKET": ("Employment Tribunal", E_W), "UKAITUR": ("Upper Tribunal (Immigration and Asylum Chamber)", UK),
    "UKSIAC": ("Special Immigration Appeals Commission", UK), "UKIPTrib": ("Investigatory Powers Tribunal", UK),
    "CSOH": ("Court of Session (Outer House)", SCOT), "CSIH": ("Court of Session (Inner House)", SCOT),
    "HCJAC": ("High Court of Justiciary (Appeal Court)", SCOT), "HCJ": ("High Court of Justiciary", SCOT),
    "FAI": ("Sheriff Court (Fatal Accident Inquiry)", SCOT),
    "IEHC": ("High Court of Ireland", IE), "IESC": ("Supreme Court of Ireland", IE),
    "IECA": ("Court of Appeal of Ireland", IE), "IECCA": ("Court of Criminal Appeal (Ireland)", IE),
    "NICA": ("Court of Appeal in Northern Ireland", NI), "NIQB": ("High Court (NI), Queen's Bench Division", NI),
    "NIKB": ("High Court (NI), King's Bench Division", NI), "NICh": ("High Court (NI), Chancery Division", NI),
    "NIFam": ("High Court (NI), Family Division", NI), "NICC": ("Crown Court (NI)", NI),
    "NIMaster": ("High Court (NI), Master", NI), "NICoroner": ("Coroner's Court (NI)", NI),
    "NICty": ("County Court (NI)", NI),
    "SGHC(I)": ("Singapore International Commercial Court", "Singapore"),
    "SGCA(I)": ("Court of Appeal of Singapore (SICC appeal)", "Singapore"),
    "SGHC": ("High Court of Singapore", "Singapore"), "SGCA": ("Court of Appeal of Singapore", "Singapore"),
    "JRC": ("Royal Court of Jersey", "Jersey"), "JCA": ("Court of Appeal of Jersey", "Jersey"),
    "PBSA": ("Parole Board", E_W), "PBRA": ("Parole Board", E_W), "PBPH": ("Parole Board", E_W),
}


def court_from_code(nc):
    m = NC_RE.search(nc)
    if not m:
        return None, None
    code, div = flat(m.group(2)), m.group(4)
    if code.startswith("SC "):
        return "Sheriff Court", SCOT
    if code.startswith("SAC"):
        return "Sheriff Appeal Court", SCOT
    if code.startswith("QIC"):
        return "Qatar International Court", "Qatar"
    court, jur = CODE_COURT.get(code, (None, None))
    if court and div:
        sub = {"EWHC": _EWHC_DIV, "UKUT": _UT_DIV, "UKFTT": _FTT_DIV}.get(code, {}).get(div)
        if sub:
            court = f"{court} ({sub})" if code != "EWHC" else f"{court}, {sub}"
    return court, jur


# ordered: first match wins. Searched in the first ~1500 chars of the document.
_COURT_RULES = [(re.compile(p, re.I), c, j) for p, c, j in [
    (r"employment appeal tribunal", "Employment Appeal Tribunal", UK),
    (r"employment tribunals? \(scotland\)", "Employment Tribunal (Scotland)", SCOT),
    (r"fair employment tribunal", "Fair Employment Tribunal", NI),
    (r"industrial tribunals?", "Industrial Tribunal (Northern Ireland)", NI),
    (r"employment tribunals?", "Employment Tribunal", E_W),
    (r"upper tribunal\s*\(?immigration and asylum chamber|immigration and asylum chamber|the immigration acts",
     "Upper Tribunal (Immigration and Asylum Chamber)", UK),
    (r"asylum and immigration tribunal|immigration appeal tribunal", "Asylum and Immigration Tribunal", UK),
    (r"upper tribunal\s*\(?lands chamber", "Upper Tribunal (Lands Chamber)", E_W),
    (r"lands tribunal", "Lands Tribunal", E_W),
    (r"upper tribunal\s*\(?tax and chancery", "Upper Tribunal (Tax and Chancery Chamber)", UK),
    (r"upper tribunal\s*\(?administrative appeals", "Upper Tribunal (Administrative Appeals Chamber)", UK),
    (r"upper tribunal for scotland|decision of sheriff .{0,60}first-tier tribunal for scotland", "Upper Tribunal for Scotland", SCOT),
    (r"first[- ]tier tribunal\s*\(?tax|tax chamber", "First-tier Tribunal (Tax Chamber)", UK),
    (r"property chamber|residential property tribunal|leasehold valuation tribunal|rent assessment (?:panel|committee)",
     "First-tier Tribunal (Property Chamber) / Leasehold Valuation Tribunal", E_W),
    (r"general regulatory chamber|information tribunal", "First-tier Tribunal (General Regulatory Chamber)", UK),
    (r"health,? education and social care|care standards", "First-tier Tribunal (Health, Education and Social Care Chamber)", E_W),
    (r"upper tribunal", "Upper Tribunal", UK),
    (r"first[- ]tier tribunal", "First-tier Tribunal", UK),
    (r"scottish information commissioner|decision notice \d{3}/\d{4}|freedom of information \(scotland\) act",
     "Scottish Information Commissioner", SCOT),
    (r"decision notice.{0,400}information commissioner|freedom of information act 2000 \((?:foia|section 50)\)|"
     r"environmental information regulations 2004 \(eir\)|reference:\s*(?:fs|fer|ic-)", "Information Commissioner's Office", UK),
    (r"data protection act.{0,80}undertaking|undertaking.{0,200}information commissioner", "Information Commissioner's Office", UK),
    (r"trade marks? act|patents act|registered designs act|for the registrar|intellectual property office",
     "UK Intellectual Property Office", UK),
    (r"dispute resolution service", "Nominet Dispute Resolution Service", UK),
    (r"judicial committee|privy council", "Judicial Committee of the Privy Council", UK),
    (r"parole board", "Parole Board", E_W),
    (r"singapore international commercial court", "Singapore International Commercial Court", "Singapore"),
    (r"qatar international court|qatar financial centre", "Qatar International Court", "Qatar"),
    (r"an ch[úu]irt uachtarach", "Supreme Court of Ireland", IE),
    (r"royal court", "Royal Court of Jersey", "Jersey"),
    (r"sheriff appeal court", "Sheriff Appeal Court", SCOT),
    (r"sheriffdom of", "Sheriff Court", SCOT),
    (r"outer house,? court of session", "Court of Session (Outer House)", SCOT),
    (r"inner house,? court of session|(?:first|second|extra) division,? inner house", "Court of Session (Inner House)", SCOT),
    (r"high court of justiciary", "High Court of Justiciary", SCOT),
    (r"court of session", "Court of Session", SCOT),
    (r"court of appeal in northern ireland", "Court of Appeal in Northern Ireland", NI),
    (r"high court of justice in northern ireland", "High Court of Justice in Northern Ireland", NI),
    (r"court of appeal \(?criminal division", "Court of Appeal (Criminal Division)", E_W),
    (r"court of appeal \(?civil division", "Court of Appeal (Civil Division)", E_W),
    (r"court of protection", "Court of Protection", E_W),
    (r"family court", "Family Court", E_W),
    (r"county court", "County Court", E_W),
    (r"crown court", "Crown Court", E_W),
    (r"high court of justice", "High Court of Justice", E_W),
    (r"house of lords", "House of Lords", UK),
    (r"the supreme court", "Supreme Court", None),
    (r"court of criminal appeal", "Court of Criminal Appeal", None),
    (r"the court of appeal", "Court of Appeal", None),
    (r"the high court", "High Court", None),
    (r"jury court", "Jury Court (Scotland)", SCOT),
]]
_EWHC_TXT = re.compile(r"(queen['’]s bench|king['’]s bench|chancery|family) division|(administrative|commercial|"
                       r"technology and construction|patents|admiralty|planning) court", re.I)


def court_from_text(head):
    best = None
    for idx, (rx, court, jur) in enumerate(_COURT_RULES):
        m = rx.search(head)
        if m and (best is None or (m.start() // 250, idx) < best[0]):
            best = ((m.start() // 250, idx), court, jur)
    if not best:
        return None, None
    _, court, jur = best
    if court == "High Court of Justice":
        m = _EWHC_TXT.search(head)
        if m:
            court += ", " + smart_title(m.group(0).upper())
    return court, jur


# --------------------------------------------------------------------------- labelled fields
_LABEL = re.compile(r"^([A-Z][A-Za-z’'&/() \-]{2,38}?)\s*(?::|\t)\s*(.*)$")


def labelled(lines):
    """{'label lowercased': 'value'} for 'Label: value' lines (value may be on the next line)."""
    merged = []
    for ln in lines:
        if ln in (":", ": ") and merged:
            merged[-1] += ":"
        elif ln.startswith(": ") and merged and len(merged[-1]) < 40:
            merged[-1] += ln
        else:
            merged.append(ln)
    out = {}
    for i, ln in enumerate(merged):
        m = _LABEL.match(ln)
        if not m:
            continue
        key, val = m.group(1).strip().lower(), m.group(2).strip()
        if not val and i + 1 < len(merged) and not _LABEL.match(merged[i + 1]):
            val = merged[i + 1].strip()
        if val and key not in out:
            out[key] = val
    return out


_ID_LABELS = ["case no", "case number", "case numbers", "case nos", "case ref", "case ref no", "case reference",
              "appeal number", "appeal numbers", "appeal no", "appeal nos", "appeal number(s)", "reference",
              "reference no", "claim no", "record no", "tribunal reference", "tribunal ref", "ico ref", "ico refs",
              "ico reference", "ref", "our ref", "file ref no", "case", "application no"]
_ID_TOKEN = re.compile(r"[A-Z0-9][A-Za-z0-9/\-.()]*\d[A-Za-z0-9/\-()]*")
_ID_INLINE = re.compile(r"\b(?i:Case|Appeal|Claim|Tribunal|ICO)\s*(?i:Ref(?:erence)?s?|No|Nos|Numbers?)\b"
                        r"\.?:?\s*([A-Z0-9][A-Za-z0-9/\-.]*\d[A-Za-z0-9/\-]*)")


_ID_IRISH = re.compile(r"(?:\[|Record (?:No|Number)\.?:?\s*)((?:19|20)\d\d[ /](?:No\.? )?\d+ ?[A-Z]{0,4}\.?)\]?")
_ID_BARE = re.compile(r"^(?:[A-Z]{1,5}\d*[-/][A-Z0-9][A-Z0-9/\-]{4,24}|D\d{8}|BL O/\d+/\d+|O[-/]\d{3}[-/]\d{2}|\d{5,8}/\d{2,4})$")


def case_number(lab, fhead, lines):
    for k in _ID_LABELS:
        v = lab.get(k)
        if v:
            m = _ID_TOKEN.search(v)
            if m:
                return m.group(0).rstrip(".")
    m = _ID_INLINE.search(fhead[:1500]) or _ID_IRISH.search(fhead[:600])
    if m:
        return m.group(1).rstrip(".")
    for ln in lines[:10]:
        if _ID_BARE.match(ln):
            return ln
    return None


# --------------------------------------------------------------------------- parties
_ROLES = r"Claimants?|Respondents?|Appellants?|Applicants?|Defendants?|Plaintiffs?|Pursuers?|Defenders?|Petitioners?|Complainers?|Minuters?|Notice Part(?:y|ies)|Interested Part(?:y|ies)|Reclaimers?"
_ROLE_LINE = re.compile(r"^\(?(?:(?:first|second|third|fourth|1st|2nd|3rd)\s+)?(%s)(?:\s*(?:/|and|&)\s*(?:%s))*\)?[:;.,/]?$" % (_ROLES, _ROLES), re.I)
_LABEL_WORD = re.compile(r"^(?:case ref(?:erence)?|property|premises|representatives?|type of application|tribunal(?: members?| judge)?|"
                         r"venue|date of (?:decision|hearing)|applicants?|respondents?)\s*:?$", re.I)
_SEP_LINE = re.compile(r"^[-–—_\s]*(?:and|v|v\.|vs\.?|-v-|against|between|b e t w e e n)[-–—_\s:/.]*$", re.I)
_JUDGE_WORD = re.compile(r"\b(?:judge|justice|lord|lady|sir|dame|honour|hon\.?|president|recorder|sheriff|bailiff|"
                         r"jurats?|commissioner|chairman|chair|master|registrar|member)\b", re.I)
_BOUNDARY = re.compile(
    r"^(?:before|between|b e t w e e n|in the (?:cause|petition|appeal|application|matter)|in$|by$|in the causes|"
    r"representation|appearances|judgment|opinion|decision|determination|heard|date|on\b|at the tribunal|"
    r"the immigration acts|transcript|approved|unapproved|©|case ref|property|premises|representative|type of application|"
    r"tribunal member|venue|date of|in person|represented by|\d{1,3}$|.*\[(?:19|20)\d\d\]|.*\b(?:tribunals?|court|chamber|division|record n|citation|"
    r"instructed by|counsel|solicitors?|for the (?:appellant|respondent|claimant|applicant))\b|[-–—_=. ]{3,}$)", re.I)
_REP_START = re.compile(r"^(?:representation|appearances|for the (?:appellant|claimant|applicant|pursuer|petitioner))\b", re.I)
_SPACED = re.compile(r"\b(?:[A-Z] ){3,}[A-Z]\b")
_INITIALS = re.compile(r"(?:(?:MR|MRS|MS|MISS) )?[A-Z]{1,3}(?: [A-Z]{1,2}){0,3}(?: ?\([A-Za-z ]+\))?")
_LINE_NO = re.compile(r"^(?:[1-9]?[05])\s+(?=[A-Z])")
_PAREN_LINE = re.compile(r"^\(.*\)$")
_NUM_PREFIX = re.compile(r"^(?:\(?(?:\d{1,2}|first|second|third|fourth|fifth|i{1,3}|iv)\)[.:]?|\d{1,2}\.)\s*", re.I)
_PARTY_LABELS = {"claimant": "claimant", "claimants": "claimant", "respondent": "respondent", "respondents": "respondent",
                 "appellant": "appellant", "appellants": "appellant", "applicant": "applicant", "applicants": "applicant",
                 "public authority": "public authority", "complainant": "complainant", "lead complainant": "complainant",
                 "additional party": "additional party", "second respondent": "respondent", "defendant": "defendant",
                 "data controller": "data controller", "authority": "public authority",
                 "applicant (nominee purchaser)": "applicant", "landlord": "respondent", "tenant": "applicant"}
_SIDE_A = {"claimant", "appellant", "applicant", "plaintiff", "pursuer", "petitioner", "complainant", "complainer", "reclaimer", "party a"}
_SIDE_B = {"respondent", "defendant", "defender", "public authority", "data controller", "party b"}
_INLINE_V = re.compile(r"^(.{3,90}?)\s+(?:against|v\.?|versus|-v-|contra)\s+(.{2,90}?)[.,]?$", re.I)
_INLINE_AG = re.compile(r"^(.{3,90}?)\s+(?:against|contra)\s+(.{2,90}?)[.,]?$", re.I)
_HMRC = re.compile(r"commissioners for h", re.I)


def _clean_party(s):
    s = _NUM_PREFIX.sub("", _LINE_NO.sub("", flat(s))).strip(" ,;:.-–")
    s = _SPACED.sub(lambda m: m.group(0).replace(" ", ""), s)
    if _HMRC.search(s):
        return "The Commissioners for HM Revenue & Customs"
    if _INITIALS.fullmatch(s):
        return s                                    # anonymised party, e.g. "A M G", "FAK (Iran)"
    toks = s.split(" ")
    if len(toks) >= 6 and sum(len(t) == 1 for t in toks) / len(toks) > 0.5:
        return ""                                   # letter-spaced OCR debris
    t = smart_title(s)
    if t == s:                                      # mixed case: only tame long shouting tokens
        t = " ".join(w.capitalize() if w.isupper() and len(w) > 4 and w.isalpha() and w not in _KEEP else w for w in toks)
    return t


def _role(s):
    r = s.lower().strip("()[]:;., ")
    r = re.sub(r"^(?:first|second|third|fourth|1st|2nd|3rd)\s+", "", r)
    r = re.split(r"\s*(?:/|and|&)\s*", r)[0]
    return {"parties": "party", "complainers": "complainer"}.get(r, r[:-1] if r.endswith("s") else r)


def parties_from_roles(lines):
    out = []
    n = min(len(lines), 90)
    if any(l.lower().rstrip(": ") in ("property", "case reference", "type of application") for l in lines[:40]):
        for i in range(n - 1):          # property-tribunal layout: the label comes first, the value after it
            if _ROLE_LINE.match(lines[i]) and not _LABEL_WORD.match(lines[i + 1]) and lines[i + 1] != ":":
                name = _clean_party(lines[i + 1])
                if name and len(name) < 100:
                    out.append({"role": _role(lines[i]), "name": name})
        return out
    for i in range(n):
        if _REP_START.match(lines[i]) and out:
            break
        if not _ROLE_LINE.match(lines[i]):
            continue
        names, j = [], i - 1
        while j >= 0 and len(names) < 5:
            ln = lines[j]
            if not names and ln.endswith("/") and _ROLE_LINE.match(ln):     # "APPLICANT/" + "APPELLANT"
                j -= 1
                continue
            if _SEP_LINE.match(ln) or _ROLE_LINE.match(ln) or _BOUNDARY.match(ln) or _BENCH_LINE.match(ln) or len(ln) > 110:
                break
            if _PAREN_LINE.match(ln) and len(ln) > 14:        # (ANONYMITY DIRECTION NOT MADE)
                j -= 1
                continue
            if _JUDGE_WORD.search(ln) and not re.search(r"secretary|commissioners?\b.*(?:revenue|police)|advocate", ln, re.I):
                break
            names.append(ln)
            j -= 1
        if not names:
            continue
        names.reverse()
        groups = []
        plural = lines[i].lower().rstrip(":;.,/) ").endswith("s") and all(len(x.split()) >= 2 for x in names)
        for ln in names:
            if _NUM_PREFIX.match(ln) or not groups or plural:
                groups.append(ln)
            else:
                groups[-1] += " " + ln
        role = _role(lines[i])
        for g in groups:
            name = _clean_party(g)
            if name:
                out.append({"role": role, "name": name})
    return out


_COUNSEL = re.compile(r";|\b(?:KC|QC|Advocate|Counsel|instructed)\b|^in person", re.I)


def parties_from_labels(lab):
    out = []
    for k, role in _PARTY_LABELS.items():
        v = lab.get(k)
        if v and len(v) < 140 and not _ROLE_LINE.match(v) and not _COUNSEL.search(v):
            out.append({"role": role, "name": _clean_party(v)})
    return out


_V_ONLY = re.compile(r"-?\s*(?:v|v\.|vs|-v-|against|and)\s*-?", re.I)
_V_IN = re.compile(r"\s+(?:AND|-v-|v\.?|V)\s+")


def parties_from_header(lines):
    """Employment-tribunal layout: 'Claimant' / 'Respondent' column heads, then 'A', 'v|AND', 'B'."""
    for i in range(min(len(lines), 40) - 3):
        if _ROLE_LINE.match(lines[i]) and _ROLE_LINE.match(lines[i + 1]):
            ra, rb = _role(lines[i]), _role(lines[i + 1])
            a = lines[i + 2]
            if i + 4 < len(lines) and _V_ONLY.fullmatch(lines[i + 3]):
                b = lines[i + 4]
            else:
                parts = _V_IN.split(a, 1)
                if len(parts) != 2:
                    return []
                a, b = parts
            a, b = _clean_party(a), _clean_party(re.sub(r"\s*\((?:first|1st) respondent\)", "", b, flags=re.I))
            if a and b and len(a) < 100 and len(b) < 100:
                return [{"role": ra, "name": a}, {"role": rb, "name": b}]
            return []
    return []


def parties_from_v(lines, old_report=False):
    if not old_report:
        for i in range(1, min(len(lines), 70) - 1):
            if re.fullmatch(r"-?\s*(?:v|v\.|vs|-v-|against)\s*-?", lines[i], re.I):
                a, b = lines[i - 1], lines[i + 1]
                if 1 < len(a) < 100 and 1 < len(b) < 100 and not _BOUNDARY.match(a):
                    a, b = _clean_party(a), _clean_party(b)
                    if a and b:
                        return [{"role": "party a", "name": a}, {"role": "party b", "name": b}]
    rx = _INLINE_AG if old_report else _INLINE_V
    for ln in lines[:40]:
        m = rx.match(ln)
        if not m or len(ln) > 160 or ln.lower().startswith(("appeal", "in ", "on appeal", "decision")):
            continue
        a, b = m.group(1), m.group(2)
        if not b[0].isupper() or not a[0].isupper() or ".-" in a or len(a.split()) > 9 or "Fol. Dic" in a:
            continue
        if old_report and not all(t[0].isupper() or t in ("of", "and", "the", "&c", "&") for t in a.split()):
            continue
        if old_report and not re.search(r"[A-Z]{4,}", ln):
            continue
        a, b = _clean_party(a), _clean_party(b)
        if a and b:
            return [{"role": "party a", "name": a}, {"role": "party b", "name": b}]
    return []


def make_case_name(parties):
    a = [p["name"] for p in parties if p["role"] in _SIDE_A]
    b = [p["name"] for p in parties if p["role"] in _SIDE_B]
    side = lambda xs: xs[0] + (" & Ors" if len(xs) > 2 else (" & Anor" if len(xs) == 2 else ""))
    if a and b:
        return f"{side(a)} v {side(b)}"
    if a or b:
        return side(a or b)
    return None


# --------------------------------------------------------------------------- judges
_BEFORE = re.compile(r"^before\b\s*[:;]?\s*(.*)$", re.I)
_JSTOP = re.compile(r"^(?:between|b e t w e e n|representation|appearances|in the matter|heard|sitting|claimant|"
                    r"appellant|respondent|the immigration acts|judgment|decision|subject matter|in the |"
                    r"attorney general|[-–—_=. ]{3,}$)|.*[:\d]", re.I)
_NAMEY = re.compile(r"^(?:[A-Z][\w’'.\-]*\.?,?\s?){1,6}(?:\([^)]{1,40}\))?$")
_J_OF = re.compile(r"\b(?:JUDGMENT|RULING|DECISION|OPINION|NOTE|DETERMINATION)\s+(?:OF|BY)\s+(?:THE COURT\s+)?(?:delivered by\s+)?"
                   r"((?:THE\s+)?(?:HON(?:OURABLE)?\.?\s+)?(?:MR\.?|MS\.?|MRS\.?|LORD|LADY|SHERIFF(?: PRINCIPAL)?|SUMMARY SHERIFF|"
                   r"JUDGE|PRESIDENT|CHIEF JUSTICE)\b[^,;:]{2,60}?)(?=\s+(?:delivered|dated|given|on\s|in\s|IN\s|handed)|[,;:]|$)", re.I)
_DELIV = re.compile(r"delivered by\s+((?:the\s+)?(?:LORD|LADY|SHERIFF|MR\.?|MS\.?|Lord|Lady)\s[^,;:]{2,50}?)(?=\s+(?:the|in|on|IN)\b|[,;:]|$)")
_BENCH_LINE = re.compile(r"^(?:(?:Lord|Lady)(?: Justice(?: General| Clerk)?| President)?(?: [A-Z][\w’']+){0,3}"
                         r"|The President|[A-Z][\w’'. \-]{2,28}? (?:C\.?J\.?|J\.?|P\.?|JJ\.?))$")
_EJ = re.compile(r"Employment Judge:?\s+((?:[A-Z][\w’'.\-]*\s?){1,4})")
_SIGNED = re.compile(r"^signed\b[:.… _]*(.*)$", re.I)


def _strip_j(s):
    s = re.sub(r"\((?:sitting|in chambers|chairman|president|vice)[^)]*\)", "", flat(s), flags=re.I).strip(" ,;:._")
    return smart_title(s)


def judges_of(lines, tail_lines, lab, fhead, court):
    out = []
    for i, ln in enumerate(lines[:90]):
        m = _BEFORE.match(ln)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest and len(rest) < 160:
            out.append(rest)
        for k, nxt in enumerate(lines[i + 1: i + 9]):
            if nxt.lower() in ("and", "judge", "&", "members", "members:"):
                continue
            if _PAREN_LINE.match(nxt):
                continue
            after = lines[i + 2 + k] if i + 2 + k < len(lines) else ""
            if _JSTOP.match(nxt) or len(nxt) > 70 or _ROLE_LINE.match(after) or _PAREN_LINE.match(after) and "ANONYM" in after.upper():
                break
            if _JUDGE_WORD.search(nxt) or _NAMEY.match(nxt):
                out.append(nxt)
            else:
                break
        break
    if not out:
        for k in ("tribunal", "tribunal member", "tribunal members", "tribunal member(s)", "judge", "employment judge",
                  "chairman", "leasehold valuation tribunal", "coram"):
            if lab.get(k) and len(lab[k]) < 90 and not any(c.isdigit() for c in lab[k]):
                out.append(lab[k])
                break
    if not out:
        for ln in lines[:45]:
            m = _J_OF.search(ln) or _DELIV.search(ln)
            if m:
                out.append(m.group(1))
        bench = [ln for ln in lines[:16] if _BENCH_LINE.match(ln)]
        out += bench
    if not out:
        m = _EJ.search(fhead[:2500]) or _EJ.search(" ".join(tail_lines[-25:]))
        if m:
            out.append("Employment Judge " + re.sub(r"\s+(?:Date|Dated|On|Sent|Judgment|JUDGMENT|REASONS)\b.*$", "", m.group(1).strip()))
    if not out and tail_lines:      # signatory at the end (ICO, IPO, Nominet, property tribunals)
        tl = tail_lines[-14:]
        for i, ln in enumerate(tl):
            if ln.lower().startswith("for the registrar") and i:
                out.append(tl[i - 1])
                break
            m = _SIGNED.match(ln)
            if m:
                cand = [m.group(1).strip(" .…_")] + tl[i + 1: i + 4]
                for c in cand:
                    c = re.sub(r"^dated?\b.*$", "", c, flags=re.I).strip()
                    if c and _NAMEY.match(c) and not find_dates(c):
                        out.append(c)
                        break
                break
    seen, res = set(), []
    for j in out:
        j = _strip_j(j)
        if 2 < len(j) < 120 and j.lower() not in seen:
            seen.add(j.lower())
            res.append(j)
    return res[:9]


# --------------------------------------------------------------------------- stage / outcome
_STAGE = [(re.compile(p, re.I), s) for p, s in [
    (r"permission to appeal|leave to appeal", "Application for permission to appeal"),
    (r"judicial review", "Judicial review"),
    (r"appeal against sentence", "Appeal against sentence"),
    (r"appeal against conviction", "Appeal against conviction"),
    (r"judgment on withdrawal|dismissed (?:up)?on withdrawal|dismissed following (?:a |the )?withdrawal|withdrawal of the claim", "Dismissal on withdrawal"),
    (r"rule 21", "Default judgment (rule 21)"),
    (r"reconsideration", "Reconsideration"),
    (r"preliminary hearing", "Preliminary hearing"),
    (r"remedy (?:hearing|judgment)|judgment on remedy", "Remedy hearing"),
    (r"hearing on expenses|costs (?:judgment|hearing|application)|application for (?:costs|expenses)|judgment on costs", "Costs"),
    (r"strike[- ]out|struck out", "Strike-out"),
    (r"sentenc", "Sentencing"),
    (r"application for bail|bail application", "Bail application"),
    (r"opposition", "Trade mark opposition"),
    (r"invalid(?:ation|ity)", "Trade mark invalidation"),
    (r"revocation", "Revocation application"),
    (r"decision notice", "Regulator decision on complaint"),
    (r"dispute resolution service", "Domain name dispute (expert decision)"),
    (r"undertaking", "Regulatory undertaking"),
    (r"upper tribunal|appeal tribunal|court of appeal|appellant|on appeal from|inner house|supreme court|house of lords|privy council|appeal court", "Appeal"),
]]


_HDR_CUT = re.compile(r"\b(?:DECISION AND REASONS|DETERMINATION AND REASONS|REASONS FOR|Introduction|INTRODUCTION|Background)\b|\s1\.\s|\[1\]")
_APPELLATE = re.compile(r"Upper Tribunal|Appeal|Supreme Court|House of Lords|Privy Council|Inner House|Lands Tribunal")
_FIRST_ONLY = ("Sentencing", "Strike-out", "Costs", "Preliminary hearing", "Reconsideration")


def stage_of(fhead, court, parties):
    h = fhead[:700]
    m = _HDR_CUT.search(h, 60)
    if m:
        h = h[: m.start()]
    appellate = bool(court and _APPELLATE.search(court)) or any(p["role"] == "appellant" for p in parties) \
        or "on appeal from" in h.lower()
    for rx, s in _STAGE:
        if rx.search(h) and not (appellate and s in _FIRST_ONLY) \
                and not (s.startswith("Regulator") and "Commissioner" not in (court or "")):
            return s
    return "Appeal" if appellate else "First instance / substantive decision"


_BE = r"(?:be |is |are |was |were |must be |should be |will be |stands? |has been |have been )?(?:hereby |therefore |accordingly |each )?"
_OUT = [(re.compile(p, re.I), s) for p, s in [
    (r"dismissed (?:up)?on withdrawal|dismissed following (?:a |the )?withdrawal", "Dismissed on withdrawal"),
    (r"appeals? " + _BE + r"(?:allowed|upheld|succeeds?|sustained)|allow(?:s|ed)? (?:the|this|both|these|that) appeals?|appeals? allowed", "Appeal allowed"),
    (r"appeals? " + _BE + r"(?:dismissed|refused)|(?:dismiss|refus)(?:es|ed|e)? (?:the|this|both|these|that) appeals?|"
     r"appeals? (?:fails?|dismissed|refused)|decision (?:of the first-tier tribunal )?(?:shall |will )?stands?", "Appeal dismissed"),
    (r"(?:does|did) not (?:involve|contain|disclose) (?:the making of )?(?:an?|any) (?:material )?errors? (?:of|on a point of) law|"
     r"no (?:material )?errors? (?:of|in) law|uphold the (?:original )?decision|decision[^.]{0,80}? is upheld", "Appeal dismissed"),
    (r"set(?:s|ting)? aside (?:the|that) decision|decision[^.]{0,60}?(?:is|be) set aside", "Decision set aside"),
    (r"(?:appeal|case|matter) is remitted|remit(?:s|ted)? (?:the|this) (?:appeal|case|matter)|be remitted", "Remitted"),
    (r"\bstruck out", "Struck out"),
    (r"(?:claim|complaint)s?[^.]{0,90}?(?:succeeds?|(?:is|are|was) well[- ]founded|(?:is|are) upheld)|was unfairly dismissed|"
     r"judgment for the claimant|respondent (?:shall|is ordered to|must|do) pay|made an unauthorised deduction", "Claim succeeds"),
    (r"(?:claim|complaint|proceeding)s?[^.]{0,90}?(?:fails?|(?:is|are|be) dismissed|not well[- ]founded|(?:is|are) not upheld)", "Claim dismissed"),
    (r"requires? the (?:public authority|council|trust|[a-z ]{3,30}) to take the following steps|"
     r"must (?:now )?(?:disclose|issue a fresh response|provide)", "Complaint upheld - steps required"),
    (r"does not require [^.]{0,80}?steps|no (?:further )?steps (?:are |is )?required|requires? no (?:further )?(?:steps|action)|"
     r"not require [^.]{0,60}?any (?:further )?action", "No steps required"),
    (r"oppositions? (?:therefore |accordingly )?(?:succeeds?|is successful|ha(?:s|ve|ving) (?:been successful|succeeded)|succeeded)", "Opposition succeeds"),
    (r"oppositions? (?:therefore |accordingly )?(?:fails?|is unsuccessful|ha(?:s|ve|ving) failed|failed)", "Opposition fails"),
    (r"domain name[^.]{0,90}?(?:be|should be) transferred|decision transfer x", "Domain name transferred"),
    (r"application " + _BE + r"(?:granted|allowed|succeeds)|grant(?:s|ed)? (?:the )?(?:application|dispensation|permission)|"
     r"permission (?:to appeal )?" + _BE + r"granted|decided to grant|dispens(?:e|es|ed|ation)\b[^.]{0,60}?(?:granted|with the)", "Application granted"),
    (r"application " + _BE + r"(?:refused|dismissed|rejected)|permission (?:to appeal )?" + _BE + r"refused|"
     r"refus(?:e|es|ed) (?:the )?(?:application|permission|leave)|is (?:therefore )?refused", "Application refused"),
    (r"interlocutors?[^.]{0,80}?affirmed|judgment[^.]{0,50}?affirmed|\baffirmed\b", "Affirmed"),
    (r"interlocutors?[^.]{0,80}?reversed|judgment[^.]{0,50}?reversed|\breversing the judgment", "Reversed"),
]]
_DEC_HDR = re.compile(r"\b(?:JUDGMENT|Decision \(including any steps ordered\)|Decision of the [Tt]ribunal|Notice of Decision|"
                      r"DECISION|ORDER|Summary [Dd]ecision|The Tribunal has decided)\b")
_HDR_SKIP = re.compile(r"^\s*(?:AND|&) REASONS|^\s*NOTICE|^\s*OF\b", re.I)


_CLAIM_LABELS = ("Claim succeeds", "Claim dismissed")
_APPEAL_BOILER = re.compile(r"rights? of appeal|permission to appeal must|wish(?:es|ing)? to appeal|"
                            r"if the tribunal refuses|application for permission to appeal", re.I)


def outcome_of(fhead, ftail, catch, court="", appellate=False):
    claims_ok = bool(court) and ("Employment" in court or "Industrial" in court) and not appellate
    skip = set() if claims_ok else set(_CLAIM_LABELS)
    if appellate:
        skip |= {"Application granted", "Application refused"}
    m = _APPEAL_BOILER.search(ftail)
    if m:
        ftail = ftail[: m.start()]
    head_zone = None
    for m in _DEC_HDR.finditer(fhead[:4000]):
        if not _HDR_SKIP.match(fhead[m.end(): m.end() + 14]):
            head_zone = (fhead[m.end(): m.end() + 900], False)
            break
    zones = [(catch, False)] if catch else []
    head_first = claims_ok or "Information Commissioner" in court
    if head_zone and head_first:
        zones.append(head_zone)
    zones.append((ftail, True))
    if head_zone and not head_first and not appellate:
        zones.append(head_zone)
    for zone, last in zones:
        best = None
        for rx, label in _OUT:
            if label in skip:
                continue
            ms = list(rx.finditer(zone))
            if not ms:
                continue
            m = ms[-1] if last else ms[0]
            if best is None or (m.start() > best[0] if last else m.start() < best[0]):
                best = (m.start(), m.end(), label)
        if best:
            s, e, label = best
            a = max(zone.rfind(". ", 0, s) + 2, s - 160, 0)
            b = zone.find(". ", e)
            b = min(b + 1 if b >= 0 else len(zone), e + 160)
            if label == "Appeal allowed" and re.search(r"remit", zone, re.I):
                label = "Appeal allowed - remitted"
            labels = {l for rx, l in _OUT if rx.search(zone)}
            if claims_ok and {"Claim succeeds", "Claim dismissed"} <= labels:
                label = "Claim succeeds in part"
            return label, zone[a:b].strip()
    return None, None


# --------------------------------------------------------------------------- topics / catchwords
_TOPICS = [(name, re.compile(r"\b(?:%s)" % pat)) for name, pat in [
    ("Employment", r"employment tribunal|unfair(?:ly)? dismiss|redundan|unlawful deduction|wages|holiday pay|notice pay|acas|employer|employee|whistleblow|protected disclosure|constructive dismissal"),
    ("Discrimination & equality", r"equality act|discriminat|harassment|victimisation|reasonable adjustments|protected characteristic|disability"),
    ("Immigration & asylum", r"immigration|asylum|leave to remain|entry clearance|deport|home department|refugee|eea regulations|humanitarian protection|residence card|human rights claim"),
    ("Information rights (FOI/EIR)", r"freedom of information|environmental information regulations|foia|information commissioner|decision notice|vexatious|public authority"),
    ("Data protection", r"data protection|gdpr|personal data|subject access|data controller"),
    ("Intellectual property", r"trade ?marks?|patents?|registered design|copyright|passing off|domain name|likelihood of confusion|abusive registration"),
    ("Tax", r"income tax|vat|value added tax|hmrc|revenue (?:and|&) customs|inland revenue|corporation tax|capital gains|stamp duty|tax(?:es)? (?:act|chamber)|assessments?|surcharge"),
    ("Landlord & tenant / housing", r"leasehold|service charges?|landlord|tenan(?:t|cy)|lessees?|enfranchisement|rent|housing act|tack"),
    ("Property & land", r"land registration|easement|servitude|boundary|heritable|infeft|disposition|adjudication|conveyanc|freehold|title to|feu|superior|valuation"),
    ("Criminal", r"sentenc|convict|indictment|accused|prosecut|offence|attorney general|majesty[’']s advocate|larceny|assault|imprisonment|bail|procurator fiscal|theft|murder"),
    ("Family & children", r"child(?:ren)?|custody|divorce|marriage|spouse|care order|adoption|guardianship|matrimonial|permanence order|maintenance|aliment|husband|wife"),
    ("Planning & environment", r"planning permission|an bord plean[áa]la|environmental impact|development plan|planning (?:authority|and development)|habitats"),
    ("Public law & human rights", r"judicial review|certiorari|ultra vires|legitimate expectation|human rights|article \d+ (?:echr|of the convention)|constitution"),
    ("Contract & commercial", r"contract|breach of|agreement|damages|sale of goods|guarantee|arbitrat|bill of exchange|insurance|cautioner|partnership|debt"),
    ("Insolvency & company", r"liquidat|bankrupt|insolven|winding[- ]up|sequestrat|administrator|companies act|receiver|creditors?"),
    ("Tort / delict & personal injury", r"negligen|personal injur|duty of care|accident|reparation|defamat|nuisance|slander|libel"),
    ("Succession, trusts & estates", r"testament|executors?|trustees?|trust deed|legac|heirs?|succession|probate|tutor|curator|entail|deceased"),
    ("Civil procedure & costs", r"costs|expenses|strike[- ]out|discovery|disclosure|limitation|prescription|jurisdiction|interlocutor|time limit"),
    ("Social security & pensions", r"social security|universal credit|disability living allowance|pension|personal independence payment|benefit"),
    ("Mental health & capacity", r"mental health|mental capacity|court of protection|incapacity|guardianship order"),
    ("Prisons & parole", r"parole|prisoner|prison service|recall"),
]]
_COURT_TOPIC = {"Employment": "Employment", "Industrial Tribunal": "Employment", "Fair Employment": "Employment",
                "Immigration and Asylum": "Immigration & asylum", "Information Commissioner": "Information rights (FOI/EIR)",
                "General Regulatory": "Information rights (FOI/EIR)", "Intellectual Property": "Intellectual property",
                "Nominet": "Intellectual property", "Tax Chamber": "Tax", "Property Chamber": "Landlord & tenant / housing",
                "Lands": "Property & land", "Parole": "Prisons & parole", "Justiciary": "Criminal",
                "Criminal": "Criminal", "Court of Protection": "Mental health & capacity", "Family": "Family & children"}


def topics_of(fhead, court):
    text = fhead[:6000].lower()
    scores = {}
    for name, rx in _TOPICS:
        found = rx.findall(text)
        if found:
            scores[name] = len(set(found)) + min(len(found), 12) / 6
    if court:
        for key, name in _COURT_TOPIC.items():
            if key in court:
                scores[name] = scores.get(name, 0) + 4
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return [n for i, (n, s) in enumerate(ranked[:3]) if s >= 2 and (i == 0 or s >= max(3.2, ranked[0][1] * 0.5))]


_DASH = re.compile(r"\s[–—-]\s|—|–\s|\s–")
_DASH_ONLY = re.compile(r"^[-–—_ .=]+$")
_SG_CW = re.compile(r"^\[(.+?—.+?)\]$")
_CW_STOP = re.compile(r"^(?:first[- ]tier|upper tribunal|in the|before|between|tribunal|©|appeal number|\[?(?:19|20)\d\d\]?)|.*\b(?:Appellants?|Respondents?)$", re.I)


def catchwords_of(pages):
    """Editorial keywords: dash-separated rubric near the top, or an explicit SUMMARY heading."""
    for pg in pages[:2]:
        lines = [l.strip() for l in pg.split("\n") if l.strip()][:45]
        sg = [m.group(1) for m in map(_SG_CW.match, lines) if m]
        if sg:
            return [flat(x) for part in sg for x in re.split(r"\s*—\s*", part)], None
        for i, ln in enumerate(lines):
            if _DASH_ONLY.match(ln) or len(_DASH.findall(ln)) < 2 or find_dates(ln) and len(ln) < 40:
                continue
            if sum(c.isalpha() for c in ln) < len(ln) * 0.5 or re.search(r"\band\b\s*[-–—]|[-–—]\s*and\b|^[-–—]", ln, re.I):
                continue
            block = [ln]
            for nxt in lines[i + 1: i + 6]:
                if _CW_STOP.match(nxt) or _DASH_ONLY.match(nxt) or (nxt.isupper() and not _DASH.search(nxt)):
                    break
                if block[-1].rstrip().endswith(".") and not _DASH.search(nxt):
                    break
                block.append(nxt)
            text = flat(" ".join(block))
            items = [x.strip(" .—–-") for x in re.split(r"\s+[–—-]\s+|—|\s+–|–\s+", text)]
            items = [x for x in items if 1 < len(x) < 200]
            if len(items) >= 2:
                rest = flat(" ".join(lines[i + len(block): i + len(block) + 12]))
                return items[:20], rest
    return [], None


_SUMM_HDR = re.compile(r"^(?:summary|headnote|decision \(including any steps ordered\)|summary of decision)\s*:?$", re.I)


def summary_of(pages):
    for pg in pages[:3]:
        lines = [l.strip() for l in pg.split("\n") if l.strip()]
        for i, ln in enumerate(lines[:60]):
            if _SUMM_HDR.match(ln):
                txt = flat(" ".join(lines[i + 1: i + 14]))
                txt = re.sub(r"^1\.\s*", "", txt)
                if len(txt) > 60:
                    return txt[:600]
    return None


# --------------------------------------------------------------------------- law report series (filename)
_SERIES_FN = [
    (re.compile(r"^Mor(\d\d)(\d+)-(\d+)"), "Morison's Dictionary of Decisions", "Mor", "Court of Session", SCOT),
    (re.compile(r"^Mor(\d\d)([A-Z]+)-(\d+)"), "Morison's Dictionary of Decisions (Appendix)", "Mor App", "Court of Session", SCOT),
    (re.compile(r"^Brn(\d\d)(\d{4})-(\d+)"), "Brown's Supplement to Morison's Dictionary", "Brn", "Court of Session", SCOT),
    (re.compile(r"^Elchies(\d\d)(\d{4})-(\d+)"), "Elchies' Decisions", "Elchies", "Court of Session", SCOT),
    (re.compile(r"^Hailes(\d\d)(\d{4})-(\d+)"), "Hailes' Decisions", "Hailes", "Court of Session", SCOT),
    (re.compile(r"^(\d+)SLR(\d{4})()"), "Scottish Law Reporter", "SLR", None, SCOT),
    (re.compile(r"^TC_(\d+)_(\d+)()"), "Reports of Tax Cases", "TC", None, UK),
]
_REPORTERS = {"Paton": ("Paton's House of Lords Appeal Cases", "Pat"), "WS": ("Wilson & Shaw's House of Lords Cases", "W & S"),
              "Macqueen": ("Macqueen's House of Lords Cases", "Macq"), "Paterson": ("Paterson's Scotch Appeals", "Paterson"),
              "Bligh": ("Bligh's House of Lords Reports", "Bligh"), "Dow": ("Dow's House of Lords Cases", "Dow"),
              "Rob": ("Robinson's House of Lords Appeals", "Rob"), "SM": ("Shaw & Maclean's House of Lords Cases", "S & M"),
              "Bell": ("Bell's House of Lords Appeal Cases", "Bell"), "Murray": ("Murray's Jury Court Reports", "Mur"),
              "Shaw": ("Shaw's House of Lords Appeals", "Shaw"), "Craigie": ("Craigie, Stewart & Paton", "C S & P")}
_REP_FN = re.compile(r"^(\d+)_([A-Za-z]+)_(\d+)")
_REP_FN1 = re.compile(r"^(Robertson|MacRob)_(\d+)")
_OLD_YEAR = re.compile(r"\b(1[5-8]\d\d)[.,;]\s")
_MOR_DATE = re.compile(r"\b(1[5-8]\d\d)[.,;]?\s+" + _MON + r"\.?\s+(\d{1,2})\b", re.I)
_SQUASH_COURT = [("houseoflords", "House of Lords", UK), ("courtofappeal", "Court of Appeal", E_W),
                 ("courtofsession", "Court of Session", SCOT), ("highcourtofjustice", "High Court of Justice", E_W),
                 ("jurycourt", "Jury Court (Scotland)", SCOT), ("courtofexchequer", "Court of Exchequer", None)]


def report_fields(name, fhead):
    stem = name.rsplit(".", 1)[0]
    r = {}
    for rx, series, abbr, court, jur in _SERIES_FN:
        m = rx.match(stem)
        if m:
            vol, page, no = m.group(1), m.group(2), m.group(3)
            r.update(report_series=series, volume=str(int(vol)), _abbr=abbr, _court=court, _jur=jur)
            if page.isdigit():
                r["start_page"] = str(int(page))
            else:
                r["dictionary_title"] = page.title()
            if no:
                r["report_item_no"] = str(int(no))
            break
    else:
        m = _REP_FN.match(stem)
        if m and (m.group(2) in _REPORTERS or m.group(2)[0].isupper() and not m.group(2).isupper()):
            series, abbr = _REPORTERS.get(m.group(2), (m.group(2) + " (nominate reports)", m.group(2)))
            court = "Jury Court (Scotland)" if m.group(2) == "Murray" else "House of Lords"
            r.update(report_series=series, volume=str(int(m.group(1))), start_page=str(int(m.group(3))),
                     _abbr=abbr, _court=court, _jur=SCOT if "Scot" in fhead[:3000] or m.group(2) in ("Paton", "Paterson", "Murray", "WS", "SM", "Macqueen", "Bell") else UK)
        else:
            m = _REP_FN1.match(stem)
            if m:
                series, abbr = {"Robertson": ("Robertson's House of Lords Appeals", "Robert"),
                                "MacRob": ("Maclean & Robinson's House of Lords Cases", "Macl & R")}[m.group(1)]
                r.update(report_series=series, start_page=str(int(m.group(2))), _abbr=abbr, _court="House of Lords", _jur=SCOT)
    if r.get("_abbr") in ("TC", "SLR") or r and not r.get("_court"):
        sq = re.sub(r"[^a-z]", "", fhead[:900].lower())
        hits = [(sq.find(k), c, j) for k, c, j in _SQUASH_COURT if k in sq]
        if hits:                        # final court reached is listed last in the report header
            _, r["_court"], j = max(hits)
            r["_jur"] = r.get("_jur") or j
    return r


# --------------------------------------------------------------------------- filename-derived identifiers
_FN_NC = [
    (re.compile(r"^\[?((?:19|20)\d\d)\]?_?(IEHC|IESC|IECA|IECCA|CSOH|CSIH|HCJAC|HCJ|csoh|csih|hcjac|EWHC|UKSC|UKPC)_?(\d+)"),
     lambda m: f"[{m.group(1)}] {m.group(2).upper()} {int(m.group(3))}"),
    (re.compile(r"^((?:19|20)\d\d)_(SGHCI|SGCAI)_(\d+)"),
     lambda m: f"[{m.group(1)}] {m.group(2)[:4]}(I) {int(m.group(3))}"),
]
_FN_YEAR = re.compile(r"(?<!\d)(1[5-9]\d\d|20[0-2]\d)(?!\d)")
_FN_YEAR_END = re.compile(r"(?:^[A-Z]{2}\d{5}|^UI)((?:19|20)\d\d)")


def from_filename(stem):
    for rx, f in _FN_NC:
        m = rx.match(stem)
        if m:
            return f(m)
    return None


# --------------------------------------------------------------------------- main entry
def extract(name, pages, text, label, tail_text=None):
    stem = name.rsplit(".", 1)[0]
    head_pages, tail = split_text(text)
    first = head_pages[0] if head_pages else ""
    if len(first) < 500 and len(head_pages) > 1:
        first += "\n" + head_pages[1]
    lines = [l.strip() for l in first.split("\n") if l.strip()]
    tail_src = tail_text or tail or (head_pages[-1] if head_pages else "")
    tail_lines = [l.strip() for l in tail_src.split("\n") if l.strip()]
    fhead = flat(" ".join(head_pages[:3]))[:8000]
    ftail = flat(tail_src)
    full = flat(text[:600000].replace("\x00TAIL", " "))
    has_text = len(fhead) >= 200
    is_case = label in ("CASE_JUDGMENT", "LAW_REPORT")

    rec = {"file": name, "document_type": label, "pages": pages, "has_text_layer": has_text}
    if pages < 0:
        return rec

    # ---- citations / identifiers
    nc = nc_src = None
    fn_nums = {int(x) for x in re.findall(r"\d+", stem)}
    for m in NC_RE.finditer(fhead[:1800]):
        cue = "itation" in fhead[max(0, m.start() - 45): m.start()]
        if m.start() < 450 or cue or (int(m.group(3)) in fn_nums and int(m.group(1)) in fn_nums):
            nc, nc_src = norm_nc(m), "text"
        break
    if not nc:
        nc = from_filename(stem)
        nc_src = "filename" if nc else None
    rep = report_fields(name, fhead) if label == "LAW_REPORT" or stem.startswith(("TC_", "Mor", "Brn")) else {}

    court = jur = None
    if nc:
        court, jur = court_from_code(nc)
    if not court and has_text:
        court, jur2 = court_from_text(fhead[:1500])
        jur = jur or jur2
    if rep.get("_court") and (label == "LAW_REPORT"):
        court, jur = rep["_court"], rep.get("_jur") or jur
    if not court and not has_text and re.fullmatch(r"(1[89]\d\d|20[01]\d)_\d{1,3}[a-z]?", stem):
        court, jur = "Judicial Committee of the Privy Council", UK
        y, n = stem.split("_")
        nc, nc_src = f"[{y}] UKPC {int(re.sub(r'[a-z]', '', n))}", "filename"
    if court == "Royal Court of Jersey" and not nc and re.fullmatch(r"(19|20)\d\d_\d{1,3}[a-z]?", stem):
        y, n = stem.split("_")
        nc, nc_src = f"[{y}] JRC {n.lstrip('0') or '0'}", "filename"
    if jur is None and court:
        if "IE" in (nc or "") or re.search(r"\bIE(?:HC|SC|CA)\b", stem):
            jur = IE
            court = court.replace("High Court", "High Court of Ireland") if court == "High Court" else court
    if court in ("High Court", "Supreme Court", "Court of Appeal", "Court of Criminal Appeal") and jur is None:
        if re.search(r"IEHC|IESC|IECA|IECCA|Record No|Dublin|An Bord|\bJ\.\s|plaintiff", fhead[:3000]):
            jur, court = IE, court + (" of Ireland" if "Criminal" not in court else " (Ireland)")

    lab = labelled(lines) if has_text else {}
    case_no = case_number(lab, fhead, lines) if has_text else None
    m = re.fullmatch(r"o(\d{3})(\d{2})", stem)
    if m and (court or "").startswith("UK Intellectual"):
        case_no = f"BL O/{m.group(1)}/{m.group(2)}"

    # ---- dates
    jdate = jsrc = hdate = None
    if has_text:
        if label == "LAW_REPORT" and rep.get("_abbr") in ("Mor", "Mor App", "Brn", "Elchies", "Hailes"):
            m = _MOR_DATE.search(fhead[:500])
            if m:
                jdate, jsrc = f"{m.group(1)}-{_MONTHS[m.group(2)[:3].lower()]:02d}-{int(m.group(3)):02d}", "report-heading"
        elif label == "LAW_REPORT" and rep.get("_abbr") != "TC":
            ds = find_dates(fhead[:1500])
            jdate, jsrc = (ds[0][1], "first-date") if ds else (None, None)
        else:
            jdate, jsrc = judgment_date(lines, fhead, ftail)
            hdate = hearing_date(fhead)
    year = int(jdate[:4]) if jdate else None
    if year is None and nc:
        year = int(nc[1:5])
    if year is None and rep.get("_abbr") == "SLR" and rep.get("volume"):
        year = 1863 + int(rep["volume"])          # vol. 1 = session 1865-66; approximate
        rec["year_is_approximate"] = True
    if year is None and rep and has_text:
        m = _OLD_YEAR.search(fhead[:400])
        if m:
            year = int(m.group(1))
    if year is None:
        m = _FN_YEAR_END.search(stem) or _FN_YEAR.search(stem)
        if m:
            year = int(m.group(1))

    # ---- parties / names
    parties = []
    if has_text and is_case:
        old_rep = rep.get("_abbr") in ("Mor", "Mor App", "Brn", "Elchies", "Hailes")
        parties = [] if old_rep else parties_from_header(lines)
        if not parties and not old_rep:
            # "Appellant: Mr X, counsel" under a Representation heading names lawyers, not parties
            cut = next((i for i, l in enumerate(lines) if _REP_START.match(l)), len(lines))
            parties = max([parties_from_labels(labelled(lines[:cut])), parties_from_roles(lines)], key=len)
        parties = [p for p in parties if p["name"]]
        if len(parties) < 2:
            pv = parties_from_v(lines, old_rep)
            if len(pv) > len(parties):
                parties = pv
    case_name = make_case_name(parties) if parties else None
    if has_text and is_case and court == "Scottish Information Commissioner":
        m = re.search(r"Decision\s+\d+/\d{4}:?\s+(.{3,160}?)\s+(?:Reference No|Applicant|Summary|[A-Z][a-z]+ [a-z]+ [a-z]+ [a-z]+:)", fhead[:600])
        m2 = next((x for x in (re.match(r"Decision\s+\d+/\d{4}\s*[:–-]?\s+(.+?)\s+and\s+(.+)$", ln) for ln in lines[:6]) if x), None)
        if m2:
            a, b = m2.group(1), m2.group(2)
            b = re.split(r"\s+(?=[A-Z][a-z]+(?: [a-z]+){2,})", b)[0] if len(b) > 60 else b
            parties = [{"role": "applicant", "name": _clean_party(a)}, {"role": "public authority", "name": _clean_party(b)}]
            case_name = f"{parties[0]['name']} and {parties[1]['name']}"
    if has_text and is_case and court == "UK Intellectual Property Office" and len(parties) < 2:
        names = re.findall(r"(?:IN THE NAME OF|\bBY|STANDING IN THE NAME OF)\s+([A-Z0-9][A-Z0-9 &.,'’()\-/]{2,80}?)(?=\s+(?:AND|TO|FOR|IN|UNDER|\d)\b|\s+[A-Z][a-z]|\s*$)", fhead[:1200])
        seen = []
        for n in names:
            n = _clean_party(n)
            if n not in seen:
                seen.append(n)
        if seen:
            parties = [{"role": "party", "name": n} for n in seen[:4]]
            case_name = " v ".join(seen[:2]) if len(seen) > 1 else seen[0]
    if has_text and is_case and court == "Nominet Dispute Resolution Service" and len(parties) >= 2:
        case_name = make_case_name(parties)

    # ---- catchwords / summary / outcome
    cw, cw_rest = catchwords_of(head_pages) if has_text and is_case else ([], None)
    if not cw and lab.get("subject matter"):
        cw = [flat(lab["subject matter"])]
    summ = summary_of(head_pages) if has_text and is_case else None
    if not summ and cw_rest and label == "LAW_REPORT" and len(cw_rest) > 80:
        summ = cw_rest[:600]

    if court == "Information Commissioner's Office" and case_name and case_no:
        title = f"ICO decision notice {case_no}: {case_name}"
    else:
        title = case_name
    rec.update({
        "title": title,
        "jurisdiction": jur,
        "date": jdate,
        "year": year,
        "primary_identifier": nc or case_no or stem,
    })
    if not is_case:
        if has_text:
            rec["title"] = next((smart_title(l) for l in lines if len(l) > 8 and sum(c.isalpha() for c in l) > 6), None)
            rec["legislation_cited"] = legislation_cited(full)
        return rec

    if has_text:
        stage = stage_of(fhead, court, parties) if label == "CASE_JUDGMENT" else ("Appeal" if court == "House of Lords" else None)
        outcome, outcome_text = outcome_of(fhead, ftail, " ".join(cw), court or "", stage == "Appeal")
        own = [c for c in (nc,) if c]
        cited = cases_cited(full, own)
        head_rc = [norm_rc(m) for m in RC_RE.finditer(fhead[:700])]
        rec.update({
            "case_name": case_name,
            "neutral_citation": nc,
            "neutral_citation_source": nc_src,
            "case_number": case_no,
            "parallel_citations": head_rc,
            "court": court,
            "judgment_date": jdate,
            "judgment_date_source": jsrc,
            "hearing_date": hdate,
            "judges": judges_of(lines, tail_lines, lab, fhead, court) if rep.get("_abbr") not in ("Mor", "Brn", "TC") else [],
            "parties": parties,
            "procedural_stage": stage,
            "outcome": outcome,
            "outcome_text": outcome_text,
            "legal_topics": topics_of(fhead, court),
            "catchwords": cw,
            "summary": summ,
            "cases_cited": cited,
            "legislation_cited": legislation_cited(full),
        })
        if lab.get("property") or lab.get("premises"):
            rec["property"] = flat(lab.get("property") or lab.get("premises"))
    else:
        rec.update({"neutral_citation": nc, "neutral_citation_source": nc_src, "court": court,
                    "note": "image-only PDF: metadata from filename only (needs OCR for the rest)"})

    if label == "LAW_REPORT":
        abbr = rep.get("_abbr")
        cite = None
        if abbr and rep.get("start_page"):
            vol = rep.get("volume")
            if abbr in ("Mor", "Mor App"):
                cite = f"({year}) Mor {rep['start_page']}" if year else f"Mor {rep['start_page']}"
            elif abbr == "TC":
                cite = f"{vol} TC {rep['start_page']}"
            else:
                cite = f"{'(' + str(year) + ') ' if year else ''}{vol + ' ' if vol else ''}{abbr} {rep['start_page']}"
        rec.update({k: v for k, v in rep.items() if not k.startswith("_")})
        rec["reporter_citation"] = cite
        rec["reported_status"] = "reported"
        rec["headnote"] = summ
        rec["editorial_catchwords"] = cw or ([rep["dictionary_title"]] if rep.get("dictionary_title") else [])
        if abbr in ("Mor", "Mor App") and has_text and not rec["editorial_catchwords"]:
            h = next((l for l in lines[:6] if l.isupper() and len(l) > 4 and not any(c.isdigit() for c in l)), None)
            if h:
                rec["editorial_catchwords"] = [h.strip(" .").title()]
        if cite and rec["primary_identifier"] == stem:
            rec["primary_identifier"] = cite
        if not rec.get("title"):
            rec["title"] = cite
    else:
        rec["reported_status"] = "reported" if rec.get("parallel_citations") else "unreported (court/tribunal transcript)"
    return rec
