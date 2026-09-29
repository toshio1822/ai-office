"""Behavioral tests for the immutable Workflow Run Manifest contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from ai_office.definitions.employee import (
    EmployeeDefinition,
    LoadedEmployee,
    load_employees,
)
from ai_office.definitions.workflow import (
    LoadedWorkflow,
    WorkflowDefinition,
    WorkflowLoadError,
    WorkflowStepDefinition,
    load_workflows,
)
from ai_office.engine.workflow_run_manifest import (
    ToolContractSnapshot,
    WorkflowRunManifestConflictError,
    WorkflowRunManifestError,
    WorkflowRunManifestLoadError,
    build_workflow_run_manifest,
    create_workflow_run_manifest,
    load_workflow_run_manifest,
    persist_workflow_run_manifest,
    serialize_workflow_run_manifest_canonical,
    workflow_run_manifest_canonical_bytes,
    workflow_run_manifest_digest,
)
from ai_office.tools import (
    ToolCatalog,
    ToolDefinition,
    ToolNotFoundError,
    ToolParameterDefinition,
)


def employee(
    employee_id: str = "researcher",
    *,
    role: str = "Researches the assigned subject.",
    instructions: str = "Work only on the assigned step.",
    allowed_tools: tuple[str, ...] = (),
) -> LoadedEmployee:
    return LoadedEmployee(
        source_path=Path(f"source-{employee_id}.yaml"),
        definition=EmployeeDefinition(
            id=employee_id,
            name=employee_id.replace("-", " ").title(),
            role=role,
            instructions=instructions,
            model="codex",
            allowed_tools=list(allowed_tools),
        ),
    )


def workflow(
    employee_id: str = "researcher",
    *,
    description: str = "Researches the assigned subject.",
    step_instructions: str = "Gather relevant information.",
) -> LoadedWorkflow:
    return LoadedWorkflow(
        source_path=Path("source-workflow.yaml"),
        definition=WorkflowDefinition(
            id="research-workflow",
            name="Research Workflow",
            description=description,
            steps=[
                WorkflowStepDefinition(
                    id="research",
                    name="Research",
                    employee=employee_id,
                    instructions=step_instructions,
                )
            ],
        ),
    )


def tool_catalog(
    *,
    description: str = "Search the web.",
    parameter_description: str = "The search query.",
) -> ToolCatalog:
    return ToolCatalog(
        tools=(
            ToolDefinition(
                name="web_search",
                description=description,
                parameters=(
                    ToolParameterDefinition(
                        name="query",
                        description=parameter_description,
                        type="string",
                        required=True,
                    ),
                ),
            ),
        )
    )


def manifest(
    *,
    run_id: str = "run-1",
    run_input: str = "Research Company ABC.",
    workflow_value: LoadedWorkflow | None = None,
    employees: tuple[LoadedEmployee, ...] = (),
    catalog: ToolCatalog | None = None,
):
    return build_workflow_run_manifest(
        run_id,
        run_input,
        workflow_value or workflow(),
        employees or (employee(),),
        tool_catalog=catalog or ToolCatalog(tools=()),
    )


def test_create_load_round_trip_preserves_exact_run_meaning_and_digest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.manifest.json"
    value = create_workflow_run_manifest(
        path,
        "run-1",
        "  preserve this input exactly\n",
        workflow(),
        [employee()],
    )

    loaded = load_workflow_run_manifest(path)
    contents = path.read_bytes()

    assert loaded == value
    assert loaded.run_input == "  preserve this input exactly\n"
    assert contents == workflow_run_manifest_canonical_bytes(value)
    assert contents == serialize_workflow_run_manifest_canonical(value).encode("utf-8")
    assert not contents.endswith(b"\n")
    assert value.digest == workflow_run_manifest_digest(value)
    assert value.digest == hashlib.sha256(contents).hexdigest()
    assert b"source-workflow.yaml" not in contents
    assert b"source-researcher.yaml" not in contents
    assert set(json.loads(contents)) == {
        "employee_snapshots",
        "run_id",
        "run_input",
        "schema_version",
        "tool_contracts",
        "workflow_id",
        "workflow_snapshot",
    }


def test_semantically_identical_values_have_identical_canonical_bytes() -> None:
    first = manifest(
        employees=(employee(), employee("unrelated")),
    )
    second = manifest(
        employees=(
            LoadedEmployee(
                source_path=Path("a-different-source.yml"),
                definition=EmployeeDefinition(
                    id="researcher",
                    name="Researcher",
                    role="Researches the assigned subject.",
                    instructions="Work only on the assigned step.",
                    model="codex",
                    allowed_tools=[],
                ),
            ),
        ),
    )

    assert workflow_run_manifest_canonical_bytes(first) == (
        workflow_run_manifest_canonical_bytes(second)
    )
    assert first.digest == second.digest


def test_independent_runs_can_pin_one_workflow_with_different_input() -> None:
    first = manifest(run_id="run-1", run_input="first request")
    second = manifest(run_id="run-2", run_input="second request")

    assert first.workflow_snapshot == second.workflow_snapshot
    assert first.run_input != second.run_input
    assert first.run_id != second.run_id
    assert first.digest != second.digest


def test_meaningful_workflow_employee_and_tool_changes_change_identity() -> None:
    baseline = manifest(
        employees=(employee(allowed_tools=("web_search",)),),
        catalog=tool_catalog(),
    )
    changed_workflow = manifest(
        workflow_value=workflow(step_instructions="Use the supplied evidence."),
        employees=(employee(allowed_tools=("web_search",)),),
        catalog=tool_catalog(),
    )
    changed_employee = manifest(
        employees=(
            employee(
                allowed_tools=("web_search",),
                instructions="Use a different fixed instruction.",
            ),
        ),
        catalog=tool_catalog(),
    )
    changed_tool = manifest(
        employees=(employee(allowed_tools=("web_search",)),),
        catalog=tool_catalog(description="A changed provider-independent contract."),
    )

    assert baseline.digest != changed_workflow.digest
    assert baseline.digest != changed_employee.digest
    assert baseline.digest != changed_tool.digest
    assert changed_tool.tool_contracts == (
        ToolContractSnapshot(
            name="web_search",
            description="A changed provider-independent contract.",
            parameters=(changed_tool.tool_contracts[0].parameters[0],),
        ),
    )


def test_unrelated_employee_definitions_do_not_affect_pinned_manifest() -> None:
    without_unrelated = manifest(employees=(employee(),))
    with_unrelated = manifest(
        employees=(employee(), employee("unrelated", role="Not referenced.")),
    )

    assert with_unrelated == without_unrelated
    assert [item.id for item in with_unrelated.employee_snapshots] == ["researcher"]


def test_source_yaml_changes_after_creation_do_not_reinterpret_loaded_manifest(
    tmp_path: Path,
) -> None:
    workflows_path = tmp_path / "workflows"
    employees_path = tmp_path / "employees"
    workflows_path.mkdir()
    employees_path.mkdir()
    workflow_path = workflows_path / "research.yaml"
    employee_path = employees_path / "researcher.yaml"
    workflow_path.write_text(
        yaml.safe_dump(
            {
                "id": "research-workflow",
                "name": "Research Workflow",
                "description": "Original meaning.",
                "steps": [
                    {
                        "id": "research",
                        "name": "Research",
                        "employee": "researcher",
                        "instructions": "Original step.",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    employee_path.write_text(
        yaml.safe_dump(
            {
                "id": "researcher",
                "name": "Researcher",
                "role": "Original role.",
                "instructions": "Original employee instructions.",
                "model": "codex",
                "allowed_tools": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    run_path = tmp_path / "run.manifest.json"
    original = create_workflow_run_manifest(
        run_path,
        "run-1",
        "same request",
        load_workflows(workflows_path)[0],
        load_employees(employees_path),
    )

    workflow_path.write_text(
        workflow_path.read_text(encoding="utf-8").replace(
            "Original meaning.", "Changed live meaning."
        ),
        encoding="utf-8",
    )
    employee_path.write_text(
        employee_path.read_text(encoding="utf-8").replace(
            "Original role.", "Changed live role."
        ),
        encoding="utf-8",
    )

    assert load_workflow_run_manifest(run_path) == original


def test_invalid_employee_reference_fails_before_manifest_persistence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.manifest.json"

    with pytest.raises(WorkflowLoadError):
        create_workflow_run_manifest(
            path,
            "run-1",
            "request",
            workflow(employee_id="missing-employee"),
            [employee()],
        )

    assert not path.exists()


def test_invalid_tool_reference_fails_before_manifest_persistence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.manifest.json"

    with pytest.raises(ToolNotFoundError):
        create_workflow_run_manifest(
            path,
            "run-1",
            "request",
            workflow(),
            [employee(allowed_tools=("missing-tool",))],
        )

    assert not path.exists()


def test_exact_re_persistence_is_idempotent_and_does_not_rewrite_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.manifest.json"
    value = manifest()
    persist_workflow_run_manifest(path, value)
    original_bytes = path.read_bytes()

    persist_workflow_run_manifest(path, value)

    assert path.read_bytes() == original_bytes
    assert load_workflow_run_manifest(path) == value


def test_conflicting_existing_content_fails_closed_without_overwrite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.manifest.json"
    original = manifest(run_input="original")
    conflicting = manifest(run_input="different")
    persist_workflow_run_manifest(path, original)
    original_bytes = path.read_bytes()

    with pytest.raises(WorkflowRunManifestConflictError):
        persist_workflow_run_manifest(path, conflicting)

    assert path.read_bytes() == original_bytes
    assert load_workflow_run_manifest(path) == original


@pytest.mark.parametrize(
    "contents",
    [
        b"{",
        b"not json",
    ],
)
def test_malformed_persisted_data_fails_closed(tmp_path: Path, contents: bytes) -> None:
    path = tmp_path / "run.manifest.json"
    path.write_bytes(contents)

    with pytest.raises(WorkflowRunManifestLoadError):
        load_workflow_run_manifest(path)


def test_duplicate_and_noncanonical_persisted_structure_fails_closed(
    tmp_path: Path,
) -> None:
    value = manifest()
    canonical = workflow_run_manifest_canonical_bytes(value)
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_bytes(
        canonical.replace(
            b'"run_id":"run-1"',
            b'"run_id":"run-1","run_id":"run-1"',
        )
    )
    noncanonical_path = tmp_path / "noncanonical.json"
    noncanonical_path.write_bytes(canonical + b"\n")

    with pytest.raises(WorkflowRunManifestLoadError):
        load_workflow_run_manifest(duplicate_path)
    with pytest.raises(WorkflowRunManifestLoadError):
        load_workflow_run_manifest(noncanonical_path)


def test_manifest_constructor_rejects_unbound_or_invalid_values() -> None:
    value = manifest()

    with pytest.raises(WorkflowRunManifestError):
        type(value)(
            schema_version=value.schema_version,
            run_id=value.run_id,
            workflow_id="different-workflow",
            run_input=value.run_input,
            workflow_snapshot=value.workflow_snapshot,
            employee_snapshots=value.employee_snapshots,
            tool_contracts=value.tool_contracts,
        )
