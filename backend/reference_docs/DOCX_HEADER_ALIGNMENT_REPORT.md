# DOCX Header Alignment Report

## What changed

Before this fix, only the Dataflix logo lived in the actual Word Header (`section.header`) — the
candidate's Name (`document.add_heading(...)`) and Contact line (`document.add_paragraph(...)`)
were both added to the document **body**, meaning the body's first visible content was the name,
not "Professional Summary" like the PDF. There was also no divider line in the DOCX at all (the
PDF draws one via `canvas.line(...)` under its header band; DOCX had no equivalent).

`app/services/file_generator.py`'s `_build_docx_with_tier`:
- Name: `document.add_heading(name, level=1)` → `header.add_paragraph(name, style="Heading 1")`.
- Contact: `document.add_paragraph(contact_line)` → `header.add_paragraph(contact_line)` (same
  explicit `_PDF_CONTACT_FONT_SIZE` override, unchanged).
- Divider: new call, `_add_horizontal_rule(header, tier)` — reusing the **existing** helper
  (already used elsewhere for the divider between experience entries) completely unchanged; it
  only ever calls `.add_paragraph()` on whatever object is passed to it, so passing `header`
  instead of `document` needed zero changes to that function.
- Logo: untouched — same paragraph (`header.paragraphs[0]`), same position, same size.

That's the entire change to `file_generator.py`: one line altered (name), one line altered
(contact), one line added (divider), one explanatory comment. No new function, no new module.

## Why this satisfies "same font/size/weight"

Word styles ("Heading 1", "Normal", etc.) are document-level definitions shared by the body and
every header/footer — `_apply_docx_style_tier` (untouched) still sets "Heading 1" to
`_NAME_FONT_SIZE`/bold regardless of which container a paragraph using that style lives in. Moving
the *paragraph* to a different container doesn't change what the *style* resolves to, so the name
and contact line render with byte-for-byte identical typography to before, just now inside the
header band instead of the body.

## Validation (actual output inspected, not assumed)

```
=== HEADER paragraphs ===
0 ''            | style: Header        (logo picture lives in this paragraph's run)
1 'Jane Doe'    | style: Heading 1
2 'jane@example.com   |   +1-555-0100' | style: Normal
3 ''            | style: Normal        (the divider - a border, no visible text)

=== BODY first paragraph ===
'Professional Summary' | style: Heading 2
```

- ✓ Candidate Name in the Header.
- ✓ Contact Information in the Header.
- ✓ Dataflix Logo in the Header (unchanged from the prior logo-only fix).
- ✓ Divider line in the Header.
- ✓ Body begins directly with "Professional Summary", matching the PDF.

## PDF output

Not touched. `build_pdf_bytes`/`_build_pdf_with_tier` were not edited — confirmed by inspecting the
diff scope, only `_build_docx_with_tier` was changed.

## Fallout: 3 pre-existing tests needed a helper fix (not a behavior regression)

Three tests broke on the first run, not because anything they actually check became false, but
because their own text-extraction helpers only ever read `document.paragraphs` (body-only —
`python-docx` structurally excludes header/footer content from that collection; there is no way to
get header text through it). The candidate's name and contact info are still present in the
generated file, exactly as required — just in a part of the DOCX these helpers weren't looking at.

Fixed by extending each helper to also read `document.sections[0].header.paragraphs`:
- `tests/test_file_generator_formatting.py::test_pdf_and_docx_render_the_same_text_content`
- `tests/test_file_generator_visibility.py::_docx_text` (used by 2 tests)
- `tests/test_resumes_visibility_api.py::_download_text` (used by 1 API-level test)

No assertions were weakened or removed — every one still checks for the exact same expected text
appearing in the DOCX; only the *helper's* read path was widened to include the header, which is
where that text now legitimately lives.

## Regression

Full suite: **879 passed**, 10 deselected (live_gemini), 0 failed.

Stop immediately after this header alignment, per instruction.
