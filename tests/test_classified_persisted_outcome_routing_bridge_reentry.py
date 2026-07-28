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
from ai_office.engine.classified_persisted_outcome_routing_reentry import (
    ClassifiedPersistedOutcomeRoutingCompatibilityError,
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


@pytest.mark.parametrize(
    "result",
    [
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
        object(),
    ],
)
def test_exact_types_and_substitutes_reject_without_calls(
    tmp_path: Path, result: object
) -> None:
    state, events, _, definition = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            result, definition, state, events, routing_function=dependency
        )
    assert calls == 0


@pytest.mark.parametrize("bad", ["workflow", "state", "events", "same", "function"])
def test_workflow_target_and_dependency_prevalidation(tmp_path: Path, bad: str) -> None:
    state, events, outcome, definition = setup(tmp_path)
    args: list[object] = [outcome, definition, state, events]
    if bad == "workflow":
        args[1] = WorkflowSubclass(**definition.__dict__)
    if bad == "state":
        args[2] = "bad"
    if bad == "events":
        args[3] = "bad"
    if bad == "same":
        args[3] = state
    function: object = object() if bad == "function" else lambda *_: outcome
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            *args, routing_function=function
        )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "outcome",
    [
        PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", "api_error"),
        PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", None),
        PersistedExecutionOutcome("persisted_failure", "w", "bad", 1, "a", "api_error"),
    ],
)
def test_malformed_success_and_failure_reject_before_dependency(
    tmp_path: Path, outcome: PersistedExecutionOutcome
) -> None:
    state, events, _, definition = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome,
            definition,
            state,
            events,
            routing_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["normal", "safe", "unexpected"])
def test_dependency_mutation_error_matrix(
    tmp_path: Path, operation: str, target: str, kind: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0
    safe = ClassifiedPersistedOutcomeRoutingCompatibilityError("result_type")

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"x")
        else:
            path.write_bytes(b"changed")

    def dependency(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("provider response output")
        return outcome

    expected = (
        ClassifiedPersistedOutcomeRoutingCompatibilityError
        if kind == "safe"
        else ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=dependency
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification in {
            "routing_contract",
            "dependency_error",
        }
        assert "provider" not in str(caught.value) and "output" not in str(caught.value)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["normal", "safe", "unexpected"])
@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failure_overrides_dependency_and_attempts_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, failed: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    originals, original_write, attempted, calls = (
        {state: state.read_bytes(), events: events.read_bytes()},
        Path.write_bytes,
        [],
        0,
    )
    safe = ClassifiedPersistedOutcomeRoutingCompatibilityError("result_type")

    def write(path: Path, contents: bytes) -> int:
        if contents == originals.get(path):
            attempted.append(path)
            if (
                failed == "both"
                or (failed == "state" and path == state)
                or (failed == "events" and path == events)
            ):
                raise OSError("provider output")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", write)

    def dependency(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        original_write(state, b"x")
        original_write(events, b"x")
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("provider output")
        return outcome

    with pytest.raises(
        ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=dependency
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1 and state in attempted and events in attempted
    assert "provider" not in str(caught.value) and "output" not in str(caught.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"decision": "workflow_complete"},
        {"workflow_id": "bad"},
        {"current_step_id": "bad"},
        {"current_step_index": 2},
        {"current_employee_id": "bad"},
        {"next_step_id": "bad"},
        {"next_step_index": 3},
        {"next_employee_id": "bad"},
        {"reason": "bad"},
    ],
)
def test_prepare_next_step_return_rejects_every_wrong_field(
    tmp_path: Path, changes: dict[str, object]
) -> None:
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
    values.update(changes)
    returned = WorkflowProgressionDecision(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=lambda *_: returned
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"decision": "prepare_next_step"},
        {"workflow_id": "bad"},
        {"current_step_id": "bad"},
        {"current_step_index": 2},
        {"current_employee_id": "bad"},
        {"next_step_id": "two"},
        {"next_step_index": 2},
        {"next_employee_id": "b"},
        {"reason": "bad"},
    ],
)
def test_workflow_complete_return_rejects_every_wrong_field(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events, outcome, definition = setup(tmp_path)
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
    values.update(changes)
    returned = WorkflowProgressionDecision(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=lambda *_: returned
        )


def test_failure_rejects_equivalent_but_distinct_return_object(tmp_path: Path) -> None:
    state, events, outcome, definition = setup(tmp_path, status="failed")
    equivalent = PersistedExecutionOutcome(*outcome.__dict__.values())
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=lambda *_: equivalent
        )


@pytest.mark.parametrize("missing", ["state", "events"])
def test_missing_target_has_zero_calls_writes_and_unchanged_other(
    tmp_path: Path, missing: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    other = events if missing == "state" else state
    before, calls = other.read_bytes(), 0
    (state if missing == "state" else events).unlink()

    def dependency(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=dependency
        )
    assert calls == 0 and other.read_bytes() == before


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
def test_unchanged_dependency_errors_preserve_identity_or_sanitize_without_writes(
    tmp_path: Path, kind: str
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0
    safe = ClassifiedPersistedOutcomeRoutingCompatibilityError("result_type")

    def dependency(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        if kind == "safe":
            raise safe
        raise RuntimeError("provider request response output")

    expected = (
        ClassifiedPersistedOutcomeRoutingCompatibilityError
        if kind == "safe"
        else ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_classified_persisted_outcome_bridge_reentry(
            outcome, definition, state, events, routing_function=dependency
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert (
            caught.value.detail.classification == "dependency_error"
            and "provider" not in str(caught.value)
        )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_step_index", 0),
        ("current_step_index", True),
        ("current_step_id", "bad"),
        ("current_employee_id", "bad"),
    ],
)
def test_invalid_outcome_identity_rejects_before_dependency(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, outcome, definition = setup(tmp_path)
    values = dict(outcome.__dict__)
    values[field] = value
    bad = PersistedExecutionOutcome(**values)  # type: ignore[arg-type]
    with pytest.raises(ClassifiedPersistedOutcomeRoutingBridgeCompatibilityError):
        route_classified_persisted_outcome_bridge_reentry(
            bad,
            definition,
            state,
            events,
            routing_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
