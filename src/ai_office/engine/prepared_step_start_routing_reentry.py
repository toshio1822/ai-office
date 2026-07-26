"""Read-only routing from one exact Phase 39 result to Phase 34."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.prepared_step_start_reentry import (
    PreparedStepStartReentryError,
    prepare_persisted_prepared_step_start,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState

PreparedStepStartRoutingClassification = Literal[
    "result_type",
    "prepared_step_contract",
    "completion_contract",
    "workflow_definition",
    "employee_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "start_contract",
    "dependency_error",
    "dependency_rollback",
]
_ERROR_MESSAGE = "prepared-step start routing inputs are incompatible"
StartReentryFunction = Callable[
    [object, object, object, object, object], PreparedStepExecutionStart
]


@dataclass(frozen=True)
class PreparedStepStartRoutingFailureDetail:
    """Safe immutable classification for one Phase 40 rejection."""

    classification: PreparedStepStartRoutingClassification


class PreparedStepStartRoutingError(ValueError):
    """Raised when Phase 40 cannot route a supplied result safely."""


class PreparedStepStartRoutingCompatibilityError(PreparedStepStartRoutingError):
    """Raised for a safe Phase 40 incompatibility or dependency failure."""

    def __init__(self, classification: PreparedStepStartRoutingClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PreparedStepStartRoutingFailureDetail(classification)


def route_prepared_step_start_reentry(
    result: object,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    *,
    start_reentry_function: StartReentryFunction = (
        prepare_persisted_prepared_step_start
    ),
) -> PreparedStepExecutionStart | WorkflowProgressionDecision:
    """Route one exact prepared step once, or stop an exact completion decision."""
    _validate_inputs(
        result, workflow, employee, state_path, events_path, start_reentry_function
    )
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    if type(result) is WorkflowProgressionDecision:
        return result
    assert type(result) is PreparedWorkflowStep and type(employee) is EmployeeDefinition
    original = _capture(state_path, events_path)
    started = _call(
        start_reentry_function,
        workflow,
        employee,
        state_path,
        events_path,
        result,
        original,
    )
    _validate_start(started, result)
    return started


def _validate_inputs(
    result: object,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    start_reentry_function: object,
) -> None:
    if type(result) not in (PreparedWorkflowStep, WorkflowProgressionDecision):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if type(result) is PreparedWorkflowStep:
        _validate_prepared(result, workflow, employee)
    else:
        _validate_completion(result, workflow, employee)
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(start_reentry_function):
        _raise("start_contract")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("dependency_error")


def _validate_prepared(
    prepared: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    employee: object | None,
) -> None:
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    if (
        isinstance(prepared.step_index, bool)
        or not isinstance(prepared.step_index, int)
        or not 1 <= prepared.step_index <= len(workflow.steps)
    ):
        _raise("prepared_step_contract")
    step = workflow.steps[prepared.step_index - 1]
    strings = (
        prepared.workflow_id,
        prepared.step_id,
        prepared.employee_id,
        prepared.employee_instructions,
        prepared.step_instructions,
        prepared.model,
    )
    if not (
        all(isinstance(value, str) and bool(value) for value in strings)
        and isinstance(prepared.allowed_tool_names, tuple)
        and all(
            isinstance(tool, str) and bool(tool) for tool in prepared.allowed_tool_names
        )
        and prepared.workflow_id == workflow.id
        and prepared.step_id == step.id
        and prepared.employee_id == step.employee == employee.id
        and prepared.employee_instructions == employee.instructions
        and prepared.step_instructions == step.instructions
        and prepared.model == employee.model
        and prepared.allowed_tool_names == tuple(employee.allowed_tools)
    ):
        _raise("prepared_step_contract")


def _validate_completion(
    decision: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object | None,
) -> None:
    final = workflow.steps[-1]
    if not (
        employee is None
        and decision.decision == "workflow_complete"
        and decision.workflow_id == workflow.id
        and decision.current_step_id == final.id
        and decision.current_step_index == len(workflow.steps)
        and decision.current_employee_id == final.employee
        and decision.next_step_id is None
        and decision.next_step_index is None
        and decision.next_employee_id is None
        and decision.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _capture(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        return state_path.read_bytes(), events_path.read_bytes()
    except OSError:
        _raise("dependency_error")


def _call(
    function: StartReentryFunction,
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
    state_path: Path,
    events_path: Path,
    prepared: PreparedWorkflowStep,
    original: tuple[bytes, bytes],
) -> object:
    try:
        value = function(workflow, employee, state_path, events_path, prepared)
    except PreparedStepStartReentryError:
        _restore(state_path, events_path, original)
        raise
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    _reject_changed(state_path, events_path, original)
    return value


def _restore(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        for path, bytes_before in (
            (state_path, original[0]),
            (events_path, original[1]),
        ):
            try:
                changed = path.read_bytes() != bytes_before
            except FileNotFoundError:
                changed = True
            if changed:
                path.write_bytes(bytes_before)
    except OSError:
        _raise("dependency_rollback")


def _reject_changed(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        changed = (
            state_path.read_bytes() != original[0]
            or events_path.read_bytes() != original[1]
        )
    except OSError:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    if changed:
        _restore(state_path, events_path, original)
        _raise("dependency_error")


def _validate_start(value: object, prepared: PreparedWorkflowStep) -> None:
    if type(value) is not PreparedStepExecutionStart:
        _raise("start_contract")
    request, running = value.request, value.running_state
    if not (
        type(request) is ModelInvocationRequest
        and request.model == prepared.model
        and request.system_instructions == prepared.employee_instructions
        and request.task_instructions == prepared.step_instructions
        and request.allowed_tools == prepared.allowed_tool_names
        and type(running) is WorkflowExecutionState
        and running.workflow_id == prepared.workflow_id
        and running.status == "running"
        and running.current_step_id == prepared.step_id
        and running.current_step_index == prepared.step_index
        and running.current_employee_id == prepared.employee_id
        and isinstance(running.completed_step_ids, tuple)
        and all(
            isinstance(step_id, str) and bool(step_id)
            for step_id in running.completed_step_ids
        )
        and (
            not running.completed_step_ids
            or running.completed_step_ids[-1] != prepared.step_id
        )
        and running.last_failure_category is None
    ):
        _raise("start_contract")


def _raise(classification: PreparedStepStartRoutingClassification) -> None:
    raise PreparedStepStartRoutingCompatibilityError(classification) from None
