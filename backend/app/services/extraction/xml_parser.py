"""Shared low-level WordprocessingML primitives used by docx_parser.py,
table_parser.py, and textbox_parser.py.

A "block container" in WordprocessingML - the document body, a table cell
(<w:tc>), a header/footer (<w:hdr>/<w:ftr>), or a text box's content
(<w:txbxContent>) - all share the same content model: a sequence of <w:p>
(paragraph) and <w:tbl> (table) children, optionally wrapped in a content-
control <w:sdt>. walk_block_container() is the one function that
understands that shape, so a table nested inside a cell (to any depth) is
reached by simply recursing into that cell as a container again, rather
than needing separate logic per nesting level or per caller.

These names have no leading underscore (unlike most module-private helpers
in this codebase) because they're intentionally shared across sibling
modules in this package - external code should still never import them
directly (see the package __init__.py for what's actually public).
"""
from docx.oxml.ns import qn


def run_text(run) -> str:
    """Text of one <w:r>, in document order, translating <w:tab>/<w:br>/
    <w:cr> to actual tab/newline characters the way python-docx's own
    Run.text does - skipping them (rather than just reading <w:t>) would
    silently concatenate two words that were only separated by a tab stop
    (e.g. a company name and a right-aligned date) into one run-on word."""
    parts: list[str] = []
    for child in run:
        if child.tag == qn("w:t"):
            parts.append(child.text or "")
        elif child.tag == qn("w:tab"):
            parts.append("\t")
        elif child.tag in (qn("w:br"), qn("w:cr")):
            parts.append("\n")
    return "".join(parts)


def paragraph_text(paragraph) -> str:
    """Text of one <w:p>, in document order, including runs wrapped in a
    <w:hyperlink> (a LinkedIn/portfolio URL rendered as a clickable link is
    wrapped this way) and a paragraph-level content control <w:sdt> - both
    invisible to python-docx's own Paragraph.text, which only looks at
    direct <w:r> children."""
    parts: list[str] = []
    for child in paragraph:
        if child.tag == qn("w:r"):
            parts.append(run_text(child))
        elif child.tag == qn("w:hyperlink"):
            for run in child.findall(qn("w:r")):
                parts.append(run_text(run))
        elif child.tag == qn("w:sdt"):
            sdt_content = child.find(qn("w:sdtContent"))
            if sdt_content is not None:
                for run in sdt_content.findall(qn("w:r")):
                    parts.append(run_text(run))
    return "".join(parts)


def walk_block_container(container) -> tuple[list[str], int]:
    """Recursively extracts every paragraph's text from `container` (and
    from every table nested inside it, to any depth), returning
    (text_chunks, nested_table_count). Every <w:tbl> this function finds
    counts as "nested" - callers only ever hand it a container that's
    already one level below the top (a cell, a text box, a header/footer),
    so any table it discovers is by definition nested inside that.
    """
    chunks: list[str] = []
    nested_table_count = 0

    def walk(node) -> None:
        nonlocal nested_table_count
        for child in node:
            if child.tag == qn("w:p"):
                text = paragraph_text(child).strip()
                if text:
                    chunks.append(text)
            elif child.tag == qn("w:tbl"):
                nested_table_count += 1
                for row in child.findall(qn("w:tr")):
                    for cell in row.findall(qn("w:tc")):
                        walk(cell)
            elif child.tag == qn("w:sdt"):
                sdt_content = child.find(qn("w:sdtContent"))
                if sdt_content is not None:
                    walk(sdt_content)

    walk(container)
    return chunks, nested_table_count
