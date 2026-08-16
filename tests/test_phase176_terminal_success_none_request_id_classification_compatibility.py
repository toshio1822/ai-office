"""Issue #373 regression: persisted terminal-success request_id=None (Phase 176).

Real Phase 176 with its full default dependency chain (default Phase175 -> 147
bindings) and a step-6 runtime success whose model invocation carries
``request_id=None`` (provider "openai") must complete end-to-end through the
real persistence chain. The persisted succeeded terminal keeps request_id=None
and provider "openai", and the classification chain (Phase 86 -> 79 -> 72)
accepts it only for the exact relaxed gate: state.status == "succeeded",
built-in int current_step_index >= 6, request_id is None, provider "openai".

Coverage (exactly two collected tests):
- seven-step: step-6 success request_id=None -> exact step-7 running state
  persistence (RunningStatePersistenceResult), terminal request_id=None kept
- six-step: final step-6 success request_id=None -> exact workflow_complete
  stop, terminal request_id=None kept
"""

# ruff: noqa: E501,E701,E702,F401,I001

import json
from dataclasses import replace
from pathlib import Path

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    WorkflowProgressionDecision,
    route_runtime_result_to_prepared_start_persistence_orchestration_boundary,
)
from ai_office.engine.next_step_preparation import NextStepPreparationApproval
from ai_office.invocation import ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
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
    """Canonical running state at ``current`` with events 1..current-1."""
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
        "state_before": state_path.read_bytes(),
        "events_before": events_path.read_bytes(),
    }


def runtime_success_none(wf: WorkflowDefinition, index: int) -> StepRuntimeExecutionSuccess:
    step = wf.steps[index - 1]
    return StepRuntimeExecutionSuccess(
        "w",
        step.id,
        index,
        step.employee,
        ModelInvocationSuccess(
            "openai",
            f"response-{step.id}",
            None,  # exact terminal-success None request ID
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


def load_events(events_path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_issue373_seven_step_step6_success_none_request_id_exact_step7_running_state(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=7, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success_none(wf, 6)
    decision = prepare_decision(wf, 6)
    approval = approval_for(decision)
    employee = employee_for(decision)
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    # Full default dependency chain: Phase 176 runs with its real default
    # Phase175 -> Phase147 bindings; no dependency is injected or captured.
    out = phase176(result, wf, approval, employee, state_path, events_path)
    assert type(out) is RunningStatePersistenceResult
    assert type(out.state_bytes_written) is int and out.state_bytes_written > 0
    final_state = load_workflow_execution_state(state_path)
    assert final_state.status == "running"
    assert final_state.current_step_id == "step-7"
    assert final_state.current_step_index == 7
    assert final_state.current_employee_id == "e7"
    assert final_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 7))
    assert final_state.last_failure_category is None
    assert out.state_bytes_written == len(state_path.read_bytes())
    events = load_events(events_path)
    assert [event["step_id"] for event in events] == [f"step-{i}" for i in range(1, 7)]
    assert all(event["event_type"] == "step_succeeded" for event in events)
    step6 = events[5]
    assert step6["request_id"] is None and step6["provider"] == "openai"
    step5 = events[4]
    assert step5["request_id"] is None and step5["provider"] == "openai"


def test_issue373_six_step_step6_success_none_request_id_exact_workflow_complete_stop(
    tmp_path: Path,
) -> None:
    values = canonical_running_setup(tmp_path, steps=6, current=6)
    wf = values["workflow"]  # type: ignore[arg-type]
    result = runtime_success_none(wf, 6)
    state_path = values["state_path"]  # type: ignore[arg-type]
    events_path = values["events_path"]  # type: ignore[arg-type]
    out = phase176(result, wf, None, None, state_path, events_path)
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
    events = load_events(events_path)
    assert len(events) == 6
    assert events[-1]["event_type"] == "step_succeeded"
    assert events[-1]["step_id"] == "step-6"
    assert events[-1]["request_id"] is None and events[-1]["provider"] == "openai"
