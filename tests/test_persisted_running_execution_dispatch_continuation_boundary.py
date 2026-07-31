"""Focused Phase 98 boundary tests."""

# ruff: noqa: E501,E702,F401,I001

import inspect
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedRunningExecutionDispatchContinuationCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_persisted_running_execution_dispatch_continuation_boundary,
)
from ai_office.invocation import ModelInvocationRequest, ModelInvocationSuccess, approve_model_invocation_execution
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import RuntimeStepEvent, StepRuntimeExecutionSuccess, WorkflowExecutionState
from ai_office.storage import RunningStatePersistenceResult, serialize_runtime_step_event_jsonl, serialize_workflow_execution_state_json
from ai_office.tools import ToolDefinition


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


def test_public_signature_and_canonical_ten_argument_identity(tmp_path: Path) -> None:
    params = list(inspect.signature(route_persisted_running_execution_dispatch_continuation_boundary).parameters.values())
    assert [p.name for p in params[:10]] == ["result", "start", "workflow", "employee", "state_path", "events_path", "resolved_tools", "api_key", "approval", "transport"]
    assert params[10].kind is inspect.Parameter.KEYWORD_ONLY
    values = setup(tmp_path); calls: list[tuple[object, ...]] = []
    expected = runtime()
    def dependency(*args: object) -> object:
        calls.append(args); return expected
    actual = route_persisted_running_execution_dispatch_continuation_boundary(**values, phase91_function=dependency)  # type: ignore[arg-type]
    assert actual is expected and len(calls) == 1
    assert all(left is right for left, right in zip(calls[0], (values["result"], values["start"], values["workflow"], values["employee"], values["state_path"], values["events_path"], values["resolved_tools"], values["api_key"], values["approval"], values["transport"]), strict=True))


def test_stop_routes_are_unchanged_and_zero_call(tmp_path: Path) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    state.write_text(serialize_workflow_execution_state_json(WorkflowExecutionState("w", "succeeded", "two", 2, "e", ("one", "two"), None)), encoding="utf-8")
    events.write_text("".join((serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "one", 1, "e", "running", "succeeded", "openai", None, "r", "q", "o", None)), serialize_runtime_step_event_jsonl(RuntimeStepEvent("step_succeeded", "w", "two", 2, "e", "running", "succeeded", "openai", None, "r2", "q", "o", None)))), encoding="utf-8")
    result = WorkflowProgressionDecision("workflow_complete", "w", "two", 2, "e", None, None, None, "last_step_succeeded")
    calls = 0
    def dependency(*_: object) -> object:
        nonlocal calls; calls += 1; return runtime()
    actual = route_persisted_running_execution_dispatch_continuation_boundary(result, None, values["workflow"], None, state, events, None, None, None, None, phase91_function=dependency)  # type: ignore[arg-type]
    assert actual is result and calls == 0


@pytest.mark.parametrize("mutation", ["state", "events"])
def test_unexpected_dependency_error_is_sanitized_and_restored(tmp_path: Path, mutation: str) -> None:
    values = setup(tmp_path); state, events = values["state_path"], values["events_path"]
    before = state.read_bytes(), events.read_bytes()
    def dependency(*_: object) -> object:
        (state if mutation == "state" else events).write_bytes(b"mutated")
        raise RuntimeError("secret")
    with pytest.raises(PersistedRunningExecutionDispatchContinuationCompatibilityError) as caught:
        route_persisted_running_execution_dispatch_continuation_boundary(**values, phase91_function=dependency)  # type: ignore[arg-type]
    assert caught.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == before
