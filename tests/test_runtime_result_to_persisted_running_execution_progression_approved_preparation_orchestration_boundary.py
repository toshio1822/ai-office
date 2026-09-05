"""Issue #388 Phase 179 focused tests.

Pin the public Phase 178 -> Phase 145 (prepare route only) orchestration:
exact call shapes, separate first/second approval-employee pairs, stop
zero-call semantics, committed-snapshot compensation, and the no-readvance
invariant.  Exactly 20 collected tests; inline subcases keep the count fixed.
Synthetic transport only; no provider/network/paid API.
"""

# ruff: noqa: E501,I001

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
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase179Error,
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary,
    route_runtime_result_to_persisted_running_execution_progression_orchestration_boundary,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase145CompatibilityError,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as Phase137CompatibilityError,
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationError as Phase137Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionOrchestrationBoundaryCompatibilityError as Phase178CompatibilityError,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    ModelInvocationSuccess,
    UpstreamStepOutput,
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
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition

phase179 = (
    route_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary
)
phase178 = (
    route_runtime_result_to_persisted_running_execution_progression_orchestration_boundary
)

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


def running_setup(
    root: Path, steps: int = 8, current: int = 6
) -> dict[str, object]:
    """Running (non-terminal) snapshot at ``current`` with events 1..current-1."""
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
    state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
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
    state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
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


def constructed_start(values: dict[str, object]) -> PreparedStepExecutionStart:
    wf = values["workflow"]  # type: ignore[assignment]
    decision = prepare_decision(wf, 6)
    employee = employee_for(decision)
    step = wf.steps[decision.next_step_index - 1]
    prepared = PreparedWorkflowStep(
        wf.id,
        step.id,
        decision.next_step_index,
        employee.id,
        employee.instructions,
        step.instructions,
        employee.model,
        tuple(employee.allowed_tools),
    )
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            prepared.model,
            prepared.employee_instructions,
            prepared.step_instructions,
            prepared.allowed_tool_names,
            (
                UpstreamStepOutput(
                    wf.id,
                    wf.steps[prepared.step_index - 2].id,
                    prepared.step_index - 1,
                    wf.steps[prepared.step_index - 2].employee,
                    "output",
                ),
            ),
        ),
        WorkflowExecutionState(
            prepared.workflow_id,
            "running",
            prepared.step_id,
            prepared.step_index,
            prepared.employee_id,
            tuple(s.id for s in wf.steps[: prepared.step_index - 1]),
            None,
        ),
    )


def execution_approval_for(values: dict[str, object]) -> object:
    start = constructed_start(values)
    return approve_model_invocation_execution(
        start.request,
        TOOLS,
        provider="openai",
        approved_by="test",
        approval_id="approval-id",
    )


def recording_stub(
    return_value: object,
) -> tuple[object, list[tuple[tuple[object, ...], dict[str, object]]]]:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def stub(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return return_value

    return stub, calls


def assert_classification(fn: object, expected: str) -> None:
    with pytest.raises(Phase179Error) as exc:
        fn()  # type: ignore[operator]
    assert exc.value.detail.classification == expected


def _scenario(
    tmp_path: Path, sub: str, steps: int = 8, current: int = 6
) -> dict[str, object]:
    values = running_setup(tmp_path / sub, steps=steps, current=current)
    wf = values["workflow"]  # type: ignore[assignment]
    result = runtime_success(wf, current, request_id_none=True)
    decision = prepare_decision(wf, current)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    next_decision = prepare_decision(wf, current + 1)
    return {
        "result": result,
        "workflow": wf,
        "approval": approval_for(decision),
        "employee": employee_for(decision),
        "next_approval": approval_for(next_decision),
        "next_employee": employee_for(next_decision),
        "state_path": values["state_path"],
        "events_path": values["events_path"],
        "state_before": values["state_before"],
        "events_before": values["events_before"],
        "api_key": api_key,
        "execution_approval": execution_approval_for(values),
        "values": values,
    }


def _committed_ref(tmp_path: Path, sub: str) -> dict[str, object]:
    """Real Phase178 commits step-7; returns the committed snapshot reference."""
    s = _scenario(tmp_path, sub)
    wf = s["workflow"]
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    decision = phase178(
        s["result"],
        wf,
        s["approval"],
        s["employee"],
        s["state_path"],
        s["events_path"],
        TOOLS,
        s["api_key"],
        s["execution_approval"],
        success_transport(calls),
    )
    assert type(decision) is WorkflowProgressionDecision
    assert decision.decision == "prepare_next_step" and decision.next_step_index == 8
    s["decision"] = decision
    s["next_approval"] = approval_for(decision)
    s["next_employee"] = employee_for(decision)
    s["committed"] = (
        Path(s["state_path"]).read_bytes(),
        Path(s["events_path"]).read_bytes(),
    )
    s["calls"] = calls
    return s


TEST_02_ARGS = None  # placeholder (unused)


def test_01_public_export_default_dependencies_and_exact_signature(
    tmp_path: Path,
) -> None:
    sig = inspect.signature(phase179)
    params = list(sig.parameters.items())
    pos_or_kw = [
        name
        for name, p in params
        if p.kind is p.POSITIONAL_OR_KEYWORD and p.default is inspect.Parameter.empty
    ]
    assert pos_or_kw == [
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
        "next_preparation_approval",
        "next_employee",
    ]
    kwonly = {
        name: p.default
        for name, p in params
        if p.kind is p.KEYWORD_ONLY
    }
    assert set(kwonly) == {"phase178_function", "phase145_function"}
    assert kwonly["phase178_function"] is phase178
    import ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as p145

    assert kwonly["phase145_function"] is (
        p145.route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    )
    assert Phase179Error is not None
    assert RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError is not None
    assert RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryFailureDetail is not None

    # --- inline matrix: subclasses / substitute models are rejected as
    # result_type before Phase 178 runs ---
    sp, ep = Path(tmp_path / "state"), Path(tmp_path / "events")
    wf = workflow(8)
    ok = runtime_success(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))

    class SubSuccess(StepRuntimeExecutionSuccess):
        pass

    sub_result = SubSuccess(
        ok.workflow_id, ok.step_id, ok.step_index, ok.employee_id, ok.invocation_result
    )

    def call(result_value: object = ok) -> object:
        return phase179(
            result_value,
            wf,
            approval,
            employee,
            sp,
            ep,
            TOOLS,
            api_key,
            object(),
            None,
            approval,
            employee,
        )

    # exact subclass of the runtime success type is a substitute -> result_type
    assert_classification(lambda: call(sub_result), "result_type")
    # unrelated / substituted models -> result_type
    assert_classification(lambda: call("NOT-A-RESULT"), "result_type")
    assert_classification(lambda: call(object()), "result_type")
    # a prepare_next_step decision as an original input is not a stop input
    assert_classification(lambda: call(prepare_decision(wf, 6)), "result_type")
    # a persisted_success outcome as an original input is not a stop input
    assert_classification(
        lambda: call(PersistedExecutionOutcome("persisted_success", "w", "step-6", 6, "e6", None)),
        "result_type",
    )

    # --- inline matrix: invalid workflow / target / configuration inputs ---
    assert_classification(
        lambda: phase179(ok, "NOT-A-WORKFLOW", approval, employee, sp, ep, TOOLS, api_key, object(), None, approval, employee),
        "workflow_definition",
    )
    assert_classification(
        lambda: phase179(ok, wf, approval, employee, "NOT-A-PATH", ep, TOOLS, api_key, object(), None, approval, employee),
        "state_target",
    )
    assert_classification(
        lambda: phase179(ok, wf, approval, employee, sp, "NOT-A-PATH", TOOLS, api_key, object(), None, approval, employee),
        "event_target",
    )
    assert_classification(
        lambda: phase179(ok, wf, approval, employee, sp, sp, TOOLS, api_key, object(), None, approval, employee),
        "target_conflict",
    )
    assert_classification(
        lambda: phase179(ok, wf, approval, employee, sp, ep, TOOLS, api_key, object(), None, approval, employee, phase178_function="NOT-CALLABLE"),  # type: ignore[arg-type]
        "configuration",
    )
    assert_classification(
        lambda: phase179(ok, wf, approval, employee, sp, ep, TOOLS, api_key, object(), None, approval, employee, phase145_function="NOT-CALLABLE"),  # type: ignore[arg-type]
        "configuration",
    )


def test_02_prepare_route_calls_phase178_once_ten_positional_no_kwargs(
    tmp_path: Path,
) -> None:
    s = _committed_ref(tmp_path, "a")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    p178_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    p145_calls: list[int] = []
    prepare = s["decision"]
    prepared = prepared_for(wf, prepare, s["next_employee"])

    def phase178_stub(*args: object, **kwargs: object) -> object:
        if kwargs:
            raise AssertionError("phase178 must receive no kwargs")
        p178_calls.append((args, kwargs))
        return prepare

    def phase145_stub(*args: object, **kwargs: object) -> object:
        p145_calls.append(1)
        return prepared

    out = phase179(
        s["result"],
        wf,
        s["approval"],
        s["employee"],
        sp,
        ep,
        TOOLS,
        s["api_key"],
        s["execution_approval"],
        None,
        s["next_approval"],
        s["next_employee"],
        phase178_function=phase178_stub,  # type: ignore[arg-type]
        phase145_function=phase145_stub,  # type: ignore[arg-type]
    )
    assert out is prepared
    assert len(p178_calls) == 1
    args = p178_calls[0][0]
    assert len(args) == 10
    assert args[0] is s["result"]
    assert args[1] is wf
    assert args[2] is s["approval"]
    assert args[3] is s["employee"]
    assert args[4] is sp
    assert args[5] is ep
    assert args[6] is TOOLS
    assert args[7] is s["api_key"]
    assert args[8] is s["execution_approval"]


def test_03_prepare_route_calls_phase145_once_six_positional_next_pair_no_kwargs(
    tmp_path: Path,
) -> None:
    s = _committed_ref(tmp_path, "a")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    prepare = s["decision"]
    prepared = prepared_for(wf, prepare, s["next_employee"])
    p145_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def phase178_stub(*args: object, **kwargs: object) -> object:
        return prepare

    def phase145_stub(*args: object, **kwargs: object) -> object:
        if kwargs:
            raise AssertionError("phase145 must receive no kwargs")
        p145_calls.append((args, kwargs))
        return prepared

    out = phase179(
        s["result"],
        wf,
        s["approval"],
        s["employee"],
        sp,
        ep,
        TOOLS,
        s["api_key"],
        s["execution_approval"],
        None,
        s["next_approval"],
        s["next_employee"],
        phase178_function=phase178_stub,  # type: ignore[arg-type]
        phase145_function=phase145_stub,  # type: ignore[arg-type]
    )
    assert out is prepared
    assert len(p145_calls) == 1
    args = p145_calls[0][0]
    assert len(args) == 6
    assert args[0] is prepare
    assert args[1] is wf
    assert args[2] is s["next_approval"]
    assert args[3] is s["next_employee"]
    assert args[4] is sp
    assert args[5] is ep


def test_04_first_and_second_approval_employee_not_interchangeable(
    tmp_path: Path,
) -> None:
    s = _committed_ref(tmp_path, "a")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    prepare = s["decision"]
    prepared = prepared_for(wf, prepare, s["next_employee"])
    seen: dict[str, object] = {}

    def phase178_stub(*args: object, **kwargs: object) -> object:
        seen["first_approval"] = args[2]
        seen["first_employee"] = args[3]
        return prepare

    def phase145_stub(*args: object, **kwargs: object) -> object:
        seen["second_approval"] = args[2]
        seen["second_employee"] = args[3]
        return prepared

    phase179(
        s["result"],
        wf,
        s["approval"],
        s["employee"],
        sp,
        ep,
        TOOLS,
        s["api_key"],
        s["execution_approval"],
        None,
        s["next_approval"],
        s["next_employee"],
        phase178_function=phase178_stub,  # type: ignore[arg-type]
        phase145_function=phase145_stub,  # type: ignore[arg-type]
    )
    assert seen["first_approval"] is s["approval"]
    assert seen["first_employee"] is s["employee"]
    assert seen["second_approval"] is s["next_approval"]
    assert seen["second_employee"] is s["next_employee"]
    assert seen["first_approval"] is not seen["second_approval"]
    assert seen["first_employee"] is not seen["second_employee"]


def test_05_second_pair_not_prevalidated_before_phase178(tmp_path: Path) -> None:
    s = _committed_ref(tmp_path, "a")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    prepare = s["decision"]
    p178_calls: list[int] = []
    p145_calls: list[tuple[object, ...]] = []

    def phase178_stub(*args: object, **kwargs: object) -> object:
        p178_calls.append(1)
        return prepare

    def phase145_stub(*args: object, **kwargs: object) -> object:
        p145_calls.append(args)
        raise Phase145CompatibilityError("employee_contract")

    with pytest.raises(Phase145Error) as exc:
        phase179(
            s["result"],
            wf,
            s["approval"],
            s["employee"],
            sp,
            ep,
            TOOLS,
            s["api_key"],
            s["execution_approval"],
            None,
            "NOT-AN-APPROVAL",
            "NOT-AN-EMPLOYEE",
            phase178_function=phase178_stub,  # type: ignore[arg-type]
            phase145_function=phase145_stub,  # type: ignore[arg-type]
        )
    # Phase178 ran exactly once even though the later pair is type-invalid
    # (no prevalidation of the second pair before Phase178).
    assert len(p178_calls) == 1
    # The invalid pair is passed through untouched to Phase145 (no sanitise).
    assert len(p145_calls) == 1
    assert p145_calls[0][2] == "NOT-AN-APPROVAL"
    assert p145_calls[0][3] == "NOT-AN-EMPLOYEE"
    # Phase145 is authoritative: exact identity re-raise, not sanitised.
    assert exc.value.detail.classification == "employee_contract"


def test_06_original_workflow_complete_stop_exact_identity_unchanged_zero145(
    tmp_path: Path,
) -> None:
    base = tmp_path / "a"
    values = terminal_setup(base, steps=6)
    wf = values["workflow"]  # type: ignore[assignment]
    sp, ep = values["state_path"], values["events_path"]
    sb, eb = sp.read_bytes(), ep.read_bytes()
    stop = complete_decision(wf, 6)
    p145_calls: list[int] = []

    def phase145_stub(*args: object, **kwargs: object) -> object:
        p145_calls.append(1)
        return None

    out = phase179(
        stop,
        wf,
        None,
        None,
        sp,
        ep,
        None,
        None,
        None,
        None,
        None,
        None,
        phase145_function=phase145_stub,  # type: ignore[arg-type]
    )
    assert out is stop
    assert sp.read_bytes() == sb and ep.read_bytes() == eb
    assert p145_calls == []


def test_07_original_persisted_failure_stop_exact_identity_unchanged_zero145(
    tmp_path: Path,
) -> None:
    base = tmp_path / "a"
    values = terminal_setup(base, steps=6, fail=True)
    wf = values["workflow"]  # type: ignore[assignment]
    sp, ep = values["state_path"], values["events_path"]
    sb, eb = sp.read_bytes(), ep.read_bytes()
    stop = failure_outcome(wf, 6)
    p145_calls: list[int] = []

    def phase145_stub(*args: object, **kwargs: object) -> object:
        p145_calls.append(1)
        return None

    out = phase179(
        stop,
        wf,
        None,
        None,
        sp,
        ep,
        None,
        None,
        None,
        None,
        None,
        None,
        phase145_function=phase145_stub,  # type: ignore[arg-type]
    )
    assert out is stop
    assert sp.read_bytes() == sb and ep.read_bytes() == eb
    assert p145_calls == []


def test_08_runtime_route_phase178_workflow_complete_stop_return_zero145(
    tmp_path: Path,
) -> None:
    base = tmp_path / "a"
    values = running_setup(base, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[assignment]
    sp, ep = values["state_path"], values["events_path"]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    p145_calls: list[int] = []

    def phase145_stub(*args: object, **kwargs: object) -> object:
        p145_calls.append(1)
        return None

    out = phase179(
        result,
        wf,
        approval_for(decision),
        employee_for(decision),
        sp,
        ep,
        TOOLS,
        api_key,
        execution_approval,
        success_transport(calls),
        None,
        None,
        phase145_function=phase145_stub,  # type: ignore[arg-type]
    )
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "workflow_complete"
    assert out.current_step_id == "step-7" and out.current_step_index == 7
    assert out.reason == "last_step_succeeded"
    assert p145_calls == []


def test_09_runtime_route_phase178_persisted_failure_stop_return_zero145(
    tmp_path: Path,
) -> None:
    base = tmp_path / "a"
    values = running_setup(base, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[assignment]
    sp, ep = values["state_path"], values["events_path"]
    result = runtime_success(wf, 6, request_id_none=True)
    decision = prepare_decision(wf, 6)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = execution_approval_for(values)
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    p145_calls: list[int] = []

    def phase145_stub(*args: object, **kwargs: object) -> object:
        p145_calls.append(1)
        return None

    out = phase179(
        result,
        wf,
        approval_for(decision),
        employee_for(decision),
        sp,
        ep,
        TOOLS,
        api_key,
        execution_approval,
        failure_transport(calls),
        None,
        None,
        phase145_function=phase145_stub,  # type: ignore[arg-type]
    )
    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"
    assert out.current_step_id == "step-7" and out.current_step_index == 7
    assert out.failure_category == "api_error"
    assert p145_calls == []


def test_10_real_default_accumulated_none_reaches_prepared_step8(
    tmp_path: Path,
) -> None:
    s = _scenario(tmp_path, "a")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    out = phase179(
        s["result"],
        wf,
        s["approval"],
        s["employee"],
        sp,
        ep,
        TOOLS,
        s["api_key"],
        s["execution_approval"],
        success_transport(calls),
        s["next_approval"],
        s["next_employee"],
    )
    assert type(out) is PreparedWorkflowStep
    assert out.step_id == "step-8" and out.step_index == 8
    assert out.employee_id == "e8"
    final_events = loaded_events(tmp_path / "a")
    assert final_events[4].request_id is None
    assert final_events[5].request_id is None
    assert len(final_events) == 7
    assert len(calls) == 1


def test_11_non_contiguous_accumulated_provenance_real_default_prepares_step8(
    tmp_path: Path,
) -> None:
    # Non-contiguous accumulated provenance: step5 request_id=None (the aged
    # predecessor generated by running_setup) and step6 request_id=non-empty.
    # The real/default Phase179 chain (real Phase178 -> real Phase145) still
    # prepares an exact PreparedWorkflowStep(step8) and leaves the
    # post-Phase178 committed snapshot byte-for-byte unchanged.

    def non_contiguous(root: Path) -> dict[str, object]:
        values = running_setup(root, steps=8, current=6)
        wf = values["workflow"]  # type: ignore[assignment]
        result = runtime_success(wf, 6, request_id_none=False)
        decision = prepare_decision(wf, 6)
        next_decision = prepare_decision(wf, 7)
        return {
            "workflow": wf,
            "result": result,
            "approval": approval_for(decision),
            "employee": employee_for(decision),
            "next_approval": approval_for(next_decision),
            "next_employee": employee_for(next_decision),
            "state_path": values["state_path"],
            "events_path": values["events_path"],
            "api_key": OpenAIApiKey(value=SecretStr("synthetic")),
            "execution_approval": execution_approval_for(values),
        }

    # Reference committed snapshot: real Phase178 alone on an identical
    # non-contiguous scenario leaves exactly the bytes Phase145 must not
    # change.
    ref = non_contiguous(tmp_path / "ref")
    wf_ref = ref["workflow"]  # type: ignore[assignment]
    sp_ref, ep_ref = Path(ref["state_path"]), Path(ref["events_path"])
    ref_calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    ref_out = phase178(
        ref["result"],
        wf_ref,
        ref["approval"],
        ref["employee"],
        sp_ref,
        ep_ref,
        TOOLS,
        ref["api_key"],
        ref["execution_approval"],
        success_transport(ref_calls),
    )
    assert type(ref_out) is WorkflowProgressionDecision
    assert ref_out.decision == "prepare_next_step" and ref_out.next_step_index == 8
    committed = (sp_ref.read_bytes(), ep_ref.read_bytes())

    # Full real/default Phase179 on the target scenario.
    s = non_contiguous(tmp_path / "a")
    wf = s["workflow"]  # type: ignore[assignment]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    out = phase179(
        s["result"],
        wf,
        s["approval"],
        s["employee"],
        sp,
        ep,
        TOOLS,
        s["api_key"],
        s["execution_approval"],
        success_transport(calls),
        s["next_approval"],
        s["next_employee"],
    )
    assert type(out) is PreparedWorkflowStep
    assert out.step_id == "step-8" and out.step_index == 8
    assert out.employee_id == "e8"
    final_events = loaded_events(tmp_path / "a")
    assert len(final_events) == 7
    assert final_events[4].request_id is None  # step5 aged None
    assert final_events[5].request_id is not None  # step6 non-empty
    assert len(calls) == 1
    # valid Phase145 leaves the post-Phase178 committed snapshot unchanged
    assert sp.read_bytes() == committed[0]
    assert ep.read_bytes() == committed[1]


def test_12_invalid_stale_next_approval_rejected_after_committed_preserved(
    tmp_path: Path,
) -> None:
    ref = _committed_ref(tmp_path, "ref")
    committed = ref["committed"]
    s = _scenario(tmp_path, "target")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    with pytest.raises(Phase145Error) as exc:
        phase179(
            s["result"],
            wf,
            s["approval"],
            s["employee"],
            sp,
            ep,
            TOOLS,
            s["api_key"],
            s["execution_approval"],
            success_transport([]),
            ref["approval"],  # stale step-7 approval (next step-7), wrong for step-8
            ref["next_employee"],
        )
    assert exc.value.detail.classification == "approval_contract"
    assert sp.read_bytes() == committed[0]
    assert ep.read_bytes() == committed[1]


def test_13_invalid_stale_next_employee_rejected_after_committed_preserved(
    tmp_path: Path,
) -> None:
    ref = _committed_ref(tmp_path, "ref")
    committed = ref["committed"]
    s = _scenario(tmp_path, "target")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    with pytest.raises(Phase145Error) as exc:
        phase179(
            s["result"],
            wf,
            s["approval"],
            s["employee"],
            sp,
            ep,
            TOOLS,
            s["api_key"],
            s["execution_approval"],
            success_transport([]),
            ref["next_approval"],
            ref["employee"],  # stale step-7 employee (id e7), wrong for step-8
        )
    assert exc.value.detail.classification == "employee_contract"
    assert sp.read_bytes() == committed[0]
    assert ep.read_bytes() == committed[1]


def test_14_malformed_substitute_phase178_output_phase178_contract_zero145(
    tmp_path: Path,
) -> None:
    s = _scenario(tmp_path, "a")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    prepare = prepare_decision(wf, 7)
    p145_calls: list[int] = []

    def phase178_stub(*args: object, **kwargs: object) -> object:
        return "NOT-A-DECISION"

    def phase145_stub(*args: object, **kwargs: object) -> object:
        p145_calls.append(1)
        return None

    def call() -> object:
        return phase179(
            s["result"],
            wf,
            s["approval"],
            s["employee"],
            sp,
            ep,
            TOOLS,
            s["api_key"],
            s["execution_approval"],
            None,
            approval_for(prepare),
            employee_for(prepare),
            phase178_function=phase178_stub,  # type: ignore[arg-type]
            phase145_function=phase145_stub,  # type: ignore[arg-type]
        )

    assert_classification(call, "phase178_contract")
    assert p145_calls == []

    # (b) unexpected Phase178 error -> dependency_error, zero Phase145 calls, and
    # no pre-Phase178 rollback (the original targets are left exactly as-is).
    s_b = _scenario(tmp_path, "b")
    wf_b = s_b["workflow"]
    sp_b, ep_b = Path(s_b["state_path"]), Path(s_b["events_path"])
    prepare_b = prepare_decision(wf_b, 7)
    p145_b: list[int] = []
    before_b = (sp_b.read_bytes(), ep_b.read_bytes())

    def phase178_unexpected_stub(*args: object, **kwargs: object) -> object:
        raise RuntimeError("boom")

    def phase145_b_stub(*args: object, **kwargs: object) -> object:
        p145_b.append(1)
        return None

    def call_b() -> object:
        return phase179(
            s_b["result"],
            wf_b,
            s_b["approval"],
            s_b["employee"],
            sp_b,
            ep_b,
            TOOLS,
            s_b["api_key"],
            s_b["execution_approval"],
            None,
            approval_for(prepare_b),
            employee_for(prepare_b),
            phase178_function=phase178_unexpected_stub,  # type: ignore[arg-type]
            phase145_function=phase145_b_stub,  # type: ignore[arg-type]
        )

    assert_classification(call_b, "dependency_error")
    assert p145_b == []
    assert sp_b.read_bytes() == before_b[0]
    assert ep_b.read_bytes() == before_b[1]

    # (c) exact lower-phase safe-error identity propagates from Phase178: the
    # Phase178CompatibilityError raised by phase178 is re-raised by identity.
    s_c = _scenario(tmp_path, "c")
    wf_c = s_c["workflow"]
    sp_c, ep_c = Path(s_c["state_path"]), Path(s_c["events_path"])
    prepare_c = prepare_decision(wf_c, 7)
    sentinel_c = Phase178CompatibilityError("phase178_contract")
    p145_c: list[int] = []

    def phase178_sentinel_stub(*args: object, **kwargs: object) -> object:
        raise sentinel_c

    def phase145_c_stub(*args: object, **kwargs: object) -> object:
        p145_c.append(1)
        return None

    with pytest.raises(Phase178CompatibilityError) as exc:
        phase179(
            s_c["result"],
            wf_c,
            s_c["approval"],
            s_c["employee"],
            sp_c,
            ep_c,
            TOOLS,
            s_c["api_key"],
            s_c["execution_approval"],
            None,
            approval_for(prepare_c),
            employee_for(prepare_c),
            phase178_function=phase178_sentinel_stub,  # type: ignore[arg-type]
            phase145_function=phase145_c_stub,  # type: ignore[arg-type]
        )
    assert exc.value is sentinel_c
    assert p145_c == []


def test_15_phase178_output_identity_mismatch_phase178_contract(
    tmp_path: Path,
) -> None:
    s = _scenario(tmp_path, "a")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    bad = WorkflowProgressionDecision(
        "workflow_complete",
        "OTHER",
        "step-7",
        7,
        "e7",
        None,
        None,
        None,
        "last_step_succeeded",
    )
    p145_calls: list[int] = []

    def phase178_stub(*args: object, **kwargs: object) -> object:
        return bad

    def phase145_stub(*args: object, **kwargs: object) -> object:
        p145_calls.append(1)
        return None

    def call() -> object:
        return phase179(
            s["result"],
            wf,
            s["approval"],
            s["employee"],
            sp,
            ep,
            TOOLS,
            s["api_key"],
            s["execution_approval"],
            None,
            None,
            None,
            phase178_function=phase178_stub,  # type: ignore[arg-type]
            phase145_function=phase145_stub,  # type: ignore[arg-type]
        )

    assert_classification(call, "phase178_contract")
    assert p145_calls == []


def test_16_phase178_output_vs_durable_snapshot_mismatch_phase178_contract(
    tmp_path: Path,
) -> None:
    s = _scenario(tmp_path, "a")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    prepare = prepare_decision(wf, 7)
    p145_calls: list[int] = []

    def phase178_stub(*args: object, **kwargs: object) -> object:
        return prepare

    def phase145_stub(*args: object, **kwargs: object) -> object:
        p145_calls.append(1)
        return prepared_for(wf, prepare, employee_for(prepare))

    def call() -> object:
        return phase179(
            s["result"],
            wf,
            s["approval"],
            s["employee"],
            sp,
            ep,
            TOOLS,
            s["api_key"],
            s["execution_approval"],
            None,
            approval_for(prepare),
            employee_for(prepare),
            phase178_function=phase178_stub,  # type: ignore[arg-type]
            phase145_function=phase145_stub,  # type: ignore[arg-type]
        )

    assert_classification(call, "phase178_contract")
    assert p145_calls == []


def test_17_malformed_substitute_phase145_return_restores_committed(
    tmp_path: Path,
) -> None:
    ref = _committed_ref(tmp_path, "ref")
    committed = ref["committed"]
    s = _scenario(tmp_path, "target")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    prepare = prepare_decision(wf, 7)

    def phase145_stub(*args: object, **kwargs: object) -> object:
        return "NOT-A-PREPARED-STEP"

    def call() -> object:
        return phase179(
            s["result"],
            wf,
            s["approval"],
            s["employee"],
            sp,
            ep,
            TOOLS,
            s["api_key"],
            s["execution_approval"],
            success_transport([]),
            approval_for(prepare),
            employee_for(prepare),
            phase145_function=phase145_stub,  # type: ignore[arg-type]
        )

    assert_classification(call, "phase145_contract")
    # restored only to post-Phase178 committed bytes (not pre-Phase178)
    assert sp.read_bytes() == committed[0]
    assert ep.read_bytes() == committed[1]


def test_18_phase145_safe_error_identity_preserved_and_targets_restored(
    tmp_path: Path,
) -> None:
    ref = _committed_ref(tmp_path, "ref")
    committed = ref["committed"]
    s = _scenario(tmp_path, "target")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    prepare = prepare_decision(wf, 7)
    sentinel = Phase145CompatibilityError("employee_contract")

    def phase145_stub(*args: object, **kwargs: object) -> object:
        sp.write_bytes(b"BAD-STATE")
        ep.write_bytes(b"BAD-EVENTS")
        raise sentinel

    with pytest.raises(Phase145Error) as exc:
        phase179(
            s["result"],
            wf,
            s["approval"],
            s["employee"],
            sp,
            ep,
            TOOLS,
            s["api_key"],
            s["execution_approval"],
            success_transport([]),
            approval_for(prepare),
            employee_for(prepare),
            phase145_function=phase145_stub,  # type: ignore[arg-type]
        )
    assert exc.value is sentinel
    assert sp.read_bytes() == committed[0]
    assert ep.read_bytes() == committed[1]

    # Phase137 identity: a safe Phase137 error surfaced through Phase145 is
    # re-raised by exact identity after restoring the committed snapshot, even
    # though the stub polluted both targets first.
    s137 = _scenario(tmp_path, "target137")
    wf137 = s137["workflow"]
    sp137, ep137 = Path(s137["state_path"]), Path(s137["events_path"])
    prepare137 = prepare_decision(wf137, 7)
    sentinel137 = Phase137CompatibilityError("terminal_contract")

    def phase145_stub137(*args: object, **kwargs: object) -> object:
        sp137.write_bytes(b"BAD-STATE")
        ep137.write_bytes(b"BAD-EVENTS")
        raise sentinel137

    with pytest.raises(Phase137Error) as exc:
        phase179(
            s137["result"],
            wf137,
            s137["approval"],
            s137["employee"],
            sp137,
            ep137,
            TOOLS,
            s137["api_key"],
            s137["execution_approval"],
            success_transport([]),
            approval_for(prepare137),
            employee_for(prepare137),
            phase145_function=phase145_stub137,  # type: ignore[arg-type]
        )
    assert exc.value is sentinel137
    assert sp137.read_bytes() == committed[0]
    assert ep137.read_bytes() == committed[1]

def test_19_unexpected_phase145_error_sanitized_and_rollback_failure(
    tmp_path: Path,
) -> None:
    # (a) unexpected Phase145 error -> dependency_error, committed preserved.
    ref = _committed_ref(tmp_path, "ref")
    committed = ref["committed"]
    s = _scenario(tmp_path, "target")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    prepare = prepare_decision(wf, 7)

    def unexpected_stub(*args: object, **kwargs: object) -> object:
        raise RuntimeError("boom")

    def call_a() -> object:
        return phase179(
            s["result"],
            wf,
            s["approval"],
            s["employee"],
            sp,
            ep,
            TOOLS,
            s["api_key"],
            s["execution_approval"],
            success_transport([]),
            approval_for(prepare),
            employee_for(prepare),
            phase145_function=unexpected_stub,  # type: ignore[arg-type]
        )

    assert_classification(call_a, "dependency_error")
    assert sp.read_bytes() == committed[0]
    assert ep.read_bytes() == committed[1]

    # (b) rollback failure: phase145 replaces the state target with a directory so
    # the committed-bytes restore write raises OSError -> rollback_failure.
    s2 = _scenario(tmp_path, "target_b")
    wf2 = s2["workflow"]
    sp2, ep2 = Path(s2["state_path"]), Path(s2["events_path"])
    prepare2 = prepare_decision(wf2, 7)

    def mutating_unexpected_stub(*args: object, **kwargs: object) -> object:
        sp2.unlink()          # remove committed state file
        sp2.mkdir()           # replace with a directory -> restore write raises
        raise RuntimeError("boom")

    def call_b() -> object:
        return phase179(
            s2["result"],
            wf2,
            s2["approval"],
            s2["employee"],
            sp2,
            ep2,
            TOOLS,
            s2["api_key"],
            s2["execution_approval"],
            success_transport([]),
            approval_for(prepare2),
            employee_for(prepare2),
            phase145_function=mutating_unexpected_stub,  # type: ignore[arg-type]
        )

    # The committed restore write cannot succeed (target is now a directory), so
    # the compensation classifies rollback_failure after attempting both targets.
    with pytest.raises(Phase179Error) as exc:
        call_b()
    assert exc.value.detail.classification == "rollback_failure"

    # (c) target read OSError -> dependency_error.  A chmod-000 state target makes
    # the post-Phase178 committed-snapshot capture read fail during the routine
    # Phase179 run.
    s3 = _scenario(tmp_path, "target_c")
    wf3 = s3["workflow"]
    sp3, ep3 = Path(s3["state_path"]), Path(s3["events_path"])
    prepare3 = prepare_decision(wf3, 7)
    sp3.chmod(0)
    try:

        def call_c() -> object:
            return phase179(
                s3["result"],
                wf3,
                s3["approval"],
                s3["employee"],
                sp3,
                ep3,
                TOOLS,
                s3["api_key"],
                s3["execution_approval"],
                success_transport([]),
                approval_for(prepare3),
                employee_for(prepare3),
            )

        assert_classification(call_c, "dependency_error")
    finally:
        sp3.chmod(0o600)


def test_20_no_readvance_invariant_after_prepared_step_return(tmp_path: Path) -> None:
    s = _scenario(tmp_path, "a")
    wf = s["workflow"]
    sp, ep = Path(s["state_path"]), Path(s["events_path"])
    calls: list[OpenAIResponsesAuthenticatedHttpRequest] = []
    out = phase179(
        s["result"],
        wf,
        s["approval"],
        s["employee"],
        sp,
        ep,
        TOOLS,
        s["api_key"],
        s["execution_approval"],
        success_transport(calls),
        s["next_approval"],
        s["next_employee"],
    )
    assert type(out) is PreparedWorkflowStep
    state = load_workflow_execution_state(sp)
    assert state.status == "succeeded"
    assert state.current_step_id == "step-7" and state.current_step_index == 7
    final_events = loaded_events(tmp_path / "a")
    assert len(final_events) == 7
    assert final_events[-1].step_id == "step-7"
    assert final_events[-1].event_type == "step_succeeded"
    assert len(calls) == 1
