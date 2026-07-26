"""Tests for the Phase 38 read-only routing boundary."""

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeRoutingCompatibilityError,
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "test",
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


def outcome(**changes: object) -> PersistedExecutionOutcome:
    values: dict[str, object] = {
        "outcome": "persisted_success",
        "workflow_id": "workflow",
        "current_step_id": "first",
        "current_step_index": 1,
        "current_employee_id": "one",
        "failure_category": None,
    }
    values.update(changes)
    return PersistedExecutionOutcome(**values)  # type: ignore[arg-type]


def targets(tmp_path: Path) -> tuple[Path, Path]:
    state, events = tmp_path / "state", tmp_path / "events"
    state.write_bytes(b"state")
    events.write_bytes(b"events")
    return state, events


def decision(**changes: object) -> WorkflowProgressionDecision:
    values: dict[str, object] = {
        "decision": "prepare_next_step",
        "workflow_id": "workflow",
        "current_step_id": "first",
        "current_step_index": 1,
        "current_employee_id": "one",
        "next_step_id": "second",
        "next_step_index": 2,
        "next_employee_id": "two",
        "reason": "next_step_available",
    }
    values.update(changes)
    return WorkflowProgressionDecision(**values)  # type: ignore[arg-type]


def test_success_routes_once_and_returns_same_decision(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    supplied, expected, calls = outcome(), decision(), [0, 0]

    def classify(*_: object) -> PersistedExecutionOutcome:
        calls[0] += 1
        return supplied

    def progress(*_: object) -> WorkflowProgressionDecision:
        calls[1] += 1
        return expected

    assert (
        route_persisted_execution_outcome_reentry(
            supplied,
            workflow(),
            state,
            events,
            classification_function=classify,
            progression_function=progress,
        )
        is expected
    )
    assert calls == [1, 1]


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
def test_failure_returns_same_outcome_without_progression(
    tmp_path: Path, category: str
) -> None:
    state, events = targets(tmp_path)
    supplied, calls = outcome(outcome="persisted_failure", failure_category=category), 0

    def classify(*_: object) -> PersistedExecutionOutcome:
        return supplied

    def progress(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_persisted_execution_outcome_reentry(
            supplied,
            workflow(),
            state,
            events,
            classification_function=classify,
            progression_function=progress,
        )
        is supplied
    )
    assert calls == 0


def test_rejects_mismatched_reclassification_before_progression(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def progress(*_: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            outcome(),
            workflow(),
            state,
            events,
            classification_function=lambda *_: outcome(current_step_id="other"),
            progression_function=progress,
        )
    assert error.value.detail.classification == "classification_contract"
    assert calls == 0


def test_restores_changed_target_and_rejects(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())

    def classify(*_: object) -> PersistedExecutionOutcome:
        events.write_bytes(b"changed")
        return outcome()

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as error:
        route_persisted_execution_outcome_reentry(
            outcome(), workflow(), state, events, classification_function=classify
        )
    assert error.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == before
