"""Offline behavioral proof for the Issue #719 Calamares investigation workflow."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.execution_evidence import list_run_execution_evidence
from ai_office.providers.openai import OpenAIApiKey, OpenAIResponsesRawHttpResponse

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]
EMPLOYEES = ROOT / "employees"
WORKFLOWS = ROOT / "workflows"
EVIDENCE = ROOT / "examples" / "issue-719" / "evidence-package.md"
WORKFLOW_ID = "investigate-calamares-usb-destination"
FINDINGS = """# 構造化調査所見

## 観測・ソース事実
- Issue #7 の報告であり、再現はしていない。
- `oyo-calamares` `d38f36148385a4b3d16aa1540de292c576bd2398` の
  `src/modules/partition/core/DeviceList.cpp:123-194` は列挙候補の除外条件を含む。
- https://calamares.io/docs/partitions/

## 仮説・安全性・不足資料
- 別USBが出ない原因は未確定。Beta2.3のpackage revisionも不明。
- source USBの誤選択はデータ損失につながる。
- session log、version、lsblk/blkid、対象USB状態が必要。
- disposable VMとthrowaway diskに限り、書込みせず候補表示を検証する。
"""
REPORT = """# Calamares USBインストール先の技術調査報告書

## 調査範囲と資料
入力されたevidence packageのみを使用。

## 報告された観測事実 (未再現)
Issue #7の報告であり、独立再現していない。

## ソースコード／設定で確認できた事実
`oyo-calamares` commit `d38f36148385a4b3d16aa1540de292c576bd2398` の
`src/modules/partition/core/DeviceList.cpp:123-194` を確認。

## 公式資料
https://calamares.io/docs/partitions/

## 原因仮説 (未確定)
原因は特定されておらず、Beta2.3のpackage revisionも未確認。

## 修正・製品方針の候補
追加証拠を得るまでは候補に留める。

## 安全上の注意
source USBの誤選択はデータ損失につながる。全removable媒体の一律許可は安全と
断定できない。

## 追加調査事項
session log、Calamares/KPMCore version、lsblk/blkid、実機での列挙状態。

## 安全な検証方法
read-only資料を先に取得し、disposable VMとthrowaway diskだけで表示を確認する。
実媒体への書込み・partitioningはしない。

## 結論と未確認事項
原因、再現性、USB installのサポート方針は未確定。
"""


def _start_args(run_root: Path, run_id: str, run_input: str) -> list[str]:
    return [
        "workflows", "start", WORKFLOW_ID, "--run-id", run_id,
        "--run-input", run_input, "--run-store", str(run_root),
        "--directory", str(WORKFLOWS), "--employees-directory", str(EMPLOYEES),
    ]


def _approval_args(preview: dict[str, object], step: str) -> list[str]:
    return [
        "--approve-business", "--business-approved-by", "offline-reviewer",
        "--business-approval-id", f"offline-business-{step}",
        "--approve-execution", "--execution-approved-by", "offline-executor",
        "--execution-approval-id", f"offline-execution-{step}",
        "--expected-step-id", str(preview["step_id"]),
        "--expected-step-index", str(preview["step_index"]),
        "--expected-employee-id", str(preview["employee_id"]),
        "--expected-request-fingerprint", str(preview["request_fingerprint"]),
    ]


def _response(response_id: str, output: str) -> OpenAIResponsesRawHttpResponse:
    body = json.dumps(
        {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": output}],
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    return OpenAIResponsesRawHttpResponse(
        200, "synthetic", (("x-request-id", response_id),), body
    )


def test_issue_719_workflow_handoffs_evidence_and_creates_markdown_offline(
    tmp_path: Path, monkeypatch
) -> None:
    run_root = tmp_path / "runs"
    run_id = "issue-719-calamares-offline-proof"
    run_input = EVIDENCE.read_text(encoding="utf-8")
    transport_calls: list[object] = []

    monkeypatch.setattr(
        cli_module,
        "load_openai_api_key_from_environment",
        lambda: OpenAIApiKey(value=SecretStr("synthetic-offline-key")),
    )

    def synthetic_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        transport_calls.append(request)
        if len(transport_calls) == 1:
            return _response("synthetic-findings", FINDINGS)
        if len(transport_calls) == 2:
            return _response("synthetic-report", REPORT)
        raise AssertionError("unexpected retry or automatic continuation")

    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", synthetic_transport
    )

    for command in (
        [
            "workflows", "validate", "--directory", str(WORKFLOWS),
            "--employees-directory", str(EMPLOYEES),
        ],
        [
            "workflows", "plan", WORKFLOW_ID, "--directory", str(WORKFLOWS),
            "--employees-directory", str(EMPLOYEES),
        ],
        [
            "workflows", "request", WORKFLOW_ID, "2", "--directory",
            str(WORKFLOWS), "--employees-directory", str(EMPLOYEES),
        ],
    ):
        checked = runner.invoke(app, command)
        assert checked.exit_code == 0, checked.stderr

    preview_result = runner.invoke(
        app, _start_args(run_root, run_id, run_input) + ["--preview-only"]
    )
    assert preview_result.exit_code == 0, preview_result.stderr
    first = json.loads(preview_result.stdout)
    assert first["step_id"] == "analyze-calamares-evidence"
    assert first["employee_id"] == "general-researcher"
    assert first["run_input"] == run_input
    assert first["business_approval_required"] is True
    assert len(first["request_fingerprint"]) == 64
    assert "root-cause hypotheses" in first["task_instructions"]
    assert "source USB" in first["task_instructions"]
    assert transport_calls == []

    started = runner.invoke(
        app,
        _start_args(run_root, run_id, run_input)
        + _approval_args(first, "analysis"),
    )
    assert started.exit_code == 0, started.stderr
    assert json.loads(started.stdout)["status"] == "prepare_next_step"
    assert len(transport_calls) == 1

    continuation = runner.invoke(
        app,
        [
            "workflows", "continue", run_id, "--run-store", str(run_root),
            "--preview-only",
        ],
    )
    assert continuation.exit_code == 0, continuation.stderr
    second = json.loads(continuation.stdout)
    assert second["step_id"] == "write-japanese-calamares-report"
    assert second["employee_id"] == "japanese-document-writer"
    assert second["upstream_inputs"][0]["output_text"] == FINDINGS
    assert second["upstream_inputs"][0]["sha256"] == hashlib.sha256(
        json.dumps(
            {
                "workflow_id": WORKFLOW_ID,
                "step_id": "analyze-calamares-evidence",
                "step_index": 1,
                "employee_id": "general-researcher",
                "output_text": FINDINGS,
            }, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    completed = runner.invoke(
        app,
        ["workflows", "continue", run_id, "--run-store", str(run_root)]
        + _approval_args(second, "report"),
    )
    assert completed.exit_code == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "workflow_complete"
    assert len(transport_calls) == 2

    artifacts = runner.invoke(
        app, ["workflows", "artifacts", run_id, "--run-store", str(run_root)]
    )
    assert artifacts.exit_code == 0, artifacts.stderr
    artifact = json.loads(artifacts.stdout)["artifacts"][0]
    assert artifact["step_id"] == "write-japanese-calamares-report"
    assert artifact["employee_id"] == "japanese-document-writer"
    assert artifact["content_type"] == "text/markdown"
    assert artifact["consistent_with_execution_evidence"] is True
    result = runner.invoke(
        app,
        [
            "workflows", "artifact", run_id, artifact["artifact_id"],
            "--run-store", str(run_root),
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["artifact"]["content"] == REPORT
    assert len(list_run_execution_evidence(run_root, run_id)) == 2
    assert len(transport_calls) == 2
