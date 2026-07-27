"""Tests for Phase 45 using injected Phase 38 routing fakes only."""

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeRoutingCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_reentry,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "first", "name": "F", "employee": "one", "instructions": "a"},
                {
                    "id": "step",
                    "name": "S",
                    "employee": "employee",
                    "instructions": "b",
                },
            ],
        }
    )


def setup(
    tmp_path: Path, status: str = "succeeded", final: bool = False
) -> tuple[Path, Path, PersistedExecutionOutcome]:
    index, step, employee = (2, "step", "employee") if final else (1, "first", "one")
    completed = (
        ("first", "step")
        if final and status == "succeeded"
        else (("first",) if status == "succeeded" else ())
    )
    state = WorkflowExecutionState(
        "workflow",
        status,
        step,
        index,
        employee,
        completed,
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "workflow",
        step,
        index,
        employee,
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "out" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(serialize_runtime_step_event_jsonl(event).encode())
    return (
        state_path,
        events_path,
        PersistedExecutionOutcome(
            "persisted_success" if status == "succeeded" else "persisted_failure",
            "workflow",
            step,
            index,
            employee,
            None if status == "succeeded" else "api_error",
        ),
    )  # type: ignore[arg-type]


def test_success_routes_once_to_same_prepare_next_step(tmp_path: Path) -> None:
    state, events, outcome = setup(tmp_path)
    expected = WorkflowProgressionDecision(
        "prepare_next_step",
        "workflow",
        "first",
        1,
        "one",
        "step",
        2,
        "employee",
        "next_step_available",
    )
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def route(*args: object):
        nonlocal calls
        calls += 1
        assert args == (outcome, workflow(), state, events)
        return expected

    assert (
        route_classified_persisted_outcome_reentry(
            outcome, workflow(), state, events, routing_function=route
        )
        is expected
    )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_failure_returns_same_exact_outcome(tmp_path: Path) -> None:
    state, events, outcome = setup(tmp_path, "failed")
    calls = 0

    def route(*_: object):
        nonlocal calls
        calls += 1
        return outcome

    assert (
        route_classified_persisted_outcome_reentry(
            outcome, workflow(), state, events, routing_function=route
        )
        is outcome
    )
    assert calls == 1


def test_completion_stops_without_phase38(tmp_path: Path) -> None:
    state, events, _ = setup(tmp_path, final=True)
    decision = WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "step",
        2,
        "employee",
        None,
        None,
        None,
        "last_step_succeeded",
    )
    calls = 0

    def unexpected(*_: object):
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_classified_persisted_outcome_reentry(
            decision, workflow(), state, events, routing_function=unexpected
        )
        is decision
    )
    assert calls == 0


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        PersistedExecutionOutcome(
            "persisted_success", "workflow", "first", 1, "one", None
        ),
    ],
)
def test_invalid_failure_return_is_rejected(tmp_path: Path, returned: object) -> None:
    state, events, outcome = setup(tmp_path, "failed")
    with pytest.raises(ClassifiedPersistedOutcomeRoutingCompatibilityError) as caught:
        route_classified_persisted_outcome_reentry(
            outcome, workflow(), state, events, routing_function=lambda *_: returned
        )
    assert caught.value.detail.classification == "routing_contract"
