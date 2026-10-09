"""Offline proof for the reusable Issue #727 research/report Workflow."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.definitions.employee import load_employees
from ai_office.definitions.workflow import (
    load_workflows,
    validate_workflow_employee_references,
)

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]
EMPLOYEES = ROOT / "employees"
WORKFLOWS = ROOT / "workflows"
SAMPLE = (
    ROOT / "examples" / "research-and-summarize" / "non-usb-engineering-evidence.md"
)
WORKFLOW_ID = "research-and-summarize"


def test_issue_727_report_workflow_previews_generic_evidence_without_side_effects(
    tmp_path: Path, monkeypatch
) -> None:
    unexpected_provider_calls: list[str] = []

    def unexpected_key_load() -> None:
        unexpected_provider_calls.append("credential")
        raise AssertionError("preview must not load credentials")

    def unexpected_transport(*args: object, **kwargs: object) -> None:
        unexpected_provider_calls.append("transport")
        raise AssertionError("preview must not contact a provider")

    monkeypatch.setattr(
        cli_module, "load_openai_api_key_from_environment", unexpected_key_load
    )
    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", unexpected_transport
    )

    workflows = load_workflows(WORKFLOWS)
    employees = load_employees(EMPLOYEES)
    validate_workflow_employee_references(workflows, employees)
    workflow = next(
        item.definition for item in workflows if item.definition.id == WORKFLOW_ID
    )
    assert len(workflow.steps) == 2
    research, report = workflow.steps
    assert research.id == "research"
    assert research.employee == "general-researcher"
    assert research.business_approval_required is True
    assert report.id == "summarize"
    assert report.employee == "japanese-document-writer"
    assert report.business_approval_required is True
    assert report.artifact_content_type == "text/markdown"
    assert "原因・説明仮説（未確定）" in report.instructions
    assert "未確認事項と証拠上の限界" in report.instructions
    assert "text/markdown Artifact" in report.instructions

    run_input = SAMPLE.read_text(encoding="utf-8")
    run_store = tmp_path / "runs"
    preview_result = runner.invoke(
        app,
        [
            "workflows",
            "start",
            WORKFLOW_ID,
            "--run-id",
            "issue-727-non-usb-preview",
            "--run-input-file",
            str(SAMPLE),
            "--run-store",
            str(run_store),
            "--directory",
            str(WORKFLOWS),
            "--employees-directory",
            str(EMPLOYEES),
            "--preview-only",
        ],
    )
    assert preview_result.exit_code == 0, preview_result.stderr
    preview = json.loads(preview_result.stdout)
    assert preview["workflow_id"] == WORKFLOW_ID
    assert preview["step_id"] == research.id
    assert preview["step_index"] == 1
    assert preview["employee_id"] == research.employee
    assert preview["business_approval_required"] is True
    assert preview["status"] == "step_ready"
    assert preview["run_input"] == run_input
    assert len(preview["request_fingerprint"]) == 64
    assert "supplied evidence" in preview["task_instructions"]
    assert "Candidate improvements" in preview["task_instructions"]
    assert "USB" not in run_input
    assert not run_store.exists()
    assert unexpected_provider_calls == []
