"""Compensatable JSON and JSONL persistence for one workflow transition."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ai_office.runtime import (
    RuntimeStepEvent,
    WorkflowExecutionState,
    WorkflowExecutionTransition,
)

_INPUT_ERROR_MESSAGE = "workflow execution persistence inputs are inconsistent"
_PERSISTENCE_ERROR_MESSAGE = "workflow execution persistence failed"
_ROLLBACK_ERROR_MESSAGE = "workflow execution persistence rollback failed"


@dataclass(frozen=True)
class WorkflowExecutionPersistenceTargets:
    """Explicit filesystem targets for a state snapshot and event log."""

    state_path: Path
    events_path: Path


@dataclass(frozen=True)
class WorkflowExecutionPersistenceResult:
    """Immutable details of one fully persisted transition."""

    state_path: Path
    events_path: Path
    state_bytes_written: int
    event_bytes_appended: int


@dataclass(frozen=True)
class WorkflowExecutionPersistenceFailureDetail:
    """Safe structured classification of a handled persistence failure."""

    operation: str


class WorkflowExecutionPersistenceInputError(ValueError):
    """Raised when a transition and persistence targets are inconsistent."""


class WorkflowExecutionPersistenceError(RuntimeError):
    """Raised after a handled persistence failure and successful rollback."""


class WorkflowExecutionPersistenceRollbackError(WorkflowExecutionPersistenceError):
    """Raised when one or more restoration operations also fail."""

    def __init__(
        self,
        primary_failure: WorkflowExecutionPersistenceFailureDetail,
        rollback_failures: tuple[WorkflowExecutionPersistenceFailureDetail, ...],
    ) -> None:
        super().__init__(_ROLLBACK_ERROR_MESSAGE)
        self.primary_failure = primary_failure
        self.rollback_failures = rollback_failures


@dataclass(frozen=True)
class _OriginalTarget:
    existed: bool
    contents: bytes | None


def build_workflow_execution_state_dict(
    state: WorkflowExecutionState,
) -> dict[str, object]:
    """Build a JSON-compatible state dictionary in deterministic key order."""
    return {
        "workflow_id": state.workflow_id,
        "status": state.status,
        "current_step_id": state.current_step_id,
        "current_step_index": state.current_step_index,
        "current_employee_id": state.current_employee_id,
        "completed_step_ids": list(state.completed_step_ids),
        "last_failure_category": state.last_failure_category,
    }


def serialize_workflow_execution_state_json(state: WorkflowExecutionState) -> str:
    """Serialize one state as compact deterministic JSON with one newline."""
    return _serialize_json(build_workflow_execution_state_dict(state)) + "\n"


def build_runtime_step_event_dict(event: RuntimeStepEvent) -> dict[str, object]:
    """Build a JSON-compatible runtime event dictionary in deterministic order."""
    return {
        "event_type": event.event_type,
        "workflow_id": event.workflow_id,
        "step_id": event.step_id,
        "step_index": event.step_index,
        "employee_id": event.employee_id,
        "previous_status": event.previous_status,
        "next_status": event.next_status,
        "provider": event.provider,
        "failure_category": event.failure_category,
        "response_id": event.response_id,
        "request_id": event.request_id,
        "output_text": event.output_text,
        "message": event.message,
    }


def serialize_runtime_step_event_jsonl(event: RuntimeStepEvent) -> str:
    """Serialize exactly one compact JSONL event record."""
    return _serialize_json(build_runtime_step_event_dict(event)) + "\n"


def persist_workflow_execution_transition(
    transition: WorkflowExecutionTransition,
    targets: WorkflowExecutionPersistenceTargets,
) -> WorkflowExecutionPersistenceResult:
    """Persist one transition or restore both targets after a handled failure."""
    try:
        _validate_persistence_input(transition, targets)
    except OSError:
        raise WorkflowExecutionPersistenceError(_PERSISTENCE_ERROR_MESSAGE) from None
    state_bytes = serialize_workflow_execution_state_json(transition.next_state).encode(
        "utf-8"
    )
    event_bytes = serialize_runtime_step_event_jsonl(transition.event).encode("utf-8")

    try:
        original_state = _capture_original_target(targets.state_path)
        original_events = _capture_original_target(targets.events_path)
    except OSError:
        raise WorkflowExecutionPersistenceError(_PERSISTENCE_ERROR_MESSAGE) from None

    try:
        _replace_state_bytes(targets.state_path, state_bytes)
        _append_event_bytes(targets.events_path, event_bytes)
    except OSError:
        rollback_failures = _restore_targets(
            targets,
            original_state,
            original_events,
        )
        primary_failure = WorkflowExecutionPersistenceFailureDetail("persistence")
        if rollback_failures:
            raise WorkflowExecutionPersistenceRollbackError(
                primary_failure,
                rollback_failures,
            ) from None
        raise WorkflowExecutionPersistenceError(_PERSISTENCE_ERROR_MESSAGE) from None

    return WorkflowExecutionPersistenceResult(
        state_path=targets.state_path,
        events_path=targets.events_path,
        state_bytes_written=len(state_bytes),
        event_bytes_appended=len(event_bytes),
    )


def _validate_persistence_input(
    transition: WorkflowExecutionTransition,
    targets: WorkflowExecutionPersistenceTargets,
) -> None:
    previous_state = transition.previous_state
    next_state = transition.next_state
    event = transition.event
    paths_are_invalid = (
        targets.state_path == targets.events_path
        or targets.state_path.is_dir()
        or targets.events_path.is_dir()
        or not targets.state_path.parent.is_dir()
        or not targets.events_path.parent.is_dir()
    )
    transition_is_invalid = (
        previous_state.workflow_id != next_state.workflow_id
        or event.workflow_id != next_state.workflow_id
        or previous_state.current_step_id != next_state.current_step_id
        or previous_state.current_step_index != next_state.current_step_index
        or previous_state.current_employee_id != next_state.current_employee_id
        or event.step_id != next_state.current_step_id
        or event.step_index != next_state.current_step_index
        or event.employee_id != next_state.current_employee_id
        or previous_state.status != "running"
        or event.previous_status != previous_state.status
        or event.next_status != next_state.status
        or (next_state.status == "succeeded" and event.event_type != "step_succeeded")
        or (next_state.status == "failed" and event.event_type != "step_failed")
    )
    if paths_are_invalid or transition_is_invalid:
        raise WorkflowExecutionPersistenceInputError(_INPUT_ERROR_MESSAGE) from None


def _serialize_json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _capture_original_target(path: Path) -> _OriginalTarget:
    if path.exists():
        return _OriginalTarget(existed=True, contents=path.read_bytes())
    return _OriginalTarget(existed=False, contents=None)


def _replace_state_bytes(path: Path, contents: bytes) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        with temporary_path.open("xb") as temporary_file:
            temporary_file.write(contents)
            temporary_file.flush()
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path.exists():
            temporary_path.unlink()
        raise


def _append_event_bytes(path: Path, contents: bytes) -> None:
    with path.open("ab") as event_file:
        event_file.write(contents)
        event_file.flush()


def _restore_targets(
    targets: WorkflowExecutionPersistenceTargets,
    original_state: _OriginalTarget,
    original_events: _OriginalTarget,
) -> tuple[WorkflowExecutionPersistenceFailureDetail, ...]:
    failures: list[WorkflowExecutionPersistenceFailureDetail] = []
    for path, original, operation in (
        (targets.events_path, original_events, "restore_events"),
        (targets.state_path, original_state, "restore_state"),
    ):
        try:
            _restore_target(path, original)
        except OSError:
            failures.append(WorkflowExecutionPersistenceFailureDetail(operation))
    return tuple(failures)


def _restore_target(path: Path, original: _OriginalTarget) -> None:
    if original.existed:
        path.write_bytes(original.contents or b"")
    elif path.exists():
        path.unlink()
