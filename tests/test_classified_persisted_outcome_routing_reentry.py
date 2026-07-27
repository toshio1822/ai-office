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
from ai_office.engine.persisted_execution_outcome_routing_reentry import (
    PersistedExecutionOutcomeRoutingCompatibilityError,
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


def test_final_success_routes_once_to_same_workflow_complete(tmp_path: Path) -> None:
    state, events, outcome = setup(tmp_path, final=True)
    prior = RuntimeStepEvent(
        "step_succeeded",
        "workflow",
        "first",
        1,
        "one",
        "running",
        "succeeded",
        "openai",
        None,
        "response",
        "request",
        "out",
        None,
    )
    events.write_bytes(
        serialize_runtime_step_event_jsonl(prior).encode() + events.read_bytes()
    )
    expected = WorkflowProgressionDecision(
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

    def route(*_: object):
        nonlocal calls
        calls += 1
        return expected

    assert (
        route_classified_persisted_outcome_reentry(
            outcome, workflow(), state, events, routing_function=route
        )
        is expected
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


@pytest.mark.parametrize(
    "mode", ["invalid-state", "invalid-events", "terminal-mismatch"]
)
def test_invalid_terminal_history_is_phase45_terminal_contract(
    tmp_path: Path, mode: str
) -> None:
    state, events, outcome = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    if mode == "invalid-state":
        state.write_bytes(b"bad")
    elif mode == "invalid-events":
        events.write_bytes(b"bad\n")
    else:
        events.write_bytes(
            serialize_runtime_step_event_jsonl(
                RuntimeStepEvent(
                    "step_failed",
                    "workflow",
                    "first",
                    1,
                    "one",
                    "running",
                    "failed",
                    "openai",
                    "api_error",
                    None,
                    "request",
                    None,
                    "safe",
                )
            ).encode()
        )
    calls = 0

    def unexpected(*_: object):
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ClassifiedPersistedOutcomeRoutingCompatibilityError) as caught:
        route_classified_persisted_outcome_reentry(
            outcome, workflow(), state, events, routing_function=unexpected
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) != before


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": "unknown"},
        {"workflow_id": "other"},
        {"current_step_id": "other"},
        {"current_step_index": 0},
        {"failure_category": "api_error"},
    ],
)
def test_outcome_contract_rejects_before_phase38(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events, outcome = setup(tmp_path)
    values = outcome.__dict__.copy()
    values.update(changes)
    supplied = PersistedExecutionOutcome(**values)  # type: ignore[arg-type]
    calls = 0

    def unexpected(*_: object):
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ClassifiedPersistedOutcomeRoutingCompatibilityError) as caught:
        route_classified_persisted_outcome_reentry(
            supplied, workflow(), state, events, routing_function=unexpected
        )
    assert caught.value.detail.classification == "outcome_contract" and calls == 0


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


@pytest.mark.parametrize(
    (
        "result",
        "definition",
        "state_value",
        "events_value",
        "function",
        "classification",
    ),
    [
        (
            OutcomeSubclass("persisted_success", "workflow", "first", 1, "one", None),
            None,
            None,
            None,
            None,
            "result_type",
        ),
        (
            DecisionSubclass(
                "workflow_complete",
                "workflow",
                "step",
                2,
                "employee",
                None,
                None,
                None,
                "last_step_succeeded",
            ),
            None,
            None,
            None,
            None,
            "result_type",
        ),
        (
            type("Compatible", (), {"outcome": "persisted_success"})(),
            None,
            None,
            None,
            None,
            "result_type",
        ),
        (
            None,
            WorkflowSubclass(**workflow().__dict__),
            None,
            None,
            None,
            "workflow_definition",
        ),
        (None, None, "wrong", None, None, "state_target"),
        (None, None, None, "wrong", None, "event_target"),
        (None, None, "same", "same", None, "target_conflict"),
        (None, None, None, None, object(), "routing_contract"),
    ],
)
def test_exact_top_level_prevalidation(
    tmp_path: Path,
    result: object,
    definition: object,
    state_value: object,
    events_value: object,
    function: object,
    classification: str,
) -> None:
    state, events, outcome = setup(tmp_path)
    before, calls, writes = (state.read_bytes(), events.read_bytes()), 0, []
    original = Path.write_bytes

    def record(path: Path, data: bytes) -> int:
        writes.append(path)
        return original(path, data)

    def unexpected(*_: object):
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(Path, "write_bytes", record)
        actual_state, actual_events = (
            (state if state_value is None else state_value),
            (events if events_value is None else events_value),
        )
        if state_value == "same":
            actual_state = actual_events = state
        with pytest.raises(
            ClassifiedPersistedOutcomeRoutingCompatibilityError
        ) as caught:
            route_classified_persisted_outcome_reentry(
                outcome if result is None else result,
                workflow() if definition is None else definition,
                actual_state,
                actual_events,
                routing_function=unexpected if function is None else function,
            )  # type: ignore[arg-type]
    assert caught.value.detail.classification == classification
    assert (
        calls == 0
        and writes == []
        and (state.read_bytes(), events.read_bytes()) == before
    )


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both", "delete-and-other"])
def test_phase38_mutations_restore_targets_once(
    tmp_path: Path, operation: str, target: str
) -> None:
    state, events, outcome = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def mutate(path: Path) -> None:
        if operation == "replace":
            path.write_bytes(b"replace")
        elif operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        else:
            path.write_bytes(path.read_bytes() + b"append")

    def route(*_: object):
        nonlocal calls
        calls += 1
        if target == "state":
            mutate(state)
        elif target == "events":
            mutate(events)
        elif target == "both":
            mutate(state)
            mutate(events)
        else:
            state.unlink()
            mutate(events)
        return WorkflowProgressionDecision(
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

    with pytest.raises(ClassifiedPersistedOutcomeRoutingCompatibilityError) as caught:
        route_classified_persisted_outcome_reentry(
            outcome, workflow(), state, events, routing_function=route
        )
    assert caught.value.detail.classification == "dependency_error"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_safe_and_unexpected_routing_errors_are_preserved_or_sanitized(
    tmp_path: Path,
) -> None:
    state, events, outcome = setup(tmp_path)
    expected = PersistedExecutionOutcomeRoutingCompatibilityError("outcome_type")
    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError) as caught:
        route_classified_persisted_outcome_reentry(
            outcome,
            workflow(),
            state,
            events,
            routing_function=lambda *_: (_ for _ in ()).throw(expected),
        )
    assert caught.value is expected
    with pytest.raises(ClassifiedPersistedOutcomeRoutingCompatibilityError) as caught:
        route_classified_persisted_outcome_reentry(
            outcome,
            workflow(),
            state,
            events,
            routing_function=lambda *_: (_ for _ in ()).throw(
                RuntimeError("provider response output failure /secret")
            ),
        )
    assert caught.value.detail.classification == "dependency_error"
    assert (
        "provider" not in str(caught.value)
        and "output" not in str(caught.value)
        and "secret" not in str(caught.value)
    )


@pytest.mark.parametrize(
    ("missing", "classification"),
    [("state", "state_target"), ("events", "event_target")],
)
def test_missing_target_rejects_before_phase38(
    tmp_path: Path, missing: str, classification: str
) -> None:
    state, events, outcome = setup(tmp_path)
    (state if missing == "state" else events).unlink()
    calls = 0

    def unexpected(*_: object):
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ClassifiedPersistedOutcomeRoutingCompatibilityError) as caught:
        route_classified_persisted_outcome_reentry(
            outcome, workflow(), state, events, routing_function=unexpected
        )
    assert caught.value.detail.classification == classification and calls == 0
