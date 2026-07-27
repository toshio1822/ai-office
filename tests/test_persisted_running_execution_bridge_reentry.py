"""Phase 49 delegates only to injected Phase 42 fakes."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedRunningExecutionBridgeCompatibilityError,
    PreparedStepExecutionStart,
    route_persisted_running_execution_bridge_reentry,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import StepRuntimeExecutionSuccess, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition


def test_running_result_delegates_exact_objects_once(tmp_path: Path) -> None:
    workflow = WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "task"}
            ],
        }
    )
    employee = EmployeeDefinition.model_validate(
        {
            "id": "e",
            "name": "E",
            "role": "R",
            "instructions": "system",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )
    request = ModelInvocationRequest("model", "system", "task", ("tool",))
    start = PreparedStepExecutionStart(
        request, WorkflowExecutionState("w", "running", "one", 1, "e", (), None)
    )
    tools = (ToolDefinition("tool", "Tool", ()),)
    approval = approve_model_invocation_execution(
        request, tools, provider="openai", approved_by="test", approval_id="id"
    )
    key = OpenAIApiKey(value=SecretStr("synthetic"))
    state, events = tmp_path / "state", tmp_path / "events"
    state.write_text(serialize_workflow_execution_state_json(start.running_state))
    events.write_bytes(b"")
    persisted = RunningStatePersistenceResult(len(state.read_bytes()))
    expected = StepRuntimeExecutionSuccess(
        "w",
        "one",
        1,
        "e",
        ModelInvocationSuccess("openai", "response", None, "completed", ("ok",), "ok"),
    )
    calls = 0

    def phase42(*args: object) -> StepRuntimeExecutionSuccess:
        nonlocal calls
        calls += 1
        assert args == (
            persisted,
            start,
            workflow,
            employee,
            state,
            events,
            tools,
            key,
            approval,
            transport,
        )
        return expected

    def transport(_: object) -> object:
        raise AssertionError("transport must not run")

    assert (
        route_persisted_running_execution_bridge_reentry(
            persisted,
            start,
            workflow,
            employee,
            state,
            events,
            tools,
            key,
            approval,
            transport,
            execution_routing_function=phase42,
        )
        is expected
    )
    assert calls == 1


def test_running_history_rejects_extra_event_before_phase42(tmp_path: Path) -> None:
    workflow = WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "task"}
            ],
        }
    )
    employee = EmployeeDefinition.model_validate(
        {
            "id": "e",
            "name": "E",
            "role": "R",
            "instructions": "system",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )
    request = ModelInvocationRequest("model", "system", "task", ("tool",))
    start = PreparedStepExecutionStart(
        request, WorkflowExecutionState("w", "running", "one", 1, "e", (), None)
    )
    tools = (ToolDefinition("tool", "Tool", ()),)
    approval = approve_model_invocation_execution(
        request, tools, provider="openai", approved_by="test", approval_id="id"
    )
    state, events = tmp_path / "state", tmp_path / "events"
    state.write_text(serialize_workflow_execution_state_json(start.running_state))
    events.write_bytes(b"extra")
    with pytest.raises(PersistedRunningExecutionBridgeCompatibilityError) as caught:
        route_persisted_running_execution_bridge_reentry(
            RunningStatePersistenceResult(len(state.read_bytes())),
            start,
            workflow,
            employee,
            state,
            events,
            tools,
            OpenAIApiKey(value=SecretStr("synthetic")),
            approval,
            lambda _: None,
            execution_routing_function=lambda *_: pytest.fail("called"),
        )
    assert caught.value.detail.classification == "persistence_contract"
