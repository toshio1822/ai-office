"""Provider-independent normalized outcomes for model invocations."""

from dataclasses import dataclass, field
from typing import Literal

ModelInvocationResponseBodyKind = Literal[
    "empty",
    "json",
    "sse",
    "html",
    "plaintext",
    "malformed_json",
    "non_utf8",
]

ModelInvocationFailureCategory = Literal[
    "api_error",
    "transport_error",
    "invalid_response",
    "invalid_output",
    "invalid_request",
    "approval_required",
]


@dataclass(frozen=True, slots=True)
class ModelInvocationResultProvenance:
    """Immutable locator for the durable evidence behind one provider result."""

    attempt_id: str
    attempt_evidence_sha256: str
    normalized_result_evidence_sha256: str
    raw_response_evidence_sha256: str | None
    raw_response_body_sha256: str | None

    def __post_init__(self) -> None:
        required_digests = (
            self.attempt_id,
            self.attempt_evidence_sha256,
            self.normalized_result_evidence_sha256,
        )
        if any(
            type(value) is not str
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for value in required_digests
        ):
            raise ValueError("model invocation result provenance is invalid")
        optional_digests = (
            self.raw_response_evidence_sha256,
            self.raw_response_body_sha256,
        )
        if (optional_digests[0] is None) != (optional_digests[1] is None) or any(
            value is not None
            and (
                type(value) is not str
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            )
            for value in optional_digests
        ):
            raise ValueError("model invocation result provenance is invalid")


@dataclass(frozen=True)
class ModelInvocationSuccess:
    """Immutable successful model invocation result for a future runtime."""

    provider: str
    response_id: str
    request_id: str | None
    status: str
    text_parts: tuple[str, ...]
    text: str
    _execution_evidence: ModelInvocationResultProvenance | None = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True)
class ModelInvocationFailureDiagnostics:
    """Secret-free metadata for one received but invalid HTTP response."""

    status_code: int
    content_type: str | None
    body_length: int
    body_kind: ModelInvocationResponseBodyKind


@dataclass(frozen=True)
class ModelInvocationFailure:
    """Immutable safe failure result for a future runtime."""

    provider: str
    category: ModelInvocationFailureCategory
    message: str
    request_id: str | None
    status_code: int | None
    provider_error_type: str | None
    provider_error_code: str | None
    response_diagnostics: ModelInvocationFailureDiagnostics | None = None
    _execution_evidence: ModelInvocationResultProvenance | None = field(
        default=None, repr=False, compare=False
    )


ModelInvocationResult = ModelInvocationSuccess | ModelInvocationFailure
