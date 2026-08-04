"""Phase 108 classified persisted-outcome progression cycle continuation boundary."""

# ruff: noqa: E501,E701

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.classified_outcome_cycle_closure_continuation_boundary import (
    ClassifiedOutcomeCycleClosureContinuationError,
    route_classified_outcome_cycle_closure_continuation_boundary,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.terminal_history_contract import (
    TerminalHistoryContractError,
    load_strict_terminal_history,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationFailureCategory

Classification = Literal[
    "result_type", "workflow_definition", "success_contract", "failure_contract",
    "completion_contract", "state_target", "event_target", "target_conflict",
    "terminal_contract", "progression_contract", "dependency_error",
    "dependency_rollback",
]
Phase101Function = Callable[..., WorkflowProgressionDecision | PersistedExecutionOutcome]
_PATH_TYPE = type(Path())
_FAILURE_CATEGORIES = frozenset(get_args(ModelInvocationFailureCategory))


@dataclass(frozen=True)
class ClassifiedPersistedOutcomeProgressionCycleContinuationFailureDetail:
    classification: Classification


class ClassifiedPersistedOutcomeProgressionCycleContinuationError(ValueError):
    """Raised when Phase 108 cannot safely progress one classified outcome."""


class ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError(
    ClassifiedPersistedOutcomeProgressionCycleContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "classified persisted outcome progression cycle continuation inputs are incompatible"
        )
        self.detail = ClassifiedPersistedOutcomeProgressionCycleContinuationFailureDetail(
            classification
        )


def route_classified_persisted_outcome_progression_cycle_continuation_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase101_function: Phase101Function = route_classified_outcome_cycle_closure_continuation_boundary,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Advance only a valid persisted success through the public Phase 101 edge."""
    _validate_inputs(result, workflow, state_path, events_path, phase101_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _PATH_TYPE and type(events_path) is _PATH_TYPE

    if type(result) is WorkflowProgressionDecision:
        _validate_completion(result, workflow)
        terminal_status = "succeeded"
    else:
        assert type(result) is PersistedExecutionOutcome
        if result.outcome == "persisted_success":
            _validate_success(result, workflow)
            terminal_status = "succeeded"
        elif result.outcome == "persisted_failure":
            _validate_failure(result, workflow)
            terminal_status = "failed"
        else:
            _raise("result_type")

    _validate_targets(state_path, events_path)
    original = _capture(state_path, events_path)
    _validate_terminal(result, workflow, state_path, events_path, terminal_status)
    _require_unchanged(state_path, events_path, original, "terminal_contract")

    if type(result) is WorkflowProgressionDecision or result.outcome == "persisted_failure":
        return result

    try:
        progressed = phase101_function(result, workflow, state_path, events_path)
    except ClassifiedOutcomeCycleClosureContinuationError as error:
        _compensate_dependency_error(state_path, events_path, original, error)
    except Exception:
        _compensate_dependency_error(state_path, events_path, original, None)

    if _changed(state_path, events_path, original):
        _restore_or_raise(state_path, events_path, original)
        _raise("progression_contract")
    _validate_progression(progressed, result, workflow)
    return progressed


def _raise(classification: Classification) -> None:
    raise ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError(
        classification
    )


def _validate_inputs(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    dependency: object,
) -> None:
    if type(result) not in {PersistedExecutionOutcome, WorkflowProgressionDecision}:
        _raise("result_type")
    if (
        type(workflow) is not WorkflowDefinition
        or type(workflow.id) is not str
        or type(workflow.name) is not str
        or type(workflow.description) is not str
        or type(workflow.steps) is not list
        or not workflow.steps
    ):
        _raise("workflow_definition")
    if any(
        type(step) is not WorkflowStepDefinition
        or type(step.id) is not str
        or type(step.name) is not str
        or type(step.employee) is not str
        or type(step.instructions) is not str
        for step in workflow.steps
    ):
        _raise("workflow_definition")
    if type(state_path) is not _PATH_TYPE:
        _raise("state_target")
    if type(events_path) is not _PATH_TYPE:
        _raise("event_target")
    if state_path == events_path:
        _raise("target_conflict")
    if not callable(dependency):
        _raise("dependency_error")


def _exact_str(value: object) -> bool:
    return type(value) is str


def _exact_optional_str(value: object) -> bool:
    return value is None or type(value) is str


def _validate_common_identity(result: object, workflow: WorkflowDefinition) -> bool:
    return (
        _exact_str(result.workflow_id)
        and _exact_str(result.current_step_id)
        and type(result.current_step_index) is int
        and _exact_str(result.current_employee_id)
        and 1 <= result.current_step_index <= len(workflow.steps)
        and result.workflow_id == workflow.id
        and result.current_step_id == workflow.steps[result.current_step_index - 1].id
        and result.current_employee_id
        == workflow.steps[result.current_step_index - 1].employee
    )


def _validate_success(result: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if (
        type(result.outcome) is not str
        or result.outcome != "persisted_success"
        or result.failure_category is not None
        or not _validate_common_identity(result, workflow)
    ):
        _raise("success_contract")


def _validate_failure(result: PersistedExecutionOutcome, workflow: WorkflowDefinition) -> None:
    if (
        type(result.outcome) is not str
        or result.outcome != "persisted_failure"
        or type(result.failure_category) is not str
        or result.failure_category not in _FAILURE_CATEGORIES
        or not _validate_common_identity(result, workflow)
    ):
        _raise("failure_contract")


def _validate_completion(
    result: WorkflowProgressionDecision, workflow: WorkflowDefinition
) -> None:
    if (
        type(result.decision) is not str
        or result.decision != "workflow_complete"
        or not _validate_common_identity(result, workflow)
        or result.current_step_index != len(workflow.steps)
        or result.next_step_id is not None
        or result.next_step_index is not None
        or result.next_employee_id is not None
        or type(result.reason) is not str
        or result.reason != "last_step_succeeded"
    ):
        _raise("completion_contract")


def _validate_targets(state_path: Path, events_path: Path) -> None:
    for path, classification in ((state_path, "state_target"), (events_path, "event_target")):
        try:
            if not path.is_file():
                _raise(classification)
        except OSError:
            _raise(classification)


def _capture(state_path: Path, events_path: Path) -> tuple[bytes, bytes]:
    values = []
    for path, classification in ((state_path, "state_target"), (events_path, "event_target")):
        try:
            values.append(path.read_bytes())
        except OSError:
            _raise(classification)
    return values[0], values[1]


def _validate_terminal(
    result: PersistedExecutionOutcome | WorkflowProgressionDecision,
    workflow: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    status: str,
) -> None:
    try:
        state, events = load_strict_terminal_history(workflow, state_path, events_path)
    except TerminalHistoryContractError:
        _raise("terminal_contract")
    final = events[-1]
    expected_failure = result.failure_category if type(result) is PersistedExecutionOutcome else None
    if (
        state.status != status
        or state.workflow_id != result.workflow_id
        or state.current_step_id != result.current_step_id
        or state.current_step_index != result.current_step_index
        or state.current_employee_id != result.current_employee_id
        or state.last_failure_category != expected_failure
        or final.workflow_id != result.workflow_id
        or final.step_id != result.current_step_id
        or final.step_index != result.current_step_index
        or final.employee_id != result.current_employee_id
        or final.next_status != status
        or final.failure_category != expected_failure
    ):
        _raise("terminal_contract")


def _validate_progression(
    decision: object,
    outcome: PersistedExecutionOutcome,
    workflow: WorkflowDefinition,
) -> None:
    if type(decision) is not WorkflowProgressionDecision:
        _raise("progression_contract")
    expected_final = outcome.current_step_index == len(workflow.steps)
    expected_decision = "workflow_complete" if expected_final else "prepare_next_step"
    expected_reason = "last_step_succeeded" if expected_final else "next_step_available"
    next_step = None if expected_final else workflow.steps[outcome.current_step_index]
    if (
        type(decision.decision) is not str
        or decision.decision != expected_decision
        or not _validate_common_identity(decision, workflow)
        or decision.workflow_id != outcome.workflow_id
        or decision.current_step_id != outcome.current_step_id
        or decision.current_step_index != outcome.current_step_index
        or decision.current_employee_id != outcome.current_employee_id
        or not _exact_optional_str(decision.next_step_id)
        or (decision.next_step_index is not None and type(decision.next_step_index) is not int)
        or not _exact_optional_str(decision.next_employee_id)
        or decision.next_step_id != (None if next_step is None else next_step.id)
        or decision.next_step_index != (None if next_step is None else outcome.current_step_index + 1)
        or decision.next_employee_id != (None if next_step is None else next_step.employee)
        or type(decision.reason) is not str
        or decision.reason != expected_reason
    ):
        _raise("progression_contract")


def _changed(state_path: Path, events_path: Path, original: tuple[bytes, bytes]) -> bool:
    try:
        return state_path.read_bytes() != original[0] or events_path.read_bytes() != original[1]
    except OSError:
        return True


def _restore_or_raise(
    state_path: Path, events_path: Path, original: tuple[bytes, bytes]
) -> None:
    failed = False
    for path, data in ((state_path, original[0]), (events_path, original[1])):
        try:
            path.write_bytes(data)
        except OSError:
            failed = True
    if failed:
        _raise("dependency_rollback")


def _require_unchanged(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    classification: Classification,
) -> None:
    if _changed(state_path, events_path, original):
        _restore_or_raise(state_path, events_path, original)
        _raise(classification)


def _compensate_dependency_error(
    state_path: Path,
    events_path: Path,
    original: tuple[bytes, bytes],
    safe_error: ClassifiedOutcomeCycleClosureContinuationError | None,
) -> None:
    if _changed(state_path, events_path, original):
        _restore_or_raise(state_path, events_path, original)
    if safe_error is not None:
        raise safe_error
    _raise("dependency_error")
