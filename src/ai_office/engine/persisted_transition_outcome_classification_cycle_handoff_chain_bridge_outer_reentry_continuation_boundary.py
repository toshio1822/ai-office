"""Phase 143 persisted-transition outcome-classification cycle handoff chain bridge outer boundary."""

# ruff: noqa: E501,E701,I001

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeCompatibilityError,
    classify_persisted_execution_outcome_reentry,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    _load_terminal_history,
    _validate_terminal_history,
    is_valid_workflow_definition,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
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
    "persistence_contract",
    "outcome_contract",
    "dependency_error",
    "dependency_rollback",
]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationFailureDetail:
    """Safe classification for one Phase 143 compatibility failure."""

    classification: Classification


class PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationError(
    ValueError
):
    """Base error for the Phase 143 boundary."""


class PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError(
    PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationError
):
    """Raised when one Phase 142 result cannot safely cross Phase 143."""

    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted-transition outcome classification cycle handoff chain bridge outer inputs are incompatible"
        )
        self.detail = PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationFailureDetail(
            classification
        )


def route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    """Classify one exact persisted transition result without progressing."""
    _check_inputs(result, workflow, state_path, events_path)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is WorkflowProgressionDecision:
        _check_completion(result, workflow)
    elif type(result) is PersistedExecutionOutcome:
        _check_failure(result, workflow)

    _check_targets(state_path, events_path)
    original = _capture_targets(state_path, events_path)

    if type(result) is WorkflowProgressionDecision:
        _check_terminal_history(
            workflow,
            state_path,
            events_path,
            "succeeded",
            result,
            classification="terminal_contract",
            minimum_index=1,
            require_immediate_openai=False,
            allow_empty_success_output=False,
            allow_empty_predecessor_output=True,
        )
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _check_terminal_history(
            workflow,
            state_path,
            events_path,
            "failed",
            result,
            classification="terminal_contract",
            minimum_index=1,
            require_immediate_openai=False,
            allow_empty_success_output=False,
            allow_empty_predecessor_output=True,
        )
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is WorkflowExecutionPersistenceResult
    state = _check_persistence(result, workflow, state_path, events_path)
    try:
        value = classify_persisted_execution_outcome_reentry(
            workflow, state_path, events_path
        )
    except PersistedExecutionOutcomeCompatibilityError as error:
        _restore_if_changed(state_path, events_path, original)
        if error.detail.classification == "history_rollback":
            _fail("dependency_rollback")
        _fail("dependency_error")
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _fail("dependency_error")

    try:
        _check_outcome(value, state, workflow)
    except PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError:
        _restore_if_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _fail("outcome_contract")
    if _changed(state_path, original[0]) or _changed(events_path, original[1]):
        _restore_if_changed(state_path, events_path, original)
        _fail("outcome_contract")
    return value


def _check_inputs(
    result: object,
    workflow: object,
    state: object,
    events: object,
) -> None:
    if type(result) not in (
        WorkflowExecutionPersistenceResult,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _fail("result_type")
    if type(workflow) is not WorkflowDefinition or not is_valid_workflow_definition(
        workflow
    ):
        _fail("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _fail("state_target")
    if type(events) is not _PATH_TYPE:
        _fail("event_target")
    if state == events:
        _fail("target_conflict")


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
        and type(index) is int
        and 1 <= index <= len(workflow.steps)
    ):
        _fail("failure_contract")
    step = workflow.steps[index - 1]
    if not (
        _exact_string(value.workflow_id, workflow.id)
        and _exact_string(value.current_step_id, step.id)
        and _exact_string(value.current_employee_id, step.employee)
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


def _check_terminal_history(
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    expected_status: Literal["succeeded", "failed"],
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    *,
    classification: Classification,
    minimum_index: int,
    require_immediate_openai: bool,
    allow_empty_success_output: bool,
    allow_empty_predecessor_output: bool,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    expected_failure = (
        result.failure_category if type(result) is PersistedExecutionOutcome else None
    )
    try:
        state, history = _load_terminal_history(state_path, events_path)
        _validate_terminal_history(
            workflow,
            state,
            history,
            expected_status=expected_status,
            result=result,
            expected_failure=expected_failure,
            minimum_index=minimum_index,
            require_immediate_openai=require_immediate_openai,
            allow_empty_success_output=allow_empty_success_output,
            allow_empty_predecessor_output=allow_empty_predecessor_output,
            provider_policy="nonempty",
            predecessor_request_id_policy="required",
            terminal_request_id_policy="optional",
            failure_message_policy="nonempty",
        )
    except TerminalHistoryContractError:
        _fail(classification)
    return state, history


def _check_persistence(
    result: WorkflowExecutionPersistenceResult,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> WorkflowExecutionState:
    if result.state_path is not state_path or result.events_path is not events_path:
        _fail("persistence_contract")
    if (
        type(result.state_bytes_written) is not int
        or result.state_bytes_written <= 0
        or type(result.event_bytes_appended) is not int
        or result.event_bytes_appended <= 0
    ):
        _fail("persistence_contract")
    try:
        state, history = _load_terminal_history(state_path, events_path)
        _validate_terminal_history(
            workflow,
            state,
            history,
            expected_status=(
                state.status if state.status in {"succeeded", "failed"} else "succeeded"
            ),
            expected_failure=state.last_failure_category,
            minimum_index=1,
            require_immediate_openai=True,
            allow_empty_success_output=True,
            allow_empty_predecessor_output=True,
            provider_policy="nonempty",
            predecessor_request_id_policy="required",
            terminal_request_id_policy="optional",
            failure_message_policy="nonempty",
            allow_immediate_none_request_id=True,
            allow_accumulated_none_request_id=True,
            immediate_none_minimum_position=5,
        )
    except TerminalHistoryContractError:
        _fail("persistence_contract")
    try:
        state_bytes = state_path.read_bytes()
        event_bytes = events_path.read_bytes()
    except OSError:
        _fail("persistence_contract")
    terminal_bytes = serialize_runtime_step_event_jsonl(history[-1]).encode("utf-8")
    expected_event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8") for event in history
    )
    if not (
        state_bytes == serialize_workflow_execution_state_json(state).encode("utf-8")
        and len(state_bytes) == result.state_bytes_written
        and event_bytes == expected_event_bytes
        and event_bytes.endswith(terminal_bytes)
        and result.event_bytes_appended == len(terminal_bytes)
    ):
        _fail("persistence_contract")
    return state


def _check_outcome(
    value: object, state: WorkflowExecutionState, workflow: WorkflowDefinition
) -> None:
    if type(value) is not PersistedExecutionOutcome:
        _fail("outcome_contract")
    expected = (
        "persisted_success" if state.status == "succeeded" else "persisted_failure"
    )
    valid_failure = (
        value.failure_category is None
        if state.status == "succeeded"
        else type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
        and value.failure_category == state.last_failure_category
    )
    if not (
        _exact_string(value.outcome, expected)
        and _exact_string(value.workflow_id, state.workflow_id)
        and _exact_string(value.current_step_id, state.current_step_id)
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
        and value.current_step_index == state.current_step_index
        and _exact_string(value.current_employee_id, state.current_employee_id)
        and valid_failure
    ):
        _fail("outcome_contract")


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


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value)


def _exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _fail(classification: Classification) -> None:
    raise PersistedTransitionOutcomeClassificationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError(
        classification
    ) from None
