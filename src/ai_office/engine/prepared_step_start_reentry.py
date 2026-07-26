"""Read-only reentry from a persisted success to Phase 27 start preparation."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.prepared_step_execution_start import (
    PreparedStepExecutionStart,
    prepare_prepared_step_execution_start,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage.workflow_execution_history import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
    load_workflow_execution_history,
)
from ai_office.storage.workflow_execution_persistence import (
    WorkflowExecutionPersistenceTargets,
)

PreparedStepStartReentryClassification = Literal[
    "workflow_definition",
    "employee_definition",
    "prepared_step_type",
    "prepared_step_identity",
    "prepared_step_content",
    "state_target",
    "event_target",
    "history_data",
    "history_identity",
    "workflow_identity",
    "start_contract",
]
_ERROR_MESSAGE = "prepared-step start reentry inputs are incompatible"
StartFunction = Callable[
    [PreparedWorkflowStep, LoadedWorkflowExecutionHistory], PreparedStepExecutionStart
]


@dataclass(frozen=True)
class PreparedStepStartReentryFailureDetail:
    classification: PreparedStepStartReentryClassification


class PreparedStepStartReentryError(ValueError):
    """Raised when Phase 33 cannot safely reach Phase 27."""


class PreparedStepStartReentryCompatibilityError(PreparedStepStartReentryError):
    """Raised for a safe Phase 33 input or returned-result incompatibility."""

    def __init__(self, classification: PreparedStepStartReentryClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PreparedStepStartReentryFailureDetail(classification)


def prepare_persisted_prepared_step_start(
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    prepared_step: object,
    *,
    start_function: StartFunction = prepare_prepared_step_execution_start,
) -> PreparedStepExecutionStart:
    """Reload one persisted success and call Phase 27 exactly once."""
    _validate_inputs(
        workflow, employee, state_path, events_path, prepared_step, start_function
    )
    assert isinstance(workflow, WorkflowDefinition)
    assert isinstance(employee, EmployeeDefinition)
    assert isinstance(state_path, Path)
    assert isinstance(events_path, Path)
    assert isinstance(prepared_step, PreparedWorkflowStep)
    history = _load_history(state_path, events_path)
    _validate_history(workflow, history)
    _validate_prepared_step(workflow, employee, history, prepared_step)
    result = start_function(prepared_step, history)
    _validate_start_result(result, prepared_step, history)
    return result


def _validate_inputs(
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    prepared_step: object,
    start_function: object,
) -> None:
    if not isinstance(workflow, WorkflowDefinition):
        _raise("workflow_definition")
    if not isinstance(employee, EmployeeDefinition):
        _raise("employee_definition")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if not isinstance(prepared_step, PreparedWorkflowStep):
        _raise("prepared_step_type")
    if not callable(start_function):
        _raise("start_contract")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("history_data")


def _load_history(
    state_path: Path, events_path: Path
) -> LoadedWorkflowExecutionHistory:
    try:
        return load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("history_data")


def _validate_history(
    workflow: WorkflowDefinition, history: LoadedWorkflowExecutionHistory
) -> None:
    state = history.state
    if (
        state.status != "succeeded"
        or state.last_failure_category is not None
        or not history.events
    ):
        _raise("history_identity")
    event = history.events[-1]
    if not (
        event.event_type == "step_succeeded"
        and event.workflow_id == state.workflow_id
        and event.step_id == state.current_step_id
        and event.step_index == state.current_step_index
        and event.employee_id == state.current_employee_id
        and event.previous_status == "running"
        and event.next_status == "succeeded"
        and event.failure_category is None
        and event.message is None
        and state.completed_step_ids
        and state.completed_step_ids[-1] == state.current_step_id
    ):
        _raise("history_identity")
    if workflow.id != state.workflow_id or not 1 <= state.current_step_index <= len(
        workflow.steps
    ):
        _raise("workflow_identity")
    current = workflow.steps[state.current_step_index - 1]
    if (
        current.id != state.current_step_id
        or current.employee != state.current_employee_id
    ):
        _raise("workflow_identity")
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    try:
        completed = [positions[step_id] for step_id in state.completed_step_ids]
    except KeyError:
        _raise("workflow_identity")
    compressed = tuple(
        value
        for index, value in enumerate(completed)
        if index == 0 or completed[index - 1] != value
    )
    if compressed != tuple(range(1, state.current_step_index + 1)):
        _raise("workflow_identity")


def _validate_prepared_step(
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
    history: LoadedWorkflowExecutionHistory,
    prepared: PreparedWorkflowStep,
) -> None:
    state = history.state
    if state.current_step_index == len(workflow.steps):
        _raise("prepared_step_identity")
    next_step = workflow.steps[state.current_step_index]
    if not (
        prepared.workflow_id == workflow.id
        and prepared.step_index == state.current_step_index + 1
        and prepared.step_id == next_step.id
        and prepared.employee_id == next_step.employee
        and prepared.employee_id == employee.id
    ):
        _raise("prepared_step_identity")
    if not _valid_prepared_content(prepared, employee, next_step.instructions):
        _raise("prepared_step_content")


def _valid_prepared_content(
    prepared: PreparedWorkflowStep, employee: EmployeeDefinition, step_instructions: str
) -> bool:
    return (
        prepared.employee_instructions == employee.instructions
        and prepared.step_instructions == step_instructions
        and prepared.model == employee.model
        and prepared.allowed_tool_names == tuple(employee.allowed_tools)
        and all(
            isinstance(value, str) and bool(value)
            for value in (
                prepared.step_id,
                prepared.employee_id,
                prepared.employee_instructions,
                prepared.step_instructions,
                prepared.model,
            )
        )
        and isinstance(prepared.step_index, int)
        and not isinstance(prepared.step_index, bool)
        and isinstance(prepared.allowed_tool_names, tuple)
        and all(
            isinstance(tool, str) and bool(tool) for tool in prepared.allowed_tool_names
        )
    )


def _validate_start_result(
    result: object,
    prepared: PreparedWorkflowStep,
    history: LoadedWorkflowExecutionHistory,
) -> None:
    if not isinstance(result, PreparedStepExecutionStart):
        _raise("start_contract")
    request, running = result.request, result.running_state
    valid = (
        isinstance(request, ModelInvocationRequest)
        and request.model == prepared.model
        and request.system_instructions == prepared.employee_instructions
        and request.task_instructions == prepared.step_instructions
        and request.allowed_tools == tuple(prepared.allowed_tool_names)
        and isinstance(running, WorkflowExecutionState)
        and running.workflow_id == prepared.workflow_id
        and running.status == "running"
        and running.current_step_id == prepared.step_id
        and running.current_step_index == prepared.step_index
        and running.current_employee_id == prepared.employee_id
        and running.completed_step_ids == history.state.completed_step_ids
        and running.last_failure_category is None
    )
    if not valid:
        _raise("start_contract")


def _raise(classification: PreparedStepStartReentryClassification) -> None:
    raise PreparedStepStartReentryCompatibilityError(classification) from None
