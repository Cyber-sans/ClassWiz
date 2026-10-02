"""Shared feature construction: filename-shape tokens + head text -> hashed n-grams."""
import re

from sklearn.feature_extraction.text import HashingVectorizer

CLASSES = ["CASE_JUDGMENT", "LAW_REPORT", "LEGISLATION", "CASE_SUMMARY",
           "GUIDANCE_RULES_CODE", "TREATY_INTERNATIONAL", "OTHER", "UNKNOWN"]

MIN_TEXT = 200  # fewer chars than this on the first pages => no usable text layer (scan)

_DIGITS = re.compile(r"\d+")
_NONWORD = re.compile(r"[^A-Za-z0-9]+")
_ALPHA = re.compile(r"[A-Za-z]+")


def filename_tokens(name):
    """'2200630_2017.pdf' -> 'fnshape_d7xd4'; 'Mor0100056-005.pdf' -> 'fnshape_mord7xd3 fnalpha_mor'."""
    stem = name.rsplit(".", 1)[0]
    shape = _NONWORD.sub("x", _DIGITS.sub(lambda m: "d%d" % len(m.group()), stem)).lower()
    toks = ["fnshape_" + shape]
    toks += ["fnalpha_" + a.lower() for a in _ALPHA.findall(stem)[:3]]
    return " ".join(toks)


def doc_string(name, text):
    fn = filename_tokens(name)
    # filename tokens repeated so they are not drowned out by ~500 text tokens
    return f"{fn} {fn} {fn} {text.lower()}"


def make_vectorizer():
    # stateless => nothing to fit, safe to rebuild anywhere, very fast
    return HashingVectorizer(n_features=2 ** 18, ngram_range=(1, 2), alternate_sign=False,
                             norm="l2", lowercase=False, token_pattern=r"(?u)\b\w\w+\b")
