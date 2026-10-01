"""Compensatable JSON and JSONL persistence for one workflow transition."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ai_office.runtime import (
    RuntimeStepEvent,
    WorkflowExecutionState,
    WorkflowExecutionTransition,
    binding_of,
)
from ai_office.runtime.run_binding import WorkflowRunBinding

_INPUT_ERROR_MESSAGE = "workflow execution persistence inputs are inconsistent"
_PERSISTENCE_ERROR_MESSAGE = "workflow execution persistence failed"
_ROLLBACK_ERROR_MESSAGE = "workflow execution persistence rollback failed"


@dataclass(frozen=True)
class WorkflowExecutionPersistenceTargets:
    """Explicit targets for one state/event pair.

    ``binding`` is optional for the provider-free historical persistence
    primitive.  Run-owned callers must supply it; when present it is an
    expected value, not metadata inferred from the two paths.  This keeps the
    durable Run contract direct while allowing the lower owner to retain its
    existing generic transition tests.
    """

    state_path: Path
    events_path: Path
    binding: WorkflowRunBinding | None = None
    namespace_root: Path | None = None

    def __post_init__(self) -> None:
        if type(self.state_path) is not type(Path()):
            raise TypeError("state_path must be a Path")
        if type(self.events_path) is not type(Path()):
            raise TypeError("events_path must be a Path")
        if self.binding is not None and type(self.binding) is not WorkflowRunBinding:
            raise TypeError("execution target binding is invalid")
        if self.namespace_root is not None and type(self.namespace_root) is not type(
            Path()
        ):
            raise TypeError("namespace_root must be a Path")
        # Run-owned filenames are an authoritative namespace even when a
        # lower path-based owner is called without an explicit target object.
        # Resolve the manifest from that namespace rather than allowing a
        # caller to pair an identity-derived history with another manifest.
        if self.binding is None and self.namespace_root is None:
            state_suffix = ".state.json"
            events_suffix = ".events.jsonl"
            state_name = self.state_path.name
            events_name = self.events_path.name
            if state_name.endswith(state_suffix) and events_name.endswith(
                events_suffix
            ):
                state_run_id = state_name[: -len(state_suffix)]
                events_run_id = events_name[: -len(events_suffix)]
                if not state_run_id or state_run_id != events_run_id:
                    raise ValueError("execution targets are not Run identity-derived")
                try:
                    from ai_office.engine.workflow_run_manifest import (
                        WorkflowRunManifestStore,
                        load_workflow_run_manifest,
                    )

                    manifest = load_workflow_run_manifest(
                        WorkflowRunManifestStore(self.state_path.parent), state_run_id
                    )
                    object.__setattr__(
                        self,
                        "binding",
                        WorkflowRunBinding(
                            manifest.run_id,
                            manifest.digest,
                            manifest.run_input,
                        ),
                    )
                    object.__setattr__(
                        self, "namespace_root", self.state_path.parent
                    )
                except Exception as error:
                    raise ValueError(
                        "identity-derived execution namespace is invalid"
                    ) from error
        if self.namespace_root is not None and self.binding is not None:
            namespace_root = self.namespace_root
        elif self.binding is not None:
            namespace_root = self.state_path.parent
        else:
            namespace_root = None
        if namespace_root is not None and self.binding is not None:
            expected_state = namespace_root / f"{self.binding.run_id}.state.json"
            expected_events = namespace_root / f"{self.binding.run_id}.events.jsonl"
            if self.state_path != expected_state or self.events_path != expected_events:
                raise ValueError("execution targets are not Run identity-derived")


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
    value: dict[str, object] = {
        "workflow_id": state.workflow_id,
        "status": state.status,
        "current_step_id": state.current_step_id,
        "current_step_index": state.current_step_index,
        "current_employee_id": state.current_employee_id,
        "completed_step_ids": list(state.completed_step_ids),
        "last_failure_category": state.last_failure_category,
    }
    binding = binding_of(state)
    if binding is not None:
        value["run_id"] = binding.run_id
        value["manifest_digest"] = binding.manifest_digest
        value["run_input"] = binding.run_input
    return value


def serialize_workflow_execution_state_json(state: WorkflowExecutionState) -> str:
    """Serialize one state as compact deterministic JSON with one newline."""
    return _serialize_json(build_workflow_execution_state_dict(state)) + "\n"


def build_runtime_step_event_dict(event: RuntimeStepEvent) -> dict[str, object]:
    """Build a JSON-compatible runtime event dictionary in deterministic order."""
    value: dict[str, object] = {
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
    binding = binding_of(event)
    if binding is not None:
        value["run_id"] = binding.run_id
        value["manifest_digest"] = binding.manifest_digest
        value["run_input"] = binding.run_input
    if event.response_diagnostics is not None:
        value["response_diagnostics"] = {
            "status_code": event.response_diagnostics.status_code,
            "content_type": event.response_diagnostics.content_type,
            "body_length": event.response_diagnostics.body_length,
            "body_kind": event.response_diagnostics.body_kind,
        }
    return value


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

    mutation_started = False
    try:
        _replace_state_bytes(targets.state_path, state_bytes)
        mutation_started = True
        _append_event_bytes(targets.events_path, event_bytes)
    except OSError:
        if not mutation_started:
            raise WorkflowExecutionPersistenceError(
                _PERSISTENCE_ERROR_MESSAGE
            ) from None
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
    previous_binding = binding_of(previous_state)
    next_binding = binding_of(next_state)
    event_binding = binding_of(event)
    binding_is_invalid = not (
        (previous_binding is None and next_binding is None and event_binding is None)
        or (
            previous_binding is not None
            and next_binding is not None
            and event_binding is not None
            and previous_binding.identity
            == next_binding.identity
            == event_binding.identity
        )
    )
    if targets.binding is not None and (
        next_binding is None or next_binding.identity != targets.binding.identity
    ):
        binding_is_invalid = True
    if next_binding is not None:
        expected_state = targets.state_path.parent / (
            f"{next_binding.run_id}.state.json"
        )
        expected_events = targets.events_path.parent / (
            f"{next_binding.run_id}.events.jsonl"
        )
        if (
            targets.state_path != expected_state
            or targets.events_path != expected_events
        ):
            binding_is_invalid = True
    if paths_are_invalid or transition_is_invalid or binding_is_invalid:
        raise WorkflowExecutionPersistenceInputError(_INPUT_ERROR_MESSAGE) from None


def _serialize_json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _capture_original_target(path: Path) -> _OriginalTarget:
    if path.exists():
        return _OriginalTarget(existed=True, contents=path.read_bytes())
    return _OriginalTarget(existed=False, contents=None)


def _replace_state_bytes(path: Path, contents: bytes) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_created = False
    try:
        with temporary_path.open("xb") as temporary_file:
            temporary_created = True
            temporary_file.write(contents)
            temporary_file.flush()
        os.replace(temporary_path, path)
    except OSError:
        if temporary_created and temporary_path.exists():
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
