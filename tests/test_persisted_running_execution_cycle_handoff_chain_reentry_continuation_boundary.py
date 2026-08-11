"""Focused Phase 126 persisted-running execution cycle handoff reentry continuation tests."""

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
    PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError,
    PersistedRunningExecutionCycleHandoffChainReentryContinuationError,
    PersistedRunningExecutionCycleHandoffChainReentryContinuationFailureDetail,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffReentryContinuationError,
    route_persisted_running_execution_cycle_handoff_reentry_continuation_boundary,
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
    return WorkflowDefinition.model_validate({"id": "w", "name": "W", "description": "D", "steps": [{"id": "one", "name": "One", "employee": "e", "instructions": "a"}, {"id": "two", "name": "Two", "employee": "e", "instructions": "b"}, {"id": "three", "name": "Three", "employee": "e", "instructions": "c"}, {"id": "four", "name": "Four", "employee": "e", "instructions": "d"}]})


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({"id": "e", "name": "E", "role": "R", "instructions": "system", "model": "model", "allowed_tools": ["tool"]})


def setup(tmp_path: Path) -> dict[str, object]:
    state = WorkflowExecutionState("w", "running", "three", 3, "e", ("one", "two"), None)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text(serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", "o", None)) + serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "two", 2, "e", "running", "succeeded", "openai", None, "r2", "q2", "o2", None)), encoding="utf-8")
    request = ModelInvocationRequest("model", "system", "c", ("tool",))
    tools = (ToolDefinition("tool", "Tool", ()),)
    return {"result": RunningStatePersistenceResult(len(state_path.read_bytes())), "start": PreparedStepExecutionStart(request, state), "workflow": workflow(), "employee": employee(), "state_path": state_path, "events_path": events_path, "resolved_tools": tools, "api_key": OpenAIApiKey(value=SecretStr("synthetic")), "approval": approve_model_invocation_execution(request, tools, provider="openai", approved_by="test", approval_id="id"), "transport": lambda _: None}


def runtime() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess("w", "three", 3, "e", ModelInvocationSuccess("openai", "response", None, "completed", ("ok",), "ok"))


def runtime_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure("w", "three", 3, "e", ModelInvocationFailure("openai", "api_error", "safe", None, None, None, None))


def reject(values: dict[str, object], classification: str, **changes: object) -> None:
    supplied = dict(values)
    supplied.update(changes)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime()

    supplied.setdefault("phase119_function", dependency)
    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**supplied)  # type: ignore[arg-type]
    assert caught.value.detail.classification == classification and calls == 0


def failed_targets(tmp_path: Path) -> tuple[Path, Path]:
    state = WorkflowExecutionState("w", "failed", "three", 3, "e", ("one", "two"), "api_error")
    state_path, events_path = tmp_path / "failed-state", tmp_path / "failed-events"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text("".join((
        serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", "o", None)),
        serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "two", 2, "e", "running", "succeeded", "openai", None, "r2", "q2", "o2", None)),
        serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_failed", "w", "three", 3, "e", "running", "failed", "openai", "api_error", None, "q", None, "safe")),
    )), encoding="utf-8")
    return state_path, events_path


def test_implementation_uses_only_public_phase119_dependency() -> None:
    source = Path("src/ai_office/engine/persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary.py").read_text()
    assert "route_persisted_running_execution_cycle_handoff_reentry_continuation_boundary" in source
    assert "phase112" not in source.lower()
    assert "route_persisted_running_execution_cycle_reentry_continuation_boundary" not in source
    assert "persisted_running_execution_cycle_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


def test_public_signature_and_canonical_ten_argument_identity(tmp_path: Path) -> None:
    params = list(inspect.signature(route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary).parameters.values())
    assert [p.name for p in params[:10]] == ["result", "start", "workflow", "employee", "state_path", "events_path", "resolved_tools", "api_key", "approval", "transport"]
    assert all(p.annotation is object for p in params[:10])
    assert params[10].kind is inspect.Parameter.KEYWORD_ONLY
    values = setup(tmp_path); calls: list[tuple[object, ...]] = []
    expected = runtime()
    def dependency(*args: object) -> object:
        calls.append(args); return expected
    actual = route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert actual is expected and len(calls) == 1
    assert all(left is right for left, right in zip(calls[0], (values["result"], values["start"], values["workflow"], values["employee"], values["state_path"], values["events_path"], values["resolved_tools"], values["api_key"], values["approval"], values["transport"]), strict=True))


def test_default_identity_and_non_callable_rejection(tmp_path: Path) -> None:
    parameter = inspect.signature(route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary).parameters["phase119_function"]
    assert parameter.default is route_persisted_running_execution_cycle_handoff_reentry_continuation_boundary
    values = setup(tmp_path)
    reject(values, "execution_inputs", phase119_function=object())


@pytest.mark.parametrize(("index", "step_id", "instructions", "completed"), [
    (1, "one", "a", ()),
    (2, "two", "b", ("one",)),
])
def test_indices_one_and_two_are_rejected_before_phase119(
    tmp_path: Path,
    index: int,
    step_id: str,
    instructions: str,
    completed: tuple[str, ...],
) -> None:
    values = setup(tmp_path)
    state = WorkflowExecutionState("w", "running", step_id, index, "e", completed, None)
    values["state_path"].write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")  # type: ignore[union-attr]
    values["events_path"].write_text("" if index == 1 else serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", "o", None)), encoding="utf-8")  # type: ignore[union-attr]
    request = replace(values["start"].request, task_instructions=instructions)  # type: ignore[union-attr]
    values["start"] = PreparedStepExecutionStart(request, state)
    values["result"] = RunningStatePersistenceResult(len(values["state_path"].read_bytes()))  # type: ignore[union-attr]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime()

    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "start_contract" and calls == 0


@pytest.mark.parametrize("field", ["result", "start", "workflow", "employee", "resolved_tools", "api_key", "approval", "transport"])
def test_execution_input_missing_is_rejected(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path)
    if field == "result": values["result"] = object()
    elif field == "transport": values[field] = None
    else: values[field] = None
    reject(values, {"result": "result_type", "start": "start_contract", "workflow": "workflow_definition", "employee": "employee_contract", "resolved_tools": "tools_contract", "api_key": "credential_contract", "approval": "approval_contract", "transport": "execution_inputs"}[field])


@pytest.mark.parametrize("field, value, classification", [
    ("result", ResultChild(1), "result_type"),
    ("start", StartChild(ModelInvocationRequest("model", "system", "c", ("tool",)), WorkflowExecutionState("w", "running", "three", 3, "e", ("one", "two"), None)), "start_contract"),
    ("workflow", WorkflowChild.model_validate(workflow().model_dump()), "workflow_definition"),
    ("employee", EmployeeChild.model_validate(employee().model_dump()), "employee_contract"),
    ("resolved_tools", (ToolChild("tool", "Tool", ()),), "tools_contract"),
    ("api_key", KeyChild(value=SecretStr("synthetic")), "credential_contract"),
])
def test_subclasses_rejected_before_dependency(tmp_path: Path, field: str, value: object, classification: str) -> None:
    values = setup(tmp_path); values[field] = value
    reject(values, classification)


@pytest.mark.parametrize("nested", ["request", "state", "approval"])
def test_exact_nested_models_are_rejected_before_phase119(tmp_path: Path, nested: str) -> None:
    values = setup(tmp_path)
    start = values["start"]
    if nested == "request":
        request = start.request  # type: ignore[union-attr]
        values["start"] = PreparedStepExecutionStart(RequestChild(request.model, request.system_instructions, request.task_instructions, request.allowed_tools), start.running_state)  # type: ignore[union-attr]
    elif nested == "state":
        running = start.running_state  # type: ignore[union-attr]
        values["start"] = PreparedStepExecutionStart(start.request, StateChild(running.workflow_id, running.status, running.current_step_id, running.current_step_index, running.current_employee_id, running.completed_step_ids, running.last_failure_category))  # type: ignore[union-attr]
    else:
        approval = values["approval"]
        values["approval"] = ApprovalChild(approval.approved, approval.provider, approval.request_fingerprint, approval.approved_by, approval.approval_id)  # type: ignore[union-attr]

    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime()

    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    classification = "approval_contract" if nested == "approval" else "start_contract"
    assert type(caught.value) is PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError
    assert caught.value.detail.classification == classification and calls == 0


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


def test_positive_persistence_byte_count_must_match_state_file(tmp_path: Path) -> None:
    values = setup(tmp_path)
    state_path = Path(str(values["state_path"]))
    values["result"] = RunningStatePersistenceResult(len(state_path.read_bytes()) + 1)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime()

    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "persistence_result_contract" and calls == 0


def test_mismatched_persisted_running_state_is_rejected_after_byte_count_match(tmp_path: Path) -> None:
    values = setup(tmp_path)
    state_path = Path(str(values["state_path"]))
    mismatched = WorkflowExecutionState("w", "running", "four", 4, "e", ("one", "two", "three"), None)
    state_path.write_text(serialize_workflow_execution_state_json(mismatched), encoding="utf-8")
    values["result"] = RunningStatePersistenceResult(len(state_path.read_bytes()))
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime()

    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "persistence_result_contract" and calls == 0


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"])
def test_predecessor_history_variants_rejected(tmp_path: Path, mutation: str) -> None:
    values = setup(tmp_path); events = values["events_path"]
    original = events.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    unrelated = serialize_runtime_step_event_jsonl(RuntimeStepEvent("unrelated", "other", "x", 9, "z", "ready", "running", "openai", None, "r", "q", "o", None))
    extra = serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "three", 3, "e", "running", "succeeded", "openai", None, "r3", "q3", "o3", None))
    if mutation == "duplicate": events.write_text(original + original, encoding="utf-8")
    elif mutation == "missing": events.write_text(lines[0], encoding="utf-8")
    elif mutation == "reordered": events.write_text(lines[1] + lines[0], encoding="utf-8")
    elif mutation == "unrelated": events.write_text(unrelated + lines[1], encoding="utf-8")
    elif mutation == "malformed": events.write_text("{malformed}\n", encoding="utf-8")
    else: events.write_text(original + extra, encoding="utf-8")

    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime()

    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "persistence_result_contract" and calls == 0


@pytest.mark.parametrize("output", ["", None, 123, 1.5])
def test_succeeded_predecessor_empty_output_is_accepted_and_non_string_rejected(tmp_path: Path, output: object) -> None:
    values = setup(tmp_path); events = values["events_path"]
    first = serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", "o", None))
    second = serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "two", 2, "e", "running", "succeeded", "openai", None, "r2", "q2", output, None))
    events.write_text(first + second, encoding="utf-8")
    calls = 0; expected = runtime()

    def dependency(*_: object) -> object:
        nonlocal calls; calls += 1; return expected

    if output == "":
        actual = route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
        assert actual is expected and calls == 1
    else:
        with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
            route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
        assert caught.value.detail.classification == "persistence_result_contract" and calls == 0


def test_earlier_succeeded_predecessor_empty_output_delegates_exactly_once_in_canonical_identity_order(tmp_path: Path) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    first = serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", "", None))
    second = serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "two", 2, "e", "running", "succeeded", "openai", None, "r2", "q2", "o2", None))
    events.write_text(first + second, encoding="utf-8")
    state_before = values["state_path"].read_bytes()
    events_before = events.read_bytes()
    transport_calls = 0

    def transport(_: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("transport must not be called")

    values["transport"] = transport
    calls: list[tuple[object, ...]] = []
    expected = runtime()

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    actual = route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert actual is expected and len(calls) == 1
    assert all(left is right for left, right in zip(calls[0], (values["result"], values["start"], values["workflow"], values["employee"], values["state_path"], values["events_path"], values["resolved_tools"], values["api_key"], values["approval"], values["transport"]), strict=True))
    assert values["state_path"].read_bytes() == state_before
    assert events.read_bytes() == events_before
    assert transport_calls == 0


def test_immediate_succeeded_predecessor_empty_output_delegates_exactly_once_in_canonical_identity_order(tmp_path: Path) -> None:
    values = setup(tmp_path)
    events = values["events_path"]
    first = serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", "o", None))
    second = serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "two", 2, "e", "running", "succeeded", "openai", None, "r2", "q2", "", None))
    events.write_text(first + second, encoding="utf-8")
    state_before = values["state_path"].read_bytes()
    events_before = events.read_bytes()
    transport_calls = 0

    def transport(_: object) -> object:
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("transport must not be called")

    values["transport"] = transport
    calls: list[tuple[object, ...]] = []
    expected = runtime()

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    actual = route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert actual is expected and len(calls) == 1
    assert all(left is right for left, right in zip(calls[0], (values["result"], values["start"], values["workflow"], values["employee"], values["state_path"], values["events_path"], values["resolved_tools"], values["api_key"], values["approval"], values["transport"]), strict=True))
    assert values["state_path"].read_bytes() == state_before
    assert events.read_bytes() == events_before
    assert transport_calls == 0


def test_exact_state_bytes_and_equal_path_identity_are_preserved(tmp_path: Path) -> None:
    values = setup(tmp_path)
    state = Path(str(values["state_path"]))
    events = Path(str(values["events_path"]))
    values["state_path"], values["events_path"] = state, events
    expected = runtime(); seen: list[tuple[object, ...]] = []
    actual = route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=lambda *args: (seen.append(args), expected)[1])  # type: ignore[arg-type]
    assert actual is expected and seen[0][4] is state and seen[0][5] is events


@pytest.mark.parametrize("value", [runtime(), runtime_failure()])
def test_valid_runtime_success_and_failure_are_returned_by_identity(tmp_path: Path, value: object) -> None:
    values = setup(tmp_path)
    actual = route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=lambda *_: value)  # type: ignore[arg-type]
    assert actual is value


@pytest.mark.parametrize("value", [RuntimeSuccessChild("w", "three", 3, "e", runtime().invocation_result), RuntimeFailureChild("w", "three", 3, "e", runtime_failure().invocation_result), SimpleNamespace(workflow_id="w", step_id="two", step_index=2, employee_id="e")])
def test_runtime_subclasses_and_attribute_substitutes_are_rejected(tmp_path: Path, value: object) -> None:
    values = setup(tmp_path)
    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=lambda *_: value)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "runtime_contract"


@pytest.mark.parametrize("field", ["workflow_id", "step_id", "step_index", "employee_id"])
def test_runtime_identity_mismatch_is_rejected(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path); replacement = {"workflow_id": "other", "step_id": "other", "step_index": 1, "employee_id": "other"}[field]
    bad = replace(runtime(), **{field: replacement})
    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=lambda *_: bad)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "runtime_contract"


def test_malformed_runtime_failure_is_safely_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path)
    malformed = replace(
        runtime_failure(),
        invocation_result=replace(runtime_failure().invocation_result, category=[]),  # type: ignore[arg-type]
    )
    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(
            **values, phase119_function=lambda *_: malformed  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "runtime_contract"


def test_nested_running_state_field_types_are_strict(tmp_path: Path) -> None:
    values = setup(tmp_path)
    values["start"] = replace(
        values["start"],
        running_state=replace(values["start"].running_state, status=object()),  # type: ignore[union-attr]
    )
    reject(values, "start_contract")


def test_persisted_failure_stop_route_is_unchanged_and_zero_call(tmp_path: Path) -> None:
    values = setup(tmp_path); state, events = failed_targets(tmp_path)
    result = PersistedExecutionOutcome("persisted_failure", "w", "three", 3, "e", "api_error")
    calls = 0
    def dependency(*_: object) -> object:
        nonlocal calls; calls += 1; return runtime()
    actual = route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(result, None, values["workflow"], None, state, events, None, None, None, None, phase119_function=dependency)  # type: ignore[arg-type]
    assert actual is result and calls == 0


@pytest.mark.parametrize("field", ["start", "employee", "resolved_tools", "api_key", "approval", "transport"])
def test_each_execution_only_input_is_rejected_on_stop(tmp_path: Path, field: str) -> None:
    values = setup(tmp_path); state, events = failed_targets(tmp_path)
    result = PersistedExecutionOutcome("persisted_failure", "w", "three", 3, "e", "api_error")
    replacement = values[field] if field != "transport" else (lambda _: None)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime()

    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(result, replacement if field == "start" else None, values["workflow"], replacement if field == "employee" else None, state, events, replacement if field == "resolved_tools" else None, replacement if field == "api_key" else None, replacement if field == "approval" else None, replacement if field == "transport" else None, phase119_function=dependency)  # type: ignore[arg-type]
    assert type(caught.value) is PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError
    assert caught.value.detail.classification == "execution_inputs" and calls == 0


def test_stop_target_mismatch_and_execution_input_combination_are_rejected(tmp_path: Path) -> None:
    values = setup(tmp_path); state, events = failed_targets(tmp_path)
    result = PersistedExecutionOutcome("persisted_failure", "w", "three", 3, "e", "api_error")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime()

    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError):
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(result, values["start"], values["workflow"], values["employee"], state, events, values["resolved_tools"], values["api_key"], values["approval"], values["transport"], phase119_function=dependency)  # type: ignore[arg-type]
    assert calls == 0


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


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_safe_phase119_error_identity_matrix_restores_without_retry(tmp_path: Path, mutation: str | None) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()
    supplied_error = PersistedRunningExecutionCycleHandoffReentryContinuationError("safe")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"): state.write_bytes(b"state-mutated")
        if mutation in ("events", "both"): events.write_bytes(b"events-mutated")
        raise supplied_error

    with pytest.raises(PersistedRunningExecutionCycleHandoffReentryContinuationError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert type(caught.value) is PersistedRunningExecutionCycleHandoffReentryContinuationError
    assert caught.value is supplied_error and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once(tmp_path: Path, failed_target: str, monkeypatch: pytest.MonkeyPatch) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    calls = {"state": 0, "events": 0}
    dependency_calls = 0
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
        nonlocal dependency_calls
        dependency_calls += 1
        original_write(state, b"mutated")
        original_write(events, b"mutated")
        raise RuntimeError("unexpected")
    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert type(caught.value) is PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls["state"] == 1 and calls["events"] == 1
    assert dependency_calls == 1


@pytest.mark.parametrize("runtime_result", [runtime(), runtime_failure()])
@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_runtime_result_target_mutation_is_compensated_without_retry(
    tmp_path: Path,
    runtime_result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    mutation: str,
) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()
    dependency_calls = 0

    def dependency(*_: object) -> object:
        nonlocal dependency_calls
        dependency_calls += 1
        if mutation in ("state", "both"):
            state.write_bytes(b"state-mutated")
        if mutation in ("events", "both"):
            events.write_bytes(b"events-mutated")
        return runtime_result

    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert type(caught.value) is PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError
    assert caught.value.detail.classification == "runtime_contract"
    assert dependency_calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("decision", ["persisted_success", "stopped_failed", "not_progressable"])
def test_unsupported_stop_progression_is_zero_call_rejected(tmp_path: Path, decision: str) -> None:
    values = setup(tmp_path)
    result = WorkflowProgressionDecision(decision, "w", "three", 3, "e", None, None, None, "reason")  # type: ignore[arg-type]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return runtime()

    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**{**values, "result": result}, phase119_function=dependency)  # type: ignore[arg-type]
    assert type(caught.value) is PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError
    assert caught.value.detail.classification == "completion_contract" and calls == 0


def test_stop_routes_are_unchanged_and_zero_call(tmp_path: Path) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    state.write_text(serialize_workflow_execution_state_json(WorkflowExecutionState("w", "succeeded", "four", 4, "e", ("one", "two", "three", "four"), None)), encoding="utf-8")
    events.write_text("".join((
        serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r1", "q1", "o1", None)),
        serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "two", 2, "e", "running", "succeeded", "openai", None, "r2", "q2", "o2", None)),
        serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "three", 3, "e", "running", "succeeded", "openai", None, "r3", "q3", "o3", None)),
        serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "four", 4, "e", "running", "succeeded", "openai", None, "r4", "q4", "o4", None)),
    )), encoding="utf-8")
    result = WorkflowProgressionDecision("workflow_complete", "w", "four", 4, "e", None, None, None, "last_step_succeeded")
    calls = 0
    def dependency(*_: object) -> object:
        nonlocal calls; calls += 1; return runtime()
    actual = route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(result, None, values["workflow"], None, state, events, None, None, None, None, phase119_function=dependency)  # type: ignore[arg-type]
    assert actual is result and calls == 0


@pytest.mark.parametrize("mutation", [None, "state", "events", "both"])
def test_unexpected_dependency_error_is_sanitized_and_restored(tmp_path: Path, mutation: str | None) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in ("state", "both"): state.write_bytes(b"state-mutated")
        if mutation in ("events", "both"): events.write_bytes(b"events-mutated")
        raise RuntimeError("secret detail")

    with pytest.raises(PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError) as caught:
        route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary(**values, phase119_function=dependency)  # type: ignore[arg-type]
    assert type(caught.value) is PersistedRunningExecutionCycleHandoffChainReentryContinuationCompatibilityError
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before
