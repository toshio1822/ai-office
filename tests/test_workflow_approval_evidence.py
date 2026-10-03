"""Behavioral tests for Milestone 2 Run-bound approval evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_office.cli import app
from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.workflow_approval_evidence import (
    WorkflowApprovalEvidenceConflictError,
    WorkflowApprovalEvidenceError,
    WorkflowApprovalEvidenceLoadError,
    WorkflowApprovalEvidencePersistenceError,
    approve_business_step,
    build_execution_approval_evidence_for_tools,
    business_approval_evidence_canonical_bytes,
    execution_approval_evidence_canonical_bytes,
    list_run_approval_evidence,
    load_business_approval_evidence,
    load_execution_approval_evidence,
    persist_business_approval_evidence,
    persist_execution_approval_evidence,
    validate_business_approval_evidence,
    validate_execution_approval_evidence,
)
from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    execution_target_fingerprint,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    approve_model_invocation_execution,
)
from tests._run_test_support import TestRun as RunFixture
from tests._run_test_support import create_test_run

runner = CliRunner()


def _workflow(
    *,
    step_count: int = 2,
    required: bool = True,
    omit_policy: bool = False,
) -> WorkflowDefinition:
    steps: list[dict[str, object]] = []
    for index in range(1, step_count + 1):
        value: dict[str, object] = {
            "id": f"step-{index}",
            "name": f"Step {index}",
            "employee": f"employee-{index}",
            "instructions": f"Instructions {index}",
        }
        if not omit_policy:
            value["business_approval_required"] = required
        steps.append(value)
    return WorkflowDefinition.model_validate(
        {
            "id": "approval-workflow",
            "name": "Approval Workflow",
            "description": "A deterministic approval workflow.",
            "steps": steps,
        }
    )


def _employees(workflow: WorkflowDefinition) -> tuple[EmployeeDefinition, ...]:
    return tuple(
        EmployeeDefinition(
            id=step.employee,
            name=step.name,
            role="Approval test employee",
            instructions=f"Employee instructions for {step.id}",
            model="test-model",
            allowed_tools=[],
        )
        for step in workflow.steps
    )


def _run(
    root: Path,
    run_id: str = "run-1",
    *,
    workflow: WorkflowDefinition | None = None,
    with_business_approvals: bool = False,
) -> tuple[WorkflowDefinition, RunFixture]:
    selected = workflow or _workflow()
    return selected, create_test_run(
        root,
        run_id,
        selected,
        _employees(selected),
        with_business_approvals=with_business_approvals,
    )


def _business(
    workflow: WorkflowDefinition,
    run: RunFixture,
    *,
    step_index: int = 1,
    approval_id: str = "business-1",
    progression_from_step_id: str | None = None,
    progression_from_step_index: int | None = None,
):
    step = workflow.steps[step_index - 1]
    return approve_business_step(
        binding=run.binding,
        workflow_id=workflow.id,
        step_id=step.id,
        step_index=step_index,
        employee_id=step.employee,
        approved_by="operator",
        approval_id=approval_id,
        progression_from_step_id=progression_from_step_id,
        progression_from_step_index=progression_from_step_index,
    )


def _execution(
    workflow: WorkflowDefinition,
    run: RunFixture,
    *,
    approved_by: str = "operator",
    approval_id: str = "execution-1",
):
    step = workflow.steps[0]
    request = ModelInvocationRequest(
        "test-model",
        "employee instructions",
        step.instructions,
        (),
        run_id=run.binding.run_id,
        manifest_digest=run.binding.manifest_digest,
        run_input=f"input-{run.binding.run_id}",
    )
    approval = approve_model_invocation_execution(
        request,
        (),
        provider="openai",
        approved_by=approved_by,
        approval_id=approval_id,
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    return build_execution_approval_evidence_for_tools(
        request,
        (),
        approval,
        workflow_id=workflow.id,
        step_id=step.id,
        step_index=1,
        employee_id=step.employee,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
    )


def _business_path(run: RunFixture, approval_id: str) -> Path:
    return run.store.root / (
        f"{run.binding.run_id}.business-approval.{approval_id}.json"
    )


def _execution_path(run: RunFixture, approval_id: str) -> Path:
    return run.store.root / (
        f"{run.binding.run_id}.execution-approval.{approval_id}.json"
    )


def test_effective_policy_default_and_opt_out_are_pinned_into_manifest(
    tmp_path: Path,
) -> None:
    default_workflow, default_run = _run(
        tmp_path / "default", "same-run", workflow=_workflow(omit_policy=True)
    )
    opt_out_workflow, opt_out_run = _run(
        tmp_path / "opt-out", "same-run", workflow=_workflow(required=False)
    )

    default_step = default_run.store.root / "same-run.manifest.json"
    assert default_workflow.steps[0].business_approval_required is True
    assert opt_out_workflow.steps[0].business_approval_required is False
    assert default_run.binding.manifest_digest != opt_out_run.binding.manifest_digest
    assert (
        default_step.read_bytes()
        != (opt_out_run.store.root / "same-run.manifest.json").read_bytes()
    )


def test_business_evidence_round_trips_after_restart_with_canonical_digest(
    tmp_path: Path,
) -> None:
    workflow, run = _run(tmp_path)
    evidence = _business(workflow, run)

    persist_business_approval_evidence(run.store, evidence)
    contents = _business_path(run, evidence.approval_id).read_bytes()
    restarted_store = type(run.store)(run.store.root)
    loaded = load_business_approval_evidence(
        restarted_store, run.binding.run_id, evidence.approval_id
    )

    assert loaded == evidence
    assert contents == business_approval_evidence_canonical_bytes(evidence)
    assert evidence.digest == hashlib.sha256(contents).hexdigest()
    assert not contents.endswith(b"\n")
    assert b"synthetic-api-key" not in contents
    assert b"raw-provider-payload" not in contents


def test_business_evidence_is_idempotent_and_conflicts_never_overwrite(
    tmp_path: Path,
) -> None:
    workflow, run = _run(tmp_path)
    evidence = _business(workflow, run)
    persist_business_approval_evidence(run.store, evidence)
    original = _business_path(run, evidence.approval_id).read_bytes()

    persist_business_approval_evidence(run.store, evidence)
    assert _business_path(run, evidence.approval_id).read_bytes() == original

    conflicting = replace(evidence, approved_by="different-operator")
    with pytest.raises(WorkflowApprovalEvidenceConflictError):
        persist_business_approval_evidence(run.store, conflicting)
    assert _business_path(run, evidence.approval_id).read_bytes() == original


def test_business_evidence_binds_progression_and_run_identity(
    tmp_path: Path,
) -> None:
    workflow, run = _run(tmp_path, workflow=_workflow(step_count=2))
    evidence = _business(
        workflow,
        run,
        step_index=2,
        approval_id="business-step-2",
        progression_from_step_id="step-1",
        progression_from_step_index=1,
    )
    validate_business_approval_evidence(
        evidence,
        binding=run.binding,
        workflow_id=workflow.id,
        step_id="step-2",
        step_index=2,
        employee_id="employee-2",
        progression_from_step_id="step-1",
        progression_from_step_index=1,
    )

    with pytest.raises(WorkflowApprovalEvidenceError):
        validate_business_approval_evidence(
            evidence,
            binding=run.binding,
            workflow_id=workflow.id,
            step_id="step-2",
            step_index=2,
            employee_id="employee-2",
            progression_from_step_id="wrong-predecessor",
            progression_from_step_index=1,
        )

    _, other_run = _run(tmp_path, "run-2", workflow=workflow)
    other_path = _business_path(other_run, evidence.approval_id)
    other_path.write_bytes(business_approval_evidence_canonical_bytes(evidence))
    with pytest.raises(WorkflowApprovalEvidenceLoadError):
        load_business_approval_evidence(
            other_run.store, other_run.binding.run_id, evidence.approval_id
        )


@pytest.mark.parametrize("mutation", ["newline", "duplicate", "unknown"])
def test_business_evidence_load_is_strict_and_rejects_noncanonical_bytes(
    tmp_path: Path, mutation: str
) -> None:
    workflow, run = _run(tmp_path, "run-1")
    evidence = _business(workflow, run, approval_id=f"business-{mutation}")
    persist_business_approval_evidence(run.store, evidence)
    path = _business_path(run, evidence.approval_id)
    canonical = path.read_bytes()
    if mutation == "newline":
        path.write_bytes(canonical + b"\n")
    elif mutation == "duplicate":
        path.write_bytes(b'{"approved":true,"approved":true}')
    else:
        value = json.loads(canonical)
        value["unexpected"] = "field"
        path.write_bytes(
            json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        )

    with pytest.raises(WorkflowApprovalEvidenceLoadError):
        load_business_approval_evidence(
            run.store, run.binding.run_id, evidence.approval_id
        )


def test_execution_evidence_binds_exact_request_and_target_and_is_restart_readable(
    tmp_path: Path,
) -> None:
    workflow, run = _run(tmp_path)
    evidence = _execution(workflow, run)
    persist_execution_approval_evidence(run.store, evidence)
    restarted_store = type(run.store)(run.store.root)
    loaded = load_execution_approval_evidence(
        restarted_store, run.binding.run_id, evidence.approval_id
    )

    assert loaded == evidence
    assert execution_approval_evidence_canonical_bytes(loaded) == (
        _execution_path(run, evidence.approval_id).read_bytes()
    )
    validate_execution_approval_evidence(
        loaded,
        binding=run.binding,
        workflow_id=workflow.id,
        step_id="step-1",
        step_index=1,
        employee_id="employee-1",
        provider="openai",
        execution_target_fingerprint_value=execution_target_fingerprint(
            DIRECT_OPENAI_EXECUTION_TARGET
        ),
        request_fingerprint=evidence.request_fingerprint,
    )
    with pytest.raises(WorkflowApprovalEvidenceError):
        validate_execution_approval_evidence(
            loaded,
            binding=run.binding,
            workflow_id=workflow.id,
            step_id="step-1",
            step_index=1,
            employee_id="employee-1",
            provider="openai",
            execution_target_fingerprint_value=execution_target_fingerprint(
                DIRECT_OPENAI_EXECUTION_TARGET
            ),
            request_fingerprint="0" * 64,
        )


def test_execution_evidence_preserves_nonempty_metadata_with_safe_storage_key(
    tmp_path: Path,
) -> None:
    workflow, run = _run(tmp_path)
    evidence = _execution(
        workflow,
        run,
        approved_by="operator with space/日本語",
        approval_id="execution approval/日本語",
    )

    persist_execution_approval_evidence(run.store, evidence)
    restarted_store = type(run.store)(run.store.root)

    assert (
        load_execution_approval_evidence(
            restarted_store, run.binding.run_id, evidence.approval_id
        )
        == evidence
    )
    paths = tuple(
        run.store.root.glob(f"{run.binding.run_id}.execution-approval.*.json")
    )
    assert len(paths) == 1
    assert evidence.approval_id not in paths[0].name


def test_business_and_execution_purposes_are_separate_and_inspectable(
    tmp_path: Path,
) -> None:
    workflow, run = _run(tmp_path)
    business = _business(workflow, run)
    execution = _execution(workflow, run)
    persist_business_approval_evidence(run.store, business)
    persist_execution_approval_evidence(run.store, execution)

    values = list_run_approval_evidence(run.store, run.binding.run_id)
    assert values == (business, execution)
    assert {value.purpose for value in values} == {
        "business_approval",
        "execution_approval",
    }

    execution_path = _execution_path(run, execution.approval_id)
    execution_path.write_bytes(business_approval_evidence_canonical_bytes(business))
    with pytest.raises(WorkflowApprovalEvidenceLoadError):
        load_execution_approval_evidence(
            run.store, run.binding.run_id, execution.approval_id
        )


def test_evidence_persistence_rejects_symlink_without_following_or_overwriting(
    tmp_path: Path,
) -> None:
    workflow, run = _run(tmp_path)
    evidence = _business(workflow, run)
    target = tmp_path / "outside.json"
    target.write_bytes(b"outside")
    path = _business_path(run, evidence.approval_id)
    path.symlink_to(target)

    with pytest.raises(WorkflowApprovalEvidencePersistenceError):
        persist_business_approval_evidence(run.store, evidence)
    assert target.read_bytes() == b"outside"


def test_cli_inspects_only_strict_run_bound_evidence(tmp_path: Path) -> None:
    workflow, run = _run(tmp_path)
    business = _business(workflow, run)
    execution = _execution(workflow, run)
    persist_business_approval_evidence(run.store, business)
    persist_execution_approval_evidence(run.store, execution)

    result = runner.invoke(
        app,
        [
            "workflows",
            "approval-evidence",
            run.binding.run_id,
            "--run-store",
            str(run.store.root),
        ],
    )

    assert result.exit_code == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["run_id"] == run.binding.run_id
    assert value["manifest_digest"] == run.binding.manifest_digest
    assert [item["purpose"] for item in value["approvals"]] == [
        "business_approval",
        "execution_approval",
    ]
    assert "synthetic-api-key" not in result.stdout
