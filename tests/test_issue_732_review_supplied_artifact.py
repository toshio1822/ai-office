"""Offline proof for the independent supplied-Artifact review Workflow."""

from __future__ import annotations

import json
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
WORKFLOW_ID = "review-supplied-artifact"


def test_review_workflow_persists_synthetic_verdict_artifact_offline(
    tmp_path: Path, monkeypatch
) -> None:
    registry = tmp_path / "destinations.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "destinations": {
                    "offline-review": {
                        "protocol": "openai-responses",
                        "endpoint": "https://api.example.com/v1/responses",
                        "credential": "OFFLINE_REVIEW_KEY",
                        "models": ["offline-model"],
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    run_input = {
        "requirements": ["The report states the measured value and its source."],
        "artifact": {
            "artifact_id": "fixture-artifact",
            "sha256": "a" * 64,
            "content_type": "text/markdown",
            "content": (
                "# Result\n\nMeasured value: 42. "
                "Source: sensor-log.md, line 8.\n"
            ),
        },
        "evidence": [{"source_id": "sensor-log", "excerpt": "line 8: value=42"}],
    }
    input_file = tmp_path / "review-input.json"
    input_file.write_text(json.dumps(run_input), encoding="utf-8")
    run_store = tmp_path / "runs"
    requests: list[object] = []
    credential_reads: list[object] = []

    workflows = load_workflows(WORKFLOWS)
    employees = load_employees(EMPLOYEES)
    validate_workflow_employee_references(workflows, employees)
    definition = next(
        item.definition for item in workflows if item.definition.id == WORKFLOW_ID
    )
    assert len(definition.steps) == 1
    step = definition.steps[0]
    assert step.id == "review"
    assert step.employee == "general-researcher"
    assert step.business_approval_required is True
    assert step.artifact_content_type == "text/markdown"

    def fake_key(target: object) -> OpenAIApiKey:
        credential_reads.append(target)
        return OpenAIApiKey(value=SecretStr("synthetic-only"))

    def fake_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        requests.append(request)
        payload = json.loads(request.body)
        assert payload["model"] == "offline-model"
        # The provider-facing request is a fresh request, not a continuation
        # containing a creator response or conversation identifier.
        assert "previous_response_id" not in payload
        assert "conversation" not in payload
        assert "creator transcript" not in json.dumps(payload).lower()
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", "issue-732-offline"),),
            json.dumps(
                {
                    "id": "response-issue-732-offline",
                    "object": "response",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        "# Review\n\n"
                                        "Verdict: PASS\n\n"
                                        "The supplied requirement is met.\n\n"
                                        "## Findings\n\n"
                                        "No actionable findings.\n"
                                    ),
                                }
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
        "issue-732-offline-review",
        "--run-input-file",
        str(input_file),
        "--run-store",
        str(run_store),
        "--directory",
        str(WORKFLOWS),
        "--employees-directory",
        str(EMPLOYEES),
        "--execution-target",
        "offline-review",
        "--execution-destinations",
        str(registry),
        "--execution-model",
        "offline-model",
    ]
    preview_result = runner.invoke(app, args + ["--preview-only"])
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    assert preview["workflow_id"] == WORKFLOW_ID
    assert preview["step_id"] == "review"
    assert preview["employee_id"] == "general-researcher"
    assert preview["step_index"] == 1
    assert preview["business_approval_required"] is True
    assert preview["run_input"] == json.dumps(run_input)
    assert preview["model"] == "offline-model"
    assert len(preview["request_fingerprint"]) == 64
    assert not run_store.exists()
    assert requests == []
    assert credential_reads == []

    executed = runner.invoke(
        app,
        args
        + [
            "--approve-business",
            "--business-approved-by",
            "offline-reviewer",
            "--business-approval-id",
            "issue-732-offline-business",
            "--approve-execution",
            "--execution-approved-by",
            "offline-operator",
            "--execution-approval-id",
            "issue-732-offline-execution",
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

    artifacts = runner.invoke(
        app,
        [
            "workflows",
            "artifacts",
            "issue-732-offline-review",
            "--run-store",
            str(run_store),
        ],
    )
    assert artifacts.exit_code == 0, artifacts.stderr
    artifact = json.loads(artifacts.stdout)["artifacts"][0]
    assert artifact["content_type"] == "text/markdown"
    artifact_id = artifact["artifact_id"]
    read = runner.invoke(
        app,
        [
            "workflows",
            "artifact",
            "issue-732-offline-review",
            artifact_id,
            "--run-store",
            str(run_store),
        ],
    )
    assert read.exit_code == 0, read.stderr
    content = json.loads(read.stdout)["artifact"]["content"]
    assert "Verdict: PASS" in content
    assert "## Findings" in content
    assert "No actionable findings." in content
    assert len(requests) == 1
