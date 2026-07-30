"""Focused Phase 79 boundary tests."""

# ruff: noqa: E501

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedOutcomeClassificationRoutingPhaseBridgeContinuationError,
    PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError,
    WorkflowProgressionDecision,
    route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class ResultSubclass(WorkflowExecutionPersistenceResult):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


def definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({
        "id": "w", "name": "W", "description": "D",
        "steps": [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}],
    })


def setup(tmp_path: Path, status: str = "succeeded"):
    workflow = definition()
    state_model = WorkflowExecutionState(
        "w", status, "one", 1, "a", ("one",) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error",
    )
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed", "w", "one", 1, "a",
        "running", status, "openai", None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None, "request", "out" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes)
    events.write_bytes(event_bytes)
    return state, events, WorkflowExecutionPersistenceResult(state, events, len(state_bytes), len(event_bytes)), workflow, state_bytes, event_bytes


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", "api_error")


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded")


def error() -> PersistedOutcomeClassificationRoutingPhaseBridgeContinuationError:
    return PersistedOutcomeClassificationRoutingPhaseBridgeContinuationError("terminal_contract")


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_valid_persistence_result_delegates_once_and_returns_exact_phase72_object(tmp_path: Path, status: str) -> None:
    state, events, result, workflow, before_state, before_events = setup(tmp_path, status)
    expected = PersistedExecutionOutcome("persisted_success" if status == "succeeded" else "persisted_failure", "w", "one", 1, "a", None if status == "succeeded" else "api_error")
    calls: list[tuple[object, ...]] = []

    def phase72(*args: object) -> object:
        calls.append(args)
        assert args == (result, workflow, state, events)
        return expected

    returned = route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=phase72)
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("value", [completion(), failure()])
def test_valid_stop_routes_return_same_object_without_phase72(tmp_path: Path, value: object) -> None:
    status = "succeeded" if isinstance(value, WorkflowProgressionDecision) else "failed"
    state, events, _, workflow, before_state, before_events = setup(tmp_path, status)
    calls = 0

    def phase72(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(value, workflow, state, events, phase72_function=phase72) is value
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_direct_persisted_success_is_rejected(tmp_path: Path) -> None:
    state, events, _, workflow, _, _ = setup(tmp_path, "succeeded")
    supplied = PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", None)
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(supplied, workflow, state, events)
    assert caught.value.detail.classification == "failure_contract"


def test_exact_input_models_and_path_identity_are_required(tmp_path: Path) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path)
    cases = [
        (ResultSubclass(state, events, result.state_bytes_written, result.event_bytes_appended), workflow, state, events),
        (result, object(), state, events),
        (result, workflow, Path(str(state)), events),
        (result, workflow, state, Path(str(events))),
        (result, workflow, state, state),
    ]
    for supplied, supplied_workflow, supplied_state, supplied_events in cases:
        with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError):
            route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(supplied, supplied_workflow, supplied_state, supplied_events, phase72_function=lambda *_: object())


@pytest.mark.parametrize("field", ["state_bytes_written", "event_bytes_appended"])
def test_persistence_byte_counts_are_exact(tmp_path: Path, field: str) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path)
    bad = replace(result, **{field: getattr(result, field) + 1})
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(bad, workflow, state, events)
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("mutate", ["state", "events", "both"])
def test_dependency_mutation_is_compensated_and_not_retried(tmp_path: Path, mutate: str) -> None:
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    calls = 0

    def phase72(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutate in ("state", "both"):
            state.write_bytes(b"changed-state")
        if mutate in ("events", "both"):
            events.write_bytes(b"changed-events")
        return object()

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError):
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=phase72)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
def test_safe_error_identity_and_unexpected_error_sanitization(tmp_path: Path, kind: str) -> None:
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    safe = error()

    def phase72(*_: object) -> object:
        if kind == "safe":
            raise safe
        raise RuntimeError("secret")

    expected = PersistedOutcomeClassificationRoutingPhaseBridgeContinuationError if kind == "safe" else PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError
    with pytest.raises(expected) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=phase72)
    if kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_safe_error_identity_survives_successful_compensation(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    safe = error()

    def phase72(*_: object) -> object:
        state.write_bytes(b"changed")
        events.write_bytes(b"changed")
        raise safe

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeContinuationError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=phase72)
    assert caught.value is safe
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_malformed_return_is_compensated(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError):
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=lambda *_: object())
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_malformed_return_and_rollback_failure_attempts_both_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path)
    state.write_bytes(state.read_bytes())
    original_write = Path.write_bytes
    attempted: list[Path] = []

    def write(path: Path, data: bytes) -> int:
        attempted.append(path)
        if path == state:
            raise OSError("state rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    def phase72(*_: object) -> object:
        original_write(state, b"changed")
        original_write(events, b"changed")
        return object()

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=phase72)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempted == [state, events]


def test_terminal_field_mismatch_is_rejected(tmp_path: Path) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path)
    data = json.loads(state.read_text())
    data["status"] = "failed"
    state.write_text(json.dumps(data) + "\n")
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError):
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events)
