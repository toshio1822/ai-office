"""Focused and real-default tests for Issue #369 (Phase 176).

Phase 176 composes the public Phase 175 (runtime result -> prepared-step
start / stop) with the public Phase 147 (prepared-start persistence) exactly
once each, stopping after one exact ``RunningStatePersistenceResult`` or one
exact stop object.

Requirement-to-test mapping (Issue #369):
- public signature / exact default dependency identities / source audit
  -> test_public_signature_default_dependencies_and_source_audit
- prepared route exact stage order Phase175 -> Phase147, canonical argument
  identity/order, exactly once, exact RunningStatePersistenceResult contract
  -> test_prepared_route_exact_stage_order_and_contract
- workflow_complete stop exact identity through Phase147 with both target
  bytes unchanged -> test_workflow_complete_stop_route_exact_identity_bytes_unchanged
- persisted_failure stop exact identity through Phase147 with both target
  bytes unchanged -> test_persisted_failure_stop_route_exact_identity_bytes_unchanged
- non-callable dependency configuration rejects before execution
  -> test_non_callable_dependency_configuration_rejects_before_execution
- Phase175 recognized safe error identity; Phase147 zero; no outer
  rollback/write -> test_phase175_recognized_safe_error_identity_phase147_zero
- Phase175 unexpected exception sanitized; Phase147 zero; no outer
  rollback/write -> test_phase175_unexpected_exception_sanitized_phase147_zero
- malformed Phase175 return -> phase175_contract; Phase147 zero; no outer
  rollback/write -> test_malformed_phase175_return_phase175_contract_phase147_zero
- Phase147 recognized safe error identity after valid Phase175; mutation
  restored only to the post-Phase175 committed snapshot
  -> test_phase147_recognized_safe_error_identity_restored_to_committed
- Phase147 unexpected exception after valid Phase175; mutation restored to
  committed snapshot then dependency_error
  -> test_phase147_unexpected_exception_restored_then_dependency_error
- malformed Phase147 prepared return rejected/restored to committed snapshot
  -> test_malformed_phase147_prepared_return_phase147_contract_restored
- malformed Phase147 stop return rejected/restored to committed snapshot
  -> test_malformed_phase147_stop_return_phase147_contract_restored
- apparently valid Phase147 persistence/stop result with unauthorized
  state/event bytes -> committed_mutation, restored to committed snapshot
  -> test_phase147_valid_return_with_unauthorized_mutation_committed_mutation
- input/target-domain rejection before Phase175, including wrong stop
  discriminator -> test_input_target_domain_rejection_before_phase175
- approval/employee deliberately not prevalidated: Phase175 runs first and
  may reject after committing; Phase147 zero; committed terminal bytes
  retained -> test_approval_employee_not_prevalidated_phase175_runs_first
- rollback failure after Phase147 mutation attempts both target restorations
  once and retries neither stage
  -> test_rollback_failure_after_phase147_mutation_both_restorations_once
- real A: seven-step step-6 success -> exact persisted step-7 running state
  -> test_real_a_seven_step_step6_success_exact_step7_running_state_persistence
- real B: six-step success stop -> exact workflow_complete identity
  -> test_real_b_six_step_success_workflow_complete_stop
- real C: six-step failure stop -> exact persisted_failure identity
  -> test_real_c_six_step_failure_persisted_failure_stop
- real D: missing approval remains Phase-175-owned; Phase147 zero
  -> test_real_d_missing_approval_phase175_owned_durable_commit_phase147_zero
"""

# ruff: noqa: E501,E701,E702,F401,I001

import ast
import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryCompatibilityError,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_runtime_result_to_prepared_start_persistence_orchestration_boundary,
    route_runtime_result_to_prepared_step_start_orchestration_boundary,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase145CompatibilityError,
)
from ai_office.engine.runtime_result_to_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase176Error,
)
from ai_office.engine.runtime_result_to_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError as Phase175ContractError,
)
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
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

phase176 = route_runtime_result_to_prepared_start_persistence_orchestration_boundary
phase175 = route_runtime_result_to_prepared_step_start_orchestration_boundary
phase147 = route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary


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


def runtime_success(wf: WorkflowDefinition, index: int) -> StepRuntimeExecutionSuccess:
    step = wf.steps[index - 1]
    return StepRuntimeExecutionSuccess(
        "w",
        step.id,
        index,
        step.employee,
        ModelInvocationSuccess(
            "openai",
            f"response-{step.id}",
            f"request-{step.id}",
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


def recording_stub(return_value: object) -> tuple[object, list[tuple[tuple[object, ...], dict[str, object]]]]:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def stub(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return return_value

    return stub, calls


def raising_stub(error: BaseException) -> tuple[object, list[tuple[tuple[object, ...], dict[str, object]]]]:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def stub(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        raise error

    return stub, calls


def assert_classification(fn: object, expected: str) -> None:
    with pytest.raises(Phase176Error) as exc:
        fn()  # type: ignore[operator]
    assert exc.value.detail.classification == expected


def test_public_signature_default_dependencies_and_source_audit() -> None:
    signature = inspect.signature(phase176)
    parameters = list(signature.parameters.values())
    assert [p.name for p in parameters] == [
        "result",
        "workflow",
        "approval",
        "employee",
        "state_path",
        "events_path",
        "phase175_function",
        "phase147_function",
    ]
    assert [p.kind for p in parameters[:6]] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD
    ] * 6
    assert [p.kind for p in parameters[6:]] == [inspect.Parameter.KEYWORD_ONLY] * 2
    assert parameters[6].default is phase175
    assert parameters[7].default is phase147

    module = inspect.getmodule(phase176)
    assert module is not None
    assert (
        module.__name__
        == "ai_office.engine.runtime_result_to_prepared_start_persistence_orchestration_boundary"
    )
    source = inspect.getsource(module)
    tree = ast.parse(source)
    called = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "phase175_function" in called
    assert "phase147_function" in called
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
        )
    ]
    assert lower_direct_calls == []


def test_prepared_route_exact_stage_order_and_contract(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    start = constructed_start(values)
    phase175_stub, phase175_calls = recording_stub(start)

    def persistence_stub(
        *args: object, **kwargs: object
    ) -> RunningStatePersistenceResult:
        started = args[0]
        assert type(started) is PreparedStepExecutionStart
        state_path = args[3]
        state_bytes = serialize_workflow_execution_state_json(
            started.running_state  # type: ignore[union-attr]
        ).encode("utf-8")
        Path(state_path).write_bytes(state_bytes)  # type: ignore[arg-type]
        return RunningStatePersistenceResult(len(state_bytes))

    phase147_stub, phase147_calls = recording_stub(persistence_stub)

    def spy147(*args: object, **kwargs: object) -> object:
        phase147_calls.append((args, kwargs))
        return persistence_stub(*args, **kwargs)

    out = phase176(
        result,
        wf,
        approval,
        employee,
        values["state_path"],
        values["events_path"],
        phase175_function=phase175_stub,  # type: ignore[arg-type]
        phase147_function=spy147,  # type: ignore[arg-type]
    )
    assert type(out) is RunningStatePersistenceResult
    assert out.state_bytes_written > 0
    assert len(phase175_calls) == 1
    assert len(phase147_calls) == 1
    phase175_args = phase175_calls[0][0]
    assert phase175_args[0] is result
    assert phase175_args[1] is wf
    assert phase175_args[2] is approval
    assert phase175_args[3] is employee
    assert phase175_args[4] is values["state_path"]
    assert phase175_args[5] is values["events_path"]
    phase147_args = phase147_calls[0][0]
    assert phase147_args[0] is start
    assert phase147_args[1] is wf
    assert phase147_args[2] is employee
    assert phase147_args[3] is values["state_path"]
    assert phase147_args[4] is values["events_path"]
    state_bytes = Path(values["state_path"]).read_bytes()  # type: ignore[arg-type]
    assert state_bytes == serialize_workflow_execution_state_json(start.running_state).encode(
        "utf-8"
    )
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]
    assert out.state_bytes_written == len(state_bytes)
    loaded = load_workflow_execution_state(Path(values["state_path"]))  # type: ignore[arg-type]
    assert loaded == start.running_state


def test_workflow_complete_stop_route_exact_identity_bytes_unchanged(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    stop = complete_decision(wf, 6)
    phase175_stub, _ = recording_stub(stop)
    phase147_stub, phase147_calls = recording_stub(stop)
    out = phase176(
        stop,
        wf,
        None,
        None,
        values["state_path"],
        values["events_path"],
        phase175_function=phase175_stub,  # type: ignore[arg-type]
        phase147_function=phase147_stub,  # type: ignore[arg-type]
    )
    assert out is stop
    assert len(phase147_calls) == 1
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_persisted_failure_stop_route_exact_identity_bytes_unchanged(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    stop = failure_outcome(wf, 6)
    phase175_stub, _ = recording_stub(stop)
    phase147_stub, phase147_calls = recording_stub(stop)
    out = phase176(
        stop,
        wf,
        None,
        None,
        values["state_path"],
        values["events_path"],
        phase175_function=phase175_stub,  # type: ignore[arg-type]
        phase147_function=phase147_stub,  # type: ignore[arg-type]
    )
    assert out is stop
    assert len(phase147_calls) == 1
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_non_callable_dependency_configuration_rejects_before_execution(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    assert_classification(
        lambda: phase176(
            result,
            wf,
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase175_function="not-callable",  # type: ignore[arg-type]
        ),
        "configuration",
    )
    assert_classification(
        lambda: phase176(
            result,
            wf,
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase147_function=123,  # type: ignore[arg-type]
        ),
        "configuration",
    )
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_phase175_recognized_safe_error_identity_phase147_zero(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    safe_error = Phase175ContractError("phase173_contract")
    phase175_stub, _ = raising_stub(safe_error)
    phase147_stub, phase147_calls = recording_stub(None)
    with pytest.raises(Phase175ContractError) as exc:
        phase176(
            result,
            wf,
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase175_function=phase175_stub,  # type: ignore[arg-type]
            phase147_function=phase147_stub,  # type: ignore[arg-type]
        )
    assert exc.value is safe_error
    assert len(phase147_calls) == 0
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_phase175_unexpected_exception_sanitized_phase147_zero(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    phase175_stub, _ = raising_stub(RuntimeError("secret-boom"))
    phase147_stub, phase147_calls = recording_stub(None)
    with pytest.raises(Phase176Error) as exc:
        phase176(
            result,
            wf,
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase175_function=phase175_stub,  # type: ignore[arg-type]
            phase147_function=phase147_stub,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "dependency_error"
    assert "secret-boom" not in str(exc.value)
    assert len(phase147_calls) == 0
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_malformed_phase175_return_phase175_contract_phase147_zero(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    employee = employee_for(prepare_decision(wf, 6))
    start = constructed_start(values)
    wrong_completed = replace(
        start,
        running_state=replace(
            start.running_state,
            completed_step_ids=("step-1",),
        ),
    )
    wrong_request = replace(
        start,
        request=replace(start.request, model="wrong-model"),
    )
    wrong_step = replace(
        start,
        running_state=replace(
            start.running_state,
            current_step_id="step-6",
            current_step_index=6,
            current_employee_id="e6",
        ),
    )
    malformed_cases = [
        (None, employee),
        ("garbage", employee),
        (object(), employee),
        (wrong_completed, employee),
        (wrong_request, employee),
        (wrong_step, employee),
        (start, None),  # wrong employee type on the prepared route
    ]
    for malformed, employee_value in malformed_cases:
        phase175_stub, _ = recording_stub(malformed)
        phase147_stub, phase147_calls = recording_stub(None)
        with pytest.raises(Phase176Error) as exc:
            phase176(
                result,
                wf,
                None,
                employee_value,
                values["state_path"],
                values["events_path"],
                phase175_function=phase175_stub,  # type: ignore[arg-type]
                phase147_function=phase147_stub,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase175_contract"
        assert len(phase147_calls) == 0
        assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
        assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_phase147_recognized_safe_error_identity_restored_to_committed(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    employee = employee_for(prepare_decision(wf, 6))
    start = constructed_start(values)
    phase175_stub, _ = recording_stub(start)
    committed_state = Path(values["state_path"]).read_bytes()  # type: ignore[arg-type]
    committed_events = Path(values["events_path"]).read_bytes()  # type: ignore[arg-type]
    safe_error = (
        PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError(
            "terminal_contract"
        )
    )

    def mutating_raising_stub(*args: object, **kwargs: object) -> object:
        Path(values["state_path"]).write_bytes(b"tampered")  # type: ignore[arg-type]
        Path(values["events_path"]).write_bytes(b"tampered-events")  # type: ignore[arg-type]
        raise safe_error

    phase147_stub, phase147_calls = recording_stub(mutating_raising_stub)

    def spy147(*args: object, **kwargs: object) -> object:
        phase147_calls.append((args, kwargs))
        return mutating_raising_stub(*args, **kwargs)

    with pytest.raises(
        PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError
    ) as exc:
        phase176(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            phase175_function=phase175_stub,  # type: ignore[arg-type]
            phase147_function=spy147,  # type: ignore[arg-type]
        )
    assert exc.value is safe_error
    assert len(phase147_calls) == 1
    assert Path(values["state_path"]).read_bytes() == committed_state  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == committed_events  # type: ignore[arg-type]


def test_phase147_unexpected_exception_restored_then_dependency_error(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    employee = employee_for(prepare_decision(wf, 6))
    start = constructed_start(values)
    phase175_stub, _ = recording_stub(start)
    committed_state = Path(values["state_path"]).read_bytes()  # type: ignore[arg-type]
    committed_events = Path(values["events_path"]).read_bytes()  # type: ignore[arg-type]

    def mutating_raising_stub(*args: object, **kwargs: object) -> object:
        Path(values["state_path"]).write_bytes(b"tampered")  # type: ignore[arg-type]
        raise RuntimeError("phase147-secret")

    phase147_stub, phase147_calls = recording_stub(mutating_raising_stub)

    def spy147(*args: object, **kwargs: object) -> object:
        phase147_calls.append((args, kwargs))
        return mutating_raising_stub(*args, **kwargs)

    with pytest.raises(Phase176Error) as exc:
        phase176(
            result,
            wf,
            None,
            employee,
            values["state_path"],
            values["events_path"],
            phase175_function=phase175_stub,  # type: ignore[arg-type]
            phase147_function=spy147,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "dependency_error"
    assert "phase147-secret" not in str(exc.value)
    assert len(phase147_calls) == 1
    assert Path(values["state_path"]).read_bytes() == committed_state  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == committed_events  # type: ignore[arg-type]


def test_malformed_phase147_prepared_return_phase147_contract_restored(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    employee = employee_for(prepare_decision(wf, 6))
    start = constructed_start(values)
    phase175_stub, _ = recording_stub(start)
    committed_state = Path(values["state_path"]).read_bytes()  # type: ignore[arg-type]
    committed_events = Path(values["events_path"]).read_bytes()  # type: ignore[arg-type]
    malformed_returns = [
        None,
        "garbage",
        RunningStatePersistenceResult(0),
        RunningStatePersistenceResult(-3),
        RunningStatePersistenceResult(True),  # type: ignore[arg-type]
        RunningStatePersistenceResult("5"),  # type: ignore[arg-type]
    ]
    for malformed in malformed_returns:
        def mutating_stub(*args: object, **kwargs: object) -> object:
            Path(values["state_path"]).write_bytes(b"tampered")  # type: ignore[arg-type]
            return malformed

        phase147_stub, phase147_calls = recording_stub(mutating_stub)

        def spy147(*args: object, **kwargs: object) -> object:
            phase147_calls.append((args, kwargs))
            return mutating_stub(*args, **kwargs)

        with pytest.raises(Phase176Error) as exc:
            phase176(
                result,
                wf,
                None,
                employee,
                values["state_path"],
                values["events_path"],
                phase175_function=phase175_stub,  # type: ignore[arg-type]
                phase147_function=spy147,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase147_contract"
        assert len(phase147_calls) == 1
        assert Path(values["state_path"]).read_bytes() == committed_state  # type: ignore[arg-type]
        assert Path(values["events_path"]).read_bytes() == committed_events  # type: ignore[arg-type]


def test_malformed_phase147_stop_return_phase147_contract_restored(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    committed_state = Path(values["state_path"]).read_bytes()  # type: ignore[arg-type]
    committed_events = Path(values["events_path"]).read_bytes()  # type: ignore[arg-type]
    for stop in (complete_decision(wf, 6), failure_outcome(wf, 6)):
        phase175_stub, _ = recording_stub(stop)
        wrong = replace(stop) if hasattr(stop, "__dataclass_fields__") else object()

        def mutating_stub(*args: object, **kwargs: object) -> object:
            Path(values["events_path"]).write_bytes(b"tampered-events")  # type: ignore[arg-type]
            return wrong

        phase147_stub, phase147_calls = recording_stub(mutating_stub)

        def spy147(*args: object, **kwargs: object) -> object:
            phase147_calls.append((args, kwargs))
            return mutating_stub(*args, **kwargs)

        with pytest.raises(Phase176Error) as exc:
            phase176(
                stop,
                wf,
                None,
                None,
                values["state_path"],
                values["events_path"],
                phase175_function=phase175_stub,  # type: ignore[arg-type]
                phase147_function=spy147,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase147_contract"
        assert len(phase147_calls) == 1
        assert Path(values["state_path"]).read_bytes() == committed_state  # type: ignore[arg-type]
        assert Path(values["events_path"]).read_bytes() == committed_events  # type: ignore[arg-type]


def test_phase147_valid_return_with_unauthorized_mutation_committed_mutation(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    employee = employee_for(prepare_decision(wf, 6))
    start = constructed_start(values)
    phase175_stub, _ = recording_stub(start)
    committed_state = Path(values["state_path"]).read_bytes()  # type: ignore[arg-type]
    committed_events = Path(values["events_path"]).read_bytes()  # type: ignore[arg-type]
    valid_persistence = RunningStatePersistenceResult(len(committed_state))

    def wrong_state_stub(*args: object, **kwargs: object) -> object:
        Path(values["state_path"]).write_bytes(b"wrong-state")  # type: ignore[arg-type]
        return RunningStatePersistenceResult(11)

    def wrong_events_stub(*args: object, **kwargs: object) -> object:
        Path(values["events_path"]).write_bytes(b"wrong-events")  # type: ignore[arg-type]
        return valid_persistence

    def delete_state_stub(*args: object, **kwargs: object) -> object:
        Path(values["state_path"]).unlink()  # type: ignore[arg-type]
        return valid_persistence

    def delete_events_stub(*args: object, **kwargs: object) -> object:
        Path(values["events_path"]).unlink()  # type: ignore[arg-type]
        return valid_persistence

    prepared_mutations = [
        wrong_state_stub,
        wrong_events_stub,
        delete_state_stub,
        delete_events_stub,
    ]
    for mutator in prepared_mutations:
        phase147_stub, phase147_calls = recording_stub(mutator)

        def spy147(*args: object, **kwargs: object) -> object:
            phase147_calls.append((args, kwargs))
            return mutator(*args, **kwargs)

        with pytest.raises(Phase176Error) as exc:
            phase176(
                result,
                wf,
                None,
                employee,
                values["state_path"],
                values["events_path"],
                phase175_function=phase175_stub,  # type: ignore[arg-type]
                phase147_function=spy147,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "committed_mutation"
        assert len(phase147_calls) == 1
        assert Path(values["state_path"]).read_bytes() == committed_state  # type: ignore[arg-type]
        assert Path(values["events_path"]).read_bytes() == committed_events  # type: ignore[arg-type]

    stop_values = canonical_running_setup(tmp_path / "stop", steps=6, current=6)
    stop_wf = stop_values["workflow"]  # type: ignore[arg-type]
    stop_committed_state = Path(stop_values["state_path"]).read_bytes()  # type: ignore[arg-type]
    stop_committed_events = Path(stop_values["events_path"]).read_bytes()  # type: ignore[arg-type]
    for stop in (complete_decision(stop_wf, 6), failure_outcome(stop_wf, 6)):
        phase175_stub, _ = recording_stub(stop)

        def stop_mutator(*args: object, **kwargs: object) -> object:
            Path(stop_values["state_path"]).write_bytes(b"stop-tampered")  # type: ignore[arg-type]
            Path(stop_values["events_path"]).write_bytes(b"stop-tampered-events")  # type: ignore[arg-type]
            return stop

        phase147_stub, phase147_calls = recording_stub(stop_mutator)

        def spy147(*args: object, **kwargs: object) -> object:
            phase147_calls.append((args, kwargs))
            return stop_mutator(*args, **kwargs)

        with pytest.raises(Phase176Error) as exc:
            phase176(
                stop,
                stop_wf,
                None,
                None,
                stop_values["state_path"],
                stop_values["events_path"],
                phase175_function=phase175_stub,  # type: ignore[arg-type]
                phase147_function=spy147,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "committed_mutation"
        assert len(phase147_calls) == 1
        assert Path(stop_values["state_path"]).read_bytes() == stop_committed_state  # type: ignore[arg-type]
        assert Path(stop_values["events_path"]).read_bytes() == stop_committed_events  # type: ignore[arg-type]


def test_input_target_domain_rejection_before_phase175(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    approval = approval_for(prepare_decision(wf, 6))
    employee = employee_for(prepare_decision(wf, 6))
    phase175_stub, phase175_calls = recording_stub(None)
    phase147_stub, _ = recording_stub(None)

    class SubDecision(WorkflowProgressionDecision):
        pass

    class SubSuccess(StepRuntimeExecutionSuccess):
        pass

    wrong_decision = WorkflowProgressionDecision(
        "prepare_next_step",
        "w",
        "step-6",
        6,
        "e6",
        "step-7",
        7,
        "e7",
        "next_step_available",
    )
    persisted_success = PersistedExecutionOutcome(
        "persisted_success", "w", "step-6", 6, "e6", "api_error"
    )
    subclass_decision = SubDecision(
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
    subclass_success = SubSuccess(
        "w",
        "step-6",
        6,
        "e6",
        ModelInvocationSuccess(
            "openai", "response", "request", "completed", ("output",), "output"
        ),
    )
    cases = [
        ("wrong stop discriminator prepare_next_step", wrong_decision, "result_type"),
        ("persisted_success outcome", persisted_success, "result_type"),
        ("subclass decision", subclass_decision, "result_type"),
        ("subclass runtime success", subclass_success, "result_type"),
    ]
    for _label, result_value, expected in cases:
        with pytest.raises(Phase176Error) as exc:
            phase176(
                result_value,
                wf,
                approval,
                employee,
                values["state_path"],
                values["events_path"],
                phase175_function=phase175_stub,  # type: ignore[arg-type]
                phase147_function=phase147_stub,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == expected
        assert len(phase175_calls) == 0

    for _label, state_value, events_value, expected in [
        ("wrong state target type", object(), values["events_path"], "state_target"),
        ("wrong events target type", values["state_path"], object(), "event_target"),
    ]:
        with pytest.raises(Phase176Error) as exc:
            phase176(
                result,
                wf,
                approval,
                employee,
                state_value,
                events_value,
                phase175_function=phase175_stub,  # type: ignore[arg-type]
                phase147_function=phase147_stub,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == expected
        assert len(phase175_calls) == 0

    with pytest.raises(Phase176Error) as exc:
        phase176(
            result,
            "not-a-workflow",
            approval,
            employee,
            values["state_path"],
            values["events_path"],
            phase175_function=phase175_stub,  # type: ignore[arg-type]
            phase147_function=phase147_stub,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "workflow_definition"
    assert len(phase175_calls) == 0

    with pytest.raises(Phase176Error) as exc:
        phase176(
            result,
            wf,
            approval,
            employee,
            values["state_path"],
            values["state_path"],  # same target conflict
            phase175_function=phase175_stub,  # type: ignore[arg-type]
            phase147_function=phase147_stub,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "target_conflict"
    assert len(phase175_calls) == 0

    missing = tmp_path / "missing"
    with pytest.raises(Phase176Error) as exc:
        phase176(
            result,
            wf,
            approval,
            employee,
            missing,
            values["events_path"],
            phase175_function=phase175_stub,  # type: ignore[arg-type]
            phase147_function=phase147_stub,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "state_target"
    assert len(phase175_calls) == 0
    with pytest.raises(Phase176Error) as exc:
        phase176(
            result,
            wf,
            approval,
            employee,
            values["state_path"],
            missing,
            phase175_function=phase175_stub,  # type: ignore[arg-type]
            phase147_function=phase147_stub,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "event_target"
    assert len(phase175_calls) == 0
    assert Path(values["state_path"]).read_bytes() == values["state_before"]  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == values["events_before"]  # type: ignore[arg-type]


def test_approval_employee_not_prevalidated_phase175_runs_first(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    employee = employee_for(prepare_decision(wf, 6))
    phase147_calls: list[object] = []

    def counting_phase147(*args: object, **kwargs: object) -> object:
        phase147_calls.append(args)
        return phase147(*args, **kwargs)

    with pytest.raises(Phase145CompatibilityError) as exc:
        phase176(
            result,
            wf,
            None,  # missing approval
            employee,
            values["state_path"],
            values["events_path"],
            phase147_function=counting_phase147,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "approval_contract"
    assert len(phase147_calls) == 0
    loaded = load_workflow_execution_state(Path(values["state_path"]))  # type: ignore[arg-type]
    assert loaded.status == "succeeded" and loaded.current_step_index == 6
    assert Path(values["events_path"]).read_bytes() != values["events_before"]  # type: ignore[arg-type]
    assert Path(values["state_path"]).read_bytes() != values["state_before"]  # type: ignore[arg-type]
    assert b"step-7" not in Path(values["state_path"]).read_bytes()  # type: ignore[arg-type]


def test_rollback_failure_after_phase147_mutation_both_restorations_once(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    employee = employee_for(prepare_decision(wf, 6))
    start = constructed_start(values)
    phase175_stub, _ = recording_stub(start)
    committed_events = Path(values["events_path"]).read_bytes()  # type: ignore[arg-type]
    state_path = Path(values["state_path"])  # type: ignore[arg-type]
    events_path = Path(values["events_path"])  # type: ignore[arg-type]

    def sabotage_stub(*args: object, **kwargs: object) -> object:
        # State target becomes an un-writable directory (restore will fail);
        # events target is tampered (restore will succeed).  Both restorations
        # must be attempted exactly once.
        state_path.unlink()
        state_path.mkdir()
        events_path.write_bytes(b"tampered-events")
        raise PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError(
            "terminal_contract"
        )

    phase147_stub, phase147_calls = recording_stub(sabotage_stub)

    def spy147(*args: object, **kwargs: object) -> object:
        phase147_calls.append((args, kwargs))
        return sabotage_stub(*args, **kwargs)

    with pytest.raises(Phase176Error) as exc:
        phase176(
            result,
            wf,
            None,
            employee,
            state_path,
            events_path,
            phase175_function=phase175_stub,  # type: ignore[arg-type]
            phase147_function=spy147,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "rollback_failure"
    assert len(phase147_calls) == 1
    assert state_path.is_dir()
    assert events_path.read_bytes() == committed_events


def test_real_a_seven_step_step6_success_exact_step7_running_state_persistence(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    out = phase176(
        result,
        wf,
        approval,
        employee,
        values["state_path"],
        values["events_path"],
    )
    assert type(out) is RunningStatePersistenceResult
    assert type(out.state_bytes_written) is int and out.state_bytes_written > 0
    final_state = load_workflow_execution_state(Path(values["state_path"]))  # type: ignore[arg-type]
    assert final_state.status == "running"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    assert final_state.current_employee_id == "e7"
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    assert final_state.last_failure_category is None
    state_bytes = Path(values["state_path"]).read_bytes()  # type: ignore[arg-type]
    assert state_bytes == serialize_workflow_execution_state_json(final_state).encode(
        "utf-8"
    )
    assert out.state_bytes_written == len(state_bytes)
    events = [
        json.loads(line)
        for line in Path(values["events_path"])
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    assert len(events) == 6
    step5 = events[4]
    assert step5["step_id"] == "step-5"
    assert step5["provider"] == "openai"
    assert step5["output_text"] == ""
    assert step5["request_id"] is None
    assert all(event["step_id"] != "step-7" for event in events)


def test_real_b_six_step_success_workflow_complete_stop(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    out = phase176(
        result, wf, None, None, values["state_path"], values["events_path"]
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
    final_state = load_workflow_execution_state(Path(values["state_path"]))  # type: ignore[arg-type]
    assert final_state.status == "succeeded"
    assert final_state.current_step_index == 6
    events = [
        json.loads(line)
        for line in Path(values["events_path"])
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    assert len(events) == 6
    assert events[-1]["event_type"] == "step_succeeded"
    assert events[-1]["step_id"] == "step-6"


def test_real_c_six_step_failure_persisted_failure_stop(tmp_path: Path) -> None:
    values = canonical_running_setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_failure(wf, 6)
    out = phase176(
        result, wf, None, None, values["state_path"], values["events_path"]
    )
    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"
    assert out.workflow_id == "w"
    assert out.current_step_id == "step-6"
    assert out.current_step_index == 6
    assert out.current_employee_id == "e6"
    assert out.failure_category == "api_error"
    final_state = load_workflow_execution_state(Path(values["state_path"]))  # type: ignore[arg-type]
    assert final_state.status == "failed"
    assert final_state.current_step_index == 6
    assert final_state.last_failure_category == "api_error"
    events = [
        json.loads(line)
        for line in Path(values["events_path"])
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    assert len(events) == 6
    assert events[-1]["event_type"] == "step_failed"
    assert events[-1]["step_id"] == "step-6"
    assert events[-1]["failure_category"] == "api_error"


def test_real_d_missing_approval_phase175_owned_durable_commit_phase147_zero(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success(wf, 6)
    employee = employee_for(prepare_decision(wf, 6))
    state_before = Path(values["state_path"]).read_bytes()  # type: ignore[arg-type]
    events_before = Path(values["events_path"]).read_bytes()  # type: ignore[arg-type]
    phase147_calls: list[object] = []

    def counting_phase147(*args: object, **kwargs: object) -> object:
        phase147_calls.append(args)
        return phase147(*args, **kwargs)

    with pytest.raises(Phase145CompatibilityError) as exc:
        phase176(
            result,
            wf,
            None,  # missing approval with otherwise matching employee
            employee,
            values["state_path"],
            values["events_path"],
            phase147_function=counting_phase147,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "approval_contract"
    assert len(phase147_calls) == 0
    state_after = Path(values["state_path"]).read_bytes()  # type: ignore[arg-type]
    events_after = Path(values["events_path"]).read_bytes()  # type: ignore[arg-type]
    assert state_after != state_before
    assert events_after != events_before
    committed = load_workflow_execution_state(Path(values["state_path"]))  # type: ignore[arg-type]
    assert committed.status == "succeeded"
    assert committed.current_step_index == 6
    assert b"step-7" not in state_after
    events = [
        json.loads(line)
        for line in Path(values["events_path"])
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    assert len(events) == 6
    assert events[-1]["step_id"] == "step-6"
    # committed step-6 bytes remain intact after the rejection (no outer rollback)
    assert Path(values["state_path"]).read_bytes() == state_after  # type: ignore[arg-type]
    assert Path(values["events_path"]).read_bytes() == events_after  # type: ignore[arg-type]
