"""Content normalisation, hashing and shingling.

Used for idempotent ingestion (exact identity) and for near-duplicate detection
(approximate identity). Both are deterministic and cheap; neither involves a model.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9 ]+")

#: Length of the word n-grams used for near-duplicate comparison. Five is long enough that
#: shared boilerplate does not create false matches and short enough to survive light
#: editing of a syndicated story.
SHINGLE_SIZE = 5


def normalise_text(text: str) -> str:
    """Canonical form used for hashing: NFKC, lowercase, punctuation-free, single-spaced."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _NON_WORD_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def content_hash(text: str) -> str:
    """SHA-256 of the normalised text."""
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def shingles(text: str, size: int = SHINGLE_SIZE) -> frozenset[str]:
    """Word n-grams of the normalised text."""
    words = normalise_text(text).split()
    if len(words) < size:
        return frozenset([" ".join(words)]) if words else frozenset()
    return frozenset(" ".join(words[i : i + size]) for i in range(len(words) - size + 1))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard similarity of two shingle sets. 0.0 when either side is empty."""
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    if not intersection:
        return 0.0
    return intersection / len(left | right)
