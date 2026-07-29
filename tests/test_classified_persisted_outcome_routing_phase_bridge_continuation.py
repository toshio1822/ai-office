"""Focused Phase 66 boundary tests using injected Phase 59 fakes only."""

# ruff: noqa: E501

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError,
    ClassifiedPersistedOutcomeRoutingPhaseBridgeContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_routing_phase_bridge_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


def workflow(two: bool = False) -> WorkflowDefinition:
    steps = [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]
    if two:
        steps.append({"id": "two", "name": "Two", "employee": "b", "instructions": "y"})
    return WorkflowDefinition.model_validate(
        {"id": "w", "name": "W", "description": "D", "steps": steps}
    )


def setup(
    tmp_path: Path, *, status: str = "succeeded", two: bool = False, index: int = 1
) -> tuple[Path, Path, PersistedExecutionOutcome, WorkflowDefinition, bytes, bytes]:
    definition = workflow(two)
    step = definition.steps[index - 1]
    state_model = WorkflowExecutionState(
        "w", status, step.id, index, step.employee,
        tuple(item.id for item in definition.steps[:index]) if status == "succeeded"
        else tuple(item.id for item in definition.steps[: index - 1]),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w", step.id, index, step.employee, "running", status, "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None, "request",
        "out" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes)
    events.write_bytes(event_bytes)
    outcome = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w", step.id, index, step.employee,
        None if status == "succeeded" else "api_error",
    )
    return state, events, outcome, definition, state_bytes, event_bytes


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded"
    )


def test_nonfinal_success_delegates_once_and_returns_exact_decision(tmp_path: Path) -> None:
    state, events, outcome, definition, before_state, before_events = setup(
        tmp_path, two=True
    )
    decision = WorkflowProgressionDecision(
        "prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available"
    )
    calls: list[tuple[object, ...]] = []

    def phase59(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        assert args == (outcome, definition, state, events)
        return decision

    returned = route_classified_persisted_outcome_routing_phase_bridge_continuation(
        outcome, definition, state, events, phase59_function=phase59
    )
    assert returned is decision and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_final_success_delegates_and_returns_workflow_complete(tmp_path: Path) -> None:
    state, events, outcome, definition, before_state, before_events = setup(tmp_path)
    decision = completion()
    calls = 0

    def phase59(*args: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        assert args[0] is outcome and args[1] is definition
        assert args[2] is state and args[3] is events
        return decision

    assert route_classified_persisted_outcome_routing_phase_bridge_continuation(
        outcome, definition, state, events, phase59_function=phase59
    ) is decision
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_failure_delegates_once_and_accepts_only_same_object(tmp_path: Path) -> None:
    state, events, outcome, definition, before_state, before_events = setup(
        tmp_path, status="failed"
    )
    calls = 0

    def phase59(*args: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        assert args[0] is outcome and args[1] is definition
        return outcome

    assert route_classified_persisted_outcome_routing_phase_bridge_continuation(
        outcome, definition, state, events, phase59_function=phase59
    ) is outcome
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_workflow_complete_is_zero_call_unchanged_stop(tmp_path: Path) -> None:
    state, events, _, definition, before_state, before_events = setup(tmp_path)
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    supplied = completion()
    assert route_classified_persisted_outcome_routing_phase_bridge_continuation(
        supplied, definition, state, events, phase59_function=unexpected
    ) is supplied
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "value",
    [
        object(),
        OutcomeSubclass("persisted_success", "w", "one", 1, "a", None),
        DecisionSubclass("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded"),
        SimpleNamespace(outcome="persisted_success"),
    ],
)
def test_exact_result_types_are_required_without_dependency_call(tmp_path: Path, value: object) -> None:
    state, events, _, definition, before_state, before_events = setup(tmp_path)
    calls = 0

    def phase59(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeContinuationCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_continuation(
            value, definition, state, events, phase59_function=phase59
        )
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("value", [WorkflowSubclass.model_validate(workflow().model_dump()), SimpleNamespace(id="w")])
def test_exact_workflow_type_is_required(tmp_path: Path, value: object) -> None:
    state, events, outcome, _, before_state, before_events = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeContinuationCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_continuation(
            outcome, value, state, events
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("field", ["outcome", "workflow_id", "current_step_id", "current_step_index", "current_employee_id", "failure_category"])
def test_malformed_failure_is_rejected_before_dependency(tmp_path: Path, field: str) -> None:
    state, events, outcome, definition, before_state, before_events = setup(tmp_path, status="failed")
    values = {
        "outcome": "persisted_success", "workflow_id": "bad", "current_step_id": "bad",
        "current_step_index": 99, "current_employee_id": "bad", "failure_category": "bad",
    }
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeContinuationCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_continuation(
            replace(outcome, **{field: values[field]}), definition, state, events,
            phase59_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("field", ["decision", "workflow_id", "current_step_id", "current_step_index", "current_employee_id", "reason"])
def test_malformed_completion_is_rejected_before_dependency(tmp_path: Path, field: str) -> None:
    state, events, _, definition, before_state, before_events = setup(tmp_path)
    values = {
        "decision": "prepare_next_step", "workflow_id": "bad", "current_step_id": "bad",
        "current_step_index": 99, "current_employee_id": "bad", "reason": "bad",
    }
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeContinuationCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_continuation(
            replace(completion(), **{field: values[field]}), definition, state, events,
            phase59_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_reconstructed_failure_return_is_rejected_without_retry(tmp_path: Path) -> None:
    state, events, outcome, definition, before_state, before_events = setup(tmp_path, status="failed")
    calls = 0
    reconstructed = replace(outcome)

    def phase59(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        return reconstructed

    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_continuation(
            outcome, definition, state, events, phase59_function=phase59
        )
    assert caught.value.detail.classification == "routing_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("which", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_target_oserrors_are_classified_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, operation: str
) -> None:
    state, events, outcome, definition, before_state, before_events = setup(tmp_path)
    target = state if which == "state" else events
    original = getattr(Path, operation)

    def fail(path: Path) -> object:
        if path == target:
            raise OSError("secret path")
        return original(path)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_continuation(
            outcome, definition, state, events
        )
    assert caught.value.detail.classification == ("state_target" if which == "state" else "event_target")
    monkeypatch.undo()
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_dependency_errors_and_mutations_are_compensated_without_retry(
    tmp_path: Path, kind: str, mutation: str
) -> None:
    state, events, outcome, definition, before_state, before_events = setup(tmp_path)
    calls = 0
    safe = ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError("terminal_contract")

    def phase59(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"changed state")
        if mutation in ("events", "both"):
            events.write_bytes(b"changed events")
        if kind == "safe":
            raise safe
        raise RuntimeError("secret provider response")

    with pytest.raises(
        (ClassifiedPersistedOutcomeRoutingPhaseBridgeContinuationCompatibilityError,
         ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError)
    ) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_continuation(
            outcome, definition, state, events, phase59_function=phase59
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
        assert "secret" not in str(caught.value)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
