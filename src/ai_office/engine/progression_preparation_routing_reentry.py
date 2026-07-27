"""Read-only bridge from one Phase 45 result to the Phase 39 boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_success_preparation_routing_reentry import (
    PersistedSuccessPreparationRoutingError,
    route_persisted_success_progression_reentry,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
)
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
)

RoutingClassification = Literal[
    "result_type",
    "workflow_definition",
    "approval_contract",
    "employee_contract",
    "completion_contract",
    "failure_contract",
    "progression_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "preparation_contract",
    "dependency_error",
    "dependency_rollback",
]
_ERROR_MESSAGE = "progression preparation routing inputs are incompatible"
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
PreparationRoutingFunction = Callable[
    ..., PreparedWorkflowStep | WorkflowProgressionDecision
]


@dataclass(frozen=True)
class ProgressionPreparationRoutingFailureDetail:
    classification: RoutingClassification


class ProgressionPreparationRoutingError(ValueError):
    """Raised when the Phase 46 routing boundary cannot proceed safely."""


class ProgressionPreparationRoutingCompatibilityError(
    ProgressionPreparationRoutingError
):
    def __init__(self, classification: RoutingClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = ProgressionPreparationRoutingFailureDetail(classification)


def route_progression_preparation_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    approval: object,
    employee: object,
    *,
    preparation_routing_function: PreparationRoutingFunction = (
        route_persisted_success_progression_reentry
    ),
) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route exactly one Phase 45 result; this boundary never writes targets."""
    _validate_inputs(
        result,
        workflow,
        state_path,
        events_path,
        approval,
        employee,
        preparation_routing_function,
    )
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    if type(result) is WorkflowProgressionDecision:
        if result.decision == "workflow_complete":
            _validate_completion(result, workflow, approval, employee)
        else:
            _validate_progression(result, workflow, approval, employee)
    else:
        assert type(result) is PersistedExecutionOutcome
        _validate_failure(result, workflow, approval, employee)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if (
        type(result) is WorkflowProgressionDecision
        and result.decision == "workflow_complete"
    ):
        _require_unchanged(state_path, events_path, original)
        return result
    if type(result) is PersistedExecutionOutcome:
        state, events = _load_terminal_history(workflow, state_path, events_path)
        if not _matches_failure(result, state):
            _raise("terminal_contract")
        _require_unchanged(state_path, events_path, original)
        return result
    _load_succeeded_history(workflow, result, state_path, events_path)
    try:
        value = preparation_routing_function(
            result, workflow, state_path, events_path, approval, employee
        )
    except PersistedSuccessPreparationRoutingError:
        _restore_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_changed(state_path, events_path, original)
        _raise("dependency_error")
    _require_unchanged(state_path, events_path, original)
    _validate_prepared(value, workflow, result, approval, employee)
    return value


def _validate_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    approval: object,
    employee: object,
    function: object,
) -> None:
    if type(result) not in (WorkflowProgressionDecision, PersistedExecutionOutcome):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if type(approval) is not NextStepPreparationApproval:
        _raise("approval_contract")
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    if not isinstance(state, Path):
        _raise("state_target")
    if not isinstance(events, Path):
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(function):
        _raise("preparation_contract")


def _validate_completion(
    value: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    approval: object,
    employee: object,
) -> None:
    final = workflow.steps[-1]
    if not (
        value.decision == "workflow_complete"
        and value.workflow_id == workflow.id
        and value.current_step_id == final.id
        and value.current_step_index == len(workflow.steps)
        and value.current_employee_id == final.employee
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and value.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _validate_progression(
    value: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    approval: object,
    employee: object,
) -> None:
    if (
        value.decision != "prepare_next_step"
        or type(approval) is not NextStepPreparationApproval
    ):
        _raise("progression_contract")
    if type(employee) is not EmployeeDefinition:
        _raise("employee_contract")
    if not 1 <= value.current_step_index < len(workflow.steps):
        _raise("progression_contract")
    current, next_step = (
        workflow.steps[value.current_step_index - 1],
        workflow.steps[value.current_step_index],
    )
    if not (
        value.workflow_id == workflow.id
        and value.current_step_id == current.id
        and value.current_employee_id == current.employee
        and value.next_step_id == next_step.id
        and value.next_step_index == value.current_step_index + 1
        and value.next_employee_id == next_step.employee
        and value.reason == "next_step_available"
        and employee.id == next_step.employee
        and approval.approved is True
        and (
            approval.workflow_id,
            approval.current_step_id,
            approval.current_step_index,
            approval.next_step_id,
            approval.next_step_index,
            approval.next_employee_id,
        )
        == (
            value.workflow_id,
            value.current_step_id,
            value.current_step_index,
            value.next_step_id,
            value.next_step_index,
            value.next_employee_id,
        )
    ):
        _raise("progression_contract")


def _validate_failure(
    value: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    approval: object,
    employee: object,
) -> None:
    if not (
        value.outcome == "persisted_failure"
        and value.workflow_id == workflow.id
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
        and value.current_step_id == workflow.steps[value.current_step_index - 1].id
        and value.current_employee_id
        == workflow.steps[value.current_step_index - 1].employee
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _raise("failure_contract")


def _validate_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _raise("state_target")
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("terminal_contract")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        return state.read_bytes(), events.read_bytes()
    except OSError:
        _raise("terminal_contract")


def _load_terminal_history(
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
    except (
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _raise("terminal_contract")
    _validate_terminal_history(workflow, history.state, history.events)
    return history.state, history.events


def _load_succeeded_history(
    workflow: WorkflowDefinition,
    decision: WorkflowProgressionDecision,
    state: Path,
    events: Path,
) -> None:
    loaded_state, _ = _load_terminal_history(workflow, state, events)
    if not (
        loaded_state.status == "succeeded"
        and loaded_state.current_step_id == decision.current_step_id
        and loaded_state.current_step_index == decision.current_step_index
        and loaded_state.current_employee_id == decision.current_employee_id
    ):
        _raise("terminal_contract")


def _validate_terminal_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> None:
    if (
        state.status not in {"succeeded", "failed"}
        or state.workflow_id != workflow.id
        or not events
    ):
        _raise("terminal_contract")
    if not 1 <= state.current_step_index <= len(workflow.steps):
        _raise("terminal_contract")
    current = workflow.steps[state.current_step_index - 1]
    if (
        current.id != state.current_step_id
        or current.employee != state.current_employee_id
    ):
        _raise("terminal_contract")
    expected_ids = (
        tuple(step.id for step in workflow.steps[: state.current_step_index])
        if state.status == "succeeded"
        else tuple(step.id for step in workflow.steps[: state.current_step_index - 1])
    )
    if state.completed_step_ids != expected_ids:
        _raise("terminal_contract")
    prior_ids = expected_ids[:-1] if state.status == "succeeded" else expected_ids
    if len(events) != len(prior_ids) + 1:
        _raise("terminal_contract")
    for event, step_id in zip(events[:-1], prior_ids, strict=True):
        step_index = next(
            (
                index
                for index, step in enumerate(workflow.steps, 1)
                if step.id == step_id
            ),
            0,
        )
        if not (
            event.event_type == "step_succeeded"
            and event.workflow_id == state.workflow_id
            and event.step_id == step_id
            and event.step_index == step_index
            and event.employee_id == workflow.steps[step_index - 1].employee
            and event.previous_status == "running"
            and event.next_status == "succeeded"
        ):
            _raise("terminal_contract")
    event = events[-1]
    base = (
        event.workflow_id == state.workflow_id
        and event.step_id == state.current_step_id
        and event.step_index == state.current_step_index
        and event.employee_id == state.current_employee_id
        and event.previous_status == "running"
    )
    if state.status == "succeeded":
        valid = (
            base
            and event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and state.last_failure_category is None
            and event.failure_category is None
            and isinstance(event.response_id, str)
            and isinstance(event.output_text, str)
            and event.message is None
        )
    else:
        valid = (
            base
            and event.event_type == "step_failed"
            and event.next_status == "failed"
            and state.last_failure_category in _FAILURE_CATEGORIES
            and event.failure_category == state.last_failure_category
            and event.response_id is None
            and event.output_text is None
            and isinstance(event.message, str)
        )
    if not valid:
        _raise("terminal_contract")


def _matches_failure(
    value: PersistedExecutionOutcome, state: WorkflowExecutionState
) -> bool:
    return (
        state.status == "failed"
        and value.workflow_id == state.workflow_id
        and value.current_step_id == state.current_step_id
        and value.current_step_index == state.current_step_index
        and value.current_employee_id == state.current_employee_id
        and value.failure_category == state.last_failure_category
    )


def _validate_prepared(
    value: object,
    workflow: WorkflowDefinition,
    decision: WorkflowProgressionDecision,
    approval: object,
    employee: object,
) -> None:
    if (
        type(value) is not PreparedWorkflowStep
        or type(employee) is not EmployeeDefinition
        or type(approval) is not NextStepPreparationApproval
    ):
        _raise("preparation_contract")
    step = workflow.steps[decision.next_step_index - 1]  # type: ignore[operator]
    if not (
        value.workflow_id == workflow.id
        and value.step_id == step.id
        and value.step_index == decision.next_step_index
        and value.employee_id == employee.id == step.employee
        and value.employee_instructions == employee.instructions
        and value.step_instructions == step.instructions
        and value.model == employee.model
        and value.allowed_tool_names == tuple(employee.allowed_tools)
    ):
        _raise("preparation_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    failed = False
    for path, before in ((state, original[0]), (events, original[1])):
        try:
            if _changed(path, before):
                path.write_bytes(before)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _require_unchanged(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_changed(state, events, original)
        _raise("dependency_error")


def _raise(classification: RoutingClassification) -> None:
    raise ProgressionPreparationRoutingCompatibilityError(classification) from None
