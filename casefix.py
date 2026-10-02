"""Audit-driven metadata/segmentation layer, applied after meta + provenance + fixups.

Works only on the already-built content blocks (cached layout) and the document type:
  * law reports: isolate the one case the file is about (drop the neighbouring cases on the page)
  * judgments: full multi-party header parse (every party once, with all its roles)
  * judges / tribunal members stated plainly in the header
  * outcomes only from explicit decision / order / conclusion text, otherwise null
  * replacement-character clean-up, garbage catchword suppression
The rule throughout: prefer null or the citation over confidently wrong metadata.
"""
import difflib
import re
from collections import Counter

import finalfix
import meta

RC = "�"
_PUA = re.compile(r"[-]")
_WS = re.compile(r"\s+")
I = re.I


def clean_str(s):
    if not isinstance(s, str):
        return s
    return _WS.sub(" ", _PUA.sub(" ", s.replace(RC, " "))).strip().lstrip(":;,-–— ").strip()


# ============================================================================ parties (judgments)
_ROLE_W = (r"(?:Appellants?|Claimants?|Respondents?|Defendants?|Plaintiffs?|Applicants?|Petitioners?|Pursuers?|Defenders?|"
           r"Notice Part(?:y|ies)|Interested Part(?:y|ies)|Third Part(?:y|ies))")
_ROLE_RX = re.compile(r"\(?\b(?:(?:First|Second|Third|Fourth|Fifth|1st|2nd|3rd)\s+)?(%s(?:\s*/\s*%s)*)\b\)?\s*/?" % (_ROLE_W, _ROLE_W), I)
_ANCHOR = re.compile(r"(?:^|\s)B\s?E\s?T\s?W\s?E\s?E\s?N\s*[:/]?\s*$", I)
_SEP_ONLY = re.compile(r"^[-–—\s]*(?:and|v\.?|vs\.?|-v-)[-–—\s]*$", I)
_SEP_END = re.compile(r"\s*[-–—]*\s*\b(?:and|v\.?|vs\.?)\s*[-–—]*\s*$", I)
_P_STOP = re.compile(r"^(?:representation|appearances|before\b|hearing|judgment\b|approved|decision\b|ruling\b|determination|reasons\b|"
                     r"introduction|tribunal\b|sitting\b|heard\b|date\b|©)|\binstructed by\b|\bfor the (?:appellant|claimant|respondent|defendant|plaintiff|applicant)", I)
_P_SKIP = re.compile(r"^\(.*(?:anonym|sitting|minor).*\)$|^in the matter of\b|^and in the matter of\b|^\[.*\]$", I)


_FIX_CAPS = {"THE": "The", "AND": "and", "OF": "of", "FOR": "for", "LTD": "Ltd", "LIMITED": "Limited", "PLC": "PLC"}


def _tidy(n):
    return " ".join(_FIX_CAPS.get(w, w) if i or w != "AND" else w for i, w in enumerate(n.split()))


def _norm_role(w):
    w = w.lower().strip()
    w = {"parties": "party"}.get(w, w)
    w = re.sub(r"ies$", "y", w)
    return w[:-1] if w.endswith("s") else w


def header_parties(blocks):
    """Parties from the 'Between ...' block of a judgment header. None if the header has no such block."""
    hb = [b for b in blocks if b.get("z") == "header" and b["t"] != "table"]
    start = next((i for i, b in enumerate(hb) if _ANCHOR.search(clean_str(b["text"]))), None)
    if start is None:
        return None
    sides, cur = [], {"names": [], "roles": [], "plural": False, "numbered": False}

    def close():
        nonlocal cur
        if cur["names"] or cur["roles"]:
            sides.append(cur)
        cur = {"names": [], "roles": [], "plural": False, "numbered": False}

    for b in hb[start + 1: start + 70]:
        t = clean_str(b["text"])
        t = re.sub(r"\(\s*(?:no )?anonymity[^)]*\)|…|\.{3,}", " ", t, flags=I).strip()
        if not t or _P_SKIP.match(t):
            continue
        if len(t) > 220 or _P_STOP.search(t):
            break
        roles = [m.group(1) for m in _ROLE_RX.finditer(t)]
        rem = _ROLE_RX.sub(" ", t)
        stop_after = bool(re.search(r"\bbetween\b", rem, I)) or bool(re.search(r"\b(?:judgment|ruling|decision) of\b", rem, I))
        rem = re.sub(r"\b(?:and\s+)?between\b.*$|\b(?:counter ?claim)\b.*$|\b(?:judgment|ruling|decision) of\b.*$", "", rem, flags=I)
        sep = False
        if _SEP_ONLY.match(rem.strip() or "x") or (not rem.strip(" -–—") and re.search(r"[-–—]\s*and\s*[-–—]", t, I)):
            sep, rem = True, ""
        else:
            m = _SEP_END.search(rem)
            if m and m.group(0).strip():
                if cur["roles"] or roles:
                    sep = True                       # "... Appellants / - and -": the other side follows
                rem = rem[: m.start()]
        rem = _WS.sub(" ", rem).strip(" ,;.-–—/:")
        if rem and not roles and not sep and sides and cur["roles"] and "num" not in b and not rem.startswith("(") and not cur["numbered"]:
            break                                        # the last party's role has been given: what follows is not a party
        if re.search(r"\(\d{1,2}\)", rem) and "num" not in b:                             # "NAME (1) OTHER NAME (2)" numbering
            pieces = [p.strip(" ,;") for p in re.split(r"\s*\(\d{1,2}\)\s*", rem) if p.strip(" ,;")]
            if pieces:
                cur["numbered"] = True
                cur["names"].extend(pieces)
                rem = ""
        if rem:
            if rem.startswith("(") and cur["names"] and "num" not in b:
                cur["names"][-1] += " " + rem
            elif "num" in b:
                cur["numbered"] = True
                cur["names"].append(rem)
            elif cur["numbered"] and cur["names"]:
                cur["names"][-1] += " " + rem           # wrapped line of a numbered party
            else:
                cur["names"].append(rem)
        cur["role_hits"] = cur.get("role_hits", 0) + (1 if roles else 0)
        for r in roles:
            for part in re.split(r"\s*/\s*", r):
                cur["plural"] |= part.lower().endswith(("s", "ies"))
                nr = _norm_role(part)
                if nr not in cur["roles"]:
                    cur["roles"].append(nr)
        if sep:
            close()
        if stop_after:
            break
    close()
    sides = [s for s in sides if s["names"]]
    if len(sides) < 2 or sum(1 for s in sides if s["roles"]) < 2:
        return None
    out = []
    for s in sides:
        names = s["names"]
        if re.search(r"commissioners for\b.*\b(?:revenue|customs)", " ".join(names), I):
            names = [" ".join(names)]
        elif not s["numbered"]:
            if not s["plural"] and len(names) > 1 and s.get("role_hits", 0) < 2:
                names = [" ".join(names)]                # one party whose name wrapped over lines
            elif s["plural"] and len(names) == 1 and "," in names[0]:
                parts = [p.strip() for p in re.split(r",\s*|\s+AND\s+|\s+and\s+(?=[A-Z])", names[0]) if p.strip()]
                if len(parts) > 1 and all(p[:1].isupper() and len(p) > 2 for p in parts):
                    names = parts
        role = s["roles"][0] if s["roles"] else "party"
        for n in names:
            n = _tidy(meta._clean_party(n))
            if not n or len(n) > 160 or not n[:1].isalnum() or re.search(r":|\d{4}/|/\d{4}", n):
                return None                              # something in the block is not a party name: do not guess
            p = {"role": role, "name": n}
            if len(s["roles"]) > 1:
                p["roles"] = s["roles"]
            if p not in out:
                out.append(p)
    if len(out) > 80:
        return None
    return out, [[p["name"] for p in out if p["role"] == (s["roles"][0] if s["roles"] else "party")] for s in sides]


def _case_name(side_names):
    sfx = lambda xs: xs[0] + (" & Ors" if len(xs) > 2 else (" & Anor" if len(xs) == 2 else ""))
    a, b = side_names[0], side_names[1]
    return f"{sfx(a)} v {sfx(b)}" if a and b else None


# ============================================================================ judges
_J_SENT = re.compile(r"\b(?:JUDGMENT|RULING|DECISION|OPINION)\s+(?:of|OF|by|BY)\s+((?:the\s+)?(?:Hon\.?\s+)?(?:Mr|Ms|Mrs|Miss)\.?\s+Justice\s+"
                     r"[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){0,3}?)(?=\s+(?:delivered|dated|given|on|this)\b|[,.]?\s*$)", I)
_J_LABEL = re.compile(r"^(Tribunal Members?|Tribunal Judge|Tribunal|Judge|Chairman|Chair|Hearing Officer|Coram|Before)\b\s*[:•·\-–(s)]*\s+(.{3,90})$", I)
_J_WORD = re.compile(r"\b(?:judge|justice|lord|lady|sir|dame|hhj|recorder|sheriff|chairman|president|member|frics|mrics|qc|kc|llb|j\.?)\b", I)


def header_judges(blocks):
    hb = [b for b in blocks if b["t"] != "table" and b.get("z") in ("header", "body")][:60]
    out = []
    for b in hb:
        t = clean_str(b["text"])
        m = _J_SENT.search(t)
        if m:
            out.append(m.group(1))
            continue
        if b.get("z") != "header" or len(t) > 110:
            continue
        m = _J_LABEL.match(t)
        if m:
            label, val = m.group(1).lower(), m.group(2).strip(" :;,")
            if any(c.isdigit() for c in val) or not val[:1].isupper():
                continue
            if label.startswith(("tribunal", "before", "coram")) and not _J_WORD.search(val) and len(val.split()) > 5:
                continue
            if re.search(r"\b(?:claimant|respondent|appellant|applicant|property|representative|service|date|decision|reference|"
                         r"hearing|venue|type|application|tribunal)\b", val, I):
                continue
            if not _J_WORD.search(val) and not re.fullmatch(r"(?:(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.? )?(?:[A-Z][\w'’.\-]*\.? ?){2,5}", val):
                continue
            out.append(val)
    seen, res = set(), []
    for j in out:
        j = meta.smart_title(j)
        if j.lower() not in seen:
            seen.add(j.lower())
            res.append(j)
    return res[:6]


# ============================================================================ outcomes
_BE = r"(?:is|are|be|was|were|must be|should be|will be|shall be|stands?)\s+(?:hereby |therefore |accordingly |each |both )*"
_WE = r"(?:I|we|the court|the tribunal|their lordships)\s+(?:would|will|shall|should|must|do|therefore|accordingly|hereby|humbly|\s)*"
_O = [(re.compile(p, I), l) for p, l in [
    (r"dismissed (?:up)?on (?:its |the |their )?withdrawal|dismissed following (?:a |the |its )?withdrawal|having been withdrawn[^.]{0,80}?dismissed", "Dismissed on withdrawal"),
    (r"\b(?:is|are|be) (?:hereby |therefore )?struck out", "Struck out"),
    (r"appeals? " + _BE + r"(?:allowed|upheld|sustained)|" + _WE + r"allow (?:the|this|both|these|each) appeals?|appeals? (?:succeeds?|allowed)\b|"
     r"\bSustain the appeal|advise (?:His|Her) Majesty that (?:the|this) appeal (?:should|ought to) be allowed", "Appeal allowed"),
    (r"appeals? " + _BE + r"(?:dismissed|refused)|" + _WE + r"(?:dismiss|refuse) (?:the|this|both|these|each) appeals?|appeals? (?:fails?|dismissed|refused)\b|"
     r"\b(?:Refuse|Dismiss) the appeal|advise (?:His|Her) Majesty that (?:the|this) appeal (?:should|ought to) be dismissed|"
     r"(?:does|did) not (?:involve|contain|disclose) (?:the making of )?(?:an?|any) (?:material )?errors? (?:of|on a point of) law", "Appeal dismissed"),
    (r"set(?:s|ting)? aside (?:the|that) decision|decision[^.]{0,80}?" + _BE + r"set aside|"
     r"(?:judgments?|decrees?|orders?)[^.]{0,90}?(?:should|ought to|must) be (?:set aside|reversed|discharged)", "Decision set aside"),
    (r"(?:appeal|case|matter)s? " + _BE + r"remitted|\bremit (?:the|this) (?:appeal|case|matter)", "Remitted"),
    (r"advise (?:His|Her) Majesty (?:that|to)[^.]{0,90}?(?:dismiss(?:ed)?\b|affirm)", "Appeal dismissed"),
    (r"advise (?:His|Her) Majesty (?:that|to)[^.]{0,90}?(?:allow(?:ed)?\b|revers|discharg)", "Appeal allowed"),
    (r"opposition[s]? (?:therefore |accordingly )?(?:succeeds?|is successful|ha(?:s|ve|ving) (?:been successful|succeeded)|succeeded)", "Opposition succeeds"),
    (r"opposition[s]? (?:therefore |accordingly )?(?:fails?|is unsuccessful|ha(?:s|ve|ving) failed|failed)", "Opposition fails"),
    (r"domain names?[^.]{0,120}?(?:be|should be|to be|will(?: therefore)? be) transferred", "Domain name transferred"),
    (r"application " + _BE + r"(?:granted|allowed)|" + _WE + r"grant (?:the |this )?(?:application|dispensation|permission)|dispensation " + _BE + r"granted|"
     r"(?:tribunal|I) (?:has |have )?(?:decided to |determined? to |hereby )?(?:grants?|dispenses?)\b", "Application granted"),
    (r"application " + _BE + r"(?:refused|dismissed|rejected)|" + _WE + r"refuse (?:the |this )?application|"
     r"(?:tribunal|I) (?:accordingly |therefore |hereby )?refuses? (?:the |this )?application", "Application refused"),
    (r"interlocutors?[^.]{0,80}?\bbe (?:,?\s*and the same (?:is|are) hereby,?\s*)?affirmed|judgment[^.]{0,60}?\baffirmed", "Affirmed"),
    (r"interlocutors?[^.]{0,80}?\bbe (?:,?\s*and the same (?:is|are) hereby,?\s*)?reversed|judgment[^.]{0,60}?\breversed", "Reversed"),
    (r"\bThe Court adhered|\badhered to the (?:Lord Ordinary|interlocutor)|refused the reclaiming[- ]note", "Adhered (decision below upheld)"),
]]
_O_ET_WIN = re.compile(r"(?:claim|complaint)s?[^.]{0,110}?(?:succeeds?|(?:is|are|was) well[- ]founded|(?:is|are) upheld)|was unfairly dismissed|"
                       r"made (?:an )?unauthori[sz]ed deductions?|(?:must|shall|is ordered to|will|to) pay (?:to )?the claimant|"
                       r"ordered to pay|judgment for the claimant", I)
_O_ET_LOSE = re.compile(r"(?:claim|complaint|proceeding)s?[^.]{0,110}?(?:fails?|(?:is|are|be) (?:hereby )?dismissed|not well[- ]founded|(?:is|are) not upheld)", I)
_O_ICO_UP = re.compile(r"commissioner[’']s decision is that[^.]{0,260}?(?:breached|failed to|did not comply|has not complied|not entitled|incorrectly|wrongly|"
                       r"not correctly|is not engaged|are not engaged|does not apply|was not correct|contravened)", I)
_O_ICO_DOWN = re.compile(r"commissioner[’']s decision is that[^.]{0,260}?(?:has correctly|correctly applied|was correct|were correct|was entitled|is entitled|were entitled|"
                         r"has complied|complied with|does not hold|did not hold|is engaged|are engaged|was not obliged|not obliged|appropriately|properly)", I)
_O_STEPS = re.compile(r"requires? [^.]{0,90}?to take the following steps|must (?:now )?(?:take the following|disclose|issue a fresh response|provide the complainant)", I)
_O_NOSTEPS = re.compile(r"(?:does|do) not require [^.]{0,90}?(?:steps|action)|requires? no (?:further )?(?:steps|action)|no (?:further )?steps (?:are |is )?required", I)
_SEC = re.compile(r"^(?:\d{1,2}\.?\s*)?(judgment(?: of the (?:employment )?tribunal)?|decision \(including any steps ordered\)|decisions?|decisions? of the tribunal|"
                  r"the tribunal[’']?s? decision|orders?|conclusions?|conclusion and disposal|disposal|disposition|notice of decision|outcome|"
                  r"summary of (?:the )?decision|result|determination|the decision)\s*:?$", I)
_BOILER = re.compile(r"permission to appeal|anonymity|published, in full|contempt of court|rights? of appeal|within 28|fee award|"
                     r"application for permission|written reasons will not be provided|public access to", I)


def _sections(blocks):
    """[(heading_text_lower, text)] for explicit decision / order / conclusion sections."""
    out = []
    for i, b in enumerate(blocks):
        if b["t"] not in ("heading", "line", "paragraph") or len(b.get("text", "")) > 60 or not _SEC.match(clean_str(b["text"])):
            continue
        parts, size = [], 0
        for nb in blocks[i + 1: i + 10]:
            if nb["t"] == "heading" or (nb["t"] != "table" and _SEC.match(clean_str(nb.get("text", "")))):
                break
            if nb["t"] == "table":
                continue
            parts.append(nb["text"])
            size += len(nb["text"])
            if size > 1600:
                break
        if parts:
            out.append((clean_str(b["text"]).lower(), " ".join(parts)[:1800], i))
    return out


def _sentence(text, s, e):
    i = text.rfind(". ", 0, s)
    a = max(i + 2 if i >= 0 else 0, s - 180)
    b = text.find(". ", e)
    return text[a: min(b + 1 if b >= 0 else len(text), e + 180)].strip()


def _generic(text, last=True):
    hits = []
    for rx, label in _O:
        ms = [m for m in rx.finditer(text) if not re.search(r"\b(?:not|never|whether|if|unless)\b[\w ]{0,12}$", text[max(0, m.start() - 22): m.start()], I)]
        if ms:
            m = ms[-1] if last else ms[0]
            hits.append((m.start(), m.end(), label))
    if not hits:
        return None, None
    s, e, label = max(hits) if last else min(hits)
    labels = {h[2] for h in hits}
    if re.search(r"\bremit", text, I) and label in ("Appeal allowed", "Decision set aside"):
        label += " - remitted"
    elif label == "Decision set aside" and "Appeal allowed" in labels:
        label = "Appeal allowed"
    return label, _sentence(text, s, e)


def explicit_outcome(rec, blocks):
    court = rec.get("court") or ""
    secs = _sections(blocks)
    is_et = "Employment Tribunal" in court or "Industrial" in court or "Fair Employment" in court
    if is_et:
        sec = next((t for h, t, _ in secs if h.startswith("judgment")), None)
        if sec is None:            # "RESERVED JUDGMENT", "RULE 21 JUDGMENT", "DEFAULT JUDGEMENT", "JUDGMENT ON REMEDY" ...
            for i, b in enumerate(blocks[:40]):
                bt = clean_str(b.get("text", ""))
                if b["t"] != "table" and len(bt) < 60 and bt.isupper() and re.search(r"JUDGE?MENT", bt):
                    sec = " ".join(x["text"] for x in blocks[i + 1: i + 9] if x["t"] != "table" and x["t"] != "heading")[:1600]
                    break
        if not sec:            # short judgments: everything between the header and the signature
            sec = " ".join(b["text"] for b in blocks if b.get("z") == "body" and b["t"] in ("paragraph", "list_item"))[:1500] \
                if len(blocks) <= 16 else None
        if sec:
            m = re.search(r"dismissed (?:up)?on (?:its |the )?withdrawal|dismissed following (?:a |the |its )?withdrawal|having been withdrawn", sec, I)
            win, lose = _O_ET_WIN.search(sec), _O_ET_LOSE.search(sec)
            if win and lose and not m:
                return "Claim succeeds in part", _sentence(sec, win.start(), win.end()), "judgment section"
            if win:
                return "Claim succeeds", _sentence(sec, win.start(), win.end()), "judgment section"
            if m:
                return "Dismissed on withdrawal", _sentence(sec, m.start(), m.end()), "judgment section"
            so = re.search(r"\b(?:is|are|be) (?:hereby |therefore )?struck out", sec, I)
            if so:
                return "Struck out", _sentence(sec, so.start(), so.end()), "judgment section"
            if lose:
                return "Claim dismissed", _sentence(sec, lose.start(), lose.end()), "judgment section"
    if "Information Commissioner" in court:
        sec = next((t for h, t, _ in secs if h.startswith(("decision (including", "summary"))), None) or next((t for h, t, _ in secs if h.startswith("decision")), None)
        if sec:
            up, down = _O_ICO_UP.search(sec), _O_ICO_DOWN.search(sec)
            steps, nosteps = _O_STEPS.search(sec), _O_NOSTEPS.search(sec)
            base = "Complaint partly upheld" if up and down else ("Complaint upheld" if up else ("Complaint not upheld" if down else None))
            tail = "steps required" if steps else ("no steps required" if nosteps else None)
            if base or tail:
                m = up or down or steps or nosteps
                label = " - ".join(x for x in (base, tail) if x)
                return label[0].upper() + label[1:], _sentence(sec, m.start(), m.end()), "decision section"
    rubric = " — ".join(rec.get("catchwords") or [])
    if rubric:
        label, text = _generic(rubric, last=True)
        if label and label.startswith(("Appeal", "Application")):
            if re.search(r"(?:allowed|dismissed|refused) in part", rubric, I):
                label += " in part"
            return label, text, "rubric"
    # explicit sections, last one first (Disposal / Conclusion / Order near the end of the document)
    heads = Counter(h for h, _, _ in secs)
    for h, t, pos in sorted(secs, key=lambda x: -x[2]):
        if (h.startswith("judgment") and not is_et) or heads[h] > 2:
            continue                                     # a heading repeated per issue is not the disposal of the case
        label, text = _generic(t, last=True)
        if label and label.startswith("Application") and pos < 0.85 * len(blocks):
            continue                                     # a ruling on one of several applications, not the disposal
        if label:
            return label, text, f"'{h}' section"
    # the closing words of the decision
    body = [b for b in blocks if b["t"] in ("paragraph", "list_item", "line") and b.get("z") != "header" and len(b.get("text", "")) > 15]
    while body and (_BOILER.search(body[-1]["text"]) or (body[-1].get("z") == "conclusions" and len(body[-1]["text"]) < 60
                                                         and not re.search(r"appeal|dismiss|allow|refus|affirm|revers|adjudg|adher", body[-1]["text"], I))):
        body.pop()
    closing = " ".join(b["text"] for b in body[-3:])[-900:]
    label, text = _generic(closing, last=True)
    if label and not label.startswith("Application"):
        return label, text, "closing paragraphs"
    return None, None, None


# ============================================================================ catchwords
def valid_catchwords(items):
    good = []
    for c in items or []:
        c = clean_str(c)
        if c.lower() in ("yes", "no", "held"):
            items = [x for x in items if x is not c]
            continue
        if not (2 < len(c) <= 140) or re.search(r"[“”\"]|[:,;]$|^\W|^\d+\.\d", c):
            continue
        if sum(ch.isalpha() for ch in c) < 0.6 * len(c) or re.search(r"\b(?:counsel|agents?|wrote|gentlemen)\b", c, I):
            continue
        good.append(c)
    n = sum(1 for x in items or [] if clean_str(x).lower() not in ("yes", "no", "held"))
    return good if good and len(good) >= 0.75 * n else []


def tag_words(rec):
    """Catchwords short enough to serve as subject tags."""
    return [c for c in (rec.get("catchwords") or rec.get("editorial_catchwords") or []) if len(c.split()) <= 5 and len(c) <= 45]


# ============================================================================ letter-spaced small caps (nominate reports)
_CONN = {"and", "of", "the", "for", "in", "to", "or", "at", "by", "on", "an", "as", "is", "it", "be", "no", "if", "he", "we", "v", "from", "with"}


def is_spaced(s):
    toks = s.split()[:14]
    return len(toks) >= 6 and sum(1 for t in toks if len(t.strip(".,;:—-")) == 1) >= 0.35 * len(toks)


def despace(s, vocab):
    out, cur = [], ""

    def flush():
        nonlocal cur
        if cur:
            out.append(cur)
            cur = ""

    for t in s.split():
        core = t.strip(".,;:—-()*'’")
        low = core.lower()
        if len(core) > 3 or not core.isalpha() and len(core) > 1:
            flush()
            out.append(t)
            continue
        if cur and len(core) >= 2 and low in _CONN and len(cur.strip(".,;:—-")) >= 3 and (cur + core).lower() not in vocab:
            flush()
            out.append(t)
            continue
        if not cur:
            if len(core) >= 2 and low in _CONN:
                out.append(t)
            else:
                cur = t
        elif t[:1].islower() or not core:
            cur += t
        else:
            flush()
            cur = t
        if cur and cur[-1] in ".,;:—":
            flush()
    flush()
    return " ".join(out)


_NAME_TOK = re.compile(r"^(?:[A-Z][a-z'’.]+,?|and|of|&|Co\.?,?|the|de|Mac[A-Z]\w+,?|M[‘'’]\w+,?)$")


def spaced_header(rec, blocks, vocab):
    """Parties and rubric of a nominate report whose heading is set in letter-spaced capitals."""
    got = {}
    for b in [b for b in blocks if b["t"] != "table"][:8]:
        t = clean_str(b["text"])
        if "ppellant" in t.replace(" ", "") and "espondent" in t.replace(" ", "") and len(t) < 260:
            d = despace(t, vocab)
            m = re.match(r"^(.{3,120}?)[,;.]?\s*Appellants?\s*[;,.]?\s*(.{3,120}?)[,;.]?\s*Respondents?\b", d)
            if m:
                a = m.group(1).strip(" ,;.")
                toks = m.group(2).strip(" ,;.").split()
                keep = []
                for tok in reversed(toks):
                    if _NAME_TOK.match(tok):
                        keep.append(tok)
                    else:
                        break
                bname = " ".join(reversed(keep)).strip(" ,;.")
                a_ok = all(_NAME_TOK.match(x) for x in a.split())
                if a_ok and bname and bname[:1].isupper() and len(bname) > 3:
                    got["parties"] = [{"role": "appellant", "name": a}, {"role": "respondent", "name": bname}]
            break
    for b in [b for b in blocks if b["t"] in ("paragraph", "line")][:8]:
        t = clean_str(b["text"])
        if "—" in t[:160] and len(t) > 60:
            d = despace(t[:400], vocab) if is_spaced(t) else t[:400]
            m = re.match(r"^(.{5,200}?)\.\s*—\s*(?=[A-Z])", d)
            if m:
                items = valid_catchwords([x.strip(" .") for x in m.group(1).split("—")])
                if items:
                    got["catchwords"] = items
            break
    return got


# ============================================================================ law-report case boundaries
_OLD = ("Morison's", "Brown's Supplement", "Elchies", "Hailes")
_DATE_LINE = re.compile(r"^(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+([A-Z][a-z]+)\.?\s+(\S{1,4})\.?$")
_COURT_HEAD = re.compile(r"DIVISION|HOUSE OF LORDS|COURT OF|OUTER HOUSE|JUSTICIARY|TEIND|BILL CHAMBER|EXCHEQUER|REGISTRATION", )
_COUNSEL = re.compile(r"^(?:Counsel|Agents?|Solicitors?) for\b|^Counsel\b.{0,60}Agents?\s*[—-]", I)
_SLR_HEAD = re.compile(r"([A-Z][A-Z0-9'’.,&() \-]{2,70}?)\s+[Vv]\.\s+([A-Z][A-Z0-9'’,&() \-]{2,80}?)\.(?:\s|$)")
_HEAD_YEAR = re.compile(r"^\W{0,3}(?:N[oO0][.,]? ?\S{1,4}[.,]? {0,2})?((?:1[5-8]\d[\dA-Za-z]|[Ilz][5-8]\d\d))[.,]")
_BARE_YEAR = re.compile(r"^\W{0,2}(1[5-8]\d\d)\W{0,2}$")
_RUN_HEAD = re.compile(r"^\W*\d{2,5}\W|\d{3,5}\W*$|\bS[EeiI][Cc] ?[Tt]\b|^DIV\b", )
_OCR_NUM = str.maketrans({"I": "1", "l": "1", "O": "0", "o": "0", "S": "5"})


def _nm(s):
    s = meta._clean_party(s)
    return " ".join(w.capitalize() if w.isupper() and len(w) > 2 and w.isalpha() else w for w in s.split())


def _against_split(text):
    """('left', 'right') around an OCR-tolerant 'against' / 'contra', or None."""
    toks = text.split()
    for i, tok in enumerate(toks):
        core = tok.lower().strip(".,;:")
        if core == "contra" or (5 <= len(core) <= 9 and difflib.SequenceMatcher(None, core, "against").ratio() >= 0.72):
            if 0 < i < len(toks) - 1:
                return " ".join(toks[:i]), " ".join(toks[i + 1:])
    return None


def _recount(content, blocks):
    content["blocks"] = blocks
    content["block_counts"] = dict(Counter(b["t"] for b in blocks))
    parts, page = [], blocks[0]["p"] if blocks else 1
    for b in blocks:
        s = "\n".join(" | ".join(r) for r in b["rows"]) if b["t"] == "table" else ((b["num"] + " ") if "num" in b else "") + b["text"]
        parts.append(("\f" if b["p"] != page else "") + s)
        page = b["p"]
    return "\n\n".join(parts)


def _seg_slr(rec, blocks, stem, dbyear):
    body = [b for b in blocks if b["t"] != "marginal_note"]
    texts = [clean_str(b.get("text", "")) for b in body]
    # a case ends with its "Counsel for ... Agents ..." lines; the next block opens the next case
    cuts = [i + 1 for i in range(len(body) - 1) if _COUNSEL.search(texts[i]) and not _COUNSEL.search(texts[i + 1])]
    bounds = [0] + cuts + [len(body)]
    segs = [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]

    def opens_case(a, b):
        head = texts[a: min(a + 5, b)]
        return any(_DATE_LINE.match(t) or (_COURT_HEAD.search(t) and len(t) < 60) or _SLR_HEAD.search(t) or (t.isupper() and 8 < len(t) < 90) for t in head[:4])

    starts = [s for s in segs if opens_case(*s)]
    if not starts:
        return None
    m = re.search(r"_(\d)$", stem)
    k = int(m.group(1)) if m else 0                       # "_1" = second case beginning on that page
    if segs[0] not in starts and len(segs) == 1:
        return None
    if k >= len(starts):
        return None
    a, b = starts[k]
    seg = body[a:b]
    info = {"isolated": True, "method": "counsel/agent lines close each report; filename suffix picks the case",
            "blocks_dropped_before": a, "blocks_dropped_after": len(body) - b,
            "confidence": "high" if (b < len(body) or _COUNSEL.search(texts[b - 1])) else "medium (end of case not seen in this file)"}
    upd = {}
    for t in texts[a: a + 6]:
        dm = _DATE_LINE.match(t)
        if dm and dbyear:
            mon = meta._MONTHS.get(dm.group(1)[:3].lower())
            day = dm.group(2).rstrip(".,").translate(_OCR_NUM)
            if mon and day.isdigit() and 1 <= int(day) <= 31:
                upd["date"] = f"{dbyear:04d}-{mon:02d}-{int(day):02d}"
        hm = _SLR_HEAD.search(t)
        if hm and "parties" not in upd:
            l, r = _nm(hm.group(1)), _nm(hm.group(2))
            if meta.garbled(hm.group(1)) or meta.garbled(hm.group(2)):
                upd["garbled"] = True
            elif l and r and not re.match(r"(?:LIMITED|LTD|COMPANY|TRUSTEES\)?|AND OTHERS|OTHERS)\b", hm.group(1)):
                upd["parties"] = [{"role": "party a", "name": l}, {"role": "party b", "name": r}]
    for bb in seg[:6]:
        if bb["t"] != "table" and len(bb["text"]) < 400:
            bb["z"] = "header"
    return seg, info, upd


def _seg_dict(rec, blocks, dbyear, item, series):
    body = [b for b in blocks if b["t"] != "marginal_note"]
    notes = [b for b in blocks if b["t"] == "marginal_note"]
    texts = [clean_str(b.get("text", "")) for b in body]

    def cand(t):
        if _BARE_YEAR.match(t):
            return True
        if _HEAD_YEAR.match(t) and (len(t) < 170 or _against_split(t[:130])):
            return True
        return len(t) < 130 and bool(_against_split(t)) and bool(re.search(r"[A-Z]{3,}", t))

    idx = [i for i, t in enumerate(texts) if cand(t)]
    groups = []
    for i in idx:
        if groups and i - groups[-1][-1] == 1 and len(texts[i]) < 130 and len(texts[groups[-1][-1]]) < 130:
            groups[-1].append(i)
        else:
            groups.append([i])
    if not groups:
        return None

    def gtext(g):
        return " ".join(texts[i] for i in g)

    target = None
    if item and series.startswith("Elchies"):
        target = next((g for g in groups if re.match(r"^\W{0,2}N[oO0][.,]? ?%s\b[.,]?" % item, gtext(g))), None)
    if target is None and dbyear:
        with_year = [g for g in groups if re.search(r"\b%d\b" % dbyear, gtext(g)[:60]) or _BARE_YEAR.match(texts[g[-1]]) and str(dbyear) in texts[g[-1]]]
        if len(with_year) == 1:
            target = with_year[0]
        elif len(with_year) > 1:
            return "ambiguous"                             # several cases of that year on these pages
    if target is None and len(groups) == 1 and not texts[0][:1].islower():
        target = groups[0]
    if target is None:
        return None
    gi = groups.index(target)
    a = target[0]
    b = groups[gi + 1][0] if gi + 1 < len(groups) else len(body)
    seg = [bb for bb in body[a:b] if not (bb["t"] == "heading" and _RUN_HEAD.search(clean_str(bb["text"])))]
    info = {"isolated": True, "method": "case heading for the source-path year" + (" / report item number" if series.startswith("Elchies") else ""),
            "blocks_dropped_before": a, "blocks_dropped_after": len(body) - b,
            "confidence": "high" if gi + 1 < len(groups) or a > 0 else "medium (end of case not seen in this file)"}
    upd = {}
    head = gtext(target)
    hm = re.search(r"(1[5-8]\d\d)[.,]?\s+([A-Za-z]{3,9})\.?,?\s+(\d{1,2})\b", head)
    if hm and meta._MONTHS.get(hm.group(2)[:3].lower()) and 1 <= int(hm.group(3)) <= 31:
        upd["date"] = f"{int(hm.group(1)):04d}-{meta._MONTHS[hm.group(2)[:3].lower()]:02d}-{int(hm.group(3)):02d}"
    name_part = re.sub(r"^.*?(?:1[5-8]\d[\dA-Za-z]|[Ilz][5-8]\d\d)[.,]\s*(?:[A-Za-z7]{3,9}\.?,?\s*\w{0,2}[.,]?\s+)?", "", head) if _HEAD_YEAR.match(head) else head
    name_part = re.sub(r"\s*\b1[5-8]\d\d\W*$", "", name_part)
    name_part = re.sub(r"^\S{2,6}\.\s+(?=[A-Z])", "", name_part)                # stray month token before the names
    name_part = re.split(r"\.\s+(?:THE|The|IN|In|A|An|ONE|Found|See)\b", name_part)[0]
    sp = _against_split(name_part)
    if sp:
        l, r = _nm(sp[0]), _nm(sp[1].rstrip(".,"))
        ok = lambda x: x[:1].isupper() and not re.search(r"\d|:", x) and sum(c.isalpha() or c == " " for c in x) >= 0.85 * len(x) \
            and not re.search(r"[a-z][A-Z]{2}|[A-Z]{2}[a-z][A-Z]", x)
        if l and r and len(l.split()) <= 10 and len(r.split()) <= 12 and ok(l) and ok(r) \
                and not meta.garbled(sp[0]) and not meta.garbled(sp[1].rstrip(".,")):
            upd["parties"] = [{"role": "party a", "name": l}, {"role": "party b", "name": r}]
    if item:
        for n in notes:
            nm = re.match(r"^\W{0,2}N[oO0][.,]? ?%s\b[.,]?\s*(.{30,})$" % item, clean_str(n["text"]))
            if nm:
                upd["headnote"] = nm.group(1)
                seg.append(n)
                break
    for i, bb in enumerate(seg):
        bb["z"] = "header" if i < len(target) and bb["t"] != "marginal_note" and len(bb.get("text", "")) < 170 else "body"
    return seg, info, upd


_DANGLING = re.compile(r"(?:\b(?:the|of|and|for|to|in|at|by|v|or|on|from)|&)$", I)
_NOT_A_NAME = re.compile(r"^(?:against|versus|for|and|in|of|by|or|the|absent|between|appellants?|respondents?)$|^(?:against|versus)\b", I)


# ============================================================================ entry point
def apply(rec, content, full_text):
    blocks = content["blocks"]
    # -- replacement characters / private-use glyphs
    n_rc = sum(b.get("text", "").count(RC) for b in blocks)
    for b in blocks:
        if "text" in b and (RC in b["text"] or _PUA.search(b["text"])):
            t = b["text"].replace("", "•").replace("", "•")
            b["text"] = _WS.sub(" ", _PUA.sub(" ", t.replace(RC, " "))).strip()
        if "rows" in b:
            b["rows"] = [[clean_str(c) if (RC in c or _PUA.search(c)) else c for c in r] for r in b["rows"]]
    if n_rc:
        rec["text_quality"] = {"replacement_characters_removed": n_rc,
                               "note": "the PDF's font could not be decoded for some glyphs (usually label separators)"}
        full_text = _WS.sub(lambda m: m.group(0) if "\n" in m.group(0) or "\f" in m.group(0) else " ",
                            _PUA.sub(" ", full_text.replace("", "•").replace(RC, " ")))
    for k in ("title", "case_name", "court", "case_number", "property", "summary", "headnote", "outcome_text"):
        if isinstance(rec.get(k), str):
            rec[k] = clean_str(rec[k]) or None
    for p in rec.get("parties") or []:
        p["name"] = clean_str(p["name"])
    rec["parties"] = [p for p in rec.get("parties") or [] if p["name"] and any(c.isalpha() for c in p["name"])]
    rec["judges"] = [j for j in (clean_str(j) for j in rec.get("judges") or []) if j]

    dtype = rec["document_type"]
    stem = rec["file"].rsplit(".", 1)[0]
    dbyear = rec.get("year") if rec.get("provenance", {}).get("bailii_year") else None
    series = rec.get("report_series") or ""
    cite = rec.get("reporter_citation") or rec.get("neutral_citation") or rec.get("bailii_citation") or stem
    ocr_first = rec.get("_ocr_first", False)

    # -- law reports: isolate the case this file is about
    if dtype == "LAW_REPORT" and blocks:
        res = None
        if "Scottish Law Reporter" in series:
            res = _seg_slr(rec, blocks, stem, dbyear)
        elif series.startswith(_OLD):
            res = _seg_dict(rec, blocks, dbyear, rec.get("report_item_no"), series)
        if res == "ambiguous":
            rec["case_segment"] = {"isolated": False, "note": "several cases of the source-path year on these pages; whole text kept"}
            rec["parties"], rec["case_name"], rec["title"] = [], None, cite
        elif res:
            seg, info, upd = res
            if seg and (info["blocks_dropped_before"] or info["blocks_dropped_after"] or upd):
                full_text = _recount(content, seg)
                blocks = seg
                flat = meta.flat(full_text)
                rec["cases_cited"] = meta.cases_cited(flat, [])
                rec["legislation_cited"] = meta.legislation_cited(flat)
            rec["case_segment"] = info
            if "parties" in upd:
                rec["parties"] = upd["parties"]
                rec["case_name"] = rec["title"] = f"{upd['parties'][0]['name']} v {upd['parties'][1]['name']}"
            elif info["blocks_dropped_before"] or info["blocks_dropped_after"] or upd.get("garbled") or series.startswith(_OLD):
                rec["parties"], rec["case_name"], rec["title"] = [], None, cite     # names found earlier may be a neighbour's
            if "date" in upd:
                rec["judgment_date"] = rec["date"] = upd["date"]
                rec["judgment_date_source"], rec["date_confidence"] = "heading of the isolated case", "high"
            if "headnote" in upd:
                rec["headnote"] = rec["summary"] = upd["headnote"]
        elif "Scottish Law Reporter" in series or series.startswith(_OLD):
            rec["case_segment"] = {"isolated": False, "note": "could not tell which case on these pages the file refers to; whole text kept"}
            if series.startswith(_OLD) and not (rec.get("judgment_date") or "").startswith(str(dbyear or "x")):
                rec["parties"], rec["case_name"], rec["title"] = [], None, cite
        # nominate reports set in letter-spaced capitals
        if not series.startswith(_OLD) and "Scottish Law Reporter" not in series and not ocr_first:
            vocab = set(re.findall(r"[a-z]{3,}", full_text.lower()))
            got = spaced_header(rec, blocks, vocab)
            if got.get("parties") and not rec.get("parties"):
                rec["parties"] = got["parties"]
                rec["case_name"] = rec["title"] = f"{got['parties'][0]['name']} v {got['parties'][1]['name']}"
            if got.get("catchwords"):
                rec["catchwords"] = rec["editorial_catchwords"] = got["catchwords"]

    # -- judgments: complete party list from the "Between" block
    if dtype == "CASE_JUDGMENT":
        hp = header_parties(blocks)
        if hp:
            parties, side_names = hp
            rec["parties"] = parties
            rec["parties_source"] = "header 'between' block"
            name = _case_name(side_names)
            if name:
                rec["case_name"] = rec["title"] = name
        else:                                              # no such block: at least never list a party twice
            seen, uniq = {}, []
            for p in rec.get("parties") or []:
                if p["name"] in seen:
                    seen[p["name"]].setdefault("roles", [seen[p["name"]]["role"]])
                    if p["role"] not in seen[p["name"]]["roles"]:
                        seen[p["name"]]["roles"].append(p["role"])
                else:
                    seen[p["name"]] = p
                    uniq.append(p)
            rec["parties"] = uniq
        if not rec.get("judges"):
            js = header_judges(blocks)
            if js:
                rec["judges"], rec["judges_source"] = js, "header text"

    # -- catchwords: keep only a real rubric
    for k in ("catchwords", "editorial_catchwords"):
        if rec.get(k):
            rec[k] = valid_catchwords(rec[k])

    # -- outcome: explicit decision text only
    old = rec.get("outcome")
    label, text, src = explicit_outcome(rec, blocks)
    rec["outcome"], rec["outcome_text"], rec["outcome_source"] = label, text, src
    if old and not label:
        rec["outcome_unconfirmed"] = old                   # what the looser rules guessed; not asserted
    finalfix.apply(rec, blocks, dtype, ocr_first)           # layout-specific parsers for what is still missing
    odd = re.compile(r"[A-Za-z]\d[A-Za-z]|[A-Za-z]{2}\d|\d[a-z]{2}|[£$*^~|\\<>=%#?!_{}\[\]]")
    if any(not p["name"][:1].isalnum() or re.search(r"\d{4}/|/\d{4}|: ", p["name"]) or odd.search(p["name"]) for p in rec.get("parties") or []):
        rec["parties"], rec["case_name"], rec["title"] = [], None, None
    for p in rec.get("parties") or []:
        p["name"] = re.sub(r"\s*[—–-]+$", "", p["name"]).strip() or p["name"]
    if any(_DANGLING.search(p["name"]) or _NOT_A_NAME.match(p["name"]) for p in rec.get("parties") or []):
        rec["parties"], rec["case_name"], rec["title"] = [], None, None      # cut off mid-name: the citation is safer
        rec["title_note"] = "party names were truncated in the source layout; citation used"
    if not rec.get("title"):
        rec["title"] = cite
    rec.pop("_ocr_first", None)
    return rec, content, full_text
