"""Tests for pure workflow progression decisions."""

from dataclasses import FrozenInstanceError, replace
from typing import get_args

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    WorkflowProgressionCompatibilityError,
    WorkflowProgressionDecision,
    WorkflowProgressionDecisionType,
    decide_workflow_progression,
)
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import LoadedWorkflowExecutionHistory


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "A workflow.",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "employee-one",
                    "instructions": "Do first.",
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "employee-two",
                    "instructions": "Do second.",
                },
                {
                    "id": "third",
                    "name": "Third",
                    "employee": "employee-three",
                    "instructions": "Do third.",
                },
            ],
        }
    )


def history(
    status: str,
    index: int = 1,
    completed: tuple[str, ...] = (),
) -> LoadedWorkflowExecutionHistory:
    step = workflow().steps[index - 1]
    return LoadedWorkflowExecutionHistory(
        WorkflowExecutionState(
            "workflow",
            status,  # type: ignore[arg-type]
            step.id,
            index,
            step.employee,
            completed,
            "api_error" if status == "failed" else None,  # type: ignore[arg-type]
        ),
        (),
    )


def test_decision_model_is_immutable_and_has_finite_outcomes() -> None:
    decision = decide_workflow_progression(workflow(), history("ready"))

    assert set(get_args(WorkflowProgressionDecisionType)) == {
        "prepare_next_step",
        "workflow_complete",
        "stopped_failed",
        "not_progressable",
    }
    assert set(WorkflowProgressionDecision.__dataclass_fields__) == {
        "decision",
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "next_step_id",
        "next_step_index",
        "next_employee_id",
        "reason",
    }
    with pytest.raises(FrozenInstanceError):
        decision.reason = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("status", "reason", "decision_type"),
    [
        ("ready", "workflow_not_started", "not_progressable"),
        ("running", "step_execution_in_progress", "not_progressable"),
    ],
)
def test_nonterminal_states_are_not_automatically_progressed(
    status: str, reason: str, decision_type: str
) -> None:
    decision = decide_workflow_progression(workflow(), history(status))

    assert decision.decision == decision_type
    assert decision.reason == reason
    assert (
        decision.next_step_id,
        decision.next_step_index,
        decision.next_employee_id,
    ) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize("index", [1, 2, 3])
def test_failed_steps_stop_without_next_step(index: int) -> None:
    completed = tuple(step.id for step in workflow().steps[: index - 1])
    decision = decide_workflow_progression(
        workflow(), history("failed", index, completed)
    )

    assert decision.decision == "stopped_failed"
    assert decision.reason == "latest_step_failed"
    assert decision.current_step_index == index
    assert decision.next_step_id is None
    assert decision.next_step_index is None
    assert decision.next_employee_id is None


def test_succeeded_step_selects_exact_immediate_next_step() -> None:
    value = history("succeeded", 2, ("first", "second"))

    decision = decide_workflow_progression(workflow(), value)

    assert decision == WorkflowProgressionDecision(
        "prepare_next_step",
        "workflow",
        "second",
        2,
        "employee-two",
        "third",
        3,
        "employee-three",
        "next_step_available",
    )
    assert value == history("succeeded", 2, ("first", "second"))


def test_succeeded_final_and_one_step_workflows_are_complete() -> None:
    decision = decide_workflow_progression(
        workflow(), history("succeeded", 3, ("first", "second", "third"))
    )
    one_step = WorkflowDefinition.model_validate(
        {
            "id": "single",
            "name": "Single",
            "description": "One step.",
            "steps": [
                {
                    "id": "only",
                    "name": "Only",
                    "employee": "employee",
                    "instructions": "Do it.",
                }
            ],
        }
    )
    one_history = LoadedWorkflowExecutionHistory(
        WorkflowExecutionState(
            "single", "succeeded", "only", 1, "employee", ("only",), None
        ),
        (),
    )

    assert decision.decision == "workflow_complete"
    assert decision.reason == "last_step_succeeded"
    assert decision.next_step_id is None
    assert (
        decide_workflow_progression(one_step, one_history).decision
        == "workflow_complete"
    )


@pytest.mark.parametrize(
    ("changed_history", "classification"),
    [
        (
            replace(
                history("ready"),
                state=replace(history("ready").state, workflow_id="other"),
            ),
            "workflow_identity",
        ),
        (
            replace(
                history("ready"),
                state=replace(history("ready").state, current_step_index=4),
            ),
            "current_step_index",
        ),
        (
            replace(
                history("ready"),
                state=replace(history("ready").state, current_step_id="other"),
            ),
            "current_step_identity",
        ),
        (
            replace(
                history("ready"),
                state=replace(history("ready").state, current_employee_id="other"),
            ),
            "current_employee_identity",
        ),
        (
            replace(
                history("ready"),
                state=replace(history("ready").state, completed_step_ids=("other",)),
            ),
            "completed_step_identity",
        ),
        (history("ready", 3, ("second", "first")), "completed_step_order"),
        (history("ready", 2, ("third",)), "completed_step_order"),
        (history("succeeded", 2, ("first",)), "completed_step_order"),
    ],
)
def test_incompatible_history_is_rejected_safely(
    changed_history: LoadedWorkflowExecutionHistory, classification: str
) -> None:
    with pytest.raises(WorkflowProgressionCompatibilityError) as caught:
        decide_workflow_progression(workflow(), changed_history)

    assert str(caught.value) == "workflow progression inputs are incompatible"
    assert caught.value.detail.classification == classification
    assert "other" not in str(caught.value)


def test_compatible_duplicate_completed_ids_are_preserved_and_deterministic() -> None:
    value = history("succeeded", 2, ("first", "first", "second", "second"))

    first = decide_workflow_progression(workflow(), value)
    second = decide_workflow_progression(workflow(), value)

    assert first == second
    assert value.state.completed_step_ids == ("first", "first", "second", "second")


@pytest.mark.parametrize(
    ("status", "index", "completed", "accepted"),
    [
        ("succeeded", 3, ("second",), False),
        ("succeeded", 3, ("first", "third"), False),
        ("succeeded", 3, ("first", "second", "third"), True),
        ("succeeded", 2, ("first", "first", "second", "second"), True),
        ("ready", 2, ("first",), True),
        ("running", 2, ("first",), True),
        ("failed", 2, ("first",), True),
        ("ready", 2, ("first", "second"), False),
        ("running", 2, ("first", "second"), False),
        ("failed", 2, ("first", "second"), False),
    ],
)
def test_completed_steps_must_form_the_state_specific_sequential_prefix(
    status: str, index: int, completed: tuple[str, ...], accepted: bool
) -> None:
    value = history(status, index, completed)

    if accepted:
        decision = decide_workflow_progression(workflow(), value)
        assert decision.current_step_index == index
    else:
        with pytest.raises(WorkflowProgressionCompatibilityError) as caught:
            decide_workflow_progression(workflow(), value)
        assert caught.value.detail.classification == "completed_step_order"
