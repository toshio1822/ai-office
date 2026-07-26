"""Tests for the read-only Phase 37 outcome-classification boundary."""

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcomeCompatibilityError,
    classify_persisted_execution_outcome_reentry,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionLoadError,
    WorkflowExecutionPersistenceTargets,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "Description",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "one",
                    "instructions": "a",
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "two",
                    "instructions": "b",
                },
            ],
        }
    )


def state(**changes: object) -> WorkflowExecutionState:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "status": "succeeded",
        "current_step_id": "second",
        "current_step_index": 2,
        "current_employee_id": "two",
        "completed_step_ids": ("first", "second"),
        "last_failure_category": None,
    }
    values.update(changes)
    return WorkflowExecutionState(**values)  # type: ignore[arg-type]


def event(**changes: object) -> RuntimeStepEvent:
    values: dict[str, object] = {
        "event_type": "step_succeeded",
        "workflow_id": "workflow",
        "step_id": "second",
        "step_index": 2,
        "employee_id": "two",
        "previous_status": "running",
        "next_status": "succeeded",
        "provider": "openai",
        "failure_category": None,
        "response_id": "response",
        "request_id": "request",
        "output_text": "output",
        "message": None,
    }
    values.update(changes)
    return RuntimeStepEvent(**values)  # type: ignore[arg-type]


def write_history(
    tmp_path: Path, value: WorkflowExecutionState, *events: RuntimeStepEvent
) -> WorkflowExecutionPersistenceTargets:
    targets = WorkflowExecutionPersistenceTargets(
        tmp_path / "state.json", tmp_path / "events.jsonl"
    )
    targets.state_path.write_text(serialize_workflow_execution_state_json(value))
    targets.events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events)
    )
    return targets


def first_event() -> RuntimeStepEvent:
    return event(step_id="first", step_index=1, employee_id="one")


def test_classifies_non_final_success_once_without_writing(tmp_path: Path) -> None:
    targets = write_history(
        tmp_path,
        state(
            current_step_id="first",
            current_step_index=1,
            current_employee_id="one",
            completed_step_ids=("first",),
        ),
        first_event(),
    )
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())
    calls = 0

    def loader(value: WorkflowExecutionPersistenceTargets) -> object:
        nonlocal calls
        calls += 1
        from ai_office.storage import load_workflow_execution_history

        return load_workflow_execution_history(value)

    result = classify_persisted_execution_outcome_reentry(
        workflow(), targets.state_path, targets.events_path, history_loader=loader
    )  # type: ignore[arg-type]
    assert result.outcome == "persisted_success"
    assert result.failure_category is None
    assert calls == 1
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


def test_classifies_final_success_as_exact_immutable_result(tmp_path: Path) -> None:
    targets = write_history(tmp_path, state(), first_event(), event())
    result = classify_persisted_execution_outcome_reentry(
        workflow(), targets.state_path, targets.events_path
    )
    assert type(result).__name__ == "PersistedExecutionOutcome"
    with pytest.raises(AttributeError):
        result.outcome = "persisted_failure"  # type: ignore[misc]


@pytest.mark.parametrize(
    "category",
    [
        "api_error",
        "transport_error",
        "invalid_response",
        "invalid_output",
        "invalid_request",
        "approval_required",
    ],
)
def test_classifies_each_persisted_failure(tmp_path: Path, category: str) -> None:
    failed = state(
        status="failed", completed_step_ids=("first",), last_failure_category=category
    )
    failed_event = event(
        event_type="step_failed",
        next_status="failed",
        failure_category=category,
        response_id=None,
        output_text=None,
        message="safe",
    )
    targets = write_history(tmp_path, failed, first_event(), failed_event)
    result = classify_persisted_execution_outcome_reentry(
        workflow(), targets.state_path, targets.events_path
    )
    assert result.outcome == "persisted_failure"
    assert result.failure_category == category


@pytest.mark.parametrize(
    ("actual_workflow", "actual_state", "actual_events", "classification"),
    [
        (object(), None, None, "workflow_definition"),
        (None, 3, None, "state_target"),
        (None, None, 3, "event_target"),
    ],
)
def test_prevalidation_rejects_without_calling_loader(
    tmp_path: Path,
    actual_workflow: object,
    actual_state: object,
    actual_events: object,
    classification: str,
) -> None:
    targets = write_history(tmp_path, state(), first_event(), event())
    calls = 0

    def loader(_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedExecutionOutcomeCompatibilityError) as error:
        classify_persisted_execution_outcome_reentry(
            workflow() if actual_workflow is None else actual_workflow,
            targets.state_path if actual_state is None else actual_state,
            targets.events_path if actual_events is None else actual_events,
            history_loader=loader,
        )  # type: ignore[arg-type]
    assert error.value.detail.classification == classification
    assert calls == 0


def test_rejects_target_conflict_without_calling_loader(tmp_path: Path) -> None:
    targets = write_history(tmp_path, state(), first_event(), event())
    with pytest.raises(PersistedExecutionOutcomeCompatibilityError) as error:
        classify_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.state_path
        )
    assert error.value.detail.classification == "target_conflict"


@pytest.mark.parametrize("missing", ["state", "events"])
def test_missing_target_rejects_without_calling_loader(
    tmp_path: Path, missing: str
) -> None:
    targets = write_history(tmp_path, state(), first_event(), event())
    (targets.state_path if missing == "state" else targets.events_path).unlink()
    calls = 0

    def loader(_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedExecutionOutcomeCompatibilityError) as error:
        classify_persisted_execution_outcome_reentry(
            workflow(),
            targets.state_path,
            targets.events_path,
            history_loader=loader,
        )  # type: ignore[arg-type]
    assert error.value.detail.classification == (
        "state_target" if missing == "state" else "event_target"
    )
    assert calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "ready"},
        {"status": "running"},
        {"current_step_id": "other"},
        {"current_step_index": 3},
        {"current_employee_id": "other"},
        {"completed_step_ids": ("second",)},
        {"completed_step_ids": ("first", "second")},
    ],
)
def test_rejects_incompatible_persisted_outcomes(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    base = state(
        status="failed",
        completed_step_ids=("first",),
        last_failure_category="api_error",
    )
    value = state(**{**base.__dict__, **changes})
    terminal = event(
        event_type="step_failed",
        next_status="failed",
        failure_category="api_error",
        response_id=None,
        output_text=None,
        message="safe",
        step_id=value.current_step_id,
        step_index=value.current_step_index,
        employee_id=value.current_employee_id,
    )
    targets = write_history(tmp_path, value, first_event(), terminal)
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())
    with pytest.raises(
        (PersistedExecutionOutcomeCompatibilityError, WorkflowExecutionLoadError)
    ):
        classify_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path
        )
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


def test_preserves_safe_loader_error_and_restores_loader_mutation(
    tmp_path: Path,
) -> None:
    targets = write_history(tmp_path, state(), first_event(), event())
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())
    expected = WorkflowExecutionLoadError("safe")

    def loader(_: object) -> object:
        targets.events_path.write_text("changed")
        raise expected

    with pytest.raises(WorkflowExecutionLoadError) as error:
        classify_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path, history_loader=loader
        )  # type: ignore[arg-type]
    assert error.value is expected
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


def test_rejects_and_restores_loader_mutation(tmp_path: Path) -> None:
    targets = write_history(tmp_path, state(), first_event(), event())
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())

    def loader(_: object) -> object:
        targets.state_path.write_text("changed")
        from ai_office.storage import load_workflow_execution_history

        return load_workflow_execution_history(targets)

    with pytest.raises(WorkflowExecutionLoadError) as error:
        classify_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path, history_loader=loader
        )  # type: ignore[arg-type]
    assert error.value.detail.operation == "state_parse"
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


def test_unexpected_loader_failure_is_safe_and_does_not_write(tmp_path: Path) -> None:
    targets = write_history(tmp_path, state(), first_event(), event())
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())

    def loader(_: object) -> object:
        raise RuntimeError("secret target details")

    with pytest.raises(PersistedExecutionOutcomeCompatibilityError) as error:
        classify_persisted_execution_outcome_reentry(
            workflow(), targets.state_path, targets.events_path, history_loader=loader
        )  # type: ignore[arg-type]
    assert error.value.detail.classification == "history_data"
    assert "secret target details" not in str(error.value)
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before
