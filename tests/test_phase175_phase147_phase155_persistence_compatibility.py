"""Real composition tests for Issue #367: Phase 147 -> Phase 139 request_id seam.

The public Phase 175 -> real Phase 147 -> real Phase 139 -> real Phase 132
chain must accept the canonical Phase-155 provenance whose immediate
predecessor carries ``request_id=None`` (with ``output_text=""``) once the
prepared route is at step 7 or later.  Phase 147 prepared routes pass the
immediate predecessor through to Phase 139 exactly once; Phase 139 passes it
through to Phase 132 exactly once.  Stop routes remain identity-preserving
zero-call stops, and the missing-approval rejection stays owned by Phase 175.

Requirement-to-test mapping (Issue #367):
- real Phase 147 -> Phase 139 -> Phase 132 accepts the canonical 7-step
  immediate-None prepared start and persists the exact step-7 running state
  -> test_real_chain_accepts_canonical_step7_start_and_persists
- Phase 147 workflow_complete stop is exact identity with zero Phase 139
  calls and unchanged committed bytes
  -> test_phase147_workflow_complete_stop_identity_zero_phase139
- Phase 147 persisted_failure stop is exact identity with zero Phase 139
  calls and unchanged committed bytes
  -> test_phase147_persisted_failure_stop_identity_zero_phase139
- missing approval stays Phase-175-owned: approval_contract rejection,
  Phase 173 once / Phase 146 zero / Phase 147 zero, step-6 terminal bytes
  durably committed and unchanged
  -> test_missing_approval_stays_phase175_owned_with_durable_step6_commit
"""

# ruff: noqa: E501,E701,E702,F401,I001

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_runtime_result_to_approved_preparation_orchestration_boundary,
    route_runtime_result_to_prepared_step_start_orchestration_boundary,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase145CompatibilityError,
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
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

phase147 = route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
phase139 = route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
phase132 = route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary
phase175 = route_runtime_result_to_prepared_step_start_orchestration_boundary


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
    """Phase-155 canonical running state at ``current`` with events 1..current-1.

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


def phase175_prepare(values: dict[str, object]) -> PreparedStepExecutionStart:
    wf = values["workflow"]
    result = runtime_success(wf, 6)  # type: ignore[arg-type]
    decision = prepare_decision(wf, 6)  # type: ignore[arg-type]
    approval = approval_for(decision)
    employee = employee_for(decision)
    out = phase175(
        result,
        wf,
        approval,
        employee,
        values["state_path"],
        values["events_path"],
    )
    assert type(out) is PreparedStepExecutionStart
    return out


def phase175_stop(
    values: dict[str, object], *, success: bool
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    wf = values["workflow"]
    result = runtime_success(wf, 6) if success else runtime_failure(wf, 6)  # type: ignore[arg-type]
    out = phase175(
        result,
        wf,
        None,
        None,
        values["state_path"],
        values["events_path"],
    )
    return out


def test_real_chain_accepts_canonical_step7_start_and_persists(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    start = phase175_prepare(values)  # exact PreparedStepExecutionStart(step 7)
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    assert type(start) is PreparedStepExecutionStart
    wf = values["workflow"]
    employee = employee_for(prepare_decision(wf, 6))  # type: ignore[arg-type]
    calls: dict[str, int] = {"phase139": 0, "phase132": 0}

    def counting139(*_: object) -> object:
        calls["phase139"] += 1
        return phase139(
            start,
            wf,
            employee,
            values["state_path"],
            values["events_path"],
            phase132_function=counting132,  # type: ignore[arg-type]
        )

    def counting132(*_: object) -> object:
        calls["phase132"] += 1
        return phase132(
            start, wf, employee, values["state_path"], values["events_path"]
        )

    out = phase147(
        start,
        wf,
        employee,
        values["state_path"],
        values["events_path"],
        phase139_function=counting139,  # type: ignore[arg-type]
    )
    assert type(out) is RunningStatePersistenceResult
    expected = serialize_workflow_execution_state_json(start.running_state).encode("utf-8")
    assert values["state_path"].read_bytes() == expected  # type: ignore[union-attr]
    assert values["events_path"].read_bytes() == events_before  # type: ignore[union-attr]
    assert values["state_path"].read_bytes() != state_before  # type: ignore[union-attr]
    assert calls == {"phase139": 1, "phase132": 1}
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert history.state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    assert len(history.events) == 6


def test_phase147_workflow_complete_stop_identity_zero_phase139(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=6, current=6)
    stop = phase175_stop(values, success=True)
    wf = values["workflow"]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls: dict[str, int] = {"phase139": 0}

    def counting139(*_: object) -> object:
        calls["phase139"] += 1
        return stop

    out = phase147(
        stop,
        wf,
        None,
        values["state_path"],
        values["events_path"],
        phase139_function=counting139,  # type: ignore[arg-type]
    )
    assert out is stop
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "workflow_complete"
    assert out.reason == "last_step_succeeded"
    assert calls["phase139"] == 0
    assert values["events_path"].read_bytes() == events_before  # type: ignore[union-attr]


def test_phase147_persisted_failure_stop_identity_zero_phase139(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=6, current=6)
    stop = phase175_stop(values, success=False)
    wf = values["workflow"]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    calls: dict[str, int] = {"phase139": 0}

    def counting139(*_: object) -> object:
        calls["phase139"] += 1
        return stop

    out = phase147(
        stop,
        wf,
        None,
        values["state_path"],
        values["events_path"],
        phase139_function=counting139,  # type: ignore[arg-type]
    )
    assert out is stop
    assert type(out) is PersistedExecutionOutcome
    assert out.outcome == "persisted_failure"
    assert out.failure_category == "api_error"
    assert calls["phase139"] == 0
    assert values["events_path"].read_bytes() == events_before  # type: ignore[union-attr]


def test_missing_approval_stays_phase175_owned_with_durable_step6_commit(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]
    result = runtime_success(wf, 6)  # type: ignore[arg-type]
    events_before = values["events_path"].read_bytes()  # type: ignore[union-attr]
    state_before = values["state_path"].read_bytes()  # type: ignore[union-attr]
    calls: dict[str, int] = {"phase173": 0, "phase146": 0, "phase147": 0}
    real_phase173 = route_runtime_result_to_approved_preparation_orchestration_boundary
    real_phase146 = route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary

    def counting147(*_: object) -> object:
        calls["phase147"] += 1
        return None

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
        phase175(
            result,
            wf,
            None,
            None,
            values["state_path"],
            values["events_path"],
            phase173_function=counting_phase173,  # type: ignore[arg-type]
            phase146_function=counting_phase146,  # type: ignore[arg-type]
        )
    assert exc.value.detail.classification == "approval_contract"
    # Phase 175 owns the missing-approval rejection: Phase 173 once, and the
    # prepared-start chain (Phase 146, and therefore Phase 147) is never
    # entered; the counting Phase-147 wrapper is intentionally not wired into
    # Phase 175, whose signature accepts no phase147_function.
    assert calls["phase173"] == 1
    assert calls["phase146"] == 0
    assert calls["phase147"] == 0
    assert counting147 is not None
    # Phase 172 durably committed the step-6 success before the rejection
    assert values["events_path"].read_bytes() != events_before  # type: ignore[union-attr]
    assert values["state_path"].read_bytes() != state_before  # type: ignore[union-attr]
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]  # type: ignore[arg-type]
        )
    )
    assert len(history.events) == 6
    assert history.state.current_step_id == "step-6"
    assert history.state.status == "succeeded"
    assert history.state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    # final target bytes remain the post-Phase173 committed step-6 terminal bytes
    assert values["state_path"].read_bytes() == serialize_workflow_execution_state_json(  # type: ignore[union-attr]
        history.state
    ).encode("utf-8")
    # step 7 was not started/persisted/executed
    assert history.state.current_step_id == "step-6"
