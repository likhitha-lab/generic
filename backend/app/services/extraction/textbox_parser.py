"""Text-box extraction for DOCX.

A text box's content lives inside a <w:txbxContent> nested under a
drawing/shape - invisible to Document.paragraphs/Document.tables entirely
(proven by direct construction-and-extraction test). Scans the body and
every unique header/footer element (a text box can be anchored in any of
them), so nothing is missed regardless of where it's anchored.
"""
from docx import Document
from docx.oxml.ns import qn

from app.services.extraction.xml_parser import walk_block_container


def extract_textboxes(doc: Document) -> tuple[list[str], int]:
    """Every text box's content, wherever it's anchored (body, header, or
    footer). Returns (chunks, textbox_count). A text box containing its
    own table is handled too, since walk_block_container recurses into any
    <w:tbl> it finds inside the text box's content.
    """
    chunks: list[str] = []
    textbox_count = 0
    roots = [doc.element.body]
    seen_header_footer_ids: set[int] = set()
    for section in doc.sections:
        for hf_element in (section.header._element, section.footer._element):
            if id(hf_element) not in seen_header_footer_ids:
                seen_header_footer_ids.add(id(hf_element))
                roots.append(hf_element)

    for root in roots:
        for txbx_content in root.iter(qn("w:txbxContent")):
            textbox_count += 1
            txbx_chunks, _ = walk_block_container(txbx_content)
            chunks.extend(txbx_chunks)
    return chunks, textbox_count
