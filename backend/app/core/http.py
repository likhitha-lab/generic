"""Shared helper for turning generated file bytes into a proper HTTP response.

Used by all three file-producing routes (/api/generate, /api/convert,
/api/render) so the StreamingResponse + header construction lives in exactly
one place.
"""
import io
import json
import urllib.parse
from typing import Optional

from fastapi.responses import StreamingResponse


def stream_resume_file(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    preview: Optional[dict] = None,
) -> StreamingResponse:
    """Return `file_bytes` as a downloadable attachment.

    If `preview` is given, it's attached as a percent-encoded JSON string in
    the `X-Resume-Preview` header (HTTP headers must be ASCII/Latin-1, so raw
    JSON with names containing accents etc. can't go in a header verbatim).
    The frontend does `decodeURIComponent` + `JSON.parse` to read it back.
    Nothing is written to disk and nothing is retained after this response
    is sent - `file_bytes` only ever exists in this process's memory.
    """
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if preview is not None:
        headers["X-Resume-Preview"] = urllib.parse.quote(json.dumps(preview))

    return StreamingResponse(io.BytesIO(file_bytes), media_type=content_type, headers=headers)
