"""Safe administrator-configured HTTP request headers for destinations.

Issue #708: named destinations may declare a small set of non-secret request
headers (for example a static ``User-Agent`` and a Run-scoped
``x-opencode-session``).  This module owns the strict policy: no arbitrary
header injection, no credential/Host/framing/cookie/hop-by-hop override, and no
unsupported substitution.  It contains no secret values and performs no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The single supported substitution.  Resolving it produces a stable,
# non-masquerading value derived only from the Run identity, so it stays
# constant across steps, continuation and recovery.
SESSION_MARKER = "{run_session}"
_SESSION_VALUE_PREFIX = "ai-office-run-"

_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HEADER_NAME_MAX_LENGTH = 128
_HEADER_VALUE_MAX_LENGTH = 512

# Names that must remain exclusively under transport/authentication control or
# that could enable smuggling, proxy confusion, credential override, or cookie
# tampering.  Comparison is case-insensitive.
_RESERVED_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "host",
        "content-length",
        "content-type",
        "transfer-encoding",
        "connection",
        "proxy-connection",
        "keep-alive",
        "close",
        "te",
        "trailer",
        "upgrade",
        "cookie",
        "cookie2",
        "set-cookie",
        "set-cookie2",
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-forwarded-port",
        "x-forwarded-server",
        "x-real-ip",
        "via",
        "expect",
    }
)
_RESERVED_NAME_PREFIXES = ("proxy-", "x-forwarded-")


class ConfiguredRequestHeaderError(ValueError):
    """Raised when a configured request header is unsafe or unsupported."""


@dataclass(frozen=True)
class ConfiguredRequestHeader:
    """One safe, non-secret request header from an administrator registry.

    ``value`` is either a static string or :data:`SESSION_MARKER`, which is
    resolved at request-build time from the Run identity.
    """

    name: str
    value: str

    def __post_init__(self) -> None:
        validate_configured_request_header(self)

    @property
    def is_session_marker(self) -> bool:
        return self.value == SESSION_MARKER


def validate_configured_request_header(header: object) -> None:
    """Validate one header name/value pair without network or secrets."""
    if type(header) is not ConfiguredRequestHeader:
        raise ConfiguredRequestHeaderError("request header is invalid")
    name = header.name
    value = header.value
    if (
        type(name) is not str
        or not name
        or len(name) > _HEADER_NAME_MAX_LENGTH
        or _HEADER_NAME_PATTERN.fullmatch(name) is None
    ):
        raise ConfiguredRequestHeaderError("request header name is invalid")
    lowered = name.lower()
    if lowered in _RESERVED_HEADER_NAMES or lowered.startswith(
        _RESERVED_NAME_PREFIXES
    ):
        raise ConfiguredRequestHeaderError("request header name is reserved")
    if type(value) is not str or not value or len(value) > _HEADER_VALUE_MAX_LENGTH:
        raise ConfiguredRequestHeaderError("request header value is invalid")
    if value != value.strip():
        raise ConfiguredRequestHeaderError(
            "request header value has surrounding whitespace"
        )
    if "\r" in value or "\n" in value:
        raise ConfiguredRequestHeaderError("request header value contains CR/LF")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ConfiguredRequestHeaderError(
            "request header value contains control characters"
        )
    if value != SESSION_MARKER and ("{" in value or "}" in value):
        raise ConfiguredRequestHeaderError("request header substitution is unsupported")


def validate_request_headers(value: object) -> None:
    """Validate a tuple of configured headers, rejecting duplicates by name."""
    if type(value) is not tuple:
        raise ConfiguredRequestHeaderError("request headers must be a tuple")
    seen: set[str] = set()
    for item in value:
        validate_configured_request_header(item)
        key = item.name.lower()
        if key in seen:
            raise ConfiguredRequestHeaderError("request header name is duplicated")
        seen.add(key)


def parse_configured_request_headers(
    raw: object,
) -> tuple[ConfiguredRequestHeader, ...]:
    """Parse a YAML mapping into a deterministic, validated header tuple."""
    if type(raw) is not dict:
        raise ConfiguredRequestHeaderError("request headers must be a mapping")
    headers: list[ConfiguredRequestHeader] = []
    seen: set[str] = set()
    for name, value in raw.items():
        header = ConfiguredRequestHeader(name=name, value=value)
        key = header.name.lower()
        if key in seen:
            raise ConfiguredRequestHeaderError("request header name is duplicated")
        seen.add(key)
        headers.append(header)
    return tuple(sorted(headers, key=lambda item: item.name.lower()))


def request_headers_from_records(
    records: object,
) -> tuple[ConfiguredRequestHeader, ...]:
    """Rebuild a header tuple from persisted ``[name, value]`` records."""
    if type(records) is not list:
        raise ConfiguredRequestHeaderError("request headers must be a list")
    headers: list[ConfiguredRequestHeader] = []
    for record in records:
        if type(record) is not list or len(record) != 2:
            raise ConfiguredRequestHeaderError("request header record is invalid")
        headers.append(ConfiguredRequestHeader(name=record[0], value=record[1]))
    result = tuple(headers)
    validate_request_headers(result)
    return result


def session_value_for_run(run_id: str) -> str:
    """Return the stable, non-masquerading session identity for one Run."""
    if type(run_id) is not str or not run_id:
        raise ConfiguredRequestHeaderError("session Run identity is invalid")
    return f"{_SESSION_VALUE_PREFIX}{run_id}"


def resolve_configured_request_headers(
    headers: tuple[ConfiguredRequestHeader, ...],
    run_id: str | None,
) -> tuple[tuple[str, str], ...]:
    """Resolve configured headers into concrete name/value pairs."""
    resolved: list[tuple[str, str]] = []
    for header in headers:
        if header.is_session_marker:
            if type(run_id) is not str or not run_id:
                raise ConfiguredRequestHeaderError(
                    "session header requires a Run identity"
                )
            resolved.append((header.name, session_value_for_run(run_id)))
        else:
            resolved.append((header.name, header.value))
    return tuple(resolved)


__all__ = [
    "ConfiguredRequestHeader",
    "ConfiguredRequestHeaderError",
    "SESSION_MARKER",
    "parse_configured_request_headers",
    "request_headers_from_records",
    "resolve_configured_request_headers",
    "session_value_for_run",
    "validate_configured_request_header",
    "validate_request_headers",
]
