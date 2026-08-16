"""Focused and real-default tests for Issue #375 (Phase 177).

Phase 177 composes the public Phase 176 (post-runtime prepared running-state
persistence) with the public Phase 155 (persisted-running execution) exactly
once each, using a capture-only delegating Phase-147 adapter to record the
exact ``PreparedStepExecutionStart`` produced by the real Phase-147 handoff,
stopping after one exact next-step runtime result or one exact stop object.

Requirement-to-test mapping (Issue #375):
- public signature / exact default dependency identities / source audit
  -> test_public_signature_default_dependencies_and_source_audit
- prepared execution-success route exact stage order Phase176 ->
  capture/delegate Phase147 -> Phase155, canonical argument identity/order,
  exactly once, exact runtime-success identity
  -> test_prepared_execution_success_route_exact_stage_order_and_contract
- prepared execution-failure route exact return contract and one Phase155
  call -> test_prepared_execution_failure_route_exact_return_contract
- workflow_complete stop route exact identity, no captured start, Phase155
  zero, targets preserved
  -> test_workflow_complete_stop_route_exact_identity_phase155_zero
- persisted_failure stop route exact identity, no captured start, Phase155
  zero, targets preserved
  -> test_persisted_failure_stop_route_exact_identity_phase155_zero
- non-callable Phase176 / Phase147 / Phase155 configuration rejects before
  execution -> test_non_callable_dependency_configuration_rejects_before_execution
- Phase176 recognized safe error identity; Phase155 zero; no pre-Phase176
  rollback/write -> test_phase176_recognized_safe_error_identity_phase155_zero
- Phase176 unexpected exception sanitized; Phase155 zero; no pre-Phase176
  rollback/write -> test_phase176_unexpected_exception_sanitized_phase155_zero
- malformed Phase176 return / prepared result with missing capture ->
  phase176_contract; Phase155 zero; no pre-Phase176 rollback
  -> test_malformed_phase176_return_or_missing_capture_phase176_contract
- capture contract narrowness: multiple/wrong/unlinked captured starts and
  captured-start-on-stop reject as phase176_contract without fabricating a
  replacement -> test_capture_contract_narrowness_phase176_contract
- Phase155 recognized safe error after valid Phase176; mutation restored only
  to the post-Phase176 committed snapshot and exact safe-error identity
  preserved -> test_phase155_recognized_safe_error_identity_restored_to_committed
- Phase155 unexpected exception after valid Phase176; mutation restored to
  committed snapshot then dependency_error
  -> test_phase155_unexpected_exception_restored_then_dependency_error
- malformed Phase155 runtime result rejected/restored to committed snapshot
  as phase155_contract -> test_malformed_phase155_return_phase155_contract_restored
- apparently valid Phase155 runtime result with unauthorized state/event
  mutation -> committed_mutation, restored to committed snapshot
  -> test_phase155_valid_return_with_unauthorized_mutation_committed_mutation
- preparation approval/employee and execution inputs deliberately not
  prevalidated: Phase176 runs first; invalid execution input rejected only by
  Phase155 after durable running-state persistence; Phase155 zero where
  Phase176 itself owns/rejects preparation errors
  -> test_preparation_execution_inputs_not_prevalidated_phase176_runs_first
- rollback failure after Phase155 mutation attempts both target restorations
  once and retries neither stage
  -> test_rollback_failure_after_phase155_mutation_both_restorations_once
- real A: accumulated-provenance step-7 execution success
  -> test_real_a_accumulated_provenance_step7_execution_success
- real B: accumulated-provenance deterministic step-7 execution failure
  -> test_real_b_accumulated_provenance_step7_execution_failure
- real C: both stop routes inline subcases
  -> test_real_c_both_stop_routes_inline_subcases
- real D: invalid execution approval occurs after durable Phase176 commit
  -> test_real_d_invalid_execution_approval_after_durable_phase176_commit
"""

# ruff: noqa: E501,E701,E702,F401,I001

import ast
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase155ContractError,
    PreparedStepExecutionStart,
    RuntimeResultToPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase177Error,
    RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase176ContractError,
    WorkflowProgressionDecision,
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_runtime_result_to_persisted_running_execution_orchestration_boundary,
    route_runtime_result_to_prepared_start_persistence_orchestration_boundary,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.runtime_result_to_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryError as Phase176SafeError,
)
from ai_office.invocation import (
    ModelInvocationFailure,
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

phase177 = route_runtime_result_to_persisted_running_execution_orchestration_boundary
phase176 = route_runtime_result_to_prepared_start_persistence_orchestration_boundary
phase147 = route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
phase155 = route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary

TOOLS = (
    ToolDefinition("tool-one", "Tool One", ()),
    ToolDefinition("tool-two", "Tool Two", ()),
)


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
    """Canonical Phase-155 running state at ``current`` with events 1..current-1.

    Immediate predecessor (current-1): provider=openai, output_text="",
    request_id=None. Earlier empty outputs for indices 2..4 (authorized).
    """
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
            events.append(
                predecessor_event(wf.steps[index - 1].id, index, output_text="")
            )
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


def terminal_history_bytes(
    wf: WorkflowDefinition, index: int, request_id_none: bool = False
) -> bytes:
    """Committed Phase-176 terminal history through ``index`` as JSONL bytes.

    The just-finished step (``index``) exactly matches the runtime result
    invocation (provider / response / request / output / no message); its
    immediate predecessor (``index - 1``) carries output_text="" with
    request_id=None; indices 2..4 carry empty output; index 1 is the normal
    predecessor.
    """
    events = []
    for position in range(1, index + 1):
        step = wf.steps[position - 1]
        if position == index:
            events.append(
                predecessor_event(
                    step.id,
                    position,
                    output_text="output",
                    request_id=None if request_id_none else f"request-{step.id}",
                )
            )
        elif position == index - 1:
            events.append(
                predecessor_event(step.id, position, output_text="", request_id=None)
            )
        elif position in (2, 3, 4):
            events.append(predecessor_event(step.id, position, output_text=""))
        else:
            events.append(predecessor_event(step.id, position))
    return "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")


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


def runtime_failure(wf: WorkflowDefinition, index: int) -> StepRuntimeExecutionFailure:
    step = wf.steps[index - 1]
    return StepRuntimeExecutionFailure(
        "w",
        step.id,
        index,
        step.employee,
        ModelInvocationFailure(
            "openai", "api_error", "safe failure", f"request-{step.id}", 500, None, None
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


def complete_decision(wf: WorkflowDefinition, index: int) -> WorkflowProgressionDecision:
    step = wf.steps[index - 1]
    return WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        step.id,
        index,
        step.employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


def failure_outcome(wf: WorkflowDefinition, index: int) -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure", "w", step.id, index, step.employee, "api_error"
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


def prepared_for(
    wf: WorkflowDefinition,
    decision: WorkflowProgressionDecision,
    employee: EmployeeDefinition,
) -> PreparedWorkflowStep:
    step = wf.steps[decision.next_step_index - 1]
    return PreparedWorkflowStep(
        wf.id,
        step.id,
        decision.next_step_index,
        employee.id,
        employee.instructions,
        step.instructions,
        employee.model,
        tuple(employee.allowed_tools),
    )


def prepared_start_for(
    wf: WorkflowDefinition, prepared: PreparedWorkflowStep
) -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            prepared.model,
            prepared.employee_instructions,
            prepared.step_instructions,
            prepared.allowed_tool_names,
        ),
        WorkflowExecutionState(
            prepared.workflow_id,
            "running",
            prepared.step_id,
            prepared.step_index,
            prepared.employee_id,
            tuple(step.id for step in wf.steps[: prepared.step_index - 1]),
            None,
        ),
    )


def constructed_start(values: dict[str, object]) -> PreparedStepExecutionStart:
    """Build a valid step-7 PreparedStepExecutionStart without touching files."""
    wf = values["workflow"]  # type: ignore[arg-type]
    decision = prepare_decision(wf, 6)
    employee = employee_for(decision)
    return prepared_start_for(wf, prepared_for(wf, decision, employee))


def step7_success(wf: WorkflowDefinition) -> StepRuntimeExecutionSuccess:
    return runtime_success(wf, 7)


def step7_failure(wf: WorkflowDefinition) -> StepRuntimeExecutionFailure:
    return runtime_failure(wf, 7)


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


def recording_stub(
    return_value: object,
) -> tuple[object, list[tuple[tuple[object, ...], dict[str, object]]]]:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def stub(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return return_value

    return stub, calls


def raising_stub(
    error: BaseException,
) -> tuple[object, list[tuple[tuple[object, ...], dict[str, object]]]]:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def stub(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        raise error

    return stub, calls


def assert_classification(fn: object, expected: str) -> None:
    with pytest.raises(Phase177Error) as exc:
        fn()  # type: ignore[operator]
    assert exc.value.detail.classification == expected


def load_events(events_path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def phase176_alone(
    root: Path,
    steps: int = 7,
    current: int = 6,
    *,
    fail: bool = False,
) -> tuple[dict[str, object], bytes, bytes]:
    """Run public Phase 176 alone on a fresh canonical setup; return bytes."""
    values = canonical_running_setup(root, steps=steps, current=current)
    wf = values["workflow"]  # type: ignore[arg-type]
    if fail:
        result: object = runtime_failure(wf, current)
    else:
        result = runtime_success(wf, current, request_id_none=True)
    if type(result) is StepRuntimeExecutionFailure or current >= steps:
        phase176(result, wf, None, None, values["state_path"], values["events_path"])
    else:
        decision = prepare_decision(wf, current)
        phase176(
            result,
            wf,
            approval_for(decision),
            employee_for(decision),
            values["state_path"],
            values["events_path"],
        )
    return values, Path(values["state_path"]).read_bytes(), Path(  # type: ignore[arg-type]
        values["events_path"]
    ).read_bytes()


def test_public_signature_default_dependencies_and_source_audit() -> None:
    signature = inspect.signature(phase177)
    parameters = list(signature.parameters.values())
    assert [p.name for p in parameters] == [
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
        "phase176_function",
        "phase147_function",
        "phase155_function",
    ]
    assert [p.kind for p in parameters[:10]] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD
    ] * 10
    assert [p.kind for p in parameters[10:]] == [inspect.Parameter.KEYWORD_ONLY] * 3
    assert parameters[10].default is phase176
    assert parameters[11].default is phase147
    assert parameters[12].default is phase155

    module = inspect.getmodule(phase177)
    assert module is not None
    assert (
        module.__name__
        == "ai_office.engine.runtime_result_to_persisted_running_execution_orchestration_boundary"
    )
    source = inspect.getsource(module)
    tree = ast.parse(source)
    called = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "phase176_function" in called
    assert "phase147_function" in called
    assert "phase155_function" in called
    lower_direct_calls = [
        name
        for name in called
        if name
        in (
            "route_runtime_result_to_approved_preparation_orchestration_boundary",
            "route_runtime_result_to_progression_orchestration_boundary",
            "route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
            "route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
            "route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary",
            "route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        )
    ]
    assert lower_direct_calls == []
    # phase147_function must be used only inside the capture-only adapter.
    adapter = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "capture_phase147"
    )
    adapter_calls = [
        node.func.id
        for node in ast.walk(adapter)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "phase147_function" in adapter_calls
    outside_adapter = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "phase147_function"
    ]
    adapter_region = (
        adapter.lineno,
        getattr(adapter, "end_lineno", adapter.lineno),
    )
    for node in outside_adapter:
        assert adapter_region[0] <= node.lineno <= adapter_region[1]


def test_prepared_execution_success_route_exact_stage_order_and_contract(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    start = constructed_start(values)
    state_bytes = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    persistence = RunningStatePersistenceResult(len(state_bytes))
    step7 = step7_success(wf)
    execution_approval = object()
    transport = object()
    api_key = object()
    phase176_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    phase147_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    phase155_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def stub_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        phase176_calls.append((res, wf_value, approval_value, employee_value, sp, ep))
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(
            terminal_history_bytes(wf, result.step_index, request_id_none=True)
        )  # type: ignore[arg-type]
        phase147_function = kwargs["phase147_function"]
        return phase147_function(start, wf_value, employee_value, sp, ep)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        phase147_calls.append((args, kwargs))
        return persistence

    def stub_phase155(*args: object, **kwargs: object) -> object:
        phase155_calls.append((args, kwargs))
        return step7

    out = phase177(
        result,
        wf,
        approval,
        employee,
        values["state_path"],
        values["events_path"],
        TOOLS,
        api_key,
        execution_approval,
        transport,
        phase176_function=stub_phase176,  # type: ignore[arg-type]
        phase147_function=stub_phase147,  # type: ignore[arg-type]
        phase155_function=stub_phase155,  # type: ignore[arg-type]
    )
    assert out is step7
    assert len(phase176_calls) == 1
    assert len(phase147_calls) == 1
    assert len(phase155_calls) == 1
    phase176_args = phase176_calls[0]
    assert phase176_args[0] is result
    assert phase176_args[1] is wf
    assert phase176_args[2] is approval
    assert phase176_args[3] is employee
    assert phase176_args[4] is values["state_path"]
    assert phase176_args[5] is values["events_path"]
    phase147_args = phase147_calls[0][0]
    assert phase147_args[0] is start
    assert phase147_args[1] is wf
    assert phase147_args[2] is employee
    assert phase147_args[3] is values["state_path"]
    assert phase147_args[4] is values["events_path"]
    phase155_args = phase155_calls[0][0]
    assert phase155_args[0] is persistence
    assert phase155_args[1] is start
    assert phase155_args[2] is wf
    assert phase155_args[3] is employee
    assert phase155_args[4] is values["state_path"]
    assert phase155_args[5] is values["events_path"]
    assert phase155_args[6] is TOOLS
    assert phase155_args[7] is api_key
    assert phase155_args[8] is execution_approval
    assert phase155_args[9] is transport
    assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == terminal_history_bytes(wf, 6, request_id_none=True)  # type: ignore[arg-type]


def test_prepared_execution_failure_route_exact_return_contract(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    start = constructed_start(values)
    state_bytes = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    persistence = RunningStatePersistenceResult(len(state_bytes))
    step7 = step7_failure(wf)
    phase147_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    phase155_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def stub_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(
            terminal_history_bytes(wf, result.step_index, request_id_none=True)
        )  # type: ignore[arg-type]
        phase147_function = kwargs["phase147_function"]
        return phase147_function(start, wf_value, employee_value, sp, ep)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        phase147_calls.append((args, kwargs))
        return persistence

    def stub_phase155(*args: object, **kwargs: object) -> object:
        phase155_calls.append((args, kwargs))
        return step7

    out = phase177(
        result,
        wf,
        approval,
        employee,
        values["state_path"],
        values["events_path"],
        TOOLS,
        object(),
        object(),
        object(),
        phase176_function=stub_phase176,  # type: ignore[arg-type]
        phase147_function=stub_phase147,  # type: ignore[arg-type]
        phase155_function=stub_phase155,  # type: ignore[arg-type]
    )
    assert out is step7
    assert type(out) is StepRuntimeExecutionFailure
    assert len(phase147_calls) == 1
    assert len(phase155_calls) == 1
    assert phase155_calls[0][0][0] is persistence
    assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]


def test_workflow_complete_stop_route_exact_identity_phase155_zero(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    stop = complete_decision(wf, 6)
    phase176_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    phase155_stub, phase155_calls = recording_stub(None)

    def stub_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        phase176_calls.append((res, wf_value, approval_value, employee_value, sp, ep))
        phase147_function = kwargs["phase147_function"]
        return phase147_function(res, wf_value, employee_value, sp, ep)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        return args[0]

    out = phase177(
        stop,
        wf,
        None,
        None,
        values["state_path"],
        values["events_path"],
        None,
        None,
        None,
        None,
        phase176_function=stub_phase176,  # type: ignore[arg-type]
        phase147_function=stub_phase147,  # type: ignore[arg-type]
        phase155_function=phase155_stub,  # type: ignore[arg-type]
    )
    assert out is stop
    assert len(phase176_calls) == 1
    assert len(phase155_calls) == 0
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_persisted_failure_stop_route_exact_identity_phase155_zero(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    stop = failure_outcome(wf, 6)
    phase176_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    phase155_stub, phase155_calls = recording_stub(None)

    def stub_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        phase176_calls.append((res, wf_value, approval_value, employee_value, sp, ep))
        phase147_function = kwargs["phase147_function"]
        return phase147_function(res, wf_value, employee_value, sp, ep)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        return args[0]

    out = phase177(
        stop,
        wf,
        None,
        None,
        values["state_path"],
        values["events_path"],
        None,
        None,
        None,
        None,
        phase176_function=stub_phase176,  # type: ignore[arg-type]
        phase147_function=stub_phase147,  # type: ignore[arg-type]
        phase155_function=phase155_stub,  # type: ignore[arg-type]
    )
    assert out is stop
    assert len(phase176_calls) == 1
    assert len(phase155_calls) == 0
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_non_callable_dependency_configuration_rejects_before_execution(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            None,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function="not-callable",  # type: ignore[arg-type]
        ),
        "configuration",
    )
    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            None,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase147_function=123,  # type: ignore[arg-type]
        ),
        "configuration",
    )
    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            None,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase155_function=None,  # type: ignore[arg-type]
        ),
        "configuration",
    )
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_phase176_recognized_safe_error_identity_phase155_zero(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    safe_error = Phase176ContractError("phase147_contract")
    phase176_stub, phase176_calls = raising_stub(safe_error)
    phase155_stub, phase155_calls = recording_stub(None)
    with pytest.raises(Phase176ContractError) as exc:
        phase177(
            result,
            wf,
            None,
            None,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=phase176_stub,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        )
    assert exc.value is safe_error
    assert len(phase176_calls) == 1
    assert len(phase155_calls) == 0
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_phase176_unexpected_exception_sanitized_phase155_zero(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    phase176_stub, _ = raising_stub(RuntimeError("phase176-secret"))
    phase155_stub, phase155_calls = recording_stub(None)
    with pytest.raises(Phase177Error) as exc:
        phase177(
            result,
            wf,
            None,
            None,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=phase176_stub,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "dependency_error"
    assert "phase176-secret" not in str(exc.value)
    assert len(phase155_calls) == 0
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_malformed_phase176_return_or_missing_capture_phase176_contract(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    employee = employee_for(prepare_decision(wf, 6))
    start = constructed_start(values)
    state_bytes = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    phase155_stub, phase155_calls = recording_stub(None)

    def malformed_phase176(*args: object, **kwargs: object) -> object:
        return "garbage"

    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=malformed_phase176,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        ),
        "phase176_contract",
    )
    assert len(phase155_calls) == 0
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]

    def no_capture_phase176(*args: object, **kwargs: object) -> object:
        Path(values["state_path"]).write_bytes(state_bytes)  # type: ignore[arg-type]
        return RunningStatePersistenceResult(len(state_bytes))

    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=no_capture_phase176,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        ),
        "phase176_contract",
    )
    assert len(phase155_calls) == 0
    # Pre-Phase176 bytes must not be restored (Phase 176 may have committed).
    assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]

    # Malformed stop subcases: 6-step workflow whose final step just finished.
    # Phase 176 must return the exact workflow_complete / persisted_failure
    # stop; every field deviation is phase176_contract with Phase 155 zero
    # calls and untouched targets.
    stop_values = canonical_running_setup(tmp_path / "stop", steps=6, current=6)
    wf6 = stop_values["workflow"]  # type: ignore[arg-type]
    stop_state_path = stop_values["state_path"]  # type: ignore[arg-type]
    stop_events_path = stop_values["events_path"]  # type: ignore[arg-type]
    stop_phase155_stub, stop_phase155_calls = recording_stub(None)

    final_success = runtime_success(wf6, 6)
    malformed_completes = [
        replace(complete_decision(wf6, 6), reason="unexpected_reason"),
        replace(complete_decision(wf6, 6), workflow_id="other"),
        replace(complete_decision(wf6, 6), current_step_id="step-5"),
        replace(complete_decision(wf6, 6), current_step_index=5),
        replace(complete_decision(wf6, 6), current_employee_id="e5"),
        replace(complete_decision(wf6, 6), next_step_id="step-7"),
        replace(complete_decision(wf6, 6), next_step_index=7),
        replace(complete_decision(wf6, 6), next_employee_id="e7"),
        failure_outcome(wf6, 6),  # wrong stop type for a success result
    ]
    for malformed_complete in malformed_completes:

        def malformed_complete_stub(*args: object, **kwargs: object) -> object:
            return malformed_complete

        assert_classification(
            lambda: phase177(
                final_success,
                wf6,
                None,
                None,
                stop_state_path,
                stop_events_path,
                None,
                None,
                None,
                None,
                phase176_function=malformed_complete_stub,  # type: ignore[arg-type]
                phase155_function=stop_phase155_stub,  # type: ignore[arg-type]
            ),
            "phase176_contract",
        )
        assert len(stop_phase155_calls) == 0
        assert Path(stop_state_path).read_bytes() == stop_values["state_before"]  # type: ignore[arg-type]
        assert Path(stop_events_path).read_bytes() == stop_values["events_before"]  # type: ignore[arg-type]

    # A non-terminal success must not stop: workflow_complete is only valid
    # when the just-finished step is the final step.
    def non_final_success_stub(*args: object, **kwargs: object) -> object:
        return complete_decision(wf6, 6)

    assert_classification(
        lambda: phase177(
            runtime_success(wf6, 5),
            wf6,
            None,
            None,
            stop_state_path,
            stop_events_path,
            None,
            None,
            None,
            None,
            phase176_function=non_final_success_stub,  # type: ignore[arg-type]
            phase155_function=stop_phase155_stub,  # type: ignore[arg-type]
        ),
        "phase176_contract",
    )
    assert len(stop_phase155_calls) == 0
    assert Path(stop_state_path).read_bytes() == stop_values["state_before"]  # type: ignore[arg-type]
    assert Path(stop_events_path).read_bytes() == stop_values["events_before"]  # type: ignore[arg-type]

    final_failure = runtime_failure(wf6, 6)
    malformed_failures = [
        replace(failure_outcome(wf6, 6), outcome="persisted_success"),
        replace(failure_outcome(wf6, 6), workflow_id="other"),
        replace(failure_outcome(wf6, 6), current_step_id="step-5"),
        replace(failure_outcome(wf6, 6), current_step_index=5),
        replace(failure_outcome(wf6, 6), current_employee_id="e5"),
        replace(failure_outcome(wf6, 6), failure_category="invalid_response"),
        complete_decision(wf6, 6),  # wrong stop type for a failure result
    ]
    for malformed_failure in malformed_failures:

        def malformed_failure_stub(*args: object, **kwargs: object) -> object:
            return malformed_failure

        assert_classification(
            lambda: phase177(
                final_failure,
                wf6,
                None,
                None,
                stop_state_path,
                stop_events_path,
                None,
                None,
                None,
                None,
                phase176_function=malformed_failure_stub,  # type: ignore[arg-type]
                phase155_function=stop_phase155_stub,  # type: ignore[arg-type]
            ),
            "phase176_contract",
        )
        assert len(stop_phase155_calls) == 0
        assert Path(stop_state_path).read_bytes() == stop_values["state_before"]  # type: ignore[arg-type]
        assert Path(stop_events_path).read_bytes() == stop_values["events_before"]  # type: ignore[arg-type]

    # Malformed committed event history: stub writes a valid state plus a
    # partial / wrong-transition terminal history -> phase176_contract, Phase
    # 155 zero calls, and the written bytes are not rolled back.
    def partial_events_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(terminal_history_bytes(wf, result.step_index - 1))  # type: ignore[arg-type]
        return RunningStatePersistenceResult(len(state_bytes))

    partial_events_bytes = terminal_history_bytes(wf, result.step_index - 1)
    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=partial_events_phase176,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        ),
        "phase176_contract",
    )
    assert len(phase155_calls) == 0
    assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == partial_events_bytes  # type: ignore[arg-type]

    wrong_transition_events = [
        predecessor_event(wf.steps[i].id, i + 1, output_text="", request_id=None)
        for i in range(result.step_index)
    ]
    wrong_transition_events[-1] = replace(
        wrong_transition_events[-1], next_status="failed"
    )
    wrong_transition_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in wrong_transition_events
    ).encode("utf-8")

    def wrong_transition_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(wrong_transition_bytes)  # type: ignore[arg-type]
        return RunningStatePersistenceResult(len(state_bytes))

    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=wrong_transition_phase176,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        ),
        "phase176_contract",
    )
    assert len(phase155_calls) == 0
    assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == wrong_transition_bytes  # type: ignore[arg-type]

    # Malformed committed event history: invalid UTF-8 bytes must classify as
    # phase176_contract (no leaked UnicodeDecodeError), Phase 155 zero calls,
    # and the written bytes are not rolled back.
    invalid_utf8_bytes = b"\xff\xfe invalid-utf8"

    def invalid_utf8_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(invalid_utf8_bytes)  # type: ignore[arg-type]
        return RunningStatePersistenceResult(len(state_bytes))

    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=invalid_utf8_phase176,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        ),
        "phase176_contract",
    )
    assert len(phase155_calls) == 0
    assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == invalid_utf8_bytes  # type: ignore[arg-type]


def test_capture_contract_narrowness_phase176_contract(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    employee = employee_for(prepare_decision(wf, 6))
    start = constructed_start(values)
    state_bytes = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    phase155_stub, phase155_calls = recording_stub(None)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        return RunningStatePersistenceResult(len(state_bytes))

    def multiple_capture_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(
            terminal_history_bytes(wf, result.step_index, request_id_none=True)
        )  # type: ignore[arg-type]
        phase147_function = kwargs["phase147_function"]
        phase147_function(start, wf_value, employee_value, sp, ep)
        return phase147_function(start, wf_value, employee_value, sp, ep)

    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=multiple_capture_phase176,  # type: ignore[arg-type]
            phase147_function=stub_phase147,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        ),
        "phase176_contract",
    )
    assert len(phase155_calls) == 0

    def wrong_capture_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(
            terminal_history_bytes(wf, result.step_index, request_id_none=True)
        )  # type: ignore[arg-type]
        phase147_function = kwargs["phase147_function"]
        return phase147_function("not-a-start", wf_value, employee_value, sp, ep)

    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=wrong_capture_phase176,  # type: ignore[arg-type]
            phase147_function=stub_phase147,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        ),
        "phase176_contract",
    )
    assert len(phase155_calls) == 0

    # Malformed exact captured starts: an exact PreparedStepExecutionStart
    # whose request / running_state is not the exact type must classify as
    # phase176_contract (no leaked AttributeError/TypeError), Phase 155 zero
    # calls, and the written bytes are not rolled back.
    malformed_captured_starts = [
        replace(start, request="not-a-request"),
        replace(start, running_state="not-a-state"),
        replace(start, request=None),  # type: ignore[arg-type]
    ]
    for malformed_start in malformed_captured_starts:

        def malformed_capture_phase176(
            res: object,
            wf_value: object,
            approval_value: object,
            employee_value: object,
            sp: object,
            ep: object,
            **kwargs: object,
        ) -> object:
            Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
            Path(ep).write_bytes(
                terminal_history_bytes(wf, result.step_index, request_id_none=True)
            )  # type: ignore[arg-type]
            phase147_function = kwargs["phase147_function"]
            return phase147_function(
                malformed_start, wf_value, employee_value, sp, ep
            )

        assert_classification(
            lambda: phase177(
                result,
                wf,
                None,
                employee,
                values["state_path"],
                values["events_path"],
                None,
                None,
                None,
                None,
                phase176_function=malformed_capture_phase176,  # type: ignore[arg-type]
                phase147_function=stub_phase147,  # type: ignore[arg-type]
                phase155_function=phase155_stub,  # type: ignore[arg-type]
            ),
            "phase176_contract",
        )
        assert len(phase155_calls) == 0
        assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]
        assert Path(values["events_path"]).read_bytes() == terminal_history_bytes(wf, result.step_index, request_id_none=True)  # type: ignore[arg-type]

    wrong_state = serialize_workflow_execution_state_json(
        replace(start.running_state, current_step_id="step-6", current_step_index=6)
    ).encode("utf-8")

    def unlinked_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(wrong_state)  # type: ignore[arg-type]
        phase147_function = kwargs["phase147_function"]
        return phase147_function(start, wf_value, employee_value, sp, ep)

    assert_classification(
        lambda: phase177(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=unlinked_phase176,  # type: ignore[arg-type]
            phase147_function=stub_phase147,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        ),
        "phase176_contract",
    )
    assert len(phase155_calls) == 0

    stop_values = canonical_running_setup(tmp_path / "stop", steps=6, current=6)
    stop_wf = stop_values["workflow"]  # type: ignore[arg-type]
    stop = complete_decision(stop_wf, 6)

    def stop_capture_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        phase147_function = kwargs["phase147_function"]
        phase147_function(start, wf_value, employee_value, sp, ep)
        return res

    assert_classification(
        lambda: phase177(
            stop,
            stop_wf,
            None,
            None,
            stop_values["state_path"],
            stop_values["events_path"],
            None,
            None,
            None,
            None,
            phase176_function=stop_capture_phase176,  # type: ignore[arg-type]
            phase147_function=stub_phase147,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        ),
        "phase176_contract",
    )
    assert len(phase155_calls) == 0


def test_phase155_recognized_safe_error_identity_restored_to_committed(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    start = constructed_start(values)
    state_bytes = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    persistence = RunningStatePersistenceResult(len(state_bytes))
    safe_error = Phase155ContractError("runtime_contract")

    def stub_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(
            terminal_history_bytes(wf, result.step_index, request_id_none=True)
        )  # type: ignore[arg-type]
        phase147_function = kwargs["phase147_function"]
        return phase147_function(start, wf_value, employee_value, sp, ep)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        return persistence

    def mutating_raising_stub(*args: object, **kwargs: object) -> object:
        Path(values["state_path"]).write_bytes(b"tampered")  # type: ignore[arg-type]
        Path(values["events_path"]).write_bytes(b"tampered-events")  # type: ignore[arg-type]
        raise safe_error

    with pytest.raises(Phase155ContractError) as exc:
        phase177(
            result,
            wf,
            approval,
            employee,
            values["state_path"],
            values["events_path"],
            TOOLS,
            object(),
            object(),
            object(),
            phase176_function=stub_phase176,  # type: ignore[arg-type]
            phase147_function=stub_phase147,  # type: ignore[arg-type]
            phase155_function=mutating_raising_stub,  # type: ignore[arg-type]
        )
    assert exc.value is safe_error
    assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == terminal_history_bytes(wf, 6, request_id_none=True)  # type: ignore[arg-type]


def test_phase155_unexpected_exception_restored_then_dependency_error(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    start = constructed_start(values)
    state_bytes = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    persistence = RunningStatePersistenceResult(len(state_bytes))

    def stub_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(
            terminal_history_bytes(wf, result.step_index, request_id_none=True)
        )  # type: ignore[arg-type]
        phase147_function = kwargs["phase147_function"]
        return phase147_function(start, wf_value, employee_value, sp, ep)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        return persistence

    def mutating_raising_stub(*args: object, **kwargs: object) -> object:
        Path(values["state_path"]).write_bytes(b"tampered")  # type: ignore[arg-type]
        raise RuntimeError("phase155-secret")

    with pytest.raises(Phase177Error) as exc:
        phase177(
            result,
            wf,
            approval,
            employee,
            values["state_path"],
            values["events_path"],
            TOOLS,
            object(),
            object(),
            object(),
            phase176_function=stub_phase176,  # type: ignore[arg-type]
            phase147_function=stub_phase147,  # type: ignore[arg-type]
            phase155_function=mutating_raising_stub,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "dependency_error"
    assert "phase155-secret" not in str(exc.value)
    assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == terminal_history_bytes(wf, 6, request_id_none=True)  # type: ignore[arg-type]


def test_malformed_phase155_return_phase155_contract_restored(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    start = constructed_start(values)
    state_bytes = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    persistence = RunningStatePersistenceResult(len(state_bytes))

    def stub_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(
            terminal_history_bytes(wf, result.step_index, request_id_none=True)
        )  # type: ignore[arg-type]
        phase147_function = kwargs["phase147_function"]
        return phase147_function(start, wf_value, employee_value, sp, ep)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        return persistence

    malformed_success = step7_success(wf)
    malformed_failure = step7_failure(wf)
    malformed_returns = [
        None,
        "garbage",
        object(),
        RunningStatePersistenceResult(0),
        # Malformed invocation results: step-7 identity intact, invocation
        # broken (the captured start is the step-7 running state).
        replace(
            malformed_success,
            invocation_result=replace(
                malformed_success.invocation_result, provider="other"
            ),
        ),
        replace(
            malformed_success,
            invocation_result=replace(
                malformed_success.invocation_result, response_id=""
            ),
        ),
        replace(
            malformed_success,
            invocation_result=replace(
                malformed_success.invocation_result, request_id=""
            ),
        ),
        replace(
            malformed_success,
            invocation_result=replace(
                malformed_success.invocation_result, status=""
            ),
        ),
        replace(
            malformed_success,
            invocation_result=replace(
                malformed_success.invocation_result, text="mismatched"
            ),
        ),
        replace(
            malformed_success,
            invocation_result=replace(
                malformed_success.invocation_result, text_parts=("output", "extra")
            ),
        ),
        replace(
            malformed_success,
            invocation_result="not-an-invocation-result",  # type: ignore[arg-type]
        ),
        replace(
            malformed_failure,
            invocation_result=replace(
                malformed_failure.invocation_result, provider="other"
            ),
        ),
        replace(
            malformed_failure,
            invocation_result=replace(
                malformed_failure.invocation_result, category="not_a_category"
            ),
        ),
        replace(
            malformed_failure,
            invocation_result=replace(
                malformed_failure.invocation_result, category=["unhashable"]  # type: ignore[arg-type]
            ),
        ),
        replace(
            malformed_failure,
            invocation_result=replace(
                malformed_failure.invocation_result, message=""
            ),
        ),
        replace(
            malformed_failure,
            invocation_result=replace(
                malformed_failure.invocation_result, status_code="500"  # type: ignore[arg-type]
            ),
        ),
        replace(
            malformed_failure,
            invocation_result=replace(
                malformed_failure.invocation_result,
                category="invalid_response",
                status_code=500,
            ),
        ),
    ]
    for malformed in malformed_returns:
        def malformed_stub(*args: object, **kwargs: object) -> object:
            return malformed

        with pytest.raises(Phase177Error) as exc:
            phase177(
                result,
                wf,
                approval,
                employee,
                values["state_path"],
                values["events_path"],
                TOOLS,
                object(),
                object(),
                object(),
                phase176_function=stub_phase176,  # type: ignore[arg-type]
                phase147_function=stub_phase147,  # type: ignore[arg-type]
                phase155_function=malformed_stub,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase155_contract"
        assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]
        assert Path(values["events_path"]).read_bytes() == terminal_history_bytes(wf, 6, request_id_none=True)  # type: ignore[arg-type]


def test_phase155_valid_return_with_unauthorized_mutation_committed_mutation(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    start = constructed_start(values)
    state_bytes = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    persistence = RunningStatePersistenceResult(len(state_bytes))
    step7 = step7_success(wf)

    def stub_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(
            terminal_history_bytes(wf, result.step_index, request_id_none=True)
        )  # type: ignore[arg-type]
        phase147_function = kwargs["phase147_function"]
        return phase147_function(start, wf_value, employee_value, sp, ep)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        return persistence

    mutators = [
        lambda: Path(values["state_path"]).write_bytes(b"tampered"),  # type: ignore[arg-type]
        lambda: Path(values["events_path"]).write_bytes(b"tampered-events"),  # type: ignore[arg-type]
        lambda: Path(values["state_path"]).unlink(),  # type: ignore[arg-type]
        lambda: Path(values["events_path"]).unlink(),  # type: ignore[arg-type]
    ]
    for mutator in mutators:
        def mutating_stub(*args: object, **kwargs: object) -> object:
            mutator()
            return step7

        with pytest.raises(Phase177Error) as exc:
            phase177(
                result,
                wf,
                approval,
                employee,
                values["state_path"],
                values["events_path"],
                TOOLS,
                object(),
                object(),
                object(),
                phase176_function=stub_phase176,  # type: ignore[arg-type]
                phase147_function=stub_phase147,  # type: ignore[arg-type]
                phase155_function=mutating_stub,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "committed_mutation"
        assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]
        assert Path(values["events_path"]).read_bytes() == terminal_history_bytes(wf, 6, request_id_none=True)  # type: ignore[arg-type]


def test_preparation_execution_inputs_not_prevalidated_phase176_runs_first(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    employee = employee_for(prepare_decision(wf, 6))
    start = constructed_start(values)
    state_bytes = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    persistence = RunningStatePersistenceResult(len(state_bytes))
    phase176_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def stub_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        phase176_calls.append((res, wf_value, approval_value, employee_value, sp, ep))
        Path(sp).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(ep).write_bytes(
            terminal_history_bytes(wf, result.step_index, request_id_none=True)
        )  # type: ignore[arg-type]
        phase147_function = kwargs["phase147_function"]
        return phase147_function(start, wf_value, employee_value, sp, ep)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        return persistence

    # Invalid/missing execution input (execution_approval=None) must not stop
    # Phase176: Phase176 runs first and durably commits; Phase155 rejects.
    safe_error = Phase155ContractError("approval_contract")

    def rejecting_phase155(*args: object, **kwargs: object) -> object:
        raise safe_error

    with pytest.raises(Phase155ContractError) as exc:
        phase177(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            TOOLS,
            object(),
            None,  # missing execution approval
            object(),
            phase176_function=stub_phase176,  # type: ignore[arg-type]
            phase147_function=stub_phase147,  # type: ignore[arg-type]
            phase155_function=rejecting_phase155,  # type: ignore[arg-type]
        )
    assert exc.value is safe_error
    assert len(phase176_calls) == 1
    assert Path(values["state_path"]).read_bytes() == state_bytes  # type: ignore[arg-type]

    # Phase176 itself owns/rejects preparation errors: Phase155 zero.
    phase155_stub, phase155_calls = recording_stub(None)
    prep_error = Phase176ContractError("phase175_contract")

    def rejecting_phase176(*args: object, **kwargs: object) -> object:
        raise prep_error

    with pytest.raises(Phase176ContractError) as exc:
        phase177(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            TOOLS,
            object(),
            None,
            object(),
            phase176_function=rejecting_phase176,  # type: ignore[arg-type]
            phase155_function=phase155_stub,  # type: ignore[arg-type]
        )
    assert exc.value is prep_error
    assert len(phase155_calls) == 0


def test_rollback_failure_after_phase155_mutation_both_restorations_once(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    start = constructed_start(values)
    state_bytes = serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    persistence = RunningStatePersistenceResult(len(state_bytes))
    state_path = Path(values["state_path"])  # type: ignore[arg-type]
    events_path = Path(values["events_path"])  # type: ignore[arg-type]
    committed_events = terminal_history_bytes(wf, 6, request_id_none=True)

    def stub_phase176(
        res: object,
        wf_value: object,
        approval_value: object,
        employee_value: object,
        sp: object,
        ep: object,
        **kwargs: object,
    ) -> object:
        sp.write_bytes(state_bytes)  # type: ignore[arg-type]
        ep.write_bytes(terminal_history_bytes(wf, result.step_index, request_id_none=True))
        phase147_function = kwargs["phase147_function"]
        return phase147_function(start, wf_value, employee_value, sp, ep)

    def stub_phase147(*args: object, **kwargs: object) -> object:
        return persistence

    def sabotage_stub(*args: object, **kwargs: object) -> object:
        # State target becomes an un-writable directory (restore will fail);
        # events target is tampered (restore will succeed).  Both restorations\n        # must be attempted exactly once.
        state_path.unlink()
        state_path.mkdir()
        events_path.write_bytes(b"tampered-events")
        return step7_success(wf)

    with pytest.raises(Phase177Error) as exc:
        phase177(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            object(),
            object(),
            object(),
            phase176_function=stub_phase176,  # type: ignore[arg-type]
            phase147_function=stub_phase147,  # type: ignore[arg-type]
            phase155_function=sabotage_stub,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "rollback_failure"
    assert state_path.is_dir()
    assert events_path.read_bytes() == committed_events


def test_real_a_accumulated_provenance_step7_execution_success(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    start = constructed_start(values)
    execution_approval = approve_model_invocation_execution(
        start.request, TOOLS, provider="openai", approved_by="test", approval_id="approval-id"
    )
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    out = phase177(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        OpenAIApiKey(value=SecretStr("synthetic")),
        execution_approval,
        success_transport(calls),
    )
    assert type(out) is StepRuntimeExecutionSuccess
    assert out.workflow_id == "w"
    assert out.step_id == "step-7"
    assert out.step_index == 7
    assert out.employee_id == "e7"
    assert len(calls) == 1
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "running"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    assert final_state.current_employee_id == "e7"
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    assert final_state.last_failure_category is None
    events = load_events(events_path)
    assert len(events) == 6
    assert all(event["event_type"] == "step_succeeded" for event in events)
    assert events[4]["step_id"] == "step-5"
    assert events[4]["provider"] == "openai"
    assert events[4]["request_id"] is None
    assert events[5]["step_id"] == "step-6"
    assert events[5]["provider"] == "openai"
    assert events[5]["request_id"] is None
    # Phase155 execution leaves the Phase176 committed bytes unchanged.
    _, alone_state, alone_events = phase176_alone(tmp_path / "alone", steps=7, current=6)
    assert state_path.read_bytes() == alone_state
    assert events_path.read_bytes() == alone_events


def test_real_b_accumulated_provenance_step7_execution_failure(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    start = constructed_start(values)
    execution_approval = approve_model_invocation_execution(
        start.request, TOOLS, provider="openai", approved_by="test", approval_id="approval-id"
    )
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    out = phase177(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        OpenAIApiKey(value=SecretStr("synthetic")),
        execution_approval,
        failure_transport(calls),
    )
    assert type(out) is StepRuntimeExecutionFailure
    assert out.workflow_id == "w"
    assert out.step_id == "step-7"
    assert out.step_index == 7
    assert out.employee_id == "e7"
    assert out.invocation_result.category == "api_error"
    assert len(calls) == 1
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "running"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    events = load_events(events_path)
    assert len(events) == 6
    assert events[4]["request_id"] is None
    assert events[5]["request_id"] is None
    _, alone_state, alone_events = phase176_alone(tmp_path / "alone", steps=7, current=6)
    assert state_path.read_bytes() == alone_state
    assert events_path.read_bytes() == alone_events


def test_real_c_both_stop_routes_inline_subcases(tmp_path: Path) -> None:
    # Subcase 1: six-step terminal success -> exact workflow_complete identity.
    values = canonical_running_setup(tmp_path / "s", steps=6, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    out = phase177(
        result, wf, None, None, state_path, events_path, None, None, None, None
    )
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "workflow_complete"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-6"
    assert out.current_step_index == 6
    assert out.current_employee_id == "e6"
    assert out.next_step_id is None
    assert out.next_step_index is None
    assert out.next_employee_id is None
    assert out.reason == "last_step_succeeded"
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "succeeded"
    assert final_state.current_step_index == 6
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    events = load_events(events_path)
    assert len(events) == 6
    assert events[-1]["event_type"] == "step_succeeded"
    assert events[-1]["step_id"] == "step-6"
    _, alone_state, alone_events = phase176_alone(tmp_path / "s-alone", steps=6, current=6)
    assert state_path.read_bytes() == alone_state
    assert events_path.read_bytes() == alone_events

    # Subcase 2: six-step terminal failure -> exact persisted_failure identity.
    values2 = canonical_running_setup(tmp_path / "f", steps=6, current=6)
    wf2 = values2["workflow"]  # type: ignore[arg-type]
    result2 = runtime_failure(wf2, 6)
    state_path2 = values2["state_path"]  # type: ignore[arg-type]
    events_path2 = values2["events_path"]  # type: ignore[arg-type]
    out2 = phase177(
        result2, wf2, None, None, state_path2, events_path2, None, None, None, None
    )
    assert type(out2) is PersistedExecutionOutcome
    assert out2.outcome == "persisted_failure"
    assert out2.workflow_id == "w"
    assert out2.current_step_id == "step-6"
    assert out2.current_step_index == 6
    assert out2.current_employee_id == "e6"
    assert out2.failure_category == "api_error"
    final_state2 = load_workflow_execution_state(state_path2)
    assert final_state2.status == "failed"
    assert final_state2.current_step_index == 6
    assert final_state2.last_failure_category == "api_error"
    events2 = load_events(events_path2)
    assert len(events2) == 6
    assert events2[-1]["event_type"] == "step_failed"
    assert events2[-1]["step_id"] == "step-6"
    _, alone_state2, alone_events2 = phase176_alone(
        tmp_path / "f-alone", steps=6, current=6, fail=True
    )
    assert state_path2.read_bytes() == alone_state2
    assert events_path2.read_bytes() == alone_events2


def test_real_d_invalid_execution_approval_after_durable_phase176_commit(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    with pytest.raises(Phase155ContractError) as exc:
        phase177(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            OpenAIApiKey(value=SecretStr("synthetic")),
            None,  # missing/invalid execution approval
            success_transport(calls),
        )
    assert exc.value.detail.classification == "approval_contract"
    assert len(calls) == 0
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "running"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    events = load_events(events_path)
    assert len(events) == 6
    assert events[-1]["step_id"] == "step-6"
    # Durable Phase176 commit remains intact; no pre-Phase176 rollback.
    _, alone_state, alone_events = phase176_alone(tmp_path / "alone", steps=7, current=6)
    assert state_path.read_bytes() == alone_state
    assert events_path.read_bytes() == alone_events
