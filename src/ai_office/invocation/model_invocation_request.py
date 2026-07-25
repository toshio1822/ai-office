"""Provider-independent inputs for a future model adapter."""

from dataclasses import dataclass

from ai_office.planning.step_execution_request import StepExecutionRequest


@dataclass(frozen=True)
class ModelInvocationRequest:
    """Immutable values required to invoke a model for one workflow step."""

    model: str
    system_instructions: str
    task_instructions: str
    allowed_tools: tuple[str, ...]


def build_model_invocation_request(
    step_request: StepExecutionRequest,
) -> ModelInvocationRequest:
    """Copy a step request into the provider-independent invocation boundary."""
    return ModelInvocationRequest(
        model=step_request.model,
        system_instructions=step_request.employee_instructions,
        task_instructions=step_request.step_instructions,
        allowed_tools=tuple(step_request.allowed_tools),
    )
