"""Focused tests for the Phase 101 cycle-closure continuation boundary."""

# ruff: noqa: E501,E701,E702

import inspect
import json
from dataclasses import replace
from pathlib import Path
from pathlib import Path as _TestPath
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedOutcomeCycleClosureContinuationCompatibilityError,
    ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_outcome_cycle_closure_continuation_boundary,
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


def prepare() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available")


def complete(current_step_id: str = "one", current_step_index: int = 1, current_employee_id: str = "a") -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "w", current_step_id, current_step_index, current_employee_id, None, None, None, "last_step_succeeded")


def rewrite(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n")


def test_public_signature_default_identity_and_canonical_order(tmp_path: Path) -> None:
    signature = inspect.signature(route_classified_outcome_cycle_closure_continuation_boundary)
    parameters = list(signature.parameters.values())
    assert [p.name for p in parameters[:4]] == ["result", "workflow", "state_path", "events_path"]
    assert [p.kind for p in parameters[:4]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 4
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[4].name == "phase94_function"
    assert parameters[4].default is route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation

    result, definition, state, events = setup(tmp_path)
    expected = prepare()
    calls: list[tuple[object, ...]] = []
    returned = route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events, phase94_function=lambda *args: calls.append(args) or expected)
    assert returned is expected
    assert calls == [(result, definition, state, events)]
    assert all(calls[0][index] is value for index, value in enumerate((result, definition, state, events)))


def test_implementation_does_not_reference_phase_private_internals() -> None:
    source = _TestPath("src/ai_office/engine/classified_outcome_cycle_closure_continuation_boundary.py").read_text()
    assert "_phase94" not in source
    assert "_phase87" not in source
    assert ". _" not in source
    assert "._inputs" not in source
    assert "._success" not in source
    assert "._terminal" not in source
    assert "._progression" not in source
    assert "route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation" in source


def test_valid_intermediate_and_final_success_routes_return_exact_dependency_object(tmp_path: Path) -> None:
    for subdir, single, expected in (("intermediate", False, prepare()), ("final", True, complete())):
        result, definition, state, events = setup(tmp_path / subdir, single=single)
        state.parent.mkdir(exist_ok=True)
        calls = 0
        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return expected
        assert route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events, phase94_function=fake) is expected
        assert calls == 1


@pytest.mark.parametrize("bad", [OutcomeSubclass("persisted_success", "w", "one", 1, "a", None), SimpleNamespace(outcome="persisted_success"), object()])
def test_outcome_subclass_and_substitute_rejected_before_phase94(tmp_path: Path, bad: object) -> None:
    _, definition, state, events = setup(tmp_path)
    calls = 0
    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(bad, definition, state, events, phase94_function=fake)
    assert caught.value.detail.classification == "result_type"
    assert calls == 0


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("field,value", [
    ("outcome", True), ("outcome", 1), ("workflow_id", True), ("current_step_id", True),
    ("current_step_index", True), ("current_step_index", 1.0), ("current_employee_id", True),
    ("failure_category", True), ("failure_category", 1.0),
])
def test_outcome_every_field_requires_exact_builtin_type(tmp_path: Path, failed: bool, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path, failed=failed)
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError):
        route_classified_outcome_cycle_closure_continuation_boundary(replace(result, **{field: value}), definition, state, events)


def test_outcome_int_subclass_is_rejected(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(replace(result, current_step_index=IntSubclass(1)), definition, state, events)
    assert caught.value.detail.classification == "success_contract"


@pytest.mark.parametrize("bad_workflow", [WorkflowSubclass.model_validate({"id": "w", "name": "W", "description": "D", "steps": [{"id": "one", "name": "One", "employee": "a", "instructions": "x"}]}), SimpleNamespace(id="w")])
def test_workflow_requires_exact_model_and_exact_step_models(tmp_path: Path, bad_workflow: object) -> None:
    result, _, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(result, bad_workflow, state, events)
    assert caught.value.detail.classification == "workflow_definition"


def test_paths_require_exact_type_conflict_existing_regular_targets(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    for supplied_state, supplied_events, classification in [
        (PathSubclass(state), events, "state_target"),
        (state, PathSubclass(events), "event_target"),
        (state, state, "target_conflict"),
        (state.with_name("missing.json"), events, "state_target"),
        (state, events.with_name("missing.jsonl"), "event_target"),
    ]:
        with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
            route_classified_outcome_cycle_closure_continuation_boundary(result, definition, supplied_state, supplied_events)
        assert caught.value.detail.classification == classification


@pytest.mark.parametrize("event_bytes", [
    lambda first, terminal: first + first + terminal,
    lambda first, terminal: terminal,
    lambda first, terminal: terminal + first,
    lambda first, terminal: first + terminal + first,
])
def test_duplicate_missing_reordered_unrelated_history_is_rejected(tmp_path: Path, event_bytes) -> None:
    result, definition, state, events, first, terminal = setup_two_step_terminal(tmp_path)
    events.write_bytes(event_bytes(first, terminal))
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events)
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("field,value", [("workflow_id", "other"), ("status", "failed"), ("current_step_id", "other"), ("current_step_index", 2), ("current_employee_id", "other"), ("completed_step_ids", []), ("last_failure_category", "api_error")])
def test_terminal_state_mismatch_rejected(tmp_path: Path, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path)
    rewrite(state, field, value)
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events)
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("field,value", [("event_type", "step_failed"), ("workflow_id", "other"), ("step_id", "other"), ("step_index", 2), ("employee_id", "other"), ("previous_status", "ready"), ("next_status", "failed"), ("failure_category", "api_error"), ("response_id", None), ("output_text", None)])
def test_terminal_event_mismatch_rejected(tmp_path: Path, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path)
    rewrite(events, field, value)
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events)
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize("field,value", [("decision", True), ("workflow_id", True), ("current_step_id", True), ("current_step_index", True), ("current_step_index", 1.0), ("current_employee_id", True), ("next_step_id", None), ("next_step_index", None), ("next_employee_id", None), ("reason", True)])
def test_prepare_next_step_mapping_fields_are_validated(tmp_path: Path, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events, phase94_function=lambda *_: replace(prepare(), **{field: value}))
    assert caught.value.detail.classification == "progression_contract"


@pytest.mark.parametrize("field,value", [("decision", True), ("workflow_id", True), ("current_step_id", True), ("current_step_index", True), ("current_step_index", 1.0), ("current_employee_id", True), ("next_step_id", "next"), ("next_step_index", 2), ("next_employee_id", "b"), ("reason", True)])
def test_workflow_complete_mapping_fields_are_validated(tmp_path: Path, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path, single=True)
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events, phase94_function=lambda *_: replace(complete(), **{field: value}))
    assert caught.value.detail.classification == "progression_contract"


@pytest.mark.parametrize("value,failed", [(complete(), False), (PersistedExecutionOutcome("persisted_failure", "w", "one", 1, "a", "api_error"), True)])
def test_stop_routes_are_identity_preserving_zero_call_and_read_only(tmp_path: Path, value: object, failed: bool) -> None:
    _, definition, state, events = setup(tmp_path, single=not failed, failed=failed)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0
    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
    assert route_classified_outcome_cycle_closure_continuation_boundary(value, definition, state, events, phase94_function=fake) is value
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("bad", [
    WorkflowProgressionDecision("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available"),
    WorkflowProgressionDecision("stopped_failed", "w", "one", 1, "a", None, None, None, "latest_step_failed"),
    DecisionSubclass("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded"),
    SimpleNamespace(decision="workflow_complete"),
])
def test_unsupported_direct_progression_subclass_and_substitute_rejected_zero_call(tmp_path: Path, bad: object) -> None:
    _, definition, state, events = setup(tmp_path)
    calls = 0
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError):
        route_classified_outcome_cycle_closure_continuation_boundary(bad, definition, state, events, phase94_function=lambda *args: calls + 1)
    assert calls == 0


@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_dependency_errors_and_malformed_returns_compensate_without_retry(tmp_path: Path, kind: str, mutation: str) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0
    safe = ClassifiedOutcomeDispatchPhaseBridgeCycleReentryContinuationError("safe")
    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"): state.write_bytes(b"changed-state")
        if mutation in ("events", "both"): events.write_bytes(b"changed-events")
        if kind == "safe": raise safe
        if kind == "unexpected": raise RuntimeError("secret")
        return object()
    with pytest.raises(Exception) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events, phase94_function=fake)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before
    if kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification in {"dependency_error", "progression_contract"}


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_decision_after_dependency_mutation_restores_both_targets_and_raises_progression_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    result, definition, state, events = setup(tmp_path)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    restored: set[str] = set()
    original_write = Path.write_bytes
    def write(path: Path, data: bytes) -> int:
        if path == state and data == before_state: restored.add("state")
        if path == events and data == before_events: restored.add("events")
        return original_write(path, data)
    def fake(*_: object) -> object:
        if mutation in ("state", "both"): state.write_bytes(b"changed-state")
        if mutation in ("events", "both"): events.write_bytes(b"changed-events")
        return prepare()
    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events, phase94_function=fake)
    assert caught.value.detail.classification == "progression_contract"
    assert restored == {"state", "events"}
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("failure_target", ["state", "events", "both"])
def test_rollback_failures_attempt_both_targets_once_without_second_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_target: str) -> None:
    result, definition, state, events = setup(tmp_path)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    original_write = Path.write_bytes
    attempts: list[Path] = []
    def write(path: Path, data: bytes) -> int:
        if data in (before_state, before_events):
            attempts.append(path)
            if failure_target in ("state", "both") and path == state: raise OSError("state rollback")
            if failure_target in ("events", "both") and path == events: raise OSError("events rollback")
        return original_write(path, data)
    def fake(*_: object) -> object:
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return object()
    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events, phase94_function=fake)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts == [state, events]


@pytest.mark.parametrize("which,method", [("state", "is_file"), ("events", "is_file"), ("state", "read_bytes"), ("events", "read_bytes")])
def test_target_oserror_is_classified_separately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, method: str) -> None:
    result, definition, state, events = setup(tmp_path)
    target = state if which == "state" else events
    original = getattr(Path, method)
    def fail(path: Path):
        if path == target: raise OSError("unreadable")
        return original(path)
    monkeypatch.setattr(Path, method, fail)
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError) as caught:
        route_classified_outcome_cycle_closure_continuation_boundary(result, definition, state, events)
    assert caught.value.detail.classification == ("state_target" if which == "state" else "event_target")
