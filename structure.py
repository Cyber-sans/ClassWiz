"""Layout (cached lines with positions) -> ordered content blocks ready for Akoma Ntoso serialisation.

Block types: heading, paragraph (optionally numbered), list_item, table, line (short header/cover
lines), footnote, marginal_note. Every block carries its start page; a paragraph that runs over a
page boundary records the character offset of each break in "pb". Running headers/footers, page
numbers and margin line numbers are removed from the flow and reported separately.
"""
import re
from collections import Counter

BOLD, ITALIC, ROTATED = 1, 2, 4

_TERMINAL = re.compile(r"[.:;!?][\"'”’)\]]*$")
_SOFT_END = re.compile(r"[,;:\-–—]$|\b(?:and|or|the|of|to|a|in|that|for|by|with|on)$", re.I)
_NUM_BRACKET = re.compile(r"^\[(\d{1,3})\]\s*")
_NUM_DOT = re.compile(r"^(\d{1,3})[.)]\s*(?=\S)")
_NUM_MULTI = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){1,3})\.?\s+(?=\S)")
_NUM_BARE = re.compile(r"^(\d{1,3})\s+(?=\S)")
_SUB = re.compile(r"^\(([a-z]{1,2}|[ivxl]{1,6}|\d{1,3}|[A-Z])\)\s+(?=\S)")
_SUB2 = re.compile(r"^([a-z]|[ivx]{2,4})[.)]\s+(?=[A-Za-z“\"'‘])")
_BULLET = re.compile(r"^([•▪◦‣●○■□➢*]|[-–—])\s+(?=\S+\s+\S)")
_RULE_LINE = re.compile(r"^[-–—_=.\s*]{3,}$")
_CROWN_FOOT = re.compile(r"^©?\s*crown copyright(?:\s+\d{4})?\.?$", re.I)
_NUM_TOKEN = re.compile(r"^(?:\[?\d{1,3}(?:\.\d{1,3}){0,3}[.\])]?|\(?[a-z]{1,3}[.)]|\(?[ivxl]{1,6}[.)]|[•▪◦‣●○■□➢*–—-])$")
_PAGE_NO = re.compile(r"^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$|^[-–—]\s*\d{1,4}\s*[-–—]$", re.I)
_MARGIN_TOKEN = re.compile(r"^(?:\d{1,3}|[A-H])$")
_DIGITS = re.compile(r"\d+")
_WS = re.compile(r"\s+")
_KNOWN_HEADING = re.compile(
    r"^(?:\d{1,2}\.?\s+|[A-Z]\.\s+|[IVX]{1,4}\.?\s+)?(?:introduction|background|the facts|facts|the law|the legal framework|"
    r"legal framework|relevant law|discussion|analysis|conclusions?|decision|decision and reasons|determination and reasons|"
    r"reasons|reasons for (?:the )?decision|judgment|approved judgment|order|summary|the issues?|issues|the evidence|evidence|"
    r"findings(?: of fact)?|submissions|the hearing|the appeal|grounds of appeal|disposal|costs|expenses|remedy|"
    r"request and response|scope of the case|right of appeal|other matters|notice of decision|preliminary|the claims?|"
    r"decision \(including any steps ordered\)|the parties|procedural history|opinion|anonymity|error of law|"
    r"the proceedings|representation|appearances)\s*:?$", re.I)
_BODY_START = re.compile(r"^(?:approved )?(?:judgment|reasons|decision(?: (?:and|&) reasons)?|determination(?: (?:and|&) reasons)?|"
                         r"opinion|introduction|decision \(including any steps ordered\)|summary|ruling|note)\s*:?$", re.I)
_SIGN = re.compile(r"judge|signed|dated?\b|registrar|chairman|tribunal|commissioner|president|\b(?:19|20)\d\d\b", re.I)


class Row:
    __slots__ = ("x0", "y0", "x1", "y1", "size", "flags", "text", "first", "pieces", "page", "grp", "blk", "table", "h", "yc")

    def __init__(self, parts, page, grp, ocr=False):
        parts.sort(key=lambda l: l[0])
        self.x0 = parts[0][0]
        self.x1 = max(p[2] for p in parts)
        self.y0 = min(p[1] for p in parts)
        self.y1 = max(p[3] for p in parts)
        big = max(parts, key=lambda l: len(l[6]))
        self.size = big[4]
        self.flags = big[5]
        self.first = parts[0][6].strip()
        self.pieces = len(parts)
        self.text = _WS.sub(" ", " ".join(p[6].strip() for p in parts)).strip()
        self.page, self.grp, self.blk = page, grp, 0 if ocr or len(parts[0]) < 8 else parts[0][7]
        self.yc = (self.y0 + self.y1) / 2 if ocr else self.y0      # OCR box tops jitter with ascenders
        self.table = None
        self.h = self.y1 - self.y0


def _furniture(pages):
    """Indices (page, line) of running headers/footers, page numbers."""
    n = len(pages)
    seen = Counter()
    cand = []
    force = set()
    for pi, pg in enumerate(pages):
        h = pg["h"]
        for li, l in enumerate(pg["lines"]):
            zone = "t" if l[3] < 0.085 * h else ("b" if l[1] > 0.915 * h else None)
            if not zone:
                continue
            txt = l[6].strip()
            key = zone + _DIGITS.sub("#", txt.lower())
            if pg["src"] == "ocr" and zone == "t" and n >= 2 and l[3] < 0.08 * h:
                force.add((pi, li))                    # scanned report pages: the top band is the running head
            cand.append((pi, li, key, txt))
            seen[(key, pi)] = 1
    per_key = Counter(k for k, _ in seen)
    need = max(3, 0.5 * n)
    drop, texts = set(), Counter()
    for pi, li, key, txt in cand:
        if (pi, li) in force or _PAGE_NO.match(txt) or _CROWN_FOOT.match(txt) or (n >= 3 and per_key[key] >= need):
            drop.add((pi, li))
            if not _PAGE_NO.match(txt):
                texts[_DIGITS.sub("#", txt)] += 1
    return drop, [t for t, _ in texts.most_common(6)]


def _columns(lines, w):
    """Split a page's lines into reading-order groups; two-column pages come back as [top, left, right, bottom]."""
    long = [l for l in lines if len(l[6]) >= 25]
    if len(long) < 14:
        return [lines]
    best = None
    for g in range(int(w * 0.38), int(w * 0.62), 3):
        left = sum(1 for l in long if l[2] <= g + 1)
        right = sum(1 for l in long if l[0] >= g - 1)
        cross = len(long) - left - right
        if left >= 0.3 * len(long) and right >= 0.3 * len(long) and cross <= 0.08 * len(long):
            if best is None or cross < best[0]:
                best = (cross, g)
    if best is None:
        return [lines]
    g = best[1]
    full = [l for l in lines if l[0] < g - 6 and l[2] > g + 6]
    cols = [l for l in lines if not (l[0] < g - 6 and l[2] > g + 6)]
    top_y = min(l[1] for l in cols)
    top = [l for l in full if l[1] < top_y + 2]
    bottom = [l for l in full if l[1] >= top_y + 2]
    return [grp for grp in (top, [l for l in cols if (l[0] + l[2]) / 2 < g], [l for l in cols if (l[0] + l[2]) / 2 >= g], bottom) if grp]


def _rows(lines, page, grp, ocr=False):
    lines = sorted(lines, key=lambda l: (l[1] + l[3]) / 2)
    out, cur, cy = [], [], None
    for l in lines:
        c = (l[1] + l[3]) / 2
        if cur and abs(c - cy) <= 0.45 * max(min(l[3] - l[1], cur[0][3] - cur[0][1]), 4):
            cur.append(l)
        else:
            if cur:
                out.append(Row(cur, page, grp, ocr))
            cur, cy = [l], c
    if cur:
        out.append(Row(cur, page, grp, ocr))
    return out


def _percentile(vals, q):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(q * len(vals)))] if vals else 0


def _caps_ratio(s):
    letters = [c for c in s if c.isalpha()]
    return sum(c.isupper() for c in letters) / len(letters) if len(letters) >= 4 else 0.0


def build(layout, doctype="CASE_JUDGMENT"):
    pages = layout.get("pages", [])
    drop, running = _furniture(pages)
    stream, margin_blocks, removed_margin = [], [], 0
    sizes = Counter()
    for pi, pg in enumerate(pages):
        w = pg["w"]
        tables = pg.get("tables") or []
        lines = []
        sideways = sum(1 for l in pg["lines"] if l[5] & ROTATED) > 0.7 * max(len(pg["lines"]), 1)   # whole page set sideways: keep it
        for li, l in enumerate(pg["lines"]):
            if (pi, li) in drop or (l[5] & ROTATED and not sideways) or not l[6].strip():
                continue
            cx, cy = (l[0] + l[2]) / 2, (l[1] + l[3]) / 2
            if any(t["bbox"][0] - 2 <= cx <= t["bbox"][2] + 2 and t["bbox"][1] - 2 <= cy <= t["bbox"][3] + 2 for t in tables):
                continue
            lines.append(l)
        # margin line numbers / marginal notes: anything wholly outside the body text band
        long = [l for l in lines if len(l[6]) >= 40]
        if len(long) >= 8:
            bx0, bx1 = _percentile([l[0] for l in long], 0.1), _percentile([l[2] for l in long], 0.9)
            body, notes = [], []
            outside = [l for l in lines if l[2] < bx0 - 3 or l[0] > bx1 + 3]
            fives = sum(1 for l in outside if l[6].strip().isdigit() and int(l[6]) % 5 == 0)
            letters = len({l[6].strip() for l in outside if re.fullmatch(r"[A-H]", l[6].strip())})
            for l in lines:
                t = l[6].strip()
                if l[2] < bx0 - 3 or l[0] > bx1 + 3:
                    if (t.isdigit() and int(t) % 5 == 0 and fives >= 3) or (re.fullmatch(r"[A-H]", t) and letters >= 4):
                        removed_margin += 1                    # line-number ladder in the margin
                    elif _NUM_TOKEN.match(t) or _MARGIN_TOKEN.match(t):
                        body.append(l)                         # hanging paragraph number: belongs to its row
                    else:
                        notes.append(l)
                else:
                    body.append(l)
            lines = body
            notes.sort(key=lambda l: l[1])
            cur = []
            for l in notes:
                if cur and l[1] - cur[-1][3] > 1.2 * (l[3] - l[1]):
                    margin_blocks.append((pi + 1, cur[0][1], " ".join(x[6].strip() for x in cur)))
                    cur = []
                cur.append(l)
            if cur:
                margin_blocks.append((pi + 1, cur[0][1], " ".join(x[6].strip() for x in cur)))
        is_ocr = pg["src"] == "ocr"
        lines = [l for l in lines if not _RULE_LINE.match(l[6].strip())]
        groups = _columns(lines, w)
        page_rows = []
        for gi, grp in enumerate(groups):
            rws = _rows(grp, pi + 1, gi, is_ocr)
            page_rows.append(rws)
        flat_rows = [r for rws in page_rows for r in rws]
        for r in (flat_rows[:1] + flat_rows[-1:]) if len(flat_rows) > 2 else []:
            if _PAGE_NO.match(r.text):                         # page number that sits outside the header band
                for rws in page_rows:
                    if r in rws:
                        rws.remove(r)
        for t in tables:                              # a table sits in the first group, at its vertical position
            r = Row([[t["bbox"][0], t["bbox"][1], t["bbox"][2], t["bbox"][3], 0, 0, "", 0]], pi + 1, 0)
            r.table = t["rows"]
            page_rows[0].append(r) if page_rows else page_rows.append([r])
            page_rows[0].sort(key=lambda r: r.y0)
        for rows in page_rows:
            for r in rows:
                if r.table is None:
                    sizes[round(r.size)] += len(r.text)
            stream.extend(rows)

    body_size = sizes.most_common(1)[0][0] if sizes else 10
    scan_like = bool(pages) and (pages[0]["src"] == "ocr" or bool(pages[0].get("img")))
    # per (page, group) geometry: left margin, right edge, leading
    geo = {}
    by_grp = {}
    for r in stream:
        if r.table is None:
            by_grp.setdefault((r.page, r.grp), []).append(r)
    for key, rows in by_grp.items():
        longr = [r for r in rows if len(r.text) >= 40] or rows
        left = Counter(round(r.x0) for r in longr).most_common(1)[0][0]
        right = _percentile([r.x1 for r in longr], 0.9)
        dys = [b.yc - a.yc for a, b in zip(rows, rows[1:]) if 0 < b.yc - a.yc < 3.2 * max(a.size, 6)]
        ocr_grp = pages[key[0] - 1]["src"] == "ocr"
        lead = _percentile(dys, 0.5 if ocr_grp else 0.3) if len(dys) >= 4 else 1.25 * max(body_size, 6)
        just = sum(1 for r in longr if abs(r.x1 - right) < 4) / len(longr) if len(longr) >= 6 else 0
        geo[key] = (left, max(right, left + 50), max(lead, 4), just >= 0.6)

    blocks = []
    cur = None
    prev = None
    last_top = None
    outer = [None]

    def flush():
        nonlocal cur
        if cur is not None:
            blocks.append(cur)
            cur = None

    def numbering(r, prev_ok, gap=False):
        """-> (kind, marker, rest) for a row that opens a numbered paragraph / list item, else None."""
        nonlocal last_top
        t = r.text
        m = _NUM_BRACKET.match(t)
        if m:
            last_top = int(m.group(1))
            return "paragraph", f"[{m.group(1)}]", t[m.end():]
        m = _NUM_MULTI.match(t)
        if m:
            head = int(m.group(1).split(".")[0])
            if last_top is None or head in (last_top, last_top + 1) or (r.pieces > 1 and prev_ok):
                if head == (last_top or 0) + 1:
                    last_top = head
                return "paragraph", m.group(1), t[m.end():]
        m = _NUM_DOT.match(t)
        bare = None
        if not m:
            bare = _NUM_BARE.match(t)
            if not (bare and r.pieces > 1 and r.first.rstrip(".") == bare.group(1)):
                bare = None
        if m or bare:
            n = int((m or bare).group(1))
            ok = False
            if last_top is not None and n == last_top + 1:
                ok = True
            elif outer[0] is not None and outer[0] < n <= outer[0] + 3 and prev_ok:
                ok, outer[0] = True, None
            elif n == 1 and prev_ok and m:
                ok = True
                if outer[0] is None:
                    outer[0] = last_top
            elif last_top is not None and last_top < n <= last_top + 12 and prev_ok and m:
                ok = True
            elif last_top is None and prev_ok and m and (n <= 3 or r.pieces > 1):
                ok = True
            elif prev_ok and m and r.pieces > 1 and r.first.rstrip(".)") == m.group(1) and n > 1 and gap:
                ok, outer[0] = True, None             # hanging number after a gap: trust the layout
            if ok:
                last_top = n
                return "paragraph", (m.group(1) + ".") if m else bare.group(1), t[(m or bare).end():]
        if prev_ok:
            for rx in (_SUB, _SUB2):
                m = rx.match(t)
                if m:
                    return "list_item", m.group(0).strip(), t[m.end():]
            m = None if re.match(r"^[-–—]\s*(?:and|v\.?|against)\b", t, re.I) else _BULLET.match(t)
            if m and (m.group(1) not in "-–—" or r.pieces > 1 or prev_ok):
                return "list_item", m.group(1), t[m.end():]
        return None

    for r in stream:
        if r.table is not None:
            flush()
            used = {ci for row in r.table for ci, c in enumerate(row) if c.strip()}
            if len(used) < 2:                       # a ruled box around prose, not a table
                cells = [_WS.sub(" ", c).strip() for row in r.table for c in row if c.strip()]
                paras, width = [], max((len(c) for c in cells), default=0)
                for c in cells:
                    if paras and not (len(paras[-1]) < 40 and not _TERMINAL.search(paras[-1])) \
                            and not (_TERMINAL.search(paras[-1]) and len(paras[-1].rsplit(" ", 12)[0]) < 0 ) \
                            and not (_TERMINAL.search(paras[-1]) and c[:1].isupper() and len(c) < 40):
                        if _TERMINAL.search(paras[-1]) and c[:1].isupper() and len(paras[-1]) % max(width, 1) < 0.75 * width and len(paras[-1]) > width:
                            paras.append(c)
                        else:
                            paras[-1] += " " + c
                    else:
                        paras.append(c)
                for row in [paras]:
                    for c in row:
                        for para in [c]:
                            blocks.append({"t": "paragraph", "p": r.page, "text": _WS.sub(" ", para).strip(), "_size": body_size,
                                           "_bold": False, "_italic": False, "_rows": 9, "_x0": 0, "_x1": 0, "_y": 0, "_left": 0,
                                           "_right": 0, "_indented": False, "_at_left": 0, "_gap": True, "_ph": 1, "boxed": True})
            else:
                blocks.append({"t": "table", "p": r.page, "rows": r.table})
            prev = None
            continue
        left, right, lead, justified = geo[(r.page, r.grp)]
        width = right - left
        same_flow = prev is not None and prev.page == r.page and prev.grp == r.grp
        prev_terminal = cur is None or bool(_TERMINAL.search(cur["text"])) or cur["t"] != "paragraph"
        ocr_row = pages[r.page - 1]["src"] == "ocr"
        gap = same_flow and (r.yc - prev.yc) > (1.7 if ocr_row else 1.45) * lead
        prev_short = prev is not None and (geo[(prev.page, prev.grp)][1] - prev.x1) > 0.3 * (geo[(prev.page, prev.grp)][1] - geo[(prev.page, prev.grp)][0])
        prev_ok = cur is None or gap or prev_terminal or prev_short or not same_flow
        num = numbering(r, prev_ok, gap or not same_flow)
        new = cur is None or num is not None
        if not new and same_flow:
            ratio = max(r.size, prev.size) / max(min(r.size, prev.size), 1)
            style = (ratio > 1.35 if (scan_like or ocr_row) else abs(r.size - prev.size) > 0.9) \
                or ((r.flags ^ prev.flags) & BOLD and (len(r.text) < 80 or len(prev.text) < 80))
            indent_first = (r.x0 - left) > 5 and (r.x0 - left) < 0.25 * width and abs(prev.x0 - left) <= 3 \
                and (cur["_indented"] or cur["_at_left"] >= 2)
            short_end = prev_short or (justified and (geo[(prev.page, prev.grp)][1] - prev.x1) > 0.12 * width and prev_terminal)
            block_break = r.blk != prev.blk and prev_terminal and (r.y0 - prev.y0) > 1.15 * lead
            new = gap or style or indent_first or short_end or block_break
        elif not new:                                   # page or column change: does the paragraph run on?
            headingish = bool(_KNOWN_HEADING.match(r.text)) or (_caps_ratio(r.text) >= 0.8 and len(r.text) < 60)
            cont = cur["t"] in ("paragraph", "list_item") and not _TERMINAL.search(cur["text"]) \
                and not prev_short and not headingish and (scan_like or abs(r.size - cur["_size"]) <= 0.9) \
                and not (r.flags & BOLD and not cur["_bold"]) \
                and (r.text[:1].islower() or bool(_SOFT_END.search(cur["text"])) or cur["text"][-1:].isalnum())
            new = not cont
        if new:
            flush()
            text = r.text
            cur = {"t": "paragraph", "p": r.page, "text": text, "_size": r.size, "_bold": bool(r.flags & BOLD),
                   "_italic": bool(r.flags & ITALIC), "_rows": 1, "_x0": r.x0, "_x1": r.x1, "_y": r.y0, "_left": left,
                   "_right": right, "_indented": (r.x0 - left) > 5, "_at_left": int(abs(r.x0 - left) <= 3),
                   "_gap": bool(gap) or not same_flow, "_ph": pages[r.page - 1]["h"]}
            if num:
                cur["t"], cur["num"], cur["text"] = num[0], num[1], num[2]
        else:
            if r.page != cur["p"] and not same_flow and r.page != prev.page:
                cur.setdefault("pb", []).append([len(cur["text"]), r.page])
            if cur["text"].endswith("-") and r.text[:1].islower() and len(cur["text"]) > 1 and cur["text"][-2].isalpha():
                cur["text"] = cur["text"][:-1] + r.text
            else:
                cur["text"] += " " + r.text
            cur["_rows"] += 1
            cur["_at_left"] += int(abs(r.x0 - left) <= 3)
            cur["_bold"] = cur["_bold"] and bool(r.flags & BOLD)
            cur["_x1"] = max(cur["_x1"], r.x1)
        prev = r
    flush()

    # ---- classify blocks: headings, footnotes
    for b in blocks:
        if b["t"] == "table":
            continue
        txt = b["text"]
        small = b["_size"] <= body_size - 1.5 and not scan_like and pages[b["p"] - 1]["src"] == "text"
        if small and "num" not in b and len(txt) >= 30 and b["_y"] > 0.55 * b["_ph"] and re.match(r"^(?:\d{1,3}|\*|†)\s*\S", txt) \
                and b["t"] != "list_item":
            b["t"] = "footnote"
            continue
        if b["t"] == "paragraph" and "num" in b and not (len(txt) <= 70 and (b["_bold"] or _caps_ratio(txt) >= 0.8)):
            continue
        if b["t"] != "paragraph" or len(txt) > 130 or b["_rows"] > 3:
            continue
        caps = _caps_ratio(txt) >= 0.8
        known = bool(_KNOWN_HEADING.match(txt))
        ends_stop = txt.endswith((".", ";", ",", ":")) and not caps
        if pages[b["p"] - 1]["src"] == "ocr" and not (caps or known):
            continue
        centred = abs((b["_x0"] + b["_x1"]) / 2 - (b["_left"] + b["_right"]) / 2) < 12 and (b["_x0"] - b["_left"]) > 25
        if known or (not ends_stop and (b["_bold"] or caps or b["_size"] >= body_size + 1
                                        or (b["_italic"] and len(txt) <= 70 and b["_gap"])
                                        or (centred and len(txt.split()) <= 6 and txt[:1].isupper() and b["_gap"]))):
            b["t"] = "heading"
            b["level"] = 1 if b["_size"] >= body_size + 3 else (2 if (caps or b["_bold"]) else 3)

    # ---- zones (document-type aware)
    n = len(blocks)
    body_from, concl_from = 0, n
    if doctype in ("CASE_JUDGMENT", "LAW_REPORT"):
        for i, b in enumerate(blocks):
            if b["t"] == "table":
                continue
            first_para = b["t"] == "paragraph" and b.get("num") in ("1.", "1", "[1]", "1.1")
            if first_para or (b["t"] == "heading" and _BODY_START.match(b["text"]) and i > 1) \
                    or (doctype == "LAW_REPORT" and b["t"] == "paragraph" and len(b["text"]) > 300):
                body_from = i
                break
        else:
            body_from = 0 if doctype == "LAW_REPORT" else min(n, next((i for i, b in enumerate(blocks) if b["t"] == "paragraph" and len(b.get("text", "")) > 250), 0))
        j = n
        while j > body_from + 1 and blocks[j - 1]["t"] in ("paragraph", "heading", "line") and len(blocks[j - 1]["text"]) < 160 \
                and "num" not in blocks[j - 1]:
            j -= 1
        if j < n and any(_SIGN.search(b["text"]) for b in blocks[j:]) and n - j <= 14:
            concl_from = j
    for i, b in enumerate(blocks):
        b["z"] = "header" if i < body_from else ("conclusions" if i >= concl_from else "body")
        if b["z"] in ("header", "conclusions") and b["t"] in ("heading", "paragraph") and "num" not in b and b["t"] != "table" \
                and len(b.get("text", "")) < 130 and not (b["t"] == "heading" and _BODY_START.match(b["text"])):
            b["t"] = "line"
            b.pop("level", None)
        for k in [k for k in b if k.startswith("_")]:
            del b[k]
    for page, y, text in margin_blocks:
        blocks.append({"t": "marginal_note", "p": page, "z": "body", "text": _WS.sub(" ", text)})
    order = {id(b): i for i, b in enumerate(blocks)}
    blocks.sort(key=lambda b: (b["p"], order[id(b)]))

    parts, page = [], 1
    for b in blocks:
        s = "\n".join(" | ".join(r) for r in b["rows"]) if b["t"] == "table" else ((b["num"] + " ") if "num" in b else "") + b["text"]
        parts.append(("\f" if b["p"] != page else "") + s)
        page = b["p"]
    stats = Counter(b["t"] for b in blocks)
    return {"blocks": blocks, "running_headers": running, "margin_numbers_removed": removed_margin,
            "block_counts": dict(stats)}, "\n\n".join(parts)


def native_text(layout):
    """Page texts in the PDF's own line order (what the metadata rules were tuned on)."""
    return ["\n".join(l[6] for l in pg["lines"]) for pg in layout.get("pages", [])]
