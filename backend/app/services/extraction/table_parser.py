"""Table extraction for DOCX - top-level tables and, recursively, any
table nested inside a cell.

Document.tables only ever returns TOP-LEVEL tables (direct children of the
body) and _Cell.text only ever returns a cell's own direct paragraphs - a
table nested inside a cell (the standard way a two-column resume template
lays out a sidebar/main-column split) is invisible to both, proven by
direct construction-and-extraction test: content placed in such a nested
table was silently and completely dropped before this rewrite.
"""
from docx import Document
from docx.oxml.ns import qn

from app.services.extraction.xml_parser import walk_block_container


def extract_tables_recursive(doc: Document) -> tuple[list[str], int]:
    """Every top-level body table's cell content, recursing into any table
    nested inside a cell to any depth. Returns (chunks, nested_table_count).
    """
    chunks: list[str] = []
    nested_table_count = 0
    for table in doc.element.body.findall(qn("w:tbl")):
        for row in table.findall(qn("w:tr")):
            for cell in row.findall(qn("w:tc")):
                cell_chunks, cell_nested = walk_block_container(cell)
                chunks.extend(cell_chunks)
                nested_table_count += cell_nested
    return chunks, nested_table_count
