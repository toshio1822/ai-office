"""Phase 100 persisted-outcome classification dispatch continuation boundary."""

# ruff: noqa: E501

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation as _phase93,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.storage import WorkflowExecutionPersistenceResult

Classification = Literal[
    "result_type", "workflow_definition", "completion_contract", "failure_contract",
    "state_target", "event_target", "target_conflict", "terminal_contract",
    "persistence_contract", "outcome_contract", "dependency_error", "dependency_rollback",
]
Phase93Function = Callable[..., PersistedExecutionOutcome]


@dataclass(frozen=True)
class PersistedOutcomeClassificationDispatchContinuationFailureDetail:
    classification: Classification


class PersistedOutcomeClassificationDispatchContinuationError(ValueError):
    """Raised when Phase 100 cannot safely dispatch a supplied value."""


class PersistedOutcomeClassificationDispatchContinuationCompatibilityError(
    PersistedOutcomeClassificationDispatchContinuationError
):
    def __init__(self, classification: Classification) -> None:
        super().__init__(
            "persisted outcome classification dispatch continuation inputs are incompatible"
        )
        self.detail = PersistedOutcomeClassificationDispatchContinuationFailureDetail(
            classification
        )


def route_persisted_outcome_classification_dispatch_continuation_boundary(
    result: object,
    workflow: object,
    state_path: object,
    events_path: object,
    *,
    phase93_function: Phase93Function = (
        _phase93.route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation
    ),
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    """Validate and dispatch one Phase 99 persistence result, or stop at a terminal value."""
    _top_level(result, workflow, state_path, events_path, phase93_function)
    assert type(workflow) is WorkflowDefinition
    assert type(state_path) is _phase93._phase86._PATH_TYPE  # type: ignore[attr-defined]
    assert type(events_path) is _phase93._phase86._PATH_TYPE  # type: ignore[attr-defined]

    if type(result) is WorkflowProgressionDecision:
        _validate_stop(result, workflow, state_path, events_path, "succeeded")
        return result
    if type(result) is PersistedExecutionOutcome:
        _validate_stop(result, workflow, state_path, events_path, "failed")
        return result

    _validate_result_model(result, state_path, events_path)
    try:
        original = _phase93._phase86._capture(state_path, events_path)  # type: ignore[attr-defined]
        state = _phase93._phase86._validate_persistence(  # type: ignore[attr-defined]
            result, workflow, state_path, events_path
        )
    except Exception as error:
        _translate_preflight(error)
    try:
        value = phase93_function(result, workflow, state_path, events_path)
    except _phase93.PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationError as error:
        _compensate_or_raise(state_path, events_path, original, error, preserve=True)
        raise AssertionError("unreachable")
    except Exception as error:
        _compensate_or_raise(state_path, events_path, original, error, preserve=False)
        raise AssertionError("unreachable")

    try:
        _phase93._phase86._require_unchanged(  # type: ignore[attr-defined]
            state_path, events_path, original, "outcome_contract"
        )
        _phase93._phase86._validate_outcome(value, state)  # type: ignore[attr-defined]
    except Exception as error:
        classification = getattr(getattr(error, "detail", None), "classification", None)
        if classification == "dependency_rollback":
            _raise("dependency_rollback")
        try:
            _phase93._phase86._restore_if_changed(  # type: ignore[attr-defined]
                state_path, events_path, original
            )
        except Exception as rollback:
            if getattr(getattr(rollback, "detail", None), "classification", None) == "dependency_rollback":
                _raise("dependency_rollback")
            _raise("dependency_rollback")
        if classification == "outcome_contract":
            _raise("outcome_contract")
        _raise("outcome_contract")
    return value


def _top_level(result: object, workflow: object, state: object, events: object, function: object) -> None:
    if type(result) not in (
        WorkflowExecutionPersistenceResult,
        WorkflowProgressionDecision,
        PersistedExecutionOutcome,
    ):
        _raise("result_type")
    if type(workflow) is not WorkflowDefinition or not _valid_workflow(workflow):
        _raise("workflow_definition")
    path_type = _phase93._phase86._PATH_TYPE  # type: ignore[attr-defined]
    if type(state) is not path_type:
        _raise("state_target")
    if type(events) is not path_type:
        _raise("event_target")
    if state == events:
        _raise("target_conflict")
    if not callable(function):
        _raise("persistence_contract")


def _validate_result_model(result: object, state: object, events: object) -> None:
    if type(result) is not WorkflowExecutionPersistenceResult:
        _raise("result_type")
    if result.state_path is not state or result.events_path is not events:  # type: ignore[union-attr]
        _raise("persistence_contract")
    if type(result.state_bytes_written) is not int or type(result.event_bytes_appended) is not int:  # type: ignore[union-attr]
        _raise("persistence_contract")
    if result.state_bytes_written <= 0 or result.event_bytes_appended <= 0:  # type: ignore[union-attr]
        _raise("persistence_contract")


def _valid_workflow(workflow: WorkflowDefinition) -> bool:
    return (
        type(workflow.id) is str
        and type(workflow.name) is str
        and type(workflow.description) is str
        and type(workflow.steps) is list
        and bool(workflow.steps)
        and all(
            type(step) is WorkflowStepDefinition
            and type(step.id) is str
            and type(step.name) is str
            and type(step.employee) is str
            and type(step.instructions) is str
            for step in workflow.steps
        )
    )
def _validate_stop(value: WorkflowProgressionDecision | PersistedExecutionOutcome, workflow: WorkflowDefinition, state: object, events: object, status: Literal["succeeded", "failed"]) -> None:
    try:
        if type(value) is WorkflowProgressionDecision:
            _phase93._phase86._validate_completion(value, workflow)  # type: ignore[attr-defined]
        else:
            _phase93._phase86._validate_failure(value, workflow)  # type: ignore[attr-defined]
        _phase93._phase86._validate_targets(state, events)  # type: ignore[attr-defined]
        _phase93._phase86._validate_terminal(value, workflow, state, events, status)  # type: ignore[attr-defined]
        original = _phase93._phase86._capture(state, events)  # type: ignore[attr-defined]
        _phase93._phase86._require_unchanged(state, events, original, "terminal_contract")  # type: ignore[attr-defined]
    except Exception as error:
        classification = getattr(getattr(error, "detail", None), "classification", None)
        _raise(classification if classification in Classification.__args__ else "terminal_contract")  # type: ignore[arg-type]


def _compensate_or_raise(state: object, events: object, original: tuple[bytes, bytes], error: Exception, *, preserve: bool) -> None:
    try:
        _phase93._phase86._restore_if_changed(state, events, original)  # type: ignore[attr-defined]
    except Exception:
        _raise("dependency_rollback")
    if preserve:
        raise error
    _raise("dependency_error")


def _translate_preflight(error: Exception) -> None:
    classification = getattr(getattr(error, "detail", None), "classification", None)
    _raise(classification if classification in Classification.__args__ else "persistence_contract")  # type: ignore[arg-type]


def _raise(classification: Classification) -> None:
    raise PersistedOutcomeClassificationDispatchContinuationCompatibilityError(classification) from None
