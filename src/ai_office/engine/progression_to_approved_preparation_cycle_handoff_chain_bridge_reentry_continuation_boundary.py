"""Phase 130 progression-to-preparation bridge with a strict public handoff."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationError as Phase123Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary import (
    route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary,
)
from ai_office.engine.terminal_history_contract import load_strict_terminal_history
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState

Classification = Literal[
    "result_type",
    "workflow_definition",
    "decision_contract",
    "completion_contract",
    "failure_contract",
    "approval_contract",
    "employee_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "prepared_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase123Function = Callable[
    [object, object, object, object, object, object],
    PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationFailureDetail:
    """Safe classification for one Phase 130 compatibility failure."""

    classification: Classification


class ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationError(
    ValueError
):
    """Raised when one Phase 129 result cannot cross the Phase 130 bridge."""


class ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationCompatibilityError(
    ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationError
):
    """Raised for a detail-safe Phase 130 compatibility rejection."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "progression to approved preparation cycle handoff chain bridge inputs are incompatible"
        )
        self.detail = ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationFailureDetail(
            classification
        )


def route_progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary(
    result: object,
    workflow: object,
    approval: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase123_function: Phase123Function = (
        route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary
    ),
) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Bridge one exact Phase 129 result through public Phase 123 once."""
    _check_inputs(result, workflow, state_path, events_path, phase123_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is WorkflowProgressionDecision:
        if result.decision == "prepare_next_step":
            _check_prepare(result, workflow, approval, employee)
            terminal_status: Literal["succeeded", "failed"] = "succeeded"
            require_openai = True
            stop = False
        elif result.decision == "workflow_complete":
            _check_completion(result, workflow, approval, employee)
            terminal_status = "succeeded"
            require_openai = False
            stop = True
        else:
            _fail("decision_contract")
    else:
        assert type(result) is PersistedExecutionOutcome
        _check_failure(result, workflow, approval, employee)
        terminal_status = "failed"
        require_openai = False
        stop = True

    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)
    _check_terminal(
        result,
        workflow,
        state_path,
        events_path,
        terminal_status,
        require_openai,
    )
    _require_unchanged(state_path, events_path, original, "terminal_contract")

    if stop:
        return result

    assert type(result) is WorkflowProgressionDecision
    assert type(approval) is NextStepPreparationApproval
    assert type(employee) is EmployeeDefinition
    try:
        prepared = phase123_function(
            result, workflow, approval, employee, state_path, events_path
        )
    except Phase123Error as error:
        _compensate_dependency_error(state_path, events_path, original, error)
    except Exception:
        _compensate_dependency_error(state_path, events_path, original, None)

    _require_unchanged(state_path, events_path, original, "prepared_contract")
    _check_prepared(prepared, result, workflow, approval, employee)
    return prepared


def _check_inputs(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    dependency: object,
) -> None:
    if type(result) not in {WorkflowProgressionDecision, PersistedExecutionOutcome}:
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _fail("workflow_definition")
    if type(state_path) is not _PATH_TYPE:
        _fail("state_target")
    if type(events_path) is not _PATH_TYPE:
        _fail("event_target")
    if state_path == events_path:
        _fail("target_conflict")
    if not callable(dependency):
        _fail("dependency_error")


def _valid_workflow(workflow: WorkflowDefinition) -> bool:
    if (
        type(workflow.id) is not str
        or not workflow.id
        or type(workflow.name) is not str
        or not workflow.name
        or type(workflow.description) is not str
        or not workflow.description
        or type(workflow.steps) is not list
        or not workflow.steps
    ):
        return False
    if any(
        type(step) is not WorkflowStepDefinition
        or not _nonempty_string(step.id)
        or not _nonempty_string(step.name)
        or not _nonempty_string(step.employee)
        or not _nonempty_string(step.instructions)
        for step in workflow.steps
    ):
        return False
    step_ids = tuple(step.id for step in workflow.steps)
    return len(step_ids) == len(set(step_ids))


def _check_prepare(
    result: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    approval: object,
    employee: object,
) -> None:
    if type(approval) is not NextStepPreparationApproval:
        _fail("approval_contract")
    if type(employee) is not EmployeeDefinition or not _valid_employee(employee):
        _fail("employee_contract")
    current_index = result.current_step_index
    next_index = result.next_step_index
    if (
        type(current_index) is not int
        or current_index < 3
        or current_index >= len(workflow.steps)
        or type(next_index) is not int
        or next_index != current_index + 1
    ):
        _fail("decision_contract")
    current = workflow.steps[current_index - 1]
    next_step = workflow.steps[next_index - 1]
    if employee.id != next_step.employee:
        _fail("employee_contract")
    if not (
        _exact_string(result.decision, "prepare_next_step")
        and _exact_string(result.workflow_id, workflow.id)
        and _exact_string(result.current_step_id, current.id)
        and _exact_string(result.current_employee_id, current.employee)
        and _exact_string(result.next_step_id, next_step.id)
        and _exact_string(result.next_employee_id, next_step.employee)
        and _exact_string(result.reason, "next_step_available")
    ):
        _fail("decision_contract")
    if not (
        type(approval.approved) is bool
        and approval.approved is True
        and _exact_string(approval.workflow_id, result.workflow_id)
        and _exact_string(approval.current_step_id, result.current_step_id)
        and type(approval.current_step_index) is int
        and approval.current_step_index == result.current_step_index
        and _exact_string(approval.next_step_id, result.next_step_id)
        and type(approval.next_step_index) is int
        and approval.next_step_index == result.next_step_index
        and _exact_string(approval.next_employee_id, result.next_employee_id)
    ):
        _fail("approval_contract")


def _check_completion(
    result: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    approval: object,
    employee: object,
) -> None:
    final = workflow.steps[-1]
    if not (
        approval is None
        and employee is None
        and _exact_string(result.decision, "workflow_complete")
        and _exact_string(result.workflow_id, workflow.id)
        and _exact_string(result.current_step_id, final.id)
        and type(result.current_step_index) is int
        and result.current_step_index == len(workflow.steps)
        and _exact_string(result.current_employee_id, final.employee)
        and result.next_step_id is None
        and result.next_step_index is None
        and result.next_employee_id is None
        and _exact_string(result.reason, "last_step_succeeded")
    ):
        _fail("completion_contract")


def _check_failure(
    result: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    approval: object,
    employee: object,
) -> None:
    index = result.current_step_index
    if not (
        approval is None
        and employee is None
        and _exact_string(result.outcome, "persisted_failure")
        and _nonempty_string(result.workflow_id)
        and result.workflow_id == workflow.id
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and _exact_string(result.current_step_id, workflow.steps[index - 1].id)
        and _exact_string(result.current_employee_id, workflow.steps[index - 1].employee)
        and type(result.failure_category) is str
        and result.failure_category in _FAILURE_CATEGORIES
    ):
        _fail("failure_contract")


def _valid_employee(employee: EmployeeDefinition) -> bool:
    return (
        _nonempty_string(employee.id)
        and _nonempty_string(employee.name)
        and _nonempty_string(employee.role)
        and _nonempty_string(employee.instructions)
        and _nonempty_string(employee.model)
        and type(employee.allowed_tools) is list
        and all(_nonempty_string(tool) for tool in employee.allowed_tools)
    )


def _check_targets(state_path: Path, events_path: Path) -> None:
    for path, classification in (
        (state_path, "state_target"),
        (events_path, "event_target"),
    ):
        try:
            if not path.is_file():
                _fail(classification)
        except OSError:
            _fail(classification)


def _capture_targets(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state_path.read_bytes()
    except OSError:
        _fail("state_target")
    try:
        event_bytes = events_path.read_bytes()
    except OSError:
        _fail("event_target")
    return state_bytes, event_bytes


def _check_terminal(
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    expected_status: Literal["succeeded", "failed"],
    require_openai: bool,
) -> None:
    try:
        state, history = load_strict_terminal_history(workflow, state_path, events_path)
    except Exception:
        _fail("terminal_contract")
    expected_failure = (
        result.failure_category if type(result) is PersistedExecutionOutcome else None
    )
    if not _valid_terminal_history(
        state,
        history,
        workflow,
        result,
        expected_status,
        expected_failure,
        require_openai,
    ):
        _fail("terminal_contract")


def _valid_terminal_history(
    state: object,
    history: object,
    workflow: WorkflowDefinition,
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    expected_status: Literal["succeeded", "failed"],
    expected_failure: object,
    require_openai: bool,
) -> bool:
    if type(state) is not WorkflowExecutionState or type(history) is not tuple or not history:
        return False
    if not _valid_state_shape(state):
        return False
    if (
        state.status != expected_status
        or state.workflow_id != result.workflow_id
        or state.current_step_id != result.current_step_id
        or state.current_step_index != result.current_step_index
        or state.current_employee_id != result.current_employee_id
        or state.last_failure_category != expected_failure
    ):
        return False
    if state.status == "succeeded":
        expected_completed = tuple(
            step.id for step in workflow.steps[: state.current_step_index]
        )
    else:
        expected_completed = tuple(
            step.id for step in workflow.steps[: state.current_step_index - 1]
        )
    if state.completed_step_ids != expected_completed:
        return False
    prior_steps = workflow.steps[: state.current_step_index - 1]
    if len(history) != len(prior_steps) + 1:
        return False
    if any(type(event) is not RuntimeStepEvent for event in history):
        return False
    for position, (event, step) in enumerate(
        zip(history[:-1], prior_steps, strict=True), 1
    ):
        if not _valid_event_shape(event) or not _valid_predecessor(
            event, step, position, state
        ):
            return False
    return _valid_event_shape(history[-1]) and _valid_terminal_event(
        history[-1], state, expected_failure, require_openai
    )


def _valid_state_shape(state: WorkflowExecutionState) -> bool:
    return (
        _nonempty_string(state.workflow_id)
        and type(state.status) is str
        and state.status in {"succeeded", "failed"}
        and _nonempty_string(state.current_step_id)
        and type(state.current_step_index) is int
        and _nonempty_string(state.current_employee_id)
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(step_id) for step_id in state.completed_step_ids)
        and (
            state.last_failure_category is None
            or (
                type(state.last_failure_category) is str
                and state.last_failure_category in _FAILURE_CATEGORIES
            )
        )
    )


def _valid_event_shape(event: RuntimeStepEvent) -> bool:
    return (
        type(event.event_type) is str
        and event.event_type in {"step_succeeded", "step_failed"}
        and _nonempty_string(event.workflow_id)
        and _nonempty_string(event.step_id)
        and type(event.step_index) is int
        and _nonempty_string(event.employee_id)
        and type(event.previous_status) is str
        and event.previous_status in {"running", "ready", "succeeded", "failed"}
        and type(event.next_status) is str
        and event.next_status in {"running", "succeeded", "failed"}
        and _nonempty_string(event.provider)
        and (
            event.failure_category is None
            or (
                type(event.failure_category) is str
                and event.failure_category in _FAILURE_CATEGORIES
            )
        )
        and _optional_text(event.response_id)
        and _optional_nonempty_text(event.request_id)
        and _optional_text(event.output_text)
        and _optional_text(event.message)
    )


def _valid_predecessor(
    event: RuntimeStepEvent,
    step: WorkflowStepDefinition,
    position: int,
    state: WorkflowExecutionState,
) -> bool:
    return (
        event.event_type == "step_succeeded"
        and event.workflow_id == state.workflow_id
        and event.step_id == step.id
        and event.step_index == position
        and event.employee_id == step.employee
        and event.previous_status == "running"
        and event.next_status == "succeeded"
        and event.failure_category is None
        and _nonempty_string(event.response_id)
        and _nonempty_string(event.output_text)
        and event.message is None
    )


def _valid_terminal_event(
    event: RuntimeStepEvent,
    state: WorkflowExecutionState,
    expected_failure: object,
    require_openai: bool,
) -> bool:
    base = (
        event.workflow_id == state.workflow_id
        and event.step_id == state.current_step_id
        and event.step_index == state.current_step_index
        and event.employee_id == state.current_employee_id
        and event.previous_status == "running"
    )
    if state.status == "succeeded":
        return (
            base
            and event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and (not require_openai or event.provider == "openai")
            and expected_failure is None
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and _nonempty_string(event.output_text)
            and event.message is None
        )
    return (
        base
        and event.event_type == "step_failed"
        and event.next_status == "failed"
        and event.failure_category == expected_failure
        and event.response_id is None
        and event.output_text is None
        and _nonempty_string(event.message)
    )


def _check_prepared(
    prepared: object,
    result: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    approval: NextStepPreparationApproval,
    employee: EmployeeDefinition,
) -> None:
    if type(prepared) is not PreparedWorkflowStep:
        _fail("prepared_contract")
    assert type(result.next_step_index) is int
    step = workflow.steps[result.next_step_index - 1]
    if not (
        approval.approved is True
        and _exact_string(prepared.workflow_id, workflow.id)
        and _exact_string(prepared.workflow_id, approval.workflow_id)
        and _exact_string(prepared.step_id, step.id)
        and _exact_string(prepared.step_id, approval.next_step_id)
        and type(prepared.step_index) is int
        and prepared.step_index == result.next_step_index == approval.next_step_index
        and _exact_string(prepared.employee_id, employee.id)
        and _exact_string(prepared.employee_id, approval.next_employee_id)
        and _exact_string(prepared.employee_instructions, employee.instructions)
        and _exact_string(prepared.step_instructions, step.instructions)
        and _nonempty_string(prepared.model)
        and prepared.model == employee.model
        and type(prepared.allowed_tool_names) is tuple
        and all(_nonempty_string(tool) for tool in prepared.allowed_tool_names)
        and prepared.allowed_tool_names == tuple(employee.allowed_tools)
    ):
        _fail("prepared_contract")


def _changed(state_path: Path, events_path: Path, original: tuple[bytes, bytes]) -> bool:
    try:
        return (
            not state_path.is_file()
            or not events_path.is_file()
            or state_path.read_bytes() != original[0]
            or events_path.read_bytes() != original[1]
        )
    except OSError:
        return True


def _restore_or_fail(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    failed = False
    for path, contents in ((state_path, original[0]), (events_path, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if not failed and _changed(state_path, events_path, original):
        failed = True
    if failed:
        _fail("dependency_rollback")


def _require_unchanged(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state_path, events_path, original):
        _restore_or_fail(state_path, events_path, original)
        _fail(classification)


def _compensate_dependency_error(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    safe_error: Phase123Error | None,
) -> None:
    if _changed(state_path, events_path, original):
        _restore_or_fail(state_path, events_path, original)
    if safe_error is not None:
        raise safe_error
    _fail("dependency_error")


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _optional_text(value: object) -> bool:
    return value is None or type(value) is str


def _optional_nonempty_text(value: object) -> bool:
    return value is None or _nonempty_string(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _fail(classification: Classification) -> None:
    raise ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationCompatibilityError(
        classification
    ) from None
