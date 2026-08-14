"""Focused Phase 93 dispatch-boundary tests."""

# ruff: noqa: E501,E701,E702

import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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


class PersistenceResultSubclass(WorkflowExecutionPersistenceResult):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


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


_SIX_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_SENTINEL = object()


def six_step_workflow():
    return WorkflowDefinition.model_validate({"id": "w", "name": "W", "description": "D", "steps": [
        {"id": step_id, "name": step_id.capitalize(), "employee": step_id[0], "instructions": step_id}
        for step_id in _SIX_STEP_IDS
    ]})


def predecessor_event(step_id: str, position: int, *, provider: object = "other", request_id: object = _SENTINEL, output_text: object = "output") -> RuntimeStepEvent:
    resolved_request_id = f"request-{step_id}" if request_id is _SENTINEL else request_id
    return RuntimeStepEvent(
        "step_succeeded", "w", step_id, position, step_id[0], "running", "succeeded",
        provider,  # type: ignore[arg-type]
        None, f"response-{step_id}", resolved_request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        None,
    )


def terminal_event(status: str, *, message: str = "safe failure") -> RuntimeStepEvent:
    if status == "succeeded":
        return RuntimeStepEvent("step_succeeded", "w", "six", 6, "s", "running", "succeeded", "openai",
                                None, "response-six", "request-six", "output-six", None)
    return RuntimeStepEvent("step_failed", "w", "six", 6, "s", "running", "failed", "openai",
                            "api_error", None, "request-six", None, message)


def setup_six(tmp_path: Path, status: str, *, earlier_empty: tuple[int, ...] = (2,), message: str = "safe failure"):
    workflow = six_step_workflow()
    state_model = WorkflowExecutionState(
        "w", status, "six", 6, "s",
        tuple(_SIX_STEP_IDS) if status == "succeeded" else tuple(_SIX_STEP_IDS[:5]),
        None if status == "succeeded" else "api_error",
    )
    events = [
        predecessor_event(step_id, position, output_text="" if position in earlier_empty else "output")
        for position, step_id in enumerate(_SIX_STEP_IDS[:5], 1)
    ]
    events[4] = predecessor_event("five", 5, provider="openai", request_id=None, output_text="")
    events.append(terminal_event(status, message=message))
    state_bytes = serialize_workflow_execution_state_json(state_model).encode("utf-8")
    event_bytes = "".join(serialize_runtime_step_event_jsonl(event) for event in events).encode("utf-8")
    terminal_bytes = serialize_runtime_step_event_jsonl(events[-1]).encode("utf-8")
    state, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state, events_path, WorkflowExecutionPersistenceResult(state, events_path, len(state_bytes), len(terminal_bytes)), workflow, state_bytes, event_bytes


def six_step_outcome(status: str) -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w", "six", 6, "s", None if status == "succeeded" else "api_error",
    )


def test_phase155_succeeded_predecessor_empty_output_delegates_once(tmp_path: Path):
    state, events, result, workflow, before_state, before_events = setup_six(tmp_path, "succeeded")
    expected = six_step_outcome("succeeded")
    calls = []
    def fake(*args):
        calls.append(args); return expected
    returned = route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert returned is expected and calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
    # Inline transitive regression (no new collected case): Phase 93's own
    # validation runs the real Phase 86 _validate_persistence, so the strict
    # succeeded-terminal contract is enforced transitively — terminal response_id=""
    # and final succeeded output_text="" are rejected with terminal_contract before
    # the phase86_function seam is reached (seam not called again).
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)
    terminal = json.loads(lines[-1])
    assert terminal["step_id"] == "six" and terminal["next_status"] == "succeeded"
    terminal["response_id"] = ""
    events.write_text("".join(lines[:-1]) + json.dumps(terminal, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == [(result, workflow, state, events)]  # seam not called again
    terminal["response_id"] = "response-six"
    terminal["output_text"] = ""
    events.write_text("".join(lines[:-1]) + json.dumps(terminal, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == [(result, workflow, state, events)]  # seam not called again


def test_phase155_failed_predecessor_empty_output_message_empty_delegates_once(tmp_path: Path):
    state, events, result, workflow, before_state, before_events = setup_six(tmp_path, "failed", message="")
    expected = six_step_outcome("failed")
    calls = []
    def fake(*args):
        calls.append(args); return expected
    returned = route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert returned is expected and calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase155_succeeded_multiple_earlier_empty_delegates_once(tmp_path: Path):
    state, events, result, workflow, before_state, before_events = setup_six(tmp_path, "succeeded", earlier_empty=(2, 3))
    expected = six_step_outcome("succeeded")
    calls = []
    def fake(*args):
        calls.append(args); return expected
    returned = route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert returned is expected and calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase155_failed_multiple_earlier_empty_delegates_once(tmp_path: Path):
    state, events, result, workflow, before_state, before_events = setup_six(tmp_path, "failed", earlier_empty=(2, 3))
    expected = six_step_outcome("failed")
    calls = []
    def fake(*args):
        calls.append(args); return expected
    returned = route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert returned is expected and calls == [(result, workflow, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase155_earlier_predecessor_none_output_remains_rejected(tmp_path: Path):
    state, events, result, workflow, *_ = setup_six(tmp_path, "succeeded")
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)
    replacement = serialize_runtime_step_event_jsonl(predecessor_event("two", 2, output_text=None))
    assert json.loads(replacement)["request_id"] == "request-two"
    events.write_text(lines[0] + replacement + "".join(lines[2:]), encoding="utf-8")
    before = state.read_bytes(), events.read_bytes()
    calls = []
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=lambda *args: calls.append(args))
    assert caught.value.detail.classification == "terminal_contract" and calls == []
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_immediate_predecessor_non_string_output_remains_rejected(tmp_path: Path):
    state, events, result, workflow, *_ = setup_six(tmp_path, "succeeded")
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)
    assert json.loads(lines[1])["output_text"] == ""
    payload = json.loads(lines[4])
    assert payload["step_id"] == "five"
    assert payload["request_id"] is None
    assert payload["provider"] == "openai"
    payload["output_text"] = 1
    lines[4] = json.dumps(payload, separators=(",", ":")) + "\n"
    events.write_text("".join(lines), encoding="utf-8")
    before = state.read_bytes(), events.read_bytes()
    calls = []
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=lambda *args: calls.append(args))
    assert caught.value.detail.classification == "terminal_contract" and calls == []
    assert (state.read_bytes(), events.read_bytes()) == before


def error():
    return PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationError("terminal_contract")


def rewrite(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n")


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
    parameters = list(signature.parameters.values())
    assert [parameter.kind for parameter in parameters[:4]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 4
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[4].name == "phase86_function"


@pytest.mark.parametrize("bad", [
    PersistenceResultSubclass(Path("s"), Path("e"), 1, 1),
    SimpleNamespace(state_path=Path("s"), events_path=Path("e"), state_bytes_written=1, event_bytes_appended=1),
])
def test_persistence_result_exact_model_and_substitute_rejection(tmp_path: Path, bad: object):
    state, events, _, workflow, *_ = setup(tmp_path)
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(bad, workflow, state, events)
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize("field,value", [
    ("state_path", "state"), ("events_path", "events"),
    ("state_bytes_written", True), ("state_bytes_written", 1.0),
    ("state_bytes_written", "1"), ("event_bytes_appended", True),
    ("event_bytes_appended", 1.0), ("event_bytes_appended", "1"),
])
def test_persistence_result_every_field_requires_exact_type(tmp_path: Path, field: str, value: object):
    state, events, result, workflow, *_ = setup(tmp_path)
    bad = replace(result, **{field: value})
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(bad, workflow, state, events)
    assert caught.value.detail.classification == "persistence_contract"


def test_equal_but_not_identical_path_targets_and_conflict_are_rejected(tmp_path: Path):
    state, events, result, workflow, *_ = setup(tmp_path)
    cases = [(result, workflow, Path(str(state)), events), (result, workflow, state, Path(str(events))), (result, workflow, state, state)]
    for supplied_result, supplied_workflow, supplied_state, supplied_events in cases:
        with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
            route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(supplied_result, supplied_workflow, supplied_state, supplied_events)


@pytest.mark.parametrize("status,field,value", [
    ("succeeded", "workflow_id", "other"), ("succeeded", "status", "failed"),
    ("succeeded", "current_step_id", "other"), ("succeeded", "current_step_index", 2),
    ("succeeded", "current_employee_id", "other"), ("succeeded", "completed_step_ids", []),
    ("succeeded", "last_failure_category", "api_error"), ("failed", "workflow_id", "other"),
    ("failed", "status", "succeeded"), ("failed", "current_step_id", "other"),
    ("failed", "current_step_index", 2), ("failed", "current_employee_id", "other"),
    ("failed", "completed_step_ids", ["one"]), ("failed", "last_failure_category", None),
])
def test_terminal_state_every_field_mismatch_is_rejected(tmp_path: Path, status: str, field: str, value: object):
    state, events, result, workflow, *_ = setup(tmp_path, status)
    rewrite(state, field, value)
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events)
    assert caught.value.detail.classification in {"terminal_contract", "persistence_contract"}


@pytest.mark.parametrize("status,field,value", [
    ("succeeded", "event_type", "step_failed"), ("succeeded", "workflow_id", "other"),
    ("succeeded", "step_id", "other"), ("succeeded", "step_index", 2),
    ("succeeded", "employee_id", "other"), ("succeeded", "previous_status", "ready"),
    ("succeeded", "next_status", "failed"), ("succeeded", "failure_category", "api_error"),
    ("succeeded", "response_id", None), ("succeeded", "output_text", None),
    ("failed", "event_type", "step_succeeded"), ("failed", "workflow_id", "other"),
    ("failed", "step_id", "other"), ("failed", "step_index", 2),
    ("failed", "employee_id", "other"), ("failed", "previous_status", "ready"),
    ("failed", "next_status", "succeeded"), ("failed", "failure_category", None),
    ("failed", "message", None),
])
def test_terminal_event_every_field_mismatch_is_rejected(tmp_path: Path, status: str, field: str, value: object):
    state, events, result, workflow, *_ = setup(tmp_path, status)
    rewrite(events, field, value)
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_failed_and_succeeded_persistence_return_exact_dependency_object(tmp_path: Path, status: str):
    state, events, result, workflow, *_ = setup(tmp_path, status)
    expected = PersistedExecutionOutcome("persisted_success" if status == "succeeded" else "persisted_failure", "w", "one", 1, "a", None if status == "succeeded" else "api_error")
    calls = []
    returned = route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=lambda *args: (calls.append(args), expected)[1])
    assert returned is expected and len(calls) == 1


@pytest.mark.parametrize("status,field,value", [
    ("succeeded", "outcome", "persisted_failure"), ("succeeded", "workflow_id", "other"),
    ("succeeded", "current_step_id", "other"), ("succeeded", "current_step_index", True),
    ("succeeded", "current_step_index", 2), ("succeeded", "current_employee_id", "other"),
    ("succeeded", "failure_category", "api_error"), ("failed", "outcome", "persisted_success"),
    ("failed", "workflow_id", "other"), ("failed", "current_step_id", "other"),
    ("failed", "current_step_index", True), ("failed", "current_step_index", 2),
    ("failed", "current_employee_id", "other"), ("failed", "failure_category", "wrong"),
])
def test_outcome_all_fields_and_mismatches_are_rejected(tmp_path: Path, status: str, field: str, value: object):
    state, events, result, workflow, *_ = setup(tmp_path, status)
    valid = PersistedExecutionOutcome("persisted_success" if status == "succeeded" else "persisted_failure", "w", "one", 1, "a", None if status == "succeeded" else "api_error")
    bad = replace(valid, **{field: value})
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=lambda *_: bad)
    assert caught.value.detail.classification == "outcome_contract"


@pytest.mark.parametrize("bad", [
    OutcomeSubclass("persisted_success", "w", "one", 1, "a", None),
    SimpleNamespace(outcome="persisted_success"), object(),
])
def test_outcome_subclass_substitute_and_malformed_return_are_rejected_once(tmp_path: Path, bad: object):
    state, events, result, workflow, *_ = setup(tmp_path)
    calls = 0
    def fake(*_):
        nonlocal calls
        calls += 1
        return bad
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert caught.value.detail.classification == "outcome_contract" and calls == 1


def test_two_step_history_rejects_duplicate_missing_reordered_and_unrelated_events(tmp_path: Path):
    workflow = WorkflowDefinition.model_validate({"id": "w", "name": "W", "description": "D", "steps": [
        {"id": "one", "name": "One", "employee": "a", "instructions": "x"},
        {"id": "two", "name": "Two", "employee": "b", "instructions": "y"},
    ]})
    state_model = WorkflowExecutionState("w", "succeeded", "two", 2, "b", ("one", "two"), None)
    first = RuntimeStepEvent("step_succeeded", "w", "one", 1, "a", "running", "succeeded", "openai", None, "r1", "q1", "one", None)
    terminal = RuntimeStepEvent("step_succeeded", "w", "two", 2, "b", "running", "succeeded", "openai", None, "r2", "q2", "two", None)
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    first_bytes = serialize_runtime_step_event_jsonl(first).encode()
    terminal_bytes = serialize_runtime_step_event_jsonl(terminal).encode()
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes); events.write_bytes(first_bytes + terminal_bytes)
    result = WorkflowExecutionPersistenceResult(state, events, len(state_bytes), len(terminal_bytes))
    for event_bytes in [first_bytes + first_bytes + terminal_bytes, terminal_bytes, terminal_bytes + first_bytes, first_bytes + terminal_bytes + first_bytes]:
        events.write_bytes(event_bytes)
        with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
            route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=lambda *_: object())


@pytest.mark.parametrize("which,method", [("state", "is_file"), ("events", "is_file"), ("state", "read_bytes"), ("events", "read_bytes")])
def test_target_oserrors_are_classified_per_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, method: str):
    state, events, result, workflow, *_ = setup(tmp_path)
    target = state if which == "state" else events
    original = getattr(Path, method)
    def fail(path: Path):
        if path == target:
            raise OSError("unreadable")
        return original(path)
    monkeypatch.setattr(Path, method, fail)
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events)
    assert caught.value.detail.classification in {"state_target", "event_target", "terminal_contract"}


@pytest.mark.parametrize("which", ["state", "events"])
def test_missing_and_non_regular_targets_are_rejected(tmp_path: Path, which: str):
    state, events, result, workflow, *_ = setup(tmp_path)
    target = state if which == "state" else events
    target.unlink()
    target.mkdir()
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events)
    assert caught.value.detail.classification == f"{('state' if which == 'state' else 'event')}_target"


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


@pytest.mark.parametrize("field,value", [
    ("decision", "prepare_next_step"), ("workflow_id", "other"),
    ("current_step_id", "other"), ("current_step_index", True),
    ("current_employee_id", "other"), ("next_step_id", "next"),
    ("next_step_index", 2), ("next_employee_id", "b"), ("reason", "other"),
])
def test_malformed_completion_stop_route_is_rejected_before_dependency(tmp_path: Path, field: str, value: object):
    state, events, _, workflow, *_ = setup(tmp_path)
    calls = []
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(replace(completion(), **{field: value}), workflow, state, events, phase86_function=lambda *args: calls.append(args))
    assert caught.value.detail.classification == "completion_contract" and calls == []


@pytest.mark.parametrize("field,value", [
    ("outcome", "persisted_success"), ("workflow_id", "other"),
    ("current_step_id", "other"), ("current_step_index", True),
    ("current_employee_id", "other"), ("failure_category", "wrong"),
])
def test_malformed_failure_stop_route_is_rejected_before_dependency(tmp_path: Path, field: str, value: object):
    state, events, _, workflow, *_ = setup(tmp_path, "failed")
    calls = []
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(replace(failure(), **{field: value}), workflow, state, events, phase86_function=lambda *args: calls.append(args))
    assert caught.value.detail.classification == "failure_contract" and calls == []


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


@pytest.mark.parametrize("error_kind", ["safe", "unexpected"])
@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_and_unexpected_errors_compensate_each_mutation_and_preserve_safe_identity(tmp_path: Path, error_kind: str, mutation: str):
    state, events, result, workflow, before_state, before_events = setup(tmp_path)
    safe = error()
    def fake(*_):
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed-events")
        if error_kind == "safe":
            raise safe
        raise RuntimeError("sensitive detail")
    expected = PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationError if error_kind == "safe" else PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError
    with pytest.raises(expected) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    if error_kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


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


@pytest.mark.parametrize("failure_target", ["state", "events", "both"])
def test_rollback_failure_state_event_both_attempt_both_once_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_target: str):
    state, events, result, workflow, *_ = setup(tmp_path)
    original = Path.write_bytes
    attempts: list[Path] = []
    def write(path: Path, data: bytes) -> int:
        attempts.append(path)
        if failure_target in {"state", "both"} and path == state:
            raise OSError("state rollback")
        if failure_target in {"events", "both"} and path == events:
            raise OSError("events rollback")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", write)
    calls = 0
    def fake(*_):
        nonlocal calls
        calls += 1
        original(state, b"changed-state")
        original(events, b"changed-events")
        return object()
    with pytest.raises(PersistedOutcomeClassificationDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation(result, workflow, state, events, phase86_function=fake)
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1 and attempts == [state, events]
