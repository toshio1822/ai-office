"""Phase 154 whole-chain real-default empty-success regression (Issue #314).

The final integration/closure proof for the Phase 140-153 empty-success
compatibility line. This regression calls only the public Phase 141 entry
with its real default dependency chain and exercises the full path from the
persisted-running execution boundary down to the actual execution path.

The ONLY synthetic seam is the final ``transport`` callable, which receives
the real authenticated OpenAI request exactly once and returns a deterministic
synthetic OpenAI HTTP response. No Phase boundary is overridden, faked, or
monkeypatched, and no real provider/network/tool call is made.
"""

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

_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_STEP_NAMES = ("One", "Two", "Three", "Four", "Five", "Six")
_STEP_INSTRUCTIONS = ("a", "b", "c", "d", "e", "f")


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": step_id, "name": name, "employee": "e", "instructions": instructions}
                for step_id, name, instructions in zip(
                    _STEP_IDS, _STEP_NAMES, _STEP_INSTRUCTIONS, strict=True
                )
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


def _predecessor_event(
    step_id: str,
    step_index: int,
    *,
    request_id: object,
    output_text: object,
) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        step_index,
        "e",
        "running",
        "succeeded",
        "openai",
        None,
        f"response-{step_index}",
        request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        None,
    )


def make_inputs(
    tmp_path: Path,
    *,
    empty_indexes: tuple[int, ...] = (),
    immediate_request_id: object = None,
    immediate_output: object = "output-5",
    replacement: tuple[int, object] | None = None,
) -> dict[str, object]:
    """Running state at step six with succeeded history for steps one through five.

    ``empty_indexes`` (1-based, steps 1..5) marks predecessors whose
    ``output_text`` is the exact empty built-in ``str``; every other output is
    an exact non-empty string. ``replacement`` optionally overrides one
    predecessor's output for the rejection cases. Earlier request IDs stay
    exact non-empty built-in strings; the immediate step-5 request ID uses the
    supplied Phase 149-valid provenance (``None`` or an exact non-empty str).
    """
    state_value = WorkflowExecutionState(
        "w",
        "running",
        "six",
        6,
        "e",
        _STEP_IDS[:5],
        None,
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(
        serialize_workflow_execution_state_json(state_value), encoding="utf-8"
    )
    events = []
    for index, step_id in enumerate(_STEP_IDS[:5], start=1):
        if replacement is not None and replacement[0] == index:
            output = replacement[1]
        elif index == 5:
            output = immediate_output
        else:
            output = "" if index in empty_indexes else f"output-{index}"
        events.append(
            _predecessor_event(
                step_id,
                index,
                request_id=immediate_request_id if index == 5 else f"request-{index}",
                output_text=output,
            )
        )
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
    """Synthetic final transport: records the real request, returns a fixed response."""

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


def _run_whole_chain(values: dict[str, object], calls: list[OpenAIResponsesAuthenticatedHttpRequest]) -> object:
    """Route through the public Phase 141 entry with real default dependencies.

    No ``phase133_function`` override and no monkeypatching: the only synthetic
    seam is the final ``transport`` callable.
    """
    return route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        **values,  # type: ignore[arg-type]
        transport=success_transport(calls),
    )


def _assert_exact_success(
    result: object,
    values: dict[str, object],
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
    assert isinstance(calls[0], OpenAIResponsesAuthenticatedHttpRequest)
    state = values["state_path"]
    events = values["events_path"]
    assert state.read_bytes() == state_before  # type: ignore[union-attr]
    assert events.read_bytes() == events_before  # type: ignore[union-attr]


def test_earlier_empty_predecessor_reaches_real_transport_once(
    tmp_path: Path,
) -> None:
    """Case 1: step-2 output empty; immediate step-5 request_id is a non-empty str."""
    values = make_inputs(tmp_path, empty_indexes=(2,), immediate_request_id="request-5")
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    result = _run_whole_chain(values, calls)
    _assert_exact_success(result, values, state_before, events_before, calls)


def test_immediate_empty_predecessor_with_none_request_id_reaches_real_transport_once(
    tmp_path: Path,
) -> None:
    """Case 2: step-5 output empty with Phase 149 ``request_id is None``.

    The critical combined regression proving the Phase 149 request-ID repair
    and the Phase 150-153 empty-output repairs compose correctly.
    """
    values = make_inputs(tmp_path, empty_indexes=(5,), immediate_request_id=None)
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    result = _run_whole_chain(values, calls)
    _assert_exact_success(result, values, state_before, events_before, calls)


def test_multiple_earlier_empty_predecessors_reach_real_transport_once(
    tmp_path: Path,
) -> None:
    """Case 3: steps 2 and 4 output empty; immediate output non-empty, request_id None."""
    values = make_inputs(tmp_path, empty_indexes=(2, 4), immediate_request_id=None)
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    result = _run_whole_chain(values, calls)
    _assert_exact_success(result, values, state_before, events_before, calls)


def test_earlier_and_immediate_empty_predecessors_reach_real_transport_once(
    tmp_path: Path,
) -> None:
    """Case 4: step-2 and step-5 output empty together; immediate request_id None."""
    values = make_inputs(tmp_path, empty_indexes=(2, 5), immediate_request_id=None)
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    result = _run_whole_chain(values, calls)
    _assert_exact_success(result, values, state_before, events_before, calls)


@pytest.mark.parametrize("invalid_output", [None, 123])
def test_invalid_predecessor_output_rejected_before_transport(
    tmp_path: Path,
    invalid_output: object,
) -> None:
    """Cases 5-6: None / non-string predecessor output stays rejected pre-transport."""
    values = make_inputs(
        tmp_path,
        empty_indexes=(),
        immediate_request_id="request-5",
        replacement=(2, invalid_output),
    )
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    with pytest.raises(
        PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        _run_whole_chain(values, calls)
    assert caught.value.detail.classification == "persistence_result_contract"
    assert calls == []
    assert values["state_path"].read_bytes() == state_before  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == events_before  # type: ignore[union-attr]
