"""Read-only bridge from an exact Phase 46 result to Phase 40."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.prepared_step_start_routing_reentry import (
    PreparedStepStartRoutingError,
    route_prepared_step_start_reentry,
)
from ai_office.engine.progression_preparation_routing_reentry import (
    _load_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationRequest

Classification = Literal[
    "result_type",
    "workflow_definition",
    "employee_contract",
    "prepared_step_contract",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "start_contract",
    "dependency_error",
    "dependency_rollback",
]
_CATEGORIES = frozenset(
    get_args(
        __import__(
            "ai_office.invocation", fromlist=["ModelInvocationFailureCategory"]
        ).ModelInvocationFailureCategory
    )
)
Function = Callable[..., PreparedStepExecutionStart | WorkflowProgressionDecision]


@dataclass(frozen=True)
class PreparedStepStartBridgeFailureDetail:
    classification: Classification


class PreparedStepStartBridgeError(ValueError):
    pass


class PreparedStepStartBridgeCompatibilityError(PreparedStepStartBridgeError):
    def __init__(self, classification: Classification) -> None:
        super().__init__("prepared-step start bridge inputs are incompatible")
        self.detail = PreparedStepStartBridgeFailureDetail(classification)


def route_prepared_step_start_bridge_reentry(
    result: object,
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    start_routing_function: Function = route_prepared_step_start_reentry,
) -> (
    PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome
):
    if type(result) not in (
        PreparedWorkflowStep,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(start_routing_function):
        _raise("start_contract")
    if not state_path.is_file():
        _raise("state_target")
    if not events_path.is_file():
        _raise("event_target")
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _completion(result, workflow)
        state, _ = _history(workflow, state_path, events_path)
        if state.status != "succeeded" or (
            state.current_step_id,
            state.current_step_index,
            state.current_employee_id,
        ) != (
            result.current_step_id,
            result.current_step_index,
            result.current_employee_id,
        ):
            _raise("terminal_contract")
        _unchanged(state_path, events_path, original)
        return result
    if type(result) is PersistedExecutionOutcome:
        _failure(result, workflow)
        state, _ = _history(workflow, state_path, events_path)
        if state.status != "failed" or (
            state.workflow_id,
            state.current_step_id,
            state.current_step_index,
            state.current_employee_id,
            state.last_failure_category,
        ) != (
            result.workflow_id,
            result.current_step_id,
            result.current_step_index,
            result.current_employee_id,
            result.failure_category,
        ):
            _raise("terminal_contract")
        _unchanged(state_path, events_path, original)
        return result
    _prepared(result, workflow, employee)
    state, _ = _history(workflow, state_path, events_path)
    if not (
        state.status == "succeeded"
        and state.current_step_index == result.step_index - 1
    ):
        _raise("terminal_contract")
    try:
        value = start_routing_function(
            result, workflow, employee, state_path, events_path
        )
    except PreparedStepStartRoutingError:
        _restore(state_path, events_path, original)
        raise
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    _unchanged(state_path, events_path, original)
    if type(value) is not PreparedStepExecutionStart:
        _raise("start_contract")
    request, running = value.request, value.running_state
    if not (
        type(request) is ModelInvocationRequest
        and request.model == result.model
        and request.system_instructions == result.employee_instructions
        and request.task_instructions == result.step_instructions
        and request.allowed_tools == result.allowed_tool_names
        and running.workflow_id == result.workflow_id
        and running.status == "running"
        and (
            running.current_step_id,
            running.current_step_index,
            running.current_employee_id,
        )
        == (result.step_id, result.step_index, result.employee_id)
        and running.completed_step_ids == state.completed_step_ids
        and running.last_failure_category is None
    ):
        _raise("start_contract")
    return value


def _prepared(
    value: PreparedWorkflowStep,
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
) -> None:
    if type(value.step_index) is not int or not 2 <= value.step_index <= len(
        workflow.steps
    ):
        _raise("prepared_step_contract")
    step = workflow.steps[value.step_index - 1]
    if not (
        value.workflow_id == workflow.id
        and value.step_id == step.id
        and value.employee_id == step.employee == employee.id
        and value.employee_instructions == employee.instructions
        and value.step_instructions == step.instructions
        and value.model == employee.model
        and value.allowed_tool_names == tuple(employee.allowed_tools)
    ):
        _raise("prepared_step_contract")


def _completion(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        value.decision == "workflow_complete"
        and (
            value.workflow_id,
            value.current_step_id,
            value.current_step_index,
            value.current_employee_id,
        )
        == (workflow.id, final.id, len(workflow.steps), final.employee)
        and value.next_step_id
        is value.next_step_index
        is value.next_employee_id
        is None
        and value.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if not (
        value.outcome == "persisted_failure"
        and value.workflow_id == workflow.id
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
        and value.current_step_id == workflow.steps[value.current_step_index - 1].id
        and value.current_employee_id
        == workflow.steps[value.current_step_index - 1].employee
        and value.failure_category in _CATEGORIES
    ):
        _raise("failure_contract")


def _history(workflow: WorkflowDefinition, state: Path, events: Path):
    try:
        return _load_terminal_history(workflow, state, events)
    except PreparedStepStartBridgeError:
        raise
    except Exception:
        _raise("terminal_contract")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("terminal_contract")


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, before in ((state, original[0]), (events, original[1])):
        try:
            if not path.is_file() or path.read_bytes() != before:
                path.write_bytes(before)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _unchanged(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    try:
        changed = (
            not state.is_file()
            or not events.is_file()
            or state.read_bytes() != original[0]
            or events.read_bytes() != original[1]
        )
    except OSError:
        changed = True
    if changed:
        _restore(state, events, original)
        _raise("dependency_error")


def _raise(classification: Classification) -> None:
    raise PreparedStepStartBridgeCompatibilityError(classification) from None
