from __future__ import annotations

from dataclasses import dataclass
import re

from .tokenizer import tokenize


@dataclass(frozen=True)
class LocatedToken:
    surface: str
    page: str = ""
    paragraph: str = ""
    line: str = ""
    source_line: int = 0
    word_in_paragraph: int = 0

    @property
    def legacy_location(self) -> str:
        """Three-part legacy-compatible location slot: page:paragraph:line.

        Empty modern fields are written as zero so unmarked text retains the
        familiar legacy ``0:0:0`` location value.
        """
        return f"{self.page or '0'}:{self.paragraph or '0'}:{self.line or '0'}"


# Metadata is intentionally restricted to bracketed markers so ordinary Arabic
# prose can never be mistaken for page/paragraph instructions. Values may be
# numeric, folio-like (12r/12v), Roman, or short scholarly labels.
_MARKER = re.compile(
    r"\[(?P<label>page|p\.?|folio|fol\.?|paragraph|para|par\.?|line|l\.?|"
    r"صفحة|ورقة|فقرة|سطر|loc|location)\s*(?:[:=]\s*|\s+)(?P<value>[^\]\r\n]+?)\]",
    re.IGNORECASE,
)
_COMPACT_LOC = re.compile(r"\[(?P<page>\d[^:\]\r\n]*):(?P<paragraph>[^:\]\r\n]+):(?P<line>[^\]\r\n]+)\]")

_PAGE_LABELS = {"page", "p", "p.", "folio", "fol", "fol.", "صفحة", "ورقة"}
_PAR_LABELS = {"paragraph", "para", "par", "par.", "فقرة"}
_LINE_LABELS = {"line", "l", "l.", "سطر"}
_LOC_LABELS = {"loc", "location"}


def _clean_value(value: str) -> str:
    return " ".join((value or "").strip().split())


def _parse_location_value(value: str) -> tuple[str, str, str] | None:
    parts = [_clean_value(x) for x in (value or "").split(":")]
    if len(parts) != 3 or not all(parts):
        return None
    return tuple(parts)  # type: ignore[return-value]


def tokenize_with_locations(text: str) -> list[LocatedToken]:
    """Tokenize Arabic while carrying explicit scholarly location metadata.

    Supported marker forms include, for example::

        [page 124]          [paragraph 3]          [line 7]
        [page=12v]          [فقرة 4]               [loc 12v:4:7]
        [12v:4:7]

    Markers may occupy their own line or appear immediately before prose. They
    are removed before Arabic tokenization. Page changes do not silently reset
    paragraph numbering because editions differ in how paragraphs continue
    across pages. Paragraph markers reset ``word_in_paragraph``.
    """
    current_page = ""
    current_paragraph = ""
    current_line = ""
    word_in_paragraph = 0
    out: list[LocatedToken] = []

    # splitlines() drops the newline but preserves a stable 1-based source line
    # number, useful for diagnostics even when no explicit [line] marker exists.
    for source_line_no, raw_line in enumerate((text or "").splitlines() or [text or ""], 1):
        line = raw_line

        # First consume labelled markers, in textual order.
        pieces: list[tuple[int, int, str, str]] = []
        for m in _MARKER.finditer(line):
            pieces.append((m.start(), m.end(), m.group("label"), m.group("value")))
        # A compact [page:paragraph:line] marker is accepted as a direct bridge
        # to the legacy three-part location slot.
        compact_matches = list(_COMPACT_LOC.finditer(line))
        for m in compact_matches:
            # Avoid double-reading values already inside [loc ...].
            if any(a <= m.start() and m.end() <= b for a, b, _, _ in pieces):
                continue
            pieces.append((m.start(), m.end(), "__compact__", f"{m.group('page')}:{m.group('paragraph')}:{m.group('line')}"))
        pieces.sort(key=lambda x: x[0])

        remove_spans: list[tuple[int, int]] = []
        for start, end, label, value in pieces:
            remove_spans.append((start, end))
            label_norm = label.lower().strip()
            value = _clean_value(value)
            if label_norm == "__compact__" or label_norm in _LOC_LABELS:
                loc = _parse_location_value(value)
                if loc:
                    current_page, current_paragraph, current_line = loc
                    word_in_paragraph = 0
                continue
            if label_norm in _PAGE_LABELS:
                current_page = value
                current_line = ""
            elif label_norm in _PAR_LABELS:
                current_paragraph = value
                current_line = ""
                word_in_paragraph = 0
            elif label_norm in _LINE_LABELS:
                current_line = value

        if remove_spans:
            chars = list(line)
            for a, b in remove_spans:
                for j in range(a, b):
                    chars[j] = " "
            line = "".join(chars)

        for surface in tokenize(line):
            word_in_paragraph += 1
            out.append(
                LocatedToken(
                    surface=surface,
                    page=current_page,
                    paragraph=current_paragraph,
                    line=current_line,
                    source_line=source_line_no,
                    word_in_paragraph=word_in_paragraph,
                )
            )
    return out
