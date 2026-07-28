"""Focused Phase 58 contract tests using injected Phase 51 fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedTerminalOutcomeClassificationBridgeCompatibilityError,
    PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError,
    WorkflowProgressionDecision,
    route_persisted_terminal_outcome_classification_phase_bridge_reentry,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class PersistenceSubclass(WorkflowExecutionPersistenceResult):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


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
) -> tuple[Path, Path, WorkflowExecutionPersistenceResult, bytes, bytes]:
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
        state_bytes,
        event_bytes,
    )


def test_persistence_result_delegates_once_with_identity_and_returns_identity(
    tmp_path: Path,
) -> None:
    state, events, persisted, before_state, before_events = setup(tmp_path)
    expected = PersistedExecutionOutcome(
        "persisted_success", "workflow", "step", 1, "employee", None
    )
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PersistedExecutionOutcome:
        calls.append(args)
        assert args == (persisted, workflow(), state, events)
        return expected

    returned = route_persisted_terminal_outcome_classification_phase_bridge_reentry(
        persisted, workflow(), state, events, phase51_function=fake
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_terminal_stop_routes_return_supplied_object_without_phase51(
    tmp_path: Path, status: str
) -> None:
    state, events, _, before_state, before_events = setup(tmp_path, status)
    supplied: object = (
        WorkflowProgressionDecision(
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
        if status == "succeeded"
        else PersistedExecutionOutcome(
            "persisted_failure", "workflow", "step", 1, "employee", "api_error"
        )
    )
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            supplied, workflow(), state, events, phase51_function=unexpected
        )
        is supplied
    )
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    "value",
    [
        object(),
        OutcomeSubclass(
            "persisted_failure", "workflow", "step", 1, "employee", "api_error"
        ),
        DecisionSubclass(
            "workflow_complete",
            "workflow",
            "step",
            1,
            "employee",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
    ],
)
def test_exact_top_level_types_are_required(tmp_path: Path, value: object) -> None:
    state, events, _, before_state, before_events = setup(tmp_path)
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            value, workflow(), state, events
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_persisted_success_is_not_a_stop_input(tmp_path: Path) -> None:
    state, events, _, _, _ = setup(tmp_path)
    supplied = PersistedExecutionOutcome(
        "persisted_success", "workflow", "step", 1, "employee", None
    )
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            supplied, workflow(), state, events
        )
    assert caught.value.detail.classification == "failure_contract"


@pytest.mark.parametrize("field", ["state_bytes_written", "event_bytes_appended"])
@pytest.mark.parametrize("count", [True, 0, -1, 1.0, "1", None, object()])
def test_invalid_counts_stop_before_phase51(
    tmp_path: Path, field: str, count: object
) -> None:
    state, events, persisted, before_state, before_events = setup(tmp_path)
    invalid = replace(persisted, **{field: count})
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            invalid,
            workflow(),
            state,
            events,
            phase51_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_malformed_dependency_is_compensated_without_retry(tmp_path: Path) -> None:
    state, events, persisted, before_state, before_events = setup(tmp_path)
    calls = 0

    def malformed(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed")
        events.write_bytes(b"changed\n")
        return object()

    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            persisted, workflow(), state, events, phase51_function=malformed
        )
    assert caught.value.detail.classification == "classification_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


def test_safe_phase51_error_identity_is_preserved_after_rollback(
    tmp_path: Path,
) -> None:
    state, events, persisted, before_state, before_events = setup(tmp_path)
    expected = PersistedTerminalOutcomeClassificationBridgeCompatibilityError(
        "terminal_contract"
    )

    def raises(*_: object) -> object:
        state.write_bytes(b"changed")
        raise expected

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            persisted, workflow(), state, events, phase51_function=raises
        )
    assert caught.value is expected
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_unexpected_phase51_error_is_sanitized_and_targets_restored(
    tmp_path: Path,
) -> None:
    state, events, persisted, before_state, before_events = setup(tmp_path)

    def raises(*_: object) -> object:
        state.write_bytes(b"secret provider response")
        raise RuntimeError("path=/private/token=secret")

    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            persisted, workflow(), state, events, phase51_function=raises
        )
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
