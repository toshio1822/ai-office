"""Offline product proof for the Issue #712 two-employee review sample."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.execution_evidence import (
    execution_normalized_result_evidence_path,
    list_run_execution_evidence,
    load_normalized_result_evidence,
)
from ai_office.providers.openai import OpenAIApiKey, OpenAIResponsesRawHttpResponse

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]
EMPLOYEES = ROOT / "employees"
WORKFLOWS = ROOT / "workflows"
SNAPSHOT = ROOT / "examples" / "m2-github-review" / "supplied-snapshot.md"
WORKFLOW_ID = "review-supplied-github-change"
ANALYSIS = """# 構造化レビュー所見

## 供給された事実
- Issue: `example/acme-widget#314` (`fixture://issue/314`)
- PR: `example/acme-widget#2718` (`fixture://pull/2718`)
- 要件: R1 必須値検証、R2 安全なエラー、R3 正常系の維持
- 変更説明: validation、test、publication documentation の3ファイル
- 検証説明: unit 84件 passed、lint passed (`fixture://ci/2718/unit`)

## 推論による評価
- 公開transport選択前の検証という説明は、無効入力での副作用抑止に整合する。
- ただし実装diff未確認のため、この評価は供給された説明に限定される。

## 未確認事項
- integration tests、実diff、無効入力時のtransport zero-call。
- Unicode空白の正規化、branch protection。
"""
REPORT = """# GitHub変更レビュー報告

## 対象と前提

- Issue: `example/acme-widget#314`（`fixture://issue/314`）
- Pull Request: `example/acme-widget#2718`（`fixture://pull/2718`）
- 本報告は供給されたオフラインsnapshotと前段の構造化所見に基づきます。
- GitHub、repository、diff、CI、providerをlive確認していません。

## 確認済みの供給事実

- 要件は、R1 `display_name`必須値検証、R2 入力値を露出しない安全な
  validation message、R3 正常なpublication pathの維持です。
- 変更説明は `src/widget/validation.py`、`tests/test_widget_validation.py`、
  `docs/widget-publication.md` の3ファイルです。
- test evidenceはunit 84件 passed、lint passedです（`fixture://ci/2718/unit`）。

## 推論による評価とリスク

- validationがpublication transport選択前に行われる説明は、無効入力の
  外部副作用抑止に整合します。
- actual diff未確認のため、R1からR3の実装充足は断定できません。
- transport zero-callを直接示すtest evidenceが供給されていない点は残存riskです。

## 未確認事項

- integration tests、actual diff、Unicode whitespace normalization、
  branch protection、required checksは未確認です。
"""


def _start_args(run_root: Path, run_id: str, run_input: str) -> list[str]:
    return [
        "workflows",
        "start",
        WORKFLOW_ID,
        "--run-id",
        run_id,
        "--run-input",
        run_input,
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


def _approval_args(preview: dict[str, object], suffix: str) -> list[str]:
    return [
        "--approve-business",
        "--business-approved-by",
        "offline-business-reviewer",
        "--business-approval-id",
        f"offline-business-{suffix}",
        "--approve-execution",
        "--execution-approved-by",
        "offline-execution-reviewer",
        "--execution-approval-id",
        f"offline-execution-{suffix}",
        "--expected-step-id",
        str(preview["step_id"]),
        "--expected-step-index",
        str(preview["step_index"]),
        "--expected-employee-id",
        str(preview["employee_id"]),
        "--expected-request-fingerprint",
        str(preview["request_fingerprint"]),
    ]


def _synthetic_response(
    response_id: str, request_id: str | None, output: str
):
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
        200,
        "synthetic",
        () if request_id is None else (("x-request-id", request_id),),
        body,
    )


def test_m2_sample_hands_actual_analysis_to_writer_and_exports_report_offline(
    tmp_path: Path, monkeypatch
) -> None:
    run_root = tmp_path / "runs"
    run_id = "m2-github-review-proof"
    run_input = SNAPSHOT.read_text(encoding="utf-8")
    transport_calls: list[object] = []
    key_calls: list[int] = []

    def load_synthetic_key() -> OpenAIApiKey:
        key_calls.append(1)
        return OpenAIApiKey(value=SecretStr("synthetic-offline-key"))

    def synthetic_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        transport_calls.append(request)
        if len(transport_calls) == 1:
            return _synthetic_response(
                "synthetic-analysis-response", None, ANALYSIS
            )
        if len(transport_calls) == 2:
            return _synthetic_response(
                "synthetic-report-response", "synthetic-report-request", REPORT
            )
        raise AssertionError("unexpected implicit retry or continuation")

    monkeypatch.setattr(
        cli_module, "load_openai_api_key_from_environment", load_synthetic_key
    )
    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", synthetic_transport
    )

    for command in (
        ["employees", "validate", "--directory", str(EMPLOYEES)],
        [
            "workflows",
            "validate",
            "--directory",
            str(WORKFLOWS),
            "--employees-directory",
            str(EMPLOYEES),
        ],
        [
            "workflows",
            "plan",
            WORKFLOW_ID,
            "--directory",
            str(WORKFLOWS),
            "--employees-directory",
            str(EMPLOYEES),
        ],
        [
            "workflows",
            "request",
            WORKFLOW_ID,
            "2",
            "--directory",
            str(WORKFLOWS),
            "--employees-directory",
            str(EMPLOYEES),
        ],
    ):
        checked = runner.invoke(app, command)
        assert checked.exit_code == 0, checked.stderr

    preview_result = runner.invoke(
        app, _start_args(run_root, run_id, run_input) + ["--preview-only"]
    )
    assert preview_result.exit_code == 0, preview_result.stderr
    first_preview = json.loads(preview_result.stdout)
    assert first_preview["step_id"] == "analyze-supplied-snapshots"
    assert first_preview["employee_id"] == "general-researcher"
    assert first_preview["run_input"] == run_input
    assert transport_calls == []
    assert key_calls == []

    unapproved_start = runner.invoke(app, _start_args(run_root, run_id, run_input))
    assert unapproved_start.exit_code == 2
    assert transport_calls == []
    assert key_calls == []

    started = runner.invoke(
        app,
        _start_args(run_root, run_id, run_input)
        + _approval_args(first_preview, "analysis"),
    )
    assert started.exit_code == 0, started.stderr
    assert json.loads(started.stdout)["status"] == "prepare_next_step"
    assert len(transport_calls) == 1
    assert key_calls == [1]
    attempts = {
        attempt.step_id: attempt
        for attempt in list_run_execution_evidence(run_root, run_id)
    }
    first_evidence = load_normalized_result_evidence(
        run_root, run_id, attempts["analyze-supplied-snapshots"].attempt_id
    )
    assert first_evidence.request_id is None

    second_preview_result = _read_command(
        run_root, "continue", run_id, "--preview-only"
    )
    assert second_preview_result.exit_code == 0, second_preview_result.stderr
    second_preview = json.loads(second_preview_result.stdout)
    assert second_preview["step_id"] == "write-japanese-review-report"
    assert second_preview["employee_id"] == "japanese-document-writer"
    upstream_provenance = {
        "workflow_id": WORKFLOW_ID,
        "step_id": "analyze-supplied-snapshots",
        "step_index": 1,
        "employee_id": "general-researcher",
        "output_text": ANALYSIS,
    }
    upstream_digest = hashlib.sha256(
        json.dumps(
            upstream_provenance,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert second_preview["upstream_inputs"] == [
        {
            **upstream_provenance,
            "sha256": upstream_digest,
        }
    ]
    task_input = json.loads(second_preview["task_input"])
    assert task_input["upstream_inputs"][0]["output_text"] == ANALYSIS
    assert task_input["run_input"] == run_input
    assert len(transport_calls) == 1
    assert key_calls == [1]

    unapproved_continue = _read_command(run_root, "continue", run_id)
    assert unapproved_continue.exit_code == 2
    assert len(transport_calls) == 1
    assert key_calls == [1]

    completed = runner.invoke(
        app,
        ["workflows", "continue", run_id, "--run-store", str(run_root)]
        + _approval_args(second_preview, "report"),
    )
    assert completed.exit_code == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "workflow_complete"
    assert len(transport_calls) == 2
    assert key_calls == [1, 1]
    second_request_body = json.loads(transport_calls[1].body)  # type: ignore[union-attr]
    assert json.loads(second_request_body["input"])["upstream_inputs"][0][
        "output_text"
    ] == ANALYSIS

    result = _read_command(run_root, "result", run_id)
    approvals = _read_command(run_root, "approval-evidence", run_id)
    executions = _read_command(run_root, "execution-evidence", run_id)
    artifacts = _read_command(run_root, "artifacts", run_id)
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["output"]["output_text"] == REPORT
    assert len(json.loads(approvals.stdout)["approvals"]) == 4
    assert len(json.loads(executions.stdout)["attempts"]) == 2
    attempts = {
        attempt.step_id: attempt
        for attempt in list_run_execution_evidence(run_root, run_id)
    }
    second_evidence = load_normalized_result_evidence(
        run_root, run_id, attempts["write-japanese-review-report"].attempt_id
    )
    assert second_evidence.request_id == "synthetic-report-request"
    artifact_values = json.loads(artifacts.stdout)["artifacts"]
    assert len(artifact_values) == 1
    assert artifact_values[0]["step_id"] == "write-japanese-review-report"
    assert artifact_values[0]["employee_id"] == "japanese-document-writer"
    assert artifact_values[0]["content_type"] == "text/markdown"
    assert artifact_values[0]["consistent_with_execution_evidence"] is True
    artifact_id = artifact_values[0]["artifact_id"]

    artifact = _read_command(run_root, "artifact", run_id, artifact_id)
    assert artifact.exit_code == 0, artifact.stderr
    content = json.loads(artifact.stdout)["artifact"]["content"]
    assert content == REPORT
    for required in (
        "example/acme-widget#314",
        "example/acme-widget#2718",
        "要件",
        "変更説明",
        "test evidence",
        "risk",
        "未確認事項",
        "fixture://issue/314",
        "fixture://pull/2718",
        "fixture://ci/2718/unit",
    ):
        assert required in content

    destination = tmp_path / "exports" / "review.md"
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
    assert destination.read_text(encoding="utf-8") == REPORT
    refused_overwrite = runner.invoke(
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
    assert refused_overwrite.exit_code == 2

    _read_command(run_root, "result", run_id)
    _read_command(run_root, "approval-evidence", run_id)
    _read_command(run_root, "execution-evidence", run_id)
    _read_command(run_root, "artifact", run_id, artifact_id)
    assert len(transport_calls) == 2
    assert key_calls == [1, 1]


@pytest.mark.parametrize("request_id", [None, "synthetic-analysis-request"])
@pytest.mark.parametrize(
    "tamper",
    ["missing-result", "event-output", "event-request-id", "event-linkage"],
)
def test_m2_persisted_evidence_tampering_stops_before_successor_transport(
    tmp_path: Path,
    monkeypatch,
    request_id: str | None,
    tamper: str,
) -> None:
    run_root = tmp_path / f"tampered-{tamper}-{request_id or 'none'}"
    run_id = "m2-tampered-evidence"
    run_input = SNAPSHOT.read_text(encoding="utf-8")
    transport_calls: list[object] = []
    key_calls: list[int] = []

    def load_synthetic_key() -> OpenAIApiKey:
        key_calls.append(1)
        return OpenAIApiKey(value=SecretStr("synthetic-offline-key"))

    def first_step_only_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        transport_calls.append(request)
        if len(transport_calls) != 1:
            raise AssertionError("successor transport must not run")
        return _synthetic_response(
            "synthetic-analysis-response", request_id, ANALYSIS
        )

    monkeypatch.setattr(
        cli_module, "load_openai_api_key_from_environment", load_synthetic_key
    )
    monkeypatch.setattr(
        cli_module,
        "send_openai_responses_http_request",
        first_step_only_transport,
    )

    first_preview = json.loads(
        runner.invoke(
            app, _start_args(run_root, run_id, run_input) + ["--preview-only"]
        ).stdout
    )
    started = runner.invoke(
        app,
        _start_args(run_root, run_id, run_input)
        + _approval_args(first_preview, "tampered-analysis"),
    )
    assert started.exit_code == 0, started.stderr
    second_preview_result = _read_command(
        run_root, "continue", run_id, "--preview-only"
    )
    assert second_preview_result.exit_code == 0, second_preview_result.stderr
    second_preview = json.loads(second_preview_result.stdout)
    attempts = {
        attempt.step_id: attempt
        for attempt in list_run_execution_evidence(run_root, run_id)
    }
    attempt = attempts["analyze-supplied-snapshots"]

    if tamper == "missing-result":
        execution_normalized_result_evidence_path(
            run_root, run_id, attempt.attempt_id
        ).unlink()
    else:
        events_path = run_root / f"{run_id}.events.jsonl"
        lines = events_path.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[0])
        if tamper == "event-output":
            event["output_text"] = "forged analysis"
        elif tamper == "event-request-id":
            event["request_id"] = "forged-request"
        else:
            for field in (
                "execution_attempt_id",
                "execution_attempt_evidence_sha256",
                "normalized_result_evidence_sha256",
                "raw_response_evidence_sha256",
                "raw_response_body_sha256",
            ):
                event.pop(field)
        lines[0] = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    continued = runner.invoke(
        app,
        ["workflows", "continue", run_id, "--run-store", str(run_root)]
        + _approval_args(second_preview, "tampered-report"),
    )
    assert continued.exit_code == 2
    assert len(transport_calls) == 1
    assert key_calls == [1]
    assert json.loads(_read_command(run_root, "artifacts", run_id).stdout)[
        "artifacts"
    ] == []


def test_m2_empty_provider_request_id_fails_without_successor_or_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_root = tmp_path / "empty-request-id"
    run_id = "m2-empty-request-id"
    run_input = SNAPSHOT.read_text(encoding="utf-8")
    transport_calls: list[object] = []

    monkeypatch.setattr(
        cli_module,
        "load_openai_api_key_from_environment",
        lambda: OpenAIApiKey(value=SecretStr("synthetic-offline-key")),
    )

    def empty_request_id_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        transport_calls.append(request)
        return _synthetic_response("synthetic-analysis-response", "", ANALYSIS)

    monkeypatch.setattr(
        cli_module,
        "send_openai_responses_http_request",
        empty_request_id_transport,
    )
    preview = json.loads(
        runner.invoke(
            app, _start_args(run_root, run_id, run_input) + ["--preview-only"]
        ).stdout
    )
    started = runner.invoke(
        app,
        _start_args(run_root, run_id, run_input)
        + _approval_args(preview, "empty-request-id"),
    )

    assert started.exit_code != 0
    assert len(transport_calls) == 1
    assert _read_command(run_root, "continue", run_id).exit_code != 0
    assert len(transport_calls) == 1
    assert json.loads(_read_command(run_root, "artifacts", run_id).stdout)[
        "artifacts"
    ] == []


def test_m2_failed_first_step_cannot_continue_or_create_report_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    run_root = tmp_path / "failed-runs"
    run_id = "m2-github-review-failed-analysis"
    run_input = SNAPSHOT.read_text(encoding="utf-8")
    transport_calls: list[object] = []
    key_calls: list[int] = []

    def load_synthetic_key() -> OpenAIApiKey:
        key_calls.append(1)
        return OpenAIApiKey(value=SecretStr("synthetic-offline-key"))

    def failing_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        transport_calls.append(request)
        return OpenAIResponsesRawHttpResponse(
            500,
            "synthetic failure",
            (("x-request-id", "synthetic-failure-request"),),
            b'{"error":{"message":"synthetic failure","type":"server_error"}}',
        )

    monkeypatch.setattr(
        cli_module, "load_openai_api_key_from_environment", load_synthetic_key
    )
    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", failing_transport
    )

    preview_result = runner.invoke(
        app, _start_args(run_root, run_id, run_input) + ["--preview-only"]
    )
    preview = json.loads(preview_result.stdout)
    failed = runner.invoke(
        app,
        _start_args(run_root, run_id, run_input)
        + _approval_args(preview, "failed-analysis"),
    )
    assert failed.exit_code == 1
    assert json.loads(failed.stdout)["status"] == "persisted_failure"
    assert len(transport_calls) == 1
    assert key_calls == [1]

    continue_preview = _read_command(run_root, "continue", run_id, "--preview-only")
    assert continue_preview.exit_code == 0, continue_preview.stderr
    assert json.loads(continue_preview.stdout)["status"] == "persisted_failure"
    continue_execute = _read_command(run_root, "continue", run_id)
    assert continue_execute.exit_code == 1
    assert json.loads(continue_execute.stdout)["status"] == "persisted_failure"
    result = _read_command(run_root, "result", run_id)
    artifacts = _read_command(run_root, "artifacts", run_id)
    executions = _read_command(run_root, "execution-evidence", run_id)
    assert json.loads(result.stdout)["output"] is None
    assert json.loads(artifacts.stdout)["artifacts"] == []
    assert len(json.loads(executions.stdout)["attempts"]) == 1
    assert len(transport_calls) == 1
    assert key_calls == [1]
