"""Focused tests for the Phase 122 classified persisted-outcome progression reentry boundary."""

# ruff: noqa: E501,E701,E702

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError,
    ClassifiedPersistedOutcomeProgressionCycleReentryContinuationCompatibilityError,
    ClassifiedPersistedOutcomeProgressionCycleReentryContinuationError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary,
    route_classified_persisted_outcome_progression_cycle_reentry_continuation_boundary,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def test_implementation_uses_only_public_phase115_dependency() -> None:
    source = Path(
        "src/ai_office/engine/"
        "classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary.py"
    ).read_text()

    assert (
        "route_classified_persisted_outcome_progression_cycle_reentry_continuation_boundary"
        in source
    )
    assert "phase108" not in source.lower()
    assert "classified_persisted_outcome_progression_cycle_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source

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
        steps.append({"id": "three", "name": "Three", "employee": "c", "instructions": "z"})
    return WorkflowDefinition.model_validate({"id": "w", "name": "W", "description": "D", "steps": steps})


def setup(tmp_path: Path, *, single: bool = False, failed: bool = False, complete: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    # A persisted-success handoff is a continuation route, so its valid
    # fixture must always have a predecessor step (index >= 2).  Completion
    # stop fixtures may still use a one-step workflow.
    definition = workflow(single if complete else False)
    if single and not complete:
        definition.steps = definition.steps[:2]
    index = len(definition.steps) if single or complete else (1 if failed else 2)
    step = definition.steps[index - 1]
    prior_events = []
    if not failed and not complete and index >= 2:
        prior_events = [
            RuntimeStepEvent(
                "step_succeeded", "w", item.id, item_index, item.employee,
                "running", "succeeded", "openai", None, "response", "request",
                "output", None,
            )
            for item_index, item in enumerate(definition.steps[: index - 1], 1)
        ]
    if complete:
        status = "succeeded"
        state_model = WorkflowExecutionState("w", status, step.id, index, step.employee, tuple(item.id for item in definition.steps), None)
        event = RuntimeStepEvent("step_succeeded", "w", step.id, index, step.employee, "running", status, "openai", None, "response", "request", "output", None)
        prior_events = [
            RuntimeStepEvent("step_succeeded", "w", item.id, item_index, item.employee, "running", "succeeded", "openai", None, "response", "request", "output", None)
            for item_index, item in enumerate(definition.steps[: index - 1], 1)
        ]
        result = WorkflowProgressionDecision("workflow_complete", "w", step.id, index, step.employee, None, None, None, "last_step_succeeded")
    else:
        status = "failed" if failed else "succeeded"
        state_model = WorkflowExecutionState("w", status, step.id, index, step.employee, tuple(item.id for item in definition.steps[:index]) if not failed else (), "api_error" if failed else None)
        event = RuntimeStepEvent("step_failed" if failed else "step_succeeded", "w", step.id, index, step.employee, "running", status, "openai", "api_error" if failed else None, None if failed else "response", "request", None if failed else "output", "safe" if failed else None)
        result = PersistedExecutionOutcome("persisted_failure" if failed else "persisted_success", "w", step.id, index, step.employee, "api_error" if failed else None)
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state_model))
    events_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in [*prior_events, event]))
    return result, definition, state_path, events_path


def expected_decision(final: bool = False) -> WorkflowProgressionDecision:
    if final:
        return WorkflowProgressionDecision("workflow_complete", "w", "two", 2, "b", None, None, None, "last_step_succeeded")
    return WorkflowProgressionDecision("prepare_next_step", "w", "two", 2, "b", "three", 3, "c", "next_step_available")


def classification(error: pytest.ExceptionInfo[ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError]) -> str:
    return error.value.detail.classification


def test_public_signature_and_default_identity() -> None:
    signature = inspect.signature(route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary)
    parameters = list(signature.parameters.values())
    assert [parameter.name for parameter in parameters] == ["result", "workflow", "state_path", "events_path", "phase115_function"]
    assert all(parameter.annotation is object for parameter in parameters[:4])
    assert [parameter.kind for parameter in parameters[:4]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 4
    assert parameters[4].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[4].default is route_classified_persisted_outcome_progression_cycle_reentry_continuation_boundary


def test_persisted_success_continuation_index_one_is_rejected_before_phase115(tmp_path: Path) -> None:
    _, definition, state, events = setup(tmp_path, single=True, complete=True)
    step = definition.steps[0]
    result = PersistedExecutionOutcome(
        "persisted_success", "w", step.id, 1, step.employee, None
    )
    expected = WorkflowProgressionDecision(
        "workflow_complete", "w", step.id, 1, step.employee, None, None, None,
        "last_step_succeeded"
    )
    before_state, before_events = state.read_bytes(), events.read_bytes()
    returned = route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
        result, definition, state, events
    )
    assert type(returned) is WorkflowProgressionDecision
    assert returned == expected
    assert returned is not result
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)

    failure_path = tmp_path / "failure"
    failure_path.mkdir(parents=True, exist_ok=True)
    failed_result, failed_definition, failed_state, failed_events = setup(
        failure_path, failed=True
    )
    failed_before = failed_state.read_bytes(), failed_events.read_bytes()
    failure_calls: list[tuple[object, ...]] = []

    def forbidden_failure(*args: object) -> object:
        failure_calls.append(args)
        raise AssertionError(args)

    returned_failure = route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
        failed_result,
        failed_definition,
        failed_state,
        failed_events,
        phase115_function=forbidden_failure,
    )
    assert returned_failure is failed_result
    assert returned_failure.failure_category == "api_error"  # type: ignore[union-attr]
    assert failure_calls == []
    assert (failed_state.read_bytes(), failed_events.read_bytes()) == failed_before

    _, complete_definition, complete_state, complete_events = setup(
        tmp_path / "complete"
    )
    complete_definition.steps = complete_definition.steps[:2]
    complete = WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        complete_definition.steps[1].id,
        2,
        complete_definition.steps[1].employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )
    complete_before = complete_state.read_bytes(), complete_events.read_bytes()
    complete_calls: list[tuple[object, ...]] = []

    def forbidden_complete(*args: object) -> object:
        complete_calls.append(args)
        raise AssertionError(args)

    returned_complete = route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
        complete, complete_definition, complete_state, complete_events, phase115_function=forbidden_complete
    )
    assert returned_complete is complete
    assert complete_calls == []
    assert (complete_state.read_bytes(), complete_events.read_bytes()) == complete_before


@pytest.mark.parametrize("single", [False, True], ids=["intermediate", "final"])
def test_persisted_success_delegates_once_in_exact_four_argument_order_and_returns_identity(tmp_path: Path, single: bool) -> None:
    result, definition, state, events = setup(tmp_path, single=single)
    returned_decision = expected_decision(final=single)
    calls: list[tuple[object, ...]] = []
    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return returned_decision
    returned = route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, definition, state, events, phase115_function=dependency)
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
    returned = route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, definition, state, events, phase115_function=forbidden)
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
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result_factory(), definition, state, events, phase115_function=forbidden)
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
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, definition, supplied_state, supplied_events)
    assert classification(caught) == expected


@pytest.mark.parametrize("mode", ["missing", "duplicate", "reordered", "unrelated", "malformed"])
def test_terminal_history_missing_duplicate_reordered_unrelated_and_malformed_are_rejected(tmp_path: Path, mode: str) -> None:
    result, definition, state, events = setup(tmp_path)
    terminal = events.read_bytes()
    unrelated = serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "other", "one", 1, "a", "running", "succeeded", "openai", None, "response", "request", "output", None)).encode()
    replacements = {"missing": b"", "duplicate": terminal + terminal, "reordered": terminal + unrelated, "unrelated": unrelated, "malformed": b"{not-json}\n"}
    events.write_bytes(replacements[mode])
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, definition, state, events)
    assert classification(caught) == "terminal_contract"


@pytest.mark.parametrize("model", ["workflow-subclass", "workflow-field", "step-model", "step-field"])
def test_workflow_and_step_models_and_builtin_field_types_are_exact(tmp_path: Path, model: str) -> None:
    result, definition, state, events = setup(tmp_path)
    supplied: object = definition
    if model == "workflow-subclass": supplied = WorkflowSubclass.model_validate(definition.model_dump())
    elif model == "workflow-field": object.__setattr__(definition, "name", SimpleNamespace())
    elif model == "step-model": definition.steps[0] = SimpleNamespace(id="one", name="One", employee="a", instructions="x")
    else: object.__setattr__(definition.steps[0], "employee", SimpleNamespace())
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, supplied, state, events)
    assert classification(caught) == "workflow_definition"


@pytest.mark.parametrize("field,value,expected", [("workflow_id", "other", "success_contract"), ("current_step_id", "other", "success_contract"), ("current_step_index", IntSubclass(1), "success_contract"), ("current_employee_id", "other", "success_contract"), ("failure_category", "api_error", "success_contract"), ("outcome", "persisted_failure", "failure_contract")])
def test_success_contract_revalidates_fields_and_exact_types(tmp_path: Path, field: str, value: object, expected: str) -> None:
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(replace(result, **{field: value}), definition, state, events)
    assert classification(caught) == expected


@pytest.mark.parametrize("field,value,expected", [("workflow_id", "other", "failure_contract"), ("current_step_id", "two", "failure_contract"), ("current_step_index", IntSubclass(1), "failure_contract"), ("current_employee_id", "b", "failure_contract"), ("failure_category", None, "failure_contract"), ("outcome", "persisted_success", "success_contract")])
def test_failure_contract_revalidates_fields_and_exact_types(tmp_path: Path, field: str, value: object, expected: str) -> None:
    result, definition, state, events = setup(tmp_path, failed=True)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(replace(result, **{field: value}), definition, state, events)
    assert classification(caught) == expected


@pytest.mark.parametrize("field,value", [("decision", "prepare_next_step"), ("workflow_id", "other"), ("current_step_id", "two"), ("current_step_index", IntSubclass(1)), ("current_employee_id", "b"), ("next_step_id", "two"), ("next_step_index", 2), ("next_employee_id", "b"), ("reason", "next_step_available")])
def test_completion_contract_revalidates_all_fields_and_exact_types(tmp_path: Path, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path, single=True, complete=True)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(replace(result, **{field: value}), definition, state, events)
    assert classification(caught) == "completion_contract"


@pytest.mark.parametrize("final", [False, True], ids=["prepare-next-step", "workflow-complete"])
def test_dependency_decision_accepts_exact_prepare_next_step_and_workflow_complete_fields(tmp_path: Path, final: bool) -> None:
    result, definition, state, events = setup(tmp_path, single=final)
    decision = expected_decision(final)
    assert route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, definition, state, events, phase115_function=lambda *args: decision) is decision


@pytest.mark.parametrize("field,value", [("decision", "workflow_complete"), ("workflow_id", "other"), ("current_step_id", "other"), ("current_step_index", IntSubclass(1)), ("current_employee_id", "other"), ("next_step_id", "wrong"), ("next_step_index", IntSubclass(2)), ("next_employee_id", "wrong"), ("reason", "last_step_succeeded")])
def test_prepare_next_step_dependency_decision_revalidates_all_fields(tmp_path: Path, field: str, value: object) -> None:
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, definition, state, events, phase115_function=lambda *args: replace(expected_decision(), **{field: value}))
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
        raise ClassifiedPersistedOutcomeProgressionCycleReentryContinuationCompatibilityError("result_type")
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleReentryContinuationError):
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, definition, state, events, phase115_function=dependency)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_safe_dependency_error_without_mutation_preserves_identity_and_is_not_retried(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0
    safe_error = ClassifiedPersistedOutcomeProgressionCycleReentryContinuationCompatibilityError(
        "result_type"
    )

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        raise safe_error

    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleReentryContinuationError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
            result, definition, state, events, phase115_function=dependency
        )
    assert caught.value is safe_error
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
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, definition, state, events, phase115_function=dependency)
    assert classification(caught) == ("dependency_error" if kind == "unexpected" else "progression_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before
    assert "secret" not in str(caught.value)


def test_unexpected_dependency_error_without_mutation_is_sanitized_and_not_retried(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    before = (state.read_bytes(), events.read_bytes())
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("secret")

    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
            result, definition, state, events, phase115_function=dependency
        )
    assert classification(caught) == "dependency_error"
    assert "secret" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


def test_malformed_dependency_return_without_mutation_is_progression_contract(tmp_path: Path) -> None:
    result, definition, state, events = setup(tmp_path)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, definition, state, events, phase115_function=lambda *args: object())
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
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(result, definition, state, events)
    assert classification(caught) == ("state_target" if target == "state" else "event_target")
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_target_restores_once_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_target: str
) -> None:
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
        if (self == state and failed_target in {"state", "both"}) or (
            self == events and failed_target in {"events", "both"}
        ):
            if data in {original_state, original_events}:
                raise OSError("restore-secret")
        return original_write(self, data)
    monkeypatch.setattr(path_type, "write_bytes", failing_restore)
    with pytest.raises(ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
            result, definition, state, events, phase115_function=dependency
        )
    assert classification(caught) == "dependency_rollback"
    assert calls == 1
    assert writes.count(state) == 1
    assert writes.count(events) == 1
    assert "restore-secret" not in str(caught.value)


_PHASE155_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_PHASE155_SENTINEL = object()


def _phase155_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": step_id,
                    "name": step_id.capitalize(),
                    "employee": step_id[0],
                    "instructions": step_id,
                }
                for step_id in _PHASE155_STEP_IDS
            ],
        }
    )


def _phase155_predecessor(
    step_id: str,
    position: int,
    *,
    provider: object = "other",
    request_id: object = _PHASE155_SENTINEL,
    output_text: object = "output",
) -> RuntimeStepEvent:
    resolved_request_id = (
        f"request-{step_id}" if request_id is _PHASE155_SENTINEL else request_id
    )
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        position,
        step_id[0],
        "running",
        "succeeded",
        provider,  # type: ignore[arg-type]
        None,
        f"response-{step_id}",
        resolved_request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        None,
    )


def phase155_setup(
    tmp_path: Path,
    status: str,
    *,
    earlier_empty: tuple[int, ...] = (2,),
    message: str = "safe failure",
):
    """Six-step Phase-155 provenance fixture shared by the Phase 168 tests.

    Terminal step 6, predecessors 1-5 succeeded, step 2 (and any extra
    ``earlier_empty`` positions) with empty ``output_text``, immediate step 5
    with empty ``output_text``, provider ``"openai"`` and ``request_id=None``,
    earlier request IDs exact non-empty built-in strings, and valid terminal
    succeeded/failed variants.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    definition = _phase155_workflow()
    state = WorkflowExecutionState(
        "w",
        status,
        "six",
        6,
        "s",
        tuple(_PHASE155_STEP_IDS)
        if status == "succeeded"
        else tuple(_PHASE155_STEP_IDS[:5]),
        None if status == "succeeded" else "api_error",
    )
    events = [
        _phase155_predecessor(
            step_id,
            position,
            output_text="" if position in earlier_empty else "output",
        )
        for position, step_id in enumerate(_PHASE155_STEP_IDS[:5], 1)
    ]
    events[4] = _phase155_predecessor(
        "five", 5, provider="openai", request_id=None, output_text=""
    )
    if status == "succeeded":
        terminal = RuntimeStepEvent(
            "step_succeeded",
            "w",
            "six",
            6,
            "s",
            "running",
            "succeeded",
            "openai",
            None,
            "response-six",
            "request-six",
            "output-six",
            None,
        )
        result = PersistedExecutionOutcome(
            "persisted_success", "w", "six", 6, "s", None
        )
    else:
        terminal = RuntimeStepEvent(
            "step_failed",
            "w",
            "six",
            6,
            "s",
            "running",
            "failed",
            "openai",
            "api_error",
            None,
            "request-six",
            None,
            message,
        )
        result = PersistedExecutionOutcome(
            "persisted_failure", "w", "six", 6, "s", "api_error"
        )
    events.append(terminal)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode("utf-8"))
    events_path.write_bytes(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events).encode("utf-8")
    )
    return result, definition, state_path, events_path


def _phase155_expected_decision() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        "six",
        6,
        "s",
        None,
        None,
        None,
        "last_step_succeeded",
    )


def _phase155_replace_event(
    events_path: Path, position: int, event: RuntimeStepEvent
) -> None:
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)
    replacement = serialize_runtime_step_event_jsonl(event)
    lines[position - 1] = replacement
    events_path.write_text("".join(lines), encoding="utf-8")


def test_phase155_success_delegates_to_phase115_exactly_once(tmp_path: Path) -> None:
    """Phase-155 persisted success delegates to Phase 115 exactly once with
    canonical four-argument object identity/order, exact returned-object
    identity, and unchanged targets."""
    result, definition, state, events = phase155_setup(tmp_path, "succeeded")
    before = state.read_bytes(), events.read_bytes()
    decision = _phase155_expected_decision()
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return decision

    returned = route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
        result, definition, state, events, phase115_function=dependency
    )
    assert returned is decision
    assert len(calls) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            calls[0], (result, definition, state, events), strict=True
        )
    )
    assert (state.read_bytes(), events.read_bytes()) == before

    # The fallback must not invent predecessor provider/request-ID policy: an
    # otherwise identical history whose immediate predecessor keeps
    # provider="other" and a non-empty request ID still delegates.
    alt = phase155_setup(tmp_path / "alt", "succeeded")
    _phase155_replace_event(
        alt[3],
        5,
        _phase155_predecessor(
            "five", 5, provider="other", request_id="request-five", output_text=""
        ),
    )
    alt_before = alt[2].read_bytes(), alt[3].read_bytes()
    alt_calls: list[tuple[object, ...]] = []

    def alt_dependency(*args: object) -> WorkflowProgressionDecision:
        alt_calls.append(args)
        return decision

    alt_returned = route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
        alt[0],
        alt[1],
        alt[2],
        alt[3],
        phase115_function=alt_dependency,
    )
    assert alt_returned is decision
    assert len(alt_calls) == 1
    assert (alt[2].read_bytes(), alt[3].read_bytes()) == alt_before

    # Terminal succeeded response_id="" stays rejected.
    pin = phase155_setup(tmp_path / "pin-response", "succeeded")
    empty_response = RuntimeStepEvent(
        "step_succeeded",
        "w",
        "six",
        6,
        "s",
        "running",
        "succeeded",
        "openai",
        None,
        "",
        "request-six",
        "output-six",
        None,
    )
    _phase155_replace_event(pin[3], 6, empty_response)
    pin_before = pin[2].read_bytes(), pin[3].read_bytes()
    pin_calls = {"phase115": 0}

    def pin_dependency(*_: object) -> object:
        pin_calls["phase115"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
            pin[0], pin[1], pin[2], pin[3], phase115_function=pin_dependency
        )
    assert classification(caught) == "terminal_contract"
    assert pin_calls["phase115"] == 0
    assert (pin[2].read_bytes(), pin[3].read_bytes()) == pin_before

    # Final succeeded terminal output_text="" stays rejected.
    pin2 = phase155_setup(tmp_path / "pin-output", "succeeded")
    empty_output = RuntimeStepEvent(
        "step_succeeded",
        "w",
        "six",
        6,
        "s",
        "running",
        "succeeded",
        "openai",
        None,
        "response-six",
        "request-six",
        "",
        None,
    )
    _phase155_replace_event(pin2[3], 6, empty_output)
    pin2_before = pin2[2].read_bytes(), pin2[3].read_bytes()
    pin2_calls = {"phase115": 0}

    def pin2_dependency(*_: object) -> object:
        pin2_calls["phase115"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
            pin2[0], pin2[1], pin2[2], pin2[3], phase115_function=pin2_dependency
        )
    assert classification(caught) == "terminal_contract"
    assert pin2_calls["phase115"] == 0
    assert (pin2[2].read_bytes(), pin2[3].read_bytes()) == pin2_before

    # workflow_complete stays strict: the completion route must not gain
    # predecessor-empty compatibility through the fallback.
    pin3 = phase155_setup(tmp_path / "pin-complete", "succeeded")
    pin3_before = pin3[2].read_bytes(), pin3[3].read_bytes()
    completion = WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        "six",
        6,
        "s",
        None,
        None,
        None,
        "last_step_succeeded",
    )
    pin3_calls = {"phase115": 0}

    def pin3_dependency(*_: object) -> object:
        pin3_calls["phase115"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
            completion,
            pin3[1],
            pin3[2],
            pin3[3],
            phase115_function=pin3_dependency,
        )
    assert classification(caught) == "terminal_contract"
    assert pin3_calls["phase115"] == 0
    assert (pin3[2].read_bytes(), pin3[3].read_bytes()) == pin3_before


def test_phase155_failure_empty_message_returns_unchanged_zero_calls(
    tmp_path: Path,
) -> None:
    """Phase-155 persisted failure with valid message="" returns the exact
    supplied object with Phase 115 call count zero and unchanged targets."""
    result, definition, state, events = phase155_setup(
        tmp_path, "failed", message=""
    )
    before = state.read_bytes(), events.read_bytes()
    calls = {"phase115": 0}

    def forbidden(*_: object) -> object:
        calls["phase115"] += 1
        raise AssertionError("must not be called")

    returned = route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
        result, definition, state, events, phase115_function=forbidden
    )
    assert returned is result
    assert calls == {"phase115": 0}
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_success_multiple_earlier_empty_delegates_once(tmp_path: Path) -> None:
    """Phase-155 persisted success with multiple earlier empty outputs (steps
    2 and 3) delegates to Phase 115 exactly once with unchanged targets."""
    result, definition, state, events = phase155_setup(
        tmp_path, "succeeded", earlier_empty=(2, 3)
    )
    before = state.read_bytes(), events.read_bytes()
    decision = _phase155_expected_decision()
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> WorkflowProgressionDecision:
        calls.append(args)
        return decision

    returned = route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
        result, definition, state, events, phase115_function=dependency
    )
    assert returned is decision
    assert len(calls) == 1
    assert all(
        actual is wanted
        for actual, wanted in zip(
            calls[0], (result, definition, state, events), strict=True
        )
    )
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_failure_multiple_earlier_empty_returns_unchanged_zero_calls(
    tmp_path: Path,
) -> None:
    """Phase-155 persisted failure with multiple earlier empty outputs returns
    the exact supplied object with zero calls and unchanged targets."""
    result, definition, state, events = phase155_setup(
        tmp_path, "failed", earlier_empty=(2, 3), message=""
    )
    before = state.read_bytes(), events.read_bytes()
    calls = {"phase115": 0}

    def forbidden(*_: object) -> object:
        calls["phase115"] += 1
        raise AssertionError("must not be called")

    returned = route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
        result, definition, state, events, phase115_function=forbidden
    )
    assert returned is result
    assert calls == {"phase115": 0}
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step2_output_none_rejected_before_phase115(tmp_path: Path) -> None:
    """Earlier predecessor step 2 output_text=None rejects with exact
    terminal_contract before Phase 115 with zero calls."""
    result, definition, state, events = phase155_setup(tmp_path, "succeeded")
    _phase155_replace_event(
        events,
        2,
        _phase155_predecessor("two", 2, output_text=None),
    )
    before = state.read_bytes(), events.read_bytes()
    calls = {"phase115": 0}

    def forbidden(*_: object) -> object:
        calls["phase115"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
            result, definition, state, events, phase115_function=forbidden
        )
    assert classification(caught) == "terminal_contract"
    assert calls == {"phase115": 0}
    assert (state.read_bytes(), events.read_bytes()) == before


def test_phase155_step5_output_non_string_rejected_before_phase115(
    tmp_path: Path,
) -> None:
    """Immediate predecessor step 5 output_text=1 (non-string) rejects with
    exact terminal_contract before Phase 115 with zero calls."""
    result, definition, state, events = phase155_setup(tmp_path, "succeeded")
    _phase155_replace_event(
        events,
        5,
        _phase155_predecessor(
            "five", 5, provider="openai", request_id=None, output_text=1
        ),
    )
    before = state.read_bytes(), events.read_bytes()
    calls = {"phase115": 0}

    def forbidden(*_: object) -> object:
        calls["phase115"] += 1
        raise AssertionError("must not be called")

    with pytest.raises(
        ClassifiedPersistedOutcomeProgressionCycleHandoffReentryContinuationCompatibilityError
    ) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary(
            result, definition, state, events, phase115_function=forbidden
        )
    assert classification(caught) == "terminal_contract"
    assert calls == {"phase115": 0}
    assert (state.read_bytes(), events.read_bytes()) == before
