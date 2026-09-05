"""Provider-independent inputs for a future model adapter."""

import json
from dataclasses import dataclass

from ai_office.planning.step_execution_request import StepExecutionRequest


@dataclass(frozen=True)
class UpstreamStepOutput:
    """One successful predecessor output forwarded as task-side data."""

    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    output_text: str


@dataclass(frozen=True)
class ModelInvocationRequest:
    """Immutable values required to invoke a model for one workflow step."""

    model: str
    system_instructions: str
    task_instructions: str
    allowed_tools: tuple[str, ...]
    upstream_inputs: tuple[UpstreamStepOutput, ...] = ()

    def __post_init__(self) -> None:
        _validate_upstream_inputs(self.upstream_inputs)

def build_model_invocation_request(
    step_request: StepExecutionRequest,
    *,
    upstream_inputs: tuple[UpstreamStepOutput, ...] = (),
) -> ModelInvocationRequest:
    """Copy a step request into the provider-independent invocation boundary."""
    _validate_upstream_inputs(upstream_inputs)
    return ModelInvocationRequest(
        model=step_request.model,
        system_instructions=step_request.employee_instructions,
        task_instructions=step_request.step_instructions,
        allowed_tools=tuple(step_request.allowed_tools),
        upstream_inputs=upstream_inputs,
    )


def build_model_invocation_task_input(request: ModelInvocationRequest) -> str:
    """Render explicit predecessor data on the task/user side of an invocation."""
    if request.upstream_inputs == ():
        return request.task_instructions
    value = {
        "task_instructions": request.task_instructions,
        "upstream_inputs": [
            {
                "employee_id": upstream.employee_id,
                "output_text": upstream.output_text,
                "step_id": upstream.step_id,
                "step_index": upstream.step_index,
                "workflow_id": upstream.workflow_id,
            }
            for upstream in request.upstream_inputs
        ],
    }
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validate_upstream_inputs(value: object) -> None:
    """Reject unordered or structurally ambiguous upstream input containers."""
    if type(value) is not tuple or any(
        type(item) is not UpstreamStepOutput for item in value
    ):
        raise TypeError("upstream_inputs must be a tuple of UpstreamStepOutput")
