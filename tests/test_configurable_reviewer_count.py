"""Config-only reviewer count: the shared engine fixes no reviewer number.

These tests drive the same common engine with Workflow/Employee definitions
that differ only in the declared reviewer set. No Python constant names a
reviewer count, so 1, 2, 3, 4 and a larger representative set all plan through
the identical code path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_office.definitions.workflow import (
    LoadedWorkflow,
    WorkflowDefinition,
)

pytestmark = pytest.mark.filterwarnings("error")

REPOSITORY = Path(__file__).resolve().parents[1]
MAX_REVISIONS = 3


def _workflow_with_reviewers(reviewers: int) -> LoadedWorkflow:
    """Build one Workflow whose reviewer set is declared only in config."""
    steps = [
        {
            "id": "write-draft",
            "name": "Write Draft",
            "employee": "article-writer",
            "model": "model",
            "instructions": "Write the requested document.",
            "business_approval_required": True,
            "artifact_content_type": "text/markdown",
        }
    ]
    review_ids = [f"review-{index + 1}" for index in range(reviewers)]
    steps.extend(
        {
            "id": review_id,
            "name": review_id,
            "employee": f"reviewer-{index + 1}",
            "model": "model",
            "instructions": "Independently review the exact Artifact.",
            "business_approval_required": True,
            "artifact_content_type": "text/markdown",
        }
        for index, review_id in enumerate(review_ids)
    )
    steps.append(
        {
            "id": "revise",
            "name": "Revise",
            "employee": "article-writer",
            "model": "model",
            "instructions": "Aggregate the reviewers' grounded findings.",
            "business_approval_required": True,
            "artifact_content_type": "text/markdown",
        }
    )
    definition = WorkflowDefinition.model_validate(
        {
            "id": "configurable-reviewers",
            "name": "Configurable Reviewers",
            "description": "Offline config-only reviewer-count fixture.",
            "bounded_review": {
                "draft_step": "write-draft",
                "review_steps": review_ids,
                "revision_step": "revise",
                "max_revisions": MAX_REVISIONS,
            },
            "steps": steps,
        }
    )
    return LoadedWorkflow(Path("<offline-test:configurable-reviewers>"), definition)


@pytest.mark.parametrize("reviewers", [1, 2, 3, 4, 5, 7])
def test_declared_reviewer_count_drives_the_plan_without_engine_change(
    reviewers: int,
) -> None:
    from ai_office.engine.workflow_bounded_job import bounded_review_action_plan

    workflow = _workflow_with_reviewers(reviewers)
    assert workflow.definition.bounded_review is not None
    assert workflow.definition.bounded_review.review_steps == [
        f"review-{index + 1}" for index in range(reviewers)
    ]

    plan = bounded_review_action_plan(workflow, MAX_REVISIONS)

    # Budget is declared, finite and independent of any Python constant:
    # 1 draft + N first-round reviews + R * (1 revision + N reviews).
    assert len(plan) == (MAX_REVISIONS + 1) * (reviewers + 1)
    assert plan[0] == ("write-draft", 1, "assignment")
    assert [item[2] for item in plan[1 : 1 + reviewers]] == ["review_input"] * reviewers
    for round_index in range(MAX_REVISIONS):
        offset = 1 + reviewers + round_index * (reviewers + 1)
        assert plan[offset] == ("revise", 1, "revision_input")
        assert [item[2] for item in plan[offset + 1 : offset + 1 + reviewers]] == [
            "re_review_input"
        ] * reviewers


@pytest.mark.parametrize("reviewers", [1, 2, 3, 4, 5])
def test_every_declared_reviewer_is_a_private_one_step_view(
    reviewers: int,
) -> None:
    from ai_office.engine.workflow_bounded_job import bounded_job_execution_view

    workflow = _workflow_with_reviewers(reviewers)
    for index in range(reviewers):
        view = bounded_job_execution_view(workflow, f"review-{index + 1}")
        assert [step.id for step in view.definition.steps] == [f"review-{index + 1}"]
    revise_view = bounded_job_execution_view(workflow, "revise")
    assert [step.id for step in revise_view.definition.steps] == ["revise"]


def test_duplicate_reviewer_employee_is_rejected_before_execution() -> None:
    steps = [
        {
            "id": "write-draft",
            "name": "Write Draft",
            "employee": "article-writer",
            "model": "model",
            "instructions": "Write.",
            "business_approval_required": True,
            "artifact_content_type": "text/markdown",
        },
        {
            "id": "review-a",
            "name": "Review A",
            "employee": "document-reviewer",
            "model": "model",
            "instructions": "Review.",
            "business_approval_required": True,
            "artifact_content_type": "text/markdown",
        },
        {
            "id": "review-b",
            "name": "Review B",
            "employee": "document-reviewer",
            "model": "model",
            "instructions": "Review.",
            "business_approval_required": True,
            "artifact_content_type": "text/markdown",
        },
        {
            "id": "revise",
            "name": "Revise",
            "employee": "article-writer",
            "model": "model",
            "instructions": "Revise.",
            "business_approval_required": True,
            "artifact_content_type": "text/markdown",
        },
    ]
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(
            {
                "id": "duplicate-reviewers",
                "name": "Duplicate Reviewers",
                "description": "Two reviewers must be independent employees.",
                "bounded_review": {
                    "draft_step": "write-draft",
                    "review_steps": ["review-a", "review-b"],
                    "revision_step": "revise",
                    "max_revisions": 1,
                },
                "steps": steps,
            }
        )


def test_empty_reviewer_set_is_rejected() -> None:
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(
            {
                "id": "no-reviewers",
                "name": "No Reviewers",
                "description": "At least one reviewer is required.",
                "bounded_review": {
                    "draft_step": "write-draft",
                    "review_steps": [],
                    "revision_step": "revise",
                    "max_revisions": 1,
                },
                "steps": [
                    {
                        "id": "write-draft",
                        "name": "Write Draft",
                        "employee": "article-writer",
                        "model": "model",
                        "instructions": "Write.",
                        "business_approval_required": True,
                        "artifact_content_type": "text/markdown",
                    },
                    {
                        "id": "revise",
                        "name": "Revise",
                        "employee": "article-writer",
                        "model": "model",
                        "instructions": "Revise.",
                        "business_approval_required": True,
                        "artifact_content_type": "text/markdown",
                    },
                ],
            }
        )


def test_shipped_article_workflow_uses_two_declared_reviewers() -> None:
    from ai_office.definitions.workflow import load_workflow_file

    workflow = load_workflow_file(
        REPOSITORY / "workflows/article-production.yaml"
    ).definition
    assert workflow.bounded_review is not None
    assert workflow.bounded_review.review_steps == ["review-quality", "review-evidence"]
    assert (MAX_REVISIONS + 1) * (len(workflow.bounded_review.review_steps) + 1) == 12
