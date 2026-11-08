"""Behavioral coverage for immutable Run-bound workflow Artifacts."""

from __future__ import annotations

import base64
import difflib
import json
import re
import subprocess
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
from ai_office.execution_evidence import (
    execution_normalized_result_evidence_path,
    execution_raw_response_evidence_path,
    list_run_execution_evidence,
    load_normalized_result_evidence,
)
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
    load_workflow_execution_history,
    persist_workflow_execution_transition,
)
from tests._execution_evidence_test_support import create_test_execution_evidence

runner = CliRunner()


def _workflow(
    *,
    artifact_content_type: str | None = "text/markdown",
    model_output_format: str | None = None,
):
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
                    "model": "model",
                    "instructions": "Write the assigned output.",
                    "business_approval_required": False,
                    "artifact_content_type": artifact_content_type,
                    "model_output_format": model_output_format,
                },
                {
                    "id": "review",
                    "name": "Review",
                    "employee": "author",
                    "model": "model",
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
                    "role": "assistant",
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
    model_output_format: str | None = None,
    output: str = "# Exact\n\nResult ✓",
    status_code: int = 200,
    run_input: str | None = None,
    captured_requests: list[object] | None = None,
):
    workflow = _workflow(
        artifact_content_type=artifact_content_type,
        model_output_format=model_output_format,
    )
    employee = _employee()
    request = ModelInvocationRequest(
        model=workflow.steps[0].model,
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
        run_input=run_input,
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

    def transport(request: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(1)
        if captured_requests is not None:
            captured_requests.append(request)
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
        artifact.raw_response_evidence_sha256 == normalized.raw_response_evidence_sha256
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


@pytest.mark.parametrize("fenced", [False, True])
def test_file_change_set_artifact_canonicalizes_one_json_object(
    tmp_path: Path, fenced: bool
) -> None:
    change_set = {
        "changes": [
            {
                "content_base64": "cHJpbnQoMSkK",
                "before_sha256": "b" * 64,
                "path": "src/example.py",
            }
        ],
        "schema_version": "file-change-set.v1",
        "base_revision": "a" * 40,
    }
    json_text = json.dumps(change_set, ensure_ascii=False, indent=2)
    output = f"```json\n{json_text}\n```" if fenced else json_text
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        output=output,
    )
    _commit_success(workflow, evidence, result)

    decision = _route(workflow, evidence)
    artifacts = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)
    normalized = load_normalized_result_evidence(
        evidence.run.store.root,
        evidence.run.binding.run_id,
        artifacts[0].execution_attempt_id,
    )

    assert decision.decision == "prepare_next_step"
    assert calls == [1]
    assert len(artifacts) == 1
    artifact = artifacts[0]
    expected = artifact_module._canonical_json(change_set).encode("utf-8")
    assert artifact.content == expected
    assert json.loads(artifact.content) == change_set
    assert artifact.content_sha256 == sha256(expected).hexdigest()
    assert normalized.text == output
    assert artifact.normalized_result_evidence_sha256 == normalized.digest
    assert (
        artifact.raw_response_evidence_sha256
        == normalized.raw_response_evidence_sha256
    )


def _diff_run_input(
    sources: dict[str, str], revision: str = "a" * 40
) -> str:
    return json.dumps(
        {
            "repository": {"base_revision": revision},
            "allowed_paths": {"read": sorted(sources), "write": sorted(sources)},
            "source_context": sources,
            "source_sha256": {
                path: sha256(content.encode("utf-8")).hexdigest()
                for path, content in sources.items()
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _unified_diff(sources: dict[str, str], replacements: dict[str, str]) -> str:
    sections = []
    for path in sorted(replacements):
        sections.extend(
            difflib.unified_diff(
                sources[path].splitlines(keepends=True),
                replacements[path].splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                n=3,
            )
        )
    return "".join(sections)


def _adjust_hunk_counts(patch: str, delta: int) -> str:
    lines = patch.splitlines()
    pattern = re.compile(r"^(@@ -\d+),(\d+) (\+\d+),(\d+)( @@.*)$")
    adjusted = 0
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match is None:
            continue
        lines[index] = (
            f"{match.group(1)},{int(match.group(2)) + delta} "
            f"{match.group(3)},{int(match.group(4)) + delta}{match.group(5)}"
        )
        adjusted += 1
    assert adjusted
    return "\n".join(lines) + ("\n" if patch.endswith("\n") else "")


@pytest.mark.parametrize(
    "hunk_count_delta",
    [0, 1, -1],
    ids=["correct-counts", "header-overstates-body", "body-exceeds-header"],
)
def test_unified_diff_artifact_applies_to_fixed_sources_and_keeps_raw_evidence(
    tmp_path: Path, hunk_count_delta: int
) -> None:
    sources = {
        "src/example.py": "def answer():\n    return 1\n",
        "tests/test_example.py": "def test_answer():\n    assert answer() == 1\n",
    }
    replacements = {
        "src/example.py": "def answer():\n    return 2\n",
        "tests/test_example.py": "def test_answer():\n    assert answer() == 2\n",
    }
    output = _unified_diff(sources, replacements)
    if hunk_count_delta:
        output = _adjust_hunk_counts(output, hunk_count_delta)
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input(sources),
    )
    _commit_success(workflow, evidence, result)

    decision = _route(workflow, evidence)
    artifact = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)[0]
    normalized = load_normalized_result_evidence(
        evidence.run.store.root,
        evidence.run.binding.run_id,
        artifact.execution_attempt_id,
    )
    value = json.loads(artifact.content)
    changes = {change["path"]: change for change in value["changes"]}

    assert decision.decision == "prepare_next_step"
    assert calls == [1]
    assert normalized.text == output
    assert value["schema_version"] == "file-change-set.v1"
    assert value["base_revision"] == "a" * 40
    assert set(changes) == set(replacements)
    for path, content in replacements.items():
        assert changes[path]["before_sha256"] == sha256(
            sources[path].encode("utf-8")
        ).hexdigest()
        assert base64.b64decode(changes[path]["content_base64"]) == content.encode(
            "utf-8"
        )
    assert artifact.content_sha256 == sha256(artifact.content).hexdigest()
    assert artifact.normalized_result_evidence_sha256 == normalized.digest
    assert (
        artifact.raw_response_evidence_sha256
        == normalized.raw_response_evidence_sha256
    )


def test_unified_diff_artifact_accepts_outer_diff_fence_and_preserves_evidence(
    tmp_path: Path,
) -> None:
    path = "src/example.py"
    sources = {path: "def answer():\n    return 1\n"}
    replacement = "def answer():\n    return 2\n"
    patch = _unified_diff(sources, {path: replacement})
    output = f"```diff\n{patch}```\n"
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input(sources),
    )
    _commit_success(workflow, evidence, result)
    attempts = list_run_execution_evidence(
        evidence.run.store.root, evidence.run.binding.run_id
    )
    assert len(attempts) == 1
    attempt_id = attempts[0].attempt_id
    raw_path = execution_raw_response_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    normalized_path = execution_normalized_result_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    evidence_before = raw_path.read_bytes(), normalized_path.read_bytes()

    decision = _route(workflow, evidence)

    artifacts = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)
    normalized = load_normalized_result_evidence(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    change = json.loads(artifacts[0].content)["changes"][0]
    assert decision.decision == "prepare_next_step"
    assert calls == [1]
    assert len(artifacts) == 1
    assert normalized.text == output
    assert (raw_path.read_bytes(), normalized_path.read_bytes()) == evidence_before
    assert (
        artifacts[0].raw_response_evidence_sha256
        == normalized.raw_response_evidence_sha256
    )
    assert change["path"] == path
    assert change["before_sha256"] == sha256(sources[path].encode()).hexdigest()
    assert base64.b64decode(change["content_base64"]) == replacement.encode()


def test_unified_diff_artifact_reanchors_unique_hunk_to_fixed_preimage(
    tmp_path: Path,
) -> None:
    path = "src/example.py"
    sources = {
        path: "# header\n\ndef target():\n    return 1\n\nafter = 2\n"
    }
    output = (
        f"--- a/{path}\n+++ b/{path}\n"
        "@@ -4,3 +4,3 @@\n def target():\n"
        "-    return 1\n+    return 2\n \n"
    )
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input(sources),
    )
    _commit_success(workflow, evidence, result)
    attempts = list_run_execution_evidence(
        evidence.run.store.root, evidence.run.binding.run_id
    )
    assert len(attempts) == 1
    attempt_id = attempts[0].attempt_id
    raw_path = execution_raw_response_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    normalized_path = execution_normalized_result_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    evidence_before = (raw_path.read_bytes(), normalized_path.read_bytes())

    decision = _route(workflow, evidence)

    artifacts = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)
    normalized = load_normalized_result_evidence(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    assert decision.decision == "prepare_next_step"
    assert calls == [1]
    assert len(artifacts) == 1
    assert normalized.text == output
    assert (raw_path.read_bytes(), normalized_path.read_bytes()) == evidence_before
    change = json.loads(artifacts[0].content)["changes"][0]
    assert change["path"] == path
    assert change["before_sha256"] == sha256(sources[path].encode()).hexdigest()
    assert base64.b64decode(change["content_base64"]) == (
        b"# header\n\ndef target():\n    return 2\n\nafter = 2\n"
    )


@pytest.mark.parametrize(
    ("source", "output", "expected"),
    [
        (
            "header\fother\nbefore\nold\nafter\n",
            "--- a/src/example.py\n+++ b/src/example.py\n"
            "@@ -2,3 +2,3 @@\n before\n-old\n+new\n after\n",
            b"header\fother\nbefore\nnew\nafter\n",
        ),
        (
            "before\r\nold\r\nafter\r\n",
            "--- a/src/example.py\r\n+++ b/src/example.py\r\n"
            "@@ -1,3 +1,3 @@\r\n before\r\n-old\r\n+new\r\n after\r\n",
            b"before\r\nnew\r\nafter\r\n",
        ),
    ],
    ids=["form-feed-is-not-a-line-break", "crlf-source-and-patch"],
)
def test_unified_diff_artifact_uses_lf_line_boundaries(
    tmp_path: Path, source: str, output: str, expected: bytes
) -> None:
    path = "src/example.py"
    sources = {path: source}
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input(sources),
    )
    _commit_success(workflow, evidence, result)
    attempts = list_run_execution_evidence(
        evidence.run.store.root, evidence.run.binding.run_id
    )
    assert len(attempts) == 1
    attempt_id = attempts[0].attempt_id
    raw_path = execution_raw_response_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    normalized_path = execution_normalized_result_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    evidence_before = (raw_path.read_bytes(), normalized_path.read_bytes())

    decision = _route(workflow, evidence)

    artifacts = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)
    normalized = load_normalized_result_evidence(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    assert decision.decision == "prepare_next_step"
    assert calls == [1]
    assert len(artifacts) == 1
    assert normalized.text == output
    assert (raw_path.read_bytes(), normalized_path.read_bytes()) == evidence_before
    change = json.loads(artifacts[0].content)["changes"][0]
    assert change["path"] == path
    assert change["before_sha256"] == sha256(source.encode()).hexdigest()
    assert base64.b64decode(change["content_base64"]) == expected


def test_unified_diff_artifact_preserves_terminal_cr_before_no_newline_marker(
    tmp_path: Path,
) -> None:
    path = "src/example.py"
    source = b"before\nold\r"
    replacement = b"before\nnew\r"
    repository = tmp_path / "git-diff"
    target = repository / path
    target.parent.mkdir(parents=True)
    target.write_bytes(source)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Offline Test"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "user.email",
            "offline@example.invalid",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "core.autocrlf", "false"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "add", "--", path], check=True
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", "baseline"],
        check=True,
    )
    target.write_bytes(replacement)
    git_output = subprocess.run(
        ["git", "-C", str(repository), "diff", "--", path],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    output = git_output[git_output.index(f"--- a/{path}\n") :]
    assert (
        b"-old\r\n\\ No newline at end of file\n+new\r\n"
        b"\\ No newline at end of file\n"
        in output.encode("utf-8")
    )

    source_text = source.decode("utf-8")
    workflow, evidence, calls, result = _start_fixture(
        tmp_path / "artifact",
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input({path: source_text}),
    )
    _commit_success(workflow, evidence, result)
    attempts = list_run_execution_evidence(
        evidence.run.store.root, evidence.run.binding.run_id
    )
    assert len(attempts) == 1
    attempt_id = attempts[0].attempt_id
    raw_path = execution_raw_response_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    normalized_path = execution_normalized_result_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    evidence_before = (raw_path.read_bytes(), normalized_path.read_bytes())

    decision = _route(workflow, evidence)

    artifacts = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)
    normalized = load_normalized_result_evidence(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    assert decision.decision == "prepare_next_step"
    assert calls == [1]
    assert len(artifacts) == 1
    assert normalized.text == output
    assert (raw_path.read_bytes(), normalized_path.read_bytes()) == evidence_before
    change = json.loads(artifacts[0].content)["changes"][0]
    assert change["path"] == path
    assert change["before_sha256"] == sha256(source).hexdigest()
    assert base64.b64decode(change["content_base64"]) == replacement


@pytest.mark.parametrize(
    ("source", "context"),
    [
        (
            "begin\nvalue = 1\nfinish\nmiddle\nbegin\nvalue = 1\nfinish\n",
            "begin\n-value = 1\n+value = 2\n finish\n",
        ),
        (
            "begin\nvalue = 1\nfinish\n",
            "missing\n-value = 1\n+value = 2\n finish\n",
        ),
    ],
    ids=["ambiguous-preimage", "missing-preimage"],
)
def test_unified_diff_artifact_rejects_offset_without_unique_preimage(
    tmp_path: Path, source: str, context: str
) -> None:
    path = "src/example.py"
    output = (
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -2,3 +2,3 @@\n {context}"
    )
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input({path: source}),
    )
    _commit_success(workflow, evidence, result)

    with pytest.raises(PersistedExecutionOutcomeRoutingError) as error:
        _route(workflow, evidence)

    assert error.value.detail.classification == "file_change_set_diff"
    assert calls == [1]
    assert not tuple(
        tmp_path.glob(f"{evidence.run.binding.run_id}.artifact.*.json")
    )


def test_unified_diff_artifact_keeps_declared_third_repeated_preimage(
    tmp_path: Path,
) -> None:
    path = "src/example.py"
    source = (
        "begin\nvalue = 1\nfinish\nseparator one\n"
        "begin\nvalue = 1\nfinish\nseparator two\n"
        "begin\nvalue = 1\nfinish\n"
    )
    output = (
        f"--- a/{path}\n+++ b/{path}\n"
        "@@ -9,3 +9,3 @@\n begin\n"
        "-value = 1\n+value = 2\n finish\n"
    )
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input({path: source}),
    )
    _commit_success(workflow, evidence, result)

    decision = _route(workflow, evidence)

    artifacts = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)
    change = json.loads(artifacts[0].content)["changes"][0]
    assert decision.decision == "prepare_next_step"
    assert calls == [1]
    assert len(artifacts) == 1
    assert change["path"] == path
    assert base64.b64decode(change["content_base64"]) == (
        b"begin\nvalue = 1\nfinish\nseparator one\n"
        b"begin\nvalue = 1\nfinish\nseparator two\n"
        b"begin\nvalue = 2\nfinish\n"
    )


def test_unified_diff_artifact_keeps_correct_multi_hunk_positions(
    tmp_path: Path,
) -> None:
    path = "src/example.py"
    source = "top\nold one\nmiddle\nspacer\nother\nold two\nlast\nend\n"
    output = (
        f"--- a/{path}\n+++ b/{path}\n"
        "@@ -1,3 +1,3 @@\n top\n-old one\n+new one\n middle\n"
        "@@ -5,3 +5,3 @@\n other\n-old two\n+new two\n last\n"
    )
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input({path: source}),
    )
    _commit_success(workflow, evidence, result)

    decision = _route(workflow, evidence)

    artifacts = list_run_artifacts(evidence.run.store, evidence.run.binding.run_id)
    change = json.loads(artifacts[0].content)["changes"][0]
    assert decision.decision == "prepare_next_step"
    assert calls == [1]
    assert len(artifacts) == 1
    assert base64.b64decode(change["content_base64"]) == (
        b"top\nnew one\nmiddle\nspacer\nother\nnew two\nlast\nend\n"
    )


def test_unified_diff_artifact_derives_insert_count_from_hunk_body(
    tmp_path: Path,
) -> None:
    sources = {"src/example.py": "def answer():\n    return 1\n"}
    output = (
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n"
        "-    return 1\n+    return 2\n+# candidate note\n"
    )
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input(sources),
    )
    _commit_success(workflow, evidence, result)
    attempts = list_run_execution_evidence(
        evidence.run.store.root, evidence.run.binding.run_id
    )
    assert len(attempts) == 1
    attempt_id = attempts[0].attempt_id
    raw_path = execution_raw_response_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    normalized_path = execution_normalized_result_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt_id
    )
    evidence_before = (raw_path.read_bytes(), normalized_path.read_bytes())

    decision = _route(workflow, evidence)

    artifacts = list_run_artifacts(
        evidence.run.store, evidence.run.binding.run_id
    )
    normalized = load_normalized_result_evidence(
        evidence.run.store.root,
        evidence.run.binding.run_id,
        attempt_id,
    )
    assert decision.decision == "prepare_next_step"
    assert calls == [1]
    assert len(artifacts) == 1
    assert normalized.text == output
    assert (raw_path.read_bytes(), normalized_path.read_bytes()) == evidence_before
    change = json.loads(artifacts[0].content)["changes"][0]
    assert change["path"] == "src/example.py"
    assert base64.b64decode(change["content_base64"]) == (
        b"def answer():\n    return 2\n# candidate note\n"
    )


def test_unified_diff_artifact_adds_terminal_lf_only_for_git(
    tmp_path: Path,
) -> None:
    sources = {
        "src/example.py": "def answer():\n    return 1\n",
        "tests/test_example.py": "def test_answer():\n    assert answer() == 1\n",
    }
    replacements = {
        "src/example.py": "def answer():\n    return 2\n",
        "tests/test_example.py": "def test_answer():\n    assert answer() == 2\n",
    }
    output_with_lf = _unified_diff(sources, replacements)
    assert output_with_lf.endswith("\n")
    outputs = (
        ("with-lf", output_with_lf),
        ("without-lf", output_with_lf.removesuffix("\n")),
    )
    artifact_contents = []

    for label, output in outputs:
        workflow, evidence, calls, result = _start_fixture(
            tmp_path / label,
            artifact_content_type="application/vnd.ai-office.file-change-set+json",
            model_output_format="unified-diff",
            output=output,
            run_input=_diff_run_input(sources),
        )
        _commit_success(workflow, evidence, result)
        attempts = list_run_execution_evidence(
            evidence.run.store.root, evidence.run.binding.run_id
        )
        assert len(attempts) == 1
        attempt_id = attempts[0].attempt_id
        raw_path = execution_raw_response_evidence_path(
            evidence.run.store.root, evidence.run.binding.run_id, attempt_id
        )
        normalized_path = execution_normalized_result_evidence_path(
            evidence.run.store.root, evidence.run.binding.run_id, attempt_id
        )
        evidence_before = (raw_path.read_bytes(), normalized_path.read_bytes())

        decision = _route(workflow, evidence)

        artifacts = list_run_artifacts(
            evidence.run.store, evidence.run.binding.run_id
        )
        assert decision.decision == "prepare_next_step"
        assert calls == [1]
        assert len(artifacts) == 1
        artifact = artifacts[0]
        normalized = load_normalized_result_evidence(
            evidence.run.store.root,
            evidence.run.binding.run_id,
            attempt_id,
        )
        assert normalized.text == output
        assert (raw_path.read_bytes(), normalized_path.read_bytes()) == evidence_before
        assert artifact.normalized_result_evidence_sha256 == normalized.digest
        assert (
            artifact.raw_response_evidence_sha256
            == normalized.raw_response_evidence_sha256
        )
        artifact_contents.append(artifact.content)

    assert artifact_contents[0] == artifact_contents[1]
    value = json.loads(artifact_contents[0])
    assert value["schema_version"] == "file-change-set.v1"
    assert {
        change["path"] for change in value["changes"]
    } == set(replacements)
    assert {
        change["path"]: base64.b64decode(change["content_base64"])
        for change in value["changes"]
    } == {path: content.encode("utf-8") for path, content in replacements.items()}


def test_unified_diff_artifact_rejects_invalid_patch_after_terminal_lf(
    tmp_path: Path,
) -> None:
    sources = {"src/example.py": "def answer():\n    return 1\n"}
    output_without_lf = (
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def not_answer():\n"
        "-    return 1\n+    return 2"
    )
    assert not output_without_lf.endswith("\n")
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output_without_lf,
        run_input=_diff_run_input(sources),
    )
    _commit_success(workflow, evidence, result)

    with pytest.raises(PersistedExecutionOutcomeRoutingError) as error:
        _route(workflow, evidence)

    assert error.value.detail.classification == "file_change_set_diff"
    assert calls == [1]
    assert not tuple(
        tmp_path.glob(f"{evidence.run.binding.run_id}.artifact.*.json")
    )


def test_unified_diff_artifact_rejects_internal_payload_over_local_limit(
    tmp_path: Path,
) -> None:
    source = "x" * 800_000 + "\nold\nkeep\n"
    output = (
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -2,2 +2,2 @@\n-old\n+new\n keep\n"
    )
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input({"src/example.py": source}),
    )
    _commit_success(workflow, evidence, result)

    with pytest.raises(PersistedExecutionOutcomeRoutingError) as error:
        _route(workflow, evidence)

    assert error.value.detail.classification == "file_change_set_diff"
    assert calls == [1]
    assert not tuple(
        tmp_path.glob(f"{evidence.run.binding.run_id}.artifact.*.json")
    )


@pytest.mark.parametrize(
    "output",
    [
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n    return 1\n-    return 1\n+    return 2\n",
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n not-the-source\n-    return 1\n+    return 2\n",
        "--- a/not-allowed.py\n+++ b/not-allowed.py\n"
        "@@ -1,1 +1,1 @@\n-old\n+new\n",
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n"
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 3\n",
        "diff --git a/src/example.py b/src/src-renamed.py\n"
        "similarity index 100%\nrename from src/example.py\n"
        "rename to src/src-renamed.py\n",
        "diff --git a/src/example.py b/src/example.py\n"
        "old mode 100644\nnew mode 100755\n",
        "diff --git a/src/example.py b/src/example.py\n"
        "deleted file mode 100644\nindex e69de29..0000000\n"
        "--- a/src/example.py\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n-def answer():\n-    return 1\n",
        "diff --git a/src/new.py b/src/new.py\n"
        "new file mode 100644\nindex 0000000..3e75765\n"
        "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+new\n",
        "Explanation of the change:\n"
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n",
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n"
        "Explanation of the change.\n",
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n"
        " This is explanation text\n",
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n"
        "-This is explanation text\n",
        "```diff\n"
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n"
        "```\ntrailing text\n",
        "```diff\n--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n````\n",
        "```python\n--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n```\n",
        "```diff\n--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n",
        "```diff\n--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n```\n"
        "```diff\n--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n```\n",
        "diff --git a/src/example.py b/src/example.py\n"
        "index 0123456..abcdef0 100644\n"
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n",
    ],
    ids=[
        "offset",
        "mismatched-context",
        "out-of-scope",
        "duplicate-path",
        "rename",
        "mode-change",
        "delete",
        "create",
        "explanation-preamble",
        "explanation-trailer",
        "space-prefixed-hunk-trailer",
        "deletion-prefixed-hunk-trailer",
        "trailing-text-after-fence",
        "four-backtick-fence",
        "wrong-fence-language",
        "unclosed-fence",
        "multiple-fences",
        "git-metadata",
    ],
)
def test_unified_diff_artifact_rejects_unsafe_or_ambiguous_patches(
    tmp_path: Path, output: str
) -> None:
    sources = {"src/example.py": "def answer():\n    return 1\n"}
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input(sources),
    )
    _commit_success(workflow, evidence, result)
    attempt = list_run_execution_evidence(
        evidence.run.store.root, evidence.run.binding.run_id
    )[0]
    raw_path = execution_raw_response_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt.attempt_id
    )
    normalized_path = execution_normalized_result_evidence_path(
        evidence.run.store.root, evidence.run.binding.run_id, attempt.attempt_id
    )
    evidence_before = raw_path.read_bytes(), normalized_path.read_bytes()

    with pytest.raises(PersistedExecutionOutcomeRoutingError) as error:
        _route(workflow, evidence)

    assert error.value.detail.classification == "file_change_set_diff"
    assert calls == [1]
    assert (raw_path.read_bytes(), normalized_path.read_bytes()) == evidence_before
    assert not tuple(
        tmp_path.glob(f"{evidence.run.binding.run_id}.artifact.*.json")
    )


@pytest.mark.parametrize("failure", ["git-process", "temporary-io"])
def test_unified_diff_artifact_runtime_failures_keep_dependency_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    sources = {"src/example.py": "def answer():\n    return 1\n"}
    output = (
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n def answer():\n-    return 1\n+    return 2\n"
    )
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input(sources),
    )
    _commit_success(workflow, evidence, result)

    if failure == "git-process":
        original_run = subprocess.run

        def fail_git_apply(command, *args, **kwargs):
            if command[:2] == ["git", "apply"]:
                raise FileNotFoundError("synthetic missing git executable")
            return original_run(command, *args, **kwargs)

        monkeypatch.setattr(artifact_module.subprocess, "run", fail_git_apply)
    else:
        original_write_bytes = Path.write_bytes

        def fail_temporary_write(path: Path, content: bytes) -> int:
            if any(part.startswith("ai-office-unified-diff-") for part in path.parts):
                raise OSError("synthetic temporary-file write failure")
            return original_write_bytes(path, content)

        monkeypatch.setattr(Path, "write_bytes", fail_temporary_write)

    with pytest.raises(PersistedExecutionOutcomeRoutingError) as error:
        _route(workflow, evidence)

    assert error.value.detail.classification == "dependency_error"
    assert calls == [1]
    assert not tuple(
        tmp_path.glob(f"{evidence.run.binding.run_id}.artifact.*.json")
    )


def test_unified_diff_artifact_checks_all_hunks_before_producing_artifact(
    tmp_path: Path,
) -> None:
    sources = {"src/example.py": "a\nb\nc\nd\ne\nf\n"}
    output = (
        "--- a/src/example.py\n+++ b/src/example.py\n"
        "@@ -1,2 +1,2 @@\n-a\n+A\n b\n"
        "@@ -5,2 +5,2 @@\n-not-e\n+E\n f\n"
    )
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=output,
        run_input=_diff_run_input(sources),
    )
    _commit_success(workflow, evidence, result)

    with pytest.raises(PersistedExecutionOutcomeRoutingError) as error:
        _route(workflow, evidence)

    assert error.value.detail.classification == "file_change_set_diff"
    assert calls == [1]
    assert not tuple(
        tmp_path.glob(f"{evidence.run.binding.run_id}.artifact.*.json")
    )


def test_unified_diff_artifact_rejects_binary_patch(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "binary-patch"
    (repository / "src").mkdir(parents=True)
    source = b"old\n"
    path = repository / "src/example.py"
    path.write_bytes(source)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Offline Test"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "user.email",
            "offline@example.invalid",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "add", "--", "src/example.py"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", "baseline"],
        check=True,
    )
    path.write_bytes(b"\x00new\n")
    patch = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "diff",
            "--binary",
            "--full-index",
            "--",
            "src/example.py",
        ],
        capture_output=True,
        check=True,
    ).stdout.decode("ascii")
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        model_output_format="unified-diff",
        output=patch,
        run_input=_diff_run_input({"src/example.py": source.decode("ascii")}),
    )
    _commit_success(workflow, evidence, result)

    with pytest.raises(PersistedExecutionOutcomeRoutingError) as error:
        _route(workflow, evidence)

    assert error.value.detail.classification == "file_change_set_diff"
    assert calls == [1]
    assert not tuple(
        tmp_path.glob(f"{evidence.run.binding.run_id}.artifact.*.json")
    )


@pytest.mark.parametrize(
    "output",
    [
        '```json\n{"schema_version":"file-change-set.v1",}\n```',
        '```json\n{"schema_version":"file-change-set.v1"}\n``` trailing text',
        '{"schema_version":"file-change-set.v1","schema_version":"other"}',
    ],
)
def test_file_change_set_artifact_rejects_invalid_or_ambiguous_json(
    tmp_path: Path, output: str
) -> None:
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        output=output,
    )
    _commit_success(workflow, evidence, result)

    with pytest.raises(PersistedExecutionOutcomeRoutingError):
        _route(workflow, evidence)

    assert calls == [1]
    assert not tuple(
        tmp_path.glob(f"{evidence.run.binding.run_id}.artifact.*.json")
    )


def test_legacy_file_change_set_artifact_remains_readable_without_rewrite(
    tmp_path: Path,
) -> None:
    output = '```json\n{"schema_version":"file-change-set.v1","changes":}\n```'
    workflow, evidence, calls, result = _start_fixture(
        tmp_path,
        artifact_content_type="application/vnd.ai-office.file-change-set+json",
        output=output,
    )
    _commit_success(workflow, evidence, result)
    store = evidence.run.store
    run_id = evidence.run.binding.run_id
    manifest = load_workflow_run_manifest(store, run_id)
    history = load_workflow_execution_history(store.execution_targets(run_id))
    event = next(item for item in history.events if item.event_type == "step_succeeded")

    legacy_artifact = artifact_module._artifact_from_success_event(
        store,
        manifest,
        event,
        canonicalize_file_change_set=False,
    )
    artifact_module._persist_workflow_artifact(store, legacy_artifact)
    path = workflow_artifact_path(store, run_id, legacy_artifact.artifact_id)
    artifact_bytes_before = path.read_bytes()
    namespace_before = _run_namespace_snapshot(store.root)

    assert (
        read_run_artifact(store, run_id, legacy_artifact.artifact_id)
        == legacy_artifact
    )
    decision = _route(workflow, evidence)

    assert decision.decision == "prepare_next_step"
    assert calls == [1]
    assert list_run_artifacts(store, run_id) == (legacy_artifact,)
    assert path.read_bytes() == artifact_bytes_before
    assert _run_namespace_snapshot(store.root) == namespace_before


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
    workflow, evidence, calls, result = _start_fixture(tmp_path, status_code=500)
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
        second_evidence.run.store.root / f"run-other.artifact.{first.artifact_id}.json"
    )
    copied_path.write_bytes(first_record.read_bytes())

    with pytest.raises(WorkflowArtifactError):
        list_run_artifacts(
            second_evidence.run.store, second_evidence.run.binding.run_id
        )
