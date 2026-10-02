"""Provider-free helpers for Run-bound execution boundary tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_office.definitions.employee import EmployeeDefinition, LoadedEmployee
from ai_office.definitions.workflow import LoadedWorkflow, WorkflowDefinition
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
) -> TestRun:
    """Create or reload one exact Manifest-backed test Run."""
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
    state_path, events_path = store.execution_paths(run_id)
    return TestRun(store, binding, state_path, events_path)
