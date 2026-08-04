"""Tests for approved, pure next-step preparation."""

from dataclasses import FrozenInstanceError, replace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    NextStepPreparationApproval,
    NextStepPreparationApprovalError,
    NextStepPreparationCompatibilityError,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    decide_workflow_progression,
    prepare_approved_next_workflow_step,
)
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import LoadedWorkflowExecutionHistory


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "Description.",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "one",
                    "instructions": "First task.",
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "two",
                    "instructions": "Second task.",
                },
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "two",
            "name": "Employee Two",
            "role": "Role",
            "instructions": "Employee instructions.",
            "model": "model",
            "allowed_tools": ["first-tool", "second-tool"],
        }
    )


def history() -> LoadedWorkflowExecutionHistory:
    return LoadedWorkflowExecutionHistory(
        WorkflowExecutionState(
            "workflow", "succeeded", "first", 1, "one", ("first",), None
        ),
        (),
    )


def decision() -> WorkflowProgressionDecision:
    return decide_workflow_progression(workflow(), history())


def approval(
    value: WorkflowProgressionDecision | None = None,
) -> NextStepPreparationApproval:
    current = value or decision()
    return NextStepPreparationApproval(
        True,
        current.workflow_id,
        current.current_step_id,
        current.current_step_index,
        current.next_step_id,  # type: ignore[arg-type]
        current.next_step_index,  # type: ignore[arg-type]
        current.next_employee_id,  # type: ignore[arg-type]
    )


def test_prepares_exact_immutable_step_and_preserves_inputs() -> None:
    value = decision()
    approved = approval(value)
    prepared = prepare_approved_next_workflow_step(
        workflow(), history(), value, approved, employee()
    )

    assert prepared == PreparedWorkflowStep(
        "workflow",
        "second",
        2,
        "two",
        "Employee instructions.",
        "Second task.",
        "model",
        ("first-tool", "second-tool"),
    )
    assert (
        prepare_approved_next_workflow_step(
            workflow(), history(), value, approved, employee()
        )
        == prepared
    )
    assert value == decision()
    with pytest.raises(FrozenInstanceError):
        approved.approved = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        prepared.model = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "outcome", ["workflow_complete", "stopped_failed", "not_progressable"]
)
def test_only_prepare_next_step_decisions_are_accepted(outcome: str) -> None:
    value = replace(decision(), decision=outcome)  # type: ignore[arg-type]

    with pytest.raises(NextStepPreparationCompatibilityError) as caught:
        prepare_approved_next_workflow_step(
            workflow(), history(), value, approval(value), employee()
        )

    assert caught.value.detail.classification == "decision_type"


@pytest.mark.parametrize(
    ("changed", "classification"),
    [
        (lambda value: replace(value, next_step_id=None), "next_step_identity"),
        (lambda value: replace(value, next_step_index=3), "next_step_identity"),
        (
            lambda value: replace(value, current_step_id="other"),
            "current_step_identity",
        ),
        (lambda value: replace(value, workflow_id="other"), "workflow_identity"),
        (lambda value: replace(value, next_employee_id="other"), "next_step_identity"),
    ],
)
def test_stale_or_incomplete_decisions_are_rejected_safely(
    changed: object, classification: str
) -> None:
    value = changed(decision())  # type: ignore[operator]

    with pytest.raises(NextStepPreparationCompatibilityError) as caught:
        prepare_approved_next_workflow_step(
            workflow(), history(), value, approval(), employee()
        )

    assert str(caught.value) == "next-step preparation inputs are incompatible"
    assert caught.value.detail.classification == classification
    assert "other" not in str(caught.value)


@pytest.mark.parametrize(
    ("changed", "classification"),
    [
        (lambda value: replace(value, approved=False), "approval_required"),
        (lambda value: replace(value, workflow_id="other"), "approval_identity"),
        (lambda value: replace(value, current_step_index=2), "approval_identity"),
        (lambda value: replace(value, next_step_id="other"), "approval_identity"),
        (lambda value: replace(value, next_step_index=True), "approval_identity"),
    ],
)
def test_approval_must_be_exactly_bound(changed: object, classification: str) -> None:
    approved = changed(approval())  # type: ignore[operator]

    with pytest.raises(NextStepPreparationApprovalError) as caught:
        prepare_approved_next_workflow_step(
            workflow(), history(), decision(), approved, employee()
        )

    assert str(caught.value) == "next-step preparation approval is invalid"
    assert caught.value.detail.classification == classification


def test_employee_identity_mismatch_is_rejected() -> None:
    incorrect = employee().model_copy(update={"id": "other"})

    with pytest.raises(NextStepPreparationCompatibilityError) as caught:
        prepare_approved_next_workflow_step(
            workflow(), history(), decision(), approval(), incorrect
        )

    assert caught.value.detail.classification == "employee_identity"


@pytest.mark.parametrize(
    "changed_state",
    [
        lambda state: replace(state, current_step_id="other"),
        lambda state: replace(state, current_employee_id="other"),
    ],
)
def test_history_current_identity_must_match_workflow_definition(
    changed_state: object,
) -> None:
    changed_history = replace(history(), state=changed_state(history().state))  # type: ignore[operator]

    with pytest.raises(NextStepPreparationCompatibilityError) as caught:
        prepare_approved_next_workflow_step(
            workflow(), changed_history, decision(), approval(), employee()
        )

    assert caught.value.detail.classification == "current_step_identity"


@pytest.mark.parametrize("status", ["running", "ready", "failed"])
def test_non_succeeded_history_rejects_forged_prepare_decision(status: str) -> None:
    changed_history = replace(history(), state=replace(history().state, status=status))

    with pytest.raises(NextStepPreparationCompatibilityError) as caught:
        prepare_approved_next_workflow_step(
            workflow(), changed_history, decision(), approval(), employee()
        )

    assert caught.value.detail.classification == "next_step_identity"


def test_final_succeeded_history_rejects_forged_next_step_decision() -> None:
    final_history = LoadedWorkflowExecutionHistory(
        WorkflowExecutionState(
            "workflow", "succeeded", "second", 2, "two", ("first", "second"), None
        ),
        (),
    )
    forged = replace(
        decision(),
        current_step_id="second",
        current_step_index=2,
        current_employee_id="two",
        next_step_id="forged",
        next_step_index=3,
        next_employee_id="forged",
    )

    with pytest.raises(NextStepPreparationCompatibilityError) as caught:
        prepare_approved_next_workflow_step(
            workflow(), final_history, forged, approval(forged), employee()
        )

    assert caught.value.detail.classification == "next_step_identity"


def test_succeeded_non_final_history_still_prepares_next_step() -> None:
    prepared = prepare_approved_next_workflow_step(
        workflow(), history(), decision(), approval(), employee()
    )

    assert prepared.step_id == "second"
