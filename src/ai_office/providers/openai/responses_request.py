"""OpenAI Responses API pre-runtime request model and adapter."""

from dataclasses import dataclass

from ai_office.invocation import (
    ModelInvocationRequest,
    build_model_invocation_task_input,
)


@dataclass(frozen=True)
class OpenAIResponsesRequest:
    """Immutable OpenAI-specific invocation information before runtime handling.

    This is not an HTTP payload or wire format.  Tool names remain unresolved and
    must not be interpreted as OpenAI tool schemas at this boundary.
    """

    model: str
    instructions: str
    input: str
    allowed_tool_names: tuple[str, ...]


def build_openai_responses_request(
    invocation_request: ModelInvocationRequest,
) -> OpenAIResponsesRequest:
    """Copy provider-independent invocation values into the OpenAI boundary."""
    return OpenAIResponsesRequest(
        model=invocation_request.model,
        instructions=invocation_request.system_instructions,
        input=build_model_invocation_task_input(invocation_request),
        allowed_tool_names=tuple(invocation_request.allowed_tools),
    )
