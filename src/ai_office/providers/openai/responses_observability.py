"""Secret-free diagnostics for completed Responses HTTP responses."""

import json
import re

from ai_office.invocation import (
    ModelInvocationFailureDiagnostics,
    ModelInvocationResponseBodyKind,
)

_HTML_TAG_RE = re.compile(r"<(?:html|head|body|title|script|style)(?:\s|>)")


def extract_openai_responses_content_type(
    headers: tuple[tuple[str, str], ...],
) -> str | None:
    """Return only the first case-insensitive Content-Type header value."""
    for name, value in headers:
        if isinstance(name, str) and name.lower() == "content-type":
            if not isinstance(value, str):
                return None
            normalized = value.strip()
            return normalized or None
    return None


def classify_openai_responses_body(
    body: bytes,
    *,
    content_type: str | None = None,
) -> ModelInvocationResponseBodyKind:
    """Classify response bytes without retaining or returning their contents."""
    if not body:
        return "empty"
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return "non_utf8"

    stripped = decoded.strip()
    try:
        json.loads(stripped, parse_constant=_reject_nonstandard_json_constant)
    except (json.JSONDecodeError, ValueError):
        pass
    else:
        return "json"

    if _looks_like_sse(decoded):
        return "sse"

    media_type = _media_type(content_type)
    lowered = stripped.lower()
    if media_type == "text/html" or lowered.startswith(
        ("<!doctype html", "<html", "<head", "<body")
    ) or _HTML_TAG_RE.search(lowered):
        return "html"
    if _looks_like_json(decoded):
        return "malformed_json"
    return "plaintext"


def build_openai_responses_response_diagnostics(
    status_code: int,
    headers: tuple[tuple[str, str], ...],
    body: bytes,
) -> ModelInvocationFailureDiagnostics:
    """Build the finite metadata allowed to cross the failure boundary."""
    content_type = extract_openai_responses_content_type(headers)
    return ModelInvocationFailureDiagnostics(
        status_code=status_code,
        content_type=content_type,
        body_length=len(body),
        body_kind=classify_openai_responses_body(body, content_type=content_type),
    )


def _media_type(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    return content_type.split(";", 1)[0].strip().lower()


def _looks_like_sse(decoded: str) -> bool:
    normalized = decoded.replace("\r\n", "\n").replace("\r", "\n")
    if "\n\n" not in normalized:
        return False
    for frame in normalized.split("\n\n"):
        lines = frame.split("\n")
        if any(
            line.startswith(("data:", "event:", "id:", "retry:", ":"))
            for line in lines
            if line
        ) and any(line.startswith("data:") for line in lines):
            return True
    return False


def _looks_like_json(decoded: str) -> bool:
    stripped = decoded.lstrip()
    if not stripped:
        return False
    if stripped[0] in "[{\"-0123456789":
        return True
    return any(
        stripped.startswith(token)
        and (len(stripped) == len(token) or not stripped[len(token)].isalnum())
        for token in ("null", "true", "false", "NaN", "Infinity")
    )


def _reject_nonstandard_json_constant(constant: str) -> object:
    raise ValueError(constant)
