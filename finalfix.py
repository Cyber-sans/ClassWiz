"""Last metadata pass: layout-specific header parsers that run only where the earlier layers left a gap.

  * parties: label rows ("Claimant: X"), trailing-role rows ("X Appellant"), Scottish "in the cause X Pursuer against Y Defender",
    Privy Council title sentence / title table, Jersey "X / -v- / Y"
  * judges: the Privy Council "Present" list, the signing hearing officer of IPO decisions
  * outcome wording: part-allowed appeals, dismissed applications, ICO notices that order steps
  * court-division text that leaked into a law-report party name
Nothing here overrides a usable value, and every name must pass the same damage checks as elsewhere: if in doubt the
citation stays as the title.
"""
import difflib
import os
import re

import meta

I = re.I
_WS = re.compile(r"\s+")
_FIX_CAPS = {"THE": "The", "AND": "and", "OF": "of", "FOR": "for", "LTD": "Ltd", "LIMITED": "Limited", "PLC": "PLC"}
_ODD = re.compile(r"[A-Za-z]\d[A-Za-z]|[A-Za-z]{2}\d|\d[a-z]{2}|[£$*^~|\\<>=%#?!_{}\[\]•]")
_DANGLING = re.compile(r"(?:\b(?:the|of|and|for|to|in|at|by|v|or|on|from)|&)$", I)
_NOT_A_NAME = re.compile(r"^(?:against|versus|for|and|in|of|by|or|the|absent|between|appellants?|respondents?|claimants?|applicants?|general|"
                         r"first|second|third|fourth|fifth|represented by|in person|none|n/a|not (?:present|represented))$|"
                         r"^(?:against|versus|represented by|case \d|anonymity|no anonymity)\b", I)
_NAME_REJECT = re.compile(r"\banonymity (?:direction|order)\b|\bon behalf of\b|\b(?:claimants?|defendants?|respondents?|appellants?|applicants?)\b|"
                          r"\bsolicitors?\b|\bcounsel\b|\binstructed by\b", I)
_SPLIT_WORD = re.compile(r"\b[A-Z] [a-z]{2,}")
_COMMON = set("limited company others another attorney general majesty's majesty the and from queen king state secretary crown advocate "
              "commissioners commissioner council corporation government".split())
_SMALL_OK = set("and of the de la le du von van der v in for or al bin alias since deceased by his her their representatives legal others another "
                "on as a an at to with née nee formerly otherwise".split())
_A_ROLES = r"Claimants?|Applicants?|Appellants?|Pursuers?|Petitioners?|Noters?|Reclaimers?|Minuters?|Complainants?"
_B_ROLES = r"Respondents?|Defenders?|Defendants?"
_ORD = r"(?:(?:1st|2nd|3rd|4th|first|second|third|fourth)\s+)?"


def _tidy(n):
    return " ".join(_FIX_CAPS.get(w, w) if i or w != "AND" else w for i, w in enumerate(n.split()))


def _name(raw, ocr=False):
    """A cleaned party name, or None if the text does not look like one."""
    raw = _WS.sub(" ", raw or "").strip(" ,;:.-–—/")
    raw = re.sub(r"\s*\(\s*[\"“'‘]?the\s[^)]{2,40}\)$", "", raw, flags=I)                    # ("the Landlord")
    raw = re.sub(r"\s*\((?:AP|Assisted Person)\)", "", raw, flags=I)
    raw = re.sub(r"\s+(?:In person|Represented by\b.*|Not (?:present|represented).*)$", "", raw, flags=I).strip(" ,;:.-–—/")
    raw = re.sub(r"^[RC]\d{1,2}[.:)]?\s+", "", raw)                                        # "R1. The Secretary of State"
    if not (2 <= len(raw) <= 160) or not raw[:1].isalnum() and raw[:1] not in "(\"“'‘":
        return None
    n = _tidy(meta._clean_party(raw) or "")
    if not n or len(n) < 2 or not any(c.isalpha() for c in n) or ":" in n or _ODD.search(n) or _DANGLING.search(n) or _NOT_A_NAME.match(n):
        return None
    if re.search(r"\d{4}/|/\d{4}|\b(?:is|was|has|had|that|which|were|are|shall|should|will)\b", n):
        return None
    if _SPLIT_WORD.search(n) or (len(n) == 2 and not n.isupper()) or _NAME_REJECT.search(n):
        return None
    if re.search(r"(?<![\w.])0(?![\w.])", raw) or (ocr and meta.garbled(raw)):       # a lone zero is a misread letter O
        return None
    return n


def _ocr_name(raw):
    """Stricter test for names read from a scan: no stray lowercase words, no shouting tokens inside a mixed-case name."""
    raw = _WS.sub(" ", raw or "").strip()
    if "," in raw:                                    # keep ", Limited" / ", and others"; anything else after a comma is debris
        head, _, rest = raw.partition(",")
        raw = raw if re.match(r"\s*(?:Limited|Ltd|and (?:others|another)|&c|Bart|Esq)\b", rest, I) else head
    n = _name(raw, True)
    if not n:
        return None
    toks = n.replace(",", " ").split()
    mixed = any(t[:1].isupper() and t[1:].islower() for t in toks)
    for k, t in enumerate(toks):
        w = t.strip(".()'’\"")
        if w.isalpha() and w.islower() and w not in _SMALL_OK and not (k == 0 and w == "the"):
            return None
        if mixed and w.isalpha() and w.isupper() and len(w) >= 3 and w not in meta._KEEP:
            return None
    return n[:1].upper() + n[1:]


def _recurs(names, blocks):
    """Names read from a scan are trusted only if their distinctive words turn up again in the document."""
    words = {}
    for b in blocks:
        for w in re.findall(r"[A-Za-z][A-Za-z'’\-]{3,}", b.get("text") or ""):
            w = w.lower()
            words[w] = words.get(w, 0) + 1
    tot = hit = 0
    for n in names:
        sig = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'’\-]{3,}", n) if w.lower() not in _COMMON]
        if not sig:
            continue
        h = sum(1 for w in sig if words.get(w, 0) >= 2)
        if h == 0:
            return False
        tot, hit = tot + len(sig), hit + h
    return hit == tot                                 # every distinctive word must be seen again: one misread letter is enough to fail


def names_bad(parties):
    if not parties:
        return True
    for p in parties:
        n = p.get("name") or ""
        if not n[:1].isalnum() or _ODD.search(n) or _DANGLING.search(n) or _NOT_A_NAME.match(n) or re.search(r"\d{4}/|/\d{4}|: ", n):
            return True
    return False


def _text(b):
    return _WS.sub(" ", b.get("text") or "").strip()


# ------------------------------------------------------------------ parties: label rows and trailing-role rows
_LABEL_SEP = re.compile(r"^%s(%s|%s|Landlords?|Tenants?)(?:\s?\(?\d\)?)?\s*(?:[:•·]\s*)+(.*)$" % (_ORD, _A_ROLES, _B_ROLES), I)
_LABEL_BARE = re.compile(r"^(Applicants?|Respondents?|Landlords?|Tenants?)\s+(?=[A-Z(0-9])(.+)$")
_TRAIL = re.compile(r"^(.{2,150}?)[\s,]+\(?%s(%s|%s)\)?[.,]?(?:\s+(?:In person|Represented by\b.*|Not (?:present|represented).*))?$" % (_ORD, _A_ROLES, _B_ROLES))
_ROLE_ONLY = re.compile(r"^(?:(?:First|Second|Third)(?: and (?:Second|Third))? )?(%s|%s)[.,:]?$" % (_A_ROLES, _B_ROLES), I)
_HDR_STOP = re.compile(r"^(?:judgment|reasons|decision\b|introduction|background|order\b|determination|the application|summary)\b", I)


def _side(role):
    return "A" if re.fullmatch(_A_ROLES + r"|Landlords?", role, I) else "B"


def _norm_role(role):
    r = role.lower()
    return r[:-1] if r.endswith("s") else r


def label_rows(hb, bare=False, cut_address=False, ocr=False):
    found = []                                   # (side, role, name)
    for i, b in enumerate(hb[:45]):
        t = _text(b)
        if len(t) > 190:
            continue
        if found and _HDR_STOP.match(t):
            break
        m = _LABEL_SEP.match(t)
        if not m and bare:
            m = _LABEL_BARE.match(t)
            if m and (len(m.group(2).split()) < 2 or re.match(r"(?:Limited|Ltd|LLP|PLC|Company|Co)\b", m.group(2), I)):
                return None
        role = val = None
        if m:
            role, val = m.group(1), m.group(2)
            if re.match(r"(?:'s|’s|s'|representative|in person$)", val, I):
                continue
            if _DANGLING.search(val.strip()) and i + 1 < len(hb) and len(_text(hb[i + 1])) < 80 and not _LABEL_SEP.match(_text(hb[i + 1])):
                val += " " + _text(hb[i + 1])
        elif _ROLE_ONLY.match(t):
            k = i - 1
            while k >= 0 and re.search(r"anonymity|^\(.*\)$", _text(hb[k]), I):
                k -= 1
            prev = _text(hb[k]) if k >= 0 else ""
            if prev and len(prev) < 150 and not _ROLE_ONLY.match(prev) and not _LABEL_SEP.match(prev) \
                    and not re.fullmatch(r"[-–—\s]*(?:and|v\.?)[-–—\s]*", prev, I):
                role, val = _ROLE_ONLY.match(t).group(1), prev
                if role.lower().endswith("s") and _side(role) == "A":
                    return None                  # "Appellants" under several name rows: one row is not the whole side
        else:
            m = _TRAIL.match(t)
            if m and (m.group(1)[:1].isupper() or m.group(1)[:1] in "\"“("):
                role, val = m.group(2), m.group(1)
        if not role:
            continue
        if cut_address:
            val = re.split(r",\s+(?=\d|PO\b|c/o\b|per\b|The [A-Z])|,\s+[A-Z][a-z]+ (?:Road|Street|Place|Park|Avenue|Wynd|Drive|Court)\b", val)[0]
        pieces = [p for p in re.split(r"\s*\(\d\)\s*|\s*;\s+(?:and\s+)?", val) if p.strip()] or [val]
        for p in pieces:
            n = _name(p, ocr)
            if n is None:
                return None                      # a labelled row that is not a clean name: do not guess
            if (_side(role), n) not in [(s, x) for s, _, x in found]:
                found.append((_side(role), _norm_role(role), n))
    if not {"A", "B"} <= {s for s, _, _ in found} or len(found) > 40:
        return None
    return found


# ------------------------------------------------------------------ parties: Scottish opinions
_SC_ROLE = re.compile(r"^(.*?)\s*\b(%s)(?:\s+and\s+(?:Reclaimers?|Respondents?|Appellants?))?\s+(against|for)$" % _A_ROLES, I)
_SC_RIGHT = re.compile(r"^(.*?)\s*\b(%s)(?:\s+and\s+(?:Reclaimers?|Respondents?))?[.,]?$" % _B_ROLES, I)


def _caps(s):
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.75


def _split_numbered(s):
    return [p for p in re.split(r"\s*\((?:FIRST|SECOND|THIRD|FOURTH|FIFTH|\d)\)\s*|;\s*(?:and\s+)?", s, flags=I) if p.strip(" ,;") and p.strip(" ,;").lower() != "and"]


def scottish(hb):
    for i, b in enumerate(hb[:22]):
        t = _text(b)
        m = _SC_ROLE.match(t)
        if not m or len(t) > 260:
            continue
        left = m.group(1).strip() or (_text(hb[i - 1]) if i else "")
        if not left or not _caps(left) or len(left) > 240:
            return None
        out = []
        for p in _split_numbered(re.sub(r"\s+and$", "", left, flags=I)):
            n = _name(p)
            if n is None:
                return None
            out.append(("A", _norm_role(m.group(2)), n))
        if m.group(3).lower() == "against" and i + 1 < len(hb):
            r1 = _text(hb[i + 1])
            mr = _SC_RIGHT.match(r1)
            right = role = None
            if mr and mr.group(1).strip() and _caps(mr.group(1)):
                right, role = mr.group(1), mr.group(2)
            elif _caps(r1) and len(r1) < 240 and i + 2 < len(hb) and _ROLE_ONLY.match(_text(hb[i + 2])):
                right, role = r1, _ROLE_ONLY.match(_text(hb[i + 2])).group(1)
            if right:
                for p in _split_numbered(right):
                    n = _name(re.sub(r"^and\s+", "", p, flags=I))
                    if n is None:
                        return None
                    out.append(("B", _norm_role(role), n))
        return out or None
    return None


# ------------------------------------------------------------------ parties: Privy Council scans, Jersey
_PC_SENT = re.compile(r"\bAp+eals?\s+o[fj]\s+(.{3,140}?),?\s+[vV]\.?\s+(.{3,140}?)[,;]?\s*(?:\(|from\b|frorn\b|fiom\b)", I)
_PC_ROLE = re.compile(r"[\s,.\-–—]*\b(?:Appellants?|Respondents?)\b[\s,.\-–—]*$", I)


def privy_council(hb, blocks):
    if not hb:
        return None
    m = _PC_SENT.search(_text(hb[0])[:420])
    if m:
        a, b = _ocr_name(m.group(1)), _ocr_name(re.sub(r"\s*\(.*$", "", m.group(2)))
        return [("A", "appellant", a), ("B", "respondent", b)] if a and b and _recurs([a, b], blocks) else None
    head = [_text(b) for b in hb[:9]]
    if not re.match(r"Privy Council Ap+e\w* N", head[0], I):
        return None
    end = next((k for k, t in enumerate(head) if re.match(r"FR[O0]M\b", t)), None)
    if end is None:
        return None
    rows = []
    for t in head[1:end]:
        t = _PC_ROLE.sub("", re.sub(r"[\s\-–—]*\b[vV]\.?$", "", t)).strip(" -–—,.")
        if t and not re.fullmatch(r"(?:and|v\.?|consolidated appeals?)", t, I):
            rows.append(t)
    if len(rows) != 2:
        return None
    a, b = _ocr_name(rows[0]), _ocr_name(rows[1])
    return [("A", "appellant", a), ("B", "respondent", b)] if a and b and _recurs([a, b], blocks) else None


def versus_rows(hb, blocks):
    for i, b in enumerate(hb[:14]):
        if i and i + 1 < len(hb) and re.fullmatch(r"-?\s?[vV]\.?\s?-?", _text(b)):
            a, c = _ocr_name(_text(hb[i - 1])), _ocr_name(_text(hb[i + 1]))
            if a and c and len(a.split()) <= 9 and len(c.split()) <= 9 and _recurs([a, c], blocks):
                return [("A", "party", a), ("B", "party", c)]
            return None
    return None


# ------------------------------------------------------------------ judges
_PRESENT = re.compile(r"^Pres[ce]n[tl](?:\s+at\s+the\s+hearin\w{0,2})?\s*[:;.]?\s*[—–-]?$", I)
_BENCH = re.compile(r"^(?:THE|MR\.?|SIR|LORD|VISCOUNT|EARL|LADY|DAME|BARON|MASTER|CHIEF)\b", I)


def present_list(blocks, ocr):
    bs = [b for b in blocks[:16] if b["t"] != "table"]
    for i, b in enumerate(bs):
        if not _PRESENT.match(_text(b)):
            continue
        out, dropped = [], 0
        for nb in bs[i + 1: i + 12]:
            t = _text(nb).strip(" .,;")
            if not _BENCH.match(t) or len(t) > 60:
                break
            j = None if re.search(r"\d", t) else _roster_name(meta.smart_title(t).strip(" .,'’"), ocr)
            if j and j not in out:
                out.append(j)
            elif not j:
                dropped += 1
        return out, dropped
    return [], 0


_ROSTER = None


def _roster_name(j, ocr):
    """Text-layer names are kept as printed. Scanned names must match the roster of known Board members, exactly or within a letter or two."""
    global _ROSTER
    if not ocr:
        return j
    if _ROSTER is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pc_bench.txt")
        _ROSTER = [l.strip() for l in open(path, encoding="utf-8") if not l.startswith("#")] if os.path.exists(path) else []
        _ROSTER = {x.lower(): x for x in _ROSTER if x}
    if j.lower() in _ROSTER:
        return _ROSTER[j.lower()]
    close = difflib.get_close_matches(j.lower(), list(_ROSTER), n=1, cutoff=0.86)
    return _ROSTER[close[0]] if close else None


_SIGN_INLINE = re.compile(r"^((?:(?:Mr|Mrs|Ms|Miss|Dr)\.? )?(?:[A-Z][\w.'’\-]*\.? ?){2,4}?)\s+(?:For the Registrar|Principal Hearing Officer|Hearing Officer|The Appointed Person)\b")
_SIGN_NAME = re.compile(r"^(?:(?:Mr|Mrs|Ms|Miss|Dr)\.? )?(?:[A-Z][\w.'’\-]*\.? ?){2,4}$")


_SIGN_TITLE = re.compile(r"\b(?:Officer|Registrar|Person|Comptroller|Tribunal|Office|Division|Section|Trade Marks?|Patents?)\b", I)


def _person(s):
    s = s.strip(" .,")
    return meta.smart_title(s) if s.isupper() else s


def signing_officer(blocks):
    bs = [b for b in blocks[-14:] if b["t"] != "table"]
    for i, b in enumerate(bs):
        t = _text(b)
        m = _SIGN_INLINE.match(t)
        if m and not _SIGN_TITLE.search(m.group(1)):
            return _person(m.group(1))
        if re.match(r"For the Registrar\b|The Appointed Person$|(?:Principal )?Hearing Officer$", t, I) and i:
            k = i - 1
            while k > 0 and _SIGN_TITLE.search(_text(bs[k])) and len(_text(bs[k])) < 40:
                k -= 1
            prev = _text(bs[k])
            if _SIGN_NAME.match(prev) and not _SIGN_TITLE.search(prev) and not re.search(
                    r"\b(?:Dated|January|February|March|April|May|June|July|August|September|October|November|December|Act|Rules?)\b", prev):
                return _person(prev)
    return None


# ------------------------------------------------------------------ entry point
_DIV_PREFIX = re.compile(r"^(?:(?:First|Second|Extra|Whole|Inner|Outer) (?:Division|House)|Court of Session|House of Lords|Bill Chamber|Teind Court|"
                         r"Court of Exchequer|Lord [A-Z][\w'’]+,? Ordinary)[.,:]?\s+(?=\S)", I)
_SCOT_COURTS = ("ScotCS", "ScotHC", "ScotSC", "ScotSAC", "ScotUT", "ScotLC")
_PROPERTY = ("EWLVT", "UKFTT/PC", "EWLands", "UKUT/LC")


_SSHD = re.compile(r"^(the )?secretary of state for the home department$", I)


def _tidy_existing(n):
    """Header words that ran into a party name in the earlier layers."""
    n = re.sub(r"\s+(?:Representation|Appearances?)\b.*$", "", n)
    before = n
    n = re.sub(r"\s*\(?\b(?:no )?anonymity (?:direction|order)s?(?: (?:not )?(?:made|in force|continued|maintained))?\)?", "", n, flags=I)
    if n != before:                                   # initials that were title-cased together with the phrase: "Ay" -> "AY"
        n = re.sub(r"^([A-Za-z]{1,4})(?=$| \()", lambda m: m.group(1).upper(), n.strip())
    n = re.sub(r"\s+in the (?:Upper|First-tier) Tribunal\b.*$|\s+[—–-]\s+(?:no|not)\b.*$", "", n, flags=I)
    n = re.sub(r"^(MR|MRS|MS|MISS|DR)\b", lambda m: m.group(1).capitalize(), n).strip(" ,;")
    m = _SSHD.match(n)
    if m:
        n = ("The " if m.group(1) else "") + "Secretary of State for the Home Department"
    return n


def _set(rec, found, source):
    sides = {"A": [], "B": []}
    parties = []
    for s, role, n in found:
        sides[s].append(n)
        parties.append({"role": role, "name": n})
    sfx = lambda xs: xs[0] + (" & Ors" if len(xs) > 2 else (" & Anor" if len(xs) == 2 else ""))
    rec["parties"], rec["parties_source"] = parties, source
    rec["case_name"] = rec["title"] = f"{sfx(sides['A'])} v {sfx(sides['B'])}" if sides["A"] and sides["B"] else sfx(sides["A"] or sides["B"])
    rec.pop("title_note", None)


def apply(rec, blocks, dtype, ocr_first):
    code = (rec.get("provenance") or {}).get("bailii_database") or ""
    hb = [b for b in blocks if b["t"] != "table" and b.get("z") in ("header", None, "body")][:60]
    ps = rec.get("parties") or []
    if dtype == "CASE_JUDGMENT" and ps:
        out, changed = [], False
        for p in ps:
            n = _tidy_existing(p["name"])
            m = re.fullmatch(r"(.{3,90}?)\s*\([1il]\)[,;]?\s+(?:and\s+)?(.{3,90}?)\s*\(2\)", n)
            parts = [m.group(1), m.group(2)] if m else [n]
            changed |= parts != [p["name"]]
            out.extend(dict(p, name=x.strip(" ,;")) for x in parts if x.strip(" ,;"))
        if changed and len({p["role"] for p in out}) == 2:
            roles = list(dict.fromkeys(p["role"] for p in out))
            sides = [[p["name"] for p in out if p["role"] == r] for r in roles]
            sfx = lambda xs: xs[0] + (" & Ors" if len(xs) > 2 else (" & Anor" if len(xs) == 2 else ""))
            rec["parties"] = out
            rec["case_name"] = rec["title"] = f"{sfx(sides[0])} v {sfx(sides[1])}"
    if dtype == "CASE_JUDGMENT" and names_bad(rec.get("parties")):
        found = src = None
        if code == "UKPC":
            found, src = privy_council(hb, blocks), "privy council title"
        elif code.startswith(_SCOT_COURTS):
            found, src = scottish(hb), "scottish instance"
            if not found:
                found, src = label_rows(hb, cut_address=code.startswith("ScotUT"), ocr=ocr_first), "header role rows"
        elif code in ("UR", "JRC", "JCA"):
            found, src = versus_rows(hb, blocks), "header 'v' rows"
        if not found and code != "UKPC":
            found, src = label_rows(hb, bare=code in _PROPERTY, ocr=ocr_first), "header role rows"
        if found:
            _set(rec, found, src)
    # court division printed before the case name in a law report
    ps = rec.get("parties") or []
    if dtype == "LAW_REPORT" and ps and _DIV_PREFIX.match(ps[0]["name"]):
        n = _DIV_PREFIX.sub("", ps[0]["name"]).strip()
        if len(n) > 1 and len(ps) == 2:
            ps[0]["name"] = n
            rec["case_name"] = rec["title"] = f"{n} v {ps[1]['name']}"
    # judges
    if not rec.get("judges"):
        if code == "UKPC":
            js, dropped = present_list(blocks, ocr_first)
            if js:
                rec["judges"], rec["judges_source"] = js[:9], "bench list ('Present')"
                if dropped:
                    rec["judges_note"] = f"{dropped} more name(s) in the bench list could not be read reliably"
        elif code == "UKIntelP":
            j = signing_officer(blocks)
            if j:
                rec["judges"], rec["judges_source"] = [j], "signature"
    # outcome wording
    lab, txt = rec.get("outcome"), rec.get("outcome_text") or ""
    if lab in ("Appeal dismissed", "Appeal allowed") and re.search(
            r"allowed (?:so far as|in part|only|to the extent of varying)|allowed[^.]{0,120}\botherwise[^.]{0,60}dismissed|dismissed[^.]{0,80}\bsave (?:that|as|for)\b", txt, I):
        rec["outcome"] = "Appeal allowed in part"
    elif lab == "Appeal dismissed" and re.search(r"application (?:to|for)[^.;]{0,70}?\bappeal\b[^.;]{0,25}?(?:is |be |was |are )?(?:dismissed|refused)", txt, I) \
            and not re.search(r"\bappeal (?:is|be|was|should be|must be|stands)\s+(?:hereby |therefore )?dismissed", txt, I):
        rec["outcome"] = "Application dismissed"
