"""Strict read-only loading for persisted workflow execution history."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import (
    RuntimeStepEvent,
    RuntimeStepEventType,
    WorkflowExecutionState,
    WorkflowExecutionStatus,
)
from ai_office.storage.workflow_execution_persistence import (
    WorkflowExecutionPersistenceTargets,
)

_LOAD_ERROR_MESSAGE = "workflow execution history could not be loaded"
_DATA_ERROR_MESSAGE = "workflow execution persisted data is invalid"
_INCONSISTENCY_ERROR_MESSAGE = "workflow execution history is inconsistent"
_STATE_KEYS = frozenset(
    {
        "workflow_id",
        "status",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "completed_step_ids",
        "last_failure_category",
    }
)
_EVENT_KEYS = frozenset(
    {
        "event_type",
        "workflow_id",
        "step_id",
        "step_index",
        "employee_id",
        "previous_status",
        "next_status",
        "provider",
        "failure_category",
        "response_id",
        "request_id",
        "output_text",
        "message",
    }
)
_STATUSES = frozenset({"ready", "running", "succeeded", "failed"})
_EVENT_TYPES = frozenset({"step_succeeded", "step_failed"})
_FAILURE_CATEGORIES = frozenset(
    {
        "api_error",
        "transport_error",
        "invalid_response",
        "invalid_output",
        "invalid_request",
        "approval_required",
    }
)


@dataclass(frozen=True)
class LoadedWorkflowExecutionHistory:
    """Immutable state snapshot and ordered runtime events read from disk."""

    state: WorkflowExecutionState
    events: tuple[RuntimeStepEvent, ...]


@dataclass(frozen=True)
class WorkflowExecutionLoadFailureDetail:
    """Safe classification for an expected loading failure."""

    operation: str


class WorkflowExecutionLoadError(RuntimeError):
    """Raised when persisted workflow execution history cannot be loaded."""


class WorkflowExecutionDataError(WorkflowExecutionLoadError):
    """Raised when persisted bytes do not satisfy the strict data contract."""

    def __init__(self, operation: str) -> None:
        super().__init__(_DATA_ERROR_MESSAGE)
        self.detail = WorkflowExecutionLoadFailureDetail(operation)


class WorkflowExecutionHistoryInconsistencyError(WorkflowExecutionLoadError):
    """Raised when independently valid state and events disagree."""

    def __init__(self) -> None:
        super().__init__(_INCONSISTENCY_ERROR_MESSAGE)
        self.detail = WorkflowExecutionLoadFailureDetail("history_consistency")


def load_workflow_execution_history(
    targets: WorkflowExecutionPersistenceTargets,
) -> LoadedWorkflowExecutionHistory:
    """Read, strictly reconstruct, and cross-check one persisted history."""
    _validate_targets(targets)
    state_bytes = _read_bytes(targets.state_path, "state_read")
    event_bytes = _read_bytes(targets.events_path, "events_read")
    state = parse_workflow_execution_state(_decode_state_json(state_bytes))
    events = _parse_runtime_step_events(event_bytes)
    _validate_history_consistency(state, events)
    return LoadedWorkflowExecutionHistory(state=state, events=events)


def load_workflow_execution_state(state_path: Path) -> WorkflowExecutionState:
    """Strictly read one explicit state target without requiring an event file."""
    try:
        invalid = not state_path.is_file()
    except OSError:
        raise WorkflowExecutionLoadError(_LOAD_ERROR_MESSAGE) from None
    if invalid:
        raise WorkflowExecutionLoadError(_LOAD_ERROR_MESSAGE)
    contents = _read_bytes(state_path, "state_read")
    return parse_workflow_execution_state(_decode_state_json(contents))


def parse_workflow_execution_state(value: object) -> WorkflowExecutionState:
    """Strictly reconstruct an immutable state from decoded JSON data."""
    data = _require_exact_object(value, _STATE_KEYS, "state_parse")
    workflow_id = _require_non_empty_string(data["workflow_id"], "state_parse")
    current_step_id = _require_non_empty_string(data["current_step_id"], "state_parse")
    current_employee_id = _require_non_empty_string(
        data["current_employee_id"], "state_parse"
    )
    status = _require_member(data["status"], _STATUSES, "state_parse")
    step_index = _require_positive_int(data["current_step_index"], "state_parse")
    completed_step_ids = _require_string_array(
        data["completed_step_ids"], "state_parse"
    )
    failure_category = _require_optional_member(
        data["last_failure_category"], _FAILURE_CATEGORIES, "state_parse"
    )
    return WorkflowExecutionState(
        workflow_id=workflow_id,
        status=cast(WorkflowExecutionStatus, status),
        current_step_id=current_step_id,
        current_step_index=step_index,
        current_employee_id=current_employee_id,
        completed_step_ids=completed_step_ids,
        last_failure_category=cast(
            ModelInvocationFailureCategory | None, failure_category
        ),
    )


def parse_runtime_step_event(value: object) -> RuntimeStepEvent:
    """Strictly reconstruct one immutable runtime event from decoded JSON data."""
    data = _require_exact_object(value, _EVENT_KEYS, "events_parse")
    event_type = _require_member(data["event_type"], _EVENT_TYPES, "events_parse")
    event = RuntimeStepEvent(
        event_type=cast(RuntimeStepEventType, event_type),
        workflow_id=_require_non_empty_string(data["workflow_id"], "events_parse"),
        step_id=_require_non_empty_string(data["step_id"], "events_parse"),
        step_index=_require_positive_int(data["step_index"], "events_parse"),
        employee_id=_require_non_empty_string(data["employee_id"], "events_parse"),
        previous_status=cast(
            WorkflowExecutionStatus,
            _require_member(data["previous_status"], _STATUSES, "events_parse"),
        ),
        next_status=cast(
            WorkflowExecutionStatus,
            _require_member(data["next_status"], _STATUSES, "events_parse"),
        ),
        provider=_require_non_empty_string(data["provider"], "events_parse"),
        failure_category=cast(
            ModelInvocationFailureCategory | None,
            _require_optional_member(
                data["failure_category"], _FAILURE_CATEGORIES, "events_parse"
            ),
        ),
        response_id=_require_optional_string(data["response_id"], "events_parse"),
        request_id=_require_optional_string(data["request_id"], "events_parse"),
        output_text=_require_optional_string(data["output_text"], "events_parse"),
        message=_require_optional_string(data["message"], "events_parse"),
    )
    _validate_event_semantics(event)
    return event


def _validate_targets(targets: WorkflowExecutionPersistenceTargets) -> None:
    try:
        invalid = (
            targets.state_path == targets.events_path
            or not targets.state_path.is_file()
            or not targets.events_path.is_file()
        )
    except OSError:
        raise WorkflowExecutionLoadError(_LOAD_ERROR_MESSAGE) from None
    if invalid:
        raise WorkflowExecutionLoadError(_LOAD_ERROR_MESSAGE)


def _read_bytes(path: Path, operation: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        error = WorkflowExecutionLoadError(_LOAD_ERROR_MESSAGE)
        error.detail = WorkflowExecutionLoadFailureDetail(operation)
        raise error from None


def _decode_state_json(contents: bytes) -> object:
    if not contents.strip():
        raise WorkflowExecutionDataError("state_parse")
    try:
        text = contents.decode("utf-8")
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError):
        raise WorkflowExecutionDataError("state_parse") from None


def _parse_runtime_step_events(contents: bytes) -> tuple[RuntimeStepEvent, ...]:
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        raise WorkflowExecutionDataError("events_parse") from None
    if not text:
        return ()
    if not text.endswith("\n"):
        raise WorkflowExecutionDataError("events_parse")
    records = tuple(
        record[:-1] if record.endswith("\r") else record
        for record in text[:-1].split("\n")
    )
    if any(not record.strip() for record in records):
        raise WorkflowExecutionDataError("events_parse")
    try:
        return tuple(
            parse_runtime_step_event(
                json.loads(record, object_pairs_hook=_reject_duplicate_keys)
            )
            for record in records
        )
    except (json.JSONDecodeError, _DuplicateKeyError):
        raise WorkflowExecutionDataError("events_parse") from None


def _validate_event_semantics(event: RuntimeStepEvent) -> None:
    valid = event.previous_status == "running" and (
        (
            event.event_type == "step_succeeded"
            and event.next_status == "succeeded"
            and event.failure_category is None
            and event.message is None
            and isinstance(event.response_id, str)
            and isinstance(event.output_text, str)
        )
        or (
            event.event_type == "step_failed"
            and event.next_status == "failed"
            and event.failure_category is not None
            and isinstance(event.message, str)
            and event.response_id is None
            and event.output_text is None
        )
    )
    if not valid:
        raise WorkflowExecutionDataError("events_parse")


def _validate_history_consistency(
    state: WorkflowExecutionState, events: tuple[RuntimeStepEvent, ...]
) -> None:
    if not events:
        valid = state.status in {"ready", "running"}
    else:
        final = events[-1]
        valid = all(event.workflow_id == state.workflow_id for event in events) and (
            (
                state.status == "running"
                and final.event_type == "step_succeeded"
                and final.next_status == "succeeded"
                and final.failure_category is None
            )
            or (
                state.status == "succeeded"
                and final.step_id == state.current_step_id
                and final.step_index == state.current_step_index
                and final.employee_id == state.current_employee_id
                and final.next_status == state.status
                and (
                    final.event_type == "step_succeeded"
                    and state.last_failure_category is None
                    and state.completed_step_ids
                    and state.completed_step_ids[-1] == final.step_id
                )
            )
            or (
                state.status == "failed"
                and final.step_id == state.current_step_id
                and final.step_index == state.current_step_index
                and final.employee_id == state.current_employee_id
                and final.next_status == state.status
                and (
                    final.event_type == "step_failed"
                    and state.last_failure_category == final.failure_category
                )
            )
        )
    if not valid:
        raise WorkflowExecutionHistoryInconsistencyError() from None


def _require_exact_object(
    value: object, keys: frozenset[str], operation: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise WorkflowExecutionDataError(operation)
    return value


def _require_non_empty_string(value: object, operation: str) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowExecutionDataError(operation)
    return value


def _require_optional_string(value: object, operation: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise WorkflowExecutionDataError(operation)
    return value


def _require_positive_int(value: object, operation: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowExecutionDataError(operation)
    return value


def _require_string_array(value: object, operation: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkflowExecutionDataError(operation)
    return tuple(value)


def _require_member(value: object, members: frozenset[str], operation: str) -> str:
    if not isinstance(value, str) or value not in members:
        raise WorkflowExecutionDataError(operation)
    return value


def _require_optional_member(
    value: object, members: frozenset[str], operation: str
) -> str | None:
    if value is None:
        return None
    return _require_member(value, members, operation)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result
