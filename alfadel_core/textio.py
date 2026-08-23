from __future__ import annotations

from dataclasses import dataclass
import codecs
from io import BytesIO
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class DecodedText:
    text: str
    encoding: str
    kind: str = "TXT"


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _looks_like_utf16_without_bom(raw: bytes) -> str | None:
    """Return a likely UTF-16 codec for BOM-less Arabic text, else None.

    Arabic letters are mostly U+06xx, so a NUL-byte heuristic alone is not
    sufficient. We decode both byte orders and score the result as plausible
    Arabic/whitespace/punctuation text. This is intentionally conservative and
    is only attempted after UTF-8 decoding has failed.
    """
    if len(raw) < 4 or len(raw) % 2:
        return None

    def score(codec: str) -> float:
        try:
            text = raw.decode(codec)
        except UnicodeDecodeError:
            return -1.0
        if not text:
            return -1.0
        arabic = 0
        sensible = 0
        bad_control = 0
        for ch in text:
            cp = ord(ch)
            is_arabic = (
                0x0600 <= cp <= 0x06FF
                or 0x0750 <= cp <= 0x077F
                or 0x08A0 <= cp <= 0x08FF
                or 0xFB50 <= cp <= 0xFDFF
                or 0xFE70 <= cp <= 0xFEFF
            )
            if is_arabic:
                arabic += 1
                sensible += 1
            elif ch.isspace() or ch.isdigit() or ch.isalpha() or ch in '.,;:!?،؛؟()[]{}«»"\'ـ-–—/\\':
                sensible += 1
            elif cp < 32 and ch not in '\n\r\t':
                bad_control += 1
        n = len(text)
        if bad_control:
            return -1.0
        arabic_ratio = arabic / n
        sensible_ratio = sensible / n
        # legacy Arabic input is Arabic, so require a material Arabic signal. This keeps
        # arbitrary Windows-1256 bytes from being misclassified as UTF-16.
        if arabic_ratio < 0.20 or sensible_ratio < 0.85:
            return -1.0
        return arabic_ratio * 2.0 + sensible_ratio

    le = score('utf-16-le')
    be = score('utf-16-be')
    best = max(le, be)
    if best < 0:
        return None
    if abs(le - be) < 0.05:
        return None
    return 'utf-16-le' if le > be else 'utf-16-be'


def decode_text_bytes(raw: bytes) -> DecodedText:
    """Decode an source lexical system plain-text input without silently damaging Arabic.

    Preferred modern input is UTF-8. BOM-marked UTF-16 is accepted because it
    is common in text exported by Windows editors. A conservative heuristic
    also recognizes BOM-less UTF-16. When the bytes are not valid Unicode
    UTF-8/UTF-16, ALFADEL falls back to Windows-1256 so legacy Arabic text files can
    be opened directly.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw must be bytes")
    raw = bytes(raw)

    if raw.startswith(codecs.BOM_UTF8):
        return DecodedText(raw.decode("utf-8-sig"), "UTF-8 with BOM", "TXT")
    if raw.startswith(codecs.BOM_UTF16_LE):
        return DecodedText(raw[len(codecs.BOM_UTF16_LE):].decode("utf-16-le"), "UTF-16 LE", "TXT")
    if raw.startswith(codecs.BOM_UTF16_BE):
        return DecodedText(raw[len(codecs.BOM_UTF16_BE):].decode("utf-16-be"), "UTF-16 BE", "TXT")

    # BOM-less UTF-16 Arabic may consist entirely of byte values that also form
    # technically valid UTF-8 control/ASCII sequences, so test the conservative
    # Arabic UTF-16 heuristic before the ordinary UTF-8 attempt.
    guessed_utf16 = _looks_like_utf16_without_bom(raw)
    if guessed_utf16:
        try:
            label = "UTF-16 LE (no BOM)" if guessed_utf16.endswith("le") else "UTF-16 BE (no BOM)"
            return DecodedText(raw.decode(guessed_utf16), label, "TXT")
        except UnicodeDecodeError:
            pass

    try:
        return DecodedText(raw.decode("utf-8"), "UTF-8", "TXT")
    except UnicodeDecodeError:
        pass

    # Legacy legacy Arabic text is commonly Windows-1256. Decode strictly so
    # unsupported bytes become an explicit import error rather than replacement
    # characters that would alter the surface forms sent to the analyzer.
    try:
        return DecodedText(raw.decode("cp1256"), "Windows-1256", "TXT")
    except UnicodeDecodeError as exc:
        raise UnicodeError(
            "The text file is neither valid UTF-8/UTF-16 nor Windows-1256. "
            "Save it as UTF-8 and try again."
        ) from exc


def _paragraph_text(p: ET.Element) -> str:
    out: list[str] = []
    for el in p.iter():
        if el.tag == _W + "t":
            out.append(el.text or "")
        elif el.tag == _W + "tab":
            out.append("\t")
        elif el.tag in {_W + "br", _W + "cr"}:
            out.append("\n")
        elif el.tag == _W + "noBreakHyphen":
            out.append("-")
    return "".join(out)


def _table_text(tbl: ET.Element) -> str:
    rows: list[str] = []
    for tr in tbl.findall(f"./{_W}tr"):
        cells: list[str] = []
        for tc in tr.findall(f"./{_W}tc"):
            parts: list[str] = []
            # Include paragraphs directly in the cell and paragraphs inside
            # content controls. Nested tables are flattened row by row.
            for child in list(tc):
                if child.tag == _W + "p":
                    parts.append(_paragraph_text(child))
                elif child.tag == _W + "tbl":
                    parts.append(_table_text(child))
                elif child.tag == _W + "sdt":
                    content = child.find(f".//{_W}sdtContent")
                    if content is not None:
                        for sub in list(content):
                            if sub.tag == _W + "p":
                                parts.append(_paragraph_text(sub))
                            elif sub.tag == _W + "tbl":
                                parts.append(_table_text(sub))
            cells.append("\n".join(x for x in parts if x != ""))
        rows.append("\t".join(cells))
    return "\n".join(rows)


def _body_blocks(parent: ET.Element):
    for child in list(parent):
        if child.tag == _W + "p":
            yield _paragraph_text(child)
        elif child.tag == _W + "tbl":
            yield _table_text(child)
        elif child.tag == _W + "sdt":
            content = child.find(f".//{_W}sdtContent")
            if content is not None:
                yield from _body_blocks(content)


def extract_docx_text(raw: bytes) -> DecodedText:
    """Extract analyzable body text from a Word .docx using only stdlib OOXML.

    Paragraphs are separated by newlines. Table cells are separated by tabs
    and rows by newlines. Deleted tracked-change text and field instructions are
    not imported because Word stores them outside ordinary ``w:t`` text nodes.
    Headers, footers, comments, footnotes and endnotes are intentionally not
    mixed into the main analysis text.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw must be bytes")
    try:
        with zipfile.ZipFile(BytesIO(bytes(raw))) as zf:
            try:
                xml = zf.read("word/document.xml")
            except KeyError as exc:
                raise ValueError("This file is not a valid Word DOCX document (word/document.xml is missing).") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError("This file is not a valid Word DOCX document.") from exc

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("The Word document XML could not be read.") from exc
    body = root.find(f".//{_W}body")
    if body is None:
        raise ValueError("The Word document does not contain a readable body.")

    blocks = list(_body_blocks(body))
    text = "\n".join(blocks)
    # Word paragraphs can contain manual line breaks; normalize only newline
    # representation, never Arabic spelling or punctuation.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return DecodedText(text, "Word DOCX (OOXML)", "DOCX")


def decode_document_bytes(raw: bytes, filename: str = "input.txt") -> DecodedText:
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        return extract_docx_text(raw)
    if suffix in {".txt", ".text", ""}:
        return decode_text_bytes(raw)
    raise ValueError("Unsupported input format. Choose a .txt or .docx file.")


def read_text_file(path: str | Path) -> DecodedText:
    path = Path(path)
    return decode_text_bytes(path.read_bytes())


def read_document_file(path: str | Path) -> DecodedText:
    path = Path(path)
    return decode_document_bytes(path.read_bytes(), path.name)
