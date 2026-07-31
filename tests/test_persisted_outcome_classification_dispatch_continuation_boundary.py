"""Focused tests for the Phase 100 continuation boundary."""

# ruff: noqa: E501

import inspect
import json
from dataclasses import replace
from pathlib import Path
from pathlib import Path as _TestPath
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedOutcomeClassificationDispatchContinuationCompatibilityError,
    PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationError,
    WorkflowProgressionDecision,
    route_persisted_outcome_classification_dispatch_continuation_boundary,
    route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def _setup(tmp_path: Path, status: str = "succeeded"):
    workflow = WorkflowDefinition.model_validate(
        {"id": "w", "name": "W", "description": "D", "steps": [
            {"id": "one", "name": "One", "employee": "a", "instructions": "x"},
        ]}
    )
    state_model = WorkflowExecutionState(
        "w", status, "one", 1, "a", ("one",) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error",
    )
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed", "w", "one", 1,
        "a", "running", status, "openai", None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None, "request",
        "out" if status == "succeeded" else None, None if status == "succeeded" else "safe",
    )
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes)
    events.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(state, events, len(state_bytes), len(event_bytes))
    return state, events, result, workflow, state_bytes, event_bytes


def _completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded")


def _failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", "api_error")


def test_public_signature_and_default_identity() -> None:
    signature = inspect.signature(route_persisted_outcome_classification_dispatch_continuation_boundary)
    parameters = list(signature.parameters.values())
    assert [p.name for p in parameters[:4]] == ["result", "workflow", "state_path", "events_path"]
    assert [p.kind for p in parameters[:4]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 4
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[4].name == "phase93_function"
    assert parameters[4].default is route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation


def test_implementation_does_not_reference_phase_private_internals() -> None:
    source = _TestPath(
        "src/ai_office/engine/persisted_outcome_classification_dispatch_continuation_boundary.py"
    ).read_text()
    assert "_phase93" not in source
    assert "_phase86" not in source
    assert "route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation" in source


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_valid_routes_call_phase93_once_and_return_exact_object(tmp_path: Path, status: str) -> None:
    state, events, result, workflow, before_state, before_events = _setup(tmp_path, status)
    expected = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w", "one", 1, "a", None if status == "succeeded" else "api_error",
    )
    calls: list[tuple[object, ...]] = []
    def fake(*args: object) -> object:
        calls.append(args)
        return expected
    returned = route_persisted_outcome_classification_dispatch_continuation_boundary(
        result, workflow, state, events, phase93_function=fake
    )
    assert returned is expected
    assert calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("bad", [SimpleNamespace(), object()])
def test_exact_result_model_and_path_identity_rejection(tmp_path: Path, bad: object) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    with pytest.raises(PersistedOutcomeClassificationDispatchContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_continuation_boundary(bad, workflow, state, events)
    assert caught.value.detail.classification == "result_type"
    with pytest.raises(PersistedOutcomeClassificationDispatchContinuationCompatibilityError):
        route_persisted_outcome_classification_dispatch_continuation_boundary(
            result, workflow, Path(str(state)), events
        )


def test_result_fields_and_positive_counts_are_exact(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    for field, value in (("state_path", Path(str(state))), ("events_path", Path(str(events))),
                         ("state_bytes_written", True), ("event_bytes_appended", 1.0),
                         ("state_bytes_written", 0)):
        bad = replace(result, **{field: value})
        with pytest.raises(PersistedOutcomeClassificationDispatchContinuationCompatibilityError) as caught:
            route_persisted_outcome_classification_dispatch_continuation_boundary(bad, workflow, state, events)
        assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("value", [_completion(), _failure()])
def test_stop_routes_are_unchanged_and_zero_call(tmp_path: Path, value: object) -> None:
    status = "succeeded" if type(value) is WorkflowProgressionDecision else "failed"
    state, events, _, workflow, before_state, before_events = _setup(tmp_path, status)
    calls = 0
    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()
    assert route_persisted_outcome_classification_dispatch_continuation_boundary(
        value, workflow, state, events, phase93_function=fake
    ) is value
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_invalid_stop_and_direct_success_are_rejected_without_call(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    calls = 0
    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return _failure()
    for value in (
        replace(_completion(), current_step_index=2),
        replace(_failure(), failure_category="wrong"),
        PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", None),
        WorkflowProgressionDecision("next_step", "w", "one", 1, "a", "two", 2, "b", "x"),
    ):
        with pytest.raises(PersistedOutcomeClassificationDispatchContinuationCompatibilityError):
            route_persisted_outcome_classification_dispatch_continuation_boundary(value, workflow, state, events, phase93_function=fake)
    assert calls == 0


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
def test_dependency_paths_compensate_and_do_not_retry(tmp_path: Path, mutation: str, kind: str) -> None:
    state, events, result, workflow, before_state, before_events = _setup(tmp_path)
    calls = 0
    safe = PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationError("dependency_error")
    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"mutated-state")
        if mutation in ("events", "both"):
            events.write_bytes(b"mutated-events")
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("boom")
        return object()
    with pytest.raises(Exception) as caught:
        route_persisted_outcome_classification_dispatch_continuation_boundary(result, workflow, state, events, phase93_function=fake)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
    if kind == "safe":
        assert caught.value is safe
    else:
        assert getattr(caught.value, "detail", None).classification in {"dependency_error", "outcome_contract"}


def test_outcome_subclass_and_substitute_rejected(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    class Sub(PersistedExecutionOutcome):
        pass
    for value in (Sub("persisted_success", "w", "one", 1, "a", None), SimpleNamespace(outcome="persisted_success")):
        with pytest.raises(PersistedOutcomeClassificationDispatchContinuationCompatibilityError) as caught:
            route_persisted_outcome_classification_dispatch_continuation_boundary(result, workflow, state, events, phase93_function=lambda *_: value)
        assert caught.value.detail.classification == "outcome_contract"


def test_unrelated_event_bytes_are_not_accepted(tmp_path: Path) -> None:
    state, events, result, workflow, *_ = _setup(tmp_path)
    payload = json.loads(events.read_text())
    payload["step_id"] = "unrelated"
    events.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    with pytest.raises(PersistedOutcomeClassificationDispatchContinuationCompatibilityError):
        route_persisted_outcome_classification_dispatch_continuation_boundary(result, workflow, state, events)
