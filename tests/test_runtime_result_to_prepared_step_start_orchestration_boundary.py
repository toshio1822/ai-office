"""Focused Phase 175 post-runtime → prepared-step-start orchestration tests.

Phase 175 is the next explicit integration/orchestration boundary after Phase
173: it accepts one exact Phase-155 runtime/stop result and composes public
Phase 173 then public Phase 146 in exactly that order. Phase 173 owns the
durable runtime-result persistence/classification/progression chain plus
explicit approval/employee validation and approved next-step preparation;
Phase 146 owns the prepared-step-start terminal-history validation and the
full public prepared-step-start chain. Phase 175 must never restore the
pre-Phase173 running bytes after a Phase-173 stage outcome, must stop at one
exact PreparedStepExecutionStart or one exact stop object, and must not
persist, start, or execute the prepared running state.

Requirement-to-test mapping (Issue #363):
- public signature / exact default dependency identities / source audit
  -> test_public_signature_default_identities_and_source_audit
- prepare route exact stage order Phase173 -> Phase146, canonical argument
  identity/order, exactly once
  -> test_prepare_route_stage_order_canonical_arguments_once
- workflow_complete route exact identity
  -> test_workflow_complete_route_exact_identity
- persisted_failure route exact identity
  -> test_persisted_failure_route_exact_identity
- non-callable dependency configuration rejects before execution
  -> test_non_callable_dependency_configuration_rejects_before_execution
- Phase173 safe error identity; Phase146 zero; no outer target rollback/write
  -> test_phase173_safe_error_identity_phase146_zero_no_rollback
- Phase173 unexpected exception sanitized; Phase146 zero; no outer rollback/write
  -> test_phase173_unexpected_error_sanitized_phase146_zero_no_rollback
- malformed Phase173 return rejected as phase173_contract; Phase146 zero; no
  outer rollback/write -> test_malformed_phase173_return_phase173_contract
- Phase146 safe error identity after valid Phase173; post-Phase173 committed
  bytes retained -> test_phase146_safe_error_identity_committed_retained
- Phase146 unexpected exception after valid Phase173; mutation restored to
  committed bytes -> test_phase146_unexpected_error_restored_to_committed
- malformed Phase146 prepared output rejected/restored to committed bytes
  -> test_malformed_phase146_prepared_output_rejected_restored_to_committed
- malformed Phase146 stop output rejected/restored to committed bytes
  -> test_malformed_phase146_stop_output_rejected_restored_to_committed
- apparently valid Phase146 prepared output that mutates a target ->
  committed_mutation, restored to committed bytes
  -> test_valid_phase146_prepared_output_mutating_target_committed_mutation
- input/target-domain rejection before Phase173
  -> test_input_and_target_domain_rejection_before_phase173
- approval/employee deliberately not prevalidated: Phase173 runs first with
  the exact unvalidated values, then its safe error is re-raised while
  committed bytes remain
  -> test_approval_employee_not_prevalidated_phase173_runs_first
- rollback failure after Phase146 mutation attempts both target restorations
  once and retries neither stage
  -> test_rollback_failure_attempts_both_targets_once_without_retry
- real-default integration regression A/B/C/D
  -> test_real_default_seven_step_step6_success_prepared_step7_start,
     test_real_default_six_step_success_workflow_complete_identity,
     test_real_default_six_step_failure_persisted_failure_identity,
     test_real_default_seven_step_step6_success_missing_approval_durable_commit
"""

# ruff: noqa: E501,E701,E702,F401,I001

import ast
import importlib
import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_runtime_result_to_approved_preparation_orchestration_boundary,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase146Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError as Phase138Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase145CompatibilityError,
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
)
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError as Phase173Error,
)
from ai_office.engine.runtime_result_to_prepared_step_start_orchestration_boundary import (
    RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError,
    RuntimeResultToPreparedStepStartOrchestrationBoundaryError,
    RuntimeResultToPreparedStepStartOrchestrationBoundaryFailureDetail,
    route_runtime_result_to_prepared_step_start_orchestration_boundary,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
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
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

MODULE_NAME = "ai_office.engine.runtime_result_to_prepared_step_start_orchestration_boundary"


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


def setup(
    tmp_path: Path,
    steps: int = 7,
    current: int = 6,
) -> dict[str, object]:
    """Phase-155-provenance running state at ``current`` for the boundary.

    Canonical Phase-155 provenance: every predecessor event carries its
    valid non-empty request_id except the immediate predecessor
    (step ``current - 1``), which has ``output_text=""`` and
    ``request_id=None``.  Phase 146 stop routes accept this provenance
    after prerequisite #365.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
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


def prepare_decision(
    wf: WorkflowDefinition, index: int
) -> WorkflowProgressionDecision:
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


def complete_decision(
    wf: WorkflowDefinition, index: int
) -> WorkflowProgressionDecision:
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


def _commit_phase173(
    values: dict[str, object], result: object
) -> PreparedWorkflowStep:
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    state_path.write_bytes(b"committed-state")
    events_path.write_bytes(b"committed-events")
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    decision = prepare_decision(wf, result.step_index)  # type: ignore[attr-defined]
    employee = employee_for(decision)
    return prepared_for(wf, decision, employee)


# ---------------------------------------------------------------------------
# 1. public signature / default identities / source audit
# ---------------------------------------------------------------------------


def test_public_signature_default_identities_and_source_audit() -> None:
    parameters = list(
        inspect.signature(
            route_runtime_result_to_prepared_step_start_orchestration_boundary
        ).parameters.values()
    )
    assert [parameter.name for parameter in parameters[:6]] == [
        "result",
        "workflow",
        "approval",
        "employee",
        "state_path",
        "events_path",
    ]
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        and parameter.annotation is object
        for parameter in parameters[:6]
    )
    assert [parameter.name for parameter in parameters[6:]] == [
        "phase173_function",
        "phase146_function",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters[6:]
    )
    assert parameters[6].default is (
        route_runtime_result_to_approved_preparation_orchestration_boundary
    )
    assert parameters[7].default is (
        route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    )
    assert issubclass(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError,
        RuntimeResultToPreparedStepStartOrchestrationBoundaryError,
    )
    assert issubclass(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryError, ValueError
    )
    detail = RuntimeResultToPreparedStepStartOrchestrationBoundaryFailureDetail(
        "configuration"
    )
    assert detail.classification == "configuration"

    module = importlib.import_module(MODULE_NAME)
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    allowed = {
        "collections.abc",
        "dataclasses",
        "pathlib",
        "typing",
        "ai_office.definitions.employee",
        "ai_office.definitions.workflow",
        "ai_office.engine.next_step_preparation",
        "ai_office.engine.persisted_execution_outcome_reentry",
        "ai_office.engine.prepared_step_execution_start",
        "ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        "ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        "ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        "ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary",
        "ai_office.engine.runtime_result_to_progression_orchestration_boundary",
        "ai_office.engine.workflow_progression",
        "ai_office.invocation",
        "ai_office.runtime",
    }
    assert imported_modules <= allowed
    # No retry loop / no workflow loop: no while/async loop; each phase route
    # is called at most once.
    assert not any(
        isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree)
    )
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert calls.count("phase173_function") == 1
    assert calls.count("phase146_function") == 1
    # No lower phase boundary route / direct preparation bypass is referenced.
    for forbidden in (
        "route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        "route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        "route_runtime_result_to_progression_orchestration_boundary",
        "route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        "prepare_approved_next_workflow_step",
    ):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# 2. prepare route stage order / canonical arguments
# ---------------------------------------------------------------------------


def test_prepare_route_stage_order_canonical_arguments_once(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    decision = prepare_decision(values["workflow"], 6)  # type: ignore[arg-type]
    approval = approval_for(decision)
    employee = employee_for(decision)
    prepared = prepared_for(values["workflow"], decision, employee)  # type: ignore[arg-type]
    start = prepared_start_for(values["workflow"], prepared)  # type: ignore[arg-type]
    order: list[str] = []

    def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        order.append("phase173")
        assert r is result
        assert w is values["workflow"]
        assert a is approval
        assert m is employee
        assert s is values["state_path"]
        assert e is values["events_path"]
        return prepared

    def phase146(r: object, w: object, m: object, s: object, e: object) -> object:
        order.append("phase146")
        assert r is prepared
        assert w is values["workflow"]
        assert m is employee
        assert s is values["state_path"]
        assert e is values["events_path"]
        return start

    out = route_runtime_result_to_prepared_step_start_orchestration_boundary(
        result,
        values["workflow"],
        approval,
        employee,
        values["state_path"],
        values["events_path"],
        phase173_function=phase173,  # type: ignore[arg-type]
        phase146_function=phase146,  # type: ignore[arg-type]
    )
    assert order == ["phase173", "phase146"]
    assert out is start
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 3 / 4. stop routes exact identity
# ---------------------------------------------------------------------------


def test_workflow_complete_route_exact_identity(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=6, current=6)
    stop = complete_decision(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase173": 0, "phase146": 0}

    def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        calls["phase173"] += 1
        assert r is stop
        return stop

    def phase146(*_: object) -> object:
        calls["phase146"] += 1
        return stop

    out = route_runtime_result_to_prepared_step_start_orchestration_boundary(
        stop,
        values["workflow"],
        None,
        None,
        values["state_path"],
        values["events_path"],
        phase173_function=phase173,  # type: ignore[arg-type]
        phase146_function=phase146,  # type: ignore[arg-type]
    )
    assert out is stop
    assert calls == {"phase173": 1, "phase146": 1}
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


def test_persisted_failure_route_exact_identity(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=6, current=6)
    stop = failure_outcome(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase173": 0, "phase146": 0}

    def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        calls["phase173"] += 1
        assert r is stop
        return stop

    def phase146(*_: object) -> object:
        calls["phase146"] += 1
        return stop

    out = route_runtime_result_to_prepared_step_start_orchestration_boundary(
        stop,
        values["workflow"],
        None,
        None,
        values["state_path"],
        values["events_path"],
        phase173_function=phase173,  # type: ignore[arg-type]
        phase146_function=phase146,  # type: ignore[arg-type]
    )
    assert out is stop
    assert calls == {"phase173": 1, "phase146": 1}
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 5. non-callable dependency configuration
# ---------------------------------------------------------------------------


def test_non_callable_dependency_configuration_rejects_before_execution(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    approval = approval_for(prepare_decision(values["workflow"], 6))  # type: ignore[arg-type]
    employee = employee_for(prepare_decision(values["workflow"], 6))  # type: ignore[arg-type]

    def phase173(*_: object) -> object:
        raise AssertionError("phase173 must not be called")

    def phase146(*_: object) -> object:
        raise AssertionError("phase146 must not be called")

    for bad_phase173, bad_phase146 in ((None, phase146), (phase173, None)):
        with pytest.raises(
            RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_prepared_step_start_orchestration_boundary(
                result,
                values["workflow"],
                approval,
                employee,
                values["state_path"],
                values["events_path"],
                phase173_function=bad_phase173,  # type: ignore[arg-type]
                phase146_function=bad_phase146,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "configuration"
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 6 / 7 / 8. Phase173 stage failures: no destructive outer rollback
# ---------------------------------------------------------------------------


def test_phase173_safe_error_identity_phase146_zero_no_rollback(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    safe = Phase173Error("phase173 safe failure")
    calls: dict[str, int] = {"phase146": 0}

    def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        # Phase 173 may fail after the runtime result was durably persisted;
        # Phase 175 must re-raise by identity and must NOT roll the targets
        # back to the pre-Phase173 running state.
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"persisted-state")
        e.write_bytes(b"persisted-events")
        raise safe

    def phase146(*_: object) -> object:
        calls["phase146"] += 1
        raise AssertionError("phase146 must not be called")

    with pytest.raises(Phase173Error) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            values["workflow"],
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value is safe
    assert calls == {"phase146": 0}
    # no outer rollback/write: the Phase-173 side-effect bytes remain
    assert values["state_path"].read_bytes() == b"persisted-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"persisted-events"  # type: ignore[union-attr]


def test_phase173_unexpected_error_sanitized_phase146_zero_no_rollback(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase146": 0}

    def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"persisted-state")
        e.write_bytes(b"persisted-events")
        raise RuntimeError("secret provider payload")

    def phase146(*_: object) -> object:
        calls["phase146"] += 1
        raise AssertionError("phase146 must not be called")

    with pytest.raises(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            values["workflow"],
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "dependency_error"
    assert "secret" not in str(exc.value)
    assert calls == {"phase146": 0}
    # no outer rollback/write: the Phase-173 side-effect bytes remain
    assert values["state_path"].read_bytes() == b"persisted-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"persisted-events"  # type: ignore[union-attr]


def test_malformed_phase173_return_phase173_contract_phase146_zero(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    result = runtime_success(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    valid = prepared_for(wf, decision, employee)
    bad_prepared: list[object] = [
        "malformed",
        replace(valid, workflow_id="other"),
        replace(valid, step_id="step-6"),
        replace(valid, step_index=6),
        replace(valid, step_index=True),  # bool is not an exact int
        replace(valid, employee_id="e9"),
        replace(valid, employee_instructions="other instructions"),
        replace(valid, step_instructions="other instructions"),
        replace(valid, model="other-model"),
        replace(valid, allowed_tool_names=["tool-one", "tool-two"]),  # list, not tuple
        replace(valid, allowed_tool_names=("tool-one",)),
        replace(valid, allowed_tool_names=("tool-one", "tool-two", "tool-three")),
        complete_decision(wf, 6),  # wrong discriminator type
        failure_outcome(wf, 6),
    ]
    for bad in bad_prepared:
        calls: dict[str, int] = {"phase146": 0}

        def phase173(*_: object, bad: object = bad) -> object:
            return bad

        def phase146(*_: object) -> object:
            calls["phase146"] += 1
            raise AssertionError("phase146 must not be called")

        with pytest.raises(
            RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_prepared_step_start_orchestration_boundary(
                result,
                wf,
                approval,
                employee,
                state_path,
                events_path,
                phase173_function=phase173,  # type: ignore[arg-type]
                phase146_function=phase146,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase173_contract"
        assert calls == {"phase146": 0}
        assert state_path.read_bytes() == values["state_before"]
        assert events_path.read_bytes() == values["events_before"]
    # A Phase-173 stage that already wrote committed-like bytes before
    # returning a malformed value must leave those bytes untouched: Phase 175
    # performs no outer rollback/write for a Phase-173 stage outcome and
    # never reaches Phase 146.
    calls = {"phase146": 0}

    def phase173_committed(*_: object) -> object:
        state_path.write_bytes(b"committed-state")
        events_path.write_bytes(b"committed-events")
        return "malformed"

    def phase146_never(*_: object) -> object:
        calls["phase146"] += 1
        raise AssertionError("phase146 must not be called")

    with pytest.raises(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            phase173_function=phase173_committed,  # type: ignore[arg-type]
            phase146_function=phase146_never,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "phase173_contract"
    assert calls == {"phase146": 0}
    # the committed-like Phase-173 side-effect bytes remain, no rollback to
    # the pre-Phase173 running bytes and no outer write
    assert state_path.read_bytes() == b"committed-state"
    assert events_path.read_bytes() == b"committed-events"
    # stop inputs must come back by exact identity
    stop = complete_decision(wf, 6)
    for bad_stop in (replace(stop), "malformed"):
        def phase173_stop(*_: object, bad: object = bad_stop) -> object:
            return bad

        with pytest.raises(
            RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_prepared_step_start_orchestration_boundary(
                stop,
                wf,
                None,
                None,
                state_path,
                events_path,
                phase173_function=phase173_stop,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase173_contract"
        # no outer rollback/write: the committed-like Phase-173 side-effect
        # bytes remain untouched by the Phase-173 stage failure
        assert state_path.read_bytes() == b"committed-state"
        assert events_path.read_bytes() == b"committed-events"


# ---------------------------------------------------------------------------
# 9 / 10 / 11 / 12 / 13. Phase146 stage outcomes after the committed snapshot
# ---------------------------------------------------------------------------


def test_phase146_safe_error_identity_committed_retained(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    result = runtime_success(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    safe_errors = [
        Phase146Error("phase146 safe failure"),
        Phase138Error("phase138 safe failure"),
    ]
    for safe in safe_errors:
        def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
            return _commit_phase173(values, result)

        def phase146(*_: object) -> object:
            raise safe

        with pytest.raises((Phase146Error, Phase138Error)) as exc:
            route_runtime_result_to_prepared_step_start_orchestration_boundary(
                result,
                wf,
                approval,
                employee,
                values["state_path"],
                values["events_path"],
                phase173_function=phase173,  # type: ignore[arg-type]
                phase146_function=phase146,  # type: ignore[arg-type]
            )
        assert exc.value is safe
        # post-Phase173 committed bytes retained, never rolled back to running
        assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
        assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]
        assert values["state_path"].read_bytes() != values["state_before"]  # type: ignore[union-attr]
        assert values["events_path"].read_bytes() != values["events_before"]  # type: ignore[union-attr]


def test_phase146_unexpected_error_restored_to_committed(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    result = runtime_success(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)

    def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        return _commit_phase173(values, result)

    def phase146(r: object, w: object, m: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"mutated-state")
        e.write_bytes(b"mutated-events")
        raise RuntimeError("boom")

    with pytest.raises(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            wf,
            approval,
            employee,
            values["state_path"],
            values["events_path"],
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "dependency_error"
    # restored to the post-Phase173 committed bytes, not the running snapshot
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]
    assert values["state_path"].read_bytes() != values["state_before"]  # type: ignore[union-attr]


def test_malformed_phase146_prepared_output_rejected_restored_to_committed(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    result = runtime_success(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    prepared = prepared_for(wf, decision, employee)
    valid = prepared_start_for(wf, prepared)
    request = valid.request
    running = valid.running_state
    bad_start: list[object] = [
        "malformed",
        replace(valid, request="not-a-request"),
        replace(valid, request=replace(request, model="other-model")),
        replace(valid, request=replace(request, system_instructions="other instructions")),
        replace(valid, request=replace(request, task_instructions="other instructions")),
        replace(
            valid,
            request=replace(request, allowed_tools=["tool-one", "tool-two"]),
        ),  # list, not tuple
        replace(valid, request=replace(request, allowed_tools=("tool-one",))),
        replace(
            valid,
            request=replace(
                request, allowed_tools=("tool-one", "tool-two", "tool-three")
            ),
        ),
        replace(valid, running_state="not-a-state"),
        replace(valid, running_state=replace(running, workflow_id="other")),
        replace(valid, running_state=replace(running, status="succeeded")),
        replace(valid, running_state=replace(running, current_step_id="step-6")),
        replace(valid, running_state=replace(running, current_step_index=6)),
        replace(valid, running_state=replace(running, current_step_index=True)),
        replace(valid, running_state=replace(running, current_employee_id="e9")),
        replace(
            valid,
            running_state=replace(
                running, completed_step_ids=["step-1", "step-2", "step-3", "step-4", "step-5", "step-6"]
            ),
        ),  # list, not tuple
        replace(
            valid,
            running_state=replace(
                running, completed_step_ids=("step-1", "step-2", "step-3", "step-4", "step-5")
            ),
        ),
        replace(
            valid,
            running_state=replace(
                running, completed_step_ids=("step-1", "step-2", "step-3", "step-4", "step-5", "step-7")
            ),
        ),
        replace(valid, running_state=replace(running, last_failure_category="api_error")),
    ]
    for bad in bad_start:
        def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
            return _commit_phase173(values, result)

        def phase146(*_: object, bad: object = bad) -> object:
            return bad

        with pytest.raises(
            RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_prepared_step_start_orchestration_boundary(
                result,
                wf,
                approval,
                employee,
                state_path,
                events_path,
                phase173_function=phase173,  # type: ignore[arg-type]
                phase146_function=phase146,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase146_contract"
        assert state_path.read_bytes() == b"committed-state"
        assert events_path.read_bytes() == b"committed-events"


def test_malformed_phase146_stop_output_rejected_restored_to_committed(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    complete = complete_decision(wf, 6)
    failure = failure_outcome(wf, 6)

    def phase173_stop(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"committed-state")
        e.write_bytes(b"committed-events")
        return complete

    for bad in ("malformed", replace(complete), replace(complete, decision="prepare_next_step")):
        def phase146_bad(*_: object, bad: object = bad) -> object:
            return bad

        with pytest.raises(
            RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_prepared_step_start_orchestration_boundary(
                complete,
                wf,
                None,
                None,
                state_path,
                events_path,
                phase173_function=phase173_stop,  # type: ignore[arg-type]
                phase146_function=phase146_bad,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase146_contract"
        assert state_path.read_bytes() == b"committed-state"
        assert events_path.read_bytes() == b"committed-events"

    # persisted_failure stop route: Phase 146 must return the exact outcome
    def phase173_failure(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"committed-state")
        e.write_bytes(b"committed-events")
        return failure

    for bad in ("malformed", replace(failure), replace(failure, outcome="persisted_success")):
        def phase146_bad2(*_: object, bad: object = bad) -> object:
            return bad

        with pytest.raises(
            RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_prepared_step_start_orchestration_boundary(
                failure,
                wf,
                None,
                None,
                state_path,
                events_path,
                phase173_function=phase173_failure,  # type: ignore[arg-type]
                phase146_function=phase146_bad2,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase146_contract"
        assert state_path.read_bytes() == b"committed-state"
        assert events_path.read_bytes() == b"committed-events"


def test_valid_phase146_prepared_output_mutating_target_committed_mutation(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    result = runtime_success(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    prepared = prepared_for(wf, decision, employee)
    start = prepared_start_for(wf, prepared)

    def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        return _commit_phase173(values, result)

    def phase146(r: object, w: object, m: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"mutated-state")
        e.write_bytes(b"mutated-events")
        return start

    with pytest.raises(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "committed_mutation"
    # restored to the post-Phase173 committed bytes
    assert state_path.read_bytes() == b"committed-state"
    assert events_path.read_bytes() == b"committed-events"


# ---------------------------------------------------------------------------
# 14. input / target-domain rejection before Phase 173
# ---------------------------------------------------------------------------


def test_input_and_target_domain_rejection_before_phase173(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    result = runtime_success(wf, 6)

    def phase173(*_: object) -> object:
        raise AssertionError("phase173 must not be called")

    def phase146(*_: object) -> object:
        raise AssertionError("phase146 must not be called")

    bad_inputs: list[tuple[object, object, object, object, str]] = [
        ("not-a-result", wf, state_path, events_path, "result_type"),
        (None, wf, state_path, events_path, "result_type"),
        # stop input domain is narrowed to the discriminator before Phase
        # 173: a prepare_next_step decision and a persisted_success outcome
        # are not stop inputs and are rejected as result_type without
        # reaching Phase 173 / Phase 146.
        (prepare_decision(wf, 6), wf, state_path, events_path, "result_type"),
        (
            replace(failure_outcome(wf, 6), outcome="persisted_success"),
            wf,
            state_path,
            events_path,
            "result_type",
        ),
        (result, "not-a-workflow", state_path, events_path, "workflow_definition"),
        (result, wf, str(state_path), events_path, "state_target"),
        (result, wf, state_path, str(events_path), "event_target"),
    ]
    for bad_result, bad_workflow, bad_state, bad_events, classification in bad_inputs:
        with pytest.raises(
            RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_prepared_step_start_orchestration_boundary(
                bad_result,
                bad_workflow,
                None,
                None,
                bad_state,
                bad_events,
                phase173_function=phase173,  # type: ignore[arg-type]
                phase146_function=phase146,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == classification
    # distinct-target conflict
    with pytest.raises(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            wf,
            None,
            None,
            state_path,
            state_path,
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "target_conflict"
    # missing / non-regular targets
    missing_state = tmp_path / "missing-state"
    with pytest.raises(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            wf,
            None,
            None,
            missing_state,
            values["events_path"],
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "state_target"
    missing_events = tmp_path / "missing-events"
    with pytest.raises(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            wf,
            None,
            None,
            values["state_path"],
            missing_events,
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "event_target"
    dir_state = tmp_path / "state-dir"
    dir_state.mkdir()
    with pytest.raises(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            wf,
            None,
            None,
            dir_state,
            values["events_path"],
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "state_target"
    dir_events = tmp_path / "events-dir"
    dir_events.mkdir()
    with pytest.raises(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            wf,
            None,
            None,
            values["state_path"],
            dir_events,
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "event_target"


# ---------------------------------------------------------------------------
# 15. approval/employee are deliberately not prevalidated
# ---------------------------------------------------------------------------


def test_approval_employee_not_prevalidated_phase173_runs_first(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    safe = Phase145CompatibilityError("approval_contract")
    calls: dict[str, int] = {"phase173": 0, "phase146": 0}

    def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        calls["phase173"] += 1
        # approval/employee reach Phase 173 exactly as supplied, unvalidated
        assert a is None and m is None
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"committed-state")
        e.write_bytes(b"committed-events")
        raise safe

    def phase146(*_: object) -> object:
        calls["phase146"] += 1
        raise AssertionError("phase146 must not be called")

    # approval=None and employee=None are invalid for a prepare route, but
    # Phase 173 must run first with the exact unvalidated values; its safe
    # Phase-145 approval_contract error is re-raised by identity afterwards.
    with pytest.raises(Phase145CompatibilityError) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            values["workflow"],
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value is safe
    assert calls == {"phase173": 1, "phase146": 0}
    # the Phase-173 stage committed bytes are retained, never rolled back
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]
    # wrong-type approval/employee also reach Phase 173 unchanged
    for bad_approval, bad_employee in (("approval", None), (None, "employee")):
        def phase173_again(
            r: object,
            w: object,
            a: object,
            m: object,
            s: object,
            e: object,
            ba: object = bad_approval,
            be: object = bad_employee,
        ) -> object:
            calls["phase173"] += 1
            assert a is ba and m is be
            raise safe

        with pytest.raises(Phase145CompatibilityError):
            route_runtime_result_to_prepared_step_start_orchestration_boundary(
                result,
                values["workflow"],
                bad_approval,
                bad_employee,
                values["state_path"],
                values["events_path"],
                phase173_function=phase173_again,  # type: ignore[arg-type]
                phase146_function=phase146,  # type: ignore[arg-type]
            )
    assert calls["phase173"] == 3
    assert calls["phase146"] == 0


# ---------------------------------------------------------------------------
# 16. rollback failure after Phase146 mutation
# ---------------------------------------------------------------------------


def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    result = runtime_success(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    prepared = prepared_for(wf, decision, employee)
    start = prepared_start_for(wf, prepared)
    calls: dict[str, int] = {"phase173": 0, "phase146": 0}
    # Phase 173 already durably committed the continuation bytes; restore
    # attempts are the only writes that must be counted per target.
    state_path.write_bytes(b"committed-state")
    events_path.write_bytes(b"committed-events")
    write_attempts: list[tuple[Path, bytes]] = []
    original_write_bytes = Path.write_bytes

    def counting_write_bytes(path: Path, data: bytes) -> int:
        write_attempts.append((path, data))
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", counting_write_bytes)

    def phase173(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        calls["phase173"] += 1
        return prepared

    def phase146(r: object, w: object, m: object, s: object, e: object) -> object:
        calls["phase146"] += 1
        assert isinstance(s, Path) and isinstance(e, Path)
        # mutate both targets and make the events target unwritable
        s.write_bytes(b"mutated-state")
        e.unlink()
        e.mkdir()  # directory: write_bytes -> IsADirectoryError
        return start

    with pytest.raises(
        RuntimeResultToPreparedStepStartOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            phase173_function=phase173,  # type: ignore[arg-type]
            phase146_function=phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "rollback_failure"
    assert calls == {"phase173": 1, "phase146": 1}
    # each target's committed-bytes restore was attempted exactly once (the
    # phase146 mutation wrote mutated-state once; no retry of any restore)
    state_restores = [
        data for path, data in write_attempts
        if path == state_path and data == b"committed-state"
    ]
    events_restores = [
        data for path, data in write_attempts
        if path == events_path and data == b"committed-events"
    ]
    assert state_restores == [b"committed-state"]
    assert events_restores == [b"committed-events"]
    # the state target restoration succeeded; the events target restoration
    # failed and its directory remains
    assert state_path.read_bytes() == b"committed-state"
    assert events_path.is_dir()


# ---------------------------------------------------------------------------
# Real-default integration regression A / B / C / D
# ---------------------------------------------------------------------------


def _assert_step6_terminal_persisted(
    values: dict[str, object],
    events_before: bytes,
    *,
    success: bool,
    steps: int,
) -> None:
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert len(history.events) == 6
    final = history.state
    event = history.events[-1]
    if success:
        assert final.status == "succeeded"
        assert final.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
        assert final.last_failure_category is None
        assert event.event_type == "step_succeeded"
        assert event.next_status == "succeeded"
        assert event.provider == "openai"
        assert event.output_text == "output"
    else:
        assert final.status == "failed"
        assert final.completed_step_ids == tuple(f"step-{i}" for i in range(1, 6))
        assert final.last_failure_category == "api_error"
        assert event.event_type == "step_failed"
        assert event.next_status == "failed"
        assert event.provider == "openai"
        assert event.message == "safe failure"
    assert final.current_step_id == f"step-{min(6, steps)}"
    assert final.current_step_index == min(6, steps)
    # Phase-155 predecessor provenance unchanged (canonical after #365:
    # earlier predecessors keep valid non-empty request IDs, the immediate
    # predecessor step-5 has request_id=None)
    assert [item.output_text for item in history.events[:5]] == [
        "output-step-1", "", "", "", "",
    ]
    assert [item.request_id for item in history.events[:4]] == [
        "request-step-1",
        "request-step-2",
        "request-step-3",
        "request-step-4",
    ]
    assert history.events[4].request_id is None
    assert [item.provider for item in history.events[:5]] == ["openai"] * 5
    # exactly one step-6 terminal event was appended
    assert values["events_path"].read_bytes() != events_before  # type: ignore[union-attr]
    # final target bytes are the committed terminal bytes
    assert values["state_path"].read_bytes() == serialize_workflow_execution_state_json(  # type: ignore[union-attr]
        history.state
    ).encode("utf-8")


def test_real_default_seven_step_step6_success_prepared_step7_start(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    # exact approval bound to Phase-172's expected step-6 -> step-7 decision
    # and an exact matching EmployeeDefinition for step 7
    approval = approval_for(prepare_decision(values["workflow"], 6))  # type: ignore[arg-type]
    employee = employee_for(prepare_decision(values["workflow"], 6))  # type: ignore[arg-type]

    out = route_runtime_result_to_prepared_step_start_orchestration_boundary(
        result,
        values["workflow"],
        approval,
        employee,
        values["state_path"],
        values["events_path"],
    )
    # real Phase 173 -> real Phase 146 yield the exact prepared step-7 start
    assert type(out) is PreparedStepExecutionStart
    assert out.request.model == employee.model
    assert out.request.system_instructions == employee.instructions
    assert out.request.task_instructions == "step-7"
    assert out.request.allowed_tools == tuple(employee.allowed_tools)
    running = out.running_state
    assert running.workflow_id == "w"
    assert running.status == "running"
    assert running.current_step_id == "step-7"
    assert running.current_step_index == 7
    assert running.current_employee_id == "e7"
    assert running.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    assert running.last_failure_category is None
    # real Phase 172 durably persisted the step-6 runtime result exactly once
    # through the real lower chain; terminal state is step-6 succeeded
    _assert_step6_terminal_persisted(values, events_before, success=True, steps=7)
    # final target bytes remain the post-Phase173 committed step-6 terminal bytes
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert values["state_path"].read_bytes() == serialize_workflow_execution_state_json(  # type: ignore[union-attr]
        history.state
    ).encode("utf-8")
    assert values["state_path"].read_bytes() != state_before  # type: ignore[union-attr]
    # step 7 is not started, persisted, or executed
    assert history.state.current_step_id == "step-6"
    assert history.state.status == "succeeded"
    assert history.state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    assert len(history.events) == 6


def test_real_default_six_step_success_workflow_complete_identity(
    tmp_path: Path,
) -> None:
    # canonical Phase-155 provenance: the immediate predecessor step-5 has
    # provider="openai", output_text="", request_id=None (accepted by the
    # public Phase 146 stop route after prerequisite #365)
    values = setup(tmp_path, steps=6, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    real_phase173 = route_runtime_result_to_approved_preparation_orchestration_boundary
    captured: dict[str, object] = {}

    def counting_phase173(
        r: object, w: object, a: object, m: object, s: object, e: object
    ) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
        decision = real_phase173(r, w, a, m, s, e)
        captured["decision"] = decision
        return decision

    out = route_runtime_result_to_prepared_step_start_orchestration_boundary(
        result,
        values["workflow"],
        None,
        None,
        values["state_path"],
        values["events_path"],
        phase173_function=counting_phase173,  # type: ignore[arg-type]
    )
    assert type(out) is WorkflowProgressionDecision
    assert out is captured["decision"]  # exact identity through real Phase 146
    assert out.decision == "workflow_complete"
    assert out.reason == "last_step_succeeded"
    assert out.current_step_id == "step-6"
    assert out.current_step_index == 6
    _assert_step6_terminal_persisted(values, events_before, success=True, steps=6)


def test_real_default_six_step_failure_persisted_failure_identity(
    tmp_path: Path,
) -> None:
    # canonical Phase-155 provenance, same as the success stop route above
    values = setup(tmp_path, steps=6, current=6)
    result = runtime_failure(values["workflow"], 6)  # type: ignore[arg-type]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    real_phase173 = route_runtime_result_to_approved_preparation_orchestration_boundary
    captured: dict[str, object] = {}

    def counting_phase173(
        r: object, w: object, a: object, m: object, s: object, e: object
    ) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
        outcome = real_phase173(r, w, a, m, s, e)
        captured["outcome"] = outcome
        return outcome

    out = route_runtime_result_to_prepared_step_start_orchestration_boundary(
        result,
        values["workflow"],
        None,
        None,
        values["state_path"],
        values["events_path"],
        phase173_function=counting_phase173,  # type: ignore[arg-type]
    )
    assert type(out) is PersistedExecutionOutcome
    assert out is captured["outcome"]  # exact identity through real Phase 146
    assert out.outcome == "persisted_failure"
    assert out.failure_category == "api_error"
    assert out.current_step_id == "step-6"
    assert out.current_step_index == 6
    _assert_step6_terminal_persisted(values, events_before, success=False, steps=6)


def test_real_default_seven_step_step6_success_missing_approval_durable_commit(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    real_phase173 = route_runtime_result_to_approved_preparation_orchestration_boundary
    real_phase146 = (
        route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    )
    calls: dict[str, int] = {"phase173": 0, "phase146": 0}

    def counting_phase173(
        r: object, w: object, a: object, m: object, s: object, e: object
    ) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
        calls["phase173"] += 1
        return real_phase173(r, w, a, m, s, e)

    def counting_phase146(
        r: object, w: object, m: object, s: object, e: object
    ) -> PreparedStepExecutionStart | WorkflowProgressionDecision | PersistedExecutionOutcome:
        calls["phase146"] += 1
        return real_phase146(r, w, m, s, e)

    with pytest.raises(Phase145CompatibilityError) as exc:
        route_runtime_result_to_prepared_step_start_orchestration_boundary(
            result,
            values["workflow"],
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase173_function=counting_phase173,  # type: ignore[arg-type]
            phase146_function=counting_phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "approval_contract"
    # no retry of either stage
    assert calls == {"phase173": 1, "phase146": 0}
    # Phase 172 durably committed the step-6 success before the rejection
    _assert_step6_terminal_persisted(values, events_before, success=True, steps=7)
    # the targets remain the post-Phase173 committed step-6 terminal bytes,
    # not the original running bytes
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert values["state_path"].read_bytes() == serialize_workflow_execution_state_json(  # type: ignore[union-attr]
        history.state
    ).encode("utf-8")
    assert values["state_path"].read_bytes() != state_before  # type: ignore[union-attr]
    # step 7 was not started/persisted/executed
    assert history.state.current_step_id == "step-6"
    assert history.state.status == "succeeded"
    assert history.state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    assert len(history.events) == 6
