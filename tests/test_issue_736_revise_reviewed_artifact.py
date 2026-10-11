"""Offline proof for the one-step reviewed-Artifact revision Workflow."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import yaml
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.definitions.employee import load_employees
from ai_office.definitions.workflow import (
    load_workflows,
    validate_workflow_employee_references,
)
from ai_office.providers.openai import OpenAIApiKey, OpenAIResponsesRawHttpResponse

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]
EMPLOYEES = ROOT / "employees"
WORKFLOWS = ROOT / "workflows"
WORKFLOW_ID = "revise-reviewed-artifact"


def test_revision_input_reaches_writer_and_creates_a_new_artifact_offline(
    tmp_path: Path, monkeypatch
) -> None:
    registry = tmp_path / "destinations.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "destinations": {
                    "offline-revision": {
                        "protocol": "openai-responses",
                        "endpoint": "https://api.example.com/v1/responses",
                        "credential": "OFFLINE_REVISION_KEY",
                        "models": ["offline-writer"],
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    source_report = (
        "# Report\n\nConfirmed: mount points are enumerated.\n\n"
        "Unknown: cleanup call path and runtime outcome.\n"
    )
    review_content = (
        "Verdict: CHANGES_REQUIRED\n\n"
        "Finding: document the supplied unmount behavior and safety boundary.\n"
    )
    run_input = {
        "source_report": {
            "run_id": "source-run",
            "artifact_id": "source-report-artifact",
            "sha256": sha256(source_report.encode("utf-8")).hexdigest(),
            "content_type": "text/markdown",
            "content": source_report,
        },
        "review": {
            "run_id": "review-run",
            "artifact_id": "review-artifact",
            "sha256": sha256(review_content.encode("utf-8")).hexdigest(),
            "content_type": "text/markdown",
            "content": review_content,
            "accepted_findings": ["CONTENT-1"],
        },
        "requirements": [
            {
                "id": "CONTENT-1",
                "subject": "deliverable/content",
                "text": "Report lifecycle behavior and safety boundaries.",
            },
            {
                "id": "OPS-1",
                "subject": "operations",
                "text": "Stop after the approved bounded run.",
            },
        ],
        "evidence_package": {
            "package_id": "evidence-package",
            "sha256": "evidence-digest",
            "sources": [
                {
                    "source_id": "partition-service",
                    "excerpt": (
                        "unmount_device enumerates mount points and attempts unmount."
                    ),
                },
                {
                    "source_id": "workflow-config",
                    "excerpt": (
                        "cleanup call path is not included in the supplied evidence."
                    ),
                },
            ],
        },
    }
    input_file = tmp_path / "revision-input.json"
    input_file.write_text(json.dumps(run_input), encoding="utf-8")
    source_input_bytes = input_file.read_bytes()
    run_store = tmp_path / "runs"
    requests: list[object] = []
    payloads: list[object] = []
    credential_reads: list[object] = []

    workflows = load_workflows(WORKFLOWS)
    employees = load_employees(EMPLOYEES)
    validate_workflow_employee_references(workflows, employees)
    definition = next(
        item.definition for item in workflows if item.definition.id == WORKFLOW_ID
    )
    assert len(definition.steps) == 1
    step = definition.steps[0]
    assert step.id == "revise"
    assert step.employee == "japanese-document-writer"
    assert step.business_approval_required is True
    assert step.artifact_content_type == "text/markdown"

    def fake_key(target: object) -> OpenAIApiKey:
        credential_reads.append(target)
        return OpenAIApiKey(value=SecretStr("synthetic-only"))

    revised_report = (
        "# Report\n\nConfirmed: mount points are enumerated and unmount is "
        "attempted.\n\n"
        "Unknown: cleanup call path and runtime outcome.\n"
    )

    def fake_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        requests.append(request)
        payload = json.loads(request.body)
        payloads.append(payload)
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", "issue-736-offline"),),
            json.dumps(
                {
                    "id": "response-issue-736-offline",
                    "object": "response",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": revised_report}
                            ],
                        }
                    ],
                }
            ).encode("utf-8"),
        )

    monkeypatch.setattr(cli_module, "load_api_key_for_execution_target", fake_key)
    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", fake_transport
    )
    args = [
        "workflows",
        "start",
        WORKFLOW_ID,
        "--run-id",
        "issue-736-offline-revision",
        "--run-input-file",
        str(input_file),
        "--run-store",
        str(run_store),
        "--directory",
        str(WORKFLOWS),
        "--employees-directory",
        str(EMPLOYEES),
        "--execution-target",
        "offline-revision",
        "--execution-destinations",
        str(registry),
        "--execution-model",
        "offline-writer",
    ]

    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    assert preview["workflow_id"] == WORKFLOW_ID
    assert preview["step_id"] == "revise"
    assert preview["employee_id"] == "japanese-document-writer"
    assert preview["step_index"] == 1
    assert preview["business_approval_required"] is True
    assert preview["run_input"] == json.dumps(run_input)
    assert preview["model"] == "offline-writer"
    assert len(preview["request_fingerprint"]) == 64
    assert not run_store.exists()
    assert requests == []
    assert credential_reads == []
    assert input_file.read_bytes() == source_input_bytes

    executed = runner.invoke(
        app,
        args
        + [
            "--approve-business",
            "--business-approved-by",
            "offline-owner",
            "--business-approval-id",
            "issue-736-offline-business",
            "--approve-execution",
            "--execution-approved-by",
            "offline-operator",
            "--execution-approval-id",
            "issue-736-offline-execution",
            "--expected-step-id",
            str(preview["step_id"]),
            "--expected-step-index",
            str(preview["step_index"]),
            "--expected-employee-id",
            str(preview["employee_id"]),
            "--expected-request-fingerprint",
            str(preview["request_fingerprint"]),
        ],
    )
    assert executed.exit_code == 0, (executed.stderr, executed.stdout)
    assert json.loads(executed.stdout)["status"] == "workflow_complete"
    assert len(requests) == 1
    assert len(credential_reads) == 1
    assert len(payloads) == 1

    def flatten_json_strings(value: object) -> list[str]:
        if isinstance(value, dict):
            return [
                text for item in value.values() for text in flatten_json_strings(item)
            ]
        if isinstance(value, list):
            return [text for item in value for text in flatten_json_strings(item)]
        if isinstance(value, str):
            nested: list[str] = [value]
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return nested
            if decoded != value:
                nested.extend(flatten_json_strings(decoded))
            return nested
        return []

    flattened_request = "\n".join(flatten_json_strings(payloads[0]))
    assert payloads[0]["model"] == "offline-writer"
    for required_text in (
        source_report,
        review_content,
        "source-report-artifact",
        "review-artifact",
        "CONTENT-1",
        "evidence-package",
        "cleanup call path is not included",
    ):
        assert required_text in flattened_request
    assert "previous_response_id" not in payloads[0]
    assert "creator transcript" not in flattened_request.lower()
    assert input_file.read_bytes() == source_input_bytes

    artifacts = runner.invoke(
        app,
        [
            "workflows",
            "artifacts",
            "issue-736-offline-revision",
            "--run-store",
            str(run_store),
        ],
    )
    assert artifacts.exit_code == 0, artifacts.stderr
    artifact = json.loads(artifacts.stdout)["artifacts"][0]
    assert artifact["content_type"] == "text/markdown"
    assert artifact["artifact_id"] != run_input["source_report"]["artifact_id"]
    read = runner.invoke(
        app,
        [
            "workflows",
            "artifact",
            "issue-736-offline-revision",
            artifact["artifact_id"],
            "--run-store",
            str(run_store),
        ],
    )
    assert read.exit_code == 0, read.stderr
    saved_content = json.loads(read.stdout)["artifact"]["content"]
    assert saved_content == revised_report
    assert input_file.read_bytes() == source_input_bytes
    assert len(requests) == 1


def test_operator_note_keeps_issue_data_out_of_reusable_definition() -> None:
    workflow_text = (WORKFLOWS / "revise-reviewed-artifact.yaml").read_text(
        encoding="utf-8"
    )
    doc_text = (ROOT / "docs" / "revise-reviewed-artifact-workflow.md").read_text(
        encoding="utf-8"
    )
    assert "issue-731" not in workflow_text
    assert "d286fe4" not in workflow_text
    assert "issue-736" not in workflow_text
    assert "source-to-review-to-revision" in workflow_text
    assert "Run Input" in doc_text
    assert "Artifact records" in doc_text
    assert "--preview-only" in doc_text
