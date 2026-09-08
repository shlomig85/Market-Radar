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
_DROP_BLOCKS = re.compile(
    r"<(script|style|ix:header|ix:hidden)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
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
        if len(line) > MAX_SENTENCE_CHARS:
            continue
        letters = sum(c.isalpha() for c in line)
        if letters < 20:
            continue
        # A line more than half digits and punctuation is a table row.
        if letters / max(len(line), 1) < 0.5:
            continue
        kept.append(line)
    return "\n".join(kept)
