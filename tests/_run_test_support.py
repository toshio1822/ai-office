"""Provider-free helpers for Run-bound execution boundary tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_office.definitions.employee import EmployeeDefinition, LoadedEmployee
from ai_office.definitions.workflow import LoadedWorkflow, WorkflowDefinition
from ai_office.engine.workflow_approval_evidence import (
    approve_business_step,
    persist_business_approval_evidence,
)
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    create_workflow_run_manifest,
    load_workflow_run_manifest,
)
from ai_office.runtime import WorkflowRunBinding


@dataclass(frozen=True)
class TestRun:
    """One authoritative, provider-free Run fixture."""

    store: WorkflowRunManifestStore
    binding: WorkflowRunBinding
    state_path: Path
    events_path: Path


def create_test_run(
    root: Path,
    run_id: str,
    workflow: WorkflowDefinition,
    employees: tuple[EmployeeDefinition, ...],
    *,
    with_business_approvals: bool = True,
) -> TestRun:
    """Create or reload one exact Manifest-backed test Run.

    Provider-owning test fixtures opt in to the same explicit approval
    evidence that a real caller supplies.  The flag lets evidence-specific
    tests construct a Run with no Business Approval sidecars.
    """
    root.mkdir(parents=True, exist_ok=True)
    store = WorkflowRunManifestStore(root)
    manifest_path = store.manifest_path(run_id)
    if manifest_path.exists():
        manifest = load_workflow_run_manifest(store, run_id)
    else:
        manifest = create_workflow_run_manifest(
            store,
            run_id,
            f"input-{run_id}",
            LoadedWorkflow(root / "workflow.yaml", workflow),
            tuple(
                LoadedEmployee(root / f"{employee.id}.yaml", employee)
                for employee in employees
            ),
        )
    binding = WorkflowRunBinding(manifest.run_id, manifest.digest)
    if with_business_approvals:
        for index, step in enumerate(workflow.steps, 1):
            if not step.business_approval_required:
                continue
            previous = workflow.steps[index - 2] if index > 1 else None
            evidence = approve_business_step(
                binding=binding,
                workflow_id=workflow.id,
                step_id=step.id,
                step_index=index,
                employee_id=step.employee,
                approved_by="test-operator",
                approval_id=f"test-business-{run_id}-{step.id}",
                progression_from_step_id=None if previous is None else previous.id,
                progression_from_step_index=None if previous is None else index - 1,
            )
            persist_business_approval_evidence(store, evidence)
    state_path, events_path = store.execution_paths(run_id)
    return TestRun(store, binding, state_path, events_path)
