"""Tests for the read-only Phase 32 approved next-step reentry boundary."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedNextStepReentryCompatibilityError,
    NextStepPreparationApproval,
    NextStepPreparationCompatibilityError,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    prepare_approved_next_step_reentry,
)
from ai_office.engine.next_step_preparation import prepare_approved_next_workflow_step
from ai_office.engine.workflow_progression import decide_workflow_progression
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow(**changes: object) -> WorkflowDefinition:
    values: dict[str, object] = {
        "id": "workflow",
        "name": "Workflow",
        "description": "Description",
        "steps": [
            {"id": "first", "name": "First", "employee": "one", "instructions": "a"},
            {"id": "second", "name": "Second", "employee": "two", "instructions": "b"},
        ],
    }
    values.update(changes)
    return WorkflowDefinition(**values)


def employee(**changes: object) -> EmployeeDefinition:
    values: dict[str, object] = {
        "id": "two",
        "name": "Two",
        "role": "Role",
        "instructions": "Employee instructions",
        "model": "model",
        "allowed_tools": ["tool"],
    }
    values.update(changes)
    return EmployeeDefinition(**values)


def state(**changes: object) -> WorkflowExecutionState:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "status": "succeeded",
        "current_step_id": "first",
        "current_step_index": 1,
        "current_employee_id": "one",
        "completed_step_ids": ("first",),
        "last_failure_category": None,
    }
    values.update(changes)
    return WorkflowExecutionState(**values)  # type: ignore[arg-type]


def event(**changes: object) -> RuntimeStepEvent:
    values: dict[str, object] = {
        "event_type": "step_succeeded",
        "workflow_id": "workflow",
        "step_id": "first",
        "step_index": 1,
        "employee_id": "one",
        "previous_status": "running",
        "next_status": "succeeded",
        "provider": "openai",
        "failure_category": None,
        "response_id": "response",
        "request_id": "request",
        "output_text": "output",
        "message": None,
    }
    values.update(changes)
    return RuntimeStepEvent(**values)  # type: ignore[arg-type]


def write_history(
    tmp_path: Path, value: WorkflowExecutionState
) -> WorkflowExecutionPersistenceTargets:
    targets = WorkflowExecutionPersistenceTargets(
        tmp_path / "state.json", tmp_path / "events.jsonl"
    )
    targets.state_path.write_text(serialize_workflow_execution_state_json(value))
    targets.events_path.write_text(serialize_runtime_step_event_jsonl(event()))
    return targets


def decision(
    targets: WorkflowExecutionPersistenceTargets,
) -> WorkflowProgressionDecision:
    return decide_workflow_progression(
        workflow(), load_workflow_execution_history(targets)
    )


def approval(value: WorkflowProgressionDecision) -> NextStepPreparationApproval:
    return NextStepPreparationApproval(
        True,
        value.workflow_id,
        value.current_step_id,
        value.current_step_index,
        value.next_step_id,  # type: ignore[arg-type]
        value.next_step_index,  # type: ignore[arg-type]
        value.next_employee_id,  # type: ignore[arg-type]
    )


def test_valid_reentry_delegates_once_and_returns_exact_prepared_step(
    tmp_path: Path,
) -> None:
    targets = write_history(tmp_path, state())
    value = decision(targets)
    expected = prepare_approved_next_workflow_step(
        workflow(),
        load_workflow_execution_history(targets),
        value,
        approval(value),
        employee(),
    )
    calls = 0
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())

    def prepare(*_args: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    assert (
        prepare_approved_next_step_reentry(
            workflow(),
            targets.state_path,
            targets.events_path,
            value,
            approval(value),
            employee(),
            preparation_function=prepare,  # type: ignore[arg-type]
        )
        is expected
    )
    assert calls == 1
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


@pytest.mark.parametrize(
    ("changed_decision", "changed_approval", "classification"),
    [
        (lambda value: object(), lambda value: approval(value), "decision_type"),
        (
            lambda value: replace(value, decision="workflow_complete"),
            lambda value: approval(value),
            "decision_type",
        ),
        (
            lambda value: replace(value, decision="stopped_failed"),
            lambda value: approval(value),
            "decision_type",
        ),
        (
            lambda value: replace(value, decision="not_progressable"),
            lambda value: approval(value),
            "decision_type",
        ),
        (
            lambda value: replace(value, current_step_id="other"),
            lambda value: approval(value),
            "decision_identity",
        ),
        (
            lambda value: replace(value, workflow_id="other"),
            lambda value: approval(value),
            "decision_identity",
        ),
        (
            lambda value: replace(value, current_step_index=2),
            lambda value: approval(value),
            "decision_identity",
        ),
        (
            lambda value: replace(value, current_employee_id="other"),
            lambda value: approval(value),
            "decision_identity",
        ),
        (
            lambda value: replace(value, next_step_id="other"),
            lambda value: approval(value),
            "decision_identity",
        ),
        (
            lambda value: replace(value, next_step_index=3),
            lambda value: approval(value),
            "decision_identity",
        ),
        (
            lambda value: replace(value, next_employee_id="other"),
            lambda value: approval(value),
            "decision_identity",
        ),
        (lambda value: value, lambda value: object(), "approval_contract"),
        (
            lambda value: value,
            lambda value: replace(approval(value), workflow_id="other"),
            "approval_contract",
        ),
        (
            lambda value: value,
            lambda value: replace(approval(value), next_step_id="first"),
            "approval_contract",
        ),
        (
            lambda value: value,
            lambda value: replace(approval(value), next_step_index=1),
            "approval_contract",
        ),
        (
            lambda value: value,
            lambda value: replace(approval(value), next_step_index=True),
            "approval_contract",
        ),
        (
            lambda value: value,
            lambda value: replace(approval(value), next_employee_id="one"),
            "approval_contract",
        ),
    ],
)
def test_invalid_decision_or_approval_rejects_before_preparation(
    tmp_path: Path,
    changed_decision: object,
    changed_approval: object,
    classification: str,
) -> None:
    targets = write_history(tmp_path, state())
    original = decision(targets)
    value = changed_decision(original)  # type: ignore[operator]
    approved = changed_approval(original)  # type: ignore[operator]
    calls = 0
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())

    def unexpected(*_args: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ApprovedNextStepReentryCompatibilityError) as caught:
        prepare_approved_next_step_reentry(
            workflow(),
            targets.state_path,
            targets.events_path,
            value,
            approved,
            employee(),
            preparation_function=unexpected,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == classification
    assert calls == 0
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


@pytest.mark.parametrize(
    "changed_state",
    [
        lambda value: replace(value, status="ready"),
        lambda value: replace(value, workflow_id="other"),
        lambda value: replace(value, completed_step_ids=("second",)),
    ],
)
def test_stale_or_incompatible_history_rejects_before_preparation(
    tmp_path: Path, changed_state: object
) -> None:
    targets = write_history(tmp_path, changed_state(state()))  # type: ignore[operator]
    value = WorkflowProgressionDecision(
        "prepare_next_step",
        "workflow",
        "first",
        1,
        "one",
        "second",
        2,
        "two",
        "next_step_available",
    )
    calls = 0

    def unexpected(*_args: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ApprovedNextStepReentryCompatibilityError):
        prepare_approved_next_step_reentry(
            workflow(),
            targets.state_path,
            targets.events_path,
            value,
            approval(value),
            employee(),
            preparation_function=unexpected,  # type: ignore[arg-type]
        )
    assert calls == 0


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        PreparedWorkflowStep("other", "second", 2, "two", "a", "b", "m", ()),
        PreparedWorkflowStep("workflow", "first", 1, "one", "a", "b", "m", ()),
    ],
)
def test_invalid_prepared_result_is_rejected_after_one_call(
    tmp_path: Path, returned: object
) -> None:
    targets = write_history(tmp_path, state())
    value = decision(targets)
    calls = 0
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())

    def prepare(*_args: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    with pytest.raises(ApprovedNextStepReentryCompatibilityError) as caught:
        prepare_approved_next_step_reentry(
            workflow(),
            targets.state_path,
            targets.events_path,
            value,
            approval(value),
            employee(),
            preparation_function=prepare,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "preparation_contract"
    assert calls == 1
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


def test_phase_26_safe_error_is_preserved(tmp_path: Path) -> None:
    targets = write_history(tmp_path, state())
    value = decision(targets)
    expected = NextStepPreparationCompatibilityError("employee_identity")

    def rejected(*_args: object) -> object:
        raise expected

    with pytest.raises(NextStepPreparationCompatibilityError) as caught:
        prepare_approved_next_step_reentry(
            workflow(),
            targets.state_path,
            targets.events_path,
            value,
            approval(value),
            employee(),
            preparation_function=rejected,  # type: ignore[arg-type]
        )
    assert caught.value is expected


def test_missing_or_malformed_targets_are_safe(tmp_path: Path) -> None:
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    events_path.write_text("")
    value = WorkflowProgressionDecision(
        "prepare_next_step",
        "workflow",
        "first",
        1,
        "one",
        "second",
        2,
        "two",
        "next_step_available",
    )
    with pytest.raises(ApprovedNextStepReentryCompatibilityError) as caught:
        prepare_approved_next_step_reentry(
            workflow(), state_path, events_path, value, approval(value), employee()
        )
    assert str(state_path) not in str(caught.value)
