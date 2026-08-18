"""Focused and real-default tests for Issue #377 (Phase 178).

Phase 178 composes the public Phase 177 (post-runtime persisted next-step
running execution) with the public Phase 172 (post-runtime persistence →
classification → progression) exactly once each, returning the exact stop
object by identity with Phase 172 zero calls for stop inputs, or the exact
progression decision / persisted-failure outcome for a valid Phase-177
runtime result, thinly proving the final durable target effect without
reimplementing Phase 161 / 143 / 144 in full.

Requirement-to-test mapping (Issue #377):
- public signature / exact dependency defaults / error hierarchy / exports /
  source audit
  -> test_public_signature_default_dependencies_error_hierarchy_and_source_audit
- runtime-success Phase177 -> Phase172 exact stage order / canonical
  argument identity / exactly once
  -> test_runtime_success_route_exact_stage_order_canonical_identity_once
- runtime-failure exact stage order / canonical identity / exactly once
  -> test_runtime_failure_route_exact_stage_order_canonical_identity_once
- both original stop routes exact identity, target unchanged, Phase172 zero
  -> test_both_stop_routes_exact_identity_target_unchanged_phase172_zero
- exact input-family/subclass/attribute-compatible substitute rejection and
  target validation
  -> test_input_family_subclass_substitute_rejection_and_target_validation
- non-callable dependency configuration rejection before execution
  -> test_non_callable_dependency_configuration_rejects_before_execution
- Phase177 recognized safe-error identity, Phase172 zero, no outer rollback
  -> test_phase177_recognized_safe_error_identity_phase172_zero
- Phase177 unexpected exception sanitization, Phase172 zero, no outer
  rollback
  -> test_phase177_unexpected_exception_sanitized_phase172_zero
- malformed Phase177 runtime return/running-snapshot/history ->
  phase177_contract, Phase172 zero, no outer rollback
  -> test_malformed_phase177_runtime_return_snapshot_history_contract
- malformed Phase177 stop identity/target behavior -> phase177_contract,
  Phase172 zero
  -> test_malformed_phase177_stop_identity_target_contract
- Phase172 recognized safe-error identity after Phase177, no outer rollback
  -> test_phase172_recognized_safe_error_identity_after_phase177
- Phase172 unexpected exception sanitization after Phase177, no outer
  rollback
  -> test_phase172_unexpected_exception_sanitized_after_phase177
- malformed Phase172 return/final durable target -> phase172_contract, no
  outer rollback
  -> test_malformed_phase172_return_or_final_target_contract
- success non-final exact durable target + prepare_next_step linkage
  -> test_success_non_final_exact_durable_target_prepare_next_step
- success final exact durable target + workflow_complete linkage
  -> test_success_final_exact_durable_target_workflow_complete
- failure exact durable target + persisted_failure linkage; no retry / no
  direct lower calls / no sensitive details covered in the source/error
  subcases
  -> test_failure_exact_durable_target_persisted_failure_linkage
- real A: eight-step step6 success -> real Phase177 synthetic step7 success
  -> real Phase172 persistence -> exact prepare step8
  -> test_real_a_eight_step_step7_success_prepares_step8
- real B: seven-step step6 success -> real Phase177 synthetic step7 success
  -> real Phase172 persistence -> exact workflow_complete
  -> test_real_b_seven_step_step7_success_workflow_complete
- real C: eight-step step6 success -> real Phase177 deterministic step7
  failure -> real Phase172 persistence -> exact persisted_failure
  -> test_real_c_eight_step_step7_failure_persisted_failure
- real D: exact original workflow_complete + persisted_failure stop routes
  as inline subcases, proving Phase172 zero and byte preservation through
  the real Phase177 stop path
  -> test_real_d_stop_routes_exact_identity_bytes_preserved_phase172_zero
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
    PreparedStepExecutionStart,
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError,
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase178Error,
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_runtime_result_to_persisted_running_execution_orchestration_boundary,
    route_runtime_result_to_persisted_running_execution_progression_orchestration_boundary,
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.runtime_result_to_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryError as Phase176SafeError,
)
from ai_office.engine.runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    RuntimeResultTransitionPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase161SafeError,
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
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition

phase178 = route_runtime_result_to_persisted_running_execution_progression_orchestration_boundary
phase177 = route_runtime_result_to_persisted_running_execution_orchestration_boundary
phase172 = route_runtime_result_to_progression_orchestration_boundary

TOOLS = (
    ToolDefinition("tool-one", "Tool One", ()),
    ToolDefinition("tool-two", "Tool Two", ()),
)


def workflow(steps: int) -> WorkflowDefinition:
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
    root: Path, steps: int = 8, current: int = 6
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


def terminal_setup(
    root: Path, steps: int = 6, *, fail: bool = False
) -> dict[str, object]:
    """Matching terminal snapshot for a terminal stop at the final step."""
    root.mkdir(parents=True, exist_ok=True)
    state_path, events_path = root / "state", root / "events"
    wf = workflow(steps)
    last = steps
    if fail:
        state = WorkflowExecutionState(
            "w",
            "failed",
            wf.steps[last - 1].id,
            last,
            wf.steps[last - 1].employee,
            tuple(step.id for step in wf.steps[: last - 1]),
            "api_error",
        )
    else:
        state = WorkflowExecutionState(
            "w",
            "succeeded",
            wf.steps[last - 1].id,
            last,
            wf.steps[last - 1].employee,
            tuple(step.id for step in wf.steps[:last]),
            None,
        )
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events = [predecessor_event(wf.steps[i].id, i + 1) for i in range(last - 1)]
    final_step = wf.steps[last - 1]
    if fail:
        events.append(
            RuntimeStepEvent(
                "step_failed",
                "w",
                final_step.id,
                last,
                final_step.employee,
                "running",
                "failed",
                "openai",
                "api_error",
                None,
                None,
                None,
                "safe failure",
            )
        )
    else:
        events.append(
            RuntimeStepEvent(
                "step_succeeded",
                "w",
                final_step.id,
                last,
                final_step.employee,
                "running",
                "succeeded",
                "openai",
                None,
                f"response-{final_step.id}",
                None,
                "output",
                None,
            )
        )
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


def running_snapshot_bytes(
    wf: WorkflowDefinition, completed: int
) -> tuple[bytes, bytes]:
    """Post-Phase177 running snapshot bytes: step ``completed + 1`` running."""
    index = completed + 1
    state = WorkflowExecutionState(
        "w",
        "running",
        wf.steps[index - 1].id,
        index,
        wf.steps[index - 1].employee,
        tuple(step.id for step in wf.steps[:completed]),
        None,
    )
    events = [predecessor_event(wf.steps[i].id, i + 1) for i in range(completed)]
    return (
        serialize_workflow_execution_state_json(state).encode("utf-8"),
        "".join(serialize_runtime_step_event_jsonl(event) for event in events).encode(
            "utf-8"
        ),
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


def loaded_events(base: Path) -> list[RuntimeStepEvent]:
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(base / "state", base / "events")
    )
    return list(loaded.events)


def recording_stub(
    return_value: object,
) -> tuple[object, list[tuple[tuple[object, ...], dict[str, object]]]]:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def stub(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return return_value

    return stub, calls


def assert_classification(fn: object, expected: str) -> None:
    with pytest.raises(Phase178Error) as exc:
        fn()  # type: ignore[operator]
    assert exc.value.detail.classification == expected


def execution_approval_for(values: dict[str, object]) -> object:
    start = constructed_start(values)
    return approve_model_invocation_execution(
        start.request,
        TOOLS,
        provider="openai",
        approved_by="test",
        approval_id="approval-id",
    )


def test_public_signature_default_dependencies_error_hierarchy_and_source_audit() -> None:
    signature = inspect.signature(phase178)
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
        "phase177_function",
        "phase172_function",
    ]
    assert [p.kind for p in parameters[:10]] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD
    ] * 10
    assert [p.kind for p in parameters[10:]] == [inspect.Parameter.KEYWORD_ONLY] * 2
    assert parameters[10].default is phase177
    assert parameters[11].default is phase172

    # Error hierarchy.
    assert issubclass(
        RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError,
        RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryError,
    )
    assert issubclass(
        RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryError,
        ValueError,
    )
    detail = RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError(
        "phase177_contract"
    ).detail
    assert (
        type(detail)
        is RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryFailureDetail
    )
    assert detail.classification == "phase177_contract"
    with pytest.raises(Exception):
        detail.classification = "phase172_contract"  # type: ignore[misc]

    # Exports are reachable from the engine package (the import above proves
    # it); the boundary name is the canonical production function.
    module = inspect.getmodule(phase178)
    assert module is not None
    assert (
        module.__name__
        == "ai_office.engine.runtime_result_to_persisted_running_execution_progression_orchestration_boundary"
    )
    source = inspect.getsource(module)
    tree = ast.parse(source)
    called = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "phase177_function" in called
    assert "phase172_function" in called
    # No direct lower route call: production may only call the injected
    # Phase177 and Phase172 dependencies.
    assert not [name for name in called if name.startswith("route_")]


def test_runtime_success_route_exact_stage_order_canonical_identity_once(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    transport = success_transport(calls)
    order: list[str] = []
    phase177_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    phase172_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    phase177_outputs: list[object] = []

    def stub_phase177(*args: object, **kwargs: object) -> object:
        order.append("177")
        phase177_calls.append((args, kwargs))
        value = phase177(*args, **kwargs)
        phase177_outputs.append(value)
        return value

    def stub_phase172(*args: object, **kwargs: object) -> object:
        order.append("172")
        phase172_calls.append((args, kwargs))
        return phase172(*args, **kwargs)

    out = phase178(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        api_key,
        execution_approval,
        transport,
        phase177_function=stub_phase177,  # type: ignore[arg-type]
        phase172_function=stub_phase172,  # type: ignore[arg-type]
    )
    assert order == ["177", "172"]
    assert len(phase177_calls) == 1
    args_177 = phase177_calls[0][0]
    assert args_177 == (
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        api_key,
        execution_approval,
        transport,
    )
    assert phase177_calls[0][1] == {}
    assert len(phase172_calls) == 1
    step7_result = phase177_outputs[0]
    assert type(step7_result) is StepRuntimeExecutionSuccess
    args_172 = phase172_calls[0][0]
    assert args_172 == (step7_result, wf, state_path, events_path)
    assert phase172_calls[0][1] == {}
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "prepare_next_step"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-7"
    assert out.current_step_index == 7
    assert out.current_employee_id == "e7"
    assert out.next_step_id == "step-8"
    assert out.next_step_index == 8
    assert out.next_employee_id == "e8"
    assert out.reason == "next_step_available"
    assert len(calls) == 1


def test_runtime_failure_route_exact_stage_order_canonical_identity_once(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    transport = failure_transport(calls)
    order: list[str] = []
    phase177_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    phase172_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    phase177_outputs: list[object] = []

    def stub_phase177(*args: object, **kwargs: object) -> object:
        order.append("177")
        phase177_calls.append((args, kwargs))
        value = phase177(*args, **kwargs)
        phase177_outputs.append(value)
        return value

    def stub_phase172(*args: object, **kwargs: object) -> object:
        order.append("172")
        phase172_calls.append((args, kwargs))
        return phase172(*args, **kwargs)

    out = phase178(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        api_key,
        execution_approval,
        transport,
        phase177_function=stub_phase177,  # type: ignore[arg-type]
        phase172_function=stub_phase172,  # type: ignore[arg-type]
    )
    assert order == ["177", "172"]
    assert len(phase177_calls) == 1
    args_177 = phase177_calls[0][0]
    assert args_177 == (
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        api_key,
        execution_approval,
        transport,
    )
    assert len(phase172_calls) == 1
    step7_result = phase177_outputs[0]
    assert type(step7_result) is StepRuntimeExecutionFailure
    args_172 = phase172_calls[0][0]
    assert args_172 == (step7_result, wf, state_path, events_path)
    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-7"
    assert out.current_step_index == 7
    assert out.current_employee_id == "e7"
    assert out.failure_category == "api_error"
    assert len(calls) == 1


def test_both_stop_routes_exact_identity_target_unchanged_phase172_zero(
    tmp_path: Path,
) -> None:
    # Subcase 1: exact workflow_complete stop route.
    values = terminal_setup(tmp_path / "s", steps=6, fail=False)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    committed_state = state_path.read_bytes()
    committed_events = events_path.read_bytes()
    stop = complete_decision(wf, 6)
    phase172_stub, phase172_calls = recording_stub(None)
    out = phase178(
        stop,
        wf,
        None,
        None,
        state_path,
        events_path,
        None,
        None,
        None,
        None,
        phase172_function=phase172_stub,  # type: ignore[arg-type]
    )
    assert out is stop
    assert state_path.read_bytes() == committed_state
    assert events_path.read_bytes() == committed_events
    assert phase172_calls == []

    # Subcase 2: exact persisted_failure stop route.
    values2 = terminal_setup(tmp_path / "f", steps=6, fail=True)
    wf2 = values2["workflow"]  # type: ignore[arg-type]
    state_path2 = values2["state_path"]  # type: ignore[arg-type]
    events_path2 = values2["events_path"]  # type: ignore[arg-type]
    committed_state2 = state_path2.read_bytes()
    committed_events2 = events_path2.read_bytes()
    stop2 = failure_outcome(wf2, 6)
    phase172_stub2, phase172_calls2 = recording_stub(None)
    out2 = phase178(
        stop2,
        wf2,
        None,
        None,
        state_path2,
        events_path2,
        None,
        None,
        None,
        None,
        phase172_function=phase172_stub2,  # type: ignore[arg-type]
    )
    assert out2 is stop2
    assert state_path2.read_bytes() == committed_state2
    assert events_path2.read_bytes() == committed_events2
    assert phase172_calls2 == []


def test_input_family_subclass_substitute_rejection_and_target_validation(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    transport = success_transport([])
    phase172_stub, phase172_calls = recording_stub(None)

    class SubDecision(WorkflowProgressionDecision):
        pass

    class SubWorkflow(WorkflowDefinition):
        pass

    attribute_compatible = type(
        "Fake",
        (),
        {
            "decision": "workflow_complete",
            "workflow_id": "w",
            "current_step_id": "step-6",
            "current_step_index": 6,
            "current_employee_id": "e6",
            "next_step_id": None,
            "next_step_index": None,
            "next_employee_id": None,
            "reason": "last_step_succeeded",
        },
    )()
    subclassed_decision = SubDecision(
        "workflow_complete",
        "w",
        "step-6",
        6,
        "e6",
        None,
        None,
        None,
        "last_step_succeeded",
    )
    for bad_result in (
        object(),
        attribute_compatible,
        subclassed_decision,
        prepare_decision(wf, 6),
        PersistedExecutionOutcome("persisted_success", "w", "step-6", 6, "e6", None),
    ):
        assert_classification(
            lambda: phase178(
                bad_result,  # type: ignore[arg-type]
                wf,
                approval,
                employee,
                state_path,
                events_path,
                TOOLS,
                api_key,
                execution_approval,
                transport,
                phase172_function=phase172_stub,  # type: ignore[arg-type]
            ),
            "result_type",
        )
        assert phase172_calls == []
    assert_classification(
        lambda: phase178(
            result,
            SubWorkflow.model_validate(wf.model_dump()),
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "workflow_definition",
    )
    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            str(state_path),
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "state_target",
    )
    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            str(events_path),
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "event_target",
    )
    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            state_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "target_conflict",
    )
    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            tmp_path / "missing-state",
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "state_target",
    )
    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            tmp_path / "missing-events",
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "event_target",
    )
    assert phase172_calls == []


def test_non_callable_dependency_configuration_rejects_before_execution(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    transport = success_transport([])
    for bad_177 in (None, object()):
        assert_classification(
            lambda: phase178(
                result,
                wf,
                approval,
                employee,
                state_path,
                events_path,
                TOOLS,
                api_key,
                execution_approval,
                transport,
                phase177_function=bad_177,  # type: ignore[arg-type]
            ),
            "configuration",
        )
    for bad_172 in (None, 42):
        assert_classification(
            lambda: phase178(
                result,
                wf,
                approval,
                employee,
                state_path,
                events_path,
                TOOLS,
                api_key,
                execution_approval,
                transport,
                phase172_function=bad_172,  # type: ignore[arg-type]
            ),
            "configuration",
        )


def test_phase177_recognized_safe_error_identity_phase172_zero(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    transport = success_transport([])
    state_before = state_path.read_bytes()
    events_before = events_path.read_bytes()
    phase172_stub, phase172_calls = recording_stub(None)
    safe_error = Phase176SafeError("phase176_contract")

    def raise_safe(*args: object, **kwargs: object) -> object:
        raise safe_error

    with pytest.raises(Phase176SafeError) as exc:
        phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=raise_safe,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        )
    assert exc.value is safe_error
    assert phase172_calls == []
    assert state_path.read_bytes() == state_before
    assert events_path.read_bytes() == events_before


def test_phase177_unexpected_exception_sanitized_phase172_zero(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    transport = success_transport([])
    state_before = state_path.read_bytes()
    events_before = events_path.read_bytes()
    phase172_stub, phase172_calls = recording_stub(None)

    def raise_unexpected(*args: object, **kwargs: object) -> object:
        raise RuntimeError("boom-177")

    with pytest.raises(Phase178Error) as exc:
        phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=raise_unexpected,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "dependency_error"
    assert "boom-177" not in str(exc.value)
    assert phase172_calls == []
    assert state_path.read_bytes() == state_before
    assert events_path.read_bytes() == events_before


def test_malformed_phase177_runtime_return_snapshot_history_contract(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path / "a", steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    transport = success_transport([])
    phase172_stub, phase172_calls = recording_stub(None)
    wf8 = workflow(8)

    # Subcase 1: malformed Phase177 return type.
    def bad_return(*args: object, **kwargs: object) -> object:
        return "not-a-runtime-result"

    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=bad_return,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "phase177_contract",
    )
    assert phase172_calls == []

    # Subcase 2: malformed running snapshot (state not running).
    def tampered_state(*args: object, **kwargs: object) -> object:
        state_bytes, event_bytes = running_snapshot_bytes(wf8, 6)
        Path(state_path).write_bytes(state_bytes)  # type: ignore[arg-type]
        Path(events_path).write_bytes(event_bytes)  # type: ignore[arg-type]
        loaded = load_workflow_execution_state(Path(state_path))  # type: ignore[arg-type]
        Path(state_path).write_bytes(  # type: ignore[arg-type]
            serialize_workflow_execution_state_json(
                WorkflowExecutionState(
                    loaded.workflow_id,
                    "succeeded",
                    loaded.current_step_id,
                    loaded.current_step_index,
                    loaded.current_employee_id,
                    loaded.completed_step_ids,
                    None,
                )
            ).encode("utf-8")
        )
        return runtime_success(wf8, 7)

    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=tampered_state,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "phase177_contract",
    )
    assert phase172_calls == []

    # Subcase 3: malformed running history (wrong event count).
    def tampered_history(*args: object, **kwargs: object) -> object:
        state_bytes, event_bytes = running_snapshot_bytes(wf8, 6)
        Path(state_path).write_bytes(state_bytes)  # type: ignore[arg-type]
        truncated = event_bytes[: len(event_bytes) // 2]
        Path(events_path).write_bytes(truncated)  # type: ignore[arg-type]
        return runtime_success(wf8, 7)

    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=tampered_history,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "phase177_contract",
    )
    assert phase172_calls == []

    # Subcase 4: running snapshot with wrong workflow id.
    def wrong_workflow_id(*args: object, **kwargs: object) -> object:
        state_bytes, event_bytes = running_snapshot_bytes(wf8, 6)
        loaded = load_workflow_execution_state(
            Path(state_path)  # type: ignore[arg-type]
        )
        Path(state_path).write_bytes(  # type: ignore[arg-type]
            serialize_workflow_execution_state_json(
                replace(loaded, workflow_id="other")
            ).encode("utf-8")
        )
        Path(events_path).write_bytes(event_bytes)  # type: ignore[arg-type]
        return runtime_success(wf8, 7)

    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=wrong_workflow_id,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "phase177_contract",
    )
    assert phase172_calls == []

    # Subcase 5: running snapshot with wrong completed step-id prefix.
    def wrong_completed_prefix(*args: object, **kwargs: object) -> object:
        state_bytes, event_bytes = running_snapshot_bytes(wf8, 6)
        loaded = load_workflow_execution_state(
            Path(state_path)  # type: ignore[arg-type]
        )
        Path(state_path).write_bytes(  # type: ignore[arg-type]
            serialize_workflow_execution_state_json(
                replace(loaded, completed_step_ids=("s1",))
            ).encode("utf-8")
        )
        Path(events_path).write_bytes(event_bytes)  # type: ignore[arg-type]
        return runtime_success(wf8, 7)

    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=wrong_completed_prefix,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "phase177_contract",
    )
    assert phase172_calls == []

    # Subcase 6: running snapshot carries a non-None failure category.
    def running_with_failure_category(*args: object, **kwargs: object) -> object:
        state_bytes, event_bytes = running_snapshot_bytes(wf8, 6)
        loaded = load_workflow_execution_state(
            Path(state_path)  # type: ignore[arg-type]
        )
        Path(state_path).write_bytes(  # type: ignore[arg-type]
            serialize_workflow_execution_state_json(
                replace(loaded, last_failure_category="api_error")
            ).encode("utf-8")
        )
        Path(events_path).write_bytes(event_bytes)  # type: ignore[arg-type]
        return runtime_success(wf8, 7)

    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=running_with_failure_category,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "phase177_contract",
    )
    assert phase172_calls == []

    # Subcase 7: correct count and ids, but a predecessor is not a
    # succeeded terminal event.
    def non_succeeded_predecessor(*args: object, **kwargs: object) -> object:
        state_bytes, event_bytes = running_snapshot_bytes(wf8, 6)
        Path(state_path).write_bytes(state_bytes)  # type: ignore[arg-type]
        events = [
            RuntimeStepEvent(**json.loads(line))
            for line in event_bytes.decode("utf-8").splitlines()
        ]
        events[-1] = replace(
            events[-1],
            previous_status="running",
            next_status="failed",
            event_type="step_failed",
            failure_category="api_error",
        )
        Path(events_path).write_bytes(  # type: ignore[arg-type]
            "".join(
                serialize_runtime_step_event_jsonl(event) for event in events
            ).encode("utf-8")
        )
        return runtime_success(wf8, 7)

    assert_classification(
        lambda: phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=non_succeeded_predecessor,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "phase177_contract",
    )
    assert phase172_calls == []


def test_malformed_phase177_stop_identity_target_contract(tmp_path: Path) -> None:
    # Subcase 1: Phase177 returns a different stop object (identity mismatch).
    values = terminal_setup(tmp_path / "s", steps=6, fail=False)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    stop = complete_decision(wf, 6)
    other = complete_decision(wf, 6)
    phase172_stub, phase172_calls = recording_stub(None)

    def wrong_stop(*args: object, **kwargs: object) -> object:
        return other

    assert_classification(
        lambda: phase178(
            stop,
            wf,
            None,
            None,
            state_path,
            events_path,
            None,
            None,
            None,
            None,
            phase177_function=wrong_stop,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "phase177_contract",
    )
    assert phase172_calls == []

    # Subcase 2: Phase177 returns the stop but targets were mutated.
    def mutated_targets(*args: object, **kwargs: object) -> object:
        Path(events_path).write_bytes(b"tampered")  # type: ignore[arg-type]
        return stop

    assert_classification(
        lambda: phase178(
            stop,
            wf,
            None,
            None,
            state_path,
            events_path,
            None,
            None,
            None,
            None,
            phase177_function=mutated_targets,  # type: ignore[arg-type]
            phase172_function=phase172_stub,  # type: ignore[arg-type]
        ),
        "phase177_contract",
    )
    assert phase172_calls == []
    assert events_path.read_bytes() == b"tampered"


def test_phase172_recognized_safe_error_identity_after_phase177(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    transport = success_transport(calls)
    safe_error = Phase161SafeError("phase161_contract")

    def raise_161(*args: object, **kwargs: object) -> object:
        raise safe_error

    with pytest.raises(Phase161SafeError) as exc:
        phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=phase177,
            phase172_function=raise_161,  # type: ignore[arg-type]
        )
    assert exc.value is safe_error
    # No outer rollback: the post-Phase177 running snapshot remains.
    assert load_workflow_execution_state(state_path).status == "running"
    assert len(calls) == 1


def test_phase172_unexpected_exception_sanitized_after_phase177(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    transport = success_transport(calls)

    def raise_unexpected(*args: object, **kwargs: object) -> object:
        raise RuntimeError("boom-172")

    with pytest.raises(Phase178Error) as exc:
        phase178(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            TOOLS,
            api_key,
            execution_approval,
            transport,
            phase177_function=phase177,
            phase172_function=raise_unexpected,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "dependency_error"
    assert "boom-172" not in str(exc.value)
    assert load_workflow_execution_state(state_path).status == "running"
    assert len(calls) == 1


def test_malformed_phase172_return_or_final_target_contract(tmp_path: Path) -> None:
    # Subcase 1: malformed Phase172 return type.
    values1 = canonical_running_setup(tmp_path / "s1", steps=8, current=6)
    wf1 = values1["workflow"]  # type: ignore[arg-type]
    state_path1 = values1["state_path"]  # type: ignore[arg-type]
    events_path1 = values1["events_path"]  # type: ignore[arg-type]
    result1 = runtime_success(wf1, 6, request_id_none=True)
    decision1 = prepare_decision(wf1, 6)
    approval1 = approval_for(decision1)
    employee1 = employee_for(decision1)
    api_key1 = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval1 = execution_approval_for(values1)
    calls1: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    transport1 = success_transport(calls1)

    def bad_return(*args: object, **kwargs: object) -> object:
        return "not-a-decision"

    assert_classification(
        lambda: phase178(
            result1,
            wf1,
            approval1,
            employee1,
            state_path1,
            events_path1,
            TOOLS,
            api_key1,
            execution_approval1,
            transport1,
            phase177_function=phase177,
            phase172_function=bad_return,  # type: ignore[arg-type]
        ),
        "phase172_contract",
    )
    # No outer rollback: the post-Phase177 running snapshot remains.
    assert load_workflow_execution_state(state_path1).status == "running"
    assert len(calls1) == 1

    # Subcase 2: Phase172 returns a valid decision but writes no durable
    # terminal transition (final durable target malformed).
    values2 = canonical_running_setup(tmp_path / "s2", steps=8, current=6)
    wf2 = values2["workflow"]  # type: ignore[arg-type]
    state_path2 = values2["state_path"]  # type: ignore[arg-type]
    events_path2 = values2["events_path"]  # type: ignore[arg-type]
    result2 = runtime_success(wf2, 6, request_id_none=True)
    decision2 = prepare_decision(wf2, 6)
    approval2 = approval_for(decision2)
    employee2 = employee_for(decision2)
    api_key2 = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval2 = execution_approval_for(values2)
    calls2: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    transport2 = success_transport(calls2)

    def no_write(*args: object, **kwargs: object) -> object:
        return prepare_decision(wf2, 7)

    assert_classification(
        lambda: phase178(
            result2,
            wf2,
            approval2,
            employee2,
            state_path2,
            events_path2,
            TOOLS,
            api_key2,
            execution_approval2,
            transport2,
            phase177_function=phase177,
            phase172_function=no_write,  # type: ignore[arg-type]
        ),
        "phase172_contract",
    )
    assert load_workflow_execution_state(state_path2).status == "running"

    # Subcase 3: Phase172 mutates the terminal history after a valid write.
    values3 = canonical_running_setup(tmp_path / "s3", steps=8, current=6)
    wf3 = values3["workflow"]  # type: ignore[arg-type]
    state_path3 = values3["state_path"]  # type: ignore[arg-type]
    events_path3 = values3["events_path"]  # type: ignore[arg-type]
    result3 = runtime_success(wf3, 6, request_id_none=True)
    decision3 = prepare_decision(wf3, 6)
    approval3 = approval_for(decision3)
    employee3 = employee_for(decision3)
    api_key3 = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval3 = execution_approval_for(values3)
    calls3: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    transport3 = success_transport(calls3)

    def corrupt_history(*args: object, **kwargs: object) -> object:
        out = phase172(*args, **kwargs)
        with Path(events_path3).open("a", encoding="utf-8") as f:  # type: ignore[arg-type]
            f.write("garbage\n")
        return out

    assert_classification(
        lambda: phase178(
            result3,
            wf3,
            approval3,
            employee3,
            state_path3,
            events_path3,
            TOOLS,
            api_key3,
            execution_approval3,
            transport3,
            phase177_function=phase177,
            phase172_function=corrupt_history,  # type: ignore[arg-type]
        ),
        "phase172_contract",
    )
    assert events_path3.read_bytes().endswith(b"garbage\n")
    assert load_workflow_execution_state(state_path3).status == "succeeded"
    assert len(calls3) == 1


def test_success_non_final_exact_durable_target_prepare_next_step(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    transport = success_transport(calls)
    out = phase178(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        api_key,
        execution_approval,
        transport,
    )
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "prepare_next_step"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-7"
    assert out.current_step_index == 7
    assert out.current_employee_id == "e7"
    assert out.next_step_id == "step-8"
    assert out.next_step_index == 8
    assert out.next_employee_id == "e8"
    assert out.reason == "next_step_available"
    # Exact final durable target.
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "succeeded"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    assert final_state.current_employee_id == "e7"
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 8))
    assert final_state.last_failure_category is None
    final_events = loaded_events(tmp_path)
    assert [event.step_id for event in final_events] == [
        f"step-{i}" for i in range(1, 8)
    ]
    terminal = final_events[-1]
    assert terminal.event_type == "step_succeeded"
    assert terminal.workflow_id == "w"
    assert terminal.step_id == "step-7"
    assert terminal.step_index == 7
    assert terminal.employee_id == "e7"
    assert terminal.previous_status == "running"
    assert terminal.next_status == "succeeded"
    assert terminal.provider == "openai"
    assert terminal.request_id == "request_123"
    assert terminal.response_id == "resp_123"
    assert terminal.output_text == "ok"
    assert terminal.failure_category is None
    assert terminal.message is None
    # Exactly one terminal event appended to the post-Phase177 history.
    assert len(final_events) == 7
    assert len(calls) == 1


def test_success_final_exact_durable_target_workflow_complete(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    transport = success_transport(calls)
    out = phase178(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        api_key,
        execution_approval,
        transport,
    )
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "workflow_complete"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-7"
    assert out.current_step_index == 7
    assert out.current_employee_id == "e7"
    assert out.next_step_id is None
    assert out.next_step_index is None
    assert out.next_employee_id is None
    assert out.reason == "last_step_succeeded"
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "succeeded"
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 8))
    assert final_state.last_failure_category is None
    final_events = loaded_events(tmp_path)
    assert [event.step_id for event in final_events] == [
        f"step-{i}" for i in range(1, 8)
    ]
    terminal = final_events[-1]
    assert terminal.event_type == "step_succeeded"
    assert terminal.next_status == "succeeded"
    assert terminal.request_id == "request_123"
    assert terminal.response_id == "resp_123"
    assert terminal.output_text == "ok"
    assert terminal.message is None
    assert len(final_events) == 7
    assert len(calls) == 1


def test_failure_exact_durable_target_persisted_failure_linkage(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    transport = failure_transport(calls)
    out = phase178(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        api_key,
        execution_approval,
        transport,
    )
    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-7"
    assert out.current_step_index == 7
    assert out.current_employee_id == "e7"
    assert out.failure_category == "api_error"
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "failed"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    assert final_state.current_employee_id == "e7"
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    assert final_state.last_failure_category == "api_error"
    final_events = loaded_events(tmp_path)
    assert [event.step_id for event in final_events] == [
        f"step-{i}" for i in range(1, 8)
    ]
    terminal = final_events[-1]
    assert terminal.event_type == "step_failed"
    assert terminal.workflow_id == "w"
    assert terminal.step_id == "step-7"
    assert terminal.step_index == 7
    assert terminal.employee_id == "e7"
    assert terminal.previous_status == "running"
    assert terminal.next_status == "failed"
    assert terminal.provider == "openai"
    assert terminal.request_id == "request_123"
    assert terminal.failure_category == "api_error"
    assert terminal.response_id is None
    assert terminal.output_text is None
    assert terminal.message == "safe failure"
    # No retry / further execution: exactly one transport call.
    assert len(calls) == 1


def test_real_a_eight_step_step7_success_prepares_step8(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path / "a", steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    out = phase178(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        api_key,
        execution_approval,
        success_transport(calls),
    )
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "prepare_next_step"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-7"
    assert out.current_step_index == 7
    assert out.current_employee_id == "e7"
    assert out.next_step_id == "step-8"
    assert out.next_step_index == 8
    assert out.next_employee_id == "e8"
    assert out.reason == "next_step_available"
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "succeeded"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 8))
    final_events = loaded_events(tmp_path / "a")
    assert [event.step_id for event in final_events] == [
        f"step-{i}" for i in range(1, 8)
    ]
    assert final_events[-1].event_type == "step_succeeded"
    assert final_events[-1].provider == "openai"
    assert final_events[-1].request_id == "request_123"
    # Accumulated step-5 / step-6 None provenance preserved byte-exact.
    assert final_events[4].request_id is None
    assert final_events[5].request_id is None
    # No step-8 preparation/start/execution happened.
    assert len(final_events) == 7
    assert len(calls) == 1


def test_real_b_seven_step_step7_success_workflow_complete(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path / "b", steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    out = phase178(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        api_key,
        execution_approval,
        success_transport(calls),
    )
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "workflow_complete"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-7"
    assert out.current_step_index == 7
    assert out.current_employee_id == "e7"
    assert out.next_step_id is None
    assert out.next_step_index is None
    assert out.next_employee_id is None
    assert out.reason == "last_step_succeeded"
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "succeeded"
    assert final_state.current_step_index == 7
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 8))
    final_events = loaded_events(tmp_path / "b")
    assert [event.step_id for event in final_events] == [
        f"step-{i}" for i in range(1, 8)
    ]
    assert final_events[-1].event_type == "step_succeeded"
    assert final_events[4].request_id is None
    assert final_events[5].request_id is None
    assert len(calls) == 1


def test_real_c_eight_step_step7_failure_persisted_failure(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path / "c", steps=8, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    out = phase178(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        TOOLS,
        api_key,
        execution_approval,
        failure_transport(calls),
    )
    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-7"
    assert out.current_step_index == 7
    assert out.current_employee_id == "e7"
    assert out.failure_category == "api_error"
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "failed"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    assert final_state.last_failure_category == "api_error"
    final_events = loaded_events(tmp_path / "c")
    assert [event.step_id for event in final_events] == [
        f"step-{i}" for i in range(1, 8)
    ]
    assert final_events[-1].event_type == "step_failed"
    assert final_events[-1].failure_category == "api_error"
    assert final_events[4].request_id is None
    assert final_events[5].request_id is None
    # No retry / further execution: exactly one transport call.
    assert len(calls) == 1


def test_real_d_stop_routes_exact_identity_bytes_preserved_phase172_zero(
    tmp_path: Path,
) -> None:
    # Subcase 1: exact workflow_complete stop through the real Phase177 stop
    # path, Phase172 zero, bytes preserved.
    values = terminal_setup(tmp_path / "d1", steps=6, fail=False)
    wf = values["workflow"]  # type: ignore[arg-type]
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    committed_state = state_path.read_bytes()
    committed_events = events_path.read_bytes()
    stop = complete_decision(wf, 6)
    phase172_stub, phase172_calls = recording_stub(None)
    out = phase178(
        stop,
        wf,
        None,
        None,
        state_path,
        events_path,
        None,
        None,
        None,
        None,
        phase172_function=phase172_stub,  # type: ignore[arg-type]
    )
    assert out is stop
    assert state_path.read_bytes() == committed_state
    assert events_path.read_bytes() == committed_events
    assert phase172_calls == []

    # Subcase 2: exact persisted_failure stop through the real Phase177 stop
    # path, Phase172 zero, bytes preserved.
    values2 = terminal_setup(tmp_path / "d2", steps=6, fail=True)
    wf2 = values2["workflow"]  # type: ignore[arg-type]
    state_path2 = values2["state_path"]  # type: ignore[arg-type]
    events_path2 = values2["events_path"]  # type: ignore[arg-type]
    committed_state2 = state_path2.read_bytes()
    committed_events2 = events_path2.read_bytes()
    stop2 = failure_outcome(wf2, 6)
    phase172_stub2, phase172_calls2 = recording_stub(None)
    out2 = phase178(
        stop2,
        wf2,
        None,
        None,
        state_path2,
        events_path2,
        None,
        None,
        None,
        None,
        phase172_function=phase172_stub2,  # type: ignore[arg-type]
    )
    assert out2 is stop2
    assert state_path2.read_bytes() == committed_state2
    assert events_path2.read_bytes() == committed_events2
    assert phase172_calls2 == []
