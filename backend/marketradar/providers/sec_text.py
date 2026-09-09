"""Filing document text extraction.

EDGAR primary documents are HTML (often XBRL-inline) with heavy markup, hidden XBRL
context blocks, and tables. Evidence extraction needs readable prose, so this strips the
document to text before it reaches the extractor.

Deliberately dependency-free: an HTML parser is a large attack surface for hostile input,
and filings are machine-generated from a narrow set of tools. This handles that shape and
fails safe — worst case it returns less text, never executes anything.
"""

from __future__ import annotations

import html
import re

#: Blocks whose contents are never prose. Inline XBRL hides machine-readable facts inside
#: <ix:header> which would otherwise dominate the extracted text.
#: Page furniture. Filings do not have navigation or affiliate disclosures; web articles are
#: mostly furniture by volume, and a live run over real feeds turned that furniture into
#: "subjects" — "may earn compensation", "affiliate link policy", "california privacy right".
#: Dropping the elements that carry it keeps it out of the corpus in the first place.
_DROP_BLOCKS = re.compile(
    r"<(script|style|ix:header|ix:hidden|nav|footer|aside|form|noscript|svg|figcaption)"
    r"\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)

#: Containers whose class or id marks them as chrome rather than article body. Matched on
#: the attribute text because sites name these consistently even when the tag is a bare div.
_CHROME_CONTAINERS = re.compile(
    r"<(div|section|ul|ol|p)\b[^>]*(?:class|id)\s*=\s*[\"\'][^\"\']*"
    r"(?:nav|menu|footer|header|sidebar|promo|related|recirc|newsletter|subscribe"
    r"|social|share|comment|advert|sponsor|affiliate|cookie|consent|privacy|legal"
    r"|copyright|disclaimer|breadcrumb|pagination|tags?|byline-meta)"
    r"[^\"\']*[\"\'][^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
#: Tags that imply a line break when removed, so sentences do not run together.
_BLOCK_LEVEL = re.compile(
    r"</?(p|div|br|tr|td|th|h[1-6]|li|table|section|article)\b[^>]*>",
    re.IGNORECASE,
)
_ANY_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")

#: Above this, a "sentence" is almost certainly a run-on table row rather than prose.
MAX_SENTENCE_CHARS = 1200


def looks_like_html(raw: str) -> bool:
    head = raw[:2048].lower()
    return "<html" in head or "<!doctype html" in head or "<ix:" in head or "<div" in head


def extract_text(raw: str) -> str:
    """Reduce a filing document to readable text.

    Returns plain text unchanged, so a .txt filing passes through untouched.
    """
    if not raw:
        return ""
    if not looks_like_html(raw):
        return _normalise(raw)

    text = _DROP_BLOCKS.sub(" ", raw)
    # Applied twice: chrome containers nest, and one pass leaves the outer wrapper's
    # siblings behind. Two passes is enough in practice and is bounded, unlike looping to a
    # fixed point on adversarial markup.
    text = _CHROME_CONTAINERS.sub(" ", text)
    text = _CHROME_CONTAINERS.sub(" ", text)
    text = _COMMENTS.sub(" ", text)
    text = _BLOCK_LEVEL.sub("\n", text)
    text = _ANY_TAG.sub(" ", text)
    text = html.unescape(text)
    return _normalise(text)


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    return _BLANK_LINES.sub("\n\n", text).strip()


def prose_only(text: str) -> str:
    """Drop lines that are numeric tables rather than narrative.

    Financial statements carry the numbers; the *narrative* sections carry the management
    language this system reads for demand, capacity and guidance signals. Keeping tables
    would swamp the extractor with digits it cannot interpret.
    """
    kept: list[str] = []
    for line in text.split("\n"):
        letters = sum(c.isalpha() for c in line)
        if letters < 20:
            continue
        # A line more than half digits and punctuation is a table row.
        if letters / max(len(line), 1) < 0.5:
            continue
        # Long lines are suspicious but not automatically junk. Filings are hard-wrapped, so
        # a very long line there is usually a flattened table; web articles are NOT wrapped,
        # so a whole paragraph arrives as one line and dropping it on length alone discarded
        # entire article bodies. Sentence punctuation is what separates the two: prose is
        # punctuated at a human rate, a flattened table is not.
        if len(line) > MAX_SENTENCE_CHARS and not _is_punctuated_prose(line):
            continue
        kept.append(line)
    return "\n".join(kept)


#: Roughly one sentence terminator per this many characters. Ordinary prose runs far denser
#: (a 25-word sentence is ~150 chars); a flattened table has almost none.
MAX_CHARS_PER_SENTENCE_END = 400


def _is_punctuated_prose(line: str) -> bool:
    terminators = sum(line.count(mark) for mark in ".!?")
    return terminators >= len(line) / MAX_CHARS_PER_SENTENCE_END
