"""Explicit state-only persistence for a Phase 27 proposed running state."""

from dataclasses import dataclass
from pathlib import Path

from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage.workflow_execution_persistence import (
    _capture_original_target,
    _replace_state_bytes,
    _restore_target,
    serialize_workflow_execution_state_json,
)

_INPUT_ERROR = "running state persistence inputs are inconsistent"
_PERSISTENCE_ERROR = "running state persistence failed"


@dataclass(frozen=True)
class RunningStatePersistenceResult:
    state_bytes_written: int


@dataclass(frozen=True)
class RunningStatePersistenceFailureDetail:
    operation: str


class RunningStatePersistenceError(RuntimeError):
    """Raised after a handled running-state persistence failure."""

    def __init__(self, operation: str) -> None:
        super().__init__(_PERSISTENCE_ERROR)
        self.detail = RunningStatePersistenceFailureDetail(operation)


class RunningStatePersistenceRollbackError(RunningStatePersistenceError):
    """Raised when restoration after a handled failure also fails."""


class RunningStatePersistenceInputError(ValueError):
    """Raised for an incompatible proposed running state or target."""


def persist_prepared_running_state(
    start: object, state_path: Path
) -> RunningStatePersistenceResult:
    """Safely replace one explicit state target without creating runtime events."""
    # Import lazily: engine depends on storage history models, while storage owns
    # this persistence boundary.  Phase 27's request has no workflow/step identity,
    # so only its existing immutable model contract can be checked here.
    from ai_office.engine.prepared_step_execution_start import (
        PreparedStepExecutionStart,
    )

    if not isinstance(start, PreparedStepExecutionStart):
        raise RunningStatePersistenceInputError(_INPUT_ERROR) from None
    state = start.running_state
    try:
        invalid = (
            not isinstance(start.request, ModelInvocationRequest)
            or not isinstance(state, WorkflowExecutionState)
            or state_path.is_dir()
            or not state_path.parent.is_dir()
            or state.status != "running"
            or state.last_failure_category is not None
            or not _nonempty_string(state.workflow_id)
            or not _nonempty_string(state.current_step_id)
            or not _nonempty_string(state.current_employee_id)
            or isinstance(state.current_step_index, bool)
            or not isinstance(state.current_step_index, int)
            or state.current_step_index < 1
            or not isinstance(state.completed_step_ids, tuple)
            or not all(isinstance(item, str) for item in state.completed_step_ids)
        )
    except OSError:
        raise RunningStatePersistenceError("target") from None
    if invalid:
        raise RunningStatePersistenceInputError(_INPUT_ERROR) from None
    contents = serialize_workflow_execution_state_json(state).encode("utf-8")
    try:
        original = _capture_original_target(state_path)
    except OSError:
        raise RunningStatePersistenceError("target") from None
    try:
        _replace_state_bytes(state_path, contents)
    except OSError:
        raise RunningStatePersistenceError("write") from None
    try:
        if state_path.read_bytes() != contents:
            raise OSError
    except OSError:
        try:
            _restore_target(state_path, original)
        except OSError:
            raise RunningStatePersistenceRollbackError("rollback") from None
        raise RunningStatePersistenceError("verification") from None
    return RunningStatePersistenceResult(len(contents))


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)
