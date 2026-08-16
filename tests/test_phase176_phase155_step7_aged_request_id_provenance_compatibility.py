"""Real whole-chain regression tests for Issue #371.

Phase 176 durably advances a canonical Phase-155 history from running step 6
to running step 7; the previously-valid step-5 immediate predecessor
(``request_id=None``, provider ``"openai"``) ages into an *earlier*
predecessor that Phase 155/141/133 still reject before this change.  These
tests run the real Phase 176 boundary with a capture-only delegating Phase
147 adapter, then the real Phase 155 execution chain through the synthetic
final transport, proving the aged step-5 provenance survives the whole
Phase 176 -> Phase 155 -> ... -> execution handoff.

Requirement-to-test mapping (Issue #371):
- aged step-5 ``request_id=None`` + immediate step-6 non-empty request ID
  -> exact StepRuntimeExecutionSuccess with transport exactly once
  -> test_real_chain_step7_aged_step5_none_success_transport_once
- same history with deterministic provider failure -> exact
  StepRuntimeExecutionFailure with transport exactly once
  -> test_real_chain_step7_aged_step5_none_deterministic_failure
- accumulated step-5 ``request_id=None`` AND immediate step-6
  ``request_id=None`` -> transport exactly once
  -> test_real_chain_step7_accumulated_none_request_ids_transport_once
- pre-threshold step-4 ``request_id=None`` remains rejected at the Phase 155
  boundary (positions 1-4 stay strict) -> persistence_result_contract
  -> test_real_chain_step7_prethreshold_step4_none_rejected
"""

# ruff: noqa: E501,E701,E702,F401,I001

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_runtime_result_to_prepared_start_persistence_orchestration_boundary,
)
from ai_office.engine.next_step_preparation import NextStepPreparationApproval
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as real_phase147,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesAuthenticatedHttpRequest,
    OpenAIResponsesRawHttpResponse,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition

phase176 = route_runtime_result_to_prepared_start_persistence_orchestration_boundary
phase155 = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary


def workflow(steps: int = 7) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{i}",
                    "name": f"Step {i}",
                    "employee": f"e{i}",
                    "instructions": f"step-{i}",
                }
                for i in range(1, steps + 1)
            ],
        }
    )


def predecessor_event(step_id: str, index: int, **changes: object) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            step_id,
            index,
            f"e{index}",
            "running",
            "succeeded",
            "openai",
            None,
            f"response-{step_id}",
            f"request-{step_id}",
            f"output-{step_id}",
            None,
        ),
        **changes,  # type: ignore[arg-type]
    )


def canonical_running_setup(
    root: Path, steps: int = 7, current: int = 6
) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    state_path, events_path = root / "state", root / "events"
    wf = workflow(steps)
    state = WorkflowExecutionState(
        "w",
        "running",
        wf.steps[current - 1].id,
        current,
        wf.steps[current - 1].employee,
        tuple(step.id for step in wf.steps[: current - 1]),
        None,
    )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events = []
    for index in range(1, current):
        if index == current - 1:
            events.append(
                predecessor_event(
                    wf.steps[index - 1].id, index, output_text="", request_id=None
                )
            )
        elif index in (2, 3, 4):
            events.append(predecessor_event(wf.steps[index - 1].id, index, output_text=""))
        else:
            events.append(predecessor_event(wf.steps[index - 1].id, index))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    return {
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
        "state_before": state_path.read_bytes(),
        "events_before": events_path.read_bytes(),
    }


def runtime_success(
    wf: WorkflowDefinition, index: int, request_id_none: bool = False
) -> StepRuntimeExecutionSuccess:
    step = wf.steps[index - 1]
    return StepRuntimeExecutionSuccess(
        "w",
        step.id,
        index,
        step.employee,
        ModelInvocationSuccess(
            "openai",
            f"response-{step.id}",
            None if request_id_none else f"request-{step.id}",
            "completed",
            ("output",),
            "output",
        ),
    )


def prepare_decision(wf: WorkflowDefinition, index: int) -> WorkflowProgressionDecision:
    current = wf.steps[index - 1]
    following = wf.steps[index]
    return WorkflowProgressionDecision(
        "prepare_next_step",
        "w",
        current.id,
        index,
        current.employee,
        following.id,
        index + 1,
        following.employee,
        "next_step_available",
    )


def approval_for(decision: WorkflowProgressionDecision) -> NextStepPreparationApproval:
    return NextStepPreparationApproval(
        True,
        decision.workflow_id,
        decision.current_step_id,
        decision.current_step_index,
        decision.next_step_id,
        decision.next_step_index,
        decision.next_employee_id,
    )


def employee_for(decision: WorkflowProgressionDecision) -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": decision.next_employee_id,
            "name": "Next Employee",
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


TOOLS = (
    ToolDefinition("tool-one", "Tool One", ()),
    ToolDefinition("tool-two", "Tool Two", ()),
)


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


def failure_transport(
    calls: list[OpenAIResponsesAuthenticatedHttpRequest],
) -> object:
    def transport(
        request_value: OpenAIResponsesAuthenticatedHttpRequest,
    ) -> OpenAIResponsesRawHttpResponse:
        calls.append(request_value)
        return OpenAIResponsesRawHttpResponse(
            status_code=500,
            reason="synthetic error",
            headers=(("x-request-id", "request_123"),),
            body=json.dumps(
                {
                    "error": {
                        "message": "safe failure",
                        "type": "server_error",
                        "param": None,
                        "code": None,
                    }
                },
                ensure_ascii=False,
            ).encode(),
        )

    return transport


def chain_inputs(
    tmp_path: Path, step6_request_id_none: bool = False
) -> dict[str, object]:
    """Run real Phase 176 (capture-only delegating Phase 147) -> step-7 inputs.

    ``step6_request_id_none`` supplies the step-6 ``StepRuntimeExecutionSuccess``
    with ``request_id=None`` at Phase 176 input time (real flow); Phase 161
    persists the step-6 succeeded event as-is, with no post-hoc rewrite.
    """
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=step6_request_id_none)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    captured: list[object] = []

    def capture_phase147(
        res: object, workflow: object, emp: object, sp: object, ep: object
    ) -> object:
        captured.append(res)
        return real_phase147(res, workflow, emp, sp, ep)

    out = phase176(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        phase147_function=capture_phase147,  # type: ignore[arg-type]
    )
    assert type(out) is RunningStatePersistenceResult
    assert len(captured) == 1
    start = captured[0]
    assert type(start) is PreparedStepExecutionStart
    # Issue #371 minimum proofs (helper-level, shared by all four cases):
    # - final running state: step 7 with exact completed prefix steps 1-6
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "running"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    assert final_state.completed_step_ids == tuple(step.id for step in wf.steps[:6])
    # - events: exact succeeded steps 1-6
    events = load_events(events_path)
    assert [event.step_id for event in events] == [step.id for step in wf.steps[:6]]
    assert all(event.event_type == "step_succeeded" for event in events)
    # - step5 (aged earlier predecessor): request_id=None / provider="openai"
    step5 = events[4]
    assert step5.request_id is None
    assert step5.provider == "openai"
    # - immediate step6 provenance: provider openai; request_id per the real
    #   flow flag (non-empty canonical, or None for the accumulated case)
    step6 = events[5]
    assert step6.provider == "openai"
    if step6_request_id_none:
        assert step6.request_id is None
    else:
        assert isinstance(step6.request_id, str) and step6.request_id
    # - actual Phase176 result.state_bytes_written == persisted running-state bytes
    assert out.state_bytes_written == len(state_path.read_bytes())
    inputs = {
        "result": out,  # exact Phase176 return value, never rebuilt
        "start": start,
        "workflow": wf,
        "employee": employee,
        "state_path": state_path,
        "events_path": events_path,
        "resolved_tools": TOOLS,
        "api_key": OpenAIApiKey(value=SecretStr("synthetic")),
        "approval": approve_model_invocation_execution(
            start.request,  # type: ignore[attr-defined]
            TOOLS,
            provider="openai",
            approved_by="test",
            approval_id="approval-id",
        ),
    }
    # - Phase147 capture adapter received the exact PreparedStepExecutionStart
    #   that is forwarded to Phase155 (same object, no reconstruction)
    assert inputs["start"] is captured[0]
    assert inputs["result"] is out
    return inputs


def load_events(events_path: Path) -> list[RuntimeStepEvent]:
    """Load all JSONL runtime step events in file order."""
    return [
        RuntimeStepEvent(**json.loads(line))
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def replace_event_line(
    inputs: dict[str, object], index: int, event: RuntimeStepEvent
) -> None:
    events_path = inputs["events_path"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    lines[index] = serialize_runtime_step_event_jsonl(event)
    events_path.write_text("".join(lines), encoding="utf-8")  # type: ignore[union-attr]


def test_real_chain_step7_aged_step5_none_success_transport_once(tmp_path: Path) -> None:
    inputs = chain_inputs(tmp_path)
    state_path = inputs["state_path"]
    events_path = inputs["events_path"]
    # Issue #371 minimum proofs (inline): canonical six-event succeeded history
    # with aged step-5 request_id=None / provider="openai"; immediate step-6
    # keeps its canonical non-empty request_id.
    events = load_events(events_path)  # type: ignore[arg-type]
    assert [event.step_id for event in events] == [f"step-{i}" for i in range(1, 7)]
    assert all(event.event_type == "step_succeeded" for event in events)
    assert events[4].request_id is None and events[4].provider == "openai"
    assert isinstance(events[5].request_id, str) and events[5].request_id
    assert events[5].provider == "openai"
    before = state_path.read_bytes(), events_path.read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    result = phase155(  # type: ignore[call-overload]
        **inputs, transport=success_transport(calls)
    )
    assert type(result) is StepRuntimeExecutionSuccess
    assert result.workflow_id == "w"
    assert result.step_id == "step-7"
    assert result.step_index == 7
    assert result.employee_id == "e7"
    invocation = result.invocation_result
    assert invocation.provider == "openai"
    assert invocation.response_id == "resp_123"
    assert invocation.request_id == "request_123"
    assert len(calls) == 1
    assert (state_path.read_bytes(), events_path.read_bytes()) == before  # type: ignore[union-attr]
    # step7 runtime-result persistence NOT performed: six-event history intact
    after = load_events(events_path)  # type: ignore[arg-type]
    assert [event.step_id for event in after] == [f"step-{i}" for i in range(1, 7)]
    assert all(event.event_type == "step_succeeded" for event in after)


def test_real_chain_step7_aged_step5_none_deterministic_failure(tmp_path: Path) -> None:
    inputs = chain_inputs(tmp_path)
    state_path = inputs["state_path"]
    events_path = inputs["events_path"]
    # Issue #371 minimum proofs (inline): same canonical six-event succeeded
    # history; deterministic provider failure still reaches transport once.
    events = load_events(events_path)  # type: ignore[arg-type]
    assert [event.step_id for event in events] == [f"step-{i}" for i in range(1, 7)]
    assert all(event.event_type == "step_succeeded" for event in events)
    assert events[4].request_id is None and events[4].provider == "openai"
    assert isinstance(events[5].request_id, str) and events[5].request_id
    assert events[5].provider == "openai"
    before = state_path.read_bytes(), events_path.read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    result = phase155(  # type: ignore[call-overload]
        **inputs, transport=failure_transport(calls)
    )
    assert type(result) is StepRuntimeExecutionFailure
    assert result.workflow_id == "w"
    assert result.step_id == "step-7"
    assert result.step_index == 7
    assert result.employee_id == "e7"
    assert result.invocation_result.provider == "openai"
    assert len(calls) == 1
    assert (state_path.read_bytes(), events_path.read_bytes()) == before  # type: ignore[union-attr]
    # step7 runtime-result persistence NOT performed: six-event history intact
    after = load_events(events_path)  # type: ignore[arg-type]
    assert [event.step_id for event in after] == [f"step-{i}" for i in range(1, 7)]
    assert all(event.event_type == "step_succeeded" for event in after)


def test_real_chain_step7_accumulated_none_request_ids_transport_once(
    tmp_path: Path,
) -> None:
    # Real flow: the step-6 StepRuntimeExecutionSuccess with request_id=None /
    # provider="openai" is supplied at Phase 176 input time; Phase 161 persists
    # the step-6 succeeded event as-is, so no persisted-history rewrite happens.
    inputs = chain_inputs(tmp_path, step6_request_id_none=True)
    state_path = inputs["state_path"]
    events_path = inputs["events_path"]
    # Issue #371 minimum proofs (inline): accumulated None request IDs on the
    # aged step-5 AND the immediate step-6 both remain None / provider="openai"
    # straight from real Phase 176 persistence (no replace_event_line rewrite).
    events = load_events(events_path)  # type: ignore[arg-type]
    assert [event.step_id for event in events] == [f"step-{i}" for i in range(1, 7)]
    assert all(event.event_type == "step_succeeded" for event in events)
    assert events[4].request_id is None and events[4].provider == "openai"
    assert events[5].request_id is None and events[5].provider == "openai"
    before = state_path.read_bytes(), events_path.read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    result = phase155(  # type: ignore[call-overload]
        **inputs, transport=success_transport(calls)
    )
    assert type(result) is StepRuntimeExecutionSuccess
    assert result.step_id == "step-7" and result.step_index == 7
    assert len(calls) == 1
    assert (state_path.read_bytes(), events_path.read_bytes()) == before  # type: ignore[union-attr]
    # step7 runtime-result persistence NOT performed: six-event history intact
    after = load_events(events_path)  # type: ignore[arg-type]
    assert [event.step_id for event in after] == [f"step-{i}" for i in range(1, 7)]
    assert all(event.event_type == "step_succeeded" for event in after)


def test_real_chain_step7_prethreshold_step4_none_rejected(tmp_path: Path) -> None:
    inputs = chain_inputs(tmp_path)
    wf = inputs["workflow"]  # type: ignore[arg-type]
    replace_event_line(
        inputs,
        3,
        predecessor_event(wf.steps[3].id, 4, output_text="", request_id=None),
    )
    state_path = inputs["state_path"]
    events_path = inputs["events_path"]
    # Issue #371 minimum proofs (inline): pre-threshold step-4 request_id=None
    # stays rejected; immediate step-6 keeps its canonical non-empty request_id.
    events = load_events(events_path)  # type: ignore[arg-type]
    assert [event.step_id for event in events] == [f"step-{i}" for i in range(1, 7)]
    assert all(event.event_type == "step_succeeded" for event in events)
    assert events[3].request_id is None and events[3].provider == "openai"
    assert isinstance(events[5].request_id, str) and events[5].request_id
    assert events[5].provider == "openai"
    before = state_path.read_bytes(), events_path.read_bytes()  # type: ignore[union-attr]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    with pytest.raises(
        PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError
    ) as caught:
        phase155(  # type: ignore[call-overload]
            **inputs, transport=success_transport(calls)
        )
    assert caught.value.detail.classification == "persistence_result_contract"
    assert len(calls) == 0
    assert (state_path.read_bytes(), events_path.read_bytes()) == before  # type: ignore[union-attr]
    # rejection leaves the six-event history intact (no step-7 event appended)
    after = load_events(events_path)  # type: ignore[arg-type]
    assert [event.step_id for event in after] == [f"step-{i}" for i in range(1, 7)]
    assert all(event.event_type == "step_succeeded" for event in after)
