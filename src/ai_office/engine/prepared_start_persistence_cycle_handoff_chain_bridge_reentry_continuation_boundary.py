"""Phase 132 prepared-start persistence cycle handoff chain bridge boundary."""

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
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainReentryContinuationError as Phase125Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.terminal_history_contract import load_strict_terminal_history
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory, ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    WorkflowExecutionDataError,
    WorkflowExecutionLoadError,
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
Phase125Function = Callable[
    [object, object, object, object, object],
    RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome,
]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationFailureDetail:
    """Safe classification for one Phase 132 compatibility failure."""

    classification: Classification


class PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationError(
    ValueError
):
    """Raised when one prepared-start persistence bridge cannot continue safely."""


class PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError(
    PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationError
):
    """Raised for a detail-safe Phase 132 compatibility rejection."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "prepared-start persistence cycle handoff chain bridge inputs are incompatible"
        )
        self.detail = PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationFailureDetail(
            classification
        )


def route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary(
    result: object,
    workflow: object,
    employee: object,
    state_path: object,
    events_path: object,
    *,
    phase125_function: Phase125Function = (
        route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary
    ),
) -> RunningStatePersistenceResult | WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Route one exact Phase 131 result through public Phase 125 once."""
    _check_inputs(result, workflow, state_path, events_path, phase125_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is PreparedStepExecutionStart:
        _check_employee(employee, result)
        assert type(employee) is EmployeeDefinition
        _check_start(result, workflow, employee)
    elif type(result) is WorkflowProgressionDecision:
        _check_completion(result, workflow, employee)
    else:
        _check_failure(result, workflow, employee)

    _check_targets(state_path, events_path)
    original = _snapshot(state_path, events_path)

    if type(result) is WorkflowProgressionDecision:
        _check_terminal(result, workflow, state_path, events_path, "succeeded", False)
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _check_terminal(result, workflow, state_path, events_path, "failed", False)
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is PreparedStepExecutionStart
    assert type(employee) is EmployeeDefinition
    predecessor = _check_predecessor(result, workflow, state_path, events_path)
    try:
        value = phase125_function(result, workflow, employee, state_path, events_path)
    except Phase125Error as error:
        _compensate_dependency_error(state_path, events_path, original, error)
    except Exception:
        _compensate_dependency_error(state_path, events_path, original, None)

    try:
        _check_persistence(value, result, state_path, events_path, original)
    except PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError as error:
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
    step_ids = tuple(step.id for step in workflow.steps)
    return len(step_ids) == len(set(step_ids))


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
    if (
        type(state.current_step_index) is not int
        or not 2 <= state.current_step_index <= len(workflow.steps)
    ):
        _fail("start_contract")
    step = workflow.steps[state.current_step_index - 1]
    expected_completed = tuple(
        item.id for item in workflow.steps[: state.current_step_index - 1]
    )
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


def _check_terminal(
    value: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    expected_status: Literal["succeeded", "failed"],
    require_openai: bool,
) -> None:
    try:
        persisted, history = load_strict_terminal_history(workflow, state, events)
    except Exception:
        _fail("terminal_contract")
    expected_failure = (
        value.failure_category if type(value) is PersistedExecutionOutcome else None
    )
    if not _valid_terminal_history(
        persisted,
        history,
        workflow,
        expected_status,
        expected_failure,
        require_openai,
    ):
        _fail("terminal_contract")
    if (
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
        _fail("terminal_contract")


def _check_predecessor(
    start: PreparedStepExecutionStart,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> WorkflowExecutionState:
    persisted, history = _load_prepared_history(workflow, state, events)
    if not _valid_terminal_history(
        persisted,
        history,
        workflow,
        "succeeded",
        None,
        True,
        allow_empty_success_output=True,
        allow_empty_predecessor_output=True,
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


def _load_prepared_history(
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    """Use the narrow empty-success fallback only on the prepared route."""
    try:
        try:
            return load_strict_terminal_history(workflow, state, events)
        except Exception:
            loaded = load_workflow_execution_history(
                WorkflowExecutionPersistenceTargets(state, events)
            )
    except Exception:
        _fail("terminal_contract")
    if type(loaded.state) is not WorkflowExecutionState or type(loaded.events) is not tuple:
        _fail("terminal_contract")
    return loaded.state, loaded.events


def _valid_terminal_history(
    state: object,
    history: object,
    workflow: WorkflowDefinition,
    expected_status: Literal["succeeded", "failed"],
    expected_failure: object,
    require_openai: bool,
    *,
    allow_empty_success_output: bool = False,
    allow_empty_predecessor_output: bool = False,
) -> bool:
    if type(state) is not WorkflowExecutionState or type(history) is not tuple:
        return False
    if not history or not _valid_state_shape(state, workflow):
        return False
    if state.status != expected_status or state.last_failure_category != expected_failure:
        return False
    expected_completed = (
        tuple(step.id for step in workflow.steps[: state.current_step_index])
        if state.status == "succeeded"
        else tuple(step.id for step in workflow.steps[: state.current_step_index - 1])
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
            event,
            step,
            position,
            state,
            allow_empty_output=allow_empty_predecessor_output,
        ):
            return False
    return _valid_event_shape(history[-1]) and _valid_terminal_event(
        history[-1],
        state,
        expected_failure,
        require_openai,
        allow_empty_success_output=allow_empty_success_output,
    )


def _valid_state_shape(state: WorkflowExecutionState, workflow: WorkflowDefinition) -> bool:
    index = state.current_step_index
    if type(index) is not int or not 1 <= index <= len(workflow.steps):
        return False
    current = workflow.steps[index - 1]
    return (
        _nonempty_string(state.workflow_id)
        and state.workflow_id == workflow.id
        and type(state.status) is str
        and state.status in {"succeeded", "failed"}
        and _exact_string(state.current_step_id, current.id)
        and _exact_string(state.current_employee_id, current.employee)
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
    *,
    allow_empty_output: bool = False,
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
        and type(event.output_text) is str
        and (allow_empty_output or bool(event.output_text))
        and event.message is None
    )


def _valid_terminal_event(
    event: RuntimeStepEvent,
    state: WorkflowExecutionState,
    expected_failure: object,
    require_openai: bool,
    *,
    allow_empty_success_output: bool,
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
            and (
                not require_openai
                or (type(event.provider) is str and event.provider == "openai")
            )
            and expected_failure is None
            and event.failure_category is None
            and _nonempty_string(event.response_id)
            and type(event.output_text) is str
            and (allow_empty_success_output or bool(event.output_text))
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
    except (
        OSError,
        UnicodeError,
        WorkflowExecutionDataError,
        WorkflowExecutionLoadError,
    ):
        _fail("persistence_contract")
    expected = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
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


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_if_changed(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
) -> None:
    changed = (_changed(state, original[0]), _changed(events, original[1]))
    if not any(changed):
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed or _changed(state, original[0]) or _changed(events, original[1]):
        _fail("dependency_rollback")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _fail(classification)


def _compensate_dependency_error(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    safe_error: Phase125Error | None,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
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
    raise PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationCompatibilityError(
        classification
    ) from None
