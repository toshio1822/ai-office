"""Focused Phase 94 dispatch-boundary tests."""

# ruff: noqa: E501,E701,E702

import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError,
    ClassifiedOutcomeRoutingPhaseBridgeContinuationError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation,
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


class PathSubclass(type(Path())):
    pass


class IntSubclass(int):
    pass


def workflow(single: bool = False) -> WorkflowDefinition:
    steps = [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]
    if not single:
        steps.append({"id": "two", "name": "Two", "employee": "b", "instructions": "y"})
    return WorkflowDefinition.model_validate({"id": "w", "name": "W", "description": "D", "steps": steps})


def setup(tmp_path: Path, *, single: bool = False, failed: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = workflow(single)
    index = len(definition.steps) if single else 1
    step = definition.steps[index - 1]
    status = "failed" if failed else "succeeded"
    state_model = WorkflowExecutionState("w", status, step.id, index, step.employee, () if failed else tuple(item.id for item in definition.steps[:index]), "api_error" if failed else None)
    event = RuntimeStepEvent("step_failed" if failed else "step_succeeded", "w", step.id, index, step.employee, "running", status, "openai", "api_error" if failed else None, None if failed else "response", "request", None if failed else "output", "failure" if failed else None)
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(serialize_workflow_execution_state_json(state_model).encode())
    events.write_bytes(serialize_runtime_step_event_jsonl(event).encode())
    result = PersistedExecutionOutcome("persisted_failure" if failed else "persisted_success", "w", step.id, index, step.employee, "api_error" if failed else None)
    return result, definition, state, events


def prepare() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available")


def complete() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded")


def rewrite(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n")


def setup_two_step_terminal(tmp_path: Path):
    definition = workflow()
    state_model = WorkflowExecutionState("w", "succeeded", "two", 2, "b", ("one", "two"), None)
    first = RuntimeStepEvent("step_succeeded", "w", "one", 1, "a", "running", "succeeded", "openai", None, "response-1", "request-1", "output-1", None)
    terminal = RuntimeStepEvent("step_succeeded", "w", "two", 2, "b", "running", "succeeded", "openai", None, "response-2", "request-2", "output-2", None)
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(serialize_workflow_execution_state_json(state_model).encode())
    first_bytes = serialize_runtime_step_event_jsonl(first).encode()
    terminal_bytes = serialize_runtime_step_event_jsonl(terminal).encode()
    events.write_bytes(first_bytes + terminal_bytes)
    result = PersistedExecutionOutcome("persisted_success", "w", "two", 2, "b", None)
    return result, definition, state, events, first_bytes, terminal_bytes


def test_public_signature_and_canonical_order(tmp_path: Path):
    signature = inspect.signature(route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation)
    parameters = list(signature.parameters.values())
    assert [p.name for p in parameters[:4]] == ["result", "workflow", "state_path", "events_path"]
    assert [p.kind for p in parameters[:4]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 4
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY and parameters[4].name == "phase87_function"
    result, definition, state, events = setup(tmp_path)
    calls = []
    expected = prepare()
    def fake(*args):
        calls.append(args)
        return expected
    returned = route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase87_function=fake)
    assert returned is expected and calls == [(result, definition, state, events)]
    assert all(calls[0][i] is value for i, value in enumerate((result, definition, state, events)))


@pytest.mark.parametrize("bad_workflow", [WorkflowSubclass.model_validate({"id": "w", "name": "W", "description": "D", "steps": [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]}), SimpleNamespace(id="w")])
def test_workflow_requires_exact_model_and_rejects_subclass_substitute(tmp_path: Path, bad_workflow: object):
    result, _, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, bad_workflow, state, events)
    assert caught.value.detail.classification == "workflow_definition"


def test_paths_require_exact_identity_type_and_target_conflict(tmp_path: Path):
    result, definition, state, events = setup(tmp_path)
    for supplied_state, supplied_events, classification in [
        (PathSubclass(state), events, "state_target"),
        (state, PathSubclass(events), "event_target"),
        (state, state, "target_conflict"),
        (SimpleNamespace(is_file=lambda: True), events, "state_target"),
        (state, SimpleNamespace(is_file=lambda: True), "event_target"),
    ]:
        with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
            route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, supplied_state, supplied_events)
        assert caught.value.detail.classification == classification


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("field,value", [
    ("outcome", True), ("outcome", 1.0), ("outcome", 1),
    ("workflow_id", True), ("workflow_id", 1), ("current_step_id", True),
    ("current_step_index", True), ("current_step_index", 1.0),
    ("current_step_index", "1"), ("current_employee_id", True),
    ("failure_category", True), ("failure_category", 1.0),
])
def test_persisted_outcome_every_field_requires_exact_builtin_type(tmp_path: Path, failed: bool, field: str, value: object):
    result, definition, state, events = setup(tmp_path, failed=failed)
    bad = replace(result, **{field: value})
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(bad, definition, state, events)


def test_persisted_outcome_int_subclass_is_rejected(tmp_path: Path):
    result, definition, state, events = setup(tmp_path)
    bad = replace(result, current_step_index=IntSubclass(1))
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(bad, definition, state, events)


def test_final_success_route_returns_exact_completion(tmp_path: Path):
    result, definition, state, events = setup(tmp_path, single=True)
    expected = complete()
    assert route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase87_function=lambda *_: expected) is expected


@pytest.mark.parametrize("field,value", [
    ("decision", True), ("decision", 1), ("workflow_id", True),
    ("current_step_id", True), ("current_step_index", True),
    ("current_step_index", 1.0), ("current_employee_id", True),
    ("next_step_id", None), ("next_step_index", None),
    ("next_employee_id", None), ("reason", True),
])
def test_prepare_next_step_every_field_and_type_is_validated(tmp_path: Path, field: str, value: object):
    result, definition, state, events = setup(tmp_path)
    bad = replace(prepare(), **{field: value})
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase87_function=lambda *_: bad)
    assert caught.value.detail.classification == "progression_contract"


@pytest.mark.parametrize("field,value", [
    ("decision", True), ("workflow_id", True), ("current_step_id", True),
    ("current_step_index", True), ("current_step_index", 1.0),
    ("current_employee_id", True), ("next_step_id", "next"),
    ("next_step_index", 2), ("next_employee_id", "b"), ("reason", True),
])
def test_workflow_complete_dependency_decision_every_field_and_type_is_validated(tmp_path: Path, field: str, value: object):
    result, definition, state, events = setup(tmp_path, single=True)
    bad = replace(complete(), **{field: value})
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase87_function=lambda *_: bad)
    assert caught.value.detail.classification == "progression_contract"


@pytest.mark.parametrize("event_bytes", [
    lambda first, terminal: first + first + terminal,
    lambda first, terminal: terminal,
    lambda first, terminal: terminal + first,
    lambda first, terminal: first + terminal + first,
])
def test_multiple_step_duplicate_missing_reordered_unrelated_history_is_rejected(tmp_path: Path, event_bytes):
    result, definition, state, events, first, terminal = setup_two_step_terminal(tmp_path)
    events.write_bytes(event_bytes(first, terminal))
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events)
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("completed", [(), ("two",), ("one", "one"), ("two", "one")])
def test_completion_prefix_must_match_terminal_event(tmp_path: Path, completed: tuple[str, ...]):
    result, definition, state, events, _, _ = setup_two_step_terminal(tmp_path)
    rewrite(state, "completed_step_ids", list(completed))
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events)
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("value,failed", [(complete(), False), (PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", "api_error"), True)])
def test_stop_routes_are_identity_preserving_zero_call(tmp_path: Path, value: object, failed: bool):
    _, definition, state, events = setup(tmp_path, single=not failed, failed=failed)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0
    def fake(*_):
        nonlocal calls
        calls += 1
    assert route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(value, definition, state, events, phase87_function=fake) is value
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("field,value", [("outcome", "persisted_failure"), ("workflow_id", "other"), ("current_step_id", "other"), ("current_step_index", True), ("current_step_index", 2), ("current_employee_id", "other"), ("failure_category", "api_error")])
def test_success_outcome_all_fields_are_exact_and_mismatch_rejected(tmp_path: Path, field: str, value: object):
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(replace(result, **{field: value}), definition, state, events)
    assert caught.value.detail.classification in {"success_contract", "failure_contract"}


@pytest.mark.parametrize("bad", [OutcomeSubclass("persisted_success", "w", "one", 1, "a", None), SimpleNamespace(outcome="persisted_success"), object()])
def test_success_subclass_and_substitute_rejected_before_dependency(tmp_path: Path, bad: object):
    _, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(bad, definition, state, events)
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize("field,value", [("decision", "stopped_failed"), ("workflow_id", "other"), ("current_step_id", "other"), ("current_step_index", True), ("current_employee_id", "other"), ("next_step_id", "wrong"), ("next_step_index", True), ("next_employee_id", "wrong"), ("reason", "wrong")])
def test_malformed_dependency_prepare_decision_fields_rejected_once(tmp_path: Path, field: str, value: object):
    result, definition, state, events = setup(tmp_path)
    calls = 0
    def fake(*_):
        nonlocal calls
        calls += 1
        return replace(prepare(), **{field: value})
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase87_function=fake)
    assert caught.value.detail.classification == "progression_contract" and calls == 1


@pytest.mark.parametrize("field,value", [("decision", "prepare_next_step"), ("workflow_id", "other"), ("current_step_id", "other"), ("current_step_index", True), ("current_employee_id", "other"), ("next_step_id", "next"), ("next_step_index", 2), ("next_employee_id", "b"), ("reason", "other")])
def test_malformed_completion_stop_fields_rejected_zero_call(tmp_path: Path, field: str, value: object):
    _, definition, state, events = setup(tmp_path, single=True)
    calls = []
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(replace(complete(), **{field: value}), definition, state, events, phase87_function=lambda *args: calls.append(args))
    assert caught.value.detail.classification == "completion_contract" and calls == []


@pytest.mark.parametrize("field,value", [("outcome", "persisted_success"), ("workflow_id", "other"), ("current_step_id", "other"), ("current_step_index", True), ("current_employee_id", "other"), ("failure_category", None), ("failure_category", "wrong")])
def test_malformed_failure_stop_fields_rejected_zero_call(tmp_path: Path, field: str, value: object):
    result, definition, state, events = setup(tmp_path, failed=True)
    calls = []
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(replace(result, **{field: value}), definition, state, events, phase87_function=lambda *args: calls.append(args))
    assert caught.value.detail.classification in {"success_contract", "failure_contract"} and calls == []


@pytest.mark.parametrize("field,value", [("workflow_id", "other"), ("status", "failed"), ("current_step_id", "other"), ("current_step_index", 2), ("current_employee_id", "other"), ("completed_step_ids", []), ("last_failure_category", "api_error")])
def test_terminal_state_mismatch_rejected(tmp_path: Path, field: str, value: object):
    result, definition, state, events = setup(tmp_path)
    rewrite(state, field, value)
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events)
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("field,value", [("event_type", "step_failed"), ("workflow_id", "other"), ("step_id", "other"), ("step_index", 2), ("employee_id", "other"), ("previous_status", "ready"), ("next_status", "failed"), ("failure_category", "api_error"), ("response_id", None), ("output_text", None)])
def test_terminal_event_mismatch_rejected(tmp_path: Path, field: str, value: object):
    result, definition, state, events = setup(tmp_path)
    rewrite(events, field, value)
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events)
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("bad", [WorkflowProgressionDecision("stopped_failed", "w", "one", 1, "a", None, None, None, "latest_step_failed"), DecisionSubclass("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available"), SimpleNamespace(decision="prepare_next_step")])
def test_unsupported_subclass_and_substitute_decisions_rejected_once(tmp_path: Path, bad: object):
    result, definition, state, events = setup(tmp_path)
    calls = 0
    def fake(*_):
        nonlocal calls
        calls += 1
        return bad
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase87_function=fake)
    assert calls == 1


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_dependency_error_mutation_compensation_and_identity(tmp_path: Path, kind: str, mutation: str):
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    safe = ClassifiedOutcomeRoutingPhaseBridgeContinuationError("safe")
    def fake(*_):
        if mutation in ("state", "both"): state.write_bytes(b"changed-state")
        if mutation in ("events", "both"): events.write_bytes(b"changed-events")
        if kind == "safe": raise safe
        raise RuntimeError("secret")
    expected = ClassifiedOutcomeRoutingPhaseBridgeContinuationError if kind == "safe" else ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError
    with pytest.raises(expected) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase87_function=fake)
    if kind == "safe": assert caught.value is safe
    else: assert caught.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_each_dependency_error_path_calls_phase87_once_without_retry(tmp_path: Path, kind: str, mutation: str):
    result, definition, state, events = setup(tmp_path)
    calls = 0
    safe = ClassifiedOutcomeRoutingPhaseBridgeContinuationError("safe")
    def fake(*_):
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"): state.write_bytes(b"state-change")
        if mutation in ("events", "both"): events.write_bytes(b"events-change")
        if kind == "safe": raise safe
        raise RuntimeError("secret")
    with pytest.raises((ClassifiedOutcomeRoutingPhaseBridgeContinuationError, ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError)):
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase87_function=fake)
    assert calls == 1


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_return_compensation_no_retry(tmp_path: Path, mutation: str):
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0
    def fake(*_):
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"): state.write_bytes(b"changed-state")
        if mutation in ("events", "both"): events.write_bytes(b"changed-events")
        return object()
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError):
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase87_function=fake)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failure_target", ["state", "events", "both"])
def test_rollback_failures_attempt_both_targets_once_without_second_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_target: str):
    result, definition, state, events = setup(tmp_path)
    original = Path.write_bytes
    attempts = []
    def write(path: Path, data: bytes) -> int:
        attempts.append(path)
        if failure_target in ("state", "both") and path == state: raise OSError("state rollback")
        if failure_target in ("events", "both") and path == events: raise OSError("events rollback")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", write)
    def fake(*_):
        original(state, b"changed-state"); original(events, b"changed-events"); return object()
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events, phase87_function=fake)
    assert caught.value.detail.classification == "dependency_rollback" and attempts == [state, events]


@pytest.mark.parametrize("which,method", [("state", "is_file"), ("events", "is_file"), ("state", "read_bytes"), ("events", "read_bytes")])
def test_target_oserror_is_classified_separately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, method: str):
    result, definition, state, events = setup(tmp_path)
    target = state if which == "state" else events
    original = getattr(Path, method)
    def fail(path: Path):
        if path == target: raise OSError("unreadable")
        return original(path)
    monkeypatch.setattr(Path, method, fail)
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events)
    assert caught.value.detail.classification in {"state_target", "event_target", "terminal_contract"}


@pytest.mark.parametrize("which", ["state", "events"])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_directory_targets_are_classified_separately(tmp_path: Path, which: str, kind: str):
    result, definition, state, events = setup(tmp_path)
    target = state if which == "state" else events
    target.unlink()
    if kind == "directory":
        target.mkdir()
    with pytest.raises(ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation(result, definition, state, events)
    assert caught.value.detail.classification == ("state_target" if which == "state" else "event_target")
