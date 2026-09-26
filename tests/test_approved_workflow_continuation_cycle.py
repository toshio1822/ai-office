"""Behavioral tests for the approved-workflow continuation public boundary.

Normal requests use the real current composition.  Fault tests patch the
owner name resolved by the continuation module only to exercise compensation
and no-retry safety; stage injection is not part of the public API under test.
"""

# ruff: noqa: E501,E701,E702,F401,I001

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import inspect
import json
from pathlib import Path

import pytest

import ai_office.engine.approved_workflow_continuation_cycle as continuation_module
from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.approved_workflow_continuation_cycle import (
    ApprovedWorkflowContinuationCycleCompatibilityError as Phase190Error,
    ApprovedWorkflowContinuationCycleError,
    ApprovedWorkflowContinuationCycleFailureDetail,
    route_approved_workflow_continuation_cycle as phase190,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_continuation_runtime_facts import (
    build_persisted_continuation_runtime_facts,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase155BoundaryError,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase147BoundaryError,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase146BoundaryError,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145BoundaryError,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    EMPTY_RUNTIME_FACTS,
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    UpstreamStepOutput,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey, OpenAIResponsesRawHttpResponse
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    RunningStatePersistenceResult,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition


def workflow(count: int = 11) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "Workflow",
            "description": "Synthetic workflow",
            "steps": [
                {
                    "id": f"step-{i}",
                    "name": f"Step {i}",
                    "employee": f"e{i}",
                    "instructions": f"instructions-{i}",
                }
                for i in range(1, count + 1)
            ],
        }
    )


def employee(wf: WorkflowDefinition, index: int) -> EmployeeDefinition:
    step = wf.steps[index - 1]
    return EmployeeDefinition(
        id=step.employee,
        name=f"Employee {index}",
        role="worker",
        instructions=f"employee-instructions-{index}",
        model="model",
        allowed_tools=[],
    )


def decision(wf: WorkflowDefinition, index: int) -> WorkflowProgressionDecision:
    step = wf.steps[index - 1]
    nxt = wf.steps[index] if index < len(wf.steps) else None
    return WorkflowProgressionDecision(
        "prepare_next_step",
        wf.id,
        step.id,
        index,
        step.employee,
        nxt.id if nxt else None,
        index + 1 if nxt else None,
        nxt.employee if nxt else None,
        "next_step_available",
    )


def complete(wf: WorkflowDefinition) -> WorkflowProgressionDecision:
    step = wf.steps[-1]
    return WorkflowProgressionDecision(
        "workflow_complete",
        wf.id,
        step.id,
        len(wf.steps),
        step.employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


def failure(
    wf: WorkflowDefinition, index: int, category: str = "api_error"
) -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure", wf.id, step.id, index, step.employee, category
    )


def runtime(
    wf: WorkflowDefinition,
    index: int,
    *,
    failed: bool = False,
) -> StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure:
    step = wf.steps[index - 1]
    if failed:
        invocation = ModelInvocationFailure(
            "openai",
            "api_error",
            "synthetic",
            f"request-{index}",
            500,
            None,
            None,
        )
        return StepRuntimeExecutionFailure(
            wf.id, step.id, index, step.employee, invocation
        )
    invocation = ModelInvocationSuccess(
        "openai",
        f"response-{index}",
        f"request-{index}",
        "completed",
        ("ok",),
        "ok",
    )
    return StepRuntimeExecutionSuccess(wf.id, step.id, index, step.employee, invocation)


def _history_event(
    wf: WorkflowDefinition, index: int, **changes: object
) -> RuntimeStepEvent:
    step = wf.steps[index - 1]
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            wf.id,
            step.id,
            index,
            step.employee,
            "running",
            "succeeded",
            "openai",
            None,
            f"response-{step.id}",
            f"request-{step.id}",
            f"output-{step.id}",
            None,
        ),
        **changes,  # type: ignore[arg-type]
    )


def _terminal_history_bytes(wf: WorkflowDefinition, current: int) -> bytes:
    """Build deterministic terminal history accepted by real current owners."""
    events = []
    for index in range(1, current + 1):
        if index == current:
            events.append(_history_event(wf, index, output_text="output"))
        elif index == current - 1:
            events.append(_history_event(wf, index, output_text="", request_id=None))
        elif index in (2, 3, 4):
            events.append(_history_event(wf, index, output_text=""))
        else:
            events.append(_history_event(wf, index))
    return b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8") for event in events
    )


def setup(tmp_path: Path, *, current: int = 9, count: int = 11) -> dict[str, object]:
    wf = workflow(count)
    state = WorkflowExecutionState(
        wf.id,
        "succeeded",
        wf.steps[current - 1].id,
        current,
        wf.steps[current - 1].employee,
        tuple(step.id for step in wf.steps[:current]),
        None,
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(_terminal_history_bytes(wf, current))
    return {
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
        "state": state,
        "before": (state_path.read_bytes(), events_path.read_bytes()),
    }


def transport(calls: list[object], *, status: int = 200) -> object:
    def fake(request_value: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(request_value)
        if status == 500:
            return OpenAIResponsesRawHttpResponse(
                status_code=500,
                reason="synthetic error",
                headers=(("x-request-id", "request_123"),),
                body=(
                    b'{"error":{"message":"safe failure","type":"server_error",'
                    b'"param":null,"code":null}}'
                ),
            )
        return OpenAIResponsesRawHttpResponse(
            status_code=200,
            reason="synthetic",
            headers=(("x-request-id", "request_123"),),
            body=(
                b'{"id":"resp_123","object":"response","status":"completed",'
                b'"output":[{"type":"message","content":'
                b'[{"type":"output_text","text":"ok"}]}]}'
            ),
        )

    return fake


def started(wf: WorkflowDefinition, index: int) -> PreparedStepExecutionStart:
    step = wf.steps[index - 1]
    predecessor = wf.steps[index - 2]
    predecessor_state = WorkflowExecutionState(
        wf.id,
        "succeeded",
        predecessor.id,
        index - 1,
        predecessor.employee,
        tuple(item.id for item in wf.steps[: index - 1]),
        None,
    )
    predecessor_event = _history_event(wf, index - 1, output_text="output")
    predecessor_history = LoadedWorkflowExecutionHistory(
        predecessor_state, (predecessor_event,)
    )
    facts = build_persisted_continuation_runtime_facts(
        wf.id,
        index,
        predecessor_history,
        state_source_sha256=sha256(
            serialize_workflow_execution_state_json(predecessor_state).encode()
        ).hexdigest(),
    )
    prepared = PreparedWorkflowStep(
        wf.id,
        step.id,
        index,
        step.employee,
        f"employee-instructions-{index}",
        step.instructions,
        "model",
        (),
    )
    request = ModelInvocationRequest(
        prepared.model,
        prepared.employee_instructions,
        prepared.step_instructions,
        (),
        (
            UpstreamStepOutput(
                wf.id,
                predecessor.id,
                index - 1,
                predecessor.employee,
                "output",
            ),
        ),
        facts,
    )
    return PreparedStepExecutionStart(
        request,
        WorkflowExecutionState(
            wf.id,
            "running",
            prepared.step_id,
            index,
            prepared.employee_id,
            tuple(item.id for item in wf.steps[: index - 1]),
            None,
        ),
    )


def execution_context(wf: WorkflowDefinition, index: int) -> dict[str, object]:
    emp = employee(wf, index)
    start = started(wf, index)
    resolved_tools = tuple(
        ToolDefinition(tool, f"Tool {tool}", ()) for tool in emp.allowed_tools
    )
    api_key = OpenAIApiKey(value="test-key")
    approval = approve_model_invocation_execution(
        start.request,
        resolved_tools,
        provider="openai",
        approved_by="reviewer",
        approval_id="approval-1",
    )
    return {
        "employee": emp,
        "resolved_tools": resolved_tools,
        "api_key": api_key,
        "execution_approval": approval,
    }


def preparation_approval(
    wf: WorkflowDefinition, index: int
) -> NextStepPreparationApproval:
    prior = decision(wf, index - 1)
    return NextStepPreparationApproval(
        True,
        prior.workflow_id,
        prior.current_step_id,
        prior.current_step_index,
        prior.next_step_id,
        prior.next_step_index,
        prior.next_employee_id,
    )


def valid_args(values: dict[str, object], index: int = 10) -> tuple[object, ...]:
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    context = execution_context(wf, index)
    return (
        decision(wf, index - 1),
        wf,
        preparation_approval(wf, index),
        context["employee"],
        values["state_path"],
        values["events_path"],
        context["resolved_tools"],
        context["api_key"],
        context["execution_approval"],
        transport([]),
    )


def write_terminal(
    workflow_value: WorkflowDefinition,
    state_path: Path,
    events_path: Path,
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
) -> None:
    step = workflow_value.steps[result.step_index - 1]
    invocation = result.invocation_result
    if type(result) is StepRuntimeExecutionSuccess:
        state = WorkflowExecutionState(
            workflow_value.id,
            "succeeded",
            step.id,
            result.step_index,
            step.employee,
            tuple(item.id for item in workflow_value.steps[: result.step_index]),
            None,
        )
        event = RuntimeStepEvent(
            "step_succeeded",
            workflow_value.id,
            step.id,
            result.step_index,
            step.employee,
            "running",
            "succeeded",
            invocation.provider,
            None,
            invocation.response_id,
            invocation.request_id,
            invocation.text,
            None,
        )
    else:
        state = WorkflowExecutionState(
            workflow_value.id,
            "failed",
            step.id,
            result.step_index,
            step.employee,
            tuple(item.id for item in workflow_value.steps[: result.step_index - 1]),
            invocation.category,
        )
        event = RuntimeStepEvent(
            "step_failed",
            workflow_value.id,
            step.id,
            result.step_index,
            step.employee,
            "running",
            "failed",
            invocation.provider,
            invocation.category,
            None,
            invocation.request_id,
            None,
            invocation.message,
        )
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(
        events_path.read_bytes() + serialize_runtime_step_event_jsonl(event).encode()
    )


def assert_safe_error(call: object, classification: str) -> None:
    with pytest.raises(Phase190Error) as caught:
        assert callable(call)
        call()
    assert caught.value.detail.classification == classification
    assert "secret" not in str(caught.value)


def test_public_api_keeps_only_the_ten_business_runtime_inputs() -> None:
    params = list(inspect.signature(phase190).parameters.values())
    assert [param.name for param in params] == [
        "result",
        "workflow",
        "preparation_approval",
        "employee",
        "state_path",
        "events_path",
        "resolved_tools",
        "api_key",
        "execution_approval",
        "transport",
    ]
    assert all(
        param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for param in params
    )
    assert issubclass(Phase190Error, ApprovedWorkflowContinuationCycleError)
    assert ApprovedWorkflowContinuationCycleFailureDetail
    with pytest.raises(TypeError):
        phase190(  # type: ignore[call-arg]
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            internal_stage=object(),
        )


def test_terminal_stop_is_identity_preserving_read_only_and_provider_free(
    tmp_path: Path,
) -> None:
    for name, stop, current in (
        ("complete", complete(workflow()), 11),
        ("failure", failure(workflow(), 10), 10),
    ):
        values = setup(tmp_path / name, current=current)
        wf = values["workflow"]
        assert isinstance(wf, WorkflowDefinition)
        before = values["before"]
        result = complete(wf) if name == "complete" else failure(wf, current)
        out = phase190(
            result,
            wf,
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
            object(),
        )
        assert out is result
        assert (
            values["state_path"].read_bytes(),
            values["events_path"].read_bytes(),
        ) == before


def test_real_composition_nonfinal_success_returns_next_progression_once(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, current=9, count=11)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    context = execution_context(wf, 10)
    calls: list[object] = []
    result = phase190(
        decision(wf, 9),
        wf,
        preparation_approval(wf, 10),
        context["employee"],
        values["state_path"],
        values["events_path"],
        context["resolved_tools"],
        context["api_key"],
        context["execution_approval"],
        transport(calls),
    )
    state = load_workflow_execution_state(values["state_path"])
    events = [
        json.loads(line)
        for line in values["events_path"].read_text().splitlines()
        if line.strip()
    ]
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "prepare_next_step"
    assert result.next_step_index == 11
    assert len(calls) == 1
    assert state.status == "succeeded"
    assert state.current_step_index == 10
    assert state.completed_step_ids == tuple(step.id for step in wf.steps[:10])
    assert len(events) == 10


def test_real_composition_final_success_returns_workflow_complete(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, current=10, count=11)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    context = execution_context(wf, 11)
    calls: list[object] = []
    result = phase190(
        decision(wf, 10),
        wf,
        preparation_approval(wf, 11),
        context["employee"],
        values["state_path"],
        values["events_path"],
        context["resolved_tools"],
        context["api_key"],
        context["execution_approval"],
        transport(calls),
    )
    state = load_workflow_execution_state(values["state_path"])
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert result.current_step_index == 11
    assert len(calls) == 1
    assert state.status == "succeeded"
    assert state.completed_step_ids == tuple(step.id for step in wf.steps)


def test_real_composition_runtime_failure_persists_failure_and_stops(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, current=9, count=11)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    context = execution_context(wf, 10)
    calls: list[object] = []
    result = phase190(
        decision(wf, 9),
        wf,
        preparation_approval(wf, 10),
        context["employee"],
        values["state_path"],
        values["events_path"],
        context["resolved_tools"],
        context["api_key"],
        context["execution_approval"],
        transport(calls, status=500),
    )
    state = load_workflow_execution_state(values["state_path"])
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    assert result.failure_category == "api_error"
    assert len(calls) == 1
    assert state.status == "failed"
    assert state.current_step_index == 10
    assert state.last_failure_category == "api_error"


def test_invalid_preparation_approval_fails_before_persistence_and_provider(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, current=9, count=11)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    context = execution_context(wf, 10)
    calls: list[object] = []
    before = values["before"]
    with pytest.raises(Phase145BoundaryError):
        phase190(
            decision(wf, 9),
            wf,
            object(),
            context["employee"],
            values["state_path"],
            values["events_path"],
            context["resolved_tools"],
            context["api_key"],
            context["execution_approval"],
            transport(calls),
        )
    assert calls == []
    assert (
        values["state_path"].read_bytes(),
        values["events_path"].read_bytes(),
    ) == before


def test_invalid_execution_approval_fails_closed_before_running_or_provider(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, current=9, count=11)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    context = execution_context(wf, 10)
    stale = replace(context["execution_approval"], request_fingerprint="0" * 64)
    calls: list[object] = []
    before = values["before"]
    with pytest.raises(Phase190Error) as caught:
        phase190(
            decision(wf, 9),
            wf,
            preparation_approval(wf, 10),
            context["employee"],
            values["state_path"],
            values["events_path"],
            context["resolved_tools"],
            context["api_key"],
            stale,
            transport(calls),
        )
    assert caught.value.detail.classification == "approval_contract"
    assert calls == []
    assert (
        values["state_path"].read_bytes(),
        values["events_path"].read_bytes(),
    ) == before
    assert load_workflow_execution_state(values["state_path"]).status == "succeeded"


def test_authoritative_runtime_facts_mismatch_stops_before_running_or_provider(
    tmp_path: Path,
) -> None:
    for mode in ("provider", "completed_count"):
        values = setup(tmp_path / mode, current=9, count=11)
        wf = values["workflow"]
        assert isinstance(wf, WorkflowDefinition)
        state_path = values["state_path"]
        events_path = values["events_path"]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        if mode == "provider":
            records = events_path.read_bytes().splitlines(keepends=True)
            last = RuntimeStepEvent(**json.loads(records[-1]))
            records[-1] = serialize_runtime_step_event_jsonl(
                replace(last, provider="omniroute")
            ).encode()
            events_path.write_bytes(b"".join(records))
        else:
            state = values["state"]
            assert isinstance(state, WorkflowExecutionState)
            state_path.write_bytes(
                serialize_workflow_execution_state_json(
                    replace(
                        state, completed_step_ids=("extra", *state.completed_step_ids)
                    )
                ).encode()
            )
        before = state_path.read_bytes(), events_path.read_bytes()
        context = execution_context(wf, 10)
        calls: list[object] = []
        with pytest.raises((Phase145BoundaryError, Phase190Error)) as caught:
            phase190(
                decision(wf, 9),
                wf,
                preparation_approval(wf, 10),
                context["employee"],
                state_path,
                events_path,
                context["resolved_tools"],
                context["api_key"],
                context["execution_approval"],
                transport(calls),
            )
        if isinstance(caught.value, Phase190Error):
            assert caught.value.detail.classification == "approval_contract"
        assert calls == []
        assert (state_path.read_bytes(), events_path.read_bytes()) == before


def test_running_persistence_safe_failure_restores_terminal_snapshot_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path, current=9, count=11)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    before = values["before"]
    calls: list[object] = []
    safe = Phase147BoundaryError("safe persistence failure")
    owner_calls = 0

    def persistence(*args: object, **kwargs: object) -> object:
        del kwargs
        nonlocal owner_calls
        owner_calls += 1
        state_path = args[3]
        events_path = args[4]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        state_path.write_text("partial-state", encoding="utf-8")
        events_path.write_text("partial-events", encoding="utf-8")
        raise safe

    monkeypatch.setattr(
        continuation_module,
        "route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        persistence,
    )
    with pytest.raises(Phase147BoundaryError) as caught:
        phase190(*valid_args(values))
    assert caught.value is safe
    assert owner_calls == 1
    assert calls == []
    assert (
        values["state_path"].read_bytes(),
        values["events_path"].read_bytes(),
    ) == before


def test_post_running_execution_failure_keeps_durable_running_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path, current=9, count=11)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    context = execution_context(wf, 10)
    safe = Phase155BoundaryError("safe execution failure")
    owner_calls = 0

    def execution(*args: object, **kwargs: object) -> object:
        del args, kwargs
        nonlocal owner_calls
        owner_calls += 1
        raise safe

    monkeypatch.setattr(
        continuation_module,
        "route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        execution,
    )
    calls: list[object] = []
    with pytest.raises(Phase155BoundaryError) as caught:
        phase190(
            decision(wf, 9),
            wf,
            preparation_approval(wf, 10),
            context["employee"],
            values["state_path"],
            values["events_path"],
            context["resolved_tools"],
            context["api_key"],
            context["execution_approval"],
            transport(calls),
        )
    assert caught.value is safe
    assert owner_calls == 1
    assert calls == []
    assert load_workflow_execution_state(values["state_path"]).status == "running"
    assert values["events_path"].read_bytes() == values["before"][1]


def test_post_runtime_terminal_commit_is_not_rolled_back_after_safe_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path, current=9, count=11)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    context = execution_context(wf, 10)
    safe = Phase172Error("safe post-runtime failure")
    calls: list[object] = []

    def after_commit(*args: object, **kwargs: object) -> object:
        del kwargs
        result, workflow_value, state_path, events_path = args
        assert isinstance(
            result, (StepRuntimeExecutionSuccess, StepRuntimeExecutionFailure)
        )
        assert isinstance(workflow_value, WorkflowDefinition)
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        write_terminal(workflow_value, state_path, events_path, result)
        raise safe

    monkeypatch.setattr(
        continuation_module,
        "route_runtime_result_to_progression_orchestration_boundary",
        after_commit,
    )
    with pytest.raises(Phase172Error) as caught:
        phase190(
            decision(wf, 9),
            wf,
            preparation_approval(wf, 10),
            context["employee"],
            values["state_path"],
            values["events_path"],
            context["resolved_tools"],
            context["api_key"],
            context["execution_approval"],
            transport(calls),
        )
    assert caught.value is safe
    assert len(calls) == 1
    assert load_workflow_execution_state(values["state_path"]).status == "succeeded"
    assert len(values["events_path"].read_text().splitlines()) == 10


def test_mutation_compensation_restores_running_snapshot_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path, current=9, count=11)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    context = execution_context(wf, 10)
    calls: list[object] = []
    owner_calls = 0

    def execution(*args: object, **kwargs: object) -> object:
        del kwargs
        nonlocal owner_calls
        owner_calls += 1
        state_path = args[4]
        events_path = args[5]
        assert isinstance(state_path, Path) and isinstance(events_path, Path)
        state_path.write_text("unexpected-state", encoding="utf-8")
        events_path.write_text("unexpected-events", encoding="utf-8")
        raise RuntimeError("secret runtime failure")

    monkeypatch.setattr(
        continuation_module,
        "route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        execution,
    )
    with pytest.raises(Phase190Error) as caught:
        phase190(
            decision(wf, 9),
            wf,
            preparation_approval(wf, 10),
            context["employee"],
            values["state_path"],
            values["events_path"],
            context["resolved_tools"],
            context["api_key"],
            context["execution_approval"],
            transport(calls),
        )
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)
    assert owner_calls == 1
    assert calls == []
    assert load_workflow_execution_state(values["state_path"]).status == "running"
    assert values["events_path"].read_bytes() == values["before"][1]


def test_rollback_failure_surfaces_safely_and_attempts_each_target_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path, current=9, count=11)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    attempts: list[Path] = []

    def prepare(*args: object, **kwargs: object) -> object:
        del kwargs
        state = args[4]
        events = args[5]
        assert isinstance(state, Path) and isinstance(events, Path)
        state.write_text("changed-state", encoding="utf-8")
        events.write_text("changed-events", encoding="utf-8")
        raise RuntimeError("secret preparation failure")

    def fail_write_bytes(path: Path, data: bytes) -> int:
        del data
        attempts.append(path)
        raise OSError("rollback")

    monkeypatch.setattr(
        continuation_module,
        "route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        prepare,
    )
    monkeypatch.setattr(Path, "write_bytes", fail_write_bytes)
    context = execution_context(wf, 10)
    with pytest.raises(Phase190Error) as caught:
        phase190(
            decision(wf, 9),
            wf,
            preparation_approval(wf, 10),
            context["employee"],
            state_path,
            events_path,
            context["resolved_tools"],
            context["api_key"],
            context["execution_approval"],
            transport([]),
        )
    assert caught.value.detail.classification == "rollback_failure"
    assert attempts == [state_path, events_path]


def test_runtime_fact_model_is_not_replaced_by_empty_compatibility_value() -> None:
    assert EMPTY_RUNTIME_FACTS is not None
    assert EMPTY_RUNTIME_FACTS.facts == ()
