"""Bridge one exact Phase 48 result to the existing Phase 42 boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_running_execution_routing_reentry import (
    PersistedRunningExecutionRoutingError,
    route_persisted_running_execution_reentry,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    is_valid_step_runtime_execution_result,
)
from ai_office.storage import RunningStatePersistenceResult

Classification = Literal[
    "result_type",
    "workflow_definition",
    "execution_inputs",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "persistence_contract",
    "execution_contract",
    "dependency_error",
    "dependency_rollback",
]
_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
Function = Callable[
    ...,
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision,
]


@dataclass(frozen=True)
class PersistedRunningExecutionBridgeFailureDetail:
    classification: Classification


class PersistedRunningExecutionBridgeError(ValueError):
    pass


class PersistedRunningExecutionBridgeCompatibilityError(
    PersistedRunningExecutionBridgeError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__("persisted-running execution bridge inputs are incompatible")
        self.detail = PersistedRunningExecutionBridgeFailureDetail(classification)


def route_persisted_running_execution_bridge_reentry(
    result: object,
    start: object | None,
    workflow: object,
    employee: object | None,
    state_path: object,
    events_path: object,
    resolved_tools: object | None,
    api_key: object | None,
    approval: object | None,
    transport: object | None,
    *,
    execution_routing_function: Function = route_persisted_running_execution_reentry,
) -> (
    StepRuntimeExecutionSuccess
    | StepRuntimeExecutionFailure
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    if type(result) not in {
        RunningStatePersistenceResult,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    }:
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(execution_routing_function):
        _raise("execution_contract")
    if type(result) is WorkflowProgressionDecision:
        _completion(result, workflow)
        _none(start, employee, resolved_tools, api_key, approval, transport)
    elif type(result) is PersistedExecutionOutcome:
        _failure(result, workflow)
        _none(start, employee, resolved_tools, api_key, approval, transport)
    _targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _terminal(result, workflow, state_path, events_path, "succeeded")
        return result
    if type(result) is PersistedExecutionOutcome:
        _terminal(result, workflow, state_path, events_path, "failed")
        return result
    try:
        value = execution_routing_function(
            result,
            start,
            workflow,
            employee,
            state_path,
            events_path,
            resolved_tools,
            api_key,
            approval,
            transport,
        )
    except PersistedRunningExecutionRoutingError as error:
        _restore(state_path, events_path, original)
        raise error
    except Exception:
        _restore(state_path, events_path, original)
        _raise("dependency_error")
    if _changed(state_path, original[0]) or _changed(events_path, original[1]):
        _restore(state_path, events_path, original)
        _raise("execution_contract")
    if type(value) not in {StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure}:
        _raise("execution_contract")
    if not is_valid_step_runtime_execution_result(
        value,
        workflow_id=value.workflow_id,
        step_id=value.step_id,
        step_index=value.step_index,
        employee_id=value.employee_id,
    ):
        _raise("execution_contract")
    return value


def _none(*values: object | None) -> None:
    if any(value is not None for value in values):
        _raise("execution_inputs")


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
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
        and value.workflow_id == workflow.id
        and value.current_step_id == workflow.steps[value.current_step_index - 1].id
        and value.current_employee_id
        == workflow.steps[value.current_step_index - 1].employee
        and value.failure_category in _CATEGORIES
    ):
        _raise("failure_contract")


def _targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _raise("state_target")
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("dependency_error")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("dependency_error")


def _terminal(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    status: str,
) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except TerminalHistoryContractError:
        _raise("terminal_contract")
    if persisted.status != status or (
        persisted.workflow_id,
        persisted.current_step_id,
        persisted.current_step_index,
        persisted.current_employee_id,
    ) != (
        value.workflow_id,
        value.current_step_id,
        value.current_step_index,
        value.current_employee_id,
    ):
        _raise("terminal_contract")
    if (
        type(value) is PersistedExecutionOutcome
        and persisted.last_failure_category != value.failure_category
    ):
        _raise("terminal_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, before in ((state, original[0]), (events, original[1])):
        try:
            if _changed(path, before):
                path.write_bytes(before)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _raise(classification: Classification) -> None:
    raise PersistedRunningExecutionBridgeCompatibilityError(classification) from None
