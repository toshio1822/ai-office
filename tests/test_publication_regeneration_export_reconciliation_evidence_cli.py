"""Focused provider-free CLI tests for Phase 277."""

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
from ai_office.engine import (
    PublicationRegenerationExportReconciliation,
    load_publication_regeneration_export_reconciliation,
    persist_publication_regeneration_export_reconciliation,
    publication_regeneration_export_reconciliation_digest,
)

runner = CliRunner()
_SCHEMA_VERSION = "publication-regeneration-export-reconciliation.v1"
_EXPECTED_DIGEST = "a" * 64
_RECEIPT_DIGEST = "b" * 64
_OBSERVED_DIGEST = "c" * 64
_FIXED_ERROR = "Error: publication reconciliation evidence could not be read\n"


def evidence_args(evidence_path: Path) -> list[str]:
    """Return the explicit Phase 277 command arguments."""
    return [
        "workflows",
        "publication-reconciliation-evidence",
        "--evidence-path",
        str(evidence_path),
    ]


def reconciliation(
    status: str,
) -> PublicationRegenerationExportReconciliation:
    """Build one valid Phase 276 evidence model for each persisted status."""
    if status == "matched":
        observed_digest: str | None = _EXPECTED_DIGEST
        observed_length: int | None = 17
    elif status == "missing":
        observed_digest = None
        observed_length = None
    else:
        observed_digest = _OBSERVED_DIGEST
        observed_length = 21
    return PublicationRegenerationExportReconciliation(
        schema_version=_SCHEMA_VERSION,
        regeneration_id=f"regen-277-{status}",
        receipt_sha256=_RECEIPT_DIGEST,
        status=status,  # type: ignore[arg-type]
        expected_business_output_sha256=_EXPECTED_DIGEST,
        expected_output_byte_length=17,
        observed_business_output_sha256=observed_digest,
        observed_output_byte_length=observed_length,
    )


def output_json(
    value: PublicationRegenerationExportReconciliation,
    evidence_sha256: str,
) -> dict[str, object]:
    """Return the exact ten-key public JSON contract."""
    return {
        "operation": "publication-reconciliation-evidence",
        "schema_version": value.schema_version,
        "regeneration_id": value.regeneration_id,
        "receipt_sha256": value.receipt_sha256,
        "status": value.status,
        "expected_business_output_sha256": value.expected_business_output_sha256,
        "expected_output_byte_length": value.expected_output_byte_length,
        "observed_business_output_sha256": value.observed_business_output_sha256,
        "observed_output_byte_length": value.observed_output_byte_length,
        "evidence_sha256": evidence_sha256,
    }


def test_command_requires_explicit_evidence_path() -> None:
    result = runner.invoke(
        app, ["workflows", "publication-reconciliation-evidence"]
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Missing option" in result.stderr


def test_workflows_help_lists_command_exactly_once() -> None:
    result = runner.invoke(app, ["workflows", "--help"])

    assert result.exit_code == 0
    assert result.stdout.count("publication-reconciliation-evidence") == 1


@pytest.mark.parametrize("status", ("matched", "missing", "content_mismatch"))
def test_all_statuses_emit_exact_ten_key_json_and_expected_exit(
    tmp_path: Path,
    status: str,
) -> None:
    evidence_path = tmp_path / f"{status}.json"
    expected = reconciliation(status)
    persist_publication_regeneration_export_reconciliation(
        evidence_path, expected
    )

    result = runner.invoke(app, evidence_args(evidence_path))

    expected_digest = publication_regeneration_export_reconciliation_digest(expected)
    assert result.exit_code == (0 if status == "matched" else 1)
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout) == output_json(expected, expected_digest)
    assert set(json.loads(result.stdout)) == {
        "operation",
        "schema_version",
        "regeneration_id",
        "receipt_sha256",
        "status",
        "expected_business_output_sha256",
        "expected_output_byte_length",
        "observed_business_output_sha256",
        "observed_output_byte_length",
        "evidence_sha256",
    }
    assert str(evidence_path) not in result.stdout + result.stderr


def test_loader_receives_exact_path_and_digest_receives_exact_loaded_object(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence_path = Path("caller/../explicit-evidence.json")
    loaded = reconciliation("matched")
    loaded_paths: list[object] = []
    digested_objects: list[object] = []
    digest = "d" * 64

    def load(path: object) -> PublicationRegenerationExportReconciliation:
        loaded_paths.append(path)
        return loaded

    def digest_helper(value: object) -> str:
        digested_objects.append(value)
        return digest

    monkeypatch.setattr(
        cli_module,
        "load_publication_regeneration_export_reconciliation",
        load,
    )
    monkeypatch.setattr(
        cli_module,
        "publication_regeneration_export_reconciliation_digest",
        digest_helper,
    )

    cli_module.publication_reconciliation_evidence_workflow(evidence_path)

    captured = capsys.readouterr()
    assert loaded_paths == [evidence_path]
    assert loaded_paths[0] is evidence_path
    assert digested_objects == [loaded]
    assert digested_objects[0] is loaded
    assert captured.err == ""
    assert json.loads(captured.out) == output_json(loaded, digest)


def test_loader_failure_is_fixed_exit_two_without_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "secret-evidence.json"
    calls: list[object] = []

    def fail(path: object) -> object:
        calls.append(path)
        raise RuntimeError(
            f"private path {path} raw evidence {('e' * 64)}"
        )

    monkeypatch.setattr(
        cli_module, "load_publication_regeneration_export_reconciliation", fail
    )

    result = runner.invoke(app, evidence_args(evidence_path))

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == _FIXED_ERROR
    assert calls == [evidence_path]
    assert str(evidence_path) not in result.stderr
    assert "raw evidence" not in result.stderr
    assert "e" * 64 not in result.stderr


def test_wrong_loader_return_type_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "wrong-type.json"
    monkeypatch.setattr(
        cli_module,
        "load_publication_regeneration_export_reconciliation",
        lambda _path: object(),
    )

    result = runner.invoke(app, evidence_args(evidence_path))

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == _FIXED_ERROR


def test_digest_failure_is_fixed_exit_two_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "digest-failure.json"
    loaded = reconciliation("matched")
    loader_calls = 0
    digest_calls = 0

    def load(_path: Path) -> PublicationRegenerationExportReconciliation:
        nonlocal loader_calls
        loader_calls += 1
        return loaded

    def fail(_value: object) -> str:
        nonlocal digest_calls
        digest_calls += 1
        raise RuntimeError("private digest and provider payload")

    monkeypatch.setattr(
        cli_module,
        "load_publication_regeneration_export_reconciliation",
        load,
    )
    monkeypatch.setattr(
        cli_module,
        "publication_regeneration_export_reconciliation_digest",
        fail,
    )

    result = runner.invoke(app, evidence_args(evidence_path))

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == _FIXED_ERROR
    assert loader_calls == 1
    assert digest_calls == 1
    assert "private digest" not in result.stderr


def test_unexpected_exception_is_fixed_exit_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "unexpected.json"

    def fail(_path: Path) -> object:
        raise RuntimeError("unexpected private failure")

    monkeypatch.setattr(
        cli_module, "load_publication_regeneration_export_reconciliation", fail
    )

    result = runner.invoke(app, evidence_args(evidence_path))

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == _FIXED_ERROR
    assert "unexpected private failure" not in result.stderr


@pytest.mark.parametrize("target", ("missing", "directory", "symlink", "tampered"))
def test_invalid_tampered_or_unsafe_target_is_read_only_fixed_failure(
    tmp_path: Path,
    target: str,
) -> None:
    expected = reconciliation("matched")
    evidence_path = tmp_path / f"{target}.json"
    before: bytes | None = None
    if target == "directory":
        evidence_path.mkdir()
    elif target == "symlink":
        source = tmp_path / "source.json"
        persist_publication_regeneration_export_reconciliation(source, expected)
        evidence_path.symlink_to(source)
        before = source.read_bytes()
    elif target == "tampered":
        persist_publication_regeneration_export_reconciliation(evidence_path, expected)
        before = evidence_path.read_bytes()
        evidence_path.write_bytes(before + b"tampered")
        before = evidence_path.read_bytes()

    result = runner.invoke(app, evidence_args(evidence_path))

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == _FIXED_ERROR
    if target == "missing":
        assert not evidence_path.exists()
    elif target == "directory":
        assert evidence_path.is_dir()
    elif target == "symlink":
        assert evidence_path.is_symlink()
        assert evidence_path.resolve().read_bytes() == before
    else:
        assert evidence_path.read_bytes() == before


def test_cli_does_not_mutate_evidence_or_access_external_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path = tmp_path / "evidence.json"
    expected = reconciliation("matched")
    persist_publication_regeneration_export_reconciliation(evidence_path, expected)
    before = evidence_path.read_bytes()

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("forbidden external access or mutation")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    real_getenv = os.getenv

    def forbidden_getenv(name: str, default: object = None) -> object:
        if name == "_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION":
            return real_getenv(name, default)
        return forbidden(name, default)

    monkeypatch.setattr(os, "getenv", forbidden_getenv)
    for method in ("write_bytes", "write_text", "unlink", "mkdir", "rename", "replace"):
        monkeypatch.setattr(Path, method, forbidden)

    result = runner.invoke(app, evidence_args(evidence_path))

    assert result.exit_code == 0
    assert result.stderr == ""
    assert evidence_path.read_bytes() == before


def test_phase276_loader_and_digest_are_used_for_real_sidecar(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "round-trip.json"
    expected = reconciliation("matched")
    persist_publication_regeneration_export_reconciliation(evidence_path, expected)

    loaded = load_publication_regeneration_export_reconciliation(evidence_path)

    assert loaded == expected
    assert hashlib.sha256(evidence_path.read_bytes()).hexdigest() == (
        publication_regeneration_export_reconciliation_digest(loaded)
    )
