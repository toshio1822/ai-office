"""Provider-independent model invocation requests."""

from ai_office.invocation.model_invocation_request import (
    ModelInvocationRequest,
    build_model_invocation_request,
)

__all__ = ["ModelInvocationRequest", "build_model_invocation_request"]
