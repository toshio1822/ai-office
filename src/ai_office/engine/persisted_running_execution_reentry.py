"""One guarded Phase 29 execution for an already persisted running state."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import OpenAIResponsesTransport
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionResult,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.runtime.persisted_start_execution import (
    execute_persisted_start_openai_step,
)
from ai_office.storage import load_workflow_execution_state
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionLoadError,
)

PersistedRunningExecutionReentryClassification = Literal[
    "workflow_definition",
    "employee_definition",
    "start_type",
    "start_identity",
    "start_content",
    "state_target",
    "state_data",
    "workflow_identity",
    "execution_contract",
    "state_immutability",
    "state_rollback",
]
_ERROR_MESSAGE = "persisted-running execution inputs are incompatible"
ExecutionFunction = Callable[..., StepRuntimeExecutionResult]


@dataclass(frozen=True)
class PersistedRunningExecutionReentryFailureDetail:
    classification: PersistedRunningExecutionReentryClassification


class PersistedRunningExecutionReentryError(ValueError):
    pass


class PersistedRunningExecutionReentryCompatibilityError(
    PersistedRunningExecutionReentryError
):
    def __init__(
        self, classification: PersistedRunningExecutionReentryClassification
    ) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedRunningExecutionReentryFailureDetail(classification)


def execute_persisted_running_openai_step(
    start: object,
    state_path: object,
    workflow: object,
    employee: object,
    resolved_tools: object,
    api_key: object,
    approval: object,
    *,
    transport: OpenAIResponsesTransport,
    execution_function: ExecutionFunction = execute_persisted_start_openai_step,
) -> StepRuntimeExecutionResult:
    """Verify persisted running bytes and delegate once to Phase 29."""
    if not isinstance(start, PreparedStepExecutionStart):
        _raise("start_type")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(workflow, WorkflowDefinition):
        _raise("workflow_definition")
    if not isinstance(employee, EmployeeDefinition):
        _raise("employee_definition")
    if not isinstance(start.request, ModelInvocationRequest) or not isinstance(
        start.running_state, WorkflowExecutionState
    ):
        _raise("start_content")
    if not callable(execution_function) or not callable(transport):
        _raise("execution_contract")
    try:
        original = state_path.read_bytes()
        persisted = load_workflow_execution_state(state_path)
    except WorkflowExecutionDataError:
        _raise("state_data")
    except (OSError, WorkflowExecutionLoadError):
        _raise("state_target")
    if (
        persisted != start.running_state
        or persisted.status != "running"
        or persisted.last_failure_category is not None
    ):
        _raise("start_identity")
    if (
        not 1 <= persisted.current_step_index <= len(workflow.steps)
        or workflow.id != persisted.workflow_id
    ):
        _raise("workflow_identity")
    step = workflow.steps[persisted.current_step_index - 1]
    if not (
        step.id == persisted.current_step_id
        and step.employee == persisted.current_employee_id
        and employee.id == step.employee
    ):
        _raise("workflow_identity")
    if not (
        start.request.model == employee.model
        and start.request.system_instructions == employee.instructions
        and start.request.task_instructions == step.instructions
        and start.request.allowed_tools == tuple(employee.allowed_tools)
    ):
        _raise("start_content")
    result = execution_function(
        start,
        state_path,
        workflow,
        employee,
        resolved_tools,
        api_key,
        approval,
        transport=transport,
    )
    if _state_changed(state_path, original):
        _restore_state(state_path, original)
        _raise("state_immutability")
    if not _valid_result(result, persisted):
        _raise("execution_contract")
    return result


def _valid_result(result: object, state: WorkflowExecutionState) -> bool:
    return isinstance(
        result, (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)
    ) and (
        result.workflow_id == state.workflow_id
        and result.step_id == state.current_step_id
        and result.step_index == state.current_step_index
        and result.employee_id == state.current_employee_id
    )


def _state_changed(path: Path, original: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != original
    except OSError:
        return True


def _restore_state(path: Path, original: bytes) -> None:
    try:
        path.write_bytes(original)
    except OSError:
        _raise("state_rollback")


def _raise(classification: PersistedRunningExecutionReentryClassification) -> None:
    raise PersistedRunningExecutionReentryCompatibilityError(classification) from None
