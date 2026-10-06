"""Behavioral coverage for immutable Run-bound workflow Artifacts."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.engine.artifact as artifact_module
from ai_office.cli import app
from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.artifact import (
    WorkflowArtifactError,
    export_run_artifact,
    list_run_artifacts,
    read_run_artifact,
    workflow_artifact_path,
)
from ai_office.engine.persisted_execution_outcome_routing_reentry import (
    PersistedExecutionOutcomeRoutingError,
    route_persisted_execution_outcome_reentry,
)
from ai_office.engine.workflow_run_manifest import (
    load_workflow_run_manifest,
    workflow_definition_from_run_manifest,
)
from ai_office.execution_evidence import load_normalized_result_evidence
from ai_office.execution_target import DIRECT_OPENAI_EXECUTION_TARGET
from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
    execute_openai_model_invocation,
)
from ai_office.runtime import (
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    transition_workflow_execution_from_step_result,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    persist_workflow_execution_transition,
)
from tests._execution_evidence_test_support import create_test_execution_evidence

runner = CliRunner()


def _workflow(*, artifact_content_type: str | None = "text/markdown"):
    return WorkflowDefinition.model_validate(
        {
            "id": "artifact-workflow",
            "name": "Artifact Workflow",
            "description": "A deterministic Artifact fixture.",
            "steps": [
                {
                    "id": "draft",
                    "name": "Draft",
                    "employee": "author",
                    "instructions": "Write the assigned output.",
                    "business_approval_required": False,
                    "artifact_content_type": artifact_content_type,
                },
                {
                    "id": "review",
                    "name": "Review",
                    "employee": "author",
                    "instructions": "Review the output.",
                    "business_approval_required": False,
                },
            ],
        }
    )


def _employee() -> EmployeeDefinition:
    return EmployeeDefinition(
        id="author",
        name="Author",
        role="Writes business results.",
        instructions="Write the assigned result.",
        model="artifact-test-model",
        allowed_tools=[],
    )


def _success_body(text: str = "# Exact\n\nResult ✓") -> bytes:
    return json.dumps(
        {
            "id": "response-artifact",
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _start_fixture(
    root: Path,
    *,
    run_id: str = "run-artifact",
    artifact_content_type: str | None = "text/markdown",
    output: str = "# Exact\n\nResult ✓",
    status_code: int = 200,
):
    workflow = _workflow(artifact_content_type=artifact_content_type)
    employee = _employee()
    request = ModelInvocationRequest(
        model=employee.model,
        system_instructions=employee.instructions,
        task_instructions=workflow.steps[0].instructions,
        allowed_tools=(),
    )
    evidence = create_test_execution_evidence(
        root,
        run_id=run_id,
        workflow=workflow,
        employees=(employee,),
        request=request,
        resolved_tools=(),
        step_id="draft",
        target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    calls: list[int] = []
    response = OpenAIResponsesRawHttpResponse(
        status_code=status_code,
        reason="synthetic",
        headers=(
            ("content-type", "application/json"),
            ("x-request-id", "artifact-request"),
            ("authorization", "Bearer response-secret"),
        ),
        body=(
            _success_body(output)
            if status_code == 200
            else b'{"error":{"message":"safe failure","type":"api_error"}}'
        ),
    )

    def transport(_request: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(1)
        return response

    result = execute_openai_model_invocation(
        evidence.request,
        (),
        OpenAIApiKey(value=SecretStr("artifact-provider-key")),
        evidence.approval,
        transport=transport,
        execution_evidence=evidence.context,
    )
    return workflow, evidence, calls, result


def _commit_success(workflow, evidence, result) -> None:
    transition = transition_workflow_execution_from_step_result(
        WorkflowExecutionState(
            workflow.id,
            "running",
            workflow.steps[0].id,
            1,
            workflow.steps[0].employee,
            (),
            None,
            binding=evidence.run.binding,
        ),
        StepRuntimeExecutionSuccess(
            workflow.id,
            workflow.steps[0].id,
            1,
            workflow.steps[0].employee,
            result,
            binding=evidence.run.binding,
        ),
    )
    persist_workflow_execution_transition(
        transition,
        WorkflowExecutionPersistenceTargets(
            evidence.run.state_path,
            evidence.run.events_path,
            binding=evidence.run.binding,
        ),
    )


def _route(workflow, evidence):
    return route_persisted_execution_outcome_reentry(
        workflow, evidence.run.state_path, evidence.run.events_path
    )


def _run_namespace_snapshot(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    """Capture every Run-namespace entry and exact bytes for read-only checks."""
    snapshot: dict[str, tuple[str, bytes | str | None]] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", path.readlink().as_posix())
        elif path.is_file():
            snapshot[relative] = ("file", path.read_bytes())
        elif path.is_dir():
            snapshot[relative] = ("directory", None)
    return snapshot


def test_configured_success_creates_exact_immutable_artifact_from_result_evidence(
    tmp_path: Path,
) -> None:
    workflow, evidence, calls, result = _start_fixture(tmp_path)
    _commit_success(workflow, evidence, result)

    decision = _route(workflow, evidence)
    artifacts = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)
    attempt = evidence.run.store.root
    normalized = load_normalized_result_evidence(
        attempt, evidence.run.binding.run_id, artifacts[0].execution_attempt_id
    )

    assert decision.decision == "prepare_next_step"
    assert len(calls) == 1
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.content == normalized.text.encode("utf-8")
    assert artifact.content == "# Exact\n\nResult ✓".encode()
    assert artifact.content_type == "text/markdown"
    assert artifact.content_length == len(artifact.content)
    assert artifact.content_sha256 == sha256(artifact.content).hexdigest()
    assert artifact.manifest_digest == evidence.run.binding.manifest_digest
    assert artifact.run_id == evidence.run.binding.run_id
    assert artifact.step_id == "draft"
    assert artifact.step_index == 1
    assert artifact.employee_id == "author"
    assert artifact.normalized_result_evidence_sha256 == normalized.digest
    assert (
        artifact.raw_response_evidence_sha256
        == normalized.raw_response_evidence_sha256
    )
    assert artifact.raw_response_body_sha256 == normalized.raw_response_body_sha256
    assert workflow_artifact_path(
        evidence.run.store, evidence.run.binding.run_id, artifact.artifact_id
    ).is_file()
    persisted_bytes = workflow_artifact_path(
        evidence.run.store, evidence.run.binding.run_id, artifact.artifact_id
    ).read_bytes()
    assert b"artifact-provider-key" not in persisted_bytes
    assert b"Bearer response-secret" not in persisted_bytes
    assert b"authorization" not in persisted_bytes.lower()


def test_missing_artifact_is_completed_after_restart_without_provider_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, evidence, calls, result = _start_fixture(tmp_path)
    _commit_success(workflow, evidence, result)
    real_link = artifact_module.os.link

    def fail_artifact_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic Artifact commit failure")

    monkeypatch.setattr(artifact_module.os, "link", fail_artifact_link)
    with pytest.raises(PersistedExecutionOutcomeRoutingError):
        _route(workflow, evidence)

    state_before = evidence.run.state_path.read_bytes()
    events_before = evidence.run.events_path.read_bytes()
    assert b'"status":"succeeded"' in state_before
    assert b'"event_type":"step_succeeded"' in events_before
    assert list_run_artifacts(evidence.run.store, evidence.run.binding.run_id) == ()
    assert not tuple(tmp_path.glob("run-artifact.artifact.*.json"))
    assert calls == [1]

    monkeypatch.setattr(artifact_module.os, "link", real_link)

    namespace_before = _run_namespace_snapshot(evidence.run.store.root)
    with pytest.raises(PersistedExecutionOutcomeRoutingError):
        route_persisted_execution_outcome_reentry(
            workflow,
            evidence.run.state_path,
            evidence.run.events_path,
            allow_artifact_completion=False,
        )
    assert _run_namespace_snapshot(evidence.run.store.root) == namespace_before
    assert evidence.run.state_path.read_bytes() == state_before
    assert evidence.run.events_path.read_bytes() == events_before
    assert list_run_artifacts(evidence.run.store, evidence.run.binding.run_id) == ()
    assert calls == [1]

    preview = runner.invoke(
        app,
        [
            "workflows",
            "continue",
            evidence.run.binding.run_id,
            "--run-store",
            str(evidence.run.store.root),
            "--preview-only",
        ],
    )
    assert preview.exit_code == 2
    assert "persisted workflow state requires recovery or investigation" in (
        preview.stderr
    )
    assert _run_namespace_snapshot(evidence.run.store.root) == namespace_before
    assert evidence.run.state_path.read_bytes() == state_before
    assert evidence.run.events_path.read_bytes() == events_before
    assert list_run_artifacts(evidence.run.store, evidence.run.binding.run_id) == ()
    assert calls == [1]

    inspected = runner.invoke(
        app,
        [
            "workflows",
            "result",
            evidence.run.binding.run_id,
            "--run-store",
            str(evidence.run.store.root),
        ],
    )
    assert inspected.exit_code == 2
    assert "persisted workflow state requires recovery or investigation" in (
        inspected.stderr
    )
    assert _run_namespace_snapshot(evidence.run.store.root) == namespace_before
    assert evidence.run.state_path.read_bytes() == state_before
    assert evidence.run.events_path.read_bytes() == events_before
    assert list_run_artifacts(evidence.run.store, evidence.run.binding.run_id) == ()
    assert calls == [1]

    continued = runner.invoke(
        app,
        [
            "workflows",
            "continue",
            evidence.run.binding.run_id,
            "--run-store",
            str(evidence.run.store.root),
        ],
    )
    assert continued.exit_code == 2
    namespace_after_continuation = _run_namespace_snapshot(evidence.run.store.root)
    added_paths = set(namespace_after_continuation) - set(namespace_before)
    assert len(added_paths) == 1
    artifact_path = next(iter(added_paths))
    assert artifact_path.startswith(f"{evidence.run.binding.run_id}.artifact.")
    assert all(
        namespace_after_continuation[path] == value
        for path, value in namespace_before.items()
    )
    assert evidence.run.state_path.read_bytes() == state_before
    assert evidence.run.events_path.read_bytes() == events_before
    assert len(list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)) == 1
    assert calls == [1]

    decision = _route(workflow, evidence)

    artifacts = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)
    assert decision.decision == "prepare_next_step"
    assert len(artifacts) == 1
    assert calls == [1]
    assert evidence.run.state_path.read_bytes() == state_before
    assert evidence.run.events_path.read_bytes() == events_before

    assert calls == [1]


def test_non_artifact_success_keeps_normal_progression_without_artifact(
    tmp_path: Path,
) -> None:
    workflow, evidence, calls, result = _start_fixture(
        tmp_path, artifact_content_type=None
    )
    _commit_success(workflow, evidence, result)

    decision = _route(workflow, evidence)

    assert decision.decision == "prepare_next_step"
    assert len(calls) == 1
    assert list_run_artifacts(evidence.run.store, evidence.run.binding.run_id) == ()


def test_failed_configured_step_does_not_create_artifact(
    tmp_path: Path,
) -> None:
    workflow, evidence, calls, result = _start_fixture(
        tmp_path, status_code=500
    )
    assert result.category == "api_error"

    # A failed step has no successful business result to commit as an Artifact.
    from ai_office.runtime import StepRuntimeExecutionFailure

    transition = transition_workflow_execution_from_step_result(
        WorkflowExecutionState(
            workflow.id,
            "running",
            workflow.steps[0].id,
            1,
            workflow.steps[0].employee,
            (),
            None,
            binding=evidence.run.binding,
        ),
        StepRuntimeExecutionFailure(
            workflow.id,
            workflow.steps[0].id,
            1,
            workflow.steps[0].employee,
            result,
            binding=evidence.run.binding,
        ),
    )
    persist_workflow_execution_transition(
        transition,
        WorkflowExecutionPersistenceTargets(
            evidence.run.state_path,
            evidence.run.events_path,
            binding=evidence.run.binding,
        ),
    )

    outcome = _route(workflow, evidence)

    assert outcome.outcome == "persisted_failure"
    assert calls == [1]
    assert list_run_artifacts(evidence.run.store, evidence.run.binding.run_id) == ()


def test_artifact_read_list_and_local_export_are_provider_free_and_no_overwrite(
    tmp_path: Path,
) -> None:
    workflow, evidence, calls, result = _start_fixture(tmp_path)
    _commit_success(workflow, evidence, result)
    _route(workflow, evidence)
    artifact = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)[0]

    listed = runner.invoke(
        app,
        [
            "workflows",
            "artifacts",
            evidence.run.binding.run_id,
            "--run-store",
            str(evidence.run.store.root),
        ],
    )
    assert listed.exit_code == 0, listed.stderr
    listed_json = json.loads(listed.stdout)
    assert listed_json["artifacts"][0]["artifact_id"] == artifact.artifact_id
    assert listed_json["artifacts"][0]["consistent_with_execution_evidence"] is True
    assert "# Exact" not in listed.stdout
    assert "content_base64" not in listed.stdout

    read = runner.invoke(
        app,
        [
            "workflows",
            "artifact",
            evidence.run.binding.run_id,
            artifact.artifact_id,
            "--run-store",
            str(evidence.run.store.root),
        ],
    )
    assert read.exit_code == 0, read.stderr
    assert json.loads(read.stdout)["artifact"]["content"] == "# Exact\n\nResult ✓"

    destination = tmp_path / "exports" / "draft.md"
    destination.parent.mkdir()
    receipt = export_run_artifact(
        evidence.run.store,
        evidence.run.binding.run_id,
        artifact.artifact_id,
        destination,
    )
    assert receipt.content_sha256 == artifact.content_sha256
    assert destination.read_bytes() == artifact.content
    assert calls == [1]

    cli_destination = destination.parent / "cli-draft.md"
    exported = runner.invoke(
        app,
        [
            "workflows",
            "artifact-export",
            evidence.run.binding.run_id,
            artifact.artifact_id,
            "--output",
            str(cli_destination),
            "--run-store",
            str(evidence.run.store.root),
        ],
    )
    assert exported.exit_code == 0, exported.stderr
    assert json.loads(exported.stdout)["operation"] == "artifact-export"
    assert cli_destination.read_bytes() == artifact.content
    assert calls == [1]

    with pytest.raises(WorkflowArtifactError):
        export_run_artifact(
            evidence.run.store,
            evidence.run.binding.run_id,
            artifact.artifact_id,
            destination,
        )
    assert destination.read_bytes() == artifact.content
    assert calls == [1]


def test_corrupt_artifact_fails_closed_without_advancing_success(
    tmp_path: Path,
) -> None:
    workflow, evidence, _calls, result = _start_fixture(tmp_path)
    _commit_success(workflow, evidence, result)
    _route(workflow, evidence)
    artifact = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)[0]
    path = workflow_artifact_path(
        evidence.run.store, evidence.run.binding.run_id, artifact.artifact_id
    )
    path.write_bytes(b"{")
    state_before = evidence.run.state_path.read_bytes()
    events_before = evidence.run.events_path.read_bytes()

    with pytest.raises(PersistedExecutionOutcomeRoutingError):
        _route(workflow, evidence)
    with pytest.raises(WorkflowArtifactError):
        read_run_artifact(
            evidence.run.store, evidence.run.binding.run_id, artifact.artifact_id
        )

    assert evidence.run.state_path.read_bytes() == state_before
    assert evidence.run.events_path.read_bytes() == events_before


def test_artifact_policy_is_pinned_and_live_definition_is_not_reloaded(
    tmp_path: Path,
) -> None:
    workflow, evidence, _calls, result = _start_fixture(tmp_path)
    manifest = load_workflow_run_manifest(
        evidence.run.store, evidence.run.binding.run_id
    )
    pinned_workflow = workflow_definition_from_run_manifest(manifest)
    changed_live_workflow = _workflow(artifact_content_type="text/plain")
    _commit_success(workflow, evidence, result)

    decision = _route(changed_live_workflow, evidence)
    artifact = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)[0]

    assert pinned_workflow.steps[0].artifact_content_type == "text/markdown"
    assert changed_live_workflow.steps[0].artifact_content_type == "text/plain"
    assert artifact.content_type == "text/markdown"
    assert decision.decision == "prepare_next_step"


def test_changed_artifact_bytes_cannot_reuse_the_existing_identity(
    tmp_path: Path,
) -> None:
    workflow, evidence, _calls, result = _start_fixture(tmp_path)
    _commit_success(workflow, evidence, result)
    _route(workflow, evidence)
    artifact = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)[0]
    with pytest.raises(WorkflowArtifactError):
        replace(artifact, content=b"changed content")


def test_changed_result_and_provenance_get_a_distinct_artifact_identity(
    tmp_path: Path,
) -> None:
    first_workflow, first_evidence, _first_calls, first_result = _start_fixture(
        tmp_path / "first"
    )
    second_workflow, second_evidence, _second_calls, second_result = _start_fixture(
        tmp_path / "second", run_id="run-other", output="Changed result"
    )
    _commit_success(first_workflow, first_evidence, first_result)
    _commit_success(second_workflow, second_evidence, second_result)
    _route(first_workflow, first_evidence)
    _route(second_workflow, second_evidence)

    first = list_run_artifacts(
        first_evidence.run.store, first_evidence.run.binding.run_id
    )[0]
    second = list_run_artifacts(
        second_evidence.run.store, second_evidence.run.binding.run_id
    )[0]

    assert first.content != second.content
    assert (
        first.normalized_result_evidence_sha256
        != second.normalized_result_evidence_sha256
    )
    assert first.artifact_id != second.artifact_id


def test_cross_run_artifact_lineage_is_rejected(tmp_path: Path) -> None:
    first_workflow, first_evidence, _first_calls, first_result = _start_fixture(
        tmp_path / "first"
    )
    second_workflow, second_evidence, _second_calls, second_result = _start_fixture(
        tmp_path / "second", run_id="run-other"
    )
    _commit_success(first_workflow, first_evidence, first_result)
    _commit_success(second_workflow, second_evidence, second_result)
    _route(first_workflow, first_evidence)
    _route(second_workflow, second_evidence)
    first = list_run_artifacts(
        first_evidence.run.store, first_evidence.run.binding.run_id
    )[0]
    first_record = workflow_artifact_path(
        first_evidence.run.store,
        first_evidence.run.binding.run_id,
        first.artifact_id,
    )
    copied_path = (
        second_evidence.run.store.root
        / f"run-other.artifact.{first.artifact_id}.json"
    )
    copied_path.write_bytes(first_record.read_bytes())

    with pytest.raises(WorkflowArtifactError):
        list_run_artifacts(
            second_evidence.run.store, second_evidence.run.binding.run_id
        )
