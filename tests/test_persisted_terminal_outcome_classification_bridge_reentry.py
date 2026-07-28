"""Focused Phase 51 bridge tests using injected Phase 44 fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedTerminalOutcomeClassificationBridgeCompatibilityError,
    WorkflowProgressionDecision,
    route_persisted_terminal_outcome_classification_bridge_reentry,
)
from ai_office.engine.persisted_terminal_outcome_classification_routing_reentry import (
    PersistedTerminalOutcomeClassificationRoutingCompatibilityError,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
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
                {
                    "id": "step",
                    "name": "S",
                    "employee": "employee",
                    "instructions": "do",
                }
            ],
        }
    )


def setup(
    tmp_path: Path, status: str = "succeeded"
) -> tuple[Path, Path, WorkflowExecutionPersistenceResult]:
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state = WorkflowExecutionState(
        "workflow",
        status,
        "step",
        1,
        "employee",
        ("step",) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "workflow",
        "step",
        1,
        "employee",
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "out" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]
    state_bytes = serialize_workflow_execution_state_json(state).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return (
        state_path,
        events_path,
        WorkflowExecutionPersistenceResult(
            state_path, events_path, len(state_bytes), len(event_bytes)
        ),
    )


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_persistence_result_routes_once_with_identity_and_unchanged_targets(
    tmp_path: Path, status: str
) -> None:
    state, events, persisted = setup(tmp_path, status)
    expected = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "workflow",
        "step",
        1,
        "employee",
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def phase44(*args: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        assert args == (persisted, workflow(), state, events)
        return expected

    assert (
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=phase44,
        )
        is expected
    )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_terminal_stop_routes_return_same_object_without_phase44(
    tmp_path: Path,
) -> None:
    state, events, _ = setup(tmp_path)
    decision = WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "step",
        1,
        "employee",
        None,
        None,
        None,
        "last_step_succeeded",
    )
    calls = 0

    def unexpected(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_persisted_terminal_outcome_classification_bridge_reentry(
            decision,
            workflow(),
            state,
            events,
            classification_routing_function=unexpected,
        )
        is decision
    )
    assert calls == 0


def test_persisted_failure_stops_without_phase44(tmp_path: Path) -> None:
    state, events, _ = setup(tmp_path, "failed")
    outcome = PersistedExecutionOutcome(
        "persisted_failure", "workflow", "step", 1, "employee", "api_error"
    )
    assert (
        route_persisted_terminal_outcome_classification_bridge_reentry(
            outcome,
            workflow(),
            state,
            events,
            classification_routing_function=lambda *_: (_ for _ in ()).throw(
                AssertionError
            ),
        )
        is outcome
    )


@pytest.mark.parametrize(
    "invalid",
    [object(), WorkflowExecutionPersistenceResult(Path("a"), Path("b"), 1, 1)],
)
def test_invalid_result_rejects_before_phase44(tmp_path: Path, invalid: object) -> None:
    state, events, _ = setup(tmp_path)
    calls = 0

    def unexpected(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedTerminalOutcomeClassificationBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_bridge_reentry(
            invalid,
            workflow(),
            state,
            events,
            classification_routing_function=unexpected,
        )
    assert calls == 0


@pytest.mark.parametrize(
    "change",
    [
        lambda result: replace(result, state_bytes_written=True),
        lambda result: replace(result, event_bytes_appended=True),
        lambda result: replace(result, state_bytes_written=0),
        lambda result: replace(result, event_bytes_appended=0),
        lambda result: replace(result, state_path=result.events_path),
    ],
)
def test_bad_persistence_contract_rejects_before_phase44(
    tmp_path: Path, change: object
) -> None:
    state, events, persisted = setup(tmp_path)
    with pytest.raises(PersistedTerminalOutcomeClassificationBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_bridge_reentry(
            change(persisted),
            workflow(),
            state,
            events,
            classification_routing_function=lambda *_: (_ for _ in ()).throw(
                AssertionError
            ),  # type: ignore[operator]
        )


@pytest.mark.parametrize("operation", ["replace", "delete", "append"])
def test_phase44_mutation_is_restored_without_retry(
    tmp_path: Path, operation: str
) -> None:
    state, events, persisted = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def phase44(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        if operation == "delete":
            events.unlink()
        elif operation == "append":
            events.write_bytes(events.read_bytes() + b"x")
        else:
            events.write_bytes(b"changed")
        return PersistedExecutionOutcome(
            "persisted_success", "workflow", "step", 1, "employee", None
        )

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=phase44,
        )
    assert caught.value.detail.classification == "classification_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_dependency_error_identity_and_sanitization(tmp_path: Path) -> None:
    state, events, persisted = setup(tmp_path)
    safe = PersistedTerminalOutcomeClassificationRoutingCompatibilityError(
        "result_type"
    )
    with pytest.raises(
        PersistedTerminalOutcomeClassificationRoutingCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=lambda *_: (_ for _ in ()).throw(safe),
        )
    assert caught.value is safe
    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=lambda *_: (_ for _ in ()).throw(
                RuntimeError("sensitive output")
            ),
        )
    assert (
        caught.value.detail.classification == "dependency_error"
        and "output" not in str(caught.value)
    )
