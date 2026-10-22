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
    WorkflowRunManifestStore,
    build_workflow_run_manifest,
    create_workflow_run_manifest,
    load_workflow_run_manifest,
    persist_workflow_run_manifest,
    serialize_workflow_run_manifest_canonical,
    workflow_definition_from_run_manifest,
    workflow_run_manifest_canonical_bytes,
    workflow_run_manifest_digest,
)
from ai_office.execution_destination import load_execution_destination_registry
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
            allowed_tools=list(allowed_tools),
        ),
    )


def workflow(
    employee_id: str = "researcher",
    *,
    description: str = "Researches the assigned subject.",
    step_instructions: str = "Gather relevant information.",
    artifact_content_type: str | None = None,
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
                    model="model",
                    instructions=step_instructions,
                    artifact_content_type=artifact_content_type,
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
    store = WorkflowRunManifestStore(tmp_path)
    value = create_workflow_run_manifest(
        store,
        "run-1",
        "  preserve this input exactly\n",
        workflow(),
        [employee()],
    )

    loaded = load_workflow_run_manifest(store, "run-1")
    contents = (tmp_path / "run-1.manifest.json").read_bytes()

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
        "execution_destinations",
        "employee_snapshots",
        "run_id",
        "run_input",
        "schema_version",
        "tool_contracts",
        "workflow_id",
        "workflow_snapshot",
    }


def test_manifest_pins_selector_resolution_for_each_step(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "destinations.yaml"
    registry_path.write_text(
        yaml.safe_dump(
            {
                "destinations": {
                    "responses-target": {
                        "endpoint": "https://responses.example.com/v1/responses",
                        "protocol": "openai-responses",
                        "credential": "RESPONSES_API_KEY",
                        "models": ["responses-model"],
                    },
                    "chat-target": {
                        "endpoint": "https://chat.example.com/v1/chat/completions",
                        "protocol": "openai-chat-completions",
                        "credential": "CHAT_API_KEY",
                        "models": ["chat-model"],
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    registry = load_execution_destination_registry(registry_path)
    responses_target = registry.resolve("responses-target").target
    chat_target = registry.resolve("chat-target").target
    loaded = LoadedWorkflow(
        source_path=Path("source-workflow.yaml"),
        definition=WorkflowDefinition(
            id="research-workflow",
            name="Research Workflow",
            description="Researches the assigned subject.",
            execution_destination="responses-target",
            steps=[
                WorkflowStepDefinition(
                    id="research",
                    name="Research",
                    employee="researcher",
                    model="chat-model",
                    execution_destination="chat-target",
                    instructions="Gather relevant information.",
                ),
                WorkflowStepDefinition(
                    id="summarize",
                    name="Summarize",
                    employee="researcher",
                    model="responses-model",
                    instructions="Summarize the findings.",
                ),
            ],
        ),
    )

    value = build_workflow_run_manifest(
        "run-routed",
        "Research Company ABC.",
        loaded,
        [employee()],
        execution_targets=(chat_target, responses_target),
    )
    run_root = tmp_path / "runs"
    run_root.mkdir(mode=0o700)
    store = WorkflowRunManifestStore(run_root)
    persisted = create_workflow_run_manifest(
        store,
        "run-routed",
        "Research Company ABC.",
        loaded,
        [employee()],
        execution_targets=(chat_target, responses_target),
    )
    restored = load_workflow_run_manifest(store, "run-routed")

    assert value.schema_version == "workflow-run-manifest.v6"
    assert value.execution_destination_for_step(1).provider == "chat-target"
    assert value.execution_destination_for_step(2).provider == "responses-target"
    assert value.workflow_snapshot.execution_destination == "responses-target"
    assert value.workflow_snapshot.steps[0].execution_destination == "chat-target"
    assert value.schema_version == "workflow-run-manifest.v6"
    encoded = json.loads(workflow_run_manifest_canonical_bytes(value))
    assert "execution_destination" not in encoded
    assert [item["provider"] for item in encoded["execution_destinations"]] == [
        "chat-target",
        "responses-target",
    ]
    assert persisted == restored == value
    assert workflow_definition_from_run_manifest(restored) == loaded.definition


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


def test_artifact_policy_is_pinned_and_changes_manifest_identity() -> None:
    no_artifact = manifest()
    artifact = manifest(
        workflow_value=workflow(artifact_content_type="text/markdown")
    )

    assert no_artifact.workflow_snapshot.steps[0].artifact_content_type is None
    assert artifact.workflow_snapshot.steps[0].artifact_content_type == "text/markdown"
    assert no_artifact.digest != artifact.digest
    assert b'"artifact_content_type":"text/markdown"' in (
        workflow_run_manifest_canonical_bytes(artifact)
    )


def test_old_v5_manifest_is_rejected_without_migration(tmp_path: Path) -> None:
    store = WorkflowRunManifestStore(tmp_path)
    create_workflow_run_manifest(
        store, "run-1", "request", workflow(), [employee()]
    )
    value = json.loads(store.manifest_path("run-1").read_text(encoding="utf-8"))
    value["schema_version"] = "workflow-run-manifest.v5"
    value["execution_destination"] = value["execution_destinations"][0]
    del value["execution_destinations"]
    value["workflow_snapshot"].pop("execution_destination")
    for step in value["workflow_snapshot"]["steps"]:
        step.pop("execution_destination")
    store.manifest_path("run-1").write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowRunManifestLoadError):
        load_workflow_run_manifest(store, "run-1")


def test_new_run_manifest_always_pins_builtin_destination_and_model() -> None:
    value = manifest()
    encoded = json.loads(workflow_run_manifest_canonical_bytes(value))

    assert value.schema_version == "workflow-run-manifest.v6"
    destination = value.execution_destination_for_step(1)
    assert destination.provider == "openai"
    assert (
        destination.endpoint == "https://api.openai.com/v1/responses"
    )
    assert (
        destination.credential_environment_variable
        == "OPENAI_API_KEY"
    )
    assert encoded["execution_destinations"][0]["fingerprint"] == (
        destination.fingerprint
    )


def test_manifest_pins_model_per_step_without_employee_model() -> None:
    configured_workflow = workflow()
    configured_workflow.definition.steps.append(
        WorkflowStepDefinition(
            id="review",
            name="Review",
            employee="researcher",
            model="review-model",
            instructions="Review the research.",
        )
    )

    value = manifest(workflow_value=configured_workflow)
    encoded = json.loads(workflow_run_manifest_canonical_bytes(value))

    assert [step.model for step in value.workflow_snapshot.steps] == [
        "model",
        "review-model",
    ]
    assert "model" not in encoded["employee_snapshots"][0]
    assert [step["model"] for step in encoded["workflow_snapshot"]["steps"]] == [
        "model",
        "review-model",
    ]


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
                        "model": "model",
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
                "allowed_tools": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    store = WorkflowRunManifestStore(tmp_path)
    original = create_workflow_run_manifest(
        store,
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

    assert load_workflow_run_manifest(store, "run-1") == original


def test_invalid_employee_reference_fails_before_manifest_persistence(
    tmp_path: Path,
) -> None:
    store = WorkflowRunManifestStore(tmp_path)

    with pytest.raises(WorkflowLoadError):
        create_workflow_run_manifest(
            store,
            "run-1",
            "request",
            workflow(employee_id="missing-employee"),
            [employee()],
        )

    assert not (tmp_path / "run-1.manifest.json").exists()


def test_invalid_tool_reference_fails_before_manifest_persistence(
    tmp_path: Path,
) -> None:
    store = WorkflowRunManifestStore(tmp_path)

    with pytest.raises(ToolNotFoundError):
        create_workflow_run_manifest(
            store,
            "run-1",
            "request",
            workflow(),
            [employee(allowed_tools=("missing-tool",))],
        )

    assert not (tmp_path / "run-1.manifest.json").exists()


def test_exact_re_persistence_is_idempotent_and_does_not_rewrite_content(
    tmp_path: Path,
) -> None:
    store = WorkflowRunManifestStore(tmp_path)
    value = manifest()
    persist_workflow_run_manifest(store, value)
    original_bytes = (tmp_path / "run-1.manifest.json").read_bytes()

    persist_workflow_run_manifest(store, value)

    assert (tmp_path / "run-1.manifest.json").read_bytes() == original_bytes
    assert load_workflow_run_manifest(store, "run-1") == value


def test_same_run_identity_and_meaning_is_one_safe_idempotent_run(
    tmp_path: Path,
) -> None:
    store = WorkflowRunManifestStore(tmp_path)

    first = create_workflow_run_manifest(
        store, "run-1", "same request", workflow(), [employee()]
    )
    second = create_workflow_run_manifest(
        store, "run-1", "same request", workflow(), [employee()]
    )

    assert second == first
    assert load_workflow_run_manifest(store, "run-1") == first
    assert sorted(tmp_path.glob("*.manifest.json")) == [
        tmp_path / "run-1.manifest.json"
    ]


def test_alternate_caller_filename_cannot_create_a_second_authoritative_run(
    tmp_path: Path,
) -> None:
    store = WorkflowRunManifestStore(tmp_path)
    original = create_workflow_run_manifest(
        store, "run-1", "original request", workflow(), [employee()]
    )
    alternate_path = tmp_path / "caller-selected-alternate.json"
    alternate_path.write_bytes(
        workflow_run_manifest_canonical_bytes(
            manifest(run_id="run-1", run_input="different request")
        )
    )

    assert load_workflow_run_manifest(store, "run-1") == original
    assert load_workflow_run_manifest(store, "run-1").run_input == "original request"
    with pytest.raises(WorkflowRunManifestConflictError):
        create_workflow_run_manifest(
            store, "run-1", "different request", workflow(), [employee()]
        )


@pytest.mark.parametrize(
    ("run_input", "changed_workflow"),
    [
        ("different request", None),
        ("same request", workflow(step_instructions="Changed.")),
    ],
)
def test_same_run_identity_with_different_meaning_conflicts_without_overwrite(
    tmp_path: Path,
    run_input: str,
    changed_workflow: LoadedWorkflow | None,
) -> None:
    store = WorkflowRunManifestStore(tmp_path)
    original = create_workflow_run_manifest(
        store, "run-1", "same request", workflow(), [employee()]
    )
    original_bytes = (tmp_path / "run-1.manifest.json").read_bytes()

    with pytest.raises(WorkflowRunManifestConflictError):
        create_workflow_run_manifest(
            store,
            "run-1",
            run_input,
            changed_workflow or workflow(),
            [employee()],
        )

    assert (tmp_path / "run-1.manifest.json").read_bytes() == original_bytes
    assert load_workflow_run_manifest(store, "run-1") == original


def test_different_run_id_with_same_workflow_is_independent(
    tmp_path: Path,
) -> None:
    store = WorkflowRunManifestStore(tmp_path)

    first = create_workflow_run_manifest(
        store, "run-1", "first request", workflow(), [employee()]
    )
    second = create_workflow_run_manifest(
        store, "run-2", "second request", workflow(), [employee()]
    )

    assert first.workflow_snapshot == second.workflow_snapshot
    assert first.run_id != second.run_id
    assert first.run_input != second.run_input
    assert load_workflow_run_manifest(store, "run-1") == first
    assert load_workflow_run_manifest(store, "run-2") == second


def test_conflicting_existing_content_fails_closed_without_overwrite(
    tmp_path: Path,
) -> None:
    store = WorkflowRunManifestStore(tmp_path)
    original = manifest(run_input="original")
    conflicting = manifest(run_input="different")
    persist_workflow_run_manifest(store, original)
    original_bytes = (tmp_path / "run-1.manifest.json").read_bytes()

    with pytest.raises(WorkflowRunManifestConflictError):
        persist_workflow_run_manifest(store, conflicting)

    assert (tmp_path / "run-1.manifest.json").read_bytes() == original_bytes
    assert load_workflow_run_manifest(store, "run-1") == original


@pytest.mark.parametrize(
    "contents",
    [
        b"{",
        b"not json",
    ],
)
def test_malformed_persisted_data_fails_closed(tmp_path: Path, contents: bytes) -> None:
    store = WorkflowRunManifestStore(tmp_path)
    path = tmp_path / "run-1.manifest.json"
    path.write_bytes(contents)

    with pytest.raises(WorkflowRunManifestLoadError):
        load_workflow_run_manifest(store, "run-1")


def test_duplicate_and_noncanonical_persisted_structure_fails_closed(
    tmp_path: Path,
) -> None:
    value = manifest()
    canonical = workflow_run_manifest_canonical_bytes(value)
    store = WorkflowRunManifestStore(tmp_path)
    duplicate_path = tmp_path / "run-1.manifest.json"
    duplicate_path.write_bytes(
        canonical.replace(
            b'"run_id":"run-1"',
            b'"run_id":"run-1","run_id":"run-1"',
        )
    )
    noncanonical_path = tmp_path / "run-2.manifest.json"
    noncanonical_path.write_bytes(canonical + b"\n")

    with pytest.raises(WorkflowRunManifestLoadError):
        load_workflow_run_manifest(store, "run-1")
    with pytest.raises(WorkflowRunManifestLoadError):
        load_workflow_run_manifest(store, "run-2")


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
            execution_destinations=value.execution_destinations,
        )
