"""Offline product proof for the Issue #704 document-writing sample."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.providers.openai import OpenAIApiKey, OpenAIResponsesRawHttpResponse

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]
EMPLOYEES = ROOT / "employees"
WORKFLOWS = ROOT / "workflows"
WORKFLOW_ID = "write-internal-ai-guide"
RUN_INPUT = (
    "対象は全社員。顧客の秘密情報と個人情報は入力禁止。下書き・要約・翻訳は利用可。"
    "外部公開前に部門責任者の確認が必要。事故時は情報システム部へ報告する。"
)
MARKDOWN = """# 社内生成AI利用ガイド

## 目的

生成AIを安全に業務利用するための基準を示します。

## 禁止事項

- 顧客の秘密情報と個人情報を入力しないでください。

## 人による確認

外部公開前に部門責任者が確認してください。
"""


def _start_args(run_root: Path, run_id: str) -> list[str]:
    return [
        "workflows",
        "start",
        WORKFLOW_ID,
        "--run-id",
        run_id,
        "--run-input",
        RUN_INPUT,
        "--run-store",
        str(run_root),
        "--directory",
        str(WORKFLOWS),
        "--employees-directory",
        str(EMPLOYEES),
    ]


def _read_command(run_root: Path, command: str, *arguments: str):
    return runner.invoke(
        app,
        ["workflows", command, *arguments, "--run-store", str(run_root)],
    )


def test_m1_sample_reaches_verified_markdown_artifact_offline(
    tmp_path: Path, monkeypatch
) -> None:
    run_root = tmp_path / "runs"
    run_id = "m1-document-proof"
    transport_calls: list[object] = []
    key_calls: list[int] = []

    def load_synthetic_key() -> OpenAIApiKey:
        key_calls.append(1)
        return OpenAIApiKey(value=SecretStr("synthetic-offline-key"))

    def synthetic_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        transport_calls.append(request)
        body = json.dumps(
            {
                "id": "synthetic-m1-response",
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": MARKDOWN}],
                    }
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", "synthetic-m1-request"),),
            body,
        )

    monkeypatch.setattr(
        cli_module, "load_openai_api_key_from_environment", load_synthetic_key
    )
    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", synthetic_transport
    )

    preview_result = runner.invoke(
        app, _start_args(run_root, run_id) + ["--preview-only"]
    )
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    assert preview["mode"] == "preview"
    assert preview["run_id"] == run_id
    assert preview["run_input"] == RUN_INPUT
    assert preview["workflow_id"] == WORKFLOW_ID
    assert preview["step_id"] == "draft-guide"
    assert preview["employee_id"] == "japanese-document-writer"
    assert preview["business_approval_required"] is True
    assert preview["allowed_tools"] == []
    assert preview["execution_target"]["provider"] == "openai"
    assert len(preview["request_fingerprint"]) == 64
    assert preview["system_instructions"] != preview["task_instructions"]
    assert transport_calls == []
    assert key_calls == []

    unapproved = runner.invoke(app, _start_args(run_root, run_id))
    assert unapproved.exit_code == 2
    assert transport_calls == []
    assert key_calls == []

    approved = runner.invoke(
        app,
        _start_args(run_root, run_id)
        + [
            "--approve-business",
            "--business-approved-by",
            "offline-test-business-approver",
            "--business-approval-id",
            "offline-test-business-approval",
            "--approve-execution",
            "--execution-approved-by",
            "offline-test-execution-approver",
            "--execution-approval-id",
            "offline-test-execution-approval",
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
    assert approved.exit_code == 0, approved.stderr
    assert json.loads(approved.stdout)["status"] == "workflow_complete"
    assert len(transport_calls) == 1
    assert key_calls == [1]

    result = _read_command(run_root, "result", run_id)
    approval_evidence = _read_command(run_root, "approval-evidence", run_id)
    execution_evidence = _read_command(run_root, "execution-evidence", run_id)
    artifacts = _read_command(run_root, "artifacts", run_id)
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["output"]["output_text"] == MARKDOWN
    assert approval_evidence.exit_code == 0, approval_evidence.stderr
    assert len(json.loads(approval_evidence.stdout)["approvals"]) == 2
    assert execution_evidence.exit_code == 0, execution_evidence.stderr
    assert len(json.loads(execution_evidence.stdout)["attempts"]) == 1

    artifact_values = json.loads(artifacts.stdout)["artifacts"]
    assert artifacts.exit_code == 0, artifacts.stderr
    assert len(artifact_values) == 1
    assert artifact_values[0]["content_type"] == "text/markdown"
    assert artifact_values[0]["consistent_with_execution_evidence"] is True
    artifact_id = artifact_values[0]["artifact_id"]

    artifact = _read_command(run_root, "artifact", run_id, artifact_id)
    assert artifact.exit_code == 0, artifact.stderr
    assert json.loads(artifact.stdout)["artifact"]["content"] == MARKDOWN

    destination = tmp_path / "exports" / "guide.md"
    destination.parent.mkdir()
    exported = runner.invoke(
        app,
        [
            "workflows",
            "artifact-export",
            run_id,
            artifact_id,
            "--run-store",
            str(run_root),
            "--output",
            str(destination),
        ],
    )
    assert exported.exit_code == 0, exported.stderr
    assert destination.read_text(encoding="utf-8") == MARKDOWN
    assert len(transport_calls) == 1
    assert key_calls == [1]
