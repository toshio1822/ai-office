"""Focused Phase 52 bridge tests using injected Phase 45 fakes only."""

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_bridge_reentry,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow(two: bool = False) -> WorkflowDefinition:
    steps = [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]
    if two:
        steps.append({"id": "two", "name": "Two", "employee": "b", "instructions": "y"})
    return WorkflowDefinition.model_validate(
        {"id": "w", "name": "W", "description": "D", "steps": steps}
    )


def setup(
    tmp_path: Path, *, two: bool = False, status: str = "succeeded", index: int = 1
) -> tuple[Path, Path, PersistedExecutionOutcome, WorkflowDefinition]:
    definition = workflow(two)
    step = definition.steps[index - 1]
    completed = (
        tuple(item.id for item in definition.steps[:index])
        if status == "succeeded"
        else tuple(item.id for item in definition.steps[: index - 1])
    )
    state = WorkflowExecutionState(
        "w",
        status,
        step.id,
        index,
        step.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w",
        step.id,
        index,
        step.employee,
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
    outcome = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        step.id,
        index,
        step.employee,
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    return state_path, events_path, outcome, definition


@pytest.mark.parametrize(
    "two,index,decision",
    [(False, 1, "workflow_complete"), (True, 1, "prepare_next_step")],
)
def test_success_delegates_exact_arguments_and_returns_same_decision(
    tmp_path: Path, two: bool, index: int, decision: str
) -> None:
    state, events, outcome, definition = setup(tmp_path, two=two, index=index)
    expected = WorkflowProgressionDecision(
        decision,
        "w",
        outcome.current_step_id,
        index,
        outcome.current_employee_id,
        None if decision == "workflow_complete" else "two",
        None if decision == "workflow_complete" else 2,
        None if decision == "workflow_complete" else "b",
        "last_step_succeeded"
        if decision == "workflow_complete"
        else "next_step_available",
    )  # type: ignore[arg-type]
    calls = 0

    def phase45(*args: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        assert (
            len(args) == 4
            and args[0] is outcome
            and args[1] is definition
            and args[2] is state
            and args[3] is events
        )
        return expected

    assert (
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=phase45
        )
        is expected
    )
    assert calls == 1


def test_failure_delegates_once_and_returns_same_supplied_object(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup(tmp_path, status="failed")
    calls = 0

    def phase45(*args: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        assert (
            args[0] is outcome
            and args[1] is definition
            and args[2] is state
            and args[3] is events
        )
        return outcome

    assert (
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=phase45
        )
        is outcome
    )
    assert calls == 1


def test_completion_stops_unchanged(tmp_path: Path) -> None:
    state, events, outcome, definition = setup(tmp_path)
    decision = WorkflowProgressionDecision(
        "workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded"
    )
    before = state.read_bytes(), events.read_bytes()
    assert (
        route_classified_persisted_outcome_bridge_reentry(
            decision,
            definition,
            state,
            events,
            routing_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
        is decision
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "result",
    [
        object(),
        PersistedExecutionOutcome("persisted_success", "bad", "one", 1, "a", None),
    ],
)
def test_prevalidation_rejects_without_dependency(
    tmp_path: Path, result: object
) -> None:
    state, events, _, definition = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            result,
            definition,
            state,
            events,
            routing_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )


@pytest.mark.parametrize("operation", ["replace", "delete", "append"])
def test_mutating_or_malformed_dependency_is_compensated(
    tmp_path: Path, operation: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def phase45(*_: object) -> PersistedExecutionOutcome:
        if operation == "delete":
            events.unlink()
        elif operation == "append":
            events.write_bytes(events.read_bytes() + b"x")
        else:
            events.write_bytes(b"changed")
        return outcome

    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=phase45
        )
    assert (state.read_bytes(), events.read_bytes()) == before
