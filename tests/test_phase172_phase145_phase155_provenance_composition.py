"""Composition tests: real Phase 172 -> public Phase 145 -> Phase 137 -> Phase 130.

Issue #355 Phase-155 compatibility segment (Phase 145 -> Phase 137 -> Phase 130)
must let the real chain reach ``PreparedWorkflowStep`` for a seven-step workflow
whose step six succeeded with Phase-155 provenance (immediate predecessor has
``request_id=None`` and empty ``output_text``).

Requirement-to-test mapping (Issue #355 / comment 5301229404):
- Case A: seven-step step-6 success -> prepare_next_step -> real Phase 145
  -> real Phase 137 -> real Phase 130 -> exact ``PreparedWorkflowStep`` for
  step 7; step 7 not started; committed bytes unchanged during preparation
  -> test_case_a_seven_step_step6_success_reaches_prepared_workflow_step
- Case B: six-step step-6 success -> workflow_complete exact identity through
  the real Phase 145 stop route
  -> test_case_b_six_step_step6_success_workflow_complete_identity
- Case C: six-step step-6 failure -> persisted_failure exact identity through
  the real Phase 145 stop route
  -> test_case_c_six_step_step6_failure_persisted_failure_identity
- Case D: seven-step success + missing approval -> safe ``approval_contract``
  rejection before any dependency call; committed step-6 terminal bytes remain;
  step 7 not started
  -> test_case_d_missing_approval_safe_rejection_committed_bytes_remain
"""

# ruff: noqa: E501,E701,E702,F401,I001

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as public_phase145,
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


def setup(base: Path, steps: int, current: int) -> dict[str, object]:
    """Phase-155-provenance running state at ``current`` for the boundary."""
    base.mkdir(parents=True, exist_ok=True)
    state_path, events_path = base / "state", base / "events"
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
            "openai",
            "api_error",
            "safe failure",
            f"request-{step.id}",
            500,
            None,
            None,
        ),
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


def loaded_events(base: Path) -> list[RuntimeStepEvent]:
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(base / "state", base / "events")
    )
    return list(loaded.events)


def test_case_a_seven_step_step6_success_reaches_prepared_workflow_step(
    tmp_path: Path,
) -> None:
    base = tmp_path / "caseA"
    values = setup(base, steps=7, current=6)
    wf = values["workflow"]
    decision = route_runtime_result_to_progression_orchestration_boundary(
        runtime_success(wf, 6), wf, values["state_path"], values["events_path"]
    )
    assert type(decision) is WorkflowProgressionDecision
    assert decision.decision == "prepare_next_step"
    events_after_172 = loaded_events(base)
    assert events_after_172[4].request_id is None
    assert events_after_172[4].provider == "openai"
    assert events_after_172[4].output_text == ""

    approval = approval_for(decision)
    employee = employee_for(decision)
    committed_state = values["state_path"].read_bytes()
    committed_events = values["events_path"].read_bytes()
    prepared = public_phase145(
        decision,
        wf,
        approval,
        employee,
        values["state_path"],
        values["events_path"],
    )
    assert type(prepared) is PreparedWorkflowStep
    assert prepared.workflow_id == "w"
    assert prepared.step_id == "step-7"
    assert prepared.step_index == 7
    assert prepared.employee_id == "e7"
    assert prepared.employee_instructions == "employee instructions"
    assert prepared.step_instructions == "step-7"
    assert prepared.model == "model-name"
    assert prepared.allowed_tool_names == ("tool-one", "tool-two")
    # committed state and event bytes unchanged during preparation
    assert values["state_path"].read_bytes() == committed_state
    assert values["events_path"].read_bytes() == committed_events
    # step 7 not started: only the six committed step events exist
    assert len(loaded_events(base)) == 6
    # terminal state still at step 6 with the completed prefix pinned
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]
        )
    )
    assert loaded.state.current_step_id == "step-6"
    assert loaded.state.current_step_index == 6
    assert loaded.state.completed_step_ids == tuple(
        f"step-{i}" for i in range(1, 7)
    )
    # exactly one step-6 terminal event; predecessor provenance unchanged
    assert [event.step_id for event in loaded.events] == [
        f"step-{i}" for i in range(1, 7)
    ]
    assert loaded.events[-1].step_id == "step-6"
    assert loaded.events[-1].step_index == 6
    assert loaded.events[4].request_id is None
    assert loaded.events[4].provider == "openai"
    assert loaded.events[4].output_text == ""


def test_case_b_six_step_step6_success_workflow_complete_identity(
    tmp_path: Path,
) -> None:
    base = tmp_path / "caseB"
    values = setup(base, steps=6, current=6)
    wf = values["workflow"]
    decision = route_runtime_result_to_progression_orchestration_boundary(
        runtime_success(wf, 6), wf, values["state_path"], values["events_path"]
    )
    assert type(decision) is WorkflowProgressionDecision
    assert decision.decision == "workflow_complete"
    committed_state = values["state_path"].read_bytes()
    committed_events = values["events_path"].read_bytes()
    out = public_phase145(
        decision, wf, None, None, values["state_path"], values["events_path"]
    )
    assert out is decision
    # committed state and event bytes unchanged through the stop route
    assert values["state_path"].read_bytes() == committed_state
    assert values["events_path"].read_bytes() == committed_events
    # Phase-155 immediate predecessor request_id=None survives unchanged
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]
        )
    )
    assert loaded.events[4].request_id is None
    assert loaded.events[4].provider == "openai"
    assert loaded.events[4].output_text == ""


def test_case_c_six_step_step6_failure_persisted_failure_identity(
    tmp_path: Path,
) -> None:
    base = tmp_path / "caseC"
    values = setup(base, steps=6, current=6)
    wf = values["workflow"]
    outcome = route_runtime_result_to_progression_orchestration_boundary(
        runtime_failure(wf, 6), wf, values["state_path"], values["events_path"]
    )
    assert type(outcome) is PersistedExecutionOutcome
    assert outcome.outcome == "persisted_failure"
    committed_state = values["state_path"].read_bytes()
    committed_events = values["events_path"].read_bytes()
    out = public_phase145(
        outcome, wf, None, None, values["state_path"], values["events_path"]
    )
    assert out is outcome
    # committed state and event bytes unchanged through the stop route
    assert values["state_path"].read_bytes() == committed_state
    assert values["events_path"].read_bytes() == committed_events
    # Phase-155 immediate predecessor request_id=None survives unchanged
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]
        )
    )
    assert loaded.events[4].request_id is None
    assert loaded.events[4].provider == "openai"
    assert loaded.events[4].output_text == ""


def test_case_d_missing_approval_safe_rejection_committed_bytes_remain(
    tmp_path: Path,
) -> None:
    base = tmp_path / "caseD"
    values = setup(base, steps=7, current=6)
    wf = values["workflow"]
    decision = route_runtime_result_to_progression_orchestration_boundary(
        runtime_success(wf, 6), wf, values["state_path"], values["events_path"]
    )
    assert type(decision) is WorkflowProgressionDecision
    assert decision.decision == "prepare_next_step"
    committed_state = values["state_path"].read_bytes()
    committed_events = values["events_path"].read_bytes()
    with pytest.raises(Phase145Error) as caught:
        public_phase145(
            decision, wf, None, None, values["state_path"], values["events_path"]
        )
    assert caught.value.detail.classification == "approval_contract"
    # committed step-6 terminal bytes remain; step 7 not started
    assert values["state_path"].read_bytes() == committed_state
    assert values["events_path"].read_bytes() == committed_events
    assert len(loaded_events(base)) == 6
    # provenance pinned: Phase-155 immediate predecessor survives unchanged
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"], values["events_path"]
        )
    )
    assert [event.step_id for event in loaded.events] == [
        f"step-{i}" for i in range(1, 7)
    ]
    assert loaded.events[4].request_id is None
    assert loaded.events[4].provider == "openai"
    assert loaded.events[4].output_text == ""
    # step 7 not started: terminal state still at step 6 with completed prefix
    assert loaded.state.current_step_id == "step-6"
    assert loaded.state.current_step_index == 6
    assert loaded.state.completed_step_ids == tuple(
        f"step-{i}" for i in range(1, 7)
    )
