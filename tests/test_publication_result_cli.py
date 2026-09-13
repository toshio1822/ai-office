"""Focused provider-free CLI tests for Phase 271."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.engine.publication_regeneration_readiness import (
    assess_publication_regeneration_result_readiness,
)
from ai_office.invocation import ModelInvocationSuccess
from tests.test_publication_regeneration_projection import (
    evidence_for_state,
    readiness_sidecar,
)
from tests.test_publication_regeneration_readiness import (
    exact_contract,
    readiness_fixture,
)

runner = CliRunner()


def publication_result_args(
    readiness_record_path: Path, result_path: Path
) -> list[str]:
    """Return the explicit path arguments for the Phase 271 command."""
    return [
        "workflows",
        "publication-result",
        "--readiness-record-path",
        str(readiness_record_path),
        "--result-path",
        str(result_path),
    ]


def test_publication_result_ready_emits_one_exact_json_line_and_exit_zero(
    tmp_path: Path,
) -> None:
    exact_text = "\n  regenerated 日本語 😀\n\n\t"
    fixture = readiness_fixture(
        tmp_path,
        result=ModelInvocationSuccess(
            provider="openai",
            response_id="response-271-ready",
            request_id="request-271-ready",
            status="completed",
            text_parts=("\n  regenerated ", "日本語 😀\n\n\t"),
            text=exact_text,
        ),
    )
    assessment = assess_publication_regeneration_result_readiness(
        result_path=fixture.result_path,
        source_audit_path=fixture.audit_path,
        claim_contract=exact_contract(fixture),
    )
    readiness_path, readiness_record = readiness_sidecar(fixture, assessment)

    result = runner.invoke(
        app, publication_result_args(readiness_path, fixture.result_path)
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    value = json.loads(result.stdout)
    assert set(value) == {
        "business_output_sha256",
        "business_output_text",
        "operation",
        "publishable",
        "readiness",
        "readiness_record_sha256",
        "reason_codes",
        "regeneration_id",
        "result_record_sha256",
        "source_audit_sha256",
    }
    assert value["operation"] == "publication-result"
    assert value["publishable"] is True
    assert value["readiness"] == "ready"
    assert value["reason_codes"] == []
    assert value["regeneration_id"] == fixture.record.regeneration_id
    assert value["readiness_record_sha256"] == readiness_record.digest
    assert value["result_record_sha256"] == fixture.record.digest
    assert value["source_audit_sha256"] == fixture.audit.digest
    assert value["business_output_text"] == exact_text
    assert value["business_output_sha256"] == hashlib.sha256(
        exact_text.encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    "state",
    ("insufficient_evidence", "stale_or_inconsistent", "result_failure"),
)
def test_publication_result_valid_non_ready_evidence_is_json_exit_one_without_secrets(
    tmp_path: Path,
    state: str,
) -> None:
    fixture, readiness_path, readiness_record, candidate_text = evidence_for_state(
        tmp_path / state, state
    )

    result = runner.invoke(
        app, publication_result_args(readiness_path, fixture.result_path)
    )

    assert result.exit_code == 1
    assert len(result.stdout.splitlines()) == 1
    value = json.loads(result.stdout)
    assert value["operation"] == "publication-result"
    assert value["publishable"] is False
    assert value["readiness"] == state
    assert value["reason_codes"] == list(readiness_record.reason_codes)
    assert value["business_output_text"] is None
    assert value["business_output_sha256"] is None
    combined = result.stdout + result.stderr
    if candidate_text is not None:
        assert candidate_text not in combined
    failure_message = getattr(fixture.record.result, "message", None)
    if failure_message is not None:
        assert failure_message not in combined


def test_publication_result_phase270_exception_is_fixed_safe_and_called_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness_path = tmp_path / "readiness.json"
    result_path = tmp_path / "result.json"
    calls: list[tuple[Path, Path]] = []

    def raise_from_phase270(
        *, readiness_record_path: Path, result_path: Path
    ) -> object:
        calls.append((readiness_record_path, result_path))
        raise RuntimeError("raw provider failure and filesystem bytes")

    monkeypatch.setattr(
        cli_module,
        "project_publication_regeneration_output",
        raise_from_phase270,
    )

    result = runner.invoke(
        app, publication_result_args(readiness_path, result_path)
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: publication regeneration evidence is invalid\n"
    assert calls == [(readiness_path, result_path)]
    assert "raw provider failure" not in result.stderr
    assert "filesystem bytes" not in result.stderr


def test_publication_result_malformed_paths_reach_phase270_once_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness_path = tmp_path / "missing-readiness.json"
    result_path = tmp_path / "missing-result.json"
    calls: list[tuple[Path, Path]] = []
    real_boundary = cli_module.project_publication_regeneration_output

    def record_phase270_call(
        *, readiness_record_path: Path, result_path: Path
    ) -> object:
        calls.append((readiness_record_path, result_path))
        return real_boundary(
            readiness_record_path=readiness_record_path,
            result_path=result_path,
        )

    monkeypatch.setattr(
        cli_module,
        "project_publication_regeneration_output",
        record_phase270_call,
    )

    result = runner.invoke(
        app, publication_result_args(readiness_path, result_path)
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: publication regeneration evidence is invalid\n"
    assert calls == [(readiness_path, result_path)]


def test_publication_result_is_read_only_and_has_no_external_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, readiness_path, _, _ = evidence_for_state(tmp_path, "ready")
    tracked_paths = sorted(
        path for path in tmp_path.rglob("*") if path.is_file()
    )
    before_files = {path: path.read_bytes() for path in tracked_paths}

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden external access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    real_getenv = os.getenv

    def forbidden_getenv(name: str, default: object = None) -> object:
        # Typer inspects this one framework variable while constructing the
        # command; the production command must not inspect any environment key.
        if name == "_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION":
            return real_getenv(name, default)
        return forbidden(name, default)

    monkeypatch.setattr(os, "getenv", forbidden_getenv)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)

    result = runner.invoke(
        app, publication_result_args(readiness_path, fixture.result_path)
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    after_paths = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    assert after_paths == tracked_paths
    assert {path: path.read_bytes() for path in after_paths} == before_files


def test_workflows_help_lists_publication_result_command() -> None:
    result = runner.invoke(app, ["workflows", "--help"])

    assert result.exit_code == 0
    assert "publication-result" in result.stdout
