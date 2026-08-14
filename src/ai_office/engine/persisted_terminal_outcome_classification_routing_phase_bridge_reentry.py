"""Bridge exact Phase 64 persistence results to the existing Phase 58 boundary."""

# The public boundary names intentionally mirror the full phase contract.
# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_terminal_outcome_classification_phase_bridge_reentry import (
    PersistedTerminalOutcomeClassificationPhaseBridgeError,
    route_persisted_terminal_outcome_classification_phase_bridge_reentry,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
)
from ai_office.storage.workflow_execution_history import (
    WorkflowExecutionDataError,
    WorkflowExecutionHistoryInconsistencyError,
    WorkflowExecutionLoadError,
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
Phase58Function = Callable[
    [object, object, object, object], PersistedExecutionOutcome | WorkflowProgressionDecision
]
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))
_PATH_TYPE = type(Path())


@dataclass(frozen=True)
class PersistedTerminalOutcomeClassificationRoutingPhaseBridgeFailureDetail:
    classification: Classification


class PersistedTerminalOutcomeClassificationRoutingPhaseBridgeError(ValueError):
    """Raised when Phase 65 cannot safely route its supplied result."""


class PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError(
    PersistedTerminalOutcomeClassificationRoutingPhaseBridgeError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted terminal outcome classification routing phase bridge "
            "inputs are incompatible"
        )
        self.detail = PersistedTerminalOutcomeClassificationRoutingPhaseBridgeFailureDetail(
            classification
        )


def route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase58_function: Phase58Function = (
        route_persisted_terminal_outcome_classification_phase_bridge_reentry
    ),
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    _validate_inputs(result, workflow, state_path, events_path, phase58_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE
    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
    elif type(result) is PersistedExecutionOutcome:
        _validate_failure(result, workflow)
    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    if type(result) is WorkflowProgressionDecision:
        _validate_terminal(result, workflow, state_path, events_path, "succeeded")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_terminal(result, workflow, state_path, events_path, "failed")
        _require_unchanged(state_path, events_path, original, "terminal_contract")
        return result

    assert type(result) is WorkflowExecutionPersistenceResult
    state = _validate_persistence(result, workflow, state_path, events_path)
    try:
        value = phase58_function(result, workflow, state_path, events_path)
    except PersistedTerminalOutcomeClassificationPhaseBridgeError as error:
        _restore_if_changed(state_path, events_path, original)
        raise error
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("dependency_error")
    try:
        _require_unchanged(state_path, events_path, original, "outcome_contract")
        _validate_outcome(value, state)
    except PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError:
        _restore_if_changed(state_path, events_path, original)
        raise
    return value


def _validate_inputs(
    result: object, workflow: object, state: object, events: object, function: object
) -> None:
    if type(result) not in (
        WorkflowExecutionPersistenceResult,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if type(state) is not _PATH_TYPE:
        _raise("state_target")
    if type(events) is not _PATH_TYPE:
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(function):
        _raise("outcome_contract")


def _validate_completion(value: WorkflowProgressionDecision, workflow: WorkflowDefinition) -> None:
    final = workflow.steps[-1]
    if not (
        type(value.decision) is str and value.decision == "workflow_complete"
        and type(value.workflow_id) is str and value.workflow_id == workflow.id
        and type(value.current_step_id) is str and value.current_step_id == final.id
        and type(value.current_step_index) is int and value.current_step_index == len(workflow.steps)
        and type(value.current_employee_id) is str and value.current_employee_id == final.employee
        and value.next_step_id is None and value.next_step_index is None
        and value.next_employee_id is None
        and type(value.reason) is str and value.reason == "last_step_succeeded"
    ):
        _raise("completion_contract")


def _validate_failure(value: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if not (
        type(value.outcome) is str and value.outcome == "persisted_failure"
        and type(value.current_step_index) is int
        and 1 <= value.current_step_index <= len(workflow.steps)
    ):
        _raise("failure_contract")
    step = workflow.steps[value.current_step_index - 1]
    if not (
        type(value.workflow_id) is str and value.workflow_id == workflow.id
        and type(value.current_step_id) is str and value.current_step_id == step.id
        and type(value.current_employee_id) is str and value.current_employee_id == step.employee
        and type(value.failure_category) is str
        and value.failure_category in _FAILURE_CATEGORIES
    ):
        _raise("failure_contract")


def _validate_targets(state: Path, events: Path) -> None:
    try:
        if not state.is_file():
            _raise("state_target")
    except OSError:
        _raise("state_target")
    try:
        if not events.is_file():
            _raise("event_target")
    except OSError:
        _raise("event_target")


def _capture(state: Path, events: Path) -> tuple[bytes, bytes]:
    try:
        state_bytes = state.read_bytes()
    except OSError:
        _raise("state_target")
    try:
        event_bytes = events.read_bytes()
    except OSError:
        _raise("event_target")
    return state_bytes, event_bytes


def _validate_terminal(
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
    state: Path,
    events: Path,
    status: Literal["succeeded", "failed"],
) -> None:
    try:
        persisted, _ = load_strict_terminal_history(workflow, state, events)
    except (OSError, TerminalHistoryContractError):
        _raise("terminal_contract")
    if persisted.status != status or (
        persisted.workflow_id, persisted.current_step_id, persisted.current_step_index,
        persisted.current_employee_id,
    ) != (
        result.workflow_id, result.current_step_id, result.current_step_index,
        result.current_employee_id,
    ):
        _raise("terminal_contract")
    if type(result) is PersistedExecutionOutcome and persisted.last_failure_category != result.failure_category:
        _raise("terminal_contract")


def _validate_persistence(
    result: WorkflowExecutionPersistenceResult,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
):
    if not (
        result.state_path == state_path and result.events_path == events_path
        and type(result.state_bytes_written) is int
        and type(result.event_bytes_appended) is int
        and result.state_bytes_written > 0 and result.event_bytes_appended > 0
    ):
        _raise("persistence_contract")
    try:
        state_bytes = state_path.read_bytes()
    except OSError:
        _raise("state_target")
    try:
        events_path.read_bytes()
    except OSError:
        _raise("event_target")
    try:
        state, history = load_strict_terminal_history(workflow, state_path, events_path)
    except (OSError, TerminalHistoryContractError):
        state, history = _load_phase155_compatible_history(
            workflow, state_path, events_path
        )
    if state.status not in ("succeeded", "failed"):
        _raise("persistence_contract")
    final_event = serialize_runtime_step_event_jsonl(history[-1]).encode()
    try:
        event_bytes = events_path.read_bytes()
    except OSError:
        _raise("event_target")
    if (
        len(state_bytes) != result.state_bytes_written
        or len(final_event) != result.event_bytes_appended
        or not event_bytes.endswith(final_event)
    ):
        _raise("persistence_contract")
    return state


def _load_phase155_compatible_history(
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    """Strict-first Phase 65 fallback bounded to the exact Phase-155 domain.

    Reachable only when the shared strict loader rejects. Reconstructs through
    the public storage loader and re-validates the complete shared strict
    terminal contract with the single relaxation that succeeded predecessors
    keep exact built-in str output_text, empty or non-empty. Bounded to exact
    built-in int current_step_index >= 6. No predecessor provider/request-ID
    gating; Phase 65 target matching stays `==` on this path.
    """
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except (
        WorkflowExecutionDataError,
        WorkflowExecutionHistoryInconsistencyError,
        WorkflowExecutionLoadError,
        OSError,
    ):
        _raise("terminal_contract")
    state, events = history.state, history.events
    if not (
        state.status in ("succeeded", "failed")
        and state.workflow_id == workflow.id
        and events
        and type(state.current_step_index) is int
        and state.current_step_index >= 6
        and state.current_step_index <= len(workflow.steps)
    ):
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
            and event.failure_category is None
            and type(event.response_id) is str
            and event.response_id != ""
            and type(event.output_text) is str
            and event.message is None
        ):
            _raise("terminal_contract")
    _validate_phase155_terminal_event(state, events[-1], workflow)
    return state, events


def _validate_phase155_terminal_event(
    state: WorkflowExecutionState, event: RuntimeStepEvent, workflow: WorkflowDefinition
) -> None:
    """Keep the complete shared strict terminal semantics on the fallback."""
    allow_empty_success_output = (
        state.status == "succeeded" and state.current_step_index < len(workflow.steps)
    )
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
            and type(event.response_id) is str
            and event.response_id != ""
            and type(event.output_text) is str
            and (allow_empty_success_output or event.output_text != "")
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


def _validate_outcome(value: object, state: WorkflowExecutionState) -> None:
    if type(value) is not PersistedExecutionOutcome:
        _raise("outcome_contract")
    if not (
        type(value.outcome) is str
        and value.outcome == ("persisted_success" if state.status == "succeeded" else "persisted_failure")
        and type(value.workflow_id) is str and value.workflow_id == state.workflow_id
        and type(value.current_step_id) is str and value.current_step_id == state.current_step_id
        and type(value.current_step_index) is int and value.current_step_index == state.current_step_index
        and type(value.current_employee_id) is str and value.current_employee_id == state.current_employee_id
        and ((value.failure_category is None) == (state.status == "succeeded"))
        and (value.failure_category is None or value.failure_category in _FAILURE_CATEGORIES)
        and value.failure_category == state.last_failure_category
    ):
        _raise("outcome_contract")


def _changed(path: Path, before: bytes) -> bool:
    try:
        return not path.is_file() or path.read_bytes() != before
    except OSError:
        return True


def _restore_if_changed(state: Path, events: Path, original: tuple[bytes, bytes]) -> None:
    if not (_changed(state, original[0]) or _changed(events, original[1])):
        return
    failed = False
    for path, contents in ((state, original[0]), (events, original[1])):
        try:
            path.write_bytes(contents)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _require_unchanged(
    state: Path, events: Path, original: tuple[bytes, bytes], classification: Classification
) -> None:
    if _changed(state, original[0]) or _changed(events, original[1]):
        _restore_if_changed(state, events, original)
        _raise(classification)


def _raise(classification: Classification) -> None:
    raise PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError(
        classification
    ) from None
