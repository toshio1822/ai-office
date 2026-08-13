"""Phase 161 runtime-result transition persistence cycle handoff chain bridge outer-chain boundary."""

# ruff: noqa: E501,E701,I001

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase142Error,
    route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationFailureCategory,
    ModelInvocationSuccess,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

Classification = Literal[
    "result_type",
    "workflow_definition",
    "completion_contract",
    "failure_contract",
    "state_target",
    "event_target",
    "target_conflict",
    "terminal_contract",
    "runtime_contract",
    "persistence_contract",
    "dependency_error",
    "dependency_rollback",
]
Phase142Function = Callable[
    [object, object, object, object],
    WorkflowExecutionPersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome,
]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationFailureDetail:
    """Safe classification for one Phase 161 compatibility failure."""

    classification: Classification


class RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError(
    ValueError
):
    """Base error for the Phase 161 boundary."""


class RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError(
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError
):
    """Raised when one runtime result cannot safely cross Phase 161."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "runtime-result transition persistence cycle handoff chain bridge outer-chain inputs are incompatible"
        )
        self.detail = (
            RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationFailureDetail(
                classification
            )
        )


def route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase142_function: Phase142Function = (
        route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    ),
) -> (
    WorkflowExecutionPersistenceResult
    | WorkflowProgressionDecision
    | PersistedExecutionOutcome
):
    """Route one exact Phase 155 runtime result through public Phase 142 once."""
    _check_inputs(result, workflow, state_path, events_path, phase142_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is WorkflowProgressionDecision:
        _check_completion(result, workflow)
    elif type(result) is PersistedExecutionOutcome:
        _check_failure(result, workflow)

    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    if type(result) is WorkflowProgressionDecision:
        _check_terminal(result, workflow, state_path, events_path, "succeeded", None)
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _check_terminal(
            result, workflow, state_path, events_path, "failed", result.failure_category
        )
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) in (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)
    state, history = _load_running_context(workflow, state_path, events_path)
    _check_runtime_result(result, workflow, state)
    _check_predecessor_history(workflow, state, history.events)

    try:
        value = phase142_function(result, workflow, state_path, events_path)
    except Phase142Error as error:
        _compensate_dependency_error(state_path, events_path, original, error)
    except Exception:
        _compensate_dependency_error(state_path, events_path, original, None)

    try:
        _check_persistence(
            value,
            result,
            state,
            history.events,
            workflow,
            state_path,
            events_path,
            original,
        )
    except RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as error:
        if error.detail.classification != "dependency_rollback":
            _restore_if_changed(state_path, events_path, original)
        raise
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
    function: object,
) -> None:
    if type(result) not in (
        StepRuntimeExecutionSuccess,
        StepRuntimeExecutionFailure,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _fail("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _fail("state_target")
    if type(events) is not _PATH_TYPE:
        _fail("event_target")
    if state == events:
        _fail("target_conflict")
    if not callable(function):
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


def _check_completion(
    value: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    final = workflow.steps[-1]
    if not (
        _exact_string(value.decision, "workflow_complete")
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
    value: PersistedExecutionOutcome, workflow: WorkflowDefinition
) -> None:
    index = value.current_step_index
    if not (
        _exact_string(value.outcome, "persisted_failure")
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
    try:
        if not state.is_file():
            _fail("state_target")
    except OSError:
        _fail("state_target")
    try:
        if not events.is_file():
            _fail("event_target")
    except OSError:
        _fail("event_target")


def _capture_targets(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _fail("state_target")
    try:
        event_bytes = events.read_bytes()
    except OSError:
        _fail("event_target")
    return state_bytes, event_bytes


def _load_running_context(
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> tuple[WorkflowExecutionState, object]:
    try:
        loaded = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (
        OSError,
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _fail("runtime_contract")
    state = loaded.state
    if not (
        type(state) is WorkflowExecutionState
        and _exact_string(state.status, "running")
        and type(state.current_step_index) is int
        and 6 <= state.current_step_index <= len(workflow.steps)
        and _exact_string(state.workflow_id, workflow.id)
        and _exact_string(
            state.current_step_id,
            workflow.steps[state.current_step_index - 1].id,
        )
        and _exact_string(
            state.current_employee_id,
            workflow.steps[state.current_step_index - 1].employee,
        )
        and type(state.completed_step_ids) is tuple
        and all(_nonempty_string(item) for item in state.completed_step_ids)
        and state.completed_step_ids
        == tuple(step.id for step in workflow.steps[: state.current_step_index - 1])
        and state.last_failure_category is None
        and type(loaded.events) is tuple
    ):
        _fail("runtime_contract")
    return state, loaded


def _check_runtime_result(
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
) -> None:
    if not (
        type(result.workflow_id) is str
        and bool(result.workflow_id)
        and type(result.step_id) is str
        and bool(result.step_id)
        and type(result.step_index) is int
        and type(result.employee_id) is str
        and bool(result.employee_id)
        and result.workflow_id == workflow.id == state.workflow_id
        and result.step_id == state.current_step_id
        and result.step_index == state.current_step_index
        and result.employee_id == state.current_employee_id
    ):
        _fail("runtime_contract")
    invocation = result.invocation_result
    if type(result) is StepRuntimeExecutionSuccess:
        if not _valid_success(invocation):
            _fail("runtime_contract")
    elif type(result) is StepRuntimeExecutionFailure:
        if not _valid_failure(invocation):
            _fail("runtime_contract")
    else:
        _fail("runtime_contract")


def _valid_success(value: object) -> bool:
    return (
        type(value) is ModelInvocationSuccess
        and _exact_string(value.provider, "openai")
        and _nonempty_string(value.response_id)
        and _optional_nonempty_string(value.request_id)
        and _nonempty_string(value.status)
        and type(value.text_parts) is tuple
        and all(type(part) is str for part in value.text_parts)
        and type(value.text) is str
        and value.text == "".join(value.text_parts)
    )


def _valid_failure(value: object) -> bool:
    return (
        type(value) is ModelInvocationFailure
        and _exact_string(value.provider, "openai")
        and type(value.category) is str
        and value.category in _FAILURE_CATEGORIES
        and _nonempty_string(value.message)
        and _optional_nonempty_string(value.request_id)
        and _optional_int(value.status_code)
        and _optional_nonempty_string(value.provider_error_type)
        and _optional_nonempty_string(value.provider_error_code)
        and (
            value.category == "api_error"
            or (
                value.status_code is None
                and value.provider_error_type is None
                and value.provider_error_code is None
            )
        )
    )


def _check_predecessor_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    events: object,
) -> None:
    prefix = workflow.steps[: state.current_step_index - 1]
    if type(events) is not tuple or len(events) != len(prefix):
        _fail("runtime_contract")
    for position, (event, step) in enumerate(zip(events, prefix, strict=True), 1):
        if not _valid_predecessor_event(
            event,
            step,
            position,
            state,
            require_openai=position == len(prefix),
            allow_empty_output=True,
            allow_none_request_id=position == len(prefix),
        ):
            _fail("runtime_contract")


def _valid_predecessor_event(
    event: object,
    step: WorkflowStepDefinition,
    position: int,
    state: WorkflowExecutionState,
    require_openai: bool = False,
    allow_empty_output: bool = False,
    allow_none_request_id: bool = False,
) -> bool:
    provider_valid = _nonempty_string(event.provider) and (
        not require_openai or event.provider == "openai"
    )
    request_id_valid = (event.request_id is None and allow_none_request_id) or (
        _nonempty_string(event.request_id)
    )
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
        and provider_valid
        and event.failure_category is None
        and _nonempty_string(event.response_id)
        and request_id_valid
        and type(event.output_text) is str
        and (allow_empty_output or bool(event.output_text))
        and event.message is None
    )


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


def _check_terminal(
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    expected_status: Literal["succeeded", "failed"],
    expected_failure: object,
) -> None:
    persisted, history = _load_history(workflow, state, events)
    if not _valid_history(
        workflow,
        persisted,
        history,
        expected_status,
        result,
        expected_failure,
        require_immediate_openai=False,
        allow_empty_success_output=False,
        allow_empty_predecessor_output=True,
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
        and _nonempty_string(event.request_id)
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
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    original_state: WorkflowExecutionState,
    original_events: tuple[RuntimeStepEvent, ...],
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
) -> None:
    if type(value) is not WorkflowExecutionPersistenceResult:
        _fail("persistence_contract")
    if value.state_path is not state_path or value.events_path is not events_path:
        _fail("persistence_contract")
    if (
        type(value.state_bytes_written) is not int
        or value.state_bytes_written <= 0
        or type(value.event_bytes_appended) is not int
        or value.event_bytes_appended <= 0
    ):
        _fail("persistence_contract")
    try:
        loaded = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
    except (
        OSError,
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
    ):
        _fail("persistence_contract")
    state = loaded.state
    history = loaded.events
    invocation = result.invocation_result
    successful = type(result) is StepRuntimeExecutionSuccess
    if not (
        type(state) is WorkflowExecutionState
        and type(history) is tuple
        and len(history) == len(original_events) + 1
        and history[:-1] == original_events
        and len(state_bytes) == value.state_bytes_written
        and state_bytes == serialize_workflow_execution_state_json(state).encode("utf-8")
        and event_bytes.startswith(original[1])
        and event_bytes[len(original[1]) :]
        == serialize_runtime_step_event_jsonl(history[-1]).encode("utf-8")
        and value.event_bytes_appended == len(event_bytes) - len(original[1])
        and value.event_bytes_appended
        == len(serialize_runtime_step_event_jsonl(history[-1]).encode("utf-8"))
    ):
        _fail("persistence_contract")
    current = workflow.steps[original_state.current_step_index - 1]
    terminal = history[-1]
    base = (
        _exact_string(state.workflow_id, workflow.id)
        and _exact_string(state.current_step_id, current.id)
        and type(state.current_step_index) is int
        and state.current_step_index == original_state.current_step_index == result.step_index
        and _exact_string(state.current_employee_id, current.employee)
        and state.current_employee_id == result.employee_id
        and _exact_string(terminal.workflow_id, result.workflow_id)
        and _exact_string(terminal.step_id, result.step_id)
        and type(terminal.step_index) is int
        and terminal.step_index == result.step_index
        and _exact_string(terminal.employee_id, result.employee_id)
        and _exact_string(terminal.previous_status, "running")
        and _exact_string(terminal.provider, "openai")
        and terminal.request_id == invocation.request_id
    )
    if successful:
        details = (
            _exact_string(state.status, "succeeded")
            and state.completed_step_ids
            == original_state.completed_step_ids + (original_state.current_step_id,)
            and state.last_failure_category is None
            and _exact_string(terminal.event_type, "step_succeeded")
            and _exact_string(terminal.next_status, "succeeded")
            and terminal.failure_category is None
            and terminal.response_id == invocation.response_id
            and terminal.output_text == invocation.text
            and terminal.message is None
        )
    else:
        details = (
            _exact_string(state.status, "failed")
            and state.completed_step_ids == original_state.completed_step_ids
            and state.last_failure_category == invocation.category
            and _exact_string(terminal.event_type, "step_failed")
            and _exact_string(terminal.next_status, "failed")
            and terminal.failure_category == invocation.category
            and terminal.response_id is None
            and terminal.output_text is None
            and terminal.message == invocation.message
        )
    if not base or not details:
        _fail("persistence_contract")


def _require_unchanged(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _fail(classification)


def _restore_if_changed(
    state: Path, events: Path, original: tuple[bytes, bytes]
) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])):
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed or _changed(state, original[0]) or _changed(events, original[1]):
        _fail("dependency_rollback")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _compensate_dependency_error(
    state: Path,
    events: Path,
    original: tuple[bytes, bytes],
    safe_error: Phase142Error | None,
) -> None:
    _restore_if_changed(state, events, original)
    if safe_error is not None:
        raise safe_error
    _fail("dependency_error")


def _optional_nonempty_string(value: object) -> bool:
    return value is None or _nonempty_string(value)


def _optional_int(value: object) -> bool:
    return value is None or type(value) is int


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _fail(classification: Classification) -> None:
    raise RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError(
        classification
    ) from None
