"""Focused Phase 79 boundary tests."""

# ruff: noqa: E501

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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


class IntSubclass(int):
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


def rewrite_json(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n")


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


def test_phase72_receives_each_exact_object_identity(tmp_path: Path) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path)
    received: list[object] = []
    expected = PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", None)

    def phase72(received_result: object, received_workflow: object, received_state: object, received_events: object) -> object:
        received.extend((received_result, received_workflow, received_state, received_events))
        return expected

    route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=phase72)
    assert received[0] is result
    assert received[1] is workflow
    assert received[2] is state
    assert received[3] is events


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state_path", Path("other")),
        ("events_path", Path("other")),
        ("state_bytes_written", True),
        ("state_bytes_written", IntSubclass(1)),
        ("state_bytes_written", 0),
        ("event_bytes_appended", True),
        ("event_bytes_appended", IntSubclass(1)),
        ("event_bytes_appended", 0),
    ],
)
def test_persistence_result_each_field_requires_exact_type_and_value(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path)
    bad = replace(result, **{field: value})
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(bad, workflow, state, events, phase72_function=lambda *_: object())
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("bad", [OutcomeSubclass("persisted_failure", "w", "one", 1, "a", "api_error"), SimpleNamespace(outcome="persisted_failure")])
def test_outcome_subclass_and_substitute_are_rejected(tmp_path: Path, bad: object) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path, "failed")
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=lambda *_: bad)
    assert caught.value.detail.classification == "outcome_contract"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", "persisted_success"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", True),
        ("current_step_index", 2),
        ("current_employee_id", "other"),
        ("failure_category", "timeout"),
        ("failure_category", None),
    ],
)
def test_outcome_each_field_is_validated_at_phase79_seam(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path, "failed")
    bad = replace(failure(), **{field: value})
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=lambda *_: bad)
    assert caught.value.detail.classification == "outcome_contract"


@pytest.mark.parametrize("field", ["decision", "workflow_id", "current_step_id", "current_step_index", "current_employee_id", "next_step_id", "next_step_index", "next_employee_id", "reason"])
def test_unsupported_or_malformed_completion_stop_route_is_rejected(tmp_path: Path, field: str) -> None:
    state, events, _, workflow, _, _ = setup(tmp_path)
    values = {"decision": "prepare_next_step", "workflow_id": "other", "current_step_id": "other", "current_step_index": True, "current_employee_id": "other", "next_step_id": "next", "next_step_index": 2, "next_employee_id": "b", "reason": "other"}
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(replace(completion(), **{field: values[field]}), workflow, state, events)
    assert caught.value.detail.classification == "completion_contract"


@pytest.mark.parametrize("field", ["outcome", "workflow_id", "current_step_id", "current_step_index", "current_employee_id", "failure_category"])
def test_malformed_persisted_failure_stop_route_is_rejected(tmp_path: Path, field: str) -> None:
    state, events, _, workflow, _, _ = setup(tmp_path, "failed")
    values = {"outcome": "persisted_success", "workflow_id": "other", "current_step_id": "other", "current_step_index": True, "current_employee_id": "other", "failure_category": "timeout"}
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(replace(failure(), **{field: values[field]}), workflow, state, events)
    assert caught.value.detail.classification == "failure_contract"


@pytest.mark.parametrize("status", ["succeeded", "failed"])
@pytest.mark.parametrize("field", ["workflow_id", "status", "current_step_id", "current_step_index", "current_employee_id", "completed_step_ids", "last_failure_category"])
def test_terminal_state_and_completion_prefix_mismatches_are_rejected(tmp_path: Path, status: str, field: str) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path, status)
    values = {"workflow_id": "other", "status": "failed" if status == "succeeded" else "succeeded", "current_step_id": "other", "current_step_index": 2, "current_employee_id": "other", "completed_step_ids": [] if status == "succeeded" else ["one"], "last_failure_category": "timeout" if status == "succeeded" else None}
    rewrite_json(state, field, values[field])
    bad = replace(result, state_bytes_written=state.stat().st_size)
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError):
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(bad, workflow, state, events, phase72_function=lambda *_: object())


@pytest.mark.parametrize("status", ["succeeded", "failed"])
@pytest.mark.parametrize("field", ["event_type", "workflow_id", "step_id", "step_index", "employee_id", "previous_status", "next_status", "provider", "failure_category", "response_id", "request_id", "output_text", "message"])
def test_terminal_event_and_failure_category_mismatches_are_rejected(tmp_path: Path, status: str, field: str) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path, status)
    values = {"event_type": "step_failed" if status == "succeeded" else "step_succeeded", "workflow_id": "other", "step_id": "other", "step_index": 2, "employee_id": "other", "previous_status": "ready", "next_status": "failed" if status == "succeeded" else "succeeded", "provider": True, "failure_category": "api_error" if status == "succeeded" else None, "response_id": 1, "request_id": True, "output_text": 1, "message": 1}
    rewrite_json(events, field, values[field])
    bad = replace(result, state_bytes_written=state.stat().st_size, event_bytes_appended=events.stat().st_size)
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError):
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(bad, workflow, state, events, phase72_function=lambda *_: object())


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_non_regular_targets_are_rejected(tmp_path: Path, kind: str) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path)
    if kind == "missing":
        state.unlink()
    else:
        events.unlink()
        events.mkdir()
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events)
    assert caught.value.detail.classification == ("state_target" if kind == "missing" else "event_target")


@pytest.mark.parametrize("which", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_state_and_event_target_oserrors_are_classified_separately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, operation: str) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path)
    target = state if which == "state" else events
    original = getattr(Path, operation)

    def fail(path: Path):
        if path == target:
            raise OSError("unreadable")
        return original(path)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events)
    assert caught.value.detail.classification == ("state_target" if which == "state" else "event_target")


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


@pytest.mark.parametrize("failure_target", ["state", "events", "both"])
def test_each_rollback_failure_attempts_both_restorations_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_target: str
) -> None:
    state, events, result, workflow, _, _ = setup(tmp_path)
    original_write = Path.write_bytes
    attempted: list[Path] = []

    def write(path: Path, data: bytes) -> int:
        attempted.append(path)
        if failure_target in ("state", "both") and path == state:
            raise OSError("state rollback")
        if failure_target in ("events", "both") and path == events:
            raise OSError("event rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)

    def phase72(*_: object) -> object:
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=phase72)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempted == [state, events]


@pytest.mark.parametrize("mutated", ["state", "events"])
def test_safe_error_identity_is_preserved_after_single_target_mutation(
    tmp_path: Path, mutated: str
) -> None:
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    safe = error()

    def phase72(*_: object) -> object:
        (state if mutated == "state" else events).write_bytes(b"changed")
        raise safe

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeContinuationError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=phase72)
    assert caught.value is safe
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("bad", [object(), OutcomeSubclass("persisted_success", "w", "one", 1, "a", None), SimpleNamespace(outcome="persisted_failure")])
def test_malformed_returns_call_phase72_once_and_never_retry(tmp_path: Path, bad: object) -> None:
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    calls = 0

    def phase72(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError):
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(result, workflow, state, events, phase72_function=phase72)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


# Phase 155 provenance compatibility: six-step fixture accepted through the
# strict-first fallback and rejected when the bounded contract is violated.

_SIX_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_SENTINEL = object()


def six_step_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({
        "id": "w", "name": "W", "description": "D",
        "steps": [
            {"id": sid, "name": sid.capitalize(), "employee": sid[0], "instructions": sid}
            for sid in _SIX_STEP_IDS
        ],
    })


def six_step_predecessor(
    step_id: str, position: int, *, provider: object = "other",
    request_id: object = _SENTINEL, output_text: object = "output",
) -> RuntimeStepEvent:
    resolved = f"request-{step_id}" if request_id is _SENTINEL else request_id
    return RuntimeStepEvent(
        "step_succeeded", "w", step_id, position, step_id[0],
        "running", "succeeded", provider, None,
        f"response-{step_id}", resolved, output_text, None,
    )


def six_step_terminal(status: str, *, message: str = "safe failure", request_id: object = _SENTINEL, provider: object = "openai") -> RuntimeStepEvent:
    resolved = "request-six" if request_id is _SENTINEL else request_id
    if status == "succeeded":
        return RuntimeStepEvent(
            "step_succeeded", "w", "six", 6, "s", "running", "succeeded",
            provider, None, "response-six", resolved, "output-six", None,
        )
    return RuntimeStepEvent(
        "step_failed", "w", "six", 6, "s", "running", "failed",
        provider, "api_error", None, resolved, None, message,
    )


def six_step_setup(
    tmp_path: Path, status: str, *, earlier_empty: tuple[int, ...] = (2,),
    message: str = "safe failure", terminal_request_id: object = _SENTINEL,
    terminal_provider: object = "openai",
) -> tuple[Path, Path, WorkflowExecutionPersistenceResult, WorkflowDefinition, bytes, bytes]:
    workflow = six_step_workflow()
    state_model = WorkflowExecutionState(
        "w", status, "six", 6, "s",
        tuple(_SIX_STEP_IDS) if status == "succeeded" else tuple(_SIX_STEP_IDS[:5]),
        None if status == "succeeded" else "api_error",
    )
    events = [
        six_step_predecessor(
            step_id, position,
            output_text="" if position in earlier_empty else "output",
        )
        for position, step_id in enumerate(_SIX_STEP_IDS[:5], 1)
    ]
    events[4] = six_step_predecessor("five", 5, provider="openai", request_id=None, output_text="")
    events.append(six_step_terminal(status, message=message, request_id=terminal_request_id, provider=terminal_provider))
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    event_bytes = "".join(serialize_runtime_step_event_jsonl(ev) for ev in events).encode()
    terminal_bytes = serialize_runtime_step_event_jsonl(events[-1]).encode()
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(
        state_path, events_path, len(state_bytes), len(terminal_bytes),
    )
    return state_path, events_path, result, workflow, state_bytes, event_bytes


def six_step_outcome(status: str) -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w", "six", 6, "s", None if status == "succeeded" else "api_error",
    )


def five_index_terminal_setup(
    tmp_path: Path, *, request_id: object = _SENTINEL, provider: object = "openai",
) -> tuple[Path, Path, WorkflowExecutionPersistenceResult, WorkflowDefinition, bytes, bytes]:
    """Succeeded persisted terminal at step index 5 (pre-threshold for the Issue #373 gate)."""
    workflow = six_step_workflow()
    state_model = WorkflowExecutionState(
        "w", "succeeded", "five", 5, "f",
        tuple(_SIX_STEP_IDS[:5]), None,
    )
    events = [
        six_step_predecessor(
            step_id, position,
            output_text="" if position in (2,) else "output",
        )
        for position, step_id in enumerate(_SIX_STEP_IDS[:4], 1)
    ]
    events[3] = six_step_predecessor("four", 4, provider="openai", request_id=None, output_text="")
    resolved = "request-five" if request_id is _SENTINEL else request_id
    events.append(RuntimeStepEvent(
        "step_succeeded", "w", "five", 5, "f", "running", "succeeded",
        provider, None, "response-five", resolved, "output-five", None,
    ))
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    event_bytes = "".join(serialize_runtime_step_event_jsonl(ev) for ev in events).encode()
    terminal_bytes = serialize_runtime_step_event_jsonl(events[-1]).encode()
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(
        state_path, events_path, len(state_bytes), len(terminal_bytes),
    )
    return state_path, events_path, result, workflow, state_bytes, event_bytes


def test_phase155_six_step_succeeded_delegates_once(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = six_step_setup(tmp_path, "succeeded")
    expected = six_step_outcome("succeeded")
    calls: list[tuple[object, ...]] = []

    def phase72(*args: object) -> object:
        calls.append(args)
        expected_args = (result, workflow, state, events)
        assert len(args) == 4
        assert all(
            actual is wanted
            for actual, wanted in zip(args, expected_args, strict=True)
        )
        return expected

    returned = route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
        result, workflow, state, events, phase72_function=phase72
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
    # Inline: the fallback does not weaken the strict succeeded-terminal
    # contract: an empty terminal response_id or an empty final output_text is
    # independently rejected with exact terminal_contract before Phase 72.
    # The original terminal bytes are restored before each mutation so the
    # second rejection cannot be caused by the first mutation still on disk.
    for field, value in (("response_id", ""), ("output_text", "")):
        events.write_bytes(before_events)
        terminal_lines = events.read_text(encoding="utf-8").splitlines(keepends=True)
        terminal_payload = json.loads(terminal_lines[-1])
        terminal_payload[field] = value
        terminal_lines[-1] = json.dumps(terminal_payload, separators=(",", ":")) + "\n"
        events.write_text("".join(terminal_lines), encoding="utf-8")
        with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
            route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
                result, workflow, state, events,
                phase72_function=lambda *_: pytest.fail("Phase 72 must not be called"),
            )
        assert caught.value.detail.classification == "terminal_contract"
    events.write_bytes(before_events)
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase155_six_step_failed_delegates_once(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = six_step_setup(tmp_path, "failed", message="")
    expected = six_step_outcome("failed")
    calls: list[tuple[object, ...]] = []

    def phase72(*args: object) -> object:
        calls.append(args)
        expected_args = (result, workflow, state, events)
        assert len(args) == 4
        assert all(
            actual is wanted
            for actual, wanted in zip(args, expected_args, strict=True)
        )
        return expected

    returned = route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
        result, workflow, state, events, phase72_function=phase72
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase155_six_step_multiple_earlier_empty_delegates_once(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = six_step_setup(tmp_path, "succeeded", earlier_empty=(2, 3))
    expected = six_step_outcome("succeeded")
    calls: list[tuple[object, ...]] = []

    def phase72(*args: object) -> object:
        calls.append(args)
        expected_args = (result, workflow, state, events)
        assert len(args) == 4
        assert all(
            actual is wanted
            for actual, wanted in zip(args, expected_args, strict=True)
        )
        return expected

    returned = route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
        result, workflow, state, events, phase72_function=phase72
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase155_six_step_failed_multiple_earlier_empty_delegates_once(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = six_step_setup(tmp_path, "failed", earlier_empty=(2, 3), message="")
    expected = six_step_outcome("failed")
    calls: list[tuple[object, ...]] = []

    def phase72(*args: object) -> object:
        calls.append(args)
        expected_args = (result, workflow, state, events)
        assert len(args) == 4
        assert all(
            actual is wanted
            for actual, wanted in zip(args, expected_args, strict=True)
        )
        return expected

    returned = route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
        result, workflow, state, events, phase72_function=phase72
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase155_six_step_rejects_none_earlier_predecessor_output(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = six_step_setup(tmp_path, "succeeded")
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)
    payload = json.loads(lines[1])
    assert payload["step_id"] == "two" and payload["request_id"] == "request-two"
    payload["output_text"] = None
    lines[1] = json.dumps(payload, separators=(",", ":")) + "\n"
    events.write_text("".join(lines), encoding="utf-8")
    mutated_events = events.read_bytes()
    calls = {"phase72": 0}

    def phase72(*_: object) -> object:
        calls["phase72"] += 1
        pytest.fail("Phase 72 must not be called")

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
            result, workflow, state, events, phase72_function=phase72
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase72": 0}
    # The rejection leaves the mutated targets byte-identical (no compensation
    # write is expected because the route never delegated).
    assert (state.read_bytes(), events.read_bytes()) == (before_state, mutated_events)


def test_phase155_six_step_rejects_non_string_immediate_predecessor_output(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = six_step_setup(tmp_path, "succeeded")
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)
    payload = json.loads(lines[4])
    assert payload["step_id"] == "five" and payload["request_id"] is None and payload["provider"] == "openai"
    payload["output_text"] = 1
    lines[4] = json.dumps(payload, separators=(",", ":")) + "\n"
    events.write_text("".join(lines), encoding="utf-8")
    mutated_events = events.read_bytes()
    calls = {"phase72": 0}

    def phase72(*_: object) -> object:
        calls["phase72"] += 1
        pytest.fail("Phase 72 must not be called")

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
            result, workflow, state, events, phase72_function=phase72
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase72": 0}
    # The rejection leaves the mutated targets byte-identical (no compensation
    # write is expected because the route never delegated).
    assert (state.read_bytes(), events.read_bytes()) == (before_state, mutated_events)


def test_issue373_succeeded_terminal_none_request_id_openai_delegates_once(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = six_step_setup(
        tmp_path, "succeeded", terminal_request_id=None, terminal_provider="openai"
    )
    expected = six_step_outcome("succeeded")
    calls: list[tuple[object, ...]] = []

    def phase72(*args: object) -> object:
        calls.append(args)
        return expected

    returned = route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
        result, workflow, state, events, phase72_function=phase72
    )
    assert returned is expected and calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_issue373_succeeded_terminal_none_request_id_non_openai_rejected_before_dependency(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = six_step_setup(
        tmp_path, "succeeded", terminal_request_id=None, terminal_provider="other"
    )
    calls = 0

    def phase72(*_: object) -> object:
        nonlocal calls
        calls += 1
        pytest.fail("Phase 72 must not be called")

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
            result, workflow, state, events, phase72_function=phase72
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_issue373_pre_threshold_index_five_none_request_id_rejected_before_dependency(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = five_index_terminal_setup(
        tmp_path, request_id=None, provider="openai"
    )
    calls = 0

    def phase72(*_: object) -> object:
        nonlocal calls
        calls += 1
        pytest.fail("Phase 72 must not be called")

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
            result, workflow, state, events, phase72_function=phase72
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_issue373_failed_terminal_none_request_id_rejected_before_dependency(tmp_path: Path) -> None:
    state, events, result, workflow, before_state, before_events = six_step_setup(
        tmp_path, "failed", terminal_request_id=None, terminal_provider="openai"
    )
    calls = 0

    def phase72(*_: object) -> object:
        nonlocal calls
        calls += 1
        pytest.fail("Phase 72 must not be called")

    with pytest.raises(PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(
            result, workflow, state, events, phase72_function=phase72
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
