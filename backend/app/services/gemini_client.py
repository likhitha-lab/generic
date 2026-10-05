"""Thin wrapper around the Gemini SDK.

Ported from the original working backend's `call_gemini()` — that logic was
solid, it just needed to stop living inline in a 330-line main.py and stop
depending on a module-level client created at import time (which crashed the
whole app on import if GEMINI_API_KEY was missing, even for routes that don't
need it, e.g. /health).

Raises typed exceptions rather than HTTPException directly - the extraction
pipeline (app/services/extraction_pipeline.py) needs to catch a truncated
response specifically and retry with a smaller prompt instead of failing the
whole request, which it can't do if this module already turned that into a
terminal HTTP error. Routers/services that just want "fail with a sensible
status code" can still do `except GeminiError as exc: raise HTTPException(...)`
at their own boundary.
"""
import json
import logging
import re
import traceback
from functools import lru_cache

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from json_repair import repair_json

from app.core.config import settings
from app.services.pii_detector import detect_pii_in_text
from app.services.pii_masker import build_mapping, mask_text, unmask_structured_verified

logger = logging.getLogger(__name__)


# =============================================================================
# TEMPORARY DEBUG LOGGING - added to investigate a real "502 Gemini is
# currently unavailable" in production validation, where the generic 502
# message (see resume_service._gemini_error_to_http) was hiding the actual
# underlying exception. REMOVE this whole block (and its two call sites
# below, each bounded by a matching "END TEMPORARY DEBUG LOGGING" comment)
# once the root cause is identified and fixed - it is not part of normal
# operation and should not stay in the codebase long-term.
def _classify_gemini_failure(exc: Exception) -> str:
    """Best-effort classification of a raw Gemini call failure for
    diagnostic logging ONLY - never changes control flow or the exception
    type raised. Distinguishes the categories a generic 502 currently
    hides: missing/invalid/expired API key, quota exceeded, model
    unavailable, SSL certificate issue, proxy/firewall, DNS issue,
    authentication failure - vs. some other, unclassified failure."""
    name = type(exc).__name__
    message = str(exc)
    lowered = message.lower()

    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        status = (getattr(exc, "status", None) or "").upper()
        if code == 401 or status == "UNAUTHENTICATED":
            return f"AUTHENTICATION FAILURE - missing/invalid API key (code={code}, status={status})"
        if code == 403 or status == "PERMISSION_DENIED":
            return f"INVALID/UNAUTHORIZED API KEY - permission denied (code={code}, status={status})"
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return f"QUOTA EXCEEDED / RATE LIMITED (code={code}, status={status})"
        if code == 404 or status == "NOT_FOUND":
            return f"MODEL UNAVAILABLE - not found (code={code}, status={status})"
        return f"Gemini API error - code={code}, status={status}, message={getattr(exc, 'message', message)!r}"
    if "sslcertverificationerror" in name.lower() or "certificate_verify_failed" in lowered:
        return "SSL CERTIFICATE ISSUE - certificate verification failed"
    if "proxy" in lowered:
        return "PROXY/FIREWALL ISSUE"
    if any(s in lowered for s in ("getaddrinfo", "name or service not known", "nodename nor servname")):
        return "DNS RESOLUTION ISSUE"
    if "connect" in lowered or "connectionerror" in name.lower():
        return "NETWORK CONNECTIVITY ISSUE (connection refused/reset/unreachable)"
    return f"UNCLASSIFIED - {name}: {message[:300]}"
# =============================================================================


class GeminiError(Exception):
    """Base for every Gemini-call failure."""


class GeminiUnavailableError(GeminiError):
    """Network/auth/SDK failure - retrying with a smaller prompt won't help."""


class GeminiTimeoutError(GeminiError):
    """The request took too long - distinct from "unavailable" so callers/
    routers can return 504 instead of a generic 502."""


class GeminiTruncatedError(GeminiError):
    """Hit max_output_tokens before finishing - the one failure mode the
    extraction pipeline is expected to recover from itself (split the prompt
    and retry), not just report."""


class GeminiInvalidResponseError(GeminiError):
    """Empty response or response text that isn't valid JSON."""


@lru_cache
def _get_client() -> genai.Client:
    # --- TEMPORARY DEBUG LOGGING (see block above - remove together) ---
    logger.warning(
        "[GEMINI DEBUG] API key detected: %s | Model: %s | Endpoint: %s",
        bool(settings.GEMINI_API_KEY), settings.GEMINI_MODEL,
        "https://generativelanguage.googleapis.com (default google-genai SDK endpoint, unless "
        "GOOGLE_GENAI_USE_VERTEXAI/a Vertex AI project override is configured)",
    )
    # --- END TEMPORARY DEBUG LOGGING ---
    if not settings.GEMINI_API_KEY:
        raise GeminiUnavailableError(
            "GEMINI_API_KEY is not set. Configure it in backend/.env (local) "
            "or as a Cloud Run secret (production)."
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def _is_timeout(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "deadline" in name


# Appended to the prompt for the one automatic retry below, only when
# Gemini's response couldn't be parsed as JSON even after _try_repair_json.
# Kept short and separate from the actual prompt content - this isn't a
# request to redo the task, just a pointed correction about output format.
_INVALID_JSON_RETRY_SUFFIX = """

IMPORTANT: Your previous response for this exact request was not valid JSON. Return STRICT VALID
JSON only - no markdown code fences, no explanation, no text before or after the JSON object."""


def _try_repair_json(raw: str) -> dict | None:
    """Attempts to recover a dict from a malformed Gemini response using
    json_repair - handles the common LLM JSON mistakes (trailing commas,
    missing closing braces/brackets, markdown fences, smart/curly quotes,
    duplicated commas, unescaped characters) far more robustly than a
    hand-rolled set of regex fixes would. Returns None (never raises) if
    repair doesn't produce a usable dict, so the caller can fall back to
    its own error handling instead of this masking a real failure."""
    try:
        repaired = repair_json(raw, return_objects=True)
    except Exception as exc:
        logger.error("JSON repair itself raised an exception (%s) - giving up on this response.", exc)
        return None
    if isinstance(repaired, dict):
        return repaired
    logger.error(
        "JSON repair produced a %s, not a JSON object - giving up on this response.",
        type(repaired).__name__,
    )
    return None


def _send_and_parse(prompt: str) -> dict:
    """Sends `prompt` to Gemini once and returns the parsed JSON object.

    Raises GeminiUnavailableError, GeminiTimeoutError, GeminiTruncatedError,
    or GeminiInvalidResponseError. GeminiInvalidResponseError specifically
    means the response text could not be parsed as JSON even after
    json_repair's automatic repair attempt.
    """
    try:
        response = _get_client().models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=settings.GEMINI_MAX_OUTPUT_TOKENS),
        )
    except GeminiUnavailableError:
        raise
    except Exception as exc:  # network/SDK errors
        # --- TEMPORARY DEBUG LOGGING (see block above - remove together) ---
        logger.error(
            "[GEMINI DEBUG] Exception type: %s | Classification: %s\n"
            "[GEMINI DEBUG] Full stack trace:\n%s",
            type(exc).__name__, _classify_gemini_failure(exc), traceback.format_exc(),
        )
        # --- END TEMPORARY DEBUG LOGGING ---
        if _is_timeout(exc):
            raise GeminiTimeoutError(f"Gemini request timed out: {exc}") from exc
        raise GeminiUnavailableError(f"Gemini request failed: {exc}") from exc

    # Checked before touching response.text: a response cut off mid-generation
    # (finish_reason MAX_TOKENS) almost always leaves invalid/incomplete JSON.
    # Raising this as its own type - not folded into GeminiInvalidResponseError
    # - is what lets the extraction pipeline specifically retry-with-a-
    # smaller-prompt on this one failure mode instead of giving up.
    candidates = getattr(response, "candidates", None) or []
    if candidates and candidates[0].finish_reason == types.FinishReason.MAX_TOKENS:
        raise GeminiTruncatedError("Gemini's response was truncated (hit max_output_tokens)")

    text = response.text
    if not text:
        raise GeminiInvalidResponseError("Gemini returned an empty response")

    cleaned = re.sub(r"```json|```", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Logged BEFORE any repair attempt/raise - this is the one place a
        # malformed response's full content is visible at all; without it,
        # "Gemini returned malformed JSON: Expecting ',' delimiter" is the
        # only information anyone ever sees, with no way to tell what Gemini
        # actually produced.
        logger.error("Gemini returned malformed JSON (%s) - raw response follows:\n%s", exc, cleaned)
        repaired = _try_repair_json(cleaned)
        if repaired is not None:
            logger.info("JSON Repair Applied: true - recovered a usable response without re-calling Gemini.")
            return repaired
        logger.info("JSON Repair Applied: false - repair did not produce a usable JSON object.")
        raise GeminiInvalidResponseError(f"Gemini returned malformed JSON: {exc}") from exc


def call_gemini(prompt: str) -> dict:
    """Send a prompt to Gemini and parse the JSON object it returns.

    If the response is malformed JSON and json_repair can't fix it, this
    retries ONCE with an explicit "return valid JSON only" correction
    appended to the same prompt before giving up - most malformed-JSON
    responses are a one-off formatting slip, not a systematic problem with
    the prompt itself, and a single retry recovers a real majority of them
    without the caller needing its own retry logic for this specific case.

    PII Masking (see reference_docs/PII_PIPELINE_INTEGRATION_REPORT.md):
    this is the ONE function every Gemini-calling module in the codebase
    already goes through (extraction_pipeline, resume_optimizer,
    experience_refiner, summary_generator, section_recovery,
    resume_service's manual-generate path) - wrapping masking here, rather
    than in each of those six callers, protects all of them uniformly with
    no change to any of their own prompt-building or business logic. The
    mask/unmask round-trip is entirely self-contained within one call: a
    fresh mapping is built from THIS prompt's own content, used to mask
    before sending and unmask the response before returning - never shared
    or persisted across separate calls, so there is no cross-call state to
    keep consistent. Unmasking uses unmask_structured_verified (see
    reference_docs/PII_UNMASKING_REPORT.md) rather than plain
    unmask_structured - same restore behavior and same returned content
    either way (`verified["structured"]` IS unmask_structured's own
    result, nothing more), but this also logs a warning if Gemini's
    response ever contains a malformed/unrecognized placeholder instead of
    silently letting it survive into the final resume unnoticed.

    Raises GeminiUnavailableError, GeminiTimeoutError, GeminiTruncatedError,
    or GeminiInvalidResponseError - never a bare Exception, never HTTPException.
    """
    pii_report = detect_pii_in_text(prompt)
    mapping = build_mapping(pii_report)
    masked_prompt = mask_text(prompt, mapping)

    try:
        result = _send_and_parse(masked_prompt)
        logger.info("Retry Count: 0")
    except GeminiInvalidResponseError:
        logger.warning("Retry Count: 1 - retrying with an explicit 'return valid JSON only' correction.")
        result = _send_and_parse(masked_prompt + _INVALID_JSON_RETRY_SUFFIX)

    return unmask_structured_verified(result, mapping)["structured"]
