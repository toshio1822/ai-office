"""Focused Phase 59 boundary tests using injected Phase 52 fakes only."""

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError,
    ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_routing_phase_bridge_reentry,
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
    "two,index",
    [(True, 1), (False, 1)],
)
def test_persisted_success_delegates_once_with_identity_and_returns_decision(
    tmp_path: Path, two: bool, index: int
) -> None:
    state, events, outcome, definition = setup(tmp_path, two=two, index=index)
    decision = WorkflowProgressionDecision(
        "prepare_next_step" if two else "workflow_complete",
        "w",
        outcome.current_step_id,
        index,
        outcome.current_employee_id,
        "two" if two else None,
        2 if two else None,
        "b" if two else None,
        "next_step_available" if two else "last_step_succeeded",
    )  # type: ignore[arg-type]
    calls = 0

    def phase52(*args: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        assert args == (outcome, definition, state, events)
        assert args[0] is outcome and args[1] is definition
        assert args[2] is state and args[3] is events
        return decision

    assert (
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=phase52
        )
        is decision
    )
    assert calls == 1


def test_persisted_failure_delegates_once_and_returns_same_object(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup(tmp_path, status="failed")
    calls = 0

    def phase52(*args: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        assert args[0] is outcome and args[1] is definition
        assert args[2] is state and args[3] is events
        return outcome

    assert (
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=phase52
        )
        is outcome
    )
    assert calls == 1


def test_workflow_complete_is_an_unchanged_zero_call_stop(tmp_path: Path) -> None:
    state, events, _, definition = setup(tmp_path)
    decision = WorkflowProgressionDecision(
        "workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded"
    )
    before = state.read_bytes(), events.read_bytes()
    assert (
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            decision,
            definition,
            state,
            events,
            phase52_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
        is decision
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "result",
    [
        object(),
        OutcomeSubclass("persisted_success", "w", "one", 1, "a", None),
        DecisionSubclass(
            "workflow_complete",
            "w",
            "one",
            1,
            "a",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
    ],
)
def test_exact_result_types_are_required_without_dependency_call(
    tmp_path: Path, result: object
) -> None:
    state, events, _, definition = setup(tmp_path)
    calls = 0

    def phase52(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            result, definition, state, events, phase52_function=phase52
        )
    assert calls == 0


@pytest.mark.parametrize("field", ["outcome", "failure_category", "current_step_index"])
def test_persisted_outcome_discriminator_and_fields_are_strict(
    tmp_path: Path, field: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    values = dict(outcome.__dict__)
    values[field] = {
        "outcome": "persisted_failure",
        "failure_category": None,
        "current_step_index": True,
    }[field]
    malformed = PersistedExecutionOutcome(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            malformed,
            definition,
            state,
            events,
            phase52_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )


@pytest.mark.parametrize(
    "field", ["decision", "workflow_id", "current_step_index", "reason"]
)
def test_completion_fields_are_strict(tmp_path: Path, field: str) -> None:
    state, events, _, definition = setup(tmp_path)
    values = dict(
        decision="workflow_complete",
        workflow_id="w",
        current_step_id="one",
        current_step_index=1,
        current_employee_id="a",
        next_step_id=None,
        next_step_index=None,
        next_employee_id=None,
        reason="last_step_succeeded",
    )
    values[field] = {"decision": "prepare_next_step", "workflow_id": "bad"}.get(
        field, True
    )
    malformed = WorkflowProgressionDecision(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            malformed,
            definition,
            state,
            events,
            phase52_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )


@pytest.mark.parametrize(
    "field",
    [
        "decision",
        "workflow_id",
        "next_step_id",
        "next_step_index",
        "next_employee_id",
        "reason",
    ],
)
def test_success_return_fields_are_strict(tmp_path: Path, field: str) -> None:
    state, events, outcome, definition = setup(tmp_path, two=True)
    values = dict(
        decision="prepare_next_step",
        workflow_id="w",
        current_step_id="one",
        current_step_index=1,
        current_employee_id="a",
        next_step_id="two",
        next_step_index=2,
        next_employee_id="b",
        reason="next_step_available",
    )
    values[field] = {"decision": "workflow_complete", "workflow_id": "bad"}.get(
        field, True
    )
    returned = WorkflowProgressionDecision(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=lambda *_: returned
        )


def test_failure_rejects_equivalent_but_distinct_return_without_retry(
    tmp_path: Path,
) -> None:
    state, events, outcome, definition = setup(tmp_path, status="failed")
    equivalent = PersistedExecutionOutcome(*outcome.__dict__.values())
    calls = 0

    def phase52(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        return equivalent

    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome, definition, state, events, phase52_function=phase52
        )
    assert calls == 1


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["normal", "safe", "unexpected", "malformed"])
def test_dependency_mutations_errors_and_malformed_returns_are_compensated(
    tmp_path: Path, operation: str, target: str, kind: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError("routing_contract")

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"x")
        else:
            path.write_bytes(b"changed")

    def phase52(*_: object) -> object:
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("sensitive provider output")
        if kind == "malformed":
            return object()
        return WorkflowProgressionDecision(
            "prepare_next_step",
            "w",
            "one",
            1,
            "a",
            "two",
            2,
            "b",
            "next_step_available",
        )

    expected = (
        ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
        if kind == "safe"
        else ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            outcome,
            definition,
            state,
            events,
            phase52_function=phase52,
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert "sensitive" not in str(caught.value)
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("bad", ["workflow", "state", "events", "same", "function"])
def test_workflow_targets_and_dependency_are_prevalidated(
    tmp_path: Path, bad: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    args: list[object] = [outcome, definition, state, events]
    if bad == "workflow":
        args[1] = WorkflowSubclass(**definition.__dict__)
    elif bad == "state":
        args[2] = "bad"
    elif bad == "events":
        args[3] = "bad"
    elif bad == "same":
        args[3] = state
    function: object = object() if bad == "function" else lambda *_: outcome
    with pytest.raises(ClassifiedPersistedOutcomeRoutingPhaseBridgeCompatibilityError):
        route_classified_persisted_outcome_routing_phase_bridge_reentry(
            *args, phase52_function=function  # type: ignore[arg-type]
        )
