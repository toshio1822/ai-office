"""Focused Phase 112 persisted-running execution cycle reentry continuation tests."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedRunningExecutionCycleReentryContinuationCompatibilityError,
    PersistedRunningExecutionCycleReentryContinuationError,
    PersistedRunningExecutionCycleReentryContinuationFailureDetail,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_cycle_reentry_continuation_boundary,
)
from ai_office.engine.persisted_running_execution_cycle_continuation_boundary import (
    PersistedRunningExecutionCycleContinuationError,
    route_persisted_running_execution_cycle_continuation_boundary,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationRequest, ModelInvocationSuccess, approve_model_invocation_execution
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import RuntimeStepEvent, StepRuntimeExecutionFailure, StepRuntimeExecutionSuccess, WorkflowExecutionState
from ai_office.storage import RunningStatePersistenceResult, serialize_runtime_step_event_jsonl, serialize_workflow_execution_state_json
from ai_office.tools import ToolDefinition


class WorkflowChild(WorkflowDefinition):
    pass


class StepChild(WorkflowStepDefinition):
    pass


class EmployeeChild(EmployeeDefinition):
    pass


class StartChild(PreparedStepExecutionStart):
    pass


class ResultChild(RunningStatePersistenceResult):
    pass


class RequestChild(ModelInvocationRequest):
    pass


class StateChild(WorkflowExecutionState):
    pass


class ToolChild(ToolDefinition):
    pass


class KeyChild(OpenAIApiKey):
    pass


class ApprovalChild(type(approve_model_invocation_execution(ModelInvocationRequest("m", "s", "t", ()), (), provider="p", approved_by="a", approval_id="i"))):
    pass


class RuntimeSuccessChild(StepRuntimeExecutionSuccess):
    pass


class RuntimeFailureChild(StepRuntimeExecutionFailure):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({"id": "w", "name": "W", "description": "D", "steps": [{"id": "one", "name": "One", "employee": "e", "instructions": "a"}, {"id": "two", "name": "Two", "employee": "e", "instructions": "b"}]})


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({"id": "e", "name": "E", "role": "R", "instructions": "system", "model": "model", "allowed_tools": ["tool"]})


def setup(tmp_path: Path) -> dict[str, object]:
    state = WorkflowExecutionState("w", "running", "two", 2, "e", ("one",), None)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", "o", None)), encoding="utf-8")
    request = ModelInvocationRequest("model", "system", "b", ("tool",))
    tools = (ToolDefinition("tool", "Tool", ()),)
    return {"result": RunningStatePersistenceResult(len(state_path.read_bytes())), "start": PreparedStepExecutionStart(request, state), "workflow": workflow(), "employee": employee(), "state_path": state_path, "events_path": events_path, "resolved_tools": tools, "api_key": OpenAIApiKey(value=SecretStr("synthetic")), "approval": approve_model_invocation_execution(request, tools, provider="openai", approved_by="test", approval_id="id"), "transport": lambda _: None}


def runtime() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess("w", "two", 2, "e", ModelInvocationSuccess("openai", "response", None, "completed", ("ok",), "ok"))


def runtime_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure("w", "two", 2, "e", ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None))


def reject(values: dict[str, object], classification: str, **changes: object) -> None:
    supplied = dict(values)
    supplied.update(changes)
    with pytest.raises(PersistedRunningExecutionCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_reentry_continuation_boundary(**supplied)  # type: ignore[arg-type]
    assert caught.value.detail.classification == classification


def failed_targets(tmp_path: Path) -> tuple[Path, Path]:
    state = WorkflowExecutionState("w", "failed", "two", 2, "e", ("one",), "api_error")
    state_path, events_path = tmp_path / "failed-state", tmp_path / "failed-events"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text("".join((
        serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", "o", None)),
        serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_failed", "w", "two", 2, "e", "running", "failed", "openai", "api_error", None, "q", None, "safe")),
    )), encoding="utf-8")
    return state_path, events_path


def test_implementation_uses_only_public_phase105_dependency() -> None:
    source = Path("src/ai_office/engine/persisted_running_execution_cycle_reentry_continuation_boundary.py").read_text()
    assert "route_persisted_running_execution_cycle_continuation_boundary" in source
    assert "phase91" not in source.lower()
    assert "dispatch_phase_bridge" not in source
    assert "._top" not in source
    assert "._execution_inputs" not in source
    assert "._raise" not in source


def test_public_signature_and_canonical_ten_argument_identity(tmp_path: Path) -> None:
    params = list(inspect.signature(route_persisted_running_execution_cycle_reentry_continuation_boundary).parameters.values())
    assert [p.name for p in params[:10]] == ["result", "start", "workflow", "employee", "state_path", "events_path", "resolved_tools", "api_key", "approval", "transport"]
    assert params[10].kind is inspect.Parameter.KEYWORD_ONLY
    values = setup(tmp_path); calls: list[tuple[object, ...]] = []
    expected = runtime()
    def dependency(*args: object) -> object:
        calls.append(args); return expected
    actual = route_persisted_running_execution_cycle_reentry_continuation_boundary(**values, phase105_function=dependency)  # type: ignore[arg-type]
    assert actual is expected and len(calls) == 1
    assert all(left is right for left, right in zip(calls[0], (values["result"], values["start"], values["workflow"], values["employee"], values["state_path"], values["events_path"], values["resolved_tools"], values["api_key"], values["approval"], values["transport"]), strict=True))


def test_default_identity_and_non_callable_rejection(tmp_path: Path) -> None:
    parameter = inspect.signature(route_persisted_running_execution_cycle_reentry_continuation_boundary).parameters["phase105_function"]
    assert parameter.default is route_persisted_running_execution_cycle_continuation_boundary
    values = setup(tmp_path)
    reject(values, "execution_inputs", phase105_function=object())


@pytest.mark.parametrize("field", ["result", "start", "workflow", "employee", "resolved_tools", "api_key", "approval", "transport"])
def test_execution_input_missing_is_rejected(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path)
    if field == "result": values["result"] = object()
    elif field == "transport": values[field] = None
    else: values[field] = None
    reject(values, {"result": "result_type", "start": "start_contract", "workflow": "workflow_definition", "employee": "employee_contract", "resolved_tools": "tools_contract", "api_key": "credential_contract", "approval": "approval_contract", "transport": "execution_inputs"}[field])


@pytest.mark.parametrize("field, value, classification", [
    ("result", ResultChild(1), "result_type"),
    ("start", StartChild(ModelInvocationRequest("model", "system", "b", ("tool",)), WorkflowExecutionState("w", "running", "two", 2, "e", ("one",), None)), "start_contract"),
    ("workflow", WorkflowChild.model_validate(workflow().model_dump()), "workflow_definition"),
    ("employee", EmployeeChild.model_validate(employee().model_dump()), "employee_contract"),
    ("resolved_tools", (ToolChild("tool", "Tool", ()),), "tools_contract"),
    ("api_key", KeyChild(value=SecretStr("synthetic")), "credential_contract"),
])
def test_subclasses_rejected_before_dependency(tmp_path: Path, field: str, value: object, classification: str) -> None:
    values = setup(tmp_path); values[field] = value
    reject(values, classification)


@pytest.mark.parametrize("field, value, classification", [
    ("result", SimpleNamespace(state_bytes_written=1), "result_type"),
    ("workflow", SimpleNamespace(id="w", name="W", description="D", steps=workflow().steps), "workflow_definition"),
    ("employee", SimpleNamespace(id="e", name="E", role="R", instructions="system", model="model", allowed_tools=["tool"]), "employee_contract"),
    ("resolved_tools", [ToolDefinition("tool", "Tool", ())], "tools_contract"),
    ("api_key", SimpleNamespace(value=SecretStr("synthetic")), "credential_contract"),
    ("approval", SimpleNamespace(approved=True), "approval_contract"),
])
def test_attribute_compatible_substitutes_rejected(tmp_path: Path, field: str, value: object, classification: str) -> None:
    values = setup(tmp_path); values[field] = value
    reject(values, classification)


@pytest.mark.parametrize("replacement", [0, -1, True, 1.0])
def test_persistence_byte_count_must_be_positive_exact_int(tmp_path: Path, replacement: object) -> None:
    values = setup(tmp_path); values["result"] = RunningStatePersistenceResult(replacement)  # type: ignore[arg-type]
    reject(values, "persistence_result_contract")


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "reordered", "unrelated"])
def test_predecessor_history_variants_rejected(tmp_path: Path, mutation: str) -> None:
    values = setup(tmp_path); events = values["events_path"]
    original = events.read_text(encoding="utf-8")
    if mutation == "duplicate": events.write_text(original + original, encoding="utf-8")
    elif mutation == "missing": events.write_text("", encoding="utf-8")
    elif mutation == "reordered": events.write_text(original.replace('"step_id":"one"', '"step_id":"other"'), encoding="utf-8")
    else: events.write_text(serialize_runtime_step_event_jsonl(RuntimeStepEvent("unrelated", "other", "x", 9, "z", "ready", "running", "openai", None, "r", "q", "o", None)), encoding="utf-8")
    reject(values, "persistence_result_contract")


@pytest.mark.parametrize("output", ["", None, 123, 1.5])
def test_succeeded_predecessor_empty_output_is_accepted_and_non_string_rejected(tmp_path: Path, output: object) -> None:
    values = setup(tmp_path); events = values["events_path"]
    events.write_text(serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", output, None)), encoding="utf-8")
    calls = 0; expected = runtime()

    def dependency(*_: object) -> object:
        nonlocal calls; calls += 1; return expected

    if output == "":
        actual = route_persisted_running_execution_cycle_reentry_continuation_boundary(**values, phase105_function=dependency)  # type: ignore[arg-type]
        assert actual is expected and calls == 1
    else:
        reject(values, "persistence_result_contract", phase105_function=dependency)
        assert calls == 0


def test_exact_state_bytes_and_equal_path_identity_are_preserved(tmp_path: Path) -> None:
    values = setup(tmp_path)
    state = Path(str(values["state_path"]))
    events = Path(str(values["events_path"]))
    values["state_path"], values["events_path"] = state, events
    expected = runtime(); seen: list[tuple[object, ...]] = []
    actual = route_persisted_running_execution_cycle_reentry_continuation_boundary(**values, phase105_function=lambda *args: (seen.append(args), expected)[1])  # type: ignore[arg-type]
    assert actual is expected and seen[0][4] is state and seen[0][5] is events


@pytest.mark.parametrize("value", [runtime(), runtime_failure()])
def test_valid_runtime_success_and_failure_are_returned_by_identity(tmp_path: Path, value: object) -> None:
    values = setup(tmp_path)
    actual = route_persisted_running_execution_cycle_reentry_continuation_boundary(**values, phase105_function=lambda *_: value)  # type: ignore[arg-type]
    assert actual is value


@pytest.mark.parametrize("value", [RuntimeSuccessChild("w", "two", 2, "e", runtime().invocation_result), RuntimeFailureChild("w", "two", 2, "e", runtime_failure().invocation_result), SimpleNamespace(workflow_id="w", step_id="two", step_index=2, employee_id="e")])
def test_runtime_subclasses_and_attribute_substitutes_are_rejected(tmp_path: Path, value: object) -> None:
    values = setup(tmp_path)
    with pytest.raises(PersistedRunningExecutionCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_reentry_continuation_boundary(**values, phase105_function=lambda *_: value)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "runtime_contract"


@pytest.mark.parametrize("field", ["workflow_id", "step_id", "step_index", "employee_id"])
def test_runtime_identity_mismatch_is_rejected(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path); replacement = {"workflow_id": "other", "step_id": "other", "step_index": 1, "employee_id": "other"}[field]
    bad = replace(runtime(), **{field: replacement})
    with pytest.raises(PersistedRunningExecutionCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_reentry_continuation_boundary(**values, phase105_function=lambda *_: bad)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "runtime_contract"


def test_persisted_failure_stop_route_is_unchanged_and_zero_call(tmp_path: Path) -> None:
    values = setup(tmp_path); state, events = failed_targets(tmp_path)
    result = PersistedExecutionOutcome("persisted_failure", "w", "two", 2, "e", "api_error")
    calls = 0
    def dependency(*_: object) -> object:
        nonlocal calls; calls += 1; return runtime()
    actual = route_persisted_running_execution_cycle_reentry_continuation_boundary(result, None, values["workflow"], None, state, events, None, None, None, None, phase105_function=dependency)  # type: ignore[arg-type]
    assert actual is result and calls == 0


@pytest.mark.parametrize("field", ["start", "employee", "resolved_tools", "api_key", "approval", "transport"])
def test_each_execution_only_input_is_rejected_on_stop(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path); state, events = failed_targets(tmp_path)
    result = PersistedExecutionOutcome("persisted_failure", "w", "two", 2, "e", "api_error")
    replacement = values[field] if field != "transport" else (lambda _: None)
    with pytest.raises(PersistedRunningExecutionCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_reentry_continuation_boundary(result, replacement if field == "start" else None, values["workflow"], replacement if field == "employee" else None, state, events, replacement if field == "resolved_tools" else None, replacement if field == "api_key" else None, replacement if field == "approval" else None, replacement if field == "transport" else None)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "execution_inputs"


def test_stop_target_mismatch_and_execution_input_combination_are_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path); state, events = failed_targets(tmp_path)
    result = PersistedExecutionOutcome("persisted_failure", "w", "two", 2, "e", "api_error")
    with pytest.raises(PersistedRunningExecutionCycleReentryContinuationCompatibilityError):
        route_persisted_running_execution_cycle_reentry_continuation_boundary(result, values["start"], values["workflow"], values["employee"], state, events, values["resolved_tools"], values["api_key"], values["approval"], values["transport"])  # type: ignore[arg-type]


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_missing_and_directory_targets_are_classified_separately(tmp_path: Path, target: str) -> None:
    values = setup(tmp_path); path = values[target]
    path.unlink()
    reject(values, "state_target" if target == "state_path" else "event_target")
    path.mkdir()
    reject(values, "state_target" if target == "state_path" else "event_target")


def test_target_conflict_and_equal_paths_are_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)
    reject(values, "target_conflict", events_path=values["state_path"])


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_is_file_oserror_is_classified_by_target(tmp_path: Path, target: str, monkeypatch: pytest.MonkeyPatch) -> None:
    values = setup(tmp_path); selected = values[target]; original = Path.is_file
    def raising(path: Path) -> bool:
        if path == selected: raise OSError("unreadable")
        return original(path)
    monkeypatch.setattr(Path, "is_file", raising)
    reject(values, "state_target" if target == "state_path" else "event_target")


@pytest.mark.parametrize("target", ["state_path", "events_path"])
def test_read_bytes_oserror_is_classified_by_target(tmp_path: Path, target: str, monkeypatch: pytest.MonkeyPatch) -> None:
    values = setup(tmp_path); selected = values[target]; original = Path.read_bytes
    def raising(path: Path) -> bytes:
        if path == selected: raise OSError("unreadable")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", raising)
    reject(values, "state_target" if target == "state_path" else "event_target")


def test_safe_phase105_error_identity_is_preserved_and_no_retry(tmp_path: Path) -> None:
    values = setup(tmp_path); calls = 0
    supplied = PersistedRunningExecutionCycleContinuationError("safe")
    def dependency(*_: object) -> object:
        nonlocal calls; calls += 1; raise supplied
    with pytest.raises(PersistedRunningExecutionCycleContinuationError) as caught:
        route_persisted_running_execution_cycle_reentry_continuation_boundary(**values, phase105_function=dependency)  # type: ignore[arg-type]
    assert caught.value is supplied and calls == 1


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once(tmp_path: Path, failed_target: str, monkeypatch: pytest.MonkeyPatch) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    calls = {"state": 0, "events": 0}
    original_write = Path.write_bytes
    def failing_write(path: Path, data: bytes) -> int:
        if failed_target in ("state", "both") and path == state:
            calls["state"] += 1
            raise OSError("state rollback")
        if failed_target in ("events", "both") and path == events:
            calls["events"] += 1
            raise OSError("event rollback")
        calls["state" if path == state else "events"] += 1
        return original_write(path, data)
    monkeypatch.setattr(Path, "write_bytes", failing_write)
    def dependency(*_: object) -> object:
        original_write(state, b"mutated")
        original_write(events, b"mutated")
        raise RuntimeError("unexpected")
    with pytest.raises(PersistedRunningExecutionCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_reentry_continuation_boundary(**values, phase105_function=dependency)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls["state"] == 1 and calls["events"] == 1


@pytest.mark.parametrize("decision", ["persisted_success", "stopped_failed", "not_progressable"])
def test_unsupported_stop_progression_is_zero_call_rejected(tmp_path: Path, decision: str) -> None:
    values = setup(tmp_path)
    result = WorkflowProgressionDecision(decision, "w", "two", 2, "e", None, None, None, "reason")  # type: ignore[arg-type]
    reject({**values, "result": result}, "completion_contract")


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
@pytest.mark.parametrize("error_kind", ["safe", "unexpected", "malformed"])
def test_dependency_matrix_compensates_without_retry(tmp_path: Path, mutation: str | None, error_kind: str) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes(); calls = 0
    def dependency(*_: object) -> object:
        nonlocal calls; calls += 1
        if mutation in ("state", "both"): state.write_bytes(b"state-mutated")
        if mutation in ("events", "both"): events.write_bytes(b"events-mutated")
        if error_kind == "safe":
            raise PersistedRunningExecutionCycleContinuationError("safe")
        if error_kind == "unexpected": raise RuntimeError("secret")
        return object()
    with pytest.raises(Exception): route_persisted_running_execution_cycle_reentry_continuation_boundary(**values, phase105_function=dependency)  # type: ignore[arg-type]
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_stop_routes_are_unchanged_and_zero_call(tmp_path: Path) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    state.write_text(serialize_workflow_execution_state_json(WorkflowExecutionState("w", "succeeded", "two", 2, "e", ("one", "two"), None)), encoding="utf-8")
    events.write_text("".join((serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", "o", None)), serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "two", 2, "e", "running", "succeeded", "openai", None, "r2", "q", "o", None)))), encoding="utf-8")
    result = WorkflowProgressionDecision("workflow_complete", "w", "two", 2, "e", None, None, None, "last_step_succeeded")
    calls = 0
    def dependency(*_: object) -> object:
        nonlocal calls; calls += 1; return runtime()
    actual = route_persisted_running_execution_cycle_reentry_continuation_boundary(result, None, values["workflow"], None, state, events, None, None, None, None, phase105_function=dependency)  # type: ignore[arg-type]
    assert actual is result and calls == 0


@pytest.mark.parametrize("mutation", ["state", "events"])
def test_unexpected_dependency_error_is_sanitized_and_restored(tmp_path: Path, mutation: str) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()
    def dependency(*_: object) -> object:
        (state if mutation == "state" else events).write_bytes(b"mutated")
        raise RuntimeError("secret")
    with pytest.raises(PersistedRunningExecutionCycleReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_reentry_continuation_boundary(**values, phase105_function=dependency)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == before
