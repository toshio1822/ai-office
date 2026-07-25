"""Provider-independent normalized outcomes for model invocations."""

from dataclasses import dataclass
from typing import Literal

ModelInvocationFailureCategory = Literal[
    "api_error",
    "transport_error",
    "invalid_response",
    "invalid_output",
]


@dataclass(frozen=True)
class ModelInvocationSuccess:
    """Immutable successful model invocation result for a future runtime."""

    provider: str
    response_id: str
    request_id: str | None
    status: str
    text_parts: tuple[str, ...]
    text: str


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


ModelInvocationResult = ModelInvocationSuccess | ModelInvocationFailure
