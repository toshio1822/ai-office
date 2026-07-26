"""One guarded execution of a prepared start whose running state is persisted."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.invocation import (
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
)
from ai_office.planning import StepExecutionRequest
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesTransport,
    send_openai_responses_http_request,
)
from ai_office.runtime.step_runtime_execution import (
    StepRuntimeExecutionInput,
    StepRuntimeExecutionResult,
    execute_openai_runtime_step,
)
from ai_office.runtime.workflow_execution_transition import WorkflowExecutionState
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionLoadError,
    load_workflow_execution_state,
)
from ai_office.tools import ToolDefinition

if TYPE_CHECKING:
    pass

PersistedStartExecutionClassification = Literal[
    "start_type",
    "state_target",
    "state_data",
    "state_status",
    "state_identity",
    "workflow_definition",
    "workflow_identity",
    "employee_identity",
    "employee_contract",
    "request_data",
]
_ERROR_MESSAGE = "persisted-start execution inputs are incompatible"


@dataclass(frozen=True)
class PersistedStartExecutionFailureDetail:
    """Safe classification for an error before Phase 21 delegation."""

    classification: PersistedStartExecutionClassification


class PersistedStartExecutionError(ValueError):
    """Raised when a prepared start cannot safely reach provider execution."""


class PersistedStartExecutionCompatibilityError(PersistedStartExecutionError):
    """Raised for a safe, classified pre-execution incompatibility."""

    def __init__(self, classification: PersistedStartExecutionClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedStartExecutionFailureDetail(classification)


def execute_persisted_start_openai_step(
    start: object,
    state_path: object,
    workflow: object,
    employee: object,
    resolved_tools: object,
    api_key: object,
    approval: object,
    *,
    transport: OpenAIResponsesTransport = send_openai_responses_http_request,
) -> StepRuntimeExecutionResult:
    """Verify a persisted running state, then delegate exactly once to Phase 21.

    This boundary deliberately does not persist the resulting success or failure.
    """
    _validate_in_memory_inputs(
        start,
        state_path,
        workflow,
        employee,
        resolved_tools,
        api_key,
        approval,
        transport,
    )
    # Narrowing follows the checked concrete contracts above.
    from ai_office.engine.prepared_step_execution_start import (
        PreparedStepExecutionStart,
    )

    assert isinstance(start, PreparedStepExecutionStart)
    assert isinstance(state_path, Path)
    assert isinstance(workflow, WorkflowDefinition)
    assert isinstance(employee, EmployeeDefinition)
    assert isinstance(resolved_tools, tuple)
    assert isinstance(api_key, OpenAIApiKey)
    assert isinstance(approval, ModelInvocationExecutionApproval)

    persisted_state = _load_running_state(state_path)
    if persisted_state != start.running_state:
        _raise("state_identity")
    workflow_step = _validate_workflow_state(workflow, persisted_state)
    step_request = _build_step_request(
        persisted_state, workflow, workflow_step, employee, start.request
    )
    return execute_openai_runtime_step(
        StepRuntimeExecutionInput(
            step_request=step_request,
            invocation_request=start.request,
            resolved_tools=resolved_tools,
            approval=approval,
        ),
        api_key,
        transport=transport,
    )


def _validate_in_memory_inputs(
    start: object,
    state_path: object,
    workflow: object,
    employee: object,
    resolved_tools: object,
    api_key: object,
    approval: object,
    transport: object,
) -> None:
    # Import lazily because engine models depend on runtime state models.
    from ai_office.engine.prepared_step_execution_start import (
        PreparedStepExecutionStart,
    )

    if not isinstance(start, PreparedStepExecutionStart):
        _raise("start_type")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(workflow, WorkflowDefinition):
        _raise("workflow_definition")
    if not isinstance(start.request, ModelInvocationRequest):
        _raise("request_data")
    if not _valid_running_state(start.running_state):
        _raise("state_identity")
    if not isinstance(employee, EmployeeDefinition):
        _raise("employee_contract")
    if not isinstance(resolved_tools, tuple) or not all(
        isinstance(tool, ToolDefinition) for tool in resolved_tools
    ):
        _raise("request_data")
    if not isinstance(api_key, OpenAIApiKey) or not isinstance(
        approval, ModelInvocationExecutionApproval
    ) or not callable(transport):
        _raise("request_data")


def _load_running_state(state_path: Path) -> WorkflowExecutionState:
    try:
        state = load_workflow_execution_state(state_path)
    except WorkflowExecutionDataError:
        _raise("state_data")
    except WorkflowExecutionLoadError:
        _raise("state_target")
    if state.status != "running":
        _raise("state_status")
    if state.last_failure_category is not None:
        _raise("state_identity")
    return state


def _build_step_request(
    state: WorkflowExecutionState,
    workflow: WorkflowDefinition,
    workflow_step: WorkflowStepDefinition,
    employee: EmployeeDefinition,
    request: ModelInvocationRequest,
) -> StepExecutionRequest:
    if employee.id != state.current_employee_id:
        _raise("employee_identity")
    if (
        employee.instructions != request.system_instructions
        or employee.model != request.model
        or tuple(employee.allowed_tools) != request.allowed_tools
    ):
        _raise("employee_contract")
    return StepExecutionRequest(
        workflow_id=state.workflow_id,
        workflow_name=workflow.name,
        step_index=state.current_step_index,
        step_id=state.current_step_id,
        step_name=workflow_step.name,
        employee_id=state.current_employee_id,
        employee_name=employee.name,
        employee_role=employee.role,
        model=request.model,
        allowed_tools=request.allowed_tools,
        employee_instructions=request.system_instructions,
        step_instructions=request.task_instructions,
    )


def _validate_workflow_state(
    workflow: WorkflowDefinition, state: WorkflowExecutionState
) -> WorkflowStepDefinition:
    """Bind the persisted running identity to the validated workflow definition."""
    if workflow.id != state.workflow_id:
        _raise("workflow_identity")
    if state.current_step_index > len(workflow.steps):
        _raise("workflow_identity")
    step = workflow.steps[state.current_step_index - 1]
    if (
        step.id != state.current_step_id
        or step.employee != state.current_employee_id
    ):
        _raise("workflow_identity")
    return step


def _valid_running_state(value: object) -> bool:
    return (
        isinstance(value, WorkflowExecutionState)
        and value.status == "running"
        and value.last_failure_category is None
        and all(
            isinstance(item, str) and bool(item)
            for item in (
                value.workflow_id,
                value.current_step_id,
                value.current_employee_id,
            )
        )
        and isinstance(value.current_step_index, int)
        and not isinstance(value.current_step_index, bool)
        and value.current_step_index > 0
        and isinstance(value.completed_step_ids, tuple)
        and all(isinstance(item, str) for item in value.completed_step_ids)
    )


def _raise(classification: PersistedStartExecutionClassification) -> None:
    raise PersistedStartExecutionCompatibilityError(classification) from None
