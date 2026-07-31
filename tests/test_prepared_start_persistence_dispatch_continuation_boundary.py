"""Focused Phase 90 strict-boundary tests."""

# ruff: noqa: E501

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStartPersistenceDispatchContinuationCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_dispatch_continuation_boundary,
)
from ai_office.engine.prepared_start_persistence_dispatch_phase_bridge_cycle_reentry_continuation import (
    PreparedStartPersistenceDispatchPhaseBridgeCycleReentryContinuationCompatibilityError,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class WorkflowChild(WorkflowDefinition):
    pass


class EmployeeChild(EmployeeDefinition):
    pass


class StartChild(PreparedStepExecutionStart):
    pass


class ResultChild(RunningStatePersistenceResult):
    pass


def reject_classification(callable_object, classification: str) -> None:
    with pytest.raises(PreparedStartPersistenceDispatchContinuationCompatibilityError) as caught:
        callable_object()
    assert caught.value.detail.classification == classification


def mutate(value: object, field: str, replacement: object) -> object:
    object.__setattr__(value, field, replacement)
    return value


def definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({"id": "workflow", "name": "Workflow", "description": "test", "steps": [
        {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
        {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
    ]})


def person() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({"id": "two", "name": "Two", "role": "role", "instructions": "employee", "model": "model", "allowed_tools": ["tool"]})


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(ModelInvocationRequest("model", "employee", "b", ("tool",)), WorkflowExecutionState("workflow", "running", "second", 2, "two", ("first",), None))


def targets(tmp_path: Path, status: str = "succeeded", index: int = 1) -> tuple[Path, Path]:
    workflow = definition()
    step = workflow.steps[index - 1]
    state = WorkflowExecutionState("workflow", status, step.id, index, step.employee, tuple(item.id for item in workflow.steps[:index]) if status == "succeeded" else (), None if status == "succeeded" else "api_error")
    events = []
    if index == 2:
        events.append(RuntimeStepEvent("step_succeeded", "workflow", "first", 1, "one", "running", "succeeded", "openai", None, "response", "request", "output", None))
    events.append(RuntimeStepEvent("step_succeeded" if status == "succeeded" else "step_failed", "workflow", step.id, index, step.employee, "running", status, "openai", None if status == "succeeded" else "api_error", "response" if status == "succeeded" else None, "request", "output" if status == "succeeded" else None, None if status == "succeeded" else "safe"))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events), encoding="utf-8")
    return state_path, events_path


def invoke(result: object, employee: object | None, state: Path, events: Path, function=None):
    kwargs = {} if function is None else {"phase90_function": function}
    return route_prepared_start_persistence_dispatch_continuation_boundary(result, definition(), employee, state, events, **kwargs)


def test_signature_is_canonical() -> None:
    assert tuple(inspect.signature(route_prepared_start_persistence_dispatch_continuation_boundary).parameters)[:5] == ("result", "workflow", "employee", "state_path", "events_path")


def test_prepared_route_calls_phase90_once_and_returns_identity(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    result = start()
    workflow = definition()
    employee = person()
    received: list[object] = []
    expected = RunningStatePersistenceResult(len(serialize_workflow_execution_state_json(result.running_state).encode()))

    def dependency(*args: object) -> object:
        received.extend(args)
        state.write_bytes(serialize_workflow_execution_state_json(result.running_state).encode())
        return expected

    actual = route_prepared_start_persistence_dispatch_continuation_boundary(result, workflow, employee, state, events, phase90_function=dependency)
    assert actual is expected
    assert received == [result, workflow, employee, state, events]
    assert all(received[index] is value for index, value in enumerate((result, workflow, employee, state, events)))
    assert received[3] is state and received[4] is events


@pytest.mark.parametrize("field, replacement", [
    ("model", 1), ("system_instructions", None), ("task_instructions", True),
    ("allowed_tools", ["tool"]),
])
def test_request_field_type_rejection(tmp_path: Path, field: str, replacement: object) -> None:
    state, events = targets(tmp_path)
    result = start()
    mutate(result.request, field, replacement)
    reject_classification(lambda: invoke(result, person(), state, events), "start_contract")


@pytest.mark.parametrize("field, replacement", [
    ("workflow_id", 1), ("status", "succeeded"), ("current_step_id", None),
    ("current_step_index", True), ("current_employee_id", 1),
    ("completed_step_ids", ["first"]), ("last_failure_category", "api_error"),
])
def test_running_state_field_type_and_value_rejection(tmp_path: Path, field: str, replacement: object) -> None:
    state, events = targets(tmp_path)
    result = start()
    mutate(result.running_state, field, replacement)
    reject_classification(lambda: invoke(result, person(), state, events), "employee_contract" if field == "current_employee_id" else "start_contract")


def test_exact_model_and_running_state_subclasses_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)

    class RequestChild(ModelInvocationRequest):
        pass

    class StateChild(WorkflowExecutionState):
        pass

    request = RequestChild("model", "employee", "b", ("tool",))
    running = StateChild("workflow", "running", "second", 2, "two", ("first",), None)
    reject_classification(lambda: invoke(PreparedStepExecutionStart(request, running), person(), state, events), "start_contract")


@pytest.mark.parametrize("result", [
    type("PreparedChild", (PreparedStepExecutionStart,), {})(ModelInvocationRequest("model", "employee", "b", ("tool",)), WorkflowExecutionState("workflow", "running", "second", 2, "two", ("first",), None)),
    type("CompletionChild", (WorkflowProgressionDecision,), {})("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded"),
    type("FailureChild", (PersistedExecutionOutcome,), {})("persisted_failure", "workflow", "first", 1, "one", "api_error"),
])
def test_result_subclasses_are_rejected(tmp_path: Path, result: object) -> None:
    state, events = targets(tmp_path, "succeeded" if type(result).__name__ == "CompletionChild" else "failed" if type(result).__name__ == "FailureChild" else "succeeded", 2 if type(result).__name__ == "CompletionChild" else 1)
    reject_classification(lambda: invoke(result, None if type(result).__name__ != "PreparedChild" else person(), state, events), "result_type")


def test_workflow_and_employee_subclasses_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    class WorkflowChild(WorkflowDefinition):
        pass
    class EmployeeChild(EmployeeDefinition):
        pass
    workflow = WorkflowChild.model_validate(definition().model_dump())
    employee = EmployeeChild.model_validate(person().model_dump())
    reject_classification(lambda: route_prepared_start_persistence_dispatch_continuation_boundary(start(), workflow, person(), state, events), "workflow_definition")
    reject_classification(lambda: invoke(start(), employee, state, events), "employee_contract")


def test_employee_and_workflow_mismatch_rejection(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    wrong_employee = EmployeeDefinition.model_validate({"id": "one", "name": "One", "role": "role", "instructions": "employee", "model": "model", "allowed_tools": ["tool"]})
    reject_classification(lambda: invoke(start(), wrong_employee, state, events), "employee_contract")
    wrong_workflow = WorkflowDefinition.model_validate({"id": "other", "name": "Other", "description": "test", "steps": [{"id": "first", "name": "First", "employee": "one", "instructions": "a"}, {"id": "second", "name": "Second", "employee": "two", "instructions": "b"}]})
    reject_classification(lambda: route_prepared_start_persistence_dispatch_continuation_boundary(start(), wrong_workflow, person(), state, events), "start_contract")


def test_request_value_mismatch_is_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    result = start()
    mutate(result.request, "model", "different-model")
    reject_classification(lambda: invoke(result, person(), state, events), "start_contract")


def test_predecessor_history_mismatch_is_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    state.write_text(serialize_workflow_execution_state_json(WorkflowExecutionState("workflow", "succeeded", "first", 1, "one", (), "api_error")), encoding="utf-8")
    reject_classification(lambda: invoke(start(), person(), state, events), "terminal_contract")


@pytest.mark.parametrize("result, status", [
    (WorkflowProgressionDecision("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded"), "succeeded"),
    (PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error"), "failed"),
])
def test_stop_routes_return_same_object_without_call(tmp_path: Path, result: object, status: str) -> None:
    state, events = targets(tmp_path, status, 2 if isinstance(result, WorkflowProgressionDecision) else 1)
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert invoke(result, None, state, events, dependency) is result
    assert calls == 0


def test_stop_routes_reject_employee_and_prepared_requires_exact_employee(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    completion = WorkflowProgressionDecision("workflow_complete", "workflow", "first", 1, "one", None, None, None, "last_step_succeeded")
    with pytest.raises(PreparedStartPersistenceDispatchContinuationCompatibilityError):
        invoke(completion, person(), state, events)
    with pytest.raises(PreparedStartPersistenceDispatchContinuationCompatibilityError):
        invoke(start(), None, state, events)


def test_invalid_return_restores_both_targets_and_does_not_retry(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    original = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*args: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"bad")
        events.write_bytes(b"also bad")
        return object()

    with pytest.raises(PreparedStartPersistenceDispatchContinuationCompatibilityError) as caught:
        invoke(start(), person(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == original


class PersistenceResultChild(RunningStatePersistenceResult):
    pass


@pytest.mark.parametrize("returned", [
    object(),
    PersistenceResultChild(1),
    RunningStatePersistenceResult(True),
    RunningStatePersistenceResult(0),
])
def test_persistence_return_type_and_field_contract(tmp_path: Path, returned: object) -> None:
    state, events = targets(tmp_path)
    original = state.read_bytes(), events.read_bytes()
    expected_bytes = serialize_workflow_execution_state_json(start().running_state).encode()

    def dependency(*args: object) -> object:
        state.write_bytes(expected_bytes)
        return returned

    reject_classification(lambda: invoke(start(), person(), state, events, dependency), "persistence_contract")
    assert (state.read_bytes(), events.read_bytes()) == original


@pytest.mark.parametrize("mutation", [
    lambda state, events: None,
    lambda state, events: state.write_bytes(b'{"workflow_id":'),
    lambda state, events: state.write_bytes(serialize_workflow_execution_state_json(WorkflowExecutionState("other", "running", "second", 2, "two", ("first",), None)).encode()),
    lambda state, events: events.write_bytes(events.read_bytes() + b"unexpected"),
])
def test_no_write_partial_unrelated_malformed_and_event_mutation_rejected(tmp_path: Path, mutation) -> None:
    state, events = targets(tmp_path)
    original = state.read_bytes(), events.read_bytes()
    expected = RunningStatePersistenceResult(len(serialize_workflow_execution_state_json(start().running_state).encode()))

    def dependency(*args: object) -> object:
        mutation(state, events)
        return expected

    reject_classification(lambda: invoke(start(), person(), state, events, dependency), "persistence_contract")
    assert (state.read_bytes(), events.read_bytes()) == original


def test_invalid_persisted_history_and_terminal_mismatch_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    state.write_text("{}", encoding="utf-8")
    reject_classification(lambda: invoke(start(), person(), state, events, lambda *_: object()), "terminal_contract")
    completion = WorkflowProgressionDecision("workflow_complete", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded")
    state, events = targets(tmp_path, "succeeded", 1)
    reject_classification(lambda: invoke(completion, None, state, events), "terminal_contract")


def test_direct_persisted_success_and_unsupported_progression_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    success = PersistedExecutionOutcome("persisted_success", "workflow", "first", 1, "one", None)
    reject_classification(lambda: invoke(success, None, state, events), "failure_contract")
    unsupported = WorkflowProgressionDecision("stopped_failed", "workflow", "second", 2, "two", None, None, None, "last_step_succeeded")
    state, events = targets(tmp_path, "succeeded", 2)
    reject_classification(lambda: invoke(unsupported, None, state, events), "completion_contract")


@pytest.mark.parametrize("target_name, classification", [("state", "state_target"), ("events", "event_target")])
def test_missing_and_non_regular_targets_are_rejected(tmp_path: Path, target_name: str, classification: str) -> None:
    state, events = targets(tmp_path)
    target = state if target_name == "state" else events
    target.unlink()
    reject_classification(lambda: invoke(start(), person(), state, events), classification)
    target.mkdir()
    reject_classification(lambda: invoke(start(), person(), state, events), classification)


@pytest.mark.parametrize("method, target_name, classification", [
    ("is_file", "state", "state_target"), ("is_file", "events", "event_target"),
    ("read_bytes", "state", "state_target"), ("read_bytes", "events", "event_target"),
])
def test_target_is_file_and_read_bytes_oserrors_are_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str, target_name: str, classification: str) -> None:
    state, events = targets(tmp_path)
    target = state if target_name == "state" else events
    original = getattr(Path, method)

    def failing(path: Path):
        if path == target:
            raise OSError("target operation")
        return original(path)

    monkeypatch.setattr(Path, method, failing)
    reject_classification(lambda: invoke(start(), person(), state, events), classification)


@pytest.mark.parametrize("changed", ["none", "state", "event", "both"])
def test_safe_error_compensates_each_target_shape(tmp_path: Path, changed: str) -> None:
    state, events = targets(tmp_path)
    original = state.read_bytes(), events.read_bytes()
    error = PreparedStartPersistenceDispatchPhaseBridgeCycleReentryContinuationCompatibilityError("dependency_error")

    def dependency(*args: object) -> object:
        if changed in ("state", "both"):
            state.write_bytes(b"state mutation")
        if changed in ("event", "both"):
            events.write_bytes(b"event mutation")
        raise error

    with pytest.raises(PreparedStartPersistenceDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(start(), person(), state, events, dependency)
    assert caught.value is error
    assert (state.read_bytes(), events.read_bytes()) == original


@pytest.mark.parametrize("failed", ["state", "event", "both"])
def test_rollback_failure_reports_dependency_rollback_and_attempts_both_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str) -> None:
    state, events = targets(tmp_path)
    original = state.read_bytes(), events.read_bytes()
    calls: list[Path] = []
    real_write = Path.write_bytes

    def dependency(*args: object) -> object:
        state.open("wb").write(b"state mutation")
        events.open("wb").write(b"event mutation")
        return object()

    def failing_write(path: Path, data: bytes) -> int:
        if path in (state, events):
            calls.append(path)
            if (failed == "both" or (failed == "state" and path == state) or (failed == "event" and path == events)) and len([item for item in calls if item == path]) == 1:
                raise OSError("rollback")
        return real_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", failing_write)
    reject_classification(lambda: invoke(start(), person(), state, events, dependency), "dependency_rollback")
    assert calls == [state, events]
    if failed == "state":
        assert events.read_bytes() == original[1]
    elif failed == "event":
        assert state.read_bytes() == original[0]


def test_rollback_failure_does_not_attempt_a_second_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state, events = targets(tmp_path)
    calls: list[Path] = []
    real_write = Path.write_bytes

    def dependency(*args: object) -> object:
        state.open("wb").write(b"bad")
        events.open("wb").write(b"bad")
        return object()

    def failing_write(path: Path, data: bytes) -> int:
        if path in (state, events):
            calls.append(path)
            raise OSError("rollback")
        return real_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", failing_write)
    reject_classification(lambda: invoke(start(), person(), state, events, dependency), "dependency_rollback")
    assert calls == [state, events]


def test_safe_phase90_error_preserves_identity_after_compensation(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    error = PreparedStartPersistenceDispatchPhaseBridgeCycleReentryContinuationCompatibilityError("dependency_error")

    def dependency(*args: object) -> object:
        state.write_bytes(b"bad")
        raise error

    with pytest.raises(PreparedStartPersistenceDispatchPhaseBridgeCycleReentryContinuationCompatibilityError) as caught:
        invoke(start(), person(), state, events, dependency)
    assert caught.value is error


def test_unexpected_error_is_sanitized(tmp_path: Path) -> None:
    state, events = targets(tmp_path)

    def dependency(*args: object) -> object:
        raise RuntimeError("secret")

    with pytest.raises(PreparedStartPersistenceDispatchContinuationCompatibilityError) as caught:
        invoke(start(), person(), state, events, dependency)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)


def test_public_signature_kinds_default_identity_and_non_callable_dependency(tmp_path: Path) -> None:
    signature = inspect.signature(route_prepared_start_persistence_dispatch_continuation_boundary)
    assert [parameter.kind for parameter in signature.parameters.values()] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    ]
    assert signature.parameters["phase90_function"].default is not None
    state, events = targets(tmp_path)
    for dependency in (None, 1, object()):
        reject_classification(lambda dependency=dependency: route_prepared_start_persistence_dispatch_continuation_boundary(start(), definition(), person(), state, events, phase90_function=dependency), "persistence_contract")


@pytest.mark.parametrize("field, value", [
    ("id", 1), ("name", 1), ("description", 1), ("steps", ()),
])
def test_workflow_all_field_types_and_substitutes_are_rejected(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    bad = definition().model_copy(update={field: value})
    reject_classification(lambda: route_prepared_start_persistence_dispatch_continuation_boundary(start(), bad, person(), state, events), "workflow_definition")
    substitute = SimpleNamespace(**definition().model_dump())
    reject_classification(lambda: route_prepared_start_persistence_dispatch_continuation_boundary(start(), substitute, person(), state, events), "workflow_definition")
    child = WorkflowChild.model_validate(definition().model_dump())
    reject_classification(lambda: route_prepared_start_persistence_dispatch_continuation_boundary(start(), child, person(), state, events), "workflow_definition")


@pytest.mark.parametrize("field, value", [
    ("id", 1), ("name", 1), ("role", 1), ("instructions", 1), ("model", 1), ("allowed_tools", ("tool",)),
])
def test_employee_all_fields_and_allowed_tool_elements_are_strict(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    bad = person().model_copy(update={field: value})
    reject_classification(lambda: invoke(start(), bad, state, events), "employee_contract")
    child = EmployeeChild.model_validate(person().model_dump())
    reject_classification(lambda: invoke(start(), child, state, events), "employee_contract")
    substitute = SimpleNamespace(**person().model_dump())
    reject_classification(lambda: invoke(start(), substitute, state, events), "employee_contract")


@pytest.mark.parametrize("field, value", [
    ("model", "different"), ("system_instructions", "different"),
    ("task_instructions", "different"), ("allowed_tools", ("different",)),
])
def test_employee_derived_request_matching_is_strict(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    bad = start()
    object.__setattr__(bad.request, field, value)
    reject_classification(lambda: invoke(bad, person(), state, events), "start_contract")


def test_start_model_and_nested_attribute_substitutes_are_exact(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    substitute = SimpleNamespace(request=start().request, running_state=start().running_state)
    reject_classification(lambda: invoke(substitute, person(), state, events), "result_type")
    child = StartChild(start().request, start().running_state)
    reject_classification(lambda: invoke(child, person(), state, events), "result_type")
    request_substitute = SimpleNamespace(model="model", system_instructions="employee", task_instructions="b", allowed_tools=("tool",))
    state_substitute = SimpleNamespace(workflow_id="workflow", status="running", current_step_id="second", current_step_index=2, current_employee_id="two", completed_step_ids=("first",), last_failure_category=None)
    reject_classification(lambda: invoke(PreparedStepExecutionStart(request_substitute, state_substitute), person(), state, events), "start_contract")


def test_path_identity_and_conflict_are_strict(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    received: list[object] = []
    expected = RunningStatePersistenceResult(len(serialize_workflow_execution_state_json(start().running_state).encode()))
    def dependency(*args: object) -> object:
        received.extend(args)
        state.write_bytes(serialize_workflow_execution_state_json(start().running_state).encode())
        return expected
    equal_state = Path(str(state))
    assert route_prepared_start_persistence_dispatch_continuation_boundary(start(), definition(), person(), equal_state, events, phase90_function=dependency) is expected
    assert received[3] is equal_state and received[4] is events
    reject_classification(lambda: invoke(start(), person(), state, state), "target_conflict")
    reject_classification(lambda: invoke(start(), person(), str(state), events), "state_target")
    reject_classification(lambda: invoke(start(), person(), state, str(events)), "event_target")


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "reordered", "unrelated"])
def test_predecessor_completion_prefix_and_terminal_events_are_strict(tmp_path: Path, mutation: str) -> None:
    state, events = targets(tmp_path)
    first = RuntimeStepEvent("step_succeeded", "workflow", "first", 1, "one", "running", "succeeded", "openai", None, "response", "request", "output", None)
    current = RuntimeStepEvent("step_succeeded", "workflow", "second", 2, "two", "running", "succeeded", "openai", None, "response", "request", "output", None)
    unrelated = RuntimeStepEvent("step_succeeded", "other", "first", 1, "one", "running", "succeeded", "openai", None, "response", "request", "output", None)
    records = {"duplicate": [first, first, current], "missing": [current], "reordered": [current, first], "unrelated": [first, unrelated, current]}[mutation]
    events.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in records), encoding="utf-8")
    reject_classification(lambda: invoke(start(), person(), state, events), "terminal_contract")


@pytest.mark.parametrize("field, value", [
    ("state_bytes_written", True), ("state_bytes_written", 0),
    ("state_bytes_written", 1),
])
def test_persistence_result_every_field_type_and_value_is_exact(tmp_path: Path, field: str, value: object) -> None:
    state, events = targets(tmp_path)
    expected_bytes = serialize_workflow_execution_state_json(start().running_state).encode()
    result = RunningStatePersistenceResult(len(expected_bytes))
    if value == len(expected_bytes):
        pytest.skip("control value")
    bad = replace(result, **{field: value})
    reject_classification(lambda: invoke(start(), person(), state, events, lambda *_: (state.write_bytes(expected_bytes), bad)[1]), "persistence_contract")
    assert events.read_bytes()


def test_persistence_result_subclass_and_attribute_substitute_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    expected_bytes = serialize_workflow_execution_state_json(start().running_state).encode()
    child = ResultChild(len(expected_bytes))
    substitute = SimpleNamespace(state_bytes_written=len(expected_bytes))
    for value in (child, substitute):
        reject_classification(lambda value=value: invoke(start(), person(), state, events, lambda *_: (state.write_bytes(expected_bytes), value)[1]), "persistence_contract")


@pytest.mark.parametrize("kind", ["unexpected", "malformed"])
@pytest.mark.parametrize("mutation", ["none", "state", "event", "both"])
def test_unexpected_and_malformed_dependency_paths_are_one_call_and_compensated(tmp_path: Path, kind: str, mutation: str) -> None:
    state, events = targets(tmp_path)
    original = state.read_bytes(), events.read_bytes()
    calls = 0
    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if mutation in {"event", "both"}:
            events.write_bytes(b"changed-event")
        if kind == "unexpected":
            raise RuntimeError("secret")
        return object()
    reject_classification(lambda: invoke(start(), person(), state, events, dependency), "dependency_error" if kind == "unexpected" else "persistence_contract")
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == original
