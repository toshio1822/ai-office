"""Tests for the Phase 54 bridge using injected Phase 47 fakes only."""

from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_prepared_step_start_phase_bridge_reentry,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "test",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "one",
                    "instructions": "a",
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "two",
                    "instructions": "b",
                },
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "two",
            "name": "Two",
            "role": "role",
            "instructions": "employee",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        "workflow", "second", 2, "two", "employee", "b", "model", ("tool",)
    )


def event(status: str, index: int) -> RuntimeStepEvent:
    step, person = ("first", "one") if index == 1 else ("second", "two")
    return RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "workflow",
        step,
        index,
        person,
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "output" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]


def targets(tmp_path: Path, status: str, index: int) -> tuple[Path, Path]:
    step, person = ("first", "one") if index == 1 else ("second", "two")
    state = WorkflowExecutionState(
        "workflow",
        status,
        step,
        index,
        person,
        ("first",) if index == 1 or status == "failed" else ("first", "second"),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    events = ([event("succeeded", 1)] if index == 2 else []) + [event(status, index)]
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events)
    )
    return state_path, events_path


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "employee", "b", ("tool",)),
        WorkflowExecutionState(
            "workflow", "running", "second", 2, "two", ("first",), None
        ),
    )


def test_prepared_step_delegates_once_with_identical_objects(tmp_path: Path) -> None:
    state, events = targets(tmp_path, "succeeded", 1)
    result, definition, person, expected = prepared(), workflow(), employee(), start()
    calls = 0

    def phase47(*args: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        assert args == (result, definition, person, state, events)
        assert all(
            actual is supplied
            for actual, supplied in zip(
                args, (result, definition, person, state, events), strict=True
            )
        )
        return expected

    before = state.read_bytes(), events.read_bytes()
    assert (
        route_prepared_step_start_phase_bridge_reentry(
            result, definition, person, state, events, start_bridge_function=phase47
        )
        is expected
    )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["completion", "failure"])
def test_terminal_routes_stop_without_phase47(tmp_path: Path, kind: str) -> None:
    status = "succeeded" if kind == "completion" else "failed"
    state, events = targets(tmp_path, status, 2)
    result: object = (
        WorkflowProgressionDecision(
            "workflow_complete",
            "workflow",
            "second",
            2,
            "two",
            None,
            None,
            None,
            "last_step_succeeded",
        )
        if kind == "completion"
        else PersistedExecutionOutcome(
            "persisted_failure", "workflow", "second", 2, "two", "api_error"
        )
    )
    before = state.read_bytes(), events.read_bytes()
    assert (
        route_prepared_step_start_phase_bridge_reentry(
            result,
            workflow(),
            None,
            state,
            events,
            start_bridge_function=lambda *_: pytest.fail("Phase 47 must not run"),
        )
        is result
    )
    assert (state.read_bytes(), events.read_bytes()) == before
