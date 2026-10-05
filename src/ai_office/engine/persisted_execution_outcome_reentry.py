"""Read-only classification for one persisted Phase 36 execution outcome."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.invocation import ModelInvocationFailureCategory
from ai_office.runtime import (
    RuntimeStepEvent,
    WorkflowExecutionState,
    WorkflowRunBinding,
    binding_of,
    select_run_binding,
)
from ai_office.storage.workflow_execution_history import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionLoadError,
    load_workflow_execution_history,
)
from ai_office.storage.workflow_execution_persistence import (
    WorkflowExecutionPersistenceTargets,
)

PersistedExecutionOutcomeClassification = Literal[
    "workflow_definition",
    "state_target",
    "event_target",
    "target_conflict",
    "history_data",
    "state_status",
    "state_identity",
    "workflow_identity",
    "event_history",
    "classification_contract",
    "history_rollback",
]
PersistedExecutionOutcomeType = Literal["persisted_success", "persisted_failure"]
_ERROR_MESSAGE = "persisted execution outcome inputs are incompatible"


@dataclass(frozen=True, init=False)
class PersistedExecutionOutcome:
    """Minimal immutable classification of one persisted terminal outcome."""

    outcome: PersistedExecutionOutcomeType
    workflow_id: str
    current_step_id: str
    current_step_index: int
    current_employee_id: str
    failure_category: ModelInvocationFailureCategory | None

    def __init__(
        self,
        outcome: PersistedExecutionOutcomeType,
        workflow_id: str,
        current_step_id: str,
        current_step_index: int,
        current_employee_id: str,
        failure_category: ModelInvocationFailureCategory | None,
        *,
        binding: WorkflowRunBinding | None = None,
    ) -> None:
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "workflow_id", workflow_id)
        object.__setattr__(self, "current_step_id", current_step_id)
        object.__setattr__(self, "current_step_index", current_step_index)
        object.__setattr__(self, "current_employee_id", current_employee_id)
        object.__setattr__(self, "failure_category", failure_category)
        object.__setattr__(
            self, "_run_binding", select_run_binding(binding, None, None)
        )

    def __eq__(self, other: object) -> bool:
        """Compare the outcome together with its direct Run binding."""
        if type(other) is not PersistedExecutionOutcome:
            return NotImplemented
        assert isinstance(other, PersistedExecutionOutcome)
        return (
            self.outcome,
            self.workflow_id,
            self.current_step_id,
            self.current_step_index,
            self.current_employee_id,
            self.failure_category,
            binding_of(self),
        ) == (
            other.outcome,
            other.workflow_id,
            other.current_step_id,
            other.current_step_index,
            other.current_employee_id,
            other.failure_category,
            binding_of(other),
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.outcome,
                self.workflow_id,
                self.current_step_id,
                self.current_step_index,
                self.current_employee_id,
                self.failure_category,
                binding_of(self),
            )
        )

    @property
    def run_id(self) -> str | None:
        binding = binding_of(self)
        return None if binding is None else binding.run_id

    @property
    def manifest_digest(self) -> str | None:
        binding = binding_of(self)
        return None if binding is None else binding.manifest_digest


@dataclass(frozen=True)
class PersistedExecutionOutcomeFailureDetail:
    """Safe category for a Phase 37 compatibility rejection."""

    classification: PersistedExecutionOutcomeClassification


class PersistedExecutionOutcomeError(ValueError):
    """Raised when a persisted terminal outcome cannot be classified safely."""


class PersistedExecutionOutcomeCompatibilityError(PersistedExecutionOutcomeError):
    """Raised for a safe incompatibility in the Phase 37 boundary."""

    def __init__(self, classification: PersistedExecutionOutcomeClassification) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.detail = PersistedExecutionOutcomeFailureDetail(classification)


def classify_persisted_execution_outcome_reentry(
    workflow: object,
    state_path: object,
    events_path: object,
) -> PersistedExecutionOutcome:
    """Classify one persisted Phase 36 outcome without progressing."""
    _validate_inputs(workflow, state_path, events_path)
    assert type(workflow) is WorkflowDefinition
    assert isinstance(state_path, Path)
    assert isinstance(events_path, Path)

    original = _capture_targets(state_path, events_path)
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(state_path, events_path)
        )
    except WorkflowExecutionLoadError:
        _restore_if_changed(state_path, events_path, original)
        raise
    except Exception:
        _restore_if_changed(state_path, events_path, original)
        _raise("history_data")
    _reject_changed_targets(state_path, events_path, original)

    return classify_loaded_persisted_execution_outcome(workflow, history)


def classify_loaded_persisted_execution_outcome(
    workflow: object,
    history: object,
) -> PersistedExecutionOutcome:
    """Classify one already-loaded history without touching persistence targets."""
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    assert type(workflow) is WorkflowDefinition
    validate_loaded_persisted_execution_history(
        workflow, history, require_terminal_state=True
    )
    assert type(history) is LoadedWorkflowExecutionHistory
    result = _build_result(history.state)
    _validate_result_contract(result, history.state)
    return result


def validate_loaded_persisted_execution_history(
    workflow: object,
    history: object,
    *,
    require_terminal_state: bool = False,
    allow_unstarted_running_current_step: bool = False,
) -> None:
    """Validate one loaded state and transcript against its pinned workflow."""
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if type(history) is not LoadedWorkflowExecutionHistory:
        _raise("history_data")
    _validate_history_contents(history)
    if require_terminal_state:
        _validate_terminal_state(history.state)
    _validate_workflow_identity(workflow, history.state)
    _validate_event_history(
        workflow,
        history.state,
        history.events,
        allow_unstarted_running_current_step=allow_unstarted_running_current_step,
    )


def _validate_inputs(
    workflow: object,
    state_path: object,
    events_path: object,
) -> None:
    if type(workflow) is not WorkflowDefinition:
        _raise("workflow_definition")
    if not isinstance(state_path, Path):
        _raise("state_target")
    if not isinstance(events_path, Path):
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    try:
        if not state_path.is_file():
            _raise("state_target")
        if not events_path.is_file():
            _raise("event_target")
    except OSError:
        _raise("history_data")


def _capture_targets(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    try:
        return state_path.read_bytes(), events_path.read_bytes()
    except OSError:
        _raise("history_data")


def _restore_if_changed(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        if _target_needs_restore(state_path, original[0]):
            state_path.write_bytes(original[0])
        if _target_needs_restore(events_path, original[1]):
            events_path.write_bytes(original[1])
    except OSError:
        _raise("history_rollback")


def _target_needs_restore(path: Path, original: bytes) -> bool:
    try:
        return path.read_bytes() != original
    except FileNotFoundError:
        return True


def _reject_changed_targets(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    try:
        changed = (
            state_path.read_bytes() != original[0]
            or events_path.read_bytes() != original[1]
        )
    except OSError:
        _restore_if_changed(state_path, events_path, original)
        _raise("history_data")
    if changed:
        _restore_if_changed(state_path, events_path, original)
        _raise("history_data")


def _validate_history_contents(history: LoadedWorkflowExecutionHistory) -> None:
    if (
        type(history.state) is not WorkflowExecutionState
        or type(history.events) is not tuple
        or any(type(event) is not RuntimeStepEvent for event in history.events)
    ):
        _raise("history_data")


def _validate_terminal_state(state: WorkflowExecutionState) -> None:
    if state.status not in {"succeeded", "failed"}:
        _raise("state_status")


def _validate_workflow_identity(
    workflow: WorkflowDefinition, state: WorkflowExecutionState
) -> None:
    if workflow.id != state.workflow_id or not 1 <= state.current_step_index <= len(
        workflow.steps
    ):
        _raise("workflow_identity")
    current = workflow.steps[state.current_step_index - 1]
    if (
        current.id != state.current_step_id
        or current.employee != state.current_employee_id
    ):
        _raise("workflow_identity")
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    try:
        completed = tuple(positions[step_id] for step_id in state.completed_step_ids)
    except KeyError:
        _raise("workflow_identity")
    compressed = tuple(
        index
        for position, index in enumerate(completed)
        if position == 0 or completed[position - 1] != index
    )
    final_index = (
        state.current_step_index
        if state.status == "succeeded"
        else state.current_step_index - 1
    )
    if compressed != tuple(range(1, final_index + 1)):
        _raise("workflow_identity")
    if state.status == "succeeded":
        if (
            not state.completed_step_ids
            or state.completed_step_ids[-1] != state.current_step_id
        ):
            _raise("state_identity")
    elif state.current_step_id in state.completed_step_ids:
        _raise("state_identity")


def _validate_event_history(
    workflow: WorkflowDefinition,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
    *,
    allow_unstarted_running_current_step: bool = False,
) -> None:
    if not events and not (
        state.status == "running" and allow_unstarted_running_current_step
    ):
        _raise("event_history")
    positions = {step.id: index for index, step in enumerate(workflow.steps, 1)}
    groups: dict[int, list[RuntimeStepEvent]] = {}
    previous_index = 0
    for event in events:
        if (
            event.workflow_id != state.workflow_id
            or event.step_id not in positions
            or event.step_index != positions[event.step_id]
            or event.employee_id != workflow.steps[event.step_index - 1].employee
            or event.step_index > state.current_step_index
            or event.step_index < previous_index
        ):
            _raise("event_history")
        previous_index = event.step_index
        groups.setdefault(event.step_index, []).append(event)

    completed_before = (
        state.current_step_index
        if state.status == "succeeded"
        else state.current_step_index - 1
    )
    expected_completed = tuple(
        step.id for step in workflow.steps[:completed_before]
    )
    if state.completed_step_ids != expected_completed:
        _raise("event_history")

    for index in range(1, state.current_step_index + 1):
        group = groups.get(index)
        if not group:
            if (
                index == state.current_step_index
                and state.status == "running"
                and allow_unstarted_running_current_step
            ):
                continue
            _raise("event_history")
        end_status: str | None = None
        for event in group:
            if event.event_type == "step_recovery_started":
                if event.previous_status == "failed":
                    valid = end_status == "failed"
                else:
                    valid = end_status in {None, "running"}
                if not valid or event.next_status != "running":
                    _raise("event_history")
                end_status = "running"
            else:
                if event.previous_status != "running" or end_status not in {
                    None,
                    "running",
                }:
                    _raise("event_history")
                if event.event_type == "step_succeeded":
                    if (
                        event.next_status != "succeeded"
                        or event.failure_category is not None
                        or event.message is not None
                        or not isinstance(event.response_id, str)
                        or not isinstance(event.output_text, str)
                    ):
                        _raise("event_history")
                    end_status = "succeeded"
                else:
                    if (
                        event.next_status != "failed"
                        or event.failure_category is None
                        or event.message is None
                        or event.response_id is not None
                        or event.output_text is not None
                    ):
                        _raise("event_history")
                    end_status = "failed"
        if index < state.current_step_index and end_status != "succeeded":
            _raise("event_history")
        if index == state.current_step_index:
            if (
                group[-1].step_id != state.current_step_id
                or group[-1].step_index != state.current_step_index
                or group[-1].employee_id != state.current_employee_id
                or end_status != state.status
                or (
                    state.status == "succeeded"
                    and state.last_failure_category is not None
                )
                or (
                    state.status == "failed"
                    and state.last_failure_category != group[-1].failure_category
                )
            ):
                _raise("event_history")
    if any(index > state.current_step_index for index in groups):
        _raise("event_history")


def _build_result(state: WorkflowExecutionState) -> PersistedExecutionOutcome:
    result = PersistedExecutionOutcome(
        outcome=(
            "persisted_success" if state.status == "succeeded" else "persisted_failure"
        ),
        workflow_id=state.workflow_id,
        current_step_id=state.current_step_id,
        current_step_index=state.current_step_index,
        current_employee_id=state.current_employee_id,
        failure_category=state.last_failure_category,
        binding=binding_of(state),
    )
    return result


def _validate_result_contract(result: object, state: WorkflowExecutionState) -> None:
    expected_outcome = (
        "persisted_success" if state.status == "succeeded" else "persisted_failure"
    )
    valid = (
        type(result) is PersistedExecutionOutcome
        and result.outcome == expected_outcome
        and result.workflow_id == state.workflow_id
        and result.current_step_id == state.current_step_id
        and result.current_step_index == state.current_step_index
        and result.current_employee_id == state.current_employee_id
        and result.failure_category == state.last_failure_category
        and (
            (result.outcome == "persisted_success") == (result.failure_category is None)
        )
        and binding_of(result) == binding_of(state)
    )
    if not valid:
        _raise("classification_contract")


def _raise(classification: PersistedExecutionOutcomeClassification) -> None:
    raise PersistedExecutionOutcomeCompatibilityError(classification) from None
