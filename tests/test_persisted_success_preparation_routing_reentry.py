"""Tests for Phase 39 routing."""

from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedSuccessPreparationRoutingCompatibilityError,
    route_persisted_success_progression_reentry,
)
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    PreparedWorkflowStep,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision


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


def decision(**changes: object) -> WorkflowProgressionDecision:
    values: dict[str, object] = {
        "decision": "prepare_next_step",
        "workflow_id": "workflow",
        "current_step_id": "first",
        "current_step_index": 1,
        "current_employee_id": "one",
        "next_step_id": "second",
        "next_step_index": 2,
        "next_employee_id": "two",
        "reason": "next_step_available",
    }
    values.update(changes)
    return WorkflowProgressionDecision(**values)  # type: ignore[arg-type]


def approval() -> NextStepPreparationApproval:
    return NextStepPreparationApproval(True, "workflow", "first", 1, "second", 2, "two")


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        "workflow", "second", 2, "two", "employee", "b", "model", ("tool",)
    )


def targets(tmp_path: Path) -> tuple[Path, Path]:
    state, events = tmp_path / "state", tmp_path / "events"
    state.write_bytes(b"state")
    events.write_bytes(b"events")
    return state, events


def test_prepare_routes_once_and_returns_same_prepared_step(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    calls = [0, 0]
    expected = prepared()

    def progress(*_: object) -> WorkflowProgressionDecision:
        calls[0] += 1
        return decision()

    def prepare(*_: object) -> PreparedWorkflowStep:
        calls[1] += 1
        return expected

    assert (
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=progress,
            preparation_function=prepare,
        )
        is expected
    )
    assert calls == [1, 1]


def test_complete_returns_same_decision_without_preparation(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    complete = decision(
        decision="workflow_complete",
        current_step_id="second",
        current_step_index=2,
        current_employee_id="two",
        next_step_id=None,
        next_step_index=None,
        next_employee_id=None,
        reason="last_step_succeeded",
    )
    calls = 0

    def prepare(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_persisted_success_progression_reentry(
            complete,
            workflow(),
            state,
            events,
            None,
            None,
            progression_function=lambda *_: complete,
            preparation_function=prepare,
        )
        is complete
    )
    assert calls == 0


@pytest.mark.parametrize(
    "change",
    [
        {"current_step_index": 3},
        {"current_step_id": "other"},
        {"next_step_id": "other"},
        {"reason": "other"},
    ],
)
def test_invalid_decision_rejects_before_dependencies(
    tmp_path: Path, change: dict[str, object]
) -> None:
    state, events = targets(tmp_path)
    calls = [0, 0]

    def p(*_: object) -> WorkflowProgressionDecision:
        calls[0] += 1
        raise AssertionError

    def q(*_: object) -> PreparedWorkflowStep:
        calls[1] += 1
        raise AssertionError

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError):
        route_persisted_success_progression_reentry(
            decision(**change),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=p,
            preparation_function=q,
        )
    assert calls == [0, 0]


def test_restores_preparation_target_mutation(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = (state.read_bytes(), events.read_bytes())

    def prepare(*_: object) -> PreparedWorkflowStep:
        events.unlink()
        return prepared()

    with pytest.raises(PersistedSuccessPreparationRoutingCompatibilityError) as error:
        route_persisted_success_progression_reentry(
            decision(),
            workflow(),
            state,
            events,
            approval(),
            employee(),
            progression_function=lambda *_: decision(),
            preparation_function=prepare,
        )
    assert error.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == before
