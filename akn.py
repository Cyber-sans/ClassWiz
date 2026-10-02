"""Minimal Akoma Ntoso 3.0 serialiser for ingest records.

Not a full AKN implementation: it shows that the stored structure (zones, numbered paragraphs,
headings, lists, tables, page breaks, FRBR-style identification) maps onto AKN without going back
to the PDF.     python akn.py ingest_sample 2200630_2017.pdf > out.xml
"""
import re
import sys
from xml.sax.saxutils import escape as _escape, quoteattr as _quoteattr

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")      # not allowed in XML 1.0


def escape(s):
    return _escape(_CTRL.sub("", s))


def quoteattr(s):
    return _quoteattr(_CTRL.sub("", s))

NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _inline(b):
    """Block text with <eop/> page-break milestones."""
    text, out, last = b.get("text", ""), [], 0
    for off, page in b.get("pb", []):
        out.append(escape(text[last:off]) + f'<eop eId="page_{page}"/>')
        last = off
    out.append(escape(text[last:]))
    return "".join(out)


def _block(b, i):
    t = b["t"]
    if t == "heading":
        return f'<hcontainer name="heading" eId="hd_{i}"><heading>{_inline(b)}</heading></hcontainer>'
    if t == "table":
        rows = "".join("<tr>" + "".join(f"<td><p>{escape(c)}</p></td>" for c in r) + "</tr>" for r in b["rows"])
        return f'<table eId="tbl_{i}">{rows}</table>'
    if t == "paragraph" and "num" in b:
        return f'<paragraph eId="para_{i}"><num>{escape(b["num"])}</num><content><p>{_inline(b)}</p></content></paragraph>'
    if t == "list_item":
        return f'<blockList eId="list_{i}"><item><num>{escape(b.get("num", ""))}</num><p>{_inline(b)}</p></item></blockList>'
    if t in ("footnote", "marginal_note"):
        place = "bottom" if t == "footnote" else "side"
        return f'<p><authorialNote placement="{place}" eId="note_{i}"><p>{_inline(b)}</p></authorialNote></p>'
    return f"<p>{_inline(b)}</p>"


def to_akn(rec):
    judgment = rec["document_type"] in ("CASE_JUDGMENT", "LAW_REPORT")
    root = "judgment" if judgment else "doc"
    ident = rec.get("neutral_citation") or rec.get("bailii_citation") or rec["source_filename"]
    date = rec.get("judgment_date") or (f"{rec['year']}-01-01" if rec.get("year") else "0001-01-01")
    uri = "/akn/" + "".join(c if c.isalnum() else "-" for c in ident).strip("-").lower()
    blocks = rec.get("content", {}).get("blocks", [])
    zone = lambda z: "".join(_block(b, i) for i, b in enumerate(blocks) if b.get("z", "body") == z)
    refs = "".join(f'<TLCOrganization eId="court" href="/ontology/organization/court" showAs={quoteattr(rec["court"])}/>'
                   for _ in [0] if rec.get("court"))
    refs += "".join(f'<TLCPerson eId="party_{i}" href="/ontology/person/party_{i}" showAs={quoteattr(p["name"])}/>'
                    for i, p in enumerate(rec.get("parties") or []))
    keywords = "".join(f'<keyword value={quoteattr(t)} showAs={quoteattr(t)} dictionary="#lwiz"/>' for t in rec.get("subject_tags") or [])
    meta = (f'<meta><identification source="#lwiz">'
            f'<FRBRWork><FRBRthis value="{uri}"/><FRBRuri value="{uri}"/><FRBRdate date="{date}" name="judgment"/>'
            f'<FRBRauthor href="#court"/><FRBRcountry value={quoteattr(rec.get("jurisdiction") or "")}/></FRBRWork>'
            f'<FRBRExpression><FRBRthis value="{uri}/eng"/><FRBRuri value="{uri}/eng"/><FRBRdate date="{date}" name="judgment"/>'
            f'<FRBRauthor href="#court"/><FRBRlanguage language="eng"/></FRBRExpression>'
            f'<FRBRManifestation><FRBRthis value="{uri}/eng/data.xml"/><FRBRuri value="{uri}/eng/data.xml"/>'
            f'<FRBRdate date="{date}" name="transform"/><FRBRauthor href="#lwiz"/></FRBRManifestation></identification>'
            f'<classification source="#lwiz">{keywords}</classification>'
            f'<references source="#lwiz">{refs}</references>'
            f'<proprietary source="#lwiz"><sourceUrl>{escape(rec.get("source_url") or "")}</sourceUrl>'
            f'<sourceFilename>{escape(rec["source_filename"])}</sourceFilename>'
            f'<cite>{escape(ident)}</cite><rights>{escape((rec.get("rights") or {}).get("status", ""))}</rights>'
            f'<restriction>{escape((rec.get("restrictions") or {}).get("status", ""))}</restriction></proprietary></meta>')
    if judgment:
        body = (f"<header>{zone('header')}</header><judgmentBody><decision>{zone('body') or '<p/>'}</decision></judgmentBody>"
                + (f"<conclusions>{zone('conclusions')}</conclusions>" if any(b.get('z') == 'conclusions' for b in blocks) else ""))
    else:
        body = f"<mainBody>{''.join(_block(b, i) for i, b in enumerate(blocks)) or '<p/>'}</mainBody>"
    name = ' name="judgment"' if judgment else ' name="document"'
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<akomaNtoso xmlns="{NS}"><{root}{name}>{meta}{body}</{root}></akomaNtoso>'


if __name__ == "__main__":
    import ingest
    for rec in ingest.read_records(sys.argv[1]):
        if len(sys.argv) < 3 or rec["source_filename"] == sys.argv[2]:
            sys.stdout.reconfigure(encoding="utf-8")
            print(to_akn(rec))
            break
