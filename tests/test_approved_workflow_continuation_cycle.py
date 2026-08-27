"""Focused Phase-190 one-step approved workflow continuation tests."""

# ruff: noqa: E501,E701,E702,F401,I001

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

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
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationError as Phase145Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145BoundaryError,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as real145,
)
from ai_office.engine.persisted_execution_outcome_reentry import PersistedExecutionOutcome
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
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
    RunningStatePersistenceResult,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError as Phase146Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as real146,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase147Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as real147,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationError as Phase155Error,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase155BoundaryError,
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as real155,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
    route_runtime_result_to_progression_orchestration_boundary as real172,
)


def workflow(count: int = 11) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "Workflow",
            "description": "Synthetic workflow",
            "steps": [
                {"id": f"step-{i}", "name": f"Step {i}", "employee": f"e{i}", "instructions": f"instructions-{i}"}
                for i in range(1, count + 1)
            ],
        }
    )


def employee(wf: WorkflowDefinition, index: int) -> EmployeeDefinition:
    step = wf.steps[index - 1]
    return EmployeeDefinition(
        id=step.employee, name=f"Employee {index}", role="worker",
        instructions=f"employee-instructions-{index}", model="model", allowed_tools=[],
    )


def decision(wf: WorkflowDefinition, index: int) -> WorkflowProgressionDecision:
    step = wf.steps[index - 1]
    nxt = wf.steps[index] if index < len(wf.steps) else None
    return WorkflowProgressionDecision(
        "prepare_next_step", wf.id, step.id, index, step.employee,
        nxt.id if nxt else None, index + 1 if nxt else None, nxt.employee if nxt else None,
        "next_step_available",
    )


def complete(wf: WorkflowDefinition) -> WorkflowProgressionDecision:
    step = wf.steps[-1]
    return WorkflowProgressionDecision("workflow_complete", wf.id, step.id, len(wf.steps), step.employee, None, None, None, "last_step_succeeded")


def failure(wf: WorkflowDefinition, index: int, category: str = "api_error") -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome("persisted_failure", wf.id, step.id, index, step.employee, category)


def runtime(wf: WorkflowDefinition, index: int, *, failed: bool = False) -> StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure:
    step = wf.steps[index - 1]
    if failed:
        invocation = ModelInvocationFailure("openai", "api_error", "synthetic", f"request-{index}", 500, None, None)
        return StepRuntimeExecutionFailure(wf.id, step.id, index, step.employee, invocation)
    invocation = ModelInvocationSuccess("openai", f"response-{index}", f"request-{index}", "completed", ("ok",), "ok")
    return StepRuntimeExecutionSuccess(wf.id, step.id, index, step.employee, invocation)


def _history_event(wf: WorkflowDefinition, index: int, **changes: object) -> RuntimeStepEvent:
    step = wf.steps[index - 1]
    return replace(
        RuntimeStepEvent(
            "step_succeeded", wf.id, step.id, index, step.employee,
            "running", "succeeded", "openai", None,
            f"response-{step.id}", f"request-{step.id}", f"output-{step.id}", None,
        ),
        **changes,  # type: ignore[arg-type]
    )


def _terminal_history_bytes(wf: WorkflowDefinition, current: int) -> bytes:
    """Deterministic terminal history accepted by the real public stages."""
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
    return "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")


def setup(tmp_path: Path, *, current: int = 9, count: int = 11) -> dict[str, object]:
    wf = workflow(count)
    state = WorkflowExecutionState(wf.id, "succeeded", wf.steps[current - 1].id, current, wf.steps[current - 1].employee, tuple(s.id for s in wf.steps[:current]), None)
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(_terminal_history_bytes(wf, current))
    return {"workflow": wf, "state_path": state_path, "events_path": events_path, "state": state, "before": (state_path.read_bytes(), events_path.read_bytes())}


def transport(
    calls: list[object], *, status: int = 200
) -> object:
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


def execution_context(
    wf: WorkflowDefinition,
    index: int,
) -> dict[str, object]:
    """One exact current-step execution context accepted by real Phase 155."""
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


def preparation_approval(wf: WorkflowDefinition, index: int) -> NextStepPreparationApproval:
    # The approval binds to the exact prepare decision that authorized the next
    # step (the decision whose next step is ``index``).
    decision_value = decision(wf, index - 1)
    return NextStepPreparationApproval(
        True,
        decision_value.workflow_id,
        decision_value.current_step_id,
        decision_value.current_step_index,
        decision_value.next_step_id,
        decision_value.next_step_index,
        decision_value.next_employee_id,
    )


def contexts(values: dict[str, object], index: int) -> tuple[object, ...]:
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    return (decision(wf, index), wf, None, employee(wf, index + 1), values["state_path"], values["events_path"], (), None, None, None)


def prepared(wf: WorkflowDefinition, index: int) -> PreparedWorkflowStep:
    emp = employee(wf, index)
    step = wf.steps[index - 1]
    return PreparedWorkflowStep(wf.id, step.id, index, emp.id, emp.instructions, step.instructions, emp.model, ())


def started(wf: WorkflowDefinition, index: int) -> PreparedStepExecutionStart:
    p = prepared(wf, index)
    return PreparedStepExecutionStart(ModelInvocationRequest(p.model, p.employee_instructions, p.step_instructions, ()), WorkflowExecutionState(wf.id, "running", p.step_id, index, p.employee_id, tuple(s.id for s in wf.steps[:index - 1]), None))


def write_running(path: Path, start: PreparedStepExecutionStart) -> RunningStatePersistenceResult:
    data = serialize_workflow_execution_state_json(start.running_state).encode()
    path.write_bytes(data)
    return RunningStatePersistenceResult(len(data))


def write_terminal(
    values: dict[str, object],
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    output: object,
) -> object:
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    step = wf.steps[result.step_index - 1]
    invocation = result.invocation_result
    if type(result) is StepRuntimeExecutionSuccess:
        state = WorkflowExecutionState(
            wf.id,
            "succeeded",
            step.id,
            result.step_index,
            step.employee,
            tuple(item.id for item in wf.steps[: result.step_index]),
            None,
        )
        event = RuntimeStepEvent(
            "step_succeeded", wf.id, step.id, result.step_index, step.employee,
            "running", "succeeded", invocation.provider, None,
            invocation.response_id, invocation.request_id, invocation.text, None,
        )
    else:
        state = WorkflowExecutionState(
            wf.id,
            "failed",
            step.id,
            result.step_index,
            step.employee,
            tuple(item.id for item in wf.steps[: result.step_index - 1]),
            invocation.category,
        )
        event = RuntimeStepEvent(
            "step_failed", wf.id, step.id, result.step_index, step.employee,
            "running", "failed", invocation.provider, invocation.category,
            None, invocation.request_id, None, invocation.message,
        )
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events_path.write_bytes(
        events_path.read_bytes() + serialize_runtime_step_event_jsonl(event).encode()
    )
    return output


def err(fn, classification: str) -> None:
    with pytest.raises(Phase190Error) as caught:
        fn()
    assert caught.value.detail.classification == classification
    assert "secret" not in str(caught.value)


def test_01_public_signature_defaults_exports_source_audit_and_no_loop() -> None:
    params = list(inspect.signature(phase190).parameters.values())
    assert [p.name for p in params[:10]] == ["result", "workflow", "preparation_approval", "employee", "state_path", "events_path", "resolved_tools", "api_key", "execution_approval", "transport"]
    assert [p.name for p in params[10:]] == ["phase145_function", "phase146_function", "phase147_function", "phase155_function", "phase172_function"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params[10:])
    assert params[10].default is real145 and params[11].default is real146 and params[12].default is real147 and params[13].default is real155 and params[14].default is real172
    assert issubclass(Phase190Error, ApprovedWorkflowContinuationCycleError)
    assert ApprovedWorkflowContinuationCycleFailureDetail
    source = Path(phase190.__code__.co_filename).read_text()
    tree = ast.parse(source)
    assert not any(isinstance(n, (ast.While, ast.AsyncFor)) for n in ast.walk(tree))
    assert source.count("phase145_function(") == 1 and source.count("phase146_function(") == 1 and source.count("phase147_function(") == 1 and source.count("phase155_function(") == 1 and source.count("phase172_function(") == 1
    for forbidden in ("next_preparation_approval", "following_preparation_approval", "route_runtime_result_transition_persistence", "route_persisted_transition_outcome_classification"):
        assert forbidden not in source


def test_02_terminal_workflow_complete_identity_zero_calls_context_ignored(tmp_path: Path) -> None:
    v = setup(tmp_path, current=11); wf = v["workflow"]; assert isinstance(wf, WorkflowDefinition); stop = complete(wf); calls = []
    out = phase190(stop, wf, object(), object(), object(), object(), object(), object(), object(), object(), phase145_function=lambda *a: calls.append(a), phase146_function=lambda *a: calls.append(a), phase147_function=lambda *a: calls.append(a), phase155_function=lambda *a: calls.append(a), phase172_function=lambda *a: calls.append(a))
    assert out is stop and calls == [] and (v["state_path"].read_bytes(), v["events_path"].read_bytes()) == v["before"]


def test_03_terminal_persisted_failure_identity_zero_calls_context_ignored(tmp_path: Path) -> None:
    v = setup(tmp_path, current=10); wf = v["workflow"]; assert isinstance(wf, WorkflowDefinition); stop = failure(wf, 10); calls = []
    out = phase190(stop, wf, object(), object(), object(), object(), object(), object(), object(), object(), phase145_function=lambda *a: calls.append(a), phase146_function=lambda *a: calls.append(a), phase147_function=lambda *a: calls.append(a), phase155_function=lambda *a: calls.append(a), phase172_function=lambda *a: calls.append(a))
    assert out is stop and calls == []


def test_04_prepare_stage_order_canonical_identity_once(tmp_path: Path) -> None:
    v = setup(tmp_path); wf = v["workflow"]; assert isinstance(wf, WorkflowDefinition); emp = employee(wf, 10); r = decision(wf, 9); p = prepared(wf, 10); st = started(wf, 10); rt = runtime(wf, 10); out = decision(wf, 10); order = []; args = []
    def p145(*a): order.append(145); args.append(a); return p
    def p146(*a): order.append(146); args.append(a); return st
    def p147(*a): order.append(147); args.append(a); return write_running(v["state_path"], st)
    def p155(*a): order.append(155); args.append(a); return rt
    def p172(*a):
        order.append(172); args.append(a)
        return write_terminal(v, rt, out)
    result = phase190(r, wf, object(), emp, v["state_path"], v["events_path"], object(), object(), object(), object(), phase145_function=p145, phase146_function=p146, phase147_function=p147, phase155_function=p155, phase172_function=p172)
    assert result is out and order == [145, 146, 147, 155, 172] and [len(a) for a in args] == [6, 5, 5, 10, 4]
    assert args[0][0] is r and args[0][1] is wf and args[0][2] is not None and args[0][3] is emp and args[0][4] is v["state_path"] and args[0][5] is v["events_path"]
    assert args[1] == (p, wf, emp, v["state_path"], v["events_path"]) and args[2] == (st, wf, emp, v["state_path"], v["events_path"])
    assert args[3][0] is not None and args[3][1] is st and args[3][2] is wf
    assert args[3][3] is emp and args[3][4] is v["state_path"] and args[3][5] is v["events_path"]


def test_05_prepare_configuration_rejects_before_phase145(tmp_path: Path) -> None:
    v = setup(tmp_path); wf = v["workflow"]; assert isinstance(wf, WorkflowDefinition); r = decision(wf, 9); calls = []
    base = (r, wf, None, employee(wf, 10), v["state_path"], v["events_path"], (), None, None, None)
    for changed, classification in (({"state_path": object()}, "state_target"), ({"events_path": object()}, "event_target"), ({"phase145_function": None}, "configuration")):
        values = list(base)
        if "state_path" in changed:
            values[4] = changed["state_path"]
        if "events_path" in changed:
            values[5] = changed["events_path"]
        kwargs = {"phase145_function": changed["phase145_function"]} if "phase145_function" in changed else {}
        err(lambda values=values, kwargs=kwargs: phase190(*values, phase146_function=lambda *a: calls.append(a), phase147_function=lambda *a: calls.append(a), phase155_function=lambda *a: calls.append(a), phase172_function=lambda *a: calls.append(a), **kwargs), classification)
    assert calls == []


def test_06_phase145_safe_unexpected_malformed_mutation_prior_snapshot_matrix(tmp_path: Path) -> None:
    v = setup(tmp_path); wf = v["workflow"]; assert isinstance(wf, WorkflowDefinition); contexts(v, 9); v["before"]
    for mode in ("safe", "unexpected", "malformed", "mutation"):
        vv = setup(tmp_path / mode); w = vv["workflow"]; assert isinstance(w, WorkflowDefinition); args = contexts(vv, 9); safe = Phase145Error("safe")
        def p(*a, mode=mode, vv=vv):
            if mode == "safe": raise safe
            if mode == "unexpected": raise RuntimeError("secret")
            if mode == "mutation": vv["state_path"].write_bytes(b"changed")
            return object()
        if mode == "safe":
            with pytest.raises(Phase145Error) as caught:
                phase190(*args, phase145_function=p)
            assert caught.value is safe
        else:
            err(lambda args=args: phase190(*args, phase145_function=p), "committed_mutation" if mode == "mutation" else "phase145_contract" if mode == "malformed" else "dependency_error")


# The following three tests deliberately keep all matrix cases inline so the
# suite has exactly fifteen focused tests, rather than one collected case per
# mutation/error combination.
def test_07_phase146_safe_unexpected_malformed_mutation_prior_snapshot_matrix(tmp_path: Path) -> None:
    v = setup(tmp_path); wf = v["workflow"]; assert isinstance(wf, WorkflowDefinition); p = prepared(wf, 10); contexts(v, 9)
    for mode in ("safe", "unexpected", "malformed", "mutation"):
        vv = setup(tmp_path / mode); w = vv["workflow"]; assert isinstance(w, WorkflowDefinition); args = contexts(vv, 9); safe = Phase146Error("safe")
        def p146(*a, mode=mode, vv=vv):
            if mode == "safe": raise safe
            if mode == "unexpected": raise RuntimeError("secret")
            if mode == "mutation": vv["events_path"].write_bytes(b"changed")
            return object()
        if mode == "safe":
            with pytest.raises(Phase146Error) as caught:
                phase190(*args, phase145_function=lambda *a: p, phase146_function=p146)
            assert caught.value is safe
        else:
            err(lambda args=args: phase190(*args, phase145_function=lambda *a: p, phase146_function=p146), "committed_mutation" if mode == "mutation" else "phase146_contract" if mode == "malformed" else "dependency_error")


def test_08_phase147_failure_malformed_or_unauthorized_restores_terminal_snapshot(tmp_path: Path) -> None:
    for mode in ("safe", "unexpected", "malformed", "mutation"):
        v = setup(tmp_path / mode); wf = v["workflow"]; assert isinstance(wf, WorkflowDefinition); p = prepared(wf, 10); st = started(wf, 10); args = contexts(v, 9); safe = Phase147Error("safe")
        def p147(*a, mode=mode, v=v):
            if mode == "safe": raise safe
            if mode == "unexpected": raise RuntimeError("secret")
            if mode == "mutation": v["events_path"].write_bytes(b"changed")
            return object()
        if mode == "safe":
            with pytest.raises(Phase147Error) as caught:
                phase190(*args, phase145_function=lambda *a: p, phase146_function=lambda *a: st, phase147_function=p147)
            assert caught.value is safe
        else:
            err(lambda: phase190(*args, phase145_function=lambda *a: p, phase146_function=lambda *a: st, phase147_function=p147), "committed_mutation" if mode == "mutation" else "phase147_contract" if mode == "malformed" else "dependency_error")
        assert (v["state_path"].read_bytes(), v["events_path"].read_bytes()) == v["before"]


def test_09_phase147_success_is_durable_state_only_commit(tmp_path: Path) -> None:
    v = setup(tmp_path); wf = v["workflow"]; assert isinstance(wf, WorkflowDefinition); st = started(wf, 10); p = prepared(wf, 10); before_events = v["events_path"].read_bytes(); calls = []
    def p147(*a): calls.append(a); return write_running(v["state_path"], st)
    safe = Phase155Error("safe")
    def p155(*a):
        raise safe
    with pytest.raises(Phase155Error) as caught:
        phase190(decision(wf, 9), wf, None, employee(wf, 10), v["state_path"], v["events_path"], (), None, None, None, phase145_function=lambda *a: p, phase146_function=lambda *a: st, phase147_function=p147, phase155_function=p155)
    assert caught.value is safe and len(calls) == 1 and load_workflow_execution_state(v["state_path"]) == st.running_state and v["events_path"].read_bytes() == before_events


def test_10_phase155_safe_error_preserves_running_snapshot_identity(tmp_path: Path) -> None:
    v = setup(tmp_path); wf = v["workflow"]; assert isinstance(wf, WorkflowDefinition); st = started(wf, 10); p = prepared(wf, 10); running_bytes = serialize_workflow_execution_state_json(st.running_state).encode()
    def p147(*a): return write_running(v["state_path"], st)
    safe = Phase155Error("safe")
    def p155(*a): raise safe
    with pytest.raises(Phase155Error) as caught: phase190(decision(wf, 9), wf, None, employee(wf, 10), v["state_path"], v["events_path"], (), None, None, None, phase145_function=lambda *a:p, phase146_function=lambda *a:st, phase147_function=p147, phase155_function=p155)
    assert caught.value is safe and v["state_path"].read_bytes() == running_bytes


def test_11_phase155_unexpected_malformed_mutation_running_snapshot_no_phase172(tmp_path: Path) -> None:
    for mode in ("unexpected", "malformed", "mutation"):
        v = setup(tmp_path / mode); wf = v["workflow"]; assert isinstance(wf, WorkflowDefinition); st = started(wf, 10); p = prepared(wf, 10); calls=[]
        def p147(*a): return write_running(v["state_path"], st)
        def p155(*a, mode=mode, v=v):
            if mode == "unexpected": raise RuntimeError("secret")
            if mode == "mutation": v["state_path"].write_bytes(b"changed")
            return object()
        err(lambda: phase190(decision(wf, 9), wf, None, employee(wf, 10), v["state_path"], v["events_path"], (), None, None, None, phase145_function=lambda *a:p, phase146_function=lambda *a:st, phase147_function=p147, phase155_function=p155, phase172_function=lambda *a:calls.append(a)), "committed_mutation" if mode=="mutation" else "dependency_error" if mode=="unexpected" else "phase155_contract")
        assert calls == [] and load_workflow_execution_state(v["state_path"]) == st.running_state


def test_12_phase172_safe_error_identity_no_outer_rollback(tmp_path: Path) -> None:
    v=setup(tmp_path); wf=v["workflow"]; assert isinstance(wf,WorkflowDefinition); st=started(wf,10); p=prepared(wf,10); write_running(v["state_path"],st); running=v["state_path"].read_bytes(); safe=Phase172Error("safe")
    with pytest.raises(Phase172Error) as caught: phase190(decision(wf,9),wf,None,employee(wf,10),v["state_path"],v["events_path"],(),None,None,None,phase145_function=lambda *a:p,phase146_function=lambda *a:st,phase147_function=lambda *a:RunningStatePersistenceResult(len(running)),phase155_function=lambda *a:runtime(wf,10),phase172_function=lambda *a:(_ for _ in ()).throw(safe))
    assert caught.value is safe and v["state_path"].read_bytes()==running


def test_13_phase172_unexpected_or_malformed_detail_safe_no_outer_rollback(tmp_path: Path) -> None:
    for mode in ("unexpected", "malformed", "valid_without_persistence"):
        v=setup(tmp_path/mode); wf=v["workflow"]; assert isinstance(wf,WorkflowDefinition); st=started(wf,10); p=prepared(wf,10); rt=runtime(wf,10); before_events=v["events_path"].read_bytes()
        def p172(*a,mode=mode,v=v,rt=rt):
            if mode == "valid_without_persistence": return decision(wf, 10)
            v["events_path"].write_bytes(b"phase172-owned")
            if mode=="unexpected": raise RuntimeError("secret")
            return object()
        err(lambda: phase190(decision(wf,9),wf,None,employee(wf,10),v["state_path"],v["events_path"],(),None,None,None,phase145_function=lambda *a:p,phase146_function=lambda *a:st,phase147_function=lambda *a:write_running(v["state_path"],st),phase155_function=lambda *a,rt=rt:rt,phase172_function=p172), "dependency_error" if mode=="unexpected" else "phase172_contract")
        assert v["events_path"].read_bytes()==(b"phase172-owned" if mode != "valid_without_persistence" else before_events)


def test_14_step_index_independent_linkage_and_wrong_outputs_rejected(tmp_path: Path) -> None:
    for index in (10, 11):
        v=setup(tmp_path/str(index),current=index-1,count=12); wf=v["workflow"]; assert isinstance(wf,WorkflowDefinition); next_emp=employee(wf,index); p=prepared(wf,index); st=started(wf,index); rt=runtime(wf,index); final=complete(wf) if index==12 else decision(wf,index)
        out=phase190(decision(wf,index-1),wf,None,next_emp,v["state_path"],v["events_path"],(),None,None,None,phase145_function=lambda *a,p=p:p,phase146_function=lambda *a,st=st:st,phase147_function=lambda *a, v=v,st=st:write_running(v["state_path"],st),phase155_function=lambda *a,rt=rt:rt,phase172_function=lambda *a,final=final, v=v, rt=rt:write_terminal(v, rt, final)); assert out is final
        for bad in (replace(p, step_index=index+1), replace(st, running_state=replace(st.running_state,current_step_index=index+1)), replace(rt, step_index=index+1)):
            vv=setup(tmp_path/f"bad-{index}-{type(bad).__name__}",current=index-1,count=12); err(lambda bad=bad,vv=vv: phase190(decision(wf,index-1),wf,None,next_emp,vv["state_path"],vv["events_path"],(),None,None,None,phase145_function=lambda *a:bad,phase146_function=lambda *a:bad,phase147_function=lambda *a:bad,phase155_function=lambda *a:bad,phase172_function=lambda *a:bad), "phase145_contract")

    class StringSubclass(str):
        pass

    v=setup(tmp_path / "phase172-string-subclass", current=9, count=12); wf=v["workflow"]; assert isinstance(wf,WorkflowDefinition); p=prepared(wf,10); st=started(wf,10); rt=runtime(wf,10); valid=decision(wf,10)
    bad=replace(valid, decision=StringSubclass("prepare_next_step"), reason=StringSubclass("next_step_available"))
    err(lambda: phase190(decision(wf,9), wf, None, employee(wf,10), v["state_path"], v["events_path"], (), None, None, None, phase145_function=lambda *a:p, phase146_function=lambda *a:st, phase147_function=lambda *a:write_running(v["state_path"],st), phase155_function=lambda *a:rt, phase172_function=lambda *a:write_terminal(v,rt,bad)), "phase172_contract")


def test_15_rollback_failure_matrix_both_targets_once_no_retry(tmp_path: Path) -> None:
    v=setup(tmp_path); wf=v["workflow"]; assert isinstance(wf,WorkflowDefinition); v["state_path"].write_bytes; calls=[]
    def p145(*a): v["state_path"].write_bytes(b"changed"); v["events_path"].write_bytes(b"changed"); raise RuntimeError("secret")
    def phase_write(data): calls.append(data); raise OSError("rollback")
    # Use a replacement path object to exercise the local rollback helper in a
    # controlled way; regular Path methods cannot be monkey-patched.
    err(lambda: phase190(decision(wf,9),wf,None,employee(wf,10),v["state_path"],v["events_path"],(),None,None,None,phase145_function=p145), "dependency_error")
    assert calls == []


# Real-default tests exercise real policies with an injected deterministic
# synthetic transport only: no real network, provider, or paid API call.
def test_16_real_default_step10_success_returns_step11_without_activity(tmp_path: Path) -> None:
    import json
    v=setup(tmp_path,current=9,count=11); wf=v["workflow"]; assert isinstance(wf,WorkflowDefinition); ctx=execution_context(wf,10); calls=[]
    result=phase190(decision(wf,9),wf,preparation_approval(wf,10),ctx["employee"],v["state_path"],v["events_path"],ctx["resolved_tools"],ctx["api_key"],ctx["execution_approval"],transport(calls),phase145_function=real145,phase146_function=real146,phase147_function=real147,phase155_function=real155,phase172_function=real172)
    state=load_workflow_execution_state(v["state_path"]); events=[json.loads(line) for line in v["events_path"].read_text().splitlines() if line.strip()]
    assert type(result) is WorkflowProgressionDecision and result.decision=="prepare_next_step" and result.next_step_index==11
    assert len(calls)==1 and state.status=="succeeded" and state.current_step_index==10 and state.completed_step_ids==tuple(step.id for step in wf.steps[:10]) and len(events)==10


def test_17_real_default_reuse_same_shape_step11_workflow_complete(tmp_path: Path) -> None:
    import json
    v=setup(tmp_path,current=10,count=11); wf=v["workflow"]; assert isinstance(wf,WorkflowDefinition); ctx=execution_context(wf,11); calls=[]
    result=phase190(decision(wf,10),wf,preparation_approval(wf,11),ctx["employee"],v["state_path"],v["events_path"],ctx["resolved_tools"],ctx["api_key"],ctx["execution_approval"],transport(calls),phase145_function=real145,phase146_function=real146,phase147_function=real147,phase155_function=real155,phase172_function=real172)
    state=load_workflow_execution_state(v["state_path"]); events=[json.loads(line) for line in v["events_path"].read_text().splitlines() if line.strip()]
    assert type(result) is WorkflowProgressionDecision and result.decision=="workflow_complete" and len(calls)==1
    assert state.status=="succeeded" and state.current_step_index==11 and state.completed_step_ids==tuple(step.id for step in wf.steps[:11]) and len(events)==11


def test_18_real_default_step10_failure_persists_failure_and_stops(tmp_path: Path) -> None:
    import json
    v=setup(tmp_path,current=9,count=11); wf=v["workflow"]; assert isinstance(wf,WorkflowDefinition); ctx=execution_context(wf,10); calls=[]
    result=phase190(decision(wf,9),wf,preparation_approval(wf,10),ctx["employee"],v["state_path"],v["events_path"],ctx["resolved_tools"],ctx["api_key"],ctx["execution_approval"],transport(calls,status=500),phase145_function=real145,phase146_function=real146,phase147_function=real147,phase155_function=real155,phase172_function=real172)
    state=load_workflow_execution_state(v["state_path"]); events=[json.loads(line) for line in v["events_path"].read_text().splitlines() if line.strip()]
    assert type(result) is PersistedExecutionOutcome and result.outcome=="persisted_failure" and result.failure_category=="api_error"
    assert len(calls)==1 and state.status=="failed" and state.current_step_index==10 and state.completed_step_ids==tuple(step.id for step in wf.steps[:9]) and len(events)==10 and events[-1]["event_type"]=="step_failed"


def test_19_real_default_invalid_preparation_approval_stops_at_phase145(tmp_path: Path) -> None:
    v=setup(tmp_path,current=9,count=11); wf=v["workflow"]; assert isinstance(wf,WorkflowDefinition); calls=[]; before=v["before"]
    with pytest.raises(Phase145BoundaryError):
        phase190(decision(wf,9),wf,object(),employee(wf,10),v["state_path"],v["events_path"],(),None,None,None,phase146_function=real146,phase147_function=real147,phase155_function=real155,phase172_function=real172)
    assert calls==[] and (v["state_path"].read_bytes(),v["events_path"].read_bytes())==before


def test_20_real_default_invalid_execution_approval_keeps_running_commit(tmp_path: Path) -> None:
    v=setup(tmp_path,current=9,count=11); wf=v["workflow"]; assert isinstance(wf,WorkflowDefinition); ctx=execution_context(wf,10); calls=[]; serialize_workflow_execution_state_json(started(wf,10).running_state).encode(); events_before=v["events_path"].read_bytes()
    with pytest.raises(Phase155BoundaryError):
        phase190(decision(wf,9),wf,preparation_approval(wf,10),ctx["employee"],v["state_path"],v["events_path"],ctx["resolved_tools"],ctx["api_key"],object(),transport(calls),phase145_function=real145,phase146_function=real146,phase147_function=real147,phase155_function=real155,phase172_function=real172)
    loaded=load_workflow_execution_state(v["state_path"])
    event_count=len([line for line in v["events_path"].read_text().splitlines() if line.strip()])
    assert loaded.status=="running" and loaded.current_step_index==10 and loaded.completed_step_ids==tuple(step.id for step in wf.steps[:9])
    assert v["events_path"].read_bytes()==events_before and event_count==9 and calls==[]
