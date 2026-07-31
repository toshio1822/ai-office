"""Focused Phase 93 dispatch-boundary tests."""

# ruff: noqa: E501,E701,E702

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError,
    PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationError,
    WorkflowProgressionDecision,
    route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def setup(tmp_path: Path, status: str = "succeeded"):
    workflow = WorkflowDefinition.model_validate({"id": "w", "name": "W", "description": "D", "steps": [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]})
    state_model = WorkflowExecutionState("w", status, "one", 1, "a", ("one",) if status == "succeeded" else (), None if status == "succeeded" else "api_error")
    event = RuntimeStepEvent("step_succeeded" if status == "succeeded" else "step_failed", "w", "one", 1, "a", "running", status, "openai", None if status == "succeeded" else "api_error", "response" if status == "succeeded" else None, "request", "out" if status == "succeeded" else None, None if status == "succeeded" else "safe")
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes); events.write_bytes(event_bytes)
    return state, events, WorkflowExecutionPersistenceResult(state, events, len(state_bytes), len(event_bytes)), workflow, state_bytes, event_bytes


def completion():
    return WorkflowProgressionDecision("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded")


def failure():
    return PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", "api_error")


def error():
    return PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationError("terminal_contract")


def test_public_signature_and_valid_routes(tmp_path: Path):
    signature = inspect.signature(route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation)
    assert list(signature.parameters)[:4] == ["result", "workflow", "state_path", "events_path"]
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    expected = PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", None)
    calls = []
    def fake(*args):
        calls.append(args); return expected
    returned = route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert returned is expected and calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_failed_and_succeeded_persistence_return_exact_dependency_object(tmp_path: Path, status: str):
    state, events, result, workflow, *_ = setup(tmp_path, status)
    expected = PersistedExecutionOutcome("persisted_success" if status == "succeeded" else "persisted_failure", "w", "one", 1, "a", None if status == "succeeded" else "api_error")
    calls = []
    returned = route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=lambda *args: (calls.append(args), expected)[1])
    assert returned is expected and len(calls) == 1


@pytest.mark.parametrize("value,status", [(completion(), "succeeded"), (failure(), "failed")])
def test_stop_routes_are_zero_call_and_unchanged(tmp_path: Path, value: object, status: str):
    state, events, _, workflow, before_state, before_events = setup(tmp_path, status)
    calls = 0
    def fake(*_):
        nonlocal calls; calls += 1
    assert route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(value, workflow, state, events, phase86_function=fake) is value
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_unsupported_and_direct_success_are_rejected_without_dependency(tmp_path: Path):
    state, events, _, workflow, *_ = setup(tmp_path)
    calls = []
    for value in [replace(completion(), decision="prepare_next_step"), PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", None)]:
        with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
            route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(value, workflow, state, events, phase86_function=lambda *args: calls.append(args))
    assert calls == []


def test_exact_models_paths_counts_and_terminal_event_are_validated(tmp_path: Path):
    state, events, result, workflow, *_ = setup(tmp_path)
    for bad in [replace(result, state_bytes_written=result.state_bytes_written + 1), replace(result, event_bytes_appended=result.event_bytes_appended + 1), replace(result, state_path=Path(str(state))), replace(result, events_path=Path(str(events)))]:
        with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
            route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(bad, workflow, state, events, phase86_function=lambda *_: object())


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_mutation_is_compensated_and_dependency_called_once(tmp_path: Path, mutation: str):
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    calls = 0
    def fake(*_):
        nonlocal calls; calls += 1
        if mutation in ("state", "both"): state.write_bytes(b"changed-state")
        if mutation in ("events", "both"): events.write_bytes(b"changed-events")
        return object()
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_safe_error_identity_and_unexpected_sanitization(tmp_path: Path):
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    safe = error()
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=lambda *_: (_ for _ in ()).throw(safe))
    assert caught.value is safe and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=lambda *_: (_ for _ in ()).throw(RuntimeError("secret")))
    assert caught.value.detail.classification == "dependency_error"


def test_malformed_return_no_retry_and_compensation(tmp_path: Path):
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    calls = 0
    def fake(*_):
        nonlocal calls; calls += 1; state.write_bytes(b"bad"); events.write_bytes(b"bad"); return object()
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_rollback_failure_attempts_both_targets_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state, events, result, workflow, *_ = setup(tmp_path)
    original = Path.write_bytes; attempts = []
    def write(path, data):
        attempts.append(path)
        if path in (state, events): raise OSError("rollback")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", write)
    def fake(*_):
        original(state, b"changed"); original(events, b"changed"); return object()
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert caught.value.detail.classification == "dependency_rollback" and attempts == [state, events]
