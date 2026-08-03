"""Focused tests for the Phase 108 classified persisted-outcome progression boundary."""

# ruff: noqa: E501,E701,E702

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedOutcomeCycleClosureContinuationCompatibilityError,
    ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_outcome_cycle_closure_continuation_boundary,
    route_classified_persisted_outcome_progression_cycle_continuation_boundary,
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


def setup(tmp_path: Path, *, single: bool = False, failed: bool = False, complete: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = workflow(single)
    index = len(definition.steps) if single or complete else 1
    step = definition.steps[index - 1]
    if complete:
        status = "succeeded"
        state_model = WorkflowExecutionState("w", status, step.id, index, step.employee, tuple(item.id for item in definition.steps), None)
        event = RuntimeStepEvent("step_succeeded", "w", step.id, index, step.employee, "running", status, "openai", None, "response", "request", "output", None)
        result = WorkflowProgressionDecision("workflow_complete", "w", step.id, index, step.employee, None, None, None, "last_step_succeeded")
    else:
        status = "failed" if failed else "succeeded"
        state_model = WorkflowExecutionState("w", status, step.id, index, step.employee, tuple(item.id for item in definition.steps[:index]) if not failed else (), "api_error" if failed else None)
        event = RuntimeStepEvent("step_failed" if failed else "step_succeeded", "w", step.id, index, step.employee, "running", status, "openai", "api_error" if failed else None, None if failed else "response", "request", None if failed else "output", "safe" if failed else None)
        result = PersistedExecutionOutcome("persisted_failure" if failed else "persisted_success", "w", step.id, index, step.employee, "api_error" if failed else None)
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state_model))
    events_path.write_text(serialize_runtime_step_event_jsonl(event))
    return result, definition, state_path, events_path


def expected_decision(final: bool = False) -> WorkflowProgressionDecision:
    if final:
        return WorkflowProgressionDecision("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded")
    return WorkflowProgressionDecision("prepare_next_step", "w", "one", 1, "a", "two", 2, "b", "next_step_available")


def classification(error: pytest.ExceptionInfo[ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError]) -> str:
    return error.value.detail.classification


def test_public_signature_and_default_identity() -> None:
    signature = inspect.signature(route_classified_persisted_outcome_progression_cycle_continuation_boundary)
    parameters = list(signature.parameters.values())
    assert [parameter.name for parameter in parameters] == ["result", "workflow", "state_path", "events_path", "phase101_function"]
    assert [parameter.kind for parameter in parameters[:4]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 4
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[4].default is route_classified_outcome_cycle_closure_continuation_boundary


@pytest.mark.parametrize("single", [False, True], ids=["intermediate", "final"])
def test_persisted_success_delegates_once_in_exact_four_argument_order_and_returns_identity(tmp_path: Path, single: bool) -> None:
    result, definition, state, events = setup(tmp_path, single=single)
    returned_decision = expected_decision(final=single)
    calls: list[tuple[object, ...]] = []
    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return returned_decision
    returned = route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, state, events, phase101_function=dependency)
    assert returned is returned_decision
    assert calls == [(result, definition, state, events)]


@pytest.mark.parametrize("route", ["persisted_failure", "workflow_complete"])
def test_stop_routes_return_exact_identity_without_dependency_call(tmp_path: Path, route: str) -> None:
    result, definition, state, events = setup(tmp_path, single=route == "workflow_complete", failed=route == "persisted_failure", complete=route == "workflow_complete")
    calls = 0
    def forbidden(*args: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError(args)
    returned = route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, state, events, phase101_function=forbidden)
    assert returned is result
    assert calls == 0


@pytest.mark.parametrize("result_factory", [lambda: object(), lambda: SimpleNamespace(outcome="persisted_success"), lambda: OutcomeSubclass("persisted_success", "w", "one", 1, "a", None), lambda: DecisionSubclass("workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded")], ids=["object", "substitute", "outcome-subclass", "decision-subclass"])
def test_exact_result_models_reject_subclasses_and_substitutes_without_call(tmp_path: Path, result_factory) -> None:
    _, definition, state, events = setup(tmp_path, single=True)
    calls = 0
    def forbidden(*args: object) -> object:
        nonlocal calls
        calls += 1
        return args[0]
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(result_factory(), definition, state, events, phase101_function=forbidden)
    assert classification(caught) == "result_type"
    assert calls == 0


@pytest.mark.parametrize("target,expected", [("state-subclass", "state_target"), ("event-subclass", "event_target"), ("same", "target_conflict"), ("missing-state", "state_target"), ("missing-events", "event_target")])
def test_exact_path_models_existing_files_and_distinct_targets_are_required(tmp_path: Path, target: str, expected: str) -> None:
    result, definition, state, events = setup(tmp_path)
    supplied_state: object = state
    supplied_events: object = events
    if target == "state-subclass": supplied_state = PathSubclass(state)
    if target == "event-subclass": supplied_events = PathSubclass(events)
    if target == "same": supplied_events = state
    if target == "missing-state": supplied_state = state.with_name("missing.json")
    if target == "missing-events": supplied_events = events.with_name("missing.jsonl")
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, supplied_state, supplied_events)
    assert classification(caught) == expected


@pytest.mark.parametrize("mode", ["missing", "duplicate", "reordered", "unrelated", "malformed"])
def test_terminal_history_missing_duplicate_reordered_unrelated_and_malformed_are_rejected(tmp_path: Path, mode: str) -> None:
    result, definition, state, events = setup(tmp_path)
    terminal = events.read_bytes()
    unrelated = serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "other", "one", 1, "a", "running", "succeeded", "openai", None, "response", "request", "output", None)).encode()
    replacements = {"missing": b"", "duplicate": terminal + terminal, "reordered": terminal + unrelated, "unrelated": unrelated, "malformed": b"{not-json}\n"}
    events.write_bytes(replacements[mode])
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, state, events)
    assert classification(caught) == "terminal_contract"


@pytest.mark.parametrize("model", ["workflow-subclass", "workflow-field", "step-model", "step-field"])
def test_workflow_and_step_models_and_builtin_field_types_are_exact(tmp_path: Path, model: str) -> None:
    result, definition, state, events = setup(tmp_path)
    supplied: object = definition
    if model == "workflow-subclass": supplied = WorkflowSubclass.model_validate(definition.model_dump())
    elif model == "workflow-field": object.__setattr__(definition, "name", SimpleNamespace())
    elif model == "step-model": definition.steps[0] = SimpleNamespace(id="one", name="One", employee="a", instructions="x")
    else: object.__setattr__(definition.steps[0], "employee", SimpleNamespace())
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, supplied, state, events)
    assert classification(caught) == "workflow_definition"


@pytest.mark.parametrize("field,value,expected", [("workflow_id", "other", "success_contract"), ("current_step_id", "two", "success_contract"), ("current_step_index", IntSubclass(1), "success_contract"), ("current_employee_id", "b", "success_contract"), ("failure_category", "api_error", "success_contract"), ("outcome", "persisted_failure", "failure_contract")])
def test_success_contract_revalidates_fields_and_exact_types(tmp_path: Path, field: str, value: object, expected: str) -> None:
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(replace(result, **{field: value}), definition, state, events)
    assert classification(caught) == expected


@pytest.mark.parametrize("field,value,expected", [("workflow_id", "other", "failure_contract"), ("current_step_id", "two", "failure_contract"), ("current_step_index", IntSubclass(1), "failure_contract"), ("current_employee_id", "b", "failure_contract"), ("failure_category", None, "failure_contract"), ("outcome", "persisted_success", "success_contract")])
def test_failure_contract_revalidates_fields_and_exact_types(tmp_path: Path, field: str, value: object, expected: str) -> None:
    result, definition, state, events = setup(tmp_path, failed=True)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(replace(result, **{field: value}), definition, state, events)
    assert classification(caught) == expected


@pytest.mark.parametrize("field,value", [("decision", "prepare_next_step"), ("workflow_id", "other"), ("current_step_id", "two"), ("current_step_index", IntSubclass(1)), ("current_employee_id", "b"), ("next_step_id", "two"), ("next_step_index", 2), ("next_employee_id", "b"), ("reason", "next_step_available")])
def test_completion_contract_revalidates_all_fields_and_exact_types(tmp_path: Path, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path, single=True, complete=True)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(replace(result, **{field: value}), definition, state, events)
    assert classification(caught) == "completion_contract"


@pytest.mark.parametrize("final", [False, True], ids=["prepare-next-step", "workflow-complete"])
def test_dependency_decision_accepts_exact_prepare_next_step_and_workflow_complete_fields(tmp_path: Path, final: bool) -> None:
    result, definition, state, events = setup(tmp_path, single=final)
    decision = expected_decision(final)
    assert route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, state, events, phase101_function=lambda *args: decision) is decision


@pytest.mark.parametrize("field,value", [("decision", "workflow_complete"), ("workflow_id", "other"), ("current_step_id", "other"), ("current_step_index", IntSubclass(1)), ("current_employee_id", "other"), ("next_step_id", "wrong"), ("next_step_index", IntSubclass(2)), ("next_employee_id", "wrong"), ("reason", "last_step_succeeded")])
def test_prepare_next_step_dependency_decision_revalidates_all_fields(tmp_path: Path, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, state, events, phase101_function=lambda *args: replace(expected_decision(), **{field: value}))
    assert classification(caught) == "progression_contract"


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_safe_dependency_error_restores_mutations_and_is_not_retried(tmp_path: Path, mutation: str) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0
    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}: state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}: events.write_bytes(b"changed-events")
        raise ClassifiedOutcomeCycleClosureContinuationCompatibilityError("result_type")
    with pytest.raises(ClassifiedOutcomeCycleClosureContinuationCompatibilityError):
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, state, events, phase101_function=dependency)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["unexpected", "malformed-return", "valid-return"])
@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_unexpected_malformed_and_valid_dependency_mutations_restore_and_do_not_retry(tmp_path: Path, kind: str, mutation: str) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0
    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}: state.write_bytes(b"changed-state")
        if mutation in {"events", "both"}: events.write_bytes(b"changed-events")
        if kind == "unexpected": raise RuntimeError("secret")
        if kind == "malformed-return": return object()
        return expected_decision()
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, state, events, phase101_function=dependency)
    assert classification(caught) == ("dependency_error" if kind == "unexpected" else "progression_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before
    assert "secret" not in str(caught.value)


def test_malformed_dependency_return_without_mutation_is_progression_contract(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, state, events, phase101_function=lambda *args: object())
    assert classification(caught) == "progression_contract"


@pytest.mark.parametrize("target,operation", [("state", "is_file"), ("events", "is_file"), ("state", "read_bytes"), ("events", "read_bytes")])
def test_target_oserror_is_safely_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, operation: str) -> None:
    result, definition, state, events = setup(tmp_path)
    target_path = state if target == "state" else events
    path_type = type(Path())
    original = getattr(path_type, operation)
    def fail_selected(self, *args, **kwargs):
        if self == target_path: raise OSError("secret")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(path_type, operation, fail_selected)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, state, events)
    assert classification(caught) == ("state_target" if target == "state" else "event_target")
    assert "secret" not in str(caught.value)


def test_rollback_failure_attempts_both_target_restores_once_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, definition, state, events = setup(tmp_path)
    original_state, original_events = state.read_bytes(), events.read_bytes()
    calls = 0
    writes: list[Path] = []
    path_type = type(Path())
    original_write = path_type.write_bytes
    def dependency(*args: object) -> WorkflowProgressionDecision:
        nonlocal calls
        calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        return expected_decision()
    def failing_restore(self: Path, data: bytes) -> int:
        writes.append(self)
        if data in {original_state, original_events}: raise OSError("restore-secret")
        return original_write(self, data)
    monkeypatch.setattr(path_type, "write_bytes", failing_restore)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_continuation_boundary(result, definition, state, events, phase101_function=dependency)
    assert classification(caught) == "dependency_rollback"
    assert calls == 1
    assert writes.count(state) == 1
    assert writes.count(events) == 1
    assert "restore-secret" not in str(caught.value)
