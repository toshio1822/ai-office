"""Focused provider-free CLI tests for Phase 273."""

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
from ai_office.engine import publication_regeneration_export as export_module
from ai_office.engine.publication_regeneration_export import (
    PublicationRegenerationExportError,
    PublicationRegenerationExportReceipt,
)
from ai_office.engine.publication_regeneration_projection import (
    project_publication_regeneration_output,
    publication_regeneration_projection_digest,
)
from tests.test_publication_regeneration_export import ready_evidence
from tests.test_publication_regeneration_projection import evidence_for_state

runner = CliRunner()


def publication_export_args(
    readiness_record_path: Path,
    result_path: Path,
    output_path: Path,
) -> list[str]:
    """Return the explicit path arguments for the Phase 273 command."""
    return [
        "workflows",
        "publication-export",
        "--readiness-record-path",
        str(readiness_record_path),
        "--result-path",
        str(result_path),
        "--output-path",
        str(output_path),
    ]


def receipt() -> PublicationRegenerationExportReceipt:
    """Build one deterministic valid in-memory Phase 272 receipt."""
    return PublicationRegenerationExportReceipt(
        schema_version="publication-regeneration-export-receipt.v1",
        regeneration_id="regen-cli",
        projection_sha256="a" * 64,
        readiness_record_sha256="b" * 64,
        result_record_sha256="c" * 64,
        source_audit_sha256="d" * 64,
        business_output_sha256="e" * 64,
        output_byte_length=17,
    )


def receipt_json(value: PublicationRegenerationExportReceipt) -> dict[str, object]:
    """Return the exact safe JSON contract expected from the adapter."""
    return {
        "operation": "publication-export",
        "schema_version": value.schema_version,
        "regeneration_id": value.regeneration_id,
        "projection_sha256": value.projection_sha256,
        "readiness_record_sha256": value.readiness_record_sha256,
        "result_record_sha256": value.result_record_sha256,
        "source_audit_sha256": value.source_audit_sha256,
        "business_output_sha256": value.business_output_sha256,
        "output_byte_length": value.output_byte_length,
    }


def test_publication_export_requires_all_three_paths_without_defaults() -> None:
    values = {
        "--readiness-record-path": "readiness.json",
        "--result-path": "result.json",
        "--output-path": "export.txt",
    }

    for missing in values:
        args = ["workflows", "publication-export"]
        for option, value in values.items():
            if option != missing:
                args.extend((option, value))

        result = runner.invoke(app, args)

        assert result.exit_code == 2
        assert result.stdout == ""
        assert "Missing option" in result.stderr


def test_workflows_help_lists_publication_export_command() -> None:
    result = runner.invoke(app, ["workflows", "--help"])

    assert result.exit_code == 0
    assert "publication-export" in result.stdout


def test_success_delegates_exact_path_objects_once_and_emits_exact_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    readiness_path = Path("raw/../readiness.json")
    result_path = Path("raw/result.json")
    output_path = Path("raw/../output/business-secret.txt")
    expected = receipt()
    calls: list[tuple[Path, Path, Path]] = []

    def export(
        *,
        output_path: Path,
        readiness_record_path: Path,
        result_path: Path,
    ) -> PublicationRegenerationExportReceipt:
        calls.append((output_path, readiness_record_path, result_path))
        return expected

    monkeypatch.setattr(cli_module, "export_publication_regeneration_output", export)

    cli_module.publication_export_workflow(
        readiness_record_path=readiness_path,
        result_path=result_path,
        output_path=output_path,
    )

    captured = capsys.readouterr()
    assert calls == [(output_path, readiness_path, result_path)]
    assert calls[0][0] is output_path
    assert calls[0][1] is readiness_path
    assert calls[0][2] is result_path
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out) == receipt_json(expected)
    assert str(output_path) not in captured.out + captured.err


def test_success_cli_json_is_exact_and_does_not_leak_path_or_business_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness_path = tmp_path / "readiness.json"
    result_path = tmp_path / "result.json"
    output_path = tmp_path / "business-secret-output.txt"
    expected = receipt()
    raw_business_text = "RAW BUSINESS OUTPUT 日本語"

    def export(**kwargs: Path) -> PublicationRegenerationExportReceipt:
        assert kwargs["output_path"] == output_path
        return expected

    monkeypatch.setattr(cli_module, "export_publication_regeneration_output", export)

    result = runner.invoke(
        app, publication_export_args(readiness_path, result_path, output_path)
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout) == receipt_json(expected)
    assert str(output_path) not in result.stdout + result.stderr
    assert raw_business_text not in result.stdout + result.stderr
    assert not output_path.exists()


@pytest.mark.parametrize(
    "text",
    ("\n  ready 日本語 😀\n\ttrailing whitespace  ", ""),
)
def test_ready_export_uses_real_phase272_boundary_and_exact_utf8_bytes(
    tmp_path: Path,
    text: str,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path, text=text)
    output_path = tmp_path / "exact-export.txt"

    result = runner.invoke(
        app, publication_export_args(readiness_path, fixture.result_path, output_path)
    )

    output_bytes = text.encode("utf-8")
    assert result.exit_code == 0
    assert result.stderr == ""
    assert output_path.read_bytes() == output_bytes
    value = json.loads(result.stdout)
    projection = project_publication_regeneration_output(
        readiness_record_path=readiness_path,
        result_path=fixture.result_path,
    )
    assert value == {
        "operation": "publication-export",
        "schema_version": "publication-regeneration-export-receipt.v1",
        "regeneration_id": projection.regeneration_id,
        "projection_sha256": publication_regeneration_projection_digest(projection),
        "readiness_record_sha256": projection.readiness_record_sha256,
        "result_record_sha256": projection.result_record_sha256,
        "source_audit_sha256": projection.source_audit_sha256,
        "business_output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "output_byte_length": len(output_bytes),
    }
    assert str(output_path) not in result.stdout + result.stderr
    if text:
        assert text not in result.stdout + result.stderr


def test_ready_empty_string_reports_zero_byte_export(tmp_path: Path) -> None:
    fixture, readiness_path = ready_evidence(tmp_path, text="")
    output_path = tmp_path / "empty-export.txt"

    result = runner.invoke(
        app, publication_export_args(readiness_path, fixture.result_path, output_path)
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert output_path.is_file()
    assert output_path.read_bytes() == b""
    assert json.loads(result.stdout)["output_byte_length"] == 0
    assert json.loads(result.stdout)["business_output_sha256"] == hashlib.sha256(
        b""
    ).hexdigest()


@pytest.mark.parametrize(
    "state",
    ("insufficient_evidence", "stale_or_inconsistent", "result_failure"),
)
def test_not_publishable_is_fixed_exit_one_without_text_or_output(
    tmp_path: Path,
    state: str,
) -> None:
    fixture, readiness_path, _record, hidden_text = evidence_for_state(
        tmp_path / state, state
    )
    output_path = tmp_path / state / "not-exported.txt"
    failure_message = getattr(fixture.record.result, "message", None)

    result = runner.invoke(
        app, publication_export_args(readiness_path, fixture.result_path, output_path)
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Error: publication regeneration output is not exportable\n"
    )
    assert not output_path.exists()
    for secret in (hidden_text, failure_message):
        if secret is not None:
            assert secret not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "target_kind", ("missing_parent", "directory", "symlink", "existing")
)
def test_invalid_output_targets_are_fixed_errors_and_preserved(
    tmp_path: Path,
    target_kind: str,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path / "evidence")
    if target_kind == "missing_parent":
        output_path = tmp_path / "missing" / "export.txt"
        before: object = None
    elif target_kind == "directory":
        output_path = tmp_path / "directory"
        output_path.mkdir()
        before = "directory"
    elif target_kind == "symlink":
        source_path = tmp_path / "source.txt"
        source_path.write_bytes(b"source")
        output_path = tmp_path / "symlink.txt"
        output_path.symlink_to(source_path)
        before = output_path.readlink()
    else:
        output_path = tmp_path / "existing.txt"
        output_path.write_bytes(b"keep me")
        before = b"keep me"

    result = runner.invoke(
        app, publication_export_args(readiness_path, fixture.result_path, output_path)
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: publication regeneration export is invalid\n"
    if target_kind == "missing_parent":
        assert not output_path.parent.exists()
    elif target_kind == "directory":
        assert output_path.is_dir()
    elif target_kind == "symlink":
        assert output_path.readlink() == before
    else:
        assert output_path.read_bytes() == before


@pytest.mark.parametrize("artifact", ("readiness", "result"))
def test_tampered_evidence_maps_to_fixed_invalid_error(
    tmp_path: Path,
    artifact: str,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path)
    tampered_path = readiness_path if artifact == "readiness" else fixture.result_path
    tampered_path.write_bytes(tampered_path.read_bytes() + b"tampered provider text")
    output_path = tmp_path / "invalid.txt"

    result = runner.invoke(
        app, publication_export_args(readiness_path, fixture.result_path, output_path)
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: publication regeneration export is invalid\n"
    assert not output_path.exists()
    assert "tampered provider text" not in result.stderr


def test_ambiguous_persistence_has_distinct_message_no_retry_and_retains_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "ambiguous output 日本語\n"
    fixture, readiness_path = ready_evidence(tmp_path, text=text)
    output_path = tmp_path / "ambiguous.txt"
    calls: list[tuple[Path, Path]] = []
    real_projection = export_module.project_publication_regeneration_output

    def project(*, readiness_record_path: Path, result_path: Path) -> object:
        calls.append((readiness_record_path, result_path))
        return real_projection(
            readiness_record_path=readiness_record_path,
            result_path=result_path,
        )

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("raw fsync secret")

    monkeypatch.setattr(
        export_module, "project_publication_regeneration_output", project
    )
    monkeypatch.setattr(export_module.os, "fsync", fail_fsync)

    result = runner.invoke(
        app, publication_export_args(readiness_path, fixture.result_path, output_path)
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "Error: publication regeneration export outcome is ambiguous\n"
    )
    assert len(calls) == 1
    assert output_path.is_file()
    assert output_path.read_bytes() == text.encode("utf-8")
    assert "raw fsync secret" not in result.stderr


def test_unexpected_phase272_exception_is_sanitized_and_called_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness_path = tmp_path / "readiness.json"
    result_path = tmp_path / "result.json"
    output_path = tmp_path / "output.txt"
    calls: list[tuple[Path, Path, Path]] = []

    def fail_export(
        *,
        output_path: Path,
        readiness_record_path: Path,
        result_path: Path,
    ) -> object:
        calls.append((output_path, readiness_record_path, result_path))
        raise RuntimeError("raw provider failure and credential text")

    monkeypatch.setattr(
        cli_module, "export_publication_regeneration_output", fail_export
    )

    result = runner.invoke(
        app, publication_export_args(readiness_path, result_path, output_path)
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: publication regeneration export is invalid\n"
    assert calls == [(output_path, readiness_path, result_path)]
    assert "raw provider failure" not in result.stderr
    assert "credential text" not in result.stderr


class PublicationRegenerationExportReceiptChild(PublicationRegenerationExportReceipt):
    """Non-exact receipt type used to verify the CLI boundary check."""


@pytest.mark.parametrize("malformed_kind", ("object", "subclass"))
def test_malformed_or_non_exact_phase272_return_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed_kind: str,
) -> None:
    readiness_path = tmp_path / "readiness.json"
    result_path = tmp_path / "result.json"
    output_path = tmp_path / "output.txt"
    calls = 0
    valid = receipt()

    def malformed_export(**_kwargs: Path) -> object:
        nonlocal calls
        calls += 1
        if malformed_kind == "object":
            return object()
        return PublicationRegenerationExportReceiptChild(
            schema_version=valid.schema_version,
            regeneration_id=valid.regeneration_id,
            projection_sha256=valid.projection_sha256,
            readiness_record_sha256=valid.readiness_record_sha256,
            result_record_sha256=valid.result_record_sha256,
            source_audit_sha256=valid.source_audit_sha256,
            business_output_sha256=valid.business_output_sha256,
            output_byte_length=valid.output_byte_length,
        )

    monkeypatch.setattr(
        cli_module, "export_publication_regeneration_output", malformed_export
    )

    result = runner.invoke(
        app, publication_export_args(readiness_path, result_path, output_path)
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: publication regeneration export is invalid\n"
    assert calls == 1
    assert not output_path.exists()


def test_other_phase272_classification_is_not_exposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_classification = "internal-secret-classification"

    def reject_export(**_kwargs: Path) -> object:
        raise PublicationRegenerationExportError(secret_classification)

    monkeypatch.setattr(
        cli_module, "export_publication_regeneration_output", reject_export
    )
    result = runner.invoke(
        app,
        publication_export_args(
            tmp_path / "readiness.json",
            tmp_path / "result.json",
            tmp_path / "output.txt",
        ),
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: publication regeneration export is invalid\n"
    assert secret_classification not in result.stdout + result.stderr


def test_adapter_does_not_call_phase270_or_predecessor_loaders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = receipt()
    forbidden_calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> object:
        forbidden_calls.append("called")
        raise AssertionError("Phase 270 must not be called by the CLI adapter")

    monkeypatch.setattr(
        cli_module, "project_publication_regeneration_output", forbidden
    )
    monkeypatch.setattr(
        cli_module,
        "export_publication_regeneration_output",
        lambda **_kwargs: expected,
    )

    result = runner.invoke(
        app,
        publication_export_args(
            tmp_path / "readiness.json",
            tmp_path / "result.json",
            tmp_path / "output.txt",
        ),
    )

    assert result.exit_code == 0
    assert forbidden_calls == []
    assert json.loads(result.stdout) == receipt_json(expected)


def test_adapter_has_no_path_preflight_or_normalization(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    readiness_path = Path("missing/../readiness.json")
    result_path = Path("missing/result.json")
    output_path = Path("missing/../output.txt")
    expected = receipt()
    received: list[tuple[Path, Path, Path]] = []

    def forbidden_path_operation(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("CLI must not preflight or normalize paths")

    def export(
        *,
        output_path: Path,
        readiness_record_path: Path,
        result_path: Path,
    ) -> PublicationRegenerationExportReceipt:
        received.append((output_path, readiness_record_path, result_path))
        return expected

    monkeypatch.setattr(Path, "exists", forbidden_path_operation)
    monkeypatch.setattr(Path, "is_dir", forbidden_path_operation)
    monkeypatch.setattr(Path, "is_symlink", forbidden_path_operation)
    monkeypatch.setattr(Path, "resolve", forbidden_path_operation)
    monkeypatch.setattr(cli_module, "export_publication_regeneration_output", export)

    cli_module.publication_export_workflow(
        readiness_record_path=readiness_path,
        result_path=result_path,
        output_path=output_path,
    )

    captured = capsys.readouterr()
    assert received == [(output_path, readiness_path, result_path)]
    assert received[0][0] is output_path
    assert json.loads(captured.out) == receipt_json(expected)


def test_adapter_has_no_external_access_or_duplicate_filesystem_writes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = receipt()

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("provider, network, clock, or duplicate write")

    real_getenv = os.getenv

    def forbidden_getenv(name: str, default: object = None) -> object:
        if name == "_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION":
            return real_getenv(name, default)
        return forbidden(name, default)

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden_getenv)
    for method in ("write_bytes", "write_text", "unlink", "mkdir", "rename", "replace"):
        monkeypatch.setattr(Path, method, forbidden)
    monkeypatch.setattr(
        cli_module,
        "export_publication_regeneration_output",
        lambda **_kwargs: expected,
    )

    cli_module.publication_export_workflow(
        readiness_record_path=Path("readiness.json"),
        result_path=Path("result.json"),
        output_path=Path("output.txt"),
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == receipt_json(expected)


def test_real_success_creates_only_explicit_output_not_receipt_or_manifest(
    tmp_path: Path,
) -> None:
    fixture, readiness_path = ready_evidence(tmp_path, text="one exact output")
    output_path = tmp_path / "explicit-output.txt"
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    result = runner.invoke(
        app, publication_export_args(readiness_path, fixture.result_path, output_path)
    )

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert result.exit_code == 0
    assert set(after) - set(before) == {output_path.relative_to(tmp_path)}
    assert not any("receipt" in path.name or "manifest" in path.name for path in after)
    assert after[output_path.relative_to(tmp_path)] == b"one exact output"
