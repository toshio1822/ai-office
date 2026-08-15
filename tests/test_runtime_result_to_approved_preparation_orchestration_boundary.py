"""Focused Phase 173 post-runtime → approved-preparation orchestration tests.

Phase 173 is the next explicit integration/orchestration boundary after Phase
172: it accepts one exact Phase-155 runtime/stop result and composes public
Phase 172 then public Phase 145 in exactly that order. Phase 172 owns the
durable runtime-result persistence/classification/progression chain; Phase 145
owns explicit approval/employee validation and approved next-step preparation.
Phase 173 must never restore the pre-Phase172 running bytes after a Phase-172
stage outcome, must stop at one exact PreparedWorkflowStep or one exact stop
object, and must not start, persist, or execute the prepared next step.

Requirement-to-test mapping (Issue #353):
- public signature / exact default dependency identities / source audit
  -> test_public_signature_default_identities_and_source_audit
- prepare route exact stage order Phase172 -> Phase145, canonical argument
  identity/order, exactly once
  -> test_prepare_route_stage_order_canonical_arguments_once
- workflow_complete route exact identity
  -> test_workflow_complete_route_exact_identity
- persisted_failure route exact identity
  -> test_persisted_failure_route_exact_identity
- non-callable dependency configuration rejects before execution
  -> test_non_callable_dependency_configuration_rejects_before_execution
- Phase172 safe error identity; Phase145 zero; no outer target rollback/write
  -> test_phase172_safe_error_identity_phase145_zero_no_rollback
- Phase172 unexpected exception sanitized; Phase145 zero; no outer rollback/write
  -> test_phase172_unexpected_error_sanitized_phase145_zero_no_rollback
- malformed Phase172 return rejected as phase172_contract; Phase145 zero; no
  outer rollback/write -> test_malformed_phase172_return_phase172_contract
- Phase145 safe error identity after valid Phase172; post-Phase172 committed
  bytes retained -> test_phase145_safe_error_identity_committed_retained
- Phase145 unexpected exception after valid Phase172; mutation restored to
  committed bytes -> test_phase145_unexpected_error_restored_to_committed
- malformed Phase145 prepare output rejected/restored to committed bytes
  -> test_malformed_phase145_prepare_output_rejected_restored_to_committed
- malformed Phase145 stop output rejected/restored to committed bytes
  -> test_malformed_phase145_stop_output_rejected_restored_to_committed
- apparently valid Phase145 prepare output that mutates a target ->
  committed_mutation, restored to committed bytes
  -> test_valid_phase145_prepare_output_mutating_target_committed_mutation
- input/target-domain rejection before Phase172
  -> test_input_and_target_domain_rejection_before_phase172
- approval/employee deliberately not prevalidated: Phase172 runs first, then
  Phase145 rejects invalid approval/employee while committed bytes remain
  -> test_approval_employee_not_prevalidated_phase172_runs_first
- rollback failure after Phase145 mutation attempts both target restorations
  once and retries neither stage
  -> test_rollback_failure_attempts_both_targets_once_without_retry
- real-default integration regression A/B/C/D
  -> test_real_default_seven_step_step6_success_approved_step7_preparation,
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
    WorkflowProgressionDecision,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_runtime_result_to_approved_preparation_orchestration_boundary,
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase145CompatibilityError,
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
)
from ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary import (
    RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError,
    RuntimeResultToApprovedPreparationOrchestrationBoundaryError,
    RuntimeResultToApprovedPreparationOrchestrationBoundaryFailureDetail,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
)
from ai_office.invocation import (
    ModelInvocationFailure,
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

MODULE_NAME = "ai_office.engine.runtime_result_to_approved_preparation_orchestration_boundary"


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


def setup(tmp_path: Path, steps: int = 7, current: int = 6) -> dict[str, object]:
    """Phase-155-provenance running state at ``current`` for the boundary."""
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


def _commit_phase172(
    values: dict[str, object], result: object
) -> WorkflowProgressionDecision:
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    state_path.write_bytes(b"committed-state")
    events_path.write_bytes(b"committed-events")
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    return prepare_decision(wf, result.step_index)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 1. public signature / default identities / source audit
# ---------------------------------------------------------------------------


def test_public_signature_default_identities_and_source_audit() -> None:
    parameters = list(
        inspect.signature(
            route_runtime_result_to_approved_preparation_orchestration_boundary
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
        "phase172_function",
        "phase145_function",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters[6:]
    )
    assert parameters[6].default is (
        route_runtime_result_to_progression_orchestration_boundary
    )
    assert parameters[7].default is (
        route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    )
    assert issubclass(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError,
        RuntimeResultToApprovedPreparationOrchestrationBoundaryError,
    )
    assert issubclass(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryError, ValueError
    )

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
        "ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        "ai_office.engine.runtime_result_to_progression_orchestration_boundary",
        "ai_office.engine.workflow_progression",
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
    assert calls.count("phase172_function") == 1
    assert calls.count("phase145_function") == 1
    # No lower phase boundary route / direct preparation bypass is referenced.
    for forbidden in (
        "route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary",
        "route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        "route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
        "route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary",
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
    expected = prepared_for(values["workflow"], decision, employee)  # type: ignore[arg-type]
    order: list[str] = []

    def phase172(r: object, w: object, s: object, e: object) -> object:
        order.append("phase172")
        assert r is result
        assert w is values["workflow"]
        assert s is values["state_path"]
        assert e is values["events_path"]
        return decision

    def phase145(
        r: object, w: object, a: object, m: object, s: object, e: object
    ) -> object:
        order.append("phase145")
        assert r is decision
        assert w is values["workflow"]
        assert a is approval
        assert m is employee
        assert s is values["state_path"]
        assert e is values["events_path"]
        return expected

    out = route_runtime_result_to_approved_preparation_orchestration_boundary(
        result,
        values["workflow"],
        approval,
        employee,
        values["state_path"],
        values["events_path"],
        phase172_function=phase172,  # type: ignore[arg-type]
        phase145_function=phase145,  # type: ignore[arg-type]
    )
    assert order == ["phase172", "phase145"]
    assert out is expected
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 3 / 4. stop routes exact identity
# ---------------------------------------------------------------------------


def test_workflow_complete_route_exact_identity(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=6, current=6)
    stop = complete_decision(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase172": 0, "phase145": 0}

    def phase172(r: object, w: object, s: object, e: object) -> object:
        calls["phase172"] += 1
        assert r is stop
        return stop

    def phase145(*_: object) -> object:
        calls["phase145"] += 1
        return stop

    out = route_runtime_result_to_approved_preparation_orchestration_boundary(
        stop,
        values["workflow"],
        None,
        None,
        values["state_path"],
        values["events_path"],
        phase172_function=phase172,  # type: ignore[arg-type]
        phase145_function=phase145,  # type: ignore[arg-type]
    )
    assert out is stop
    assert calls == {"phase172": 1, "phase145": 1}
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


def test_persisted_failure_route_exact_identity(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=6, current=6)
    stop = failure_outcome(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase172": 0, "phase145": 0}

    def phase172(r: object, w: object, s: object, e: object) -> object:
        calls["phase172"] += 1
        assert r is stop
        return stop

    def phase145(*_: object) -> object:
        calls["phase145"] += 1
        return stop

    out = route_runtime_result_to_approved_preparation_orchestration_boundary(
        stop,
        values["workflow"],
        None,
        None,
        values["state_path"],
        values["events_path"],
        phase172_function=phase172,  # type: ignore[arg-type]
        phase145_function=phase145,  # type: ignore[arg-type]
    )
    assert out is stop
    assert calls == {"phase172": 1, "phase145": 1}
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

    def phase172(*_: object) -> object:
        raise AssertionError("phase172 must not be called")

    def phase145(*_: object) -> object:
        raise AssertionError("phase145 must not be called")

    for bad_phase172, bad_phase145 in ((None, phase145), (phase172, None)):
        with pytest.raises(
            RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_approved_preparation_orchestration_boundary(
                result,
                values["workflow"],
                approval,
                employee,
                values["state_path"],
                values["events_path"],
                phase172_function=bad_phase172,  # type: ignore[arg-type]
                phase145_function=bad_phase145,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "configuration"
    assert values["state_path"].read_bytes() == values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == values["events_before"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 6 / 7 / 8. Phase172 stage failures: no destructive outer rollback
# ---------------------------------------------------------------------------


def test_phase172_safe_error_identity_phase145_zero_no_rollback(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    safe = Phase172Error("phase172 safe failure")
    calls: dict[str, int] = {"phase145": 0}

    def phase172(r: object, w: object, s: object, e: object) -> object:
        # Phase 172 may fail after the runtime result was durably persisted;
        # Phase 173 must re-raise by identity and must NOT roll the targets
        # back to the pre-Phase172 running state.
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"persisted-state")
        e.write_bytes(b"persisted-events")
        raise safe

    def phase145(*_: object) -> object:
        calls["phase145"] += 1
        raise AssertionError("phase145 must not be called")

    with pytest.raises(Phase172Error) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            values["workflow"],
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value is safe
    assert calls == {"phase145": 0}
    # no outer rollback/write: the Phase-172 side-effect bytes remain
    assert values["state_path"].read_bytes() == b"persisted-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"persisted-events"  # type: ignore[union-attr]


def test_phase172_unexpected_error_sanitized_phase145_zero_no_rollback(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase145": 0}

    def phase172(r: object, w: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"persisted-state")
        e.write_bytes(b"persisted-events")
        raise RuntimeError("secret provider payload")

    def phase145(*_: object) -> object:
        calls["phase145"] += 1
        raise AssertionError("phase145 must not be called")

    with pytest.raises(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            values["workflow"],
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "dependency_error"
    assert "secret" not in str(exc.value)
    assert calls == {"phase145": 0}
    # no outer rollback/write: the Phase-172 side-effect bytes remain
    assert values["state_path"].read_bytes() == b"persisted-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"persisted-events"  # type: ignore[union-attr]


def test_malformed_phase172_return_phase172_contract_phase145_zero(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    result = runtime_success(wf, 6)
    valid = prepare_decision(wf, 6)
    stop = complete_decision(wf, 6)
    bad_runtime: list[object] = [
        "malformed",
        replace(valid, decision="workflow_complete"),  # wrong discriminator
        replace(valid, workflow_id="other"),
        replace(valid, current_step_id="step-5"),
        replace(valid, current_step_index=5),
        replace(valid, current_step_index=True),  # bool is not an exact int
        replace(valid, current_employee_id="e9"),
        replace(valid, next_step_id="step-6"),
        replace(valid, next_step_index=6),
        replace(valid, next_step_index=True),  # bool is not an exact int
        replace(valid, next_employee_id="e6"),
        replace(valid, reason="last_step_succeeded"),
    ]
    for bad in bad_runtime:
        calls: dict[str, int] = {"phase145": 0}

        def phase172(*_: object, bad: object = bad) -> object:
            return bad

        def phase145(*_: object) -> object:
            calls["phase145"] += 1
            raise AssertionError("phase145 must not be called")

        with pytest.raises(
            RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_approved_preparation_orchestration_boundary(
                result,
                wf,
                None,
                None,
                state_path,
                events_path,
                phase172_function=phase172,  # type: ignore[arg-type]
                phase145_function=phase145,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase172_contract"
        assert calls == {"phase145": 0}
        assert state_path.read_bytes() == values["state_before"]
        assert events_path.read_bytes() == values["events_before"]
    # stop inputs must come back by exact identity
    for bad_stop in (replace(stop), replace(stop, decision="prepare_next_step")):
        def phase172_stop(*_: object, bad: object = bad_stop) -> object:
            return bad

        with pytest.raises(
            RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_approved_preparation_orchestration_boundary(
                stop,
                wf,
                None,
                None,
                state_path,
                events_path,
                phase172_function=phase172_stop,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase172_contract"
        assert state_path.read_bytes() == values["state_before"]
        assert events_path.read_bytes() == values["events_before"]


# ---------------------------------------------------------------------------
# 9 / 10 / 11 / 12 / 13. Phase145 stage outcomes after the committed snapshot
# ---------------------------------------------------------------------------


def test_phase145_safe_error_identity_committed_retained(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    safe = Phase145Error("phase145 safe failure")

    def phase172(r: object, w: object, s: object, e: object) -> object:
        return _commit_phase172(values, result)

    def phase145(*_: object) -> object:
        raise safe

    with pytest.raises(Phase145Error) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            values["workflow"],
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value is safe
    # post-Phase172 committed bytes retained, never rolled back to running
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]
    assert values["state_path"].read_bytes() != values["state_before"]  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() != values["events_before"]  # type: ignore[union-attr]


def test_phase145_unexpected_error_restored_to_committed(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]

    def phase172(r: object, w: object, s: object, e: object) -> object:
        return _commit_phase172(values, result)

    def phase145(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"mutated-state")
        e.write_bytes(b"mutated-events")
        raise RuntimeError("boom")

    with pytest.raises(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            values["workflow"],
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "dependency_error"
    # restored to the post-Phase172 committed bytes, not the running snapshot
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]
    assert values["state_path"].read_bytes() != values["state_before"]  # type: ignore[union-attr]


def test_malformed_phase145_prepare_output_rejected_restored_to_committed(
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
    ]
    for bad in bad_prepared:
        def phase172(r: object, w: object, s: object, e: object) -> object:
            return _commit_phase172(values, result)

        def phase145(*_: object, bad: object = bad) -> object:
            return bad

        with pytest.raises(
            RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_approved_preparation_orchestration_boundary(
                result,
                wf,
                approval,
                employee,
                state_path,
                events_path,
                phase172_function=phase172,  # type: ignore[arg-type]
                phase145_function=phase145,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase145_contract"
        assert state_path.read_bytes() == b"committed-state"
        assert events_path.read_bytes() == b"committed-events"


def test_malformed_phase145_stop_output_rejected_restored_to_committed(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    result = runtime_success(wf, 6)
    complete = complete_decision(wf, 6)
    failure = failure_outcome(wf, 6)

    def phase172(r: object, w: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"committed-state")
        e.write_bytes(b"committed-events")
        return complete

    for bad in ("malformed", replace(complete), replace(complete, decision="prepare_next_step")):
        def phase145_bad(*_: object, bad: object = bad) -> object:
            return bad

        with pytest.raises(
            RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_approved_preparation_orchestration_boundary(
                result,
                wf,
                None,
                None,
                state_path,
                events_path,
                phase172_function=phase172,  # type: ignore[arg-type]
                phase145_function=phase145_bad,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase145_contract"
        assert state_path.read_bytes() == b"committed-state"
        assert events_path.read_bytes() == b"committed-events"

    # persisted_failure stop route: Phase 145 must return the exact outcome
    def phase172_failure(r: object, w: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"committed-state")
        e.write_bytes(b"committed-events")
        return failure

    for bad in ("malformed", replace(failure), replace(failure, outcome="persisted_success")):
        def phase145_bad2(*_: object, bad: object = bad) -> object:
            return bad

        with pytest.raises(
            RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_approved_preparation_orchestration_boundary(
                failure,
                wf,
                None,
                None,
                state_path,
                events_path,
                phase172_function=phase172_failure,  # type: ignore[arg-type]
                phase145_function=phase145_bad2,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == "phase145_contract"
        assert state_path.read_bytes() == b"committed-state"
        assert events_path.read_bytes() == b"committed-events"


def test_valid_phase145_prepare_output_mutating_target_committed_mutation(
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
    expected = prepared_for(wf, decision, employee)

    def phase172(r: object, w: object, s: object, e: object) -> object:
        return _commit_phase172(values, result)

    def phase145(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"mutated-state")
        e.write_bytes(b"mutated-events")
        return expected

    with pytest.raises(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "committed_mutation"
    # restored to the post-Phase172 committed bytes
    assert state_path.read_bytes() == b"committed-state"
    assert events_path.read_bytes() == b"committed-events"


# ---------------------------------------------------------------------------
# 14. input / target-domain rejection before Phase 172
# ---------------------------------------------------------------------------


def test_input_and_target_domain_rejection_before_phase172(tmp_path: Path) -> None:
    values = setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    state_path = values["state_path"]
    events_path = values["events_path"]
    assert isinstance(state_path, Path) and isinstance(events_path, Path)
    result = runtime_success(wf, 6)

    def phase172(*_: object) -> object:
        raise AssertionError("phase172 must not be called")

    def phase145(*_: object) -> object:
        raise AssertionError("phase145 must not be called")

    bad_inputs: list[tuple[object, object, object, object, str]] = [
        ("not-a-result", wf, state_path, events_path, "result_type"),
        (None, wf, state_path, events_path, "result_type"),
        (result, "not-a-workflow", state_path, events_path, "workflow_definition"),
        (result, wf, str(state_path), events_path, "state_target"),
        (result, wf, state_path, str(events_path), "event_target"),
    ]
    for bad_result, bad_workflow, bad_state, bad_events, classification in bad_inputs:
        with pytest.raises(
            RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
        ) as exc:
            route_runtime_result_to_approved_preparation_orchestration_boundary(
                bad_result,
                bad_workflow,
                None,
                None,
                bad_state,
                bad_events,
                phase172_function=phase172,  # type: ignore[arg-type]
                phase145_function=phase145,  # type: ignore[arg-type]
            )
        assert exc.value.detail.classification == classification
    # distinct-target conflict
    with pytest.raises(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            wf,
            None,
            None,
            state_path,
            state_path,
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "target_conflict"
    # missing / non-regular targets
    missing_state = tmp_path / "missing-state"
    with pytest.raises(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            wf,
            None,
            None,
            missing_state,
            values["events_path"],
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "state_target"
    missing_events = tmp_path / "missing-events"
    with pytest.raises(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            wf,
            None,
            None,
            values["state_path"],
            missing_events,
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "event_target"
    dir_state = tmp_path / "state-dir"
    dir_state.mkdir()
    with pytest.raises(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            wf,
            None,
            None,
            dir_state,
            values["events_path"],
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "state_target"
    dir_events = tmp_path / "events-dir"
    dir_events.mkdir()
    with pytest.raises(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            wf,
            None,
            None,
            values["state_path"],
            dir_events,
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "event_target"


# ---------------------------------------------------------------------------
# 15. approval/employee are deliberately not prevalidated
# ---------------------------------------------------------------------------


def test_approval_employee_not_prevalidated_phase172_runs_first(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    safe = Phase145CompatibilityError("approval_contract")
    calls: dict[str, int] = {"phase172": 0, "phase145": 0}

    def phase172(r: object, w: object, s: object, e: object) -> object:
        calls["phase172"] += 1
        assert isinstance(s, Path) and isinstance(e, Path)
        s.write_bytes(b"committed-state")
        e.write_bytes(b"committed-events")
        return _commit_phase172(values, result)

    def phase145(*_: object) -> object:
        calls["phase145"] += 1
        raise safe

    # approval=None and employee=None are invalid for a prepare route, but
    # Phase 172 must run first; Phase 145 rejects afterwards.
    with pytest.raises(Phase145CompatibilityError) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            values["workflow"],
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value is safe
    assert calls == {"phase172": 1, "phase145": 1}
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == b"committed-events"  # type: ignore[union-attr]
    # wrong-type approval/employee also reach Phase 145 unchanged
    for bad_approval, bad_employee in (("approval", None), (None, "employee")):
        def phase172_again(r: object, w: object, s: object, e: object) -> object:
            calls["phase172"] += 1
            return _commit_phase172(values, result)

        with pytest.raises(Phase145CompatibilityError):
            route_runtime_result_to_approved_preparation_orchestration_boundary(
                result,
                values["workflow"],
                bad_approval,
                bad_employee,
                values["state_path"],
                values["events_path"],
                phase172_function=phase172_again,  # type: ignore[arg-type]
                phase145_function=phase145,  # type: ignore[arg-type]
            )
    assert calls["phase172"] == 3
    assert calls["phase145"] == 3


# ---------------------------------------------------------------------------
# 16. rollback failure after Phase145 mutation
# ---------------------------------------------------------------------------


def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    result = runtime_success(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    expected = prepared_for(wf, decision, employee)
    calls: dict[str, int] = {"phase172": 0, "phase145": 0}

    def phase172(r: object, w: object, s: object, e: object) -> object:
        calls["phase172"] += 1
        return _commit_phase172(values, result)

    def phase145(r: object, w: object, a: object, m: object, s: object, e: object) -> object:
        calls["phase145"] += 1
        assert isinstance(s, Path) and isinstance(e, Path)
        # mutate both targets and make the events target unwritable
        s.write_bytes(b"mutated-state")
        e.unlink()
        e.mkdir()  # directory: write_bytes -> IsADirectoryError
        return expected

    with pytest.raises(
        RuntimeResultToApprovedPreparationOrchestrationBoundaryCompatibilityError
    ) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            wf,
            approval,
            employee,
            values["state_path"],
            values["events_path"],
            phase172_function=phase172,  # type: ignore[arg-type]
            phase145_function=phase145,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "rollback_failure"
    assert calls == {"phase172": 1, "phase145": 1}
    # the state target restoration was attempted (succeeded); the events
    # target restoration was attempted and failed (directory remains)
    assert values["state_path"].read_bytes() == b"committed-state"  # type: ignore[union-attr]
    assert values["events_path"].is_dir()  # type: ignore[union-attr]


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
    # Phase-155 predecessor provenance unchanged
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


def test_real_default_seven_step_step6_success_approved_step7_preparation(
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

    out = route_runtime_result_to_approved_preparation_orchestration_boundary(
        result,
        values["workflow"],
        approval,
        employee,
        values["state_path"],
        values["events_path"],
    )
    # real Phase 145 prepares the exact PreparedWorkflowStep for step 7
    assert type(out) is PreparedWorkflowStep
    assert out.workflow_id == "w"
    assert out.step_id == "step-7"
    assert out.step_index == 7
    assert out.employee_id == "e7"
    assert out.employee_instructions == employee.instructions
    assert out.step_instructions == "step-7"
    assert out.model == employee.model
    assert out.allowed_tool_names == tuple(employee.allowed_tools)
    # real Phase 172 durably persisted the step-6 runtime result exactly once
    # through the real lower chain; terminal state is step-6 succeeded
    _assert_step6_terminal_persisted(values, events_before, success=True, steps=7)
    # final target bytes remain the post-Phase172 committed step-6 terminal bytes
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
    values = setup(tmp_path, steps=6, current=6)
    result = runtime_success(values["workflow"], 6)  # type: ignore[arg-type]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    real_phase172 = route_runtime_result_to_progression_orchestration_boundary
    captured: dict[str, object] = {}

    def counting_phase172(
        r: object, w: object, s: object, e: object
    ) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
        decision = real_phase172(r, w, s, e)
        captured["decision"] = decision
        return decision

    out = route_runtime_result_to_approved_preparation_orchestration_boundary(
        result,
        values["workflow"],
        None,
        None,
        values["state_path"],
        values["events_path"],
        phase172_function=counting_phase172,  # type: ignore[arg-type]
    )
    assert type(out) is WorkflowProgressionDecision
    assert out is captured["decision"]  # exact identity through Phase 145
    assert out.decision == "workflow_complete"
    assert out.reason == "last_step_succeeded"
    assert out.current_step_id == "step-6"
    assert out.current_step_index == 6
    _assert_step6_terminal_persisted(values, events_before, success=True, steps=6)


def test_real_default_six_step_failure_persisted_failure_identity(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, steps=6, current=6)
    result = runtime_failure(values["workflow"], 6)  # type: ignore[arg-type]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    real_phase172 = route_runtime_result_to_progression_orchestration_boundary
    captured: dict[str, object] = {}

    def counting_phase172(
        r: object, w: object, s: object, e: object
    ) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
        outcome = real_phase172(r, w, s, e)
        captured["outcome"] = outcome
        return outcome

    out = route_runtime_result_to_approved_preparation_orchestration_boundary(
        result,
        values["workflow"],
        None,
        None,
        values["state_path"],
        values["events_path"],
        phase172_function=counting_phase172,  # type: ignore[arg-type]
    )
    assert type(out) is PersistedExecutionOutcome
    assert out is captured["outcome"]  # exact identity through Phase 145
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
    real_phase172 = route_runtime_result_to_progression_orchestration_boundary
    real_phase145 = (
        route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    )
    calls: dict[str, int] = {"phase172": 0, "phase145": 0}

    def counting_phase172(
        r: object, w: object, s: object, e: object
    ) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
        calls["phase172"] += 1
        return real_phase172(r, w, s, e)

    def counting_phase145(
        r: object, w: object, a: object, m: object, s: object, e: object
    ) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
        calls["phase145"] += 1
        return real_phase145(r, w, a, m, s, e)

    with pytest.raises(Phase145CompatibilityError) as exc:
        route_runtime_result_to_approved_preparation_orchestration_boundary(
            result,
            values["workflow"],
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase172_function=counting_phase172,  # type: ignore[arg-type]
            phase145_function=counting_phase145,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "approval_contract"
    # no retry of either stage
    assert calls == {"phase172": 1, "phase145": 1}
    # Phase 172 durably committed the step-6 success before the rejection
    _assert_step6_terminal_persisted(values, events_before, success=True, steps=7)
    # the targets remain the post-Phase172 committed step-6 terminal bytes,
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
