from __future__ import annotations

"""Small dependency-free XLSX exporter for ALFADEL core.

The application deliberately avoids requiring Excel/Office or third-party Python
packages.  It writes a standards-compliant Office Open XML workbook with two
worksheets: Analysis and Index.
"""

from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape
import math
import re

_INVALID_XML = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

ANALYSIS_COLUMNS = [
    "token_index", "legacy_location", "page", "paragraph", "line",
    "source_line", "word_in_paragraph", "surface", "selection",
    "automatic_decision_reason", "lemma", "pos", "broad_pos", "root",
    "base_score", "corpus_rank_support", "historical_rules",
]
INDEX_COLUMNS = [
    "page", "paragraph", "line", "word_in_paragraph", "token_index",
    "legacy_location", "surface", "lemma", "pos", "broad_pos", "root",
    "selection",
]


def _clean(value) -> str:
    if value is None:
        return ""
    return _INVALID_XML.sub("", str(value))


def _candidate(token: dict) -> dict:
    candidates = token.get("candidates") or []
    selected = token.get("selected")
    if isinstance(selected, bool):
        selected = None
    if isinstance(selected, int) and 0 <= selected < len(candidates):
        return candidates[selected] or {}
    return {}


def analysis_row(token: dict) -> list:
    c = _candidate(token)
    return [
        token.get("index", ""), token.get("legacy_location") or "0:0:0",
        token.get("page") or "", token.get("paragraph") or "", token.get("line") or "",
        token.get("source_line") or "", token.get("word_in_paragraph") or "",
        token.get("surface") or "", "manual" if token.get("manual_selected") else "automatic",
        token.get("decision_reason") or "", c.get("lemma") or "", c.get("pos") or "",
        c.get("broad_pos") or "", c.get("root") or "", c.get("score", ""),
        c.get("rank_support", ""), "; ".join(c.get("historical_rules") or []),
    ]


def index_row(token: dict) -> list:
    c = _candidate(token)
    return [
        token.get("page") or "", token.get("paragraph") or "", token.get("line") or "",
        token.get("word_in_paragraph") or "", token.get("index", ""),
        token.get("legacy_location") or "0:0:0", token.get("surface") or "",
        c.get("lemma") or "", c.get("pos") or "", c.get("broad_pos") or "",
        c.get("root") or "", "manual" if token.get("manual_selected") else "automatic",
    ]


def _col_name(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref: str, value, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{style_attr}><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            value = ""
        else:
            return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = escape(_clean(value))
    preserve = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t{preserve}>{text}</t></is></c>'


def _sheet_xml(headers: list[str], rows: list[list], widths: list[float]) -> bytes:
    out = BytesIO()
    write = lambda s: out.write(s.encode("utf-8"))
    last_col = _col_name(len(headers))
    last_row = len(rows) + 1
    write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    write('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">')
    write('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    write('<sheetFormatPr defaultRowHeight="15"/>')
    write('<cols>')
    for i, width in enumerate(widths, 1):
        write(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>')
    write('</cols><sheetData>')
    write('<row r="1">')
    for col, value in enumerate(headers, 1):
        write(_cell(f'{_col_name(col)}1', value, 1))
    write('</row>')
    for r_idx, row in enumerate(rows, 2):
        write(f'<row r="{r_idx}">')
        for c_idx, value in enumerate(row, 1):
            # Arabic/text-heavy body columns receive wrap-text style.
            style = 2 if headers[c_idx - 1] in {"surface", "lemma", "root", "automatic_decision_reason", "historical_rules"} else 0
            write(_cell(f'{_col_name(c_idx)}{r_idx}', value, style))
        write('</row>')
    write('</sheetData>')
    write(f'<autoFilter ref="A1:{last_col}{last_row}"/>')
    write('</worksheet>')
    return out.getvalue()


def build_alfadel_workbook(tokens: list[dict]) -> bytes:
    """Return an XLSX workbook containing Analysis and Index sheets."""
    tokens = tokens or []
    analysis_rows = [analysis_row(t) for t in tokens]
    index_rows = [index_row(t) for t in tokens]

    analysis_widths = [12, 18, 11, 12, 9, 11, 18, 24, 12, 30, 24, 16, 16, 18, 12, 18, 28]
    index_widths = [11, 12, 9, 18, 12, 18, 24, 24, 16, 16, 18, 12]

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Analysis" sheetId="1" r:id="rId1"/><sheet name="Index" sheetId="2" r:id="rId2"/></sheets>
</workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF3D5AF1"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
    core = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>ALFADEL core Analysis Export</dc:title><dc:creator>ALFADEL core</dc:creator></cp:coreProperties>'''
    app = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>ALFADEL core</Application></Properties>'''

    buf = BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", _sheet_xml(ANALYSIS_COLUMNS, analysis_rows, analysis_widths))
        z.writestr("xl/worksheets/sheet2.xml", _sheet_xml(INDEX_COLUMNS, index_rows, index_widths))
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
    return buf.getvalue()
