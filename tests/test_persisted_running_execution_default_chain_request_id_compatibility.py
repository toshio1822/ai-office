"""Real-default-chain regression for the Issue #304 (Phase 149) compatibility repair."""

# ruff: noqa: E501

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "a"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "b"},
                {"id": "three", "name": "Three", "employee": "e", "instructions": "c"},
                {"id": "four", "name": "Four", "employee": "e", "instructions": "d"},
                {"id": "five", "name": "Five", "employee": "e", "instructions": "e"},
                {"id": "six", "name": "Six", "employee": "e", "instructions": "f"},
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "e",
            "name": "E",
            "role": "R",
            "instructions": "system",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def _event(
    step_id: str,
    step_index: int,
    provider: object = "openai",
    request_id: object = "request",
    output_text: object = "output",
) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        step_index,
        "e",
        "running",
        "succeeded",
        provider,  # type: ignore[arg-type]
        None,
        f"response-{step_index}",
        request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        None,
    )


def make_inputs(
    tmp_path: Path,
    *,
    immediate_request_id: object = None,
    earlier_request_id: object = "request",
) -> dict[str, object]:
    """Running state at step six with succeeded history for steps one through five.

    Every predecessor keeps an exact non-empty built-in ``str`` ``output_text`` so the
    known Phase 126-and-below empty-output contract gap stays isolated from this repair.
    Earlier predecessors use a non-openai provider with exact non-empty request IDs;
    the immediately preceding step-5 event uses exact provider ``"openai"``, exact
    non-empty response ID, exact non-empty output text, and the supplied ``request_id``.
    """
    state_value = WorkflowExecutionState(
        "w",
        "running",
        "six",
        6,
        "e",
        ("one", "two", "three", "four", "five"),
        None,
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(
        serialize_workflow_execution_state_json(state_value), encoding="utf-8"
    )
    events = [
        _event("one", 1, "other", earlier_request_id),
        _event("two", 2, "other", earlier_request_id),
        _event("three", 3, "other", earlier_request_id),
        _event("four", 4, "other", earlier_request_id),
        _event("five", 5, "openai", immediate_request_id),
    ]
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    request = ModelInvocationRequest("model", "system", "f", ("tool",))
    tools = (ToolDefinition("tool", "Tool", ()),)
    return {
        "result": RunningStatePersistenceResult(len(state_path.read_bytes())),
        "start": PreparedStepExecutionStart(request, state_value),
        "workflow": workflow(),
        "employee": employee(),
        "state_path": state_path,
        "events_path": events_path,
        "resolved_tools": tools,
        "api_key": OpenAIApiKey(value=SecretStr("synthetic")),
        "approval": approve_model_invocation_execution(
            request,
            tools,
            provider="openai",
            approved_by="test",
            approval_id="approval-id",
        ),
    }


def success_transport(
    calls: list[OpenAIResponsesAuthenticatedHttpRequest],
) -> object:
    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        calls.append(request_value)
        return OpenAIResponsesRawHttpResponse(
            status_code=200,
            reason="synthetic",
            headers=(("x-request-id", "request_123"),),
            body=json.dumps(
                {
                    "id": "resp_123",
                    "object": "response",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode(),
        )

    return transport


def _assert_exact_success(
    result: object,
    state: object,
    events: object,
    state_before: bytes,
    events_before: bytes,
    calls: list[OpenAIResponsesAuthenticatedHttpRequest],
) -> None:
    assert isinstance(result, StepRuntimeExecutionSuccess)
    assert (
        result.workflow_id,
        result.step_id,
        result.step_index,
        result.employee_id,
    ) == ("w", "six", 6, "e")
    invocation = result.invocation_result
    assert invocation.provider == "openai"
    assert invocation.response_id == "resp_123"
    assert invocation.request_id == "request_123"
    assert invocation.status == "completed"
    assert invocation.text == "ok"
    assert len(calls) == 1
    assert state.read_bytes() == state_before  # type: ignore[union-attr]
    assert events.read_bytes() == events_before  # type: ignore[union-attr]


def test_immediate_none_request_id_delegates_through_real_default_chain(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path)
    state = values["state_path"]
    events = values["events_path"]
    state_before = state.read_bytes()  # type: ignore[union-attr]
    events_before = events.read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    result = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values,  # type: ignore[arg-type]
        transport=success_transport(calls),
    )
    _assert_exact_success(result, state, events, state_before, events_before, calls)


def test_immediate_nonempty_request_id_still_delegates_through_real_default_chain(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, immediate_request_id="request-5")
    state = values["state_path"]
    events = values["events_path"]
    state_before = state.read_bytes()  # type: ignore[union-attr]
    events_before = events.read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    result = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values,  # type: ignore[arg-type]
        transport=success_transport(calls),
    )
    _assert_exact_success(result, state, events, state_before, events_before, calls)


def test_earlier_none_request_id_is_rejected_before_transport(
    tmp_path: Path,
) -> None:
    values = make_inputs(tmp_path, earlier_request_id=None)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    with pytest.raises(
        PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            **values,  # type: ignore[arg-type]
            transport=success_transport(calls),
        )
    assert caught.value.detail.classification == "persistence_result_contract"
    assert calls == []
