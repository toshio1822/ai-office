"""Focused Phase 74 tests using injected Phase 67 fakes only."""

# ruff: noqa: E501

from pathlib import Path

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_approved_next_step_preparation_phase_bridge_cycle_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate({
        "id": "workflow", "name": "Workflow", "description": "test",
        "steps": [
            {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
            {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
        ],
    })


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate({
        "id": "two", "name": "Two", "role": "role", "instructions": "employee",
        "model": "model", "allowed_tools": ["tool"],
    })


def decision() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("prepare_next_step", "workflow", "first", 1,
                                       "one", "second", 2, "two", "next_step_available")


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "first", 1, "second", 2, "two")


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep("workflow", "second", 2, "two", "employee", "b", "model", ("tool",))


def targets(tmp_path: Path, status: str = "succeeded", index: int = 1) -> tuple[Path, Path]:
    definition = workflow()
    step = definition.steps[index - 1]
    state = WorkflowExecutionState("workflow", status, step.id, index, step.employee,
                                   tuple(item.id for item in definition.steps[:index])
                                   if status == "succeeded" else (),
                                   None if status == "succeeded" else "api_error")
    events = []
    for event_index in range(1, index):
        previous = definition.steps[event_index - 1]
        events.append(RuntimeStepEvent("step_succeeded", "workflow", previous.id, event_index,
                                       previous.employee, "running", "succeeded", "openai", None,
                                       "response", "request", "output", None))
    events.append(RuntimeStepEvent("step_succeeded" if status == "succeeded" else "step_failed",
                                   "workflow", step.id, index, step.employee, "running", status,
                                   "openai", None if status == "succeeded" else "api_error",
                                   "response" if status == "succeeded" else None, "request",
                                   "output" if status == "succeeded" else None,
                                   None if status == "succeeded" else "safe"))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.json"
    state_path.write_text(serialize_workflow_execution_state_json(state), encoding="utf-8")
    events_path.write_text("".join(serialize_runtime_step_event_jsonl(item) for item in events), encoding="utf-8")
    return state_path, events_path


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision("workflow_complete", "workflow", "second", 2,
                                       "two", None, None, None, "last_step_succeeded")


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome("persisted_failure", "workflow", "first", 1, "one", "api_error")


def test_prepare_delegates_exact_phase67_arguments_once_and_returns_same_object(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    result, definition, approved, person, value = decision(), workflow(), approval(), employee(), prepared()
    calls: list[tuple[object, ...]] = []

    def phase67(*args: object) -> PreparedWorkflowStep:
        calls.append(args)
        assert args == (result, definition, state, events, approved, person)
        return value

    returned = route_approved_next_step_preparation_phase_bridge_cycle_continuation(
        result, definition, approved, person, state, events, phase67_function=phase67
    )
    assert returned is value
    assert len(calls) == 1


def test_stop_routes_are_unchanged_and_zero_call(tmp_path: Path) -> None:
    calls = 0

    def phase67(*args: object):
        nonlocal calls
        calls += 1
        raise AssertionError("must not be called")

    state, events = targets(tmp_path, index=2)
    value = completion()
    returned = route_approved_next_step_preparation_phase_bridge_cycle_continuation(
        value, workflow(), None, None, state, events, phase67_function=phase67
    )
    assert returned is value

    state, events = targets(tmp_path, "failed")
    value = failure()
    returned = route_approved_next_step_preparation_phase_bridge_cycle_continuation(
        value, workflow(), None, None, state, events, phase67_function=phase67
    )
    assert returned is value and calls == 0
