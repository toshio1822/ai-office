"""Provider-independent model invocation requests."""

from ai_office.invocation.model_invocation_request import (
    ModelInvocationRequest,
    build_model_invocation_request,
)
from ai_office.invocation.model_invocation_result import (
    ModelInvocationFailure,
    ModelInvocationFailureCategory,
    ModelInvocationResult,
    ModelInvocationSuccess,
)

__all__ = [
    "ModelInvocationFailure",
    "ModelInvocationFailureCategory",
    "ModelInvocationRequest",
    "ModelInvocationResult",
    "ModelInvocationSuccess",
    "build_model_invocation_request",
]
