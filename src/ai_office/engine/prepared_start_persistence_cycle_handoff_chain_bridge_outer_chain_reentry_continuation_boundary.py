"""Phase 147 prepared-start persistence cycle handoff chain bridge outer-chain boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase139Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_workflow_execution_state_json,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "start_contract",
    "employee_contract",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "persistence_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase139Function = Callable[
    [object, object, object, object, object],
    RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationFailureDetail:
    """Detail-safe classification for one Phase 147 rejection."""

    classification: Classification


class PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError(
    ValueError
):
    """Raised when the Phase 147 boundary cannot safely continue."""


class PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError(
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError
):
    """Raised with a detail-safe classification."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "prepared-start persistence cycle handoff chain bridge outer-chain inputs are incompatible"
        )
        self.detail = (
            PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationFailureDetail(
                classification
            )
        )


def route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
    result: object,
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase139_function: Phase139Function = (
        route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    ),
) -> RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Handoff one exact Phase 146 continuation result through public Phase 139 once."""
    _check_inputs(result, workflow, state_path, events_path, phase139_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is PreparedStepExecutionStart:
        _check_employee(employee, result)
        assert type(employee) is EmployeeDefinition
        _check_start(result, workflow, employee)
        stop = False
    elif type(result) is WorkflowProgressionDecision:
        _check_completion(result, workflow, employee)
        stop = True
    else:
        assert type(result) is PersistedExecutionOutcome
        _check_failure(result, workflow, employee)
        stop = True

    _check_targets(state_path, events_path)
    original = _snapshot(state_path, events_path)

    if stop:
        assert type(result) in (WorkflowProgressionDecision, PersistedExecutionOutcome)
        expected_status: Literal["succeeded", "failed"] = (
            "succeeded" if type(result) is WorkflowProgressionDecision else "failed"
        )
        expected_failure = (
            None if type(result) is WorkflowProgressionDecision else result.failure_category
        )
        _check_terminal(
            result,
            workflow,
            state_path,
            events_path,
            expected_status,
            expected_failure,
            require_immediate_openai=False,
            allow_empty_success_output=False,
            allow_empty_predecessor_output=True,
        )
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is PreparedStepExecutionStart
    assert type(employee) is EmployeeDefinition
    predecessor = _check_predecessor(result, workflow, state_path, events_path)
    try:
        value = phase139_function(result, workflow, employee, state_path, events_path)
    except Phase139Error as error:
        _compensate_dependency_error(state_path, events_path, original, error)
    except Exception:
        _compensate_dependency_error(state_path, events_path, original, None)

    try:
        _check_persistence(value, result, state_path, events_path, original)
    except PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore_if_changed(state_path, events_path, original)
        raise
    _check_predecessor_identity(predecessor, result, workflow)
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    dependency: object,
) -> None:
    if type(result) not in (
        PreparedStepExecutionStart,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
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
        _fail("persistence_contract")


def _valid_workflow(workflow: WorkflowDefinition) -> bool:
    if not (
        _nonempty_string(workflow.id)
        and _nonempty_string(workflow.name)
        and _nonempty_string(workflow.description)
        and type(workflow.steps) is list
        and bool(workflow.steps)
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
    ids = tuple(step.id for step in workflow.steps)
    return len(ids) == len(set(ids))


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


def _check_employee(value: object, start: PreparedStepExecutionStart) -> None:
    if type(value) is not EmployeeDefinition:
        _fail("employee_contract")
    assert type(value) is EmployeeDefinition
    if not _valid_employee(value) or value.id != start.running_state.current_employee_id:
        _fail("employee_contract")


def _check_start(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    employee: EmployeeDefinition,
) -> None:
    request, state = start.request, start.running_state
    if type(request) is not ModelInvocationRequest or type(state) is not WorkflowExecutionState:
        _fail("start_contract")
    index = state.current_step_index
    if type(index) is not int or not 6 <= index <= len(workflow.steps):
        _fail("start_contract")
    step = workflow.steps[index - 1]
    expected_completed = tuple(item.id for item in workflow.steps[: index - 1])
    if not (
        _exact_string(state.workflow_id, workflow.id)
        and _exact_string(state.status, "running")
        and _exact_string(state.current_step_id, step.id)
        and _exact_string(state.current_employee_id, step.employee)
        and _exact_string(state.current_employee_id, employee.id)
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in state.completed_step_ids)
        and state.completed_step_ids == expected_completed
        and state.last_failure_category is None
        and _exact_string(request.model, employee.model)
        and _exact_string(request.system_instructions, employee.instructions)
        and _exact_string(request.task_instructions, step.instructions)
        and type(request.allowed_tools) is tuple
        and all(_nonempty_string(item) for item in request.allowed_tools)
        and request.allowed_tools == tuple(employee.allowed_tools)
    ):
        _fail("start_contract")


def _check_completion(
    value: WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    employee: object,
) -> None:
    final = workflow.steps[-1]
    if not (
        employee is None
        and _exact_string(value.decision, "workflow_complete")
        and _exact_string(value.workflow_id, workflow.id)
        and _exact_string(value.current_step_id, final.id)
        and type(value.current_step_index) is int
        and value.current_step_index == len(workflow.steps)
        and _exact_string(value.current_employee_id, final.employee)
        and value.next_step_id is None
        and value.next_step_index is None
        and value.next_employee_id is None
        and _exact_string(value.reason, "last_step_succeeded")
    ):
        _fail("completion_contract")


def _check_failure(
    value: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    employee: object,
) -> None:
    index = value.current_step_index
    if not (
        employee is None
        and _exact_string(value.outcome, "persisted_failure")
        and _exact_string(value.workflow_id, workflow.id)
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
        and _exact_string(value.current_step_id, workflow.steps[index - 1].id)
        and _exact_string(value.current_employee_id, workflow.steps[index - 1].employee)
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _fail("failure_contract")


def _check_targets(state: Path, events: Path) -> None:
    for path, classification in ((state, "state_target"), (events, "event_target")):
        try:
            if not path.is_file():
                _fail(classification)
        except OSError:
            _fail(classification)


def _snapshot(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _fail("state_target")
    try:
        event_bytes = events.read_bytes()
    except OSError:
        _fail("event_target")
    return state_bytes, event_bytes


def _load_history(
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    try:
        loaded = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state, events)
        )
    except Exception:
        _fail("terminal_contract")
    if type(loaded.state) is not WorkflowExecutionState or type(loaded.events) is not tuple:
        _fail("terminal_contract")
    return loaded.state, loaded.events


def _check_predecessor(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> WorkflowExecutionState:
    persisted, history = _load_history(workflow, state, events)
    if not _valid_history(
        workflow,
        persisted,
        history,
        "succeeded",
        None,
        None,
        require_immediate_openai=True,
        allow_empty_success_output=True,
        allow_empty_predecessor_output=True,
        allow_immediate_none_request_id=(
            start.running_state.current_step_index - 1 >= 6
        ),
        allow_accumulated_openai_none=True,
    ):
        _fail("terminal_contract")
    previous_index = start.running_state.current_step_index - 1
    previous = workflow.steps[previous_index - 1]
    expected_completed = tuple(item.id for item in workflow.steps[:previous_index])
    if not (
        persisted.workflow_id == workflow.id
        and persisted.status == "succeeded"
        and persisted.current_step_id == previous.id
        and persisted.current_step_index == previous_index
        and persisted.current_employee_id == previous.employee
        and persisted.completed_step_ids == expected_completed
        and persisted.last_failure_category is None
    ):
        _fail("terminal_contract")
    return persisted


def _check_terminal(
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    expected_status: Literal["succeeded", "failed"],
    expected_failure: object,
    *,
    require_immediate_openai: bool,
    allow_empty_success_output: bool,
    allow_empty_predecessor_output: bool,
) -> None:
    persisted, history = _load_history(workflow, state, events)
    if not _valid_history(
        workflow,
        persisted,
        history,
        expected_status,
        result,
        expected_failure,
        require_immediate_openai=require_immediate_openai,
        allow_empty_success_output=allow_empty_success_output,
        allow_empty_predecessor_output=allow_empty_predecessor_output,
        allow_immediate_none_request_id=result.current_step_index >= 6,
    ):
        _fail("terminal_contract")


def _valid_history(
    workflow: WorkflowDefinition,
    state: object,
    history: object,
    expected_status: Literal["succeeded", "failed"],
    result: object | None,
    expected_failure: object,
    *,
    require_immediate_openai: bool,
    allow_empty_success_output: bool,
    allow_empty_predecessor_output: bool,
    allow_immediate_none_request_id: bool = False,
    allow_accumulated_openai_none: bool = False,
) -> bool:
    if type(state) is not WorkflowExecutionState or type(history) is not tuple or not history:
        return False
    index = state.current_step_index
    if not (
        type(index) is int
        and _valid_state(state, workflow)
        and state.status == expected_status
        and state.last_failure_category == expected_failure
    ):
        return False
    if result is not None and not (
        _exact_string(result.workflow_id, state.workflow_id)
        and _exact_string(result.current_step_id, state.current_step_id)
        and type(result.current_step_index) is int
        and result.current_step_index == index
        and _exact_string(result.current_employee_id, state.current_employee_id)
    ):
        return False
    expected_completed = (
        tuple(step.id for step in workflow.steps[:index])
        if state.status == "succeeded"
        else tuple(step.id for step in workflow.steps[: index - 1])
    )
    if state.completed_step_ids != expected_completed:
        return False
    prior_steps = workflow.steps[: index - 1]
    if len(history) != len(prior_steps) + 1:
        return False
    if any(type(event) is not RuntimeStepEvent for event in history):
        return False
    for position, (event, step) in enumerate(zip(history[:-1], prior_steps, strict=True), 1):
        if not _valid_predecessor(
            event,
            step,
            position,
            state,
            require_openai=require_immediate_openai and position == len(prior_steps),
            allow_empty_output=allow_empty_predecessor_output,
            allow_none_request_id=(
                (
                    allow_immediate_none_request_id and position == len(prior_steps)
                )
                or (
                    allow_accumulated_openai_none
                    and event.request_id is None
                    and position >= 5
                    and type(event.provider) is str
                    and event.provider == "openai"
                    and type(state.current_step_index) is int
                    and state.current_step_index >= 7
                )
            ),
        ):
            return False
    return _valid_terminal_event(
        history[-1],
        state,
        expected_failure,
        require_openai=require_immediate_openai,
        allow_empty_success_output=allow_empty_success_output,
    )


def _valid_state(state: WorkflowExecutionState, workflow: WorkflowDefinition) -> bool:
    index = state.current_step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    current = workflow.steps[index - 1]
    expected_completed = (
        tuple(step.id for step in workflow.steps[:index])
        if state.status == "succeeded"
        else tuple(step.id for step in workflow.steps[: index - 1])
    )
    return (
        _exact_string(state.workflow_id, workflow.id)
        and type(state.status) is str
        and state.status in {"succeeded", "failed"}
        and _exact_string(state.current_step_id, current.id)
        and _exact_string(state.current_employee_id, current.employee)
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in state.completed_step_ids)
        and state.completed_step_ids == expected_completed
        and (
            state.last_failure_category is None
            or (
                type(state.last_failure_category) is str
                and state.last_failure_category in _FAILURE_CATEGORIES
            )
        )
    )


def _valid_predecessor(
    event: RuntimeStepEvent,
    step: WorkflowStepDefinition,
    position: int,
    state: WorkflowExecutionState,
    *,
    require_openai: bool,
    allow_empty_output: bool = False,
    allow_none_request_id: bool = False,
) -> bool:
    return (
        type(event) is RuntimeStepEvent
        and _exact_string(event.event_type, "step_succeeded")
        and _exact_string(event.workflow_id, state.workflow_id)
        and _exact_string(event.step_id, step.id)
        and type(event.step_index) is int
        and event.step_index == position
        and _exact_string(event.employee_id, step.employee)
        and _exact_string(event.previous_status, "running")
        and _exact_string(event.next_status, "succeeded")
        and _nonempty_string(event.provider)
        and (not require_openai or event.provider == "openai")
        and event.failure_category is None
        and _nonempty_string(event.response_id)
        and (
            (event.request_id is None and allow_none_request_id)
            or _nonempty_string(event.request_id)
        )
        and type(event.output_text) is str
        and (allow_empty_output or bool(event.output_text))
        and event.message is None
    )


def _valid_terminal_event(
    event: RuntimeStepEvent,
    state: WorkflowExecutionState,
    expected_failure: object,
    *,
    require_openai: bool,
    allow_empty_success_output: bool,
) -> bool:
    base = (
        type(event) is RuntimeStepEvent
        and _exact_string(event.workflow_id, state.workflow_id)
        and _exact_string(event.step_id, state.current_step_id)
        and type(event.step_index) is int
        and event.step_index == state.current_step_index
        and _exact_string(event.employee_id, state.current_employee_id)
        and _exact_string(event.previous_status, "running")
        and _nonempty_string(event.provider)
        and (not require_openai or event.provider == "openai")
        and (event.request_id is None or _nonempty_string(event.request_id))
    )
    if state.status == "succeeded":
        return (
            base
            and _exact_string(event.event_type, "step_succeeded")
            and _exact_string(event.next_status, "succeeded")
            and expected_failure is None
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and type(event.output_text) is str
            and (allow_empty_success_output or bool(event.output_text))
            and event.message is None
        )
    return (
        base
        and _exact_string(event.event_type, "step_failed")
        and _exact_string(event.next_status, "failed")
        and type(expected_failure) is str
        and expected_failure in _FAILURE_CATEGORIES
        and event.failure_category == expected_failure
        and event.response_id is None
        and event.output_text is None
        and _nonempty_string(event.message)
    )


def _check_persistence(
    value: object,
    start: PreparedStepExecutionStart,
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
) -> None:
    try:
        state_bytes = state.read_bytes()
        event_bytes = events.read_bytes()
        loaded = load_workflow_execution_state(state)
    except Exception:
        _fail("persistence_contract")
    expected = serialize_workflow_execution_state_json(start.running_state).encode("utf-8")
    if not (
        type(value) is RunningStatePersistenceResult
        and type(value.state_bytes_written) is int
        and value.state_bytes_written > 0
        and value.state_bytes_written == len(state_bytes)
        and state_bytes == expected
        and event_bytes == original[1]
        and type(loaded) is WorkflowExecutionState
        and loaded == start.running_state
    ):
        _fail("persistence_contract")


def _check_predecessor_identity(
    predecessor: WorkflowExecutionState,
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
) -> None:
    previous_index = start.running_state.current_step_index - 1
    previous = workflow.steps[previous_index - 1]
    if (
        predecessor.workflow_id,
        predecessor.current_step_id,
        predecessor.current_step_index,
        predecessor.current_employee_id,
    ) != (workflow.id, previous.id, previous_index, previous.employee):
        _fail("terminal_contract")


def _changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> bool:
    try:
        return (
            not state.is_file()
            or not events.is_file()
            or state.read_bytes() != original[0]
            or events.read_bytes() != original[1]
        )
    except OSError:
        return True


def _restore_if_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    changed = (_changed(state, events, original),)
    if not any(changed):
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed or _changed(state, events, original):
        _fail("dependency_rollback")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, events, original):
        _restore_if_changed(state, events, original)
        _fail(classification)


def _compensate_dependency_error(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    safe_error: Phase139Error | None,
) -> None:
    if _changed(state, events, original):
        _restore_if_changed(state, events, original)
    if safe_error is not None:
        raise safe_error
    _fail("dependency_error")


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _fail(classification: Classification) -> None:
    raise PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError(
        classification
    ) from None
